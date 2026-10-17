"""answer/cli.py — the box's command line.

    uv run python -m hyrag.answer.cli check          # the gate: one traced example per class
    uv run python -m hyrag.answer.cli answer --id mult-051
    uv run python -m hyrag.answer.cli eval           # R4's row over the 30 held-out questions

`check` is cheap (≈ $0.02). `eval` is the delivered row's benchmark: 30 questions through the
full pipeline, ≈ $0.2 and ~15 minutes, dominated by the ten thematic turns at LightRAG's
measured 51 s.

Every command needs the serving process (`:8001`); `eval` also needs the reranker on disk for
the multi-hop questions.
"""

from __future__ import annotations

import argparse
import json

from hyrag.answer import checks, pipeline
from hyrag.kg_light import config as kg_light_config


def cmd_answer(args: argparse.Namespace) -> int:
    question = next(record for record in checks._questions() if record["id"] == args.id)
    record = pipeline.answer_question(question)
    print(f"\n  {question['id']} · {question['class']} · {question['question']}")
    print(f"  route {'+'.join(record['route']['arms'])}"
          + (" + fuse" if record["route"]["fuse"] else ""))
    print(f"\n  {record['answer']}\n")
    print(f"  citations {len(record['citations_used'])} · unknown {len(record['unknown_sources'])}")
    for citation in record["citations_used"]:
        print(f"    · {citation}")
    print(f"  cost ${record['cost_usd']:.4f} · {record['latency_ms']/1000:.1f}s · "
          f"{record['usage']['total_tokens']:,} tokens")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    return checks.gate()


def cmd_eval(args: argparse.Namespace) -> int:
    summary = checks.evaluate(args.coverage, limit=args.limit)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyrag.answer.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="the node's gate").set_defaults(func=cmd_check)
    eval_cmd = sub.add_parser("eval", help="R4's row over a question coverage")
    eval_cmd.add_argument("--coverage", default="router", choices=["router", "judge"],
                          help="router = the 30 held-out; judge = the judge's 120 (R4's table cell)")
    eval_cmd.add_argument("--limit", type=int, default=None)
    eval_cmd.set_defaults(func=cmd_eval)

    answer_cmd = sub.add_parser("answer", help="one question through the delivered pipeline")
    answer_cmd.add_argument("--id", required=True)
    answer_cmd.set_defaults(func=cmd_answer)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
