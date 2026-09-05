# Lumina Execution V2 Final Audit & Freeze

## 1. Audit scope

This final source-level audit covers the current `Execution_lab2` worktree
against:

- `docs/LUMINA_EXECUTION_MVP_ARCHITECTURE.md`;
- `docs/LUMINA_EXECUTION_FINAL_ARCHITECTURE.md`;
- `Execution_lab2/CURRENT_STATUS.md`;
- `Execution_lab2/SOURCE_AUDIT.md`;
- the three implementation modules, eight deterministic test modules, and
  retained canonical experiment evidence.

The fixed review point is:

```text
branch: Execution_lab2
HEAD:   f08e73840241e4037e8015456fe6762ce60a345f
date:   2026-08-28
```

The audit inspected the existing source, tests, EventLog/DecisionFrame evidence,
and frozen result/artifact files. It sent **zero real model requests**, designed
no benchmark, and made no capability, prompt, budget, topology, provider, or
Runtime semantic change.

## 2. Final verdict

# EXECUTION_V2_READY_TO_FREEZE

The next-stage formal design baseline is:

```text
Persistent Actor
+ Event-sourced Runtime
+ Shared Environment
+ Persistent IPython
+ IPython-native recursive AgentProcess
```

The verdict freezes a bounded execution substrate. It does not validate an
autonomous delegation policy, Child utility, recursive topology emergence,
multi-agent superiority, or Prime Agent parity.

## 3. Frozen architecture

```text
caller-owned Goal + FileContentEquals
                   |
             AgentProcess
                   |
        bounded ModelRequest
                   |
 DeepSeek V4 Pro, thinking disabled
                   |
       Root: IPython | Wait | ClaimComplete
      Child: IPython | Wait | Return
                   |
   optional await spawn_child(goal) in IPython
                   |
         one narrow Jupyter comm
                   |
        existing Host admission
                   |
        existing ChildRef/lifecycle
                   |
  per-Actor EventLog -> fold -> ExecutionState
                   |
        bounded Context + DecisionFrame
                   |
     one shared local workspace reality
```

The module interfaces are deep enough to freeze without another framework:

- `DeepSeekModel.decide(ModelRequest)` owns provider translation and protocol
  validation;
- `RootAgentProcess` / `AgentProcess` owns the existing mechanical lifecycle;
- `PersistentIPython.execute(code)` owns bounded kernel lifecycle and outer
  execution observation;
- `ToolHost.execute(request)` owns typed native workspace actions;
- `EventLog` and `fold_execution_state` own history and derived state.

The conceptual ExecutionRuntime remains implemented inside
`RootAgentProcess`. This is a maintainability limit, not a second state
authority or evidence that semantic planning entered Runtime.

## 4. Final classification matrix

### 4.1 Core Runtime

| Mechanism | Classification | Strongest evidence | Frozen limit |
|---|---|---|---|
| Persistent Root identity | **KEEP** | execution/root ids persist in `EXECUTION_STARTED` and survive WAIT, completion, reconciliation, suspension, and restart tests | Isolated lab Actor, not production Chat identity |
| Append-only EventLog | **KEEP** | durable append-before-authority, load/schema/causal validation, corruption rejection | Local single-process JSONL |
| `ExecutionState = Fold(EventLog)` | **KEEP** | live, restored, and full-replay State equality across core and Child tests | State contains Runtime facts, not every world/Python fact |
| Checkpoint + tail replay | **KEEP** | prefix digest validation, stale-tail fold, full-replay fallback | Optimization only; no independent authority |
| DecisionFrame fidelity | **KEEP** | exact request, surface, provider wire request/response, call ids, action, and refs are frozen and replayed | Evidence of sampling conditions, not hidden reasoning |
| Bounded Context | **KEEP** | absolute character-bound tests cover long results and four sibling outcomes | Character bound, not token/byte bound |
| UNKNOWN Write reconciliation | **KEEP** | confirmed file equality appends causal `ACTION_RECONCILED` without replay | Write-only postcondition evidence; no exactly-once claim |
| Completion verification | **KEEP** | claimed/rejected/verified/completed causal chain against current filesystem | Only `FileContentEquals` |
| Explicit interrupt/suspend/resume | **KEEP** | admission race, in-flight Tool, live IPython interrupt, restart, and explicit resume tests | Best-effort kernel interrupt; arbitrary crashed cell unresolved |
| WAIT/event/resume | **KEEP** | durable exact event match, irrelevant event, wake suffix, restart tests | Exact event type only |
| Completed-action non-replay | **KEEP** | settled/reconciled prefixes do not repeat; ordinary frozen suffix resumes in order | Partial IPython sibling continuation unsupported |
| Runtime semantic planning | **DROP / NOT PRESENT** | source contains mechanical validation/dispatch only; no goal parser, planner, reviewer, or recovery planner | Keep absent |

