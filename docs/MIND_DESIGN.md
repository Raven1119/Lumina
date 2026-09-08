# Lumina Mind Organ Design — MVP

> Status: original MVP architecture boundary; current standalone behavior is documented separately.\
> Date: 2026-08-30\
> Purpose: give Codex a stable reference for **what Mind is, what it may do, what it must not do, and which external implementations should be consulted**.

> 后续设计提案（2026-09-05）：[Mind 认知架构](MIND_COGNITIVE_ARCHITECTURE.md)将 Tycho / MetaWorld 参照与跨活动连续性、关切、情绪及目标形成放入同一目标设计。本文件继续保留 MVP 契约；新文档中的扩展需要逐阶段验证，不表示已经进入生产。

> Current standalone implementation (V73): user goals/messages reach persistent
> Mind through Nervous before Execution. One owner goal is projected by role;
> Mind chooses inquiry, cognitive revision and sparse guidance. See the
> [current chain contract and commands](../Mind/docs/INTEGRATED_CHAIN.md).
> The original MVP scope and progression below are design history, not a list
> of components still missing. [Experiment history](../Mind/docs/EXPERIMENT_HISTORY.md)
> preserves earlier failures and limits. This chain is separate from production Chat.

---

## 0. How to use this document

Before non-trivial Mind work, read in this order:

1. `AGENTS.md`
2. `docs/NORTH_STAR.md`
3. `docs/CURRENT_STATUS.md`
4. the current explicit task and, for the standalone chain, Mind/docs/INTEGRATED_CHAIN.md
5. this document

This document defines the intended **Mind architecture boundary**. It is not permission to implement every future feature mentioned here.

Use [CURRENT_STATUS](CURRENT_STATUS.md) to locate current evidence, then verify implementation facts against source and tests. The current explicit task governs authorized scope.

Development rule: **do not productize a mechanism merely because it appears elegant or appears in a successful reference system. Validate it with a targeted experiment first, preferably changing one variable at a time.**

---

## 1. North Star relationship

Lumina's long-term North Star is not “a good agent harness”. It is an independent digital life with continuous integrated cognition, memory, proactive agency, and recursive self-evolution.

Mind is therefore expected to become the central cognitive organ of that digital life. However, the current phase deliberately does **not** attempt to build the full mind described by the North Star.

The MVP should establish only the smallest architecture that can later support richer cognition without confusing cognition with execution.

### Current North Star property served

A stable separation between:

- **what Lumina understands / judges / wants to guide**, and
- **how an execution process carries out work**.

### Future-facing work deliberately not implemented now

- personality;
- emotion;
- desire;
- self-narrative;
- Purpose / endogenous goal generation;
- cognitive graph / dynamic world model;
- autonomous attention competition;
- full Nervous scheduling;
- autonomous Dream or Evolution behavior.

---

# 2. Core definition

## 2.1 Stable definition

**Mind is Lumina's “brain”: the organ responsible for acquiring relevant information, processing/integrating that information, and producing high-level guidance or cognitive decisions.**

For the MVP, Mind can be treated as a special **cognitive agent**:

```text
collect information
    ↓
think / integrate
    ↓
collect more information when needed
    ↓
think again
    ↓
produce high-level guidance / decision intent
```

The internal loop may be ReAct-like, but it is a **cognitive ReAct loop**, not an execution loop.

## 2.2 Mind is not an Actor

Mind is not an `AgentProcess` inside the Execution recursive tree.

```text
Execution recursive topology

Root Actor
├── Child
│   └── Child
└── Child

        ↑ observe / guide
       Mind
        ↑
   important events
```

Therefore:

- Execution must not `spawn_child("Mind")`;
- Mind must not be represented as an Execution child;
- Mind does not inherit Actor execution authority;
- Mind can inspect and guide the execution system through explicitly exposed cognitive interfaces.

This boundary is structural, not just a prompt convention.

## 2.3 Mind is not a Planner

A Planner answers questions such as:

```text
How do I complete this task?
What steps should I execute?
What dependency order should I follow?
```

Mind answers questions such as:

