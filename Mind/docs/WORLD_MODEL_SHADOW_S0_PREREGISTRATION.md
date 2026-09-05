# World Model Shadow Integration S0 — Preregistration

**Status:** REFROZEN BEFORE THE REVIEW-CORRECTED DETERMINISTIC CAMPAIGN

Earlier generated artifacts were invalidated during the required final code
review: generated Execution UUIDs made them non-reproducible; bounded lineage
could lose or misorder the selected PRE ref; a reusable durable commit helper
allowed retained-PRE and concurrent ordering attacks; and terminal history
could not replay the earlier PRE evidence by ref. No claim from those artifacts
is retained. This amendment freezes the corrected code and the
`S0_INCONCLUSIVE` replay rule before the final replacement campaign.

**Experiment:** Execution-derived Reality Evidence Projection

**Provider calls authorized:** 0

## 1. Hypothesis and single variable

Hypothesis:

> Execution can project owner-controlled, bounded, redacted and replayable facts so a zero-authority shadow predictor can durably commit before a later outcome, compare through the existing W0 mechanism, and have no behavioral effect on Execution.

Single variable:

```text
synthetic Reality Evidence
→ Execution-derived Reality Evidence
```

Frozen outside the variable: deterministic scripted model, W0 prediction/observation/comparison semantics, no Builder, no LLM, no Mind routing, no steering, no automatic trigger, no terminal-protocol change.

## 2. Source audit

### Facts available before outcome

The actual `Execution/execution.py` chronology appends and `fsync`s:

```text
EXECUTION_STARTED
→ MODEL_DECISION(Wait)
→ ROOT_WAITING
```

`RootAgentProcess._drive()` returns after `ROOT_WAITING`, so the supported `ExecutionOrgan.run_goal()` caller can obtain a durable waiting state before any terminal event exists. This is the S0 `PRE_OUTCOME` fact.

### Facts available only after outcome

After the host delivers the matching wake event, Execution appends:

```text
EXTERNAL_EVENT_RECEIVED
→ ROOT_WOKEN
→ later MODEL_DECISION
→ COMPLETION_CLAIMED
→ COMPLETION_VERIFIED
→ EXECUTION_COMPLETED
```

or a causally linked `EXECUTION_FAILED`. These terminal facts do not exist on the pre-outcome projection surface.

### Stable provenance and replay

- `ExecutionEvent.event_id` is the durable contiguous identity `event-NNNNNN`.
- `ExecutionEvent.source_event_refs` points to immutable earlier causes.
- `EventLog._persist()` flushes and `fsync`s each canonical event.
- `EventLog.load()` validates identity, sequence, schema and causal ordering.
- `ExecutionOrgan` already reopens the durable EventLog; no recovery framework is added.

### Existing seam and minimal addition

Before S0 there was no supported public read-only Reality Evidence projection. `ExecutionState` and `ExecutionResult` were public, but the only detailed facts were internal EventLog/DecisionFrame objects. S0 adds only:

```text
RealityEvidence (immutable, Execution-constructed DTO)
ExecutionOrgan.reality_evidence(after_sequence=0)
```

The projector remains Execution-owned. The predictor receives only `RealityEvidence`; it never receives `ExecutionOrgan`, EventLog, DecisionFrame, Runtime, IPython or ToolHost.

### Redacted internal fields

The projection does not copy arbitrary event payloads. It therefore excludes:

- `DecisionFrame.actual_request`, provider wire request, raw provider response and raw model response;
- IPython code and tool request content;
- absolute/relative paths and file content;
- tool output, errors, external-event data, goals and completion-spec content;
- environment variables, credentials and mutable handles.

## 3. Frozen projection schema and bounds

```text
RealityEvidence
- evidence_ref: stable Execution-owned ref
- execution_ref: durable execution identity
- source_event_refs: bounded causal lineage
- kind: PRE_OUTCOME | OUTCOME
- sequence: source Execution event ordering fact
- payload: immutable fixed-schema scalar mapping
```

Bounds:

```text
max_evidence_items_per_call = 8
max_payload_chars           = 128 canonical JSON characters
max_source_event_refs       = 16
ordering                    = ascending Execution event sequence
cursor                      = exclusive after_sequence
```

Only two source event classes are projected:

```text
ROOT_WAITING       → PRE_OUTCOME
EXECUTION_COMPLETED / EXECUTION_FAILED → OUTCOME
```

When history is terminal, historical PRE facts are no longer exposed. This prevents constructing a new pre-outcome prediction from a final result. Waiting-history reopen still produces byte/canonical-equivalent PRE evidence. Because the same PRE cannot then be resolved by ref from the later terminal history, the replacement campaign must report `S0_INCONCLUSIVE`; S0 will not add a lease, registry or cross-log receipt to force `S0_PASS`.

If a terminal causal lineage exceeds 16 refs, the projector reserves the first
causally ancestral `ROOT_WAITING` ref and fills the remaining bounded slots
from the newest causal refs. This preserves the S0 selected PRE-to-OUTCOME pair
without making the projection unbounded.

## 4. Frozen comparison target

Target:

```text
completion_verified
```

Mapping into the unchanged W0 integer observation:

```text
True  → 1
False → 0
missing / no completion-verification fact → None
```

W0 remains authoritative for:

