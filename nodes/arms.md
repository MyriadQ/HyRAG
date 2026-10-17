# arms — one interface over two retrieval systems, one record shape

**~7.5 h** · **feeds** `fuse`, `answer` · **Phase 2's third box** · **gate** at the bottom · **status** 🟢 **closed 2026-10-17 — gate GREEN**

> **How to use this page.** The brief for the next agent, not a design to be taken on faith. §*Verified facts* is what exists in the repo today and was re-checked while opening the page. §*Open questions* is what the implementer decides. Nothing in `Discoveries` exists yet.

## Purpose

Turn the two retrieval systems into **two arms with one shape**. The router says which strategy a turn uses; `fuse` needs to merge evidence from both; `answer` needs to cite from either. None of that is possible while LightRAG returns a `(result, data, metadata)` triple and GraphRAG returns a CLI stdout block with `[Data: …]` markers. `arms` is the adapter layer — and the place where **the delivered system's per-query cost and latency become record fields** instead of something a later node has to re-derive.

It is deliberately not a router and not a fuser: an arm is handed a question and a class, it answers, and it says what it retrieved and what it cost. No arm sees the other.

## Black box

| | |
|---|---|
| **in** | a question · its class (the router's label) · the arm's own configuration: LightRAG's `mix` (its measured row mode) and the bridged root at `parsed/kg_graph_bridged` with §7.4's settings (global, dynamic selection, level 2) |
| **out** | one `AnswerRecord` per (question, arm): answer · citations at the arm's own granularity · evidence chunk ids · tokens · calls · latency · method · unresolved references |
| **writes** | `hyrag/arms/` (the two adapters, the runner, the gate), `results/arms_<arm>.jsonl` (gitignored) |
| **feeds** | `fuse` (the two arms' evidence and ranks), `answer` (citations + text), `judge`'s R4 and §7.4 ablation (the records) |

## Contract — what an arm must be

- **One record shape, two implementations.** Same fields for both arms; a field an arm cannot fill stays `None`, never a fabricated zero (`judge/records.py` made the same decision for the benchmark).
- **The arm answers in its own configuration.** LightRAG runs the mode its row was scored under (`mix`, 150/150 records); the bridged arm runs §7.4's settings — `global` with dynamic community selection at level 2 for thematic, `local` for factoid/multi-hop — because an ablation that changes two things measures neither.
- **Citations at the arm's own granularity, never invented.** LightRAG emits `[n] filename`; a page range cannot be recovered from it and is not manufactured. The bridged arm emits `[Data: …]` markers that `kg_graph.resolve` walks to `file, §section, p. N–M`. The record says which unit it carries.
- **Evidence is our chunk ids, always.** LightRAG's chunk keys resolve through the §5.6 map; GraphRAG's text units through the bridge's own map. An arm that cannot map an id to our space leaves it unresolved and counts it.
- **Tokens and latency are the run's own.** Tokens window `parsed/llm_proxy.jsonl` around the call, per row tag (`kg-light`, `kg-graph-bridge`); latency is the wall clock of the call. Nothing is estimated from a previous run.
- **No arm caches answers.** LightRAG's answer cache is off (`enable_llm_cache=False`) — a cached answer records `calls: 0` and a $0.00 cost for work that was paid for. The bridge's *index* cache is irrelevant here; its report cache is not used at query time.
- **The arms are independent of the router and of each other.** They take a class, not a strategy; a record that mentions `fuse` or a strategy table is a leak of a decision that belongs upstream.

## Verified facts — do not re-derive

1. **LightRAG's query path exists and is measured.** `hyrag/kg_light/query.py::ask` returns the answer, the retrieved entities/relations/chunks and the keyword split in one call (`aquery_llm`), with the proxy window taken around it; `resolve()` maps LightRAG's chunk keys to our chunk ids through `kg_map`. R2's 150 records are on disk: `mode: mix` for all, mean latency **4.2 s** (factoid) · **10.8 s** (multi-hop) · **50.6 s** (thematic), and its citations are filenames only (`hyrag/judge/citations.py::resolve_r2` states the unit).
2. **The bridged root is built and its query path is the same as R3's.** `parsed/kg_graph_bridged` holds 932 communities/reports over 4,389 entities, and `hyrag.kg_graph.run.query(root, …)` drives `graphrag query --root <bridged>` exactly as it drives the native root. `hyrag.kg_graph.resolve.resolve(answer, root)` needs only the root — the text-unit map it uses is shared, so the same `[Data: …]` → citation chain works on both indexes.
3. **The bridged arm's cost is not R3's.** Its thematic turn rates **932** reports through dynamic selection instead of 644 — ≈ **1.4× R3's 357.8 s / $0.380** — an estimate that the arms gate later measured at **280.5 s / $0.45**; the estimate multiplied by the 932 total communities, but dynamic selection rates level-2 communities only (`sessions/S12-arms.md`).
4. **The router's table is data and this box consumes it, not edits it.** `hyrag/router/strategy.py`: factoid → `kg-light`; thematic → `kg-light`; multi-hop → both arms, fuse. The table's budget is ≤ 60 s / ≤ $0.02 per turn.
5. **`hyrag/kg_light/bench.py` and `hyrag/kg_graph/bench.py` already run both rows over the frozen set** with one record per question, resume, and per-question token windows. The adapters should reuse their call paths, not re-implement them; the new part is the unified record and the gate.

## Open questions — settle before writing the adapters

1. **Does an arm answer, or only retrieve?** Both systems generate their answer inside the query call (LightRAG's `aquery_llm`, GraphRAG's global map-reduce). Splitting generation out would double the calls and would not match the benchmark's rows. Decision to state: the arm returns the system's own answer **and** its evidence; `answer` later decides whether to use that text or synthesise over fused evidence.
2. **What is "ranked evidence" when GraphRAG does not rank?** `PREPLAN.md` §7.3: GraphRAG's global search returns a map-reduced answer with truncated context and **no scores**. LightRAG returns round-robin entities/relations/chunks with no comparable score either. So the record carries evidence **ids** and, where the system provides an order, that order — never a synthetic score. `fuse` will need to know which arms can supply a ranked list; say so in the record.
3. **Which method per class for the bridged arm?** R3's benchmark mapping is thematic → `global`, factoid/multi-hop → `local` (`kg_graph/bench.py::METHODS`). Keep it: the delivered arm must be the row that was measured, or §7.4 measures a different system.
4. **One process or two?** LightRAG needs its in-process instance and a live Postgres; GraphRAG is a subprocess per question. The runner should own the difference — one CLI, two backends — so a caller never imports either system.
5. **Where do the gate's questions come from?** The router's eval half is off-limits (it is the held-out set). Use dev ids, one per class, fixed in the gate.

## Cost and time — measured where it can be

- **The gate** was measured, not estimated: **$0.47 and ~6 min** for four rows — kg-light factoid 4.0 s / $0.004 and thematic 54.9 s / $0.011; bridged multi-hop 10.1 s / $0.002 and thematic **280.5 s / $0.450**. The bridged thematic call is the whole bill.
- **The 5-question ablation sample** is therefore ≈ **$2.25** and ~24 min, not the ~$2.75 estimated when it was authorised — the estimate was conservative in the right direction.
- **A full R4 answer run** (120 eval questions) is not this box's work: it belongs to `answer`, once `fuse` exists.

## Inherited landmines — read the entry before touching the area

- **No `uv add` while the serving process runs** — it broke `/v1/embeddings` once with `/health` still green (`sessions/S09-judge.md`).
- **LightRAG's answer cache makes a cost column lie.** `enable_llm_cache=False` in every scored path; the extraction cache is a different cache and stays.
- **GraphRAG's CLI is not an API.** The answer is the text after the last log line (`kg_graph/checks._answer_from_stdout`); a canned refusal and an empty answer are both failures and must be recorded as such, not as text.
- **Dynamic community selection must be on for the bridged global arm** — it is off by default and silently degrades global search to a flat top-k over reports.
- **The bridged arm's settings tag is `kg-graph-bridge`**; window the proxy log with that row or the tokens land in R3's bucket.
- **The router's held-out eval ids are not a test set for this box.** Use dev questions; the eval half stays untouched until the report.

## Gate

> each arm answers its class; every arm emits answers, citations, evidence ids, tokens and latency from one command

Concretely, one command should print, per arm and class, a row with:

1. **status** — a non-empty answer, not a canned refusal;
2. **citations** — at least one, at the arm's stated unit, with the count of unresolved references;
3. **evidence ids** — at least one our-chunk id, and the number that failed to map (must be zero);
4. **tokens and calls** — from the proxy window, non-zero;
5. **latency** — measured, and in the same row so the cost/latency numbers in the report have a source;
6. **method** — the exact configuration that produced the answer (`mix`, `global` + dynamic selection + level 2, `local`), because a row without it is not reproducible.

Green means every arm's every routed class row has all six. A gate that prints an answer but not its cost is not a gate for this system.

## Decisions already locked

- **No naive arm** — the naive method is LightRAG's `naive` mode (README → Decisions, 2026-10-16).
- **The bridged arm uses §7.4's settings**, identical to R3's, or the ablation compares settings instead of indexes.
- **Citations are the arm's own claim**, never a page the arm did not have.
- **One record shape**, the judge's `AnswerRecord` fields, so R4 needs no new adapter.

## If the gate fails

*(Shelf, after your own diagnosis.)* Likely suspects: LightRAG's instance built with the cache on (cost fields read 0) · the proxy window tagged with the wrong row (`kg-graph` instead of `kg-graph-bridge`) · the bridged root queried without `--dynamic-community-selection` · `_answer_from_stdout` returning the log tail as the answer · a chunk key that does not resolve because the map was rebuilt after the query · the gate question taken from the router's eval half.

## Discoveries

**The gate, green, on the first run — four rows, six fields each.** `uv run python -m hyrag.arms.cli check`:

| arm | question | class | method | answer | citations | evidence | tokens (in/out) | calls | latency |
|---|---|---|---|---|---|---|---|---|---|
| kg-light | fact-001 | factoid | `mix` | 154 chars | 1 file | 20, 0 unresolved | 25,965 / 260 | 2 | 4.0 s |
| kg-light | them-101 | thematic | `mix` | 6,168 chars | 4 files | 15, 0 unresolved | 30,274 / 9,954 | 2 | 54.9 s |
| bridged | mult-051 | multi-hop | `local` | 1,155 chars | 16 canonical | 16, 0 unresolved | 10,745 / 579 | 1 | 10.1 s |
| bridged | them-101 | thematic | `global`+dynamic+level 2 | 17,588 chars | **175 canonical** | 175, 0 unresolved | 1,362,408 / 409,688 | 800 | 280.5 s |

**The bridged thematic turn is cheaper than the bridge node estimated, and the reason matters.** The estimate was ~500 s / ~$0.55 (1.4× R3's 357.8 s, from 932 reports against 644). Measured: **280.5 s and ≈ $0.45** — *faster* than R3's native turn. The dynamic-selection pass rates one report **per community at level 2**, and the bridged hierarchy's level-2 count is not the 932 total; the total community count is the wrong multiplier. The delivered system's budget decision does not change (280 s is still 4.7× the 60 s envelope), but the ablation sample is now priced at ≈ $2.25, and §7.4's "native vs bridged" delta has a real cost per question on both sides.

**The shipped thematic route reproduces its benchmark cell outside the benchmark harness.** kg-light thematic: **54.9 s / $0.011** here against the judge's 50.6 s / $0.0094 mean over 50. That is the number the router's table was chosen on, and it survives being called from a different process.

**Fusion is affordable on the class that needs it.** The bridged arm's `local` search answers multi-hop in **10–14 s for one call** and returns 16 canonical page-range citations with zero unresolved — cheap enough that running both arms on every multi-hop turn costs cents, which is what makes `fuse` a real option rather than a budget problem.

**The two arms differ in more than cost, and the record says so.** kg-light returns its chunks in retrieval order (`ranked: True`); the bridged global search returns a map-reduced answer and an unordered report set (`ranked: False`). `fuse` can build a ranked list from the first and not the second, which is exactly the asymmetry `PREPLAN.md` §7.3 predicted and the reason the thematic class stays route-only.

## Session log

- 2026-10-17 — **built and closed.** `hyrag/arms/{record,lightrag,bridged,runner,checks,cli}.py`; gate green on the first run at **$0.47 / ~6 min** for four rows. The measured bridged thematic turn (280.5 s, ≈ $0.45, 175 canonical citations) corrected the bridge node's ~500 s estimate and re-priced the ablation sample at ≈ $2.25. `sessions/S12-arms.md` has the record.
- 2026-10-17 — page opened. Both query paths and the record shapes pinned above; the gate priced at ≈ $0.57, dominated by one bridged thematic answer.
