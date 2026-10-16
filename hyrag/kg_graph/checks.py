"""kg_graph/checks.py — the node's gate, as one command.

    uv run python -m hyrag.kg_graph.cli check

The gate (`ROADMAP.md`, `nodes/kg-graph.md`):

> its own CLI completes over the corpus; its global search answers a thematic question from
> its native tables; entity, relation, community and token counts logged

Each check prints a number rather than a verdict:

1. **the tables** — every output table GraphRAG should have written, with its row count. A
   missing table is a workflow that silently did nothing; the count is how that shows.
2. **text units ↔ chunks** — 369 in, 369 matched to our chunks **by content**, with the
   document-id join cross-checked against the input filenames. This is D16's evidence: not
   "we sent 369 files" but "what GraphRAG stored is our chunk set".
3. **tokens** — from the proxy log, windowed to the run recorded in the root's
   `index_record.json`, with GraphRAG's own per-process counters beside it as a cross-check
   (they count logical requests; the proxy counts attempts, and the retries explain the gap).
   The other half of D1's ledger, beside `kg-light`'s 2,347 calls / 3.43 M tokens / 648.8 s.
4. **a thematic query through its own CLI** — global search with dynamic community selection,
   answered from `community_reports` + the LanceDB vectors, with no LightRAG id anywhere.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from hyrag import llm_log
from hyrag.kg_graph import config, inputs, resolve, run, tables

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- the checks


def check_tables(root: Path) -> tuple[bool, dict[str, int]]:
    counts = tables.counts(root)
    missing = [name for name, count in counts.items() if count == 0]
    ok = not missing
    print(f"  {'✓' if ok else '✗'} tables: " + " · ".join(f"{name} {count}" for name, count in counts.items()))
    if missing:
        print(f"      empty or missing: {', '.join(missing)}")
    return ok, counts


def check_textunits(root: Path) -> tuple[bool, dict]:
    report = tables.textunit_map(root)
    # The root's own manifest says how many chunks it was built from — a smoke root holds 8,
    # not the corpus's 369, and the check must compare against what this root was meant to hold.
    expected = inputs.manifest(root)["chunks"]
    ok = (
        report["text_units"] == expected
        and report["mapped"] == expected
        and report["distinct_chunks"] == expected
        and not any(report["problems"].values())
    )
    print(
        f"  {'✓' if ok else '✗'} text units: {report['text_units']} stored · {report['mapped']} matched to "
        f"our chunks by content · {report['distinct_chunks']} distinct · problems {report['problems']}"
    )
    for key in ("unmatched_text", "unmapped_document", "mismatch"):
        for problem in report[key][:3]:
            print(f"      {key}: {problem}")

    # Persisted for the same reason `kg-light` persists its map: Phase 2's ablation has to
    # name which native text unit a report or entity came from, and re-deriving that later
    # costs a full read of two tables.
    document = {
        "type": "kg_graph_textunit_map",
        "graphrag_pin": config.GRAPHRAG_PIN,
        "settings_sha256": config.settings_sha256(),
        "counts": {"text_units": report["text_units"], "mapped": report["mapped"],
                   "distinct_chunks": report["distinct_chunks"]},
        "problems": report["problems"],
        "map": report["map"],
    }
    config.TEXTUNIT_MAP.write_text(json.dumps(document, **_JSON) + "\n", encoding="utf-8")
    return ok, report


def check_tokens(root: Path, *, since: str | None = None, until: str | None = None) -> tuple[bool, dict]:
    """Token accounting from the proxy's own log — the API's `usage`, not a model of it.

    `hyrag/serving/llm.py` writes one record per call, so this counts exactly what GraphRAG
    sent through the local endpoint: prompt/completion tokens, calls, and how many requests
    had their `response_format` rewritten. GraphRAG's own per-call metrics are reported
    beside it as a cross-check (two independent counters agreeing is worth more than one).
    """
    records = [record for record in _proxy_records() if record.get("row") in ("kg-graph", "unknown")]
    # A window, because the proxy log is append-only across runs: without it a re-index would
    # inherit the previous run's tokens (and the smoke root's), and the report would overstate
    # the cost of the pass it claims to measure.
    if since:
        records = [record for record in records if str(record.get("ts") or "") >= since]
    if until:
        records = [record for record in records if str(record.get("ts") or "") <= until]
    calls = len(records)
    totals = {
        "prompt_tokens": sum(int(record.get("prompt_tokens") or 0) for record in records),
        "completion_tokens": sum(int(record.get("completion_tokens") or 0) for record in records),
        "total_tokens": sum(int(record.get("total_tokens") or 0) for record in records),
        "rewritten": sum(1 for record in records if record.get("response_format_rewritten")),
        "errors": sum(1 for record in records if int(record.get("status") or 0) >= 400),
    }
    ok = calls > 0 and totals["total_tokens"] > 0
    print(
        f"  {'✓' if ok else '✗'} tokens: {calls} calls · prompt {totals['prompt_tokens']:,} · "
        f"completion {totals['completion_tokens']:,} · total {totals['total_tokens']:,} · "
        f"response_format repaired {totals['rewritten']} · errors {totals['errors']}"
    )
    return ok, {"llm_calls": calls, **totals}


def _proxy_records() -> list[dict]:
    path = config.LLM_PROXY_LOG
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check_query(root: Path, question: str | None = None) -> tuple[bool, dict]:
    question = question or first_thematic_question()
    log_path = root / "logs" / "query.stdout.log"
    marker = llm_log.log_marker()
    started = time.perf_counter()
    stdout = run.query(root, question, method="global", log_path=log_path)
    latency_ms = round((time.perf_counter() - started) * 1000)
    usage = llm_log.usage_since(marker, row=config.LLM_ROW)
    answer = _answer_from_stdout(stdout)
    # GraphRAG answers a query it cannot ground with a canned refusal; an empty answer and a
    # refusal are different failures, and both are failures for this row's gate.
    refusal = "unable to answer this question" in answer.lower()
    # The answer cites `[Data: Reports (…)]` — resolvable through the native tables, and a
    # marker that does not resolve is as bad as a citation naming a page that does not exist.
    resolution = resolve.resolve(answer, root)
    ok = len(answer.strip()) > 0 and not refusal and not resolution["unresolved"]
    detail = "canned refusal" if refusal else f"{len(answer)} chars"
    print(f"  {'✓' if ok else '✗'} global query: {detail} · {latency_ms:,} ms · mode global · dynamic selection on")
    print(f"      {question[:110]}")
    print(f"      {answer[:200].replace(chr(10), ' ')}…")
    print(f"      tokens: {usage['calls']} calls · {usage['total_tokens']:,} total")
    print(f"      citations: {len(resolution['citations'])} resolved from "
          f"{resolution['distinct_ids']} marker ids · unresolved {len(resolution['unresolved'])}"
          + (f" · +more (truncated lists) {resolution['truncated_markers']}" if resolution["truncated_markers"] else ""))
    if resolution["citations"]:
        print(f"      e.g. {resolution['citations'][0]}")
    record = {"question": question, "answer": answer, "latency_ms": latency_ms,
              "usage": usage, "method": "global", "dynamic_selection": config.DYNAMIC_SELECTION,
              "community_level": config.COMMUNITY_LEVEL,
              "citations": resolution["citations"],
              "evidence_chunk_ids": resolution["evidence_chunk_ids"],
              "citation_resolution": {key: resolution[key] for key in
                                      ("markers", "ids", "distinct_ids", "resolved_ids",
                                       "truncated_markers", "unresolved")}}
    config.QUERY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with config.QUERY_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, **_JSON) + "\n")
    return ok, record


def _answer_from_stdout(stdout: str) -> str:
    """The CLI prints logs and then the answer. Take everything after the last log line."""
    lines = stdout.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].strip()
        if line.startswith(("INFO", "WARNING", "ERROR", "DEBUG", "╭", "│", "╰", "─")):
            return "\n".join(lines[index + 1 :]).strip()
    return stdout.strip()


def first_thematic_question() -> str:
    """The first thematic question in file order — the same rule `kg-light` used."""
    from hyrag.kg_light import config as kg_light_config

    path = kg_light_config.REPO / "qset" / "questions.jsonl"
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("class") == "thematic":
                return record["question"]
    raise SystemExit(f"no thematic question in {path}")


# ---------------------------------------------------------------- the record


def index_record_path(root: Path) -> Path:
    return root / config.INDEX_RECORD_NAME


def write_index_record(root: Path, *, wall_clock_s: float, exit_code: int,
                       started_at: str | None = None,
                       finished_at_iso: str | None = None) -> dict:
    finished_at_iso = finished_at_iso or _now()
    counts = tables.counts(root)
    tokens = check_tokens(root, since=started_at)[1]
    cross_check = graphrag_metrics(root, since=started_at, until=finished_at_iso)
    record = {
        "type": "kg_graph_index",
        "graphrag_pin": config.GRAPHRAG_PIN,
        "settings_sha256": config.settings_sha256(),
        "prompts_sha256": config.prompts_sha256(),
        "llm_model": config.LLM_MODEL,
        "embedding_base": config.EMBEDDING_BASE,
        "workspace": str(root),
        "started_at": started_at,
        "finished_at": finished_at_iso,
        "wall_clock_s": wall_clock_s,
        "exit_code": exit_code,
        "chunks_in": len(inputs.chunks()),
        "tables": counts,
        "tokens": tokens,
        "cross_check": cross_check,
    }
    path = index_record_path(root)
    path.write_text(json.dumps(record, **_JSON) + "\n", encoding="utf-8")
    return record


def load_index_record(root: Path) -> dict | None:
    path = index_record_path(root)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def graphrag_metrics(root: Path, *, since: str | None = None, until: str | None = None) -> dict:
    """GraphRAG's own per-process counters, as an independent cross-check on the proxy log.

    The two count different things, and the difference is the interesting part: the proxy logs
    **every attempt**, GraphRAG's `attempted_request_count` counts **logical requests** with
    `retries` beside it. On the 2026-10-16 corpus run that was 2,653 attempts against 2,624
    requests + 31 retries, and the extra attempts are why the proxy's token total sits ~3%
    above the metrics' — two honest numbers, neither a substitute for the other.
    """
    totals: dict[str, float] = {}
    for record in tables.metrics(root, since=since, until=until):
        for key, value in (record.get("metrics") or {}).items():
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + float(value)
    keep = ("attempted_request_count", "failed_response_count", "retries",
            "prompt_tokens", "completion_tokens", "total_tokens")
    return {key: int(totals.get(key, 0)) for key in keep}


# ---------------------------------------------------------------- the gate


def run_gate(root: Path, *, skip_query: bool = False) -> int:
    print(f"\n  kg-graph gate · root {root}\n")
    tables_ok, _counts = check_tables(root)
    textunits_ok, _report = check_textunits(root)
    # The gate reports the *index's* tokens: the window is the run recorded in
    # `kg_graph_index.json`, not everything the proxy has ever seen (which includes the smoke
    # root and every query since).
    record = load_index_record(root)
    window = (record or {}).get("started_at"), (record or {}).get("finished_at")
    tokens_ok, _totals = check_tokens(root, since=window[0], until=window[1])
    if record and record.get("wall_clock_s"):
        print(f"  · index: exit {record['exit_code']} · {record['wall_clock_s']}s wall clock · "
              f"tables {record['tables']}")
    if record and record.get("cross_check"):
        check = record["cross_check"]
        print(f"  · graphrag's own counters: {check['attempted_request_count']:,} requests · "
              f"{check['retries']:,} retries · {check['total_tokens']:,} tokens (logical requests, "
              f"so below the proxy's attempt count)")
    query_ok = True
    if not skip_query:
        query_ok, _record = check_query(root)
    gate = tables_ok and textunits_ok and tokens_ok and query_ok
    print(f"\n  gate: {'GREEN' if gate else 'RED'}\n")
    return 0 if gate else 1