```text
1 == expected → MATCHED
different      → ERROR
None           → UNVERIFIABLE
```

No second comparator or shadow trace format is introduced.

## 5. Frozen trajectories

### A — MATCHED

```text
Scripted predictor expects completion_verified=True
Wait("continue") → wake → ClaimComplete
actual completion_verified=True
expected: MATCHED
```

### B — ERROR

Same Execution trajectory, but predictor expects `False`.

```text
expected: ERROR
```

### C — UNVERIFIABLE

```text
Wait("first") → wake → Wait("second") → wake
→ deterministic decision_limit_reached
```

The terminal failure is legitimate Execution behavior, but no completion-verification fact exists, so the frozen target is unavailable.

```text
expected: UNVERIFIABLE
```

No malformed EventLog, corrupted Execution or provider failure is used to manufacture C.

## 6. Frozen invariants

### Anti-hindsight

Before prediction commit:

- projection contains exactly one PRE and no OUTCOME;
- W0 trace is created and the prediction event is flushed + `fsync`ed;
- the trace contains exactly one event before the wake is delivered.

Afterward:

- OUTCOME sequence must be greater than PRE sequence;
- PRE backing event must occur in the bounded OUTCOME causal lineage;
- terminal projection must not expose historical PRE.

### Explicit pairing

The comparison records:

```text
prediction_ref
pre_evidence_ref
outcome_evidence_ref
source_event_refs
comparison
```

Pairing by “latest outcome” without refs is forbidden.

### Non-interference

Control and shadow runs use the same scripted actions. After normalizing only generated execution identities, they must have the same:

- terminal status/output/failure and decision count;
- action types;
- Execution event type/sequence/causal-reference trajectory;
- workspace effect hash.

Only the shadow trace may differ.

### Fail-soft

Projection, predictor, trace-write or comparison failure must remain outside Execution semantics. The deterministic failure injection is the frozen predictor's boolean failure mode, which raises `RuntimeError`; Execution must still complete with no failure.

### Authority

The predictor object may hold only frozen scalar configuration: the expected boolean, model version and failure-injection boolean. The adapter accepts this exact concrete type only, before any predictor call. It rejects duck-typed objects that could capture an owner, callback, closure or bound method. The accepted predictor has no `run_goal`, `resume`, `interrupt`, event delivery, child, IPython, ToolHost, shell, filesystem, advisory, Directive, Intention or Focus capability.

The reusable adapter has no durable commit function and imports no
`ExecutionOrgan`. Durable append exists only inline in the deterministic
campaign function that creates and exclusively owns its local organ. Its fixed
program order is PRE projection → prediction build → W0 `fsync` → unchanged
PRE assertion → wake delivery → OUTCOME. There is no supported API that can
submit a retained PRE after completion.

### No provider

Only `ScriptedModel` is supplied to `ExecutionOrgan`.

```text
provider_request_count = 0
```

The campaign patches only the experiment process's UUID generator with a deterministic sequence. Production code and Execution semantics are unchanged; two fresh campaign runs must serialize byte-equivalent results.

## 7. Refrozen source hashes

SHA-256 immediately before the official deterministic campaign:

| Source | SHA-256 |
|---|---|
| `Execution/__init__.py` | `f587b24ee4eee2aa5e521aae8e77765e9ed8de4f5fcce1fced2981e9b6190ffc` |
| `Execution/organ.py` | `ae2d00b054a0766fbca325074a53ad83457a8f1d13b77c0e19f93f4e3c5814f0` |
| `Mind/world_model_experiment.py` | `7bb1b8cf4d7652cf424ed7607e477e00580cf1799d8b6db7184a0f453cd53348` |
| `Mind/world_model_shadow_s0.py` | `10e317c3c8f79bd179dda5983efbe19e151309495f80cd349ed55ca720157c12` |
| `Mind/world_model_shadow_s0_campaign.py` | `0931d3da4db2252a84e750ffd21d6c35815d7d66e76c614c17e512203858fcd9` |
| `Execution/test_execution_organ.py` | `7f4d01aa308a569d05e4c36db7ade924a41096ea34fba0ac3f1d4e15cee979a1` |
| `Mind/test_world_model_shadow_s0.py` | `7389279d048fc5dd03562fd9f87bd7920ea836664c3a7bf5b04854f781c8d544` |

## 8. Verdict rules

### `S0_PASS`

All must hold:

- Evidence is Execution-derived and immutable;
- PRE exists before terminal outcome;
- prediction is durably committed first;
- stable explicit causal pairing;
- MATCHED, ERROR and UNVERIFIABLE all resolve through W0;
- projection and shadow replay are deterministic across reopen;
- projection is bounded and redacted;
- shadow ON/OFF observables are identical;
- injected shadow failure is fail-soft;
- predictor has zero action authority;
- provider request count is zero.

### `S0_INCONCLUSIVE`

Safety holds but stable pairing, replay, natural UNVERIFIABLE or noise-free non-interference cannot be established.

### `S0_BLOCKED`

PRE cannot be obtained before outcome without final-result reconstruction, internal EventLog parsing by Mind, mutable owner leakage or Actor-loop modification.

### `S0_FAIL`

Any hindsight leakage, behavior change, authority leak, sensitive projection, wrong outcome linkage, provider call, Reality rewrite or shadow-caused Execution failure.
