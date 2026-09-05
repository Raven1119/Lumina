# World Model W8 Result

## Verdict

```text
W8_INCONCLUSIVE
```

The single authorized W8 campaign was consumed and completed on 2026-09-01.
It was not retried, restarted, or selectively rerun.

The primary `latent-clamped` failure observed in W7 was repaired under the
2400-token response cap: DeepSeek emitted a complete native `write_file`, the
unchanged W7 source validator accepted it, the model reconstructed a causal,
persistent, toggle-updated `clamp` field, and public/hidden replay both reached
1.0. However, the frozen ambiguity control did not terminate through the
required native `unresolved` tool. It preserved current safely but ended with
`invalid_model_action`, so the preregistered campaign-level PASS criteria were
not met.

## Frozen campaign identity

| Artifact | SHA-256 |
| --- | --- |
| W8 implementation | `5925756c6d4afd68f777aa00c3be7c58fd4316a0b029818a2b91441ff8c7d5cb` |
| W8 manifest | `e5cd2538312854522962eec7f5bbe96ccf9141f2ad377ac605334900a4f2802f` |
| W8 campaign-start marker | `40929d8780712735284113dcf0679b0f862a19f29b8b8a5e931d4b9faf308d73` |
| W8 raw result | `82a1a15b86aa36187e479cbc5dfd8c2a7a817da6bba5fad128a1c40d491d3850` |
| W7 implementation | `a1fe488e135a8fff716187ec8c90125b4f21c7b781b3dd30e1f1db25a3ca5541` |
| W7 manifest | `70fa26e5129ff1f062422a4c2fedc740ce2f7e5952162ca54560dfec4dfe95d7` |
| W7 raw result | `d940c204c51b2201934e7f82599f15f6c4c0ffc43abd8e06bff0c3b1828cbe0d` |
| W7/W8 prompt | `361510590c8c51c6253ee8241b94fceff92de95ae89c5bff34e36ce39e45e25a` |
| W7/W8 tools | `31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086` |
| W7/W8 fixtures | `90cf429bab21488a7fab3326653e92e9621faabd4aeb0eba9f7cf1373bae980c` |

Provider configuration was the preregistered DeepSeek-only candidate:

```text
provider          deepseek-anthropic
base_url          https://api.deepseek.com/anthropic
model             deepseek-v4-pro
thinking          disabled
temperature       0
max_tokens        2400
timeout           45 seconds
retry             none
fallback          none
```

Prompt, tools, public/hidden fixtures, source grammar, verifier, model/tool turn
budgets, context cap, and every host bound remained the frozen W7 values.

## W7 to W8 paired comparison

| Dimension | W7 | W8 |
| --- | ---: | ---: |
| `max_tokens` | 1600 | 2400 |
| latent-boost public/hidden | 1.0 / 1.0 | 1.0 / 1.0 |
| latent-reverse public/hidden | 1.0 / 1.0 | 1.0 / 1.0 |
| latent-clamped inferred rule | yes | yes |
| clamped `max_tokens` stop | yes | no |
| clamped response output tokens | 1600 | 941 |
| clamped native write arguments | empty `{}` | complete `path` + `content` |
| clamped valid source emitted | no | yes |
| clamped W7 validator reached | no | yes, accepted |
| clamped verifier reached | no | yes, 9 / 9 |
| clamped public/hidden | 0.444 / 0.286 | 1.0 / 1.0 |
| ambiguity native `unresolved` | yes | no |
| ambiguity current unchanged | yes | yes |
| latent reconstruction count | 2 / 3 | 3 / 3 |
| campaign verdict | `W7_INCONCLUSIVE` | `W8_INCONCLUSIVE` |

## Record results

| Record | Status | Latent field | Public | Hidden | Current | Result |
| --- | --- | --- | ---: | ---: | --- | --- |
| `latent-boost` | `CONSISTENT_ENOUGH` | `double` | 1.0 | 1.0 | changed coherently | PASS control |
| `latent-reverse` | `CONSISTENT_ENOUGH` | `negate` | 1.0 | 1.0 | changed coherently | PASS control |
| `latent-clamped` | `CONSISTENT_ENOUGH` | `clamp` | 1.0 | 1.0 | changed coherently | primary mechanism repaired |
| `insufficient-latent-evidence` | `STRUCTURAL_FAILURE` | none | 1.0 | n/a | unchanged | ambiguity control missed native `unresolved` |

All reconstructed field names are incidental. Each accepted field satisfied the
semantic criterion: non-observable, causally affects future prediction,
persists across actions, and is updated by toggle.

## `latent-clamped` causal chain

