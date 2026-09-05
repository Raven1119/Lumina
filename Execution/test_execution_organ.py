from __future__ import annotations

import dataclasses
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from Execution import ExecutionOrgan, FileContentEquals, RealityEvidence
from Execution.execution import (
    ClaimComplete,
    IPythonCode,
    NativeModelDecision,
    Return,
    Wait,
)


ROOT_SURFACE = (
    "ipython(code: str)",
    "wait(event_type: str)",
    "claim_complete()",
)
CHILD_SURFACE = (
    "ipython(code: str)",
    "wait(event_type: str)",
    "return(local_result: str)",
)


class _ScriptedModel:
    def __init__(self, actions, *, tool_contracts=ROOT_SURFACE, identifier="scripted"):
        self._actions = iter(actions)
        self.tool_contracts = tool_contracts
        self.identifier = identifier
        self.received_requests = []

    def decide(self, request):
        self.received_requests.append(request)
        return next(self._actions)


def _organ(tmp_path, model, **overrides):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    options = {
        "workspace": workspace,
        "event_log_path": tmp_path / "state" / "execution.jsonl",
        "model": model,
        "max_decisions": 4,
    }
    options.update(overrides)
    return ExecutionOrgan(**options)


def test_root_ipython_completion_uses_the_frozen_production_surface(tmp_path):
    model = _ScriptedModel(
        [
            IPythonCode(
                "from pathlib import Path\n"
                "Path('answer.txt').write_text('42', encoding='utf-8')"
            ),
            ClaimComplete(),
        ]
    )
    organ = _organ(tmp_path, model, max_decisions=2)

    try:
        result = organ.run_goal(
            "Write the answer.",
            FileContentEquals("answer.txt", "42"),
        )
    finally:
        organ.shutdown()

    assert result.status == "completed"
    assert organ.state == result.state
    assert organ.result == result
    assert tuple(model.received_requests[0].available_tools) == ROOT_SURFACE
    assert (tmp_path / "workspace" / "answer.txt").read_text(encoding="utf-8") == "42"


def test_reality_projection_is_execution_owned_and_immutable(tmp_path):
    organ = _organ(tmp_path, _ScriptedModel([Wait("continue")]))

    try:
        result = organ.run_goal(
            "Wait for one external event.",
            FileContentEquals("answer.txt", "done"),
        )
        evidence = organ.reality_evidence()
    finally:
        organ.shutdown()

    assert result.status == "waiting"
    assert len(evidence) == 1
    assert isinstance(evidence[0], RealityEvidence)
    assert evidence[0].kind == "PRE_OUTCOME"
    assert evidence[0].sequence < len(result.events) + 1
    assert evidence[0].source_event_refs[-1] == "event-000003"
    with pytest.raises(TypeError):
        evidence[0].payload["execution_status"] = "mutated"
    with pytest.raises(dataclasses.FrozenInstanceError):
        evidence[0].kind = "OUTCOME"

    with pytest.raises(TypeError):
        RealityEvidence(
            evidence_ref="forged",
            execution_ref="forged",
            source_event_refs=("event-000001",),
            kind="PRE_OUTCOME",
            sequence=1,
            payload={},
        )


def test_reality_projection_is_bounded_and_cursor_ordered(tmp_path):
    waits = [Wait(f"wake-{index}") for index in range(10)]
    organ = _organ(tmp_path, _ScriptedModel(waits), max_decisions=12)

    try:
        organ.run_goal(
            "Wait repeatedly.",
            FileContentEquals("answer.txt", "done"),
        )
        for index in range(9):
            organ.deliver_event(f"wake-{index}")
        first_page = organ.reality_evidence()
        second_page = organ.reality_evidence(
            after_sequence=first_page[-1].sequence
        )
    finally:
        organ.shutdown()

    assert len(first_page) == 8
    assert len(second_page) == 2
    assert tuple(item.sequence for item in first_page) == tuple(
        sorted(item.sequence for item in first_page)
    )
    assert all(len(item.source_event_refs) <= 16 for item in (*first_page, *second_page))
    assert all(
        len(json.dumps(dict(item.payload), separators=(",", ":"), sort_keys=True))
        <= 128
        for item in (*first_page, *second_page)
    )


