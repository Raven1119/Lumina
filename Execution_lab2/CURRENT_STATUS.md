# Execution V2 Lab Status

## Implemented

- `execution.py` remains an isolated, standard-library-only lab module. No
  production Chat, Memory, Dream, or Mind code is imported or changed.
- Slice 1 behavior remains intact: typed tool requests, structured
  `ToolResult`, workspace-guarded read/write, explicit-argv bounded shell,
  failure-as-Observation, and a hard decision limit.
- The Action contract is now `ToolCall | Wait | ClaimComplete`. The only Wait
  condition is an exact, non-empty `event_type` string.
- Execution creation requires one frozen caller-owned
  `FileContentEquals(path, expected_content)`. That typed CompletionSpec is
  persisted in `EXECUTION_STARTED`, reconstructed by the State fold, visible
  through a bounded Context projection, and cannot be supplied or changed by
  `ClaimComplete`.
- `EventLog` can be in-memory or local append-only JSONL. A durable append is
  flushed and `fsync`ed before the event enters the authoritative in-memory
  sequence. `load()` rejects malformed records, schemas, causal links,
  sequences, and lifecycle transitions.
- Each event retains deterministic `event_id`, contiguous `sequence`, fixed
  `event_type`, deeply immutable payload, and past-only `source_event_refs`.
  Durable restart continues the same sequence; persisted lines are never
  rewritten by EventLog.
- `EXECUTION_STARTED` durably records one `execution_id` and one
  `root_actor_id`. Fresh `RootAgentProcess` objects loaded from the same log
  reconstruct the same identities.
- `ExecutionState` remains a pure fold of execution facts. It now derives
  `running | waiting | completed | failed`, Root identity, `waiting_for`, and
  the latest typed external event in addition to the Slice 2 state fields.
- `Checkpoint` internally derives one materialized `ExecutionState` snapshot
  from its EventLog prefix, plus
  `last_applied_event_sequence`, schema version, and an integrity digest of the
  snapshot plus its durable event prefix. Valid recovery checks that digest and
  folds only the tail. Missing checkpoints use full replay; stale checkpoints
  fold their durable tail; malformed or inconsistent checkpoints fail
  explicitly. Tests separately establish full-replay equivalence.
- A persisted `ROOT_WAITING` event makes State `waiting` and stops sampling.
  `resume()` performs no Model or Tool call while no matching event exists.
- `deliver_event(event_type, data)` first persists
  `EXTERNAL_EVENT_RECEIVED`. Exact typed matching alone appends `ROOT_WOKEN`
  and resumes; irrelevant events remain durable without waking Root. If a
  process dies after the matching event append but before `ROOT_WOKEN`, a fresh
  `resume()` mechanically finishes that wake.
- Resume builds the next bounded request from current Goal, reconstructed
  State, the incoming event, and the latest bounded Observation. It does not
  restore or inject an old transcript or model control flow.
- `DecisionFrame` still stores the exact `ModelRequest` object, exposed
  Action/tool contracts, raw response snapshot, structured Action, State
  version, and source refs for every new decision.
- Recovery still accepts only explicit safe tails, with one narrow exception:
  a dangling `TOOL_CALL_STARTED` carrying a `WriteRequest` is inspected
  against the current Host filesystem. Exact logical-content equality appends
  a causal `ACTION_RECONCILED` with `status=confirmed_applied`, path,
  intended/observed SHA-256, and observed character count. The old call event
  is not rewritten and the Write is not executed again.
- Missing, unreadable, or different Write targets remain UNKNOWN and raise an
  explicit unresolved recovery error without appending success or sampling the
  Model. Dangling `ShellRequest` remains explicitly unsupported; no generic
  effect reconciliation or retry path was added.
- A Root completion claim now appends `COMPLETION_CLAIMED`; it never terminates
  Execution directly. Runtime mechanically reads the current Host workspace
  against the execution-start `FileContentEquals` spec. Exact logical UTF-8
  equality appends causal `COMPLETION_VERIFIED` and then
  `EXECUTION_COMPLETED(status=verified)` without another Model call.
