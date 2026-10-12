"""chunker/checks.py — the invariants the chunk set must hold, run as one command.

    uv run python -m hyrag.chunker.cli check --all

Same contract as `hyrag.parse.checks`: each check states the property it verifies, and a
failure names the document and the offending record, so the fix is a lookup rather than
an investigation.

Per document, in the order a reviewer would ask:

* `artifacts_present`   blocks.jsonl, meta.json and chunks.jsonl all exist;
* `refs_resolve`        every `refs` entry names a block that exists and has a page
                        — the whole citation chain is only as good as this;
* `chunk_intact`        a chunk's `content` is exactly its blocks' text joined, and its
                        `tokens` is that string's size (a chunk that drifted from its
                        refs would cite text it does not contain);
* `budget`              no chunk over `max_tokens`, except a single block that is bigger
                        on its own (those are emitted alone, never split);
* `ids_unique`          `chunk_id` is unique inside the document;
* `coverage`            every block is in at least one chunk, and each chunk's refs are
                        in reading order. At `overlap == 0` this is strengthened to the
                        exact reconstruction: chunks concatenated == blocks concatenated;
* `section_honesty`     every chunk's `§` is true for every block in it. This is a hard
                        requirement, not a metric: the `free` policy is a lab variant and
                        fails it on purpose (41% of chunks on the slice);
* `determinism`         re-chunking from blocks.jsonl reproduces the file byte for byte.

Across the corpus (`_ingest.jsonl`):

* `ingest_unique`       no two records share content — the property that stops LightRAG
                        collapsing two documents into one chunk key;
* `occurrences`         every record carries at least one location, and every location's
                        refs resolve, with a page range and a citation.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hyrag.chunker.citation import ProvenanceError, page_numbers
from hyrag.chunker.packing import chunk_blocks, count_tokens
from hyrag.chunker.store import (
    build_ingest_set,
    chunk_corpus,
    load_document,
    chunk_records,
    chunks_path,
    document_header,
    ingest_path,
    read_jsonl,
    write_jsonl,
)
from hyrag.parse.config import doc_dir, slice_docs
from hyrag.parse.sidecar import read_blocks, read_meta


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None  # None = informational
    detail: str = ""

    def line(self) -> str:
        mark = "PASS" if self.ok else ("INFO" if self.ok is None else "FAIL")
        return f"  [{mark}] {self.name}" + (f" — {self.detail}" if self.detail else "")


def _knobs(header: dict) -> dict:
    """The three packing parameters, as recorded by the run that wrote the file."""
    return {key: header[key] for key in ("max_tokens", "overlap", "section_policy")}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- one document


def check_document(stem: str, *, reparse: bool = True) -> list[Check]:
    """Run every chunk invariant for one document."""
    target = doc_dir(stem)
    needed = [target / "blocks.jsonl", target / "meta.json", chunks_path(stem)]
    missing = [path.name for path in needed if not path.is_file()]
    checks: list[Check] = [
        Check("artifacts_present", not missing, "missing: " + ", ".join(missing) if missing else f"{stem}/")
    ]
    if missing:
        return checks

    meta = read_meta(target / "meta.json")
    _sidecar_meta, blocks = read_blocks(target / "blocks.jsonl")
    header, chunks = read_jsonl(chunks_path(stem))
    knobs = _knobs(header)
    by_id = {block["blockid"]: block for block in blocks}

    # -- refs -> pages: the chain the whole project rests on
    broken: list[str] = []
    for chunk in chunks:
        try:
            page_numbers(chunk["refs"], by_id)
        except ProvenanceError as exc:
            broken.append(f"{chunk['chunk_id']}: {exc}")
    checks.append(
        Check("refs_resolve", not broken, f"{len(broken)} chunks with a broken chain"
              + (f", e.g. {broken[0][:90]}" if broken else ""))
    )

    # -- a chunk's text is its blocks' text, and its token count is that string's size
    drifted: list[str] = []
    for chunk in chunks:
        from_refs = "\n\n".join(by_id[r]["content"] for r in chunk["refs"] if r in by_id)
        if from_refs != chunk["content"]:
            drifted.append(f"{chunk['chunk_id']} text")
        elif count_tokens(chunk["content"]) != chunk["tokens"]:
            drifted.append(f"{chunk['chunk_id']} tokens")
    checks.append(Check("chunk_intact", not drifted, f"{len(drifted)} chunks disagree with their refs"))

    # -- the budget is a cap; a single oversized block alone is the documented exception
    over = [
        chunk["chunk_id"]
        for chunk in chunks
        if chunk["tokens"] > knobs["max_tokens"] and len(chunk["refs"]) > 1
    ]
    oversized = sum(1 for chunk in chunks if chunk["tokens"] > knobs["max_tokens"])
    checks.append(
        Check("budget", not over, f"{oversized} over max_tokens ({knobs['max_tokens']}), "
                                  f"{len(over)} of them with neighbours")
    )

    # -- ids
    ids = [chunk["chunk_id"] for chunk in chunks]
    checks.append(Check("ids_unique", len(set(ids)) == len(ids), f"{len(ids)} chunk ids"))

    # -- coverage: the strong form when nothing is repeated, the weak form under overlap
    covered = {ref for chunk in chunks for ref in chunk["refs"]}
    order = [block["blockid"] for block in blocks]
    out_of_order = [
        chunk["chunk_id"]
        for chunk in chunks
        if [order.index(r) for r in chunk["refs"] if r in order]
        != sorted(order.index(r) for r in chunk["refs"] if r in order)
    ]
    if knobs["overlap"] == 0:
        same_text = "\n\n".join(c["content"] for c in chunks) == "\n\n".join(b["content"] for b in blocks)
        checks.append(
            Check("coverage", same_text and not out_of_order,
                  f"reconstruction={'exact' if same_text else 'DIFFERENT'}, "
                  f"{len(out_of_order)} chunks out of reading order")
        )
    else:
        checks.append(
            Check("coverage", covered == set(order) and not out_of_order,
                  f"{len(covered)}/{len(order)} blocks covered (overlap={knobs['overlap']} "
                  f"repeats text, so exact reconstruction is not expected), "
                  f"{len(out_of_order)} chunks out of reading order")
        )

    # -- the § must be true for the whole chunk, not only for its first block
    dishonest = []
    for chunk in chunks:
        heading = chunk["heading"]
        if not all(
            by_id[r]["heading"] == heading or heading in by_id[r]["parent_headings"]
            for r in chunk["refs"]
            if r in by_id
        ):
            dishonest.append(chunk["chunk_id"])
    checks.append(
        Check("section_honesty", not dishonest,
              f"{len(dishonest)}/{len(chunks)} chunks whose § is not true for all their text"
              + (" (policy 'free' is a lab variant — it is expected to fail here)"
                 if dishonest and knobs["section_policy"] == "free" else ""))
    )

    # -- determinism: same blocks in, same bytes out
    if reparse:
        with tempfile.TemporaryDirectory() as tmp:
            again = Path(tmp) / "chunks.jsonl"
            again_chunks = chunk_blocks(blocks, knobs["max_tokens"], knobs["overlap"],
                                        knobs["section_policy"])
            write_jsonl(again, document_header(stem, meta, knobs, blocks),
                        chunk_records(stem, [dict(c) for c in again_chunks]))
            same = _digest(again) == _digest(chunks_path(stem))
            checks.append(Check("determinism", same,
                                "re-chunked file is byte-identical" if same else "BYTES DIFFER"))
    return checks


def check_document_with_pdf(stem: str, *, reparse: bool = True, sample: int = 3) -> list[Check]:
    """`check_document`, plus the independent check against the PDF itself."""
    checks = check_document(stem, reparse=reparse)
    if checks[0].name != "artifacts_present" or checks[0].ok is False:
        return checks
    _sidecar_meta, blocks, meta, chunks = load_document(stem)
    by_id = {block["blockid"]: block for block in blocks}
    checks.append(spot_check_pdf(stem, chunks, by_id, meta, sample=sample))
    return checks


# ---------------------------------------------------------------- against the PDF


_PDF_WORD = re.compile(r"[a-z0-9]{5,}")
# Docling's markers (`<drawing />`, `<drawing caption="…" />`) are markup, not words on the
# page: `drawing` and `caption` would never be found in the PDF's own text, and counting them
# made a chart page look like a bad citation.
_MARKER = re.compile(r"<[^>]*>")
SPOT_CHECK_THRESHOLD = 0.7
# A chunk made of markers (`<drawing />`, a bare table header) has almost no prose, so the
# PDF cannot confirm it — there is nothing to find. Those chunks are skipped, not failed.
MIN_VERIFIABLE_WORDS = 5


def _words(text: str) -> set[str]:
    """The words worth comparing: 5+ characters, markup removed, lowercased."""
    return set(_PDF_WORD.findall(_MARKER.sub(" ", text).lower()))


def _pdf_words(pdf_path: Path, pages: list[int]) -> set[str]:
    """Every word of 5+ characters on those sheets, straight out of the PDF."""
    import pypdfium2 as pdfium  # a different library from Docling's, and not ours at all

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        words: set[str] = set()
        for page in pages:
            words |= _words(document[page - 1].get_textpage().get_text_range())
        return words
    finally:
        document.close()


def pdf_page_coverage(pdf_path: Path, pages: list[int], text: str) -> tuple[float, int]:
    """How much of `text` really sits on those sheets, judged by the PDF.

    The citation chain — chunk -> refs -> blocks -> page — is ours end to end, so checking
    it against our own artifacts proves only that we are self-consistent. This reads the
    original PDF with a second, unrelated library (no Docling, no sidecar) and compares
    word sets: each word of the chunk text that is at least five characters long, against
    the words on the physical sheets the citation names.

    Measured on the slice: a chunk's own pages score **0.96–1.00**, the same chunk checked
    against pages shifted by two scores **0.27–0.39**, and a chunk from another document
    scores **below 0.10** — so the threshold below separates right pages from wrong ones by
    a wide margin. Short words and numbers are ignored on purpose: they are everywhere.
    """
    wanted = _words(text)
    if len(wanted) < MIN_VERIFIABLE_WORDS:
        return 0.0, len(wanted)
    found = wanted & _pdf_words(pdf_path, pages)
    return len(found) / len(wanted), len(wanted)


def spot_check_pdf(stem: str, chunks: list[dict], blocks_by_id: dict, meta: dict,
                   sample: int = 3) -> Check:
    """Verify `sample` chunks against the PDF, spaced through the document.

    Evenly spaced indices, not the first few: the front matter, the body and the tail fail
    differently, and a sample from one of them would hide the others' trouble.
    """
    if sample <= 0:
        return Check("pdf_spot_check", None, "skipped")
    pdf = Path(meta["source"])
    if not pdf.is_file():
        return Check("pdf_spot_check", None, f"source PDF not on disk: {pdf}")

    verifiable = [
        index for index, chunk in enumerate(chunks)
        if len(_words(chunk["content"])) >= MIN_VERIFIABLE_WORDS
    ]
    if not verifiable:
        return Check("pdf_spot_check", None, "no chunk in this document has verifiable prose")
    if len(verifiable) <= sample:
        indices = verifiable
    else:
        step = (len(verifiable) - 1) / (sample - 1)
        indices = sorted({verifiable[round(i * step)] for i in range(sample)})

    scores = []
    for index in indices:
        chunk = chunks[index]
        pages = sorted(set(page_numbers(chunk["refs"], blocks_by_id)))
        coverage, _n_words = pdf_page_coverage(pdf, pages, chunk["content"])
        scores.append(coverage)

    worst = min(scores)
    return Check(
        "pdf_spot_check",
        worst >= SPOT_CHECK_THRESHOLD,
        f"{len(indices)} chunks checked against the PDF: coverage " +
        ", ".join(f"{score:.2f}" for score in scores) +
        f" (threshold {SPOT_CHECK_THRESHOLD}, own pages 0.96–1.00, wrong pages below 0.40)",
    )


# ---------------------------------------------------------------- the corpus


def check_ingest(*, reparse: bool = True) -> list[Check]:
    """Run the corpus-wide checks over `_ingest.jsonl`."""
    stems = [spec.stem for spec in slice_docs()]
    path = ingest_path(stems[0])
    if not path.is_file():
        return [Check("ingest_present", False, f"missing {path} — run `chunk --all` first")]

    header, records = read_jsonl(path)
    knobs = _knobs(header)

    contents = [record["content"] for record in records]
    checks = [
        Check(
            "ingest_unique",
            len(set(contents)) == len(contents),
            f"{len(contents)} records, {len(contents) - len(set(contents))} shared contents",
        )
    ]

    # every record carries locations, and every location resolves to a real page
    broken, no_location = [], []
    for record in records:
        occurrences = record.get("occurrences") or []
        if not occurrences:
            no_location.append(record["chunk_id"])
            continue
        for where in occurrences:
            try:
                pages = page_numbers(where["refs"], _blocks_for(where["stem"]))
            except (ProvenanceError, FileNotFoundError) as exc:
                broken.append(f"{where['chunk_id']}: {exc}")
                continue
            if where["page_range"] != [min(pages), max(pages)]:
                broken.append(f"{where['chunk_id']}: page_range {where['page_range']} != {[min(pages), max(pages)]}")
    checks.append(
        Check("occurrences", not broken and not no_location,
              f"{sum(len(r.get('occurrences') or []) for r in records)} locations across "
              f"{len(records)} chunks; {len(no_location)} without any, {len(broken)} broken")
    )

    if reparse:
        with tempfile.TemporaryDirectory() as tmp:
            rebuilt = Path(tmp) / "ingest.jsonl"
            again_header, again_records, _metas = rebuild_ingest(stems, knobs)
            write_jsonl(rebuilt, again_header, again_records)
            same = _digest(rebuilt) == _digest(path)
            checks.append(Check("determinism", same,
                                f"rebuilt from blocks: {'identical' if same else 'BYTES DIFFER'}"))
    return checks


_BLOCK_CACHE: dict[str, dict[str, dict]] = {}


def _blocks_for(stem: str) -> dict[str, dict]:
    """`{blockid: block}` for one document, read once per process."""
    if stem not in _BLOCK_CACHE:
        _sidecar_meta, blocks = read_blocks(doc_dir(stem) / "blocks.jsonl")
        _BLOCK_CACHE[stem] = {block["blockid"]: block for block in blocks}
    return _BLOCK_CACHE[stem]


def rebuild_ingest(stems: list[str], knobs: dict) -> tuple[dict, list[dict], dict[str, dict]]:
    """Chunk every document again and rebuild the ingest set — the corpus determinism check.

    Returns `(header, records, blocks_by_id)`; the caller writes or compares them.
    """
    per_document, metas, by_id = chunk_corpus(stems, knobs)
    header, records = build_ingest_set(per_document, by_id, metas, knobs)
    return header, records, by_id
