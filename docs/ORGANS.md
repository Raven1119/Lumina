# Current organ contract

[Organ A](TASK_organs_a.md) established event-driven Chat; [persona correction](TASK_organs_a_persona.md) moved the dialogue default to A2. [Organ B](TASK_organs_b.md) adds delegated helpers and removes the standalone cognitive chain. This document describes running code. Actual validation and limits are in [Organ B results](RESULTS_organs_b.md).

## Ownership and entry

`core/main.py` starts one `Nervous/scheduler.py` worker with the Chat service. Each `/api/chat` input becomes a durable `user.message`; the caller waits up to `chat.first_reply_timeout_s` for its own first speech, then receives the existing fallback if the wait expires. The Mind worker is serial. `Nervous/bus.py` uses SQLite WAL and a per-thread connection. The service requires one process (`--workers 1`).

| Owner | Responsibility |
| --- | --- |
| Nervous | `bus.py` publishes, routes and acknowledges durable events; `event_triggers.py` is the closed trigger registry; `scheduler.py` serializes Mind and dispatches Language/Execution; `lumina_state.py` projects actual state and focus. Nervous does not judge. |
| Mind | `runner.py` runs bounded dialogue and helper-event thoughts; `helper_actions.py` parses the typed action list; `dialogue_state.py` keeps private thought/carry. Mind may read relative workspace files and recall Memory; it cannot write files. |
| Language | `channel.py` is the durable speech outlet; `rephrase.py` builds the optional language-model request. It appends final text through core Hot ownership and records Mind/original and final text in receipts. |
| Execution | `pool.py` registers/queues helpers, limits concurrency, and translates questions/reports to events. `organ.py` and `execution.py` own each V2 action lifecycle and unknown-outcome protection. `sandbox.py` owns Docker isolation. |
| Core draft / Memory / Dream | Core owns Hot, Cold, provenance and compaction. `Conversation_Memory/facade.py` owns recall and traces; Dream alone writes its graph. |

The former `python -m Mind` chain, old Nervous Stage1 and manual `/api/execution` endpoint are retired. Historical descriptions live in `docs/history/`; they are not new runtime imports.

## Events and scheduling

| Event | Target | Effect |
| --- | --- | --- |
| `user.message` | Mind, priority 10 | Dialogue thought; new user messages arriving during a thought are inserted before its next step. |
| `agent.question` | Mind, priority 20 | Non-dialogue thought; answer/cancel/hold required. |
| `agent.report` | Mind, priority 30 | Non-dialogue thought; inspect acceptance and optionally speak proactively. |
| `mind.say`, `mind.trace` | Language | Idempotent speech and first-batch Memory trace. |
| `mind.spawn`, `mind.reply`, `mind.cancel`, `mind.hold`, `guard.return` | Execution pool | Create helper; deliver answer; cancel; hold; signal a guard return. |
| `mind.spoke` | Dream | After dialogue compaction, check existing automatic Dream conditions. |

The scheduler chooses pending user messages before questions before reports. Same-kind helper events available together form one event thought. Responses are journaled before actions. An action uses a stable thought/step/ordinal key; repeated publication is idempotent. A response known after restart is reused. A request with no stored response remains unknown and is never silently called again. Execution V2 applies the same stop-on-unknown-action principle.

## Dialogue and private state

A1 uses the original memory-v1 system/messages/prefill/parameters and one speech. A2 uses the shared `Conversation_Memory/answer.py` request builder: `answer_v5`, full original [17-paragraph background](../prompts/chat_background.md), then [dialogue prompt](../prompts/dialogue_a2_persona.md). It retains recent conversation, optional state block, numbered memory and current message, and previous step results. Missing/malformed optional fields are ignored without retry. A2 takes at most four steps. Read/recall results can advance the thought; on the final step, further result-returning actions are skipped and a bounded interrupted thought is stored. Mind can also dispatch helper actions without another step.

