from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from Conversation_Memory.adapter import _source_backend as source
from Conversation_Memory.adapter._anchor_fusion import (
    LexicalEventIndex,
    _lexical_score,
    _query_features,
)
from Conversation_Memory.adapter.models import ColdDraftSegment, ColdDraftTurn, RecallPolicy
from Conversation_Memory.adapter.source_memory import _source_rows


@dataclass
class Node:
    node_id: str
    node_type: str
    timestamp: datetime
    content_narrative: str
    attributes: dict


def source_node(text, node_id="source-a"):
    at = datetime(2026, 9, 1, tzinfo=UTC)
    turn = ColdDraftTurn(node_id + "-turn", "user", text, at, "UTC", "client")
    segment = ColdDraftSegment(node_id + "-segment", "session", "pending_digest",
                               (turn,), at, "UTC", "2")
    row = _source_rows(segment, lambda _text: True)[0]
    metadata = dict(row["metadata"], evidence_id=node_id)
    return Node(node_id, "source", at, text, metadata)


def rank(index, nodes, query, *, max_nodes=20, **kwargs):
    reads = []

    class Graph:
        def get_node(self, node_id):
            reads.append(node_id)
            return nodes[node_id]

    hits = index.rank(graph_db=Graph(), query=query, max_nodes=max_nodes,
                      event_node_type=Node, node_type=SimpleNamespace(EVENT="source"),
                      **kwargs)
    return hits, reads


@pytest.mark.parametrize("query,text", [
    ("林岚把样稿交给谁？", "林岚把样稿交给孟舟，自己保留底稿。"),
    ("Who has the silver cable?", "Mira has the silver cable; Tomas kept the case."),
    ("calibrated?", "The calibration kit is ready."),
    ("红色 adapter 放哪？", "红色adapter在工具箱里。"),
])
def test_source_candidates_score_the_features_that_selected_the_posting(query, text):
    node = source_node(text)
    assert set(_query_features(query)) & set(_query_features(text))
    obj = SimpleNamespace(
        trg=SimpleNamespace(graph_db=SimpleNamespace(nodes={node.node_id: node}),
                            vector_db=SimpleNamespace(index=SimpleNamespace(ntotal=0),
                                                      id_to_index={}, index_to_id={})),
        _node_type=SimpleNamespace(EVENT="event", NARRATIVE="source"),
        _event_node_type=Node,
    )
    source.rebuild(obj)
    hits = source.candidates(obj, query, RecallPolicy(top_k=10, max_nodes=20))
    assert [hit.text for hit in hits] == [text]
    assert obj.last_source_stats["lexical_node_reads"] == 1
    assert obj.last_source_stats["lexical_candidates"] == 1
    assert obj.last_source_stats["dense_node_reads"] == 0
    assert obj.last_source_stats["embedding_calls"] == 0


def test_default_fact_rank_preserves_legacy_filter_and_entity_surface_fallback():
    query, text = "林岚把样稿交给谁？", "林岚把样稿交给孟舟，自己保留底稿。"
    node = source_node(text)
    index = LexicalEventIndex()
    index.add(node)
    assert _lexical_score(query, text) is None
    assert rank(index, {node.node_id: node}, query) == ([], [node.node_id])
    hits, reads = rank(index, {node.node_id: node}, query, entity_surfaces=("林岚",))
    assert hits == [node] and reads == [node.node_id]
    assert _lexical_score("the xy zq", "xy zq") == 20


def test_source_overlap_is_distinct_and_ties_are_stable():
    a = source_node("calibration", "a")
    z = source_node("calibration " * 20, "z")
    index = LexicalEventIndex()
    for node in (z, a):
        index.add(node)
    hits, _ = rank(index, {node.node_id: node for node in (z, a)}, "calibration",
                   score_posting_overlap=True)
    assert hits == [a, z]


def test_source_overlap_prefers_more_shared_features_and_ignores_unmatched_nodes():
    sparse = source_node("样稿在抽屉。", "a")
    fuller = source_node("林岚把样稿交给孟舟，自己保留底稿。", "z")
    unrelated = source_node("violet luggage", "unrelated")
    index = LexicalEventIndex()
    nodes = {node.node_id: node for node in (sparse, fuller, unrelated)}
    for node in nodes.values():
        index.add(node)
    hits, reads = rank(index, nodes, "林岚把样稿交给谁？", score_posting_overlap=True)
    assert hits == [fuller, sparse]
    assert set(reads) == {"a", "z"}


def test_source_overlap_keeps_unique_projection_bound_and_updated_postings():
    index = LexicalEventIndex()
    nodes = {str(i): source_node("样稿存档", str(i)) for i in range(12)}
    for node in nodes.values():
        index.add(node)
    hits, reads = rank(index, nodes, "样稿在哪存档？", max_nodes=2,
                       score_posting_overlap=True)
    assert len(hits) == len(reads) == len(set(reads)) == 2
    nodes["0"] = source_node("violet luggage", "0")
    index.add(nodes["0"])
    hits, reads = rank(index, nodes, "样稿在哪存档？", max_nodes=2,
                       score_posting_overlap=True)
    assert "0" not in reads and len(hits) == len(reads) == 2


def test_source_overlap_retains_provenance_validation():
    node = source_node("样稿存档")
    node.attributes["provenance"]["source_role"] = "system"
    index = LexicalEventIndex()
    index.add(node)
    hits, reads = rank(index, {node.node_id: node}, "样稿在哪存档？",
                       score_posting_overlap=True)
    assert hits == [] and reads == [node.node_id]
