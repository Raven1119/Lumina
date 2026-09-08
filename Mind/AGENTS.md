# AGENTS.md — Lumina Mind Workspace

## 1. Workspace role

This directory is the independent development and experiment workspace for the **Lumina Mind Organ**.

Paths in this file are relative to `Mind/`; `../` is the repository root.

Opening `Mind/` as the coding workspace does **not** create a filesystem sandbox.

Agents working here may read and, when the current task requires it, modify files elsewhere in the Lumina repository. Integration work may therefore touch Memory, Execution, `core/`, tests, or documentation outside `Mind/`.

Rules:

- default new experimental code belongs under `Mind/`;
- cross-directory changes must be directly required by the active task;
- do not modify unrelated repository areas;
- preserve existing production behavior unless the current task authorizes changing it;
- do not duplicate already-supported Memory or Execution capabilities.

---

## 2. Current entry points

Production Chat uses the earlier Mind Recall gate:

```text
LlmMindGate
→ MindDecision { recall: bool }
```

That gate is existing production behavior. It is not the new cognitive Mind architecture.

`organ.py` and `host.py` implement persistent cognition and event handling with
Nervous. The current working tree also has a separate `python -m Mind` entry
through `chain.py` connecting Mind, Nervous, and Execution. It is not production
Chat routing. Check the actual caller and relevant evidence before describing
any mechanism as experimental, supported, or promoted.

---

## 3. Task-relevant reading and authority

Follow `../AGENTS.md` and the current task. Use `../docs/CURRENT_STATUS.md`
to locate reported implementation and verify it against current source/tests.
For cognitive architecture, read `../docs/MIND_COGNITIVE_ARCHITECTURE.md`;
`../docs/MIND_DESIGN.md` supplies the earlier MVP contract, not a reason to
discard subsequent explicitly authorized design. For event integration, read
`../docs/NERVOUS_EVENT_FOUNDATION.md`; for the standalone chain, read
`docs/INTEGRATED_CHAIN.md` for current behavior and `docs/EXPERIMENT_HISTORY.md`
for condensed historical conclusions. Raw campaigns are not part of the published tree.

Keep proposed design, accepted decisions, working-tree behavior, and historical
results distinct. Record discrepancies rather than treating code as proof of
design correctness. Read only the material needed for the task. The North Star
remains direction, not authorization to expand scope.

---

## 4. Mind definition

Mind is Lumina's cognitive organ responsible for:

```text
acquire relevant information
→ process / integrate information
→ acquire more information when needed
→ form high-level judgment
→ output guidance or semantic decision intent
```

For the MVP, Mind is a **bounded cognitive agent**.

Its internal loop may be ReAct-like:

```text
reason
→ inspect / recall
→ observe
→ reason
→ inspect / recall
→ observe
→ decide
```

This is **Cognitive ReAct**, not Execution ReAct.

---

## 5. Structural boundaries

### Mind is not an Execution Actor

Mind must not become:

```text
Root Actor
Child Actor
AgentProcess
recursive spawn target
```

Execution Actors act in the world. Mind observes, interprets, and guides them from outside the recursive execution tree.

Do not implement `spawn_child("Mind")` or equivalent architecture.

### Mind is not a Planner

Valid Mind guidance:

```text
"The current direction depends on an assumption that has repeatedly failed.
Re-evaluate that assumption before continuing."
```

Mind should not normally produce:

```text
1. open file A
2. edit function B
3. run command C
4. commit change D
```

Concrete planning and execution belong to Execution.

### Mind is not Nervous

Mind determines semantic meaning and direction.

The existing Nervous event foundation owns mechanical delivery. Broader
attention/Focus design concerns include:

```text
wake
queue
defer
interrupt
Focus scheduling
```

Reuse the existing event foundation; extend Nervous or Focus only when the
current task includes that capability.

---

## 6. Core concepts

### Intention

`Intention` is the single authoritative overall direction currently pursued by Execution.

There must not be separate drifting Mind and Execution goal strings.

Mind may recommend revising Intention, but an Intention change must occur through an explicit code-owned state transition.

Natural-language guidance must never silently mutate authoritative Intention.

### Directive

A Directive is high-level advisory steering.

It:

- identifies what concern or direction matters;
- does not prescribe low-level tool steps;
- does not itself mutate Intention;
- is one-shot by default.

One-shot delivery semantics:

```text
MIND_DIRECTIVE_ISSUED
→ pending
→ next eligible Execution decision context
→ MIND_DIRECTIVE_APPLIED
→ consumed
```

Reuse `directive.py` and trusted-host `MindOrgan.prepare_directive` delivery.
Preserve eligibility, durable binding, and single consumption; an existing
delivery repair does not need to repeat the original experiment.

### DecisionIntent

A `DecisionIntent` means Mind concludes that system-level state should change.

Examples:

```text
revise Intention
suspend work
resume work
future Focus change
```

Mind emits semantic intent. Code-owned machinery performs the mutation.

### NoChange

`NoChange` is a first-class valid result.