- Missing, mismatched, unreadable, or workspace-invalid completion targets
  append causal `COMPLETION_REJECTED` with typed evidence containing the spec
  type/fingerprint, observed path, match result, and bounded reason. State
  remains runnable and the next Root request receives a bounded rejection
  Observation; Runtime does not repair the file.

## Source mapping

The symbol-level sources, licenses, direct facts, Lumina adaptations, and
rejected architecture are recorded in `SOURCE_AUDIT.md`.

| Source | Audited version | Borrowed semantic |
| --- | --- | --- |
| OpenAI Codex | `a9ed4f154a4fad64acf538d6418d3ed012aeab86`, Apache-2.0 | Durable identity is distinct from live session/turn/request state; reopen identity before new work. |
| DeepSeek Harness | `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`, `0.1.1-rc.2`, MIT | Started-without-result is canonically UNKNOWN; verify external state before retry and append causal repair rather than mutating history. |
| Prime Agent | `514633727bf26d74f39f3119c2b0e31a5ceb2a9d`, `v0.8.1`, MIT | A live agent object is replaceable and Host-owned external state remains outside model working state (the authority conclusion is explicitly an inference). |
| LongHorizon-Harness | `a1dd930614972b92361c1b9cd6aac441a6db5a65`, `v0.1.7`, MIT | Agent completion claims require independent acceptance authority grounded in the current workspace; Lumina does not copy its LLM Auditor. |
| Temporal Server / Go SDK | `19a774302c613da9adc4436ab14278ccdca8e0a5` / `b7c242c6894df088a57a85b33d0586e908da8b93`, MIT | Durable history does not make an external Activity exactly once; a crash before completion is recorded can retry the effect. |

No upstream session/plugin ecosystem, Worker infrastructure, Manager ontology,
provider stack, IPython, Actor Directory, Child runtime, or generic event bus
was copied.

## Experiment results

Slice 5:

- **A - false completion rejected:** an initial claim against mismatched file
  content appends `COMPLETION_REJECTED`, leaves Execution runnable, and gives
  Root a bounded structured Observation. Root then writes the caller-required
  content, claims again, and reaches verified completion.
- **B - true completion verified:** a pre-satisfied file requires exactly one
  Model call and produces `CLAIMED -> VERIFIED -> COMPLETED`; no follow-up
  Model call is made.
- **C - Root cannot self-authorize:** `ClaimComplete` has no fields, rejects
  attempted `expected_content` or `success` arguments, and cannot mutate the
  frozen execution-start CompletionSpec.
- **D - restart after rejection:** a durable rejected claim reconstructs the
  same execution/root identities in non-completed runnable State. A fresh Root
  sees the rejection Observation, fixes the file, and can complete.
- **E - restart after verification:** a completed durable log reloads as
  completed with zero new Model calls and zero Tool actions, even if the file
  changes after terminal completion; verification is not rerun.
- **F - event/state replay:** durable completion events reload exactly;
  full-log fold equals restored State, and the immediate causal references are
  `MODEL_DECISION -> COMPLETION_CLAIMED -> COMPLETION_VERIFIED ->
  EXECUTION_COMPLETED`.
- Separate experiments establish deterministic `missing`, `content_mismatch`,
  and `unreadable` rejection outcomes without Tool execution or fabricated
  success.

Slice 4:

- **A - committed Write crash:** `TOOL_CALL_STARTED`, real filesystem write,
  simulated process death before `TOOL_RESULT`, total Runtime replacement,
  exact Host inspection, `ACTION_RECONCILED(confirmed_applied)`, and Complete
  succeeds. The Write execution count remains exactly 1.
- **B - replay and identity:** after recovered completion,
  `fold(full durable EventLog) == restored ExecutionState`;
  `execution_id` and `root_actor_id` remain unchanged across replacement.
