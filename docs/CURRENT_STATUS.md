# Current Status

## Complete

- mock model mode by default;
- explicit MiniMax Anthropic-compatible adapter;
- same-origin browser chat frontend served by FastAPI at `/`;
- optional `.env.local` loading with process-environment precedence;
- safe provider fallback and truthful mock/model/fallback API semantics;
- append-only, restart-persistent Hot Draft JSONL;
- segment-oriented pending/consumed Cold Draft JSONL;
- pair-aware, Cold-first logical compaction;
- idempotent retry after Cold append succeeds but state advancement fails;
- restart recovery for Hot context, Cold pending segments, and compaction state.

The active Cold Draft preservation contract is reconciled with the MVP
implementation in `docs/COLD_DRAFT.md`.

## Partial

- logical compaction bounds the recent raw-turn tail, but accumulated
  preservation markers leave total model-facing context without a global bound;
- Hot Draft writes are attempted in user-then-assistant order, but a failed user
  write does not prevent the assistant write from being attempted;
- Draft persistence failures fail soft and remain internal, while the public
  response still reports `message_consumed=true`;
- Cold Draft supports `pending_digest` and `consumed` storage states, but no
  runtime component consumes pending segments;
- real-model mode supports one MiniMax Anthropic-compatible adapter; incomplete
  or unsupported explicit configuration falls back to mock mode.

## Known Limits

- Hot Draft remains physically append-only;
- preservation markers currently have no global count cap;
- local Draft files have no multi-process transaction or writer lock;
- request size and total logical context have no application-level global bound;
- Hot and Cold JSONL reads scan their files rather than using an indexed store.

## Not Started

- Conversation Memory;
- Conversation Graph;
- Dream;
- graph or semantic recall;
- PostgreSQL memory;
- embeddings or vector search;
- ContextBuilder or ToolRuntime;
- other organs, agents, tasks, schedulers, or workers.
