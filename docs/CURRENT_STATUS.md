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
  rank source. With `intent`, `beam_width`, and `drop_threshold` all unset,
  Recall keeps the existing fixed traversal. Supplying any one of them enables
  bounded Adaptive Traversal. `GENERAL` uses upstream MAGMA's actual unknown-
  intent fallback (the ENTITY weight table), `WHEN` prioritizes temporal links,
  `ENTITY` prioritizes entity links, and `WHY` deliberately behaves as
  `GENERAL` without causal specialization. The MAGMA-derived execution uses
  beam width `10`, drop threshold `0.15`, and cumulative
  `0.6 * relation + 0.4 * cosine` transition scoring. Adaptive failure falls
  back to fixed traversal; a subsequent fixed failure retains safe-empty
  behavior. A caller-supplied explicit `intent` also enables deterministic
  Context Linearization after the bounded evidence set is fixed: `WHEN` orders
  evidence by the actual UTC event instant and then `evidence_id`, while
  `GENERAL` and `ENTITY` preserve retrieval order and `WHY` continues to behave
  as `GENERAL`. Explicit-intent model context renders each item as
  `[UTC timestamp] original text`; with `intent=None`, the previous flat
  newline-only evidence format and ordering remain unchanged. There is no
  causal linearization, Evidence Organizer, or narrative generation, and MAGMA
  `narrative_context` remains excluded from public output;
- in one bounded 48-turn/20-question synthetic comparison, fixed depth-1 versus
  explicitly enabled `GENERAL` Adaptive Traversal changed evidence Recall from
  `0.620370` to `0.791667`, complete-hit from `0.388889` to `0.611111`, NDCG
  from `0.495872` to `0.675608`, strict-gold noise ratio from `0.801667` to
  `0.590000`, mean evidence count from `5.85` to `3.95`, and mean rendered
  characters from `418` to `289.2`; mean local latency increased from
  `12.4608 ms` to `25.4399 ms`. Both no-answer controls still returned six
  evidence items, so this synthetic result demonstrates a bounded noise/context
  improvement but not reliable abstention or general production effectiveness;
- a one-off Context Linearization serialization check over the same 20-query
  synthetic fixture covered 117 evidence items. All `100/100` mode-query
  comparisons retained the same bounded evidence-ID set; `GENERAL`, `ENTITY`,
  and `WHY` each preserved retrieval order in `20/20` queries, while `WHEN`
  produced strict event-time order in `20/20` and differed from the original
  retrieval order in `20/20`. Mean rendered characters increased from `416.55`
  to `551.10` (`+32.301%`); both forms had zero truncations, and repeated-run
  fingerprints matched. This check validates deterministic serialization only;
  it does not establish answer-quality or retrieval-effectiveness gains;
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
- Recall has no no/light/deep scheduling layer, evidence-sufficiency escalation,
  automatic intent routing, or dynamic edge/depth selection. Adaptive Traversal
  requires explicit caller fields and provides no trustworthy causal Recall;
- Recall has no automatic temporal-query parsing or independent temporal
  ranking. Context Linearization requires an explicit caller-provided intent and
  does not infer intent, causality, current state, conflicts, or sufficiency;
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
