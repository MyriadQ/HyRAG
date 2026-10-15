# baseline — naive RAG: dense embeddings → top-k over the shared chunks

**~3 h + one-time setup** · **feeds** `judge` (row R1) · **gate** at the bottom · **status** ✅ done — gate green 2026-10-14, with one recorded caveat (see Gate → the amended wording)

> **How to use this page.** Open it at the start of this node; close it when the gate is green. Input is `parsed/_ingest.jsonl` — 369 unique chunks from the 508-pp dev slice, already carrying `chunk_id`, `file`, `heading` and a ready-made `citation` string. **Setup comes first**: none of the runtimes this row needs exist on this machine yet. The reading below is what *this* node needs; the `kg-*` and `judge` material read early is retention lost.

## Purpose

A benchmark with no control measures nothing. R1 is the answer to *"graph augmentation beats what?"* — the naive RAG both papers compare against, reproduced over our own chunks. It is deliberately weak (dense-only, no lexical arm, no fusion, no reranker — settled 2026-10-11), and the cost of that weakness is stated in the report rather than hidden: a win for the graph rows can always be answered with *"they beat a weak baseline"*, which is the same exposure both papers carry.

It also has a second job. If R1 wins a class, it returns in Phase 2 as that class's router entry (PREPLAN §9) — chosen by measurement, not by preference.

## Black box

| | |
|---|---|
| **in** | `parsed/_ingest.jsonl` — 369 chunks, each with `chunk_id` (`sha256:…`), `content`, `tokens`, `file`, `heading`, `level`, `refs`, and `occurrences[].citation` |
| **out** | `results/baseline_answers.jsonl` — one record per question: answer · citations · ranked evidence ids · raw token usage · latency; plus a per-class **top-k evidence hit rate** |
| **writes** | a pgvector table (one row per chunk), `results/` (gitignored — regenerable) |
| **feeds** | `judge` (row R1), the report's class→strategy table, and `answer`'s citation contract by precedent |

## Contract

- **Dense-only.** One embedding per chunk, cosine top-k, **one** LLM call. No lexical index, no fusion, no reranker — those belong to Phase 2's `fuse`.
- **Standalone.** It does not run as a mode of LightRAG or GraphRAG. The reason is not purity: it is that R1 would then share a framework, an ingest path and an id space with the row it controls for, and §7.1 requires R1–R3 to be measured without each other in the loop.
- All four rows read **the same chunks** (D16) — this row never re-chunks and never re-parses.
- Every answer carries `file, §section, p. N–M` citations (D13), **copied from the retrieved evidence**, never composed by the model.
- Cost and latency are **measured per query**, from the API's own usage numbers — not estimated.
- Reproducible: model, `k`, prompt and endpoint are recorded with the results (§7.5).

## Setup — none of this exists yet

