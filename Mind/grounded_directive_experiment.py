"""Experiment E3: paired Directive authorization contracts.

This module is experiment-only. It reuses the existing bounded activation
runner and changes only the candidate arm's Directive output contract.
"""

from __future__ import annotations

import argparse
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

from core.model_client import ModelClient
from Mind.experiment_a import (
    MAX_MODEL_OUTPUT_CHARS,
    ActivationFailure,
    ActivationInput,
    DecisionIntent,
    Directive,
    ExecutionObservation,
    NoChange,
    run_activation,
)
from Mind.trace import SYSTEM_PROMPT


BASELINE_PROTOCOL = "baseline"
GROUNDED_PROTOCOL = "grounded"
ProtocolName = Literal["baseline", "grounded"]

EVIDENCE_SOURCES = (
    "activation.trigger",
    "activation.execution_goal_snapshot",
    "activation.execution_status",
    "initial_execution_observation.goal",
    "initial_execution_observation.status",
    "initial_execution_observation.recent_outcome",
    "initial_execution_observation.failure",
)
MAX_EVIDENCE_ITEMS = 3
MAX_EVIDENCE_QUOTE_CHARS = 500

_BASELINE_DIRECTIVE_ENVELOPE = '{"type":"directive","text":"..."}'
_GROUNDED_DIRECTIVE_ENVELOPE = (
    '{"type":"directive","text":"...","evidence":'
    '[{"source":"...","quote":"..."}]}'
)
_GROUNDING_SENTENCE = (
    "If you issue a Directive, cite the exact visible evidence that supports "
    "the factual premise of the guidance."
)
_GROUNDING_SCHEMA = (
    "Directive evidence must contain 1 to 3 objects. Each source must be one "
    f"of: {', '.join(EVIDENCE_SOURCES)}. Each quote must be a non-empty exact "
    "substring of that visible source."
)
GROUNDED_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    _BASELINE_DIRECTIVE_ENVELOPE,
    _GROUNDED_DIRECTIVE_ENVELOPE,
).replace(
    "Only request information when the input says information acquisition is allowed.",
    f"{_GROUNDING_SENTENCE}\n{_GROUNDING_SCHEMA}\n"
    "Only request information when the input says information acquisition is allowed.",
)

_ALLOWED_REVIEW_LABELS = {"SUPPORTED", "UNCERTAIN", "CONTRADICTED"}
_SOURCE_SNAPSHOT_PATHS = (
    "Mind/grounded_directive_experiment.py",
    "Mind/test_grounded_directive_experiment.py",
    "Mind/fixtures/e3/manifest.json",
    "Mind/experiment_a.py",
    "Mind/trace.py",
    "core/model_client.py",
)


@dataclass(frozen=True, slots=True)
class E3Case:
    activation: ActivationInput
    execution_observation: ExecutionObservation


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    source: str
    quote: str


SemanticResult = NoChange | Directive | DecisionIntent | ActivationFailure


@dataclass(frozen=True, slots=True)
class ProtocolRun:
    protocol: ProtocolName
    raw_output: str | None
    result: SemanticResult
    grounding_status: Literal["not_applicable", "accepted", "rejected"]
    evidence: tuple[EvidenceCitation, ...]
    model_calls: int
    capability_calls: int
    input_projection_sha256: str | None


@dataclass(frozen=True, slots=True)
class FrozenE3Case:
    case_id: str
    stratum: Literal["clear_problem", "reasonable", "ambiguous"]
    semantic_case: E3Case
    sha256: str


@dataclass(frozen=True, slots=True)
class RegisteredE3Campaign:
    manifest_path: Path
    raw_manifest: bytes
    manifest_sha256: str
    cases: tuple[FrozenE3Case, ...]
    arm_order: tuple[Literal["baseline_first", "candidate_first"], ...]


class ExperimentIntegrityError(RuntimeError):
    pass


class E3Blocked(RuntimeError):
    pass


class _DirectiveNotGrounded(Exception):
    pass


class _ForbiddenMemory:
    def recall(self, query: str, policy: object) -> object:
        raise AssertionError("E3 must not invoke Memory")


