"""judge/model.py — the judge itself: DeepEval's metrics, our proxy, DeepSeek's thinking off.

DeepEval implements D8a's `GEval` (pointwise) and `ArenaGEval` (blind pairwise); what it does
not have is a way to reach **our** endpoint. `DeepEvalBaseLLM` is the hook: implement
`generate` / `a_generate` / `load_model` / `get_model_name`, and every metric in the library
runs against it. Three consequences worth stating:

* **Every judge token is counted and attributed.** Calls go through `hyrag/serving/llm.py`
  with the header `X-Hyrag-Row: judge`, so the judge's spend is separable from the rows' in
  `parsed/llm_proxy.jsonl` and appears in the report beside them. This process also writes
  `results/judge_calls.jsonl` — one line per call, with the label of what it was scoring —
  which is what makes a per-item cost attribution possible at all.
* **Thinking disabled.** DeepSeek thinks by default (`reasoning_effort: high`) and a reasoning
  trace arrives *before* the JSON that the metric parses; the judge is a scoring call, not a
  reasoning contest, and the switch is the one item in README → Landmines that names the judge
  specifically.
* **Nothing leaves the machine except to our own endpoint** (D8c). The base URL is the local
  proxy, and the proxy holds the upstream key. `DEEPEVAL_TELEMETRY_OPT_OUT=1` is set in
  `hyrag/judge/__init__.py` before DeepEval is ever imported, and `deepeval login` is never run.

`temperature = 0` is deliberate: the judge should not invent variance that the rows did not
cause. Three replicates still run per cell (§7.2), and the spread it measures is reported —
if it comes out at zero, that is a fact about the judge, not a reason to have fewer.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import weakref

from deepeval.models import DeepEvalBaseLLM

from hyrag import llm_log
from hyrag.judge import config
from hyrag.kg_light import config as kg_light_config

_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}
_lock = threading.Lock()
_clients: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _get_client():
    """One async client **per event loop**.

    The proxy is local and holds the upstream key; the subtlety is that `runner.py` runs each
    pass under its own `asyncio.run`, and an `AsyncOpenAI` built in a closed loop keeps a
    connection pool that loop owned — the pairwise pass then dies with *"Event loop is
    closed"* while the pointwise pass, which built the client, is fine. Keying the cache by the
    running loop makes each pass open its own client and lets the old one be collected.
    """
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=f"{kg_light_config.LLM_BASE}",
                             api_key=kg_light_config.deepseek_key())
        _clients[loop] = client
    return client


def _record(label: str, usage: dict | None, latency_ms: int, attempts: int, error: str = "") -> None:
    usage = usage or {}
    entry = {
        "ts": llm_log.timestamp(),
        "label": label,
        "model": kg_light_config.LLM_MODEL,
        "attempts": attempts,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
        "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens", 0),
        "latency_ms": latency_ms,
        "error": error[:200],
    }
    path = config.CALLS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, **_JSON) + "\n")


class ProxyJudge(DeepEvalBaseLLM):
    """The judge model: DeepSeek through the local chat proxy, JSON mode, thinking off."""

    def __init__(self, label: str = "judge", *, max_tokens: int | None = None):
        self.label = label
        self.max_tokens = max_tokens or config.MAX_TOKENS
        super().__init__(model=kg_light_config.LLM_MODEL)

    def load_model(self):  # DeepEvalBaseLLM calls this from __init__
        return self

    def get_model_name(self) -> str:
        return kg_light_config.LLM_MODEL

    async def a_generate(self, prompt: str, schema=None, **kwargs) -> str:
        return await self._call(prompt, schema=schema is not None)

    def generate(self, prompt: str, schema=None, **kwargs) -> str:
        return asyncio.run(self._call(prompt, schema=schema is not None))

    async def _call(self, prompt: str, *, schema: bool) -> str:
        """One completion. With `schema=True`, retry once if the JSON does not parse.

        DeepEval parses the returned string itself (`trimAndLoadJson`), so a malformed body
        would surface as a metric failure with no retry of its own. One nudge here is
        cheaper than losing a data point, and the attempt count is recorded either way.
        """
        payload = {
            "model": kg_light_config.LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": config.TEMPERATURE,
            "max_tokens": self.max_tokens,
            "extra_body": {"thinking": {"type": "disabled"}},
            "extra_headers": {"X-Hyrag-Row": "judge"},
        }
        if schema:
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        last_error = ""
        for attempt in (1, 2):
            try:
                response = await _get_client().chat.completions.create(**payload)
                content = (response.choices[0].message.content or "").strip()
                usage = response.usage.model_dump() if response.usage else {}
                if schema:
                    body = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                    try:
                        json.loads(body)
                    except json.JSONDecodeError as error:
                        last_error = f"json: {error}"
                        if attempt == 1:
                            payload["messages"] = [
                                {"role": "user", "content": prompt},
                                {"role": "assistant", "content": content or "{}"},
                                {"role": "user", "content": "Return that same result as valid JSON only."},
                            ]
                            continue
                _record(self.label, usage, round((time.perf_counter() - started) * 1000), attempt)
                return content
            except Exception as error:  # a transient 500 must not lose the item
                last_error = f"{type(error).__name__}: {error}"
                if attempt == 2:
                    _record(self.label, None, round((time.perf_counter() - started) * 1000), attempt,
                            error=last_error)
                    raise
                await asyncio.sleep(2)
        raise RuntimeError(last_error)
