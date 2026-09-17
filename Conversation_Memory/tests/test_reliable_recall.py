"""Synthetic reliable-v1 contracts, with no app, provider, or neural load."""
from dataclasses import replace
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
                      source_timezone="UTC", ingestion_version="grounded-formation-v4", timezone_source="client")
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
                              associative_read_profile="reliable-v1")
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


def test_direct_seed_order_is_protected_from_graph_competition():
    rows = [(candidate("d"+str(i)), .02, 0) for i in range(5)]
    rows += [(candidate("a"+str(i)), .5, .2) for i in range(5)]
    adapter = memory(list(reversed(rows)), ("d3", "d1", "d4", "d0", "d2"))
    result = adapter.recall(policy=policy(max_chars=1000, max_evidence_items=5))
    assert [i.evidence_id for i in result.facts.evidence] == ["d3", "d1", "d4", "a0", "a1"]
    assert [s.channel for s in result.selections] == ["direct"]*3 + ["associated"]*2
    assert adapter.activation_calls == ["cue"]
    assert adapter._last_reliable_recall_diagnostics["candidate_outcomes"]["d3"]["protected_direct"]


@pytest.mark.parametrize("count,expected", [(1,["d0"]), (2,["d0","d1"]), (3,["d0","d1","a"])])
def test_small_k_rounds_towards_direct(count, expected):
    rows = [(candidate("d0"), .1, 0), (candidate("d1"), .1, 0), (candidate("a"), 1, 1)]
    result = memory(rows, ("d0","d1")).recall(policy=policy(max_evidence_items=count))
    assert [i.evidence_id for i in result.facts.evidence] == expected


def test_unused_quota_is_borrowed_without_replacing_base_facts():
    rows = [(candidate("a"+str(i)), .5, 0) for i in range(4)]
    result = memory(rows, ()).recall(policy=policy(max_evidence_items=4))
    assert len(result.facts.evidence) == 4
    assert all(s.channel == "associated" for s in result.selections)


def test_direct_whole_fact_that_exceeds_reserved_share_can_borrow():
    item = candidate("d", "x"*90)
    result = memory([(item,.1,0), (candidate("a","a"*300),1,1)], ("d",)).recall(
        policy=policy(max_chars=110, max_evidence_items=1))
    assert result.facts.evidence[0].text == "x"*90
    assert len(result.rendered_text) <= 110


def test_associated_attention_runs_only_within_nonseed_pool():
    from Conversation_Memory.adapter.first_hit import FirstHitPolicy
    rows = [(candidate("d"), 1, 1), (candidate("a"), .2, 0)]
    result = memory(rows, ("d",)).recall(policy=policy(max_evidence_items=3))
    assert [e.evidence_id for e in result.facts.evidence] == ["d","a"]
    adapter = memory(rows, ("d",), first_hit=FirstHitPolicy(attention_penalty=.3))
    assert len(adapter.recall().facts.evidence) == 1
    assert adapter._last_reliable_recall_diagnostics["candidate_outcomes"]["a"]["reason"] == "competition_zero"


def test_utf8_bytes_are_counted_without_cutting_a_fact():
    item = candidate("d", "\u754c"*20)
    adapter = memory([(item,1,1)], ("d",))
    result = adapter.recall(policy=policy(max_chars=100, max_bytes=40))
    assert not result.facts.evidence and result.truncated
    assert adapter._last_reliable_recall_diagnostics["candidate_outcomes"]["d"]["reason"] == "byte_budget"
    assert adapter.recall(policy=policy(max_chars=100)).facts.evidence


def test_optional_whole_turn_too_large_keeps_complete_fact():
    raw = source("raw", "x"*500, turn_id="t")
    cited = replace(raw, text="x"*10, source_end=10)
    cold = Cold(raw)
    fact = candidate("f", "A bounded complete statement.", refs=[ref(cited)])
    adapter = memory([(fact,1,1)], ("f",), cold=cold)
    result = adapter.recall(policy=policy(max_chars=100,max_evidence_items=2), include_sources=True,
                            source_context_turns=1)
    assert result.facts.evidence[0].text == fact.text
    assert not result.sources.evidence and result.selections[0].visible_representation == "fact"
    assert adapter._last_reliable_recall_diagnostics["source_outcomes"]["f"] == "source_char_budget"
    assert cold.calls[0][1]["whole_turns"] is True
    assert fact.text in result.rendered_text