```text
What is Lumina currently trying to do?
What important event just happened?
What information matters to this judgment?
Is the current execution direction still sensible?
Should the current direction be maintained, redirected, suspended, or reconsidered?
```

Mind may say:

> The current approach is repeatedly failing for the same reason; verify assumption X before continuing.

Mind should not turn that into:

```text
1. open file A
2. grep symbol B
3. modify line C
4. run test D
```

Detailed task decomposition belongs inside Execution. A planner may later emerge or be spawned **inside Execution** as one execution strategy; Planner is not the ontology of Mind.

---

# 3. Core ontology and ownership

## 3.1 Intention

The standalone chain uses one owner task: business_goal preserves the goal and
business acceptance; execution_protocol preserves runtime instructions. Their
deterministic full rendering remains the actual Execution goal. Mind receives
the business view with shared task identity and source references. Operational
completion is evidence, not proof of business acceptance. This does not wire
production Chat or implement formal Intention switching. See the
[current role and output contract](../Mind/docs/INTEGRATED_CHAIN.md).

`Intention` is the overall direction that Execution is currently pursuing. Conceptually it is similar to a persistent `/Goal`.

Examples:

```text
Investigate why the Recall regression occurs and produce a verified fix.

Audit the current Execution Organ against Prime Agent capabilities.
```

Properties:

- there should be one authoritative current Intention, not separate drifting Mind and Execution copies;
- Mind interprets and supervises the Intention;
- changing what Lumina is currently doing is **not** an implicit side effect of natural-language advice;
- a requested Intention change must become an explicit event / state transition handled in code.

The exact storage owner may be decided by implementation constraints, but semantically there must be one source of truth.

## 3.2 Focus

`Focus` is where Lumina's primary attention is currently allocated.

Long-term direction:

- Mind should gain substantial autonomy over Focus because deciding what deserves attention is cognitive;
- Nervous may mechanically schedule, defer, or interrupt Focus in specific cases;
- Focus and Intention are different.

Example:

```text
Intention remains: finish investigation X

urgent user event arrives
→ Focus temporarily moves to user event
→ event handled
→ Focus returns to investigation X
```

Focus is **not part of the current Mind MVP implementation target** beyond preserving interfaces that do not make future Focus control impossible.

## 3.3 Directive

A `Directive` is high-level steering from Mind to an Actor or organ.

For MVP, Directive semantics are:

- advisory high-level guidance;
- one-shot by default;
- does not prescribe low-level execution steps;
- does not itself change Intention;
- Execution remains responsible for deciding how to implement it.

Example:

```text
Directive:
"The last three attempts share the same unverified assumption. Re-check the
provider-native tool-result semantics before making another runtime change."
```

This is valid.

The following is too planner-like for Mind:

```text
"Edit runtime.py at line 140, add retry_loop(), then run pytest X and Y."
```

## 3.4 Decision Intent

A `DecisionIntent` is Mind's semantic conclusion that a system-level change should occur.

Examples may eventually include:

- revise current Intention;
- clear Intention;
- suspend current work;
- resume prior work;
- request Focus change.

Mind **does not directly mutate these states**. A DecisionIntent must enter the explicit event/state-transition path.

The exact event vocabulary is not frozen by this document.

## 3.5 Nervous

Nervous is the future event-routing / waking / scheduling / focus-management organ.

Mind answers primarily **what something means and what direction should be taken**.

Nervous answers primarily **what gets awakened, when, and how attention or pending events are scheduled mechanically**.

Do not build the full Nervous Organ as part of the Mind MVP.

---

# 4. Mind activation model

## 4.1 One activation is a bounded cognitive episode

Mind must not be permanently modeled as one infinite LLM conversation.

Each activation should conceptually be:

```text
Important Event
    ↓
create Mind activation
    ↓
construct bounded initial MindContext
    ↓
Mind LLM reasons
    ↓
optional cognitive capability calls
    ↓
new observations
    ↓
Mind LLM continues reasoning
    ↓
final Directive / DecisionIntent / NoChange
    ↓
activation ends
```

An activation may contain multiple model calls and multiple information-acquisition steps.

