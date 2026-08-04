# Cold Draft Contract

This is the active authority for Lumina's Hot Draft / Cold Draft boundary.

## Core rule

Hot Draft may be semantically compressed only after every raw turn leaving the
live window has been durably preserved in Cold Draft. Hot Draft is live rolling
context. Cold Draft is immutable source evidence for Dream; it is not MAGMA and
is not queried by Recall.

## Hot Draft

The physical hot_drafts.jsonl contains zero or one rolling summary record plus
recent raw user/assistant turns. New raw turns preserve turn ID, role, text,
aware UTC creation time, source IANA timezone, and truthful timezone source.

Defaults are:

    max_raw_turns_before_compression = 24
    retain_recent_raw_turns = 12

The trigger is strictly greater than 24. With complete pairs, 26 raw turns cause
the first compaction: the oldest 14 are archived and 12 remain.

On later compaction, the previous summary and only that round's selected raw
turns are synchronously sent to the configured model. The new summary replaces
the previous summary. Old summaries do not accumulate as records, but the old
summary participates in the next generation. A summary never enters Cold.

Model-facing Draft context is the current rolling summary, when present,
followed by recent raw turns. Hot is no longer physically append-only after
compaction: it is atomically replaced by the updated one summary plus raw tail.

## Cold physical format

cold_drafts.jsonl stores one original turn per JSONL line. Each cold_turn line
contains the raw turn fields and:

    record_type, schema_version, segment_id
    segment_turn_index, segment_turn_count, segment_created_at
    source, state
    role, text
    native V2 provenance when present
    consumed_at when consumed

All lines from one compaction share one stable segment_id. Indexes are
continuous from zero through count minus one. Count, schema, source, segment
creation time, state, and consumed time agree. Original order, content, role,
timestamp, ID, and provenance are preserved.

The store groups lines by segment_id, validates completeness, orders turns by
index, and reconstructs the existing aggregate segment with a turns list.
Dream still receives one logical segment per compaction. Fourteen lines are one
pending segment, not fourteen Dream jobs. Bounded pending status counts complete
segments, not lines.

Incomplete, duplicate-indexed, mixed-state, conflicting-metadata, or invalid
provenance groups are not returned to Dream, counted, or consumed.

The former one-line nested turns-array format is not read, migrated, or
supported in parallel. Existing old-format bytes are not rewritten merely by
reading the store and remain unrelated raw bytes during atomic rewrites.

## Atomic Cold operations

Appending one segment is an all-or-nothing replacement:

1. Read the current file while preserving unrelated and malformed bytes.
2. Construct the complete file with every new cold_turn line.
3. Write a unique temporary file in the same directory.
4. Flush and fsync it.
5. Atomically replace cold_drafts.jsonl.

Failure leaves old bytes unchanged and cleans the temporary file. Identical
stable-ID append is idempotent, including after consumption; incomplete or
conflicting reuse is rejected.

mark_consumed performs one atomic full-file rewrite. Every line in the target
segment becomes consumed with the same aware consumed_at. A segment cannot be
partly pending and partly consumed; unrelated valid and malformed lines remain.

## Cold-first compaction

After a response pair is persisted:

1. Read the current summary and raw Hot tail.
2. Return not_needed below threshold or without a complete pair boundary.
3. Set read-only is_running to true.
4. Summarize the old summary plus only the selected raw prefix.
5. Atomically preserve that raw prefix as one Cold segment.
6. Atomically replace Hot with the new summary plus recent raw tail.
7. Update recovery state.
8. Restore is_running to false in every exit path.

is_running stays false for not_needed and covers summary generation, Cold
preservation, Hot replacement, and recovery-state update.

Summary failure changes neither file. Cold failure does not change Hot. If Hot
replacement fails after Cold succeeds, retry reuses the stable segment ID.
Recovery-state failure after the authoritative Cold and Hot writes does not
undo the completed transition.

## Status and browser behavior

GET /api/status exposes compaction.running without waiting for the shared
writer mutex and without exposing summaries, turns, IDs, paths, prompts, or
exceptions.

Only while one /api/chat request is in flight, the frontend polls status every
500 ms. When running is true it displays this notice:

Hot Draft &#27491;&#22312;&#21387;&#32553;&#65292;&#35831;&#31245;&#20505;&#8230;&#8230;

The timer stops when Chat ends. A generation token prevents late responses from
overwriting the final notice. completed retains the completed notice and makes
exactly one additional status refresh so pending count comes from the backend.
not_needed makes no extra refresh; poll failure does not fail Chat.

There is no idle/permanent polling, WebSocket, SSE, background compaction,
progress stream, or cancellation.

## Dream and verified browser flow

Dream remains explicit, synchronous, bounded, and segment-oriented. One
complete pending Cold segment produces one ingestion. Conversation Memory and
MAGMA persistence plus checkpointing complete before the whole segment is
atomically marked consumed. Chat never performs Dream ingestion.

The verified two-round browser flow displayed both running and completed
notices. Pending changed 0 to 1 to 2 without reload or restart. Hot remained one
summary plus its tail. The first Cold segment contained 14 cold_turn lines; the
second compaction created its own segment using the same one-turn-per-line
contract. Dream reported attempted=2 rather than a per-line count, consumed both
complete segments, changed every line in each segment together, and reduced
pending to zero. Recall continued to work.

## Storage, deployment, and non-goals

Private files are:

    data/draft/hot_drafts.jsonl
    data/draft/cold_drafts.jsonl
    data/draft/hot_draft_compaction_state.json

The file-backed system requires one process, one worker, no --reload, and no
external Dream CLI concurrently. The process-local mutex is not cross-process.

This contract adds no automatic Dream, background work, WebSocket/SSE, Cold
migration, dual-format reader, database/WAL, viewer, or Recall algorithm.
