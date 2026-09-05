# World Model Shadow Integration S0 — Result

**Verdict:** `S0_INCONCLUSIVE`

S0 established that supported Execution can provide a bounded, redacted,
immutable PRE/OUTCOME surface to a zero-action-authority scripted shadow
predictor without changing Execution. It did not establish the full frozen
replay contract: after terminal history suppresses PRE to reject direct
hindsight construction, the earlier PRE `evidence_ref` cannot be resolved
again from that terminal Execution history.

No DeepSeek, other LLM, Builder, Mind routing, Directive, Intention or
Execution steering was used.

## 1. Single variable and source result

```text
synthetic Reality Evidence
→ Execution-derived Reality Evidence
```

The source audit found the durable pre-outcome boundary in the supported
Execution path:

```text
EXECUTION_STARTED
→ MODEL_DECISION(Wait)
→ ROOT_WAITING (append + flush + fsync)
→ run_goal()/deliver_event() returns waiting
```

The later terminal path is:

```text
EXTERNAL_EVENT_RECEIVED
→ ROOT_WOKEN
→ MODEL_DECISION
→ COMPLETION_CLAIMED
→ COMPLETION_VERIFIED
→ EXECUTION_COMPLETED
```

or a causally linked `EXECUTION_FAILED`. Therefore `ROOT_WAITING` is a real
pre-outcome fact; it is not reconstructed from `ExecutionResult`.

Stable provenance comes from `ExecutionEvent.event_id`, event sequence and
`source_event_refs`. EventLog persistence and load validation already provide
durable ordering and restart reconstruction. No AgentProcess decision-path
change was needed.

## 2. Implemented surface

Execution owns one public read-only seam:

```python
ExecutionOrgan.reality_evidence(*, after_sequence: int = 0)
    -> tuple[RealityEvidence, ...]
```

`RealityEvidence` is frozen and can only be constructed with an
Execution-private owner token. Its payload is a fixed-schema immutable scalar
mapping. It carries no EventLog, DecisionFrame, Runtime, IPython, ToolHost,
callback, closure or parent object.

Frozen bounds:

```text
max evidence items = 8
max payload chars  = 128
max source refs    = 16
ordering           = ascending Execution event sequence
cursor             = exclusive after_sequence
```

Projected events:

```text
ROOT_WAITING                   → PRE_OUTCOME
EXECUTION_COMPLETED / FAILED   → OUTCOME
```

For a terminal lineage longer than 16 refs, the bounded projection reserves
the first causally ancestral `ROOT_WAITING` ref and fills the remaining slots
from the newest refs. A five-wait, 25-event regression confirms the selected
PRE remains explicitly pairable with OUTCOME.

The thin Mind experiment adapter reuses the unchanged W0
`WorldModelTrace`, `Prediction`, `EnvironmentObservation` and comparator. The
accepted predictor is the exact frozen `ScriptedTerminalPredictor` type with
primitive fields only. A duck-typed predictor holding `ExecutionOrgan` is
rejected before its method can run.

The reusable adapter has no durable commit function and imports no
`ExecutionOrgan`. Durable prediction append exists only inline in the
single-thread deterministic campaign, which creates and exclusively owns its
organ. Its fixed order is PRE projection → prediction build → W0 `fsync` →
unchanged PRE assertion → wake delivery → OUTCOME. Therefore no supported
commit path accepts a retained PRE or exposes a concurrent owner handle.

## 3. Deterministic campaign artifact

Artifact: `Mind/fixtures/shadow_s0/result.json`

SHA-256:

```text
db26365daabdc9c6f07661ad37d02eaac8bdda7f6bcd260329bc37dd2c4618e1
```

The experiment host supplies a campaign-local deterministic UUID sequence;
production ID generation and runtime semantics are untouched. A focused test
runs two fresh campaigns and asserts canonical JSON byte equivalence.

| Case | Expected | Observed | PRE seq | OUTCOME seq | Prediction durable first |
|---|---|---|---:|---:|---|
| matched | `MATCHED` | `MATCHED` | 3 | 9 | yes |
| error | `ERROR` | `ERROR` | 3 | 9 | yes |
| unverifiable | `UNVERIFIABLE` | `UNVERIFIABLE` | 3 | 10 | yes |

For each case, PRE was obtained while the execution was waiting and no OUTCOME
was visible. The W0 prediction record was flushed and `fsync`ed before the
host delivered the wake event. Pairing stores prediction, PRE and OUTCOME refs
plus bounded causal refs explicitly.

The natural UNVERIFIABLE case used two valid waits followed by the deterministic
decision limit. It did not corrupt Execution or fabricate a malformed event.

## 4. Non-interference, failure and authority

The shadow OFF and ON normalized Execution observables were identical:

