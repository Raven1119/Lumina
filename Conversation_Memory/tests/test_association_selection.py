from dataclasses import replace

import pytest

from adapter.magma_adapter import MagmaMemoryAdapter
from adapter.models import BackendCandidate, RecallPolicy
from ingestion.state_store import IngestionStateStore


def candidates():
    provenance = dict(segment_id="s", conversation_id="c", turn_id="u",
                      source_role="user", source_timestamp="2026-09-13T00:00:00+00:00",
                      source_timezone="UTC", ingestion_version="grounded-formation-v2",
                      timezone_source="client")
    def make(eid, text, subject, obj):
        return BackendCandidate(text, provenance["source_timestamp"], 0.1, dict(
            evidence_id=eid, provenance=provenance,
            subject_entity_ref=subject, object_entity_ref=obj,
        ))
    bridge = make("f1", "项目甲由严青负责。", "project", "person")
    endpoint = make("f2", "严青并不研究材料乙。", "person", "material")
    endpoint = replace(endpoint, metadata={**endpoint.metadata,
        "association_chain_evidence_ids": ["f1", "f2"],
        "association_bridge_evidence_ids": ["f1"],
    })
    return bridge, endpoint


class Backend:
    def __init__(self, items):
        self.items = items
    def resolve_target_entity_ref(self, query):
        return None
    def recall(self, query, policy):
        return self.items


class Reranker:
    def __init__(self):
        self.texts = ()

    def fits_pair(self, query, text):
        return True

    def score(self, query, texts):
        assert len(texts) == 2
        self.texts = tuple(texts)
        return (0.01, 0.9)


@pytest.mark.parametrize("count,max_chars,complete", [(2, 1000, True), (1, 1000, False), (2, 20, False)])
def test_selected_endpoint_requires_whole_source_chain(tmp_path, count, max_chars, complete):
    bridge, endpoint = candidates()
    adapter = MagmaMemoryAdapter(Backend([bridge, endpoint]), IngestionStateStore(tmp_path / "state"))
    adapter._bge_reranker_load_attempted = True
    adapter._bge_reranker = Reranker()
    result = adapter.recall("负责项目甲的人研究什么？", RecallPolicy(
        max_evidence_items=count, max_chars=max_chars, final_min_score=0.144,
    ))
    assert result.safe_error_code is None
    assert bool(result.evidence) == complete
    if complete:
        assert [e.evidence_id for e in result.evidence] == ["f1", "f2"]
        assert result.evidence[-1].text == endpoint.text
        assert result.evidence[0].text == bridge.text
        assert adapter._bge_reranker.texts == (bridge.text, bridge.text + "\n" + endpoint.text)
        assert not result.truncated
    else:
        assert result.rendered_text == "" and result.truncated


def test_missing_or_unrelated_bridge_cannot_be_added(tmp_path):
    bridge, endpoint = candidates()
    unrelated = replace(bridge, metadata={**bridge.metadata,
                                         "subject_entity_ref": "other", "object_entity_ref": "unrelated"})
    adapter = MagmaMemoryAdapter(Backend([unrelated, endpoint]), IngestionStateStore(tmp_path / "state"))
    adapter._bge_reranker_load_attempted = True
    adapter._bge_reranker = Reranker()
    result = adapter.recall("项目负责人研究什么？", RecallPolicy(final_min_score=0.144))
    assert result.evidence == ()
    assert result.safe_error_code is None and result.truncated
    assert adapter._bge_reranker.texts == (unrelated.text, endpoint.text)


def test_invalid_bridge_provenance_cannot_influence_endpoint_score(tmp_path):
    bridge, endpoint = candidates()
    bridge = replace(bridge, metadata={**bridge.metadata, "provenance": {
        "source_timestamp": bridge.timestamp,
    }})
    adapter = MagmaMemoryAdapter(Backend([bridge, endpoint]), IngestionStateStore(tmp_path / "state"))
    adapter._bge_reranker_load_attempted = True
    adapter._bge_reranker = Reranker()
    result = adapter.recall("负责项目甲的人研究什么？", RecallPolicy(final_min_score=0.144))
    assert adapter._bge_reranker.texts == (bridge.text, endpoint.text)
    assert result.evidence == ()
    assert result.safe_error_code is None and result.truncated


