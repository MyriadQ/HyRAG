"""qset/draft.py — drafting the question set from the chunk set.

The instrument the whole benchmark is measured with is 150 questions (50 factoid, 50
multi-hop, 50 thematic). This module produces **drafts**; a human filters them into
`qset/questions.jsonl` and freezes that. Nothing here writes the frozen file.

Why drafting is grounded in *chunks* and not in PDFs: every question must name the
chunk that contains its answer (the evidence target), and that target has to be true.
If the question is written from `parsed/_ingest.jsonl`, the target is known at the
moment the question exists — no reconstruction afterwards, and the gold answer can be
read off the same chunk.

The three classes need different material, so they get different prompts:

    factoid    one chunk                       the fact is in it
    multi-hop  two chunks, same document,      the answer needs both
               different sections
    thematic   chunks from several documents   a theme that spans them

Licence note: the drafts carry chunk text beside each question, so `qset/drafts.jsonl`
is **gitignored** — part of the corpus is © OEM material held under terms that forbid
posting it on any network. Only the filtered `questions.jsonl` is committed, and its
gold answers stay short paraphrases, the way a citation does.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hyrag.parse.config import REPO

INGEST = REPO / "parsed" / "_ingest.jsonl"
DRAFTS = REPO / "qset" / "drafts.jsonl"
QUESTIONS = REPO / "qset" / "questions.jsonl"

MODEL = "deepseek-flash"
PROMPT_VERSION = "qset-draft-3"
TEMPERATURE = 0.7
SEED = 20261011  # fixed: the *selection* of what to draft from must be reproducible


# ---------------------------------------------------------------- the model


def api_key() -> str:
    """`DEEPSEEK_API_KEY` from the environment, or from `.env` at the repo root."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    env = REPO / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("no DEEPSEEK_API_KEY: put it in .env at the repo root (gitignored)")


def chat(
    system: str,
    user: str,
    *,
    json_mode: bool = True,
    temperature: float = TEMPERATURE,
    max_tokens: int = 8000,
    attempts: int = 3,
) -> Any:
    """One call to DeepSeek, **thinking disabled**, returning parsed JSON.

    `thinking: {"type": "disabled"}` is the toggle — verified 2026-10-11: the same
    prompt costs 1 completion token with it and 68 without (`reasoning_effort: "low"`
    still thinks; `chat_template_kwargs` is ignored). Both this module and the judge
    (PREPLAN D8c) need the model to emit clean JSON, not a chain of thought.
    """
    payload: dict[str, Any] = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = json.loads(response.read())
            text = body["choices"][0]["message"]["content"]
            return json.loads(text) if json_mode else text
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))  # 2s, 4s, 6s — rate limits and blips
    raise RuntimeError(f"deepseek call failed after {attempts} attempts: {last}")


# ---------------------------------------------------------------- the material


@dataclass
class Source:
    """One chunk of the ingest set, reduced to what a prompt needs."""

    chunk_id: str
    file: str
    heading: str
    citation: str
    content: str
    tokens: int
    label: str = ""  # how the prompt should refer to it: [C1], [C2], …

    def block(self) -> str:
        return (
            f"{self.label} source: {self.file} | section: {self.heading or '(none)'} | "
            f"citation: {self.citation}\n{self.content}"
        )


def load_sources(path: Path = INGEST) -> list[Source]:
    """The chunk set, as prompt material. Read from the deduped ingest records."""
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    sources: list[Source] = []
    for record in records:
        if record.get("type") != "chunk":
            continue
        sources.append(
            Source(
                chunk_id=record["chunk_id"],
                file=record["file"],
                heading=record.get("heading", ""),
                citation=(record.get("occurrences") or [{}])[0].get("citation", ""),
                content=record["content"],
                tokens=record["tokens"],
            )
        )
    return sources


_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-]{3,}")
_STOP = {"with", "that", "this", "from", "when", "which", "will", "been", "they", "their", "have", "were", "must",
         "should", "into", "over", "than", "then", "also", "such", "each", "more", "most", "only", "upon", "does"}


def _terms(source: Source) -> set[str]:
    """Entity-shaped words in a chunk: acronyms, anything with a digit, or a long word."""
    out = set()
    for word in _TOKEN.findall(source.content):
        low = word.lower()
        if low in _STOP or len(word) < 4:
            continue
        if any(ch.isdigit() for ch in word) or word.isupper() or len(word) >= 7:
            out.add(low)
    return out


