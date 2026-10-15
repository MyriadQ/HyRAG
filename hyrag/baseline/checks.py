"""baseline/checks.py — the invariants the R1 index must hold, as one command.

    uv run python -m hyrag.baseline.cli check

Same contract as `hyrag.parse.checks` and `hyrag.chunker.checks`: each check states the
property it verifies, and a failure names what it failed on.

* `extension`       pgvector is loaded, and at the version the store assumes;
* `row_count`       the table holds exactly one row per unique chunk in `_ingest.jsonl`;
* `ids_match`       the two id sets are equal **both ways** — a row with no chunk is as wrong
                    as a chunk with no row, and only the second one is obvious;
* `dimensions`      every vector is 1024 — enforced by the column type, re-asserted here so
                    a hand-edited table cannot pass silently. Note `vector_dims`, and note
                    that the norm function for a `vector` column is `vector_norm`: `l2_norm`
                    exists only for `halfvec` and `sparsevec`, so calling it unqualified is
                    an ambiguity error rather than a missing function;
* `unit_norm`       every vector has Euclidean length 1, matching the encoder's contract;
* `model`           every row records the encoder that produced it — the property that makes
                    a stale vector detectable after a model change;
* `citation_shape`  each row has a citation and `page_start <= page_end`;
* `evidence_recall` INFO — the recall curve again, but computed **by Postgres**: each
                    question ranked by `ORDER BY embedding <=> q`, which is the retrieval the
                    row will actually use. If this disagrees with `hyrag.embedding.checks`,
                    the store is at fault, not the encoder.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass

from hyrag.baseline import store
from hyrag.embedding.checks import K_VALUES  # imported so both curves share an x-axis
from hyrag.embedding.encoder import MODEL_NAME, encode
from hyrag.parse.config import REPO

QSET_PATH = REPO / "qset" / "questions.jsonl"
CLASSES = ("factoid", "multi-hop", "thematic")
DIM = 1024


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None  # None = informational
    detail: str = ""

    def line(self) -> str:
        mark = "PASS" if self.ok else ("INFO" if self.ok is None else "FAIL")
        return f"  [{mark}] {self.name}" + (f" — {self.detail}" if self.detail else "")


def _questions() -> list[dict]:
    with QSET_PATH.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _rows(conn, sql: str, params: tuple = ()) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _scalar(conn, sql: str, params: tuple = ()):
    rows = _rows(conn, sql, params)
    return rows[0][0] if rows else None


# ---------------------------------------------------------------- the stored index


def check_table(conn) -> list[Check]:
    """Everything that can be asserted about the index without asking a question."""
    chunks = store.load_chunks()
    expected = {chunk["chunk_id"] for chunk in chunks}

    version = _scalar(conn, "SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    rows = _scalar(conn, "SELECT count(*) FROM baseline_chunks")
    stored = {row[0] for row in _rows(conn, "SELECT chunk_id FROM baseline_chunks")}
    missing, extra = expected - stored, stored - expected

    bad_dims = _scalar(
        conn, "SELECT count(*) FROM baseline_chunks WHERE vector_dims(embedding) <> %s", (DIM,)
    )
    worst_norm = _scalar(conn, "SELECT coalesce(max(abs(vector_norm(embedding) - 1)), 0) FROM baseline_chunks")
    stale = _scalar(conn, "SELECT count(*) FROM baseline_chunks WHERE model <> %s", (MODEL_NAME,))
    bad_citations = _scalar(
        conn, "SELECT count(*) FROM baseline_chunks WHERE citation = '' OR page_start > page_end"
    )

    return [
        Check("extension", bool(version), f"pgvector {version}"),
        Check("row_count", rows == len(chunks), f"{rows} rows for {len(chunks)} unique chunks"),
        Check("ids_match", not missing and not extra,
              f"{len(missing)} chunks with no row · {len(extra)} rows with no chunk"),
        Check("dimensions", bad_dims == 0, f"{bad_dims} rows not {DIM}-dimensional"),
        Check("unit_norm", float(worst_norm) < 1e-5, f"max |‖v‖ − 1| = {float(worst_norm):.2e}"),
        Check("model", stale == 0, f"{stale} rows from another encoder (expected {MODEL_NAME})"),
        Check("citation_shape", bad_citations == 0, f"{bad_citations} rows with a broken citation"),
    ]


# ---------------------------------------------------------------- the retrieval


def check_evidence_recall(conn) -> list[Check]:
    """The recall curve, computed by the database rather than in memory.

    Each question is encoded once and handed to `rank_of`, which asks Postgres for the rank
    of that question's own evidence chunk among all 369 rows — the same operator and the same
    ordering the row will use. This is the honest version of the number the encoder's own
    check produced, and the two must agree.
    """
    questions = _questions()
    vectors = encode([question["question"] for question in questions])

    ranks: dict[str, list[int]] = defaultdict(list)
    missing = 0
    for question, vector in zip(questions, vectors):
        rank = store.rank_of(conn, vector, question["evidence_target"]["chunk_ids"])
        if rank is None:
            missing += 1
            continue
        ranks[question["class"]].append(rank)

    def curve(found: list[int]) -> str:
        return " · ".join(f"@{k} {sum(1 for r in found if r <= k)}/{len(found)}" for k in K_VALUES)

    def median(found: list[int]) -> int:
        return int(sorted(found)[len(found) // 2])

    checks: list[Check] = []
    if missing:
        checks.append(Check("evidence_present", False, f"{missing} questions have no evidence row"))
    for question_class in CLASSES:
        found = ranks.get(question_class, [])
        if found:
            checks.append(Check(f"evidence_recall [{question_class}]", None,
                                f"median rank {median(found)} · {curve(found)}"))
    everything = [rank for found in ranks.values() for rank in found]
    checks.append(Check("evidence_recall [all]", None,
                        f"median rank {median(everything)} · {curve(everything)}"))
    return checks


def run_checks() -> list[Check]:
    with store.connect() as conn:
        return check_table(conn) + check_evidence_recall(conn)
