from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import faiss
import numpy as np
import pytest

from Conversation_Memory.adapter import _source_backend as source
from Conversation_Memory.adapter.backend import RealMagmaBackend
from Conversation_Memory.adapter.models import ColdDraftSegment, ColdDraftTurn, RecallPolicy
from Conversation_Memory.adapter.source_memory import _source_rows


@dataclass
class Node:
    node_id: str
    node_type: str
    timestamp: datetime
    content_narrative: str
    attributes: dict
    embedding_vector: list | None = None


class Graph:
    def __init__(self):
        self.nodes = {}
        self.read_only = False

    def add_node(self, node):
        assert not self.read_only
        self.nodes[node.node_id] = node

    def get_node(self, node_id):
        return self.nodes.get(node_id)


class Vectors:
    def __init__(self):
        self.index = faiss.IndexFlatL2(3)
        self.id_to_index = {}
        self.index_to_id = {}
        self.fail_once = False
        self.read_only = False

    def add_vector(self, vector_id, vector, metadata):
        assert not self.read_only
        if self.fail_once:
            self.fail_once = False
            raise OSError("synthetic graph-before-vector interruption")
        position = self.index.ntotal
        self.index.add(vector.reshape(1, -1).astype(np.float32))
        self.id_to_index[vector_id] = position
        self.index_to_id[position] = vector_id
        return True


class Tokenizer:
    def __call__(self, text, **kwargs):
        assert kwargs["truncation"] is False
        return {"input_ids": [0] * (len(text) + 2)}


class Encoder:
    def __init__(self):
        self.calls = []
        self.model = SimpleNamespace(max_seq_length=256, tokenizer=Tokenizer())

    def encode(self, text):
        self.calls.append(text)
        return np.asarray([len(text), sum(map(ord, text)) % 101, text.count(" ")], dtype=np.float32)


def backend():
    obj = SimpleNamespace(
        trg=SimpleNamespace(graph_db=Graph(), vector_db=Vectors(), encoder=Encoder(),
                            keyword_enricher=SimpleNamespace(enrich_query=lambda text: text)),
        _node_type=SimpleNamespace(EVENT="event", NARRATIVE="narrative"),
        _event_node_type=Node, _source_bge_tokenizer=Tokenizer(),
    )
    source.rebuild(obj)
    return obj


def segment(sid="seg-a", conversation="session", texts=None, start=0):
    now = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=start)
    if texts is None:
        texts = [("assistant", "Use both items together?"), ("user", "Yes, both together for this run.")]
    turns = tuple(ColdDraftTurn(f"{sid}-t{i}", role, text, now + timedelta(minutes=i), "UTC", "client")
                  for i, (role, text) in enumerate(texts))
    return ColdDraftSegment(sid, conversation, "pending_digest", turns, now, "UTC", "2")


def add_segment(obj, seg, window=200):
    rows = _source_rows(seg, lambda text: len(text) <= window)
    for row in rows:
        node_id = source.add(obj, **row)
        source.ensure_persisted(obj, node_id)
    return rows


def ref(seg, index, start=0, end=None):
    turn = seg.turns[index]
    end = len(turn.content) if end is None else end
    return dict(segment_id=seg.segment_id, conversation_id=seg.conversation_id,
                turn_id=turn.turn_id, source_start=start, source_end=end,
                supporting_span=turn.content[start:end], source_role=turn.role,
                source_timestamp=turn.timestamp.isoformat(), source_timezone=turn.source_timezone,
                timezone_source=turn.timezone_source, ingestion_version="grounded-formation-v2")


def restart(obj):
    other = backend()
    other.trg.graph_db.nodes = deepcopy(obj.trg.graph_db.nodes)
    other.trg.vector_db.index = faiss.clone_index(obj.trg.vector_db.index)
    other.trg.vector_db.id_to_index = dict(obj.trg.vector_db.id_to_index)
    other.trg.vector_db.index_to_id = dict(obj.trg.vector_db.index_to_id)
    source.rebuild(other)
    return other


