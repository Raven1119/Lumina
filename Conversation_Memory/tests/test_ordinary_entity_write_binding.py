from __future__ import annotations

import json
from datetime import UTC, datetime

from Conversation_Memory.adapter.backend import RealMagmaBackend
from Conversation_Memory.adapter.entity_consolidation import (
    EntityCandidate,
    resolve_entity_binding,
)
from Conversation_Memory.adapter.grounded_formation import (
    FORMATION_VERSION,
    FormationModel,
    GroundedMemoryUnit,
    SourceRef,
)
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter
from Conversation_Memory.adapter.models import ColdDraftSegment, ColdDraftTurn
from Conversation_Memory.ingestion.state_store import IngestionStateStore


class _ScriptedModel(FormationModel):
    client_kind = "model"

    def __init__(self, responses: list[dict]) -> None:
        self._responses = iter(responses)
        self.calls = 0
        self.mention_calls = 0
        self.messages = []

    def generate(self, recent_context, user_message, *, system_prompt):
        if system_prompt.startswith((
            "Extract only entity mentions",
            "Select from the Candidate mentions",
        )):
            # mention extraction/selection calls (Production Slice 1) are not
            # part of the scripted formation responses; answer with no
            # mentions and keep the formation call/message counters intact
            self.mention_calls += 1
            return json.dumps({"entities": []}, ensure_ascii=False)
        self.calls += 1
        self.messages.append(json.loads(user_message))
        response = next(self._responses)
        if callable(response):
            response = response(self.messages[-1])
        return json.dumps(response, ensure_ascii=False)


def _segment(segment_id: str, *contents: str) -> ColdDraftSegment:
    return ColdDraftSegment(
        segment_id=segment_id,
        conversation_id="ordinary-entity-test",
        state="pending_digest",
        schema_version="2",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        source_timezone="UTC",
        turns=tuple(
            ColdDraftTurn(
                turn_id=f"u{index}",
                role="user",
                content=content,
                timestamp=datetime(2026, 8, index, tzinfo=UTC),
                source_timezone="UTC",
                timezone_source="client",
            )
            for index, content in enumerate(contents, start=1)
        ),
    )


def _unit(
    text: str,
    relation: str,
    value: str,
    turn_id: str,
    *,
    subject: str = "小林",
) -> dict:
    return {
        "text": text,
        "subject": subject,
        "relation": relation,
        "value": value,
        "source_refs": [{"turn_id": turn_id, "supporting_span": text}],
        "referenced_time": None,
    }


def _entity_nodes(backend):
    from memory.graph_db import NodeType

    return [
        node for node in backend.trg.graph_db.nodes.values()
        if node.node_type == NodeType.ENTITY
    ]


def _subject_refers_to_links(backend):
    from memory.graph_db import LinkSubType, LinkType

    return [
        link for link in backend.trg.graph_db.links.values()
        if link.link_type == LinkType.ENTITY
        and link.properties.get("sub_type") == LinkSubType.REFERS_TO.value
        and link.properties.get("role") == "subject"
    ]


