from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from Conversation_Memory.adapter.models import MemoryContext
from Mind.experiment_a import (
    ActivationFailure,
    ActivationInput,
    DecisionIntent,
    Directive,
    ExecutionObservation,
    NoChange,
    run_activation,
)
from Mind.grounded_directive_experiment import (
    BASELINE_PROTOCOL,
    EVIDENCE_SOURCES,
    GROUNDED_PROTOCOL,
    GROUNDED_SYSTEM_PROMPT,
    MAX_EVIDENCE_QUOTE_CHARS,
    E3Case,
    E3Blocked,
    ExperimentIntegrityError,
    _real_model_environment,
    evaluate_verdict,
    finalize_e3,
    load_registered_campaign,
    merge_audit_reviews,
    run_campaign,
    run_semantic_protocol,
)
from Mind.trace import SYSTEM_PROMPT


ACTIVATION = ActivationInput(
    trigger="Supervisor review after the latest verification attempt.",
    execution_goal_snapshot="Produce a release candidate with verified checksums.",
    execution_status="in_progress",
)
OBSERVATION = ExecutionObservation(
    goal="Produce a release candidate with verified checksums.",
    status="blocked",
    recent_outcome="The second package was built from the same source tree.",
    failure="Checksum mismatch recurred on the second package.",
)
CASE = E3Case(ACTIVATION, OBSERVATION)


def test_e3_historical_minimax_campaign_cannot_call_a_provider() -> None:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e3" / "manifest.json"
    )

    with pytest.raises(E3Blocked, match="historical_minimax_provider_retired"):
        with _real_model_environment(campaign):
            pytest.fail("retired provider context must not open")


class ScriptedModel:
    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = iter(responses)
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        self.calls.append(
            {
                "recent_context": copy.deepcopy(recent_context),
                "system_prompt": system_prompt,
                "user_message": user_message,
            }
        )
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


class EmptyMemory:
    def recall(self, query: str, policy: object) -> MemoryContext:
        return MemoryContext(query=query)


def _candidate(payload: object) -> object:
    model = ScriptedModel([json.dumps(payload, ensure_ascii=False)])
    return run_semantic_protocol(CASE, model=model, protocol=GROUNDED_PROTOCOL)


def test_e3_h1_valid_exact_span_quote_is_accepted() -> None:
    run = _candidate(
        {
            "type": "directive",
            "text": "Re-evaluate the unchanged packaging assumption.",
            "evidence": [
                {
                    "source": "initial_execution_observation.failure",
                    "quote": "Checksum mismatch recurred",
                }
            ],
        }
    )

    assert run.result == Directive(
        "Re-evaluate the unchanged packaging assumption."
    )
    assert run.grounding_status == "accepted"
    assert [(item.source, item.quote) for item in run.evidence] == [
        (
            "initial_execution_observation.failure",
            "Checksum mismatch recurred",
        )
    ]
    assert run.model_calls == 1
    assert run.capability_calls == 0


def test_e3_h2_invented_quote_fails_as_directive_not_grounded() -> None:
    run = _candidate(
        {
            "type": "directive",
            "text": "Change the packaging assumption.",
            "evidence": [
                {
                    "source": "initial_execution_observation.failure",
                    "quote": "The signing key expired.",
                }
            ],
        }
    )

    assert run.result == ActivationFailure("directive_not_grounded")
    assert run.grounding_status == "rejected"


def test_e3_h3_unlisted_source_fails_as_directive_not_grounded() -> None:
    run = _candidate(
        {
            "type": "directive",
            "text": "Change the packaging assumption.",
            "evidence": [
                {
                    "source": "model.private_reasoning",
                    "quote": "Checksum mismatch recurred",
                }
            ],
        }
    )

    assert run.result == ActivationFailure("directive_not_grounded")


def test_e3_h4_extra_evidence_item_field_is_a_strict_failure() -> None:
    run = _candidate(
        {
            "type": "directive",
            "text": "Change the packaging assumption.",
            "evidence": [
                {
                    "source": "initial_execution_observation.failure",
                    "quote": "Checksum mismatch recurred",
                    "source_text": OBSERVATION.failure,
                }
            ],
        }
    )

    assert run.result == ActivationFailure("directive_not_grounded")


def test_e3_h5_quote_from_a_different_visible_source_fails() -> None:
    run = _candidate(
        {
            "type": "directive",
            "text": "Change the packaging assumption.",
            "evidence": [
                {
                    "source": "activation.trigger",
                    "quote": "Checksum mismatch recurred",
                }
            ],
        }
    )

    assert run.result == ActivationFailure("directive_not_grounded")


def test_e3_h6_zero_evidence_items_fail() -> None:
    run = _candidate(
        {
            "type": "directive",
            "text": "Change the packaging assumption.",
            "evidence": [],
        }
    )

    assert run.result == ActivationFailure("directive_not_grounded")


