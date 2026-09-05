# World Model Experiment W2 Result

## Verdict

```text
W2_INCONCLUSIVE
```

W2 established the intended bounded revision-loop mechanics and preserved every
safety boundary, but the frozen real DeepSeek campaign did not establish
evidence-driven World Model repair. The three resolvable records consumed their
eight-turn budgets by repeatedly reading the working model and the same first
evidence slice. They made zero semantic revisions, so no post-edit automatic
verifier observation was produced and no current model was replaced.

The ambiguity record ended `UNRESOLVED` with grounded competing possibilities
and no rewrite. Provider availability, source validation, transaction safety,
and epistemic restraint therefore passed; iterative semantic value did not.

## Hypothesis and single variable

W2 changed only this mechanism relative to W1:

```text
one fresh DeepSeek synthesis call
-> fresh bounded inspect/revise episode
-> automatic deterministic verification after each semantic edit
-> first-divergence feedback
-> further revision within the same episode
```

The W1 public observations and true rules, executable
`CanonicalWorldModel.predict(state, action)` representation, strict AST grammar,
isolated evaluator, explicit host activation, and exact DeepSeek provider/model
were held fixed. The W1 manifest and implementation were hash-pinned throughout
the run.

The preregistered real campaign does not support the hypothesis. It also does
not falsify the automatic verifier's ability to guide a later revision, because
the real model never performed the first semantic edit needed to expose that
feedback channel.

## Mechanism

### Implemented W2 path

```text
explicit host activation
-> fresh Builder episode with no prior transcript
-> compact current verifier state + first divergence + resource handles
-> bounded read_file / run_python / write_file / unresolved actions
-> isolated working revision
-> strict source validation
-> deterministic replay of every public observation
-> first-divergence observation after a semantic edit
-> later revision or bounded termination
-> atomic current-file replace only on exact public agreement
```

One episode terminates as exactly one of:

```text
CONSISTENT_ENOUGH
UNRESOLVED
BUDGET_EXHAUSTED
STRUCTURAL_FAILURE
```

Budget exhaustion, unresolved reasoning, invalid source, provider failure, or
transaction failure preserves the current coherent model.

### Adapted from Tycho

Source: official `NIMI-research/Tycho`, commit
`f68912a764372ead0a610db2e1c011d41ce5197e`, Apache-2.0.

- `tycho/agent/builder.py::WorldModelBuilder.build`: fresh bounded auxiliary
  cognition, separate from the acting process's history.
- `tycho/agent/builder.py::_verify_state`: a compact verifier state and first
  divergence at episode kickoff.
- `tycho/workspace/agent_tools.py::ToolExecutor._wm_feedback`: semantic model
  edits automatically invoke deterministic verification.
- `tycho/workspace/agent_tools.py::ToolExecutor._run_python`: fresh bounded
  subprocess analysis rather than persistent Python state.
- `tycho/workspace/workspace.py::_resolve`: model-visible resources are confined
  to a narrow workspace namespace.
- `tycho/prompts/builder.system.j2`: observations outrank beliefs; hidden state
  may be necessary; inspect exact evidence on demand; prefer shared mechanics;
  retain uncertainty in notes.
- `tycho/workspace/version_store.py`: atomic/content-hash principles informed
  the working/current transaction without copying the general snapshot store.

### Removed or changed for Lumina

W2 does not copy Tycho's game/level scope, grid/render ontology, terminal
`outcome()` contract, Actor report, automatic `wm_signal` activation, action
advice, search methods, broad file workspace, persistent analysis helpers, or
general version store. The Builder has no environment action, Execution, Mind
or Root transcript, browser, external shell, process-control, or recursive-agent
surface.

`run_python` is narrower than Tycho's: a fresh `python -I -S` process receives
only bounded public evidence and a statically limited analysis expression. It
cannot import, access attributes, assign state, materialize nested
comprehensions, or read/write host files. Its runtime, source, output, literals,
and iteration shapes are bounded.

