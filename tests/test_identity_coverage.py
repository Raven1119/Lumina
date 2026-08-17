"""Identity coverage guard: deterministic post-Formation self-identity fill-in.

The guard runs inside ``form_grounded_memory_units`` after model-call
validation: when Formation omitted an explicit user self-identification, the
guard deterministically constructs ONE source-grounded identity unit from the
raw source evidence and runs it through the UNCHANGED strict validator. It
never calls an LLM, never triggers the semantic fallback, never fires on
non-self-identity content, and never duplicates an equivalent existing unit.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = ROOT / "Conversation_Memory"
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from adapter.grounded_formation import (  # noqa: E402
    FORMATION_VERSION,
    form_grounded_memory_units,
    serialize_grounded_memory_units,
    deserialize_grounded_memory_units,
    validate_persisted_grounded_memory_units,
)
from adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from adapter.models import ColdDraftSegment, ColdDraftTurn  # noqa: E402
from adapter.user_self import (  # noqa: E402
    CURRENT_USER_ENTITY_REF,
    classify_subject_entity_ref,
)
from ingestion.state_store import IngestionStateStore  # noqa: E402


class FakeFormationModel:
    """Formation returns fixed candidates; fallback (if reached) supports all."""

    def __init__(self, units: list[dict[str, object]]) -> None:
        self.units = units
        self.calls = 0

    def generate(self, recent_context, user_message, *, system_prompt):
        assert recent_context == []
        if system_prompt.startswith((
            "Extract only entity mentions",
            "Select from the Candidate mentions",
        )):
            return json.dumps({"entities": []}, ensure_ascii=False)
        self.calls += 1
        if "source-grounded atomic facts" in system_prompt:
            return json.dumps({"units": self.units}, ensure_ascii=False)
        assert "Given ONLY each candidate" in system_prompt
        candidates = json.loads(user_message)["candidates"]
        return json.dumps({
            "results": [{"supported": True} for _ in candidates]
        })


def _segment(*turns: tuple[str, str, str]) -> ColdDraftSegment:
    return ColdDraftSegment(
        segment_id="identity-segment",
        conversation_id="identity-conversation",
        state="pending_digest",
        turns=tuple(
            ColdDraftTurn(
                turn_id=turn_id,
                role=role,
                content=text,
                timestamp=datetime(2026, 8, 17, index, tzinfo=UTC),
                source_timezone="Asia/Shanghai",
                timezone_source="configured_default",
            )
            for index, (turn_id, role, text) in enumerate(turns)
        ),
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        source_timezone="Asia/Shanghai",
        schema_version="2",
    )


def _unit(text, subject, relation, value, refs, referenced_time=None):
    return {
        "text": text,
        "subject": subject,
        "relation": relation,
        "value": value,
        "source_refs": [
            {"turn_id": turn_id, "supporting_span": span}
            for turn_id, span in refs
        ],
        "referenced_time": referenced_time,
    }


POSITIVE_CASES = [
    ("我叫 Raven。", "叫", "Raven", "我叫 Raven。"),
    ("我的名字是 Raven。", "名字是", "Raven", "我的名字是 Raven。"),
    ("你可以叫我 Raven。", "叫", "Raven", "你可以叫我 Raven。"),
    ("我叫测试员。", "叫", "测试员", "我叫测试员。"),
]

NEGATIVE_CASES = [
    "Raven 是我同学。",
    "我认识 Raven。",
    "Raven 叫小林。",
    "“Raven”只是项目名。",
]

GENERIC_SHI_CASES = [
    "我是学生。",
    "我是化学系大二学生。",
    "我是项目负责人。",
]


@pytest.mark.parametrize("text", GENERIC_SHI_CASES)
def test_generic_wo_shi_expressions_do_not_fire(text):
    """Generic "我是 X" (role/state) is not a validated name/appellation
    self-identification; the guard must not fire."""
    segment = _segment(("u1", "user", text))
    model = FakeFormationModel([])
    units = form_grounded_memory_units(segment, model)
    assert units == ()
    assert model.calls == 1


@pytest.mark.parametrize("text,relation,name,span", POSITIVE_CASES)
def test_omitted_self_identity_is_covered_without_extra_llm_call(
    text, relation, name, span,
):
    segment = _segment(("u1", "user", text))
    model = FakeFormationModel([])  # Formation omits the fact entirely
    semantic_unit_ids: set[str] = set()
    units = form_grounded_memory_units(
        segment, model, semantic_unit_ids=semantic_unit_ids,
    )
    assert model.calls == 1  # guard adds no provider call, triggers no fallback
    assert len(units) == 1
    unit = units[0]
    assert (unit.subject, unit.relation, unit.value) == ("我", relation, name)
    assert unit.text == span
    assert unit.formation_version == FORMATION_VERSION
    assert unit.id.startswith("grounded_memory_v1:")
    # exact-span grounding of the guard value
    assert unit.value in segment.turns[0].content
    assert len(unit.source_refs) == 1
    assert unit.source_refs[0].turn_id == "u1"
    assert segment.turns[0].content.count(unit.source_refs[0].supporting_span) == 1
    # guard units are strict-path units, never semantic-fallback units
    assert semantic_unit_ids == set()


def test_multi_sentence_turn_uses_enclosing_sentence_span():
    segment = _segment(("u1", "user", "你好呀。我叫 Raven。请多关照。"))
    units = form_grounded_memory_units(segment, FakeFormationModel([]))
    assert len(units) == 1
    assert units[0].source_refs[0].supporting_span == "我叫 Raven。"
    assert units[0].value == "Raven"


def test_guard_unit_binds_current_user_ref():
    segment = _segment(("u1", "user", "我叫 Raven。"))
    units = form_grounded_memory_units(segment, FakeFormationModel([]))
    assert len(units) == 1
    assert classify_subject_entity_ref(units[0], segment) == CURRENT_USER_ENTITY_REF


def test_assistant_self_identification_does_not_fire():
    segment = _segment(("a1", "assistant", "我叫 Raven。"))
    units = form_grounded_memory_units(segment, FakeFormationModel([]))
    assert units == ()


@pytest.mark.parametrize("text", NEGATIVE_CASES)
def test_negatives_never_fire(text):
    segment = _segment(("u1", "user", text))
    model = FakeFormationModel([])
    units = form_grounded_memory_units(segment, model)
    assert units == ()
    assert model.calls == 1


def test_negative_ordinary_formation_fact_passes_through_untouched():
    text = "Raven 叫小林。"
    segment = _segment(("u1", "user", text))
    ordinary = _unit(text, "Raven", "叫", "小林", [("u1", text)])
    model = FakeFormationModel([ordinary])
    units = form_grounded_memory_units(segment, model)
    assert len(units) == 1
    assert (units[0].subject, units[0].relation, units[0].value) == (
        "Raven", "叫", "小林",
    )


def test_no_duplicate_when_formation_covered_strict_shape():
    text = "我叫 Raven。"
    segment = _segment(("u1", "user", text))
    existing = _unit(text, "我", "叫", "Raven", [("u1", text)])
    model = FakeFormationModel([existing])
    units = form_grounded_memory_units(segment, model)
    assert len(units) == 1
    assert units[0].subject == "我"


def test_no_duplicate_when_formation_covered_subject_is_name():
    text = "我叫 Raven。"
    segment = _segment(("u1", "user", text))
    existing = _unit(text, "Raven", "叫", "Raven", [("u1", text)])
    units = form_grounded_memory_units(segment, FakeFormationModel([existing]))
    assert len(units) == 1
    assert units[0].subject == "Raven"


@pytest.mark.parametrize("subject", ["用户", "user", "the user", "user (Raven)"])
def test_no_duplicate_for_formation_user_surface_variants(subject):
    """Fallback-accepted Formation units with speaker-normalized user surfaces
    — including the observed ``user (Raven)`` variant — already express the
    self-identity fact; the guard must add nothing."""
    text = "我叫 Raven。"
    segment = _segment(("u1", "user", text))
    existing = _unit(
        "The user's name is Raven.", subject, "name", "Raven",
        [("u1", "我叫 Raven")],
    )
    model = FakeFormationModel([existing])  # accepted via semantic fallback
    units = form_grounded_memory_units(segment, model)
    assert len(units) == 1
    assert units[0].subject == subject


def test_guard_still_adds_when_fallback_rejected_formation_candidate():
    text = "我叫 Raven。"
    segment = _segment(("u1", "user", text))
    bogus = _unit(
        "The user's name is Raven.", "user", "name", "Raven",
        [("u1", "我叫 Raven")],
    )

    class RejectingModel(FakeFormationModel):
        def generate(self, recent_context, user_message, *, system_prompt):
            if "Given ONLY each candidate" in system_prompt:
                self.calls += 1
                return json.dumps({"results": [{"supported": False}]})
            return super().generate(
                recent_context, user_message, system_prompt=system_prompt,
            )

    model = RejectingModel([bogus])
    units = form_grounded_memory_units(segment, model)
    assert model.calls == 2  # formation + one fallback; guard adds no call
    assert len(units) == 1
    assert (units[0].subject, units[0].relation, units[0].value) == (
        "我", "叫", "Raven",
    )


def test_guard_unit_id_deterministic_and_serde_round_trip():
    segment = _segment(("u1", "user", "我叫 Raven。"))
    first = form_grounded_memory_units(segment, FakeFormationModel([]))
    second = form_grounded_memory_units(segment, FakeFormationModel([]))
    assert [unit.id for unit in first] == [unit.id for unit in second]
    # production round-trips through the JSON state store (tuples -> lists)
    raw = json.loads(json.dumps(
        serialize_grounded_memory_units(first), ensure_ascii=False,
    ))
    restored = deserialize_grounded_memory_units(raw)
    assert restored == first
    # durable-reuse revalidation runs the strict path (no semantic ids)
    assert validate_persisted_grounded_memory_units(restored, segment)


class _RecordingBackend:
    def __init__(self):
        self.events = {}
        self.add_calls = 0

    def find_memory_id(self, evidence_id):
        for memory_id, event in self.events.items():
            if event["metadata"]["evidence_id"] == evidence_id:
                return memory_id
        return None

    def add_event(self, text, timestamp, metadata):
        self.add_calls += 1
        memory_id = f"memory-{len(self.events)}"
        self.events[memory_id] = {
            "text": text, "timestamp": timestamp, "metadata": metadata,
        }
        return memory_id

    def persist(self):
        return None

    def create_relationships(self, _memory_ids):
        return None

    def recall(self, *_args):
        return ()

    def resolve_target_entity_ref(self, _query):
        return None


def test_adapter_retry_reuses_guard_unit_checkpoint(tmp_path):
    segment = _segment(("u1", "user", "我叫 Raven。"))
    state_path = tmp_path / "state.json"
    backend = _RecordingBackend()
    model = FakeFormationModel([])  # Formation omits; guard fills in
    first = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(state_path),
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    ).ingest(segment)
    assert first.status == "completed"
    assert model.calls == 1
    assert len(first.memory_ids) == 1
    assert backend.add_calls == 1

    state = IngestionStateStore(state_path).get(
        IngestionStateStore.key(segment.segment_id, FORMATION_VERSION)
    )
    assert state["semantic_unit_ids"] == []
    assert len(state["formed_units"]) == 1
    assert state["formed_units"][0]["subject"] == "我"
    assert state["formed_units"][0]["value"] == "Raven"

    class MustNotBeCalled:
        calls = 0

        def generate(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("retry must reuse the owned checkpoint")

    retry_model = MustNotBeCalled()
    second = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(state_path),
        ingestion_version=FORMATION_VERSION,
        formation_model=retry_model,
    ).ingest(segment)
    assert second.status == "completed"
    assert second.already_ingested
    assert second.memory_ids == first.memory_ids
    assert retry_model.calls == 0
    assert backend.add_calls == 1  # no duplicate MAGMA write
    event = backend.events[first.memory_ids[0]]
    assert event["metadata"]["subject"] == "我"
    assert event["metadata"]["value"] == "Raven"
    assert event["metadata"]["subject_entity_ref"] == CURRENT_USER_ENTITY_REF