@dataclass
class Batch:
    """One drafting job: a class, the sources it is drafted from, and an id."""

    class_name: str
    sources: list[Source]
    index: int
    extra: dict = field(default_factory=dict)

    @property
    def draft_id(self) -> str:
        return f"{self.class_name[:4]}-{self.index:03d}"


def _allocate(total: int, available: dict[str, int]) -> dict[str, int]:
    """Per-document quotas: hand out one slot at a time to whoever has the most material left.

    Two failures to avoid, and this sits between them. Handing out slots by chunk count lets the
    four long reports own 80% of a class, and the MMEL dispatch conditions and Safety First
    procedures the assignment is actually about become a rounding error. Splitting evenly instead
    caps the pool at what the *smallest* document can give. Taking one slot at a time from the
    document with the most unused material does both: every document contributes, and a document
    with a hundred spare chunks still tops the class up.
    """
    remaining = dict(available)
    quota = {document: 0 for document in available}
    for _ in range(total):
        document = max(remaining, key=lambda d: (remaining[d], d))  # ties by name: deterministic
        if remaining[document] <= 0:
            break
        remaining[document] -= 1
        quota[document] += 1
    return quota


def select_batches(
    sources: list[Source], *, factoid: int, multi_hop: int, thematic: int, seed: int = SEED
) -> list[Batch]:
    """Pick what to draft from — spread across documents, deterministically.

    factoid   one chunk, big enough to hold a fact and small enough to read
    multi-hop two chunks of one document, in different sections, sharing a rare term
              (the shared term is the hint that a connection exists to be asked about)
    thematic  up to six chunks from different documents, all containing one term that
              travels — a theme that lives in one document would be a summary instead
    """
    rng = random.Random(seed)
    batches: list[Batch] = []

    readable = [s for s in sources if 60 <= s.tokens <= 700]
    by_document: dict[str, list[Source]] = {}
    for source in readable:
        by_document.setdefault(source.file, []).append(source)

    # -- factoid: a quota per document, so the class follows the corpus and not the chunk counts
    order = sorted(by_document)
    quota = _allocate(factoid, {document: len(by_document[document]) for document in order})
    picked: list[Source] = []
    for document in order:
        pool = list(by_document[document])
        rng.shuffle(pool)
        picked += pool[:quota[document]]
    for index, source in enumerate(picked):
        batches.append(Batch("factoid", [source], index))

    # -- multi-hop: groups that share a rare term but sit in different sections. Three when
    #    three exist, so the model can pick the pair that really connects — two otherwise, and
    #    then it either finds the connection or refuses with {"skip": true}.
    triples: list[list[Source]] = []
    for document, chunks in sorted(by_document.items()):
        term_to_chunks: dict[str, list[Source]] = {}
        for chunk in chunks:
            for term in _terms(chunk):
                term_to_chunks.setdefault(term, []).append(chunk)
        for term, holders in sorted(term_to_chunks.items()):
            if not 2 <= len(holders) <= 8:
                continue
            distinct: list[Source] = []
            seen_headings = set()
            for holder in holders:
                if holder.heading not in seen_headings:
                    distinct.append(holder)
                    seen_headings.add(holder.heading)
            if len(distinct) >= 2:
                triples.append(distinct[:3])
    # the same quota applies here: a report with forty sections must not own the class
    by_document_triples: dict[str, list[list[Source]]] = {}
    for group in triples:
        by_document_triples.setdefault(group[0].file, []).append(group)
    quota = _allocate(multi_hop, {document: len(pool) for document, pool in by_document_triples.items()})
    chosen: list[list[Source]] = []
    for document in sorted(by_document_triples):
        pool = by_document_triples[document]
        rng.shuffle(pool)
        chosen += pool[:quota[document]]
    rng.shuffle(chosen)
    for index, group in enumerate(chosen):
        batches.append(Batch("multi-hop", group, index, {}))

    # -- thematic: one term that appears in several documents
    term_documents: dict[str, dict[str, list[Source]]] = {}
    for document, chunks in by_document.items():
        for chunk in chunks:
            for term in _terms(chunk):
                term_documents.setdefault(term, {}).setdefault(document, []).append(chunk)
    spread = [(term, docs) for term, docs in term_documents.items() if len(docs) >= 3]
    spread.sort(key=lambda item: (-len(item[1]), item[0]))
    rng.shuffle(spread)  # sorted by spread, then shuffled *within* the same spread
    spread.sort(key=lambda item: -len(item[1]))
    # a thematic group should look across the corpus, not across its six largest documents:
    # walk the document list from a rotating start, taking one chunk per document that has the term
    rotating = sorted(by_document)
    for index, (term, documents) in enumerate(spread[:thematic]):
        start = index % len(rotating)
        walk = rotating[start:] + rotating[:start]
        holders = [document for document in walk if document in documents][:6]
        if len(holders) < 3:
            continue
        group = [rng.choice(documents[document]) for document in holders]
        batches.append(Batch("thematic", group, index, {"term": term}))

    return batches


