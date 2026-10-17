"""arms/cli.py — the box's command line.

    uv run python -m hyrag.arms.cli check
    uv run python -m hyrag.arms.cli answer --arm kg-light --class factoid --limit 2
    uv run python -m hyrag.arms.cli answer --arm kg-graph-bridged --ids them-101 them-102
    uv run python -m hyrag.arms.cli answer --arm kg-graph-bridged --class thematic --limit 5

`check` is the gate: one question per arm per routed class, six fields printed each. `answer` is
the runner the ablation and R4 will drive — the 5-question §7.4 sample is
`--arm kg-graph-bridged --class thematic --limit 5`, and it is the only command in this box that
costs real money (~$0.55 per thematic question).

Both commands need the serving process (`:8001`), Postgres for the LightRAG arm, and the
bridged root on disk for the other.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hyrag.arms import checks, runner


def cmd_answer(args: argparse.Namespace) -> int:
    records = runner.questions(ids=args.ids, classes=args.question_class, limit=args.limit)
    if not records:
        raise SystemExit("no questions matched — check --ids / --class / --limit")
    tally = runner.answer_many(args.arm, records, out=Path(args.out) if args.out else None,
                               resume=not args.no_resume)
    print(f"\n  answered {tally['answered']} · failed {tally['failed']} · skipped {tally['skipped']}")
    return 0 if not tally["failed"] else 1


def cmd_check(args: argparse.Namespace) -> int:
    return checks.gate()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyrag.arms.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    answer_cmd = sub.add_parser("answer", help="answer questions with one arm")
    answer_cmd.add_argument("--arm", required=True, choices=sorted(runner.ARMS))
    answer_cmd.add_argument("--class", dest="question_class", action="append",
                            choices=["factoid", "multi-hop", "thematic"])
    answer_cmd.add_argument("--ids", nargs="+", default=None)
    answer_cmd.add_argument("--limit", type=int, default=None)
    answer_cmd.add_argument("--out", default=None)
    answer_cmd.add_argument("--no-resume", action="store_true")
    answer_cmd.set_defaults(func=cmd_answer)

    sub.add_parser("check", help="the node's gate").set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
