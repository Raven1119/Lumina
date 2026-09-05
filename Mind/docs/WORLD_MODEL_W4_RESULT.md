# World Model Experiment W4 Result

## Verdict

```text
W4_INCONCLUSIVE
```

The single preregistered real campaign was executed once on 2026-09-01 with
`deepseek-v4-pro`. There was no retry, fallback, selective rerun, prompt change,
or parameter change.

W4 proved that the experiment adapter sends real Anthropic-compatible `tools`
and receives genuine structured `tool_use` blocks. It did not prove the full
`tool_use -> host observation -> tool_result -> next model call` trajectory in
the real campaign: on the first turn of every episode, DeepSeek returned three
read calls in one response while the frozen W4 host permits exactly one tool
call per turn. Every episode therefore failed closed before any tool executed.

This is safe and not a provider or JSON-parser failure. It is a newly exposed
cardinality mismatch between the provider-native action affordance and W4's
single-action host contract.

## Frozen run identity

```text
provider              deepseek-anthropic
endpoint              https://api.deepseek.com/anthropic
model                 deepseek-v4-pro
thinking              disabled
temperature           0
max tokens            1600
timeout               45 seconds
retry / fallback      none / none

manifest SHA-256      debb82ad9ebb3eb6f522cda47376d4a61889aafc15e6f14c699a473a08e495eb
implementation SHA-256
                      044f3da4cffc3decb48724d6040d07cbd73468babbdf594c631d8dd82d047992
prompt SHA-256        6289a41df94da1bfcc15585be329ec692d7805f91b20b9fc9fa847e8f78a54a9
tools SHA-256         31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086
result SHA-256        e4d134ae7a4f2c1f2f5c442cbc1c43841422f7f927d5f351d429ee22d82cf8de
```

The result artifact is
`Mind/fixtures/w4/real_campaign_result.json` and preserves every actual provider
request and response. The API key is absent from the artifact.

## Direct W3 / W4 comparison

| Dimension | W3 | W4 |
|---|---:|---:|
| native tool protocol | no | yes: real `tools` + `tool_use`; no real continuation reached |
| invalid action envelope | 1 | 4 |
| host-rejected bad reads | 6 | 0 individual reads; 4 multi-call envelopes rejected |
| resolvable write attempts | 2 / 3 | 0 / 3 |
| accepted revisions | 1 / 3 | 0 / 3 |
| reverse verifier reached | yes | no |
| ambiguity unresolved | no | no |
| public exact | 1 / 3 | 0 / 3 |
| hidden exact | 1 / 3 | 0 / 3 |

The native protocol removed W3's prose-plus-JSON parser failure mode in the
sense that prose and structured calls coexisted legally. It replaced that
failure with a different action-contract mismatch: the provider used native
parallel/batched reads while the host required one call.

## Per-record trajectory

All four first turns had the same structure:

```text
assistant text:
I'll start by inspecting the current model, notes, and evidence.

tool_use read_file(path="world_model.py")
tool_use read_file(path="notes/world_model.md")
tool_use read_file(path="evidence.json", start=0, count=2)
```

Each request exposed exactly the four registered tools. Each of the twelve raw
tool calls was individually within its published schema and host bounds. The
response envelope nevertheless contained three calls instead of exactly one,
so `_canonical_action` was never invoked and no read observation was produced.

| Record | Calls / executed tools | Termination | Public | Hidden | Current changed |
|---|---:|---|---:|---:|---:|
| boost-step | 1 / 0 | `STRUCTURAL_FAILURE: invalid_model_action` | 0.5 | 0.0 | no |
| reverse-step | 1 / 0 | `STRUCTURAL_FAILURE: invalid_model_action` | 0.5 | 0.0 | no |
| clamped-step | 1 / 0 | `STRUCTURAL_FAILURE: invalid_model_action` | 0.6 | 0.0 | no |
| insufficient-evidence | 1 / 0 | `STRUCTURAL_FAILURE: invalid_model_action` | 1.0 | n/a | no |

The ambiguity record's 1.0 public score is its unchanged initial model on the
deliberately insufficient public evidence. It did not emit native `unresolved`
and therefore does not satisfy the ambiguity criterion.

## Protocol metrics

Raw campaign totals:

```text
provider calls                         4
native tool_use blocks                12
turns with mixed text + tool_use       4
plain-text turns without tool_use      0
invalid multi-call action envelopes    4
tools executed                         0
real next-turn tool_result observed    0
provider failures                      0
```

The artifact's summary contains:

```text
native_tool_call_count          12
schema_invalid_tool_calls        4
host_rejected_tool_calls         4
invalid_action_envelope_count    4
```

The last two `*_invalid/rejected_tool_calls` values require a qualification.
They are produced from per-turn defaults after W4 rejects `len(tool_use) != 1`;
they do not mean that four individual tool inputs violated schema or host
bounds. Inspection of the preserved raw blocks gives the causally accurate
classification:

```text
individually schema-invalid tool calls   0 / 12
individually host-invalid tool calls     0 / 12
invalid multi-call envelopes             4 / 4
```

This instrumentation ambiguity was not visible in the single-call mock tests.
The frozen implementation and result are not rewritten after the real run.
Future work must separate per-call validity from envelope cardinality before
using these aggregate names quantitatively.

`native_protocol_pass=true` proves actual request `tools` and structured
response content. `tool_call_pairing_pass=true` is vacuous in this run because
no host result or next request existed. The scripted tests prove the adapter's
pairing behavior, but the real campaign supplies no empirical `tool_result`
continuation evidence.

## Semantic and source-contract review

No episode reached `write_file(world_model.py)`. Therefore:

| Record | Protocol-valid source proposal | Semantically meaningful? | Source contract accepted? |
|---|---:|---:|---:|
| boost-step | none | n/a | n/a |
| reverse-step | none | n/a | n/a |
| clamped-step | none | n/a | n/a |
| insufficient-evidence | none | n/a | n/a |

There is no W4 clamped proposal to compare with W3's meaningful zero-floor
proposal, and no AST rejection occurred. The frozen AST contract was unchanged;
it simply was never reached.

## Acceptance criteria

### Passed

- Reality Evidence remained unchanged.
- All current sources remained coherent and unchanged.
- Hidden holdouts remained isolated.
- Authority exposure remained exactly the four bounded Builder tools.
- Episode freshness passed.
- Every model-visible history stayed within 16,000 characters.
- Provider identity/settings, no-retry, and no-fallback constraints held.
- Actual requests contained tool schemas and actual responses contained
  structured `tool_use` blocks.
- Assistant prose did not erase or corrupt the structured tool calls.

### Not met

- `invalid_action_envelope_count == 0` (actual: 4).
- Ambiguity did not reach native `unresolved`.
- Resolvable native World Model writes were 0 / 3 (required at least 2 / 3).
- Reverse-step did not reach the automatic verifier.
- A real next-turn `tool_result` was not observed.

No W4_FAIL condition occurred: there was no evidence mutation, hidden leakage,
authority escape, current corruption, context bypass, cross-episode leakage, or
unsupported ambiguity rewrite. The preregistered safe-but-insufficient outcome
is therefore `W4_INCONCLUSIVE`.

## Interpretation and next boundary

The W3 action friction cannot now be attributed only to plain-text JSON. The
real model used the native interface immediately and produced well-formed,
bounded reads, but its learned/provider-native affordance was to batch
independent inspections. W4's host rejected that structurally before cognition
could continue.

The next experiment should isolate exactly one cardinality mechanism before any
AST/W5 expressivity work, for example either:

```text
native response: multiple read-only calls
-> deterministic bounded batch execution
-> one paired result per provider call ID
```

or, if the provider officially supports and reliably honors it:

```text
native request configuration
-> disable parallel tool use
```

Those are alternative hypotheses and must not be changed together. The first
keeps provider behavior and changes host admission; the second keeps the host
contract and changes provider request control. Neither is implemented here.

Do not proceed to AST expressivity on the basis of W4: this campaign never
reached a source proposal. Also do not weaken fail-closed behavior, silently
execute arbitrary multi-call batches, add a generic tool registry, strengthen
the editing prompt, change budgets/tool names, or rerun W4.

## Promotion decision

```text
DO_NOT_PROMOTE_W4
```

The experiment-only native adapter remains useful evidence, but W4 does not yet
establish a reliable real provider-native Builder loop. No production file or
production provider seam was changed.
