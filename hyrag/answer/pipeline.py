"""answer/pipeline.py — question → one answer record, with the used/invented split.

The record is the delivered system's unit of evidence: what was asked, which route it took,
which chunks were handed in with which citations, what came back, which citations the answer
actually used, and what the whole turn cost (arrows from both arms plus the one generation
call).

Nothing here re-retrieves or re-routes. The arms already answered, `fuse` already reranked, the
router already chose — this box only writes and checks.
"""

from __future__ import annotations

from hyrag.answer import gather, render
from hyrag.baseline.answer import verify_citations
from hyrag.judge.config import cost_usd


def answer_question(question: dict, *, encoder=None) -> dict:
    gathered = gather.gather(question, encoder=encoder)
    entries = gathered["evidence"]
    result = render.generate(question["question"], entries)

    used, unknown = verify_citations(
        result["answer"],
        [{"citation": entry["citation"], "file": entry["file"]} for entry in entries],
    )
    by_citation = {entry["citation"]: entry for entry in entries}
    used_entries = [by_citation[citation] for citation in used if citation in by_citation]

    arms = gathered["arms"]
    arm_cost = sum(record.cost_usd for record in arms.values())
    answer_cost = cost_usd(result["usage"]["prompt_tokens"], result["usage"]["completion_tokens"])
    return {
        "question_id": question["id"],
        "question_class": question["class"],
        "question": question["question"],
        "route": {"arms": gathered["route"]["arms"], "fuse": gathered["route"]["fuse"]},
        "evidence": entries,
        "answer": result["answer"],
        "refused": render.refusal(result["answer"]),
        "citations_used": used,
        "unknown_sources": unknown,
        "usage": result["usage"],
        "latency_ms": result["latency_ms"],
        "prompt_sha256": result["prompt_sha256"],
        "arms": {
            arm: {"method": record.method, "calls": record.calls,
                  "tokens_in": record.tokens_in, "tokens_out": record.tokens_out,
                  "latency_ms": record.latency_ms, "cost_usd": record.cost_usd,
                  "citations": len(record.citations)}
            for arm, record in arms.items()
        },
        "cost_usd": round(answer_cost + arm_cost, 6),
        "answer_cost_usd": round(answer_cost, 6),
        "fusion": (None if gathered["fusion"] is None else {
            "positions_moved": gathered["fusion"]["diagnostics"]["positions_moved"],
            "arms_in_top_k": gathered["fusion"]["diagnostics"]["arms_in_top_k"],
        }),
    }
