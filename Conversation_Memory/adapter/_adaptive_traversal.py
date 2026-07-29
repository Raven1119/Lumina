"""Private, opt-in adaptive graph traversal for Lumina Recall.

Algorithm source:

- ``upstream/MAGMA/memory/query_engine.py::_probabilistic_beam_search``
- ``upstream/MAGMA/memory/query_engine.py::_adaptive_graph_traversal``

This module preserves the upstream intent-conditioned relation weights,
cosine semantic affinity, ``0.6 * relation + 0.4 * semantic`` transition
formula, cumulative path score, beam-width default ``10``, drop-threshold
default ``0.15``, and rank-one RRF anchor score ``1 / 61``. Lumina supplies
its own depth, node, temporal, evidence-projectability, and deterministic
ordering bounds.

Upstream's unknown-intent fallback is the ENTITY weight table. Lumina calls
that fallback ``GENERAL``. ``WHY`` deliberately maps to ``GENERAL`` because
the current memory graph does not provide trustworthy causal edges; this is
not causal Recall.

Dataset routing, query classification, benchmark constants, prompt/regex
heuristics, reranking, answer formatting, narrative generation, debug output,
import-time clients, and unbounded full-graph fallback are excluded.

Upstream MAGMA is distributed under the MIT License:
Copyright (c) 2024 Anonymous Authors.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Any, Callable, Sequence

from ._anchor_fusion import _is_projectable_event, _stable_node_key

_BEAM_WIDTH_DEFAULT = 10
_DROP_THRESHOLD_DEFAULT = 0.15
_RRF_ANCHOR_SCORE = 1.0 / 61.0
_RELATION_WEIGHT = 0.6
_SEMANTIC_WEIGHT = 0.4

_GENERAL_WEIGHTS = {
    "ENTITY": 0.6,
    "SEMANTIC": 0.3,
    "TEMPORAL": 0.05,
    "CAUSAL": 0.05,
}
_WHEN_WEIGHTS = {
    "TEMPORAL": 0.7,
    "CAUSAL": 0.1,
    "SEMANTIC": 0.1,
    "ENTITY": 0.1,
}
_INTENT_WEIGHTS = {
    "GENERAL": _GENERAL_WEIGHTS,
    "WHEN": _WHEN_WEIGHTS,
    "ENTITY": _GENERAL_WEIGHTS,
}


@dataclass(frozen=True)
class _AdaptiveExpansion:
    """One private event; no field crosses Lumina's public DTO boundary."""

    node: Any
    hop: int
    score: float


@dataclass(frozen=True)
class _BeamEntry:
    node: Any
    hop: int
    score: float
    semantic_score: float


def _normalized_intent(intent: str | None) -> str:
    if intent == "WHEN":
        return "WHEN"
    if intent == "ENTITY":
        return "ENTITY"
    # GENERAL uses upstream's actual unknown-intent fallback (ENTITY table).
    # WHY intentionally receives no causal specialization in this phase.
    return "GENERAL"


def _flatten_vector(value: Any) -> tuple[float, ...]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    while (
        isinstance(value, (list, tuple))
        and len(value) == 1
        and isinstance(value[0], (list, tuple))
    ):
        value = value[0]
    if not isinstance(value, (list, tuple)) or not value:
        return ()
    vector = tuple(float(item) for item in value)
    if not all(isfinite(item) for item in vector):
        return ()
    return vector


def _cosine_similarity(left: Any, right: Any) -> float:
    left_vector = _flatten_vector(left)
    right_vector = _flatten_vector(right)
    if not left_vector or len(left_vector) != len(right_vector):
        return 0.0
    left_norm = sqrt(sum(item * item for item in left_vector))
    right_norm = sqrt(sum(item * item for item in right_vector))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return sum(
        left_item * right_item
        for left_item, right_item in zip(left_vector, right_vector)
    ) / (left_norm * right_norm + 1e-8)


def _relation_type(link: Any) -> str:
    link_type = getattr(link, "link_type", None)
    value = getattr(link_type, "value", link_type)
    return value.upper() if isinstance(value, str) else ""


def _entry_key(entry: _BeamEntry) -> tuple[float, str, str]:
    evidence_id, node_id = _stable_node_key(entry.node)
    return (-entry.score, evidence_id, node_id)