### 4.2 Persistent IPython

| Mechanism | Classification | Strongest evidence | Frozen limit |
|---|---|---|---|
| Persistent per-Actor kernel | **KEEP** | namespace persists across live decisions; Root/Child/siblings use distinct kernels | Namespace is live-process-only |
| Direct filesystem/process authority | **KEEP** | deterministic `pathlib`, aggregation, write, and `subprocess` regression | Trusted worker OS authority, not a sandbox |
| Startup/execution timeout split | **KEEP** | delayed readiness and code-timeout tests; startup failures report `kernel_startup_error` | Fixed local bounds, no generic timeout framework |
| Failure/timeout observation | **KEEP** | Python exception, bounded output, timeout, and Runtime event tests | Runtime records the outer cell, not each internal effect |
| Interrupt and settlement | **KEEP** | active kernel interrupt settles to observed failure/timeout before suspension | No hard guarantee that interrupt is immediate |
| Kernel cleanup | **KEEP** | close/terminal/failure tests and zero-live-kernel checks | Host process must call close or reach terminal cleanup |
| Namespace isolation | **KEEP** | Root/Child/sibling sentinel tests | Workspace remains shared by design |
| Restart boundary | **KEEP** | durable execution survives while namespace is fresh | Active arbitrary Python continuation is not recovered |

### 4.3 Model-facing surface

| Surface item | Classification | Current fact |
|---|---|---|
| Root `ipython` / `wait` / `claim_complete` | **KEEP** | Exact DecisionFrame and provider request regression |
| Child `ipython` / `wait` / `return` | **KEEP** | Exact role/depth provider request regression |
| `spawn_child(goal)` inside eligible Actor IPython | **KEEP** | Truthful bounded Context declaration plus callable/Host bridge tests |
| Provider-facing Read / Write / Shell | **DROP / EXPERIMENT-ONLY** | Not in normal Root or Child provider request |
| Provider-facing SpawnChild | **DROP / EXPERIMENT-ONLY** | Not in normal provider request; delegation is inside IPython |
| `tool_mode`, `shell_visible`, `native_tools_visible`, surface A/B flags | **DROP** | Absent from formal implementation path |
| Native ToolHost and parser compatibility | **KEEP — INTERNAL** | Needed by Runtime/reconciliation/deterministic historical regressions; not model-visible by default |
| Four-call sequential admission | **KEEP WITH LIMIT** | Mechanical protocol/experimental bound, not a provider requirement or safety theorem |

### 4.4 Child AgentProcess

| Mechanism | Classification | Strongest evidence | Frozen limit |
|---|---|---|---|
| Independent actor/execution identity and parent lineage | **KEEP** | ChildRef/start facts, restart conflict rejection, depth tests | No Actor Directory |
| Independent Context/EventLog/State/DecisionFrame | **KEEP** | sentinel isolation and full-fold equality for every Actor | Shared workspace is intentionally visible |
| Independent IPython namespace | **KEEP** | Root/Child/sibling live namespace tests | Non-durable after restart |
| Shared Environment | **KEEP** | descendant writes are observed by parent/Root | No conflict detection/locking |
| Direct-parent Return/failure | **KEEP** | identified `CHILD_RETURNED` / `CHILD_FAILED` routing and replay tests | No direct messaging or automatic bubbling |
| Single-Child path | **KEEP — MECHANISM** | deterministic full chain and recorded prompted 3/3 DeepSeek chain | Exact one-Child depth-one cap currently uses a narrow subclass |
| Up to three direct siblings | **KEEP — BOUNDED SUBSTRATE** | deterministic admission/restart/isolation and one recorded prompted two-sibling chain | Sequential only; default `AgentProcess(max_depth=1)` path |
| Depth-two recursion | **KEEP — MECHANISM** | 0->1->2 lineage, return chain, isolation, failure, restart, total-bound tests | One Child per eligible Actor, three Actors total |
| Autonomous recursive topology | **NOT VALIDATED** | real depth-two attempt produced 0/3 recursive chains | Do not infer model policy from deterministic tests |
| Child utility | **NOT VALIDATED / NOT EVALUATED** | both utility campaigns committed zero B-arm Child | No capability-gain claim |
| Parallelism, scheduler, join, resource economy | **DEFER** | Not implemented and not required to freeze | Requires a separate failure-driven task |

