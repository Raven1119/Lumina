# Cold Draft MVP Goal — Completed Historical Milestone

This file records the original Cold Draft MVP target. It is no longer Lumina's
current production objective and must not be used to infer that Conversation
Memory, Dream, or Recall are unimplemented.

The completed milestone was:

```text
browser chat
-> mock or explicitly configured real model
-> restart-persistent Hot Draft
-> Cold-first logical compaction
-> pending Cold Draft segment
-> bounded recent raw context after restart
```

Lumina has since added manual Dream ingestion, an unmodified MAGMA-backed
Conversation Memory adapter, bounded graph-enhanced Recall, deterministic Context
Linearization, and optional production chat Recall injection.

Use these documents for current decisions:

- `docs/CURRENT_STATUS.md` for implementation facts;
- `docs/final_goal.md` for the current production objective;
- `AGENTS.md` for active development boundaries;
- `docs/COLD_DRAFT.md` for the preservation invariant.

The historical MVP remains important because every later memory and product
feature must preserve its Cold-first durability contract.
