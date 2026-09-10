from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import InitVar, asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Literal, Mapping

from Execution.deepseek_model import DeepSeekModel
from Execution.execution import (
    AgentProcess,
    ChildRef,
    CompletionSpec,
    DecisionFrame,
    EventLog,
    ExecutionResult,
    ExecutionState,
    Model,
    SharedEnvironment,
    ToolHost,
    restore_execution_state,
    _action_sequence,
    _encode_value,
    _unsettled_action_start,
)
from Execution.ipython_control import PersistentIPython


_MAX_REALITY_EVIDENCE_ITEMS = 8
_MAX_REALITY_PAYLOAD_CHARS = 128
_MAX_REALITY_SOURCE_REFS = 16
_REALITY_EVIDENCE_OWNER = object()
_MAX_PREDICTION_RECEIPTS = 64


@dataclass(frozen=True)
class PredictionReceipt:
    """Owner attestation: this digest was registered at a waiting event head.

    The receipt says nothing about the truth of the predicted value. The
    caller must persist the exact prediction before requesting registration.
    """

    prediction_id: str
    prediction_digest: str
    pre_ref: str
    execution_ref: str
    sequence: int
    target: Literal["completion_verified"] = "completion_verified"


@dataclass(frozen=True)
class RealityEvidence:
    """One immutable, bounded fact projected by Execution."""

    evidence_ref: str
    execution_ref: str
    source_event_refs: tuple[str, ...]
    kind: Literal["PRE_OUTCOME", "OUTCOME"]
    sequence: int
    payload: Mapping[str, str | bool | int | None]
    _owner_token: InitVar[object] = None

    def __post_init__(self, _owner_token: object) -> None:
        if _owner_token is not _REALITY_EVIDENCE_OWNER:
            raise TypeError("RealityEvidence can only be projected by Execution")


