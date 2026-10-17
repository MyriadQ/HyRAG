"""arms/checks.py — the node's gate: each arm answers its class, with its numbers.

    uv run python -m hyrag.arms.cli check

The gate (`ROADMAP.md`, `nodes/arms.md`):

> each arm answers its class; every arm emits answers, citations, evidence ids, tokens and
> latency from one command

Four rows — kg-light on its two routed classes (factoid, thematic), the bridged arm on
multi-hop (`local`, its fusion class) and thematic (`global` + dynamic selection, the §7.4
class). Each row is checked for the six fields the contract names, and the check is printed
beside the value rather than summarised into a verdict: a gate that prints an answer but not
its cost is not a gate for this system.

The questions are **dev ids**, chosen once and fixed here. The router's eval half stays
untouched — it is the held-out set, and using it here would spend it on a smoke test.
"""

from __future__ import annotations

import json

from hyrag.arms import runner
from hyrag.arms.record import AnswerRecord
from hyrag.kg_light import config as kg_light_config

GATE_CASES = (
    ("kg-light", "fact-001", "its routed class"),
    ("kg-light", "them-101", "its routed class"),
    ("kg-graph-bridged", "mult-051", "fusion class, local search"),
    ("kg-graph-bridged", "them-101", "§7.4 class, global + dynamic selection"),
)

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}
GATE_LOG = kg_light_config.RESULTS / "arms_gate.jsonl"


def _checks(record: AnswerRecord) -> dict[str, tuple[bool, str]]:
    """The six fields the gate names, each with the value that makes it true or false."""
    return {
        "answer": (record.ok, f"{len(record.answer)} chars"
                   + (" · refusal" if record.refused else "")),
        "citations": (bool(record.citations),
                      f"{len(record.citations)} at {record.citation_unit}"),
        "evidence_ids": (bool(record.evidence_chunk_ids) and not record.unresolved,
                         f"{len(record.evidence_chunk_ids)} ids · "
                         f"{len(record.unresolved)} unresolved"),
        "tokens": (record.tokens_in > 0 and record.calls > 0,
                   f"{record.tokens_in:,} in / {record.tokens_out:,} out · {record.calls} calls"),
        "latency": (record.latency_ms > 0, f"{record.latency_ms / 1000:.1f} s"),
        "method": (bool(record.method), record.method),
    }


def gate() -> int:
    print("\n  arms gate")
    worst = True
    GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with GATE_LOG.open("a", encoding="utf-8") as handle:
        for arm, question_id, why in GATE_CASES:
            question = next(record for record in runner.questions(ids=[question_id]))
            print(f"\n  {arm} · {question_id} · {question['class']} · {why}")
            try:
                record = runner.answer_one(arm, question)
            except (Exception, SystemExit) as error:
                print(f"    ✗ the arm raised {type(error).__name__}: {error}"[:300])
                worst = False
                continue

            handle.write(json.dumps(record.to_dict(), **_JSON) + "\n")
            handle.flush()
            checks = _checks(record)
            for name, (passed, detail) in checks.items():
                print(f"    {'✓' if passed else '✗'} {name:<12} {detail}")
            print(f"    · answer     {record.answer[:160].replace(chr(10), ' ')}…")
            worst = worst and all(passed for passed, _detail in checks.values())

    print(f"\n  wrote {GATE_LOG}")
    print(f"\n  gate: {'GREEN' if worst else 'RED'}\n")
    return 0 if worst else 1
