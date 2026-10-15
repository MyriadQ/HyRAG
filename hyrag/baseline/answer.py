"""baseline/answer.py — evidence in, one cited answer out, one record per question.

    uv run python -m hyrag.baseline.cli answer --question-id fact-003   # one question, traced
    uv run python -m hyrag.baseline.cli answer --all                    # all 150 -> results/

The row's last two stages, in one module because they are inseparable: an answer nobody
recorded is an answer nobody can measure.

**What the model gets.** The retrieval result as numbered blocks, each led by its own
citation string:

    [1] aaib_1-2025_b737-8200_ei-het.pdf, §CVR, p. 10
    During the approach, there was no discussion to indicate …

and one rule that the whole benchmark rests on: **cite by copying the string from the block,
never by composing one.** A page number the model invents looks verified and is not — the
failure D13 and the whole parse node exist to prevent. So the rule is not left to trust: after
generation, a deterministic check looks for file names in the answer that are not among the
evidence's (`verify_citations`). It catches an invented *document*; inventing a page inside a
real document is caught later, by the citation-resolution metric, which checks the cited page
against the parse provenance (§7.3, D8b).

**Thinking is on.** This is the QUERY role (§6.1), so the payload deliberately omits the
`thinking: {"type": "disabled"}` key that `hyrag.qset.draft` sets. Reasoning tokens are billed
and arrive inside the API's `usage` block, which is why that block is stored raw rather than
summarised by us — §7.3 compares cost and latency across four rows, and a number this row
computed differently from the others would not be comparable.

**Temperature 0.** The row should be reproducible; §7.2's replicates are the *judge's*
stochasticity. Whether the judge wants three answers per question or three passes over one
answer is the `judge` node's decision — it is recorded here, not decided here.

**Two failure modes found on the first full run, and what closed them.**

* *The reasoning budget.* `max_tokens` caps reasoning *and* answer together. At 8000, three
  thematic questions spent every token thinking: two returned an empty string, one returned
  1497 characters of answer. Measured on a re-run at 16384, the same question finished its
  reasoning in 3352 tokens and produced 7930 characters. The ceiling is now 16384 — roughly
  double the largest non-truncated completion observed.
* *The dash.* The model writes `p. 62-63` where our citation says `p. 62–63`: an ASCII hyphen
  for the en dash `format_page_range` emits. Checking the answer by substring therefore
  missed a correct citation. `_fold` normalises dash variants and whitespace on **both**
  sides before comparing, and the record keeps *our* canonical string rather than the
  model's rendering. **This affects the judge too** — any citation-resolution check done by
  string matching is dash-sensitive, and the parse node's landmine about `U+2212` in AIM
  labels is the same family of bug.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from hyrag.baseline import store
from hyrag.parse.config import REPO

RESULTS = REPO / "results"
ANSWERS = RESULTS / "baseline_answers.jsonl"

MODEL = "deepseek-flash"
PROMPT_VERSION = "baseline-answer-1"
TEMPERATURE = 0.0
MAX_TOKENS = 16384  # see below — at 8000, thinking consumed the whole budget and answers came back empty
K = 20  # recorded in the report beside the row (nodes/baseline.md → Open questions)
DEFAULT_URL = "https://api.deepseek.com/chat/completions"
_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}

SYSTEM = """You answer questions about aviation documents using only the numbered evidence \
given to you.

Rules:
1. Use only the evidence below. Do not use outside knowledge, and do not guess.
2. Cite after every statement of fact by copying the source's citation string exactly as it \
appears at the top of the evidence block. It looks like this:

       document.pdf, §Section name, p. 12-13

   Copy it character for character, inside square brackets, after the sentence it supports. \
Never shorten it, never invent a document that is not in the evidence, never invent a page.
3. If the evidence does not contain the answer, say so plainly and name what is missing. \
That is a correct answer, not a failure.
4. Answer in your own words rather than pasting evidence wholesale.

Example, to show the shape only. The document in it is NOT in your evidence, so do not reuse \
its citation:

    [1] sample-notes.pdf, §Arrival briefing, p. 7-8
    The arrival briefing covered the approach and the missed-approach procedure.
    Question: What did the arrival briefing cover?
    Answer: The arrival briefing covered the approach and the missed-approach procedure \
