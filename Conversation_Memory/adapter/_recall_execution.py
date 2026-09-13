"""Private execution seam for Lumina's fixed Recall path.

Upstream behavior mapped here:

- ``upstream/MAGMA/memory/graph_db.py::TraversalConstraints``
- ``upstream/MAGMA/memory/trg_memory.py::TemporalResonanceGraphMemory.query``

Upstream MAGMA is distributed under the MIT License:
Copyright (c) 2024 Anonymous Authors.

Dense anchors are fused with a bounded lexical ranking before the fixed,
bounded graph traversal.
"""

from __future__ import annotations

from copy import copy
from collections import deque
from typing import Any

from ._anchor_fusion import _rank_lexical_events, _rrf_fuse
from .models import RecallPolicy


def _execute_fixed_recall(
    *,
    trg: Any,
    constraints_type: Any,
    event_node_type: type[Any],
    node_type: Any,
    query: str,
    policy: RecallPolicy,
    target_entity_ref: str | None = None,
    target_entity_refs: tuple[str, ...] = (),
    lexical_index: Any = None,
    entity_surfaces: tuple[str, ...] = (),
    entity_membership: dict[str, Any] | None = None,
) -> Any:
    scan_stats = {"entity_adjacency_links_read": 0}
    constraints = constraints_type(
        max_depth=policy.max_graph_depth,
        max_nodes=policy.max_nodes,
        follow_temporal=True,
        follow_semantic=True,
        follow_causal=True,
        time_window=None,
    )
    indexed_graph = hasattr(trg.graph_db, "node_to_links") and hasattr(trg.graph_db, "graph")
    dense_constraints = copy(constraints)
    if indexed_graph:
        # Upstream traverse builds all neighbors before enforcing max_nodes.
        # Retrieve dense anchors with no expansion; the adapter owns bounded
        # projection below and never depends on upstream's paths[:10].
        dense_constraints.max_depth = 0
    context = trg.query(
        query,
        max_results=min(policy.top_k, policy.max_nodes),
        constraints=dense_constraints,
    )

    scores = context.metadata.get("search_scores", [])
    dense_nodes = []
    dense_scores = []
    for index, node in enumerate(context.anchor_nodes):
        dense_nodes.append(node)
        if index < len(scores):
            dense_scores.append(scores[index])

    dense_context = copy(context)
    dense_context.anchor_nodes = dense_nodes
    dense_context.metadata = dict(context.metadata)
    dense_context.metadata["search_scores"] = dense_scores

    try:
        lexical_kwargs = dict(
            query=query,
            max_nodes=policy.max_nodes,
            event_node_type=event_node_type,
            node_type=node_type,
        )
        if lexical_index is not None and indexed_graph:
            lexical_nodes = lexical_index.rank(graph_db=trg.graph_db, entity_surfaces=entity_surfaces,
                                              **lexical_kwargs)
        else:
            lexical_nodes = _rank_lexical_events(graph_nodes=trg.graph_db.nodes.items(), **lexical_kwargs)
        try:
            entity_subset_nodes = _entity_subset_events(
                trg,
                query=query,
                target_entity_ref=target_entity_ref,
                target_entity_refs=target_entity_refs,
                policy=policy,
                event_node_type=event_node_type,
                node_type=node_type,
                scan_stats=scan_stats,
                entity_membership=entity_membership,
            )
        except Exception:
            entity_subset_nodes = []
        ranked_lists = (dense_nodes, lexical_nodes)
        if entity_subset_nodes:
            ranked_lists = (dense_nodes, lexical_nodes, entity_subset_nodes)
        fused = _rrf_fuse(
            ranked_lists,
            limit=min(policy.top_k, policy.max_nodes),
        )
        fused_nodes = [node for node, _score in fused]
        fused_scores = [score for _node, score in fused]
    except Exception:
        return dense_context

    fused_context = copy(dense_context)
    fused_context.anchor_nodes = fused_nodes
    fused_context.narrative_context = ""
    fused_context.metadata = dict(dense_context.metadata)
    fused_context.metadata["search_scores"] = fused_scores

    if indexed_graph:
        traversal_result = _bounded_projection(
            graph_db=trg.graph_db, anchors=fused_nodes, constraints=constraints,
            target_refs=tuple(dict.fromkeys((*target_entity_refs,
                *((target_entity_ref,) if target_entity_ref else ())))),
            event_node_type=event_node_type, node_type=node_type,
            initial_reads=scan_stats["entity_adjacency_links_read"],
        )
        fused_context.metadata["association_metadata"] = traversal_result["association_metadata"]
        fused_context.metadata["bounded_recall_stats"] = traversal_result["stats"]
        fused_context.metadata["bounded_recall_stats"].update(scan_stats)
    else:
        traversal_result = trg.graph_db.traverse(
            start_nodes=[node.node_id for node in fused_nodes], constraints=constraints,
        )
    fused_context.traversal_paths = traversal_result.get("paths", [])
    return fused_context


