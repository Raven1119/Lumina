import json
import os

import pytest

from Execution_lab2.deepseek_model import DeepSeekModel
from Execution_lab2.execution import (
    AgentProcess,
    CHILD_IPYTHON_TOOL_CONTRACTS,
    ClaimComplete,
    EventLog,
    FileContentEquals,
    IPythonCode,
    IPythonObservation,
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
from Execution_lab2.ipython_control import PersistentIPython


def test_root_admits_three_sibling_children_and_rejects_a_fourth(tmp_path):
    root = AgentProcess(
        model=ScriptedModel(
            [
                SpawnChild("branch A"),
                SpawnChild("branch B"),
                SpawnChild("branch C"),
                SpawnChild("branch D"),
            ]
        ),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=4,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )

    first = root.run(
        "form sibling branches",
        FileContentEquals("unused.txt", "unused"),
    )
    second = root.resume()
    third = root.resume()
    limited = root.resume()

    child_refs = third.state.child_refs
    assert first.status == second.status == third.status == "child_pending"
    assert limited.status == "failed"
    assert limited.failure == "child_limit_reached"
    assert [child.local_goal for child in child_refs] == [
        "branch A",
        "branch B",
        "branch C",
    ]
    assert len({child.child_actor_id for child in child_refs}) == 3
    assert len({child.child_execution_id for child in child_refs}) == 3
    assert {child.parent_actor_id for child in child_refs} == {
        third.state.root_actor_id
    }
    assert [event.event_type for event in limited.events].count(
        "CHILD_SPAWNED"
    ) == 3
    assert limited.state == fold_execution_state(limited.events)


def test_three_child_outcomes_fit_the_minimum_root_context_budget(tmp_path):
    (tmp_path / "answer.txt").write_text("ready", encoding="utf-8")
    root_model = ScriptedModel(
        [
            SpawnChild("branch A"),
            SpawnChild("branch B"),
            SpawnChild("branch C"),
            Wait("CHILD_RESULT"),
            Wait("CHILD_RESULT"),
            Wait("CHILD_RESULT"),
            ToolCall(ReadRequest("answer.txt")),
            ClaimComplete(),
        ]
    )
    tools = ToolHost(SharedEnvironment(tmp_path))
    root = AgentProcess(
        model=root_model,
        tools=tools,
        max_decisions=8,
        max_context_chars=768,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    root.run(
        "keep three identified outcomes visible",
        FileContentEquals("answer.txt", "ready"),
    )
    root.resume()
    root.resume()
    waiting = root.resume()

    child_runs = [
        AgentProcess.for_child(
            child_ref,
            model=ScriptedModel([Return(result)]),
            tools=tools,
            max_decisions=1,
        ).run_child()
        for child_ref, result in zip(
            waiting.state.child_refs,
            ("alpha", "beta", "gamma"),
            strict=True,
        )
    ]
    root.accept_child(child_runs[0])
    root.accept_child(child_runs[1])
    completed = root.accept_child(child_runs[2])

    assert completed.status == "completed"
    final_request = root_model.received_requests[-1]
    assert final_request.context_size_chars <= 768
    visible_children = json.loads(final_request.context)["children"]
    assert visible_children["actor_ids"] == [
        child_ref.child_actor_id for child_ref in waiting.state.child_refs
    ]
    assert visible_children["facts"]["text"].splitlines() == [
        "0 returned alpha",
        "1 returned beta",
        "2 returned gamma",
    ]
    visible_observation = json.loads(final_request.context)["observation"]
    assert visible_observation["request"]["path"]["text"] == "answer.txt"
    assert visible_observation["result"]["output"]["text"] == "ready"


def test_sibling_contexts_are_isolated_while_world_and_outcomes_are_shared(
    tmp_path,
):
    (tmp_path / "answer.txt").write_text("ready", encoding="utf-8")
    root_model = ScriptedModel(
        [
            SpawnChild("A_PRIVATE: write shared.txt and return alpha"),
            SpawnChild("B_PRIVATE: read shared.txt and return beta"),
            Wait("CHILD_RESULT"),
            Wait("CHILD_RESULT"),
            ToolCall(ReadRequest("shared.txt")),
            ClaimComplete(),
        ]
    )
    tools = ToolHost(SharedEnvironment(tmp_path))
    root = AgentProcess(
        model=root_model,
        tools=tools,
        max_decisions=6,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )

    root.run(
        "form two independent branches",
        FileContentEquals("answer.txt", "ready"),
    )
    root.resume()
    waiting = root.resume()
    child_a_ref, child_b_ref = waiting.state.child_refs

    child_a_model = ScriptedModel(
        [
            ToolCall(WriteRequest("shared.txt", "from A")),
            Return("alpha"),
        ],
        identifier="child-a",
    )
    child_a = AgentProcess.for_child(
        child_a_ref,
        model=child_a_model,
        tools=tools,
        max_decisions=2,
    ).run_child()
    waiting_again = root.accept_child(child_a)

    child_b_model = ScriptedModel(
        [
            ToolCall(ReadRequest("shared.txt")),
            Return("beta"),
        ],
        identifier="child-b",
    )
    child_b = AgentProcess.for_child(
        child_b_ref,
        model=child_b_model,
        tools=tools,
        max_decisions=2,
    ).run_child()
    completed = root.accept_child(child_b)

    assert waiting.status == waiting_again.status == "waiting"
    assert completed.status == "completed"
    assert "B_PRIVATE" not in child_a_model.received_requests[0].context
    assert "A_PRIVATE" not in child_b_model.received_requests[0].context
    child_b_read = next(
        step.observation
        for step in child_b.steps
        if isinstance(step.action, ToolCall)
        and isinstance(step.action.request, ReadRequest)
    )
    assert child_b_read.result.output == "from A"
    returned = [
        event
        for event in completed.events
        if event.event_type == "CHILD_RETURNED"
    ]
    assert [
        (event.payload["child_actor_id"], event.payload["local_result"])
        for event in returned
    ] == [
        (child_a_ref.child_actor_id, "alpha"),
        (child_b_ref.child_actor_id, "beta"),
    ]
    final_context = root_model.received_requests[-1].context
    assert child_a_ref.child_actor_id in final_context
    assert child_b_ref.child_actor_id in final_context
    assert "alpha" in final_context
    assert "beta" in final_context
    visible_observation = json.loads(final_context)["observation"]
    assert visible_observation["request"]["path"]["text"] == "shared.txt"
    assert visible_observation["result"]["output"]["text"] == "from A"
    assert completed.state == fold_execution_state(completed.events)


def test_return_failure_and_return_remain_distinct_root_facts(tmp_path):
    (tmp_path / "answer.txt").write_text("ready", encoding="utf-8")
    root_model = ScriptedModel(
        [
            SpawnChild("A returns"),
            SpawnChild("B fails"),
            SpawnChild("C returns"),
            Wait("CHILD_RESULT"),
            Wait("CHILD_RESULT"),
            Wait("CHILD_RESULT"),
            ClaimComplete(),
        ]
    )
    tools = ToolHost(SharedEnvironment(tmp_path))
    root = AgentProcess(
        model=root_model,
        tools=tools,
        max_decisions=7,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    root.run(
        "preserve every sibling outcome",
        FileContentEquals("answer.txt", "ready"),
    )
    root.resume()
    root.resume()
    waiting = root.resume()
    child_a_ref, child_b_ref, child_c_ref = waiting.state.child_refs

    child_a = AgentProcess.for_child(
        child_a_ref,
        model=ScriptedModel([Return("alpha")]),
        tools=tools,
        max_decisions=1,
    ).run_child()
    child_b = AgentProcess.for_child(
        child_b_ref,
        model=ScriptedModel([{"unexpected": "decision"}]),
        tools=tools,
        max_decisions=1,
    ).run_child()
    child_c = AgentProcess.for_child(
        child_c_ref,
        model=ScriptedModel([Return("gamma")]),
        tools=tools,
        max_decisions=1,
    ).run_child()

    root.accept_child(child_a)
    root.accept_child(child_b)
    completed = root.accept_child(child_c)

    assert completed.status == "completed"
    assert [
        (outcome.child_ref.child_actor_id, outcome.status)
        for outcome in completed.state.child_outcomes
    ] == [
        (child_a_ref.child_actor_id, "returned"),
        (child_b_ref.child_actor_id, "failed"),
        (child_c_ref.child_actor_id, "returned"),
    ]
    event_types = [event.event_type for event in completed.events]
    assert event_types.count("CHILD_RETURNED") == 2
    assert event_types.count("CHILD_FAILED") == 1
    assert child_b.failure == "unknown_action:dict"
    assert "unknown_action:dict" in root_model.received_requests[-1].context
    assert completed.state == fold_execution_state(completed.events)

    restored_model = ScriptedModel([])
    restored = AgentProcess(
        model=restored_model,
        tools=tools,
        max_decisions=7,
        event_log=EventLog.load(tmp_path / "root.jsonl"),
    )
    with pytest.raises(ValueError, match="no pending Child"):
        restored.accept_child(child_b)
    reloaded = restored.resume()
    assert reloaded.status == "completed"
    assert [event.event_type for event in reloaded.events] == event_types
    assert restored_model.received_requests == []


def test_restart_after_sibling_delivery_does_not_respawn_or_redeliver(
    tmp_path,
):
    class CrashAfterBothReturns:
        identifier = "crashing-root"

        def __init__(self):
            self.actions = iter(
                [
                    SpawnChild("return alpha"),
                    SpawnChild("return beta"),
                    Wait("CHILD_RESULT"),
                    Wait("CHILD_RESULT"),
                ]
            )

        def decide(self, request):
            try:
                return next(self.actions)
            except StopIteration:
                raise SystemExit("crash after durable sibling returns")

    root_log_path = tmp_path / "root.jsonl"
    tools = ToolHost(SharedEnvironment(tmp_path))
    root = AgentProcess(
        model=CrashAfterBothReturns(),
        tools=tools,
        max_decisions=6,
        event_log=EventLog(root_log_path),
    )
    root.run(
        "survive sibling delivery",
        FileContentEquals("answer.txt", "alpha+beta"),
    )
    root.resume()
    waiting = root.resume()
    child_a_ref, child_b_ref = waiting.state.child_refs
    original_execution_id = waiting.state.execution_id
    original_root_actor_id = waiting.state.root_actor_id

    child_a = AgentProcess.for_child(
        child_a_ref,
        model=ScriptedModel([Return("alpha")]),
        tools=tools,
        max_decisions=1,
    ).run_child()
    child_b = AgentProcess.for_child(
        child_b_ref,
        model=ScriptedModel([Return("beta")]),
        tools=tools,
        max_decisions=1,
    ).run_child()
    root.accept_child(child_a)
    with pytest.raises(
        SystemExit,
        match="crash after durable sibling returns",
    ):
        root.accept_child(child_b)

    restored_model = ScriptedModel(
        [
            ToolCall(WriteRequest("answer.txt", "alpha+beta")),
            ClaimComplete(),
        ]
    )
    restored = AgentProcess(
        model=restored_model,
        tools=tools,
        max_decisions=6,
        event_log=EventLog.load(root_log_path),
    )
    with pytest.raises(ValueError, match="no pending Child"):
        restored.accept_child(child_a)
    with pytest.raises(ValueError, match="no pending Child"):
        restored.accept_child(child_b)
    completed = restored.resume()

    event_types = [event.event_type for event in completed.events]
    assert completed.status == "completed"
    assert event_types.count("CHILD_SPAWNED") == 2
    assert event_types.count("CHILD_RETURNED") == 2
    assert completed.state.execution_id == original_execution_id
    assert completed.state.root_actor_id == original_root_actor_id
    assert [child.child_actor_id for child in completed.state.child_refs] == [
        child_a_ref.child_actor_id,
        child_b_ref.child_actor_id,
    ]
    assert [outcome.local_result for outcome in completed.state.child_outcomes] == [
        "alpha",
        "beta",
    ]
    assert len(restored_model.received_requests) == 2
    assert completed.state == fold_execution_state(completed.events)


def test_three_siblings_have_independent_live_ipython_namespaces(tmp_path):
    root = AgentProcess(
        model=ScriptedModel(
            [
                SpawnChild("A_CONTEXT_ONLY"),
                SpawnChild("B_CONTEXT_ONLY"),
                SpawnChild("C_CONTEXT_ONLY"),
            ]
        ),
        tools=ToolHost(SharedEnvironment(tmp_path)),
        max_decisions=3,
        event_log=EventLog(tmp_path / "root.jsonl"),
    )
    root.run(
        "create three isolated siblings",
        FileContentEquals("unused.txt", "unused"),
    )
    root.resume()
    spawned = root.resume()
    refs = spawned.state.child_refs
    labels = ("alpha", "beta", "gamma")
    controls = [PersistentIPython(tmp_path) for _ in refs]
    child_results = []
    child_models = []

    try:
        for index, (child_ref, label, control) in enumerate(
            zip(refs, labels, controls, strict=True)
        ):
            own_name = f"private_{index}"
            other_names = [
                f"private_{other}"
                for other in range(len(refs))
                if other != index
            ]
            model = ScriptedModel(
                [
                    IPythonCode(
                        f"{own_name} = {label!r}; "
                        f"print({own_name}, "
                        + ", ".join(
                            f"{name!r} in globals()" for name in other_names
                        )
                        + ")"
                    ),
                    Return(label),
                ],
                identifier=f"child-{label}",
            )
            model.tool_contracts = CHILD_IPYTHON_TOOL_CONTRACTS
            child_models.append(model)
            child_results.append(
                AgentProcess.for_child(
                    child_ref,
                    model=model,
                    tools=ToolHost(SharedEnvironment(tmp_path)),
                    max_decisions=2,
                    ipython_control=control,
                ).run_child()
            )

        for index, (result, model, label) in enumerate(
            zip(child_results, child_models, labels, strict=True)
        ):
            python_observation = next(
                step.observation
                for step in result.steps
                if isinstance(step.observation, IPythonObservation)
            )
            assert python_observation.result.output.strip() == (
                f"{label} False False"
            )
            assert result.output == label
            for other_index in range(len(refs)):
                if other_index != index:
                    assert (
                        f"{chr(65 + other_index)}_CONTEXT_ONLY"
                        not in model.received_requests[0].context
                    )
    finally:
        for control in controls:
            control.close()

    assert all(control.is_alive is False for control in controls)
    assert len({child.event_log_path for child in refs}) == 3


_RUN_REAL = (
    os.environ.get("RUN_DEEPSEEK_REAL_TESTS") == "1"
    and bool(os.environ.get("DEEPSEEK_API_KEY"))
)


@pytest.mark.skipif(
    not _RUN_REAL,
    reason="set RUN_DEEPSEEK_REAL_TESTS=1 and DEEPSEEK_API_KEY",
)
def test_real_deepseek_multi_child_full_chain_one_of_three(tmp_path):
    summaries = []
    validated = False
    for run_number in range(1, 4):
        workspace = tmp_path / f"multi-child-{run_number}"
        workspace.mkdir()
        (workspace / "a.txt").write_text("17", encoding="utf-8")
        (workspace / "b.txt").write_text("25", encoding="utf-8")
        tools = ToolHost(SharedEnvironment(workspace))
        root = AgentProcess(
            model=DeepSeekModel(),
            tools=tools,
            max_decisions=10,
            event_log=EventLog(workspace / "root.jsonl"),
        )
        child_processes = []
        child_results = {}
        try:
            root_result = root.run(
                "Use separate child processes to inspect a.txt and b.txt. "
                "Combine their findings and write answer.txt containing their "
                "sum. You can use multiple Child processes.",
                FileContentEquals("answer.txt", "42"),
            )
            for _ in range(20):
                if root_result.status in ("completed", "failed", "suspended"):
                    break
                pending = [
                    child
                    for child in root_result.state.pending_child_refs
                    if child.child_actor_id not in child_results
                ]
                if root_result.status == "child_pending":
                    if len(root_result.state.child_refs) < 2 and not (
                        root_result.state.child_outcomes
                    ):
                        root_result = root.resume()
                        continue
                    if not pending:
                        root_result = root.resume()
                        continue
                elif root_result.status == "waiting":
                    if (
                        root_result.state.waiting_for != "CHILD_RESULT"
                        or not pending
                    ):
                        break
                else:
                    break

                child_ref = pending[0]
                child = AgentProcess.for_child(
                    child_ref,
                    model=DeepSeekModel(),
                    tools=tools,
                    max_decisions=5,
                )
                child_processes.append(child)
                child_result = child.run_child()
                child_results[child_ref.child_actor_id] = child_result
                if child_result.status not in ("completed", "failed"):
                    break
                root_result = root.accept_child(child_result)

            event_types = [event.event_type for event in root_result.events]
            returned_ids = [
                event.payload["child_actor_id"]
                for event in root_result.events
                if event.event_type == "CHILD_RETURNED"
            ]
            output_match = (
                (workspace / "answer.txt").is_file()
                and (workspace / "answer.txt").read_text(encoding="utf-8")
                == "42"
            )
            full_chain = (
                root_result.status == "completed"
                and len(set(returned_ids)) >= 2
                and len(
                    [
                        result
                        for result in child_results.values()
                        if result.status == "completed"
                    ]
                )
                >= 2
                and event_types.count("CHILD_SPAWNED") >= 2
                and "COMPLETION_VERIFIED" in event_types
                and output_match
            )
            summaries.append(
                {
                    "run": run_number,
                    "root_status": root_result.status,
                    "spawned": event_types.count("CHILD_SPAWNED"),
                    "returned": event_types.count("CHILD_RETURNED"),
                    "failed": event_types.count("CHILD_FAILED"),
                    "verified": "COMPLETION_VERIFIED" in event_types,
                    "output_match": output_match,
                    "full_chain": full_chain,
                }
            )
            assert root_result.state == fold_execution_state(root_result.events)
            for child_result in child_results.values():
                assert child_result.state == fold_execution_state(
                    child_result.events
                )
            if full_chain:
                validated = True
                break
        finally:
            for child in child_processes:
                child.close()
            root.close()

    print("DEEPSEEK_REAL_MULTI_CHILD=" + json.dumps(summaries, sort_keys=True))
    assert validated
