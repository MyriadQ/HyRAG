"""router/model.py — one completion, through the proxy, thinking off, tagged.

Three things are deliberate and each has already cost someone time in this repo:

* **The call goes to `hyrag/serving/llm.py`, not to DeepSeek.** That is where `json_schema` is
  repaired, where every call's tokens are recorded, and where the `X-Hyrag-Row: router` header
  turns into an attributable line in `parsed/llm_proxy.jsonl`. Calling the provider directly
  would make the router the one box whose cost is a guess.
* **`thinking` is disabled.** DeepSeek reasons by default; a reasoning trace arrives before the
  JSON the classifier parses, and a routing decision does not need a contest of reasoning
  budgets. The flag rides in `extra_body` because the SDK does not model it.
* **`temperature = 0`.** The router's job is to be reproducible on a question; variance here
  would show up later as an arm's answer changing between two runs of the same turn.

`complete` is synchronous on purpose: the gate classifies 30 questions and the dialogue test 3
turns, sequentially, so a thread pool would buy nothing and cost determinism of the token
window. If `arms` later wants concurrency it can build its own path.
"""

from __future__ import annotations

import time

from openai import OpenAI

from hyrag.kg_light import config as kg_light_config
from hyrag.router import config


def complete(
    system: str,
    user: str,
    *,
    json_mode: bool = False,
    max_tokens: int = 60,
    temperature: float = 0.0,
) -> dict:
    """One chat completion. Returns `{"text", "usage", "latency_ms"}`; raises on transport errors.

    A transport error is *not* retried here: `bench` counts failures and the gate refuses to
    report an accuracy over a run that silently dropped a question. A malformed **JSON body**
    is a different failure and belongs to the caller, which knows what a valid label is.
    """
    client = OpenAI(base_url=config.LLM_BASE, api_key=kg_light_config.deepseek_key())
    started = time.perf_counter()
    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"} if json_mode else None,
        extra_body={"thinking": {"type": "disabled"}},
        extra_headers={"X-Hyrag-Row": config.ROW_TAG},
    )
    latency_ms = round((time.perf_counter() - started) * 1000)
    usage = response.usage
    return {
        "text": (response.choices[0].message.content or "").strip(),
        "usage": {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        },
        "latency_ms": latency_ms,
    }
