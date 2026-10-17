"""bridge/convert.py — LightRAG's graph, re-expressed as GraphRAG's input tables.

This is the whole of BYOG's conversion half. GraphRAG's community layer reads four parquet
tables and nothing else, so the converter's output *is* the interface:

| table | source | what is derived here |
|---|---|---|
| `entities` | graphml nodes | `degree` (incident edges), `frequency`, `text_unit_ids` |
| `relationships` | graphml edges | `combined_degree`, `weight` as a float, `text_unit_ids` |
| `text_units` | R3's `output/text_units.parquet` | `entity_ids` / `relationship_ids` re-pointed at the bridged graph |
| `documents` | R3's `output/documents.parquet` | nothing — copied verbatim |

**Why R3's text units are reused rather than rebuilt.** They are a function of the chunk set,
not of the graph: 369 rows, already mapped 369/369 to our chunks by `kg_graph.tables`. Reuse
keeps one text-unit id space across both indexes, which is what lets a bridged report and a
native report cite the same chunk. The two reference columns (`entity_ids`,
`relationship_ids`) are re-pointed at the bridged graph because the native ones name native
uuids that no longer exist — a dangling reference in the *other* direction, and exactly what
`arms`' local search would trip on later.

**Entity names are uppercased, because GraphRAG's own pipeline uppercases them.** Not a style
choice: `graphrag/graphs/stable_lcc.py::_normalize_name` does `html.unescape(name).upper().strip()`
on every edge endpoint before clustering, and `create_communities` then looks the cluster's node
names up in `entities.title` — unnormalised titles do not match, the entity is silently dropped,
and the communities table quietly covers a fraction of the graph. Measured 2026-10-17 on the
first full bridged run: **370 of 4,525 entities reached a community (8%)** against native R3's
2,502 of 2,817 (89%). `graphrag index` exits 0 and no warning is printed, which is why this is
written down here rather than left to be rediscovered. Native R3's titles are all uppercase
(2,817/2,817), so uppercasing is also what makes the two indexes comparable.

**Merging is counted and listed, never silent.** Uppercasing collapses LightRAG's case variants
(`APU`/`Apu`, `FAs`/`Fas`) — 133 groups, 136 names, at the time of writing. Each merge is
recorded with every name in it, and the gate checks `entities + merged == graph nodes`: a merge
nobody listed would show up as a count that no longer adds up. Entity descriptions are joined
with a newline (both were extracted; dropping one loses evidence), `text_unit_ids` are unioned,
and the type is the alphabetically first of the members' types — deterministic, and recorded.

**`frequency` is the number of distinct text units that mention the entity.** It does not
drive the report's ordering (only `degree` does — `graph_context/sort_context.py`), and it
matches what native R3's own table holds (mean frequency 1.745 == mean `len(text_unit_ids)`
1.745, measured 2026-10-17).

**`<SEP>` is the multi-source separator.** 996 of LightRAG's nodes carry several chunk keys
joined by the literal `<SEP>`. Splitting is not optional: a single-string join loses every
mention but the first, the dangling check still passes, and the evidence is quietly thin.

**Text-unit ids are GraphRAG's, not ours.** The graphml names LightRAG chunk keys
(`<docstem>-sha256:<hex>`); the §5.6 id map turns those into our chunk ids; GraphRAG's tables
are keyed by a third id — its own content hash. Two lookups, both asserted; writing our ids into
`text_unit_ids` passes a row count and fails every join downstream.

**What is *not* normalised.** Edge direction. LightRAG stores an undirected graph
(`nx.Graph`, 0 symmetric pairs, 0 self-loops), so each pair appears once; the orientation is the
one LightRAG stored and is preserved because the description reads that way ("SMOKE fault
indication applies with Mod. 30354"). After uppercasing, mirrored pairs can appear; they are
collapsed to the first occurrence's orientation and counted.
"""

from __future__ import annotations

import hashlib
import html
import json
from collections import Counter
from pathlib import Path

import networkx as nx
import pyarrow as pa
import pyarrow.parquet as pq
from lightrag.constants import GRAPH_FIELD_SEP

from hyrag.kg_graph import config, tables
from hyrag.kg_light import config as kg_light_config
from hyrag.kg_light import idmap as kg_light_idmap

