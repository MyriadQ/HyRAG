"""router/bench.py — the node's gate: held-out accuracy, then the 3-turn condense test.

    uv run python -m hyrag.router.cli check

The gate (`ROADMAP.md`, `nodes/router.md`):

> class accuracy on a held-out set, stated as a number; **and** the 3-turn condense test

Two clauses, and each prints its evidence rather than a verdict:

1. **Accuracy.** Thirty questions the prompt was not written against (ten per class, frozen by
   rule in `config.split`), a confusion matrix, and per-class accuracy. The number is paired
   with the prompt's hash, the table's version and the split's rule — an accuracy without those
   is a claim nobody can reproduce. Two floors are applied, and they are *choices* recorded in
   `config.py`: overall ≥ 0.80 and no class below 0.60, on a set where chance is 0.33.
2. **The 3-turn condense test.** `PREPLAN.md` §2.3's own dialogue: a self-contained turn, an
   elliptical follow-up that must restore the entity *and* keep the attribute, and a third turn
   that changes class. Every assertion is a string check, not a judged score — the gate must not
   depend on the thing it is measuring.

The record (`results/router_record.json`) carries both, so the report can cite one file.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from hyrag.router import classify, condense, config, strategy

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- clause 1: accuracy


def check_no_leak() -> None:
    """The prompt must not contain an eval question — the leak an accuracy number cannot show."""
    system = classify.system_prompt()
    for record in config.split()["eval"]:
        if record["question"] in system:
            raise SystemExit(
                f"leak: eval question {record['id']} appears in the classifier prompt — the "
                f"examples must come from the dev split only"
            )


def evaluate(part: str = "eval", *, verbose: bool = True) -> dict:
    """Score one half of the split. `eval` is the gate; `dev` is evidence, never the number."""
    check_no_leak()
    parts = config.split()
    rows: list[dict] = []
    for record in parts[part]:
        result = classify.classify(record["question"])
        rows.append(
            {
                "id": record["id"],
                "gold": record["class"],
                "pred": result["label"],
                "correct": result["label"] == record["class"],
                "fallback": result["fallback"],
                "attempts": result["attempts"],
                "usage": result["usage"],
                "latency_ms": result["latency_ms"],
            }
        )

    confusion = {gold: {pred: 0 for pred in config.CLASSES} for gold in config.CLASSES}
    for row in rows:
        confusion[row["gold"]][row["pred"]] += 1

    per_class = {
        label: round(sum(1 for row in rows if row["gold"] == label and row["correct"])
                     / max(1, sum(1 for row in rows if row["gold"] == label)), 4)
        for label in config.CLASSES
    }
    overall = round(sum(1 for row in rows if row["correct"]) / max(1, len(rows)), 4)
    usage = {key: sum(row["usage"][key] for row in rows)
             for key in ("prompt_tokens", "completion_tokens", "total_tokens")}

    if verbose:
        label = "held-out split" if part == "eval" else "dev split (tuning evidence, not the gate)"
        print(f"\n  accuracy on the {label} · {len(rows)} questions · "
              f"prompt {classify.prompt_sha256()[:12]} · table {strategy.VERSION}")
        print("    gold ↓ / pred →   " + "".join(f"{label:>12}" for label in config.CLASSES))
        for gold in config.CLASSES:
            cells = "".join(f"{confusion[gold][pred]:>12}" for pred in config.CLASSES)
            print(f"    {gold:<17}{cells}   acc {per_class[gold]:.2f}")
        print(f"    overall {overall:.3f} · fallbacks {sum(1 for row in rows if row['fallback'])} "
              f"· calls {sum(row['attempts'] for row in rows)} · "
              f"tokens {usage['total_tokens']:,} · "
              f"latency {round(sum(row['latency_ms'] for row in rows) / len(rows))} ms/query")
        misses = [row for row in rows if not row["correct"]]
        for row in misses[:6]:
            print(f"    ! {row['id']}: gold {row['gold']} → pred {row['pred']}")

    log = config.EVAL_LOG if part == "eval" else config.DEV_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, **_JSON) + "\n")

    floors_ok = overall >= config.OVERALL_FLOOR and all(
        value >= config.CLASS_FLOOR for value in per_class.values()
    )
    return {
        "rows": len(rows),
        "overall": overall,
        "per_class": per_class,
        "confusion": confusion,
        "fallbacks": sum(1 for row in rows if row["fallback"]),
        "calls": sum(row["attempts"] for row in rows),
        "usage": usage,
        "latency_ms_mean": round(sum(row["latency_ms"] for row in rows) / max(1, len(rows))),
        "floors": {"overall": config.OVERALL_FLOOR, "per_class": config.CLASS_FLOOR},
        "floors_ok": floors_ok,
        "prompt_sha256": classify.prompt_sha256(),
        "misses": [{"id": row["id"], "gold": row["gold"], "pred": row["pred"]}
                   for row in rows if not row["correct"]],
    }


# ---------------------------------------------------------------- clause 2: the 3-turn test


# PREPLAN §2.3's dialogue, plus a third turn that must change class: a test that only repeats
# one class proves the router can be consistent, not that it can re-route.
SCRIPT = (
    {
        "turn": "What's the maximum operating altitude of the A320?",
        "intended": "factoid",
        "must_keep": ("maximum operating altitude", "a320"),
        "why": "self-contained; entity and attribute are what later turns must restore",
    },
    {
        "turn": "What about the 737?",
        "intended": "factoid",
        "must_keep": ("737", "altitude"),
        "why": "elliptical: the entity changes, the attribute comes from turn 1",
    },
    {
        "turn": "Do limits like that show up as recurring themes across the accident reports?",
        "intended": "thematic",
        "must_keep": ("recurring",),
        "why": "a class change — the router must re-route, not repeat",
    },
)


def dialogue(*, verbose: bool = True) -> dict:
    history: list[dict] = []
    turns: list[dict] = []
    failures: list[str] = []

    if verbose:
        print("\n  3-turn condense test · PREPLAN §2.3's dialogue")

    for index, step in enumerate(SCRIPT, start=1):
        condensed = condense.condense(step["turn"], history)
        label = classify.classify(condensed["query"])

        kept = [token for token in step["must_keep"] if token in condensed["query"].lower()]
        checks = {
            "kept_entity_and_attribute": len(kept) == len(step["must_keep"]),
            "label_intended": label["label"] == step["intended"],
            "is_question": condensed["query"].rstrip().endswith("?"),
        }
        if index > 1:
            checks["class_changed"] = (
                label["label"] != turns[-1]["label"] if step["intended"] != turns[-1]["intended"]
                else True
            )
        for name, passed in checks.items():
            if not passed:
                failures.append(f"turn {index}: {name} "
                                f"(kept {kept}/{list(step['must_keep'])}, label {label['label']})")

        turns.append(
            {
                "turn": index,
                "input": step["turn"],
                "history_len": condensed["history_len"],
                "query": condensed["query"],
                "changed": condensed["changed"],
                "label": label["label"],
                "intended": step["intended"],
                "strategy": strategy.for_class(label["label"])["arms"],
                "fuse": strategy.for_class(label["label"])["fuse"],
                "fallback": label["fallback"],
                "checks": checks,
                "usage": {
                    key: condensed["usage"][key] + label["usage"][key]
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                },
                "latency_ms": condensed["latency_ms"] + label["latency_ms"],
            }
        )
        history.append({"role": "user", "content": step["turn"]})

        if verbose:
            mark = "✓" if all(checks.values()) else "✗"
            print(f"    {mark} turn {index} · {step['intended']:<9} → {label['label']:<9} · "
                  f"{'rerouted' if index > 1 and label['label'] != turns[-2]['label'] else 'same class'}")
            print(f"        in   {step['turn']}")
            print(f"        out  {condensed['query']}")
            print(f"        why  {step['why']}")

    config.DIALOGUE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with config.DIALOGUE_LOG.open("w", encoding="utf-8") as handle:
        for turn in turns:
            handle.write(json.dumps(turn, **_JSON) + "\n")

    if verbose:
        print(f"    {'all checks passed' if not failures else f'{len(failures)} check(s) failed'}")
        for failure in failures:
            print(f"    ! {failure}")
    return {"turns": turns, "failures": failures, "ok": not failures}


# ---------------------------------------------------------------- the gate


def check() -> int:
    parts = config.split()
    print("\n  router gate")
    print(f"  · qset {config.qset_sha256()[:12]} · {config.describe_split(parts)}")
    print(f"  · classifier prompt {classify.prompt_sha256()[:12]} · {strategy.describe()}")
    print(f"  · budget {strategy.BUDGET['max_latency_s']} s / "
          f"${strategy.BUDGET['max_cost_usd']} — {strategy.BUDGET['why']}")

    accuracy = evaluate()
    script = dialogue()
    green = accuracy["floors_ok"] and script["ok"]

    summary = {
        "type": "router_check",
        "when": _now(),
        "qset": {"path": str(config.QSET), "sha256": config.qset_sha256()},
        "split": {
            "rule": config.SPLIT_RULE,
            "dev": len(parts["dev"]),
            "eval": len(parts["eval"]),
            "eval_ids": [row["id"] for row in parts["eval"]],
        },
        "classifier": {"prompt_sha256": accuracy["prompt_sha256"], "model": config.LLM_MODEL},
        "table": strategy.as_record(),
        "accuracy": accuracy,
        "dialogue": script,
        "green": green,
    }
    config.RECORD.parent.mkdir(parents=True, exist_ok=True)
    config.RECORD.write_text(json.dumps(summary, **_JSON) + "\n", encoding="utf-8")
    print(f"\n  wrote {config.RECORD}")
    print(f"\n  gate: {'GREEN' if green else 'RED'}\n")
    return 0 if green else 1
