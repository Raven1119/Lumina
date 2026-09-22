"""Shared-budget multi-frontier mechanism checks; no models or real state."""
import numpy as np
import pytest

from Conversation_Memory.adapter import _first_hit_read as read
from Conversation_Memory.adapter.first_hit import FirstHitPolicy, FirstHitUnavailable
from Conversation_Memory.tests.test_first_hit_read import View


def test_round_robin_gives_weak_second_cue_a_frontier_under_shared_node_cap():
    rows = {"a": [("a1", 1)], "a1": [("a2", 1)], "a2": [("a3", 1)],
            "a3": [], "b": [("target", 1)], "target": []}
    seeds = {"a": .99, "b": .01}
    policy = FirstHitPolicy(max_nodes=4)
    groups = {"first": {"a": 1}, "second": {"b": 1}}
    old = read.discover_first_hit_read(View(rows), seeds, policy, group_seeds=groups)
    view = View(rows)
    new = read.discover_query_first_hit(view, seeds, policy, group_seeds=groups)
    assert "target" not in old.node_ids
    assert new.node_ids == ("a", "b", "a1", "target")
    assert new.group_h["second"]["target"] == pytest.approx(.75)
    assert new.h["target"] == pytest.approx(.0075)
    assert new.stats["frontier_work"]["second"]["turns"] > 0
    assert len(view.reads) == len(set(view.reads)) == new.stats["edges_read"]
    assert new.stats["partial_reasons"] == ("node_budget",)


def test_initial_peeks_rotate_before_one_groups_extra_seed_peeks():
    rows = {"a1": [("x", 1)], "a2": [("y", 1)], "b": [("target", 1)],
            "x": [], "y": [], "target": []}
    view = View(rows)
    result = read.discover_query_first_hit(view, {"a1": .49, "a2": .49, "b": .02},
        FirstHitPolicy(max_edges=2), group_seeds={"a": {"a1": .5, "a2": .5}, "b": {"b": 1}})
    assert view.reads == [("a1", 0), ("b", 0)]
    assert "target" in result.node_ids and "y" not in result.node_ids
    assert result.stats["edges_read"] == 2


def test_three_frontiers_reuse_physical_arcs_and_complete_hub_denominator():
    rows = {"hub": [(f"n{i:04}", 1) for i in range(1000)]}
    rows.update({f"n{i:04}": [] for i in range(1000)})
    view = View(rows)
    result = read.discover_query_first_hit(view, {"hub": 1}, FirstHitPolicy(max_edges=7),
        group_seeds={str(i): {"hub": 1} for i in range(3)})
    assert len(view.reads) == len(set(view.reads)) == result.stats["edges_read"] == 7
    assert result.stats["cached_arc_reuses"] == 14
    assert result.stats["physical_arc_notifications"] == 3 * 7
    assert result.delta == pytest.approx(.75 * 993 / 1000)
    assert all(result.h[f"n{i:04}"] == pytest.approx(.75 / 1000) for i in range(7))
    assert all(values == result.h for values in result.group_h.values())
    assert len(result.read_arcs) == 7
    assert result.P.sum() == pytest.approx(.007)


def test_late_strong_path_updates_weak_seed_and_cached_transmission_for_a_cue():
    rows = {"a": [("decoy", 1), ("b", .9)], "b": [("c", 1)],
            "c": [("target", 1)], "decoy": [("other", .1)],
            "other": [], "target": []}
    view = View(rows)
    seeds = {"a": .98, "b": .01, "c": .01}
    result = read.discover_query_first_hit(view, seeds, FirstHitPolicy(max_nodes=5),
                                           group_seeds={"cue": seeds})
    paths = result.stats["group_path_strength"]["cue"]
    assert paths["b"] == pytest.approx(.98 * .75 * .9 / 1.9)
    assert paths["c"] == pytest.approx(paths["b"] * .75)
    assert paths["target"] == pytest.approx(paths["c"] * .75)
    assert "other" not in result.node_ids
    assert result.stats["stale_queue_pops"] > 0
    assert len(view.reads) == len(set(view.reads))


