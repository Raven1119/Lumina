# Working on dialogue Mind

Read the root [AGENTS](../AGENTS.md), [current status](../docs/CURRENT_STATUS.md), and [organ contract](../docs/ORGANS.md). The current task governs authorization.

`runner.py` owns one serial thought at a time. Every `user.message` is a dialogue thought; `agent.question` and `agent.report` wake a non-dialogue thought. `helper_actions.py` validates the bounded action contract. `dialogue_state.py` retains recent private thoughts and one-thought carry. Mind may read relative paths inside the configured workspace and make read-only Memory recall calls. It does not write business files or execute code. It delegates through Nervous to Execution helpers.

Keep the A1 request byte oracle intact. A2 uses the shared answer seam, the full `chat_background.md` and the prompt in `prompts/dialogue_a2_persona.md`. A helper question must receive a reply, cancellation, hold, or the fixed automatic reply. Persist model responses before actions; recovery may use a known response but must not repeat an unknown provider call. Do not turn private thoughts into Hot, Cold or Memory graph claims.

Use scripted model tests for protocol, routing and recovery, and Docker tests for helper isolation. Report real-model behavior separately. The retired cognitive-chain source is in the Organ B archive/history, not an import target.