## 5. Evidence hierarchy

### 5.1 VALIDATED — mechanisms

The following are supported by source plus maintained deterministic tests:

- Persistent Actor identity and durable event-sourced lifecycle
- State/Trace/Context separation and exact DecisionFrame
- checkpoint + tail replay
- WAIT/event/resume
- explicit interrupt/suspend/resume
- completed-action non-replay
- minimal Write reconciliation
- typed completion verification
- persistent per-Actor IPython
- Prime-like provider surface
- independent Child process facts and direct-parent Return
- bounded direct siblings
- bounded depth-two recursive substrate
- IPython-native Child bridge

A deterministic mechanism result is not promoted to autonomous model behavior.

### 5.2 VALIDATED — recorded real-model behavior

Frozen artifacts establish these bounded observations:

| Evidence | Result | Allowed claim |
|---|---|---|
| Canonical DeepSeek tool execution | 3/3 verified | Fixed adapter can complete the tested local task |
| Sequential multi-tool recovery | 6/6 verified; one two-Shell sibling response crossed the path | The concrete provider compatibility failure was resolved |
| Persistent IPython A/B | IPython 5/6 versus Native 4/6 over two three-run task groups | Persistent IPython was viable for the MVP control plane |
| Prompted single Child | 3/3 complete Root->Child->Return->Root chains | DeepSeek can use the mechanism when the task explicitly requires it |
| Prompted bounded siblings | first allowed run completed with two Children | DeepSeek emitted and integrated one homogeneous sibling batch |
| Wide vs Prime-like surface | 0/3 versus 2/3 verified, equal complete evidence | The smaller surface was better on that frozen sample |
| Final IPython Child utility gate | B invoked/committed Child 0/3 | Autonomous delegation remained unobserved |

These observations do not establish general performance, policy, topology
utility, or superiority.

### 5.3 Unsupported claims

| Claim | Status | Why |
|---|---|---|
| DeepSeek autonomous delegation policy | **NOT VALIDATED** | Final eligible B arm selected no Spawn in 3/3 |
| Recursive topology emergence | **NOT VALIDATED** | Depth two occurred 0/3 |
| Child capability gain | **NOT EVALUATED** | No utility B run actually used a Child |
| Multi-agent superiority | **NOT VALIDATED** | Mechanism demonstrations are not comparative utility evidence |
| Prime Agent parity | **UNVERIFIABLE** | EXP-025 runner lacks verifiable commit/path/content identity |
| Exactly-once side effects | **NOT VALIDATED / NOT CLAIMED** | Durable history cannot causally prove external authorship |
| Secure Python sandbox | **NOT IMPLEMENTED / NOT CLAIMED** | IPython intentionally has worker OS authority |

## 6. Core invariant and authority audit

The frozen truth flow is:

```text
EventLog (Runtime history)
    -> fold
ExecutionState (derived current view)
    -> bounded projection
Context (actual model-visible input)
    -> frozen with response
DecisionFrame (sampling evidence)

IPython namespace (ephemeral working state)
SharedEnvironment (external reality)
```

No pair forms a double authority:

- **EventLog vs Checkpoint:** Checkpoint is accepted only after prefix digest
  validation and can be discarded for full replay.
- **EventLog vs provider context:** provider request/response exists as immutable
  DecisionFrame evidence; adapter has no private canonical transcript.
- **EventLog vs IPython:** EventLog records outer execution and Actor lifecycle;
  the namespace is non-durable working state.
- **EventLog vs Environment:** EventLog says what Runtime committed; current
  filesystem reality remains authoritative for verification/reconciliation.
- **Parent vs Child logs:** each Actor owns its own canonical log. Parent
  receives only a causal, validated outcome projection.

## 7. IPython-native Child bridge review

The actual path is:

```text
await spawn_child(goal)
-> lumina.spawn_child Jupyter comm
-> PersistentIPython._handle_spawn_child
-> RootAgentProcess._spawn_child_from_ipython
-> existing depth/capacity/durable-log admission
-> existing _append_child_spawn
-> ChildRef + CHILD_SPAWNED
-> existing AgentProcess.for_child / run_child
-> existing accept_child
-> CHILD_RETURNED or CHILD_FAILED to direct parent
```

No second Actor manager, RPC framework, Child loop, EventLog, State reducer, or
Return channel was introduced.

