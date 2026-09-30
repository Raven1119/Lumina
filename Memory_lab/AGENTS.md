# Memory_lab: evaluation facility for Conversation_Memory

The public `organs` source snapshot excludes evaluation fixtures and lab tests;
the instructions below apply when those authorized local materials are restored.

The maintained memory implementation lives in `Conversation_Memory/engine/`
and is exposed through `Conversation_Memory/facade.py`. This directory owns
synthetic dialogue sets, replay, measurement, and blind judging. Lab code may
import the formal engine; the formal engine must not import the lab.

- Preserve the frozen B1, P8, and P9 preset meanings and their model cache keys.
- Keep `eval_set/scripts/`, `eval_set/gold/`, and `eval_set/build.py` immutable.
- Do not run `holdout_c` without explicit owner authorization.
- Keep model responses cached before applying them. A cache-only replay must
  never make a provider request.
- Use injected or scripted time in tests and replay.
- Historical TASK, DESIGN, and RESULTS documents remain under `docs/history/`.
- Keep raw runs, caches, credentials, and model weights local.

With local fixtures restored, run offline tests using
`../Conversation_Memory/.venv/bin/python -m pytest tests -q`.
For a change to P8 recall or rendering, compare cache-only dev_a and dev_b
probes against the R0 directories recorded in `docs/RESULTS_memory_v1.md`.
