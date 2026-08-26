# Execution V2 MVP Final Evidence Audit

## 1. Audit scope

The original read-only evidence audit of `Execution_lab2` was performed at:

```text
branch: Execution_lab2
HEAD:   690501a0ebd262ed5c8130bd52a0e8fe0968603c
date:   2026-08-27
```

Condition 5 and the affected final verdict were re-audited after the one
authorized final lifecycle slice:

```text
branch:              Execution_lab2
implementation HEAD: 39121e1
slice commits:        76b1e29, 39121e1
date:                 2026-08-27
```

All other success-condition evidence and historical findings below remain
unchanged.

The normative design and implementation records examined were:

- `docs/LUMINA_EXECUTION_MVP_ARCHITECTURE.md`;
- `Execution_lab2/CURRENT_STATUS.md`;
- `Execution_lab2/SOURCE_AUDIT.md`;
- the three production modules and four test modules in `Execution_lab2`;
- the Execution V2 commit series from `f0beeed` through `690501a`;
- the existing deterministic and recorded real-provider experiments.

No Runtime, ToolHost, IPython, provider adapter, test, benchmark, prompt, or
fixture was changed. No new live-provider experiment was run. The audit used
the required verdict vocabulary only:

```text
SUPPORTED
SUPPORTED_WITH_LIMITS
NOT_ESTABLISHED
REFUTED
OUT_OF_SCOPE
```

The final verdict is based on the frozen architecture, not on test existence
alone. In particular, WAIT/resume and crash/restart evidence was not counted as
evidence for explicit operator interrupt/suspend/resume.

## 2. Current architecture

The implemented flow is:

```text
caller-owned Goal + FileContentEquals
    -> RootAgentProcess
       -> bounded ModelRequest
       -> DeepSeekModel or ScriptedModel
       -> typed Action / bounded sequential Action tuple
       -> mechanical dispatch
          -> ToolHost -> SharedEnvironment filesystem/shell
          -> PersistentIPython -> same workspace, live kernel namespace
       -> append-only durable EventLog
       -> fold_execution_state(EventLog) -> ExecutionState
       -> bounded Context + exact DecisionFrame
       -> completion verification against current SharedEnvironment

checkpoint = validated EventLog-prefix snapshot + tail replay optimization
```

The frozen conceptual formula is substantially present:

```text
Persistent Root Actor
+ Programmable IPython
+ Event-sourced Runtime
+ Shared Environment
```

There are two structural qualifications. First, the frozen document names a
separate first-class `ExecutionRuntime`, while the implementation places its
mechanical lifecycle inside `RootAgentProcess`. Second, the implemented class
is named `PersistentIPython`, not `IPythonControlPlane`. These are module-shape
differences, not evidence that the underlying mechanisms are absent. The first
does, however, concentrate too many reasons to change in one class.

The native provider path is sufficiently established for the fixed MVP
surface, with limits. Offline contract tests cover DeepSeek request/response
shape, call identity, failure continuation, restart reconstruction, bounds,
and sequential sibling calls. Recorded real-provider evidence includes the
canonical task completing 3/3, live call-id continuity, real acceptance of a
mechanically seeded failed ToolResult continuation, the persistent-IPython A/B
experiment, and the post-Slice-7 6/6 matrix including a real response with two
sibling Shell calls. These are fixed-provider compatibility results, not a
generic provider framework claim; environment-gated real tests remain skipped
in ordinary regression runs.

## 3. Evidence matrix

