"""parse/cli.py — the parse stage's command line.

    uv run python -m hyrag.parse.cli parse --all              # the whole dev slice
    uv run python -m hyrag.parse.cli parse <pdf> [<pdf> ...]  # explicit files
    uv run python -m hyrag.parse.cli check --all              # invariants + determinism
    uv run python -m hyrag.parse.cli check --all --docling    # + re-run Docling itself
    uv run python -m hyrag.parse.cli show <stem>              # inspect one parse

`parse` is sequential on purpose: Docling's models are already multi-threaded
and each process would load its own copy, so N processes cost ~N× the RAM for
less than N× the speed. Long runs are expected — ~2–8 s per 10 pages with table
structure on, and the 193-page reports are the slow ones.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hyrag.parse.checks import check_document
from hyrag.parse.config import doc_dir, slice_docs
from hyrag.parse.pipeline import parse_document
from hyrag.parse.sidecar import read_blocks, read_meta


# ---------------------------------------------------------------- commands


def cmd_parse(args: argparse.Namespace) -> int:
    targets: list[str] = list(args.targets)
    if args.all:
        targets += [d.relpath for d in slice_docs()]
    if not targets:
        print("nothing to do: pass PDF paths or --all", file=sys.stderr)
        return 2

    failures = 0
    for target in targets:
        try:
            parse_document(target, layout=args.layout, force_convert=args.force)
        except Exception as exc:  # one bad file must not stop a slice run
            failures += 1
            print(f"  FAILED {target}: {type(exc).__name__}: {exc}", file=sys.stderr)
    if failures:
        print(f"{failures}/{len(targets)} documents failed", file=sys.stderr)
    return 1 if failures else 0


def cmd_check(args: argparse.Namespace) -> int:
    stems: list[str] = list(args.stems) if args.stems else []
    if args.all:
        stems = [d.stem for d in slice_docs()]
    if not stems:
        print("nothing to check: pass stems or --all", file=sys.stderr)
        return 2

    failed = 0
    for stem in stems:
        print(f"{stem}")
        checks = check_document(stem, reparse=not args.no_reparse, docling=args.docling)
        for check in checks:
            print(check.line())
            if check.ok is False:
                failed += 1
        print()
    if failed:
        print(f"{failed} check(s) FAILED", file=sys.stderr)
        return 1
    print("all checks passed")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    target = doc_dir(args.stem)
    meta = read_meta(target / "meta.json")
    _sidecar_meta, blocks = read_blocks(target / "blocks.jsonl")
    print(f"{args.stem}  ({meta['title']})")
    print(f"  layout={meta['layout']}  pages={meta['n_pages']}  blocks={meta['counts']['blocks']}  "
          f"headings={meta['counts']['headings']}")
    print(f"  page labels: {meta['page_labels_note']}")

    if args.labels:
        print("\n  page  printed  source")
        for page, entry in sorted(meta["page_labels"].items(), key=lambda kv: int(kv[0])):
            print(f"  {page:>4}  {entry['printed']:>7}  {entry['source']}")
        return 0

    if args.headings:
        print("\n  idx  page  lvl  heading")
        for item in meta["heading_outline"]:
            print(f"  {item['index']:>3}  {item['page']:>4}  {item['level']:>3}  {item['heading']}")
        return 0

    limit = args.limit
    selected = [b for b in blocks if args.page is None or b["positions"][0]["physical"] == args.page]
    for b in selected[:limit]:
        anchor = b["positions"][0]["anchor"]
        heading = b["heading"] or ""
        text = b["content"].replace("\n", " ")[:74]
        print(f"  {b['blockid'][:8]}  p{anchor:>5}  {b['kind']:<9} l{b['level']}  {heading[:26]:<26}  {text}")
    if len(selected) > limit:
        print(f"  … {len(selected) - limit} more (raise --limit)")
    return 0


# ---------------------------------------------------------------- entry point


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m hyrag.parse.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="PDF -> parsed/<stem>/{blocks.jsonl,meta.json}")
    p.add_argument("targets", nargs="*", help="PDF paths (repo-relative or absolute)")
    p.add_argument("--all", action="store_true", help="the whole dev slice")
    p.add_argument("--layout", choices=("mmel", "report", "article", "handbook"), default=None)
    p.add_argument("--force", action="store_true", help="re-run Docling, ignore the cache")
    p.set_defaults(func=cmd_parse)

    c = sub.add_parser("check", help="run the parse invariants")
    c.add_argument("stems", nargs="*", help="document stems under parsed/")
    c.add_argument("--all", action="store_true")
    c.add_argument("--no-reparse", action="store_true", help="skip the determinism re-parse")
    c.add_argument("--docling", action="store_true", help="also re-run Docling and compare")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("show", help="inspect one parsed document")
    s.add_argument("stem")
    s.add_argument("--page", type=int, default=None, help="only blocks on this physical page")
    s.add_argument("--headings", action="store_true", help="print the heading outline")
    s.add_argument("--labels", action="store_true", help="print the page-label map")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(func=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
