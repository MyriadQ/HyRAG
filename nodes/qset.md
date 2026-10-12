# qset — 150 questions, 50 per class, frozen in git

**~5 h** · **blocks** nothing · **gate** at the bottom · **status** ▶ current (opened 2026-10-11)

> **How to use this page.** Open it at the start of this node; close it when the gate is green. The output is one committed file, not a pipeline. The `qset` is the instrument the whole benchmark is measured with — it is frozen **before** `router` exists, because `router`'s class→strategy table is read *off* these results (`PREPLAN.md` §5.6, §2.3, §10 Q4). Tuning questions after the fact is tuning on the test set.

## How to run it

```
cd ~/Repos/HyRAG                                  # the package is NOT installed: `-m hyrag.…`
                                                  # resolves from the repo root only
uv run python -m hyrag.qset.cli <draft|critique|shortlist|show|freeze>
```

Two traps, both hit on 2026-10-11 and both worth knowing once:

- **The working directory matters.** From inside `hyrag/qset/`, `python -m hyrag.qset.cli` fails with
  `ModuleNotFoundError: No module named 'hyrag'` — the packages are used from the repo root, not
  installed into the environment.
- **`python` on this machine is Anaconda's 3.13**, which has none of the dependencies (no `tiktoken`,
  no Docling). Always go through `uv run` (or `.venv/bin/python`). From another directory,
  `uv run --directory ~/Repos/HyRAG python -m hyrag.qset.cli …` also works.

## Purpose

`ROADMAP.md` compares four systems on the same three classes of question. Every number in the results table is an answer to a question from this file, so the file's quality is the experiment's ceiling: a set that is 90% factoid makes the thematic column meaningless, and a set drawn from one document measures that document, not the corpus. The `qset` is also the only artifact in the project whose **commit SHA is load-bearing** — it is the proof that the questions predate the router.

## Black box