- **C - ambiguous reality:** a dangling Write whose current target contains
  different content is not replayed and does not produce fabricated success;
  recovery remains explicitly unresolved and the dangling call stays last.
- **D - unsupported Shell:** a dangling `ShellRequest` reports unsupported,
  appends no reconciliation event, executes no Shell on recovery, and samples
  no Model.

Slice 3 regression evidence:

- **A — durable replay:** a Runtime-created JSONL reloads to an equal immutable
  Event tuple, and `fold(original) == fold(reloaded)`.
- **B — checkpoint equivalence:** a WAIT checkpoint at sequence 6 remains valid
  after external-event/wake/tool/completion tail events; verified
  checkpoint-plus-tail State equals full replay State. Missing checkpoint also
  reaches the same State.
- **C — WAIT zero sampling:** repeated `resume()` while WAITING leaves Model
  calls unchanged and executes no Tool. A wake at the hard decision bound adds
  no extra Model call.
- **D — full restart and wake:** Tool A, Wait, total object destruction, fresh
  EventLog/Root/Model/ToolHost construction, `CONTINUE`, Tool B, and Complete
  succeeds with unchanged execution and Root ids.
- **E — no completed-action replay:** the workspace sentinel Tool A executes
  exactly once across the full restart; only Tool B executes afterward.
- **F — irrelevant event:** `NOISE` is durable, leaves State WAITING, and does
  not sample. A later `CONTINUE` is the only event that wakes Root.
- **G — recovery boundaries:** missing checkpoint falls back to full replay;
  stale checkpoint folds its tail; malformed JSONL and corrupt checkpoint fail
  explicitly. A failed durable append does not enter the authoritative event
  tuple.

## Validated invariants

- Durable EventLog is historical authority; State and Checkpoint are
  disposable projections, and Context is a separate bounded view.
- Completed Action results are not replayed. Recovery resumes current State,
  not historical model decisions.
- A confirmed interrupted Write advances State only through the appended
  `ACTION_RECONCILED` fact. Exact current reality is evidence of the requested
  postcondition, not an exactly-once execution guarantee.
- WAITING performs zero polling, Model sampling, or Tool execution.
- Event matching is exact typed equality and contains no semantic planning.
- Canonical result values remain in events while model-visible projections are
  explicitly truncated to the configured absolute character bound.
- DecisionFrame exact-request identity and Slice 1 ToolHost/failure behavior
  remain covered by regression tests.
- Validation: `Execution_lab2` 43 passed; root 328 passed / 24 skipped;
  Conversation Memory 163 passed / 45 skipped; Dream 36 passed / 1 skipped.

## Known limits

- UNKNOWN side-effect crash reconciliation is implemented only for a dangling
  `WriteRequest` whose target can be read and exactly equals intended content.
  All ambiguous Writes and every other effect kind remain unresolved.
- Completion verification supports only caller-declared
  `FileContentEquals(path, expected_content)`. There is no natural-language
  goal judgment, Reviewer Agent, LLM verifier, registry, composite predicate,
  test runner, or second verifier type.
- IPython: **NOT IMPLEMENTED**.
- real provider: **NOT IMPLEMENTED**.
- Child/recursion: **NOT IMPLEMENTED**.
- Restart is supported at a durable settled result, WAIT/external-event safe
  point, initial start, terminal event, or the exact confirmed-Write case
  above. Other unsettled tool calls remain unsupported/unresolved.
- JSONL and Checkpoint are local single-process files. There is no multi-writer
  coordination, database, checkpoint rotation, scheduler, background worker,
  generic event bus, generic verifier framework, or resource accounting.
- Context is bounded in Python characters rather than provider tokens or UTF-8
  bytes. Shell isolation remains cwd-based rather than an OS sandbox.
- Checkpoint recovery hashes the prefix and folds only the tail, but no
  replay-performance benchmark has been measured.

Further Execution stages require a separate approved task.
