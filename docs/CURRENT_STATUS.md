# Lumina current state

The cognitive core is now `Mind <-> Nervous <-> Execution <-> Environment`.
Each organ owns its state and continuation; there is no central Session/Host.
The supported single-goal foreground CLI is `python -m Mind`, using a new
`--state` directory. Production Chat still runs its separate Recall gate;
the manual Execution API is also separate.

## Current implementation

| Part | Responsibility |
| --- | --- |
| Mind | Original user/important events, persistent selective cognition, necessary evidence/analysis, NoChange or original high-level guidance. |
| Nervous | Original input transport, durable causal mailboxes, idempotent completion, mechanical foreground continuation and shared provider accounting. |
| Execution | Independent local implementation, AgentProcess/facade lifecycle, immutable environment sources, single guidance binding, persistent received advice and feedback. |
| Analysis | Mind-owned independent bounded context; structured understanding, optional isolated static/stateful computation, model reuse/revision and compact attributed reports. |
| Prediction feedback | Prospective action/condition/object/time/quantity declaration, actual observation watch, mechanical alignment/comparison and Mind reassessment. |

Current cognition/native formats are `mind-cognition-v1` and
`mind-native-v1:6-calls`. Old experiment session/schema/native replay is retired.
Historical conclusions are preserved; old raw campaigns and provider responses
are not maintained runtime or test dependencies.

Promoted mechanisms now live in formal activity/cognition/contracts/model/
analysis modules, Execution's model/sandbox/evidence/runtime, and Nervous's
provider/storage. The old chain -> event_loop -> decoupling -> behavioral imports,
experiment_a, central host and checkpoint fixture are removed.
Production Recall gates, the supported Execution facade and licenses remain.

## Evidence

V70-V73 previously demonstrated scoped correction of retained erroneous cognition,
NoChange closure and a CLI file task, with development repairs and explicit
recovery involved. Those results did not establish general autonomous reliability.
All failed/INCONCLUSIVE conclusions remain in
[EXPERIMENT_HISTORY](../Mind/docs/EXPERIMENT_HISTORY.md).

This refactor uses deterministic current-invariant regression rather than a new
model-ability campaign. A new Unicode CSV smoke has run actual isolated model
calculation and IPython action, preserved original guidance, measured real output,
compared declared byte quantities, returned to the same Mind and restarted quietly.
It used eight injected provider responses (Mind 4, analysis 2, Execution 2),
zero real provider requests. Scripted decisions do not establish model judgment.

The smoke exposed and corrected duplicate reviews when request_mind and a changed
prediction described the same committed checkpoint. Review also identified
initialization and stale-guidance continuation gaps; targeted recovery regressions
cover the repaired paths. Explicit CLI retry preserves failures and cost while
reassessing current evidence; known read/analysis errors no longer wedge delivery.

Core refactor validation on 2026-09-09 (commit `1f893d7`):

- Maintained default suite: **641 passed, 29 skipped**. Optional/environment tests
  remain opt-in; the two upstream MAGMA deprecation warnings are unchanged.
- Isolated Docker computation plus the fresh whole-loop smoke: **14 passed**.
- Standards and Spec review: no remaining findings after the recovery fixes.
- Current Python parsing/import audit, maintained document links and
  `git diff --check` passed; no retired campaign module is imported.

The full suite uses a fresh temporary directory outside the checkout: Recall's
sandbox tests intentionally reject repository paths. An initial in-repository
test directory caused six such refusals; moving test state fixed the setup
without changing Recall's safety policy. No real model/provider calls were made.

## Event semantics follow-up

The post-refactor source review found two event-delivery defects. User input
was deduplicated by text/type rather than submission identity; a second
intentional identical message could disappear. Submission now has a fresh
identity by default; explicit `submission_id` / CLI `--submission-id` reuses
only the same transport submission, with immutable content checked by Nervous.

Prediction watching had also returned early without an Execution Run, despite
accepting the watch. Registered external observations now remain monitored on
foreground resume after NoChange, even without an Actor. Subsequent review
also caught A-B-A observations being mistaken for an old notification: without
a Run, the latest accepted review now distinguishes successive observation
checks while a pending notification retains the same identity. Each new watch
retains its own registration baseline until a later accepted review covers it;
an earlier review cannot manufacture a change for that watch. Recovery and delivery
remain owned by the existing organs; no background scheduler is introduced.

Event-fix validation at `5757df7`: **323 passed, 5 skipped** in the full Mind/Nervous/Execution
suite. Regressions cover completed/pending and lost-response submission retries,
both CLI input forms, and eight no-Run observation scenarios including repeated
values, registration after an earlier review, unread sources and restart.
Standards and Spec review have no remaining findings. Real provider calls: zero;
new prediction tests inject both model responses and calculation output.
The whole-repository rerun did not start because automatic execution approval
timed out twice; Docker opt-ins were not rerun. The earlier refactor counts above
remain historical evidence, not a claim of a new full-tree validation.

The next source review found that the A-B-A fix still excluded an existing Run
at an unchanged waiting checkpoint. The runtime now uses the latest accepted
review to distinguish later observation changes both with and without a Run.
An accepted review also closes notification of that exact checkpoint without
falsely acknowledging unread prediction comparisons. Original pending events
keep their identities through delivery retries and restart. No transport store,
scheduler or cognitive authority was added.

Fixed-checkpoint validation: **331 passed, 5 skipped** in Mind/Nervous/Execution.
Before the repair, all eight added waiting-Run variants reproduced the lost
notification, while the eight no-Run variants passed. The expanded regression
checks A-B-A-B, an unchanged Actor checkpoint and deliveries, unread obligations,
publication retries, restart and quiet settled resumes. The final addition of
restart after publication but before handling passed all **16** focused cases
(one Docker opt-in skipped, one unrelated test deselected). Standards and Spec
review found no remaining issues in this diff. Real provider calls: zero;
responses and calculation output are injected. Whole-repository and Docker
checks were not rerun for this narrow repair. This is scoped regression evidence,
not a claim that the entire cognitive loop has no remaining defects.

## Scope and limits

- One goal, foreground operation, one writer and a small authorized workspace.
  No background scheduler, autonomous goals or formal Intention switch.
- Optional models describe conditional consequences; arbitrary outside-reality
  anomaly detection and full-trajectory validation are not implemented.
- Source references, legal schema, successful computation, delivery and runtime
  markers do not establish semantic correctness or business success.
- UTF-8-sig source text is not a general proof of original bytes.
- Unknown actions/dispatches remain stopped for explicit resolution; resources
  and processing failures remain visible, without fabricated NoChange.
- Retired historical sessions are not migrated. Start a fresh current state.
- Chat, Memory algorithms, Cold-first continuity and manual Dream are unchanged.

Use [INTEGRATED_CHAIN](../Mind/docs/INTEGRATED_CHAIN.md) for setup, input, status,
resume, retry and budgets; [NORTH_STAR](NORTH_STAR.md) for long-term direction.
