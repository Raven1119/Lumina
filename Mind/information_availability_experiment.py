"""Preregistered PULL/PUSH harness for Mind Experiment E2.

The experiment host may inspect files and durable Execution evidence.  None of
that authority is exposed to the runtime Mind, whose only inputs remain data
and the existing read-only cognitive capability surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from Conversation_Memory.adapter.models import MemoryContext
from Execution.execution import Model
from Execution.deepseek_model import DeepSeekModel
from Mind.behavioral_experiment import (
    E1Config,
    ArmOutcome,
    BranchStart,
    ExperimentIntegrityError,
    ForkedBranches,
    FrozenPrefix,
    FrozenTaskSpec,
    _CountingExecutionModel,
    _CountingMindModel,
    _assert_post_branch_tools_equal,
    _execution_observation,
    _file_sha256,
    _model_identifier_history,
    _provider_failed,
    _provider_failed_in_log,
    _run_branch,
    _verify_existing_branches,
    fork_prefix,
    freeze_prefix,
)
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
    PROJECTOR_VERSION,
    PROMPT_VERSION,
    MindTrace,
)


class E2Blocked(RuntimeError):
    """A required real provider or frozen E2 precondition is absent."""


_SOURCE_SNAPSHOT_PATHS = (
    "Mind/information_availability_experiment.py",
    "Mind/test_information_availability.py",
    "Mind/experiment_a.py",
    "Mind/trace.py",
    "Mind/directive.py",
    "Mind/execution_steering_experiment.py",
    "Mind/behavioral_experiment.py",
    "Mind/fixtures/e2/manifest.json",
    "Execution/organ.py",
    "Execution/execution.py",
    "Execution/deepseek_model.py",
    "Execution/ipython_control.py",
    "core/model_client.py",
    "Conversation_Memory/adapter/models.py",
)


MindResult = NoChange | Directive | DecisionIntent | ActivationFailure


@dataclass(frozen=True, slots=True)
class RegisteredE2Campaign:
    manifest_path: Path
    manifest_sha256: str
    raw_manifest: bytes
    config: E1Config
    tasks: tuple[FrozenTaskSpec, ...]
    arm_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InformationArm:
    snapshot: ExecutionObservation
    mind_result: MindResult
    mind_trace: MindTrace
    mind_trace_sha256: str
    directive_application: DirectiveApplication | None
    mind_model_calls: int
    execution: ArmOutcome


@dataclass(frozen=True, slots=True)
class InformationPair:
    branches: ForkedBranches
    snapshot: ExecutionObservation
    pull: InformationArm
    push: InformationArm
    budgets_equal: bool
    verifier_equal: bool
    environment_equal: bool


def load_registered_campaign(path: str | Path) -> RegisteredE2Campaign:
    """Load the exact six-task E2 preregistration without overrides."""
    target = Path(path)
    raw = target.read_bytes()
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
        if not isinstance(document, dict) or set(document) != {
            "arm_order",
            "budget",
            "contamination_policy",
            "directive_rendering_version",
            "execution_model",
            "memory_policy",
            "mind_model",
            "output_contract",
            "primary_metric",
            "pull_push_mechanism",
            "review_policy",
            "schema",
            "semantic_audit_rubric",
            "single_variable",
            "tasks",
            "trigger",
            "verdict_criteria",
        }:
            raise ValueError
        if (
            document["schema"] != "mind-e2-preregistered-fixtures-v1"
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
            "Mechanical E2 activation after Root decision "
            f"{config.branch_after_root_decisions}."
        ):
            raise ValueError
        mind_model = document["mind_model"]
        if (
            not isinstance(mind_model, dict)
            or mind_model.get("prompt_version") != PROMPT_VERSION
            or mind_model.get("projector_version") != PROJECTOR_VERSION
            or mind_model.get("max_model_calls") != 2
            or mind_model.get("max_capability_calls") != 1
        ):
            raise ValueError
        mechanism = document["pull_push_mechanism"]
        if not isinstance(mechanism, dict) or set(mechanism) != {
            "inspect_rule",
            "pull",
            "push",
            "snapshot_rule",
        }:
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
            _registered_task(item, padding, padding_count)
            for item in raw_tasks
        )
        if len({task.task_id for task in tasks}) != 6:
            raise ValueError
        raw_order = document["arm_order"]
        if (
            not isinstance(raw_order, list)
            or len(raw_order) != 6
            or any(
                item not in {"pull_first", "push_first"}
                for item in raw_order
            )
        ):
            raise ValueError
        arm_order = tuple(raw_order)
        rubric = document["semantic_audit_rubric"]
        if not isinstance(rubric, dict) or set(rubric) != {
            "CONTRADICTED",
            "SUPPORTED",
            "UNCERTAIN",
        }:
            raise ValueError
    except (KeyError, TypeError, UnicodeDecodeError, ValueError):
        raise ExperimentIntegrityError("invalid_e2_manifest") from None
    return RegisteredE2Campaign(
        manifest_path=target,
        manifest_sha256=_sha256(raw),
        raw_manifest=raw,
        config=config,
        tasks=tasks,
        arm_order=arm_order,
    )


def run_information_pair(
    prefix: FrozenPrefix,
    *,
    pull_execution_model: Model,
    push_execution_model: Model,
    mind_model: object,
    output_dir: str | Path,
    branches: ForkedBranches | None = None,
    push_first: bool = False,
) -> InformationPair:
    """Run PULL and PUSH over one immutable frozen-prefix snapshot."""
    if pull_execution_model.identifier != push_execution_model.identifier:
        raise ExperimentIntegrityError("execution_model_mismatch")
    if getattr(pull_execution_model, "tool_contracts", None) != getattr(
        push_execution_model,
        "tool_contracts",
        None,
    ):
        raise ExperimentIntegrityError("execution_tools_mismatch")
    identifiers = _model_identifier_history(prefix.event_log_path)
    if identifiers != (pull_execution_model.identifier,) * len(
        prefix.request_history
    ):
        raise ExperimentIntegrityError("execution_model_changed_after_prefix")
    if type(push_first) is not bool:
        raise TypeError("push_first must be bool")

    branches = (
        fork_prefix(prefix, output_dir)
        if branches is None
        else _verify_existing_branches(prefix, branches)
    )
    snapshot = _execution_observation(prefix.state)
    expected_environment = dict(os.environ)
    pull = push = None
    for arm_name in (
        ("push", "pull") if push_first else ("pull", "push")
    ):
        if arm_name == "pull":
            pull = _run_information_arm(
                prefix,
                branches.baseline,
                snapshot=snapshot,
                visible=False,
                mind_model=mind_model,
                execution_model=pull_execution_model,
                trace_path=Path(output_dir) / "pull-mind-trace.jsonl",
            )
        else:
            push = _run_information_arm(
                prefix,
                branches.candidate,
                snapshot=snapshot,
                visible=True,
                mind_model=mind_model,
                execution_model=push_execution_model,
                trace_path=Path(output_dir) / "push-mind-trace.jsonl",
            )
        if dict(os.environ) != expected_environment:
            raise ExperimentIntegrityError("environment_changed")
    if pull is None or push is None:
        raise AssertionError("both E2 arms must run")

    budgets_equal = all(
        arm.execution.additional_root_decisions
        <= prefix.config.max_additional_root_decisions
        for arm in (pull, push)
    )
    verifier_equal = (
        pull.execution.result.state.completion_spec
        == push.execution.result.state.completion_spec
        == prefix.state.completion_spec
    )
    if not budgets_equal:
        raise ExperimentIntegrityError("execution_budget_exceeded")
    if not verifier_equal:
        raise ExperimentIntegrityError("completion_verifier_mismatch")
    _assert_post_branch_tools_equal(
        prefix.request_history[-1].available_tools,
        pull.execution,
        push.execution,
    )
    return InformationPair(
        branches=branches,
        snapshot=snapshot,
        pull=pull,
        push=push,
        budgets_equal=True,
        verifier_equal=True,
        environment_equal=True,
    )


def _run_information_arm(
    prefix: FrozenPrefix,
    branch: BranchStart,
    *,
    snapshot: ExecutionObservation,
    visible: bool,
    mind_model: object,
    execution_model: Model,
    trace_path: Path,
) -> InformationArm:
    mind_trace = MindTrace.create(
        trace_path,
        activation_id=(
            f"e2-{prefix.task.task_id}-{'push' if visible else 'pull'}"
        ),
    )
    counted = _CountingMindModel(mind_model)
    mind_result = run_activation(
        ActivationInput(
            trigger=(
                "Mechanical E2 activation after Root decision "
                f"{prefix.config.branch_after_root_decisions}."
            ),
            execution_goal_snapshot=prefix.state.goal,
            execution_status="in_progress",
        ),
        model=counted,
        memory_retriever=_EmptyMemoryRetriever(),
        execution_observation=snapshot,
        initial_execution_observation_visible=visible,
        trace=mind_trace,
    )
    application = None
    advisory = None
    if isinstance(mind_result, Directive):
        application = prepare_for_execution_decision(
            mind_trace,
            branch.next_decision_id,
            eligible=True,
        )
        advisory = decision_advisory_from(application)
        if advisory is None:
            raise ExperimentIntegrityError("directive_projection_failed")
    execution = _run_branch(
        branch,
        task=prefix.task,
        model=execution_model,
        config=prefix.config,
        decision_advisory=advisory,
    )
    return InformationArm(
        snapshot=snapshot,
        mind_result=mind_result,
        mind_trace=mind_trace,
        mind_trace_sha256=_file_sha256(trace_path),
        directive_application=application,
        mind_model_calls=counted.calls,
        execution=execution,
    )


class _EmptyMemoryRetriever:
    def recall(self, query: str, policy: object) -> MemoryContext:
        return MemoryContext(query=query)


def build_blinded_directive_audit(
    runs: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], tuple[dict[str, str], ...]]:
    """Build outcome-blind reviewer input plus a private unblinding map."""
    items: list[dict[str, object]] = []
    mapping: list[dict[str, str]] = []
    rubric = {
        "CONTRADICTED": (
            "The Directive makes a factual current-Execution claim that the "
            "frozen observation explicitly refutes."
        ),
        "SUPPORTED": (
            "The Directive factual premise is supported by the observation, "
            "or explicitly states uncertainty."
        ),
        "UNCERTAIN": (
            "The observation is insufficient to confirm or refute the premise."
        ),
    }
    for run in runs:
        observation = run.get("execution_observation")
        if not isinstance(observation, Mapping):
            raise ExperimentIntegrityError("missing_execution_observation")
        for arm_name in ("pull", "push"):
            arm = run.get(arm_name)
            if not isinstance(arm, Mapping):
                raise ExperimentIntegrityError("invalid_e2_run_record")
            mind = arm.get("mind")
            if not isinstance(mind, Mapping):
                raise ExperimentIntegrityError("invalid_e2_run_record")
            text = mind.get("directive_text")
            if text is None:
                continue
            if type(text) is not str or not text:
                raise ExperimentIntegrityError("invalid_directive_text")
            review_id = f"directive-{len(items) + 1:03d}"
            items.append(
                {
                    "directive_text": text,
                    "execution_observation": dict(observation),
                    "review_id": review_id,
                }
            )
            mapping.append(
                {
                    "arm": arm_name,
                    "review_id": review_id,
                    "task_id": str(run["task_id"]),
                }
            )
    return (
        {
            "items": items,
            "rubric": rubric,
            "schema": "mind-e2-blinded-directive-audit-v1",
        },
        tuple(mapping),
    )


def evaluate_verdict(
    runs: Sequence[Mapping[str, object]],
    audit_labels: Mapping[str, str],
) -> str:
    """Apply the frozen E2 thresholds without post-hoc interpretation."""
    review_ids: list[str] = []
    for run in runs:
        for arm_name in ("pull", "push"):
            review_id = run[arm_name]["mind"].get("review_id")  # type: ignore[index]
            if review_id is not None:
                if type(review_id) is not str:
                    raise ExperimentIntegrityError("audit_coverage_mismatch")
                review_ids.append(review_id)
    expected_review_ids = set(review_ids)
    if (
        len(expected_review_ids) != len(review_ids)
        or set(audit_labels) != expected_review_ids
    ):
        raise ExperimentIntegrityError("audit_coverage_mismatch")
    allowed_labels = {"SUPPORTED", "UNCERTAIN", "CONTRADICTED"}
    if any(label not in allowed_labels for label in audit_labels.values()):
        raise ExperimentIntegrityError("invalid_audit_label")
    pull_completions = sum(
        bool(run["pull"]["verified_completion"])  # type: ignore[index]
        for run in runs
    )
    push_completions = sum(
        bool(run["push"]["verified_completion"])  # type: ignore[index]
        for run in runs
    )
    push_wins = sum(run["classification"] == "PUSH_WIN" for run in runs)
    pull_wins = sum(run["classification"] == "PULL_WIN" for run in runs)
    if push_completions < pull_completions or (
        pull_wins >= 2 and push_wins == 0
    ):
        return "EXPERIMENT_E2_NEGATIVE"

    information_quality = False
    for run in runs:
        pull = run["pull"]  # type: ignore[assignment]
        push = run["push"]  # type: ignore[assignment]
        pull_review = pull["mind"].get("review_id")  # type: ignore[index]
        push_review = push["mind"].get("review_id")  # type: ignore[index]
        if (
            pull_review is not None
            and audit_labels.get(pull_review) == "CONTRADICTED"
            and (
                push_review is None
                or audit_labels.get(push_review) != "CONTRADICTED"
            )
        ):
            information_quality = True
        if (
            run["classification"] == "PUSH_WIN"
            and push["mind"].get("result") == "Directive"  # type: ignore[index]
            and bool(push["delivery"].get("applied"))  # type: ignore[index]
            and push_review is not None
            and audit_labels.get(push_review) == "SUPPORTED"
        ):
            information_quality = True
    if (
        push_completions > pull_completions
        and push_wins >= 2
        and pull_wins <= 1
        and information_quality
    ):
        return "EXPERIMENT_E2_SUPPORTED"
    return "EXPERIMENT_E2_INCONCLUSIVE"


def merge_audit_reviews(
    review_ids: Sequence[str],
    reviewer_a: Mapping[str, str],
    reviewer_b: Mapping[str, str],
) -> dict[str, str]:
    """Merge two complete independent reviews; disagreements are uncertain."""
    ordered_review_ids = tuple(review_ids)
    expected = set(ordered_review_ids)
    if (
        len(expected) != len(ordered_review_ids)
        or set(reviewer_a) != expected
        or set(reviewer_b) != expected
    ):
        raise ExperimentIntegrityError("audit_coverage_mismatch")
    allowed = {"SUPPORTED", "UNCERTAIN", "CONTRADICTED"}
    if any(
        label not in allowed
        for label in (*reviewer_a.values(), *reviewer_b.values())
    ):
        raise ExperimentIntegrityError("invalid_audit_label")
    return {
        review_id: (
            reviewer_a[review_id]
            if reviewer_a[review_id] == reviewer_b[review_id]
            else "UNCERTAIN"
        )
        for review_id in ordered_review_ids
    }


def build_preregistration(
    campaign: RegisteredE2Campaign,
    prefix_records: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Freeze source/config/task/prefix facts before the first Mind call."""
    if campaign.manifest_path.read_bytes() != campaign.raw_manifest:
        raise ExperimentIntegrityError("registered_manifest_changed")
    if len(prefix_records) != 6:
        raise ExperimentIntegrityError("prefix_preregistration_incomplete")
    repo_root = Path(__file__).resolve().parent.parent
    source_hashes = {}
    for relative in _SOURCE_SNAPSHOT_PATHS:
        source = repo_root / PurePosixPath(relative)
        if not source.is_file():
            raise ExperimentIntegrityError("source_snapshot_missing")
        source_hashes[relative] = _file_sha256(source)
    manifest = json.loads(campaign.raw_manifest.decode("utf-8"))
    document: dict[str, object] = {
        "arm_order": list(campaign.arm_order),
        "budget": asdict(campaign.config),
        "contamination_policy": manifest["contamination_policy"],
        "execution_model": manifest["execution_model"],
        "manifest_sha256": campaign.manifest_sha256,
        "memory_policy": manifest["memory_policy"],
        "mind_model": manifest["mind_model"],
        "prefixes": [dict(record) for record in prefix_records],
        "primary_metric": manifest["primary_metric"],
        "pull_push_mechanism": manifest["pull_push_mechanism"],
        "review_policy": manifest["review_policy"],
        "schema": "mind-e2-preregistration-v1",
        "semantic_audit_rubric": manifest["semantic_audit_rubric"],
        "single_variable": manifest["single_variable"],
        "source_sha256": source_hashes,
        "tasks": [
            {
                "description": task.description,
                "task_id": task.task_id,
                "task_sha256": task.sha256,
            }
            for task in campaign.tasks
        ],
        "trigger": manifest["trigger"],
        "verdict_criteria": manifest["verdict_criteria"],
    }
    document["preregistration_sha256"] = _sha256(_canonical_json(document))
    return document