def test_multi_turn_package_costs_each_actual_item_and_falls_back():
    a,b = source("s1","The proposal.",turn_id="t1"), source("s2","I agree once.",turn_id="t2",index=1)
    fact = candidate("f","User agreed once.", refs=[ref(a),ref(b)])
    adapter = memory([(fact,1,1)], ("f",), cold=Cold(a,b))
    result = adapter.recall(policy=policy(max_chars=1000,max_evidence_items=1), include_sources=True)
    assert not result.sources.evidence and result.facts.evidence
    assert adapter._last_reliable_recall_diagnostics["source_outcomes"]["f"] == "source_item_budget"
    assert result.selections[0].source_evidence_ids == ()


def test_shared_multi_turn_package_is_rendered_once_with_real_roles():
    a,b = source("s1","I propose one visit.",turn_id="t1",role="assistant"), source("s2","I accept once.",turn_id="t2",index=1)
    refs = [ref(a),ref(b)]
    facts = [candidate("f1","Lumina proposed one visit.",refs=refs), candidate("f2","User accepted once.",refs=refs)]
    cold=Cold(a,b); adapter=memory([(f,1,1) for f in facts],("f1","f2"),cold=cold)
    result=adapter.recall(policy=policy(max_chars=1000,max_evidence_items=2),include_sources=True)
    assert len(result.sources.evidence)==2 and len(cold.calls)==1 and len(cold.calls[0][0])==2
    assert all(s.visible_representation=="sources" for s in result.selections)
    assert all(s.source_evidence_ids==("s1","s2") for s in result.selections)
    assert result.rendered_text.count(a.text)==result.rendered_text.count(b.text)==1
    assert "LUMINA" in result.rendered_text and "USER" in result.rendered_text
    assert all(f.text not in result.rendered_text for f in facts)
    assert result.facts.rendered_text == ""
    assert adapter._last_reliable_recall_diagnostics["visible_items"]==2


def test_invalid_ref_cannot_be_validated_by_another_returned_range():
    raw=source("s1","Actual source",turn_id="t1")
    bad=ref(raw); bad["supporting_span"]="Invented text"
    fact=candidate("f","A full stored fact.",refs=[bad])
    result=memory([(fact,1,1)],("f",),cold=Cold(raw)).recall(include_sources=True)
    assert result.selections[0].visible_representation=="fact" and not result.sources.evidence
    assert result.safe_error_code=="cold_source_partial"


def test_disjoint_ranges_never_hide_unread_gaps():
    a=source("a","first",turn_id="t",start=0,turn_length=50)
    b=source("b","last",turn_id="t",start=46,turn_length=50)
    fact=candidate("f","Both ends are relevant.",refs=[ref(a),ref(b)])
    adapter=memory([(fact,1,1)],("f",),cold=Cold(a,b))
    result=adapter.recall(policy=policy(max_evidence_items=2),include_sources=True)
    assert len(result.sources.evidence)==2
    assert "chars=0:5" in result.rendered_text and "chars=46:50" in result.rendered_text
    assert "chars=0:50" not in result.rendered_text


def test_source_unavailable_is_explicit_without_an_optional_read_by_default():
    cold=Cold(error="cold_source_window_expired")
    item=candidate("f")
    adapter=memory([(item,1,1)],("f",),cold=cold)
    adapter.recall(); assert cold.calls==[]
    result=adapter.recall(include_sources=True)
    assert result.facts.evidence and result.safe_error_code=="cold_source_window_expired"
    assert result.sources.evidence==() and result.truncated


def test_owner_source_read_is_bounded_and_does_not_modify_cold(tmp_path):
    from core.cold_draft_store import ColdDraftStore
    cold=ColdDraftStore(tmp_path/"owner.jsonl",source_window_segments=4,source_window_bytes=32000)
    cold.append_segment([dict(turn_id="t",role="user",text="I accept only this visit.",
                             created_at="2026-09-17T00:00:00+00:00",source_timezone="UTC",timezone_source="client")],
                        segment_id="s")
    raw=source("expected","I accept only this visit.",turn_id="t")
    fact=candidate("f","User accepted only this visit.",refs=[ref(raw)])
    adapter=memory([(fact,1,1)],("f",),cold=cold)
    before=cold._path.read_bytes()
    result=adapter.recall(policy=policy(max_chars=200,max_evidence_items=1),include_sources=True)
    assert result.sources.evidence and result.sources.evidence[0].text==raw.text
    assert result.selections[0].visible_representation=="sources"
    assert len(result.rendered_text)<=200 and cold._path.read_bytes()==before
    assert "cold-draft:s" not in result.rendered_text


