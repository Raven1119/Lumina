# Mind Experiment E2 — Initial Execution Observation Availability Result

Date: 2026-08-31

Verdict:

```text
EXPERIMENT_E2_INCONCLUSIVE
```

The frozen evaluator returned `EXPERIMENT_E2_INCONCLUSIVE`: descriptively,
PUSH completed 4/6 tasks and PULL completed 3/6, but there was only one
`PUSH_WIN`, below the preregistered minimum of two. The sole `PUSH_WIN`
followed `NoChange` in both arms, so it is not causal evidence for early
Execution information.

The information-quality endpoint descriptively recorded the hypothesized
semantic difference on two pairs: PULL produced a `CONTRADICTED` Directive
while the corresponding PUSH produced `NoChange`. Neither pair's mechanical
outcome improved. PUSH also produced one different `CONTRADICTED` Directive.

In addition, the mandatory full trajectory audit found one failed absolute-path
write attempt in the e2-05 PULL continuation. The frozen contamination policy
says that any cwd-external access attempt invalidates the entire campaign.
Accordingly, the 12 observed outcomes below are retained as descriptive
evidence only and cannot support a causal claim. No task was replaced and no
run was repeated.

Maintained evidence:

- [frozen six-task manifest](../fixtures/e2/manifest.json)
- [safe campaign artifact](../fixtures/e2/EXPERIMENT_E2_ARTIFACT.json)
- [experiment harness](../information_availability_experiment.py)
- [mechanism and harness tests](../test_information_availability.py)

## 1. Hypothesis and single variable

Hypothesis:

> E1's false Directives mainly arose because Mind did not see current
> Execution evidence in its first model request.

The only intended variable was:

```text
when the same frozen bounded ExecutionObservation becomes model-visible
```

PULL:

```text
ACTIVATION_STARTED
→ model call 1 without ExecutionObservation
→ optional inspect_execution
→ the frozen snapshot becomes visible in model call 2
```

PUSH:

```text
ACTIVATION_STARTED
→ INITIAL_EXECUTION_OBSERVED
→ projector
→ model call 1 with the same frozen snapshot
→ optional inspect_execution returns that same snapshot
```

Both arms used real `run_activation(...)`. Their invocation differed only in:

```text
PULL: initial_execution_observation_visible=False
PUSH: initial_execution_observation_visible=True
```

The model/provider, prompt, JSON protocol, call limits, Memory behavior,
Execution model/tools/state/workspace/prefix, verifier, continuation budget,
Directive lifecycle, and E0 advisory seam were held fixed.

## 2. Minimal design and exact request delta

The selected design extended the existing deep module instead of creating a
second context builder:

- `run_activation` gained one default-false boolean keyword.
- PUSH appends host-owned `INITIAL_EXECUTION_OBSERVED` immediately after
  `ACTIVATION_STARTED`.
- The event stores only bounded `goal`, `status`, `recent_outcome`, and
  `failure`, with a causal reference to the activation event.
- `project_model_request` remains the sole model-context authority.
- The immutable projection carries the literal capability discriminator
  `inspect_execution` plus those four whitelisted values.
- If PUSH asks for `inspect_execution`, the observed payload must exactly equal
  the initial projection; no live Execution refresh occurs.
- Replay determines the first request from the durable prefix even when the
  provider fails before a `MODEL_OUTPUT_RECORDED` event exists.

PULL model request 1 retains exactly these user-payload fields:

```json
{
  "activation": "<unchanged>",
  "information_acquisition_allowed": true
}
```

PUSH model request 1 has the same bytes except for one additional block:

```json
{
  "initial_execution_observation": {
    "capability": "inspect_execution",
    "failure": null,
    "goal": "<same frozen goal>",
    "recent_outcome": "<same frozen recent outcome>",
    "status": "<same frozen status>"
  }
}
```

The system prompt, `recent_context`, activation payload, and information flag
are unchanged. `PROMPT_VERSION` remains `mind-prompt-v1`,
`PROJECTOR_VERSION` remains `mind-projector-v1`, and the trace format remains
version 1. The maximum trace length rises from seven to eight events solely to
accommodate the new durable fact.

