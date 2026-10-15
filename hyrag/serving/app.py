"""serving/app.py — the one HTTP surface this project exposes to itself.

Two routes, both OpenAI-compatible, both pointed at by rows that cannot import our Python:

    POST /v1/embeddings        local bge-m3, in-process              (embeddings.py)
    POST /v1/chat/completions  DeepSeek, with the transport repaired  (llm.py)
    GET  /health               liveness + the embedding contract

They share a port because they share a purpose: a row should have to learn exactly one URL to
reach both the vectors and the model. Start it with

    uv run python -m hyrag.serving.cli serve
"""

from __future__ import annotations

from fastapi import FastAPI

from hyrag.serving import embeddings, llm

app = FastAPI(title="hyrag serving", version="0.2")
app.include_router(embeddings.router)
app.include_router(llm.router)
