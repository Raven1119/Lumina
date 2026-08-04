# Lumina

Lumina is a local-first conversational runtime built around one continuity
invariant: conversation material must be durably preserved before it leaves the
live context.

The current production path is:

```text
Browser -> FastAPI -> MessageRuntime
        -> optional bounded Conversation Memory Recall
        -> ModelClient
        -> Hot Draft -> Cold-first logical compaction -> Cold Draft
```

The current offline memory path is:

```text
manual Dream command
-> pending Cold Draft segments
-> Lumina Conversation Memory adapter
-> unmodified upstream MAGMA
-> durable checkpoint
-> Cold Draft segment consumed
```

Lumina is therefore already a local chatbot with an optional, manually
maintained long-term conversation-memory loop. Dream is still invoked through
the CLI; there is not yet a browser Dream control or an application-wide writer
mutex.

## Current Capabilities

- same-origin browser chat with `GET /api/status` and `POST /api/chat`;
- deterministic mock mode by default and explicit MiniMax
  Anthropic-compatible model mode;
- safe provider and Recall fallback without exposing credentials, paths,
  tracebacks, provider bodies, or memory internals;
- append-only, restart-persistent Hot Draft turns;
- native per-turn provenance with stable IDs, aware UTC timestamps, source IANA
  timezones, and truthful timezone source;
- pair-aware, Cold-first logical compaction into immutable Cold Draft source
  segments;
- manually triggered, synchronous, bounded Dream ingestion;
- pinned, unmodified upstream MAGMA with durable
  `(segment_id, ingestion_version)` checkpoints and idempotent retry;
- bounded Recall through a Lumina-owned facade;
- dense and bounded lexical anchor rankings fused with MAGMA-style RRF;
- half-open aware-UTC `temporal_window=[start,end)` hard filtering;
- fixed traversal and explicitly enabled adaptive, relation-aware graph
  traversal;
- deterministic intent-aware Context Linearization;
- optional production chat injection of only `MemoryContext.rendered_text`,
  disabled by default;
- safe empty or failed Recall fallback to ordinary chat;
- an isolated real-MAGMA end-to-end acceptance harness.

Current `GENERAL` traversal intentionally retains the fixed upstream
entity-biased fallback after a controlled ablation. No generic Temporal Anchor
Ranking was added because the pinned MAGMA checkout does not contain a safe,
benchmark-independent implementation.

## Install

Install the root chat and test dependencies:

```bash
python -m pip install -r requirements.txt
```

Use the prepared Conversation Memory environment for real MAGMA operations when
required by the local checkout.

## Run Chat

Mock mode requires no provider configuration:

```bash
python -m uvicorn core.main:app
```

Open `http://127.0.0.1:8000/`. API documentation is available at
`http://127.0.0.1:8000/docs`.

The current file-backed runtime assumes one active writer. Do not use multiple
Uvicorn workers. Do not use `--reload` while validating Dream or shared memory
writes, and do not run the external Dream CLI concurrently with the server.

## Run With MiniMax

Copy the ignored `.env.example` to `.env.local`, set
`LUMINA_MODEL_MODE=real`, and provide the MiniMax Anthropic-compatible provider,
key, base URL, and model name. Process environment variables take precedence
over `.env.local`; restart the server after changing configuration.

No provider request occurs during import or startup. Provider failure returns a
safe fallback response.

## Enable Conversation Memory Recall

Production chat Recall is opt-in and disabled by default. Set:

```dotenv
LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED=true
```

Only bounded rendered memory text enters the model request. Recall failure or an
empty result does not block ordinary chat, and injected memory is not persisted
as a new Draft turn.

Recall can only use segments that have already been ingested through Dream.

## Run Manual Dream Ingestion

Stop the chat server before running the current CLI so the file-backed stores
have one active writer:

```bash
python -m Dream.runner --max-segments 10
```

The run is synchronous, bounded, and serial. It processes only eligible
`pending_digest` segments and marks a segment consumed only after memory
persistence and the durable checkpoint succeed. Re-running the same ingestion
version converges without duplicate logical memory.

The next production objective is a browser Dream button backed by the existing
`DreamRunner.run_once(...)`, a shared app-owned Cold Draft and memory backend,
and one Chat/Dream writer mutex. That interface is not implemented yet.

## Local Data

Default private runtime data is stored under:

```text
data/draft/hot_drafts.jsonl
data/draft/cold_drafts.jsonl
data/draft/hot_draft_compaction_state.json
data/conversation_memory/ingestion_state.json
data/conversation_memory/magma/
```

`data/` and `.env.local` are ignored by Git and must be preserved during code or
documentation maintenance.

## Validation

```bash
python -m pytest -q
python -m pytest Conversation_Memory/tests -q
python -m pytest Dream/tests -q
python -m scripts.recall_e2e_test
git diff --check
```

Tests and harnesses must use synthetic data and temporary or marker-owned paths.
They must not read or modify default private runtime data.

## Active Documents

- [`docs/NORTH_STAR.md`](docs/NORTH_STAR.md): long-term direction, not current
  implementation authorization;
- [`docs/final_goal.md`](docs/final_goal.md): current product direction and next
  production objective;
- [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md): implementation facts;
- [`docs/COLD_DRAFT.md`](docs/COLD_DRAFT.md): Cold-first preservation contract;
- [`docs/DREAM_UI_CODE_AUDIT.md`](docs/DREAM_UI_CODE_AUDIT.md): verified Dream
  UI integration boundary and concurrency findings.

Conversation Graph as a separate organ, PostgreSQL memory, ContextBuilder,
ToolRuntime, autonomous Dream, schedulers, workers, agents, and tasks are not
implemented.
