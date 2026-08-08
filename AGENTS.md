# AGENTS.md

## 1. Repository state

Lumina is currently a local-first, single-user, single-continuous-conversation
chatbot runtime. The implemented product loop is:

```text
Browser
-> FastAPI / MessageRuntime / ModelClient
-> rolling Hot Draft summary + recent raw turns
-> Cold-first per-turn Cold archive
-> explicit Dream
-> shared Conversation Memory / pinned MAGMA backend
-> optional bounded Recall
-> model request
```

A separate read-only history projection restores the single conversation after
restart:

```text
Cold raw turns + Hot recent raw turns
-> GET /api/history
-> initial latest-page restore
-> upward cursor pagination
```

The Conversation Memory v1 execution boundary is implemented and should now be
treated as a stable subsystem boundary, not as an unfinished integration
milestone. This does not mean retrieval quality, abstention, conflict handling,
or autonomous scheduling are complete.

## 2. Existing capabilities

Treat the following as completed behavior. Do not rebuild or redesign them
without a verified defect, a reproducible real failure, or an explicit task
that requires a narrow caller-contract change.

- same-origin browser chat;
- `GET /api/status`, `POST /api/chat`, `POST /api/dream/run`, and
  `GET /api/history`;
- deterministic mock mode and explicit MiniMax Anthropic-compatible mode;
- startup-loaded `prompts/chat_background.md`, injected only as the native
  system prompt for normal Chat generations;
- one physical Hot Draft containing zero or one rolling summary record plus
  recent raw user/assistant turns;
- default rolling compaction rule: compress when raw turns exceed 24 and retain
  the latest 12 raw turns, moving only complete user/assistant pairs;
- Cold-first preservation with one original Cold turn per JSONL line and
  segment-level atomic append/consume behavior;
- restart-persistent, read-only single-conversation history with cursor
  pagination and upward lazy loading;
- explicit browser/API Dream and a separate stop-the-service CLI entry;
- app-owned shared Cold store, memory adapter/backend, and process-local
  Chat/Dream writer mutex;
- pinned, unmodified upstream MAGMA at the recorded commit;
- one MAGMA event per source turn, stable evidence IDs, provenance, and durable
  `(segment_id, ingestion_version)` checkpoints;
- dense + bounded lexical anchor retrieval fused with RRF;
- aware-UTC half-open temporal hard filtering;
- fixed traversal and caller-opted adaptive relation-aware traversal;
- bounded evidence projection and intent-aware Context Linearization;
- optional production Recall injection, disabled by default, with safe empty or
  failure fallback;
- real-MAGMA synthetic end-to-end validation, restart recovery, and idempotency;
- documented depth, adaptive traversal, GENERAL weight, temporal-ranking, and
  cross-turn-reference experiments. Synthetic results must not be generalized
  into production-quality claims.

## 3. Authority

Use these documents according to their purpose:

- `docs/NORTH_STAR.md`: long-term form and direction only;
- `docs/final_goal.md`: current product direction and next development boundary;
- `docs/CURRENT_STATUS.md`: current implementation facts;
- `docs/LUMINA_CODEBASE_SCAN.md`: current codebase-wide architecture baseline;
- `docs/COLD_DRAFT.md`: active Hot/Cold preservation contract;
- `docs/DRAFT_TURN_PROVENANCE_V2.md`: native turn identity and time provenance;
- `docs/RECALL_E2E_ACCEPTANCE.md`: real-MAGMA synthetic E2E contract;
- `Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md`;
- `Conversation_Memory/docs/CHINESE_TEMPORAL_PARSER.md`;
- `Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md`;
- `Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`;
- `Conversation_Memory/AGENTS.md`;
- `Dream/AGENTS.md`;
- this file.

Decision order:

1. obey the explicit task card and acceptance criteria;
2. preserve Cold-first durability and the synchronous Chat path;
3. preserve truthful provenance, idempotency, and safe failure behavior;
4. use `docs/CURRENT_STATUS.md` and current tests for implementation facts;
5. follow the most specific non-stale workspace rule;
6. use `docs/NORTH_STAR.md` only as a tie-breaker among already-valid options.

Historical audits and scans are reference material, not current implementation
authority unless their header explicitly says otherwise.

## 4. Ownership

### `core/`

Owns application composition, API validation, the single `MessageRuntime`, the
single `ModelClient` protocol, background-prompt loading, Hot and Cold store
ownership, rolling compaction, shared writer coordination, read-only History
projection, and narrow product wiring.

Do not put MAGMA, FAISS, graph traversal, temporal parsing, or Dream business
logic inside `MessageRuntime`.

### `Conversation_Memory/`

Owns the pinned upstream MAGMA checkout, ingestion validation, temporal
normalization, checkpoints, backend isolation, bounded Recall execution,
Lumina-owned DTO projection, fixtures, tests, and memory documentation.

Production code outside this workspace may depend only on Lumina-owned
interfaces and DTOs, never directly on MAGMA, NetworkX, FAISS, or embedding
classes.

### `Dream/`

Owns explicit bounded run orchestration, deterministic pending-segment
selection, per-segment failure isolation, conversion to Conversation Memory
DTOs, memory-complete-before-consumed coordination, and safe aggregate reports.

Dream must not duplicate Draft parsing, temporal parsing, graph storage, vector
storage, or memory idempotency.

### Cold Draft owner

The existing `ColdDraftStore` is the sole authority for reconstructing logical
segments and for the `pending_digest -> consumed` transition. Do not edit the
Cold JSONL directly or create another writer.

### `edge/static/`

