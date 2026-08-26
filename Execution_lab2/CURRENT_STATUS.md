# Execution V2 Lab Status

## Implemented

- `execution.py` remains an isolated lab module. Its one programmable
  control-plane helper uses pinned `jupyter_client` and `ipykernel`; no
  production Chat, Memory, Dream, or Mind code is imported or changed.
- Slice 1 behavior remains intact: typed tool requests, structured
  `ToolResult`, workspace-guarded read/write, explicit-argv bounded shell,
  failure-as-Observation, and a hard decision limit.
- The Action contract is now
  `ToolCall | IPythonCode | Wait | ClaimComplete`. The only Wait condition
  is an exact, non-empty `event_type` string.
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
- Slice 6 adds one fixed `DeepSeekModel` adapter for the official
  `deepseek-v4-pro` Chat Completions endpoint. Native-mode requests use
  `read`, `write`, `shell`, `wait`, and `claim_complete` function schemas;
  IPython mode uses only `ipython`, `wait`, and `claim_complete`. Both use
  `thinking={"type":"disabled"}` and `stream=false`. There is no generic
  provider registry, SDK session, retry, fallback, streaming, parallel
  execution, or scheduler.
- The adapter accepts one native call or at most four ordinary sibling calls.
  It parses the whole response and validates the count, every call shape,
  unique non-empty call ids, every tool name, JSON, exact arguments, and the
  control rule before any Host effect. Any batch containing `wait` or
  `claim_complete` with count greater than one is rejected as a whole.
- Valid ordinary siblings execute strictly in model order, one start/settle at
  a time. A committed Tool failure remains an Observation and later siblings
  still execute. There is no abort policy, rollback, transaction, dependency
  inference, concurrency classifier, or rolling pool.
- Every DeepSeek-issued `tool_call.id` is persisted with the ordered typed
  native decision, `DecisionFrame`, individual started event, and structured
  `Observation`. The next provider request retains the original assistant
  `tool_calls` array and emits matching `role=tool` messages in model order.
  A fresh Runtime after WAIT reconstructs that
  continuation from durable frames and bounded current facts; the adapter has
  no private message transcript.
- `DecisionFrame` now retains the actual Lumina request, actual exposed Lumina
  contracts, secret-free provider-wire request, reasoning-free provider
  response, one typed Action or ordered Action tuple, matching call id(s), and
  source refs. API credentials
  are read only from `DEEPSEEK_API_KEY` inside the HTTP send boundary and are
  not part of provider payloads, frames, events, or test artifacts.
- Native continuation applies one shared `model_visible_context_limit` to the
  aggregate dynamic `user` plus all `tool` message content. They remain structured
  JSON projections with explicit text truncation; fixed system text and the
  five fixed schemas contain no ToolResult/EventLog data. A 20,000-character
  canonical read at the minimum 768-character Runtime limit stays complete in
  EventLog while all dynamic native continuation content together remains at
  or below 768 characters.
- Two 20,000-character sibling reads and separate four-sibling success and
  four-sibling failure batches were verified to remain canonical and complete
  while all ordered provider-visible results plus user context stayed within
  the minimum 768-character aggregate bound.
- Normal Tool results are rejected by EventLog if their observation call id
  differs from the causal provider decision. The same id also survives the
  committed-Write crash window: `ACTION_RECONCILED` derives its structured
  Observation with the original durable DeepSeek call id before continuation.
- The promoted IPython arm exposes only `ipython(code)`, `wait`, and
  `claim_complete`. A lazy `PersistentIPython` owns at most one
  `ipykernel` for one live Root, fixes its initial cwd to the shared
  workspace, preserves Python namespace across decisions, and closes the
  kernel at terminal execution or explicit process close.
- Multiple IPython calls in one valid response use that same live kernel in
  strict order. The maintained acceptance test executes `x = 41` followed by
  `print(x + 1)` and observes `42` from the second sibling.
