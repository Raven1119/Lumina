"""Public read behavior with synthetic sources; no encoder/provider/BGE."""
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

from Conversation_Memory.adapter import _associative_recall, _graph_read
from Conversation_Memory.adapter.first_hit import FirstHitPolicy
from Conversation_Memory.adapter.graph_read_query import GraphReadQuery, ReadClue, RelationConstraint
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import RecallPolicy
from Conversation_Memory.ingestion.state_store import IngestionStateStore
from Conversation_Memory.tests.test_first_hit_read import View
from Conversation_Memory.tests.test_reliable_recall_dispatch import candidate


def fixture(tmp_path, monkeypatch, rows, seeds, *, roles=None, facts=None, policy=None):
    facts = facts or {key: candidate(key, f"{key} complete original fact.") for key in rows}
    view = View(rows, facts=facts)
    roles = roles or {}
    view.entity_roles = lambda node, ref: frozenset(roles.get((node, ref), ()))
    backend = SimpleNamespace(
        first_hit_view=lambda: view,
        first_hit_candidate=lambda key: facts.get(key),
        resolve_target_entity_refs=lambda text, limit: (),
        _lexical_index=SimpleNamespace(node_features={}),
    )
    calls = []
    def find(backend, text, policy, **kwargs):
        calls.append((text, kwargs))
        return tuple(seeds.items()), {}
    monkeypatch.setattr(_graph_read, "find_recall_seeds", find)
    monkeypatch.setattr(_associative_recall, "find_recall_seeds", find)
    adapter = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "never-written.json"),
        ingestion_version="grounded-formation-v6", first_hit=policy or FirstHitPolicy(),
        associative_read_profile="graph-read-v1")
    return adapter, calls


def test_real_facade_dispatch_fixes_discovery_without_changing_writer(tmp_path, monkeypatch):
    adapter, calls = fixture(tmp_path, monkeypatch,
        {"A": [("B", 1), ("D", .5)], "B": [("target", 1)],
         "D": [("E", 1)], "E": [], "target": []}, {"A": .99, "B": .01},
        policy=FirstHitPolicy(max_nodes=4, max_edges=10))
    output = RecallPolicy(max_evidence_items=5, max_chars=3000)
    new = adapter.recall("complete original question", output)
    assert "target complete original fact." in new.rendered_text
    assert {e.evidence_id for e in new.evidence} == {"A", "B", "D", "target"}
    writer = adapter._activate_first_hit("complete original question")
    assert "target" not in {row[0].metadata["evidence_id"] for row in writer.candidates}
    assert writer.read_selection is None
    adapter.associative_read_profile = "reliable-v2"
    old = adapter.recall("complete original question", output)
    assert "target complete original fact." not in old.rendered_text
    assert all(text == "complete original question" for text, _ in calls) and len(calls) == 3
    assert not (tmp_path / "never-written.json").exists()
    assert set(asdict(adapter.first_hit)) == {"decay", "max_seeds", "max_nodes", "max_edges",
                                            "attention_budget", "attention_penalty", "max_links"}


def role_adapter(tmp_path, monkeypatch, *, ordinary=False):
    forward, reverse = candidate("forward", "A guides B."), candidate("reverse", "B guides A.")
    forward.metadata.update(relation="guides", subject_entity_ref="A", object_entity_ref="B")
    reverse.metadata.update(relation="guides", subject_entity_ref="B", object_entity_ref="A")
    roles = {("forward", "A"): ("subject",), ("forward", "B"): ("object",),
             ("reverse", "B"): ("subject",), ("reverse", "A"): ("object",)}
    if ordinary:
        roles = {key: ("ordinary",) for key in roles}
    return fixture(tmp_path, monkeypatch,
        {"entity:a": [("forward", 1), ("reverse", 1)], "entity:b": [],
         "forward": [], "reverse": []}, {"entity:a": 1}, roles=roles,
        facts={"forward": forward, "reverse": reverse})


