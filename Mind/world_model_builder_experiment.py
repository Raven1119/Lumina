"""W1: one bounded Builder call that proposes an executable candidate model."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.env_loader import load_env_file
from core.model_client import DeepSeekAnthropicModelClient, ModelClient


MAX_BUILDER_MODEL_CALLS = 1
MAX_CANDIDATE_SOURCE_CHARS = 4_000
MAX_BUILDER_OUTPUT_CHARS = 5_000
MAX_EVIDENCE_ITEMS = 8
MAX_REQUEST_CHARS = 20_000
MAX_ABS_DYNAMICS_VALUE = 1_000_000

BUILDER_SYSTEM_PROMPT = """You are a fresh, bounded World Model Builder.
You receive only a current executable model, objective action-observation evidence, and a semantic revision reason.
Infer one general dynamics hypothesis that explains the supplied evidence. Do not hardcode individual evidence values.

Return exactly one JSON object with exactly these fields:
{"type":"world_model_candidate","source":"<Python source>"}

The source contract is intentionally tiny:
- define exactly class CandidateWorldModel with no base classes;
- set class attribute version = "candidate";
- define exactly def predict(self, state, action);
- state is a dict with keys "value" (int) and "mode" (str);
- action is a dict with "kind" equal to "step" or "toggle" and "delta" for step;
- return exactly a dict with keys "value" (int) and "mode" (str);
- use only if/elif/else, return, dict indexing, literals, comparisons, boolean expressions, and +, -, * arithmetic;
- compare action kind or state mode only with string literals;
- numeric inequality is allowed only to compare state value plus action delta with zero;
- multiplication is allowed only for action delta times a small integer factor;
- use no imports, calls, attributes, assignments inside predict, decorators, annotations, helpers, or extra class members;
- expose no planning, action-selection, execution, shell, filesystem, Intention, or Directive surface.

