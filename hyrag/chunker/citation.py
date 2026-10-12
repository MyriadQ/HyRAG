"""chunker/citation.py — a chunk's block ids turned into `file, §section, p. N–M`.

Citations are the project's fixed unit: the file, the section, and a
page **range** — never a single page, because roughly two thirds of chunks cross a
sheet boundary.

Where each part comes from:

* the **file**    — `meta["source"]`, the PDF the blocks were parsed out of;
* the **section** — `chunk["heading"]`, the section the chunk *starts* in;
* the **pages**   — the `blockid`s in `chunk["refs"]`, looked up in the corpus-wide
  `blocks_by_id` table, taking each block's `positions[0]["physical"]`.

A page has two numbers and the two must not be confused:

* **physical** — the sheet's position in the PDF file, counted from 1. Always known.
* **printed**  — the number printed on the paper (`29-3`, `037`, `iv`). A reader finds
  this with their eye, so it is preferred — but only where the parser actually saw it
  or could infer it from a constant offset (`hyrag.parse.pagelabels`).

Not every sheet has a printed number. On the slice, the cover sheet of every article
and report is unmapped, and the NTSB reports number their front matter in roman and
restart the body at `1`. The rule implemented here (decided in `scratch/chunker.ipynb`
§4) is deliberately blunt:

    both ends mapped and the same numeral shape  ->  p. 29-3–29-5
    anything else                                ->  p. 1–2 (physical)

The second line covers both awkward cases with one branch: a span that begins on an
unnumbered cover sheet, and a span that crosses a numbering restart (roman front
matter into a body starting at `1`), which no single range can express. Printing
physical pages with an explicit `(physical)` tag keeps the range readable and never
claims more confidence than the parse had.

What this module does **not** do: decide which document a chunk belongs to when the
same text occurs in two of them. That is `packing.collapse_duplicate_chunks` (§5.6).
"""

from __future__ import annotations

import re
from pathlib import Path

# The numeral shapes a printed label can take on this corpus. Two labels may share a
# range only when they are the same shape: `xiii` followed by `1` is a numbering
# restart, not an interval.
_CHAPTER_PAGE = re.compile(r"^\d{1,3}-\d{1,3}$")  # MMEL printed page: 29-3
_PAGE_NUMBER = re.compile(r"^\d{1,4}$")  # article / report / handbook footer: 037, 12
_ROMAN = re.compile(r"^[ivxlcdm]{1,7}$")  # NTSB front matter: iv, xiii

PHYSICAL_TAG = "(physical)"  # printed after a range built from physical sheet numbers


class ProvenanceError(RuntimeError):
    """A citation cannot be produced: the chain from chunk to page is broken.

    Raised — never swallowed — when a ref has no block, or a block has no page. Both
    mean provenance was dropped somewhere upstream (a store that serialised the text
    without the anchors, a ref that was rewritten). The one thing that must not happen
    is a plausible-looking page number: an explicit failure is recoverable, a wrong
    citation is not (PREPLAN §5.6, step 4).
    """


# ---------------------------------------------------------------- page numbers


def label_shape(label: str) -> str:
    """Which numbering system a printed label belongs to, or `"unknown"` if it fits none.

    Used only to refuse a range whose two ends are not comparable — the actual
    formatting of a label is never changed.
    """
    if _CHAPTER_PAGE.match(label):
        return "chapter-page"
    if _PAGE_NUMBER.match(label):
        return "page-number"
    if _ROMAN.match(label.lower()):
        return "roman"
    return "unknown"


def page_numbers(refs: list[str], blocks_by_id: dict[str, dict]) -> list[int]:
    """The physical sheet number of every block a chunk was built from, in reading order.

    Raises `ProvenanceError` when a ref has no block, or a block has no page: a chunk
    pointing at a block that does not exist is a broken provenance chain, and a citation
    built from it would be a guess. Callers treat that as a hard failure — never as
    something to skip or default (PREPLAN §5.6, step 4).
    """
    pages: list[int] = []
    for ref in refs:
        block = blocks_by_id.get(ref)
        if block is None:
            raise ProvenanceError(
                f"blockid {ref} is not in this document's blocks — the chunk's refs and its "
                f"blocks.jsonl disagree"
            )
        positions = block.get("positions") or []
        if not positions or not isinstance(positions[0].get("physical"), int):
            raise ProvenanceError(
                f"block {ref} carries no physical page — provenance was dropped before this "
                f"chunk, so no citation can be trusted"
            )
        pages.append(positions[0]["physical"])
    return pages


def format_page_range(pages: list[int], labels: dict[str, dict]) -> str:
    """`p. N–M` for the sheets a chunk covers.

    `pages` is a list of physical sheet numbers in any order; `labels` is
    `meta["page_labels"]` — keyed by `str(physical)`, each value
    `{"printed": ..., "source": "detected" | "inferred"}`. Pages with no entry are
    unmapped, which is a normal state, not an error.
    """
    first, last = min(pages), max(pages)
    printed = [labels.get(str(p), {}).get("printed") for p in range(first, last + 1)]

    # Both ends mapped *and* one numbering system across the whole span: the printed
    # labels are what a reader can verify against the paper, so they win.
    if all(printed) and len({label_shape(label) for label in printed}) == 1:
        return f"p. {printed[0]}" if first == last else f"p. {printed[0]}–{printed[-1]}"

    # Otherwise the span is either unnumbered at an end or crosses a restart. Physical
    # pages are always true, and `(physical)` says why the numbers look like that.
    if first == last:
        return f"p. {first} {PHYSICAL_TAG}"
    return f"p. {first}–{last} {PHYSICAL_TAG}"


# ---------------------------------------------------------------- the citation


def format_citation(chunk: dict, blocks_by_id: dict[str, dict], meta: dict) -> str:
    """The whole citation for one chunk: `file.pdf, §section, p. N–M`.

    `chunk` is a `packing.Chunk`, `blocks_by_id` covers the corpus (block ids are
    md5(doc_id : index : heading : content), so they never collide across documents),
    and `meta` is that chunk's document `meta.json`.
    """
    file_name = Path(meta["source"]).name
    section = chunk["heading"] or "(none)"  # a chunk can start on a block with no heading
    pages = page_numbers(chunk["refs"], blocks_by_id)
    return f"{file_name}, §{section}, {format_page_range(pages, meta['page_labels'])}"
