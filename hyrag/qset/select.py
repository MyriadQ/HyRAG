"""qset/select.py — which questions of the frozen set a run answers.

`qset/questions.jsonl` is the frozen set (commit `2c5de79`): 150 questions, 50 per class,
each with a gold answer and an evidence target. A benchmark run does not always answer all
of them — R3's thematic global search costs **$0.33 and ~5 minutes per question** (measured
2026-10-16), so the thematic class is sampled, while every cell whose cost is a rounding
error keeps the full 50. That split is the scale decision recorded in `nodes/judge.md`.

**The sample rule is systematic:** every 2.5th thematic question in file order, `n = 20` —
indices `0, 2, 5, …, 48`. Mechanical, reproducible, no cherry-picking to defend, and it
touches all 11 documents in the slice. The rule lives in code rather than in a saved list so
that a fresh clone re-derives the same questions from the frozen set alone; the ids it
produces are printed into the run record beside the table (`hyrag/judge/` pins them).

Why not "the first 20": the questions were drafted in passes and kept in draft order, and a
prefix is exactly the kind of choice that later reads as selection. A stride cannot be
accused of it and needs no seed to verify.
"""

from __future__ import annotations

import json

from hyrag.parse.config import REPO

QUESTIONS = REPO / "qset" / "questions.jsonl"
CLASSES = ("factoid", "multi-hop", "thematic")
THEMATIC_SAMPLE_N = 20  # of 50 — see the module docstring


def load() -> list[dict]:
    """Every frozen question, in file order."""
    with QUESTIONS.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def by_class(records: list[dict], label: str) -> list[dict]:
    return [record for record in records if record["class"] == label]


def systematic(records: list[dict], n: int) -> list[dict]:
    """`n` questions spread evenly over `records`, first included, last near-excluded."""
    if n >= len(records):
        return list(records)
    step = len(records) / n
    return [records[min(len(records) - 1, round(index * step))] for index in range(n)]


def evaluation_set(*, thematic_n: int = THEMATIC_SAMPLE_N) -> list[dict]:
    """What the judged table covers: all of factoid and multi-hop, `thematic_n` thematic.

    R1 answered all 150 and keeps its full row; the table's thematic cell is the *same*
    sample for every row, which is what makes the column comparable. Pass
    `thematic_n=50` to widen it, or 0 to drop the class.
    """
    records = load()
    selected = by_class(records, "factoid") + by_class(records, "multi-hop")
    if thematic_n:
        selected += systematic(by_class(records, "thematic"), thematic_n)
    order = {record["id"]: index for index, record in enumerate(records)}
    # Thematic first: it is the sampled class and the run's long pole (~5 min/question against
    # seconds for the other two). A staged run wants its expensive phase in front, where a
    # checkpoint can still change the plan (S06's habit — sample, read it, then commit).
    return sorted(selected, key=lambda record: (
        0 if record["class"] == "thematic" else 1, order[record["id"]]))


def select(*, ids: list[str] | None = None, classes: list[str] | None = None) -> list[dict]:
    """An explicit subset — `--id` / `--class` on a runner's command line."""
    records = load()
    if classes:
        records = [record for record in records if record["class"] in classes]
    if ids:
        wanted = set(ids)
        records = [record for record in records if record["id"] in wanted]
    return records


def describe(records: list[dict]) -> str:
    """`150 questions · 50 factoid · 50 multi-hop · 50 thematic` — for a log line."""
    counts: dict[str, int] = {}
    for record in records:
        counts[record["class"]] = counts.get(record["class"], 0) + 1
    parts = " · ".join(f"{counts.get(label, 0)} {label}" for label in CLASSES)
    return f"{len(records)} questions · {parts}"
