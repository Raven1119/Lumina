from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
import json

import pytest

from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import (
    BackendCandidate, ColdDraftSegment, ColdDraftTurn, MemoryContext,
    RecallPolicy, SourceMemoryContext,
)
from Conversation_Memory.adapter.source_memory import SOURCE_VERSION, render_source
from Conversation_Memory.ingestion.state_store import IngestionStateStore


def segment(rows, sid="source-a"):
    now = datetime(2026, 8, 12, 9, tzinfo=UTC)
    turns = tuple(ColdDraftTurn(
        f"{sid}-t{i}", role, text, now + timedelta(minutes=2 * i),
        "UTC", "client",
    ) for i, (role, text) in enumerate(rows))
    return ColdDraftSegment(sid, f"conversation-{sid}", "pending_digest",
                            turns, now, "UTC", "2")


class SourceBackend:
    """Fake the source persistence/search contract, without MAGMA or a model."""

    def __init__(self, *, window=1000):
        self.window = window
        self.rows = {}
        self.vector_ids = set()
        self.anchor_ids = []
        self.events = {}
        self.fact_reads = 0
        self.source_reads = []
        self.neighbor_reads = []
        self.writes = []
        self.fail_vector_once = False
        self.fail_persist_once = False
        self.read_only = False

    def _write(self, operation):
        assert not self.read_only, f"read path attempted {operation}"
        self.writes.append(operation)

    def source_text_fits(self, text):
        return len(text) <= self.window

    def add_source(self, text, timestamp, metadata):
        self._write("add_source")
        eid = metadata["evidence_id"]
        candidate = BackendCandidate(text, timestamp.isoformat(), 1.0,
                                     deepcopy(metadata))
        if eid in self.rows:
            assert self.rows[eid] == candidate
        else:
            self.rows[eid] = candidate
        return eid

    def ensure_source_persisted(self, memory_id):
        self._write("ensure_source_persisted")
        if self.fail_vector_once:
            self.fail_vector_once = False
            raise OSError("synthetic vector interruption")
        self.vector_ids.add(memory_id)

    def persist(self):
        self._write("persist")
        if self.fail_persist_once:
            self.fail_persist_once = False
            raise OSError("synthetic persistence interruption")

    def source_candidates(self, query, policy):
        self.source_reads.append((query, policy))
        return tuple(self.rows[eid] for eid in self.anchor_ids[:policy.max_nodes])

    def source_neighbors(self, anchor, *, before, after, limit,
                         known_candidates=None, max_nodes=None):
        self.neighbor_reads.append((anchor.metadata["evidence_id"], before, after, limit))
        position = anchor.metadata["block_index"]
        candidates = [c for c in self.rows.values()
                      if c.metadata["segment_id"] == anchor.metadata["segment_id"]
                      and position - before <= c.metadata["block_index"] <= position + after]
        available = dict(known_candidates or {})
        result = []
        for candidate in sorted(candidates, key=lambda c: c.metadata["block_index"]):
            eid = candidate.metadata["evidence_id"]
            if eid in available:
                result.append(available[eid])
            elif max_nodes is None or len(available) < max_nodes:
                available[eid] = candidate
                result.append(candidate)
        return tuple(result[:limit])

    def resolve_target_entity_refs(self, query, *, limit):
        return ()

    def recall(self, query, policy, **kwargs):
        self.fact_reads += 1
        return ()

    def find_memory_id(self, evidence_id):
        return evidence_id if evidence_id in self.events else None

    def add_event(self, text, timestamp, metadata):
        self._write("add_event")
        self.events[metadata["evidence_id"]] = (text, timestamp, deepcopy(metadata))
        return metadata["evidence_id"]

    def create_relationships(self, memory_ids):
        self._write("create_relationships")


