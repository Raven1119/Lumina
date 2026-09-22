# Provenance and Idempotency

## Idempotency key

The durable key is:

```text
segment_id + ":" + ingestion_version
```

Configured real-model manual Dream uses `grounded-formation-v6` with versioned
F1/F2/G1/G2 receipts, body durability and FirstHit completion keys described in
[Reliable Memory](RELIABLE_MEMORY.md). The shared source and checkpoint owner
below remains the same. Historical `grounded-formation-v2` checkpoints and
explicit runs retain the following protocol. Each v2 unit ID
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
New raw extraction receipts additionally carry `source_coverage_version=1`.
Unmarked receipts retain their original parsing semantics; unknown markers fail
closed. This marker does not change the ingestion key, Formation version or ID
algorithm. Verified/completed and repair-verified checkpoints are read as saved,
without retroactive source expansion or migration.
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

Before saving a new verified batch, final mention and identity decisions also
constrain fact admission. Rejected or missing same-as targets invalidate their
dependents, including transitive links and conflicting distinct-from claims;
unrelated co-occurrences and independent ancestors are not invalidated. An
unresolved occurrence remains visible. A fact using it may remain unbound only
when its complete text is literal in a cited span and its subject/value appear
in that text. This deliberately conservative fallback can reject paraphrases;
surface overlap alone cannot authorize an inserted name. Healthy identities
use the unchanged verifier. Repair applies the same admission rule to new
facts and never demotes a saved positive binding or rewrites prior facts.

For marked receipts only, a candidate rejected by the detail guard may recover
missing evidence from its own explicit named/new subject/object occurrences.
Every missing identifier must come from those literal role surfaces; all cited
spans and roles must resolve exactly within the same turn. The effective ref is
the smallest continuous source interval covering the original refs and these
role positions. Ordinary participants, unresolved identities, same-as chains,
other turns and backend name candidates cannot supply this evidence. Absent
attribute values remain unsupported. Originally valid units retain their refs
and IDs unchanged, including ordinary names that never failed the detail guard.

Expansion prepares a candidate, not a verified fact. The original mandatory
verifier still checks pairing, speech acts, conditions and identity; the final
identity veto remains unchanged. The effective ref travels with the unit through
verification, stable ID creation, event metadata and restart validation. Raw
extraction/repair responses and all SRV/text/roles remain immutable. Recovery of
new candidates after source-ref repair uses the original receipt's marker too.
The exact compact verifier input budget first reserves previously eligible
units and the full bounded conversation. Initial formation reserves every
mention; repair reserves the original subset's identity dependencies and counts
each added candidate together with its additional required mentions. Expanded
candidates fit in candidate order; excess candidates receive a pending
`formation_source_coverage_budget_exceeded` issue without blocking independent
units or gaining another repair attempt. No model call or prompt is added.

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

`IngestionResult.retryable` describes an executable next step for the same
input and protocol. Provider/unsaved verification failures, checkpoint/backend
write failures, an unused eligible source-ref repair, and a saved valid repair
awaiting verification remain retryable. Saved invalid extraction/repair output,
partial batches without eligible repairs, exhausted repairs with pending issues,
and invalid source/checkpoint state do not. Pending is not consumed in either
case. A later explicit run can retry recoverable work without replacing saved
responses; corrected source can arrive as a new Cold segment while the original
failed record and its diagnostics remain intact. No checkpoint deletion is
required to process later source.

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


## Explicit first-hit completion responsibility

An explicitly configured new Formation v2 window first reserves
`segment_id:first-hit-v1` in this same store. Its profile, source fingerprint,
ordered evidence manifest and semantic link plan are frozen before graph writes.
New events skip legacy automatic semantic linking; the frozen local plan must
be durable and completed before Formation completes and Dream consumes Cold.
Retries reuse plans. Existing v2 checkpoints with no reservation keep their old
responsibilities; there is no automatic migration or consumed-window backfill.
An existing first-hit stage requires its original configuration on resume.
See [first-hit contract](FIRST_HIT_MEMORY.md) for ordering, failure, repair
extension and exact read/source boundaries.
