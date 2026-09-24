"""V6 read candidate: immutable base, local FirstHit, and one late supplement."""
from __future__ import annotations

from dataclasses import replace
import re

from Conversation_Memory.recall.rendering import render_reliable_fact

from ._calibrated_recall import amplitude_weights
from ._first_hit_read import discover_first_hit_read
from ._semantic_recall_v5 import (
    LockedBaseSelection, _fingerprint, _index_signature,
    lock_base as _lock_v5, prepare_base as _prepare_v5,
)
from .models import MemoryContext, MemoryEvidence, PreparedRecall, SourceProvenance
from .semantic_protocol import usage_guidance, validate_use_relation

PANEL_ITEMS, PANEL_CHARS, PANEL_BYTES = 24, 7000, 28000
FINAL_ITEMS, FINAL_CHARS, FINAL_BYTES = 4, 5000, 20000


def prepare_base(adapter, query, policy):
    prepared = _prepare_v5(adapter, query, policy)
    adapter._last_semantic_read_diagnostics["profile"] = "semantic-associative-v6"
    return prepared


def lock_base(adapter, prepared: PreparedRecall, suggestions) -> LockedBaseSelection:
    if prepared.context.safe_error_code:
        raise ValueError("base_panel_unavailable")
    if prepared.context.evidence:
        return replace(_lock_v5(adapter, prepared, suggestions, False, "none", ""),
                       remaining_slots=1)
    if suggestions != () or prepared._semantic_v4_snapshot is None:
        raise ValueError("invalid_semantic_selection")
    signature, version, hits, seeds = prepared._semantic_v4_snapshot
    return LockedBaseSelection(
        prepared.context.query, _fingerprint(prepared.selection_items), (), (), (),
        (), (), (), 1, False, "none", "", signature, version, hits, seeds)


def _pool_panel(query, rows, policy, *, label, first_number):
    groups = sorted({row["group"] for row in rows.values()})
    if label == "graph":
        key = lambda row: (-row["h"], -row["cosine"], -row["lexical"],
                           row["item"].evidence_id)
    else:
        key = lambda row: (-row["cosine"], -row["lexical"],
                           row["item"].evidence_id)
    first = sorted((min((row for row in rows.values() if row["group"] == group),
                        key=key) for group in groups), key=key)
    first_ids = {row["item"].evidence_id for row in first}
    ordered = first + sorted((row for row in rows.values()
                              if row["item"].evidence_id not in first_ids), key=key)
    labels = {group: f"G{n}" for n, group in enumerate(groups, 1)}
    items, cards, blocks = [], [], []
    omitted = {"item": [], "char": [], "byte": []}
    for row in ordered:
        item = row["item"]
        eid = item.evidence_id
        if len(items) >= PANEL_ITEMS:
            omitted["item"].append(eid)
            continue
        card = (f"[C{len(items)+1}]\nspeaker="
                + ("USER" if item.provenance.source_role == "user" else "LUMINA")
                + f"\nspoken_at={item.provenance.source_timestamp}"
                + f"\nsource_group={labels[row['group']]}\ntext={item.text}")
        proposed = "\n".join((*cards, card))
        if len(proposed) > PANEL_CHARS:
            omitted["char"].append(eid)
            continue
        if len(proposed.encode("utf-8")) > PANEL_BYTES:
            omitted["byte"].append(eid)
            continue
        items.append(item)
        cards.append(card)
        blocks.append(render_reliable_fact(
            item, f"M{first_number+len(items)-1}",
            include_source_context=policy.include_source_context))
    return PreparedRecall(
        MemoryContext(query, tuple(items), "\n".join(blocks), any(omitted.values())),
        tuple(blocks), tuple((item.evidence_id, ()) for item in items),
        (1, FINAL_CHARS, FINAL_BYTES), tuple(cards)), omitted


