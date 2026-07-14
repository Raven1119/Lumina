# Cold Draft Fixture Adapter Design

## Scope

The adapter contract is covered by the native V2 synthetic fixture
`fixtures/cold_draft_segment_v2.json`, the retained V1 adapter fixture, and a
synthetic production-format legacy fixture. Dream, not this adapter, reads
production Cold Draft JSONL and owns the consumed-state transition. Upstream
MAGMA remains unchanged.

## Data flow

```text
synthetic JSON fixture
-> ingestion.fixture_loader schema validation
-> Lumina ColdDraftSegment / ColdDraftTurn
-> per-turn temporal normalization + deterministic entity fallback
-> MagmaMemoryAdapter
-> private MemoryBackend protocol
-> unmodified MAGMA event/graph/vector persistence
-> backend candidates
-> stable Lumina MemoryEvidence
-> bounded MemoryContext
```

MAGMA objects remain behind `adapter.backend.MemoryBackend`. The public adapter
accepts and returns only Lumina-owned frozen dataclasses.

## Schema

Schema versions `1` and `2` require a non-empty segment and conversation ID,
`pending_digest` state, aware ISO-8601 `created_at`, an explicit source timezone,
and at least one turn. Each turn requires a unique-facing ID, `user` or
`assistant` role, non-empty content, and an aware ISO-8601 timestamp. Native V2
turns additionally carry their own validated IANA `source_timezone` and
`timezone_source`; V1 fixtures are projected as
`legacy_segment_fallback`.

The loader rejects malformed/missing fields with a stable
`SegmentValidationError.code`. The adapter repeats invariant checks for callers
that construct DTOs directly. Neither layer returns paths, tracebacks, provider
payloads, credentials, or MAGMA objects.

## Lumina-owned interfaces

- `MemoryIngestor.ingest(ColdDraftSegment) -> IngestionResult`
- `MemoryRetriever.recall(str, RecallPolicy) -> MemoryContext`
- `MagmaMemoryAdapter` implements both interfaces.
- `MemoryBackend` is the only MAGMA-facing protocol.

The DTO set comprises `ColdDraftSegment`, `ColdDraftTurn`,
`SourceProvenance`, `NormalizedTemporalReference`, `IngestionResult`,
`RecallPolicy`, `MemoryEvidence`, and `MemoryContext`.

## MAGMA conversion

Every source turn becomes one MAGMA event. The original source timestamp is
passed directly as the event timestamp—there is no session timestamp or
`dia_id` offset. Metadata includes:

- stable `evidence_id`;
- role and original text;
- deterministic entities;
- normalized temporal references;
- provenance containing segment, conversation, turn, timestamp, timezone,
  timezone source, and ingestion version.

`RealMagmaBackend` uses `TemporalResonanceGraphMemory.add_event`, upstream graph
and vector persistence, upstream entity-edge construction, and upstream query.
It does not copy graph extraction or traversal algorithms. The one-time
MiniLM model must already exist in the isolated environment cache; runs set
Hugging Face offline mode.

## Time normalization

The adapter recognizes `today`, `yesterday`, `tomorrow`, `last week`, and
`next week` case-insensitively. Each expression is resolved from its own source
turn's aware timestamp after conversion into that turn's source timezone. The
original expression, reference timestamp, reference timezone, normalized
start/end, method, and confidence are preserved.
The original content is never replaced.

Offset strings such as `+08:00` remain supported for legacy schema V1. Native
V2 uses validated IANA names, allowing midnight and DST boundaries to follow
the correct local calendar while the aware timestamp remains authoritative for
the instant.

## Entity fallback

`ingestion.entities.extract_entities` is a small explainable regular expression
for capitalized proper names plus optional configured entities. It deduplicates
in encounter order and excludes a short stop list. It is not presented as full
NER. Extracted names enter MAGMA metadata, allowing upstream entity relationship
construction; the integration test verifies an ENTITY link for Raven.

## Recall boundary

`RecallPolicy` requires positive `top_k`, `max_chars`,
`max_evidence_items`, `max_graph_depth`, and `max_nodes`. Optional
`min_relevance` is a finite cosine threshold in `[-1.0, 1.0]`; it defaults to
`None`. `max_relevance_candidates` is bounded to 1–20. The backend receives the
graph budgets. The facade then:

1. discards candidates without valid provenance/stable evidence IDs;
2. sorts deterministically by descending score, timestamp, and evidence ID;
3. when enabled, scores at most `max_relevance_candidates` with captured query
   and persisted evidence vectors, then filters without reordering;
4. applies `min(top_k, max_evidence_items)`;
5. truncates rendered evidence to `max_chars`;
6. returns only frozen Lumina DTOs and truncation metadata.

Backend scores and vectors are private. Public evidence may contain only the
local cosine `relevance_score`. If the enabled scorer is unavailable, recall
returns `relevance_unavailable` rather than silently bypassing the gate. If all
candidates are filtered, recall returns a valid empty context without an error.

There is no Cold Draft scan during recall. MAGMA UUIDs are never exposed as
evidence identifiers.

## Failure handling

- Schema failures use stable validation codes.
- Corrupt state returns `state_corrupt` and does not overwrite the file.
- `MagmaMemoryAdapter.create_real` catches MAGMA/embedding initialization
  failures and installs a safe unavailable backend, so ingestion and recall
  still return their structured failure DTOs.
- Write/persistence failures return `memory_write_failed`, remain retryable,
  and never become completed.
- Recall exceptions, missing models, or corrupt memory return an empty
  `MemoryContext` with `recall_unavailable`.
- Empty retrieval returns a valid empty context without an error.

Raw exception strings are intentionally discarded at the facade boundary.

## Explicit non-integration

The Conversation Memory facade does not import Draft stores, MessageRuntime,
ModelClient, API routes, or frontend code. Production conversion and the
consumed transition remain in the separately bounded manual Dream layer.