def test_opposite_explicit_roles_select_original_facts_on_identical_local_graph(tmp_path, monkeypatch):
    adapter, calls = role_adapter(tmp_path, monkeypatch)
    policy = RecallPolicy(max_evidence_items=5, max_chars=3000)
    a = adapter.recall(GraphReadQuery("A B guides", relations=(RelationConstraint("guides", subject_refs=("A",)),)), policy)
    first = adapter._last_graph_read_snapshot
    b = adapter.recall(GraphReadQuery("A B guides", relations=(RelationConstraint("guides", object_refs=("A",)),)), policy)
    second = adapter._last_graph_read_snapshot
    assert [x.text for x in a.evidence] == ["A guides B."]
    assert [x.text for x in b.evidence] == ["B guides A."]
    assert "A guides B." in a.rendered_text and "B guides A." not in a.rendered_text
    assert "B guides A." in b.rendered_text and "A guides B." not in b.rendered_text
    assert first.node_ids == second.node_ids and np.array_equal(first.P, second.P)
    assert first.seed_weights == second.seed_weights and first.h == second.h
    assert len(calls) == 2


def test_two_actual_relation_facts_must_fit_no_invented_join(tmp_path, monkeypatch):
    adapter, _ = role_adapter(tmp_path, monkeypatch)
    request = GraphReadQuery("A B guides", relations=(RelationConstraint("guides", subject_refs=("A",)),
                                                       RelationConstraint("guides", subject_refs=("B",))))
    both = adapter.recall(request, RecallPolicy(max_evidence_items=2, max_chars=3000))
    assert {x.text for x in both.evidence} == {"A guides B.", "B guides A."}
    assert all(x.text in both.rendered_text for x in both.evidence)
    assert adapter._last_first_hit_diagnostics["evidence_complete"]
    partial = adapter.recall(request, RecallPolicy(max_evidence_items=1, max_chars=3000))
    assert len(partial.evidence) == 1 and partial.truncated
    assert partial.safe_error_code == "graph_read_evidence_incomplete"
    assert len(adapter._last_first_hit_diagnostics["missing_relation_indices"]) == 1
    assert "graph_support" not in partial.rendered_text


def test_role_only_condition_does_not_require_a_guessed_predicate(tmp_path, monkeypatch):
    adapter, _ = role_adapter(tmp_path, monkeypatch)
    request = GraphReadQuery("A B", relations=(RelationConstraint(object_refs=("A",)),))
    result = adapter.recall(request, RecallPolicy())
    assert [x.text for x in result.evidence] == ["B guides A."]
    assert adapter._last_first_hit_diagnostics["evidence_complete"]
    with pytest.raises(ValueError, match="graph_read_empty_relation_constraint"):
        RelationConstraint()


def test_derived_relation_clue_keeps_both_sets_of_identity_alternatives(tmp_path, monkeypatch):
    adapter, _ = fixture(tmp_path, monkeypatch, {"seed": []}, {"seed": 1},
                         roles={("seed", "object-last"): ("object",)})
    request = GraphReadQuery("question", relations=(RelationConstraint("guides",
        subject_refs=tuple("subject-" + str(i) for i in range(5)),
        object_refs=("object-first", "object-last")),))
    adapter.recall(request, RecallPolicy())
    assert adapter._last_graph_read_snapshot.group_seed_weights["clue_0"] == {"seed": 1}
    assert len(adapter._last_graph_read_snapshot.seed_weights) == 1


def test_unknown_roles_not_promoted_but_open_navigation_keeps_them(tmp_path, monkeypatch):
    adapter, _ = role_adapter(tmp_path, monkeypatch, ordinary=True)
    precise = adapter.recall(GraphReadQuery("A B guides", relations=(RelationConstraint("guides", subject_refs=("A",)),)), RecallPolicy())
    assert not precise.evidence and precise.safe_error_code == "graph_read_evidence_incomplete"
    open_result = adapter.recall("A B guides", RecallPolicy())
    assert len(open_result.evidence) == 2
    assert adapter._last_first_hit_diagnostics["selection_mode"] == "open"
    assert adapter._last_first_hit_diagnostics["parse_gaps"]