def test_e3_h7_more_than_three_evidence_items_fail() -> None:
    citation = {
        "source": "initial_execution_observation.failure",
        "quote": "Checksum mismatch recurred",
    }
    run = _candidate(
        {
            "type": "directive",
            "text": "Change the packaging assumption.",
            "evidence": [citation, citation, citation, citation],
        }
    )

    assert run.result == ActivationFailure("directive_not_grounded")


def test_e3_h8_no_change_schema_and_semantics_are_unchanged() -> None:
    run = _candidate({"type": "no_change"})

    assert run.result == NoChange()
    assert run.grounding_status == "not_applicable"
    assert run.evidence == ()


def test_e3_h9_decision_intent_schema_and_semantics_are_unchanged() -> None:
    run = _candidate(
        {"type": "decision_intent", "intent": "suspend_execution"}
    )

    assert run.result == DecisionIntent("suspend_execution")
    assert run.grounding_status == "not_applicable"
    assert run.evidence == ()


@pytest.mark.parametrize(
    "raw",
    [
        '{"type":"no_change"}',
        '{"type":"directive","text":"Maintain the verified direction."}',
        '{"type":"decision_intent","intent":"suspend_execution"}',
        '{"type":"capability_request","capability":"inspect_execution"}',
        '{"type":"directive","text":"x","evidence":[]}',
    ],
)
def test_e3_h10_baseline_is_request_and_semantic_compatible_with_current_contract(
    raw: str,
) -> None:
    e3_model = ScriptedModel([raw])
    current_model = ScriptedModel([raw])

    e3_run = run_semantic_protocol(
        CASE,
        model=e3_model,
        protocol=BASELINE_PROTOCOL,
    )
    current_result = run_activation(
        ACTIVATION,
        model=current_model,
        memory_retriever=EmptyMemory(),
        execution_observation=OBSERVATION,
        initial_execution_observation_visible=True,
        allow_information_acquisition=False,
    )

    assert e3_model.calls == current_model.calls
    assert e3_run.result == current_result
    assert e3_run.raw_output == raw
    assert e3_run.model_calls == 1
    assert e3_run.capability_calls == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "directive", "text": "Change direction."},
        {
            "type": "directive",
            "text": "Change direction.",
            "evidence": [
                {
                    "source": "initial_execution_observation.failure",
                    "quote": "Checksum mismatch recurred",
                }
            ],
            "confidence": 1,
        },
        {
            "type": "directive",
            "text": "Change direction.",
            "evidence": [
                {
                    "source": "initial_execution_observation.failure",
                    "quote": "x" * (MAX_EVIDENCE_QUOTE_CHARS + 1),
                }
            ],
        },
    ],
)
def test_e3_candidate_directive_contract_failures_are_never_no_change(
    payload: object,
) -> None:
    run = _candidate(payload)

    assert run.result == ActivationFailure("directive_not_grounded")
    assert not isinstance(run.result, NoChange)


def test_e3_candidate_prompt_changes_only_the_grounded_directive_contract() -> None:
    expected = SYSTEM_PROMPT.replace(
        '{"type":"directive","text":"..."}',
        '{"type":"directive","text":"...","evidence":'
        '[{"source":"...","quote":"..."}]}',
    ).replace(
        "Only request information when the input says information acquisition is allowed.",
        "If you issue a Directive, cite the exact visible evidence that supports "
        "the factual premise of the guidance.\n"
        "Directive evidence must contain 1 to 3 objects. Each source must be one "
        f"of: {', '.join(EVIDENCE_SOURCES)}. Each quote must be a non-empty exact "
        "substring of that visible source.\n"
        "Only request information when the input says information acquisition is allowed.",
    )

    assert GROUNDED_SYSTEM_PROMPT == expected
    forbidden = ("conservative", "prefer nochange", "inspect first", "double-check")
    assert not any(term in GROUNDED_SYSTEM_PROMPT.lower() for term in forbidden)


def test_e3_manifest_freezes_twelve_new_cases_and_all_campaign_controls() -> None:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e3" / "manifest.json"
    )

    assert len(campaign.cases) == 12
    assert {stratum: sum(case.stratum == stratum for case in campaign.cases)
            for stratum in {"clear_problem", "reasonable", "ambiguous"}} == {
        "clear_problem": 4,
        "reasonable": 4,
        "ambiguous": 4,
    }
    assert campaign.arm_order == tuple(
        "candidate_first" if index % 2 else "baseline_first"
        for index in range(12)
    )
    assert all(len(case.sha256) == 64 for case in campaign.cases)


class CampaignModel:
    client_kind = "model"

    def __init__(self, preregistration_path: Path) -> None:
        self._preregistration_path = preregistration_path
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        assert self._preregistration_path.is_file()
        call = {
            "recent_context": copy.deepcopy(recent_context),
            "system_prompt": system_prompt,
            "user_message": user_message,
        }
        self.calls.append(call)
        visible = json.loads(user_message)
        if system_prompt == GROUNDED_SYSTEM_PROMPT:
            quote = visible["activation"]["trigger"]
            return json.dumps(
                {
                    "type": "directive",
                    "text": "Reassess the current direction.",
                    "evidence": [
                        {"source": "activation.trigger", "quote": quote}
                    ],
                }
            )
        return '{"type":"directive","text":"Reassess the current direction."}'


