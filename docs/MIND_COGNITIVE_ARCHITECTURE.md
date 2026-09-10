# Mind cognitive architecture

The current core is `Mind <-> Nervous <-> Execution <-> Environment`.
There is no separate Session/Host owning the loop.
[Mind design](MIND_DESIGN.md) defines the cognitive/action boundary;
[current status](CURRENT_STATUS.md) distinguishes mechanics from behavioral evidence.

## Owners

| Owner | State and interface |
| --- | --- |
| MindOrgan | Current authorized Task, cognitive activity, selected immutable source copies and prediction interpretation; in Stage1 preserves one Mind across serial Task contracts. |
| Cognition | Accepted items and revisions; activate/accept_result/inspect; atomic final commits and bounded activity history, including opt-in Intention/Task/Watch effects. |
| Mind analysis | Optional independent context, selected evidence, computation artifacts and concise attributed results. No business workspace. |
| Nervous | Durable mailboxes, causal completion, pending delivery, foreground continuation and common provider accounting; Stage1 fixed attention selection, owner queries and Watch scheduling. No semantic direction. |
| Execution | AgentProcess lifecycle, linked runs, environment sources, original guidance binding/receipts, feedback obligations and declared observation watches. |

The CLI assembles these owners and submits an event. No fourth coordinator
controls their semantic state. Chat's Recall gate remains separate.

## Trace, State and Context

Trace is the append-only activity/native request history. State is the accepted
understanding folded from atomic submissions. Context is a bounded projection
for the current decision, reconstructed from state and selected sources.
Removing irrelevant context does not erase history.

Baseline uses `mind-cognition-v1`; opt-in pursuit uses
`mind-cognition-stage1-v1`. Both use `mind-native-v1:6-calls`.
Retired V/D/W formats are not loaded or migrated.

A cognitive_step contains selective updates and a next decision. Unsubmitted
items remain unchanged. An update replaces the named record completely,
including claim, status, basis and optional discriminator. An optional current
selection retires omitted records from effective understanding while preserving
their history. In Stage1, this selection applies only to visible cognition;
unselected records stay accepted. An exact current owner read expands the
activity's editable set. Final commits alone change accepted state.

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

## Opt-in Stage1 pursuit

`--pursuit` freezes the real owner's scope and preserves the same Mind identity
and budget across serial Tasks. `Mind/intention.py` validates versioned effects
inside Cognition's existing final commit: Intention changes, an optional Task
proposal and Watch changes. Mind owns their meaning; Execution owns accepting
the immutable Task version and its action facts. Candidate, committed, paused
and closed Intentions express Mind's judgment; only one may be committed.

Nervous selects bounded cognition/source references from current Task versions,
Intention understanding links and event sources, and freezes the actual Frame.
Queries append attributed owner results to the waiting activity. Earlier Task
goals, evidence and working background retain their original scope; the current
request uses relevant content and retrieval references. This extends the existing
organ journals and mailbox, without a new manager or a transplanted upstream loop.

Implementation is under validation; the
[Stage1 runtime contract](../Mind/docs/INTENTION_STAGE1.md) defines the current
authority, selection and continuation boundaries. Source identity, structural
validity and effect delivery do not establish model judgment quality.

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

Single-goal operation remains the baseline. Explicit Stage1 pursuit adds bounded
serial Tasks in the same authorized workspace and foreground process. Broader
autonomy and self-improvement remain North Star goals; this implementation does
not establish subjective experience, general autonomous reliability or new
workspace/tool/budget authority.
Historical experiments and their limitations are condensed in
[EXPERIMENT_HISTORY](../Mind/docs/EXPERIMENT_HISTORY.md).
