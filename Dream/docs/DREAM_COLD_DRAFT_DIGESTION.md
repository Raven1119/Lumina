# Manual Cold Draft Digestion

## Scope

This milestone adds one developer-triggered, synchronous, bounded orchestration
step. It changes when the existing Conversation Memory adapter writes MAGMA
events; it does not add a new memory representation, recall behavior, or chat
runtime feature.

```text
python -m Dream.runner
-> ColdDraftStore.list_pending(limit)
-> ColdDraftSegmentConverter
-> MemoryIngestor.ingest(ColdDraftSegment)
-> verify completed durable result
-> ColdDraftStore.mark_consumed(segment_id)
-> DreamRunReport
```

`/api/chat`, application startup, Hot Draft compaction, and recall never invoke
Dream. There is no scheduler, worker, background thread, async batch, or public
API route.

## Manual command

From the repository root:

```bash
python -m Dream.runner --max-segments 10
```

Optional arguments are:

```text
--max-segments N
--stop-on-error
--ingestion-version VERSION
```

The safe default is ten segments, failures do not stop later segments by
default, and the default ingestion version is `dream-v1`. Invalid bounds or an
empty version produce a non-zero exit. Any failed segment also produces a
non-zero exit. Standard output is only the JSON representation of
`DreamRunReport`; it contains no Draft text, memory IDs, paths, exception text,
provider data, or graph data.

The default private runtime locations are the production Cold Draft file under
`data/draft/` and Conversation Memory state under `data/conversation_memory/`.
Both are covered by the repository's `data/` ignore rule. Developers may set
`LUMINA_DREAM_COLD_DRAFT_PATH`, `LUMINA_DREAM_INGESTION_STATE_PATH`, and
`LUMINA_DREAM_MAGMA_PERSIST_DIR` for local testing. Paths are never returned in
the report.

## Actual production Cold Draft schema

`ColdDraftStore.append_segment` currently writes one compact JSON object per
line:

```json
{
  "segment_id": "opaque stable ID",
  "turns": [{"role": "user", "text": "verbatim source text"}],
  "created_at": "aware UTC ISO-8601 timestamp",
  "source": "hot_draft_precompression",
  "state": "pending_digest"
}
```

After the owner performs the state transition, `state` is `consumed` and an
aware UTC `consumed_at` is added. The current production record does **not**
contain `conversation_id`, per-turn `turn_id`, per-turn timestamp, or a named
timezone. Dream does not claim otherwise.

The owner already provides:

- `list_pending(limit)`, which returns `pending_digest` records in deterministic
  JSONL/file order and applies the requested bound;
- `mark_consumed(segment_id)`, the sole Cold Draft write boundary.

The only owner change in this milestone makes `mark_consumed` return successful
for a segment already in `consumed`, without rewriting the file. This gives the
transition the required idempotent semantics. Dream never edits JSONL itself.

## Production conversion

The converter produces the existing Lumina-owned Conversation Memory
`ColdDraftSegment` and `ColdDraftTurn` DTOs. It preserves the source segment ID,
turn order, role, and complete `text` value exactly.

Because the present production schema lacks more granular provenance, the
mapping is deterministic and explicit:

```text
conversation_id = "cold-draft:" + segment_id
turn_id          = segment_id + ":turn:" + zero-padded source index
turn timestamp   = segment.created_at
source timezone  = UTC for the current UTC records, otherwise the source offset
schema_version   = "1"
```

These are adapter/orchestration identities, not generated semantic content.
The segment's source `created_at` is used for every current-schema turn; Dream
run time is never substituted. If a later owner record supplies explicit
`conversation_id`, `turn_id`, aware turn `timestamp`, or source timezone, the
converter preserves those values. Conversation Memory remains responsible for
relative-time normalization, entity fallback, provenance projection, MAGMA
writes, graph/vector persistence, and its completed checkpoint.

Malformed records return stable error codes and remain pending. Dream does not
silently discard or reinterpret source content.

## Boundaries and data flow

Dream depends on two narrow Lumina-owned boundaries:

```text
ColdDraftOwner.list_pending(limit)
ColdDraftOwner.mark_consumed(segment_id)

MemoryIngestorProvider.get(ingestion_version)
MemoryIngestor.ingest(ColdDraftSegment) -> IngestionResult
```

The provider exists because the adapter's durable idempotency version is fixed
when the adapter is constructed. It returns an adapter configured for the run's
version and caches it for that run. Dream imports the Lumina adapter only; it
does not import upstream MAGMA, NetworkX, FAISS, embedding models, or graph
objects.

For each selected segment, execution is strictly serial:

1. Recheck that the record is `pending_digest`.
2. Convert the record to the existing Conversation Memory DTO.
3. Obtain the version-configured `MemoryIngestor` and call `ingest`.
4. Require `status=completed`, matching segment/version, and one persisted
   private memory ID per source turn.
5. Ask the Cold Draft owner to mark the segment consumed.
6. Emit a safe result without exposing those private memory IDs.

The runner requests at most `max_segments` and also slices an over-returning
owner result defensively. It preserves owner order and performs no concurrent
writes or graph scans. The current file stores assume one active writer.

## State transition and recovery

The only successful transition is:

```text
pending_digest
-> every turn/event written
-> graph/vector persistence completed
-> Conversation Memory key marked completed
-> owner mark_consumed succeeds or is already consumed
-> consumed
```

The Conversation Memory key remains the sole memory completion truth:

```text
segment_id + ":" + ingestion_version
```

Dream does not create a second idempotency store.

Recovery windows are:

- Conversion, initialization, event, embedding, persistence, or checkpoint
  failure: do not call `mark_consumed`; the source remains pending and a later
  run retries ingestion.
- Memory completed but owner transition failed: the source remains pending. A
  later run calls the adapter with the same key; the adapter returns
  `already_ingested=true` without adding events, and Dream retries only the
  owner transition.
- Segment already consumed: skip without calling ingestion.
- One segment fails: continue in deterministic order unless
  `stop_on_error=True`.

`DreamRunReport.ingested` counts newly completed-and-consumed segments.
`consumed` also includes a segment whose already-completed memory checkpoint was
recovered and then consumed. `skipped` and `failed` are disjoint fixed statuses.

## Explicit non-features

This milestone does not implement automatic triggering, LLM reflection,
summarization, consolidation, duplicate merging, contradiction handling,
salience, forgetting, deletion, memory rewriting, M-flow/multi-granularity
redesign, recall changes, chat-time writes, public UI/API controls, or changes
to upstream MAGMA.

## Validation

```bash
python -m pytest Dream/tests -q
Conversation_Memory/.venv/Scripts/python.exe -m pytest Dream/tests -q
python -m pytest Conversation_Memory/tests -q
python -m pytest -q
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
git -C Conversation_Memory/upstream/MAGMA diff --stat
```

The isolated-environment Dream run executes the real MAGMA integration test.
All fixtures use temporary production-format Cold Draft files and synthetic
conversation text.
