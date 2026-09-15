"""Opt-in complete source context with optional Formation-derived navigation.

Facts and occurrences only locate dialogue; they do not become Answer evidence.
"""
from __future__ import annotations
from dataclasses import asdict, replace
from copy import deepcopy
import json
from recall.hindsight_scoring import score_hindsight_post_rerank
from .models import SourceMemoryContext
from ._source_backend import SOURCE_DENSE_UNAVAILABLE
from .source_memory import _excerpt, render_source

NAVIGATION_LIMIT = 20
REFERENCE_LIMIT = 80


def _navigation(memory, query, policy):
    trace = {"facts": (), "mentions": (), "errors": []}
    references, mention_references = [], []
    bounded = replace(policy, max_nodes=min(policy.max_nodes, NAVIGATION_LIMIT),
                      max_evidence_items=NAVIGATION_LIMIT)
    try:
        from .magma_adapter import _user_self_binding_enabled, classify_target_entity_ref
        targets = ()
        if _user_self_binding_enabled():
            targets = tuple(memory.backend.resolve_target_entity_refs(query, limit=NAVIGATION_LIMIT))
            current_user = classify_target_entity_ref(query)
            if current_user is not None:
                targets = tuple(dict.fromkeys((current_user, *targets)))[:NAVIGATION_LIMIT]
        facts = tuple(memory.backend.recall(query, bounded, target_entity_refs=tuple(targets)))
        trace["facts"] = facts[:NAVIGATION_LIMIT]
        for fact in trace["facts"]:
            for ref in fact.metadata.get("source_refs", ()):
                if len(references) >= REFERENCE_LIMIT:
                    break
                references.append({**fact.metadata.get("provenance", {}), **ref})
    except Exception:
        trace["errors"].append("fact_navigation_unavailable")
    try:
        context = memory.recall_mentions(query, limit=NAVIGATION_LIMIT)
        trace["mentions"] = context.mentions
        if context.safe_error_code:
            trace["errors"].append("mention_navigation_unavailable")
        for mention in context.mentions:
            mention_references.append({**asdict(mention.provenance),
                "supporting_span": mention.surface,
                "source_start": mention.source_start, "source_end": mention.source_end})
    except Exception:
        trace["errors"].append("mention_navigation_unavailable")
    # Alternate provenance channels; a prolific fact cannot hide all occurrences.
    combined = []
    for index in range(max(len(references), len(mention_references))):
        for channel in (references, mention_references):
            if index < len(channel) and len(combined) < REFERENCE_LIMIT:
                combined.append(channel[index])
    trace["references"] = tuple(combined)
    return combined, trace


def _fuse(channels, limit):
    """Existing rank-one RRF(k=60), over exact source identities."""
    scores, items = {}, {}
    for channel in channels:
        seen = set()
        for rank, item in enumerate(channel, 1):
            eid = item.metadata["evidence_id"]
            if eid in seen:
                continue
            seen.add(eid)
            items.setdefault(eid, item)
            scores[eid] = scores.get(eid, 0.0) + 1.0 / (60 + rank)
    order = {eid: i for i, eid in enumerate(items)}
    return tuple(items[eid] for eid in sorted(items,
        key=lambda eid: (-scores[eid], order[eid]))[:limit])


def render_context(candidates):
    """Merge exact blocks into whole turns; share only the session header."""
    by_turn = {}
    for candidate in candidates:
        item = _excerpt(candidate)
        p = item.provenance
        by_turn.setdefault((p.segment_id, p.turn_id), []).append(item)
    turns = []
    for blocks in by_turn.values():
        blocks.sort(key=lambda item: item.source_start)
        first, position = blocks[0], 0
        for item in blocks:
            if (item.source_start != position or item.provenance != first.provenance
                    or item.turn_length != first.turn_length or item.turn_index != first.turn_index):
                raise ValueError("source_context_incomplete_turn")
            position = item.source_end
        if position != first.turn_length:
            raise ValueError("source_context_incomplete_turn")
        turns.append((first, "".join(item.text for item in blocks)))
    if not turns:
        return ""
    # Backend provides session-segment order and original within-segment order.
    session = turns[0][0].provenance.conversation_id
    if any(item.provenance.conversation_id != session for item, _ in turns):
        raise ValueError("source_context_mixed_sessions")
    compact = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    parts = ["[SOURCE SESSION " + compact(session) + "]"]
    for item, text in turns:
        p = item.provenance
        header = {"segment": p.segment_id, "turn": item.turn_index,
                  "spoken_at": p.source_timestamp, "timezone": p.source_timezone}
        role = "USER" if p.source_role == "user" else "LUMINA"
        parts.append("[" + role + " " + compact(header) + "]\n" + text)
    return "\n".join(parts)