Never force an intervention merely because Mind was activated.

---

## 7. MVP information and authority

The earlier MVP used Memory Recall and Execution Inspect. Current cognitive
work also uses correlated capability results delivered through Nervous and
bounded pure-computation projections. Follow the active caller contract;
calculation or simulation output is not evidence that an event occurred.

The model receives bounded projections and named cognitive capabilities.
Trusted hosts call the owning organs, persist cognitive/trace state, and apply
permitted decisions. Do not pass mutable owner handles to the model.

The runtime Mind model must not receive direct authority for:

```text
shell
filesystem mutation
IPython
process control
browser actions
Execution tool invocation
pause/resume mutation
direct Intention mutation
```

Authority must be enforced by capability exposure, not only prompt instructions.

### Development workspace authority

The **development agent** operating from `Mind/` may read and modify files anywhere under the Lumina repository when required by the active task.

This does not grant equivalent authority to the runtime Mind Organ.

Keep these separate:

```text
Codex/development workspace authority
!=
runtime Mind capability authority
```

---

## 8. Mind activation

One Mind activation is a bounded cognitive episode:

```text
important event
→ bounded MindContext
→ model reasoning
→ optional Memory Recall / Execution Inspect
→ observation
→ further reasoning if needed
→ NoChange / Directive / DecisionIntent
→ activation ends
```

A single activation may contain multiple model calls.

Do not model Mind as an indefinitely growing LLM conversation.

Cross-activation continuity should come from durable external state:

```text
current Intention
Mind Trace
Memory
relevant persisted organ state
```

Exact limits such as:

```text
max steps
max model calls
context budget
output budget
```

are experimental variables unless explicitly frozen by a task card.

---

## 9. Trace / State / Context

Preserve:

```text
Trace != State != Context
```

Target philosophy:

```text
append-only Mind EventLog
→ deterministic projection
→ bounded model-visible MindContext
→ model / capability interaction
→ append resulting events
```

A completed activation should allow reconstruction of:

```text
trigger
initial model-visible context
capability calls
capability results
subsequent model-visible context
final permitted model output
Directive / DecisionIntent / NoChange
later application/state transition
```

Do not store or require hidden chain-of-thought.

Maintain causal provenance from Mind decisions back to triggering/source events.

---

## 10. Failure semantics

Mind failure is conservative.

If Mind:

```text
times out
returns malformed output
cannot reach the provider
fails during capability use
```

then:

```text
current Intention remains unchanged
Execution may continue under the current Intention
no fallback redirect is invented
important unresolved event remains pending when applicable
failure is traceable
```

Mechanical user controls such as explicit stop must not depend on successful Mind reasoning.

---

## 11. Development method

Apply the root development workflow. Ordinary fixes use focused regression
tests; documentation changes use static checks. The following experiment rules
apply to new algorithms or architectural mechanisms:

Rules:

1. One mechanism / variable at a time.
2. Experiment before production promotion.
3. Use the smallest vertical slice capable of falsifying the hypothesis.
4. Do not add future-facing frameworks without a current caller.
5. Inspect suitable upstream implementations before inventing Lumina-specific equivalents.
6. A benchmark PASS proves only the tested claim.
7. Preserve failed experiments as evidence where useful.
8. After acceptance, promote only the minimal supported mechanism and remove obsolete duplicate scaffolding where appropriate.

Do not productize architecture merely because it appears elegant.

---

## 12. Historical experiment navigation

The original A–E sequence investigated bounded cognition, structural authority,
Trace reconstruction, one-shot Directive delivery, and supervision value.
It is historical evidence, not a stage gate for current tasks. Locate the
relevant reports through `../docs/CURRENT_STATUS.md`; use the implemented
capability and current acceptance criteria without repeating completed stages.
Existing Nervous integration does not need a substitute temporary trigger.

---

## 13. Reference priorities

External systems are mechanism references, not Lumina parent architectures.

### AVO

Primary reference for:

```text
sparse supervisor intervention
trajectory-level steering
one-shot advisory redirect
```

Do not copy evolutionary-search-specific topology.

### DeepSeek Harness

Primary reference for:

```text
append-only trace
derived model-visible context
causal provenance
```

Do not copy the entire framework unless a concrete failure requires it.

### Tycho

Primary reference for:

```text
event wake seams
bounded fresh auxiliary cognition
simple trigger logic
advisory cognition
```

Do not import fixed Actor / Builder / Scribe ontology.

Consult Tycho when its mechanism is relevant to the current event/attention task.

### Prime Agent

Reference for the contrast:

```text
Execution Actor
→ programmable execution authority

Mind
→ bounded cognitive/read-only authority
```

Do not expose Prime-style IPython to Mind for symmetry.

### VISTA

Future reference for:

```text
external durable evidence
model-controlled retrieval
fresh bounded continuation
```

It is not a direct dependency of the Mind MVP.

---

## 14. Source-first rule

For non-trivial mechanisms, prefer:

```text
official source code
> official package/release
> paper + official code
> paper specification
> high-quality reproduction
> Lumina-specific invention
```

