"""Read-side mathematics and resource invariants; no models or stored history."""
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from Conversation_Memory.adapter import _first_hit_read as read
from Conversation_Memory.adapter.first_hit import (
    DerivedFirstHitGraph, FirstHitPolicy, FirstHitUnavailable,
    discover_first_hit, solve_first_hit,
)


class View:
    def __init__(self, rows, *, facts=None):
        self.version = 0
        self.rows = {key: sorted((-weight, target, target, "SEMANTIC")
                                for target, weight in edges)
                     for key, edges in rows.items()}
        self.totals = {key: sum(weight for _, weight in edges)
                       for key, edges in rows.items()}
        self.reads = []
        self.facts = set(rows if facts is None else facts)

    def check(self, version=None):
        if version is not None and version != self.version:
            raise FirstHitUnavailable("first_hit_snapshot_unavailable")

    def stable_id(self, key): return key
    def eligible(self, key): return key in self.rows
    def is_fact(self, key): return key in self.facts
    def outgoing_weight(self, key): return self.totals[key]

    def cursor(self, key, version):
        for index, row in enumerate(self.rows[key]):
            self.reads.append((key, index))
            yield row


def test_attached_counterexample_updates_already_seeded_node():
    rows = {"A": [("B", 1), ("D", .5)], "B": [("target", 1)],
            "D": [("E", 1)], "E": [], "target": []}
    policy = FirstHitPolicy(max_nodes=4, max_edges=10)
    baseline = discover_first_hit(View(rows), {"A": .99, "B": .01}, policy)
    view = View(rows)
    result = read.discover_first_hit_read(view, {"A": .99, "B": .01}, policy)
    assert "target" not in baseline.node_ids
    assert "target" in result.node_ids and "E" not in result.node_ids
    assert result.path_strength["B"] == pytest.approx(.495)
    assert result.h["target"] == pytest.approx(.37875)
    assert result.stats["path_improvements"] >= 1
    assert result.stats["stale_queue_pops"] >= 1
    assert result.stats["edges_read"] == len(view.reads) == len(set(view.reads))
    assert {target for _, target, _, _ in result.read_arcs} >= {"E", "target"}
    assert result.stats["partial_reasons"] == ("node_budget",)


def test_cached_arcs_propagate_later_support_without_being_read_twice():
    # Both weak seeds open and cache their outgoing arcs before A's second arc
    # reaches B. B's already-read B->C arc must carry that later improvement.
    rows = {"A": [("decoy", 1), ("B", .9)], "B": [("C", 1)],
            "C": [("target", 1)], "decoy": [("other", .1)],
            "other": [], "target": []}
    view = View(rows)
    result = read.discover_first_hit_read(view, {"A": .98, "B": .01, "C": .01},
                                          FirstHitPolicy(max_nodes=5, max_edges=20))
    assert result.path_strength["C"] == pytest.approx(.98 * .75 * .9 / 1.9 * .75)
    assert result.path_strength["target"] == pytest.approx(result.path_strength["C"] * .75)
    assert "other" not in result.node_ids
    assert len(view.reads) == len(set(view.reads)) == result.stats["edges_read"]
    assert result.stats["local_relaxations"] > result.stats["edges_read"]


def test_diamond_max_path_schedules_but_solve_adds_distinct_paths():
    rows = {"seed": [("a", 1), ("b", 1)], "a": [("join", 1)],
            "b": [("join", 1)], "join": []}
    result = read.discover_first_hit_read(View(rows), {"seed": 1}, FirstHitPolicy())
    assert result.path_strength["join"] == pytest.approx(.5 * .75 ** 2)
    assert result.h["join"] == pytest.approx(.75 ** 2)
    assert not result.stats["partial"]


