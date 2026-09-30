# Lumina current state

Updated for Organ B on 2026-10-01. [ORGANS](ORGANS.md) describes the implemented contract; [RESULTS_organs_b](RESULTS_organs_b.md) separates scripted checks, Docker checks and real scenarios.

## Running architecture

`core/main.py` is the single-process Chat service (`--workers 1`). It publishes each user input as `user.message` to the new Nervous SQLite WAL bus. One scheduler worker runs Mind thoughts serially. A1 retains memory-v1 request bytes. A2 uses the original 17-paragraph `chat_background.md`, bounded steps, direct workspace reads and read-only Memory recall. Ordinary A2 replies are sent directly by default (`language.render=proactive_only`); proactive speech after a helper event uses Language. Language writes final speech through core's Hot owner and stores Mind/original and final text in its durable receipt. Cold-first compaction and automatic Dream checks remain with their original owners.

Mind can delegate a four-field task contract (goal, reason, acceptance, context). `Execution/pool.py` queues helpers and runs at most two `ExecutionOrgan` V2 loops. Docker mounts the whole configured workspace read-only and each helper's own task directory writable, with networking disabled. The helper's event log stays outside the mounted workspace. Questions, terminal reports, cancellation and guard returns travel through Nervous. Helper questions wake a Mind event thought; lack of a reply, hold or cancellation leads to the fixed automatic response. Holds remain visible and expire after 24 hours. `/api/status` and the frontend show actual state, focus and helper rows; the frontend polls while helpers run.

The former `python -m Mind` cognitive chain, `Execution/runtime.py`, old Nervous Stage1, old provider/working-context path and `/api/execution` were retired. Historical designs, results and applicable licenses are in [history](history/) and `Execution/THIRD_PARTY/`. `Nervous/storage.py` remains because Execution V2 uses its atomic/integrity helpers.

## Data and limits

`core/` owns Hot and Cold; Cold source text, IDs and times remain immutable. `Conversation_Memory/facade.py` owns read-only recall and traces. Dream alone writes the long-term memory graph. Memory v1 uses P8 and local BGE-M3; P9 is lab-only. Dream has a separate lock and does not hold the Chat write lock across model calls. Credentials and real draft/memory data stay local under `.env.local` and `data/`.

Stage 0 baselines, the 14-probe diagnosis, Stage 1 byte oracle, Stage 2 scripted tests and Docker isolation results are in [the Organ B report](RESULTS_organs_b.md). Stage 4 real R1–R5 outcomes are not inferred from those structural checks. On this WSL installation Docker Desktop can run the image but cannot mount the default Linux repository path until integration is available; temporary Windows-backed synthetic workspaces were used for Docker checks.

## Model policy

`model_policy.py` and `config/model.toml` choose a shared default `deepseek-flash` with role overrides. `config/lumina.toml` controls Mind protocol, Language rendering, workspace, helper concurrency, guard and frontend polling. A1 remains a rollback option; A2 is the current default based on the prior persona comparison, whose judgments and limits remain in [A persona results](RESULTS_organs_a_persona.md). The Organ B language change has deterministic tests only and no new blind evaluation by task design.
