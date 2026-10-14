"""embedding/encoder.py — text in, one unit-length 1024-float vector out.

Retrieval compares numbers, so every chunk and every question has to become a point in the
*same* 1024-dimensional space, made by the *same* code. Two encoders — even with identical
weights — do not produce comparable vectors, and a citation built on the wrong ordering is
wrong silently. Hence one function, one place.

What happens to one text, in five steps:

    text ──tokenize──▶ ids
         ──forward───▶ last_hidden_state   (batch, tokens, 1024)
         ──pool──────▶ row 0               (batch, 1024)   ← CLS, per the model's 1_Pooling/config.json
         ──normalise─▶ Euclidean length 1
         ──return────▶ list[list[float]]

Two behaviours that look like details and are not:

* **Two tokenizers, deliberately.** `tokens` in `_ingest.jsonl` is tiktoken `o200k_base`,
  the count the chunker used for its 1200 cap. The model reads XLM-R sentencepiece, which
  counts the same text 15% higher (measured on the slice: mean 642 vs 556, max 1619 vs
  1200). Neither count is wrong; they measure different things. The chunker owns the cap,
  this module owns the 8192 window.
* **No query prefix.** Several BGE models require an instruction prefix on the query side.
  bge-m3 does not: queries and documents go through the same call. Do not "fix" this.

The model is loaded once per process (1.7 s, ~2.3 GB fp32) and cached. Device is chosen
once — MPS when available, CPU otherwise — and can be forced with `HYRAG_DEVICE=cpu` if an
MPS op turns out not to be implemented; CPU encodes the whole 369-chunk corpus in ~6 min.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache  # for caching result returned from function calls

import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer  # from the HuggingFace library

MODEL_NAME = "BAAI/bge-m3"
DIM = 1024  # word embedding dimension, default of BAAI/bge-m3
MAX_TOKENS = 8192  # also default of BAAI/bge-m3; the cap we actually choose is the chunker's
DEFAULT_BATCH_SIZE = 8

log = logging.getLogger(__name__)


def device() -> str:
    """Which computing device PyTorch should use: `mps` on this Mac, else `cpu`.

    `HYRAG_DEVICE` overrides the choice, which is the escape hatch if an MPS op turns out
    not to be implemented — swapping device must never require editing the loader.
    """
    requested = os.environ.get("HYRAG_DEVICE")
    if requested:
        return requested
    return "mps" if torch.backends.mps.is_available() else "cpu"


@lru_cache(maxsize=1)
def _load() -> tuple[AutoTokenizer, torch.nn.Module]:
    """The tokenizer and the model, loaded once per process and kept."""
    target = device()  # get either mps or cpu
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)  # class method

    model = AutoModel.from_pretrained(MODEL_NAME)
    model.to(target)  # move weights to the chosen device
    # eval() is not cosmetic: in training mode dropout stays on and the same text returns a
    # different vector on every call, which turns any spot check into a coin toss.
    model.eval()

    log.info("encoder ready: %s on %s", MODEL_NAME, target)
    return tokenizer, model


def encode(texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE) -> list[list[float]]:
    """One unit-length vector per text, returned in the same order as `texts`."""
    if not texts:
        return []

    tokenizer, model = _load()
    target = next(model.parameters()).device
    vectors: list[list[float]] = []  # to store final embeddings

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]

        # `return_tensors="pt"` gives a BatchEncoding of PyTorch tensors instead of lists.
        # truncation=False so a cut can be *reported* rather than taken silently; safe here
        # because our longest chunk is 1619 tokens against an 8192 window, and the slice
        # below exists for the day that stops being true.
        enc = tokenizer(batch, padding=True, truncation=False, return_tensors="pt")
        # every real token, padding excluded — the attention mask is what marks the difference
        lengths = enc["attention_mask"].sum(dim=1).tolist()

        if max(lengths) > MAX_TOKENS:
            log.warning(
                "truncating %d of %d inputs to %d tokens (longest %d)",
                sum(1 for n in lengths if n > MAX_TOKENS),
                len(batch),
                MAX_TOKENS,
                max(lengths),
            )
            # [:, :MAX_TOKENS] keeps every row but only the first MAX_TOKENS columns
            enc["input_ids"] = enc["input_ids"][:, :MAX_TOKENS]
            enc["attention_mask"] = enc["attention_mask"][:, :MAX_TOKENS]

        # items() walks the key/value pairs and sends each tensor to the chosen device
        enc = {name: tensor.to(target) for name, tensor in enc.items()}
        with torch.inference_mode():
            hidden = model(**enc).last_hidden_state  # (batch, tokens, 1024)
        # Row 0 is the `<s>` token (CLS): the model's summary of the whole input. Every row
        # below it belongs to one token position and is not an embedding of the text.
        # F.normalize rescales each vector so its Euclidean length is 1 — the sum of squares
        # is 1, elements stay in [-1, 1] and may be negative. Cosine needs the normalisation;
        # without it, an inner product would be dominated by whichever vector is longer.
        pooled = F.normalize(hidden[:, 0], p=2, dim=1)
        vectors.extend(pooled.tolist())  # this batch's vectors, appended in order

    return vectors


def encode_one(text: str) -> list[float]:
    """The query-time shape of `encode` — one text, one vector."""
    return encode([text])[0]
