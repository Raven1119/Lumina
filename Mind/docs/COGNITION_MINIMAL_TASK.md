# Minimal cognitive loop — implementation task

2026-09-05. Authorized by the creator's `/ponytail` implementation request.
Reference: `docs/MIND_COGNITIVE_ARCHITECTURE.md`; this is an isolated, explicit
Mind entry, not migration of the production Chat Recall gate.

**Resumed work (2026-09-05):** the creator explicitly resumed Mind development,
then required Nervous Organ event infrastructure first. Build and validate the
minimal mailbox in `docs/NERVOUS_EVENT_FOUNDATION.md` before continuing Mind's
external request/result integration. This supersedes the previous stop below.
The creator then explicitly limited implementation to Mind and Nervous, with no
Chat or Memory connection. That scoped event loop is implemented and validated;
the foundation document records the interfaces, failure semantics and evidence.
The broader historical roadmap below is not a claim that other organs are wired.

**Previous stopping point (2026-09-05):** the creator required event-driven
interaction with other systems, then narrowed the remaining work to Execution
validation and requested stopping afterward. That validation is finished; see
`cognition_minimal/EXECUTION_VALIDATION.md`. That task stopped at the requested
boundary, not a claim that the entire proposed cognition architecture is built.
The stage notes below retain their historical order. That stop limited the
previous task; the resumed scope above authorizes the current Mind/Nervous work.

The goal is a continuous investigation: retain understanding across activations
and restart, use evidence to compare explanations and conditional scenarios,
request a useful observation/model when needed, and revise guidance from actual
results. Emotion, autonomous goals, scheduling, and automatic Dream are excluded.

## Stages and evidence required

1. P0: historical PRE is readable independently of new prediction eligibility.
   Execution accepts a prediction digest only at its current Root waiting head.
   Receipts survive completion/restart; late/stale/foreign registration fails;
   lost responses can be reconciled; failed persistence preserves the prefix.
2. P1: one persistent MindOrgan reuses the existing A–D cognitive loop and
   Directive delivery. It records input identity before inference, accepts a
   bounded update atomically, survives restart, and rejects event/source/goal
   conflicts. An issued but unaccepted Directive is not deliverable.
3. P2: compare competing beliefs and qualitative SAO scenarios using genuine
   owner evidence; test whether guidance changes real Execution behavior.
   Scripted tests prove protocol mechanics only. A real-model trace is needed
   for cognitive behavior, and a same-budget comparison for claimed benefit.
4. P3: inspect and adapt Tycho's official isolated computation and model
   verification implementation; independently verify isolation before exposing
   computation. Keep generated code away from host authority and gold outcomes.
   Measure initialization, dynamics, outcome, coverage and prospective results
   separately. No new arithmetic-only fixture campaign.

## Frozen initial protocol and bounds

- DeepSeek-V4-Pro, non-thinking; deterministic mock adapters for tests.
- One activation: at most 2 model calls, 1 Memory/Execution read, 2000 model
  output characters per call; existing A Recall policy remains unchanged.
- Initial real protocol smoke: max 2000 output tokens, temperature 0, timeout
  30 seconds; at most 2 activations / 4 calls, synthetic local evidence only.
  Stop on the first protocol failure; record it rather than tuning and rerunning.
  This smoke is not the P2 behavioral-benefit comparison.
- At most 4 item updates per activation, 8 active items, 3 evidence inputs,
  3 steps per qualitative scenario, 8000-character cognitive context.
- One intention per store; max 64 activations. No implicit intention switch.
  One native OS writer lock, released by process exit. A concurrent request is
  returned as busy for the caller to retry; this does not implement a queue.
- Acceptance records keep a bounded immutable prefix in an atomically replaced,
  checksummed local file. This deliberately substitutes bounded atomic file
  replacement for physical JSONL append; it creates no knowledge database.
- Execution prediction receipts similarly retain at most 64 records. Registration
  shares the event owner's lock, tries without waiting for contention, and
  persists before a later event can append. No new Execution state event is
  introduced; the receipt sequence anchors the immutable waiting event head.
  As with the existing Execution EventLog, one live writer owns an execution.
- A partial activation with no final model output is conservatively interrupted;
  a persisted final cognitive output can be finalized without another model call.
  Partial multi-step continuation and active prediction reconciliation still
  need to be assessed before claiming the entire design complete.

## Reference fidelity

