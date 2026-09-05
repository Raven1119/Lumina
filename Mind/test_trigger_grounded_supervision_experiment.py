from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from Execution.execution import EventLog
from Mind.experiment_a import (
    ActivationFailure,
    ActivationInput,
    Directive,
    ExecutionObservation,
    NoChange,
    SupervisorEvidence,
    SupervisorEvidenceBundle,
    SupervisorTrigger,
    run_activation,
    run_activation_with_supervisor_evidence,
)
from Mind.trace import (
    SUPERVISOR_EVIDENCE_OBSERVED,
    SYSTEM_PROMPT,
    MindTrace,
    TraceError,
    replay_activation,
)
from Mind.trigger_grounded_supervision_experiment import (
    BASELINE_VIEW,
    CANDIDATE_VIEW,
    E4Case,
    ExperimentIntegrityError,
    evaluate_verdict,
    load_registered_campaign,
    merge_audit_reviews,
    _valid_registered_bundle,
    run_campaign,
    run_semantic_view,
)


FIXED_TIMESTAMP = "2026-08-31T08:00:00.000000Z"
ACTIVATION = ActivationInput(
    trigger="Objective Execution supervision was mechanically triggered.",
    execution_goal_snapshot="Publish the verified release artifact.",
    execution_status="running",
)
OBSERVATION = ExecutionObservation(
    goal="Publish the verified release artifact.",
    status="running",
    recent_outcome="Execution claimed completion after writing release.txt.",
    failure="The completion verifier rejected the claim.",
)
TRIGGER = SupervisorTrigger(
    trigger_type="completion_rejected",
    summary="Completion was rejected because release.txt did not match the frozen specification.",
    source_refs=("event-000004", "event-000005"),
)
EVIDENCE = (
    SupervisorEvidence(
        kind="action_result",
        text="The preceding write reported success for release.txt.",
    ),
    SupervisorEvidence(
        kind="completion_rejected",
        text="Verifier evidence reported content_mismatch for release.txt.",
    ),
)
BUNDLE = SupervisorEvidenceBundle(
    execution_observation=OBSERVATION,
    trigger=TRIGGER,
    recent_evidence=EVIDENCE,
)


class ScriptedModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
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
        return self._responses.pop(0)


class ForbiddenMemory:
    def recall(self, query: str, policy: object) -> object:
        raise AssertionError("E4 must not invoke Memory")


def _baseline(model: ScriptedModel, *, trace: MindTrace | None = None):
    return run_activation(
        ACTIVATION,
        model=model,
        memory_retriever=ForbiddenMemory(),
        execution_observation=OBSERVATION,
        initial_execution_observation_visible=True,
        allow_information_acquisition=False,
        trace=trace,
    )


def _candidate(
    model: ScriptedModel,
    *,
    bundle: object = BUNDLE,
    trace: MindTrace | None = None,
):
    return run_activation_with_supervisor_evidence(
        ACTIVATION,
        model=model,
        memory_retriever=ForbiddenMemory(),
        execution_observation=OBSERVATION,
        supervisor_evidence=bundle,
        trace=trace,
    )


def _expected_baseline_payload() -> dict[str, object]:
    return {
        "activation": {
            "execution_goal_snapshot": ACTIVATION.execution_goal_snapshot,
            "execution_status": ACTIVATION.execution_status,
            "trigger": ACTIVATION.trigger,
        },
        "information_acquisition_allowed": False,
        "initial_execution_observation": {
            "capability": "inspect_execution",
            "failure": OBSERVATION.failure,
            "goal": OBSERVATION.goal,
            "recent_outcome": OBSERVATION.recent_outcome,
            "status": OBSERVATION.status,
        },
    }


def _expected_supervisor_block() -> dict[str, object]:
    return {
        "recent_evidence": [
            {"kind": item.kind, "text": item.text} for item in EVIDENCE
        ],
        "trigger": {
            "source_refs": list(TRIGGER.source_refs),
            "summary": TRIGGER.summary,
            "type": TRIGGER.trigger_type,
        },
    }


