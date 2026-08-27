# Single Child AgentProcess Result

## Hypothesis

Can one complete, independent Child AgentProcess exist on the validated
Execution substrate, operate against the same external workspace, return one
bounded local result, and allow the persistent Root to continue to its sole
verified completion?

Baseline: `4faa14accf1cd8cd9fe9e31f58eba6af1d345585`.

The annotated local tag `execution-mvp-v1` freezes that validated MVP baseline.

## Frozen semantics

- Root-only action: `SpawnChild(goal: str)`.
- Spawn durably returns `ChildRef`; it never returns the Child answer.
- Exactly one direct Child is admitted: `max_children=1`, `max_depth=1`.
- The Host explicitly drives Root spawn, Child run, Child return, and Root
  continuation. There is no scheduler or background dispatcher.
- Child receives only its bounded local goal and its own execution facts. Root
  transcript, EventLog, DecisionFrames, Context, and IPython namespace are not
  copied.
- Child owns a separate EventLog, derived ExecutionState, bounded Context,
  DecisionFrames, live IPython control, and lifecycle. Root and Child share
  only the existing `SharedEnvironment`.
- Root capabilities are Tool/IPython, Wait, SpawnChild, and ClaimComplete.
  Child capabilities are Tool/IPython, Wait, and Return.
- `Return(local_result)` is bounded to 1,024 characters. Child cannot
  ClaimComplete or Spawn; Root cannot Return.
- The Root EventLog receives one causal `CHILD_RETURNED` or `CHILD_FAILED`.
  Root Context receives a bounded structured Child Observation and Root
  decides what to do next.

## Mechanism evidence

- Spawn persists distinct Child execution/actor identity, direct parent
  identity, local goal, and Child EventLog path before returning
  `child_pending`.
- `AgentProcess` is a thin capability-enabled subclass of the frozen
  `RootAgentProcess`; Root and Child execute the same loop. There is no
  `ChildAgentLoop`.
- A Child EventLog begins with its own identity and lineage. Its State and
  DecisionFrames fold only from that log.
- Host delivery validates the Child handle, terminal identity, local goal, and
  durable Child log before appending the corresponding Root outcome.
- Root remains blocked while `child_pending`; after one outcome it becomes
  runnable. The retained handle rejects a second spawn and prevents
  redelivery.
- Full durable EventLog folds equal the restored State for both processes.
  Restart after durable Root delivery neither respawns the Child nor accepts
  the same result again.
- The focused deterministic suite covers identity, context isolation, direct
  shared-world observation, separate live IPython namespaces, Return,
  role-limited authority, one-Child enforcement, failure propagation,
  restart/deduplication, provider schema isolation, and replay equivalence.

## Real DeepSeek smoke

The secret-safe gated test used three fresh workspaces, each initialized with:

```text
facts.txt = VALUE=42
```

Goal:

```text
Use one child process to inspect facts.txt and obtain VALUE.
Then write answer.txt containing only the VALUE.
```

CompletionSpec: `FileContentEquals("answer.txt", "42")`.

| Run | Root model calls | Child model calls | Child returned | Root verified | Exact output |
| --- | ---: | ---: | --- | --- | --- |
| 1 | 3 | 3 | yes | yes | yes |
| 2 | 3 | 2 | yes | yes | yes |
| 3 | 3 | 2 | yes | yes | yes |

All three executions contained exactly one `CHILD_SPAWNED`, one delivered
`CHILD_RETURNED`, and `COMPLETION_VERIFIED`. No credential value, prefix,
suffix, Authorization header, provider request body, or raw provider response
was printed by the experiment.

## Verdict

`FAIL`

The single-Child mechanism itself passed all 11 deterministic tests and all
three fresh real DeepSeek executions. The task-level PASS gate is nevertheless
not satisfied because the required exact Execution regression command did not
finish green:

```text
python -m pytest Execution_lab2 -q
106 passed, 7 skipped, 1 failed
```

The only remaining failure is the pre-existing
`test_ipython_timeout_is_explicit_and_shutdown_leaves_no_kernel`: with its
0.5-second limit, the unchanged pinned Jupyter startup path sometimes returns
`kernel_error` before code execution instead of reaching the expected
`timeout`. The test passed once in isolation during this run but repeatedly
failed in the full command. Both `ipython_control.py` and its test have zero
diff from `execution-mvp-v1`. This Slice did not modify them because that
would add a second variable outside the task.

Other required regressions completed:

- focused single Child: 11 passed, 1 gated real-provider test skipped;
- all Execution tests except the named baseline timing case: 106 passed,
  7 skipped, 1 deselected;
- repository default suite: 328 passed, 24 skipped;
- Conversation Memory: 163 passed, 45 skipped;
- Dream: 36 passed, 1 skipped.

Consequently the evidence supports the mechanism hypothesis, but the task's
strict overall result remains FAIL. No claim about performance, recursive
utility, emergent hierarchy, swarm behavior, or topology superiority is made.

## Known limits

- There is no second Child, grandchild, recursive Spawn, or parallel actor.
- There is no scheduler, worker pool, queue, registry, actor directory,
  message bus, Blackboard, resource lease, capability search, or specialist
  role.
- Host sequencing is explicit. Child crash recovery is not automatic.
- Only the local shared filesystem/shell workspace is established as shared
  external reality.
- Child IPython state is live-process-only and is intentionally fresh after a
  process restart.
