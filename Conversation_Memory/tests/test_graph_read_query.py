"""Deterministic request/role contracts; no model, encoder or persistent state."""
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from Conversation_Memory.adapter.graph_read_query import (
    GraphReadQuery, ReadClue, RelationConstraint, compile_graph_read_query,
    qualify_fact,
)
from Conversation_Memory.adapter.models import BackendCandidate


class View:
    def __init__(self, roles=(), known=("A", "B", "C")):
        self.roles = dict(roles)
        self.known = {"entity:" + ref.casefold() for ref in known}

    def eligible(self, node_id):
        return node_id in self.known

    def entity_roles(self, node_id, ref):
        return frozenset(self.roles.get((node_id, ref), ()))


class Names:
    def __init__(self, names):
        self.names, self.calls = names, []

    def _matching_surfaces(self, text):
        return (name for name in self.names if name in text)

    def find_entity_candidates(self, surface, *, limit):
        self.calls.append((surface, limit))
        return tuple(SimpleNamespace(entity_ref=ref) for ref in self.names[surface][:limit])

    def first_hit_view(self):
        return View(known=tuple(ref for refs in self.names.values() for ref in refs))


def fact(**changes):
    metadata = dict(evidence_id="F1", relation="guides", subject_entity_ref="A", object_entity_ref="B",
                    source_start=0, source_end=10, provenance=dict(
                        segment_id="S", conversation_id="C", turn_id="T", source_role="user",
                        source_timestamp="2026-09-22T00:00:00+00:00", source_timezone="UTC",
                        ingestion_version="grounded-formation-v6", timezone_source="client"))
    metadata.update(changes)
    return BackendCandidate("A guides B", "2026-09-22T00:00:00+00:00", None, metadata)


def roles():
    return View(((('F1', 'A'), ('subject',)), (('F1', 'B'), ('object',))))


def test_natural_names_are_individual_clues_with_or_identity_alternatives():
    backend = Names({"林禾": ("A", "B"), "周岚": ("C",)})
    original = "  谁指导林禾，周岚有什么关系？  "
    result = compile_graph_read_query(original, backend)
    assert result.request.text == original
    assert result.request.clues == (ReadClue("林禾", ("A", "B")), ReadClue("周岚", ("C",)))
    assert result.request.require_all is False and result.request.relations == ()
    assert result.diagnostics["selection_mode"] == "open"
    assert "relation_direction_unresolved" in result.diagnostics["parse_gaps"]
    assert "combination_unresolved" in result.diagnostics["parse_gaps"]
    assert backend.calls == [("林禾", 6), ("周岚", 6)]


def test_lookup_truncation_is_reported_without_inventing_absent_conditions():
    backend = Names({str(i): tuple("id" + str(n) for n in range(7)) for i in range(7)})
    result = compile_graph_read_query("0123456", backend)
    assert len(result.request.clues) == 5
    assert all(len(clue.entity_refs) == 5 for clue in result.request.clues)
    assert result.diagnostics["clue_limit_exceeded"]
    assert len(result.diagnostics["truncated_entity_alternatives"]) == 5
    assert not result.request.require_all


def test_resolver_fallback_groups_refs_as_alternatives_not_conjunction():
    backend = SimpleNamespace(resolve_target_entity_refs=lambda text, limit: ("A", "B"))
    result = compile_graph_read_query("two names", backend)
    assert result.request.clues == (ReadClue("two names", ("A", "B")),)
    assert not result.request.require_all
    assert result.diagnostics["surface_grouping_unavailable"]
    assert result.diagnostics["entity_reference_validation"] == "unavailable"


def test_unavailable_or_unresolved_lookup_keeps_open_original_question():
    result = compile_graph_read_query("who?", SimpleNamespace())
    assert result.request == GraphReadQuery("who?")
    assert result.diagnostics["entity_lookup_unavailable"]
    unresolved = compile_graph_read_query("who?", Names({"who": ()}))
    assert unresolved.diagnostics["unresolved_clue_indices"] == (0,)


def test_explicit_request_is_not_reinterpreted_and_unknown_ref_is_reported():
    query = GraphReadQuery("original", (ReadClue("a", ("A", "not-stored")),), True,
                           (RelationConstraint("guides", object_refs=("B",)),))
    result = compile_graph_read_query(query, Names({"a": ("A", "B")}))
    assert result.request is query
    assert result.diagnostics["unknown_entity_refs"] == ("not-stored",)
    with pytest.raises(FrozenInstanceError):
        query.text = "rewritten"