| # | Mechanism | Verdict | Strongest evidence | Tests / experiment / commit | Known limitation | MVP blocked? |
|---|---|---|---|---|---|---|
| 1 | Persistent Root selects and settles multiple real ToolHost/IPython actions, then claims completion | `SUPPORTED` | A real DeepSeek Root performed read -> write -> verified completion 3/3; bounded sibling calls now settle in model order | `test_root_completes_multistep_filesystem_task`; real canonical 3/3; real multi-tool 6/6; `f0beeed`, `b529795`, `690501a` | Only the fixed local workspace and fixed DeepSeek surface are established | NO |
| 2 | Each top-level Action returns a structured Observation based on Host execution | `SUPPORTED_WITH_LIMITS` | Read/Write/Shell and IPython result/failure events feed the next bounded request; failed actions remain visible and the Root continues | `test_tool_failure_is_returned_to_the_model_for_recovery`; `test_ipython_failure_is_bounded_observation_and_root_can_continue`; sibling-failure tests; `f0beeed`, `6534a`, `690501a` | Effects performed *inside* Python are not separately typed or EventLogged; the outer IPython result and later environment evidence are authoritative | NO |
| 3 | EventLog is append-only historical authority and ExecutionState is its fold | `SUPPORTED_WITH_LIMITS` | Full replay equality, causal-reference validation, durable append-before-authority, corruption rejection, and checkpoint-prefix validation are tested | `test_execution_state_is_an_exact_replay_of_an_append_only_event_log`; persistence replay/corruption/checkpoint tests; `e386cdd`, `71cbf35` | EventLog reconstructs Runtime facts, not ephemeral kernel variables or every internal Python side effect | NO |
| 4 | State and Context are projections rather than an unbounded transcript | `SUPPORTED` | State is fold-derived; Context is rebuilt under an absolute character bound; four successful or failed siblings fit the minimum bound | `test_state_derived_context_does_not_grow_with_the_full_event_history`; large-result/minimum-bound/four-sibling tests; `e386cdd`, `7cdbd6f`, `690501a` | Canonical results can be larger than their model-visible bounded projections by design | NO |
| 5 | Explicit interrupt stops future work, settles/cancels in-flight work, enters SUSPENDED, and explicitly resumes | `SUPPORTED` | Host-owned interrupt is serialized with every model/action admission boundary; ordinary Tool work truthfully settles, live IPython uses its existing interrupt primitive then records actual failure/timeout, and only a causal SUSPENDED event permits explicit resume | Six required lifecycle experiments plus admission/crash-window regressions; `76b1e29`, `39121e1` | Kernel interrupt is best-effort and may settle through the existing timeout; process-crash recovery of a dangling IPython cell remains explicitly unsupported rather than fabricated | NO |
| 6 | Checkpoint + EventLog + Environment restore settled execution after process loss | `SUPPORTED_WITH_LIMITS` | Checkpoint-tail replay equals full replay; identity survives; completed actions are not replayed; WAIT wake and committed Write recovery survive full Runtime replacement | checkpoint/restart/wake/non-replay/reconciliation tests; `71cbf35`, `9a1a72f`, `25ac10d`, `5957369` | Dangling Shell and process-crashed IPython remain explicit unresolved/unsupported cases; no generic crash-consistency claim is made | NO |
| 7 | Recovery does not require a Python instruction pointer | `SUPPORTED` | Restart reconstructs durable execution while deliberately starting a fresh IPython namespace | `test_restart_preserves_execution_but_starts_a_fresh_namespace`; `6534a00` | Live kernel variables are intentionally not durable | NO |
| 8 | An uncertain side effect can remain UNKNOWN and undergo minimal reconciliation | `SUPPORTED_WITH_LIMITS` | A dangling Write is confirmed only when current logical file content exactly equals the intended content; a causal `ACTION_RECONCILED` event is appended and the Write is not repeated | committed/ambiguous Write and unsupported Shell restart tests; `25ac10d` | Equality proves the current postcondition, not causal authorship, exactly-once execution, or generic side-effect recovery | NO |
| 9 | Completion authority is separated from the Root's claim and checked against reality | `SUPPORTED` | Caller-owned immutable `FileContentEquals` is read from the current filesystem; claimed -> verified/rejected -> completed events are causal and replayable | false/true completion, authority, restart, and event-replay tests; `5957369` | Only one deterministic verifier type exists, as required by the MVP slice | NO |
| 10 | DecisionFrame reconstructs the model's actual sampling conditions | `SUPPORTED` | The persisted frame freezes the exact bounded request, exposed tools, raw result, provider wire request/response, selected Action(s), call IDs, and source refs | request/capability fidelity, mutable-response snapshot, call-ID, native restart, and sibling tests; `7cdbd6f`, `b529795`, `690501a` | It is request evidence, not a second provider transcript or world-state authority | NO |
| 11 | Runtime performs mechanical lifecycle work, not semantic planning | `SUPPORTED` | Model output chooses tool, arguments, Python code, wait, or completion claim; Runtime only validates, preflights, dispatches, folds, reconciles the one Write rule, and evaluates the typed completion predicate | Action-path tests and code inspection at `690501a` | Fixed mechanical policies such as a four-call cap and sequential settling remain local adaptations | NO |

