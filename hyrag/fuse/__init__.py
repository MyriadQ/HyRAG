"""fuse — RRF across the two arms, then a cross-encoder rerank.

The multi-hop class is where both arms fire: LightRAG returns entity/relation neighbourhoods in
retrieval order, the bridged GraphRAG returns an entity-centred context with page-range
citations in the order its answer cites them. Neither dominates the benchmark, so the roadmap's
design is **merge by rank** (D5) and then re-score the merged shortlist with a cross-encoder.

The order is a decision, not a preference: **RRF first, reranker second** (README → Landmines).
RRF is rank-based and score-agnostic — the only comparison that is legal across two systems
whose scores are different quantities — and the reranker then re-scores a bounded shortlist with
one model. The shortlist is the reranker's universe, so RRF's `k` and the shortlist size set its
recall ceiling.

| module | job |
|---|---|
| `rrf.py` | reciprocal rank fusion over the arms' orderings — the arithmetic, nothing else |
| `rerank.py` | the cross-encoder, loaded from local disk, CPU, truncation recorded |
| `combine.py` | arms' records + the question → one ranked evidence list with provenance |
| `checks.py` | the gate: both systems in the fused top-k, the reranker reorders, every chunk resolves |
| `cli.py` | `fuse · check` |
"""
