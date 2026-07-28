# Lumina Final Goal

Lumina is intended to become a local-first companion whose continuity is earned
through durable, inspectable behavior rather than simulated by tone alone.

The product promise is simple: preserve what happened, remember it only through
an explicit durable process, and use recalled context without making ordinary
conversation depend on the memory system being available.

## Continuity Invariant

Cold-first preservation remains the non-negotiable foundation:

```text
Hot Draft
-> durably preserve older raw turns in Cold Draft
-> advance logical compaction state
-> retain the recent raw tail for the next response
```

Hot Draft may be logically compacted only after the matching raw segment exists
in Cold Draft. If preservation fails, the logical Hot view must remain
uncompacted. Cold Draft source records are not summarized, rewritten, or
reinterpreted in place.

Every new user and assistant/fallback turn carries its own stable identity,
aware UTC timestamp, source IANA timezone, and truthful timezone source. That
provenance must survive Draft persistence, compaction, Dream ingestion, MAGMA
persistence, and Recall projection without substituting Dream time or segment
time.

## Current Baseline

The continuity foundation, offline memory loop, and minimal optional Recall
injection seam now exist:

```text
Production chat
Browser -> FastAPI -> MessageRuntime
        -> optional bounded Recall injection
        -> ModelClient
        -> Hot Draft -> Cold-first compaction -> Cold Draft

Offline memory
manual Dream -> pending Cold Draft -> Conversation Memory
             -> unmodified MAGMA -> durable checkpoint -> consumed

Recall
query -> bounded Lumina-owned facade -> empty or bounded MemoryContext
      -> optional bounded rendered-memory injection -> normal ModelClient call
```

The current system provides restart-persistent Draft state, native V2 turn
provenance, pair-aware Cold-first compaction, manual bounded Dream ingestion,
durable idempotency, deterministic English/Chinese temporal normalization, and
bounded Recall with stable evidence and safe failure behavior.

Conversation turns remain one MAGMA event each. Dream is manual, synchronous,
bounded, and single-writer; no ingestion occurs during chat or startup.

Recall injection is disabled by default. When explicitly enabled, only bounded
`MemoryContext.rendered_text` may become model-visible. Empty Recall,
initialization failure, unavailable dependencies, corruption, or Recall failure
falls back to ordinary chat. Injected memory context is not persisted into Hot
or Cold Draft.

The current public Recall evidence remains anchor-only. MAGMA graph traversal
runs internally, but traversal paths, narrative context, and expanded non-anchor
nodes are not yet projected into `MemoryContext`.

## Next Production Objective

The next Conversation Memory objective is one separately authorized, bounded
projection of eligible graph-traversal expansion nodes into Lumina evidence:

```text
MAGMA QueryContext
-> anchor nodes plus eligible non-anchor traversal expansions
-> bounded, stable Lumina-owned evidence projection
-> existing MemoryContext rendering
-> existing optional chat injection
```

The projection must satisfy all of the following:

- preserve anchor evidence and keep anchors ahead of graph expansions;
- admit only expansion nodes within existing traversal bounds and with complete
  source provenance;
- deduplicate anchor and expansion nodes deterministically;
- bound graph-expansion evidence separately while preserving the existing total
  evidence and rendered-size limits;
- preserve stable ordering based on graph distance and stable identifiers;
- expose only Lumina-owned evidence fields, never traversal paths, graph objects,
  backend scores, MAGMA UUIDs, embeddings, internal statistics, or raw Draft
  records;
- do not inject or depend on MAGMA `narrative_context`;
- keep the existing default-disabled, failure-safe Recall injection unchanged;
- keep Dream and ingestion completely outside `/api/chat`;
- add no Recall scheduler, none/light/deep modes, query classifier, evidence
  sufficiency escalation, Evidence Organizer, relevance threshold, LLM judge,
  cross-encoder, or adjacent memory feature.

This is the smallest step that makes MAGMA graph traversal capable of changing
public Recall evidence without weakening the current durability, provenance,
bounding, or failure-isolation contracts.

## Growth Rule

Later capabilities must extend these boundaries rather than route around them:

1. Cold Draft remains the durable authority for raw material leaving Hot
   context.
2. Dream remains separate from synchronous chat and consumes only eligible
   preserved segments.
3. Memory completion is established before a Cold Draft segment is marked
   consumed, using `(segment_id, ingestion_version)` for retry convergence.
4. Recall remains bounded, provenance-preserving, leak-safe, optional, and
   failure-isolated from normal chat availability.
5. Injected memory context remains model-only context and is not rewritten into
   user or assistant Draft records.
6. New storage, reasoning, or automation must not weaken durability,
   idempotency, truthful provenance, or safe fallback behavior.

Conversation Graph, PostgreSQL memory, ContextBuilder, ToolRuntime, autonomous
Dream, background scheduling, agents, tasks, dynamic Recall scheduling, and an
Evidence Organizer are not current capabilities or implied parts of the next
step.

The active Draft contract is defined in `docs/COLD_DRAFT.md`. Current
implementation facts belong in `docs/CURRENT_STATUS.md`; when plan and fact
differ, the status document must remain truthful about what is actually built.
