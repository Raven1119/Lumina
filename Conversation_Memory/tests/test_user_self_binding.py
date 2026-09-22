from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

import Conversation_Memory.adapter.magma_adapter as magma_adapter_module  # noqa: E402
from Conversation_Memory.adapter.grounded_formation import (  # noqa: E402
    FORMATION_VERSION,
    GroundedMemoryUnit,
    SourceRef,
)
from Conversation_Memory.adapter.magma_adapter import MagmaMemoryAdapter  # noqa: E402
from Conversation_Memory.adapter.models import (  # noqa: E402
    BackendCandidate,
    ColdDraftSegment,
    ColdDraftTurn,
    RecallPolicy,
)
from Conversation_Memory.adapter.user_self import (  # noqa: E402
    CURRENT_USER_ENTITY_REF,
    classify_subject_entity_ref,
    classify_target_entity_ref,
)
from Conversation_Memory.ingestion.state_store import IngestionStateStore  # noqa: E402


def _segment(*turns: tuple[str, str, str]) -> ColdDraftSegment:
    return ColdDraftSegment(
        segment_id="user-self-segment",
        conversation_id="user-self-conversation",
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
    return GroundedMemoryUnit(
        id="unit-under-test",
        text=text,
        subject=subject,
        relation=relation,
        value=value,
        source_refs=tuple(
            SourceRef(turn_id=turn_id, supporting_span=span)
            for turn_id, span in refs
        ),
        referenced_time=referenced_time,
    )


# --- write-side classifier (shadow labels_write_side key cases) ---


def test_self_naming_binds_subject_to_current_user():
    segment = _segment(("u1", "user", "我叫林岚，来自东海大学物理学院，现在大二"))
    unit = _unit(
        "林岚 来自东海大学物理学院", "林岚", "来自", "东海大学物理学院",
        [("u1", "我叫林岚，来自东海大学物理学院")],
    )
    assert classify_subject_entity_ref(unit, segment) == CURRENT_USER_ENTITY_REF


def test_first_person_subject_in_user_span_binds():
    segment = _segment(("u1", "user", "我的生日是5月20日。"))
    unit = _unit(
        "我的生日是5月20日。", "我", "生日", "5月20日",
        [("u1", "我的生日是5月20日。")],
    )
    assert classify_subject_entity_ref(unit, segment) == CURRENT_USER_ENTITY_REF


def test_first_person_subject_in_assistant_span_does_not_bind():
    segment = _segment(
        ("u1", "user", "刚才那段配置你记一下。"),
        ("a1", "assistant", "我会记住这件事。"),
    )
    unit = _unit(
        "我会记住这件事。", "我", "会记住", "这件事",
        [("a1", "我会记住这件事。")],
    )
    assert classify_subject_entity_ref(unit, segment) is None


def test_user_spoken_fact_about_third_party_does_not_bind():
    segment = _segment(("u1", "user", "我朋友小林开了家公司。"))
    unit = _unit(
        "小林开了家公司。", "小林", "开了", "家公司",
        [("u1", "小林开了家公司。")],
    )
    assert classify_subject_entity_ref(unit, segment) is None


def test_possessive_relative_subject_does_not_bind():
    segment = _segment(("u1", "user", "我妻子在南京工作。"))
    unit = _unit(
        "我妻子在南京工作。", "我妻子", "在南京工作", "南京",
        [("u1", "我妻子在南京工作。")],
    )
    assert classify_subject_entity_ref(unit, segment) is None


def test_assistant_naming_with_user_acceptance_binds():
    segment = _segment(
        ("a1", "assistant", "你叫林岚。"),
        ("u1", "user", "对。"),
    )
    unit = _unit(
        "你叫林岚。", "林岚", "叫", "林岚",
        [("a1", "你叫林岚。"), ("u1", "对。")],
    )
    assert classify_subject_entity_ref(unit, segment) == CURRENT_USER_ENTITY_REF


def test_english_self_naming_binds():
    segment = _segment(
        ("u1", "user", "My name is Lin Lan, and I study physics."),
    )
    unit = _unit(
        "My name is Lin Lan.", "Lin Lan", "name", "Lin Lan",
        [("u1", "My name is Lin Lan")],
    )
    assert classify_subject_entity_ref(unit, segment) == CURRENT_USER_ENTITY_REF


def test_english_first_person_subject_binds():
    segment = _segment(("u1", "user", "I work as a barista on weekends."))
    unit = _unit(
        "I work as a barista on weekends.", "I", "work as", "a barista",
        [("u1", "I work as a barista on weekends.")],
    )
    assert classify_subject_entity_ref(unit, segment) == CURRENT_USER_ENTITY_REF


def test_segment_level_self_name_binding():
    segment = _segment(
        ("u1", "user", "我叫林岚。"),
        ("u2", "user", "东海大学物理学院是我的学院。"),
    )
    unit = _unit(
        "林岚的学院是东海大学物理学院", "林岚", "的学院", "东海大学物理学院",
        [("u2", "东海大学物理学院是我的学院。")],
    )
    assert classify_subject_entity_ref(unit, segment) == CURRENT_USER_ENTITY_REF


# --- write-side Formation-normalized "用户" subject surface ---


def test_formation_normalized_user_subject_binds_from_user_span():
    segment = _segment(("u1", "user", "我挺喜欢物理的"))
    unit = _unit(
        "用户表示喜欢物理。", "用户", "表示喜欢", "物理",
        [("u1", "我挺喜欢物理的")],
    )
    assert classify_subject_entity_ref(unit, segment) == CURRENT_USER_ENTITY_REF


def test_formation_normalized_user_subject_binds_self_introduction():
    segment = _segment(("u1", "user", "我叫林岚，来自东海大学物理学院，现在大二"))
    unit = _unit(
        "用户来自东海大学物理学院。", "用户", "来自", "东海大学物理学院",
        [("u1", "我叫林岚，来自东海大学物理学院")],
    )
    assert classify_subject_entity_ref(unit, segment) == CURRENT_USER_ENTITY_REF


def test_formation_normalized_user_subject_with_assistant_address_acceptance():
    segment = _segment(
        ("a1", "assistant", "你叫林岚。"),
        ("u1", "user", "对。"),
    )
    unit = _unit(
        "用户的名字是林岚。", "用户", "的名字是", "林岚",
        [("a1", "你叫林岚。"), ("u1", "对。")],
    )
    assert classify_subject_entity_ref(unit, segment) == CURRENT_USER_ENTITY_REF


def test_formation_normalized_user_subject_without_first_person_no_bind():
    segment = _segment(("u1", "user", "小林开了家公司。"))
    unit = _unit(
        "用户提到小林开了家公司。", "用户", "提到", "小林开了家公司",
        [("u1", "小林开了家公司。")],
    )
    assert classify_subject_entity_ref(unit, segment) is None


def test_formation_normalized_user_subject_assistant_only_no_bind():
    segment = _segment(
        ("u1", "user", "刚才那段配置你记一下。"),
        ("a1", "assistant", "我会记住这件事。"),
    )
    unit = _unit(
        "用户会记住这件事。", "用户", "会记住", "这件事",
        [("a1", "我会记住这件事。")],
    )
    assert classify_subject_entity_ref(unit, segment) is None


def test_formation_normalized_possessive_subject_does_not_bind():
    segment = _segment(("u1", "user", "我妻子在南京工作。"))
    unit = _unit(
        "用户的妻子在南京工作。", "用户的妻子", "在南京工作", "南京",
        [("u1", "我妻子在南京工作。")],
    )
    assert classify_subject_entity_ref(unit, segment) is None


def test_formation_normalized_english_user_surface_binds():
    segment = _segment(("u1", "user", "我挺喜欢物理的"))
    for surface in ("user", "User", "the user", "The user"):
        unit = _unit(
            "The user likes physics.", surface, "preference/likes", "physics",
            [("u1", "我挺喜欢物理的")],
        )
        assert classify_subject_entity_ref(unit, segment) == CURRENT_USER_ENTITY_REF


def test_formation_normalized_english_user_surface_without_first_person_no_bind():
    segment = _segment(("u1", "user", "小林开了家公司。"))
    unit = _unit(
        "The user mentions that Xiaolin opened a company.",
        "user", "mentions", "Xiaolin opened a company",
        [("u1", "小林开了家公司。")],
    )
    assert classify_subject_entity_ref(unit, segment) is None


def test_formation_normalized_possessive_english_surface_does_not_bind():
    segment = _segment(("u1", "user", "我妻子在南京工作。"))
    for surface in ("user's wife", "the user's wife"):
        unit = _unit(
            "The user states that their wife works in Nanjing.",
            surface, "work_location", "Nanjing",
            [("u1", "我妻子在南京工作。")],
        )
        assert classify_subject_entity_ref(unit, segment) is None


# --- query-side classifier (shadow labels_query_side key cases) ---


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("我是谁？", CURRENT_USER_ENTITY_REF),
        ("我的学校是什么？", CURRENT_USER_ENTITY_REF),
        ("我朋友小林是谁？", None),
        ("小林是谁？", None),
        ("我之前说过小林在哪工作？", None),
        ("我之前说过我在哪工作吗？", CURRENT_USER_ENTITY_REF),
        ("我的银行卡号是多少？", CURRENT_USER_ENTITY_REF),
        ("今天天气怎么样？", None),
        ("我喜欢什么颜色？", CURRENT_USER_ENTITY_REF),
        ("我的生日是哪天？", CURRENT_USER_ENTITY_REF),
        ("小林喜欢什么？", None),
        ("我叫什么名字？", CURRENT_USER_ENTITY_REF),
        ("小林的学校是哪个？", None),
        ("我上次提到的我的工作是什么？", CURRENT_USER_ENTITY_REF),
        ("你知道小林多大了吗？", None),
    ],
)
def test_query_target_entity_ref(query, expected):
    assert classify_target_entity_ref(query) == expected


