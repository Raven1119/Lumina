# Standalone Mind—Nervous—Execution chain

The supported entry is `python -m Mind`. Current routing/input profile is
`cognitive-chain-v73`; selective cognitive submission remains V67.
The chain owns one authorized goal and one bounded workspace. Production Chat
still uses its separate Recall gate; this CLI does not wire Chat or Memory.

## Run and recover

Run from the repository root with the prepared environment and Docker available.
The runtime reads `DEEPSEEK_API_KEY` through the existing environment loader.
Keep the session directory outside the business workspace. Do not mount the
repository, credentials or development logs into the execution workspace.

```powershell
.venv/Scripts/python.exe -m Mind start --session .mind-sessions/task1 --workspace <authorized-workspace> --goal-file <goal.txt> --max-calls 40 --max-output-tokens 655360 --max-request-bytes 6000000
.venv/Scripts/python.exe -m Mind status --session .mind-sessions/task1
.venv/Scripts/python.exe -m Mind resume --session .mind-sessions/task1
.venv/Scripts/python.exe -m Mind resume --session .mind-sessions/task1 --message "<actual new information>"
```

Use a new session name only for a genuinely new task, not to reset a campaign's
cost. Status and a quiet settled resume do not call a model. Later owner messages
become Nervous events for the same Mind; they do not require an Execution wait.
A completed run can have a legally linked successor if owner input and accepted
Mind guidance warrant further work. The original formal goal remains unchanged.

After resolving a known failed, quiescent review, use `resume --retry-review`.
The original failure remains recorded. Unknown dispatched action/provider outcomes
require reconciliation; repeated sampling is not recovery. Additional explicitly
authorized quota uses `--add-calls`, `--add-output-tokens` and
`--add-request-bytes`; historical spend is retained. A depleted budget does not
prove business completion. See `python -m Mind --help` for optional settings.

## Responsibility and information flow

```text
owner goal/message -> Nervous -> persistent Mind
Mind -> optional read_evidence / analyze_world_model -> same activity
Mind -> cognitive_step -> NoChange or original-text high-level Directive
Directive -> eligible Execution decision -> actual work
important Execution request/result or registered observation -> Nervous -> Mind
```

Mind owns goal interpretation, material conditions, uncertainty, stage priorities
and direction. Execution retains implementation and local correction. Builder is
a temporary independent analysis role, not a second decision owner. Mind and
Builder have no business filesystem, shell or IPython authority. Trusted owners
persist and deliver their bounded outputs.

Execution can call `request_mind(question, evidence_files=(), model_ref="")`
inside an ordinary IPython cell. This publishes a request after the cell commits;
it does not require a business Wait. Ordinary tool feedback stays in Execution.
Foreground control yields at known completed-action boundaries so relevant
pending events and outstanding feedback get an opportunity. It is not a
background scheduler or a rule that every action needs Mind approval.

Mind sees the owner business goal, relevant current cognition, attributed source
records and the important event. Execution receives the real task, local state
and original guidance. Builder receives its question, selected evidence and an
optional previous model. Full reasoning and debugging are not copied between
roles. Original evidence, requests, histories and model artifacts remain in the
local session and can be read by reference.

## Cognitive revision and closure

Native read/analysis calls commit no provisional cognition. The same activity
can consult and continue, then submit only changed items through cognitive_step.
Omitted existing items remain; an explicit current selection or archived status
retires obsolete knowledge. Trace preserves prior versions. Current belief input
uses prior_truth (true/false/null) as a lossless representation of the previous
supported/contradicted/open judgment. This is not verified reality and the host
does not flip a status for the model.

A belief's submitted status evaluates its literal new claim. Material conditions,
scope, source basis and useful unresolved tests must remain consistent when a
claim changes. A correct sentence about an earlier error can itself be supported.
Both discovering an error and revising old cognition remain model judgments.

