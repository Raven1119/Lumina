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
provenance through Draft, Memory v1 Dream, Recall, and final evidence projection.

## Current Baseline

The active Chat memory side is P8-derived Memory v1. Chat reads memory locally
on each enabled turn, uses answer_v5 on DeepSeek-V4-Pro, and records a trace
after storing its reply. Memory integration and pattern runs use
`deepseek-flash` with pinned BGE-M3 embeddings. Dream may run in the background
after a real reply once its cursor and threshold are ready; explicit API and
CLI entry points remain. Cold originals remain authoritative, while Memory
owns its own cursor and rebuildable index. [Code map](../README.md),
[design](../Conversation_Memory/docs/DESIGN.md), and
[results](RESULTS_memory_v1.md) record the current route.

Chat remains separate from the foreground Mind–Nervous–Execution chain.
`ExecutionOrgan` supports both that chain and the manual API. Old graph and
gate results remain historical evidence in [Memory history](MEMORY_EXPERIMENT_HISTORY.md).

## Recall improvement principles

1. Keep the original Cold source immutable and preserve turn provenance.
2. Compare one changed mechanism against frozen P8 conditions on both
   development sets; the R0 byte comparison and all-probe Chat/Lab equality
   are regression gates.
3. Use bounded, read-only, fail-soft recall so ordinary Chat still works when
   Memory or embedding is unavailable.
4. Keep Dream's graph writes and Chat's trace appends separate. A read-side
   change does not silently alter the write-side prompt or model.
5. Do not tune production behavior to individual probe strings or a score gap.

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

Future memory capabilities must retain Cold as original evidence, a durable
Memory cursor, bounded local recall, versioned prompts, and auditable model
costs. New algorithms need targeted evidence before promotion. Independent
cognitive-chain authority does not move into Chat as a side effect of a
memory change.