class _ProtocolModel:
    def __init__(
        self,
        model: ModelClient,
        protocol: ProtocolName,
        case: E3Case,
    ) -> None:
        self._model = model
        self._protocol = protocol
        self._case = case
        self.model_calls = 0
        self.raw_output: str | None = None
        self.grounding_status: Literal[
            "not_applicable", "accepted", "rejected"
        ] = "not_applicable"
        self.evidence: tuple[EvidenceCitation, ...] = ()
        self.input_projection_sha256: str | None = None

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        self.model_calls += 1
        if self.model_calls != 1:
            raise RuntimeError("E3 permits one model call per arm")
        actual_prompt = (
            system_prompt
            if self._protocol == BASELINE_PROTOCOL
            else GROUNDED_SYSTEM_PROMPT
        )
        self.input_projection_sha256 = _sha256(
            _canonical_json(
                {
                    "recent_context": recent_context,
                    "user_message": user_message,
                }
            )
        )
        raw = self._model.generate(
            recent_context,
            user_message,
            system_prompt=actual_prompt,
        )
        if isinstance(raw, str):
            self.raw_output = raw
        if self._protocol == BASELINE_PROTOCOL:
            return raw
        return self._candidate_projection(raw)

    def _candidate_projection(self, raw: str) -> str:
        if not isinstance(raw, str) or len(raw) > MAX_MODEL_OUTPUT_CHARS:
            return raw
        try:
            payload = json.loads(
                raw,
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
            )
        except (RecursionError, TypeError, ValueError):
            return raw
        if not isinstance(payload, dict) or payload.get("type") != "directive":
            return raw

        evidence = _grounded_evidence(payload, self._case)
        if evidence is None:
            self.grounding_status = "rejected"
            raise _DirectiveNotGrounded
        self.grounding_status = "accepted"
        self.evidence = evidence
        return json.dumps(
            {"type": "directive", "text": payload.get("text")},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def summarize_hot_draft(self, old_summary: object, moved_turns: object) -> str:
        raise AssertionError("E3 must not summarize Hot Draft")


def run_semantic_protocol(
    case: E3Case,
    *,
    model: ModelClient,
    protocol: ProtocolName,
) -> ProtocolRun:
    """Run one frozen E3 arm through the current activation runner."""
    if protocol not in {BASELINE_PROTOCOL, GROUNDED_PROTOCOL}:
        raise ValueError("unsupported E3 protocol")
    if type(case) is not E3Case:
        raise TypeError("case must be E3Case")

    protocol_model = _ProtocolModel(model, protocol, case)
    result = run_activation(
        case.activation,
        model=protocol_model,
        memory_retriever=_ForbiddenMemory(),
        execution_observation=case.execution_observation,
        initial_execution_observation_visible=True,
        allow_information_acquisition=False,
    )
    if protocol_model.grounding_status == "rejected":
        result = ActivationFailure("directive_not_grounded")
    return ProtocolRun(
        protocol=protocol,
        raw_output=protocol_model.raw_output,
        result=result,
        grounding_status=protocol_model.grounding_status,
        evidence=protocol_model.evidence,
        model_calls=protocol_model.model_calls,
        capability_calls=0,
        input_projection_sha256=protocol_model.input_projection_sha256,
    )


def load_registered_campaign(path: str | Path) -> RegisteredE3Campaign:
    """Load and verify the exact preregistered E3 manifest."""
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
            "cases",
            "grounding_contract",
            "model_config",
            "protocols",
            "review_policy",
            "schema",
            "semantic_audit_rubric",
            "single_variable",
            "verdict_criteria",
        }:
            raise ValueError
        if document["schema"] != "mind-e3-preregistered-fixtures-v1":
            raise ValueError
        activation_policy = document["activation_policy"]
        if activation_policy != {
            "allow_information_acquisition": False,
            "initial_execution_observation_visible": True,
            "max_capability_calls": 0,
            "max_model_calls": 1,
        }:
            raise ValueError
        grounding = document["grounding_contract"]
        if not isinstance(grounding, dict) or grounding != {
            "allowed_sources": list(EVIDENCE_SOURCES),
            "failure_code": "directive_not_grounded",
            "matching": "raw_exact_substring_in_declared_host_owned_source",
            "max_evidence_items": MAX_EVIDENCE_ITEMS,
            "max_quote_chars": MAX_EVIDENCE_QUOTE_CHARS,
            "min_evidence_items": 1,
        }:
            raise ValueError
        protocols = document["protocols"]
        if not isinstance(protocols, dict) or set(protocols) != {
            "baseline",
            "candidate",
        }:
            raise ValueError
        if protocols["baseline"] != {
            "directive_schema": _BASELINE_DIRECTIVE_ENVELOPE,
            "system_prompt_sha256": _sha256(SYSTEM_PROMPT.encode("utf-8")),
        } or protocols["candidate"] != {
            "directive_schema": _GROUNDED_DIRECTIVE_ENVELOPE,
            "system_prompt_sha256": _sha256(
                GROUNDED_SYSTEM_PROMPT.encode("utf-8")
            ),
        }:
            raise ValueError
        rubric = document["semantic_audit_rubric"]
        if not isinstance(rubric, dict) or set(rubric) != _ALLOWED_REVIEW_LABELS:
            raise ValueError
        review_policy = document["review_policy"]
        if not isinstance(review_policy, dict) or review_policy != {
            "disagreement": "UNCERTAIN_without_third_reviewer",
            "evidence_declarations_visible": False,
            "reviewers": 2,
            "visible_fields": [
                "opaque_review_id",
                "ActivationInput",
                "ExecutionObservation",
                "Directive_text",
            ],
        }:
            raise ValueError
        raw_cases = document["cases"]
        if not isinstance(raw_cases, list) or len(raw_cases) != 12:
            raise ValueError
        cases = tuple(_registered_case(item) for item in raw_cases)
        if len({case.case_id for case in cases}) != 12:
            raise ValueError
        strata = Counter(case.stratum for case in cases)
        if strata != Counter(
            {"clear_problem": 4, "reasonable": 4, "ambiguous": 4}
        ):
            raise ValueError
        raw_order = document["arm_order"]
        expected_order = [
            "baseline_first" if index % 2 == 0 else "candidate_first"
            for index in range(12)
        ]
        if raw_order != expected_order:
            raise ValueError
        _validate_model_config(document["model_config"])
        _validate_verdict_criteria(document["verdict_criteria"])
    except (KeyError, TypeError, UnicodeDecodeError, ValueError):
        raise ExperimentIntegrityError("invalid_e3_manifest") from None
    return RegisteredE3Campaign(
        manifest_path=target,
        raw_manifest=raw,
        manifest_sha256=_sha256(raw),
        cases=cases,
        arm_order=tuple(raw_order),
    )


