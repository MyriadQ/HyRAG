"""judge/citations.py — the deterministic half of the judged table (§7.3, D8b).

Citation resolution is **never** a model's opinion: either the location a row named exists in
the parse provenance and belongs to what the row retrieved, or it does not. Three rows cite in
three shapes, so the harness needs one reading of each — and the shapes are not equally
informative, which the table states rather than smooths over:

* **R1** writes canonical `file.pdf, §section, p. N–M` strings. Its own answerer already folded
  and matched them against the evidence, so the record's `citations` are the resolved ones and
  `unknown_sources` are the mentions that matched nothing.
* **R3** writes `[Data: Reports (…)]` markers; `kg_graph.resolve` walks
  marker → community → text unit → our chunk → citation, and lists what did not resolve.
* **R2** writes `[n] filename` references. A filename is the whole of what LightRAG emits: the
  page range is not recoverable from it, and inventing one would put a page in the table that
  the row never claimed. So R2's unit is **file**, resolution means *the file is real and was
  retrieved*, and the table's footnote says so.

The dash fold is `hyrag/baseline/answer.py::_fold`'s — the node page names it as the reference
implementation, and the en dash has already cost one answer its citation once (`U+2013` vs
`-`). Re-implementing it here would be a second chance to get it wrong.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from hyrag.baseline.answer import _fold as fold  # the reference fold, deliberately shared
from hyrag.judge import config

REFERENCES = re.compile(r"^\s*[-*]?\s*\[(\d+)\]\s+(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ChunkIndex:
    """`_ingest.jsonl` read the way a citation check needs it: by citation, by id, by file."""

    by_citation: dict[str, str]      # folded canonical citation -> chunk_id
    by_chunk: dict[str, dict]        # chunk_id -> the ingest record
    files: frozenset[str]            # every file in the corpus

    def canonical_for(self, chunk_id: str) -> str | None:
        record = self.by_chunk.get(chunk_id)
        if not record or not record.get("occurrences"):
            return None
        return record["occurrences"][0]["citation"]

    def has_citation(self, citation: str) -> bool:
        return fold(citation) in self.by_citation


def load_index() -> ChunkIndex:
    by_citation: dict[str, str] = {}
    by_chunk: dict[str, dict] = {}
    files: set[str] = set()
    with config.CHUNKS.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("type") != "chunk":
                continue
            by_chunk[record["chunk_id"]] = record
            files.add(record["file"])
            for occurrence in record.get("occurrences") or []:
                by_citation[fold(occurrence["citation"])] = record["chunk_id"]
    return ChunkIndex(by_citation=by_citation, by_chunk=by_chunk, files=frozenset(files))


def parse_r2_references(answer: str) -> tuple[list[tuple[int, str]], int]:
    """`([(n, filename)…], inline_markers)` from an R2 answer.

    The list after `### References` is LightRAG's own numbering; the inline `[n]` markers are
    what the prose actually points at. They disagree often enough to be worth counting
    separately — a reference list nothing points at is a citation that was never used.
    """
    body, _, tail = answer.partition("### References")
    references = [(int(number), name) for number, name in REFERENCES.findall(tail)]
    inline = len(set(re.findall(r"\[(\d+)\]", body)))
    return references, inline


def resolve_r2(record: dict) -> dict:
    """R2's citation report: file-level claims checked against the corpus and its retrieval."""
    answer = record.get("answer") or ""
    references, inline = parse_r2_references(answer)
    retrieved_files = {chunk.get("file_path") for chunk in (record.get("chunks") or [])}
    citations: list[str] = []
    unresolved: list[dict] = []
    for number, name in references:
        if name not in _index().files:
            unresolved.append({"kind": "file", "id": name, "reason": "not in the corpus"})
            continue
        if name not in retrieved_files:
            unresolved.append({"kind": "file", "id": name, "reason": "cited but not retrieved"})
            continue
        # The row's own candidates for this file, canonicalised through the provenance map —
        # the closest this row can come to a page, and labelled as a derived citation.
        for chunk in record.get("chunks") or []:
            if chunk.get("file_path") == name:
                citation = _index().canonical_for(chunk.get("chunk_id"))
                if citation and citation not in citations:
                    citations.append(citation)
    return {
        "references": references,
        "inline_markers": inline,
        "refs_asserted": len(references),
        "refs_resolved": len(references) - len(unresolved),
        "citations_canonical": citations,
        "unresolved": unresolved,
    }


_INDEX: ChunkIndex | None = None


def _index() -> ChunkIndex:
    """One index per process: `_ingest.jsonl` is 1.4 MB and does not change mid-run."""
    global _INDEX
    if _INDEX is None:
        _INDEX = load_index()
    return _INDEX
