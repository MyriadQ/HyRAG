"""serving/checks.py — the equality the endpoint owes the rows that already exist.

    uv run python -m hyrag.serving.checks --write-reference   # once, server stopped
    uv run python -m hyrag.serving.checks                     # with the server running

`baseline` embedded all 369 chunks by calling `hyrag.embedding.encoder` in-process, and
every one of R1's stored vectors came from that call. `kg-light` (and every row after it)
reaches the same module over HTTP instead. **Until this check runs, "all four rows share
one encoder" is an intention, not a fact**: two services that both load bge-m3 can still
differ in pooling, normalisation or batch handling, and a difference here would surface as
a retrieval number nobody could explain.

**Why the reference is written first, in its own run.** The first version of this check did
both halves at once and took tens of minutes for 369 chunks — two processes, two copies of
bge-m3, one GPU. Sequencing them costs two commands and removes the contention *and* the
second copy of torch: the reference process imports the encoder, the comparison process
imports only numpy. The reference is cached at `parsed/embedding_reference.npy` and carries
the sha256 of `hyrag/embedding/encoder.py` as of the moment it was built, so a reference
that no longer matches the code fails loudly instead of certifying a stale model.

    text ──▶ encode() ──▶ reference (npy)          ← the module, in-process
    text ──▶ POST /v1/embeddings ──▶ served        ← the service, over the wire
                 compare: exact equality, both encoding formats
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from hyrag.chunker.store import INGEST_NAME, read_jsonl
from hyrag.embedding.encoder import DIM, MODEL_NAME
from hyrag.parse.config import PARSED

INGEST_PATH = PARSED / INGEST_NAME
REFERENCE_PATH = PARSED / "embedding_reference.npy"
REFERENCE_META = PARSED / "embedding_reference.json"
ENCODER_PATH = Path(__file__).resolve().parents[1] / "embedding" / "encoder.py"
DEFAULT_URL = "http://127.0.0.1:8001/v1/embeddings"
BATCH = 64  # requests in flight are serialised by the server anyway; this is round trips


def chunks(limit: int | None = None) -> list[str]:
    _header, records = read_jsonl(INGEST_PATH)
    texts = [record["content"] for record in records]
    return texts[:limit] if limit else texts


def _encoder_sha256() -> str:
    return hashlib.sha256(ENCODER_PATH.read_bytes()).hexdigest()


def write_reference(limit: int | None = None) -> int:
    """Embed the ingest set in-process and cache the answer. Run with the server stopped."""
    from hyrag.embedding.encoder import encode  # imported here: only this half needs torch

    texts = chunks(limit)
    vectors = np.asarray(encode(texts), dtype=np.float64)
    np.save(REFERENCE_PATH, vectors)
    meta = {
        "model": MODEL_NAME,
        "dim": DIM,
        "chunks": len(texts),
        "encoder_sha256": _encoder_sha256(),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    REFERENCE_META.write_text(json.dumps(meta, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\n  ✓ reference: {vectors.shape[0]}×{vectors.shape[1]} → {REFERENCE_PATH}")
    print(f"    {meta['model']} · encoder.py {meta['encoder_sha256'][:12]} · {meta['built_at']}")
    return 0


def _load_reference(limit: int | None) -> np.ndarray:
    if not (REFERENCE_PATH.is_file() and REFERENCE_META.is_file()):
        raise SystemExit("no reference — run `--write-reference` once with the server stopped")
    meta = json.loads(REFERENCE_META.read_text(encoding="utf-8"))
    if meta["model"] != MODEL_NAME or meta["dim"] != DIM:
        raise SystemExit(f"reference was built for {meta['model']}/{meta['dim']}; module says {MODEL_NAME}/{DIM}")
    if meta["encoder_sha256"] != _encoder_sha256():
        raise SystemExit(
            "reference is stale: hyrag/embedding/encoder.py has changed since it was built\n"
            "  rebuild it with `--write-reference`"
        )
    vectors = np.load(REFERENCE_PATH)
    return vectors[:limit] if limit else vectors


def _post(url: str, texts: list[str], encoding_format: str) -> list[np.ndarray]:
    payload = json.dumps({"input": texts, "encoding_format": encoding_format}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"cannot reach {url}: {exc}\n  start it with `uv run python -m hyrag.serving.cli serve`"
        )
    rows = sorted(body["data"], key=lambda row: row["index"])
    if encoding_format == "base64":
        return [
            np.frombuffer(base64.b64decode(row["embedding"]), dtype=np.float32).astype(np.float64)
            for row in rows
        ]
    return [np.asarray(row["embedding"], dtype=np.float64) for row in rows]


def check(url: str = DEFAULT_URL, limit: int | None = None) -> int:
    """Compare the served vectors against the cached reference, both encodings."""
    texts = chunks(limit)
    reference = _load_reference(limit)
    if len(reference) != len(texts):
        raise SystemExit(f"reference holds {len(reference)} vectors, ingest set now has {len(texts)}")

    print(f"\n  {len(texts)} chunks · {MODEL_NAME} · {url}")
    failures = 0
    for encoding_format in ("float", "base64"):
        served: list[np.ndarray] = []
        for start in range(0, len(texts), BATCH):
            served.extend(_post(url, texts[start : start + BATCH], encoding_format))
        array = np.stack(served)
        if array.shape != reference.shape:
            print(f"  ✗ {encoding_format:<6} shape {array.shape} != {reference.shape}")
            failures += 1
            continue
        delta = float(np.max(np.abs(array - reference)))
        exact = bool(np.array_equal(array, reference))
        print(
            f"  {'✓' if exact else '✗'} {encoding_format:<6} {array.shape[0]}×{array.shape[1]}"
            f" · dim {DIM} · max|Δ| {delta:.3e} · exact {exact}"
        )
        failures += 0 if exact else 1

    print(
        f"\n  {2 - failures}/2 paths identical to the in-process encoder"
        if not failures
        else f"\n  {2 - failures}/2 paths identical — THE ENDPOINT IS NOT THE SAME ENCODER"
    )
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hyrag.serving.checks", description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--limit", type=int, default=None, help="only the first N chunks")
    parser.add_argument(
        "--write-reference",
        action="store_true",
        help="embed the ingest set in-process and cache it (server stopped)",
    )
    args = parser.parse_args(argv)
    if args.write_reference:
        return write_reference(args.limit)
    return check(args.url, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
