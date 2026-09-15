"""Bounded first-hit propagation over a rebuildable, owner-maintained view.

The view is navigation over existing facts and identities, never a second fact
store. All matrix rows retain the complete eligible outgoing denominator.
"""
from __future__ import annotations

from bisect import insort
from dataclasses import dataclass
from datetime import datetime
import heapq
from math import fsum, isfinite
from time import perf_counter
from typing import Iterable, Mapping

import numpy as np


class FirstHitUnavailable(ValueError):
    """A local snapshot or numerical contract could not be established."""


@dataclass(frozen=True)
class FirstHitPolicy:
    decay: float = 0.75
    max_seeds: int = 5
    max_nodes: int = 64
    max_edges: int = 256
    attention_budget: float = 1.0
    attention_penalty: float = 0.0
    max_links: int = 3

    def __post_init__(self):
        for name in ("max_seeds", "max_nodes", "max_edges", "max_links"):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name in {"max_edges", "max_links"} else 1):
                raise ValueError("first_hit_policy_invalid")
        for name in ("decay", "attention_budget", "attention_penalty"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError("first_hit_policy_invalid")
        if not 0 < self.decay < 1 or self.attention_budget < 0 or self.attention_penalty < 0:
            raise ValueError("first_hit_policy_invalid")


def _array(value, *, ndim):
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        raise FirstHitUnavailable("first_hit_numeric_input_invalid") from None
    if result.ndim != ndim or not np.isfinite(result).all():
        raise FirstHitUnavailable("first_hit_numeric_input_invalid")
    return result


def solve_first_hit(P, b, full_row_mass, decay=0.75):
    """Return (h, delta), using row-oriented b @ solve(I-lambda*P, I)."""
    P, b, full = _array(P, ndim=2), _array(b, ndim=1), _array(full_row_mass, ndim=1)
    size = len(b)
    tolerance = 128 * np.finfo(np.float64).eps * max(1, size)
    if (P.shape != (size, size) or full.shape != (size,)
            or isinstance(decay, bool) or not isinstance(decay, (int, float))
            or not isfinite(decay) or not 0 < decay < 1
            or np.any(P < 0) or np.any(b < 0) or b.sum() > 1 + tolerance
            or np.any(full < 0) or np.any(full > 1 + tolerance)):
        raise FirstHitUnavailable("first_hit_transition_invalid")
    local = P.sum(axis=1)
    if np.any(local > full + tolerance):
        raise FirstHitUnavailable("first_hit_transition_invalid")
    if not size:
        return np.empty(0, dtype=np.float64), 0.0
    identity = np.eye(size, dtype=np.float64)
    K = identity - decay * P
    try:
        R = np.linalg.solve(K, identity)
    except np.linalg.LinAlgError:
        raise FirstHitUnavailable("first_hit_solve_unavailable") from None
    diagonal = np.diag(R)
    if (not np.isfinite(R).all() or np.any(diagonal <= 0)
            or np.any(R < -tolerance)
            or not np.allclose(K @ R, identity, rtol=tolerance, atol=tolerance)):
        raise FirstHitUnavailable("first_hit_solve_invalid")
    y = b @ R
    h = y / diagonal
    exits = decay * np.maximum(full - local, 0.0)
    delta = float(y @ exits)
    seed_mass = float(b.sum())
    if (not np.isfinite(h).all() or np.any(h < -tolerance)
            or np.any(h > seed_mass + tolerance) or not isfinite(delta)
            or delta < -tolerance or delta > seed_mass + tolerance):
        raise FirstHitUnavailable("first_hit_result_invalid")
    return np.clip(h, 0.0, seed_mass), min(max(delta, 0.0), seed_mass)


def project_attention(h, budget=1.0, penalty=0.0):
    """Nonnegative l1-ball projection; spare budget is deliberately unfilled."""
    h = _array(h, ndim=1)
    if (np.any(h < 0) or any(isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value) or value < 0 for value in (budget, penalty))):
        raise FirstHitUnavailable("first_hit_attention_invalid")
    values = np.maximum(h - penalty, 0.0)
    if values.sum() <= budget:
        return values
    if budget == 0:
        return np.zeros_like(values)
    ordered = np.sort(values)[::-1]
    thresholds = (np.cumsum(ordered) - budget) / np.arange(1, len(ordered) + 1)
    positive = np.nonzero(ordered > thresholds)[0]
    if not len(positive):
        raise FirstHitUnavailable("first_hit_attention_invalid")
    return np.maximum(values - thresholds[positive[-1]], 0.0)


def _value(value):
    return getattr(value, "value", value)


