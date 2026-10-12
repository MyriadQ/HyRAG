"""chunker/packing.py — turning blocks into the chunks every retrieval row reads.

A **block** is what the parser produced (`hyrag.parse`): a paragraph, a table, a
heading or a picture, each carrying the sheet it sits on. A **chunk** is what the
retrieval systems index: a run of blocks that fits a token budget, carrying the
`blockid`s it was built from, so a citation can be derived later instead of stored
and drifting.

Three knobs, each explained at its own function:

* `max_tokens`     — how much text one chunk may hold (chosen by measurement, §7 of
                    the chunker lab);
* `section_policy` — when a heading change is allowed to close a chunk (§5);
* `overlap`        — how many trailing blocks are repeated into the next chunk (§6).

What the callers are promised:

* `count_tokens` measures the same quantity LightRAG measures, so our budget and its
  budget are the same number;
* a chunk never stores a page range or a section — both are *derived* from its refs,
  so they cannot disagree with the blocks;
* one block larger than `max_tokens` is emitted on its own rather than split: a chunk
  that the embedder would truncate would hold text in the file and not in the vector;
* at `overlap == 0` the chunks, concatenated, are exactly the blocks concatenated —
  no text lost, duplicated or reordered.

Two notes on naming, for whoever reads this next:

* the chunk being filled is called *pending*: it is not a chunk until it is closed,
  and two names for two states beats one name for both;
* `refs` keeps the LightRAG sidecar's field name (`docs/LightRAGSidecarFormat.md`),
  because these ids are also what that format exchanges with the ingest side.
"""

from __future__ import annotations

import hashlib
from typing import Literal, TypedDict

import tiktoken

from hyrag.chunker.citation import page_numbers

# How blocks are joined into a chunk's text. Not a choice: the sidecar header's
# `document_hash` is the sha256 of the block contents joined by exactly this
# separator, so our chunk text is the concatenation that format already defines.
SEPARATOR = "\n\n"

# The tokenizer LightRAG's default chunker uses for `chunk["tokens"]`. Same call, same
# string, so our number and its number are the same quantity.
_ENCODING = tiktoken.encoding_for_model("gpt-4o-mini")

# Which heading changes close a chunk. `significant` needs a depth to draw its line at:
# formal sections sit at the top of the hierarchy, sub-sections merge upward.
SectionPolicy = Literal["hard", "significant", "free"]
SIGNIFICANT_MAX_LEVEL = 2


class Chunk(TypedDict):
    """One chunk record.

    `heading`, `parent_headings` and `level` describe where the chunk *starts* — they
    are copied from its first block, which is the block that decides what the chunk's
    citation will claim. `refs` is the authority for everything else.
    """

    content: str
    tokens: int
    heading: str
    parent_headings: list[str]
    level: int
    refs: list[str]


# ---------------------------------------------------------------- measuring


def count_tokens(text: str) -> int:
    """Tokens in `text`, under LightRAG's tokenizer — the unit the budget is spent in."""
    return len(_ENCODING.encode(text))


def _join(blocks: list[dict]) -> str:
    """The exact text a chunk will hold for these blocks."""
    return SEPARATOR.join(block["content"] for block in blocks)


def _fits(pending: list[dict], block: dict, max_tokens: int) -> bool:
    """Would `block` still fit into the pending chunk?

    Measured on the joined string, never as the sum of the blocks' own token counts:
    that estimate is optimistic by ~1% (the separators and the run-together words),
    and an optimistic estimate means a chunk the embedder silently truncates.
    """
    return count_tokens(_join(pending + [block])) <= max_tokens


# ---------------------------------------------------------------- building one chunk


def build_chunk(blocks: list[dict]) -> Chunk:
    """Turn one run of blocks into a chunk record.

    `blocks` must be in reading order and belong together — the caller (the packer)
    decides where a run ends; this function only records what it was given.
    """
    content = _join(blocks)
    chunk: Chunk = {
        "content": content,
        "tokens": count_tokens(content),  # of the joined string, not the sum of parts
        "heading": blocks[0]["heading"],  # the chunk's context is where it starts
        "parent_headings": blocks[0]["parent_headings"],
        "level": blocks[0]["level"],
        "refs": [block["blockid"] for block in blocks],  # in reading order — the citation chain
    }
    # A TypedDict is not enforced at runtime, so the declaration checks itself here.
    assert set(chunk) == set(Chunk.__annotations__), set(chunk) ^ set(Chunk.__annotations__)
    return chunk


# ---------------------------------------------------------------- section boundaries


def _starts_new_section(policy: SectionPolicy, pending: list[dict], block: dict) -> bool:
    """Does `block` open a section that must not share a chunk with `pending`?

    The three policies differ only in where they allow the line to be drawn:

    * `hard`        — every heading change closes the chunk: one chunk, one section.
    * `significant` — only headings at or above `max(SIGNIFICANT_MAX_LEVEL, the chunk's
                      own level)` close it, so sub-sections (level 3, 4, ...) merge into the
                      section they sit in, while a chunk that had to open deep — a budget
                      break in the middle of a level-3 section — still closes when the
                      next sibling of that section starts. Its `heading` is then true for
                      every block in it, never merely the first one.
    * `free`        — headings never close a chunk; `max_tokens` is the only reason.

    A block with no heading (`heading == ""`, level 0 — a cover picture, a stray table)
    cannot open a section, so it never closes one. It can still be the first block of a
    chunk, and then the chunk's own `heading` is empty, which is honest about it.
    """
    if not block["heading"]:
        return False  # no heading -> a continuation, whatever the policy
    if block["heading"] == pending[0]["heading"]:
        return False  # same section as the chunk's start

    if policy == "hard":
        return True
    if policy == "significant":
        # Draw the line at the chunk's own level, but never below SIGNIFICANT_MAX_LEVEL:
        # sub-sections merge upward, and a chunk that opened deep still stops at its own
        # siblings. Closing only at an absolute level <= 2 would let two level-3 sections
        # share a chunk whenever a budget break started one of them, and the chunk's
        # heading would then be true of its first blocks only.
        boundary_level = max(SIGNIFICANT_MAX_LEVEL, pending[0]["level"])
        return block["level"] <= boundary_level
    return False


