"""kg_graph/run.py — driving GraphRAG's own CLI, and keeping its output where we can read it.

The row is a subprocess on purpose (see `config.py`): `graphrag index` and `graphrag query`
in `.venv-graphrag`, with the environment built in `config.child_env()`. Two consequences
worth naming:

* **The gate can say "its own CLI completes"** because it literally does — no Python API of
  ours is in the loop, and nothing of ours is imported into GraphRAG's process.
* **Everything we know about a run we learn from what it wrote** — the parquet tables, the
  metrics JSONL, the exit code. That is the same habit `kg-light` used for the id map, and it
  is why this module returns exit codes and not opinions.

Index output is streamed to a log file as well as captured, because the run is long and the
log is evidence: `parsed/kg_graph/logs/index.stdout.log`.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from hyrag.kg_graph import config


def graphrag(args: list[str], root: Path, *, log_path: Path | None = None,
             timeout: int | None = None) -> subprocess.CompletedProcess:
    """Run `graphrag <args…> --root <root>`, capturing stdout/stderr (and logging them)."""
    command = [str(config.GRAPHRAG_BIN), *args, "--root", str(root)]
    if not config.GRAPHRAG_BIN.is_file():
        raise SystemExit(
            f"no GraphRAG interpreter at {config.GRAPHRAG_BIN}\n"
            f"  uv venv --python 3.13 {config.GRAPH_INTERPRETER}\n"
            f"  uv pip install --python {config.GRAPH_INTERPRETER}/bin/python "
            f"-r {config.REQUIREMENTS}"
        )
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=config.child_env(),
        timeout=timeout,
    )
    elapsed = round(time.perf_counter() - started, 1)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n$ {' '.join(command)}   ({elapsed}s, exit {completed.returncode})\n")
            handle.write(completed.stdout)
            if completed.stderr:
                handle.write("\n--- stderr ---\n" + completed.stderr)
    return completed


def index(root: Path, *, dry_run: bool = False, timeout: int | None = None,
          log_path: Path | None = None) -> int:
    """`graphrag index` — the whole native pipeline, one process."""
    args = ["index", "--method", "standard"]
    if dry_run:
        args.append("--dry-run")
    completed = graphrag(args, root, log_path=log_path, timeout=timeout)
    if completed.returncode != 0 and not dry_run:
        tail = (completed.stdout or "")[-2000:]
        raise SystemExit(f"graphrag index failed (exit {completed.returncode}):\n{tail}")
    return completed.returncode


def query(root: Path, question: str, *, method: str = "global",
          community_level: int | None = None, dynamic_selection: bool | None = None,
          log_path: Path | None = None) -> str:
    """One query through GraphRAG's CLI; returns its stdout (the answer is the last block).

    `dynamic_selection` defaults to `config.DYNAMIC_SELECTION` and `community_level` to
    `config.COMMUNITY_LEVEL` — the CLI's own defaults are *not* the row's configuration (D12).
    """
    community_level = config.COMMUNITY_LEVEL if community_level is None else community_level
    dynamic_selection = config.DYNAMIC_SELECTION if dynamic_selection is None else dynamic_selection
    args = [
        "query", question,
        "--method", method,
        "--community-level", str(community_level),
        "--dynamic-community-selection" if dynamic_selection else "--no-dynamic-community-selection",
    ]
    completed = graphrag(args, root, log_path=log_path)
    if completed.returncode != 0:
        raise SystemExit(f"graphrag query failed (exit {completed.returncode}):\n{completed.stdout[-2000:]}")
    return completed.stdout
