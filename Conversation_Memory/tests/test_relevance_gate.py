from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from adapter.magma_adapter import MagmaMemoryAdapter
from adapter.backend import RealMagmaBackend
from adapter.models import (
    BackendCandidate,
    RecallPolicy,
    RelevanceScoreResult,
    ScoredRecallCandidate,
)
from ingestion.state_store import IngestionStateStore
from ingestion.fixture_loader import load_fixture
from recall.relevance import CosineEmbeddingRelevanceScorer


PROVENANCE = {
    "segment_id": "segment-1",
    "conversation_id": "conversation-1",
    "turn_id": "turn-1",
    "source_timestamp": "2026-07-14T10:00:00+08:00",
    "source_timezone": "+08:00",
    "ingestion_version": "v1",
}
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cold_draft_segment_v1.json"
IS_ISOLATED_ENV = (
    Path(sys.executable).resolve()
    == (Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe").resolve()
)


def candidate(
    evidence_id: str,
    backend_score: float,
    query_vector: tuple[float, ...],
    evidence_vector: tuple[float, ...],
    *,
    text: str | None = None,
) -> BackendCandidate:
    provenance = {**PROVENANCE, "turn_id": evidence_id}
    return BackendCandidate(
        text or evidence_id,
        "2026-07-14T10:00:00+08:00",
        backend_score,
        {"evidence_id": evidence_id, "provenance": provenance},
        query_vector,
        evidence_vector,
    )


class RecallOnlyBackend:
    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.policies = []

    def recall(self, query, policy):
        self.policies.append(policy)
        return list(self.candidates)


class FailingScorer:
    def score(self, query, candidates):
        raise RuntimeError("C:\\private\\embedding traceback OPENAI_API_KEY=secret")


class RecordingScorer(CosineEmbeddingRelevanceScorer):
    def __init__(self):
        self.batch_sizes = []

    def score(self, query, candidates):
        self.batch_sizes.append(len(candidates))
        return super().score(query, candidates)


def adapter_for(tmp_path, candidates, *, scorer=CosineEmbeddingRelevanceScorer()):
    backend = RecallOnlyBackend(candidates)
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "state.json"),
        ingestion_version="v1",
        relevance_scorer=scorer,
    )
    return adapter, backend


def test_policy_default_disables_gate_and_preserves_old_candidate_behavior(tmp_path):
    item = candidate("evidence-a", 0.9, (), ())
    adapter, _ = adapter_for(tmp_path, [item], scorer=None)
    context = adapter.recall("query", RecallPolicy())
    assert context.safe_error_code is None
    assert [evidence.evidence_id for evidence in context.evidence] == ["evidence-a"]
    assert context.evidence[0].relevance_score is None


@pytest.mark.parametrize("threshold", [-1.0, 0.0, 0.5, 1.0])
def test_policy_accepts_finite_cosine_thresholds(threshold):
    assert RecallPolicy(min_relevance=threshold).min_relevance == threshold


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), -1.01, 1.01, True, "0.5"])
def test_policy_rejects_invalid_thresholds(threshold):
    with pytest.raises(ValueError, match="min_relevance"):
        RecallPolicy(min_relevance=threshold)


@pytest.mark.parametrize("value", [0, -1, 21])
def test_policy_enforces_candidate_scoring_bound(value):
    with pytest.raises(ValueError, match="max_relevance_candidates"):
        RecallPolicy(max_relevance_candidates=value)


def test_cosine_scores_have_documented_range_and_expected_values():
    scorer = CosineEmbeddingRelevanceScorer()
    candidates = [
        candidate("same", 1.0, (1.0, 0.0), (1.0, 0.0)),
        candidate("orthogonal", 0.9, (1.0, 0.0), (0.0, 1.0)),
        candidate("opposite", 0.8, (1.0, 0.0), (-1.0, 0.0)),
    ]
    result = scorer.score("query", candidates)
    assert [item.relevance_score for item in result.scored_candidates] == [1.0, 0.0, -1.0]
    assert all(-1.0 <= item.relevance_score <= 1.0 for item in result.scored_candidates)


def test_zero_vectors_are_safe():
    scorer = CosineEmbeddingRelevanceScorer()
    result = scorer.score("query", [
        candidate("zero", 1.0, (1.0, 0.0), (0.0, 0.0)),
    ])
    assert [item.relevance_score for item in result.scored_candidates] == [0.0]


@pytest.mark.parametrize("invalid", [(math.nan, 1.0), (math.inf, 1.0)])
def test_nan_and_inf_vectors_return_structured_failure(invalid):
    scorer = CosineEmbeddingRelevanceScorer()
    result = scorer.score("query", [
        candidate("invalid", 1.0, (1.0, 0.0), invalid),
    ])
    assert result.scored_candidates == ()
    assert result.safe_error_code == "relevance_evidence_vector_invalid"


def test_gate_filters_below_threshold_includes_equal_boundary_and_preserves_order(tmp_path):
    candidates = [
        candidate("first", 0.9, (1.0, 0.0), (0.6, 0.8)),
        candidate("filtered", 0.8, (1.0, 0.0), (0.4, math.sqrt(0.84))),
        candidate("equal", 0.7, (1.0, 0.0), (0.5, math.sqrt(0.75))),
    ]
    adapter, _ = adapter_for(tmp_path, candidates)
    context = adapter.recall("query", RecallPolicy(min_relevance=0.5))
    assert [item.evidence_id for item in context.evidence] == ["first", "equal"]
    assert [item.relevance_score for item in context.evidence] == pytest.approx([0.6, 0.5])


