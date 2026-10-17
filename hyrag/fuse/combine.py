"""fuse/combine.py — two arms' records in; one ranked evidence list with provenance out.

The shape of the output is what `answer` and the judge need: for each chunk, **why it is there**
(the RRF score and the rank each arm gave it), **what it says** (its text, truncated only for
scoring), and **where it is** (`file, §section, p. N–M`, derived from `_ingest.jsonl` — never
composed here).

Two boundary rules that would otherwise drift:

* **The bridged arm's order is citation order.** `kg_graph.resolve` returns evidence in the
  order the answer's markers mention it, which is a real order and a weak prior. It enters RRF
  as a rank like any other, and the record prints both arms' ranks so a reader can see that the
  two lists were weighed the same rather than one being treated as a relevance ranking.
* **A fused chunk that cannot be traced back to a chunk record is an error**, not a row with an
  empty citation. `_ingest.jsonl` is the authority; a chunk id it does not hold means the arms
  and the map have drifted, and the only honest response is to stop.

`shortlist` bounds what the cross-encoder sees: RRF over the full union, then the top `N` are
re-scored and reordered. `k` and `N` ride in the output so an ablation can move them later
without the box silently changing its meaning.
"""

from __future__ import annotations

from hyrag.arms.record import AnswerRecord
from hyrag.fuse import rerank as rerank_module
from hyrag.fuse.rrf import reciprocal_rank_fusion
from hyrag.kg_light import config as kg_light_config

DEFAULT_K = 60
DEFAULT_SHORTLIST = 20
TOP_K_REPORT = 10  # what "carries evidence from both systems" is measured over


def chunks() -> dict[str, dict]:
    """`_ingest.jsonl` as `chunk_id -> record`; cached per process."""
    return {record["chunk_id"]: record for record in kg_light_config.our_chunks()}


def fuse(
    question: dict,
    arms: dict[str, AnswerRecord],
    *,
    reranker: "rerank_module.CrossEncoder | None" = None,
    k: int = DEFAULT_K,
    shortlist: int = DEFAULT_SHORTLIST,
) -> dict:
    """Fuse the arms' evidence for one multi-hop question. Returns the record `answer` reads."""
    if question["class"] != "multi-hop":
        raise ValueError(
            f"fusion is for multi-hop only — {question['id']} is {question['class']}. "
            f"The thematic class is route-only and factoid is single-arm (D5, nodes/fuse.md)"
        )
    if not arms:
        raise ValueError("fuse needs at least one arm record")

    rankings = {arm: list(record.evidence_chunk_ids) for arm, record in arms.items()}
    fused = reciprocal_rank_fusion(rankings, k=k)

    catalogue = chunks()
    head = fused[:shortlist]
    missing = [row["chunk_id"] for row in head if row["chunk_id"] not in catalogue]
    if missing:
        raise SystemExit(
            f"{question['id']}: {len(missing)} fused chunk id(s) are not in _ingest.jsonl "
            f"({missing[:3]}) — the arms and the chunk catalogue have drifted"
        )

    passages = [catalogue[row["chunk_id"]]["content"] for row in head]
    encoder = reranker or rerank_module.CrossEncoder()
    scores = encoder.score(question["question"], passages)

    entries = []
    for row, passage, score in zip(head, passages, scores, strict=True):
        record = catalogue[row["chunk_id"]]
        entries.append(
            {
                "chunk_id": row["chunk_id"],
                "rrf": row["score"],
                "ranks": row["ranks"],
                "rerank": round(score, 6),
                "citation": record["occurrences"][0]["citation"],
                "file": record["file"],
                "chars": len(passage),
                "truncated": encoder.truncated(passage),
                "text": passage,
            }
        )
    reranked = sorted(entries, key=lambda entry: (-entry["rerank"], entry["chunk_id"]))
    moved = sum(1 for left, right in zip(entries, reranked, strict=True)
                if left["chunk_id"] != right["chunk_id"])

    contributions = {
        arm: sum(1 for entry in entries[:TOP_K_REPORT] if arm in entry["ranks"])
        for arm in rankings
    }
    return {
        "question_id": question["id"],
        "question_class": question["class"],
        "question": question["question"],
        "config": {
            "rrf_k": k,
            "shortlist": shortlist,
            "top_k_report": TOP_K_REPORT,
            "reranker": encoder.id,
            "max_length": encoder.max_length,
            "arms": {arm: {"method": record.method, "latency_ms": record.latency_ms,
                           "calls": record.calls, "tokens_in": record.tokens_in,
                           "tokens_out": record.tokens_out,
                           "citations": len(record.citations),
                           "evidence": len(record.evidence_chunk_ids)}
                     for arm, record in arms.items()},
        },
        "fused": fused,
        "shortlist": entries,
        "reranked": reranked,
        "diagnostics": {
            "arms_in_top_k": contributions,
            "both_arms_in_top_k": all(count > 0 for count in contributions.values()),
            "positions_moved": moved,
            "reordered": moved > 0,
            "truncated": sum(1 for entry in entries if entry["truncated"]),
            "unresolved": 0,  # a missing chunk id raises above; the field exists for the gate
        },
    }
