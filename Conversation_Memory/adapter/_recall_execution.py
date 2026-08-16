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
) -> Any:
    constraints = constraints_type(
        max_depth=policy.max_graph_depth,
        max_nodes=policy.max_nodes,
        follow_temporal=True,
        follow_semantic=True,
        follow_causal=True,
        time_window=None,
    )
    context = trg.query(
        query,
        max_results=min(policy.top_k, policy.max_nodes),
        constraints=constraints,
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
        lexical_nodes = _rank_lexical_events(
            graph_nodes=trg.graph_db.nodes.items(),
            query=query,
            max_nodes=policy.max_nodes,
            event_node_type=event_node_type,
            node_type=node_type,
        )
        try:
            entity_subset_nodes = _entity_subset_events(
                trg,
                query=query,
                target_entity_ref=target_entity_ref,
                policy=policy,
                event_node_type=event_node_type,
                node_type=node_type,
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

    traversal_result = trg.graph_db.traverse(
        start_nodes=[node.node_id for node in fused_nodes],
        constraints=constraints,
    )
    fused_context.traversal_paths = traversal_result.get("paths", [])
    return fused_context


def _entity_subset_events(
    trg: Any,
    *,
    query: str,
    target_entity_ref: str | None,
    policy: RecallPolicy,
    event_node_type: type[Any],
    node_type: Any,
) -> list[Any]:
    """Entity-conditioned semantic channel.

    When the query resolved a target entity ref, rank that entity's
    ``REFERS_TO`` events — both the ``role=subject`` edges and the role-less
    generic mention edges written for ``mention_entity_refs`` — by the same
    enriched-query embedding the dense path
    uses, via a FAISS ``IDSelectorBatch`` subset search, and return them as a
    third RRF list. The subset only adds candidates; ranking and admission
    are unchanged. No ref, no EntityNode, or any failure yields an empty list
    and the two-list fusion is byte-identical to before.
    Shadow evidence: ``docs/experiments/entity_conditioned_retrieval/``,
    ``docs/experiments/multi_entity_recall_gain/RESULT_CROWDED.md``.
    """
    if not target_entity_ref:
        return []
    entity_node = trg.graph_db.get_node(
        f"entity:{target_entity_ref.casefold()}"
    )
    if entity_node is None:
        return []
    # Imported lazily so controlled test doubles that stub
    # ``memory.graph_db`` with event-only symbols stay valid.
    from memory.graph_db import LinkSubType, LinkType

    event_ids = [
        link.source_node_id
        for link in trg.graph_db.links.values()
        if link.link_type == LinkType.ENTITY
        and link.target_node_id == entity_node.node_id
        and link.properties.get("sub_type") == LinkSubType.REFERS_TO.value
    ]
    if not event_ids:
        return []
    vector_db = trg.vector_db
    index = getattr(vector_db, "index", None)
    id_to_index = getattr(vector_db, "id_to_index", None)
    index_to_id = getattr(vector_db, "index_to_id", None)
    if index is None or not id_to_index or not index_to_id:
        return []
    positions = [
        id_to_index[event_id] for event_id in event_ids if event_id in id_to_index
    ]
    if not positions:
        return []

    import faiss
    import numpy as np

    # Same query view as the dense path: upstream trg_memory.query enriches
    # then encodes before searching the vector DB.
    enriched_query = trg.keyword_enricher.enrich_query(query)
    query_vector = np.asarray(
        trg.encoder.encode(enriched_query), dtype=np.float32,
    ).reshape(1, -1)
    selector = faiss.IDSelectorBatch(np.asarray(positions, dtype=np.int64))
    params = faiss.SearchParameters(sel=selector)
    k = min(policy.top_k, len(positions))
    _distances, indices = index.search(query_vector, k, params=params)

    nodes = []
    for position in indices[0]:
        if position == -1:
            continue
        node_id = index_to_id.get(int(position))
        if not node_id:
            continue
        node = trg.graph_db.get_node(node_id)
        if (
            isinstance(node, event_node_type)
            and getattr(node, "node_type", None) == node_type.EVENT
        ):
            nodes.append(node)
    return nodes
