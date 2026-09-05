# World Model Experiment W0 Result

## Verdict

```text
WORLD_MODEL_W0_PASS
```

W0 establishes only the minimal predictive-world-model substrate: a versioned
Prediction is durably recorded before an action outcome exists, an independent
Environment supplies the Observation, and host-owned deterministic comparison
produces a replayable MATCHED, ERROR, or UNVERIFIABLE result. It does not show
that a World Model can learn, improve Execution, or wake Mind.

## Hypothesis and single variable

Hypothesis:

> Lumina can preserve “what the model expected” as a first-class durable fact
> before reality is observed, then deterministically judge that expectation
> against independent evidence without hindsight reconstruction.

The only experimental variable is the addition of the first-class
`Prediction -> Observation -> PredictionResult` lifecycle.

| Baseline | W0 candidate |
|---|---|
| `Action -> Environment -> Observation` | `WorldModel.predict -> durable Prediction -> Action -> independent Observation -> deterministic comparison -> durable PredictionResult` |

Execution planning, Mind cognition, Directive, Intention, Memory, providers,
trigger policy, and model revision are held absent.

## Source audit

### Existing Lumina Mind Trace

`Mind/trace.py::MindTrace` already supplies a strict JSONL append/flush/fsync
pattern, immutable event snapshots, causal refs, strict reopen, and deterministic
projection. It is nevertheless a closed, activation-specific Mind trace with an
activation event vocabulary. W0 does not generalize it into a system EventBus and
does not import its private helpers. Instead, the experiment keeps one independent,
three-event trace in `Mind/world_model_experiment.py`. This preserves locality and
avoids changing the accepted Experiment C surface.

### Tycho

