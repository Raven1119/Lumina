# Mind: high-level judgment and continuous understanding

Mind is Lumina's cognitive center outside Execution's actor tree. It interprets
the authorized goal, conditions and important events, reconciles prior
understanding with evidence, and decides direction. Execution performs local
planning, implementation and action. Nervous carries their events.

This document defines the boundary; [current implementation](CURRENT_STATUS.md),
[cognitive state](MIND_COGNITIVE_ARCHITECTURE.md) and the
[operating contract](../Mind/docs/INTEGRATED_CHAIN.md) provide details.
[NORTH_STAR](NORTH_STAR.md) supplies the longer-term direction.

## One intention, distinct role views

The owner task contains a business goal and an Execution protocol. Their full
rendering is the actual Execution goal; Mind receives the business view with the
same identity. Business acceptance and safety constraints remain intact.
Runtime completion is not business acceptance.

Mind owns the continuing interpretation. A Directive does not silently change
the task. Single-goal operation remains the default. Explicit `--pursuit` starts
the same Mind under an owner-authorized scope and permits serial, immutable
Task versions; Execution accepts each Task before it authorizes action.
DecisionIntent by itself does not switch a goal. The
[Stage1 contract](../Mind/docs/INTENTION_STAGE1.md) defines the supported scope,
Task and Watch rules. Broader autonomous goals, emotions and personality
evolution remain longer-term objectives.

## One cognitive activity

A user event or important execution event begins a bounded activity:

```text
event -> relevant prior cognition + attributed evidence
      -> optional evidence read / independent analysis
      -> selective, atomic cognitive submission
      -> NoChange or high-level Directive
```

There is no mandatory rewriting Agent or permanent reviewer. Mind chooses
whether new information or analysis is needed. Consultation drafts do not alter
accepted state. Final submission evaluates the current literal claim, its
conditions, status and basis together. Correct unchanged knowledge remains;
obsolete knowledge is explicitly revised or retired while history persists.

NoChange is a real choice, including after cognitive revision or successful
result review. It neither certifies success nor asks Execution to do more work.
A Directive communicates the conclusion, relevant conditions, constraints,
decisive evidence/gap and remaining business priority. Filenames and fields can
identify a requirement. Code, commands and step-by-step tool procedures belong
to Execution.

## Information and authority

Mind receives bounded task/state projections and immutable source references.
It can request specific evidence and analyze selected sources; it cannot act in
the business workspace. A report is attributed to its producer. Repetition
through a file, log or another model does not turn inference into observation.
Missing evidence supports a scoped unknown, not an unlimited negative claim.

World-model analysis is an optional Mind capability in an independent bounded
context. It can reason from selected evidence or build/reuse a pure calculation.
Mind receives a compact report, not code/debugging history. Calculation results
remain conditional on their inputs. A declared future observation can later
be compared only when action, conditions, object, time and quantities correspond.
Mind decides the significance of agreement or divergence and whether to revise.

## Events, action and recovery

Mind publishes accepted guidance verbatim through Nervous. Execution binds it
to its still-applicable run/decision and owns delivery evidence and feedback.
Previously received guidance remains available across ordinary history trimming;
visibility is not a second delivery. Ordinary tool results stay in Execution.
Committed request_mind, important outcomes and declared observation changes can
return to the same persistent Mind.

Each organ owns its durable state and output receipt. Nervous atomically
acknowledges delivery with causal emissions. Known responses and committed
actions are not repeated on restart; unknown action outcomes require explicit
resolution. Pending cognition and bounded failures remain visible. A quiet
restart does not call a model.

## Evidence and reference systems

AVO informs high-level guidance and feedback, Tycho informs isolated conditional
computation and observation comparison, and MetaWorld informs structured
understanding rather than mandatory code modeling. These are scoped design
references, not claims of copied general intelligence or reproduced performance.
Source versions, licenses and adaptation limits are in
[REFERENCES](../Mind/docs/REFERENCES.md). Failed and inconclusive development
conclusions remain in [EXPERIMENT_HISTORY](../Mind/docs/EXPERIMENT_HISTORY.md).