The activation is bounded by explicit resource limits such as model-call / step / context / output limits. Exact values are experimental parameters, not architectural constants.

## 4.2 Continuity does not come from an infinite Mind transcript

Across activations, continuity should come from durable external state such as:

- current Intention;
- Mind Trace;
- Memory;
- relevant organ state;
- later, richer cognitive state.

A fresh activation should reconstruct only the bounded context it needs.

This follows the broader Lumina rule that persistent identity/state is external to a single model context.

---

# 5. Mind information sources

## 5.1 MVP information sources

The minimal Mind gets information from only two systems:

```text
1. Execution
2. Memory
```

Do not add unrelated organs merely to make the interface “general”.

## 5.2 Execution information

Mind should receive or request a **supervisor view**, not the complete raw Execution transcript by default.

The view should be sufficient to understand:

- current Intention;
- current coarse execution status;
- important recent outcomes / failures / transitions;
- bounded key trace evidence when needed.

Mind should be able to ask for more information through a read-only capability when the initial view is insufficient.

Do not make full recursive Actor history the default Mind context.

## 5.3 Memory information

Memory Recall is a cognitive capability.

Mind decides whether recall is useful and what to query.

Conceptually:

```text
Mind reasoning
→ "I need earlier context about X"
→ recall_memory(query)
→ bounded Memory result
→ reasoning continues
```

Do not automatically dump large amounts of Memory into every Mind activation.

Existing Memory durability / provenance / bounded Recall contracts remain authoritative; Mind should consume the Lumina-owned Recall surface rather than bypassing it.

---

# 6. Cognitive ReAct and Capability Surface

## 6.1 Cognitive ReAct

The Mind loop may use ReAct-like interaction:

```text
reason
→ acquire information
→ observe result
→ reason
→ acquire information
→ observe result
→ decide
```

But the available actions are cognitive / read-only.

It must not become:

```text
reason
→ edit workspace
→ run shell
→ change runtime state
→ perform the task itself
```

## 6.2 Capability Surface is the authority boundary

Mind's actual authority must be enforced by the capabilities exposed to it.

The rule is:

> A capability existing somewhere in Lumina does not imply Mind can invoke it.

For the MVP, the conceptual surface is no broader than:

```text
Memory Recall      — read-only cognitive capability
Execution Inspect  — read-only cognitive capability
```

Exact function names and DTOs should follow current codebase constraints and targeted experiments; do not create a generalized tool framework prematurely.

## 6.3 Capability Discovery vs Capability Invocation

Long-term, Mind may know which Skills / capabilities Lumina possesses.

Separate two concepts:

```text
Capability Discovery
→ Mind can know "Lumina is capable of X"

Capability Invocation
→ Mind has authority to call X directly
```

A future capability registry may expose metadata through a fixed channel.

For example:

```text
Execution skill: browser research
Mind may know it exists
Mind may recommend using it
Mind does not directly invoke the browser
```

Only cognitive/read-only capabilities should be directly invokable by Mind unless a later design explicitly changes the authority model.

Do **not** implement a general Skill registry as part of the current MVP unless separately authorized.

## 6.4 No IPython for Mind MVP

Prime Agent's persistent IPython surface is appropriate for an Actor because an Actor needs programmable orchestration and execution authority.

Mind MVP does not.

Therefore the current design does not expose IPython, shell, filesystem mutation, or arbitrary process execution to Mind.

If future Mind cognition becomes complex enough that a programmable cognitive workspace could help, that must be tested separately rather than assumed now.

---

# 7. Mind output contract

A Mind activation must be allowed to produce **no intervention**.

Conceptually the final semantic result is one or more of:

```text
NoChange
Directive
DecisionIntent
```

Do not force the model to invent a correction on every activation.

## 7.1 Directive delivery

For MVP, borrow the AVO-style one-shot intervention model:

```text
MIND_DIRECTIVE_ISSUED
        ↓
      pending
        ↓
injected into the next eligible Execution decision context
        ↓
MIND_DIRECTIVE_APPLIED
        ↓
      consumed
```

Required semantics:

