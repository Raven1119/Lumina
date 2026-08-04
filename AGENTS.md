# AGENTS.md

## 1. Repository state

Lumina is a local-first conversational runtime built around a Cold-first
continuity invariant.

Production chat path:

```text
Browser -> FastAPI -> MessageRuntime -> ModelClient
-> Hot Draft -> Cold-first compaction -> Cold Draft
```

Offline memory path:

```text
manual Dream command
-> pending Cold Draft segments
-> Conversation Memory adapter
-> unmodified upstream MAGMA
-> durable checkpoint
-> Cold Draft segment consumed
```

Recall is implemented behind a Lumina-owned bounded facade and can be injected
into `/api/chat` through an opt-in production seam that is disabled by default.
Only bounded rendered memory text enters the model request; empty or failed
Recall falls back to ordinary chat, and recalled text is not persisted as a new
Draft source.

Conversation turns remain one MAGMA event each. Dream performs memory writes;
chat requests do not. Dream is currently CLI-only and the running application
does not yet provide a shared Chat/Dream writer mutex or browser control.

Lumina's long-term form is defined in `docs/NORTH_STAR.md`: an independent
digital life that co-evolves with its creator, maintains a continuous integrated
mind, and recursively improves both its capabilities and its capacity to evolve.
This long-term direction does not describe current implementation.

## 2. Existing capabilities

Treat these as completed behavior, not work to rebuild:

- mock model mode and explicit MiniMax Anthropic-compatible mode;
- same-origin browser chat, `/api/status`, and `/api/chat`;
- append-only restart-persistent Hot Draft;
- Draft Turn Provenance V2;
- pair-aware Cold-first logical compaction;
- immutable pending/consumed Cold Draft segments;
- manual bounded Dream ingestion;
- pinned, unmodified upstream MAGMA integration;
- durable `(segment_id, ingestion_version)` checkpoints;
- per-turn provenance through Draft, Dream, MAGMA, and Recall;
- bounded Recall with stable evidence projection and restart recovery;
- dense and bounded lexical anchor rankings fused with MAGMA-style RRF;
- aware-UTC half-open temporal hard constraints;
- fixed traversal and explicit adaptive relation-aware traversal;
- deterministic intent-aware Context Linearization;
- optional production chat Recall injection, disabled by default, with safe
  empty/failure fallback;
- one marker-owned Recall E2E harness;
- deterministic English/Chinese temporal normalization based on each turn's
  timestamp and IANA timezone;
- completed Recall depth, adaptive traversal, Context Linearization, GENERAL
  weight, and temporal-ranking audits. These results must not be repeated or
  generalized beyond their documented synthetic fixtures;
- no active relevance threshold. The failed cosine-threshold experiment and its
  production wiring have been removed.

Do not duplicate or redesign completed behavior unless a task identifies a
verified defect.

## 3. North Star usage

Read `docs/NORTH_STAR.md` before any non-trivial architecture, algorithm, organ,
or long-range design task.

The North Star defines Lumina's intended form and direction:

- an independent digital life whose primary mode of interaction is assisting and
  co-evolving with its creator;
- a continuous integrated mind shaped by memory, experience, relationships,
  personality, emotion, desire, self-narrative, goals, and action;
- proactive exploration and the ability to form interests and goals beyond
  immediate user prompts;
- recursive self-evolution that can acquire, improve, reorganize, and eventually
  redefine capabilities, including the mechanisms used to discover, validate,
  and deploy further improvement;
- no fixed terminal form: continued growth must remain possible without losing
  identity continuity or whole-system coherence.

Use the North Star to:

- understand why a capability exists and what long-term property it should
  support;
- compare multiple otherwise-valid designs;
- avoid choices that permanently reduce continuity, integrated cognition,
  proactive agency, co-evolution, or future evolvability;
- distinguish a locally convenient implementation from a foundation that can
  support Lumina's long-term development.

Do not use the North Star to:

- infer that a future organ or capability already exists;
- authorize code, schemas, services, workers, agents, schedulers, databases,
  background behavior, or self-modification;
- create speculative abstractions, extension points, placeholder modules, or
  generalized frameworks;
