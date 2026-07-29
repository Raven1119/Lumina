# Current Status

## Complete

- mock model mode by default;
- explicit MiniMax Anthropic-compatible adapter;
- same-origin browser chat frontend served by FastAPI at `/`;
- optional `.env.local` loading with process-environment precedence;
- safe provider fallback and truthful mock/model/fallback API semantics;
- append-only, restart-persistent Hot Draft JSONL;
- native Draft Turn V2 provenance for new user and assistant/fallback turns,
  including stable IDs, separate aware UTC timestamps, validated IANA source
  timezone, and truthful timezone source;
- segment-oriented pending/consumed Cold Draft JSONL;
- pair-aware, Cold-first logical compaction that copies complete V2 turn
  provenance without regenerating IDs or replacing timestamps;
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
  real MAGMA persistence, bounded English/Chinese Recall, restart recovery,
  idempotency, leak checks, and safe cleanup in one isolated sandbox;
- a Lumina-owned, MAGMA-style deterministic Chinese temporal parser with
  per-turn IANA calendar semantics, half-open aware-UTC intervals,
  longest-span multi-mention extraction, and unified Chinese/English write
  metadata. It performs no LLM, network, or embedding work and leaves upstream
  MAGMA unchanged;
- bounded Recall without the rejected cosine threshold experiment. The
  experiment found no recommended threshold, so its policy fields, vector
  interception, scorer, calibration CLI, and E2E branch were removed;
- optional production Recall injection into model requests, disabled by
  default. When enabled, it injects only the existing bounded
  `MemoryContext.rendered_text`; empty results or initialization/Recall failures
  fall back to normal chat, and the injected block is not persisted to Draft.

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
- Conversation Memory Recall remains behind its Lumina-owned facade. Anchors are
  now selected by RRF over the existing dense ranking and a deterministic
  lexical ranking. The first lexical implementation scans at most `max_nodes`
  graph entries in stable persisted order, admits only evidence-projectable
  `EventNode` records, and falls back to dense-only anchors if lexical fusion is
  unavailable. `top_k` limits the fused anchor total. Qualified non-anchor
  traversal events can enter `MemoryContext.evidence` after all anchors, while
  `max_evidence_items` limits total public evidence. A caller-supplied
  `temporal_window` remains a hard half-open `[start, end)` constraint on dense,
  lexical, and graph-expansion candidates; it is not an independent temporal
  rank source. MAGMA `narrative_context` remains excluded from public output;
- legacy Hot/Cold records remain role/text or segment-time only; they are not
  migrated, and Dream marks their deterministic segment-time projection as
  `legacy_segment_fallback`;
- production Draft records still have no real conversation/thread ID, so Dream
  continues to use the documented stable segment-derived conversation ID;
- real-model mode supports one MiniMax Anthropic-compatible adapter; incomplete
  or unsupported explicit configuration falls back to mock mode.

## Known Limits

- Hot Draft remains physically append-only;
- preservation markers currently have no global count cap;
- local Draft files have no multi-process transaction or writer lock;
- Dream and the file-backed Conversation Memory checkpoint assume one active
  writer;
- Recall remains a fixed bounded pipeline without a no/light/deep scheduling
  layer, evidence-sufficiency escalation, or edge/depth selection;
- `intent`, `beam_width`, and `drop_threshold` remain execution-inactive reserved
  controls. Recall has no automatic temporal-query parsing, independent temporal
  ranking, Adaptive Traversal, Beam Search, or Context Linearization;
- Recall has no post-retrieval Evidence Organizer for semantic duplicate merging,
  current-versus-historical state separation, conflict presentation, timeline
  organization, or evidence sufficiency;
- request size and total logical context have no application-level global bound;
- Hot and Cold JSONL reads scan their files rather than using an indexed store.

## Not Started

- Conversation Graph;
- PostgreSQL memory;
- ContextBuilder or ToolRuntime;
- other organs, agents, tasks, schedulers, or workers.
