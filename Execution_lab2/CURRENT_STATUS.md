# Execution V2 — Frozen Status

Date: 2026-08-28
Branch: `Execution_lab2`
Starting HEAD for the final audit: `f08e73840241e4037e8015456fe6762ce60a345f`
Status: **EXECUTION V2 SUBSTRATE FROZEN**

Execution V2's frozen implementation is now supported from the production
`Execution/` package. This directory remains the historical evidence and
regression suite; its three former implementation modules are compatibility
aliases to production, so there is no second active copy. Execution remains
unconnected to production Chat, Mind, Memory, or Dream.

## Frozen definition

```text
Persistent Actor
+ Event-sourced Runtime
+ Shared Environment
+ Persistent IPython
+ IPython-native recursive AgentProcess
```

The smallest formal provider-facing surface is:

| Actor | Provider-facing functions |
|---|---|
| Root | `ipython`, `wait`, `claim_complete` |
| Child | `ipython`, `wait`, `return` |

When current depth and capacity permit delegation, the Actor's persistent
IPython namespace additionally contains:

```python
await spawn_child(goal)
```

`read`, `write`, `shell`, and `spawn_child` are not normal Root
provider-facing functions. Native Read/Write/Shell and native Spawn parsing
remain internal/historical compatibility paths for ToolHost, recovery, and
deterministic tests. **Available is not the same as model-visible.**

## Authority map

| Object | Frozen authority |
|---|---|
| `EventLog` | Append-only historical truth for Runtime and Actor lifecycle facts |
| `ExecutionState` | Pure derived view: `fold(EventLog)` |
| `Checkpoint` | Validated EventLog-prefix snapshot and tail-replay optimization; never an independent authority |
| `Context` | Bounded model-visible projection rebuilt from current state and relevant observations |
| `DecisionFrame` | Frozen evidence of the actual request, capabilities, provider request/response, call ids, and selected action at sampling time |
| IPython namespace | Per-Actor live working state; intentionally non-durable across process restart |
| `SharedEnvironment` | Current external filesystem/process reality |
| Provider context | Decision evidence held in DecisionFrame; not an independent transcript or state store |

There is no dual state authority. EventLog does not claim to contain every
Python side effect, and the workspace does not replace Runtime history.

## Frozen mechanisms

### Core Runtime

- Persistent execution and Actor identities survive EventLog reload.
- EventLog validates schema, sequence, causal references, and lifecycle
  transitions before accepting loaded or appended facts.
- State, Trace, Context, and DecisionFrame remain distinct.
- Checkpoint recovery validates the durable prefix and folds only the tail;
  full replay remains authoritative.
- `WAIT -> EXTERNAL_EVENT_RECEIVED -> ROOT_WOKEN -> resume` is durable.
- Explicit interrupt stops future admission, settles or interrupts in-flight
  work, appends `ACTOR_SUSPENDED`, and requires explicit resume.
- Completed actions are not replayed. A frozen ordinary sibling decision may
  resume only its never-started suffix.
- A dangling Write may be reconciled only when current file content exactly
  proves the requested postcondition; otherwise it remains unresolved.
  Dangling Shell and arbitrary active IPython execution remain unsupported.
- Root can only claim completion. Runtime verifies the caller-owned immutable
  `FileContentEquals` against the current workspace before appending
  `EXECUTION_COMPLETED`.
- Runtime validates and dispatches typed actions; it does not interpret the
  natural-language goal or perform semantic planning.

### Persistent IPython

- One lazy kernel per live Actor, with a persistent namespace across that
  Actor's decisions.
- Kernel cwd is the shared workspace. Python has direct `os`, `pathlib`,
  filesystem, and `subprocess` authority under the worker OS identity.
- Startup readiness and code execution have separate bounds; code and output
  are bounded; Python failures and timeouts are explicit observations.
- Live interrupt uses the kernel interrupt primitive and waits for an observed
  failure/timeout settlement.
- Terminal close shuts down channels and kernel resources. Restart creates a
  fresh namespace while retaining durable Actor facts and workspace reality.
- This is a trusted local execution plane, not a security sandbox.

### Child AgentProcess

Every Child uses the same `AgentProcess` loop and owns:

- a distinct `actor_id` and `execution_id`;
- durable direct-parent lineage and depth;
- an independent EventLog, derived State, bounded Context, DecisionFrames, and
  IPython namespace;
- access to the same SharedEnvironment;
- `Return(local_result)` instead of Root completion authority.

Return/failure travels only to the direct parent as a bounded identified
observation. Runtime does not bubble a grandchild result directly to Root.

Current reachable bounds must be stated exactly:

- `RootAgentProcess` has no Child admission.
- `AgentProcess(max_depth=1)` admits at most three direct Root children,
  sequentially; its children cannot Spawn.
- `AgentProcess(max_depth=2)` admits one Child per eligible Actor and at most
  three Actors total: Root -> Child -> Grandchild.
- The exact one-Child depth-one experiment used a narrow subclass override;
  there is no public `max_children_per_actor` constructor parameter today.

The single-Child path, bounded sibling path, and depth-two substrate are
mechanically validated. They are not evidence that a model will choose or
benefit from those topologies.

## Evidence tiers

### VALIDATED — mechanisms

