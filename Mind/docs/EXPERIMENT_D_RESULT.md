# Mind Experiment D — Durable One-shot Directive Delivery

## Mandatory AVO source audit

### Sources and revision

Primary sources inspected on 2026-08-30:

- NVIDIA paper: Terry Chen et al., [*AVO: Agentic Variation Operators for Autonomous Evolutionary Search*, arXiv:2603.24517v1](https://arxiv.org/abs/2603.24517v1), submitted 2026-03-25. The relevant original mechanism is §3.3, [Continuous Evolution](https://arxiv.org/html/2603.24517v1#S3.SS3); §4.1 identifies the variation operator as an internally developed general-purpose coding agent.
- Mechanism reproduction: [`gatordevin/avo`](https://github.com/gatordevin/avo) `main` resolved by a read-only remote-ref lookup to exact commit [`f6dad9e639d3c9e5d5ac9ccacbe076d82cfa39d2`](https://github.com/gatordevin/avo/commit/f6dad9e639d3c9e5d5ac9ccacbe076d82cfa39d2). Inspected commit-pinned [`README.md`](https://github.com/gatordevin/avo/blob/f6dad9e639d3c9e5d5ac9ccacbe076d82cfa39d2/README.md), [`src/avo/prompts.py`](https://github.com/gatordevin/avo/blob/f6dad9e639d3c9e5d5ac9ccacbe076d82cfa39d2/src/avo/prompts.py), [`src/avo/session.py`](https://github.com/gatordevin/avo/blob/f6dad9e639d3c9e5d5ac9ccacbe076d82cfa39d2/src/avo/session.py), [`src/avo/loop.py`](https://github.com/gatordevin/avo/blob/f6dad9e639d3c9e5d5ac9ccacbe076d82cfa39d2/src/avo/loop.py), and [`src/avo/run.py`](https://github.com/gatordevin/avo/blob/f6dad9e639d3c9e5d5ac9ccacbe076d82cfa39d2/src/avo/run.py). The repository is Apache-2.0 licensed.

These sources have different authority. The paper is the NVIDIA-authored primary source for AVO. It says that conditional self-supervision detects a stalled or unproductive search, reviews the overall trajectory, and steers exploration toward several candidate directions (§3.3). It does **not** publish the internal NVIDIA agent runtime or specify a durable one-shot delivery protocol. `gatordevin/avo` explicitly calls itself an open, independent reproduction and says it is not affiliated with or endorsed by NVIDIA. Its source is therefore evidence for one concrete reproduction of delivery semantics, not NVIDIA's official runtime source.

### Exact reproduction symbols and behavior

| File / symbol | Actual behavior at the pinned revision | Experiment D relevance |
|---|---|---|
| `prompts.py::build_supervisor_prompt` | Gives the supervisor a trajectory-review role and explicitly says it does not write code. It asks for several different directions grounded in observed trajectory evidence. | Supports a cognitive supervisor producing advisory redirect content, separate from the actor. |
| `prompts.py::build_variation_prompt` | When `supervisor_note` is present, inserts one `Supervisor intervention` section into the variation prompt. Its authority is stated precisely: treat the redirect as a **strong prior, not an order** because the acting agent has fresher profiling data. | Borrow the advisory precedence rule, not its optimization-specific prompt or concrete action directions. |
| `session.py::SessionState.supervisor_note` | Persists the pending note in mutable `session.json`; the setter overwrites the file. `record_supervisor()` also retains a bounded history entry. A malformed state file is silently replaced by empty state. | Confirms a pending-delivery concept, but its overwrite and fail-empty persistence are unsuitable for Lumina's append-only/conservative trace contract. |
| `session.py::record_supervisor` | Rejects an empty note, stores it, records history, and reports `applies_to_step=run.next_step()`. | Concrete evidence for targeting the next eligible step. |
| `session.py::variation_prompt` | Reads the pending note into the next prompt and returns `supervisor_note_applied`. Merely asking for the prompt does not clear the note. | Shows projection/application separately from eventual consumption; Lumina must define its own crash-safe claim/apply boundary. |
| `session.py::submit` | Copies the note into the accepted or rejected step record, then clears it after that step. The comment states that a note is consumed by exactly one step. | Concrete one-shot behavior: one pending redirect can influence one completed step, regardless of acceptance or rejection. It is not an event-sourced atomic protocol. |
| `loop.py::evolve` | On stagnation and only when no note is already pending, invokes the supervisor, saves the note, passes it into the next variation prompt, records it with the accepted/rejected trajectory item, then clears it. | Independently confirms pending → next-step inclusion → one-step clearing in unattended mode. |
| `loop.py::_run_supervisor` | Runs the supervisor and then reverts the lineage work tree to ensure supervisor-side edits do not survive. | Shows intent to keep supervision non-acting, but revert-after-write is not a structural read-only authority boundary and must not be copied. |
| `run.py::Run.variation_prompt` | Passes the optional note through to `build_variation_prompt`. | The actual delivery point is model-visible context construction, not mutation of the actor's goal. |
| `run.py::Run.steps_since_best` | Derives the stagnation trigger from consecutive non-improving trajectory records. | Out of scope: Experiment D supplies the Directive and tests delivery, not trigger quality. |

A subtle but important distinction is that this reproduction consumes after `submit`, not at the first prompt projection. Repeated calls to `session.variation_prompt()` before submission can render the same note again. Therefore its code does not itself prove crash-safe issue/application or idempotent retry. Lumina establishes those properties for host-serialized delivery through its own event model and tests; simultaneous multi-writer arbitration is explicitly not claimed.

### Mechanism borrowed for Lumina

Experiment D borrows only this narrow semantic shape:

```text
supervisor semantic redirect
→ durable pending state
→ next eligible actor decision context
→ advisory strong prior, not an order
→ one-shot consumption with an auditable application fact
```

The adaptation is deliberately stricter than the reproduction where Lumina's existing contracts require it:

- Mind emits semantic guidance; it does not edit, execute, or mutate Intention.
- Execution context construction is the delivery boundary; the Directive is data, never a callable capability.
- `ISSUED`, `APPLIED`, and consumption must be reconstructible from Lumina's append-only facts and must fail conservatively after corruption or interrupted writes.
- A Directive may steer one eligible decision while Execution retains authority over concrete plans and actions.

### Mechanisms explicitly not borrowed

The following AVO mechanisms are irrelevant or conflict with the Experiment D boundary and are not imported:

- stagnation-window detection, supervisor triggering, and continuous autonomous scheduling;
- evolutionary search, populations/archives, lineage sampling, git commits, score vectors, correctness gates, acceptance/rejection policy, and trajectory optimization;
- the full coding-agent loop, shell/filesystem authority, persistent model conversation, agent backends, and same-session role switching;
- optimization-specific prompts that demand concrete implementation directions—the Lumina Mind must not become Planner;
- mutable overwrite-based `session.json`, silent corruption recovery, and the reproduction's non-atomic read/render/submit/clear sequence;
- revert-after-supervisor execution as an authority boundary; Experiment B's structural no-shell/no-filesystem/no-IPython boundary remains authoritative.

Source conclusion: AVO supports the **advisory, next-step, one-shot redirect concept**. It does not supply a production-ready durable delivery protocol for Lumina, and no AVO persistence, trigger, actor, or evolutionary machinery should be copied.

## Hypothesis and single variable

Hypothesis:

> The existing append-only Mind Trace can support a durable, restart-safe, decision-scoped, one-shot Directive lifecycle without modifying Execution, Intention, or the bounded cognitive loop.

The single changed mechanism is **Directive delivery lifecycle**. The following were held fixed:

- Experiment A's two-model-call / one-cognitive-capability bounds and strict JSON protocol;
- Experiment B's data-only `ExecutionObservation` authority boundary;
- Experiment C's append-only JSONL trace and deterministic cognitive request projector;
- `ModelClient`, `MemoryRetriever`, Memory behavior, and Directive semantic text;
- NoChange and DecisionIntent behavior;
- production Mind Recall gate behavior.

No trigger policy, scheduler, Nervous/Focus behavior, Intention semantics, Memory behavior, or real Execution path was changed.

## Baseline and candidate

Baseline (Experiment C):

```text
model Directive
→ ACTIVATION_FINISHED({type: directive, text})
→ durable but inert final semantic result
```

Candidate (Experiment D):

```text
model Directive
→ MIND_DIRECTIVE_ISSUED({directive_id, text})
→ derived PENDING
→ caller supplies stable decision_id + eligible
→ MIND_DIRECTIVE_APPLIED({directive_id, decision_id})
→ derived CONSUMED
```

`MIND_DIRECTIVE_ISSUED` is the Directive's sole legal semantic terminal event. There is no `ACTIVATION_FINISHED(Directive) → ISSUE` second write. Existing NoChange and DecisionIntent continue to use `ACTIVATION_FINISHED`. The validator deliberately rejects the old Experiment C `ACTIVATION_FINISHED(Directive)` shape: accepting it would preserve exactly the legal "final Directive without pending delivery" state that Experiment D must eliminate. Experiment C's reconstruction claim remains covered through ISSUE, but ad-hoc pre-D Directive JSONL artifacts are not forward-readable. No production path persisted or consumed these experimental traces.

## Codebase-design decision

Three concrete designs were compared before tests:

1. put event grammar, state projection, preparation, and model framing entirely in `trace.py`;
2. expose a rich Directive projection DTO plus a separate delivery module;
3. keep event validity/durability/replay in `trace.py`, while a small `directive.py` owns derived delivery semantics and model-facing advisory framing.

The candidate uses option 3. This keeps the deep boundaries local:

- `trace.py` owns legal historical facts, causal references, append + flush + `fsync`, corruption rejection, and cognitive replay;
- `directive.py` owns the pure pending/consumed fold, the single caller operation, and `DirectiveApplication` rendering;
- `experiment_a.py` only changes Directive finalization to emit the single durable ISSUE terminal.

`MindTrace.reopen()` remains read-only. `MindTrace.reopen_for_delivery()` is an explicit, narrow append-capable seam that accepts only a cognitively complete issued/consumed Directive trace. It does not resume cognition or Execution. No store, manager, queue, registry, state-machine framework, scheduler, or event bus was added.

## Exact files and symbols

| File | Symbol / change | Responsibility |
|---|---|---|
| `Mind/trace.py` | `MIND_DIRECTIVE_ISSUED`, `MIND_DIRECTIVE_APPLIED` | The only two new event types. |
| `Mind/trace.py` | `MindTrace.reopen_for_delivery()` | Reopens only an issued or consumed Directive trace for the delivery phase. |
| `Mind/trace.py` | `MindTrace._refresh_for_delivery()` | Refreshes the durable tail before each host-serialized decision so a pre-opened stale handle cannot bind a second decision. |
| `Mind/trace.py` | `_validate_sequence()` and Directive validators | Closed lifecycle grammar, exact payload fields, deterministic ID, exact causal refs, bounded decision ID, and no event after consumption. |
| `Mind/trace.py` | `directive_id_for()` | Derives `activation_id + ":directive:" + issuing_seq`; no random ID service. |
| `Mind/trace.py` | `replay_activation()` | Treats ISSUE as the cognitive Directive terminal and ignores APPLIED for reconstructed model calls/result/observations. |
| `Mind/experiment_a.py` | `_record_terminal()` | Emits ISSUE instead of FINISHED for a Directive and returns the Directive only after durable append succeeds. |
| `Mind/directive.py` | `DirectiveState` | Derived `PENDING` / `CONSUMED` values only; no persisted status flag. |
| `Mind/directive.py` | `DirectiveApplication` | Frozen `{directive_id, decision_id, text}` DTO plus fixed advisory rendering. |
| `Mind/directive.py` | `project_directive_state()` | Pure fold over a fully validated trace. |
| `Mind/directive.py` | `prepare_for_execution_decision()` | The sole delivery operation: no-op, durable first binding, or same-decision reconstruction. |
| `Mind/test_directive.py` | D1–D11 tests | Durable lifecycle, restart, idempotence, corruption, provenance, framing, and authority checks. |

No file under `Execution/`, `Execution_lab2/`, `core/`, Memory, Dream, or the production API path was modified by Experiment D.

## Directive event and derived-state model

The legal candidate extension is:

```text
after final model output
├─ ACTIVATION_FINISHED(NoChange | DecisionIntent)
├─ ACTIVATION_FAILED
└─ MIND_DIRECTIVE_ISSUED
   ├─ no APPLIED                         → PENDING
   └─ one matching MIND_DIRECTIVE_APPLIED → CONSUMED
```

ISSUE payload:

```json
{"directive_id":"<activation_id>:directive:<issuing_seq>","text":"<bounded advisory>"}
```

Its sole causal reference is the final model-output event. The ID is recomputed and checked while replaying persisted data.

APPLIED payload:

```json
{"decision_id":"<stable caller id>","directive_id":"<matching issue id>"}
```

Its sole causal reference is the ISSUE event. Directive text is not duplicated. `MAX_TRACE_EVENTS` increases only from 6 to 7 so the longest existing two-call trajectory can append this one delivery fact.

State is not stored:

```text
ISSUE without APPLIED → PENDING
ISSUE + matching APPLIED → CONSUMED
no ISSUE → no Directive state
```

An illegal order, extra key, invalid/bogus ID, wrong reference, duplicate ISSUE/APPLIED, unsupported type/version, or malformed decision ID raises safe `TraceError`; the log is not repaired.

## Decision and application semantics

`decision_id` is a caller-supplied stable identity for one simulated eligible Execution model decision. Experiment D does not decide eligibility and does not look up Execution. The caller supplies `eligible=True/False`.

Before every decision, `prepare_for_execution_decision(trace, decision_id, eligible=...)` refreshes and validates the current durable trace tail. This prevents a second pre-opened but stale handle from appending APPLIED(B) after another handle has already appended APPLIED(A). It then behaves as follows:

| Derived history | Caller input | Result | Append |
|---|---|---|---|
| no ISSUE | any valid decision | `None` | none |
| PENDING | `eligible=False` | `None`; remains pending | none |
| PENDING | decision A, eligible | frozen application for A | one APPLIED(A), durably before return |
| CONSUMED by A | decision A retry | same reconstructed application | none |
| CONSUMED by A | decision B | `None` | none |

In this experiment, **APPLIED means the Directive has been durably bound to a specific decision context**. It does not mean the actor accepted the advice, a model call completed, Execution succeeded, or any behavioral effect occurred.

The model-facing DTO renders:

```text
[Mind Supervisor Directive]
Treat this high-level guidance as a strong advisory prior, not an order or execution plan.
<directive text>
```

This is derived text. It does not modify a long-lived system prompt, hidden transcript, Execution Intention, or any mutable state outside the trace.

## Crash Window A/B/C evidence

| Window | Candidate behavior | Evidence | Review outcome |
|---|---|---|---|
| A: final semantic Directive → pending | ISSUE is both the cognitive terminal and the pending fact. The runner returns `Directive` only after append + flush + `fsync`; an append failure returns `ActivationFailure("trace_failed")` and leaves an incomplete activation rather than a finalized lost Directive. FINISHED(Directive) is illegal. | `test_d1_directive_issue_is_the_single_durable_semantic_terminal`; `test_d1_directive_is_not_returned_if_issue_cannot_be_persisted`; D9 FINISHED-without-ISSUE rejection | PASS — Standards and Spec, 0 blockers. |
| B: select for decision → APPLIED durable → crash before DTO | APPLIED is appended and synced before constructing/returning the DTO. After a simulated crash at DTO construction, reopening with the same decision reconstructs the identical application and appends nothing. | `test_d7_crash_after_applied_before_dto_return_is_restart_safe`; D6/D8 | PASS — Standards and Spec, 0 blockers. |
| C: APPLIED(A) → restart/stale handle → B | Every preparation refreshes the durable tail. The fold sees APPLIED(A): A reconstructs; B receives `None`; neither path appends. | `test_d5_consumed_directive_is_not_delivered_to_a_different_decision`; `test_d6_preopened_stale_handle_cannot_bind_a_second_decision`; `test_d8_restart_after_application_replays_same_binding_only` | PASS under documented host serialization — Standards and Spec, 0 blockers. |

The raw provider response in `MODEL_OUTPUT_RECORDED` is historical model output, not an authorized final semantic Directive. Semantic finalization occurs at the single ISSUE append. This is what closes Window A without pretending that two durable writes are atomic.

## Corruption and causal provenance

D9 covers all required illegal transitions plus the deterministic ISSUE ID itself:

- APPLIED without ISSUE;
- incorrect deterministic ISSUE ID;
- APPLIED with a wrong Directive ID;
- APPLIED with a wrong ISSUE reference;
- duplicate APPLIED for a different decision;
- duplicate ISSUE;
- legacy FINISHED(Directive) without ISSUE;
- invalid caller and persisted decision IDs;
- extra ISSUE or APPLIED payload keys;
- unknown event type or trace version.

Each case fails conservative and preserves the corrupt bytes unchanged.

For a direct one-call activation, D10 reconstructs:

```text
MODEL_OUTPUT_RECORDED(seq=1)
→ MIND_DIRECTIVE_ISSUED(seq=2, refs=[1])
→ MIND_DIRECTIVE_APPLIED(seq=3, refs=[2], decision_id=decision-A)
```

A second D10 test exercises the longest two-model-call + Memory observation trajectory. Adding APPLIED changes only the final causal-chain item; reconstructed call #1, call #2, model outputs, capability request, observation, and final Directive remain exactly equal before and after delivery. Thus delivery does not become a second cognitive Context authority.

## Authority boundary

The D implementation receives only:

```text
MindTrace
stable decision_id
caller-owned eligible bool
```

It imports no Execution package and exposes no `set_intention`, `revise_intention`, `pause`, `resume`, `run_goal`, `interrupt`, actor, tool, shell, filesystem-capability, or IPython operation. The only filesystem effect is the host-owned trace append already established by Experiment C. `DirectiveApplication` is inert data with one pure rendering method.

Static D11 checks scan both `directive.py` and `trace.py` for Execution dependencies and authority-bearing methods. Experiment B's existing AST/data-only authority tests remain green.

## Required skills and development trajectory

### `/codebase-design`

The deepening and Design-It-Twice guidance was applied before tests. Three parallel interface designs were compared. The selected design preserves Trace as the durability/validity authority and adds only one small lifecycle module for derived semantics and framing. A new store was rejected because ISSUE/APPLIED already determine state.

### `/tdd`

Initial RED:

```text
ERROR collecting Mind/test_directive.py
ImportError: cannot import name 'directive' from 'Mind'
```

The first GREEN was `29 passed`. A second RED/GREEN micro-cycle removed an authority-ambiguous `resume` method name in favor of `reopen_for_delivery`. Additional deterministic-ID and two-call replay coverage brought D to `31 passed`. Mandatory review then reproduced two blocking cases—stale pre-opened handles and legal FINISHED(Directive)—which were captured as failing tests before the durable-tail refresh and closed grammar brought D to `33 passed`.

### Ponytail

Applicable rules were followed: inspect sources before invention, freeze the failure seam before coding, implement the smallest vertical slice, avoid speculative abstractions, and validate before promotion. The result has two new events, one DTO, one enum, two public lifecycle functions, no external dependency, and no new framework or store.

### `/code-review`

Independent Standards and Spec reviews were run in parallel after implementation. The Spec review reproduced two blocking cases that the initial tests missed:

1. two handles opened while pending could each use a stale cached prefix and bind different decisions;
2. `_validate_finished()` still allowed a Directive to be legally finalized without ISSUE.

Both findings were converted into RED tests. The candidate now refreshes durable facts before every preparation, and FINISHED(Directive) is illegal. Re-review results:

```text
Standards: PASS — 0 blocking implementation findings
Spec:      PASS — 0 blocking implementation findings
Window A: PASS
Window B: PASS
Window C: PASS under host-serialized delivery calls
```

The reviewers agreed that true simultaneous multi-writer arbitration remains outside the explicit Experiment D claim and should not cause a lock, queue, or database to be added.

## Acceptance evidence

| Case | Evidence |
|---|---|
| D1 | Single durable semantic ISSUE, reopen → PENDING, same semantic Directive; ISSUE failure returns no Directive. |
| D2 | NoChange and DecisionIntent use FINISHED and project no Directive state. |
| D3 | Ineligible call returns no application, appends nothing, remains pending. |
| D4 | First eligible decision gets a frozen DTO after one APPLIED and state becomes consumed. |
| D5 | A different later decision gets no DTO and no append. |
| D6 | Same decision retry gets an equal DTO and no append; a stale pre-opened handle cannot bind a different decision. |
| D7 | Pending survives object discard/reopen and applies normally. |
| D8 | APPLIED survives object discard; same decision reconstructs, different decision is excluded. |
| D9 | Required corrupt/illegal histories and invalid caller inputs fail conservative without repair. |
| D10 | Exact model → ISSUE → APPLIED causal refs plus decision ID; cognitive replay unchanged after APPLIED. |
| D11 | No Intention mutation, Execution authority, or real Execution dependency. |
| D12 | Experiment A/B, C, production Mind gate, and root regression commands are listed below. |

## Validation

All commands below were rerun after the review fixes:

```text
.venv\Scripts\python.exe -m pytest Mind\test_directive.py -q
33 passed

.venv\Scripts\python.exe -m pytest Mind\test_trace.py -q
26 passed

.venv\Scripts\python.exe -m pytest Mind\test_experiment_a.py -q
46 passed

.venv\Scripts\python.exe -m pytest Mind -q
105 passed

.venv\Scripts\python.exe -m pytest \
  tests\test_mind_gate.py \
  tests\test_mind_llm_gate.py \
  tests\test_mind_gate_shadow.py \
  tests\test_mind_gate_operational.py \
  tests\test_mind_promotion_controls.py -q
24 passed

.venv\Scripts\python.exe -m pytest -q
338 passed, 24 skipped, 2 existing upstream warnings

.venv\Scripts\python.exe -m py_compile \
  Mind\trace.py Mind\experiment_a.py Mind\directive.py Mind\test_directive.py
PASS

git diff --check
PASS (exit 0; only existing LF→CRLF working-copy warnings)

git status --short
captured; repository remains dirty from the pre-task baseline
```

Because the pre-existing `Mind/` experiment tree is untracked in the repository baseline, normal `git diff --check` cannot inspect those files. An explicit trailing-whitespace scan over all five task files returned no matches. The task wrote only:

```text
Mind/directive.py                         new
Mind/test_directive.py                    new
Mind/docs/EXPERIMENT_D_RESULT.md          new
Mind/trace.py                             modified
Mind/experiment_a.py                      modified
```

No task-caused production-code diff exists outside `Mind/`. No commit, push, rebase, or reset was performed.

## Limitations and non-claims

- This proves one Directive from one activation binding to at most one distinct caller-named decision under host-serialized delivery calls, including sequential calls through handles opened before the first binding.
- Callers are assumed to serialize delivery access. Concurrent/multi-writer claiming is not tested or claimed; no lock or arbitration machinery was added.
- Multiple pending Directive arbitration, priority, TTL, cancellation, replacement, and actor targeting remain out of scope.
- Eligibility and trigger policy are supplied externally; no Nervous, Focus, stagnation detector, or scheduler exists here.
- `reopen_for_delivery()` resumes only the trace delivery phase; cognition itself is not resumed.
- Append + flush + `fsync` is the inherited Experiment C durability boundary. It does not prove filesystem power-loss atomicity; a torn/partial record is rejected conservatively on replay rather than repaired.
- Pre-D experimental traces ending in `ACTIVATION_FINISHED(Directive)` are rejected so they cannot create a final-but-never-pending Directive state.
- APPLIED proves durable decision-context binding, not exactly-once Execution model invocation, actor compliance, task success, or behavioral value.
- No real Execution integration or production Mind-to-Execution steering exists.
- DecisionIntent remains inert and Intention remains unchanged.

## Verdict

```text
EXPERIMENT_D_PASS
```

Permitted claim:

> **Mind Experiment D has shown that one high-level Directive can be durably issued as an append-only historical fact, remain pending across restart, and bind to only one eligible Execution decision under host-serialized delivery; the same decision can deterministically recover the application, while later different decisions cannot consume it again.**

This does not claim production Mind→Execution steering, exactly-once Execution model invocation, behavioral improvement, Nervous completion, or Intention control.
