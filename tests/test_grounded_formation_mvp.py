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
    FormationError,
    form_grounded_memory_units,
)
from adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from adapter.models import ColdDraftSegment, ColdDraftTurn  # noqa: E402
from ingestion.state_store import IngestionStateStore  # noqa: E402


class FakeFormationModel:
    def __init__(self, units: list[dict[str, object]]) -> None:
        self.units = units
        self.calls = 0

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls += 1
        assert recent_context == []
        assert "source-grounded atomic facts" in system_prompt
        assert "turns" in json.loads(user_message)
        return json.dumps({"units": self.units}, ensure_ascii=False)


class FakeSemanticFormationModel:
    def __init__(self, units, decisions):
        self.units = units
        self.decisions = decisions
        self.calls = 0
        self.semantic_payload = None

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls += 1
        assert recent_context == []
        if self.calls == 1:
            assert "source-grounded atomic facts" in system_prompt
            return json.dumps({"units": self.units}, ensure_ascii=False)
        assert self.calls == 2
        assert "Given ONLY each candidate" in system_prompt
        self.semantic_payload = json.loads(user_message)
        return json.dumps({
            "results": [
                {"supported": supported}
                for supported in self.decisions
            ]
        })


def _segment(*turns: tuple[str, str, str]) -> ColdDraftSegment:
    return ColdDraftSegment(
        segment_id="formation-segment",
        conversation_id="formation-conversation",
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


def _supports_subject_relations(units, subject, relations):
    wanted_subject = subject.strip().casefold()
    available = {
        unit.relation.strip().casefold()
        for unit in units
        if unit.subject.strip().casefold() == wanted_subject
    }
    return bool(relations) and all(
        relation.strip().casefold() in available
        for relation in relations
    )


def test_frozen_validator_audit_recovers_four_and_preserves_two():
    audit = json.loads(
        (ROOT / "docs" / "FORMATION_VALIDATOR_AUDIT_CASES.json")
        .read_text(encoding="utf-8")
    )
    recovered = preserved = 0
    decisions_by_case = {
        "same_turn_split": [True, True],
        "multi_turn_grounding": [],
        "user_correction": [True, False],
        "assistant_contamination": [True],
    }
    for case in audit["cases"]:
        if case.get("transport_exhausted"):
            continue
        candidates = [item["candidate"] for item in case["candidates"]]
        expected = [
            item["semantic_audit"]["classification"]
            for item in case["candidates"]
        ]
        model = FakeSemanticFormationModel(
            candidates, decisions_by_case[case["case_id"]],
        )
        segment = _segment(*(
            (turn["turn_id"], turn["role"], turn["text"])
            for turn in case["source_turns"]
        ))
        units = form_grounded_memory_units(segment, model)
        accepted_texts = {unit.text for unit in units}
        for item, classification in zip(case["candidates"], expected):
            accepted = item["candidate"]["text"].strip() in accepted_texts
            if classification == "VALIDATOR_FALSE_REJECTION":
                recovered += int(accepted)
            elif classification == "VALIDATOR_CORRECT_REJECTION":
                preserved += int(not accepted)
    assert recovered == 4
    assert preserved == 2


def test_semantic_fallback_is_one_cited_only_batch_behind_hard_checks():
    sources = {
        "u1": "实验室打印机坏了",
        "u2": "Panel PW-875 current setting is Alpha",
        "u3": "我的门牌号码是5434",
        "u4": "Device DV-4 status is ready",
        "u5": "Panel PW-875 current setting to Beta",
        "u6": "Maybe port PT-2 is not set to 52.4 kilohertz",
        "u7": "Rack RK-1 voltage is 4.7 volts",
        "u8": "Rack RK-2 voltage is 4.7 volts",
        "u9": "First binding fragment",
    }
    candidates = [
        _unit("The lab printer is broken", "lab printer", "status", "broken", [("u1", sources["u1"])]),
        _unit("Panel PW-875 had setting Alpha", "Panel PW-875", "had setting", "Alpha", [("u2", sources["u2"])]),
        _unit("我的门牌号码是5434", "用户", "门牌号码是", "5434", [("u3", sources["u3"])]),
        _unit("Device WRONG status is ready", "Device WRONG", "status", "ready", [("u4", sources["u4"])]),
        _unit("Device DV-4 owner is ready", "Device DV-4", "owner", "ready", [("u4", sources["u4"])]),
        _unit("Panel PW-875 had setting changed to Beta", "Panel PW-875", "had setting changed to", "Beta", [("u5", sources["u5"])]),
        _unit("Device DV-4 status is stale", "Device DV-4", "status", "stale", [("u4", sources["u4"])]),
        _unit("Port PT-2 is set to 52.4 kilohertz", "PT-2", "setting", "52.4 kilohertz", [("u6", sources["u6"])]),
        _unit("Rack RK-1 voltage is 4.8 volts", "RK-1", "voltage", "4.8 volts", [("u7", sources["u7"])]),
        _unit("Rack RK-2 voltage is 4.7 amperes", "RK-2", "voltage", "4.7 amperes", [("u8", sources["u8"])]),
        _unit("Invented complete binding is First binding fragment", "Invented complete binding", "is", "First binding fragment", [("u9", sources["u9"]), ("u4", sources["u4"])]),
        _unit("Your secret code is BAD-9", "you", "secret code", "BAD-9", [("a1", "Your secret code is BAD-9")]),
    ]
    model = FakeSemanticFormationModel(
        candidates, [True, True, True, False, False, False],
    )
    units = form_grounded_memory_units(
        _segment(*[
            (turn_id, "user", source)
            for turn_id, source in sources.items()
        ], ("a1", "assistant", "Your secret code is BAD-9")),
        model,
    )
    assert model.calls == 2
    assert len(units) == 3
    assert {unit.value for unit in units} == {"broken", "Alpha", "5434"}
    payload = model.semantic_payload
    assert len(payload["candidates"]) == 6
    assert all(set(item) == {"candidate", "source_refs"} for item in payload["candidates"])
    assert all(
        set(item["candidate"]) == {"text", "subject", "relation", "value"}
        for item in payload["candidates"]
    )
    assert all(
        set(ref) == {"role", "supporting_span"}
        for item in payload["candidates"] for ref in item["source_refs"]
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "turn_id" not in serialized
    assert "referenced_time" not in serialized
    assert sources["u6"] not in serialized
    assert "BAD-9" not in serialized


def test_semantic_fallback_malformed_response_fails_closed_once():
    candidate = _unit(
        "The lab printer is broken", "lab printer", "status", "broken",
        [("u1", "实验室打印机坏了")],
    )
    class MalformedFallback(FakeSemanticFormationModel):
        def generate(self, recent_context, user_message, *, system_prompt):
            if self.calls == 0:
                self.calls += 1
                return json.dumps({"units": self.units}, ensure_ascii=False)
            self.calls += 1
            return '{"results":[]}'
    model = MalformedFallback([candidate], [])
    assert form_grounded_memory_units(
        _segment(("u1", "user", "实验室打印机坏了")), model,
    ) == ()
    assert model.calls == 2


def test_semantic_fallback_rejects_value_only_source_span():
    source = "上海。"
    candidate = _unit(
        source, "出差目的地", "是", "上海", [("u2", source)],
    )
    model = FakeSemanticFormationModel([candidate], [True])

    units = form_grounded_memory_units(
        _segment(
            ("u1", "user", "小林下周要出差。"),
            ("a1", "assistant", "去哪？"),
            ("u2", "user", source),
        ),
        model,
    )

    assert units == ()
    assert model.calls == 1
    assert model.semantic_payload is None


def test_semantic_fallback_rejects_ascii_value_only_source_span():
    source = "Alpha."
    candidate = _unit(
        source, "panel setting", "is", "Alpha", [("u1", source)],
    )
    model = FakeSemanticFormationModel([candidate], [True])

    assert form_grounded_memory_units(
        _segment(("u1", "user", source)), model,
    ) == ()
    assert model.calls == 1
    assert model.semantic_payload is None


def test_semantic_fallback_accepts_full_proposition_paraphrase():
    source = (
        "My access pass for AP-317 is TEAL-PASS-317 "
        "at 52.4 kilohertz."
    )
    candidate = _unit(
        (
            "User claims an access pass for AP-317 has the identifier "
            "TEAL-PASS-317 at 52.4 kilohertz."
        ),
        "access pass AP-317",
        "has identifier",
        "TEAL-PASS-317 at 52.4 kilohertz",
        [("u1", source)],
    )
    model = FakeSemanticFormationModel([candidate], [True])

    units = form_grounded_memory_units(
        _segment(("u1", "user", source)), model,
    )

    assert len(units) == 1
    assert units[0].value == "TEAL-PASS-317 at 52.4 kilohertz"
    assert model.calls == 2
    assert model.semantic_payload is not None


def test_semantic_fallback_accepts_exact_ap471_temporal_candidate():
    audit = json.loads(
        (ROOT / "docs" / "EXACT_9_VALIDATOR_REJECTION_AUDIT_CASES.json")
        .read_text(encoding="utf-8")
    )
    case = next(
        item for item in audit["cases"]
        if item["turn_id"] == "r3c13"
    )
    candidate = case["candidate"]
    source_ref = candidate["source_refs"][0]
    assert (
        candidate["subject"],
        candidate["relation"],
        candidate["value"],
        candidate["referenced_time"],
        source_ref,
    ) == (
        "AP-471",
        "aperture_setting",
        "f/4.8",
        "earlier",
        {
            "turn_id": "r3c13",
            "supporting_span": "Aperture unit AP-471 earlier used f/4.8.",
        },
    )
    model = FakeSemanticFormationModel([candidate], [True])

    units = form_grounded_memory_units(
        _segment(("r3c13", "user", source_ref["supporting_span"])),
        model,
    )

    assert len(units) == 1
    assert units[0].referenced_time == "earlier"
    assert model.calls == 2
    assert model.semantic_payload is not None


def test_semantic_unit_checkpoint_retry_never_calls_model_again(tmp_path):
    source = "我今晚早点睡"
    candidate = _unit(
        "I will sleep early tonight", "I", "plan", "sleep early tonight",
        [("u1", source)], "tonight",
    )
    first_model = FakeSemanticFormationModel([candidate], [True])
    segment = _segment(("u1", "user", source))
    state_path = tmp_path / "state.json"

    class FailOnceBackend:
        def __init__(self):
            self.fail_once = True
            self.events = {}

        def find_memory_id(self, evidence_id):
            for memory_id, event in self.events.items():
                if event["metadata"]["evidence_id"] == evidence_id:
                    return memory_id
            return None

        def add_event(self, text, timestamp, metadata):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("synthetic backend failure")
            self.events["memory-1"] = {
                "text": text, "timestamp": timestamp, "metadata": metadata,
            }
            return "memory-1"

        def persist(self):
            return None

        def create_relationships(self, _memory_ids):
            return None

        def recall(self, *_args):
            return ()

    backend = FailOnceBackend()
    first = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(state_path),
        ingestion_version=FORMATION_VERSION,
        formation_model=first_model,
    ).ingest(segment)
    assert first.status == "failed"
    assert first_model.calls == 2
    state = IngestionStateStore(state_path).get(
        IngestionStateStore.key(segment.segment_id, FORMATION_VERSION)
    )
    assert state["semantic_unit_ids"] == state["unit_ids"]

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
    assert retry_model.calls == 0
    assert len(backend.events) == 1
    event = backend.events["memory-1"]
    assert event["timestamp"] == segment.turns[0].timestamp
    assert event["metadata"]["referenced_time"] is None


def test_single_fact_exact_details_one_call_and_shadow_support():
    source = "My access pass for AP-317 is TEAL-PASS-317 at 52.4 kilohertz."
    model = FakeFormationModel([_unit(
        source, "AP-317", "access pass", "TEAL-PASS-317",
        [("u1", source)],
    )])
    units = form_grounded_memory_units(_segment(("u1", "user", source)), model)
    assert model.calls == 1
    assert len(units) == 1
    assert (units[0].subject, units[0].relation, units[0].value) == (
        "AP-317", "access pass", "TEAL-PASS-317",
    )
    assert units[0].source_refs[0].supporting_span == source
    assert "52.4 kilohertz" in units[0].text
    assert _supports_subject_relations(units, "AP-317", ("access pass",))
    assert not _supports_subject_relations(units, "AP-317", ("archive slot",))


def test_same_turn_is_split_into_two_atomic_units():
    source = "我今晚早点睡。另外实验室打印机坏了。"
    candidates = [
        _unit("我今晚早点睡。", "我", "早点睡", "早点", [("u1", "我今晚早点睡。")]),
        _unit("实验室打印机坏了。", "实验室打印机", "坏了", "坏了", [("u1", "实验室打印机坏了。")]),
    ]
    units = form_grounded_memory_units(
        _segment(("u1", "user", source)), FakeFormationModel(candidates)
    )
    assert len(units) == 2
    assert {unit.value for unit in units} == {"早点", "坏了"}


def test_composite_candidate_is_rejected():
    source = "My pass is TEAL. My badge is MINT."
    units = form_grounded_memory_units(
        _segment(("u1", "user", source)),
        FakeFormationModel([
            _unit(
                source,
                "My pass",
                "pass",
                "TEAL",
                [("u1", source)],
            )
        ]),
    )
    assert units == ()


def test_multi_turn_grounding_preserves_all_refs():
    turns = (
        ("u1", "user", "小林下周要出差。"),
        ("a1", "assistant", "去哪？"),
        ("u2", "user", "上海。"),
    )
    model = FakeFormationModel([_unit(
        "小林下周要出差去上海。", "小林", "下周要出差", "上海",
        [(turns[0][0], turns[0][2]), (turns[2][0], turns[2][2])],
        "下周",
    )])
    units = form_grounded_memory_units(_segment(*turns), model)
    assert len(units) == 1
    assert tuple(ref.turn_id for ref in units[0].source_refs) == ("u1", "u2")
    assert units[0].referenced_time == "下周"


def test_assistant_only_claim_is_rejected_but_user_correction_is_grounded():
    assistant = "你的门牌号码是5433。"
    correction = "不，我的门牌号码是5434。"
    candidates = [
        _unit(assistant, "你", "门牌号码", "5433", [("a1", assistant)]),
        _unit(correction, "我", "门牌号码", "5434", [("u1", correction)]),
    ]
    units = form_grounded_memory_units(
        _segment(("a1", "assistant", assistant), ("u1", "user", correction)),
        FakeFormationModel(candidates),
    )
    assert len(units) == 1
    assert units[0].value == "5434"


def test_unrelated_user_acceptance_cannot_authorize_assistant_claim():
    claim = "The access pass for AP-317 is TEAL-PASS-317."
    units = form_grounded_memory_units(
        _segment(
            ("a1", "assistant", claim),
            ("a2", "assistant", "Do you like tea?"),
            ("u1", "user", "Yes."),
        ),
        FakeFormationModel([
            _unit(
                claim,
                "AP-317",
                "access pass",
                "TEAL-PASS-317",
                [("a1", claim), ("u1", "Yes.")],
            )
        ]),
    )
    assert units == ()


def test_adjacent_user_acceptance_can_authorize_exact_assistant_claim():
    claim = "The access pass for AP-317 is TEAL-PASS-317."
    units = form_grounded_memory_units(
        _segment(
            ("a1", "assistant", claim),
            ("u1", "user", "Yes."),
        ),
        FakeFormationModel([
            _unit(
                claim,
                "AP-317",
                "access pass",
                "TEAL-PASS-317",
                [("a1", claim), ("u1", "Yes.")],
            )
        ]),
    )
    assert len(units) == 1


def test_assistant_self_memory_preserves_assistant_role_without_authorizing_user_claim():
    self_claim = "I assigned mount MT-734 to RACK-PLUM-734."
    user_claim = "Your access pass for AP-317 is TEAL-PASS-317."
    speculative_claim = "I think AP-317 access pass is TEAL-PASS-317."
    units = form_grounded_memory_units(
        _segment(
            ("a1", "assistant", self_claim),
            ("a2", "assistant", user_claim),
            ("a3", "assistant", speculative_claim),
        ),
        FakeFormationModel([
            _unit(
                self_claim,
                "MT-734",
                "assigned mount",
                "RACK-PLUM-734",
                [("a1", self_claim)],
            ),
            _unit(
                user_claim,
                "AP-317",
                "access pass",
                "TEAL-PASS-317",
                [("a2", user_claim)],
            ),
            _unit(
                speculative_claim,
                "AP-317",
                "access pass",
                "TEAL-PASS-317",
                [("a3", speculative_claim)],
            ),
        ]),
    )
    assert len(units) == 1
    assert units[0].subject == "MT-734"
    assert units[0].source_refs[0].turn_id == "a1"


def test_nearby_chinese_relation_is_not_treated_as_grounded():
    source = "我的电压电平（VB-769）是 4.7 伏特。"
    units = form_grounded_memory_units(
        _segment(("u1", "user", source)),
        FakeFormationModel([
            _unit(
                "VB-769 的电压代码是 4.7 伏特。",
                "VB-769",
                "电压代码",
                "4.7 伏特",
                [("u1", source)],
            )
        ]),
    )
    assert units == ()


def test_candidate_validation_drops_inverted_negation_uncertainty_and_details():
    source = "Maybe port PORT-BIRCH-365 is not set to 52.4 kilohertz."
    candidates = [
        _unit("Port PORT-BIRCH-365 is set to 52.4 kilohertz.", "PORT-BIRCH-365", "setting", "52.4 kilohertz", [("u1", source)]),
        _unit("Maybe port PORT-BIRCH-365 is not set to 52.5 kilohertz.", "PORT-BIRCH-365", "setting", "52.5 kilohertz", [("u1", source)]),
    ]
    assert form_grounded_memory_units(
        _segment(("u1", "user", source)), FakeFormationModel(candidates)
    ) == ()


def test_unrelated_negation_cannot_mask_target_inversion():
    fact = "AP-317 access pass is TEAL-PASS-317."
    unrelated = "The printer is not working."
    units = form_grounded_memory_units(
        _segment(("u1", "user", fact), ("u2", "user", unrelated)),
        FakeFormationModel([
            _unit(
                "AP-317 access pass is not TEAL-PASS-317.",
                "AP-317",
                "access pass",
                "TEAL-PASS-317",
                [("u1", fact), ("u2", unrelated)],
            )
        ]),
    )
    assert units == ()


def test_same_relation_negation_cannot_mask_target_value_inversion():
    fact = "AP-317 access pass is TEAL-PASS-317."
    status = "AP-317 access pass is not revoked."
    units = form_grounded_memory_units(
        _segment(("u1", "user", fact), ("u2", "user", status)),
        FakeFormationModel([
            _unit(
                "AP-317 access pass is not TEAL-PASS-317.",
                "AP-317",
                "access pass",
                "TEAL-PASS-317",
                [("u1", fact), ("u2", status)],
            )
        ]),
    )
    assert units == ()


def test_correction_keeps_both_source_grounded_facts_without_supersession():
    first = "My panel PW-875 current setting is Alpha."
    second = "I later changed panel PW-875 current setting to Beta."
    units = form_grounded_memory_units(
        _segment(("u1", "user", first), ("u2", "user", second)),
        FakeFormationModel([
            _unit(first, "PW-875", "current setting", "Alpha", [("u1", first)]),
            _unit(second, "PW-875", "current setting", "Beta", [("u2", second)]),
        ]),
    )
    assert {unit.value for unit in units} == {"Alpha", "Beta"}


def test_wrong_relation_missing_private_and_multifact_shadow_coverage():
    pw = "Panel PW-875 current setting is 3.6 amperes."
    fuse = "Panel PW-875 fuse tag is FUSE-875."
    badge = "My borrowing badge for BM-794 is MINT-BADGE-794."
    units = form_grounded_memory_units(
        _segment(("u1", "user", pw), ("u2", "user", fuse), ("u3", "user", badge)),
        FakeFormationModel([
            _unit(pw, "PW-875", "current setting", "3.6 amperes", [("u1", pw)]),
            _unit(fuse, "PW-875", "fuse tag", "FUSE-875", [("u2", fuse)]),
            _unit(badge, "BM-794", "borrowing badge", "MINT-BADGE-794", [("u3", badge)]),
        ]),
    )
    assert not _supports_subject_relations(units, "PW-875", ("chest rune",))
    assert not _supports_subject_relations(units, "BM-794", ("archive slot",))
    assert _supports_subject_relations(units, "PW-875", ("current setting", "fuse tag"))
    assert not _supports_subject_relations(units[:1], "PW-875", ("current setting", "fuse tag"))


def test_malformed_provider_output_fails_closed():
    class BadModel:
        def generate(self, *_args, **_kwargs):
            return "not-json"

    with pytest.raises(FormationError, match="formation_failed"):
        form_grounded_memory_units(
            _segment(("u1", "user", "My pass for AP-317 is TEAL-PASS-317.")),
            BadModel(),
        )


def test_formation_is_persisted_before_magma_and_retry_reuses_it(tmp_path):
    source = "My access pass for AP-317 is TEAL-PASS-317."
    model = FakeFormationModel([_unit(
        source,
        "AP-317",
        "access pass",
        "TEAL-PASS-317",
        [("u1", source)],
    )])
    segment = _segment(("u1", "user", source))
    state_store = IngestionStateStore(tmp_path / "state.json")

    class FailOnceBackend:
        def __init__(self):
            self.events = {}
            self.failed = False
            self.checkpoint_seen = False

        def find_memory_id(self, evidence_id):
            for memory_id, event in self.events.items():
                if event["metadata"]["evidence_id"] == evidence_id:
                    return memory_id
            return None

        def add_event(self, text, timestamp, metadata):
            state = state_store.get(state_store.key(
                segment.segment_id,
                FORMATION_VERSION,
            ))
            self.checkpoint_seen = bool(
                state
                and state["formed_units"]
                and state["memory_ids"] == []
            )
            if not self.failed:
                self.failed = True
                raise RuntimeError("synthetic backend failure")
            memory_id = f"memory-{len(self.events)}"
            self.events[memory_id] = {
                "text": text,
                "timestamp": timestamp,
                "metadata": metadata,
            }
            return memory_id

        def create_relationships(self, _memory_ids):
            return None

        def persist(self):
            return None

        def recall(self, _query, _policy):
            return ()

    backend = FailOnceBackend()
    first = MagmaMemoryAdapter(
        backend,
        state_store,
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    ).ingest(segment)
    assert first.status == "failed"
    assert first.retryable
    assert backend.checkpoint_seen
    assert model.calls == 1

    restarted = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "state.json"),
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    )
    second = restarted.ingest(segment)
    assert second.status == "completed"
    assert model.calls == 1
    assert backend.events[second.memory_ids[0]]["metadata"]["subject"] == "AP-317"
    assert len(backend.events[second.memory_ids[0]]["metadata"]["source_refs"]) == 1

    third = restarted.ingest(segment)
    assert third.status == "completed"
    assert third.already_ingested
    assert model.calls == 1


