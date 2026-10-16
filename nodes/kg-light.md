# kg-light — LightRAG's graph, plus the id map

**~7.5 h + the inherited endpoint (~1 h)** · **feeds** `judge` (row R2) · `bridge` (Phase 2's whole input) · **gate** at the bottom · **status** ✅ done — closed 2026-10-16 (`sessions/S07-kg-light.md`)

> **How to use this page.** Open it at the start of this node; close it when the gate is green. What is genuinely new here is that two of the three systems in this project *read* what this node writes: R2 is scored on its output, and Phase 2 is built on its graph (D1). A defect here is not one row's problem. Reading below is what this node needs; the GraphRAG material read early is retention lost.

## Purpose

R2 is the first of the two augmentation methods, measured standalone: LightRAG builds its own index over the shared chunks — entities, relations carrying keywords, and chunk vectors — and answers by dual-level retrieval, where a query is matched against both low-level entities and high-level concepts. It is the row that tests *entity and relation extraction* against `baseline`'s chunks-only dense retrieval.

Its second job is larger than its row. **The delivered system is built on this graph.** D1 pays for one extraction pass instead of two, and LightRAG performs it; Phase 2's `bridge` reads these tables and builds GraphRAG's community layer on top (D2, D4).

## Black box

| | |
|---|---|
| **in** | `parsed/_ingest.jsonl` — the same 369 chunks the other three rows read (D16), each with `chunk_id`, `content`, `file`, `heading`, `refs` |
| **out** | a LightRAG index — entities, relations **with keywords**, chunk vectors — plus the **LightRAG-key ↔ `chunk_id` map** that §5.6 makes load-bearing |
| **writes** | LightRAG's stores (KV + vectors in Postgres, graph in NetworkX) and an id map artifact under `parsed/` (gitignored, regenerable) |
| **feeds** | `judge` (row R2), `bridge` — and through it every thematic answer in Phase 2 |

## Contract

- **One extraction pass** over the shared chunks; no re-chunking, no re-parsing, no second extraction for Phase 2 (D1, D16). If LightRAG cannot ingest our text units without a custom step, that is a stated limitation of the row — **never left silent**, and it goes in the report.
- Relations carry **keywords** — the one field GraphRAG's schema lacks, and the reason D2 puts LightRAG on the extraction side of the bridge.
- The **id map is a map with a gate, never a fuzzy match**: read back from LightRAG's own chunk records after ingest, asserted bijective, any unmapped `source_id` a stop-the-line error (§5.6, README → *Landmines*).
- Graph storage is NetworkX behind `BaseGraphStorage`; Postgres carries KV, vectors and document status (D15). **Never select LightRAG's Postgres graph backend** — it is Apache AGE-based.
- Extraction is incremental by construction; nobody in Phase 1 uses that, and Phase 2 inherits it (D10).

## Setup

| # | what | why |
|---|---|---|
| 1 | LightRAG **pinned at commit `453dce8`** (`uv add "lightrag-hku @ git+https://github.com/HKUDS/LightRAG@453dce8"`) | §6.1. The commit is the citation; `naive_query`'s behaviour was read at this exact revision |
| 2 | the **`/v1/embeddings` endpoint** — the FastAPI adapter over `hyrag/embedding/encoder.py` that `baseline` deliberately did not build | LightRAG reaches embeddings through an OpenAI-compatible binding; see *Inherited from `baseline`* below |
| 3 | DeepSeek as LightRAG's LLM, **`EXTRACT` and `KEYWORD` roles with thinking disabled** | extraction must emit clean structured output. Landmine: DeepSeek's thinking is on by default, `reasoning_effort: "low"` still thinks, and only `thinking: {"type": "disabled"}` turns it off (README → *Landmines*, verified 2026-10-11) |
| 4 | a **`working_dir`** for LightRAG's stores, gitignored | it holds derived data, and its name must not be guessed at twice |
| 5 | Postgres KV + vector backends enabled; graph left on NetworkX | D15. Also confirm the **vector dimension matches 1024** — the same failure the `vector(1024)` column fails loudly on |

## Open questions — settled (the answers and their evidence live in `sessions/S07-kg-light.md`)

**1. The ingestion path — answered: a fourth route, `SDK raw enqueue + a custom chunker`.**
`apipeline_enqueue_documents(input=[body], ids=[stem], file_paths=[file])` with
`chunking_func=our_chunker` injected at construction. It keeps **our chunks** (the callback
returns `_ingest.jsonl` records verbatim, each asserted a substring of the document body),
**our `file_path`**, and **extraction runs** (the pipeline's normal Stage 3). The trade: a
custom chunker is not eligible for source-span sidecar backfill (`docs/ThirdPartyChunker.md`),
so the chunks carry no LightRAG-side page span — harmless here, because the citation chain is
our own map (`chunk_id → occurrences → file, §section, page range`), which is what §5.6 asks
for. Chunk keys come out as `{stem}-{our chunk_id}`: ours, prefixed by the document.

**2. What "a query returns entities, relations and chunks" means — answered.**
`aquery_data` (or `aquery_llm`'s `data` section) returns all three for every mode except
`naive` (chunks only) and `bypass`. **The row is evaluated under `mix`** — local + global +
naive, LightRAG's strongest native mode; `hyrag.kg_light.config.QUERY_MODE` is a flag, not a
constant, so `judge` can ablate it.

**3. Extraction cost — measured, not estimated.** 2,347 LLM calls · 2.59 M prompt + 0.84 M
completion tokens · **648.8 s** wall clock for 12 documents / 369 chunks at extract concurrency
12 — **6.4 calls and 9.3 k tokens per chunk**. That is D1's number on the extraction side; a
second native pass (GraphRAG) buys the same order of magnitude.

**4. When the map is built — after the run, by reading LightRAG's rows back.** Matched to
`_ingest.jsonl` **by content**; written to `parsed/kg_light_idmap.json`; re-derived by `check`.
A map built from what we sent could not notice a storage-side change.

**5. Do `keywords` survive — yes.** All **6,835** edges carry them in the graphml; that is D2's
dependency on LightRAG's schema, verified rather than assumed.

**6.** ~~Where chunking happens~~ — settled by `parse`, and constrained here by question 1.

## How to build it — the shape, not the code

Four stages, and the first one can end the node early if it goes badly:

1. **Configure and smoke-test the stack** — LightRAG's storage backends, the embedding endpoint, the LLM with thinking disabled, vector dimension 1024. A single `insert` of one short document, then a query, before any real ingest.
2. **Ingest the 369 chunks** on the dev slice, with the extraction cost instrumented (calls, tokens, wall clock) — this is a background job whose numbers go in the report.
3. **Read the index back, and build the map.** Every chunk LightRAG holds, resolved to a `chunk_id`; the map asserted bijective; a count of unmatched references, which must be zero. Do not build this from what you *sent* — build it from what LightRAG *stored*, since those are the keys that will come back at query time.
4. **Query** — one question per class through the mode chosen in open question 2 — and log what comes back: entities, relations, chunks, their counts, and what a thematic answer looks like when the retrieval is dual-level rather than flat.

## Inherited from `baseline`

Written down because these would otherwise live only in a session log.

- **The embedding path.** `kg-light` inherits the OpenAI-compatible `/v1/embeddings` adapter that `baseline` deliberately did not build: the encoder ships in-process (`hyrag/embedding/encoder.py`), and the server is a thin wrapper over that same module. **The rule: the adapter contains no encoding logic** — no pooling, no normalization constant, no max length. If any of those appear in the server file, it is a bug.
- **The equality check it owes.** R1's vectors were produced by the module alone. The endpoint owes them a one-command verification on arrival: encode all 369 chunks through both paths and assert identical output. Until that runs, "all four rows share one encoder" is an intention, not a fact.
- **A prediction to falsify.** Measured 2026-10-14 on the slice: the naive baseline retrieves factoid evidence in the top 5 **50/50**, multi-hop in the top 10 **49/50**, and thematic only **20/50** at k=20 — climbing to 48/50 at k=100, so the thematic failure is a **budget** problem rather than a representation problem. R2's local retrieval is expected to inherit the first two; the graph is its only lever on the third, and a flat-lining thematic column here is a finding, not a bug.
- **The measurement harness** already exists: `hyrag/embedding/checks.py` and `hyrag/baseline/checks.py` compute per-class top-k evidence recall with no LLM and no database dependency beyond the store. Reuse it rather than rewriting it.
- **A dash landmine that will bite here too.** The model writes `p. 62-63` where our citations carry an en dash `p. 62–63` (U+2013). Any citation comparison — including LightRAG's `* [n] file_path` reference list resolving back to our provenance — must fold dash variants, or correct citations read as missing (`nodes/baseline.md` → Discoveries).

## Reading (what to extract)

1. **LightRAG at `453dce8`** — `docs/ProgramingWithCore.md` (the insert/query API surface) and `docs/ThirdPartyChunker.md` (what a custom chunker is allowed to return — and note it cannot carry per-chunk metadata). ~45 min. *Extract:* which functions extract and which merely store; that is open question 1.
2. **LightRAG's paper** — `papers/lightrag_hku.pdf` §3 (dual-level retrieval) and §5 (baselines). ~30 min. *Extract:* what the method claims to do that `baseline` does not, in the authors' own words — that is the row's hypothesis.
3. **`PREPLAN.md` §5.6** — the three id spaces and the map. ~20 min. *Extract:* exactly which ids must line up, and where the map is asserted.
4. **`PREPLAN.md` §5.3** — the columns GraphRAG needs, since `bridge` will read these tables. ~15 min. *Extract:* what the extraction must not drop.
5. **Our own `parsed/<stem>/blocks.jsonl` sidecar** (`hyrag/parse/sidecar.py`) against LightRAG's `docs/LightRAGSidecarFormat.md` — our format was built to this spec. ~20 min. *Extract:* which fields LightRAG will actually consume, and which it will discard (page anchors are the ones that matter).

**Not yet:** GraphRAG's community layer, BYOG, `create_community_reports` — that is `bridge` and `kg-graph`. And nothing about RRF or reranking.

## Gate

> a query returns entities, relations and chunks; **and** the id map is bijective, with zero unmapped references

The second half is the one that matters for Phase 2 — a bijective map is the difference between a bridge that works and a bridge that silently merges two documents into one citation. Demonstrate it by **enumerating every `source_id` LightRAG holds and resolving each to a `chunk_id` in `_ingest.jsonl`** — asserted, not spot-checked. A zero is not "probably fine"; it is a count you print.

## Decisions already locked

- **D1/D2** — LightRAG extracts; its graph is what Phase 2 shares. GraphRAG never extracts in the delivered system.
- **D9/D15** — Postgres + pgvector for KV and vectors, NetworkX for the graph, no containers, single worker.
- **D16** — the shared chunk set; **§5.6** — the id map is a map with a gate.
- **§6.1** — LightRAG pinned at commit `453dce8`; both systems point at one embedding service; `uv`, not conda.

## If the gate fails

*(Shelf, after your own diagnosis.)* Likely suspects: the ingestion route silently re-chunked (a `chunk_id` map that is 1:1 in count but wrong in content — compare text, not just the count) · `file_path` normalised to a basename, so `corpus/…/x.pdf` and `x.pdf` both survive as different keys · an unmapped `source_id` produced by *gleanings* rather than by chunking · the vector dimension silently mismatched against the endpoint.

## Discoveries

**Gate: green, 2026-10-16.** Every number below is printed by
`uv run python -m hyrag.kg_light.cli check` (and re-derivable from Postgres + the graphml):

| check | result |
|---|---|
| map | **369 LightRAG keys ↔ 369 our chunks**, bijective, 0 unresolved, 0 `file_path` mismatches |
| `source_id` | 7,443 node + 7,484 edge = **14,927 references (366 distinct) · 0 unmapped** · 0 attributes without one |
| vector columns | 4 × `vector(1024)` (three LightRAG tables + `baseline_chunks`) |
| queries | factoid 72 ent / 138 rel / 20 chunks · multi-hop 29 / 131 / 18 · thematic 37 / 144 / 15 — all non-empty, 0 unresolved keys |

**The index** — 12 documents → 369 chunks → **4,525 entities**, **6,835 relations**, 369 chunk
vectors. Extraction: **2,347 LLM calls, 3.43 M tokens, 648.8 s** wall clock at concurrency 12.

**The endpoint check R1 was owed**: all 369 chunks, through the module and through HTTP, are
**exactly equal** (`max|Δ| 0.000e+00`) in both `float` and `base64` encodings — "all four rows
share one encoder" is now a fact with a command behind it (`hyrag.serving.checks`).

**What this row contributes to the report.**

- *One extraction pass now has a price*: 6.4 calls and 9.3 k tokens per chunk. That is the cost
  side of D1; `kg-graph`'s native run shows what the second pass would have cost.
- *The honest limitation*: LightRAG cannot take pre-chunked text through any documented route
  without trading one of our chunks / our `file_path` / extraction. The route taken is custom
  code we own and is not sidecar-backfill eligible; the row's page provenance therefore comes
  from our map, not from LightRAG. State it in the report, not in a footnote.
- *The map is the deliverable Phase 2 stands on*: 14,927 references resolve, and the resolution
  is a content match against LightRAG's own rows — a hash recomputed on our side would not have
  caught a storage-side change.

**Two LightRAG behaviours that cost a run each and are in none of the docs this page listed.**

1. **It calls LLM functions as `func(prompt, system_prompt=…)`** — the prompt is positional, the
   model is not. A `functools.partial(openai_complete_if_cache, model=…)` dies on the first
   extraction with *"got multiple values for argument 'model'"*; it needs a wrapper that binds
   the model and lets the queue wrapper's kwargs win.
2. **FAILED documents are never auto-resumed.** The auto-resume set is
   PENDING/PROCESSING/PARSING/ANALYZING (`pipeline.py::_AUTO_RESUME_DOC_STATUSES`); a failed
   document needs an explicit retry, and until then a re-run reports "No documents to process"
   while the row sits there. Re-enqueueing known content does not refresh it either — it writes
   a `dup-<hash>` stub that fails and lingers.

**Environment facts worth keeping.** LightRAG's optional extras are not in its default install
— `openai` and `asyncpg` must be added by hand, and its Postgres backend is asyncpg, not
psycopg (which R1 uses). `POSTGRES_PASSWORD` must exist in the environment even for a
trust-auth cluster: the storage check tests membership, not value. File-based storages write
under `working_dir/<workspace>/`, so the graphml is one level deeper than `working_dir`.

**Post-node (2026-10-16):** R2's completion calls were routed through the local proxy
(`hyrag/serving/llm.py`, tagged `kg-light`), so §7.3's cost-per-query is now measurable for
this row the same way it is for R3 — `query.ask` reports `usage` read back from the proxy log
around its own call. Verified on an uncached question (2 calls · 35,303 tokens · 30.9 s).

**Carried into `kg-graph`:** the DeepSeek role split (thinking off for extraction-type work,
on for the query role), the `/v1/embeddings` endpoint (GraphRAG will point at the same one and
its `vector_size` must match 1024), and the table layout — both rows write into the same `hyrag`
database, under **separate workspaces**; `baseline_chunks` is untouched by this node.

## Session log

- 2026-10-14 — page opened (it existed as a contract-and-gate stub since the same day); `baseline` closed and this node made current in `ROADMAP.md`.
- 2026-10-15 → 2026-10-16 — **closed, gate green.** Reconnaissance found the fourth ingestion route; `hyrag/serving/` built the endpoint R1 was owed (369 chunks, exact, both encodings); `hyrag/kg_light/` built, smoke-tested, then run over all 12 documents (2,347 calls · 3.43 M tokens · 648.8 s); the map is bijective and all 14,927 `source_id` references resolve. Two defects and one deviation (GitHub fetch failed mid-transfer, so `pyproject.toml` resolves the pin from a pristine local checkout of `453dce8`): `sessions/S07-kg-light.md`.