def test_cycle_never_adds_support_or_consumes_unbounded_work():
    result = read.discover_first_hit_read(View({"a": [("b", 1)], "b": [("a", 1)]}),
                                          {"a": .9, "b": .1}, FirstHitPolicy())
    assert result.path_strength == {"a": .9, "b": .675}
    assert result.h == pytest.approx({"a": .975, "b": .775})
    assert result.stats["path_improvements"] == 1
    assert result.stats["queue_updates"] <= result.stats["queue_update_limit"]
    assert result.stats["local_relaxations"] <= result.stats["local_relaxation_limit"]
    assert result.stats["queue_pops"] <= result.stats["queue_updates"]


def test_weak_bridge_zero_attention_still_transmits():
    rows = {"seed": [("bridge", .01)], "bridge": [("goal", 1)], "goal": []}
    result = read.discover_first_hit_read(View(rows), {"seed": 1},
                                          FirstHitPolicy(attention_penalty=.1))
    assert result.h["goal"] == pytest.approx(.01 * .75 ** 2)
    assert result.attention["bridge"] == result.attention["goal"] == 0


@pytest.mark.parametrize("max_nodes,max_edges", [(1, 0), (2, 1), (4, 3), (64, 256)])
def test_hub_does_not_expand_or_redistribute_unread_adjacency(max_nodes, max_edges):
    rows = {"seed": [(f"n{i:04}", 1) for i in range(1000)]}
    rows.update({f"n{i:04}": [] for i in range(1000)})
    view = View(rows)
    result = read.discover_first_hit_read(view, {"seed": 1},
        FirstHitPolicy(max_nodes=max_nodes, max_edges=max_edges))
    assert len(result.node_ids) <= max_nodes
    assert len(view.reads) == result.stats["edges_read"] == max_edges
    assert len(set(view.reads)) == len(view.reads)
    assert all(value == pytest.approx(.75 / 1000)
               for key, value in result.h.items() if key != "seed")
    assert result.delta == pytest.approx(.75 * (1 - (len(result.node_ids) - 1) / 1000))
    assert result.stats["queue_update_limit"] == max_edges * (max_nodes + 1)
    assert result.stats["local_relaxation_limit"] == max_edges * max_nodes
    assert result.stats["partial"]


@pytest.mark.parametrize("limits,reason", [((0, 20), "queue_budget"),
                                           ((20, 0), "relaxation_budget")])
def test_internal_work_cap_returns_marked_partial_with_actual_peek(limits, reason, monkeypatch):
    # Force the boundary at the first unit to verify the exhaustion branch;
    # production limits remain derived exclusively from the original budgets.
    monkeypatch.setattr(read, "_work_limits", lambda policy: limits)
    view = View({"a": [("b", 1)], "b": []})
    result = read.discover_first_hit_read(view, {"a": .5, "b": .5}, FirstHitPolicy())
    assert reason in result.stats["partial_reasons"]
    assert result.stats["partial"]
    assert result.stats["edges_read"] == 1
    assert result.stats["queue_updates"] <= limits[0]
    assert result.stats["local_relaxations"] <= limits[1]
    # The peek already read a real local arc: stopping queue work cannot erase it.
    assert result.P[0, 1] == 1
    assert result.h["b"] == pytest.approx(.875)


def test_explicit_groups_reuse_one_solve_and_keep_mixture_ambiguity_visible(monkeypatch):
    rows = {"s1": [("only1", .6), ("both", .3)],
            "s2": [("only2", .6), ("both", .3)],
            "only1": [], "only2": [], "both": []}
    calls = []
    original = np.linalg.solve
    def solve(*args):
        calls.append(args[0].shape)
        return original(*args)
    monkeypatch.setattr(np.linalg, "solve", solve)
    result = read.discover_first_hit_read(View(rows), {"s1": .5, "s2": .5},
        FirstHitPolicy(), group_seeds={"cue1": {"s1": 1}, "cue2": {"s2": 1}})
    assert calls == [(5, 5)]
    assert result.h["only1"] == result.h["only2"] == result.h["both"]
    assert result.group_h["cue1"]["only2"] == result.group_h["cue2"]["only1"] == 0
    assert min(values["both"] for values in result.group_h.values()) == pytest.approx(.225)
    assert np.asarray([list(values.values()) for values in result.group_h.values()]).shape == (2, 5)
    assert result.b.sum() == 1
    assert all(mass <= 1 for mass in result.stats["group_seed_mass"].values())
    assert not result.P.flags.writeable and not result.b.flags.writeable
    assert not result.full_row_mass.flags.writeable


