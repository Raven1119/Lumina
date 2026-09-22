"""Query-driven public Recall contracts on isolated graphs; no model calls.

These checks establish binding, packing and bounded-entry behavior, not
natural-language parsing accuracy or retrieval benefit on stored memories.
"""
from types import SimpleNamespace
from datetime import datetime

import numpy as np
import pytest

from Conversation_Memory.adapter import _query_graph_read as query_read
from Conversation_Memory.adapter._anchor_fusion import _query_features
from Conversation_Memory.adapter._reliable_recall import pack_reliable
from Conversation_Memory.adapter.first_hit import FirstHitPolicy
from Conversation_Memory.adapter.graph_read_query import (
    GraphReadQuery, QueryClue, QueryIntent, QueryRelation, query_source,
)
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.ingestion.state_store import IngestionStateStore
from Conversation_Memory.tests.test_first_hit_read import View
from Conversation_Memory.tests.test_reliable_recall_dispatch import candidate


def relation_fact(eid, text, subject, relation, value, subject_ref, object_ref=None):
    fact = candidate(eid, text)
    fact.metadata.update(subject=subject, relation=relation, value=value,
                         subject_entity_ref=subject_ref)
    if object_ref is not None:
        fact.metadata["object_entity_ref"] = object_ref
    return fact


def query_adapter(tmp_path, monkeypatch, *, rows=None, seeds=None, facts=None,
                  roles=None, identities=None, search_rows=None, policy=None):
    """Return an actual graph-read-v2 facade and indexed-search call receipts.

    ``search_rows`` optionally maps exact input text to seed rows (or is a
    callable). The default two Fact fixture also supports the real Chat test.
    Only indexed seed lookup is mocked; graph discovery, role qualification,
    joins and canonical evidence packing execute their maintained code.
    """
    if facts is None:
        facts = {
            "use": relation_fact("use", "我用录音机 R1。", "我", "use",
                                 "录音机 R1", "E_001", "RECORDER_1"),
            "mass": relation_fact("mass", "录音机 R1 的质量是 240 g。", "录音机 R1",
                                  "mass", "240 g", "RECORDER_1"),
        }
        # Exact persisted mention surfaces are separate from display bodies.
        facts["use"].metadata["object_entity_surface"] = "录音机"
        facts["mass"].metadata["subject_entity_surface"] = "录音机"
    if rows is None:
        rows = {"entity:e_001": [("use", 1)], "use": [("entity:recorder_1", 1)],
                "entity:recorder_1": [("use", 1), ("mass", 1)], "mass": []}
    if seeds is None:
        seeds = {"use": 1}
    if roles is None:
        roles = {(node, fact.metadata[role + "_entity_ref"]): (role,)
                 for node, fact in facts.items() for role in ("subject", "object")
                 if fact.metadata.get(role + "_entity_ref")}
    if identities is None:
        identities = {"录音机": ("RECORDER_1",), "录音机 R1": ("RECORDER_1",)}
    view = View(rows, facts=facts)
    view.entity_roles = lambda node, ref: frozenset(roles.get((node, ref), ()))
    backend = SimpleNamespace(
        first_hit_view=lambda: view,
        first_hit_candidate=lambda node: facts.get(node),
        resolve_target_entity_refs=lambda text, limit: tuple(dict.fromkeys(
            ref for surface, refs in identities.items() if surface in text for ref in refs))[:limit],
        _matching_surfaces=lambda text: (surface for surface in identities if surface in text),
        find_entity_candidates=lambda surface, limit: tuple(
            SimpleNamespace(entity_ref=ref) for ref in identities.get(surface, ())[:limit]),
        _lexical_index=SimpleNamespace(node_features={
            node: frozenset(_query_features(fact.text)) for node, fact in facts.items()}),
    )
    calls = []

    def find(backend, text, policy, **kwargs):
        calls.append((text, kwargs))
        found = search_rows(text) if callable(search_rows) else (
            search_rows.get(text, ()) if search_rows is not None else seeds)
        return tuple(found.items() if isinstance(found, dict) else found), {}

    monkeypatch.setattr(query_read, "find_recall_seeds", find)
    adapter = MagmaMemoryAdapter(
        backend, IngestionStateStore(tmp_path / "never-written.json"),
        ingestion_version="grounded-formation-v6", first_hit=policy or FirstHitPolicy(),
        associative_read_profile="graph-read-v2",
    )
    return adapter, calls


