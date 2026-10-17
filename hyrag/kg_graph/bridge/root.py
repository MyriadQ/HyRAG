"""bridge/root.py — a bridged GraphRAG root: the four input tables plus a patched settings copy.

Two rules from the node page shape this module:

* **The settings file is hashed into the run record; edit a copy in the root, never the
  versioned original.** So `hyrag/kg_graph/settings.yaml` stays byte-identical to the one R3
  ran under, its sha is recorded as the configuration's identity, and the *root's* copy —
  with two deliberate overrides — gets a hash of its own.
* **The overrides are exactly two, and both are named in the run record.**

  1. `workflows:` — the whole switch that makes this BYOG. Three workflows, so GraphRAG's
     extractor never runs: one extraction pass, one graph (D1, D2, D4).
  2. `X-Hyrag-Row: kg-graph-bridge` — so the report calls land in `parsed/llm_proxy.jsonl`
     under their own row tag instead of inside `kg-graph`'s window. Without it §7.4's two
     halves would share a token bucket and neither cost would be attributable.

Everything else — `cluster_graph.seed`, `max_cluster_size`, the report prompts, the models,
the embedding endpoint — is inherited untouched, because §7.4 compares *indexes*, not search
settings or prompt wording.

The patch is string surgery rather than a YAML round-trip on purpose: `pyyaml` is not a
declared dependency (it arrives transitively), and a round-trip would reformat the file so
that "differs by two things" stops being checkable by eye. The two edits carry occurrence
assertions; a third occurrence, or the loss of one, stops the build instead of shipping a
root whose record describes a file that did not run.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from hyrag.kg_graph import config
from hyrag.kg_graph.bridge import convert

BRIDGE_ROOT = config.PARSED / "kg_graph_bridged"
SMOKE_ROOT = config.PARSED / "kg_graph_bridged_smoke"
RECORD_NAME = "index_record.json"
MANIFEST_NAME = "bridge_manifest.json"
SNAPSHOT_DIR = "run1"  # run one's tables, kept beside run two's for the diff

SMOKE_NODES = 150  # big enough to cluster into several communities, small enough to read

# The community layer, and only the community layer. `create_communities` needs
# entities+relationships; `create_community_reports` needs communities on top; the embedding
# workflow turns the reports (and the reused text units) into the vectors global search reads.
WORKFLOWS = ["create_communities", "create_community_reports", "generate_text_embeddings"]

ROW_TAG = "kg-graph-bridge"
BASE_ROW_LINE = '"X-Hyrag-Row": "kg-graph"'

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


# ---------------------------------------------------------------- the settings copy


def settings_copy(source: str) -> tuple[str, dict]:
    """The versioned settings text, plus the two overrides. Returns `(text, overrides)`."""
    occurrences = source.count(BASE_ROW_LINE)
    if occurrences != 2:
        raise SystemExit(
            f"expected 2 occurrences of {BASE_ROW_LINE!r} in settings.yaml (one per completion "
            f"model), found {occurrences} — the override below would tag the wrong number of rows"
        )
    if "\nworkflows:" in source:
        raise SystemExit("settings.yaml already carries a top-level `workflows:` — refusing to guess")

    text = source.replace(BASE_ROW_LINE, f'"X-Hyrag-Row": "{ROW_TAG}"')
    text = text.rstrip("\n") + (
        "\n\n### Bridge overrides — this root only; the versioned file above is untouched ###\n"
        "#\n"
        "# 1. Only GraphRAG's community layer runs. Its extraction workflows are absent on\n"
        "#    purpose: the entities and relationships come from LightRAG's graph (D1, D2, D4),\n"
        "#    and a second extraction pass is the cost this box exists to avoid.\n"
        "# 2. Every completion call from this root is tagged `kg-graph-bridge` in the proxy log,\n"
        "#    so §7.4 can attribute the bridged arm's tokens separately from R3's.\n"
        "workflows:\n"
        + "".join(f"  - {name}\n" for name in WORKFLOWS)
    )

    # Re-read what we are about to write: the assertions above are about the input, these are
    # about the output, and only the second kind can catch a replace that silently missed.
    if text.count(f'"X-Hyrag-Row": "{ROW_TAG}"') != 2:
        raise SystemExit("settings copy: the row tag was not applied to both completion models")
    if text.count('"X-Hyrag-Row": "kg-graph"') != 0:
        raise SystemExit("settings copy: an untagged completion model survives")
    overrides = {"workflows": WORKFLOWS, "row_tag": ROW_TAG}
    return text, overrides


# ---------------------------------------------------------------- building the root


def clear_output(root: Path) -> None:
    """Remove the run's product, keep its cache.

    The second run of the gate must not reuse run one's output tables — that would make the
    diff a comparison of a file with itself — but it *should* reuse the report cache, because
    a cache hit is the evidence that the two runs sent byte-identical requests. The cache is
    keyed on the request payload, not on the root, so it says exactly that and nothing about
    where the answer came from.
    """
    output = root / "output"
    if output.exists():
        shutil.rmtree(output)


def prepare(root: Path, *, clean: bool = False, smoke: bool = False) -> dict:
    """Write the four tables, the patched settings copy and the prompts into `root`."""
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "output").mkdir(parents=True, exist_ok=True)

    built = convert.build(limit=SMOKE_NODES if smoke else None)
    convert.write_tables(root, built["rows"])

    settings_text, overrides = settings_copy(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    (root / "settings.yaml").write_text(settings_text, encoding="utf-8")

    if (root / "prompts").exists():
        shutil.rmtree(root / "prompts")
    shutil.copytree(config.PROMPTS_DIR, root / "prompts")

    manifest = {
        "type": "kg_graph_bridge_inputs",
        "graphrag_pin": config.GRAPHRAG_PIN,
        "smoke": bool(smoke),
        "settings_sha256": config.settings_sha256(),
        "settings_root_sha256": convert.sha256_file(root / "settings.yaml"),
        "prompts_sha256": config.prompts_sha256(),
        "overrides": overrides,
        **built["meta"],
    }
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, **_JSON) + "\n", encoding="utf-8")
    return {"manifest": manifest, "rows": built["rows"]}


def manifest(root: Path) -> dict:
    path = root / MANIFEST_NAME
    if not path.is_file():
        raise SystemExit(f"{root} has no {MANIFEST_NAME} — run `bridge prepare` first")
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot(root: Path, name: str = SNAPSHOT_DIR) -> Path:
    """Copy the run's six output tables aside, so a second run can be diffed against them."""
    destination = root / name
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for table in (*convert.TABLE_NAMES, "communities", "community_reports"):
        source = root / "output" / f"{table}.parquet"
        if source.is_file():
            shutil.copy2(source, destination / source.name)
    return destination
