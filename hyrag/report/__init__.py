"""report — the written deliverable and the checks that keep it honest.

`REPORT.md` is the thing an examiner reads; this package is what stops it from drifting away
from the artifacts. Three checks, all mechanical: every path it names exists; every pinned
version matches the environment; every headline number appears in the artifact it cites (and
the total spend is recomputed from the proxy log rather than trusted).
"""
