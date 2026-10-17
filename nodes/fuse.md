# fuse — RRF across the two arms, then a cross-encoder rerank

**~5 h** · **feeds** `answer` · **Phase 2's fourth box** · **gate** at the bottom · **status** 🟢 **closed 2026-10-17 — gate GREEN**

> **How to use this page.** The brief for the next agent, not a design to be taken on faith. §*Verified facts* was checked against the repo and the model on disk while opening the page — including the download, which is the risky part and is done. §*Open questions* is what the implementer decides. Nothing in `Discoveries` exists yet.

## Purpose

The multi-hop class is the one where both arms retrieve different things: LightRAG returns entity/relation neighbourhoods with their source chunks, the bridged GraphRAG returns entity-centred context with page-range citations. The benchmark says neither arm dominates (`kg-light` pointwise 0.870, `kg-graph` 65% pairwise), and the roadmap's design says **both arms fire and their evidence is merged by rank** (D5) — then a cross-encoder re-scores the shortlist, because RRF is a *rank* rule and cannot tell two chunks at rank 3 apart.

The order is not negotiable: **reranker after fusion, never before** (README → Landmines). The fused shortlist is the reranker's universe, so RRF's `k` and the shortlist size set its recall ceiling, and the report says so.

What this box does **not** do: generate an answer. It produces ranked evidence with provenance; `answer` writes the turn.

## Black box

| | |
|---|---|
| **in** | a multi-hop question · two `AnswerRecord`s from `arms` (kg-light and the bridged arm) · the chunk texts from `parsed/_ingest.jsonl` · the cross-encoder on local disk |
| **out** | one fused evidence list: chunk id · RRF score and its per-arm ranks · rerank score · canonical citation · file · chunk text (truncated for scoring only) — plus the diagnostics the gate needs |
| **writes** | `hyrag/fuse/` (rrf, rerank, combine, checks, cli), `results/fuse_*.jsonl` (gitignored) |
| **feeds** | `answer` (the ranked shortlist and citations), `judge`'s R4 row (the evidence behind each answer) |

## Contract

