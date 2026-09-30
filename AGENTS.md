# Working on Lumina

Lumina aims at a continuous mind, independent judgment and long-term symbiosis with its creator. [NORTH_STAR](docs/NORTH_STAR.md) is direction; [CURRENT_STATUS](docs/CURRENT_STATUS.md) records current facts. The current task grants scope. Historical milestones are evidence, not runtime gates.

For platform and Codex placement follow `docs/final_goal.md#target-platform`. Start with the [code map](README.md), [scheme catalog](docs/EXPERIMENTS.md), HEAD and working tree. Read local instructions for affected owners. Finish authorized implementation, validation and delivery; decide ordinary reversible details, preserve failures and unknowns, and report deviations.

## Current organs and owners

| Owner | Current entry and boundary |
| --- | --- |
| Nervous | `Nervous/bus.py`, `event_triggers.py`, `scheduler.py`, `lumina_state.py`: SQLite WAL event transport, fixed triggers, one serial Mind worker, actual state/focus. Transport does not judge. |
| Mind | `Mind/runner.py`, `dialogue_state.py`, `helper_actions.py`: every Chat input is a dialogue thought; helper questions/reports trigger non-dialogue thoughts. Mind may read `workspace/` and recall Memory, but does not write files. |
| Language | `Language/channel.py`, `rephrase.py`: durable speech outlet through core Hot and trace owners. Ordinary replies are direct by default; proactive speech uses the language model. |
| Execution | `Execution/pool.py` runs at most two helpers around the durable `Execution/organ.py` V2. `sandbox.py` mounts the whole workspace read-only and only one task directory writable, with no network. Helpers choose implementation; their reports and questions return as events. |
| Core draft | `core/main.py`, `dialogue_io.py`, Hot/Cold stores and compactor retain Chat API, original turns and rolling summary ownership. A1 preserves memory-v1 request bytes. |
| Memory and Dream | `Conversation_Memory/facade.py` is the read/trace/Dream boundary. Dream alone writes the graph; Cold retains immutable source turns. Read local instructions in `Conversation_Memory/AGENTS.md` and `Dream/AGENTS.md`. |

[ORGANS](docs/ORGANS.md) gives event, state, recovery and configuration contracts. The old standalone Mind chain and `/api/execution` endpoint were retired in Organ B. Its source and designs are in `docs/history/` and local archive tags; new code must not import them.

## Preserve boundaries and evidence

- Organs communicate through Nervous events. Basic Mind read/recall actions use their owner directly and write a durable action result. Execution's unknown action outcome requires manual confirmation; never replay it as if it had failed safely.
- Cold preserves each departing Hot turn before compaction. Dream is Memory's sole graph writer. Chat appends only recall traces keyed by assistant turn ID. Recall remains bounded, read-only and fail-soft. Any Recall change needs cache-only P8 comparison on both development sets and Chat/Lab block equality.
- Dream has its own lock. Model calls run outside the Chat write lock. Stop Chat before manual Memory cursor changes. The service is one process (`--workers 1`).
- Model selection is `model_policy.py` plus `config/model.toml`; `deepseek-flash` is the initial shared default with per-role overrides. Historical experiments keep their recorded model identities. `config/lumina.toml` controls organ behavior. Credentials remain in environment variables.
- Preserve `.env.local`, `data/`, real Hot/Cold/Memory, and unrelated changes. Use temporary test state. Public output must not expose private runtime bodies, credentials, or tracebacks. Commit, push, rebase, reset and history rewriting require current authorization. For this task, follow its explicit staged delivery and all-history push audit.

## Validation

Use `Conversation_Memory/.venv/bin/python` in Linux. Check affected owner tests and the whole maintained tree with `python -m pytest -q`; for Chat run `python -m pytest tests -q`, and for Execution V2 run `python -m pytest Execution -q`. Docker isolation tests use synthetic temporary files and zero model calls. Historical deleted-chain tests are not resurrected. Review the full task diff and run `git diff --check`. Distinguish scripted wiring, Docker isolation and real-model behavior in reports.

<!-- BEGIN lumina-interface-language -->
## Lumina frontend design language

For this project's frontend appearance and interaction work, read
`.agents/skills/lumina-interface-language/SKILL.md` first.
Visual authority: its `assets/reference.html`, tokens and screenshots.
Motion authority: its `assets/motion-reference.html` (Research Interface Language 1.0.0 / V9).
Use Lumina's mineral / depth / stone palettes; preserve the accepted matte visual language.
Do not substitute the Research/CyberScientist visual skin, old purple palette,
custom plate animations, or approximations of the approved 15 motion mechanisms.
Keep real business behavior and project layout; demo geometry is not a mandatory avatar.
Apply only required effects. Follow CHECKLIST.md and report actual test results.
<!-- END lumina-interface-language -->