def test_exact_surface_binding_reuses_unique_candidate_creates_new_and_fails_open_on_ambiguity():
    unit = GroundedMemoryUnit(
        "ordinary-surface", "王老师推荐小林加入项目。", "王老师", "推荐", "小林",
        (SourceRef("u1", "王老师推荐小林加入项目。"),),
    )

    created = resolve_entity_binding(unit, ())
    distinct_from_xiaolin = resolve_entity_binding(
        unit,
        (EntityCandidate("E_XIAOLIN", "小林"),),
    )
    reused = resolve_entity_binding(
        unit,
        (EntityCandidate(created.entity_ref, "王老师"),),
    )
    tlhey_unit = GroundedMemoryUnit(
        "tlhey-surface", "Tlhey lives in Shanghai.", "Tlhey", "lives in", "Shanghai",
        (SourceRef("u2", "Tlhey lives in Shanghai."),),
    )
    tlhey_created = resolve_entity_binding(tlhey_unit, ())
    tlhey_reused = resolve_entity_binding(
        tlhey_unit,
        (EntityCandidate(tlhey_created.entity_ref, "Tlhey"),),
    )
    alex_unit = GroundedMemoryUnit(
        "alex-surface", "Alex joined the project.", "Alex", "joined", "the project",
        (SourceRef("u3", "Alex joined the project."),),
    )
    ambiguous = resolve_entity_binding(
        alex_unit,
        (
            EntityCandidate("E_ALEX_1", "Alex"),
            EntityCandidate("E_ALEX_2", "Alex"),
        ),
    )

    assert created.canonical_surface == "王老师"
    assert distinct_from_xiaolin is not None
    assert distinct_from_xiaolin.entity_ref != "E_XIAOLIN"
    assert reused.entity_ref == created.entity_ref
    assert tlhey_reused.entity_ref == tlhey_created.entity_ref
    assert ambiguous is None


def test_two_units_in_one_segment_create_then_reuse_one_ordinary_entity(tmp_path):
    first = "小林在东海大学学习。"
    second = "小林喜欢天文。"
    model = _ScriptedModel([
        {"units": [
            _unit(first, "在", "东海大学学习", "u1"),
            _unit(second, "喜欢", "天文", "u2"),
        ]},
    ])
    backend = RealMagmaBackend(tmp_path / "magma")
    state_store = IngestionStateStore(tmp_path / "state.json")
    adapter = MagmaMemoryAdapter(
        backend,
        state_store,
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    )

    result = adapter.ingest(_segment("ordinary-two-units", first, second))

    assert result.status == "completed"
    assert model.calls == 1
    events = [backend.trg.graph_db.get_node(memory_id) for memory_id in result.memory_ids]
    refs = [event.attributes["subject_entity_ref"] for event in events]
    assert refs[0].startswith("E_")
    assert refs == [refs[0], refs[0]]
    assert len(model.messages) == 1
    assert [node.attributes for node in _entity_nodes(backend)] == [{
        "entity_ref": refs[0], "canonical_surface": "小林",
    }]
    assert len(_subject_refers_to_links(backend)) == 2
    state = state_store.get(state_store.key("ordinary-two-units", FORMATION_VERSION))
    assert state["entity_bindings"] == [
        {"unit_id": unit_id, "entity_ref": refs[0], "canonical_surface": "小林"}
        for unit_id in state["unit_ids"]
    ]


class _NoCallsModel(FormationModel):
    client_kind = "model"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls += 1
        raise AssertionError("restart must reuse persisted formation and entity bindings")


def _two_unit_model(first: str, second: str) -> _ScriptedModel:
    return _ScriptedModel([
        {"units": [
            _unit(first, "在", "东海大学学习", "u1"),
            _unit(second, "喜欢", "天文", "u2"),
        ]},
    ])


def test_restart_after_checkpoint_reuses_entity_bindings_without_model_calls(tmp_path):
    first = "小林在东海大学学习。"
    second = "小林喜欢天文。"
    segment = _segment("ordinary-checkpoint-crash", first, second)
    model = _two_unit_model(first, second)
    backend = RealMagmaBackend(tmp_path / "magma")
    state_store = IngestionStateStore(tmp_path / "state.json")
    original_add_event = backend.add_event

    def crash_before_magma_write(*args, **kwargs):
        raise RuntimeError("crash before MAGMA write")

    backend.add_event = crash_before_magma_write
    first_attempt = MagmaMemoryAdapter(
        backend, state_store, ingestion_version=FORMATION_VERSION,
        formation_model=model,
    ).ingest(segment)
    assert first_attempt.status == "failed"
    assert model.calls == 1
    checkpoint = state_store.get(state_store.key(segment.segment_id, FORMATION_VERSION))
    assert len(checkpoint["entity_bindings"]) == 2

    backend.add_event = original_add_event
    restarted_model = _NoCallsModel()
    restarted = MagmaMemoryAdapter(
        backend, state_store, ingestion_version=FORMATION_VERSION,
        formation_model=restarted_model,
    ).ingest(segment)
    assert restarted.status == "completed"
    assert restarted_model.calls == 0
    assert len(_entity_nodes(backend)) == 1
    assert len(_subject_refers_to_links(backend)) == 2


