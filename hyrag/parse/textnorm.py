"""parse/textnorm.py — the one text repair the parse stage applies, and nothing else.

**The artifact.** In this corpus the `fi`/`fl` ligature glyph is emitted as its
own text run, and Docling's span assembly inserts a space after it:

    "Insuffi cient"      -> "Insufficient"
    "confi guration"     -> "configuration"
    "(fi g.1)"           -> "(fig.1)"
    "fl ight"            -> "flight"

Measured before writing this file (70 occurrences across the three layouts):
every true case ends the left token in `fi` or `fl`. Every false positive a
naive rule would hit — "off the", "takeoff obstacles" — ends in `ff`. So the
rule repairs `fi`/`fl` only. It is deliberately *not* a general spelling
corrector and it does not touch spacing anywhere else.

This matters beyond cosmetics: BM25 matches tokens, and `confi guration` does
not match a query for `configuration`; entity extraction sees the same broken
token.

**Known limitation.** When the ligature is detached from *both* sides —
"Safety fi rst" (the magazine masthead) — the left token is the bare "fi" and
the rule above cannot touch it without also joining phrases like "sci fi movie".
One occurrence in the slice, and it is visible in the parse output rather than
silently joined; a dictionary-free rule cannot separate the two cases.

**Why here and not in the chunker.** It is a property of the extraction, not of
how text is grouped: every consumer of the text (chunker, entity extraction,
BM25, embeddings) needs the repair, and it must happen exactly once. Every
artifact records `normalizations: ["ligature-gap-join/v1"]` so a re-run with a
newer Docling can be compared rather than trusted.
"""

from __future__ import annotations

import re

NORMALIZATION_ID = "ligature-gap-join/v1"

# A word ending in fi/fl, a single space, then a lowercase word start.
# `(?<=\w)` keeps the left part from starting mid-symbol; the right side must
# start with a lowercase letter, so a genuine sentence boundary never matches.
_LIGATURE_GAP = re.compile(r"(?<=\w)(\w*(?:fi|fl)) ([a-z]\w*)")


def normalize(text: str) -> str:
    """Apply every repair the parse stage owns. Idempotent by construction."""
    if not text or ("fi " not in text and "fl " not in text):
        return text  # fast path: nothing to scan
    return _LIGATURE_GAP.sub(lambda m: m.group(1) + m.group(2), text)


def ligature_gaps(text: str) -> list[str]:
    """Left-over `<word ending fi/fl> <lowercase word>` fragments, for `checks.py`."""
    return [m.group(0) for m in _LIGATURE_GAP.finditer(text)]
