"""kg_graph/ — R3: GraphRAG run natively over the shared chunks.

Its own environment (`.venv-graphrag`) and its own CLI, because `graphrag==3.3.0` and
`docling` cannot share one interpreter. See `nodes/kg-graph.md` for the contract and the
gate, and `hyrag/kg_graph/config.py` for the wiring this package adds around it.
"""

from __future__ import annotations

from hyrag.kg_graph import config  # noqa: F401

__all__ = ["config"]
