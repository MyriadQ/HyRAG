"""kg_light/query.py — stage 4: what a query actually returns, logged rather than eyeballed.

    uv run python -m hyrag.kg_light.cli query --class factoid
    uv run python -m hyrag.kg_light.cli query --id them-003

One call, two results: `aquery_llm` returns the retrieval (`data`: entities, relationships,
chunks, references — each reference already carrying its LightRAG chunk key) **and** the
generated answer, so the row is never queried twice and the keyword-extraction cost is paid
once. That matters here because the keyword role is thinking-off but not free.

The mode is the row's evaluated mode (`config.QUERY_MODE`, default `mix`) unless the caller
overrides it; the node's open question 2 separates "which mode proves the index exists" —
any of local/global/hybrid/mix, all of which return entities *and* relations *and* chunks —
from "which mode the row is scored under", which is this default.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from lightrag import QueryParam

from hyrag.kg_light import config

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


async def ask(rag, question: str, *, mode: str | None = None) -> dict:
    """Run one question and return a record: retrieval, answer, keywords, latency."""
    mode = mode or config.QUERY_MODE
    param = QueryParam(mode=mode, stream=False)

    started = time.perf_counter()
    result = await rag.aquery_llm(question, param)
    latency_ms = round((time.perf_counter() - started) * 1000)

    data = (result or {}).get("data") or {}
    metadata = (result or {}).get("metadata") or {}
    entities = data.get("entities") or []
    relationships = data.get("relationships") or []
    chunks = data.get("chunks") or []
    # `keywords` is where the keyword role's output lands; it is also the cheapest evidence
    # that dual-level retrieval happened rather than a plain vector search with a graph
    # drawn on top of it.
    keywords = metadata.get("keywords") or {}

    return {
        "question": question,
        "mode": mode,
        "status": (result or {}).get("status"),
        "latency_ms": latency_ms,
        "answer": ((result or {}).get("llm_response") or {}).get("content") or "",
        "counts": {
            "entities": len(entities),
            "relationships": len(relationships),
            "chunks": len(chunks),
        },
        "keywords": {
            "high_level": list(keywords.get("high_level") or []),
            "low_level": list(keywords.get("low_level") or []),
        },
        "entities": [
            {
                "entity_name": entity.get("entity_name"),
                "entity_type": entity.get("entity_type"),
                "source_id": entity.get("source_id"),
                "file_path": entity.get("file_path"),
            }
            for entity in entities
        ],
        "relationships": [
            {
                "src_id": relationship.get("src_id"),
                "tgt_id": relationship.get("tgt_id"),
                "keywords": relationship.get("keywords"),
                "source_id": relationship.get("source_id"),
                "file_path": relationship.get("file_path"),
            }
            for relationship in relationships
        ],
        # The chunk key LightRAG returns here is the map's other end (§5.6) — a query-time
        # resolution that a content hash cannot fake.
        "chunks": [
            {"chunk_id": chunk.get("chunk_id"), "file_path": chunk.get("file_path")}
            for chunk in chunks
        ],
    }


def resolve(record: dict) -> dict:
    """Translate a query record's LightRAG keys back into our chunk ids and citations.

    A one-line proof that the map works at the only moment it is used: every chunk key the
    query returned should resolve, and the unresolved list should be empty. Returns the same
    record with `our_chunks` filled in and `unresolved` listed.
    """
    mapping = config_idmap()
    resolved = []
    unresolved = []
    for chunk in record.get("chunks", []):
        key = chunk.get("chunk_id")
        our_id = mapping.get(key)
        if our_id is None:
            unresolved.append(key)
        else:
            resolved.append({"lightrag_key": key, "chunk_id": our_id})
    return {**record, "our_chunks": resolved, "unresolved": unresolved}


def config_idmap() -> dict:
    from hyrag.kg_light import idmap

    return idmap.load_map()


def write(records: list[dict], path: Path | None = None) -> Path:
    """Append query records to `results/` (gitignored: they quote the corpus)."""
    path = path or config.QUERY_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, **_JSON) + "\n")
    return path
