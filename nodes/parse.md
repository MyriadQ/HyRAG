# parse — chunks carrying section heading and page range

**~10 h** · **blocks** `kg` · **gate** at the bottom · **status** ✅ done (2026-10-11) — parse stage built and verified, chunker built, gate green, handbook smoke test run (findings below)

> **How to use this page.** Open it at the start of this node; close it when the gate is green. Input is the dev slice (`corpus/slice/`, 508 pp); the full corpus is a path change, never the development target. The reading below is what this node needs and nothing more — the next node's material read early is retention lost.

## Purpose

Citations are `file, §section, p. N–M` (D13), and nothing in the stack emits that by default: LightRAG's `native` parser refuses PDFs, `legacy` produces no sidecar at all, page anchors exist in the parser sidecar but are dropped at chunking and again at the vector store, and roughly two thirds of chunks span a page boundary. This node builds the only provenance chain the project has — every citation in the evaluation, every citation before the bridge, and the *citation-resolution* metric itself all resolve through it.

## Black box

| | |
|---|---|
| **in** | PDFs from the dev slice — one is the minimum; three layouts make the gate honest (MMEL conditional pages, an NTSB report, a Safety First article) |
| **out** | chunk records: text · source file · section heading (or explicit `none`) · page range — plus the provenance sidecar the retrieval path reads |
| **writes** | `parsed/<stem>/chunks.jsonl` per document, plus the corpus-wide deduplicated `parsed/_ingest.jsonl` (the artifact `kg` reads); gitignored, regenerable |
| **feeds** | `kg-light`, `kg-graph`, `baseline` (all four benchmark rows read this one chunk set, D16) · `qset` (evidence targets) · every citation in `answer` |

## Contract

- Every chunk carries file id · section heading or explicit `none` · **page range** — never a single page (D13; ~2/3 of chunks cross a boundary).
- Page labels survive the whole chain: parser anchor → block → chunk → store. Nothing between us and the store may drop them; the join is ours.
- Deterministic and re-runnable: same PDFs in → identical chunks out. Not a notebook (D11).
- Chunking is single and shared — one chunk set for all four rows (`PREPLAN.md` §6, D16).
- **No byte-identical chunk pair reaches ingest.** LightRAG keys chunks by content, so a duplicate that survives chunking silently merges two documents into one citation. The chunker collapses duplicates and records *all* their locations — `chunk_id → [(file, §section, page range), …]` — so the citation enumerates them instead of picking one. Rationale and the rejected alternative: `PREPLAN.md` §5.6.
- ~~The handbook layer (PHAK/AFH/AIM) gets a parse-only smoke test once the slice passes~~ — **run 2026-10-11**: 1,846 pp parsed, all invariants and determinism pass on all three; it found one real gap, in AIM's printed page labels (Discoveries).

## Open questions (settle before writing code)

1. ~~**Docling or MinerU**~~ — **settled: Docling**, native, no Docker. MinerU was dropped: Docling emits `prov` on every item (the anchor this node exists for) and runs in-process (`uv`), with no service. MinerU's `middle.json` also carries page anchors, but it costs a service for no anchor gain. `native` refuses PDFs and `legacy` emits no sidecar at all.
2. **Where chunking happens** — LightRAG's tokenizer or ours? If ours: which LightRAG API ingests pre-chunked text with `file_path` intact? Check the core insert paths in the source before deciding. *(Open — next session. The parse stage deliberately stops at blocks; a chunk must keep the block ids it came from.)*
3. ~~**Heading detection**~~ — **settled**, one rule set per layout, selected by registry (never auto-detected): `mmel` (ATA chapter line, incl. inside the page-sized table), `report` (NTSB numbering depth), `aaib` (unnumbered), `article` (caps/title case), `handbook` (generic). A block with no heading above it carries `heading=""`, `level=0`. Details and the measured traps: `sessions/S02-parse.md`.
4. ~~**Chunk size**~~ — **settled 2026-10-11: `max_tokens=1200`, `overlap=64`, `section_policy=significant`** (measured on the slice; see Discoveries). The trade in `PREPLAN.md` §10 Q2 was real: citation width 2.1 sheets at 1200 against 1.5 at 512.

## Built — the parse stage (2026-10-07)

`hyrag/parse/` — code, not a notebook (D11). Entry point:

```
uv run python -m hyrag.parse.cli parse --all      # PDF -> parsed/<stem>/{blocks.jsonl,meta.json}
uv run python -m hyrag.parse.cli check --all      # invariants + byte-determinism, 12/12 PASS
uv run python -m hyrag.parse.cli show <stem> [--headings|--labels|--page N]
```

**`parsed/<stem>/blocks.jsonl`** — LightRAG sidecar format, one line per block:

