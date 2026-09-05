# Lumina Final Goal

Lumina is intended to become a local-first companion whose continuity is earned
through durable, inspectable behavior and increasingly accurate use of its own
history.

The long-term direction remains defined by `docs/NORTH_STAR.md`. The current
product-development goal is narrower: **make durable conversation memory
reliably retrievable and useful during ordinary chat without weakening the
existing persistence and failure-isolation guarantees.**

## Continuity Invariant

Cold-first preservation remains non-negotiable:

```text
Hot Draft
-> durably preserve older raw turns in Cold Draft
-> advance logical compaction state
-> retain the recent raw tail
```

Cold source records remain authoritative evidence. Recall optimization must not
rewrite, delete, summarize in place, or reinterpret those source records.

Each native user/assistant turn preserves stable identity and truthful time
provenance through Draft, Dream, MAGMA, Recall, and final evidence projection.

## Current Baseline

The user-facing memory loop is now connected:

```text
Production chat
Browser -> FastAPI -> MessageRuntime
        -> bounded Recall
        -> optional MemoryContext injection
        -> ModelClient
        -> Hot Draft -> Cold-first compaction -> Cold Draft

Offline memory
manual Dream -> pending Cold Draft
             -> DeepSeek-V4-Pro Grounded Formation
                (non-thinking, max_tokens=2000)
             -> grounding validator + bounded semantic fallback
                + value-only guard + self-name coverage guard
             -> durable GroundedMemoryUnit checkpoint before MAGMA
             -> span-grounded entity mention extraction
                + durable mention-binding checkpoint
             -> Conversation Memory adapter -> unmodified MAGMA
             -> consumed
```

Current production Recall is:

```text
query
-> target_entity_ref classification (CURRENT_USER -> E_001, or exact-surface
   lookup over persisted EntityNodes; 0/multi hit -> None)
-> keyword-enriched MAGMA dense anchors
-> bounded lexical anchors
-> entity-conditioned FAISS subset list when a target_entity_ref is present
-> RRF
-> fixed depth-1 bounded graph BFS
-> fail-open ControlledRelationResolver when relation surfaces are supplied
-> BGE rerank ([SAME_ENTITY] per-pair projection on equal refs)
-> Hindsight recency adjustment
-> final_score >= 0.144
-> stable bounded top-3 MemoryContext
```

Recall is enabled by source default, remains explicitly disableable, and fails
soft to normal chat when memory is empty or unavailable.

## Current Problem

The adopted memory loop works end to end. On the authorization-aligned 36-case
development subset, Grounded Write scores 29/36 versus the raw-turn baseline at
26/36, preserving all 6 currently authorized positive cases and improving
negative correctness from 20/30 to 23/30.

The remaining boundary is explicit: `ControlledRelationResolver` can use
caller-supplied relation surfaces, but normal Chat supplies none. Assistant
utterance alone is not verified fact/self-action provenance, and future
self-action memory waits for Execution Trace or tool-result provenance.

## Current Product Objective: Preserve the parent baseline around Execution

The Memory objective is met: the adopted loop is in production end to end,
including Recall in chat, the Mind Recall gate stage 2, Grounded Write with the
self-name coverage guard, and the generic multi-entity Entity graph.

Execution V1 is frozen as isolated experimental evidence at tag
`execution-organ-v1-final` and was never promoted into the production path.
The frozen and audited Execution V2 substrate is promoted as the supported
`Execution/` package behind `ExecutionOrgan`. It remains deliberately separate
from production Chat, Mind, Memory, and Dream; wiring any of those paths to
Execution requires a separate approved task.

The Memory-side boundary remains explicit: `ControlledRelationResolver` can
use caller-supplied relation surfaces, but normal Chat supplies none. Future
capabilities must preserve this seam unless separately authorized.

## Recall Optimization Principles

1. **Source-first.** Reuse official algorithm/source implementations before
   inventing new Recall logic.
2. **One variable at a time.** Separate anchor, routing, traversal, ranking,
   admission, and graph-formation failures.
3. **No benchmark patching.** Do not tune production behavior to individual
   fixture strings, entities, or final-regression score gaps.
4. **BGE remains the fixed reranker by default.** Replace or retrain it only
   when evidence isolates ranking as the bottleneck.
5. **MAGMA upstream stays pinned and unmodified.** Lumina may adapt behavior
   behind its own facade, but must not patch upstream.
6. **Recall remains bounded and fail-soft.** Better retrieval must not make
   normal chat depend on memory availability.
7. **Write-side memory stays stable during read-side experiments.** Dream,
   Cold-first ownership, and GroundedMemoryUnit persistence are not changed merely
   to improve a read benchmark.

## Quality Target

The objective is not a particular threshold or model score. The objective is:

```text
relevant historical evidence is retained
+ irrelevant/insufficient evidence is withheld
+ temporal and relation-sensitive queries behave correctly
+ results are deterministic, bounded, provenance-preserving, and restart-safe
```

The existing 60-case synthetic set is a development diagnostic, not a blinded
holdout and not proof of real-user Answer quality. It should be used to expose
failure strata and compare isolated changes. Production-quality claims require
independent evaluation and regression protection.

## Growth Rule

Later memory capabilities must extend, not bypass, these boundaries:

1. Cold remains the durable authority for conversation evidence leaving Hot.
2. Dream remains separate from synchronous chat.
3. Memory persistence and checkpointing remain idempotent before Cold consume.
4. Recall remains Lumina-owned, bounded, provenance-preserving, optional for
   chat availability, and leak-safe.
5. Retrieval quality should be improved before adding architectural complexity.
6. New mechanisms require reproducible failures that the smaller design cannot
   solve.

Conversation Graph as a separate production system, PostgreSQL/Neo4j,
autonomous Dream, schedulers, additional organs, and generalized memory
management are not implied by the completed Memory stage or the supported,
still-unwired Execution substrate.