Tycho commit: `f68912a764372ead0a610db2e1c011d41ce5197e`, Apache-2.0.
Source: `tycho/workspace/sandbox.py`, `tycho/agent/builder.py`,
`tycho/prompts/builder.system.j2`, `docs/ARCHITECTURE.md`.
Exact source/adaptation records will accompany copied implementation.

MetaWorld means Wenge Decitron's world model. Only public descriptions have
been verified, not its implementation or a reusable code license. SAO fields
and qualitative scenarios here are Lumina's adaptation, not a reproduction.
No numerical probability, equilibrium, causal-identification, or subjective
experience claim follows from these data structures.

## Validation so far

- P0 receipt tests: 5 passed; current Execution suite: 14 passed.
- Initial P1 cognition tests: 10 passed, including restart, evidence revision,
  exact request replay, source conflicts, atomic admission and Directive gating.
- Combined cognition / A / C / D / E0 / S0 regression: 138 passed.
- Real-model smoke, real Execution benefit, executable modeling and complete
  goal audit remain outstanding. These results do not establish production
  cognition or change historical E/W verdicts.

## First smoke: host endpoint selection failure

`cognition_minimal/protocol_smoke_1.json` preserves the actual request, failed
trace and original temporary script. One provider attempt produced no model
output and no accepted cognition. The temporary harness incorrectly used the
legacy `DEEPSEEK_BASE_URL` environment value, selecting `/v1/messages` instead
of the supported adapter's fixed `/anthropic/v1/messages`. HTTP status was not
captured; do not invent a 404 or classify this as model cognition failure.

The retry script now uses the exact fixed endpoint from `core/model_client.py`
and writes a separate `protocol_smoke_2.json`. The creator selected the corrected
retry. It made one call and returned 525 characters of valid JSON: two grounded
cognitive updates followed by `output.type=capability_request` asking to inspect
Execution. The current final-only envelope rejects that output and accepts no
cognition. This is a protocol/termination failure, not a size-limit failure.

The second artifact preserves that exact response and failed trace. No further
provider calls have occurred. A creator choice is pending between a unified
cognitive-step envelope (proposed updates remain uncommitted while acquiring
information) and retaining the strict split with clearer prompting. Either route
must keep actual requests replayable, preserve historical v1 prompts and traces,
and retain the two-call / one-read limit. Also make available read capabilities
explicit: the smoke's optional ExecutionObservation was absent, so advertising
inspect_execution without that fact would create a second avoidable failure.

Additional regressions: Conversation Memory 163 passed / 45 skipped; Dream
36 passed / 1 skipped. The first root run had 341 passed / 24 skipped and six
path-safety test failures because the test base directory was under source code.
The safety guard correctly refused that location. A corrected run uses a fresh
system temporary directory and passes: 347 passed / 24 skipped. The guard and
assertions are not changed. Upstream MAGMA status/diff are empty.

Both smoke artifacts retain the exact temporary script and its SHA-256. The
second response's basis quotes all match the visible source. No conclusion on
long-term cognitive or Execution benefit follows from this one rejected step.
The temporary smoke script was removed after preserving this evidence.

## Creator-selected unified protocol (v2)

The creator selected option 1. New activations use `cognitive_step` with
`updates` and `next`. A read request may carry provisional updates; the exact
first output remains in Trace and those candidates appear as `pending_updates`
in the second actual request. Final updates replace the provisional proposal,
not accepted state; only validated final updates are committed. NoChange remains
a valid final result. The limits remain two model calls and one read.

Available read capabilities are captured in the start event from actual host
inputs and become empty after the read. New prompt/projector versions are v2;
v1 prompts, traces and failed smoke artifacts remain unchanged. This subtask
touches the three existing cognition modules and extends its existing test file.
The next real-model smoke writes a new artifact, uses the fixed supported
DeepSeek endpoint and unchanged provider settings, and stops on its first failure.

Independent P3 component validation: `Mind/test_world_model.py` passed all seven
tests with Docker enabled (17.63 seconds), including actual isolated computation,
host-only verification, permissions/resource limits, output and memory rejection,
and timeout removal of a container with a live child. No provider calls were used.
Source attribution and frozen bounds are in
`cognition_minimal/ISOLATED_MODEL_SOURCE_AUDIT.md`. The component is not yet
exposed to the cognitive protocol and does not establish the parent goal complete.

## v2 regression setup failure (before tests ran)

