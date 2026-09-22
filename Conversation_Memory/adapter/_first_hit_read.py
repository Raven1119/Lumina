"""Opt-in, request-local FirstHit exploration with independently visible cues.

Maximum path strength schedules work; it is not the final multi-path first-hit
score. The writer and historical reader continue to use discover_first_hit.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import heapq
from math import isfinite
from time import perf_counter
from typing import Iterable, Mapping

import numpy as np

from .first_hit import (FirstHitPolicy, FirstHitResult, FirstHitUnavailable,
                        _solve_first_hit_with_resolvent, project_attention)


MAX_CUE_GROUPS = 5


@dataclass(frozen=True)
class FirstHitReadResult(FirstHitResult):
    """Private local diagnostics, never public memory evidence or stored state."""

    P: np.ndarray
    b: np.ndarray
    full_row_mass: np.ndarray
    group_h: dict[str, dict[str, float]]
    group_seed_weights: dict[str, dict[str, float]]
    read_arcs: tuple[tuple[str, str, float, str], ...]
    path_strength: dict[str, float]


def _seed_items(seeds):
    combined = {}
    for node_id, value in seeds.items() if isinstance(seeds, Mapping) else seeds:
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) or value < 0):
            raise FirstHitUnavailable("first_hit_seed_invalid")
        if value:
            combined[node_id] = combined.get(node_id, 0.0) + value
            if not isfinite(combined[node_id]):
                raise FirstHitUnavailable("first_hit_seed_invalid")
    return combined


def _work_limits(policy):
    return policy.max_edges * (policy.max_nodes + 1), policy.max_edges * policy.max_nodes


def discover_first_hit_read(view, seeds: Mapping[str, float] | Iterable[tuple[str, float]],
                            policy: FirstHitPolicy, *, group_seeds=None,
                            excluded_node_ids=()):
    """Freeze one bounded graph, solve once, retain each declared cue's score.

    The shared seed set alone controls discovery and the global b. Each group
    is scaled by max(1, its complete supplied mass), then restricted to shared
    seeds; uncovered mass is not redistributed. Groups cannot expand entry
    or traversal budgets. At most five groups are accepted.

    Heap pushes are capped at E*(N+1), cached-arc relaxation attempts at E*N,
    where E/N are the existing edge/node budgets. A strict improvement resets
    a source's cached-arc cursor. Each relaxation consumes one work unit; each
    physical adjacency entry is fetched once. Heap pops cannot exceed pushes,
    and queued relaxation cursors cannot exceed N. Hitting either work cap
    returns a marked partial graph instead of an unbounded convergence loop.
    """
    view.check()
    version = view.version
    excluded = set(excluded_node_ids)
    combined = {key: value for key, value in _seed_items(seeds).items()
                if key not in excluded and view.eligible(key)}
    ranked = sorted(combined, key=lambda key: (-combined[key], view.stable_id(key), key))
    node_ids = ranked[:min(policy.max_seeds, policy.max_nodes)]
    total = sum(combined[key] for key in node_ids)
    if not isfinite(total):
        raise FirstHitUnavailable("first_hit_seed_invalid")
    weights = {key: combined[key] / max(1.0, total) for key in node_ids}

    groups = {} if group_seeds is None else group_seeds
    if not isinstance(groups, Mapping) or len(groups) > MAX_CUE_GROUPS:
        raise FirstHitUnavailable("first_hit_group_invalid")
    group_weights, group_missing = {}, {}
    for group, supplied in groups.items():
        if not isinstance(group, str) or not group.strip():
            raise FirstHitUnavailable("first_hit_group_invalid")
        values = _seed_items(supplied)
        mass = sum(values.values())
        if not isfinite(mass):
            raise FirstHitUnavailable("first_hit_group_invalid")
        scale = max(1.0, mass)
        group_weights[group] = {key: value / scale for key, value in values.items()
                                if key in weights}
        group_missing[group] = tuple(sorted((key for key in values if key not in weights),
                                             key=lambda key: (view.stable_id(key), key)))

    indices = {key: index for index, key in enumerate(node_ids)}
    path_strength = dict(weights)
    cursors, pending, generations, full_mass = {}, {}, {}, {}
    cached, relax_position = {}, {}
    relax_queue, scheduled = deque(), set()
    queue, arcs = [], []
    reads = queue_updates = queue_pops = stale_pops = relaxations = improvements = 0
    queue_limit, relaxation_limit = _work_limits(policy)
    halt_reason = None
    node_limit_hit = False

    def schedule(source):
        if source not in scheduled and relax_position.get(source, 0) < len(cached.get(source, ())):
            scheduled.add(source)
            relax_queue.append(source)

    def refresh(source):
        nonlocal queue_updates, halt_reason
        if source not in pending or halt_reason:
            return
        if queue_updates >= queue_limit:
            halt_reason = "queue_budget"
            return
        arc_index = pending[source]
        _source, target, probability, _family = arcs[arc_index]
        generation = generations.get(source, 0) + 1
        generations[source] = generation
        heapq.heappush(queue, (-path_strength[source] * policy.decay * probability,
                               view.stable_id(target), target, view.stable_id(source),
                               source, generation, arc_index))
        queue_updates += 1

    def improve(target, strength):
        nonlocal improvements
        if strength > path_strength[target]:
            path_strength[target] = strength
            improvements += 1
            relax_position[target] = 0
            refresh(target)
            schedule(target)

    def relax_cached():
        nonlocal relaxations, halt_reason
        while relax_queue and not halt_reason:
            if relaxations >= relaxation_limit:
                halt_reason = "relaxation_budget"
                break
            source = relax_queue.popleft()
            scheduled.remove(source)
            position = relax_position[source]
            arc_index = cached[source][position]
            relax_position[source] = position + 1
            _source, target, probability, _family = arcs[arc_index]
            relaxations += 1
            if target in indices:
                improve(target, path_strength[source] * policy.decay * probability)
            schedule(source)

    def push_next(source):
        nonlocal reads
        if reads >= policy.max_edges or halt_reason:
            return
        view.check(version)
        try:
            negative_weight, _stable, target, family = next(cursors[source])
        except StopIteration:
            return
        reads += 1  # A peek is charged even if its heap entry is never popped.
        probability = -negative_weight / max(1.0, view.outgoing_weight(source))
        if not isfinite(probability) or not 0 <= probability <= 1:
            raise FirstHitUnavailable("first_hit_transition_invalid")
        arc_index = len(arcs)
        arcs.append((source, target, probability, family))
        cached[source].append(arc_index)
        pending[source] = arc_index
        refresh(source)
        schedule(source)

    def open_node(node_id):
        outgoing = view.outgoing_weight(node_id)
        full_mass[node_id] = outgoing / max(1.0, outgoing)
        cursors[node_id] = view.cursor(node_id, version)
        cached[node_id] = []
        relax_position[node_id] = 0
        push_next(node_id)

    for node_id in node_ids:
        open_node(node_id)
    relax_cached()
    while queue and not halt_reason:
        negative_strength, _target_key, target, _source_key, source, generation, arc_index = heapq.heappop(queue)
        queue_pops += 1
        if generations[source] != generation or pending.get(source) != arc_index:
            stale_pops += 1
            continue
        del pending[source]
        if target not in excluded:
            if target not in indices:
                if len(node_ids) < policy.max_nodes:
                    indices[target] = len(node_ids)
                    node_ids.append(target)
                    path_strength[target] = -negative_strength
                    open_node(target)
                else:
                    node_limit_hit = True
            else:
                improve(target, -negative_strength)
        push_next(source)
        relax_cached()

    view.check(version)
    size = len(node_ids)
    P = np.zeros((size, size), dtype=np.float64)
    for source, target, probability, _family in arcs:
        if target in indices:
            P[indices[source], indices[target]] += probability
    b = np.asarray([weights.get(key, 0.0) for key in node_ids], dtype=np.float64)
    full = np.asarray([full_mass[key] for key in node_ids], dtype=np.float64)
    started = perf_counter()
    h_values, delta, R = _solve_first_hit_with_resolvent(P, b, full, policy.decay)
    h = dict(zip(node_ids, map(float, h_values)))
    fact_ids = tuple(key for key in node_ids if view.is_fact(key))
    attention = project_attention([h[key] for key in fact_ids], policy.attention_budget,
                                  policy.attention_penalty)
    group_h = {}
    if groups:
        B = np.asarray([[values.get(key, 0.0) for key in node_ids]
                        for values in group_weights.values()], dtype=np.float64)
        H = (B @ R) / np.diag(R) if size else B
        tolerance = 128 * np.finfo(np.float64).eps * max(1, size)
        masses = B.sum(axis=1)
        if (not np.isfinite(H).all() or np.any(H < -tolerance)
                or np.any(H > masses[:, None] + tolerance)):
            raise FirstHitUnavailable("first_hit_group_result_invalid")
        for index, group in enumerate(groups):
            values = np.clip(H[index], 0.0, masses[index])
            group_h[group] = dict(zip(node_ids, map(float, values)))
    missing_arcs = bool(np.any(full - P.sum(axis=1) > 1e-12))
    reasons = []
    if len(ranked) > len(weights):
        reasons.append("seed_budget")
    if node_limit_hit:
        reasons.append("node_budget")
    if reads >= policy.max_edges and missing_arcs:
        reasons.append("edge_budget")
    if halt_reason:
        reasons.append(halt_reason)
    if missing_arcs and not reasons:
        reasons.append("omitted_arcs")
    stats = {"nodes_read": size, "edges_read": reads, "matrix_size": size,
             "solve_seconds": perf_counter() - started, "graph_version": version,
             "budget_exhausted": bool(reasons), "partial": bool(reasons),
             "partial_reasons": tuple(reasons), "queue_updates": queue_updates,
             "queue_pops": queue_pops, "stale_queue_pops": stale_pops,
             "queue_update_limit": queue_limit, "local_relaxations": relaxations,
             "local_relaxation_limit": relaxation_limit, "path_improvements": improvements,
             "group_missing_seeds": group_missing,
             "group_seed_mass": {key: sum(values.values()) for key, values in group_weights.items()}}
    view.check(version)
    for array in (P, b, full):
        array.setflags(write=False)
    return FirstHitReadResult(tuple(node_ids), fact_ids, h,
                              dict(zip(fact_ids, map(float, attention))), delta, stats, weights,
                              P, b, full, group_h, group_weights, tuple(arcs), path_strength)
