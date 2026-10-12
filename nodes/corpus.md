# corpus — a frozen, licence-clear, provenance-recorded document set

**~2.5 h** · **blocks** `parse`, `qset` · **gate** at the bottom · **status** ✅ done — gate green 2026-10-07

> **How to use this page.** Open it at the start of this node; close it when the gate is green. The URLs, licences, page counts and checksums live in `CORPUS.md` (`corpus.sha256` is the machine-checkable half); this page is the contract around them. The dev slice is deliberately **not** carved until the set has been reviewed by hand.

## Purpose

Everything downstream — chunks, graph, communities, the frozen 150 questions, every citation — derives from these files. Licence sloppiness or silent file drift poisons the demo and the oral. This set is the floor the whole project stands on.

## Black box

| | |
|---|---|
| **in** | the licence-vetted source table in `PREPLAN.md` §11 + public URLs |
| **out** | PDFs on disk, `CORPUS.md` manifest, `corpus.sha256` |
| **writes** | `corpus/**` (gitignored), `CORPUS.md`, `corpus.sha256` |
| **feeds** | `parse` (the PDFs), `qset` (the content questions are written about) |

## Contract

- Every PDF on disk appears in `CORPUS.md` with source URL, licence class, page count and sha256.
- **Vendored** (redistributable) vs **link-only** (© OEM — local copy only, never committed or reposted) follows `PREPLAN.md` §11.
- Frozen: additions/removals are recorded in the manifest — no silent file edits.
- The PDFs stay local; hashes are committed (size rationale — matches DeepWalk's "data gitignored, hashes committed").
- Narrative-heavy selection (D14: extractability over brand).

## Acquired 2026-10-07 — 18 docs · 3,939 pp · ~453 MB

| folder | what | docs | pp |
|---|---|---|---|
| `corpus/vendored/faa/` | PHAK · AFH · AIM · FSB 737 | 4 | 1,913 |
| `corpus/vendored/ntsb/` | AARs: USAir 427, SWA 1248, US Airways 1549, Continental 1404, SWA 1380 | 5 | 1,141 |
| `corpus/vendored/aaib/` | 1/2025 (737-8200) · 9/2020 (A320-214) | 2 | 51 |
| `corpus/vendored/airbus-safety-first/` | 5 articles (GNSS, slides, EWIS, cockpit windows, parking) | 5 | 55 |
| `corpus/link-only/boeing/` | 737 MMEL Rev 63 | 1 | 333 |
| `corpus/link-only/airbus/` | A320 MMEL Rev 30 (draft) | 1 | 446 |
| **total** | | **18** | **3,939** |

## Gate

**“licence manifest + every PDF on disk with a checksum”** (`ROADMAP.md`).

Green when: `CORPUS.md` covers every file **and** `shasum -a 256 -c corpus.sha256` passes from the repo root.

- [x] manifest covers all 18 files
- [x] checksums recorded (18/18) — `shasum -a 256 -c corpus.sha256` passes
- [x] user review of the set — confirmed 2026-10-07
- [x] dev slice v1 carved — 508 pp, 12 files → `CORPUS.md` §Dev slice v1 · `slice.sha256`

## Decisions locked

- Corpus selection by extractability, not brand (D14) — tables-heavy documents stay out or minimal.
- OEM MMELs are link-only: never committed, never reposted.
- One fixed edition per document (see `CORPUS.md` → status notes) — no floating "latest".
- Corpus frozen for the demo (D11); any change goes through an explicit manifest edit.

## Open decisions (yours)

1. ~~Confirm / adjust the 18 files~~ — confirmed 2026-10-07.
2. ~~Dev slice~~ — v1 carved 2026-10-07 (508 pp; PHAK ch. 7 out, Continental 1404 + EI-HET in).
3. **Optional additions** — FAA TCDS A16WE + EASA A.064, 737/A320 ADs, an ASRS sample, USAF AFMANs, more NTSB/AAIB/Safety First (e.g. AAIB Titan A321 G-OATW). Only if the slice shows the set is thin where it hurts.
4. **FSB version** — Rev 17 (final) kept; Rev 22 draft not pulled. Revisit only if needed.

## Session log

- `sessions/S01-corpus.md` — 2026-10-07: acquisition, manifest, checksums, dev slice v1.