class Reranker:
    def __init__(self, max_pair_chars=10000):
        self.max_pair_chars = max_pair_chars
        self.scored = []

    def fits_pair(self, query, text):
        return len(query) + len(text) <= self.max_pair_chars

    def score(self, query, texts):
        assert all(self.fits_pair(query, text) for text in texts)
        self.scored.append((query, texts))
        return tuple(0.8 for _ in texts)


def adapter(tmp_path, backend=None, *, reranker=None, store=None, **kwargs):
    obj = MagmaMemoryAdapter(backend or SourceBackend(),
                             store or IngestionStateStore(tmp_path / "state.json"),
                             **kwargs)
    obj._bge_reranker = reranker or Reranker()
    obj._bge_reranker_load_attempted = True
    return obj


def policy(**changes):
    return replace(RecallPolicy(top_k=20, max_nodes=20,
                                max_evidence_items=20, max_chars=10000), **changes)


def choose_anchors(obj, *positions):
    ids = list(obj.backend.rows)
    obj.backend.anchor_ids = [ids[position] for position in positions]


def test_long_turn_ranges_cover_every_character_including_tail(tmp_path):
    tail = " The final setting remains OFF.\n"
    raw = "ab\u79cd\u5b50 " * 130 + tail
    seg = segment([("user", raw), ("assistant", "Is that confirmed?"),
                   ("user", "Only for the recorded test.")])
    before = asdict(seg)
    obj = adapter(tmp_path, SourceBackend(window=31))

    result = obj.ingest_sources(seg)

    assert result.status == "completed"
    assert asdict(seg) == before
    assert not obj.backend.events
    assert len(obj.backend.rows) > 20
    rows = list(obj.backend.rows.values())
    assert [c.metadata["block_index"] for c in rows] == list(range(len(rows)))
    for i, turn in enumerate(seg.turns):
        parts = [c for c in rows if c.metadata["turn_index"] == i]
        assert "".join(c.text for c in parts) == turn.content
        cursor = 0
        for c in parts:
            assert c.metadata["source_start"] == cursor
            cursor = c.metadata["source_end"]
            assert c.text == turn.content[c.metadata["source_start"]:cursor]
            assert c.metadata["turn_length"] == len(turn.content)
            assert c.metadata["block_count"] == len(rows)
            assert c.metadata["provenance"]["source_role"] == turn.role
            assert c.metadata["provenance"]["source_timestamp"] == turn.timestamp.isoformat()
            assert c.metadata["provenance"]["timezone_source"] == "client"
        assert cursor == len(turn.content)
    assert "".join(c.text for c in rows if c.metadata["turn_index"] == 0).endswith(tail)


def test_neighbors_keep_session_boundary_turn_order_and_individual_roles(tmp_path):
    seg = segment([("user", "The old estimate was 2400."),
                   ("assistant", "I guess it stayed unchanged."),
                   ("user", "It was later corrected to 3100."),
                   ("assistant", "Should the earlier record be removed?"),
                   ("user", "No, retain the original record.")])
    other = segment([("user", "Unrelated person in a different session.")], "source-b")
    obj = adapter(tmp_path)
    assert obj.ingest_sources(seg).status == "completed"
    assert obj.ingest_sources(other).status == "completed"
    choose_anchors(obj, 2)

    context = obj.recall_sources("What changed?", policy())

    assert isinstance(context, SourceMemoryContext)
    assert context.safe_error_code is None
    assert [e.text for e in context.evidence] == [t.content for t in seg.turns]
    assert [e.provenance.source_role for e in context.evidence] == [t.role for t in seg.turns]
    assert {e.provenance.segment_id for e in context.evidence} == {seg.segment_id}
    headers = [json.loads(line[len("[SOURCE "):-1])
               for line in context.rendered_text.splitlines() if line.startswith("[SOURCE ")]
    assert [h["turn"] for h in headers] == list(range(5))
    assert [h["role"] for h in headers] == [
        "USER" if t.role == "user" else "LUMINA" for t in seg.turns
    ]
    assert [h["spoken_at"] for h in headers] == [t.timestamp.isoformat() for t in seg.turns]
    assert all(h["session"] == seg.conversation_id for h in headers)
    assert "different session" not in context.rendered_text


