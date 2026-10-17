# bridge — GraphRAG's community layer over LightRAG's graph

**~6 h** · **feeds** `arms` · **Phase 2's first box** · **gate** at the bottom · **status** 🟢 **closed 2026-10-17 — gate GREEN**

> **How to use this page.** The brief for the next agent, not a design to be taken on faith. §*Verified facts* is what was confirmed against `graphrag==3.3.0` and this repo's artifacts on 2026-10-16 — treat it as measured, and re-verify anything it says "verify" about. §*Open questions* was what the implementer decides, now answered with what forced each answer. §*Discoveries* is the outcome, and the upper-case trap in it is the one that will bite a future reader first.

## Purpose

Give the delivered system a **second arm without a second extraction pass** (D1, D2, D4). Phase 1 paid for LightRAG's extraction once and GraphRAG's extraction again — §7.4's ledger: **6.4 calls / 9.3 k tokens per chunk** for LightRAG's pass, and the second pass cost **2,655 calls / 5.94 M tokens / 25.5 min**. The delivered system cannot afford the second pass, so it runs GraphRAG's **community layer only** — communities, reports, vectors — over the graph LightRAG already built. The delta between R3 (native) and R4 (bridged) is the measured price of sharing one graph, and it is the number that turns D1 from an assumption into a result.

## Black box

| | |
|---|---|
| **in** | `parsed/lightrag/hyrag/graph_chunk_entity_relation.graphml` — **4,525 nodes / 6,835 edges**, LightRAG's own store `↔` its fields are in §*Verified facts* 2 · `parsed/kg_light_idmap.json` — **369 ↔ 369 bijective** (§5.6), `source_id` → our chunk id · `parsed/kg_graph/output/{text_units,documents}.parquet` — GraphRAG's own text-unit id space, already mapped **369/369** to our chunks (`parsed/kg_graph_textunit_map.json`) · `hyrag/kg_graph/settings.yaml` + `prompts/` — the community layer's configuration, versioned |
| **out** | `<root>/output/` with `entities`, `relationships`, `communities`, `community_reports` (+ LanceDB vectors), in GraphRAG's schema, plus a run record (pins, settings sha, counts, windowed tokens, wall clock) |
| **writes** | `hyrag/kg_graph/bridge*` (converter, runner, checks), `parsed/kg_graph_bridged/` (gitignored) |
| **feeds** | `arms` (the bridged arm), and `judge`'s Phase 2 addendum — §7.4's native-vs-bridged ablation |

## Contract — what the bridge must be

- **One graph, one extraction pass.** The only source of entities and relationships is LightRAG's graph. Running GraphRAG's extractor "just to fill a column" is the failure this box exists to prevent (D1, D4).
- **GraphRAG's community layer unmodified.** Same `settings.yaml`, same prompts, same `seed` as R3 — otherwise §7.4 compares *search settings* or *prompt wording* instead of indexes, which is the trap the node page for `kg-graph` names.
- **Deterministic.** Run it twice, diff the tables: identical. The only permitted nondeterminism is none — `cluster_graph.seed: 42` is set, and everything upstream of it is a lookup.
- **Provenance is a gate, not a hope.** Every `text_unit_ids` entry on every entity, relationship, community and report maps to one of our 369 chunks; zero dangling. The join is `source_id → chunk_id` through `kg_light_idmap.json` — **a map, never a fuzzy match** (§5.6).
- **Same embedding endpoint** as every other row (`hyrag.serving`, `vector_size: 1024`), through HTTP.
- **The report calls go through the local proxy** (`hyrag/serving/llm.py`) so their tokens land in `parsed/llm_proxy.jsonl` with every other row's, tagged `kg-graph-bridge`.

## Verified facts — do not re-derive (checked 2026-10-16 against `graphrag==3.3.0` and this repo)

**1. BYOG is a workflow subset, and the settings file is the switch.** `graphrag/index/workflows/factory.py`:

```python
workflows = config.workflows or cls.pipelines.get(method, [])
```

