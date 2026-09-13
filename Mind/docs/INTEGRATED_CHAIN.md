# Mind-Nervous-Execution operating contract

The supported foreground entry is `python -m Mind`. The current core has
organ-owned state, baseline and opt-in pursuit cognition under one native protocol; it does not
load old V1-V72 sessions or campaign traces. Production Chat's Recall gate and
the manual Execution API remain separate.

## Setup and use

Install the root requirements and provide `DEEPSEEK_API_KEY` through the existing
environment loader. Docker must be available for workspace actions and isolated
model computation. Build the small interpreter image if it is not installed:

```powershell
docker build -t lumina-execution-ipython:d2 Execution/container
docker pull python@sha256:3b3706a90cb23f04fabb0d255824f9a70ceb46177041898133dd5a35f3a50f0a
```

The retained interpreter tag identifies an installed image, not an old runtime
contract. Its Dockerfile pins the Python base and IPython version. Computation
has no business mount; Execution mounts the explicitly authorized workspace.
Experimental context modes also mount a separate read-only export containing
only this Run's saved action/result projections, never the provider or owner store.
Neither backend has network access or a host-execution fallback.

Keep private state, credentials and development logs outside that workspace.
Use a small task directory, not the whole repository.

```powershell
.venv/Scripts/python.exe -m Mind start --state .mind-state/task1 --workspace <task-directory> --goal-file <goal.txt> --max-calls 40
.venv/Scripts/python.exe -m Mind status --state .mind-state/task1
.venv/Scripts/python.exe -m Mind resume --state .mind-state/task1
.venv/Scripts/python.exe -m Mind resume --state .mind-state/task1 --message "<actual new information>"
.venv/Scripts/python.exe -m Mind resume --state .mind-state/task1 --event INPUT_READY --data "<actual evidence>"
```

The original goal/workspace launch input is retained by Nervous before organ
construction. Interrupted initialization resumes from those same arguments.
An exact repeated start is idempotent; a conflicting task/workspace is rejected.
Later messages reach the same Mind before action, regardless of Execution's
current wait status. Only an actual matching event satisfies an outside wait.

Each later message/event is a new submission by default, even when its text
matches an earlier one. To retry a transport submission, supply the same
`--submission-id` on its original invocation and retry:

```powershell
.venv/Scripts/python.exe -m Mind resume --state .mind-state/task1 --message "Continue." --submission-id click-1
```

A second intentional "Continue." uses a new identity (or omits the flag).
Reusing an identity with changed text/event type is rejected. This transport
identity does not restart an already handled cognitive activity or action.

With no new information, baseline resume stays quiet without model calls.
Stage1 resume also checks foreground source/due Watches; `status` is read-only
in both modes.

Optional exact repetition observation is fixed at start; its default is off:

```powershell
.venv/Scripts/python.exe -m Mind start --state .mind-state/observed-task --workspace <task-directory> --goal-file <goal.txt> --repetition-mode execution --max-calls 40
```

`execution` presents a bounded fact to Execution after three consecutive identical
completed IPython cells, full results and unchanged sampled sources in one Run/Task.
`mind` also routes that fact to the same Mind through Nervous. Reads and original
high-level advice use their existing paths. NoChange and new advice neither erase
the observation before Execution sees it nor rearm notification. Changed sampled
sources/owner input or a new Task/Run can begin a new segment. Repetition is not
proof of no progress; unobserved memory and external changes remain unknown.
Full samples stay external, and oversized reads retain their existing capacity
limits. Waiting, pending/unknown actions, truncated results and completed quiet
restarts do not become new repetition reviews. Original completion continuation
still has priority. Resume takes no mode override. This foreground feature is
opt-in; no default or behavioral superiority is promoted by the
[diagnostic comparison](REPETITION_REASSESSMENT_RESULT.md).

Single-goal `--goal` / `--goal-file` remains the baseline launch. The opt-in
`start --pursuit "<original authorized scope>"` keeps the same Mind across
serial bounded Tasks, with explicit Intention/Watch effects and Nervous attention
selection. Use a fresh private state directory and retain the original scope;
later Task proposals do not change workspace permissions or reset costs.
Stage1 implementation is under validation. Its commands, authority boundaries,
owner queries and foreground Watch behavior are in the
[Stage1 runtime contract](INTENTION_STAGE1.md).

## Pause, recovery and working context

The first Ctrl-C requests a cooperative foreground pause: it closes admission
to further calls/actions. A known in-flight result is saved before stopping.
`status` reads owner state and valid diagnostic log prefixes without constructing
an Actor, observing workspace changes, starting a kernel or resuming anything.
It reports pending transport, unknown action references, context progress and cost.