def test_overlapping_groups_deduplicate_before_item_and_character_budget(tmp_path):
    obj = adapter(tmp_path)
    obj.ingest_sources(segment([("user", f"Record {i}.") for i in range(4)]))
    choose_anchors(obj, 1, 2)
    full = obj.recall_sources("records", policy(max_evidence_items=4))
    assert len(full.evidence) == 4
    assert not full.truncated
    assert len({e.evidence_id for e in full.evidence}) == 4

    exact = obj.recall_sources("records", policy(max_evidence_items=4,
                                                max_chars=len(full.rendered_text)))
    assert exact.evidence == full.evidence
    assert exact.rendered_text == full.rendered_text
    omitted = obj.recall_sources("records", policy(max_chars=len(full.rendered_text) - 1))
    assert omitted.truncated
    assert not omitted.evidence
    assert not omitted.rendered_text


def test_group_is_omitted_whole_when_item_budget_cannot_hold_context(tmp_path):
    obj = adapter(tmp_path)
    obj.ingest_sources(segment([("user", f"Record {i}.") for i in range(5)]))
    choose_anchors(obj, 2)
    context = obj.recall_sources("records", policy(max_evidence_items=4))
    assert context.truncated
    assert context.evidence == ()
    assert context.rendered_text == ""


def test_available_neighbor_union_respects_max_nodes(tmp_path):
    obj = adapter(tmp_path)
    obj.ingest_sources(segment([("user", f"Record {i}.") for i in range(7)]))
    choose_anchors(obj, 2, 4)
    context = obj.recall_sources("records", policy(max_nodes=3))
    assert context.safe_error_code is None
    assert context.truncated
    assert len(obj.last_source_read["candidates"]) <= 3
    assert len(context.evidence) <= 3
    assert len({e.evidence_id for e in context.evidence}) == len(context.evidence)


@pytest.mark.parametrize("failure", ["fail_vector_once", "fail_persist_once"])
def test_restart_repairs_interrupted_source_persistence_without_duplicate_ids(tmp_path, failure):
    backend = SourceBackend(window=25)
    setattr(backend, failure, True)
    obj = adapter(tmp_path, backend)
    seg = segment([("user", "A long exact account with an important final correction.")])
    first = obj.ingest_sources(seg)
    assert first.status == "failed" and first.retryable
    key = obj.state_store.key(seg.segment_id, SOURCE_VERSION)
    assert obj.state_store.get(key)["status"] == "in_progress"

    restored = SourceBackend(window=25)
    restored.rows = deepcopy(backend.rows)
    restored.vector_ids = backend.vector_ids.copy()
    restarted = adapter(tmp_path, restored)
    second = restarted.ingest_sources(seg)
    assert second.status == "completed"
    assert not second.already_ingested
    assert set(second.memory_ids) == set(restored.rows) == restored.vector_ids
    checkpoint_bytes = restarted.state_store.path.read_bytes()
    third = restarted.ingest_sources(seg)
    assert third.status == "completed" and third.already_ingested
    assert third.memory_ids == second.memory_ids
    assert restarted.state_store.path.read_bytes() == checkpoint_bytes
    assert seg.state == "pending_digest"


def test_completion_checkpoint_failure_recovers_durable_sources(tmp_path, monkeypatch):
    store = IngestionStateStore(tmp_path / "state.json")
    original_put = store.put
    failed = False

    def fail_first_completion(key, value):
        nonlocal failed
        if value["status"] == "completed" and not failed:
            failed = True
            raise OSError("synthetic checkpoint interruption")
        original_put(key, value)

    monkeypatch.setattr(store, "put", fail_first_completion)
    obj = adapter(tmp_path, store=store)
    seg = segment([("user", "The source remains exact.")])
    assert obj.ingest_sources(seg).status == "failed"
    persisted = deepcopy(obj.backend.rows)
    restarted = adapter(tmp_path, obj.backend)
    assert restarted.ingest_sources(seg).status == "completed"
    assert restarted.backend.rows == persisted