@contextmanager
def _real_model_environment(campaign: RegisteredE2Campaign):
    del campaign
    raise E2Blocked("E2_BLOCKED:historical_minimax_provider_retired")
    yield  # pragma: no cover - makes this a context manager generator


def run_registered_e2(output_path: str | Path) -> dict[str, object]:
    """Run exactly the frozen six-prefix, twelve-continuation campaign."""
    output_root = Path(output_path)
    if output_root.exists():
        raise FileExistsError("E2 output destination already exists")
    campaign = load_registered_campaign(
        Path(__file__).parent / "fixtures" / "e2" / "manifest.json"
    )
    output_root.mkdir(parents=True)
    prefixes: list[FrozenPrefix] = []
    branch_pairs: list[ForkedBranches] = []
    prefix_records: list[dict[str, object]] = []
    pair_records: list[dict[str, object]] = []
    blinded_primary: list[dict[str, object]] = []
    unblinding: list[dict[str, str]] = []

    with _real_model_environment(campaign) as mind_model:
        for task in campaign.tasks:
            if campaign.manifest_path.read_bytes() != campaign.raw_manifest:
                raise ExperimentIntegrityError("registered_manifest_changed")
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
                    raise E2Blocked("E2_BLOCKED:execution_provider") from None
                raise E2Blocked(f"E2_BLOCKED:{exc}") from None
            if prefix.state.child_refs:
                raise E2Blocked("E2_BLOCKED:prefix_contains_child")
            branches = fork_prefix(
                prefix,
                output_root / "pairs" / task.task_id,
            )
            prefixes.append(prefix)
            branch_pairs.append(branches)
            prefix_records.append(
                _prefix_record(prefix, branches, execution_model.safe_usage())
            )

        preregistration = build_preregistration(campaign, prefix_records)
        _write_json(output_root / "preregistration.json", preregistration)

        for index, (prefix, branches, order) in enumerate(
            zip(prefixes, branch_pairs, campaign.arm_order, strict=True)
        ):
            if build_preregistration(campaign, prefix_records) != preregistration:
                raise ExperimentIntegrityError("preregistration_changed")
            pull_model = _CountingExecutionModel(DeepSeekModel())
            push_model = _CountingExecutionModel(DeepSeekModel())
            paired = run_information_pair(
                prefix,
                pull_execution_model=pull_model,
                push_execution_model=push_model,
                mind_model=mind_model,
                output_dir=branches.baseline.root.parent,
                branches=branches,
                push_first=order == "push_first",
            )
            for arm in (paired.pull, paired.push):
                if (
                    isinstance(arm.mind_result, ActivationFailure)
                    and arm.mind_result.code == "model_failed"
                ):
                    raise E2Blocked("E2_BLOCKED:mind_provider")
                if _provider_failed(arm.execution):
                    raise E2Blocked("E2_BLOCKED:execution_provider")
            record = _pair_record(
                prefix,
                paired,
                order,
                pull_model.safe_usage(),
                push_model.safe_usage(),
            )
            pair_records.append(record)
            labels = (
                ("arm-A", "arm-B")
                if index % 2 == 0
                else ("arm-B", "arm-A")
            )
            blinded_primary.extend(
                sorted(
                    [
                        {
                            "arm_label": labels[0],
                            "task_id": prefix.task.task_id,
                            "verified_completion": paired.pull.execution.verified_completion,
                        },
                        {
                            "arm_label": labels[1],
                            "task_id": prefix.task.task_id,
                            "verified_completion": paired.push.execution.verified_completion,
                        },
                    ],
                    key=lambda item: item["arm_label"],
                )
            )
            unblinding.append(
                {
                    "pull": labels[0],
                    "push": labels[1],
                    "task_id": prefix.task.task_id,
                }
            )

    if build_preregistration(campaign, prefix_records) != preregistration:
        raise ExperimentIntegrityError("preregistration_changed")
    blinded_path = output_root / "blinded-primary.json"
    _write_json(
        blinded_path,
        {"rows": blinded_primary, "schema": "mind-e2-blinded-primary-v1"},
    )
    audit_document, audit_mapping = build_blinded_directive_audit(pair_records)
    _attach_review_ids(pair_records, audit_mapping)
    audit_path = output_root / "directive-audit-input.json"
    _write_json(audit_path, audit_document)
    artifact = {
        "blinded_directive_audit_sha256": _file_sha256(audit_path),
        "blinded_primary": blinded_primary,
        "blinded_primary_sha256": _file_sha256(blinded_path),
        "directive_audit_unblinding": list(audit_mapping),
        "prefixes": prefix_records,
        "preregistration": preregistration,
        "runs": pair_records,
        "schema": "mind-e2-artifact-v1",
        "summary": _campaign_summary(pair_records),
        "unblinding": unblinding,
    }
    _write_json(output_root / "e2-artifact.json", artifact)
    return artifact


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
    files = value["files"]
    if not isinstance(files, list) or not files:
        raise ValueError
    task_files = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "text"}:
            raise ValueError
        if type(item["path"]) is not str or type(item["text"]) is not str:
            raise ValueError
        task_files.append((item["path"], item["text"].encode("utf-8")))
    payload = value["expected_payload"]
    if type(payload) is not str or not payload or "\n" in payload:
        raise ValueError
    return FrozenTaskSpec(
        task_id=value["task_id"],
        description=value["description"],
        goal=value["goal"],
        files=tuple(task_files),
        completion_path=value["completion_path"],
        expected_content=padding * padding_count + "\n" + payload + "\n",
    )


