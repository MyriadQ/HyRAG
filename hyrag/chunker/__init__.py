"""chunker — blocks in, chunks with a citation out.

This is roadmap box `parse`'s last piece: the shared chunk set that all four
benchmark rows read (PREPLAN D16 — one extraction pass, one chunk set, so a
difference between rows comes from extraction and retrieval, never from where the
text was cut).

    parsed/<stem>/blocks.jsonl
            │
            ├─ packing.py    chunk_blocks(...)  -> chunks carrying their block ids
            │                 collapse_duplicate_chunks(...) -> no content key can collide
            └─ citation.py   format_citation(...) -> "file.pdf, §section, p. N–M"

What this package guarantees to whatever indexes the chunks:

* every chunk records the block ids it was built from — the page range and the
  section are derived from them, never stored beside them, so they cannot drift;
* `count_tokens` is LightRAG's tokenizer, so a `max_tokens` budget means the same
  thing on both sides of the ingest;
* a chunk that is indexed once cannot be a duplicate of another indexed chunk: the
  dedupe pass either drops it or records where it also occurred;
* a citation is `<file>, §<section>, p. <range>` and never a bare page number.

The experiments that choose `max_tokens`, the section policy and the overlap live in
`scratch/chunker.ipynb` (the lab); this package holds only what is reused, shipped or
cited (D11 — a re-runnable function, not a hand-driven notebook).
"""

from hyrag.chunker.citation import format_citation, format_page_range, page_numbers
from hyrag.chunker.packing import (
    Chunk,
    SectionPolicy,
    build_chunk,
    chunk_blocks,
    collapse_duplicate_chunks,
    count_tokens,
)

__all__ = [
    "Chunk",
    "SectionPolicy",
    "build_chunk",
    "chunk_blocks",
    "collapse_duplicate_chunks",
    "count_tokens",
    "format_citation",
    "format_page_range",
    "page_numbers",
]