def test_exact_role_time_refs_ignore_fact_binding_and_preserve_raw_roles():
    obj, seg = backend(), segment()
    add_segment(obj, seg)
    references = [ref(seg, 0), ref(seg, 1)]
    references[0]["subject_entity_ref"] = "wrong-binding-does-not-authorize-role"
    before = deepcopy(obj.trg.graph_db.nodes)
    obj.trg.graph_db.read_only = obj.trg.vector_db.read_only = True
    hits = RealMagmaBackend.source_locate(obj, references, limit=10)
    assert [hit.text for hit in hits] == [turn.content for turn in seg.turns]
    assert [hit.metadata["provenance"]["source_role"] for hit in hits] == ["assistant", "user"]
    assert all(hit.metadata["provenance"]["ingestion_version"] == "source-window-v1" for hit in hits)
    assert obj.trg.graph_db.nodes == before
    assert obj.last_source_stats["last_locate"]["refs_matched"] == 2


@pytest.mark.parametrize("field,value", [
    ("source_role", "user"), ("source_timestamp", "2027-09-01T00:00:00+00:00"),
    ("source_timezone", "Asia/Shanghai"), ("timezone_source", "configured_default"),
    ("conversation_id", "other-session"), ("supporting_span", "x" * 24),
    ("source_start", 1), ("source_end", 300),
])
def test_mismatched_reference_is_not_a_navigation_hit(field, value):
    obj, seg = backend(), segment()
    add_segment(obj, seg)
    reference = ref(seg, 0)
    reference[field] = value
    assert source.locate(obj, [reference], limit=10) == []
    stats = obj.last_source_stats["last_locate"]
    assert stats["refs_invalid"] + stats["refs_mismatched"] == 1


def test_multiblock_ref_requires_complete_exact_span_and_counts_unique_reads():
    obj = backend()
    seg = segment(texts=[("user", "prefix alpha beta gamma delta omega suffix")])
    rows = add_segment(obj, seg, window=8)
    reference = ref(seg, 0, 6, 35)
    known = {}
    hits = source.locate(obj, [reference], limit=8, known_candidates=known)
    assert len(hits) > 1
    assert obj.last_source_stats["last_locate"]["refs_matched"] == 1
    before = len(known)
    assert source.locate(obj, [reference], limit=8, known_candidates=known) == hits
    assert obj.last_source_stats["last_locate"]["new_node_reads"] == 0
    assert len(known) == before
    assert source.locate(obj, [reference], limit=1) == []
    assert obj.last_source_stats["last_locate"]["truncated"]
    wrong = dict(reference, supporting_span="X" * (35 - 6))
    assert source.locate(obj, [wrong], limit=8) == []
    assert obj.last_source_stats["last_locate"]["refs_mismatched"] == 1


def test_reference_budget_independent_of_output_and_projection_budgets():
    obj, seg = backend(), segment()
    add_segment(obj, seg)
    references = [dict(ref(seg, 0), conversation_id="not-present") for _ in range(30)] + [ref(seg, 1)]
    hits = source.locate(obj, references, limit=1, max_refs=40, max_nodes=3)
    assert [hit.text for hit in hits] == [seg.turns[1].content]
    assert obj.last_source_stats["last_locate"]["refs_checked"] == 31
    assert source.locate(obj, references, limit=1, max_refs=20) == []
    assert obj.last_source_stats["last_locate"]["truncated"]
    for invalid in [0, -1, True, 1.5]:
        with pytest.raises(ValueError, match="source_navigation_bound_invalid"):
            source.locate(obj, references, limit=1, max_refs=invalid)


def test_parent_collects_shared_session_segments_in_original_block_order():
    obj = backend()
    later = segment("later", start=2)
    earlier = segment("earlier", start=0)
    separate = segment("other", conversation="different", start=1)
    add_segment(obj, later)
    add_segment(obj, separate)
    add_segment(obj, earlier)
    anchor = source.locate(obj, [ref(later, 1)], limit=10)[0]
    before = deepcopy(obj.trg.graph_db.nodes)
    obj.trg.graph_db.read_only = obj.trg.vector_db.read_only = True
    hits = RealMagmaBackend.source_parent(obj, anchor, limit=10)
    assert [hit.metadata["provenance"]["turn_id"] for hit in hits] == [
        turn.turn_id for seg in [earlier, later] for turn in seg.turns]
    assert obj.last_source_stats["last_parent"]["complete"]
    assert obj.last_source_stats["last_parent"]["indexed_segments"] == 2
    assert obj.trg.graph_db.nodes == before


