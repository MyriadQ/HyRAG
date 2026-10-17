"""judge/config.py — the harness's constants: rows, rates, paths, and the protocol's shape.

Everything a reader needs to know *what was compared* lives here, and nothing that decides
*how* — the criteria are in `prompts/criteria.py`, the loops in `runner.py`. The numbers in
this file are either decisions recorded in `nodes/judge.md` or facts pinned by a row's own
node page; where a fact could drift (a pin, a rate), the drift is a bug, not a preference.

The four rows and where their answers live
------------------------------------------

| row | label | answers | citation unit it can actually support |
|---|---|---|---|
| R1 | `baseline` | `results/baseline_answers.jsonl` | `file, §section, p. N–M` (canonical, from the parse provenance) |
| R2 | `kg-light` | `results/kg_light_answers.jsonl` | **file only** — LightRAG emits `[n] filename` references and no page |
| R3 | `kg-graph` | `results/kg_graph_answers.jsonl` | `file, §section, p. N–M`, via `resolve.py` over the native tables |
| R4 | the delivered hybrid | *Phase 2 — the harness runs without it* | — |

R2's granularity is a property of the row, not a bug in the harness: the wrapper resolves
what the row emitted (a filename) and refuses to invent the page. It is reported with the
asymmetry stated, because a citation-resolution column that silently compares file-level
against page-level would be a lie told by a column heading.

Cost
----

The rate card is DeepSeek's off-peak list: **$0.15 per M input tokens (cache miss), $0.003
per M (cache hit), $0.60 per M output** — the figures `serving/llm.py` records and the four
independent money figures in `nodes/judge.md` all resolve to. The table reports cost as the
**upper bound** (`in × miss + out × output`), uniformly for every row: the alternative is
computing one row's bill with its cache split and another's without, which would turn a
comparison of systems into a comparison of caching luck. The observed cache-hit share is
reported separately.
"""

from __future__ import annotations

from pathlib import Path

from hyrag.parse.config import PARSED, REPO
from hyrag.qset import select

# ---------------------------------------------------------------- paths

JUDGE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = JUDGE_DIR / "prompts"
RESULTS = REPO / "results"
SCORES_LOG = RESULTS / "judge_scores.jsonl"        # one record per (item, criterion, replicate)
# The headline comparison uses **the papers' three criteria** (decided 2026-10-16). The
# control variant — same answers, same seed, plus a Directness criterion that prices padding —
# is kept beside it as a sensitivity check, never as the table's number.
PAIRWISE_LOG = RESULTS / "judge_pairwise_paper3.jsonl"        # headline: the papers' rubric
PAIRWISE_LOG_CONTROL = RESULTS / "judge_pairwise.jsonl"       # sensitivity: + padding control
PAIRWISE_LOG_PAIRS = RESULTS / "judge_pairwise_pairs.jsonl"   # two-way head-to-heads, the papers' claim
BLINDING_LOG = RESULTS / "judge_blinding.jsonl"    # the "could the judge tell?" control
CALLS_LOG = RESULTS / "judge_calls.jsonl"          # the judge's own tokens, per call
CSV_PATH = RESULTS / "results.csv"                 # the deliverable
PINS_PATH = RESULTS / "judge_pins.json"            # what the table was produced from
PROMPTS_COPY = RESULTS / "judge_prompts.json"      # the instantiated criteria, beside the table

# ---------------------------------------------------------------- the rows

ROWS = {
    "R1": {"label": "baseline", "answers": RESULTS / "baseline_answers.jsonl",
           "citation_unit": "file, §section, p. N–M", "row_tag": "baseline"},
    "R2": {"label": "kg-light", "answers": RESULTS / "kg_light_answers.jsonl",
           "citation_unit": "file", "row_tag": "kg-light"},
    "R3": {"label": "kg-graph", "answers": RESULTS / "kg_graph_answers.jsonl",
           "citation_unit": "file, §section, p. N–M", "row_tag": "kg-graph"},
}
ROW_ORDER = ("R1", "R2", "R3")

# ---------------------------------------------------------------- the run's shape

REPLICATES = 3          # §7.2 — the judge is stochastic; one replicate is an anecdote
SEED = 20261016         # shuffling is seeded per (question, replicate), so it re-derives
CONCURRENCY = 8         # in-flight judge calls; the endpoint is local, DeepSeek is not
MAX_TOKENS = 2048       # a score + a paragraph of reasoning; thinking is off
TEMPERATURE = 0.0       # the judge should not invent variance the rows did not cause
BLINDING_SAMPLE = 5     # questions per class, from the evaluation set

# ---------------------------------------------------------------- money

RATES = {"input_miss": 0.15, "input_hit": 0.003, "output": 0.60}  # $ per 1M tokens


def cost_usd(tokens_in: int, tokens_out: int) -> float:
    """The conservative bill: every input token billed at the cache-miss rate."""
    return round(tokens_in / 1e6 * RATES["input_miss"] + tokens_out / 1e6 * RATES["output"], 6)


def evaluation_set() -> list[dict]:
    """The questions the table covers — `qset.select`, so the sample has one definition."""
    return select.evaluation_set()


def questions_by_id() -> dict[str, dict]:
    return {record["id"]: record for record in select.load()}


CHUNKS = PARSED / "_ingest.jsonl"  # the 369-chunk provenance map every row's citations fold into
