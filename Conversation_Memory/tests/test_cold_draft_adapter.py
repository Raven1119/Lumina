from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adapter.backend import RealMagmaBackend
from adapter.magma_adapter import MagmaMemoryAdapter
from adapter.models import BackendCandidate, ColdDraftTurn, RecallPolicy, SourceProvenance
from ingestion.fixture_loader import SegmentValidationError, load_fixture, parse_segment
from ingestion.state_store import IngestionStateStore
from ingestion.temporal import normalize_temporal_references

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cold_draft_segment_v2.json"
LEGACY_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cold_draft_segment_v1.json"


class FakeBackend:
    def __init__(self):
        self.events = {}
        self.order = []
        self.persist_calls = 0
        self.fail_add_once_at = None
        self.fail_persist_once_at = None
        self.fail_recall = False
        self.relationship_entities = set()

    def find_memory_id(self, evidence_id):
        for memory_id, event in self.events.items():
            if event["metadata"]["evidence_id"] == evidence_id:
                return memory_id
        return None

    def add_event(self, text, timestamp, metadata):
        if self.fail_add_once_at == len(self.order):
            self.fail_add_once_at = None
            raise RuntimeError("secret C:\\private\\key traceback")
        memory_id = f"memory-{len(self.order)}"
        self.events[memory_id] = {"text": text, "timestamp": timestamp, "metadata": metadata}
        self.order.append(memory_id)
        return memory_id

    def create_relationships(self, memory_ids):
        entity_to_ids = {}
        for memory_id in memory_ids:
            for entity in self.events[memory_id]["metadata"]["entities"]:
                entity_to_ids.setdefault(entity, []).append(memory_id)
        self.relationship_entities = {entity for entity, ids in entity_to_ids.items() if len(ids) > 1}

    def persist(self):
        self.persist_calls += 1
        if self.fail_persist_once_at == self.persist_calls:
            self.fail_persist_once_at = None
            raise OSError("C:\\private\\graph.json")

    def recall(self, query, policy):
        if self.fail_recall:
            raise RuntimeError("OPENAI_API_KEY=secret C:\\private traceback")
        result = []
        for rank, memory_id in enumerate(reversed(self.order)):
            event = self.events[memory_id]
            result.append(BackendCandidate(
                event["text"], event["timestamp"].isoformat(), 1.0 - rank / 10,
                event["metadata"],
            ))
        return result


def make_adapter(tmp_path, backend=None, version="v1"):
    backend = backend or FakeBackend()
    return MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "state.json"), ingestion_version=version), backend


def test_valid_fixture_ingests_and_preserves_provenance(tmp_path):
    segment = load_fixture(FIXTURE)
    adapter, backend = make_adapter(tmp_path)
    result = adapter.ingest(segment)
    assert result.status == "completed"
    assert len(result.memory_ids) == len(segment.turns)
    metadata = backend.events[result.memory_ids[0]]["metadata"]
    assert metadata["provenance"] == {
        "segment_id": segment.segment_id,
        "conversation_id": segment.conversation_id,
        "turn_id": "turn-001",
        "source_timestamp": "2026-07-14T02:00:00+00:00",
        "source_timezone": "Asia/Shanghai",
        "ingestion_version": "v1",
        "timezone_source": "client",
    }
    assert backend.events[result.memory_ids[0]]["timestamp"] == segment.turns[0].timestamp
    mention = metadata["temporal_mentions"][0]
    assert mention == {
        "original_expression": "yesterday",
        "reference_timestamp": "2026-07-14T02:00:00Z",
        "reference_timezone": "Asia/Shanghai",
        "normalized_start": "2026-07-12T16:00:00Z",
        "normalized_end": "2026-07-13T16:00:00Z",
        "normalization_method": "deterministic_relative_day",
        "normalization_confidence": 1.0,
        "language": "en",
    }
    assert metadata["dates_mentioned"] == [
        {"original": "yesterday", "parsed": "2026-07-12T16:00:00Z"}
    ]


def test_duplicate_key_does_not_duplicate_nodes(tmp_path):
    adapter, backend = make_adapter(tmp_path)
    first = adapter.ingest(load_fixture(FIXTURE))
    second = adapter.ingest(load_fixture(FIXTURE))
    assert first.memory_ids == second.memory_ids
    assert second.already_ingested is True
    assert len(backend.events) == 4


