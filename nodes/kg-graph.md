# kg-graph — GraphRAG, run natively

**~7.5 h** · **feeds** `judge` (row R3) · the ablation's native side (§7.4) · **gate** at the bottom · **status** ✅ done — closed 2026-10-16 (`sessions/S08-kg-graph.md`)

> **How to use this page.** Open it at the start of this node; close it when the gate is green. This is the **critical path** of Phase 1: R1 and R2 can be judged without it; R3 and the whole native-vs-bridged ablation cannot. What it produces is deliberately *not* the delivered system's index — D4 keeps the two apart, and §7.4 measures the difference.

## Purpose

R3 is the second augmentation method, measured standalone: GraphRAG runs **its own pipeline** over the shared chunks — its own extraction, its own Leiden communities, one report per community — and answers thematic questions from a hierarchy of community summaries instead of a flat top-k. It is the row that tests *community summarisation* against `baseline`'s chunks-only dense retrieval and `kg-light`'s entity/relation retrieval.

Its second job is the ablation's native side. Phase 2 does not run GraphRAG natively: `bridge` builds the same layer from LightRAG's graph (BYOG, D2a), and §7.4 compares the two global searches over the same questions. **R3 is the "what would the second extraction pass have bought us" measurement** — the number that decides whether D1 (one extraction pass, one shared graph) was a good trade.

## Black box

| | |
|---|---|
| **in** | the same 369 chunks (`parsed/_ingest.jsonl`, D16) — one text file per chunk, so GraphRAG's chunker has nothing to re-decide |
| **out** | GraphRAG's own tables: `documents` · `text_units` · `entities` · `relationships` · `communities` · `community_reports` (parquet + LanceDB vectors), and a `content`-bearing report per community |
| **writes** | `parsed/kg_graph/` (gitignored, regenerable): `input/`, `output/`, `cache/`, `logs/` |
| **feeds** | `judge` (row R3), and §7.4's native-vs-bridged comparison |

## Contract

- **Native means native.** GraphRAG's own extraction prompts, its own entity/relation schema, its own clustering, its own reports. The only custom step is the input: our chunks arrive as 369 one-chunk text files, so D16 holds without asking GraphRAG to honour anything.
- **The config is an artifact.** The settings that ran are versioned in `hyrag/kg_graph/settings.yaml` and copied into the root at index time; the run's numbers go in `parsed/kg_graph_index.json`, beside the settings' hash.
- **One completion model per job, mirroring `kg-light`'s roles**: extraction and community reports run with DeepSeek's **thinking disabled** (structured output must not start with a reasoning trace), the query role keeps it **on**. Two model ids in one settings file.
- **Everything GraphRAG writes is its own** — no BYOG tables, no LightRAG ids. The bridge is Phase 2's business; this node must not touch it (D4).
- **`graphrag==3.3.0`, in its own environment.** It cannot share an environment with `docling` (they require disjoint `typer` ranges), so it lives in `.venv-graphrag` and is driven by **its own CLI**, which is also what the gate asks about. The pin is recorded in `envs/graphrag/requirements.txt`.

## Setup — what the smoke run must prove before the corpus run

| # | what | why |
|---|---|---|
| 1 | `.venv-graphrag` with `graphrag==3.3.0` | the pin; its own env because of the `typer` conflict with `docling` |
| 2 | one text file per chunk, `chunking.size` above our 1200-token cap, `overlap 0` | 369 text units in, 369 out — D16, verified by content, not by count |
| 3 | `completion_models`: `deepseek/deepseek-flash` with `api_base` = the `.env` endpoint; extraction model carries `call_args.extra_body = {"thinking": {"type": "disabled"}}` | the same landmine `kg-light` hit: DeepSeek thinks by default, and the reasoning trace arrives *before* the JSON |
| 4 | `embedding_models`: `openai/bge-m3` pointed at `http://127.0.0.1:8001/v1`; `vector_store.vector_size: 1024` | §6.1 — one embedding service for every row. GraphRAG fails fast on a dimension mismatch, so this is also a check on the endpoint |
| 5 | `cluster_graph.seed` fixed | Gemeinschaft detection is stochastic; an unseeded run cannot be reproduced (§7.5) |
| 6 | `snapshots.graphml: true`, `reporting` to `logs/` | the graph artifact and the run's counts, both needed by the gate |
| 7 | dynamic community selection **on** for the global search | it is off by default and the default degrades to flat top-k over reports — the thing the community layer exists to avoid (D12). The same setting will run over the bridged index in Phase 2, so the ablation compares indexes, not search settings |

