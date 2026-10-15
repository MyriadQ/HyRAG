"""kg_light/ingest.py — one extraction pass over the shared chunks (D1), with its cost logged.

    uv run python -m hyrag.kg_light.cli ingest --limit 1     # smoke: one document
    uv run python -m hyrag.kg_light.cli ingest               # the 12 documents, 369 chunks

What this does, in the pipeline's own stages:

1. enqueue 12 documents **raw** — body, `ids` (our stems), `file_paths` (our filenames). The
   raw path takes our text verbatim and skips LightRAG's parser; with `ids` supplied the
   pipeline treats the documents as SDK inserts, not uploads.
2. process the queue — LightRAG's Stage 3 runs: our custom chunker returns the shared chunks,
   the EXTRACT role pulls entities/relations out of them (thinking off), the MERGE stage
   builds descriptions, and the graph, KV and vector stores are written.
3. write `parsed/kg_light_extraction.json` — calls, tokens, wall clock (open question 3).

Why the run is resumable: LightRAG journals document status in Postgres, so an interrupt
leaves documents `PROCESSING` and the next run picks up pending/failed/abandoned documents
rather than redoing the finished ones. The LLM response cache means even a repeated document
costs nothing.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from lightrag.utils import TokenTracker

from hyrag.baseline.store import connect
from hyrag.kg_light import config, idmap
from hyrag.kg_light.builder import build_rag

log = logging.getLogger("hyrag.kg_light")

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- retry / reset
#
# Two facts about LightRAG's scheduler that bite on the second run of anything:
#
# * a FAILED document is **never** auto-resumed. The pipeline's auto-resume set is
#   PENDING/PROCESSING/PARSING/ANALYZING — PROCESSING and friends self-heal because an
#   interrupted run left them mid-flight, but a genuine failure is retried only through an
#   explicit operator request (pipeline.py, `_AUTO_RESUME_DOC_STATUSES`). So a failed
#   document sits there forever while `ingest` cheerfully reports "No documents to process".
# * re-enqueuing a document whose content hash is already known does not update the existing
#   row: it creates a `dup-<hash>` record that immediately fails with "File name already
#   exists". Retrying therefore means flipping the original row back, and clearing the dups.


def retry_failed() -> dict:
    """FAILED → PENDING for this workspace, and drop the `dup-` stubs a re-enqueue creates."""
    with connect() as conn, conn.cursor() as cur:
        duplicates = _drop_duplicate_stubs(cur)
        cur.execute(
            """
            UPDATE lightrag_doc_status
               SET status = 'pending', error_msg = NULL, updated_at = CURRENT_TIMESTAMP
             WHERE workspace = %s AND status = 'failed'
            """,
            (config.WORKSPACE,),
        )
        flipped = cur.rowcount
    if flipped or duplicates:
        log.info("retry: flipped %d failed document(s) to pending, removed %d duplicate stub(s)",
                 flipped, duplicates)
    return {"retried": flipped, "duplicate_stubs_removed": duplicates}


def _drop_duplicate_stubs(cur) -> int:
    """Delete the `dup-<hash>` rows an enqueue creates for content it already holds.

    Re-enqueueing a document whose content hash is known does not refresh the existing row;
    it writes a `dup-` stub that fails with *"File name already exists"* and then sits in the
    status table for ever (FAILED is never auto-resumed). It is bookkeeping damage, not index
    damage — drop it so the status table keeps meaning "our documents".
    """
    cur.execute(
        "DELETE FROM lightrag_doc_status WHERE workspace = %s AND id LIKE 'dup-%%'",
        (config.WORKSPACE,),
    )
    return cur.rowcount


def reset_index(keep_cache: bool = True) -> dict:
    """Delete the row's derived data for this workspace; keep the LLM cache by default.

    The cache is the expensive part and it is keyed by prompt, not by document — keeping it
    means a reset costs embeddings and merges, not a second paid extraction pass. Everything
    else (documents, chunks, vectors, doc status) is regenerable from `_ingest.jsonl`.
    """
    removed: dict[str, int] = {}
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name LIKE 'lightrag\\_%%'"
        )
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            if keep_cache and table == "lightrag_llm_cache":
                continue
            cur.execute(f'DELETE FROM "{table}" WHERE workspace = %s', (config.WORKSPACE,))
            removed[table] = cur.rowcount
    graph = idmap.graph_path()
    if graph.is_file():
        graph.unlink()
        removed["graphml"] = 1
    return removed


async def ingest(documents=None, *, limit: int | None = None, retry: bool = False) -> dict:
    """Enqueue and process the ingest set. Returns the cost record it also writes to disk."""
    documents = list(config.docs() if documents is None else documents)
    if limit is not None:
        documents = documents[:limit]
    if retry:
        retry_failed()

    tracker = TokenTracker()
    rag = build_rag(tracker)
    await rag.initialize_storages()

    started = _now()
    started_clock = time.perf_counter()
    try:
        track_id = await rag.apipeline_enqueue_documents(
            input=[doc.text for doc in documents],
            ids=[doc.stem for doc in documents],
            file_paths=[doc.file for doc in documents],
        )
        log.info("enqueued %d documents under track_id %s", len(documents), track_id)
        await rag.apipeline_process_enqueue_documents()
        stubs = _cleanup_duplicate_stubs()
        counts = idmap.stored_counts()
    finally:
        await rag.finalize_storages()
    wall_clock = round(time.perf_counter() - started_clock, 1)

    usage = tracker.get_usage()
    record = {
        "type": "kg_light_extraction",
        "lightrag_rev": config.LIGHTRAG_REV,
        "llm_model": config.LLM_MODEL,
        "query_mode": config.QUERY_MODE,
        "documents": len(documents),
        "chunks": sum(len(doc.chunks) for doc in documents),
        "started": started,
        "wall_clock_s": wall_clock,
        "llm_calls": usage.get("call_count", 0),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "extract_max_async": config.EXTRACT_MAX_ASYNC,
        "stored": counts,
        "duplicate_stubs_removed": stubs,
    }
    config.COST_PATH.write_text(json.dumps(record, **_JSON) + "\n", encoding="utf-8")
    return record


def _cleanup_duplicate_stubs() -> int:
    with connect() as conn, conn.cursor() as cur:
        return _drop_duplicate_stubs(cur)


def cost_record(path: Path | None = None) -> dict | None:
    """The last run's cost record, if there is one — read, never recomputed."""
    path = path or config.COST_PATH
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
