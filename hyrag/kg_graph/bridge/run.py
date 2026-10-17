"""bridge/run.py — `graphrag index` over a bridged root, and the run record.

The row stays a subprocess, exactly as `kg_graph/run.py` argues: the gate can say "its own CLI
completes" because it literally does, and everything known about the run is read back from
what it wrote. The bridge only adds the two numbers this box has to publish — **what the
community layer cost** (calls, tokens, wall clock, windowed from the proxy log by row tag) and
**which graph produced it** (`source_graph_sha256` beside the settings hashes).

The record's shape mirrors `parsed/kg_graph/index_record.json` field for field, so §7.4's two
sides can be read by the same code. The extras are additive and named `bridge_*` or nested:
`source_graph`, `references`, `merges`, `name_variants`, `coverage`, `rerun`.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from hyrag import llm_log
from hyrag.judge.config import RATES  # the same rate card every row's cost is billed at
from hyrag.kg_graph import checks as kg_graph_checks
from hyrag.kg_graph import config, run as kg_graph_run, tables
from hyrag.kg_graph.bridge import convert, root as bridge_root
from hyrag.kg_light import config as kg_light_config

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- the run


def index(root: Path, *, dry_run: bool = False, timeout: int | None = None) -> dict:
    """Run the three-workflow pipeline. Returns the timing/exit facts; never raises on failure.

    A non-zero exit is *recorded* before the caller decides what to do with it: a failed run
    that leaves no record costs a full re-run to diagnose, and the log beside it is the only
    other evidence.
    """
    log_path = root / "logs" / "index.stdout.log"
    started_at = _now()
    started = time.perf_counter()
    args = ["index", "--method", "standard"]
    if dry_run:
        args.append("--dry-run")  # validate the config without a single model call
    completed = kg_graph_run.graphrag(args, root, log_path=log_path, timeout=timeout)
    return {
        "started_at": started_at,
        "finished_at": _now(),
        "wall_clock_s": round(time.perf_counter() - started, 1),
        "exit_code": completed.returncode,
        "stdout_tail": (completed.stdout or "")[-3000:],
        "log_path": str(log_path),
        "dry_run": bool(dry_run),
    }


# ---------------------------------------------------------------- reading the cost back


def proxy_tokens(*, since: str | None, until: str | None, row: str = bridge_root.ROW_TAG) -> dict:
    """This row's calls inside a window, from the proxy's own record of the API's `usage`.

    The window matters for the same reason it does on the native side: the log is append-only
    and shared by every row and every run, so without it a re-run inherits its predecessor's
    tokens and the number reported is a sum of two runs wearing one run's name.
    """
    entries = []
    for record in _proxy_records():
        stamp = str(record.get("ts") or "")
        if since and stamp < since:
            continue
        if until and stamp > until:
            continue
        entries.append(record)

    tagged = [record for record in entries if record.get("row") == row]
    untagged = [record for record in entries if record.get("row") == "unknown"]
    totals = {
        "llm_calls": len(tagged),
        "prompt_tokens": sum(int(record.get("prompt_tokens") or 0) for record in tagged),
        "completion_tokens": sum(int(record.get("completion_tokens") or 0) for record in tagged),
        "total_tokens": sum(int(record.get("total_tokens") or 0) for record in tagged),
        "prompt_cache_hit_tokens": sum(int(record.get("prompt_cache_hit_tokens") or 0) for record in tagged),
        "prompt_cache_miss_tokens": sum(int(record.get("prompt_cache_miss_tokens") or 0) for record in tagged),
        "rewritten": sum(1 for record in tagged if record.get("response_format_rewritten")),
        "errors": sum(1 for record in tagged if int(record.get("status") or 0) >= 400),
    }
    return {
        "row": row,
        "since": since,
        "until": until,
        **totals,
        "untagged_calls": len(untagged),  # a tag that fell off is a bug, so it is counted
    }


def _proxy_records() -> list[dict]:
    path = llm_log.LOG_PATH
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def usd(tokens: dict) -> float:
    """The bill, splitting the prompt into DeepSeek's cache-hit and cache-miss rates.

    `judge.config.cost_usd` intentionally bills every input token at the miss rate (an upper
    bound applied uniformly). The bridge's own record has the split, so it uses it — and keeps
    the rate card in one place, imported rather than retyped.
    """
    return round(
        int(tokens.get("prompt_cache_miss_tokens") or 0) / 1e6 * RATES["input_miss"]
        + int(tokens.get("prompt_cache_hit_tokens") or 0) / 1e6 * RATES["input_hit"]
        + int(tokens.get("completion_tokens") or 0) / 1e6 * RATES["output"],
        4,
    )


def coverage(root: Path) -> dict:
    """How much of the entity set reaches a community — and therefore a report.

    `cluster_graph.use_lcc` is `True` (GraphRAG's default, and what R3 ran with), so anything
    outside the largest connected component is never clustered and never cited by a report.
    The ratio is published rather than fixed: changing it would make §7.4 compare clustering
    settings instead of indexes. R3's own numbers put the loss at 315/2,817 (11%).
    """
    entities = tables.read_table(root, "entities")
    communities = tables.read_table(root, "communities")
    covered = {entity_id for row in communities for entity_id in (row.get("entity_ids") or [])}
    return {
        "entities": len(entities),
        "in_communities": len(covered),
        "outside_communities": len(entities) - len(covered),
        "ratio": round(len(covered) / len(entities), 4) if entities else 0.0,
        "communities": len(communities),
    }


# ---------------------------------------------------------------- the record


def record_path(root: Path) -> Path:
    return root / bridge_root.RECORD_NAME


def write_record(
    root: Path,
    run: dict,
    *,
    previous: dict | None = None,
) -> dict:
    """Write the run record: pins, hashes, counts, tokens, and the previous run beside it.

    `previous` is run one's summary when this is run two: the artifact the record describes is
    always the *final* one, and the run before it is kept in the record for the diff and for the
    cost comparison (a cached second pass costs nothing, and that is a finding).
    """
    manifest = bridge_root.manifest(root)
    tables_counts = tables.counts(root)
    tokens = proxy_tokens(since=run["started_at"], until=run["finished_at"])
    cross_check = kg_graph_checks.graphrag_metrics(root, since=run["started_at"], until=run["finished_at"])

    record = {
        "type": "kg_graph_bridge_index",
        "graphrag_pin": config.GRAPHRAG_PIN,
        "settings_sha256": manifest["settings_sha256"],  # the versioned original
        "settings_root_sha256": manifest["settings_root_sha256"],  # the patched copy that ran
        "prompts_sha256": manifest["prompts_sha256"],
        "overrides": manifest["overrides"],
        "source_graph": manifest["source_graph"],
        "idmap": {
            "path": str(kg_light_config.IDMAP_PATH),
            "sha256": convert.sha256_file(kg_light_config.IDMAP_PATH),
        },
        "text_units": manifest["text_units"],
        "references": manifest["references"],
        "merges": manifest["merges"],
        "name_variants": manifest["name_variants"],
        "edges": manifest["edges"],
        "smoke": manifest.get("smoke", False),
        "llm_model": config.LLM_MODEL,
        "embedding_base": config.EMBEDDING_BASE,
        "workspace": str(root),
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "wall_clock_s": run["wall_clock_s"],
        "exit_code": run["exit_code"],
        "tables": tables_counts,
        "coverage": coverage(root),
        "tokens": tokens,
        "cross_check": cross_check,
        "cost": {
            "rate_card": "hyrag/judge/config.py RATES ($/1M: miss {input_miss}, hit {input_hit}, out {output})".format(**RATES),
            "paid_run_usd": usd((previous or {}).get("tokens") or tokens),
            "final_run_usd": usd(tokens) if previous else 0.0,
            "total_usd": round(
                usd((previous or {}).get("tokens") or tokens) + (usd(tokens) if previous else 0.0), 4
            ),
        },
        "previous_run": previous,
    }
    path = record_path(root)
    path.write_text(json.dumps(record, **_JSON) + "\n", encoding="utf-8")
    return record


def dumps(record: dict) -> str:
    """The record's on-disk form — one line, sorted keys, so two records diff by eye."""
    return json.dumps(record, **_JSON)


def load_record(root: Path) -> dict | None:
    path = record_path(root)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