# --- ingest metadata seam ---


class FakeFormationModel:
    def __init__(self, units):
        self.units = units

    def generate(self, recent_context, user_message, *, system_prompt):
        assert recent_context == []
        if system_prompt.startswith((
            "Extract only entity mentions",
            "Select from the Candidate mentions",
        )):
            # mention extraction/selection calls (Production Slice 1); this
            # fake binds no mentions
            return json.dumps({"entities": []}, ensure_ascii=False)
        return json.dumps({"units": self.units}, ensure_ascii=False)


class FakeBackend:
    def __init__(self):
        self.events = {}
        self.order = []

    def find_memory_id(self, evidence_id):
        for memory_id, event in self.events.items():
            if event["metadata"]["evidence_id"] == evidence_id:
                return memory_id
        return None

    def add_event(self, text, timestamp, metadata):
        memory_id = f"memory-{len(self.order)}"
        self.events[memory_id] = {
            "text": text, "timestamp": timestamp, "metadata": metadata,
        }
        self.order.append(memory_id)
        return memory_id

    def create_relationships(self, memory_ids):
        pass

    def persist(self):
        pass

    def resolve_target_entity_ref(self, query):
        return None

    def recall(self, query, policy):
        return []


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


def test_formation_ingest_marks_user_self_subject(tmp_path):
    introduction = "我叫林岚，来自东海大学物理学院，现在大二"
    model = FakeFormationModel([_unit_payload(
        "林岚 来自东海大学物理学院", "林岚", "来自", "东海大学物理学院",
        [("u1", "我叫林岚，来自东海大学物理学院")],
    )])
    backend = FakeBackend()
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "state.json"),
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    )
    result = adapter.ingest(_segment(("u1", "user", introduction)))
    assert result.status == "completed"
    metadata = backend.events[result.memory_ids[0]]["metadata"]
    assert metadata["subject_entity_ref"] == CURRENT_USER_ENTITY_REF