def _prefix_record(
    prefix: FrozenPrefix,
    branches: ForkedBranches,
    usage: dict[str, object],
) -> dict[str, object]:
    return {
        "branch_equivalent": branches.equivalent,
        "checkpoint_sha256": prefix.evidence.checkpoint_sha256,
        "decision_count": prefix.state.decision_count,
        "event_log_sha256": prefix.evidence.event_log_sha256,
        "execution_id": prefix.state.execution_id,
        "next_decision_id": prefix.next_decision_id,
        "provider_usage": usage,
        "root_actor_id": prefix.state.root_actor_id,
        "status": prefix.state.status,
        "task_id": prefix.task.task_id,
        "task_sha256": prefix.evidence.task_sha256,
        "workspace_sha256": prefix.evidence.workspace_sha256,
    }


def _pair_record(
    prefix: FrozenPrefix,
    paired: InformationPair,
    order: str,
    pull_usage: dict[str, object],
    push_usage: dict[str, object],
) -> dict[str, object]:
    return {
        "arm_order": order,
        "classification": _paired_class(
            paired.pull.execution.verified_completion,
            paired.push.execution.verified_completion,
        ),
        "execution_observation": _snapshot_document(paired.snapshot),
        "integrity": {
            "branch_equivalent": paired.branches.equivalent,
            "budgets_equal": paired.budgets_equal,
            "environment_equal": paired.environment_equal,
            "verifier_equal": paired.verifier_equal,
        },
        "pull": _arm_record(paired.pull, pull_usage),
        "push": _arm_record(paired.push, push_usage),
        "task_id": prefix.task.task_id,
        "task_sha256": prefix.task.sha256,
    }