A top-level **`workflows:`** list in `settings.yaml` replaces the whole pipeline. The workflows read their inputs from **the root's parquet files** via `context.output_table_provider`, not from in-memory state:

| workflow | reads | notes |
|---|---|---|
| `create_communities` | `entities`, `relationships` | Leiden; needs `cluster_graph.seed` |
| `create_community_reports` | `entities`, `relationships`, `communities` (+ `covariates` if enabled) | the LLM step — one call per community |
| `generate_text_embeddings` | the final tables | writes LanceDB vectors |

So the bridge is: **write the input tables into `<root>/output/`, set `workflows: [create_communities, create_community_reports, generate_text_embeddings]`, run `graphrag index`.** No CLI flag for it — the CLI's `--method` offers `standard | fast | standard-update | fast-update` only.

**2. What the source graph actually contains.** `graphml` keyed by `entity_id` (the name, e.g. `SMOKE`); edges carry `source`/`target` as those names. Node data: `entity_type`, `description`, `source_id`, `file_path`, `created_at`, `truncate`. Edge data: `weight`, `description`, `keywords`, `source_id`, `file_path`, `created_at`, `truncate`. **`source_id` is LightRAG's chunk key** (`<docstem>-sha256:<hash>`) and **996 nodes carry several, joined by the literal `<SEP>`** — split and map each; a single-string join is how a bridge silently loses evidence.

**3. Reuse R3's text units.** `parsed/kg_graph/output/text_units.parquet` and `documents.parquet` are a function of the **chunk set**, not of the graph: 369 rows each, and `kg_graph_textunit_map.json` already maps them 369/369 by content. Copying them keeps one text-unit id space for both indexes, which is what lets a bridged report and a native report cite the same chunks — and it removes the only place the bridge could invent a second id space.

**4. The columns to produce — this is the target, read off R3's own tables:**

| table | columns |
|---|---|
| `entities` | `id`, `human_readable_id`, `title`, `type`, `description`, `text_unit_ids`, `frequency`, `degree` |
| `relationships` | `id`, `human_readable_id`, `source`, `target`, `description`, `weight`, `combined_degree`, `text_unit_ids` |
| `communities` · `community_reports` | produced by the workflows — do not hand-write |

**`finalize_graph` will not run**, so three columns must be computed by the bridge or the reports degrade *silently*: `entities.degree` (incident edges), `relationships.combined_degree` (degree(source) + degree(target)) — the report context sorts by exactly these (`summarize_communities/graph_context/sort_context.py`) — and `entities.frequency` (define it; it does not drive the ordering).

**5. What the bridge saves and what it costs, measured.** The community-report workflow on R3's index was **675 calls · 2.05 M in / 0.87 M out · $0.83**; extraction (which the bridge does **not** pay) was **1,980 calls · 2.32 M in / 0.70 M out · $0.77**. Embeddings are local (~15 of R3's 25.5 minutes). Expect **≈ $0.85 and ~20–25 min per full run**, ×2 for the gate's run-twice-diff, plus the conversion's engineering time.

**6. The query side is not this box's problem — but know its shape.** A thematic `global` query answered with **~466 calls** (`results/kg_graph_answers.jsonl`): **~430 of them are the dynamic-community-selection rating pass** — one call per community report at level 2 (350), each sending the report's **full content** (`dynamic_search_use_summary: false`), with the query model's thinking **on** — and only ~30 calls write the answer. That is why a thematic turn costs 358 s. The knobs exist in `global_search`'s config (`dynamic_search_threshold`, `dynamic_search_keep_parent`, `dynamic_search_num_repeats`, `dynamic_search_use_summary`, `dynamic_search_max_level`) and belong to `arms`/`router`, not here — but the bridged arm **inherits this cost**, so §7.4's ablation should record which of these were used on both sides.

## Open questions — settled 2026-10-17, and what forced each answer