def test_same_name_alternatives_are_or_not_two_required_facts(tmp_path, monkeypatch):
    adapter, _ = role_adapter(tmp_path, monkeypatch)
    request = GraphReadQuery("A B guides", clues=(ReadClue("same name", ("A", "B")),),
                             relations=(RelationConstraint("guides", subject_refs=("A", "B")),))
    result = adapter.recall(request, RecallPolicy(max_evidence_items=1, max_chars=3000))
    assert len(result.evidence) == 1
    assert adapter._last_first_hit_diagnostics["evidence_complete"]
    assert len(adapter._last_graph_read_snapshot.group_h) == 1


def test_explicit_conjunction_ranks_group_coverage_inside_existing_pools(tmp_path, monkeypatch):
    rows = {"s1": [("only1", .6), ("both", .3)], "s2": [("only2", .6), ("both", .3)],
            "only1": [], "only2": [], "both": []}
    adapter, calls = fixture(tmp_path, monkeypatch, rows, {"s1": .5, "s2": .5},
        roles={("s1", "one"): ("ordinary",), ("s2", "two"): ("ordinary",)})
    request = GraphReadQuery("unchanged full question", clues=(ReadClue("one", ("one",)),
        ReadClue("two", ("two",))), require_all=True)
    result = adapter.recall_associative(request, RecallPolicy(max_evidence_items=3, max_chars=3000))
    assert [x.evidence_id for x in result.facts.evidence] == ["s1", "s2", "both"]
    assert adapter._last_reliable_recall_diagnostics["direct_reserved_items"] == 2
    assert adapter._last_first_hit_diagnostics["evidence_complete"]
    assert len(calls) == 1 and len(adapter._last_graph_read_snapshot.seed_weights) == 2
    # Group support ranks evidence. It never adds a synthesized AND claim.
    assert result.rendered_text.count("complete original fact.") == 3


def test_uncovered_clue_reports_capacity_gap_without_claiming_absent_memory(tmp_path, monkeypatch):
    adapter, _ = fixture(tmp_path, monkeypatch, {"seed": []}, {"seed": 1})
    request = GraphReadQuery("query", clues=(ReadClue("not in any seed"),), require_all=True)
    result = adapter.recall(request, RecallPolicy())
    assert len(result.evidence) == 1 and result.safe_error_code == "graph_read_evidence_incomplete"
    assert adapter._last_first_hit_diagnostics["uncovered_seed_groups"] == ("clue_0",)
    assert adapter._last_first_hit_diagnostics["coverage_meaning"] == "graph_support_only_not_logical_entailment"


def test_candidate_failure_does_not_retry_or_leak_unqualified_seeds(tmp_path, monkeypatch):
    adapter, calls = role_adapter(tmp_path, monkeypatch)
    def fail(*args, **kwargs):
        raise ValueError("private runtime payload")
    monkeypatch.setattr(_graph_read, "discover_first_hit_read", fail)
    result = adapter.recall(GraphReadQuery("A B guides", relations=(RelationConstraint("guides"),)), RecallPolicy())
    assert result.safe_error_code == "graph_read_unavailable"
    assert not result.evidence and not result.rendered_text
    assert len(calls) == 1 and adapter._last_graph_read_snapshot is None


def test_missing_seed_membership_stays_degraded_not_empty_success(tmp_path, monkeypatch):
    adapter, _ = fixture(tmp_path, monkeypatch, {"seed": []}, {"seed": 1})
    monkeypatch.setattr(_graph_read, "find_recall_seeds", lambda *a, **k:
                        ((("seed", 1),), {"entity_membership_unavailable": True}))
    result = adapter.recall("whole question", RecallPolicy())
    assert len(result.evidence) == 1
    assert result.safe_error_code == "first_hit_seed_channel_unavailable"


def test_read_profile_requires_existing_first_hit_configuration(tmp_path):
    with pytest.raises(ValueError, match="reliable_read_requires_first_hit"):
        MagmaMemoryAdapter(SimpleNamespace(), IngestionStateStore(tmp_path / "unused"),
                           associative_read_profile="graph-read-v1")
