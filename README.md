# Lumina

Lumina is a local-first, single-user, single-continuous-conversation LLM
chatbot prototype with rolling work memory, immutable raw conversation archive,
explicit long-term-memory ingestion, bounded MAGMA Recall, and restart-persistent
browser history.

## Current runtime

```text
Browser
-> FastAPI / MessageRuntime / ModelClient
-> rolling Hot Draft summary + recent raw turns
-> Cold-first per-turn archive
-> explicit Dream
-> shared Conversation Memory / MAGMA backend
-> optional bounded Recall
-> model request
```

The browser history path is separate from the model context:

```text
Cold raw turns + Hot recent raw turns
-> GET /api/history
-> latest-page restore
-> upward cursor pagination
```

The complete transcript is never replayed into the model request.

## Current capabilities

- browser Chat with safe mock and MiniMax Anthropic-compatible modes;
- startup-loaded `prompts/chat_background.md` as the native Chat system prompt;
- rolling semantic Hot Draft with one current summary and recent raw turns;
- Cold-first, per-turn `cold_turn` archive with segment-level atomic operations;
- explicit browser Dream button and a stop-the-service CLI entry;
- shared app-owned Cold store and MAGMA backend, so Dreamed memories are
  immediately visible to the resident retriever;
- optional bounded Recall using dense + lexical RRF anchors, temporal hard
  filtering, fixed/adaptive graph traversal, and Context Linearization;
- read-only single-conversation history restored after restart and lazily loaded
  upward;
- restart recovery and idempotent synthetic real-MAGMA E2E validation.

## Install

From the repository root:

```bash
python -m pip install -r requirements.txt
```

The MAGMA path uses the prepared environment under `Conversation_Memory/.venv`.
On Windows, the simplest supported launch is:

```powershell
& .\Conversation_Memory\.venv\Scripts\python.exe -m uvicorn core.main:app
```

Or, after activating that environment:

```bash
python -m uvicorn core.main:app
```

Open:

```text
http://127.0.0.1:8000/
```

Current file-backed writer safety requires:

- one process;
- one Uvicorn worker;
- no `--reload`;
- no concurrent external `python -m Dream.runner` while the service is running.

## Chat background

Lumina reads:

```text
prompts/chat_background.md
```

once during application startup. Its complete content is sent only as the
provider-native system prompt for normal Chat generations. It is not stored in
Hot/Cold Draft, rolling-summary input, Dream, Recall, MAGMA, History, or logs.
Restart the application after editing it.

## Model configuration

Copy `.env.example` to the ignored `.env.local` and set the supported MiniMax
Anthropic-compatible variables. Process environment variables take precedence.
Incomplete or unsupported explicit configuration falls back safely to mock
mode.

No provider request occurs at import or startup. Normal Chat and Hot summary
compression are the only current external-model call sites.

## Enable Conversation Memory Recall

Recall is disabled by default. Enable it in `.env.local` or the process
environment, then restart:

```bash
LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED=true
```

When enabled:

- the current user query is sent through the Lumina-owned `MemoryRetriever`;
- only non-empty, bounded `MemoryContext.rendered_text` enters the model context;
- Recall may contain fused anchors and valid graph-expanded event evidence;
- empty, unavailable, corrupt, or failed Recall falls back to ordinary Chat;
- recalled text is not written into Hot or Cold Draft;
- MAGMA UUIDs, graph paths, scores, embeddings, provenance objects, local paths,
  and raw Draft records are not injected.

Recall does not scan Cold Draft. Dream is the only path from pending Cold source
segments into MAGMA.

## Hot and Cold Draft

Default paths:

```text
data/draft/hot_drafts.jsonl
data/draft/cold_drafts.jsonl
data/draft/hot_draft_compaction_state.json
```

Hot Draft contains zero or one rolling summary record plus recent raw
user/assistant turns. The default rule compresses when raw turns exceed 24 and
retains the latest 12 raw turns, moving only complete user/assistant pairs.

