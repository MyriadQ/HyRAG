"""kg_light/builder.py — assembling the LightRAG instance the row runs on.

Everything here is wiring, and every wire is a decision from a node page or `PREPLAN.md`
that would otherwise be re-decided silently:

* **Storages** — Postgres for KV, vectors and document status; **NetworkX for the graph**
  (D9/D15). LightRAG also ships a Postgres graph backend; it is Apache AGE-based and must
  never be selected here (a build-from-source hazard on a Homebrew Postgres).
* **Embeddings** — through the OpenAI-compatible `/v1/embeddings` service
  (`hyrag.serving.embeddings`), never by importing the encoder. Two rows pointing at one
  service is the whole of §6.1's "hold the embedding model fixed".
* **LLM roles** (§6.1) — DeepSeek for all three roles, but the *EXTRACT* and *KEYWORD*
  roles disable thinking: extraction must emit clean structured output, and a reasoning
  trace is billed output that arrives before the JSON. The QUERY role keeps thinking on.
* **The chunker** — `hyrag.kg_light.chunker.our_chunker`, the route that keeps our chunks
  (see that module's docstring).

A `functools.partial` is the whole of the per-role binding: `openai_complete_if_cache` takes
model/base_url/api_key as ordinary arguments, and the partial pre-binds them while leaving
LightRAG's call signature (`prompt=`, `system_prompt=`, …) intact.

`tracker` is optional and exists for one purpose — open question 3, the extraction cost.
Passed into the OpenAI binding, it accumulates call count and token usage for the run, which
is the number that justifies D1 ("one extraction pass, not two").
"""

from __future__ import annotations

from functools import partial
from typing import Any

from lightrag import LightRAG
from lightrag.llm.openai import openai_complete_if_cache, openai_embed
from lightrag.llm_roles import RoleLLMConfig
from lightrag.utils import EmbeddingFunc

from hyrag.kg_light import config
from hyrag.kg_light.chunker import our_chunker


def _llm_func(*, thinking_off: bool, max_tokens: int, tracker: Any | None):
    """A DeepSeek completion function for one role, in LightRAG's calling convention.

    LightRAG calls every LLM function as `func(prompt, system_prompt=…, **kwargs)` — the
    first positional argument is the **prompt**, not the model — and the queue wrapper may
    also supply `max_tokens` and `response_format` in kwargs. So this cannot be a
    `functools.partial` of `openai_complete_if_cache` (whose first parameter is `model`):
    that raises `got multiple values for argument 'model'` on the very first extraction,
    which is how this function came to exist. The wrapper binds the model and endpoint and
    lets the caller's kwargs win where they overlap.

    The only behavioural difference between the roles is the request body: `extra_body`
    carries the `thinking` switch, the field DeepSeek actually reads (README → Landmines).
    """

    async def complete(prompt: str, system_prompt: str | None = None,
                       history_messages: list | None = None, **kwargs: Any) -> str:
        kwargs.setdefault("max_tokens", max_tokens)
        if tracker is not None:
            kwargs.setdefault("token_tracker", tracker)
        if thinking_off:
            kwargs.setdefault("extra_body", config.THINKING_OFF)
        return await openai_complete_if_cache(
            model=config.LLM_MODEL,
            prompt=prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            base_url=config.deepseek_base(),
            api_key=config.deepseek_key(),
            **kwargs,
        )

    return complete


def embedding_func(tracker: Any | None = None) -> EmbeddingFunc:
    """bge-m3 over HTTP, through the same endpoint every row will use.

    `openai_embed.func` is the *unwrapped* function: `openai_embed` is already decorated
    with `wrap_embedding_func_with_attrs`, and wrapping it twice is how dimensions and
    asymmetric-prefix settings end up applied in the wrong order (lightrag.utils
    → `EmbeddingFunc`). `max_token_size` is bge-m3's 8192 window: above it the client would
    truncate silently, below it a long description would be cut by our own hand.
    """
    return EmbeddingFunc(
        embedding_dim=config.EMBEDDING_DIM,
        max_token_size=config.EMBEDDING_MAX_TOKENS,
        model_name=config.EMBEDDING_MODEL,
        func=partial(
            openai_embed.func,
            model=config.EMBEDDING_MODEL,
            base_url=config.EMBEDDING_URL,
            api_key="hyrag-local",  # the endpoint has no auth; the client requires a value
            token_tracker=tracker,
        ),
    )


def build_rag(tracker: Any | None = None) -> LightRAG:
    """The row's one LightRAG instance. Caller owns its lifecycle (initialize/finalize)."""
    extract = _llm_func(thinking_off=True, max_tokens=config.EXTRACT_MAX_TOKENS, tracker=tracker)
    keyword = _llm_func(thinking_off=True, max_tokens=config.EXTRACT_MAX_TOKENS, tracker=tracker)
    query = _llm_func(thinking_off=False, max_tokens=config.QUERY_MAX_TOKENS, tracker=tracker)

    return LightRAG(
        working_dir=str(config.WORKING_DIR),
        workspace=config.WORKSPACE,
        # Storage backends by name — the classes are resolved from `lightrag.kg` (D15).
        kv_storage="PGKVStorage",
        vector_storage="PGVectorStorage",
        doc_status_storage="PGDocStatusStorage",
        graph_storage="NetworkXStorage",
        embedding_func=embedding_func(tracker),
        llm_model_func=query,
        role_llm_configs={
            "extract": RoleLLMConfig(func=extract, max_async=config.EXTRACT_MAX_ASYNC),
            "keyword": RoleLLMConfig(func=keyword, max_async=config.KEYWORD_MAX_ASYNC),
        },
        chunking_func=our_chunker,
        llm_model_max_async=config.LLM_MAX_ASYNC,
        embedding_func_max_async=config.EMBEDDING_MAX_ASYNC,
        embedding_batch_num=config.EMBEDDING_BATCH,
    )