def test_parent_budget_or_missing_block_returns_no_partial_parent():
    obj, seg = backend(), segment()
    rows = _source_rows(seg, lambda text: len(text) <= 200)
    source.add(obj, **rows[0])
    anchor = source.locate(obj, [ref(seg, 0)], limit=4)[0]
    assert source.parent(obj, anchor, limit=4) == []
    assert obj.last_source_stats["last_parent"]["reason"] == "source_parent_incomplete"
    source.add(obj, **rows[1])
    assert source.parent(obj, anchor, limit=1) == []
    assert obj.last_source_stats["last_parent"]["truncated"]
    assert not obj.last_source_stats["last_parent"]["complete"]
    known = {anchor.metadata["evidence_id"]: anchor}
    assert source.parent(obj, anchor, limit=4, known_candidates=known, max_nodes=1) == []
    assert len(known) == 1
    assert obj.last_source_stats["last_parent"]["reason"] == "source_parent_node_budget"


def test_graph_before_vector_repair_survives_restart_without_new_embedding():
    obj, seg = backend(), segment(texts=[("user", "One exact source.")])
    row = _source_rows(seg, lambda text: True)[0]
    obj.trg.vector_db.fail_once = True
    with pytest.raises(OSError, match="graph-before-vector"):
        source.add(obj, **row)
    assert len(obj.trg.graph_db.nodes) == 1 and obj.trg.vector_db.index.ntotal == 0
    other = restart(obj)
    source.ensure_persisted(other, row["metadata"]["evidence_id"])
    assert other.trg.vector_db.index.ntotal == 1
    assert other.trg.encoder.calls == []
    assert source.add(other, **row) == row["metadata"]["evidence_id"]
    source.ensure_persisted(other, row["metadata"]["evidence_id"])
    assert len(other.trg.graph_db.nodes) == other.trg.vector_db.index.ntotal == 1
    assert other.trg.encoder.calls == []


def test_bitmap_growth_incremental_rebuild_and_array_search_are_equivalent():
    obj = backend()
    for i in range(140):
        add_segment(obj, segment(f"s-{i}", conversation=f"c-{i}",
                                texts=[("user", f"unique source record {i} alpha {i * 17}")]))
    assert obj.source_stats["index_rebuilds"] == 1
    assert obj.source_stats["index_nodes_inspected"] == 0
    assert obj.source_stats["source_selector_builds"] == 3
    assert obj.source_stats["source_selector_bytes_copied"] == 24
    policy = RecallPolicy(top_k=10, max_nodes=20)
    query = "unique source record 83 alpha"
    before = source.candidates(obj, query, policy)
    vec = obj.trg.encoder.encode(query).reshape(1, -1)
    positions = np.asarray(sorted(obj._source_vector_positions), dtype=np.int64)
    a_dist, a_ids = obj.trg.vector_db.index.search(vec, 10, params=faiss.SearchParameters(sel=faiss.IDSelectorArray(positions)))
    b_dist, b_ids = obj.trg.vector_db.index.search(vec, 10, params=faiss.SearchParameters(sel=obj._source_vector_selector))
    np.testing.assert_array_equal(a_ids, b_ids)
    np.testing.assert_array_equal(a_dist, b_dist)
    rebuilt = restart(obj)
    after = source.candidates(rebuilt, query, policy)
    assert before == after
    assert set(obj._source_vector_positions) == set(range(140))


def test_source_owner_rejects_fact_mixing_and_stale_views():
    obj = backend()
    obj.trg.graph_db.nodes["fact"] = Node("fact", "event", datetime.now(UTC), "fact", {})
    source.rebuild(obj)
    with pytest.raises(ValueError, match="source_store_contains_facts"):
        source.locate(obj, [], limit=1)
    obj = backend()
    obj.trg.graph_db.nodes["external"] = Node("external", "other", datetime.now(UTC), "other", {})
    with pytest.raises(ValueError, match="source_index_stale"):
        source.locate(obj, [], limit=1)