def _arm_record(
    arm: InformationArm,
    usage: dict[str, object],
) -> dict[str, object]:
    application = arm.directive_application
    first_request = arm.execution.new_frames[0].actual_request
    saw_directive = "mind_supervisor_directive" in first_request.context
    expected_saw = application is not None
    if saw_directive != expected_saw:
        raise ExperimentIntegrityError("directive_delivery_mismatch")
    later_absent = all(
        "mind_supervisor_directive" not in frame.actual_request.context
        and (
            frame.actual_request.native_tool_continuation is None
            or "[Mind Supervisor Directive]"
            not in frame.actual_request.native_tool_continuation.previous_model_context
        )
        and "[Mind Supervisor Directive]"
        not in repr(frame.provider_wire_request)
        for frame in arm.execution.new_frames[1:]
    )
    if not later_absent:
        raise ExperimentIntegrityError("directive_replayed")
    return {
        "additional_root_decisions": arm.execution.additional_root_decisions,
        "delivery": {
            "applied": application is not None,
            "applied_decision_id": (
                application.decision_id if application is not None else None
            ),
            "later_directive_absent": later_absent,
            "next_request_saw_directive": saw_directive,
        },
        "failure": arm.execution.result.failure,
        "mind": {
            "directive_text": (
                arm.mind_result.text
                if isinstance(arm.mind_result, Directive)
                else None
            ),
            "failure_code": (
                arm.mind_result.code
                if isinstance(arm.mind_result, ActivationFailure)
                else None
            ),
            "model_calls": arm.mind_model_calls,
            "result": _mind_result_type(arm.mind_result),
            "review_id": None,
            "trace_sha256": arm.mind_trace_sha256,
        },
        "model_usage": usage,
        "status": arm.execution.result.status,
        "verified_completion": arm.execution.verified_completion,
        "wall_seconds": round(arm.execution.elapsed_seconds, 6),
    }