| | |
|---|---|
| **in** | the **dev slice** (`corpus/slice/`, 12 documents, 508 pp — decided 2026-10-11; the questions are drawn from it and every one names the chunk that answers it) |
| **out** | one committed file: 150 questions — 50 factoid · 50 multi-hop · 50 thematic — each with its **evidence target** and its **gold answer** |
| **feeds** | `judge` (the instrument; §7.2's 4 × 3 matrix), `router` (its class table is read off the judged results) |
| **not** | a demo script, a smoke test, or something a system can be tuned against. Nothing reads it until it is frozen. |

## Contract

- **Exactly 50 per class.** Class is assigned by the *information need*, never by "which system we expect to win".
- **Every question names at least one evidence target** — the `chunk_id`(s), and therefore the page range, that contain its answer. This is what makes retrieval quality a **deterministic** check (§7.2), and it is checkable against `parsed/_ingest.jsonl` the moment the question is written.
- **Every question carries a gold answer** (decided 2026-10-11). It is the answer we assert is correct, drafted **from the evidence target** and hand-checked — writing it is how the target gets verified, not extra work beside that check. It is what lets the judge grade correctness and completeness rather than only groundedness, and it brings DeepEval's contextual metrics back into play (D8c).
- **The thematic class uses its gold answer as a checklist, not a string.** There the reference is *the set of themes the corpus supports*, and the rubric still scores Comprehensiveness/Diversity/Empowerment (§7.2): a system is not penalised for well-supported themes we did not list, and it is penalised for missing the anchor themes or inventing unsupported ones. Grading a thematic answer by similarity to our own list would measure agreement with us, not quality.

  ```
  {"id": "fact-014", "class": "factoid",
   "question": "At what height above the landing runway threshold must the aircraft be stabilised for landing in IMC, per the operator's OM?",
   "evidence_target": {"stem": "aaib_1-2025_b737-8200_ei-het", "chunk_ids": ["sha256:…"],
                       "citation": "aaib_1-2025_b737-8200_ei-het.pdf, §Operations Manual (OM) Part A, p. 12"},
   "gold_answer": "1,000 ft above the landing runway threshold elevation.",
   "draft": {"model": "…", "prompt_version": "…"}}
  ```
- **Answerable from the corpus, and only from the corpus.** A question whose answer needs world knowledge measures the model, not the system. Drafts that a general model could answer without retrieval are rejected.
- **No question names a system, a technique or a method** ("what does the knowledge graph say about…"). The questions ask about aircraft, systems, procedures and documents.
- **Frozen means frozen**: changes go through a new commit and a line on this page, never by editing the file in place.
- **The commit SHA is recorded here**, and it must predate the first `router` commit.

## The three classes, defined so two people would agree

| class | what makes a question this class | what disqualifies a draft |
|---|---|---|
| **factoid** | one fact, present in **one** chunk: a number, a limit, a name, a condition | it needs a second chunk to be answerable (that is multi-hop, not a hard factoid) |
| **multi-hop** | requires connecting **≥ 2 facts from ≥ 2 places** (or a relation between two named things) before an answer exists | the two facts turn out to sit in the same chunk; or the question is only *long*, not composite |
| **thematic** | asks for recurring patterns, differences or themes **across documents**; no single chunk answers it | a factoid wearing a plural; a question a single report's summary already answers |

Balancing rule: the three classes should be roughly **equally hard to answer from one retrieved chunk**. Otherwise a "class ranking" is a ranking of how easy the classes are. Note it, don't chase it — §7.6 reports per class for exactly this reason.

## Open questions — settle before generating anything

1. ~~**Gold answer per question, or reference-free?**~~ — **settled 2026-10-11: gold answers, on every question**, with the thematic caveat written into the contract above. What it costs: ~150 short answers reviewed in the same pass that filters the questions, and the risk that a wrong reference makes every row wrong in the same direction — hence the rule that no answer is admitted without the page it was read from.
2. ~~**Which documents feed the questions.**~~ — **settled 2026-10-11: the dev slice, and the benchmark is evaluated on it** (`PREPLAN.md` §7). 375 chunks, both manufacturers, everything chunked and gated today. What this costs: the thematic class draws on **two** NTSB reports rather than five, and the report states the scope as a limitation. The full corpus stays a Phase 2 scale-up.
3. **Class → document distribution.** Which documents can carry a thematic question at all (it needs cross-document reach, ideally across both manufacturers)? No single document may dominate a class.
4. **Who drafts.** LLM-generated from **chunks** (not raw text), then hand-filtered — the prompt and the model are recorded in the session file, per §7.5.
5. **Unanswerable questions** — the three classes are fixed by `ROADMAP.md`; a refusal probe would have to live *inside* a class or be struck. Decide explicitly rather than letting a few slip in.

## Procedure

1. Choose the source documents per class (open question 2).
2. Draft against the **chunks** — from `parsed/_ingest.jsonl`, so the evidence target is recorded at the moment the question is written, not reconstructed afterwards.
3. Filter by hand: right class · answer is in the target · corpus-only · no system names.
4. Balance: 50/50/50, and check the per-document distribution.
5. **Freeze**: commit the file, record the SHA on this page.
6. Pilot before scaling: ten questions across the three classes, read them out loud, and only then produce the rest. A class definition that survives ten drafts usually survives fifty; one that does not, is caught cheaply.

## Gate

> committed; its commit SHA predates `router`'s

Concretely: 50/50/50 · every question's evidence target resolves to real `blockid`s in the ingest set · no question names a system or technique · the SHA is on this page and in the session file.

## Decisions already locked

- 150 questions, 50 per class (`ROADMAP.md`).
- The three classes are factoid · multi-hop · thematic (`PREPLAN.md` §2.3, D5).
- The judge reads it through DeepEval (D8c); the harness owns the protocol (§7.2).
- The questions come from the **dev slice**, and the benchmark is evaluated on the slice (`PREPLAN.md` §7) — decided 2026-10-11 to keep four indexing runs cheap.

## If the gate fails

*(Shelf, after your own diagnosis.)* Likely suspects: evidence targets that resolve to a chunk that does *not* contain the answer (a drafting shortcut — the question was written from the PDF, not the chunk) · a class definition drifting during generation (re-read the class table every 25 questions) · thematic questions that are really one report's summary · `qset` grown past 150 by keeping the drafts.

## Discoveries

*Drafting round 2026-10-11. The gate is not closed — the pool is drafted and audited, the hand-filter is not done.*

- **Drafting from chunks is the right shape.** Every candidate is born with its evidence target, so
  the target cannot drift from the question. Pool: **580 candidates** (150 factoid · 340 multi-hop ·
  90 thematic) in 1m45s, 8 dropped for staying conjoined after a rewrite.
- **Two filtering layers before a human reads anything.** The drafting call's own `self_check` is
  worthless — it clusters at 4–5 and flagged 1 of 360 — so a **second, stricter pass re-reads every
  draft against the class definition** (`hyrag/qset/critique.py`, temperature 0) and answers hard
  yes/no questions: *is it the class it claims? is the answer in the sources? does it ask one thing?
  is the answer short (factoid only)? is it corpus-only?* It rejects **37%**, mostly for being two
  questions joined by "and". 358 survived; the shortlist is 62 + 62 + 62 = **186 for 150 slots**.
- **The "multi-hop" failure mode is a conjunction, and it survives prose.** A prompt that forbids
  "what did X do, and what does Y say" does not stop the model writing it: 46 of the first 120
  multi-hop drafts were conjoined. What worked: (i) a **mechanical shape check** — more than one
  interrogative, or two clauses joined by "and what" → one rewrite, then drop; (ii) handing the
  model **three** chunks instead of two and letting it refuse with `{"skip": true}`.
- **The class followed the chunk counts, not the corpus.** The four long reports own ~80% of the
  chunks, so they owned ~80% of the questions, and the MMEL dispatch conditions and Safety First
  procedures — the Airbus/Boeing material the assignment is about — were a rounding error. Fixed
  with a per-document quota handed out one slot at a time to whoever has the most material left.
- **Multi-hop was availability-limited, then wasn't.** Requiring *three* chunks sharing a rare term
  in different sections yielded only ~55 usable candidates. Relaxing it to **pairs** (they either
  connect or the model refuses) took survivors from 55 to 155.
- **Two of these bugs were mine, in the auditor, and the calibration examples caught both**: it was
  applying the factoid length rule to multi-hop (rejecting a question it simultaneously called
  "genuine multi-hop"), and it was reading the drafting-time excerpts — three chunks truncated to
  700 characters — so a thematic question drawn from six documents looked unsupported.
- **And one was a real crash that hid behind a `tail`.** `draft_batch`'s retry block read `record`
  before it was built (`UnboundLocalError`), which killed a 400-candidate run at 150 and wrote
  nothing. Nothing was lost — the file is written after the loop — but the lesson is cheap and worth
  keeping: **read the whole log, not the last three lines.**
- **Parameters:** `deepseek-flash`, prompts `qset-draft-3` / `qset-critique-1`, drafting at
  temperature 0.7 (variety) and the audit at 0 (repeatability), **thinking disabled**, seed 20261011
  for selection so *what was drafted from* is reproducible even though the drafting is not.


- **Selection round (2026-10-11).** 186 offered → **150 kept, 50/50/50**, frozen into
  `qset/questions.jsonl`. Selection was made against the whole shortlist, not by score: 35 were dropped
  and every drop has its reason in `qset/selection.md`. The categories are worth knowing because they
  are what a human eye catches and an LLM audit does not:
  **near-duplicates** (the biggest group — five variants of the cockpit-seat 16-G question, three of the
  HPC-blade-fracture cause, three of the adhesive latch plate, and the thematic class's near-identical
  framings of the same theme), **two contradictions** (fact-000 vs fact-001 gave 2.5 and 2.2 units for the
  same stabiliser-trim event — both dropped rather than pick a winner), **page-locator questions** ("on
  what page does Appendix A begin"), **glossary lookups** ("what does FBO stand for"), and **facts about
  the reports themselves** rather than about the aircraft (which Board Member joined the team, which
  report covers another accident, a revision date).
- **The distribution, and one limitation to state in the report.** Factoid draws on **7 documents**,
  thematic on **10**. Multi-hop draws on **three** — `aaib_9-2020`, `aar1004`, `aar1903` — because a
  connection needs shared entities inside one document, and only the long reports have them in quantity.
  That is a property of the class, not of the selection, and it is the one class where the corpus balance
  is not what it should be. A cross-document multi-hop variant is the obvious future fix.
- **The shortlist itself had a balance bug, caught before the selection.** Ranking survivors by score
  handed the factoid class to three accident reports and dropped the FSB (26 survivors), both Safety First
  articles and every MMEL. Fixed by allocating the shortlist **proportionally per document** — score
  decides which of a document's candidates are offered, balance decides how many.
- **Licence housekeeping.** `qset/critique.jsonl` and `qset/shortlist.jsonl` embed chunk excerpts next to
  each candidate and are now **gitignored** alongside `drafts.jsonl` and `review.md`. Only four files are
  meant for git: `questions.jsonl` (questions, gold answers, citations — no source text), `keep.txt`,
  `selection.md` and `final.md`.
## Session log

- opened 2026-10-11 — 360 drafted candidates in `qset/drafts.jsonl` (gitignored; it carries source
  text), review table in `qset/review.md`, hand-filter pending.

## Session log

- opened 2026-10-11; no questions drafted yet.
