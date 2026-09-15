"""Query-time dense failure preserves healthy, bounded lexical source access."""
from copy import deepcopy

import faiss
import numpy as np
import pytest

from Conversation_Memory.adapter import _source_backend as source
from Conversation_Memory.tests.test_source_backend_views import segment
from Conversation_Memory.tests.test_source_experiences import assert_literal, memory, policy
from Conversation_Memory.adapter.source_reader import SourceReadLimits


def failure(*args, **kwargs):
    raise OSError("synthetic private provider path must not escape")


def sample(tmp_path):
    original = segment(texts=[
        ("user", "Keep the amber card private until I approve it."),
        ("assistant", "Could the amber card be shown tomorrow?"),
        ("user", "No approval has been given."),
    ])
    obj = memory(tmp_path, original)
    owner = obj.backend.owner
    owner.trg.graph_db.read_only = owner.trg.vector_db.read_only = True
    return obj, original, owner


@pytest.mark.parametrize("stage", ["enrich", "tokenize", "encode", "search"])
def test_dense_runtime_failure_preserves_lexical_source_and_experience(tmp_path, monkeypatch, stage):
    obj, original, owner = sample(tmp_path)
    target = {
        "enrich": (owner.trg.keyword_enricher, "enrich_query"),
        "tokenize": (owner.trg.encoder.model, "tokenizer"),
        "encode": (owner.trg.encoder, "encode"),
        "search": (owner.trg.vector_db.index, "search"),
    }
    monkeypatch.setattr(*target[stage], failure)
    graph = deepcopy(owner.trg.graph_db.nodes)
    vectors = faiss.serialize_index(owner.trg.vector_db.index).copy()
    mappings = deepcopy((owner.trg.vector_db.id_to_index, owner.trg.vector_db.index_to_id))
    for read in (obj.recall_sources, obj.recall_experiences):
        context = read("amber card", policy())
        assert context.evidence and context.truncated
        assert context.safe_error_code == "source_dense_unavailable"
        assert_literal(context, [original])
        assert "private provider path" not in repr(context)
        assert owner.last_source_stats["embedding_calls"] == int(stage in {"encode", "search"})
        assert owner.last_source_stats["dense_candidates"] == 0
        assert owner.last_source_stats["lexical_candidates"] > 0
        assert len(context.rendered_text) <= policy().max_chars
    assert owner.trg.graph_db.nodes == graph
    np.testing.assert_array_equal(faiss.serialize_index(owner.trg.vector_db.index), vectors)
    assert (owner.trg.vector_db.id_to_index, owner.trg.vector_db.index_to_id) == mappings
    assert obj.backend.fact_reads == 0


def test_empty_partial_result_is_not_empty_success_and_next_read_recovers(tmp_path, monkeypatch):
    obj, original, owner = sample(tmp_path)
    encode = owner.trg.encoder.encode
    with monkeypatch.context() as patch:
        patch.setattr(owner.trg.encoder, "encode", failure)
        patch.setattr(owner._source_lexical, "rank", lambda **kwargs: [])
        for read in (obj.recall_sources, obj.recall_experiences):
            context = read("amber card", policy())
            assert not context.evidence and context.truncated
            assert context.safe_error_code == "source_dense_unavailable"
    assert owner.trg.encoder.encode == encode
    healthy = obj.recall_experiences("amber card", policy())
    assert healthy.evidence and healthy.safe_error_code is None
    assert "dense_error_code" not in owner.last_source_stats
    assert_literal(healthy, [original])


def test_reader_partial_search_cache_preserves_error_and_literal_expansion(tmp_path, monkeypatch):
    obj, original, owner = sample(tmp_path)
    calls = []
    def broken(text):
        calls.append(text)
        failure()
    monkeypatch.setattr(owner.trg.encoder, "encode", broken)
    reader = obj.open_source_reader("What was approved?", SourceReadLimits())
    result = reader.search("amber card")
    assert result["error"] == "source_dense_unavailable" and result["sources"]
    before = (len(calls), len(obj._bge_reranker.scored), reader.node_reads)
    again = reader.search("  amber card  ")
    assert again["cached"] and again["error"] == result["error"]
    assert again["sources"] == result["sources"]
    assert (len(calls), len(obj._bge_reranker.scored), reader.node_reads) == before
    expanded = reader.read(original.segment_id, 0, 2)
    assert "error" not in expanded and expanded["requested_complete"]
    assert len(calls) == before[0]
    assert_literal(reader.context(), [original])
    assert "private provider path" not in repr(result)


@pytest.mark.parametrize("stage", ["stale", "mixed", "invalid_vector_view"])
def test_source_integrity_failures_do_not_enter_dense_recovery(tmp_path, monkeypatch, stage):
    obj, _, owner = sample(tmp_path)
    if stage == "stale":
        owner._source_view_signature = None
    elif stage == "mixed":
        owner._source_event_count = 1
    else:
        owner._source_view_error = "source_vector_mapping_invalid"
    monkeypatch.setattr(owner.trg.encoder, "encode", lambda *_: pytest.fail("integrity failure encoded"))
    monkeypatch.setattr(owner._source_lexical, "rank", lambda **_: pytest.fail("integrity failure searched"))
    context = obj.recall_experiences("amber card", policy())
    assert not context.evidence and context.safe_error_code == "source_recall_unavailable"


def test_failed_reranker_still_reports_unavailable_after_dense_fallback(tmp_path, monkeypatch):
    obj, _, owner = sample(tmp_path)
    monkeypatch.setattr(owner.trg.encoder, "encode", failure)
    monkeypatch.setattr(obj._bge_reranker, "score", failure)
    context = obj.recall_experiences("amber card", policy())
    assert not context.evidence and context.safe_error_code == "source_recall_unavailable"


def test_unrecognized_backend_diagnostic_is_not_a_public_error(tmp_path, monkeypatch):
    obj, _, owner = sample(tmp_path)
    original = obj.backend.source_candidates
    def with_private_diagnostic(*args):
        result = original(*args)
        owner.last_source_stats["dense_error_code"] = "private provider path"
        return result
    monkeypatch.setattr(obj.backend, "source_candidates", with_private_diagnostic)
    context = obj.recall_experiences("amber card", policy())
    assert context.evidence and context.safe_error_code is None
    assert "private provider path" not in repr(context)


@pytest.mark.parametrize("empty", [False, True])
def test_complete_source_context_retains_dense_status(tmp_path, monkeypatch, empty):
    obj, original, owner = sample(tmp_path)
    monkeypatch.setattr(owner.trg.encoder, "encode", failure)
    monkeypatch.setattr(obj.backend, "source_parent", lambda anchor, **kwargs:
                        source.parent(owner, anchor, **kwargs), raising=False)
    if empty:
        monkeypatch.setattr(owner._source_lexical, "rank", lambda **kwargs: [])
    context = obj.recall_source_context("amber card", policy())
    assert bool(context.evidence) is not empty
    assert context.safe_error_code == "source_dense_unavailable" and context.truncated
    assert_literal(context, [original])


def test_other_source_error_with_evidence_is_not_relabeled_as_dense_partial(tmp_path, monkeypatch):
    from dataclasses import replace
    obj, _, _ = sample(tmp_path)
    context = obj.recall_sources("amber card", policy())
    assert context.evidence
    monkeypatch.setattr(obj, "recall_sources", lambda *_:
                        replace(context, safe_error_code="source_recall_unavailable"))
    viewed = obj.recall_experiences("amber card", policy())
    assert not viewed.evidence and viewed.safe_error_code == "source_recall_unavailable"
