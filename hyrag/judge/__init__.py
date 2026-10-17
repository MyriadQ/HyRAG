"""judge/__init__.py — Phase 1's last box: four rows, three classes, one reproducible table.

Import order matters here for one reason: DeepEval reads its telemetry and dotenv settings
when its own modules are imported, so the opt-out has to be set before anything under
`deepeval` is touched. The corpus holds © OEM material whose licence forbids putting it on a
network that is not ours (D8c); the judge must not be the component that forgets.

Modules:

* `config`   — rows, paths, rates, replicates; the run's shape, and nothing else's
* `records`  — three rows' answer files in one shape, with the citation report attached
* `citations`— the deterministic resolution (D8b), three adapters, en dash folded
* `model`    — the judge model: DeepSeek through our own proxy, JSON mode, thinking off
* `runner`   — the blind, shuffled, replicate loop — pointwise, pairwise, and the control
* `table`    — `results.csv`, the pin record, and the summary tables the report cites
"""

from __future__ import annotations

import os

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
# The proxy is plain HTTP on localhost and the corpus is licence-bound: no evaluator trace,
# no upload, no `deepeval login` — ever (README → Landmines).
os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")
