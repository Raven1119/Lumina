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

Public Recall evidence now contains vector anchors plus eligible non-anchor
event nodes discovered through existing MAGMA traversal paths. Anchors remain
first, repeated expansion nodes are deduplicated, invalid-provenance nodes are
skipped, `top_k` limits vector anchors, and `max_evidence_items` limits the final
public evidence total. Traversal paths, relation explanations, backend scores,
hop metadata, and MAGMA `narrative_context` remain internal.

## Next Production Objective

The next Conversation Memory objective is a small, bounded evaluation of the
Recall behavior that now exists. Before introducing any Recall scheduler,
Lumina must determine where graph-enhanced Recall improves evidence retrieval
and where it adds noise or cost:

```text
fixed memory corpus and fixed query set
-> anchor-only Recall with max_graph_depth = 0
-> graph-enhanced Recall with max_graph_depth > 0
-> compare evidence quality and Recall cost
```

The evaluation must remain narrow:

- use a small fixed set of direct-fact, historical-change,
  relationship/reason, and negative-control questions;
- hold `top_k`, `max_evidence_items`, and all non-depth settings constant;
- record target-evidence recovery, irrelevant evidence count, final evidence
  count, Recall latency, and the difference between the two conditions;
- evaluate the retrieval layer directly rather than answer style;
- use no LLM judge, cross-encoder, relevance threshold, or learned reranker;
- add no permanent benchmark framework, service, dashboard, result database, or
  new production interface;
- do not modify production Recall behavior as part of the evaluation;
- use the results only to decide whether a later minimal static route is
  justified.

A dynamic Recall scheduler, none/light/deep routing, query classification,
evidence-sufficiency escalation, and an Evidence Organizer remain later design
questions, not implied implementation work.

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
5. Vector anchors remain bounded by `top_k`; the final public evidence set,
   including eligible graph expansions, remains bounded by
   `max_evidence_items`.
6. Injected memory context remains model-only context and is not rewritten into
   user or assistant Draft records.
7. New storage, reasoning, or automation must not weaken durability,
   idempotency, truthful provenance, or safe fallback behavior.

Conversation Graph, PostgreSQL memory, ContextBuilder, ToolRuntime, autonomous
Dream, background scheduling, agents, tasks, dynamic Recall scheduling, and an
Evidence Organizer are not current capabilities or implied parts of the next
step.

The active Draft contract is defined in `docs/COLD_DRAFT.md`. Current
implementation facts belong in `docs/CURRENT_STATUS.md`; when plan and fact
differ, the status document must remain truthful about what is actually built.
