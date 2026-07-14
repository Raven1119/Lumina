# Cold Draft Contract

This document restores the original Hot Draft / Cold Draft design into the new
MVP and reconciles it with the code that exists now. It is the active authority
for the Draft boundary.

Historical sources were the earlier `DRAFT_SYSTEM_V1_DESIGN.md`,
`HOT_DRAFT_COMPRESSION_BOUNDARY_DESIGN.md`, and the Draft System section of
`MEMORY_ORGAN_FULL_SHAPE.md`. Their Cold-first preservation rule is retained;
their unimplemented future-organ assumptions are not active in this MVP.

## Core Rule

> Hot Draft may be compressed. Cold Draft preserves the pre-compression raw
> material. Nothing is compressed in Hot Draft until the corresponding raw
> segment is safely stored in Cold Draft.

Cold Draft is not a rollover backup of the whole Hot Draft file. It stores the
older, chronological user/assistant segment selected for logical compression.

## Hot Draft

Hot Draft is the live conversation buffer.

It:

- records user and assistant/fallback turns in append-only JSONL;
- supplies prior conversation context to `ModelClient`;
- remains automatic runtime infrastructure rather than a model-selected tool;
- retains recent raw turns after logical compaction;
- survives process restart.

It does not classify durable facts or become long-term memory.

## Cold Draft

Cold Draft is the durable preservation layer for raw turns leaving the hot
context window.

It:

- stores complete user/assistant pairs in original order;
- uses an opaque internal `segment_id`;
- records `created_at`, `source`, `turns`, and `state`;
- begins in `pending_digest` state;
- may be marked `consumed` through the internal store interface;
- survives process restart;
- never enters ordinary model context or the browser UI.

`pending_digest` may now be consumed only by the bounded developer-triggered
Dream command documented in `Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`. There is
still no chat-time ingestion, background consumer, startup hook, scheduler, or
autonomous long-term memory process.

## Current Chat Flow

The new MVP uses one synchronous path:

```text
read previous logical Draft context
-> call ModelClient
-> construct the public response
-> append the user turn to Hot Draft
-> append the assistant or fallback turn to Hot Draft
-> evaluate compaction once
-> return the already-constructed response
```

The current user message is passed directly to the model and is not duplicated
inside `recent_context`. Prior Draft context is read before model generation.

## Cold-First Compaction

Compaction runs after the response pair has been captured.

1. Read raw Hot Draft turns.
2. Skip while the raw turn count is at or below the configured threshold.
3. Exclude turns already covered by compaction state.
4. Keep the configured recent raw tail.
5. Shrink the selected older prefix to complete user/assistant pairs.
6. Derive a stable segment identity from the prefix offset and selected turns.
7. Append that segment to Cold Draft.
8. Only after the append succeeds, atomically replace the logical compaction
   state.

Default constructor values are:

```text
max_raw_turns_before_compression = 24
retain_recent_raw_turns = 12
```

The trigger is strictly greater than the threshold. With ordinary complete
turn pairs, the first default compaction therefore occurs after the thirteenth
chat response has been captured.

## Logical Context After Compaction

The physical Hot Draft JSONL remains append-only. Compaction is a logical view
implemented by a separate state file.

The model-facing context becomes:

```text
zero or more deterministic preservation markers
+ recent uncompressed user/assistant turns
```

Each marker has the fixed form:

```text
[Compressed conversation segment preserved in Cold Draft: N turns.]
```

It is represented as an assistant role/text item for compatibility with the
minimal model-input contract. It is not an LLM summary and contains no raw
conversation text or internal segment ID.

## Failure Semantics

### Cold Draft append fails

- do not advance compaction state;
- keep all raw Hot Draft turns logically visible;
- return an internal `cold_draft_failed` result;
- do not change the public chat response;
- do not expose the exception or storage path.

### Compaction state write fails after Cold Draft succeeds

- keep the already-preserved Cold Draft segment;
- do not claim logical compaction succeeded;
- reuse the same stable segment identity on retry;
- do not duplicate the Cold Draft record when content and identity match.

The policy is preservation before compactness. Safe retention is preferred to
silent loss.

## Storage Files

The default local files are:

```text
data/draft/hot_drafts.jsonl
data/draft/cold_drafts.jsonl
data/draft/hot_draft_compaction_state.json
```

They are private runtime data and ignored by Git. `.env.local` and Draft data
must not be deleted during code or documentation maintenance.

## No-Leak Boundary

Cold Draft records, logical state, and compaction results are internal only.
They must not appear in:

- `/api/chat` or `/api/status` responses;
- the browser DOM or JavaScript state;
- model-visible recent context, except for the fixed preservation marker;
- user-facing errors or logs.

Never expose API keys, provider bodies or URLs, `.env.local`, absolute paths,
tracebacks, raw JSON dumps, internal IDs, or raw Cold Draft segments.

## Implemented Guarantees

The current tests prove:

- append, pending-list, consumed-state, and restart behavior;
- malformed-record tolerance and input validation;
- stable-ID idempotency and conflict rejection;
- no compaction below threshold;
- complete-pair preservation and recent-tail retention;
- Cold-first failure safety;
- retry after state-write failure without duplicate segments;
- restart recovery of logical context and compaction state;
- unchanged, no-leak chat responses and frontend/API coexistence.

## Known MVP Limits

- Hot Draft is physically append-only and is not truncated by compaction.
- Preservation markers accumulate; the raw tail is bounded, but the total
  marker count does not yet have a global cap.
- The marker records preservation only; it carries no semantic summary.
- Only the manual Dream command consumes `pending_digest` segments; there is no
  automatic or chat-time consumer.
- The JSONL files and state file do not provide a multi-process transaction or
  cross-process writer lock.
- Cold Draft is not searchable, model-visible, or a long-term memory system.

These limits must remain visible. They cannot be described as completed memory
features.

## Non-Goals

This contract does not authorize further memory-graph or recall integration,
query routing, ContextBuilder, ToolRuntime, autonomous/background Dream,
background workers, LLM summarization, a Cold Draft viewer, or a new root
dependency. The separately authorized manual Dream command is limited to the
durable ingestion and consumed transition described above.
