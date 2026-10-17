"""router/classify.py — the standalone query, in; exactly one class, out.

The classes are `nodes/qset.md`'s, quoted rather than paraphrased: the router's accuracy is
measured against the frozen labels, so a definition that drifts here makes the number
meaningless even while the prompt looks reasonable.

**Few-shot examples come from the dev split only**, at fixed positions, and `bench` asserts
that no eval question's text appears in the system prompt. A prompt that carried an eval
question would be a leak the accuracy number could not show.

**The fallback is a rule, not a confidence.** The model is asked for one of three labels and a
malformed reply is retried once; if it is still malformed, a deterministic keyword rule picks a
label and the record says it fired. Three classes, always one label — a router that can return
"unknown" has a fourth, undocumented class, and `arms` has no branch for it.

The rule is a last resort, and it is deliberately dumb: *recurring / across / themes / pattern
/ differences between* suggest thematic; *why / how does … relate / compare / both / and what*
suggest multi-hop; everything else is factoid. If it fires more than rarely, the prompt is the
thing to fix — the count is printed by the gate for exactly that reason.
"""

from __future__ import annotations

import hashlib
import json
import re

from hyrag.router import config, model

CLASSIFIER_FRAME = """You route questions for a retrieval system over aircraft-maintenance and \
accident-investigation documents. Assign exactly one class, using these definitions verbatim.

- factoid: one fact, present in ONE chunk — a number, a limit, a name, a condition. \
If it needs a second chunk to be answerable, it is not factoid.
- multi-hop: requires connecting TWO OR MORE facts from TWO OR MORE places before an answer \
exists, or a relation between two named things. A question that is only long is not composite.
- thematic: asks for recurring patterns, differences or themes ACROSS documents; no single \
chunk answers it. A factoid wearing a plural is not thematic, and neither is a question that \
one report's summary already answers.

Reply with JSON only, no prose: {{"class": "factoid" | "multi-hop" | "thematic"}}.

Examples, two per class:
{examples}
"""

# Fixed positions inside each class's dev list — never the eval positions, and never sampled at
# call time (a prompt that changes per call cannot be hashed into the run record).
EXAMPLE_POSITIONS = (3, 23)

_THEMATIC = re.compile(r"recurring|across|theme|pattern|difference between|both manufacturers", re.I)
_MULTIHOP = re.compile(r"\bwhy\b|\brelate\b|\bcompare\b|\bboth\b|\band what\b|\bwhich systems\b", re.I)


def examples() -> list[tuple[str, str]]:
    """Two dev questions per class, fixed positions — the prompt's few-shot block."""
    picked: list[tuple[str, str]] = []
    for label in config.CLASSES:
        dev = config.dev_of(label)
        for position in EXAMPLE_POSITIONS:
            chosen = dev[position % len(dev)]
            picked.append((label, chosen["question"]))
    return picked


def system_prompt() -> str:
    block = "\n".join(f"- {label}: {question}" for label, question in examples())
    return CLASSIFIER_FRAME.format(examples=block)


def prompt_sha256() -> str:
    return hashlib.sha256(system_prompt().encode("utf-8")).hexdigest()


def fallback(question: str) -> str:
    """The deterministic last resort, in order: thematic markers, multi-hop markers, factoid."""
    if _THEMATIC.search(question):
        return "thematic"
    if _MULTIHOP.search(question):
        return "multi-hop"
    return "factoid"


def _parse(raw: str) -> str | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    label = str(payload.get("class") or "").strip().lower()
    return label if label in config.CLASSES else None


def classify(question: str) -> dict:
    """One question → one label, plus everything the record needs to explain it."""
    system = system_prompt()
    attempts: list[dict] = []
    label: str | None = None
    for attempt in range(2):  # one retry, because a JSON parse failure is cheap to re-ask
        result = model.complete(system, question, json_mode=True, max_tokens=30)
        attempts.append({**result, "label": _parse(result["text"])})
        label = attempts[-1]["label"]
        if label is not None:
            break

    used_fallback = label is None
    if used_fallback:
        label = fallback(question)

    return {
        "question": question,
        "label": label,
        "fallback": used_fallback,
        "attempts": len(attempts),
        "raw": [attempt["text"] for attempt in attempts],
        "usage": {
            key: sum(attempt["usage"][key] for attempt in attempts)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "latency_ms": sum(attempt["latency_ms"] for attempt in attempts),
        "prompt_sha256": prompt_sha256(),
    }
