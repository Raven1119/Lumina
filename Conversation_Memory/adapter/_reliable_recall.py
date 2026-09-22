"""Fixed direct/associated packing over one unchanged FirstHit activation.

The direct share is an engineering starting value, not a confidence estimate.
Every visible range consumes its actual item/character/UTF-8 budget.

`reliable-v1`: source expansion replaces complete Fact views only after their
base selection is protected; upgraded source items share the Fact item cap.
`reliable-v2`: a selected Fact always renders its canonical body; sources are
a bounded supplement with a separate item allowance of
`policy.max_evidence_items`, sharing only the character/byte budget with the
always-visible bodies. Selection is identical for both profiles.
"""
from __future__ import annotations

from dataclasses import asdict
from math import ceil, isfinite
from time import perf_counter

from Conversation_Memory.recall.rendering import render_reliable_fact, render_reliable_source

from .models import (
    AssociativeMemoryContext, AssociativeSelection, MemoryContext,
    MemoryEvidence, SourceMemoryContext, SourceProvenance,
)

DIRECT_SHARE = 0.6
_MAX_SOURCE_REFS = 64


def _measure(text):
    return len(text), len(text.encode("utf-8"))


def _budget_reason(text, items, *, count, chars, bytes_limit):
    if items > count:
        return "item_budget"
    length, byte_length = _measure(text)
    if length > chars:
        return "char_budget"
    if bytes_limit is not None and byte_length > bytes_limit:
        return "byte_budget"
    return None


def _references(item, candidate):
    metadata = candidate.metadata
    refs = metadata.get("source_refs")
    if refs is None:
        refs = [{"turn_id": item.provenance.turn_id,
                 "source_start": metadata.get("source_start"),
                 "source_end": metadata.get("source_end"),
                 "supporting_span": item.text,
                 "source_role": item.provenance.source_role,
                 "source_timestamp": item.provenance.source_timestamp,
                 "source_timezone": item.provenance.source_timezone,
                 "timezone_source": item.provenance.timezone_source}]
    if not isinstance(refs, (list, tuple)) or not refs or any(not isinstance(r, dict) for r in refs):
        return ()
    return tuple({**ref, "segment_id": item.provenance.segment_id,
                  "conversation_id": item.provenance.conversation_id,
                  "ingestion_version": item.provenance.ingestion_version} for ref in refs)


def _reference_key(ref):
    # All declared provenance participates in deduplication and validation.
    return tuple((key, repr(value)) for key, value in sorted(ref.items()))


def _cover(ref, source):
    """A returned neighboring range does not validate a different bad citation."""
    try:
        p = source.provenance
        start, end = ref["source_start"], ref["source_end"]
        if (type(start) is not int or type(end) is not int or not start < end
                or not source.source_start <= start < end <= source.source_end
                or ref["segment_id"] != p.segment_id or ref["turn_id"] != p.turn_id):
            return False
        if any(key in ref and ref[key] != getattr(p, key) for key in (
                "conversation_id", "source_role", "source_timestamp", "source_timezone", "timezone_source")):
            return False
        return ("supporting_span" not in ref or ref["supporting_span"] ==
                source.text[start-source.source_start:end-source.source_start])
    except (AttributeError, KeyError, TypeError):
        return False