def test_persist_failure_after_add_reuses_event_without_formation_retry(tmp_path):
    source = "My access pass for AP-317 is TEAL-PASS-317."
    model = FakeFormationModel([_unit(
        source,
        "AP-317",
        "access pass",
        "TEAL-PASS-317",
        [("u1", source)],
    )])
    segment = _segment(("u1", "user", source))

    class FailPersistOnceBackend:
        def __init__(self):
            self.events = {}
            self.fail_once = True

        def find_memory_id(self, evidence_id):
            for memory_id, event in self.events.items():
                if event["metadata"]["evidence_id"] == evidence_id:
                    return memory_id
            return None

        def add_event(self, text, timestamp, metadata):
            memory_id = f"memory-{len(self.events)}"
            self.events[memory_id] = {
                "text": text,
                "timestamp": timestamp,
                "metadata": metadata,
            }
            return memory_id

        def persist(self):
            if self.fail_once:
                self.fail_once = False
                raise OSError("synthetic persist failure")

        def create_relationships(self, _memory_ids):
            return None

        def recall(self, _query, _policy):
            return ()

    backend = FailPersistOnceBackend()
    path = tmp_path / "state.json"
    first = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(path),
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    ).ingest(segment)
    assert first.status == "failed"
    assert len(backend.events) == 1
    assert model.calls == 1

    second = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(path),
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    ).ingest(segment)
    assert second.status == "completed"
    assert len(backend.events) == 1
    assert model.calls == 1


