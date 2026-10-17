"""judge/runner.py — the loop: blind, shuffled per replicate, spread reported rather than hidden.

Three passes, one per question of the evaluation set each time:

1. **Pointwise** — every (question, row, replicate) becomes one `LLMTestCase` per criterion.
   `input` = the question, `actual_output` = the row's answer, `expected_output` = the gold
   answer for factoid and multi-hop and **absent for thematic** (its gold is a checklist we
   wrote; similarity to it would measure agreement with us). The row's name appears nowhere in
   the object: `name` is `⟨question id⟩-r⟨replicate⟩` (§7.2).
2. **Pairwise** — an `ArenaGEval` over the answers to one question, all rows as contestants.
   The library masks contestant names with its own dummy names before the judge sees them, so
   the comparison is blind without us inventing a second blinding scheme.
3. **The blinding control** (trap i) — a sample of questions where the judge is shown the
   unlabelled answers *and* a one-line description of each system's method, and is asked to
   say which system wrote which. Structural blinding does not survive a 26,000-character
   report next to a paragraph; this is how the report says so with a number instead of a hope.

Replicates are judge replicates, not generation replicates: the rows answered once, and the
three passes re-ask the judge about the same answers. Nothing here regenerates an answer —
regenerating would change the object being compared (§7.4's own rule, applied to the matrix).

Every pass appends to its own JSONL in `results/` as it goes, so a run that dies at item 900
resumes by rerunning the same command with the same seed and skipping what is already there.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

from deepeval.metrics import ArenaGEval, GEval
from deepeval.test_case import ArenaTestCase, Contestant, LLMTestCase, SingleTurnParams

from hyrag.judge import config, records
from hyrag.judge import prompts
from hyrag.judge.model import ProxyJudge

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}
_PARAMS_WITH_GOLD = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT,
                     SingleTurnParams.EXPECTED_OUTPUT]
_PARAMS_QUESTION_ONLY = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]


@dataclass(frozen=True)
class Item:
    """One row's answer to one question, with the question that produced it."""

    row: str
    question_id: str
    question: str
    question_class: str
    answer: str
    expected_output: str | None
    label: str  # "baseline · fact-000" — for logs, never for the judge


def build_items(rows: dict[str, dict[str, records.AnswerRecord]] | None = None,
                *, question_ids: list[str] | None = None, per_class: int | None = None,
                only_rows: tuple[str, ...] = config.ROW_ORDER) -> list[Item]:
    """The (question, row) pairs the table covers — every row that answered, scored equally.

    `per_class` keeps the **first N of each class** rather than the first N questions: a smoke
    run that happened to take its three questions from one class would exercise one branch of
    the prompt construction and leave the other two untested.
    """
    rows = rows or records.load_all(only_rows)
    items: list[Item] = []
    questions = config.evaluation_set()
    if question_ids:
        wanted = set(question_ids)
        questions = [question for question in questions if question["id"] in wanted]
    if per_class:
        kept: list[dict] = []
        counts: dict[str, int] = {}
        for question in questions:
            if counts.get(question["class"], 0) < per_class:
                kept.append(question)
                counts[question["class"]] = counts.get(question["class"], 0) + 1
        questions = kept
    for question in questions:
        gold = question.get("gold_answer") if question["class"] in ("factoid", "multi-hop") else None
        for row in only_rows:
            record = rows.get(row, {}).get(question["id"])
            if record is None:
                continue
            items.append(Item(row=row, question_id=question["id"], question=question["question"],
                              question_class=question["class"],
                              answer=record.answer, expected_output=gold,
                              label=f"{record.label} · {question['id']}"))
    return items


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, **_JSON) + "\n")
        handle.flush()


def done_keys(path: Path, key_fields: tuple[str, ...], ok_field: str) -> set[tuple]:
    """Keys of **successful** records. A failed call is retried, not treated as an answer.

    Getting this wrong is expensive in a quiet way: a run that dies inside a pass would
    resume believing the missing items were done, and the table would carry a hole nothing
    pointed at. The `ok_field` is the one that only a real result carries — `score` for
    pointwise, `winner` for pairwise, `correct` for the control.
    """
    if not path.is_file():
        return set()
    keys = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get(ok_field) is not None:
                keys.add(tuple(record.get(field) for field in key_fields))
    return keys


def _params_for(item: Item) -> list[SingleTurnParams]:
    return list(_PARAMS_WITH_GOLD) if item.expected_output else list(_PARAMS_QUESTION_ONLY)


