"""parse — PDF -> blocks carrying a section heading and a page anchor.

This is roadmap box `parse`, **stopping at blocks**. Chunking is the next thing
built on top of it (by hand, in this repo), so nothing here has an opinion about
chunk size, overlap or packing.

The pipeline this package implements:

    PDF ──Docling──► DoclingDocument (sidecar JSON, cached)
                          │
                          ├─ blocks.py      reading-order walk, page anchors, heading stack
                          ├─ pagelabels.py  printed-page-label map (physical -> printed)
                          └─ sidecar.py     blocks.jsonl (LightRAG format) + meta.json
                                            │
                                            ▼
                                    a chunker reads blocks.jsonl,
                                    keeps block ids + pages on every chunk

What the parse stage guarantees to whatever chunker is built on top:

* every block has a stable `blockid` and a `positions[0]` with `physical` page
  (1-based) — plus `anchor` (printed label when known) and `bbox`/`charspan`
  where Docling supplied them;
* every block carries its section context: `heading`, `parent_headings`,
  `level` (level 0 / heading "" means "no heading", carried explicitly);
* the artifacts are byte-identical on a re-run (no timestamps).

The chunk contract the *chunker* must uphold, i.e. what `parse` needs back from
it so a citation can resolve: each chunk records the block ids it was built
from. Page range and section then follow from the blocks — they are never
stored twice, so they can never drift.

Read the files in the order above; each states its input and output at the top.
"""

from hyrag.parse.blocks import Block, BlocksResult, build_blocks
from hyrag.parse.config import DocSpec, doc_spec, layout_for
from hyrag.parse.pipeline import DocArtifacts, parse_document

__all__ = [
    "Block",
    "BlocksResult",
    "DocArtifacts",
    "DocSpec",
    "build_blocks",
    "doc_spec",
    "layout_for",
    "parse_document",
]
