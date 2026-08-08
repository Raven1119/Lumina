# Current Status

> Snapshot: 2026-08-05
>
> Codebase baseline: `docs/LUMINA_CODEBASE_SCAN.md`.

## Product state

Lumina is a local, single-user, single-continuous-conversation chatbot runtime.
The current implemented loop is:

```text
Browser Chat
-> fixed background + rolling Hot context + optional Recall
-> user/assistant raw persistence
-> Cold-first rolling compaction
-> explicit Dream
-> shared MAGMA long-term memory
-> later bounded Recall
```

A separate History projection restores the original transcript after restart
without replaying it into the model context.

## Complete

### Chat and model

- same-origin native browser frontend;
- `GET /api/status`, `POST /api/chat`, `POST /api/dream/run`, and
  `GET /api/history`;
- deterministic mock mode and explicit MiniMax Anthropic-compatible mode;
- safe provider fallback without leaking credentials, payloads, paths, or
  tracebacks;
- startup-loaded `prompts/chat_background.md`, injected only as the native
  system prompt for normal Chat generations.

### Hot and Cold Draft

- canonical Hot path: `data/draft/hot_drafts.jsonl`;
- physical rolling Hot representation: zero or one current summary plus recent
  raw user/assistant turns;
- default 24/12 compaction rule, moving only complete user/assistant pairs;
- real model summarization using only the previous summary and current archived
  raw segment;
- Cold-first ordering: summary success, atomic Cold segment append, atomic Hot
  replacement, then non-authoritative compaction-state update;
- per-turn `cold_turn` JSONL storage with segment ID, continuous index/count,
  pending/consumed state, and original V2 provenance;
- atomic segment append and whole-segment consumed transition;
- browser compaction-running/completed/failed notices and immediate pending
  refresh;
- restart persistence and failure-window idempotency.

### Dream

- browser button and synchronous `POST /api/dream/run`;
- separate CLI entry for use only while the service is stopped;
- app-owned shared Cold store and shared Conversation Memory adapter/backend;
- one process-local nonblocking writer lock shared by complete Chat and Dream
  operations;
- default policy `max_segments=10`, `stop_on_error=False`,
  `ingestion_version="dream-v1"`;
- deterministic serial pending-segment processing;
- memory-complete-before-consumed ordering;
- retry convergence for partial ingestion and completed-memory/failed-consume
  windows;
- safe aggregate public result boundary.

### Conversation Memory and Recall

- pinned, unmodified upstream MAGMA at commit
  `467cb70b67ac337b22fdb42194d37c04ad701b62`;
- one source turn per MAGMA event;
- stable evidence IDs, complete source provenance, and durable
  `(segment_id, ingestion_version)` checkpoints;
- deterministic English/Chinese temporal normalization using each source turn's
  timestamp and timezone;
- MiniLM dense anchors plus bounded lexical anchors fused with RRF (`k=60`);
- aware UTC half-open temporal hard filtering;
- fixed traversal by default and explicit adaptive relation-aware traversal;
- `max_graph_depth=0` anchor-only support;
- final evidence total controlled by `max_evidence_items`, independently of
  anchor `top_k`;
- intent-aware Context Linearization with chronological `WHEN` rendering;
- optional Chat Recall injection, disabled by default, using only bounded
  `MemoryContext.rendered_text`;
- safe empty/failure fallback and no recalled-text persistence into Draft;
- real-MAGMA synthetic E2E, restart Recall, and Dream idempotency validation.

### Single-conversation History

- read-only `GET /api/history`;
- original transcript reconstructed from valid Cold raw turns plus current Hot
  raw turns;
- stable `turn_id` deduplication with Cold winning overlaps;
- rolling summary and internal metadata excluded;
- default 40-item page, maximum 100, exclusive `before` turn-ID cursor;
- restart restoration, initial latest-page load, upward lazy loading, and scroll
  position preservation;
- Dream pending-to-consumed transitions do not remove transcript entries;
- History calls do not invoke the model, Recall, Dream, or store mutation.

### Validation snapshot

The 2026-08-05 codebase scan recorded:

- root suite: 169 passed, 0 skipped;
- Conversation Memory: 70 passed, 0 skipped;
- Dream: 32 passed, 0 skipped;
- Recall E2E: PASS, 9/9 queries, restart and idempotency PASS;
- `git diff --check`: PASS;
- upstream MAGMA status/diff: clean.

## Partial support

- Cross-turn reference: nearby antecedent and anaphoric turns can often be
  jointly recalled through dense/lexical anchors and temporal/semantic graph
  adjacency, but there is no explicit coreference resolution, segment-aware
  context link, or general ordering guarantee.
- Knowledge update: old and new events can both be recalled and interpreted by
  the final model, but there is no fact supersession, conflict resolution, or
  current-state truth layer.
- Adaptive GENERAL: uses validated ENTITY-biased upstream fallback weights;
  temporal adjacency may be pruned and `WHY` has no causal specialization.
- No-answer behavior: Recall is source-valid and bounded but may still return
  full evidence for an absent answer; there is no reliable abstention.
- Failure recovery: Cold-first ordering and stable IDs cover known retry windows,
  but the system is not ACID across Hot, Cold, graph, vectors, and checkpoint.

## Known limitations

- one process, one worker, no `--reload`, no concurrent external Dream CLI;
- no cross-process lock or multi-instance coherence;
- user and assistant Hot appends are not one pair transaction;
- public `message_consumed` does not fully represent Draft persistence success;
- Hot raw append is not fsync-based;
- History/status scan JSONL files and do not use an index or consistent
  cross-file snapshot;
- MAGMA graph, vectors, and ingestion checkpoint are not one cross-file
  transaction;
- Mock mode cannot generate rolling summaries; compaction safely fails and
  preserves Hot when the threshold is reached;
- no unified global token/character budget over background, summary, recent raw,
  Recall, and current user input;
- no real-user long-term Recall/answer-quality or production-scale performance
  evaluation.

## Not implemented

- automatic, scheduled, startup, background, or model-triggered Dream;
- Mind System and Recall scheduler;
- automatic intent/query classification or none/light/deep routing;
- evidence sufficiency escalation or reliable abstention;
- Evidence Organizer/Ledger, semantic deduplication, current-state/conflict
  handling, fact supersession, or timeline synthesis;
- explicit coreference resolution;
- multiple conversations, multiple users, multi-worker deployment, or database
  transactions;
- Conversation Graph, PostgreSQL memory, ContextBuilder, ToolRuntime, Execution,
  Focus, Method, Evolution, agents, tasks, schedulers, or workers.

## Current development boundary

Memory v1 should be treated as frozen at the ownership, DTO, provenance,
Cold-first, Dream, and bounded Recall-facade boundaries. Modify it only for a
reproducible correctness/data-loss bug, a real Recall failure sample, or a
minimal caller-contract need.

The recommended next stage is a **docs-first Mind System caller-contract design**
covering:

```text
whether to Recall
intent / temporal window / budget selection
anchor-only vs graph-enhanced depth
how to judge evidence sufficiency
when to request another bounded Recall
```

This is a design boundary, not authorization to implement autonomous routing,
Evidence Organizer, automatic Dream, workers, or agents.