- issued Directive survives crash/restart before application;
- already-applied Directive is not silently injected again;
- application is traceable;
- the Actor remains responsible for how to respond.

A useful model-facing framing is conceptually:

```text
[Current Intention]
...

[Mind Supervisor Directive]
...
```

Do not secretly mutate a long-lived Execution system prompt to apply a Directive.

## 7.2 Intention changes are event-driven

A Directive is not an Intention change.

If Mind concludes that “what Lumina is currently doing” should change:

```text
Mind semantic decision
→ explicit DecisionIntent / event
→ code-owned state transition
→ updated authoritative Intention
→ trace the transition
```

No hidden state mutation through natural-language output.

---

# 8. Trace model

## 8.1 Trace philosophy

Mind Trace should stay conceptually similar to Execution Trace and use DeepSeek Harness (DSH) as the primary external implementation reference.

Core rule:

```text
Trace != State != Context
```

- **Trace**: append-only historical facts.
- **State**: current derived system state.
- **Context**: bounded projection actually shown to a model for one decision.

The append-only log is the historical source of truth. Model context is derived from that truth; it is not the truth itself.

## 8.2 What must be reconstructible

For a Mind decision, the system should be able to reconstruct:

```text
what triggered Mind
→ what initial context was visible
→ what information capability Mind invoked
→ what result was returned
→ what subsequent model-visible context was formed
→ what final Directive / DecisionIntent / NoChange was produced
→ what later system transition applied that output
```

This does **not** mean persisting private hidden chain-of-thought.

Record externally observable model messages, capability calls/results, model outputs permitted by the provider, and state transitions. Do not invent or require hidden CoT storage.

## 8.3 Causal provenance

Mind decisions and directives should retain causal references to the events that led to them, analogous to DSH's `sourceEventSeqs` idea.

This is important because future Self-Cognition/Evolution must be able to distinguish:

```text
creator explicitly changed direction
vs
Mind independently judged a redirect was needed
vs
Execution failure caused reconsideration
```

## 8.4 Illustrative event vocabulary

The following names are illustrative, not a frozen schema:

```text
mind/activation_started
mind/capability_called
mind/capability_result
mind/decision
mind/directive_issued
mind/directive_applied
mind/failed
intention/revision_requested
intention/revised
```

Prefer the smallest vocabulary that preserves the required facts. Do not build a generic event-bus ontology during the MVP.

---

# 9. Event and Nervous seam

Mind does not poll everything in Lumina continuously.

The desired long-term flow is:

```text
external / internal change
        ↓
      Event
        ↓
     Nervous
  wake / queue / defer
        ↓
       Mind
 semantic judgment
```

Internal events are first-class future inputs: Dream insights, cognitive contradictions, self-cognition findings, etc. That is a future direction, not an MVP implementation requirement.

For the current phase:

- provide only the narrow activation seam required by experiments;
- do not build the complete Nervous scheduler;
- supervision trigger policy may temporarily borrow an AVO-style stagnation condition for testing;
- detailed trigger policy belongs to the later Nervous design.

---

# 10. Failure semantics

Mind failure must be conservative.

If Mind times out, returns malformed output, loses its provider, or otherwise fails:

```text
current Intention remains unchanged
current Execution may continue under that Intention
important unhandled event remains pending when applicable
no default redirect is invented
Mind failure is appended to Trace
```

User / creator mechanical control such as an explicit stop must not depend on Mind successfully reasoning first.

Failing Mind must not create a new Intention by fallback heuristic.

---

# 11. Relationship to recursive Execution

Lumina's recursive execution architecture and Mind are deliberately orthogonal.

Execution may dynamically produce:

```text
linear trajectories
branches
parallel siblings
recursive trees
local DAG-like dependencies
joins
backtracking
specialized temporary children
```

Mind observes the resulting execution process from outside the execution tree and provides high-level steering when needed.

Do not import Tycho's fixed `actor / builder / scribe` topology into Lumina.

A Lumina Actor may autonomously spawn a child whose local role resembles a Tycho Builder, reviewer, researcher, verifier, etc. The **role is emergent/local**, not a fixed global Agent type required by the harness.

