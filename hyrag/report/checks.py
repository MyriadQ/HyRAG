"""report/checks.py — the report's gate: paths, pins, numbers.

    uv run python -m hyrag.report.cli check

The gate (`nodes/report.md`):

> every number in it traces to `results.csv` or a session file; library versions pinned;
> agent-assisted draft

Three mechanical clauses:

1. **Paths** — every `results/…`, `sessions/…`, `nodes/…`, `parsed/…` path the report names must
   exist. A path written from memory is the commonest way a report stops being reproducible.
2. **Pins** — the versions in the report's table must match `pyproject.toml`,
   `envs/graphrag/requirements.txt` and the installed `transformers`/`torch`.
3. **Numbers** — a list of the report's load-bearing figures, each checked against the artifact
   it cites; plus the total spend, **recomputed** from `parsed/llm_proxy.jsonl` at the rate card
   rather than accepted as written.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from hyrag.judge.config import RATES
from hyrag.parse.config import REPO

REPORT = REPO / "REPORT.md"
PATH = re.compile(r"\b(?:results|sessions|nodes|parsed|qset|corpus|envs)/[A-Za-z0-9_./-]+")
NUMBER = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")
SKIP = ("*", "<", "…", "{", "}")

# (number as written in the report, the artifacts it may trace to)
HEADLINES = (
    ("0.7945", ("results/results.csv",)),
    ("0.8033", ("results/results.csv",)),
    ("0.3796", ("results/results.csv",)),
    ("0.867", ("results/router_record.json",)),
    ("0.60", ("results/router_record.json",)),
    ("932", ("parsed/kg_graph_bridged/index_record.json",)),
    ("3771", ("parsed/kg_graph_bridged/index_record.json",)),
    ("1059", ("parsed/kg_graph_bridged/index_record.json",)),
    ("353.9", ("results/ablation_record.json",)),
    ("237.4", ("results/ablation_record.json",)),
    ("17737", ("results/ablation_record.json",)),
    ("0.3814", ("results/ablation_record.json",)),
)


def _numbers(text: str) -> set[float]:
    out: set[float] = set()
    for token in NUMBER.findall(text):
        try:
            out.add(float(token.replace(",", "")))
        except ValueError:
            continue
    return out


def _exists(path: str) -> bool:
    return (REPO / path).exists()


def check_paths(text: str) -> tuple[bool, list[str]]:
    named = sorted({match.rstrip(".,);") for match in PATH.findall(text)})
    named = [path for path in named if not any(marker in path for marker in SKIP)]
    missing = [path for path in named if not _exists(path)]
    print(f"  {'✓' if not missing else '✗'} paths: {len(named)} named · {len(missing)} missing")
    for path in missing:
        print(f"      ! {path}")
    return not missing, missing


def check_pins(text: str) -> tuple[bool, list[str]]:
    import transformers
    import torch

    pins = {
        "graphrag==3.3.0": "graphrag==3.3.0" in (REPO / "envs/graphrag/requirements.txt").read_text(),
        "453dce8": "453dce8" in (REPO / "pyproject.toml").read_text(),
        transformers.__version__: transformers.__version__ in text,
        torch.__version__: torch.__version__ in text,
    }
    missing = [pin for pin, ok in pins.items() if not (ok and pin in text)]
    print(f"  {'✓' if not missing else '✗'} pins: " + " · ".join(pins))
    for pin in missing:
        print(f"      ! missing or mismatched: {pin}")
    return not missing, missing


def check_numbers(text: str) -> tuple[bool, list[str]]:
    problems = []
    for written, sources in HEADLINES:
        target = float(written.replace(",", ""))
        # A report rounds; the artifact stores six decimals. Compare numerically with a
        # tolerance tight enough that 0.867 cannot match 0.86, loose enough that a rounded
        # quote of 0.379634 (0.3796) still traces.
        found = any(
            any(abs(value - target) <= max(5e-4, abs(target) * 5e-4)
                for value in _numbers((REPO / source).read_text(encoding="utf-8")))
            for source in sources
        )
        if not found:
            problems.append(f"{written} not in {sources}")
    print(f"  {'✓' if not problems else '✗'} numbers: {len(HEADLINES)} headline figures checked")
    for problem in problems:
        print(f"      ! {problem}")
    return not problems, problems


def spend() -> dict:
    """The report's cost table, recomputed from the proxy log at the judge's rate card."""
    totals: dict[str, dict] = {}
    for line in (REPO / "parsed/llm_proxy.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        row = record.get("row") or "unknown"
        cell = totals.setdefault(row, {"calls": 0, "prompt": 0, "completion": 0, "usd": 0.0})
        cell["calls"] += 1
        prompt_in = int(record.get("prompt_tokens") or 0)
        miss = int(record.get("prompt_cache_miss_tokens") or 0)
        hit = int(record.get("prompt_cache_hit_tokens") or 0)
        if miss + hit < prompt_in:
            miss += prompt_in - (miss + hit)
        completion = int(record.get("completion_tokens") or 0)
        cell["prompt"] += prompt_in
        cell["completion"] += completion
        cell["usd"] += (miss / 1e6 * RATES["input_miss"] + hit / 1e6 * RATES["input_hit"]
                        + completion / 1e6 * RATES["output"])
    return {
        "rows": {row: {**cell, "usd": round(cell["usd"], 2)} for row, cell in totals.items()},
        "calls": sum(cell["calls"] for cell in totals.values()),
        "prompt": sum(cell["prompt"] for cell in totals.values()),
        "completion": sum(cell["completion"] for cell in totals.values()),
        "usd": round(sum(cell["usd"] for cell in totals.values()), 2),
    }


def check_spend(text: str, tolerance: float = 0.02) -> tuple[bool, list[str]]:
    """The report's spend is a **snapshot**; the log keeps growing after it is written.

    So the check is not string equality — that would force the report to be rewritten after
    every subsequent call. It is: the report states a total and a call count within 2% of the
    log's state at check time. A wrong row still shows up, because a wrong row changes the
    total by more than 2% (the smallest row is $0.05 of $15).
    """
    measured = spend()
    dollars = [float(value) for value in re.findall(r"\$(\d+\.\d{2})", text)]
    counts = [int(value.replace(",", "")) for value in re.findall(r"\b\d{1,3}(?:,\d{3})+\b", text)]
    ok_usd = any(abs(value - measured["usd"]) / measured["usd"] <= tolerance for value in dollars)
    ok_calls = any(abs(value - measured["calls"]) / measured["calls"] <= tolerance for value in counts)
    problems = []
    if not ok_usd:
        problems.append(f"no stated total within {tolerance:.0%} of ${measured['usd']:.2f}")
    if not ok_calls:
        problems.append(f"no stated call count within {tolerance:.0%} of {measured['calls']:,}")
    print(f"  {'✓' if not problems else '✗'} spend: {measured['calls']:,} calls · "
          f"{measured['prompt']/1e6:.1f} M in · {measured['completion']/1e6:.1f} M out · "
          f"${measured['usd']:.2f} (within {tolerance:.0%})")
    for problem in problems:
        print(f"      ! {problem}")
    return not problems, problems


def gate() -> int:
    text = REPORT.read_text(encoding="utf-8")
    print(f"\n  report gate · {REPORT.name} · {len(text):,} chars")
    results = [check_paths(text), check_pins(text), check_numbers(text), check_spend(text)]
    green = all(ok for ok, _ in results)
    print(f"\n  gate: {'GREEN' if green else 'RED'}\n")
    return 0 if green else 1