- **RRF over rank, never over scores.** `score(c) = Σ_arm 1/(k + rank_arm(c))`, `k = 60`, from the arms' own orderings. A vector cosine and a LightRAG `mix` score are not comparable (D5), and inventing a normalisation is how "the more confident system wins" comes back in through the window.
- **The bridged arm's order is citation order, not relevance.** `kg_graph.resolve` returns evidence in the order the answer's `[Data: …]` markers mention it. That is a real order and a weak prior; the record says which arm supplied which rank, and the reranker — not RRF — is what actually decides the final order.
- **The shortlist is bounded before the reranker.** RRF is computed over the full union; only the top `shortlist` reach the cross-encoder, because the cross-encoder is O(n) model calls and the fused tail is noise. The bound is a parameter in the record.
- **The reranker re-scores the fused shortlist and reorders it.** It never adds chunks, never drops a chunk for scoring below a threshold (a threshold would be a second, unstated recall cut), and never sees a chunk the arms did not retrieve.
- **Every fused chunk resolves to `file, §section, p. N–M`**, derived from `_ingest.jsonl` — never composed. A chunk whose citation cannot be derived is a stop-the-line error, not a row with an empty page range.
- **Fusion is for multi-hop only.** The thematic class stays route-only (GraphRAG's global search returns a map-reduced answer, not a ranked evidence list) and factoid is single-arm. A fusion call on a non-multi-hop question is a caller bug and raises.
- **Deterministic given the arms' records.** No sampling, no temperature, CPU inference; two runs over the same two records produce the same list.

## Verified facts — do not re-derive

1. **The two arms emit the same record, and the multi-hop evidence is real.** `hyrag/arms` gate (2026-10-17): kg-light multi-hop returns 15–20 chunk ids in retrieval order (`ranked: True`); the bridged `local` search returns 16 chunk ids with 16 canonical citations in 10–14 s and one call (`ranked: False`). Both carry `evidence_chunk_ids` in **our chunk-id space**, so the fusion key needs no mapping.
2. **The reranker is on disk and working.** `BAAI/bge-reranker-base` (XLM-RoBERTa sequence classification, 1.1 GB) at `models/reranker/bge-reranker-base/`, downloaded through **`hf-mirror.com`** because `huggingface.co` is unreachable from this machine (HTTP 000 on both IPv4 and IPv6; the mirror returns 200). Loaded with `transformers` 5.17.0 — no new dependency — and scored a three-passage sanity batch correctly (relevant **+6.48**, irrelevant **−10.20 / −9.25**) in 1.9 s on CPU. **Disk was 4.4 GB free before the download and 3.1 GB after**; that is why it is `base` and not `bge-reranker-v2-m3` (2.3 GB), and the report states the substitution.
3. **`_ingest.jsonl` is the citation source.** 369 chunks, each with `content`, `file`, `occurrences[0].citation` = `file, §section, p. N–M`. The judge's `ChunkIndex` reads the same file; the fused record carries the citation string, so `answer` never re-derives it.
4. **The arms' cost is small enough that fusion is free to run.** One multi-hop question through both arms ≈ **$0.01** (kg-light $0.005 + bridged $0.004); reranking 20 chunks is ~2–4 s on CPU. The gate's three questions cost ≈ **$0.03** in total.
5. **The `answer` box does not exist yet**, so the fused record's consumer is the judge's R4 row and `fuse`'s own gate. Do not couple to a future API.

## Open questions — settle before writing the reranker

1. **Does the reranker run in-process or behind the serving app?** In-process is the choice here: the embedding service holds MPS, and a second process on the same device makes everything slower (README → Landmines). CPU inference over 20 shortlist chunks is seconds. Say it in the module.
2. **Truncation.** `bge-reranker-base` takes 512 tokens; our chunks run to ~1,200 tokens. Truncate to 512 for scoring and record `truncated: true` on those chunks — a rerank score that never saw the chunk's tail is still a score, but the record should not pretend otherwise.
3. **`k` and shortlist size.** `k = 60` is the RRF paper's default and is not tuned here; the shortlist is 20. Both are fields in the record, so a later ablation can move them without rewriting the box.
4. **What happens when one arm returns nothing** (an empty answer, a refusal)? RRF over one list is a valid ranking; the gate requires both arms **on the gate questions**, and the record marks a missing arm rather than silently weighting the other. An arm that returns no evidence is a caller-visible event, not a dropped term.
5. **Tie-breaks and float determinism.** RRF scores tie exactly when two chunks share ranks; break by chunk id so the order is stable, and sort the reranked list by `(-score, chunk_id)` for the same reason.
6. **Does fusion need the arms' answers at all?** No — only their evidence and the question. The answers are recorded for the judge's R4 row but do not enter the fused list; a summary of them belongs to `answer`, not here.

## Cost and time

- **Gate**: three dev multi-hop questions × (both arms + rerank) ≈ **$0.03** and ~35 s of retrieval plus ~10 s of reranking, once the model is loaded (~2 s).
- **The model**: 1.1 GB on disk, already downloaded. Loading takes ~1 s and stays in memory for the process.
- **A full R4 multi-hop run** (50 questions, both arms) is ≈ **$0.45** of retrieval and ~15 min — `answer`'s bill, not this box's.

## Inherited landmines — read the entry before touching the area

- **The reranker goes after fusion, never before.** Reordering one arm's list and then fusing measures the reranker against a different candidate set than the system ships.
- **`huggingface.co` is unreachable; `hf-mirror.com` works.** Any model fetch must set the mirror or it hangs for minutes. `models/` is gitignored, so the artifact lives on this machine only — record the model id and the path in the run record.
- **Disk is the binding constraint** — 3.1 GB free at the time of writing, 99% full. Do not start a second download without clearing space.
- **Chunks can exceed the reranker's 512-token window**; truncation is recorded, never silent.
- **The bridged arm's evidence order is the answer's citation order.** Treating it as a relevance ranking and weighting it equally with LightRAG's retrieval order is the mistake this page exists to name.
- **The serving process need not be running for the reranker** (in-process CPU), but the arms need it for embeddings and chat.

## Gate

> the fused top-k carries evidence from both systems on multi-hop queries; the reranker reorders it

Concretely, `uv run python -m hyrag.fuse.cli check` prints, per dev multi-hop question:

1. **both arms present in the fused top-10** — the count of chunks each arm contributed, and the interleaving;
2. **the RRF list** — rank, chunk id prefix, RRF score, per-arm ranks;
3. **the reranked list** — the same chunks with cross-encoder scores and the number of positions that changed against the RRF order (zero changed positions fails the gate: a reranker that preserves the order is not reranking);
4. **provenance** — every fused chunk resolves to `file, §section, p. N–M`, and the count of chunks that could not be resolved is zero;
5. **the run's shape** — `k`, shortlist size, reranker id, model path, seconds spent reranking, and the tokens/calls the arms spent.

## Decisions already locked

- **RRF by rank, `k = 60`**, after both arms, never before.
- **Cross-encoder after RRF**, over a bounded shortlist, no score threshold.
- **Multi-hop only**; thematic stays route-only and factoid single-arm.
- **Citations derived from `_ingest.jsonl`** — `file, §section, p. N–M` (D13).
- **The reranker is `BAAI/bge-reranker-base`**, not v2-m3, for disk; the substitution is reported.

## If the gate fails

*(Shelf, after your own diagnosis.)* Likely suspects: the bridged arm's evidence ids arriving in a different id space (they should be our chunk ids through `resolve`) · an arm returning zero chunks and the fused list silently becoming single-arm · RRF ties broken by dict order instead of chunk id · the reranker truncating at a different length than the record claims · `_ingest.jsonl` read without the `type == "chunk"` filter (the file has a header record) · chunk text passed to the reranker in the wrong field order (`(question, passage)`, not `(passage, question)`).

## Discoveries

**The gate, green on the first run — three dev multi-hop questions, four clauses each.** `uv run python -m hyrag.fuse.cli check`, ≈ **$0.022 and ~75 s** total:

| question | both arms in top-10 | positions moved by the reranker | provenance | deterministic |
|---|---|---|---|---|
| mult-051 | kg-light 10 · bridged 10 | **18/20** | 20 citations · 0 unresolved · 8 truncated | ✓ |
| mult-052 | kg-light 8 · bridged 7 | **18/20** | 20 citations · 0 unresolved · 17 truncated | ✓ |
| mult-053 | kg-light 8 · bridged 8 | **14/20** | 20 citations · 0 unresolved · 17 truncated | ✓ |

**The two arms genuinely interleave, and neither swallows the other.** On mult-051 every one of the top-10 chunks was retrieved by *both* arms (which is what a hard multi-hop question looks like when both systems are right about the neighbourhood); on mult-052 and mult-053 the top-10 is split 8/7 and 8/8, with bridged-only and LightRAG-only chunks in the head. RRF is doing what it is for: the union is larger than either list and the head is shared.

**The cross-encoder disagrees with RRF often, which is the point.** 14–18 of 20 positions move. On mult-052, `637e3f21` — a chunk **only kg-light retrieved**, ranked 8 by RRF — was promoted to **4** (+3.94); on mult-051 `d59b8c81` moved 8 → 4. The other side is visible too: on mult-053 the model found exactly one strongly relevant chunk (+8.96) and scored the rest of the shortlist at or below zero (−1.40, −1.45, −1.87), which is a signal `answer` must respect rather than smooth over. That is the evidence for "reranker after fusion": fusion decides the candidate set, the model decides the order.

**Truncation is the measured limitation.** Half to nearly all of the shortlist exceeds `bge-reranker-base`'s 512-token window (8/20 on mult-051, 17/20 on the other two — our chunks run to ~1,200 XLM-R tokens). The reranker is scoring the head of each page-range chunk, not the whole of it; the record flags every truncated chunk. `bge-reranker-v2-m3` has the same 512-token window, so a longer-window reranker is a different model, not a bigger copy of this one.

**Determinism held** — the same two arm records fused twice produced the same order, CPU inference with ties broken by chunk id. Cost per question: both arms ≈ **$0.007**, reranking ≈ 2 s once the model is loaded; the box's total spend was ≈ $0.03.

**The operational trap is the download, not the fusion.** `huggingface.co` is unreachable from this machine (HTTP 000 on IPv4 and IPv6, timeouts) while `hf-mirror.com` serves 200 and the full file set; the model was fetched with `curl` into `models/` (gitignored) and loaded with `local_files_only=True`. Disk fell from 4.4 GB to **3.1 GB** free — the reason the smaller reranker was chosen is now a number in this page and a landmine in the README.

## Session log

- 2026-10-17 — **built and closed.** `hyrag/fuse/{rrf,rerank,combine,checks,cli}.py`; gate green on three dev multi-hop questions at ≈ $0.022, the reranker moving 14–18 of 20 positions with 0 unresolved citations. `sessions/S13-fuse.md` has the record.
- 2026-10-17 — page opened. Reranker downloaded through the mirror (1.1 GB; huggingface.co blocked), sanity-scored on three passages, and the model path pinned above. Disk after: 3.1 GB free.