def prepare_supplements(adapter, lock: LockedBaseSelection, policy):
    """Exactly one bounded graph walk; index-only and graph-only panels are disjoint."""
    from .magma_adapter import _relation_compatible, _RELATION_RESOLVER

    diagnostics = {"profile": "semantic-associative-v6", "graph_traversals": 0}
    adapter._last_semantic_graph_diagnostics = diagnostics
    try:
        view = adapter.backend.first_hit_view()
        index = adapter.backend.calibrated_read_index()
        if view.version != lock.graph_version or _index_signature(index) != lock.index_signature:
            raise ValueError("graph_supplement_snapshot_mismatch")
        bounds = replace(adapter.first_hit, max_seeds=min(adapter.first_hit.max_seeds, 5),
                         max_nodes=min(adapter.first_hit.max_nodes, 64),
                         max_edges=min(adapter.first_hit.max_edges, 256))
        refs = tuple(adapter.backend.resolve_target_entity_refs(lock.query, limit=bounds.max_seeds))
        hits, search, qvector = index.search(lock.query, target_entity_refs=refs, limit=20)
        ranked = sorted((hit for hit in hits if view.eligible(hit.node_id)),
                        key=lambda hit: (-max(0.0, hit.cosine),
                                         view.stable_id(hit.node_id), hit.node_id))[:bounds.max_seeds]
        weights = amplitude_weights([max(0.0, min(1.0, hit.cosine)) for hit in ranked])
        seeds = tuple((hit.node_id, weight) for hit, weight in zip(ranked, weights)
                      if weight > 0)
        if tuple(hit.node_id for hit in hits) != lock.index_hit_ids or seeds != lock.seeds:
            raise ValueError("graph_supplement_snapshot_mismatch")
        result = discover_first_hit_read(view, seeds, bounds)
        adapter._last_first_hit_snapshot = result
        diagnostics.update(graph_traversals=1, graph_query_search=search,
                           graph_first_hit=dict(result.stats), graph_read_arcs=result.read_arcs)
        relation_ids = _RELATION_RESOLVER.resolve_query_relations(policy.relation_surfaces or ())
        index_nodes = {hit.node_id for hit in hits}
        index_eids = set()
        direct_rows = {}
        graph_rows = {}

        def add(node_id, target, cosine, lexical, h=0.0):
            candidate = adapter.backend.first_hit_candidate(node_id)
            if candidate is None or not _relation_compatible(candidate, relation_ids):
                return
            eid = candidate.metadata.get("evidence_id")
            if (not isinstance(eid, str) or not eid or
                    not isinstance(candidate.text, str) or not candidate.text.strip()):
                return
            try:
                provenance = SourceProvenance(**candidate.metadata["provenance"])
            except (KeyError, TypeError, ValueError):
                return
            if not all(isinstance(value, str) and value.strip()
                       for value in vars(provenance).values()):
                return
            if target is direct_rows:
                index_eids.add(eid)
                if eid in lock.selected_ids:
                    return
            elif eid in lock.base_panel_ids or eid in lock.selected_ids:
                return
            target.setdefault(eid, {
                "item": MemoryEvidence(eid, candidate.text, candidate.timestamp, provenance),
                "group": provenance.segment_id or provenance.conversation_id,
                "h": h, "cosine": cosine, "lexical": lexical, "node_id": node_id})

        for hit in hits:
            index_candidate = adapter.backend.first_hit_candidate(hit.node_id)
            if index_candidate is not None:
                index_eid = index_candidate.metadata.get("evidence_id")
                if isinstance(index_eid, str) and index_eid:
                    index_eids.add(index_eid)
            score = index.score_nodes(qvector, lock.query, (hit.node_id,)).get(hit.node_id, (0., 0.))
            add(hit.node_id, direct_rows, *score)
        reached = {arc[1] for arc in result.read_arcs}
        graph_nodes = tuple(node for node in result.fact_ids
                            if node not in index_nodes and node in reached)
        support = index.score_nodes(qvector, lock.query, graph_nodes)
        for node in graph_nodes:
            add(node, graph_rows, *support.get(node, (0., 0.)), h=result.h[node])
        for eid in index_eids:
            graph_rows.pop(eid, None)
        base_numbers = [int(match.group(1)) for block in lock.rendered_blocks
                        if (match := re.search(r"\[M(\d+)\s", block))]
        supplement_number = max(base_numbers, default=0) + 1
        direct, d_omitted = _pool_panel(lock.query, direct_rows, policy, label="direct",
                                        first_number=supplement_number)
        graph, g_omitted = _pool_panel(lock.query, graph_rows, policy, label="graph",
                                       first_number=supplement_number)
        view.check(lock.graph_version)
        index._check()
        diagnostics.update(
            direct_pool_count=len(direct_rows), graph_pool_count=len(graph_rows),
            direct_pool_ids=tuple(sorted(direct_rows)),
            graph_exclusive_pool_ids=tuple(sorted(graph_rows)),
            direct_panel_ids=tuple(item.evidence_id for item in direct.context.evidence),
            graph_panel_ids=tuple(item.evidence_id for item in graph.context.evidence),
            direct_omitted=d_omitted, graph_omitted=g_omitted)
        return direct, graph
    except ValueError as error:
        code = ("graph_supplement_snapshot_mismatch" if str(error) ==
                "graph_supplement_snapshot_mismatch" else "graph_supplement_unavailable")
    except Exception:
        code = "graph_supplement_unavailable"
    diagnostics["failure"] = code
    failed = PreparedRecall(MemoryContext(lock.query, safe_error_code=code))
    return failed, failed


def append_supplement(lock: LockedBaseSelection, panel: PreparedRecall, suggestions):
    """Pack one complete Fact after the byte-identical locked base prefix."""
    if panel.context.safe_error_code:
        raise ValueError(panel.context.safe_error_code)
    if type(suggestions) is not tuple or len(suggestions) > 4:
        raise ValueError("invalid_supplement_selection")
    if len({row[0] for row in suggestions if type(row) is tuple and row}) != len(suggestions):
        raise ValueError("invalid_supplement_selection")
    known = {item.evidence_id for item in panel.context.evidence}
    for row in suggestions:
        if (type(row) is not tuple or len(row) != 3 or type(row[0]) is not str
                or row[0] not in known or row[0] in lock.selected_ids
                or not validate_use_relation(row[1], row[2])):
            raise ValueError("invalid_supplement_selection")
    base = lock.context
    lookup = {item.evidence_id: (item, block) for item, block in
              zip(panel.context.evidence, panel._rendered_blocks)}
    rejected = []
    for eid, use, relation in suggestions:
        item, block = lookup[eid]
        suffix = usage_guidance(use, relation) + "\n" + block
        text = base.rendered_text + ("\n" if base.rendered_text else "") + suffix
        if (len(text) > FINAL_CHARS or len(text.encode("utf-8")) > FINAL_BYTES):
            rejected.append(eid)
            continue
        final = MemoryContext(lock.query, base.evidence + (item,), text,
                              base.truncated or panel.context.truncated)
        if (tuple(x.evidence_id for x in final.evidence[:len(base.evidence)])
                != lock.selected_ids or not final.rendered_text.startswith(base.rendered_text)
                or len(final.evidence) > FINAL_ITEMS):
            raise ValueError("semantic_v6_base_mutation")
        return final, {"selected_order": lock.selected_ids + (eid,),
                       "rejected_by_budget": tuple(rejected)}
    return base, {"selected_order": lock.selected_ids,
                  "rejected_by_budget": tuple(rejected)}
