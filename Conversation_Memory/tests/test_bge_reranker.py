from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

import pytest

import adapter.magma_adapter as magma_adapter_module
from adapter.magma_adapter import MagmaMemoryAdapter
from adapter.models import BackendCandidate, RecallPolicy, SourceProvenance
from ingestion.state_store import IngestionStateStore
from recall.bge_reranker import BGE_MODEL, BGE_REVISION


class _CandidateBackend:
    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.recall_calls = []

    def recall(self, query, policy, target_entity_ref=None):
        self.recall_calls.append((query, policy))
        return list(self.candidates)


class _FakeReranker:
    def __init__(self, scores=(), error=None):
        self.scores = scores
        self.error = error
        self.calls = []

    def score(self, query, texts):
        self.calls.append((query, texts))
        if self.error is not None:
            raise self.error
        return self.scores


def _policy(
    *,
    final_count=1,
    final_min_score=None,
):
    return RecallPolicy(
        top_k=2,
        max_graph_depth=0,
        max_nodes=20,
        max_evidence_items=final_count,
        max_chars=1000,
        final_min_score=final_min_score,
    )


def _candidate(unit_id, text, score, *, minute=0):
    timestamp = datetime(2026, 8, 8, 14, minute, tzinfo=UTC)
    provenance = SourceProvenance(
        segment_id="production-test-segment",
        conversation_id="production-test-conversation",
        turn_id=f"turn-{unit_id}",
        source_role="user",
        source_timestamp=timestamp.isoformat(),
        source_timezone="Asia/Shanghai",
        ingestion_version="grounded-span-v2",
        timezone_source="client",
    )
    return BackendCandidate(
        text,
        timestamp.isoformat(),
        score,
        {
            "evidence_id": unit_id,
            "provenance": provenance.__dict__,
        },
    ), provenance


def _adapter(tmp_path, candidates):
    backend = _CandidateBackend(candidates)
    return (
        MagmaMemoryAdapter(
            backend,
            IngestionStateStore(tmp_path / "production-bge-state.json"),
        ),
        backend,
    )


def _install_reranker(monkeypatch, reranker, load_calls):
    def load():
        load_calls.append(reranker)
        if isinstance(reranker, BaseException):
            raise reranker
        return reranker

    monkeypatch.setattr(magma_adapter_module, "_create_bge_reranker", load)


def test_fixed_model_identity_is_pinned():
    assert BGE_MODEL == "BAAI/bge-reranker-v2-m3"
    assert BGE_REVISION == "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


def test_production_recall_batches_nonempty_candidates_and_reranks(
    tmp_path,
    monkeypatch,
):
    first, _ = _candidate("first", "first memory", 0.99)
    target, target_provenance = _candidate(
        "target", "target memory", 0.01, minute=1,
    )
    blank, _ = _candidate("blank", "   ", 1.0, minute=2)
    adapter, backend = _adapter(tmp_path, [first, blank, target])
    reranker = _FakeReranker((-4321.0, 9876.5))
    load_calls = []
    _install_reranker(monkeypatch, reranker, load_calls)

    context = adapter.recall("  target query  ", _policy(final_count=2))

    assert backend.recall_calls == [("target query", _policy(final_count=2))]
    assert reranker.calls == [(
        "target query", ("first memory", "target memory"),
    )]
    assert load_calls == [reranker]
    assert [item.evidence_id for item in context.evidence] == [
        "target", "first",
    ]
    assert context.evidence[0].provenance == target_provenance
    assert context.rendered_text == (
        "[USER]\ntarget memory\n[USER]\nfirst memory"
    )
    assert context.safe_error_code is None
    assert first.score == 0.99 and target.score == 0.01
    public = repr(asdict(context))
    assert "9876.5" not in public and "-4321.0" not in public
    assert all(not hasattr(item, "score") for item in context.evidence)