## Frozen experiment and evidence

Real-model configuration:

```text
provider                 deepseek-anthropic
endpoint                 https://api.deepseek.com/anthropic
model                    deepseek-v4-pro
thinking                 disabled
temperature              0
max output tokens        1600
request timeout          45 seconds
retry                    none
fallback                 none
```

Freeze evidence:

```text
W1 manifest              fa4fef80cd0eb80b3a7b4da31b409b483e95f7c8a2640d21b6a67926b9b987a8
W1 implementation        04047f5b4b0f2281ebdf5b6f75fa65ed9baf0bfaba5be3922485f5b68889d882
W2 implementation        22c06089718bb1ef7674cd64c73f4608b6318314016cf6de52771f7413703354
W2 prompt                44e6f8bb01201d86100b9ce9622db648a1e2463ff74e46f4fe122d2dfd45c5a9
W2 manifest              e10024852e234d920c4d6a2e7979c6c9d26da5d606d92fb8bb3d7496030fea7f
```

Bounds were frozen before the real run:

| Boundary | Value |
|---|---:|
| model turns / tool calls | 8 / 8 |
| fresh Python runtime | 2 seconds |
| file read / verifier observation | 4,000 / 2,400 chars |
| model output / model source | 6,000 / 4,000 chars |
| notes / projected context | 4,000 / 16,000 chars |
| evidence items per read | 2 |
| Python source / output | 2,000 / 2,000 chars |

Real campaign result:

| Record | Calls | Tool steps | Semantic revisions | Termination | Public accuracy | Hidden accuracy | Current changed |
|---|---:|---:|---:|---|---:|---:|---|
| boost-step | 8 | 8 | 0 | `BUDGET_EXHAUSTED` | 0.50 | 0.00 | no |
| reverse-step | 8 | 8 | 0 | `BUDGET_EXHAUSTED` | 0.50 | 0.00 | no |
| clamped-step | 8 | 8 | 0 | `BUDGET_EXHAUSTED` | 0.60 | 0.00 | no |
| insufficient-evidence | 8 | 7 | 0 | `UNRESOLVED` | 1.00 | n/a | no |

Aggregate:

```text
provider failures                  0
structural invariants              PASS
resolvable public exact            0 / 3
resolvable hidden exact            0 / 3
reverse recovered                  no
reverse feedback-driven repair     no
uncertainty preserved              yes
```

The full event sequences, model/provider identity, calls, tool steps, source,
notes, scores, bounds, and hashes are preserved in
`Mind/fixtures/w2/real_campaign_result.json`.

## Causality

### Why W1 reverse-step failed

W1's one-call DeepSeek output kept the original `+ delta` rule in reverse mode
and added an irrelevant negative-boundary branch. Its public accuracy therefore
remained `0.50`. This was semantic synthesis failure, not provider, syntax,
sandbox, or verifier failure.

### What W2 exposed

The deterministic kickoff state exposed the correct first mismatch in every
resolvable record:

- boost: predicted delta `+2`, observed delta `+4`;
- reverse: predicted delta `+2`, observed delta `-2`;
- clamped: predicted delta `-3`, observed delta `-1` at the zero floor.

DeepSeek then repeatedly selected bounded reads. Boost alternated among the
working model and evidence rows `0..1`. Reverse and clamped also attempted wider
evidence reads, received `bounded_range_required`, and returned to the same
model/first evidence slice. No record emitted `write_file(world_model.py)`.

Consequently there was no semantic save, no post-edit verifier result, and no
later revision whose change could be causally attributed to verifier feedback.
The observed failure is a loop-progress/action-protocol failure before the
mechanism's central feedback step, not evidence that the deterministic feedback
itself worsened a model.

The scripted tests do prove the code path mechanically: a wrong safe working
revision produces the exact first divergence, a later general rule reaches exact
agreement, and only that later revision is atomically applied. The preregistered
real-model criterion intentionally requires the same causal pattern on
reverse-step; it was not met.

