# Cold Draft contract

Cold Draft is the owner of original turns that leave Hot during rolling
compaction. Memory's SQLite Cold table is a rebuildable index. A summary never
replaces the original text, and the old Cold pending/consumed marker is not the
Memory v1 Dream cursor.

## Preservation and format

`core/hot_draft_compactor.py` chooses complete user/assistant pairs. It first
appends each departing turn through `ColdDraftStore.append_segment`, then
atomically replaces Hot with the new summary and recent raw tail. A failure
before the Cold append leaves Hot unchanged. A failure after Cold preservation
may leave an overlap; stable turn IDs let `/api/history` deduplicate it.

Cold is JSONL with one physical `cold_turn` record per original turn and a
logical `segment_id`. Each record preserves role, text, order, source, and,
when known, `turn_id`, `created_at`, `source_timezone`, and
`timezone_source`. The owner rejects incomplete/conflicting logical segments
on reconstruction. `list_all_turns()` returns original turns in file order,
including historically consumed segments. It does not rewrite source text.

The old `pending_digest` / `consumed` state and selection cursor remain
readable for pre-migration data, but Memory v1 never uses them to decide what
to integrate. Memory stores its own `cold_cursor` in
`data/memory_v1/memory.sqlite`. A cursor absent from the database prevents
automatic backlog processing. `python -m Dream.runner cursor-end` starts from
future Cold, while `rebuild` starts from the beginning; both require the Chat
service stopped. Untimed legacy turns are excluded from rebuild rather than
given invented timestamps.

## Dream and API

After a real Chat reply, the app checks embedding availability, cursor,
pause state, and `LUMINA_DREAM_TRIGGER_TURNS` (default 40). It launches at
most one pair-aligned window in a background thread. The Dream lock prevents
overlap. Model calls run outside the Chat write lock; only copying Cold and
committing the graph coordinate briefly. A failed window retains its cursor;
three consecutive failures pause automatic processing until a manual run
succeeds.

`POST /api/dream/run` runs one window explicitly. `GET /api/status` reports
unintegrated Cold turn count, most recent Dream and pause state.
`GET /api/memory` lists committed memories and recent Dream logs. These
responses do not expose filesystem paths, provider bodies, credentials or
tracebacks. The independent cognitive chain does not consume Chat's Cold
cursor.

## Testing

Use temporary Cold, Hot, and Memory paths. Validate preservation,
restart reconstruction, cursor separation, retry, and Chat/Dream concurrency
with `tests/test_cold_draft_store.py`, `tests/test_cold_draft_progress.py`,
`tests/test_history_api.py`, and `tests/test_memory_v1_facade.py`.