# ---------------------------------------------------------------- the prompts

_SYSTEM = """You are building an evaluation question set for a retrieval benchmark over aircraft \
maintenance, safety and accident documents. Your questions must be answerable ONLY from the \
sources you are given, and every answer must be found in them.

Absolute rules:
- Never mention the retrieval system, the evaluation, "documents provided", or anything about the task.
- Write the question the way a maintainer, pilot or investigator would ask it.
- The answer must be short and factual, and must come from the sources — quote figures, names and
  limits exactly as they appear.
- Reply with JSON only, in the schema you are given."""

_PROMPTS = {
    "factoid": """Here is one source chunk.

{blocks}

Write ONE question that this chunk answers by itself: a single fact — a number, a limit, a name,
a condition, a cause. The question must need nothing beyond this chunk.
The answer must be SHORT and specific: a number, a name, a limit, or a phrase of at most about
fifteen words. If the only answer available is a long conditional clause (a dispatch rule, a list of
provisos), pick a different fact from the same chunk. Never answer with "see section …".
Disqualify yourself if the answer needs a second source.

JSON schema:
{{"question": "...", "gold_answer": "...",
  "self_check": {{"answer_in_source": true, "needs_only_this_source": true, "class_fit": 1-5}}}}""",
    "multi-hop": """Here are {count} source chunks from the same document, in different sections.

{blocks}

Find a question whose answer requires **connecting two of them** — a cause and its consequence, a
system and what it affects, a finding and the evidence behind it, a requirement and what was done
against it. Then write it.

THE SHAPE RULE, and the most common way this goes wrong: the question must contain exactly ONE
interrogative — one "what", "which", "why" or "how". It must not be two questions joined by "and",
"and also", or "and what". These are WRONG, however well they read:

  ✗ "What did the ARFF station do, and how did that affect their arrival?"   (two questions)
  ✓ "Why did the ARFF station reach the accident site from the wrong direction?"  (one question,
     whose answer needs both sources)

Before you answer, count the interrogatives. If there are two, rewrite it as one.

If no such connection exists between any two of these chunks, say so instead of inventing one:
{{"skip": true, "why": "no connection"}}

Otherwise, JSON schema:
{{"question": "...", "gold_answer": "...", "connection": "one clause: what the two chunks together establish",
  "needs_sources": ["C1", "C2"],
  "self_check": {{"answer_in_source": true, "needs_only_this_source": true, "class_fit": 1-5}}}}""",
    "thematic": """Here are source chunks from several different documents.

{blocks}

Write ONE question that asks for themes, patterns or recurring lessons ACROSS these documents —
something no single chunk states. Then give, as the gold answer, the list of themes these sources
actually support (2 to 5 themes, each one short).

Two rules. The themes must be specific to what these sources show — not generic aviation wisdom that
would fit any document set. And vary your phrasing: do not start every question with "Across these …";
ask it the way a person would (what keeps recurring, what do these have in common, what lesson do
these share).

JSON schema:
{{"question": "...", "gold_answer": "themes, one line each",
  "themes_supported": ["...", "..."],
  "self_check": {{"answer_in_source": true, "needs_only_this_source": true, "class_fit": 1-5}}}}""",
}


def _render(batch: Batch) -> str:
    for number, source in enumerate(batch.sources, start=1):
        source.label = f"[C{number}]"
    blocks = "\n\n".join(source.block() for source in batch.sources)
    return _PROMPTS[batch.class_name].format(blocks=blocks, count=len(batch.sources))


_CONJUNCTIVE = re.compile(r",\s+and\s+(what|how|why|which|where|who)\b|\band also\b|\band what\b", re.I)

_SHAPE_RETRY = """

Your previous answer asked TWO things joined by a conjunction. That is not a multi-hop question.
Rewrite it as ONE question with exactly one interrogative — one "what", "which", "why" or "how" —
whose answer still cannot exist without both sources."""