def test_e3_campaign_freezes_before_calls_and_builds_an_arm_blind_audit(
    tmp_path: Path,
) -> None:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e3" / "manifest.json"
    )
    output = tmp_path / "e3"
    model = CampaignModel(output / "preregistration.json")

    artifact = run_campaign(campaign, model=model, output_path=output)

    assert len(model.calls) == 24
    assert artifact["pending_artifact_sha256"] == hashlib.sha256(
        (output / "e3-pending-artifact.json").read_bytes()
    ).hexdigest()
    assert artifact["summary"] == {
        "baseline_Directive": 12,
        "candidate_Directive": 12,
        "candidate_grounded_Directive": 12,
        "directive_not_grounded": 0,
        "verdict": "PENDING_BLIND_REVIEW",
    }
    for first, second in zip(model.calls[::2], model.calls[1::2], strict=True):
        assert first["user_message"] == second["user_message"]
        assert first["recent_context"] == second["recent_context"]
    audit = json.loads((output / "directive-audit-input.json").read_text())
    assert len(audit["items"]) == 24
    assert set(audit) == {"items", "rubric", "schema"}
    assert all(
        set(item)
        == {"activation", "directive_text", "execution_observation", "review_id"}
        for item in audit["items"]
    )
    assert all(
        len(item["review_id"]) >= 20
        and item["review_id"].startswith("e3r-")
        and not any(
            marker in item["review_id"]
            for marker in ("baseline", "candidate", "grounded", "e3-0")
        )
        for item in audit["items"]
    )


def test_e3_finalization_requires_the_externally_pinned_pending_hash(
    tmp_path: Path,
) -> None:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e3" / "manifest.json"
    )
    output = tmp_path / "e3"
    artifact = run_campaign(
        campaign,
        model=CampaignModel(output / "preregistration.json"),
        output_path=output,
    )
    review_ids = tuple(
        item["review_id"] for item in artifact["review_unblinding"]
    )
    labels = {review_id: "SUPPORTED" for review_id in review_ids}

    with pytest.raises(ExperimentIntegrityError, match="pending_artifact_changed"):
        finalize_e3(
            output,
            expected_pending_sha256="0" * 64,
            reviewer_a=labels,
            reviewer_b=labels,
        )
    final = finalize_e3(
        output,
        expected_pending_sha256=artifact["pending_artifact_sha256"],
        reviewer_a=labels,
        reviewer_b=labels,
    )

    assert final["verdict"] == "EXPERIMENT_E3_INCONCLUSIVE"


def test_e3_review_merge_requires_exact_coverage_and_disagreement_is_uncertain() -> None:
    review_ids = ("r-01", "r-02")

    assert merge_audit_reviews(
        review_ids,
        {"r-01": "SUPPORTED", "r-02": "SUPPORTED"},
        {"r-01": "SUPPORTED", "r-02": "CONTRADICTED"},
    ) == {"r-01": "SUPPORTED", "r-02": "UNCERTAIN"}
    with pytest.raises(ExperimentIntegrityError, match="audit_coverage"):
        merge_audit_reviews(
            review_ids,
            {"r-01": "SUPPORTED"},
            {"r-01": "SUPPORTED", "r-02": "SUPPORTED"},
        )


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        (
            {
                "baseline": {"SUPPORTED": 1, "CONTRADICTED": 3},
                "candidate": {"SUPPORTED": 2, "CONTRADICTED": 1},
                "candidate_accepted_directives": 2,
                "candidate_supported_grounded_directives": 2,
            },
            "EXPERIMENT_E3_SUPPORTED",
        ),
        (
            {
                "baseline": {"SUPPORTED": 1, "CONTRADICTED": 1},
                "candidate": {"SUPPORTED": 2, "CONTRADICTED": 2},
                "candidate_accepted_directives": 2,
                "candidate_supported_grounded_directives": 2,
            },
            "EXPERIMENT_E3_NEGATIVE",
        ),
        (
            {
                "baseline": {"SUPPORTED": 2, "CONTRADICTED": 0},
                "candidate": {"SUPPORTED": 0, "CONTRADICTED": 0},
                "candidate_accepted_directives": 0,
                "candidate_supported_grounded_directives": 0,
            },
            "EXPERIMENT_E3_NEGATIVE",
        ),
        (
            {
                "baseline": {"SUPPORTED": 1, "CONTRADICTED": 1},
                "candidate": {"SUPPORTED": 2, "CONTRADICTED": 0},
                "candidate_accepted_directives": 2,
                "candidate_supported_grounded_directives": 2,
            },
            "EXPERIMENT_E3_INCONCLUSIVE",
        ),
    ],
)
def test_e3_verdict_is_the_frozen_rule(
    metrics: dict[str, object],
    expected: str,
) -> None:
    assert evaluate_verdict(metrics) == expected