def _carry(pending: list[dict], overlap: int, following: dict) -> list[dict]:
    """The trailing blocks to repeat into the next chunk, oldest first.

    Walks backwards until the overlap budget is spent, so the repeat is at most
    `overlap` tokens — or nothing, if even the last block is bigger than the budget.

    The walk also stops at a section change: a block is only worth repeating if the
    text that follows it belongs to the same section. Repeating a sub-section that had
    already ended would give the new chunk a first block from that sub-section, and
    the chunk's `heading` — taken from its first block — would then be false for the
    rest of it. That is the one thing this project cannot afford: a citation that is
    confidently wrong.
    """
    if overlap <= 0:
        return []
    carried: list[dict] = []
    used = 0
    for block in reversed(pending):
        if block["heading"] != following["heading"]:
            break  # the repeat would span a section change
        size = count_tokens(block["content"])
        if used + size > overlap:
            break
        carried.insert(0, block)
        used += size
    return carried


# ---------------------------------------------------------------- the packer


def chunk_blocks(
    blocks: list[dict],
    max_tokens: int,
    overlap: int = 0,
    section_policy: SectionPolicy = "free",
) -> list[Chunk]:
    """Walk the blocks once and emit chunks.

    The pending chunk is closed when either happens first:

    * the next block no longer fits `max_tokens`;
    * `section_policy` says the next block opens a new section.

    A block larger than `max_tokens` on its own is closed out and emitted alone: it is
    never split, and it is never carried into the next chunk as overlap, because it
    could not fit there either.

    `overlap` repeats trailing blocks **within a section only**. Repeating them across
    a section boundary would give the next chunk a first block from the previous
    section, and its `heading` — taken from that block — would then be a lie about the
    text that follows it. Retrieval quality is not worth a false citation; the honest
    fix would be to store the range, and the whole design is built to avoid that.
    """
    if not blocks:
        return []

    chunks: list[Chunk] = []
    pending: list[dict] = []  # the run being filled; becomes a chunk when it is closed

    for block in blocks:
        size = count_tokens(block["content"])

        if size > max_tokens:
            # A single block over the cap: emit it alone, so nothing else inherits its size.
            if pending:
                chunks.append(build_chunk(pending))
                pending = []
            chunks.append(build_chunk([block]))
            continue

        if pending:
            budget_break = not _fits(pending, block, max_tokens)
            section_break = _starts_new_section(section_policy, pending, block)

            if budget_break or section_break:
                chunks.append(build_chunk(pending))
                # Repeat the tail only where the break was about size, and only if the
                # repeat still leaves room for the block that caused the break.
                carried = _carry(pending, overlap, block) if budget_break and not section_break else []
                if carried and not _fits(carried, block, max_tokens):
                    carried = []
                pending = carried

        pending.append(block)

    if pending:  # the tail flush — without it the last blocks are silently dropped
        chunks.append(build_chunk(pending))

    return chunks


# ---------------------------------------------------------------- duplicates


def collapse_duplicate_chunks(
    chunks: list[Chunk], blocks_by_id: dict[str, dict]
) -> tuple[list[Chunk], dict[str, list[dict]]]:
    """Drop byte-identical chunks, recording every place each one came from.

    Why this exists (PREPLAN §5.6): LightRAG keys a chunk by a hash of its *content*.
    An MMEL repeats boilerplate across pages, so two byte-identical chunks with
    different provenance are routine — and at ingest they collapse into one key with
    one file path, after which a citation names one document while being wrong for the
    other. Duplicate text is a property of the corpus, so it is fixed in the corpus,
    not in the prompt: identical text is indexed once, and every origin survives here.

    Returns `(unique_chunks, duplicates)`:

    * `unique_chunks` — the first chunk of each distinct content, in reading order;
    * `duplicates`    — `{content_sha256: [occurrence, ...]}`, one entry per chunk that
      was collapsed. An occurrence says where that text also appears: its own `refs`,
      section, level and physical page span. The sha256 is ours and is **not**
      LightRAG's key — that join is built at ingest by reading LightRAG's own store
      back, never by recomputing a hash and hoping (§5.6, step 2).

    Run it over the whole ingested set at once, not per document: LightRAG's key space
    is content, so a duplicate in another document is exactly the collision it prevents.
    """
    unique: list[Chunk] = []
    duplicates: dict[str, list[dict]] = {}
    seen: set[str] = set()  # the content hashes already kept — one set is enough, no re-hashing

    for chunk in chunks:
        key = hashlib.sha256(chunk["content"].encode("utf-8")).hexdigest()
        if key not in seen:
            seen.add(key)
            unique.append(chunk)
            continue

        pages = page_numbers(chunk["refs"], blocks_by_id)
        duplicates.setdefault(key, []).append(
            {
                "refs": list(chunk["refs"]),
                "heading": chunk["heading"],
                "parent_headings": list(chunk["parent_headings"]),
                "level": chunk["level"],
                "page_range": [min(pages), max(pages)],
            }
        )

    return unique, duplicates