def _views(selected, source_views, source_items, policy):
    """Serialize exactly what is visible, with shared ranges printed once."""
    wanted = {sid for ids in source_views.values() for sid in ids}
    visible_sources = tuple(s for s in source_items if s.evidence_id in wanted)
    handles = {s.evidence_id: f"S{i+1}" for i, s in enumerate(visible_sources)}
    sources = {s.evidence_id: s for s in visible_sources}
    seen, parts, fact_parts, source_parts, selections = set(), [], [], [], []
    fact_count = 0
    for index, (item, channel) in enumerate(selected):
        ids = source_views.get(item.evidence_id, ())
        handle = f"M{index+1}"
        if not ids:
            block = render_reliable_fact(item, handle, include_source_context=policy.include_source_context)
            parts.append(block)
            fact_parts.append(block)
            fact_count += 1
            selections.append(AssociativeSelection(item.evidence_id, channel, "fact"))
            continue
        parts.append("[" + handle + " sources=" + ",".join(handles[sid] for sid in ids) + "]")
        for sid in ids:
            if sid in seen:
                continue
            seen.add(sid)
            block = render_reliable_source(sources[sid], handles[sid], include_source_context=policy.include_source_context)
            parts.append(block)
            source_parts.append(block)
        selections.append(AssociativeSelection(item.evidence_id, channel, "sources", ids))
    return ("\n".join(parts), "\n".join(fact_parts), "\n".join(source_parts),
            visible_sources, tuple(selections), fact_count + len(visible_sources))


def _views_v2(selected, source_views, source_items, policy):
    """reliable-v2: the canonical body is always visible; sources supplement it."""
    wanted = {sid for ids in source_views.values() for sid in ids}
    visible_sources = tuple(s for s in source_items if s.evidence_id in wanted)
    handles = {s.evidence_id: f"S{i+1}" for i, s in enumerate(visible_sources)}
    sources = {s.evidence_id: s for s in visible_sources}
    seen, parts, fact_parts, source_parts, selections = set(), [], [], [], []
    for index, (item, channel) in enumerate(selected):
        handle = f"M{index+1}"
        block = render_reliable_fact(item, handle, include_source_context=policy.include_source_context)
        parts.append(block)
        fact_parts.append(block)
        ids = source_views.get(item.evidence_id, ())
        if not ids:
            selections.append(AssociativeSelection(item.evidence_id, channel, "fact"))
            continue
        parts.append("[" + handle + " sources=" + ",".join(handles[sid] for sid in ids) + "]")
        for sid in ids:
            if sid in seen:
                continue
            seen.add(sid)
            block = render_reliable_source(sources[sid], handles[sid], include_source_context=policy.include_source_context)
            parts.append(block)
            source_parts.append(block)
        selections.append(AssociativeSelection(item.evidence_id, channel, "fact+sources", ids))
    return ("\n".join(parts), "\n".join(fact_parts), "\n".join(source_parts),
            visible_sources, tuple(selections), len(visible_sources))


def _source_refs_read(adapter, query, policy, refs, source_context_turns, diagnostics):
    """One bounded owner read over exact refs; ((), None) means the read failed."""
    # Cold's legacy transport renders full private provenance headers. Reserve a
    # separately metered bounded transport allowance; the visible view below is
    # still constrained by the caller's unchanged total character/byte budget.
    transport_items = min(256, _MAX_SOURCE_REFS * (1 + 2 * source_context_turns))
    transport_chars = policy.max_chars + 1024 * transport_items
    transport_bytes = (policy.max_bytes or 4 * policy.max_chars) + 4096 * transport_items
    diagnostics["cold_read_attempts"] = 1
    diagnostics["cold_requested_refs"] = len(refs)
    diagnostics["cold_transport_limits"] = {
        "max_refs": _MAX_SOURCE_REFS, "max_items": transport_items,
        "max_chars": transport_chars, "max_bytes": transport_bytes,
    }
    read_started = perf_counter()
    try:
        context = adapter.cold_store.read_source_refs(
            refs, query=query, before=source_context_turns, after=source_context_turns,
            whole_turns=bool(source_context_turns), max_refs=_MAX_SOURCE_REFS,
            max_items=transport_items, max_chars=transport_chars, max_bytes=transport_bytes,
        )
        source_items = tuple(context.evidence)
        # Exact occurrence IDs are emitted by the owner. Never join disjoint
        # ranges or merge turns in order to make a source package cost one item.
        source_items = tuple({s.evidence_id: s for s in source_items}.values())
        diagnostics["cold_returned_items"] = len(source_items)
        diagnostics["cold_returned_chars"], diagnostics["cold_returned_bytes"] = _measure(context.rendered_text)
        return source_items, context
    except Exception:
        return (), None
    finally:
        diagnostics["cold_read_seconds"] = perf_counter() - read_started


