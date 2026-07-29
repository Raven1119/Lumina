# Lumina

Lumina is a local-first conversational runtime built around one continuity
invariant: conversation material must be durably preserved before it leaves the
live context.

The synchronous chat path is intentionally small:

```text
Browser -> FastAPI -> MessageRuntime
-> optional bounded Recall injection
-> ModelClient
-> Hot Draft -> Cold-first logical compaction -> Cold Draft
```

An offline memory path is also implemented:

```text
manual Dream command
-> pending Cold Draft segments
-> Lumina Conversation Memory adapter
-> unmodified upstream MAGMA
-> durable checkpoint
-> Cold Draft segment consumed
```

Bounded Recall works behind a Lumina-owned facade, including restart recovery
and stable evidence projection. Recall can be optionally injected into the
production model request, but it remains disabled by default. When enabled,
only bounded `MemoryContext.rendered_text` becomes model-visible; empty results
or Recall initialization/execution failures fall back to ordinary chat. The
injected memory block is not persisted into Hot or Cold Draft.

Public Recall evidence now includes vector anchors plus eligible non-anchor
event nodes discovered through existing MAGMA traversal paths. Anchors remain
first, repeated expansions are deduplicated, and nodes without complete
provenance are skipped. `top_k` limits vector anchors, while
`max_evidence_items` limits the final evidence total. Traversal paths, relation
explanations, backend scores, hop metadata, and MAGMA `narrative_context` remain
internal.

## Current Capabilities

- same-origin browser chat, `GET /api/status`, and `POST /api/chat`;
- deterministic mock mode by default and explicit MiniMax
  Anthropic-compatible model mode;
- safe fallback when real-model configuration or a provider call fails;
- append-only, restart-persistent Hot Draft turns;
- native per-turn provenance with stable IDs, distinct aware UTC timestamps,
  and truthful IANA source timezones;
- pair-aware Cold-first compaction into Cold Draft segments whose source content
  stays immutable across the owner-controlled pending/consumed transition;
- manually triggered, synchronous, bounded Dream ingestion;
- pinned, unmodified upstream MAGMA with durable
  `(segment_id, ingestion_version)` checkpoints;
- one MAGMA event per conversation turn, with idempotent retry and
  memory-complete-before-consumed ordering;
- bounded Recall with stable evidence IDs, provenance, safe empty/failure
  behavior, deterministic English/Chinese temporal normalization, and
  anchor-first projection of eligible graph-traversal expansion events;
- `top_k` limits vector anchors and `max_evidence_items` limits the final public
  evidence total across anchors and graph expansions;
- default-disabled, opt-in Recall injection that exposes only bounded
  `MemoryContext.rendered_text` to the model and never persists the injected
  memory block into Draft.

Dream is never run by chat, application startup, or a background worker.
Recall does not scan Cold Draft, and the removed cosine relevance threshold is
not part of the production path.

## Install and Run Chat

Install the root chat/test dependencies:

```bash
python -m pip install -r requirements.txt
```

Mock mode requires no configuration:

```bash
python -m uvicorn core.main:app --reload
```

Open `http://127.0.0.1:8000/` for the chat frontend. API documentation is at
`http://127.0.0.1:8000/docs`.

## Run With MiniMax

Copy the ignored `.env.example` to `.env.local`, set
`LUMINA_MODEL_MODE=real`, and provide the MiniMax Anthropic-compatible
provider, key, base URL, and model name. Existing process environment variables
take precedence over `.env.local`; restart the server after changing the file.

No provider request is made during import or startup. A provider request occurs
only after a chat message is submitted. Provider failure returns a safe
fallback response without exposing credentials, provider bodies, paths, or
tracebacks.

## Enable Optional Conversation Memory Recall

Recall is disabled by default. To enable the existing bounded Recall injection,
set the following in `.env.local` or the process environment and restart the
server:

```bash
LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED=true
```

When enabled:

- the current user message is sent through the existing Lumina-owned
  `MemoryRetriever`;
- only non-empty, bounded `MemoryContext.rendered_text` is inserted into the
  model context using fixed boundary markers;
- evidence DTOs, provenance objects, backend scores, graph objects, MAGMA UUIDs,
  embeddings, local paths, and raw Draft records are not injected;
- empty Recall, unavailable dependencies, initialization failure, corruption,
  or execution failure falls back to ordinary chat;
- no Dream or ingestion work runs during `/api/chat`;
- the injected memory block is not written into Hot or Cold Draft.

When Recall is enabled, the bounded rendered context may contain both vector
anchors and eligible event nodes reached through MAGMA graph traversal. Anchors
remain first, and the final result still uses the existing evidence and rendered
size limits. The model does not receive graph paths, relation metadata, backend
scores, MAGMA identifiers, or `narrative_context`.

## Run Manual Dream Ingestion

From the repository root, using the prepared Conversation Memory environment
when real MAGMA dependencies are required:

```bash
python -m Dream.runner --max-segments 10
```

The run is bounded and serial. It processes only eligible `pending_digest`
segments and marks a segment consumed only after memory persistence and its
durable checkpoint succeed. Re-running the same ingestion version converges
without duplicate logical memory.

See
[`Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`](Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md)
for command options, environment overrides, and recovery semantics.

## Local Data

Default private runtime data is stored under:

```text
data/draft/hot_drafts.jsonl
data/draft/cold_drafts.jsonl
data/draft/hot_draft_compaction_state.json
data/conversation_memory/ingestion_state.json
data/conversation_memory/magma/
```

`data/` and `.env.local` are ignored by Git and must be preserved during code
or documentation maintenance. Current JSONL stores assume one active writer.

Logical compaction bounds the recent raw-turn tail, but physical Hot Draft
remains append-only and accumulated preservation markers leave total
model-facing context without a global cap.

## Validation

Run the standard suites with synthetic data and temporary paths:

```bash
python -m pytest -q
python -m pytest Conversation_Memory/tests -q
python -m pytest Dream/tests -q
git diff --check
```

Changes that affect the real MAGMA path should also run the isolated Recall
acceptance harness:

```powershell
.\Conversation_Memory\.venv\Scripts\python.exe -m scripts.recall_e2e_test
```

The harness uses a marker-owned sandbox and does not read or modify default
production Draft or Conversation Memory data. Details are in
[`docs/RECALL_E2E_ACCEPTANCE.md`](docs/RECALL_E2E_ACCEPTANCE.md).

## Project Boundaries

- [`docs/final_goal.md`](docs/final_goal.md) states the product direction and
  next production objective.
- [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md) is the authority for
  completed, partial, and not-started implementation facts.
- [`docs/COLD_DRAFT.md`](docs/COLD_DRAFT.md) defines the active Cold-first Draft
  contract.
- [`docs/DRAFT_TURN_PROVENANCE_V2.md`](docs/DRAFT_TURN_PROVENANCE_V2.md) defines
  native turn identity and time provenance.
- [`Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md`](Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md)
  defines memory provenance and retry guarantees.

Conversation Graph, PostgreSQL memory, ContextBuilder, ToolRuntime, autonomous
Dream, schedulers, workers, agents, tasks, dynamic Recall scheduling, and an
Evidence Organizer are not implemented. Graph-enhanced Recall exists, but its
quality, noise, and cost have not yet been characterized against the
anchor-only path.
