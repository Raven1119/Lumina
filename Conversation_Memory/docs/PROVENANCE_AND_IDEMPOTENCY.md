# Provenance and Idempotency

## Idempotency key

The durable key is:

```text
segment_id + ":" + ingestion_version
```

Configured real-model manual Dream uses `grounded-formation-v2`. Each unit ID
is a stable hash of the atomic SRV/text, exact source refs, optional referenced
time, and Formation version. Mock/legacy ingestion retains
`grounded-span-v2`; for every deterministic eligible user or assistant span,
its stable external evidence identity is:

```text
grounded_span_v2:{turn_id}:{start}:{end}
```

The identity is used directly for MAGMA lookup/idempotency. No second span hash
is introduced and role is not duplicated in the ID because `turn_id` is
globally unique. Immutable `source_role` is stored in provenance.
MAGMA UUID4 node IDs are retained only as private backend handles.

V2 source occurrence IDs derive from conversation/turn identity and checked
source offsets, independently of facts. Original V1 evidence IDs and consumed
Cold records remain unchanged; there is no automatic backfill.

## State machine

```text
absent -> pending -> in_progress -> completed
                              \-> partial -> in_progress -> completed or partial
```

V2 returns a failed result while retaining the last durable stage. `partial`
means all independently verified results are durable but at least one candidate
still has a processing issue. It never authorizes Cold consumption. Explicit
semantic rejection is distinct from pending processing. There is no separate
V2 persisted `failed` status. An invalid stored extraction remains an explicit
failure on retry rather than being replaced by a fresh model sample.

A V2 Formation record checkpoints `extracted`, `verified`, stable `mentions`
bindings and private `memory_ids` in the existing state owner. Successful stages
are reused on retry; malformed or missing stage dependencies fail closed.
New extraction receipts save the bounded raw response before JSON/structural
parsing. Internal `grounded-formation-v2-progress-v1` receipts and verified
batches record candidate indexes and pending/rejected processing issues. Old
exact V2 records remain readable without re-extraction or evidence ID changes.
Invalid mentions and identity links isolate their dependent candidates; every
independently eligible result still passes the original strict verifier.
Full-source fingerprints, occurrence offsets, provenance and identity-link
invariants are rechecked. A completed record covers mentions as well as facts.
The V1 record/IDs remain readable for explicit historical compatibility; the
legacy span record remains manifest-only.

Source-verified local `same_as` follows the target's existing bound ref;
unrelated namesakes in the lookup candidates do not negate that source link.
Unresolved targets, rejected identity claims and actual `distinct_from`
conflicts remain unresolved or fail validation. New and old namesakes retain
separate refs and facts. Previously completed checkpoints are not rebound by
this fix; there is no automatic historical migration.

## Atomic state writes

`IngestionStateStore.put` reads the state map, writes compact JSON to a temporary
file in the same directory, flushes and `fsync`s it, then calls `os.replace`.
This prevents readers from observing a partially written JSON document.
Malformed existing JSON is treated as `state_corrupt` and is not silently
discarded.

This simple file store assumes a single writer; it is not a multi-process
transaction or lock.

## Checkpoints and retry

Once independent results are durable, a later retry may make one local
`source_refs` repair call for pending facts whose original turn IDs exist and
whose structure and mention dependencies are valid. It cannot change text,
subject/relation/value, roles, rejected candidates or existing mention bindings.
The `repair` receipt is saved before parsing; it is never resampled after a
response is saved. Repaired candidates alone pass the original verifier with
their necessary verified mentions. A failed verifier resumes from that receipt.
The original `extracted` and `verified` remain unchanged; `repair_verified`
records the merged batch, preserves previous facts in order, and atomically
returns the manifest to `in_progress` before any new graph writes. Remaining
processing issues keep the result partial; a completed repair stage is never
repeated. This is not general repair, re-extraction or automatic backfill.

V2 persists graph-only mention metadata even when no facts are accepted, then
persists each fact event/vector before recording progress. Stable IDs find
already-written events. A graph-before-vector failure is repaired from the
stored event embedding without new extraction or a duplicate event.

After every manifest item is present, relationship creation runs idempotently,
graph/vectors persist again, and only then is the checkpoint completed. Tests
cover extraction/verification failures, graph/vector and state-write windows,
zero-fact mentions, restart, source mismatch and invalid stage dependencies.

## Provenance fields

Every grounded event stores:

- the grounded span evidence/unit ID;
- source `turn_id`;
- exact `source_start` and `source_end` offsets;
- original `source_role=user|assistant`;
- `segment_id` and `conversation_id`;
- exact aware `source_timestamp`;
- declared `source_timezone`;
- truthful `timezone_source` (`client`, `configured_default`, or
  `legacy_segment_fallback`);
- `ingestion_version`.
Temporal metadata additionally stores the original expression, reference
timestamp/timezone, normalized start/end, normalization method, and confidence.
Recall reconstructs `SourceProvenance`; candidates with malformed or missing
provenance are excluded.

Grounded events inherit their eligible user source turn's ID, timestamp, IANA
timezone, and timezone source. Legacy records keep the stable indexed turn ID and segment
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

1. every source occurrence, derived memory event and vector;
2. required graph relationships;
3. provenance metadata;
4. the completed idempotency checkpoint.

That transition uses the existing Cold Draft owner and preserves Cold-first
semantics. Dream reuses this state as the sole idempotency source: if memory is
completed but the consumed transition fails, the next manual run receives
`already_ingested=true` and retries the owner transition without adding events.
