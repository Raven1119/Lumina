# Dream Cold Draft Digestion

## Status

Implemented production contract.

Dream is Lumina's explicit, synchronous, bounded orchestration path from
complete pending Cold Draft segments into the current Conversation Memory
backend. It is not an automatic background process.

## Entrypoints

### Browser / HTTP

The normal manual path is:

```text
Run Dream button
-> POST /api/dream/run
-> app-owned DreamRunner.run_once(DreamRunPolicy())
```

The HTTP client cannot supply policy fields. Current defaults are:

```text
max_segments = 10
stop_on_error = false
ingestion_version = grounded-formation-v2  # configured real-model app
```

### CLI

The developer CLI remains available:

```bash
python -m Dream.runner
python -m Dream.runner --max-segments 10
```

Because it constructs an independent Cold/memory owner, use the CLI only while
the application service is stopped. Do not run it concurrently with the
browser service.

### Explicit first-hit configuration

`python -m Dream.runner --first-hit` opts newly started Formation v2 windows
into local first-hit semantic linking. Python assembly may instead pass
`first_hit=FirstHitPolicy(), cold_store=owner` to the existing
`RealMemoryIngestorProvider`. The same adapter then exposes associative Recall
and optional bounded source expansion through that Cold owner. The CLI enables
a 32-segment/1 MiB recent source window; its existing path settings still apply.
Default v2, original Recall, manual trigger and HTTP policy remain unchanged.

The existing ingestion store freezes the new profile and link plan before graph
writes; new combined ingestion completes only after those edges are durable.
Failures leave Cold pending. Old v2 checkpoints without the new reservation
retain their original obligations. No new scheduler, backend, model call or
consumer flow is introduced. See the
[first-hit contract](../../Conversation_Memory/docs/FIRST_HIT_MEMORY.md).

## Application wiring

`create_app()` constructs one in-app Dream runner using:

- the app-owned `ColdDraftStore`;
- the same `MagmaMemoryAdapter` and resident backend used by Chat Recall;
- the same process-local nonblocking writer mutex used by complete Chat
  operations.

This ensures successful Dream writes are immediately visible to the resident
retriever without restarting the service.

Busy Chat/Dream conflicts return stable `409 Conflict`. Dream unavailable at
startup returns safe `503` from the explicit Dream endpoint while normal Chat
remains usable.

## Source format

Cold Draft is stored at:

```text
data/draft/cold_drafts.jsonl
```

Each original source turn occupies one `record_type="cold_turn"` JSONL line.
Lines in one logical segment share:

```text
segment_id
segment_turn_count
segment_created_at
source
state
```

and contain continuous `segment_turn_index` values from `0` to
`segment_turn_count - 1`.

`ColdDraftStore` validates and reconstructs a complete logical segment before
Dream sees it. Dream never processes individual lines as separate jobs.

- 14 `cold_turn` lines in one segment = 1 pending segment;
- `count_pending_bounded()` counts valid complete pending segments, not lines;
- `mark_consumed(segment_id)` atomically updates every line in the segment;
- malformed or incomplete groups are not listed, counted, ingested, or
  consumed.

Cold source turn text, IDs, order, timestamps, timezone, role, and provenance
remain unchanged across the state transition.

## Data flow

```text
explicit trigger
-> DreamRunner.run_once(policy)
-> ColdDraftStore.list_pending_page(limit)
-> complete logical Cold segment
-> ColdDraftSegmentConverter
-> bounded DeepSeek-V4-Pro extraction of facts and full-window mentions
-> extraction checkpoint, then batch proposition/role/identity verification
-> verified batch and stable bindings checkpointed in Memory
-> MemoryIngestor.ingest(ColdDraftSegment)
-> MAGMA graph/vector persistence
-> ingestion checkpoint completed
-> validate complete IngestionResult
-> ColdDraftStore.mark_consumed(segment_id)
-> DreamRunReport
```

