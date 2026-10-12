"""chunker/cli.py — the chunking stage's command line.

    uv run python -m hyrag.chunker.cli chunk --all            # every parsed slice document
    uv run python -m hyrag.chunker.cli chunk <stem> [<stem> ...]
    uv run python -m hyrag.chunker.cli check --all            # invariants + determinism
    uv run python -m hyrag.chunker.cli cite <stem> --index 3  # one stored chunk -> its citation

`chunk` writes both artifacts: `parsed/<stem>/chunks.jsonl` per document, then the
corpus-wide, deduplicated `parsed/_ingest.jsonl` (see `hyrag.chunker.store`).

`cite` is the gate's demo, and it reads the **stored** chunk — what a retrieval step
would hand back — rather than re-chunking. `--drop-provenance` deliberately strips every
block's `positions[]` first, simulating a store that kept the text and lost the anchors:
the command must then fail loudly. A missing page is recoverable; a plausible wrong page
is not.

The three packing parameters default to what the slice sweep chose, recorded in
`scratch/chunker.ipynb` §7 and (once written down) `nodes/parse.md` → Discoveries. They
are arguments here so the choice can be re-measured without editing code.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from hyrag.chunker.checks import (
    MIN_VERIFIABLE_WORDS,
    SPOT_CHECK_THRESHOLD,
    check_document,
    check_document_with_pdf,
    check_ingest,
    pdf_page_coverage,
)
from hyrag.chunker.citation import ProvenanceError, format_citation, page_numbers
from hyrag.chunker.store import (
    blocks_by_id,
    chunks_path,
    ingest_path,
    load_document,
    read_jsonl,
)
from hyrag.parse.config import slice_docs

# Decided 2026-10-11, recorded in nodes/parse.md -> Discoveries: the cap matches the LightRAG
# paper's chunk size (1200), the section policy decides the boundaries (`significant`), and
# the overlap is the swept value. The notebook's CHOSEN (scratch/chunker.ipynb, §7) and this
# constant are meant to be identical — change both together.
DEFAULT_KNOBS = {"max_tokens": 1200, "overlap": 64, "section_policy": "significant"}


# ---------------------------------------------------------------- chunk


def cmd_chunk(args: argparse.Namespace) -> int:
    from hyrag.chunker.store import (
        build_ingest_set,
        chunk_corpus,
        write_document,
        write_jsonl,
    )

    stems: list[str] = list(args.stems)
    if args.all:
        stems = [spec.stem for spec in slice_docs()]
    if not stems:
        print("nothing to chunk: pass stems or --all", file=sys.stderr)
        return 2

    knobs = {
        "max_tokens": args.max_tokens,
        "overlap": args.overlap,
        "section_policy": args.policy,
    }
    try:
        per_document, metas, by_id = chunk_corpus(stems, knobs)
    except FileNotFoundError as exc:
        print(f"{exc} — run `python -m hyrag.parse.cli parse --all` first", file=sys.stderr)
        return 2

    print(f"chunking {len(stems)} documents at {knobs}\n")
    print(f"  {'document':<44}{'blocks':>7}{'chunks':>7}{'tokens':>8}{'mean':>6}{'p-span':>8}")
    for stem, blocks, chunks in per_document:
        write_document(stem, metas[stem], knobs, blocks, chunks)
        sizes = [chunk["tokens"] for chunk in chunks]
        spanning = sum(
            len({by_id[ref]["positions"][0]["physical"] for ref in chunk["refs"]}) > 1
            for chunk in chunks
        )
        print(f"  {stem:<44}{len(blocks):>7}{len(chunks):>7}{sum(sizes):>8}"
              f"{round(statistics.mean(sizes)):>6}{round(100 * spanning / len(chunks)):>7}%")

    header, records = build_ingest_set(per_document, by_id, metas, knobs)
    target = ingest_path(stems[0])
    write_jsonl(target, header, records)
    print(f"\n  ingest set → {target}")
    print(f"    {header['chunks']} chunks → {header['unique_chunks']} unique "
          f"({header['collapsed']} collapsed, {header['occurrences']} locations recorded)")
    if header["collapsed"]:
        print("    duplicate text exists at these settings: the dedupe is load-bearing, "
              "and every collapsed chunk kept its locations")
    else:
        print("    no byte-identical pair at these settings: the dedupe is insurance here")
    return 0


# ---------------------------------------------------------------- check


def cmd_check(args: argparse.Namespace) -> int:
    stems: list[str] = list(args.stems)
    if args.all:
        stems = [spec.stem for spec in slice_docs()]
    if not stems:
        print("nothing to check: pass stems or --all", file=sys.stderr)
        return 2

    failed = 0
    for stem in stems:
        print(f"{stem}")
        for check in check_document_with_pdf(stem, reparse=not args.no_reparse,
                                             sample=args.spot_check):
            print(check.line())
            failed += check.ok is False
        print()

    if args.ingest:
        print("_ingest.jsonl  (the corpus set)")
        for check in check_ingest(reparse=not args.no_reparse):
            print(check.line())
            failed += check.ok is False

    if failed:
        print(f"{failed} check(s) FAILED", file=sys.stderr)
        return 1
    print("all checks passed")
    return 0


# ---------------------------------------------------------------- cite


def cmd_cite(args: argparse.Namespace) -> int:
    stem = args.stem
    try:
        _sidecar_meta, blocks, meta, chunks = load_document(stem)
    except FileNotFoundError as exc:
        print(f"{exc} — run `python -m hyrag.chunker.cli chunk {stem}` first", file=sys.stderr)
        return 2

    index = args.index
    if not 0 <= index < len(chunks):
        print(f"{stem}: no chunk #{index} (the document has {len(chunks)})", file=sys.stderr)
        return 2
    chunk = chunks[index]

    by_id = blocks_by_id(blocks)
    if args.drop_provenance:
        # Simulate the store losing the anchors: the text survives, the pages do not.
        print("--drop-provenance: every block's positions[] removed, the text left in place\n")
        for block in blocks:
            block.pop("positions", None)
        by_id = blocks_by_id(blocks)

    try:
        citation = format_citation(chunk, by_id, meta)
        pages = page_numbers(chunk["refs"], by_id)
    except ProvenanceError as exc:
        print(f"PROVENANCE LOST: {exc}", file=sys.stderr)
        print("refusing to print a page — a plausible number would be worse than none",
              file=sys.stderr)
        return 2

    print(f"  {citation}")
    print(f"  chunk_id {chunk['chunk_id']}   {chunk['tokens']} tokens   "
          f"{len(chunk['refs'])} blocks   physical pages {sorted(set(pages))}")
    print(f"  labels: {[meta['page_labels'].get(str(p), {}).get('printed') for p in sorted(set(pages))]}")
    print(f"  text:   {chunk['content'][:args.chars]!r}")

    if args.verify:
        source = Path(meta["source"])
        if not source.is_file():
            print(f"  verify: source PDF not on disk ({source}) — skipped")
            return 0
        coverage, words = pdf_page_coverage(source, sorted(set(pages)), chunk["content"])
        if words < MIN_VERIFIABLE_WORDS:
            print(f"  verify: not verifiable — this chunk holds {words} words of 5+ characters "
                  f"(a picture or table marker has no prose to find in the PDF)")
        else:
            verdict = "PASS" if coverage >= SPOT_CHECK_THRESHOLD else "FAIL"
            print(f"  verify: {coverage:.2f} of the chunk's {words} long words sit on those sheets, "
                  f"per the PDF itself → {verdict} (threshold {SPOT_CHECK_THRESHOLD})")
            if coverage < SPOT_CHECK_THRESHOLD:
                return 1
    return 0


# ---------------------------------------------------------------- entry point


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m hyrag.chunker.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("chunk", help="blocks.jsonl -> chunks.jsonl + the corpus ingest set")
    c.add_argument("stems", nargs="*", help="document stems under parsed/")
    c.add_argument("--all", action="store_true", help="every parsed slice document")
    c.add_argument("--max-tokens", type=int, default=DEFAULT_KNOBS["max_tokens"])
    c.add_argument("--overlap", type=int, default=DEFAULT_KNOBS["overlap"])
    c.add_argument("--policy", choices=("hard", "significant", "free"),
                   default=DEFAULT_KNOBS["section_policy"])
    c.set_defaults(func=cmd_chunk)

    k = sub.add_parser("check", help="run the chunk invariants")
    k.add_argument("stems", nargs="*")
    k.add_argument("--all", action="store_true")
    k.add_argument("--no-reparse", action="store_true", help="skip the determinism re-chunk")
    k.add_argument("--spot-check", type=int, default=3,
                   help="chunks per document checked against the PDF itself (0 = skip)")
    k.add_argument("--ingest", action="store_true", default=True,
                   help="also check the corpus-wide ingest set (default on)")
    k.set_defaults(func=cmd_check)

    t = sub.add_parser("cite", help="one stored chunk -> its citation (the gate's demo)")
    t.add_argument("stem")
    t.add_argument("--index", type=int, default=0, help="which chunk of the document")
    t.add_argument("--chars", type=int, default=160, help="how much of the chunk text to print")
    t.add_argument("--drop-provenance", action="store_true",
                   help="strip every block's positions[] first: the citation must fail loudly")
    t.add_argument("--verify", action="store_true",
                   help="also check the cited pages against the PDF's own text")
    t.set_defaults(func=cmd_cite)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
