"""parse/pipeline.py — one function: PDF -> blocks.jsonl + meta.json.

This is the node's entry point. The stages are separate modules so each can be
tested alone; this file wires them and records what happened in `meta.json`
(which is what makes an artifact self-describing months later):

    convert.convert_pdf      PDF        -> DoclingDocument (cached JSON sidecar)
    blocks.build_blocks      document   -> Blocks + furniture + page texts
    pagelabels.detect/infer  page texts -> physical -> printed page map
    sidecar.write_*          -> blocks.jsonl, meta.json

The output is *blocks*, not chunks: every block carries its page anchor and its
section context, and a chunker built on top keeps block ids on each chunk so the
citation join never has to re-read the PDF.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path

from hyrag.parse.blocks import Block, build_blocks, doc_id_for
from hyrag.parse.config import DocSpec, doc_dir, doc_spec, docling_path
from hyrag.parse.convert import convert_pdf, sha256_file
from hyrag.parse.pagelabels import PageLabelMap, detect_labels, infer_labels
from hyrag.parse.sidecar import write_blocks, write_meta
from hyrag.parse.textnorm import NORMALIZATION_ID


@dataclass
class DocArtifacts:
    """What `parse_document` produced, for the caller to report or check."""

    spec: DocSpec
    docling_json: Path
    blocks_path: Path
    meta_path: Path
    blocks: list[Block] = field(default_factory=list)
    labels: PageLabelMap | None = None
    converted: bool = False
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


def parse_document(
    path: str | Path,
    *,
    layout: str | None = None,
    force_convert: bool = False,
    out_dir: Path | None = None,
    verbose: bool = True,
) -> DocArtifacts:
    """Run the whole parse stage for one PDF. Deterministic and re-runnable."""
    spec = doc_spec(path, layout=layout)
    if not spec.path.is_file():
        raise FileNotFoundError(spec.path)
    target = out_dir or doc_dir(spec.stem)
    t0 = time.perf_counter()

    doc, docling_json, converted = convert_pdf(
        spec.path, docling_path(spec.stem), force=force_convert
    )
    result = build_blocks(doc, spec)
    blocks = result.blocks
    if not blocks:
        raise ValueError(f"{spec.relpath}: no anchored blocks — refusing to write an empty artifact")

    detected = detect_labels(spec.layout, result.page_texts, result.furniture_texts)
    labels = infer_labels(detected, n_pages=len(doc.pages))

    meta = _build_meta(
        spec=spec,
        doc=doc,
        blocks=blocks,
        headings=result.headings,
        labels=labels,
        detected=detected,
        docling_json=docling_json,
        warnings=result.warnings,
    )
    blocks_path, meta_path = target / "blocks.jsonl", target / "meta.json"
    write_blocks(blocks_path, meta["sidecar_meta"], blocks, labels)
    write_meta(meta_path, meta)

    seconds = time.perf_counter() - t0
    if verbose:
        print(
            f"  {spec.stem}: {len(doc.pages)} pp · {len(blocks)} blocks · {len(result.headings)} headings · "
            f"labels: {labels.note}" + ("" if converted else " (docling cached)")
        )
    return DocArtifacts(
        spec=spec,
        docling_json=docling_json,
        blocks_path=blocks_path,
        meta_path=meta_path,
        blocks=blocks,
        labels=labels,
        converted=converted,
        seconds=seconds,
        warnings=result.warnings,
    )


def _outline(headings: list) -> list[dict]:
    """Heading events as an outline, with consecutive repeats collapsed."""
    out: list[dict] = []
    for h in headings:
        if out and out[-1]["heading"] == h.text and out[-1]["level"] == h.level:
            continue
        out.append({"index": h.index, "page": h.page_no, "level": h.level, "heading": h.text})
    return out


def _build_meta(
    *,
    spec: DocSpec,
    doc,
    blocks: list[Block],
    headings: list,
    labels: PageLabelMap,
    detected: dict[int, str],
    docling_json: Path,
    warnings: list[str],
) -> dict:
    """Assemble `meta.json` — everything needed to interpret `blocks.jsonl`.

    Deliberately no timestamp and no run id: `checks.py` re-parses a document and
    compares bytes, and a clock would defeat that.
    """
    merged_text = "\n\n".join(b.content() for b in blocks if b.content().strip())
    page_sizes = {
        str(p): [round(doc.pages[p].size.width, 2), round(doc.pages[p].size.height, 2)]
        for p in sorted(doc.pages)
    }
    first_heading = next((b.heading for b in blocks if b.kind == "heading"), None)

    sidecar_meta = {
        "type": "meta",
        "format": "lightrag",
        "version": "1.0",
        "document_name": spec.path.name,
        "document_format": "pdf",
        # Spec: sha256 of the merged block text. Not the file hash — that lives
        # in meta.json, so the sidecar stays valid after any lossless re-export.
        "document_hash": "sha256:" + hashlib.sha256(merged_text.encode()).hexdigest(),
        "table_file": False,
        "equation_file": False,
        "drawing_file": False,
        "asset_dir": False,
        "blocks": len(blocks),
        "doc_id": doc_id_for(spec),
        "parse_engine": "docling",
        "doc_title": spec.title or (first_heading or ""),
        "bbox_attributes": {
            "origin": "LEFTTOP",
            "unit": "pt",
            "anchor_policy": "printed page label when known, else physical page",
        },
        "split_option": {"engine_version": version("docling")},
    }

    return {
        "doc_id": doc_id_for(spec),
        "source": spec.relpath,
        "title": spec.title,
        "layout": spec.layout,
        "sha256": sha256_file(spec.path),
        "n_pages": len(doc.pages),
        "page_sizes": page_sizes,
        "page_labels": labels.as_dict(),
        "page_labels_detected": {str(p): v for p, v in sorted(detected.items())},
        "page_labels_note": labels.note,
        "docling": {
            "path": str(docling_json.relative_to(docling_json.parents[2])),
            "sha256": sha256_file(docling_json),
            "version": version("docling"),
        },
        "counts": {
            "pages": len(doc.pages),
            "blocks": len(blocks),
            "headings": len(headings),
        },
        # Text repairs applied after extraction (see textnorm.py). Recorded so
        # a re-run with a different parser can be compared, not just trusted.
        "normalizations": [NORMALIZATION_ID],
        # The outline the heading rules produced, straight from the stacks. The
        # chunker's section field and the citation's §section come from here —
        # read this to see whether the parse understood the document's structure.
        # Consecutive repeats collapse: a running header (MMEL prints the ATA
        # chapter on every page) re-establishes the same section, which is not
        # information an outline should repeat.
        "heading_outline": _outline(headings),
        "warnings": warnings,
        "sidecar_meta": sidecar_meta,
    }