def build_preregistration(
    campaign: RegisteredE3Campaign,
) -> dict[str, object]:
    """Freeze all E3 inputs before the first real-model call."""
    if campaign.manifest_path.read_bytes() != campaign.raw_manifest:
        raise ExperimentIntegrityError("registered_manifest_changed")
    repo_root = Path(__file__).resolve().parent.parent
    source_hashes: dict[str, str] = {}
    for relative in _SOURCE_SNAPSHOT_PATHS:
        path = repo_root / PurePosixPath(relative)
        if not path.is_file():
            raise ExperimentIntegrityError("source_snapshot_missing")
        source_hashes[relative] = _file_sha256(path)
    manifest = json.loads(campaign.raw_manifest.decode("utf-8"))
    document: dict[str, object] = {
        "activation_policy": manifest["activation_policy"],
        "arm_order": list(campaign.arm_order),
        "cases": [
            {
                "case_id": case.case_id,
                "case_sha256": case.sha256,
                "stratum": case.stratum,
            }
            for case in campaign.cases
        ],
        "grounding_contract": manifest["grounding_contract"],
        "manifest_sha256": campaign.manifest_sha256,
        "model_config": manifest["model_config"],
        "prompts": {
            "baseline": SYSTEM_PROMPT,
            "baseline_sha256": _sha256(SYSTEM_PROMPT.encode("utf-8")),
            "candidate": GROUNDED_SYSTEM_PROMPT,
            "candidate_sha256": _sha256(
                GROUNDED_SYSTEM_PROMPT.encode("utf-8")
            ),
        },
        "protocols": manifest["protocols"],
        "review_policy": manifest["review_policy"],
        "schema": "mind-e3-preregistration-v1",
        "semantic_audit_rubric": manifest["semantic_audit_rubric"],
        "single_variable": manifest["single_variable"],
        "source_sha256": source_hashes,
        "verdict_criteria": manifest["verdict_criteria"],
    }
    document["preregistration_sha256"] = _sha256(_canonical_json(document))
    return document


