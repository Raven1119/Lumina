"""Private execution seam for Lumina's current fixed MAGMA Recall path.

Upstream behavior mapped here:

- ``upstream/MAGMA/memory/graph_db.py::TraversalConstraints``
- ``upstream/MAGMA/memory/trg_memory.py::TemporalResonanceGraphMemory.query``

Upstream MAGMA is distributed under the MIT License:
Copyright (c) 2024 Anonymous Authors.

Dense anchors are fused with a bounded lexical ranking before the existing
graph traversal. ``temporal_window`` is enforced as a Lumina-owned hard
constraint; ``intent``, ``beam_width``, and ``drop_threshold``
remain reserved for later authorized phases and have no execution effect here.
"""

from __future__ import annotations

from copy import copy
from datetime import datetime, timezone
from typing import Any

from ._anchor_fusion import _rank_lexical_events, _rrf_fuse
from .models import RecallPolicy


def _timestamp_in_temporal_window(
    timestamp: Any,
    temporal_window: tuple[datetime, datetime] | None,
) -> bool:
    """Return whether an event timestamp satisfies the requested ``[start, end)``."""
    if temporal_window is None:
        return True
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        return False
    try:
        if timestamp.utcoffset() is None:
            return False
        timestamp_utc = timestamp.astimezone(timezone.utc)
        start_utc = temporal_window[0].astimezone(timezone.utc)
        end_utc = temporal_window[1].astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError):
        return False
    return start_utc <= timestamp_utc < end_utc


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
        time_window=policy.temporal_window,
    )
    context = trg.query(
        query,
        max_results=min(policy.top_k, policy.max_nodes),
        constraints=constraints,
    )

    scores = context.metadata.get("search_scores", [])
    filtered_nodes = []
    filtered_scores = []
    for index, node in enumerate(context.anchor_nodes):
        if policy.temporal_window is not None and not _timestamp_in_temporal_window(
            getattr(node, "timestamp", None),
            policy.temporal_window,
        ):
            continue
        filtered_nodes.append(node)
        if index < len(scores):
            filtered_scores.append(scores[index])

    dense_context = copy(context)
    dense_context.anchor_nodes = filtered_nodes
    dense_context.metadata = dict(context.metadata)
    dense_context.metadata["search_scores"] = filtered_scores

    try:
        lexical_nodes = _rank_lexical_events(
            graph_nodes=trg.graph_db.nodes.items(),
            query=query,
            max_nodes=policy.max_nodes,
            event_node_type=event_node_type,
            node_type=node_type,
            temporal_window=policy.temporal_window,
            timestamp_in_window=_timestamp_in_temporal_window,
        )
        fused = _rrf_fuse(
            (filtered_nodes, lexical_nodes),
            limit=min(policy.top_k, policy.max_nodes),
        )
        fused_nodes = [node for node, _score in fused]
        fused_scores = [score for _node, score in fused]
        traversal_result = trg.graph_db.traverse(
            start_nodes=[node.node_id for node in fused_nodes],
            constraints=constraints,
        )

        fused_context = copy(dense_context)
        fused_context.anchor_nodes = fused_nodes
        fused_context.traversal_paths = traversal_result.get("paths", [])
        fused_context.narrative_context = ""
        fused_context.metadata = dict(dense_context.metadata)
        fused_context.metadata["search_scores"] = fused_scores
        return fused_context
    except Exception:
        return dense_context
