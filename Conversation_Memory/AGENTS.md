# AGENTS.md

## Scope

This file applies to all work under `Conversation_Memory/`.

Conversation Memory v1 is an implemented Lumina-owned ingestion and Recall
facade over a pinned, unmodified MAGMA backend. It is no longer an initial
integration milestone.

Current chain:

```text
ColdDraftSegment
-> MagmaMemoryAdapter.ingest(...)
-> per-turn MAGMA events + graph/vector persistence
-> durable ingestion checkpoint

query + RecallPolicy
-> dense + bounded lexical rankings
-> RRF anchors
-> temporal hard filtering
-> fixed or caller-opted adaptive traversal
-> bounded MemoryEvidence / MemoryContext
```

## Authority

Also obey:

- root `AGENTS.md`;
- `docs/CURRENT_STATUS.md`;
- `docs/LUMINA_CODEBASE_SCAN.md`;
- `docs/COLD_DRAFT.md`;
- `Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md`;
- `Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md`;
- `Conversation_Memory/docs/CHINESE_TEMPORAL_PARSER.md`.

When instructions conflict, preserve Cold-first provenance and the narrow
Lumina-owned facade before optimizing MAGMA behavior.

## Current public boundary

Production callers may use only Lumina-owned interfaces and DTOs:

```text
MemoryIngestor.ingest(ColdDraftSegment) -> IngestionResult
MemoryRetriever.recall(query, RecallPolicy) -> MemoryContext
```

`MagmaMemoryAdapter` implements both interfaces. MAGMA nodes, UUIDs, NetworkX,
FAISS, embeddings, graph paths, backend scores, and narrative context remain
private.

## Current ingestion behavior

- One source turn becomes one MAGMA event.
- The original source text and event timestamp are preserved.
- Stable evidence IDs are derived from source identity and ingestion version.
- Provenance includes segment, conversation, turn, timestamp, timezone, and
  ingestion version.
- Temporal normalization uses each source turn's own timestamp/timezone.
- English and Chinese temporal mentions are stored as aware UTC half-open
  intervals `[start, end)` without replacing the original text.
- Ingestion is durable and retryable through
  `(segment_id, ingestion_version)` checkpoints.
- Memory completion must be established before Dream may consume the source
  Cold segment.

## Current RecallPolicy

The current policy surface contains:

```text
top_k
max_chars
max_evidence_items
max_graph_depth
max_nodes
intent
temporal_window
beam_width
drop_threshold
```

Semantics:

- `top_k` limits fused dense/lexical RRF anchors;
- `max_evidence_items` limits final public anchors plus graph expansions;
- `max_graph_depth=0` is valid and means anchor-only;
- `max_nodes` is a hard internal scan/traversal budget;
- `temporal_window` is an aware UTC half-open hard filter applied to anchors and
  expansions;
- optional caller fields default to `None` and preserve the fixed traversal
  path;
- explicit `intent`, `beam_width`, or `drop_threshold` enables adaptive
  traversal;
- adaptive failure falls back to fixed traversal; fixed failure returns a safe
  empty context.

Intent behavior:

- `GENERAL` and `ENTITY` use the validated ENTITY-biased upstream fallback
  weights;
- `WHEN` strongly favors temporal relations;
- `WHY` currently maps to GENERAL and is not causal specialization.

## Current anchor and traversal behavior

- Dense MiniLM and bounded lexical rankings are fused with RRF using `k=60`.
- Lexical failure safely falls back to dense-only.
- Fixed traversal projects valid event expansions behind anchors.
- Adaptive traversal uses bounded beam search and relation-aware scoring.
- Internal graph nodes may participate in traversal, but only event nodes with
  valid text, aware timestamp, stable evidence ID, and provenance may become
  public evidence.
- Cross-turn context is only partially supported through semantic/temporal
  adjacency and joint recall; there is no explicit coreference resolution.

## Context Linearization

- `intent=None` preserves retrieval order and the legacy plain-text rendering.
- Explicit intents add UTC timestamps.
- `WHEN` orders selected evidence chronologically.
- `GENERAL`, `ENTITY`, and `WHY` preserve retrieval order.
- Rendering obeys `max_chars` and never exposes internal metadata.

## Frozen v1 boundaries

Do not change the following without an explicit task and supporting evidence:

- one-turn-one-event granularity;
- Cold-first source authority;
- Lumina-owned DTO/facade boundary;
- stable evidence/provenance projection;
- fixed/adaptive selection semantics;
- RRF constants and validated GENERAL weights;
- safe empty/failure behavior;
- pinned upstream MAGMA revision.

A narrow change is permitted only for a reproducible bug, a real failure sample,
or a minimal caller-contract requirement from an explicitly designed Mind
System.

## Not authorized by default

Do not add or infer authorization for:

- automatic Dream or chat-time ingestion;
- custom coreference resolution, event rewriting, segment merging, or
  contextual-window embeddings;
- custom temporal anchor ranking where the pinned upstream has no generic
  implementation;
- fact supersession, contradiction resolution, forgetting, deletion, or memory
  rewriting;
- automatic intent/query classification or Recall scheduling;
- Evidence Organizer/Ledger, LLM Judge, cross-encoder, or relevance gate;
- PostgreSQL, Neo4j, another graph database, or a new vector backend;
- public exposure of MAGMA internals;
- modification of `upstream/MAGMA/`.

## Upstream policy

The upstream checkout is pinned by `MAGMA_COMMIT.txt`. Keep its worktree clean.
Reuse upstream algorithms through Lumina-owned adapters; do not copy research
routers, benchmark heuristics, answer formatters, or narrative generators into
the production path.

## Testing

Use synthetic data and temporary/marker-owned paths. Never use real user Draft
or memory data in committed tests.

Run:

```bash
python -m pytest Conversation_Memory/tests -q
python -m pytest -q
python -m scripts.recall_e2e_test
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
git -C Conversation_Memory/upstream/MAGMA diff --stat
```
