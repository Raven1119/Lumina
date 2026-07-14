from __future__ import annotations

import math
from typing import Sequence

from adapter.models import (
    BackendCandidate,
    RelevanceScoreResult,
    ScoredRecallCandidate,
)


class CosineEmbeddingRelevanceScorer:
    """Scores a bounded candidate batch using private, already-created vectors."""

    @staticmethod
    def _cosine(
        query_vector: tuple[float, ...],
        evidence_vector: tuple[float, ...],
    ) -> float:
        if not query_vector or len(query_vector) != len(evidence_vector):
            return -1.0
        if not all(math.isfinite(value) for value in (*query_vector, *evidence_vector)):
            return -1.0
        query_norm_sq = sum(value * value for value in query_vector)
        evidence_norm_sq = sum(value * value for value in evidence_vector)
        if query_norm_sq == 0.0 or evidence_norm_sq == 0.0:
            return 0.0
        score = sum(
            query_value * evidence_value
            for query_value, evidence_value in zip(query_vector, evidence_vector)
        ) / math.sqrt(query_norm_sq * evidence_norm_sq)
        if not math.isfinite(score):
            return -1.0
        return max(-1.0, min(1.0, float(score)))

    def score(
        self,
        query: str,
        candidates: Sequence[BackendCandidate],
    ) -> RelevanceScoreResult:
        if not isinstance(query, str) or not query.strip():
            return RelevanceScoreResult(safe_error_code="relevance_invalid_query")
        if not candidates:
            return RelevanceScoreResult()
        query_vector = candidates[0].query_embedding
        if query_vector is None:
            return RelevanceScoreResult(safe_error_code="relevance_vector_unavailable")
        if not query_vector or not all(math.isfinite(value) for value in query_vector):
            return RelevanceScoreResult(safe_error_code="relevance_query_vector_invalid")

        scored: list[ScoredRecallCandidate] = []
        for candidate in candidates:
            if candidate.query_embedding != query_vector:
                return RelevanceScoreResult(
                    candidates_scored=len(scored),
                    safe_error_code="relevance_query_vector_mismatch",
                )
            evidence_vector = candidate.evidence_embedding
            if (
                evidence_vector is None
                or len(evidence_vector) != len(query_vector)
                or not all(math.isfinite(value) for value in evidence_vector)
            ):
                return RelevanceScoreResult(
                    candidates_scored=len(scored),
                    safe_error_code="relevance_evidence_vector_invalid",
                )
            score = self._cosine(
                query_vector,
                evidence_vector,
            )
            scored.append(ScoredRecallCandidate(candidate, score))
        return RelevanceScoreResult(tuple(scored), len(scored))
