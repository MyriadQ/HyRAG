"""embedding/checks.py — the properties the encoder must hold, as one command.

    uv run python -m hyrag.embedding.checks                              # everything, ~7 min
    uv run python -m hyrag.embedding.checks --chunks 40 --questions 6    # smoke run, ~40 s

Same contract as `hyrag.parse.checks` and `hyrag.chunker.checks`: each check states the
property it verifies, and a failure names what it failed on. `--chunks` and `--questions`
exist only to make a first run fast — **recall numbers from a subset mean nothing**.

Each check, and the failure it catches:

* `dimension`         every vector is 1024 floats;
* `unit_norm`         every vector has Euclidean length 1 — what every consumer assumes
                      before it reaches for an inner product;
* `determinism`       the same text twice gives identical floats — catches a model left in
                      training mode, where dropout stays on;
* `batch_invariance`  a text encoded alone equals the same text encoded beside a longer one
                      — the attention-mask check. Drop the mask and the padding positions
                      leak into the pooled row, silently, in every batch;
* `order`             reversing the input reverses the output, vector for vector. Order is
                      load-bearing twice: `chunk_id` → vector here, and the HTTP adapter
                      later re-assembles batches by position;
* `truncation`        no chunk exceeds the model's 8192-token window;
* `token_counts`      INFO — the model's XLM-R counts beside the chunker's `o200k_base`
                      counts. They differ (~15%) and conflating them is a live hazard;
* `evidence_recall`   INFO — the qset's own evidence targets, ranked by brute-force cosine
                      over the whole corpus, as a **curve over k**. This is the node's gate
                      mechanism measured before Postgres, the LLM or a line of the loop
                      exists (`nodes/baseline.md` → Gate). The curve is what distinguishes
                      a budget problem (recovers as k grows) from a representation problem
                      (stays flat) — different fixes, so both it and the median rank print.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from hyrag.chunker.store import INGEST_NAME, read_jsonl
from hyrag.embedding.encoder import _load, encode
from hyrag.parse.config import PARSED, REPO

# Repo-root absolute, resolved from `__file__` inside `hyrag.parse.config` — never relative to
# the working directory, so this module behaves the same from a notebook in `scratch/` as it
# does from `uv run` at the repo root. `hyrag.qset.draft.QUESTIONS` names the same file as
# QSET_PATH; importing it would drag the drafting module's imports in with it.
INGEST_PATH = PARSED / INGEST_NAME  # the one chunk set all four rows read 
QSET_PATH = REPO / "qset" / "questions.jsonl"
CLASSES = ("factoid", "multi-hop", "thematic")
K_VALUES = (1, 5, 10, 20, 50, 100)  # the recall curve's x-axis


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None  # None = informational
    detail: str = ""

    def line(self) -> str:
        mark = "PASS" if self.ok else ("INFO" if self.ok is None else "FAIL")
        return f"  [{mark}] {self.name}" + (f" — {self.detail}" if self.detail else "")


# ---------------------------------------------------------------- inputs


def _corpus(limit: int | None = None) -> list[dict]:
    """The ingest set's chunk records, optionally truncated for a smoke run."""
    _header, records = read_jsonl(INGEST_PATH)
    return records if limit is None else records[:limit]


def _questions(limit: int | None = None) -> list[dict]:
    with QSET_PATH.open(encoding="utf-8") as fh:
        questions = [json.loads(line) for line in fh if line.strip()]
    return questions if limit is None else questions[:limit]


