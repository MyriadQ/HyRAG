"""kg_graph/cli.py — the R3 box's command line.

    uv run python -m hyrag.kg_graph.cli inputs --smoke        # 8 chunks into a scratch root
    uv run python -m hyrag.kg_graph.cli index --smoke         # validate the config cheaply
    uv run python -m hyrag.kg_graph.cli inputs                # the 369-chunk root
    uv run python -m hyrag.kg_graph.cli index                 # the native run (background job)
    uv run python -m hyrag.kg_graph.cli query --class thematic
    uv run python -m hyrag.kg_graph.cli counts
    uv run python -m hyrag.kg_graph.cli check                 # the node's gate

`--smoke` swaps the root to `parsed/kg_graph_smoke` and the input to the first eight chunks:
S06's lesson, applied to the second extraction pass this project pays for — a config error
found on eight chunks costs seconds, on 369 it costs the run.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from hyrag.kg_graph import checks, config, inputs, resolve, run, tables

ANSWER_PREVIEW = 700


def _root(args: argparse.Namespace) -> Path:
    return config.SMOKE_ROOT if args.smoke else config.GRAPH_ROOT


def _limit(args: argparse.Namespace) -> int | None:
    return config.SMOKE_CHUNKS if args.smoke else getattr(args, "limit", None)


def cmd_inputs(args: argparse.Namespace) -> int:
    root = _root(args)
    manifest = inputs.prepare_root(root, _limit(args), clean=args.clean)
    print(f"\n  root     {root}")
    print(f"  inputs   {manifest['chunks']} chunks · settings {manifest['settings_sha256'][:12]} · prompts {manifest['prompts_sha256'][:12]}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    root = _root(args)
    log_path = root / "logs" / "index.stdout.log"
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    started = time.perf_counter()
    exit_code = run.index(root, dry_run=args.dry_run, timeout=args.timeout, log_path=log_path)
    wall_clock = round(time.perf_counter() - started, 1)
    print(f"\n  graphrag index: exit {exit_code} in {wall_clock}s · log {log_path}")
    if args.dry_run:
        return exit_code
    record = checks.write_index_record(root, wall_clock_s=wall_clock, exit_code=exit_code,
                                       started_at=started_at)
    print(f"  tables   {record['tables']}")
    print(f"  tokens   {record['tokens']}")
    print(f"  wrote    {checks.index_record_path(root)}")
    return exit_code


def cmd_query(args: argparse.Namespace) -> int:
    root = _root(args)
    question = args.text or checks.first_thematic_question()
    stdout = run.query(root, question, method=args.method, log_path=root / "logs" / "query.stdout.log")
    answer = checks._answer_from_stdout(stdout)
    print(f"\n  {question}\n")
    print(f"  {answer[:ANSWER_PREVIEW]}…")
    return 0


def cmd_citations(args: argparse.Namespace) -> int:
    """Resolve the last logged R3 answer's `[Data: …]` markers — no LLM call, always re-runnable."""
    question, answer = resolve.latest_answer()
    result = resolve.resolve(answer, _root(args))
    print(f"\n  {question[:100]}\n")
    print(f"  markers      {result['markers']}")
    print(f"  ids          {result['ids']} ({result['distinct_ids']} distinct) · resolved {result['resolved_ids']}")
    print(f"  text units   {result['text_units']} · our chunks {len(result['evidence_chunk_ids'])}")
    print(f"  citations    {len(result['citations'])} · unresolved {len(result['unresolved'])}"
          + (f" · +more {result['truncated_markers']}" if result["truncated_markers"] else ""))
    for citation in result["citations"][:8]:
        print(f"      {citation}")
    for problem in result["unresolved"][:5]:
        print(f"      ! {problem}")
    return 0 if not result["unresolved"] else 1


def cmd_counts(args: argparse.Namespace) -> int:
    root = _root(args)
    for name, count in tables.counts(root).items():
        print(f"    {name}: {count:,}")
    record = checks.load_index_record(root)
    if record:
        print(f"  last index: {record['finished_at']} · {record['wall_clock_s']}s · tokens {record['tokens']}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    return checks.run_gate(_root(args), skip_query=args.skip_query)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyrag.kg_graph.cli", description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="use the scratch root and the first 8 chunks")
    sub = parser.add_subparsers(dest="command", required=True)

    inputs_cmd = sub.add_parser("inputs", help="write the chunk files, settings and prompts into the root")
    inputs_cmd.add_argument("--limit", type=int, default=None)
    inputs_cmd.add_argument("--clean", action="store_true", help="wipe the root first")
    inputs_cmd.set_defaults(func=cmd_inputs)

    index_cmd = sub.add_parser("index", help="run graphrag index over the root")
    index_cmd.add_argument("--dry-run", action="store_true")
    index_cmd.add_argument("--timeout", type=int, default=None)
    index_cmd.set_defaults(func=cmd_index)

    query_cmd = sub.add_parser("query", help="one query through graphrag query")
    query_cmd.add_argument("--method", default="global", choices=["global", "local", "drift", "basic"])
    query_cmd.add_argument("--text", default=None, help="default: the first thematic question")
    query_cmd.set_defaults(func=cmd_query)

    citations_cmd = sub.add_parser("citations", help="resolve the last answer's [Data: …] markers")
    citations_cmd.set_defaults(func=cmd_citations)

    counts_cmd = sub.add_parser("counts", help="row counts per output table")
    counts_cmd.set_defaults(func=cmd_counts)

    check_cmd = sub.add_parser("check", help="the node's gate")
    check_cmd.add_argument("--skip-query", action="store_true")
    check_cmd.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