def recorder_query(*, topic=True, unresolved=()):
    text = "我用的录音机多重？"
    source = (query_source(-1, text, 0, text, ()),)
    clues = (QueryClue("c1", "我", "current_user", source),)
    if topic:
        clues += (QueryClue("c2", "录音机", "topic", source),)
    return GraphReadQuery(text, intent=QueryIntent("precise", clues, (
        QueryRelation("c1", "用", "?entity", source),
        QueryRelation("?entity", "重", "?value", source),
    ), unresolved))


def _recall(adapter, request, *, items=5, chars=3000, bytes_limit=None):
    return adapter.recall_associative(request, RecallPolicy(
        max_evidence_items=items, max_chars=chars, max_bytes=bytes_limit))


def test_same_entity_join_returns_both_canonical_facts_without_synthesizing_answer(tmp_path, monkeypatch):
    adapter, _ = query_adapter(tmp_path, monkeypatch)
    result = _recall(adapter, recorder_query())
    assert {fact.evidence_id for fact in result.facts.evidence} == {"use", "mass"}
    assert "我用录音机 R1。" in result.rendered_text
    assert "录音机 R1 的质量是 240 g。" in result.rendered_text
    assert adapter._last_first_hit_diagnostics["evidence_complete"]
    assert adapter._last_first_hit_diagnostics["visible_bundles"] == (("use", "mass"),)
    assert "所以" not in result.rendered_text and "?entity" not in result.rendered_text
    assert not (tmp_path / "never-written.json").exists()


def test_different_entity_facts_cannot_complete_shared_variable(tmp_path, monkeypatch):
    adapter, _ = query_adapter(tmp_path, monkeypatch)
    fact = adapter.backend.first_hit_candidate("mass")
    fact.metadata["subject_entity_ref"] = "OTHER_RECORDER"
    view = adapter.backend.first_hit_view()
    original = view.entity_roles
    view.entity_roles = lambda node, ref: frozenset({"subject"}) if (
        node == "mass" and ref == "OTHER_RECORDER") else original(node, ref)
    result = _recall(adapter, recorder_query(topic=False))
    assert {fact.evidence_id for fact in result.facts.evidence} == {"use", "mass"}
    assert not adapter._last_first_hit_diagnostics["bundle_candidates"]
    assert not adapter._last_first_hit_diagnostics["evidence_complete"]
    assert result.safe_error_code == "graph_read_evidence_incomplete"


@pytest.mark.parametrize("role", ["subject", "object"])
def test_missing_persisted_role_is_not_inferred_from_metadata_or_reachability(tmp_path, monkeypatch, role):
    adapter, _ = query_adapter(tmp_path, monkeypatch)
    view = adapter.backend.first_hit_view()
    original = view.entity_roles
    missing = ("use", "E_001" if role == "subject" else "RECORDER_1")
    view.entity_roles = lambda node, ref: frozenset({"ordinary"}) if (node, ref) == missing else original(node, ref)
    result = _recall(adapter, recorder_query())
    assert "mass" in adapter._last_graph_read_snapshot.node_ids
    assert "use" not in {fact.evidence_id for fact in result.facts.evidence}
    assert result.safe_error_code == "graph_read_evidence_incomplete"
    assert not adapter._last_first_hit_diagnostics["evidence_complete"]


