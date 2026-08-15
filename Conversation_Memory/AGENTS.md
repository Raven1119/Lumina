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
-> one bounded Formation call for configured real-model Dream
-> deterministic validation of atomic GroundedMemoryUnit values
-> durable formed-unit checkpoint
-> 0..M MAGMA events + graph/vector persistence

query + RecallPolicy
-> dense + bounded lexical rankings
-> RRF anchors
-> fixed bounded traversal
-> fail-open controlled relation compatibility for supplied relation surfaces
-> fixed BGE batch rerank
-> pinned Hindsight post-rerank score composition
-> bounded MemoryEvidence / MemoryContext
```

## Authority

Also obey:

- root `AGENTS.md`;
- `docs/CURRENT_STATUS.md`;
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

- Configured real-model manual Dream uses `grounded-formation-v1` with
  MiniMax-M3 in non-thinking mode and a Formation-only 2000-token output budget;
  the deterministic `grounded-span-v2` path remains for mock/legacy callers.
- Formation makes one call for a newly seen bounded segment, then validates
  atomic subject/relation/value units, exact source refs, role authorization,
  negation, uncertainty, and exact details.
- One accepted `GroundedMemoryUnit` becomes one MAGMA event. A source segment may produce
  `0..M` events, and a completed empty manifest is valid.
- Cold remains immutable. Unit source refs preserve source turn identity, role,
  exact unambiguous span offsets, timestamp, and timezone.
- Stable evidence IDs are derived from grounded-unit identity and ingestion
  version.
- Provenance includes segment, conversation, turn, exact offsets, timestamp,
  timezone, and ingestion version.
- Temporal normalization uses each source turn's own timestamp/timezone.
- English and Chinese temporal mentions are stored as aware UTC half-open
  intervals `[start, end)` without replacing the original text.
- Formation checkpoints contain the validated units and ordered IDs before any
  MAGMA event. A downstream retry revalidates and reuses them without another
  Formation call.
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
final_min_score
relation_surfaces
```

Semantics:

- `top_k` limits fused dense/lexical RRF anchors;
- `max_evidence_items` limits final public anchors plus graph expansions;
- `max_graph_depth=0` is valid and means anchor-only;
- `max_nodes` is a hard internal scan/traversal budget;
- `final_min_score` is an optional inclusive floor over the composed Hindsight
  post-rerank score; production sets it to `0.144`;
- `relation_surfaces` accepts explicit caller-supplied relation text. Resolved
  incompatibility rejects a candidate; compatible or either-side `UNRESOLVED`
  keeps existing Recall behavior. Normal Chat currently supplies `None`;
- fixed traversal failure returns a safe empty context.

## Current anchor and traversal behavior

- Dense MiniLM and bounded lexical rankings are fused with RRF using `k=60`.
- Lexical failure safely falls back to dense-only.
- Fixed traversal projects valid event expansions behind anchors.
- Internal graph nodes may participate in traversal, but only event nodes with
  valid text, aware timestamp, stable evidence ID, and provenance may become
  public evidence.
- Cross-turn context is only partially supported through semantic/temporal
  adjacency and joint recall; there is no explicit coreference resolution.

## Context Linearization

- Rendering preserves retrieval order, obeys `max_chars`, and never exposes
  internal metadata.

## Frozen production boundaries

Do not change the following without an explicit task and supporting evidence:

- bounded, source-grounded atomic unit granularity;
- `N` source turns to `0..M` grounded events with at most one Formation call
  for a newly seen configured real-model segment;
- Cold-first source authority;
- Lumina-owned DTO/facade boundary;
- stable evidence/provenance projection;
- fixed traversal semantics;
- RRF constants and validated GENERAL weights;
- safe empty/failure behavior;
- fixed production BGE batch reranking with private scores and lazy per-adapter
  reuse;
- pinned Hindsight post-rerank normalization, linear recency, and
  multiplicative composition with neutral unsupported signals; final scores
  remain private;
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
- Evidence Organizer/Ledger, LLM Judge, another cross-encoder, or relevance
  gate;
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