def test_all_filtered_returns_valid_empty_context_without_error(tmp_path):
    adapter, _ = adapter_for(tmp_path, [
        candidate("low", 1.0, (1.0, 0.0), (0.0, 1.0)),
    ])
    context = adapter.recall("query", RecallPolicy(min_relevance=0.1))
    assert context.evidence == ()
    assert context.rendered_text == ""
    assert context.truncated is False
    assert context.safe_error_code is None


def test_gate_precedes_top_k_item_and_character_bounds(tmp_path):
    candidates = [
        candidate(f"evidence-{index}", 1.0 - index / 100, (1.0, 0.0), (1.0, 0.0), text="x" * 100)
        for index in range(6)
    ]
    adapter, _ = adapter_for(tmp_path, candidates)
    top_one = adapter.recall("query", RecallPolicy(top_k=1, max_evidence_items=5, min_relevance=0.5))
    max_two = adapter.recall("query", RecallPolicy(top_k=10, max_evidence_items=2, min_relevance=0.5))
    short = adapter.recall("query", RecallPolicy(top_k=5, max_evidence_items=5, max_chars=25, min_relevance=0.5))
    assert len(top_one.evidence) == 1
    assert len(max_two.evidence) == 2
    assert len(short.rendered_text) <= 25 and short.truncated


def test_enabled_gate_never_silently_bypasses_missing_or_failed_scorer(tmp_path):
    item = candidate("evidence", 1.0, (1.0, 0.0), (1.0, 0.0))
    missing, _ = adapter_for(tmp_path, [item], scorer=None)
    failed, _ = adapter_for(tmp_path, [item], scorer=FailingScorer())
    for adapter in (missing, failed):
        context = adapter.recall("query", RecallPolicy(min_relevance=0.5))
        assert context.evidence == ()
        assert context.safe_error_code == "relevance_unavailable"
        assert "private" not in repr(context).lower()
        assert "secret" not in repr(context).lower()


def test_scoring_count_is_defensively_bounded_even_if_backend_overreturns(tmp_path):
    scorer = RecordingScorer()
    candidates = [
        candidate(f"evidence-{index:02d}", 1.0 - index / 100, (1.0, 0.0), (1.0, 0.0))
        for index in range(30)
    ]
    adapter, _ = adapter_for(tmp_path, candidates, scorer=scorer)
    adapter.recall(
        "query",
        RecallPolicy(min_relevance=0.5, max_relevance_candidates=7),
    )
    assert scorer.batch_sizes == [7]


def test_public_context_never_serializes_private_vectors_or_backend_score(tmp_path):
    adapter, _ = adapter_for(tmp_path, [
        candidate("evidence", 0.123456, (1.0, 0.0), (1.0, 0.0)),
    ])
    context = adapter.recall("query", RecallPolicy(min_relevance=0.5))
    serialized = json.dumps(asdict(context), sort_keys=True)
    assert "query_embedding" not in serialized
    assert "evidence_embedding" not in serialized
    assert "backend_score" not in serialized
    assert "0.123456" not in serialized


def test_scorer_source_has_no_llm_network_or_model_initialization():
    source = (
        Path(__file__).resolve().parents[1] / "recall" / "relevance.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in (
        "openai",
        "minimax",
        "requests",
        "httpx",
        "socket",
        "sentencetransformer",
        "transformers",
    ):
        assert forbidden not in source


@pytest.mark.skipif(not IS_ISOLATED_ENV, reason="real MAGMA relevance test uses isolated environment")
def test_real_backend_reuses_query_and_persisted_vectors_without_model_reload(tmp_path):
    persist_dir = tmp_path / "magma"
    state_path = tmp_path / "state.json"
    backend = RealMagmaBackend(persist_dir)
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(state_path),
        ingestion_version="relevance-test-v1",
    )
    assert adapter.ingest(load_fixture(FIXTURE)).status == "completed"
    encoder_identity = id(backend.trg.encoder)
    policy = RecallPolicy(
        top_k=4,
        max_evidence_items=4,
        min_relevance=-1.0,
        max_relevance_candidates=10,
    )
    first = adapter.recall("Why did the membrane experiment fail?", policy)
    second = adapter.recall("Why did the membrane experiment fail?", policy)

    assert first.safe_error_code is None and first.evidence
    assert id(backend.trg.encoder) == encoder_identity
    assert backend.last_recall_diagnostics == {
        "query_embedding_calls": 1,
        "additional_embedding_calls": 0,
        "candidates_returned": len(second.evidence),
        "persisted_vectors_reused": len(second.evidence),
    }
    assert [item.evidence_id for item in first.evidence] == [
        item.evidence_id for item in second.evidence
    ]
    assert [item.relevance_score for item in first.evidence] == pytest.approx(
        [item.relevance_score for item in second.evidence]
    )

    restarted_backend = RealMagmaBackend(persist_dir)
    restarted = MagmaMemoryAdapter(
        restarted_backend,
        IngestionStateStore(state_path),
        ingestion_version="relevance-test-v1",
    ).recall("Why did the membrane experiment fail?", policy)
    assert [item.evidence_id for item in restarted.evidence] == [
        item.evidence_id for item in first.evidence
    ]
    assert [item.relevance_score for item in restarted.evidence] == pytest.approx(
        [item.relevance_score for item in first.evidence]
    )
