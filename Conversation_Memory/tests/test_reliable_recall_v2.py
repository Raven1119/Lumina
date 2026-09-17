"""Synthetic reliable-v2 presentation contracts, with no app, provider, or neural load.

Selection is identical to reliable-v1; these tests pin only the v2 presentation:
the canonical body is always visible and sources are a bounded supplement with
their own item allowance, sharing only the character/byte budget with bodies.
"""
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    # This precedes all production imports and protects relative-path defaults.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LUMINA_MIND_DECISION_LOG_PATH", str(tmp_path / "decisions.jsonl"))
    monkeypatch.setenv("LUMINA_DREAM_COLD_DRAFT_PATH", str(tmp_path / "cold.jsonl"))
    monkeypatch.setenv("LUMINA_DREAM_INGESTION_STATE_PATH", str(tmp_path / "ingestion.json"))
    monkeypatch.setenv("LUMINA_DREAM_MAGMA_PERSIST_DIR", str(tmp_path / "magma"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("network forbidden"))


def candidate(eid, text=None, *, role="user", refs=None):
    from Conversation_Memory.adapter.models import BackendCandidate
    text = text or (eid + " complete supported fact.")
    provenance = dict(segment_id="s", conversation_id="cold-draft:s", turn_id=eid,
                      source_role=role, source_timestamp="2026-09-17T00:00:00+00:00",
                      source_timezone="UTC", ingestion_version="grounded-formation-v5", timezone_source="client")
    metadata = dict(evidence_id=eid, provenance=provenance, source_start=0, source_end=len(text))
    if refs is not None:
        metadata["source_refs"] = refs
    return BackendCandidate(text, provenance["source_timestamp"], None, metadata)


def memory(rows, seeds, *, cold=None, first_hit=None):
    from Conversation_Memory.adapter._associative_recall import Activation, recall_associative
    from Conversation_Memory.adapter.first_hit import FirstHitPolicy
    from Conversation_Memory.adapter.models import RecallPolicy
    calls = []
    adapter = SimpleNamespace(first_hit=first_hit or FirstHitPolicy(), cold_store=cold,
                              associative_read_profile="reliable-v2")
    def activation(cue):
        calls.append(cue)
        return Activation(tuple(rows), diagnostics={}, seed_fact_ids=tuple(seeds))
    adapter._activate_first_hit = activation
    adapter.recall = lambda cue="cue", policy=None, **kwargs: recall_associative(
        adapter, cue, policy or RecallPolicy(), **kwargs)
    adapter.activation_calls = calls
    return adapter


def policy(**kwargs):
    from Conversation_Memory.adapter.models import RecallPolicy
    return RecallPolicy(**kwargs)


def source(eid, text, *, turn_id=None, role="user", start=0, turn_length=None, index=0):
    from Conversation_Memory.adapter.models import SourceExcerpt, SourceProvenance
    p = SourceProvenance(**candidate(turn_id or eid, role=role).metadata["provenance"])
    return SourceExcerpt(eid, text, p, index, start, start+len(text), turn_length or start+len(text))


def ref(item):
    p = item.provenance
    return dict(turn_id=p.turn_id, source_start=item.source_start, source_end=item.source_end,
                supporting_span=item.text, source_role=p.source_role,
                source_timestamp=p.source_timestamp, source_timezone=p.source_timezone,
                timezone_source=p.timezone_source)


class Cold:
    def __init__(self, *items, error=None):
        self.items, self.error, self.calls = items, error, []
    def read_source_refs(self, refs, **kwargs):
        from Conversation_Memory.adapter.models import SourceMemoryContext
        self.calls.append((refs, kwargs))
        return SourceMemoryContext(kwargs["query"], self.items, "\n".join(i.text for i in self.items),
                                   bool(self.error), self.error)


def test_body_stays_visible_next_to_its_source_supplement():
    raw = source("s1", "I accepted only that one visit.", turn_id="t1")
    fact = candidate("f", "User accepted only that one visit.", refs=[ref(raw)])
    adapter = memory([(fact, 1, 1)], ("f",), cold=Cold(raw))
    result = adapter.recall(policy=policy(max_chars=1000, max_evidence_items=1), include_sources=True)
    assert fact.text in result.rendered_text and raw.text in result.rendered_text
    assert fact.text in result.facts.rendered_text
    assert result.facts.rendered_text  # never the empty v1 replacement view
    assert result.selections[0].visible_representation == "fact+sources"
    assert result.selections[0].source_evidence_ids == ("s1",)
    assert "[M1 sources=S1]" in result.rendered_text
    assert adapter._last_reliable_recall_diagnostics["source_outcomes"]["f"] == "sources_visible"


def test_source_items_use_a_separate_allowance_at_fact_cap():
    # Five Facts fill the Fact cap; one Fact carries a two-item source package.
    # Structurally unaffordable in reliable-v1, shown in reliable-v2.
    a = source("s1", "The proposal.", turn_id="t1")
    b = source("s2", "I agree once.", turn_id="t2", index=1)
    rich = candidate("f0", "User agreed once.", refs=[ref(a), ref(b)])
    rows = [(rich, .02, 0)] + [(candidate("d"+str(i)), .02, 0) for i in range(1, 5)]
    seeds = ("f0", "d1", "d2", "d3", "d4")
    result = memory(rows, seeds, cold=Cold(a, b)).recall(
        policy=policy(max_chars=2000, max_evidence_items=5), include_sources=True)
    assert [i.evidence_id for i in result.facts.evidence] == list(seeds)
    assert all(row[0].text in result.rendered_text for row in rows)
    assert result.selections[0].visible_representation == "fact+sources"
    assert result.selections[0].source_evidence_ids == ("s1", "s2")
    legacy = memory(rows, seeds, cold=Cold(a, b))  # v1 contrast on the same fixture
    legacy.associative_read_profile = "reliable-v1"
    old = legacy.recall(policy=policy(max_chars=2000, max_evidence_items=5), include_sources=True)
    assert all(s.visible_representation == "fact" for s in old.selections)
    assert legacy._last_reliable_recall_diagnostics["source_outcomes"]["f0"] == "source_item_budget"
    assert a.text not in old.rendered_text

def test_source_allowance_exhaustion_falls_back_to_body_only():
    group_a = [source("a"+str(i), "alpha turn %d." % i, turn_id="ta"+str(i), index=i) for i in range(3)]
    group_b = [source("b"+str(i), "beta turn %d." % i, turn_id="tb"+str(i), index=i) for i in range(3)]
    fa = candidate("fa", "Alpha combined statement.", refs=[ref(s) for s in group_a])
    fb = candidate("fb", "Beta combined statement.", refs=[ref(s) for s in group_b])
    cold = Cold(*group_a, *group_b)
    adapter = memory([(fa, .5, 0), (fb, .5, 0)], ("fa", "fb"), cold=cold)
    # 3 + 3 source items against a separate allowance of 5: the 6th item is rejected.
    result = adapter.recall(policy=policy(max_chars=4000, max_evidence_items=5), include_sources=True)
    outcomes = adapter._last_reliable_recall_diagnostics["source_outcomes"]
    assert outcomes["fa"] == "sources_visible"
    assert outcomes["fb"] == "source_item_budget"
    selections = {s.evidence_id: s for s in result.selections}
    assert selections["fa"].visible_representation == "fact+sources"
    assert selections["fb"].visible_representation == "fact"
    assert all(s.text in result.rendered_text for s in group_a)
    assert not any(s.text in result.rendered_text for s in group_b)
    assert fa.text in result.rendered_text and fb.text in result.rendered_text
    assert adapter._last_reliable_recall_diagnostics["visible_source_items"] == 3
    assert result.sources.truncated and result.sources.safe_error_code == "cold_source_partial"


def test_shared_char_budget_rejects_supplement_but_keeps_complete_body():
    raw = source("s1", "x"*200, turn_id="t1")
    fact = candidate("f", "A bounded complete statement.", refs=[ref(raw)])
    adapter = memory([(fact, 1, 1)], ("f",), cold=Cold(raw))
    result = adapter.recall(policy=policy(max_chars=120, max_evidence_items=2), include_sources=True)
    assert [i.evidence_id for i in result.facts.evidence] == ["f"]
    assert fact.text in result.rendered_text and raw.text not in result.rendered_text
    assert not result.sources.evidence
    assert result.selections[0].visible_representation == "fact"
    assert adapter._last_reliable_recall_diagnostics["source_outcomes"]["f"] == "source_char_budget"


def test_shared_package_prints_once_and_counts_once_for_two_facts():
    a = source("s1", "I propose one visit.", turn_id="t1", role="assistant")
    b = source("s2", "I accept once.", turn_id="t2", index=1)
    refs = [ref(a), ref(b)]
    facts = [candidate("f1", "Lumina proposed one visit.", refs=refs),
             candidate("f2", "User accepted once.", refs=refs)]
    adapter = memory([(f, 1, 1) for f in facts], ("f1", "f2"), cold=Cold(a, b))
    result = adapter.recall(policy=policy(max_chars=1000, max_evidence_items=2), include_sources=True)
    assert result.rendered_text.count(a.text) == result.rendered_text.count(b.text) == 1
    assert all(f.text in result.rendered_text for f in facts)
    assert all(s.visible_representation == "fact+sources" for s in result.selections)
    assert all(s.source_evidence_ids == ("s1", "s2") for s in result.selections)
    diagnostics = adapter._last_reliable_recall_diagnostics
    assert diagnostics["visible_source_items"] == 2  # not double-counted per Fact
    assert diagnostics["visible_items"] == 4  # bodies plus the shared ranges, once
    assert len(result.sources.evidence) == 2 and len(adapter.cold_store.calls) == 1


def test_selections_and_diagnostics_record_the_v2_profile():
    raw = source("s1", "exact source", turn_id="t1")
    rich = candidate("f1", "Supported statement.", refs=[ref(raw)])
    plain = candidate("f2", "Unsupported second statement.", refs=[])
    adapter = memory([(rich, .5, 0), (plain, .5, 0)], ("f1", "f2"), cold=Cold(raw))
    result = adapter.recall(policy=policy(max_chars=1000, max_evidence_items=3), include_sources=True)
    reps = {s.evidence_id: s.visible_representation for s in result.selections}
    assert reps == {"f1": "fact+sources", "f2": "fact"}
    diagnostics = adapter._last_reliable_recall_diagnostics
    assert diagnostics["profile"] == "reliable-v2"
    assert diagnostics["source_reserved_items"] == 3
    assert diagnostics["source_outcomes"]["f1"] == "sources_visible"
    assert diagnostics["source_outcomes"]["f2"] == "source_support_incomplete"


def test_without_sources_the_render_matches_reliable_v1():
    raw = source("s1", "exact source", turn_id="t1")
    rows = [(candidate("f1", "Supported.", refs=[ref(raw)]), .5, 0), (candidate("f2"), .5, 0)]
    cold = Cold(raw)
    adapter = memory(rows, ("f1", "f2"), cold=cold)
    result = adapter.recall(policy=policy(max_chars=1000, max_evidence_items=3))
    assert cold.calls == []  # include_sources=False performs no source read
    legacy = memory(rows, ("f1", "f2"), cold=Cold(raw))
    legacy.associative_read_profile = "reliable-v1"
    old = legacy.recall(policy=policy(max_chars=1000, max_evidence_items=3))
    assert result.rendered_text == old.rendered_text
    assert result.facts.rendered_text == old.facts.rendered_text
    assert adapter._last_reliable_recall_diagnostics["profile"] == "reliable-v2"


def test_facade_accepts_reliable_v2_profile(tmp_path):
    from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
    from Conversation_Memory.adapter.first_hit import FirstHitPolicy
    from Conversation_Memory.ingestion.state_store import IngestionStateStore
    adapter = MagmaMemoryAdapter(SimpleNamespace(), IngestionStateStore(tmp_path/"state"),
                                 ingestion_version="grounded-formation-v5", formation_model=object(),
                                 first_hit=FirstHitPolicy(), associative_read_profile="reliable-v2")
    assert adapter.associative_read_profile == "reliable-v2"
    with pytest.raises(ValueError, match="reliable_read_requires_first_hit"):
        MagmaMemoryAdapter(SimpleNamespace(), IngestionStateStore(tmp_path/"bare"),
                           ingestion_version="grounded-formation-v5",
                           associative_read_profile="reliable-v2")


def test_dream_reliable_cli_maps_to_reliable_v2(monkeypatch, capsys):
    import Dream.runner as runner
    calls = []
    model = SimpleNamespace(client_kind="model")
    monkeypatch.setattr(runner, "build_formation_model_client", lambda: model)
    def build(injected, **kwargs):
        assert injected is model
        calls.append(kwargs)
        return SimpleNamespace(run_once=lambda policy: runner.DreamRunReport.from_results(()))
    monkeypatch.setattr(runner, "build_default_runner", build)
    assert runner.main(["--reliable-memory"]) == 0
    assert calls[0]["associative_read_profile"] == "reliable-v2"
    capsys.readouterr()
