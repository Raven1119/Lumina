# Mind-Nervous-Execution operating contract

The supported foreground entry is `python -m Mind`. The current core has
organ-owned state and a single current cognitive/native protocol; it does not
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
has no business mount; Execution mounts only the explicitly authorized workspace.
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

No pending information means quiet status/resume with no model calls.
To explicitly retry a failed cognitive activity after addressing its cause:

```powershell
.venv/Scripts/python.exe -m Mind resume --state .mind-state/task1 --retry-review
.venv/Scripts/python.exe -m Mind resume --state .mind-state/task1 --add-calls 8 --add-output-tokens 131072 --add-request-bytes 1200000
```

A retry is a new bounded judgment of the original matter with current evidence;
it does not erase the failed activity or reset cost. Unknown action outcomes
are not repaired by this flag. A legitimately needed follow-up after a terminal
Execution uses a linked run under the same formal goal.

## Owners and flow

| Responsibility formerly in Session | Current owner |
| --- | --- |
| Goal interpretation, activations, cognitive repair and outcome judgment | Mind/organ.py and cognition.py |
| Optional analysis, model reuse and comparison interpretation | Mind/analysis.py, world_model.py |
| Original input transport, mailbox completion, pumping, shared call accounting | Nervous/organ.py, provider.py |
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
explicit archived status/current selection retires obsolete knowledge without
deleting history. A revised claim, status, basis and optional discriminator are
one replacement. Status evaluates the new literal assertion, not task pass/fail.
The previous prior_truth is an earlier judgment, not certified reality.

NoChange can close a successful review, including after cognitive revision.
It does not erase previous guidance or create work. A Directive conveys the
decision, material conditions, decisive evidence/gap and business priority;
Execution chooses code and tools. DecisionIntent is representable but formal
goal switching is not implemented.

Execution binds guidance once to a still-applicable run/decision. Previously
received text remains visible across normal history trimming; visibility is
not another delivery or proof of adoption. If the position or evidence changes
before use, old advice is withdrawn and an attributed event returns to Mind
for a fresh judgment, retaining the original owner input.

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
Analysis is bounded to six calls/computations. World-model inputs have at most
16 scalar fields, with strings at most 256 characters.

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

The supported scope is one goal, a bounded authorized workspace and foreground
operation. There is no arbitrary-reality anomaly detector, background scheduler,
autonomous goal creation or Chat/Memory/Dream wiring.