## Generalization

The hidden observations were never present in kickoff context, resource reads,
runtime verification, notes, or the atomic-apply decision. They were evaluated
only after each episode terminated.

All three hidden scores were `0.00` because the corresponding current models
remained unchanged. This is not evidence of row-specific memorization—the model
never wrote a rule at all—but it fails the preregistered generalization
criterion. No hidden result was used to refine or replace a model.

## Epistemics

The ambiguity activation reason was deliberately neutral; it did not say that
the evidence was insufficient or disclose the required verdict. Its two public
positive-delta observations fit the current rule exactly and cannot distinguish
several possible conditional dynamics.

DeepSeek ended `UNRESOLVED` and recorded that the current evidence does not show
whether mode affects step dynamics, whether toggle changes value, or whether a
latent condition exists. It requested a toggle-followed-by-step or another
mode-conditioned observation. It did not rewrite the executable model.

Thus the real campaign showed epistemic restraint under the tested ambiguity.
It does not establish a general confidence or uncertainty system.

## Safety and authority

All four records preserved these invariants:

- Reality Evidence remained byte/logically unchanged;
- current model files remained unchanged and structurally coherent;
- no broken working text reached current;
- model-visible paths were only `world_model.py`, `notes/world_model.md`, and
  logical `evidence.json`;
- no environment action, Execution, Mind/Root transcript, external filesystem,
  browser, shell, or recursive-agent capability was exposed;
- no provider fallback or retry occurred.

The current model remaining unchanged in the three exhausted episodes is the
required conservative behavior, not a successful semantic result.

## Context behavior

Kickoff context contained only:

```text
task and neutral activation reason
remaining model/tool budgets
current public verifier metric
first divergence with state/action/predicted/actual/deltas
bounded logical resource handles
```

The complete model source, notes, and evidence were accessible only through
explicit reads. Evidence reads returned at most two public observations. Hidden
observations, full histories, W1 outputs, fixture identity, and Mind/Root
transcripts were absent. Between turns, at most two bounded tool observations
were projected, so context size did not grow linearly with evidence or turn
count.

This bounded projection was safe but not sufficient for loop progress in the
real run: the stateless deterministic JSON-action interaction repeatedly chose
the same reads instead of committing a working change.

## Development evidence

The test-first sequence began with import failure because the W2 module did not
exist, then covered:

- authority and workspace boundaries;
- bounded initial attention and exact on-demand reads;
- automatic verifier feedback and exact first divergence;
- multi-revision atomic apply;
- broken working-source isolation;
- fresh restricted Python analysis;
- model/tool/context bounds and conservative termination;
- nested-output and invalid-UTF-8 failures;
- W1 public-evidence identity and source freeze;
- hidden-observation isolation;
- real W2 verdict dimensions and ambiguity restraint.

The required dual-axis review initially found experimental-validity and safety
gaps. Fixes added the causal reverse-step criterion, neutral ambiguity reason,
W1 implementation pin, stricter analysis resource grammar, complete evidence
validation, bounded-cap enforcement, fail-closed decoding, atomic-write cleanup,
and freeze-before-result persistence. Spec and Standards re-review both returned
PASS.

## North Star conclusion

W2 does **not** establish the full relation:

```text
Reality falsifies understanding
-> bounded cognition revises understanding
```

It establishes the safe deterministic substrate for that relation and shows
that ambiguity can remain unresolved. In the real campaign, Reality did expose
the correct mismatches, but bounded cognition did not proceed from inspection to
a working revision. No claim is made for autonomous curiosity, continuous world
understanding, Mind supervision, Nervous, Evolution, RSI, or digital life.

A future experiment, if separately authorized, should isolate loop-progress
continuity/action protocol as its single variable. W2 itself must not be rerun,
retuned, or expanded after this frozen outcome.

```text
W2_INCONCLUSIVE
```
