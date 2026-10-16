"""kg_graph/tables.py — reading GraphRAG's own tables, and the text-unit ↔ chunk map.

GraphRAG has never heard of our ids. Its `text_units` are keyed by a hash it computes over
the text it received, and its `documents` are keyed from the input filenames. So the map from
its ids back to ours is built the same way `kg-light`'s was (§5.6): **read the system's own
store back and match by content**, never by recomputing a hash on our side and hoping.

Consumers: `checks.py` (the gate) and, later, Phase 2's ablation, which has to know which
native text unit a report or an entity came from.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq

from hyrag.kg_graph import config, inputs

TABLES = (
    "documents",
    "text_units",
    "entities",
    "relationships",
    "communities",
    "community_reports",
)


def table_path(root: Path, name: str) -> Path:
    return root / "output" / f"{name}.parquet"


def read_table(root: Path, name: str) -> list[dict]:
    """One output table as a list of dicts. Empty if the table was not written."""
    path = table_path(root, name)
    if not path.is_file():
        return []
    return pq.read_table(path).to_pylist()


def counts(root: Path) -> dict[str, int]:
    """Row counts for every table the index should have produced."""
    return {name: len(read_table(root, name)) for name in TABLES}


def textunit_map(root: Path, limit: int | None = None) -> dict:
    """Map GraphRAG's text units back to our chunk ids, verified by content.

    Two joins, both checked:

    * **content** — the text unit's text must equal one of our chunks' content. Since the
      ingest set is deduped (§5.6), content is a key, and a text unit that matches nothing is
      a stop-the-line error rather than a row to skip.
    * **filename** — `documents.parquet` carries the input's title, which is our
      `<sha256>.txt`, and each text unit carries a `document_id`. Where the content join is the
      authority, the filename join is the cheap cross-check that catches a document whose text
      was normalised *and* whose id spaces drifted. (In 3.3.0 the column is singular,
      `document_id` — the plural belongs to `documents.text_unit_ids`.)

    Returns the map plus the problems list, so a caller can print counts instead of a verdict.
    """
    our_by_content = {record["content"]: record for record in inputs.chunks()}
    filename_to_chunk = {
        inputs.chunk_filename(record["chunk_id"]): record["chunk_id"] for record in inputs.chunks()
    }

    documents = read_table(root, "documents")
    document_ids: dict[str, str] = {}
    for document in documents:
        title = str(document.get("title") or "")
        document_ids[str(document.get("id"))] = filename_to_chunk.get(title, "")

    mapping: dict[str, str] = {}
    problems: dict[str, list[str]] = {"unmatched_text": [], "unmapped_document": [], "mismatch": []}

    text_units = read_table(root, "text_units")
    if limit:
        text_units = text_units[:limit]
    for unit in text_units:
        text = str(unit.get("text") or "")
        record = our_by_content.get(text) or our_by_content.get(text.strip())
        if record is None:
            problems["unmatched_text"].append(str(unit.get("id")))
            continue
        mapping[str(unit.get("id"))] = record["chunk_id"]

        document_id = str(unit.get("document_id") or "")  # singular: 3.3.0's column name
        expected = document_ids.get(document_id)
        if expected is None:
            problems["unmapped_document"].append(document_id)
        elif expected != record["chunk_id"]:
            problems["mismatch"].append(f"{unit.get('id')}: content says {record['chunk_id']}, filename says {expected}")

    return {
        "text_units": len(text_units),
        "mapped": len(mapping),
        "distinct_chunks": len(set(mapping.values())),
        "unmatched_text": problems["unmatched_text"][:10],
        "unmapped_document": problems["unmapped_document"][:10],
        "mismatch": problems["mismatch"][:10],
        "problems": {key: len(value) for key, value in problems.items()},
        "map": mapping,
    }


def metrics(root: Path, *, since: str | None = None, until: str | None = None) -> list[dict]:
    """GraphRAG's per-process metrics records, optionally limited to a time window.

    They are written **one file per process**, named with the local start time
    (`%Y%m%d_%H%M%S`), and a root keeps every process that ever ran in it — the index, then
    each query. So a window is not optional when the numbers are attributed to a run: without
    it the index's counters would silently include the queries that followed it.
    """
    from datetime import datetime

    local = datetime.now().astimezone().tzinfo
    start = datetime.fromisoformat(since).astimezone(local) if since else None
    end = datetime.fromisoformat(until).astimezone(local) if until else None

    records: list[dict] = []
    for path in sorted((root / "metrics").glob("*.jsonl")):
        try:
            started = datetime.strptime(path.stem, "%Y%m%d_%H%M%S").replace(tzinfo=local)
        except ValueError:
            continue  # an unexpected name means we cannot attribute it; skipping is honest
        if start and started < start:
            continue
        if end and started > end:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json_loads(line))
    return records


def json_loads(line: str) -> dict:
    import json

    return json.loads(line)
