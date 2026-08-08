# Lumina Final Goal

Lumina is intended to become a local-first digital companion whose continuity is
built from durable evidence, integrated cognition, relationships, experience,
and action rather than simulated by tone alone.

The long-term direction is defined more fully in `docs/NORTH_STAR.md`. This file
sets the current product direction and the next bounded development boundary; it
does not claim that future organs already exist.

## Continuity foundation

The implemented continuity chain is:

```text
normal Chat
-> stable background system prompt
-> rolling Hot working context
-> Cold-first immutable raw archive
-> explicit Dream
-> persistent MAGMA event memory
-> optional bounded Recall
-> later Chat
```

A separate History projection restores the original single-conversation
transcript after restart without replaying the entire transcript into the model.

## Current baseline

The current product already provides:

- a local single-user, single-continuous-conversation chatbot;
- fixed background injection for every normal Chat generation;
- rolling semantic Hot Draft plus recent raw turns;
- per-turn Cold Draft raw archive with segment-level atomic state changes;
- explicit browser Dream and stop-the-service CLI Dream;
- shared resident memory backend and process-local Chat/Dream write exclusion;
- pinned MAGMA event/graph/vector persistence with provenance and idempotency;
- dense + lexical RRF anchors, temporal hard filters, fixed/adaptive traversal,
  bounded evidence, and Context Linearization;
- default-disabled safe Recall injection;
- restart-persistent read-only transcript history with cursor pagination.

The current system is a long-memory chatbot prototype. It is not yet a complete
Mind System, autonomous agent runtime, or digital life.

## Memory v1 freeze

Freeze the following unless a later task identifies a reproducible defect, a
real failure sample, or a narrow caller-contract requirement:

```text
Cold-first raw evidence authority
one source turn -> one MAGMA event
stable source provenance and evidence IDs
(segment_id, ingestion_version) idempotency
Lumina-owned MemoryIngestor / MemoryRetriever facade
bounded Recall and safe failure
pinned unmodified MAGMA backend
```

Freezing this boundary does not mean memory quality is final. Known open
problems include abstention, state/conflict interpretation, fact supersession,
explicit coreference, real-user quality evaluation, and cross-file transaction
safety.

## Next development boundary

The next stage should begin with a **Mind System caller-contract design**, not
another memory-backend expansion.

The caller must eventually decide:

```text
whether memory is needed
which Recall intent applies
whether a temporal window is known
anchor-only or graph-enhanced search depth
node/evidence/context budgets
whether returned evidence is sufficient
whether another bounded Recall is justified
```

The first step is docs-only: define the ownership boundary, inputs, outputs,
failure behavior, and synthetic acceptance cases around the existing
`MemoryRetriever.recall(query, policy) -> MemoryContext` interface.

Do not yet implement:

- automatic intent or query classification;
- autonomous Recall scheduling;
- Evidence Organizer/Ledger;
- automatic Dream;
- workers, queues, background cognition, or agents;
- conflict/current-state resolution or fact supersession;
- self-modification or evolution.

These require separate design and authorization.

## Growth rules

Later organs must extend rather than bypass the implemented continuity
foundation:

1. Cold raw evidence remains authoritative for what was actually said.
2. Hot remains a bounded rolling working representation, not the complete
   transcript authority.
3. Dream remains separate from normal Chat until an explicit later design
   changes that ownership.
4. Memory completion is durable before a Cold segment is consumed.
5. Recall remains bounded, provenance-preserving, leak-safe, and optional for
   Chat availability.
6. The future Mind owns decisions about attention and memory use; the memory
   backend should not become an implicit global router.
7. New capabilities must preserve identity continuity, auditability, safe
   failure, and future evolvability.

Current implementation facts belong in `docs/CURRENT_STATUS.md`. The current
codebase-wide evidence baseline is `docs/LUMINA_CODEBASE_SCAN.md`.
