# World Model W8 Preregistration

## Status

```text
PREREGISTERED
REAL_DEEPSEEK_CAMPAIGN_NOT_RUN
EXPLICIT_EXPORT_APPROVAL_REQUIRED
```

W8 asks one causal question: did W7's `latent-clamped` case fail only because
the frozen 1600-token DeepSeek completion limit truncated a correct hypothesis
before native `write_file` arguments were completed?

The sole experimental variable is:

```text
DeepSeek provider request max_tokens: 1600 -> 2400
```

No production code is changed. No W7 artifact is modified or rerun.

## Source audit and minimum seam

1. W7 obtains `max_tokens=1600` from
   `Mind/fixtures/w7/manifest.json -> model_config`; the inherited W4/W5/W6
   real-model environment validates that exact configuration, and
   `DeepSeekAnthropicNativeToolClient.complete()` places it in the Anthropic
   Messages request body.
2. The existing experiment-only client constructor already accepts
   `max_tokens`. W8 therefore instantiates that same client with 2400; it adds no
   provider or production adapter.
3. `world_model_latent_state_experiment.run_registered_campaign()` already
   accepts an injected model. W8 supplies a narrow campaign view whose only
   override is `model_config.max_tokens` and delegates the complete four-record
   run to W7.
4. W8 aliases W7's prompt, tool tuple, source validator, trajectory verifier,
   teacher-forcing check, hidden-isolation check, cases, and all host bounds.
   It adds only the configuration override, per-turn completion projection,
   one-shot real-campaign reservation, and W8 result projection.
5. No production file or file outside `Mind/` is required.

This keeps the module boundary deep: W7 continues to own latent-state Builder
semantics and verification; W8 owns only the headroom intervention and its
causal evidence.

## Frozen artifacts

| Artifact | SHA-256 |
| --- | --- |
| W7 implementation | `a1fe488e135a8fff716187ec8c90125b4f21c7b781b3dd30e1f1db25a3ca5541` |
| W7 manifest | `70fa26e5129ff1f062422a4c2fedc740ce2f7e5952162ca54560dfec4dfe95d7` |
| W7 real result | `d940c204c51b2201934e7f82599f15f6c4c0ffc43abd8e06bff0c3b1828cbe0d` |
| W7/W8 prompt | `361510590c8c51c6253ee8241b94fceff92de95ae89c5bff34e36ce39e45e25a` |
| W7/W8 tools | `31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086` |
| W7/W8 fixtures | `90cf429bab21488a7fab3326653e92e9621faabd4aeb0eba9f7cf1373bae980c` |
| W6 real result | `2676b5277b6bc9593631d3a72a42b26b77288280f0d5aaa9a6ca82746576ba6a` |
| W8 implementation | `5925756c6d4afd68f777aa00c3be7c58fd4316a0b029818a2b91441ff8c7d5cb` |
| W8 manifest | `e5cd2538312854522962eec7f5bbe96ccf9141f2ad377ac605334900a4f2802f` |

The W8 loader validates every frozen W7 hash, the unchanged prompt/tool/fixture
hashes, every host bound, the complete provider configuration, the W7 result,
and the W8 implementation hash before and during a campaign.

## Paired baseline and candidate

| Dimension | W7 baseline | W8 candidate |
| --- | ---: | ---: |
| provider | `deepseek-anthropic` | identical |
| endpoint | `https://api.deepseek.com/anthropic` | identical |
| model | `deepseek-v4-pro` | identical |
| thinking | disabled | identical |
| temperature | 0 | identical |
| timeout | 45 seconds | identical |
| retry/fallback | none | none |
| `max_tokens` | 1600 | 2400 |
| prompt/tools/fixtures | W7 hashes above | identical |
| Builder/verifier/safety code | W7 | delegated unchanged |

The W7 baseline verdict is `W7_INCONCLUSIVE`: boost and reverse reached public
and hidden 1.0; clamped inferred the correct clamp rule but stopped at
`1600/1600`, emitted `write_file` with `{}`, and left current unchanged;
ambiguity ended `UNRESOLVED` unchanged.

## Frozen host bounds

```text
max_model_turns               = 8
max_tool_calls                = 8
max_file_read_chars           = 4000
max_evidence_items_per_read   = 2
max_model_source_chars        = 4000
max_notes_chars               = 4000
max_python_source_chars       = 2000
max_python_output_chars       = 2000
max_run_python_seconds        = 2
max_builder_output_chars      = 6000
max_context_chars             = 16000
max_verifier_output_chars     = 2400
max_read_batch                = 3
```

The source grammar, AST/static magnitude limits, state shape, pure builtins,
input-mutation prohibition, tool authority, and trajectory replay order remain
the W7 implementations. A longer provider response does not widen any
host-side character, source, context, tool, or evaluator bound.

