"""llm_log.py — the proxy's call log, and the windows read from it.

`hyrag/serving/llm.py` writes one record per chat completion it forwards: model, status,
tokens, latency, and the row that asked (from the `X-Hyrag-Row` header). Rows read it back to
report **their own** cost — and they read it as a *window* (a marker taken before their call,
the records after), because the file is append-only and shared by every row and every run.

Why a separate module rather than a function on the proxy: the rows must not import the
serving app to count their tokens (the proxy pulls in FastAPI and an HTTP client; a row's
query path needs neither). The log is the interface; this is its reader.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from hyrag.parse.config import PARSED

LOG_PATH = Path(os.environ.get("HYRAG_LLM_LOG", PARSED / "llm_proxy.jsonl"))


def append(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_marker() -> int:
    """How many records the log holds right now — a marker for `usage_since`."""
    if not LOG_PATH.is_file():
        return 0
    with LOG_PATH.open("rb") as handle:
        return sum(1 for _ in handle)


def records_since(marker: int) -> list[dict]:
    """The records written after `marker`, in order."""
    if not LOG_PATH.is_file():
        return []
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[marker:] if line.strip()]


def usage_since(marker: int, row: str | None = None) -> dict:
    """Tokens and calls recorded after `marker`, optionally only for one row tag."""
    records = records_since(marker)
    if row is not None:
        records = [record for record in records if record.get("row") == row]
    return {
        "calls": len(records),
        "prompt_tokens": sum(int(record.get("prompt_tokens") or 0) for record in records),
        "completion_tokens": sum(int(record.get("completion_tokens") or 0) for record in records),
        "total_tokens": sum(int(record.get("total_tokens") or 0) for record in records),
        "errors": sum(1 for record in records if int(record.get("status") or 0) >= 400),
    }