def test_literal_mass_requires_value_but_no_entity_or_object_edge(tmp_path, monkeypatch):
    adapter, _ = query_adapter(tmp_path, monkeypatch)
    result = _recall(adapter, recorder_query())
    assert adapter._last_first_hit_diagnostics["evidence_complete"]
    assert "240 g" in result.rendered_text
    assert "object_entity_ref" not in adapter.backend.first_hit_candidate("mass").metadata
    assert not any("240" in node for node in adapter._last_graph_read_snapshot.node_ids)
    adapter.backend.first_hit_candidate("mass").metadata["value"] = ""
    missing = _recall(adapter, recorder_query())
    assert missing.safe_error_code == "graph_read_evidence_incomplete"
    assert "mass" not in {fact.evidence_id for fact in missing.facts.evidence}


def test_same_name_alternatives_are_or_but_identity_remains_unresolved(tmp_path, monkeypatch):
    text = "Alex use recorder?"
    source = (query_source(-1, text, 0, text, ()),)
    request = GraphReadQuery(text, intent=QueryIntent("precise", (
        QueryClue("c1", "Alex", "name", source),), (
        QueryRelation("c1", "use", "?entity", source),)))
    fact = relation_fact("use", "Alex uses R1.", "Alex", "use", "R1", "A1", "R1")
    adapter, _ = query_adapter(tmp_path, monkeypatch,
        rows={"use": [], "entity:a1": [], "entity:a2": [], "entity:r1": []},
        facts={"use": fact}, identities={"Alex": ("A1", "A2")})
    result = _recall(adapter, request, items=1)
    assert [item.evidence_id for item in result.facts.evidence] == ["use"]
    assert adapter._last_first_hit_diagnostics["bundle_candidates"] == (("use",),)
    assert adapter._last_first_hit_diagnostics["ambiguous_clues"] == ("c1",)
    assert not adapter._last_first_hit_diagnostics["evidence_complete"]
    assert result.safe_error_code == "graph_read_evidence_incomplete"


def test_both_facts_must_fit_final_item_char_and_byte_budgets(tmp_path, monkeypatch):
    adapter, _ = query_adapter(tmp_path, monkeypatch)
    request = recorder_query()
    full = _recall(adapter, request, items=2)
    assert adapter._last_first_hit_diagnostics["evidence_complete"]
    exact = _recall(adapter, request, items=2, chars=len(full.rendered_text),
                    bytes_limit=len(full.rendered_text.encode("utf-8")))
    assert exact.rendered_text == full.rendered_text
    for limits in ({"items": 1}, {"chars": len(full.rendered_text) - 1},
                   {"bytes_limit": len(full.rendered_text.encode("utf-8")) - 1}):
        partial = _recall(adapter, request, **limits)
        assert len(partial.facts.evidence) < 2
        assert partial.safe_error_code == "graph_read_evidence_incomplete" and partial.truncated
        assert not adapter._last_first_hit_diagnostics["visible_bundles"]
        assert not adapter._last_first_hit_diagnostics["evidence_complete"]
        assert all(item.text in partial.rendered_text for item in partial.facts.evidence)


def test_bundle_cannot_replace_protected_direct_to_claim_completion(tmp_path, monkeypatch):
    protected = relation_fact("protected", "我用设备 P。", "我", "use", "设备 P", "E_001", "P")
    use = relation_fact("use", "我用录音机 R1。", "我", "use", "录音机 R1", "E_001", "RECORDER_1")
    mass = relation_fact("mass", "录音机 R1 的质量是 240 g。", "录音机 R1", "mass", "240 g", "RECORDER_1")
    adapter, _ = query_adapter(tmp_path, monkeypatch, facts={"protected": protected, "use": use, "mass": mass},
        seeds={"protected": .7, "use": .3}, rows={"protected": [], "use": [("mass", 1)],
            "mass": [], "entity:e_001": [], "entity:recorder_1": [], "entity:p": []})
    result = _recall(adapter, recorder_query(topic=False), items=2)
    assert {item.evidence_id for item in result.facts.evidence} == {"protected", "use"}
    assert result.safe_error_code == "graph_read_evidence_incomplete"
    outcomes = adapter._last_reliable_recall_diagnostics["candidate_outcomes"]
    assert outcomes["protected"]["protected_direct"] and outcomes["use"]["protected_direct"]
    assert adapter._last_reliable_recall_diagnostics["direct_share"] == .6


