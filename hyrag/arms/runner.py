"""arms/runner.py — one command over a question set, one record per question, resume.

The two backends could not be more different — LightRAG is an in-process instance holding a
Postgres connection, GraphRAG is a subprocess per question — so the runner owns the difference
and a caller never imports either system. Three habits carry over from the benchmark runners
(`kg_light/bench.py`, `kg_graph/bench.py`) because they are what made a 150-question run
survivable:

* **append, then read back on resume** — a crash costs the question in flight;
* **one bad question does not end the run** — it is recorded as an error row and the loop
  continues;
* **the row's own numbers** — tokens come from the proxy window around the call, latency from
  this loop's stopwatch.

The output is one JSONL per arm under `results/` (gitignored: answers quote the corpus).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from hyrag.arms.bridged import BridgedArm
from hyrag.arms.lightrag import LightRAGArm
from hyrag.arms.record import AnswerRecord
from hyrag.kg_light import config as kg_light_config

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}
ARMS = {"kg-light": LightRAGArm, "kg-graph-bridged": BridgedArm}
RESULTS = kg_light_config.RESULTS


def questions(*, ids=None, classes=None, limit=None) -> list[dict]:
    """The frozen set, filtered — read directly, so this box owes nothing to a sampling module."""
    path = kg_light_config.REPO / "qset" / "questions.jsonl"
    with path.open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    if classes:
        wanted = set(classes)
        records = [record for record in records if record["class"] in wanted]
    if ids:
        wanted = set(ids)
        records = [record for record in records if record["id"] in wanted]
    return records[:limit] if limit else records


def out_path(arm: str) -> Path:
    return RESULTS / f"arms_{arm.replace('-', '_')}.jsonl"


def done_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    ids = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("status") == "success" and record.get("question_id"):
                ids.add(record["question_id"])
    return ids


def append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, **_JSON) + "\n")
        handle.flush()


def answer_many(arm: str, records: list[dict], *, out: Path | None = None,
                resume: bool = True, log=print) -> dict:
    """Answer every question with one arm. Returns a tally plus the records produced."""
    out = out or out_path(arm)
    seen = done_ids(out) if resume else set()
    todo = [record for record in records if record["id"] not in seen]
    log(f"  {arm} · {len(records)} questions · {len(todo)} to answer · out {out}")
    if not todo:
        return {"answered": 0, "failed": 0, "skipped": len(records), "records": []}

    if arm == "kg-light":
        produced = asyncio.run(_kg_light_loop(todo, out, log))
    else:
        produced = _bridged_loop(todo, out, log)
    answered = sum(1 for record in produced if record.get("status") == "success")
    return {
        "answered": answered,
        "failed": len(produced) - answered,
        "skipped": len(records) - len(todo),
        "records": produced,
    }


async def _kg_light_loop(todo: list[dict], out: Path, log) -> list[dict]:
    arm = await LightRAGArm().open()
    produced: list[dict] = []
    try:
        for index, question in enumerate(todo, 1):
            record = await _one_kg_light(arm, question, index, len(todo), log, out)
            produced.append(record)
    finally:
        await arm.close()
    return produced


async def _one_kg_light(arm, question: dict, index: int, total: int, log, out: Path) -> dict:
    try:
        record = await arm.answer(question)
        payload = record.to_dict()
    except Exception as error:  # one bad question must not end a run
        payload = _error(arm.name, question, error)
    append(out, payload)
    usage = payload.get("usage") or {}
    log(f"  [{index:3d}/{total}] {question['id']:<9} {question['class']:<9} "
        f"{payload.get('latency_ms', 0)/1000:6.1f}s  calls {usage.get('calls', 0):>3}  "
        f"tok {usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0):>8,}  "
        f"{payload.get('status')}")
    return payload


def _bridged_loop(todo: list[dict], out: Path, log) -> list[dict]:
    arm = BridgedArm()
    produced: list[dict] = []
    for index, question in enumerate(todo, 1):
        try:
            payload = arm.answer(question).to_dict()
        except (Exception, SystemExit) as error:
            payload = _error(arm.name, question, error)
        append(out, payload)
        produced.append(payload)
        usage = payload.get("usage") or {}
        log(f"  [{index:3d}/{len(todo)}] {question['id']:<9} {question['class']:<9} "
            f"{payload.get('latency_ms', 0)/1000:6.1f}s  calls {usage.get('calls', 0):>4}  "
            f"tok {(usage.get('prompt_tokens') or 0) + (usage.get('completion_tokens') or 0):>9,}  "
            f"{payload.get('status')}")
    return produced


def answer_one(arm: str, question: dict) -> AnswerRecord:
    """One question, no file, no resume — the gate's shape."""
    if arm == "kg-light":
        return asyncio.run(_answer_one_kg_light(question))
    return BridgedArm().answer(question)


async def _answer_one_kg_light(question: dict) -> AnswerRecord:
    instance = await LightRAGArm().open()
    try:
        return await instance.answer(question)
    finally:
        await instance.close()


def _error(arm: str, question: dict, error: Exception) -> dict:
    return {
        "arm": arm,
        "question_id": question["id"],
        "question_class": question["class"],
        "question": question["question"],
        "status": "error",
        "error": f"{type(error).__name__}: {error}"[:400],
        "answer": "",
        "latency_ms": 0,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    }