- Runtime records
  `MODEL_DECISION -> IPYTHON_EXECUTION_STARTED -> RESULT/FAILED` with the
  code SHA-256, provider call id, bounded output/error, truthful original
  output character count, and causal refs. It does not invent ToolHost events
  for Python's internal filesystem or subprocess operations; current
  filesystem reality plus Completion Verification remains acceptance
  authority.
- IPython code, execution time, and captured output have fixed bounds.
  Generated Python intentionally has the worker OS permissions and is not a
  sandbox. `DEEPSEEK_API_KEY` is removed from the kernel environment.
- Runtime restart preserves durable Root/EventLog/State identity but not
  Python namespace. A resumed Root lazily starts a fresh kernel. A crash tail
  at `IPYTHON_EXECUTION_STARTED` is explicitly unresolved; code is not
  replayed automatically and no kernel snapshot/dill path exists.

## Fresh Python Code Mode experiment gate

This section is retained as historical evidence for the earlier, stricter
typed-binding-only authority requirement. The current task explicitly
superseded that requirement for persistent IPython by granting IPython and
ToolHost equal trusted local OS/workspace authority. It does not retroactively
turn fresh Code Mode into an isolation mechanism.

- **Result: BLOCKED before implementation.** The task required generated
  Python to be unable to access filesystem, process, or network reality except
  through typed bindings that re-enter the existing Runtime and `ToolHost`.
- The current lab has no code-isolation substrate. A normal Python subprocess
  or same-process `exec` inherits Host permissions and can directly use
  `open`, `os.remove`, `subprocess`, or `Path.write_text`, bypassing workspace
  checks, structured `ToolResult`, EventLog, and causal identity.
- Existing Shell execution is `subprocess.run(..., cwd=workspace)`; the working
  directory is not an OS sandbox. No dependency or module supplies a
  container, AppContainer, restricted process, or equivalent deny-by-default
  Python backend.
- Source audit confirmed the desired DSH orchestration semantics but also its
  explicit bash-equivalent, non-security-boundary trust posture. Prime's
  persistent IPython likewise executes with worker OS permissions and is only
  a future comparison, not an isolation solution.
- No `run_code` schema, `code_mode.py`, test module, prompt change, Runtime or
  ToolHost change, credential load, or live DeepSeek A/B call was made. A
  restricted-builtins, AST, or import-filter Python jail was deliberately not
  invented.
- The smallest unblocker is a pre-existing independently verified process or
  container sandbox that denies direct filesystem/process/network authority
  and permits only bounded typed IPC bindings. Building that substrate is
  outside this task.

## Source mapping

The symbol-level sources, licenses, direct facts, Lumina adaptations, and
rejected architecture are recorded in `SOURCE_AUDIT.md`.

| Source | Audited version | Borrowed semantic |
| --- | --- | --- |
| DeepSeek official API | live docs audited 2026-08-27; no source commit or stated docs license | Ordered `tool_calls[]`, exact per-call result correlation, fixed `deepseek-v4-pro` request shape, and explicit non-thinking mode. |
| OpenAI Codex | sibling-call re-audit pin `bde9db1375667c50dcc0c2b52532a4e2672571c2`, Apache-2.0 | Preserve each call identity across Host dispatch and model-visible output; no synthetic batch id. |
| DeepSeek Harness | `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`, `0.1.1-rc.2`, MIT | Whole-response planning, distinct sibling identity, and model-ordered commit; parallel scheduling was not copied. Its UNKNOWN repair semantics remain the Write-reconciliation reference. |
| Prime Agent | `514633727bf26d74f39f3119c2b0e31a5ceb2a9d`, `v0.8.1`, MIT | One persistent kernel per live session, worker-OS trust boundary, and Host-owned kernel lifecycle. |
| Jupyter | `jupyter_client==8.9.1` / `ipykernel==7.3.0`, BSD-3-Clause | Mature kernel start, execute, IOPub result collection, interrupt, and shutdown. |
| LongHorizon-Harness | `a1dd930614972b92361c1b9cd6aac441a6db5a65`, `v0.1.7`, MIT | Agent completion claims require independent acceptance authority grounded in the current workspace; Lumina does not copy its LLM Auditor. |
| Temporal Server / Go SDK | `19a774302c613da9adc4436ab14278ccdca8e0a5` / `b7c242c6894df088a57a85b33d0586e908da8b93`, MIT | Durable history does not make an external Activity exactly once; a crash before completion is recorded can retry the effect. |