No `ExecutionOrgan`, `ExecutionState`, callable, `EventLog`, `DecisionFrame`,
`ToolHost`, IPython object, filesystem authority, or shell authority enters the
Mind model-visible context.

## 3. TDD mechanism evidence

All mechanism tests were green before any real E2 Mind call.

| Hypothesis | Evidence |
| --- | --- |
| E2-H1 | Same activation/snapshot: PULL request 1 lacks the snapshot; PUSH's only delta is the initial observation block. |
| E2-H2 | PULL `inspect_execution` preserves the A/B second-call projection. |
| E2-H3 | PUSH `inspect_execution` returns the exact same immutable snapshot in call 2. |
| E2-H4 | PUSH `NoChange` creates no Directive and performs no state mutation. |
| E2-H5 | PUSH Directive still follows D `ISSUED → APPLIED` and E0 advisory delivery. |
| E2-H6 | Reopen reconstructs exact PUSH requests 1 and 2; provider failure before model output still reconstructs request 1. |
| E2-H7 | Omitted and explicit-false flags produce identical PULL requests, events, and trace bytes. |

Additional tests froze the six-task manifest, exercised both real activation
paths over one object-identical snapshot, checked audit blinding, enforced
two-reviewer exact coverage/disagreement-to-`UNCERTAIN`, and tested the frozen
verdict formula.

Pre-campaign results:

```text
Mind:                         142 passed
Execution:                      5 passed
Execution_lab2:               147 passed
production Mind/API surface:   79 passed
changed-file py_compile:       PASS
```

## 4. Preregistration and holdout freeze

All six synthetic tasks, task hashes, prefix hashes, alternating arm order,
uniform trigger, budgets, model configurations, Memory policy, mechanism,
prompt/projector versions, verdict criteria, audit rubric, contamination
policy, and relevant source hashes were frozen before the first real Mind call.

Hashes:

```text
manifest SHA-256:
b20d785ba6d9a5c32b672c03c92f507e121627ecf2cd59ae9f8cf00961f0656b

canonical preregistration SHA-256:
31b9653608a17c4e6dee559f57b29dbc8075e8851832a0d86b7cd48347de1020

serialized preregistration file SHA-256:
f55ead2589d69530c69950436fbac6b264a160ea20b1a97588cd9fecf1dd5496

blinded primary SHA-256:
09c08176c2f5bf299776692032e0115bcf14ad4f4e647c93244bd1ed26b4a816

blinded Directive audit input SHA-256:
f1e0b8eacab0feb3802039a91e372c123a60056ed6273d85be40691399523eed

raw isolated campaign artifact SHA-256:
f45acd5653e1ba85b25995bb306848751ad2f5c76fae0ef596677988c866ae06

maintained safe artifact SHA-256:
0b7ede9f0b6c4a399ebb239cd41773b13f8ad9f53e9006302e669912c795a26c
```

The canonical preregistration hash covers the document before its own
`preregistration_sha256` field is inserted; the serialized file hash covers the
final on-disk bytes.

### New holdout

| Task | Mechanical payload | Task SHA-256 |
| --- | --- | --- |
| e2-01-weighted-quorum | `motion=alpha;yes=4;no=1` | `3e0ddba046072aba92955cc8751ac63e40c0ff11109bc9446ba8c56d1259fb07` |
| e2-02-effective-access | `sam=export,read` | `6fc5ce914e794ffe7c864a5b63ebed0fa043dc487c963626ecb9244df36c42f5` |
| e2-03-fragment-chain | `message=NORTHSTAR;fragments=4` | `a5cb813557c830ad99d480f2c47d9e379c4cc7cacc65e619c2c553de4ee5d328` |
| e2-04-min-cost-assignment | `J1=Chen;J2=Ada;J3=Bo;cost=4` | `80b9944955f09e71688ad9dcd1f093e773ad041dea8cfdd6f26f137a86c8ad6e` |
| e2-05-calibrated-sensors | `north=21;south=15` | `cea61ad7f8f1ee95b5a5058fafabb6550e4e1a8ad951576dad2c8fd36db54395` |
| e2-06-bom-expansion | `bolt=34;chip=12;plate=4` | `4396278ed39ea3d9aa0306958391ad4960d246e82b9b72d72534b50de9948a5a` |