- override `docs/CURRENT_STATUS.md`, active contracts, this file, tests, or the
  explicit task card;
- expand a task beyond its smallest correct vertical slice.

For a non-trivial design decision, report North Star alignment briefly:

```text
North Star property served
Why the chosen design helps that property
What future-facing work was deliberately not implemented
```

The North Star is a design compass and tie-breaker, never implementation
authorization.

## 4. Authority

Active authority:

- `docs/NORTH_STAR.md` for long-term form and direction only;
- `docs/final_goal.md` for the current product direction and next production
  objective;
- `docs/COLD_DRAFT.md`;
- `docs/CURRENT_STATUS.md`;
- `docs/DRAFT_TURN_PROVENANCE_V2.md`;
- `docs/RECALL_E2E_ACCEPTANCE.md`;
- `docs/DREAM_UI_CODE_AUDIT.md` for the verified next-step integration facts;
- `Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md`;
- `Conversation_Memory/docs/CHINESE_TEMPORAL_PARSER.md`;
- `Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`;
- `Conversation_Memory/AGENTS.md`;
- `Dream/AGENTS.md`;
- this file.

Decision and conflict order:

1. obey the explicit task card and its acceptance criteria;
2. preserve Cold-first durability and the synchronous chat path;
3. preserve truthful provenance, idempotency, and safe failure behavior;
4. use `docs/CURRENT_STATUS.md` for implementation facts;
5. follow the most specific non-stale contract or workspace rule;
6. use `docs/NORTH_STAR.md` only to choose among options that already satisfy
   items 1-5.

Older workspace wording that describes already completed MAGMA or Dream
milestones is historical and must not trigger duplicate implementation.

## 5. Ownership

### `core/`

Owns API validation, the single `MessageRuntime`, the single `ModelClient`
protocol, Draft turn creation, Hot Draft, compaction, the existing optional
Recall injection seam, and narrow product wiring that coordinates app-owned
Chat/Dream resources without moving Dream logic into `MessageRuntime`.

Do not put MAGMA, FAISS, graph traversal, temporal parsing, or Dream
orchestration inside `MessageRuntime`.

### `Conversation_Memory/`

Owns the upstream MAGMA checkout, ingestion validation, temporal normalization,
checkpoints, adapter behavior, bounded Recall, memory DTO projection, fixtures,
tests, and memory documentation.

Production code outside this workspace may depend only on Lumina-owned
interfaces and DTOs, never directly on MAGMA, NetworkX, FAISS, or embedding
classes.

### `Dream/`

Owns manual run orchestration, bounded pending-segment selection, deterministic
ordering, failure isolation, calls to Conversation Memory, and
memory-complete-before-consumed coordination.

Dream must not duplicate Draft parsing, temporal parsing, graph storage, vector
storage, or memory idempotency.

### Cold Draft owner

The existing Cold Draft owner is the sole authority for reading records and for
the `pending_digest -> consumed` transition. Do not edit Cold Draft JSONL
files directly or create another writer.

## 6. Non-negotiable invariants

### Cold-first preservation

- Cold Draft persistence must succeed before logical compaction advances.
- Failed preservation leaves the logical Hot view uncompacted.
- Cold Draft source records are immutable.
- Do not rewrite, truncate, delete, summarize in place, or reinterpret them.
- Physical Hot Draft remains append-only unless a later task explicitly
  authorizes a migration.

### Turn provenance

New V2 turns preserve through every layer:

```text
turn_id, role, text, created_at, source_timezone, timezone_source
```

- User and assistant/fallback turns have different IDs and timestamps.
- IDs exist before first persistence and survive retry/restart.
- Timestamps are aware RFC 3339 UTC values.
- Client IANA timezone and fallback source are recorded truthfully.
- Legacy records remain readable and explicitly use
  `legacy_segment_fallback`.
- Do not auto-migrate or re-ingest existing consumed data.

### Dream and ingestion

- Dream remains manual, synchronous, bounded, and single-writer.
- Reuse `DreamRunner.run_once(...)`; do not copy CLI orchestration or create a
  second Dream service.
- Any in-app Dream entry must reuse the app-owned Cold Draft owner and the same
  Conversation Memory adapter/backend used by the resident retriever, or an
  equivalently verified locked refresh strategy.