No upstream session/plugin ecosystem, Worker infrastructure, Manager ontology,
provider stack, RLM, Actor Directory, Child runtime, or generic event bus was
copied.

## Experiment results

Fresh Python Code Mode gate:

- **A/B task 1:** not run; Native 0, Code 0.
- **A/B task 2:** not run; Native 0, Code 0.
- No model calls, Tool/capability calls, token usage, wall-time comparison,
  context-exposure comparison, syntax/runtime failures, or live credentials
  were generated by this blocked experiment.
- The programmable Code Mode value hypothesis remains unmeasured. Persistent
  IPython was subsequently authorized under a different equal-local-authority
  trust model; that later result does not change this historical isolation
  finding.

Persistent IPython A/B:

- **Result: PROMOTE.** The frozen real experiment used two tasks, two arms,
  three fresh runs per arm, the same `deepseek-v4-pro`, CompletionSpec,
  workspaces, decision bound, and Context bound.
- Conditional task: Native and IPython each verified 2/3. Median provider
  calls were 4 vs 3; input tokens 3,436 vs 2,141; model-visible result
  characters 884 vs 435; maximum request characters 3,016 vs 2,531.
- Aggregation task: Native verified 2/3 and IPython 3/3. Median provider calls
  were 6 vs 4; input/output tokens 6,449/420 vs 3,445/344; wall time
  11.002 s vs 8.034 s; result characters 2,756 vs 1,226; maximum request
  characters 3,831 vs 3,085.
- Both arms encountered the then-existing single-call adapter's
  `model_protocol:multiple_tool_calls` boundary; the old result artifact
  retained failure codes but not raw call arrays. IPython had zero Python
  runtime failures. All tested terminal kernels were closed. Full per-run
  evidence and metric definitions are in `IPYTHON_AB_RESULT.md`.
- The promotion is bounded to this MVP control plane. It does not authorize
  sandbox claims, durable namespace, multiple kernels, automatic code replay,
  Child, RLM, recursion, or a benchmark framework.

Bounded sequential sibling slice:

- **Failure audit:** the historical A/B artifact names three
  `multiple_tool_calls` failures (conditional Native run 3, conditional
  IPython run 1, aggregation Native run 2) but did not persist their raw
  arrays. Nine fresh unchanged baseline reproductions captured one exact
  `ORDINARY_SIBLINGS` response: three `read` calls for `data-1.txt`,
  `data-2.txt`, and `data-3.txt`, with distinct ids
  `call_00_JpALCfWFEQRyKrhg969p0457`,
  `call_01_lTOsmlUFnAsRIbeqPXdW5180`, and
  `call_02_pqVNAG2nJho71DUYny353445`. It contained no control action and
  failed only at the old adapter boundary.
- **Mechanism:** one response now carries 1..4 ordinary siblings; complete
  preflight precedes effects; controls remain single-only; execution and
  result commit are strictly sequential; runtime Tool failure does not erase
  later siblings; one frame preserves all actions, ids, raw response, and
  order. A replacement Runtime resumes only the never-started ToolCall suffix
  after a settled or confirmed-Write prefix; it does not replay that prefix.
  Offline A-G tests cover ordinary reads, atomic invalid-third rejection,
  success/failure/success, count bound, duplicate ids, both mixed-control
  cases, shared-kernel sequential IPython, minimum-bound projection, and both
  settled-prefix and reconciled-Write restart.
- **Real DeepSeek:** six fresh executions reused the exact historical
  aggregation prompt/fixture with no prompt change. All six were
  completion-verified. Five emitted only single calls. One emitted two
  ordinary `shell` siblings, `ls -la` and `cat data-*.txt`, with ids
  `call_00_k8YrWDw3xGga52bnPLlm6868` and
  `call_01_a0oHzhrnGccB3rfb25O04193`; started and settled order matched
  provider order, the next decision was accepted, and completion verified.
