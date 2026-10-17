"""bridge/cli.py — the box's command line.

    uv run python -m hyrag.kg_graph.bridge.cli prepare --smoke --clean   # 150-node subgraph
    uv run python -m hyrag.kg_graph.bridge.cli index --smoke             # validate the config
    uv run python -m hyrag.kg_graph.bridge.cli prepare --clean           # the 4,525-node graph
    uv run python -m hyrag.kg_graph.bridge.cli index                     # run one (background job)
    uv run python -m hyrag.kg_graph.bridge.cli rerun                     # run two, and the diff
    uv run python -m hyrag.kg_graph.bridge.cli check                     # the node's gate
    uv run python -m hyrag.kg_graph.bridge.cli counts

`prepare` is deliberately separate from `index`: the tables are cheap and inspectable, the
report pass is the expensive part, and a converter bug found after a 20-minute run costs the
run. `--smoke` converts the 150 highest-degree nodes — the dense core clusters into several
communities, so the pipeline and the prompts are exercised for a few cents.

`rerun` is the gate's second half and is one command because it is one sequence: snapshot run
one's tables, clear the output (the cache stays — a cache hit is what proves the requests were
identical), rebuild the input tables, run again, diff, and write both windows into the record.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hyrag.kg_graph import checks as kg_graph_checks
from hyrag.kg_graph import tables
from hyrag.kg_graph.bridge import checks, convert, root as bridge_root, run as bridge_run


def _root(args: argparse.Namespace) -> Path:
    return bridge_root.SMOKE_ROOT if args.smoke else bridge_root.BRIDGE_ROOT


def _run_summary(record: dict | None) -> dict | None:
    if not record:
        return None
    return {
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "wall_clock_s": record.get("wall_clock_s"),
        "exit_code": record.get("exit_code"),
        "tokens": record.get("tokens"),
        "tables": record.get("tables"),
    }


def cmd_prepare(args: argparse.Namespace) -> int:
    root = _root(args)
    prepared = bridge_root.prepare(root, clean=args.clean, smoke=args.smoke)
    manifest = prepared["manifest"]
    print(f"\n  root     {root}")
    print(f"  source   graph {manifest['source_graph']['sha256'][:12]} · "
          f"{manifest['source_graph']['nodes']} nodes / {manifest['source_graph']['edges']} edges"
          + (f" → subset {manifest['source_graph']['subset_nodes']} / {manifest['source_graph']['subset_edges']}"
             if manifest.get("smoke") else ""))
    print(f"  tables   {manifest['counts']}")
    print(f"  refs     {manifest['references']['distinct']} distinct chunks · "
          f"unmapped {len(manifest['references']['unmapped'])}")
    merges = manifest["merges"]
    print(f"  merges   {merges['entities']['count']} entity names in "
          f"{len(merges['entities']['groups'])} groups (listed) · "
          f"{merges['relationships']['count']} relationship pairs")
    print(f"  settings {manifest['settings_sha256'][:12]} → root copy {manifest['settings_root_sha256'][:12]}")
    print(f"  overrides {manifest['overrides']}")
    print(f"  wrote    {root / bridge_root.MANIFEST_NAME}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    root = _root(args)
    run = bridge_run.index(root, dry_run=args.dry_run, timeout=args.timeout)
    print(f"\n  graphrag index: exit {run['exit_code']} in {run['wall_clock_s']}s · log {run['log_path']}")
    if args.dry_run:
        return run["exit_code"]
    if run["exit_code"] != 0:
        print(run["stdout_tail"])
        raise SystemExit(run["exit_code"])
    record = bridge_run.write_record(root, run)
    print(f"  tables   {record['tables']}")
    print(f"  coverage {record['coverage']}")
    print(f"  tokens   {record['tokens']}")
    print(f"  wrote    {bridge_run.record_path(root)}")
    return 0


def cmd_rerun(args: argparse.Namespace) -> int:
    root = _root(args)
    first = bridge_run.load_record(root)
    if not first:
        raise SystemExit(f"no run record in {root} — run `index` first; `rerun` compares against it")
    snapshot = bridge_root.snapshot(root)
    print(f"\n  snapshot {snapshot}")

    bridge_root.clear_output(root)
    prepared = bridge_root.prepare(root, smoke=args.smoke)
    print(f"  tables rebuilt {prepared['manifest']['counts']}")

    run = bridge_run.index(root, timeout=args.timeout)
    print(f"  graphrag index: exit {run['exit_code']} in {run['wall_clock_s']}s")
    if run["exit_code"] != 0:
        print(run["stdout_tail"])
        raise SystemExit(run["exit_code"])

    record = bridge_run.write_record(root, run, previous=_run_summary(first))
    print(f"  tokens   {record['tokens']}")

    identical, detail = checks.check_determinism(root, snapshot)
    record["diff"] = {"against": str(snapshot), "identical": identical, "tables": detail}
    bridge_run.record_path(root).write_text(
        bridge_run.dumps(record) + "\n", encoding="utf-8"
    )
    print(f"\n  diff: {'identical' if identical else 'DIFFERENT'}")
    return 0 if identical else 1


def cmd_counts(args: argparse.Namespace) -> int:
    root = _root(args)
    for name, count in tables.counts(root).items():
        print(f"    {name}: {count:,}")
    record = bridge_run.load_record(root)
    if record:
        print(f"  coverage {record['coverage']}")
        print(f"  tokens   {record['tokens']}")
        print(f"  diff     {record.get('diff')}")
    else:
        print(f"  (no run record in {root})")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = _root(args)
    against = Path(args.against) if args.against else None
    return checks.run_gate(root, against=against)


def cmd_dry_run(args: argparse.Namespace) -> int:
    """`graphrag index --dry-run`: the config is validated without a single LLM call."""
    root = _root(args)
    run = bridge_run.index(root, dry_run=True, timeout=args.timeout)
    print(run["stdout_tail"])
    return run["exit_code"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyrag.kg_graph.bridge.cli", description=__doc__)
    # `--smoke` lives on the subparsers, not the top level: a flag that has to precede the
    # command is a flag nobody types twice.
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--smoke", action="store_true",
                        help="use the smoke root and the 150-node subgraph")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_cmd = sub.add_parser("prepare", parents=[shared], help="write the input tables and the patched settings copy")
    prepare_cmd.add_argument("--clean", action="store_true", help="wipe the root first")
    prepare_cmd.set_defaults(func=cmd_prepare)

    index_cmd = sub.add_parser("index", parents=[shared], help="run the three-workflow community layer")
    index_cmd.add_argument("--dry-run", action="store_true")
    index_cmd.add_argument("--timeout", type=int, default=None)
    index_cmd.set_defaults(func=cmd_index)

    rerun_cmd = sub.add_parser("rerun", parents=[shared], help="run two: snapshot, rebuild, re-run, diff")
    rerun_cmd.add_argument("--timeout", type=int, default=None)
    rerun_cmd.set_defaults(func=cmd_rerun)

    counts_cmd = sub.add_parser("counts", parents=[shared], help="row counts, coverage and the last record")
    counts_cmd.set_defaults(func=cmd_counts)

    check_cmd = sub.add_parser("check", parents=[shared], help="the node's gate")
    check_cmd.add_argument("--against", help="the snapshot to diff against (e.g. <root>/run1)")
    check_cmd.set_defaults(func=cmd_check)

    dry_cmd = sub.add_parser("dry-run", parents=[shared], help="validate the root's config without calling a model")
    dry_cmd.add_argument("--timeout", type=int, default=None)
    dry_cmd.set_defaults(func=cmd_dry_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
