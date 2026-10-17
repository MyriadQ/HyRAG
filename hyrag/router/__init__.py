"""router — one turn plus history, in; one strategy class, out.

The delivered system's first decision, made **before any retrieval happens** (D5): which
strategy answers this turn, and what standalone query the arms will retrieve against (D6).

Two artifacts, deliberately different in kind:

* **the classifier** — `standalone query → {factoid, multi-hop, thematic}`, one call, hashed;
* **the strategy table** — `class → {arms, fuse}`, **data**, read off `results/class_strategy.json`
  and chosen under a stated cost/latency budget, so it can be ablated (D5).

Why the budget is part of the table rather than an implementation detail: the benchmark's
pairwise winner wins every class and costs **357.8 s and $0.380** on the thematic one
(`results/class_strategy.json`). A route is a choice under a budget; a table without one is a
preference wearing a measurement's clothes. The chosen envelope is **≤ 60 s · ≤ $0.02 per
turn**, which excludes that cell by construction — and that exclusion is a finding the report
states, not a setting hidden in code.

Modules:

| module | job |
|---|---|
| `config.py` | the frozen 120/30 split, the qset loader, paths, the model's endpoint |
| `model.py` | one completion through the local proxy, thinking off, row tag `router` |
| `condense.py` | turn + history → one standalone query |
| `classify.py` | standalone query → exactly one class label, with a deterministic fallback |
| `strategy.py` | the table and the budget it was chosen under |
| `bench.py` | the gate: held-out accuracy with a confusion matrix, and the 3-turn condense test |
| `cli.py` | `split · table · classify · eval · dialogue · check` |
"""