- Process only eligible `pending_digest` segments.
- Use `(segment_id, ingestion_version)` as the durable checkpoint key.
- Consume only after memory persistence and checkpointing succeed.
- Retry must converge without duplicate logical memory.
- Conversation turns remain one event each.

### Temporal normalization

- Use each turn's `created_at` and `source_timezone`, never Dream time.
- Preserve original text and original expression.
- Store aware UTC half-open intervals `[start, end)`.
- Use real local-calendar day/week/month/year boundaries and DST semantics.
- Do not expand into lunar calendars, holidays, vague time, time-of-day,
  durations, or missing-year inference without explicit authorization.

### Recall

- Recall is accessed only through a Lumina-owned facade.
- Bound candidate count, traversal depth, evidence count, and rendered size.
- Preserve stable ordering, evidence IDs, and provenance.
- Do not scan Cold Draft during Recall.
- Empty Recall is valid.
- Recall failure must not block normal chat.
- Do not expose embeddings, backend scores, graph objects, MAGMA UUIDs, paths,
  credentials, provider bodies, tracebacks, or raw Draft records.
- Do not restore the removed cosine gate, `min_relevance`, vector interception,
  calibration infrastructure, LLM judge, or cross-encoder without a separate
  authorized task and new evidence.

### Chat runtime

- Keep one `MessageRuntime` and one `ModelClient` protocol.
- Preserve mock mode and safe provider fallback.
- Keep the default path synchronous and restart-persistent.
- Dream and MAGMA must not become mandatory startup requirements for normal
  chat availability.
- No ingestion or Dream work may run inside `/api/chat`.
- Until a cross-process solution is explicitly implemented, shared file-backed
  writes require one process, one worker, no reload, and no concurrent external
  Dream CLI.

## 7. Current authorized next step

The next production objective is a minimal manual Dream control in the existing
browser frontend, based on the verified findings in
`docs/DREAM_UI_CODE_AUDIT.md`.

Required chain:

```text
existing DreamRunner.run_once
+ app-owned ColdDraftStore
+ shared Conversation Memory adapter/backend
+ one process-local writer mutex shared by Chat and Dream
+ synchronous POST /api/dream/run
+ expanded GET /api/status
+ bounded no-body pending count
+ native frontend maintenance row
```

Requirements:

- keep Dream manual, synchronous, serial, and bounded;
- use the current policy defaults: `max_segments=10`,
  `stop_on_error=False`, `ingestion_version="dream-v1"`;
- do not accept client-controlled Dream strategy fields;
- do not call `build_default_runner()` per request or construct a second Cold
  Draft or MAGMA owner;
- serialize complete Chat handling and complete Dream runs with one shared
  process-local writer mutex for the first safe version;
- return stable `409 Conflict` when the writer is busy and safe unavailable
  behavior when Dream cannot initialize;
- expose only bounded aggregate status and results, never Cold Draft text,
  segment IDs, paths, tracebacks, MAGMA objects, UUIDs, scores, or provider
  configuration;
- ensure memory written by Dream is immediately visible to the current Chat
  retriever;
- keep deployment to one worker, without `--reload`, and without a concurrent
  external Dream CLI;
- add only a compact maintenance row to the existing native frontend; no UI
  framework, queue, polling loop, WebSocket, history, or Memory Viewer.

Expected implementation scope is limited to the existing app wiring, Cold Draft
owner, narrow HTTP contracts, three native static files, and existing API/store
tests. `Dream/runner.py`, Dream orchestration, RecallPolicy, memory DTOs, Draft
state machines, and upstream MAGMA should remain unchanged unless a verified
blocking defect is reported before coding.

Implementation still requires an explicit task card with acceptance tests. This
section defines the objective and boundaries; it does not authorize adjacent
work.

## 8. Minimal-change rule

Default to the smallest vertical change that satisfies the current task.

Unless explicitly authorized:

- modify at most three existing production modules;
- add at most one production file and one test file;
- add no permanent benchmark or new E2E harness;
- add no new documentation file;
- do not add a `Protocol`, `Factory`, `Manager`, `Registry`, `Facade`, or generic
  framework when an existing boundary works;
