"""serving/llm.py — an OpenAI-compatible chat proxy in front of DeepSeek.

It exists for two reasons and does nothing else:

1. **`json_schema` → `json_object`.** GraphRAG's community-report step asks for structured
   output by passing a Pydantic model as `response_format`; LiteLLM turns that into
   `{"type": "json_schema", …}` and forwards it, and DeepSeek answers
   *"This response_format type is unavailable now"*. DeepSeek's own JSON mode
   (`{"type": "json_object"}`) is supported, is what the prompt already asks for, and parses
   the same way — so the proxy rewrites the type and forwards. This is a **repair of the
   transport**, not a change to the row's questions: the model, the prompt and the parsing are
   untouched, and the rewrite is counted in the log so a reader can see how often it happened.
2. **One place where every token is counted.** The response's `usage` block is logged per
   call, with the row's name taken from an `X-Hyrag-Row` header when the caller sets one. The
   report needs cost and latency per row; taking those from the API's own numbers is the only
   version that cannot drift from what was billed.

What it deliberately does **not** do: hold a key of its own (the caller's `Authorization`
header is forwarded), retry (the client does), or touch embeddings — those are local and stay
in `embeddings.py`.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from hyrag.parse.config import PARSED, REPO

log = logging.getLogger("hyrag.serving")

router = APIRouter()
LOG_PATH = Path(os.environ.get("HYRAG_LLM_LOG", PARSED / "llm_proxy.jsonl"))
DEFAULT_UPSTREAM = "https://api.deepseek.com"
LOGGED_INPUT_CHARS = 0  # never log prompt text: it is corpus-derived and licence-bound


def upstream_base() -> str:
    return os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_UPSTREAM).rstrip("/")


def _rewrite_response_format(payload: dict) -> bool:
    """DeepSeek has no `json_schema`; its `json_object` mode is the supported equivalent."""
    response_format = payload.get("response_format")
    if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
        payload["response_format"] = {"type": "json_object"}
        return True
    return False


def _record(payload: dict, *, status: int, stream: bool, rewritten: bool,
            usage: dict | None, latency_ms: int, row: str, error: str = "") -> None:
    usage = usage or {}
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "row": row,
        "model": payload.get("model"),
        "stream": stream,
        "status": status,
        "response_format_rewritten": rewritten,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "latency_ms": latency_ms,
        "error": error[:300],
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _target() -> str:
    return f"{upstream_base()}/chat/completions"


def _fallback_key() -> str:
    """`DEEPSEEK_API_KEY` from the environment, else the repo-root `.env`.

    The proxy prefers the caller's `Authorization` header — the row already holds the key, and
    a proxy that quietly substitutes its own would make an authentication failure look like a
    model failure. This fallback exists for direct probes of the endpoint (a curl with no
    header), which is exactly what the smoke test does.
    """
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    env_file = REPO / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def _headers(request: Request) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    authorization = request.headers.get("authorization")
    if authorization:
        headers["Authorization"] = authorization
    else:
        key = _fallback_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
    return headers


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    payload = await request.json()
    rewritten = _rewrite_response_format(payload)
    row = request.headers.get("x-hyrag-row", "unknown")
    streaming = bool(payload.get("stream"))
    started = time.perf_counter()

    if not streaming:
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(_target(), json=payload, headers=_headers(request))
        latency_ms = round((time.perf_counter() - started) * 1000)
        usage, error = None, ""
        if response.status_code < 400:
            try:
                usage = response.json().get("usage")
            except ValueError:
                error = "unparsable response"
        else:
            error = response.text
        _record(payload, status=response.status_code, stream=False, rewritten=rewritten,
                usage=usage, latency_ms=latency_ms, row=row, error=error)
        return Response(content=response.content, status_code=response.status_code,
                        media_type="application/json")

    client = httpx.AsyncClient(timeout=None)
    upstream = await client.send(
        client.build_request("POST", _target(), json=payload, headers=_headers(request)),
        stream=True,
    )

    async def relay():
        usage = None
        error = ""
        try:
            async for line in upstream.aiter_lines():
                if line.startswith("data: ") and line[6:].strip() not in ("", "[DONE]"):
                    try:
                        chunk = json.loads(line[6:])
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                    except ValueError:
                        pass
                yield (line + "\n").encode("utf-8")
        finally:
            await upstream.aclose()
            await client.aclose()
            _record(payload, status=upstream.status_code, stream=True, rewritten=rewritten,
                    usage=usage, latency_ms=round((time.perf_counter() - started) * 1000),
                    row=row, error=error)

    return StreamingResponse(relay(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type", "text/event-stream"))
