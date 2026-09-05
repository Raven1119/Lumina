# Nervous event foundation

2026-09-05. The creator resumed Mind development and required Nervous event
infrastructure first. This supersedes the previous stop after Execution validation.
The subsequent scope instruction limits this work to Mind and Nervous: do not
connect Chat or Memory. No new Execution connection is included either.

Existing authority: `Lumina_Canvas/Lumina_Nervous_Organ.canvas` identifies an Event
Bus between organs and triggers; `docs/plan/MIND_DEFINITION_V1.md` defines the
minimal Nervous as a waiting-message queue. `docs/MIND_DESIGN.md` assigns mechanical
queueing/routing to Nervous and semantic judgment to Mind.

## First vertical slice

The hypothesis is that one small durable mailbox can carry organ request/result
events across interruptions without owning cognition, actions or organ storage.
Before this slice, the baseline was the manual host event exchange documented in
`Mind/docs/cognition_minimal/EXECUTION_VALIDATION.md`; no Nervous package existed.

Implement `Nervous/organ.py` and one focused test file. Reuse the existing local
atomic-file and native single-writer-lock pattern from `Mind/organ.py`; these are
persistence primitives, not a new memory algorithm. No external dependency or
generic callback/subscription framework is needed.

- Immutable data-only Event: stable ID, source, target, kind, bounded JSON data,
  optional causation ID referring to an existing event.
- `publish(event)`: persist before reporting acceptance; same ID/content is a
  duplicate, changed content is an identity conflict. No silent eviction.
- `pending(target, limit)`: bounded FIFO view; reading does not consume work.
- `complete(event_id, target, emitted=())`: atomically acknowledge one input and
  persist its result/request events. Emitted source must match the handling organ
  and causation must name the input. Exact retry is idempotent; changed retry fails.
- Reopening recovers pending work and exact completion receipts. Handler/provider
  failure before completion leaves input pending. Downstream organ operations
  still need their own stable IDs/receipts: this queue promises at-least-once
  delivery, not exactly-once external side effects.
- One live owner process, thread-serialized operations; other organs communicate
  through the host's data dispatch, not concurrent owners of the same files.
  Model-visible contexts never receive the mailbox or an Execution owner handle.
- Bound the prototype to 256 retained events, 32 KiB per serialized event,
  12 MiB file and 32 pending items per read. The event bound accommodates the
  existing Mind input's bounded Chinese evidence/goal/observation in UTF-8.
  Full history rejects new events; no
  automatic pruning, lost duplicate protection or unbounded memory growth.
- Use temporary synthetic state only. Do not migrate Chat, automate Dream, add
  timer policy, Focus, a worker/service, or background model invocation.

## Evidence required

Validate actual process restart, immutable payloads, FIFO routing, duplicate and
conflicting publication/completion, request/result causation, failure before/after
durable replacement, pending input after handler failure, competing writer
rejection, boundedness and corruption rejection. A result event is not evidence
that its claimed content is true; Memory/Execution remain the evidence owners.

Then connect Mind's deferred capability request/result boundary to this mailbox.
The foundation test alone does not complete the broader Mind development goal.

## Foundation evidence

Five focused tests passed in 0.92 seconds. They include an actual child Python
process committing a result then exiting with `os._exit`, reopening pending FIFO
messages, native writer exclusion, immutable nested data, JSON identity distinguishing
true/1/1.0, all-or-none acknowledgement/outbox persistence, lost-response recovery,
capacity limits and corrupted-file rejection. The first test run exposed a test
setup collision (duplicate emitted IDs obscured the intended bad-cause branch);
using distinct IDs exercises the intended validation without changing assertions.

## Mind request/result integration

Keep the existing standalone A/E historical runner and prompt versions replayable.
Current Mind must return a durable pending request instead of holding a Memory
facade. A result is correlated to that exact request; invalid or changed duplicate
results must not enter a second model call. Preserve two model calls and one
capability per activation. Record the result before continuing inference; an
uncertain interrupted inference must not be blindly repeated. Narrow trace reopening
is allowed only at the capability boundary. The explicit host dispatches through
Nervous; no generic handler registry or background worker is introduced.

This boundary is now implemented in `Mind/organ.py` and the explicit
`Mind/host.py` adapter. `MindOrgan` no longer stores/calls a Memory facade;
non-None `memory_retriever` is rejected. Read capability names may be declared
for synthetic result-event tests; no Memory handler is connected. The original
standalone A/E experimental host retains its supported historical behavior.