- **Result: PASS.** The observed real blocker crossed the new path. This does
  not claim a success-rate improvement and does not authorize parallel tools,
  mixed controls, a scheduler, transaction/rollback, Child, or recursion.
  Exact evidence is in `MULTI_TOOL_RESULT.md`.

Slice 6:

- **Local provider configuration:** the repository's existing
  `core.env_loader.load_env_file()` loaded the ignored `.env.local` without a
  new configuration seam. A secret-safe smoke confirmed a configured
  `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL=deepseek-v4-pro`, and
  `DEEPSEEK_BASE_URL=https://api.deepseek.com`; thinking and streaming remain
  fixed in code as disabled/false. `.env.local` remains ignored and untracked.
- **A - adapter protocol:** 21 deterministic tests pass across native
  read/write/shell/wait/claim mapping, invalid name/JSON/arguments, zero and
  multiple call rejection, provider failure, exact request settings, and
  environment-only credential policy. This includes a test-only evidence
  extractor regression that distinguishes a legal assistant response with no
  `tool_calls` as `mechanism_absent` from a malformed assistant message.
  Rejected decisions execute zero Tools.
- **B - native call/result continuity:** `call_123` is preserved in the typed
  decision, durable frame/Observation, next assistant tool call, and matching
  `role=tool.tool_call_id`. The second request contains a native tool-result
  message rather than prose pretending to be one.
- **C - real canonical task:** **PASS, 3/3 verified.** Three fresh
  `deepseek-v4-pro` executions, all with thinking/streaming disabled, each made
  3 provider requests / 3 model decisions / 2 Tool calls and produced exact
  `output.txt == "ALPHA"` followed by verified completion. Run 1 used 2,430
  input / 145 output / 2,575 total tokens in 5.026 s; run 2 used 2,445 / 157 /
  2,602 in 4.201 s; run 3 used 2,411 / 132 / 2,543 in 4.170 s. Aggregate:
  9 requests, 9 decisions, 6 Tool calls, 7,286 input / 434 output / 7,720
  total tokens, and 13.397 s measured model/Runtime wall time.
- **C - real call-id continuity:** **PASS.** The first new canonical run exposed
  an `APPARATUS` defect: the test treated EventLog's frozen tuple snapshot as
  a mutable list. The task itself reached a native call, but no equality result
  was accepted. After a test-only `_plain()` projection fix, one fresh
  canonical evidence run completed and programmatically proved both Tool
  chains. For `read`, provider id
  `call_00_861ZQVWYNYRAvrUfs8y40455`; for `write`, provider id
  `call_00_QmOWiXG3eEaJTuIp01su5753`. In each chain:
  provider assistant id == `DecisionFrame.provider_tool_call_id` == structured
  `Observation.provider_tool_call_id` == the next assistant call id ==
  `role=tool.tool_call_id`. Both causal sequences were
  `MODEL_DECISION -> TOOL_CALL_STARTED -> TOOL_RESULT -> MODEL_DECISION`.
  Adapter private transcript state remains **NONE**.
- **D - evidence extractor repair:** the previous autonomous experiment could
  index a user-only continuation as though it were an assistant tool call and
  raise `KeyError("tool_calls")`. The test-only extractor now returns
  `mechanism_absent` for a legal assistant message without calls,
  `malformed` for an invalid assistant shape, and `observed` only for one
  non-empty call id. It never fabricates a call; its regression passes.
- **E - mechanically exercised real failed ToolResult continuation:** **PASS.**
  Test apparatus seeded the valid assistant call
  `call_test_failure_continuation: read(candidate.txt)`; the existing
  `DeepSeekModel` parser produced `ToolCall(ReadRequest("candidate.txt"))`;
  the real `ToolHost` produced structured `not_found`; the next wire request
  paired the same id in the assistant call and `role=tool.tool_call_id`.
  `deepseek-v4-pro` accepted that request without HTTP/protocol error and
  returned one schema-valid native call, parsed through the existing adapter as
  a `ReadRequest`. The single real continuation used 905 input / 60 output /
  965 total tokens. The experiment stopped at the two-decision protocol bound;
  completion was intentionally not exercised.