## 4. MVP success-condition audit

### 4.1 Root can complete a basic multi-step real-tool task

**Mechanism:** one persistent Root makes bounded decisions, ToolHost executes
typed requests in a shared workspace, results return as structured
observations, and completion is independently verified.

**Verdict:** `SUPPORTED`.

**Strongest evidence:** this is not based only on the scripted unit test. The
recorded fixed-provider experiment completed the canonical read/write task in
three fresh runs, retaining native call identity through Host results. Slice 7
then resolved the observed multi-tool compatibility failure with bounded,
strictly sequential sibling settlement and recorded a 6/6 result matrix.

**Known limitation:** this establishes the local MVP task family and fixed
DeepSeek path, not browser/API/device execution or provider generality.

**MVP completion blocked:** NO.

### 4.2 Every action receives real Environment feedback

**Mechanism:** the Host executes each top-level typed action and appends a
success or failure observation before the next decision. Canonical result data
is distinct from its bounded model projection.

**Verdict:** `SUPPORTED_WITH_LIMITS`.

**Strongest evidence:** missing files, failed siblings, Shell process results,
IPython exceptions/timeouts, and successful workspace effects all become
explicit observations and are visible to the next model request.

**Known limitation:** a top-level IPython action can perform several direct OS
effects. Runtime observes the outer execution result and current workspace; it
does not manufacture a typed event for each internal Python operation.

**MVP completion blocked:** NO under the later, explicit persistent-IPython
authority used by the implementation.

### 4.3 EventLog can rebuild the execution's main facts

**Mechanism:** durable immutable events with contiguous sequence and causal
references fold into `ExecutionState`.

**Verdict:** `SUPPORTED_WITH_LIMITS`.

**Strongest evidence:** full durable reload and fold equal live state;
malformed logs and bad causal shapes fail explicitly; a durable append failure
does not become an in-memory authoritative fact.

**Known limitation:** “main facts” means Runtime lifecycle facts. External
filesystem reality and ephemeral Python namespace contents are not duplicated
into the log.

**MVP completion blocked:** NO.

### 4.4 State and Context do not depend on an infinite transcript

**Mechanism:** State is a fold; Context is a separately bounded projection;
DecisionFrame captures the actual request.

**Verdict:** `SUPPORTED`.

**Strongest evidence:** tests extend history while keeping Context within the
absolute bound, including maximum-width sibling success and failure cases.

**Known limitation:** bounded projections intentionally omit/truncate detail
that remains in canonical events/results.

**MVP completion blocked:** NO.

### 4.5 Normal pause/resume includes explicit interrupt/suspend/resume

**Mechanism required by the frozen design:**

```text
ACTIVE
-> explicit interrupt
-> stop future actions
-> settle or safely cancel in-flight work
-> checkpoint
-> SUSPENDED
-> explicit resume
-> continue
```

**Mechanism:** external `RootAgentProcess.interrupt()` appends
`INTERRUPT_REQUESTED` and establishes one lifecycle admission gate shared by
model sampling, Tool/IPython start, control decisions, and terminal failure.
An ordinary in-flight Tool records its actual outcome before suspension. A
live IPython phase receives the existing kernel interrupt request and still
must produce an actual failure or timeout outcome. Only then does Runtime
append `ACTOR_SUSPENDED`; only explicit `resume()` appends
`ACTOR_RESUMED`.

**Verdict:** `SUPPORTED`.

