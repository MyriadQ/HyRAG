"""answer/checks.py — the gate, and the delivered row's evaluation.

Two different things, both needed:

* **`gate()`** — the node's gate: one traced example per class, citations verified **against
  the original PDF** with `chunker/checks.py::pdf_page_coverage` (a second, unrelated library
  reads the sheets), the used/invented split printed, and the route and cost beside them.
* **`evaluate()`** — R4's row over the 30 eval questions: evidence-target hit rate, citation
  resolution, refusals, tokens, latency and cost per class. Deterministic, no judge calls —
  the judged R4 row is the judge's addendum and needs a loader the harness does not have yet.

`evaluate` classifies by the question's **gold class**, not the router's prediction. The two
measurements are separate on purpose: the router's accuracy is its own 0.867 on a held-out
split, and this row asks "given the right route, how good is the delivered answer?" Mixing them
would make a routing error look like an answer error.
"""

from __future__ import annotations

import json
from pathlib import Path

from hyrag.answer import pipeline
from hyrag.answer import render
from hyrag.answer.gather import EVIDENCE_BUDGET
from hyrag.chunker.checks import SPOT_CHECK_THRESHOLD, pdf_page_coverage
from hyrag.kg_light import config as kg_light_config
from hyrag.parse.config import PARSED

GATE_IDS = ("fact-001", "mult-051", "them-101")
GATE_LOG = kg_light_config.RESULTS / "answer_gate.jsonl"
EVAL_LOG = kg_light_config.RESULTS / "answer_eval.jsonl"
EVAL_RECORD = kg_light_config.RESULTS / "answer_eval_record.json"
# The judge's coverage: all factoid + multi-hop, thematic sampled (120). Separate files, so the
# router's 30-question eval and the judged row never overwrite each other.
JUDGE_LOG = kg_light_config.RESULTS / "answer_judge.jsonl"
JUDGE_RECORD = kg_light_config.RESULTS / "answer_judge_record.json"
_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


def _meta(stem: str) -> dict:
    return json.loads((PARSED / stem / "meta.json").read_text(encoding="utf-8"))


def pdf_coverage(record: dict) -> list[dict]:
    """Each used citation's chunk, checked against the cited sheets of the original PDF."""
    results = []
    for entry in record["evidence"]:
        if entry["citation"] not in record["citations_used"]:
            continue
        chunk = next(row for row in kg_light_config.our_chunks() if row["chunk_id"] == entry["chunk_id"])
        occurrence = chunk["occurrences"][0]
        meta = _meta(occurrence["stem"])
        # `page_range` is [first, last] — the whole span, not two sheets. Reading only the
        # endpoints scored a correct citation at 0.50 until this was fixed (S14).
        pages = list(range(int(occurrence["page_range"][0]), int(occurrence["page_range"][1]) + 1))
        coverage, words = pdf_page_coverage(Path(meta["source"]), pages, chunk["content"])
        results.append({"citation": entry["citation"], "coverage": round(coverage, 3),
                        "words": words, "pages": pages})
    return results


def gate() -> int:
    print("\n  answer gate")
    everything_green = True
    GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with GATE_LOG.open("w", encoding="utf-8") as handle:
        for question_id in GATE_IDS:
            question = next(record for record in _questions() if record["id"] == question_id)
            print(f"\n  {question_id} · {question['class']} · {question['question'][:100]}")
            record = pipeline.answer_question(question)
            coverage = pdf_coverage(record)
            worst = min((row["coverage"] for row in coverage), default=0.0)
            checks = {
                "answer": bool(record["answer"].strip()) and not record["refused"],
                "citations_used": bool(record["citations_used"]),
                "no_invented": not record["unknown_sources"],
                "pdf_spot_check": bool(coverage) and worst >= SPOT_CHECK_THRESHOLD,
                "cost_recorded": record["usage"]["total_tokens"] > 0 and record["latency_ms"] > 0,
            }
            print(f"    route      {'+'.join(record['route']['arms'])}"
                  + (" + fuse" if record["route"]["fuse"] else "")
                  + f" · evidence {len(record['evidence'])} chunks"
                  + (f" · {EVIDENCE_BUDGET[question['class']]} budget" if record["evidence"] else ""))
            for name, passed in checks.items():
                detail = ""
                if name == "citations_used":
                    detail = f"{len(record['citations_used'])} · {record['citations_used'][:1]}"
                elif name == "no_invented":
                    detail = f"{len(record['unknown_sources'])} unknown"
                elif name == "pdf_spot_check":
                    detail = f"worst coverage {worst:.2f} (threshold {SPOT_CHECK_THRESHOLD}) " + \
                             ", ".join(f"{row['coverage']:.2f}" for row in coverage)
                elif name == "cost_recorded":
                    detail = (f"{record['usage']['total_tokens']:,} tokens · "
                              f"{record['latency_ms']/1000:.1f}s · "
                              f"${record['cost_usd']:.4f} (arms ${sum(a['cost_usd'] for a in record['arms'].values()):.4f})")
                print(f"    {'✓' if passed else '✗'} {name:<14} {detail}")
            print(f"    answer     {record['answer'][:260].replace(chr(10), ' ')}…")
            everything_green = everything_green and all(checks.values())
            handle.write(json.dumps({**record, "pdf": coverage, "checks": checks}, **_JSON) + "\n")
            handle.flush()

    print(f"\n  wrote {GATE_LOG}")
    print(f"\n  gate: {'GREEN' if everything_green else 'RED'}\n")
    return 0 if everything_green else 1


