"""Query-driven, read-only candidate: bounded entries and local evidence joins.

The language gate supplies source-located syntax, never graph identities. This
module resolves existing identities and tests actual persisted Fact roles. It
does not interpret path activation as a proposition or modify stored material.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import islice, product

from ._anchor_fusion import _query_features
from ._associative_recall import Activation
from ._first_hit_read import discover_query_first_hit
from ._recall_execution import find_recall_seeds
from .controlled_relation import ControlledRelationResolver, _normalize
from .graph_read_query import (
    GraphReadQuery, RelationConstraint, compile_graph_read_query, qualify_fact,
    validate_query_intent,
)
from .user_self import CURRENT_USER_ENTITY_REF, classify_target_entity_ref


_RELATIONS = ControlledRelationResolver()
# These two elementary families are candidate-local. The old controlled
# vocabulary has neither family; changing it would alter older readers.
_ALIASES = {
    **dict.fromkeys(("use", "uses", "using", "使用", "用"), "use"),
    **dict.fromkeys(("mass", "weight", "weigh", "weighs", "重量", "质量", "重", "多重"), "mass"),
}


def _predicate(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = _normalize(value)
    return _ALIASES.get(value) or _RELATIONS.resolve_query_relations((value,))[0] or value


@dataclass(frozen=True)
class EntryGroup:
    key: str
    text: str
    refs: tuple[str, ...] = ()
    predicate: str | None = None


@dataclass(frozen=True)
class QueryEntries:
    """Transient frozen B/C input. Valid only for this request and view version."""
    request: GraphReadQuery
    version: int
    seeds: tuple[tuple[str, float], ...]
    groups: dict[str, dict[str, float]]
    refs: dict[str, tuple[str, ...]]
    diagnostics: dict


def _resolve_clues(backend, request, view):
    refs, diagnostics = {}, {"identity_alternatives": {}, "unresolved_clues": [],
                              "truncated_clues": []}
    intent = request.intent
    if intent is None:
        return refs, diagnostics
    for clue in intent.clues:
        values = []
        if clue.kind == "current_user":
            values = [CURRENT_USER_ENTITY_REF]
        elif clue.kind != "literal":
            # Existing indexed surfaces, not generated identity descriptions.
            surfaces = tuple(islice(backend._matching_surfaces(clue.text), 5))
            if len(surfaces) > 4:
                diagnostics["truncated_clues"].append(clue.id)
            if not surfaces:
                surfaces = (clue.text,)
            for surface in surfaces[:4]:
                for candidate in backend.find_entity_candidates(surface, limit=6):
                    if candidate.entity_ref not in values:
                        values.append(candidate.entity_ref)
                    if len(values) > 5:
                        break
                if len(values) > 5:
                    break
        if len(values) > 5 and clue.id not in diagnostics["truncated_clues"]:
            diagnostics["truncated_clues"].append(clue.id)
        values = tuple(ref for ref in values[:5] if view.eligible("entity:" + ref.casefold()))
        refs[clue.id] = values
        diagnostics["identity_alternatives"][clue.id] = values
        if not values and clue.kind in {"name", "current_user"}:
            diagnostics["unresolved_clues"].append(clue.id)
    return refs, diagnostics


def _entry_groups(request, refs):
    intent = request.intent
    if intent is None:
        return tuple(EntryGroup(f"clue_{i}", clue.text, clue.entity_refs)
                     for i, clue in enumerate(request.clues[:3]))
    clues = {clue.id: clue for clue in intent.clues}
    groups, used = [], set()
    for i, relation in enumerate(intent.relations):
        endpoints = [key for key in (relation.subject, relation.object) if key in clues]
        used.update(endpoints)
        known = tuple(dict.fromkeys(ref for key in endpoints for ref in refs[key]))
        # Missing targets stay variables. No answer or candidate body enters
        # this supplementary search string.
        text = " ".join((*(clues[key].text for key in endpoints), relation.predicate))
        groups.append(EntryGroup(f"relation_{i}", text, known, relation.predicate))
    for clue in intent.clues:
        if clue.id not in used and len(groups) < 3:
            groups.append(EntryGroup(clue.id, clue.text, refs[clue.id]))
    return tuple(groups)


def freeze_query_entries(adapter, request):
    """One whole-question retrieval, at most two missing-clue supplements.

    Each retrieval retains <=20 RRF candidates from four <=5 channels, hence
    the request union is <=60 (below the 64 limit), before the shared five.
    Two original whole-query anchors are protected. A greedy coverage pass
    fills remaining slots; ties retain stable whole/supplement RRF order.
    """
    backend = adapter.backend
    policy = replace(adapter.first_hit, max_seeds=min(5, adapter.first_hit.max_seeds),
                     max_nodes=min(64, adapter.first_hit.max_nodes),
                     max_edges=min(256, adapter.first_hit.max_edges))
    view = backend.first_hit_view()
    version = view.version
    refs, diagnostics = _resolve_clues(backend, request, view)
    groups = _entry_groups(request, refs)
    candidate_cache = {}

    def supports(node, group):
        if group.refs and not any(node == "entity:" + ref.casefold()
                                  or view.entity_roles(node, ref) for ref in group.refs):
            return False
        if group.predicate is not None:
            if node not in candidate_cache:
                candidate_cache[node] = backend.first_hit_candidate(node)
            candidate = candidate_cache[node]
            return candidate is not None and _predicate(candidate.metadata.get("relation")) == _predicate(group.predicate)
        if group.refs:
            return True
        features = getattr(getattr(backend, "_lexical_index", None), "node_features", {})
        return bool(set(_query_features(group.text)).intersection(features.get(node, ())))

    original_refs = list(backend.resolve_target_entity_refs(request.text, limit=5))
    current_user = classify_target_entity_ref(request.text)
    if current_user:
        original_refs.insert(0, current_user)
    original_refs = tuple(dict.fromkeys(original_refs))[:5]
    searches, buffer = [], {}

    def search(text, known, label):
        rows, stats = find_recall_seeds(backend, text, policy, target_entity_refs=known[:5],
                                        _candidate_buffer=True)
        eligible = tuple((node, weight) for node, weight in rows if view.eligible(node))
        searches.append({"label": label, "query": text, "refs": known[:5],
                         "candidates": eligible, "stats": stats})
        for node, weight in eligible:
            buffer[node] = max(buffer.get(node, 0.0), weight)
        if len(buffer) > 64:
            raise ValueError("query_candidate_buffer_exceeded")
        return eligible

    whole = search(request.text, original_refs, "whole_question")
    for group in groups:
        if len(searches) >= 3:
            break
        if not any(supports(node, group) for node in buffer):
            search(group.text, group.refs, group.key)
    selected = [node for node, _ in whole[:min(2, policy.max_seeds)]]
    covered = {group.key for group in groups if any(supports(node, group) for node in selected)}
    while len(selected) < policy.max_seeds:
        remaining = [node for node in buffer if node not in selected]
        if not remaining:
            break
        # Python's stable max resolves equal support/score by buffer order.
        node = max(remaining, key=lambda key: (
            sum(group.key not in covered and supports(key, group) for group in groups), buffer[key]))
        selected.append(node)
        covered.update(group.key for group in groups if supports(node, group))
    total = sum(buffer[node] for node in selected)
    seeds = tuple((node, buffer[node] / total) for node in selected) if total else ()
    group_seeds = {}
    for group in groups:
        values = {node: weight for node, weight in seeds if supports(node, group)}
        mass = sum(values.values())
        group_seeds[group.key] = {node: weight / mass for node, weight in values.items()} if mass else {}
    if not group_seeds:
        group_seeds = {"whole_question": dict(seeds)}
    clue_coverage = {}
    for clue in request.intent.clues if request.intent else ():
        group = EntryGroup(clue.id, clue.text, refs[clue.id])
        clue_coverage[clue.id] = tuple(node for node, _ in seeds if supports(node, group))
    diagnostics.update(
        searches=searches, candidate_buffer=tuple(buffer.items()), candidate_buffer_count=len(buffer),
        whole_question_anchors=tuple(node for node, _ in whole[:2]),
        shared_seeds=seeds, entry_groups=tuple(groups),
        uncovered_seed_groups=tuple(key for key, values in group_seeds.items() if not values),
        entry_search_count=len(searches),
        clue_seed_coverage=clue_coverage,
        uncovered_clues=tuple(key for key, nodes in clue_coverage.items() if not nodes),
        entry_coverage_meaning="retrieval_support_not_role_or_truth",
    )
    view.check(version)
    return QueryEntries(request, version, seeds, group_seeds, refs, diagnostics)


def _match_relation(candidate, node, relation, clues, refs, request, view):
    """One candidate must independently establish all roles in one relation."""
    metadata = candidate.metadata
    if _predicate(relation.predicate) != _predicate(metadata.get("relation")):
        return None, "predicate_unresolved"
    bindings, role_refs = {}, {}
    for role, endpoint in (("subject", relation.subject), ("object", relation.object)):
        clue = clues.get(endpoint)
        if endpoint == "?value" or clue is not None and clue.kind == "literal":
            value = metadata.get("value")
            if not isinstance(value, str) or not value.strip():
                return None, "literal_value_missing"
            if clue is not None and _normalize(value) != _normalize(clue.text):
                return None, "literal_value_mismatch"
            if endpoint == "?value":
                bindings[endpoint] = _normalize(value)
            continue
        actual = metadata.get(role + "_entity_ref")
        if not isinstance(actual, str) or not actual.strip():
            return None, "binding_missing"
        if endpoint != "?entity" and actual not in refs.get(endpoint, ()):
            return None, "identity_unresolved_or_different"
        previous = bindings.get(endpoint)
        if previous is not None and previous != actual:
            return None, "within_fact_identity_conflict"
        bindings[endpoint] = actual
        role_refs[role] = (actual,)
    # Predicate equivalence was checked above. Ask the unchanged qualifier to
    # verify this actual stored predicate, actual role edges and source shape.
    condition = RelationConstraint(metadata["relation"], role_refs.get("subject", ()),
                                   role_refs.get("object", ()))
    qualification = qualify_fact(candidate, node, GraphReadQuery(request.text, relations=(condition,)), view)
    if not qualification.matched_indices:
        return None, "role_or_source_unknown"
    if metadata.get("negated") is True or metadata.get("polarity") in {"negative", "negated"}:
        return None, "polarity_unresolved"
    return bindings, "matched"


@dataclass
class QuerySelection:
    request: GraphReadQuery
    matched: dict[str, tuple[int, ...]]
    bundles: tuple[tuple[str, ...], ...]
    group_h: dict[str, dict[str, float]]
    diagnostics: dict = field(default_factory=dict)

    def permits(self, eid):
        return not self.request.intent or not self.request.intent.relations or bool(self.matched.get(eid))

    def priority(self, eid, selected):
        completed = {index for key in selected for index in self.matched.get(key, ())}
        return (-int(any(eid in bundle and selected.intersection(bundle) for bundle in self.bundles)),
                -len(set(self.matched.get(eid, ())) - completed),
                -min((scores.get(eid, 0.0) for scores in self.group_h.values()), default=0.0))

    def finish(self, selected):
        intent = self.request.intent
        if intent is None or intent.mode == "open":
            self.diagnostics.update(evidence_complete=False, result_status="open_association",
                                    visible_bundles=())
            return None
        satisfied = {index for key in selected for index in self.matched.get(key, ())}
        visible = tuple(bundle for bundle in self.bundles if set(bundle).issubset(selected))
        scoped = self.diagnostics.get("complete_bundle_candidates", ())
        blocked = bool(intent.unresolved or self.diagnostics.get("ambiguous_bindings")
                       or self.diagnostics.get("unresolved_clues") or self.diagnostics.get("truncated_clues"))
        complete = any(bundle in scoped for bundle in visible) and not blocked
        self.diagnostics.update(
            missing_relation_indices=tuple(i for i in range(len(intent.relations)) if i not in satisfied),
            visible_bundles=visible, evidence_complete=complete,
            result_status="complete_evidence_bundle" if complete else "partial_evidence",
            qualification_scope="stored_role_and_source_shape_not_verified_truth_or_current_scope",
        )
        return None if complete else "graph_read_evidence_incomplete"


def _selection(request, entries, candidates, result, view, diagnostics):
    intent = request.intent
    matched, qualifications = {}, {}
    rows = [[] for _ in intent.relations] if intent else []
    clues = {clue.id: clue for clue in intent.clues} if intent else {}
    group_evidence = {key: {} for key in result.group_h}
    for node, candidate in candidates:
        eid = candidate.metadata["evidence_id"]
        statuses, indices = [], []
        for i, relation in enumerate(intent.relations if intent else ()):
            bindings, status = _match_relation(candidate, node, relation, clues, entries.refs, request, view)
            statuses.append(status)
            if bindings is not None:
                rows[i].append((eid, bindings))
                indices.append(i)
        matched[eid] = tuple(indices)
        qualifications[eid] = tuple(statuses)
        for key, values in result.group_h.items():
            group_evidence[key][eid] = values[node]
    bundles, assignments, complete_bundles, scope_gaps = [], [], [], []
    by_id = {candidate.metadata["evidence_id"]: (node, candidate) for node, candidate in candidates}
    used_clues = {endpoint for relation in intent.relations
                  for endpoint in (relation.subject, relation.object)} if intent else set()
    # <=64 x 64, two clauses only. This is request-local enumeration, not an
    # unbounded general join engine or another graph search.
    attempts = 0
    for combination in product(*rows) if rows else ():
        attempts += 1
        binding, ids = {}, []
        for eid, part in combination:
            if any(key in binding and binding[key] != value for key, value in part.items()):
                break
            binding.update(part)
            ids.append(eid)
        else:
            bundle = tuple(dict.fromkeys(ids))
            if bundle not in bundles:
                bundles.append(bundle)
                assignments.append(binding)
                gaps, time_scopes = [], []
                surfaces, predicates = set(), set()
                for eid in bundle:
                    node, candidate = by_id[eid]
                    meta = candidate.metadata
                    predicates.add(_predicate(meta.get("relation")))
                    for role, fallback in (("subject", "subject"), ("object", "value")):
                        ref = meta.get(role + "_entity_ref")
                        surface = meta.get(role + "_entity_surface") or meta.get(fallback)
                        if (isinstance(ref, str) and role in view.entity_roles(node, ref)
                                and isinstance(surface, str)):
                            surfaces.add(_normalize(surface))
                    # Speaking timestamps are provenance, not validity ranges.
                    # Existing explicit time annotations cannot be reconciled
                    # by this two-clause fragment; do not silently erase them.
                    scope = meta.get("referenced_time") or meta.get("temporal_mentions") or None
                    time_scopes.append(scope)
                if len(bundle) > 1 and any(time_scopes):
                    if any(scope is None for scope in time_scopes) or any(scope != time_scopes[0] for scope in time_scopes):
                        gaps.append("fact_time_scopes_unresolved")
                for clue in intent.clues if intent else ():
                    if clue.id in used_clues:
                        continue
                    # Extra clues remain retrieval hints, not invented AND
                    # conditions. Without exact role-surface/predicate support
                    # their contribution to the original question is unproved.
                    if _normalize(clue.text) not in surfaces and _predicate(clue.text) not in predicates:
                        gaps.append("unaccounted_clue:" + clue.id)
                if gaps:
                    scope_gaps.append({"bundle": bundle, "gaps": tuple(gaps)})
                else:
                    complete_bundles.append(bundle)
    signatures = {tuple(sorted(binding.items())) for binding in assignments}
    ambiguous_clues = tuple(key for key, alternatives in entries.refs.items() if len(alternatives) > 1)
    diagnostics.update(qualifications=qualifications, bundle_candidates=tuple(bundles),
                       bundle_bindings=tuple(assignments), join_attempts=attempts,
                       complete_bundle_candidates=tuple(complete_bundles), bundle_scope_gaps=tuple(scope_gaps),
                       ambiguous_clues=ambiguous_clues,
                       ambiguous_bindings=bool(len(signatures) > 1 or ambiguous_clues),
                       unresolved_conditions=intent.unresolved if intent else (),
                       coverage_meaning="graph_support_only_not_logical_entailment")
    return QuerySelection(request, matched, tuple(bundles), group_evidence, diagnostics)


def activate_query_read(adapter, cue, *, entries=None, seed_only=False):
    """Public-facade activation; optional frozen inputs are for the B/C ablation."""
    diagnostics = {"profile": "graph-read-v2", "seed_only": seed_only}
    adapter._last_graph_read_snapshot = None
    adapter._last_query_entries = None
    try:
        request = cue if isinstance(cue, GraphReadQuery) else GraphReadQuery(cue)
        validate_query_intent(request)
        if request.intent is None:
            if request.clues or request.relations or request.require_all:
                raise ValueError("legacy_conditions_require_graph_read_v1")
            compiled = compile_graph_read_query(request.text, adapter.backend)
            request = compiled.request
            if len(request.clues) > 3:
                request = replace(request, clues=request.clues[:3])
                diagnostics["fallback_clues_truncated"] = True
            diagnostics.update(compiled.diagnostics, interpretation="open_without_structured_gate")
        else:
            diagnostics["interpretation"] = request.intent.mode
    except (TypeError, ValueError):
        return Activation(safe_error_code="invalid_query", diagnostics=diagnostics)
    try:
        view = adapter.backend.first_hit_view()
        if entries is None:
            entries = freeze_query_entries(adapter, request)
        if entries.request != request:
            raise ValueError("frozen_query_mismatch")
        view.check(entries.version)
        diagnostics.update(entries.diagnostics)
        result = discover_query_first_hit(view, entries.seeds, adapter.first_hit,
                                         group_seeds=entries.groups, seed_only=seed_only)
        candidates = []
        for node in result.fact_ids:
            candidate = adapter.backend.first_hit_candidate(node)
            if candidate is not None and isinstance(candidate.metadata.get("evidence_id"), str):
                candidates.append((node, candidate))
        selection = _selection(request, entries, candidates, result, view, diagnostics)
        seed_eids = {node: candidate.metadata["evidence_id"] for node, candidate in candidates}
        diagnostics.update(result.stats, delta=result.delta)
        view.check(entries.version)
        adapter._last_graph_read_snapshot = result
        adapter._last_query_entries = entries
        unavailable = any(value for search in entries.diagnostics["searches"]
                          for key, value in search["stats"].items() if key.endswith("_unavailable"))
        return Activation(tuple((candidate, result.h[node], result.attention[node]) for node, candidate in candidates),
                          "first_hit_seed_channel_unavailable" if unavailable else None, diagnostics,
                          tuple(seed_eids[node] for node, _ in entries.seeds if node in seed_eids), selection)
    except Exception as error:
        # Record a safe class, never a private path/body/traceback in public DTOs.
        diagnostics["failure_type"] = type(error).__name__
        return Activation(safe_error_code="graph_read_unavailable", diagnostics=diagnostics)