**Strongest evidence:** maintained experiments cover interrupt between
decisions, durable suspended restart with stable execution/root identity,
ordinary Event delivery that cannot wake the Root, completed-Tool no replay,
in-flight ordinary Tool settlement, live IPython interrupt/actual-outcome
settlement, frozen sibling suffix continuation, model-admission atomicity,
pre-model interrupt atomicity, and crash after a durable interrupt request.
Every restored State equals `fold(full EventLog)`; the final
`Execution_lab2` suite reports 96 passed and 6 environment-gated real-provider
tests skipped. Implementation commits are `76b1e29` and `39121e1`.

**Known limitation:** this is one local Root lifecycle, not a generic
cancellation framework. Jupyter interruption is best-effort and the maintained
Windows path may settle through the existing bounded timeout. A process crash
leaving only `IPYTHON_EXECUTION_STARTED` remains explicit unsupported
recovery; no success or cancellation is fabricated.

**MVP completion blocked:** NO.

### 4.6 Crash/restart recovery uses Checkpoint + EventLog + Environment

**Mechanism:** verify a checkpoint against its EventLog prefix, fold the tail,
inspect the Environment only for the supported dangling Write case, and never
replay a durably settled action.

**Verdict:** `SUPPORTED_WITH_LIMITS`.

**Strongest evidence:** checkpoint-tail restoration equals full replay; Root
and execution identities survive; restart after rejection or completion has
the correct model/tool call count; a committed Write is reconciled without a
second execution.

**Known limitation:** dangling Shell and in-flight IPython are explicit
unresolved/unsupported recovery outcomes. No generic crash-consistency claim
is justified.

**MVP completion blocked:** NO.

### 4.7 Python instruction-pointer restoration is unnecessary

**Mechanism:** restart restores durable Runtime facts and creates a fresh
kernel rather than serializing a Python frame.

**Verdict:** `SUPPORTED`.

**Strongest evidence:** the restart test preserves execution identity and
workspace truth but shows that a prior Python variable is absent.

**Known limitation:** the design deliberately abandons live namespace state on
restart.

**MVP completion blocked:** NO.

### 4.8 UNKNOWN side effects can be reconciled without guessing

**Mechanism:** `TOOL_CALL_STARTED(Write)` contains call identity, path,
content, and deterministic fingerprint. On restart, exact current content can
produce a new causal `ACTION_RECONCILED(confirmed_applied)` event; otherwise
recovery stops unresolved.

**Verdict:** `SUPPORTED_WITH_LIMITS`.

**Strongest evidence:** the crash-window test writes once, destroys Runtime,
reloads, confirms present reality, and completes with execution count one. The
ambiguous and dangling-Shell tests prove no automatic replay or fabricated
success.

**Known limitation:** a matching file is postcondition evidence only. Another
actor could have produced it. This is not exactly-once, causal proof, generic
reconciliation, rollback, or an idempotency framework.

**MVP completion blocked:** NO; the frozen minimum is satisfied by the one
demonstrated Write reconciliation rule and truthful unsupported outcomes.

### 4.9 Completion is verified against reality

**Mechanism:** caller supplies immutable `FileContentEquals` at execution
start. The Root can only `ClaimComplete`; Runtime reads the current file and
appends claimed, then verified/rejected, then completed when appropriate.

**Verdict:** `SUPPORTED`.

**Strongest evidence:** false completion returns a rejection observation and
lets the Root fix the file; true completion does not cause an extra model call;
both rejected and completed histories survive restart.

**Known limitation:** exactly one typed predicate is supported.

**MVP completion blocked:** NO.

### 4.10 DecisionFrame rebuilds actual model-visible conditions

**Mechanism:** each `MODEL_DECISION` stores the exact bounded request and its
source refs together with frozen output, Action(s), available contracts, and
native provider evidence.

**Verdict:** `SUPPORTED`.

**Strongest evidence:** tests compare the actual request and available tools,
mutate a provider response after sampling to prove snapshotting, and rebuild a
native continuation after restart while preserving each provider call id.

**Known limitation:** DecisionFrame is evidence of sampling conditions, not an
independent conversation or state store.

**MVP completion blocked:** NO.

### 4.11 Runtime does not perform semantic planning

**Mechanism:** semantic choices stay in Model output. Runtime implements only
typed validation, limits, dispatch ordering, durable transitions, the single
Write postcondition check, and the single typed completion check.

**Verdict:** `SUPPORTED`.

