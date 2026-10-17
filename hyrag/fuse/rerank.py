"""fuse/rerank.py — the cross-encoder, in-process, on CPU.

**Why in-process and not behind `hyrag/serving`:** the embedding service holds MPS, and a
second process on the same device makes embedding-bound work crawl (README → Landmines). This
model scores a 20-chunk shortlist — two to four seconds on CPU — and needs no equality contract
with anything else, so the serving layer buys it nothing.

**Why `bge-reranker-base` and not `bge-reranker-v2-m3`:** disk. The machine had 4.4 GB free when
the model was fetched and 3.1 GB after; v2-m3 is 2.3 GB. `base` is the same family as the fixed
embedder (`bge-m3`), trained on English and Chinese — and the corpus is English. The
substitution is recorded in the run record rather than left in a path.

**Why the model is on local disk and not in the HF cache:** `huggingface.co` is unreachable from
this machine (HTTP 000 on IPv4 and IPv6); `hf-mirror.com` serves it. The files were fetched once
into `models/` (gitignored) and loaded with `local_files_only=True`, so a run cannot hang on a
network call.

Truncation is explicit: the model takes 512 tokens and our chunks run to ~1,200 XLM-R tokens.
Scoring truncates, and the caller records which chunks were truncated — a score that never saw
the chunk's tail is still a score, but the record must not pretend otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

from hyrag.parse.config import REPO

MODEL_ID = "BAAI/bge-reranker-base"
MODEL_PATH = Path(os.environ.get("HYRAG_RERANKER", REPO / "models" / "reranker" / "bge-reranker-base"))
MAX_LENGTH = 512
BATCH = 8


class CrossEncoder:
    """`score(question, passages) -> [float]`, higher = more relevant. Lazy, CPU, eval mode."""

    def __init__(self, path: Path | None = None, max_length: int = MAX_LENGTH):
        self.path = Path(path) if path else MODEL_PATH
        self.max_length = max_length
        self._tokenizer = None
        self._model = None

    @property
    def id(self) -> str:
        return f"{MODEL_ID}@{self.path.name}"

    def load(self) -> "CrossEncoder":
        if self._model is not None:
            return self
        if not (self.path / "config.json").is_file():
            raise SystemExit(
                f"no reranker at {self.path} — fetch {MODEL_ID} into it "
                f"(huggingface.co is unreachable here; hf-mirror.com works): "
                f"see nodes/fuse.md → Verified facts 2"
            )
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(str(self.path), local_files_only=True)
        self._model = AutoModelForSequenceClassification.from_pretrained(
            str(self.path), local_files_only=True
        ).eval()
        torch.set_grad_enabled(False)
        return self

    def score(self, question: str, passages: list[str]) -> list[float]:
        """Relevance of each passage to the question, in input order."""
        if not passages:
            return []
        self.load()
        import torch

        scores: list[float] = []
        for start in range(0, len(passages), BATCH):
            batch = passages[start : start + BATCH]
            encoded = self._tokenizer(
                [[question, passage] for passage in batch],
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            logits = self._model(**encoded).logits.view(-1)
            scores.extend(float(value) for value in logits.tolist())
        return scores

    def truncated(self, passage: str) -> bool:
        """Whether scoring this passage will have cut it — the tokenizer's own answer."""
        self.load()
        return len(self._tokenizer(passage, add_special_tokens=False)["input_ids"]) > self.max_length
