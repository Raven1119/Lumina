# Mind Experiment E1 — Paired Behavioral Value Test Result

Date: 2026-08-30

Verdict:

```text
EXPERIMENT_E1_INCONCLUSIVE
```

The candidate completed 2/6 tasks versus the baseline's 1/6, producing two
recorded `RESCUE` outcomes and one `REGRESSION`. Those counts satisfy E1's
numeric threshold for further causal review, but they do not satisfy the
required causal condition for `SUPPORTED`: neither rescue contained a
Directive. One rescue followed `NoChange`; the other followed a fail-soft
`ActivationFailure`. The two delivered Directives produced one regression and
one both-fail outcome.

The experiment therefore does not establish behavioral value from current
Mind supervision. It also does not meet the negative criterion because the
candidate's verified-completion count was not lower than the baseline's and
there were not two regressions with zero rescues.

## 1. Experimental claim and single variable

E1 tested one variable:

```text
whether one real bounded cognitive Mind activation
may produce and deliver one one-shot Directive
```

Baseline:

```text
frozen durable Execution prefix
→ no Mind activation
→ Execution continuation
```

Candidate:

```text
the same frozen durable Execution prefix
→ real run_activation(...)
→ optional read-only cognitive capability
→ NoChange / DecisionIntent / Directive / conservative failure
→ D lifecycle when Directive
→ E0 one-shot decision advisory
→ Execution continuation
```

No production routing, Intention mutation, DecisionIntent application,
Nervous, Focus, Memory quality change, provider addition, or equal-compute
baseline was introduced.

## 2. Pre-registration and freeze

Maintained evidence:

- [frozen manifest](../fixtures/e1/manifest.json)
- [blinded-first review](../fixtures/e1/BLINDED_PRIMARY_REVIEW.md)
- [safe campaign artifact](../fixtures/e1/EXPERIMENT_E1_ARTIFACT.json)
- [experiment harness](../behavioral_experiment.py)
- [H1–H6 tests](../test_behavioral_experiment.py)

Hashes:

```text
manifest SHA-256:
6a9c915a52c31d209a9ba51e1f14be8497ac12e5fd82ee88a73e8ef7be90fb4f

preregistration SHA-256:
b115ff25aabc79b00247fa88b574f1a73c0841ef40e75044e1d2c0f4d259edad

blinded-primary SHA-256:
2d1d6584bcfaf9b66a97d2d72b425044688f773c58059d5673c071687bcd75f3

safe campaign artifact SHA-256:
27e79975bc15051ccdc6258ec77ff92fb7c11cdd9ce8e4cdfb870cc112752ff0
```

The preregistration froze the manifest, all six task hashes, arm order,
budgets, trigger, model configurations, current A–E0 implementation, relevant
Execution source, Memory DTO, prompt/projector versions, contamination policy,
and review policy. All six real prefixes and both copies of each prefix were
created before the first candidate Mind activation.

The first attempted external launch was rejected before process creation. No
run directory or provider data resulted. After explicit user authorization,
the complete campaign was run once from the beginning. No task was replaced,
removed, or rerun.

### Trigger decision

The uniform trigger was:

```text
after Root decision N, with N = 1
```

`N=1` was selected before any candidate run. The supported Execution runtime
can wait, complete, or spawn before decision 3, so `N=3` was not a uniformly
stable branch boundary. Decision 1 is the earliest durable restart boundary.
The preregistered rule was to block the whole campaign if that boundary was
unavailable, not select a task-specific trigger.

In the actual six prefixes, Root decision 1 only read `mission.txt`. No prefix
created a Child or relied on an arbitrary live IPython variable.

### Frozen holdout tasks

All tasks were new synthetic fixtures and were frozen before real execution.

| Task | Description | Task SHA-256 |
| --- | --- | --- |
| e1-01-ledger-replay | Replay a ledger with duplicate-event suppression | `4beca1e26ed7f842d216cb7a48b44acbf8dc2be1350694422a07c3563497c987` |
| e1-02-utc-window | Find the earliest common UTC interval | `3841c1bbd1ef7f770ad90ce20bc0dde4eab85a47d3fefefa4e325ce353b18868` |
| e1-03-dependency-lock | Select the highest compatible non-revoked tuple | `c8b61ef108b41e9922c1f21af37ff0746ff82381ed1a210c8b9057e8e6802469` |
| e1-04-event-projection | Project state with duplicate suppression and rollback | `ecc954b7215b5c6c03fd4c90c4f256ed9cdc55f171fdb0e1678cea75c944f2f9` |
| e1-05-constrained-route | Find a lowest-cost route through a waypoint | `0150ae44579d42a6fb0ec4aaac31667dfab600e8acf6dac1b18af96b1f0d0d7a` |
| e1-06-grid-transform | Transform a grid and extract indexed cells | `de6006d4911030b53de96c424cd67210c3f33cdb486c5fcfb26364c82a9457fe` |

