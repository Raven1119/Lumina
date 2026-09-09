# Nervous event contract

Nervous owns durable transport and mechanical foreground continuation between
Mind and Execution. It does not interpret goals, select business actions,
approve conclusions or own another organ's state.

## Current entry

`NervousOrgan(directory, limits=..., transport=...)` preserves a single-writer
mailbox and shared provider ledger. Public mechanics are:

- publish an immutable Event;
- pending(target) in retained event order;
- complete(event_id, target, emitted=...) atomically;
- submit original user text to Mind;
- run(mind, execution) for bounded foreground delivery/continuation;
- status(...) and explicit extend_budget(...) preserving prior spend.

The fixed logical addresses are mind, mind.results, mind.analysis and execution.
These are organ interfaces, not a plugin registry or background scheduler.
CLI composition is in Mind/cli.py.

## Identity and causal delivery

Each Event has event_id, source, target, kind, inert data and optional
causation_id. Same identity with different content is rejected. Completion
requires the actual recipient; every emitted event must name that recipient
as source and the consumed event as cause. Repeating the same completion is
idempotent; a different emission set is rejected.

User submission identity is separate from text. `submit(text, event_type,
submission_id=...)` uses the caller's identity for an idempotent transport retry;
without an identity every invocation creates a new event. Identical text in two
submissions is still two events. Reusing an identity with different text/type
is rejected, including after completion and restart. Existing event publication
and receipts provide this guarantee; there is no separate deduplication store.

An organ persists its handling receipt before Nervous acknowledges it.
Publication after a crash repeats the same event, not the action. Execution
owns its outbound event cursor; Mind owns pending cognitive continuations.

## Fixed flow

| Event | Owner response |
| --- | --- |
| user.input -> mind | Interpret original user authorization; request current evidence. |
| execution.inspect -> execution | Read-only attributed snapshot, no action. |
| execution.snapshot -> mind.results | Begin/continue the pending cognitive activity. |
| evidence.read -> execution | Exact immutable source result or explicit capacity response. |
| evidence.result -> mind.results | Continue the same activity without treating a directory as reality. |
| analysis.request -> mind.analysis | Independent bounded analysis of selected copies. |
| analysis.result -> mind.results | Mind receives compact analysis and decides. |
| mind.decision -> execution | Bind original guidance or accept NoChange at a still-applicable position. |
| prediction.watch -> execution | Register a declared future observation, never an invented observation. |
| execution.changed -> mind | Important committed request/result or declared observation change. |

Ordinary action results stay inside Execution. Its advance returns at a known
safe boundary; Nervous can service pending work then continue. This handoff is
not a business Wait or a fabricated owner event. Declared observation watches
also run when no Execution Actor/Run exists: NoChange does not cancel a forecast.
A later foreground resume can publish changed observation evidence to Mind.
With no pending information
and an externally waiting/completed Execution, the loop is quiet.

## Recovery and bounds

Current retained limits are 256 events, 32 KiB per event, 12 MiB mailbox and
240 foreground loop steps. There is one foreground writer, not concurrent
distributed delivery. The mailbox is integrity checked and atomically replaced.

Provider dispatch/accounting is mechanical: all roles share a persisted
call/output/request allocation. Explicit extensions add capacity without
resetting costs. Unknown provider/action outcomes are not automatically retried.
Cognitive failure remains an unresolved Mind obligation; transport completion
does not certify that understanding was updated.

This core has no production Chat/Memory/Dream integration. Older experimental
host targets and versioned replay branches are retired.
