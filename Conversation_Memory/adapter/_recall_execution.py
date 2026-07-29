"""Private execution seam for Lumina's current fixed MAGMA Recall path.

Upstream behavior mapped here:

- ``upstream/MAGMA/memory/graph_db.py::TraversalConstraints``
- ``upstream/MAGMA/memory/trg_memory.py::TemporalResonanceGraphMemory.query``

Upstream MAGMA is distributed under the MIT License:
Copyright (c) 2024 Anonymous Authors.

The fixed query path remains the default. ``temporal_window`` is enforced as a
Lumina-owned hard constraint; ``intent``, ``beam_width``, and ``drop_threshold``
remain reserved for later authorized phases and have no execution effect here.
"""

from __future__ import annotations

from copy import copy
from datetime import datetime, timezone
from typing import Any

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
    if policy.temporal_window is None:
        return context

    scores = context.metadata.get("search_scores", [])
    filtered_nodes = []
    filtered_scores = []
    for index, node in enumerate(context.anchor_nodes):
        if not _timestamp_in_temporal_window(
            getattr(node, "timestamp", None),
            policy.temporal_window,
        ):
            continue
        filtered_nodes.append(node)
        if index < len(scores):
            filtered_scores.append(scores[index])

    filtered_context = copy(context)
    filtered_context.anchor_nodes = filtered_nodes
    filtered_context.metadata = dict(context.metadata)
    filtered_context.metadata["search_scores"] = filtered_scores
    return filtered_context
