"""judge/cli.py — the harness's command line.

    uv run python -m hyrag.judge.cli smoke                 # one question per class, three passes
    uv run python -m hyrag.judge.cli run                   # the judged table's full coverage
    uv run python -m hyrag.judge.cli table                 # results.csv + pins + markdown
    uv run python -m hyrag.judge.cli check                 # re-derive the table, compare, report

`smoke` writes to the same logs as `run`, and `run` resumes: a smoke answer is a real answer,
so the full pass skips it instead of paying for it twice. That is deliberate — the alternative
is a separate fixture path that a real run never exercises, which is how a smoke test stops
predicting anything about the run it is supposed to protect.

The serving process must be up (`hyrag.serving.cli serve`): the judge reaches DeepSeek through
the same local proxy as the rows, which is what makes its tokens countable beside theirs.
"""

from __future__ import annotations

import argparse
import asyncio

from hyrag.judge import config, records, runner, table
from hyrag.judge import prompts
from hyrag.judge.table import build_cells, build_pairwise, build_blinding


def _items(args: argparse.Namespace):
    only_rows = tuple(args.rows) if args.rows else config.ROW_ORDER
    rows = records.load_all(only_rows)
    present = {row: len(rows.get(row, {})) for row in only_rows}
    print(f"  rows: " + " · ".join(f"{config.ROWS[row]['label']} {count} answers"
                                   for row, count in present.items()))
    return runner.build_items(rows, question_ids=args.ids, per_class=args.per_class,
                              only_rows=only_rows), rows


def cmd_smoke(args: argparse.Namespace) -> int:
    args.per_class = args.per_class or 1
    args.replicates = args.replicates or 1
    return cmd_run(args)


def cmd_run(args: argparse.Namespace) -> int:
    items, _rows = _items(args)
    print(f"  items: {len(items)} (question × row) · replicates {args.replicates or config.REPLICATES}"
          f" · concurrency {args.concurrency or config.CONCURRENCY}")
    if not items:
        print("  nothing to do — answer files are missing or empty")
        return 1
    if args.dry_run:
        by_class: dict[str, int] = {}
        for item in items:
            by_class[item.question_class] = by_class.get(item.question_class, 0) + 1
        calls = sum(len(prompts.criteria.for_class(item.question_class)) for item in items)
        print(f"  dry run · {by_class} · pointwise calls ≈ "
              f"{calls * (args.replicates or config.REPLICATES)}")
        return 0

    passes = args.passes
    if "pointwise" in passes:
        tally = asyncio.run(runner.run_pointwise(
            items, replicates=args.replicates, concurrency=args.concurrency,
            resume=not args.no_resume))
        print(f"  pointwise · {tally}")
    if "pairwise" in passes and args.pairs:
        pairs = [tuple(part.split("-")) for part in args.pairs.split(",") if part.strip()]
        tally = asyncio.run(runner.run_head_to_head(
            items, pairs=pairs, replicates=args.replicates, concurrency=args.concurrency,
            resume=not args.no_resume))
        print(f"  head-to-head · {tally}")
    elif "pairwise" in passes:
        tally = asyncio.run(runner.run_pairwise(
            items, replicates=args.replicates, concurrency=args.concurrency,
            resume=not args.no_resume, variant=args.pairwise_criteria))
        print(f"  pairwise · {tally}")
    if "blinding" in passes:
        tally = asyncio.run(runner.run_blinding(
            items, concurrency=args.concurrency, resume=not args.no_resume))
        print(f"  blinding · {tally}")
    return 0