Owns the current native browser UI only. Do not introduce a framework, state
library, WebSocket/SSE layer, or background job model without explicit
authorization.

## 5. Non-negotiable invariants

### Cold-first rolling work memory

- Hot Draft is physically rewritten during successful compaction; it is not an
  append-only full transcript.
- Hot contains at most one current rolling summary plus recent raw turns.
- The summary input is only the previous summary plus the raw turns being moved
  during the current compaction.
- Cold persistence of the complete source segment must succeed before Hot is
  replaced.
- A failed summary or Cold append leaves the previous Hot representation valid.
- Cold source turn text and provenance are immutable; only owner-controlled
  state metadata changes on consume.
- Rolling summaries do not enter Cold, Dream, MAGMA, or History.

### Turn provenance

New native turns preserve through every layer:

```text
turn_id, role, text, created_at, source_timezone, timezone_source
```

- User and assistant/fallback turns have distinct IDs and timestamps.
- IDs exist before first persistence and survive compaction, retry, Dream,
  MAGMA, Recall projection, History, and restart.
- Timestamps are aware RFC 3339 UTC values with truthful source timezone.
- Do not fabricate missing native provenance or silently treat legacy records as
  native V2 evidence.

### Background prompt isolation

- `prompts/chat_background.md` is loaded once at application startup.
- It is sent only as the native system prompt for normal Chat generations.
- It must not enter Hot, Cold, rolling-summary inputs, Dream, Recall queries,
  MAGMA, History, logs, or public API output.

### Dream and ingestion

- Dream is explicit, synchronous, serial, bounded, and never triggered by Chat,
  startup, compaction, Recall, a timer, or a model decision.
- The browser/API path reuses the app-owned Cold owner and the same memory
  adapter/backend used by the resident retriever.
- The CLI path is allowed only while the application service is stopped.
- Use `(segment_id, ingestion_version)` as the durable checkpoint key.
- Consume a segment only after complete memory persistence and checkpoint
  verification.
- Retry must converge without duplicate logical memory.
- Conversation turns remain one MAGMA event each.

### Recall

- Recall is accessed only through the Lumina-owned facade.
- Bound anchors, graph depth, graph nodes, public evidence count, and rendered
  characters.
- `top_k` limits fused anchors; `max_evidence_items` limits the final public
  anchors-plus-expansions total.
- `max_graph_depth=0` is valid and means anchor-only.
- Do not scan Cold Draft during Recall.
- Empty Recall is valid; Recall failure must not block normal Chat.
- Do not expose embeddings, scores, paths, graph objects, MAGMA UUIDs,
  credentials, provider bodies, local paths, tracebacks, or raw Draft records.
- Do not restore the rejected cosine relevance gate, LLM judge, cross-encoder,
  or a custom temporal/coreference algorithm without a separate task and new
  evidence.

### History

- History is a read-only projection of valid Cold raw turns plus current Hot raw
  turns, deduplicated by stable `turn_id` with Cold winning overlaps.
- History must exclude the rolling summary, Recall evidence, MAGMA metadata,
  Dream state, and the background prompt.
- History must not be injected into the model context or mutate any store.

### Writer and deployment boundary

- One process-local nonblocking writer mutex serializes complete Chat and
  in-app Dream operations.
- Current supported deployment is one process, one worker, no `--reload`, and no
  concurrent external Dream CLI.
- A process-local lock does not make multi-worker or multi-instance operation
  safe.

## 6. Current development boundary

The Memory v1 ownership, DTO, provenance, Cold-first, Dream, and bounded Recall
facade boundaries are frozen unless a task presents:

1. a reproducible correctness or data-loss bug;
2. a real Recall failure sample that justifies a narrow change; or
3. a minimal contract adjustment required by an explicitly designed caller.

The recommended next stage is a narrow **Mind System caller-contract design**:
when to recall, which intent/window/budget to request, how to assess evidence
sufficiency, and how to request another search. Start docs-only. This file does
not authorize Mind implementation, automatic routing, Evidence Organizer,
automatic Dream, workers, agents, or background behavior.

## 7. Known limitations

Do not describe these as implemented:

- automatic or scheduled Dream;
- Recall scheduler, automatic query/intent classification, or none/light/deep
  routing;
- evidence sufficiency, reliable abstention, or no-answer rejection;
- Evidence Organizer/Ledger, conflict/current-state resolution, fact
  supersession, or timeline synthesis;
- explicit coreference resolution;
- multi-conversation identity, multi-user isolation, multi-worker safety, or
  cross-process transactions;
- global model-context token budgeting;
- database-backed History indexes or consistent cross-file snapshots;
- production-scale or real-user answer-quality validation.

## 8. Change discipline

- Prefer the smallest correct vertical change.
- Preserve `.env.local`, `data/`, model caches, and user-owned worktree changes.
- Never commit credentials, provider bodies, generated memory data, embeddings,
  or user conversations.
- Do not silently patch upstream MAGMA.
- Do not automatically commit, push, rebase, reset, delete branches, or rewrite
  history.
- When a task changes a public contract, persistence schema, or more than one
  organ, stop at explicit checkpoints and report the exact boundary change.

## 9. Validation

Use the existing project environment so real MAGMA tests do not skip:

```bash
python -m pytest -q
python -m pytest Conversation_Memory/tests -q
python -m pytest Dream/tests -q
python -m scripts.recall_e2e_test
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
git -C Conversation_Memory/upstream/MAGMA diff --stat
```

The MAGMA status and diff outputs must remain empty unless a separate upstream
patch task explicitly authorizes otherwise.
