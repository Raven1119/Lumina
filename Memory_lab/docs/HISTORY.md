# Memory Lab history

The laboratory is the evaluation facility for the promoted
[Conversation Memory v1](../../Conversation_Memory/docs/DESIGN.md). Its supported
presets are B1 (raw Cold baseline), P8 (production memory side), and P9
(isolated candidate). Historical task cards, designs and results are preserved
verbatim in [history](history/).

| Period | Decision and evidence |
| --- | --- |
| v1 and answer v2 | Established scripted development sets, probe scoring, original Hot/Cold replay, and separate answer evaluation. See [TASK_CARD](history/TASK_CARD.md), [RESULTS_v1](history/RESULTS_v1.md), [RESULTS_answer_v2](history/RESULTS_answer_v2.md). |
| v3 | Explored usage, time and association behavior; later P8 rules superseded these presets. See [TASK_v3](history/TASK_v3.md), [RESULTS_v3](history/RESULTS_v3.md). |
| v4 | Separated answer and memory evidence; blind judging and counterfactual checks exposed long-set answer limits. See [TASK_v4](history/TASK_v4.md), [RESULTS_v4](history/RESULTS_v4.md). |
| v5 | Pattern grouping improved some memory-side cases but did not establish answer benefit. See [DESIGN_v5](history/DESIGN_v5.md), [RESULTS_v5](history/RESULTS_v5.md). |
| v5.1 | P8 fixed `pattern_v2`, `integrate_v4`, `render_v4` and quote-grounded usage. It became the memory-v1 source. See [DESIGN_v5_1](history/DESIGN_v5_1.md), [RESULTS_v5_1](history/RESULTS_v5_1.md). |
| v5.2 | P9 tested trace patterns and tolerant entity names, but remained isolated. See [DESIGN_v5_2](history/DESIGN_v5_2.md), [RESULTS_v5_2](history/RESULTS_v5_2.md). |

The R0 and post-migration cache-only comparisons, all-probe Chat/Lab equality,
and remaining limits are recorded in [RESULTS_memory_v1](../../docs/RESULTS_memory_v1.md).