@pytest.mark.parametrize("fit_behavior", ["missing", "reject", "error"])
def test_unverified_token_fit_uses_original_single_fact_score(fit_behavior):
    from adapter.magma_adapter import _score_candidates
    bridge, endpoint = candidates()
    class Scorer:
        texts = ()
        def score(self, query, texts):
            self.texts = tuple(texts)
            return (0.01, 0.9)
    scorer = Scorer()
    if fit_behavior == "reject":
        scorer.fits_pair = lambda query, text: False
    elif fit_behavior == "error":
        def broken(query, text):
            raise ValueError("tokenizer unavailable")
        scorer.fits_pair = broken
    assert _score_candidates(scorer, "query", None, ((0, bridge), (1, endpoint))) == (0.01, 0.9)
    assert scorer.texts == (bridge.text, endpoint.text)


def test_token_fit_checks_actual_same_entity_marked_pair():
    from adapter.magma_adapter import _score_candidates
    from adapter.user_self import entity_marked_text
    bridge, endpoint = candidates()
    class Scorer:
        fit_calls = []
        score_calls = []
        def fits_pair(self, query, text):
            self.fit_calls.append((query, text))
            return False
        def score(self, query, texts):
            self.score_calls.append((query, tuple(texts)))
            return tuple(0.1 for _ in texts)
    scorer = Scorer()
    _score_candidates(scorer, "query", "person", ((0, bridge), (1, endpoint)))
    assert scorer.fit_calls == [(entity_marked_text("person", "query"),
                                 entity_marked_text("person", bridge.text + "\n" + endpoint.text))]
    assert (entity_marked_text("person", "query"), (entity_marked_text("person", endpoint.text),)) in scorer.score_calls


@pytest.fixture(scope="module")
def cached_bge_tokenizer():
    from recall.bge_reranker import BGE_MODEL, BGE_REVISION
    transformers = pytest.importorskip("transformers")
    try:
        return transformers.AutoTokenizer.from_pretrained(
            BGE_MODEL, revision=BGE_REVISION, local_files_only=True,
        )
    except OSError:
        pytest.skip("the pinned BGE tokenizer must already be cached")


@pytest.mark.parametrize("long_bridge", [False, True])
def test_cached_tokenizer_guards_complete_bridge_and_endpoint(cached_bge_tokenizer, long_bridge):
    from adapter.magma_adapter import _score_candidates
    from recall.bge_reranker import BgeReranker, BGE_MAX_LENGTH
    bridge, endpoint = candidates()
    query = "负责项目甲的人研究什么？"
    if long_bridge:
        bridge = replace(bridge, text=bridge.text[:-1] + ", id=" +
                         ",".join(str(i).zfill(4) for i in range(350)) + ".")
        assert len(bridge.text) < 2000
        # Reproduce the actual failure, not a character-count approximation:
        # fixed-window BGE cannot distinguish opposite unseen endpoints.
        positive = endpoint.text.replace("并不", "")
        def truncated(tail):
            return cached_bge_tokenizer(query, bridge.text + "\n" + tail,
                                        truncation=True, max_length=BGE_MAX_LENGTH)["input_ids"]
        assert truncated(positive) == truncated(endpoint.text)
        assert "材料乙" not in cached_bge_tokenizer.decode(truncated(endpoint.text))
    scorer = BgeReranker.__new__(BgeReranker)
    scorer._tokenizer = cached_bge_tokenizer
    scored_texts = []
    def record_score(_query, texts):
        scored_texts.extend(texts)
        return tuple(0.1 for _ in texts)
    scorer.score = record_score
    combined = bridge.text + "\n" + endpoint.text
    assert scorer.fits_pair(query, combined) is (not long_bridge)
    _score_candidates(scorer, query, None, ((0, bridge), (1, endpoint)))
    assert scored_texts == [bridge.text, endpoint.text if long_bridge else combined]
