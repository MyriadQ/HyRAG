"""bridge/checks.py — the node's gate, as one command.

    uv run python -m hyrag.kg_graph.bridge.cli check --against parsed/kg_graph_bridged/run1

The gate (`nodes/bridge.md`):

> run twice, diff, **identical**; zero dangling `text_unit_ids`; no silent merging of a known
> distinct entity pair

Each clause prints a number rather than a verdict, and the diff names **which table and which
rows** — a gate that says "different" is not a gate.

Three things this gate deliberately does not pretend:

* **`communities.id` is excluded from the diff.** `create_communities` assigns
  `str(uuid4())` to every community (`graphrag/index/workflows/create_communities.py`), so a
  byte comparison fails on every run for a reason that says nothing about the bridge. The
  column is excluded, the archive is **not** rewritten, and the excluded count is printed.
  Every other cell of every other table is compared, and `community_reports.id` — a sha512 of
  `full_content` — *is* compared, because it is deterministic and carries the report text.
* **A cached report pass is good evidence, not a shortcut.** Run two's reports come from
  GraphRAG's response cache: the cache key is a hash of the request, so a hit means run one and
  run two sent byte-identical requests. The proxy-call count for run two is printed beside the
  community count, and the reader can see that the identity is the *inputs'*, not the
  sampler's. Re-rolling the LLM would test DeepSeek's determinism, which is not this box.
* **Coverage is a number, not a pass.** `use_lcc: True` (GraphRAG's default, and R3's setting)
  drops everything outside the largest connected component: 684 of LightRAG's 4,525 entities,
  514 of them isolates. It is recorded so nobody reads the bridged arm's answers as if the
  whole graph were reachable — and left alone, because changing it would make §7.4 compare
  clustering settings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from hyrag.kg_graph import config, tables
from hyrag.kg_graph.bridge import convert
from hyrag.kg_graph.bridge import root as bridge_root
from hyrag.kg_graph.bridge import run as bridge_run
from hyrag.kg_light import config as kg_light_config

# What the community layer writes, plus the tables the bridge wrote into it.
DIFF_TABLES = ("entities", "relationships", "text_units", "documents", "communities", "community_reports")

# The row key per table. Communities are keyed by (level, community) — their `id` is random —
# and everything else by its own id, which the bridge derives from content.
ROW_KEYS: dict[str, tuple[str, ...]] = {
    "entities": ("id",),
    "relationships": ("id",),
    "text_units": ("id",),
    "documents": ("id",),
    "communities": ("level", "community"),
    "community_reports": ("id",),
}

# The one column a run-twice diff cannot compare, with the reason printed beside it.
EXCLUDED: dict[str, tuple[str, ...]] = {"communities": ("id",)}


# ---------------------------------------------------------------- helpers


def _rows(directory: Path, name: str) -> list[dict]:
    """Rows of one table from a root (`<dir>/output/…`) or a snapshot directory."""
    path = directory / "output" / f"{name}.parquet"
    if not path.is_file():
        path = directory / f"{name}.parquet"
    if not path.is_file():
        return []
    return pq.read_table(path).to_pylist()


def _key(row: dict, fields: tuple[str, ...]) -> tuple:
    return tuple(row.get(field) for field in fields)


def _row_id(row: dict) -> str:
    return str(row.get("id") or row.get("title") or "?")


# ---------------------------------------------------------------- clause 1: the tables


def check_tables(root: Path, manifest: dict) -> tuple[bool, dict[str, int]]:
    counts = tables.counts(root)
    expected = manifest["counts"]
    problems: list[str] = []
    for name in convert.TABLE_NAMES:
        if counts.get(name, 0) != expected[name]:
            problems.append(f"{name}: wrote {expected[name]}, root holds {counts.get(name, 0)}")
    for name in ("communities", "community_reports"):
        if counts.get(name, 0) == 0:
            problems.append(f"{name}: not written by the run")
    ok = not problems
    print(
        f"  {'✓' if ok else '✗'} tables: "
        + " · ".join(f"{name} {count}" for name, count in counts.items())
    )
    if problems:
        for problem in problems:
            print(f"      {problem}")
    return ok, counts


# ---------------------------------------------------------------- clause 2: provenance


def check_provenance(root: Path) -> tuple[bool, dict]:
    """Every `text_unit_ids` entry resolves to one of our 369 chunks — both directions."""
    units = {str(row["id"]) for row in tables.read_table(root, "text_units")}
    mapping = json.loads(config.TEXTUNIT_MAP.read_text(encoding="utf-8"))["map"]
    ours = {record["chunk_id"] for record in kg_light_config.our_chunks()}

    dangling: list[str] = []
    referenced: set[str] = set()
    for table in ("entities", "relationships", "communities", "community_reports"):
        for row in tables.read_table(root, table):
            for unit in row.get("text_unit_ids") or []:
                referenced.add(str(unit))
                if str(unit) not in units:
                    dangling.append(f"{table} {_row_id(row)} → text unit {unit} (not in this root)")
                elif str(unit) not in mapping:
                    dangling.append(f"{table} {_row_id(row)} → text unit {unit} (no chunk mapping)")
                elif mapping[str(unit)] not in ours:
                    dangling.append(f"{table} {_row_id(row)} → {mapping[str(unit)]} (chunk not in ingest)")

    entity_ids = {str(row["id"]) for row in tables.read_table(root, "entities")}
    relationship_ids = {str(row["id"]) for row in tables.read_table(root, "relationships")}
    reverse: list[str] = []
    for row in tables.read_table(root, "text_units"):
        for entity_id in row.get("entity_ids") or []:
            if str(entity_id) not in entity_ids:
                reverse.append(f"text unit {row['id']} → entity {entity_id} (not in entities)")
        for relationship_id in row.get("relationship_ids") or []:
            if str(relationship_id) not in relationship_ids:
                reverse.append(f"text unit {row['id']} → relationship {relationship_id} (not in relationships)")

    # What global search can actually reach. The chain is the one `resolve.py` walks:
    # an answer cites `[Data: Reports (n)]` → `communities[human_readable_id=n].text_unit_ids`
    # → our chunk. `community_reports` itself carries no text units — the communities table is
    # where the evidence lives, which is why the marker resolves against *it*.
    report_chunks = {
        mapping[str(unit)]
        for row in tables.read_table(root, "communities")
        for unit in (row.get("text_unit_ids") or [])
        if str(unit) in mapping
    }
    ok = not dangling and not reverse
    print(
        f"  {'✓' if ok else '✗'} provenance: {len(referenced)} text units referenced · "
        f"{len(report_chunks)}/{len(ours)} chunks reachable from reports · "
        f"dangling {len(dangling)} · reverse-dangling {len(reverse)}"
    )
    for problem in (dangling + reverse)[:5]:
        print(f"      ! {problem}")
    return ok, {
        "referenced_text_units": len(referenced),
        "reachable_chunks": len(report_chunks),
        "dangling": len(dangling),
        "reverse_dangling": len(reverse),
    }


# ---------------------------------------------------------------- clause 3: no silent merges


def check_merges(root: Path, manifest: dict, record: dict | None) -> tuple[bool, dict]:
    """No merge without a name in the record — and the arithmetic has to close.

    Uppercasing (GraphRAG's own normalisation) collapses case variants, so merges are expected.
    What the gate forbids is a merge nobody can see: `entities + merged_names` must equal the
    source graph's node count, and the sum of the listed groups' lost names must equal the count
    claimed. A single unrecorded merge breaks the first identity; a list that disagrees with the
    claim breaks the second.
    """
    source = manifest["source_graph"]
    merges = manifest["merges"]
    entities = len(tables.read_table(root, "entities"))
    # A smoke root converts a subset, so "one row per node" is the subset's node count.
    expected = source.get("subset_nodes", source["nodes"])
    entity_merges = merges["entities"]
    groups = entity_merges["groups"]
    counted = entity_merges["count"]
    named = sum(len(group["names"]) - 1 for group in groups)
    edge_merges = merges["relationships"]

    ok = named == counted and entities + counted == expected
    print(
        f"  {'✓' if ok else '✗'} merges: {counted} entity names merged in {len(groups)} groups "
        f"(all listed) · {entities} entities from {expected} graph nodes · "
        f"{edge_merges['count']} relationship pairs merged"
    )
    for group in groups[:5]:
        print(f"      merged {' / '.join(group['names'])} → {group['id']}")
    if groups:
        print(f"      … {len(groups)} groups in the run record (`merges.entities.groups`)")
    for group in edge_merges["groups"][:3]:
        print(f"      merged edge {group['pair']} (kept orientation {group['kept']})")
    if not ok:
        print(f"      ! accounting: listed {named}, claimed {counted}, entities {entities} + "
              f"{counted} != {expected}")
    return ok, {
        "entities": entities,
        "expected": expected,
        "counted": counted,
        "named": named,
        "groups": len(groups),
    }


# ---------------------------------------------------------------- clause 4: run twice


def check_determinism(root: Path, against: Path) -> tuple[bool, dict]:
    """Content diff of two runs, modulo row order — every table, every cell, except one column."""
    results: dict[str, dict] = {}
    ok = True
    for name in DIFF_TABLES:
        left = _rows(root, name)
        right = _rows(against, name)
        if not left or not right:
            ok = False
            results[name] = {"missing": name not in {p.stem for p in (against / "output").glob("*.parquet")}}
            print(f"  ✗ diff {name}: one side is empty ({len(left)} vs {len(right)} rows)")
            continue

        ignored = EXCLUDED.get(name, ())
        key_fields = ROW_KEYS[name]
        left_by_key = {_key(row, key_fields): row for row in left}
        right_by_key = {_key(row, key_fields): row for row in right}

        missing = sorted(set(left_by_key) - set(right_by_key), key=str)
        extra = sorted(set(right_by_key) - set(left_by_key), key=str)
        differing: list[tuple[tuple, list[str]]] = []
        for key in left_by_key.keys() & right_by_key.keys():
            columns = _different_columns(left_by_key[key], right_by_key[key], ignored)
            if columns:
                differing.append((key, columns))

        table_ok = not missing and not extra and not differing
        ok = ok and table_ok
        results[name] = {
            "rows": len(left),
            "missing": [str(key) for key in missing[:5]],
            "extra": [str(key) for key in extra[:5]],
            "differing": len(differing),
            "columns": sorted({column for _key_, columns in differing for column in columns}),
            "excluded": list(ignored),
        }
        detail = f"{len(left)} rows · identical" if table_ok else (
            f"{len(left)} rows · missing {len(missing)} · extra {len(extra)} · differing {len(differing)}"
        )
        print(f"  {'✓' if table_ok else '✗'} diff {name}: {detail}")
        if ignored:
            print(f"      excluded {', '.join(ignored)} — uuid4 per run in graphrag {config.GRAPHRAG_PIN}")
        for key, columns in differing[:3]:
            print(f"      ! {key}: columns {', '.join(columns)}")
            for column in columns[:2]:
                print(f"          run1 {column} = {left_by_key[key].get(column)!r}")
                print(f"          run2 {column} = {right_by_key[key].get(column)!r}")
        for key in missing[:3]:
            print(f"      ! only in run1: {key}")
        for key in extra[:3]:
            print(f"      ! only in run2: {key}")
    return ok, results


def _different_columns(left: dict, right: dict, ignored: tuple[str, ...]) -> list[str]:
    columns = []
    for column in sorted(set(left) | set(right)):
        if column in ignored:
            continue
        if left.get(column) != right.get(column):
            columns.append(column)
    return columns


# ---------------------------------------------------------------- tokens


def check_tokens(record: dict) -> tuple[bool, dict]:
    """Print what the community layer cost, on both passes — the cache as evidence.

    Run one pays; run two is served from GraphRAG's response cache, whose key is a hash of the
    request. So run two's proxy-call count is a *measurement*: it says how many of the report
    prompts were re-sent unchanged. A count near zero beside a full set of identical reports is
    the strongest determinism evidence this box can produce without paying for a third run.
    """
    final = record.get("tokens") or {}
    previous = (record.get("previous_run") or {}).get("tokens") or {}
    paid, label = (previous, "run one") if previous else (final, "run")
    communities = (record.get("tables") or {}).get("communities", 0)

    ok = int(paid.get("llm_calls") or 0) > 0 and int(paid.get("total_tokens") or 0) > 0
    print(
        f"  {'✓' if ok else '✗'} tokens ({label}): {paid.get('llm_calls', 0)} calls · "
        f"prompt {paid.get('prompt_tokens', 0):,} · completion {paid.get('completion_tokens', 0):,} · "
        f"total {paid.get('total_tokens', 0):,} · cache hit {paid.get('prompt_cache_hit_tokens', 0):,} · "
        f"repaired {paid.get('rewritten', 0)} · errors {paid.get('errors', 0)} · "
        f"untagged {paid.get('untagged_calls', 0)}"
    )
    if previous:
        calls = int(final.get("llm_calls") or 0)
        print(
            f"  \u00b7 tokens (run two): {calls} calls for {communities} communities · "
            f"{max(communities - calls, 0)} report(s) served from the response cache — the cache key "
            f"is the request, so a hit is evidence the two runs sent identical prompts"
        )
    cost = record.get("cost") or {}
    if cost:
        print(
            f"  \u00b7 cost: {cost.get('paid_run_usd')} USD for run one + "
            f"{cost.get('final_run_usd')} for run two = {cost.get('total_usd')} USD "
            f"({cost.get('rate_card')})"
        )
    previous_run = record.get("previous_run") or {}
    if previous_run:
        print(
            f"  \u00b7 wall clock: run one {previous_run.get('wall_clock_s')}s · "
            f"run two {record.get('wall_clock_s')}s · exit {previous_run.get('exit_code')}/"
            f"{record.get('exit_code')}"
        )
    return ok, {"paid": paid, "final": final}


# ---------------------------------------------------------------- the gate


def run_gate(root: Path, *, against: Path | None = None) -> int:
    manifest = bridge_root.manifest(root)
    record = bridge_run.load_record(root)
    print(f"\n  bridge gate · root {root}" + ("  [smoke]" if manifest.get("smoke") else ""))
    print(
        f"  · graph {manifest['source_graph']['sha256'][:12]} · "
        f"{manifest['source_graph']['nodes']} nodes / {manifest['source_graph']['edges']} edges · "
        f"settings {manifest['settings_sha256'][:12]} → root copy {manifest['settings_root_sha256'][:12]}"
    )
    print(f"  · overrides {manifest['overrides']}")

    tables_ok, _counts = check_tables(root, manifest)
    provenance_ok, _prov = check_provenance(root)
    merges_ok, _merges = check_merges(root, manifest, record)
    diff_ok: bool | None = None
    if against is not None:
        print(f"  · diff against {against}")
        diff_ok, _diff = check_determinism(root, against)
    tokens_ok, _tokens = check_tokens(record) if record else (False, {})
    if record is None:
        print("  ✗ no run record — run `bridge index` before the gate")

    complete = diff_ok is not None
    green = tables_ok and provenance_ok and merges_ok and tokens_ok and diff_ok is True
    if not complete:
        print("\n  gate: INCOMPLETE — the live clauses pass, but `run twice, diff, identical` "
              "has not been checked (pass --against <snapshot>)")
    print(f"\n  gate: {'GREEN' if green else 'RED'}\n")
    return 0 if green else 1