`resume` claims an already received provider response from its original frozen
request. Saved decisions and known action results are not sampled/executed again.
An unstarted plan whose conditions or Python namespace changed is retired;
Execution makes a fresh decision. A started action with an unknown outcome stays
blocked for an actual outcome check. A partially executed batch retains its real
results; only its unstarted suffix is retired. Python variables are not restored
after a process restart. Persistent files and source references survive.

`COMPLETION_DEFERRED` preserves the original completion claim while its Mind
review is pending. Once that review clears, Execution can resume the same claim
and recheck the environment without another model-generated `claim_complete`.
This applies only at the unchanged decision/authority boundary, with no new
guidance or other intervening action. New owner input, guidance or changed
conditions require the appropriate fresh decision. Existing provider-request
responsibilities are recovered first; an unknown request cannot be bypassed by
settling completion. A cleared review is not business acceptance, and failed
completion verification remains a rejection.

Optional working projections are fixed at initial launch and survive interrupted
initialization and restarts:

```powershell
.venv/Scripts/python.exe -m Mind start --state .mind-state/context-task --workspace <task-directory> --goal-file <goal.txt> --context-mode summary --max-calls 40
```

The default `baseline` preserves the existing projection. `mask` retains recent
complete history and replaces older result bodies with explicit readable refs.
`summary` uses one owner-local rolling handoff/background, then keeps the recent
complete suffix; its initial threshold is 12 completed segments, retaining 6.
Whole-request capacity may trigger earlier bounded compaction. Under that pressure,
owners preserve the latest complete segment rather than requiring six historical
segments to fit; no native round is split. Summaries are
derived history, never accepted beliefs, new owner evidence, guidance receipts
or action results. Current authority remains separately visible. Baseline retains
its cognition projection; Stage1 selects relevant accepted items while preserving
unselected state and exact owner reads for later expansion.

Execution's `read_history(ref, offset=0, limit=8000)` in ordinary IPython reads
its exported action/result records. Mind uses existing `read_evidence` with
`history:<activation>:<sequence>[:offset:limit]` (limit at most 6000). The request
catalogues supply actual refs. Mind reads a role projection of the historical
piece: business constraints and source identity remain, while Execution's
internal completion protocol stays outside Mind. Canonical Trace is unchanged.
Range/total/truncation metadata travels with the actual returned text. History
retrieval cannot reconstruct output discarded by the original tool.

Summary requests have `purpose=compaction` in the same provider ledger and
count toward the task's limits. A known summary response can finish its atomic
commit after restart; an unknown/invalid summary or insufficient capacity pauses
without replacing the old projection. The implementation does not automatically
resample invalid summaries. For a known rejected summary, an explicit retry is:

```powershell
.venv/Scripts/python.exe -m Mind resume --state .mind-state/task1 --retry-context
```

This archives the original failed context and retains its provider response/cost,
then freezes a new attempt over the same historical prefix with concrete format
feedback and the received text reply to revise. It does not retry unknown
dispatches or change accepted cognition.
New v4 summaries reserve 8192 output tokens and target 6000 summary characters.
The provider output allocation, complete request/context capacity and cumulative
task budgets are hard limits; exceeding the drafting target alone is not a
rejection. A larger summary can still cause the next whole request to pause before
dispatch. Old v1-v3 pending requests retain their original hard character limit
until an explicit retry creates a new v4 attempt; old failures remain failures.
A valid saved response uses ordinary recovery. Full native tool batches/thinking blocks remain
paired; they are not partially cut to fit a budget.

Retain the whole private task directory and business workspace for recovery,
including provider records, canonical owner logs, current derived contexts and
the narrow history export. `status` may diagnose a damaged canonical tail but
does not truncate it or authorize resumed side effects. Files use atomic replace
and file fsync; parent-directory fsync is attempted on POSIX. This is not a claim
of protection from every platform/filesystem/power-loss failure.

See the [recovery/context design](../../docs/RECOVERY_AND_WORKING_CONTEXT_DESIGN.md)
and [condensed validation history](EXPERIMENT_HISTORY.md) for scope, evidence and
remaining limits. Detailed campaigns and raw records stay in local recovery
storage. Experimental context options are not a claim of general performance
or cognitive superiority.

To explicitly retry a failed cognitive activity after addressing its cause:

```powershell
.venv/Scripts/python.exe -m Mind resume --state .mind-state/task1 --retry-review
.venv/Scripts/python.exe -m Mind resume --state .mind-state/task1 --add-calls 8 --add-output-tokens 131072 --add-request-bytes 1200000
```

