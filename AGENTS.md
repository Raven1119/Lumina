# AGENTS.md

## Current scope

Lumina is a Cold Draft conversational MVP. Its only active runtime chain is:

```text
Browser frontend -> FastAPI -> ModelClient -> Hot Draft -> Cold-first compaction -> Cold Draft
```

The default model is mock. Real MiniMax use must be explicitly configured.

## Required boundaries

- Treat `docs/final_goal.md` and `docs/COLD_DRAFT.md` as active authority for
  product direction and the Draft preservation contract. Do not prune them as
  historical planning material.
- Keep exactly one `MessageRuntime`, one `ModelClient` protocol, one Hot Draft
  owner, and one Cold Draft owner.
- Keep the main chat path synchronous, bounded, and restart-persistent.
- Read prior Draft context before calling the model.
- Persist user before assistant/fallback.
- Write Cold Draft before advancing logical compaction state.
- Never expose credentials, provider bodies, provider URLs, file paths, raw
  exceptions, or Draft internals through public responses.
- Preserve local `.env.local` and `data/` contents.

## Do not add

Do not add Conversation Graph, Conversation Memory, Dream, MAGMA, PostgreSQL,
SQLite graph storage, recall, ContextBuilder, ToolRuntime, embeddings, vectors,
hybrid retrieval, query routing, graph traversal, lifecycle systems, task systems,
agents, schedulers, workers, or future organs.

## Change discipline

- Each task may modify at most three production modules unless a user task card
  explicitly authorizes a repository-wide reduction.
- Keep all existing chat, fallback, Cold-first, restart, and no-leak tests passing.
- Add no dependency without explicit user approval.
- Do not automatically commit, push, rebase, hard reset, or rewrite history.

## Validation

```bash
python -m pytest -q
git diff --check
```