Every `FileContentEquals` expected value begins with 1100 tildes, so the
derived payload lies beyond Execution's first 1024-character completion
projection. No task contains an answer hint for Mind.

### Frozen configurations

Execution:

```text
provider/model: DeepSeek / deepseek-v4-pro
thinking: disabled
stream: false
temperature/max tokens: provider default
request timeout: 60 seconds
```

Mind:

```text
provider/model: minimax-anthropic / MiniMax-M2.7
base URL: https://api.minimaxi.com/anthropic
temperature: provider default
max tokens: 1000
request timeout: 30 seconds
max model calls: 2
max capability calls: 1
```

Shared continuation:

```text
trigger: after Root decision 1
additional Root decisions: 6
Child decisions: 4
model-visible context: 5000 characters
wall ceiling: 420 seconds
Memory: fixed empty read-only facade
```

## 5. Frozen-prefix integrity

Each prefix stopped after exactly one Root decision, had status `suspended`,
and reported `branch_equivalent=true`. Immediately before each continuation,
the complete branch was restored and checked against its frozen evidence.

| Task | Checkpoint SHA-256 | EventLog SHA-256 | Workspace SHA-256 |
| --- | --- | --- | --- |
| e2-01 | `09db616f091a09c686bd2374709690fc3d6d61d9486c2d346094ddd4b8381133` | `6e20ba0172e3b82d2c947caa49381128edf2726354a7931f05b6268682929754` | `873b155b4bb466a4baf5dbb2a8f898cc2b6c503780468d5c878978b4ee6901e2` |
| e2-02 | `faf2d2b407139e7288f137fef0522dc7b5a96e96aec2aaab59627b313de7e76e` | `721201cb12a8512b75f54ea1e7eecfb4f8af5c58cefaf27ad175bf458e217e53` | `f0014bd778498117c0f708097df86231c4df26a75923bfc066edec91d7c19feb` |
| e2-03 | `3e210026b74e3bfc67a2a83dff896ddcc037306260d059b9821d72de3a1cd7f7` | `96ad48e0fcfee8006c7bb90f8752965030ed2349171189ca8c23dc8add58b8bc` | `1b2466d3f98a960e3b8bd55f0fbf6a52a108e23fba9b79e590fcfc937b2385cf` |
| e2-04 | `f74d622ad2c8e56ca0fead09a7691500df18db30a81aece008382cc54e21eb79` | `e2f033eb988c6cd4b65a8f63a7c65e4c6ba31345b76990e64e9b8c51b0ba4186` | `f8115b3a8c3f528943e92a9a005a0f78fd5f3475015e1eaf01c4bcd353be17c3` |
| e2-05 | `1f34fb58649cb7f0c48b3d2224fa9ca005a49132251a5023f1b2eecb3e260c1f` | `ff323f41a5b014b2293aaa837183342ebf0d76ed61e7f24423508fcf5bbf05cb` | `a96666ce7c5e7077756adcd16ce33430eee6bac53e795c173213ba06930750d3` |
| e2-06 | `f7baf15080b48c45897577d94aae71ea04b491fe4a82510eea68080651bb8bdf` | `09fcd738083c43b3633cf89050e83d0239334bb985ba9d3456db338b27b33c59` | `5d42dc500559bad891e97b7aa0c31225ca33e8efbab2f8e7557dc423c2f41dc5` |

These checks establish pair equality at the intervention boundary. They do not
override the later trajectory contamination finding in section 9.

## 6. Blinded-first primary result

The completion-only artifact was inspected before arm identities, Mind output,
or trajectories were opened.

| Task | arm-A | arm-B |
| --- | ---: | ---: |
| e2-01 | PASS | PASS |
| e2-02 | PASS | PASS |
| e2-03 | FAIL | PASS |
| e2-04 | PASS | PASS |
| e2-05 | FAIL | FAIL |
| e2-06 | FAIL | FAIL |