**Strongest evidence:** code inspection finds no goal interpretation, natural
language completion judgment, tool selection, recovery planner, Reviewer, or
hidden semantic retry policy.

**Known limitation:** `RootAgentProcess` contains many mechanical concerns in
one class, which is a maintainability issue rather than semantic planning.

**MVP completion blocked:** NO.

## 5. Core invariant audit

### Persistent Root Actor — `SUPPORTED_WITH_LIMITS`

Normal Runtime creation always appends stable non-empty `execution_id` and
`root_actor_id`; restart reloads them before another model call, and tests prove
identity continuity through WAIT, reconciliation, rejection, and completion.

Limit: the in-memory `EventLog` compatibility schema permits a manually
constructed `EXECUTION_STARTED` event without either identity, and the fold
then produces nullable identities. `RootAgentProcess.run()` does not use this
path, but the schema is weaker than the Persistent Root invariant and is
reported as debt.

### Programmable IPython — `SUPPORTED_WITH_LIMITS`

One live Root lazily owns one kernel. Its namespace persists across live model
decisions, starts in the shared workspace, reports exceptions/timeouts and
bounded output, rejects oversized code before launch, exposes shutdown failure,
and is closed at terminal completion. Restart keeps durable execution/world
facts but intentionally creates a fresh namespace.

Limits: the kernel is not a security sandbox; Python has worker OS authority;
internal Python effects are not individually EventLogged; in-flight IPython
crash recovery is unsupported. The frozen architecture's earlier typed-binding
language is stale relative to the later explicitly authorized persistent-
IPython experiment, which granted direct local OS/workspace authority.

### Event-sourced Runtime — `SUPPORTED_WITH_LIMITS`

The implemented historical/state/context authority split is sound. Explicit
interrupt, settled/cancelled outcome evidence, SUSPENDED, and explicit resume
are append-only events whose folded State survives restart; Checkpoint remains
only a validated replay optimization. The conceptual Runtime is still merged
into `RootAgentProcess`, rather than being the named first-class module in the
architecture document, so the structural limit remains.

### Shared Environment — `SUPPORTED`

`SharedEnvironment` resolves an existing workspace and gives ToolHost, Shell,
completion verification, Write reconciliation, and IPython the same filesystem
reality. Path escape is rejected for typed file tools; Shell receives argv,
uses `shell=False`, and runs with workspace cwd. This is sufficient for the
MVP. Future browser/API/device environments are not required evidence.

### Multi-tool compatibility — `SUPPORTED_WITH_LIMITS`

Bounded sequential multi-tool is correctly treated as a provider compatibility
mechanism, not a new action ontology. The full array is preflighted before any
effect, controls remain single-only, each sibling has its own identity and
settled causal chain, failures do not erase later siblings, and recovery only
continues a frozen unstarted suffix. The absence of parallel dispatch is not an
MVP gap.

### Truth-authority separation

| Object | Role | Authority conclusion |
|---|---|---|
| EventLog | Immutable historical Runtime facts | Sole durable historical authority |
| ExecutionState | Pure fold of EventLog | Derived view; not independently persisted as truth |
| Checkpoint | Digest-validated prefix snapshot | Replay optimization; corruption/staleness cannot override EventLog |
| Context | Bounded model-visible projection | Sampling input only; rebuilt from durable facts |
| DecisionFrame | Exact evidence of actual sampling conditions | Historical request/response evidence; not world truth |
| Provider continuation | Reconstruction from DecisionFrames and bounded observations | No private adapter transcript or second session authority |
| IPython namespace | Ephemeral live working state | Not durable truth; discarded on restart |
| Shared Environment | Current external reality | Authoritative for actual files/process outcomes; not duplicated into State |

No pair currently forms an intended double authority. The material caveat is
that direct Python effects may change Shared Environment without individual
Runtime events; later environment inspection/verification remains the reality
authority for those effects.

## 6. Source-faithfulness audit

`SOURCE_AUDIT.md` identifies direct source facts, inferences, borrowed
semantics, Lumina adaptations, and deliberately un-copied systems. The record
is source-faithful for the core mechanisms audited here.