def _criterion_metric(criterion: dict, item: Item, label: str) -> GEval:
    params = _params_for(item)
    return GEval(
        name=criterion["name"],
        criteria=criterion["criteria"],
        evaluation_steps=criterion["evaluation_steps"],
        evaluation_params=params,
        model=ProxyJudge(label),
        async_mode=True,
        verbose_mode=False,
    )


async def run_pointwise(items: list[Item], *, replicates: int | None = None,
                        concurrency: int | None = None, resume: bool = True,
                        log=print) -> dict:
    """The 4 × 3 matrix: one judged score per (item, criterion, replicate)."""
    replicates = replicates or config.REPLICATES
    concurrency = concurrency or config.CONCURRENCY
    path = config.SCORES_LOG
    seen = done_keys(path, ("row", "question_id", "replicate", "criterion"), "score") if resume else set()
    semaphore = asyncio.Semaphore(concurrency)
    tally = {"scored": 0, "failed": 0, "skipped": 0}
    started = time.perf_counter()

    async def one(item: Item, criterion: dict, replicate: int) -> None:
        key = (item.row, item.question_id, replicate, criterion["key"])
        if key in seen:
            tally["skipped"] += 1
            return
        label = f"{item.row}:{item.question_id}:r{replicate}:{criterion['key']}"
        test_case = LLMTestCase(
            input=item.question,
            actual_output=item.answer,
            expected_output=item.expected_output,
            name=f"{item.question_id}-r{replicate}",
        )
        metric = _criterion_metric(criterion, item, label)
        async with semaphore:
            try:
                score = await metric.a_measure(test_case, _show_indicator=False)
                record = {
                    "row": item.row, "question_id": item.question_id, "class": item.question_class,
                    "replicate": replicate, "criterion": criterion["key"], "score": float(score),
                    "reason": (metric.reason or "")[:2000], "label": label,
                }
                tally["scored"] += 1
            except Exception as error:
                record = {
                    "row": item.row, "question_id": item.question_id, "class": item.question_class,
                    "replicate": replicate, "criterion": criterion["key"], "score": None,
                    "error": f"{type(error).__name__}: {error}"[:300], "label": label,
                }
                tally["failed"] += 1
        _append(path, record)
        done = tally["scored"] + tally["failed"]
        total = sum(len(prompts.criteria.for_class(item.question_class)) for item in items) * replicates
        if done % 25 == 0 or done == total:
            rate = (time.perf_counter() - started) / max(done, 1)
            log(f"  pointwise {done:5d}/{total} · failed {tally['failed']} · "
                f"{rate:.2f}s/item · eta {(total - done) * rate / 60:5.1f}m")

    await asyncio.gather(*(one(item, criterion, replicate)
                           for item in items
                           for replicate in range(1, replicates + 1)
                           for criterion in prompts.criteria.for_class(item.question_class)))
    return tally


async def run_pairwise(items: list[Item], *, replicates: int | None = None,
                       concurrency: int | None = None, resume: bool = True,
                       variant: str = "paper", log=print) -> dict:
    """The headline: blind, masked pairwise comparisons between the rows, per question.

    `variant="paper"` is the benchmark's rubric — the papers' three criteria. `variant="control"`
    adds Directness, which prices padding; it runs to its own log and is a sensitivity check,
    not the table's number (the two rank the rows in opposite orders).
    """
    replicates = replicates or config.REPLICATES
    concurrency = concurrency or config.CONCURRENCY
    criteria = (prompts.criteria.PAIRWISE_PAPER3 if variant == "paper"
                else prompts.criteria.PAIRWISE)
    path = config.PAIRWISE_LOG if variant == "paper" else config.PAIRWISE_LOG_CONTROL
    seen = done_keys(path, ("question_id", "replicate"), "winner") if resume else set()
    semaphore = asyncio.Semaphore(concurrency)
    by_question: dict[str, list[Item]] = {}
    for item in items:
        by_question.setdefault(item.question_id, []).append(item)
    tally = {"compared": 0, "failed": 0, "skipped": 0}
    started = time.perf_counter()

    async def one(question_id: str, group: list[Item], replicate: int) -> None:
        if (question_id, replicate) in seen:
            tally["skipped"] += 1
            return
        # Per-replicate shuffle: the order contestants are presented in changes between
        # replicates, so a position preference in the judge cannot masquerade as a winner.
        order = list(group)
        random.Random(f"{config.SEED}:{question_id}:{replicate}").shuffle(order)
        gold = order[0].expected_output
        contestants = [
            Contestant(name=item.row, test_case=LLMTestCase(
                input=item.question,
                actual_output=item.answer, expected_output=gold,
                name=f"{question_id}-r{replicate}"))
            for item in order
        ]
        arena = ArenaTestCase(contestants=contestants)
        metric = ArenaGEval(
            name=criteria["name"],
            criteria=criteria["criteria"],
            evaluation_steps=criteria["evaluation_steps"],
            evaluation_params=_params_for(order[0]),
            model=ProxyJudge(f"pairwise-{variant}:{question_id}:r{replicate}"),
            async_mode=True,
        )
        async with semaphore:
            try:
                winner = await metric.a_measure(arena, _show_indicator=False)
                record = {
                    "question_id": question_id, "class": group[0].question_class,
                    "replicate": replicate, "presented_order": [item.row for item in order],
                    "winner": winner, "reason": (metric.reason or "")[:2000],
                }
                tally["compared"] += 1
            except Exception as error:
                record = {
                    "question_id": question_id, "class": group[0].question_class,
                    "replicate": replicate, "presented_order": [item.row for item in order],
                    "winner": None, "error": f"{type(error).__name__}: {error}"[:300],
                }
                tally["failed"] += 1
        _append(path, record)
        done = tally["compared"] + tally["failed"]
        total = len(by_question) * replicates
        if done % 10 == 0 or done == total:
            rate = (time.perf_counter() - started) / max(done, 1)
            log(f"  pairwise  {done:5d}/{total} · failed {tally['failed']} · "
                f"{rate:.1f}s/item · eta {(total - done) * rate / 60:5.1f}m")

    await asyncio.gather(*(one(question_id, group, replicate)
                           for question_id, group in by_question.items()
                           for replicate in range(1, replicates + 1)))
    return tally


