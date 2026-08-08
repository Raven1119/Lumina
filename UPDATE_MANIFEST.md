# Lumina Documentation Alignment Manifest

Basis: `docs/LUMINA_CODEBASE_SCAN.md` (2026-08-05).

Replace these repository files:

```text
AGENTS.md
README.md
docs/CURRENT_STATUS.md
docs/final_goal.md
Conversation_Memory/AGENTS.md
Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md
Dream/AGENTS.md
Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md
docs/DREAM_UI_CODE_AUDIT.md
docs/MEMORY_SYSTEM_CODE_SCAN.md
```

Intentionally unchanged:

```text
docs/COLD_DRAFT.md
docs/NORTH_STAR.md
docs/MVP_GOAL.md
Conversation_Memory/docs/RELEVANCE_GATE_DESIGN.md
docs/CROSS_TURN_REFERENCE_AUDIT.md
```

Key alignment decisions:

- current Hot is one rolling summary plus recent raw turns;
- Cold is one original turn per line, grouped and consumed by segment;
- Dream has browser/API and stop-the-service CLI entrypoints;
- Chat and Dream share an in-process writer lock and resident memory backend;
- Recall includes dense+lexical RRF, temporal hard filters, fixed/adaptive graph
  traversal, and Context Linearization;
- History is a read-only single-conversation projection;
- Memory v1 boundaries are frozen; the next recommended boundary is a docs-first
  Mind System caller-contract design;
- historical audits/scans are marked as such rather than rewritten as current
  authority.
