# Persistent IPython A/B result

Date: 2026-08-26

## Hypothesis

A persistent IPython kernel can serve as the Root's programmable control plane
and reduce model round trips or model-visible intermediate state relative to
the existing native tools without reducing verified completion reliability.

## Frozen design

- Baseline: `Execution_lab2` at
  `7a2e21ed6cf90f10f969eaa89527d3395e1ca5b2`.
- Model: `deepseek-v4-pro`; thinking disabled; streaming disabled.
- Both arms used the same task text, workspace fixture,
  `FileContentEquals`, 10-decision bound, and 2,000-character Context bound.
- Native exposed only `read`, `write`, `shell`, `wait`, and
  `claim_complete`.
- IPython exposed only `ipython`, `wait`, and `claim_complete`, with the
  exact minimal system instruction authorized by the task card.
- Each task/arm used three fresh workspaces and a fresh Root. Each IPython run
  used at most one lazy kernel; kernels were closed at terminal state.
- IPython limits were 20,000 code characters, 10 seconds per execution, and
  10,000 captured output characters.

`result chars` below is cumulative native tool-message content in the actual
provider requests, including repeated visibility in later requests.
`max request` is the largest compact JSON provider-request size in
characters. Tokens are provider-reported totals across that run.

## Task 1: conditional multi-step

| Arm | Run | Verified | Calls | Decisions | Input | Output | Wall s | Result chars | Max request | IPython execs | Native calls | Python failures | Failure |
| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Native | 1 | yes | 4 | 4 | 3,436 | 204 | 6.619 | 884 | 3,016 | 0 | 3 | 0 | - |
| Native | 2 | yes | 7 | 7 | 6,254 | 403 | 10.487 | 1,768 | 3,076 | 0 | 6 | 0 | - |
| Native | 3 | no | 1 | 1 | 698 | 123 | 1.758 | 0 | 2,044 | 0 | 0 | 0 | `multiple_tool_calls` |
| IPython | 1 | no | 2 | 2 | 1,361 | 206 | 4.774 | 372 | 2,377 | 1 | 0 | 0 | `multiple_tool_calls` |
| IPython | 2 | yes | 4 | 4 | 3,076 | 273 | 7.611 | 856 | 2,646 | 3 | 0 | 0 | - |
| IPython | 3 | yes | 3 | 3 | 2,141 | 207 | 6.787 | 435 | 2,531 | 2 | 0 | 0 | - |

Median summary:

| Arm | Success | Calls | Input | Output | Wall s | Result chars | Max request | IPython execs | Native calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Native | 2/3 | 4 | 3,436 | 204 | 6.619 | 884 | 3,016 | 0 | 3 |
| IPython | 2/3 | 3 | 2,141 | 207 | 6.787 | 435 | 2,531 | 2 | 0 |

Success was unchanged. IPython reduced median provider calls, input tokens,
model-visible result characters, maximum request size, and executions. Median
output tokens and wall time were approximately flat. Each arm had one
single-call protocol failure.

## Task 2: small programmatic aggregation

| Arm | Run | Verified | Calls | Decisions | Input | Output | Wall s | Result chars | Max request | IPython execs | Native calls | Python failures | Failure |
| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Native | 1 | yes | 6 | 6 | 6,449 | 420 | 11.002 | 2,756 | 3,831 | 0 | 5 | 0 | - |
| Native | 2 | no | 3 | 3 | 2,575 | 261 | 5.046 | 774 | 3,164 | 0 | 2 | 0 | `multiple_tool_calls` |
| Native | 3 | yes | 7 | 7 | 7,408 | 435 | 11.598 | 3,147 | 3,917 | 0 | 6 | 0 | - |
| IPython | 1 | yes | 3 | 3 | 2,621 | 219 | 7.368 | 1,012 | 3,085 | 2 | 0 | 0 | - |
| IPython | 2 | yes | 5 | 5 | 4,167 | 357 | 9.050 | 1,441 | 3,028 | 4 | 0 | 0 | - |
| IPython | 3 | yes | 4 | 4 | 3,445 | 344 | 8.034 | 1,226 | 3,208 | 3 | 0 | 0 | - |

Median summary:

| Arm | Success | Calls | Input | Output | Wall s | Result chars | Max request | IPython execs | Native calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Native | 2/3 | 6 | 6,449 | 420 | 11.002 | 2,756 | 3,831 | 0 | 5 |
| IPython | 3/3 | 4 | 3,445 | 344 | 8.034 | 1,226 | 3,085 | 3 | 0 |

IPython improved verified success in this sample and reduced every recorded
median cost measure. No IPython execution produced a Python runtime failure.

## Decision

**PROMOTE.** The task-card gate is met in both task categories:

- conditional success was not lower and important median context/call costs
  improved;
- aggregation success increased from 2/3 to 3/3 while provider calls, tokens,
  wall time, model-visible result characters, request size, and executions all
  decreased;
- there was no Python-runtime reliability regression, and all terminal kernels
  were shut down.

This is evidence only for the frozen two-task, three-run-per-arm experiment.
It does not establish general superiority, a sandbox boundary, durable kernel
state, multi-tool-call support, or authority for Child/RLM/recursion.