def test_full_and_seed_only_share_frozen_entries_without_retrieval_retries(tmp_path, monkeypatch):
    adapter, calls = query_adapter(tmp_path, monkeypatch)
    request = recorder_query()
    entries = query_read.freeze_query_entries(adapter, request)
    frozen_call_count = len(calls)
    baseline = query_read.activate_query_read(adapter, request, entries=entries, seed_only=True)
    b = adapter._last_graph_read_snapshot
    assert not adapter.backend.first_hit_view().reads and b.stats["edges_read"] == 0
    assert np.count_nonzero(b.P) == 0
    candidate_activation = query_read.activate_query_read(adapter, request, entries=entries)
    c = adapter._last_graph_read_snapshot
    assert b.seed_weights == c.seed_weights == dict(entries.seeds)
    assert b.group_seed_weights == c.group_seed_weights == entries.groups
    assert len(calls) == frozen_call_count
    assert {item.metadata["evidence_id"] for item, _, _ in baseline.candidates} == {"use"}
    assert {item.metadata["evidence_id"] for item, _, _ in candidate_activation.candidates} == {"use", "mass"}
    policy = RecallPolicy(max_chars=3000, max_evidence_items=2)
    adapter._last_first_hit_diagnostics = dict(baseline.diagnostics)
    b_visible = pack_reliable(adapter, request.text, policy, baseline, include_sources=False,
                              source_context_turns=0, profile="reliable-v2")
    adapter._last_first_hit_diagnostics = dict(candidate_activation.diagnostics)
    c_visible = pack_reliable(adapter, request.text, policy, candidate_activation, include_sources=False,
                              source_context_turns=0, profile="reliable-v2")
    assert b_visible.safe_error_code == "graph_read_evidence_incomplete"
    assert c_visible.safe_error_code is None and "240 g" in c_visible.rendered_text


def test_supplement_uses_only_question_clues_and_keeps_whole_question_anchors(tmp_path, monkeypatch):
    request = recorder_query()
    facts = {"anchor1": candidate("anchor1", "Unrelated anchor one."),
             "anchor2": candidate("anchor2", "Unrelated anchor two."),
             "use": relation_fact("use", "我用录音机 R1。", "我", "use", "录音机 R1", "E_001", "RECORDER_1"),
             "mass": relation_fact("mass", "录音机 R1 的质量是 240 g。", "录音机 R1", "mass", "240 g", "RECORDER_1")}
    rows = {node: [] for node in (*facts, "entity:e_001", "entity:recorder_1")}
    adapter, calls = query_adapter(tmp_path, monkeypatch, rows=rows, facts=facts,
        search_rows={request.text: {"anchor1": 1, "anchor2": .9}, "我 用": {"use": 1}, "重": {"mass": 1}})
    entries = query_read.freeze_query_entries(adapter, request)
    assert [text for text, _ in calls] == [request.text, "我 用", "重"]
    assert tuple(node for node, _ in entries.seeds)[:2] == ("anchor1", "anchor2")
    assert {node for node, _ in entries.seeds} == set(facts)
    assert all("240" not in text and "R1" not in text and "RECORDER_1" not in text for text, _ in calls)
    assert all(options["_candidate_buffer"] for _, options in calls)
    assert entries.groups["relation_0"] and entries.groups["relation_1"]


def test_three_searches_share_five_seeds_and_bounded_candidate_union(tmp_path, monkeypatch):
    text = "first second third"
    source = (query_source(-1, text, 0, text, ()),)
    request = GraphReadQuery(text, intent=QueryIntent("open", tuple(
        QueryClue(f"c{i+1}", word, "topic", source) for i, word in enumerate(text.split()))))
    facts = {f"f{i}": candidate(f"f{i}", f"Unrelated fact number {i}.") for i in range(60)}
    rows = {node: [] for node in facts}
    searches = {text: {f"f{i}": 1 for i in range(20)},
                "first": {f"f{i}": 1 for i in range(20, 40)},
                "second": {f"f{i}": 1 for i in range(40, 60)}}
    adapter, calls = query_adapter(tmp_path, monkeypatch, rows=rows, facts=facts,
        identities={}, search_rows=searches, policy=FirstHitPolicy(max_seeds=20, max_nodes=100, max_edges=1000))
    entries = query_read.freeze_query_entries(adapter, request)
    assert len(calls) == entries.diagnostics["entry_search_count"] == 3
    assert len(entries.seeds) == 5 and len(entries.groups) == 3
    assert entries.diagnostics["candidate_buffer_count"] == 60
    assert entries.diagnostics["whole_question_anchors"] == ("f0", "f1")
    assert entries.diagnostics["uncovered_seed_groups"] == ("c1", "c2", "c3")


