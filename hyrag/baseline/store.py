"""baseline/store.py — the R1 index: chunks and their vectors in Postgres.

    psql hyrag -f hyrag/baseline/schema.sql          # once: create the table
    uv run python -m hyrag.baseline.cli ingest       # _ingest.jsonl -> baseline_chunks
    uv run python -m hyrag.baseline.cli search "…"   # top-k by cosine

Why a database at all for 369 rows: uniformity, not speed. One service will hold LightRAG's
KV, the dialogue checkpoints and these vectors (D15) — and a row already carries its own
citation, so a retrieved chunk arrives with everything the answer needs and nothing is
re-joined from `_ingest.jsonl` afterwards.

The index is **derived data**, rebuilt from `_ingest.jsonl` plus the encoder in about two
minutes, so `ingest` replaces rather than updates: the whole write runs inside one
transaction, and if the chunk set or the encoder changed, no stale vector can survive to
poison a comparison. Two minutes is cheaper than the class of bug it removes.

The retrieval is one statement:

    ORDER BY embedding <=> %s LIMIT k

`<=>` is cosine *distance* — smaller is nearer, which is why the sort ascends — and both
sides are unit-length vectors, so it equals `1 - (dot product)`. The operator appears in
`ORDER BY` rather than on an alias on purpose: Postgres can only use a vector index when the
sort is the bare operator, and the `1 - (…) DESC` rewrite silently drops to a sequential
scan (pgvector README → *Troubleshooting*).
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg  # the driver: Python <-> Postgres
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from hyrag.chunker.store import INGEST_NAME, read_jsonl
from hyrag.embedding.encoder import DEFAULT_BATCH_SIZE, MODEL_NAME, encode, encode_one
from hyrag.parse.config import PARSED

INGEST_PATH = PARSED / INGEST_NAME  # the one chunk set all four rows read (D16)

INSERT_COLUMNS = (
    "chunk_id", "file", "heading", "level", "page_start", "page_end",
    "citation", "occurrences", "tokens", "content", "model", "embedding",
)


def db_url() -> str:
    """Where `hyrag` lives. `HYRAG_DB` overrides; the default is the Homebrew instance."""
    return os.environ.get("HYRAG_DB", "dbname=hyrag")


def connect() -> psycopg.Connection:
    """Open the database and teach psycopg the `vector` type.

    `register_vector` registers a dumper for pgvector's own `Vector` class and for numpy
    arrays — **not** for plain Python lists (see `pgvector/psycopg/vector.py`). A bare list
    is therefore sent as `double precision[]`, which Postgres will cast to `vector` when it
    fills a column (assignments allow the cast) but *not* when resolving the `<=>` operator.
    That is why a list inserts fine and fails in `ORDER BY`. Pass `Vector(...)` at the edges.
    """
    conn = psycopg.connect(db_url())
    register_vector(conn)
    return conn


# ---------------------------------------------------------------- writing


def load_chunks() -> list[dict]:
    """The 369 unique chunk records — the same set all four rows read (D16)."""
    _header, records = read_jsonl(INGEST_PATH)
    return records


def _row(chunk: dict, vector: list[float]) -> tuple:
    """One chunk record plus its vector, in `INSERT_COLUMNS` order.

    `page_start`/`page_end` come from the chunk's *primary* location — the first place the
    text occurred. Every other location stays in `occurrences`, because the chunker's
    contract is that a citation enumerates all of them rather than picking one.
    """
    primary = chunk["occurrences"][0]
    page_start, page_end = primary["page_range"]
    return (
        chunk["chunk_id"],
        chunk["file"],
        chunk["heading"],
        chunk["level"],
        page_start,
        page_end,
        primary["citation"],
        Jsonb(chunk["occurrences"]),  # jsonb needs the wrapper, or psycopg sends a plain str
        chunk["tokens"],
        chunk["content"],
        MODEL_NAME,
        vector,
    )


def ingest(conn: psycopg.Connection, chunks: list[dict] | None = None,
           batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Embed every chunk and replace the table's contents. Returns the row count.

    Encoding is the slow half (~2 min for 369 chunks on MPS) and happens *before* the
    transaction opens: a model that fails halfway must not leave the table truncated and
    empty. `TRUNCATE` and the inserts share one transaction, so a failure inside it rolls
    back to the previous contents.
    """
    chunks = load_chunks() if chunks is None else chunks
    vectors = encode([c["content"] for c in chunks], batch_size=batch_size)

    # zip takes the i-th chunk and the i-th vector and packages them: (chunks[i], vectors[i])
    rows = [_row(chunk, vector) for chunk, vector in zip(chunks, vectors)]

    # one %s per column, joined: "%s, %s, …" — a placeholder for each value in a row
    placeholders = ", ".join(["%s"] * len(INSERT_COLUMNS))
    statement = f"INSERT INTO baseline_chunks ({', '.join(INSERT_COLUMNS)}) VALUES ({placeholders})"

    # two context managers: the cursor closes, and the transaction commits only if nothing raised
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("TRUNCATE baseline_chunks")
        cur.executemany(statement, rows)
    return len(rows)


# ---------------------------------------------------------------- reading


def search(conn: psycopg.Connection, question: str, k: int = 20) -> list[dict]:
    """The top-k chunks for a question, nearest first, each with its citation."""
    return search_vector(conn, encode_one(question), k=k)


def search_vector(conn: psycopg.Connection, vector: list[float], k: int = 20) -> list[dict]:
    """`search` for a vector that was already computed — the shape a trace wants."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT chunk_id, file, heading, page_start, page_end, citation, tokens, content,
                   embedding <=> %s AS distance
            FROM baseline_chunks
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (Vector(vector), Vector(vector), k),
        )
        return cur.fetchall()


def rank_of(conn: psycopg.Connection, vector: list[float], chunk_ids: list[str]) -> int | None:
    """Where the nearest of `chunk_ids` ranks among *all* rows, 1-based. None if absent.

    This is how the node's gate gets checked per question: not "did that look right", but the
    rank of the chunk the question was written from. `row_number() OVER (ORDER BY …)` is the
    window-function form of top-k, so the whole table is ranked and the target looked up.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT min(rnk) FROM (
                SELECT chunk_id, row_number() OVER (ORDER BY embedding <=> %s) AS rnk
                FROM baseline_chunks
            ) AS ranked
            WHERE chunk_id = ANY(%s)
            """,
            (Vector(vector), chunk_ids),
        )
        row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None
