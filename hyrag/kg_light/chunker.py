"""kg_light/chunker.py — the custom chunker LightRAG is told to use, and why it exists.

**The problem this solves.** LightRAG's own ingestion chunks a document itself, with its own
budget and its own boundaries. R2 must index **our** 369 chunks instead (D16: all four rows
read the same chunk set), and LightRAG's three documented ingest routes each drop one of the
three properties we need — our chunks, our `file_path`, extraction actually running
(`nodes/kg-light.md` → open question 1). The fourth route is this one: `ainsert`-style raw
enqueue with `ids` and `file_paths`, plus a **custom chunker** that returns the shared chunks
for the document being processed. Then:

    our chunks      ✓  the callback returns `parsed/_ingest.jsonl` records verbatim
    our file_path   ✓  the pipeline keeps the `file_paths` we passed
    extraction      ✓  the document pipeline runs its normal extract stage (Stage 3)
    page anchors    ✗  this path is not eligible for sidecar backfill — and does not need
                       to be: the page range is derived from our own record's `refs` in
                       `_ingest.jsonl`, which is where the citation chain lives (§5.6)

**The chunker runs on the event loop, once per document, inside the processing pipeline.**
It receives the document body we enqueued — the block text of that document — and returns
one dict per chunk: `chunk_id`, `content`, `tokens`, `chunk_order_index`. LightRAG keys each
chunk as `{doc_id}-{chunk_id}` because our ids do not already carry the doc prefix, so the
stored key is *ours + the document*, and the map is built by reading those keys back and
matching content — never by recomputing a hash and hoping (§5.6, step 2).

**The check inside the loop is the load-bearing line.** `content in body` asserts that the
chunk really is a slice of the document text we handed over; if our reconstruction of the
body and the chunker's own output ever disagree, this fails loudly at ingest instead of
producing a subtly wrong index. That is also why the body is assembled with the sidecar's
own separator (`packing.SEPARATOR`) rather than an arbitrary one.
"""

from __future__ import annotations

from typing import Any

from lightrag.chunker.registry import accepts_chunking_context

from hyrag.kg_light import config


@accepts_chunking_context
def our_chunker(
    tokenizer: Any,
    content: str,
    split_by_character: str | None,
    split_by_character_only: bool,
    chunk_overlap_token_size: int,
    chunk_token_size: int,
    *,
    context: Any,
) -> list[dict]:
    """Return the shared chunks for `context.doc_id`, in reading order.

    The six positional arguments are LightRAG's legacy chunker contract and are deliberately
    ignored: the budget, the policy and the boundaries were all decided when the shared chunk
    set was built, and a chunker that re-measured them here would be a second, quieter
    chunking policy. `tokenizer` is the pipeline's tiktoken tokenizer — the same one whose
    counts are stored in `_ingest.jsonl`, which is why the returned `tokens` are comparable.
    """
    doc = config.docs_by_stem().get(context.doc_id)
    if doc is None:
        raise ValueError(
            f"no shared chunks for doc_id {context.doc_id!r}: the ingest set carries "
            f"{sorted(config.docs_by_stem())}"
        )

    chunks: list[dict] = []
    for order, record in enumerate(doc.chunks):
        text = record["content"]
        if text not in content:
            raise ValueError(
                f"{context.doc_id} chunk {record['chunk_id']} is not a slice of the document "
                f"body ({len(text)} chars) — the body was not reconstructed from the same "
                f"blocks the chunk was built from"
            )
        chunks.append(
            {
                "chunk_id": record["chunk_id"],
                "content": text,
                "tokens": record["tokens"],
                "chunk_order_index": order,
            }
        )
    return chunks
