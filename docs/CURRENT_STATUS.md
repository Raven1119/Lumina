# Current Status

## Complete

- mock model mode by default;
- explicit MiniMax Anthropic-compatible adapter;
- same-origin browser chat frontend served by FastAPI at `/`;
- optional `.env.local` loading with process-environment precedence;
- safe provider fallback and truthful mock/model/fallback API semantics;
- append-only, restart-persistent Hot Draft JSONL;
- segment-oriented pending/consumed Cold Draft JSONL;
- pair-aware, Cold-first logical compaction;
- idempotent retry after Cold append succeeds but state advancement fails;
- restart recovery for Hot context, Cold pending segments, and compaction state;
- pinned, unmodified upstream MAGMA baseline in an isolated Conversation Memory
  environment;
- Lumina-owned Conversation Memory ingestion/recall DTOs and adapter, with
  durable `(segment_id, ingestion_version)` checkpoints, per-turn provenance,
  bounded recall, and synthetic real-MAGMA integration coverage;
- manually triggered, synchronous, bounded Dream ingestion of production-format
  `pending_digest` Cold Draft segments;
- durable-memory-before-consumed orchestration, including idempotent consumed
  transitions and restart recovery when memory completes before the Draft state
  transition;
- a developer-only, marker-owned Recall end-to-end acceptance harness that
  exercises production-format Hot/Cold Draft compaction, manual Dream ingestion,
  real MAGMA persistence, bounded recall, restart recovery, idempotency, leak
  checks, and safe cleanup in an isolated sandbox;
- an optional, default-off Lumina-owned cosine relevance gate with bounded
  candidate scoring, reuse of MAGMA's query embedding and persisted event
  vectors, structured failure behavior, and no LLM or network calls;
- a marker-owned real-MAGMA relevance calibration harness with a balanced
  20-positive/20-negative synthetic query set, full threshold metrics, and
  measured performance diagnostics.

The active Cold Draft preservation contract is reconciled with the MVP
implementation in `docs/COLD_DRAFT.md`.

## Partial

- logical compaction bounds the recent raw-turn tail, but accumulated
  preservation markers leave total model-facing context without a global bound;
- Hot Draft writes are attempted in user-then-assistant order, but a failed user
  write does not prevent the assistant write from being attempted;
- Draft persistence failures fail soft and remain internal, while the public
  response still reports `message_consumed=true`;
- Cold Draft pending segments can be consumed only by the explicit developer
  Dream command; there is no automatic, startup, background, or chat-time
  consumer;
- Conversation Memory recall exists only behind its isolated Lumina-owned
  facade and is not injected into the production chat/model request;
- current relevance calibration has no recommended threshold: positive scores
  overlap negative scores, and no scanned point simultaneously reaches 0.90
  positive recall and at most 0.10 false injection rate, so the gate remains
  disabled by default and E2E reports `threshold_not_recommended`;
- production Cold Draft records do not yet store a conversation ID, per-turn
  IDs, per-turn timestamps, or named timezone, so the manual converter uses
  documented stable IDs and the segment's aware `created_at` source timestamp;
- real-model mode supports one MiniMax Anthropic-compatible adapter; incomplete
  or unsupported explicit configuration falls back to mock mode.

## Known Limits

- Hot Draft remains physically append-only;
- preservation markers currently have no global count cap;
- local Draft files have no multi-process transaction or writer lock;
- Dream and the file-backed Conversation Memory checkpoint assume one active
  writer;
- request size and total logical context have no application-level global bound;
- Hot and Cold JSONL reads scan their files rather than using an indexed store.

## Not Started

- Conversation Graph;
- PostgreSQL memory;
- ContextBuilder or ToolRuntime;
- other organs, agents, tasks, schedulers, or workers.