def _node_kind(node):
    """Only source-backed EVENTs and actually bound EntityRefs can propagate."""
    attributes = getattr(node, "attributes", None)
    if not isinstance(attributes, dict):
        return None
    kind = _value(getattr(node, "node_type", None))
    if kind == "ENTITY":
        ref = attributes.get("entity_ref")
        return "entity" if isinstance(ref, str) and ref.strip() else None
    if kind != "EVENT" or attributes.get("memory_kind") == "source-window-v1":
        return None
    evidence_id = attributes.get("evidence_id")
    provenance = attributes.get("provenance")
    text = getattr(node, "content_narrative", None)
    timestamp = getattr(node, "timestamp", None)
    if (not isinstance(evidence_id, str) or not evidence_id.strip()
            or not isinstance(text, str) or not text.strip()
            or not isinstance(timestamp, datetime) or timestamp.utcoffset() is None
            or not isinstance(provenance, dict)):
        return None
    fields = ("segment_id", "conversation_id", "turn_id", "source_role", "source_timestamp",
              "source_timezone", "ingestion_version")
    if (any(not isinstance(provenance.get(key), str) or not provenance[key].strip() for key in fields)
            or provenance["source_role"] not in {"user", "assistant"}
            or provenance.get("timezone_source", "legacy_segment_fallback") not in {
                "client", "configured_default", "legacy_segment_fallback"}):
        return None
    try:
        if datetime.fromisoformat(provenance["source_timestamp"]).utcoffset() is None:
            return None
    except ValueError:
        return None
    return "fact"


class DerivedFirstHitGraph:
    """Load/write-maintained weighted rows; query cursors never sort a hub.

    An arc is one deduplicated (source,target,family) channel. Reading a
    cursor entry counts even if its endpoint is excluded or cannot be added.
    Parallel roles preserve their physical metadata but use max weight here.
    """

    def __init__(self, graph):
        self.graph = graph
        self.version = 0
        self._kinds = {}
        self._stable = {}
        self._channels = {}
        self._rows = {}
        self._totals = {}
        self._invalid = set()
        self.rebuilds = 1
        self.updates = 0
        self._building = True
        for node in graph.nodes.values():
            self._add_node(node)
        for link in graph.links.values():
            self._add_link(link)
        self._building = False
        for (source, target, family), weight in self._channels.items():
            self._rows[source].append((-weight, self.stable_id(target), target, family))
        for node_id, row in self._rows.items():
            row.sort()
            self._totals[node_id] = fsum(-entry[0] for entry in row)
        self._source = self._signature()

    def _signature(self):
        graph = self.graph
        return id(graph.nodes), len(graph.nodes), id(graph.links), len(graph.links)

    def check(self, version=None):
        if self._source != self._signature() or (version is not None and version != self.version):
            raise FirstHitUnavailable("first_hit_snapshot_unavailable")

    def _add_node(self, node):
        node_id = getattr(node, "node_id", None)
        kind = _node_kind(node)
        if not isinstance(node_id, str) or not node_id or kind is None:
            return
        self._kinds[node_id] = kind
        self._stable[node_id] = (node.attributes["evidence_id"] if kind == "fact"
                                 else node.attributes["entity_ref"])
        self._rows.setdefault(node_id, [])
        self._totals.setdefault(node_id, 0.0)

    def add_node(self, node):
        self._add_node(node)
        self._acknowledge()

    def _acknowledge(self):
        self.version += 1
        self.updates += 1
        self._source = self._signature()

    def eligible(self, node_id):
        return node_id in self._kinds

    def is_fact(self, node_id):
        return self._kinds.get(node_id) == "fact"

    def stable_id(self, node_id):
        return self._stable.get(node_id, node_id)

    def outgoing_weight(self, node_id):
        if node_id in self._invalid:
            raise FirstHitUnavailable("first_hit_edge_weight_unavailable")
        return self._totals[node_id]

    def _add_channel(self, source, target, family, weight):
        key = (source, target, family)
        previous = self._channels.get(key, 0.0)
        if weight <= previous:
            return
        if not self._building:
            row = self._rows[source]
            if previous:
                row.remove((-previous, self.stable_id(target), target, family))
            insort(row, (-weight, self.stable_id(target), target, family))
        self._channels[key] = weight
        if not self._building:
            self._totals[source] = fsum(-entry[0] for entry in self._rows[source])

    def _add_link(self, link):
        source, target = link.source_node_id, link.target_node_id
        if not self.eligible(source) or not self.eligible(target):
            return
        if _value(getattr(link, "metadata", {}).get("status", "ACTIVE")) != "ACTIVE":
            return
        family = _value(link.link_type)
        properties = getattr(link, "properties", {})
        subtype = _value(properties.get("sub_type"))
        if family == "ENTITY":
            # Legacy EVENT--EVENT name shortcuts do not get a second vote.
            if (subtype != "REFERS_TO" or not self.is_fact(source)
                    or self._kinds[target] != "entity"):
                return
            self._add_channel(source, target, family, 1.0)
            self._add_channel(target, source, family, 1.0)
        elif family == "TEMPORAL" and subtype in {"PRECEDES", "SUCCEEDS"}:
            if self.is_fact(source) and self.is_fact(target):
                self._add_channel(source, target, family, 1.0)
        elif family == "SEMANTIC" and subtype == "RELATED_TO":
            if not self.is_fact(source) or not self.is_fact(target):
                return
            # Pinned legacy format is 1/(1+L2 distance). New format explicitly
            # marks nonnegative endpoint cosine. Raw index distances are invalid.
            weight = properties.get("similarity_score")
            source_kind = properties.get("weight_source", "legacy_inverse_l2")
            if (isinstance(weight, bool) or not isinstance(weight, (int, float))
                    or not isfinite(weight) or not 0 <= weight <= 1
                    or source_kind not in {"legacy_inverse_l2", "nonnegative_endpoint_cosine"}):
                self._invalid.add(source)
                return
            self._add_channel(source, target, family, float(weight))

    def add_link(self, link):
        self._add_link(link)
        self._acknowledge()

    def cursor(self, node_id, version):
        self.check(version)
        # The tuple entries are immutable. No degree-sized copy or sorting.
        return iter(self._rows[node_id])