NoChange can accept a correct result and end a cognitive activity without new
business work. It does not prevent Execution from finishing. Directive conveys
a new decision, material conditions, decisive evidence or gap and its acceptance
or priority implication. It is not a completion acknowledgement or a request to
repeat an already satisfactory report. Completion protocols remain Execution's
responsibility. DecisionIntent is represented but formal goal switching is not
implemented by this chain.

A Directive is delivered once to an applicable run/decision. Persistent display
through later local actions is not a second delivery or evidence of adoption.
New events and guidance retain provenance and ordering. Stale unreceived bindings
are not blindly applied; actual delivery and result feedback are tracked.

## World-model analysis and prediction

Mind decides whether analysis is useful. Builder may answer directly, compute
named quantities with predict(inputs, action), or use stateful simulation.
Programs are optional. Code generation, debugging and reruns stay isolated;
the compact report distinguishes answer, assumptions, unknowns and actual run_ref.
Use model_ref="" for new analysis. Reuse/revision copies an existing opaque
model reference; a descriptive title is not a model handle.

Ordinary calculation creates no prediction obligation. A prospective comparison
also declares observation_file and check_spec: candidate action, conditions,
object, time, quantities, meaning and units. The model and run are saved before
the later observation. Relevant observation changes enter the existing event
loop, which checks binding applicability before quantities. Missing observations
remain unverified; changed conditions are not applicable; missing/unmappable
quantities are incomparable. Comparison is limited to declared final fields.

Mind receives differences and source references and decides whether the model,
assumptions, interpretation or direction needs revision. Numerical agreement
does not certify premises, causality, an entire trajectory or general model
accuracy. The current mechanism observes bounded workspace records, not arbitrary
outside reality. Text projection uses UTF-8-sig; character counts are not general
raw-byte certification and BOM can be stripped.

## Persistence, budgets and failure

Nervous owns durable request/result events and idempotent acknowledgement.
Mind owns accepted cognition; Execution owns actions and reality records.
The chain persists feedback obligations and model/decision references.
Only a final accepted submission changes effective cognition. Explicit errors,
pending/failed understanding and remaining resources are visible through status.

DeepSeek-V4-Pro through the official Anthropic-compatible API is the runtime
model. Default Mind and Builder thinking are enabled with low effort;
Execution thinking is disabled. The existing activity and chain budgets include
all roles, repairs and provider requests. The cognitive activity is bounded
at six model calls; evidence/analysis continuation does not reset its quota.
Current whole cognitive submission is at most 6000 characters and 16 updates,
with 16000 characters of active knowledge. These are versioned implementation
bounds, not principles that future evidence cannot change.

A received but uncommitted action and a connection failure before dispatch have
different recovery semantics. The runtime must not replay unknown side effects.
Failed important reviews remain explicit; they are not converted to NoChange.
Quiet waiting and successful restart do not themselves prove semantic correctness.

## Validation and current scope

```powershell
.venv/Scripts/python.exe -m pytest Mind Nervous Execution tests -q
```

Deterministic tests use temporary state and scripted model responses. Docker
checks explicitly marked opt-in remain separate. Real provider campaigns are
not part of the ordinary test suite and require bounded authorization.

Current functionality supports a single-goal foreground task with persistent
judgment, sparse direction, actual execution and feedback. V70-V73 demonstrated
scoped retained-error recovery and a fresh file task, not general unattended
reliability or independent-context superiority. The [history summary](EXPERIMENT_HISTORY.md)
retains failures and inconclusive outcomes. [Current status](../../docs/CURRENT_STATUS.md)
distinguishes implementation from evidence; [references](REFERENCES.md) records
actual third-party reuse.

Raw experiment runs, copied runtime versions and phase-by-phase result/review
documents were removed by owner instruction during repository cleanup. Future
local sessions and generated records belong in ignored directories; maintained
tests keep small synthetic/static inputs, not archived provider bodies.