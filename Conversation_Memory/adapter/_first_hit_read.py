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


MAX_QUERY_CUE_GROUPS = 3


class _QueryFrontier:
    """One request's cue scheduling state over the shared physical cache."""

    def __init__(self, name, weights, node_ids):
        self.name = name
        self.path = {key: weights.get(key, 0.0) for key in node_ids}
        self.queue = []
        self.positions = {}
        self.pending = {}
        self.generations = {}
        self.relaxed_positions = {}
        self.scheduled = set()
        self.turns = self.updates = self.pops = self.stale = self.relaxations = 0
        self.improvements = self.cache_reuses = 0


def _query_work_limits(node_budget, edge_budget, group_count):
    limit = group_count * edge_budget * (node_budget + 1)
    return limit, limit


def discover_query_first_hit(view, seeds: Mapping[str, float] | Iterable[tuple[str, float]],
                             policy: FirstHitPolicy, *, group_seeds=None,
                             excluded_node_ids=(), seed_only=False):
    """Explicit v2 discovery: round-robin cue frontiers, one bounded read graph.

    At most three covered cue groups take turns, each popping one valid arc
    from its own versioned max-path heap. All groups use a single physical
    cursor/cache per node, capped at five shared seeds, 64 nodes and 256 read
    arcs (or smaller caller policy bounds). A cached physical arc can support
    every group, but enters P only once. No group receives another edge budget.

    Heap pushes and cached-arc relaxation attempts each have a total cap of
    G*E*(N+1), with G<=3. Stale pops count too and cannot exceed pushes. A strict
    support improvement restarts that cue's bounded cached-row scan. Physical
    cache notifications visit at most G receivers per arc; admission revisits
    at most G incoming records per already-read arc. Work-cap exhaustion
    freezes the actual-read graph and returns marked partial output.

    Global b/h/delta keep the original kernel. The path_strength diagnostic is
    maximum *cue scheduling* support, not global b's first-hit probability;
    individual group paths are retained in stats. When no supplied group has a
    covered seed, one global fallback frontier explores the unchanged shared
    entries while declared empty groups retain zero H and missing diagnostics.
    The explicit seed_only control keeps those same entries/group vectors and
    full row denominators, opens no physical cursor, and solves with P=0.
    """
    view.check()
    if type(seed_only) is not bool:
        raise FirstHitUnavailable("first_hit_seed_only_invalid")
    version = view.version
    node_budget, edge_budget = min(64, policy.max_nodes), min(256, policy.max_edges)
    seed_budget = min(5, policy.max_seeds, node_budget)
    excluded = set(excluded_node_ids)
    combined = {key: value for key, value in _seed_items(seeds).items()
                if key not in excluded and view.eligible(key)}
    ranked = sorted(combined, key=lambda key: (-combined[key], view.stable_id(key), key))
    node_ids = ranked[:seed_budget]
    total = sum(combined[key] for key in node_ids)
    if not isfinite(total):
        raise FirstHitUnavailable("first_hit_seed_invalid")
    weights = {key: combined[key] / max(1.0, total) for key in node_ids}
    groups = {} if group_seeds is None else group_seeds
    if not isinstance(groups, Mapping) or len(groups) > MAX_QUERY_CUE_GROUPS:
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
        group_weights[group] = {key: value / scale for key, value in values.items() if key in weights}
        group_missing[group] = tuple(sorted((key for key in values if key not in weights),
                                             key=lambda key: (view.stable_id(key), key)))
    active = [(name, values) for name, values in group_weights.items() if values]
    fallback = not active
    if fallback:
        active = [("__global_fallback__", weights)]
    frontiers = [_QueryFrontier(name, values, node_ids) for name, values in active]
    queue_limit, relaxation_limit = _query_work_limits(node_budget, edge_budget, len(frontiers))
    indices = {key: index for index, key in enumerate(node_ids)}
    cursors, cached, incoming, full_mass = {}, {}, {}, {}
    exhausted = set()
    relax_queue = deque()
    arcs = []
    reads = updates = pops = relaxations = improvements = cache_reuses = 0
    admission_notifications = physical_notifications = 0
    halt_reason = None
    node_limit_hit = False

    def schedule(group, source, *, restart_at=None):
        frontier = frontiers[group]
        if frontier.path.get(source, 0.0) <= 0:
            return
        position = frontier.relaxed_positions.setdefault(source, 0)
        if restart_at is not None:
            position = min(position, restart_at)
            frontier.relaxed_positions[source] = position
        if source not in frontier.scheduled and position < len(cached[source]):
            frontier.scheduled.add(source)
            relax_queue.append((group, source))

    def shared_arc(source, position):
        nonlocal reads, physical_notifications
        if position < len(cached[source]):
            return cached[source][position], True
        if source in exhausted or reads >= edge_budget or halt_reason:
            return None, False
        view.check(version)
        try:
            negative_weight, _stable, target, family = next(cursors[source])
        except StopIteration:
            exhausted.add(source)
            return None, False
        reads += 1
        probability = -negative_weight / max(1.0, view.outgoing_weight(source))
        if not isfinite(probability) or not 0 <= probability <= 1:
            raise FirstHitUnavailable("first_hit_transition_invalid")
        arc_index = len(arcs)
        arcs.append((source, target, probability, family))
        cached[source].append(arc_index)
        incoming.setdefault(target, []).append((source, position))
        for group in range(len(frontiers)):
            physical_notifications += 1
            schedule(group, source)
        return arc_index, False

    def refresh(group, source):
        nonlocal updates, halt_reason
        frontier = frontiers[group]
        if source not in frontier.pending or halt_reason:
            return
        if updates >= queue_limit:
            halt_reason = "queue_budget"
            return
        arc_index = frontier.pending[source]
        _source, target, probability, _family = arcs[arc_index]
        generation = frontier.generations.get(source, 0) + 1
        frontier.generations[source] = generation
        heapq.heappush(frontier.queue, (-frontier.path[source] * policy.decay * probability,
            view.stable_id(target), target, view.stable_id(source), source, generation, arc_index))
        updates += 1
        frontier.updates += 1

    def push_next(group, source):
        nonlocal cache_reuses
        frontier = frontiers[group]
        if frontier.path.get(source, 0.0) <= 0 or source in frontier.pending or halt_reason:
            return
        position = frontier.positions.setdefault(source, 0)
        arc_index, reused = shared_arc(source, position)
        if arc_index is None:
            return
        if reused:
            cache_reuses += 1
            frontier.cache_reuses += 1
        frontier.pending[source] = arc_index
        refresh(group, source)

    def improve(group, target, strength):
        nonlocal improvements
        frontier = frontiers[group]
        if strength > frontier.path.get(target, 0.0):
            frontier.path[target] = strength
            improvements += 1
            frontier.improvements += 1
            if target in frontier.pending:
                refresh(group, target)
            else:
                push_next(group, target)
            schedule(group, target, restart_at=0)

    def relax_cached():
        nonlocal relaxations, halt_reason
        while relax_queue and not halt_reason:
            if relaxations >= relaxation_limit:
                halt_reason = "relaxation_budget"
                break
            group, source = relax_queue.popleft()
            frontier = frontiers[group]
            frontier.scheduled.remove(source)
            position = frontier.relaxed_positions[source]
            arc_index = cached[source][position]
            frontier.relaxed_positions[source] = position + 1
            _source, target, probability, _family = arcs[arc_index]
            relaxations += 1
            frontier.relaxations += 1
            if target in indices:
                improve(group, target, frontier.path[source] * policy.decay * probability)
            schedule(group, source)

    def open_shared(node_id):
        outgoing = view.outgoing_weight(node_id)
        full_mass[node_id] = outgoing / max(1.0, outgoing)
        if not seed_only:
            cursors[node_id] = view.cursor(node_id, version)
        cached[node_id] = []
        for frontier in frontiers:
            frontier.path.setdefault(node_id, 0.0)

    for node_id in node_ids:
        open_shared(node_id)
    # Initialization also rotates across groups: one group's multiple seed
    # peeks cannot consume the physical budget before another group's first.
    initial = [deque(sorted((key for key in node_ids if frontier.path[key] > 0),
                             key=lambda key: (-frontier.path[key], view.stable_id(key), key)))
               for frontier in frontiers]
    while any(initial) and not halt_reason and not seed_only:
        for group, entries in enumerate(initial):
            if entries:
                push_next(group, entries.popleft())
    relax_cached()
    while not halt_reason:
        progressed = False
        for group, frontier in enumerate(frontiers):
            entry = None
            while frontier.queue and not halt_reason:
                proposed = heapq.heappop(frontier.queue)
                pops += 1
                frontier.pops += 1
                _strength, _target_key, _target, _source_key, source, generation, arc_index = proposed
                if (frontier.generations[source] != generation
                        or frontier.pending.get(source) != arc_index):
                    frontier.stale += 1
                    continue
                entry = proposed
                break
            if entry is None or halt_reason:
                continue
            progressed = True
            frontier.turns += 1
            negative_strength, _target_key, target, _source_key, source, _generation, _arc = entry
            del frontier.pending[source]
            frontier.positions[source] += 1
            if target not in excluded:
                if target not in indices:
                    if len(node_ids) < node_budget:
                        indices[target] = len(node_ids)
                        node_ids.append(target)
                        open_shared(target)
                        improve(group, target, -negative_strength)
                        # A new local node makes previously cached incoming
                        # support usable to every cue, without another read.
                        for previous_source, position in incoming.get(target, ()):
                            for other in range(len(frontiers)):
                                admission_notifications += 1
                                schedule(other, previous_source, restart_at=position)
                    else:
                        node_limit_hit = True
                else:
                    improve(group, target, -negative_strength)
            push_next(group, source)
            relax_cached()
        if not progressed:
            break

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
            group_h[group] = dict(zip(node_ids, map(float, np.clip(H[index], 0.0, masses[index]))))
    missing_arcs = bool(np.any(full - P.sum(axis=1) > 1e-12))
    reasons = []
    if len(ranked) > len(weights):
        reasons.append("seed_budget")
    if node_limit_hit:
        reasons.append("node_budget")
    if seed_only and missing_arcs:
        reasons.append("exploration_disabled")
    if not seed_only and reads >= edge_budget and missing_arcs:
        reasons.append("edge_budget")
    if halt_reason:
        reasons.append(halt_reason)
    if missing_arcs and not reasons:
        reasons.append("omitted_arcs")
    stats = {"profile": "graph-read-v2", "nodes_read": size, "edges_read": reads,
             "matrix_size": size, "solve_seconds": perf_counter() - started, "graph_version": version,
             "budget_exhausted": any(reason != "exploration_disabled" for reason in reasons),
             "partial": bool(reasons), "partial_reasons": tuple(reasons), "seed_only": seed_only,
             "queue_updates": updates, "queue_pops": pops, "stale_queue_pops": sum(f.stale for f in frontiers),
             "queue_update_limit": queue_limit, "local_relaxations": relaxations,
             "local_relaxation_limit": relaxation_limit, "path_improvements": improvements,
             "physical_arc_notifications": physical_notifications,
             "admission_notifications": admission_notifications, "cached_arc_reuses": cache_reuses,
             "group_missing_seeds": group_missing,
             "group_seed_mass": {key: sum(values.values()) for key, values in group_weights.items()},
             "uncovered_seed_groups": tuple(key for key, values in group_weights.items() if not values),
             "frontier_order": tuple(f.name for f in frontiers), "frontier_fallback": fallback,
             "frontier_schedule": "round_robin_one_valid_arc_per_active_group",
             "path_strength_meaning": "maximum_cue_scheduling_support_not_global_first_hit",
             "group_path_strength": {f.name: dict(f.path) for f in frontiers},
             "frontier_work": {f.name: {"turns": f.turns, "queue_updates": f.updates,
                 "queue_pops": f.pops, "local_relaxations": f.relaxations,
                 "path_improvements": f.improvements, "cached_arc_reuses": f.cache_reuses} for f in frontiers},
             "shared_budgets": {"seeds": seed_budget, "nodes": node_budget, "edges": edge_budget}}
    view.check(version)
    for array in (P, b, full):
        array.setflags(write=False)
    paths = {key: max(frontier.path[key] for frontier in frontiers) for key in node_ids}
    return FirstHitReadResult(tuple(node_ids), fact_ids, h, dict(zip(fact_ids, map(float, attention))),
                              delta, stats, weights, P, b, full, group_h, group_weights, tuple(arcs), paths)