def recall_source_context(adapter, query, policy, *, fact_memory=None):
    """Rank source anchors, then pack complete indexed original sessions.

    Independent search keeps the original 20-node search budget. max_nodes also
    bounds source dereferences, including navigation and parents. Parents that
    exceed reading or Answer budgets are omitted whole. No fact_memory is the
    identical navigation-off ablation; it gets no extra search budget.
    """
    adapter.last_source_context_read = {}
    if not isinstance(query, str) or not query.strip():
        return SourceMemoryContext(query if isinstance(query, str) else "",
                                   safe_error_code="invalid_query")
    query = query.strip()
    trace = {"navigation_enabled": fact_memory is not None}
    try:
        search_policy = replace(policy, max_nodes=min(policy.max_nodes, NAVIGATION_LIMIT))
        independent = tuple(adapter.backend.source_candidates(query, search_policy))
        dense_error = (SOURCE_DENSE_UNAVAILABLE if
            getattr(adapter.backend, "last_source_stats", {}).get("dense_error_code")
            == SOURCE_DENSE_UNAVAILABLE else None)
        known = {item.metadata["evidence_id"]: item for item in independent}
        located = ()
        if fact_memory is not None:
            references, navigation = _navigation(fact_memory, query, search_policy)
            trace["navigation"] = navigation
            try:
                located = tuple(adapter.backend.source_locate(references,
                    limit=NAVIGATION_LIMIT, max_refs=REFERENCE_LIMIT,
                    known_candidates=known, max_nodes=policy.max_nodes))
                trace["locator_stats"] = deepcopy(getattr(adapter.backend, "last_source_stats", {}).get("last_locate", {}))
            except Exception:
                navigation["errors"].append("source_navigation_unavailable")
        anchors = _fuse((independent, located), min(policy.top_k, policy.max_nodes))
        trace.update(independent_anchors=independent, navigation_anchors=located, anchors=anchors)
        if not anchors:
            adapter.last_source_context_read = trace
            return SourceMemoryContext(query, truncated=bool(dense_error), safe_error_code=dense_error)
        scorer = adapter._get_bge_reranker()
        if scorer is None:
            raise ValueError("source_reranker_unavailable")
        scoring = tuple(render_source(_excerpt(item)) for item in anchors)
        if not all(scorer.fits_pair(query, text) for text in scoring):
            raise ValueError("source_reranker_window_exceeded")
        raw = scorer.score(query, scoring)
        from .magma_adapter import _candidate_snapshot_reference_time
        times = tuple(item.metadata["provenance"]["source_timestamp"] for item in anchors)
        scores = score_hindsight_post_rerank(raw, times,
            now=_candidate_snapshot_reference_time(tuple(
                item.metadata["provenance"]["source_timestamp"] for item in known.values())))
        ranked = sorted(range(len(anchors)), key=lambda i: (-scores[i].final_score, i))
        seen_sessions, selected, groups, parts = set(), [], [], []
        omitted, truncated = [], bool(dense_error)
        parent_reads = []
        for index in ranked:
            anchor = anchors[index]
            session = anchor.metadata["provenance"]["conversation_id"]
            if session in seen_sessions:
                continue
            seen_sessions.add(session)
            if policy.final_min_score is not None and scores[index].final_score < policy.final_min_score:
                continue
            group = tuple(adapter.backend.source_parent(anchor, limit=policy.max_nodes,
                known_candidates=known, max_nodes=policy.max_nodes))
            parent_stats = deepcopy(getattr(adapter.backend, "last_source_stats", {}).get("last_parent", {}))
            parent_reads.append(parent_stats)
            if not group:
                truncated = True
                omitted.append({"session": session, "reason": parent_stats.get("reason") or "parent_unavailable_or_read_budget"})
                continue
            text = render_context(group)
            groups.append(group)
            if (len(selected) + len(group) > policy.max_evidence_items
                    or len("\n".join([*parts, text])) > policy.max_chars):
                truncated = True
                omitted.append({"session": session, "reason": "answer_context_budget"})
                continue
            selected.extend(_excerpt(item) for item in group)
            parts.append(text)
        trace.update(candidates=tuple(known.values()),
            candidate_meaning="all materialized source blocks, including invalid locator inspections and unpacked parents",
            parent_reads=tuple(parent_reads), groups=tuple(groups), scoring_texts=scoring,
            raw_scores=tuple(raw), scores=tuple(asdict(score) for score in scores),
            omitted_parents=tuple(omitted), selected_source_chars=sum(len(item.text) for item in selected),
            rendered_chars=len("\n".join(parts)), bge_silent_truncations=0)
        adapter.last_source_context_read = trace
        return SourceMemoryContext(query, tuple(selected), "\n".join(parts), truncated, dense_error)
    except Exception:
        trace["error"] = "source_context_unavailable"
        adapter.last_source_context_read = trace
        return SourceMemoryContext(query, safe_error_code="source_context_unavailable")
