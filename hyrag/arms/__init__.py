"""arms — one interface over two retrieval systems, one record shape.

The router decides a strategy; `fuse` merges evidence; `answer` writes the turn. All three need
the same thing from the two retrieval systems: **a record that says what was retrieved, what it
came from, and what it cost.** LightRAG returns `(result, data, metadata)` from an in-process
call; GraphRAG returns a CLI stdout block with `[Data: …]` markers. This package is the adapter
layer, and the place where per-query cost and latency become fields instead of folklore.

An arm is handed a question and its class, and it answers. It never sees the router's strategy,
the other arm, or a fusion decision — those belong upstream.

| module | job |
|---|---|
| `record.py` | the one record shape both arms emit, and both `fuse` and the judge read |
| `lightrag.py` | the `kg-light` arm: `mix`, citations at **file** granularity, chunk keys through §5.6 |
| `bridged.py` | the `kg-graph-bridged` arm: R3's settings over the bridged root, canonical citations |
| `runner.py` | one command over a question set, one JSONL per arm, resume by question id |
| `checks.py` | the gate: each arm answers its class, with answer · citations · evidence · tokens · latency |
| `cli.py` | `answer · check` |
"""
