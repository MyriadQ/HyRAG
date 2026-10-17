# answer — one answer over the routed evidence, with citations that resolve

**~5 h** · **feeds** `judge` (row R4), `report` · **Phase 2's fifth box** · **gate** at the bottom · **status** 🟢 **closed 2026-10-17 — gate GREEN**

> **How to use this page.** §*Verified facts* is what exists and was checked while opening the page. §*Open questions* is what the implementer decides. Nothing in `Discoveries` exists yet.

## Purpose

The delivered system's only user-facing output: **one answer, with citations that resolve to `file, §section, p. N–M`**. Everything before this box produces evidence; this box writes the answer and proves the citations are real.

It is also the box where the routed strategy becomes observable: a factoid turn answers from LightRAG's evidence, a thematic turn from LightRAG's retrieval, a multi-hop turn from the fused shortlist — and the trace says which.

## Black box

| | |
|---|---|
| **in** | a question · its class (the router) · the routed evidence: one arm's chunks, or `fuse`'s reranked shortlist · `_ingest.jsonl` for the chunk text and its canonical citation |
| **out** | one answer · the citations it used · the evidence ids behind it · the route that produced it · tokens · latency · the arm records (cost) |
| **writes** | `hyrag/answer/` (gather, render, checks, cli), `results/answer_*.jsonl` (gitignored) |
| **feeds** | `judge`'s R4 row and the §7.4 ablation, `report` |

## Contract

- **One generation call over the routed evidence**, with the citation strings handed in the prompt and required verbatim in the answer. A citation the model composes is a citation nobody can check.
- **The route is the router's**, not this box's: `hyrag/router/strategy.py` says which arm(s) fire and whether fusion applies. Factoid and thematic take one arm's evidence; multi-hop takes `fuse`'s reranked shortlist.
- **Citations are verified, not trusted.** Every citation the answer uses must be a folded substring of one of the evidence citations (`baseline/answer.py::_fold`), and every used citation's chunk is PDF-spot-checked with `chunker/checks.py::pdf_page_coverage` — the original PDF, read by a second library.
- **An unanswerable question is answered as unanswerable.** If the evidence does not contain the answer, the model is told to say so; a confident fabrication is worse than "the corpus does not say".
- **The trace is part of the output**: question → route → evidence (ids + citations) → answer → citations used → PDF coverage. One command, one example per class.

## Verified facts — do not re-derive

1. **The router's table is the route.** `hyrag/router/strategy.py`: factoid → `kg-light`; thematic → `kg-light`; multi-hop → both arms + fuse, budget ≤ 60 s / ≤ $0.02.
2. **The arms emit evidence ids in our chunk space**, and `fuse` reorders them with a cross-encoder; the fused record carries `chunk_id`, `citation`, `text`, `rerank`. Nothing needs re-retrieval here.
3. **The citation verifier exists.** `chunker/checks.py::pdf_page_coverage(pdf, pages, text)` compares the chunk's words (≥5 chars) against the PDF's own text for those sheets: own pages **0.96–1.00**, wrong pages **< 0.40**. `parsed/<stem>/meta.json` holds the PDF path; `_ingest.jsonl` holds `occurrences[0].page_range` and `.stem`.
4. **The fold is `baseline/answer.py::_fold`** (en dash vs hyphen); `verify_citations(answer, rows)` already implements the "used vs invented" split.
5. **A generation call through the proxy costs ~$0.001–0.003** at 2–4 k prompt tokens; the gate's three questions are ≈ **$0.01** plus the arms they route through.

## Open questions — settle before writing the renderer

1. **Evidence budget per class.** Factoid 6 chunks, multi-hop 8 fused chunks, thematic 12. Bigger is not better: the thematic answer already runs long, and a long evidence block invites a long answer.
2. **What to do when an arm's answer is better than a synthesis.** The arms already generate answers; the delivered system generates its own over their evidence because D13 demands one citation granularity. State the trade in the report: R4's text is ours, its retrieval is theirs.
3. **How strict is "unanswerable"?** The model is told to say so; no separate classifier. The gate records how often it happened, and the report does not hide it.
4. **Temperature.** 0, like the router, so a re-run of the same evidence is the same answer. Determinism is a property the report can claim.

## Gate

> citations spot-checked against the source PDFs; one traced example per class from one command

Concretely, `uv run python -m hyrag.answer.cli check` prints, per class (fact-001, mult-051, them-101 — dev ids):

1. the **route** and the evidence ids/citations handed in;
2. the **answer**;
3. **citations used** and the count that is not in the evidence (must be zero);
4. **PDF coverage** per used citation (threshold from `chunker/checks.py`), against the original PDF;
5. **tokens, latency, cost** for the generation and for the arms behind it.

## Decisions already locked

- **One answer, one citation granularity**: `file, §section, p. N–M` (D13).
- **The router decides the route**; this box never chooses an arm.
- **Verified against the PDF**, not against our own parse artifacts alone.

## If the gate fails

Likely suspects: the model paraphrasing a citation (hand the exact strings in the prompt, check folded) · the evidence including a chunk whose `page_range` is empty (the parser's `(none)` heading case) · the PDF path missing for a stem · a fused chunk ordering that puts the answer's evidence below the cut.

## Discoveries

**The gate, green — three traced examples, every citation checked against the PDF.**

| class | route | evidence | citations used | invented | PDF coverage | cost |
|---|---|---|---|---|---|---|
| fact-001 | kg-light | 6 | 1 | 0 | **1.00** | $0.0043 |
| mult-051 | kg-light + bridged + fuse | 8 | 2 | 0 | **1.00** | $0.0068 |
| them-101 | kg-light | 12 | 3 | 0 | **1.00** | $0.0100 |

**R4's row, 30 held-out questions, gold-class routes** (`results/answer_eval_record.json`): factoid **hit 1.00** · 1.7 citations · 5.9 s · $0.0054; multi-hop **1.00** · 2.8 · 24.4 s · $0.0090; thematic **0.40** · 6.5 · 34.9 s · $0.0098. **0 invented citations across 30 answers, 0 refusals**, $0.24 for the run. The thematic cell is LightRAG's own 0.40 — the delivered system inherits its retrieval, not a better one.

**`page_range` is a range, not two pages.** The first PDF check passed only the endpoints of `[8, 11]` and scored a *correct* citation at **0.50**; expanding to pages 8–10 gave **1.00**. The chunker's own spot check never had the bug because it uses block page numbers, not the occurrence's range — a reminder that a second caller of a verifier can misuse it even when the verifier is right.

**A refusal is a short answer, not a sentence inside a long one.** A substantive 3.5 k-character thematic answer ending with *"the provided evidence does not show a single cross-report finding…"* was first counted as a refusal. The detector now requires the marker to be the whole reply (or its opening), which is why the R4 run reports **0 refusals** with no answers suppressed.

**Generation is the cheap part.** One call per question at 1.8–4.2 s and $0.001–0.004; the arms behind it cost more than the answer does, and the end-to-end latency is dominated by retrieval (thematic 34.9 s of which 4.2 s is generation).

## Session log

- 2026-10-17 — **built and closed.** `hyrag/answer/{gather,render,pipeline,checks,cli}.py`; gate green (PDF coverage 1.00 on every used citation), R4 evaluated on 30 held-out questions (0 invented citations, 0 refusals, $0.24). `sessions/S14-answer.md`.
- 2026-10-17 — page opened.
