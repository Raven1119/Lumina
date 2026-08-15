from __future__ import annotations

from math import isfinite
from typing import Sequence


BGE_MODEL = "BAAI/bge-reranker-v2-m3"
BGE_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
BGE_MAX_LENGTH = 512


class BgeReranker:
    """Reusable CPU scorer for the fixed multilingual BGE reranker."""

    def __init__(self) -> None:
        import torch
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(
            BGE_MODEL,
            revision=BGE_REVISION,
        )
        self._model = AutoModelForSequenceClassification.from_pretrained(
            BGE_MODEL,
            revision=BGE_REVISION,
        )
        self._model.to("cpu")
        self._model.eval()
        if getattr(self._model.config, "_commit_hash", None) != BGE_REVISION:
            raise RuntimeError("BGE reranker revision mismatch")

    def score(
        self,
        query: str,
        candidate_texts: Sequence[str],
    ) -> tuple[float, ...]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty")
        texts = tuple(candidate_texts)
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("candidate texts must be non-empty strings")
        if not texts:
            return ()

        inputs = self._tokenizer(
            [query] * len(texts),
            list(texts),
            padding=True,
            truncation=True,
            max_length=BGE_MAX_LENGTH,
            return_tensors="pt",
        )
        with self._torch.no_grad():
            logits = (
                self._model(**inputs, return_dict=True)
                .logits.view(-1)
                .float()
                .cpu()
                .tolist()
            )
        scores = tuple(float(score) for score in logits)
        if len(scores) != len(texts) or any(
            not isfinite(score) for score in scores
        ):
            raise RuntimeError("BGE reranker returned malformed logits")
        return scores
