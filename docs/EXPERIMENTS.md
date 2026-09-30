# Lumina scheme catalog

This catalog separates production routes from frozen research evidence. [Current status](CURRENT_STATUS.md) and [Organs](ORGANS.md) describe the running code.

| Route | Status | Evidence |
| --- | --- | --- |
| Memory v1 P8 | Production Chat recall | [Design](../Conversation_Memory/docs/DESIGN.md), [facade](../Conversation_Memory/facade.py), [migration result](RESULTS_memory_v1.md) |
| A2 dialogue + proactive-only Language | Current organ default | [Organ B task](TASK_organs_b.md), [Organ B result](RESULTS_organs_b.md), [A persona blind comparison](RESULTS_organs_a_persona.md) |
| Execution V2 helper pool | Current delegated action route | [pool](../Execution/pool.py), [V2](../Execution/organ.py), [sandbox](../Execution/sandbox.py), [B result](RESULTS_organs_b.md) |
| B1 raw-window recall | Lab baseline | `Memory_lab/lab/ --preset B1`; [lab history](../Memory_lab/docs/HISTORY.md) |
| P9 recall | Lab candidate, not production | `Memory_lab/lab/ --preset P9`; [lab history](../Memory_lab/docs/HISTORY.md) |
| Former graph adapter and Chat gate | Historical | Local `archive/pre-memory-v1` tag, [memory history](MEMORY_EXPERIMENT_HISTORY.md) |
| Former standalone cognitive Mind, Stage1 and context/repetition campaigns | Historical, removed from runtime | [Mind experiment history](history/Mind/EXPERIMENT_HISTORY.md), [Stage1 result](history/Mind/INTENTION_STAGE1_RESULT.md), [repetition result](history/Mind/REPETITION_REASSESSMENT_RESULT.md) |

Historical C1 working-context, C2 repetition and C3 serial Task results remain evidence of those frozen campaigns. Their former `python -m Mind` commands and old test paths are not current entry points. The old design documents are in [docs/history](history/); the Organ B result lists deletion and retained dependencies. Experimental Canvas material remains design, without runtime authority.
