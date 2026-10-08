"""parse/headings.py — per-layout heading rules.

Docling labels items `section_header` with a `level`, but neither is usable as a
section heading on its own. Measured on the slice:

* MMEL: every `section_header` has `level=1` — including
  "U.S. DEPARTMENT OF TRANSPORTATION…", "TABLE KEY" and the repeated page
  header. The only real section is the ATA chapter line "26. Fire Protection".
* NTSB/AAIB reports: real headings are numbered — "1. Factual Information",
  "1.5.3 Pilots' Recent History" — and the depth *is* the numbering depth. The
  cover page also produces letter-spaced junk headings ("Avi iation n Acc ident
  Repo ort") that must not enter the section stack.
* Safety First articles: headings are unnumbered; ALL-CAPS headings are the
  top level, title-case ones are subsections, and figure captions
  ("(fi g.2)") are mislabeled `section_header` too.

So the rule is: **one small classifier per layout**, selected by name from
`config` (never auto-detected). A block that no rule recognises is body content
and inherits the current heading stack; a chunk with no heading above it
carries an explicit `None` — the contract allows `none` but never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class HeadingHit:
    """A recognised heading: the text as it will be cited, and its depth (1-based)."""

    text: str
    level: int


# `section_header` items longer than this are prose that Docling mislabeled.
MAX_HEADING_CHARS = 110

# Figure/table captions and source notes that Docling files under `section_header`.
CAPTION_LIKE = re.compile(
    r"^(?:\(?fi\s?g|figure|fig\.|table|photo|source|note:|\d+\.\s*figure)",
    flags=re.IGNORECASE,
)

# "11.2.1 Some Title" — report-style numbering. Depth = number of components.
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$")

# ATA chapter heading, MMEL style: "26. Fire Protection".
_ATA = re.compile(r"^(\d{2})\.\s+(\S.*)$")

# In some A320 and B737 pages the chapter heading shares a grid cell with the
# column header ("26. Fire Protection Sequence No.") or with a MEL item number
# ("26. Fire Protection 26-22-10"). The chapter title ends where the table
# vocabulary starts.
_MMEL_TABLE_WORDS = re.compile(
    r"\s+(?=Sequence\s+No\.?\b|Item\b|Change\s+Bar\b|\d{2}-\d{2}-\d{2}\b)"
)

# Fragments of the MMEL page number that TableFormer leaves in the grid
# ("NO. 26-4"), and the running header it merges with other cells
# ("REVISION NO. 63 DATE: 04/03/2026 PAGE NO. 26-4").
_MMEL_CELL_LABEL = re.compile(r"^NO\.\s*\d{2}\s*-\s*\d+$")
_COMBINED_CELL_LABEL = re.compile(
    r"^REVISION NO\..*(?:DATE:|PAGE NO\.?\s*\d{2}\s*-\s*\d+)"
)


def _clean(text: str) -> str:
    """One tidy line: collapse whitespace, strip. Citations embed this verbatim."""
    return " ".join(text.split()).strip()


def _letter_spaced(text: str) -> bool:
    """True for cover-page letter art, e.g. 'Avi iation n Acc ident Repo ort'.

    These arrive as `section_header` from Docling because they look like
    headings typographically, but they are broken-up title lines on the report
    cover. The tell is a high share of one-character words.
    """
    tokens = text.split()
    if len(tokens) < 3:
        return False
    singles = sum(1 for t in tokens if len(t) == 1 and t.isalpha())
    return singles / len(tokens) >= 0.4


class LayoutHeadings(Protocol):
    name: str

    def heading(self, text: str, label: str) -> HeadingHit | None: ...

    def furniture(self, text: str, label: str) -> bool: ...

    def clean_table_row(self, cells: Sequence[str]) -> list[str]: ...

    def table_heading(self, cells: Sequence[str]) -> HeadingHit | None: ...


class BaseLayout:
    """Default behaviour: nothing is furniture, no table cell is dropped,
    no table cell is a heading. Layouts override what they know; a layout that
    overrides nothing is generic — and generic is the safe direction here:
    a table cell that only *looks* like a heading must not open a section."""

    name = "generic"

    def furniture(self, text: str, label: str) -> bool:
        return False

    def clean_table_row(self, cells: Sequence[str]) -> list[str]:
        return list(cells)

    def table_heading(self, cells: Sequence[str]) -> HeadingHit | None:
        return None


# ---------------------------------------------------------------- layouts


class HandbookHeadings(BaseLayout):
    """Fallback/generic rule: numbered headings get their numbering depth,
    unnumbered ones are level 1. Used by the FAA handbooks and the FSB."""

    name = "handbook"

    def heading(self, text: str, label: str) -> HeadingHit | None:
        if label != "section_header":
            return None
        t = _clean(text)
        if not t or len(t) > MAX_HEADING_CHARS or _letter_spaced(t):
            return None
        if CAPTION_LIKE.match(t):
            return None
        m = _NUMBERED.match(t)
        if m:
            return HeadingHit(t, min(m.group(1).count(".") + 1, 6))
        return HeadingHit(t, 1)


class MmelHeadings(BaseLayout):
    """MMEL rule: the ATA chapter line is the only heading.

    "26. Fire Protection" appears as a header on every page of the chapter;
    repeating it as the current heading is exactly right — every chunk on those
    pages belongs to ATA 26.
    """

    name = "mmel"

    # Repeated page-furniture lines that Docling labels `section_header` or
    # plain `text`: the FAA/Boeing header block printed on every MMEL page.
    # They are dropped from the block stream (still scanned for page labels).
    FURNITURE = (
        "U.S. DEPARTMENT",
        "MASTER MINIMUM EQUIPMENT LIST",
        "TABLE KEY",
        "REVISION NO",
        "PAGE NO",
        "DATE:",
        "AIRCRAFT:",
        "BOEING B-737",
        "AIRBUS A320",
    )

    def furniture(self, text: str, label: str) -> bool:
        t = _clean(text).upper()
        return t.startswith(self.FURNITURE) or bool(_COMBINED_CELL_LABEL.match(t))

    def clean_table_row(self, cells: Sequence[str]) -> list[str]:
        """Drop the page-furniture *cells* TableFormer folds into the grid.

        On pages where the whole layout was read as one table (MMEL pp. 3-4,
        8-9, 12, 15), the FAA header block arrives as ordinary cells inside
        the MEL grid: "REVISION NO. 63 DATE: …", "NO. 26-4", "TABLE KEY". A
        whole row is rarely all furniture, so the filter is per cell; a row
        that empties out is dropped by the caller.
        """
        out: list[str] = []
        for cell in cells:
            upper = _clean(cell).upper()
            if upper.startswith(self.FURNITURE) or _MMEL_CELL_LABEL.match(upper):
                continue
            out.append(cell)
        return out

    def table_heading(self, cells: Sequence[str]) -> HeadingHit | None:
        """The ATA chapter line when the page was read as one big table.

        This is A320-specific in practice: the whole page is a single TableItem
        and the chapter heading is a merged cell inside it — either alone
        ("26. Fire Protection", repeated once per grid column) or glued to the
        column header ("26. Fire Protection Sequence No."). Both shapes are
        recognised here; nothing else is, which is what keeps ordinary MEL data
        rows out of the heading stack.
        """
        unique = list(dict.fromkeys(cells))
        if not unique:
            return None
        if len(unique) == 1 or _ATA.match(unique[0]):
            return self.heading(unique[0], "section_header")
        return None
    def heading(self, text: str, label: str) -> HeadingHit | None:
        if label != "section_header":
            return None
        t = _clean(text)
        if t.upper().startswith(self.FURNITURE):
            return None
        m = _ATA.match(t)
        if not m:
            return None
        title = _MMEL_TABLE_WORDS.split(_clean(m.group(2)))[0]
        return HeadingHit(f"{m.group(1)}. {title}", 1)



class ReportHeadings(BaseLayout):
    """NTSB rule: numbering is the outline.

    Depth = dots + 1: "1. Factual Information" -> 1, "1.10.3 Runway Selection"
    -> 3. Unnumbered headings are level 1 when they are a known front-matter
    section or a short all-caps line. (AAIB reports have their own layout —
    they do not number their sections.)
    """

    name = "report"

    _SECTION_WORDS = {
        "executive summary",
        "contents",
        "figures",
        "tables",
        "abbreviations",
        "abbreviations and acronyms",
        "preface",
        "glossary",
        "appendix",
        "summary",
    }

    def heading(self, text: str, label: str) -> HeadingHit | None:
        if label != "section_header":
            return None
        t = _clean(text)
        if not t or len(t) > MAX_HEADING_CHARS or _letter_spaced(t):
            return None
        if CAPTION_LIKE.match(t):
            return None
        m = _NUMBERED.match(t)
        if m:
            return HeadingHit(t, min(m.group(1).count(".") + 1, 6))
        if t.lower() in self._SECTION_WORDS:
            return HeadingHit(t, 1)
        if t.isupper() and len(t) <= 60:
            return HeadingHit(t, 1)
        return None


class AaibHeadings(BaseLayout):
    """AAIB rule: unnumbered section headings, flat outline.

    AAIB reports do not number their sections ("Synopsis", "History of the
    flight", "Conclusions"), so unlike the NTSB reports the numbering is not the
    outline; every `section_header` Docling finds is a level-1 heading. The
    heading stack still works — the nearest heading above a block is its
    section — it just never nests.
    """

    name = "aaib"

    _SKIP = {"aaib", "air accident investigation branch", "contents", "footnote"}

    def heading(self, text: str, label: str) -> HeadingHit | None:
        if label != "section_header":
            return None
        t = _clean(text)
        if not t or len(t) > MAX_HEADING_CHARS or _letter_spaced(t):
            return None
        if CAPTION_LIKE.match(t) or t.lower() in self._SKIP:
            return None
        m = _NUMBERED.match(t)
        if m:
            return HeadingHit(t, min(m.group(1).count(".") + 1, 6))
        return HeadingHit(t, 1)


class ArticleHeadings(BaseLayout):
    """Safety First rule: type case + all-caps, no numbering.

    The first heading of the document is the article title (level 1); ALL-CAPS
    headings are level 2 sections; everything else is a level 3 subsection.
    This is a convention of the publication, verified on the three articles in
    the slice — if a future article breaks it, the level is cosmetic (the
    *text* is what citations use); the heading stack still nests correctly.
    """

    name = "article"

    def __init__(self) -> None:
        self._seen_first = False

    def heading(self, text: str, label: str) -> HeadingHit | None:
        if label != "section_header":
            return None
        t = _clean(text)
        if not t or len(t) > MAX_HEADING_CHARS:
            return None
        if CAPTION_LIKE.match(t):
            return None
        if not self._seen_first:
            self._seen_first = True
            return HeadingHit(t, 1)
        letters = [c for c in t if c.isalpha()]
        caps_ratio = sum(1 for c in letters if c.isupper()) / max(len(letters), 1)
        return HeadingHit(t, 2 if caps_ratio >= 0.8 else 3)


_LAYOUTS: dict[str, type[LayoutHeadings]] = {
    "mmel": MmelHeadings,
    "report": ReportHeadings,
    "aaib": AaibHeadings,
    "article": ArticleHeadings,
    "handbook": HandbookHeadings,
}


def headings_for(layout: str) -> LayoutHeadings:
    """Instantiate the rule set for a layout name. Raises on an unknown name —
    a typo in the registry must fail loudly, not fall back silently."""
    try:
        return _LAYOUTS[layout]()
    except KeyError:
        raise ValueError(f"unknown layout {layout!r}; known: {sorted(_LAYOUTS)}") from None


def heading_stack(stack: list[tuple[str, int]], hit: HeadingHit) -> list[tuple[str, int]]:
    """Update a heading stack with a new heading and return the new stack.

    A heading at level L closes every heading at level >= L, then opens itself.
    This is the standard outline behaviour; without it, "1.2 Injuries" followed
    by "2. Analysis" would keep "1.2" as the parent of an unrelated section.
    """
    new = [(t, l) for (t, l) in stack if l < hit.level]
    new.append((hit.text, hit.level))
    return new