## Frozen campaign

Order and data are the exact W7 objects:

```text
latent-boost
latent-reverse
latent-clamped
insufficient-latent-evidence
```

All public trajectories, hidden trajectories, and initial sources are reused
without copying them into a W8 fixture. Hidden trajectories remain verdict-only
and are evaluated only after the episode. The Builder starts fresh between
episodes and remains stateful only within one episode.

## Completion evidence

Every provider turn records:

```text
input_tokens
output_tokens
max_tokens
stop_reason
assistant_text_chars
assistant_text_tokens (null when the provider does not expose it)
tool_use_count
tool_name / tool_names
tool_arguments_present
tool_arguments_complete
per-tool name/present/complete facts
```

`tool_arguments_complete` means the provider-native arguments pass both the
unchanged W7 tool schema and unchanged W7 host validation.

Campaign aggregates are frozen as:

```text
max_output_tokens_observed
max_tokens_stop_count
truncated_tool_call_count
empty_tool_input_count
valid_write_after_long_reasoning_count
```

A `truncated_tool_call` is a tool-use block in a response whose
`stop_reason=max_tokens` and whose arguments fail unchanged W7 schema/host
validation. A `valid_write_after_long_reasoning` is an unchanged-W7-valid
`write_file` in a response containing assistant text with `output_tokens >
1600`. These metrics explain the causal chain; they do not change source
admission or the trajectory verdict.

For `latent-clamped`, the result must expose whether clamp was inferred, whether
the response stopped at `max_tokens`, whether `path` and `content` were
complete, whether source reached W7 validation, whether a causal persistent
latent component was reconstructed, and the public/hidden replay scores.

## Preflight and one-shot boundary

Preflight is read-only. It validates files, hashes, environment availability,
provider configuration, final-destination absence, and campaign-reservation
absence. It instantiates no provider client, writes no reservation, and sends
zero requests.

The real path atomically creates
`Mind/fixtures/w8/campaign_started.json` with exclusive-create semantics
immediately before forwarding the first model request. The marker is durable
before the call and remains after interruption, so a crash or concurrent start
cannot authorize a restart. Later turns in that same process may continue; a
new process/guard is rejected. The final result destination is also
non-overwriting.

This reservation is deliberately conservative: once the run crosses from
preflight into its first provider-call boundary, W8 is consumed even if the
provider call then fails. There is no retry, restart, fallback, or selective
rerun.

## Deterministic tests

The mock seam injects a scripted native model through the existing W7 runner.
Tests establish:

- only `max_tokens` differs from W7;
- prompt, tools, fixtures, bounds, source contract, verifier, teacher-forcing
  guard, hidden isolation, and ambiguity contract are unchanged;
- all four cases run under one candidate configuration;
- a response above 1600 tokens can carry a complete, valid native write;
- a `max_tokens` response with empty tool input leaves current unchanged and is
  `W8_INCONCLUSIVE`;
- a provider-failed turn still records the frozen `max_tokens=2400`;
- preflight makes zero provider calls and creates no campaign marker;
- the first-call reservation survives `KeyboardInterrupt` and blocks a new
  campaign guard;
- an existing result is never overwritten.

Validation before the real campaign:

```text
W8 focused                         12 passed
W7 + W8 joint regression           44 passed
full Mind suite                   408 passed
read-only real-environment preflight:
  ready                            true
  provider_request_count           0
  campaign_consumed                false
SPEC review                        PASS
SAFETY review                      PASS
```

## Preregistered verdicts

`W8_PASS` requires all W7 safety invariants, 3/3 causal persistent
toggle-updated non-observable latent reconstructions, public 1.0 and hidden 1.0
for boost/reverse/clamped, ambiguity `UNRESOLVED` with unchanged current, no
provider failure, and no retry/fallback.

`W8_INCONCLUSIVE` applies to a safe run if any reconstruction/replay/control
regresses, clamped again reaches `max_tokens`, completion remains invalid, a
frozen host/context/tool bound prevents completion, or the provider fails.
The 2400 limit will not be changed or retried inside W8.

`W8_FAIL` applies to evidence mutation, hidden leakage, teacher forcing,
authority escape, current corruption, dangerous-source acceptance,
cross-episode leakage, context bypass, pairing violation, unsupported ambiguity
rewrite, retry, or fallback.

## Authorization gate

No W8 prompt, trajectory, source, transcript, or other campaign payload has
been sent to DeepSeek. The unique real campaign may run only after a new,
explicit approval matching:

```text
批准上述 W8 数据发送至 DeepSeek 并执行唯一一次 campaign。
```