After the v2 edits, the combined cognition/A/C/D/E0/S0 command used pytest's
default temporary root. It stopped on the first fixture setup with Windows
`PermissionError [WinError 5]` while enumerating the existing
`%LOCALAPPDATA%/Temp/pytest-of-wmywb` directory. No test body ran; this is not
evidence of a cognition assertion failure or a passed v2 regression.

Proposed correction: create a unique, previously nonexistent `lumina-mind-v2-<GUID>`
path directly under the system temporary directory, verify its resolved parent,
and pass it via `--basetemp`. This avoids accessing or altering the old pytest
directory. The creator subsequently instructed autonomous repair and validation,
pausing only for decisions that need confirmation; this supersedes the earlier
request to stop for every problem.

The corrected test run exposed an existing structural contract: the A host must
not discover authority using `callable/getattr`. New availability projection was
changed to the host's explicit Memory-or-None input; the boundary test was retained.
The selected cognition/A/C/D/E0/S0 command now passes 138 tests, including the new
provisional-update and original-v1-replay checks. Root regression passes 347 tests
with 24 skips. The pinned MAGMA status/diff remain empty.

## Real v2 smoke

`cognition_minimal/protocol_smoke_3.json` preserves the original harness source,
its hash, each actual provider request/output and both activation traces.
DeepSeek-V4-Pro made three calls (533, 682 and 1385 output characters). Both
activations were accepted, advancing revision 0 -> 1 -> 2. The first call proposed
two provisional items and requested Memory; the next request contained those exact
candidates plus an empty Memory observation, and the second call finalized them.
After closing/reopening the organ, new evidence caused the old explanation to be
archived, the prior question to close, and a new explanation/question to be kept.

This proves the selected protocol works in one real-model, synthetic-evidence
smoke and that accepted state survives organ reopening. It is not an independent
Execution-benefit test or an operating-system process-restart test. The quality
of the proposed discriminating observation still needs independent evaluation;
protocol acceptance does not certify causal reasoning. No v1 artifact was changed.

## Next minimal connection

Expose the already isolated model computation as an optional alternative to the
single read in a cognitive step; keep two model calls and one capability use.
Generated code and the pure-data initial state/actions belong only in that
capability request. Its response is COMPUTED, never reality evidence. A compact
model item retains the artifact reference, source basis, scope and unknowns, so
later activations can inspect or revise a model. Preserve v2 replay by introducing
a separate version for this capability. Changes remain in the three existing
cognition modules, reuse `world_model.py`, and extend the existing cognition test.
Independent checks, prospective registration/reconciliation and actual Execution
value remain outstanding after this connection.

The optional connection now exists through the explicit host flag
`MindOrgan(..., allow_model_computation=True)`, with projector/prompt v3. Default
activations retain v2. The single capability slot can read or compute, not both;
the public A host signature and earlier experiments retain their old behavior.
A model item attaches only an actual successful computed artifact. Simulation
output, including failure messages, is excluded from the reality-source map;
citing it as `activation:observation` cannot support an accepted belief.
`MindOrgan.verify_model` compares host-supplied later observations/outcomes with
the stored prediction without recomputation, state mutation or retroactive
prospective eligibility. It is not a runtime model capability.

Initial v3 regression: 140 passed / 1 explicit Docker skip. The integrated Docker
test then passed separately (6.26 seconds): a scripted Mind requested actual
isolated computation, retained its artifact, independently checked the service
trajectory, continued another activation, and a genuinely new Python process
reopened the same store and recovered revision/model ID/source digest. This
proves the connection and process persistence, not LLM model-building quality.
The subsequent safe-error projection preserves bounded failure codes, including
container cleanup failure, for host diagnosis; it never returns raw stderr.

After the safe-error projection, all 141 selected Mind regressions passed with
the actual Docker integration enabled. Root regression again passed 347 / 24
skipped. The first real v3 program-generation attempt exceeded the frozen output
limit; v4 adds compact-JSON/deferred-metadata instructions while retaining all
earlier prompt versions. Its selected regression passed 140 / 1 Docker skip.
The subsequent real v4 smoke executed two model programs and accepted revisions
1 and 2, but independent checks found prediction/projection errors and a changed
action sequence. See `cognition_minimal/MODEL_BUILD_SMOKE_TASK.md` for the exact
falsification and pending frozen-probe/verification-feedback correction.

Outstanding parent-goal work remains: effective model revision from independent
checks, owner-attested prospective prediction registration/reconciliation,
real Execution behavior/value, final authority review and relevant regression.
No Chat migration, emotion, autonomous goals, scheduler or Dream changes were made.