@pytest.mark.parametrize("make", [
    lambda: ReadClue("a", tuple(str(i) for i in range(6))),
    lambda: RelationConstraint("a", object_refs=("",)),
    lambda: GraphReadQuery("q", tuple(ReadClue(str(i)) for i in range(6))),
    lambda: GraphReadQuery("q", relations=tuple(RelationConstraint(str(i)) for i in range(6))),
    lambda: GraphReadQuery("q", require_all=1),
    lambda: GraphReadQuery(" "),
    lambda: ReadClue("a", ["A"]),
])
def test_request_bounds_and_immutability_are_validated(make):
    with pytest.raises(ValueError):
        make()


def test_role_direction_uses_metadata_and_actual_persisted_roles():
    query = GraphReadQuery("who guides B?", relations=(
        RelationConstraint("guides", object_refs=("B",)),
        RelationConstraint("guides", subject_refs=("B",)),
    ))
    result = qualify_fact(fact(), "F1", query, roles())
    assert result.statuses == ("matched", "unmatched")
    assert result.matched_indices == (0,) and result.unknown_indices == ()


def test_ordinary_mentions_or_metadata_alone_do_not_establish_a_role():
    query = GraphReadQuery("q", relations=(RelationConstraint("guides", object_refs=("B",)),))
    for view in (View(), View(((('F1', 'B'), ('ordinary',)),))):
        result = qualify_fact(fact(), "F1", query, view)
        assert result.statuses == ("unknown",)
        assert result.unknown_indices == (0,)
        assert result.diagnostics["constraints"][0]["object_reason"] == "persisted_role_missing"
    no_binding = qualify_fact(fact(object_entity_ref=None), "F1", query, roles())
    assert no_binding.statuses == ("unknown",)


def test_duplicate_labels_have_no_extra_qualification_and_alias_refs_are_or():
    query = GraphReadQuery("q", relations=(RelationConstraint("guides", object_refs=("C", "B")),))
    multi = View(((('F1', 'B'), ('object', 'object', 'ordinary', 'subject')),))
    assert qualify_fact(fact(), "F1", query, multi).statuses == ("matched",)
    assert qualify_fact(fact(), "F1", query, roles()).statuses == ("matched",)


def test_separate_facts_can_meet_separate_obligations_without_fabricated_join():
    query = GraphReadQuery("q", relations=(
        RelationConstraint("guides", subject_refs=("A",), object_refs=("B",)),
        RelationConstraint("visits", subject_refs=("B",), object_refs=("C",)),
        RelationConstraint("guides", subject_refs=("A",), object_refs=("C",)),
    ))
    first = qualify_fact(fact(), "F1", query, roles())
    second = qualify_fact(fact(relation="visits", subject_entity_ref="B", object_entity_ref="C"),
                           "F2", query, View(((('F2', 'B'), ('subject',)), (('F2', 'C'), ('object',)))))
    assert first.matched_indices == (0,) and second.matched_indices == (1,)
    assert first.statuses[2] == second.statuses[2] == "unmatched"


@pytest.mark.parametrize(("query", "stored", "expected"), [
    ("Guides", "ｇｕｉｄｅｓ", "matched"),
    ("service desk route", "服务联系渠道", "matched"),
    ("service desk route", "filter mesh classification", "unmatched"),
    ("guides", "mentors", "unknown"),
    ("guides", None, "unknown"),
])
def test_predicate_matching_uses_existing_aliases_or_normalized_exact_surface(query, stored, expected):
    request = GraphReadQuery("q", relations=(RelationConstraint(query),))
    assert qualify_fact(fact(relation=stored), "F1", request, roles()).statuses == (expected,)


@pytest.mark.parametrize("changes", [
    {"provenance": {}}, {"source_refs": []}, {"source_start": None},
    {"source_refs": [{"turn_id": "t", "source_start": 2, "source_end": 1, "source_role": "user"}]},
    {"source_refs": [{"turn_id": "t", "source_start": 0, "source_end": 1,
                       "source_role": "user", "supporting_span": "too long"}]},
])
def test_missing_or_invalid_source_scope_cannot_count_as_precise_match(changes):
    query = GraphReadQuery("q", relations=(RelationConstraint("guides", object_refs=("B",)),))
    result = qualify_fact(fact(**changes), "F1", query, roles())
    assert result.statuses == ("unknown",)
    assert not result.matched_indices


def test_source_shape_does_not_claim_to_have_checked_cold_text():
    query = GraphReadQuery("q", relations=(RelationConstraint("guides"),))
    candidate = fact(source_refs=[dict(turn_id="T", source_start=3, source_end=6,
                                       source_role="user", supporting_span="abc")])
    result = qualify_fact(candidate, "F1", query, roles())
    assert result.statuses == ("matched",)
    assert result.diagnostics["source_check_scope"] == "stored_metadata_only"
    assert qualify_fact(candidate, "F1", replace(query, relations=()), roles()).statuses == ()