This distinction is fundamental:

```text
Tycho pattern:
Actor → known specialized Builder

Lumina execution target:
Actor → generic recursive Child
             ↓
        local role emerges from task
```

Mind itself remains outside both patterns.

---

# 12. Original MVP implementation scope

The smallest meaningful Mind vertical slice should prove these properties:

1. an important event can create one bounded Mind activation;
2. Mind can understand the current Intention;
3. Mind can acquire information from Execution through a read-only surface;
4. Mind can call bounded Conversation Memory Recall when useful;
5. Mind can take more than one reasoning/information-acquisition step in one activation;
6. Mind can finish with `NoChange`, a high-level Directive, or a DecisionIntent;
7. Directive delivery is explicit, durable enough for crash/retry semantics, and one-shot;
8. Mind cannot directly execute shell/filesystem/runtime mutations;
9. Mind Trace can reconstruct model-visible inputs and capability observations without hidden CoT;
10. Mind failure preserves the existing Intention.

Do not implement more merely to make the architecture look complete.

---

# 13. Original MVP non-goals

The following are out of scope unless a separate task authorizes them:

- full Nervous Organ;
- generic scheduler;
- background autonomous loop;
- Mind-controlled shell or IPython;
- workspace edits from Mind;
- generic planner;
- task DAG generation;
- fixed multi-agent role graph;
- Tycho-style executable world model;
- VISTA-style visual memory;
- Personality;
- Emotion;
- Purpose / self-generated goals;
- cognitive graph;
- self-narrative;
- full Focus arbitration;
- autonomous Dream;
- autonomous Evolution;
- general Skill ecosystem / registry;
- direct invocation of execution Skills by Mind.

---

# 14. Historical experimental progression

This is the original A-E progression, not current unfinished work. Results are summarized in [experiment history](../Mind/docs/EXPERIMENT_HISTORY.md); current implementation and limits are in the [chain guide](../Mind/docs/INTEGRATED_CHAIN.md). A current task governs further work.

## Experiment A — bounded cognitive ReAct

Goal: prove that Mind can decide to acquire information and then continue reasoning.

Compare against the current simplest single-call decision baseline if one exists in the active codebase.

Success evidence should include actual trajectories showing:

```text
activation
→ inspect/recall only when useful
→ observation
→ final judgment
```

Do not evaluate “smartness” before the loop itself is reliable.

## Experiment B — authority boundary

Goal: prove Mind cannot perform execution actions even when prompted to do so.

The result should follow from the exposed capability surface, not merely from a system prompt saying “do not”.

## Experiment C — Trace reconstruction

Goal: replay a completed activation from durable events and reconstruct the exact bounded model-visible sequence used for decisions.

Use DSH's event-log/surface separation as the implementation reference.

## Experiment D — one-shot Directive delivery

Goal: prove:

```text
ISSUED → pending → APPLIED → consumed
```

under normal run, retry, and restart boundaries.

## Experiment E — behavioral value

Only after A-D are reliable, test whether Mind supervision actually improves Execution on tasks where the Actor can become stuck or pursue a bad direction.

Temporarily use a simple AVO-like sparse supervision trigger rather than inventing a complex Nervous policy.

Compare one variable at a time.

---

# 15. Reference systems and what to borrow

## 15.1 NVIDIA AVO — primary reference for supervision

### Sources

- Paper: **AVO: Agentic Variation Operators for Autonomous Evolutionary Search**\
  https://arxiv.org/abs/2603.24517
- NVIDIA ARC-AGI-3 architecture/result discussion:\
  https://developer.nvidia.com/blog/nvidia-avo-reaches-100-on-arc-agi-3-demonstrating-a-frontier-level-general-purpose-architecture-for-long-horizon-autonomous-agents/

### Source-backed idea to borrow

AVO keeps the main agent responsible for local work while a supervisor monitors the longer trajectory and conditionally intervenes during stagnation. The paper describes the supervisor as reviewing the broader evolutionary trajectory and redirecting exploration when the strategy plateaus.

For Lumina:

