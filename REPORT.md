# Benchmarking graph-augmented retrieval — and delivering the hybrid

**What this is.** Two published claims about retrieval-augmented generation — *"graphs beat naive
RAG"* (LightRAG) and *"community summaries beat both"* (GraphRAG) — tested on one corpus, one
frozen question set, one blind judge; then a delivered dialogue system that pays for **one**
extraction pass and routes each question class to the strategy the benchmark says wins it.

Every number below names its source. The full debug trail is `sessions/`; the design decisions
are `PREPLAN.md`; the per-box contracts are `nodes/`.

---

## 1. The corpus and the instrument

18 documents · 3,939 pages (`CORPUS.md`, `corpus.sha256`) for the shared corpus; the benchmark
and the delivered system run on the **dev slice** — 11 documents · 508 pages — because a
question set that fits in one sitting was worth more than corpus breadth at this scope.

```
PDFs → anchored blocks → 375 chunks (369 unique, 6 collapsed) → 150 questions (50/50/50)
```

Each chunk carries the block ids it was built from, so a citation is *derived*:
`file.pdf, §section, p. N–M` (`nodes/parse.md` → Discoveries). The question set is frozen at
commit **`2c5de79`**, which predates the router that later consumes it — the ordering the whole
benchmark rests on (`qset/questions.jsonl`).

| class | definition (`nodes/qset.md`) |
|---|---|
| **factoid** | one fact, present in one chunk — a number, a limit, a name, a condition |
| **multi-hop** | ≥ 2 facts from ≥ 2 places, or a relation between two named things |
| **thematic** | recurring patterns or differences across documents; no single chunk answers it |

---

## 2. Phase 1 — three systems, one judged table

Three standalone rows: R1 naive RAG (dense top-k), R2 LightRAG `453dce8` (`mix`), R3 GraphRAG
`3.3.0` (native). Answers judged blind, masked, three replicates, on the papers' rubric
(Comprehensiveness · Diversity · Empowerment), with a gold-anchored **Accuracy** column kept
*outside* the rubric. Source: `results/results.csv`, `results/head_to_head.csv`.

| row | class | n | rubric mean | accuracy | $/query | s/query | evidence hit | citation rate |
|---|---|---|---|---|---|---|---|---|
| R1 baseline | factoid | 50 | 0.7945 | **1.000** | $0.0025 | 2.2 | 1.00 | 1.00 |
| R1 baseline | multi-hop | 50 | 0.8029 | 0.897 | $0.0032 | 7.2 | 0.98 | 1.00 |
| R1 baseline | thematic | 20 | **0.9534** | — | $0.0048 | 18.7 | 0.40 | 1.00 |
| R2 kg-light | factoid | 50 | **0.8033** | **1.000** | $0.0047 | 4.2 | 1.00 | 1.00 |
| R2 kg-light | multi-hop | 50 | **0.8702** | 0.903 | $0.0054 | 10.8 | 0.98 | 1.00 |
| R2 kg-light | thematic | 20 | 0.9422 | — | $0.0094 | 51.3 | 0.40 | 1.00 |
| R3 kg-graph | factoid | 50 | 0.7902 | 0.917 | $0.0026 | 27.4 | 0.84 | 1.00 |
| R3 kg-graph | multi-hop | 50 | 0.8206 | 0.789 | $0.0039 | 40.7 | 0.92 | 0.999 |
| R3 kg-graph | thematic | 20 | 0.9472 | — | **$0.3796** | **357.8** | **1.00** | 1.00 |

**Head-to-head (per-question majority, blind).** Source: `results/head_to_head.csv`,
`results/class_strategy.json`.

- **R3 beats R1 and R2 in every class** — 76% · 65% · 90% against the field, and 82% · 76% · 95%
  against R2 alone. **The papers' result reproduces** on a corpus none of their authors saw.
- **R2 beats R1** 56% (factoid) · 74% (multi-hop) · tie (thematic): LightRAG's *"beats naive
  RAG"* claim reproduces.
