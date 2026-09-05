# Wide versus Prime-like Execution Surface Result

## Scope and frozen comparison

This was the second and final Execution-surface experiment. Exactly six fresh
DeepSeek-V4-Pro, thinking-disabled runs executed once in this order:

```text
WIDE-1, PRIME_LIKE-1, WIDE-2, PRIME_LIKE-2, WIDE-3, PRIME_LIKE-3
```

All runs reused the Experiment 1 ordered-route fixtures. Each pair had an
identical fixture fingerprint, Goal, CompletionSpec, Runtime, AgentProcess,
EventLog/DecisionFrame, persistent IPython, eight-decision/four-call bounds,
sequential Child driver, provider settings, and generic hybrid system prompt.

The sole treatment was the model-facing schema projection:

```text
WIDE:       Read, Write, Shell, IPython, SpawnChild, Wait, ClaimComplete
PRIME_LIKE: IPython, SpawnChild, Wait, ClaimComplete
```

Both arms retained the same underlying local filesystem/process capability.
Prime-like could read, write, enumerate, search, aggregate, and invoke
processes through persistent IPython. Runtime authority, SharedEnvironment,
ToolHost, and completion verification were unchanged.

For all three pairs, complete initial provider requests were equal after
removing only Wide's Read/Write/Shell schemas and normalizing fresh
execution/root identities. Both arms remained `tool_mode='hybrid'`; the
system prompt did not change. All validity checks passed.

## Per-run results

| Run | Complete evidence | Repetitive | Redundant after evidence | Write / Claim | Model calls | Input tokens | Wall s | Verified |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| WIDE-1 | D3 | yes | 6 | 0 / 0 | 8 | 7,436 | 12.417 | no |
| PRIME_LIKE-1 | D3 | yes | 3 | 1 / 1 | 8 | 6,769 | 14.434 | yes |
| WIDE-2 | D3 | yes | 6 | 0 / 0 | 8 | 7,433 | 11.548 | no |
| PRIME_LIKE-2 | D6 | yes | 2 | 0 / 0 | 8 | 6,789 | 13.980 | no |
| WIDE-3 | D3 | yes | 5 | 0 / 0 | 8 | 7,900 | 10.286 | no |
| PRIME_LIKE-3 | D3 | no | 0 | 1 / 1 | 5 | 4,053 | 8.173 | yes |

## Aggregate comparison

| Metric | Wide | Prime-like |
|---|---:|---:|
| Verified completion | 0 / 3 | 2 / 3 |
| Complete leaf evidence | 3 / 3 | 3 / 3 |
| Repetitive local search | 3 / 3 | 2 / 3 |
| Post-evidence redundant actions | 17 | 5 |
| Model calls | 24 | 21 |
| Input tokens | 22,769 | 17,611 |
| Wall seconds | 34.251 | 36.587 |
| Native Shell calls | 2 | 0 |
| IPython calls | 0 | 19 |
| IPython subprocess calls | 0 | 0 |
| Spawn intents | 0 | 0 |

The primary authority is verified completion: Prime-like completed two tasks
while Wide completed none, without losing complete-evidence acquisition. The
lower redundant-action and token totals are secondary. Wall time did not
improve because Prime-like paid kernel startup/execution cost.

PRIME_LIKE-1 still reread evidence before writing, and PRIME_LIKE-2 exhausted
the decision limit. The treatment improved this fixed sample; it did not
eliminate over-acquisition.

## Verdict

**PRIME_LIKE_SURFACE_BETTER**

The Prime-like arm produced a clear net basic-execution gain under the
preregistered priority rule: higher verified completion with equal complete
evidence and no observed capability loss.

## Final model-facing surface decision

Adopt the smaller Execution surface for the next engineering stage:

```text
IPython
SpawnChild
Wait
ClaimComplete
```

IPython is the single general reality-operation entry. This is an engineering
choice based on the final fixed evidence and the feasibility of a minimal
programmable surface. It does **not** claim that IPython-only is theoretically
optimal, that Shell is always harmful, or that capability compression
universally improves agents.

## Evidence limits and stop rule

- The sample is three paired fixtures with one run per arm; no statistical
  generalization is claimed.
- IPython acquisition/write categories are a deterministic, auditable code-
  surface projection. Exact calls and observations remain in the artifact.
- Prime-like was not uniformly successful and had slightly higher aggregate
  wall time.
- These results concern basic local filesystem execution, not recursive
  usefulness, browser/API/device work, or security isolation.
- Per the preregistered task card: **STOP EXPERIMENTING**. No third surface,
  ablation, prompt, fixture family, or recursive experiment follows.

Canonical evidence: `PRIME_LIKE_SURFACE_AB_ARTIFACT.json`.