- Persistent Root and event-sourced Runtime
- bounded Context and exact DecisionFrame fidelity
- checkpoint + tail replay
- WAIT/event/resume and explicit interrupt/suspend/resume
- completed-action non-replay
- minimal Write UNKNOWN reconciliation
- deterministic completion verification
- persistent per-Actor IPython
- Prime-like provider surface
- independent Child identity/lineage/Context/State/EventLog/IPython
- SharedEnvironment and direct-parent Return
- single Child, bounded direct siblings, and bounded depth-two substrate
- IPython `spawn_child(goal)` -> Jupyter comm -> existing Host admission ->
  existing Child lifecycle

### VALIDATED — recorded real-model behavior

Recorded, frozen DeepSeek-V4-Pro evidence establishes only that:

- the fixed adapter can execute real local tasks and preserve native call
  identity;
- persistent IPython can complete the tested local filesystem tasks;
- a prompted single-Child chain completed 3/3;
- one prompted two-sibling chain completed and integrated two Returns;
- the Prime-like surface beat the Wide surface on the frozen three-pair A/B
  sample (2/3 versus 0/3 verified completion).

These are bounded observations, not general policy or superiority claims.
Canonical details remain in the corresponding `RESULT.md` and `ARTIFACT.json`
files.

### NOT VALIDATED / UNVERIFIABLE

- DeepSeek autonomous delegation policy: **NOT VALIDATED**; the final eligible
  B arm invoked `spawn_child` 0/3.
- Recursive topology emergence: **NOT VALIDATED**; depth two occurred 0/3.
- Child utility gain: **NOT EVALUATED** because the utility B runs committed no
  Child.
- Multi-agent or recursive superiority: **NOT VALIDATED**.
- Prime Agent basic-execution parity: **UNVERIFIABLE**, not failed; the
  historical EXP-025 Prime runner lacks verifiable commit/path/content
  identity.

## Differences from the architecture documents

The MVP architecture's durable Runtime invariants are implemented and
validated. Later work intentionally changed three design details:

1. **IPython authority.** The original diagrams route every Python effect
   through a typed capability and ToolHost. The frozen implementation instead
   gives IPython and ToolHost equal trusted local OS/workspace authority.
   Runtime records the outer IPython lifecycle and later authoritative
   environment evidence; it does not intercept every internal Python side
   effect.
2. **Capability surface.** Provider-visible Read/Write/Shell and native
   SpawnChild were replaced by the minimal programmable surface. Delegation is
   an IPython callable backed by a narrow Host comm.
3. **Module shape.** The conceptual ExecutionRuntime lifecycle remains
   co-located in `RootAgentProcess` rather than split into a separate module.

The final architecture's Activity, Actor Directory, Blackboard, Capability
Search/Registry, parallel Actors, resource economy, Mind integration,
Self-Cognition, and Evolution remain future-only.

## Known limits and revisit gates

- A crash during an arbitrary active IPython cell is explicit unsupported
  recovery; Python continuation and namespace are not restored.
- Write equality is current-postcondition evidence, not causal proof,
  exactly-once execution, or generic side-effect reconciliation.
- Completion verification supports only `FileContentEquals`.
- Jupyter comm invocation identity is not a separate durable idempotency key;
  the durable facts are the outer IPython call and newly admitted Child
  identities. Multiple invocations in one cell share the outer provider call
  id. Admission bounds cap effects but do not deduplicate them, so no generic
  exactly-once bridge claim is made.
- Child execution is Host-driven and sequential. There is no automatic Child
  crash recovery, scheduler, parallel join, global resource policy, or
  shared-world conflict protocol.
- Context is bounded in characters, not provider tokens.
- The local workspace/process authority is sufficient for this lab but is not
  a sandbox or a browser/API/device environment.
- Default sibling capacity and the one-Child experimental configuration do not
  share a public configuration interface. Revisit that interface only when a
  production integration task needs it.

## Frozen evidence and repository policy

Canonical result documents and JSON artifacts are retained. Deterministic
regression tests are retained. The final cleanup removed:

- the topology-only pre-admission evidence callback from the production
  adapter after its artifact was frozen;
- eight opt-in real-provider experiment runners and their fixture/metric
  helpers;
- generated Python cache files.

The four-call admission rule and all Runtime/provider outcomes are unchanged.
No real provider request was sent by the final audit.

Source provenance and adaptation details remain in `SOURCE_AUDIT.md`. The
complete final classification is in `EXECUTION_V2_FINAL_AUDIT.md`.

## Final validation

- Execution deterministic suite: **147 passed**.
- Full repository suite: **328 passed, 24 skipped**, with two unchanged pinned
  MAGMA warnings.
- `git diff --check`: **PASS**; line-ending notices only.
- Live `ipykernel`: **0**.
- Credential-pattern files under `Execution_lab2`: **0**.
- Generated `__pycache__` / `*.pyc`: **0 / 0** after cleanup.
- Pinned MAGMA status/diff: **clean / clean**.
- Real provider requests made by the final audit: **0**.
- Final `/code-review`: **Spec PASS / Standards PASS**, no findings.
- Final verdict: **EXECUTION_V2_READY_TO_FREEZE**.

## Freeze rule

No more Execution behavior, surface, benchmark, delegation-prompt, or topology
experiments are authorized by this status. Any future change requires a
separate approved task. The next research question, outside this freeze, is
whether Child/recursive AgentProcess topology can produce a real capability
gain; the current substrate does not answer it.