@pytest.mark.parametrize("field", ["content", "timestamp", "role"])
def test_changed_source_cannot_reuse_completed_checkpoint(tmp_path, field):
    obj = adapter(tmp_path)
    seg = segment([("user", "Keep the original wording.")])
    obj.ingest_sources(seg)
    state_before = obj.state_store.path.read_bytes()
    rows_before = deepcopy(obj.backend.rows)
    writes_before = len(obj.backend.writes)
    value = {"content": "Changed wording.", "timestamp": seg.created_at + timedelta(days=1),
             "role": "assistant"}[field]
    changed = replace(seg, turns=(replace(seg.turns[0], **{field: value}),))

    result = obj.ingest_sources(changed)

    assert result.status == "failed"
    assert result.safe_error_code == "source_checkpoint_mismatch"
    assert obj.state_store.path.read_bytes() == state_before
    assert obj.backend.rows == rows_before
    assert len(obj.backend.writes) == writes_before


def test_source_checkpoint_survives_failed_formation_without_completing_it(tmp_path):
    class FailedFormation:
        def generate(self, *args, **kwargs):
            raise RuntimeError("synthetic formation unavailable")

    obj = adapter(tmp_path, ingestion_version="grounded-formation-v2",
                  formation_model=FailedFormation())
    seg = segment([("user", "The source must survive an extraction failure.")])
    failed = obj.ingest(seg)
    assert failed.status == "failed"
    assert not obj.backend.rows
    fact_key = obj.state_store.key(seg.segment_id, "grounded-formation-v2")
    old_fact_state = deepcopy(obj.state_store.get(fact_key))

    source_result = obj.ingest_sources(seg)

    assert source_result.status == "completed"
    assert obj.state_store.get(fact_key) == old_fact_state
    assert obj.state_store.get(fact_key)["status"] != "completed"
    assert obj.state_store.get(obj.state_store.key(seg.segment_id, SOURCE_VERSION))["status"] == "completed"
    assert seg.state == "pending_digest"
    ordinary = obj.recall("source", policy())
    assert isinstance(ordinary, MemoryContext)
    assert ordinary.safe_error_code is None and ordinary.evidence == ()
    assert obj.backend.fact_reads == 1
    assert obj.backend.source_reads == []


def test_default_fact_ingestion_never_indexes_sources(tmp_path):
    obj = adapter(tmp_path)
    seg = segment([("user", "The original deterministic fact entry remains available.")])
    result = obj.ingest(seg)
    assert result.status == "completed"
    assert result.ingestion_version == "grounded-span-v2"
    assert obj.backend.events
    assert obj.backend.rows == {}
    assert all(key.endswith(":grounded-span-v2") for key in obj.state_store.read_all())


def test_source_recall_is_read_only_and_invalid_query_does_not_read(tmp_path, monkeypatch):
    obj = adapter(tmp_path)
    obj.ingest_sources(segment([("user", "Original statement."),
                                ("assistant", "Unconfirmed suggestion.")]))
    choose_anchors(obj, 0)
    state_before = obj.state_store.path.read_bytes()
    rows_before = deepcopy(obj.backend.rows)
    vectors_before = obj.backend.vector_ids.copy()
    obj.backend.read_only = True
    monkeypatch.setattr(obj.state_store, "put",
                        lambda *args: pytest.fail("recall attempted checkpoint write"))
    context = obj.recall_sources("original", policy())
    assert context.safe_error_code is None and len(context.evidence) == 2
    reads = len(obj.backend.source_reads)
    invalid = obj.recall_sources("  ", policy())
    assert invalid.safe_error_code == "invalid_query"
    assert len(obj.backend.source_reads) == reads
    assert obj.state_store.path.read_bytes() == state_before
    assert obj.backend.rows == rows_before
    assert obj.backend.vector_ids == vectors_before



