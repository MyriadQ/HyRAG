"""serving/embeddings.py — the OpenAI-compatible `/v1/embeddings` service.

    uv run python -m hyrag.serving.cli serve            # 127.0.0.1:8001
    curl -s localhost:8001/health

Why a server rather than a Python import: LightRAG reaches embeddings through an
OpenAI-compatible *client*, and GraphRAG will reach them through LiteLLM's `api_base`.
Neither can import `hyrag.embedding.encoder`. One local service lets both point at the same
weights, and the comparison across rows holds the embedding model fixed (§6.1).

**The rule this file lives under (nodes/kg-light.md → Inherited from `baseline`): the
adapter contains no encoding logic.** No pooling, no normalization, no max length, no
prefixes — those belong to `hyrag/embedding/encoder.py` and only there. If a decision about
*what the vector means* appears below rather than a decision about *how it travels*, it is
a bug: R1's vectors were produced by the module alone, and this endpoint owes them an exact
equality (`hyrag.serving.checks` proves it over all 369 chunks).

What is transport, and therefore allowed here: the request/response envelope, and the
`encoding_format` (`float` lists, or `base64` of little-endian float32 — what the OpenAI
SDK asks for by default and what `lightrag.llm.openai.openai_embed` decodes).

Concurrency: one process, one model, one lock. LightRAG embeds concurrently (its client
issues overlapping requests), and a torch model shared across threads on MPS is a race we
do not need — the lock serialises encodes instead. Throughput is irrelevant at this scale:
the full 369-chunk set encodes in about two minutes.
"""

from __future__ import annotations

import base64
import logging
import threading
import time

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from hyrag.embedding.encoder import DIM, MODEL_NAME, encode

log = logging.getLogger("hyrag.serving")

app = FastAPI(title="hyrag embeddings", version="0.1")

# Serialises `encode`. See the module docstring: correctness over throughput.
_encode_lock = threading.Lock()


class EmbeddingRequest(BaseModel):
    """The subset of OpenAI's request we accept — extra fields are ignored, not rejected.

    `dimensions` is accepted so a client that asks for it does not 400; it is then
    *checked*, because bge-m3 has no truncated-dimension mode and a silent mismatch here
    would be a 1024-float vector compared against a truncated reference somewhere else.
    """

    model_config = ConfigDict(extra="ignore")

    input: str | list[str]
    model: str | None = None
    encoding_format: str = "float"
    dimensions: int | None = None
    user: str | None = None


@app.get("/health")
def health() -> dict:
    """Liveness plus the two facts a caller must not guess: which model, which dimension."""
    return {"status": "ok", "model": MODEL_NAME, "dim": DIM}


@app.post("/v1/embeddings")
def embeddings(request: EmbeddingRequest) -> dict:
    texts = [request.input] if isinstance(request.input, str) else list(request.input)
    if request.dimensions not in (None, DIM):
        raise HTTPException(
            status_code=400,
            detail=f"{MODEL_NAME} returns {DIM} dimensions; {request.dimensions} requested",
        )
    if request.encoding_format not in ("float", "base64"):
        raise HTTPException(
            status_code=400, detail=f"unknown encoding_format {request.encoding_format!r}"
        )

    started = time.perf_counter()
    with _encode_lock:
        vectors = encode(texts)
    elapsed = time.perf_counter() - started
    log.info("encoded %d texts in %.2fs", len(texts), elapsed)

    if request.encoding_format == "base64":
        payload: list[str] = [
            base64.b64encode(np.asarray(vector, dtype=np.float32).tobytes()).decode("ascii")
            for vector in vectors
        ]
    else:
        payload = vectors  # type: ignore[assignment]

    return {
        "object": "list",
        "model": request.model or MODEL_NAME,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(payload)
        ],
    }