1. **Entity id normalisation — uppercase, by GraphRAG's own rule.** `html.unescape(name).upper().strip()`, exactly `graphrag/graphs/stable_lcc.py::_normalize_name`. It is not a preference: `create_communities` joins the clustered node names back to `entities.title`, so a title whose case differs is dropped with **no log line** (measured: 370 of 4,525 entities reached a community before the fix, 3,771 after — see Discoveries). Uppercasing merges LightRAG's case variants: **136 names in 133 groups**, each group listed in the run record, and `entities + merged == graph nodes` is asserted so no merge can be silent.
2. **`frequency`** = the number of distinct `text_unit_ids` — written in `convert.py`'s docstring, and the definition native R3's own table already uses (mean 1.745 both ways). Only `degree` drives the report's ordering.
3. **Edge direction.** LightRAG's graphml is an undirected `nx.Graph`: 6,835 edges, **0 symmetric pairs, 0 self-loops**. The stored orientation is preserved (the description reads that way), dedupe is on the ordered pair as `finalize_relationships` does, and the **60 pairs that uppercasing makes mirrored** are collapsed to the first occurrence and listed.
4. **Community-report prompts** are `hyrag/kg_graph/prompts/` verbatim; `prompts_sha256 = 5285a86d…` is identical to R3's run record.
5. **Run record** mirrors `parsed/kg_graph/index_record.json` field for field, plus `source_graph` (sha over the graphml), `idmap`, `references`, `merges`, `name_variants`, `coverage`, `cost`, `diff` and `previous_run`.
6. **The checks live in `bridge/checks.py`**, reusing the native text-unit map and GraphRAG's own per-process counters; only the run-twice diff is bridge-specific.

## Inherited landmines — read the entry before touching the area

- **`json_schema` is unavailable on DeepSeek** and the community-report step is the one that needs it: 675 of R3's 2,655 calls were rewritten by `hyrag/serving/llm.py`. The bridge's reports need the same repair — route them through the proxy, never straight to the provider (README → Landmines).
- **Thinking must be off for the report calls.** A reasoning trace arrives before the JSON; `settings.yaml`'s `extract_completion_model` already carries `{"thinking": {"type": "disabled"}}` — copy it, do not re-type it.
- **GraphRAG lives in its own interpreter** (`.venv-graphrag`, `typer` conflict with `docling`); the bridge's Python runs in `.venv` and drives `graphrag index` as a subprocess, exactly as `kg_graph/run.py` does.
- **The serving process must be running** for embeddings *and* the chat route, and **do not `uv add` while it runs** — it broke `/v1/embeddings` on 2026-10-16 with `/health` still green (S09).
- **The settings file is hashed** into the run record; edit a copy in the root, never the versioned original, and record both hashes.
- **`cluster_graph.seed`** must be set or the communities are irreproducible; `max_cluster_size: 10` is R3's, keep it for comparability.

## Gate

> run twice, diff, **identical**; zero dangling `text_unit_ids`; no silent merging of a known distinct entity pair

Concretely, one command should print: the table counts, the diff of two runs (byte-for-byte on `entities`/`relationships`/`communities`/`community_reports`, modulo row order), **0 dangling `text_unit_ids`**, every merge counted and listed, and the report/token totals windowed from the proxy log. If the second run differs, the diff must say *which* table and *which* rows — a gate that says "different" is not a gate.

## Decisions already locked

- **D1 / D2 / D4** — one extraction pass, one graph; GraphRAG runs as a workflow subset over it; nothing of LightRAG's retrieval is mixed into the community layer.
- **D15** — no Neo4j; the graph is LightRAG's NetworkX store, read from its `graphml`.
- **§7.4** — the bridged arm answers with the **same query settings** as R3 (global, dynamic selection, level 2), and gives §7.4 a delta that is about the index.
- **The id join is a map with a gate** (§5.6) — `kg_light_idmap.json`, asserted bijective, unmapped references stop the line.

## If the gate fails

*(Shelf, after your own diagnosis.)* Likely suspects: `finalize_graph` skipped and `degree`/`combined_degree` left at 0 (reports still generate, ordering quietly wrong) · `<SEP>` not split, so multi-source entities lose all but their first chunk (the dangling check then passes while the evidence is thin) · entity-id normalisation merging two titles (the merge count is the tell) · settings edited in place instead of copied, so the run record's hash no longer describes what ran · the second run diffed with `pandas` row order rather than sorted by id · embeddings through a different endpoint than the rows'.

