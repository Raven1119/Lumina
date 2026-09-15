"""Deterministic correctness checks, using synthetic graphs and isolated state."""
from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from adapter.backend import RealMagmaBackend
from adapter.first_hit import (DerivedFirstHitGraph, FirstHitPolicy, FirstHitUnavailable,
                               discover_first_hit, project_attention, solve_first_hit)


@pytest.fixture
def graph_types(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "upstream/MAGMA/memory/graph_db.py"
    spec = importlib.util.spec_from_file_location("_first_hit_graph_types", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "memory.graph_db", module)
    return module


def fact(types, key, **attributes):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return types.EventNode(node_id=key, timestamp=now, content_narrative="fact " + key,
        embedding_vector=[1.0, 0.0], attributes={"evidence_id": "e-" + key,
            "provenance": {"segment_id": "s-" + key, "conversation_id": "conversation",
                "turn_id": "t-" + key, "source_role": "user", "source_timestamp": now.isoformat(),
                "source_timezone": "UTC", "ingestion_version": "grounded-formation-v2"},
            **attributes})


def link(types, graph, source, target, weight=1.0, family="SEMANTIC", **properties):
    subtype = {"SEMANTIC": "RELATED_TO", "ENTITY": "REFERS_TO", "TEMPORAL": "PRECEDES",
               "CAUSAL": "LEADS_TO"}[family]
    edge = types.Link(source_node_id=source, target_node_id=target,
                      link_type=types.LinkType(family), properties={"sub_type": subtype,
                      "similarity_score": weight, **properties})
    graph.add_link(edge)
    return edge


def independent_absorbing(P, b, decay):
    # For each target solve a separate boundary-value equation f[target]=1.
    values = []
    for target in range(len(b)):
        K = np.eye(len(b)) - decay * P
        K[target, :] = 0.0
        K[target, target] = 1.0
        rhs = np.zeros(len(b)); rhs[target] = 1.0
        values.append(float(b @ np.linalg.solve(K, rhs)))
    return values


@pytest.mark.parametrize("P,b", [
    ([[0,1,0], [0,0,1], [0,0,0]], [1,0,0]),
    ([[0,.5,.5,0], [0,0,0,1], [0,0,0,1], [0,0,0,0]], [1,0,0,0]),
    ([[0,1], [1,0]], [1,0]),
    ([[1]], [0.4]),
    ([[0,0], [0,0]], [.5,.5]),
    ([[.4,.3,0], [.2,.2,.4], [.1,0,.6]], [.2,.3,.1]),
])
def test_first_hit_matches_independent_target_absorption(P, b):
    P, b = np.array(P, float), np.array(b, float)
    h, delta = solve_first_hit(P, b, P.sum(axis=1))
    assert h == pytest.approx(independent_absorbing(P, b, .75))
    assert delta == pytest.approx(0)


def test_empty_and_cycle_first_arrival_not_visit_count():
    h, delta = solve_first_hit(np.empty((0, 0)), [], [])
    assert h.size == 0 and delta == 0
    h, _ = solve_first_hit([[0,1], [1,0]], [1,0], [1,1])
    assert h == pytest.approx([1,.75])
    assert project_attention([.2,.1], budget=1).sum() == pytest.approx(.3)


def test_local_leakage_bound_and_monotonic_addition_fixed_denominators():
    full = np.array([[0,.6,.4,0], [.1,0,0,.9], [0,.4,0,.6], [0,0,1,0]])
    local = full.copy(); local[0,2] = 0; local[1,3] = 0; local[3,2] = 0
    b = np.array([1,0,0,0])
    h_full, _ = solve_first_hit(full, b, full.sum(axis=1))
    h_local, delta = solve_first_hit(local, b, full.sum(axis=1))
    assert np.all(h_full >= h_local - 1e-12)
    assert np.all(h_full - h_local <= delta + 1e-12)
    local[0,2] = full[0,2]
    h_added, delta_added = solve_first_hit(local, b, full.sum(axis=1))
    assert np.all(h_added >= h_local - 1e-12)
    assert delta_added <= delta + 1e-12


def test_attention_projection_kkt_and_symmetric_candidates():
    h = np.array([.9,.9,.6,.01])
    a = project_attention(h, budget=1, penalty=.1)
    assert a.sum() == pytest.approx(1)
    assert a[0] == a[1] and a[-1] == 0
    dual = h[a > 0] - .1 - a[a > 0]
    assert dual == pytest.approx(np.repeat(dual[0], len(dual)))
    assert np.all(h[a == 0] - .1 <= dual[0])
    assert project_attention(h, budget=0).sum() == 0


@pytest.mark.parametrize("P,b,full,decay", [
    ([[float("nan")]], [1], [1], .75), ([[1.01]], [1], [1], .75),
    ([[-.1]], [1], [1], .75), ([[0]], [1.1], [1], .75),
    ([[0]], [-1], [1], .75), ([[0]], [1], [1], 1),
    ([[0]], [1], [float("inf")], .75), ([[0]], [1,0], [1], .75),
])
def test_invalid_numerics_are_explicit(P, b, full, decay):
    with pytest.raises(FirstHitUnavailable):
        solve_first_hit(P, b, full, decay)


def test_numerical_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(np.linalg, "solve", lambda *a: np.array([[float("nan")]]))
    with pytest.raises(FirstHitUnavailable, match="solve_invalid"):
        solve_first_hit([[0]], [1], [0])


def test_weak_bridge_propagates_before_final_competition(graph_types):
    t=graph_types; graph=t.NetworkXGraphDB()
    for key in ("seed", "bridge", "goal"):
        graph.add_node(fact(t,key))
    link(t,graph,"seed","bridge",.01)
    link(t,graph,"bridge","goal",1)
    result=discover_first_hit(DerivedFirstHitGraph(graph), {"seed":1},
                             FirstHitPolicy(attention_penalty=.1))
    assert result.h["goal"] == pytest.approx(.01*.75*.75)
    assert result.attention["bridge"] == 0 and result.attention["goal"] == 0
    assert result.stats["nodes_read"] == 3


def test_thousand_neighbor_cursor_actual_budget_and_unredistributed_mass(graph_types):
    t=graph_types; graph=t.NetworkXGraphDB(); graph.add_node(fact(t,"seed"))
    for i in range(1000):
        key=f"n{i:04}"; graph.add_node(fact(t,key)); link(t,graph,"seed",key)
    view=DerivedFirstHitGraph(graph)
    # Query cannot scan the authoritative links or reconstruct the view.
    class NoIteration(dict):
        def values(self): raise AssertionError("query scanned links")
        def items(self): raise AssertionError("query scanned links")
        def __iter__(self): raise AssertionError("query scanned links")
    graph.links=NoIteration(graph.links); view._source=view._signature()
    result=discover_first_hit(view,{"seed":1},FirstHitPolicy(max_edges=3))
    assert result.stats["edges_read"] == 3
    assert result.stats["nodes_read"] == 4
    assert result.h["n0000"] == pytest.approx(.75/1000)
    assert result.delta == pytest.approx(.75*997/1000)


def test_already_local_unread_edge_is_not_free(graph_types):
    t=graph_types; graph=t.NetworkXGraphDB()
    for key in ("a","b"): graph.add_node(fact(t,key))
    link(t,graph,"a","b")
    result=discover_first_hit(DerivedFirstHitGraph(graph),{"a":.5,"b":.5},
                             FirstHitPolicy(max_edges=0))
    assert result.h == {"a":.5,"b":.5}
    assert result.delta == pytest.approx(.375)
    assert result.stats["edges_read"] == 0


def test_duplicate_roles_max_channels_no_causal_or_legacy_clique(graph_types):
    t=graph_types; graph=t.NetworkXGraphDB()
    for key in ("a","b","causal"): graph.add_node(fact(t,key))
    graph.add_node(t.EventNode(node_id="entity:e",node_type=t.NodeType.ENTITY,
                              attributes={"entity_ref":"E"}))
    link(t,graph,"a","b",.3); link(t,graph,"a","b",.7); link(t,graph,"a","b",.7)
    link(t,graph,"a","entity:e",family="ENTITY",role="subject")
    link(t,graph,"a","entity:e",family="ENTITY",role="object")
    link(t,graph,"a","b",family="ENTITY",entity="E")
    link(t,graph,"a","causal",family="CAUSAL")
    view=DerivedFirstHitGraph(graph)
    assert view.outgoing_weight("a") == pytest.approx(1.7)
    assert view.outgoing_weight("entity:e") == 1
    result=discover_first_hit(view,{"a":1},FirstHitPolicy())
    assert "causal" not in result.node_ids
    assert "entity:e" in result.node_ids and "entity:e" not in result.attention


def test_missing_semantic_weight_is_unavailable_not_maximal(graph_types):
    t=graph_types; graph=t.NetworkXGraphDB()
    for key in ("a","b"): graph.add_node(fact(t,key))
    edge=link(t,graph,"a","b"); del edge.properties["similarity_score"]
    with pytest.raises(FirstHitUnavailable,match="weight_unavailable"):
        discover_first_hit(DerivedFirstHitGraph(graph),{"a":1},FirstHitPolicy())


def test_snapshot_mutation_refused_without_query_rebuild(graph_types):
    t=graph_types; graph=t.NetworkXGraphDB(); graph.add_node(fact(t,"a"))
    view=DerivedFirstHitGraph(graph)
    graph.add_node(fact(t,"b"))
    with pytest.raises(FirstHitUnavailable,match="snapshot_unavailable"):
        discover_first_hit(view,{"a":1},FirstHitPolicy())


def test_excluded_batch_remains_exit_mass(graph_types):
    t=graph_types; graph=t.NetworkXGraphDB()
    for key in ("a","new"): graph.add_node(fact(t,key))
    link(t,graph,"a","new")
    result=discover_first_hit(DerivedFirstHitGraph(graph),{"a":1},FirstHitPolicy(),
                             excluded_node_ids={"new"})
    assert result.node_ids == ("a",) and result.delta == pytest.approx(.75)


def test_backend_frozen_plan_incremental_idempotence_and_restart(graph_types,tmp_path):
    t=graph_types; graph=t.NetworkXGraphDB()
    for key in ("a","b"): graph.add_node(fact(t,key))
    backend=RealMagmaBackend.__new__(RealMagmaBackend)
    backend.trg=SimpleNamespace(graph_db=graph,encoder=SimpleNamespace(encode=lambda text:[1.,0.]))
    backend._rebuild_first_hit_view()
    view=backend.first_hit_view()
    plan=[{"source_evidence_id":"e-a","target_evidence_id":"e-b",
           "weight":.4,"algorithm":"first-hit-v1"}]
    backend.apply_first_hit_links(plan)
    assert backend.first_hit_view() is view and view.outgoing_weight("a") == .4
    backend.apply_first_hit_links(plan)
    assert len(graph.links) == 2
    assert backend.endpoint_similarity("fact a","b") == pytest.approx(1)
    first=discover_first_hit(view,{"a":1},FirstHitPolicy())
    path=tmp_path/"graph.json"; graph.save(str(path))
    loaded=t.NetworkXGraphDB(); loaded.load(str(path)); backend.trg.graph_db=loaded
    backend._rebuild_first_hit_view(); backend.apply_first_hit_links(plan)
    second=discover_first_hit(backend.first_hit_view(),{"a":1},FirstHitPolicy())
    assert first.h == second.h and len(loaded.links) == 2
    with pytest.raises(ValueError,match="plan_conflict"):
        backend.apply_first_hit_links([{**plan[0],"weight":.5}])


def test_mentions_only_write_keeps_view_current_without_rebuild(graph_types):
    t=graph_types; graph=t.NetworkXGraphDB()
    backend=RealMagmaBackend.__new__(RealMagmaBackend)
    backend.trg=SimpleNamespace(graph_db=graph)
    backend._ensure_indexes=lambda: None
    backend._prepare_entity_membership_write=lambda: None
    backend._refresh_entity_membership=lambda *args: None
    backend._index_node=lambda node: None
    backend._rebuild_first_hit_view(); view=backend.first_hit_view()
    backend.upsert_entity_mentions([{"mention_id":"m-one","surface":"Ada","entity_ref":"E_ADA"}])
    assert backend.first_hit_view() is view and view.rebuilds == 1
    result=discover_first_hit(view,{"entity:e_ada":1},FirstHitPolicy())
    assert result.node_ids == ("entity:e_ada",) and not result.fact_ids


def test_stale_view_still_allows_valid_seed_source_projection(graph_types):
    t=graph_types; graph=t.NetworkXGraphDB(); graph.add_node(fact(t,"a"))
    backend=RealMagmaBackend.__new__(RealMagmaBackend)
    backend.trg=SimpleNamespace(graph_db=graph); backend._rebuild_first_hit_view()
    graph.add_node(fact(t,"b"))
    with pytest.raises(FirstHitUnavailable): backend.first_hit_view()
    assert backend.first_hit_candidate("a").metadata["evidence_id"] == "e-a"


def test_budget_exhaustion_describes_unread_mass_not_equality(graph_types):
    t=graph_types; graph=t.NetworkXGraphDB()
    for key in ("a","b"): graph.add_node(fact(t,key))
    link(t,graph,"a","b")
    view=DerivedFirstHitGraph(graph)
    assert not discover_first_hit(view,{"a":1},FirstHitPolicy(max_edges=1)).stats["budget_exhausted"]
    assert discover_first_hit(view,{"a":1},FirstHitPolicy(max_edges=0)).stats["budget_exhausted"]


def test_snapshot_owner_mutation_during_solve_is_rejected(graph_types,monkeypatch):
    t=graph_types; graph=t.NetworkXGraphDB(); graph.add_node(fact(t,"a"))
    view=DerivedFirstHitGraph(graph); original=np.linalg.solve
    def interrupted(*args):
        result=original(*args)
        view.add_node(graph.get_node("a"))
        return result
    monkeypatch.setattr(np.linalg,"solve",interrupted)
    with pytest.raises(FirstHitUnavailable,match="snapshot_unavailable"):
        discover_first_hit(view,{"a":1},FirstHitPolicy())


@pytest.mark.skipif(Path(sys.executable).resolve() != (
    Path(__file__).resolve().parents[1]/".venv/Scripts/python.exe").resolve(),
    reason="real MAGMA runs in the prepared isolated Memory environment")
def test_real_backend_new_semantic_writer_and_default_remain_separate(tmp_path, monkeypatch):
    import dotenv
    import socket
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)
    monkeypatch.setattr(dotenv, "find_dotenv", lambda *a, **kw: "")
    def no_network(*args, **kwargs):
        raise AssertionError("first_hit_test_network_forbidden")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    backend=RealMagmaBackend(tmp_path/"magma")
    from memory.graph_db import LinkType
    from types import SimpleNamespace
    # The real encoder and native FAISS are used; only source input is synthetic.
    types=SimpleNamespace(EventNode=backend._event_node_type)
    a=fact(types,"a",subject_entity_ref="E_A",subject_entity_surface="Ada")
    b=fact(types,"b",subject_entity_ref="E_A",subject_entity_surface="Ada")
    first=backend.add_event(a.content_narrative,a.timestamp,a.attributes,automatic_semantic=False)
    second=backend.add_event(b.content_narrative,b.timestamp,b.attributes,automatic_semantic=False)
    backend.create_relationships([first,second],automatic_entity_links=False)
    graph=backend.trg.graph_db
    assert not any(edge.link_type == LinkType.SEMANTIC for edge in graph.links.values())
    assert sum(edge.link_type == LinkType.TEMPORAL for edge in graph.links.values()) == 2
    assert sum(edge.link_type == LinkType.ENTITY for edge in graph.links.values()) == 2
    view=backend.first_hit_view()
    before=discover_first_hit(view,{first:1},FirstHitPolicy())
    backend.apply_first_hit_links([{"source_evidence_id":"e-a","target_evidence_id":"e-b",
                                   "weight":.5,"algorithm":"first-hit-v1"}])
    assert backend.first_hit_view() is view
    assert backend._entity_membership_for_recall() is not None
    assert sum(edge.link_type == LinkType.SEMANTIC for edge in graph.links.values()) == 2
    # Real graph-before-vector interruption retains its original temporal
    # predecessor even if newer ingestion arrives before this retry.
    from datetime import timedelta
    failed=fact(types,"failed"); failed.timestamp += timedelta(hours=2)
    original_add_vector=backend.trg.vector_db.add_vector
    def fail_vector(*args, **kwargs):
        raise OSError("synthetic_vector_write_failure")
    monkeypatch.setattr(backend.trg.vector_db,"add_vector",fail_vector)
    with pytest.raises(OSError,match="synthetic_vector_write_failure"):
        backend.add_event(failed.content_narrative,failed.timestamp,failed.attributes,
                          automatic_semantic=False)
    failed_id=backend.find_memory_id("e-failed")
    assert failed_id is not None
    assert backend.trg._lumina_automatic_semantic is True
    monkeypatch.setattr(backend.trg.vector_db,"add_vector",original_add_vector)
    later=fact(types,"later"); later.timestamp += timedelta(hours=1)
    later_id=backend.add_event(later.content_narrative,later.timestamp,later.attributes,
                               automatic_semantic=False)
    backend.ensure_event_persisted(failed_id)
    backend.create_relationships([failed_id],automatic_entity_links=False)
    repaired=[edge for edge in graph.links.values() if edge.link_type == LinkType.TEMPORAL
              and failed_id in (edge.source_node_id,edge.target_node_id)]
    assert len(repaired) == 2
    assert all(second in (edge.source_node_id,edge.target_node_id) for edge in repaired)
    assert all(later_id not in (edge.source_node_id,edge.target_node_id) for edge in repaired)
    assert backend._entity_membership_for_recall() is not None
    c=fact(types,"c")
    third=backend.add_event(c.content_narrative,c.timestamp,c.attributes)
    assert any(edge.link_type == LinkType.SEMANTIC and third in
               (edge.source_node_id,edge.target_node_id) for edge in graph.links.values())
    backend.persist()
    restarted=RealMagmaBackend(tmp_path/"magma")
    result=discover_first_hit(restarted.first_hit_view(),{first:1},FirstHitPolicy())
    assert result.h[second] > 0 and before.h[second] > 0
    assert restarted.first_hit_candidate(first).metadata["evidence_id"] == "e-a"


