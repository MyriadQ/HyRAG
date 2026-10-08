"""parse/checks.py — the invariants the parse stage must hold, run as one command.

    uv run python -m hyrag.parse.cli check --all

Each check states the property it verifies. A failure names the document and the
offending record, so the fix is a lookup, not an investigation.

The checks, in the order a reviewer would ask:

* `block_anchors`      every block has a physical page inside the document;
* `anchor_policy`      `positions[].anchor` agrees with the page-label map —
                       printed where known, physical where not (nothing is
                       stored twice, so nothing can drift);
* `headings`           every block's section context is self-consistent, and
                       every parent heading appeared earlier as a heading;
* `pages_covered`      informational: pages with no block at all (blank or
                       image-only pages are legitimate, but should be visible);
* `determinism`        re-parsing produces byte-identical artifacts.

`--docling` adds the expensive one: re-run the PDF through Docling from scratch
and compare the sidecar hash. It is off by default because it re-parses the
whole document; run it before trusting a new parser version.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hyrag.parse.config import doc_dir
from hyrag.parse.pipeline import parse_document
from hyrag.parse.sidecar import read_blocks, read_meta
from hyrag.parse.textnorm import ligature_gaps


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None  # None = informational
    detail: str = ""

    def line(self) -> str:
        mark = "PASS" if self.ok else ("INFO" if self.ok is None else "FAIL")
        return f"  [{mark}] {self.name}" + (f" — {self.detail}" if self.detail else "")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_document(stem: str, *, reparse: bool = True, docling: bool = False) -> list[Check]:
    """Run every invariant for one parsed document."""
    target = doc_dir(stem)
    meta_path, blocks_path = target / "meta.json", target / "blocks.jsonl"
    missing = [p.name for p in (meta_path, blocks_path) if not p.is_file()]
    checks: list[Check] = [
        Check(
            "artifacts_present",
            not missing,
            ("missing: " + ", ".join(missing)) if missing else str(target),
        )
    ]
    if missing:
        return checks

    meta = read_meta(meta_path)
    sidecar_meta, blocks = read_blocks(blocks_path)
    n_pages = meta["n_pages"]
    labels = meta["page_labels"]

    # -- sidecar meta agrees with the artifact it describes
    checks.append(
        Check(
            "sidecar_counts",
            sidecar_meta["blocks"] == len(blocks) == meta["counts"]["blocks"],
            f"meta {sidecar_meta['blocks']} / lines {len(blocks)} / meta.json {meta['counts']['blocks']}",
        )
    )

    # -- anchors
    bad_anchor = [
        b["blockid"]
        for b in blocks
        if not (
            b.get("positions")
            and isinstance(b["positions"][0].get("physical"), int)
            and 1 <= b["positions"][0]["physical"] <= n_pages
        )
    ]
    checks.append(Check("block_anchors", not bad_anchor, f"{len(bad_anchor)} unanchored blocks"))

    # -- anchor policy: printed label when the map has one, physical otherwise
    bad_policy = []
    for b in blocks:
        physical = b["positions"][0]["physical"]
        expected = (labels.get(str(physical)) or {}).get("printed") or str(physical)
        if b["positions"][0]["anchor"] != expected:
            bad_policy.append((b["blockid"], b["positions"][0]["anchor"], expected))
    checks.append(
        Check("anchor_policy", not bad_policy, f"{len(bad_policy)} blocks disagree with meta.json")
    )

    # -- headings
    # A heading can live inside a table (the A320 MMEL prints the ATA chapter
    # line as a merged cell), so the valid heading texts are those carried by
    # heading blocks *and* those set as a section context on any other block.
    heading_texts = {b["content"].lstrip("# ").strip() for b in blocks if b["kind"] == "heading"}
    heading_texts |= {b["heading"] for b in blocks if b.get("heading")}
    bad_heading = []
    for b in blocks:
        heading, level, parents = b["heading"], b["level"], b["parent_headings"]
        if b["kind"] == "heading":
            if not heading or level < 1:
                bad_heading.append((b["blockid"], "heading block without level"))
        else:
            if heading and level < 1:
                bad_heading.append((b["blockid"], "context heading at level 0"))
            if not heading and (level != 0 or parents):
                bad_heading.append((b["blockid"], "no heading but level/parents set"))
        if len(parents) >= level and level:
            bad_heading.append((b["blockid"], f"{len(parents)} parents for level {level}"))
        if any(p not in heading_texts for p in parents):
            bad_heading.append((b["blockid"], "parent heading never seen as a heading"))
    checks.append(Check("headings", not bad_heading, f"{len(bad_heading)} inconsistent blocks"))

    # -- empty content / empty pages
    empty_blocks = [b["blockid"] for b in blocks if not b["content"].strip()]
    checks.append(Check("content_nonempty", not empty_blocks, f"{len(empty_blocks)} empty blocks"))
    covered = {b["positions"][0]["physical"] for b in blocks}
    missing_pages = [p for p in range(1, n_pages + 1) if p not in covered]
    checks.append(
        Check("pages_covered", None, f"pages without any block: {missing_pages or 'none'}")
    )

    # -- page labels
    bad_labels = [
        p
        for p, entry in labels.items()
        if not (1 <= int(p) <= n_pages) or entry.get("source") not in ("detected", "inferred")
    ]
    checks.append(Check("page_labels", not bad_labels, f"{len(bad_labels)} malformed entries"))

    # -- text normalization: no `confi guration`-style fragments left behind
    leftovers = [(b["blockid"], ligature_gaps(b["content"])) for b in blocks if ligature_gaps(b["content"])]
    checks.append(
        Check(
            "text_normalized",
            not leftovers,
            f"{len(leftovers)} blocks still carry ligature gaps"
            + (f", e.g. {leftovers[0][1][:2]}" if leftovers else ""),
        )
    )

    # -- determinism: same inputs, same bytes
    if reparse:
        with tempfile.TemporaryDirectory() as tmp:
            again = parse_document(meta["source"], out_dir=Path(tmp), verbose=False)
            same_blocks = _digest(again.blocks_path) == _digest(blocks_path)
            same_meta = _digest(again.meta_path) == _digest(meta_path)
            checks.append(
                Check(
                    "determinism",
                    same_blocks and same_meta,
                    f"blocks={'same' if same_blocks else 'DIFFERENT'}, "
                    f"meta={'same' if same_meta else 'DIFFERENT'}",
                )
            )

    # -- expensive: Docling itself is deterministic on this file
    if docling:
        recorded = meta["docling"]["sha256"]
        again = parse_document(meta["source"], force_convert=True, verbose=False)
        checks.append(
            Check(
                "docling_determinism",
                again.docling_json.stat().st_size > 0 and _digest(again.docling_json) == recorded,
                f"sidecar {recorded[:12]}…",
            )
        )
    return checks