def _source_views(adapter, query, policy, selected, candidates, source_context_turns, diagnostics):
    """One bounded owner read; failed optional upgrades never remove a Fact."""
    per_fact = {item.evidence_id: _references(item, candidates[item.evidence_id]) for item, _ in selected}
    refs = list({_reference_key(ref): ref for values in per_fact.values() for ref in values}.values())
    outcomes = diagnostics["source_outcomes"]
    if adapter.cold_store is None or not refs:
        for item, _ in selected:
            outcomes[item.evidence_id] = "cold_source_unavailable" if adapter.cold_store is None else "source_refs_invalid"
        return {}, (), "cold_source_unavailable", True
    source_items, context = _source_refs_read(adapter, query, policy, refs,
                                              source_context_turns, diagnostics)
    if context is None:
        for item, _ in selected:
            outcomes[item.evidence_id] = "cold_source_unavailable"
        return {}, (), "cold_source_unavailable", True
    groups = {}
    for item, _ in selected:
        item_refs = per_fact[item.evidence_id]
        matches = [next((s for s in source_items if _cover(ref, s)), None) for ref in item_refs]
        if not item_refs or any(s is None for s in matches):
            outcomes[item.evidence_id] = context.safe_error_code or "source_support_incomplete"
            continue
        required = {s.evidence_id for s in matches}
        anchors = {(s.provenance.segment_id, s.turn_index) for s in matches}
        ids = tuple(s.evidence_id for s in source_items if s.evidence_id in required or
                    source_context_turns and any(s.provenance.segment_id == segment and
                    abs(s.turn_index-index) <= source_context_turns for segment, index in anchors))
        groups.setdefault(ids, []).append(item.evidence_id)
    views = {}
    # A shared two-turn package for two Facts is considered jointly. An
    # intermediate one-Fact replacement must not falsely exceed the item cap.
    for ids, evidence_ids in groups.items():
        proposed = {**views, **{eid: ids for eid in evidence_ids}}
        rendered, _, _, _, _, items = _views(selected, proposed, source_items, policy)
        reason = _budget_reason(rendered, items, count=policy.max_evidence_items,
                                chars=policy.max_chars, bytes_limit=policy.max_bytes)
        for eid in evidence_ids:
            outcomes[eid] = "source_" + reason if reason else "sources_visible"
        if reason is None:
            views = proposed
    incomplete = len(views) != len(selected) or bool(context.truncated or context.safe_error_code)
    error = context.safe_error_code or ("cold_source_partial" if incomplete else None)
    return views, source_items, error, incomplete