def test_reality_projection_keeps_late_first_wait_refs_in_sequence_order(tmp_path):
    actions = [
        IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('done')"),
        *(IPythonCode(f"value_{index} = {index}") for index in range(4)),
    ]
    actions.extend((Wait("continue"), ClaimComplete()))
    organ = _organ(tmp_path, _ScriptedModel(actions), max_decisions=7)

    try:
        waiting = organ.run_goal(
            "Do local computation, wait, then complete.",
            FileContentEquals("answer.txt", "done"),
        )
        completed = organ.deliver_event("continue")
        outcome = organ.reality_evidence()[-1]
    finally:
        organ.shutdown()

    assert waiting.status == "waiting"
    assert completed.status == "completed"
    assert len(outcome.source_event_refs) == 16
    assert outcome.source_event_refs == tuple(
        sorted(
            outcome.source_event_refs,
            key=lambda event_ref: int(event_ref.removeprefix("event-")),
        )
    )


def test_reality_projection_is_redacted_deterministic_and_replayable(tmp_path):
    secret_path = str((tmp_path / "private" / "secret.txt").resolve())
    secret_code = "open('secret.txt').read() # RAW_CODE_SENTINEL"
    model = _ScriptedModel(
        [
            NativeModelDecision(
                action=Wait("continue"),
                provider_wire_request={
                    "provider_body": "PROVIDER_SENTINEL",
                    "path": secret_path,
                },
                raw_provider_response={
                    "reasoning": secret_code,
                    "file_content": "FILE_CONTENT_SENTINEL",
                },
            )
        ]
    )
    organ = _organ(tmp_path, model)

    try:
        organ.run_goal(
            "Keep PRIVATE_GOAL_SENTINEL internal.",
            FileContentEquals(secret_path, "FILE_CONTENT_SENTINEL"),
        )
        first = organ.reality_evidence()
        second = organ.reality_evidence()
    finally:
        organ.shutdown()

    replay_organ = _organ(tmp_path, _ScriptedModel([]))
    try:
        replayed = replay_organ.reality_evidence()
    finally:
        replay_organ.shutdown()
    serialized = json.dumps(
        [
            {
                "evidence_ref": item.evidence_ref,
                "execution_ref": item.execution_ref,
                "source_event_refs": item.source_event_refs,
                "kind": item.kind,
                "sequence": item.sequence,
                "payload": dict(item.payload),
            }
            for item in first
        ],
        sort_keys=True,
    )

    assert first == second == replayed
    assert len(serialized) < 1_024
    for forbidden in (
        "PROVIDER_SENTINEL",
        "RAW_CODE_SENTINEL",
        "FILE_CONTENT_SENTINEL",
        "PRIVATE_GOAL_SENTINEL",
        secret_path,
    ):
        assert forbidden not in serialized


def test_restart_replays_completed_state_without_model_or_action_replay(tmp_path):
    first_model = _ScriptedModel(
        [
            IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('done')"),
            ClaimComplete(),
        ]
    )
    first = _organ(tmp_path, first_model, max_decisions=2)
    completed = first.run_goal(
        "Write done.",
        FileContentEquals("answer.txt", "done"),
    )
    execution_id = completed.state.execution_id
    root_actor_id = completed.state.root_actor_id
    first.shutdown()

    class _NoCallModel:
        identifier = "must-not-run"
        tool_contracts = ROOT_SURFACE

        def __init__(self):
            self.calls = 0

        def decide(self, request):
            self.calls += 1
            raise AssertionError("completed execution was sampled again")

    no_call_model = _NoCallModel()
    restored = _organ(tmp_path, no_call_model, max_decisions=2)
    replayed = restored.run_goal(
        "Write done.",
        FileContentEquals("answer.txt", "done"),
    )
    restored.shutdown()

    assert replayed.status == "completed"
    assert replayed.events == completed.events
    assert replayed.state.execution_id == execution_id
    assert replayed.state.root_actor_id == root_actor_id
    assert no_call_model.calls == 0


