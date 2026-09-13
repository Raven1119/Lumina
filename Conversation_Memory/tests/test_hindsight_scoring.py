from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import exp

import pytest

from recall.hindsight_scoring import (
    NEUTRAL_SIGNAL,
    compute_linear_recency,
    normalize_cross_encoder_scores,
    score_hindsight_post_rerank,
)


def test_logits_are_sigmoid_normalized_for_the_entire_batch():
    normalized = normalize_cross_encoder_scores((-2.0, 0.25, 2.0))

    assert normalized == pytest.approx(tuple(
        1.0 / (1.0 + exp(-score)) for score in (-2.0, 0.25, 2.0)
    ))


def test_raw_logits_inside_unit_interval_are_still_normalized():
    assert normalize_cross_encoder_scores((0.0, 0.25, 1.0)) == pytest.approx((
        0.5,
        1.0 / (1.0 + exp(-0.25)),
        1.0 / (1.0 + exp(-1.0)),
    ))


def test_missing_timestamp_is_neutral_recency():
    assert compute_linear_recency(
        None,
        now=datetime(2026, 8, 10, tzinfo=UTC),
    ) == NEUTRAL_SIGNAL


def test_linear_recency_uses_365_day_window_floor_and_future_clamp():
    now = datetime(2026, 8, 10, tzinfo=UTC)

    assert compute_linear_recency(now.isoformat(), now=now) == 1.0
    assert compute_linear_recency(
        (now - timedelta(days=182.5)).isoformat(),
        now=now,
    ) == 0.5
    assert compute_linear_recency(
        (now - timedelta(days=365)).isoformat(),
        now=now,
    ) == 0.1
    assert compute_linear_recency(
        (now - timedelta(days=730)).isoformat(),
        now=now,
    ) == 0.1
    assert compute_linear_recency(
        (now + timedelta(days=30)).isoformat(),
        now=now,
    ) == 1.0


def test_final_formula_keeps_missing_temporal_and_proof_signals_neutral():
    now = datetime(2026, 8, 10, tzinfo=UTC)
    result = score_hindsight_post_rerank(
        (-1.0,),
        ((now - timedelta(days=182.5)).isoformat(),),
        now=now,
    )[0]

    expected_normalized = 1.0 / (1.0 + exp(1.0))
    assert result.normalized_cross_encoder_score == pytest.approx(
        expected_normalized
    )
    assert result.recency == 0.5
    assert result.temporal == 0.5
    assert result.proof_norm == 0.5
    assert result.final_score == pytest.approx(
        expected_normalized
        * (1.0 + 0.2 * (0.5 - 0.5))
        * (1.0 + 0.2 * (0.5 - 0.5))
        * (1.0 + 0.1 * (0.5 - 0.5))
    )
