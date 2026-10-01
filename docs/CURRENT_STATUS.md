# Lumina current state

Updated for Organ B2 on 2026-10-01. [ORGANS](ORGANS.md) describes the implemented contract; [B2 results](RESULTS_organs_b2.md) separate scripted checks, Docker checks and real scenarios.

## Running architecture

`core/main.py` is the single-process Chat service (`--workers 1`). It publishes each user input as `user.message` to the Nervous SQLite WAL bus. One scheduler worker runs Mind thoughts serially. A1 retains memory-v1 request bytes. A2 uses the original 17-paragraph `chat_background.md` and native tool calls for reading workspace files, read-only recall and helper actions. Each model response and tool result is durably recorded; invalid calls return a tool error in the next step. Default `language.render=never` sends dialogue and proactive speech directly; other rendering modes remain configurable. Language writes final speech through core's Hot owner and stores Mind/original and final text in its durable receipt. Cold-first compaction and automatic Dream checks remain with their original owners.

Mind can delegate a four-field task contract (goal, reason, acceptance, context). `Execution/pool.py` queues helpers and runs at most two `ExecutionOrgan` V2 loops. Docker mounts the whole configured workspace read-only and each helper's own task directory writable, with networking disabled. The helper's event log stays outside the mounted workspace. Questions, terminal reports, cancellation and guard returns travel through Nervous. Helper questions wake a Mind event thought with recent dialogue and the task's question/answer history. Lack of a reply, hold or cancellation leads to the fixed automatic response. Holds remain visible and expire after 24 hours. The helper's `.lumina-outcome` records its self-reported result; terminal state and report event commit together. `/api/status` and the frontend show actual state, focus, helper rows and dead-letter count. Each event failure is isolated and retried up to three times.

`/api/chat` responds with `model` speech, `none` for no speech, `error` for a processing failure, or `pending` after first-reply timeout. The latter three do not append Hot turns. The frontend hides `none`, displays a restrained system error for `error`, and continues history polling for `pending`. `scripts/usage_report.py --since YYYY-MM-DD` opens the Nervous database read-only and summarizes calls, tokens, tools and errors without dialogue bodies by default.

The former `python -m Mind` cognitive chain, `Execution/runtime.py`, old Nervous Stage1, old provider/working-context path and `/api/execution` were retired. Historical designs, results and applicable licenses are in [history](history/) and `Execution/THIRD_PARTY/`. `Nervous/storage.py` remains because Execution V2 uses its atomic/integrity helpers.

## Data and limits

`core/` owns Hot and Cold; Cold source text, IDs and times remain immutable. `Conversation_Memory/facade.py` owns read-only recall and traces. Dream alone writes the long-term memory graph. Memory v1 uses P8 and local BGE-M3; P9 is lab-only. Dream has a separate lock and does not hold the Chat write lock across model calls. Credentials and real draft/memory data stay local under `.env.local` and `data/`.

The B2 report records baselines, deterministic checks and all first-run and repeat real scenarios. On this WSL installation Docker Desktop can run the image but could not mount the default Linux repository path; temporary Windows-backed synthetic workspaces were used for Docker and real scenario checks. This does not validate deployment with the default mount.

## Model policy

`model_policy.py` and `config/model.toml` choose a shared default `deepseek-flash` with role overrides. `config/lumina.toml` controls Mind protocol, tool cap, Language rendering, workspace, helper concurrency, guard and frontend polling. A1 remains a rollback option; A2 is the current default based on the prior persona comparison, whose judgments and limits remain in [A persona results](RESULTS_organs_a_persona.md). B2 did not run a blind evaluation by task design.

## B2 patch in progress

Mind filters stale helper questions before starting a thought, acknowledges them with content-free event/helper/reason receipts and counts `工具·过时提问`. A fully stale batch starts no thought or automatic answer. Unanswered questions at helper termination are marked and shown as “（它没等答复就结束了）” in report context. Dialogue/event prompts implement the supplied same-thought action instructions; the supplied helper prompt requires waiting immediately after asking and forbids repeating an answered question.

The 2026-10-01 patch check found Linux Docker Client installed but no default socket. Windows Docker Server is reachable, while the default repository workspace bind failed; default-workspace R1 remains unverified (environment). [Patch results](RESULTS_organs_b2_patch.md) records current validation and outstanding work.