Where useful, record:

```text
SOURCE
VERSION / COMMIT
SOURCE SYMBOL
ORIGINAL BEHAVIOR
LUMINA ADAPTATION
EXPERIMENTAL EVIDENCE
```

If actual source behavior is unclear, inspect it before implementation.

---

## 15. Minimal-change rule

Unless explicitly authorized:

- keep experimental implementation primarily under `Mind/`;
- modify files outside `Mind/` when required by the vertical slice;
- do not perform repository-wide refactors;
- do not add generic `Manager`, `Registry`, `Factory`, plugin framework, scheduler, database, or service layer;
- do not change Memory algorithms merely to simplify Mind;
- do not change Execution architecture merely for symmetry;
- reuse existing Lumina-owned facades and DTOs;
- do not bypass Memory or Execution ownership boundaries;
- update `docs/CURRENT_STATUS.md` only when relevant validation establishes the
  claim: tests for changed behavior, source/static checks for corrections to
  existing factual descriptions.

If the minimum valid experiment requires broader changes, document why before expanding scope.

---

## 16. Scope of new capabilities

The following list originated as MVP non-goals. Some areas now have bounded
implementations or experiments; inspect their current contracts before assuming
they are absent. Repairs within the current task need no renewed stage approval.
The list does not prohibit already-authorized work or relax runtime authority.

Do not implement without a separate task:

```text
Personality
Emotion
Desire
Purpose / endogenous goals
self-narrative
cognitive graph
dynamic world model
full Focus arbitration
full Nervous Organ
background autonomous cognition
generic scheduler
autonomous Dream
Evolution
Self-Cognition
generic Skill registry
Mind-controlled IPython
Mind-controlled shell
Mind-controlled filesystem writes
direct browser execution by Mind
fixed Planner
task DAG generator
fixed multi-agent role topology
```

---

## 17. Production integration

Experimental success does not automatically authorize production integration.

For a newly promoted mechanism, establish:

```text
targeted experiment PASS
→ regression validation
→ architecture-boundary review
→ source/provenance review
→ production seam integration
→ relevant regression coverage
→ update CURRENT_STATUS
```

The existing `LlmMindGate` remains supported until an explicit migration task replaces or incorporates it.

This promotion bar does not require ordinary fixes or documentation edits to
repeat the original experiment and promotion sequence.

---

## 18. Tests and evidence

For a new algorithm or architectural mechanism experiment, define:

```text
hypothesis
single changed mechanism
baseline
candidate
fixtures / tasks
acceptance criteria
failure criteria
artifacts to inspect
promotion decision
```

For ordinary tasks, the goal, acceptance criteria, and relevant validation are
sufficient; an experiment card is not an approval prerequisite.

Use synthetic/local test data and isolated temporary state. Preserve real data.

For integration changes, test the affected Mind behavior and cross-module
contracts. Follow root validation guidance: broaden or repeat checks only for
a new change, failure, or unresolved concern; Markdown-only work uses static
validation.

Do not optimize against individual benchmark strings, case IDs, or fixture-specific exceptions.

Do not tune several architectural variables simultaneously merely to obtain PASS.

---

## 19. Git and repository safety

Unless explicitly authorized:

```text
do not commit
do not push
do not rebase
do not hard-reset
do not rewrite history
```

Also:

- preserve `.env.local`, `data/`, real conversation state, and user files;
- inspect `git status` before destructive cleanup;
- do not overwrite unrelated user changes;
- do not silently patch pinned upstream dependencies;
- remove generated caches and temporary outputs before final promotion when they are not intentional artifacts;
- run `git diff --check` before declaring implementation complete.

---

## 20. Decision checklist

Before adding a mechanism, ask:

```text
Is this required by the current experiment?
Does this turn Mind into a Planner?
Does this turn Mind into an Execution Actor?
Is runtime authority enforced structurally?
Is model-visible information bounded?
Can Mind request more information only when needed?
Is output high-level?
Are Intention changes explicit state transitions?
Is Trace reconstructible without hidden CoT?
Does failure preserve current Intention?
Am I expanding beyond the current task and existing capability boundaries?
Did a targeted experiment demonstrate the need?
```

If a mechanism is unnecessary to test the current hypothesis, do not add it.

---

## 21. Compact target

```text
Important Event
      ↓
bounded Mind activation
      ↓
Mind model
  ↙          ↘
Memory       Execution
Recall       Inspect
(read-only)  (read-only)
  ↘          ↙
 cognitive integration
      ↓
NoChange / Directive / DecisionIntent
      ↓
append-only trace
```

Authority boundary:

```text
Development agent working in Mind/
    → may read/modify the Lumina repository as task-required

Runtime Mind model
    → bounded cognitive requests and read-only/pure-computation projections
    → no direct world mutation authority
```

Trusted hosts and owning organs retain persistence, delivery, and permitted
action authority through their interfaces; these handles are not model tools.

The objective is to establish the smallest reliable cognitive supervision substrate for Lumina and require later experiments to justify every additional mechanism.
