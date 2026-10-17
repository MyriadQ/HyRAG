"""fuse/cli.py — the box's command line.

    uv run python -m hyrag.fuse.cli check
    uv run python -m hyrag.fuse.cli fuse --ids mult-051
    uv run python -m hyrag.fuse.cli fuse --ids mult-051 --k 30 --shortlist 10

`check` is the gate: three dev multi-hop questions, both arms live, fused, reranked, with the
four clauses printed. `fuse` is the one-question path for reading a result by hand — it prints
the fused top-10 and writes the record.

Both commands need the serving process (`:8001`) for the arms; the reranker runs in-process on
CPU and needs nothing running.
"""

from __future__ import annotations

import argparse
import json

from hyrag.arms import runner
from hyrag.fuse import checks, combine
from hyrag.kg_light import config as kg_light_config

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}
ONE_LOG = kg_light_config.RESULTS / "fuse_questions.jsonl"


def cmd_fuse(args: argparse.Namespace) -> int:
    encoder = combine.rerank_module.CrossEncoder()
    for question_id in args.ids:
        question = next(record for record in runner.questions(ids=[question_id]))
        arms = {
            "kg-light": runner.answer_one("kg-light", question),
            "kg-graph-bridged": runner.answer_one("kg-graph-bridged", question),
        }
        record = combine.fuse(question, arms, reranker=encoder,
                              k=args.k, shortlist=args.shortlist)
        print(f"\n  {question_id} · {question['question'][:110]}")
        print("    rank  rrf        kg-light  bridged   rerank   citation")
        for position, entry in enumerate(record["reranked"][: args.top], start=1):
            print(f"    {position:>4}  {entry['rrf']:.6f}  "
                  f"{entry['ranks'].get('kg-light', '—')!s:>8}  "
                  f"{entry['ranks'].get('kg-graph-bridged', '—')!s:>7}  "
                  f"{entry['rerank']:>8.2f}  {entry['citation'][:64]}")
        print(f"    · arms {record['diagnostics']['arms_in_top_k']} · "
              f"{record['diagnostics']['positions_moved']} positions moved · "
              f"{record['diagnostics']['truncated']} truncated")
        ONE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ONE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, **_JSON) + "\n")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    return checks.gate()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyrag.fuse.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fuse_cmd = sub.add_parser("fuse", help="fuse one or more multi-hop questions")
    fuse_cmd.add_argument("--ids", nargs="+", required=True)
    fuse_cmd.add_argument("--k", type=int, default=combine.DEFAULT_K)
    fuse_cmd.add_argument("--shortlist", type=int, default=combine.DEFAULT_SHORTLIST)
    fuse_cmd.add_argument("--top", type=int, default=10)
    fuse_cmd.set_defaults(func=cmd_fuse)

    sub.add_parser("check", help="the node's gate").set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
