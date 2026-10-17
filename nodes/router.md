# router — one turn plus history, in; one strategy class, out

**~7.5 h** · **feeds** `arms` · **Phase 2's second box** · **gate** at the bottom · **status** 🟢 **closed 2026-10-17 — gate GREEN**

> **How to use this page.** The brief for the next agent, not a design to be taken on faith. §*What the benchmark says* is measured and lives in `results/class_strategy.json` — read it, do not re-derive it. §*Open questions* is what the implementer decides, and one of them (the held-out set) is a decision only the owner of the project can make. Nothing in `Discoveries` exists yet.

## Purpose

Decide **before any retrieval happens** which strategy answers this turn (D5), and turn an elliptical turn into a question that can be retrieved against (D6). Two artifacts, and they are not the same kind of thing:

| artifact | what it is | where it lives |
|---|---|---|
| **the classifier** | `standalone query → {factoid, multi-hop, thematic}` | code: prompt + call, versioned and hashed |
| **the strategy table** | `class → which arms fire, and whether fuse applies` | **data**, read off Phase 1's results, so it can be ablated (D5) |

The router never sees a retrieval score. That is the whole point of D5: a LightRAG `mix` score and a GraphRAG report rating are different quantities on different scales, so "whichever is more confident" is not implementable. The decision is made on the question, from a table chosen by measurement.

## Black box

| | |
|---|---|
| **in** | one user turn · the dialogue history from the checkpointer · `results/class_strategy.json` (the measured per-class ordering, costs and latencies) · the class definitions in `nodes/qset.md`'s class table |
| **out** | the **standalone query** (condensed) · exactly one **class label** · the **strategy** the table assigns to it (`arms` to fire, `fuse` on/off) · a record: both prompts' sha, the label, the strategy, tokens, latency |
| **writes** | `hyrag/router/` (condense, classify, table, cli), the dialogue state, `results/router_*.jsonl` |
| **feeds** | `arms` (the strategy), `answer` (the condensed query), and the report's dialogue walk-through |

## Contract

- **The table is data, not code.** `class → {arms, fuse}` comes from `results/class_strategy.json`; changing a route is a configuration edit, and the record says which version of the table produced which turn.
- **Exactly one label, always.** Three classes, no "unknown", no abstain that reaches `arms`. An ambiguous question gets a deterministic fallback (a rule in the table) and the record notes that the fallback fired — a router that can return nothing is a router with a fourth, undocumented class.
- **The router sees only the question.** No retrieval, no chunk scores, no arm-specific features. Anything else re-introduces the incomparable-score problem D5 exists to avoid.
- **Condense precedes routing** (D6) and sees only turn + history. The condensed query is what `arms` retrieves with, so a condense that drops the entity fails twice.
- **The label is the benchmark's vocabulary, not a new one.** The three classes are `nodes/qset.md`'s definitions, verbatim; redefining them here would make accuracy unmeasurable against the frozen set.
- **The held-out set is frozen in git before the classifier's prompt is written.** The `qset` is frozen at `2c5de79` for exactly this reason and the same logic applies to the router's own eval: a split decided after the prompt is a split fitted to the prompt.
- **Every call goes through the local proxy** (`hyrag/serving/llm.py`, row tag `router`) so the router's cost lands in `parsed/llm_proxy.jsonl` with everything else's, and `thinking` is disabled for the JSON-emitting call.

## Locked 2026-10-17 — the budget the table is chosen under

The project owner's call, and it decides the table before any code is written:

| decision | value | why |
|---|---|---|
| **held-out split** | option (a): the frozen 150 split **120 dev / 30 eval**, 10 eval per class, by `select.systematic`'s own stride rule | zero drafting run, zero answer generation — eval questions are *classified*, not answered (~$0.003 for the lot) |
| **cost envelope for a turn** | **≤ 60 s · ≤ $0.02** | excludes the benchmark's thematic winner by construction (357.8 s · $0.380 native; measured on the bridged index: **280.5 s · $0.45**) |
| **thematic route** | **`kg-light` `mix`** — the mode its row was measured in (51.3 s · $0.0094) | the only arm that fits the envelope; R4's thematic cell therefore costs **$0.19** for 20 answers, or $0 if the delivered row reuses R2's recorded answers. The benchmark's 90% pairwise winner is **not shipped** — that is the finding, stated in the table and in the report |
| **multi-hop route** | both arms, `fuse` on | the roadmap's design; the bridged arm at ~$0.004/query is affordable here |
| **factoid route** | `kg-light` `mix` (4.2 s · $0.0047) | pointwise best and inside the envelope; kg-graph buys 76% pairwise at 27.4 s |
| **§7.4 ablation** | **5 bridged thematic questions**, native answers reused | **measured $1.91** instead of ~$11; native side already on disk and free |

