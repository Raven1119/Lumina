"""One-shot harness for Mind Experiment E1 paired continuations.

This module is experiment control code.  Its filesystem and Execution trace
inspection are never exposed as runtime Mind capabilities.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import Mapping
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from threading import Event

from Conversation_Memory.adapter.models import MemoryContext
from Execution import (
    ExecutionOrgan,
    ExecutionResult,
    ExecutionState,
    FileContentEquals,
)
from Execution.execution import (
    DecisionFrame,
    EventLog,
    Model,
    ModelRequest,
    restore_execution_state,
)
from Execution.deepseek_model import DeepSeekModel
from Mind.directive import DirectiveApplication, prepare_for_execution_decision
from Mind.execution_steering_experiment import decision_advisory_from
from Mind.experiment_a import (
    ActivationFailure,
    ActivationInput,
    DecisionIntent,
    Directive,
    ExecutionObservation,
    NoChange,
    run_activation,
)
from Mind.trace import (
    MAX_FAILURE_CHARS,
    MAX_OUTCOME_CHARS,
    PROJECTOR_VERSION,
    PROMPT_VERSION,
    MindTrace,
)


class ExperimentIntegrityError(RuntimeError):
    """A safe failure showing that a paired comparison is not valid."""


class E1Blocked(RuntimeError):
    """A required real provider or frozen campaign precondition is absent."""


_SOURCE_SNAPSHOT_PATHS = (
    "Mind/behavioral_experiment.py",
    "Mind/test_behavioral_experiment.py",
    "Mind/experiment_a.py",
    "Mind/trace.py",
    "Mind/directive.py",
    "Mind/execution_steering_experiment.py",
    "Execution/organ.py",
    "Execution/execution.py",
    "Execution/deepseek_model.py",
    "Execution/ipython_control.py",
    "core/model_client.py",
    "Conversation_Memory/adapter/models.py",
)


@dataclass(frozen=True, slots=True)
class E1Config:
    branch_after_root_decisions: int
    max_additional_root_decisions: int
    max_child_decisions: int
    max_context_chars: int
    wall_time_seconds: float

    def __post_init__(self) -> None:
        integer_fields = (
            self.branch_after_root_decisions,
            self.max_additional_root_decisions,
            self.max_child_decisions,
            self.max_context_chars,
        )
        if any(type(value) is not int or value < 1 for value in integer_fields):
            raise ValueError("E1 integer bounds must be positive")
        if self.max_context_chars < 768:
            raise ValueError("max_context_chars must be at least 768")
        if (
            isinstance(self.wall_time_seconds, bool)
            or not isinstance(self.wall_time_seconds, (int, float))
            or self.wall_time_seconds <= 0
        ):
            raise ValueError("wall_time_seconds must be positive")


@dataclass(frozen=True, slots=True)
class FrozenTaskSpec:
    task_id: str
    description: str
    goal: str
    files: tuple[tuple[str, bytes], ...]
    completion_path: str
    expected_content: str

    def __post_init__(self) -> None:
        text_fields = (
            self.task_id,
            self.description,
            self.goal,
            self.completion_path,
            self.expected_content,
        )
        if any(type(value) is not str or not value for value in text_fields):
            raise ValueError("task text fields must be non-empty strings")
        paths = []
        for relative, content in self.files:
            if type(relative) is not str or type(content) is not bytes:
                raise ValueError("task files must be (str, bytes) pairs")
            _validate_relative_path(relative)
            paths.append(relative)
        _validate_relative_path(self.completion_path)
        if len(paths) != len(set(paths)):
            raise ValueError("task file paths must be unique")
        if self.completion_path in paths:
            raise ValueError("completion target must be absent from the seed")

    @property
    def sha256(self) -> str:
        document = {
            "completion_path": self.completion_path,
            "description": self.description,
            "expected_content": self.expected_content,
            "files": [
                {
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "path": path,
                }
                for path, content in sorted(self.files)
            ],
            "goal": self.goal,
            "schema": "mind-e1-task-v1",
            "task_id": self.task_id,
        }
        return _sha256(_canonical_json(document))


@dataclass(frozen=True, slots=True)
class RegisteredCampaign:
    manifest_path: Path
    manifest_sha256: str
    raw_manifest: bytes
    config: E1Config
    tasks: tuple[FrozenTaskSpec, ...]
    arm_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PrefixEvidence:
    task_sha256: str
    workspace_sha256: str
    event_log_sha256: str
    checkpoint_sha256: str


@dataclass(frozen=True, slots=True)
class FrozenPrefix:
    task: FrozenTaskSpec
    config: E1Config
    root: Path
    workspace: Path
    event_log_path: Path
    checkpoint_path: Path
    evidence: PrefixEvidence
    state: ExecutionState
    request_history: tuple[ModelRequest, ...]
    next_decision_id: str


@dataclass(frozen=True, slots=True)
class BranchStart:
    root: Path
    workspace: Path
    event_log_path: Path
    checkpoint_path: Path
    evidence: PrefixEvidence
    state: ExecutionState
    request_history: tuple[ModelRequest, ...]
    next_decision_id: str


@dataclass(frozen=True, slots=True)
class ForkedBranches:
    baseline: BranchStart
    candidate: BranchStart
    equivalent: bool


@dataclass(frozen=True, slots=True)
class ArmOutcome:
    result: ExecutionResult
    new_frames: tuple[DecisionFrame, ...]
    event_log_bytes: bytes
    elapsed_seconds: float
    additional_root_decisions: int
    verified_completion: bool


MindResult = NoChange | Directive | DecisionIntent | ActivationFailure


@dataclass(frozen=True, slots=True)
class PairedRun:
    branches: ForkedBranches
    baseline: ArmOutcome
    candidate: ArmOutcome
    mind_result: MindResult
    mind_trace: MindTrace
    directive_application: DirectiveApplication | None
    mind_model_calls: int
    budgets_equal: bool
    verifier_equal: bool
    environment_equal: bool


class _PrefixGateModel:
    """Delegate N real decisions, then stop before provider call N+1."""

    def __init__(self, delegate: Model, decision_n: int) -> None:
        self._delegate = delegate
        self._decision_n = decision_n
        self.identifier = delegate.identifier
        if hasattr(delegate, "tool_contracts"):
            self.tool_contracts = getattr(delegate, "tool_contracts")
        self.calls = 0
        self.delegated_calls = 0
        self.boundary_reached = Event()
        self.release_boundary = Event()
        self.intercepted_request: ModelRequest | None = None

    def decide(self, request: ModelRequest) -> object:
        self.calls += 1
        if self.calls <= self._decision_n:
            self.delegated_calls += 1
            return self._delegate.decide(request)
        self.intercepted_request = request
        self.boundary_reached.set()
        if not self.release_boundary.wait(timeout=600):
            raise TimeoutError("E1 prefix boundary was not released")
        return None


class _CountingExecutionModel:
    def __init__(self, delegate: Model) -> None:
        self._delegate = delegate
        self.identifier = delegate.identifier
        if hasattr(delegate, "tool_contracts"):
            self.tool_contracts = getattr(delegate, "tool_contracts")
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.usage_complete = True

    def decide(self, request: ModelRequest) -> object:
        self.calls += 1
        result = self._delegate.decide(request)
        response = getattr(result, "raw_provider_response", None)
        usage = response.get("usage") if isinstance(response, Mapping) else None
        if not isinstance(usage, Mapping):
            self.usage_complete = False
            return result
        values = (
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )
        if any(type(value) is not int or value < 0 for value in values):
            self.usage_complete = False
            return result
        self.prompt_tokens += values[0]
        self.completion_tokens += values[1]
        self.total_tokens += values[2]
        return result

    def safe_usage(self) -> dict[str, object]:
        return {
            "calls": self.calls,
            "completion_tokens": (
                self.completion_tokens if self.usage_complete else "not_exposed"
            ),
            "prompt_tokens": (
                self.prompt_tokens if self.usage_complete else "not_exposed"
            ),
            "total_tokens": (
                self.total_tokens if self.usage_complete else "not_exposed"
            ),
        }


def load_registered_campaign(path: str | Path) -> RegisteredCampaign:
    """Load the exact six-task E1 preregistration without policy overrides."""
    target = Path(path)
    raw = target.read_bytes()
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
        if not isinstance(document, dict) or set(document) != {
            "arm_order", "budget", "contamination_policy",
            "directive_rendering_version", "execution_model",
            "memory_policy", "mind_model", "output_contract",
            "primary_metric", "review_policy", "schema",
            "single_variable", "tasks", "trigger",
        }:
            raise ValueError
        if (
            document["schema"] != "mind-e1-preregistered-fixtures-v1"
            or document["primary_metric"] != "VerifiedCompletion"
            or document["memory_policy"] != "fixed_empty_read_only"
        ):
            raise ValueError
        trigger = document["trigger"]
        budget = document["budget"]
        if not isinstance(trigger, dict) or not isinstance(budget, dict):
            raise ValueError
        config = E1Config(
            branch_after_root_decisions=trigger["branch_after_root_decisions"],
            max_additional_root_decisions=budget["max_additional_root_decisions"],
            max_child_decisions=budget["max_child_decisions"],
            max_context_chars=budget["max_context_chars"],
            wall_time_seconds=budget["wall_time_seconds"],
        )
        if trigger.get("text") != (
            "Mechanical E1 activation after Root decision "
            f"{config.branch_after_root_decisions}."
        ):
            raise ValueError
        mind_model = document["mind_model"]
        if not isinstance(mind_model, dict):
            raise ValueError
        if (
            mind_model.get("prompt_version") != PROMPT_VERSION
            or mind_model.get("projector_version") != PROJECTOR_VERSION
            or mind_model.get("max_model_calls") != 2
            or mind_model.get("max_capability_calls") != 1
        ):
            raise ValueError
        output = document["output_contract"]
        if not isinstance(output, dict):
            raise ValueError
        padding = output.get("padding_character")
        padding_count = output.get("padding_count")
        if (
            type(padding) is not str
            or len(padding) != 1
            or type(padding_count) is not int
            or padding_count <= 1_024
            or output.get("separator") != "\n"
            or output.get("trailing_newline") is not True
        ):
            raise ValueError
        raw_tasks = document["tasks"]
        if not isinstance(raw_tasks, list) or len(raw_tasks) != 6:
            raise ValueError
        tasks = tuple(
            _registered_task(item, padding, padding_count) for item in raw_tasks
        )
        if len({task.task_id for task in tasks}) != len(tasks):
            raise ValueError
        raw_order = document["arm_order"]
        if (
            not isinstance(raw_order, list)
            or len(raw_order) != len(tasks)
            or any(
                item not in {"baseline_first", "candidate_first"}
                for item in raw_order
            )
        ):
            raise ValueError
        arm_order = tuple(raw_order)
    except (KeyError, TypeError, UnicodeDecodeError, ValueError):
        raise ExperimentIntegrityError("invalid_registered_manifest") from None
    return RegisteredCampaign(
        manifest_path=target,
        manifest_sha256=_sha256(raw),
        raw_manifest=raw,
        config=config,
        tasks=tasks,
        arm_order=arm_order,
    )


def _registered_task(
    value: object,
    padding: str,
    padding_count: int,
) -> FrozenTaskSpec:
    if not isinstance(value, dict) or set(value) != {
        "completion_path",
        "description",
        "expected_payload",
        "files",
        "goal",
        "task_id",
    }:
        raise ValueError
    raw_files = value["files"]
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError
    files = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or set(raw_file) != {"path", "text"}:
            raise ValueError
        path = raw_file["path"]
        text = raw_file["text"]
        if type(path) is not str or type(text) is not str:
            raise ValueError
        files.append((path, text.encode("utf-8")))
    payload = value["expected_payload"]
    if type(payload) is not str or not payload or "\n" in payload:
        raise ValueError
    return FrozenTaskSpec(
        task_id=value["task_id"],
        description=value["description"],
        goal=value["goal"],
        files=tuple(files),
        completion_path=value["completion_path"],
        expected_content=padding * padding_count + "\n" + payload + "\n",
    )


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError
    return dict(pairs)


def freeze_prefix(
    task: FrozenTaskSpec,
    *,
    model: Model,
    output_dir: str | Path,
    config: E1Config,
) -> FrozenPrefix:
    """Create one settled, suspended prefix after exactly N real decisions."""
    if type(task) is not FrozenTaskSpec or type(config) is not E1Config:
        raise TypeError("freeze_prefix requires frozen E1 values")
    root = Path(output_dir)
    if root.exists():
        raise FileExistsError("frozen prefix destination already exists")
    workspace = _new_isolated_workspace()
    workspace.mkdir(parents=True)
    _seed_workspace(task, workspace)
    event_log_path = root / "execution.jsonl"
    checkpoint_path = root / "checkpoint.json"
    gated = _PrefixGateModel(model, config.branch_after_root_decisions)
    organ = ExecutionOrgan(
        workspace=workspace,
        event_log_path=event_log_path,
        checkpoint_path=checkpoint_path,
        max_decisions=(
            config.branch_after_root_decisions
            + config.max_additional_root_decisions
        ),
        max_context_chars=config.max_context_chars,
        model=gated,
    )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            run_future = pool.submit(
                organ.run_goal,
                task.goal,
                FileContentEquals(
                    task.completion_path,
                    task.expected_content,
                ),
            )
            deadline = time.monotonic() + config.wall_time_seconds
            while not gated.boundary_reached.wait(timeout=0.02):
                if run_future.done():
                    result = run_future.result()
                    raise ExperimentIntegrityError(
                        f"prefix_not_runnable:{result.status}"
                    )
                if time.monotonic() >= deadline:
                    raise ExperimentIntegrityError("prefix_boundary_timeout")

            interrupt_future = pool.submit(organ.interrupt)
            while True:
                state = organ.state
                if (
                    state is not None
                    and state.lifecycle_notice == "interrupt_requested"
                ):
                    break
                if interrupt_future.done():
                    interrupt_future.result()
                if time.monotonic() >= deadline:
                    raise ExperimentIntegrityError("prefix_interrupt_timeout")
                time.sleep(0.01)
            gated.release_boundary.set()
            interrupted = interrupt_future.result(
                timeout=max(1.0, config.wall_time_seconds)
            )
            run_result = run_future.result(
                timeout=max(1.0, config.wall_time_seconds)
            )
    finally:
        gated.release_boundary.set()
        organ.shutdown()

    if (
        interrupted.status != "suspended"
        or run_result.status != "suspended"
        or gated.delegated_calls != config.branch_after_root_decisions
        or len(run_result.decision_frames)
        != config.branch_after_root_decisions
        or run_result.state.decision_count
        != config.branch_after_root_decisions
        or run_result.state.pending_child_refs
    ):
        raise ExperimentIntegrityError("invalid_frozen_prefix")
    if gated.intercepted_request is None:
        raise ExperimentIntegrityError("missing_prefix_boundary_request")

    prefix = _load_start(task, config, root, workspace=workspace)
    if prefix.request_history != tuple(
        frame.actual_request for frame in run_result.decision_frames
    ):
        raise ExperimentIntegrityError("request_history_mismatch")
    return prefix


def fork_prefix(
    prefix: FrozenPrefix,
    output_dir: str | Path,
) -> ForkedBranches:
    """Copy both arms from the same verified immutable prefix."""
    if type(prefix) is not FrozenPrefix:
        raise TypeError("fork_prefix requires a FrozenPrefix")
    destination = Path(output_dir)
    if destination.exists():
        raise FileExistsError("pair destination already exists")
    current = _load_start(
        prefix.task,
        prefix.config,
        prefix.root,
        workspace=prefix.workspace,
    )
    if current.evidence != prefix.evidence:
        raise ExperimentIntegrityError("frozen_prefix_changed")

    destination.mkdir(parents=True)
    baseline_root = destination / "baseline"
    candidate_root = destination / "candidate"
    shutil.copytree(prefix.root, baseline_root)
    shutil.copytree(prefix.root, candidate_root)
    baseline_workspace = _new_isolated_workspace()
    candidate_workspace = _new_isolated_workspace()
    shutil.copytree(prefix.workspace, baseline_workspace)
    shutil.copytree(prefix.workspace, candidate_workspace)
    baseline = _load_branch(
        prefix.task,
        baseline_root,
        workspace=baseline_workspace,
    )
    candidate = _load_branch(
        prefix.task,
        candidate_root,
        workspace=candidate_workspace,
    )
    equivalent = (
        baseline.evidence == candidate.evidence == prefix.evidence
        and baseline.state == candidate.state == prefix.state
        and baseline.request_history
        == candidate.request_history
        == prefix.request_history
        and baseline.next_decision_id
        == candidate.next_decision_id
        == prefix.next_decision_id
    )
    if not equivalent:
        raise ExperimentIntegrityError("branch_mismatch")
    return ForkedBranches(baseline, candidate, True)


def run_pair(
    prefix: FrozenPrefix,
    *,
    baseline_model: Model,
    candidate_model: Model,
    mind_model: object,
    output_dir: str | Path,
    branches: ForkedBranches | None = None,
    candidate_first: bool = False,
) -> PairedRun:
    """Run the fixed baseline and one current A→D→E0 candidate."""
    if baseline_model.identifier != candidate_model.identifier:
        raise ExperimentIntegrityError("execution_model_mismatch")
    if getattr(baseline_model, "tool_contracts", None) != getattr(
        candidate_model,
        "tool_contracts",
        None,
    ):
        raise ExperimentIntegrityError("execution_tools_mismatch")
    prefix_identifiers = _model_identifier_history(prefix.event_log_path)
    if prefix_identifiers != (baseline_model.identifier,) * len(
        prefix.request_history
    ):
        raise ExperimentIntegrityError("execution_model_changed_after_prefix")

    if type(candidate_first) is not bool:
        raise TypeError("candidate_first must be bool")
    branches = (
        fork_prefix(prefix, output_dir)
        if branches is None
        else _verify_existing_branches(prefix, branches)
    )
    expected_environment = dict(os.environ)
    baseline = candidate = None
    for arm in (
        ("candidate", "baseline")
        if candidate_first
        else ("baseline", "candidate")
    ):
        if arm == "baseline":
            baseline = _run_branch(
                branches.baseline,
                task=prefix.task,
                model=baseline_model,
                config=prefix.config,
                decision_advisory=None,
            )
        else:
            (
                mind_result,
                mind_trace,
                directive_application,
                decision_advisory,
                mind_model_calls,
            ) = _activate_candidate(prefix, branches, mind_model, output_dir)
            candidate = _run_branch(
                branches.candidate,
                task=prefix.task,
                model=candidate_model,
                config=prefix.config,
                decision_advisory=decision_advisory,
            )
        if dict(os.environ) != expected_environment:
            raise ExperimentIntegrityError("environment_changed")
    if baseline is None or candidate is None:
        raise AssertionError("both E1 arms must run")
    environment_equal = dict(os.environ) == expected_environment
    if not environment_equal:
        raise ExperimentIntegrityError("environment_changed")

    budgets_equal = (
        baseline.additional_root_decisions
        <= prefix.config.max_additional_root_decisions
        and candidate.additional_root_decisions
        <= prefix.config.max_additional_root_decisions
    )
    verifier_equal = (
        baseline.result.state.completion_spec
        == candidate.result.state.completion_spec
        == prefix.state.completion_spec
    )
    if not budgets_equal:
        raise ExperimentIntegrityError("execution_budget_exceeded")
    if not verifier_equal:
        raise ExperimentIntegrityError("completion_verifier_mismatch")
    _assert_post_branch_tools_equal(
        prefix.request_history[-1].available_tools,
        baseline,
        candidate,
    )
    return PairedRun(
        branches=branches,
        baseline=baseline,
        candidate=candidate,
        mind_result=mind_result,
        mind_trace=mind_trace,
        directive_application=directive_application,
        mind_model_calls=mind_model_calls,
        budgets_equal=True,
        verifier_equal=True,
        environment_equal=True,
    )


def _activate_candidate(
    prefix: FrozenPrefix,
    branches: ForkedBranches,
    mind_model: object,
    output_dir: str | Path,
) -> tuple[
    MindResult,
    MindTrace,
    DirectiveApplication | None,
    tuple[str, str] | None,
    int,
]:
    mind_trace = MindTrace.create(
        Path(output_dir) / "mind-trace.jsonl",
        activation_id=f"e1-{prefix.task.task_id}",
    )
    counted_mind = _CountingMindModel(mind_model)
    mind_result = run_activation(
        ActivationInput(
            trigger=(
                "Mechanical E1 activation after Root decision "
                f"{prefix.config.branch_after_root_decisions}."
            ),
            execution_goal_snapshot=prefix.state.goal,
            execution_status="in_progress",
        ),
        model=counted_mind,
        memory_retriever=_EmptyMemoryRetriever(),
        execution_observation=_execution_observation(prefix.state),
        trace=mind_trace,
    )
    directive_application = None
    decision_advisory = None
    if isinstance(mind_result, Directive):
        directive_application = prepare_for_execution_decision(
            mind_trace,
            branches.candidate.next_decision_id,
            eligible=True,
        )
        decision_advisory = decision_advisory_from(directive_application)
        if decision_advisory is None:
            raise ExperimentIntegrityError("directive_projection_failed")
    return (
        mind_result,
        mind_trace,
        directive_application,
        decision_advisory,
        counted_mind.calls,
    )


def build_preregistration(campaign: RegisteredCampaign) -> dict[str, object]:
    """Build a deterministic source/config/task snapshot before any run."""
    if campaign.manifest_path.read_bytes() != campaign.raw_manifest:
        raise ExperimentIntegrityError("registered_manifest_changed")
    repo_root = Path(__file__).resolve().parent.parent
    source_hashes = {}
    for relative in _SOURCE_SNAPSHOT_PATHS:
        source = repo_root / PurePosixPath(relative)
        if not source.is_file():
            raise ExperimentIntegrityError("source_snapshot_missing")
        source_hashes[relative] = _file_sha256(source)
    manifest_document = json.loads(campaign.raw_manifest.decode("utf-8"))
    document: dict[str, object] = {
        "arm_order": list(campaign.arm_order),
        "budget": asdict(campaign.config),
        "contamination_policy": manifest_document["contamination_policy"],
        "directive_rendering_version": manifest_document[
            "directive_rendering_version"
        ],
        "execution_model": manifest_document["execution_model"],
        "manifest_sha256": campaign.manifest_sha256,
        "memory_policy": manifest_document["memory_policy"],
        "mind_model": manifest_document["mind_model"],
        "primary_metric": manifest_document["primary_metric"],
        "review_policy": manifest_document["review_policy"],
        "schema": "mind-e1-preregistration-v1",
        "single_variable": manifest_document["single_variable"],
        "source_sha256": source_hashes,
        "tasks": [
            {
                "description": task.description,
                "task_id": task.task_id,
                "task_sha256": task.sha256,
            }
            for task in campaign.tasks
        ],
        "trigger": manifest_document["trigger"],
    }
    document["preregistration_sha256"] = _sha256(_canonical_json(document))
    return document


@contextmanager
def _real_model_environment(campaign: RegisteredCampaign):
    del campaign
    raise E1Blocked("E1_BLOCKED:historical_minimax_provider_retired")
    yield  # pragma: no cover - makes this a context manager generator


def run_registered_e1(output_path: str | Path) -> dict[str, object]:
    """Run exactly the frozen six-pair E1 campaign and emit one artifact."""
    output_root = Path(output_path)
    if output_root.exists():
        raise FileExistsError("E1 output destination already exists")
    manifest_path = Path(__file__).parent / "fixtures" / "e1" / "manifest.json"
    campaign = load_registered_campaign(manifest_path)
    preregistration = build_preregistration(campaign)
    output_root.mkdir(parents=True)
    _write_json(output_root / "preregistration.json", preregistration)

    prefixes: list[FrozenPrefix] = []
    branch_pairs: list[ForkedBranches] = []
    prefix_records: list[dict[str, object]] = []
    pair_records: list[dict[str, object]] = []
    blinded_primary: list[dict[str, object]] = []
    unblinding: list[dict[str, object]] = []

    with _real_model_environment(campaign) as mind_model:
        # Freeze and clone all six prefixes before the first candidate activation.
        for task in campaign.tasks:
            if build_preregistration(campaign) != preregistration:
                raise ExperimentIntegrityError("preregistration_changed")
            execution_model = _CountingExecutionModel(DeepSeekModel())
            prefix_dir = output_root / "prefixes" / task.task_id
            try:
                prefix = freeze_prefix(
                    task,
                    model=execution_model,
                    output_dir=prefix_dir,
                    config=campaign.config,
                )
            except ExperimentIntegrityError as exc:
                if _provider_failed_in_log(prefix_dir / "execution.jsonl"):
                    raise E1Blocked("E1_BLOCKED:execution_provider") from None
                raise E1Blocked(f"E1_BLOCKED:{exc}") from None
            if prefix.state.child_refs:
                raise E1Blocked("E1_BLOCKED:prefix_contains_child")
            pair_dir = output_root / "pairs" / task.task_id
            branches = fork_prefix(prefix, pair_dir)
            prefixes.append(prefix)
            branch_pairs.append(branches)
            prefix_records.append(
                _prefix_record(prefix, branches, execution_model.safe_usage())
            )

        for index, (prefix, branches, order) in enumerate(
            zip(prefixes, branch_pairs, campaign.arm_order, strict=True)
        ):
            if build_preregistration(campaign) != preregistration:
                raise ExperimentIntegrityError("preregistration_changed")
            baseline_model = _CountingExecutionModel(DeepSeekModel())
            candidate_model = _CountingExecutionModel(DeepSeekModel())
            paired = run_pair(
                prefix,
                baseline_model=baseline_model,
                candidate_model=candidate_model,
                mind_model=mind_model,
                output_dir=branches.baseline.root.parent,
                branches=branches,
                candidate_first=order == "candidate_first",
            )
            if (
                isinstance(paired.mind_result, ActivationFailure)
                and paired.mind_result.code == "model_failed"
            ):
                raise E1Blocked("E1_BLOCKED:mind_provider")
            if _provider_failed(paired.baseline) or _provider_failed(
                paired.candidate
            ):
                raise E1Blocked("E1_BLOCKED:execution_provider")
            record = _pair_record(
                prefix,
                paired,
                order,
                baseline_model.safe_usage(),
                candidate_model.safe_usage(),
            )
            pair_records.append(record)
            labels = (
                ("arm-A", "arm-B")
                if index % 2 == 0
                else ("arm-B", "arm-A")
            )
            blind_rows = [
                {
                    "arm_label": labels[0],
                    "task_id": prefix.task.task_id,
                    "verified_completion": paired.baseline.verified_completion,
                },
                {
                    "arm_label": labels[1],
                    "task_id": prefix.task.task_id,
                    "verified_completion": paired.candidate.verified_completion,
                },
            ]
            blinded_primary.extend(
                sorted(blind_rows, key=lambda item: item["arm_label"])
            )
            unblinding.append(
                {
                    "baseline": labels[0],
                    "candidate": labels[1],
                    "task_id": prefix.task.task_id,
                }
            )
    if build_preregistration(campaign) != preregistration:
        raise ExperimentIntegrityError("preregistration_changed")
    summary = _campaign_summary(pair_records)
    blinded_path = output_root / "blinded-primary.json"
    _write_json(
        blinded_path,
        {
            "rows": blinded_primary,
            "schema": "mind-e1-blinded-primary-v1",
        },
    )
    artifact = {
        "blinded_primary": blinded_primary,
        "blinded_primary_sha256": _file_sha256(blinded_path),
        "prefixes": prefix_records,
        "preregistration": preregistration,
        "runs": pair_records,
        "schema": "mind-e1-artifact-v1",
        "summary": summary,
        "unblinding": unblinding,
    }
    _write_json(output_root / "e1-artifact.json", artifact)
    return artifact


def _prefix_record(
    prefix: FrozenPrefix,
    branches: ForkedBranches,
    usage: dict[str, object],
) -> dict[str, object]:
    return {
        "branch_equivalent": branches.equivalent,
        "decision_count": prefix.state.decision_count,
        "evidence": asdict(prefix.evidence),
        "execution_id": prefix.state.execution_id,
        "next_decision_id": prefix.next_decision_id,
        "provider_usage": usage,
        "root_actor_id": prefix.state.root_actor_id,
        "status": prefix.state.status,
        "task_id": prefix.task.task_id,
    }


def _pair_record(
    prefix: FrozenPrefix,
    paired: PairedRun,
    order: str,
    baseline_usage: dict[str, object],
    candidate_usage: dict[str, object],
) -> dict[str, object]:
    baseline_first = paired.baseline.new_frames[0].actual_request
    candidate_first = paired.candidate.new_frames[0].actual_request
    if isinstance(paired.mind_result, Directive):
        application = paired.directive_application
        if application is None:
            raise ExperimentIntegrityError("missing_directive_application")
        baseline_context = json.loads(baseline_first.context)
        candidate_context = json.loads(candidate_first.context)
        advisory = candidate_context.pop("mind_supervisor_directive", None)
        only_advisory_delta = (
            advisory == application.as_model_context()
            and candidate_context == baseline_context
            and _same_request_except_context(baseline_first, candidate_first)
        )
        if not only_advisory_delta:
            raise ExperimentIntegrityError("candidate_request_contaminated")
    else:
        only_advisory_delta = False
        if candidate_first != baseline_first:
            raise ExperimentIntegrityError("inert_mind_changed_request")

    later_directive_absent = all(
        "mind_supervisor_directive" not in frame.actual_request.context
        and (
            frame.actual_request.native_tool_continuation is None
            or "[Mind Supervisor Directive]"
            not in frame.actual_request.native_tool_continuation.previous_model_context
        )
        and "[Mind Supervisor Directive]"
        not in repr(frame.provider_wire_request)
        for frame in paired.candidate.new_frames[1:]
    )
    if not later_directive_absent:
        raise ExperimentIntegrityError("directive_replayed")

    classification = _paired_class(
        paired.baseline.verified_completion,
        paired.candidate.verified_completion,
    )
    mind_type = _mind_result_type(paired.mind_result)
    directive_text = (
        paired.mind_result.text
        if isinstance(paired.mind_result, Directive)
        else None
    )
    failure_code = (
        paired.mind_result.code
        if isinstance(paired.mind_result, ActivationFailure)
        else None
    )
    trace_output_chars = sum(
        len(str(event.payload["text"]))
        for event in paired.mind_trace.events
        if event.event_type == "MODEL_OUTPUT_RECORDED"
    )
    return {
        "arm_order": order,
        "baseline": _arm_record(paired.baseline, baseline_usage),
        "candidate": _arm_record(paired.candidate, candidate_usage),
        "classification": classification,
        "delivery": {
            "applied_decision_id": (
                paired.directive_application.decision_id
                if paired.directive_application is not None
                else None
            ),
            "later_directive_absent": later_directive_absent,
            "next_request_only_advisory_delta": only_advisory_delta,
            "next_request_saw_directive": (
                "mind_supervisor_directive" in candidate_first.context
            ),
        },
        "integrity": {
            "branch_equivalent": paired.branches.equivalent,
            "budgets_equal": paired.budgets_equal,
            "environment_equal": paired.environment_equal,
            "tools_equal": True,
            "verifier_equal": paired.verifier_equal,
        },
        "mind": {
            "directive_text": directive_text,
            "failure_code": failure_code,
            "model_calls": paired.mind_model_calls,
            "output_chars": trace_output_chars,
            "result": mind_type,
            "token_usage": "not_exposed_by_ModelClient",
            "trace_sha256": _file_sha256(
                paired.branches.baseline.root.parent / "mind-trace.jsonl"
            ),
        },
        "task_id": prefix.task.task_id,
        "task_sha256": prefix.task.sha256,
    }


def _arm_record(
    outcome: ArmOutcome,
    usage: dict[str, object],
) -> dict[str, object]:
    return {
        "additional_root_decisions": outcome.additional_root_decisions,
        "failure": outcome.result.failure,
        "model_usage": usage,
        "status": outcome.result.status,
        "verified_completion": outcome.verified_completion,
        "wall_seconds": round(outcome.elapsed_seconds, 6),
    }


def _same_request_except_context(
    baseline: ModelRequest,
    candidate: ModelRequest,
) -> bool:
    return replace(candidate, context=baseline.context) == baseline


def _paired_class(baseline: bool, candidate: bool) -> str:
    if not baseline and candidate:
        return "RESCUE"
    if baseline and not candidate:
        return "REGRESSION"
    if baseline and candidate:
        return "BOTH_PASS"
    return "BOTH_FAIL"


def _mind_result_type(result: MindResult) -> str:
    if isinstance(result, NoChange):
        return "NoChange"
    if isinstance(result, Directive):
        return "Directive"
    if isinstance(result, DecisionIntent):
        return "DecisionIntent"
    return "ActivationFailure"


def _campaign_summary(records: list[dict[str, object]]) -> dict[str, object]:
    counts = {
        name: sum(record["classification"] == name for record in records)
        for name in ("RESCUE", "REGRESSION", "BOTH_PASS", "BOTH_FAIL")
    }
    baseline = sum(
        bool(record["baseline"]["verified_completion"])  # type: ignore[index]
        for record in records
    )
    candidate = sum(
        bool(record["candidate"]["verified_completion"])  # type: ignore[index]
        for record in records
    )
    if candidate < baseline or (
        counts["REGRESSION"] >= 2 and counts["RESCUE"] == 0
    ):
        provisional = "EXPERIMENT_E1_NEGATIVE"
    elif (
        candidate > baseline
        and counts["RESCUE"] >= 2
        and counts["REGRESSION"] <= 1
    ):
        provisional = "SUPPORTED_PENDING_CAUSAL_REVIEW"
    else:
        provisional = "EXPERIMENT_E1_INCONCLUSIVE"
    return {
        "baseline_verified_completions": baseline,
        "candidate_verified_completions": candidate,
        **counts,
        "provisional_verdict": provisional,
    }


def _provider_failed(outcome: ArmOutcome) -> bool:
    return bool(
        outcome.result.failure
        and outcome.result.failure.startswith("model_provider:")
    )


def _provider_failed_in_log(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        state = restore_execution_state(EventLog.load(path).events, None)
    except ValueError:
        return False
    return bool(state.failure and state.failure.startswith("model_provider:"))


def _write_json(path: Path, value: object) -> None:
    encoded = _canonical_json(value) + b"\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(encoded)
    os.replace(temporary, path)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen Mind Experiment E1")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    artifact = run_registered_e1(arguments.output)
    print(
        json.dumps(
            {
                "blinded_primary_path": str(
                    arguments.output / "blinded-primary.json"
                ),
                "blinded_primary_sha256": artifact[
                    "blinded_primary_sha256"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


class _EmptyMemoryRetriever:
    def recall(self, query, policy) -> MemoryContext:
        return MemoryContext(query=query)


class _CountingMindModel:
    def __init__(self, delegate: object) -> None:
        self._delegate = delegate
        self.calls = 0

    def generate(self, recent_context, user_message, *, system_prompt):
        self.calls += 1
        generate = getattr(self._delegate, "generate", None)
        if not callable(generate):
            raise TypeError("Mind model has no generate method")
        return generate(
            recent_context,
            user_message,
            system_prompt=system_prompt,
        )

    def summarize_hot_draft(self, old_summary, moved_turns):
        summarize = getattr(self._delegate, "summarize_hot_draft", None)
        if not callable(summarize):
            raise TypeError("Mind model has no summarizer")
        return summarize(old_summary, moved_turns)


def _execution_observation(state: ExecutionState) -> ExecutionObservation:
    outcome = None
    failure = state.failure
    result = state.last_result
    if result is not None:
        raw_output = getattr(result, "output", None)
        if isinstance(raw_output, str) and raw_output.strip():
            outcome = raw_output.strip()[:MAX_OUTCOME_CHARS]
        raw_error = getattr(result, "error", None)
        if failure is None and isinstance(raw_error, str) and raw_error.strip():
            failure = raw_error.strip()[:MAX_FAILURE_CHARS]
    return ExecutionObservation(
        goal=state.goal,
        status="in_progress",
        recent_outcome=outcome,
        failure=failure,
    )


def _run_branch(
    branch: BranchStart,
    *,
    task: FrozenTaskSpec,
    model: Model,
    config: E1Config,
    decision_advisory: tuple[str, str] | None,
) -> ArmOutcome:
    if _load_branch(task, branch.root, workspace=branch.workspace) != branch:
        raise ExperimentIntegrityError("branch_changed_before_resume")
    total_decisions = (
        config.branch_after_root_decisions
        + config.max_additional_root_decisions
    )
    organ = ExecutionOrgan(
        workspace=branch.workspace,
        event_log_path=branch.event_log_path,
        checkpoint_path=branch.checkpoint_path,
        max_decisions=total_decisions,
        max_context_chars=config.max_context_chars,
        model=model,
    )
    if organ.state != branch.state or (
        organ.next_root_decision_id != branch.next_decision_id
    ):
        organ.shutdown()
        raise ExperimentIntegrityError("branch_changed_before_resume")
    started = time.monotonic()
    try:
        result = organ.resume(decision_advisory=decision_advisory)
        while result.status == "child_pending":
            pending = result.state.pending_child_refs
            if not pending:
                break
            progressed = False
            for child_ref in pending:
                child = organ.open_child(
                    child_ref,
                    max_decisions=config.max_child_decisions,
                    model=model,
                )
                try:
                    child_result = child.run_child()
                finally:
                    child.shutdown()
                if child_result.status not in ("completed", "failed"):
                    continue
                result = organ.accept_child(child_result)
                progressed = True
            if not progressed:
                break
    finally:
        organ.shutdown()
    elapsed = time.monotonic() - started
    if elapsed > config.wall_time_seconds:
        raise ExperimentIntegrityError("continuation_wall_time_exceeded")
    additional = (
        result.state.decision_count - config.branch_after_root_decisions
    )
    new_frames = result.decision_frames[
        config.branch_after_root_decisions :
    ]
    event_types = [event.event_type for event in result.events]
    verified = (
        result.status == "completed"
        and "COMPLETION_VERIFIED" in event_types
        and event_types[-1] == "EXECUTION_COMPLETED"
    )
    return ArmOutcome(
        result=result,
        new_frames=new_frames,
        event_log_bytes=branch.event_log_path.read_bytes(),
        elapsed_seconds=elapsed,
        additional_root_decisions=additional,
        verified_completion=verified,
    )


def _assert_post_branch_tools_equal(
    expected_tools: tuple[str, ...],
    baseline: ArmOutcome,
    candidate: ArmOutcome,
) -> None:
    surfaces = tuple(
        frame.actual_tools_exposed
        for outcome in (baseline, candidate)
        for frame in outcome.new_frames
    )
    if not surfaces or any(surface != expected_tools for surface in surfaces):
        raise ExperimentIntegrityError("execution_tools_mismatch")


def _model_identifier_history(path: Path) -> tuple[str, ...]:
    return tuple(
        event.payload["frame"].model_identifier
        for event in EventLog.load(path).events
        if event.event_type == "MODEL_DECISION"
    )


def _verify_existing_branches(
    prefix: FrozenPrefix,
    branches: ForkedBranches,
) -> ForkedBranches:
    if type(branches) is not ForkedBranches or branches.equivalent is not True:
        raise ExperimentIntegrityError("branch_mismatch")
    baseline = _load_branch(
        prefix.task,
        branches.baseline.root,
        workspace=branches.baseline.workspace,
    )
    candidate = _load_branch(
        prefix.task,
        branches.candidate.root,
        workspace=branches.candidate.workspace,
    )
    if (
        baseline != branches.baseline
        or candidate != branches.candidate
        or baseline.evidence != candidate.evidence
        or baseline.evidence != prefix.evidence
        or baseline.state != candidate.state
        or baseline.state != prefix.state
        or baseline.request_history != candidate.request_history
        or baseline.request_history != prefix.request_history
    ):
        raise ExperimentIntegrityError("branch_mismatch")
    return branches


def _load_start(
    task: FrozenTaskSpec,
    config: E1Config,
    root: Path,
    *,
    workspace: Path,
) -> FrozenPrefix:
    branch = _load_branch(task, root, workspace=workspace)
    if (
        branch.state.status != "suspended"
        or branch.state.decision_count != config.branch_after_root_decisions
        or branch.state.pending_child_refs
    ):
        raise ExperimentIntegrityError("invalid_frozen_state")
    return FrozenPrefix(
        task=task,
        config=config,
        root=root,
        workspace=branch.workspace,
        event_log_path=branch.event_log_path,
        checkpoint_path=branch.checkpoint_path,
        evidence=branch.evidence,
        state=branch.state,
        request_history=branch.request_history,
        next_decision_id=branch.next_decision_id,
    )


def _load_branch(
    task: FrozenTaskSpec,
    root: Path,
    *,
    workspace: Path,
) -> BranchStart:
    event_log_path = root / "execution.jsonl"
    checkpoint_path = root / "checkpoint.json"
    if not workspace.is_dir() or not event_log_path.is_file():
        raise ExperimentIntegrityError("missing_prefix_artifact")
    log = EventLog.load(event_log_path)
    state = restore_execution_state(log.events, checkpoint_path)
    frames = tuple(
        event.payload["frame"]
        for event in log.events
        if event.event_type == "MODEL_DECISION"
    )
    if any(type(frame) is not DecisionFrame for frame in frames):
        raise ExperimentIntegrityError("invalid_decision_frame")
    request_history = tuple(frame.actual_request for frame in frames)
    next_decision_id = f"decision-{state.decision_count + 1:06d}"
    evidence = PrefixEvidence(
        task_sha256=task.sha256,
        workspace_sha256=_workspace_sha256(workspace),
        event_log_sha256=_file_sha256(event_log_path),
        checkpoint_sha256=(
            _file_sha256(checkpoint_path)
            if checkpoint_path.is_file()
            else _sha256(b"")
        ),
    )
    return BranchStart(
        root=root,
        workspace=workspace,
        event_log_path=event_log_path,
        checkpoint_path=checkpoint_path,
        evidence=evidence,
        state=state,
        request_history=request_history,
        next_decision_id=next_decision_id,
    )


def _new_isolated_workspace() -> Path:
    """Keep actor cwd away from control logs and sibling arm artifacts."""
    return Path(tempfile.mkdtemp(prefix="lumina-e1-")) / "workspace"


def _seed_workspace(task: FrozenTaskSpec, workspace: Path) -> None:
    for relative, content in sorted(task.files):
        target = workspace.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != value
    ):
        raise ValueError("task paths must be normalized relative POSIX paths")


def _workspace_sha256(root: Path) -> str:
    digest = hashlib.sha256(b"mind-e1-workspace-v1\0")
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ExperimentIntegrityError("workspace_symlink_not_allowed")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_dir():
            digest.update(b"D\0" + relative + b"\0")
        elif path.is_file():
            content = path.read_bytes()
            digest.update(
                b"F\0"
                + relative
                + b"\0"
                + len(content).to_bytes(8, "big")
                + content
            )
        else:
            raise ExperimentIntegrityError("unsupported_workspace_entry")
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


if __name__ == "__main__":
    raise SystemExit(_main())