def _sample(chunks: list[dict]) -> list[str]:
    """Three real chunks — shortest, median, longest — so the batch spans different token
    lengths. Padding is where the mask bug would live, so the sample has to exercise it."""
    ordered = sorted(chunks, key=lambda chunk: chunk["tokens"])
    picked = [ordered[0], ordered[len(ordered) // 2], ordered[-1]]
    return [chunk["content"] for chunk in picked]


# ---------------------------------------------------------------- the checks


def check_vector_properties(chunks: list[dict]) -> list[Check]:
    vectors = np.asarray(encode(_sample(chunks)), dtype=np.float32)
    dims = {vector.shape[0] for vector in vectors}
    norms = np.linalg.norm(vectors, axis=1)
    return [
        Check("dimension", dims == {1024}, f"shapes seen: {sorted(dims)}"),
        Check(
            "unit_norm",
            bool(np.allclose(norms, 1.0, atol=1e-5)),
            f"lengths {norms.min():.6f} … {norms.max():.6f}",
        ),
    ]


def check_determinism(chunks: list[dict]) -> list[Check]:
    text = _sample(chunks)[1]
    first, second = encode([text])[0], encode([text])[0]
    divergence = next((i for i, (a, b) in enumerate(zip(first, second)) if a != b), None)
    return [
        Check("determinism", first == second, "" if divergence is None else f"diverges at {divergence}")
    ]


def check_batch_invariance(chunks: list[dict]) -> list[Check]:
    short, _median, long = _sample(chunks)
    alone = np.asarray(encode([short])[0], dtype=np.float32)
    in_batch = np.asarray(encode([long, short, long])[1], dtype=np.float32)
    delta = float(np.abs(alone - in_batch).max())
    return [Check("batch_invariance", delta < 1e-5, f"max |Δ| = {delta:.2e}")]


def check_order(chunks: list[dict]) -> list[Check]:
    texts = _sample(chunks)
    forward = encode(texts)
    backward = encode(list(reversed(texts)))
    aligned = all(forward[i] == backward[len(texts) - 1 - i] for i in range(len(texts)))
    return [Check("order", aligned, f"{len(texts)} texts, reversed and compared")]


def check_truncation(chunks: list[dict]) -> list[Check]:
    """The model's window, and the two token counts side by side.

    Tokenising the whole corpus is cheap (0.2 s — no forward pass), so this check always
    runs on the full set even during a smoke run.
    """
    tokenizer, _model = _load()
    model_lengths = [len(tokenizer(chunk["content"])["input_ids"]) for chunk in chunks]
    ours = [chunk["tokens"] for chunk in chunks]
    over = sum(1 for length in model_lengths if length > 8192)
    ratio = sum(model_lengths) / sum(ours) if ours else 0.0
    return [
        Check("truncation", over == 0, f"{over} of {len(chunks)} over the model's window"),
        Check(
            "token_counts",
            None,
            f"model (XLM-R) mean {sum(model_lengths) / len(model_lengths):.0f} "
            f"max {max(model_lengths)} · ours (o200k) mean {sum(ours) / len(ours):.0f} "
            f"max {max(ours)} · ratio {ratio:.3f}",
        ),
    ]


def check_evidence_recall(
    chunks: list[dict], questions: list[dict], limit: int | None = None
) -> list[Check]:
    """Is each question's own evidence chunk inside the top k of the whole corpus?

    Brute force and deterministic: encode everything, multiply, sort. No Postgres, no LLM,
    no reranker — the encoder alone, which is the point. Questions whose evidence is not in
    the loaded subset are dropped *before* `limit` applies, so a `--chunks` smoke run tests
    the real path instead of reporting that nothing matched.
    """
    position = {chunk["chunk_id"]: index for index, chunk in enumerate(chunks)}
    usable = [
        question
        for question in questions
        if all(cid in position for cid in question["evidence_target"]["chunk_ids"])
    ]
    skipped = len(questions) - len(usable)
    if limit is not None:
        usable = usable[:limit]
    if not usable:
        return [Check("evidence_recall", False, "no question's evidence is in this subset")]

    corpus = np.asarray(encode([chunk["content"] for chunk in chunks]), dtype=np.float32)
    queries = np.asarray(encode([question["question"] for question in usable]), dtype=np.float32)

    # every vector is unit length, so the dot product *is* the cosine similarity
    # argsort(-x) puts the most similar first; top-k is then just the first k entries
    ranking = np.argsort(-(queries @ corpus.T), axis=1)

    ranks: dict[str, list[int]] = defaultdict(list)
    for row, question in zip(ranking, usable):
        targets = {position[cid] for cid in question["evidence_target"]["chunk_ids"]}
        # the question's best evidence: its highest-ranked target, 1-based
        ranks[question["class"]].append(
            min(int(np.where(row == target)[0][0]) + 1 for target in targets)
        )

    def curve(found: list[int]) -> str:
        """How many of these questions have their evidence inside the first k, per k.

        The curve is the point: a class that recovers at larger k has a *budget* problem,
        one that stays flat has a *representation* problem — different fixes, so both the
        curve and the median rank are reported.
        """
        return " · ".join(
            f"@{k} {sum(1 for rank in found if rank <= k)}/{len(found)}" for k in K_VALUES
        )

    checks: list[Check] = []
    for question_class in CLASSES:
        found = ranks.get(question_class, [])
        if not found:
            continue
        checks.append(
            Check(
                f"evidence_recall [{question_class}]",
                None,
                f"median rank {int(np.median(found))} · {curve(found)}",
            )
        )
    everything = [rank for found in ranks.values() for rank in found]
    detail = f"median rank {int(np.median(everything))} · {curve(everything)}"
    if skipped:
        detail += f" · {skipped} dropped (evidence outside the subset)"
    checks.append(Check("evidence_recall [all]", None, detail))
    return checks

# ---------------------------------------------------------------- driver


def run(chunks: int | None = None, questions: int | None = None) -> list[Check]:
    corpus = _corpus(chunks)
    return (
        check_vector_properties(corpus)
        + check_determinism(corpus)
        + check_batch_invariance(corpus)
        + check_order(corpus)
        + check_truncation(_corpus(None))
        + check_evidence_recall(corpus, _questions(None), limit=questions)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="encoder invariants")
    parser.add_argument("--chunks", type=int, default=None, help="smoke runs only")
    parser.add_argument("--questions", type=int, default=None, help="smoke runs only")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="  · %(message)s")

    checks = run(chunks=args.chunks, questions=args.questions)
    for check in checks:
        print(check.line())
    failed = [check for check in checks if check.ok is False]
    print(f"\n  {len(checks)} checks · {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