def test_production_recall_equal_scores_keep_backend_order(tmp_path, monkeypatch):
    first, _ = _candidate("first", "first memory", 0.1)
    second, _ = _candidate("second", "second memory", 0.9, minute=1)
    second = BackendCandidate(
        second.text,
        first.timestamp,
        second.score,
        second.metadata,
    )
    second.metadata['provenance']['source_timestamp'] = (
        first.metadata['provenance']['source_timestamp']
    )
    adapter, _backend = _adapter(tmp_path, [first, second])
    _install_reranker(monkeypatch, _FakeReranker((3.0, 3.0)), [])

    context = adapter.recall("query", _policy(final_count=2))

    assert [item.evidence_id for item in context.evidence] == [
        "first", "second",
    ]


def test_hindsight_reference_time_is_candidate_snapshot_watermark(
    tmp_path,
    monkeypatch,
):
    older, _ = _candidate('older', 'older memory', 0.9)
    newer, _ = _candidate('newer', 'newer memory', 0.8, minute=1)
    adapter, _backend = _adapter(tmp_path, [older, newer])
    _install_reranker(monkeypatch, _FakeReranker((0.5, 0.5)), [])
    observed_reference_times = []
    original_score = magma_adapter_module.score_hindsight_post_rerank

    def record_reference_time(scores, timestamps, *, now):
        observed_reference_times.append(now)
        return original_score(scores, timestamps, now=now)

    monkeypatch.setattr(
        magma_adapter_module,
        'score_hindsight_post_rerank',
        record_reference_time,
    )

    class EarlyWallClock(datetime):
        @classmethod
        def now(cls, timezone):
            return cls(1990, 1, 1, tzinfo=timezone)

    class LateWallClock(datetime):
        @classmethod
        def now(cls, timezone):
            return cls(2090, 1, 1, tzinfo=timezone)

    monkeypatch.setattr(magma_adapter_module, 'datetime', EarlyWallClock)
    early = adapter.recall('query', _policy(final_count=2))
    monkeypatch.setattr(magma_adapter_module, 'datetime', LateWallClock)
    late = adapter.recall('query', _policy(final_count=2))

    expected = datetime.fromisoformat(newer.timestamp).astimezone(UTC)
    assert observed_reference_times == [expected, expected]
    assert early == late
    assert [item.evidence_id for item in early.evidence] == [
        'newer', 'older',
    ]


@pytest.mark.parametrize('source_timestamp', [None, '2026-08-08T14:00:00'])
def test_hindsight_reference_time_requires_aware_persisted_timestamp(
    tmp_path,
    monkeypatch,
    source_timestamp,
):
    candidate, _ = _candidate('candidate', 'candidate memory', 0.5)
    candidate.metadata['provenance']['source_timestamp'] = source_timestamp
    adapter, _backend = _adapter(tmp_path, [candidate])
    _install_reranker(monkeypatch, _FakeReranker((0.5,)), [])

    context = adapter.recall('query', _policy())

    assert context.evidence == ()
    assert context.safe_error_code == 'recall_unavailable'


def test_production_recall_lazy_loads_once_and_reuses_instance(
    tmp_path,
    monkeypatch,
):
    candidate, _ = _candidate("candidate", "candidate memory", 0.5)
    adapter, _backend = _adapter(tmp_path, [candidate])
    reranker = _FakeReranker((1.0,))
    load_calls = []
    _install_reranker(monkeypatch, reranker, load_calls)

    first = adapter.recall("first query", _policy())
    second = adapter.recall("second query", _policy())

    assert first.safe_error_code is None and second.safe_error_code is None
    assert load_calls == [reranker]
    assert len(reranker.calls) == 2
    assert adapter._bge_reranker is reranker


def test_production_zero_backend_candidates_is_successful_and_skips_bge(
    tmp_path,
    monkeypatch,
):
    adapter, _backend = _adapter(tmp_path, [])

    def unexpected_load():
        raise AssertionError("empty recall must not construct BGE")

    monkeypatch.setattr(
        magma_adapter_module, "_create_bge_reranker", unexpected_load,
    )

    context = adapter.recall("query", _policy())

    assert context.evidence == ()
    assert context.rendered_text == ""
    assert context.safe_error_code is None
    assert adapter._bge_reranker_load_attempted is False


