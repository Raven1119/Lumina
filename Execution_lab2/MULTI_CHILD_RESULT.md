# Homogeneous Spawn Batch Result

## Verdict

- Deterministic mechanism: **PASS**.
- Real DeepSeek topology evidence: **PASS / VALIDATED**.
- Overall research claim: **PASS / VALIDATED**.

One Root model response can now request two or three independent direct
children through an ordered homogeneous tuple of existing
`SpawnChild(goal)` actions. The Host admits separate Child identities and
later returns separate results to the same Root continuation. This is not a
`SpawnMany` action, a scheduler, or parallel execution.

## Implemented surface

- `DeepSeekModel` accepts multiple calls only when every parsed action is
  `SpawnChild`. Ordinary Tool/IPython sibling batches remain supported.
  Every other multi-control response remains wholly rejected.
- Before creating any Child id, Runtime checks Root authority, durable Root
  storage, the total remaining capacity, complete ordered provider call ids,
  call-id uniqueness, and each action/call-id binding.
- One provider response produces one `MODEL_DECISION` and one
  `DecisionFrame` whose resulting action and provider id are ordered tuples.
  Each admitted Child still gets its own `CHILD_SPAWNED` event citing that
  decision; no synthetic batch event or batch id exists.
- `SpawnChild.provider_tool_call_id` and
  `ChildRef.provider_tool_call_id` retain the original opaque provider
  identity. Each `ChildObservation` exposes the matching identity only for
  native continuation correlation.
- The Host drives admitted children sequentially in creation order. Root is
  not sampled after only a prefix of the batch settles. Once every sibling in
  that decision has returned or failed, the next native request contains one
  ordered `role=tool` message per original call id.
- EventLog remains historical authority. Child handles, pending status,
  outcomes, and Context are derived by fold/projection. Restart does not create
  a second Child identity or repeat a Spawn event.

## Deterministic evidence

The maintained A-G experiment establishes:

1. **A - two Spawn calls:** one DecisionFrame freezes the ordered A/B action
   and call-id tuples; two unique direct Child identities and two independent
   causal `CHILD_SPAWNED` events are committed.
2. **B - three Spawn calls:** three children are admitted in provider/model
   order under the fixed ceiling of three.
3. **C - atomic capacity rejection:** with two prior Child identities, a C/D
   batch is rejected as `child_limit_reached`; zero new identity or Spawn
   event is created.
4. **D - duplicate provider identity:** the entire response fails as
   `model_protocol:duplicate_tool_call_id`; zero Child is admitted.
5. **E - mixed controls:** Spawn+Read, Spawn+Wait, Spawn+ClaimComplete,
   Spawn+Return, Wait+Wait, and Claim+Claim are all rejected before any Child,
   Tool, Wait, or completion effect.
6. **F - native continuation:** the A/B assistant calls become exactly two
   ordered Tool result messages with the same A/B provider ids and independent
   Child results.
7. **G - restart:** Root execution id, Root actor id, Child identities,
   creation order, call identities, and fold equality survive EventLog reload.
   Resume performs zero model calls while any sibling from the batch remains
   pending and performs no repeated Spawn. A separate crash-between-appends
   test proves that restart preserves the already committed Child and appends
   only the same DecisionFrame's never-committed Spawn suffix.

Batch preflight also rejects a native multi-Spawn decision with missing call
ids. The legacy one-at-a-time scripted Spawn path remains unchanged.

The earlier bounded-sibling evidence also remains green: shared filesystem
reality with isolated Child Context/IPython namespaces, independently
identified return/failure facts, a fourth-Child ceiling, bounded Context, and
single-Child compatibility.

## Real DeepSeek experiment

The frozen experiment used:

```text
a.txt = 17
b.txt = 25
goal = use separate child processes to inspect each input, combine the findings,
       and write answer.txt containing their sum
CompletionSpec = FileContentEquals("answer.txt", "42")
```

No prompt or provider setting was changed. The first fresh run succeeded, so
the allowed second and third attempts were not sent.

| Run | Root response shape | Spawned | Returned | Failed | Integrated output | Verified |
|---:|---|---:|---:|---:|---|---|
| 1 | two homogeneous `spawn_child` calls | 2 | 2 | 0 | `answer.txt == "42"` | yes |

Both provider call ids were distinct and non-empty. Their opaque literal values
were intentionally not printed to stdout; admission and native continuation
mechanically required equality across provider response, `SpawnChild`,
`ChildRef`, `ChildObservation`, and the two ordered `role=tool` messages.
The durable Root history contained two `CHILD_SPAWNED`, two
`CHILD_RETURNED`, and `COMPLETION_VERIFIED`.

This validates only the bounded claim:

```text
one Root local decision
-> multiple independent direct sibling Child identities
-> independent Child execution and Return
-> one Root continuation integrating both results
-> environment-verified completion
```

It does not establish parallel speedup, swarm behavior, recursive topology,
automatic decomposition quality, or general multi-agent superiority.

## Preserved boundaries

- Maximum direct children remains three and maximum depth remains one.
- Child still cannot Spawn or ClaimComplete; Root still cannot Return.
- No Child parallelism, scheduler, join, Actor Directory, Blackboard,
  messaging layer, resources, specialists, Planner, Reviewer, retry, or
  cancellation framework was added.
- Ordinary Tool sibling batches keep their existing ordered sequential
  semantics.
- Runtime still performs no semantic planning; it only validates and commits
  the typed provider decision.

## Validation

- Focused homogeneous Spawn batch tests: 14 passed.
- `Execution_lab2`: 129 passed, 8 gated real tests skipped.
- Repository default suite: 328 passed, 24 skipped.
- Conversation Memory: 163 passed, 45 skipped.
- Dream: 36 passed, 1 skipped.
- Real DeepSeek frozen multi-child experiment: 1/1 full chain validated.
- `git diff --check`: clean.
- Live `ipykernel` processes after validation: 0.
- Credential-pattern scan of in-scope files: no credential value found.
- Pinned MAGMA status and diff: clean.
