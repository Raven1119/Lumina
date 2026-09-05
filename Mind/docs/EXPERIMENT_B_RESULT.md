# Mind Experiment B — Structural Execution Authority Boundary Result

- Date: 2026-08-30
- Scope: isolated `Mind/` experiment; no production wiring
- Production code outside `Mind/`: unchanged
- Final verdict: recorded in section 15 after validation and review

## 1. Claim under test

Experiment B asks one question:

> Can Experiment A keep its bounded cognitive trajectory while replacing the
> arbitrary Execution callable with an activation-local, immutable, bounded
> data snapshot?

This experiment does not create a production Execution supervisor interface.
It does not test a Python security sandbox or prove that arbitrary hostile
Python code is side-effect-free.

## 2. Baseline, candidate, and single variable

The Experiment A baseline accepted an executable dependency:

```python
inspect_execution: Callable[[], object]
```

On an `inspect_execution` request, the Mind host invoked that arbitrary
callable and then attempted to project its result.

The Experiment B candidate accepts data instead:

```python
execution_observation: ExecutionObservation | None
```

The single changed variable is:

```text
Execution information dependency representation

baseline:  arbitrary injected callable
candidate: pre-supplied immutable bounded DTO
```

Held fixed:

- `ModelClient` and `MemoryRetriever` seams;
- `ActivationInput` and the strict JSON protocol;
- two model calls and one information request at most;
- fixed Memory Recall policy;
- `NoChange / Directive / DecisionIntent / ActivationFailure` results;
- prompt family, continuation shape, and conservative failure policy.

## 3. Design decision and implemented surface

`/codebase-design` found no need for `experiment_b.py`, a supervisor manager,
a registry, or a generic capability layer. The smallest deep seam was to
evolve the already-tested `experiment_a.py::run_activation` operation.

The candidate flow is:

```text
outside the Mind runtime
→ construct ExecutionObservation
→ run_activation receives data
→ validate exact DTO and exact built-in field types
→ project a new whitelist-only dict
→ model call #1
→ optional model request: inspect_execution
→ reveal the already-prepared dict
→ model call #2
→ final semantic result
```

`inspect_execution` remains a model-facing cognitive request name. It no
longer resolves to a callable, dynamic symbol, Execution object, or live
lookup.

The snapshot remains the Experiment A frozen DTO:

```text
ExecutionObservation
- goal
- status
- recent_outcome
- failure
```

Validation is performed before the first model call. The outer object must
have exact type `ExecutionObservation`; each non-null field must have exact
built-in type `str`. This ordering rejects arbitrary objects, subclasses,
callables, and `str` subclasses before invoking a property or custom string
method. A fresh dict containing only the four allowed fields plus the literal
capability name is used for the model continuation.

Existing bounds are unchanged:

- goal: 2,000 characters;
- status: 200 characters;
- recent outcome: 1,000 characters;
- failure: 500 characters;
- rendered observation JSON: 3,000 characters.

## 4. Memory boundary

The Memory path is intentionally unchanged:

```text
MemoryRetriever.recall(explicit_query, fixed RecallPolicy)
→ bounded MemoryContext projection
→ model continuation
```

Experiment B does not generalize all cognitive reads into snapshots. The
Lumina-owned `MemoryRetriever` is already the supported read-only Memory
facade. Tests also supply an Execution snapshot sentinel during a Memory
request and prove that the snapshot enters neither model call.

## 5. TDD trajectory

`/tdd` was applied as small authority-boundary slices:

| Slice | Red evidence | Minimum change / green evidence |
|---|---|---|
| B1 | public signature still exposed `inspect_execution` | replace it with `execution_observation` |
| B2 | old trajectory depended on invoking the fake inspector | reveal a pre-supplied snapshot only after the request |
| B3 | missing snapshot initially produced the wrong failure | return `execution_observation_unavailable` |
| B4/B5 | an arbitrary object/function was accepted far enough to reach normal cognition | exact outer DTO rejection before the model |
| B6 | DTO subclasses could carry unknown data or authority | exact-type-only snapshot boundary |
| B7 | exact DTO fields could still contain a callable or a `str` subclass | exact built-in field types before any method call |
| B8–B10 | Experiment A tests still used the deleted callable fake | mechanically migrate tests while retaining Memory, protocol, bounds, and determinism assertions |

The transitive-field tests were observed failing before the field guard:

```text
2 failed: malicious str subclass and callable field were accepted
```

After the minimum guard and test migration:

```text
12 B-targeted tests passed
46 complete Experiment A/B tests passed
```

The PATH-selected Conda Python has no `pytest`; all evidence below uses the
repository `.venv` interpreter.

## 6. Dynamic adversarial evidence