def test_temporal_repair_uses_original_insertion_history(graph_types):
    from datetime import timedelta
    t=graph_types; graph=t.NetworkXGraphDB()
    first=fact(t,"first"); failed=fact(t,"failed"); later=fact(t,"later")
    failed.timestamp += timedelta(hours=2)
    later.timestamp += timedelta(hours=1)  # Closer predecessor arrived only after interruption.
    for node in (first,failed,later): graph.add_node(node)
    backend=RealMagmaBackend.__new__(RealMagmaBackend)
    backend.trg=SimpleNamespace(graph_db=graph); backend._node_type=t.NodeType
    backend._rebuild_first_hit_view()
    backend._repair_first_hit_temporal_links([failed.node_id])
    backend._repair_first_hit_temporal_links([failed.node_id])
    assert len(graph.links) == 2
    assert all("later" not in (edge.source_node_id,edge.target_node_id) for edge in graph.links.values())
    assert backend.first_hit_view().outgoing_weight("failed") == 1


def test_missing_optional_adjacency_disables_first_hit_without_assuming_empty_graph():
    backend=RealMagmaBackend.__new__(RealMagmaBackend)
    backend.trg=SimpleNamespace(graph_db=SimpleNamespace(nodes={}))
    backend._rebuild_first_hit_view()
    assert backend._first_hit_graph is None
    with pytest.raises(FirstHitUnavailable,match="snapshot_unavailable"):
        backend.first_hit_view()
