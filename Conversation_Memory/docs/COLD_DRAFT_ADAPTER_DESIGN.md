# Cold Draft Adapter and Recall Design

## Status and scope

This document describes the current production Conversation Memory v1 boundary,
not only the original synthetic-fixture milestone.

```text
production ColdDraftSegment
-> Lumina-owned ingestion DTOs
-> MagmaMemoryAdapter
-> pinned unmodified MAGMA backend
-> durable graph/vector memory

query + RecallPolicy
-> bounded Lumina-owned Recall execution
-> MemoryContext
```

Production Cold ownership, Dream scheduling, and Chat injection remain outside
this workspace.

## Lumina-owned interfaces

```text
MemoryIngestor.ingest(ColdDraftSegment) -> IngestionResult
MemoryRetriever.recall(query, RecallPolicy) -> MemoryContext
```

`MagmaMemoryAdapter` implements both. `MemoryBackend` is the private
MAGMA-facing protocol.

Public DTOs include:

- `ColdDraftSegment` / `ColdDraftTurn`;
- `SourceProvenance`;
- `NormalizedTemporalReference`;
- `IngestionResult`;
- `RecallPolicy`;
- `MemoryEvidence`;
- `MemoryContext`.

MAGMA classes, graph nodes, UUIDs, paths, scores, embeddings, NetworkX, and
FAISS never cross the facade.

## Ingestion conversion

Configured real-model Dream sends one bounded Cold segment to dedicated
DeepSeek-V4-Pro Grounded Formation in non-thinking mode with `max_tokens=2000`.
Accepted atomic `GroundedMemoryUnit` values pass the deterministic grounding
validator, bounded semantic fallback, and value-only guard before checkpointing
and MAGMA writes. Mock/legacy ingestion deterministically builds exact
`GroundedSpanUnit` spans. Either path may produce `0..M` events.

For configured real-model Formation, event text is `GroundedMemoryUnit.text`,
the public evidence ID is the stable grounded-unit ID, and private metadata
retains subject/relation/value, every exact source ref and offset, Formation
version, referenced time, and full source provenance.

For mock/legacy `GroundedSpanUnit` ingestion only, event text is exactly
`source_turn.content[start:end]` and the evidence ID is
`grounded_span_v2:{turn_id}:{start}:{end}`. Its metadata retains source role,
turn ID, exact offsets, deterministic entity fallback, normalized temporal
references, and full source provenance.

Both paths inherit source-turn time; the adapter never substitutes Dream
execution time.

## Temporal normalization

Temporal normalization is Lumina-owned and uses each source turn's own
`created_at` and `source_timezone`.

- English and Chinese relative/calendar expressions are supported according to
  the active parser contracts.
- Results are stored as aware UTC half-open intervals `[start, end)`.
- Original text and expressions remain unchanged.
- DST and real local calendar boundaries are respected.
- Upstream MAGMA remains unmodified.

## Idempotency and persistence

Durable ingestion key:

```text
(segment_id, ingestion_version)
```

The adapter checkpoints pending/in-progress/completed state plus the ordered
grounded unit-ID manifest and private memory IDs written so far. It stores no
span text. Stable unit IDs allow retry to converge after partial graph/vector
persistence, while a same-version manifest mismatch fails closed.

Dream may consume the source segment only after the adapter returns a complete,
validated durable result.

MAGMA graph, vectors, and the Lumina checkpoint are separate files and are not
one ACID transaction. Current correctness relies on per-event persistence,
stable IDs, checkpoints, and retry convergence.

## RecallPolicy

Current fields:

| Field | Default | Meaning |
| --- | ---: | --- |
| `top_k` | 5 | maximum fused anchors |
| `max_chars` | 2000 | maximum rendered context characters |
| `max_evidence_items` | 5 | maximum public anchors + expansions |
| `max_graph_depth` | 5 | graph depth; `0` is valid anchor-only |
| `max_nodes` | 100 | bounded lexical/traversal candidate budget |
| `final_min_score` | `None` | optional inclusive composed-score floor; Chat uses `0.144` |
| `relation_surfaces` | `None` | explicit caller-supplied relation surfaces |