def test_groups_cannot_expand_shared_seeds_or_redistribute_missing_mass():
    rows = {key: [] for key in "abcdef"}
    result = read.discover_first_hit_read(View(rows), dict.fromkeys(rows, 1), FirstHitPolicy(),
        group_seeds={"partly": {"a": 2, "f": 2}, "absent": {"f": 1}, "empty": {}})
    assert result.node_ids == tuple("abcde")
    assert result.group_seed_weights == {"partly": {"a": .5}, "absent": {}, "empty": {}}
    assert result.group_h["partly"]["a"] == .5
    assert not any(result.group_h["absent"].values())
    assert result.stats["group_missing_seeds"] == {"partly": ("f",), "absent": ("f",), "empty": ()}
    assert result.stats["partial_reasons"] == ("seed_budget",)


def test_empty_no_groups_and_group_limit():
    result = read.discover_first_hit_read(View({}), {}, FirstHitPolicy(), group_seeds={"missing": {"x": 1}})
    assert result.group_h == {"missing": {}}
    assert result.P.shape == (0, 0) and result.b.shape == result.full_row_mass.shape == (0,)
    assert result.delta == 0 and result.stats["local_relaxations"] == 0
    with pytest.raises(FirstHitUnavailable, match="group_invalid"):
        read.discover_first_hit_read(View({}), {}, FirstHitPolicy(), group_seeds={str(i): {} for i in range(6)})


@pytest.mark.parametrize("group", [{"negative": {"a": -1}}, {"nan": {"a": float("nan")}},
                                   {"boolean": {"a": True}}, {"": {}}, [1]])
def test_invalid_group_input_is_explicit(group):
    with pytest.raises(FirstHitUnavailable):
        read.discover_first_hit_read(View({"a": []}), {"a": 1}, FirstHitPolicy(), group_seeds=group)


def test_excluded_mass_and_snapshot_change_remain_explicit():
    result = read.discover_first_hit_read(View({"a": [("excluded", 1)], "excluded": []}),
        {"a": 1}, FirstHitPolicy(), excluded_node_ids=("excluded",))
    assert result.node_ids == ("a",) and result.delta == .75
    assert result.read_arcs == (("a", "excluded", 1, "SEMANTIC"),)
    class ChangingView(View):
        def cursor(self, key, version):
            for row in super().cursor(key, version):
                self.version += 1
                yield row
    with pytest.raises(FirstHitUnavailable, match="snapshot_unavailable"):
        read.discover_first_hit_read(ChangingView({"a": [("b", 1)], "b": []}), {"a": 1}, FirstHitPolicy())


def test_global_and_group_numerics_match_fixed_local_problem():
    rng = np.random.default_rng(12017)
    for size in (0, 1, 2, 7, 64):
        P = rng.uniform(size=(size, size))
        if size:
            P /= np.maximum(1, P.sum(axis=1))[:, None]
        b = rng.uniform(size=size)
        b /= max(1, b.sum())
        rows = {str(i): [(str(j), P[i, j]) for j in range(size)] for i in range(size)}
        seeds = {str(i): float(b[i]) for i in range(size)}
        result = read.discover_first_hit_read(View(rows), seeds, FirstHitPolicy(),
                                               group_seeds={"same": seeds})
        expected_h, expected_delta = solve_first_hit(result.P, result.b, result.full_row_mass)
        assert np.array_equal(np.array(list(result.h.values())), expected_h)
        assert result.delta == expected_delta
        assert result.group_h["same"] == pytest.approx(result.h)


