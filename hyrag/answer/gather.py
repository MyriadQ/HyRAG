"""answer/gather.py — the route the router chose, turned into evidence entries.

The per-class evidence budget is a decision, not a default: factoid 6, multi-hop 8, thematic
12. A factoid needs the chunk that holds the fact; a thematic answer that reads 20 chunks is
prompting for a report, which is what `kg-graph` already produces and what the delivered
system deliberately does **not** ship (the 358-second, 24 k-character turn).

For multi-hop the entries come from `fuse`'s **reranked** list, not from RRF's order — the
cross-encoder is the last thing that looked at relevance, and going back to the pre-rerank
order here would silently undo it.
"""

from __future__ import annotations

from hyrag.arms import runner
from hyrag.fuse import combine as fuse_combine
from hyrag.fuse import rerank as rerank_module
from hyrag.kg_light import config as kg_light_config
from hyrag.router import strategy

EVIDENCE_BUDGET = {"factoid": 6, "multi-hop": 8, "thematic": 12}


def catalogue() -> dict[str, dict]:
    return {record["chunk_id"]: record for record in kg_light_config.our_chunks()}


def gather(question: dict, *, encoder: "rerank_module.CrossEncoder | None" = None) -> dict:
    """Run the routed arm(s) and return the evidence entries in the order the answer should see."""
    decision = strategy.for_class(question["class"])
    budget = EVIDENCE_BUDGET[question["class"]]
    arms: dict[str, object] = {}

    if decision["fuse"]:
        arms = {
            "kg-light": runner.answer_one("kg-light", question),
            "kg-graph-bridged": runner.answer_one("kg-graph-bridged", question),
        }
        fused = fuse_combine.fuse(question, arms, reranker=encoder)
        entries = [
            {"chunk_id": row["chunk_id"], "citation": row["citation"], "file": row["file"],
             "text": row["text"], "rerank": row["rerank"], "rrf": row["rrf"]}
            for row in fused["reranked"][:budget]
        ]
        return {"route": decision, "arms": arms, "evidence": entries, "fusion": fused}

    arm = decision["arms"][0]
    record = runner.answer_one(arm, question)
    arms = {arm: record}
    catalogue_ = catalogue()
    entries = []
    for chunk_id in record.evidence_chunk_ids[:budget]:
        chunk = catalogue_.get(chunk_id)
        if chunk is None:  # the arms' ids are already ours; a miss means drift, not a skip
            raise SystemExit(f"{question['id']}: evidence id {chunk_id} is not in _ingest.jsonl")
        entries.append({"chunk_id": chunk_id, "citation": chunk["occurrences"][0]["citation"],
                        "file": chunk["file"], "text": chunk["content"]})
    return {"route": decision, "arms": arms, "evidence": entries, "fusion": None}
