# Current Status

## Complete

### Chat and Draft

- deterministic mock mode by default;
- explicit MiniMax Anthropic-compatible adapter;
- same-origin browser chat served by FastAPI at `/`;
- `GET /api/status` and `POST /api/chat`;
- optional `.env.local` loading with process-environment precedence;
- safe provider fallback and truthful mock/model/fallback response semantics;
- append-only, restart-persistent Hot Draft JSONL;
- native Draft Turn V2 provenance with stable IDs, separate aware UTC
  timestamps, validated IANA source timezone, and truthful timezone source;
- segment-oriented `pending_digest`/`consumed` Cold Draft JSONL;
- pair-aware, Cold-first logical compaction that preserves complete turn
  provenance;
- restart recovery and idempotent retry after Cold append succeeds but
  compaction-state advancement fails.

### Dream and ingestion

- manually triggered, synchronous, bounded Dream ingestion;
- `DreamRunner.run_once(policy) -> DreamRunReport` as a reusable structured
  application boundary;
- default serial policy of `max_segments=10`, `stop_on_error=False`, and
  `ingestion_version="dream-v1"`;
- pinned, unmodified upstream MAGMA;
- durable `(segment_id, ingestion_version)` checkpoints;
- one MAGMA event per conversation turn;
- memory-complete-before-consumed ordering;
- restart-safe, idempotent recovery when memory completes before the Cold Draft
  state transition.

### Conversation Memory Recall

- Lumina-owned ingestion and Recall DTOs and adapter boundaries;
- bounded dense and lexical anchor rankings fused with MAGMA-style RRF;
- aware-UTC half-open `temporal_window=[start,end)` hard filtering;
- fixed graph traversal and explicitly enabled adaptive relation-aware beam
  traversal;
- supported explicit intents `GENERAL`, `WHEN`, `ENTITY`, and `WHY`, with
  `WHY` currently using `GENERAL` behavior;
- deterministic Context Linearization: `WHEN` is chronological while
  `GENERAL`, `ENTITY`, and `WHY` preserve retrieval order;
- stable evidence IDs, provenance validation, evidence and character budgets,
  deterministic ordering, and safe internal projection;
- optional production chat Recall injection, disabled by default;
- only bounded `MemoryContext.rendered_text` is injected;
- empty, failed, unavailable, or corrupt Recall safely falls back to ordinary
  chat;
- recalled text is not written back into Draft as a new memory source;
- real-MAGMA restart, idempotency, leak, English/Chinese temporal, and
  end-to-end acceptance coverage.

### Completed evaluations and audits

- graph expansion depth evaluation completed: depth 1 improved evidence
  coverage over anchor-only retrieval on the synthetic fixture; depth 2 added no
  further benefit;
- adaptive traversal evaluation completed: the upstream fallback policy improved
  coverage, ranking, noise, and context size on the synthetic fixture at higher
  but still low absolute latency;
- Context Linearization completed without changing retrieved evidence sets;
- GENERAL relation-weight ablation completed; the upstream entity-biased
  fallback was retained because the balanced candidate reduced overall Recall
  and NDCG, increased noise, and materially hurt temporal questions;
- MAGMA Temporal Anchor Ranking audit completed; no safe generic upstream
  implementation exists, so no custom ranking algorithm was introduced;
- Dream UI code audit completed with result `READY`.

## Partial

- Dream is available only through the CLI; there is no browser button or HTTP
  Dream endpoint;
- Chat, Dream, and Cold Draft read-modify-write paths do not yet share an
  application-level writer mutex;
- concurrent Chat/Chat, Chat/Dream, or Dream/Dream writes are unsafe;
- the current external Dream CLI creates its own memory adapter/backend, so a
  running Chat retriever may retain an old in-memory graph/vector snapshot until
  it is rebuilt or the process restarts;
- `/api/status` does not expose Dream running state, bounded pending count, or
  truthful Recall-enabled state;
- pending count has no streaming, no-body, early-stop store operation;
- logical compaction bounds the recent raw-turn tail, but accumulated
  preservation markers leave total model-facing context without a global cap;
- Draft persistence failures fail soft while the public response can still
  report `message_consumed=true`;
- production Draft records have no real conversation/thread identity and use a
  documented stable segment-derived fallback for Dream ingestion;
- legacy records remain readable but are not migrated;
- real-model mode supports one explicit provider adapter.

## Known Limits

- Hot Draft remains physically append-only;
- user and assistant Draft writes are not transactional as a pair;
- local JSONL stores have no cross-process transaction or writer lock;
- single Uvicorn worker does not prevent concurrent request threads;
- `--reload`, multiple workers, and an external concurrent Dream CLI violate the
  current single-writer assumption;
- MAGMA graph/vector persistence is not protected by a cross-process lock or a
  multi-file transaction;
- Hot and Cold JSONL reads scan files rather than using an indexed store;
- no-answer controls still return evidence; Recall does not determine whether
  memory is sufficient to answer;
- `GENERAL` is an upstream entity-biased fallback, not a neutral relationship
  weighting;
- no generic query-time Temporal Anchor Ranking is available in the pinned
  upstream MAGMA checkout;
- `WHY` has no trusted causal specialization;
- there is no Evidence Organizer, state reconciliation, contradiction handling,
  memory sufficiency gate, or autonomous Recall scheduler.

## Next Production Objective

Implement one minimal, manually triggered Dream control in the existing browser
frontend while preserving current Dream semantics:

```text
existing DreamRunner.run_once
+ app-owned ColdDraftStore
+ shared Conversation Memory adapter/backend
+ one Chat/Dream process-local writer mutex
+ POST /api/dream/run
+ expanded GET /api/status
+ bounded no-body pending count
+ native frontend maintenance row
```

The first implementation must remain synchronous and bounded, use the existing
Dream policy defaults, return `409 Conflict` when the shared writer is busy, and
make successfully ingested memory immediately visible to the current Chat
retriever.

Deployment for that first version remains single worker, without `--reload`, and
without a concurrent external Dream CLI.

## Not Started

- automatic, scheduled, startup, background, or model-triggered Dream;
- cross-process writer locking or multi-worker support;
- Dream history, cancellation, progress streaming, or Memory Viewer;
- Conversation Graph as a separate organ;
- Evidence Organizer and memory-state reconciliation;
- Mind System Recall scheduling and policy generation;
- PostgreSQL memory;
- ContextBuilder or ToolRuntime;
- other organs, agents, tasks, schedulers, or workers.
