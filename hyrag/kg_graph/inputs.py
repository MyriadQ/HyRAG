"""kg_graph/inputs.py — the 369 chunks as GraphRAG input, and the root it reads them from.

**Why one file per chunk.** D16 says all four rows read the same chunk set. GraphRAG parses
and chunks its own inputs; the only way to satisfy D16 without asking GraphRAG to honour ids
it has never heard of is to make the *input* our chunks — one `.txt` per chunk, and a chunk
size above our 1200-token cap with zero overlap so its chunker has nothing to decide. What it
produces is then one text unit per chunk, which `tables.py` verifies **by content**, not by
count (a count that agrees while the text differs is exactly the failure the check exists to
catch).

**Why the settings and prompts are copied in from the repo.** They are part of the measured
configuration (§7.5), so they live under version control and the root is a build product:
`parsed/kg_graph/` can be deleted and rebuilt, and the hash beside the run's numbers says
which configuration produced the tables.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from hyrag.kg_graph import config
from hyrag.kg_light import config as kg_light_config

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


def chunks(limit: int | None = None) -> list[dict]:
    """The shared ingest set, in file order — the same records `kg-light` indexed."""
    records = kg_light_config.our_chunks()
    return records[:limit] if limit else records


def chunk_filename(chunk_id: str) -> str:
    """`sha256:<hex>` → `<hex>.txt`. A colon in a filename is legal but asks for trouble."""
    return chunk_id.split(":", 1)[1] + ".txt"


def manifest(root: Path) -> dict:
    """The manifest written when this root was built — it says how many chunks went in."""
    import json as _json

    path = root / "input_manifest.json"
    if not path.is_file():
        raise SystemExit(f"{root} has no input manifest — run `hyrag.kg_graph.cli inputs` first")
    return _json.loads(path.read_text(encoding="utf-8"))


def prepare_root(root: Path, limit: int | None = None, *, clean: bool = False) -> dict:
    """Build a GraphRAG root: inputs, settings, prompts. Returns the manifest.

    `clean` wipes the root first — the input directory must contain exactly our chunks, and a
    leftover file from an earlier shape of the corpus would be indexed as a document with no
    counterpart in `_ingest.jsonl`.
    """
    if clean and root.exists():
        shutil.rmtree(root)
    (root / "input").mkdir(parents=True, exist_ok=True)

    records = chunks(limit)
    written: list[dict] = []
    expected = {path.name for path in (root / "input").iterdir()}
    for record in records:
        name = chunk_filename(record["chunk_id"])
        (root / "input" / name).write_text(record["content"], encoding="utf-8")
        expected.discard(name)
        written.append(
            {
                "filename": name,
                "chunk_id": record["chunk_id"],
                "file": record["file"],
                "tokens": record["tokens"],
                "citation": record["occurrences"][0]["citation"],
            }
        )
    for stale in expected:  # a file we did not write this time
        (root / "input" / stale).unlink()

    shutil.copy(config.SETTINGS_PATH, root / "settings.yaml")
    if (root / "prompts").exists():
        shutil.rmtree(root / "prompts")
    shutil.copytree(config.PROMPTS_DIR, root / "prompts")

    manifest = {
        "type": "kg_graph_inputs",
        "graphrag_pin": config.GRAPHRAG_PIN,
        "settings_sha256": config.settings_sha256(),
        "prompts_sha256": config.prompts_sha256(),
        "chunks": len(written),
        "files": written,
    }
    (root / "input_manifest.json").write_text(json.dumps(manifest, **_JSON) + "\n", encoding="utf-8")
    return manifest
