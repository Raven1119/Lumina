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

Cold ownership, Dream scheduling and Chat injection remain with their existing owners.

## Lumina-owned interfaces

```text
MemoryIngestor.ingest(ColdDraftSegment) -> IngestionResult
MemoryRetriever.recall(query, RecallPolicy) -> MemoryContext
MemoryRetriever.recall_mentions(query, limit=20) -> EntityMentionContext
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

Configured real-model Dream uses `grounded-formation-v2`: one bounded
DeepSeek-V4-Pro extraction over the complete source window, then batch
verification of every structurally eligible proposition and mention identity.
The local output budget is 8192 tokens per call; input remains 32 turns/20,000
characters, with explicit overflow failure. Mention surface and occurrence
ordinal are deterministically resolved to exact source offsets. Facts retain
subject/relation/value and verified subject/object mention roles.

The bounded extraction response is checkpointed before parsing; verification
and bindings are checkpointed before graph mutation. Candidate processing
errors isolate their dependencies while independently verified results persist.
Pending processing errors block segment completion; semantic rejections do not.
One later source-ref repair attempt may re-evidence eligible pending facts and
verify that subset without changing saved results or repeating extraction.
See [provenance and recovery](PROVENANCE_AND_IDEMPOTENCY.md) for exact stages.
Isolated mentions persist even in a
zero-fact window. `recall_mentions` returns source occurrences through public
DTOs, without asserting their proposed content. The deterministic
`grounded-span-v2` path and explicit historical V1 compatibility remain separate
from the configured real-model writer. Old evidence IDs are unchanged.

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

The adapter checkpoints pending/in-progress/partial/completed state, validated units,
source occurrence records, stable bindings and private memory IDs written so far. Stable unit IDs allow retry to converge after partial graph/vector
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
| `max_nodes` | 100 | bounds returned retrieval candidates, projected nodes and actual graph adjacency reads; not historical vector membership |
| `final_min_score` | `None` | optional inclusive composed-score floor; default Chat uses `0.144` |
| `relation_surfaces` | `None` | explicit caller-supplied relation surfaces |
| `include_source_context` | `False` | strict boolean; opt-in source time and anonymous existing role bindings in rendering |

## Anchor identification

```text
MiniLM dense ranking
+ bounded deterministic lexical ranking
+ entity-conditioned FAISS subset ranking over the complete eligible
  REFERS_TO subject/object/mention membership of the resolved entity refs
  (bounded returned candidates; adds candidates only)
