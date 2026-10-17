"""arms/lightrag.py — the `kg-light` arm: LightRAG's own answer, its own retrieval, its own mode.

The arm is the **row that was measured**: `mix` (150/150 records in
`results/kg_light_answers.jsonl`), `enable_llm_cache=False` so a cosine-cached answer cannot
report `calls: 0` and a $0.00 cost for work that was paid for, and the chunk keys resolved
through §5.6's map at the moment they are used.

Citations are **filenames**, because that is the whole of what LightRAG emits
(`* [1] aaib_1-2025_….pdf`). A page range cannot be recovered from a filename and is not
manufactured here — `judge/citations.py` says the same thing at the table's edge. A filename
that is not one of the corpus's 11 is recorded as unresolved, not dropped.

The instance is built once and reused for a whole run: storage initialisation and the Postgres
connection are the expensive parts, not the question.
"""

from __future__ import annotations

import re
import time

from hyrag.arms.record import AnswerRecord
from hyrag.kg_light import config, query

REFERENCES = re.compile(r"^\s*[-*]?\s*\[(\d+)\]\s+(\S+)\s*$", re.MULTILINE)
INLINE = re.compile(r"\[(\d+)\]")


class LightRAGArm:
    name = "kg-light"
    citation_unit = "file"
    ranked = True  # LightRAG returns its chunks in retrieval order

    def __init__(self, mode: str | None = None):
        self.mode = mode or config.QUERY_MODE
        self._rag = None

    async def open(self) -> "LightRAGArm":
        from hyrag.kg_light.builder import build_rag

        # The answer cache is off; the extraction cache is a different cache and is irrelevant
        # here (the index is already built).
        self._rag = build_rag(enable_llm_cache=False)
        await self._rag.initialize_storages()
        return self

    async def close(self) -> None:
        if self._rag is not None:
            await self._rag.finalize_storages()
            self._rag = None

    async def answer(self, question: dict) -> AnswerRecord:
        if self._rag is None:
            raise RuntimeError("LightRAGArm.answer() before open()")
        started = time.perf_counter()
        raw = await query.ask(self._rag, question["question"], mode=self.mode)
        raw["question_id"] = question["id"]
        raw["class"] = question["class"]
        raw = query.resolve(raw)
        latency_ms = round((time.perf_counter() - started) * 1000)

        citations, unresolved = _citations(raw.get("answer") or "", _corpus_files())
        usage = raw.get("usage") or {}
        return AnswerRecord(
            arm=self.name,
            question_id=question["id"],
            question_class=question["class"],
            question=question["question"],
            answer=str(raw.get("answer") or ""),
            latency_ms=latency_ms,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            calls=int(usage.get("calls") or 0),
            citations=tuple(citations),
            citation_unit=self.citation_unit,
            evidence_chunk_ids=tuple(chunk["chunk_id"] for chunk in raw.get("our_chunks") or []),
            unresolved=tuple(unresolved),
            method=self.mode,
            ranked=self.ranked,
            raw=raw,
        )


def _corpus_files() -> set[str]:
    """The 11 files in the ingests — a filename outside this set is a fabricated source."""
    return {record["file"] for record in config.our_chunks()}


def _citations(answer: str, files: set[str]) -> tuple[list[str], list[dict]]:
    """LightRAG's `### References` list, checked against the corpus.

    The inline `[n]` markers are counted but not resolved: the numbering is LightRAG's, and a
    marker whose reference is missing is a citation the reader cannot follow — recorded as
    unresolved rather than guessed from the surrounding prose.
    """
    body, _, tail = answer.partition("### References")
    references = [(int(number), name) for number, name in REFERENCES.findall(tail)]
    inline = {int(number) for number in INLINE.findall(body)}

    citations: list[str] = []
    unresolved: list[dict] = []
    for number, name in references:
        if name not in files:
            unresolved.append({"kind": "file", "id": name, "reason": "not in the corpus"})
        elif name not in citations:
            citations.append(name)
    for number in sorted(inline - {number for number, _name in references}):
        unresolved.append({"kind": "reference", "id": f"[{number}]",
                           "reason": "cited inline with no reference entry"})
    return citations, unresolved