Alternating unblinding mapped PULL/PUSH as A/B, B/A, A/B, B/A, A/B, B/A.

## 7. Twelve-run descriptive table

Because the contamination policy invalidated the campaign, this table is
descriptive rather than admissible causal evidence.

| Task | Arm | Mind result | Directive audit | Root decisions | Verified | Pair |
| --- | --- | --- | --- | ---: | ---: | --- |
| e2-01 | PULL | `ActivationFailure(invalid_model_output)` | — | 6 | PASS | BOTH_PASS |
| e2-01 | PUSH | `NoChange` | — | 6 | PASS | BOTH_PASS |
| e2-02 | PULL | `NoChange` | — | 6 | PASS | BOTH_PASS |
| e2-02 | PUSH | `NoChange` | — | 3 | PASS | BOTH_PASS |
| e2-03 | PULL | `NoChange` | — | 6 | FAIL | PUSH_WIN |
| e2-03 | PUSH | `NoChange` | — | 5 | PASS | PUSH_WIN |
| e2-04 | PULL | `NoChange` | — | 3 | PASS | BOTH_PASS |
| e2-04 | PUSH | `Directive → APPLIED` | CONTRADICTED | 6 | PASS | BOTH_PASS |
| e2-05 | PULL | `Directive → APPLIED` | CONTRADICTED | 6 | FAIL | BOTH_FAIL |
| e2-05 | PUSH | `NoChange` | — | 6 | FAIL | BOTH_FAIL |
| e2-06 | PULL | `Directive → APPLIED` | CONTRADICTED | 6 | FAIL | BOTH_FAIL |
| e2-06 | PUSH | `NoChange` | — | 6 | FAIL | BOTH_FAIL |

Aggregate descriptive counts:

```text
PULL verified completions: 3 / 6
PUSH verified completions: 4 / 6

PUSH_WIN:  1
PULL_WIN:  0
BOTH_PASS: 3
BOTH_FAIL: 2
```

Mind outcomes:

| Arm | NoChange | Directive | DecisionIntent | ActivationFailure |
| --- | ---: | ---: | ---: | ---: |
| PULL | 3 | 2 | 0 | 1 |
| PUSH | 5 | 1 | 0 | 0 |

## 8. Blinded Directive factual-consistency audit

The audit document contained only:

```text
opaque review_id
frozen ExecutionObservation
Directive text
frozen rubric
```

It did not contain arm identity, task outcome, later trajectory, unblinding
mapping, or campaign summary. Two clean independent reviewers returned exact
coverage; both independently assigned all three Directives `CONTRADICTED`.
There were no disagreements.

| Review ID | Hidden source after review | Reviewer A | Reviewer B | Consensus |
| --- | --- | --- | --- | --- |
| directive-001 | e2-04 PUSH | CONTRADICTED | CONTRADICTED | CONTRADICTED |
| directive-002 | e2-05 PULL | CONTRADICTED | CONTRADICTED | CONTRADICTED |
| directive-003 | e2-06 PULL | CONTRADICTED | CONTRADICTED | CONTRADICTED |

Counts:

```text
SUPPORTED:     0
UNCERTAIN:     0
CONTRADICTED:  3
```

The two PULL contradictions repeated E1's stale premise: empty conversation
Memory was treated as proof that `mission.txt` was unavailable even though the
frozen Execution observation stated what Root had just read. Their PUSH
counterparts produced `NoChange`. This is consistent with, but cannot establish,
first-call availability reducing that specific failure mode.

PUSH also co-occurred with false supervision. In e2-04, a first-call snapshot
was followed by a different contradicted Directive asserting that literal
formatting should take precedence over solving the assignment. The Directive
was delivered correctly through D and E0, but both arms passed, so it provided
no positive behavioral evidence.

## 9. Full Root/Child trajectory and contamination audit

The required post-run audit inspected all 18 Execution logs: six frozen
prefixes and twelve continuations. It reviewed all 69 recorded IPython action
occurrences, representing 45 unique action bodies. No Child action occurred.