-> RRF(k=60)
-> stable fused top_k anchors
```

A rebuildable lexical index selects and scores at most `max_nodes` matching
events across history. Exact entity-name matches support unsegmented Chinese;
generic CJK overlap is not a second semantic ranker. Lexical failure safely
falls back to dense-only. A rebuildable name index supports bounded multiple
identities and aliases without treating local pronouns as global names.

Entity membership is another rebuildable backend view over authoritative graph
relationships and available EVENT vector positions in the existing single
FAISS index. It is built on load and maintained by relationship writes and
vector repair; it changes no stored facts, identities or persistence format.
Sorted immutable sparse position buffers keep cached selectors alive. A single
ref uses `IDSelectorArray`; multiple refs combine cached `IDSelectorBatch`
selectors with native OR, so overlapping roles/refs cannot duplicate results.
The bounded returned set is ordered by distance and stable evidence ID;
equal-distance cutoff membership remains deterministic for the fixed vector
positions and sorted selectors. Adjacency insertion order is not eligibility.

Normal queries reuse this view without enumerating entity history. Missing or
stale membership disables only the entity channel until an owner load/write
rebuild; it never falls back to a truncated neighbor prefix. Building and
updating the derived membership may inspect the full stored relationship set
or affected members. Sparse selector storage is proportional to memberships,
not copied embeddings. Native search and selector union costs depend on index
size, membership sizes and the number of queried refs; `max_nodes` does not
claim constant-time search or cap those internal computations. Private backend
statistics distinguish that work from bounded graph expansion and output.

`top_k` limits anchors only. The final public evidence total is separately
limited to `0..max_evidence_items`. Zero selected evidence is a successful
Recall result with empty rendering and no safe error; `recall_unavailable`
remains a distinct safe failure. This cardinality contract does not implement
automatic relevance abstention.

## Fixed traversal

The adapter uses lazy MAGMA adjacency under the existing depth/node budgets,
counting actual neighbor reads rather than materializing full neighbor lists.
It bypasses the upstream first-ten-path truncation. With positive graph depth,
explicit subject/object roles also permit one fixed two-fact evidence projection;
depth zero remains anchor-only. Inferred relationships are never written back.

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

These DTO fields expose no graph path, relation metadata, score, embedding,
MAGMA UUID, local path, or narrative context. The optional source-context
rendering below uses private candidate role bindings without adding persistent
EntityRefs to the public DTO or creating another Recall facade.

## Controlled relation compatibility

When a caller supplies `relation_surfaces`, the small controlled resolver maps
query surfaces and private GroundedMemoryUnit relation metadata to canonical
IDs. A resolved mismatch rejects the candidate before BGE. A compatible match,
an unresolved query, an unresolved memory relation, or absent metadata preserves
existing behavior. Compound query surfaces remain independent. Normal Chat does
not supply relation surfaces; this is a structured caller/future Mind seam, not
a free-text query parser.

## Context Linearization

BGE returns raw logits, including values that happen to lie in `[0, 1]`.
The Hindsight-style composition applies one numerically stable sigmoid to
each logit without inspecting other scores or batch boundaries. Normalized
retrieval scores are not calibrated probabilities of factual correctness.
Recency weights, the source-snapshot reference time and the inclusive final
floor remain unchanged; a newer source in a different candidate snapshot can
still change the recency contribution.

- Preserve selected score order and, by default, render plain role-labelled evidence text.
- Rendering obeys `max_chars`; facts are kept whole or omitted with honest
  `truncated=true`. A selected relationship endpoint requires its original
  bridge fact; the complete group must fit item and rendered-character limits.
  BGE scores the combined pair only if the complete marked query and both facts
  fit its fixed token window; otherwise it uses the original single fact.

With `include_source_context=True`, headers add `spoken_at` and `timezone` from
source provenance. This is the time of the source statement, not the time its
proposition became true; historical dates, conditions and exact wording stay in
the fact body. Missing or unusable time is labelled `unknown`, never replaced by
wall time. The header describes the canonical source turn, not every supporting
turn of a fact with multiple source refs.

The adapter assigns anonymous, result-local `subject_binding` and
`object_binding` labels from existing source-valid candidates' role refs. A
shared label preserves an existing binding across facts; labels do not establish
attributes or prove that different labels denote different real-world objects.
Missing or unresolved roles stay unlabelled. Ordinary mention refs and name
surfaces cannot supply roles or occupations. Any occupational distinction must
remain a complete original fact connected by the existing binding. Persistent
refs and backend objects stay private, and ingestion is unchanged.

All source-context header characters enter the same item/group packing budget
before selection. A larger header can cause a whole fact or chain to be omitted;
no header is appended after `max_chars` has been enforced. The default `False`
branch keeps the prior speaker-only headers unchanged.

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

- Default Chat uses the fixed BGE reranker and inclusive
  `final_min_score=0.144`; neither constitutes a reliable semantic no-answer
  contract. Explicit callers can remove the floor through the existing policy;
  source-context rendering does not change BGE, Hindsight or candidate retrieval.
- No automatic intent/query classification, free-text query relation parser,
  general cross-conversation identity resolution, or Recall scheduler.
- No Evidence Organizer/Ledger, conflict/current-state resolver, fact
  supersession, or semantic deduplication.
- Explicit source-supported aliases/coreference within the bounded Formation
  window can share identity. Unresolved references retain their occurrences;
  there is no general coreference solver.
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
