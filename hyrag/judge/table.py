"""judge/table.py — `results.csv`, the pin record, and the tables the report quotes.

The gate's sentence is *"one command reproduces the results table from the frozen qset, with
the judge prompts and rubric version saved beside it"*, and every clause of it lands here:

* **reproduces** — the CSV is a pure function of the answer files, `judge_scores.jsonl` and
  `judge_pairwise.jsonl`; nothing is copied by hand from a terminal, and no number in it is
  typed. Rerun the command and the file is rewritten from the same inputs.
* **from the frozen qset** — the question set's sha256 and the commit that last touched it go
  into the pins, so a table built against a different set is detectable rather than arguable.
* **prompts and rubric version beside it** — `results/judge_prompts.json` holds the criteria
  exactly as the judge saw them, with `RUBRIC_VERSION`; the pins hold its sha256.

The judged scores are aggregated **per question first**, then averaged: a cell's quality is
the mean of the per-question means over the three replicates, so a question that the judge
scored three times does not outweigh a question it scored once (a failed call). The replicate
spread is reported beside every criterion — mean over questions of the standard deviation
across the three replicates — because §7.2's "three replicates, spread reported" is about
knowing how much of a difference the judge itself would call noise.
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import subprocess
from datetime import datetime, timezone

from hyrag.judge import citations, config, records
from hyrag.judge import prompts
from hyrag.parse.config import REPO
from hyrag.qset import select

CRITERIA_KEYS = tuple(item["key"] for item in prompts.criteria.CRITERIA)  # incl. sensitivity



def criteria_for(question_class: str) -> tuple[str, ...]:
    """Which criteria a class is judged on — the rubric plus accuracy, never the sensitivity."""
    return tuple(item["key"] for item in prompts.criteria.for_class(question_class))


# ---------------------------------------------------------------- loading


def load_scores() -> list[dict]:
    if not config.SCORES_LOG.is_file():
        return []
    return [json.loads(line) for line in config.SCORES_LOG.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def load_pairwise() -> list[dict]:
    if not config.PAIRWISE_LOG.is_file():
        return []
    return [json.loads(line) for line in config.PAIRWISE_LOG.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def load_pairs() -> list[dict]:
    if not config.PAIRWISE_LOG_PAIRS.is_file():
        return []
    return [json.loads(line) for line in config.PAIRWISE_LOG_PAIRS.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def build_head_to_head(pairs: list[dict] | None = None) -> list[dict]:
    """Two-way win shares — "does A beat B", which is what the papers claim.

    Per-question majority over the three replicates, because the question is the unit the
    sample was drawn in; the per-replicate share is carried beside it so a reader can see the
    judge's own spread.
    """
    pairs = load_pairs() if pairs is None else pairs
    cells: list[dict] = []
    # The ladder is whatever was run, not a hard-coded trio: Phase 2 adds R4 against each row,
    # and a fixed list would compute those comparisons and silently never publish them
    # (observed 2026-10-17: 1,080 R4 comparisons logged, no R4 row in head_to_head.csv).
    tags = sorted({record["pair"] for record in pairs
                   if record.get("pair") and record.get("winner")})
    for label in select.CLASSES:
        for tag in tags:
            left, right = tag.split("-", 1)
            subset = [record for record in pairs
                      if record.get("class") == label and record.get("pair") == tag
                      and record.get("winner")]
            if not subset:
                continue
            by_question: dict[str, list[str]] = {}
            for record in subset:
                by_question.setdefault(record["question_id"], []).append(record["winner"])
            majority = [statistics.mode(winners) for winners in by_question.values()]
            n = len(majority)
            per_replicate = []
            for replicate in sorted({record["replicate"] for record in subset}):
                batch = [record for record in subset if record["replicate"] == replicate]
                per_replicate.append(sum(1 for record in batch if record["winner"] == left) / len(batch))
            cells.append({
                "class": label, "pair": tag, "left": left, "right": right, "n": n,
                "left_wins": sum(1 for winner in majority if winner == left),
                "right_wins": sum(1 for winner in majority if winner == right),
                "left_share": round(sum(1 for winner in majority if winner == left) / n, 4),
                "replicate_min": round(min(per_replicate), 4),
                "replicate_max": round(max(per_replicate), 4),
            })
    return cells


def load_blinding() -> list[dict]:
    if not config.BLINDING_LOG.is_file():
        return []
    return [json.loads(line) for line in config.BLINDING_LOG.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def load_calls() -> list[dict]:
    if not config.CALLS_LOG.is_file():
        return []
    return [json.loads(line) for line in config.CALLS_LOG.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# ---------------------------------------------------------------- the cells


def _scores_by_item(scores: list[dict]) -> dict[tuple[str, str, str], dict[int, float]]:
    """`{(row, question, criterion): {replicate: score}}` — one question, one weight."""
    grouped: dict[tuple[str, str, str], dict[int, float]] = {}
    for record in scores:
        if record.get("score") is None:
            continue
        grouped.setdefault((record["row"], record["question_id"], record["criterion"]), {})[
            record["replicate"]] = float(record["score"])
    return grouped


def build_cells(*, rows: dict[str, dict[str, records.AnswerRecord]] | None = None,
                scores: list[dict] | None = None) -> list[dict]:
    rows = rows or records.load_all()
    scores = load_scores() if scores is None else scores
    grouped = _scores_by_item(scores)
    questions = config.questions_by_id()
    cells: list[dict] = []

    for row in config.ROW_ORDER:
        for label in select.CLASSES:
            wanted = [question for question in config.evaluation_set() if question["class"] == label]
            answered = [question for question in wanted
                        if question["id"] in rows.get(row, {})]
            if not answered:
                continue
            records_ = [rows[row][question["id"]] for question in answered]
            cell = {
                "row": row, "label": config.ROWS[row]["label"], "class": label,
                "n": len(answered), "replicates": config.REPLICATES,
                "tokens_in": round(statistics.mean(r.tokens_in for r in records_)),
                "tokens_out": round(statistics.mean(r.tokens_out for r in records_)),
                "calls": round(statistics.mean(r.calls for r in records_), 2),
                "cost_usd": round(sum(r.cost_usd for r in records_) / len(records_), 6),
                "latency_s": round(statistics.mean(r.latency_ms for r in records_) / 1000, 1),
                "answer_chars": round(statistics.mean(r.answer_chars for r in records_)),
                "evidence_chunks": round(statistics.mean(len(r.evidence_chunk_ids) for r in records_), 1),
                "citation_unit": config.ROWS[row]["citation_unit"],
                "refs_asserted": sum(r.refs_asserted for r in records_),
                "refs_resolved": sum(r.refs_resolved for r in records_),
                "unresolved": sum(len(r.unresolved) for r in records_),
            }
            cell["citation_rate"] = (round(cell["refs_resolved"] / cell["refs_asserted"], 4)
                                     if cell["refs_asserted"] else None)
            hits = 0
            for question, record in zip(answered, records_):
                target = set(question["evidence_target"]["chunk_ids"])
                if target & set(record.evidence_chunk_ids):
                    hits += 1
            cell["retrieval_hit_rate"] = round(hits / len(answered), 4)
            # The papers' three criteria, and only those: Correctness is an accuracy check
            # reported beside the rubric, not a fourth judged dimension.
            quality_keys = list(prompts.criteria.RUBRIC)
            for key in criteria_for(label):
                per_question = []
                spreads = []
                for question in answered:
                    replicates = grouped.get((row, question["id"], key), {})
                    if not replicates:
                        continue
                    values = list(replicates.values())
                    per_question.append(statistics.mean(values))
                    if len(values) > 1:
                        spreads.append(statistics.stdev(values))
                cell[key] = round(statistics.mean(per_question), 4) if per_question else None
                cell[f"{key}_sd"] = round(statistics.mean(spreads), 4) if spreads else 0.0
                cell[f"{key}_n"] = len(per_question)
            present = [cell[key] for key in quality_keys if cell.get(key) is not None]
            cell["rubric_mean"] = round(statistics.mean(present), 4) if present else None
            cells.append(cell)
    return cells


def build_pairwise(*, rows: dict[str, dict[str, records.AnswerRecord]] | None = None,
                   pairwise: list[dict] | None = None) -> list[dict]:
    """Win counts per (class, row), per replicate and overall — the table's headline."""
    rows = rows or records.load_all()
    pairwise = load_pairwise() if pairwise is None else pairwise
    cells: list[dict] = []
    for label in select.CLASSES:
        records_ = [record for record in pairwise if record.get("class") == label and record.get("winner")]
        if not records_:
            continue
        # Only rows that actually competed. The arena is recorded with its contestants
        # (`presented_order`); a row that was never in it must not appear as 0 wins, which
        # reads as "lost every comparison" rather than "did not enter".
        contestants = {row for record in records_ for row in (record.get("presented_order") or [])}
        for row in config.ROW_ORDER:
            if contestants and row not in contestants:
                continue
            wins = [record for record in records_ if record["winner"] == row]
            per_replicate = {}
            for replicate in sorted({record["replicate"] for record in records_}):
                subset = [record for record in records_ if record["replicate"] == replicate]
                per_replicate[replicate] = round(
                    sum(1 for record in subset if record["winner"] == row) / len(subset), 4)
            cells.append({
                "class": label, "row": row, "label": config.ROWS[row]["label"],
                "questions": len({record["question_id"] for record in records_}),
                "comparisons": len(records_),
                "wins": len(wins),
                "win_rate": round(len(wins) / len(records_), 4),
                "replicate_min": min(per_replicate.values()),
                "replicate_max": max(per_replicate.values()),
                "replicate_sd": round(statistics.stdev(per_replicate.values()), 4)
                                 if len(per_replicate) > 1 else 0.0,
            })
    return cells