**Phase 2 spend, measured: $3.28** (bridge $1.00 · ablation $1.91 · arms/fuse gates ≈ $0.31 · router $0.01 · answer $0.05). What is left is the judge's R4 addendum, ≈ $1.5–3. The index compute is finished and is not paid again.

## What the benchmark says — measured, `results/class_strategy.json`

This is the input the table is read from. Pairwise (blind, masked, the papers' rubric) versus pointwise, with cost and latency per query:

| class | pairwise winner | pointwise best | cheapest | fastest | latency that comes with the winner |
|---|---|---|---|---|---|
| factoid | **kg-graph 76%** | kg-light 0.803 | baseline $0.0025 | baseline 2.2 s | kg-graph 27.4 s, $0.0026 |
| multi-hop | **kg-graph 65%** | kg-light 0.870 | baseline $0.0032 | baseline 7.2 s | kg-graph 40.7 s, $0.0039 |
| thematic | **kg-graph 90%** | baseline 0.953 | baseline $0.0048 | baseline 18.7 s | **kg-graph 357.8 s, $0.380** |

**The tension the router has to resolve is in the last column.** The judged winner wins every class, and on thematic it costs **79× the baseline and 19× its latency** for a 24,056-character report. The file says what it is: *"A route is a choice under a latency/cost budget; Phase 2's router node owns that choice. This file records the benchmark's ordering and the per-arm cost, not a route."* So the table the router ships must state **the budget it was chosen under** — a route without one is a preference wearing a measurement's clothes.

Phase 2's arms are LightRAG and the **bridged** GraphRAG (no naive arm, no native index). The bridged row's numbers are not R3's: same query path, 932 reports instead of 644, so the thematic cell's rating pass is ~1.4× R3's — roughly **8 minutes** rather than 6.

## Verified facts — do not re-derive

1. **The frozen set is 150 questions, 50 per class**, each with a class label, a gold answer and an evidence target (`qset/questions.jsonl`, commit `2c5de79` — the SHA predates this node, which is what makes accuracy meaningful). The three class definitions are the table in `nodes/qset.md`: *factoid = one fact in one chunk · multi-hop = ≥ 2 facts from ≥ 2 places, or a relation between two named things · thematic = recurring patterns across documents, no single chunk answers it*.
2. **`hyrag/qset/select.py` already defines the benchmark's sample rule** (all factoid + multi-hop, systematic n=20 for thematic) and `select.load()` / `by_class()` / `systematic()` are reusable. It has **no held-out split** — that is this node's decision to make.
3. **The judged results already exist as data**: `results/class_strategy.json` (per class: pairwise winner and win rate, pointwise best, per-arm rubric mean, cost, latency, retrieval hit rate, n, and the `routing_note` above) — rewritten by `hyrag.judge.cli table`, with `results.csv` and `results/head_to_head.csv` beside it.
4. **The four query paths exist and are runnable today**: `hyrag.baseline.cli` (R1), `hyrag.kg_light.cli query` (R2), `hyrag.kg_graph.cli query` (R3 native), `hyrag.kg_graph.bridge.cli` + `graphrag query --root parsed/kg_graph_bridged` (R4's index). The `arms` box does not exist, so a router that needs an arm to be testable is out of order — its tests must run against recorded answers and stubs.
5. **The condense pattern is specified, not invented** (`PREPLAN.md` §2.3): turn 1 passes through, turn 2's *"What about the 737?"* becomes *"What is the maximum operating altitude of the Boeing 737?"*, and without it both the router and the retriever see a question with no attribute in it.
6. **The class → mode mapping is a table in our code, never a decision inside the model** (D5, README → *Decisions*). The model may pick the class; it may not pick the arms.

## Open questions — settle before writing the classifier

1. ~~**What is the held-out set, and where does it come from?**~~ — **settled 2026-10-17: option (a), a 120/30 split of the frozen set**, 10 eval per class by the same stride rule `hyrag.qset.select` uses, derived in code from the frozen file and **frozen before the classifier's prompt is written**. The other options and why they were not taken: drafting new questions is a run plus a labelling pass (no money saved where the money is); judge-labelling measures agreement with DeepSeek rather than with the class definitions.
2. **Classifier mechanics and the latency budget.** A rule pass (interrogative shape, "why did…", "across both manufacturers") is free and instant; an LLM call is ~1–2 s and ~$0.0001. The router sits on **every** turn, and the factoid route's whole answer costs 2.2 s — a 2 s router triples it. Is the rule pass the fast path with the LLM as fallback, or is every turn an LLM call? Whichever it is, it goes in the record and the table.
3. **Ambiguity policy.** A question can be genuinely two classes (*"what recurring shortfalls explain the NTSB's recommendation?"*). Which class wins, and is the rule stated per pair (factoid→multi-hop, multi-hop→thematic)? The fallback must be deterministic and in the table, not in a model's confidence field.
4. **Condense scope.** How much history does it see — the last N turns, or the whole conversation? What does it do when the turn is self-contained (pass through unchanged, or rewrite anyway and risk drift)? Is it one LLM call, and does it smoke-test against the three-turn script below before the classifier is built?
5. **Where the dialogue state lives.** `PREPLAN.md` §2.2 puts it in a LangGraph checkpointer; `answer` also needs it. Does this node own the state, or only its read interface? Decide before writing, because two boxes owning one store is how a rewrite loop appears.
6. **What the record must carry.** Proposed minimum: `turn`, `history_len`, `condensed_query`, `label`, `strategy`, `fallback_fired`, `table_version`, prompt shas, `tokens`, `latency_ms`. The report will want one traced three-turn example, which means the record has to survive across turns.

## Cost and time — measured where it can be, estimated where it cannot

- **The index compute is done and is not paid again.** `bridge` ($1.00) and both Phase 1 indexes exist on disk; nothing in this node rebuilds them.
- **Router calls are cheap**: ~1.5 k prompt (definitions + six dev examples) + ~20 completion tokens per classification ≈ **$0.0002**; 150 questions × 2 (dev + eval) plus a handful of condense turns ≈ **$0.05**, minutes of wall clock. *Estimate — confirm on the first ten calls with a proxy window.*
- **The rest of Phase 2 is not free**, and saying so here keeps this page honest: `arms` regenerates answers (R2's 150 cost **$0.97**), the judge's R4 addendum is a judged pass (the table was **$1.58**), and `fuse` needs a cross-encoder download (~2.3 GB — **7.7 GB free**, recheck before starting). Under the locked budget the whole remainder is **≈ $5–7**: the R4 judged pass $1.5–3, the 5-question ablation sample ~$2.75, everything else cents. The remaining boxes are cheap in *indexing* money, not in *generation* money.
- Time: the roadmap prices this node at **7.5 h**; the engineering is the cost, as it was for the judge.

## Inherited landmines — read the entry before touching the area

- **The `qset` frozen before the router is the ordering claim** (`2c5de79`). Freeze the router's own held-out split in the same spirit: commit it before writing the prompt, or the accuracy number measures the prompt's author.
- **`class_strategy.json` is an ordering, not a route** — its own `routing_note` says so. Shipping it as the table without stating a latency/cost budget is the mistake this node exists to prevent.
- **`arms` does not exist yet.** Test against `results/*.jsonl` answers and a stub arm; never let the router's gate depend on a box that is still `unknown`.
- **GraphRAG's cost is inherited by the route.** A `thematic → kg-graph` table entry means every thematic turn is ~6–8 minutes and ~$0.38. If that is not shippable, the table must say what is, and the report states the trade.
- **DeepSeek needs `thinking: disabled` for JSON**, and the proxy repairs `json_schema` → `json_object` (the report calls were 1,057 of 1,059 on the bridged run). Ask for `json_object` directly rather than relying on the repair for a new call shape.
- **The serving process must be running** (embeddings *and* chat), and **no `uv add` while it runs** — it broke `/v1/embeddings` once with `/health` still green (`sessions/S09-judge.md`).
- **Three class labels, not a spectrum.** Any output format that admits a fourth label or a confidence float invites a later "0.6 means thematic" that no one can ablate.

## Gate

> class accuracy on a held-out set, stated as a number; **and** the 3-turn condense test

Concretely, one command should print:

1. **the split** — which questions are dev and which are eval, with the commit that froze it, and a per-class count;
2. **a confusion matrix and per-class accuracy** on the eval half, as numbers (`factoid n/20 · multi-hop n/20 · thematic n/20`, plus overall), with the classifier's prompt sha and the table version beside them;
3. **the 3-turn condense test** — a scripted dialogue whose turns 2 and 3 are elliptical, printing each turn's `condensed_query`, label and strategy, and asserting three things: the entity from turn 1 survives, the attribute survives, and the label is the intended one. One turn must change class (factoid → multi-hop or thematic), or the test proves only that the router can repeat itself;
4. **the table that was used**, with the budget it was chosen under, and the fallback rule with the count of times it fired.

**The floors this node adopts** (a choice, recorded in `config.py`): overall accuracy ≥ 0.80 and no class below 0.60, on a balanced set where chance is 0.33. Without a floor, "accuracy stated as a number" gates green at chance.

A gate that prints "accuracy 0.87" without the split, the prompt sha and the table is not a gate — the number is unverifiable and the table is unreproducible.

## Decisions already locked

- **D5** — route before retrieval, on the question; fuse by rank if at all.
- **D6** — condense before routing; the router never sees an elliptical turn.
- **One label, three classes**, the qset's vocabulary.
- **The strategy table is data**, read off `results/class_strategy.json`, editable and ablatable.
- **No naive arm** — the naive method is LightRAG's own `naive` mode, one switch inside an existing arm.
- **Corpus questions come from the frozen set or the qset's own drafter**; no ad-hoc questions typed from memory, which cannot be labelled consistently.

## If the gate fails

*(Shelf, after your own diagnosis.)* Likely suspects: an eval question written or chosen after the prompt (the split was not really frozen) · class definitions drifting between the prompt, `nodes/qset.md` and the labelling of the eval set (re-read the class table every 25 questions) · the classifier seeing the gold answer or the evidence target · a condense that rewrites a self-contained turn, so turn 1's label changes · history truncation dropping the entity the turn refers to · the table applied at a different version from the one the record names · accuracy quoted on the dev half.

## Discoveries

**The gate, green, with its numbers.** `uv run python -m hyrag.router.cli check` — 30 held-out questions (10 per class, split frozen by rule before the prompt was written), **overall 0.867**: factoid **1.00** · thematic **1.00** · multi-hop **0.60**; 0 fallbacks, 30 calls, 11,981 prompt + 210 completion tokens, **873 ms per query**, ≈ **$0.002** for the evaluation (13,883 tokens for the whole `check`, the dialogue included). The 3-turn condense test passed every assertion on the first run, and a second `check` reproduced both the matrix and the dialogue (temperature 0, same prompt sha `5ed25370534b`).

**Multi-hop is the boundary, and it is systematic, not sampling noise.** Four eval misses — `mult-065`, `070`, `080`, `095` — all predicted factoid, and the **dev half shows the same rate: 0.62 (15 of 40 misrouted)**. Reading them explains why: they are labelled multi-hop because *answering* them requires connecting evidence from two places, but their surface is a single-fact lookup (*"Which NTSB chairman accompanied the investigative team…"*, *"What does selecting the PA reception knob… cause the CVR to record?"*). **The class is a property of the answer path, not of the question's text**, and D5 forbids the router from seeing the retrieval that would distinguish them — so a text-only classifier has a ceiling here that a better prompt will not lift much. The consequence is bounded: a multi-hop → factoid miss drops **fusion**, not the arm, because kg-light is the pointwise-best row on multi-hop anyway (0.870).

**The prompt was written once and never tuned on the eval misses.** The dev run came after the first eval run, and it was used as evidence about whether the 0.60 was an accident, not as a tuning set; the prompt sha is identical across every run recorded here. That is what makes 0.867 a held-out number rather than a fitted one.

**The table ships a deliberate loss, and that is the router's real decision.** `kg-graph` wins the thematic class 90% pairwise and costs **357.8 s and $0.380** per query natively (measured 2026-10-17: **280.5 s / ≈ $0.45**) against kg-light's **51.3 s / $0.0094**. The envelope is **≤ 60 s / ≤ $0.02**, so the shipped thematic route is kg-light and the benchmark's winner is not deployed. R4's thematic cell therefore costs $0.19 for 20 answers, or $0 if the delivered row reuses R2's recorded ones — and the report states the price of the choice instead of hiding it in a setting.

**The condense test is the dialogue `PREPLAN.md` §2.3 described, and it works.** Turn 2's *"What about the 737?"* becomes *"What's the maximum operating altitude of the 737?"* — entity restored, attribute carried from turn 1 — and turn 3 re-routes to `thematic` rather than repeating the class. Both are string-checked, so the gate does not depend on the judge it is measuring.

**Factoid routing is the one row that does not follow the pairwise winner.** kg-graph wins factoid 76% pairwise — but that is R3's *native* row, and the delivered system runs the **bridged** index, which has no per-class measurement yet (§7.4's ablation is thematic and sampled at five questions). Routing factoid to it would be routing on a proxy, so the table routes to kg-light (pointwise best, hit rate 1.0) until the ablation measures the bridged arm.

## Session log

- 2026-10-17 — **built and closed.** `hyrag/router/{config,model,condense,classify,strategy,bench,cli}.py`; split frozen 120/30 before the prompt; gate green at 0.867 overall (multi-hop 0.60, and 0.62 on dev — the finding above). The budget decision (≤ 60 s / ≤ $0.02, thematic routed to kg-light, ablation sampled at five) was taken by the project owner before any code was written, and is recorded in `strategy.py` beside the table it constrains. Whole node spend ≈ **$0.02** (dev 120 + eval 30 + two checks).
- 2026-10-17 — page opened. The measured table (`results/class_strategy.json`), the frozen set's class labels and the condense example pinned above; the node's first decision is the held-out set, and it is the owner's, not the implementer's.