def _entity_subset_events(
    trg: Any,
    *,
    query: str,
    target_entity_ref: str | None,
    target_entity_refs: tuple[str, ...] = (),
    policy: RecallPolicy,
    event_node_type: type[Any],
    node_type: Any,
    scan_stats: dict | None = None,
    entity_membership: dict[str, Any] | None = None,
) -> list[Any]:
    """Search complete cached entity membership in the existing vector index.

    The backend derives eligible EVENT positions from actual REFERS_TO edges
    during load/write, covering subject, object and ordinary mention roles.
    Query-time work combines cached selectors; it never walks an entity's
    adjacency or limits historical eligibility to a neighbor prefix. FAISS
    work depends on index/member/ref counts, separately from the max_nodes
    bounds on returned candidates and subsequent graph adjacency reads.
    Missing or invalid membership disables only this optional RRF channel.
    """
    refs = tuple(dict.fromkeys((*target_entity_refs,
                               *((target_entity_ref,) if target_entity_ref else ()))))
    if not refs:
        return []
    members = [
        entity_membership[key] for key in dict.fromkeys(f"entity:{ref.casefold()}" for ref in refs)
        if entity_membership is not None and key in entity_membership
        and len(entity_membership[key].positions)
    ]
    if scan_stats is not None:
        scan_stats.update(
            entity_adjacency_links_read=0,
            entity_membership_unavailable=entity_membership is None,
            entity_membership_refs=len(members),
            # Sum of membership sizes, not the size of their deduplicated union.
            entity_membership_entries=sum(len(member.positions) for member in members),
        )
    if not members:
        return []
    vector_db = trg.vector_db
    index = getattr(vector_db, "index", None)
    id_to_index = getattr(vector_db, "id_to_index", None)
    index_to_id = getattr(vector_db, "index_to_id", None)
    if index is None or not id_to_index or not index_to_id:
        return []
    import faiss
    import numpy as np

    # Same query view as the dense path: upstream trg_memory.query enriches
    # then encodes before searching the vector DB.
    enriched_query = trg.keyword_enricher.enrich_query(query)
    query_vector = np.asarray(
        trg.encoder.encode(enriched_query), dtype=np.float32,
    ).reshape(1, -1)
    # Array uses IndexFlat's direct subset fast path for a single identity.
    # OR uses cached Batch selectors, whose native membership checks avoid
    # scanning every selected position for each index row. Keep `members` and
    # intermediate OR selectors alive for the complete native search call.
    selectors = []
    selector = members[0].array_selector if len(members) == 1 else members[0].batch_selector
    for member in members[1:]:
        selector = faiss.IDSelectorOr(selector, member.batch_selector)
        selectors.append(selector)
    params = faiss.SearchParameters(sel=selector)
    k = min(policy.top_k, policy.max_nodes, sum(len(member.positions) for member in members))
    distances, indices = index.search(query_vector, k, params=params)

    nodes = []
    seen = set()
    for distance, position in zip(distances[0], indices[0]):
        if position == -1:
            continue
        node_id = index_to_id.get(int(position))
        if not node_id or node_id in seen:
            continue
        node = trg.graph_db.get_node(node_id)
        if (
            isinstance(node, event_node_type)
            and getattr(node, "node_type", None) == node_type.EVENT
        ):
            seen.add(node_id)
            nodes.append((float(distance), str(node.attributes.get("evidence_id", node_id)), node_id, node))
    nodes.sort(key=lambda item: item[:3])
    if scan_stats is not None:
        scan_stats.update(entity_selector_union_nodes=len(selectors),
                          entity_vector_index_size=int(index.ntotal),
                          entity_search_candidates_returned=len(nodes))
    return [item[3] for item in nodes]