def test_production_final_selection_may_be_empty_without_failure(
    tmp_path,
    monkeypatch,
):
    candidate, _ = _candidate("candidate", "candidate memory", 0.5)
    adapter, _backend = _adapter(tmp_path, [candidate])
    reranker = _FakeReranker((1.0,))
    _install_reranker(monkeypatch, reranker, [])

    def select_none(items, *, count, max_chars):
        assert len(items) == 1
        assert count == 1
        assert max_chars == 1000
        return (), "", False

    monkeypatch.setattr(magma_adapter_module, "bound_evidence", select_none)

    context = adapter.recall("query", _policy())

    assert reranker.calls == [("query", ("candidate memory",))]
    assert context.evidence == ()
    assert context.rendered_text == ""
    assert context.truncated is False
    assert context.safe_error_code is None
    assert 0 <= len(context.evidence) <= _policy().max_evidence_items


def test_hindsight_final_floor_is_inclusive_and_can_return_successful_empty(
    tmp_path,
    monkeypatch,
):
    kept, _ = _candidate('kept', 'kept memory', 0.9)
    removed, _ = _candidate('removed', 'removed memory', 0.8)
    adapter, _backend = _adapter(tmp_path, [kept, removed])
    reranker = _FakeReranker((0.5, 0.4))
    _install_reranker(monkeypatch, reranker, [])

    filtered = adapter.recall(
        'query',
        _policy(final_count=2, final_min_score=0.55),
    )
    empty = adapter.recall(
        'query',
        _policy(final_count=2, final_min_score=0.56),
    )

    assert [item.evidence_id for item in filtered.evidence] == ['kept']
    assert filtered.safe_error_code is None
    assert empty.evidence == ()
    assert empty.rendered_text == ''
    assert empty.truncated is False
    assert empty.safe_error_code is None
    assert all(not hasattr(item, 'score') for item in filtered.evidence)


def test_production_bge_load_failure_is_cached_and_safe(tmp_path, monkeypatch):
    candidate, _ = _candidate("candidate", "candidate memory", 0.5)
    adapter, _backend = _adapter(tmp_path, [candidate])
    load_calls = []
    _install_reranker(
        monkeypatch, RuntimeError("private model path"), load_calls,
    )

    first = adapter.recall("first query", _policy())
    second = adapter.recall("second query", _policy())

    assert first.safe_error_code == "recall_unavailable"
    assert second.safe_error_code == "recall_unavailable"
    assert first.evidence == second.evidence == ()
    assert len(load_calls) == 1
    assert "private model path" not in repr((first, second))


def test_production_bge_scoring_failure_is_safe(tmp_path, monkeypatch):
    candidate, _ = _candidate("candidate", "candidate memory", 0.5)
    adapter, _backend = _adapter(tmp_path, [candidate])
    reranker = _FakeReranker(error=RuntimeError("private scoring body"))
    _install_reranker(monkeypatch, reranker, [])

    context = adapter.recall("query", _policy())

    assert context.evidence == ()
    assert context.safe_error_code == "recall_unavailable"
    assert "private scoring body" not in repr(context)


@pytest.mark.parametrize(
    "scores",
    [None, (), (1.0, 2.0), (float("nan"),), (float("inf"),)],
)
def test_production_bge_invalid_outputs_are_safe(tmp_path, monkeypatch, scores):
    candidate, _ = _candidate("candidate", "candidate memory", 0.5)
    adapter, _backend = _adapter(tmp_path, [candidate])
    _install_reranker(monkeypatch, _FakeReranker(scores), [])

    context = adapter.recall("query", _policy())

    assert context.evidence == ()
    assert context.safe_error_code == "recall_unavailable"


def test_production_projection_exception_is_safe(tmp_path, monkeypatch):
    candidate, _ = _candidate("candidate", "candidate memory", 0.5)

    class ExplodingMetadata(dict):
        def get(self, _key, _default=None):
            raise RuntimeError("private projection body")

    candidate = BackendCandidate(
        candidate.text,
        candidate.timestamp,
        candidate.score,
        ExplodingMetadata(candidate.metadata),
    )
    adapter, _backend = _adapter(tmp_path, [candidate])
    _install_reranker(monkeypatch, _FakeReranker((1.0,)), [])

    context = adapter.recall("query", _policy())

    assert context.evidence == ()
    assert context.safe_error_code == "recall_unavailable"
    assert "private projection body" not in repr(context)