`读` accepts only workspace-relative regular UTF-8 files and truncates at `mind.read_max_chars`; `回忆` is read-only. Active recall is not added to the first automatic recall batch and cannot reinforce it. The first automatic batch alone becomes the assistant-turn trace; final emitted text determines “used” reinforcement. Mind thought/carry never enters Hot, Cold or the graph.

`Mind/dialogue_state.py` stores the most recent `mind.thought_window` nonempty thoughts. It adds time and reason; a selected old thought can survive one further handoff through `带着`, then expires if not carried again. The live status block includes task rows and unanswered helper questions. It is omitted when empty. Own-state and unrelated task planning remain unimplemented.

Default `language.render=proactive_only` sends replies to his messages directly. Non-dialogue proactive speech goes through Language, even if it is the second or third sentence of an event thought, and then writes an assistant Hot turn without a preceding new user turn. Explicit `always` and `mind_choice` retain their prior A2 behavior. Language sees its [voice paragraphs](../prompts/language_voice.md), recent dialogue, current memory block, state line and Mind text. It may return the text unchanged; failure falls back to Mind text and is recorded. A1 never adds a language call.

## Helper execution

Mind's `派活` supplies `目标`, `理由`, `验收`, `背景`. The pool assigns an id and persists the contract in the same SQLite state table as the event bus. Up to `execution.max_concurrent` helpers run in background threads; extra tasks queue. Each helper owns one ExecutionOrgan V2 root, and its existing SpawnChild mechanism remains under that root rather than becoming a Mind-visible helper. All work is under `workspace/tasks/<id>/`.

Docker mounts the configured `workspace/` read-only and the helper's own task directory read-write at the matching nested path, with no network and a non-root uid. The Execution event log/checkpoint stay outside the mounted workspace. The kernel exposes `ask_mind`; a committed request becomes `agent.question`, and the helper waits for `MIND_REPLY`. Mind's answer is delivered through Execution V2 `deliver_event`. The system prompt is [helper.md](../prompts/helper.md). Completion still requires `.lumina-complete` containing `done` followed by `ClaimComplete`; the marker is a control check, not independent proof of task correctness. Failure or cancellation is reported with bounded output paths.

After each decision the guard checks the V2 `repetition_tail` (same action/result and unchanged task files) and the helper-call cap. A triggered `RETURN_REQUESTED` is delivered through `deliver_event` if the actor waits for it; if it is still running, the next model context carries the pending control signal. After the configured grace steps without a report, the pool interrupts the actor and emits a pulled-plug report. There is no cost budget in the runtime pool. An unresolved recovered IPython action reports “失败：需要人工确认”.

## State, status, configuration

`/api/status` returns the actual Lumina states, focus and helper list. “空闲”, “做梦” and “执行中” can coexist. Thinking over a user message sets focus to replying to him; a helper event names the helper. Each helper row gives id, goal and current status: queued, running, waiting, completed, failed, pulled or cancelled. Terminal rows remain visible for `execution.done_keep_hours`. The frontend uses the existing five-second status/history poll while thinking or helpers run. It never invents a progress percentage.

Configure at [lumina.toml](../config/lumina.toml): `nervous.db_path`, `chat.first_reply_timeout_s`, `mind.protocol`, dialogue and non-dialogue step/read/thought/carry limits, `mind.nondialogue_thinking=low`, `mind.nondialogue_recent_turns=0`, `language.render/model/recent_turns`, `workspace.path`, `execution.max_concurrent/helper_call_cap/repeat_threshold/return_grace_steps/question_hold_hours/done_keep_hours`, `frontend.poll_interval_s`, and `model.max_output_tokens`. Model identity is selected separately by [model policy](../model_policy.py) and [model.toml](../config/model.toml); credentials remain environment-only.

The non-dialogue model request uses native low-effort thinking without prefill. Invalid JSON or exhausted output gets one disabled-thinking prefill retry. If a helper question ends with no answer, cancellation or hold, Nervous supplies the fixed automatic answer. A held question remains in state; after the configured age it receives the same automatic answer. No other autonomous trigger is registered.
