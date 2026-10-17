"""bridge — GraphRAG's community layer, run over LightRAG's graph (BYOG).

The delivered system needs a second arm and cannot pay for a second extraction pass (D1,
D2, D4). Phase 1 measured what the second pass costs — 2,655 calls / 5.94 M tokens / 25.5 min
for R3's extraction (`nodes/kg-graph.md`) — so Phase 2 runs GraphRAG's **community layer
only**: communities, reports, vectors, over the entities and relationships LightRAG already
extracted.

Why a workflow subset rather than a flag: `graphrag/index/workflows/factory.py` reads
`config.workflows or cls.pipelines[method]`, so a top-level `workflows:` list in
`settings.yaml` replaces the entire pipeline. The three workflows kept here
(`create_communities`, `create_community_reports`, `generate_text_embeddings`) read their
inputs from the root's parquet files, which means the bridge's job is exactly this:

    LightRAG graphml ──► entities / relationships parquet ──► create_communities
    R3's text_units  ──► text_units / documents parquet   ──► create_community_reports
                                                          └─► generate_text_embeddings

`finalize_graph` does **not** run, so the columns it would have computed — `degree`,
`combined_degree`, `frequency` — are written by this package or the reports degrade
silently (the report context sorts by exactly those).

Modules:

| module | job |
|---|---|
| `convert.py` | the converter: graphml + id map + R3's text units → the four input tables |
| `root.py` | a bridged root: the tables, plus a *patched copy* of `settings.yaml` |
| `run.py` | `graphrag index` as a subprocess, and the run record |
| `checks.py` | the node's gate: provenance, merges, run-twice diff, tokens |
| `cli.py` | `python -m hyrag.kg_graph.bridge.cli {prepare,index,counts,check}` |
"""