**Source:** official
[`NIMI-research/Tycho`](https://github.com/NIMI-research/Tycho), commit
[`f68912a764372ead0a610db2e1c011d41ce5197e`](https://github.com/NIMI-research/Tycho/commit/f68912a764372ead0a610db2e1c011d41ce5197e).

- [`builder.system.j2`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/prompts/builder.system.j2)
  defines generated `world_model.py` through `init_state`, `transition`, `render`,
  and `outcome`, while also describing optional planning conveniences. W0 borrows
  only deterministic state/action prediction; it deliberately omits outcome,
  actions, subgoals, heuristic, and planner surfaces.
- [`wmlib_template.py::verify`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/wmlib_template.py#L567)
  advances the executable model from an initial state and compares rendered
  predictions with recorded transitions. W0 adapts the narrow principle that
  independent observed evidence grades predicted dynamics.
- [`wmlib_template.py::verify_outcome`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/wmlib_template.py#L1071)
  replays recorded evidence to falsify terminal predictions. W0 borrows replayable
  falsification, not Tycho's terminal/game domain.
- [`builder.py::WorldModelBuilder.build`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/agent/builder.py#L318)
  is a bounded LLM/tool loop that edits `world_model.py`. It is explicitly outside
  W0; no Builder, model write, model promotion, or model call was added.

### DeepSeek Harness

**Source:** official
[`deepseek-ai/DeepSeek-Harness`](https://github.com/deepseek-ai/DeepSeek-Harness),
commit
[`0a53fb55bea101816fa226bb964ae2bed71c343b`](https://github.com/deepseek-ai/DeepSeek-Harness/commit/0a53fb55bea101816fa226bb964ae2bed71c343b).

- [`SessionEventMap` / `SessionEvent`](https://github.com/deepseek-ai/DeepSeek-Harness/blob/0a53fb55bea101816fa226bb964ae2bed71c343b/packages/core/session/src/types.ts)
  treat the append-only event log as source of truth and carry contiguous seqs and
  earlier `sourceEventSeqs`.
- [`Session.append` / `Session.deriveMessages`](https://github.com/deepseek-ai/DeepSeek-Harness/blob/0a53fb55bea101816fa226bb964ae2bed71c343b/packages/core/session/src/index.ts)
  append immutable JSON facts and derive model-visible history from the log.

W0 adapts append-only facts, earlier causal refs, and deterministic derivation. It
does not copy DSH Session, surface replacement, plugins, persistence backends, or a
generic Context projection; W0 has no model-visible Context.

### Prime Agent

**Source:** official
[`PrimeIntellect-ai/prime-agent`](https://github.com/PrimeIntellect-ai/prime-agent),
commit
[`c718bf3c30fd8da206ed551837cbb54f7ad15948`](https://github.com/PrimeIntellect-ai/prime-agent/commit/c718bf3c30fd8da206ed551837cbb54f7ad15948).

[`core/prompts/rlm.ts`](https://github.com/PrimeIntellect-ai/prime-agent/blob/c718bf3c30fd8da206ed551837cbb54f7ad15948/packages/coding-agent/src/core/prompts/rlm.ts)
defines persistent IPython as the Actor's long-lived programmable control
environment, with state, shell orchestration, and recursive calls. This is a
contrast boundary only: W0 gives `WorldModel` exactly `predict(state, action)` and
no IPython, shell, filesystem, process, or action authority.

The three temporary source clones were used only for audit and removed afterward;
none is a Lumina dependency.

## Minimal design

Implementation: `Mind/world_model_experiment.py`.

```text
CounterState + CounterAction
        -> CounterWorldModel.predict()
        -> ExpectedObservation
        -> host fsyncs WORLD_MODEL_PREDICTION_MADE
        -> CounterEnvironment.observe()
        -> host fsyncs ENVIRONMENT_OBSERVED
        -> deterministic _compare()
        -> host fsyncs WORLD_MODEL_PREDICTION_RESOLVED
```

`WorldModel` is the only model protocol:

```python
class WorldModel(Protocol):
    version: str

    def predict(
        self,
        state: CounterState,
        action: CounterAction,
    ) -> ExpectedObservation: ...
```

Its public behavior is prediction only. The concrete domain is a deterministic
counter where `next = value + delta + model_bias`; the canonical model uses zero
bias, while tests use nonzero bias to produce an intentionally wrong prediction.
`CounterEnvironment` independently computes `value + delta` and is the sole source
of `observed_value`.

The World Model never receives the trace path or Environment instance. Host-owned
`run_counter_prediction_cycle` persists the Prediction with `flush + fsync` before
calling `CounterEnvironment.observe`. Prediction therefore cannot execute, cancel,
or change the action.

## Durable schemas

### Prediction

| Field | Meaning |
|---|---|
| `prediction_id` | Bounded Prediction identity |
| `world_model_version` | Version matching `wm-[A-Za-z0-9][A-Za-z0-9._-]{0,63}` |
| `state_ref` | State on which prediction was based |
| `action_ref` | Action whose outcome is predicted |
| `expected_observation.value` | Predicted counter value |

### Observation

| Field | Meaning |
|---|---|
| `observation_id` | Bounded independent evidence identity |
| `action_ref` | Action actually associated with the observation |
| `observed_value` | Environment value; `null` means insufficient evidence |

Observation is an immutable value object. A wrong model produces ERROR; it never
rewrites or “corrects” the Observation.

### PredictionResult

| Field | Meaning |
|---|---|
| `status` | `MATCHED`, `ERROR`, or `UNVERIFIABLE` |
| `prediction_id` | Source Prediction identity |
| `observation_id` | Source Observation identity |
| `world_model_version` | Exact version that made the Prediction |
| `result_event_seq` | Durable result event sequence |
| `source_event_seqs` | Exactly `(1, 2)`: Prediction and Observation events |

Comparison is fixed:

```text
observed_value is null             -> UNVERIFIABLE
observed_value == expected value   -> MATCHED
otherwise                          -> ERROR
```

It reads no wall clock, random source, model, network, or external state.

## Event vocabulary and ordering

The complete vocabulary is intentionally three events rather than three separate
terminal event types:

```text
1  WORLD_MODEL_PREDICTION_MADE       source_event_seqs=[]
2  ENVIRONMENT_OBSERVED              source_event_seqs=[1]
3  WORLD_MODEL_PREDICTION_RESOLVED   source_event_seqs=[1,2]
```

The resolved event carries the terminal status. This is sufficient to express all
three outcomes without a generic event system.

Strict reopen requires:

- exactly three newline-terminated JSONL records within a 64 KiB bound;
- no duplicate JSON keys or unknown envelope/payload fields;
- the known trace format, exact event order, and contiguous seqs;
- unique causal refs to earlier events, with the exact W0 source sets;
- valid bounded IDs and model-version shape;
- equal Prediction/Observation action refs;
- result IDs/version matching their sources;
- result status equal to a fresh deterministic comparison.

No repair is attempted. Missing facts, forward refs, duplicates, malformed shapes,
reordering, truncated termination, and result tampering all fail conservatively.

### Anti-hindsight invariant

`WORLD_MODEL_PREDICTION_MADE` is appended, flushed, and fsynced before the host calls
the Environment. `WorldModelTrace.record_observation` rejects an empty trace, and
strict reopen rejects any history whose first event is not the Prediction. Thus the
accepted history cannot be `Observation -> reconstructed Prediction`.

## TDD progression

The `/tdd` progression was observable rather than retrospective:

1. W0-1 was written first and failed collection with
   `ModuleNotFoundError: world_model_experiment`.
2. The smallest prediction/observation/MATCHED lifecycle made W0-1 green.
3. ERROR, UNVERIFIABLE, ordering, action linkage, and version tests passed on that
   same comparison seam; deterministic reopen then failed because `reopen` did not
   exist, and was implemented next.
4. The corruption matrix exposed missing checks for forward refs, model-version
   shape, falsified result, duplicate keys, and normalized ordering failures. Strict
   reconstruction made the suite green at 17 tests.
5. Standards review found the unterminated-final-record boundary. A new test first
   failed, the reader was tightened, and the final targeted suite reached 18 tests.

At the user's request, `pytest 9.1.1` was also installed into the current Conda
Python. This environment change is outside the repository and is not a W0 runtime
dependency.

## Required test evidence

| Case | Evidence |
|---|---|
| W0-1 correct prediction | Expected 3, Environment 3, MATCHED, exact three durable events |
| W0-2 incorrect prediction | Expected 3, Environment 4 unchanged, ERROR |
| W0-3 insufficient evidence | `observed_value=None`, UNVERIFIABLE |
| W0-4 prediction before observation | One durable Prediction line exists before observation; reverse append rejected |
| W0-5 wrong action linkage | Observation for action B rejected before append to Prediction for action A |
| W0-6 model version provenance | Same state/action under wm-v1 and wm-v2 retains different expected values, status, and source versions |
| W0-7 deterministic replay | Two independent reopen/replays equal the original result and causal refs |
| W0-8 corruption | Missing Prediction, missing Observation, forward ref, duplicate result, illegal version, reordered events, falsified result, duplicate JSON key, and unterminated record rejected |
| W0-9 Environment authority | Wrong model remains unchanged; Environment value remains 4 and grades ERROR |
| W0-10 no planning surface | Static public-method audit requires exactly `{predict}` on Protocol and implementation |

## Development-method checks

### `/codebase-design`

The chosen deep seam is `WorldModel.predict(state, action)`. All lifecycle mechanics
remain host-owned. Existing `MindTrace` was inspected but not generalized; one
counter domain and one W0 trace file are enough to falsify the hypothesis.

### Ponytail

- standard library only;
- one implementation file, one test file, one result document;
- no Manager, Registry, Factory, EventBus, plugin, provider, database, service, or
  migration layer;
- no speculative Environment protocol because W0 has one concrete domain;
- no modification of existing production modules.

The strict reader is longer than the state machine because W0 explicitly requires
conservative rejection for multiple corruption classes. Those checks are exercised
by current tests rather than future-facing abstraction.

### `/code-review`

- Spec axis: PASS, no findings.
- Standards axis: initially found one medium issue—an unterminated final JSONL
  record was accepted. The issue was reproduced, fixed, and re-reviewed: PASS.
- Final blocking findings: none.

## Validation

```text
python -m pytest -q test_world_model_experiment.py
18 passed

<repo .venv> python -m pytest -q
338 passed, 24 skipped, 2 warnings

python -m py_compile world_model_experiment.py test_world_model_experiment.py
PASS
```

The root warnings are existing MAGMA deprecation/future warnings. An initial root
run placed pytest's base temp inside `Mind/`; six Recall sandbox-safety tests
correctly rejected that repository path. The unchanged suite was rerun with an
external system temp directory and passed as shown above.

Final safety evidence:

```text
git diff --check
PASS (exit 0; only existing LF/CRLF conversion warnings)

git diff --no-index --check -- NUL <each new W0 file>
no whitespace-error diagnostics

git -C Conversation_Memory/upstream/MAGMA status --short
clean (no status entries)

git -C Conversation_Memory/upstream/MAGMA diff --stat
clean (no diff)
```

No task-caused file outside `Mind/` was added or modified. The pre-existing dirty
worktree was preserved.

## Limitations and non-claims

- One trace represents one complete counter prediction cycle; there is no multi-cycle
  world-state store or production routing.
- The action is represented by an immutable `action_ref` and counter delta; W0 does
  not integrate the real Execution Organ or persist a separate Execution action event.
- No model-visible Context is projected because W0 makes no model call.
- Prediction Error is only a durable fact. It cannot rewrite/promote the World Model,
  invoke Builder, wake Mind, mutate Intention, or steer Execution.
- No DeepSeek call was needed. This is consistent with the repository's DeepSeek-only
  provider policy: W0 introduces no provider and invokes no alternative model.
- W0 does not establish learning, predictive coding, behavioral improvement,
  long-horizon modeling, or autonomy.

## Permitted claim

Lumina has established a minimal predictive-world-model substrate: before an action
outcome is observed, a versioned World Model can produce a durable Prediction;
afterward, an independent Environment supplies the Observation; deterministic,
replayable comparison produces Prediction Match/Error/Unverifiable while preserving
the Environment as reality authority.

```text
WORLD_MODEL_W0_PASS
```
