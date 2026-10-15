"""kg_light/ — R2: LightRAG over the shared chunks, plus the id map Phase 2 is built on.

The row's whole wiring — endpoints, model names, paths, concurrency — is named here, once,
so that no other file in this package contains a constant that can drift from it. Import
this module **before** `lightrag`: it sets the Postgres environment LightRAG's storage
backends read (`POSTGRES_*`, `POSTGRES_WORKSPACE`), and a backend that connects before
those are set writes to the wrong database with no error.
"""

from __future__ import annotations

from hyrag.kg_light import config  # noqa: F401  (sets POSTGRES_* at import time)

__all__ = ["config"]
