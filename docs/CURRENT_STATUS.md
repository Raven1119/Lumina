# Current Status

## Complete

### Chat and Draft

- deterministic mock mode by default;
- explicit MiniMax Anthropic-compatible adapter;
- one UTF-8 Chat background file at `prompts/chat_background.md`, loaded once
  during application construction and injected into every normal Chat model
  generation through the provider-native `system` field;
- Chat background changes take effect after restart and remain isolated from
  Hot Draft, Cold Draft, rolling summary, Recall queries, Dream, MAGMA,
  checkpoints, public API responses, logs, and safe exceptions;
- same-origin browser chat served by FastAPI at `/`;
- `GET /api/status`, `POST /api/chat`, and synchronous
  `POST /api/dream/run`;
- optional `.env.local` loading with process-environment precedence;
- safe provider fallback and truthful mock/model/fallback response semantics;
- restart-persistent Hot Draft JSONL containing one rolling semantic summary
  plus the recent raw-turn tail after compaction;
- native Draft Turn V2 provenance with stable IDs, separate aware UTC
  timestamps, validated IANA source timezone, and truthful timezone source;
- Cold Draft JSONL with one original `cold_turn` per line and logical
  `pending_digest`/`consumed` segments reconstructed by shared segment ID;
- pair-aware, Cold-first rolling semantic compaction with default 24/12
  threshold/tail behavior and complete turn-provenance preservation;
- restart recovery and idempotent retry after Cold append succeeds but Hot
  replacement fails;
- atomic Cold segment append and whole-segment consumed transitions using a
  same-directory unique temporary file, flush, fsync, and file replacement;
- `compaction.running` in `GET /api/status` reflects only a real compaction;
- temporary 500 ms status polling exists only during an in-flight Chat request,
  with late-response protection and one backend status refresh after completed;
- verified two-round browser behavior: running/completed notices appeared twice,
  pending changed 0 to 1 to 2, Hot retained one updated summary plus its tail,
  and Cold produced two logical turn-line segments, with 14 lines in the first;

### Dream and ingestion

- manually triggered, synchronous, bounded Dream ingestion;
- native browser maintenance row for explicit Dream runs and bounded pending
  status;
- `DreamRunner.run_once(policy) -> DreamRunReport` as a reusable structured
  application boundary;
- default serial policy of `max_segments=10`, `stop_on_error=False`, and
  `ingestion_version="dream-v1"`;
- pinned, unmodified upstream MAGMA;
- durable `(segment_id, ingestion_version)` checkpoints;
- one MAGMA event per conversation turn;
- memory-complete-before-consumed ordering;
- restart-safe, idempotent recovery when memory completes before the Cold Draft
  state transition;
- one app-owned Cold Draft store and the same Conversation Memory
  adapter/backend shared by in-app Dream and Chat Recall;
- successfully ingested memory is visible to enabled Chat Recall without a
  process restart;
- one process-local, nonblocking writer mutex serializes complete Chat requests
  and Dream runs and returns a safe `409 Conflict` when busy;
- `GET /api/status` exposes Recall-enabled state plus bounded Dream
  availability, running state, pending count, and truncation state;
- Dream run responses contain safe aggregate counts only;
- the verified two-segment browser run reported `attempted=2`, consumed both
  segments atomically, reduced pending to zero, and preserved working Recall.

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

- rolling compaction bounds the recent raw-turn tail, but the generated rolling
  summary and total model-facing context have no independent global character
  cap;
- Draft persistence failures fail soft while the public response can still
  report `message_consumed=true`;
- production Draft records have no real conversation/thread identity and use a
  documented stable segment-derived fallback for Dream ingestion;
- legacy role/text Hot records remain readable; the obsolete nested Cold
  `turns[]` format is neither read nor migrated;
- real-model mode supports one explicit provider adapter.

## Known Limits

- Hot compaction atomically rewrites the file; there is no cross-process
  transaction around that replacement;
- user and assistant Draft writes are not transactional as a pair;
- local JSONL stores have no cross-process transaction or writer lock;
- the process-local writer mutex does not coordinate multiple processes;
- `--reload`, `--workers 2` or another multi-worker configuration, and an
  external Dream CLI running concurrently with the service violate the current
  single-writer assumption;
- MAGMA graph/vector persistence is not protected by a cross-process lock or a
  multi-file transaction;
- Hot and Cold JSONL reads scan files rather than using an indexed store;
- Cold pending status counts complete reconstructed segments rather than
  physical `cold_turn` lines;
- compaction status polling is temporary and Chat-scoped; there is no
  background job, permanent polling, WebSocket, or SSE;
- no-answer controls still return evidence; Recall does not determine whether
  memory is sufficient to answer;
- `GENERAL` is an upstream entity-biased fallback, not a neutral relationship
  weighting;
- no generic query-time Temporal Anchor Ranking is available in the pinned
  upstream MAGMA checkout;
- `WHY` has no trusted causal specialization;
- there is no Evidence Organizer, state reconciliation, contradiction handling,
  memory sufficiency gate, or autonomous Recall scheduler.

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
