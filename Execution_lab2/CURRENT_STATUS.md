# Execution V2 Lab Status

## Implemented

- `execution.py` remains an isolated, standard-library-only lab module; no
  production Chat, Memory, Dream, or Mind code is imported or changed.
- Slice 1 behavior remains intact: typed `ToolCall | Complete`, structured
  `ToolResult`, workspace-guarded read/write, explicit-argv bounded shell,
  failure-as-Observation, and a hard decision limit.
- `EventLog` appends the fixed Slice 1 facts `EXECUTION_STARTED`,
  `MODEL_DECISION`, `TOOL_CALL_STARTED`, `TOOL_RESULT`, `TOOL_FAILED`,
  `EXECUTION_COMPLETED`, and `EXECUTION_FAILED`. Each immutable event has a
  deterministic id, monotonic sequence, payload, and past-only causal refs.
- `ExecutionState` is produced only by `fold_execution_state(events)`. It holds
  current goal/status/version, decision count, latest Observation, last
  action/result, and completion/failure; it does not copy the event history.
- Every model call receives one immutable `ModelRequest`: a JSON projection of
  Goal + current State + latest relevant Observation, plus the exact fixed tool
  contracts and source event refs. Context has an explicit character bound
  (`2,000` by default, configurable down to `512`).
- Text fields carry `truncated` and `original_chars`. Canonical `ToolResult`
  values remain in the EventLog while the request receives only the bounded
  projection.
- Every sampling boundary emits a `DecisionFrame` containing decision/model
  identity, goal, pre-decision State version, source refs, the same actual
  request object passed to the model, actual exposed tools, raw response, and
  parsed `ToolCall | Complete` (or `None` for an unknown response).

## Source mapping

The symbol-by-symbol audit, original contracts, licenses, Lumina adaptations,
and rejected scope are recorded in `SOURCE_AUDIT.md`.

| Source | Audited version | Behavior used |
| --- | --- | --- |
| OpenAI Codex | `3ba7b6941d3caf6eec5b3c4e564988ee57d3f083`, Apache-2.0 | Request-scoped context/tool snapshot and explicit canonical-vs-visible tool-result truncation. |
| DeepSeek Harness | `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`, `0.1.1-rc.2`, MIT | Append-only sequenced facts, fold-derived surface, and causal source-event refs. |
| Prime Agent | `514633727bf26d74f39f3119c2b0e31a5ceb2a9d`, `v0.8.1`, MIT | Host-controlled tool side effects and a separate model-visible working projection. |
| LongHorizon-Harness | `a1dd930614972b92361c1b9cd6aac441a6db5a65`, `v0.1.7`, MIT | Separation of execution evidence, derived progress state, and verification conclusions. |

No session/plugin framework, persistence layer, IPython, provider stack,
Manager/Executor/Auditor roles, recovery protocol, or child-agent machinery was
copied.

## Experiment results

- **A   reconstruction:** the 4-decision
  `read -> write -> read -> Complete` task succeeds, and its returned final
  State equals `fold(full EventLog)` exactly. Event sequences are contiguous;
  tool results cite their tool-call event.
- **B   bounded-context comparison:** over 24 tool interactions with the same
  completed task result, a test-only full-history serialization grows from
  `212` to `4,894` characters. The State-derived Context is `250` characters
  initially and stays between `827` and `833` after Observations appear, under
  the configured `900`-character bound.
- **C   large result:** canonical EventLog output retains all `20,028`
  characters. The next actual model Context is exactly `900` characters and
  exposes `252` output characters with `truncated=true` and
  `original_chars=20028`; the hidden tail is absent.
- **D   exact fidelity:** for every call,
  `ScriptedModel.received_requests[i] is DecisionFrame.actual_request`; model
  id, tools, raw response, structured Action, State version, and source refs
  match one-for-one.
- **E   failure chain:** missing read becomes `TOOL_FAILED`, folds into State,
  appears in the next bounded request, and is cited by its DecisionFrame. The
  alternative write is the scripted model response; Runtime adds no recovery
  decision.

## Validated invariants

- EventLog is the in-run execution-fact authority; the materialized State is
  disposable and exactly replayable.
- Model requests do not include or traverse the full event transcript.
- Neither a large Observation nor a large Goal/action text can exceed the
  configured Context character bound; truncation remains explicit.
- DecisionFrame captures the request before/at the real call boundary by
  retaining the exact object passed to `Model.decide`, not by reconstruction.
- Slice 1 completion/failure semantics and typed ToolHost boundaries are
  unchanged.
- Validation: `Execution_lab2` 13 passed; root 328 passed / 24 skipped;
  Conversation Memory 163 passed / 45 skipped; Dream 36 passed / 1 skipped.

## Known limits

- EventLog and DecisionFrames are in-memory results of one synchronous run;
  there is no persistence, checkpoint, restart, WAIT, pause/resume, or recovery.
- The Context budget is measured in Python characters, not provider tokens or
  UTF-8 bytes. No provider adapter exists in this lab slice.
- Shell isolation remains cwd-based rather than an OS security sandbox, and
  `subprocess.run` may buffer output before ToolHost truncates it.
- No completion verifier, unknown-action reconciliation, semantic retrieval,
  compaction model, planner, scheduler, Child/Spawn, or multi-agent runtime is
  implemented.

## Next unanswered question

If a separately approved slice adds durability, can it persist this same
append-only fact contract without making stored State or reconstructed model
requests a second authority?
