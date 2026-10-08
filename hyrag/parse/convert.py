"""parse/convert.py — PDF -> DoclingDocument, cached and deterministic.

Docling is the chosen parser. What was verified on
this machine before writing this file, and why each setting is what it is:

* Docling runs natively via `uv` — no Docker, no service. `lightrag`'s
  `native` parser refuses PDFs and its `legacy` parser emits no page anchors;
  Docling emits `prov` on every item, which is the anchor this node exists for.
* `do_ocr=False`: the slice PDFs are born-digital (a real text layer), so OCR
  would only add models, runtime and OCR errors on top of correct text.
* `do_table_structure=True`: the MMEL chapters *are* tables (30 TableItems on
  the 18-page Boeing cut); without TableFormer they would arrive as loose text.
* page/picture images are **off**: they cost memory and disk, and multimodal
  content is out of scope (PREPLAN §9). Only the provenance fields are kept.
* the export is written with `image_mode=PLACEHOLDER`, `coord_precision=2` and
  `sort_keys=True` — the documented deterministic form. Re-converting the same
  PDF must give a byte-identical sidecar; `checks.py` verifies it.

The sidecar is a *cache*, not the node's output: `blocks.py` reads it, and it is
safe to delete (re-conversion just costs CPU).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DoclingDocument, ImageRefMode

_converter: DocumentConverter | None = None


def build_converter() -> DocumentConverter:
    """The configured Docling pipeline. Models load lazily on first convert."""
    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.generate_page_images = False
    opts.generate_picture_images = False
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def convert_pdf(
    pdf: Path,
    sidecar: Path,
    *,
    force: bool = False,
    page_range: tuple[int, int] | None = None,
) -> tuple[DoclingDocument, Path, bool]:
    """Convert `pdf` to a DoclingDocument, caching the JSON sidecar.

    Returns `(document, sidecar_path, converted)`. `converted=False` means the
    cache was reused — the normal path when iterating on chunking, because
    Docling is the slow step (~2–8 s per 10 pages here).
    """
    global _converter
    if sidecar.exists() and not force:
        return DoclingDocument.load_from_json(sidecar), sidecar, False
    if _converter is None:
        _converter = build_converter()
    kwargs: dict = {} if page_range is None else {"page_range": page_range}
    t0 = time.perf_counter()
    doc = _converter.convert(pdf, **kwargs).document
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    doc.save_as_json(
        sidecar,
        image_mode=ImageRefMode.PLACEHOLDER,
        coord_precision=2,
        sort_keys=True,
    )
    print(f"    docling: {pdf.name} -> {sidecar.name} in {time.perf_counter() - t0:.1f}s")
    return doc, sidecar, True


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    """Streaming sha256 — PDFs here reach hundreds of MB (AFH is 261 MB)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()