```text
Execution Actor owns "how"
Mind observes broader state
Mind intervenes sparsely with high-level steering
```

### Open reproduction used as an implementation reference

Repository:\
https://github.com/gatordevin/avo

Relevant files:

- `src/avo/loop.py`
  - stagnation gate via `steps_since_best()`;
  - supervisor invoked conditionally;
  - supervisor output stored for the next variation step;
  - note cleared after that step;
  - supervisor edits are reverted, enforcing read-only/advisory intent.
- `src/avo/prompts.py`
  - supervisor sees target, lineage, recent trajectory, stagnation length and knowledge index;
  - variation prompt frames the supervisor redirect as a **strong prior, not an order**.

Important evidence rule: **`gatordevin/avo` is an open reproduction, not NVIDIA's official runtime source.** Use it as an engineering reference only where behavior is independently consistent with the AVO paper/blog.

### Do not copy

- evolutionary population/lineage specialization;
- GPU scoring machinery;
- a scalar “steps since best” metric as a permanent general Mind trigger without Lumina-specific evidence.

---

## 15.2 DeepSeek Harness (DSH) — primary reference for Trace / Context projection

Repository:\
https://github.com/deepseek-ai/deepseek-harness

Relevant source:

- `packages/core/session/src/index.ts`
- `packages/core/session/src/surface.ts`

Key implementation idea:

```text
append-only SessionEvent log = source of truth
Surface = derived ordered model-visible projection
non-surface events remain trace/replay facts
```

`surface.ts` explicitly states that the append-only log remains the source of truth and that model-visible messages are derived by a canonical projection. It also carries causal provenance for replacements using source event sequences.

For Lumina Mind:

```text
MindEventLog
    ↓ deterministic projection
MindContext / model-visible surface
    ↓
Mind decision
    ↓ append more events
```

Borrow the philosophy and small mechanisms; do not clone DSH's entire Session/Surface framework before Mind actually needs replacement/compaction semantics.

---

## 15.3 Tycho — reference for event wake seams and fresh bounded auxiliary cognition

Repository:\
https://github.com/NIMI-research/Tycho

Paper: **Tycho: Active Abstraction with Programmatic World Models for ARC-AGI-3**\
https://arxiv.org/abs/2607.28287

Relevant source:

- `tycho/agent/events.py`
  - small explicit event vocabulary;
  - plain Python trigger predicates;
  - `sees_full_history` distinction;
  - auxiliary agents may run with fresh bounded context.
- `tycho/agent/dispatcher.py`
  - state/evidence-driven trigger;
  - no arbitrary cooldown in the main falsification trigger;
  - snapshot/restore of trigger state.
- `tycho/agent/modes.py`
  - explicit distinction between actor-pull and harness-push orchestration.
- `tycho/agent/agent.py`
  - one durable freeform tool-native Actor conversation;
  - older heavy evidence remains retrievable from the workspace rather than permanently occupying context.
- Builder implementation under `tycho/agent/`
  - focused auxiliary cognition in a bounded conversation;
  - shared durable evidence;
  - advisory report returned to Actor.

### Borrow for Mind / later Nervous

- important event wakes a cognitive component; event does not itself perform cognition;
- plain, inspectable trigger logic is preferable to a premature trigger DSL;
- auxiliary cognition should receive a bounded context slice rather than the entire Actor transcript;
- a helper can advise the Actor without owning Actor execution.

### Do not copy into Lumina Execution

Tycho has known role/topology concepts such as Actor / Builder / Scribe and currently constrains combinations of orchestration modes. That conflicts with Lumina's intended generic recursive AgentProcess substrate.

Do **not** turn Builder/Scribe into fixed Lumina execution process types.

Also do not copy Tycho's ARC-specific executable `world_model.py`, verifier, or planner into Mind MVP.

---

## 15.4 VISTA — future reference for external evidence and model-controlled recall

Official project page:\
https://vista-research.github.io/

Official GitHub website/replay repository:\
https://github.com/vista-research/vista-research.github.io

VISTA publicly documents:

- lossless external visual memory indexed by turn/frame;
- model-controlled `inspect` / pixel retrieval;
- minimal durable `GUIDE.md` and `WORKING.md` notes;
- fresh-context continuation near context limits while external memory, notes and action history remain available.

This reinforces a general Lumina principle:

> Long-term continuity should rely on durable external state and model-controlled retrieval, not on keeping every past observation permanently inside one LLM context.

VISTA is **not a direct implementation dependency for Mind MVP**. It should be revisited when optimizing Execution context/evidence handling.

Evidence caveat: the public material examined here is the official project site, replay data and website repository. Do not claim access to a non-public VISTA harness implementation unless it is later released.

---

## 15.5 Prime Agent — reference for Actor/capability contrast, not Mind architecture

Repository:\
https://github.com/PrimeIntellect-ai/prime-agent

Relevant source:

- `packages/coding-agent/src/core/tools/index.ts`
  - model-visible built-in tool surface is centered on `ipython`.
- `packages/coding-agent/src/core/prompts/rlm.ts`
  - persistent IPython is the programmable control environment;
  - recursive subagents are programmatic;
  - installed Skills can be made discoverable and callable through explicit runtime contracts.

Prime validates the usefulness of a programmable surface for an **Actor**.

The Mind conclusion is deliberately different:

```text
Execution Actor → programmable execution capability
Mind            → bounded cognitive/read-only capability
```

Do not expose Prime-style IPython to Mind merely for architectural symmetry.

Long-term Skill discovery should borrow the explicit capability-contract idea while preserving the distinction between **knowing a Skill exists** and **having authority to invoke it**.

---

# 16. Reference priority by subsystem

Use the references this way:

```text
Mind supervision behavior
    → AVO

Mind Trace / model-context reconstruction
    → DSH

Mind activation seam / future Nervous triggers / bounded auxiliary cognition
    → Tycho

Execution evidence/context optimization
    → VISTA + Tycho + Prime Agent

Recursive execution substrate
    → Prime Agent + existing Lumina Execution design
```

No external project is the “mother architecture” of Lumina.

Borrow mechanisms only where they preserve Lumina's own ontology and survive targeted experiments.

---

# 17. Codex implementation guardrails

When implementing or experimenting on Mind, Codex should check all of the following before expanding scope:

- Is this change required to prove the current experiment?
- Does this accidentally turn Mind into a Planner?
- Does this accidentally turn Mind into an Execution Actor?
- Is execution authority blocked by code/capability exposure rather than prompt wording alone?
- Is the information Mind receives bounded?
- Can Mind actively request Memory/Execution information when needed?
- Is the final guidance high-level?
- Are Intention changes explicit state transitions rather than hidden prompt mutations?
- Is Directive delivery traceable and idempotent?
- Is the model-visible context reconstructible from append-only facts?
- Are causal source events retained?
- Does Mind failure preserve current Intention?
- Did this task accidentally implement Nervous, Personality, Emotion, Purpose, cognitive graph, or a generic Skills framework without authorization?
- Was a targeted experiment run before promoting the mechanism?

If the answer to the final two questions is “yes” / “no” respectively, stop and reduce scope.

---

# 18. Compact architecture summary

```text
                   Important Event
                         │
                         ▼
                  ┌─────────────┐
                  │    Mind     │
                  │ cognitive   │
                  │   agent     │
                  └──────┬──────┘
                         │
              bounded cognitive ReAct
                 ┌───────┴────────┐
                 ▼                ▼
        Conversation Memory    Execution
             Recall           Inspect/View
          (read-only)          (read-only)
                 └───────┬────────┘
                         ▼
              Guidance / Directive
                 / DecisionIntent
                         │
                         ▼
              explicit event/state seam
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
           Nervous               Execution
       wake/schedule/focus     decide how to act
        (scheduling proposed)   recursive AgentProcess
```

Persistence model:

```text
append-only Mind Trace
        ↓
derived bounded MindContext
        ↓
Mind model + cognitive capability calls
        ↓
Directive / DecisionIntent
        ↓
explicit application event
```

The MVP should prove this loop with the minimum code necessary. It should not attempt to simulate the full future mind.
