"""Graph-backed regressions; no encoder, model call, or real user data."""
from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from adapter._anchor_fusion import _rank_lexical_events
from adapter._recall_execution import _bounded_projection
from adapter.backend import RealMagmaBackend
from adapter.models import MemoryEvidence, RecallPolicy, SourceProvenance
from recall.rendering import bound_evidence, bound_evidence_groups


@pytest.fixture
def graph_types(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "upstream/MAGMA/memory/graph_db.py"
    spec = importlib.util.spec_from_file_location("_isolated_entity_graph", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    package = ModuleType("memory")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "memory", package)
    monkeypatch.setitem(sys.modules, "memory.graph_db", module)
    return module


def _backend(types):
    backend = RealMagmaBackend.__new__(RealMagmaBackend)
    backend._node_type = types.NodeType
    backend._event_node_type = types.EventNode
    backend._constraints_type = types.TraversalConstraints
    backend.trg = SimpleNamespace(graph_db=types.NetworkXGraphDB())
    backend._rebuild_indexes()
    return backend


def _provenance(evidence_id):
    return dict(segment_id="test-segment", conversation_id="test-conversation",
                turn_id=evidence_id, source_role="user",
                source_timestamp="2026-09-13T00:00:00+00:00", source_timezone="UTC",
                timezone_source="client", ingestion_version="test-v2")


def _event(backend, types, evidence_id, text, subject=None, obj=None):
    node = types.EventNode(node_id=evidence_id, node_type=types.NodeType.EVENT,
                           timestamp=datetime(2026, 9, 13, tzinfo=UTC),
                           content_narrative=text,
                           attributes={"evidence_id": evidence_id, "provenance": _provenance(evidence_id),
                                       "subject_entity_ref": subject, "subject_entity_surface": subject,
                                       "object_entity_ref": obj, "object_entity_surface": obj})
    backend.trg.graph_db.add_node(node)
    backend._create_subject_entity_ref_links([evidence_id])
    backend._create_object_entity_ref_links([evidence_id])
    backend._rebuild_indexes()
    return node


def _project(backend, types, anchors, targets, *, budget=20, depth=1):
    return _bounded_projection(graph_db=backend.trg.graph_db, anchors=anchors,
                               constraints=types.TraversalConstraints(max_depth=depth, max_nodes=budget),
                               target_refs=targets, event_node_type=types.EventNode, node_type=types.NodeType)


def test_name_lookup_precedes_limit_and_preserves_multiple_identities(graph_types):
    backend = _backend(graph_types)
    records = [{"mention_id": f"m{i}", "surface": f"Person{i}", "entity_ref": f"E_{i:03}",
                "provenance": _provenance(str(i))} for i in range(25)]
    records.extend([{"mention_id": "x1", "surface": "Alex", "entity_ref": "E_998"},
                    {"mention_id": "x2", "surface": "Alex", "entity_ref": "E_999"}])
    backend.upsert_entity_mentions(records)
    assert all(item.canonical_surface != "Alex" for item in backend.list_entity_candidates(limit=20))
    assert {item.entity_ref for item in backend.find_entity_candidates("Alex", limit=20)} == {"E_998", "E_999"}
    assert backend.resolve_target_entity_ref("Alex是谁？") is None
    assert set(backend.resolve_target_entity_refs("Alex和Person24分别是谁？")) == {"E_998", "E_999", "E_024"}


def test_mentions_survive_graph_restart_without_becoming_events(graph_types, tmp_path):
    backend = _backend(graph_types)
    records = [{"mention_id": "named", "surface": "材料B", "entity_ref": "E_B",
                "source_role": "user", "provenance": _provenance("named")},
               {"mention_id": "alias", "surface": "蓝色样品", "entity_ref": "E_B"},
               {"mention_id": "pronoun", "surface": "他", "entity_ref": None,
                "candidate_entity_refs": ["E_A", "E_C"], "identity": "unresolved"},
               {"mention_id": "other-pronoun", "surface": "同学", "entity_ref": None,
                "candidate_entity_refs": ["E_D"], "identity": "unresolved"}]
    backend.upsert_entity_mentions(records)
    backend.upsert_entity_mentions(records)
    path = tmp_path / "graph.json"
    backend.trg.graph_db.save(str(path))
    reloaded = _backend(graph_types)
    reloaded.trg.graph_db.load(str(path))
    reloaded._rebuild_indexes()
    assert reloaded.resolve_target_entity_refs("蓝色样品是什么？") == ("E_B",)
    assert reloaded.resolve_target_entity_refs("他是谁？") == ()
    assert reloaded.list_entity_mentions("他", limit=1)[0]["candidate_entity_refs"] == ["E_A", "E_C"]
    assert reloaded.list_entity_mentions("同学", limit=1)[0]["candidate_entity_refs"] == ["E_D"]
    assert reloaded.list_entity_mentions("材料B", limit=3) == (records[0],)
    assert len(reloaded.trg.graph_db.nodes) == 2
    assert all(node.node_type == graph_types.NodeType.ENTITY for node in reloaded.trg.graph_db.nodes.values())


def test_mention_retry_rejects_changed_source(graph_types):
    backend = _backend(graph_types)
    backend.upsert_entity_mentions([dict(mention_id="m", entity_ref="E_A", surface="A", source_start=0)])
    with pytest.raises(ValueError, match="provenance_conflict"):
        backend.upsert_entity_mentions([dict(mention_id="m", entity_ref="E_A", surface="A", source_start=5)])


@pytest.mark.parametrize("surface", ["我", "我们", "他", "她们", "这个", "用户", "CURRENT_USER",
                                      "I", "we", "the user", "he", "hers", "they", "yourself"])
def test_local_pronoun_bindings_never_become_global_names(graph_types, surface):
    backend = _backend(graph_types)
    records = [dict(mention_id="local", entity_ref="E_A", surface=surface),
               dict(mention_id="name", entity_ref="E_A", surface="林岚"),
               dict(mention_id="alias-pronoun", entity_ref="E_B", surface="小陈"),
               dict(mention_id="alias-local", entity_ref="E_B", surface=surface)]
    backend.upsert_entity_mentions(records)
    assert backend.find_entity_candidates(surface, limit=20) == ()
    assert backend.resolve_target_entity_refs(surface) == ()
    assert len(backend.list_entity_mentions(surface, limit=20)) == 2
    assert backend.resolve_target_entity_refs("林岚是谁？") == ("E_A",)
    backend._rebuild_indexes()
    assert backend.resolve_target_entity_refs(surface) == ()


def test_explicit_current_user_name_remains_searchable(graph_types):
    backend = _backend(graph_types)
    backend.upsert_entity_mentions([dict(mention_id="self", entity_ref="E_001", surface="宁竹", identity="current_user"),
                                     dict(mention_id="local", entity_ref="E_001", surface="我", identity="current_user")])
    assert backend.resolve_target_entity_refs("宁竹是谁？") == ("E_001",)
    assert backend.resolve_target_entity_refs("我是谁？") == ()


@pytest.mark.parametrize("query,text", [("needle42", "needle42 rare fact"),
                                         ("项目A负责人是谁？", "项目A由小林负责。"),
                                         ("electrochemistry", "electrochemistry-based material")])
def test_index_finds_late_fact_without_scanning_history(graph_types, query, text):
    backend = _backend(graph_types)
    for i in range(30):
        _event(backend, graph_types, f"old{i}", f"irrelevant older record {i}")
    target = _event(backend, graph_types, "target", text)
    if "项目A" in query:
        backend.upsert_entity_mentions([dict(mention_id="project", surface="项目A", entity_ref="E_A")])
    baseline = _rank_lexical_events(graph_nodes=backend.trg.graph_db.nodes.items(), query=query,
                                     max_nodes=5, event_node_type=graph_types.EventNode, node_type=graph_types.NodeType)
    assert target not in baseline
    reads = []
    original = backend.trg.graph_db.get_node
    def counted(node_id):
        reads.append(node_id)
        return original(node_id)
    backend.trg.graph_db.get_node = counted
    selected = backend._lexical_index.rank(graph_db=backend.trg.graph_db, query=query,
                                           max_nodes=5, event_node_type=graph_types.EventNode, node_type=graph_types.NodeType,
                                           entity_surfaces=tuple(backend._matching_surfaces(query)))
    assert target in selected
    assert len(reads) <= 5


def test_unsegmented_question_overlap_is_not_a_lexical_signal(graph_types):
    backend = _backend(graph_types)
    _event(backend, graph_types, "question", "王芳问我是谁。")
    assert backend._lexical_index.rank(graph_db=backend.trg.graph_db, query="我是谁？",
                                       max_nodes=5, event_node_type=graph_types.EventNode,
                                       node_type=graph_types.NodeType) == []


def test_backend_returns_two_fact_candidate_metadata_at_default_depth(graph_types):
    backend = _backend(graph_types)
    first = _event(backend, graph_types, "F1", "小林负责项目A。", "E_LIN", "E_A")
    _event(backend, graph_types, "F2", "小林研究材料B。", "E_LIN", "E_B")
    def query(_query, *, max_results, constraints):
        assert constraints.max_depth == 0
        return SimpleNamespace(anchor_nodes=[first], metadata={"search_scores": [1.0]},
                               traversal_paths=[], narrative_context="")
    backend.trg.query = query
    backend.trg.vector_db = SimpleNamespace(index=None, id_to_index={}, index_to_id={})
    candidates = backend.recall("负责项目A的人研究什么？", RecallPolicy(top_k=1, max_graph_depth=1, max_nodes=20),
                                 target_entity_refs=("E_A",))
    assert {item.metadata["evidence_id"] for item in candidates} == {"F1", "F2"}
    terminal = next(item for item in candidates if item.metadata["evidence_id"] == "F2")
    assert terminal.metadata["association_bridge_evidence_ids"] == ["F1"]
    assert backend.last_recall_stats["adjacency_links_read"] <= 20


def test_two_fact_projection_uses_explicit_roles_and_no_upstream_paths(graph_types):
    backend = _backend(graph_types)
    first = _event(backend, graph_types, "F1", "小林负责项目A。", "E_LIN", "E_A")
    second = _event(backend, graph_types, "F2", "小林研究材料B。", "E_LIN", "E_B")
    backend.trg.graph_db.get_neighbors = lambda *a, **k: pytest.fail("unbounded neighbors called")
    backend.trg.graph_db.traverse = lambda *a, **k: pytest.fail("upstream traversal called")
    result = _project(backend, graph_types, [first], ("E_A",))
    assert result["association_metadata"][second.node_id] == {
        "association_bridge_evidence_ids": ["F1"], "association_chain_evidence_ids": ["F1", "F2"]}
    assert ["F1", "F2"] in result["paths"]
    assert result["stats"]["adjacency_links_read"] <= 20
    assert _project(backend, graph_types, [first], ("E_A",), depth=0)["association_metadata"] == {}
    assert len(backend.trg.graph_db.nodes) == 5


def test_generic_cooccurrence_cannot_become_relationship_chain(graph_types):
    backend = _backend(graph_types)
    first = _event(backend, graph_types, "F1", "项目A。小林今天出差。", "E_LIN")
    first.attributes.update(mention_entity_refs=["E_A"], mention_entity_surfaces=["项目A"])
    backend._create_mention_entity_ref_links(["F1"])
    _event(backend, graph_types, "F2", "小林研究材料B。", "E_LIN", "E_B")
    assert _project(backend, graph_types, [first], ("E_A",))["association_metadata"] == {}


def test_high_degree_projection_counts_actual_reads_and_exceeds_old_ten_paths(graph_types):
    backend = _backend(graph_types)
    first = _event(backend, graph_types, "F1", "小林负责项目A。", "E_LIN", "E_A")
    for i in range(50):
        _event(backend, graph_types, f"F{i+2}", f"小林拥有属性{i}。", "E_LIN")
    class CountingLinks(dict):
        reads = 0
        def get(self, key, default=None):
            self.reads += 1
            return super().get(key, default)
    links = CountingLinks(backend.trg.graph_db.links)
    backend.trg.graph_db.links = links
    result = _project(backend, graph_types, [first], ("E_A",), budget=18)
    assert links.reads == result["stats"]["adjacency_links_read"] == 18
    assert 10 < len(result["association_metadata"]) <= 17
    assert result["stats"]["budget_exhausted"]


def test_graph_only_partial_event_vector_write_is_repaired_once(graph_types):
    backend = _backend(graph_types)
    node = _event(backend, graph_types, "F1", "小林负责项目A。", "E_LIN", "E_A")
    node.embedding_vector = [0.1, 0.2]
    writes = []
    vector_db = SimpleNamespace(id_to_index={})
    def add_vector(**kwargs):
        writes.append(kwargs)
        vector_db.id_to_index[kwargs["vector_id"]] = 0
    vector_db.add_vector = add_vector
    backend.trg.vector_db = vector_db
    backend.ensure_event_persisted("F1")
    backend.ensure_event_persisted("F1")
    assert len(writes) == 1
    assert writes[0]["vector"].tolist() == pytest.approx([0.1, 0.2])


def test_adapter_extraction_preserves_long_fact_qualifier(graph_types, monkeypatch, tmp_path):
    module = ModuleType("memory.trg_memory")
    class TruncatingUpstream:
        def __init__(self, **_kwargs):
            self.graph_db = graph_types.NetworkXGraphDB()
        def _extract_event(self, content, metadata=None):
            return SimpleNamespace(content_narrative=content[:500], keywords=["retained"], entities=[])
    module.TemporalResonanceGraphMemory = TruncatingUpstream
    monkeypatch.setitem(sys.modules, "memory.trg_memory", module)
    backend = RealMagmaBackend(tmp_path / "long-fact")
    fact = "材料规格" + "精确说明" * 150 + "；但不适用于高温。"
    extracted = backend.trg._extract_event(fact, {})
    assert extracted.content_narrative == fact
    assert extracted.content_narrative.endswith("不适用于高温。")
    assert extracted.keywords == ["retained"]


def _evidence(evidence_id, text):
    return MemoryEvidence(evidence_id, text, "2026-09-13T00:00:00+00:00",
                          SourceProvenance(**_provenance(evidence_id)))


def test_fact_and_chain_rendering_are_atomic():
    bridge, endpoint = _evidence("f1", "项目A由小林负责。"), _evidence("f2", "小林不研究材料B。")
    small = _evidence("s", "短事实。")
    assert bound_evidence([endpoint], count=1, max_chars=10) == ((), "", True)
    selected, text, truncated = bound_evidence_groups([[bridge, endpoint], [small]], count=1, max_chars=100)
    assert selected == (small,)
    assert truncated and endpoint.text not in text and bridge.text not in text
    selected, text, truncated = bound_evidence_groups([[bridge, endpoint], [bridge]], count=2, max_chars=100)
    assert selected == (bridge, endpoint)
    assert not truncated
    assert text.count(bridge.text) == 1