- **F - autonomous failure-first behavior:** the earlier three autonomous
  attempts remain `MODEL_BEHAVIOR / MECHANISM_NOT_EXERCISED` and were not
  rerun. The mechanical experiment does **not** claim DeepSeek autonomously
  selected the initial failure-first strategy.
- **G - persistence:** a deterministic DeepSeek wire fixture performs WAIT,
  destroys Runtime/model state, reloads the durable EventLog, wakes, and sends
  the original native call id on the next request. Completion succeeds with
  unchanged execution/root identity and full-fold equivalence. Adapter private
  persistent state: **NONE**. A separate committed-Write crash fixture proves
  reconciliation preserves the original provider call id in its Observation
  and next native tool-result continuation; a mismatched durable result id is
  rejected.
- A separate large-result experiment proves canonical output remains complete
  in the durable event while the native `role=tool` content obeys the absolute
  model-context bound and excludes the hidden tail.

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
- Final bounded-sibling validation reports: `Execution_lab2` 88 passed / 6
  real-provider experiments skipped; root 328 passed / 24 skipped;
  Conversation Memory 163 passed / 45 skipped; Dream 36 passed / 1 skipped.
  The six Execution skips are explicitly gated real DeepSeek experiments;
  normal regression runs do not load local credentials or make provider calls.

## Known limits

- UNKNOWN side-effect crash reconciliation is implemented only for a dangling
  `WriteRequest` whose target can be read and exactly equals intended content.
  All ambiguous Writes and every other effect kind remain unresolved.
- Completion verification supports only caller-declared
  `FileContentEquals(path, expected_content)`. There is no natural-language
  goal judgment, Reviewer Agent, LLM verifier, registry, composite predicate,
  test runner, or second verifier type.
- Persistent IPython control plane: **IMPLEMENTED AND PROMOTED** for the
  bounded one-kernel-per-live-Root surface above.
- DeepSeek native provider adapter: **IMPLEMENTED AND VALIDATED** for this
  fixed non-thinking, non-streaming surface with one call or at most four
  strictly sequential ordinary siblings. Six fresh unchanged aggregation runs
  completed 6/6; one real two-shell sibling response crossed the new path with
  exact ids and verified completion. Existing canonical call-id and seeded
  ToolHost-failure continuation evidence remains valid.
- Child/recursion: **NOT IMPLEMENTED**.
- Restart is supported at a durable settled result, WAIT/external-event safe
  point, initial start, terminal event, or the exact confirmed-Write case
  above. Other unsettled tool calls remain unsupported/unresolved.
- For a partially dispatched ordinary ToolCall sibling batch, restart
  preserves the settled/reconciled prefix and executes only its never-started
  suffix in original order. This is frozen-decision continuation, not retry,
  scheduling, rollback, or transaction management. Partial IPython sibling
  recovery remains unsupported because its live namespace is not durable.
- JSONL and Checkpoint are local single-process files. There is no multi-writer
  coordination, database, checkpoint rotation, scheduler, background worker,
  generic event bus, generic verifier framework, or resource accounting.
- Context is bounded in Python characters rather than provider tokens or UTF-8
  bytes. Shell isolation remains cwd-based rather than an OS sandbox.
- Fresh typed-binding-only Python Code Mode remains **NOT IMPLEMENTED** because
  its stricter isolation boundary is unavailable. Persistent IPython is
  implemented under the later explicit equal-local-authority decision and is
  deliberately not described as a sandbox.
- IPython namespace is live-process-only. It is discarded on Runtime
  replacement, and an interrupted in-flight IPython execution remains
  unresolved rather than being replayed.
- Checkpoint recovery hashes the prefix and folds only the tail, but no
  replay-performance benchmark has been measured.

Further Execution stages require a separate approved task.
