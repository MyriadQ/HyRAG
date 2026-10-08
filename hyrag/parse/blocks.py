"""parse/blocks.py — DoclingDocument -> page-anchored blocks.

Input:  a `DoclingDocument` (from `convert.py`) and a layout rule set.
Output: an ordered list of `Block`s, one per Docling item, plus

* `furniture` — page headers/footers, kept out of the chunk stream but scanned
  for printed page labels by `pagelabels.py`;
* `page_texts` — every text on a page (including table *cells*), the raw
  material for printed-label detection.

The reading order is Docling's own (`iterate_items`), not a coordinate sort: it
is the order a human reads and the order the extraction LLM expects. `prov`
supplies the anchor — `page_no`, `bbox`, `charspan` — and one item can in
principle carry several `prov`s (a paragraph merged from two columns); we keep
the first and count the rest, because a chunk's page range is computed from
block *pages* and the join must stay honest.

Coordinate discipline (the trap): Docling boxes for text items are
BOTTOMLEFT-origin while table grid cells are TOPLEFT. Mixing them silently
produces negative heights. Every block's box is normalised here to LEFTTOP
points via `to_top_left_origin(page_height)`, so nothing downstream has to
know about the difference.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from docling_core.types.doc import (
    ContentLayer,
    DoclingDocument,
    PictureItem,
    TableItem,
    TextItem,
)

from hyrag.parse.config import DocSpec
from hyrag.parse.headings import HeadingHit, heading_stack, headings_for
from hyrag.parse.textnorm import normalize

# Docling labels -> our block kinds. Only these kinds reach the chunker.
_KIND = {
    "section_header": "heading",
    "title": "heading",
    "text": "text",
    "paragraph": "text",
    "list_item": "list",
    "caption": "caption",
    "footnote": "footnote",
    "formula": "formula",
    "code": "code",
    "table": "table",
    "picture": "picture",
}


@dataclass(frozen=True)
class Block:
    """One addressable unit of the document, with its anchor.

    `heading`/`parents` are the section context *at this block*: for a heading
    block it is the heading itself, for body content it is the innermost
    heading above it. `level == 0` and `heading is None` means: no heading
    above this block — carried explicitly, never guessed.
    """

    index: int
    kind: str
    text: str
    layer: str
    page_no: int
    bbox: tuple[float, float, float, float] | None  # (l, t, r, b), LEFTTOP
    charspan: tuple[int, int] | None
    heading: str | None
    parents: tuple[str, ...]
    level: int
    self_ref: str
    block_id: str
    label: str
    n_prov: int = 1

    def content(self) -> str:
        """The block as it appears in a chunk / sidecar content line.

        Heading blocks are rendered with markdown `#` prefixes (LightRAG
        sidecar convention); body blocks are their text as-is.
        """
        if self.kind == "heading":
            prefix = "#" * max(self.level, 1)
            return f"{prefix} {self.text}"
        return self.text


@dataclass(frozen=True)
class Furniture:
    """A page header/footer. Never chunked, but it is where printed page
    numbers live in two of the three layouts."""

    page_no: int
    label: str
    text: str


@dataclass(frozen=True)
class HeadingEvent:
    """A heading taking effect at a point in the reading order.

    Most headings are their own block; a few live *inside* a table (the A320
    MMEL prints the ATA chapter line as a merged cell), so the outline is not
    derivable from heading blocks alone.
    """

    index: int  # block index the heading took effect at
    page_no: int
    level: int
    text: str


@dataclass
class BlocksResult:
    blocks: list[Block]
    furniture: list[Furniture]
    headings: list[HeadingEvent] = field(default_factory=list)
    page_texts: dict[int, list[str]] = field(default_factory=dict)
    furniture_texts: dict[int, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- table text


def table_to_markdown(item: TableItem, layout) -> tuple[str, list[HeadingHit]]:
    """Render a Docling table as pipe-separated rows, and pull out headings.

    Deliberately *not* a full markdown table: no header separator, because
    TableFormer's first row is often the page header, not a header row. The
    output is retrieval text — compact, per-row, order-preserving.

    Three normalisations matter on this corpus:
    * merged cells repeat their text across grid columns (MMEL headers show up
      in all 7 columns) — consecutive verbatim duplicates collapse to one;
    * per-layout furniture cells are dropped by the layout rule. The raw grid
      stays in the Docling sidecar;
    * a row the layout accepts as a heading (`table_heading` — MMEL only) is
      returned separately and its text is removed from the table: on the A320
      MMEL the ATA chapter line is a merged cell inside the page-sized table,
      and it is a section heading, not table data.
    """
    grid = item.data.grid
    headings: list[HeadingHit] = []
    rows: list[list[str]] = []
    for row_index, raw_row in enumerate(grid):
        cells: list[str] = []
        for cell in raw_row:
            text = (cell.text or "").strip()
            if not text:
                continue
            text = normalize(" ".join(text.split()))
            if cells and cells[-1] == text:  # merged cell repeated verbatim
                continue
            cells.append(text)

        # A heading inside the table (MMEL only — see `table_heading`): the
        # heading text is taken out of the cell, and what is left of that cell
        # — usually a real column header — stays in the row.
        if row_index < 5:
            hit = layout.table_heading(cells)
            if hit is not None:
                if hit not in headings:
                    headings.append(hit)
                remainder = cells[0][len(hit.text) :].strip() if cells else ""
                cells = ([remainder] + cells[1:]) if remainder else cells[1:]

        cells = layout.clean_table_row(cells)
        if cells:
            rows.append(cells)
    markdown = "\n".join("| " + " | ".join(r) + " |" for r in rows)
    return markdown, headings



def picture_to_text(item: PictureItem, doc: DoclingDocument) -> str:
    """Pictures become a placeholder, caption included. Multimodal is out of
    scope (PREPLAN §9), but a marker keeps the text stream honest about what was
    there — and the caption, when present, is real retrievable text."""
    captions: list[str] = []
    for ref in item.captions or []:
        try:
            target = ref.resolve(doc)
        except Exception:  # unresolvable ref: skip, do not fail the document
            continue
        text = getattr(target, "text", "") or ""
        if text.strip():
            captions.append(normalize(" ".join(text.split())))
    caption = " ".join(captions)
    if caption:
        return f'<drawing caption="{caption}" />'
    return "<drawing />"


# ---------------------------------------------------------------- blocks


def build_blocks(doc: DoclingDocument, spec: DocSpec) -> BlocksResult:
    """Walk the document once, in reading order, and build anchored blocks."""
    layout = headings_for(spec.layout)
    doc_id = doc_id_for(spec)

    blocks: list[Block] = []
    furniture: list[Furniture] = []
    headings: list[HeadingEvent] = []
    page_texts: dict[int, list[str]] = {}
    furniture_texts: dict[int, list[str]] = {}
    warnings: list[str] = []
    stack: list[tuple[str, int]] = []

    def note_text(page_no: int, text: str) -> None:
        if text and text.strip():
            page_texts.setdefault(page_no, []).append(text.strip())

    def push_heading(hit: HeadingHit, index: int, page_no: int) -> None:
        """Apply a heading to the stack and record it for the outline."""
        nonlocal stack
        stack = heading_stack(stack, hit)
        headings.append(HeadingEvent(index=index, page_no=page_no, level=hit.level, text=hit.text))

    # --- furniture first: headers/footers are their own stream (never chunked)
    for collection in (doc.texts, doc.tables, doc.pictures):
        for item in collection:
            if item.content_layer != ContentLayer.FURNITURE:
                continue
            page_no = _first_page(item)
            text = _item_text(item, doc, layout) if not isinstance(item, PictureItem) else ""
            if page_no is None or not text:
                continue
            furniture.append(Furniture(page_no, str(item.label.value), text))
            note_text(page_no, text)
            furniture_texts.setdefault(page_no, []).append(text.strip())

    # --- body, in Docling's reading order
    for item, _depth in doc.iterate_items():
        if getattr(item, "content_layer", ContentLayer.BODY) != ContentLayer.BODY:
            continue
        # Tables are read once, here: the markdown for the block, plus any
        # heading the layout recognised in a merged cell (A320 MMEL pages are
        # one big table with the ATA chapter line inside it).
        table_headings: list[HeadingHit] = []
        if isinstance(item, TableItem):
            text, table_headings = table_to_markdown(item, layout)
        else:
            text = _item_text(item, doc, layout)
        if text is None:
            continue
        text = text.strip()
        if not text:
            continue
        page_no = _first_page(item)
        if page_no is None:
            warnings.append(f"unanchored item {item.self_ref} ({item.label.value}) dropped")
            continue

        label = str(item.label.value)
        # Page furniture that Docling did not classify as furniture (MMEL page
        # headers arrive as body `section_header`/`text`). Dropped from the
        # block stream, kept for page-label detection.
        if layout.furniture(text, label):
            note_text(page_no, text)
            continue

        # A heading found inside the table updates the stack before the table
        # block is built, so the block itself belongs to that section.
        for table_hit in table_headings:
            push_heading(table_hit, len(blocks), page_no)

        hit: HeadingHit | None = layout.heading(text, label) if label in ("section_header", "title") else None
        if hit is not None:
            push_heading(hit, len(blocks), page_no)
            stack_texts = [t for t, _ in stack]
            heading, parents, level = hit.text, tuple(stack_texts[:-1]), hit.level
            kind = "heading"
            text = hit.text
        else:
            # A `section_header` that no rule recognised is not a heading — it
            # is text (a caption Docling mislabeled, a letter-spaced cover line).
            kind = "text" if label in ("section_header", "title") else _KIND.get(label, "text")
            heading = stack[-1][0] if stack else None
            parents = tuple(t for t, _ in stack[:-1]) if stack else ()
            level = stack[-1][1] if stack else 0

        bbox, charspan, n_prov = _anchor(item, doc)
        index = len(blocks)
        block = Block(
            index=index,
            kind=kind,
            text=text,
            layer="body",
            page_no=page_no,
            bbox=bbox,
            charspan=charspan,
            heading=heading,
            parents=parents,
            level=level,
            self_ref=item.self_ref,
            block_id=_block_id(doc_id, index, heading, text),
            label=label,
            n_prov=n_prov,
        )
        blocks.append(block)
        note_text(page_no, text)
        if isinstance(item, TableItem):
            for row in item.data.grid:  # table cells may carry the page label
                for cell in row:
                    if cell.text:
                        note_text(page_no, cell.text)

    return BlocksResult(
        blocks=blocks,
        furniture=furniture,
        headings=headings,
        page_texts=page_texts,
        furniture_texts=furniture_texts,
        warnings=warnings,
    )


# ---------------------------------------------------------------- helpers


def _item_text(item: Any, doc: DoclingDocument, layout) -> str | None:
    if isinstance(item, TableItem):
        return table_to_markdown(item, layout)[0]
    if isinstance(item, PictureItem):
        return picture_to_text(item, doc)
    if isinstance(item, TextItem):
        return normalize(item.text)
    text = getattr(item, "text", None)  # other TextItem-like labels
    return normalize(text) if isinstance(text, str) else None


def _first_page(item: Any) -> int | None:
    prov = getattr(item, "prov", None) or []
    return prov[0].page_no if prov else None


def _anchor(
    item: Any, doc: DoclingDocument
) -> tuple[tuple[float, float, float, float] | None, tuple[int, int] | None, int]:
    """(bbox LEFTTOP, charspan, number of prov entries) for the first prov."""
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None, None, 0
    p = prov[0]
    bbox = None
    if p.bbox is not None:
        page = doc.pages[p.page_no]
        height = page.size.height
        bb = p.bbox.to_top_left_origin(height)
        bbox = (round(bb.l, 2), round(bb.t, 2), round(bb.r, 2), round(bb.b, 2))
    charspan = tuple(p.charspan) if getattr(p, "charspan", None) else None
    if charspan == (0, 0):
        charspan = None  # table items report a dummy span
    return bbox, charspan, len(prov)


def doc_id_for(spec: DocSpec) -> str:
    """Stable document id: `doc-<md5(relpath)>`.

    Not a content hash: the same file moved keeps its id, and re-parsing cannot
    change it. Content identity is carried separately by the sha256 in
    `meta.json` — id = identity of the *document*, hash = identity of the
    *bytes*.
    """
    return "doc-" + hashlib.md5(spec.relpath.encode()).hexdigest()


def _block_id(doc_id: str, index: int, heading: str | None, text: str) -> str:
    """LightRAG sidecar blockid: md5(doc_id : index : heading : content)."""
    payload = f"{doc_id}:{index}:{heading or ''}:{text}"
    return hashlib.md5(payload.encode()).hexdigest()