def test_restart_after_partial_magma_write_converges_without_duplicate_entity(tmp_path):
    first = "小林在东海大学学习。"
    second = "小林喜欢天文。"
    segment = _segment("ordinary-partial-write", first, second)
    model = _two_unit_model(first, second)
    persist_dir = tmp_path / "magma"
    state_path = tmp_path / "state.json"
    backend = RealMagmaBackend(persist_dir)
    original_add_event = backend.add_event
    writes = 0

    def crash_after_one_event(*args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("crash after first MAGMA event")
        return original_add_event(*args, **kwargs)

    backend.add_event = crash_after_one_event
    first_attempt = MagmaMemoryAdapter(
        backend, IngestionStateStore(state_path),
        ingestion_version=FORMATION_VERSION, formation_model=model,
    ).ingest(segment)
    assert first_attempt.status == "failed"
    assert len(first_attempt.memory_ids) == 1

    reloaded_backend = RealMagmaBackend(persist_dir)
    restarted_model = _NoCallsModel()
    restarted = MagmaMemoryAdapter(
        reloaded_backend, IngestionStateStore(state_path),
        ingestion_version=FORMATION_VERSION, formation_model=restarted_model,
    ).ingest(segment)
    assert restarted.status == "completed"
    assert restarted_model.calls == 0
    assert len(_entity_nodes(reloaded_backend)) == 1
    assert len(_subject_refers_to_links(reloaded_backend)) == 2


def test_persisted_ordinary_entity_is_a_bounded_reuse_candidate(tmp_path):
    first = "小林在东海大学学习。"
    second = "小林喜欢天文。"
    backend = RealMagmaBackend(tmp_path / "magma")
    state_store = IngestionStateStore(tmp_path / "state.json")
    created = MagmaMemoryAdapter(
        backend, state_store, ingestion_version=FORMATION_VERSION,
        formation_model=_ScriptedModel([
            {"units": [_unit(first, "在", "东海大学学习", "u1")]},
        ]),
    ).ingest(_segment("ordinary-create", first))
    created_ref = backend.trg.graph_db.get_node(
        created.memory_ids[0],
    ).attributes["subject_entity_ref"]
    model = _ScriptedModel([
        {"units": [_unit(second, "喜欢", "天文", "u1")]},
    ])
    result = MagmaMemoryAdapter(
        backend, state_store, ingestion_version=FORMATION_VERSION,
        formation_model=model,
    ).ingest(_segment("ordinary-reuse", second))

    assert result.status == "completed"
    assert model.calls == 1
    assert backend.trg.graph_db.get_node(
        result.memory_ids[0],
    ).attributes["subject_entity_ref"] == created_ref


def test_current_user_e001_skips_ordinary_entity_resolver(tmp_path):
    text = "我住在上海。"
    model = _ScriptedModel([
        {"units": [_unit(text, "住在", "上海", "u1", subject="我")]},
    ])
    backend = RealMagmaBackend(tmp_path / "magma")
    result = MagmaMemoryAdapter(
        backend, IngestionStateStore(tmp_path / "state.json"),
        ingestion_version=FORMATION_VERSION, formation_model=model,
    ).ingest(_segment("current-user-bypass", text))

    assert result.status == "completed"
    assert model.calls == 1
    assert backend.trg.graph_db.get_node(
        result.memory_ids[0],
    ).attributes["subject_entity_ref"] == "E_001"
