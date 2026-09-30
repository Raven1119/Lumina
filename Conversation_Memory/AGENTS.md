# Conversation Memory work

Read the root `AGENTS.md` and [Memory v1 design](docs/DESIGN.md) before changing
this organ. The current task supplies authorization.

The public production boundary is `facade.py`: read and render, append a trace,
run one Dream window, and inspect. `engine/` owns P8 computation and SQLite
state; `prompts/` holds versioned text. Chat owns Hot/Cold and passes explicit
turns, time, paths, and model factories. Dream is the only graph writer. Keep
Cold text immutable and its mirror rebuildable.

For an algorithm or prompt change, compare cache-only P8 replay on both
development sets against the R0 directories in
`Memory_lab/runs/memory_v1/`, then run the all-probe Chat/Lab block check.
Production embedding accepts the pinned BGE-M3 identity only. Use temporary
state in tests and keep real model calls inside the task budget.
