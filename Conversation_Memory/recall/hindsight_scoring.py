from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import exp
from typing import Sequence


# Mirrors the post-rerank scoring defaults in vectorize-io/hindsight at
# f1c825d88471d069aec0480446d071c589ab10bd (MIT). Lumina deliberately keeps
# temporal proximity and proof count neutral because its current read path has
# no equivalent truthful per-candidate signals.
RECENCY_ALPHA = 0.2
TEMPORAL_ALPHA = 0.2
PROOF_COUNT_ALPHA = 0.1
RECENCY_LINEAR_WINDOW_DAYS = 365.0
RECENCY_LINEAR_FLOOR = 0.1
NEUTRAL_SIGNAL = 0.5


@dataclass(frozen=True)
class HindsightPostRerankScore:
    raw_cross_encoder_score: float
    normalized_cross_encoder_score: float
    recency: float
    temporal: float
    proof_norm: float
    final_score: float


def normalize_cross_encoder_scores(
    raw_scores: Sequence[float],
) -> tuple[float, ...]:
    """Apply Hindsight's batch-level pass-through-or-sigmoid rule."""

    scores = tuple(float(score) for score in raw_scores)
    if not scores:
        return ()
    if min(scores) >= 0.0 and max(scores) <= 1.0:
        return scores
    return tuple(_sigmoid(score) for score in scores)


def compute_linear_recency(
    source_timestamp: str | None,
    *,
    now: datetime,
) -> float:
    """Mirror Hindsight's default 365-day linear recency signal."""

    if source_timestamp is None:
        return NEUTRAL_SIGNAL
    occurred = datetime.fromisoformat(source_timestamp)
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    days_ago = (now - occurred).total_seconds() / 86400.0
    return max(
        RECENCY_LINEAR_FLOOR,
        min(1.0, 1.0 - (days_ago / RECENCY_LINEAR_WINDOW_DAYS)),
    )


def score_hindsight_post_rerank(
    raw_scores: Sequence[float],
    source_timestamps: Sequence[str | None],
    *,
    now: datetime,
) -> tuple[HindsightPostRerankScore, ...]:
    """Compute the one concrete Hindsight-style score used by Lumina Recall."""

    scores = tuple(float(score) for score in raw_scores)
    timestamps = tuple(source_timestamps)
    if len(scores) != len(timestamps):
        raise ValueError("score and timestamp counts must match")
    normalized_scores = normalize_cross_encoder_scores(scores)
    results: list[HindsightPostRerankScore] = []
    for raw_score, normalized_score, source_timestamp in zip(
        scores,
        normalized_scores,
        timestamps,
        strict=True,
    ):
        recency = compute_linear_recency(source_timestamp, now=now)
        temporal = NEUTRAL_SIGNAL
        proof_norm = NEUTRAL_SIGNAL
        recency_boost = 1.0 + RECENCY_ALPHA * (recency - NEUTRAL_SIGNAL)
        temporal_boost = 1.0 + TEMPORAL_ALPHA * (
            temporal - NEUTRAL_SIGNAL
        )
        proof_count_boost = 1.0 + PROOF_COUNT_ALPHA * (
            proof_norm - NEUTRAL_SIGNAL
        )
        final_score = (
            normalized_score
            * recency_boost
            * temporal_boost
            * proof_count_boost
        )
        results.append(
            HindsightPostRerankScore(
                raw_cross_encoder_score=raw_score,
                normalized_cross_encoder_score=normalized_score,
                recency=recency,
                temporal=temporal,
                proof_norm=proof_norm,
                final_score=final_score,
            )
        )
    return tuple(results)


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + exp(-value))
    exp_value = exp(value)
    return exp_value / (1.0 + exp_value)
