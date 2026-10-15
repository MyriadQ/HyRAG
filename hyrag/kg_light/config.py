"""kg_light/config.py — the R2 row's constants, its ingest set, and the doc reconstruction.

Three things live here, and nothing else in the package may restate them:

1. **The wiring** — which embedding endpoint, which LLM, which Postgres, which workspace,
   how much concurrency. Ports and model names are the sort of thing that gets typed twice
   and then disagrees in a report.
2. **The ingest set** — `parsed/_ingest.jsonl`, the 369 deduped chunks every row reads (D16),
   regrouped per document in reading order. R1 reads the same file; this is the second reader,
   not a copy.
3. **The document text** — the concatenation of the document's blocks under the sidecar's
   own separator (the same one `packing.SEPARATOR` uses, and the one the sidecar's
   `document_hash` is computed over). It is what we hand LightRAG as the document body:
   the pipeline stores it and hashes it, and our custom chunker then returns the shared
   chunks that were built from exactly this text — so "no re-chunking" is a property, not
   a promise.

**Postgres is set here, at import time, on purpose.** LightRAG's PG backends read
`POSTGRES_HOST/PORT/USER/DATABASE/PASSWORD` and `POSTGRES_WORKSPACE` when they connect, and
its storage classes resolve the workspace once, in `initialize()`. Setting them any later
means the row silently writes to the default database or the wrong workspace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from hyrag.chunker.packing import SEPARATOR
from hyrag.chunker.store import INGEST_NAME, read_jsonl
from hyrag.parse.config import PARSED, REPO
from hyrag.parse.sidecar import read_blocks

# ---------------------------------------------------------------- paths

RESULTS = REPO / "results"
INGEST_PATH = PARSED / INGEST_NAME
WORKING_DIR = PARSED / "lightrag"  # LightRAG's own scratch: NetworkX graph, file caches
IDMAP_PATH = PARSED / "kg_light_idmap.json"  # the §5.6 map, written after ingest
COST_PATH = PARSED / "kg_light_extraction.json"  # open question 3's number
QUERY_LOG = RESULTS / "kg_light_queries.jsonl"  # one record per query the node ran

# ---------------------------------------------------------------- the pin

LIGHTRAG_REV = "453dce8"  # §6.1 — the commit, not a version range

# ---------------------------------------------------------------- embeddings

EMBEDDING_URL = os.environ.get("HYRAG_EMBEDDING_URL", "http://127.0.0.1:8001/v1")
EMBEDDING_MODEL = "bge-m3"
EMBEDDING_DIM = 1024
EMBEDDING_MAX_TOKENS = 8192  # bge-m3's window; our longest chunk is 1619 XLM-R tokens
EMBEDDING_BATCH = 8
EMBEDDING_MAX_ASYNC = 4

# ---------------------------------------------------------------- the LLM (§6.1 roles)

LLM_MODEL = os.environ.get("HYRAG_LLM_MODEL", "deepseek-flash")
DEEPSEEK_DEFAULT_URL = "https://api.deepseek.com"

# DeepSeek's thinking is on by default; `reasoning_effort: "low"` still thinks, and only
# this key turns it off (README → Landmines, verified 2026-10-11). It rides in the request
# body via the OpenAI client's `extra_body`, which is the only way to reach a field the
# SDK does not model.
THINKING_OFF = {"thinking": {"type": "disabled"}}

EXTRACT_MAX_TOKENS = 8192  # a chunk's extraction JSON; thinking is off, so this is all output
QUERY_MAX_TOKENS = 16384  # thinking is ON for the query role; 8000 was measured too small (S06)

LLM_MAX_ASYNC = int(os.environ.get("HYRAG_LLM_MAX_ASYNC", "4"))
EXTRACT_MAX_ASYNC = int(os.environ.get("HYRAG_EXTRACT_MAX_ASYNC", "8"))
KEYWORD_MAX_ASYNC = int(os.environ.get("HYRAG_KEYWORD_MAX_ASYNC", "4"))

# ---------------------------------------------------------------- query

# The mode the row is *evaluated* under. `mix` = local + global + naive, LightRAG's strongest
# native mode, and the one the landmines name as the thing Phase 2 must beat. Which mode
# *proves the index exists* is a different question and is answered by `aquery_data` —
# any of local/global/hybrid/mix returns entities, relations and chunks.
QUERY_MODE = os.environ.get("HYRAG_QUERY_MODE", "mix")

# ---------------------------------------------------------------- postgres (D15)

os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_USER", os.environ.get("USER", "celery"))
os.environ.setdefault("POSTGRES_DATABASE", "hyrag")
# Homebrew's local cluster authenticates by trust, so there is no password — but LightRAG's
# `check_storage_env_vars` only tests *membership* in the environment, and raises for an
# unset POSTGRES_PASSWORD before it ever connects. Empty string it is: the value is never
# used, and this keeps the check happy without inventing a credential.
os.environ.setdefault("POSTGRES_PASSWORD", "")
# The workspace is pinned rather than left to `default`: the tables are shared with R1's
# `baseline_chunks`, and a row set that moves workspace between runs looks like an empty
# index. `POSTGRES_WORKSPACE` wins over the instance's own `workspace` (postgres_impl
# `initialize()`), so both are set to the same value.
WORKSPACE = os.environ.setdefault("POSTGRES_WORKSPACE", "hyrag")
os.environ.setdefault("WORKSPACE", WORKSPACE)


# ---------------------------------------------------------------- credentials


def _dotenv(key: str) -> str | None:
    """`key` from the environment, else from the repo-root `.env`. Same file R1 reads."""
    value = os.environ.get(key)
    if value:
        return value
    env_file = REPO / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return None


def deepseek_key() -> str:
    key = _dotenv("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("no DEEPSEEK_API_KEY: put it in .env at the repo root (gitignored)")
    return key


def deepseek_base() -> str:
    return _dotenv("DEEPSEEK_BASE_URL") or DEEPSEEK_DEFAULT_URL


# ---------------------------------------------------------------- the ingest set


@dataclass(frozen=True)
class Doc:
    """One document as LightRAG will see it: a body, a citation path, and our chunks.

    `chunks` holds only the records whose *primary* occurrence is this document — the
    collapsed duplicates of §5.6 belong to the document that appears first, and every
    other location survives in that record's `occurrences`.
    """

    stem: str
    file: str
    text: str
    chunks: tuple[dict, ...]


@lru_cache(maxsize=1)
def docs() -> tuple[Doc, ...]:
    """The 12 documents of the ingest set, each with its shared chunks in reading order."""
    _header, records = read_jsonl(INGEST_PATH)

    by_stem: dict[str, list[dict]] = {}
    for record in records:
        stem = record["occurrences"][0]["stem"]
        by_stem.setdefault(stem, []).append(record)

    documents: list[Doc] = []
    for stem, chunks in sorted(by_stem.items()):
        # `chunk_id` is `stem:0007` — zero-padded, so lexicographic order is reading order.
        chunks.sort(key=lambda record: record["occurrences"][0]["chunk_id"])
        files = {record["file"] for record in chunks}
        if len(files) != 1:
            raise ValueError(f"{stem}: chunks disagree about their file: {sorted(files)}")

        _meta, blocks = read_blocks(PARSED / stem / "blocks.jsonl")
        text = SEPARATOR.join(block["content"] for block in blocks)
        documents.append(Doc(stem=stem, file=chunks[0]["file"], text=text, chunks=tuple(chunks)))
    return tuple(documents)


@lru_cache(maxsize=1)
def docs_by_stem() -> dict[str, Doc]:
    return {doc.stem: doc for doc in docs()}


def our_chunks() -> list[dict]:
    """Every ingest record, corpus-wide — the 369 the map must account for."""
    _header, records = read_jsonl(INGEST_PATH)
    return records
