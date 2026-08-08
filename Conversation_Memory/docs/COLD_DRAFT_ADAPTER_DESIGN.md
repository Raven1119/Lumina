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

Every source turn becomes one MAGMA event. The original source text and source
turn timestamp are retained. Metadata includes:

- stable `evidence_id`;
- role and original text;
- deterministic entity fallback;
- normalized temporal references;
- source provenance containing segment, conversation, turn, timestamp,
  timezone, and ingestion version.

The adapter does not replace source timestamps with Dream time, session time, or
`dia_id` offsets.

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

The adapter checkpoints pending/in-progress/completed state and the private
memory IDs written so far. Stable evidence IDs allow retry to converge after
partial graph/vector persistence.

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
| `intent` | `None` | caller-supplied `GENERAL/WHY/WHEN/ENTITY` |
| `temporal_window` | `None` | aware UTC `[start,end)` hard filter |
| `beam_width` | `None` | optional adaptive beam width |
| `drop_threshold` | `None` | optional adaptive relative-drop threshold |

All optional fields `None` preserves the fixed traversal path. Supplying
`intent`, `beam_width`, or `drop_threshold` opts into adaptive traversal.
Supplying only a temporal window does not enable adaptive traversal.

## Anchor identification

```text
MiniLM dense ranking
+ bounded deterministic lexical ranking
-> RRF(k=60)
-> stable fused top_k anchors
```

Lexical ranking scans at most `max_nodes` graph entries and only projects valid
event nodes. Lexical failure safely falls back to dense-only.

`top_k` limits anchors only. The final public evidence total is separately
limited by `max_evidence_items`.

The pinned upstream contains no generic production-ready temporal anchor rank
source. `temporal_window` therefore remains a hard filter, not a custom temporal
ranking algorithm.

## Temporal hard filtering

Lumina applies:

```text
start <= event_timestamp < end
```

in aware UTC to dense anchors, lexical candidates, and graph expansions.
Missing, naive, or invalid event timestamps are skipped. Window-excluded events
cannot re-enter through graph traversal.

## Fixed traversal

The default path uses the current MAGMA graph traversal under
`max_graph_depth`/`max_nodes` constraints.

- Anchors are projected first.
- Valid non-anchor event expansions may be projected afterward.
- Entity and other internal nodes never become public evidence.
- Expansions are deduplicated using stable IDs and bounded before rendering.

## Adaptive traversal

Caller opt-in adaptive traversal uses bounded relation-aware beam search.
Current transition score is:

```text
0.6 * relation_weight + 0.4 * query/event cosine similarity
```

Current relation weights:

- GENERAL/ENTITY: ENTITY `0.60`, SEMANTIC `0.30`, TEMPORAL `0.05`, CAUSAL `0.05`;
- WHEN: TEMPORAL `0.70`, other relation types `0.10` each;
- WHY: currently maps to GENERAL; no causal specialization.

Defaults when adaptive is enabled without explicit values are beam width `10`
and drop threshold `0.15`.

Adaptive failure falls back to fixed traversal. If both fail, Recall returns a
safe empty context with a stable error code.

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

## Context Linearization

- `intent=None`: preserve retrieval order and render plain evidence text for
  compatibility.
- Explicit intents: add normalized UTC timestamps.
- `WHEN`: sort the selected evidence chronologically, then by evidence ID.
- `GENERAL`, `ENTITY`, `WHY`: preserve retrieval order.
- `WHY` does not synthesize causal explanations.
- Rendering obeys `max_chars`; final-line truncation is allowed and reported.

## Chat injection boundary

Recall is optional and disabled by default. When enabled, normal Chat receives
only non-empty bounded `MemoryContext.rendered_text` as a temporary context
block.

Recall does not:

- scan Cold Draft;
- trigger Dream or ingestion;
- persist the injected block into Draft;
- expose evidence DTOs or backend internals to the provider.

Empty or failed Recall falls back to ordinary Chat.

## Current capability boundaries

- No relevance threshold, LLM judge, cross-encoder, or reliable abstention.
- No automatic intent/query classification or Recall scheduler.
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
