"""qset/cli.py — drafting and freezing the question set.

    uv run python -m hyrag.qset.cli draft --pilot          # 4 + 3 + 2 candidates, to read first
    uv run python -m hyrag.qset.cli draft                  # the full pool (~100 candidates)
    uv run python -m hyrag.qset.cli review                 # -> qset/review.md, the table you filter
    #   … keep the ids you want in qset/keep.txt, one per line …
    uv run python -m hyrag.qset.cli freeze                 # -> qset/questions.jsonl

`draft` never writes the frozen set: candidates go to `qset/drafts.jsonl` (gitignored — it
carries source text), a human decides, and `freeze` writes `qset/questions.jsonl` (committed).
The gate is the commit SHA of that file, and it must predate `router` (nodes/qset.md).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from hyrag.qset.critique import (
    CRITIQUE,
    CRITIQUE_VERSION,
    SHORTLIST,
    read_critique,
    render_shortlist,
    run_critique,
    select_shortlist,
    write_critique,
)
from hyrag.qset.draft import (
    DRAFTS,
    QUESTIONS,
    load_sources,
    read_drafts,
    run,
    select_batches,
    write_drafts,
)
from hyrag.parse.config import REPO

REVIEW = REPO / "qset" / "review.md"
KEEP = REPO / "qset" / "keep.txt"


def cmd_draft(args: argparse.Namespace) -> int:
    sizes = {"factoid": args.factoid, "multi-hop": args.multi_hop, "thematic": args.thematic}
    if args.pilot:
        sizes = {"factoid": 4, "multi-hop": 3, "thematic": 2}
    sources = load_sources()
    if not sources:
        print("no chunk set: run `python -m hyrag.chunker.cli chunk --all` first", file=sys.stderr)
        return 2
    batches = select_batches(sources, **{
        "factoid": sizes["factoid"], "multi_hop": sizes["multi-hop"], "thematic": sizes["thematic"]})
    print(f"drafting {len(batches)} candidates "
          f"({sizes['factoid']} factoid · {sizes['multi-hop']} multi-hop · {sizes['thematic']} thematic) "
          f"from {len(sources)} chunks with {args.workers} workers\n")
    records = run(batches, workers=args.workers)
    path = write_drafts(records)
    failed = sum(1 for r in records if r.get("error"))
    print(f"\n  {len(records) - failed}/{len(records)} drafted → {path}")
    if failed:
        print(f"  {failed} failed — re-run `draft` to retry, or read the errors in the file")
    return 0


def cmd_critique(args: argparse.Namespace) -> int:
    records = read_drafts()
    print(f"re-reading {len(records)} drafts against the class definitions "
          f"({CRITIQUE_VERSION}, temperature 0)\n")
    critiqued = run_critique(records, workers=args.workers)
    path = write_critique(critiqued)
    rejected = [r for r in critiqued if r["critique"].get("rejected")]
    count = Counter((r["class"], r["critique"].get("why", "")[:40]) for r in rejected)
    print(f"\n  {len(critiqued) - len(rejected)}/{len(critiqued)} survived → {path}")
    for (class_name, why), number in count.most_common(8):
        print(f"    {class_name:<10} {number:>4}  {why}")
    return 0


def cmd_shortlist(args: argparse.Namespace) -> int:
    if not CRITIQUE.is_file():
        print("run `python -m hyrag.qset.cli critique` first", file=sys.stderr)
        return 2
    records = read_critique()
    chosen, notes = select_shortlist(records, target=args.target, spares=args.spares)
    SHORTLIST.parent.mkdir(parents=True, exist_ok=True)
    with SHORTLIST.open("w", encoding="utf-8") as handle:
        for record in chosen:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    render_shortlist(chosen, REVIEW)
    print(f"{SHORTLIST}  ·  {len(chosen)} candidates for review → {REVIEW}\n")
    print(f"  {'class':<10}{'survivors':>10}{'offered':>9}{'documents':>11}")
    for class_name, note in notes.items():
        print(f"  {class_name:<10}{note['survivors']:>10}{note['offered']:>9}{note['documents']:>11}")
        print("      " + " · ".join(f"{doc}×{n}" for doc, n in note["per_document"].items()))
    print(f"\n  read {REVIEW}, keep the ids you want in {KEEP}, then freeze")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    records = read_drafts()
    path = render_review(records, REVIEW)
    usable = [r for r in records if not r.get("error")]
    per_class = Counter(r["class"] for r in usable)
    per_document = Counter(e["file"] for r in usable for e in r["evidence"])
    flagged = sum(1 for r in usable if not all(r["self_check"].get(k) for k in
                                               ("answer_in_source", "needs_only_this_source")))
    print(f"{path}\n")
    print(f"  {len(usable)} usable of {len(records)} drafted")
    for name in ("factoid", "multi-hop", "thematic"):
        print(f"    {name:<10}{per_class.get(name, 0):>4}")
    print(f"  self-flagged by the model (bad target / needs another source): {flagged}")
    print("\n  documents drawn on:")
    for document, count in per_document.most_common():
        print(f"    {count:>4}  {document}")
    print(f"\n  keep ids in {KEEP} (one per line), then: python -m hyrag.qset.cli freeze")
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    if not KEEP.is_file():
        print(f"{KEEP} does not exist — write the ids you kept into it, one per line", file=sys.stderr)
        return 2
    keep = [line.strip() for line in KEEP.read_text().splitlines()
            if line.strip() and not line.startswith("#")]
    path, summary = freeze_and_report(keep, force=args.force)
    if path is None:
        return 1
    print(f"{path}")
    print(f"  kept {summary['kept']}  (" +
          " · ".join(f"{k} {v}" for k, v in summary["per_class"].items()) + ")")
    if summary["unknown_ids"]:
        print(f"  unknown ids ignored: {', '.join(summary['unknown_ids'])}", file=sys.stderr)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    records = {r["draft_id"]: r for r in read_drafts()}
    if args.draft_id not in records:
        print(f"no such draft: {args.draft_id}", file=sys.stderr)
        return 2
    record = records[args.draft_id]
    print(f"{record['draft_id']}  ·  {record['class']}  ·  self-check {record.get('self_check')}")
    print(f"\n  Q: {record.get('question', '')}\n\n  A: {record.get('gold_answer', '')}")
    if record.get("connection"):
        print(f"\n  connection: {record['connection']}")
    if record.get("themes_supported"):
        print("\n  themes: " + " | ".join(record["themes_supported"]))
    for evidence, text in zip(record["evidence"], record.get("review", {}).get("chunks", [])):
        print(f"\n{'─' * 100}\n  target: {evidence['citation']}\n{'─' * 100}\n{text}")
    return 0


def freeze_and_report(keep: list[str], *, force: bool = False):
    from hyrag.qset.draft import freeze, validate

    questions_path, summary = freeze(keep, read_drafts(), QUESTIONS)
    from hyrag.qset.draft import load_sources, read_drafts as _read  # noqa: F401 (kept explicit)

    questions = [json.loads(line) for line in questions_path.read_text().splitlines() if line.strip()]
    problems = validate(questions, {s.chunk_id for s in load_sources()})
    if problems and not force:
        questions_path.unlink(missing_ok=True)  # never leave an unvalidated set on disk
        print("the freeze failed validation — nothing written:", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  {problem}", file=sys.stderr)
        if len(problems) > 20:
            print(f"  … and {len(problems) - 20} more", file=sys.stderr)
        return None, {"problems": problems, **summary}
    return questions_path, {"problems": problems, **summary}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m hyrag.qset.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("draft", help="draft candidates -> qset/drafts.jsonl")
    d.add_argument("--pilot", action="store_true", help="4 + 3 + 2, to read before the full pool")
    d.add_argument("--factoid", type=int, default=60)
    d.add_argument("--multi-hop", type=int, default=25)
    d.add_argument("--thematic", type=int, default=15)
    d.add_argument("--workers", type=int, default=8)
    d.set_defaults(func=cmd_draft)

    c = sub.add_parser("critique", help="re-read every draft against the class definitions")
    c.add_argument("--workers", type=int, default=8)
    c.set_defaults(func=cmd_critique)

    sl = sub.add_parser("shortlist", help="survivors -> qset/shortlist.jsonl + review.md")
    sl.add_argument("--target", type=int, default=50)
    sl.add_argument("--spares", type=int, default=12)
    sl.set_defaults(func=cmd_shortlist)

    r = sub.add_parser("review", help="render qset/review.md from all drafts, with the balance")
    r.set_defaults(func=cmd_review)

    f = sub.add_parser("freeze", help="qset/keep.txt -> qset/questions.jsonl (validates first)")
    f.add_argument("--force", action="store_true", help="write even if validation fails")
    f.set_defaults(func=cmd_freeze)

    s = sub.add_parser("show", help="one candidate with its source chunks, to verify the gold answer")
    s.add_argument("draft_id")
    s.set_defaults(func=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