def _adaptive_traverse(
    *,
    trg: Any,
    constraints: Any,
    event_node_type: type[Any],
    node_type: Any,
    query: str,
    anchors: Sequence[Any],
    intent: str | None,
    beam_width: int | None,
    drop_threshold: float | None,
    max_graph_depth: int,
    max_nodes: int,
    temporal_window: Any,
    timestamp_in_window: Callable[[Any, Any], bool],
) -> list[_AdaptiveExpansion]:
    """Select bounded graph expansion events from existing fused anchors."""
    if max_graph_depth <= 0 or max_nodes <= 0 or not anchors:
        return []

    resolved_beam_width = (
        _BEAM_WIDTH_DEFAULT if beam_width is None else beam_width
    )
    resolved_drop_threshold = (
        _DROP_THRESHOLD_DEFAULT
        if drop_threshold is None
        else float(drop_threshold)
    )
    weights = _INTENT_WEIGHTS[_normalized_intent(intent)]
    query_embedding = trg.encoder.encode(query)

    frontier: list[_BeamEntry] = []
    visited: set[str] = set()
    for anchor in anchors:
        try:
            node_id = getattr(anchor, "node_id", None)
            if (
                not isinstance(node_id, str)
                or not node_id.strip()
                or node_id in visited
            ):
                continue
            if not timestamp_in_window(
                getattr(anchor, "timestamp", None),
                temporal_window,
            ):
                continue
            semantic_score = _cosine_similarity(
                query_embedding,
                getattr(anchor, "embedding_vector", None),
            )
            frontier.append(
                _BeamEntry(
                    node=anchor,
                    hop=0,
                    score=_RRF_ANCHOR_SCORE,
                    semantic_score=semantic_score,
                )
            )
            visited.add(node_id)
        except Exception:
            continue
    frontier.sort(key=_entry_key)

    selected: list[_AdaptiveExpansion] = []
    for hop in range(1, max_graph_depth + 1):
        if not frontier or len(visited) >= max_nodes:
            break
        candidates: dict[str, _BeamEntry] = {}
        for parent in frontier:
            try:
                neighbors = trg.graph_db.get_neighbors(parent.node.node_id)
            except Exception:
                continue
            for neighbor, link in neighbors:
                try:
                    if not constraints.allows_link(link):
                        continue
                    node_id = getattr(neighbor, "node_id", None)
                    if (
                        not isinstance(node_id, str)
                        or not node_id.strip()
                        or node_id in visited
                        or not _is_projectable_event(
                            neighbor,
                            event_node_type=event_node_type,
                            node_type=node_type,
                            temporal_window=temporal_window,
                            timestamp_in_window=timestamp_in_window,
                        )
                    ):
                        continue
                    semantic_score = _cosine_similarity(
                        query_embedding,
                        getattr(neighbor, "embedding_vector", None),
                    )
                    if (
                        parent.semantic_score - semantic_score
                        > resolved_drop_threshold
                    ):
                        continue
                    structural_score = weights.get(_relation_type(link), 0.01)
                    transition_score = (
                        _RELATION_WEIGHT * structural_score
                        + _SEMANTIC_WEIGHT * semantic_score
                    )
                    cumulative_score = parent.score + transition_score
                    if not isfinite(cumulative_score):
                        continue
                    candidate = _BeamEntry(
                        node=neighbor,
                        hop=hop,
                        score=cumulative_score,
                        semantic_score=semantic_score,
                    )
                    previous = candidates.get(node_id)
                    if previous is None or _entry_key(candidate) < _entry_key(
                        previous
                    ):
                        candidates[node_id] = candidate
                except Exception:
                    continue

        ranked = sorted(candidates.values(), key=_entry_key)
        remaining = max_nodes - len(visited)
        frontier = ranked[: min(resolved_beam_width, remaining)]
        for entry in frontier:
            node_id = entry.node.node_id
            visited.add(node_id)
            selected.append(
                _AdaptiveExpansion(
                    node=entry.node,
                    hop=entry.hop,
                    score=entry.score,
                )
            )

    selected.sort(
        key=lambda item: (
            -item.score,
            *_stable_node_key(item.node),
        )
    )
    return selected