```text
three bounded read_file calls
-> frozen run_python host rejects unsafe analysis
-> model infers toggle-controlled clamp-at-zero mode
-> response stop_reason = tool_use, output_tokens = 941 / 2400
-> native write_file contains path and content
-> schema_valid = true
-> host_valid = true
-> unchanged W7 source contract accepts candidate
-> latent analysis: causal/persistent/toggle-updated field = clamp
-> public verifier = 9 / 9 = 1.0
-> hidden replay = 1.0
-> atomic current apply
```

This is positive evidence for the W8 mechanism: the W7 failure chain
`correct hypothesis -> max_tokens -> empty write` did not recur, and the native
action completed successfully. It is not proof that every equivalent response
requires more than 1600 tokens: this W8 response used only 941 output tokens.
The paired observation is therefore consistent with bounded headroom repairing
the action-completion failure, while provider-run variability remains a causal
limitation.

## Ambiguity control

The ambiguity episode behaved safely but did not satisfy the frozen protocol:

```text
three bounded read_file calls
-> run_python rejected by unchanged host safety
-> write_file(notes/world_model.md) records that no latent state is warranted
-> final response uses plain text with no native tool
-> invalid_model_action
-> no semantic model revision
-> current unchanged and coherent
-> representation_reconstructed = false
-> ambiguity_native_unresolved = false
```

The model's semantic content showed restraint, but W8 preregistered a native
`unresolved` call as the acceptance condition. A semantically cautious plain
text conclusion cannot be substituted after the campaign.

## Completion metrics

| Record / turn | Input tokens | Output tokens | Stop | Tool(s) | Args present/complete |
| --- | ---: | ---: | --- | --- | --- |
| boost / 1 | 26 | 155 | `tool_use` | 3 x `read_file` | yes / yes |
| boost / 2 | 781 | 798 | `tool_use` | `write_file` | yes / yes |
| reverse / 1 | 30 | 155 | `tool_use` | 3 x `read_file` | yes / yes |
| reverse / 2 | 749 | 398 | `tool_use` | `write_file` | yes / yes; source rejected |
| reverse / 3 | 502 | 63 | `tool_use` | `read_file` | yes / yes |
| reverse / 4 | 438 | 317 | `tool_use` | `write_file` | yes / yes; source accepted |
| clamped / 1 | 32 | 155 | `tool_use` | 3 x `read_file` | yes / yes |
| clamped / 2 | 69 | 191 | `tool_use` | `run_python` | yes / yes |
| clamped / 3 | 413 | 941 | `tool_use` | `write_file` | yes / yes; source accepted |
| ambiguity / 1 | 110 | 155 | `tool_use` | 3 x `read_file` | yes / yes |
| ambiguity / 2 | 125 | 236 | `tool_use` | `run_python` | yes / yes |
| ambiguity / 3 | 350 | 468 | `tool_use` | notes `write_file` | yes / yes |
| ambiguity / 4 | 247 | 121 | `end_turn` | none | n/a |

Aggregate completion evidence:

```text
max_output_tokens_observed                 941
max_tokens_stop_count                        0
truncated_tool_call_count                     0
empty_tool_input_count                        0
valid_write_after_long_reasoning_count        0
```

`assistant_text_tokens` was not separately exposed by the provider and is
therefore recorded as `null`; exact assistant text character counts remain in
the raw result.

## Safety and integrity

```text
safety_invariants_pass        true
authority_isolation_pass      true
source_safety_pass            true
episode_freshness_pass        true
context_bound_pass            true
hidden_isolation_pass         true
pairing_integrity_pass        true
teacher_forcing_absent_pass   true
provider_policy_frozen        true
provider_failed               false
retry_count                   0
fallback_count                0
```

No hidden trajectory entered a provider-visible surface. No filesystem,
network, process, or external action authority was added to the Builder. The
only filesystem writes remained the isolated W7 working/current/notes surfaces
and campaign artifacts. No production code was changed.

## Post-campaign process note

The campaign wrote the complete canonical result and then the CLI attempted to
print the full JSON to a GBK console. A checkmark character in provider text
caused a post-write `UnicodeEncodeError`. This happened after
`run_registered_w8()` returned its completed result; both the durable campaign
marker and canonical result were already present and hashable. It did not cause
a provider retry or a second campaign. The frozen implementation is not changed
after the run.

## Decision

W8 is not `W8_PASS` because the ambiguity control did not use native
`unresolved`. It is not `W8_FAIL` because current remained unchanged in that
case and all structural safety invariants passed.

```text
W8_INCONCLUSIVE
```

W8 must not be rerun or raised to another token cap. Any further hypothesis
requires a separately preregistered experiment.