def test_formation_ingest_third_party_subject_gets_exact_surface_ref(tmp_path):
    model = FakeFormationModel([_unit_payload(
        "小林开了家公司。", "小林", "开了", "家公司",
        [("u1", "小林开了家公司。")],
    )])
    backend = FakeBackend()
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "state.json"),
        ingestion_version=FORMATION_VERSION,
        formation_model=model,
    )
    result = adapter.ingest(_segment(("u1", "user", "我朋友小林开了家公司。")))
    assert result.status == "completed"
    metadata = backend.events[result.memory_ids[0]]["metadata"]
    assert metadata["subject_entity_ref"].startswith("E_")
    assert metadata["subject_entity_surface"] == "小林"


def test_grounded_span_ingest_has_no_entity_ref_field(tmp_path):
    backend = FakeBackend()
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "state.json"),
        ingestion_version="grounded-span-v2",
    )
    result = adapter.ingest(
        _segment(("u1", "user", "我叫林岚，来自东海大学物理学院，现在大二")),
    )
    assert result.status == "completed"
    assert result.memory_ids
    for memory_id in result.memory_ids:
        assert "subject_entity_ref" not in backend.events[memory_id]["metadata"]


# --- recall BGE scoring projection seam ---


class _RecordingReranker:
    def __init__(self):
        self.calls = []

    def score(self, query, texts):
        self.calls.append((query, tuple(texts)))
        return tuple(10.0 for _text in texts)