- do not add future-facing extension points without a current caller;
- do not expose APIs only for tests;
- do not refactor neighboring modules;
- do not update unrelated documents;
- prefer deletion, inlining, merging, private helpers, and extension of existing
  tests over new structure.

If the minimum correct solution exceeds this budget, stop before coding and
report:

1. why current interfaces are insufficient;
2. the minimum extra surface required;
3. the smaller alternative and its omitted behavior.

A persisted schema change may receive a larger explicit budget in its task
card.

For non-trivial tasks, the final report must separate:

```text
production diff
test diff
documentation diff
new and removed public symbols
new abstractions and why each is necessary
deliberately omitted work
```

## 9. Not authorized without a later task

Do not add:

- automatic/startup/background/chat-time Dream;
- schedulers, workers, cron, autonomous triggers, or model-decided ingestion;
- LLM reflection, summarization, abstraction, consolidation, or memory rewrite;
- forgetting, deletion, decay, contradiction resolution, duplicate merging, or
  salience mutation;
- M-flow multi-granularity redesign;
- Conversation Graph;
- PostgreSQL, Neo4j, or another production database;
- a conversation/thread identity system;
- physical Hot Draft truncation;
- ContextBuilder or ToolRuntime;
- additional model providers;
- relevance-model infrastructure;
- upstream MAGMA modifications;
- repository-wide formatting, renaming, or unrelated architectural refactors.

Never commit credentials, `.env.local`, real Draft data, generated MAGMA stores,
vector indexes, embeddings, model caches, logs, or test sandboxes.

## 10. Known limits

Do not describe these as complete:

- preservation markers and total model-facing context lack a global cap;
- Hot Draft is physically append-only;
- user/assistant Draft writes are not transactional as a pair;
- public `message_consumed` does not fully represent persistence failure;
- local JSONL stores have no process-local or cross-process writer lock;
- Chat/Chat, Chat/Dream, and Dream/Dream writes can currently race;
- Dream and memory checkpoints assume one active writer;
- the external Dream CLI creates a separate memory backend, so a resident Chat
  retriever can retain an old in-memory snapshot until rebuild or restart;
- Hot and Cold reads scan JSONL files, and pending count has no bounded no-body
  store operation;
- Dream has no HTTP endpoint or browser control;
- Recall injection is opt-in and disabled by default;
- production data has no real conversation/thread ID;
- legacy records retain only fallback provenance;
- only one explicit real-model adapter exists.

## 11. Tests and validation

Use synthetic data and temporary paths only. Prefer parameterized tests and the
existing Recall E2E harness over duplicate full-chain fixtures.

Preserve coverage for:

- Draft restart, append, compaction, and failed-Cold-write behavior;
- V2 provenance and legacy fallback;
- ingestion idempotency and partial-failure retry;
- memory-complete-before-consumed ordering and recovery;
- English/Chinese temporal normalization from source turn timezones;
- bounded Recall, stable ordering, provenance, restart, and idempotency;
- safe empty/failure behavior and leak prevention;
- memory-disabled chat behavior and production Recall failure fallback;
- for the next Dream UI task: shared-writer 409 behavior, lock release on every
  path, bounded no-body pending count, safe status/result projection, and
  immediate post-Dream visibility to the resident retriever.

Standard validation:

```bash
python -m pytest -q
python -m pytest Conversation_Memory/tests -q
python -m pytest Dream/tests -q
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
git -C Conversation_Memory/upstream/MAGMA diff --stat
```

Run the documented isolated-environment Recall E2E when a task affects the real
MAGMA path. Upstream MAGMA status and diff output must be empty.

## 12. Change safety

- Preserve `.env.local` and `data/`.
- Do not automatically commit, push, rebase, hard reset, delete branches, or
  rewrite history.
- Delete a directory only when ownership is proven by its marker contract.
- Do not silently patch or reformat upstream MAGMA.
- Do not change unnamed public behavior.
- Update `docs/CURRENT_STATUS.md` only after tests establish the fact.
- Never describe planned, experimental, disabled, or rejected behavior as a
  completed production capability.
