"""report/ablation.py — §7.4's native-vs-bridged delta, measured on five thematic questions.

The project's central Phase 2 claim is D1: the delivered system pays for **one** extraction pass
and bridges GraphRAG's community layer over LightRAG's graph. The obvious attack — *"you
benchmarked a GraphRAG you didn't deploy"* — is answered by running the same search over both
indexes and comparing.

Design choices that keep it a comparison and not a demo:

* **Same questions as R3's judged sample.** The native side is not re-generated: R3's recorded
  answers on these ids are on disk (`results/kg_graph_answers.jsonl`), paid for in Phase 1.
  Regenerating them would change the answers being compared.
* **Same search settings** — `global` + dynamic community selection at level 2, which is what
  the bridged arm runs and what R3 ran.
* **Deterministic comparison only.** The judged head-to-head is the judge's Phase 2 addendum and
  needs an R4 loader the harness does not have; this file measures what can be measured without
  a model: evidence-target retrieval, citation resolution, answer length, latency and cost.
* **Five questions, not twenty.** The budget decision of 2026-10-17; each bridged thematic turn
  is ≈ $0.45 and ≈ 5 minutes.
"""

from __future__ import annotations

import json
from pathlib import Path

from hyrag.arms import runner
from hyrag.judge.config import cost_usd
from hyrag.kg_light import config as kg_light_config

IDS = ("them-102", "them-112", "them-122", "them-128", "them-138")
NATIVE = kg_light_config.RESULTS / "kg_graph_answers.jsonl"
OUT = kg_light_config.RESULTS / "ablation.jsonl"
SUMMARY = kg_light_config.RESULTS / "ablation_record.json"
_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


def native_records() -> dict[str, dict]:
    rows = [json.loads(line) for line in NATIVE.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["question_id"]: row for row in rows}


def run() -> dict:
    native = native_records()
    missing = [question_id for question_id in IDS if question_id not in native]
    if missing:
        raise SystemExit(f"no native R3 answer for {missing} — run `hyrag.kg_graph.cli answer --eval-set` first")

    records = []
    print(f"\n  §7.4 ablation · {len(IDS)} thematic questions · native answers reused")
    for index, question_id in enumerate(IDS, start=1):
        question = next(record for record in runner.questions(ids=[question_id]))
        print(f"  [{index}/{len(IDS)}] {question_id} · bridged arm (≈5 min) …", flush=True)
        bridged = runner.answer_one("kg-graph-bridged", question)
        source = native[question_id]
        target = set(question["evidence_target"]["chunk_ids"])
        record = {
            "question_id": question_id,
            "question": question["question"],
            "target_chunks": sorted(target),
            "native": {
                "evidence_chunk_ids": source.get("evidence_chunk_ids") or [],
                "citations": source.get("citations") or [],
                "answer_chars": len(source.get("answer") or ""),
                "latency_ms": source.get("latency_ms"),
                "tokens_in": (source.get("usage") or {}).get("prompt_tokens", 0),
                "tokens_out": (source.get("usage") or {}).get("completion_tokens", 0),
                "calls": (source.get("usage") or {}).get("calls", 0),
            },
            "bridged": {
                "evidence_chunk_ids": list(bridged.evidence_chunk_ids),
                "citations": list(bridged.citations),
                "answer_chars": len(bridged.answer),
                "latency_ms": bridged.latency_ms,
                "tokens_in": bridged.tokens_in,
                "tokens_out": bridged.tokens_out,
                "calls": bridged.calls,
            },
        }
        for side in ("native", "bridged"):
            body = record[side]
            body["retrieval_hit"] = bool(target & set(body["evidence_chunk_ids"]))
            body["cost_usd"] = cost_usd(body["tokens_in"], body["tokens_out"])
        record["target_hits"] = {
            "native": len(target & set(record["native"]["evidence_chunk_ids"])),
            "bridged": len(target & set(record["bridged"]["evidence_chunk_ids"])),
            "of": len(target),
        }
        records.append(record)
        for side in ("native", "bridged"):
            body = record[side]
            print(f"      {side:<8} hit {'Y' if body['retrieval_hit'] else 'n'} · "
                  f"{len(body['citations'])} cites · {body['answer_chars']} chars · "
                  f"{(body['latency_ms'] or 0)/1000:.0f}s · ${body['cost_usd']:.3f}")

    OUT.write_text("".join(json.dumps(row, **_JSON) + "\n" for row in records), encoding="utf-8")

    def mean(side: str, field: str) -> float:
        return round(sum(row[side][field] for row in records) / len(records), 4)

    summary = {
        "type": "ablation",
        "n": len(records),
        "ids": list(IDS),
        "settings": "global + dynamic community selection at level 2, identical on both sides",
        "native_index": "parsed/kg_graph (644 communities / 644 reports, 2,655 extraction calls)",
        "bridged_index": "parsed/kg_graph_bridged (932 communities / 932 reports, one extraction pass shared)",
        "native": {
            "retrieval_hit_rate": round(sum(row["native"]["retrieval_hit"] for row in records) / len(records), 4),
            "citations_mean": round(sum(len(r["native"]["citations"]) for r in records) / len(records), 2),
            "answer_chars_mean": mean("native", "answer_chars"),
            "latency_s_mean": round(sum((r["native"]["latency_ms"] or 0) for r in records) / len(records) / 1000, 1),
            "tokens_mean": round(sum(r["native"]["tokens_in"] + r["native"]["tokens_out"] for r in records) / len(records)),
            "cost_usd_mean": round(sum(r["native"]["cost_usd"] for r in records) / len(records), 4),
        },
        "bridged": {
            "retrieval_hit_rate": round(sum(row["bridged"]["retrieval_hit"] for row in records) / len(records), 4),
            "citations_mean": round(sum(len(r["bridged"]["citations"]) for r in records) / len(records), 2),
            "answer_chars_mean": mean("bridged", "answer_chars"),
            "latency_s_mean": round(sum(r["bridged"]["latency_ms"] for r in records) / len(records) / 1000, 1),
            "tokens_mean": round(sum(r["bridged"]["tokens_in"] + r["bridged"]["tokens_out"] for r in records) / len(records)),
            "cost_usd_mean": round(sum(r["bridged"]["cost_usd"] for r in records) / len(records), 4),
        },
    }
    summary["total_cost_usd"] = round(sum(r["bridged"]["cost_usd"] for r in records), 4)
    SUMMARY.write_text(json.dumps(summary, **_JSON) + "\n", encoding="utf-8")
    print(f"\n  {summary}")
    print(f"  wrote {OUT} and {SUMMARY}")
    return summary
