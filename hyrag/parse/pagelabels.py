"""parse/pagelabels.py — printed page labels, and the honest fallback to physical.

The citation unit is `p. N–M` (D13). *Which* N is a real question on this corpus:

* the MMEL cuts are carved out of the middle of the source PDF — physical page 1
  of `b737_mmel_rev63__src-p147-164.pdf` is printed `26-1`;
* the Safety First articles print `037` on physical page 2;
* the NTSB reports number the front matter in roman (`i`…`viii`) and restart the
  body at `1` on physical page 14.

A reader verifies a citation against the printed label far more reliably than
against a slice-relative page number, so printed labels are preferred — but
only where they are actually *seen*. The rule here:

1. **Detect** labels from the layout's known locations (MMEL: "PAGE NO. 26-3"
   anywhere on the page, text or table cell; article/report/handbook: the short
   page-header/footer furniture text).
2. **Infer** missing pages from a constant offset, marked `source="inferred"`.
   Inference stays inside the span the offset was observed on, plus a bounded
   tail (`TAIL_TOLERANCE`) so a run that stops a few pages early does not
   silently abandon the last pages of a short document.
3. **Never invent**: a page with neither a detection nor a constant-offset
   inference stays unmapped, and `citation.py` then prints the *physical* page,
   marked as such. Every entry carries where it came from; `meta.json` keeps it.

The gate's failure demo (deliberately drop provenance) leans on the same
machinery — the point is that a missing anchor is *visible*, not plausible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "PAGE NO. 26-3" — the Boeing/Airbus MMEL printed page label. Two guards:
# * the literal `PAGE NO.` must precede the number — A320 MEL *item* numbers look
#   like `26-23-01` and matched a looser pattern on two pages;
# * no trailing digit or hyphen — `26-23-01` must not match as page `26-23`.
# TableFormer sometimes splits the header across cells ("… DATE: … PAGE" +
# "NO. 26-4"), so the search runs over the page's texts joined by a space.
_MMEL_LABEL = re.compile(
    r"PAGE\s+NO\.?\s*(\d{2})\s*-\s*(\d{1,3})(?![\d-])", flags=re.IGNORECASE
)
_MMEL_VALUE = re.compile(r"^(\d{2})-(\d{1,3})$")

_ARABIC = re.compile(r"^\d{1,4}$")
_ROMAN = re.compile(r"^[ivxlcdm]{1,7}$")
# "Page 12 of 67" and "Page 5/11" — the FAA and Airbus footer forms.
_PAGE_OF = re.compile(
    r"^page\s+(\d{1,4})\s*(?:of|/)\s*\d{1,4}$", flags=re.IGNORECASE
)
# "1-3" — chapter-page numbering, common in FAA handbooks (AFH/PHAK/AIM).
_CHAPTER_PAGE = re.compile(r"^\d{1,2}-\d{1,3}$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}

# How far past the last detected page an offset may be extended (pages).
# Bounded on purpose: appendices restart numbering, and an unbounded extension
# would print a confident wrong page for them.
TAIL_TOLERANCE = 10


@dataclass(frozen=True)
class PageLabelMap:
    """Physical page -> printed label, with provenance for every entry."""

    labels: dict[int, str]  # page_no -> printed label
    source: dict[int, str]  # page_no -> "detected" | "inferred"
    note: str = ""

    def printed(self, page_no: int) -> str | None:
        return self.labels.get(page_no)

    def origin(self, page_no: int) -> str | None:
        return self.source.get(page_no)

    def as_dict(self) -> dict[str, dict[str, str]]:
        return {
            str(p): {"printed": self.labels[p], "source": self.source[p]}
            for p in sorted(self.labels)
        }


# ---------------------------------------------------------------- detection


def detect_labels(
    layout: str,
    page_texts: dict[int, list[str]],
    furniture_texts: dict[int, list[str]],
) -> dict[int, str]:
    """Printed label per physical page, from the layout's known locations.

    * `mmel` — "PAGE NO. 26-3", searched in *all* text, because on the pages
      TableFormer read as one grid the label sits inside a table cell, not in
      body text or furniture.
    * everything else — the page number is printed in a header or footer, so
      only **furniture** is scanned. Scanning body text here is how the FSB's
      "Page 20 of 67" footer got missed while a body "17" was picked up as a
      page label, with a different offset per page.

    At most one label per page (first match wins). Two candidates on one page
    would mean the rule is wrong, and a guess would hide that; the first match
    is then checkable against the raw sidecar.
    """
    found: dict[int, str] = {}
    if layout == "mmel":
        for page_no in sorted(page_texts):
            joined = " ".join(text.strip() for text in page_texts[page_no])
            m = _MMEL_LABEL.search(joined)
            if m:
                found[page_no] = f"{m.group(1)}-{int(m.group(2))}"
        return found

    for page_no in sorted(furniture_texts):
        for text in furniture_texts[page_no]:
            t = text.strip()
            m = _PAGE_OF.match(t)
            if m:
                found[page_no] = m.group(1)
                break
            if _ARABIC.match(t) or _ROMAN.match(t) or _CHAPTER_PAGE.match(t):
                found[page_no] = t
                break
    return found


# ---------------------------------------------------------------- inference


def _roman_value(label: str) -> int | None:
    total, prev = 0, 0
    for ch in reversed(label):
        v = _ROMAN_VALUES.get(ch)
        if v is None:
            return None
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def _to_roman(value: int) -> str:
    if value <= 0:
        return ""
    table = (
        (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
        (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
    )
    out: list[str] = []
    for n, s in table:
        while value >= n:
            out.append(s)
            value -= n
    return "".join(out)


def _scheme(label: str) -> tuple[str, str]:
    """(scheme, prefix) — MMEL labels carry an ATA prefix (`26-…`)."""
    m = _MMEL_VALUE.match(label)
    if m:
        return ("mmel", m.group(1))
    return ("arabic", "")


def _offset(label: str, page_no: int) -> int | None:
    """Constant difference between printed page value and physical page."""
    m = _MMEL_VALUE.match(label)
    if m:
        return int(m.group(2)) - page_no
    if _ARABIC.match(label):
        return int(label) - page_no
    return None


def _apply(sample: str, page_no: int, offset: int) -> str:
    """Rebuild a label for `page_no` using the width of the observed sample.

    Width matters: the Safety First articles print `037`, and an inferred `38`
    beside a detected `039` would make citations read `p. 38–039`.
    """
    value = page_no + offset
    m = _MMEL_VALUE.match(sample)
    if m:
        return f"{m.group(1)}-{str(value).zfill(len(m.group(2)))}"
    return str(value).zfill(len(sample)) if sample.isdigit() else str(value)


def infer_labels(detected: dict[int, str], n_pages: int) -> PageLabelMap:
    """Extend detected labels where the numbering is arithmetic.

    * arabic / MMEL: group detections by (scheme, offset) — the Safety First
      articles print a label only on even pages, so contiguity is not required.
      Fill the span each group was observed on, plus a bounded tail.
    * roman: fill between the first and last roman detection; roman numbering is
      a front-matter device and does not reach the body.
    """
    labels = dict(detected)
    source = {p: "detected" for p in detected}
    notes: list[str] = []

    roman_pages = sorted(p for p, l in detected.items() if _ROMAN.match(l))
    if roman_pages:
        first, last = roman_pages[0], roman_pages[-1]
        step = _roman_value(detected[first]) or 0
        for p in range(first, last + 1):
            if p in labels:
                step = _roman_value(labels[p]) or step
                continue
            step += 1
            labels[p] = _to_roman(step)
            source[p] = "inferred"
        notes.append(f"roman {first}-{last}")

    groups: dict[tuple[str, str, int], list[tuple[int, str]]] = {}
    for p, l in sorted(detected.items()):
        offset = _offset(l, p)
        if offset is None or _ROMAN.match(l):
            continue
        key = (*_scheme(l), offset)
        groups.setdefault(key, []).append((p, l))

    for (_scheme_name, _prefix, offset), entries in sorted(groups.items()):
        pages = [p for p, _label in entries]
        sample = entries[0][1]
        first, last = min(pages), max(pages)
        end = last
        if n_pages - last <= TAIL_TOLERANCE:
            end = n_pages  # short tail: the offset cannot have changed meaning
        for p in range(first, end + 1):
            if p in labels:
                continue
            labels[p] = _apply(sample, p, offset)
            source[p] = "inferred"
        notes.append(f"{_scheme_name}{('/' + _prefix) if _prefix else ''} offset {offset:+d} pages {first}-{end}")

    return PageLabelMap(labels=labels, source=source, note="; ".join(notes) or "no labels recognised")
