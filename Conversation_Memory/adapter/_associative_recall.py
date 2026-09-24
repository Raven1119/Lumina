"""One first-hit activation seam shared by the public read and ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._graph_read import ReadSelection

from Conversation_Memory.recall.rendering import bound_evidence_groups

from ._recall_execution import find_recall_seeds
from .models import (
    AssociativeMemoryContext, BackendCandidate, MemoryContext, MemoryEvidence,
    SourceMemoryContext, SourceProvenance,
)


@dataclass(frozen=True)
class Activation:
    # All visited eligible facts, including zero-attention bridge facts.
    candidates: tuple[tuple[BackendCandidate, float, float], ...] = ()
    safe_error_code: str | None = None
    diagnostics: dict = field(default_factory=dict, repr=False)
    # Actual Fact seeds from the same search, in its original order.
    seed_fact_ids: tuple[str, ...] = ()
    # Request-only candidate selection; never used by the shared writer path.
    read_selection: ReadSelection | None = field(default=None, repr=False)


def activate(adapter, cue, *, target_entity_refs=None, exclude_evidence_ids=(), seed_only=False):
    from .first_hit import discover_first_hit, project_attention
    from .user_self import classify_target_entity_ref

    if adapter.first_hit is None:
        return Activation(safe_error_code="first_hit_not_configured")
    backend, policy = adapter.backend, adapter.first_hit
    seeds, diagnostics = (), {}
    try:
        excluded = tuple(mid for eid in exclude_evidence_ids
                         if (mid := backend.find_memory_id(eid)) is not None)
        try:
            view = backend.first_hit_view()
            version = view.version
        except Exception:
            view = None
        refs = tuple(target_entity_refs or ())
        if target_entity_refs is None and view is not None:
            refs = tuple(backend.resolve_target_entity_refs(cue, limit=policy.max_seeds))
            current_user = classify_target_entity_ref(cue)
            if current_user:
                refs = tuple(dict.fromkeys((current_user, *refs)))[:policy.max_seeds]
        seeds, diagnostics = find_recall_seeds(
            backend, cue, policy, target_entity_refs=refs,
            excluded_node_ids=excluded,
        )
        if view is None:
            raise ValueError("first_hit_snapshot_unavailable")
        view.check(version)
        # Filter with the same eligibility used by complete row denominators.
        seeds = tuple((node_id, score) for node_id, score in seeds
                      if view.eligible(node_id) and node_id not in excluded)
        total = sum(score for _, score in seeds)
        seeds = tuple((node_id, score / total) for node_id, score in seeds) if total else ()
        result = discover_first_hit(view, seeds, replace(policy, max_edges=0) if seed_only else policy, excluded_node_ids=excluded)
        adapter._last_first_hit_snapshot = result
        candidates, seed_facts = [], {}
        seed_nodes = {node_id for node_id, _ in seeds}
        for node_id in result.fact_ids:
            candidate = backend.first_hit_candidate(node_id)
            if candidate is not None:
                candidates.append((candidate, result.h[node_id], result.attention[node_id]))
                if node_id in seed_nodes:
                    seed_facts[node_id] = candidate.metadata.get("evidence_id")
        view.check(version)
        diagnostics.update(result.stats)
        diagnostics["delta"] = result.delta
        partial = any(value for key, value in diagnostics.items()
                      if key.endswith("_unavailable"))
        return Activation(tuple(candidates),
                          "first_hit_seed_channel_unavailable" if partial else None,
                          diagnostics, tuple(seed_facts[node_id] for node_id, _ in seeds
                                             if isinstance(seed_facts.get(node_id), str)))
    except Exception:
        # One failure, no resampling/retrieval retry. Preserve only independently
        # projectable seed Facts; callers can see activation is unavailable.
        fallback = []
        total = sum(score for _, score in seeds)
        for node_id, score in seeds:
            try:
                candidate = backend.first_hit_candidate(node_id)
                if candidate is not None:
                    weight = score / total if total else 0.0
                    fallback.append((candidate, weight, weight))
            except Exception:
                continue
        try:
            attention = project_attention([row[1] for row in fallback],
                                          policy.attention_budget, policy.attention_penalty)
            fallback = [(candidate, weight, float(score))
                        for (candidate, weight, _), score in zip(fallback, attention)]
        except Exception:
            fallback = []
        return Activation(tuple(fallback), "first_hit_unavailable", diagnostics,
                          tuple(row[0].metadata["evidence_id"] for row in fallback
                                if isinstance(row[0].metadata.get("evidence_id"), str)))


def recall_associative(adapter, cue, policy, *, include_sources=False,
                       source_context_turns=0):
    from .magma_adapter import _source_context_roles, _relation_compatible, _RELATION_RESOLVER

    from .graph_read_query import GraphReadQuery
    profile = getattr(adapter, "associative_read_profile", "first-hit-v1")
    query = (cue.text if isinstance(cue, GraphReadQuery) and profile in ("graph-read-v1", "graph-read-v2")
             else cue.strip() if isinstance(cue, str) else "")

    def empty(code):
        return AssociativeMemoryContext(MemoryContext(query, safe_error_code=code),
                                        SourceMemoryContext(query), safe_error_code=code)

    if not query:
        return empty("invalid_query")
    if adapter.first_hit is None:
        return empty("first_hit_not_configured")
    # Old scores have different units: a non-None BGE/Hindsight floor is an
    # explicit policy conflict, even when its value happens to be zero.
    if policy.final_min_score is not None:
        return empty("first_hit_score_policy_conflict")
    if (type(include_sources) is not bool or type(source_context_turns) is not int
            or not 0 <= source_context_turns <= 4):
        return empty("invalid_source_policy")
    if profile in {"semantic-associative-v1", "semantic-associative-v2", "semantic-associative-v3", "semantic-associative-v4", "semantic-associative-v5"}:
        return empty("semantic_selection_required")
    if profile == "body-recall-v1":
        from ._body_recall import recall_bodies
        return recall_bodies(adapter, query, policy)
    if profile == "calibrated-first-hit-v1":
        from ._calibrated_recall import recall_calibrated
        return recall_calibrated(adapter, query, policy, include_sources=include_sources,
                                 source_context_turns=source_context_turns)
    if profile == "graph-read-v2":
        from ._query_graph_read import activate_query_read
        activation = activate_query_read(adapter, cue)
    elif profile == "graph-read-v1":
        from ._graph_read import activate_read
        activation = activate_read(adapter, cue)
    else:
        activation = adapter._activate_first_hit(query)
    adapter._last_first_hit_diagnostics = dict(activation.diagnostics)
    if profile in ("reliable-v1", "reliable-v2", "graph-read-v1", "graph-read-v2"):
        from ._reliable_recall import pack_reliable
        return pack_reliable(adapter, query, policy, activation,
                             include_sources=include_sources,
                             source_context_turns=source_context_turns,
                             profile="reliable-v2" if profile in ("graph-read-v1", "graph-read-v2") else profile)
    relation_ids = _RELATION_RESOLVER.resolve_query_relations(policy.relation_surfaces or ())
    ranked = sorted(activation.candidates,
                    key=lambda row: (-row[2], -row[1], row[0].metadata.get("evidence_id", "")))
    projected, by_id = {}, {}
    for candidate, _h, attention in ranked:
        if attention <= 0 or not _relation_compatible(candidate, relation_ids):
            continue
        try:
            eid = candidate.metadata["evidence_id"]
            if not isinstance(eid, str) or not eid or not candidate.text.strip():
                continue
            provenance = SourceProvenance(**candidate.metadata["provenance"])
            projected.setdefault(eid, MemoryEvidence(eid, candidate.text,
                                                     candidate.timestamp, provenance))
            by_id.setdefault(eid, candidate)
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    roles = (_source_context_roles(tuple(enumerate(by_id.values())), projected)
             if policy.include_source_context else None)
    evidence, rendered, packed_truncated = bound_evidence_groups(
        [[item] for item in projected.values()], count=policy.max_evidence_items,
        max_chars=policy.max_chars, source_context_roles=roles,
    )
    facts = MemoryContext(query, evidence, rendered,
                          packed_truncated or bool(activation.diagnostics.get("budget_exhausted")),
                          activation.safe_error_code)
    sources = SourceMemoryContext(query)
    if include_sources and evidence:
        refs = []
        for item in evidence:
            meta = by_id[item.evidence_id].metadata
            source_refs = meta.get("source_refs")
            # Legacy grounded spans already store exact offsets and source role.
            if source_refs is None:
                source_refs = [{"turn_id": item.provenance.turn_id,
                                "source_start": meta.get("source_start"),
                                "source_end": meta.get("source_end"),
                                "supporting_span": item.text,
                                "source_role": item.provenance.source_role,
                                "source_timestamp": item.provenance.source_timestamp,
                                "source_timezone": item.provenance.source_timezone,
                                "timezone_source": item.provenance.timezone_source}]
            for ref in source_refs if isinstance(source_refs, (list, tuple)) else ():
                if isinstance(ref, dict):
                    refs.append({**ref, "segment_id": item.provenance.segment_id,
                                 "conversation_id": item.provenance.conversation_id,
                                 "ingestion_version": item.provenance.ingestion_version})
        remaining = policy.max_chars - len(rendered) - bool(rendered)
        if adapter.cold_store is None or not refs:
            sources = SourceMemoryContext(query, safe_error_code="cold_source_unavailable")
        elif remaining <= 0:
            sources = SourceMemoryContext(query, truncated=True,
                                          safe_error_code="cold_source_partial")
        else:
            try:
                sources = adapter.cold_store.read_source_refs(
                    refs, query=query, before=source_context_turns, after=source_context_turns,
                    max_chars=remaining, max_items=policy.max_evidence_items,
                )
            except Exception:
                sources = SourceMemoryContext(query, safe_error_code="cold_source_unavailable")
    combined = "\n".join(part for part in (rendered, sources.rendered_text) if part)
    return AssociativeMemoryContext(
        facts, sources, combined, facts.truncated or sources.truncated,
        facts.safe_error_code or sources.safe_error_code,
    )