class ExecutionOrgan:
    """Own one durable Root or Child AgentProcess in an explicit workspace."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        event_log_path: str | Path,
        max_decisions: int,
        model: Model | None = None,
        max_context_chars: int = 2_000,
        checkpoint_path: str | Path | None = None,
        max_depth: int = 1,
        ipython_control=None,
        max_decisions_per_advance: int | None = None,
        completion_review_required: Callable[[], bool] | None = None,
        changed_decision_context: Callable[[DecisionFrame], Mapping | None] | None = None,
        before_dispatch: Callable[[], None] | None = None,
        _child_ref: ChildRef | None = None,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        self._event_log_path = Path(event_log_path).resolve()
        self._checkpoint_path = (
            Path(checkpoint_path).resolve()
            if checkpoint_path is not None
            else None
        )
        self._max_decisions = max_decisions
        self._max_decisions_per_advance = max_decisions_per_advance
        self._max_context_chars = max_context_chars
        self._model = model if model is not None else DeepSeekModel()
        self._child_ref = _child_ref
        self._result: ExecutionResult | None = None

        environment = SharedEnvironment(self._workspace)
        event_log = self._load_or_create_event_log(self._event_log_path)
        # The host may supply the same execute/interrupt/close control interface
        # with an isolated kernel. Normal production construction is unchanged.
        ipython = ipython_control if ipython_control is not None else PersistentIPython(self._workspace)
        process_options = {
            "model": self._model,
            "tools": ToolHost(environment),
            "max_decisions": max_decisions,
            "max_decisions_per_advance": max_decisions_per_advance,
            "max_context_chars": max_context_chars,
            "event_log": event_log,
            "checkpoint_path": self._checkpoint_path,
            "ipython_control": ipython,
        }
        if _child_ref is None:
            self._process = AgentProcess(
                **process_options,
                max_depth=max_depth,
                completion_review_required=completion_review_required,
                changed_decision_context=changed_decision_context,
                before_dispatch=before_dispatch,
            )
        else:
            self._process = AgentProcess.for_child(
                _child_ref,
                **process_options,
            )
        self._event_log = event_log

    def completion_review_pending(self) -> bool:
        """A real completion claim yielded for host review; no business Wait was invented."""
        events = self._event_log.events
        return bool(events and events[-1].event_type == "COMPLETION_DEFERRED")

    @staticmethod
    def _load_or_create_event_log(path: Path) -> EventLog:
        if path.exists() and path.stat().st_size:
            return EventLog.load(path)
        return EventLog(path)

    @property
    def state(self) -> ExecutionState | None:
        events = self._event_log.events
        checkpoint_exists = (
            self._checkpoint_path is not None
            and self._checkpoint_path.exists()
        )
        if not events and not checkpoint_exists:
            return None
        return restore_execution_state(
            events,
            self._checkpoint_path,
        )

    @property
    def result(self) -> ExecutionResult | None:
        return self._result

    @property
    def next_root_decision_id(self) -> str | None:
        """Return the stable Actor-local identity of the next Root decision."""
        if self._child_ref is not None:
            return None
        state = self.state
        if state is not None and state.status in ("completed", "failed"):
            return None
        decision_count = state.decision_count if state is not None else 0
        return f"decision-{decision_count + 1:06d}"

    def has_unhandled_external_event(self) -> bool:
        """Whether a durable wake awaits its first committed model decision.

        Reading does not consume it; equal text in a later wake is a new event.
        """
        for event in reversed(self._event_log.events):
            if event.event_type == "MODEL_DECISION":
                return False
            if event.event_type == "ROOT_WOKEN":
                return True
        return False

    def committed_tool_calls(self, *, limit: int = 6) -> tuple[tuple[str, str], ...]:
        """Bounded committed tool-batch digests; not proof of task success."""
        if type(limit) is not int or not 1 <= limit <= 120:
            raise ValueError('invalid_history_limit')
        result = []
        retired = self.retired_decisions()
        started = {e.source_event_refs[0] for e in self._event_log.events
                   if e.event_type in {'TOOL_CALL_STARTED', 'IPYTHON_EXECUTION_STARTED'}}
        for event in reversed(self._event_log.events):
            if event.event_type != "MODEL_DECISION":
                continue
            frame = event.payload["frame"]
            if frame.decision_id in retired and event.event_id not in started:
                continue
            if frame.provider_tool_call_id is not None:
                calls = frame.raw_provider_response['choices'][0]['message']['tool_calls']
                value = [(c['id'], c['function']['name'], c['function']['arguments']) for c in calls]
                digest = hashlib.sha256(json.dumps(value, ensure_ascii=False,
                    separators=(',', ':')).encode('utf-8')).hexdigest()
                result.append((frame.decision_id, digest))
            if len(result) == limit:
                break
        return tuple(reversed(result))

    def history_segments(self) -> list[dict]:
        """Completed decision ranges, without recursively embedded model requests.

        Actual result truncation metadata is retained. Retired plans remain
        identifiable as unexecuted; they never acquire fabricated tool results.
        """
        events = self._event_log.events
        positions = [i for i, event in enumerate(events) if event.event_type == 'MODEL_DECISION']
        segments = []
        for index, position in enumerate(positions):
            end = positions[index + 1] if index + 1 < len(positions) else len(events)
            group = events[position:end]
            frame = group[0].payload['frame']
            results = [e for e in group[1:] if e.event_type in {
                'TOOL_RESULT', 'TOOL_FAILED', 'IPYTHON_EXECUTION_RESULT', 'IPYTHON_EXECUTION_FAILED',
                'ACTION_RECONCILED', 'ROOT_WAITING', 'COMPLETION_VERIFIED', 'COMPLETION_REJECTED',
                'COMPLETION_DEFERRED', 'DECISION_RETIRED'}]
            if (_unsettled_action_start(events[:end]) is not None or not results
                    or len(results) < len(_action_sequence(frame.resulting_action))
                    and not any(e.event_type == 'DECISION_RETIRED' for e in results)):
                continue
            segments.append({'ref': 'execution-history:' + self.state.execution_id + ':' + frame.decision_id,
                'content': {'decision': frame.decision_id, 'action': _encode_value(frame.resulting_action),
                    'results': [{'ref': e.event_id, 'kind': e.event_type, 'data': _encode_value(e.payload)}
                                for e in results]}})
        return segments

    def retired_decisions(self) -> tuple[str, ...]:
        """Plans barred from dispatch; their retirement suffix may still be pending."""
        retired = {event.source_event_refs[0] for event in self._event_log.events
                   if event.event_type == 'DECISION_RETIRED'}
        return tuple(event.payload['frame'].decision_id for event in self._event_log.events
                     if event.event_type == 'MODEL_DECISION'
                     and (event.event_id in retired
                          or getattr(event.payload['frame'].raw_model_response, 'retirement_reason', None) is not None))

    def latest_transport_failure(self) -> tuple[str, str] | None:
        """Stable owner evidence for the latest IPython transport outcome."""
        for event in reversed(self._event_log.events):
            if event.event_type in {"IPYTHON_EXECUTION_RESULT", "IPYTHON_EXECUTION_FAILED"}:
                result = event.payload["observation"].result
                if result.error_code in {"isolated_kernel_failed", "kernel_closed"}:
                    return event.event_id, result.error_code
                return None
        return None

    def cognitive_requests(self) -> tuple[tuple[str, str], ...]:
        """Committed actor requests and their causal result IDs; reading consumes nothing."""
        return tuple((event.event_id, event.payload['observation'].result.cognitive_request)
            for event in self._event_log.events if event.event_type == 'IPYTHON_EXECUTION_RESULT'
            and event.payload['observation'].result.cognitive_request is not None)

    def reality_evidence(
        self,
        *,
        after_sequence: int = 0,
        include_historical_pre: bool = False,
    ) -> tuple[RealityEvidence, ...]:
        """Project bounded facts; historical PRE grants no prediction eligibility."""
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        if type(include_historical_pre) is not bool:
            raise ValueError("include_historical_pre must be a boolean")
        events = self._event_log.events
        if not events:
            return ()
        execution_ref = events[0].payload.get("execution_id")
        if not isinstance(execution_ref, str) or not execution_ref:
            raise ValueError("durable execution is missing its identity")
        by_id = {event.event_id: event for event in events}
        terminal = events[-1].event_type in (
            "EXECUTION_COMPLETED",
            "EXECUTION_FAILED",
        )

        def source_refs(event_id: str) -> tuple[str, ...]:
            pending = [event_id]
            lineage: set[str] = set()
            while pending:
                current_id = pending.pop()
                if current_id in lineage:
                    continue
                current = by_id.get(current_id)
                if current is None:
                    raise ValueError("execution history has an unknown causal ref")
                lineage.add(current_id)
                pending.extend(current.source_event_refs)
            ordered = tuple(
                event.event_id for event in events if event.event_id in lineage
            )
            if len(ordered) <= _MAX_REALITY_SOURCE_REFS:
                return ordered
            first_wait = next(
                (
                    event_ref
                    for event_ref in ordered
                    if by_id[event_ref].event_type == "ROOT_WAITING"
                ),
                None,
            )
            if first_wait is None:
                return ordered[-_MAX_REALITY_SOURCE_REFS:]
            newest = ordered[-_MAX_REALITY_SOURCE_REFS:]
            if first_wait in newest:
                return newest
            return (first_wait, *ordered[-(_MAX_REALITY_SOURCE_REFS - 1) :])

        projected: list[RealityEvidence] = []
        for event in events:
            if event.sequence <= after_sequence:
                continue
            if event.event_type == "ROOT_WAITING":
                if terminal and not include_historical_pre:
                    continue
                kind: Literal["PRE_OUTCOME", "OUTCOME"] = "PRE_OUTCOME"
                payload: dict[str, str | bool | int | None] = {
                    "execution_status": "waiting",
                    "target": "completion_verified",
                }
            elif event.event_type == "EXECUTION_COMPLETED":
                kind = "OUTCOME"
                payload = {
                    "execution_status": "completed",
                    "completion_verified": True,
                }
            elif event.event_type == "EXECUTION_FAILED":
                kind = "OUTCOME"
                payload = {
                    "execution_status": "failed",
                    "completion_verified": None,
                }
            else:
                continue
            payload_chars = len(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            if payload_chars > _MAX_REALITY_PAYLOAD_CHARS:
                raise ValueError("RealityEvidence payload exceeds its bound")
            projected.append(
                RealityEvidence(
                    evidence_ref=(
                        f"{execution_ref}:{event.event_id}:{kind.lower()}"
                    ),
                    execution_ref=execution_ref,
                    source_event_refs=source_refs(event.event_id),
                    kind=kind,
                    sequence=event.sequence,
                    payload=MappingProxyType(payload),
                    _owner_token=_REALITY_EVIDENCE_OWNER,
                )
            )
            if len(projected) == _MAX_REALITY_EVIDENCE_ITEMS:
                break
        return tuple(projected)

    def prediction_receipt(self, prediction_id: str) -> PredictionReceipt | None:
        """Resolve a durable registration, including after completion/restart."""
        self._validate_prediction_identity(prediction_id)
        with self._event_log._lock:
            return next(
                (item for item in self._prediction_receipts()
                 if item.prediction_id == prediction_id),
                None,
            )

    def register_prediction(
        self, *, prediction_id: str, prediction_digest: str, pre_ref: str,
    ) -> PredictionReceipt | None:
        """Register only at the current Root waiting head; None means ineligible.

        Uses the same owner lock as event persistence, with no model call and
        no Execution wake/action/state event. Contention does not wait for an
        execution operation. Like EventLog, this requires one live writer per
        execution; reopening another owner concurrently is unsupported.
        """
        self._validate_prediction_identity(prediction_id)
        if (
            type(prediction_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", prediction_digest) is None
            or type(pre_ref) is not str or not 1 <= len(pre_ref) <= 256
        ):
            raise ValueError("invalid prediction registration")
        if not self._event_log._lock.acquire(blocking=False):
            return None
        try:
            receipts = self._prediction_receipts()
            for receipt in receipts:
                if receipt.prediction_id == prediction_id:
                    if (receipt.prediction_digest, receipt.pre_ref) != (
                        prediction_digest, pre_ref,
                    ):
                        raise ValueError("prediction identity conflict")
                    return receipt
            events = self._event_log.events
            if (
                self._child_ref is not None or not events
                or events[-1].event_type != "ROOT_WAITING"
                or len(receipts) >= _MAX_PREDICTION_RECEIPTS
            ):
                return None
            execution_ref = events[0].payload["execution_id"]
            current_pre = f"{execution_ref}:{events[-1].event_id}:pre_outcome"
            if pre_ref != current_pre:
                return None
            receipt = PredictionReceipt(
                prediction_id, prediction_digest, pre_ref,
                execution_ref, events[-1].sequence,
            )
            self._persist_prediction_receipts((*receipts, receipt))
            return receipt
        finally:
            self._event_log._lock.release()

    @staticmethod
    def _validate_prediction_identity(prediction_id: str) -> None:
        if (
            type(prediction_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", prediction_id)
            is None
        ):
            raise ValueError("invalid prediction identity")

    @property
    def _prediction_receipts_path(self) -> Path:
        return self._event_log_path.with_suffix(".prediction-receipts.json")

    def _prediction_receipts(self) -> tuple[PredictionReceipt, ...]:
        path = self._prediction_receipts_path
        if not path.exists():
            return ()
        if path.stat().st_size > 96 * 1024:
            raise ValueError("prediction receipt file exceeds its bound")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if set(document) != {"version", "receipts", "sha256"}:
                raise ValueError("invalid receipt document")
            records = document["receipts"]
            canonical = json.dumps(records, sort_keys=True, separators=(",", ":"))
            if (
                type(document["version"]) is not int or document["version"] != 1
                or not isinstance(records, list)
                or len(records) > _MAX_PREDICTION_RECEIPTS
                or hashlib.sha256(canonical.encode()).hexdigest() != document["sha256"]
            ):
                raise ValueError("invalid receipt integrity")
            fields = {"prediction_id", "prediction_digest", "pre_ref", "execution_ref", "sequence", "target"}
            if any(type(record) is not dict or set(record) != fields for record in records):
                raise ValueError("invalid receipt fields")
            events = self._event_log.events
            receipts = tuple(PredictionReceipt(**record) for record in records)
            seen: set[str] = set()
            for receipt in receipts:
                self._validate_prediction_identity(receipt.prediction_id)
                if (
                    not events or receipt.prediction_id in seen
                    or type(receipt.sequence) is not int
                    or not 1 <= receipt.sequence <= len(events)
                    or receipt.execution_ref != events[0].payload["execution_id"]
                    or receipt.target != "completion_verified"
                    or type(receipt.prediction_digest) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", receipt.prediction_digest) is None
                ):
                    raise ValueError("invalid receipt binding")
                head = events[receipt.sequence - 1]
                if (
                    head.event_type != "ROOT_WAITING"
                    or receipt.pre_ref != f"{receipt.execution_ref}:{head.event_id}:pre_outcome"
                ):
                    raise ValueError("invalid receipt source")
                seen.add(receipt.prediction_id)
            return receipts
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid prediction receipt file") from exc

    def _persist_prediction_receipts(
        self, receipts: tuple[PredictionReceipt, ...],
    ) -> None:
        # ponytail: atomically retain the bounded prefix; use an append journal
        # if a measured use case needs more than 64 predictions per execution.
        records = [asdict(item) for item in receipts]
        canonical = json.dumps(records, sort_keys=True, separators=(",", ":"))
        document = json.dumps({
            "version": 1, "receipts": records,
            "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        }, sort_keys=True, separators=(",", ":"))
        path = self._prediction_receipts_path
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".prediction-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = stream.name
                stream.write(document)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def run_goal(
        self,
        goal: str,
        completion_spec: CompletionSpec,
        *,
        decision_advisory: tuple[str, str] | None = None,
        defer_actions: bool = False,
    ) -> ExecutionResult:
        if type(defer_actions) is not bool:
            raise ValueError("defer_actions must be a bool")
        if self._child_ref is not None:
            raise ValueError("Child execution must use run_child")
        state = self.state
        if state is not None:
            if defer_actions:
                raise ValueError("deferred start requires a new execution")
            if state.goal != goal or state.completion_spec != completion_spec:
                raise ValueError(
                    "goal and completion spec must match durable execution"
                )
            return self._remember(
                self._process.resume(decision_advisory=decision_advisory)
            )
        return self._remember(
            self._process.run(
                goal,
                completion_spec,
                decision_advisory=decision_advisory,
                defer_actions=defer_actions,
            )
        )

    def run_child(self) -> ExecutionResult:
        if self._child_ref is None:
            raise ValueError("only a Child ExecutionOrgan can use run_child")
        if self.state is not None:
            return self._remember(self._process.resume())
        return self._remember(self._process.run_child())

    def open_child(
        self,
        child_ref: ChildRef,
        *,
        max_decisions: int,
        model: Model | None = None,
        checkpoint_path: str | Path | None = None,
    ) -> ExecutionOrgan:
        state = self.state
        if state is None or child_ref not in state.pending_child_refs:
            raise ValueError("Child handle is not pending for this Actor")
        if child_ref.event_log_path is None:
            raise ValueError("Child handle is missing durable EventLog identity")
        return ExecutionOrgan(
            workspace=self._workspace,
            event_log_path=child_ref.event_log_path,
            max_decisions=max_decisions,
            model=model,
            max_context_chars=self._max_context_chars,
            max_decisions_per_advance=self._max_decisions_per_advance,
            checkpoint_path=checkpoint_path,
            _child_ref=child_ref,
        )

    def accept_child(self, child_result: ExecutionResult) -> ExecutionResult:
        return self._remember(self._process.accept_child(child_result))

    def deliver_event(self, event_type: str, data: str = "", *,
                      decision_advisory: tuple[str, str] | None = None,
                      defer_actions: bool = False) -> ExecutionResult:
        if self._child_ref is not None and decision_advisory is not None:
            raise ValueError("Child execution cannot accept a decision advisory")
        return self._remember(self._process.deliver_event(
            event_type, data, decision_advisory=decision_advisory, defer_actions=defer_actions))

    def interrupt(self) -> ExecutionResult:
        return self._remember(self._process.interrupt())

    def resume(
        self,
        *,
        decision_advisory: tuple[str, str | None] | None = None,
    ) -> ExecutionResult:
        """None recovers guidance; (next_id, None) withdraws it without advancing."""
        if self._child_ref is not None and decision_advisory is not None:
            raise ValueError("Child execution cannot accept a decision advisory")
        return self._remember(
            self._process.resume(decision_advisory=decision_advisory)
        )

    def shutdown(self) -> None:
        self._process.close()

    def _remember(self, result: ExecutionResult) -> ExecutionResult:
        self._result = result
        return result