async def run_head_to_head(items: list[Item], *, pairs: list[tuple[str, str]],
                           replicates: int | None = None, concurrency: int | None = None,
                           resume: bool = True, log=print) -> dict:
    """Two-way comparisons — "does A beat B", which is what the papers actually claim.

    A three-way arena answers *who is best of the three*, and a middle-length row is squeezed
    out of that question by the longest one regardless of its accuracy or its cost. The ladder
    is the instrument for the head-to-head claims: LightRAG vs GraphRAG, and each augmentation
    vs the baseline, on the papers' three criteria, with the presentation order shuffled per
    (question, pair, replicate) so position cannot prefer a row.
    """
    replicates = replicates or config.REPLICATES
    concurrency = concurrency or config.CONCURRENCY
    path = config.PAIRWISE_LOG_PAIRS
    seen = done_keys(path, ("question_id", "pair", "replicate"), "winner") if resume else set()
    semaphore = asyncio.Semaphore(concurrency)
    by_question: dict[str, dict[str, Item]] = {}
    for item in items:
        by_question.setdefault(item.question_id, {})[item.row] = item
    tally = {"compared": 0, "failed": 0, "skipped": 0}
    started = time.perf_counter()
    total = len(by_question) * len(pairs) * replicates
    criteria = prompts.criteria.PAIRWISE_PAPER3

    async def one(question_id: str, pair: tuple[str, str], replicate: int) -> None:
        tag = f"{pair[0]}-{pair[1]}"
        if (question_id, tag, replicate) in seen:
            tally["skipped"] += 1
            return
        present = [by_question[question_id][row] for row in pair if row in by_question[question_id]]
        if len(present) != 2:
            tally["skipped"] += 1
            return
        order = list(present)
        random.Random(f"{config.SEED}:{question_id}:{tag}:{replicate}").shuffle(order)
        gold = order[0].expected_output
        arena = ArenaTestCase(contestants=[
            Contestant(name=item.row, test_case=LLMTestCase(
                input=item.question, actual_output=item.answer, expected_output=gold,
                name=f"{question_id}-r{replicate}"))
            for item in order])
        metric = ArenaGEval(
            name=criteria["name"], criteria=criteria["criteria"],
            evaluation_steps=criteria["evaluation_steps"],
            evaluation_params=_params_for(order[0]),
            model=ProxyJudge(f"h2h:{tag}:{question_id}:r{replicate}"),
            async_mode=True,
        )
        async with semaphore:
            try:
                winner = await metric.a_measure(arena, _show_indicator=False)
                record = {"question_id": question_id, "class": present[0].question_class,
                          "pair": tag, "replicate": replicate,
                          "presented_order": [item.row for item in order],
                          "winner": winner, "reason": (metric.reason or "")[:2000]}
                tally["compared"] += 1
            except Exception as error:
                record = {"question_id": question_id, "class": present[0].question_class,
                          "pair": tag, "replicate": replicate,
                          "presented_order": [item.row for item in order],
                          "winner": None, "error": f"{type(error).__name__}: {error}"[:300]}
                tally["failed"] += 1
        _append(path, record)
        done = tally["compared"] + tally["failed"] + tally["skipped"]
        if done % 50 == 0 or done == total:
            rate = (time.perf_counter() - started) / max(done, 1)
            log(f"  head-to-head {done:5d}/{total} · failed {tally['failed']} · {rate:.1f}s · "
                f"eta {(total - done) * rate / 60:5.1f}m")

    await asyncio.gather(*(one(question_id, pair, replicate)
                           for question_id in by_question
                           for pair in pairs
                           for replicate in range(1, replicates + 1)))
    return tally


