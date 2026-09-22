"""Synthetic contracts for the explicit first-hit public read."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from Conversation_Memory.adapter._associative_recall import Activation
from Conversation_Memory.adapter.first_hit import FirstHitPolicy
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import (
    BackendCandidate, RecallPolicy, SourceMemoryContext, SourceExcerpt, SourceProvenance,
)
from Conversation_Memory.ingestion.state_store import IngestionStateStore


def candidate(eid, text, role="user", refs=None):
    provenance = dict(segment_id="segment", conversation_id="synthetic", turn_id=eid,
                      source_role=role, source_timestamp="2026-09-16T00:00:00+00:00",
                      source_timezone="UTC", ingestion_version="grounded-formation-v2",
                      timezone_source="client")
    metadata = dict(evidence_id=eid, provenance=provenance, source_start=0, source_end=len(text))
    if refs is not None:
        metadata["source_refs"] = refs
    return BackendCandidate(text, provenance["source_timestamp"], None, metadata)


def memory(tmp_path, rows, **kwargs):
    adapter = MagmaMemoryAdapter(SimpleNamespace(), IngestionStateStore(tmp_path / "state"),
                                ingestion_version="grounded-formation-v2",
                                first_hit=FirstHitPolicy(), **kwargs)
    adapter._activate_first_hit = lambda cue: Activation(tuple(rows), diagnostics={"delta": .42})
    adapter._get_bge_reranker = lambda: pytest.fail("first-hit must not run BGE")
    adapter.backend.recall = lambda *args: pytest.fail("first-hit must not run old Recall")
    return adapter


def test_order_whole_facts_and_private_diagnostics(tmp_path):
    bridge = candidate("bridge", "An intermediate fact.")
    a, b = candidate("a", "The earlier claim is uncertain."), candidate("b", "The later claim is false.", "assistant")
    adapter = memory(tmp_path, [(bridge, .9, 0), (b, .7, .3), (a, .7, .3)])
    result = adapter.recall_associative("claim", RecallPolicy(max_chars=2000))
    assert [e.evidence_id for e in result.facts.evidence] == ["a", "b"]
    assert "bridge" not in result.rendered_text
    assert "[USER]" in result.rendered_text and "[LUMINA]" in result.rendered_text
    public = str(asdict(result))
    assert all(key not in public for key in ["delta", "attention", "graph_version"])
    assert adapter._last_first_hit_diagnostics == {"delta": .42}
    small = adapter.recall_associative("claim", RecallPolicy(max_chars=12))
    assert not small.facts.evidence and small.truncated


def test_old_floor_is_an_explicit_conflict(tmp_path):
    adapter = memory(tmp_path, [])
    for floor in (0., .144):
        result = adapter.recall_associative("query", RecallPolicy(final_min_score=floor))
        assert result.safe_error_code == "first_hit_score_policy_conflict"
    plain = MagmaMemoryAdapter(SimpleNamespace(), IngestionStateStore(tmp_path / "other"))
    assert plain.recall_associative("query").safe_error_code == "first_hit_not_configured"


def test_sources_receive_exact_selected_refs_and_share_budget(tmp_path):
    first = candidate("a", "A supported fact.", refs=[dict(
        turn_id="supporting-turn", source_start=7, source_end=21,
        supporting_span="exact original", source_role="assistant",
        source_timestamp="2026-09-15T00:00:00+00:00", source_timezone="UTC",
        timezone_source="client")])
    ignored = candidate("b", "Unselected fact.")
    calls = []

    class Cold:
        def read_source_refs(self, refs, **kwargs):
            calls.append((refs, kwargs))
            return SourceMemoryContext(kwargs["query"], rendered_text="raw excerpt")

    adapter = memory(tmp_path, [(ignored, .2, 0), (first, .9, .8)], cold_store=Cold())
    result = adapter.recall_associative("cue", RecallPolicy(max_chars=100), include_sources=True,
                                        source_context_turns=1)
    refs, kwargs = calls[0]
    assert len(refs) == 1 and refs[0]["turn_id"] == "supporting-turn"
    assert refs[0]["source_role"] == "assistant" and refs[0]["source_start"] == 7
    assert refs[0]["segment_id"] == "segment" and refs[0]["conversation_id"] == "synthetic"
    assert kwargs["max_chars"] == 100 - len(result.facts.rendered_text) - 1
    assert kwargs["before"] == kwargs["after"] == 1
    assert len(result.rendered_text) <= 100


def test_source_missing_does_not_erase_fact_and_not_read_unless_requested(tmp_path):
    adapter = memory(tmp_path, [(candidate("a", "A fact."), 1, 1)])
    assert adapter.recall_associative("cue").safe_error_code is None
    expanded = adapter.recall_associative("cue", include_sources=True)
    assert expanded.facts.evidence and expanded.safe_error_code == "cold_source_unavailable"


def test_numerical_failure_keeps_seed_fact_with_visible_status(tmp_path, monkeypatch):
    from Conversation_Memory.adapter import _associative_recall as module
    backend = SimpleNamespace(resolve_target_entity_refs=lambda *a, **k: (),
                              first_hit_candidate=lambda node: candidate("seed", "A source fact."))
    adapter = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "state"),
                                ingestion_version="grounded-formation-v2", first_hit=FirstHitPolicy())
    monkeypatch.setattr(module, "find_recall_seeds", lambda *a, **k: ((("node", .2),), {}))
    backend.first_hit_view = lambda: (_ for _ in ()).throw(ValueError("private path"))
    result = adapter.recall_associative("cue")
    assert result.facts.evidence[0].evidence_id == "seed"
    assert result.safe_error_code == "first_hit_unavailable"
    assert "private path" not in str(asdict(result))


def test_explicit_empty_write_refs_do_not_infer_an_identity(tmp_path, monkeypatch):
    from Conversation_Memory.adapter import _associative_recall as module
    backend = SimpleNamespace(resolve_target_entity_refs=lambda *a, **k: pytest.fail("write identity inference"))
    adapter = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "state"),
                                ingestion_version="grounded-formation-v2", first_hit=FirstHitPolicy())
    calls = []
    def seeds(*args, **kwargs):
        calls.append(kwargs["target_entity_refs"])
        return (), {}
    monkeypatch.setattr(module, "find_recall_seeds", seeds)
    result = adapter._activate_first_hit("I measured it", target_entity_refs=())
    assert calls == [()]


def test_recent_raw_entry_is_explicit_and_bounded(tmp_path):
    calls = []
    class Cold:
        def search_recent_sources(self, cue, **kwargs):
            calls.append((cue, kwargs))
            return SourceMemoryContext(cue)
    adapter = memory(tmp_path, [], cold_store=Cold())
    adapter.recall_associative("cue")
    assert calls == []
    assert adapter.recall_recent_sources("no fact", RecallPolicy(max_chars=27)).safe_error_code is None
    assert calls[0][1]["max_chars"] == 27


@pytest.mark.parametrize("adapter_namespace,policy_namespace", [
    ("adapter", "Conversation_Memory.adapter"),
    ("Conversation_Memory.adapter", "adapter"),
])
def test_first_hit_policy_accepts_the_existing_memory_namespace_alias(tmp_path, monkeypatch, adapter_namespace, policy_namespace):
    from importlib import import_module
    from pathlib import Path

    # Historical callers explicitly added the organ to PYTHONPATH. Keep that
    # compatibility local to this check; ordinary imports use the package name.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    adapter_type = import_module(adapter_namespace + ".magma_adapter").MagmaMemoryAdapter
    policy_type = import_module(policy_namespace + ".first_hit").FirstHitPolicy
    supplied = policy_type(decay=0.5, max_nodes=17, max_links=2)
    adapter = adapter_type(SimpleNamespace(), IngestionStateStore(tmp_path / "alias-state"),
                           ingestion_version="grounded-formation-v2", first_hit=supplied)
    assert asdict(adapter.first_hit) == asdict(supplied)


@pytest.mark.parametrize("channel", [
    "dense_unavailable", "lexical_unavailable", "entity_unavailable", "entity_membership_unavailable",
])
def test_partial_seed_channels_keep_facts_but_report_unavailable(tmp_path, monkeypatch, channel):
    from Conversation_Memory.adapter import _associative_recall as module
    from Conversation_Memory.adapter import first_hit

    class View:
        version = 1
        def check(self, version):
            assert version == self.version
        def eligible(self, node):
            return True

    backend = SimpleNamespace(
        first_hit_view=lambda: View(), resolve_target_entity_refs=lambda *a, **k: (),
        first_hit_candidate=lambda node: candidate("seed", "A surviving source fact."),
    )
    adapter = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "partial-state"),
                                ingestion_version="grounded-formation-v2", first_hit=FirstHitPolicy())
    monkeypatch.setattr(module, "find_recall_seeds", lambda *a, **k: (
        (("node", 0.2),), {channel: True}))
    monkeypatch.setattr(first_hit, "discover_first_hit", lambda *a, **k: SimpleNamespace(
        fact_ids=("node",), h={"node": 1.0}, attention={"node": 1.0}, stats={}, delta=0.0))
    activation = adapter._activate_first_hit("cue", target_entity_refs=())
    assert activation.safe_error_code == "first_hit_seed_channel_unavailable"
    assert activation.candidates[0][0].metadata["evidence_id"] == "seed"
    result = adapter.recall_associative("cue")
    assert result.facts.evidence[0].evidence_id == "seed"
    assert result.safe_error_code == "first_hit_seed_channel_unavailable"
    assert channel not in str(asdict(result))


def test_seed_search_cannot_mix_a_changed_graph_snapshot(tmp_path, monkeypatch):
    from Conversation_Memory.adapter import _associative_recall as module
    from Conversation_Memory.adapter import first_hit

    class View:
        version = 1
        def check(self, version):
            if version != self.version:
                raise ValueError("snapshot changed")
        def eligible(self, node):
            return True

    view = View()
    backend = SimpleNamespace(
        first_hit_view=lambda: view, resolve_target_entity_refs=lambda *a, **k: (),
        first_hit_candidate=lambda node: candidate("seed", "A source fact."),
    )
    adapter = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "snapshot-state"),
                                ingestion_version="grounded-formation-v2", first_hit=FirstHitPolicy())
    def seeds(*args, **kwargs):
        view.version += 1
        return (("node", 0.2),), {}
    monkeypatch.setattr(module, "find_recall_seeds", seeds)
    monkeypatch.setattr(first_hit, "discover_first_hit", lambda *a, **k: pytest.fail("mixed snapshot solve"))
    result = adapter.recall_associative("cue")
    assert result.safe_error_code == "first_hit_unavailable"
    assert result.facts.evidence[0].evidence_id == "seed"


@pytest.mark.parametrize("policy", [FirstHitPolicy(attention_budget=0),
                                    FirstHitPolicy(attention_penalty=2)])
def test_seed_fallback_still_respects_final_attention_policy(tmp_path, monkeypatch, policy):
    from Conversation_Memory.adapter import _associative_recall as module
    backend = SimpleNamespace(first_hit_candidate=lambda node: candidate("seed", "A source fact."))
    adapter = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "state"),
                                ingestion_version="grounded-formation-v2", first_hit=policy)
    monkeypatch.setattr(module, "find_recall_seeds", lambda *a, **k: ((("node", .2),), {}))
    backend.first_hit_view = lambda: (_ for _ in ()).throw(ValueError("snapshot unavailable"))
    result = adapter.recall_associative("cue")
    assert not result.facts.evidence
    assert result.safe_error_code == "first_hit_unavailable"