def test_cached_strength_is_max_product_not_accumulated_visits():
    rng = np.random.default_rng(72931)
    for _ in range(40):
        keys = [str(i) for i in range(int(rng.integers(1, 20)))]
        rows = {source: [(target, float(rng.uniform())) for target in keys if rng.uniform() < .3]
                for source in keys}
        seeds = {key: float(rng.uniform()) for key in keys[:5]}
        policy = FirstHitPolicy(max_nodes=int(rng.integers(1, 20)),
                                max_edges=int(rng.integers(0, 100)))
        view = View(rows)
        result = read.discover_first_hit_read(view, seeds, policy)
        # Independent synchronous max-product recurrence on the frozen physical
        # arcs. No simple best path needs more than N-1 edges when decay < 1.
        expected = dict.fromkeys(result.node_ids, 0.0)
        expected.update(result.seed_weights)
        for _ in result.node_ids:
            previous = dict(expected)
            for source, target, probability, _family in result.read_arcs:
                if target in expected:
                    expected[target] = max(expected[target], previous[source] * .75 * probability)
        assert result.path_strength == pytest.approx(expected)
        assert len(view.reads) == len(set(view.reads)) <= policy.max_edges
        assert result.stats["queue_updates"] <= result.stats["queue_update_limit"]
        assert result.stats["local_relaxations"] <= result.stats["local_relaxation_limit"]


def test_legacy_reader_retains_frozen_cycle_values_and_order():
    # Recorded from the pre-upgrade default reader; repository history is not
    # required to execute this maintained regression in a source distribution.
    rows = {"a": [("b", .4), ("c", .6)], "b": [("c", 1)], "c": [("a", .3)]}
    result = discover_first_hit(View(rows), {"a": .6, "b": .4}, FirstHitPolicy())
    assert result.node_ids == ("a", "b", "c")
    assert result.h == pytest.approx({"a": .6675, "b": .6002781641168289, "c": .7050000000000001})
    assert result.attention == pytest.approx({"a": .343240611961057, "b": .2760187760778859,
                                            "c": .3807406119610571})
    assert result.delta == 0 and result.seed_weights == {"a": .6, "b": .4}


def test_persisted_role_labels_do_not_add_navigation_mass():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fact = SimpleNamespace(node_id="fact", node_type="EVENT", content_narrative="source fact",
        timestamp=now, attributes={"evidence_id": "ev", "provenance": {
            "segment_id": "s", "conversation_id": "c", "turn_id": "t", "source_role": "user",
            "source_timestamp": now.isoformat(), "source_timezone": "UTC", "ingestion_version": "v6"}})
    entity = SimpleNamespace(node_id="entity", node_type="ENTITY", attributes={"entity_ref": "E"})
    def edge(role, status="ACTIVE"):
        return SimpleNamespace(source_node_id="fact", target_node_id="entity", link_type="ENTITY",
            metadata={"status": status}, properties={"sub_type": "REFERS_TO", "role": role})
    graph = SimpleNamespace(nodes={"fact": fact, "entity": entity}, links={
        1: edge("subject"), 2: edge(None), 3: edge(["subject"]), 4: edge("object", "INACTIVE")})
    view = DerivedFirstHitGraph(graph)
    assert view.entity_roles("fact", "E") == frozenset({"subject", "ordinary"})
    assert view.entity_roles("fact", "missing") == frozenset()
    graph.links[5] = edge("object")
    view.add_link(graph.links[5])
    assert view.entity_roles("fact", "E") == frozenset({"subject", "object", "ordinary"})
    assert view.outgoing_weight("fact") == view.outgoing_weight("entity") == 1
    old = discover_first_hit(view, {"fact": 1}, FirstHitPolicy())
    new = read.discover_first_hit_read(view, {"fact": 1}, FirstHitPolicy())
    assert old.h == new.h and old.delta == new.delta