def test_orphan_checkpoint_fails_before_the_first_durable_event(tmp_path):
    checkpoint_path = tmp_path / "state" / "checkpoint.json"
    checkpoint_path.parent.mkdir()
    checkpoint_path.write_text("{malformed", encoding="utf-8")
    event_log_path = tmp_path / "state" / "execution.jsonl"
    organ = _organ(
        tmp_path,
        _ScriptedModel([ClaimComplete()]),
        event_log_path=event_log_path,
        checkpoint_path=checkpoint_path,
    )

    try:
        with pytest.raises(ValueError, match="checkpoint"):
            organ.run_goal(
                "Do not mutate history.",
                FileContentEquals("answer.txt", "done"),
            )
    finally:
        organ.shutdown()

    assert not event_log_path.exists() or event_log_path.stat().st_size == 0


class _BlockingModel:
    identifier = "blocking-scripted"
    tool_contracts = ROOT_SURFACE

    def __init__(self):
        self.calls = 0
        self.second_call_started = threading.Event()
        self.release_second_call = threading.Event()

    def decide(self, request):
        self.calls += 1
        if self.calls == 1:
            return IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('A')")
        if self.calls == 2:
            self.second_call_started.set()
            if not self.release_second_call.wait(timeout=3):
                raise TimeoutError("test did not release model")
            return IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('B')")
        if self.calls == 3:
            return IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('B')")
        return ClaimComplete()


def test_explicit_interrupt_suspends_then_resumes_the_same_root(tmp_path):
    model = _BlockingModel()
    organ = _organ(tmp_path, model, max_decisions=3)

    with ThreadPoolExecutor(max_workers=2) as pool:
        run_future = pool.submit(
            organ.run_goal,
            "Write B.",
            FileContentEquals("answer.txt", "B"),
        )
        assert model.second_call_started.wait(timeout=3)
        interrupt_future = pool.submit(organ.interrupt)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = organ.state
            if state is not None and state.lifecycle_notice == "interrupt_requested":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("interrupt was not durably recorded")
        model.release_second_call.set()
        interrupted = interrupt_future.result(timeout=3)
        run_result = run_future.result(timeout=3)

    assert interrupted.status == run_result.status == "suspended"
    assert model.calls == 2
    completed = organ.resume()
    organ.shutdown()

    assert completed.status == "completed"
    assert model.calls == 4
    assert (tmp_path / "workspace" / "answer.txt").read_text(encoding="utf-8") == "B"


def test_ipython_child_return_is_driven_through_the_same_organ_seam(tmp_path):
    root_model = _ScriptedModel(
        [
            IPythonCode(
                "handle = await spawn_child('derive the local answer')\n"
                "print(handle['depth'])"
            ),
            ClaimComplete(),
        ]
    )
    root = _organ(tmp_path, root_model, max_decisions=2, max_depth=1)

    pending = root.run_goal(
        "Write the returned answer.",
        FileContentEquals("answer.txt", "42"),
    )
    child_model = _ScriptedModel(
        [
            IPythonCode("open('answer.txt', 'w', encoding='utf-8').write('42')"),
            Return("42"),
        ],
        tool_contracts=CHILD_SURFACE,
        identifier="scripted-child",
    )
    child = root.open_child(
        pending.child_ref,
        model=child_model,
        max_decisions=2,
    )
    returned = child.run_child()
    child.shutdown()
    completed = root.accept_child(returned)
    root.shutdown()

    assert returned.status == "completed"
    assert completed.status == "completed"
    assert pending.child_ref.depth == 1
    assert pending.child_ref.parent_actor_id == completed.state.root_actor_id
    assert tuple(root_model.received_requests[0].available_tools) == ROOT_SURFACE
    assert tuple(child_model.received_requests[0].available_tools) == CHILD_SURFACE
    assert "spawn_child(goal)" in root_model.received_requests[0].context
    assert "spawn_child" not in child_model.received_requests[0].context