## Open questions — settled (evidence in `sessions/S08-kg-graph.md`)

1. **Exact chunks** — yes: 369 text units, matched to our chunks **by content**, filename join cross-checked, 0 problems.
2. **The id map** — `text_units.document_id` (singular) → `documents.title` (our `<sha256>.txt`) → `chunk_id`; written to `parsed/kg_graph_textunit_map.json`.
3. **Cost** — 2,653 calls · 5.94 M tokens · 1,527.2 s. The other half of D1's ledger, beside R2's 2,347 calls / 3.43 M / 648.8 s.
4. **Which search** — `global`, level 2, dynamic selection on (settled by §7.4's need for the same search over both indexes).
5. **Communities** — 644 across five levels (26 / 166 / 350 / 100 / 2); Phase 2's bridged layer should land in the same order of magnitude, and a 5× gap is a finding.

1. **Does GraphRAG's chunker return exactly our chunks?** `size` must clear our 1200-token cap and `overlap` must be 0, or a 369-chunk input silently becomes some other number of text units. Verify by **content**, per text unit, not by count.
2. **How do document ids map to our chunk ids?** The input loader names documents from the filename; text units carry `document_ids`. The map has to be deterministic and read back from GraphRAG's own `documents.parquet`/`text_units.parquet` — the §5.6 habit, applied to a system that has no idea our ids exist.
3. **What does the run actually cost?** Token counts live in GraphRAG's reporting artifacts; the gate wants them logged. This is the other half of D1's ledger (R2's extraction was 2,347 calls / 3.43 M tokens / 648.8 s).
4. **Which search is the row evaluated under?** Global (thematic class) is settled by §7.4; whether R3 also answers factoid/multi-hop — where GraphRAG's `basic`/`local` searches are its native answer — is a `judge` question. The row's own gate asks only for a thematic global search from the native tables.
5. **How many communities and reports, and how big?** The count is the ablation's backdrop: Phase 2's bridged layer should land in the same order of magnitude, and a 5× difference is a finding.

## How to build it — the shape, not the code

1. **Input** — write the 369 chunks to `parsed/kg_graph/input/<sha256>.txt` plus a manifest (`file → chunk_id`), directly from the ingest set.
2. **Config** — copy the versioned `settings.yaml` into the root, then `graphrag index --dry-run` to let GraphRAG validate it before any LLM call.
3. **Smoke** — index ~10 chunks in a scratch root, read the tables back, and check question 1 and 2 by content. S06's lesson: sample first, read it, then commit to the full run.
4. **Index** — the full 369-chunk run, in the background; it is the second extraction pass this project pays for, so its wall clock and tokens are report numbers.
5. **Query + counts** — one thematic question through `graphrag query --method global` with dynamic selection, and every table counted from the parquet.

## Inherited from `kg-light`

- **The embedding service** (`uv run python -m hyrag.serving.cli serve`) and its equality check; GraphRAG points at the same endpoint, and `vector_size: 1024` is the contract.
- **The DeepSeek role split**: thinking off for anything that must emit JSON, on for the query.
- **The measurement habit**: read the numbers out of the system's own artifacts, never from what we sent it.

## Gate

> its own CLI completes over the corpus; its global search answers a thematic question from its native tables; entity, relation, community and token counts logged

"From its native tables" is the load-bearing phrase: the answer must come out of `community_reports.parquet` and the LanceDB vectors GraphRAG built, with no LightRAG id anywhere in the path — that index belongs to Phase 2's `bridge`.

## Decisions already locked

- **D4** — native here, workflow subset in the delivered system. Two configurations, never mixed.
- **D2a** — the bridge is a documented BYOG configuration, not a custom pipeline; nothing in this node may pre-empt it.
- **D12** — dynamic community selection is on for the global search; the default is flat top-k and looks identical in the output.
- **§6.1** — `graphrag==3.3.0`, pinned; both systems point at one embedding service.
- **§7.5** — the settings file is versioned and its hash recorded with the run's numbers.

## If the gate fails

*(Shelf, after your own diagnosis.)* Likely suspects: the chunker re-split (count is right, content is not — compare text, not counts) · a filename-derived document id that collides across documents · the extraction model failing JSON parsing because thinking came back on · `vector_size` silently mismatched against the endpoint · a global search that returned a map-reduced answer with no report rows behind it (dynamic selection off, or `--community-level` pointing at a level with no reports).

## Discoveries

**Gate: green, 2026-10-16.** All numbers printed by `uv run python -m hyrag.kg_graph.cli check`
and recorded in `parsed/kg_graph_index.json`:

| check | result |
|---|---|
| its own CLI completes | `graphrag index` **exit 0 · 1,527.2 s (25.5 min)** |
| text units = our chunks | **369 stored · 369 matched by content · 0 problems** — D16 holds without asking GraphRAG to honour anything |
| tables | 369 documents · **2,817 entities · 5,895 relationships · 644 communities · 644 community reports** |
| tokens | **2,653 LLM calls · 4.37 M prompt + 1.57 M completion = 5,942,969 tokens · 0 errors** |
| global search from native tables | 26,266-char answer, **276.5 s**, level 2, dynamic selection on, no LightRAG id in the path |

**The hierarchy** (Leiden, seed 42): levels 0–4 hold 26 / 166 / 350 / 100 / 2 communities — a
genuine multi-level structure, which is what makes the level-2 query a hierarchical read
rather than a flat one.

**D1's ledger is now complete.** A second extraction pass over the same 369 chunks cost
**5.94 M tokens and 25.5 minutes** (R2's single pass: 3.43 M and 10.8 min). Per chunk:
R3 7.2 calls / 16.1 k tokens against R2's 6.4 / 9.3 k. §7.4's ablation now measures quality
against *that* price, not against an estimate — and the answer is the reason the delivered
system pays for one graph instead of two.

**What the row looks like next to R2, and why it matters to the judge.**

| | R2 `kg-light` | R3 `kg-graph` |
|---|---|---|
| entities / relations | 4,525 / 6,835 | 2,817 / 5,895 |
| extra structure | — (dual-level retrieval) | 644 communities, one report each |
| thematic query | 66.5 s, evidence lists (entities + relations + chunks) | 276.5 s, a **26 k-character map-reduced report** |

The length gap is a finding, not a bug: GraphRAG's global search synthesises a document;
the other rows return an answer over ranked evidence. **The judge's `Directness` control is
what keeps that difference from being read as quality** — and R3 is the row that makes the
control non-optional.

**Three defects that only a smoke run could surface, and did.**

1. **DeepSeek has no `json_schema`.** GraphRAG's community reports pass a Pydantic model as
   `response_format`; litellm forwards it as `json_schema`; DeepSeek answers *"This
   response_format type is unavailable now"* — 675 calls failed. litellm will not drop it
   (`response_format` is listed as supported for the provider), so the repair is ours:
   `hyrag/serving/llm.py` rewrites it to `json_object`, the mode the prompt already assumes.
2. **`text_units.document_id` is singular in 3.3.0.** Reading `document_ids` silently
   produced 369 "unmapped document" problems while the content join was perfect.
3. **A check must compare against the root it checks** — the smoke root holds 8 chunks, not
   the corpus's 369. It now reads the root's own manifest.

**Two environment facts that outlive this node.**

- `graphrag==3.3.0` and `docling` require **disjoint `typer` ranges** (`>=0.27` vs `<0.27`):
  no single interpreter holds both, so R3 runs in `.venv-graphrag` and is driven by its CLI
  (`envs/graphrag/requirements.txt` records the pin). A row driven through a subprocess is
  also a row that cannot accidentally share our process state.
- **The row reaches one local service for everything**: embeddings (`/v1/embeddings`) and now
  completions (`/v1/chat/completions`), which is where the token accounting comes from. R2
  still talks to DeepSeek directly; pointing it at the same proxy would give the two rows one
  measuring stick.

## Session log

- 2026-10-16 — page opened; environment conflict resolved (GraphRAG and `docling` require disjoint `typer` ranges → its own venv, driven by its CLI); `graphrag init` template read, config schema inspected (`ModelConfig.call_args` carries the thinking switch; `vector_store.vector_size` is the dimension contract, `cluster_graph.seed` the reproducibility knob).
- 2026-10-16 — **closed, gate green.** Smoke-first found the `json_schema` failure (repaired in our own `/v1/chat/completions` proxy), a wrong column name, and a check bound to the wrong corpus size. Full run: exit 0 · 25.5 min · 2,653 calls · 5.94 M tokens → 2,817 entities / 5,895 relations / 644 communities / 644 reports, 369 text units matched by content, one thematic global query answered in 26 k characters.