def _source_views_v2(adapter, query, policy, selected, candidates, source_context_turns, diagnostics):
    """reliable-v2: a bounded supplement with its own source item allowance.

    Up to `policy.max_evidence_items` distinct source items are charged to a
    separate allowance (parity with the first-hit-v1 appended source read);
    characters/bytes stay shared with the always-visible bodies. A group whose
    supplement does not fit keeps complete bodies only — never handle-only.
    """
    per_fact = {item.evidence_id: _references(item, candidates[item.evidence_id]) for item, _ in selected}
    refs = list({_reference_key(ref): ref for values in per_fact.values() for ref in values}.values())
    outcomes = diagnostics["source_outcomes"]
    diagnostics["source_reserved_items"] = policy.max_evidence_items
    if adapter.cold_store is None or not refs:
        for item, _ in selected:
            outcomes[item.evidence_id] = "cold_source_unavailable" if adapter.cold_store is None else "source_refs_invalid"
        return {}, (), "cold_source_unavailable", True
    source_items, context = _source_refs_read(adapter, query, policy, refs,
                                              source_context_turns, diagnostics)
    if context is None:
        for item, _ in selected:
            outcomes[item.evidence_id] = "cold_source_unavailable"
        return {}, (), "cold_source_unavailable", True
    groups = {}
    for item, _ in selected:
        item_refs = per_fact[item.evidence_id]
        matches = [next((s for s in source_items if _cover(ref, s)), None) for ref in item_refs]
        if not item_refs or any(s is None for s in matches):
            outcomes[item.evidence_id] = context.safe_error_code or "source_support_incomplete"
            continue
        required = {s.evidence_id for s in matches}
        anchors = {(s.provenance.segment_id, s.turn_index) for s in matches}
        ids = tuple(s.evidence_id for s in source_items if s.evidence_id in required or
                    source_context_turns and any(s.provenance.segment_id == segment and
                    abs(s.turn_index-index) <= source_context_turns for segment, index in anchors))
        groups.setdefault(ids, []).append(item.evidence_id)
    views = {}
    # Shared packages are still accepted or rejected jointly, but the joint
    # fit is measured against the separate source item allowance plus the
    # remaining shared character/byte budget; rejection never removes a body.
    for ids, evidence_ids in groups.items():
        proposed = {**views, **{eid: ids for eid in evidence_ids}}
        rendered, _, _, _, _, source_items_used = _views_v2(selected, proposed, source_items, policy)
        reason = _budget_reason(rendered, source_items_used, count=policy.max_evidence_items,
                                chars=policy.max_chars, bytes_limit=policy.max_bytes)
        for eid in evidence_ids:
            outcomes[eid] = "source_" + reason if reason else "sources_visible"
        if reason is None:
            views = proposed
    incomplete = len(views) != len(selected) or bool(context.truncated or context.safe_error_code)
    error = context.safe_error_code or ("cold_source_partial" if incomplete else None)
    return views, source_items, error, incomplete