| field | meaning |
|---|---|
| `blockid` | `md5(doc_id : index : heading : content)` — the join key a chunk must keep |
| `content` | the block text (headings rendered `# …`, tables as pipe-markdown) |
| `heading` / `parent_headings` / `level` | section context at this block; `""` / `[]` / `0` when there is none |
| `positions[0]` | `anchor` (printed label when known), `physical` (1-based PDF page), `range` (LEFTTOP points), `charspan` |
| `kind`, `self_ref`, `label` | block kind, Docling JSON pointer, Docling label — for tracing back |

**`parsed/<stem>/meta.json`** — `n_pages`, per-page size, `page_labels` (physical → printed + `detected`/`inferred`), `heading_outline`, counts, source and sidecar sha256, `normalizations`.

**What the chunker must uphold.** A chunk records the block ids it was built from (LightRAG's `sidecar.refs` shape) and nothing else about pages: the page range and `§section` are then *derived* from those blocks, so they can never drift from the anchor. Every invariant the parse stage promises is in `hyrag/parse/checks.py` and can be extended for chunks — the chunk-level additions being **content uniqueness after dedupe** and **every collapsed duplicate having at least one recorded location**.

## Built — the chunker (2026-10-11)

`hyrag/chunker/`, five modules, one concern each (`hyrag/parse/`'s convention):

```
uv run python -m hyrag.chunker.cli chunk --all      # blocks -> chunks.jsonl + _ingest.jsonl
uv run python -m hyrag.chunker.cli check --all      # 111 invariants, incl. the PDF spot check
uv run python -m hyrag.chunker.cli cite <stem> --index N [--verify] [--drop-provenance]
```

| module | holds |
|---|---|
| `packing.py` | `chunk_blocks` (three `section_policy` variants, block-granular `overlap`), `build_chunk`, `collapse_duplicate_chunks` |
| `citation.py` | `format_citation`, `format_page_range`, `ProvenanceError` — the citation rule is in its docstring |
| `store.py` | the two artifacts and their readers |
| `checks.py` | 9 per-document invariants + 3 corpus ones + `pdf_spot_check` (reads the PDF with `pypdfium2`, not our sidecar) |
| `cli.py` | `chunk` · `check` · `cite` |

A citation chain that breaks raises `ProvenanceError` and the command exits 2 — never a plausible page number (PREPLAN §5.6 step 4). The gate's deliberate-failure demo is `cite --drop-provenance`.

## Reading (what to extract)

1. `PREPLAN.md` §2.3 Case A + §6 Contracts (Parse row) — the exact citation chain to produce. ~20 min.
2. `PREPLAN.md` §8 — the verified parser facts (sidecar `positions[].anchor`, 53/80 page-spanning chunks in the probe run) and §10 Q1–Q2. ~15 min.
3. LightRAG `docs/` — what `native` vs `legacy` emit, and the ingestion path for externally chunked text (`ProgramingWithCore.md`). ~30 min.
4. Docling provenance docs vs MinerU `middle.json` — page anchor structure on each side. ~30 min.

**Not yet:** anything for `kg` (embeddings, merge semantics) or `bridge` — this node ends at chunks-with-provenance.

## Gate

> one real PDF → one retrieved chunk → resolves to `file, §section, p. N–M`; **and** no byte-identical chunk pair reaches ingest, with every collapsed duplicate's locations recorded

Concretely: demonstrate on ≥3 slice layouts, and show it **fail** once (drop provenance deliberately → the citation must degrade visibly, not silently). Report the duplicate-collision count on the slice — if it is zero, the dedupe is insurance rather than load-bearing.

## Decisions already locked

- D13: citation unit is `file, §section, page range`.
- D15: no Docker — the parser runs natively or as a local service (brew/uv).
- Single chunking, shared across all arms.
- The block→page join is custom code we own.

## If the gate fails

*(Shelf, after your own diagnosis.)* Likely suspects: sidecar → chunker drop · vector store serialization dropping fields · slice physical→printed page offset (the four MMEL cuts carry a map in `CORPUS.md`).

## Discoveries

*Parse half filled by the build session; the chunk-size line measured 2026-10-11 (§7 of `scratch/chunker.ipynb`).*

- **Parser chosen + why:** **Docling, native (no Docker/service)** — the only one of the two candidates that both emits per-item page anchors (`prov`) and runs in-process; MinerU would add a service for no anchor gain. `do_ocr=False`, `do_table_structure=True`, images off. Sidecar re-converts **byte-identically** (verified on two documents).
- **Chunk size chosen + measured page-spanning rate:** **`max_tokens=1200`, `overlap=64`, `section_policy=significant`** (decided 2026-10-11). Measured over the 12-document slice: **59% of chunks span more than one sheet**, mean citation width **2.1 sheets**, 375 chunks (31.2/doc, mean 545 tokens, median 430), **6 byte-identical chunks collapsed**. The cap is a ceiling, not a target: the section policy decides the boundaries, so a mean chunk is 545 tokens and the cap only cuts long sections. Chosen to match the **LightRAG paper** — *"To ensure consistency, the chunk size is set to 1200 across all datasets"* (`papers/lightrag_hku.pdf`, Evaluation Details) — and LightRAG is the extractor the delivered system pays for. What that costs: the **GraphRAG paper used 600-token chunks with 100-token overlaps** (its corpus section) and measured that *"GPT-4 extracted almost twice as many entity references when the chunk size was 600 tokens than when it was 2400"* (Figure 3). One chunk set cannot match both papers (D16), so the `kg-graph` row runs 2× its paper's chunk size — **a stated deviation for that row, not a silent one**. Overlap: at 1200 it is nearly inert (375 chunks at 0, 64 and 100; +0.4% / +1.1% text), so LightRAG's own 100-token default was not worth adopting.
- **`section_policy=significant` is not a preference, it is a citation requirement:** with the `free` policy (headings ignored) **41% of chunks on the slice carry a `§` that is false for most of their text** — a citation that reads as verified and is not. `hard` (close at every heading change) is equally honest but doubles the chunk count (742 vs 646). `significant` closes at `max(2, the chunk's own level)`, so sub-sections merge upward while a chunk that opened deep still stops at its own siblings.
- **Surprises:**
  - the `fi`/`fl` ligature split (`Insuffi cient`, `confi guration`) is systemic across all three layouts and both Docling PDF backends — a PDF-encoding property, not a backend bug; bounded repair in `textnorm.py`;
  - the A320 MMEL has **no** `section_header` items at all — the whole page is one table and the ATA chapter line is a merged cell inside it;
  - Docling mislabels figure captions (`(fi g.2)`) and cover title art (`Avi iation n Acc ident Repo ort`) as `section_header`;
  - the FSB prints `Page 12 of 67`, the articles `Page 1/12`, the NTSB reports roman front matter then a body restart at 1 — four label conventions, three of them invisible unless furniture is read separately from body text;
  - Docling's `level` field is useless for the outline (always 1 on the MMEL) — depth has to come from the document's own numbering.
- **Handbook smoke test (2026-10-11)** — PHAK 522 pp / 7,346 blocks / 1,018 headings (Docling 158 s) · AFH 406 pp / 4,847 blocks / 599 headings (80 s) · AIM 918 pp / 12,952 blocks / 2,294 headings (278 s). `parse check` on each: **9 PASS**, including `headings`, `text_normalized` and `determinism`; only `pages_covered` reports INFO (blank/dividers: 10, 4 and 28 pages). Parse-only, as the contract says: no chunks, no embedding, no model calls — **8m42s of CPU, ~0.1 GB of gitignored artifacts, $0**.
  - **The gap it found: AIM's page labels.** AIM prints its page number as `1−1−33` — three parts (`chapter−section−page`), separated by **U+2212 MINUS SIGN**, not a hyphen. `pagelabels.py` matches `^\d{1,2}-\d{1,3}$` over text that `textnorm.py` never dash-normalizes, so **859 of 918 pages stay unmapped** and every AIM citation falls back to `p. <physical> (physical)`. Visible, not silent — which is the design working — but the handbook layer would cite physical pages only. The fix is local to label *detection* (normalize U+2210–U+2212/U+2010–U+2015 to `-`, add a three-part pattern); it must **not** touch `textnorm.py`'s block text, because the document hash is computed over block content and a change there would move the whole chunk set.
  - PHAK 433/522 and AFH 369/406 pages carry printed labels (chapter-page `1-2`, `12-16`, roman front matter); the unmapped remainder are chapter dividers and blanks. The `page_labels_note` renders these as `mmel/NN offset …` because the note is built from the *pattern family* — cosmetic, but it reads as if an MMEL rule fired on a handbook.
- **What `kg` must not assume:**
  - that a chunk's page range can be recomputed from the chunk *text* — it cannot; it comes from block ids, so the chunker must carry them;
  - that `positions[].anchor` is numeric — it is a string and may be a printed label (`26-3`, `iv`, `1/12`); `physical` is the integer page;
  - that headings always arrive as their own block — MMEL chapter headings can live inside a table;
  - that every page is mapped to a printed label — unmapped pages fall back to the physical page and the citation says so.

## Session log

- `sessions/S02-parse.md` — parse stage built, slice parsed (508 pp · 3,181 blocks · 473 headings), checks 12/12. Chunking left to the next session by decision.
