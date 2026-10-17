"""arms/bridged.py — the `kg-graph-bridged` arm: GraphRAG's own answer over the bridged index.

Same settings as R3, on purpose (`PREPLAN.md` §7.4): thematic → `global` with **dynamic
community selection at level 2**, factoid/multi-hop → `local`. An ablation that changed the
search settings would compare settings, not indexes, and the delivered system would be a
different system from the one the benchmark scored.

The arm drives `graphrag query` as a subprocess — GraphRAG cannot share an interpreter with
`docling` — and reads back three things: the answer (the text after the last log line), the
`[Data: …]` markers resolved through `kg_graph.resolve` into `file, §section, p. N–M`, and the
tokens windowed from the proxy log under the **`kg-graph-bridge`** row tag, which is what the
bridged root's settings write on every completion.

Two facts about this arm that `fuse` must not discover later:

* **It is not ranked.** Global search returns a map-reduced answer and a set of report ids; the
  context arrives truncated and without scores. The record says `ranked: False`.
* **It is expensive.** 932 reports go through the dynamic-selection rating pass, ~1.4× R3's
  native 357.8 s / $0.380 per thematic question. That is why the router's shipped thematic
  route is `kg-light` and this arm appears in the ablation and in multi-hop fusion.
"""

from __future__ import annotations

import time

from hyrag import llm_log
from hyrag.arms.record import AnswerRecord
from hyrag.kg_graph import checks as kg_graph_checks
from hyrag.kg_graph import config as kg_graph_config
from hyrag.kg_graph import resolve, run
from hyrag.kg_graph.bridge import root as bridge_root

# R3's benchmark mapping, kept: the delivered arm must be the row that was measured.
METHODS = {"thematic": "global", "multi-hop": "local", "factoid": "local"}


class BridgedArm:
    name = "kg-graph-bridged"
    citation_unit = "file, §section, p. N–M"
    ranked = False

    def __init__(self, root=None):
        self.root = root or bridge_root.BRIDGE_ROOT

    def answer(self, question: dict) -> AnswerRecord:
        method = METHODS[question["class"]]
        marker = llm_log.log_marker()
        started = time.perf_counter()
        stdout = run.query(
            self.root,
            question["question"],
            method=method,
            log_path=self.root / "logs" / "arms.stdout.log",
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        answer = kg_graph_checks._answer_from_stdout(stdout)
        resolution = resolve.resolve(answer, self.root)
        usage = llm_log.usage_since(marker, row=bridge_root.ROW_TAG)
        pending = [problem for problem in resolution["unresolved"] if problem.get("kind") != "text_unit"]
        return AnswerRecord(
            arm=self.name,
            question_id=question["id"],
            question_class=question["class"],
            question=question["question"],
            answer=answer,
            latency_ms=latency_ms,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            calls=int(usage.get("calls") or 0),
            citations=tuple(resolution["citations"]),
            citation_unit=self.citation_unit,
            evidence_chunk_ids=tuple(resolution["evidence_chunk_ids"]),
            unresolved=tuple(pending),
            method=method + (f"+dynamic+level{kg_graph_config.COMMUNITY_LEVEL}"
                             if method == "global" else ""),
            ranked=self.ranked,
            raw={
                "stdout_tail": stdout[-1200:],
                "resolution": {key: resolution[key] for key in
                               ("markers", "ids", "distinct_ids", "resolved_ids",
                                "truncated_markers", "unresolved")},
            },
        )