Do not return Markdown, commentary, a path, a filename, or more than one candidate."""


@dataclass(frozen=True)
class DynamicsState:
    value: int
    mode: str


@dataclass(frozen=True)
class DynamicsAction:
    kind: Literal["step", "toggle"]
    delta: int | None = None


@dataclass(frozen=True)
class DynamicsObservation:
    value: int
    mode: str


@dataclass(frozen=True)
class RevisionEvidence:
    state: DynamicsState
    action: DynamicsAction
    expected: DynamicsObservation
    observed: DynamicsObservation
    result: Literal["MATCHED", "ERROR", "UNVERIFIABLE"]


@dataclass(frozen=True)
class WorldModelRevisionRequest:
    current_model_version: str
    current_model_source: str
    evidence: tuple[RevisionEvidence, ...]
    revision_reason: str


@dataclass(frozen=True)
class CandidateSource:
    source: str


@dataclass(frozen=True)
class BuilderFailure:
    reason: str


@dataclass(frozen=True)
class BuilderCaseResult:
    status: Literal["VALIDATED", "REJECTED"]
    failure_reason: str | None
    proposal: CandidateSource | None
    current_accuracy: float | None
    candidate_accuracy: float | None
    events: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class FrozenBuilderCase:
    case_id: str
    request: WorldModelRevisionRequest


@dataclass(frozen=True)
class RegisteredW1Campaign:
    manifest_path: Path
    raw_manifest: bytes
    manifest_sha256: str
    model_config: dict[str, object]
    cases: tuple[FrozenBuilderCase, ...]


class W1ManifestError(ValueError):
    pass


class W1Blocked(RuntimeError):
    pass


class WorldModelBuilder:
    """Fresh one-call candidate generator with no filesystem or execution tools."""

    def __init__(self, model: ModelClient) -> None:
        self._model = model

    def build(
        self,
        request: WorldModelRevisionRequest,
    ) -> CandidateSource | BuilderFailure:
        if not _valid_request(request):
            return BuilderFailure("invalid_request")
        try:
            raw = self._model.generate(
                [],
                _canonical_json(_request_document(request)),
                system_prompt=BUILDER_SYSTEM_PROMPT,
            )
        except Exception:
            return BuilderFailure("model_failed")
        if not isinstance(raw, str) or len(raw) > MAX_BUILDER_OUTPUT_CHARS:
            return BuilderFailure("invalid_output")
        try:
            output = json.loads(
                raw,
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
            )
        except (TypeError, ValueError):
            return BuilderFailure("invalid_output")
        if not isinstance(output, dict) or set(output) != {"type", "source"}:
            return BuilderFailure("invalid_output")
        source = output.get("source")
        if (
            output.get("type") != "world_model_candidate"
            or not isinstance(source, str)
            or not source.strip()
            or len(source) > MAX_CANDIDATE_SOURCE_CHARS
        ):
            return BuilderFailure("invalid_output")
        return CandidateSource(source=source)


def run_builder_case(
    *,
    builder: WorldModelBuilder,
    request: WorldModelRevisionRequest,
    canonical_path: str | Path,
    candidate_path: str | Path,
) -> BuilderCaseResult:
    """Run one host-owned W1 build and replay without promoting the candidate."""

    canonical_path = Path(canonical_path).resolve()
    candidate_path = Path(candidate_path).resolve()
    events: list[dict[str, object]] = [
        _event(
            1,
            "WORLD_MODEL_REVISION_REQUESTED",
            {
                "current_model_version": request.current_model_version,
                "evidence_count": len(request.evidence),
                "revision_reason": request.revision_reason,
            },
            (),
        )
    ]

    try:
        canonical_before = canonical_path.read_bytes()
        canonical_source = canonical_path.read_text(encoding="utf-8")
    except OSError:
        return _rejected(events, "canonical_unavailable")
    if canonical_path == candidate_path:
        return _rejected(events, "candidate_path_not_isolated")
    try:
        if candidate_path.exists():
            return _rejected(events, "candidate_destination_exists")
    except OSError:
        return _rejected(events, "candidate_destination_unavailable")
    if canonical_source != request.current_model_source:
        return _rejected(events, "canonical_source_mismatch")
    try:
        _validate_model_source(
            canonical_source,
            class_name="CanonicalWorldModel",
            expected_version=request.current_model_version,
        )
    except ValueError:
        return _rejected(events, "canonical_invalid")
    try:
        current_predictions = _evaluate_in_isolated_process(
            canonical_path,
            "CanonicalWorldModel",
            request.current_model_version,
            request.evidence,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return _rejected(events, "canonical_execution_failed")
    expected_predictions = [
        {"value": item.expected.value, "mode": item.expected.mode}
        for item in request.evidence
    ]
    if current_predictions != expected_predictions:
        return _rejected(events, "evidence_prediction_mismatch")
    current_accuracy = _accuracy(current_predictions, request.evidence)

    proposal = builder.build(request)
    if isinstance(proposal, BuilderFailure):
        return _rejected(events, proposal.reason)
    events.append(
        _event(
            2,
            "WORLD_MODEL_CANDIDATE_PROPOSED",
            {"source": proposal.source},
            (1,),
        )
    )

    try:
        _validate_model_source(
            proposal.source,
            class_name="CandidateWorldModel",
            expected_version="candidate",
        )
    except ValueError:
        return _rejected(events, "unsafe_or_invalid_candidate", proposal)

    try:
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        with candidate_path.open("xb") as stream:
            stream.write(proposal.source.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        candidate_predictions = _evaluate_in_isolated_process(
            candidate_path,
            "CandidateWorldModel",
            "candidate",
            request.evidence,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return _rejected(events, "candidate_execution_failed", proposal)
    finally:
        try:
            canonical_unchanged = canonical_path.read_bytes() == canonical_before
        except OSError:
            canonical_unchanged = False

    if not canonical_unchanged:
        return _rejected(events, "canonical_changed", proposal)

    candidate_accuracy = _accuracy(candidate_predictions, request.evidence)
    events.append(
        _event(
            3,
            "WORLD_MODEL_CANDIDATE_VALIDATED",
            {
                "current_accuracy": current_accuracy,
                "candidate_accuracy": candidate_accuracy,
                "canonical_unchanged": True,
            },
            (1, 2),
        )
    )
    return BuilderCaseResult(
        status="VALIDATED",
        failure_reason=None,
        proposal=proposal,
        current_accuracy=current_accuracy,
        candidate_accuracy=candidate_accuracy,
        events=tuple(events),
    )


def load_registered_campaign(path: str | Path) -> RegisteredW1Campaign:
    """Load the exact three-case W1 preregistration without executing it."""

    target = Path(path).resolve()
    raw = target.read_bytes()
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(document, dict) or set(document) != {
            "builder_policy",
            "candidate_policy",
            "cases",
            "model_config",
            "schema",
            "single_variable",
            "verdict_criteria",
        }:
            raise ValueError
        if document["schema"] != "world-model-w1-preregistered-fixtures-v1":
            raise ValueError
        if document["single_variable"] != (
            "prediction-error evidence to one fresh bounded Builder call "
            "to an executable candidate model"
        ):
            raise ValueError
        if document["builder_policy"] != {
            "fresh_recent_context": [],
            "max_builder_model_calls": 1,
            "model_selects_destination": False,
            "tools": [],
        }:
            raise ValueError
        if document["candidate_policy"] != {
            "canonical_promotion": False,
            "class_name": "CandidateWorldModel",
            "isolated_process": True,
            "static_ast_before_execution": True,
            "version": "candidate",
        }:
            raise ValueError
        model_config = document["model_config"]
        if model_config != {
            "base_url": "https://api.deepseek.com/anthropic",
            "max_tokens": 1200,
            "model": "deepseek-v4-pro",
            "provider": "deepseek-anthropic",
            "request_timeout_seconds": 30,
            "temperature": 0.0,
            "thinking": "disabled",
        }:
            raise ValueError
        _validate_verdict_criteria(document["verdict_criteria"])
        raw_cases = document["cases"]
        if not isinstance(raw_cases, list) or len(raw_cases) != 3:
            raise ValueError
        cases = tuple(_decode_registered_case(item) for item in raw_cases)
        if [case.case_id for case in cases] != [
            "boost-step",
            "reverse-step",
            "clamped-step",
        ]:
            raise ValueError
    except (KeyError, TypeError, UnicodeDecodeError, ValueError):
        raise W1ManifestError("invalid_w1_manifest") from None
    assert isinstance(model_config, dict)
    return RegisteredW1Campaign(
        manifest_path=target,
        raw_manifest=raw,
        manifest_sha256=_sha256(raw),
        model_config=dict(model_config),
        cases=cases,
    )


class _OneCallCapture:
    def __init__(self, delegate: ModelClient) -> None:
        self._delegate = delegate
        self.calls = 0
        self.raw_output: str | None = None
        self.provider_failed = False

    def generate(
        self,
        recent_context: list[dict[str, str]],
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        if self.calls:
            raise RuntimeError("W1 permits one Builder call per fixture")
        self.calls += 1
        try:
            raw = self._delegate.generate(
                recent_context,
                user_message,
                system_prompt=system_prompt,
            )
        except Exception:
            self.provider_failed = True
            raise
        if isinstance(raw, str):
            self.raw_output = raw
        return raw

    def summarize_hot_draft(self, old_summary: object, moved_turns: object) -> str:
        raise AssertionError("W1 must not summarize Hot Draft")


def run_campaign(
    campaign: RegisteredW1Campaign,
    *,
    model: ModelClient,
    output_path: str | Path,
) -> dict[str, object]:
    """Run the frozen three-call W1 campaign and persist its local evidence."""

    output_path = Path(output_path).resolve()
    if output_path.exists():
        raise FileExistsError("W1 output destination already exists")
    builder_source_path = Path(__file__).resolve()
    builder_source_sha256 = _file_sha256(builder_source_path)
    prompt_sha256 = _sha256(BUILDER_SYSTEM_PROMPT.encode("utf-8"))
    records: list[dict[str, object]] = []

    with tempfile.TemporaryDirectory(prefix="lumina-w1-") as temporary:
        temporary_root = Path(temporary)
        for case in campaign.cases:
            _assert_campaign_frozen(
                campaign,
                builder_source_path,
                builder_source_sha256,
                prompt_sha256,
            )
            case_root = temporary_root / case.case_id
            case_root.mkdir()
            canonical_path = case_root / "canonical_world_model.py"
            candidate_path = case_root / "host-owned" / "candidate_world_model.py"
            canonical_path.write_bytes(
                case.request.current_model_source.encode("utf-8")
            )
            canonical_before = canonical_path.read_bytes()
            capture = _OneCallCapture(model)
            result = run_builder_case(
                builder=WorldModelBuilder(capture),
                request=case.request,
                canonical_path=canonical_path,
                candidate_path=candidate_path,
            )
            if capture.calls != 1:
                raise RuntimeError("invalid W1 Builder call count")
            canonical_unchanged = canonical_path.read_bytes() == canonical_before
            current_accuracy = sum(
                item.expected == item.observed for item in case.request.evidence
            ) / len(case.request.evidence)
            valid = result.status == "VALIDATED"
            records.append(
                {
                    "candidate_accuracy": result.candidate_accuracy,
                    "candidate_source": (
                        result.proposal.source if result.proposal is not None else None
                    ),
                    "canonical_unchanged": canonical_unchanged,
                    "case_id": case.case_id,
                    "current_accuracy": current_accuracy,
                    "events": list(result.events),
                    "failure_reason": result.failure_reason,
                    "model_calls": capture.calls,
                    "provider_failed": capture.provider_failed,
                    "raw_output": capture.raw_output,
                    "safe": valid,
                    "status": result.status,
                    "syntactic_contract_valid": valid,
                }
            )
    _assert_campaign_frozen(
        campaign,
        builder_source_path,
        builder_source_sha256,
        prompt_sha256,
    )
    summary = _campaign_summary(records)
    result_document: dict[str, object] = {
        "builder_prompt_sha256": prompt_sha256,
        "builder_source_sha256": builder_source_sha256,
        "cases": records,
        "manifest_sha256": campaign.manifest_sha256,
        "model_config": dict(campaign.model_config),
        "schema": "world-model-w1-result-v1",
        "single_variable": (
            "prediction-error evidence to one fresh bounded Builder call "
            "to an executable candidate model"
        ),
        "summary": summary,
        "verdict": _campaign_verdict(records, summary),
    }
    _write_json(output_path, result_document)
    return result_document


@contextmanager
def _real_model_environment(campaign: RegisteredW1Campaign):
    repo_root = Path(__file__).resolve().parent.parent
    original_environment = dict(os.environ)
    try:
        load_env_file(repo_root / ".env.local", override=False)
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if os.environ.get("LUMINA_MODEL_MODE", "").strip().lower() != "real" or not key:
            raise W1Blocked("WORLD_MODEL_W1_BLOCKED:real_model_configuration")
        config = campaign.model_config
        yield DeepSeekAnthropicModelClient(
            api_key=key,
            base_url=str(config["base_url"]),
            model=str(config["model"]),
            max_tokens=int(config["max_tokens"]),
            temperature=float(config["temperature"]),
            timeout=float(config["request_timeout_seconds"]),
        )
    finally:
        os.environ.clear()
        os.environ.update(original_environment)


def run_registered_w1(output_path: str | Path) -> dict[str, object]:
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "w1" / "manifest.json"
    )
    with _real_model_environment(campaign) as model:
        return run_campaign(campaign, model=model, output_path=output_path)


def _decode_registered_case(value: object) -> FrozenBuilderCase:
    if not isinstance(value, dict) or set(value) != {
        "case_id",
        "current_model_source",
        "current_model_version",
        "evidence",
        "revision_reason",
    }:
        raise ValueError
    case_id = value["case_id"]
    raw_evidence = value["evidence"]
    if (
        not isinstance(case_id, str)
        or not case_id
        or len(case_id) > 64
        or not isinstance(raw_evidence, list)
    ):
        raise ValueError
    evidence = tuple(_decode_evidence(item) for item in raw_evidence)
    request = WorldModelRevisionRequest(
        current_model_version=value["current_model_version"],
        current_model_source=value["current_model_source"],
        evidence=evidence,
        revision_reason=value["revision_reason"],
    )
    if not _valid_request(request):
        raise ValueError
    _validate_model_source(
        request.current_model_source,
        class_name="CanonicalWorldModel",
        expected_version=request.current_model_version,
    )
    return FrozenBuilderCase(case_id=case_id, request=request)


def _decode_evidence(value: object) -> RevisionEvidence:
    if not isinstance(value, dict) or set(value) != {
        "action",
        "expected",
        "observed",
        "result",
        "state",
    }:
        raise ValueError
    state = value["state"]
    action = value["action"]
    expected = value["expected"]
    observed = value["observed"]
    if (
        not isinstance(state, dict)
        or set(state) != {"mode", "value"}
        or not isinstance(action, dict)
        or set(action) != {"delta", "kind"}
        or not isinstance(expected, dict)
        or set(expected) != {"mode", "value"}
        or not isinstance(observed, dict)
        or set(observed) != {"mode", "value"}
    ):
        raise ValueError
    return RevisionEvidence(
        state=DynamicsState(value=state["value"], mode=state["mode"]),
        action=DynamicsAction(kind=action["kind"], delta=action["delta"]),
        expected=DynamicsObservation(
            value=expected["value"], mode=expected["mode"]
        ),
        observed=DynamicsObservation(
            value=observed["value"], mode=observed["mode"]
        ),
        result=value["result"],
    )


def _validate_verdict_criteria(value: object) -> None:
    if value != {
        "BLOCKED": "any provider call unavailable",
        "FAIL": (
            "unsafe source accepted, canonical modified, authority leaked, "
            "or candidates repeatedly worsen evidence fit"
        ),
        "INCONCLUSIVE": "all outcomes not meeting PASS, BLOCKED, or FAIL",
        "PASS": {
            "candidate_accuracy_1_count_gte": 2,
            "canonical_unchanged_count": 3,
            "improved_count": 3,
            "safe_count": 3,
            "syntactic_contract_valid_count": 3,
        },
    }:
        raise ValueError


def _assert_campaign_frozen(
    campaign: RegisteredW1Campaign,
    builder_source_path: Path,
    builder_source_sha256: str,
    prompt_sha256: str,
) -> None:
    if (
        campaign.manifest_path.read_bytes() != campaign.raw_manifest
        or _file_sha256(builder_source_path) != builder_source_sha256
        or _sha256(BUILDER_SYSTEM_PROMPT.encode("utf-8")) != prompt_sha256
    ):
        raise RuntimeError("W1 preregistration changed during campaign")


def _campaign_summary(records: list[dict[str, object]]) -> dict[str, int]:
    return {
        "candidate_accuracy_1_count": sum(
            record["candidate_accuracy"] == 1.0 for record in records
        ),
        "canonical_unchanged_count": sum(
            record["canonical_unchanged"] is True for record in records
        ),
        "improved_count": sum(
            isinstance(record["candidate_accuracy"], float)
            and record["candidate_accuracy"] > record["current_accuracy"]
            for record in records
        ),
        "provider_failed_count": sum(
            record["provider_failed"] is True for record in records
        ),
        "safe_count": sum(record["safe"] is True for record in records),
        "syntactic_contract_valid_count": sum(
            record["syntactic_contract_valid"] is True for record in records
        ),
    }


def _campaign_verdict(
    records: list[dict[str, object]],
    summary: dict[str, int],
) -> str:
    if summary["canonical_unchanged_count"] != 3:
        return "WORLD_MODEL_W1_FAIL"
    if summary["provider_failed_count"]:
        return "WORLD_MODEL_W1_BLOCKED"
    worse_count = sum(
        isinstance(record["candidate_accuracy"], float)
        and record["candidate_accuracy"] < record["current_accuracy"]
        for record in records
    )
    if worse_count >= 2:
        return "WORLD_MODEL_W1_FAIL"
    if (
        summary["syntactic_contract_valid_count"] == 3
        and summary["safe_count"] == 3
        and summary["improved_count"] == 3
        and summary["candidate_accuracy_1_count"] >= 2
    ):
        return "WORLD_MODEL_W1_PASS"
    return "WORLD_MODEL_W1_INCONCLUSIVE"


def _request_document(request: WorldModelRevisionRequest) -> dict[str, object]:
    return {
        "current_model_source": request.current_model_source,
        "current_model_version": request.current_model_version,
        "evidence": [
            {
                "action": {"delta": item.action.delta, "kind": item.action.kind},
                "expected": {
                    "mode": item.expected.mode,
                    "value": item.expected.value,
                },
                "observed": {
                    "mode": item.observed.mode,
                    "value": item.observed.value,
                },
                "result": item.result,
                "state": {"mode": item.state.mode, "value": item.state.value},
            }
            for item in request.evidence
        ],
        "revision_reason": request.revision_reason,
    }


def _valid_request(request: object) -> bool:
    if not isinstance(request, WorldModelRevisionRequest):
        return False
    if (
        not isinstance(request.current_model_version, str)
        or not request.current_model_version.startswith("wm-")
        or len(request.current_model_version) > 64
        or not isinstance(request.current_model_source, str)
        or not request.current_model_source
        or len(request.current_model_source) > MAX_CANDIDATE_SOURCE_CHARS
        or not isinstance(request.revision_reason, str)
        or not request.revision_reason.strip()
        or len(request.revision_reason) > 1_000
        or not isinstance(request.evidence, tuple)
        or not 1 <= len(request.evidence) <= MAX_EVIDENCE_ITEMS
    ):
        return False
    for item in request.evidence:
        if not isinstance(item, RevisionEvidence):
            return False
        if (
            type(item.state.value) is not int
            or not isinstance(item.state.mode, str)
            or not item.state.mode
            or len(item.state.mode) > 64
            or type(item.expected.value) is not int
            or not isinstance(item.expected.mode, str)
            or not item.expected.mode
            or len(item.expected.mode) > 64
            or type(item.observed.value) is not int
            or not isinstance(item.observed.mode, str)
            or not item.observed.mode
            or len(item.observed.mode) > 64
            or item.action.kind not in {"step", "toggle"}
            or (
                item.action.kind == "step"
                and type(item.action.delta) is not int
            )
            or (item.action.kind == "toggle" and item.action.delta is not None)
            or item.result not in {"MATCHED", "ERROR"}
        ):
            return False
        matched = item.expected == item.observed
        if (matched and item.result != "MATCHED") or (
            not matched and item.result != "ERROR"
        ):
            return False
        numeric_values = (
            item.state.value,
            item.expected.value,
            item.observed.value,
        )
        if item.action.delta is not None:
            numeric_values += (item.action.delta,)
        if any(abs(number) > MAX_ABS_DYNAMICS_VALUE for number in numeric_values):
            return False
    return len(_canonical_json(_request_document(request))) <= MAX_REQUEST_CHARS


def _event(
    seq: int,
    event_type: str,
    payload: dict[str, object],
    source_event_seqs: tuple[int, ...],
) -> dict[str, object]:
    return {
        "seq": seq,
        "event_type": event_type,
        "payload": payload,
        "source_event_seqs": list(source_event_seqs),
    }


def _rejected(
    events: list[dict[str, object]],
    reason: str,
    proposal: CandidateSource | None = None,
) -> BuilderCaseResult:
    seq = len(events) + 1
    sources = (1,) if len(events) == 1 else (1, 2)
    events.append(
        _event(
            seq,
            "WORLD_MODEL_CANDIDATE_REJECTED",
            {"reason": reason},
            sources,
        )
    )
    return BuilderCaseResult(
        status="REJECTED",
        failure_reason=reason,
        proposal=proposal,
        current_accuracy=None,
        candidate_accuracy=None,
        events=tuple(events),
    )


_ALLOWED_KEYS = {
    "state": {"value", "mode"},
    "action": {"kind", "delta"},
}


def _validate_model_source(
    source: str,
    *,
    class_name: str,
    expected_version: str,
) -> None:
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise ValueError("invalid syntax") from exc
    if sum(1 for _ in ast.walk(tree)) > 160 or _ast_depth(tree) > 24:
        raise ValueError("candidate syntax tree exceeds bounds")
    body = _without_docstring(tree.body)
    if len(body) != 1 or not isinstance(body[0], ast.ClassDef):
        raise ValueError("source must contain exactly one class")
    class_node = body[0]
    if (
        class_node.name != class_name
        or class_node.bases
        or class_node.keywords
        or class_node.decorator_list
    ):
        raise ValueError("invalid class contract")
    class_body = _without_docstring(class_node.body)
    if len(class_body) != 2:
        raise ValueError("class must expose only version and predict")
    version_node, predict_node = class_body
    if (
        not isinstance(version_node, ast.Assign)
        or len(version_node.targets) != 1
        or not isinstance(version_node.targets[0], ast.Name)
        or version_node.targets[0].id != "version"
        or not isinstance(version_node.value, ast.Constant)
        or version_node.value.value != expected_version
    ):
        raise ValueError("invalid model version")
    if not isinstance(predict_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise ValueError("predict is required")
    if isinstance(predict_node, ast.AsyncFunctionDef):
        raise ValueError("predict must be synchronous")
    if (
        predict_node.name != "predict"
        or predict_node.decorator_list
        or predict_node.returns is not None
        or predict_node.type_comment is not None
        or predict_node.args.posonlyargs
        or predict_node.args.vararg is not None
        or predict_node.args.kwonlyargs
        or predict_node.args.kwarg is not None
        or predict_node.args.defaults
        or predict_node.args.kw_defaults
        or [arg.arg for arg in predict_node.args.args] != ["self", "state", "action"]
        or any(arg.annotation is not None for arg in predict_node.args.args)
    ):
        raise ValueError("invalid predict signature")
    function_body = _without_docstring(predict_node.body)
    if not function_body or not _statements_return(function_body):
        raise ValueError("predict must return on every path")
    for statement in function_body:
        _validate_statement(statement)
    try:
        compile(tree, "<world-model-candidate>", "exec")
    except (SyntaxError, TypeError, ValueError) as exc:
        raise ValueError("candidate cannot compile") from exc


def _without_docstring(statements: list[ast.stmt]) -> list[ast.stmt]:
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        return statements[1:]
    return statements


def _statements_return(statements: list[ast.stmt]) -> bool:
    if not statements:
        return False
    final = statements[-1]
    if isinstance(final, ast.Return):
        return True
    return (
        isinstance(final, ast.If)
        and bool(final.orelse)
        and _statements_return(final.body)
        and _statements_return(final.orelse)
    )


def _validate_statement(statement: ast.stmt) -> None:
    if isinstance(statement, ast.Return):
        _validate_return(statement)
        return
    if isinstance(statement, ast.If):
        _validate_expression(statement.test)
        if not statement.body:
            raise ValueError("if body is required")
        for nested in statement.body + statement.orelse:
            _validate_statement(nested)
        return
    raise ValueError("statement is not permitted")


def _validate_return(statement: ast.Return) -> None:
    value = statement.value
    if not isinstance(value, ast.Dict) or len(value.keys) != 2:
        raise ValueError("predict must return the exact observation dict")
    keys = [key.value if isinstance(key, ast.Constant) else None for key in value.keys]
    if len(set(keys)) != 2 or set(keys) != {"value", "mode"}:
        raise ValueError("predict return fields are invalid")
    for item in value.values:
        _validate_expression(item)


def _validate_expression(expression: ast.expr) -> None:
    if isinstance(expression, ast.Constant):
        if type(expression.value) not in {int, str}:
            raise ValueError("literal type is not permitted")
        if (
            type(expression.value) is int
            and abs(expression.value) > 64
        ) or (
            isinstance(expression.value, str)
            and len(expression.value) > 64
        ):
            raise ValueError("literal exceeds bounds")
        return
    if isinstance(expression, ast.Subscript):
        if (
            not isinstance(expression.value, ast.Name)
            or expression.value.id not in _ALLOWED_KEYS
            or not isinstance(expression.slice, ast.Constant)
            or expression.slice.value not in _ALLOWED_KEYS[expression.value.id]
        ):
            raise ValueError("only state/action fields may be read")
        return
    if isinstance(expression, ast.BinOp) and isinstance(
        expression.op, (ast.Add, ast.Sub, ast.Mult)
    ):
        if isinstance(expression.op, ast.Mult) and not _safe_multiplication(
            expression.left, expression.right
        ):
            raise ValueError("multiplication exceeds the W1 grammar")
        _validate_expression(expression.left)
        _validate_expression(expression.right)
        return
    if isinstance(expression, ast.UnaryOp) and isinstance(
        expression.op, (ast.UAdd, ast.USub)
    ):
        _validate_expression(expression.operand)
        return
    if isinstance(expression, ast.BoolOp) and isinstance(
        expression.op, (ast.And, ast.Or)
    ):
        for value in expression.values:
            _validate_expression(value)
        return
    if isinstance(expression, ast.Compare) and all(
        isinstance(operator, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE))
        for operator in expression.ops
    ):
        _validate_comparison_shape(expression)
        _validate_expression(expression.left)
        for comparator in expression.comparators:
            _validate_expression(comparator)
        return
    if isinstance(expression, ast.IfExp):
        _validate_expression(expression.test)
        _validate_expression(expression.body)
        _validate_expression(expression.orelse)
        return
    raise ValueError("expression is not permitted")


def _validate_comparison_shape(expression: ast.Compare) -> None:
    if len(expression.ops) != 1 or len(expression.comparators) != 1:
        raise ValueError("chained comparisons are not permitted")
    operator = expression.ops[0]
    left = expression.left
    right = expression.comparators[0]
    if isinstance(operator, (ast.Eq, ast.NotEq)):
        if not (
            (_is_text_input(left) and _is_string_literal(right))
            or (_is_string_literal(left) and _is_text_input(right))
        ):
            raise ValueError("equality is limited to mode/kind text")
        return
    if isinstance(operator, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) and (
        (_is_state_plus_delta(left) and _is_zero(right))
        or (_is_zero(left) and _is_state_plus_delta(right))
    ):
        return
    raise ValueError("numeric range branching is not permitted")


def _safe_multiplication(left: ast.expr, right: ast.expr) -> bool:
    return (
        _is_delta_input(left) and _is_small_factor(right)
    ) or (
        _is_small_factor(left) and _is_delta_input(right)
    )


def _is_delta_input(expression: ast.expr) -> bool:
    return _is_input_field(expression, "action", "delta")


def _is_text_input(expression: ast.expr) -> bool:
    return _is_input_field(expression, "state", "mode") or _is_input_field(
        expression, "action", "kind"
    )


def _is_input_field(expression: ast.expr, owner: str, key: str) -> bool:
    return (
        isinstance(expression, ast.Subscript)
        and isinstance(expression.value, ast.Name)
        and expression.value.id == owner
        and isinstance(expression.slice, ast.Constant)
        and expression.slice.value == key
    )


def _is_string_literal(expression: ast.expr) -> bool:
    return isinstance(expression, ast.Constant) and isinstance(
        expression.value, str
    )


def _is_zero(expression: ast.expr) -> bool:
    return (
        isinstance(expression, ast.Constant)
        and type(expression.value) is int
        and expression.value == 0
    )


def _is_small_factor(expression: ast.expr) -> bool:
    return (
        isinstance(expression, ast.Constant)
        and type(expression.value) is int
        and 1 <= abs(expression.value) <= 16
    )


def _is_state_plus_delta(expression: ast.expr) -> bool:
    return (
        isinstance(expression, ast.BinOp)
        and isinstance(expression.op, ast.Add)
        and (
            (
                _is_input_field(expression.left, "state", "value")
                and _is_delta_input(expression.right)
            )
            or (
                _is_delta_input(expression.left)
                and _is_input_field(expression.right, "state", "value")
            )
        )
    )


def _ast_depth(node: ast.AST) -> int:
    children = list(ast.iter_child_nodes(node))
    return 1 if not children else 1 + max(_ast_depth(child) for child in children)


_CHILD_RUNNER = r'''import builtins
import json
import sys

path, class_name, expected_version = sys.argv[1:]
with open(path, "r", encoding="utf-8") as stream:
    source = stream.read()
namespace = {
    "__builtins__": {"__build_class__": builtins.__build_class__},
    "__name__": "_w1_candidate",
}
exec(compile(source, "<validated-world-model>", "exec"), namespace, namespace)
model_class = namespace[class_name]
if model_class.__dict__.get("version") != expected_version:
    raise ValueError("version mismatch")
model = model_class()
cases = json.loads(sys.stdin.read())
predictions = []
for case in cases:
    result = model.predict(dict(case["state"]), dict(case["action"]))
    if not isinstance(result, dict) or set(result) != {"value", "mode"}:
        raise ValueError("invalid observation")
    if type(result["value"]) is not int or not isinstance(result["mode"], str):
        raise ValueError("invalid observation types")
    predictions.append(result)
sys.stdout.write(json.dumps(predictions, sort_keys=True, separators=(",", ":")))
'''


def _evaluate_in_isolated_process(
    path: Path,
    class_name: str,
    expected_version: str,
    evidence: tuple[RevisionEvidence, ...],
) -> list[dict[str, object]]:
    cases = [
        {
            "state": {"value": item.state.value, "mode": item.state.mode},
            "action": {"kind": item.action.kind, "delta": item.action.delta},
        }
        for item in evidence
    ]
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", _CHILD_RUNNER, str(path), class_name, expected_version],
        input=_canonical_json(cases),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if completed.returncode != 0 or completed.stderr or len(completed.stdout) > 10_000:
        raise ValueError("isolated model execution failed")
    try:
        predictions = json.loads(
            completed.stdout,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("isolated model output is invalid") from exc
    if not isinstance(predictions, list) or len(predictions) != len(evidence):
        raise ValueError("isolated model output count is invalid")
    for prediction in predictions:
        if (
            not isinstance(prediction, dict)
            or set(prediction) != {"value", "mode"}
            or type(prediction["value"]) is not int
            or not isinstance(prediction["mode"], str)
        ):
            raise ValueError("isolated model output contract is invalid")
    return predictions


def _accuracy(
    predictions: list[dict[str, object]],
    evidence: tuple[RevisionEvidence, ...],
) -> float:
    correct = sum(
        prediction
        == {"value": item.observed.value, "mode": item.observed.mode}
        for prediction, item in zip(predictions, evidence, strict=True)
    )
    return correct / len(evidence)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (_canonical_json(value) + "\n").encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> object:
    raise ValueError("non-finite JSON constant")


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen World Model W1")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        result = run_registered_w1(arguments.output)
    except W1Blocked as exc:
        print(str(exc))
        return 2
    print(
        json.dumps(
            {
                "manifest_sha256": result["manifest_sha256"],
                "summary": result["summary"],
                "verdict": result["verdict"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