def _iter_adjacent_links(graph_db, node_id):
    """Lazy existing MultiDiGraph adjacency; no full neighbor list or graph scan."""
    graph = getattr(graph_db, "graph", None)
    if graph is not None:
        for adjacency in (graph.succ, graph.pred):
            for edges in adjacency.get(node_id, {}).values():
                for link_id in edges:
                    link = graph_db.links.get(link_id)
                    if link is not None:
                        yield link
    else:
        # Minimal graph test doubles may expose the existing adjacency index.
        for link_id in getattr(graph_db, "node_to_links", {}).get(node_id, ()):
            link = graph_db.links.get(link_id)
            if link is not None:
                yield link


def _bounded_projection(*, graph_db, anchors, constraints, target_refs,
                        event_node_type, node_type, initial_reads=0):
    """One two-fact role projection plus bounded ordinary graph continuation.

    Relationship projection uses two source facts joined on an explicit
    subject/object identity. Co-occurrence mention edges cannot establish the
    join. Every actual adjacency read consumes the shared max_nodes budget;
    this bound applies before fetching the next neighbor.
    """
    from ._anchor_fusion import _is_projectable_event
    from memory.graph_db import LinkSubType, LinkType
    budget = constraints.max_nodes
    reads = initial_reads
    paths = []
    association_metadata = {}
    seen_nodes = {node.node_id for node in anchors}
    exhausted = False

    def adjacent(node_id):
        nonlocal reads, exhausted
        iterator = _iter_adjacent_links(graph_db, node_id)
        while reads < budget:
            try:
                link = next(iterator)
            except StopIteration:
                return
            reads += 1
            yield link
        exhausted = True

    if constraints.max_depth > 0 and target_refs:
        targets = set(target_refs)
        for anchor in anchors:
            if not _is_projectable_event(anchor, event_node_type=event_node_type, node_type=node_type):
                continue
            metadata = getattr(anchor, "attributes", {})
            subject, obj = metadata.get("subject_entity_ref"), metadata.get("object_entity_ref")
            bridge_evidence_id = metadata.get("evidence_id")
            if not subject or not obj or not bridge_evidence_id or not targets.intersection((subject, obj)):
                continue
            for ref in dict.fromkeys((subject, obj)):
                if ref in targets:
                    continue
                entity_id = f"entity:{ref.casefold()}"
                for link in adjacent(entity_id):
                    if (link.link_type != LinkType.ENTITY
                            or link.properties.get("sub_type") != LinkSubType.REFERS_TO.value
                            or link.properties.get("role") not in {"subject", "object"}
                            or link.target_node_id != entity_id
                            or link.source_node_id == anchor.node_id
                            or not constraints.allows_link(link)):
                        continue
                    node = graph_db.get_node(link.source_node_id)
                    if not _is_projectable_event(node, event_node_type=event_node_type, node_type=node_type):
                        continue
                    attrs = node.attributes
                    if attrs.get("evidence_id") == bridge_evidence_id:
                        continue
                    if ref not in (attrs.get("subject_entity_ref"), attrs.get("object_entity_ref")):
                        continue
                    if node.node_id not in seen_nodes and len(seen_nodes) >= budget:
                        exhausted = True
                        continue
                    seen_nodes.add(node.node_id)
                    # This is one fact-to-fact projection, not a claim that
                    # two source facts consume two underlying graph edges.
                    # Any positive graph depth permits this fixed two-fact
                    # capability; depth=0 remains anchor-only.
                    paths.append([anchor.node_id, node.node_id])
                    association_metadata.setdefault(node.node_id, {
                        "association_bridge_evidence_ids": [bridge_evidence_id],
                        "association_chain_evidence_ids": [bridge_evidence_id, attrs["evidence_id"]],
                    })

    queue = deque((node.node_id, 0, [node.node_id]) for node in anchors)
    expanded = set()
    while queue and reads < budget:
        node_id, depth, path = queue.popleft()
        if depth >= constraints.max_depth or node_id in expanded:
            continue
        expanded.add(node_id)
        for link in adjacent(node_id):
            if not constraints.allows_link(link):
                continue
            neighbor_id = link.target_node_id if link.source_node_id == node_id else link.source_node_id
            if neighbor_id in path:
                continue
            if neighbor_id not in seen_nodes and len(seen_nodes) >= budget:
                exhausted = True
                continue
            seen_nodes.add(neighbor_id)
            next_path = path + [neighbor_id]
            paths.append(next_path)
            queue.append((neighbor_id, depth + 1, next_path))
    return {"paths": paths, "association_metadata": association_metadata,
            "stats": {"adjacency_links_read": reads, "nodes_projected": len(seen_nodes),
                      "association_chains": len(association_metadata),
                      "budget_exhausted": exhausted or bool(queue and reads >= budget)}}
