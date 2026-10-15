"""serving/ — the HTTP surface this project exposes to itself.

One endpoint so far: an OpenAI-compatible `/v1/embeddings`, because two of the four rows
(LightRAG, and GraphRAG later) reach embeddings through a client that only speaks HTTP.
It exists so that all rows point at the **same** embedding service (§6.1) rather than each
carrying its own copy of the encoder — and so `.env`, one port and one `/health` are the
whole of the wiring.
"""
