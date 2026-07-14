# Lumina Cold Draft MVP

Lumina is currently a small local conversational runtime. This checkout proves
one synchronous chat path with mock or explicitly configured MiniMax responses,
restart-persistent Hot Draft turns, Cold Draft segments, and Cold-first logical
compaction.

## What Works

- `GET /api/status`
- `POST /api/chat`
- browser chat frontend at `/`
- deterministic mock mode by default
- explicit MiniMax Anthropic-compatible model mode
- safe provider fallback
- append-only Hot Draft JSONL with recent multi-turn context
- pending/consumed Cold Draft JSONL segments
- pair-aware, Cold-first compaction with restart-persistent state

Compaction bounds the recent raw-turn tail, but the model-facing preservation
markers and physical Hot Draft file do not yet have global size bounds. Draft
write failures are handled without exposing internals or breaking the public
response; persistence is not transactional across the user/assistant pair.

This MVP is not long-term memory. It does not include Conversation Memory,
Conversation Graph, Dream, MAGMA, recall, embeddings, vector search, PostgreSQL,
agents, tasks, or background workers.

The non-lossy Draft boundary and its current limitations are documented in
[`docs/COLD_DRAFT.md`](docs/COLD_DRAFT.md). The current product direction is in
[`docs/final_goal.md`](docs/final_goal.md).

## Install

```bash
python -m pip install -r requirements.txt
```

## Run In Mock Mode

Mock mode requires no configuration:

```bash
python -m uvicorn core.main:app --reload
```

Open `http://127.0.0.1:8000/` for the chat frontend. API documentation remains
available at `http://127.0.0.1:8000/docs`.

## Run With MiniMax

Create an ignored `.env.local` from `.env.example`, set
`LUMINA_MODEL_MODE=real`, and provide the MiniMax Anthropic-compatible provider,
key, base URL, and model name. Existing process environment variables take
precedence over `.env.local`. Restart the server after changing the file.

No provider request is made at import or startup. A request happens only when a
chat message is submitted. Provider failure returns a safe fallback response.

## Draft Data

The default files are:

```text
data/draft/hot_drafts.jsonl
data/draft/cold_drafts.jsonl
data/draft/hot_draft_compaction_state.json
```

`data/` and `.env.local` are ignored by Git. Preserve them when changing code.

## Test

```bash
python -m pytest -q
```

Tests use fake HTTP transports and temporary Draft paths. They never call a real
provider.