@dataclass(frozen=True)
class FirstHitResult:
    node_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]
    h: dict[str, float]
    attention: dict[str, float]
    delta: float
    stats: dict
    seed_weights: dict[str, float]


def discover_first_hit(view, seeds: Mapping[str, float] | Iterable[tuple[str, float]],
                       policy: FirstHitPolicy, *, excluded_node_ids=()):
    """Discover by path strength, freeze the actually read arcs, then solve."""
    view.check()
    version = view.version
    excluded = set(excluded_node_ids)
    combined = {}
    for node_id, value in seeds.items() if isinstance(seeds, Mapping) else seeds:
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) or value < 0):
            raise FirstHitUnavailable("first_hit_seed_invalid")
        if value > 0 and node_id not in excluded and view.eligible(node_id):
            combined[node_id] = combined.get(node_id, 0.0) + value
    ranked = sorted(combined, key=lambda key: (-combined[key], view.stable_id(key), key))
    node_ids = ranked[:min(policy.max_seeds, policy.max_nodes)]
    total = sum(combined[key] for key in node_ids)
    weights = {key: combined[key] / max(1.0, total) for key in node_ids}
    indices = {key: i for i, key in enumerate(node_ids)}
    path_strength = dict(weights)
    cursors = {}
    pending = {}
    queue = []
    full_mass = {}
    reads = 0
    arcs = []

    def push_next(source):
        nonlocal reads
        if reads >= policy.max_edges:
            return
        view.check(version)
        try:
            entry = next(cursors[source])
        except StopIteration:
            return
        reads += 1  # Cursor peek is real adjacency I/O, even if never popped.
        negative_weight, _stable, target, family = entry
        denominator = max(1.0, view.outgoing_weight(source))
        probability = -negative_weight / denominator
        pending[source] = (target, family, probability)
        heapq.heappush(queue, (-path_strength[source] * policy.decay * probability,
                               view.stable_id(target), target, view.stable_id(source), source))

    def open_node(node_id):
        outgoing = view.outgoing_weight(node_id)
        full_mass[node_id] = outgoing / max(1.0, outgoing)
        cursors[node_id] = view.cursor(node_id, version)
        push_next(node_id)

    for node_id in node_ids:
        open_node(node_id)
    while queue:
        strength, _target_key, target, _source_key, source = heapq.heappop(queue)
        pending_target, family, probability = pending.pop(source)
        if pending_target != target:
            raise FirstHitUnavailable("first_hit_cursor_invalid")
        if target not in excluded:
            if target not in indices and len(node_ids) < policy.max_nodes:
                indices[target] = len(node_ids)
                node_ids.append(target)
                path_strength[target] = -strength
                open_node(target)
            if target in indices:
                arcs.append((source, target, probability))
        push_next(source)
    view.check(version)
    size = len(node_ids)
    P = np.zeros((size, size), dtype=np.float64)
    for source, target, probability in arcs:
        P[indices[source], indices[target]] += probability
    b = np.asarray([weights.get(node_id, 0.0) for node_id in node_ids], dtype=np.float64)
    started = perf_counter()
    h_values, delta = solve_first_hit(P, b, [full_mass[key] for key in node_ids], policy.decay)
    fact_ids = tuple(key for key in node_ids if view.is_fact(key))
    h = dict(zip(node_ids, (float(value) for value in h_values)))
    attention = project_attention([h[key] for key in fact_ids], policy.attention_budget,
                                  policy.attention_penalty)
    view.check(version)
    return FirstHitResult(tuple(node_ids), fact_ids, h,
                          dict(zip(fact_ids, (float(value) for value in attention))), delta,
                          {"nodes_read": size, "edges_read": reads, "matrix_size": size,
                           "solve_seconds": perf_counter() - started, "graph_version": version,
                           "budget_exhausted": len(ranked) > len(weights) or bool(np.any(
                               np.asarray([full_mass[key] for key in node_ids]) - P.sum(axis=1) > 1e-12))},
                          weights)
