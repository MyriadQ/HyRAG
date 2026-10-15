"""kg_light/checks.py — the node's gate, as one command.

    uv run python -m hyrag.kg_light.cli check

The gate (`ROADMAP.md`, `nodes/kg-light.md`):

> a query returns entities, relations and chunks; **and** the id map is bijective, with
> zero unmapped references

Four checks, each printing a number rather than a verdict:

1. **the map** — rebuilt from LightRAG's own rows and compared to the ingest set by content.
   Bijective means both directions: every stored key resolves to one of our chunks, and every
   one of our chunks is present. The second direction is the one a row count hides.
2. **unmapped references** — every `source_id` in the persisted graph, split on LightRAG's
   field separator, looked up in the map. **Zero is the answer, and it is printed as a count.**
3. **vector dimension** — the `vector(1024)` column check the endpoint's dimension must
   satisfy; read from the catalogue, not from a config file.
4. **a query per class** — retrieval actually returns entities, relationships and chunks.
   The gate's first half is about the index existing, not about answer quality: the counts
   are printed, and quality is `judge`'s business, not this node's.
"""

from __future__ import annotations

import json

from hyrag.baseline.store import connect
from hyrag.kg_light import config, idmap, ingest, query

CLASSES = ("factoid", "multi-hop", "thematic")


def check_map() -> tuple[bool, dict]:
    """Rebuild the map from storage and assert bijectivity in both directions."""
    document = idmap.build()
    counts = document["counts"]
    problems = document["problems"]
    ok = bool(document["bijective"])
    print(
        f"  {'✓' if ok else '✗'} map: {counts['lightrag_chunks']} LightRAG keys ↔ "
        f"{counts['our_chunks']} our chunks · mapped {counts['mapped']} · "
        f"unresolved {len(problems['unresolved'])} · mismatched file_path {len(problems['file_path_mismatch'])}"
    )
    for problem in problems["unresolved"][:5]:
        print(f"      unresolved: {problem}")
    for problem in document["unmapped_our_chunks"][:5]:
        print(f"      our chunk never stored: {problem}")
    return ok, document


def check_source_ids() -> bool:
    """Enumerate every reference the graph holds; zero unmapped is the gate."""
    result = idmap.unmapped_source_ids()
    ok = not result["unmapped"] and not result["attributes_without_source_id"]
    print(
        f"  {'✓' if ok else '✗'} source_id: {result['node_references']} node + "
        f"{result['edge_references']} edge = {result['total_references']} references "
        f"({result['distinct']} distinct) · unmapped {len(result['unmapped'])} · "
        f"without source_id {len(result['attributes_without_source_id'])}"
    )
    for reference in result["unmapped"][:5]:
        print(f"      unmapped: {reference}")
    for attribute in result["attributes_without_source_id"][:5]:
        print(f"      no source_id: {attribute}")
    return ok


def check_vector_dimension() -> bool:
    """Every vector column LightRAG created must be `vector(1024)` — read from the catalogue."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) LIKE 'vector%'
            ORDER BY c.relname, a.attname
            """
        )
        columns = cur.fetchall()

    expected = f"vector({config.EMBEDDING_DIM})"
    ok = bool(columns) and all(kind == expected for _table, _column, kind in columns)
    print(f"  {'✓' if ok else '✗'} vector columns: {len(columns)} found · expected {expected}")
    for table, column, kind in columns:
        flag = "" if kind == expected else "  <- wrong dimension"
        print(f"      {table}.{column} = {kind}{flag}")
    return ok


def questions() -> list[dict]:
    """One question per class, the first of each in file order — deterministic, not cherry-picked."""
    with (config.REPO / "qset" / "questions.jsonl").open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    picked = []
    for label in CLASSES:
        picked.append(next(record for record in records if record["class"] == label))
    return picked


async def check_queries(mode: str | None = None) -> tuple[bool, list[dict]]:
    """One question per class through the row's query path: retrieval must be non-empty."""
    from hyrag.kg_light.builder import build_rag

    rag = build_rag()
    await rag.initialize_storages()
    records: list[dict] = []
    ok = True
    try:
        for question in questions():
            record = await query.ask(rag, question["question"], mode=mode)
            record["question_id"] = question["id"]
            record["class"] = question["class"]
            record = query.resolve(record)
            records.append(record)
            counts = record["counts"]
            good = counts["entities"] > 0 and counts["relationships"] > 0 and counts["chunks"] > 0
            ok = ok and good and not record["unresolved"]
            print(
                f"  {'✓' if good else '✗'} {question['id']:<9} {question['class']:<9} "
                f"entities {counts['entities']:>3} · relations {counts['relationships']:>3} · "
                f"chunks {counts['chunks']:>3} · {record['latency_ms']:>6} ms · "
                f"unresolved keys {len(record['unresolved'])}"
            )
            print(f"      {record['question'][:110]}")
    finally:
        await rag.finalize_storages()
    query.write(records)
    return ok, records


async def run(mode: str | None = None) -> int:
    """The gate. Prints every number, returns a process exit code."""
    print("\n  kg-light gate\n")
    map_ok, _document = check_map()
    references_ok = check_source_ids()
    dimensions_ok = check_vector_dimension()

    cost = ingest.cost_record()
    if cost:
        print(
            f"  · extraction: {cost['llm_calls']} LLM calls · "
            f"{cost['total_tokens']:,} tokens · {cost['wall_clock_s']}s wall clock "
            f"over {cost['chunks']} chunks / {cost['documents']} documents"
        )

    queries_ok, _records = await check_queries(mode)

    gate = map_ok and references_ok and dimensions_ok and queries_ok
    print(f"\n  gate: {'GREEN' if gate else 'RED'}\n")
    return 0 if gate else 1
