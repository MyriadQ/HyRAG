"""router/cli.py — the box's command line.

    uv run python -m hyrag.router.cli split        # the frozen 120/30 split, with ids
    uv run python -m hyrag.router.cli table        # the strategy table and its budget, as JSON
    uv run python -m hyrag.router.cli classify --id fact-014
    uv run python -m hyrag.router.cli classify --text "which systems does the CFM56 affect?"
    uv run python -m hyrag.router.cli dialogue     # the 3-turn condense test alone
    uv run python -m hyrag.router.cli eval         # held-out accuracy alone
    uv run python -m hyrag.router.cli check        # the node's gate (both clauses)

`check` is the gate; the two halves are exposed separately because they fail for different
reasons — an accuracy miss is a prompt problem, a dialogue failure is a condense problem — and
a single command that reports both is how a debugging session spends ten minutes on the wrong
one. Every command that calls a model needs the serving process (`:8001`); `split` and `table`
do not.
"""

from __future__ import annotations

import argparse
import json

from hyrag.router import bench, classify, config, strategy


def cmd_split(args: argparse.Namespace) -> int:
    parts = config.split()
    print(f"\n  {config.describe_split(parts)}")
    print(f"  qset {config.QSET} · sha {config.qset_sha256()[:16]}")
    for name in ("dev", "eval"):
        counts = {label: [r["id"] for r in parts[name] if r["class"] == label] for label in config.CLASSES}
        print(f"\n  {name} ({len(parts[name])})")
        for label, ids in counts.items():
            print(f"    {label:<9} {len(ids):>3}  {', '.join(ids[:6])}{' …' if len(ids) > 6 else ''}")
    return 0


def cmd_table(args: argparse.Namespace) -> int:
    print(strategy.dumps())
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    question = args.text
    if args.id:
        record = next((q for q in config.questions() if q["id"] == args.id), None)
        if record is None:
            raise SystemExit(f"no question {args.id} in {config.QSET}")
        question = record["question"]
    if not question:
        raise SystemExit("pass --id or --text")

    parts = {record["id"]: part for part, rows in config.split().items() for record in rows}
    result = classify.classify(question)
    row = strategy.for_class(result["label"])
    print(f"\n  {question}")
    print(f"  label      {result['label']}" + (f"  (fallback)" if result["fallback"] else ""))
    print(f"  strategy   arms {row['arms']} · fuse {row['fuse']}")
    print(f"  tokens     {result['usage']['total_tokens']:,} · {result['latency_ms']} ms"
          + (f" · attempts {result['attempts']}" if result["attempts"] > 1 else ""))
    if args.id:
        print(f"  split      {parts.get(args.id, '?')}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    summary = bench.evaluate(args.split)
    return 0 if args.split != "eval" or summary["floors_ok"] else 1


def cmd_dialogue(args: argparse.Namespace) -> int:
    result = bench.dialogue()
    return 0 if result["ok"] else 1


def cmd_check(args: argparse.Namespace) -> int:
    return bench.check()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyrag.router.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("split", help="the frozen dev/eval split").set_defaults(func=cmd_split)

    table_cmd = sub.add_parser("table", help="the class → strategy table, its budget and source")
    table_cmd.set_defaults(func=cmd_table)

    classify_cmd = sub.add_parser("classify", help="one question → one label")
    classify_cmd.add_argument("--id", help="a frozen qset id")
    classify_cmd.add_argument("--text", help="a question")
    classify_cmd.set_defaults(func=cmd_classify)

    eval_cmd = sub.add_parser("eval", help="accuracy and the confusion matrix")
    eval_cmd.add_argument("--split", default="eval", choices=["eval", "dev"],
                          help="eval is the gate; dev is tuning evidence")
    eval_cmd.set_defaults(func=cmd_eval)
    sub.add_parser("dialogue", help="the 3-turn condense test").set_defaults(func=cmd_dialogue)
    sub.add_parser("check", help="the node's gate").set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