A retry is a new bounded judgment of the original matter with current evidence;
it does not erase the failed activity or reset cost. Unknown action outcomes
are not repaired by this flag. A legitimately needed follow-up after a terminal
Execution uses a linked run under the same formal goal in baseline mode.
Stage1 may instead propose a distinct Task or a new version at an admitted safe
boundary; those proposals use the existing owner authorization and shared ledger.

## Owners and flow

| Responsibility formerly in Session | Current owner |
| --- | --- |
| Goal interpretation, activations, cognitive repair and outcome judgment | Mind/organ.py and cognition.py |
| Optional analysis, model reuse and comparison interpretation | Mind/analysis.py, world_model.py |
| Original input transport, mailbox completion, pumping, shared call accounting | Nervous/organ.py, provider.py |
| Stage1 fixed attention selection, registered view routing and Watch scheduling | Nervous/attention.py, triggers.py, views.py, watch.py; persisted by Nervous/organ.py |
| Workspace observation, source snapshots, action lifecycle and recovery | Execution/runtime.py, evidence.py and the supported ExecutionOrgan facade |
| Guidance binding/receipt, execution context, feedback obligation and observation watch | Execution/runtime.py |
| Command-line arguments and organ construction | Mind/cli.py; no central state machine |

```text
user -> Nervous -> Mind
Mind -> optional evidence read / analysis -> same activity
Mind -> accepted NoChange or original Directive -> Nervous -> Execution
Execution -> ordinary actions -> Environment
important request/result or declared observation change -> Nervous -> same Mind
```

Execution can call `request_mind(question, evidence_files=(), model_ref="")`
inside a normal IPython cell. It is published only after the cell commits and
does not require Wait. Ordinary action results stay inside Execution.
A completed action yields control so pending events and feedback can run;
this handoff is neither a business wait nor fabricated evidence.
Multiple triggers for the same committed checkpoint share one review event.

Mind receives the original business requirements, relevant accepted cognition,
attributed sources and a bounded execution snapshot. Execution receives its
actual task, local state and original guidance. Analysis receives a question,
selected source copies and optionally a prior artifact. No role inherits the
other roles' complete reasoning or debugging history.

## Cognition, direction and source meaning

Only a final cognitive_step commits selective updates. Unsubmitted items stay;
an explicit archived status retires obsolete knowledge without deleting history.
A revised claim, status, basis and optional discriminator are one replacement.
Status evaluates the new literal assertion, not task pass/fail. The previous
prior_truth is an earlier judgment, not certified reality.

New pursuit activities freeze `cognitive_interface="mind-cognitive-interface-v2"`:
only `updates` adds, replaces or archives records; `current` is neither advertised
nor accepted. Baseline and previously frozen activities retain their original
interface. Old Stage1 `current` selects only within that activity's visible set;
leaving attention never retires an item or Intention. An unseen item must first
be read completely at its current owner version before it can be updated.

Stage1 cognition catalogues offer 240-character previews with truncation metadata
for selecting relevant records, not evidence or permission to edit unseen items.
The current `cognitive-item-v2` owner read returns the target record with its
original refs, without inlining basis-source or assumption bodies. Retrieve those
separately when needed. Legacy complete receipts remain readable. Target-only
reads reduce duplicated material but retain the existing capacity limit; a single
extremely escaped or large item may still fail to fit. Failed or partial retrieval
does not become a complete read.

NoChange can close a successful review, including after cognitive revision.
It does not erase previous guidance or create work. A Directive conveys the
decision, material conditions, decisive evidence/gap and business priority;
Execution chooses code and tools. DecisionIntent is representable without a
baseline formal goal switch. Stage1 uses explicit versioned Task effects for
authorized changes; a free-text decision is not a replacement Task contract.

Execution binds guidance once to a still-applicable run/decision. Previously
received text remains visible across normal history trimming; visibility is
not another delivery or proof of adoption. If the position or evidence changes
before use, old advice is withdrawn and an attributed event returns to Mind
for a fresh judgment, retaining the original owner input.

In new Execution requests, previously received guidance remains in the owner
context with its identity and scope. Historical native pairs follow it; the
last message contains current checkpoint facts from the same owner projection.
A newly bound one-shot Directive is still carried verbatim before that current
checkpoint. This ordering does not redeliver old guidance or choose an action.
The small repeated checkpoint fields count toward actual provider request size.
Already frozen requests and pending corrections retain their original wire.

