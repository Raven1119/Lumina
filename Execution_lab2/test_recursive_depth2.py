import json
import os
from dataclasses import replace

import pytest

from Execution_lab2.deepseek_model import DeepSeekModel

from Execution_lab2.execution import (
    AgentProcess,
    ClaimComplete,
    EventLog,
    FileContentEquals,
    ReadRequest,
    Return,
    ScriptedModel,
    SharedEnvironment,
    SpawnChild,
    ToolCall,
    ToolHost,
    Wait,
    WriteRequest,
    fold_execution_state,
)


def _tools(workspace):
    return ToolHost(SharedEnvironment(workspace))


def test_depth_two_lineage_nested_return_context_isolation_and_shared_world(
    tmp_path,
):
    (tmp_path / "inner.txt").write_text("VALUE=21", encoding="utf-8")
    root_model = ScriptedModel(
        [
            SpawnChild("CHILD_PRIVATE_SENTINEL investigate the requested value"),
            ToolCall(ReadRequest("grandchild.txt")),
            ToolCall(WriteRequest("answer.txt", "42")),
            ClaimComplete(),
        ],
        identifier="root-model",
    )
    root = AgentProcess(
        model=root_model,
        tools=_tools(tmp_path),
        max_decisions=5,
        max_depth=2,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )

    root_spawned = root.run(
        "ROOT_PRIVATE_SENTINEL produce answer.txt",
        FileContentEquals("answer.txt", "42"),
    )
    child_model = ScriptedModel(
        [
            SpawnChild("read inner.txt and write grandchild.txt"),
            ToolCall(ReadRequest("grandchild.txt")),
            Return("42"),
        ],
        identifier="child-model",
    )
    child = AgentProcess.for_child(
        root_spawned.child_ref,
        model=child_model,
        tools=_tools(tmp_path),
        max_decisions=4,
    )

    child_spawned = child.run_child()
    grandchild_model = ScriptedModel(
        [
            ToolCall(WriteRequest("grandchild.txt", "21")),
            Return("21"),
        ],
        identifier="grandchild-model",
    )
    grandchild = AgentProcess.for_child(
        child_spawned.child_ref,
        model=grandchild_model,
        tools=_tools(tmp_path),
        max_decisions=3,
    )

    grandchild_returned = grandchild.run_child()
    child_returned = child.accept_child(grandchild_returned)
    root_completed = root.accept_child(child_returned)

    root_state = root_spawned.state
    child_state = child_spawned.state
    grandchild_state = grandchild_returned.state
    assert (root_state.depth, child_state.depth, grandchild_state.depth) == (0, 1, 2)
    assert child_state.parent_actor_id == root_state.actor_id
    assert grandchild_state.parent_actor_id == child_state.actor_id
    assert len({root_state.actor_id, child_state.actor_id, grandchild_state.actor_id}) == 3
    assert grandchild_returned.output == "21"
    assert child_returned.output == "42"
    assert root_completed.status == "completed"
    assert (tmp_path / "grandchild.txt").read_text(encoding="utf-8") == "21"
    assert (tmp_path / "answer.txt").read_text(encoding="utf-8") == "42"

    grandchild_context = grandchild_model.received_requests[0].context
    assert "ROOT_PRIVATE_SENTINEL" not in grandchild_context
    assert "CHILD_PRIVATE_SENTINEL" not in grandchild_context
    assert "21" in child_model.received_requests[1].context
    assert "42" in root_model.received_requests[1].context
    assert '"local_result":{"text":"21"' not in root_model.received_requests[1].context

    assert grandchild_returned.events[-1].payload["parent_actor_id"] == child_state.actor_id
    child_delivery = next(
        event
        for event in child_returned.events
        if event.event_type == "CHILD_RETURNED"
        and event.payload["child_actor_id"] == grandchild_state.actor_id
    )
    root_delivery = next(
        event
        for event in root_completed.events
        if event.event_type == "CHILD_RETURNED"
    )
    assert child_delivery.payload["parent_actor_id"] == child_state.actor_id
    assert root_delivery.payload["child_actor_id"] == child_state.actor_id
    assert root_completed.state == fold_execution_state(root_completed.events)
    assert child_returned.state == fold_execution_state(child_returned.events)
    assert grandchild_returned.state == fold_execution_state(
        grandchild_returned.events
    )