| Boundary | Audit result |
|---|---|
| Call identity | Outer provider IPython call id is retained on the IPython event and ChildRef; every admitted Child receives new durable execution/actor ids. Multiple Spawn invocations in one cell share that outer call id |
| Duplicate handling | Host rechecks current event, depth, capacity, and durable-log availability. Bounds cap effects but do not deduplicate comm delivery; there is no separate durable comm-invocation idempotency key or exactly-once claim |
| Kernel shutdown | Existing PersistentIPython close path stops channels, shuts down kernel, and cleans resources |
| Interrupt | Existing active-IPython interrupt and settlement semantics apply |
| Host admission failure | Bounded error is returned through the comm and becomes an explicit IPython execution failure |
| Child failure | Existing `CHILD_FAILED` reaches only the direct parent |
| Return routing | Existing `accept_child` validates Child durable history/identity before direct-parent delivery |
| Restart | Settled Child facts replay normally; `CHILD_SPAWNED` before enclosing cell settlement is explicitly rejected as unsupported recovery |

The frozen crash rule is correct and intentionally narrow:

> Never fabricate or resume unknown post-`await` Python continuation after a
> process crash.

## 8. Differences from the original architectures

| Architecture area | Implemented/validated | Changed since original design | Future-only |
|---|---|---|---|
| MVP Persistent Root | Yes | Runtime remains co-located in `RootAgentProcess` | Production Chat/Mind integration |
| Event-sourced Runtime | Yes | More Child lifecycle facts were added append-only | Distributed/multi-writer persistence |
| IPython | Yes | Direct workspace/OS authority replaces mandatory typed-capability mediation | Sandbox or arbitrary continuation recovery |
| Shared Environment | Yes | Root/Child/Grandchild share one local workspace | Browser/API/device world |
| Completion authority | Yes | Still only `FileContentEquals` | Composite/test/LLM verification |
| Capability surface | Minimal Prime-like surface validated | Native Read/Write/Shell/Spawn removed from normal provider visibility | Registry/Search |
| Recursive AgentProcess | Bounded mechanism validated | Delegation entered IPython through a narrow Host bridge | General depth/branching/parallelism |
| Activity / Actor Directory / Blackboard | No | None | Entirely future-only |
| Mind fast/slow, Self-Cognition, Evolution | No | None | Entirely future-only |

The final architecture remains a direction document. This audit freezes only
the implemented subset and explicitly rejects reading future sections as
current behavior.

## 9. Source faithfulness

`SOURCE_AUDIT.md` records exact commits/symbols/licenses. The frozen
implementation follows these borrowed semantics without claiming every
ordinary engineering choice is upstream-derived:

- **Codex:** Host-owned action lifecycle, exact provider call identity, and
  actual-request evidence.
- **DSH:** canonical durable event truth, ordered provider call arrays, durable
  delegation depth/lineage, and explicit interrupted-history handling.
- **Prime Agent:** persistent local IPython, Host-owned kernel lifecycle,
  independent Child session/identity, and Python-to-Host Child admission.
- **Temporal:** durable history does not make external side effects exactly
  once.
- **LongHorizon-Harness:** completion requires current environment evidence,
  not only an Agent claim.
- **DeepSeek official protocol:** ordered tool calls, matching result ids,
  fixed V4-Pro non-thinking request shape.
- **Jupyter client:** start/channels/readiness before execution timeout, IOPub
  settlement, interrupt, and shutdown.

Lumina adaptations are the bounded append-only event schema, one concrete Write
reconciliation rule, `FileContentEquals`, character-bounded Context, sequential
four-call admission, local topology caps, and the narrow Jupyter comm bridge.
The comm protocol, event vocabulary, and exact local caps are Lumina-specific
engineering adaptations, not copied generic algorithms.

No substantively new recovery, scheduling, planning, topology-selection, or
resource-allocation algorithm was found.

## 10. KEEP / REVISIT / DEFER

