"""parse/sidecar.py — the two artifacts this node writes, and their readers.

    parsed/<stem>/blocks.jsonl   block body + anchors   (LightRAG sidecar format)
    parsed/<stem>/meta.json      everything needed to interpret blocks.jsonl

Why these shapes:

* `blocks.jsonl` follows `docs/LightRAGSidecarFormat.md` (the format LightRAG's
  own parser services emit) so the `kg` node can hand it to LightRAG's pipeline
  without a translation layer. Fields beyond the spec (`self_ref`, `kind`,
  `label`, and `positions[].physical`) are additive and are what makes a block
  traceable back to the Docling JSON and to a page a human can open.
* `meta.json` is ours and is **authoritative for citations**: it holds the
  physical->printed page map, with a `source` for every entry, so a citation
  can never be more confident than the parse was. The heading outline is here
  too — the chunker's `§section` comes from it.

Determinism (the node contract) means: no timestamps, no run ids, sorted keys,
fixed separators. `checks.py` re-parses a document and compares these files
byte-for-byte.
"""

from __future__ import annotations

import json
from pathlib import Path

from hyrag.parse.blocks import Block
from hyrag.parse.pagelabels import PageLabelMap

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


# ---------------------------------------------------------------- write


def write_blocks(path: Path, meta_line: dict, blocks: list[Block], labels: PageLabelMap) -> None:
    """Write `blocks.jsonl`: one `meta` line, then one `content` line per block."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta_line, **_JSON) + "\n")
        for block in blocks:
            fh.write(json.dumps(_block_line(block, labels), **_JSON) + "\n")


def write_meta(path: Path, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2, **_JSON) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- read


def read_blocks(path: Path) -> tuple[dict, list[dict]]:
    """Returns `(meta_line, block_lines)`."""
    with path.open("r", encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    if not lines or lines[0].get("type") != "meta":
        raise ValueError(f"{path}: first line is not a sidecar meta line")
    return lines[0], lines[1:]


def read_meta(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- helpers


def _block_line(block: Block, labels: PageLabelMap) -> dict:
    """One LightRAG-format content line for a block.

    `positions[].anchor` is the printed page label when one is known, else the
    physical page; `physical` keeps the raw page either way. That policy is
    recorded in the sidecar meta so nobody has to infer it.
    """
    printed = labels.printed(block.page_no)
    position: dict = {
        "type": "bbox",
        "anchor": printed or str(block.page_no),
        "physical": block.page_no,
    }
    if block.bbox is not None:
        left, top, right, bottom = block.bbox
        position["range"] = [top, left, bottom, right]  # [h1, w1, h2, w2], LEFTTOP
    if block.charspan is not None:
        position["charspan"] = list(block.charspan)
    return {
        "type": "content",
        "blockid": block.block_id,
        "format": "plain_text",
        "content": block.content(),
        "heading": block.heading or "",
        "parent_headings": list(block.parents),
        "level": block.level,
        "session_type": "body",
        "kind": block.kind,
        "self_ref": block.self_ref,
        "label": block.label,
        "positions": [position],
    }