def test_different_ingestion_version_can_reimport(tmp_path):
    backend = FakeBackend()
    first, _ = make_adapter(tmp_path, backend, "v1")
    second, _ = make_adapter(tmp_path, backend, "v2")
    assert first.ingest(load_fixture(FIXTURE)).status == "completed"
    assert second.ingest(load_fixture(FIXTURE)).status == "completed"
    assert len(backend.events) == 8


def test_write_failure_is_not_completed_and_retry_converges(tmp_path):
    backend = FakeBackend()
    backend.fail_add_once_at = 1
    adapter, _ = make_adapter(tmp_path, backend)
    failed = adapter.ingest(load_fixture(FIXTURE))
    assert failed.status == "failed" and failed.retryable
    state = IngestionStateStore(tmp_path / "state.json").read_all()
    assert next(iter(state.values()))["status"] == "failed"
    retried = adapter.ingest(load_fixture(FIXTURE))
    assert retried.status == "completed"
    assert len(backend.events) == 4


def test_persist_failure_does_not_leak_or_duplicate_on_retry(tmp_path):
    backend = FakeBackend()
    backend.fail_persist_once_at = 1
    adapter, _ = make_adapter(tmp_path, backend)
    result = adapter.ingest(load_fixture(FIXTURE))
    assert result.safe_error_code == "memory_write_failed"
    assert "private" not in repr(result).lower()
    assert adapter.ingest(load_fixture(FIXTURE)).status == "completed"
    assert len(backend.events) == 4


def test_relative_time_uses_each_source_timestamp_and_timezone():
    segment = load_fixture(FIXTURE)
    refs = normalize_temporal_references(segment.turns[0])
    assert refs[0].original_expression == "yesterday"
    assert refs[0].reference_timestamp == "2026-07-14T02:00:00Z"
    assert refs[0].normalized_start == "2026-07-12T16:00:00Z"
    assert refs[0].normalized_end == "2026-07-13T16:00:00Z"
    assert refs[0].reference_timezone == "Asia/Shanghai"


def test_legacy_fixture_truthfully_marks_segment_timezone_fallback():
    segment = load_fixture(LEGACY_FIXTURE)
    assert segment.schema_version == "1"
    assert all(
        turn.timezone_source == "legacy_segment_fallback"
        for turn in segment.turns
    )
    mention = normalize_temporal_references(segment.turns[0])[0]
    assert mention.reference_timezone == "+08:00"
    assert mention.reference_timestamp == "2026-07-14T02:00:00Z"
    assert (mention.normalized_start, mention.normalized_end) == (
        "2026-07-12T16:00:00Z", "2026-07-13T16:00:00Z",
    )


def test_cross_midnight_relative_dates_use_new_york_calendar_not_utc():
    before_midnight = ColdDraftTurn(
        "before", "user", "today", datetime(2026, 7, 15, 3, 55, tzinfo=UTC),
        "America/New_York", "client",
    )
    after_midnight = ColdDraftTurn(
        "after", "user", "yesterday tomorrow", datetime(2026, 7, 15, 4, 5, tzinfo=UTC),
        "America/New_York", "client",
    )
    before_ref = normalize_temporal_references(before_midnight)[0]
    after_refs = normalize_temporal_references(after_midnight)
    assert before_ref.reference_timestamp == "2026-07-15T03:55:00Z"
    assert (before_ref.normalized_start, before_ref.normalized_end) == (
        "2026-07-14T04:00:00Z", "2026-07-15T04:00:00Z",
    )
    assert after_refs[0].reference_timestamp == "2026-07-15T04:05:00Z"
    assert (after_refs[0].normalized_start, after_refs[0].normalized_end) == (
        "2026-07-14T04:00:00Z", "2026-07-15T04:00:00Z",
    )
    assert (after_refs[1].normalized_start, after_refs[1].normalized_end) == (
        "2026-07-16T04:00:00Z", "2026-07-17T04:00:00Z",
    )


