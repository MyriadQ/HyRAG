"""fuse/checks.py — the node's gate: both systems in the fused top-k, and the reranker moves it.

    uv run python -m hyrag.fuse.cli check

The gate (`ROADMAP.md`, `nodes/fuse.md`):

> the fused top-k carries evidence from both systems on multi-hop queries; the reranker
> reorders it

Three dev multi-hop questions, both arms run live through `hyrag.arms`, then fused. Each
question prints the fused top-5 with both arms' ranks, the RRF and rerank scores, and four
checks:

1. **both arms in the top-10** — the fusion clause, counted per arm rather than asserted;
2. **the reranker reorders** — positions changed against the RRF order; zero fails;
3. **provenance** — every shortlisted chunk resolves to `file, §section, p. N–M`;
4. **determinism** — a second fusion over the same two records produces the same order.

The questions are **dev ids**, as in `arms`: the router's eval half is not spent on a smoke
test.
"""

from __future__ import annotations

import json
import time

from hyrag.arms import runner
from hyrag.fuse import combine
from hyrag.kg_light import config as kg_light_config

GATE_IDS = ("mult-051", "mult-052", "mult-053")
GATE_LOG = kg_light_config.RESULTS / "fuse_gate.jsonl"
_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}


def gate() -> int:
    print("\n  fuse gate")
    encoder = combine.rerank_module.CrossEncoder()
    print(f"  · reranker {encoder.id} · max_length {encoder.max_length} · k {combine.DEFAULT_K} · "
          f"shortlist {combine.DEFAULT_SHORTLIST}")
    everything_green = True
    GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with GATE_LOG.open("w", encoding="utf-8") as handle:
        for question_id in GATE_IDS:
            question = next(record for record in runner.questions(ids=[question_id]))
            print(f"\n  {question_id} · {question['question'][:110]}")

            started = time.perf_counter()
            arms = {
                "kg-light": runner.answer_one("kg-light", question),
                "kg-graph-bridged": runner.answer_one("kg-graph-bridged", question),
            }
            record = combine.fuse(question, arms, reranker=encoder)
            # Determinism: the same two records, fused twice. Cheap — the model is loaded, the
            # arms are not re-run — and it is the contract "deterministic given the records".
            again = combine.fuse(question, arms, reranker=encoder)
            deterministic = [entry["chunk_id"] for entry in again["reranked"]] == [
                entry["chunk_id"] for entry in record["reranked"]
            ]
            elapsed = time.perf_counter() - started

            diagnostics = record["diagnostics"]
            checks = {
                "both_arms_in_top_10": diagnostics["both_arms_in_top_k"],
                "reranker_reorders": diagnostics["reordered"],
                "provenance": all(entry["citation"] for entry in record["shortlist"])
                and diagnostics["unresolved"] == 0,
                "deterministic": deterministic,
            }
            for name, passed in checks.items():
                detail = ""
                if name == "both_arms_in_top_10":
                    detail = " · ".join(f"{arm} {count}" for arm, count
                                        in diagnostics["arms_in_top_k"].items())
                elif name == "reranker_reorders":
                    detail = f"{diagnostics['positions_moved']}/{len(record['shortlist'])} positions moved"
                elif name == "provenance":
                    detail = f"{len(record['shortlist'])} citations · 0 unresolved · " \
                             f"{diagnostics['truncated']} truncated at {encoder.max_length}"
                print(f"    {'✓' if passed else '✗'} {name:<20} {detail}")
            everything_green = everything_green and all(checks.values())

            print("    rank  rrf        kg-light  bridged   rerank   chunk     citation")
            for position, entry in enumerate(record["reranked"][:5], start=1):
                light = entry["ranks"].get("kg-light", "—")
                bridged = entry["ranks"].get("kg-graph-bridged", "—")
                print(f"    {position:>4}  {entry['rrf']:.6f}  {light:>8}  {bridged:>7}  "
                      f"{entry['rerank']:>8.2f}  {entry['chunk_id'][7:15]}  "
                      f"{entry['citation'][:58]}")
            print(f"    · {elapsed:.1f}s for both arms + two fusions · "
                  f"arms cost {sum(arm.cost_usd for arm in arms.values()):.4f} USD")

            handle.write(json.dumps({**record, "checks": checks}, **_JSON) + "\n")
            handle.flush()

    print(f"\n  wrote {GATE_LOG}")
    print(f"\n  gate: {'GREEN' if everything_green else 'RED'}\n")
    return 0 if everything_green else 1