class _CandidateBackend:
    def __init__(self, candidates):
        self.candidates = candidates
        self.recalled_queries = []
        self.recalled_target_refs = []
        self.lookup_ref = None
        self.lookup_queries = []

    def find_memory_id(self, evidence_id):
        return None

    def add_event(self, text, timestamp, metadata):
        raise AssertionError("recall-only backend")

    def create_relationships(self, memory_ids):
        pass

    def persist(self):
        pass

    def resolve_target_entity_ref(self, query):
        self.lookup_queries.append(query)
        return self.lookup_ref

    def recall(self, query, policy, target_entity_ref=None):
        self.recalled_queries.append(query)
        self.recalled_target_refs.append(target_entity_ref)
        return list(self.candidates)


def _candidate(text, *, subject_entity_ref=None):
    metadata = {
        "evidence_id": f"evidence-{text}",
        "provenance": {
            "segment_id": "segment",
            "conversation_id": "conversation",
            "turn_id": "u1",
            "source_role": "user",
            "source_timestamp": "2026-08-12T00:00:00+00:00",
            "source_timezone": "Asia/Shanghai",
            "ingestion_version": FORMATION_VERSION,
            "timezone_source": "client",
        },
    }
    if subject_entity_ref is not None:
        metadata["subject_entity_ref"] = subject_entity_ref
    return BackendCandidate(text, "2026-08-12T00:00:00+00:00", 1.0, metadata)


def _recall_adapter(candidates, monkeypatch, tmp_path):
    reranker = _RecordingReranker()
    monkeypatch.setattr(
        magma_adapter_module, "_create_bge_reranker", lambda: reranker,
    )
    backend = _CandidateBackend(candidates)
    adapter = MagmaMemoryAdapter(
        backend,
        IngestionStateStore(tmp_path / "state.json"),
        ingestion_version=FORMATION_VERSION,
    )
    return adapter, backend, reranker


_RECALL_POLICY = RecallPolicy(
    top_k=10,
    max_chars=5000,
    max_evidence_items=3,
    max_graph_depth=1,
    max_nodes=20,
)