# ---------------------------------------------------------------- the target schemas

# Column for column what R3's own tables carry (`nodes/kg-graph.md` §Verified facts 4).
# Written explicitly rather than inferred: an inferred `list<object>` for a column GraphRAG
# reads as `list<string>` fails three workflows later, in a stack trace that names none of this.
ENTITY_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("human_readable_id", pa.int64()),
        ("title", pa.string()),
        ("type", pa.string()),
        ("description", pa.string()),
        ("text_unit_ids", pa.list_(pa.string())),
        ("frequency", pa.int64()),
        ("degree", pa.int64()),
    ]
)

RELATIONSHIP_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("human_readable_id", pa.int64()),
        ("source", pa.string()),
        ("target", pa.string()),
        ("description", pa.string()),
        ("weight", pa.float64()),
        ("combined_degree", pa.int64()),
        ("text_unit_ids", pa.list_(pa.string())),
    ]
)

TEXT_UNIT_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("human_readable_id", pa.int64()),
        ("text", pa.string()),
        ("n_tokens", pa.int64()),
        ("document_id", pa.string()),
        ("entity_ids", pa.list_(pa.string())),
        ("relationship_ids", pa.list_(pa.string())),
        ("covariate_ids", pa.list_(pa.null())),
    ]
)

DOCUMENT_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("human_readable_id", pa.int64()),
        ("title", pa.string()),
        ("text", pa.string()),
        ("text_unit_ids", pa.list_(pa.string())),
        ("creation_date", pa.string()),
        ("raw_data", pa.null()),
    ]
)

SCHEMAS = {
    "entities": ENTITY_SCHEMA,
    "relationships": RELATIONSHIP_SCHEMA,
    "text_units": TEXT_UNIT_SCHEMA,
    "documents": DOCUMENT_SCHEMA,
}

TABLE_NAMES = tuple(SCHEMAS)


# ---------------------------------------------------------------- reading the sources


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source_graph_path() -> Path:
    """LightRAG's own store — the one file the bridge's entities and relationships come from."""
    return kg_light_idmap.graph_path()


def read_source_graph(path: Path | None = None) -> nx.Graph:
    """The graphml, as NetworkX wrote it. Raises on a shape this converter cannot honour.

    The graph being **undirected and simple** is the assumption the whole dedupe story rests
    on: LightRAG writes `nx.Graph`, so one edge per pair. A future version writing a
    `MultiGraph` would silently give GraphRAG two rows for one relationship, and the report
    context would quote the same edge twice — so this fails loudly instead.
    """
    path = path or source_graph_path()
    if not path.is_file():
        raise SystemExit(f"no LightRAG graph at {path} — run the `kg-light` box first")
    graph = nx.read_graphml(path)
    if graph.is_directed() or graph.is_multigraph():
        raise SystemExit(
            f"{path.name} is {'directed' if graph.is_directed() else 'simple'}/"
            f"{'multi' if graph.is_multigraph() else 'single'} — the bridge assumes LightRAG's "
            f"undirected simple graph (one row per pair)"
        )
    return graph


def load_idmap() -> dict:
    """§5.6's map, asserted bijective before anything is converted through it."""
    path = kg_light_config.IDMAP_PATH
    if not path.is_file():
        raise SystemExit(f"no id map at {path} — run `hyrag.kg_light.cli map` first")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not document.get("bijective"):
        raise SystemExit(
            f"{path.name} is not bijective: {document.get('counts')} · "
            f"problems {document.get('problems')} · unmapped {document.get('unmapped_our_chunks')}"
        )
    return document


def load_textunit_map() -> dict:
    """GraphRAG's text-unit id → our chunk id, built by `kg-graph`'s own gate (369/369)."""
    if not config.TEXTUNIT_MAP.is_file():
        raise SystemExit(
            f"no text-unit map at {config.TEXTUNIT_MAP} — run `hyrag.kg_graph.cli check` on the "
            f"native root first (it writes the map as a side effect)"
        )
    document = json.loads(config.TEXTUNIT_MAP.read_text(encoding="utf-8"))
    return document["map"]


