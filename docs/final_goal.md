# Lumina Final Goal

Lumina is intended to become a local-first companion whose continuity is earned
through durable, inspectable behavior and increasingly accurate use of its own
history.

The long-term direction remains defined by `docs/NORTH_STAR.md`. The current
product-development goal is narrower: **make durable conversation memory
reliably retrievable and useful during ordinary chat without weakening the
existing persistence and failure-isolation guarantees.**


## Target Platform

The user set the following platform direction for Noespire on 2026-09-22:

- Noespire's application runtime targets Windows.
- All of its Codex processes run in a Linux environment.
- Development also takes place in Linux using Codex, with the user's goal of
  making full use of Astra's capabilities.

Treat Windows application execution and Linux Codex execution as distinct
environments when designing process invocation, filesystem access, and
integration. The choice of Linux hosting and cross-environment communication
remains open. This is a target-platform decision; Windows compatibility must
be validated on Windows before being reported as supported.

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

Source-verified entry points and owners are maintained in the [code map](../README.md).
The real-model Memory default is now `grounded-formation-v6` + FirstHit +
`reliable-v2`; it bypasses legacy BGE/Hindsight and has no final score floor.
Chat stays separate from the foreground Mind–Nervous–Execution chain.
`ExecutionOrgan` supports both the chain's execution owner and the manual API.
Dream remains explicit and Cold-first continuity remains unchanged.

## Current Product Objective: Make the maintained system understandable

The current task consolidates responsibilities and the latest implementation of
each independent candidate route. The [scheme catalog](EXPERIMENTS.md) records
entry points, comparison conditions, evidence and historical replacements.
It does not promote a candidate, change a runtime model, or connect new organs.

The earlier 29/36 Grounded Write versus 26/36 raw-turn result belongs to its
historical authorization-aligned Memory version; it is not a v6 measurement.
Likewise, current mechanical correctness does not establish general answer
quality or autonomous capability. Historical results remain in their original
[Memory](MEMORY_EXPERIMENT_HISTORY.md) and [cognitive](../Mind/docs/EXPERIMENT_HISTORY.md)
records.

## Recall Optimization Principles

1. **Source-first.** Reuse official algorithm/source implementations before
   inventing new Recall logic.
2. **One variable at a time.** Separate anchor, routing, traversal, ranking,
   admission, and graph-formation failures.
3. **No benchmark patching.** Do not tune production behavior to individual
   fixture strings, entities, or final-regression score gaps.
4. **Keep each comparison component fixed.** The current reliable reader has no
   BGE step; explicit legacy routes retain their own fixed BGE policy.
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
separate Chat and cognitive-chain runtime boundaries.