def test_seed_fact_ids_retain_search_order_without_extra_search(monkeypatch):
    from Conversation_Memory.adapter import _associative_recall as module
    from Conversation_Memory.adapter import first_hit
    class View:
        version=1
        def check(self, version): assert version==1
        def eligible(self, node): return True
    backend=SimpleNamespace(first_hit_view=lambda:View(), resolve_target_entity_refs=lambda *a,**k:(),
                            first_hit_candidate=lambda node:candidate(node))
    adapter=SimpleNamespace(backend=backend, first_hit=first_hit.FirstHitPolicy())
    calls=[]
    def seeds(*a,**k): calls.append(1); return (("d2",.2),("hub",.3),("d1",.1)),{}
    monkeypatch.setattr(module,"find_recall_seeds",seeds)
    monkeypatch.setattr(first_hit,"discover_first_hit",lambda *a,**k:SimpleNamespace(
        fact_ids=("a","d1","d2"),h={"a":.1,"d1":.2,"d2":.3},attention={"a":.1,"d1":.2,"d2":.3},stats={},delta=0))
    result=module.activate(adapter,"cue")
    assert result.seed_fact_ids==("d2","d1") and calls==[1]


def test_source_upgrade_cannot_displace_other_selected_direct_facts():
    raw=source("s1","large source "*30,turn_id="t")
    short=replace(raw,text="large source ",source_end=13)
    first=candidate("first","First exact statement.",refs=[ref(short)])
    second=candidate("second","Second exact statement.")
    adapter=memory([(first,.1,0),(second,.1,0)],("first","second"),cold=Cold(raw))
    result=adapter.recall(policy=policy(max_chars=120,max_evidence_items=2),include_sources=True,
                          source_context_turns=1)
    assert [e.evidence_id for e in result.facts.evidence]==["first","second"]
    assert first.text in result.rendered_text and second.text in result.rendered_text
    assert not result.sources.evidence


def test_legacy_profile_preserves_its_historical_selection_and_rendering():
    from Conversation_Memory.adapter._associative_recall import recall_associative
    rows=[(candidate("d"),.1,0),(candidate("a"),.8,.8)]
    adapter=memory(rows,("d",)); adapter.associative_read_profile="first-hit-v1"
    result=recall_associative(adapter,"cue",policy(max_bytes=1))
    assert [e.evidence_id for e in result.facts.evidence]==["a"]
    assert result.rendered_text=="[USER]\n"+rows[1][0].text
    assert result.selections==()


def test_fact_and_source_utf8_limit_share_one_visible_budget():
    raw=source("s","\u754c"*30,turn_id="t")
    fact=candidate("f","Supported fact.",refs=[ref(raw)])
    adapter=memory([(fact,1,1)],("f",),cold=Cold(raw))
    result=adapter.recall(policy=policy(max_chars=300,max_bytes=70),include_sources=True)
    assert result.selections[0].visible_representation=="fact"
    assert len(result.rendered_text.encode("utf-8"))<=70
    assert adapter._last_reliable_recall_diagnostics["source_outcomes"]["f"]=="source_byte_budget"
    assert adapter._last_reliable_recall_diagnostics["cold_read_attempts"]==1


def test_owner_exception_retains_facts_and_records_attempt():
    class Broken:
        def read_source_refs(self,*a,**k): raise OSError("private path")
    adapter=memory([(candidate("f"),1,1)],("f",),cold=Broken())
    result=adapter.recall(include_sources=True)
    assert result.facts.evidence and result.safe_error_code=="cold_source_unavailable"
    assert "private path" not in repr(result)
    assert adapter._last_reliable_recall_diagnostics["cold_read_attempts"]==1
    assert adapter._last_reliable_recall_diagnostics["cold_read_seconds"]>=0