def test_dst_timezone_uses_zoneinfo_offset_for_each_turn_date():
    winter = ColdDraftTurn(
        "winter", "user", "today", datetime(2026, 1, 15, 17, tzinfo=UTC),
        "America/New_York", "client",
    )
    summer = replace(
        winter,
        turn_id="summer",
        timestamp=datetime(2026, 7, 15, 16, tzinfo=UTC),
    )
    winter_ref = normalize_temporal_references(winter)[0]
    summer_ref = normalize_temporal_references(summer)[0]
    assert (winter_ref.normalized_start, winter_ref.normalized_end) == (
        "2026-01-15T05:00:00Z", "2026-01-16T05:00:00Z",
    )
    assert (summer_ref.normalized_start, summer_ref.normalized_end) == (
        "2026-07-15T04:00:00Z", "2026-07-16T04:00:00Z",
    )


@pytest.mark.parametrize("field,value,code", [
    ("segment_id", "", "invalid_segment_id"),
    ("state", "consumed", "segment_not_pending"),
    ("schema_version", "3", "unsupported_schema_version"),
])
def test_schema_rejections(field, value, code):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw[field] = value
    with pytest.raises(SegmentValidationError) as error:
        parse_segment(raw)
    assert error.value.code == code


@pytest.mark.parametrize("field,value,code", [
    ("role", "tool", "invalid_role"),
    ("content", "", "invalid_content"),
    ("timestamp", "not-a-time", "invalid_turn_timestamp"),
    ("timestamp", "2026-07-14T10:00:00", "timestamp_timezone_required"),
])
def test_turn_schema_rejections(field, value, code):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["turns"][0][field] = value
    with pytest.raises(SegmentValidationError) as error:
        parse_segment(raw)
    assert error.value.code == code


def test_invalid_source_timezone_is_rejected():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["source_timezone"] = "Mars/Olympus"
    with pytest.raises(SegmentValidationError) as error:
        parse_segment(raw)
    assert error.value.code == "invalid_source_timezone"


def test_entity_fallback_enables_shared_raven_relation(tmp_path):
    adapter, backend = make_adapter(tmp_path)
    adapter.ingest(load_fixture(FIXTURE))
    assert "Raven" in backend.relationship_entities


def test_recall_is_bounded_stable_and_contains_only_dtos(tmp_path):
    adapter, _ = make_adapter(tmp_path)
    adapter.ingest(load_fixture(FIXTURE))
    policy = RecallPolicy(top_k=3, max_evidence_items=2, max_chars=45)
    first = adapter.recall("membrane experiment", policy)
    second = adapter.recall("membrane experiment", policy)
    assert len(first.evidence) <= 2
    assert len(first.rendered_text) <= 45
    assert [item.evidence_id for item in first.evidence] == [item.evidence_id for item in second.evidence]
    assert first.truncated is True
    assert all(item.provenance.segment_id == "fixture-segment-membrane-001" for item in first.evidence)


def test_empty_and_failed_recall_are_safe(tmp_path):
    adapter, backend = make_adapter(tmp_path)
    empty = adapter.recall("nothing", RecallPolicy())
    assert empty.evidence == () and empty.safe_error_code is None
    backend.fail_recall = True
    failed = adapter.recall("nothing", RecallPolicy())
    assert failed.evidence == () and failed.safe_error_code == "recall_unavailable"
    assert "secret" not in repr(failed).lower() and "private" not in repr(failed).lower()


def test_corrupt_state_returns_structured_failure(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    adapter = MagmaMemoryAdapter(FakeBackend(), IngestionStateStore(path))
    result = adapter.ingest(load_fixture(FIXTURE))
    assert result.safe_error_code == "state_corrupt" and not result.retryable


def test_real_backend_initialization_failure_is_structured(tmp_path, monkeypatch):
    import adapter.backend as backend_module

    def fail_init(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY=secret C:\\private\\model")

    monkeypatch.setattr(backend_module, "RealMagmaBackend", fail_init)
    adapter = MagmaMemoryAdapter.create_real(tmp_path / "magma", IngestionStateStore(tmp_path / "state.json"))
    ingested = adapter.ingest(load_fixture(FIXTURE))
    recalled = adapter.recall("membrane", RecallPolicy())
    assert ingested.safe_error_code == "memory_write_failed"
    assert recalled.safe_error_code == "recall_unavailable"
    assert "secret" not in repr((ingested, recalled)).lower()


def test_restart_recovers_completed_idempotency_state(tmp_path):
    backend = FakeBackend()
    first = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "state.json"))
    initial = first.ingest(load_fixture(FIXTURE))
    restarted = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "state.json"))
    result = restarted.ingest(load_fixture(FIXTURE))
    assert result.already_ingested and result.memory_ids == initial.memory_ids


