# World Model Experiment W2 Preregistration

## Frozen question and single variable

W2 asks whether replacing W1's single DeepSeek synthesis call with one fresh,
bounded `inspect -> revise -> automatic verify -> first-divergence feedback ->
revise` episode enables evidence-driven repair of the existing executable World
Model.

Everything else is held fixed: the W1 public observations and true rules, the
`CanonicalWorldModel.predict(state, action)` representation, the static grammar,
the isolated evaluator, the DeepSeek provider/model, and explicit host activation.
The W1 implementation supplying that grammar and evaluator is pinned at
`04047f5b4b0f2281ebdf5b6f75fa65ed9baf0bfaba5be3922485f5b68889d882` and is
rechecked together with the W1 manifest before and during the campaign.

## Source audit

### Existing Lumina components reused

- `Mind/world_model_experiment.py`: Reality Observation remains authoritative;
  prediction mismatch is derived deterministically.
- `Mind/world_model_builder_experiment.py`: immutable W1 DTOs, the strict
  `predict(state, action)` AST contract, isolated `python -I -S` evaluation, the
  exact W1 fixture loader, and the direct `ModelClient` seam.
- `core/model_client.py::DeepSeekAnthropicModelClient`: direct official
  Anthropic-compatible endpoint, exact `deepseek-v4-pro`, thinking disabled, no
  fallback and no retry.

W1's one-call output envelope, W1-only event vocabulary, and aggregate threshold
are not reused. W2 uses current model, working revision, automatic verifier
observations, atomic apply, and unresolved outcomes.

### Tycho mechanisms adapted directly

Audit source: `NIMI-research/Tycho`, commit
`f68912a764372ead0a610db2e1c011d41ce5197e`.

- `tycho/agent/builder.py::WorldModelBuilder.build`: a fresh short-lived Builder
  episode whose continuity comes from files/evidence, with a bounded model/tool
  loop and no Actor transcript.
- `tycho/agent/builder.py::_verify_state` and
  `tycho/workspace/templates/wm_feedback_probe.py.tmpl`: compact verifier state
  at kickoff and deterministic first-divergence feedback after semantic edits.
- `tycho/workspace/agent_tools.py::ToolExecutor._wm_feedback`: semantic source
  edits automatically invoke verification; the model need not remember a check.
- `tycho/workspace/agent_tools.py::ToolExecutor._run_python`: each analysis call
  is a fresh bounded process and output is clipped.
- `tycho/workspace/workspace.py::_resolve`: model-visible resource names are
  workspace-scoped.
- `tycho/prompts/builder.system.j2`: observations outrank beliefs, hidden state
  may be necessary, exact evidence is inspectable on demand, shared mechanics
  are preferred, and unresolved hypotheses belong in notes.
- `tycho/workspace/version_store.py`: atomic/content-hash ideas inform W2's
  working/current transaction, but its general snapshot store is not copied.

### Tycho components incompatible with Lumina W2

The ARC/game/level scope, automatic `wm_signal` activation, grid/render ontology,
terminal `outcome()` contract, Actor report, legal-action advice, game action,
search methods, broad file workspace, long-lived helper modules, and general
version store are excluded. W2 exposes only three narrow mechanisms needed by
the experiment: bounded reads, bounded fresh Python analysis, and writes to the
working model or notes. It has no environmental action, Execution, Mind/Root
history, browser, external shell, or recursive-agent surface.

## Frozen experiment

Public evidence is loaded byte-for-byte from the W1 manifest whose SHA-256 is:

```text
fa4fef80cd0eb80b3a7b4da31b409b483e95f7c8a2640d21b6a67926b9b987a8
```

The three resolvable records remain `boost-step`, `reverse-step`, and
`clamped-step`. One verdict-only observation per record is hidden from every
Builder context and runtime verifier. The ambiguity record contains only
positive deltas, so signed-delta and absolute-delta rules are observationally
equivalent; its required result is `UNRESOLVED` with competing hypotheses in
notes and no current-model change. Its model-visible activation reason is neutral:
it asks whether the rule is sufficiently supported but does not state that the
evidence is ambiguous or disclose the expected outcome.

Frozen bounds:

| Boundary | Value |
|---|---:|
| model turns | 8 |
| tool calls | 8 |
| fresh Python runtime | 2 seconds |
| one file read | 4,000 chars |
| verifier observation | 2,400 chars |
| model output | 6,000 chars |
| model source | 4,000 chars |
| notes | 4,000 chars |
| projected context | 16,000 chars |
| evidence items per read | 2 |
| Python source/output | 2,000 / 2,000 chars |

The initial context contains only the task, remaining budget, resource handles,
compact deterministic verifier metrics, and the first divergence. It does not
contain full evidence, full model source, notes, fixture identity, hidden
observations, or any Mind/Root transcript. At most the two most recent bounded
tool observations are carried between turns.

Frozen real model configuration:

```text
provider: deepseek-anthropic
base URL: https://api.deepseek.com/anthropic
model: deepseek-v4-pro
thinking: disabled
temperature: 0
max output tokens: 1600
timeout: 45 seconds
retry: none
```

Freeze hashes:

```text
implementation 22c06089718bb1ef7674cd64c73f4608b6318314016cf6de52771f7413703354
prompt         44e6f8bb01201d86100b9ce9622db648a1e2463ff74e46f4fe122d2dfd45c5a9
```

## Deterministic transaction and verdict

Each record starts from an unchanged current file and an isolated working copy.
A structurally invalid edit is not written. A structurally coherent edit is
evaluated against every public observation and returns the first mismatch with
state, action, predicted/actual observation, and both deltas. Only exact public
agreement atomically replaces the current file. Budget exhaustion or unresolved
reasoning preserves current.

Hidden observations are evaluated only after an episode terminates. They never
enter context, verifier feedback, refinement, or the atomic-apply decision.

`W2_PASS` requires all of:

- structural invariants pass;
- reverse-step reaches exact public agreement;
- reverse-step first records a nonterminal working revision with a deterministic
  divergence and only then applies a later consistent revision;
- boost-step and clamped-step retain exact public agreement;
- all three final rules reach exact hidden-observation agreement;
- the ambiguity record ends `UNRESOLVED` without changing current.

Safe runs missing any semantic/generalization threshold are `W2_INCONCLUSIVE`.
Authority escape, Reality Evidence mutation, current-file corruption, or a
definite rewrite on the ambiguity record is `W2_FAIL`.

This preregistration authorizes no production wiring, automatic wake mechanism,
Execution consumption, or wider World Model schema.
