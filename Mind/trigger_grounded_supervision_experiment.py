"""Experiment E4: coarse versus trigger-grounded supervisor evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import secrets
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from core.env_loader import load_env_file
from core.model_client import DeepSeekAnthropicModelClient, ModelClient
from Mind.experiment_a import (
    ActivationFailure,
    ActivationInput,
    DecisionIntent,
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
    MAX_FAILURE_CHARS,
    MAX_GOAL_CHARS,
    MAX_OBSERVATION_CHARS,
    MAX_OUTCOME_CHARS,
    MAX_STATUS_CHARS,
    MAX_SUPERVISOR_BUNDLE_CHARS,
    MAX_SUPERVISOR_EVIDENCE_CHARS,
    MAX_SUPERVISOR_EVIDENCE_ITEMS,
    MAX_SUPERVISOR_SOURCE_REF_CHARS,
    MAX_SUPERVISOR_SOURCE_REFS,
    MAX_SUPERVISOR_TRIGGER_CHARS,
    MAX_TRIGGER_CHARS,
    SUPERVISOR_EVIDENCE_KINDS,
    SUPERVISOR_TRIGGER_TYPES,
    SYSTEM_PROMPT,
    MindTrace,
)


BASELINE_VIEW = "baseline"
CANDIDATE_VIEW = "candidate"
ViewName = Literal["baseline", "candidate"]
SemanticResult = NoChange | Directive | DecisionIntent | ActivationFailure

_ALLOWED_REVIEW_LABELS = {"SUPPORTED", "UNCERTAIN", "CONTRADICTED"}
_COVERAGE = {
    "ambiguous_failure",
    "correct_output_failed_protocol",
    "direction_sound_after_local_failure",
    "missed_existing_artifact",
    "repeated_observable_failure",
    "wrong_completion_assumption",
}
_SOURCE_SNAPSHOT_PATHS = (
    "Mind/trigger_grounded_supervision_experiment.py",
    "Mind/test_trigger_grounded_supervision_experiment.py",
    "Mind/fixtures/e4/manifest.json",
    "Mind/experiment_a.py",
    "Mind/trace.py",
    "core/model_client.py",
)


@dataclass(frozen=True, slots=True)
class E4Case:
    activation: ActivationInput
    execution_observation: ExecutionObservation
    supervisor_evidence: SupervisorEvidenceBundle


@dataclass(frozen=True, slots=True)
class ViewRun:
    view: ViewName
    raw_output: str | None
    result: SemanticResult
    model_calls: int
    capability_calls: int
    model_request: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class FrozenE4Case:
    case_id: str
    coverage: str
    semantic_case: E4Case
    execution_prefix: tuple[Mapping[str, str], ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class RegisteredE4Campaign:
    manifest_path: Path
    raw_manifest: bytes
    manifest_sha256: str
    cases: tuple[FrozenE4Case, ...]
    arm_order: tuple[Literal["baseline_first", "candidate_first"], ...]


class ExperimentIntegrityError(ValueError):
    pass


class E4Blocked(RuntimeError):
    pass


class _ForbiddenMemory:
    def recall(self, query: str, policy: object) -> object:
        raise AssertionError("E4 must not invoke Memory")


class _OneCallModel:
    def __init__(self, delegate: ModelClient) -> None:
        self._delegate = delegate
        self.calls: list[dict[str, object]] = []
        self.raw_output: str | None = None

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        if self.calls:
            raise RuntimeError("E4 permits one model call per arm")
        self.calls.append(
            {
                "recent_context": copy.deepcopy(recent_context),
                "system_prompt": system_prompt,
                "user_message": user_message,
            }
        )
        raw = self._delegate.generate(
            recent_context,
            user_message,
            system_prompt=system_prompt,
        )
        if isinstance(raw, str):
            self.raw_output = raw
        return raw

    def summarize_hot_draft(self, old_summary: object, moved_turns: object) -> str:
        raise AssertionError("E4 must not summarize Hot Draft")


def run_semantic_view(
    case: E4Case,
    *,
    model: ModelClient,
    view: ViewName,
    trace: MindTrace | None = None,
) -> ViewRun:
    """Run one frozen view through the existing one-call cognitive host."""
    if type(case) is not E4Case:
        raise TypeError("case must be E4Case")
    if view not in {BASELINE_VIEW, CANDIDATE_VIEW}:
        raise ValueError("unsupported E4 view")
    capture = _OneCallModel(model)
    if view == BASELINE_VIEW:
        result = run_activation(
            case.activation,
            model=capture,
            memory_retriever=_ForbiddenMemory(),
            execution_observation=case.execution_observation,
            initial_execution_observation_visible=True,
            allow_information_acquisition=False,
            trace=trace,
        )
    else:
        result = run_activation_with_supervisor_evidence(
            case.activation,
            model=capture,
            memory_retriever=_ForbiddenMemory(),
            execution_observation=case.execution_observation,
            supervisor_evidence=case.supervisor_evidence,
            trace=trace,
        )
    return ViewRun(
        view=view,
        raw_output=capture.raw_output,
        result=result,
        model_calls=len(capture.calls),
        capability_calls=0,
        model_request=(
            copy.deepcopy(capture.calls[0]) if capture.calls else None
        ),
    )


def load_registered_campaign(path: str | Path) -> RegisteredE4Campaign:
    """Load the exact twelve-case E4 preregistration manifest."""
    target = Path(path)
    raw = target.read_bytes()
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(document, dict) or set(document) != {
            "activation_policy",
            "arm_order",
            "bounds",
            "cases",
            "evidence_vocabulary",
            "model_config",
            "review_policy",
            "schema",
            "semantic_audit_rubric",
            "single_variable",
            "trigger_vocabulary",
            "verdict_criteria",
            "views",
        }:
            raise ValueError
        if document["schema"] != "mind-e4-preregistered-fixtures-v1":
            raise ValueError
        if document["activation_policy"] != {
            "allow_information_acquisition": False,
            "initial_execution_observation_visible": True,
            "max_capability_calls": 0,
            "max_model_calls": 1,
        }:
            raise ValueError
        if document["bounds"] != _bounds_document():
            raise ValueError
        if document["trigger_vocabulary"] != sorted(
            SUPERVISOR_TRIGGER_TYPES
        ) or document["evidence_vocabulary"] != sorted(
            SUPERVISOR_EVIDENCE_KINDS
        ):
            raise ValueError
        prompt_hash = _sha256(SYSTEM_PROMPT.encode("utf-8"))
        if document["views"] != {
            "baseline": {
                "initial_execution_observation": True,
                "supervisor_evidence": False,
                "system_prompt_sha256": prompt_hash,
            },
            "candidate": {
                "initial_execution_observation": True,
                "supervisor_evidence": True,
                "system_prompt_sha256": prompt_hash,
            },
        }:
            raise ValueError
        _validate_model_config(document["model_config"])
        _validate_review(document)
        _validate_verdict_criteria(document["verdict_criteria"])

        raw_cases = document["cases"]
        if not isinstance(raw_cases, list) or len(raw_cases) != 12:
            raise ValueError
        cases = tuple(_registered_case(item) for item in raw_cases)
        if len({case.case_id for case in cases}) != 12:
            raise ValueError
        if Counter(case.coverage for case in cases) != Counter(
            {coverage: 2 for coverage in _COVERAGE}
        ):
            raise ValueError
        if Counter(
            case.semantic_case.supervisor_evidence.trigger.trigger_type
            for case in cases
        ) != Counter({"completion_rejected": 6, "action_failure": 6}):
            raise ValueError

        raw_order = document["arm_order"]
        expected_order = [
            "baseline_first" if index % 2 == 0 else "candidate_first"
            for index in range(12)
        ]
        if raw_order != expected_order:
            raise ValueError
    except (KeyError, TypeError, UnicodeDecodeError, ValueError):
        raise ExperimentIntegrityError("invalid_e4_manifest") from None
    return RegisteredE4Campaign(
        manifest_path=target,
        raw_manifest=raw,
        manifest_sha256=_sha256(raw),
        cases=cases,
        arm_order=tuple(raw_order),
    )


def build_preregistration(
    campaign: RegisteredE4Campaign,
) -> dict[str, object]:
    """Freeze cases, code, request contracts, rubric, and verdict before calls."""
    if campaign.manifest_path.read_bytes() != campaign.raw_manifest:
        raise ExperimentIntegrityError("registered_manifest_changed")
    repo_root = Path(__file__).resolve().parent.parent
    source_hashes: dict[str, str] = {}
    for relative in _SOURCE_SNAPSHOT_PATHS:
        source = repo_root / PurePosixPath(relative)
        if not source.is_file():
            raise ExperimentIntegrityError("source_snapshot_missing")
        source_hashes[relative] = _file_sha256(source)
    manifest = json.loads(campaign.raw_manifest.decode("utf-8"))
    document: dict[str, object] = {
        "activation_policy": manifest["activation_policy"],
        "arm_order": list(campaign.arm_order),
        "bounds": manifest["bounds"],
        "cases": [
            {
                "case_id": case.case_id,
                "case_sha256": case.sha256,
                "coverage": case.coverage,
            }
            for case in campaign.cases
        ],
        "manifest_sha256": campaign.manifest_sha256,
        "model_config": manifest["model_config"],
        "prompt": SYSTEM_PROMPT,
        "prompt_sha256": _sha256(SYSTEM_PROMPT.encode("utf-8")),
        "review_policy": manifest["review_policy"],
        "schema": "mind-e4-preregistration-v1",
        "semantic_audit_rubric": manifest["semantic_audit_rubric"],
        "single_variable": manifest["single_variable"],
        "source_sha256": source_hashes,
        "trigger_vocabulary": manifest["trigger_vocabulary"],
        "evidence_vocabulary": manifest["evidence_vocabulary"],
        "verdict_criteria": manifest["verdict_criteria"],
        "views": manifest["views"],
    }
    document["preregistration_sha256"] = _sha256(_canonical_json(document))
    return document


def run_campaign(
    campaign: RegisteredE4Campaign,
    *,
    model: ModelClient,
    output_path: str | Path,
) -> dict[str, object]:
    """Run the frozen twelve-pair semantic campaign once, without Execution."""
    output_root = Path(output_path)
    if output_root.exists():
        raise FileExistsError("E4 output destination already exists")
    output_root.mkdir(parents=True)
    traces = output_root / "traces"
    traces.mkdir()
    preregistration = build_preregistration(campaign)
    _write_json(output_root / "preregistration.json", preregistration)

    records: list[dict[str, object]] = []
    total_calls = 0
    for case, order in zip(campaign.cases, campaign.arm_order, strict=True):
        if build_preregistration(campaign) != preregistration:
            raise ExperimentIntegrityError("preregistration_changed")
        by_view: dict[str, ViewRun] = {}
        ordered_views = (
            (BASELINE_VIEW, CANDIDATE_VIEW)
            if order == "baseline_first"
            else (CANDIDATE_VIEW, BASELINE_VIEW)
        )
        for view in ordered_views:
            trace_path = traces / f"{case.case_id}-{view}.jsonl"
            trace = MindTrace.create(
                trace_path,
                activation_id=f"{case.case_id}-{view}",
                fixed_timestamp="2026-08-31T08:00:00.000000Z",
            )
            run = run_semantic_view(
                case.semantic_case,
                model=model,
                view=view,
                trace=trace,
            )
            if run.model_calls != 1 or run.capability_calls != 0:
                raise ExperimentIntegrityError("invalid_call_budget")
            total_calls += run.model_calls
            by_view[view] = run
        baseline = by_view[BASELINE_VIEW]
        candidate = by_view[CANDIDATE_VIEW]
        _assert_request_delta(
            baseline.model_request,
            candidate.model_request,
            case.semantic_case.supervisor_evidence,
        )
        records.append(
            {
                "arm_order": order,
                "baseline": _run_document(baseline),
                "candidate": _run_document(candidate),
                "case_id": case.case_id,
                "case_sha256": case.sha256,
                "coverage": case.coverage,
                "trace_sha256": {
                    view: _file_sha256(
                        traces / f"{case.case_id}-{view}.jsonl"
                    )
                    for view in (BASELINE_VIEW, CANDIDATE_VIEW)
                },
            }
        )
    if total_calls != 24:
        raise ExperimentIntegrityError("invalid_campaign_call_count")
    if build_preregistration(campaign) != preregistration:
        raise ExperimentIntegrityError("preregistration_changed")

    manifest = json.loads(campaign.raw_manifest.decode("utf-8"))
    audit, mapping = _build_blinded_audit(
        campaign.cases,
        records,
        manifest["semantic_audit_rubric"],
    )
    audit_path = output_root / "directive-audit-input.json"
    _write_json(audit_path, audit)
    pending: dict[str, object] = {
        "blinded_audit_sha256": _file_sha256(audit_path),
        "preregistration": preregistration,
        "review_unblinding": mapping,
        "runs": records,
        "schema": "mind-e4-pending-artifact-v1",
        "summary": _pending_summary(records),
    }
    pending_path = output_root / "e4-pending-artifact.json"
    _write_json(pending_path, pending)
    return {
        **pending,
        "pending_artifact_sha256": _file_sha256(pending_path),
    }


def merge_audit_reviews(
    review_ids: Sequence[str],
    reviewer_a: Mapping[str, str],
    reviewer_b: Mapping[str, str],
) -> dict[str, str]:
    expected = set(review_ids)
    if set(reviewer_a) != expected or set(reviewer_b) != expected:
        raise ExperimentIntegrityError("audit_coverage_mismatch")
    if any(
        label not in _ALLOWED_REVIEW_LABELS
        for label in (*reviewer_a.values(), *reviewer_b.values())
    ):
        raise ExperimentIntegrityError("invalid_audit_label")
    return {
        review_id: (
            reviewer_a[review_id]
            if reviewer_a[review_id] == reviewer_b[review_id]
            else "UNCERTAIN"
        )
        for review_id in review_ids
    }


def evaluate_verdict(metrics: Mapping[str, object]) -> str:
    baseline = metrics.get("baseline")
    candidate = metrics.get("candidate")
    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        raise ExperimentIntegrityError("invalid_metrics")
    baseline_supported = _metric_count(baseline, "SUPPORTED")
    baseline_contradicted = _metric_count(baseline, "CONTRADICTED")
    candidate_supported = _metric_count(candidate, "SUPPORTED")
    candidate_contradicted = _metric_count(candidate, "CONTRADICTED")
    candidate_directives = _metric_count(candidate, "Directive")
    improvements = _metric_count(metrics, "paired_semantic_improvements")

    if baseline_contradicted < 2:
        return "EXPERIMENT_E4_INCONCLUSIVE"
    if (
        candidate_contradicted > baseline_contradicted
        or candidate_supported < baseline_supported - 2
    ):
        return "EXPERIMENT_E4_NEGATIVE"
    if (
        candidate_contradicted < baseline_contradicted
        and candidate_contradicted <= 1
        and candidate_supported >= baseline_supported
        and candidate_supported >= 3
        and candidate_directives >= 3
        and improvements >= 2
    ):
        return "EXPERIMENT_E4_SUPPORTED"
    return "EXPERIMENT_E4_INCONCLUSIVE"


def finalize_e4(
    output_path: str | Path,
    *,
    expected_pending_sha256: str,
    reviewer_a: Mapping[str, str],
    reviewer_b: Mapping[str, str],
) -> dict[str, object]:
    """Merge two complete blind reviews and apply the frozen E4 formula."""
    output_root = Path(output_path)
    pending_path = output_root / "e4-pending-artifact.json"
    if (
        type(expected_pending_sha256) is not str
        or len(expected_pending_sha256) != 64
        or _file_sha256(pending_path) != expected_pending_sha256
    ):
        raise ExperimentIntegrityError("pending_artifact_changed")
    pending = json.loads(
        pending_path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_json_constant,
    )
    audit_path = output_root / "directive-audit-input.json"
    if pending.get("blinded_audit_sha256") != _file_sha256(audit_path):
        raise ExperimentIntegrityError("blinded_audit_changed")
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e4" / "manifest.json"
    )
    if pending.get("preregistration") != build_preregistration(campaign):
        raise ExperimentIntegrityError("preregistration_changed")
    mappings = pending.get("review_unblinding")
    runs = pending.get("runs")
    if not isinstance(mappings, list) or not isinstance(runs, list):
        raise ExperimentIntegrityError("invalid_pending_artifact")
    review_ids = tuple(item["review_id"] for item in mappings)
    merged = merge_audit_reviews(review_ids, reviewer_a, reviewer_b)
    labels = {
        (item["case_id"], item["view"]): merged[item["review_id"]]
        for item in mappings
    }
    metrics = _final_metrics(runs, labels)
    verdict = evaluate_verdict(metrics)
    final = {
        **pending,
        "merged_reviews": merged,
        "metrics": metrics,
        "reviewer_a": dict(reviewer_a),
        "reviewer_b": dict(reviewer_b),
        "schema": "mind-e4-artifact-v1",
        "verdict": verdict,
    }
    _write_json(output_root / "e4-artifact.json", final)
    return final


def _registered_case(value: object) -> FrozenE4Case:
    if not isinstance(value, dict) or set(value) != {
        "activation",
        "case_id",
        "case_sha256",
        "coverage",
        "execution_observation",
        "execution_prefix",
        "supervisor_evidence",
    }:
        raise ValueError
    case_id = value["case_id"]
    coverage = value["coverage"]
    activation = value["activation"]
    observation = value["execution_observation"]
    supervisor = value["supervisor_evidence"]
    prefix = value["execution_prefix"]
    if (
        type(case_id) is not str
        or not 1 <= len(case_id) <= 128
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in case_id
        )
        or type(coverage) is not str
        or coverage not in _COVERAGE
        or not isinstance(activation, dict)
        or set(activation)
        != {"execution_goal_snapshot", "execution_status", "trigger"}
        or not isinstance(observation, dict)
        or set(observation)
        != {"failure", "goal", "recent_outcome", "status"}
        or not isinstance(supervisor, dict)
        or set(supervisor)
        != {"execution_observation", "recent_evidence", "trigger"}
        or supervisor["execution_observation"] != observation
        or not isinstance(prefix, list)
        or not 3 <= len(prefix) <= 8
    ):
        raise ValueError
    if (
        not _valid_registered_text(activation["trigger"], MAX_TRIGGER_CHARS)
        or not _valid_registered_text(
            activation["execution_goal_snapshot"],
            MAX_GOAL_CHARS,
        )
        or not _valid_registered_text(
            activation["execution_status"],
            MAX_STATUS_CHARS,
        )
        or not _valid_registered_observation(observation)
    ):
        raise ValueError
    activation_input = ActivationInput(
        trigger=activation["trigger"],
        execution_goal_snapshot=activation["execution_goal_snapshot"],
        execution_status=activation["execution_status"],
    )
    execution_observation = ExecutionObservation(
        goal=observation["goal"],
        status=observation["status"],
        recent_outcome=observation["recent_outcome"],
        failure=observation["failure"],
    )
    trigger_value = supervisor["trigger"]
    evidence_value = supervisor["recent_evidence"]
    if (
        not isinstance(trigger_value, dict)
        or set(trigger_value) != {"source_refs", "summary", "type"}
        or not isinstance(trigger_value["source_refs"], list)
        or not isinstance(evidence_value, list)
    ):
        raise ValueError
    trigger = SupervisorTrigger(
        trigger_type=trigger_value["type"],
        summary=trigger_value["summary"],
        source_refs=tuple(trigger_value["source_refs"]),
    )
    evidence = tuple(
        SupervisorEvidence(kind=item["kind"], text=item["text"])
        for item in evidence_value
        if isinstance(item, dict) and set(item) == {"kind", "text"}
    )
    if len(evidence) != len(evidence_value):
        raise ValueError
    bundle = SupervisorEvidenceBundle(execution_observation, trigger, evidence)
    if not _valid_registered_bundle(bundle):
        raise ValueError

    frozen_prefix: list[Mapping[str, str]] = []
    for item in prefix:
        if (
            not isinstance(item, dict)
            or set(item) != {"event_id", "event_type", "text"}
            or any(type(item[key]) is not str or not item[key].strip() for key in item)
            or len(item["text"]) > MAX_SUPERVISOR_EVIDENCE_CHARS
        ):
            raise ValueError
        frozen_prefix.append(dict(item))
    event_ids = [item["event_id"] for item in frozen_prefix]
    if (
        len(set(event_ids)) != len(event_ids)
        or any(ref not in event_ids for ref in trigger.source_refs)
        or frozen_prefix[-1]["event_id"] not in trigger.source_refs
        or (
            trigger.trigger_type == "completion_rejected"
            and frozen_prefix[-1]["event_type"] != "COMPLETION_REJECTED"
        )
        or (
            trigger.trigger_type == "action_failure"
            and frozen_prefix[-1]["event_type"]
            not in {"TOOL_FAILED", "IPYTHON_EXECUTION_FAILED"}
        )
    ):
        raise ValueError

    hash_input = {key: value[key] for key in value if key != "case_sha256"}
    expected_hash = _sha256(_canonical_json(hash_input))
    if value["case_sha256"] != expected_hash:
        raise ValueError
    return FrozenE4Case(
        case_id=case_id,
        coverage=coverage,
        semantic_case=E4Case(
            activation_input,
            execution_observation,
            bundle,
        ),
        execution_prefix=tuple(frozen_prefix),
        sha256=expected_hash,
    )


def _valid_registered_bundle(bundle: SupervisorEvidenceBundle) -> bool:
    trigger = bundle.trigger
    if (
        type(trigger.trigger_type) is not str
        or trigger.trigger_type not in SUPERVISOR_TRIGGER_TYPES
        or type(trigger.summary) is not str
        or not trigger.summary.strip()
        or trigger.summary != trigger.summary.strip()
        or len(trigger.summary) > MAX_SUPERVISOR_TRIGGER_CHARS
        or not 1 <= len(trigger.source_refs) <= MAX_SUPERVISOR_SOURCE_REFS
        or any(type(ref) is not str for ref in trigger.source_refs)
        or len(set(trigger.source_refs)) != len(trigger.source_refs)
        or any(
            type(ref) is not str
            or not ref.strip()
            or ref != ref.strip()
            or len(ref) > MAX_SUPERVISOR_SOURCE_REF_CHARS
            for ref in trigger.source_refs
        )
        or not 1 <= len(bundle.recent_evidence) <= MAX_SUPERVISOR_EVIDENCE_ITEMS
    ):
        return False
    if any(
        type(item.kind) is not str
        or item.kind not in SUPERVISOR_EVIDENCE_KINDS
        or type(item.text) is not str
        or not item.text.strip()
        or item.text != item.text.strip()
        or len(item.text) > MAX_SUPERVISOR_EVIDENCE_CHARS
        for item in bundle.recent_evidence
    ):
        return False
    return len(
        _canonical_json(_bundle_document(bundle)).decode("utf-8")
    ) <= MAX_SUPERVISOR_BUNDLE_CHARS


def _valid_registered_observation(value: Mapping[str, object]) -> bool:
    if (
        not _valid_registered_text(value["goal"], MAX_GOAL_CHARS)
        or not _valid_registered_text(value["status"], MAX_STATUS_CHARS)
        or not _valid_registered_optional_text(
            value["recent_outcome"],
            MAX_OUTCOME_CHARS,
        )
        or not _valid_registered_optional_text(
            value["failure"],
            MAX_FAILURE_CHARS,
        )
    ):
        return False
    projection = {"capability": "inspect_execution", **value}
    return len(_canonical_json(projection).decode("utf-8")) <= (
        MAX_OBSERVATION_CHARS
    )


def _valid_registered_text(value: object, limit: int) -> bool:
    return (
        type(value) is str
        and bool(value.strip())
        and value == value.strip()
        and len(value) <= limit
    )


def _valid_registered_optional_text(value: object, limit: int) -> bool:
    return value is None or _valid_registered_text(value, limit)


def _assert_request_delta(
    baseline: Mapping[str, object] | None,
    candidate: Mapping[str, object] | None,
    bundle: SupervisorEvidenceBundle,
) -> None:
    if baseline is None or candidate is None:
        raise ExperimentIntegrityError("missing_model_request")
    if (
        baseline.get("system_prompt") != candidate.get("system_prompt")
        or baseline.get("recent_context") != candidate.get("recent_context")
        or set(baseline) != {"recent_context", "system_prompt", "user_message"}
        or set(candidate) != set(baseline)
    ):
        raise ExperimentIntegrityError("paired_request_mismatch")
    baseline_payload = json.loads(str(baseline["user_message"]))
    candidate_payload = json.loads(str(candidate["user_message"]))
    if candidate_payload.pop("supervisor_evidence", None) != (
        _supervisor_block(bundle)
    ) or candidate_payload != baseline_payload:
        raise ExperimentIntegrityError("paired_request_mismatch")


def _run_document(run: ViewRun) -> dict[str, object]:
    return {
        "capability_calls": run.capability_calls,
        "model_calls": run.model_calls,
        "model_request": copy.deepcopy(run.model_request),
        "raw_output": run.raw_output,
        "semantic_result": _result_document(run.result),
    }


def _result_document(result: SemanticResult) -> dict[str, str]:
    if isinstance(result, NoChange):
        return {"type": "NoChange"}
    if isinstance(result, Directive):
        return {"text": result.text, "type": "Directive"}
    if isinstance(result, DecisionIntent):
        return {"intent": result.intent, "type": "DecisionIntent"}
    return {"code": result.code, "type": "ActivationFailure"}


def _build_blinded_audit(
    cases: Sequence[FrozenE4Case],
    records: Sequence[Mapping[str, object]],
    rubric: Mapping[str, str],
) -> tuple[dict[str, object], list[dict[str, str]]]:
    by_id = {case.case_id: case for case in cases}
    items: list[dict[str, object]] = []
    mapping: list[dict[str, str]] = []
    for record in records:
        case_id = str(record["case_id"])
        case = by_id[case_id]
        for view in (BASELINE_VIEW, CANDIDATE_VIEW):
            arm = record[view]
            assert isinstance(arm, Mapping)
            semantic = arm["semantic_result"]
            assert isinstance(semantic, Mapping)
            if semantic["type"] != "Directive":
                continue
            review_id = f"e4r-{secrets.token_hex(12)}"
            items.append(
                {
                    "Directive_text": semantic["text"],
                    "full_ground_truth_supervisor_evidence": _bundle_document(
                        case.semantic_case.supervisor_evidence
                    ),
                    "opaque_review_id": review_id,
                    "rubric": dict(rubric),
                }
            )
            mapping.append(
                {"case_id": case_id, "review_id": review_id, "view": view}
            )
    items.sort(key=lambda item: str(item["opaque_review_id"]))
    mapping.sort(key=lambda item: item["review_id"])
    return (
        {"items": items, "schema": "mind-e4-blind-directive-audit-v1"},
        mapping,
    )


def _pending_summary(
    records: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    summary: dict[str, object] = {}
    for view in (BASELINE_VIEW, CANDIDATE_VIEW):
        counts = Counter(
            record[view]["semantic_result"]["type"]  # type: ignore[index]
            for record in records
        )
        summary[view] = {
            result_type: counts[result_type]
            for result_type in (
                "ActivationFailure",
                "DecisionIntent",
                "Directive",
                "NoChange",
            )
        }
    summary["verdict"] = "PENDING_BLIND_REVIEW"
    return summary


def _final_metrics(
    runs: Sequence[Mapping[str, object]],
    labels: Mapping[tuple[str, str], str],
) -> dict[str, object]:
    metrics: dict[str, object] = {
        view: {
            "ActivationFailure": 0,
            "CONTRADICTED": 0,
            "DecisionIntent": 0,
            "Directive": 0,
            "NoChange": 0,
            "SUPPORTED": 0,
            "UNCERTAIN": 0,
        }
        for view in (BASELINE_VIEW, CANDIDATE_VIEW)
    }
    improvements = 0
    regressions = 0
    for run in runs:
        case_id = str(run["case_id"])
        outcomes: dict[str, tuple[str, str | None]] = {}
        for view in (BASELINE_VIEW, CANDIDATE_VIEW):
            arm = run[view]
            assert isinstance(arm, Mapping)
            semantic = arm["semantic_result"]
            assert isinstance(semantic, Mapping)
            result_type = str(semantic["type"])
            view_metrics = metrics[view]
            assert isinstance(view_metrics, dict)
            view_metrics[result_type] += 1
            label = labels.get((case_id, view))
            if result_type == "Directive":
                if label is None:
                    raise ExperimentIntegrityError("missing_directive_review")
                view_metrics[label] += 1
            outcomes[view] = (result_type, label)
        baseline_type, baseline_label = outcomes[BASELINE_VIEW]
        candidate_type, candidate_label = outcomes[CANDIDATE_VIEW]
        if (
            baseline_type == "Directive"
            and baseline_label in {"CONTRADICTED", "UNCERTAIN"}
            and candidate_type == "Directive"
            and candidate_label == "SUPPORTED"
        ) or (
            baseline_type == "Directive"
            and baseline_label == "CONTRADICTED"
            and candidate_type == "NoChange"
        ):
            improvements += 1
        if (
            candidate_type == "Directive"
            and candidate_label == "CONTRADICTED"
            and baseline_label != "CONTRADICTED"
        ):
            regressions += 1
    metrics["paired_semantic_improvements"] = improvements
    metrics["paired_semantic_regressions"] = regressions
    return metrics


def _validate_review(document: Mapping[str, object]) -> None:
    rubric = document["semantic_audit_rubric"]
    policy = document["review_policy"]
    if not isinstance(rubric, dict) or set(rubric) != _ALLOWED_REVIEW_LABELS:
        raise ValueError
    if policy != {
        "arm_visible": False,
        "disagreement": "UNCERTAIN_without_third_reviewer",
        "full_ground_truth_supervisor_evidence_visible": True,
        "raw_model_metadata_visible": False,
        "reviewers": 2,
    }:
        raise ValueError


def _validate_model_config(value: object) -> None:
    if value != {
        "base_url": "https://api.deepseek.com/anthropic",
        "max_tokens": 1000,
        "model": "deepseek-v4-pro",
        "provider": "deepseek-anthropic",
        "request_timeout_seconds": 30,
        "temperature": "provider_default",
    }:
        raise ValueError


def _validate_verdict_criteria(value: object) -> None:
    if value != {
        "INCONCLUSIVE": "all_other_outcomes_including_baseline_CONTRADICTED_lt_2",
        "NEGATIVE": {
            "candidate_CONTRADICTED_gt_baseline": True,
            "or_candidate_SUPPORTED_lt_baseline_minus": 2,
        },
        "SUPPORTED": {
            "baseline_CONTRADICTED_gte": 2,
            "candidate_CONTRADICTED_lt_baseline": True,
            "candidate_CONTRADICTED_lte": 1,
            "candidate_Directive_gte": 3,
            "candidate_SUPPORTED_gte": 3,
            "candidate_SUPPORTED_gte_baseline": True,
            "paired_semantic_improvements_gte": 2,
        },
    }:
        raise ValueError


def _bounds_document() -> dict[str, int]:
    return {
        "evidence_count": MAX_SUPERVISOR_EVIDENCE_ITEMS,
        "evidence_item_chars": MAX_SUPERVISOR_EVIDENCE_CHARS,
        "source_ref_chars": MAX_SUPERVISOR_SOURCE_REF_CHARS,
        "source_ref_count": MAX_SUPERVISOR_SOURCE_REFS,
        "supervisor_bundle_chars": MAX_SUPERVISOR_BUNDLE_CHARS,
        "trigger_summary_chars": MAX_SUPERVISOR_TRIGGER_CHARS,
    }


def _bundle_document(bundle: SupervisorEvidenceBundle) -> dict[str, object]:
    return {
        "execution_observation": _observation_document(
            bundle.execution_observation
        ),
        "recent_evidence": [
            {"kind": item.kind, "text": item.text}
            for item in bundle.recent_evidence
        ],
        "trigger": {
            "source_refs": list(bundle.trigger.source_refs),
            "summary": bundle.trigger.summary,
            "type": bundle.trigger.trigger_type,
        },
    }


def _supervisor_block(bundle: SupervisorEvidenceBundle) -> dict[str, object]:
    document = _bundle_document(bundle)
    document.pop("execution_observation")
    return document


def _observation_document(
    observation: ExecutionObservation,
) -> dict[str, object]:
    return {
        "failure": observation.failure,
        "goal": observation.goal,
        "recent_outcome": observation.recent_outcome,
        "status": observation.status,
    }


@contextmanager
def _real_model_environment(campaign: RegisteredE4Campaign):
    repo_root = Path(__file__).resolve().parent.parent
    original_environment = dict(os.environ)
    try:
        load_env_file(repo_root / ".env.local", override=False)
        effective = dict(os.environ)
        manifest = json.loads(campaign.raw_manifest.decode("utf-8"))
        expected = manifest["model_config"]
        actual = {
            "base_url": "https://api.deepseek.com/anthropic",
            "model": "deepseek-v4-pro",
            "provider": "deepseek-anthropic",
        }
        if (
            effective.get("LUMINA_MODEL_MODE", "").strip().lower() != "real"
            or not effective.get("DEEPSEEK_API_KEY", "").strip()
            or actual
            != {
                "base_url": expected["base_url"],
                "model": expected["model"],
                "provider": expected["provider"],
            }
        ):
            raise E4Blocked("E4_BLOCKED:real_model_configuration")
        model = DeepSeekAnthropicModelClient(
            api_key=effective["DEEPSEEK_API_KEY"].strip(),
            base_url=expected["base_url"],
            model=expected["model"],
            max_tokens=expected["max_tokens"],
            timeout=float(expected["request_timeout_seconds"]),
        )
        yield model
    finally:
        os.environ.clear()
        os.environ.update(original_environment)


def run_registered_e4(output_path: str | Path) -> dict[str, object]:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e4" / "manifest.json"
    )
    with _real_model_environment(campaign) as model:
        return run_campaign(campaign, model=model, output_path=output_path)


def _metric_count(metrics: Mapping[str, object], key: str) -> int:
    value = metrics.get(key)
    if type(value) is not int or value < 0:
        raise ExperimentIntegrityError("invalid_metrics")
    return value


def _write_json(path: Path, value: object) -> None:
    encoded = _canonical_json(value) + b"\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(encoded)
    os.replace(temporary, path)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _strict_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate JSON key")
    return dict(pairs)


def _reject_json_constant(_: str) -> object:
    raise ValueError("non-standard JSON constant")


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen Mind Experiment E4")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    pending = run_registered_e4(arguments.output)
    print(
        json.dumps(
            {
                "blinded_audit_path": str(
                    arguments.output / "directive-audit-input.json"
                ),
                "blinded_audit_sha256": pending["blinded_audit_sha256"],
                "pending_artifact_sha256": pending[
                    "pending_artifact_sha256"
                ],
                "preregistration_sha256": pending["preregistration"][
                    "preregistration_sha256"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
