"""fuse/rrf.py — reciprocal rank fusion, and nothing else.

`score(c) = Σ_arm 1 / (k + rank_arm(c))`, with ranks starting at 1. The paper's default
`k = 60` is used and not tuned: it flattens the head of each list, which is what makes the
fusion robust to one arm being confidently wrong, and a tuned `k` would be a second hidden
choice the report has to defend.

Two properties matter more than the constant:

* **Only order is consumed.** The arms' scores — a vector cosine, a LightRAG `mix` score, a
  community-report rating — are never compared. That is the whole of D5's argument.
* **Ties are broken by chunk id.** RRF ties exactly whenever two chunks hold the same ranks in
  the same lists; leaving the order to dict insertion would make the output depend on the order
  the arms happened to return, which is precisely what a run-twice check is meant to catch.
"""

from __future__ import annotations


def reciprocal_rank_fusion(
    rankings: dict[str, list[str]],
    *,
    k: int = 60,
) -> list[dict]:
    """`[{chunk_id, score, ranks: {arm: rank}}]`, best first, ties broken by chunk id.

    `rankings` is `{arm: [chunk_id, …]}`; duplicates inside a list are dropped keeping the
    best (first) position, because an arm that returns the same chunk twice did not retrieve
    it twice.
    """
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for arm, chunks in rankings.items():
        seen: set[str] = set()
        position = 0
        for chunk_id in chunks:
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            position += 1
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + position)
            ranks.setdefault(chunk_id, {})[arm] = position

    return [
        {"chunk_id": chunk_id, "score": round(scores[chunk_id], 8), "ranks": ranks[chunk_id]}
        for chunk_id in sorted(scores, key=lambda cid: (-scores[cid], cid))
    ]
