# Mind cognitive architecture

The current core is `Mind <-> Nervous <-> Execution <-> Environment`.
There is no separate Session/Host owning the loop.
[Mind design](MIND_DESIGN.md) defines the cognitive/action boundary;
[current status](CURRENT_STATUS.md) distinguishes mechanics from behavioral evidence.

## Owners

| Owner | State and interface |
| --- | --- |
| MindOrgan | One authorized task, current cognitive activity, selected immutable source copies, prediction interpretation; handles user/events and cognitive continuations. |
| Cognition | Accepted items and revisions; activate/accept_result/inspect; atomic final commits and bounded activity history. |
| Mind analysis | Optional independent context, selected evidence, computation artifacts and concise attributed results. No business workspace. |
| Nervous | Durable mailboxes, causal completion, pending delivery, foreground continuation and common provider accounting. No semantic direction. |
| Execution | AgentProcess lifecycle, linked runs, environment sources, original guidance binding/receipts, feedback obligations and declared observation watches. |

The CLI assembles these owners and submits an event. No fourth coordinator
controls their semantic state. Chat's Recall gate remains separate.

## Trace, State and Context

Trace is the append-only activity/native request history. State is the accepted
understanding folded from atomic submissions. Context is a bounded projection
for the current decision, reconstructed from state and selected sources.
Removing irrelevant context does not erase history.

The current schema is `mind-cognition-v1`; the native protocol is
`mind-native-v1:6-calls`. Retired V/D/W formats are not loaded or migrated.

A cognitive_step contains selective updates and a next decision. Unsubmitted
items remain unchanged. An update replaces the named record completely,
including claim, status, basis and optional discriminator. An optional current
selection retires omitted records from effective understanding while preserving
their history. Final commits alone change accepted state.

A belief's status evaluates its NEW literal claim: supported means warranted,
contradicted means its negation is warranted, open means unresolved. Conditions
and temporal scope belong in the claim. A discriminator is an optional useful
unobserved test, not a mandatory invented premise. Questions and scenarios may
capture unresolved matters. References must resolve; their existence does not
certify the inference.

## Cognitive activity and result flow

```text
user/important event
  -> Mind requests a bounded Execution snapshot
  -> Mind activates existing Cognition
  -> optional read_evidence / analyze_world_model
  -> durable request and result events
  -> final atomic cognitive_step
  -> original NoChange / Directive through Nervous
  -> Execution applies at an eligible decision
  -> important result event returns to Mind
```

An activity shares six model calls across consultation and protocol recovery.
It may end earlier. Invalid structure receives specific bounded feedback; no
semantic reviewer or runtime answer replacement is introduced. Failed or
unresolved activities remain explicitly visible, never converted to NoChange.
DecisionIntent is preserved but does not implement a formal goal switch.

## Analysis and prediction

Analysis receives only its question, selected source records and an optional
prior artifact. It can return structured understanding/unknowns without code,
or build/reuse/revise a bounded isolated calculation. Mind and Execution do not
receive its full transcript.

Prospective computations declare candidate action, applicable conditions,
observation object/time and quantity meaning/units before later evidence.
Execution owns the observation and watch; Mind owns comparison interpretation.
The comparator distinguishes unverified, inapplicable, incomparable and
comparable quantities. Final-field agreement is not full trajectory validation.
Real observations, model forecasts and producer explanations retain distinct
references. No universal anomaly detector is implied.

## Persistence and scope

Mind preserves accepted revisions and raw current-protocol activity history.
Nervous preserves events and causal completion. Execution preserves owner
EventLog/checkpoints, guidance receipts and unknown-action protection.
Provider attempts are reserved and recorded before dispatch; costs span all roles.
A known response can be resumed without re-sampling. Unknown outcomes stop
instead of being blindly replayed.

The supported scope is one goal, a bounded workspace and foreground operation.
Longer-term North Star work includes broader continuity, autonomous focus/goals
and self-improvement; none is implied by this core refactor.
Historical experiments and their limitations are condensed in
[EXPERIMENT_HISTORY](../Mind/docs/EXPERIMENT_HISTORY.md).
