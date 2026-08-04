# Lumina

Lumina is a local-first conversational runtime built around one continuity
invariant: conversation material must be durably preserved before it leaves the
live context.

The current production path is:

```text
Browser -> FastAPI -> MessageRuntime
        -> optional bounded Conversation Memory Recall
        -> ModelClient
        -> Hot Draft -> Cold-first rolling semantic compaction -> Cold Draft
```

The current manual memory path is:

```text
browser Dream control, `POST /api/dream/run`, or stopped-server CLI
-> pending Cold Draft segments
-> Lumina Conversation Memory adapter
-> unmodified upstream MAGMA
-> durable checkpoint
-> Cold Draft segment consumed
```

Lumina is therefore already a local chatbot with an optional, manually
maintained long-term conversation-memory loop. The native browser maintenance
row can run Dream explicitly and show bounded pending status; Dream is not
automatic or background work.

## Current Capabilities

- same-origin browser chat with `GET /api/status`, `POST /api/chat`, and a native
  manual Dream control backed by `POST /api/dream/run`;
- deterministic mock mode by default and explicit MiniMax
  Anthropic-compatible model mode;
- safe provider and Recall fallback without exposing credentials, paths,
  tracebacks, provider bodies, or memory internals;
- restart-persistent Hot Draft with one rolling semantic summary plus recent
  raw turns after compaction;
- native per-turn provenance with stable IDs, aware UTC timestamps, source IANA
  timezones, and truthful timezone source;
- default 24/12 pair-aware, Cold-first rolling compaction; the first 26 raw
  turns archive 14, and later rounds replace the same summary while including
  the previous summary as input;
- one original `cold_turn` per Cold Draft JSONL line, grouped back into one
  logical segment by shared ID and continuous index/count metadata;
- manually triggered, synchronous, bounded Dream ingestion;
- one app-owned Cold Draft store and one shared Conversation Memory
  adapter/backend for in-app Dream and Chat Recall;
- one process-local, nonblocking writer mutex across complete Chat requests and
  Dream runs, with safe busy responses;
- bounded no-body pending status counted by logical segment, aggregate-only
  Dream results, and atomic whole-segment consumed transitions;
- `GET /api/status` reports real compaction running state; the frontend polls
  it every 500 ms only during an in-flight Chat request, stops afterward, and
  refreshes backend pending status once after completed compaction;
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

This is the supported launch command for the current file-backed runtime. The
deployment must use one process and one worker. `--reload`, `--workers 2` (or
any multi-worker configuration), and running the external Dream CLI while the
service is running are unsupported.

During a real rolling compaction the browser displays this notice:

Hot Draft &#27491;&#22312;&#21387;&#32553;&#65292;&#35831;&#31245;&#20505;&#8230;&#8230;

The polling is temporary and protected from late responses; there is no
permanent polling, WebSocket, SSE, or background compaction.

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

While the service is running, use the **Run Dream** control in the browser
maintenance row or call the synchronous endpoint directly:

```bash
curl -X POST http://127.0.0.1:8000/api/dream/run
```

The in-app path reuses the app-owned Cold Draft store and the same Conversation
Memory adapter/backend used by Chat Recall. When Recall is enabled, successfully
ingested memory is visible to subsequent Chat requests without restarting the
service. Status and run responses expose only bounded counts and safe aggregate
results.

The external CLI remains available only when the chat service is stopped:

```bash
python -m Dream.runner --max-segments 10
```

The run is synchronous, bounded, and serial. It processes only eligible
`pending_digest` segments and marks a segment consumed only after memory
persistence and the durable checkpoint succeed. Re-running the same ingestion
version converges without duplicate logical memory. Dream remains explicitly
triggered; there is no automatic, startup, scheduled, or background Dream.

The verified two-round browser flow kept one Hot summary plus its recent tail,
created two turn-line Cold segments (14 `cold_turn` lines in the first), and
refreshed pending from 0 to 1 to 2 without reload. One manual Dream run reported
`attempted=2`, consumed both logical segments as wholes, reduced pending to
zero, and left enabled Recall working without restart.

## Local Data

Default private runtime data is stored under:

```text
data/draft/hot_drafts.jsonl
data/draft/cold_drafts.jsonl
data/draft/hot_draft_compaction_state.json
data/conversation_memory/ingestion_state.json
data/conversation_memory/magma/
```

Cold segment append and consumed transitions use a unique same-directory
temporary file, flush, fsync, and atomic replacement. The obsolete nested
`turns[]` Cold format is not read or migrated; no dual-format reader exists.

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