| Source | Pinned source / symbol | Borrowed mechanism | Lumina adaptation | Not copied |
|---|---|---|---|---|
| OpenAI Codex | `bde9db1375667c50dcc0c2b52532a4e2672571c2`; `ToolCallRuntime`, call-id/output correlation; earlier thread lifecycle sources | Host dispatch retains concrete call identity; durable identity is separate from a live process | One execution/root identity; call ids persisted through started/result observations and provider continuation | Codex scheduler, parallel locks/router, approvals, sandbox/session framework, Responses protocol |
| DeepSeek Harness | `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`; `SessionEvent`, `sourceEventSeqs`, `interruptedTurnClosers`, `executeToolCalls` | Canonical append-only event truth, derived model surface, explicit UNKNOWN distinction, planned distinct ordered calls | Durable-first local append; pure State fold; exact Write reality check; sequential sibling settlement and suffix recovery | Session/plugin ecosystem, synthetic generic repair, parallel scheduler, CodeRuntime framework |
| Prime Agent | `514633727bf26d74f39f3119c2b0e31a5ceb2a9d`; `AgentTool.execute`, `AgentSession`, `KernelManager`, IPython tool | Host owns external capability and lifecycle; one live agent session can own persistent Python working state | One live Root owns one lazy kernel while EventLog/completion remain Host authority; restart starts a fresh namespace | RLM, Child/recursive agents, daemon/session hierarchy, provider stack, kernel revival/snapshot |
| Temporal | Server `19a774302c613da9adc4436ab14278ccdca8e0a5`, Go SDK `b7c242c6894df088a57a85b33d0586e908da8b93`, docs `6f46de944c41b1823331536a65356548b94578c7` | Durable event history rebuilds state; settled Activity results are not replayed; external effects are not exactly once | WAIT is durable before quiescence; wake is an appended fact; completed actions do not replay; UNKNOWN is explicit | Workflow-code replay, task queues, workers, automatic Activity retry, nondeterminism machinery |
| LongHorizon-Harness | `a1dd930614972b92361c1b9cd6aac441a6db5a65`; `ManagedRound`, `AuditReport`, `run_role_manager`, resume path | Resume from durable records and current workspace; claimed completion requires independent evidence | Checkpoint + tail reconstruction; one deterministic caller-owned filesystem predicate | Manager/Executor/Auditor ontology, supervisor/control bus, LLM reviewer, dashboard |
| DeepSeek native API | Official live Chat Completions and Tool Calls docs re-audited 2026-08-27; `tool_calls[]`, `tool_call.id`, `role=tool.tool_call_id` | Native ordered call array and exact result correlation | Thin fixed-model adapter; bounded projections; durable reconstruction; max four sequential siblings | SDK transcript, provider registry, streaming, retry, parallelism, compatible-provider abstraction |

### Borrowed mechanism versus local engineering

The core boundaries are borrowed and adapted rather than re-derived as a novel
agent architecture: host-owned effects, durable canonical events, fold-derived
state, persisted call identity, recovery from facts plus present reality,
completion authority separation, and a host-owned persistent kernel all have
named upstream precedents.

The following are genuine Lumina-specific engineering choices, not upstream
algorithms and not misattributed as such:

- the exact small event vocabulary and causal validation rules;
- durable append-and-fsync before an event becomes authoritative;
- the local checkpoint encoding and EventLog-prefix digest validation;
- the 768-character minimum Context bound and associated projections;
- exact logical file-content equality as the sole Write reconciliation rule;
- `FileContentEquals` as the sole deterministic completion predicate;
- max-four, full-batch preflight, controls-single-only, strictly sequential
  sibling execution and frozen-suffix recovery;
- fresh IPython namespace after Runtime restart.

These are ordinary, narrow adaptations rather than a new recovery or planning
algorithm. No generic reconciler, verifier, scheduler, transaction manager,
idempotency framework, or effect ontology was introduced.

## 7. Known limitations

- Live interrupt/suspend/resume is intentionally local and cooperative:
  ordinary Tool work settles, and IPython uses the existing best-effort kernel
  interrupt then waits for actual failure/timeout evidence. This is not
  process-tree cancellation or generic effect safety.