Each task used the existing `FileContentEquals` verifier. Expected content was
1100 tildes, a newline, the derived payload, and a final newline. The existing
Execution projection exposes only the first 1024 expected characters, so the
payload itself was not present in the model-visible completion projection.

## 3. Frozen configurations

### Execution

```text
provider: DeepSeek
model: deepseek-v4-pro
thinking: disabled
stream: false
temperature: provider default
max tokens: provider default
request timeout: 60 seconds
```

### Mind

```text
provider: minimax-anthropic
base URL: https://api.minimaxi.com/anthropic
model: MiniMax-M2.7
temperature: provider default
max tokens: 1000
request timeout: 30 seconds
max model calls per activation: 2
max cognitive capability calls per activation: 1
PROMPT_VERSION: mind-prompt-v1
PROJECTOR_VERSION: mind-projector-v1
```

The exact credential-free Mind base URL was frozen and checked at runtime, not
only its hostname. Model credentials were not persisted into experiment
artifacts. `ModelClient` does not expose MiniMax token usage, so Mind cost is
reported by calls and output characters rather than invented token counts.

### Shared continuation budget

```text
additional Root decisions: 6
Child decisions, if a Child existed: 4
model-visible context: 5000 characters
wall validity ceiling per continuation: 420 seconds
completion verifier: identical FileContentEquals object
Memory: fixed empty, read-only facade
```

The 5000-character context limit was frozen for both arms. The existing
completion projection and E0 advisory fit within this shared limit; no task or
arm received a different cap.

## 4. Frozen-prefix integrity

For every task, the frozen prefix had:

```text
status: suspended
Root decision_count: 1
next decision_id: decision-000002
pending children: none
```

Before intervention, the two copies were checked for exact equality of:

- `ExecutionState`;
- task and workspace content hashes;
- EventLog prefix bytes and hash;
- checkpoint bytes and hash;
- Root actor/execution identity;
- actual `ModelRequest` history;
- next decision identity.

The harness also restored and rehashed each branch immediately before its arm
ran. A targeted test mutates the second arm from the first arm and proves the
second arm is rejected before its model call.

The fork is an exact copy of the supported durable restart projection, not a
copy of an arbitrary live Python kernel. This distinction did not lose a
task-relevant prefix namespace in this campaign: each prefix action only read
and printed `mission.txt`.

## 5. TDD evidence: E1-H1 through E1-H6

The final targeted suite reported:

```text
9 passed
```

| Requirement | Evidence |
| --- | --- |
| H1 | State, workspace, EventLog, checkpoint, next ID, and physical copies are equal at the fork |
| H2 | Prefix `ModelRequest` history is exact-equal |
| H3 | Real `run_activation` returning `NoChange` leaves the next Execution request and resulting log equal in the deterministic test |
| H4 | A Directive changes exactly the advisory-bearing context field and follows D ISSUE/APPLIED events |
| H5 | The following distinct decision and native provider continuation contain no Directive; requests reconverge |
| H6 | Verifier, budgets, tools, and environment are identical |
| contamination regression | A first-arm mutation of the second workspace is detected before second-arm resume |

The tests use scripted seams only to prove harness invariants. The behavioral
campaign itself used real DeepSeek and MiniMax calls.

## 6. Blinded-first primary result

The CLI printed only the blinded artifact path and hash. Before opening the
arm mapping, Mind outputs, or full artifact, the following table was persisted:

| Task | arm-A | arm-B |
| --- | ---: | ---: |
| e1-01-ledger-replay | FAIL | FAIL |
| e1-02-utc-window | FAIL | PASS |
| e1-03-dependency-lock | FAIL | PASS |
| e1-04-event-projection | FAIL | FAIL |
| e1-05-constrained-route | FAIL | PASS |
| e1-06-grid-transform | FAIL | FAIL |