def _is_conjoined(question: str) -> bool:
    """Two questions in one: two interrogatives, or two joined by a conjunction."""
    marks = question.count("?")
    interrogatives = len(re.findall(r"\b(what|which|why|how|who|where)\b", question, re.I))
    return marks > 1 or interrogatives > 1 or bool(_CONJUNCTIVE.search(question))


def _record(batch: Batch, answer: dict) -> dict:
    """One candidate, built from the model's answer."""
    return {
        "draft_id": batch.draft_id,
        "class": batch.class_name,
        "question": (answer.get("question") or "").strip(),
        "gold_answer": (answer.get("gold_answer") or "").strip(),
        "evidence": [
            {"chunk_id": source.chunk_id, "file": source.file, "heading": source.heading,
             "citation": source.citation}
            for source in batch.sources
        ],
        "self_check": answer.get("self_check", {}),
        "connection": answer.get("connection", ""),
        "themes_supported": answer.get("themes_supported", []),
        "selection": batch.extra,
        "source": {
            "model": MODEL,
            "prompt_version": PROMPT_VERSION,
            "temperature": TEMPERATURE,
            "thinking": "disabled",
            "drafted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        # kept for review only — this file is gitignored, it carries © OEM text
        "review": {"chunks": [source.content[:700] for source in batch.sources]},
    }


def draft_batch(batch: Batch) -> dict:
    """Draft one candidate. Failures are returned as a record, never raised — one bad call
    must not end a six-hundred-call run.

    The shape rule is enforced mechanically, not only in prose: the drafting call slips into
    two questions ("… and what did X do?") often enough that a prompt forbidding it is not
    enough. One rewrite, then the candidate is dropped — a conjoined "multi-hop" question is
    not worth a human's time.
    """
    try:
        answer = chat(_SYSTEM, _render(batch))
    except Exception as exc:  # noqa: BLE001 — the caller reports failures, it does not crash on them
        return {"draft_id": batch.draft_id, "class": batch.class_name, "error": str(exc)[:300]}
    if answer.get("skip"):
        return {"draft_id": batch.draft_id, "class": batch.class_name,
                "error": f"model skipped: {answer.get('why', 'no connection')}"[:120]}

    record = _record(batch, answer)

    if batch.class_name == "multi-hop" and _is_conjoined(record["question"]):
        try:
            again = chat(_SYSTEM, _render(batch) + _SHAPE_RETRY)
        except Exception as exc:  # noqa: BLE001
            return {"draft_id": batch.draft_id, "class": batch.class_name,
                    "error": f"shape retry failed: {exc}"[:200]}
        if again.get("skip") or _is_conjoined(again.get("question", "")):
            return {"draft_id": batch.draft_id, "class": batch.class_name,
                    "error": "still two questions after a rewrite — dropped"}
        record = _record(batch, again)

    if not record["question"] or not record["gold_answer"]:
        record["error"] = "model returned an empty question or answer"
    return record


def run(batches: list[Batch], *, workers: int = 8, verbose: bool = True) -> list[dict]:
    """Draft every batch, `workers` at a time, printing progress as it goes.

    A future that raises would otherwise kill the whole run — that is how a 400-candidate
    multi-hop run died at 150 with nothing written (an `UnboundLocalError` in the shape-retry
    block, hidden behind a `tail`). `draft_batch` catches its own failures, and this loop is
    the second line of defence: one bad future becomes one error record.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(draft_batch, batch): batch for batch in batches}
        for done, future in enumerate(as_completed(futures), start=1):
            try:
                record = future.result()
            except Exception as exc:  # noqa: BLE001
                batch = futures[future]
                record = {"draft_id": batch.draft_id, "class": batch.class_name,
                          "error": f"unexpected: {exc}"[:200]}
            results.append(record)
            if verbose:
                mark = "ERR" if record.get("error") else "ok "
                print(f"  [{done:>3}/{len(batches)}] {mark} {record['draft_id']:<10} "
                      f"{(record.get('question') or record.get('error', ''))[:78]}")
    results.sort(key=lambda r: (r["class"], r["draft_id"]))
    return results


# ---------------------------------------------------------------- review + freeze


def write_drafts(records: list[dict], path: Path = DRAFTS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def read_drafts(path: Path = DRAFTS) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def render_review(records: list[dict], path: Path) -> Path:
    """A markdown table to read and strike through — the hand-filter step."""
    lines = ["# qset — drafts for review", "",
             f"{len(records)} candidates from `{PROMPT_VERSION}` ({MODEL}, thinking disabled). "
             "Keep the ones you want in `qset/keep.txt` — one `draft_id` per line — then run "
             "`python -m hyrag.qset.cli freeze`.", ""]
    for class_name in ("factoid", "multi-hop", "thematic"):
        rows = [r for r in records if r["class"] == class_name]
        usable = [r for r in rows if not r.get("error")]
        lines += [f"## {class_name} — {len(usable)} drafts"
                  + (f" ({len(rows) - len(usable)} failed)" if len(rows) != len(usable) else ""), ""]
        if not usable:
            lines += ["*none*", ""]
            continue
        lines += ["| keep? | id | fit | question | gold answer | citation |",
                  "|---|---|---|---|---|---|"]
        for record in sorted(usable, key=lambda r: -int(r["self_check"].get("class_fit") or 0)):
            check = record["self_check"]
            flags = "".join("" if check.get(key) else " ⚠"
                            for key in ("answer_in_source", "needs_only_this_source"))
            citation = record["evidence"][0]["citation"].replace("|", "/")[:74]
            question = record["question"].replace("|", "/")[:110]
            gold = record["gold_answer"].replace("|", "/").replace("\n", "; ")[:80]
            lines.append(f"|  | `{record['draft_id']}` | {check.get('class_fit', '?')}{flags} | "
                         f"{question} | {gold} | {citation} |")
        lines.append("")
        if class_name == "thematic":
            lines += ["Thematic gold answers are *checklists* of supported themes — the judge scores "
                      "coverage, not similarity (PREPLAN §7.2).", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# What a question may not mention: a system, a method, or the fact that sources were provided.
# A question that says "in the provided documents" or "per the knowledge graph" measures something
# other than retrieval.
_BANNED = re.compile(
    r"\b(graph|knowledge graph|community report|entity|entities|retrieval|RAG|vector|embedding"
    r"|provided (?:sources|documents|chunks)|these sources|the corpus)\b", re.I)


def validate(questions: list[dict], ingest_chunk_ids: set[str], *, expected: int = 50) -> list[str]:
    """The gate, as a function: 50/50/50, resolvable targets, no banned words (nodes/qset.md)."""
    problems: list[str] = []
    for class_name in ("factoid", "multi-hop", "thematic"):
        count = sum(1 for q in questions if q["class"] == class_name)
        if count != expected:
            problems.append(f"{class_name}: {count} questions, expected {expected}")
    for question in questions:
        if _BANNED.search(question["question"]):
            problems.append(f"{question['id']}: the question names a system, a method or our sources")
        if not question["gold_answer"].strip():
            problems.append(f"{question['id']}: no gold answer")
        target = question["evidence_target"]
        if not target["citations"]:
            problems.append(f"{question['id']}: no citation — a target that cannot be shown")
        unknown = [c for c in target["chunk_ids"] if c not in ingest_chunk_ids]
        if unknown:
            problems.append(f"{question['id']}: {len(unknown)} evidence chunk(s) not in the ingest set")
    return problems


def freeze(keep: list[str], records: list[dict], path: Path = QUESTIONS) -> tuple[Path, dict]:
    """Write the frozen question set from the ids a human kept."""
    kept = [r for r in records if r["draft_id"] in set(keep)]
    missing = sorted(set(keep) - {r["draft_id"] for r in kept})
    questions = []
    for number, record in enumerate(sorted(kept, key=lambda r: (r["class"], r["draft_id"]))):
        if record.get("error"):
            continue
        questions.append({
            "id": f"{record['class'][:4]}-{number:03d}",
            "class": record["class"],
            "question": record["question"],
            "gold_answer": record["gold_answer"],
            "themes_supported": record.get("themes_supported", []),
            "evidence_target": {
                "files": sorted({e["file"] for e in record["evidence"]}),
                "chunk_ids": [e["chunk_id"] for e in record["evidence"]],
                "citations": [e["citation"] for e in record["evidence"]],
            },
            "draft": {"model": record["source"]["model"],
                      "prompt_version": record["source"]["prompt_version"]},
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for question in questions:
            handle.write(json.dumps(question, ensure_ascii=False, sort_keys=True) + "\n")
    counts = {name: sum(1 for q in questions if q["class"] == name)
              for name in ("factoid", "multi-hop", "thematic")}
    return path, {"kept": len(questions), "per_class": counts, "unknown_ids": missing}