def normalize_name(name: str) -> str:
    """GraphRAG's own entity-name normalisation, mirrored from `graphs/stable_lcc.py`.

    Copied rather than imported: it is a private function in a package pinned by rev, and a
    rename there would turn into an ImportError in a run that has already paid for its
    embeddings. The three operations are the contract — unescape, upper, strip — and the gate
    compares the resulting entity count against the graph's node count, so a drift in this
    function shows up as a merge count that no longer adds up.
    """
    return html.unescape(str(name)).upper().strip()


def split_refs(raw) -> list[str]:
    """`<key><SEP><key>` → the chunk keys, in order, without blanks."""
    if raw is None:
        return []
    text = str(raw)
    if not text:
        return []
    return [part for part in (piece.strip() for piece in text.split(GRAPH_FIELD_SEP)) if part]


# ---------------------------------------------------------------- the conversion


def subset(graph: nx.Graph, limit: int) -> nx.Graph:
    """The `limit` highest-degree nodes and the edges among them — a smoke's graph.

    Degree is computed on the full graph first, so the subset is the dense core rather than
    an arbitrary slice: it clusters into several communities and exercises the report prompt,
    which is the point of a smoke run.

    **Built by hand, in sorted node order, instead of `graph.subgraph(keep).copy()`.** The
    NetworkX view's iteration order follows hash-ordered internals and therefore changes with
    `PYTHONHASHSEED` — measured on 2026-10-17: the same 150-node subset yielded a different
    edge *orientation* in three consecutive processes, which flipped half the relationship ids
    and made a run-twice diff fail for a reason that had nothing to do with the bridge. The
    full graph needs none of this (its order comes from the file), but the smoke's does.
    """
    ranking = sorted(graph.degree, key=lambda item: (-item[1], str(item[0])))
    keep = sorted(str(node) for node, _degree in ranking[:limit])
    kept = set(keep)
    trimmed = nx.Graph()
    for node in keep:
        trimmed.add_node(node, **dict(graph.nodes[node]))
    for source, target, attributes in graph.edges(data=True):  # the file's own order
        if source in kept and target in kept:
            trimmed.add_edge(str(source), str(target), **dict(attributes))
    return trimmed


def _graph_facts(graph: nx.Graph) -> dict:
    components = sorted((len(part) for part in nx.connected_components(graph)), reverse=True)
    return {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "components": len(components),
        "largest_component": components[0] if components else 0,
        "isolates": sum(1 for size in components if size == 1),
        "symmetric_pairs": _symmetric_pairs(graph),
        "self_loops": sum(1 for source, target in graph.edges() if source == target),
    }


