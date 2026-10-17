"""kg_graph/resolve.py — turning GraphRAG's `[Data: …]` markers into our citations.

R3 answers differently from every other row: its global search synthesises a report and cites
it with markers like

    [Data: Reports (184, 527, 464, 468, 448)]

— GraphRAG's *human-readable* ids, not our chunk ids, not a filename, not a page. A
citation-resolution check that looks for `file.pdf` or `p. N–M` in the answer scores this row
at zero while the answer is perfectly grounded. So the resolution happens where it can be
exact, through the native tables:

    [Data: Reports (184)] → communities[human_readable_id=184].text_unit_ids
                          → kg_graph_textunit_map            (text unit → our chunk_id)
                          → _ingest.jsonl occurrences        → `file, §section, p. N–M`

Every hop is a lookup, none is a match: no fuzzy string comparison, no invented page number,
and an id that does not resolve is **reported**, never dropped (§5.6's habit, applied to a row
that has never heard of our ids).

The same chain serves `Entities (…)` and `Relationships (…)` — both tables carry
`text_unit_ids` too — so a marker of any supported kind lands on the same citations.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pyarrow.parquet as pq

from hyrag.kg_graph import config, tables
from hyrag.kg_light import config as kg_light_config

# `Reports (1, 2); Entities (3)` — the kinds GraphRAG's knowledge prompt emits. Both spellings
# in the wild are singular/plural; matching case-insensitively costs nothing.
MARKER = re.compile(r"\[Data:\s*(.*?)\]", re.DOTALL)
PART = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")
KIND_TABLE = {
    "report": "communities",
    "reports": "communities",
    "entity": "entities",
    "entities": "entities",
    "relationship": "relationships",
    "relationships": "relationships",
}


# GraphRAG truncates a long reference list with a literal `+more` token:
#     [Data: Reports (527, 464, 458, 444, 271, +more)]
# and the model sometimes writes its own ellipsis instead:
#     [Data: Relationships (1511...)]
# Neither is an id and neither may be "resolved" — but neither may vanish either: a truncated
# list means the answer cites more evidence than it names, which a reader should know. So both
# are counted as truncations, and only a well-formed id that matches no row is *unresolved*.
TRUNCATION = "+more"
ELLIPSIS = ("...", "\u2026")


def parse_markers(answer: str) -> tuple[dict[str, list[str]], int]:
    """`({kind: [id, …]}, truncated)` for every `[Data: …]` marker, in order of appearance."""
    found: dict[str, list[str]] = {}
    truncated = 0
    for marker in MARKER.findall(answer):
        for kind, id_list in PART.findall(marker):
            key = kind.lower()
            if key not in KIND_TABLE:
                continue
            for value in (item.strip() for item in id_list.split(",")):
                if not value:
                    continue
                if value.startswith("+") or any(mark in value for mark in ELLIPSIS):
                    truncated += 1
                    continue
                found.setdefault(key, []).append(value)
    return found, truncated


def _index(root: Path) -> dict[str, dict[str, dict]]:
    """Each referencing table indexed by `human_readable_id` — the id the markers carry."""
    index: dict[str, dict[str, dict]] = {}
    for table in set(KIND_TABLE.values()):
        rows = tables.read_table(root, table)
        index[table] = {str(row.get("human_readable_id")): row for row in rows}
    return index


def resolve(answer: str, root: Path | None = None) -> dict:
    """Resolve every marker in `answer` to our chunk ids and citations.

    Returns counts, the citations, and the ids that did **not** resolve — the last list being
    the one that must be empty for the answer to count as grounded.
    """
    root = root or config.GRAPH_ROOT
    index = _index(root)
    mapping = json.loads(config.TEXTUNIT_MAP.read_text(encoding="utf-8"))["map"]
    ours = {chunk["chunk_id"]: chunk for chunk in kg_light_config.our_chunks()}

    markers, truncated = parse_markers(answer)
    resolved: list[str] = []
    unresolved: list[dict] = []
    chunk_ids: list[str] = []
    text_units = 0

    for kind, ids in markers.items():
        table = KIND_TABLE[kind]
        rows = index.get(table, {})
        for value in ids:
            row = rows.get(value)
            if row is None:
                unresolved.append({"kind": kind, "id": value, "reason": f"no row in {table}"})
                continue
            resolved.append(value)
            for unit in list(row.get("text_unit_ids") or []):
                text_units += 1
                chunk_id = mapping.get(str(unit))
                if chunk_id is None:
                    unresolved.append({"kind": "text_unit", "id": str(unit), "reason": "not in the text-unit map"})
                    continue
                if chunk_id not in chunk_ids:
                    chunk_ids.append(chunk_id)

    # The citations are derived from the chunk records, never composed here.
    citations: list[str] = []
    for chunk_id in chunk_ids:
        record = ours.get(chunk_id)
        if record is None:
            unresolved.append({"kind": "chunk", "id": chunk_id, "reason": "not in _ingest.jsonl"})
            continue
        citation = record["occurrences"][0]["citation"]
        if citation not in citations:
            citations.append(citation)

    return {
        "markers": {kind: len(ids) for kind, ids in markers.items()},
        "truncated_markers": truncated,
        "ids": sum(len(ids) for ids in markers.values()),
        "distinct_ids": len({(kind, value) for kind, ids in markers.items() for value in ids}),
        "resolved_ids": len(resolved),
        "unresolved": unresolved,
        "text_units": text_units,
        "evidence_chunk_ids": chunk_ids,
        "citations": citations,
    }


def latest_answer(path: Path | None = None) -> tuple[str, str]:
    """The most recent logged answer and its question — so this can be checked without a query."""
    path = path or config.QUERY_LOG
    if not path.is_file():
        raise SystemExit(f"no query log at {path} — run `hyrag.kg_graph.cli query` first")
    record = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    return record["question"], record["answer"]