- A dangling Shell and an in-flight IPython action cannot be reconciled; both
  fail explicitly rather than retrying or guessing.
- Write reconciliation proves only a current exact postcondition.
- The IPython kernel is trusted worker-local execution, not a sandbox; its live
  namespace and internal side effects are not durable Runtime state.
- Only `FileContentEquals` completion verification exists.
- The DeepSeek adapter is a fixed-provider surface; ordinary tests skip the
  environment-gated real calls.
- Typed file tools enforce workspace containment, but Shell/Python have the
  worker's local OS permissions.
- The frozen architecture text still describes typed Runtime capability
  re-entry from IPython, while the later authorized experiment implemented
  direct workspace/OS authority. Current status documents the later decision,
  but the design document is not internally current on this boundary.
- An in-memory EventLog compatibility path permits missing persistent Root
  identities even though normal Runtime creation always supplies them.

## 8. MVP blockers

No MVP blocker remains. The former explicit-interrupt gap is now established
by Host-owned API, three causal lifecycle events, fold-derived SUSPENDED State,
live Tool/IPython settlement evidence, suspended restart, ordinary-event
no-wake, and explicit-resume/no-replay tests. WAIT and crash recovery remain
distinct semantics rather than substitute evidence.

## 9. Non-blocking deferred capabilities

The following remain outside this MVP and are not blockers: Child/Spawn,
recursion, parallel tools, Blackboard, Capability Search, generic resources,
Mind/Memory integration, Self-Cognition/Evolution, browser/API/device
environments, generic side-effect reconciliation, exactly-once external
effects, generic completion verifiers, Python namespace snapshot/revival, and a
security-sandboxed code runtime.

Implementation debt observed without modification:

- `execution.py` is 2,552 physical lines; `RootAgentProcess` is approximately
  432 lines and owns model sampling, dispatch, recovery, verification,
  checkpoint use, WAIT/wake, and kernel lifecycle. The interface is small, but
  the class has many reasons to change and no separate first-class
  `ExecutionRuntime` seam.
- Native-provider continuation knowledge is spread through `execution.py`
  DTOs, validation, fold, Context reconstruction, and dispatch. There is no
  DeepSeek import or literal in Runtime, but the continuation boundary is not
  wholly confined to the adapter.
- `EventLog._validate_append`, event freezing, and
  `fold_execution_state` repeat large event/action switches. A new event
  requires coordinated edits in several cascades.
- `ScriptedModel` lives in production code but has only test consumers.
- The identity-less in-memory start-event compatibility path weakens schema
  consistency.

No Manager, Registry, Factory, unused effect hierarchy, production experiment
harness, or hidden parallel scheduler was found.

Current inventory:

```text
production Python: 3 files / 3,227 physical LOC
test Python:       4 files / 3,663 physical LOC
lab artifacts:   11 files
direct Execution dependencies: httpx, jupyter_client, ipykernel
test-only dependency: pytest
```

## 10. Final verdict

```text
MVP_VALIDATED
```

All eleven success conditions are supported or supported with explicitly
bounded limits. Condition 5 is now `SUPPORTED`; no blocker remains. The
direct-IPython authority design delta, effect-recovery limits, and listed
implementation debts remain visible and do not weaken the frozen MVP claims
beyond their recorded scope.

## 11. Recommended next action

Freeze the Execution MVP at the validated boundary. Do not enter Child,
recursion, parallelism, generic cancellation, or another Execution stage
without a separate approved research task.

## Validation record

Run from the repository root with the existing project virtual environment on
2026-08-27:

```text
python -m pytest Execution_lab2 -q
96 passed, 6 skipped

python -m pytest -q
328 passed, 24 skipped, 2 upstream warnings

python -m pytest Conversation_Memory/tests -q
163 passed, 45 skipped, 7 upstream warnings

python -m pytest Dream/tests -q
36 passed, 1 skipped, 2 upstream warnings

git diff --check
PASS

live ipykernel / secret scan / upstream MAGMA status
0 / clean / clean
```

The skipped tests are the existing environment-gated real-provider tests; no
fresh external model run was needed for this evidence audit.