Evidence is immutable by reference. Source kinds distinguish original owner
statements, actor judgments, observed text, file metadata, catalogues and
computation. A citation establishes provenance, not inference correctness.
UTF-8-sig text projection is not raw-byte certification; BOM may be stripped.
Oversize reads return explicit capacity metadata without truncating source text.

## World-model analysis

Mind chooses analysis when useful. The independent bounded role can return
understanding/unknowns directly, compute named quantities with
`predict(inputs, action)`, or run a state-transition model. Programs are optional.
The compact result keeps answer, assumptions, unknowns and an actual run reference.

Ordinary calculation creates no future-check obligation. A prospective
calculation additionally declares observation_file and check_spec before later
evidence: action, conditions, object, time, quantity meanings and units.
Execution observes the declared record; Mind compares applicable fields and
decides what agreement/divergence means. Missing observations remain unverified,
different conditions are not applicable, unmappable quantities incomparable.
Unretrieved observation bodies are explicitly unread, not absent.
Only declared final observables are compared, not the whole trajectory.
Observation watching does not require an Execution Run. A prediction followed
by NoChange remains registered. A new watch first uses its own registration
baseline, not a snapshot from an earlier unrelated review. When an external
observation appears or changes,
a foreground resume returns that evidence to the same Mind. Returning to a
previously seen value after a completed reassessment is a new change, not a
retry of the old notification, including when a waiting Run keeps the same
execution checkpoint. Pending retries retain the original event identity
across restart. An accepted review of an unchanged checkpoint stays quiet
without a model call; unread prediction comparisons remain pending and are
not acknowledged merely because their notification was handled.

## Durability and budgets

Nervous atomically acknowledges events with causal emissions. Mind preserves
activity/native traces and accepted revisions. Execution preserves EventLog,
checkpoints, guidance receipts, sources and feedback. Each organ caches its own
handling result before transport acknowledgement. There is no extra Host/Session.

All roles use DeepSeek-V4-Pro through the official Anthropic-compatible API.
Mind and analysis default to enabled low-effort thinking; Execution is disabled.
An activity shares six Mind calls across consultation and recovery. Current
submission limits are 6000 characters, 16 updates and 16000 active-state characters.
New activities report used/max/remaining characters for the same canonical accepted
state measured by the commit limit; this capacity accounting does not retire
items. Stage1 attention may omit items from a request while retaining that state.
Analysis is bounded to six calls/computations. World-model inputs have at most
16 scalar fields, with strings at most 256 characters.

New pursuit activities allow at most one protocol correction in that six-call
budget. This includes a received `max_tokens`/`end_turn` response containing only
text/thinking and no tool submission. Its original assistant blocks are preserved
and followed by ordinary user feedback, without a fabricated `tool_result` or a
host-selected conclusion. Complete-tool field corrections retain their real tool
ID and share the same one-correction allowance. The original wire and responses
stay frozen; cached responses are recovered without another charge. Old activities
keep their previous rules. A second failed submission remains an explicit failure,
and unknown dispatched requests are not eligible for this recovery.

Default launch allocation is 40 calls, 200000 reserved output tokens and
2800000 request bytes, shared across Mind, analysis and Execution. Explicit
extensions preserve spent allocations and actual usage. Guidance/prediction
feedback reserves room for result judgment and recovery before further action.

Known responses/committed actions resume without repetition. Protocol and model
errors remain explicit failed activities; no failure becomes NoChange.
Unknown dispatched provider/action outcomes and isolation/integrity failures
stop rather than blindly retry. State remains local and inspectable.

## Validation and limitations

```powershell
.venv/Scripts/python.exe -m pytest -q
$env:LUMINA_TEST_CORE_DOCKER="1"
.venv/Scripts/python.exe -m pytest Mind/test_core_loop.py -q
```

The fresh smoke uses scripted native model decisions with real isolated
calculation and IPython action: a Unicode CSV export, declared byte prediction,
actual measurement, feedback and restart. It proves the current mechanical
integration, not general model judgment or architectural superiority.
Historical behavioral conclusions remain in [EXPERIMENT_HISTORY](EXPERIMENT_HISTORY.md);
[CURRENT_STATUS](../../docs/CURRENT_STATUS.md) records current validation.

The baseline scope is one goal, a bounded authorized workspace and foreground
operation. Opt-in Stage1 adds persistent Intentions and serial Tasks within
explicit scope; its behavioral acceptance remains under validation. There is no
arbitrary-reality anomaly detector, background scheduler, unrestricted goal
creation or Chat/Memory/Dream wiring.
