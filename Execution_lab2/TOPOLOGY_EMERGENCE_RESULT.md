# Flat-vs-Nested Topology Emergence Result

## Verdict

**NOT VALIDATED**

The evidence-preserving replication ran exactly six fresh real
DeepSeek-V4-Pro executions: Flat 3 and Nested 3. The EventLog classifier
mechanically labeled all six `LINEAR` because no Child was committed. Flat
verified completion was **3/3**. Nested depth two and verified completion were
both **0/3**; every Nested run exhausted the frozen eight-decision bound.

No response in this batch exceeded four tool calls, so the new redacted
pre-admission evidence arrays are empty. The original batch's three
cardinality-truncated Nested responses remain historical and unrecoverable;
this replication does not retroactively classify them. No prompt, fixture,
Runtime behavior, capability, model setting, Context limit, Child/depth bound,
or tool-call limit was changed, and no additional run was sent.

## Hypothesis

With Agent policy, Runtime, tools, recursion capability, model configuration,
and execution bounds held fixed, replacing direct Flat evidence with two-level
Nested manifest-to-leaf dependencies might cause the model's own local policy
to select a different execution topology.

This experiment tests topology emergence only. It does not test whether a
depth-two substrate is more useful than a depth-one substrate.

## Frozen substrate

| Variable | Frozen value |
|---|---|
| Model | DeepSeek-V4-Pro |
| Thinking | disabled |
| Tool mode | native |
| Root goal | `Read task.txt and complete its instructions. Child processes are available when useful.` |
| Completion | caller-owned `FileContentEquals("answer.txt", expected_sum)` |
| Agent loop | current `AgentProcess` for every Actor |
| Depth | `max_depth=2` |
| Capacity | existing recursive-mode bound: one direct Child per Actor, at most three Actors |
| Scheduling | existing sequential Host drive |
| Decision limit | 8 for every Actor |
| Context bound | existing default 2,000 characters |
| Workspace | new temporary workspace for every run |
| Runtime/model object | new objects for every run and Actor |
| IPython | unchanged native route; no `PersistentIPython` or kernel was instantiated |

Spawn semantics, depth, Child limits, Context construction, Tool surface,
IPython, provider prompt policy, scheduler, and resource policy were not
modified. The only executable addition is one test-only fixture builder,
sequential experiment driver, EventLog measurement function, and mechanical
classifier in `test_topology_emergence.py`.

## Flat and Nested fixtures

Each fixture contains exactly six source files and 1,200 UTF-8 source bytes.
Each pair performs the same operation: read two integers, add them, and write
only the sum to `answer.txt`. Literal names and values differ across pairs.

| Pair | Flat direct files | Nested manifests -> leaves | Values | Expected |
|---:|---|---|---:|---:|
| 1 | `orchid.dat`, `cedar.dat` | `amber.module` -> `orchid.leaf`; `cobalt.module` -> `cedar.leaf` | 17 + 25 | 42 |
| 2 | `delta.dat`, `echo.dat` | `lunar.module` -> `delta.leaf`; `solar.module` -> `echo.leaf` | 31 + 14 | 45 |
| 3 | `birch.dat`, `maple.dat` | `north.module` -> `birch.leaf`; `south.module` -> `maple.leaf` | 9 + 58 | 67 |

Flat `task.txt` directly names the two evidence files. Nested `task.txt` names
two manifests; each manifest names one leaf and field. No task text contains
`delegate`, `spawn`, `recursion`, `recursive`, or `child`. Padding lives only
in inert noise files.

## Preregistered criteria

Topology is derived mechanically from canonical EventLogs:

- `LINEAR`: maximum depth 0.
- `SIBLING_BRANCHING`: maximum depth 1 and at least one direct Root Child.
- `DEPTH2_RECURSIVE`: maximum depth 2.

The allowed result rules were frozen before the first provider call:

- `TASK_CONDITIONED_RECURSION_SUPPORTED` only if Nested depth two is at least
  2/3 and Flat depth two is at most 1/3.
- `AUTONOMOUS_RECURSION_OBSERVED` if Nested depth two is at least 1/3 but the
  paired difference threshold is not met.
- `NOT VALIDATED` if Nested depth two is 0/3.
- `BLOCKED` only if running the experiment requires a Runtime change.

## Per-run topology

`Returns` and `Child failures` count direct outcome deliveries in Parent logs.
A Child's own terminal `CHILD_RETURNED` fact is excluded to avoid counting one
return twice. All edge lists were empty.

