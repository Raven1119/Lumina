# Bounded sequential sibling-call result

Date: 2026-08-27
Baseline: `6534a0069366e32e2d73f4702afccb312129fa24`

## Failure audit

`IPYTHON_AB_RESULT.md` retained three historical
`model_protocol:multiple_tool_calls` failures: conditional Native run 3,
conditional IPython run 1, and aggregation Native run 2. It did not persist
their raw `tool_calls[]`, so exact historical arrays were not reconstructed.

Nine fresh executions of the unchanged fixtures were run against baseline
behavior. Eight completed. Aggregation Native run 2 reproduced one exact
`ORDINARY_SIBLINGS` response with no control:

- `call_00_JpALCfWFEQRyKrhg969p0457`: `read {"path":"data-1.txt"}`
- `call_01_lTOsmlUFnAsRIbeqPXdW5180`: `read {"path":"data-2.txt"}`
- `call_02_pqVNAG2nJho71DUYny353445`: `read {"path":"data-3.txt"}`

The old adapter rejected that otherwise valid ordinary batch before Host
execution. The observed failures were therefore not all `MIXED_CONTROL`.

## Maintained mechanism evidence

Offline acceptance covers two ordered reads with independent causal chains,
atomic invalid-third rejection, success/failure/success continuation, the
four-call bound, duplicate ids, both mixed-control cases, and two IPython
siblings sharing one namespace (`x = 41`, then `print(x + 1)` -> `42`).
Durable reload folds to the same State, and combined provider-visible sibling
results remain inside the minimum 768-character absolute Context bound without
truncating canonical EventLog results; separate four-call success and
four-call failure cases lock that lower bound. Restart tests also prove that a
settled prefix, including a filesystem-confirmed interrupted Write, is never
replayed: only the frozen decision's never-started ToolCall suffix executes.

## Fresh real DeepSeek regression

The exact historical aggregation prompt, 18-file fixture,
`FileContentEquals("answer.txt", "63")`, 10-decision limit, 2,000-character
Context bound, model `deepseek-v4-pro`, non-thinking mode, and non-streaming
request were reused. No prompt was strengthened.

| Batch | Run | Multi decision | Tool count | Completion |
| --- | ---: | --- | ---: | --- |
| 1 | 1 | none | - | verified |
| 1 | 2 | none | - | verified |
| 1 | 3 | none | - | verified |
| 2 | 1 | `shell`, `shell` | 2 | verified |
| 2 | 2 | none | - | verified |
| 2 | 3 | none | - | verified |

The observed multi decision was:

- `call_00_k8YrWDw3xGga52bnPLlm6868`: `shell {"argv":["ls","-la"]}`
- `call_01_a0oHzhrnGccB3rfb25O04193`: `shell {"argv":["cat","data-*.txt"]}`

Started order and settled Observation ids exactly matched provider order. Both
results were accepted by the next real provider decision, and authoritative
completion verification passed. Across six fresh runs: 6/6 completed, one
multi-tool decision was observed, and the old failure occurred zero times.

## Result

**PASS.** A real ordinary sibling batch crossed the bounded sequential path.
This does not claim improved task success rate and does not authorize parallel
execution, mixed controls, scheduling, transaction/rollback, Child, Spawn,
recursion, or a benchmark framework.
