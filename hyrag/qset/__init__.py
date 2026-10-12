"""qset — the 150-question instrument the benchmark is measured with.

    uv run python -m hyrag.qset.cli draft | review | freeze

`draft.py` builds candidates from the chunk set (every question is born with the chunk that
contains its answer, so the evidence target cannot drift); a human filters them; `freeze`
writes `qset/questions.jsonl`, whose commit SHA is the proof that the questions predate
`router` (PREPLAN §10 Q4, nodes/qset.md).

Only `questions.jsonl` is committed. `drafts.jsonl` and `review.md` carry source text and are
gitignored — part of the corpus is © OEM material held under terms that forbid reposting.
"""

from hyrag.qset.draft import (
    QUESTIONS,
    Source,
    draft_batch,
    freeze,
    load_sources,
    read_drafts,
    render_review,
    run,
    select_batches,
    write_drafts,
)

__all__ = [
    "QUESTIONS",
    "Source",
    "draft_batch",
    "freeze",
    "load_sources",
    "read_drafts",
    "render_review",
    "run",
    "select_batches",
    "write_drafts",
]