def test_unresolved_condition_is_retained_and_never_reported_complete(tmp_path, monkeypatch):
    adapter, _ = query_adapter(tmp_path, monkeypatch)
    result = _recall(adapter, recorder_query(unresolved=("historical scope unknown",)))
    assert len(result.facts.evidence) == 2
    assert result.safe_error_code == "graph_read_evidence_incomplete"
    assert adapter._last_first_hit_diagnostics["unresolved_conditions"] == ("historical scope unknown",)
    assert not adapter._last_first_hit_diagnostics["evidence_complete"]


def test_corrupt_source_range_never_completes_join(tmp_path, monkeypatch):
    adapter, _ = query_adapter(tmp_path, monkeypatch)
    adapter.backend.first_hit_candidate("mass").metadata["source_end"] = 0
    result = _recall(adapter, recorder_query())
    assert result.safe_error_code == "graph_read_evidence_incomplete"
    assert [item.evidence_id for item in result.facts.evidence] == ["use"]


def test_extra_recorder_topic_cannot_be_satisfied_by_car_roles(tmp_path, monkeypatch):
    use = relation_fact("use", "我用汽车 C1。", "我", "use", "汽车 C1", "E_001", "CAR_1")
    mass = relation_fact("mass", "汽车 C1 的质量是 1200 kg。", "汽车 C1", "mass", "1200 kg", "CAR_1")
    adapter, _ = query_adapter(tmp_path, monkeypatch, facts={"use": use, "mass": mass}, identities={},
        rows={"use": [("mass", 1)], "mass": [], "entity:e_001": [], "entity:car_1": []})
    result = _recall(adapter, recorder_query())
    assert len(result.facts.evidence) == 2
    assert result.safe_error_code == "graph_read_evidence_incomplete"
    assert not adapter._last_first_hit_diagnostics["evidence_complete"]


@pytest.mark.parametrize("mass_time", [None, "2025"])
def test_incompatible_or_unaligned_historical_scope_stays_partial(tmp_path, monkeypatch, mass_time):
    adapter, _ = query_adapter(tmp_path, monkeypatch)
    adapter.backend.first_hit_candidate("use").metadata["referenced_time"] = "2023"
    adapter.backend.first_hit_candidate("mass").metadata["referenced_time"] = mass_time
    result = _recall(adapter, recorder_query())
    assert len(result.facts.evidence) == 2
    assert result.safe_error_code == "graph_read_evidence_incomplete"
    assert not adapter._last_first_hit_diagnostics["evidence_complete"]


def test_different_speaking_times_do_not_alone_imply_historical_scope_conflict(tmp_path, monkeypatch):
    adapter, _ = query_adapter(tmp_path, monkeypatch)
    adapter.backend.first_hit_candidate("mass").metadata["provenance"]["source_timestamp"] = "2026-09-18T00:00:00+00:00"
    result = _recall(adapter, recorder_query())
    assert len(result.facts.evidence) == 2
    assert adapter._last_first_hit_diagnostics["evidence_complete"]


def test_open_association_keeps_bridge_and_does_not_claim_precise_success(tmp_path, monkeypatch):
    adapter, _ = query_adapter(tmp_path, monkeypatch, roles={})
    request = GraphReadQuery("录音机有什么线索", intent=QueryIntent("open"))
    result = _recall(adapter, request)
    assert {item.evidence_id for item in result.facts.evidence} == {"use", "mass"}
    assert adapter._last_first_hit_diagnostics["result_status"] == "open_association"
    assert not adapter._last_first_hit_diagnostics["evidence_complete"]