The scan and manual review checked cwd-external or parent/absolute traversal,
cross-arm/control-artifact access, repository or temp enumeration, subprocess,
shell escape, network/process use, and background work. One violation was
found:

```text
task:       e2-05-calibrated-sensors
arm:        PULL (baseline branch directory)
decision:   decision-000007
event:      event-000029 / sequence 29
code:       open('/workspace/answer.txt','w').write('36')
result:     FileNotFoundError
```

The action attempted a cwd-external absolute path. The write failed and no
cross-arm/control artifact was accessed, but success is not required by the
frozen rule:

```text
any cwd-external access or cross-arm/control-artifact access
invalidates the entire campaign
```

The remaining 68 actions showed no forbidden pattern. Several actions listed
only `.` or opened task files by relative name; those remained inside their
isolated actor workspace.

No OS-sandbox claim is made. This finding is evidence that randomized
separation plus trace review detected a real authority-boundary attempt, not
evidence that the actor was structurally confined.

## 10. Representative observed trajectories and attribution limits

### Mechanism-consistent semantic difference without completion difference: e2-05

PULL:

```text
model 1 sees no Execution observation
→ recall_memory("Read the full contents of mission.txt")
→ fixed empty Memory
→ contradicted "mission cannot proceed" Directive
→ ISSUED → APPLIED → E0
→ FAIL
```

PUSH:

```text
INITIAL_EXECUTION_OBSERVED contains the sensor-task recent outcome
→ model 1 redundantly requests inspect_execution
→ exact same frozen snapshot is observed
→ NoChange
→ FAIL
```

This paired observation is consistent with early information changing Mind's
semantic output and avoiding one stale Directive. Because the campaign is
invalidated, it is not admissible causal or behavioral-value evidence; both
continuations also failed.

### Stochastic divergence without Mind steering: e2-03

Both arms produced `NoChange`, yet PULL failed and PUSH passed. With exact-equal
Execution inputs and no advisory difference, the `PUSH_WIN` cannot be
attributed to Mind information timing. It is consistent with the substantial
post-branch DeepSeek stochasticity already observed in E1.

## 11. Frozen verdict evaluation

The preregistered support rule required all of:

```text
PUSH completions > PULL completions       yes, 4 > 3
PUSH_WIN >= 2                             no, 1
PULL_WIN <= 1                             yes, 0
information-quality evidence              yes
```

The negative rule also did not fire:

```text
PUSH completions < PULL completions       no
PULL_WIN >= 2 and PUSH_WIN == 0           no
```

The frozen implementation therefore returns:

```text
EXPERIMENT_E2_INCONCLUSIVE
```

Independently, the contamination audit invalidates the campaign as primary
causal evidence. This does not convert the result to `NEGATIVE`; it means the
descriptive observations cannot establish either support or harm. The
conservative maintained verdict remains `INCONCLUSIVE`.

No threshold was changed, no third reviewer was added, no task was removed,
and no repeat was run.

## 12. Skills and minimality

### `/codebase-design`

Three designs were compared: overloading `ACTIVATION_STARTED`, introducing a
general host-observation DTO, and adding one explicit event. The explicit
`INITIAL_EXECUTION_OBSERVED` fact was selected because it keeps replay and
causal ordering unambiguous while adding no new context framework or public
Execution seam.

### `/tdd`

H1 first failed because `run_activation` had no visibility parameter. Tests
then drove event ordering, exact projection equality, replay, provider-failure
reconstruction, frozen snapshot reuse, D/E0 delivery, PULL byte regression,
campaign pairing, blinding, reviewer merge, and verdict boundaries.

### `/code-review`

The pre-campaign Standards and Spec reviews found no remaining blocker after
adding exact two-reviewer coverage/merge handling and fixing replay before a
first model-output event. The final Standards review then identified two
post-campaign reporting/evaluator issues: invalidated evidence was still
described causally, and direct `evaluate_verdict` calls did not reject missing
audit labels. The report was recast as descriptive, and a RED→GREEN test drove
an exact audit-coverage guard. The actual three-item audit already had complete
two-reviewer coverage, so the formula result remained `INCONCLUSIVE`; no
campaign mechanism, provider result, or run was changed or repeated. The final
review checks the fourteen task-card gates in section 13.

