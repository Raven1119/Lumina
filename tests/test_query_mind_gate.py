"""Single-call structured Chat interpretation; no provider or encoder calls."""
from dataclasses import asdict, replace
import json

import pytest

from Conversation_Memory.adapter.graph_read_query import (
    GraphReadQuery, QueryContextTurn, QuerySource, validate_query_intent,
)
from Mind.interfaces import MindDecision
from Mind.llm_gate import LlmQueryMindGate


class CannedModel:
    client_kind = "model"

    def __init__(self, response):
        self.response, self.calls = response, []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls.append((recent_context, user_message, system_prompt))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def source(quote, index=-1, occurrence=0):
    return dict(index=index, quote=quote, occurrence=occurrence)


def interpreted(message="我用的录音机多重？"):
    return dict(recall=True, mode="precise", clues=[
        dict(id="c1", text="我", kind="current_user", sources=[source("我")]),
        dict(id="c2", text="录音机", kind="topic", sources=[source("录音机")]),
    ], relations=[
        dict(subject="c1", predicate="用", object="?entity", sources=[source("我用的录音机")]),
        dict(subject="?entity", predicate="重", object="?value", sources=[source("多重")]),
    ], unresolved=[])


def decide(payload=None, message="我用的录音机多重？", context=None):
    model = CannedModel(json.dumps(payload if payload is not None else interpreted(), ensure_ascii=False))
    return LlmQueryMindGate(model).decide(message, context or []), model


def test_one_call_preserves_original_question_and_unknown_shared_variable():
    result, model = decide()
    assert result.recall and len(model.calls) == 1
    assert result.query.text == "我用的录音机多重？"
    intent = validate_query_intent(result.query)
    assert intent.relations[0].object == intent.relations[1].subject == "?entity"
    assert intent.relations[1].object == "?value"
    assert all(token not in json.dumps(asdict(intent), ensure_ascii=False) for token in ("380", "UUID", "录音机X"))
    assert model.calls[0][0] == []
    assert json.loads(model.calls[0][1])["current_message"] == result.query.text
    assert result.audit["source_check"] == "exact_ranges_not_semantic_validation"


def test_literal_attribute_result_needs_no_entity_reference():
    result, _ = decide()
    assert result.query.intent.relations[1].object == "?value"
    assert "entity_ref" not in json.dumps(asdict(result.query))


def test_short_context_and_occurrence_resolve_actual_offsets():
    payload = dict(recall=True, mode="precise", clues=[dict(
        id="c1", text="Lin", kind="name", sources=[source("Lin", 0, 1)])], relations=[dict(
        subject="c1", predicate="weight", object="?value", sources=[source("weight")])], unresolved=[])
    message = "What is its weight?"
    context = [{"role": "user", "text": "Lin met another Lin."}]
    result, _ = decide(payload, message, context)
    reference = result.query.intent.clues[0].sources[0]
    assert reference.start == 16 and reference.end == 19
    assert result.query.recent_context == (QueryContextTurn("user", context[0]["text"]),)
    assert result.audit["recent_context"] == context
    assert result.query.text == message


def test_valid_unresolved_qualifier_preserves_precise_conditions_and_marks_partial():
    payload = interpreted()
    payload["unresolved"] = ["仅讨论尚未回复许可的情况，不能当作已获同意"]
    result, _ = decide(payload)
    assert result.query.intent.mode == "precise" and len(result.query.intent.relations) == 2
    assert result.query.intent.unresolved == tuple(payload["unresolved"])
    assert result.audit["interpretation_status"] == "partial"
    assert "fallback_reason" not in result.audit


def test_open_uncertainty_does_not_fake_precise_direction():
    payload = dict(recall=True, mode="open", clues=[], relations=[], unresolved=["不确定谁指导谁"])
    result, _ = decide(payload)
    assert result.query.intent.mode == "open" and not result.query.intent.relations
    assert result.query.intent.unresolved and result.recall


@pytest.mark.parametrize("question,subject,obj", [
    ("Who guides Lin?", "?entity", "c1"),
    ("Whom does Lin guide?", "c1", "?entity"),
])
def test_direction_is_preserved_from_the_receipt_without_runtime_guessing(question, subject, obj):
    payload = dict(recall=True, mode="precise", clues=[dict(
        id="c1", text="Lin", kind="name", sources=[source("Lin")])], relations=[dict(
        subject=subject, predicate="guide", object=obj, sources=[source(question)])], unresolved=[])
    result, _ = decide(payload, question)
    assert result.query.intent.relations[0].subject == subject
    assert result.query.intent.relations[0].object == obj