Dream passes the bounded source segment to the adapter. Configured real-model
writes use DeepSeek-V4-Pro in non-thinking mode with a local 8192-token output
budget. One extraction and one verification call process a healthy new batch;
the raw response is checkpointed before parsing. Invalid candidates isolate
their dependencies while independent verified results persist. Pending
processing issues keep the parent segment pending. A later explicit retry may
perform one checkpointed source-ref repair and verify only its new subset.
Facts and independent
mentions must both be durable before completion, including zero-fact windows.
Retries reuse successful stages. Mock/legacy adapters retain deterministic
`grounded-span-v2`; either path may produce `0..M` memory IDs. See the
[current entity-memory contract](../../Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md).

## Policy and ordering

- Processing is serial.
- Default selection follows the Cold owner's persisted round-robin cursor in
  source file order, wrapping at most once per page without duplicates.
- `max_segments` bounds one run.
- After each actual default attempt, including failure, the owner saves the
  selected record's cursor. Ten pending failures cannot starve a healthy
  eleventh segment: the next explicit run starts there, including after restart.
  Failures stay pending; selection does not assert ingestion completion.
- `stop_on_error=True` is available only to direct Python/CLI callers. It uses
  the original pending prefix, stops on failure and leaves the cursor unchanged.
- A cursor write failure stops further attempts and sets the internal report's
  `progress_saved=false` while preserving actual segment outcomes. HTTP reports
  the existing safe Dream failure; CLI exits unsuccessfully. Already completed
  ingestion/consumption is retained and later retries remain idempotent.
- Cold still reconstructs its complete file, and each cursor update atomically
  replaces that file. Only page output and digest attempts are bounded by
  `max_segments`; overall I/O is not `O(max_segments)`.

Segment results project the adapter's `retryable` flag: unfinished provider,
verification, eligible repair or persistence work can resume; saved bad output
or exhausted/no eligible repair cannot advance under the same protocol. Neither
kind is blacklisted or consumed on failure. A later page can still reach them,
and later corrected source need not delete the old checkpoint. No timer,
background worker, automatic run or additional model call is introduced.

## Completion and consumed transition

A Cold segment is consumed only when the memory result:

- refers to the same segment and ingestion version;
- is durably `completed`;
- returns a tuple of non-empty memory-ID strings; an empty tuple is valid;
- has passed the adapter's durable validation boundary.

Dream does not compare memory count with source-turn count.

Only then may Dream ask the Cold owner to change the segment from
`pending_digest` to `consumed`.

## Idempotency and recovery

Durable key:

```text
(segment_id, ingestion_version)
```

Recovery behavior:

```text
memory incomplete
-> retry ingestion
-> keep Cold pending
```

```text
memory completed
-> Cold consumed transition failed
-> rerun recognizes completed memory
-> retry consumed transition only
```

```text
segment already consumed
-> skip safely
```

Stable evidence IDs and checkpoints prevent duplicate logical memory across
retry and restart.

## Failure behavior

- No pending segment returns a successful zero-attempt report.
- Malformed/incomplete Cold segments are not consumed.
- Conversion or memory failure leaves the segment pending.
- One segment failure is isolated by default.
- Unexpected exceptions are converted to safe structured failures.
- Dream failure does not mutate or break the normal Chat path.
- The writer lock and `running` status are released/reset in all success and
  failure paths.

## Public result boundary

HTTP responses expose aggregate fields only:

```text
attempted
ingested
consumed
skipped
failed
```

Do not expose per-segment text, segment IDs, memory IDs, graph/vector details,
MAGMA UUIDs, scores, provider configuration, local paths, raw exceptions, or
tracebacks.

## Deployment boundary

Current safe service assumptions:

```text
one process
one Uvicorn worker
no --reload
no concurrent external Dream CLI
```

A process-local mutex does not provide multi-worker or cross-process safety.

## Explicit non-features

Dream currently does not provide:

- automatic, scheduled, background, startup, compaction-triggered, or
  model-triggered execution;
- reflection, abstraction, pattern extraction, salience updates, forgetting,
  contradiction resolution, fact supersession, or memory rewriting;
- parallel ingestion, cancellation, progress streaming, queueing, or history;
- changes to Recall or upstream MAGMA.

## Validation

```bash
python -m pytest Dream/tests -q
python -m pytest Conversation_Memory/tests -q
python -m pytest -q
python -m scripts.recall_e2e_test
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
git -C Conversation_Memory/upstream/MAGMA diff --stat
```

Use temporary stores and synthetic data. Do not read or commit real user Draft
or memory data.