| ID | Evidence and result |
|---|---|
| B1 | Exact public parameter set contains `execution_observation`, contains no `inspect_execution`, and has no `**kwargs`. |
| B2 | Call #1 requests `inspect_execution`; the pre-supplied sentinel is absent from call #1, present only in the whitelisted observation for call #2, and the result is `Directive`. |
| B3 | A request with `None` snapshot returns `ActivationFailure("execution_observation_unavailable")` after one model call. |
| B4 | An ExecutionOrgan-like object with `interrupt()` and `run_goal()` is rejected before the model; mutation count stays zero. |
| B5 | A malicious function passed as the snapshot is rejected before the model and is never invoked. |
| B6 | An `ExecutionObservation` subclass carrying `secret` and `authority_object` is rejected; no model-visible request is constructed. |
| B7 | A hostile object's property is never read. An exact DTO containing a side-effecting `str` subclass or a transitive callable is rejected without invoking either. |
| B8 | AST regression finds no Execution package import, callable parameter, dynamic lookup primitive, or Execution mutation method reference. |
| B9 | The existing Memory request → observation → second-call → final trajectory passes, and a supplied Execution sentinel never leaks into that path. |
| B10 | Unknown capability, second capability, provider failure, Memory failure, strict JSON, bounded context, immutable inputs, and deterministic trajectories remain passing. |

## 7. Static authority audit

### 7.1 Dependencies accepted by `run_activation`

The operation accepts:

```text
ActivationInput
ModelClient
MemoryRetriever
ExecutionObservation | None
bool experiment flag
```

`ModelClient.generate` and the supported read-only
`MemoryRetriever.recall` remain executable collaborators. The precise claim is
that there is no arbitrary **Execution** callable/object dependency; it is not
that the Python function invokes no collaborators at all.

### 7.2 Callable and dynamic resolution

Source inspection establishes:

- no arbitrary Execution callable parameter and no `**kwargs` registration
  path;
- no `Callable` import;
- no `getattr`, `hasattr`, `vars`, `dir`, `asdict`, or member enumeration;
- no reflection dispatch;
- no `eval`, `exec`, `importlib`, or dynamic import;
- no callable table, tool registry, plugin dispatch, or name-to-symbol lookup.

Model strings are parsed as strict JSON. A capability value can reach only two
literal branches: `recall_memory` or `inspect_execution`. It cannot influence
Python symbol resolution. Unknown names fail as `invalid_model_output`.

### 7.3 Execution ownership and object identity

The module imports no `Execution/` package or authority-bearing implementation:

```text
ExecutionOrgan
ToolHost
AgentProcess
PersistentIPython
EventLog
DecisionFrame
ExecutionState / ExecutionResult
```

The continuation is rebuilt from a new dict of exact built-in values. It does
not serialize the input object's identity, `__dict__`, unknown fields, methods,
or subclass members. The DTO itself is not passed to the model.

For an invalid outer object, `type(value) is ExecutionObservation` is checked
before reading any field, so a property getter cannot run. For an exact DTO,
field objects are checked with `type(field) is str` before `.strip()` or
`len()`, so an overridden `str` method cannot run. Callable/object field values
are rejected before model or capability processing.

### 7.4 Mutation paths

The experiment has no Execution/Intention mutation operation. It does not
import or call `run_goal`, `claim_complete`, `interrupt`, `resume`,
`spawn_child`, IPython, shell, filesystem, process, browser, or persistence
APIs. `Directive` and `DecisionIntent` remain inert return values.

Thus `inspect_execution` now means only: reveal a bounded data value already
present for this activation.

## 8. Experiment A regressions

The complete Experiment A/B module suite passes. In particular:

- direct `NoChange` and all three legal semantic results;
- one Memory continuation and one Execution snapshot continuation;
- one information round changing a deterministic scripted judgment;
- strict JSON and unknown-capability rejection;
- second capability request with no third model call;
- provider and Memory conservative failures;
- all input, output, observation, and semantic-result bounds;
- repeatable result, model sequence, and Memory call sequence.

The prior Execution-callable exception test was removed because an Execution
callable is no longer a legal dependency. B3–B7 now test the candidate's
corresponding safe failures at the data boundary.

## 9. Skills used

### `/codebase-design`

It identified the single existing operation as the correct seam and kept the
change inside that deep module. No second runner, `SupervisorManager`, adapter
hierarchy, registry, framework, or future production abstraction was added.

### `/tdd`

Authority tests were added before each minimum boundary change. The important
red state was the transitive-object loophole: checking only the outer DTO still
allowed a callable or side-effecting `str` subclass in a field.

### `/code-review`

Two independent review axes inspected the final code: repository Standards and
the Experiment B Spec. Their findings and resolution are recorded in section
11.

### Ponytail

Applied rules: Think Before Coding, smallest vertical slice, small reversible
change, test before promotion, reuse existing ownership boundaries, and avoid
speculative abstraction. The result is one parameter replacement, one eager
data projection, and focused tests; there is no new framework or production
surface.

## 10. Validation

Final commands using the repository interpreter:

