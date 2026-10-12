"""qset/critique.py — the second reader: judging the drafts against the class definitions.

The drafting call's own `self_check` is nearly worthless — it clusters at 4–5 and flagged 1 of
360. This is the pass that does the work: each draft is re-read *against the class definition*
and the source excerpt, and gets hard yes/no answers on the properties the contract demands
(nodes/qset.md). The distinctive one is `single_question`: a "multi-hop" question of the form
"what did X do, and what does Y say" is two factoids joined by a conjunction, and the drafting
call cannot see its own habit.

Nothing here judges *quality* in the abstract — only whether a candidate is the class it claims
to be and whether its answer is where it says it is. Taste stays with the human, who reads the
shortlist this produces.

    python -m hyrag.qset.cli critique     # -> qset/critique.jsonl
    python -m hyrag.qset.cli shortlist    # -> qset/shortlist.jsonl + review.md
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from hyrag.parse.config import REPO
from hyrag.qset.draft import MODEL, PROMPT_VERSION, chat, load_sources, read_drafts

CRITIQUE = REPO / "qset" / "critique.jsonl"
SHORTLIST = REPO / "qset" / "shortlist.jsonl"
CRITIQUE_VERSION = "qset-critique-1"

_DEFINITIONS = {
    "factoid": "A FACTIOD question has one answer, present in ONE source chunk: a number, a limit, a "
               "name, a condition, a cause. It must not need a second chunk, and its answer must be "
               "short — at most about fifteen words, not a dispatch clause and not 'refer to …'.",
    "multi-hop": "A MULTI-HOP question needs TWO sources connected before its answer exists — a cause "
                 "and its consequence, a system and what it affects, a finding and the evidence behind "
                 "it, a requirement and what was done against it. It is ONE question about ONE thing. "
                 "A question of the form 'what did X do, and what does Y say' is two factoids joined "
                 "by a conjunction and is NOT multi-hop, however well it reads.",
    "thematic": "A THEMATIC question asks for themes or recurring lessons ACROSS documents — something "
                "no single chunk states. Its gold answer is a checklist of themes the sources actually "
                "support. The themes must be specific to these sources, not generic aviation wisdom "
                "that would fit any document set.",
}

_SYSTEM = """You are auditing evaluation questions before they are frozen into a benchmark. Be strict: \
the point of this pass is to reject candidates that do not hold up, and a question that slips through \
gets scored against real systems later. Answer only from the material given. Reply with JSON only."""

_PROMPT = """Class: {class_name}
Definition: {definition}

Question: {question}
Claimed answer: {gold_answer}
{support}
Source excerpt(s):
{sources}

Judge each property independently, strictly:

- class_correct: does the question really meet the definition above?
- answer_in_sources: is the claimed answer fully supported by the source excerpts — every figure,
  name and limit exactly as the excerpt states it?
- single_question: does it ask ONE thing? False if it asks two things joined by "and", "and also",
  "and what", or a second question mark.
- answer_short_enough: ONLY for a factoid class candidate, is the answer at most about fifteen
  words and specific? For a multi-hop or thematic candidate, answer true — a connection and a
  checklist are allowed to be longer, and judging them by length would reject the correct ones.
- corpus_only: is it answerable from these sources rather than from general knowledge?