def _attach_review_ids(
    runs: Sequence[dict[str, object]],
    mapping: Sequence[Mapping[str, str]],
) -> None:
    by_task = {str(run["task_id"]): run for run in runs}
    for item in mapping:
        run = by_task[item["task_id"]]
        run[item["arm"]]["mind"]["review_id"] = item["review_id"]  # type: ignore[index]


def _campaign_summary(runs: Sequence[Mapping[str, object]]) -> dict[str, object]:
    counts = {
        name: sum(run["classification"] == name for run in runs)
        for name in ("PUSH_WIN", "PULL_WIN", "BOTH_PASS", "BOTH_FAIL")
    }
    return {
        "PULL_verified_completions": sum(
            bool(run["pull"]["verified_completion"])  # type: ignore[index]
            for run in runs
        ),
        "PUSH_verified_completions": sum(
            bool(run["push"]["verified_completion"])  # type: ignore[index]
            for run in runs
        ),
        **counts,
        "verdict": "PENDING_BLINDED_DIRECTIVE_AUDIT",
    }


def _snapshot_document(snapshot: ExecutionObservation) -> dict[str, object]:
    return {
        "failure": snapshot.failure,
        "goal": snapshot.goal,
        "recent_outcome": snapshot.recent_outcome,
        "status": snapshot.status,
    }


def _paired_class(pull: bool, push: bool) -> str:
    if not pull and push:
        return "PUSH_WIN"
    if pull and not push:
        return "PULL_WIN"
    if pull and push:
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


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError
    return dict(pairs)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen Mind Experiment E2")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    artifact = run_registered_e2(arguments.output)
    print(
        json.dumps(
            {
                "blinded_directive_audit_path": str(
                    arguments.output / "directive-audit-input.json"
                ),
                "blinded_directive_audit_sha256": artifact[
                    "blinded_directive_audit_sha256"
                ],
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


if __name__ == "__main__":
    raise SystemExit(_main())
