"""chunker/store.py — the two chunk artifacts, and their writers and readers.

    parsed/<stem>/chunks.jsonl   one document's chunks, with the block ids they came from
    parsed/_ingest.jsonl         the whole corpus, deduplicated: one record per distinct
                                 chunk text, carrying **every** place it occurred

Both are JSON Lines with a `meta` header line first — the same shape as the parser's
`blocks.jsonl` — and both are byte-identical on a re-run (sorted keys, no timestamps,
no run ids). `hyrag.chunker.checks` re-chunks and compares the bytes.

Why there are two files rather than one:

* the per-document file is the chunk set you can read beside its PDF, and the thing the
  id map of PREPLAN §5.6 will be built against (`chunk_id` -> LightRAG's key);
* the corpus file is the set that may reach ingest. LightRAG keys a chunk by a hash of
  its **content**, so an ingest set that still holds two identical texts collapses them
  into one key behind our back — one `file_path`, and a citation that then names the
  wrong document. So the dedupe happens here, and each surviving record carries its
  `occurrences`, every place that text came from. A citation enumerates them instead of
  picking one.

A record's fields:

    chunk_id          "stem:0007" per document, "sha256:…" in the ingest set (ours;
                      LightRAG's own key is read back at ingest, never recomputed)
    content, tokens   the text and its size under LightRAG's tokenizer
    heading / parent_headings / level      the section the chunk *starts* in
    refs              the blockids it was built from, in reading order
    occurrences       ingest set only: one entry per place the text occurs, with its
                      own refs, section and page range, plus the citation string
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hyrag.chunker.citation import format_citation, page_numbers
from hyrag.chunker.packing import Chunk, chunk_blocks, collapse_duplicate_chunks, count_tokens
from hyrag.parse.config import doc_dir
from hyrag.parse.sidecar import read_blocks, read_meta

# Compact, sorted, unescaped — the same convention `hyrag.parse.sidecar` uses, so a
# diff between two runs is a diff of content and nothing else.
_JSON = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}

INGEST_NAME = "_ingest.jsonl"


# ---------------------------------------------------------------- paths


def chunks_path(stem: str) -> Path:
    """`parsed/<stem>/chunks.jsonl` — this document's chunk set."""
    return doc_dir(stem) / "chunks.jsonl"


def ingest_path(stem: str) -> Path:
    """`parsed/_ingest.jsonl` — the corpus-wide set. `stem` only locates `parsed/`."""
    return doc_dir(stem).parent / INGEST_NAME


# ---------------------------------------------------------------- raw I/O