def test_shared_admission_makes_old_cached_incoming_support_available_to_other_cue():
    rows = {"a": [("join", 1)], "b": [("join", 1)], "join": [("goal", 1)], "goal": []}
    result = read.discover_query_first_hit(View(rows), {"a": .5, "b": .5}, FirstHitPolicy(),
        group_seeds={"left": {"a": 1}, "right": {"b": 1}})
    for name in ("left", "right"):
        assert result.stats["group_path_strength"][name]["join"] == pytest.approx(.75)
        assert result.stats["group_path_strength"][name]["goal"] == pytest.approx(.75 ** 2)
    assert result.h["goal"] == pytest.approx(.75 ** 2)
    assert result.stats["edges_read"] == 3
    assert result.stats["cached_arc_reuses"] > 0


def test_cycles_and_diamonds_do_not_accumulate_scheduling_support():
    rows = {"seed": [("a", 1), ("b", 1)], "a": [("join", 1)],
            "b": [("join", 1)], "join": [("seed", 1)]}
    result = read.discover_query_first_hit(View(rows), {"seed": 1}, FirstHitPolicy(),
        group_seeds={"one": {"seed": 1}, "two": {"seed": .5}})
    assert result.stats["group_path_strength"]["one"]["seed"] == 1
    assert result.stats["group_path_strength"]["one"]["join"] == pytest.approx(.5 * .75 ** 2)
    assert result.group_h["one"]["join"] == pytest.approx(.75 ** 2)
    assert result.group_h["two"]["join"] == pytest.approx(.5 * .75 ** 2)
    assert result.stats["queue_pops"] <= result.stats["queue_updates"] <= result.stats["queue_update_limit"]
    assert result.stats["local_relaxations"] <= result.stats["local_relaxation_limit"]


def test_zero_attention_bridge_is_not_removed_from_any_frontier():
    rows = {"seed": [("bridge", .01)], "bridge": [("goal", 1)], "goal": []}
    result = read.discover_query_first_hit(View(rows), {"seed": 1},
        FirstHitPolicy(attention_penalty=.1), group_seeds={"one": {"seed": 1}, "two": {"seed": .5}})
    assert result.h["goal"] == pytest.approx(.01 * .75 ** 2)
    assert result.group_h["two"]["goal"] == pytest.approx(.5 * .01 * .75 ** 2)
    assert result.attention["bridge"] == result.attention["goal"] == 0
    assert result.stats["edges_read"] == 2


@pytest.mark.parametrize("limit,reason", [((0, 100), "queue_budget"), ((100, 0), "relaxation_budget")])
def test_total_work_caps_return_a_truthful_frozen_partial(limit, reason, monkeypatch):
    monkeypatch.setattr(read, "_query_work_limits", lambda *args: limit)
    view = View({"a": [("b", 1)], "b": []})
    result = read.discover_query_first_hit(view, {"a": .5, "b": .5}, FirstHitPolicy(),
                                           group_seeds={"one": {"a": 1}, "two": {"b": 1}})
    assert result.stats["partial"] and reason in result.stats["partial_reasons"]
    assert result.stats["queue_updates"] <= limit[0]
    assert result.stats["local_relaxations"] <= limit[1]
    assert result.stats["edges_read"] == 1
    assert result.P[0, 1] == 1 and result.h["b"] == pytest.approx(.875)


@pytest.mark.parametrize("nodes,edges", [(1, 0), (2, 1), (4, 3), (128, 500)])
def test_one_global_node_and_edge_cap_not_one_per_group(nodes, edges):
    rows = {"hub": [(str(i), 1) for i in range(600)]}
    rows.update({str(i): [] for i in range(600)})
    view = View(rows)
    result = read.discover_query_first_hit(view, {"hub": 1},
        FirstHitPolicy(max_nodes=nodes, max_edges=edges), group_seeds={str(i): {"hub": 1} for i in range(3)})
    n, e = min(nodes, 64), min(edges, 256)
    assert len(result.node_ids) <= n
    assert len(view.reads) == len(set(view.reads)) == result.stats["edges_read"] == e
    assert result.stats["queue_update_limit"] == 3 * e * (n + 1)
    assert result.stats["local_relaxation_limit"] == 3 * e * (n + 1)
    assert result.stats["queue_updates"] <= result.stats["queue_update_limit"]
    assert result.stats["local_relaxations"] <= result.stats["local_relaxation_limit"]