class _ControlledNodeType:
    EVENT = "EVENT"
    ENTITY = "ENTITY"


class _ControlledEventNode:
    def __init__(self, node_id, text, timestamp, metadata, *, node_type="EVENT"):
        self.node_id = node_id
        self.node_type = node_type
        self.content_narrative = text
        self.timestamp = timestamp
        self.attributes = metadata


class _ControlledTraversalConstraints:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _controlled_provenance(turn_id, timestamp):
    return {
        "segment_id": "controlled-segment",
        "conversation_id": "controlled-conversation",
        "turn_id": turn_id,
        "source_timestamp": timestamp.isoformat(),
        "source_timezone": "Asia/Shanghai",
        "ingestion_version": "cold-draft-v1",
        "timezone_source": "client",
    }


def _controlled_event(
    node_id,
    turn_id,
    text,
    timestamp,
    *,
    node_type=_ControlledNodeType.EVENT,
    include_provenance=True,
):
    metadata = {
        "evidence_id": f"evidence-{turn_id}",
        "private_path": [node_id],
        "backend_score": 0.999,
    }
    if include_provenance:
        metadata["provenance"] = _controlled_provenance(turn_id, timestamp)
    return _ControlledEventNode(
        node_id,
        text,
        timestamp,
        metadata,
        node_type=node_type,
    )


def _controlled_real_backend(tmp_path, monkeypatch, nodes, context):
    from types import ModuleType

    memory_package = ModuleType("memory")
    memory_package.__path__ = []
    graph_module = ModuleType("memory.graph_db")
    graph_module.TraversalConstraints = _ControlledTraversalConstraints
    graph_module.EventNode = _ControlledEventNode
    graph_module.NodeType = _ControlledNodeType

    class ControlledGraphDB:
        def get_node(self, node_id):
            return nodes.get(node_id)

        def load(self, _path):
            raise AssertionError("controlled backend must not load persisted data")

    class ControlledTRG:
        def __init__(self, **_kwargs):
            self.graph_db = ControlledGraphDB()
            self.query_calls = []

        def query(self, query, *, max_results, constraints):
            self.query_calls.append((query, max_results, constraints))
            return context

    trg_module = ModuleType("memory.trg_memory")
    trg_module.TemporalResonanceGraphMemory = ControlledTRG
    monkeypatch.setitem(sys.modules, "memory", memory_package)
    monkeypatch.setitem(sys.modules, "memory.graph_db", graph_module)
    monkeypatch.setitem(sys.modules, "memory.trg_memory", trg_module)
    return RealMagmaBackend(tmp_path / "controlled-magma", upstream_dir=tmp_path)