[sample-notes.pdf, §Arrival briefing, p. 7-8]."""


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


def endpoint() -> str:
    """The chat-completions URL, from `DEEPSEEK_BASE_URL` or the default.

    `hyrag.qset.draft` hardcodes the same default and ignores the `.env` entry; reading it
    here keeps the endpoint in one place, which is what the other two rows will need too.
    """
    base = os.environ.get("DEEPSEEK_BASE_URL")
    if base:
        return f"{base.rstrip('/')}/chat/completions"
    env = REPO / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if line.startswith("DEEPSEEK_BASE_URL="):
                return f"{line.split('=', 1)[1].strip().rstrip('/')}/chat/completions"
    return DEFAULT_URL


def complete(system: str, user: str, *, attempts: int = 3) -> tuple[str, dict, int]:
    """One completion. Returns `(text, the API's raw usage block, wall-clock ms)`.

    Same retry shape as `hyrag.qset.draft.chat` — stdlib `urllib`, no client library — with
    two differences that matter: thinking stays **on**, and the latency measured is around the
    HTTP call only (not prompt assembly, not the database), because that is what the judge's
    latency column compares between rows.
    """
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
    }
    last: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            endpoint(),
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                body = json.loads(response.read())
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            message = body["choices"][0]["message"]
            return message["content"], body.get("usage", {}), elapsed_ms
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"deepseek call failed after {attempts} attempts: {last}")


# ---------------------------------------------------------------- the prompt


def build_user_message(question: str, rows: list[dict]) -> str:
    """The evidence blocks, then the question. Nothing else — no system text, no preamble."""
    blocks = [
        f"[{rank}] {row['citation']}\n{row['content'].strip()}"
        for rank, row in enumerate(rows, 1)
    ]
    return "Evidence:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}"


def prompt_sha(system: str) -> str:
    """The prompt's identity, for §7.5: a result that cannot be re-derived is not a result."""
    return hashlib.sha256((PROMPT_VERSION + "\0" + system).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- the check


# The model normalises our en dash (`p. 62–63`) to an ASCII hyphen (`p. 62-63`). Any
# comparison against a stored citation has to fold them, or a correct citation reads as a
# missing one — including the judge's citation-resolution metric (§7.3, D8b).
_DASH_FOLD = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                            "\u2014": "-", "\u2015": "-", "\u2212": "-"})


def _fold(text: str) -> str:
    """Dash- and whitespace-normalised, for comparing against a stored citation."""
    return " ".join(text.translate(_DASH_FOLD).split())


def verify_citations(answer: str, rows: list[dict]) -> tuple[list[str], list[str]]:
    """`(citations used, files mentioned but not in the evidence)`.

    The deterministic half of the citation-resolution metric (§7.3, D8b). `used` is found by
    substring over the *folded* forms — the evidence strings are exact, so anything the model
    copied is found — and the record keeps our canonical string, never the model's rendering.
    A `.pdf` name in the answer that is in no block is a fabricated source, which is the one
    failure worth stopping the line for.
    """
    folded = _fold(answer)
    used = [row["citation"] for row in rows if _fold(row["citation"]) in folded]
    evidence_files = {row["file"] for row in rows}
    mentioned = set(re.findall(r"[\w.\-]+\.pdf", answer))
    return used, sorted(mentioned - evidence_files)


# ---------------------------------------------------------------- one record


def answer_question(conn, question: dict, k: int = K) -> dict:
    """Retrieve, generate, verify, and return the row's record for one question."""
    rows = store.search(conn, question["question"], k=k)
    user = build_user_message(question["question"], rows)
    answer, usage, latency_ms = complete(SYSTEM, user)
    used, unknown = verify_citations(answer, rows)

    return {
        "question_id": question["id"],
        "class": question["class"],
        "answer": answer,
        "citations": used,
        "evidence": [
            {"chunk_id": row["chunk_id"], "citation": row["citation"],
             "distance": float(row["distance"])}
            for row in rows
        ],
        "usage": usage,
        "latency_ms": latency_ms,
        "model": MODEL,
        "k": k,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": prompt_sha(SYSTEM),
        "unknown_sources": unknown,
        # not shown to the judge: the gate's own bookkeeping, kept so a failure is diagnosable
        "evidence_rank": _evidence_rank(question, rows),
    }


def _evidence_rank(question: dict, rows: list[dict]) -> int | None:
    """Where the question's own evidence chunk landed in this row's top-k, 1-based."""
    targets = set(question["evidence_target"]["chunk_ids"])
    for rank, row in enumerate(rows, 1):
        if row["chunk_id"] in targets:
            return rank
    return None


def write_records(records: list[dict], path: Path = ANSWERS) -> Path:
    """One JSON object per line; the file is gitignored because it carries corpus text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, **_JSON) + "\n")
    return path


def totals(records: list[dict]) -> dict:
    """Row-level numbers for the report: tokens in and out, mean latency, citations found."""
    prompt = sum(int(r["usage"].get("prompt_tokens", 0)) for r in records)
    completion = sum(int(r["usage"].get("completion_tokens", 0)) for r in records)
    return {
        "questions": len(records),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "mean_latency_ms": int(sum(r["latency_ms"] for r in records) / max(len(records), 1)),
        "with_citation": sum(1 for r in records if r["citations"]),
        "empty_answers": sum(1 for r in records if not r["answer"].strip()),
        "unknown_sources": sum(1 for r in records if r["unknown_sources"]),
    }
