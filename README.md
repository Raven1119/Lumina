# Lumina code map

The `organs` branch is a clean source snapshot rooted independently of the
historical experiment commits. The original local branch and its evidence are
preserved; this public branch excludes generated runs, caches, raw evaluation
sets and old Canvas working files. See [Memory Lab](Memory_lab/README.md) for
the source-only laboratory boundary.

[Current status](docs/CURRENT_STATUS.md) records implemented and tested facts. [Organs](docs/ORGANS.md) is the runtime contract; [scheme catalog](docs/EXPERIMENTS.md) separates current Memory from historical experiments. [North Star](docs/NORTH_STAR.md) supplies direction.

## Entry points

| Entry | Owner and behavior |
| --- | --- |
| `Conversation_Memory/.venv/bin/python -m uvicorn core.main:app --workers 1` | Chat API and static frontend. `user.message` enters Nervous; serial Mind dialogue may answer, read, recall, or delegate. Helper questions/reports wake non-dialogue Mind. |
| `Conversation_Memory/.venv/bin/python -m Dream.runner run` | Manual one-window Dream; `rebuild`, `cursor-end` and `inspect` are separate commands. Stop Chat before cursor changes. |

API: `POST /api/chat`, `GET /api/history`, `GET /api/status`, `GET /api/memory`, `POST /api/dream/run`. The previous manual `/api/execution` and `python -m Mind` cognitive entry were retired by Organ B.

## Current route

```text
His message → Nervous WAL event → serial Mind dialogue thought
    → Language speech outlet → core Hot / trace → Cold-first compaction
    → optional automatic Dream
                   └→ mind.spawn → Execution helper pool (at most 2)
                                     └→ agent.question / agent.report → Mind event thought
                                                          └→ optional proactive speech
```

Mind can read `workspace/` and recall Memory but cannot write there. A helper uses Execution V2 in Docker: workspace read-only, its `workspace/tasks/<helper ID>/` writable, no network. The helper chooses implementation; Mind sees only its questions and reports. The fixed event registry contains no alarms or self-created triggers. See [Organ B task](docs/TASK_organs_b.md) and [results](docs/RESULTS_organs_b.md) for measured scope.

| Area | Source and tests |
| --- | --- |
| Nervous transport, focus, scheduling | [bus.py](Nervous/bus.py), [scheduler.py](Nervous/scheduler.py), [event_triggers.py](Nervous/event_triggers.py), [lumina_state.py](Nervous/lumina_state.py); [organ tests](tests/test_organs_b_helpers.py) |
| Mind dialogue and helper decisions | [runner.py](Mind/runner.py), [dialogue_state.py](Mind/dialogue_state.py), [helper_actions.py](Mind/helper_actions.py); [A1 byte oracle](tests/test_organs_a1.py), [A2 tests](tests/test_organs_a2.py) |
| Language | [channel.py](Language/channel.py), [rephrase.py](Language/rephrase.py); [language policy tests](tests/test_organs_b_language.py) |
| Execution helpers | [pool.py](Execution/pool.py), [organ.py](Execution/organ.py), [sandbox.py](Execution/sandbox.py); [Docker tests](tests/test_organs_b_docker.py) |
| Chat, drafts, provenance | [main.py](core/main.py), [dialogue_io.py](core/dialogue_io.py), `draft_store.py`, `cold_draft_store.py`, `hot_draft_compactor.py`; [Cold contract](docs/COLD_DRAFT.md) |
| Memory / Dream | [facade.py](Conversation_Memory/facade.py), [Dream runner](Dream/runner.py), [memory results](docs/RESULTS_memory_v1.md) |

## Configuration and development

Set models in [config/model.toml](config/model.toml); the initial shared default is DeepSeek V4.1 Flash (`deepseek-flash`). A global or role environment override is also supported. Organ limits and language policy are in [config/lumina.toml](config/lumina.toml). `.env.local` is local and must never be committed. `language.render=proactive_only` sends ordinary replies directly and routes proactive speech through Language. Explicit `always` and `mind_choice` remain. A1 preserves the memory-v1 wire request. A2 Mind uses the full 17-paragraph [background](prompts/chat_background.md); Language uses [voice](prompts/language_voice.md).

Use Linux and the prepared environment:

```bash
Conversation_Memory/.venv/bin/python -m pytest -q
Conversation_Memory/.venv/bin/python -m pytest tests -q
Conversation_Memory/.venv/bin/python -m pytest Execution -q
```

Memory v1 P8 is production recall; P9 remains lab-only. Historical standalone cognition/Execution documents were moved to [docs/history](docs/history), and raw experiment artifacts stay local. A test count proves the tested wiring, not general autonomous ability.