def test_state_write_failure_after_event_is_idempotent_on_retry(tmp_path):
    source = "My access pass for AP-317 is TEAL-PASS-317."
    model = FakeFormationModel([_unit(
        source,
        "AP-317",
        "access pass",
        "TEAL-PASS-317",
        [("u1", source)],
    )])
    segment = _segment(("u1", "user", source))
    path = tmp_path / "state.json"

    class Backend:
        def __init__(self):
            self.events = {}

        def find_memory_id(self, evidence_id):
            for memory_id, event in self.events.items():
                if event["metadata"]["evidence_id"] == evidence_id:
                    return memory_id
            return None

        def add_event(self, text, timestamp, metadata):
            memory_id = f"memory-{len(self.events)}"
            self.events[memory_id] = {
                "text": text,
                "timestamp": timestamp,
                "metadata": metadata,
            }
            return memory_id

        def persist(self):
            return None

        def create_relationships(self, _memory_ids):
            return None

        def recall(self, _query, _policy):
            return ()

    class FailAfterEventStateStore(IngestionStateStore):
        def __init__(self, state_path):
            super().__init__(state_path)
            self.in_progress_writes = 0

        def put(self, key, value):
            if value.get("status") == "in_progress":
                self.in_progress_writes += 1
                if self.in_progress_writes == 2:
                    raise OSError("synthetic state write failure")
            super().put(key, value)

    backend = Backend()
    first = MagmaMemoryAdapter(
        backend,
        FailAfterEventStateStore(path),
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    ).ingest(segment)
    assert first.status == "failed"
    assert len(backend.events) == 1
    assert model.calls == 1

    second = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(path),
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    ).ingest(segment)
    assert second.status == "completed"
    assert len(backend.events) == 1
    assert model.calls == 1