def test_all_group_scores_share_one_resolvent_and_frozen_matrices(monkeypatch):
    original = np.linalg.solve
    calls = []
    def solve(*args):
        calls.append(args[0].shape)
        return original(*args)
    monkeypatch.setattr(np.linalg, "solve", solve)
    rows = {"a": [("goal", 1)], "b": [("goal", 1)], "goal": []}
    result = read.discover_query_first_hit(View(rows), {"a": .5, "b": .5}, FirstHitPolicy(),
        group_seeds={"a": {"a": 1}, "b": {"b": 1}, "both": {"a": .5, "b": .5}})
    assert calls == [(3, 3)]
    assert result.h["goal"] == pytest.approx(.75)
    assert all(values["goal"] == pytest.approx(.75) for values in result.group_h.values())
    assert not result.P.flags.writeable and not result.b.flags.writeable and not result.full_row_mass.flags.writeable
    assert max(result.stats["group_seed_mass"].values()) <= 1


def test_empty_cue_uses_global_frontier_but_never_acquires_fabricated_group_support():
    result = read.discover_query_first_hit(View({"a": [("goal", 1)], "goal": [], "missing": []}),
        {"a": 1}, FirstHitPolicy(), group_seeds={"uncovered": {"missing": 1}, "empty": {}})
    assert result.h["goal"] == pytest.approx(.75)
    assert result.stats["frontier_fallback"]
    assert result.stats["uncovered_seed_groups"] == ("uncovered", "empty")
    assert result.stats["group_missing_seeds"] == {"uncovered": ("missing",), "empty": ()}
    assert all(not any(scores.values()) for scores in result.group_h.values())
    assert "missing" not in result.node_ids


def test_global_direct_seed_stays_in_b_even_when_not_in_cue_masks():
    rows = {"direct": [], "cue": [("goal", 1)], "goal": []}
    result = read.discover_query_first_hit(View(rows), {"direct": .9, "cue": .1}, FirstHitPolicy(),
                                           group_seeds={"cue": {"cue": 1}})
    assert result.seed_weights == {"direct": .9, "cue": .1}
    assert result.h["direct"] == .9 and result.h["goal"] == pytest.approx(.075)
    assert result.group_h["cue"]["direct"] == 0
    assert result.path_strength["direct"] == 0
    assert result.stats["path_strength_meaning"] == "maximum_cue_scheduling_support_not_global_first_hit"


def test_seed_group_exclusion_and_upper_limits_are_explicit():
    rows = {str(i): [] for i in range(9)}
    result = read.discover_query_first_hit(View(rows), dict.fromkeys(rows, 1),
        FirstHitPolicy(max_seeds=10), group_seeds={"partial": {"0": 1, "8": 1}}, excluded_node_ids=("0",))
    assert result.node_ids == ("1", "2", "3", "4", "5")
    assert result.group_seed_weights["partial"] == {}
    assert result.stats["group_missing_seeds"]["partial"] == ("0", "8")
    assert "seed_budget" in result.stats["partial_reasons"]
    with pytest.raises(FirstHitUnavailable, match="group_invalid"):
        read.discover_query_first_hit(View({}), {}, FirstHitPolicy(), group_seeds={str(i): {} for i in range(4)})


def test_excluded_arc_is_charged_once_and_keeps_its_exit_mass_for_every_cue():
    view = View({"seed": [("excluded", 1), ("goal", 1)], "excluded": [], "goal": []})
    result = read.discover_query_first_hit(view, {"seed": 1}, FirstHitPolicy(),
        group_seeds={"one": {"seed": 1}, "two": {"seed": 1}}, excluded_node_ids=("excluded",))
    assert result.node_ids == ("seed", "goal")
    assert result.h["goal"] == pytest.approx(.375) and result.delta == pytest.approx(.375)
    assert result.stats["edges_read"] == len(view.reads) == len(set(view.reads)) == 2
    assert result.stats["partial_reasons"] == ("omitted_arcs",)
    assert len(result.read_arcs) == 2


def test_empty_input_has_consistent_shapes_and_never_solves(monkeypatch):
    monkeypatch.setattr(np.linalg, "solve", lambda *args: pytest.fail("empty matrix solve"))
    result = read.discover_query_first_hit(View({}), {}, FirstHitPolicy(), group_seeds={"empty": {}})
    assert result.P.shape == (0, 0) and result.b.shape == result.full_row_mass.shape == (0,)
    assert result.group_h == {"empty": {}}
    assert result.stats["edges_read"] == result.stats["queue_updates"] == result.stats["local_relaxations"] == 0


