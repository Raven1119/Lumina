# Working on Mind

Read the root instructions, [current status](../docs/CURRENT_STATUS.md) and
[chain contract](docs/INTEGRATED_CHAIN.md) for the affected behavior.
[NORTH_STAR](../docs/NORTH_STAR.md) guides design; the current task governs scope.

Mind is the persistent high-level judgment owner, outside Execution's actor tree.
`organ.py` owns semantic activities, one authorized task and analysis references.
`cognition.py` atomically accepts selective updates; `trace.py` preserves the
actual activity and native continuation. `model.py` supplies bounded role input.
Working background is a derived projection, separate from accepted cognition;
original activity pieces remain readable by bounded history references. Recovery
uses the frozen original request before preparing a future context. See the
[operating contract](docs/INTEGRATED_CHAIN.md) for pause, capacity and context modes.
`analysis.py` owns an optional independent calculation/understanding context;
`world_model.py` runs isolated programs without business workspace access.

Mind models see only data plus read/analysis tools. They do not receive
Execution handles, shell, filesystem or IPython authority. Preserve source
identity and the difference between observations, assumptions and judgments.
Do not repair model semantics in hidden runtime code.

User and important Execution events arrive through Nervous. Mind may inspect
evidence, analyze, revise cognition and choose NoChange or an original high-level
Directive. Execution owns applicability, one causal delivery, actual actions
and result feedback. NoChange can close a review without erasing prior advice.
DecisionIntent is representable; formal goal switching is not implemented.

Preserve current crash recovery, bounded costs and pending failures. Retired
experimental contracts and campaign replay are not compatibility obligations.
History conclusions live in [EXPERIMENT_HISTORY](docs/EXPERIMENT_HISTORY.md);
upstream provenance/licenses live in [REFERENCES](docs/REFERENCES.md).

The Recall gates (`constant_gate.py`, `llm_gate.py`) remain production Chat
components and do not run this cognitive loop. Do not change that caller
boundary without authorization.

Use focused current-invariant tests, then the affected Mind/Nervous/Execution
suite. Test mechanics with injected responses; use real-model calls when the
claim concerns model judgment. Do not turn a structural refactor into a new
architecture-benefit experiment.