The compactor first generates the new summary, then atomically archives the
complete raw segment to Cold, then atomically replaces Hot. A failed summary or
Cold append leaves the previous Hot state valid.

Cold Draft stores one original turn per `cold_turn` JSONL line. A group of lines
with the same `segment_id` is one logical pending/consumed segment. Original
text and provenance remain unchanged across consume.

## Run Dream

Use the **Run Dream** button in the browser for the normal manual workflow. It
calls:

```text
POST /api/dream/run
```

with the fixed default policy:

```text
max_segments=10
stop_on_error=false
ingestion_version=dream-v1
```

The frontend displays aggregate attempted/ingested/consumed/skipped/failed
counts and refreshes the bounded pending-segment status.

The CLI is available only while the application service is stopped:

```bash
python -m Dream.runner --max-segments 10
```

A segment is marked consumed only after durable memory completion and checkpoint
verification. Re-running converges without duplicate logical memory.

## Single-conversation history

`GET /api/history` returns the original transcript projection:

```text
valid Cold raw turns + current Hot raw turns
```

It excludes the rolling summary, background prompt, Recall evidence, Dream
state, and MAGMA metadata.

- default page size: 40;
- valid limit: 1..100;
- `before=<turn_id>` is an exclusive cursor;
- response pages are ordered oldest-to-newest;
- the browser initially loads the latest page and loads older pages when the
  user scrolls near the top;
- prepending older messages preserves the visible scroll position;
- Dream changing Cold state from pending to consumed does not remove History.

Current pagination still scans the JSONL stores on each request. There is no
History database or index.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/status` | model/Recall/compaction/Dream/pending status |
| `POST` | `/api/chat` | synchronous Chat generation and Draft persistence |
| `POST` | `/api/dream/run` | explicit bounded Dream ingestion |
| `GET` | `/api/history` | read-only single-conversation transcript pagination |

Public responses intentionally exclude raw Draft records, the background prompt,
provider configuration, MAGMA internals, graph paths, scores, embeddings, local
paths, and tracebacks.

## Private runtime data

```text
data/draft/hot_drafts.jsonl
data/draft/cold_drafts.jsonl
data/draft/hot_draft_compaction_state.json
data/conversation_memory/ingestion_state.json
data/conversation_memory/magma/
```

`data/` and `.env.local` are private and ignored by Git. Do not delete, migrate,
or commit them during normal code/document maintenance.

## Validation

Use the existing Conversation Memory environment so real MAGMA tests execute:

```powershell
& .\Conversation_Memory\.venv\Scripts\python.exe -m pytest -q
& .\Conversation_Memory\.venv\Scripts\python.exe -m pytest Conversation_Memory/tests -q
& .\Conversation_Memory\.venv\Scripts\python.exe -m pytest Dream/tests -q
& .\Conversation_Memory\.venv\Scripts\python.exe -m scripts.recall_e2e_test
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
git -C Conversation_Memory/upstream/MAGMA diff --stat
```

The upstream MAGMA status and diff must remain empty.

## Current limitations

Not currently implemented:

- automatic/background Dream;
- Recall scheduling, automatic intent/query routing, or none/light/deep modes;
- reliable no-answer abstention;
- Evidence Organizer/Ledger, conflict/current-state resolution, fact
  supersession, or timeline synthesis;
- explicit coreference resolution;
- multi-conversation, multi-user, multi-worker, or cross-process safety;
- global model-context token budgeting;
- database-backed History indexing;
- production-scale or real-user answer-quality validation.

Cross-turn reference is only partially supported through anchors, temporal or
semantic graph adjacency, joint Recall, and the final model's interpretation.

## Project documents

- `docs/NORTH_STAR.md`: long-term direction;
- `docs/final_goal.md`: current product direction and next boundary;
- `docs/CURRENT_STATUS.md`: factual current status;
- `docs/LUMINA_CODEBASE_SCAN.md`: current codebase-wide scan;
- `docs/COLD_DRAFT.md`: active Hot/Cold contract;
- `Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`: Dream contract;
- `Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md`: ingestion/Recall
  boundary.