def test_snapshot_change_fails_without_query_rebuild():
    class Changing(View):
        def cursor(self, key, version):
            for row in super().cursor(key, version):
                self.version += 1
                yield row
    with pytest.raises(FirstHitUnavailable, match="snapshot_unavailable"):
        read.discover_query_first_hit(Changing({"a": [("b", 1)], "b": []}), {"a": 1}, FirstHitPolicy())


def test_seed_only_control_uses_same_entries_groups_and_denominators_with_no_cursor(monkeypatch):
    rows = {"a": [("b", 1), ("goal", 1)], "b": [("goal", 1)], "goal": []}
    seeds = {"a": .6, "b": .4}
    groups = {"a": {"a": 1}, "b": {"b": 1}}
    expanded = read.discover_query_first_hit(View(rows), seeds, FirstHitPolicy(), group_seeds=groups)
    class NoCursor(View):
        def cursor(self, *args):
            pytest.fail("seed-only control opened physical adjacency")
    calls = []
    original = np.linalg.solve
    def solve(*args):
        calls.append(args[0].shape)
        return original(*args)
    monkeypatch.setattr(np.linalg, "solve", solve)
    result = read.discover_query_first_hit(NoCursor(rows), seeds, FirstHitPolicy(),
                                           group_seeds=groups, seed_only=True)
    assert calls == [(2, 2)]
    assert result.seed_weights == expanded.seed_weights
    assert result.group_seed_weights == expanded.group_seed_weights
    assert result.node_ids == ("a", "b") and result.read_arcs == ()
    assert not result.P.any() and result.full_row_mass.tolist() == [1, 1]
    assert result.h == seeds and result.group_h == {"a": {"a": 1, "b": 0}, "b": {"a": 0, "b": 1}}
    assert result.delta == pytest.approx(.75)
    assert result.stats["edges_read"] == result.stats["queue_updates"] == result.stats["local_relaxations"] == 0
    assert result.stats["partial_reasons"] == ("exploration_disabled",)
    assert not result.stats["budget_exhausted"] and result.stats["seed_only"]
    with pytest.raises(FirstHitUnavailable, match="seed_only_invalid"):
        read.discover_query_first_hit(View(rows), seeds, FirstHitPolicy(), seed_only=1)


def test_each_cue_path_matches_independent_max_product_on_shared_read_arcs():
    rng = np.random.default_rng(53711)
    for _ in range(35):
        keys = [str(i) for i in range(int(rng.integers(1, 16)))]
        rows = {source: [(target, float(rng.uniform())) for target in keys if rng.uniform() < .25]
                for source in keys}
        seeds = {key: float(rng.uniform()) for key in keys[:5]}
        groups = {str(i): {key: value for j, (key, value) in enumerate(seeds.items()) if j % 3 == i}
                  for i in range(3)}
        policy = FirstHitPolicy(max_nodes=int(rng.integers(1, 16)), max_edges=int(rng.integers(0, 80)))
        view = View(rows)
        result = read.discover_query_first_hit(view, seeds, policy, group_seeds=groups)
        for name, actual in result.stats["group_path_strength"].items():
            expected = dict.fromkeys(result.node_ids, 0.0)
            expected.update(result.group_seed_weights[name])
            for _ in result.node_ids:
                previous = dict(expected)
                for source, target, probability, _family in result.read_arcs:
                    if target in expected:
                        expected[target] = max(expected[target], previous[source] * .75 * probability)
            assert actual == pytest.approx(expected)
        assert len(view.reads) == len(set(view.reads)) <= policy.max_edges
        assert result.stats["queue_pops"] <= result.stats["queue_updates"] <= result.stats["queue_update_limit"]
        assert result.stats["local_relaxations"] <= result.stats["local_relaxation_limit"]


def test_v1_frozen_counterexample_values_are_unchanged_after_v2_addition():
    rows = {"A": [("B", 1), ("D", .5)], "B": [("target", 1)],
            "D": [("E", 1)], "E": [], "target": []}
    result = read.discover_first_hit_read(View(rows), {"A": .99, "B": .01},
                                          FirstHitPolicy(max_nodes=4, max_edges=10))
    assert result.node_ids == ("A", "B", "target", "D")
    assert result.h == {"A": .99, "B": .505, "target": .37875, "D": .2475}
    assert result.attention == {"A": .69875, "B": .21375, "target": .08749999999999997, "D": 0.0}
    assert result.stats["edges_read"] == 4 and result.stats["queue_updates"] == 5
    assert result.stats["local_relaxations"] == 4 and result.stats["stale_queue_pops"] == 1
