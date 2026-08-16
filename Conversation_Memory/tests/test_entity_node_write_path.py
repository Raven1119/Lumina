"""Write-side generic EntityRef slice (production migration slice 2).

A validated GroundedMemoryUnit whose subject is the current user persists
generic ``subject_entity_ref="E_001"`` metadata, a graph-only EntityNode, and
a single ``Event --ENTITY/REFERS_TO(role=subject)--> EntityNode`` edge. The
same graph contract applies to uniquely bound ordinary named subjects. The
write is idempotent across retry and restart, and EntityNodes never enter the
VectorDB or the temporal chain (slice 1 boundary). Shadow evidence:
``docs/experiments/entity_composite_e2e/`` and
``docs/experiments/context_role_entityref/``.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from adapter.backend import RealMagmaBackend
from adapter.grounded_formation import FORMATION_VERSION
from adapter.magma_adapter import MagmaMemoryAdapter
from adapter.models import ColdDraftSegment, ColdDraftTurn
from adapter.user_self import CURRENT_USER_ENTITY_REF
from ingestion.state_store import IngestionStateStore

_REAL_MAGMA_VENV = (
    Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
)

pytestmark = pytest.mark.skipif(
    Path(sys.executable).resolve() != _REAL_MAGMA_VENV.resolve(),
    reason="real MAGMA test runs in the isolated Conversation Memory environment",
)

_ENTITY_NODE_ID = "entity:e_001"


class _FakeFormationModel:
    def __init__(self, units, *, span_mentions=None, selector_choices=None):
        self.units = units
        self.span_mentions = dict(span_mentions or {})
        self.selector_choices = dict(selector_choices or {})
        self.extraction_calls = 0
        self.selector_calls = 0

    def generate(self, recent_context, user_message, *, system_prompt):
        assert recent_context == []
        if system_prompt.startswith("Extract only entity mentions"):
            self.extraction_calls += 1
            for span, entities in self.span_mentions.items():
                if f"Text: {span}" in system_prompt:
                    return json.dumps({"entities": entities}, ensure_ascii=False)
            return json.dumps({"entities": []}, ensure_ascii=False)
        if system_prompt.startswith("Select from the Candidate mentions"):
            self.selector_calls += 1
            for marker, entities in self.selector_choices.items():
                if marker in system_prompt:
                    return json.dumps({"entities": entities}, ensure_ascii=False)
            return json.dumps({"entities": []}, ensure_ascii=False)
        return json.dumps({"units": self.units}, ensure_ascii=False)


class _MustNotBeCalledModel:
    def __init__(self):
        self.calls = 0

    def generate(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("checkpoint reuse must not call the provider")


def _segment(segment_id, *turns):
    return ColdDraftSegment(
        segment_id=segment_id,
        conversation_id=f"{segment_id}-conversation",
        state="pending_digest",
        turns=tuple(
            ColdDraftTurn(
                turn_id=turn_id,
                role=role,
                content=text,
                timestamp=datetime(2026, 8, 12, index, tzinfo=UTC),
                source_timezone="Asia/Shanghai",
                timezone_source="client",
            )
            for index, (turn_id, role, text) in enumerate(turns)
        ),
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        source_timezone="Asia/Shanghai",
        schema_version="2",
    )


def _unit_payload(text, subject, relation, value, refs):
    return {
        "text": text,
        "subject": subject,
        "relation": relation,
        "value": value,
        "source_refs": [
            {"turn_id": turn_id, "supporting_span": span}
            for turn_id, span in refs
        ],
        "referenced_time": None,
    }


def _ingest(tmp_path, segment, units, dirname="magma"):
    model = _FakeFormationModel(units)
    backend = RealMagmaBackend(tmp_path / dirname)
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / f"{dirname}-state.json"),
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    )
    result = adapter.ingest(segment)
    assert result.status == "completed"
    return backend, result


def _entity_nodes(backend):
    from memory.graph_db import NodeType

    return [
        node
        for node in backend.trg.graph_db.nodes.values()
        if node.node_type == NodeType.ENTITY
    ]


def _refers_to_links(backend):
    from memory.graph_db import LinkSubType, LinkType

    return [
        link
        for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.ENTITY
        and link.properties.get("sub_type") == LinkSubType.REFERS_TO.value
    ]


def test_current_user_unit_persists_e001_node_and_single_refers_to_edge(tmp_path):
    introduction = "我叫林岚，来自东海大学物理学院，现在大二"
    backend, result = _ingest(
        tmp_path,
        _segment("entity-segment-1", ("u1", "user", introduction)),
        [_unit_payload(
            "林岚 来自东海大学物理学院", "林岚", "来自", "东海大学物理学院",
            [("u1", "我叫林岚，来自东海大学物理学院")],
        )],
    )
    memory_id = result.memory_ids[0]

    event = backend.trg.graph_db.get_node(memory_id)
    assert event.attributes["subject_entity_ref"] == CURRENT_USER_ENTITY_REF
    assert CURRENT_USER_ENTITY_REF == "E_001"

    entity_nodes = _entity_nodes(backend)
    assert [node.node_id for node in entity_nodes] == [_ENTITY_NODE_ID]
    assert entity_nodes[0].attributes.get("entity_ref") == CURRENT_USER_ENTITY_REF
    assert not entity_nodes[0].embedding_vector
    # graph-only: the EntityNode is never vector-indexed
    assert _ENTITY_NODE_ID not in backend.trg.vector_db.id_to_index

    refers_to = _refers_to_links(backend)
    assert [
        (link.source_node_id, link.target_node_id, link.properties.get("role"))
        for link in refers_to
    ] == [(memory_id, _ENTITY_NODE_ID, "subject")]
    # single canonical edge only: no reverse MENTIONED_IN edge
    from memory.graph_db import LinkSubType, LinkType

    assert not [
        link
        for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.ENTITY
        and link.properties.get("sub_type") == LinkSubType.MENTIONED_IN.value
    ]


def test_entity_write_idempotent_across_retry_and_reload(tmp_path):
    introduction = "我叫林岚，来自东海大学物理学院，现在大二"
    backend, result = _ingest(
        tmp_path,
        _segment("entity-segment-1", ("u1", "user", introduction)),
        [_unit_payload(
            "林岚 来自东海大学物理学院", "林岚", "来自", "东海大学物理学院",
            [("u1", "我叫林岚，来自东海大学物理学院")],
        )],
    )
    # simulate ingestion retry: relationship step runs again on the same ids
    backend.create_relationships(list(result.memory_ids))
    backend.persist()

    reloaded = RealMagmaBackend(tmp_path / "magma")
    entity_nodes = _entity_nodes(reloaded)
    assert [node.node_id for node in entity_nodes] == [_ENTITY_NODE_ID]
    assert len(_refers_to_links(reloaded)) == 1
    event = reloaded.trg.graph_db.get_node(result.memory_ids[0])
    assert event.attributes["subject_entity_ref"] == CURRENT_USER_ENTITY_REF


def test_second_current_user_event_reuses_node_and_temporal_chain_stays_pure(
    tmp_path,
):
    backend, first = _ingest(
        tmp_path,
        _segment("entity-segment-1", ("u1", "user", "我叫林岚。")),
        [_unit_payload("我叫林岚。", "我", "叫", "林岚",
                       [("u1", "我叫林岚")])],
    )
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "magma-state.json"),
        ingestion_version=FORMATION_VERSION,
        formation_model=_FakeFormationModel([_unit_payload(
            "我现在大三。", "我", "现在", "大三",
            [("u1", "我现在大三")],
        )]),
    )
    second = adapter.ingest(
        _segment("entity-segment-2", ("u1", "user", "我现在大三。")),
    )
    assert second.status == "completed"

    # both current-user events share one EntityNode
    assert [node.node_id for node in _entity_nodes(backend)] == [_ENTITY_NODE_ID]
    refers_to = _refers_to_links(backend)
    assert sorted(link.source_node_id for link in refers_to) == sorted(
        [first.memory_ids[0], second.memory_ids[0]],
    )
    assert all(link.target_node_id == _ENTITY_NODE_ID for link in refers_to)

    # slice 1 boundary: the entity node (created between the two events, with
    # a current timestamp) never enters the temporal chain
    from memory.graph_db import LinkType

    temporal_links = [
        link
        for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.TEMPORAL
    ]
    assert all(
        _ENTITY_NODE_ID not in (link.source_node_id, link.target_node_id)
        for link in temporal_links
    )
    assert any(
        link.source_node_id == first.memory_ids[0]
        and link.target_node_id == second.memory_ids[0]
        for link in temporal_links
    )


def test_third_party_named_subject_creates_one_entity_node(tmp_path):
    backend, result = _ingest(
        tmp_path,
        _segment("entity-segment-3", ("u1", "user", "我朋友小林开了家公司。")),
        [_unit_payload("小林开了家公司。", "小林", "开了", "家公司",
                       [("u1", "小林开了家公司。")])],
    )
    assert result.memory_ids
    event = backend.trg.graph_db.get_node(result.memory_ids[0])
    entity_ref = event.attributes["subject_entity_ref"]
    assert entity_ref.startswith("E_")
    assert [node.attributes for node in _entity_nodes(backend)] == [{
        "entity_ref": entity_ref,
        "canonical_surface": "小林",
    }]
    assert [link.source_node_id for link in _refers_to_links(backend)] == [
        result.memory_ids[0],
    ]


# --- Production Slice 1: durable grounded mention bindings -----------------
# Shadow evidence: docs/experiments/grounded_mention_selection/RESULT.md.

_MENTION_BINDING_KEYS = {
    "unit_id", "entity_ref", "canonical_surface", "turn_id", "supporting_span",
}


def _ingest_with_model(tmp_path, segment, model, dirname="magma"):
    backend = RealMagmaBackend(tmp_path / dirname)
    state_store = IngestionStateStore(tmp_path / f"{dirname}-state.json")
    adapter = MagmaMemoryAdapter(
        backend,
        state_store,
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    )
    return backend, state_store, adapter


def _mention_state(state_store, segment_id):
    state = state_store.get(state_store.key(segment_id, FORMATION_VERSION))
    return state["mention_entity_bindings"]


def test_single_unit_span_binds_all_grounded_mentions_without_selector(tmp_path):
    span = "小林在南京大学与王老师会面。"
    segment = _segment("mention-single", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload("小林与王老师会面。", "小林", "会面", "王老师",
                       [("u1", span)])],
        span_mentions={span: ["小林", "南京大学", "王老师"]},
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    result = adapter.ingest(segment)

    assert result.status == "completed"
    assert model.extraction_calls == 1
    assert model.selector_calls == 0

    state = state_store.get(state_store.key(segment.segment_id, FORMATION_VERSION))
    unit_id = state["unit_ids"][0]
    bindings = _mention_state(state_store, segment.segment_id)
    assert len(bindings) == 3
    assert all(set(record) == _MENTION_BINDING_KEYS for record in bindings)
    assert all(record["unit_id"] == unit_id for record in bindings)
    assert all(
        record["turn_id"] == "u1" and record["supporting_span"] == span
        for record in bindings
    )
    assert {record["canonical_surface"] for record in bindings} == {
        "小林", "南京大学", "王老师",
    }
    assert all(record["entity_ref"].startswith("E_") for record in bindings)

    event = backend.trg.graph_db.get_node(result.memory_ids[0])
    assert set(event.attributes["mention_entity_surfaces"]) == {
        "小林", "南京大学", "王老师",
    }
    assert len(event.attributes["mention_entity_refs"]) == 3

    # Slice 2: each non-subject mention ref gets a graph-only EntityNode and
    # one generic role-less REFERS_TO edge; the subject-surface mention adds
    # neither a second node nor a duplicate edge.
    assert [node.attributes["canonical_surface"] for node in _entity_nodes(backend)] == [
        "小林", "南京大学", "王老师",
    ]
    links = _refers_to_links(backend)
    assert len(links) == 3
    generic = [link for link in links if "role" not in link.properties]
    assert len(generic) == 2
    assert all(set(link.properties) == {"sub_type"} for link in generic)


def test_shared_span_selector_isolates_each_units_mentions(tmp_path):
    span = "小林的导师是王老师，林素在南京大学读书。"
    segment = _segment("mention-shared", ("u1", "user", span))
    model = _FakeFormationModel(
        [
            _unit_payload("小林的导师是王老师。", "小林", "导师", "王老师",
                          [("u1", span)]),
            _unit_payload("林素在南京大学读书。", "林素", "读书", "南京大学",
                          [("u1", span)]),
        ],
        span_mentions={span: ["小林", "王老师", "林素", "南京大学"]},
        selector_choices={
            "Fact value: 王老师": ["小林", "王老师"],
            "Fact value: 南京大学": ["林素", "南京大学"],
        },
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    result = adapter.ingest(segment)

    assert result.status == "completed"
    assert model.extraction_calls == 1
    assert model.selector_calls == 2

    bindings = _mention_state(state_store, segment.segment_id)
    assert len(bindings) == 4
    by_unit = {}
    for record in bindings:
        assert set(record) == _MENTION_BINDING_KEYS
        by_unit.setdefault(record["unit_id"], set()).add(record["canonical_surface"])
    # zero cross-unit contamination: each unit carries exactly its own fact
    assert {frozenset(surfaces) for surfaces in by_unit.values()} == {
        frozenset({"小林", "王老师"}),
        frozenset({"林素", "南京大学"}),
    }
    assert len(by_unit) == 2

    for memory_id in result.memory_ids:
        event = backend.trg.graph_db.get_node(memory_id)
        unit_id = event.attributes["grounded_memory_unit_id"]
        assert set(event.attributes["mention_entity_surfaces"]) == by_unit[unit_id]
        assert len(event.attributes["mention_entity_refs"]) == len(by_unit[unit_id])
    # Slice 2: two subject edges plus one generic mention edge per unit
    # (王老师 for the 导师 unit, 南京大学 for the 读书 unit); the
    # subject-surface mentions add no duplicate edges.
    links = _refers_to_links(backend)
    assert len(links) == 4
    assert sum(1 for link in links if "role" not in link.properties) == 2


def test_no_entity_span_binds_nothing_and_never_calls_selector(tmp_path):
    span = "我今天有点累，想早点休息。"
    segment = _segment("mention-empty", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload("我想早点休息。", "我", "想", "早点休息", [("u1", span)])],
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    result = adapter.ingest(segment)

    assert result.status == "completed"
    assert model.extraction_calls == 1
    assert model.selector_calls == 0
    assert _mention_state(state_store, segment.segment_id) == []
    event = backend.trg.graph_db.get_node(result.memory_ids[0])
    assert event.attributes["mention_entity_refs"] == []
    assert event.attributes["mention_entity_surfaces"] == []
    # CURRENT_USER subject binding stays exactly as-is.
    assert event.attributes["subject_entity_ref"] == CURRENT_USER_ENTITY_REF


def test_transient_invalid_extraction_retries_once_then_succeeds(tmp_path):
    span = "陈雨在北京工作。"
    segment = _segment("mention-flaky", ("u1", "user", span))

    class FlakyExtractionModel(_FakeFormationModel):
        def generate(self, recent_context, user_message, *, system_prompt):
            if (
                system_prompt.startswith("Extract only entity mentions")
                and self.extraction_calls == 0
            ):
                self.extraction_calls += 1
                return "not-json"
            return super().generate(
                recent_context, user_message, system_prompt=system_prompt,
            )

    model = FlakyExtractionModel(
        [_unit_payload(span, "陈雨", "工作", "北京", [("u1", span)])],
        span_mentions={span: ["陈雨", "北京"]},
    )
    _backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    result = adapter.ingest(segment)

    assert result.status == "completed"
    assert model.extraction_calls == 2
    assert {
        record["canonical_surface"]
        for record in _mention_state(state_store, segment.segment_id)
    } == {"陈雨", "北京"}


def test_persistent_extraction_failure_is_retryable_without_checkpoint(tmp_path):
    span = "陈雨在北京工作。"
    segment = _segment("mention-persistent-failure", ("u1", "user", span))

    class BadExtractionModel(_FakeFormationModel):
        def generate(self, recent_context, user_message, *, system_prompt):
            if system_prompt.startswith("Extract only entity mentions"):
                self.extraction_calls += 1
                return "not-json"
            return super().generate(
                recent_context, user_message, system_prompt=system_prompt,
            )

    model = BadExtractionModel(
        [_unit_payload(span, "陈雨", "工作", "北京", [("u1", span)])],
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    result = adapter.ingest(segment)

    assert result.status == "failed"
    assert result.retryable
    assert result.safe_error_code == "mention_extraction_failed"
    assert model.extraction_calls == 2  # one bounded retry, then give up
    assert result.memory_ids == ()
    assert state_store.get(
        state_store.key(segment.segment_id, FORMATION_VERSION),
    ) is None


def test_crash_after_mention_checkpoint_retries_without_new_provider_calls(tmp_path):
    span = "李娜加入了星河科技。"
    segment = _segment("mention-crash", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload(span, "李娜", "加入", "星河科技", [("u1", span)])],
        span_mentions={span: ["李娜", "星河科技"]},
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)

    def crash_before_magma_write(*args, **kwargs):
        raise RuntimeError("crash before MAGMA write")

    original_add_event = backend.add_event
    backend.add_event = crash_before_magma_write
    first = adapter.ingest(segment)
    assert first.status == "failed"
    assert first.retryable
    assert model.extraction_calls == 1
    assert model.selector_calls == 0
    checkpoint = state_store.get(state_store.key(segment.segment_id, FORMATION_VERSION))
    assert len(checkpoint["mention_entity_bindings"]) == 2

    backend.add_event = original_add_event
    guarded = _MustNotBeCalledModel()
    retry = MagmaMemoryAdapter(
        backend,
        state_store,
        ingestion_version=FORMATION_VERSION,
        formation_model=guarded,
    ).ingest(segment)
    assert retry.status == "completed"
    assert guarded.calls == 0
    event = backend.trg.graph_db.get_node(retry.memory_ids[0])
    assert set(event.attributes["mention_entity_surfaces"]) == {"李娜", "星河科技"}


def test_restart_reuses_mention_checkpoint_with_zero_provider_calls(tmp_path):
    span = "李娜加入了星河科技。"
    segment = _segment("mention-restart", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload(span, "李娜", "加入", "星河科技", [("u1", span)])],
        span_mentions={span: ["李娜", "星河科技"]},
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)

    def crash_before_magma_write(*args, **kwargs):
        raise RuntimeError("crash before MAGMA write")

    backend.add_event = crash_before_magma_write
    first = adapter.ingest(segment)
    assert first.status == "failed"
    assert model.extraction_calls == 1

    reloaded_backend = RealMagmaBackend(tmp_path / "magma")
    reloaded_state = IngestionStateStore(tmp_path / "magma-state.json")
    guarded = _MustNotBeCalledModel()
    restarted = MagmaMemoryAdapter(
        reloaded_backend,
        reloaded_state,
        ingestion_version=FORMATION_VERSION,
        formation_model=guarded,
    ).ingest(segment)
    assert restarted.status == "completed"
    assert guarded.calls == 0
    event = reloaded_backend.trg.graph_db.get_node(restarted.memory_ids[0])
    assert set(event.attributes["mention_entity_surfaces"]) == {"李娜", "星河科技"}
    assert set(event.attributes["mention_entity_refs"]) == {
        record["entity_ref"]
        for record in reloaded_state.get(
            reloaded_state.key(segment.segment_id, FORMATION_VERSION),
        )["mention_entity_bindings"]
    }


def test_corrupt_persisted_mention_bindings_fail_as_state_corrupt(tmp_path):
    span = "陈雨在北京工作。"
    segment = _segment("mention-corrupt", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload(span, "陈雨", "工作", "北京", [("u1", span)])],
        span_mentions={span: ["陈雨", "北京"]},
    )
    _backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    assert adapter.ingest(segment).status == "completed"
    key = state_store.key(segment.segment_id, FORMATION_VERSION)
    valid = state_store.get(key)
    good_record = valid["mention_entity_bindings"][0]

    def assert_corrupt(bindings):
        state_store.put(key, {**valid, "mention_entity_bindings": bindings})
        result = adapter.ingest(segment)
        assert result.status == "failed"
        assert not result.retryable
        assert result.safe_error_code == "state_corrupt"

    assert_corrupt([{
        name: value for name, value in good_record.items() if name != "turn_id"
    }])
    assert_corrupt([{**good_record, "canonical_surface": "不存在"}])
    assert_corrupt([{**good_record, "turn_id": "u9"}])
    assert_corrupt([{**good_record, "supporting_span": "别的跨度。"}])

    state_store.put(key, valid)
    restored = adapter.ingest(segment)
    assert restored.status == "completed"
    assert restored.already_ingested


def test_unbound_mention_create_ref_is_deterministic_across_runs(tmp_path):
    from adapter.entity_consolidation import stable_mention_entity_ref

    span = "陈雨在北京工作。"

    def run(dirname):
        segment = _segment("mention-determinism", ("u1", "user", span))
        model = _FakeFormationModel(
            [_unit_payload(span, "陈雨", "工作", "北京", [("u1", span)])],
            span_mentions={span: ["陈雨", "北京"]},
        )
        _backend, state_store, adapter = _ingest_with_model(
            tmp_path, segment, model, dirname=dirname,
        )
        assert adapter.ingest(segment).status == "completed"
        return state_store.get(
            state_store.key("mention-determinism", FORMATION_VERSION),
        )

    first = run("magma-a")
    second = run("magma-b")
    assert first["unit_ids"] == second["unit_ids"]
    unit_id = first["unit_ids"][0]
    subject_ref = first["entity_bindings"][0]["entity_ref"]
    expected = {
        # mention equal to the subject surface reuses the subject ref
        ("陈雨", subject_ref),
        # unbound non-subject mention keeps the stable CREATE seed
        ("北京", stable_mention_entity_ref(unit_id, "北京")),
    }
    for state in (first, second):
        assert {
            (record["canonical_surface"], record["entity_ref"])
            for record in state["mention_entity_bindings"]
        } == expected


# --- Slice 1 defect fix: a mention equal to the SAME unit's bound subject
# surface reuses the subject EntityRef; no second ref per entity.

def test_mention_equal_to_subject_surface_reuses_subject_ref(tmp_path):
    span = "小林开了家公司。"
    segment = _segment("mention-subject-dedup", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload(span, "小林", "开了", "家公司", [("u1", span)])],
        span_mentions={span: ["小林"]},
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    result = adapter.ingest(segment)

    assert result.status == "completed"
    state = state_store.get(state_store.key(segment.segment_id, FORMATION_VERSION))
    subject_ref = state["entity_bindings"][0]["entity_ref"]
    bindings = state["mention_entity_bindings"]
    assert len(bindings) == 1
    assert bindings[0]["canonical_surface"] == "小林"
    assert bindings[0]["entity_ref"] == subject_ref

    event = backend.trg.graph_db.get_node(result.memory_ids[0])
    assert event.attributes["subject_entity_ref"] == subject_ref
    assert event.attributes["mention_entity_refs"] == [subject_ref]
    # exactly one ref exists for the surface: one EntityNode, one subject edge
    assert [node.attributes["entity_ref"] for node in _entity_nodes(backend)] == [subject_ref]
    assert len(_refers_to_links(backend)) == 1


def test_current_user_mention_reuses_e001_without_a_second_user_ref(tmp_path):
    span = "我叫林岚，来自东海大学物理学院"
    segment = _segment("mention-user-dedup", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload(
            "林岚 来自东海大学物理学院", "林岚", "来自", "东海大学物理学院",
            [("u1", span)],
        )],
        span_mentions={span: ["林岚", "东海大学物理学院"]},
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    result = adapter.ingest(segment)

    assert result.status == "completed"
    bindings = _mention_state(state_store, segment.segment_id)
    assert len(bindings) == 2
    by_surface = {record["canonical_surface"]: record["entity_ref"] for record in bindings}
    assert by_surface["林岚"] == CURRENT_USER_ENTITY_REF
    assert by_surface["东海大学物理学院"].startswith("E_")
    assert by_surface["东海大学物理学院"] != CURRENT_USER_ENTITY_REF

    event = backend.trg.graph_db.get_node(result.memory_ids[0])
    assert event.attributes["subject_entity_ref"] == CURRENT_USER_ENTITY_REF
    assert set(event.attributes["mention_entity_refs"]) == set(by_surface.values())
    # no second user entity ref anywhere: the user still has exactly one
    # graph-only node and one subject edge; the non-user mention gets its
    # own node plus one generic role-less REFERS_TO edge (Slice 2).
    school_node_id = f"entity:{by_surface['东海大学物理学院'].casefold()}"
    assert [node.node_id for node in _entity_nodes(backend)] == [
        _ENTITY_NODE_ID, school_node_id,
    ]
    links = _refers_to_links(backend)
    assert len(links) == 2
    generic = [link for link in links if "role" not in link.properties]
    assert [link.target_node_id for link in generic] == [school_node_id]


def test_subject_mention_reuses_subject_ref_while_other_mentions_bind_normally(tmp_path):
    span = "小林的导师是王老师。"
    segment = _segment("mention-mixed-dedup", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload(span, "小林", "导师", "王老师", [("u1", span)])],
        span_mentions={span: ["小林", "王老师"]},
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    result = adapter.ingest(segment)

    assert result.status == "completed"
    state = state_store.get(state_store.key(segment.segment_id, FORMATION_VERSION))
    subject_ref = state["entity_bindings"][0]["entity_ref"]
    by_surface = {
        record["canonical_surface"]: record["entity_ref"]
        for record in state["mention_entity_bindings"]
    }
    assert by_surface["小林"] == subject_ref
    # 王老师 is not the subject surface: normal deterministic CREATE
    from adapter.entity_consolidation import stable_mention_entity_ref

    unit_id = state["unit_ids"][0]
    assert by_surface["王老师"] == stable_mention_entity_ref(unit_id, "王老师")
    assert by_surface["王老师"] != subject_ref
    event = backend.trg.graph_db.get_node(result.memory_ids[0])
    assert set(event.attributes["mention_entity_refs"]) == {
        subject_ref, by_surface["王老师"],
    }


def test_mention_subject_ref_reuse_survives_restart_without_provider_calls(tmp_path):
    span = "小林开了家公司。"
    segment = _segment("mention-dedup-restart", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload(span, "小林", "开了", "家公司", [("u1", span)])],
        span_mentions={span: ["小林"]},
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)

    def crash_before_magma_write(*args, **kwargs):
        raise RuntimeError("crash before MAGMA write")

    backend.add_event = crash_before_magma_write
    first = adapter.ingest(segment)
    assert first.status == "failed"
    key = state_store.key(segment.segment_id, FORMATION_VERSION)
    first_state = state_store.get(key)
    assert (
        first_state["mention_entity_bindings"][0]["entity_ref"]
        == first_state["entity_bindings"][0]["entity_ref"]
    )

    reloaded_backend = RealMagmaBackend(tmp_path / "magma")
    reloaded_state = IngestionStateStore(tmp_path / "magma-state.json")
    guarded = _MustNotBeCalledModel()
    restarted = MagmaMemoryAdapter(
        reloaded_backend,
        reloaded_state,
        ingestion_version=FORMATION_VERSION,
        formation_model=guarded,
    ).ingest(segment)
    assert restarted.status == "completed"
    assert guarded.calls == 0
    restarted_state = reloaded_state.get(key)
    assert (
        restarted_state["mention_entity_bindings"]
        == first_state["mention_entity_bindings"]
    )
    assert (
        restarted_state["entity_bindings"] == first_state["entity_bindings"]
    )
    event = reloaded_backend.trg.graph_db.get_node(restarted.memory_ids[0])
    assert event.attributes["mention_entity_refs"] == [
        event.attributes["subject_entity_ref"]
    ]


# --- Production Slice 2: event-centric multi-entity REFERS_TO --------------
# Generic (role-less) mention edges consumed from mention_entity_refs metadata.
# Shadow evidence: docs/experiments/multi_entity_recall_gain/RESULT_CROWDED.md.

def _generic_refers_to_links(backend):
    from memory.graph_db import LinkSubType, LinkType

    return [
        link
        for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.ENTITY
        and link.properties.get("sub_type") == LinkSubType.REFERS_TO.value
        and "role" not in link.properties
    ]


def test_mention_ref_creates_generic_refers_to_edge_and_node(tmp_path):
    span = "小林的导师是王老师。"
    segment = _segment("mention-edge", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload(span, "小林", "导师", "王老师", [("u1", span)])],
        span_mentions={span: ["小林", "王老师"]},
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    result = adapter.ingest(segment)
    assert result.status == "completed"

    event = backend.trg.graph_db.get_node(result.memory_ids[0])
    subject_ref = event.attributes["subject_entity_ref"]
    refs = event.attributes["mention_entity_refs"]
    surfaces = event.attributes["mention_entity_surfaces"]
    wang_ref = refs[surfaces.index("王老师")]
    assert wang_ref != subject_ref  # 小林 was deduped onto the subject ref

    nodes = {node.node_id: node for node in _entity_nodes(backend)}
    subject_node_id = f"entity:{subject_ref.casefold()}"
    wang_node_id = f"entity:{wang_ref.casefold()}"
    assert set(nodes) == {subject_node_id, wang_node_id}
    assert nodes[wang_node_id].attributes == {
        "entity_ref": wang_ref,
        "canonical_surface": "王老师",
    }
    # graph-only: the mention EntityNode is never vector-indexed
    assert wang_node_id not in backend.trg.vector_db.id_to_index

    refers_to = _refers_to_links(backend)
    assert len(refers_to) == 2
    subject_edges = [
        link for link in refers_to if link.properties.get("role") == "subject"
    ]
    generic_edges = _generic_refers_to_links(backend)
    assert [
        (link.source_node_id, link.target_node_id) for link in subject_edges
    ] == [(result.memory_ids[0], subject_node_id)]
    assert [
        (link.source_node_id, link.target_node_id) for link in generic_edges
    ] == [(result.memory_ids[0], wang_node_id)]
    from memory.graph_db import LinkSubType

    assert generic_edges[0].properties == {"sub_type": LinkSubType.REFERS_TO.value}


def test_subject_surface_mention_adds_no_duplicate_generic_edge(tmp_path):
    span = "小林开了家公司。"
    segment = _segment("mention-no-dup-edge", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload(span, "小林", "开了", "家公司", [("u1", span)])],
        span_mentions={span: ["小林"]},
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    result = adapter.ingest(segment)
    assert result.status == "completed"

    event = backend.trg.graph_db.get_node(result.memory_ids[0])
    # dedup reuse: the only mention ref IS the subject ref
    assert event.attributes["mention_entity_refs"] == [
        event.attributes["subject_entity_ref"]
    ]
    # the (event, entity) pair is covered by the subject edge: no generic edge
    assert _generic_refers_to_links(backend) == []
    assert len(_refers_to_links(backend)) == 1
    assert len(_entity_nodes(backend)) == 1


def test_non_entity_values_never_produce_entity_nodes(tmp_path):
    age_span = "小林今年23岁。"
    age_segment = _segment("mention-age", ("u1", "user", age_span))
    age_model = _FakeFormationModel(
        [_unit_payload(age_span, "小林", "今年", "23岁", [("u1", age_span)])],
        span_mentions={age_span: ["小林"]},  # the extractor never emits 23
    )
    backend, state_store, adapter = _ingest_with_model(
        tmp_path, age_segment, age_model, dirname="magma-age",
    )
    age_result = adapter.ingest(age_segment)
    assert age_result.status == "completed"
    age_event = backend.trg.graph_db.get_node(age_result.memory_ids[0])
    assert age_event.attributes["value"] == "23岁"
    age_nodes = _entity_nodes(backend)
    assert [node.attributes["canonical_surface"] for node in age_nodes] == ["小林"]
    assert all("23" not in node.attributes["canonical_surface"] for node in age_nodes)
    assert _generic_refers_to_links(backend) == []

    code_span = "我的项目编号是 PRJ-204。"
    code_segment = _segment("mention-code", ("u1", "user", code_span))
    code_model = _FakeFormationModel(
        [_unit_payload(code_span, "我", "编号", "PRJ-204", [("u1", code_span)])],
        span_mentions={code_span: []},  # codes are not entity mentions
    )
    code_backend, code_state, code_adapter = _ingest_with_model(
        tmp_path, code_segment, code_model, dirname="magma-code",
    )
    code_result = code_adapter.ingest(code_segment)
    assert code_result.status == "completed"
    assert _mention_state(code_state, "mention-code") == []
    code_event = code_backend.trg.graph_db.get_node(code_result.memory_ids[0])
    assert code_event.attributes["value"] == "PRJ-204"
    # only the current-user node exists; no node for the code
    assert [node.node_id for node in _entity_nodes(code_backend)] == [_ENTITY_NODE_ID]
    assert _generic_refers_to_links(code_backend) == []


def test_mention_edges_stable_across_persist_reload_and_retry(tmp_path):
    span = "小林的导师是王老师。"
    segment = _segment("mention-edge-retry", ("u1", "user", span))
    model = _FakeFormationModel(
        [_unit_payload(span, "小林", "导师", "王老师", [("u1", span)])],
        span_mentions={span: ["小林", "王老师"]},
    )
    backend, state_store, adapter = _ingest_with_model(tmp_path, segment, model)
    result = adapter.ingest(segment)
    assert result.status == "completed"
    assert len(_entity_nodes(backend)) == 2
    assert len(_refers_to_links(backend)) == 2

    # ingestion retry: relationship step runs again on the same ids
    backend.create_relationships(list(result.memory_ids))
    backend.persist()
    reloaded = RealMagmaBackend(tmp_path / "magma")
    assert len(_entity_nodes(reloaded)) == 2
    assert len(_refers_to_links(reloaded)) == 2
    assert len(_generic_refers_to_links(reloaded)) == 1

    reloaded.create_relationships(list(result.memory_ids))
    reloaded.persist()
    reloaded_again = RealMagmaBackend(tmp_path / "magma")
    assert len(_entity_nodes(reloaded_again)) == 2
    assert len(_refers_to_links(reloaded_again)) == 2
    assert len(_generic_refers_to_links(reloaded_again)) == 1