- **R2 loses to R3** 18% · 24% · 5%: LightRAG's *"beats GraphRAG"* claim does not.

---

## 3. Phase 2 — one extraction pass, delivered

```
LightRAG's graph (4,525 entities · 6,835 relations)
   ├─ bridge → GraphRAG's community layer → 932 communities · 932 reports
   └─ router → strategy table → one arm, or both arms + RRF + reranker → answer
```

| box | what it is | measured | source |
|---|---|---|---|
| `bridge` | GraphRAG's community layer over LightRAG's graph, as a 3-workflow subset | **932 communities/reports** · 4,389 entities · 6,775 relationships · **$1.00** · **30.2 min** · 1,059 calls · 4.56 M tokens · run-twice **identical** · 0 dangling ids · 342/369 chunks reachable (native: 338) | `parsed/kg_graph_bridged/index_record.json`, `sessions/S10-bridge.md` |
| `router` | condense the turn, classify it, read the strategy table | **0.867** held-out (factoid 1.00 · thematic 1.00 · **multi-hop 0.60**) · 873 ms/query · **$0.02** | `results/router_record.json`, `sessions/S11-router.md` |
| `arms` | one `AnswerRecord` over LightRAG and the bridged index | four gate rows · bridged thematic **280.5 s / 175 canonical citations** · gate **$0.47** | `results/arms_gate.jsonl`, `sessions/S12-arms.md` |
| `fuse` | RRF by rank (`k=60`), then a cross-encoder over a 20-chunk shortlist | both arms in every fused top-10 · reranker moves **14–18/20** positions · **$0.022** | `results/fuse_gate.jsonl`, `sessions/S13-fuse.md` |
| `answer` | one generation over the routed evidence, citations verbatim | **30 held-out questions · 0 invented citations · 0 refusals** · gate **$0.02** | `results/answer_eval.jsonl`, `sessions/S14-answer.md` |

**The delivered row (R4), measured on the router's 30 held-out questions, gold-class routes.**
Source: `results/answer_eval_record.json`.

| class | n | evidence-target hit | citations / answer | refusals | end-to-end latency | $/query |
|---|---|---|---|---|---|---|
| factoid | 10 | **1.00** | 1.7 | 0 | 5.9 s | $0.0054 |
| multi-hop | 10 | **1.00** | 2.8 | 0 | 24.4 s | $0.0090 |
| thematic | 10 | 0.40 | 6.5 | 0 | 34.9 s | $0.0098 |

**§7.4 — native vs bridged index, five thematic questions, same search settings, native answers
reused.** Source: `results/ablation_record.json`.

| | evidence hit | citations | answer chars | latency | $/query | tokens |
|---|---|---|---|---|---|---|
| native R3 (644 reports) | 1.00 | 222.8 | 28,059 | 353.9 s | $0.3665 | 1.25 M |
| bridged (932 reports) | **1.00** | 174.4 | **17,737** | **237.4 s** | $0.3814 | 1.49 M |

Bridging **keeps the retrieval** (5/5 questions), writes **37% shorter** answers, is **33%
faster**, and costs **4% more** per thematic turn.

---

## 4. Costs — measured, not estimated

Every completion call in Phase 2 and most of Phase 1 goes through the local proxy, which records
the API's own `usage`. Source: `parsed/llm_proxy.jsonl`, billed at DeepSeek's off-peak card
(`hyrag/judge/config.py`).

| row | calls | prompt tokens | completion | USD |
|---|---|---|---|---|
| `kg-graph` | 13,915 | 26.5 M | 11.7 M | **$9.49** |
| `kg-graph-bridge` | 5,498 | 11.4 M | 3.8 M | **$3.22** |
| `judge` | 8,852 | 10.1 M | 1.0 M | **$1.62** |
| `kg-light` | 396 | 5.8 M | 0.6 M | **$1.20** |
| `answer` | 40 | 0.3 M | 0.0 M | **$0.05** |
| `router` | 228 | 0.1 M | 0.0 M | **$0.01** |
| **total** | **28,931** | **54.2 M** | **17.2 M** | **$15.58** |

