"""router/config.py — the frozen split, the qset loader, and the endpoint.

**The split is the node's first decision and it is frozen here, in code, before the
classifier's prompt exists.** Ten questions per class go to eval, by the same stride rule
`hyrag.qset.select.systematic` uses (`round(i × 50/10)`, first included) — mechanical,
re-derivable from the committed qset alone, and impossible to re-cut quietly after seeing an
accuracy number. Dev is 120 questions; eval is 30.

Why split at all when the qset is the project's test set: the router *classifies*, it does not
answer. The benchmark's numbers were computed before this node opened and are not recomputed,
so nothing in `results/results.csv` depends on the split. What the split buys is the only
thing the gate needs — a number on questions the prompt was not written against.

Why the qset is read directly rather than through `hyrag.qset.select`: that module belongs to
the judge and defines *its* sample (all factoid + multi-hop, systematic thematic). The router's
split is a different rule over the same file, and a node that imports another node's sampling
turns a two-line rule into a dependency.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hyrag.kg_light import config as kg_light_config
from hyrag.parse.config import REPO

QSET = REPO / "qset" / "questions.jsonl"
CLASSES = ("factoid", "multi-hop", "thematic")
EVAL_PER_CLASS = 10  # → 30 eval, 120 dev; enough for a per-class cell, cheap to classify
SPLIT_RULE = "per class: eval = round(i * 50/10) for i in 0..9, in file order (first included)"

RESULTS = REPO / "results"
EVAL_LOG = RESULTS / "router_eval.jsonl"  # one line per eval question
DEV_LOG = RESULTS / "router_dev.jsonl"  # the same for the dev half — tuning evidence, not the gate
RECORD = RESULTS / "router_record.json"  # the summary the gate prints and the report cites
DIALOGUE_LOG = RESULTS / "router_dialogue.jsonl"  # one line per turn of the 3-turn test

# The model: the same proxy every row uses, so the router's calls land in
# `parsed/llm_proxy.jsonl` under their own tag and its cost is separable.
LLM_BASE = kg_light_config.LLM_BASE
LLM_MODEL = kg_light_config.LLM_MODEL
ROW_TAG = "router"

# The gate's floors — a choice, written down so the accuracy number is read against something.
# Chance is 0.33 on a balanced three-class set; a router that misroutes one turn in five is a
# bad router, and one that collapses a class is worse than the majority-class baseline.
OVERALL_FLOOR = 0.80
CLASS_FLOOR = 0.60

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


def questions() -> list[dict]:
    """The frozen set, in file order — 150 questions, class and gold answer on each."""
    with QSET.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def qset_sha256() -> str:
    return hashlib.sha256(QSET.read_bytes()).hexdigest()


def split() -> dict[str, list[dict]]:
    """`{"dev": [...], "eval": [...]}` — the frozen split, derived from the frozen file.

    The eval indices are computed per class, so a class that grew or shrank could not silently
    change the other two classes' eval sets. Order inside each list follows the file, which is
    what makes a printed confusion matrix re-derivable by hand.
    """
    out: dict[str, list[dict]] = {"dev": [], "eval": []}
    for label in CLASSES:
        records = [record for record in questions() if record["class"] == label]
        step = len(records) / EVAL_PER_CLASS
        eval_ids = {records[min(len(records) - 1, round(index * step))]["id"]
                    for index in range(EVAL_PER_CLASS)}
        for record in records:
            out["eval" if record["id"] in eval_ids else "dev"].append(record)
    return out


def describe_split(parts: dict[str, list[dict]] | None = None) -> str:
    parts = parts or split()
    counts = {name: {label: sum(1 for r in rows if r["class"] == label) for label in CLASSES}
              for name, rows in parts.items()}
    return (f"dev {len(parts['dev'])} {counts['dev']} · "
            f"eval {len(parts['eval'])} {counts['eval']} · {SPLIT_RULE}")


def dev_of(label: str) -> list[dict]:
    return [record for record in split()["dev"] if record["class"] == label]
