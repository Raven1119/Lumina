"""Frozen controls for the actual Recall scoring boundary, with synthetic logits.

No model weights or local experimental artifacts are needed by these tests.
"""

from __future__ import annotations

import tempfile
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from adapter.magma_adapter import _score_candidates
from recall.bge_reranker import BGE_MAX_LENGTH, BgeReranker
from recall.hindsight_scoring import normalize_cross_encoder_scores, score_hindsight_post_rerank
from Conversation_Memory.tests.test_bge_reranker import _adapter, _candidate, _policy


@pytest.fixture(autouse=True)
def isolate_read_foundation_state(tmp_path, monkeypatch):
    assert tmp_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    monkeypatch.setenv("LUMINA_MIND_DECISION_LOG_PATH", str(tmp_path / "mind_decisions.jsonl"))


@pytest.mark.parametrize("logits,expected", [
    ((), ()),
    ((0.1,), (0.5249791874789400,)),
    ((0.9,), (0.7109495026250039,)),
    ((0.0, 0.25, 1.0), (0.5, 0.5621765008857981, 0.7310585786300049)),
    ((-2.0, 0.1), (0.11920292202211755, 0.5249791874789400)),
    ((0.9, 1.2), (0.7109495026250039, 0.7685247834990175)),
    ((-1000.0, -0.0, 1000.0), (0.0, 0.5, 1.0)),
])
def test_raw_logits_always_receive_one_stable_sigmoid(logits, expected):
    assert normalize_cross_encoder_scores(logits) == pytest.approx(expected)


@pytest.mark.parametrize("logit,other,normalized", [
    (0.1, -2.0, 0.5249791874789400),
    (0.9, 1.2, 0.7109495026250039),
])
def test_fixed_time_score_does_not_change_when_another_raw_logit_is_added(logit, other, normalized):
    now = datetime(2026, 9, 13, 9, tzinfo=UTC)
    single = score_hindsight_post_rerank((logit,), (now.isoformat(),), now=now)[0]
    together = score_hindsight_post_rerank(
        (logit, other), (now.isoformat(), now.isoformat()), now=now,
    )[0]
    assert single == together
    assert single.raw_cross_encoder_score == logit
    assert single.normalized_cross_encoder_score == pytest.approx(normalized)
    assert single.final_score == pytest.approx(normalized * 1.1)
    assert single.recency == 1.0 and single.temporal == single.proof_norm == 0.5
    assert single.final_score >= 0.144


@pytest.mark.parametrize("chunk_sizes", [(6,), (1, 1, 1, 1, 1, 1), (4, 2), (2, 1, 3)])
def test_normalized_logits_are_independent_of_batch_partition(chunk_sizes):
    logits = (0.0, 0.1, 0.9, 1.0, -2.0, 1.2)
    expected = (0.5, 0.5249791874789400, 0.7109495026250039,
                0.7310585786300049, 0.11920292202211755, 0.7685247834990175)
    actual, offset = [], 0
    for size in chunk_sizes:
        actual.extend(normalize_cross_encoder_scores(logits[offset:offset + size]))
        offset += size
    assert offset == len(logits)
    assert actual == pytest.approx(expected)
    assert normalize_cross_encoder_scores(tuple(reversed(logits))) == pytest.approx(tuple(reversed(expected)))


def test_bge_score_returns_model_logits_without_normalizing_them():
    raw = [0.1, 0.9, -2.0]
    calls = []

    class Logits:
        def view(self, dimension):
            assert dimension == -1
            return self

        def float(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return raw

    def tokenize(queries, texts, **kwargs):
        calls.append((queries, texts, kwargs))
        return {"input_ids": "synthetic tensor"}

    def forward(**inputs):
        assert inputs == {"input_ids": "synthetic tensor", "return_dict": True}
        return SimpleNamespace(logits=Logits())

    scorer = BgeReranker.__new__(BgeReranker)
    scorer._torch = SimpleNamespace(no_grad=nullcontext)
    scorer._tokenizer, scorer._model = tokenize, forward
    assert scorer.score("tool query", ("Iris uses a lathe.", "Nora uses a lathe.", "No tool is specified.")) == tuple(raw)
    assert calls[0][0] == ["tool query"] * 3
    assert calls[0][2] == {"padding": True, "truncation": True,
                           "max_length": BGE_MAX_LENGTH, "return_tensors": "pt"}


def test_marked_and_plain_scoring_preserves_raw_logit_positions():
    first, _ = _candidate("marked", "Iris uses a lathe.", 0.01)
    first.metadata["subject_entity_ref"] = "iris"
    second, _ = _candidate("plain", "Nora uses a lathe.", 0.99)
    calls = []

    class Scorer:
        def score(self, query, texts):
            calls.append((query, tuple(texts)))
            return (0.1,) if "Iris uses a lathe." in texts[0] else (-2.0,)

    raw = _score_candidates(Scorer(), "Which tool?", "iris", ((0, first), (1, second)))
    assert raw == (0.1, -2.0) and len(calls) == 2
    assert calls[0][0] != calls[1][0] and calls[1][0] == "Which tool?"
    assert normalize_cross_encoder_scores(raw) == pytest.approx((0.5249791874789400, 0.11920292202211755))


def test_public_recall_keeps_the_same_candidate_across_the_fixed_point_144_floor(tmp_path):
    target, _ = _candidate("target", "Mira uses a lathe.", 0.01)
    distractor, _ = _candidate("distractor", "Tessa uses a lathe.", 0.99)
    observed = []

    class Scorer:
        def score(self, query, texts):
            observed.append((query, tuple(texts)))
            return tuple(0.1 if text == target.text else -2.0 for text in texts)

    for candidates in ([target], [target, distractor], [distractor, target]):
        instance, _backend = _adapter(tmp_path, candidates)
        instance._bge_reranker, instance._bge_reranker_load_attempted = Scorer(), True
        result = instance.recall("Which tool does Mira use?", _policy(final_count=2, final_min_score=0.144))
        assert result.safe_error_code is None
        assert [item.evidence_id for item in result.evidence] == ["target"]
        assert result.evidence[0].text == target.text
    assert len(observed) == 3
