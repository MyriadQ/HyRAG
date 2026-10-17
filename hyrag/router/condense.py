"""router/condense.py — this turn plus the history, in; one standalone query, out (D6).

The router sees the condensed query, never the elliptical turn. `PREPLAN.md` §2.3's example is
the specification: turn 1 passes through, *"What about the 737?"* on turn 2 comes out as the
full question with the entity restored. Without it both the router and the arms see a question
with no attribute in it.

Two failure modes are worth naming because the gate checks for them:

* **Hallucinated drift** — the rewrite invents an attribute the user did not ask about (turn 2's
  "what about the 737?" becoming a range question when the conversation was about altitude).
  The test asserts the entity *and* the attribute survive the rewrite.
* **Self-contained turns rewritten anyway** — every rewrite is a chance to change the question,
  and a changed question changes the label. The prompt says to pass a standalone turn through
  unchanged; the gate checks that turn 1 of the script survives verbatim in its content words.

History is rendered oldest-first as `User:` / `Assistant:` lines. The window is the last
`HISTORY_TURNS` exchanges: an unrestricted window grows the prompt on every turn of a long
conversation and buys nothing the last few exchanges do not already carry.
"""

from __future__ import annotations

import re

from hyrag.router import model

HISTORY_TURNS = 6  # user+assistant messages, i.e. the last three exchanges

CONDENSE_SYSTEM = """You rewrite the last user turn of a conversation into ONE standalone \
question, using the conversation before it.

Rules:
- If the last turn is already self-contained, return it unchanged.
- Restore what the turn refers to: pronouns and ellipses take the entity from the conversation.
- Keep the ATTRIBUTE the conversation is about. If the user asks "what about the 737?", the \
attribute is whatever the previous question asked; do not substitute a different one.
- Reply with the question only — no preamble, no explanation, no quotes.

Conversation:
{history}

Last user turn: {turn}

Standalone question:"""


def render_history(history: list[dict], limit: int = HISTORY_TURNS) -> str:
    """The last `limit` messages as text lines; empty history renders as `(none)`."""
    if not history:
        return "(none)"
    recent = history[-limit:]
    lines = []
    for message in recent:
        role = "User" if message.get("role") == "user" else "Assistant"
        lines.append(f"{role}: {str(message.get('content') or '').strip()}")
    return "\n".join(lines)


def condense(turn: str, history: list[dict] | None = None) -> dict:
    """One call. Returns the standalone query plus the usage the record needs."""
    history = history or []
    system = CONDENSE_SYSTEM.format(history=render_history(history), turn=turn)
    result = model.complete(system, turn, json_mode=False, max_tokens=200)
    query = _clean(result["text"]) or turn
    return {
        "turn": turn,
        "history_len": len(history),
        "query": query,
        "changed": query.strip() != turn.strip(),
        "usage": result["usage"],
        "latency_ms": result["latency_ms"],
    }


def _clean(text: str) -> str:
    """Strip the wrappers a model adds even when told not to, and collapse whitespace."""
    cleaned = text.strip()
    cleaned = re.sub(r"^(standalone question|question)\s*:\s*", "", cleaned, flags=re.I)
    cleaned = cleaned.strip().strip('"').strip("'").strip()
    return re.sub(r"\s+", " ", cleaned)
