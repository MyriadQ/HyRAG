"""hyrag.baseline — the naive RAG control row (R1).

Dense embeddings over the shared chunks, top-k by cosine, one answer, citations copied from
the rows. No lexical arm, no fusion, no reranker (settled 2026-10-11) — those belong to
Phase 2's `fuse`. The encoder lives in `hyrag.embedding` and is shared with the other rows;
`store.py` here is the index that reads it.
"""