def evaluate(coverage: str = "router", *, limit: int | None = None,
             resume: bool = True) -> dict:
    """Answer one of two coverages and measure the row. No judge calls.

    `router` — the 30 held-out questions (the router's split), the delivered row's own eval.
    `judge`  — the judge's 120 (all factoid + multi-hop, thematic sampled), which is what makes
               R4's table cell comparable with R1–R3's.

    **Append per question, resume by id.** A 40-minute run is longer than a proxy connection
    lives; an eval that holds 120 records in memory and writes them at the end loses the lot
    when the socket drops at question 90 (observed 2026-10-17). One bad question is recorded as
    an error row and retried on the next run, and a failed row never counts as an answer.
    """
    if coverage == "judge":
        from hyrag.qset import select

        questions = select.evaluation_set()
        log, record_path = JUDGE_LOG, JUDGE_RECORD
    else:
        from hyrag.router import config as router_config

        questions = router_config.split()["eval"]
        log, record_path = EVAL_LOG, EVAL_RECORD
    if limit:
        questions = questions[:limit]

    existing = _read_rows(log)
    seen = {row["question_id"] for row in existing if row.get("status") == "success"} if resume else set()
    todo = [question for question in questions if question["id"] not in seen]
    print(f"\n  R4 evaluation · coverage {coverage} · {len(questions)} questions · "
          f"{len(todo)} to answer · {len(existing)} on disk")

    log.parent.mkdir(parents=True, exist_ok=True)
    if not existing:
        log.write_text("", encoding="utf-8")
    with log.open("a", encoding="utf-8") as handle:
        for index, question in enumerate(todo, 1):
            try:
                record = pipeline.answer_question(question)
                target = set(question["evidence_target"]["chunk_ids"])
                evidence = {entry["chunk_id"] for entry in record["evidence"]}
                record["retrieval_hit"] = bool(target & evidence)
                record["status"] = "success"
            except Exception as error:  # a dropped socket must not cost the run
                record = {
                    "question_id": question["id"], "question_class": question["class"],
                    "question": question["question"], "answer": "", "citations_used": [],
                    "unknown_sources": [], "evidence": [], "arms": {},
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "latency_ms": 0, "cost_usd": 0.0, "retrieval_hit": False,
                    "status": "error", "error": f"{type(error).__name__}: {error}"[:300],
                }
            handle.write(json.dumps(record, **_JSON) + "\n")
            handle.flush()
            print(f"  [{index:2d}/{len(todo)}] {question['id']:<9} {question['class']:<9} "
                  f"hit {'Y' if record.get('retrieval_hit') else 'n'}  "
                  f"{record['latency_ms']/1000:5.1f}s  ${record['cost_usd']:.4f}  "
                  f"cites {len(record.get('citations_used') or [])}"
                  + ("" if record["status"] == "success" else f"  ERROR {record['error'][:60]}"))

    rows = [row for row in _read_rows(log) if row.get("status") == "success"]
    summary = {"type": "answer_eval", "coverage": coverage, "n": len(rows),
               "per_class": {}, "total_cost_usd": 0.0,
               "errors": len(_read_rows(log)) - len(rows)}
    for label in ("factoid", "multi-hop", "thematic"):
        subset = [row for row in rows if row["question_class"] == label]
        if not subset:
            continue
        summary["per_class"][label] = {
            "n": len(subset),
            "retrieval_hit_rate": round(sum(row["retrieval_hit"] for row in subset) / len(subset), 4),
            "refusals": sum(1 for row in subset if row.get("refused")),
            "citations_mean": round(sum(len(row["citations_used"]) for row in subset) / len(subset), 2),
            "unknown_sources": sum(len(row["unknown_sources"]) for row in subset),
            # End to end: the arms and the generation are sequential, so the turn's wall clock
            # is the sum. Reporting only the generation would make a 51-second thematic turn
            # look like a 4-second one.
            "latency_s_mean": round(sum(
                row["latency_ms"] + sum(arm["latency_ms"] for arm in row["arms"].values())
                for row in subset) / len(subset) / 1000, 1),
            "answer_latency_s_mean": round(sum(row["latency_ms"] for row in subset) / len(subset) / 1000, 1),
            "cost_usd_mean": round(sum(row["cost_usd"] for row in subset) / len(subset), 5),
            "answer_chars_mean": round(sum(len(row["answer"]) for row in subset) / len(subset)),
        }
    summary["total_cost_usd"] = round(sum(row["cost_usd"] for row in rows), 4)
    record_path.write_text(json.dumps(summary, **_JSON) + "\n", encoding="utf-8")

    for label, cell in summary["per_class"].items():
        print(f"  {label:<9} n {cell['n']:>2} · hit {cell['retrieval_hit_rate']:.2f} · "
              f"cites {cell['citations_mean']} · refused {cell['refusals']} · "
              f"{cell['latency_s_mean']}s · ${cell['cost_usd_mean']}")
    print(f"  total ${summary['total_cost_usd']} · {summary['errors']} error(s) · wrote {log}")
    return summary


def _read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _questions() -> list[dict]:
    with (kg_light_config.REPO / "qset" / "questions.jsonl").open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