```text
OFF SHA-256 = 6c18250a10e17b0cedf07f780b54a19e6e3add60ab9e5fbcd18e7c79da90c48e
ON  SHA-256 = 6c18250a10e17b0cedf07f780b54a19e6e3add60ab9e5fbcd18e7c79da90c48e
```

Compared facts were terminal result/failure, decision count, action types,
Execution event semantics and workspace effect hash. The only candidate-only
effect was the separate W0 shadow trace.

Injected prediction failure produced `RuntimeError`; Execution still completed
with no failure. Projection, trace-create and comparison errors were likewise
isolated from Execution semantics.

The accepted predictor has none of:

```text
run_goal / resume / interrupt / deliver_event / open_child
IPython / ToolHost / shell / filesystem / network
decision_advisory / Directive / Intention / Focus
```

The experiment adapter imports only the public `RealityEvidence` DTO from
Execution. The separate host owns lifecycle operations and never passes the
owner to the predictor.

## 5. Redaction and provider disposition

Synthetic internal sentinels covered provider body, raw response/code, file
content and an absolute workspace path. None appeared in projected evidence.
The projector copies no arbitrary event payload, goal, external data, tool
result or mutable object.

```text
provider_request_count = 0
DeepSeek calls         = 0
other LLM calls        = 0
```

## 6. Why the verdict is INCONCLUSIVE

At a waiting-state restart, PRE projection is equivalent. At a terminal-state
restart, OUTCOME projection is equivalent. W0 trace replay is equivalent.

However, terminal history deliberately suppresses historical PRE facts to
reject this adversarial path:

```text
finish Execution
→ read terminal projection
→ construct a new PRE prediction
```

That means the original PRE `evidence_ref` is not owner-resolvable from the
later terminal history. A copied PRE in the separate shadow trace does not
satisfy the task's stronger “Execution-owned replayable ref” requirement.

Fixing both properties requires an additional ordering/validity seam, such as
a durable cross-log prediction receipt or distinct historical-versus-eligible
projection semantics. Either would be a second mechanism beyond S0's single
variable. Per the task card and Ponytail rule, S0 does not add it merely to
obtain `PASS`.

## 7. Required A–L answers

### A. Was Reality Evidence truly Execution-derived?

**Yes.** Execution projected both kinds from its owned durable EventLog; Mind
did not parse internal events.

### B. Was pre-outcome evidence available before outcome?

**Yes.** Durable `ROOT_WAITING` was projected while no terminal event existed.

### C. Was prediction durably committed before outcome existed?

**Yes for the frozen campaign.** The sole W0 prediction event was `fsync`ed
before the wake call that could create OUTCOME.

### D. Was Reality projection bounded and redacted?

**Yes.** The 8-item / 128-character / 16-ref bounds and all sentinel checks
passed.

### E. Were source/evidence refs stable and explicit?

**Partly.** Refs and causal pairing were stable and explicit, including a
greater-than-16-lineage test. The PRE ref was not resolvable after the history
became terminal.

### F. Could the existing comparator resolve real Execution evidence?

**Yes.** It produced `MATCHED`, `ERROR` and `UNVERIFIABLE` without a second
comparator.

### G. Was replay deterministic?

**Partly.** Waiting PRE, terminal OUTCOME, W0 trace and complete campaign bytes
were deterministic. PRE-by-ref replay from terminal history was unavailable.

### H. Was shadow ON/OFF behavior identical?

**Yes.** The normalized observable documents and hashes were equal.

### I. Did shadow failure remain fail-soft?

**Yes.** Injected shadow failures did not block, retry or reclassify Execution.

### J. Did World Model obtain any action authority?

**No.** The accepted concrete predictor receives only an immutable DTO and
primitive configuration; owner-capturing duck types are rejected before call.

### K. Were any provider calls made?

**No.** Provider request count was zero.

### L. Is S0 sufficient to proceed to S1?

**No.** The terminal PRE replay/eligibility contract must be resolved in a
separately approved, single-variable task before S1.

## 8. Validation

```text
focused S0 + Execution + W0: 36 passed
root regression:             342 passed, 24 skipped
Mind full suite:             417 passed
Python compilation:          passed
```

The Mind suite required a workspace-local pytest basetemp because the default
system pytest temp root returned a Windows permission error. The isolated rerun
completed successfully; this was environment noise, not a code failure.

Required final code-review axes:

```text
SPEC                    PASS
AUTHORITY               PASS
ANTI-HINDSIGHT          PASS (frozen single-thread campaign scope)
ARCHITECTURE / Ponytail PASS
```

## Final verdict

```text
S0_INCONCLUSIVE
EXECUTION_EVIDENCE_PLUMBING_AND_SAFETY_SUPPORTED
TERMINAL_PRE_REF_REPLAY_NOT_SUPPORTED
DO_NOT_PROCEED_TO_S1
NOT_PRODUCTION_WORLD_MODEL
```