def run_campaign(
    campaign: RegisteredE3Campaign,
    *,
    model: ModelClient,
    output_path: str | Path,
) -> dict[str, object]:
    """Run the frozen twelve-pair semantic campaign once."""
    output_root = Path(output_path)
    if output_root.exists():
        raise FileExistsError("E3 output destination already exists")
    output_root.mkdir(parents=True)
    preregistration = build_preregistration(campaign)
    _write_json(output_root / "preregistration.json", preregistration)

    records: list[dict[str, object]] = []
    total_model_calls = 0
    for case, order in zip(campaign.cases, campaign.arm_order, strict=True):
        if build_preregistration(campaign) != preregistration:
            raise ExperimentIntegrityError("preregistration_changed")
        by_protocol: dict[str, ProtocolRun] = {}
        protocols = (
            (BASELINE_PROTOCOL, GROUNDED_PROTOCOL)
            if order == "baseline_first"
            else (GROUNDED_PROTOCOL, BASELINE_PROTOCOL)
        )
        for protocol in protocols:
            run = run_semantic_protocol(
                case.semantic_case,
                model=model,
                protocol=protocol,
            )
            if run.model_calls != 1 or run.capability_calls != 0:
                raise ExperimentIntegrityError("invalid_call_budget")
            total_model_calls += run.model_calls
            by_protocol[protocol] = run
        baseline = by_protocol[BASELINE_PROTOCOL]
        candidate = by_protocol[GROUNDED_PROTOCOL]
        if baseline.input_projection_sha256 != candidate.input_projection_sha256:
            raise ExperimentIntegrityError("paired_input_mismatch")
        records.append(
            {
                "arm_order": order,
                "baseline": _run_document(baseline),
                "candidate": _run_document(candidate),
                "case_id": case.case_id,
                "case_sha256": case.sha256,
                "stratum": case.stratum,
            }
        )
    if total_model_calls != 24:
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
    artifact: dict[str, object] = {
        "blinded_audit_sha256": _file_sha256(audit_path),
        "preregistration": preregistration,
        "review_unblinding": mapping,
        "runs": records,
        "schema": "mind-e3-pending-artifact-v1",
        "summary": _pending_summary(records),
    }
    _write_json(output_root / "e3-pending-artifact.json", artifact)
    return {
        **artifact,
        "pending_artifact_sha256": _file_sha256(
            output_root / "e3-pending-artifact.json"
        ),
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
    baseline = metrics["baseline"]
    candidate = metrics["candidate"]
    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        raise ExperimentIntegrityError("invalid_metrics")
    baseline_supported = _metric_count(baseline, "SUPPORTED")
    baseline_contradicted = _metric_count(baseline, "CONTRADICTED")
    candidate_supported = _metric_count(candidate, "SUPPORTED")
    candidate_contradicted = _metric_count(candidate, "CONTRADICTED")
    candidate_accepted = _metric_count(metrics, "candidate_accepted_directives")
    candidate_supported_grounded = _metric_count(
        metrics,
        "candidate_supported_grounded_directives",
    )
    if (
        candidate_contradicted > baseline_contradicted
        or (baseline_supported >= 2 and candidate_supported == 0)
    ):
        return "EXPERIMENT_E3_NEGATIVE"
    if (
        baseline_contradicted >= 2
        and candidate_contradicted < baseline_contradicted
        and candidate_contradicted <= 1
        and candidate_supported >= 2
        and candidate_accepted >= 2
        and candidate_supported_grounded >= 2
    ):
        return "EXPERIMENT_E3_SUPPORTED"
    return "EXPERIMENT_E3_INCONCLUSIVE"


def _grounded_evidence(
    payload: dict[str, object],
    case: E3Case,
) -> tuple[EvidenceCitation, ...] | None:
    if set(payload) != {"type", "text", "evidence"}:
        return None
    raw_evidence = payload["evidence"]
    if (
        not isinstance(raw_evidence, list)
        or not 1 <= len(raw_evidence) <= MAX_EVIDENCE_ITEMS
    ):
        return None

    source_values: dict[str, str | None] = {
        "activation.trigger": case.activation.trigger,
        "activation.execution_goal_snapshot": (
            case.activation.execution_goal_snapshot
        ),
        "activation.execution_status": case.activation.execution_status,
        "initial_execution_observation.goal": (
            case.execution_observation.goal
        ),
        "initial_execution_observation.status": (
            case.execution_observation.status
        ),
        "initial_execution_observation.recent_outcome": (
            case.execution_observation.recent_outcome
        ),
        "initial_execution_observation.failure": (
            case.execution_observation.failure
        ),
    }
    accepted: list[EvidenceCitation] = []
    for item in raw_evidence:
        if not isinstance(item, dict) or set(item) != {"source", "quote"}:
            return None
        source = item["source"]
        quote = item["quote"]
        if not isinstance(source, str) or source not in EVIDENCE_SOURCES:
            return None
        source_value = source_values[source]
        if not isinstance(source_value, str):
            return None
        if (
            not isinstance(quote, str)
            or not quote.strip()
            or len(quote) > MAX_EVIDENCE_QUOTE_CHARS
            or quote not in source_value
        ):
            return None
        accepted.append(EvidenceCitation(source, quote))
    return tuple(accepted)


def _strict_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate JSON key")
    return dict(pairs)


def _reject_json_constant(_: str) -> object:
    raise ValueError("non-standard JSON constant")


def _registered_case(value: object) -> FrozenE3Case:
    if not isinstance(value, dict) or set(value) != {
        "activation",
        "case_id",
        "case_sha256",
        "execution_observation",
        "stratum",
    }:
        raise ValueError
    case_id = value["case_id"]
    stratum = value["stratum"]
    activation = value["activation"]
    observation = value["execution_observation"]
    if (
        not isinstance(case_id, str)
        or not case_id.strip()
        or stratum not in {"clear_problem", "reasonable", "ambiguous"}
        or not isinstance(activation, dict)
        or set(activation)
        != {"execution_goal_snapshot", "execution_status", "trigger"}
        or not isinstance(observation, dict)
        or set(observation)
        != {"failure", "goal", "recent_outcome", "status"}
    ):
        raise ValueError
    required_values = [*activation.values(), observation["goal"], observation["status"]]
    if any(not isinstance(item, str) or not item.strip() for item in required_values):
        raise ValueError
    if any(
        item is not None and (not isinstance(item, str) or not item.strip())
        for item in (observation["recent_outcome"], observation["failure"])
    ):
        raise ValueError
    hash_input = {
        "activation": activation,
        "case_id": case_id,
        "execution_observation": observation,
        "stratum": stratum,
    }
    expected_hash = _sha256(_canonical_json(hash_input))
    if value["case_sha256"] != expected_hash:
        raise ValueError
    return FrozenE3Case(
        case_id=case_id,
        stratum=stratum,
        semantic_case=E3Case(
            ActivationInput(
                trigger=activation["trigger"],
                execution_goal_snapshot=activation["execution_goal_snapshot"],
                execution_status=activation["execution_status"],
            ),
            ExecutionObservation(
                goal=observation["goal"],
                status=observation["status"],
                recent_outcome=observation["recent_outcome"],
                failure=observation["failure"],
            ),
        ),
        sha256=expected_hash,
    )


def _validate_model_config(value: object) -> None:
    if value != {
        "base_url": "https://api.minimaxi.com/anthropic",
        "max_tokens": 1000,
        "model": "MiniMax-M2.7",
        "provider": "minimax-anthropic",
        "request_timeout_seconds": 30,
        "temperature": "provider_default",
    }:
        raise ValueError


def _validate_verdict_criteria(value: object) -> None:
    if value != {
        "INCONCLUSIVE": "all_other_outcomes",
        "NEGATIVE": {
            "candidate_CONTRADICTED_gt_baseline": True,
            "or_baseline_SUPPORTED_gte_2_and_candidate_SUPPORTED_eq_0": True,
        },
        "SUPPORTED": {
            "baseline_CONTRADICTED_gte": 2,
            "candidate_CONTRADICTED_lt_baseline": True,
            "candidate_CONTRADICTED_lte": 1,
            "candidate_SUPPORTED_gte": 2,
            "candidate_accepted_Directive_gte": 2,
            "candidate_supported_grounded_Directive_gte": 2,
        },
    }:
        raise ValueError


def _run_document(run: ProtocolRun) -> dict[str, object]:
    return {
        "capability_calls": run.capability_calls,
        "evidence": [
            {"quote": item.quote, "source": item.source} for item in run.evidence
        ],
        "grounding_status": run.grounding_status,
        "input_projection_sha256": run.input_projection_sha256,
        "model_calls": run.model_calls,
        "raw_model_output": run.raw_output,
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
    cases: Sequence[FrozenE3Case],
    records: Sequence[Mapping[str, object]],
    rubric: object,
) -> tuple[dict[str, object], list[dict[str, str]]]:
    by_case = {case.case_id: case for case in cases}
    items: list[dict[str, object]] = []
    mapping: list[dict[str, str]] = []
    for record in records:
        case_id = record["case_id"]
        if not isinstance(case_id, str) or case_id not in by_case:
            raise ExperimentIntegrityError("invalid_campaign_record")
        case = by_case[case_id]
        for protocol, arm_key in (
            (BASELINE_PROTOCOL, "baseline"),
            (GROUNDED_PROTOCOL, "candidate"),
        ):
            run = record[arm_key]
            if not isinstance(run, Mapping):
                raise ExperimentIntegrityError("invalid_campaign_record")
            semantic = run["semantic_result"]
            if not isinstance(semantic, Mapping) or semantic.get("type") != "Directive":
                continue
            review_id = "e3r-" + secrets.token_hex(12)
            activation = case.semantic_case.activation
            observation = case.semantic_case.execution_observation
            items.append(
                {
                    "activation": {
                        "execution_goal_snapshot": activation.execution_goal_snapshot,
                        "execution_status": activation.execution_status,
                        "trigger": activation.trigger,
                    },
                    "directive_text": semantic["text"],
                    "execution_observation": {
                        "failure": observation.failure,
                        "goal": observation.goal,
                        "recent_outcome": observation.recent_outcome,
                        "status": observation.status,
                    },
                    "review_id": review_id,
                }
            )
            mapping.append(
                {
                    "case_id": case_id,
                    "protocol": protocol,
                    "review_id": review_id,
                }
            )
    items.sort(key=lambda item: str(item["review_id"]))
    mapping.sort(key=lambda item: item["review_id"])
    return (
        {
            "items": items,
            "rubric": rubric,
            "schema": "mind-e3-blinded-directive-audit-v1",
        },
        mapping,
    )


def _pending_summary(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    baseline_directives = 0
    candidate_directives = 0
    grounded_directives = 0
    grounding_failures = 0
    for record in records:
        baseline = record["baseline"]
        candidate = record["candidate"]
        assert isinstance(baseline, Mapping) and isinstance(candidate, Mapping)
        baseline_result = baseline["semantic_result"]
        candidate_result = candidate["semantic_result"]
        assert isinstance(baseline_result, Mapping)
        assert isinstance(candidate_result, Mapping)
        baseline_directives += baseline_result.get("type") == "Directive"
        candidate_directives += candidate_result.get("type") == "Directive"
        grounded_directives += (
            candidate_result.get("type") == "Directive"
            and candidate.get("grounding_status") == "accepted"
        )
        grounding_failures += (
            candidate_result.get("type") == "ActivationFailure"
            and candidate_result.get("code") == "directive_not_grounded"
        )
    return {
        "baseline_Directive": baseline_directives,
        "candidate_Directive": candidate_directives,
        "candidate_grounded_Directive": grounded_directives,
        "directive_not_grounded": grounding_failures,
        "verdict": "PENDING_BLIND_REVIEW",
    }


def finalize_e3(
    output_path: str | Path,
    *,
    expected_pending_sha256: str,
    reviewer_a: Mapping[str, str],
    reviewer_b: Mapping[str, str],
) -> dict[str, object]:
    """Merge two complete blind audits and apply the frozen verdict rule."""
    output_root = Path(output_path)
    pending_path = output_root / "e3-pending-artifact.json"
    if (
        not isinstance(expected_pending_sha256, str)
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
        Path(__file__).parent / "fixtures" / "e3" / "manifest.json"
    )
    if pending.get("preregistration") != build_preregistration(campaign):
        raise ExperimentIntegrityError("preregistration_changed")
    mapping = pending.get("review_unblinding")
    runs = pending.get("runs")
    if not isinstance(mapping, list) or not isinstance(runs, list):
        raise ExperimentIntegrityError("invalid_pending_artifact")
    review_ids = tuple(item["review_id"] for item in mapping)
    merged = merge_audit_reviews(review_ids, reviewer_a, reviewer_b)
    labels = {
        (item["case_id"], item["protocol"]): merged[item["review_id"]]
        for item in mapping
    }
    metrics: dict[str, object] = {
        "baseline": {label: 0 for label in sorted(_ALLOWED_REVIEW_LABELS)},
        "candidate": {label: 0 for label in sorted(_ALLOWED_REVIEW_LABELS)},
        "candidate_accepted_directives": 0,
        "candidate_supported_grounded_directives": 0,
    }
    for run in runs:
        case_id = run["case_id"]
        for protocol, metric_name, arm_key in (
            (BASELINE_PROTOCOL, "baseline", "baseline"),
            (GROUNDED_PROTOCOL, "candidate", "candidate"),
        ):
            arm = run[arm_key]
            semantic = arm["semantic_result"]
            if semantic["type"] != "Directive":
                continue
            label = labels[(case_id, protocol)]
            arm_metrics = metrics[metric_name]
            assert isinstance(arm_metrics, dict)
            arm_metrics[label] += 1
            if protocol == GROUNDED_PROTOCOL:
                metrics["candidate_accepted_directives"] = (
                    int(metrics["candidate_accepted_directives"]) + 1
                )
                if label == "SUPPORTED" and arm["grounding_status"] == "accepted":
                    metrics["candidate_supported_grounded_directives"] = (
                        int(metrics["candidate_supported_grounded_directives"]) + 1
                    )
    verdict = evaluate_verdict(metrics)
    final = {
        **pending,
        "merged_reviews": merged,
        "metrics": metrics,
        "reviewer_a": dict(reviewer_a),
        "reviewer_b": dict(reviewer_b),
        "schema": "mind-e3-artifact-v1",
        "verdict": verdict,
    }
    _write_json(output_root / "e3-artifact.json", final)
    return final


@contextmanager
def _real_model_environment(campaign: RegisteredE3Campaign):
    del campaign
    raise E3Blocked("E3_BLOCKED:historical_minimax_provider_retired")
    yield  # pragma: no cover - makes this a context manager generator


def run_registered_e3(output_path: str | Path) -> dict[str, object]:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e3" / "manifest.json"
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
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen Mind Experiment E3")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    artifact = run_registered_e3(arguments.output)
    print(
        json.dumps(
            {
                "blinded_audit_path": str(
                    arguments.output / "directive-audit-input.json"
                ),
                "blinded_audit_sha256": artifact["blinded_audit_sha256"],
                "preregistration_sha256": artifact["preregistration"][
                    "preregistration_sha256"
                ],
                "pending_artifact_sha256": artifact[
                    "pending_artifact_sha256"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
