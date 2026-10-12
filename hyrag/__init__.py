"""HyRAG — a hybrid RAG pipeline (naive / GraphRAG / LightRAG) over one shared graph.

Package layout mirrors the roadmap boxes (`ROADMAP.md`):

    hyrag.parse     PDF -> blocks (page-anchored) -> chunks (page range + section)
    hyrag.kg        (next) LightRAG extraction over the shared graph
    ...

Only the parse box exists. Everything else is added when its node opens.
"""
