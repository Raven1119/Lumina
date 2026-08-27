# Depth-Two Recursive AgentProcess Result

## Verdict

- Deterministic mechanism: **PASS**.
- Real DeepSeek depth-two topology evidence: **NOT VALIDATED (0/3)**.
- Overall Slice 11 research claim: **NOT VALIDATED**.

The same `AgentProcess` implementation can deterministically run one fixed
three-Actor chain:

```text
Root(depth=0)
-> Child(depth=1)
-> Grandchild(depth=2)
-> Return to Child
-> Return to Root
```

This establishes the mechanism under scripted decisions. It does not satisfy
the task card's real-provider validation gate because DeepSeek did not choose
the recursive topology in any of the three permitted fresh attempts.

## Implemented surface

- `AgentProcess(..., max_depth=2)` enables the frozen recursive mode. Existing
  default depth-one, up-to-three-direct-Child branching remains compatible.
- Recursive mode fixes one Child per Actor, depth at two, total Actors at
  three, and Host execution to the existing sequential drive path.
- `ChildRef`, `EXECUTION_STARTED`, and derived `ExecutionState` retain Actor
  identity, direct parent identity, depth, and maximum depth. Durable start
  facts reject a conflicting downgraded handle on restart.
- Depth-one Child requests expose the existing `SpawnChild`; depth-two
  Grandchild requests do not. Capacity/depth rejection occurs before identity
  creation or `CHILD_SPAWNED` append.
- Root keeps the only `ClaimComplete` authority. Every non-Root Actor uses
  `Return`. A descendant outcome is an identified `ChildObservation` in its
  direct parent's EventLog; it is not automatically forwarded to Root.
- Root, Child, and Grandchild retain separate EventLogs, State, bounded
  Context, DecisionFrames, and live working state. They share only the same
  workspace reality through `SharedEnvironment`.

No `RootAgent`/`ChildAgent`/`GrandchildAgent` loops, RecursiveSpawn, scheduler,
parallel execution, Actor Directory, Blackboard, direct messaging, resource
lease, planner, reviewer, specialist persona, or depth-generic framework was
added.

## Deterministic evidence

Eight maintained deterministic/recovery tests establish all task-card cases:

1. **A - depth lineage:** Root, Child, and Grandchild retain depths 0, 1, and
   2, three distinct Actor identities, and exact direct-parent links.
2. **B - Grandchild cannot Spawn:** `spawn_child` is absent from the depth-two
   request. A model-emitted attempt fails as `unauthorized_action:SpawnChild`
   with no new identity or `CHILD_SPAWNED` fact.
3. **C - recursive Context isolation:** Grandchild Context contains neither
   the Root sentinel nor the Child-private sentinel. It receives only its
   local goal, local State/observations, and shared-world access.
4. **D - nested Return:** Grandchild returns `"21"` to Child; Child sees that
   identified result and separately returns `"42"` to Root. Causal parent and
   child identities remain exact at both delivery boundaries.
5. **E - shared world:** Grandchild writes `grandchild.txt`; Child and Root
   later read that same workspace file before verified completion.
6. **F - failure propagation:** Grandchild failure appends `CHILD_FAILED` only
   to Child. Child sees it and decides to return a handled result; Runtime does
   not promote the failure directly to Root.
7. **G - restart integrity:** Reload preserves depth-one parent, depth-two
   descendant, lineage, and the single committed Spawn. Pending resume samples
   no model and creates no new Child. A forged depth downgrade conflicts with
   canonical start facts and is rejected.
8. **H - total bound:** recursive Root admission rejects a second Child before
   identity creation; depth-two Spawn is also rejected before any fourth
   Actor can exist.

For all three Actor logs, `fold(full EventLog) == restored ExecutionState`.
Two additional restart boundaries are explicit: a durable single-Spawn
DecisionFrame whose Spawn append was interrupted is completed without another
Model call or duplicate identity; schema-1 checkpoints fall back to full
canonical EventLog replay, while schema-2 checkpoints retain tail folding.

## Real DeepSeek smoke

The frozen experiment used three fresh workspaces:

```text
outer.txt = TARGET=answer
inner.txt = VALUE=42
goal = Determine the requested target from outer.txt.
       Delegation is available recursively when useful.
       Write answer.txt containing the final value.
CompletionSpec = FileContentEquals("answer.txt", "42")
```

No prompt, provider setting, adapter behavior, or Runtime planning rule was
changed. Exactly the permitted three attempts were sent.

| Attempt | Root Spawn | Child Spawn | Recursive chain | Verified completion |
|---:|---:|---:|---|---|
| 1 | 0 | 0 | no | no; Root direct path failed |
| 2 | 0 | 0 | no | no; Root direct path failed |
| 3 | 0 | 0 | no | yes; Root completed directly |

Attempt 3 confirms only the already-validated Root/provider/tool/completion
path. Under the task card, direct completion is not depth-two evidence, so the
real recursive claim remains **NOT VALIDATED**. The prompt was not strengthened
and no fourth attempt was sent.

## Boundaries and next decision

The deterministic result supports only this bounded claim: the same
AgentProcess mechanism can recursively reach depth two and can propagate local
results one parent at a time. It does not establish that a real model will
choose recursive delegation from a natural task description, that recursion
improves performance, that hierarchical decomposition is useful, or that
deeper/branching topology should be opened.

Because the required real topology did not occur, Slice 11 is not promoted as
validated. The next decision should be based on this observed model-choice
failure; this report does not authorize deeper recursion, prompt steering, a
spawn heuristic, or trajectory-topology claims.

## Validation

```text
python -m pytest Execution_lab2/test_recursive_depth2.py
8 passed, 1 gated real-provider test skipped

focused Slice 11 plus checkpoint-tail regression
9 passed, 1 gated real-provider test skipped

python -m pytest Execution_lab2 -q
137 passed, 9 skipped

python -m pytest -q
328 passed, 24 skipped

python -m pytest Conversation_Memory/tests -q
163 passed, 45 skipped

python -m pytest Dream/tests -q
36 passed, 1 skipped

git diff --check
PASS

live ipykernel / secret scan / upstream MAGMA status
0 / clean / clean
```

The independent Standards/Spec review found two restart defects: legacy
checkpoint digest compatibility and the single-Spawn decision-to-append crash
window. Both were fixed test-first and the final re-review reports no remaining
code or Spec finding. One stale status sentence was narrowed to distinguish
settled-decision replay from completion of a current frozen decision's
never-committed suffix.