def pack_reliable(adapter, query, policy, activation, *, include_sources=False, source_context_turns=0,
                  profile="reliable-v1"):
    from .first_hit import project_attention
    from .magma_adapter import _relation_compatible, _RELATION_RESOLVER

    started = perf_counter()
    diagnostics = {"profile": profile, "direct_share": DIRECT_SHARE,
                   "candidate_outcomes": {}, "source_outcomes": {}, "cold_read_attempts": 0,
                   "cold_read_seconds": 0.0, "bge_pairs": 0, "provider_requests": 0}
    relation_ids = _RELATION_RESOLVER.resolve_query_relations(policy.relation_surfaces or ())
    read_selection = activation.read_selection
    if read_selection is not None:
        diagnostics["read_profile"] = activation.diagnostics.get("profile", "graph-read-v1")
    legal, candidates, masses = {}, {}, {}
    for position, (candidate, h, _attention) in enumerate(activation.candidates):
        eid = candidate.metadata.get("evidence_id") if isinstance(candidate.metadata, dict) else None
        key = eid if isinstance(eid, str) and eid else f"invalid-candidate-{position}"
        if read_selection is not None and not read_selection.permits(eid):
            diagnostics["candidate_outcomes"][key] = {"reason": "explicit_condition_unmatched_or_unknown"}
            continue
        if not _relation_compatible(candidate, relation_ids):
            diagnostics["candidate_outcomes"][key] = {"reason": "relation_filter"}
            continue
        try:
            if not isinstance(eid, str) or not eid or not isinstance(candidate.text, str) or not candidate.text.strip():
                raise ValueError()
            provenance = SourceProvenance(**candidate.metadata["provenance"])
            if not all(isinstance(value, str) and value.strip() for value in asdict(provenance).values()):
                raise ValueError()
            if not isfinite(h) or h < 0:
                raise ValueError()
            legal.setdefault(eid, MemoryEvidence(eid, candidate.text, candidate.timestamp, provenance))
            candidates.setdefault(eid, candidate)
            masses.setdefault(eid, h)
        except (AttributeError, KeyError, TypeError, ValueError):
            diagnostics["candidate_outcomes"][key] = {"reason": "invalid_fact"}
    direct = list(dict.fromkeys(eid for eid in activation.seed_fact_ids if eid in legal))
    direct_set = set(direct)
    associated = [eid for eid in legal if eid not in direct_set]
    attention_started = perf_counter()
    scores = project_attention([masses[eid] for eid in associated], adapter.first_hit.attention_budget,
                               adapter.first_hit.attention_penalty)
    diagnostics["association_projection_seconds"] = perf_counter() - attention_started
    association_scores = dict(zip(associated, scores))
    associated.sort(key=lambda eid: (-association_scores[eid], -masses[eid], eid))
    for eid in associated:
        if association_scores[eid] <= 0:
            diagnostics["candidate_outcomes"][eid] = {"channel": "associated", "reason": "competition_zero"}
    associated = [eid for eid in associated if association_scores[eid] > 0]
    count = policy.max_evidence_items
    direct_count = ceil(DIRECT_SHARE * count)
    direct_chars = ceil(DIRECT_SHARE * policy.max_chars)
    direct_bytes = ceil(DIRECT_SHARE * policy.max_bytes) if policy.max_bytes is not None else None
    diagnostics.update(direct_candidates=len(direct), associated_candidates=len(associated),
                       direct_reserved_items=direct_count, direct_reserved_chars=direct_chars,
                       direct_reserved_bytes=direct_bytes)
    selected, selected_ids = [], set()

    def take(pool, channel, item_cap, char_cap, byte_cap, *, protected=False):
        local_blocks, local_count = [], 0
        remaining = list(pool)
        pool_order = {eid: i for i, eid in enumerate(pool)}
        while remaining:
            if read_selection is not None:
                remaining.sort(key=lambda eid: (*read_selection.priority(eid, selected_ids), pool_order[eid]))
            eid = remaining.pop(0)
            if eid in selected_ids:
                continue
            item = legal[eid]
            block = render_reliable_fact(item, f"M{len(selected)+1}", include_source_context=policy.include_source_context)
            local = "\n".join((*local_blocks, block))
            current = [render_reliable_fact(value, f"M{i+1}", include_source_context=policy.include_source_context)
                       for i, (value, _) in enumerate(selected)]
            proposed = "\n".join((*current, block))
            reason = _budget_reason(local, local_count+1, count=item_cap, chars=char_cap, bytes_limit=byte_cap)
            reason = reason or _budget_reason(proposed, len(selected)+1, count=count,
                                             chars=policy.max_chars, bytes_limit=policy.max_bytes)
            if reason:
                continue
            selected.append((item, channel))
            selected_ids.add(eid)
            local_blocks.append(block)
            local_count += 1
            diagnostics["candidate_outcomes"][eid] = {"channel": channel, "reason": "selected",
                                                       "protected_direct": protected}

    take(direct, "direct", direct_count, direct_chars, direct_bytes, protected=True)
    # Query-driven joins reserve the complete missing bundle jointly. Existing
    # protected direct items are immutable, charged only once, and never
    # replaced. All members must also survive the same Fact legality and
    # association attention rules above. v1 has no bundles and is unchanged.
    bundles = getattr(read_selection, "bundles", ())
    if bundles:
        available = set(direct) | set(associated)
        attempts = []
        for bundle in sorted(bundles, key=lambda ids: -len(selected_ids.intersection(ids))):
            missing = tuple(eid for eid in bundle if eid not in selected_ids)
            if not missing:
                break
            if not set(bundle).issubset(available):
                attempts.append({"bundle": bundle, "reason": "member_not_eligible_for_output"})
                continue
            proposed = [*selected, *((legal[eid], "direct" if eid in direct_set else "associated")
                                     for eid in missing)]
            rendered = "\n".join(render_reliable_fact(item, f"M{i+1}",
                                  include_source_context=policy.include_source_context)
                                 for i, (item, _) in enumerate(proposed))
            reason = _budget_reason(rendered, len(proposed), count=count,
                                    chars=policy.max_chars, bytes_limit=policy.max_bytes)
            attempts.append({"bundle": bundle, "reason": reason or "selected"})
            if reason:
                continue
            selected[:] = proposed
            selected_ids.update(missing)
            for eid in missing:
                diagnostics["candidate_outcomes"][eid] = {
                    "channel": "direct" if eid in direct_set else "associated",
                    "reason": "selected", "protected_direct": False, "bundle_member": True}
            break
        diagnostics["bundle_packing_attempts"] = attempts
    take(associated, "associated", count-direct_count, policy.max_chars-direct_chars,
         policy.max_bytes-direct_bytes if policy.max_bytes is not None else None)
    # Unused quota can be borrowed, without replacing either already selected set.
    take(direct, "direct", count, policy.max_chars, policy.max_bytes)
    take(associated, "associated", count, policy.max_chars, policy.max_bytes)
    selection_error = read_selection.finish(selected_ids) if read_selection is not None else None
    if read_selection is not None:
        diagnostics["graph_selection"] = dict(read_selection.diagnostics)
        adapter._last_first_hit_diagnostics.update(read_selection.diagnostics)
    base, _, _, _, _, _ = _views(selected, {}, (), policy)
    packing_omitted = False
    for channel, pool in (("direct", direct), ("associated", associated)):
        for eid in pool:
            if eid in selected_ids:
                continue
            block = render_reliable_fact(legal[eid], f"M{len(selected)+1}", include_source_context=policy.include_source_context)
            reason = _budget_reason("\n".join(p for p in (base, block) if p), len(selected)+1,
                                    count=count, chars=policy.max_chars, bytes_limit=policy.max_bytes)
            diagnostics["candidate_outcomes"][eid] = {"channel": channel, "reason": reason or "not_selected"}
            packing_omitted = True
    source_views, source_items, source_error, source_truncated = {}, (), None, False
    if include_sources and selected:
        if profile == "reliable-v2":
            source_views, source_items, source_error, source_truncated = _source_views_v2(
                adapter, query, policy, selected, candidates, source_context_turns, diagnostics)
        else:
            source_views, source_items, source_error, source_truncated = _source_views(
                adapter, query, policy, selected, candidates, source_context_turns, diagnostics)
    if profile == "reliable-v2":
        rendered, fact_rendered, source_rendered, visible_sources, selections, source_items_used = _views_v2(
            selected, source_views, source_items, policy)
        visible_items = len(selected) + source_items_used
        diagnostics["visible_source_items"] = source_items_used
    else:
        rendered, fact_rendered, source_rendered, visible_sources, selections, visible_items = _views(
            selected, source_views, source_items, policy)
    diagnostics["visible_items"] = visible_items
    diagnostics["visible_chars"], diagnostics["visible_bytes"] = _measure(rendered)
    diagnostics["selected_direct"] = sum(channel == "direct" for _, channel in selected)
    diagnostics["selected_associated"] = sum(channel == "associated" for _, channel in selected)
    diagnostics["packing_seconds"] = perf_counter() - started
    adapter._last_reliable_recall_diagnostics = diagnostics
    truncated = packing_omitted or bool(activation.diagnostics.get("budget_exhausted")) or bool(selection_error)
    facts = MemoryContext(query, tuple(item for item, _ in selected), fact_rendered,
                          truncated, activation.safe_error_code or selection_error)
    sources = SourceMemoryContext(query, visible_sources, source_rendered, source_truncated, source_error)
    return AssociativeMemoryContext(facts, sources, rendered, truncated or source_truncated,
                                    activation.safe_error_code or selection_error or source_error, selections)