ASSIGNMENT = re.compile(r'"system"\s*:\s*"([ABCabc])"')  # kept for reading older records



async def run_blinding(items: list[Item], *, sample: int | None = None,
                       concurrency: int | None = None, resume: bool = True,
                       log=print) -> dict:
    """Trap (i): can the judge tell which system wrote an answer when the method is described?"""
    sample = sample or config.BLINDING_SAMPLE
    concurrency = concurrency or config.CONCURRENCY
    path = config.BLINDING_LOG
    seen = done_keys(path, ("question_id",), "correct") if resume else set()
    by_question: dict[str, list[Item]] = {}
    for item in items:
        by_question.setdefault(item.question_id, []).append(item)
    picked = [question_id for question_id in by_question
              if question_id not in seen
              and question_id in _sample_of(by_question, sample)]
    tally = {"asked": 0, "failed": 0, "skipped": len(by_question) - len(picked)}
    semaphore = asyncio.Semaphore(concurrency)
    questions = config.questions_by_id()

    async def one(question_id: str) -> None:
        group = by_question[question_id]
        order = list(group)
        random.Random(f"{config.SEED}:blind:{question_id}").shuffle(order)
        candidates = "\n\n".join(
            f"**Candidate {index}**\n{item.answer}" for index, item in enumerate(order, 1))
        prompt = prompts.criteria.BLINDING_PROMPT.format(
            A=prompts.criteria.BLINDING_SYSTEMS["A"],
            B=prompts.criteria.BLINDING_SYSTEMS["B"],
            C=prompts.criteria.BLINDING_SYSTEMS["C"],
            question=questions[question_id]["question"], candidates=candidates)
        model = ProxyJudge(f"blinding:{question_id}", max_tokens=1024)
        async with semaphore:
            try:
                # A pool of the answer's row per candidate position, and the systems in A/B/C
                # order, so a correct-answer count is a lookup rather than a parse of prose.
                # The assignments are parsed as JSON, not by pattern-matching the prose: a model
                # that lists candidate 3 before candidate 1 would otherwise shift every guess.
                answer = await model.a_generate(prompt, schema=True)
                body = answer.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                assignments = json.loads(body).get("assignments", [])
                by_candidate = {int(a.get("candidate", 0)): str(a.get("system", "")).upper()
                                for a in assignments if isinstance(a, dict)}
                expected = _row_to_system()
                guesses = [by_candidate.get(index, "?") for index in range(1, len(order) + 1)]
                correct = sum(1 for guess, item in zip(guesses, order)
                              if guess == expected[item.row])
                record = {
                    "question_id": question_id, "class": group[0].question_class,
                    "presented_order": [item.row for item in order],
                    "guesses": guesses,
                    "expected": [expected[item.row] for item in order],
                    "correct": correct, "chance": round(1 / len(order), 4), "raw": answer[:1500],
                }
                tally["asked"] += 1
            except Exception as error:
                record = {"question_id": question_id, "class": group[0].question_class,
                          "error": f"{type(error).__name__}: {error}"[:300]}
                tally["failed"] += 1
        _append(path, record)

    await asyncio.gather(*(one(question_id) for question_id in picked))
    log(f"  blinding  {len(picked)} questions · asked {tally['asked']} · failed {tally['failed']}")
    return tally


def _sample_of(by_question: dict[str, list[Item]], per_class: int) -> set[str]:
    """`per_class` questions from each class, taken by stride — the same rule as the sample."""
    chosen: dict[str, list[str]] = {}
    for question_id in by_question:
        label = by_question[question_id][0].question_class
        chosen.setdefault(label, []).append(question_id)
    picked: set[str] = set()
    for ids in chosen.values():
        ids = sorted(ids)
        step = max(1, len(ids) // per_class)
        picked.update(ids[::step][:per_class])
    return picked


def _row_to_system() -> dict[str, str]:
    """Which A/B/C label each row has — the descriptions are always presented in this order."""
    return {"R1": "A", "R2": "B", "R3": "C"}