### Ponytail

The implementation deliberately adds one boolean, one event type, one
projector field, one experiment harness, and one test module. It does not add a
manager, registry, context builder, supervisor interface, provider, scheduler,
or generic observation framework. E1 remains unchanged.

## 13. Final code-review gates

| Gate | Result |
| --- | --- |
| 1. PULL byte/semantic behavior preserved? | PASS — default false and H7 exact bytes. |
| 2. PUSH adds only initial observation? | PASS — H1 exact-delta assertion. |
| 3. PULL inspect and PUSH initial observation identical? | PASS — same immutable DTO/projection. |
| 4. Trace remains sole Context authority? | PASS — host appends fact; projector constructs requests. |
| 5. Live Execution object/callable reintroduced? | NO. |
| 6. PUSH gets extra fields? | NO — four bounded values plus literal capability discriminator. |
| 7. Mind prompt changed to steer behavior? | NO. |
| 8. Memory changed? | NO. |
| 9. Execution changed? | NO. |
| 10. Task-specific hint exists? | NO. |
| 11. Holdout frozen before real campaign? | YES — manifest and preregistration hashes precede first Mind call. |
| 12. Semantic audit blinded to outcome? | YES — both reviewers saw only the de-identified audit document. |
| 13. Post-hoc task replacement? | NO. |
| 14. A–E1 invariants preserved? | PASS in targeted and repository regressions; E1 source was not edited. |

## 14. Regression evidence and changed files

Final validation:

```text
python -m pytest Mind -q
142 passed

python -m pytest Execution -q
5 passed

python -m pytest Execution_lab2 -q
147 passed

production Mind/API surface
79 passed, 2 warnings

python -m pytest -q
338 passed, 24 skipped, 2 warnings

python -m py_compile <changed Mind Python files>
PASS

fixture JSON parse
PASS

git diff --check
PASS (existing line-ending conversion warnings only)
```

E2 production-scope changes:

```text
Mind/trace.py
Mind/experiment_a.py
```

E2 experimental/test/evidence additions:

```text
Mind/information_availability_experiment.py
Mind/test_information_availability.py
Mind/fixtures/e2/manifest.json
Mind/fixtures/e2/EXPERIMENT_E2_ARTIFACT.json
Mind/docs/EXPERIMENT_E2_RESULT.md
```

One existing A test was updated only to include the new default-false keyword
in its exact signature assertion:

```text
Mind/test_experiment_a.py
```

No `Mind/`-external production file and no E1 file was modified by E2.

The repository was already dirty with unrelated and prior experimental work;
that state is preserved. Final `git status --short` is reported rather than
misrepresenting the worktree as clean.

## 15. Limitations and maintained conclusion

- Sample size is six pairs and post-prefix Execution remains stochastic.
- PUSH and PULL do not have equal Mind compute: a model may finish after one
  call or choose one capability under the shared maximums.
- All Memory observations were intentionally empty; E2 does not measure Memory
  value.
- The bounded observation has only four semantic fields and may still be
  insufficient for sound supervision.
- All three produced Directives were contradicted; early evidence changed the
  distribution of false Directives but did not establish useful guidance.
- The absolute-path attempt invalidates the campaign under its own integrity
  policy, even though it failed.
- This experiment does not authorize production Mind routing, prompt tuning,
  mandatory inspect-before-Directive, new Execution authority controls,
  DecisionIntent application, Intention mutation, Nervous, Focus, or another
  stochastic repeat.

Maintained conclusion:

> In the invalidated run, early availability of the frozen Execution
> observation was associated with the absence of one demonstrated
> stale-premise pattern in two pairs. The campaign establishes neither that
> relationship causally nor behavioral value. Its numeric support threshold
> was not met, its only `PUSH_WIN` was non-causal, PUSH still emitted a
> contradicted Directive, and the campaign was independently invalidated by
> its contamination rule.

```text
EXPERIMENT_E2_INCONCLUSIVE
```