def test_formation_requires_dedicated_version_and_rejects_old_manifest(tmp_path):
    source = "My access pass for AP-317 is TEAL-PASS-317."
    model = FakeFormationModel([_unit(
        source,
        "AP-317",
        "access pass",
        "TEAL-PASS-317",
        [("u1", source)],
    )])
    backend = type("Backend", (), {"recall": lambda *_args: ()})()
    with pytest.raises(ValueError, match="formation_ingestion_version_required"):
        MagmaMemoryAdapter(
            backend,
            IngestionStateStore(tmp_path / "invalid.json"),
            ingestion_version="grounded-span-v2",
            formation_model=model,
        )

    state_store = IngestionStateStore(tmp_path / "legacy.json")
    state_store.put(
        state_store.key("formation-segment", FORMATION_VERSION),
        {
            "segment_id": "formation-segment",
            "ingestion_version": FORMATION_VERSION,
            "status": "pending",
            "unit_ids": [],
            "memory_ids": [],
        },
    )
    result = MagmaMemoryAdapter(
        backend,
        state_store,
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    ).ingest(_segment(("u1", "user", source)))
    assert result.status == "failed"
    assert not result.retryable
    assert result.safe_error_code == "state_corrupt"
    assert model.calls == 0


def test_formation_failure_writes_no_checkpoint_or_magma(tmp_path):
    class BadModel:
        def __init__(self):
            self.calls = 0

        def generate(self, *_args, **_kwargs):
            self.calls += 1
            raise RuntimeError("synthetic provider failure")

    class Backend:
        def __init__(self):
            self.add_calls = 0

        def add_event(self, *_args, **_kwargs):
            self.add_calls += 1
            raise AssertionError("MAGMA must not be called")

        def recall(self, *_args):
            return ()

    model = BadModel()
    backend = Backend()
    state_store = IngestionStateStore(tmp_path / "state.json")
    segment = _segment((
        "u1", "user", "My access pass for AP-317 is TEAL-PASS-317.",
    ))
    result = MagmaMemoryAdapter(
        backend,
        state_store,
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    ).ingest(segment)
    assert result.status == "failed"
    assert result.retryable
    assert result.safe_error_code == "formation_failed"
    assert model.calls == 1
    assert backend.add_calls == 0
    assert state_store.get(state_store.key(
        segment.segment_id, FORMATION_VERSION,
    )) is None