## Discoveries

- **The silent one: GraphRAG uppercases names, and mixed-case titles vanish.** `graphrag/graphs/stable_lcc.py::_normalize_name` runs `html.unescape(name).upper().strip()` over every edge endpoint; `create_communities` then maps the cluster's node names back through `entities.title`. A case mismatch drops the entity and, if the whole community fails, the community row too — **`graphrag index` exits 0 and prints no warning.** Measured on the first full conversion: **370/4,525 entities in a community (8.2%), 121 communities**; native R3's 2,502/2,817 (88.8%) is the number that exposed it. After normalising names GraphRAG's way: **3,771/4,389 (85.9%), 932 communities**. Native R3's titles are 2,817/2,817 uppercase, which is why it never saw this. Anyone who changes the converter's normalisation re-opens the trap.
- **`communities.id` is `uuid4()` per run** (`create_communities.py`), so "run twice, diff, identical" cannot be a byte diff on that column. The gate excludes `id` from `communities` (count printed, artifact **not** rewritten) and compares every other cell — including `community_reports.id`, a sha512 of `full_content`, which is deterministic.
- **Run two is 930 of 932 reports served from GraphRAG's response cache**: 2 proxy calls for 932 communities, all tables identical. The cache key is the request, so the hits are the evidence that both runs sent byte-identical prompts. Re-rolling the LLM would measure DeepSeek's determinism, which is not this box.
- **Coverage is 85.9% and the 618 missing entities are structural**: `use_lcc: True` (GraphRAG's default, R3's setting) clusters only the largest connected component; LightRAG's graph is **568 components with 514 isolates**. Native R3 loses 315/2,817 (11%) the same way, and the setting is left alone because changing it would make §7.4 compare clustering settings. Chunks reachable from reports: **342/369** (native: 338/369).
- **Cost, measured: $1.00** — 1,059 calls · 3.30 M prompt / 1.26 M completion with the cache split included; **$1.25** on the judge's uniform upper-bound basis. The page priced the reports at $0.83; they cost more because the bridged graph is 56% larger — 932 reports against 644.
- **Wall clock got worse, not better: 1,810.8 s against R3's 1,527.2 s.** Embeddings dominate both runs and the entity set grew 56%; the extraction time saved is more than spent again on vectors. Sharing the graph pays in **calls (−60%) and tokens (−23%)**, and the $0.77 extraction pass is not paid at all — not in minutes.
- **The bridged arm inherits a bigger query bill.** Dynamic community selection is one rating call per report, so a bridged thematic turn rates 932 reports against native's 644 — roughly **1.4× R3's 358 s**. That knob belongs to `arms`/`router`, but the bridged arm cannot be priced from R3's numbers.
- **`graph.subgraph(...).copy()` is not deterministic across processes** (hash-ordered internals): the same 150-node smoke subset produced a different edge *orientation* under three `PYTHONHASHSEED`s, which flipped half the relationship ids and failed a run-twice diff for a reason unrelated to the bridge. Only the smoke path builds a subgraph; it now rebuilds the graph by hand in sorted node order.

## Session log

- 2026-10-17 — built, run twice, gate green. `hyrag/kg_graph/bridge/{convert,root,run,checks,cli}.py`; measured above; `sessions/S10-bridge.md` has the debug trail (the upper-case trap, the subgraph hash-order bug, the `<SEP>`/text-unit id-space join).
- 2026-10-16 — page opened. Verified against `graphrag==3.3.0`: BYOG is a `workflows:` list in `settings.yaml` plus the input tables in `<root>/output/`; the community layer reads only parquet, and `finalize_graph` does not run, so degree columns must be written by the converter. Measured what the bridge pays (reports $0.83) and what it saves (extraction $0.77). The source graph's fields and the `<SEP>` multi-source separator are pinned above; R3's text-unit tables are reusable verbatim, which keeps one id space for both indexes.
