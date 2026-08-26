# Execution V2 Lab Status

## Implemented

- An isolated, standard-library-only module in `Execution_lab2/execution.py`;
  it imports no production Chat, Memory, Dream, or Mind code.
- `SharedEnvironment` identifies one existing filesystem workspace owned
  outside the runtime.
- `ToolHost.execute()` accepts typed `ReadRequest`, `WriteRequest`, and
  `ShellRequest` values and returns one structured `ToolResult`.
- File paths reject absolute paths and resolved traversal outside the
  workspace. Shell uses explicit argv, `shell=False`, the workspace as cwd,
  a fixed timeout, and bounded returned text.
- `Model.decide()` is the provider-neutral seam. `ScriptedModel` is the
  only adapter.
- Actions are the typed `ToolCall | Complete` union. No text or JSON action
  parser exists.
- `RootAgentProcess.run()` mechanically relays the latest Observation to the
  Model, executes one action at a time, records lightweight `ExecutionStep`
  values, and stops on Complete, unknown Action, or the decision limit.
- Runtime code contains no semantic planning, tool selection, retry policy, or
  recovery strategy.

### Source lineage

| Source | Version / license | Source symbol or contract | Lumina adaptation |
| --- | --- | --- | --- |
| [OpenAI Codex](https://github.com/openai/codex/blob/3ba7b6941d3caf6eec5b3c4e564988ee57d3f083/codex-rs/core/src/tools/context.rs) | `3ba7b6941d3caf6eec5b3c4e564988ee57d3f083`, Apache-2.0 | `ExecCommandToolOutput::truncated_output`: raw command output plus a configured budget becomes explicitly bounded model-facing output. | `ToolHost._bounded` applies the same bounded-result decision with a character budget and a `truncated` fact; Codex code was not copied. |
| [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent/blob/5146337/packages/coding-agent/docs/usage.md#design-principles) | `5146337`, MIT | The TypeScript host owns provider calls and tool execution while the model-facing process chooses actions; tool results return to the process. | Root owns semantic choices through `Model.decide`; the Python host only dispatches typed actions. IPython and recursive agents are intentionally omitted. |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/blob/b150a551b8d465e31e418e1b2eaf5e79bbb7d28e/packages/llm/llm/README.md#messages-messagets-and-content-blocks-typests) | `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`, MIT | `createToolResultMessage(..., isError)` and typed tool-result blocks preserve success/failure as model-visible data. | `ToolResult` plus `Observation` carries stable error codes and diagnostic text into the next decision; no DSH event/session/plugin machinery is included. |

## Validated

- Case A: 4 decisions, 3 real tool interactions
  (`read -> write -> read -> Complete`); final file content is correct.
- Case B: missing-file `not_found` is visible in the next model context;
  ScriptedModel then chooses a successful write and Complete.
- Case C: both `../` and absolute-path writes return
  `workspace_boundary`; the external sentinel remains unchanged.
- Case D: a never-Complete script samples exactly 3 decisions, executes no
  fourth action, and returns `decision_limit_reached`.
- Additional checks cover real shell cwd/exit/stderr, timeout, output
  truncation, invalid request fields, and unknown Action/Tool handling.
- `python -m pytest Execution_lab2 -q`: 8 passed.
- `python -m pytest -q`: 328 passed, 24 skipped.

## Not implemented

EventLog, StateReducer, persistent ExecutionState, Checkpoint, DecisionFrame
persistence, IPython, provider adapters, Wait/wake, pause/resume, crash
recovery, completion verification, Child/Spawn/recursion, Blackboard,
Capability Search, plugins, resource accounting, parallel or multi-tool
execution, Planner/DAG/Swarm, and production-organ integration.

The V1 implementation was not read or copied.

## Observed limitations

- Shell isolation is cwd-based, not an operating-system security sandbox; a
  launched process retains the host account's filesystem permissions.
- Captured shell output is bounded before it enters `ToolResult`, but
  `subprocess.run` may buffer more output internally before truncation.

## Next experimental question

Can this same semantic-actor/mechanical-runtime split be preserved when the
next approved slice adds durable execution facts, without adding planning or
recovery policy to Runtime?
