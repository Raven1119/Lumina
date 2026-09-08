# AGENTS.md

## Scope

This file applies to all work under `Dream/`.

Dream is Lumina's explicit, synchronous, bounded Cold-to-Conversation-Memory
orchestration layer. It is implemented and available through:

```text
browser button -> POST /api/dream/run -> app-owned DreamRunner
```

and, only while the application service is stopped:

```text
python -m Dream.runner
```

Dream is not part of the synchronous `/api/chat` business flow and never runs
automatically.

## Authority

Also obey:

- root `AGENTS.md`;
- `Conversation_Memory/AGENTS.md`;
- `docs/COLD_DRAFT.md`;
- `docs/CURRENT_STATUS.md`;
- `Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`;
- `Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md`.

## Current application boundary

Reuse the existing application service boundary:

```python
DreamRunner.run_once(policy: DreamRunPolicy) -> DreamRunReport
```

The in-app runner is constructed once by `create_app()` and reuses:

- the app-owned `ColdDraftStore`;
- the same `MagmaMemoryAdapter` / backend used by the resident Chat retriever;
- the same process-local writer mutex used by Chat.

Do not call `build_default_runner()` for each HTTP request and do not create a
second Cold owner or a second resident MAGMA backend.

## Current data flow

```text
explicit Dream trigger
-> bounded complete pending segments from ColdDraftStore
-> ColdDraftSegmentConverter
-> one bounded DeepSeek-V4-Pro Formation call in non-thinking mode with
   max_tokens=2000 when a real model is configured
-> validated units checkpointed before MemoryIngestor MAGMA writes
-> durable graph/vector persistence and ingestion checkpoint
-> verify complete IngestionResult
-> ColdDraftStore.mark_consumed(segment_id)
-> safe aggregate DreamRunReport
```

Cold is physically one `cold_turn` JSONL record per source turn, but Dream works
on reconstructed complete logical segments. Fourteen lines in one segment are
one Dream job, not fourteen jobs.

## Current policy

The HTTP policy is fixed and not client-configurable. A configured real-model
app uses the shared adapter's Formation version:

```text
max_segments = 10
stop_on_error = false
ingestion_version = grounded-formation-v1
```

Mock/legacy injected adapters retain `grounded-span-v2`. Execution is serial
and deterministic. One segment failure does not block later
segments unless `stop_on_error=True` is explicitly used by a direct Python/CLI
caller.

## Responsibilities

Dream owns:

- explicit run orchestration;
- bounded deterministic segment selection;
- per-segment failure isolation;
- conversion to current Conversation Memory DTOs;
- verification of memory completion before consume;
- safe aggregate reports.

Dream does not own:

- Hot Draft compaction or summarization;
- Cold JSONL parsing rules or state transitions;
- temporal parsing or entity extraction;
- graph/vector persistence internals;
- memory idempotency;
- Recall algorithms.

Reuse the existing owners.

## Cold Draft rules

- Read only complete `pending_digest` segments reconstructed by the Cold owner.
- Never read or modify rolling Hot summary state.
- Never edit Cold JSONL directly.
- Never rewrite source turn text, order, IDs, timestamps, role, or provenance.
- Mark the whole segment consumed only after memory completion is durable.
- Ingestion failure, unavailable memory, malformed source, partial result, or
  timeout leaves the segment pending.
- Consumed transition updates all lines in a segment atomically.

## Idempotency and recovery

Use the Conversation Memory key:

```text
segment_id + ingestion_version
```

Required recovery windows:

```text
memory incomplete -> retry ingestion -> do not consume
memory completed + consume failed -> retry consume only
already consumed -> skip safely
```

Dream must not create a competing source of truth for memory completion.

## Writer and deployment boundary

- Complete in-app Chat and complete in-app Dream runs share one nonblocking
  process-local mutex.
- Busy requests return stable `409 Conflict`.
- Current supported service mode is one process, one worker, no `--reload`.
- Do not run the external Dream CLI concurrently with the service.
- Multi-worker or cross-process safety is not implemented.

## Safe output boundary

Public Dream status and result objects may expose aggregate counts only. Do not
expose:

- Cold turn text or segment IDs;
- memory IDs, MAGMA UUIDs, graph/vector details, or scores;
- local paths, provider data, credentials, raw exceptions, or tracebacks.

## Not authorized by default

Do not add:

- automatic, scheduled, startup, compaction-triggered, or model-decided Dream;
- background jobs, workers, queues, WebSocket/SSE progress, or cancellation;
- LLM reflection, abstraction, pattern mining, salience mutation, forgetting,
  contradiction handling, fact supersession, or memory rewriting;
- multi-granularity redesign;
- parallel ingestion;
- chat-time memory writing;
- changes to Recall or upstream MAGMA.

## Testing

Use temporary production-format Hot/Cold stores and fake memory backends for
failure tests. Use the real pinned MAGMA only in the isolated project
environment and marker-owned E2E paths.

Choose relevant tests for the affected behavior under the root validation
rules. Ordinary fixes may use focused cases; Markdown-only changes use static
checks. Broader Dream regression uses:

```bash
python -m pytest Dream/tests -q
git diff --check
```

For changed integration contracts, include the affected Memory and API tests.
Changes affecting Recall behavior also require the existing real-MAGMA E2E
through the environment and isolation rules in
`docs/RECALL_E2E_ACCEPTANCE.md`. Check upstream status when working on that
integration; pinned MAGMA must remain unchanged.
