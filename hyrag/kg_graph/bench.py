"""kg_graph/bench.py — R3 answering the frozen evaluation set through its own CLI.

`checks.check_query` answers **one** question, the first thematic one, and is the node's
gate; a benchmark needs 120 answers on one configuration with a record each, and the ability
to resume after question 97 dies. That is this module.

**One R3 process at a time.** Token accounting windows the proxy log with a marker taken
before the call and `usage_since(marker, row="kg-graph")` after it. Two R3 processes running
concurrently would interleave their calls inside each other's windows and silently attribute
one question's tokens to another — so the loop is sequential by construction, and running a
second copy against the same row tag is a mistake, not a speedup. (R2 may run beside it: a
different row tag is a different window.)

**Per-class method** — the row's own, not a knob we tuned:

| class | method | why |
|---|---|---|
| thematic | `global` (+ dynamic community selection, level 2) | community summarisation is the class it exists for (D12) |
| multi-hop | `local` | entity/relationship context around the entities the question names |
| factoid | `local` | the same path, with the fact lookup left to the entity descriptions |

`basic` is not used: it is GraphRAG's vector-over-text-units route, which is R1's method
wearing GraphRAG's tables, and scoring it as "the GraphRAG row" would measure the wrong
thing. The method actually used is a field in every record, so the report never has to
infer it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from hyrag import llm_log
from hyrag.kg_graph import checks, config, resolve, run
from hyrag.parse.config import REPO

ANSWER_LOG = REPO / "results" / "kg_graph_answers.jsonl"
METHODS = {"thematic": "global", "multi-hop": "local", "factoid": "local"}
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


def answer_all(questions: list[dict], *, out: Path | None = None, resume: bool = True,
               root: Path | None = None, log=print) -> dict:
    """Answer every question in `questions`, appending one record each. Returns a tally."""
    out = out or ANSWER_LOG
    root = root or config.GRAPH_ROOT
    seen = done_ids(out) if resume else set()
    todo = [question for question in questions if question["id"] not in seen]
    counts: dict[str, int] = {}
    for question in todo:
        counts[question["class"]] = counts.get(question["class"], 0) + 1
    log(f"  R3 bench · {len(questions)} questions · {len(todo)} to answer · "
        f"{counts} · out {out}")
    if not todo:
        return {"answered": 0, "failed": 0, "skipped": len(questions)}

    answered = failed = 0
    started_all = time.perf_counter()
    for index, question in enumerate(todo, 1):
        method = METHODS[question["class"]]
        marker = llm_log.log_marker()
        started = time.perf_counter()
        try:
            stdout = run.query(root, question["question"], method=method,
                               log_path=root / "logs" / "answer.stdout.log")
            answer = checks._answer_from_stdout(stdout)
            resolution = resolve.resolve(answer, root)
            usage = llm_log.usage_since(marker, row=config.LLM_ROW)
            status = "success" if answer.strip() else "empty"
            record = {
                "question_id": question["id"],
                "class": question["class"],
                "question": question["question"],
                "method": method,
                "dynamic_selection": config.DYNAMIC_SELECTION if method == "global" else None,
                "community_level": config.COMMUNITY_LEVEL if method == "global" else None,
                "answer": answer,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "usage": usage,
                "status": status,
                "citations": resolution["citations"],
                "evidence_chunk_ids": resolution["evidence_chunk_ids"],
                "citation_resolution": {key: resolution[key] for key in
                                        ("markers", "ids", "distinct_ids", "resolved_ids",
                                         "truncated_markers", "unresolved")},
            }
            answered += status == "success"
            failed += status != "success"
            if status != "success":
                # An empty answer means `_answer_from_stdout` found no block after the logs —
                # usually because the run stopped on something the CLI did not print as an
                # error. Keep the tail, or the record says only that it failed.
                record["stdout_tail"] = stdout[-1200:]
        except (Exception, SystemExit) as error:  # one bad question must not end the run
            failed += 1
            record = {
                "question_id": question["id"], "class": question["class"],
                "question": question["question"], "method": method,
                "answer": "", "status": "error",
                "error": f"{type(error).__name__}: {error}"[:400],
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "usage": llm_log.usage_since(marker, row=config.LLM_ROW),
            }
        append(out, record)
        usage = record.get("usage") or {}
        elapsed = time.perf_counter() - started_all
        rate = elapsed / index
        log(f"  [{index:3d}/{len(todo)}] {question['id']:<9} {question['class']:<9} "
            f"{method:<6} {record['latency_ms']/1000:7.1f}s  calls {usage.get('calls', 0):>4}  "
            f"tok {usage.get('total_tokens', 0):>9,}  "
            f"eta {(len(todo) - index) * rate / 60:5.1f}m")
    return {"answered": answered, "failed": failed, "skipped": len(questions) - len(todo)}
