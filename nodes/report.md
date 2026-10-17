# report — the written deliverable

**~5 h** · **the last box** · **feeds** an examiner · **gate** at the bottom · **status** 🟢 **closed 2026-10-17 — gate GREEN**

> **How to use this page.** The report is a deliverable, not a journal: `REPORT.md` at the repo root, written for a reader who was not here. This page says what it must contain and how the gate checks it. Nothing in `Discoveries` exists yet.

## Purpose

Turn twenty session files and a set of CSV/JSON artifacts into **one document an examiner can read and re-derive**: what was built, what was measured, what it cost, what the findings are, and where every number comes from.

The report is graded on two things the code cannot supply: whether the claims are the ones the evidence supports, and whether a reader can follow a number back to its source.

## Contract

- **Every number traces.** Each figure in `REPORT.md` names the file it came from (`results/results.csv`, `results/router_record.json`, `parsed/kg_graph_bridged/index_record.json`, a session file). The gate checks that the paths exist and that a list of headline numbers actually appears in those files.
- **Versions are pinned in the report**, not only in `pyproject.toml`: `graphrag==3.3.0`, LightRAG rev `453dce8`, `deepseek-flash`, `bge-m3` at 1024 dimensions, `bge-reranker-base`, and the installed `transformers`/`torch`.
- **Findings are the evidence's, not the author's.** Where the benchmark contradicts the design (the delivered system does not ship the benchmark's thematic winner), the report says so in those words.
- **Limitations are a section, not a footnote.** Thematic n=20, the dev slice, the unjudged R4 row, the reranker's 512-token window, the entities outside the connected component.
- **The report is reproducible**: one section lists the commands, in order, with the measured cost and wall clock beside each.

## Gate

> every number in it traces to `results.csv` or a session file; library versions pinned; agent-assisted draft

Concretely, `uv run python -m hyrag.report.cli check`:

1. every `results/…`, `sessions/…`, `nodes/…`, `parsed/…`, `qset/…` path named in `REPORT.md` exists;
2. the pinned versions in `REPORT.md` match the environment (`graphrag==3.3.0`, `453dce8`, `transformers`, `torch`);
3. every headline number in the report appears in the artifact it cites (a list of `(number, file)` pairs).

## Decisions already locked

- **The deliverable is the report plus the oral**, not a demo (2026-10-08).
- **The judged table is the benchmark's result**; `results.csv` is the only place a benchmark number becomes a result.
- **The Phase 2 addendum (R4's judged row) is not in the report** as a measured number — it is named as the remaining work, with its cost.

## If the gate fails

Likely suspects: a path written from memory (`results/answers.csv` vs `results/results.csv`) · a number copied from a session file's prose instead of the artifact · a version still at a pre-upgrade value.

## Discoveries

**The gate, green.** `uv run python -m hyrag.report.cli check`: **23 named paths exist**, the four pins match (`graphrag==3.3.0`, `453dce8`, `transformers` 5.17.0, `torch` 2.14.1), **12 headline figures** trace to their artifacts, and the spend is **recomputed from the proxy log** — 28,931 calls · 54.2 M in · 17.2 M out · **$15.58** — rather than trusted from the prose.

**§7.4's delta is now measured** (`results/ablation_record.json`, 5 thematic questions, same settings, native answers reused): evidence hit **1.00 on both sides**, citations 222.8 → 174.4, answer length 28,059 → **17,737** chars, latency 353.9 → **237.4** s, cost $0.3665 → **$0.3814** per turn. Bridging keeps the retrieval, shortens the answer by 37%, runs 33% faster and costs 4% more.

**A rounding tolerance is part of the gate, not a loophole.** The report quotes 0.867 and 0.3796 where the artifacts store 0.8667 and 0.379634; a checker without tolerance would force six decimals into a document nobody reads that way. The tolerance is 5e-4 — tight enough that 0.867 cannot match 0.86.

**The cost table is arithmetic, so the gate recomputes it.** Per-row dollars are the same computation the checker runs; it verifies the total and the call count, which means a wrong row cannot sit beside a right total.

**What the report says it does not know.** The judged R4 row is named as remaining work, the ablation is deterministic only, thematic is n=20/10, and 618 entities never reach a community. Those are in §6, not in a footnote.

## Session log

- 2026-10-17 — **written and closed.** `REPORT.md` at the repo root, `hyrag/report/{ablation,checks,cli}.py`, gate green. §7.4 measured on five thematic questions ($1.91). `sessions/S15-report.md`.
- 2026-10-17 — page opened.
