"""Opt-in complete Fact panel for one read-after-retrieval Mind decision."""
from __future__ import annotations

from dataclasses import asdict, replace
from time import perf_counter

from Conversation_Memory.recall.rendering import render_reliable_fact

from ._calibrated_recall import amplitude_weights, parameters
from ._first_hit_read import discover_first_hit_read
from .models import MemoryContext, MemoryEvidence, PreparedRecall, SourceProvenance

PANEL_ITEMS = 20
PANEL_CHARS = 5000
PANEL_BYTES = 20000
FINAL_ITEMS = 3


def prepare_semantic_recall(adapter, query, policy, *, seed_only=False):
    """One index query and one bounded graph read; no final numeric admission."""
    from .magma_adapter import _relation_compatible, _RELATION_RESOLVER

    if not isinstance(query, str) or not query.strip():
        return PreparedRecall(MemoryContext("", safe_error_code="invalid_query"),
                              _semantic_final_limits=(FINAL_ITEMS, min(policy.max_chars, PANEL_CHARS),
                                                      min(policy.max_bytes or PANEL_BYTES, PANEL_BYTES)))
    diagnostics = {"profile": "semantic-associative-v1", "seed_only": seed_only,
                   "provider_requests": 0, "bge_pairs": 0, "candidate_outcomes": {}}
    adapter._last_semantic_read_diagnostics = diagnostics
    if policy.final_min_score is not None:
        return PreparedRecall(MemoryContext(query, safe_error_code="first_hit_score_policy_conflict"))
    started = perf_counter()
    try:
        if getattr(adapter, "_calibrated_index_error", None):
            raise ValueError("calibrated_index_unavailable")
        index = adapter.backend.calibrated_read_index()
        expected = parameters()
        if (index.identity["model"] != expected["model"]
                or index.identity["revision"] != expected["revision"]
                or index.identity["weights_sha256"] != expected["model_weights_sha256"]
                or index.identity["text_view"] != expected["text_view"]):
            raise ValueError("calibrated_index_fingerprint_mismatch")
        view = adapter.backend.first_hit_view()
        version = view.version
        bounds = replace(adapter.first_hit,
                         max_seeds=min(adapter.first_hit.max_seeds, 5),
                         max_nodes=min(adapter.first_hit.max_nodes, 64),
                         max_edges=min(adapter.first_hit.max_edges, 256))
        refs = tuple(adapter.backend.resolve_target_entity_refs(query, limit=bounds.max_seeds))
        hits, search, qvector = index.search(query, target_entity_refs=refs, limit=PANEL_ITEMS)
        diagnostics["search"] = search
        diagnostics["index_query_seconds"] = perf_counter() - started
        ranked = sorted((hit for hit in hits if view.eligible(hit.node_id)),
                        key=lambda hit: (-max(0.0, hit.cosine), view.stable_id(hit.node_id), hit.node_id))
        ranked = ranked[:bounds.max_seeds]
        strengths = [max(0.0, min(1.0, hit.cosine)) for hit in ranked]
        weights = amplitude_weights(strengths)
        seeds = tuple((hit.node_id, weight) for hit, weight in zip(ranked, weights) if weight > 0)
        diagnostics["seeds"] = [{"node_id": hit.node_id, "cosine": hit.cosine, "weight": weight}
                                for hit, weight in zip(ranked, weights)]
        graph_started = perf_counter()
        result = discover_first_hit_read(
            view, seeds, replace(bounds, max_edges=0) if seed_only else bounds)
        adapter._last_first_hit_snapshot = result
        diagnostics["graph_seconds"] = perf_counter() - graph_started
        diagnostics["first_hit"] = dict(result.stats)
        diagnostics["read_arcs"] = result.read_arcs
        # Keep every legal seed, then alternate independent index hits and
        # graph visits. Neither the old 0.6 output quota nor its logistic
        # threshold decides what the semantic selector is allowed to see.
        seed_ids = [hit.node_id for hit in ranked if hit.node_id in result.h]
        search_ids = [hit.node_id for hit in hits if hit.node_id not in seed_ids]
        graph_ids = sorted((node_id for node_id in result.fact_ids if node_id not in seed_ids),
                           key=lambda node_id: (-result.h[node_id], view.stable_id(node_id), node_id))
        order = list(seed_ids)
        for position in range(max(len(search_ids), len(graph_ids))):
            if position < len(search_ids):
                order.append(search_ids[position])
            if position < len(graph_ids):
                order.append(graph_ids[position])
        order = tuple(dict.fromkeys(order))
        diagnostics["index_hit_ids"] = tuple(hit.node_id for hit in hits)
        diagnostics["graph_fact_ids"] = tuple(result.fact_ids)
        support = index.score_nodes(qvector, query, result.fact_ids)
        node_positions = {node_id: position for position, node_id in enumerate(result.node_ids)}
        diagnostics["graph_support"] = {node_id: {
            "h": result.h[node_id], "b": float(result.b[node_positions[node_id]]),
            "cosine": support.get(node_id, (None, None))[0],
            "lexical": support.get(node_id, (None, None))[1],
        } for node_id in result.fact_ids}
        view.check(version)
    except Exception as error:
        code = str(error) if str(error).startswith("calibrated_") else "semantic_read_unavailable"
        diagnostics["failure"] = code
        return PreparedRecall(MemoryContext(query, safe_error_code=code))

    relation_ids = _RELATION_RESOLVER.resolve_query_relations(policy.relation_surfaces or ())
    items, blocks = [], []
    max_items = min(policy.max_evidence_items, PANEL_ITEMS)
    max_chars = min(policy.max_chars, PANEL_CHARS)
    max_bytes = min(policy.max_bytes or PANEL_BYTES, PANEL_BYTES)
    diagnostics["enumerated_node_ids"] = len(order)
    diagnostics["candidate_fetches"] = 0
    for node_id in order:
        eid = None
        try:
            diagnostics["candidate_fetches"] += 1
            candidate = adapter.backend.first_hit_candidate(node_id)
            if candidate is None or not _relation_compatible(candidate, relation_ids):
                continue
            eid = candidate.metadata.get("evidence_id")
            if not isinstance(eid, str) or not eid or not isinstance(candidate.text, str) or not candidate.text.strip():
                raise ValueError("invalid_fact")
            provenance = SourceProvenance(**candidate.metadata["provenance"])
            if not all(isinstance(value, str) and value.strip() for value in asdict(provenance).values()):
                raise ValueError("invalid_provenance")
            if any(item.evidence_id == eid for item in items):
                continue
            item = MemoryEvidence(eid, candidate.text, candidate.timestamp, provenance)
            block = render_reliable_fact(item, f"M{len(items)+1}",
                                         include_source_context=policy.include_source_context)
            proposed = "\n".join((*blocks, block))
            reason = ("item_budget" if len(items) >= max_items else
                      "char_budget" if len(proposed) > max_chars else
                      "byte_budget" if len(proposed.encode("utf-8")) > max_bytes else None)
            diagnostics["candidate_outcomes"][eid] = {"node_id": node_id,
                "origin": "seed" if node_id in seed_ids else "index" if node_id in search_ids else "graph",
                "reason": reason or "panel"}
            if reason:
                continue
            items.append(item)
            blocks.append(block)
        except (KeyError, TypeError, ValueError, AttributeError):
            diagnostics["candidate_outcomes"][str(eid)] = {"node_id": node_id, "reason": "invalid_fact"}
    diagnostics["panel_items"] = len(items)
    diagnostics["panel_chars"] = len("\n".join(blocks))
    diagnostics["panel_bytes"] = len("\n".join(blocks).encode("utf-8"))
    diagnostics["panel_seconds"] = perf_counter() - started
    try:
        view.check(version)
        check_index = getattr(index, "_check", None)
        if callable(check_index):
            check_index()
    except Exception:
        diagnostics["failure"] = "calibrated_index_stale"
        return PreparedRecall(MemoryContext(query, safe_error_code="calibrated_index_stale"))
    context = MemoryContext(query, tuple(items), "\n".join(blocks),
                            any(row["reason"].endswith("budget") for row in diagnostics["candidate_outcomes"].values()))
    return PreparedRecall(context, tuple(blocks), tuple((item.evidence_id, ()) for item in items),
                          (FINAL_ITEMS, max_chars, max_bytes))