def test_self_query_injects_marker_into_bge_projection_only(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    adapter, backend, reranker = _recall_adapter(
        [
            _candidate("林岚 来自东海大学物理学院", subject_entity_ref=CURRENT_USER_ENTITY_REF),
            _candidate("小林开了家公司。"),
        ],
        monkeypatch,
        tmp_path,
    )
    context = adapter.recall("我是谁？", _RECALL_POLICY)
    assert backend.recalled_queries == ["我是谁？"]
    # per-pair injection: the constant [SAME_ENTITY] marker enters a
    # (query, candidate) pair only when both sides carry the same entity ref;
    # unmatched pairs score byte-identically.
    assert reranker.calls == [
        ("[SAME_ENTITY] 我是谁？", ("[SAME_ENTITY] 林岚 来自东海大学物理学院",)),
        ("我是谁？", ("小林开了家公司。",)),
    ]
    assert context.query == "我是谁？"
    assert sorted(item.text for item in context.evidence) == [
        "小林开了家公司。",
        "林岚 来自东海大学物理学院",
    ]
    assert "[SAME_ENTITY]" not in context.rendered_text
    assert "E_001" not in context.rendered_text


def test_non_self_query_is_byte_identical(monkeypatch, tmp_path):
    adapter, backend, reranker = _recall_adapter(
        [_candidate("林岚 来自东海大学物理学院", subject_entity_ref=CURRENT_USER_ENTITY_REF)],
        monkeypatch,
        tmp_path,
    )
    adapter.recall("小林是谁？", _RECALL_POLICY)
    assert reranker.calls == [("小林是谁？", ("林岚 来自东海大学物理学院",))]


def test_self_query_without_marked_candidates_is_byte_identical(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    adapter, _backend, reranker = _recall_adapter(
        [_candidate("小林开了家公司。")],
        monkeypatch,
        tmp_path,
    )
    adapter.recall("我是谁？", _RECALL_POLICY)
    assert reranker.calls == [("我是谁？", ("小林开了家公司。",))]


def test_binding_disabled_by_env_is_byte_identical(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "false")
    adapter, _backend, reranker = _recall_adapter(
        [_candidate("林岚 来自东海大学物理学院", subject_entity_ref=CURRENT_USER_ENTITY_REF)],
        monkeypatch,
        tmp_path,
    )
    adapter.recall("我是谁？", _RECALL_POLICY)
    assert reranker.calls == [("我是谁？", ("林岚 来自东海大学物理学院",))]


def test_binding_default_on_marks_projection(monkeypatch, tmp_path):
    monkeypatch.delenv("LUMINA_USER_SELF_BINDING_ENABLED", raising=False)
    adapter, _backend, reranker = _recall_adapter(
        [_candidate("林岚 来自东海大学物理学院", subject_entity_ref=CURRENT_USER_ENTITY_REF)],
        monkeypatch,
        tmp_path,
    )
    adapter.recall("我是谁？", _RECALL_POLICY)
    assert reranker.calls == [(
        "[SAME_ENTITY] 我是谁？",
        ("[SAME_ENTITY] 林岚 来自东海大学物理学院",),
    )]


# --- query-side classification happens before candidate generation ---


def test_self_query_passes_target_ref_into_candidate_generation(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    adapter, backend, _reranker = _recall_adapter(
        [_candidate("林岚 来自东海大学物理学院", subject_entity_ref=CURRENT_USER_ENTITY_REF)],
        monkeypatch,
        tmp_path,
    )
    adapter.recall("我是谁？", _RECALL_POLICY)
    assert backend.recalled_target_refs == [CURRENT_USER_ENTITY_REF]


def test_non_self_query_passes_no_target_ref(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    adapter, backend, _reranker = _recall_adapter(
        [_candidate("小林开了家公司。")],
        monkeypatch,
        tmp_path,
    )
    adapter.recall("小林是谁？", _RECALL_POLICY)
    assert backend.recalled_target_refs == [None]


def test_binding_disabled_passes_no_target_ref(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "false")
    adapter, backend, _reranker = _recall_adapter(
        [_candidate("林岚 来自东海大学物理学院", subject_entity_ref=CURRENT_USER_ENTITY_REF)],
        monkeypatch,
        tmp_path,
    )
    adapter.recall("我是谁？", _RECALL_POLICY)
    assert backend.recalled_target_refs == [None]


# --- query-side ordinary entity exact lookup (backend seam) -----------------


def test_self_query_short_circuits_backend_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    adapter, backend, _reranker = _recall_adapter(
        [_candidate("林岚 来自东海大学物理学院", subject_entity_ref=CURRENT_USER_ENTITY_REF)],
        monkeypatch,
        tmp_path,
    )
    backend.lookup_ref = "E_WANG"
    adapter.recall("我是谁？", _RECALL_POLICY)
    # CURRENT_USER classification wins; the lookup is never consulted
    assert backend.lookup_queries == []
    assert backend.recalled_target_refs == [CURRENT_USER_ENTITY_REF]


def test_non_self_query_defers_to_backend_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "true")
    adapter, backend, _reranker = _recall_adapter(
        [_candidate("小林的导师是王老师。", subject_entity_ref="E_XIAOLIN")],
        monkeypatch,
        tmp_path,
    )
    backend.lookup_ref = "E_WANG"
    adapter.recall("王老师的学生是谁？", _RECALL_POLICY)
    assert backend.lookup_queries == ["王老师的学生是谁？"]
    assert backend.recalled_target_refs == ["E_WANG"]


def test_binding_disabled_never_consults_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMINA_USER_SELF_BINDING_ENABLED", "false")
    adapter, backend, _reranker = _recall_adapter(
        [_candidate("小林的导师是王老师。", subject_entity_ref="E_XIAOLIN")],
        monkeypatch,
        tmp_path,
    )
    backend.lookup_ref = "E_WANG"
    adapter.recall("王老师的学生是谁？", _RECALL_POLICY)
    assert backend.lookup_queries == []
    assert backend.recalled_target_refs == [None]
