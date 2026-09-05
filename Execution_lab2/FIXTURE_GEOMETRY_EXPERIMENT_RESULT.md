# Fixture Geometry Isolation Result

## Scope and frozen comparison

This was the first of the final two Execution-surface experiments. Exactly six
fresh DeepSeek-V4-Pro, thinking-disabled runs executed once in this order:

```text
SIBLING-1, ROUTE-1, SIBLING-2, ROUTE-2, SIBLING-3, ROUTE-3
```

Every arm used the same Runtime, AgentProcess, EventLog/DecisionFrame,
persistent IPython implementation, FileContentEquals verifier, eight-decision
bound, four-call admission bound, sequential Child driver, resource limits,
provider settings, Goal, and wide hybrid model-facing surface:

```text
Read, Write, Shell, IPython, SpawnChild, Wait, ClaimComplete
```

Each pair held payloads, two leaves, required leaf evidence, `/` join,
`outcome.txt`, completion wording, directory depth, and total 1,600 UTF-8
source bytes fixed. The only intended landscape change was:

```text
SIBLING: mission -> two independent index files -> two leaves
ROUTE:   mission -> one index file with ORDER_A/ORDER_B -> two leaves
```

All three complete initial provider-request pairs were equal after normalizing
only fresh execution/root identities. Pair-fidelity and provider-setting
checks passed with no provider or unrecorded admission anomaly.

## Results

| Run | Complete evidence | First no-progress | Repetitive | Post-evidence redundant actions | Write / Claim | Verified | Terminal |
|---|---:|---:|---|---:|---:|---:|---|
| SIBLING-1 | D6 | D3 | yes | 3 | 0 / 0 | no | decision limit |
| ROUTE-1 | D3 | D4 | yes | 6 | 0 / 0 | no | decision limit |
| SIBLING-2 | D4 | D3 | yes | 8 | 0 / 0 | no | decision limit |
| ROUTE-2 | D3 | D4 | yes | 6 | 0 / 0 | no | decision limit |
| SIBLING-3 | not reached | D3 | yes | 0 | 0 / 0 | no | decision limit |
| ROUTE-3 | D3 | D4 | yes | 4 | 1 / 1 | yes | completed |

Every run selected local Read actions; none used Shell, IPython, SpawnChild, or
Wait. Five runs spent the full eight decisions without a completion intent.
ROUTE-3 alone transitioned from rereading to Write at D7 and ClaimComplete at
D8.

The preregistered pair profile was:

```text
(verified completion, post-evidence transition, not repetitive)
```

Pairs 1 and 2 had equal profiles across geometries. Pair 3 favored Route, but
one directionally different pair does not meet the required stable 2/3 paired
direction.

## Verdict

**GEOMETRY_EFFECT_NOT_SUPPORTED**

Two-sibling versus one ordered route did not produce a stable geometry-driven
trajectory difference. Both geometries repeatedly failed to leave evidence
acquisition after enough information was available. The isolated ROUTE-3
completion remains real evidence, but cannot distinguish geometry from model
stochasticity in this fixed sample.

This does not establish that geometry never matters. It establishes that the
candidate mechanism from the cross-experiment audit did not survive this
controlled three-pair test strongly enough to explain the earlier result.

## Evidence limits

- Three pairs are an engineering experiment, not a statistical estimate.
- SIBLING-3 never exposed both leaf markers, so its zero post-evidence count
  means “no post-evidence phase,” not efficient execution.
- The marker projection observes evidence made visible in Tool/IPython/Child
  results; unseen private model state is neither recovered nor inferred.
- The complete artifact retains every DecisionFrame projection, actual
  provider request/response, exposed capability list, ordered typed call,
  observation, termination, usage, and completion state.

Canonical evidence: `FIXTURE_GEOMETRY_EXPERIMENT_ARTIFACT.json`.