def cmd_table(args: argparse.Namespace) -> int:
    cells = build_cells()
    pairwise = build_pairwise()
    blinding = build_blinding()
    if not cells:
        print("  no cells — run `judge run` first")
        return 1
    path = table.write_csv(cells)
    pins = table.write_pins(cells)
    strategy = table.write_strategy(cells, pairwise)
    ladder = table.write_head_to_head(table.build_head_to_head())
    print(f"  wrote {path}")
    print(f"  wrote {config.RESULTS / 'class_strategy.json'} · "
          + " · ".join(f"{label}: {entry['pairwise_winner']}" for label, entry in strategy.items()))
    print(f"  wrote {ladder}")
    print(f"  wrote {config.PINS_PATH} · rubric {pins['rubric_version']} · "
          f"qset {pins['qset']['sha256'][:12]} @ {(pins['qset']['commit'] or '?')[:12]}")
    print(f"  wrote {config.PROMPTS_COPY}")
    markdown = table.markdown(cells, pairwise, blinding)
    print()
    print(markdown)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Re-derive everything from the artifacts and say what is missing or inconsistent."""
    cells = build_cells()
    pairwise = build_pairwise()
    blinding = build_blinding()
    calls = table._judge_call_totals()
    expected = {label: len([question for question in config.evaluation_set()
                            if question["class"] == label]) for label in ("factoid", "multi-hop", "thematic")}
    problems = []
    for cell in cells:
        if cell["n"] < expected[cell["class"]]:
            problems.append(f"{cell['label']}/{cell['class']}: {cell['n']}/{expected[cell['class']]} answered")
        if cell["citation_rate"] is not None and cell["citation_rate"] < 0.999:
            problems.append(f"{cell['label']}/{cell['class']}: citation rate {cell['citation_rate']}")
    for cell in cells:
        for key in table.criteria_for(cell["class"]):
            if cell.get(f"{key}_n", 0) < cell["n"]:
                problems.append(f"{cell['label']}/{cell['class']}/{key}: "
                                f"scored {cell.get(f'{key}_n', 0)}/{cell['n']} questions")
    print(f"  cells {len(cells)} · pairwise {sum(cell['comparisons'] for cell in pairwise) // 3 if pairwise else 0} "
          f"comparisons · judge calls {calls['calls']} · upper-bound cost ${calls['cost_usd_upper_bound']}")
    if problems:
        print("  problems:")
        for problem in problems[:40]:
            print(f"    ! {problem}")
        return 1
    print("  no problems found")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyrag.judge.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def shared(sub_parser: argparse.ArgumentParser) -> None:
        sub_parser.add_argument("--rows", action="append", choices=list(config.ROW_ORDER),
                                help="restrict to some rows (repeatable)")
        sub_parser.add_argument("--ids", action="append", help="specific question ids (repeatable)")
        sub_parser.add_argument("--per-class", type=int, default=None,
                                help="take the first N of each class (smoke runs)")
        sub_parser.add_argument("--replicates", type=int, default=None)
        sub_parser.add_argument("--concurrency", type=int, default=None)
        sub_parser.add_argument("--pairs", default=None,
                                help="two-way ladder, e.g. R1-R2,R1-R3,R2-R3 (default: three-way arena)")
        sub_parser.add_argument("--pairwise-criteria", default="paper", choices=["paper", "control"],
                                help="paper = the papers' three criteria (the benchmark); control = those plus a padding penalty (sensitivity)")
        sub_parser.add_argument("--passes", default="pointwise,pairwise,blinding",
                                help="comma-separated: pointwise,pairwise,blinding")
        sub_parser.add_argument("--no-resume", action="store_true")
        sub_parser.add_argument("--dry-run", action="store_true")

    smoke = sub.add_parser("smoke", help="one question per class, one replicate, all three passes")
    shared(smoke)
    smoke.set_defaults(func=cmd_smoke)

    run = sub.add_parser("run", help="the full judged coverage")
    shared(run)
    run.set_defaults(func=cmd_run)

    table_cmd = sub.add_parser("table", help="results.csv + pins + prompt copy + markdown")
    table_cmd.set_defaults(func=cmd_table)

    check = sub.add_parser("check", help="re-derive the artifacts and report gaps")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(args, "passes") and isinstance(args.passes, str):
        args.passes = [name.strip() for name in args.passes.split(",") if name.strip()]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
