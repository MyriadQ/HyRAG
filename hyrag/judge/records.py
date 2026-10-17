"""judge/records.py — one shape for three rows' answers.

The three rows record different things, in different fields, with different citation units,
and a harness that reads all three has to decide once what "an answer record" means. This
module is that decision, and it is deliberately shallow: it renames and counts, it never
interprets. Anything a row did not record stays `None` — a fabricated zero would enter a
table that then looks complete.

What every row does provide, verified at the node's open (2026-10-16):

    question_id · class · answer · latency_ms · usage{prompt_tokens, completion_tokens}

What each row provides beyond that:

* **R1** — `citations` in canonical form (its own checker already compared them against the
  parse provenance), `evidence[]` with the chunk id and rank of every retrieved chunk.
* **R2** — `our_chunks[]`: the retrieved chunks mapped back through the §5.6 id map. Its
  answer cites **filenames** (`[n] file.pdf`), so its citation unit is the file; the page is
  not recoverable and is not invented (`citations.py`).
* **R3** — `[Data: …]` markers already resolved by `kg_graph.resolve` into canonical
  citations and the chunks they came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from hyrag.judge import citations, config


@dataclass(frozen=True)
class AnswerRecord:
    """One row's answer to one question, in the harness's one shape."""

    row: str                 # "R1" | "R2" | "R3"
    label: str               # "baseline" | "kg-light" | "kg-graph" — for labels, never the judge
    question_id: str
    question_class: str
    answer: str
    latency_ms: int
    tokens_in: int
    tokens_out: int
    calls: int
    citations_canonical: tuple[str, ...]   # locations at the row's own granularity
    citation_unit: str
    refs_asserted: int                     # citation references the row actually emitted
    refs_resolved: int                     # of those, the ones that point at a real retrieved location
    unresolved: tuple[dict, ...]           # the row's own unresolved list, if it has one
    evidence_chunk_ids: tuple[str, ...]    # what the row retrieved, as our chunk ids
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def cost_usd(self) -> float:
        return config.cost_usd(self.tokens_in, self.tokens_out)

    @property
    def citation_rate(self) -> float | None:
        return None if not self.refs_asserted else round(self.refs_resolved / self.refs_asserted, 4)

    @property
    def answer_chars(self) -> int:
        return len(self.answer)


def _usage(record: dict) -> tuple[int, int, int]:
    usage = record.get("usage") or {}
    return (int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            int(usage.get("calls") or 0))


def _load_r1(record: dict) -> AnswerRecord:
    tokens_in, tokens_out, _calls = _usage(record)
    unknown = list(record.get("unknown_sources") or [])
    return AnswerRecord(
        row="R1", label="baseline",
        question_id=record["question_id"], question_class=record["class"],
        answer=record.get("answer") or "",
        latency_ms=int(record.get("latency_ms") or 0),
        tokens_in=tokens_in, tokens_out=tokens_out, calls=1,  # one completion per question
        citations_canonical=tuple(record.get("citations") or []),
        citation_unit=config.ROWS["R1"]["citation_unit"],
        # `citations` are the claims that matched a real evidence location; a `.pdf` the model
        # named that is in no block is a fabricated source and lands in `unknown_sources`.
        refs_asserted=len(record.get("citations") or []) + len(unknown),
        refs_resolved=len(record.get("citations") or []),
        unresolved=tuple({"kind": "source", "id": name, "reason": "mentioned but not retrieved"}
                         for name in unknown),
        evidence_chunk_ids=tuple(row["chunk_id"] for row in (record.get("evidence") or [])),
        raw=record,
    )


def _load_r2(record: dict) -> AnswerRecord:
    tokens_in, tokens_out, calls = _usage(record)
    report = citations.resolve_r2(record)
    return AnswerRecord(
        row="R2", label="kg-light",
        question_id=record["question_id"], question_class=record["class"],
        answer=record.get("answer") or "",
        latency_ms=int(record.get("latency_ms") or 0),
        tokens_in=tokens_in, tokens_out=tokens_out, calls=calls,
        citations_canonical=tuple(report["citations_canonical"]),
        citation_unit=config.ROWS["R2"]["citation_unit"],
        refs_asserted=report["refs_asserted"], refs_resolved=report["refs_resolved"],
        unresolved=tuple(report["unresolved"]),
        evidence_chunk_ids=tuple(chunk["chunk_id"] for chunk in (record.get("our_chunks") or [])),
        raw=record,
    )


def _load_r3(record: dict) -> AnswerRecord:
    tokens_in, tokens_out, calls = _usage(record)
    report = record.get("citation_resolution") or {}
    return AnswerRecord(
        row="R3", label="kg-graph",
        question_id=record["question_id"], question_class=record["class"],
        answer=record.get("answer") or "",
        latency_ms=int(record.get("latency_ms") or 0),
        tokens_in=tokens_in, tokens_out=tokens_out, calls=calls,
        citations_canonical=tuple(record.get("citations") or []),
        citation_unit=config.ROWS["R3"]["citation_unit"],
        # Marker ids are what the row emitted; each resolves through the native tables to a
        # canonical location, and `unresolved` is its own gate's failure list.
        refs_asserted=int(report.get("ids") or 0),
        refs_resolved=int(report.get("resolved_ids") or 0),
        unresolved=tuple(report.get("unresolved") or ()),
        evidence_chunk_ids=tuple(record.get("evidence_chunk_ids") or []),
        raw=record,
    )


LOADERS = {"R1": _load_r1, "R2": _load_r2, "R3": _load_r3}


def load_row(row: str, path: Path | None = None) -> dict[str, AnswerRecord]:
    """`{question_id: AnswerRecord}` for one row, last record per id winning (a re-run)."""
    path = path or config.ROWS[row]["answers"]
    if not path.is_file():
        return {}
    records: dict[str, AnswerRecord] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            # R1's records predate the `status` convention and carry none: the presence of an
            # answer *is* the status. R2/R3 write one explicitly, and a failed record must not
            # be scored as if the row had answered.
            if not raw.get("question_id") or not raw.get("answer"):
                continue
            if raw.get("status") not in (None, "success"):
                continue
            records[raw["question_id"]] = LOADERS[row](raw)
    return records


def load_all(row_keys: tuple[str, ...] = config.ROW_ORDER) -> dict[str, dict[str, AnswerRecord]]:
    return {row: load_row(row) for row in row_keys}
