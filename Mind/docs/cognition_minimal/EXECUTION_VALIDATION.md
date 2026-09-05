# Current Mind / Execution validation

2026-09-05. The creator narrowed this task: complete Execution validation, then
stop. Do not continue the broader cognition roadmap or production integration.
Mind / other-organ interaction must use events, with dispatch owned by the host.

## Existing evidence reused

- E0: real Root advisory projection, one-shot delivery, retry/reopening, exact
  request fidelity and authority boundaries passed. No behavioral-value claim.
- E1: candidate 2/6 versus baseline 1/6, but neither rescue carried a Directive;
  delivered Directives produced one regression and one both-fail. INCONCLUSIVE.
- E2: PUSH 4/6 versus PULL 3/6, with NoChange in the sole differing pair; a
  cwd-external access attempt invalidated causal interpretation. INCONCLUSIVE.
- E3 / E4: grounded-guidance experiments, not proof of Execution improvement.
  E4 DeepSeek produced 10 supported Directives, but baseline had no contradicted
  Directive, so its preregistered verdict remains INCONCLUSIVE.
- W0-W8: bounded synthetic modeling history, not real Execution prediction.
- S0: owner PRE/OUTCOME and non-interference worked; terminal historical PRE
  resolution did not. S0 remains INCONCLUSIVE. The new P0 receipt tests separately
  validate historical resolution and owner registration ordering; no relabeling.

References: `../EXPERIMENT_E0_RESULT.md` through `../EXPERIMENT_E4_RESULT.md`,
`../WORLD_MODEL_W0_W8_AUDIT.md`, `../WORLD_MODEL_SHADOW_S0_RESULT.md`.

## Current gap and minimal fix

Persistent Mind binds APPLIED to `execution_ref:root:decision_id`. E0's older
bridge passes its input ID unchanged, while Execution expects an Actor-local
decision ID. A new, pure-data host projection validates the complete receiving
run/Root/decision identity before projecting the local ID. The original E0 entry
and durable full target remain unchanged. No Execution runtime change is needed.

The added integration regression reopens accepted Mind state, routes its output
to actual Execution, checks one-shot request delivery and unchanged goal/tools,
then passes the owner's completion evidence back as a new MindInput event.
Duplicate inputs cause no additional cognition. Wrong-run/decision routing fails
closed. All 15 E0/current-integration tests passed (2.38 seconds).

## Frozen live smoke

One fresh smoke reuses the unmodified first E2 task (weighted quorum); it does not
rerun or revise E2's historical provider campaign. An actual Execution IPython
read of rules.txt, selected by a deterministic seed action, creates the common
pre-continuation evidence. The existing E1 freeze helper suspends at the next
decision boundary. The subsequent Execution decisions and Mind are DeepSeek-V4-Pro.

- One explicit start event, one accepted Mind result, optional one-shot advisory,
  one Execution continuation, and one owner-result event back to the same Mind
  store after reopening. NoChange / failure are retained without resampling.
- Memory unavailable, no computation request, no model callback to Execution.
  The host constructs immutable observations and alone calls public owner methods.
- Mind: at most two activations / four provider calls, two calls per activation,
  2000 output tokens / 2000 characters, temperature 0, timeout 30 seconds.
- Execution: existing 5000-character context; at most six additional Root
  decisions, four per Child, and eight provider decisions total across Actors.
  The existing E1 continuation wall-validity ceiling is 420 seconds.
- Temporary, unrelated workspace/control roots; preserve exact requests, outputs,
  source hashes, event records and final journal. Inspect every action for access
  outside its workspace. No production data or provider credentials in artifacts.
- The verifier and task are unchanged. Report provider/protocol failure, completion,
  delivery, returned evidence and replay independently. This is an integration
  smoke, not a new benefit comparison, holdout, or generalization claim.
- A suspended prefix is ineligible for new owner prediction registration. Do not
  invent a prospective result. Validate the waiting-head receipt seam separately
  with the existing P0 tests, including lost-response/restart reconciliation.

After relevant regressions and recording the result, stop. Full request/result
event orchestration for the older synchronous Memory acquisition loop and an
automatic prediction lifecycle remain unimplemented and are deferred by this stop.

## Recorded result and stopping point

`execution_smoke_1.json`: the first host attempt made zero provider calls. The
historical E1 observation helper labels work `in_progress`, while the new MindInput
correctly requires its snapshot status to equal the event status (`suspended`).
It rejected `execution_snapshot_conflict` before recording an activation. The
retry changed only this temporary host projection, preserving the actual owner
status; the failed artifact and its exact original script remain unchanged.

`execution_smoke_2.json`: two DeepSeek Mind calls and six DeepSeek Execution
decisions. Both Mind events were accepted (revision 1 -> 2); reopening recovered
the original state, duplicate delivery made no new calls, and all actual Mind
requests replayed exactly. The first activation retained the voting rules and an
open data-source question, returning NoChange. Therefore there was no advisory
delivery in this live smoke. The deterministic integration regression, separately,
proves the non-empty advisory path, recipient binding and one-shot delivery.

Execution computed the winning motion correctly but wrote only `alpha` to
`answer.txt`, omitting the existing task's required padding and result fields.
It subsequently reread task inputs and reached `decision_limit_reached` after the
six allowed continuation decisions (15.03 seconds). No ClaimComplete occurred.
This is an observed execution/task-contract failure, not a transport failure or
a reason to change the fixture, budget or provider and rerun until it passes.
Execution's own OUTCOME was `failed`, with `completion_verified=null`; null is
unverified, not an owner-attested false prediction. Mind retained that failure
and a new question about its cause when the host returned the owner event.

All seed and continuation IPython actions were inspected: relative accesses were
limited to mission.txt, rules.txt, members.csv, ballots.jsonl and answer.txt. No
Child, shell/subprocess, network, parent/absolute traversal or external workspace
access was requested. The unused `import os` in the first continuation did not
perform OS operations. This is a trace audit, not an OS sandbox claim.

Validation:

- 15 E0/current-delivery tests passed, including the added actual Execution loop.
- 155 selected cognition/trace/delivery/S0/P0 tests passed, one explicit Docker
  skip. The five P0 receipt tests cover historical PRE, stale/foreign/late
  rejection, persisted receipts, lost-response reconciliation and restart.
- Root regression: 347 passed / 24 skipped. Earlier unchanged Memory and Dream
  regressions remain 163 / 45 skipped and 36 / 1 skipped respectively.
- `git diff --check` passed; pinned MAGMA status/diff stayed empty. Exact smoke
  scripts and their hashes were verified in the artifacts before deleting the
  temporary scripts and both owned synthetic Execution workspaces.
- No production Chat migration, Memory/Dream changes, or Execution runtime
  changes were made in this final validation step. The only implementation fix
  is the pure-data recipient projection in `execution_steering_experiment.py`.

**Disposition:** event round-trip integration validated for this explicit host;
non-empty advisory delivery validated by deterministic actual-runtime regression;
live task completion failed; behavioral benefit remains unestablished. Validation
is finished and development stops here as requested. The earlier synchronous
capability path and full prospective prediction orchestration are not claimed
complete, and earlier E/W/S verdicts are not rewritten.
