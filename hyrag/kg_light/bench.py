"""kg_light/bench.py — R2 answering the frozen evaluation set, one question at a time.

`cli query` answers one question and prints a trace; that is the right shape for a check and
the wrong shape for a benchmark, which needs 150 answers, a record each, and the ability to
resume after question 97 dies. This module is the loop.

Three things it insists on, each of which would corrupt a number if left to chance:

* **The LLM cache is off.** LightRAG caches completions by default, so a question answered
  once and asked again returns the cached answer with `calls: 0` — a cost column reading
  `$0.00` for work that was paid for. The scored run builds its instance with
  `enable_llm_cache=False`; the cache remains useful for *development*, which is why the
  builder takes it as an argument rather than the constant being flipped.
* **One question, one record, one append.** The out file is append-only and read back on
  resume, so a crash costs the question in flight, not the run. Failures are recorded as
  records with `status: error` and do not stop the loop.
* **The row's own numbers.** `query.ask` windows the proxy log around the call, so tokens
  and calls are the API's own; `wall_ms` is this loop's stopwatch, which includes the
  retrieval the row does in process and the answer generation.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from hyrag.kg_light import config, query

ANSWER_LOG = config.RESULTS / "kg_light_answers.jsonl"
_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


def done_ids(path: Path) -> set[str]:
    """Question ids already answered **successfully** in `path` — the resume set."""
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


async def answer_all(questions: list[dict], *, mode: str | None = None,
                     out: Path | None = None, resume: bool = True,
                     log=print) -> dict:
    """Answer every question in `questions`, appending one record each. Returns a tally."""
    from hyrag.kg_light.builder import build_rag

    out = out or ANSWER_LOG
    seen = done_ids(out) if resume else set()
    todo = [question for question in questions if question["id"] not in seen]
    log(f"  R2 bench · {len(questions)} questions · {len(todo)} to answer · "
        f"mode {mode or config.QUERY_MODE} · cache off · out {out}")
    if not todo:
        return {"answered": 0, "failed": 0, "skipped": len(questions)}

    # `enable_llm_cache=False`: see the module docstring. The extraction pass already ran and
    # its cache is irrelevant here — what is disabled is the *answer* cache.
    rag = build_rag(enable_llm_cache=False)
    await rag.initialize_storages()
    answered = failed = 0
    started_all = time.perf_counter()
    try:
        for index, question in enumerate(todo, 1):
            started = time.perf_counter()
            try:
                record = await query.ask(rag, question["question"], mode=mode)
                record["question_id"] = question["id"]
                record["class"] = question["class"]
                record = query.resolve(record)
                if record.get("status") != "success":
                    failed += 1
                else:
                    answered += 1
            except Exception as error:  # one bad question must not end a 150-record run
                failed += 1
                record = {
                    "question_id": question["id"], "class": question["class"],
                    "question": question["question"], "mode": mode or config.QUERY_MODE,
                    "status": "error", "error": f"{type(error).__name__}: {error}"[:400],
                    "answer": "",
                }
            record["wall_ms"] = round((time.perf_counter() - started) * 1000)
            append(out, record)
            usage = record.get("usage") or {}
            elapsed = time.perf_counter() - started_all
            rate = elapsed / index
            log(f"  [{index:3d}/{len(todo)}] {question['id']:<9} {question['class']:<9} "
                f"{record['wall_ms']/1000:6.1f}s  calls {usage.get('calls', 0):>3}  "
                f"tok {usage.get('total_tokens', 0):>8,}  "
                f"eta {(len(todo) - index) * rate / 60:5.1f}m")
    finally:
        await rag.finalize_storages()
    return {"answered": answered, "failed": failed, "skipped": len(questions) - len(todo)}


def run(questions: list[dict], **kwargs) -> dict:
    return asyncio.run(answer_all(questions, **kwargs))
