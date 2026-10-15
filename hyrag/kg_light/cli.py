"""kg_light/cli.py — the R2 box's command line.

    uv run python -m hyrag.kg_light.cli ingest --limit 1        # one document, end to end
    uv run python -m hyrag.kg_light.cli ingest                  # the ingest set (369 chunks)
    uv run python -m hyrag.kg_light.cli ingest --retry-failed   # resume documents a crash left FAILED
    uv run python -m hyrag.kg_light.cli reset                   # derived data only; keeps the LLM cache
    uv run python -m hyrag.kg_light.cli map                     # build + print the §5.6 map
    uv run python -m hyrag.kg_light.cli query --class thematic  # one question, traced
    uv run python -m hyrag.kg_light.cli check                   # the node's gate

`ingest` is the expensive command (it is the one extraction pass D1 pays for); everything
else is a read. `--limit` exists because S06's lesson was *sample first, read it, then
commit to the full run* — the ingest set is small enough to smoke-test on one document for
the price of one document's extraction, and a failure there costs minutes instead of an hour.

No computation belongs in this file: it parses arguments, calls the module that owns the
work, and prints what came back.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from hyrag.kg_light import checks, config, idmap, ingest, query

ANSWER_PREVIEW = 600  # characters printed per query; the full text goes to results/


def _setup_logging() -> None:
    """INFO to stderr, and the same to `parsed/lightrag/kg_light.log` for the session record."""
    config.WORKING_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(config.WORKING_DIR / "kg_light.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr), handler],
    )
    logging.getLogger("lightrag").setLevel(logging.INFO)


def _questions() -> list[dict]:
    path = config.REPO / "qset" / "questions.jsonl"
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _pick(args: argparse.Namespace) -> dict:
    records = _questions()
    if args.id:
        return next(record for record in records if record["id"] == args.id)
    if args.question_class:
        return next(record for record in records if record["class"] == args.question_class)
    if args.text:
        return {"id": "ad-hoc", "class": "ad-hoc", "question": args.text}
    raise SystemExit("give --id, --class, or --text")


# ---------------------------------------------------------------- commands


def cmd_ingest(args: argparse.Namespace) -> int:
    record = asyncio.run(ingest.ingest(limit=args.limit, retry=args.retry_failed))
    print("\n  ingest done")
    for key in ("documents", "chunks", "wall_clock_s", "llm_calls", "prompt_tokens",
                "completion_tokens", "total_tokens"):
        print(f"    {key}: {record[key]:,}" if isinstance(record[key], int) else f"    {key}: {record[key]}")
    print(f"    stored: {record['stored']}")
    print(f"    wrote {config.COST_PATH}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    removed = ingest.reset_index(keep_cache=not args.drop_cache)
    print("\n  reset (derived data only; `_ingest.jsonl` and the corpus are untouched)")
    for table, count in sorted(removed.items()):
        print(f"    {table}: {count} rows")
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    result = ingest.retry_failed()
    print(
        f"\n  retried {result['retried']} failed document(s) · "
        f"removed {result['duplicate_stubs_removed']} duplicate stub(s)"
    )
    return 0


def cmd_map(args: argparse.Namespace) -> int:
    document = idmap.build()
    counts = document["counts"]
    print(f"\n  map: {counts['lightrag_chunks']} LightRAG keys ↔ {counts['our_chunks']} our chunks")
    print(f"  bijective: {document['bijective']}")
    if document["problems"]["unresolved"]:
        print(f"  unresolved keys: {len(document['problems']['unresolved'])}")
    if document["unmapped_our_chunks"]:
        print(f"  our chunks never stored: {len(document['unmapped_our_chunks'])}")
    references = idmap.unmapped_source_ids(document["map"])
    print(
        f"  source_id references: {references['total_references']} "
        f"({references['distinct']} distinct) · unmapped {len(references['unmapped'])}"
    )
    print(f"  wrote {config.IDMAP_PATH}")
    return 0 if document["bijective"] and not references["unmapped"] else 1


def cmd_query(args: argparse.Namespace) -> int:
    async def run() -> dict:
        from hyrag.kg_light.builder import build_rag

        rag = build_rag()
        await rag.initialize_storages()
        try:
            question = _pick(args)
            record = await query.ask(rag, question["question"], mode=args.mode)
            record["question_id"] = question["id"]
            record["class"] = question["class"]
            return query.resolve(record)
        finally:
            await rag.finalize_storages()

    record = asyncio.run(run())
    counts = record["counts"]
    print(f"\n  {record['question_id']} [{record['class']}] · mode {record['mode']}")
    print(f"  {record['question']}\n")
    print(f"  entities {counts['entities']} · relations {counts['relationships']} · chunks {counts['chunks']}")
    print(f"  keywords: high {record['keywords']['high_level'][:5]} · low {record['keywords']['low_level'][:5]}")
    print(f"  retrieved chunks resolved through the map: {len(record['our_chunks'])} · unresolved {len(record['unresolved'])}")
    print(f"  latency: {record['latency_ms']} ms\n")
    print(f"  {record['answer'][:ANSWER_PREVIEW]}")
    path = query.write([record])
    print(f"\n  wrote {path}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    return asyncio.run(checks.run(args.mode))


# ---------------------------------------------------------------- parsing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyrag.kg_light.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_cmd = sub.add_parser("ingest", help="run the one extraction pass (D1)")
    ingest_cmd.add_argument("--limit", type=int, default=None, help="only the first N documents")
    ingest_cmd.add_argument(
        "--retry-failed", action="store_true",
        help="flip FAILED documents back to PENDING first (LightRAG never auto-resumes them)",
    )
    ingest_cmd.set_defaults(func=cmd_ingest)

    reset_cmd = sub.add_parser("reset", help="delete the row's derived data (keeps the LLM cache)")
    reset_cmd.add_argument("--drop-cache", action="store_true", help="also delete LIGHTRAG_LLM_CACHE")
    reset_cmd.set_defaults(func=cmd_reset)

    retry_cmd = sub.add_parser("retry", help="FAILED -> PENDING, so the next ingest resumes them")
    retry_cmd.set_defaults(func=cmd_retry)

    map_cmd = sub.add_parser("map", help="build the LightRAG-key ↔ chunk-id map and print it")
    map_cmd.set_defaults(func=cmd_map)

    query_cmd = sub.add_parser("query", help="one question through the row, traced")
    query_cmd.add_argument("--id", help="a question id from qset/questions.jsonl")
    query_cmd.add_argument("--class", dest="question_class", choices=checks.CLASSES)
    query_cmd.add_argument("--text", help="an ad-hoc question")
    query_cmd.add_argument("--mode", help=f"LightRAG query mode (default {config.QUERY_MODE})")
    query_cmd.set_defaults(func=cmd_query)

    check_cmd = sub.add_parser("check", help="the node's gate")
    check_cmd.add_argument("--mode", help=f"LightRAG query mode (default {config.QUERY_MODE})")
    check_cmd.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
