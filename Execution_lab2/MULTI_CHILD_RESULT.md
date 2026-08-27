# Bounded Sibling Child Result

## Verdict

- Deterministic mechanism: **PASS**.
- Real DeepSeek topology evidence: **NOT VALIDATED**.
- Overall research claim: **NOT VALIDATED**.

The implementation proves that one Root can durably form and integrate up to
three independent depth-one sibling Child processes. The required real-model
chain did not occur: DeepSeek attempted two `spawn_child` calls in one provider
response rather than making separate local decisions.

## Implemented surface

- The existing `SpawnChild(goal) -> ChildRef` action is unchanged. There is no
  `SpawnMany`.
- `AgentProcess` admits at most three direct Root children. `max_depth` remains
  one because Child still cannot Spawn or ClaimComplete.
- Every accepted Spawn persists a distinct Child execution id, actor id, direct
  parent id, bounded local goal, and Child EventLog path.
- Each Child keeps its own EventLog, derived State, bounded Context,
  DecisionFrames, IPython namespace, and lifecycle. All children use the same
  `SharedEnvironment`.
- The Host drives children sequentially in creation order. There is no actor
  parallelism, scheduler, worker pool, or async join.
- Each delivered outcome remains a separate Root event with exact Child and
  parent identity: `CHILD_RETURNED` or `CHILD_FAILED`.
- Root may use the existing `Wait("CHILD_RESULT")`. One matching Child outcome
  wakes Root, which decides whether to wait again, act, or claim completion.
  Runtime has no `wait_all_children` or `all_results_ready` predicate.
- Root Context projects at most three identified Child handles/outcomes.
  Child Context does not project sibling private state.
- Child handles and outcomes are derived only by folding the canonical Root
  EventLog. They are not a second persistence authority or Actor Directory.

## Deterministic evidence

Six maintained tests establish:

1. Three Spawns produce three unique Child execution/actor identities with one
   parent. A fourth Spawn fails with `child_limit_reached` and produces no
   fourth `CHILD_SPAWNED`.
2. Child A writes `shared.txt`; Child B reads the resulting filesystem state.
   Their local goals and model Contexts remain isolated while world reality is
   shared. Root receives independent `alpha` and `beta` events with the correct
   Child ids.
3. A return, a failure, and another return remain three distinct Root facts.
   Runtime does not retry, replace, or cancel a sibling; Root continues and
   reaches verified completion.
4. A crash after two durable returns reconstructs the same Root execution id,
   Root actor id, Child identities, lineage, and results. Neither Spawn nor
   Return delivery repeats.
5. Three live sibling IPython controls retain separate namespaces and shut down
   cleanly. Each Child sees its own sentinel and not either sibling sentinel.
6. Three identified Child outcomes plus a later ordinary Tool observation
   remain usable at the minimum supported `max_context_chars=768`. Root sees
   the creation-ordered actor-id list, the correspondingly indexed return facts,
   and the real Tool result without exceeding the absolute Context bound.

The migrated Single Child regression also proves that Root may decide to Spawn
a second sibling after the first Child returns. All other Single Child
identity, authority, isolation, failure, provider, IPython, and restart
regressions remain green.

## Real DeepSeek experiment

The experiment used three fresh workspaces:

```text
a.txt = 17
b.txt = 25
CompletionSpec = FileContentEquals("answer.txt", "42")
```

Root received the task-card goal and only the statement that multiple Child
processes were available. It was not told to Spawn exactly two children or to
use a fixed A/B order.

Observed durable facts:

| Run | Root decisions before failure | Provider control response | Committed Spawn | Verified |
|---:|---|---|---:|---:|
| 1 | first decision | `spawn_child`, `spawn_child` in one response | 0 | no |
| 2 | shell, then two ordinary reads, then control response | `spawn_child`, `spawn_child` in one response | 0 | no |
| 3 | first decision | `spawn_child`, `spawn_child` in one response | 0 | no |

All three executions ended with
`model_protocol:mixed_control_tool_calls`. This is the expected admission
failure for multiple control calls in one provider response. Accepting the
response would turn one provider-level fan-out into a fake sequence of Root
local decisions and violate the Slice contract. The adapter and prompt were
therefore not changed, and no fourth real execution was sent.

DeepSeek demonstrated an intent to branch, but no run produced:

```text
Root -> at least 2 committed Child identities
     -> independent Returns
     -> Root integration
     -> COMPLETION_VERIFIED
```

The real-provider criterion is consequently **NOT VALIDATED**, not PASS.

## Preserved boundaries

- `RootAgentProcess` MVP actions and completion authority are unchanged.
- Child cannot Spawn, Return remains bounded, and Root cannot Return.
- Actors remain sequential and depth remains one.
- No recursion, grandchild, parallel execution, `SpawnMany`, join framework,
  Blackboard, Actor Directory, direct messaging, resource system, specialist
  roles, Planner, or Reviewer was added.
- No performance, swarm, emergence, or multi-agent superiority claim is made.

## Validation

- Focused multi/single Child: 17 passed, 2 gated real tests skipped.
- `Execution_lab2`: 115 passed, 8 gated real tests skipped.
- Repository default suite: 328 passed, 24 skipped.
- Conversation Memory: 163 passed, 45 skipped.
- Dream: 36 passed, 1 skipped.
- Real DeepSeek multi-child experiment: 0/3 validated; exact failure above.
- `git diff --check`: clean (line-ending notices only).
- Live `ipykernel` processes after validation: 0.
- Credential-pattern scan across the six in-scope files: no match.
- Pinned MAGMA status and diff: clean.
