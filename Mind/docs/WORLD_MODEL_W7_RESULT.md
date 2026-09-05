# World Model W7 Result

## Verdict

```text
W7_INCONCLUSIVE
```

The single preregistered DeepSeek campaign completed without retry or fallback.
Safety, source authority, state threading, anti-teacher-forcing, hidden
isolation, episode freshness, context bounds, tool pairing, and ambiguity
restraint all passed.

Two of the three resolvable records produced an applied causal, persistent
model-owned state and reached exact public and hidden replay. The third record,
`latent-clamped`, correctly induced the intended latent mechanic in model text
but exhausted the frozen 1600-token output budget before emitting a valid
`write_file` input. It therefore made no semantic revision and left current
unchanged. The preregistered 3/3 threshold was not met.

W7 changed only:

```text
environment-provided complete state
→ model-owned state threaded across an observation/action trajectory
```

The W5/W6 DeepSeek provider, model, native tool protocol, budgets, fresh-episode
semantics, verifier acceptance philosophy, and lack of retry/fallback remained
frozen.

## Baseline versus candidate

The observable-only seed was evaluated by the same trajectory-threaded
verifier before Builder interaction.

| Record | Observable-only baseline | Final status | Final public | Final hidden | Latent reconstruction |
|---|---:|---|---:|---:|---|
| `latent-boost` | `3/8 = 0.375` | `CONSISTENT_ENOUGH` | `1.0` | `1.0` | `double` |
| `latent-reverse` | `3/7 ≈ 0.4286` | `CONSISTENT_ENOUGH` | `1.0` | `1.0` | `forward` |
| `latent-clamped` | `4/9 ≈ 0.4444` | `STRUCTURAL_FAILURE` | `0.4444` | `2/7 ≈ 0.2857` | none applied |
| `insufficient-latent-evidence` | `3/3 = 1.0` | `UNRESOLVED` | `1.0` | n/a | none |

This establishes the preregistered baseline failure for all three resolvable
trajectories. It does not establish 3/3 candidate success.

## A. Did observable-only representation fail as preregistered?

Yes. The seed carried only `value`; it could not preserve the invisible effect
of `toggle`. Its initial accuracies were `0.375`, `0.4286`, and `0.4444` for
boost, reverse, and clamp. First divergences occurred after an invisible toggle
while model state was still only `{"value": ...}`.

The ambiguity control remained exact at `1.0`, confirming that the harness did
not require latent state when observable-only dynamics were sufficient.

## B. Did Builder change representation, not merely arithmetic?

Yes for 2/3 resolvable records.

`latent-boost` changed state from:

```python
{"value": observation["value"]}
```

to:

```python
{"value": observation["value"], "double": False}
```

`latent-reverse` changed it to:

```python
{"value": observation["value"], "forward": True}
```

The deterministic field-name-independent analysis found both extra fields to
be causal, persistent across multiple actions, and updated by invisible
toggles. They were not present in Environment Observation.

`latent-clamped` produced no valid source proposal, so it cannot count as a
representation revision even though the model's natural-language reasoning
identified the right mechanism.

## C. What latent fields did it invent?

| Record | Field | Initial value | Predictive meaning inferred from source |
|---|---|---|---|
| `latent-boost` | `double` | `False` | whether step deltas use multiplier 2 |
| `latent-reverse` | `forward` | `True` | whether step deltas are added or subtracted |
| `latent-clamped` | none applied | n/a | model reasoned about a clamp flag but emitted no valid source |

Neither successful field was named `mode`. PASS logic did not depend on field
names or fixture ontology.

## D. Did those fields persist across multiple actions?

Yes for both applied models. `double` remained set across the boost public and
hidden multi-step sequences until the second toggle. `forward` likewise
remained changed across consecutive reverse-mode steps and was restored by the
next toggle.

The deterministic analysis reported:

```text
latent-boost   causal=double  persistent=double  toggle-updated=double
latent-reverse causal=forward persistent=forward toggle-updated=forward
```

The clamp record supplied no applied field to test.

## E. Did toggle/action update them correctly?

Yes for the two applied sources:

- boost: `toggle` returns a fresh state with `double = not double`; `step`
  applies multiplier 2 only while the flag is true;
- reverse: `toggle` returns a fresh state with `forward = not forward`; `step`
  adds while true and subtracts while false.

Both repeated-toggle public trajectories and changed hidden action sequences
replayed exactly.

For clamp, DeepSeek explicitly derived the correct candidate mechanic:

```text
initial unclamped
toggle → clamped
clamped step → max(0, value + delta)
second toggle → negatives allowed again
```

However, reasoning is not an executable revision and did not satisfy the
acceptance criterion.

## F. Did `observe(model_state)` predict only external fields?

Yes. Both applied sources implement:

```python
def observe(self, state):
    return {"value": state["value"]}
```

Latent `double` and `forward` never entered predicted Environment Observation.
The source validator and isolated evaluator also enforce exact
`{"value": int}` projection.