| Run | Topology | Root Spawn | Actors (d0/d1/d2) | Max depth | Edges | Returns | Child failures | Verified | Output match | Terminal status |
|---|---|---:|---|---:|---|---:|---:|---|---|---|
| Flat-1 | LINEAR | 0 | 1 (1/0/0) | 0 | `[]` | 0 | 0 | yes | yes | completed |
| Nested-1 | LINEAR | 0 | 1 (1/0/0) | 0 | `[]` | 0 | 0 | no | no | failed: `decision_limit_reached` |
| Flat-2 | LINEAR | 0 | 1 (1/0/0) | 0 | `[]` | 0 | 0 | yes | yes | completed |
| Nested-2 | LINEAR | 0 | 1 (1/0/0) | 0 | `[]` | 0 | 0 | no | no | failed: `decision_limit_reached` |
| Flat-3 | LINEAR | 0 | 1 (1/0/0) | 0 | `[]` | 0 | 0 | yes | yes | completed |
| Nested-3 | LINEAR | 0 | 1 (1/0/0) | 0 | `[]` | 0 | 0 | no | no | failed: `decision_limit_reached` |

Mechanical EventLog topology rates:

| Shape | LINEAR | SIBLING_BRANCHING | DEPTH2_RECURSIVE | Verified |
|---|---:|---:|---:|---:|
| Flat | 3/3 | 0/3 | 0/3 | 3/3 |
| Nested | 3/3 | 0/3 | 0/3 | 0/3 |

## Metrics

Token counts are provider-reported usage. Model-visible request characters are
the sum and maximum of the bounded `ModelRequest.context` strings recorded in
durable DecisionFrames; they are not JSON wire-body sizes. Duplicate reads are
repeat `ReadRequest` observations of the same path by the same Actor. Because
no Child was created, every per-Actor value belongs to `depth0-1`.

| Run | Model calls | Input tokens | Output tokens | Wall s | Tool / IPython actions | Request chars sum / max | Duplicate reads |
|---|---:|---:|---:|---:|---:|---:|---:|
| Flat-1 | 7 | 5,844 | 406 | 9.842 | 8 / 0 | 4,873 / 955 | 4 |
| Nested-1 | 8 | 7,249 | 769 | 14.562 | 15 / 0 | 6,445 / 1,816 | 2 |
| Flat-2 | 5 | 4,268 | 313 | 7.658 | 5 / 0 | 3,678 / 952 | 1 |
| Nested-2 | 8 | 7,675 | 552 | 13.175 | 11 / 0 | 6,854 / 1,612 | 1 |
| Flat-3 | 4 | 3,230 | 212 | 5.523 | 4 / 0 | 2,727 / 952 | 0 |
| Nested-3 | 8 | 7,430 | 487 | 11.436 | 10 / 0 | 6,729 / 1,613 | 2 |

The shape totals are Flat: 16 model calls, 13,342 input tokens, 931 output
tokens, 17 Tool actions, 23.023 seconds; Nested: 24 model calls, 22,354 input
tokens, 1,808 output tokens, 36 Tool actions, 39.173 seconds. These aggregates
are descriptive only: Nested runs reached the decision limit, so they are not
a controlled efficiency comparison.

## Observed failure modes

- The optional research sink emits only count, ordered tool names, call-id
  presence/uniqueness, JSON-object/schema flags and ordinary/control/unknown
  classification before the unchanged cardinality rejection. It does not
  write arguments, file/user content, credentials, headers or provider bodies
  into EventLog.
- All six `pre_admission_call_shapes` arrays are empty: no response in this
  replication crossed the unchanged four-call limit.
- Every Flat run completed and verified. Every Nested run remained at Root for
  eight admitted decisions and ended with `decision_limit_reached`.
- The absence of Child events is unambiguous: all six EventLog-derived Actor
  trees contain only depth zero, with no parent/Child edge or result event.
- The sanitized maintained record is
  `TOPOLOGY_EMERGENCE_ARTIFACT.json`. No Runtime semantic change was
  required, so the experiment is not `BLOCKED`.

## Claims allowed and not allowed

Allowed:

- Under these three frozen task pairs, Flat completed at depth zero in 3/3
  fresh runs.
- Nested committed no Child across eight admitted decisions per run and
  reached the decision limit in 3/3 fresh runs.
- Autonomous depth-two recursion remains **NOT VALIDATED**.
- This replication contains no cardinality rejection and therefore provides
  no new sample for `LEGAL_ORDINARY_OVER_CAP`, homogeneous Spawn, mixed
  control or malformed-response classification.

Not allowed:

- Recursion is useless, harmful, or unavailable.
- DeepSeek never autonomously delegates.
- Nested tasks generally reduce success, cost more, or require heuristics.
- AgentSpawn-style scoring, stronger prompting, specialist roles, deeper
  recursion, or a scheduler are justified by this result.
- A depth-two-versus-depth-one utility comparison has been performed.

The evidence-preserving replication is final for this task. No utility A/B,
prompt repair, heuristic Spawn policy, cardinality change, or deeper recursion
was started.
