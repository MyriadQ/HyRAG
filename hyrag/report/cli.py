"""report/cli.py — the report's command line.

    uv run python -m hyrag.report.cli check      # paths, pins, headline numbers, spend
    uv run python -m hyrag.report.cli ablation   # §7.4's native-vs-bridged sample (≈ $1.9)

`check` is the gate and costs nothing. `ablation` is the measurement the report's §7.4 section
quotes — five bridged thematic answers against R3's recorded native ones, ~25 minutes and
≈ $1.9, run once (its output is `results/ablation.jsonl`).
"""

from __future__ import annotations

import argparse

from hyrag.report import ablation, checks


def cmd_check(args: argparse.Namespace) -> int:
    return checks.gate()


def cmd_ablation(args: argparse.Namespace) -> int:
    ablation.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyrag.report.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="the report's gate").set_defaults(func=cmd_check)
    sub.add_parser("ablation", help="§7.4's native-vs-bridged sample").set_defaults(func=cmd_ablation)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