def test_app_and_cli_wiring_select_formation_only_with_model(monkeypatch, tmp_path):
    from Conversation_Memory.adapter import magma_adapter as packaged_magma
    from Dream.runner import RealMemoryIngestorProvider
    from core import main as core_main

    model = FakeFormationModel([])
    created = []

    def fake_create_real(*args, **kwargs):
        created.append((args, kwargs))
        return object()

    monkeypatch.setattr(
        packaged_magma.MagmaMemoryAdapter,
        "create_real",
        staticmethod(fake_create_real),
    )
    assert core_main._build_memory_retriever(model) is not None
    assert created[-1][1]["ingestion_version"] == FORMATION_VERSION
    assert created[-1][1]["formation_model"] is model

    monkeypatch.setattr(
        MagmaMemoryAdapter,
        "create_real",
        staticmethod(fake_create_real),
    )
    provider = RealMemoryIngestorProvider(
        tmp_path / "magma", tmp_path / "state.json", model,
    )
    provider.get(FORMATION_VERSION)
    assert created[-1][1]["formation_model"] is model
    provider.get("grounded-span-v2")
    assert created[-1][1]["formation_model"] is None
    with pytest.raises(RuntimeError, match="formation_model_unavailable"):
        RealMemoryIngestorProvider(
            tmp_path / "magma-2", tmp_path / "state-2.json",
        ).get(FORMATION_VERSION)