def test_entry_failure_has_no_retry_or_public_private_payload(tmp_path, monkeypatch):
    adapter, calls = query_adapter(tmp_path, monkeypatch)
    def fail(*args, **kwargs):
        calls.append((args[1], kwargs))
        raise ValueError("private graph path and contents")
    monkeypatch.setattr(query_read, "find_recall_seeds", fail)
    result = _recall(adapter, recorder_query())
    assert len(calls) == 1 and not result.facts.evidence and not result.rendered_text
    assert result.safe_error_code == "graph_read_unavailable"
    assert "private" not in repr(result)


def test_surface_probe_overflow_cannot_hide_identity_ambiguity(tmp_path, monkeypatch):
    text = "A B C D E use recorder?"
    source = (query_source(-1, text, 0, text, ()),)
    request = GraphReadQuery(text, intent=QueryIntent("precise", (
        QueryClue("c1", "A B C D E", "name", source),), (
        QueryRelation("c1", "use", "?entity", source),)))
    fact = relation_fact("use", "A uses R1.", "A", "use", "R1", "A1", "R1")
    adapter, _ = query_adapter(tmp_path, monkeypatch,
        rows={"use": [], "entity:a1": [], "entity:a2": [], "entity:r1": []},
        facts={"use": fact}, identities={**dict.fromkeys("ABCD", ("A1",)), "E": ("A2",)})
    probed = []
    def surfaces(text):
        for name in "ABCDE":
            probed.append(name)
            yield name
        pytest.fail("identity surface lookup must stop after its bounded overflow witness")
    adapter.backend._matching_surfaces = surfaces
    result = _recall(adapter, request)
    assert len(probed) == 5
    assert adapter._last_first_hit_diagnostics["truncated_clues"] == ["c1"]
    assert result.safe_error_code == "graph_read_evidence_incomplete"
    assert not adapter._last_first_hit_diagnostics["evidence_complete"]


def test_existing_complete_seed_bundle_can_succeed_without_any_graph_read(tmp_path, monkeypatch):
    adapter, _ = query_adapter(tmp_path, monkeypatch, seeds={"use": .6, "mass": .4})
    request = recorder_query()
    entries = query_read.freeze_query_entries(adapter, request)
    activation = query_read.activate_query_read(adapter, request, entries=entries, seed_only=True)
    adapter._last_first_hit_diagnostics = dict(activation.diagnostics)
    result = pack_reliable(adapter, request.text, RecallPolicy(max_evidence_items=2), activation,
        include_sources=False, source_context_turns=0, profile="reliable-v2")
    assert adapter._last_first_hit_diagnostics["evidence_complete"]
    assert {item.evidence_id for item in result.facts.evidence} == {"use", "mass"}
    assert result.safe_error_code is None
    assert not adapter.backend.first_hit_view().reads


def test_fixed_literal_condition_rejects_a_different_measurement(tmp_path, monkeypatch):
    text = "录音机重240 g吗？"
    source = (query_source(-1, text, 0, text, ()),)
    request = GraphReadQuery(text, intent=QueryIntent("precise", (
        QueryClue("c1", "录音机", "name", source),
        QueryClue("c2", "240 g", "literal", source),), (
        QueryRelation("c1", "重", "c2", source),)))
    adapter, _ = query_adapter(tmp_path, monkeypatch, seeds={"mass": 1})
    matched = _recall(adapter, request)
    assert [item.evidence_id for item in matched.facts.evidence] == ["mass"]
    assert adapter._last_first_hit_diagnostics["evidence_complete"]
    adapter.backend.first_hit_candidate("mass").metadata["value"] = "300 g"
    unmatched = _recall(adapter, request)
    assert not unmatched.facts.evidence
    assert unmatched.safe_error_code == "graph_read_evidence_incomplete"


