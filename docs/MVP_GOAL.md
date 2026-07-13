# Cold Draft MVP Goal

Lumina's current goal is a minimal, restart-persistent local chat runtime with a
same-origin browser frontend:

```text
existing Hot Draft context
-> mock or explicitly configured real model
-> user and assistant/fallback Hot Draft persistence
-> threshold-triggered Cold-first compaction
-> pending Cold Draft segment
-> bounded logical Hot Draft context after restart
```

The MVP includes only real/mock chat, Hot Draft, Cold Draft, compaction, safe
fallback, and restart continuity. It is not long-term memory and does not include
Conversation Memory, Dream, graph recall, PostgreSQL memory, or other organs.

The product direction is recorded in `docs/final_goal.md`. The restored
Cold-first preservation contract is authoritative in `docs/COLD_DRAFT.md`.
