"""answer — one answer over the routed evidence, with citations that resolve.

The delivered system's user-facing box: it takes the router's class, gathers the evidence the
strategy says to gather (one arm's chunks, or `fuse`'s reranked shortlist), makes **one**
generation call with the citation strings handed in, and verifies what came back.

Three properties matter here and are all checkable:

* **One citation granularity** — `file, §section, p. N–M` (D13). LightRAG cites filenames and
  GraphRAG cites report ids; the delivered answer cites the chunk's own provenance, which is
  why `answer` generates rather than forwarding an arm's text.
* **Citations are verified against the PDF**, not against our parse alone:
  `chunker/checks.py::pdf_page_coverage` reads the original PDF with a second library.
* **The route is the router's.** This box never chooses an arm; it reads
  `hyrag/router/strategy.py` and follows it, so the table an ablation swaps is the one the
  system actually used.

| module | job |
|---|---|
| `gather.py` | route → evidence entries (chunk id, citation, text), per class |
| `render.py` | the citation contract, the prompt, the one call |
| `pipeline.py` | question → answer record, with the used/invented split |
| `checks.py` | the gate and the delivered row's evaluation |
| `cli.py` | `answer · check · eval` |
"""
