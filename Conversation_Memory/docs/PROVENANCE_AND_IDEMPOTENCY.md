# Provenance and Idempotency

## Idempotency key

The durable key is:

```text
segment_id + ":" + ingestion_version
```

Different ingestion versions intentionally produce a fresh derived memory set.
For each turn, the stable public evidence ID is:

```text
sha256(segment_id + NUL + turn_id + NUL + ingestion_version)
```

MAGMA UUID4 node IDs are retained only as private backend handles.

## State machine

```text
absent -> pending -> in_progress -> completed
                             \-> failed -> in_progress (retry)
```

The state record contains only status and private memory IDs. A completed key
returns `already_ingested=true` without writing nodes. Failures never write a
completed status.

## Atomic state writes

`IngestionStateStore.put` reads the state map, writes compact JSON to a temporary
file in the same directory, flushes and `fsync`s it, then calls `os.replace`.
This prevents readers from observing a partially written JSON document.
Malformed existing JSON is treated as `state_corrupt` and is not silently
discarded.

This simple file store assumes a single writer; it is not a multi-process
transaction or lock.

## Checkpoints and retry

Ingestion persists MAGMA after every new event, then atomically records the
private memory ID. Before writing a turn, the adapter searches existing MAGMA
metadata by stable evidence ID. This closes the important retry window where a
MAGMA event exists but its state checkpoint did not complete.

After all turns, entity relationships are constructed, MAGMA is persisted
again, and only then is the key marked completed. A partial retry reuses found
events and adds only missing events. Tests cover event-write failure,
persistence failure after a node write, and restart recovery.

## Provenance fields

Every event stores:

- `segment_id`;
- `conversation_id`;
- `turn_id`;
- exact aware `source_timestamp`;
- declared `source_timezone`;
- truthful `timezone_source` (`client`, `configured_default`, or
  `legacy_segment_fallback`);
- `ingestion_version`.

Temporal metadata additionally stores the original expression, reference
timestamp/timezone, normalized start/end, normalization method, and confidence.
Recall reconstructs `SourceProvenance`; candidates with malformed or missing
provenance are excluded.

Native V2 events use each source turn's own ID, timestamp, IANA timezone, and
timezone source. Legacy records keep the stable indexed turn ID and segment
timestamp fallback and are explicitly marked `legacy_segment_fallback`. Old
persisted provenance that predates the field is projected with the same legacy
default; it is not rewritten or automatically re-ingested.

Turn IDs are generated before the first Hot persistence attempt. Retrying the
same `DraftTurn` reuses the ID, and Cold-first compaction copies it verbatim.
The segment/version checkpoint remains the ingestion idempotency authority;
the V2 schema does not introduce a competing checkpoint.

## Stable ordering

Recall ordering does not depend on MAGMA UUIDs. It uses the private descending
backend score, then source timestamp, then the stable SHA-256 evidence ID. The
same backend result set therefore produces the same Lumina ordering. Backend
scores and vectors are not exposed by `MemoryEvidence`.

## Manual production Cold Draft transition

The separately authorized manual Dream consumer may mark a real segment
consumed only after all of the following are durable:

1. every derived memory event and vector;
2. required graph relationships;
3. provenance metadata;
4. the completed idempotency checkpoint.

That transition uses the existing Cold Draft owner and preserves Cold-first
semantics. Dream reuses this state as the sole idempotency source: if memory is
completed but the consumed transition fails, the next manual run receives
`already_ingested=true` and retries the owner transition without adding events.
