# Lumina Final Goal

Lumina is intended to become a local-first companion whose continuity is earned
through durable, inspectable behavior rather than simulated by tone alone.

The product promise is:

> Preserve what happened before compressing it, transform preserved experience
> into memory through an explicit durable process, and use recalled context
> without making ordinary conversation depend on memory availability.

## Continuity Invariant

Cold-first preservation remains non-negotiable:

```text
Hot Draft
-> durably preserve older raw turns in Cold Draft
-> advance logical compaction state
-> retain the recent raw tail for the next response
```

If preservation fails, the logical Hot view must remain uncompacted. Cold Draft
source records remain the immutable authority for conversation material that has
left the immediate prompt.

Stable per-turn identity, aware UTC time, source IANA timezone, and truthful
timezone source must survive Draft persistence, compaction, Dream ingestion,
MAGMA persistence, Recall projection, and model-context injection.

## Current Baseline

The first end-to-end continuity loop now exists:

```text
Production chat
Browser -> FastAPI -> MessageRuntime
        -> optional bounded Recall
        -> ModelClient
        -> Hot Draft -> Cold-first compaction -> Cold Draft

Memory formation
manual Dream -> pending Cold Draft -> Conversation Memory
             -> unmodified MAGMA -> durable checkpoint -> consumed

Memory use
query -> bounded Lumina-owned Recall -> rendered MemoryContext
      -> optional model-context injection -> ordinary response path
```

This baseline includes:

- restart-persistent Hot and Cold Draft state;
- Cold-first compaction and immutable source preservation;
- native per-turn provenance;
- manual bounded Dream ingestion;
- durable idempotent MAGMA persistence;
- dense and lexical RRF anchors;
- temporal hard constraints;
- fixed and adaptive graph traversal;
- deterministic intent-aware Context Linearization;
- optional, bounded, failure-isolated production Recall injection.

The memory execution boundary is now sufficiently complete for its current
role. Unverified query-time temporal ranking was deliberately not invented, and
the current upstream GENERAL fallback was retained after controlled evaluation.

## Next Production Objective

The next objective is not a new memory algorithm. It is a safe, minimal product
control for the existing manual Dream operation:

```text
browser Dream button
-> bounded synchronous POST /api/dream/run
-> existing DreamRunner.run_once
-> shared app-owned Cold Draft and memory backend
-> memory-complete-before-consumed
-> current Chat Recall immediately sees the new memory
```

This step must also establish one process-local writer boundary shared by Chat
and Dream. A browser button without backend mutual exclusion would make the
system easier to operate while weakening continuity, which is unacceptable.

The first version should provide only:

- one compact Dream maintenance row in the existing native frontend;
- bounded pending-segment status without exposing source text;
- truthful `running` and Recall-enabled status;
- one synchronous manual-run action;
- aggregate results using the existing Dream terminology;
- stable busy and unavailable responses;
- single-worker, no-reload, single-writer deployment guidance.

It should not add background execution, scheduling, progress streaming, task
queues, Dream history, memory editing, graph visualization, strategy controls,
or autonomous behavior.

## Growth Rule

Later capabilities must extend rather than bypass these boundaries:

1. Cold Draft remains the durable authority for raw material leaving Hot
   context.
2. Dream consumes only eligible preserved segments and marks them consumed only
   after memory completion is durable.
3. Retry converges through `(segment_id, ingestion_version)` without duplicate
   logical memory.
4. Recall remains bounded, provenance-preserving, optional, and safe to ignore
   on failure.
5. User-facing controls must share the same owners and write boundaries as the
   underlying runtime; they must not create parallel stores or split memory
   views.
6. Future automation may replace manual triggering only after its scheduling,
   interruption, persistence, and audit semantics are explicitly designed.
7. New storage, reasoning, and autonomous systems must not weaken continuity,
   truthful provenance, idempotency, or whole-system coherence.

Conversation Graph as a separate organ, Evidence Organizer, Mind System,
PostgreSQL memory, ContextBuilder, ToolRuntime, autonomous Dream, agents, tasks,
and schedulers are future capabilities, not implications of the current step.

The active Cold Draft contract is defined in `docs/COLD_DRAFT.md`. Current
implementation facts belong in `docs/CURRENT_STATUS.md`. The verified integration
boundary for the next step is recorded in `docs/DREAM_UI_CODE_AUDIT.md`.