def write_jsonl(path: Path, header: dict, records: list[dict]) -> None:
    """One `meta` line, then one line per record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "meta", **header}, **_JSON) + "\n")
        for record in records:
            fh.write(json.dumps(record, **_JSON) + "\n")


def read_jsonl(path: Path) -> tuple[dict, list[dict]]:
    """Returns `(header, records)`. Raises if the first line is not the meta header."""
    with path.open("r", encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    if not lines or lines[0].get("type") != "meta":
        raise ValueError(f"{path}: first line is not a chunk meta header")
    return lines[0], lines[1:]


# ---------------------------------------------------------------- records


def chunk_records(stem: str, chunks: list[Chunk]) -> list[dict]:
    """The per-document records: the chunk plus an id that says which document it is from."""
    return [
        {"type": "chunk", "chunk_id": f"{stem}:{ordinal:04d}", **chunk}
        for ordinal, chunk in enumerate(chunks)
    ]


def document_header(stem: str, meta: dict, knobs: dict, blocks: list[dict]) -> dict:
    """What a reader needs to know before trusting the records below it."""
    return {
        "stem": stem,
        "source": meta["source"],
        "blocks_sha256": hashlib.sha256(
            json.dumps([b["blockid"] for b in blocks], **_JSON).encode()
        ).hexdigest(),
        "tokenizer": "o200k_base",  # tiktoken's name for the model LightRAG's chunker uses
        **knobs,
    }


def occurrence(stem: str, ordinal: int, chunk: Chunk, blocks_by_id: dict, meta: dict) -> dict:
    """Where one chunk came from — everything a citation needs, recorded per location."""
    pages = page_numbers(chunk["refs"], blocks_by_id)
    return {
        "chunk_id": f"{stem}:{ordinal:04d}",
        "stem": stem,
        "refs": list(chunk["refs"]),
        "heading": chunk["heading"],
        "parent_headings": list(chunk["parent_headings"]),
        "level": chunk["level"],
        "page_range": [min(pages), max(pages)],
        "citation": format_citation(chunk, blocks_by_id, meta),
    }


def build_ingest_set(
    per_document: list[tuple[str, list[dict], list[Chunk]]],
    blocks_by_id: dict[str, dict],
    metas: dict[str, dict],
    knobs: dict,
) -> tuple[dict, list[dict]]:
    """Deduplicate the corpus and record every location of every chunk that survived.

    `per_document` is `[(stem, chunks), ...]` in the order the documents were chunked;
    the first occurrence of a content becomes the record's primary location — the one
    whose `file`, `heading` and `refs` sit at the top level, because that is the single
    location an ingest layer can carry (`file_path` in LightRAG's case).

    Returns `(header, records)` — ready for `write_jsonl`.
    """
    everything = [(stem, ordinal, chunk) for stem, _blocks, chunks in per_document
                  for ordinal, chunk in enumerate(chunks)]
    unique_keys: list[str] = []
    locations: dict[str, list[dict]] = {}

    for stem, ordinal, chunk in everything:
        key = hashlib.sha256(chunk["content"].encode("utf-8")).hexdigest()
        locations.setdefault(key, []).append(
            occurrence(stem, ordinal, chunk, blocks_by_id, metas[stem])
        )
        if key not in unique_keys:
            unique_keys.append(key)

    records: list[dict] = []
    for ordinal, key in enumerate(unique_keys):
        first = locations[key][0]
        chunk = next(c for stem, o, c in everything if f"{stem}:{o:04d}" == first["chunk_id"])
        records.append(
            {
                "type": "chunk",
                "chunk_id": f"sha256:{key}",
                "content": chunk["content"],
                "tokens": chunk["tokens"],
                "file": Path(metas[first["stem"]]["source"]).name,
                "heading": first["heading"],
                "parent_headings": first["parent_headings"],
                "level": first["level"],
                "refs": first["refs"],
                "occurrences": locations[key],
            }
        )

    header = {
        "documents": len(per_document),
        "chunks": len(everything),
        "unique_chunks": len(records),
        "collapsed": len(everything) - len(records),
        "occurrences": sum(len(v) for v in locations.values()),
        "tokenizer": "o200k_base",
        **knobs,
    }
    return header, records


def write_document(stem: str, meta: dict, knobs: dict, blocks: list[dict], chunks: list[Chunk]) -> Path:
    """Write `parsed/<stem>/chunks.jsonl` and return its path."""
    path = chunks_path(stem)
    records = chunk_records(stem, [dict(chunk) for chunk in chunks])  # type: ignore[arg-type]
    write_jsonl(path, document_header(stem, meta, knobs, blocks), records)
    return path


def chunk_corpus(stems: list[str], knobs: dict) -> tuple[list[tuple[str, list[dict], list[Chunk]]], dict[str, dict], dict[str, dict]]:
    """Chunk every named document. Pure computation — nothing is written here.

    Returns `([(stem, blocks, chunks), ...], {stem: meta}, {blockid: block})`. The
    corpus-wide block lookup works because a `blockid` is an md5 over
    `doc_id : index : heading : content`, so ids from different documents cannot collide.
    """
    per_document: list[tuple[str, list[dict], list[Chunk]]] = []
    metas: dict[str, dict] = {}
    lookup: dict[str, dict] = {}

    for stem in stems:
        target = doc_dir(stem)
        _sidecar_meta, blocks = read_blocks(target / "blocks.jsonl")
        meta = read_meta(target / "meta.json")
        chunks = chunk_blocks(blocks, knobs["max_tokens"], knobs["overlap"], knobs["section_policy"])
        per_document.append((stem, blocks, chunks))
        metas[stem] = meta
        lookup.update({block["blockid"]: block for block in blocks})

    return per_document, metas, lookup


# ---------------------------------------------------------------- loading


def load_document(stem: str) -> tuple[dict, list[dict], dict, list[dict]]:
    """`(sidecar_meta, blocks, meta, chunks)` for one parsed+chunked document."""
    target = doc_dir(stem)
    sidecar_meta, blocks = read_blocks(target / "blocks.jsonl")
    meta = read_meta(target / "meta.json")
    _header, chunks = read_jsonl(target / "chunks.jsonl")
    return sidecar_meta, blocks, meta, chunks


def blocks_by_id(blocks: list[dict]) -> dict[str, dict]:
    """`{blockid: block}` — the lookup every citation and every ref check goes through."""
    return {block["blockid"]: block for block in blocks}


def total_tokens(chunks: list[dict]) -> int:
    """Sum of the chunk sizes — for the one-line summary `chunk` prints per document."""
    return sum(count_tokens(chunk["content"]) for chunk in chunks)
