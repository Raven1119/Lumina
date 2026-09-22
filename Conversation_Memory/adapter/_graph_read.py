"""Explicit read-side activation; the ingestion activation is unchanged.

One whole-query search freezes the shared entry set. Cue masks use existing
entity memberships or lexical features on those entries, with no extra search
or encoder call. They measure graph support, never logical truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ._anchor_fusion import _query_features
from ._associative_recall import Activation
from ._first_hit_read import discover_first_hit_read
from ._recall_execution import find_recall_seeds
from .graph_read_query import GraphReadQuery, compile_graph_read_query, qualify_fact
from .user_self import classify_target_entity_ref


@dataclass
class ReadSelection:
    """Small request-local packing hints, not persisted or publicly rendered."""
    request: GraphReadQuery
    group_h: dict[str, dict[str, float]]
    matched: dict[str, tuple[int, ...]]
    diagnostics: dict = field(default_factory=dict)

    def permits(self, eid):
        return not self.request.relations or bool(self.matched.get(eid))

    def priority(self, eid, selected):
        satisfied = {i for key in selected for i in self.matched.get(key, ())}
        new = set(self.matched.get(eid, ())) - satisfied
        coverage = (min((scores.get(eid, 0.0) for scores in self.group_h.values()), default=0.0)
                    if self.request.require_all else 0.0)
        # Stable sorting retains each pool's existing order after these keys.
        return -len(new), -coverage

    def finish(self, selected):
        satisfied = {i for key in selected for i in self.matched.get(key, ())}
        missing = tuple(i for i in range(len(self.request.relations)) if i not in satisfied)
        uncovered = tuple(key for key, values in self.group_h.items()
                          if not any(values.get(eid, 0.0) > 0 for eid in selected))
        incomplete = bool(missing or self.request.require_all and uncovered)
        self.diagnostics.update(
            missing_relation_indices=missing, uncovered_visible_groups=uncovered,
            evidence_complete=not incomplete,
            coverage_meaning="graph_support_only_not_logical_entailment",
        )
        return "graph_read_evidence_incomplete" if incomplete else None


def _group_seeds(backend, view, seeds, request):
    clues = tuple((clue.text, clue.entity_refs) for clue in request.clues)
    if not clues and request.relations:
        clues = tuple((part.relation or request.text, tuple(dict.fromkeys(
            (*part.subject_refs, *part.object_refs)))) for part in request.relations)
    if not clues:
        return {"whole_query": dict(seeds)}, {"group_assignment": "whole_query"}
    groups, assignments = {}, {}
    index = getattr(backend, "_lexical_index", None)
    seed_features = getattr(index, "node_features", {})
    for position, (text, refs) in enumerate(clues):
        supported = {}
        features = frozenset(_query_features(text))
        for node_id, weight in seeds:
            if refs:
                match = any(node_id == "entity:" + ref.casefold()
                            or bool(view.entity_roles(node_id, ref)) for ref in refs)
            else:
                # Read only up to five already frozen entries in the owner's
                # existing lexical view. No per-cue global retrieval/traversal.
                match = bool(features.intersection(seed_features.get(node_id, ())))
            if match:
                supported[node_id] = weight
        total = sum(supported.values())
        key = f"clue_{position}"
        groups[key] = {node: weight / total for node, weight in supported.items()} if total else {}
        assignments[key] = "entity_membership" if refs else "indexed_lexical_overlap"
    return groups, {"group_assignment": assignments,
                    "uncovered_seed_groups": tuple(key for key, weights in groups.items() if not weights),
                    "group_scale": "unit_mass_per_covered_cue_on_shared_entries"}


def activate_read(adapter, cue):
    """The graph-read-v1 entry; never called by `_activate_first_hit`/writer."""
    backend, policy = adapter.backend, adapter.first_hit
    diagnostics = {"profile": "graph-read-v1"}
    adapter._last_graph_read_snapshot = None
    try:
        compiled = compile_graph_read_query(cue, backend)
        request = compiled.request
        diagnostics.update(compiled.diagnostics)
    except (TypeError, ValueError):
        return Activation(safe_error_code="invalid_query", diagnostics=diagnostics)
    try:
        view = backend.first_hit_view()
        version = view.version
        # Identical whole-query search to the baseline. Explicit conditions do
        # not import gold entries or acquire extra seed slots.
        refs = tuple(backend.resolve_target_entity_refs(request.text, limit=policy.max_seeds))
        current_user = classify_target_entity_ref(request.text)
        if current_user:
            refs = tuple(dict.fromkeys((current_user, *refs)))[:policy.max_seeds]
        seeds, search_stats = find_recall_seeds(backend, request.text, policy, target_entity_refs=refs)
        diagnostics.update(search_stats)
        view.check(version)
        seeds = tuple((node, score) for node, score in seeds if view.eligible(node))
        total = sum(score for _, score in seeds)
        seeds = tuple((node, score / total) for node, score in seeds) if total else ()
        groups, group_stats = _group_seeds(backend, view, seeds, request)
        diagnostics.update(group_stats)
        result = discover_first_hit_read(view, seeds, policy, group_seeds=groups)
        candidates, seed_facts, matched, qualifications = [], {}, {}, {}
        group_evidence = {key: {} for key in result.group_h}
        for node in result.fact_ids:
            candidate = backend.first_hit_candidate(node)
            if candidate is None:
                continue
            eid = candidate.metadata.get("evidence_id")
            if not isinstance(eid, str) or not eid:
                continue
            candidates.append((candidate, result.h[node], result.attention[node]))
            if node in result.seed_weights:
                seed_facts[node] = eid
            qualification = qualify_fact(candidate, node, request, view)
            matched[eid] = qualification.matched_indices
            qualifications[eid] = {"statuses": qualification.statuses, **qualification.diagnostics}
            for key, values in result.group_h.items():
                group_evidence[key][eid] = values[node]
        view.check(version)
        diagnostics.update(result.stats, delta=result.delta, qualifications=qualifications,
                           shared_seeds=tuple(seeds))
        selection = ReadSelection(request, group_evidence, matched, diagnostics)
        # Private transient audit data, not in public DTOs, graph or checkpoint.
        adapter._last_graph_read_snapshot = result
        unavailable = any(value for key, value in search_stats.items() if key.endswith("_unavailable"))
        return Activation(tuple(candidates),
                          safe_error_code="first_hit_seed_channel_unavailable" if unavailable else None,
                          diagnostics=diagnostics,
                          seed_fact_ids=tuple(seed_facts[node] for node, _ in seeds if node in seed_facts),
                          read_selection=selection)
    except Exception:
        # Do not silently retry via the writer or reuse unqualified seed facts.
        # An incomplete constrained query must never become open success.
        return Activation(safe_error_code="graph_read_unavailable", diagnostics=diagnostics)