def test_e4_h1_baseline_is_exact_current_e2_push_request() -> None:
    direct_model = ScriptedModel(['{"type":"no_change"}'])
    harness_model = ScriptedModel(['{"type":"no_change"}'])

    assert isinstance(_baseline(direct_model), NoChange)
    harness = run_semantic_view(
        E4Case(ACTIVATION, OBSERVATION, BUNDLE),
        model=harness_model,
        view=BASELINE_VIEW,
    )
    expected_call = {
        "recent_context": [],
        "system_prompt": SYSTEM_PROMPT,
        "user_message": json.dumps(
            _expected_baseline_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    }
    assert direct_model.calls == [expected_call]
    assert harness.model_request == expected_call


def test_e4_h2_candidate_only_adds_one_supervisor_evidence_block() -> None:
    baseline_model = ScriptedModel(['{"type":"no_change"}'])
    candidate_model = ScriptedModel(['{"type":"no_change"}'])

    assert isinstance(_baseline(baseline_model), NoChange)
    assert isinstance(_candidate(candidate_model), NoChange)
    baseline_call = baseline_model.calls[0]
    candidate_call = candidate_model.calls[0]
    assert candidate_call["system_prompt"] == baseline_call["system_prompt"]
    assert candidate_call["recent_context"] == baseline_call["recent_context"]

    baseline_payload = json.loads(str(baseline_call["user_message"]))
    candidate_payload = json.loads(str(candidate_call["user_message"]))
    assert candidate_payload.pop("supervisor_evidence") == (
        _expected_supervisor_block()
    )
    assert candidate_payload == baseline_payload


@pytest.mark.parametrize(
    "bundle",
    [
        SupervisorEvidenceBundle(
            execution_observation=OBSERVATION,
            trigger=SupervisorTrigger(
                "completion_rejected",
                "x" * 501,
                ("event-000005",),
            ),
            recent_evidence=EVIDENCE,
        ),
        SupervisorEvidenceBundle(
            execution_observation=OBSERVATION,
            trigger=TRIGGER,
            recent_evidence=(SupervisorEvidence("observation", "x" * 1001),),
        ),
        SupervisorEvidenceBundle(
            execution_observation=ExecutionObservation(
                goal="g" * 1900,
                status="running",
                recent_outcome="o" * 900,
                failure="f" * 400,
            ),
            trigger=TRIGGER,
            recent_evidence=EVIDENCE,
        ),
        SupervisorEvidenceBundle(
            execution_observation=OBSERVATION,
            trigger=SupervisorTrigger(
                "completion_rejected",
                "t" * 500,
                ("event-000005",),
            ),
            recent_evidence=tuple(
                SupervisorEvidence("observation", "e" * 1000)
                for _ in range(3)
            ),
        ),
    ],
)
def test_e4_h3_trigger_evidence_and_bundle_are_hard_bounded(
    bundle: SupervisorEvidenceBundle,
) -> None:
    model = ScriptedModel(['{"type":"no_change"}'])

    result = _candidate(model, bundle=bundle)

    assert isinstance(result, ActivationFailure)
    assert result.code == "invalid_supervisor_evidence"
    assert model.calls == []


def test_e4_h4_raw_execution_objects_are_rejected_before_model_call() -> None:
    model = ScriptedModel(['{"type":"no_change"}'])

    result = _candidate(model, bundle=EventLog())

    assert isinstance(result, ActivationFailure)
    assert result.code == "invalid_supervisor_evidence"
    assert model.calls == []


def test_e4_h4_bundle_must_bind_the_same_execution_observation() -> None:
    mismatched = SupervisorEvidenceBundle(
        execution_observation=ExecutionObservation(
            goal=OBSERVATION.goal,
            status=OBSERVATION.status,
            recent_outcome="A different host summary.",
            failure=OBSERVATION.failure,
        ),
        trigger=TRIGGER,
        recent_evidence=EVIDENCE,
    )
    model = ScriptedModel(['{"type":"no_change"}'])

    result = _candidate(model, bundle=mismatched)

    assert isinstance(result, ActivationFailure)
    assert result.code == "invalid_supervisor_evidence"
    assert model.calls == []


def test_e4_h5_evidence_count_has_a_hard_cap() -> None:
    bundle = SupervisorEvidenceBundle(
        execution_observation=OBSERVATION,
        trigger=TRIGGER,
        recent_evidence=tuple(
            SupervisorEvidence("observation", f"bounded fact {index}")
            for index in range(4)
        ),
    )
    model = ScriptedModel(['{"type":"no_change"}'])

    result = _candidate(model, bundle=bundle)

    assert isinstance(result, ActivationFailure)
    assert result.code == "invalid_supervisor_evidence"
    assert model.calls == []


def test_e4_registered_bundle_uses_the_same_character_bound_as_live_trace() -> None:
    bundle = SupervisorEvidenceBundle(
        execution_observation=ExecutionObservation("g", "running", "o", None),
        trigger=SupervisorTrigger(
            "action_failure",
            "界" * 400,
            ("event-000003",),
        ),
        recent_evidence=tuple(
            SupervisorEvidence("observation", "界" * 800) for _ in range(3)
        ),
    )

    assert _valid_registered_bundle(bundle)


def test_e4_h6_reopen_reconstructs_the_exact_candidate_request(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate.jsonl"
    trace = MindTrace.create(
        path,
        activation_id="e4-candidate",
        fixed_timestamp=FIXED_TIMESTAMP,
    )
    model = ScriptedModel(['{"type":"directive","text":"Re-check the completion assumption."}'])

    result = _candidate(model, trace=trace)
    replay = replay_activation(MindTrace.reopen(path).events)

    assert result == Directive("Re-check the completion assumption.")
    assert replay.model_requests[0].as_model_call() == model.calls[0]
    assert [event.event_type for event in trace.events][2] == (
        SUPERVISOR_EVIDENCE_OBSERVED
    )


def test_e4_h7_corrupt_supervisor_event_fails_conservative(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate.jsonl"
    trace = MindTrace.create(
        path,
        activation_id="e4-corrupt",
        fixed_timestamp=FIXED_TIMESTAMP,
    )
    assert isinstance(
        _candidate(ScriptedModel(['{"type":"no_change"}']), trace=trace),
        NoChange,
    )
    documents = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    documents[2]["payload"]["recent_evidence"][0]["kind"] = "raw_event_log"
    path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
            for item in documents
        ),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(TraceError):
        MindTrace.reopen(path)


@pytest.mark.parametrize(
    "target",
    ("projector_version", "trigger_type", "source_ref", "evidence_kind"),
)
def test_e4_corrupt_unhashable_values_fail_as_trace_error(
    tmp_path: Path,
    target: str,
) -> None:
    path = tmp_path / f"candidate-{target}.jsonl"
    trace = MindTrace.create(
        path,
        activation_id=f"e4-corrupt-{target}",
        fixed_timestamp=FIXED_TIMESTAMP,
    )
    assert isinstance(
        _candidate(ScriptedModel(['{"type":"no_change"}']), trace=trace),
        NoChange,
    )
    documents = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if target == "projector_version":
        documents[0]["payload"]["projector_version"] = {}
    elif target == "trigger_type":
        documents[2]["payload"]["trigger"]["type"] = {}
    elif target == "source_ref":
        documents[2]["payload"]["trigger"]["source_refs"][0] = {}
    else:
        documents[2]["payload"]["recent_evidence"][0]["kind"] = {}
    path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
            for item in documents
        ),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(TraceError):
        MindTrace.reopen(path)


def test_e4_h8_model_cannot_choose_or_replace_host_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate.jsonl"
    trace = MindTrace.create(
        path,
        activation_id="e4-host-owned",
        fixed_timestamp=FIXED_TIMESTAMP,
    )
    model = ScriptedModel(
        [
            '{"type":"directive","text":"Pretend the trigger was a timeout and continue."}'
        ]
    )

    assert isinstance(_candidate(model, trace=trace), Directive)
    evidence_event = trace.events[2]
    assert evidence_event.event_type == SUPERVISOR_EVIDENCE_OBSERVED
    assert evidence_event.payload["trigger"]["type"] == "completion_rejected"
    assert evidence_event.payload["trigger"]["summary"] == TRIGGER.summary


def test_e4_h9_existing_run_activation_signature_and_baseline_semantics_remain() -> None:
    import inspect

    assert set(inspect.signature(run_activation).parameters) == {
        "activation",
        "model",
        "memory_retriever",
        "execution_observation",
        "initial_execution_observation_visible",
        "allow_information_acquisition",
        "trace",
    }
    direct_model = ScriptedModel(['{"type":"directive","text":"Stay at the current direction."}'])
    result = _baseline(direct_model)
    assert result == Directive("Stay at the current direction.")


def test_e4_h10_experiment_surface_has_no_execution_authority() -> None:
    source = Path(__file__).with_name(
        "trigger_grounded_supervision_experiment.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden_names = {
        "ExecutionOrgan",
        "AgentProcess",
        "DecisionFrame",
        "EventLog",
        "PersistentIPython",
        "ToolHost",
        "interrupt",
        "resume",
        "run_goal",
        "spawn_child",
    }

    assert all(not name.startswith("Execution") for name in imported_modules)
    assert "MiniMax" not in source
    assert not (
        forbidden_names
        & {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    )


def test_e4_semantic_view_uses_the_existing_runner_for_both_arms() -> None:
    case = E4Case(ACTIVATION, OBSERVATION, BUNDLE)
    baseline_model = ScriptedModel(['{"type":"no_change"}'])
    candidate_model = ScriptedModel(
        ['{"type":"directive","text":"Reconcile the claim with verifier evidence."}']
    )

    baseline = run_semantic_view(
        case,
        model=baseline_model,
        view=BASELINE_VIEW,
    )
    candidate = run_semantic_view(
        case,
        model=candidate_model,
        view=CANDIDATE_VIEW,
    )

    assert isinstance(baseline.result, NoChange)
    assert candidate.result == Directive(
        "Reconcile the claim with verifier evidence."
    )
    assert baseline.model_calls == candidate.model_calls == 1
    assert baseline.capability_calls == candidate.capability_calls == 0


def test_e4_manifest_freezes_twelve_new_strong_trigger_cases() -> None:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e4" / "manifest.json"
    )

    assert len(campaign.cases) == 12
    assert len({case.case_id for case in campaign.cases}) == 12
    assert [
        case.semantic_case.supervisor_evidence.trigger.trigger_type
        for case in campaign.cases
    ].count("completion_rejected") == 6
    assert [
        case.semantic_case.supervisor_evidence.trigger.trigger_type
        for case in campaign.cases
    ].count("action_failure") == 6
    assert {case.coverage for case in campaign.cases} == {
        "ambiguous_failure",
        "correct_output_failed_protocol",
        "direction_sound_after_local_failure",
        "missed_existing_artifact",
        "repeated_observable_failure",
        "wrong_completion_assumption",
    }
    for case in campaign.cases:
        assert case.execution_prefix[-1]["event_id"] in (
            case.semantic_case.supervisor_evidence.trigger.source_refs
        )


@pytest.mark.parametrize(
    ("target", "invalid_value"),
    (
        ("case_id", "../outside"),
        ("activation_trigger", []),
        ("observation_goal", []),
    ),
)
def test_e4_manifest_rejects_unsafe_ids_and_untyped_context(
    tmp_path: Path,
    target: str,
    invalid_value: object,
) -> None:
    source = Path(__file__).parent / "fixtures" / "e4" / "manifest.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    case = document["cases"][0]
    if target == "case_id":
        case["case_id"] = invalid_value
    elif target == "activation_trigger":
        case["activation"]["trigger"] = invalid_value
    else:
        case["execution_observation"]["goal"] = invalid_value
        case["supervisor_evidence"]["execution_observation"]["goal"] = (
            invalid_value
        )
    hash_input = {key: value for key, value in case.items() if key != "case_sha256"}
    case["case_sha256"] = hashlib.sha256(
        json.dumps(
            hash_input,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    target_path = tmp_path / f"invalid-{target}.json"
    target_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ExperimentIntegrityError):
        load_registered_campaign(target_path)


class _PreregistrationCheckingModel(ScriptedModel):
    def __init__(self, preregistration_path: Path) -> None:
        super().__init__(
            [
                '{"type":"directive","text":"Reconcile the completion claim."}',
                '{"type":"directive","text":"Reconcile the completion evidence."}',
            ]
            + ['{"type":"no_change"}'] * 22
        )
        self._preregistration_path = preregistration_path

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        assert self._preregistration_path.is_file()
        return super().generate(
            recent_context,
            user_message,
            system_prompt=system_prompt,
        )


def test_e4_campaign_freezes_before_calls_and_runs_exactly_24_paired_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e4" / "manifest.json"
    )
    output = tmp_path / "campaign"
    model = _PreregistrationCheckingModel(output / "preregistration.json")
    opaque_ids = iter(("b" * 24, "a" * 24))
    monkeypatch.setattr(
        "Mind.trigger_grounded_supervision_experiment.secrets.token_hex",
        lambda _: next(opaque_ids),
    )

    pending = run_campaign(campaign, model=model, output_path=output)

    assert len(model.calls) == 24
    assert pending["summary"] == {
        "baseline": {
            "ActivationFailure": 0,
            "DecisionIntent": 0,
            "Directive": 1,
            "NoChange": 11,
        },
        "candidate": {
            "ActivationFailure": 0,
            "DecisionIntent": 0,
            "Directive": 1,
            "NoChange": 11,
        },
        "verdict": "PENDING_BLIND_REVIEW",
    }
    assert (output / "directive-audit-input.json").is_file()
    assert (output / "e4-pending-artifact.json").is_file()
    audit = json.loads(
        (output / "directive-audit-input.json").read_text(encoding="utf-8")
    )
    assert len(audit["items"]) == 2
    assert [item["opaque_review_id"] for item in audit["items"]] == sorted(
        item["opaque_review_id"] for item in audit["items"]
    )
    for item in audit["items"]:
        assert set(item) == {
            "Directive_text",
            "full_ground_truth_supervisor_evidence",
            "opaque_review_id",
            "rubric",
        }
    for record in pending["runs"]:
        baseline_payload = json.loads(record["baseline"]["model_request"]["user_message"])
        candidate_payload = json.loads(record["candidate"]["model_request"]["user_message"])
        candidate_payload.pop("supervisor_evidence")
        assert candidate_payload == baseline_payload


def test_e4_blind_review_merge_requires_exact_two_reviewer_coverage() -> None:
    review_ids = ("opaque-a", "opaque-b")

    assert merge_audit_reviews(
        review_ids,
        {"opaque-a": "SUPPORTED", "opaque-b": "CONTRADICTED"},
        {"opaque-a": "SUPPORTED", "opaque-b": "UNCERTAIN"},
    ) == {"opaque-a": "SUPPORTED", "opaque-b": "UNCERTAIN"}
    with pytest.raises(ValueError):
        merge_audit_reviews(
            review_ids,
            {"opaque-a": "SUPPORTED"},
            {"opaque-a": "SUPPORTED", "opaque-b": "UNCERTAIN"},
        )


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        (
            {
                "baseline": {
                    "SUPPORTED": 3,
                    "UNCERTAIN": 0,
                    "CONTRADICTED": 3,
                    "Directive": 6,
                },
                "candidate": {
                    "SUPPORTED": 4,
                    "UNCERTAIN": 0,
                    "CONTRADICTED": 1,
                    "Directive": 5,
                },
                "paired_semantic_improvements": 2,
            },
            "EXPERIMENT_E4_SUPPORTED",
        ),
        (
            {
                "baseline": {
                    "SUPPORTED": 5,
                    "UNCERTAIN": 1,
                    "CONTRADICTED": 1,
                    "Directive": 7,
                },
                "candidate": {
                    "SUPPORTED": 1,
                    "UNCERTAIN": 0,
                    "CONTRADICTED": 2,
                    "Directive": 3,
                },
                "paired_semantic_improvements": 0,
            },
            "EXPERIMENT_E4_INCONCLUSIVE",
        ),
        (
            {
                "baseline": {
                    "SUPPORTED": 5,
                    "UNCERTAIN": 0,
                    "CONTRADICTED": 2,
                    "Directive": 7,
                },
                "candidate": {
                    "SUPPORTED": 2,
                    "UNCERTAIN": 0,
                    "CONTRADICTED": 1,
                    "Directive": 3,
                },
                "paired_semantic_improvements": 1,
            },
            "EXPERIMENT_E4_NEGATIVE",
        ),
    ],
)
def test_e4_verdict_formula_is_frozen(
    metrics: dict[str, object], expected: str
) -> None:
    assert evaluate_verdict(metrics) == expected