@pytest.mark.parametrize("negation", [{"negated": True}, {"polarity": "negative"}])
def test_explicit_negation_cannot_become_positive_join(tmp_path, monkeypatch, negation):
    adapter, _ = query_adapter(tmp_path, monkeypatch)
    adapter.backend.first_hit_candidate("use").metadata.update(negation)
    result = _recall(adapter, recorder_query())
    assert not adapter._last_first_hit_diagnostics["evidence_complete"]
    assert result.safe_error_code == "graph_read_evidence_incomplete"


def test_frozen_entries_cannot_cross_queries_or_graph_versions(tmp_path, monkeypatch):
    adapter, calls = query_adapter(tmp_path, monkeypatch)
    request = recorder_query()
    entries = query_read.freeze_query_entries(adapter, request)
    count = len(calls)
    other = recorder_query(unresolved=("ambiguous pronoun",))
    mismatch = query_read.activate_query_read(adapter, other, entries=entries)
    assert mismatch.safe_error_code == "graph_read_unavailable" and not mismatch.candidates
    adapter.backend.first_hit_view().version += 1
    stale = query_read.activate_query_read(adapter, request, entries=entries)
    assert stale.safe_error_code == "graph_read_unavailable" and not stale.candidates
    assert len(calls) == count


def test_opt_in_index_buffer_keeps_legacy_top_five_and_channel_bounds(monkeypatch):
    from Conversation_Memory.adapter import _recall_execution

    def event(eid):
        fact = candidate(eid, eid + " original fact.")
        return SimpleNamespace(node_id=eid, node_type="EVENT", attributes=fact.metadata,
            content_narrative=fact.text, timestamp=datetime.fromisoformat(fact.timestamp))

    lexical = [event(f"lexical{i}") for i in range(7)]
    entity_events = [event(f"member{i}") for i in range(7)]
    hubs = {f"entity:r{i}": SimpleNamespace(node_id=f"entity:r{i}", attributes={}) for i in range(5)}
    backend = SimpleNamespace(
        trg=SimpleNamespace(
            vector_db=SimpleNamespace(index=SimpleNamespace(ntotal=0)),
            graph_db=SimpleNamespace(get_node=hubs.get),
            encoder=SimpleNamespace(encode=lambda *args: pytest.fail("zero encoding fixture")),
        ),
        _event_node_type=SimpleNamespace, _node_type=SimpleNamespace(EVENT="EVENT"),
        _lexical_index=SimpleNamespace(rank=lambda **kwargs: lexical),
        _matching_surfaces=lambda query: (), _entity_membership_for_recall=lambda: None,
    )
    monkeypatch.setattr(_recall_execution, "_entity_subset_events", lambda *args, **kwargs: entity_events)
    options = {"target_entity_refs": tuple(f"R{i}" for i in range(5))}
    old, old_stats = _recall_execution.find_recall_seeds(backend, "query", FirstHitPolicy(), **options)
    buffered, stats = _recall_execution.find_recall_seeds(
        backend, "query", FirstHitPolicy(), _candidate_buffer=True, **options)
    assert len(old) == old_stats["seeds_returned"] == 5
    assert len(buffered) == stats["seeds_returned"] == 15
    assert old == buffered[:5]
    assert {node for node, _ in buffered} == {
        *(f"lexical{i}" for i in range(5)), *(f"member{i}" for i in range(5)), *hubs}
    assert not any(value for key, value in stats.items() if key.endswith("_unavailable"))
def test_v2_never_silently_discards_legacy_explicit_conditions(tmp_path, monkeypatch):
    from Conversation_Memory.adapter.graph_read_query import GraphReadQuery, RelationConstraint
    from Conversation_Memory.adapter.models import RecallPolicy
    adapter, calls = query_adapter(tmp_path, monkeypatch)
    result = adapter.recall(GraphReadQuery(recorder_query().text, relations=(RelationConstraint("use"),)),
                            RecallPolicy())
    assert result.safe_error_code == "invalid_query"
    assert not result.evidence and not result.rendered_text
    assert not calls