def build_blinding(blinding: list[dict] | None = None) -> dict:
    blinding = load_blinding() if blinding is None else blinding
    asked = [record for record in blinding if "correct" in record]
    if not asked:
        return {"asked": 0}
    return {
        "asked": len(asked),
        "correct": sum(record["correct"] for record in asked),
        "opportunities": sum(len(record["presented_order"]) for record in asked),
        "accuracy": round(sum(record["correct"] for record in asked)
                          / sum(len(record["presented_order"]) for record in asked), 4),
        "chance": round(statistics.mean(record["chance"] for record in asked), 4),
    }


# ---------------------------------------------------------------- writing


def write_csv(cells: list[dict], path=None) -> str:
    path = path or config.CSV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["row", "label", "class", "n", "replicates"]
    for key in CRITERIA_KEYS:
        columns += [key, f"{key}_sd", f"{key}_n"]
    columns += ["rubric_mean", "tokens_in", "tokens_out", "calls", "cost_usd", "latency_s", "answer_chars",
                "evidence_chunks", "retrieval_hit_rate", "citation_unit", "refs_asserted",
                "refs_resolved", "citation_rate", "unresolved"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for cell in cells:
            writer.writerow(cell)
    return str(path)


def _sha256(path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _qset_commit() -> str:
    """The commit that last touched the frozen set — §7.5 wants the sha, read-only."""
    try:
        return subprocess.run(["git", "log", "-1", "--format=%H", "--", "qset/questions.jsonl"],
                              cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return ""


def write_pins(cells: list[dict], *, extra: dict | None = None) -> dict:
    """Everything a stranger needs to re-derive the CSV — written beside it, not in a slide."""
    index_record = {}
    record_path = REPO / "parsed" / "kg_graph" / "index_record.json"
    if record_path.is_file():
        index_record = json.loads(record_path.read_text(encoding="utf-8"))
    lightrag_rev = ""
    try:
        from hyrag.kg_light import config as kg_light_config

        lightrag_rev = kg_light_config.LIGHTRAG_REV
    except Exception:
        pass
    import deepeval

    prompts_copy = prompts.criteria.as_dict()
    config.PROMPTS_COPY.write_text(json.dumps(prompts_copy, indent=1, ensure_ascii=False),
                                   encoding="utf-8")
    pins = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rubric_version": prompts.criteria.RUBRIC_VERSION,
        "prompts_sha256": _sha256(config.PROMPTS_COPY),
        "qset": {"path": "qset/questions.jsonl", "sha256": _sha256(select.QUESTIONS),
                 "commit": _qset_commit()},
        "protocol": {
            "replicates": config.REPLICATES, "seed": config.SEED,
            "temperature": config.TEMPERATURE, "thinking": "disabled",
            "judge_model": "deepseek-flash", "deepeval": deepeval.__version__,
            "evidence_in_prompt": "none — the pointwise matrix is judged without retrieval_context",
            "thematic_gold": "rubric only; not passed as expected_output",
            "citation_resolution": "deterministic; R2 at file granularity by the row's own shape",
            "thematic_sample": {
                "n": select.THEMATIC_SAMPLE_N,
                "rule": "systematic; every 2.5th thematic question in file order",
                "ids": [record["id"] for record in config.evaluation_set()
                        if record["class"] == "thematic"],
            },
        },
        "rows": {
            row: {"answers": str(config.ROWS[row]["answers"].relative_to(REPO)),
                  "sha256": _sha256(config.ROWS[row]["answers"]),
                  "records": len(records.load_row(row))}
            for row in config.ROW_ORDER
        },
        "indexes": {"graphrag": index_record.get("graphrag_pin", "graphrag==3.3.0"),
                    "graphrag_settings_sha256": index_record.get("settings_sha256", ""),
                    "lightrag": lightrag_rev},
        "rates_usd_per_m": config.RATES,
        "judge_calls": _judge_call_totals(),
        # The benchmark's rubric is the papers' three; the control is the sensitivity variant.
        "rubric": {
            "criteria": list(prompts.criteria.RUBRIC),
            "accuracy_beside_rubric": list(prompts.criteria.ACCURACY),
            "headline_pairwise": str(config.PAIRWISE_LOG.relative_to(REPO)),
            "head_to_head": {"log": str(config.PAIRWISE_LOG_PAIRS.relative_to(REPO)),
                             "records": _count(config.PAIRWISE_LOG_PAIRS),
                             "pairs": ["R1-R2", "R1-R3", "R2-R3"],
                             "use": "two-way comparisons — the papers' own claim format"},
            "sensitivity_pairwise": {
                "log": str(config.PAIRWISE_LOG_CONTROL.relative_to(REPO)),
                "records": _count(config.PAIRWISE_LOG_CONTROL),
                "criteria": "the three above plus Directness (a padding penalty)",
                "use": "sensitivity only — it ranks the rows in the opposite order; not in the table",
            },
        },
        "cells": [{key: cell[key] for key in ("row", "class", "n")} for cell in cells],
    }
    if extra:
        pins.update(extra)
    config.PINS_PATH.write_text(json.dumps(pins, indent=1, ensure_ascii=False), encoding="utf-8")
    return pins


def _count(path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) if path.is_file() else 0


def _judge_call_totals() -> dict:
    calls = load_calls()
    prompt = sum(call.get("prompt_tokens", 0) for call in calls)
    completion = sum(call.get("completion_tokens", 0) for call in calls)
    hit = sum(call.get("prompt_cache_hit_tokens", 0) for call in calls)
    return {
        "calls": len(calls), "prompt_tokens": prompt, "completion_tokens": completion,
        "prompt_cache_hit_tokens": hit,
        "prompt_cache_hit_share": round(hit / prompt, 4) if prompt else None,
        "cost_usd_upper_bound": config.cost_usd(prompt, completion),
        "errors": sum(1 for call in calls if call.get("error")),
    }


# ---------------------------------------------------------------- markdown


def strategy_table(cells: list[dict], pairwise: list[dict]) -> dict:
    """The class → arm mapping, read off the table rather than invented.

    Phase 2's router is configured from this, and its whole justification is that the mapping
    is a *derivation from Phase 1* rather than a belief about which system suits which
    question. So the rule is mechanical: per class, the arm with the highest mean judged
    quality, with the pairwise winner beside it (a mean over criteria and a forced choice can
    disagree — the disagreement is the interesting part) and the cost and latency that came
    with it. Fusion is *not* decided here: whether both arms are worth running for multi-hop
    is §7.4's ablation, and that needs Phase 2's bridged index.
    """
    out: dict[str, dict] = {}
    for label in select.CLASSES:
        subset = [cell for cell in cells if cell["class"] == label]
        if not subset:
            continue
        scored = []
        for cell in subset:
            if cell.get("rubric_mean") is None:  # no judged scores yet for this cell
                continue
            scored.append((cell["rubric_mean"], cell))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        wins = {entry["row"]: entry["win_rate"] for entry in pairwise if entry["class"] == label}
        best_wins = max(wins, key=wins.get) if wins else None
        pairwise_row = next((cell for cell in subset if cell["row"] == best_wins), scored[0][1])
        out[label] = {
            # A description of the benchmark, not a routing instruction. "Highest win rate"
            # is not a route: a route is chosen under a latency and cost budget, and this
            # table carries the budget so Phase 2's `router` can choose rather than inherit.
            "pairwise_winner": config.ROWS[pairwise_row["row"]]["label"],
            "pairwise_winner_row": pairwise_row["row"],
            "pairwise_win_rate": wins.get(best_wins) if wins else None,
            "pairwise_by_arm": wins or None,
            "pointwise_best": config.ROWS[scored[0][1]["row"]]["label"],
            "pointwise_best_mean": round(scored[0][0], 4),
            "arms": {config.ROWS[cell["row"]]["label"]: {
                        "rubric_mean": cell["rubric_mean"],
                        "cost_usd_per_query": cell["cost_usd"],
                        "latency_s_per_query": cell["latency_s"],
                        "pairwise_share_3way": (wins or {}).get(cell["row"]),
                        "retrieval_hit_rate": cell["retrieval_hit_rate"]}
                     for cell in subset},
            "n": pairwise_row["n"],
            "routing_note": ("A route is a choice under a latency/cost budget; Phase 2's router "
                             "node owns that choice. This file records the benchmark's ordering "
                             "and the per-arm cost, not a route."),
            "derived_from": "results.csv + judge_pairwise_paper3.jsonl",
        }
    return out


def write_head_to_head(head_to_head: list[dict], path=None) -> str:
    path = path or (config.RESULTS / "head_to_head.csv")
    import csv as _csv
    columns = ["class", "pair", "n", "left_wins", "right_wins", "left_share",
               "replicate_min", "replicate_max"]
    class _Writer(_csv.DictWriter):
        pass
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = _csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for cell in head_to_head:
            writer.writerow(cell)
    return str(path)


def write_strategy(cells: list[dict], pairwise: list[dict], path=None) -> dict:
    path = path or (config.RESULTS / "class_strategy.json")
    strategy = strategy_table(cells, pairwise)
    path.write_text(json.dumps(strategy, indent=1, ensure_ascii=False), encoding="utf-8")
    return strategy


def markdown(cells: list[dict], pairwise: list[dict], blinding: dict) -> str:
    lines: list[str] = []
    for label in select.CLASSES:
        subset = [cell for cell in cells if cell["class"] == label]
        if not subset:
            continue
        lines.append(f"### {label}")
        lines.append("")
        keys = criteria_for(label)
        headers = " | ".join("Accuracy" if key in prompts.criteria.ACCURACY
                             else prompts.criteria.CRITERIA[[item["key"] for item in prompts.criteria.CRITERIA].index(key)]["name"]
                             for key in keys)
        lines.append(f"| row | n | {headers} | mean (rubric) | cost/query | latency "
                     f"| answer chars | retrieval hit | citations |")
        lines.append("|---" * (3 + len(keys)) + "|")
        for cell in subset:
            scores = " | ".join("—" if cell.get(key) is None else f"{cell[key]:.2f}" for key in keys)
            lines.append(
                f"| {cell['label']} | {cell['n']} | {scores} | {cell['rubric_mean']} | ${cell['cost_usd']:.4f} "
                f"| {cell['latency_s']}s | {cell['answer_chars']:,} "
                f"| {cell['retrieval_hit_rate']:.0%} "
                f"| {cell['refs_resolved']}/{cell['refs_asserted']} "
                f"({cell['citation_rate']:.0%}) |")
        lines.append("")
    head_to_head = build_head_to_head()
    if head_to_head:
        lines.append("### head-to-head ladder (two-way, blind, per-question majority of 3)")
        lines.append("")
        lines.append("| class | pair | winner | share | across replicates |")
        lines.append("|---|---|---|---|---|")
        for cell in head_to_head:
            winner = cell["left"] if cell["left_wins"] >= cell["right_wins"] else cell["right"]
            share = max(cell["left_wins"], cell["right_wins"]) / cell["n"]
            lines.append(f"| {cell['class']} | {cell['left']} vs {cell['right']} "
                         f"| **{config.ROWS[winner]['label']}** | {share:.0%} "
                         f"| {cell['replicate_min']:.0%}–{cell['replicate_max']:.0%} (R1) |")
        lines.append("")
    if pairwise:
        lines.append("### three-way arena (blind, masked, majority of 3 replicates)")
        lines.append("")
        lines.append("| class | row | wins | win rate | across replicates |")
        lines.append("|---|---|---|---|---|")
        for cell in pairwise:
            lines.append(f"| {cell['class']} | {cell['label']} | {cell['wins']}/{cell['comparisons']} "
                         f"| {cell['win_rate']:.0%} | {cell['replicate_min']:.0%}–{cell['replicate_max']:.0%} |")
        lines.append("")
    if blinding.get("asked"):
        lines.append(f"**Blinding control:** the judge matched answers to systems "
                     f"{blinding['correct']}/{blinding['opportunities']} times "
                     f"({blinding['accuracy']:.0%}, chance {blinding['chance']:.0%}) "
                     f"over {blinding['asked']} questions.")
        lines.append("")
    return "\n".join(lines)
