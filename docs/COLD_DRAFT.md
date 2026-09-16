# Cold Draft Contract

This is the active authority for Lumina's Hot Draft / Cold Draft boundary.

## Core rule

Hot Draft may be semantically compressed only after every raw turn leaving the
live window has been durably preserved in Cold Draft. Hot Draft is live rolling
context. Cold Draft is immutable source evidence for Dream; it is not MAGMA.
Existing Recall does not query Cold. The explicit first-hit Memory entry may
expand source citations through the Cold owner's bounded read-only window below;
it cannot parse the archive directly or change consumption state.

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

The same file may also contain one owner-managed `dream_selection_cursor`
record with `schema_version=1` and `after_segment_id` (no `segment_id` field).
It is mechanical manual-Dream progress, not source or ingestion state. The
owner writes it at a segment boundary and preserves it across append/consume;
turn reconstruction, history and pending counts ignore it.

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

`list_pending_page(limit)` returns at most `limit` unique pending segments in
file order after the cursor, wrapping at most once. The anchor uses all valid
segments, including consumed ones. `advance_pending_cursor(segment_id)` replaces
only the owner's single cursor record using the same atomic writer; it leaves
every source line unchanged. Missing cursors start at the beginning; invalid
or duplicate cursors fail visibly instead of silently resetting selection.
The original read-only `list_pending` prefix contract is unchanged.

These operations still reconstruct the full Cold file. Page output and Dream
attempts are bounded, not total file-read complexity. Each attempted default
Dream segment also incurs at most one full-file atomic cursor update.

## Explicit bounded source reads

`ColdDraftStore(path, source_window_segments=32,
source_window_bytes=1_048_576)` enables a disposable recent-source cache. The
constructor defaults to zero segments (disabled), preserving existing callers.
Initialization reconstructs the existing archive once. Successful owner append,
consume and cursor operations refresh the derived view from the snapshot they
already read; these operations retain their existing full-file costs.

The cache retains the latest complete segment suffix under both limits. Byte
charge is each immutable aggregate's compact UTF-8 JSON: source text, IDs, roles,
timestamps and source metadata, excluding `state` and `consumed_at`. It includes
metadata rather than counting characters as bytes. An oversized newest segment
leaves the cache empty; later complete segments can enter normally. Consumption,
identical append retries and cursor progress never refresh source age. Expiry
removes only read eligibility and its derived lexical postings; the archive,
Facts and their stored support citations remain intact.

`read_source_refs(refs, query="", before=0, after=0, max_refs=64,
max_chars=8000, max_bytes=32768, max_items=32)` returns existing
`SourceMemoryContext` / `SourceExcerpt` DTOs. Each flattened reference identifies
`segment_id`, `turn_id`, `source_start` and `source_end`; the Memory facade adds
the segment ID from the Fact's provenance. If supplied, support text, role,
timestamp, timezone, timezone source and conversation ID must match Cold exactly.
Native turn provenance is preserved. Legacy turns use the existing
`{segment_id}:turn:{index:04d}` projection, segment timestamp and offset-derived
timezone with `legacy_segment_fallback`; consumed segments are read directly,
without invoking the pending-only Dream converter or inventing pending state.

Only explicit support ranges and requested same-segment adjacent turns are
eligible. Exact support receives packing budget before optional context. Output
preserves append/turn order and exact offsets. Overlapping or touching ranges
merge within one turn; unread gaps remain separate labelled ranges. Whole
ranges fit or are omitted. Character, UTF-8 byte and item budgets include the
rendered headers, and the first-hit facade deducts the selected Fact text from
the shared final character budget before requesting source expansion.

A complete requested read has no error code. Missing, rejected or budget-omitted
ranges report `truncated=true`, with `cold_source_partial` when some source is
available or `cold_source_unavailable` when none is available. Facts remain
usable when the underlying source is outside this window. Empty reference lists
produce empty successful reads on a valid enabled window.

`search_recent_sources(query, limit=8, max_refs=64, snippet_chars=400, ...)`
is a separate optional lexical entry for window dialogue that never formed a
Fact. It reuses Memory's word/fragment/CJK features over bounded in-memory
postings, limits candidate inspection and exact snippets, and uses the same
source/output budgets. It performs no generation, embedding or Fact writes and
is not forced ahead of graph Recall.

Ordinary source reads check the archive's file identity, size and
filesystem-reported timestamps, then use only the cache. They never reopen/read
the archive or call `list_all_turns()`. An external rewrite, append or removal
that changes this signature makes the view `cold_source_window_stale` and clears
its cached bodies/postings; read failure reports `cold_source_window_unavailable`.
The signature is checked before and after window construction and source reads.
It is a metadata change detector, not a content fingerprint: same-length in-place
writes can remain invisible if the filesystem reports unchanged timestamps
(including rapid Windows writes), or if metadata is deliberately preserved.
External concurrent writers are unsupported; after out-of-band edits restart
the owner to rebuild the snapshot. Queries never scan the archive to compensate
for this limitation. Restart or a successful owner operation rebuilds from a
known snapshot. The existing single-process, single-writer boundary still
applies; this cache is neither a second source authority nor a cross-process
synchronization mechanism. `source_window_status` reports count/byte limits and
availability without source bodies or filesystem paths.

### Opt-in whole-turn expansion

`ColdDraftStore.read_source_refs(refs, whole_turns=True)` expands each valid
citation to its complete source turn. The default remains exact span reads.
The owner validates the supplied turn identity, range, supporting text and
provided provenance fields before expansion; an invalid citation cannot gain
access to a turn through this option. Adjacent turns still require explicit
`before` / `after` limits and remain within the same segment.

Expansion uses only the existing bounded source window, preserves original
roles, IDs, timestamps and text, and deduplicates overlapping anchors. The
existing `max_refs` and `max_items` caps still apply; `max_chars` and UTF-8
`max_bytes` include rendering overhead. A turn that does not fit is omitted with the existing
partial/unavailable status; it is not clipped or fetched from an expired
segment. Consumption and restart retain the existing source-window behavior.

This is an owner-level read option. It does not change the default v2 Recall,
first-hit selection, Fact packing, Formation, graph writes or automatic source
expansion. Callers remain responsible for their combined output budget.

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