| # | what | why |
|---|---|---|
| 1 | `postgresql@17` + `pgvector 0.8.7`, both Homebrew | the store (D15). Both are bottled — no build from source. **The Homebrew `pgvector` formula only wires into the `postgresql@17` and `postgresql@18` formulas** (stated in pgvector's own README) — so `@17` or `@18`, never `@16`. Keg-only, so the `bin` needs a path or a symlink |
| 2 | a **bge-m3 embedding server** on `127.0.0.1`, OpenAI-compatible (`POST /v1/embeddings`) | one endpoint serves this row, LightRAG and GraphRAG, so the embedding model is a fixed variable (§6.1). Not in the HF cache — and **a full snapshot is 4.6 GB, of which 2.27 GB is a redundant ONNX copy**: fetch with `snapshot_download('BAAI/bge-m3', ignore_patterns=['onnx/*','imgs/*','*.jpg','.DS_Store'])` → ~2.3 GB. The repo ships **no `model.safetensors`**, so loading goes through `torch.load` (which defaults to `weights_only=True` on torch ≥ 2.6) |
| 3 | three Python packages | `psycopg[binary]` (driver) · `pgvector` (the `Vector` type + `register_vector` for psycopg3) · `openai` (one client for both the embedding endpoint and DeepSeek). The server needs its own deps — `FlagEmbedding` (BAAI's own package for bge-m3) plus `fastapi`/`uvicorn` |
| 4 | `.env` | already carries `DEEPSEEK_API_KEY` and `DEEPSEEK_BASE_URL` — the answer call needs nothing new |

```bash
brew install postgresql@17 pgvector
brew services start postgresql@17
brew services list                                          # expect "started"
psql -l                                                     # if this fails, nothing below will work
createdb hyrag
psql hyrag -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql hyrag -c "SELECT extversion FROM pg_extension WHERE extname='vector';"   # expect 0.8.7
```

**No `PATH` juggling needed** — verified on this machine: `brew install postgresql@17` leaves `/opt/homebrew/bin/psql` symlinked to the keg's own 17.11 binary, and there is no conda `psql` to shadow it. Postgres listens on **5432**, which is free; if `brew services list` reports `error` rather than `started`, the log is `/opt/homebrew/var/log/postgresql@17.log`, and `SHOW port;` inside `psql` reports the port actually bound.

**Two traps in the setup.**

- **Port 8000 is occupied** by a stray `python -m http.server` — that port belongs to the *embedding server*, not to Postgres (5432). Pick a free port, bind the embedding server to `127.0.0.1` only, and keep the port in **one** config value: LightRAG and GraphRAG will point at the same endpoint. This corpus is © OEM material under terms that forbid network exposure (README → *Landmines*).
- **Verify the dimension before anything downstream exists.** One `curl` returning a 1024-length array is the whole check. GraphRAG's `vector_size` and the `vector(1024)` column both fail loudly on a mismatch, but only if you check before the first insert.

The repo's convention is one package per box — `hyrag/parse/`, `hyrag/chunker/`, `hyrag/qset/` — one module per concern plus a `cli.py`. `hyrag/baseline/` (with the server next to its client) is the shape that fits; the server is a service, so start it the way `parse` starts Docling — in-process, no container (D15).

## Open questions — settle these **before** writing the loop

1. **`k`.** Not a local choice: §7.2 trap (ii) says unequal evidence budgets make `retrieval_context` incomparable across rows. The naive RAG in the LightRAG paper is 1200-token chunks with vector top-k; LightRAG's own `naive_query` uses **k=20**, GraphRAG's `basic_search` **k=10**. At our mean of **556 tokens/chunk**, k=20 ≈ **11 k tokens** of context — comfortably inside DeepSeek's window. **Recommend k=20 and record it**, for the honest reason: a control row tuned to lose is a straw man, and matching LightRAG's chunk-based default keeps the two chunk-level rows comparable. Whatever you pick, it appears in the report beside the row that used it.
2. **What text gets embedded.** `content` alone (naive, and what the papers describe) or `file + heading + content` (better retrieval, less naive). **Recommend `content` alone** — the row's job is to reproduce the papers' baseline, not to be the best dense retriever we can build — and record the choice.
3. **The 24 chunks under 25 tokens.** Several are bare headings (`# 2. Analysis`, 5 tokens). Dense retrieval will spend top-k slots on them. **Do not filter them out of the index** — that changes the chunk set (D16) and quietly strengthens the row. Expect them in the results and say so in the findings.
4. **Do the 5 multi-occurrence chunks need enumerating?** Verified on the slice: the collapsed duplicates are 5 chunks — `<drawing />`, `# 1. Factual Information`, `# 2. Analysis`, `# 3. Conclusions`, `# Analysis` — each occurring in two documents. The chunker's contract says a citation enumerates every location rather than picking one. For these five, enumerating is noise; **but the dedupe is still load-bearing**: without it LightRAG would merge `# 2. Analysis` from two NTSB reports into one content key with one `file_path`, and a citation would name the wrong report. Decide the rendering rule now (enumerate, or cite the primary and note the collapse) and put it in the prompt.
5. **Where results live.** `results/baseline_answers.jsonl` is regenerable and large; `results.csv` (§7.5) is the committed artifact. Confirm the `.gitignore` line before the first run, not after.
6. **Refusal behaviour.** Does the row answer when the evidence does not contain the answer? A row that never refuses cannot be measured for groundedness, and the judge's `Faithfulness`/`Hallucination` metrics assume the possibility of refusal. Pick the instruction deliberately.

## How to build it — the shape, not the code

Five stages, five decisions, five ways this goes quietly wrong.

**1 · Embed once.** Read `_ingest.jsonl`, send the chosen text field in batches to the local endpoint. Key each vector by the chunk's content hash so a re-run is a no-op — you will re-run this, and re-embedding 369 chunks because the cache key was the row index is the classic waste. bge-m3's window (8192) is far above our 1200-token cap, so **nothing truncates** — but verify that rather than assume it. Note the model needs **no instruction prefix** (unlike `bge-large`), so `query == document` encoding is correct here.

**2 · Store.** One table: `chunk_id` primary key, the metadata columns you want to cite from (`file`, `heading`, `citation`, `page_range`), the `occurrences` as `jsonb`, the text, and `embedding vector(1024)`. Insert idempotently. **Do not build an ANN index at this size** — 369 rows scan exactly in about a millisecond, and HNSW is *approximate*: it can miss the true nearest neighbour, which would fail your own gate for a reason that has nothing to do with the method. If you build one anyway for the experience, raise `hnsw.ef_search` and record it.

**3 · Retrieve.** Embed the question with **the same endpoint and the same model as ingest** — a mismatch here is invisible and costs quality silently. `ORDER BY embedding <=> <query> LIMIT k` returns rank order and distances. Keep the distance: it is the row's only score, it is *not* comparable across rows (§7.6), and it is worth having when a single question behaves oddly.

**4 · Generate.** A system prompt plus numbered evidence blocks — each `[n]` followed by its **citation string and** its text. The instruction must (a) answer only from the evidence, (b) copy the citation strings **verbatim**, never invent a page, (c) refuse when the evidence does not contain the answer. Two references worth 15 minutes each: LlamaIndex's `CITATION_QA_TEMPLATE` (numbered sources plus a worked few-shot) and Azure's `prompts/answer.txt` (citation wording: always cite, never merge). The QUERY role runs with DeepSeek's thinking **on** (§6.1) — so record the API's raw `usage` block verbatim rather than your own arithmetic over it, because reasoning tokens are billed and counting them wrongly corrupts the cost column.

**5 · Record.** One JSON object per question. The judge reads this file; build it to the judge's shape the first time (§7.2: `input`, `actual_output`, `retrieval_context`, `token_cost`, `completion_time`, and **no field naming the system**).

| field | why it exists |
|---|---|
| `question_id`, `class` | joins back to the frozen `qset`; the class is what the results table is per |
| `answer` | the raw output, unedited — the judge scores what the model actually said |
| `citations` | the citation strings the answer used |
| `evidence` | ranked `{chunk_id, citation, distance}` — **ids only**; the judge resolves text from `_ingest.jsonl` |
| `usage` | the API's own token numbers, verbatim |
| `latency_ms` | wall clock around the completion — and say exactly what you timed |
| `model`, `k`, `prompt_sha256` | §7.5: a result that cannot be re-derived is not a result |

**One free check before the judge.** Every citation string in `answer` must appear among its `evidence` citations — anything else was invented. That is a deterministic pre-check for §7.3's citation-resolution metric, needs no LLM, and catches the failure mode this whole node exists to avoid.

## Reading (what to extract)

1. **`papers/lightrag_hku.pdf` §5, "Baselines"** — the Naive RAG paragraph verbatim. ~10 min. *Extract:* the definition your row reproduces, and the fact that their baseline also ran 1200-token chunks, which we already match.
2. **`papers/graphrag_google.pdf`** — search for *"conventional RAG baseline"* and *"SS"*. ~10 min. *Extract:* which comparator is which. The default-prompt experiment compares against **SS**, a map-reduce *source-text summarization* baseline — do not let that be called "the naive RAG baseline" in the report.
3. **`graphrag` v3.3.0, `query/structured_search/basic_search/basic_context.py` + `search.py`** — read only. ~20 min. *Extract:* what a first-party baseline actually does (CSV blob `id|text`, k=10, a 12 000-token budget, one call) and how little of it there is.
4. **LightRAG `453dce8`, `lightrag/operate.py` → `naive_query` and `_get_vector_context`** — read only. ~15 min. *Extract:* k=20, and that its context is JSON lines with a `reference_id` — the same "ids not text" discipline you want in `evidence`.
5. **`pgvector-python/examples/rag/example.py`** (MIT) — ~10 min. *Extract:* the loop in ~12 lines: bulk insert, `ORDER BY embedding <=> %s LIMIT k`. This is the only code in the survey genuinely worth copying, and even here you replace Ollama with our endpoint and add our columns.
6. **LlamaIndex `citation_query_engine.py` prompt templates** + **Azure `prompts/answer.txt`** — ~15 min combined. *Extract:* the numbering and citation-instruction wording. **Do not** take LlamaIndex's engine: it re-splits retrieved nodes at 512 tokens and nulls `start_char_idx`, which would destroy our page provenance.
7. **pgvector's README** — operators, opclasses, index types. ~15 min. *Extract:* `<=>` is cosine distance and the right operator here; `vector(1024)` enforces the dimension at insert.
8. **DeepEval `LLMTestCase` fields** — ~15 min, and only the field list. *Extract:* `retrieval_context`, `token_cost`, `completion_time` — the record you write in stage 5 has to carry exactly these.

**Not yet:** anything for `kg-light`, `kg-graph`, `bridge`, RRF or the reranker. Phase 2's `fuse` (RRF → rerank) is where ranking machinery belongs; reading it now only tempts you into adding it to a row that must not have it.

## Gate

> answers all three classes with page-range citations; **and** the retrieval demonstrably fires — for a spot-checked question per class, the evidence-target chunk is inside the retrieved top-k

Concretely, and do the cheap half over **all 150** rather than three — it is deterministic, costs no LLM calls, and turns the gate from an anecdote into a number:

- [x] **Top-k evidence hit rate**, per class and overall. Each `qset` question already names its `evidence_target.chunk_ids`; retrieve for all 150, and report the rank of the target. *This is the gate's real content* — the spot check is just its most readable instance. **Done 2026-10-14**: factoid `50/50` @5, multi-hop `49/50` @10, thematic `20/50` @20 — the curve is in Discoveries, and the thematic number is the caveat the roadmap's wording still needs amended for.
- [x] **One traced question per class** printed end to end: question → retrieved ids with citations → answer → the citation strings it used. Read them against the PDF. **Done 2026-10-14** — `fact-003`, `mult-050`, `them-122`, all three read against their source pages.
- [x] **Full 150-question run** → `results/baseline_answers.jsonl`, every answer carrying `file, §section, p. N–M`, every citation traceable into `evidence`. **Done 2026-10-14**: 150/150 answered, 150/150 cited, 0 fabricated sources, 0 empty. The file is gitignored — it carries corpus-derived text.
- [x] **Row totals** for the report: tokens in/out, mean latency, and `k` — the numbers `judge`'s cost and latency columns will compare against the other three rows. **Done**: 2,443,698 prompt + 291,268 completion tokens, 10.4 s mean latency, `k = 20`.
- [x] `prompt_sha256` recorded; the run reproducible from the committed prompt. **Done** — `prompt_version: baseline-answer-1`, sha per record.

## Decisions already locked

- **Dense-only** — no lexical arm, no RRF, no reranker (§10 Q8, settled 2026-10-11). The experiment therefore has no lexical arm at all, and the report says so.
- **D16** one chunk set for all four rows; **D13** citations are `file, §section, p. N–M`; **D15** no containers — Homebrew Postgres, no Docker.
- **Standalone, not a framework mode** — decided in this node, 2026-10-12. GraphRAG's `basic_search` is close to this row but forces LanceDB and cites parquet row indices; LightRAG's `naive_query` shares an index with R2 and reranks unless explicitly disabled. Neither is cheaper than the loop, and both weaken the control.
- The citation string is **copied from the record**, never composed by the model.

## If the gate fails

*(Shelf, after your own diagnosis.)* Likely suspects: a question whose evidence target does not actually contain the answer (a `qset` drafting shortcut — the target was verified when written, so this should surface as an outlier, not a pattern) · `k` too small for chunks that average 2.1 sheets wide · heading-only chunks crowding the top-k · an ANN index returning approximate neighbours · a thematic question whose answer is genuinely corpus-global while its target is one chunk — in which case the **question** is the problem, not the retriever, and that is a finding to record rather than tune around. **Do not tune `k` or the prompt to make this class pass.** A control that was tuned until it passed is no longer a control.

## Discoveries

*(Opened 2026-10-12; the recall curve and the answer stage added 2026-10-14. Everything below is measured unless it says *decided*.)*

### The encoder — built and verified 2026-10-14

- **Shipped as a module, verified by its own checks.** `hyrag/embedding/encoder.py` (one function, `encode(texts)`) plus `hyrag/embedding/checks.py`. Eleven checks pass: dimension, unit norm (`1.000000`), determinism, batch invariance (max |Δ| `1.34e-07`), order, truncation, token counts, recall. Run: `uv run python -m hyrag.embedding.checks`.
- **MPS works, and it is worth ~3×.** The full run — 369 chunks *and* 150 questions encoded — takes **2m15s** on `mps`, against a ~6–7 min CPU estimate taken from a 40-chunk sample. `user` time is 5 s, so the wall clock is the accelerator, not the CPU. The device is chosen once and forced with `HYRAG_DEVICE=cpu`.
- **The two tokenizers disagree by 15.4 %, measured over the whole slice.** The model's XLM-R sentencepiece counts the same text at **mean 642 / max 1619** tokens; the chunker's `o200k_base` counts **mean 556 / max 1200** (the cap we chose). Ratio **1.154**, and **zero chunks exceed the model's 8192 window** — nothing truncates, so "no silent truncation" is observed rather than hoped for. Any future claim about context budget must say which of the two counts it means.

### The recall curve — the benchmark's first number (2026-10-14)

Measured before Postgres, before the LLM, before a line of the retrieval loop: every question encoded, ranked by brute-force cosine against all 369 chunks. The metric is the question's **best-ranked** evidence target — the generous reading.

| class | median rank | @1 | @5 | @10 | @20 | @50 | @100 |
|---|---|---|---|---|---|---|---|
| factoid | 1 | 35/50 | 50/50 | 50/50 | 50/50 | 50/50 | 50/50 |
| multi-hop | 1 | 34/50 | 47/50 | 49/50 | 49/50 | 49/50 | 50/50 |
| thematic | 28 | 3/50 | 6/50 | 10/50 | 20/50 | 35/50 | 48/50 |
| **all** | 2 | 72/150 | 103/150 | 109/150 | **119/150** | 134/150 | 148/150 |

- **Two shapes, and the difference between them is the finding.** Factoid and multi-hop **saturate and stop** — perfect by k=5 and k=10, then flat forever; retrieval has no headroom left to gain. Thematic **keeps climbing** all the way to k=100. That is a **budget** problem, not a representation problem: the evidence *is* retrievable, it is simply ranked ~28th rather than 1st. A corpus-wide question has no single best-matching chunk, so its true evidence sits mid-ranking.
- **The naive baseline is excellent exactly where the answer lives in one chunk, and structurally blind to corpus-wide questions** — which is the axis GraphRAG's community reports claim to fix. This is a **prediction made before the other two rows exist**: if `kg-graph` does not beat `baseline` on the thematic class, the interesting failure is in `kg-graph` or in the thematic questions — not in the control.
- **`k = 20` is now an evidenced choice rather than a preference.** It costs nothing against k=5 on factoid and multi-hop (both already saturated), and it is about the largest k that leaves the row a *naive* RAG: at k=100 the prompt would carry ~64 k tokens of context, and the row stops resembling the baseline the papers describe.
- **Two caveats to carry into the report.** (i) The metric takes the question's *best* target; requiring every target would be harsher, and multi-target questions are the norm (36 questions name 6 targets). (ii) **15 of 150 questions have no evidence inside the top 100 at all** — worth reading by hand before the numbers harden into a conclusion.
- **The gate's wording needs a deliberate amendment.** It reads *"for a spot-checked question per class, the evidence-target chunk is inside the retrieved top-k"*. On thematic at k=20 that spot check passes 40 % of the time. The per-class curve over all 150 questions is stronger evidence of *retrieval demonstrably fires* than three chosen questions — but the roadmap text should be amended **visibly, with this reason**, never quietly reinterpreted.

### The answer stage — built and measured 2026-10-14

- **Shipped and run end to end.** `hyrag/baseline/answer.py` plus `uv run python -m hyrag.baseline.cli answer --all`. One `deepseek-flash` call per question, thinking **on** (the QUERY role, §6.1), temperature 0, `k = 20`, prompt version `baseline-answer-1` with its sha in every record. Full run: **26m20s**, **2,443,698** prompt tokens and **291,268** completion tokens. The output file is gitignored — it carries corpus-derived text.
- **The row's numbers, per class** — these are what `judge`'s cost and latency columns compare against the other three rows:

| class | n | latency median | latency max | citations median | output tokens median | evidence in top-20 |
|---|---|---|---|---|---|---|
| factoid | 50 | 1.9 s | 6.9 s | 1 | 222 | 50/50 |
| multi-hop | 50 | 4.7 s | 24.5 s | 2 | 831 | 49/50 |
| thematic | 50 | 18.5 s | 77.7 s | 14 | 3583 | 20/50 |

  Nothing is truncated, nothing is empty, no source is invented: **150/150 answered, 150/150 carrying at least one citation, 0 fabricated documents**. The cost profile is a direct consequence of the recall curve above — a thematic question needs fourteen citations because its evidence is spread across the corpus, and it costs ten times what a factoid question costs for exactly that reason.

- **Bug 1 — the en dash, and it reaches the judge.** The model writes `p. 62-63` where our citation says `p. 62–63`: an ASCII hyphen for the **en dash U+2013** that `format_page_range` emits. A substring check therefore missed a correct citation (`mult-050` looked uncited in the first run). Fixed by folding dash variants and whitespace on **both** sides (`_fold`), with the record keeping *our* canonical string rather than the model's rendering. **This is a `judge` obligation too**: §7.3's citation-resolution metric, implemented by string matching, will read correct citations as unresolved unless it folds the same characters — the same family as the parse node's `U+2212` landmine in AIM page labels.
- **Bug 2 — `max_tokens` caps reasoning *and* answer together, and thinking is on.** At 8000, **three thematic questions** spent the entire budget thinking; **two returned an empty string** and one returned 1497 characters. Raising the ceiling to 16384, the same question finished reasoning in 3352 tokens and produced 7930 characters. A run that reports a mean latency of 10 s while quietly discarding 2 of 150 answers is worse than one that costs more — so the ceiling is now roughly double the largest non-truncated completion observed, and `empty_answers` is a reported total rather than something to notice later.
- **What the first run cost us, and why it was still the cheapest option.** The row was generated twice (25m20s, then 26m20s) because the first output was not a valid artifact. Both bugs were invisible without running it: a `KeyError` would have been kinder. The lesson to carry into `kg-light` and `kg-graph`, whose outputs are far more expensive than 2.4 M tokens: **generate a small sample and read it before committing to the full run.**

- **The embedding endpoint is deferred to `kg-light`; the encoder ships as a module, in-process (decided 2026-10-12).** R1 calls `hyrag/embedding/encoder.py::encode(texts)` directly — no server. When `kg-light` opens, the same module gets a thin FastAPI adapter exposing `POST /v1/embeddings` for LightRAG and GraphRAG. **The rule that keeps the two paths identical: the adapter contains no encoding logic** — no pooling, no normalization constant, no max length. If any of those ever appear in the server file, it is a bug.
  - **Why the endpoint cannot be avoided later.** GraphRAG's CLI reaches a local model only through LiteLLM's `model_provider` + `api_base` (HTTP). Its other local route — registering a custom embedding class via the Model Protocol — is documented as *"not supported with the CLI"*. And LiteLLM's `huggingface` provider is the **hosted HF Inference API** (`HF_TOKEN`), which would post © OEM corpus text to a third party and is disqualified by the corpus licence before it is disqualified technically (README → *Landmines*).
  - **Why "no deployment" does not apply.** `127.0.0.1:PORT` is a local process, not a deployment — the same species as `brew services start postgresql@17`. D15 bans containers, not local services.
  - **Two bookkeeping consequences.** `baseline` drops the ~1 h the endpoint was priced at; `kg-light` inherits it. And **R1's numbers are produced before the endpoint exists**, so the endpoint owes them a one-command equality check when it arrives: encode all 369 chunks both ways and assert identical vectors.

## Session log

- 2026-10-12 — node page written; Postgres 17 + pgvector 0.8.7 installed and verified (`hyrag`, `extversion = 0.8.7`, port 5432); bge-m3 (2.27 GB) in the HF cache; encoder next.
- 2026-10-14 — pgvector ladder run in Postico; encoder written in `scratch/encoder.ipynb`, reviewed, transferred to `hyrag/embedding/`; `checks.py` written; 11/11 checks pass, full run 2m15s on MPS; **recall curve measured** (factoid 100 % @5, multi-hop 98 % @10, thematic 40 % @20 climbing to 96 % @100).
- 2026-10-14 (later) — store written in `scratch/store.ipynb`, reviewed and transferred to `hyrag/baseline/store.py`; `schema.sql` applied; `cli.py` with `ingest` / `search` / `check`; **369 rows**, unit norms to 7.6e-08, the 5 multi-location chunks preserved, `check` 11/11 with the recall curve reproduced *through Postgres*. Then `answer.py`: one `deepseek-flash` call per question, thinking on. **Two bugs found only by running** — the en dash in citations (silently un-cited one correct answer, and it reaches the judge's metric) and `max_tokens` capping reasoning plus answer together (two empty thematic answers). Both fixed; the row regenerated. **150/150 answered and cited, 0 fabricated sources**, 2.44 M prompt + 291 k completion tokens, 10.4 s mean latency, 26m20s. Gate green with the thematic caveat recorded above. Next: the findings are here for `report`; `kg-light` is the following node and inherits the embedding endpoint.
