"""router/strategy.py — `class → {arms, fuse}`, as data, under a stated budget.

D5 says the mapping is a **table in our code**, read off Phase 1's results rather than decided
inside the model. This module is that table, and it carries three things a bare dict would
not:

* **the budget it was chosen under** — the benchmark's pairwise winner wins every class and
  costs 357.8 s and $0.380 per thematic query (`results/class_strategy.json`). A route is a
  choice under a latency/cost envelope; without one, "kg-graph everywhere" looks like the
  measurement and the shipped system becomes a 6-minute turn. The envelope is **≤ 60 s and
  ≤ $0.02 per turn** (decided 2026-10-17, `nodes/router.md`).
* **why each row is what it is**, in the row, next to the number it was chosen from.
* **a version**, so a record can say which table produced a turn and an ablation can swap it.

The arms are the delivered system's, not the benchmark's rows: `kg-light` and
`kg-graph-bridged`. There is no naive arm (README → Decisions, 2026-10-16); the naive method is
LightRAG's own `naive` mode, one switch inside an existing arm.

**Why factoid does not route to kg-graph even though it won 76% pairwise.** That number belongs
to the **native** row R3. The delivered system runs the **bridged** index, whose per-class
quality has not been measured — §7.4's ablation is thematic and sampled at five questions. A
table that routed factoid to the bridged arm would be routing on a proxy for a row that is not
deployed. Until the ablation measures it, the shipped arm is the one with measured per-class
numbers.
"""

from __future__ import annotations

import hashlib
import json

from hyrag.router import config

VERSION = "table-2026-10-17"

BUDGET = {
    "max_latency_s": 60,
    "max_cost_usd": 0.02,
    "decided": "2026-10-17",
    "why": (
        "The benchmark's pairwise winner wins every class and its thematic cell is "
        "357.8 s / $0.380 native and ~500 s / ~$0.55 bridged. A dialogue system cannot ship "
        "a six-to-eight-minute turn, so the envelope excludes it by construction and the "
        "exclusion is reported as a finding, not hidden as a setting."
    ),
}

TABLE: dict[str, dict] = {
    "factoid": {
        "arms": ["kg-light"],
        "fuse": False,
        "why": (
            "kg-light is the pointwise best (0.803) with a 1.0 evidence-target hit rate at "
            "4.2 s / $0.0047. kg-graph's 76% pairwise win belongs to the native row; the "
            "bridged arm is unmeasured per class, and routing to it would be routing on a proxy."
        ),
    },
    "multi-hop": {
        "arms": ["kg-light", "kg-graph-bridged"],
        "fuse": True,
        "why": (
            "The roadmap's design: both arms, merged by RRF. kg-light is pointwise best (0.870) "
            "and the two retrieve different units (entity/relation neighbourhoods vs community "
            "reports), which is the case fusion exists for. D5: fusion is by rank, never by "
            "comparing scores across systems."
        ),
    },
    "thematic": {
        "arms": ["kg-light"],
        "fuse": False,
        "why": (
            "Budget, not quality: kg-graph wins 90% pairwise and costs 357.8 s / $0.380 per "
            "query native (~500 s / ~$0.55 bridged) against kg-light's 51.3 s / $0.0094. The "
            "delivered system does not ship the benchmark's thematic winner; the report states "
            "the price of that choice. The bridged arm still appears in §7.4's ablation, "
            "sampled at five questions."
        ),
    },
}


def for_class(label: str) -> dict:
    if label not in TABLE:
        raise KeyError(f"no strategy for class {label!r} — the table has {sorted(TABLE)}")
    return TABLE[label]


def source() -> dict:
    """The measured file the table was read off, with its hash — provenance, not a citation."""
    path = config.RESULTS / "class_strategy.json"
    if not path.is_file():
        return {"path": str(path), "sha256": None, "present": False}
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "present": True,
    }


def as_record() -> dict:
    return {
        "version": VERSION,
        "budget": BUDGET,
        "table": TABLE,
        "chosen_from": source(),
    }


def describe() -> str:
    rows = " · ".join(
        f"{label} → {'+'.join(row['arms'])}" + (" (fuse)" if row["fuse"] else "")
        for label, row in TABLE.items()
    )
    return f"{VERSION} · budget ≤{BUDGET['max_latency_s']} s / ≤${BUDGET['max_cost_usd']} · {rows}"


def dumps() -> str:
    return json.dumps(as_record(), ensure_ascii=False, sort_keys=True, indent=1)
