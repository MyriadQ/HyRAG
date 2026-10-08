"""parse/config.py — where documents, layouts and output paths are registered.

Nothing here parses anything. This module answers three questions 

1. *which* documents are in scope (the dev slice, or an explicit list);
2. *which layout* each document uses (MMEL / report / article / handbook) — the
   heading rules and the printed-page-label rules are chosen by this name;
3. *where* artifacts go (`parsed/`, gitignored).

Why a registry instead of auto-detecting the layout: the corpus is frozen
, we know exactly which file is an MMEL and which is a report, and an
explicit map is deterministic and debuggable. Auto-detection would be a
heuristic hiding next to the rules it selects — if the map is wrong, the
artifact is wrong, visibly, at this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- paths

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "corpus"
SLICE = CORPUS / "slice"

# Derived data. Gitignored: everything here is re-generable from `corpus/`.
PARSED = REPO / "parsed"
DOCLING_DIR = PARSED / "docling"  # one raw DoclingDocument per PDF (the cache)

LAYOUTS = ("mmel", "report", "aaib", "article", "handbook")


@dataclass(frozen=True)
class DocSpec:
    """One document: where it is, which layout rules it follows, how to cite it."""

    relpath: str  # relative to REPO, e.g. "corpus/slice/vendored/ntsb/aar1004_...pdf"
    layout: str  # one of LAYOUTS
    title: str  # human title for meta/citations

    @property
    def path(self) -> Path:
        return REPO / self.relpath

    @property
    def stem(self) -> str:
        return self.path.stem


# ---------------------------------------------------------------- the dev slice
#
# The slice is the development target (`CORPUS.md` -> Dev slice v1, 508 pp).
# The full corpus is a path change, not a second code path: `parse` takes any
# PDF, the registry only supplies the layout rules.
_SLICE_DOCS: tuple[DocSpec, ...] = (
    DocSpec("corpus/slice/vendored/faa/fsb_b737_rev17_2020-11-16.pdf", "handbook", "FAA FSB 737 Rev 17"),
    DocSpec("corpus/slice/vendored/ntsb/aar1903_southwest1380_b737.pdf", "report", "NTSB AAR-19/03"),
    DocSpec("corpus/slice/vendored/ntsb/aar1004_continental1404_b737.pdf", "report", "NTSB AAR-10/04"),
    DocSpec("corpus/slice/vendored/aaib/aaib_9-2020_a320-214_oe-loa.pdf", "aaib", "AAIB 9/2020"),
    DocSpec("corpus/slice/vendored/aaib/aaib_1-2025_b737-8200_ei-het.pdf", "aaib", "AAIB 1/2025"),
    DocSpec("corpus/slice/vendored/airbus-safety-first/gnss-interference.pdf", "article", "Safety First — GNSS interference"),
    DocSpec("corpus/slice/vendored/airbus-safety-first/proper-ewis-maintenance-for-safe-operations.pdf", "article", "Safety First — EWIS maintenance"),
    DocSpec("corpus/slice/vendored/airbus-safety-first/safe-aircraft-parking.pdf", "article", "Safety First — Safe aircraft parking"),
    DocSpec("corpus/slice/link-only/airbus/a320_mmel_rev30_draft__src-p157-174.pdf", "mmel", "A320 MMEL Rev 30 (draft) — ATA 26"),
    DocSpec("corpus/slice/link-only/airbus/a320_mmel_rev30_draft__src-p248-254.pdf", "mmel", "A320 MMEL Rev 30 (draft) — ATA 29"),
    DocSpec("corpus/slice/link-only/boeing/b737_mmel_rev63__src-p147-164.pdf", "mmel", "B737 MMEL Rev 63 — ATA 26"),
    DocSpec("corpus/slice/link-only/boeing/b737_mmel_rev63__src-p206-209.pdf", "mmel", "B737 MMEL Rev 63 — ATA 29"),
)

BY_STEM: dict[str, DocSpec] = {d.stem: d for d in _SLICE_DOCS}


def slice_docs() -> tuple[DocSpec, ...]:
    """The dev slice, in registry order."""
    return _SLICE_DOCS


def doc_spec(path: str | Path, *, layout: str | None = None) -> DocSpec:
    """Resolve a PDF to a DocSpec.

    Registered slice files keep their curated title/layout. Anything else (the
    handbook smoke tests, the full corpus later) gets a fallback spec whose
    layout is inferred from the path — see `layout_for`. The fallback is
    deliberately conservative: `handbook` is the generic rule set.
    """
    p = Path(path)
    if not p.is_absolute():
        p = REPO / p
    spec = BY_STEM.get(p.stem)
    if spec is not None and spec.path == p:
        return spec
    rel = p.relative_to(REPO) if p.is_relative_to(REPO) else p
    return DocSpec(str(rel), layout or layout_for(p), p.stem)


def layout_for(path: str | Path) -> str:
    """Layout name for an unregistered PDF, by path pattern.

    This is a *fallback for smoke tests*, not the main mechanism — the slice
    registry above is the main mechanism. Patterns follow the corpus naming in
    `CORPUS.md`.
    """
    low = str(path).lower()
    if "mmel" in low:
        return "mmel"
    if "/aaib/" in low or "aaib_" in low:
        return "aaib"
    if "/ntsb/" in low or "aar" in low:
        return "report"
    if "safety-first" in low or "safety_first" in low:
        return "article"
    return "handbook"


# ---------------------------------------------------------------- output paths


def doc_dir(stem: str) -> Path:
    """Per-document artifact directory: `parsed/<stem>/{blocks.jsonl,meta.json}`."""
    return PARSED / stem


def docling_path(stem: str) -> Path:
    """Raw DoclingDocument sidecar (the conversion cache)."""
    return DOCLING_DIR / f"{stem}.docling.json"