def test_cli_default_version_tracks_effective_model_and_explicit_value(monkeypatch):
    import Dream.runner as runner_module
    from Dream.models import DreamRunReport
    from core.model_client import MockModelClient

    observed = []

    class Runner:
        def run_once(self, policy):
            observed.append(policy.ingestion_version)
            return DreamRunReport.from_results(())

    monkeypatch.setattr(runner_module, "build_default_runner", lambda _model: Runner())
    requested_models = []

    def build_mock(*, model_name_override=None, max_tokens_override=None):
        requested_models.append((model_name_override, max_tokens_override))
        return MockModelClient()

    monkeypatch.setattr(runner_module, "build_model_client_from_env", build_mock)
    assert runner_module.main([]) == 0
    assert requested_models[-1] == ("MiniMax-M3", 2000)
    assert observed[-1] == "grounded-span-v2"

    real = FakeFormationModel([])
    real.client_kind = "model"
    def build_real(*, model_name_override=None, max_tokens_override=None):
        requested_models.append((model_name_override, max_tokens_override))
        return real

    monkeypatch.setattr(runner_module, "build_model_client_from_env", build_real)
    assert runner_module.main([]) == 0
    assert requested_models[-1] == ("MiniMax-M3", 2000)
    assert observed[-1] == FORMATION_VERSION
    assert runner_module.main(["--ingestion-version", "grounded-span-v2"]) == 0
    assert observed[-1] == "grounded-span-v2"