## Anchor identification

```text
MiniLM dense ranking
+ bounded deterministic lexical ranking
+ entity-conditioned FAISS IDSelectorBatch subset ranking when the query
  carries a target_entity_ref (bounded to that EntityNode's
  REFERS_TO(role=subject) events; adds candidates only)
-> RRF(k=60)
-> stable fused top_k anchors
```

Lexical ranking scans at most `max_nodes` graph entries and only projects valid
event nodes. Lexical failure safely falls back to dense-only.

`top_k` limits anchors only. The final public evidence total is separately
limited to `0..max_evidence_items`. Zero selected evidence is a successful
Recall result with empty rendering and no safe error; `recall_unavailable`
remains a distinct safe failure. This cardinality contract does not implement
automatic relevance abstention.

## Fixed traversal

The default path uses the current MAGMA graph traversal under
`max_graph_depth`/`max_nodes` constraints.

- Anchors are projected first.
- Valid non-anchor event expansions may be projected afterward.
- Entity and other internal nodes never become public evidence.
- Expansions are deduplicated using stable IDs and bounded before rendering.

## Evidence projection

Before public projection, candidates must have:

- non-empty event text;
- aware timestamp;
- stable evidence ID;
- complete valid provenance.

Public `MemoryEvidence` contains only:

```text
evidence_id
text
timestamp
SourceProvenance
```

No graph path, relation metadata, score, embedding, MAGMA UUID, local path, or
narrative context is returned.

## Controlled relation compatibility

When a caller supplies `relation_surfaces`, the small controlled resolver maps
query surfaces and private GroundedMemoryUnit relation metadata to canonical
IDs. A resolved mismatch rejects the candidate before BGE. A compatible match,
an unresolved query, an unresolved memory relation, or absent metadata preserves
existing behavior. Compound query surfaces remain independent. Normal Chat does
not supply relation surfaces; this is a structured caller/future Mind seam, not
a free-text query parser.

## Context Linearization

- Preserve retrieval order and render plain role-labelled evidence text.
- Rendering obeys `max_chars`; final-line truncation is allowed and reported.

## Chat injection boundary

Recall is enabled by source default and may be explicitly disabled with
`LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED=false`. When enabled, normal Chat
receives only non-empty bounded `MemoryContext.rendered_text` as a temporary
context block.

Recall does not:

- scan Cold Draft;
- trigger Dream or ingestion;
- persist the injected block into Draft;
- expose evidence DTOs or backend internals to the provider.

Empty or failed Recall falls back to ordinary Chat.

## Current capability boundaries

- Production uses the fixed BGE reranker and inclusive
  `final_min_score=0.144`; neither constitutes a reliable semantic no-answer
  contract.
- No automatic intent/query classification, free-text relation parser, entity
  resolver, or Recall scheduler.
- No Evidence Organizer/Ledger, conflict/current-state resolver, fact
  supersession, or semantic deduplication.
- Cross-turn reference is only partially supported through joint recall and
  graph adjacency; there is no explicit coreference resolution.
- Knowledge updates are preserved as new/old events and interpreted by the
  final model; no memory is automatically invalidated.

## Failure handling

- Schema failures use stable validation codes.
- Corrupt checkpoint state is not overwritten.
- Initialization failure creates a safe unavailable backend where configured.
- Write failure remains retryable and never becomes completed.
- Recall exception/corruption returns an empty context with
  `recall_unavailable`.
- Empty retrieval returns a valid empty context without an error.
- Raw exception text is discarded at the facade boundary.

## Validation

Use synthetic fixtures and temporary/marker-owned paths:

```bash
python -m pytest Conversation_Memory/tests -q
python -m pytest -q
python -m scripts.recall_e2e_test
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
git -C Conversation_Memory/upstream/MAGMA diff --stat
```

The upstream MAGMA worktree must remain clean.