def _symmetric_pairs(graph: nx.Graph) -> int:
    """How many edges have their reverse present — 0 for LightRAG's undirected store.

    Counted rather than assumed, because it is exactly what would make the ordered-pair
    dedupe below drop a row, and a dropped row is evidence a reader cannot see.
    """
    seen: set[tuple[str, str]] = set()
    mirrored = 0
    for source, target in graph.edges():
        if (str(target), str(source)) in seen:
            mirrored += 1
        seen.add((str(source), str(target)))
    return mirrored


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def build(
    graph: nx.Graph | None = None,
    mapping: dict | None = None,
    textunit_map: dict | None = None,
    native_root: Path | None = None,
    limit: int | None = None,
) -> dict:
    """Convert the graph into the four tables. Returns `{"rows": ..., "meta": ...}`.

    `limit` is the smoke switch: it converts the highest-degree subgraph and marks the result
    as a subset, so no gate can mistake a smoke root's coverage for the corpus's.
    """
    full_graph = graph if graph is not None else read_source_graph()
    mapping = mapping if mapping is not None else load_idmap()["map"]
    textunit_map = textunit_map if textunit_map is not None else load_textunit_map()
    native_root = native_root or config.GRAPH_ROOT

    text_unit_of_chunk: dict[str, str] = {chunk: unit for unit, chunk in textunit_map.items()}
    if len(text_unit_of_chunk) != len(textunit_map):
        raise SystemExit("the text-unit map is not injective — two units claim one chunk")

    source_facts = _graph_facts(full_graph)
    graph = subset(full_graph, limit) if limit else full_graph

    # ---- references: every chunk key the graph names, split on <SEP>, as GraphRAG's id space
    unmapped: set[str] = set()
    chunks_without_unit: set[str] = set()
    nodes_without_source: list[str] = []
    chunk_counts: Counter[str] = Counter()

    def unit_ids(raw) -> list[str]:
        units: list[str] = []
        for key in split_refs(raw):
            chunk = mapping.get(key)
            if chunk is None:
                unmapped.add(key)
                continue
            chunk_counts[chunk] += 1
            unit = text_unit_of_chunk.get(chunk)
            if unit is None:
                chunks_without_unit.add(chunk)
                continue
            if unit not in units:
                units.append(unit)
        return units

    # ---- relationships first: the entity degrees come from the merged edge set
    edge_rows: dict[tuple[str, str], dict] = {}
    merged_edges: list[dict] = []
    non_numeric_weight = 0
    for source, target, attributes in graph.edges(data=True):
        source, target = normalize_name(source), normalize_name(target)
        pair = tuple(sorted((source, target)))
        raw_weight = attributes.get("weight")
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            non_numeric_weight += 1
            weight = 0.0
        units = unit_ids(attributes.get("source_id"))
        description = str(attributes.get("description") or "").strip()

        existing = edge_rows.get(pair)
        if existing is None:
            edge_rows[pair] = {
                # Deterministic where native uses `uuid4()`: the pair is the identity, and a
                # run-twice diff has to be able to say "same row" without a random string.
                "id": hashlib.sha512(f"{pair[0]}|{pair[1]}".encode()).hexdigest(),
                "human_readable_id": 0,  # assigned after sorting, below
                "source": source,
                "target": target,
                "description": description,
                "weight": weight,
                "combined_degree": 0,
                "text_unit_ids": units,
            }
            continue
        # Two case variants of the same pair, or the mirrored orientation. Merged, and named.
        merged_edges.append({"pair": list(pair), "kept": [existing["source"], existing["target"]]})
        _extend_unique(existing["text_unit_ids"], units)
        if description and description not in existing["description"]:
            existing["description"] = "\n".join(filter(None, [existing["description"], description]))
        existing["weight"] = max(existing["weight"], weight)

    degree: Counter[str] = Counter()
    for row in edge_rows.values():
        degree[row["source"]] += 1
        degree[row["target"]] += 1

    relationship_rows = sorted(edge_rows.values(), key=lambda row: (row["source"], row["target"]))
    for index, row in enumerate(relationship_rows):
        row["human_readable_id"] = index
        row["combined_degree"] = degree[row["source"]] + degree[row["target"]]

    # ---- entities: GraphRAG's normalisation, every merge named
    by_name: dict[str, list[str]] = {}
    for raw in sorted(str(node) for node in graph.nodes()):
        by_name.setdefault(normalize_name(raw), []).append(raw)
    merge_groups = [
        {"id": name, "names": raws} for name, raws in sorted(by_name.items()) if len(raws) > 1
    ]

    entity_rows: list[dict] = []
    for name in sorted(by_name):
        units: list[str] = []
        descriptions: list[str] = []
        types: list[str] = []
        for raw in by_name[name]:
            attributes = graph.nodes[raw]
            if not attributes.get("source_id"):
                nodes_without_source.append(raw)
            _extend_unique(units, unit_ids(attributes.get("source_id")))
            description = str(attributes.get("description") or "").strip()
            if description and description not in descriptions:
                descriptions.append(description)
            types.append(str(attributes.get("entity_type") or "UNKNOWN"))
        entity_rows.append(
            {
                "id": name,
                "human_readable_id": len(entity_rows),
                "title": name,
                "type": sorted(types)[0],
                "description": "\n".join(descriptions),
                "text_unit_ids": units,
                "frequency": len(units),  # distinct text units mentioning the entity
                "degree": degree.get(name, 0),
            }
        )

    merged_names = sum(len(group["names"]) - 1 for group in merge_groups)
    source_nodes = graph.number_of_nodes()
    if len(entity_rows) + merged_names != source_nodes:
        raise SystemExit(
            f"entity accounting is broken: {len(entity_rows)} rows + {merged_names} merged "
            f"names != {source_nodes} graph nodes"
        )

    # ---- text units: R3's ids and text kept, the two reference columns re-pointed
    native_units = tables.read_table(native_root, "text_units")
    native_documents = tables.read_table(native_root, "documents")
    if not native_units:
        raise SystemExit(
            f"{native_root}/output/text_units.parquet is missing or empty — run the `kg-graph` "
            f"box first; the bridge reuses its text-unit id space"
        )

    entities_by_chunk: dict[str, list[str]] = {}
    for row in entity_rows:
        for unit in row["text_unit_ids"]:
            entities_by_chunk.setdefault(textunit_map[unit], []).append(row["id"])
    relationships_by_chunk: dict[str, list[str]] = {}
    for row in relationship_rows:
        for unit in row["text_unit_ids"]:
            relationships_by_chunk.setdefault(textunit_map[unit], []).append(row["id"])

    text_unit_rows: list[dict] = []
    unknown_text_units: list[str] = []
    for row in native_units:
        chunk = textunit_map.get(str(row["id"]))
        if chunk is None:
            unknown_text_units.append(str(row["id"]))
            continue
        text_unit_rows.append(
            {
                "id": str(row["id"]),
                "human_readable_id": int(row["human_readable_id"]),
                "text": str(row["text"]),
                "n_tokens": int(row["n_tokens"]),
                "document_id": str(row["document_id"]),
                "entity_ids": entities_by_chunk.get(chunk, []),
                "relationship_ids": relationships_by_chunk.get(chunk, []),
                "covariate_ids": [],
            }
        )

    document_rows = [
        {
            "id": str(row["id"]),
            "human_readable_id": int(row["human_readable_id"]),
            "title": str(row["title"]),
            "text": str(row["text"]),
            "text_unit_ids": [str(unit) for unit in (row.get("text_unit_ids") or [])],
            "creation_date": row.get("creation_date"),
            "raw_data": None,
        }
        for row in native_documents
    ]

    meta = {
        "smoke": bool(limit),
        "smoke_limit": limit,
        "source_graph": {
            "path": str(source_graph_path()),
            "sha256": sha256_file(source_graph_path()),
            **source_facts,
            **({"subset_nodes": graph.number_of_nodes(), "subset_edges": graph.number_of_edges()}
               if limit else {}),
        },
        "normalisation": {
            "rule": "html.unescape(name).upper().strip()",
            "why": "graphrag/graphs/stable_lcc.py applies it to every edge endpoint; titles must match",
        },
        "references": {
            "total": sum(chunk_counts.values()),
            "distinct": len(chunk_counts),
            "unmapped": sorted(unmapped),
            "chunks_without_text_unit": sorted(chunks_without_unit),
            "nodes_without_source_id": nodes_without_source[:10],
            "chunks_covered": len(chunk_counts),
        },
        "merges": {
            "entities": {"count": merged_names, "groups": merge_groups},
            "relationships": {"count": len(merged_edges), "groups": merged_edges[:50]},
        },
        "name_variants": {
            "case_groups": len(merge_groups),
            "examples": [group["names"] for group in merge_groups[:15]],
        },
        "edges": {
            "non_numeric_weight": non_numeric_weight,
            "weight_min": min((row["weight"] for row in relationship_rows), default=0.0),
            "weight_max": max((row["weight"] for row in relationship_rows), default=0.0),
        },
        "text_units": {
            "native_rows": len(native_units),
            "kept": len(text_unit_rows),
            "unknown": unknown_text_units[:10],
        },
        "counts": {
            "entities": len(entity_rows),
            "relationships": len(relationship_rows),
            "text_units": len(text_unit_rows),
            "documents": len(document_rows),
        },
    }
    rows = {
        "entities": entity_rows,
        "relationships": relationship_rows,
        "text_units": text_unit_rows,
        "documents": document_rows,
    }
    return {"rows": rows, "meta": meta}


def write_tables(root: Path, rows: dict[str, list[dict]]) -> None:
    """Write the four tables into `<root>/output/` with the schemas above."""
    output = root / "output"
    output.mkdir(parents=True, exist_ok=True)
    for name in TABLE_NAMES:
        table = pa.Table.from_pylist(rows[name], schema=SCHEMAS[name])
        pq.write_table(table, output / f"{name}.parquet")
