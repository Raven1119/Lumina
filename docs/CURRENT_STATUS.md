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

The original Cold Draft design intent has been restored and reconciled with the
MVP implementation in `docs/COLD_DRAFT.md`.

## Known Limits

- Hot Draft remains physically append-only;
- preservation markers currently have no global count cap;
- `pending_digest` segments have no runtime consumer;
- local Draft files have no multi-process transaction or writer lock.

## Not Started

- Conversation Memory;
- Conversation Graph;
- Dream;
- graph or semantic recall;
- PostgreSQL memory;
- embeddings or vector search;
- other organs, agents, tasks, schedulers, or workers.
