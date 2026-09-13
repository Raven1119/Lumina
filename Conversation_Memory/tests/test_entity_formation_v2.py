"""v2 admission, source identity and stage recovery regressions.

The deterministic verifier decisions exercise orchestration, not model ability;
real-provider acceptance is measured separately through the ingestion facade.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from adapter.grounded_formation import (
    FORMATION_ENTITY_VERSION,
    FormationError,
    _validate_candidate,
    deserialize_grounded_memory_batch,
    deserialize_grounded_memory_units,
    form_grounded_memory_batch,
    serialize_grounded_memory_batch,
    serialize_grounded_memory_units,
    validate_persisted_grounded_memory_batch,
)
from adapter.models import ColdDraftSegment, ColdDraftTurn


def segment(*turns):
    return ColdDraftSegment(
        segment_id="entity-formation-segment", conversation_id="entity-conversation",
        state="pending_digest", created_at=datetime(2026, 9, 13, tzinfo=UTC),
        source_timezone="Asia/Shanghai", schema_version="2",
        turns=tuple(ColdDraftTurn(
            turn_id=turn_id, role=role, content=content,
            timestamp=datetime(2026, 9, 13, tzinfo=UTC),
            source_timezone="Asia/Shanghai", timezone_source="client",
        ) for turn_id, role, content in turns),
    )


def unit(text, subject, relation, value, source, *, turn_id="u1", **roles):
    return {
        "text": text, "subject": subject, "relation": relation, "value": value,
        "source_refs": [{"turn_id": turn_id, "supporting_span": source}],
        "referenced_time": None, **roles,
    }


def mention(handle, surface, source, *, turn_id="u1", start=None, **identity):
    start = source.index(surface) if start is None else start
    return {
        "handle": handle, "surface": surface, "turn_id": turn_id,
        "source_start": start, "source_end": start + len(surface),
        "identity": "named", **identity,
    }


class Model:
    def __init__(self, output, decisions=None, *, fail_verifier=False):
        self.output = output
        self.decisions = decisions
        self.fail_verifier = fail_verifier
        self.calls = []

    def generate(self, recent_context, user_message, *, system_prompt):
        assert recent_context == []
        payload = json.loads(user_message)
        self.calls.append((system_prompt, payload))
        if system_prompt.startswith("Extract source-grounded"):
            return json.dumps(self.output, ensure_ascii=False)
        assert system_prompt.startswith("Verify ONLY")
        if self.fail_verifier:
            raise RuntimeError("private provider details")
        return json.dumps(self.decisions if self.decisions is not None else {
            "units": [{"supported": True} for _ in payload["units"]],
            "mentions": [{"supported": True, "identity_supported": True} for _ in payload["mentions"]],
        })


@pytest.mark.parametrize("source,text,subject,relation,value", [
    ("张三喜欢茶，李四喜欢咖啡。", "张三喜欢咖啡。", "张三", "喜欢", "咖啡"),
    ("张三喜欢咖啡吗？", "张三喜欢咖啡。", "张三", "喜欢", "咖啡"),
    ("假设张三喜欢咖啡。", "张三喜欢咖啡。", "张三", "喜欢", "咖啡"),
    ("样品A厚度200 nm，样品B厚度300 nm。", "样品A厚度300 nm。", "样品A", "厚度", "300 nm"),
])
def test_legacy_literal_admission_is_not_a_v2_semantic_bypass(source, text, subject, relation, value):
    cold = segment(("u1", "user", source))
    candidate = unit(text, subject, relation, value, source)
    assert _validate_candidate(candidate, cold) is not None  # Reproduces HEAD's bug.
    model = Model({"mentions": [], "units": [candidate]}, {"units": [{"supported": False}], "mentions": []})
    batch = form_grounded_memory_batch(cold, model)
    assert batch.units == ()
    assert len(model.calls) == 2
    assert len(model.calls[1][1]["units"]) == 1


def test_assistant_self_action_is_rejected_before_even_a_permissive_verifier():
    source = "I deleted the file"
    cold = segment(("a1", "assistant", source))
    candidate = unit(source, "I", "deleted", "file", source, turn_id="a1")
    assert _validate_candidate(candidate, cold) is not None
    model = Model({"mentions": [], "units": [candidate]})
    assert form_grounded_memory_batch(cold, model).units == ()
    assert len(model.calls) == 1


def test_mentions_survive_without_facts_and_repeated_occurrences_have_distinct_ids():
    source = "项目R、材料Z与软件Q怎么样？项目R还在吗？"
    occurrences = [
        mention("p1", "项目R", source), mention("m", "材料Z", source),
        mention("s", "软件Q", source), mention("p2", "项目R", source, start=source.rindex("项目R")),
    ]
    cold = segment(("u1", "user", source))
    model = Model({"mentions": occurrences, "units": []})
    batch = form_grounded_memory_batch(cold, model)
    assert len(batch.mentions) == 4
    assert len({item.id for item in batch.mentions}) == 4
    assert batch.units == ()
    for item in batch.mentions:
        assert cold.turns[0].content[item.source_start:item.source_end] == item.surface
        assert item.source_role == "user"
    assert deserialize_grounded_memory_batch(serialize_grounded_memory_batch(batch)) == batch
    assert validate_persisted_grounded_memory_batch(batch, cold)


def test_entity_roles_are_distinct_from_literal_attribute_values():
    source = "小林研究材料B。材料B厚度200 nm。"
    output = {
        "mentions": [mention("person", "小林", source), mention("material", "材料B", source)],
        "units": [
            unit("小林研究材料B。", "小林", "研究", "材料B", "小林研究材料B。", subject_mention="person", object_mention="material"),
            unit("材料B厚度200 nm。", "材料B", "厚度", "200 nm", "材料B厚度200 nm。", subject_mention="material", object_mention=None),
        ],
    }
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), Model(output))
    assert len(batch.units) == 2
    assert batch.unit_mentions[0].object == batch.mentions[1].id
    assert batch.unit_mentions[1].object is None
    assert all(item.formation_version == FORMATION_ENTITY_VERSION for item in batch.units)
    assert all(item.id.startswith("grounded_memory_v2:") for item in batch.units)


def test_explicit_alias_new_identity_and_current_user_retain_source_evidence():
    source = "我叫林舟，也叫阿舟。另一个林舟负责项目R。"
    identity_ref = [{"turn_id": "u1", "supporting_span": "我叫林舟，也叫阿舟。"}]
    difference_ref = [{"turn_id": "u1", "supporting_span": "另一个林舟负责项目R。"}]
    output = {"units": [], "mentions": [
        mention("name", "林舟", source, identity="current_user", identity_source_refs=identity_ref),
        mention("alias", "阿舟", source, same_as="name", identity_source_refs=identity_ref),
        mention("other", "林舟", source, start=source.rindex("林舟"), identity="new", distinct_from=["name"], identity_source_refs=difference_ref),
    ]}
    cold = segment(("u1", "user", source))
    batch = form_grounded_memory_batch(cold, Model(output))
    assert batch.mentions[0].identity == "current_user"
    assert batch.mentions[1].same_as == batch.mentions[0].id
    assert batch.mentions[2].identity == "new"
    assert batch.mentions[2].distinct_from == (batch.mentions[0].id,)
    assert validate_persisted_grounded_memory_batch(batch, cold)


def test_unsupported_identity_keeps_literal_mention_unresolved():
    source = '我的同学说“我叫阿林”。'
    output = {"units": [], "mentions": [mention(
        "quoted", "阿林", source, identity="current_user",
        identity_source_refs=[{"turn_id": "u1", "supporting_span": source}],
    )]}
    decisions = {"units": [], "mentions": [{"supported": True, "identity_supported": False}]}
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), Model(output, decisions))
    assert batch.mentions[0].surface == "阿林"
    assert batch.mentions[0].identity == "unresolved"


def test_extract_checkpoint_survives_verifier_failure_and_skips_successful_call():
    source = "Project River is active"
    cold = segment(("u1", "user", source))
    output = {"mentions": [mention("p", "Project River", source)], "units": [unit(source, "Project River", "is", "active", source, subject_mention="p")]}
    stages = {}
    model = Model(output, fail_verifier=True)
    with pytest.raises(FormationError, match="formation_verification_failed"):
        form_grounded_memory_batch(cold, model, checkpoint=lambda stage, payload: stages.__setitem__(stage, payload))
    assert set(stages) == {"extracted"}
    retry = Model(None)
    batch = form_grounded_memory_batch(cold, retry, extracted_checkpoint=stages["extracted"], checkpoint=lambda stage, payload: stages.__setitem__(stage, payload))
    assert len(retry.calls) == 1
    assert set(stages) == {"extracted", "verified"}
    restarted = Model(None, fail_verifier=True)
    assert form_grounded_memory_batch(cold, restarted, verified_checkpoint=stages["verified"]) == batch
    assert restarted.calls == []


def test_checkpoint_write_failure_never_runs_next_model_stage():
    source = "项目R怎么样？"
    model = Model({"units": [], "mentions": [mention("p", "项目R", source)]})
    def fail_checkpoint(stage, payload):
        raise OSError("disk write failed")
    with pytest.raises(OSError, match="disk write failed"):
        form_grounded_memory_batch(segment(("u1", "user", source)), model, checkpoint=fail_checkpoint)
    assert len(model.calls) == 1


@pytest.mark.parametrize("bad_result", [{}, {"units": [], "mentions": []}, {"units": [], "mentions": [{"supported": 1, "identity_supported": True}]}])
def test_malformed_verification_cannot_masquerade_as_empty_success(bad_result):
    source = "项目R怎么样？"
    model = Model({"units": [], "mentions": [mention("p", "项目R", source)]}, bad_result)
    with pytest.raises(FormationError, match="formation_verification_failed"):
        form_grounded_memory_batch(segment(("u1", "user", source)), model)


def test_wrong_mention_offset_is_a_visible_extraction_failure():
    source = "项目R怎么样？项目R还有预算吗？"
    bad = mention("p", "项目R", source)
    bad["source_start"] = 1
    model = Model({"units": [], "mentions": [bad]})
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), model)
    assert batch.mentions == batch.units == ()
    assert [(issue.candidate, issue.index, issue.status) for issue in batch.issues] == [("mention", 0, "pending")]
    assert len(model.calls) == 1


def test_unique_surface_repairs_model_character_count_without_guessing():
    source = "项目R怎么样？"
    bad = mention("p", "项目R", source)
    bad.update(source_start=1, source_end=7)
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), Model({"units": [], "mentions": [bad]}))
    assert (batch.mentions[0].source_start, batch.mentions[0].source_end) == (0, 3)


def test_occurrence_ordinals_distinguish_same_name_different_people():
    source = "林舟喜欢茶，另一个林舟喜欢咖啡。"
    output = {"units": [], "mentions": [
        {"handle": "a", "surface": "林舟", "turn_id": "u1", "occurrence": 0, "identity": "named"},
        {"handle": "b", "surface": "林舟", "turn_id": "u1", "occurrence": 1, "identity": "new", "distinct_from": ["a"], "identity_source_refs": [{"turn_id": "u1", "supporting_span": source}]},
    ]}
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), Model(output))
    first, second = batch.mentions
    assert first.source_start == source.index("林舟")
    assert second.source_start == source.rindex("林舟")
    assert first.id != second.id
    assert second.distinct_from == (first.id,)


@pytest.mark.parametrize("occurrence", [-1, 2, True, "1", 1.0, None])
def test_invalid_occurrence_is_a_visible_failure_even_if_surface_is_real(occurrence):
    source = "林舟喜欢茶，林舟研究陶瓷。"
    model = Model({"units": [], "mentions": [{"handle": "p", "surface": "林舟", "turn_id": "u1", "occurrence": occurrence, "identity": "named"}]})
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), model)
    assert batch.mentions == batch.units == ()
    assert [(issue.candidate, issue.index, issue.status) for issue in batch.issues] == [("mention", 0, "pending")]
    assert len(model.calls) == 1


def test_conflicting_repeated_ordinal_and_legacy_offset_cannot_choose_one():
    source = "林舟喜欢茶，林舟研究陶瓷。"
    item = mention("p", "林舟", source)
    item["occurrence"] = 1
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), Model({"units": [], "mentions": [item]}))
    assert batch.mentions == batch.units == ()
    assert any(issue.candidate == "mention" and issue.status == "pending" for issue in batch.issues)


def test_output_budget_overflow_is_not_a_prefix_success():
    source = "项目R怎么样？"
    model = Model({"units": [], "mentions": [mention(str(index), "项目R", source) for index in range(193)]})
    with pytest.raises(FormationError, match="formation_output_too_large"):
        form_grounded_memory_batch(segment(("u1", "user", source)), model)
    assert len(model.calls) == 1


def test_legacy_unit_id_is_still_readable_and_unchanged():
    source = "张三喜欢茶。"
    cold = segment(("u1", "user", source))
    old = _validate_candidate(unit(source, "张三", "喜欢", "茶", source), cold)
    assert old is not None
    raw = json.loads(json.dumps(serialize_grounded_memory_units((old,))))
    assert deserialize_grounded_memory_units(raw) == (old,)
    assert old.id.startswith("grounded_memory_v1:")


def test_dense_window_exceeds_old_32_unit_limit_without_truncating_mentions():
    sentences = [f"样品Z{index}厚度{100 + index} nm。" for index in range(40)]
    source = "".join(sentences)
    output = {
        "mentions": [mention(str(index), f"样品Z{index}", source) for index in range(40)],
        "units": [unit(sentence, f"样品Z{index}", "厚度", f"{100 + index} nm", sentence, subject_mention=str(index)) for index, sentence in enumerate(sentences)],
    }
    model = Model(output)
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), model)
    assert len(batch.units) == len(batch.mentions) == 40
    assert batch.mentions[-1].surface == "样品Z39"
    assert len(model.calls) == 2
    assert deserialize_grounded_memory_batch(serialize_grounded_memory_batch(batch)) == batch


def test_extracted_checkpoint_is_bound_to_immutable_source_window():
    source = "项目R怎么样？"
    stages = {}
    form_grounded_memory_batch(
        segment(("u1", "user", source)),
        Model({"units": [], "mentions": [mention("p", "项目R", source)]}),
        checkpoint=lambda stage, payload: stages.__setitem__(stage, payload),
    )
    model = Model(None)
    with pytest.raises(FormationError, match="formation_checkpoint_invalid"):
        form_grounded_memory_batch(segment(("u1", "user", "项目R已经取消。")), model, extracted_checkpoint=stages["extracted"])
    assert model.calls == []


def test_malformed_unit_structure_does_not_become_a_valid_empty_result():
    source = "项目R正在推进。"
    candidate = unit(source, "项目R", "正在", "推进", source)
    candidate["subject"] = []
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), Model({"units": [candidate], "mentions": []}))
    assert batch.units == ()
    assert [(issue.candidate, issue.index, issue.status) for issue in batch.issues] == [("unit", 0, "pending")]


def test_model_output_order_cannot_move_new_identity_before_earlier_occurrence():
    source = "林舟负责项目R。另一个林舟住在上海。"
    output = {"units": [], "mentions": [
        mention("new", "林舟", source, start=source.rindex("林舟"), identity="new", identity_source_refs=[{"turn_id": "u1", "supporting_span": "另一个林舟住在上海。"}]),
        mention("old", "林舟", source),
    ]}
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), Model(output))
    assert [item.identity for item in batch.mentions] == ["named", "new"]
    assert [item.source_start for item in batch.mentions] == sorted(item.source_start for item in batch.mentions)


def test_assistant_only_alias_cannot_authorize_an_identity_merge():
    source = "Alex also goes by Zed"
    output = {"units": [], "mentions": [
        mention("a", "Alex", source, turn_id="a1"),
        mention("z", "Zed", source, turn_id="a1", same_as="a", identity_source_refs=[{"turn_id": "a1", "supporting_span": source}]),
    ]}
    batch = form_grounded_memory_batch(segment(("a1", "assistant", source)), Model(output))
    assert len(batch.mentions) == 2
    assert batch.mentions[1].identity == "unresolved"
    assert batch.mentions[1].same_as is None


@pytest.mark.parametrize("wrapper", ["```json\n{}\n```", "```\n{}\n```", " \n```JSON\r\n{}\r\n```\n"])
def test_complete_json_fence_is_only_a_transport_wrapper(wrapper):
    class FencedModel(Model):
        def generate(self, *args, **kwargs):
            return wrapper.format(super().generate(*args, **kwargs))
    source = "项目R怎么样？"
    model = FencedModel({"units": [], "mentions": [mention("p", "项目R", source)]})
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), model)
    assert len(batch.mentions) == 1
    assert len(model.calls) == 2


@pytest.mark.parametrize("wrapper", ["Here is the output:\n```json\n{}\n```", "```json\n{}\n```\nExtra explanation", "```json\n{}\n```\n```json\n{{}}\n```"])
def test_mixed_text_or_multiple_fences_remain_invalid(wrapper):
    class MixedModel(Model):
        def generate(self, *args, **kwargs):
            return wrapper.format(super().generate(*args, **kwargs))
    with pytest.raises(FormationError, match="formation_failed"):
        form_grounded_memory_batch(segment(("u1", "user", "项目R怎么样？")), MixedModel({"units": [], "mentions": []}))


@pytest.mark.parametrize("source", [
    "林舟负责项目R。林舟研究材料K。",
    "林舟负责项目R吗？假设林舟研究材料K。",
])
def test_repeated_identity_span_is_expanded_and_verified_with_full_modality(source):
    output = {"mentions": [
        {"handle": "a", "surface": "林舟", "turn_id": "u1", "occurrence": 0, "identity": "named"},
        {"handle": "b", "surface": "林舟", "turn_id": "u1", "occurrence": 1, "identity": "named", "same_as": "a", "identity_source_refs": [{"turn_id": "u1", "supporting_span": "林舟"}]},
    ], "units": [unit("林舟研究材料K。", "林舟", "研究", "材料K", source, subject_mention="b")]}
    is_hypothesis = "假设" in source
    model = Model(output, {
        "units": [{"supported": not is_hypothesis}],
        "mentions": [{"supported": True, "identity_supported": True}] * 2,
    })
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), model)
    assert len(model.calls) == 2
    refs = batch.mentions[1].identity_source_refs
    assert refs[0].supporting_span == source
    assert model.calls[1][1]["mentions"][1]["identity_source_refs"][0]["supporting_span"] == source
    assert bool(batch.units) is not is_hypothesis


def test_repeated_fact_source_remains_invalid_without_unambiguous_fact_evidence():
    source = "林舟喜欢茶。林舟喜欢茶。"
    candidate = unit("林舟喜欢茶。", "林舟", "喜欢", "茶", "林舟喜欢茶。")
    batch = form_grounded_memory_batch(segment(("u1", "user", source)), Model({"mentions": [], "units": [candidate]}))
    assert batch.units == ()
    assert [(issue.candidate, issue.index, issue.status) for issue in batch.issues] == [("unit", 0, "pending")]