def test_real_backend_projects_expansions_once_using_minimum_hop_and_stable_order(
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace

    anchor = _controlled_event(
        "magma-anchor-internal",
        "anchor",
        "Anchor memory",
        datetime(2026, 7, 14, 2, tzinfo=UTC),
    )
    first = _controlled_event(
        "magma-first-internal",
        "first",
        "First graph expansion",
        datetime(2026, 7, 14, 3, tzinfo=UTC),
    )
    second = _controlled_event(
        "magma-second-internal",
        "second",
        "Second graph expansion",
        datetime(2026, 7, 14, 4, tzinfo=UTC),
    )
    nodes = {
        anchor.node_id: anchor,
        first.node_id: first,
        second.node_id: second,
    }
    context = SimpleNamespace(
        anchor_nodes=[anchor],
        traversal_paths=[
            [anchor.node_id, second.node_id, first.node_id],
            [anchor.node_id, first.node_id],
            [anchor.node_id, first.node_id],
            [anchor.node_id, second.node_id],
        ],
        narrative_context="narrative-secret",
        metadata={"search_scores": [0.8], "stats": {"private": True}},
    )
    backend = _controlled_real_backend(tmp_path, monkeypatch, nodes, context)

    candidates = backend.recall(
        "query",
        RecallPolicy(top_k=1, max_evidence_items=3, max_graph_depth=3),
    )

    assert [item.metadata["evidence_id"] for item in candidates] == [
        "evidence-anchor",
        "evidence-first",
        "evidence-second",
    ]
    assert backend.trg.query_calls[0][1] == 1
    assert backend.trg.query_calls[0][2].max_depth == 3


def test_real_backend_skips_malformed_paths_non_events_and_invalid_expansions(
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace

    anchor = _controlled_event(
        "magma-anchor-internal",
        "anchor",
        "Anchor memory",
        datetime(2026, 7, 14, 2, tzinfo=UTC),
    )
    valid = _controlled_event(
        "magma-valid-internal",
        "valid",
        "Valid expansion",
        datetime(2026, 7, 14, 3, tzinfo=UTC),
    )
    behind_non_string = _controlled_event(
        "magma-behind-non-string-internal",
        "behind-non-string",
        "Must not survive an invalid path",
        datetime(2026, 7, 14, 3, tzinfo=UTC),
    )
    behind_blank = _controlled_event(
        "magma-behind-blank-internal",
        "behind-blank",
        "Must not survive a blank path ID",
        datetime(2026, 7, 14, 3, tzinfo=UTC),
    )
    non_event = _controlled_event(
        "magma-entity-internal",
        "entity",
        "Entity node",
        datetime(2026, 7, 14, 3, tzinfo=UTC),
        node_type=_ControlledNodeType.ENTITY,
    )
    blank = _controlled_event(
        "magma-blank-internal",
        "blank",
        " ",
        datetime(2026, 7, 14, 3, tzinfo=UTC),
    )
    no_time = _controlled_event(
        "magma-no-time-internal",
        "no-time",
        "Missing time",
        datetime(2026, 7, 14, 3, tzinfo=UTC),
    )
    no_time.timestamp = None
    nodes = {
        item.node_id: item
        for item in (anchor, valid, behind_non_string, behind_blank, non_event, blank, no_time)
    }
    context = SimpleNamespace(
        anchor_nodes=[anchor],
        traversal_paths=[
            None,
            [],
            "not-a-path",
            ["foreign-anchor", valid.node_id],
            [anchor.node_id, 42],
            [anchor.node_id, 42, behind_non_string.node_id],
            [anchor.node_id, " ", behind_blank.node_id],
            [anchor.node_id, "missing-node"],
            [anchor.node_id, non_event.node_id],
            [anchor.node_id, blank.node_id],
            [anchor.node_id, no_time.node_id],
            [anchor.node_id, valid.node_id],
        ],
        narrative_context="narrative-secret",
        metadata={"search_scores": [0.8]},
    )
    backend = _controlled_real_backend(tmp_path, monkeypatch, nodes, context)

    candidates = backend.recall("query", RecallPolicy(top_k=1))

    assert [item.metadata["evidence_id"] for item in candidates] == [
        "evidence-anchor",
        "evidence-valid",
    ]


def test_adapter_uses_total_evidence_limit_and_does_not_leak_graph_internals(
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace

    anchor = _controlled_event(
        "magma-anchor-uuid-secret",
        "anchor",
        "Anchor memory",
        datetime(2026, 7, 14, 2, tzinfo=UTC),
    )
    first = _controlled_event(
        "magma-first-uuid-secret",
        "first",
        "First graph expansion",
        datetime(2026, 7, 14, 3, tzinfo=UTC),
    )
    second = _controlled_event(
        "magma-second-uuid-secret",
        "second",
        "Second graph expansion",
        datetime(2026, 7, 14, 4, tzinfo=UTC),
    )
    missing_provenance = _controlled_event(
        "magma-missing-provenance-secret",
        "missing",
        "Must be skipped",
        datetime(2026, 7, 14, 2, 30, tzinfo=UTC),
        include_provenance=False,
    )
    incomplete_provenance = _controlled_event(
        "magma-incomplete-provenance-secret",
        "incomplete",
        "Incomplete provenance must not consume budget",
        datetime(2026, 7, 14, 2, 10, tzinfo=UTC),
    )
    incomplete_provenance.attributes["provenance"].pop("source_timestamp")
    blank_provenance = _controlled_event(
        "magma-blank-provenance-secret",
        "blank-provenance",
        "Blank provenance must not consume budget",
        datetime(2026, 7, 14, 2, 20, tzinfo=UTC),
    )
    blank_provenance.attributes["provenance"]["segment_id"] = " "
    naive_timestamp = _controlled_event(
        "magma-naive-timestamp-secret",
        "naive-timestamp",
        "Naive timestamp must not consume budget",
        datetime(2026, 7, 14, 2, 30),
    )
    blank_evidence_id = _controlled_event(
        "magma-blank-evidence-secret",
        "blank-evidence",
        "Blank evidence ID must not consume budget",
        datetime(2026, 7, 14, 2, 40, tzinfo=UTC),
    )
    blank_evidence_id.attributes["evidence_id"] = " "
    legacy_provenance = _controlled_event(
        "magma-legacy-provenance-secret",
        "legacy",
        "Legacy provenance expansion",
        datetime(2026, 7, 14, 2, 50, tzinfo=UTC),
    )
    legacy_provenance.attributes["provenance"].pop("timezone_source")
    invalid_timezone_source = _controlled_event(
        "magma-invalid-timezone-source-secret",
        "invalid-timezone-source",
        "Invalid timezone source must not consume budget",
        datetime(2026, 7, 14, 2, 5, tzinfo=UTC),
    )
    invalid_timezone_source.attributes["provenance"]["timezone_source"] = "invalid"
    nodes = {
        item.node_id: item
        for item in (
            anchor,
            first,
            second,
            missing_provenance,
            incomplete_provenance,
            blank_provenance,
            naive_timestamp,
            blank_evidence_id,
            legacy_provenance,
            invalid_timezone_source,
        )
    }
    context = SimpleNamespace(
        anchor_nodes=[anchor],
        traversal_paths=[
            [anchor.node_id, invalid_timezone_source.node_id],
            [anchor.node_id, legacy_provenance.node_id],
            [anchor.node_id, incomplete_provenance.node_id],
            [anchor.node_id, blank_provenance.node_id],
            [anchor.node_id, naive_timestamp.node_id],
            [anchor.node_id, blank_evidence_id.node_id],
            [anchor.node_id, missing_provenance.node_id],
            [anchor.node_id, second.node_id],
            [anchor.node_id, first.node_id],
            [anchor.node_id, first.node_id],
        ],
        narrative_context="narrative-secret",
        metadata={"search_scores": [0.8], "stats": {"private": True}},
    )
    backend = _controlled_real_backend(tmp_path, monkeypatch, nodes, context)
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "state.json"),
    )
    policy = RecallPolicy(top_k=1, max_evidence_items=2, max_chars=500, max_nodes=3)

    backend_candidates = backend.recall("query", policy)
    backend_evidence_ids = [
        item.metadata["evidence_id"] for item in backend_candidates
    ]
    assert backend_evidence_ids == [
        "evidence-anchor",
        "evidence-legacy",
        "evidence-first",
    ]
    assert "evidence-invalid-timezone-source" not in backend_evidence_ids

    first_context = adapter.recall("query", policy)
    second_context = adapter.recall("query", policy)

    assert [item.evidence_id for item in first_context.evidence] == [
        "evidence-anchor",
        "evidence-legacy",
    ]
    assert len(backend_candidates) > policy.max_evidence_items
    assert len(first_context.evidence) <= policy.max_evidence_items
    assert [item.evidence_id for item in first_context.evidence] == [
        item.evidence_id for item in second_context.evidence
    ]
    assert first_context.evidence[1].timestamp == "2026-07-14T02:50:00+00:00"
    expected_legacy_provenance = _controlled_provenance(
        "legacy",
        datetime(2026, 7, 14, 2, 50, tzinfo=UTC),
    )
    expected_legacy_provenance.pop("timezone_source")
    assert first_context.evidence[1].provenance == SourceProvenance(
        **expected_legacy_provenance
    )
    assert (
        first_context.evidence[1].provenance.timezone_source
        == "legacy_segment_fallback"
    )
    public_output = repr(first_context)
    for private_value in (
        "magma-anchor-uuid-secret",
        "magma-legacy-provenance-secret",
        "magma-first-uuid-secret",
        "private_path",
        "backend_score",
        "search_scores",
        "narrative-secret",
    ):
        assert private_value not in public_output
    assert all(not hasattr(item, "score") for item in first_context.evidence)


@pytest.mark.skipif(
    Path(sys.executable).resolve() != (Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe").resolve(),
    reason="real MAGMA test runs in the isolated Conversation Memory environment",
)
def test_real_magma_non_anchor_traversal_event_enters_bounded_evidence(tmp_path):
    segment = load_fixture(FIXTURE)
    backend = RealMagmaBackend(tmp_path / "magma")
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "state.json"),
    )
    result = adapter.ingest(segment)
    assert result.status == "completed"
    policy = RecallPolicy(
        top_k=1,
        max_evidence_items=2,
        max_chars=1000,
        max_graph_depth=2,
        max_nodes=10,
    )
    query = segment.turns[0].content
    constraints = backend._constraints_type(
        max_depth=policy.max_graph_depth,
        max_nodes=policy.max_nodes,
        follow_temporal=True,
        follow_semantic=True,
        follow_causal=True,
    )
    query_context = backend.trg.query(
        query,
        max_results=policy.top_k,
        constraints=constraints,
    )
    anchor_id = result.memory_ids[0]
    expansion_id = result.memory_ids[1]
    anchor_ids = [node.node_id for node in query_context.anchor_nodes]
    traversed_ids = {
        node_id
        for path in query_context.traversal_paths
        for node_id in path[1:]
    }
    assert anchor_ids == [anchor_id]
    assert expansion_id not in anchor_ids
    assert expansion_id in traversed_ids

    recalled = adapter.recall(query, policy)

    expected_anchor_evidence_id = adapter._evidence_id(
        segment.segment_id,
        segment.turns[0].turn_id,
    )
    expected_expansion_evidence_id = adapter._evidence_id(
        segment.segment_id,
        segment.turns[1].turn_id,
    )
    assert [item.evidence_id for item in recalled.evidence] == [
        expected_anchor_evidence_id,
        expected_expansion_evidence_id,
    ]
    assert len(recalled.evidence) <= policy.max_evidence_items
    expansion = recalled.evidence[1]
    assert expansion.timestamp == segment.turns[1].timestamp.isoformat()
    assert expansion.provenance == SourceProvenance(
        segment_id=segment.segment_id,
        conversation_id=segment.conversation_id,
        turn_id=segment.turns[1].turn_id,
        source_timestamp=segment.turns[1].timestamp.isoformat(),
        source_timezone=segment.turns[1].source_timezone,
        ingestion_version=adapter.ingestion_version,
        timezone_source=segment.turns[1].timezone_source,
    )
    assert sum(
        item.evidence_id == expected_expansion_evidence_id
        for item in recalled.evidence
    ) == 1
    public_output = repr(recalled)
    for internal_value in (
        anchor_id,
        expansion_id,
        "traversal_paths",
        "search_scores",
        "narrative_context",
        "embedding_vector",
        "raw_content",
    ):
        assert internal_value not in public_output

@pytest.mark.skipif(
    Path(sys.executable).resolve() != (Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe").resolve(),
    reason="real MAGMA test runs in the isolated Conversation Memory environment",
)
def test_real_magma_fixture_ingestion_and_recall(tmp_path):
    backend = RealMagmaBackend(tmp_path / "magma")
    adapter = MagmaMemoryAdapter(backend, IngestionStateStore(tmp_path / "state.json"))
    result = adapter.ingest(load_fixture(FIXTURE))
    assert result.status == "completed" and len(result.memory_ids) == 4
    assert any(link.link_type.value == "ENTITY" for link in backend.trg.graph_db.links.values())
    context = adapter.recall("Why did the membrane experiment fail?", RecallPolicy(top_k=3, max_chars=500, max_evidence_items=3))
    assert context.evidence
    assert any("solvent" in item.text.lower() for item in context.evidence)