JSON schema:
{{"class_correct": true|false, "answer_in_sources": true|false, "single_question": true|false,
  "answer_short_enough": true|false, "corpus_only": true|false,
  "score": 1-5, "why": "one short clause naming the decisive reason"}}"""


def _support(record: dict) -> str:
    if record.get("connection"):
        return f"Claimed connection: {record['connection']}\n"
    if record.get("themes_supported"):
        return "Claimed themes: " + " | ".join(record["themes_supported"]) + "\n"
    return ""


def critique_record(record: dict, source_text: dict[str, str] | None = None) -> dict:
    """Re-read one draft against the *full* text of every chunk it cites.

    Not the drafting-time excerpts: those are three chunks truncated to 700 characters, and a
    thematic question drawn from six documents then looks unsupported for its themes — the audit
    would reject a good question because it could not see the evidence. The chunk ids are the
    authority, so the full text is looked up from the ingest set.

    Failures are recorded, never raised: one bad call must not stop a run.
    """
    if record.get("error"):
        return {**record, "critique": {"rejected": True, "why": record["error"][:120]}}
    texts = source_text or {}
    chunks = [texts.get(evidence["chunk_id"], "") for evidence in record["evidence"]]
    if not any(chunks):  # no lookup table given: fall back to what the draft recorded
        chunks = record.get("review", {}).get("chunks", [])[:3]
    sources = "\n\n".join(f"[C{n}] {text[:3000]}" for n, text in enumerate(chunks, start=1) if text)
    prompt = _PROMPT.format(
        class_name=record["class"],
        definition=_DEFINITIONS[record["class"]],
        question=record["question"],
        gold_answer=record["gold_answer"],
        support=_support(record),
        sources=sources or "(no excerpt recorded)",
    )
    try:
        verdict = chat(_SYSTEM, prompt, temperature=0.0, max_tokens=1500)
    except Exception as exc:  # noqa: BLE001 — reported, not fatal
        return {**record, "critique": {"rejected": True, "why": f"critique call failed: {exc}"[:120]}}
    required = ["class_correct", "answer_in_sources", "single_question", "corpus_only"]
    if record["class"] == "factoid":
        required.append("answer_short_enough")  # only a factoid owes a short answer
    verdict["rejected"] = not all(verdict.get(key) for key in required)
    return {**record, "critique": verdict}


def run_critique(records: list[dict], *, workers: int = 8, verbose: bool = True) -> list[dict]:
    from functools import partial

    source_text = {source.chunk_id: source.content for source in load_sources()}
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(partial(critique_record, source_text=source_text), record): record
                   for record in records}
        for done, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            if verbose and done % 25 == 0:
                rejected = sum(1 for r in results if r["critique"].get("rejected"))
                print(f"  {done:>3}/{len(records)} read · {rejected} rejected so far")
    results.sort(key=lambda r: (r["class"], r["draft_id"]))
    return results


def write_critique(records: list[dict], path: Path = CRITIQUE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def read_critique(path: Path = CRITIQUE) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def select_shortlist(records: list[dict], *, target: int = 50, spares: int = 12) -> tuple[list[dict], dict]:
    """The pile worth a human's time: survivors, deduplicated, allocated **proportionally per document**.

    Why proportional and not "the best 62 by score": ranking by score alone handed the factoid class
    to three accident reports and dropped the FSB (26 survivors), both Safety First articles and every
    MMEL — a corpus benchmark whose questions come from three documents is measuring those documents.
    Each document contributes in proportion to how many survivors it has, best-scoring first within
    the document, so quality decides *which* of a document's candidates are offered, and balance
    decides how many.
    """
    survivors = [r for r in records if not r["critique"].get("rejected")]

    deduped: list[dict] = []
    seen: dict[str, dict] = {}
    for record in sorted(survivors, key=lambda r: (-int(r["critique"].get("score") or 0), r["draft_id"])):
        opening = " ".join(record["question"].lower().split()[:6]).strip("?.,: ")
        if opening in seen:
            continue
        seen[opening] = record
        deduped.append(record)

    chosen: list[dict] = []
    notes: dict[str, dict] = {}
    for class_name in ("factoid", "multi-hop", "thematic"):
        pool = [r for r in deduped if r["class"] == class_name]
        by_document: dict[str, list[dict]] = {}
        for record in pool:
            by_document.setdefault(record["evidence"][0]["file"], []).append(record)
        for document in by_document:                     # best first inside each document
            by_document[document].sort(key=lambda r: (-int(r["critique"].get("score") or 0), r["draft_id"]))

        total = sum(len(v) for v in by_document.values())
        quota = {document: max(1, round((target + spares) * len(rows) / total))
                 for document, rows in by_document.items()} if total else {}
        # hand out any remainder to the documents with the most survivors
        while sum(quota.values()) < min(target + spares, total):
            document = max(by_document, key=lambda d: (len(by_document[d]) - quota[d], d))
            if quota[document] >= len(by_document[document]):
                if all(quota[d] >= len(by_document[d]) for d in by_document):
                    break
                continue
            quota[document] += 1

        taken = 0
        for document in sorted(by_document):
            take = min(quota.get(document, 0), len(by_document[document]))
            chosen += by_document[document][:take]
            taken += take
        notes[class_name] = {"survivors": total, "offered": taken,
                             "documents": len(by_document),
                             "per_document": {d[:28]: min(quota.get(d, 0), len(v))
                                              for d, v in sorted(by_document.items())}}
    return chosen, notes


def render_shortlist(records: list[dict], path: Path) -> Path:
    """The review table, from the shortlist only — this is what a human reads."""
    from hyrag.qset.draft import render_review

    return render_review(records, path)