- **Two passes predate the proxy** and are priced from their own records: R2's extraction
  (2,347 calls · 3.43 M tokens · 648.8 s, `parsed/kg_light_extraction.json`) ≈ **$0.89**, and
  R1's 150 answers (the per-query costs in §2 × 120 questions) ≈ **$0.38** → **≈ $16.85 all-in**
  for the project.
- **Per-query costs** are in the Phase 1 table above; the delivered system's are in its row.
- **Index builds**: R2 extraction 648.8 s · R3 1,527 s · bridge 1,810.8 s. Embeddings are local
  and cost $0 in money, but dominate wall clock in all three.

---

## 5. Key findings

1. **The papers' protocol reproduces, and its price is visible.** GraphRAG's row wins every class
   head-to-head (76% · 65% · 90%) on a corpus its authors never saw — at **79× the baseline's cost
   and 19× its latency** on the thematic class ($0.38 and 358 s for a 24,056-character report).

2. **The win is coverage, and coverage is not accuracy.** R3 has the highest thematic evidence
   coverage (100% against 40% for both other rows) and the **lowest accuracy** on the classes
   with a gold answer (0.917 factoid, 0.789 multi-hop against 1.000 and 0.903 for kg-light). The
   augmentation buys breadth; the report should not confuse that with being right more often.

3. **The rubric saturates, and the benchmark says so.** 94% of Comprehensiveness scores are
   exactly 1.0 — a bounded scale cannot express "covers more". That is why the paired comparison
   carries the claim and the pointwise matrix is the per-criterion view.

4. **The ranking is a rubric choice, measured rather than argued.** A fourth criterion that prices
   padding inverts every class (R1 82% · 76% · 50%). The control was run, reported as a
   sensitivity variant, and kept out of the table (`results/judge_pairwise.jsonl`).

5. **Sharing one extraction pass is cheaper in tokens, not in time.** The bridged index costs
   **$1.00** and saves the second extraction pass ($0.77) — but the full build takes **1,811 s
   against R3's 1,527 s**: embeddings dominate both runs and the bridged graph holds 56% more
   entities (4,389 vs 2,817). Calls fall **60%**, tokens **23%**.

6. **GraphRAG silently uppercases entity names, and mixed-case input loses most of the graph.**
   `stable_lcc` normalises every edge endpoint with `html.unescape(name).upper().strip()`, and
   `create_communities` joins the result back to `entities.title`. The first bridged build
   covered **370 of 4,525 entities (8%)**, exit 0, no warning; after matching the normalisation,
   **3,771 of 4,389 (86%)**. 136 merged names in 133 groups are listed in the run record.

7. **The delivered system does not ship the benchmark's thematic winner, on purpose.** kg-graph
   wins thematic 90% pairwise and costs **280.5 s / $0.45** per turn on the bridged index; the
   latency budget is **60 s / $0.02**. Thematic routes to LightRAG (**51.3 s / $0.0094**), and the
   report carries the price of the choice instead of hiding it in a setting.

8. **Routing is cheap and accurate except at one boundary.** The classifier is **0.867** on a
   held-out split for **$0.02**, with the whole error concentrated in multi-hop→factoid
   (**0.60**, and **0.62** on the dev half). Those questions read like single-fact lookups and
   are labelled multi-hop because *answering* them needs two places: the class is a property of
   the answer path, and a router that is forbidden from seeing retrieval (D5) cannot see it.