def test_depth_two_actor_cannot_spawn_and_creates_no_identity_or_event(tmp_path):
    root = AgentProcess(
        model=ScriptedModel([SpawnChild("child")]),
        tools=_tools(tmp_path),
        max_decisions=1,
        max_depth=2,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    root_spawned = root.run("root", FileContentEquals("unused", "unused"))
    child = AgentProcess.for_child(
        root_spawned.child_ref,
        model=ScriptedModel([SpawnChild("grandchild")]),
        tools=_tools(tmp_path),
        max_decisions=1,
    )
    child_spawned = child.run_child()
    model = ScriptedModel([SpawnChild("forbidden fourth actor")])
    grandchild = AgentProcess.for_child(
        child_spawned.child_ref,
        model=model,
        tools=_tools(tmp_path),
        max_decisions=1,
    )

    rejected = grandchild.run_child()

    assert rejected.status == "failed"
    assert rejected.failure == "unauthorized_action:SpawnChild"
    assert rejected.state.depth == 2
    assert all("spawn_child" not in tool for tool in model.received_requests[0].available_tools)
    assert "CHILD_SPAWNED" not in [event.event_type for event in rejected.events]


def test_grandchild_failure_reaches_only_child_until_child_returns(tmp_path):
    root_model = ScriptedModel(
        [SpawnChild("investigate"), ToolCall(WriteRequest("answer.txt", "handled")), ClaimComplete()]
    )
    root = AgentProcess(
        model=root_model,
        tools=_tools(tmp_path),
        max_decisions=4,
        max_depth=2,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    root_spawned = root.run(
        "handle nested failure", FileContentEquals("answer.txt", "handled")
    )
    child_model = ScriptedModel(
        [SpawnChild("fail locally"), Return("handled")], identifier="child-model"
    )
    child = AgentProcess.for_child(
        root_spawned.child_ref,
        model=child_model,
        tools=_tools(tmp_path),
        max_decisions=3,
    )
    child_spawned = child.run_child()
    failed_grandchild = AgentProcess.for_child(
        child_spawned.child_ref,
        model=ScriptedModel([{"unexpected": "decision"}]),
        tools=_tools(tmp_path),
        max_decisions=1,
    ).run_child()

    child_returned = child.accept_child(failed_grandchild)

    assert failed_grandchild.status == "failed"
    assert child_returned.status == "completed"
    assert child_returned.output == "handled"
    assert "unknown_action:dict" in child_model.received_requests[1].context
    assert "CHILD_FAILED" in [event.event_type for event in child_returned.events]
    assert "CHILD_FAILED" not in [event.event_type for event in root_spawned.events]

    completed = root.accept_child(child_returned)
    assert completed.status == "completed"
    assert "unknown_action:dict" not in root_model.received_requests[1].context
    assert [event.event_type for event in completed.events].count("CHILD_RETURNED") == 1


def test_restart_preserves_depth_lineage_and_spawn_denial(tmp_path):
    root_path = tmp_path / "root.jsonl"
    root = AgentProcess(
        model=ScriptedModel([SpawnChild("child")]),
        tools=_tools(tmp_path),
        max_decisions=1,
        max_depth=2,
        event_log=EventLog(root_path),
    )
    root_spawned = root.run("root", FileContentEquals("unused", "unused"))
    child = AgentProcess.for_child(
        root_spawned.child_ref,
        model=ScriptedModel([SpawnChild("grandchild")]),
        tools=_tools(tmp_path),
        max_decisions=1,
    )
    child_spawned = child.run_child()
    original_ref = child_spawned.child_ref

    restored_child_model = ScriptedModel([])
    restored_child = AgentProcess.for_child(
        root_spawned.child_ref,
        model=restored_child_model,
        tools=_tools(tmp_path),
        max_decisions=2,
    )
    restored_pending = restored_child.resume()

    assert restored_pending.state.depth == 1
    assert restored_pending.child_ref == original_ref
    assert restored_pending.state.actor_id == child_spawned.state.actor_id
    assert restored_pending.state.parent_actor_id == root_spawned.state.actor_id
    assert restored_child_model.received_requests == []
    assert [event.event_type for event in restored_pending.events].count(
        "CHILD_SPAWNED"
    ) == 1

    grandchild_model = ScriptedModel([SpawnChild("forbidden")])
    restored_grandchild = AgentProcess.for_child(
        original_ref,
        model=grandchild_model,
        tools=_tools(tmp_path),
        max_decisions=1,
    ).run_child()
    assert restored_grandchild.state.depth == 2
    assert restored_grandchild.state.parent_actor_id == child_spawned.state.actor_id
    assert restored_grandchild.failure == "unauthorized_action:SpawnChild"
    assert all(
        "spawn_child" not in tool
        for tool in grandchild_model.received_requests[0].available_tools
    )

    with pytest.raises(ValueError, match="durable lineage"):
        AgentProcess.for_child(
            replace(original_ref, depth=1),
            model=ScriptedModel([SpawnChild("forbidden")]),
            tools=_tools(tmp_path),
            max_decisions=1,
        )


def test_recursive_mode_rejects_a_second_child_before_identity_creation(tmp_path):
    root_model = ScriptedModel([SpawnChild("first"), SpawnChild("second")])
    root = AgentProcess(
        model=root_model,
        tools=_tools(tmp_path),
        max_decisions=3,
        max_depth=2,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    first = root.run("one recursive chain", FileContentEquals("unused", "unused"))
    returned = AgentProcess.for_child(
        first.child_ref,
        model=ScriptedModel([Return("done")]),
        tools=_tools(tmp_path),
        max_decisions=1,
    ).run_child()

    rejected = root.accept_child(returned)

    assert rejected.status == "failed"
    assert rejected.failure == "child_limit_reached"
    assert [event.event_type for event in rejected.events].count("CHILD_SPAWNED") == 1
    assert len(rejected.state.child_refs) == 1


def test_depth_policy_is_fixed_and_does_not_open_deeper_recursion(tmp_path):
    with pytest.raises(ValueError, match="max_depth"):
        AgentProcess(
            model=ScriptedModel([Return("unused")]),
            tools=_tools(tmp_path),
            max_decisions=1,
            max_depth=3,
        )


def test_legacy_v1_checkpoint_falls_back_to_canonical_event_replay(tmp_path):
    root_path = tmp_path / "root.jsonl"
    checkpoint_path = tmp_path / "checkpoint.json"
    waiting = AgentProcess(
        model=ScriptedModel([Wait("CONTINUE")]),
        tools=_tools(tmp_path),
        max_decisions=2,
        event_log=EventLog(root_path),
        checkpoint_path=checkpoint_path,
    ).run("legacy checkpoint", FileContentEquals("unused", "unused"))
    record = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    record["schema_version"] = 1
    record["state"]["fields"].pop("depth")
    record["state"]["fields"].pop("max_depth")
    checkpoint_path.write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    restored_model = ScriptedModel([])

    restored = AgentProcess(
        model=restored_model,
        tools=_tools(tmp_path),
        max_decisions=2,
        event_log=EventLog.load(root_path),
        checkpoint_path=checkpoint_path,
    ).resume()

    assert restored.status == "waiting"
    assert restored.state.execution_id == waiting.state.execution_id
    assert restored.state.actor_id == waiting.state.actor_id
    assert (restored.state.depth, restored.state.max_depth) == (0, 1)
    assert restored.state == fold_execution_state(restored.events)
    assert restored_model.received_requests == []


def test_restart_commits_one_frozen_spawn_without_resampling_or_new_decision(
    tmp_path,
):
    class CrashBeforeSpawn(EventLog):
        def append(self, event_type, payload, source_event_refs=()):
            if event_type == "CHILD_SPAWNED":
                raise RuntimeError("crash before Spawn append")
            return super().append(event_type, payload, source_event_refs)

    root_path = tmp_path / "root.jsonl"
    with pytest.raises(RuntimeError, match="before Spawn append"):
        AgentProcess(
            model=ScriptedModel([SpawnChild("one durable child")]),
            tools=_tools(tmp_path),
            max_decisions=2,
            max_depth=2,
            event_log=CrashBeforeSpawn(root_path),
        ).run("survive admission crash", FileContentEquals("unused", "unused"))

    crashed = EventLog.load(root_path)
    assert [event.event_type for event in crashed.events][-1] == "MODEL_DECISION"
    restored_model = ScriptedModel([])
    restored_root = AgentProcess(
        model=restored_model,
        tools=_tools(tmp_path),
        max_decisions=2,
        event_log=crashed,
    )

    pending = restored_root.resume()

    assert pending.status == "child_pending"
    assert pending.child_ref.depth == 1
    assert pending.child_ref.max_depth == 2
    assert pending.child_ref.parent_actor_id == pending.state.actor_id
    assert [event.event_type for event in pending.events].count("MODEL_DECISION") == 1
    assert [event.event_type for event in pending.events].count("CHILD_SPAWNED") == 1
    assert restored_model.received_requests == []

    second_model = ScriptedModel([])
    reloaded = AgentProcess(
        model=second_model,
        tools=_tools(tmp_path),
        max_decisions=2,
        event_log=EventLog.load(root_path),
    ).resume()
    assert reloaded.child_ref == pending.child_ref
    assert [event.event_type for event in reloaded.events].count("CHILD_SPAWNED") == 1
    assert second_model.received_requests == []


_RUN_REAL = (
    os.environ.get("RUN_DEEPSEEK_REAL_TESTS") == "1"
    and bool(os.environ.get("DEEPSEEK_API_KEY"))
)


@pytest.mark.skipif(
    not _RUN_REAL,
    reason="set RUN_DEEPSEEK_REAL_TESTS=1 and DEEPSEEK_API_KEY",
)
def test_real_deepseek_depth_two_recursive_chain_one_of_three(tmp_path):
    summaries = []
    validated = False
    for run_number in range(1, 4):
        workspace = tmp_path / f"recursive-depth2-{run_number}"
        workspace.mkdir()
        (workspace / "outer.txt").write_text("TARGET=answer", encoding="utf-8")
        (workspace / "inner.txt").write_text("VALUE=42", encoding="utf-8")
        tools = _tools(workspace)
        root = AgentProcess(
            model=DeepSeekModel(),
            tools=tools,
            max_decisions=8,
            max_depth=2,
            event_log=EventLog(workspace / "root.jsonl"),
        )
        child = None
        grandchild = None
        child_result = None
        grandchild_result = None
        try:
            root_result = root.run(
                "Determine the requested target from outer.txt. "
                "Delegation is available recursively when useful. "
                "Write answer.txt containing the final value.",
                FileContentEquals("answer.txt", "42"),
            )
            if root_result.status == "child_pending":
                child = AgentProcess.for_child(
                    root_result.child_ref,
                    model=DeepSeekModel(),
                    tools=tools,
                    max_decisions=7,
                )
                child_result = child.run_child()
                if child_result.status == "child_pending":
                    grandchild = AgentProcess.for_child(
                        child_result.child_ref,
                        model=DeepSeekModel(),
                        tools=tools,
                        max_decisions=6,
                    )
                    grandchild_result = grandchild.run_child()
                    if grandchild_result.status in ("completed", "failed"):
                        child_result = child.accept_child(grandchild_result)
                if child_result.status in ("completed", "failed"):
                    root_result = root.accept_child(child_result)

            root_events = [event.event_type for event in root_result.events]
            child_events = (
                [event.event_type for event in child_result.events]
                if child_result is not None
                else []
            )
            exact_output = (
                (workspace / "answer.txt").is_file()
                and (workspace / "answer.txt").read_text(encoding="utf-8")
                == "42"
            )
            full_chain = (
                root_result.status == "completed"
                and child_result is not None
                and child_result.status == "completed"
                and grandchild_result is not None
                and grandchild_result.status == "completed"
                and root_events.count("CHILD_SPAWNED") == 1
                and root_events.count("CHILD_RETURNED") == 1
                and child_events.count("CHILD_SPAWNED") == 1
                and child_events.count("CHILD_RETURNED") == 2
                and grandchild_result.state.depth == 2
                and "COMPLETION_VERIFIED" in root_events
                and exact_output
            )
            summaries.append(
                {
                    "run": run_number,
                    "root_status": root_result.status,
                    "child_status": (
                        child_result.status if child_result is not None else None
                    ),
                    "grandchild_status": (
                        grandchild_result.status
                        if grandchild_result is not None
                        else None
                    ),
                    "root_spawned": root_events.count("CHILD_SPAWNED"),
                    "child_spawned": child_events.count("CHILD_SPAWNED"),
                    "verified": "COMPLETION_VERIFIED" in root_events,
                    "exact_output": exact_output,
                }
            )
            if full_chain:
                validated = True
                break
        finally:
            if grandchild is not None:
                grandchild.close()
            if child is not None:
                child.close()
            root.close()

    assert validated, summaries