Blinded totals were arm-A 0/6 and arm-B 3/6. Only then was the mapping opened.

## 7. Twelve continuation runs

`B` is baseline and `C` is candidate. Decision counts exclude the shared
one-decision prefix.

| Task | Arm order | B result / decisions | C result / decisions | Class | Mind result / calls | Directive |
| --- | --- | --- | --- | --- | --- | --- |
| e1-01-ledger-replay | baseline first | FAIL / 6 | FAIL / 6 | BOTH_FAIL | NoChange / 2 | none |
| e1-02-utc-window | candidate first | PASS / 3 | FAIL / 6 | REGRESSION | Directive / 1 | applied to decision-000002 |
| e1-03-dependency-lock | baseline first | FAIL / 6 | PASS / 3 | RESCUE | NoChange / 2 | none |
| e1-04-event-projection | candidate first | FAIL / 6 | FAIL / 6 | BOTH_FAIL | Directive / 2 | applied to decision-000002 |
| e1-05-constrained-route | baseline first | FAIL / 6 | PASS / 6 | RESCUE | ActivationFailure / 1 | none |
| e1-06-grid-transform | candidate first | FAIL / 6 | FAIL / 6 | BOTH_FAIL | NoChange / 2 | none |

Aggregate primary result:

```text
baseline verified completions:  1 / 6
candidate verified completions: 2 / 6

RESCUE:      2
REGRESSION:  1
BOTH_PASS:   0
BOTH_FAIL:   3
```

Every failed arm ended with `decision_limit_reached`. No provider failure or
wall-ceiling invalidation occurred. The slowest continuation was 26.187
seconds, well below 420 seconds.

## 8. Mind results and Directive delivery

| Task | Cognitive episode |
| --- | --- |
| e1-01 | `inspect_execution` observed the mission text already read by Root; Mind returned `NoChange` |
| e1-02 | Mind used no capability and issued a Directive claiming `mission.txt` content/location was missing |
| e1-03 | `recall_memory("mission.txt")` returned fixed empty evidence; Mind returned `NoChange` |
| e1-04 | Empty Recall led to a Directive claiming the mission was unavailable and should be investigated |
| e1-05 | Mind requested `inspect_execution` inside a Markdown JSON fence; strict parsing produced fail-soft `invalid_model_output` |
| e1-06 | Empty Recall was observed; Mind returned `NoChange` |

Result distribution:

```text
NoChange:          3
Directive:         2
DecisionIntent:    0
ActivationFailure: 1
```

For both Directives:

- `MIND_DIRECTIVE_ISSUED` was durably recorded;
- `MIND_DIRECTIVE_APPLIED` referenced `decision-000002`;
- the candidate's next request differed by exactly the one advisory field;
- the next distinct decision did not contain the Directive;
- no Intention or goal mutation occurred.

The delivery mechanism therefore worked. The semantic guidance did not show
positive behavioral value.

## 9. Trajectory review and causal interpretation

### Apparent rescue: e1-03-dependency-lock

Both arms wrote the exact correct answer. The baseline repeatedly inspected it
and exhausted its budget without `ClaimComplete`; the candidate claimed it at
its next decision and verified successfully.

Mind had returned `NoChange`. Baseline and candidate decision-2 inputs were
identical. This is a real paired outcome class of `RESCUE`, but it is not a
Mind-Directive rescue and is best explained as post-branch model variation.

### Second apparent rescue: e1-05-constrained-route

Mind failed conservatively because the real model wrapped an otherwise valid
capability request in a Markdown fence. No capability ran and no Directive was
delivered. The candidate later wrote and verified the correct route; the
baseline computed it but kept exploring until its budget expired.

This rescue also cannot be attributed to cognitive supervision.

### Regression: e1-02-utc-window

The Directive said the mission content/location was missing even though Root
had already read it in the frozen prefix. It was applied only to decision 2.
Both arms then read the same local inputs and wrote the same correct answer.
The baseline claimed completion. After the advisory had disappeared, the
candidate misread the projected expected length, performed redundant checks,
and never claimed completion.

The outcome is correctly classified as `REGRESSION`, but the raw trace does not
prove the later failure was caused solely by the one-shot Directive: decision
2 was semantically the same in both arms, and divergence occurred later under
stochastic model sampling.

### e1-04-event-projection

