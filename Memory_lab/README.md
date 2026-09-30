# Memory Lab

The public `organs` source snapshot omits raw evaluation sets, judge outputs,
model caches, run directories, and laboratory tests that require those fixtures.
The original materials remain in the local research archive. This branch alone
supports production Chat/Memory and its maintained tests; it does not reproduce
the historical lab scores without the archived evaluation materials.

This is the evaluation facility for [Conversation Memory v1](../Conversation_Memory/docs/DESIGN.md).
`lab/` imports the formal engine in `Conversation_Memory/engine/`. B1 is the
raw-window baseline, P8 is the production memory-side preset, and P9 is an
isolated candidate. The original task, design and result documents are in
[docs/history](docs/history/); [HISTORY](docs/HISTORY.md) summarizes their decisions.

The metric, blind packet builder, `judge_prompt_v2`, and aggregation source
remain here. Scripted development sets and the holdout are excluded from this
public snapshot; using the holdout still requires explicit authorization and
`--allow-holdout`.

With locally restored authorized evaluation materials, use the prepared
environment from this directory:

```bash
../Conversation_Memory/.venv/bin/python -m lab run --set dev_a --preset P8 --llm real --model deepseek-flash --embedder bge-m3 --cache-only --out runs/check_dev_a_P8
../Conversation_Memory/.venv/bin/python -m lab run --set dev_b --preset P8 --llm real --model deepseek-flash --embedder bge-m3 --cache-only --out runs/check_dev_b_P8
```

`runs/` and `cache/` are local artifacts. The memory-side comparisons and
Chat/Lab all-probe equality procedure are in
[RESULTS_memory_v1](../docs/RESULTS_memory_v1.md).
