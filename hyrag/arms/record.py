"""arms/record.py — the one shape both arms emit.

The fields are `hyrag/judge/records.py::AnswerRecord`'s, minus the row/label bookkeeping the
benchmark needed: an arm's identity is `arm`, and its number in the table is a Phase 2 addendum
decision. Keeping the names identical is the point — the R4 row and §7.4's ablation must not
need a fourth adapter to read the delivered system.

Two fields exist to stop later boxes from guessing:

* **`citation_unit`** — LightRAG cites **files**; the page range is not recoverable from
  `[n] filename` and is not invented. The bridged arm cites `file, §section, p. N–M`, because
  `kg_graph.resolve` walks the chain that far. A table that compares them without saying so
  would be a column heading that lies.
* **`ranked`** — whether the arm's evidence list carries an order worth fusing. LightRAG returns
  its chunks in retrieval order; GraphRAG's global search returns a map-reduced answer and an
  unordered set of report ids. `fuse` needs to know which arms can supply a ranking before it
  builds one.

A field an arm cannot fill stays `None`; it never becomes a zero. `judge/records.py` made the
same call and for the same reason: a fabricated zero enters a table that then looks complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AnswerRecord:
    """One arm's answer to one question, with everything the later boxes need."""

    arm: str                       # "kg-light" | "kg-graph-bridged"
    question_id: str
    question_class: str
    question: str
    answer: str
    latency_ms: int
    tokens_in: int
    tokens_out: int
    calls: int
    citations: tuple[str, ...]     # at `citation_unit`'s granularity
    citation_unit: str
    evidence_chunk_ids: tuple[str, ...]
    unresolved: tuple[dict, ...]   # ids the arm could not map — must be empty for a green gate
    method: str                    # the exact configuration: `mix`, `global`+level 2, `local`
    ranked: bool
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def refused(self) -> bool:
        """GraphRAG answers an ungrounded question with a canned refusal; that is not an answer."""
        return "unable to answer this question" in self.answer.lower()

    @property
    def ok(self) -> bool:
        return bool(self.answer.strip()) and not self.refused

    @property
    def cost_usd(self) -> float:
        from hyrag.judge.config import cost_usd  # the one rate card in the repo

        return cost_usd(self.tokens_in, self.tokens_out)

    def to_dict(self) -> dict:
        return {
            "arm": self.arm,
            "question_id": self.question_id,
            "question_class": self.question_class,
            "question": self.question,
            "answer": self.answer,
            "latency_ms": self.latency_ms,
            "usage": {
                "prompt_tokens": self.tokens_in,
                "completion_tokens": self.tokens_out,
                "calls": self.calls,
            },
            "citations": list(self.citations),
            "citation_unit": self.citation_unit,
            "evidence_chunk_ids": list(self.evidence_chunk_ids),
            "unresolved": list(self.unresolved),
            "method": self.method,
            "ranked": self.ranked,
            "status": "success" if self.ok else ("refused" if self.refused else "empty"),
            "raw": self.raw,
        }
