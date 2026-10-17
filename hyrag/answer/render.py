"""answer/render.py — the citation contract, the prompt, and the one call.

The contract is the whole box. The model is handed the evidence **with its citation strings**
and told to copy them character for character; the checker then compares what the answer uses
against that same list, folded (en dash, whitespace — `baseline/answer.py::_fold`). A model that
paraphrases a citation fails the check, which is correct: a citation nobody can follow is a
citation that does not exist.

"Answer or say you cannot" is deliberate. The corpus is a fixed slice; some questions are
outside it, and a delivered system that invents a page is worse than one that says the evidence
does not cover the question. The count of those answers is recorded, not hidden.

`temperature = 0` and thinking off: the same evidence must produce the same answer, and the
delivery path has no reason to buy reasoning tokens it does not use.
"""

from __future__ import annotations

import json
import time

from openai import OpenAI

from hyrag.kg_light import config as kg_light_config

MAX_ANSWER_TOKENS = 900
ROW_TAG = "answer"

SYSTEM = """You answer questions about aircraft maintenance and accident-investigation \
documents, using ONLY the evidence provided.

Rules:
- Cite every factual claim with the evidence's exact citation string in parentheses, copied \
character for character from its header — for example (aaib_1-2025_b737-8200_ei-het.pdf, \
§Airport information, p. 6-7). Never invent a section, a page or a document.
- Use only what the evidence says. If the evidence does not answer the question, say so in one \
sentence: "The provided evidence does not answer this." Do not guess.
- Answer first, then the supporting detail. No restating of the question, no preamble.
- Prefer numbers, names and limits verbatim from the evidence."""


def build_user(question: str, entries: list[dict]) -> str:
    blocks = []
    for index, entry in enumerate(entries, start=1):
        blocks.append(f"[{index}] {entry['citation']}\n{entry['text']}")
    return f"Question: {question}\n\nEvidence:\n\n" + "\n\n".join(blocks)


def generate(question: str, entries: list[dict]) -> dict:
    """One completion. Returns the text plus the usage the record needs."""
    client = OpenAI(base_url=kg_light_config.LLM_BASE,
                    api_key=kg_light_config.deepseek_key())
    started = time.perf_counter()
    response = client.chat.completions.create(
        model=kg_light_config.LLM_MODEL,
        temperature=0.0,
        max_tokens=MAX_ANSWER_TOKENS,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": build_user(question, entries)},
        ],
        extra_body={"thinking": {"type": "disabled"}},
        extra_headers={"X-Hyrag-Row": ROW_TAG},
    )
    usage = response.usage
    return {
        "answer": (response.choices[0].message.content or "").strip(),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        },
        "prompt_sha256": __import__("hashlib").sha256(SYSTEM.encode()).hexdigest(),
    }


def refusal(answer: str) -> bool:
    """The system's own "the evidence does not answer this" — a valid outcome, counted.

    The phrase alone is not enough: a substantive answer can end with a caveat ("the provided
    evidence does not show a single cross-report finding that ties these together") and that is
    an answer, not a refusal. So the marker must also be a *short* answer — the refusal is a
    sentence, not a paragraph — or start the reply.
    """
    lowered = answer.lower()
    marker = "does not answer" in lowered or "provided evidence does not" in lowered
    return marker and (len(answer) < 400 or lowered.lstrip().startswith("the provided evidence"))


def dumps(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