def test_default_runner_uses_dedicated_formation_model(monkeypatch, tmp_path):
    import Dream.runner as runner_module

    model = FakeFormationModel([])
    model.client_kind = "model"
    requested_models = []

    def build_real(*, model_name_override=None, max_tokens_override=None):
        requested_models.append((model_name_override, max_tokens_override))
        return model

    monkeypatch.setattr(runner_module, "build_model_client_from_env", build_real)
    monkeypatch.setenv(
        "LUMINA_DREAM_COLD_DRAFT_PATH",
        str(tmp_path / "cold.jsonl"),
    )
    monkeypatch.setenv(
        "LUMINA_DREAM_INGESTION_STATE_PATH",
        str(tmp_path / "state.json"),
    )
    monkeypatch.setenv(
        "LUMINA_DREAM_MAGMA_PERSIST_DIR",
        str(tmp_path / "magma"),
    )
    runner_module.build_default_runner()
    assert requested_models == [("MiniMax-M3", 2000)]


def test_multi_turn_referenced_time_uses_the_unique_source_turn_timestamp(tmp_path):
    first = "小林要出差。"
    second = "明天去上海。"
    model = FakeFormationModel([_unit(
        "小林明天要出差去上海。",
        "小林",
        "要出差",
        "上海",
        [("u1", first), ("u2", second)],
        "明天",
    )])
    segment = _segment(
        ("u1", "user", first),
        ("u2", "user", second),
    )

    class Backend:
        def __init__(self):
            self.event = None

        def find_memory_id(self, _evidence_id):
            return None

        def add_event(self, text, timestamp, metadata):
            self.event = (text, timestamp, metadata)
            return "memory-1"

        def persist(self):
            return None

        def create_relationships(self, _memory_ids):
            return None

        def recall(self, *_args):
            return ()

    backend = Backend()
    result = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "state.json"),
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    ).ingest(segment)
    assert result.status == "completed"
    _text, timestamp, metadata = backend.event
    assert timestamp == segment.turns[1].timestamp
    assert metadata["turn_id"] == "u2"
    assert metadata["provenance"]["turn_id"] == "u2"
    assert metadata["temporal_mentions"][0]["original_expression"] == "明天"
