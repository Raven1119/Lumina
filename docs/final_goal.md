# Lumina Final Goal

Lumina is intended to become a local-first companion whose continuity is earned
through reliable behavior, not simulated by tone alone.

The current reboot deliberately begins with one small promise: a conversation
must survive context pressure and process restart without silently losing the
raw material that was compressed out of the immediate prompt.

## Continuity Before Intelligence

The first trustworthy form of continuity is not long-term factual memory. It is
the ability to preserve what actually happened.

Lumina therefore begins with a Draft System:

```text
Hot Draft
-> preserve older raw turns into Cold Draft
-> advance logical compaction state
-> retain recent raw turns for the next response
```

The order is part of the product promise. Hot Draft may be compressed, but the
matching raw segment must already exist in Cold Draft. A failed preservation
must leave the Hot Draft view uncompressed rather than trade detail for a
smaller context.

## Current Public Goal

The current MVP should provide:

- one local browser chat path;
- explicitly configured real-model use with a safe mock fallback;
- restart-persistent user and assistant turns;
- bounded recent raw context;
- pair-aware, Cold-first logical compaction;
- durable Cold Draft segments that stay internal;
- safe failure behavior with no credential, path, traceback, or raw Draft
  exposure.

This is the foundation of continuity, not a claim of long-term memory. Cold
Draft preserves source material; it does not decide facts, retrieve memories,
or interpret the relationship.

## Growth Rule

Future work must preserve this invariant rather than route around it. No later
summarizer, storage backend, memory system, or background process may mark raw
conversation material as compacted before its durable preservation succeeds.

The active Cold Draft contract is defined in `docs/COLD_DRAFT.md`. Current
implementation facts belong in `docs/CURRENT_STATUS.md`.