The current explicit flow is:

```text
activation_event(MindInput) -> Nervous.publish
-> run_mind_once -> Mind.activate
-> atomic input acknowledgement + mind.request event
-> optional run_computation_once (isolated pure computation only)
-> atomic request acknowledgement + mind.result event
-> run_mind_once -> Mind.accept_result
-> cognitive commit + atomic result acknowledgement / mind.receipt event
```

`mind`, `mind.requests`, `mind.results` and `host` are logical mailbox addresses.
The separate result mailbox prevents queued new activations from blocking the
reply that completes current cognition. The host checks results first and handles
at most one event per explicit call. This is not a timer or full Focus policy.
Event source/target fields describe routing; they are not authentication or an
attestation that the data is true. Actual evidence remains owner-controlled.

## Persistence and failure contract

- A pending Mind request reconstructs from the durable request trace; a first
  raw model output persisted just before request append can also reconstruct
  the same request without another model call.
- `accept_result(MindResultEvent)` validates exact request identity, observation
  schema, fixed probe inputs and source snapshot before recording receipt.
  Canonical JSON comparisons distinguish true, 1 and 1.0.
- The Mind journal now writes version 2, retaining version 1 reads and unchanged
  historical prompt/projector versions. At most one `result_received` record per
  activation increases the record ceiling from 128 to 192; the 64-activation,
  two-call, one-capability and 4 MiB ceilings remain unchanged.
- The result receipt is durable before continued inference. On recovery, a
  persisted final output can be committed without resampling. An interrupted
  inference with no recorded output fails conservatively as `interrupted`.
  Its received result stays in the journal; no third model call is issued.
- Mind may commit before Nervous acknowledgement. Re-delivery returns the same
  accepted outcome and the host normalizes duplicate status in its stable
  receipt event. It does not repeat model work or silently lose the output.
- An invalid event/result raises before inference and remains pending for host
  diagnosis. A valid failure result terminates the activation conservatively.
  No automatic dead-letter policy or new retry scheduler is implemented.
- Pure isolated computation may repeat if its host dies before committing its
  result event. It has no reality authority or user-workspace access. Nervous
  deduplicates committed request/result delivery; it does not promise exactly-once
  side effects in an unconnected external service.

## Public entry and validation

The maintained entry points are `NervousOrgan.publish/pending/complete`,
`MindOrgan.activate/accept_result/inspect`, and the three explicit host helpers:
`activation_event`, `run_mind_once`, `run_computation_once`. Callers own when they
invoke a step. `result_event` supports a correlated synthetic/host reply.
No provider is created by these helpers: supply the existing configured model
to Mind, or a deterministic test model.

Run focused verification from the repository root:

```powershell
$env:PYTHONPATH = (Get-Location).Path
$nervousTestPath = Join-Path ([IO.Path]::GetTempPath()) ('lumina-nervous-' + [Guid]::NewGuid().ToString('N'))
& ./.venv/Scripts/python.exe -m pytest Nervous/test_events.py Mind/test_cognition.py -q -p no:cacheprovider --basetemp $nervousTestPath
```

Current evidence:

- 173 selected Mind/Nervous/A/C/D/E0/E1/S0 regressions passed, one explicit Docker
  skip (8.02 seconds). Includes new result identity, write-failure, interrupted
  inference, raw-output recovery, queue/Mind commit-gap, and actual process-exit
  tests. Forty queued new inputs did not obstruct the result after process restart.
- Two focused actual Docker integrations passed (1.23 seconds), including the
  complete Nervous -> Mind -> isolated computation -> Nervous -> Mind path and
  independent prediction checks. No LLM provider was called in these tests.
- 74 additional E2/E3/E4/computation regressions passed / four Docker skips.
- Root regression: 347 passed / 24 skips. Existing Execution source hashes match
  the prior validation artifact; pinned MAGMA status/diff stayed empty.
- After the final canonical context-identity check, the focused cognition suite
  passed 29 tests / one explicit Docker skip (1.12 seconds).
- `git diff --check` and explicit whitespace checks cover tracked and newly
  created files. Prior real-model artifacts and their verdicts remain unchanged.

This completes the currently scoped Mind/Nervous event foundation and bounded
cognitive request/result loop. It does not claim production Chat integration,
Memory wiring, autonomous scheduling, emotion, endogenous goals, Execution
behavioral benefit or owner-attested prospective prediction orchestration.