Mind queried empty long-term Memory instead of inspecting the already-available
Execution observation, then issued a stale Directive saying the mission was
unavailable. Candidate decision 2 inspected local files, correctly projected
state by decision 3, wrote the correct answer only at decision 6, and never
claimed completion. The baseline also wrote the correct answer and never
claimed. This was `BOTH_FAIL`, with no positive Directive evidence.

### Remaining both-fail tasks

- e1-01: candidate wrote the correct answer but spent its final decision
  rereading it rather than claiming; baseline never wrote it. Mind was
  `NoChange`.
- e1-06: both arms repeatedly recomputed or inspected the transform and never
  completed. Mind was `NoChange`.

## 10. Contamination and authority audit

Every Root `DecisionFrame.resulting_action`, IPython action, and relevant result
was reviewed after the blinded table was fixed. No Child logs existed.

Observed:

- all file reads/writes used cwd-relative task paths;
- several `os.listdir('.')` calls listed only the arm's own task files;
- no parent or absolute traversal;
- no repository/global-temp enumeration;
- no manifest, EventLog, checkpoint, Mind trace, result artifact, or other-arm
  access;
- no subprocess, shell escape, network call, background work, thread, or Child;
- no direct or indirect Intention/goal mutation.

Therefore the preregistered contamination invalidation rule was not triggered.

The harness placed actor workspaces and control artifacts under unrelated
high-entropy temporary roots and rechecked each branch before resume. This is
randomized separation plus complete non-adversarial trace review, not an OS
sandbox. Supported Execution IPython still has trusted worker filesystem
authority. `FileContentEquals.expected_content` also remains plaintext in the
host-owned EventLog/checkpoint, although no actor accessed those controls.

The existing local IPython kernel emitted its warning that TCP kernel traffic
was not encrypted. That behavior was shared by both arms and is not an E1
variable, but it remains a host-level limitation.

Runtime Mind itself received no shell, filesystem, IPython, process, browser,
Execution tool, or mutation capability. Its only possible cognitive
capabilities remained read-only Memory Recall and Execution Inspect.

## 11. Descriptive cost

Shared prefix cost:

```text
DeepSeek calls:              6
prompt tokens:            3944
completion tokens:         338
total tokens:             4282
```

Continuation cost:

| Arm | DeepSeek calls | Prompt tokens | Completion tokens | Total tokens | Root decisions | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 33 | 34,313 | 4,902 | 39,215 | 33 | 91.329 |
| Candidate | 33 | 36,448 | 5,729 | 42,177 | 33 | 103.999 |

Mind cost:

```text
MiniMax calls: 10
token usage: not exposed by current ModelClient
```

These are descriptive only. E1 intentionally did not add an equal-compute
baseline, and success remained primary to efficiency.

## 12. Verdict evaluation

| Criterion | Result |
| --- | --- |
| candidate completions > baseline | yes: 2 > 1 |
| RESCUE >= 2 | yes: 2 |
| REGRESSION <= 1 | yes: 1 |
| at least one relevant Directive → applied → observed → consistent trajectory change → verified rescue | **no** |
| candidate completions < baseline | no |
| REGRESSION >= 2 and RESCUE == 0 | no |

The numeric supported gate advanced to causal review, but the causal gate
failed. The negative gate also failed. Under the preregistered rules, the only
valid verdict is:

```text
EXPERIMENT_E1_INCONCLUSIVE
```

The dominant explanation is post-branch Execution-model stochasticity plus
insufficient Mind information use:

- three activations made no intervention;
- one activation failed strict parsing and remained inert;
- both Directives were based on a false/stale premise that the mission was not
  available;
- neither Directive produced a verified rescue;
- exact-equal/no-advisory inputs still produced different claim behavior.

No prompt, schema, task, budget, threshold, or fixture was changed after seeing
these results, and no additional samples were added.

## 13. Matt skills, Ponytail, and review findings

### `/codebase-design`

Three small designs were considered: artifact-oriented frozen snapshots, a
single procedural runner, and a fixture-centric design. The selected design
kept explicit `FrozenTaskSpec`, `FrozenPrefix`, branch copy, and one registered
campaign runner. It reused public Execution durability and Mind A–E0 seams
without adding a Manager, registry, scheduler, or production abstraction.

The design audit also selected uniform `N=1`, identified the supported durable
restart boundary, and exposed the absence of an Execution IPython filesystem
sandbox.