## G. Did public trajectory replay reach exact agreement?

Two of three resolvable records did:

```text
latent-boost   8/8 = 1.0
latent-reverse 7/7 = 1.0
latent-clamped 4/9 ≈ 0.4444
```

The two successful candidates each entered the deterministic verifier and were
atomically applied only after exact agreement. Clamp had zero semantic
revisions and current remained the observable-only seed.

## H. Did unseen trajectory replay generalize?

Two of three did:

```text
latent-boost   hidden 7/7 = 1.0
latent-reverse hidden 7/7 = 1.0
latent-clamped hidden 2/7 ≈ 0.2857
```

The successful hidden trajectories changed initial values, deltas, sequence,
toggle positions, and the number of consecutive latent-mode steps. They were
scored only after each provider episode ended.

The 2/3 result misses the preregistered 3/3 requirement and therefore cannot be
promoted to W7_PASS.

## I. Did ambiguity avoid unnecessary latent ontology?

Yes. `insufficient-latent-evidence` remained exact under the simple seed,
emitted provider-native `unresolved`, made no semantic revision, and left
current unchanged. Its notes explicitly stated that `step(+1)` and `step(+2)`
were fully explained without unobserved state.

## J. Was any hidden truth leaked?

No detected leak.

- all four records report `hidden_isolated=True`;
- hidden truth and hidden trajectories were absent from Builder context,
  verifier feedback, tool results, and atomic apply;
- record refs and generator mechanisms were not model-visible;
- the successful model fields (`double`, `forward`) were invented by the model
  and differ from fixture labels;
- `teacher_forcing_absent_pass=True`: changing later actual observations does
  not change the isolated model replay;
- Reality Observation was supplied to model state only once at trajectory
  initialization.

## `latent-clamped` termination analysis

This record followed the frozen interaction normally through two turns:

1. one legal three-read envelope inspected model, notes, and public evidence;
2. one legal `run_python` request was structurally rejected as
   `unsafe_analysis` because it used a forbidden loop;
3. DeepSeek then manually derived the correct persistent clamp-mode rule and
   requested `write_file`.

The third response ended with:

```text
stop_reason = max_tokens
output_tokens = 1600
tool = write_file
tool input = {}
```

The frozen tool host correctly marked both schema and host validation false and
terminated `invalid_model_action`. No candidate source reached the validator,
working file, or current. This is a bounded interaction/protocol completion
failure, not evidence that the inferred clamp representation was false.

Per preregistration, the campaign was not retried and the token budget, prompt,
tools, grammar, and fixture were not changed.

## Campaign safety and interaction

```text
safety invariants        PASS
source safety            PASS
authority isolation      PASS
hidden isolation         PASS
teacher forcing absent   PASS
episode freshness        PASS
context bounds           PASS
tool pairing             PASS
provider policy frozen   PASS
provider failure         false
retry / fallback         none / none
```

Interaction totals:

```text
provider-native tool uses       23
tool results                    19
multi-read envelopes             4
multi-read calls executed       12
maximum calls in one response    3
mixed/mutating batch rejections  0
pairing violations               0
accepted semantic revisions      2
```

All final current sources remained coherent. Reality/public evidence remained
unchanged.

## Frozen evidence

```text
W7 implementation  a1fe488e135a8fff716187ec8c90125b4f21c7b781b3dd30e1f1db25a3ca5541
W7 manifest        70fa26e5129ff1f062422a4c2fedc740ce2f7e5952162ca54560dfec4dfe95d7
W7 result          d940c204c51b2201934e7f82599f15f6c4c0ffc43abd8e06bff0c3b1828cbe0d
W7 prompt          361510590c8c51c6253ee8241b94fceff92de95ae89c5bff34e36ce39e45e25a
W7 tools           31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086
W7 fixtures        90cf429bab21488a7fab3326653e92e9621faabd4aeb0eba9f7cf1373bae980c
W6 result          2676b5277b6bc9593631d3a72a42b26b77288280f0d5aaa9a6ca82746576ba6a
```

Provider configuration:

```text
provider     deepseek-anthropic
model        deepseek-v4-pro
thinking     disabled
temperature  0.0
max tokens   1600
timeout      45 seconds
```

## Decision

W7 supplies positive evidence that model-owned latent-state reconstruction is
possible in this bounded deterministic trajectory domain: DeepSeek independently
invented two differently named causal state components, preserved them across
actions, updated them on invisible toggles, and generalized exactly to unseen
trajectories without hidden leakage or new runtime authority.

It does not satisfy the preregistered experiment claim because the required
third executable representation was never emitted or verified. The correct
status remains:

```text
W7_INCONCLUSIVE
```

No production integration, automatic trigger, Execution wiring, state registry,
belief model, Evolution, or retry is authorized by this result. Any follow-up
must be a separately preregistered experiment with one explicitly chosen
variable; this campaign must not be rerun.