def test_unsuccessful_read_never_retains_previous_question_diagnostics(tmp_path, monkeypatch):
    obj = adapter(tmp_path)
    obj.ingest_sources(segment([("user", "Earlier question source.")]))

    for next_read in ("invalid_query", "empty_candidates", "backend_failure"):
        choose_anchors(obj, 0)
        successful = obj.recall_sources("earlier question", policy())
        assert successful.safe_error_code is None and successful.evidence
        assert obj.last_source_read["selected_source_chars"] > 0
        assert obj.last_source_read["anchors"]

        with monkeypatch.context() as patch:
            if next_read == "invalid_query":
                current = obj.recall_sources("   ", policy())
                assert current.safe_error_code == "invalid_query"
            elif next_read == "empty_candidates":
                obj.backend.anchor_ids = []
                current = obj.recall_sources("unrelated question", policy())
                assert current.safe_error_code is None
            else:
                def unavailable(*args, **kwargs):
                    raise OSError("synthetic source search failure")
                patch.setattr(obj.backend, "source_candidates", unavailable)
                current = obj.recall_sources("next question", policy())
                assert current.safe_error_code == "source_recall_unavailable"
        assert current.evidence == ()
        assert current.rendered_text == ""
        assert obj.last_source_read == {}


def test_bge_window_fallback_is_explicit_and_never_silently_truncates_source(tmp_path):
    obj = adapter(tmp_path)
    obj.ingest_sources(segment([("user", "First source."),
                                ("assistant", "Question only."),
                                ("user", "Confirmed answer.")]))
    choose_anchors(obj, 1)
    full = obj.recall_sources("record", policy())
    anchor = next(e for e in full.evidence if e.turn_index == 1)
    obj._bge_reranker = Reranker(max_pair_chars=len("record") + len(render_source(anchor)))

    context = obj.recall_sources("record", policy())

    assert context.evidence == full.evidence
    assert obj._bge_reranker.scored == [("record", (render_source(anchor),))]
    assert obj.last_source_read["bge_context_omitted"] == 1
    assert obj.last_source_read["bge_silent_truncations"] == 0
    obj._bge_reranker = Reranker(max_pair_chars=1)
    failed = obj.recall_sources("record", policy())
    assert failed.safe_error_code == "source_recall_unavailable"
    assert failed.evidence == () and failed.rendered_text == ""


@pytest.mark.parametrize("mutation", ["wrong_kind", "inexact_range"])
def test_fact_or_malformed_range_is_not_exposed_as_source(tmp_path, mutation):
    obj = adapter(tmp_path)
    obj.ingest_sources(segment([("user", "Original statement.")]))
    choose_anchors(obj, 0)
    eid = obj.backend.anchor_ids[0]
    candidate = obj.backend.rows[eid]
    metadata = deepcopy(candidate.metadata)
    if mutation == "wrong_kind":
        metadata["memory_kind"] = "grounded-formation-v2"
    else:
        metadata["source_end"] -= 1
    obj.backend.rows[eid] = replace(candidate, metadata=metadata)
    context = obj.recall_sources("original", policy())
    assert context.safe_error_code == "source_recall_unavailable"
    assert not context.evidence


@pytest.mark.parametrize("rows", [
    [("user", "x")] * 33,
    [("user", "x" * 20001)],
])
def test_oversized_source_window_fails_before_checkpoint_or_backend_write(tmp_path, rows):
    obj = adapter(tmp_path)
    result = obj.ingest_sources(segment(rows))
    assert result.status == "failed"
    assert result.safe_error_code == "source_window_exceeded"
    assert not obj.state_store.path.exists()
    assert obj.backend.writes == []