### `/tdd`

Fork equality and request equality were proven before real Mind integration;
NoChange, Directive delta, one-shot reconvergence, native provider payload,
budget/verifier equality, and cross-arm mutation rejection followed. No real
provider call occurred until the final nine targeted tests and pre-run
regressions were green.

### `/code-review`

Parallel Standards and Spec reviews found and resolved these pre-run issues:

- sibling/control-artifact discoverability: actor workspaces moved to unrelated
  high-entropy roots and are rehashed immediately before each arm;
- false assurance from literal path scans: removed in favor of the explicit
  non-adversarial full-trace review policy;
- blinded output: a separate blinded-primary artifact is emitted and reviewed
  first;
- endpoint freeze: full credential-free MiniMax base URL is exact-checked;
- source freeze: `Execution/ipython_control.py` was added to source hashes.

The review retained two explicit limitations: wall time is a post-return
validity check rather than preemptive cancellation, and randomized separation
is not a structural sandbox.

### Ponytail

Ponytail removed the generic object normalizer/serializer, redundant state and
request hashes, fake control-access substring scanner, progress file, and
duplicated arm-order flow. The final review found no must-cut framework;
remaining bulk maps directly to preregistration, fork integrity, real-provider
orchestration, evidence, and H1–H6.

## 14. Regression evidence

Critical suites were run before and after the real campaign:

| Suite | Before | After |
| --- | ---: | ---: |
| `python -m pytest Mind -q` | 128 passed | 128 passed |
| `python -m pytest Execution -q` | 5 passed | 5 passed |
| `python -m pytest Execution_lab2 -q` | 147 passed | 147 passed |
| production Mind gate test set | 54 passed | 54 passed |

Final root regression:

```text
338 passed, 24 skipped, 2 warnings
```

The warnings came from pinned upstream MAGMA deprecation/future notices, not
from E1 failures.

Final repository checks:

```text
safe artifact JSON parse: PASS
git diff --check: exit 0
git status --short: inspected
```

`git diff --check` emitted only pre-existing Windows LF/CRLF conversion
warnings. The repository was already substantially dirty before E1; those
unrelated modified and untracked files were preserved. E1 added only these
files under `Mind/`:

```text
Mind/behavioral_experiment.py
Mind/test_behavioral_experiment.py
Mind/fixtures/e1/manifest.json
Mind/fixtures/e1/BLINDED_PRIMARY_REVIEW.md
Mind/fixtures/e1/EXPERIMENT_E1_ARTIFACT.json
Mind/docs/EXPERIMENT_E1_RESULT.md
```

No production module outside `Mind/` was changed by E1.

## 15. Limitations

- Six synthetic pairs with one continuation per arm cannot estimate provider
  variance.
- Frozen-prefix pairing removes pre-branch stochastic divergence, not
  post-branch DeepSeek stochasticity.
- The experiment clones durable state/workspace/EventLog, not arbitrary live
  IPython namespace state.
- `N=1` tests early supervision only.
- Memory was deliberately fixed empty; this is not a Memory-value experiment.
- The 5000-character context cap was a shared preregistered experiment config,
  not the current Execution default.
- Wall time invalidates after a synchronous continuation returns; it does not
  preempt at exactly 420 seconds.
- Mind model token usage is unavailable from the current `ModelClient`.
- Execution IPython remains trusted worker authority; E1 used separation and
  trace review rather than claiming sandboxing.
- `FileContentEquals` persists plaintext expected content in host controls.
- The two Directives reveal an information-quality problem: Mind can act before
  inspecting the Execution observation and may mistake empty long-term Memory
  for absence of task-local information.

## 16. Maintained conclusion and next single-variable hypothesis

E1 does not justify production Mind supervision or any claim that current Mind
improves Execution. It does establish that the paired harness, A–D activation
and trace, D lifecycle, and E0 one-shot delivery can execute together under
real providers without violating their tested boundaries.

If a follow-up experiment is authorized, the clearest single variable is
information availability: expose the already-legal bounded
`ExecutionObservation` to the first Mind decision (or require inspection before
a Directive) while holding tasks, providers, prompts other than that single
mechanism, budgets, and delivery unchanged. That would test whether stale
Directives came from insufficient Execution information. It must be a new
preregistered experiment; E1 itself remains unchanged.

No production promotion is recommended from this result.
