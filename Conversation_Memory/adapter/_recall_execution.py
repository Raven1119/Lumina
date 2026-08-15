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
        fused = _rrf_fuse(
            (dense_nodes, lexical_nodes),
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