def test_unsupported_predicate_is_not_silently_rewritten_by_gate_validation():
    payload = interpreted()
    payload["relations"][1]["predicate"] = "unknown attribute wording"
    result, _ = decide(payload)
    assert result.query.intent.relations[1].predicate == "unknown attribute wording"
    assert result.audit["interpretation_status"] == "validated_shape"


def test_valid_false_never_constructs_a_memory_query():
    result, model = decide(dict(recall=False, mode="open", clues=[], relations=[], unresolved=[]))
    assert result.recall is False and result.query is None
    assert len(model.calls) == 1 and result.audit["provider_call_attempts"] == 1


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(recall="true"),
    lambda p: p.update(extra="invented"),
    lambda p: p["clues"][0]["sources"][0].update(quote="not present"),
    lambda p: p["clues"][0]["sources"][0].update(occurrence=50),
    lambda p: p["clues"][0].update(text="invented device answer"),
    lambda p: p["relations"][0].update(object="database-uuid"),
    lambda p: p["relations"][1].update(subject="?value"),
    lambda p: p["relations"][0].update(subject="c3"),
    lambda p: p["clues"].append(p["clues"][0]),
    lambda p: p.update(relations=p["relations"] * 2),
])
def test_invalid_description_falls_open_once_without_resampling(mutate):
    payload = interpreted()
    mutate(payload)
    result, model = decide(payload)
    assert result.recall and len(model.calls) == 1
    assert result.query.intent.mode == "open" and not result.query.intent.relations
    assert result.query.text == "我用的录音机多重？"
    assert result.audit["interpretation_status"] == "open_fallback"


@pytest.mark.parametrize("response", ["not json", '{"recall":true,"recall":false}',
                                      RuntimeError("secret provider body")])
def test_malformed_and_provider_errors_have_one_bounded_attempt(response):
    model = CannedModel(response)
    result = LlmQueryMindGate(model).decide("Who helped me?", [])
    assert result.recall and result.query.intent.mode == "open"
    assert len(model.calls) == 1
    assert "secret provider body" not in json.dumps(result.audit)


def test_missing_client_or_oversized_input_falls_open_without_provider():
    missing = LlmQueryMindGate(None).decide("question", [])
    assert missing.audit["provider_call_attempts"] == 0
    model = CannedModel("unused")
    oversized = LlmQueryMindGate(model).decide("x" * 8001, [])
    assert oversized.query.text == "x" * 8001 and not model.calls
    assert oversized.audit["fallback_reason"] == "query_gate_input_limit"


def test_near_context_is_bounded_by_whole_turns_not_rewritten_slices():
    payload = dict(recall=True, mode="open", clues=[], relations=[], unresolved=[])
    context = [{"role": "user", "text": str(i) * 1000} for i in range(14)]
    result, model = decide(payload, context=context)
    sent = json.loads(model.calls[0][1])["recent_context"]
    assert len(sent) <= 12 and sum(len(turn["text"]) for turn in sent) <= 6000
    assert all(any(turn["text"] == original["text"] for original in context) for turn in sent)
    assert result.audit["omitted_recent_turns"] > 0


def test_memory_boundary_rejects_forged_offsets_and_assistant_only_self_binding():
    result, _ = decide()
    query = result.query
    clue = query.intent.clues[0]
    source_ref = clue.sources[0]
    forged = replace(source_ref, start=1, end=2)
    altered = replace(query, intent=replace(query.intent, clues=(replace(clue, sources=(forged,)), query.intent.clues[1])))
    with pytest.raises(ValueError, match="source_mismatch"):
        validate_query_intent(altered)
    quote = QuerySource(0, "我", 0, 0, 1)
    altered = replace(query, recent_context=(QueryContextTurn("assistant", "我说过的"),),
                      intent=replace(query.intent, clues=(replace(clue, sources=(quote,)), query.intent.clues[1])))
    with pytest.raises(ValueError, match="current_user_source_role"):
        validate_query_intent(altered)


def test_legacy_boolean_dto_remains_equal_and_has_no_structured_payload():
    assert MindDecision(True) == MindDecision(recall=True, query=None, audit=None)


def test_v2_does_not_silently_ignore_mixed_legacy_conditions():
    result, _ = decide()
    with pytest.raises(ValueError, match="mixed_contracts"):
        validate_query_intent(replace(result.query, require_all=True))