9. **The delivered answers hold the citation contract.** 30 held-out questions, **0 invented
   citations, 0 refusals**, every used citation verified against the original PDF (word coverage
   1.00 on the gate's traced examples). Evidence-target retrieval is **1.00** on factoid and
   multi-hop; the thematic cell keeps LightRAG's **0.40** — the same weakness the benchmark
   found, inherited honestly rather than papered over.

10. **Bridging the community layer is a fair trade for the thematic class.** Same retrieval on
    5/5 questions, answers **37% shorter**, **33% faster**, **4% costlier**. The judged quality of
    the bridged arm — the one thing that would settle whether shorter is worse — is the remaining
    Phase 2 addendum.

---

## 6. Limitations

- **Thematic is sampled**: n=20 for the judged rows, n=10 for the delivered row. Those cells are
  descriptions with intervals, not significance claims (`nodes/judge.md`).
- **The dev slice, not the full corpus**: 11 of 18 documents. The thematic class draws on fewer
  cross-document cases than the corpus could supply.
- **R4 has no judged row.** The delivered system is measured on retrieval, citations, cost and
  latency; its answer *quality* is not in `results.csv`. Running it needs an R4 loader and
  ≈ $1.5–3 of judging.
- **The §7.4 ablation is deterministic only** — evidence hit, citations, length, cost. It does
  not say which of the two answers a judge would prefer.
- **The reranker truncates at 512 tokens**, and 8–17 of 20 shortlisted chunks exceed it. The
  record flags every truncated chunk; a longer-window model is a different model.
- **618 of 4,389 entities never reach a community** — `use_lcc: True` keeps the largest connected
  component, and LightRAG's graph is 568 components with 514 isolates. Native R3 loses 315 of
  2,817 the same way. The setting was kept for comparability.
- **The delivered thematic route is not the best available answer**, only the best one inside the
  latency budget (finding 7).

---

## 7. Reproduce

All commands are `uv run python -m …` from the repository root, and the serving process
(`hyrag.serving.cli serve`, port 8001) must be running for anything that embeds or completes.

```
# shared input
hyrag.parse.cli parse --all · check --all          # PDFs → anchored blocks
hyrag.chunker.cli chunk --all · check --all · cite <stem> --index N --verify
hyrag.qset.cli freeze                              # commit 2c5de79

# Phase 1
hyrag.baseline.cli ingest · answer --all           # R1
hyrag.kg_light.cli ingest · map · answer --eval-set# R2
hyrag.kg_graph.cli inputs · index · answer --eval-set   # R3 (needs .venv-graphrag)
hyrag.judge.cli run · table · check                # the judged table

# Phase 2
hyrag.kg_graph.bridge.cli prepare · index · rerun · check --against …/run1
hyrag.router.cli check                             # split frozen in code
hyrag.arms.cli check
hyrag.fuse.cli check
hyrag.answer.cli check · eval
hyrag.report.cli check                             # this document
```

Measured: Phase 1 ≈ **$13.6** of model calls (R3 $9.49 · judge $1.62 · R2 $1.20 + $0.89
extraction · R1 $0.38), Phase 2 ≈ **$3.3** (bridge $1.00 · ablation $1.91 · arms/fuse gates
$0.31 · router $0.01 · answer $0.05), whole project ≈ **$16.85**.

---

## 8. Pins and environment

| what | pin | source |
|---|---|---|
| GraphRAG | `graphrag==3.3.0` (own interpreter, `.venv-graphrag`) | `envs/graphrag/requirements.txt` |
| LightRAG | rev `453dce8` | `pyproject.toml` |
| completion model | `deepseek-flash`, thinking disabled for extraction/JSON | `parsed/llm_proxy.jsonl` |
| embeddings | `bge-m3`, 1024 dims, local service | `hyrag/serving/embeddings.py` |
| reranker | `BAAI/bge-reranker-base`, 512-token window, CPU | `hyrag/fuse/rerank.py` |
| python | 3.13 · `torch` 2.14.1 · `transformers` 5.17.0 | this environment |
| database | Postgres 17 + pgvector 0.8.7, NetworkX for the graph | `README.md` → Environment |

**Agent-assisted draft.** The code, the runs and this document were produced with an AI coding
assistant driving the repository; every figure was produced by the commands above and is
traceable to the artifact named beside it. The session files in `sessions/` are the working
record, including the dead ends.