```text
.venv\Scripts\python.exe -m pytest Mind\test_experiment_a.py -q \
  -k "b1 or b2 or b3 or b4 or b5 or b6 or b7 or b8 or b9 or b10"
12 passed, 34 deselected

.venv\Scripts\python.exe -m pytest Mind\test_experiment_a.py -q
46 passed

.venv\Scripts\python.exe -m pytest \
  tests\test_mind_gate.py \
  tests\test_mind_llm_gate.py \
  tests\test_mind_gate_shadow.py \
  tests\test_mind_gate_operational.py \
  tests\test_mind_promotion_controls.py -q
24 passed

.venv\Scripts\python.exe -m pytest -q
338 passed, 24 skipped

.venv\Scripts\python.exe -m py_compile \
  Mind\experiment_a.py Mind\test_experiment_a.py
PASS
```

The root suite emitted only the two existing upstream warnings: MAGMA's
`ast.Str` deprecation and the sentence-transformers method rename.

## 11. Code-review findings

The required two-axis `/code-review` used the task-start Experiment A state as
the fixed point. The files were already untracked, so no Git commit could be
used as that fixed point; reviewers were given the exact B change scope and
excluded unrelated dirty-worktree changes.

Final results:

```text
Standards: PASS — 0 blocking, 0 non-blocking findings
Spec:      PASS — 0 blocking, 0 non-blocking findings
```

Standards confirmed the minimal-change, ownership, exact projection, no
production coupling, and Ponytail constraints. Spec confirmed B1–B10,
including the transitive callable/`str`-subclass guards, no-snapshot behavior,
Memory preservation, and Experiment A regressions. No corrective change was
required after review.

Both reviewers retained the same caveat: this removes the Execution
callable/object seam; it does not remove the intentional `ModelClient` or
`MemoryRetriever` collaborators and does not prove a general Python sandbox.

## 12. Git and scope audit

Experiment B's intended task files are:

```text
Mind/experiment_a.py                 modified from Experiment A
Mind/test_experiment_a.py            modified from Experiment A
Mind/docs/EXPERIMENT_B_RESULT.md     added
```

No `experiment_b.py` was needed. No file was changed in `Execution/`, `core/`,
`Conversation_Memory/`, `Dream/`, the production Mind gate, or pytest discovery
configuration.

The repository was already dirty at task start, including the untracked
Experiment A files and unrelated tracked/untracked work. Those changes were
preserved. This task performed no commit, push, rebase, reset, or history
rewrite.

Final Git safety evidence:

```text
git diff --check
PASS (exit 0)

git status --short
completed; Experiment B task files remain under the untracked Mind paths
```

`git diff --check` printed only LF-to-CRLF warnings for pre-existing unrelated
tracked files. Additional no-index whitespace checks of the three Experiment B
task files reported no whitespace errors; their non-zero status only denotes
that each untracked file differs from the Windows `NUL` baseline.

## 13. Acceptance assessment

The implementation and dynamic evidence satisfy B1–B10:

1. the arbitrary `inspect_execution` callable seam is removed;
2. the model-facing cognitive request still works;
3. Execution information comes only from a pre-supplied bounded DTO;
4. authority-bearing objects and callables are rejected before cognition;
5. invalid object properties and transitive custom methods are not invoked;
6. subclass/unknown fields cannot enter model-visible context;
7. no dynamic symbol/callable resolution or Execution import exists;
8. Memory behavior and Experiment A bounds/failures/determinism remain intact;
9. no production code outside `Mind/` is changed by this experiment;
10. target, production Mind-gate, and root regressions pass.

The required two-axis code review and Git safety checks also pass.

## 14. Limitations and non-claims

- This is an isolated experiment with deterministic mocks, not live Execution
  supervision or real-provider semantic-quality evidence.
- No Execution-owned supervisor projection/interface has been implemented.
- No production trigger, `MessageRuntime` wiring, or live snapshot acquisition
  exists.
- Python is not a capability-secure language. The evidence covers this
  Lumina module/API seam, not a general hostile-code sandbox.
- The runtime still calls the explicitly injected `ModelClient` and the
  Lumina-owned read-only `MemoryRetriever`; the claim is specific to Execution
  authority.
- No Trace, Directive persistence/application, Intention mutation, Nervous,
  Focus, IPython, shell, browser, or filesystem authority was added.

The permitted claim, if the final gate passes, is only:

> Experiment B's Mind runtime no longer holds Execution callable/object
> authority; Execution information enters the cognitive loop through an
> activation-local bounded immutable data snapshot.

## 15. Final verdict

```text
EXPERIMENT_B_PASS
```

The Experiment A cognitive trajectory is preserved while the only Execution
information dependency has become a pre-supplied, exact, bounded data snapshot.
There is no valid Execution callable/object injection path, and the adversarial,
regression, static-review, root-test, and Git-safety gates all pass within the
experiment's deliberately narrow claim.
