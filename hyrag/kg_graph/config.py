"""kg_graph/config.py — R3's wiring: paths, the GraphRAG interpreter, the endpoints.

GraphRAG runs as a **subprocess** (`graphrag index` / `graphrag query`) in its own
interpreter, because `graphrag==3.3.0` and `docling` require disjoint `typer` ranges and
cannot coexist in one environment. Everything this module names is therefore either
(a) something we hand to that subprocess, or (b) something we read back from what it wrote.

Credentials and endpoints come from `hyrag.kg_light.config` — one place reads `.env`, so the
two rows cannot disagree about which DeepSeek account or which embedding service they used.
That import also sets `POSTGRES_*` in this process; GraphRAG never sees those tables, and the
child environment it builds is explicit about what it passes on.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from hyrag.kg_light.config import deepseek_base, deepseek_key
from hyrag.parse.config import PARSED, REPO

# ---------------------------------------------------------------- paths

GRAPH_ROOT = PARSED / "kg_graph"  # the real root (gitignored, regenerable)
SMOKE_ROOT = PARSED / "kg_graph_smoke"  # ~8 chunks, for validating the config
SETTINGS_PATH = Path(__file__).with_name("settings.yaml")  # versioned (§7.5)
PROMPTS_DIR = Path(__file__).with_name("prompts")  # the shipped prompts, versioned too
INDEX_RECORD_NAME = "index_record.json"  # inside the root, so a smoke run cannot
                                         # overwrite the corpus run's numbers
TEXTUNIT_MAP = PARSED / "kg_graph_textunit_map.json"
QUERY_LOG = REPO / "results" / "kg_graph_queries.jsonl"

# ---------------------------------------------------------------- the pinned interpreter

GRAPH_INTERPRETER = REPO / ".venv-graphrag"
GRAPHRAG_BIN = GRAPH_INTERPRETER / "bin" / "graphrag"
GRAPHRAG_PIN = "graphrag==3.3.0"
REQUIREMENTS = REPO / "envs" / "graphrag" / "requirements.txt"

# ---------------------------------------------------------------- the models

EMBEDDING_BASE = os.environ.get("HYRAG_EMBEDDING_URL", "http://127.0.0.1:8001/v1")
# The chat side of the same service: `hyrag/serving/llm.py` repairs DeepSeek's missing
# `json_schema` support and counts every call. GraphRAG's completion endpoint is therefore
# local — and its tokens come from the API's own `usage` block, not from a guess.
LLM_BASE = os.environ.get("HYRAG_LLM_BASE", EMBEDDING_BASE)
LLM_PROXY_LOG = PARSED / "llm_proxy.jsonl"
EMBEDDING_DIM = 1024
LLM_MODEL = "deepseek-flash"

# ---------------------------------------------------------------- the run's shape

# D12: dynamic community selection is OFF by default in GraphRAG, and the default degrades
# global search to a flat top-k over reports with no error. The bridged arm in Phase 2 runs
# the same setting, so §7.4's comparison is between indexes and not between search settings.
COMMUNITY_LEVEL = 2
DYNAMIC_SELECTION = True

SMOKE_CHUNKS = 8  # enough for extraction to produce something, small enough to read


def settings_sha256() -> str:
    """The configuration's identity, recorded with every run's numbers."""
    return hashlib.sha256(SETTINGS_PATH.read_bytes()).hexdigest()


def prompts_sha256() -> str:
    """One hash over all shipped prompts — they are part of the measured configuration."""
    digest = hashlib.sha256()
    for path in sorted(PROMPTS_DIR.glob("*.txt")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def child_env() -> dict[str, str]:
    """The environment the GraphRAG subprocess runs in.

    `settings.yaml` interpolates `${DEEPSEEK_API_KEY}`, `${HYRAG_LLM_BASE}` and
    `${HYRAG_EMBEDDING_BASE}` (graphrag_common.config → `Template(text).substitute(os.environ)`),
    so the key travels in the environment and is never written to the root directory.
    """
    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = deepseek_key()
    env["DEEPSEEK_BASE_URL"] = deepseek_base()
    env["HYRAG_EMBEDDING_BASE"] = EMBEDDING_BASE
    env["HYRAG_LLM_BASE"] = LLM_BASE
    return env