| Decision | Items |
|---|---|
| **KEEP** | strict bounded AgentProcess; append-only EventLog; State != Context != Trace; exact DecisionFrame; persistent Actor; WAIT/event/resume; explicit interrupt/suspend/resume; Write UNKNOWN reconciliation; completion verification; persistent IPython; minimal provider surface; SharedEnvironment; Child identity/isolation; IPython spawn bridge; direct-parent Return; bounded sibling/depth-two substrate |
| **REVISIT** | active-cell crash boundary; durable comm invocation identity/duplicate semantics; autonomous delegation policy; Child utility; public topology-cap configuration; global execution resource policy; multi-Actor shared-world conflicts; long-horizon Context policy |
| **DEFER** | Blackboard; Capability Search/Registry; parallel Actors; scheduler/join; resource economy; general recursion/branching; Activity/Actor Directory; Mind integration; Self-Cognition; Evolution |
| **DROP / EXPERIMENT-ONLY** | normal provider-facing Read/Write/Shell/SpawnChild; surface A/B switches; topology evidence callback; opt-in real-model experiment runners; benchmark fixture/metric helpers |

`REVISIT` is not authorization to implement. Each item requires a separate
approved, failure-driven task.

## 11. Known limitations

- Local JSONL/Checkpoint persistence is single-process and single-writer.
- Only one external-effect reconciliation and one completion predicate exist.
- Python is trusted and can bypass typed ToolHost event granularity.
- Active arbitrary Python cannot be resumed after process death.
- Child scheduling is explicit and sequential; Child crash recovery is manual.
- Shared-world write conflicts have no coordination policy.
- Four tool calls and character-bounded Context are local fixed policies, not
  universal protocol limits.
- The default direct-sibling capacity is three; exact single-Child depth-one
  capacity is not a constructor option.
- The bridge has no independent durable invocation/idempotency identity.
- No production Chat/Mind integration is present.

None blocks freezing the isolated, bounded substrate because every limitation
is explicit, produces no fabricated authority, and lies outside the validated
claim.

## 12. Repository cleanup

The final audit removed only ended experiment apparatus:

- `DeepSeekModel.cardinality_evidence_sink`, its redacted-shape extractor, and
  three extractor-only tests;
- eight opt-in `RUN_DEEPSEEK_REAL_TESTS` runners plus their local
  fixture/metric helpers;
- generated `__pycache__`/`*.pyc` output after validation.

Retained:

- three production-candidate modules;
- eight deterministic test modules;
- 147 deterministic Execution tests;
- canonical result Markdown and seven complete JSON artifacts;
- `SOURCE_AUDIT.md` and historical evidence conclusions;
- native ToolHost/reconciliation implementations and test-only native adapter.

The production adapter no longer contains a research telemetry callback. The
four-call rejection remains exactly where it was and still executes zero calls.
No generated workspace, smoke helper, surface switch, benchmark runner, or
temporary output remains under `Execution_lab2`.

Current footprint after cleanup:

```text
production Python files: 3
production Python LOC:   4,831
test Python files:       8
test Python LOC:         5,345
canonical JSON artifacts: 7
canonical RESULT.md files: 12
```

## 13. Validation

The final validation pass used the repository virtual environment. The system
Conda interpreter does not contain pytest.

| Check | Result |
|---|---|
| `.venv\\Scripts\\python.exe -m pytest Execution_lab2 -q` | **147 passed** |
| `.venv\\Scripts\\python.exe -m pytest -q` | **328 passed, 24 skipped, 2 unchanged upstream warnings** |
| `git diff --check` | **PASS**; line-ending notices only |
| live Python processes with `ipykernel` in the command line | **0** |
| credential-pattern files under `Execution_lab2` | **0** |
| `__pycache__` directories / `*.pyc` files after cleanup | **0 / 0** |
| pinned MAGMA status / diff | **clean / clean** |
| real provider requests made by this audit | **0** |
| `/code-review` Spec axis | **PASS — no findings** |
| `/code-review` Standards axis | **PASS — no findings** |

The two warnings come from the pinned upstream MAGMA code and are unrelated to
Execution. No provider test gate remains in `Execution_lab2`. Pre-existing
Canvas changes, the deleted historical docs file, and other user worktree
changes were not touched by this audit.

## 14. Recommended frozen baseline

Freeze exactly this substrate:

```text
Root:
  IPython + Wait + ClaimComplete
  + await spawn_child(goal) only when current admission permits

Child:
  IPython + Wait + Return
  + await spawn_child(goal) only below the depth bound

Authority:
  EventLog -> ExecutionState
  Checkpoint = replay optimization
  Context/DecisionFrame = bounded sampling evidence
  IPython = ephemeral per-Actor working state
  SharedEnvironment = external reality

Topology:
  sequential, local, bounded
  default depth 1 / up to 3 direct Root children
  optional depth 2 / one Child per Actor / 3 Actors total
```

Stop Execution behavior and surface experiments here. Any later production
integration or Child-utility research must start from a separate approved task
without weakening these authority and replay invariants.
