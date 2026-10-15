"""baseline/cli.py — the R1 box's command line.

    uv run python -m hyrag.baseline.cli ingest                          # _ingest.jsonl -> Postgres
    uv run python -m hyrag.baseline.cli search "text of a question"
    uv run python -m hyrag.baseline.cli search --question-id fact-003   # + where its evidence ranked
    uv run python -m hyrag.baseline.cli answer --question-id fact-003   # one cited answer, traced
    uv run python -m hyrag.baseline.cli answer --all                    # all 150 -> results/
    uv run python -m hyrag.baseline.cli check                           # the index's invariants

Why this file exists at all: `store.py` holds every line of logic, and this holds argument
parsing and printing. The pipeline has to be re-runnable by something that is not a human
with Jupyter open — the `judge` node calls these boxes, and §7.5 wants one command that
reproduces a number (D11: code, not a notebook). **No computation belongs in this file** —
the same rule that keeps encoding logic out of the HTTP adapter.

`search` is the tracing tool: rank, cosine distance and citation per row, so a question that
behaves oddly can be read rather than guessed at. `--question-id` adds the one line the gate
cares about — the rank of the chunk that question was written from.
"""

from __future__ import annotations

import argparse
import json
import logging

from hyrag.baseline import answer, store
from hyrag.baseline.checks import QSET_PATH, run_checks


def _questions() -> list[dict]:
    with QSET_PATH.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _question(question_id: str) -> dict:
    for question in _questions():
        if question["id"] == question_id:
            return question
    raise SystemExit(f"no question {question_id!r} in {QSET_PATH}")


# ---------------------------------------------------------------- commands


def cmd_ingest(args: argparse.Namespace) -> int:
    chunks = store.load_chunks()
    print(f"  embedding {len(chunks)} chunks …")
    with store.connect() as conn:
        written = store.ingest(conn, chunks)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM baseline_chunks")
            stored = cur.fetchone()[0]
    print(f"  wrote {written} rows · table holds {stored}")
    return 0 if written == stored else 1


def cmd_search(args: argparse.Namespace) -> int:
    question = _question(args.question_id) if args.question_id else None
    text = question["question"] if question else args.text
    if not text:
        raise SystemExit("give a question in quotes, or --question-id")

    with store.connect() as conn:
        rows = store.search(conn, text, k=args.k)
        if not rows:
            raise SystemExit("the table is empty — run `ingest` first")

        targets = set(question["evidence_target"]["chunk_ids"]) if question else set()
        print(f"\n  {text}\n")
        hit = None
        for rank, row in enumerate(rows, 1):
            flag = ""
            if row["chunk_id"] in targets:
                flag, hit = "  <- evidence target", hit or rank
            print(f"  {rank:>3}. {row['distance']:.4f}  {row['citation']}{flag}")
            if args.show:
                print(f"       {row['content'][:160].replace(chr(10), ' ')} …")

        if question:
            print(f"\n  evidence target: rank {hit} of {len(rows)} shown" if hit
                  else f"\n  evidence target: NOT in the top {len(rows)}")
    return 0


def cmd_answer(args: argparse.Namespace) -> int:
    """One question traced, or all 150 written to `results/`."""
    with store.connect() as conn:
        if not args.all:
            question = _question(args.question_id)
            print(f"\n  {question['id']}: {question['question']}\n")
            record = answer.answer_question(conn, question, k=args.k)
            print(record["answer"].strip())
            print(f"\n  citations used : {record['citations'] or 'none'}")
            print(f"  unknown sources: {record['unknown_sources'] or 'none'}")
            print(f"  evidence rank  : {record['evidence_rank']} of {args.k}")
            print(f"  latency        : {record['latency_ms']} ms · usage {record['usage']}")
            print(f"  prompt sha     : {record['prompt_sha256']}")
            return 0

        questions = _questions()
        records: list[dict] = []
        for index, question in enumerate(questions, 1):
            record = answer.answer_question(conn, question, k=args.k)
            records.append(record)
            print(f"  {index:>3}/{len(questions)} {question['id']:<10} "
                  f"{record['latency_ms']:>6} ms  cites={len(record['citations'])} "
                  f"unknown={len(record['unknown_sources'])}")

    path = answer.write_records(records)
    summary = answer.totals(records)
    print(f"\n  wrote {path}")
    for key, value in summary.items():
        print(f"    {key}: {value}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    checks = run_checks()
    for check in checks:
        print(check.line())
    failed = [check for check in checks if check.ok is False]
    print(f"\n  {len(checks)} checks · {len(failed)} failed")
    return 1 if failed else 0


# ---------------------------------------------------------------- argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyrag.baseline", description="the naive RAG index")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "ingest", help="embed the chunk set and replace the table"
    ).set_defaults(func=cmd_ingest)

    search = commands.add_parser("search", help="top-k for a question")
    search.add_argument("text", nargs="?", default=None, help="the question, in quotes")
    search.add_argument("--question-id", default=None, help="read the text from the qset instead")
    search.add_argument("--k", type=int, default=20)
    search.add_argument("--show", action="store_true", help="print the start of each chunk")
    search.set_defaults(func=cmd_search)

    commands.add_parser(
        "check", help="index invariants and the recall curve"
    ).set_defaults(func=cmd_check)

    answer_command = commands.add_parser("answer", help="generate cited answers")
    answer_command.add_argument("--question-id", default=None, help="one question, traced")
    answer_command.add_argument("--all", action="store_true", help="all 150 -> results/")
    answer_command.add_argument("--k", type=int, default=answer.K)
    answer_command.set_defaults(func=cmd_answer)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="  · %(message)s")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
