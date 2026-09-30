# Stage1 implementation and bounded verification — 2026-09-10

The original 32-call result below is retained unchanged. Dated follow-ups retain
each continuation, failure and cost; the latest section describes the current
repair outcome. Later user authorization permits bounded cumulative extensions.

Verdict: **implemented; real functional acceptance is partial**. The same Mind
selected two distinct serial Tasks from actual measurements, revised an existing
belief, and delivered both original Directives. Task A completed its feedback
chain. Task B's document was written, but its result review and execution closure
remain pending at the explicitly frozen 32-call ceiling. This is not a fully
closed two-task acceptance or evidence of generally stronger autonomy.

Base: `Execution_lab2` at `734c9b39659d731a3dd86649c9f274898d1357dc`, plus the
preserved uncommitted unknown-action status fix and Stage1 changes. No commit,
push, history rewrite, Chat wiring or user-data cleanup was performed.

## Actual implementation

The existing cognitive journal, native continuation, mailbox transaction,
outboxes, Execution owner and provider ledger remain authoritative. New code
adds opt-in versioned Intentions and immutable serial Task contracts, fixed
attention selection, registered owner queries, `source.changed` and one-shot
`review.due`. No external framework/code was transplanted. Original authority
survives Task changes; task-specific completion tokens prevent A completing B.

Focused review caught and fixed hidden-cognition query routing, scenario
dependency expansion, selection against a proposal instead of the accepted
Task, and cross-Task source/background visibility. Same-cycle prediction and
source Watch notifications share one judgment while retaining both original
events and crash-safe responsibility. Source changes observed as A→B→A remain
distinct occurrences. The pre-existing P2 status repair retains both an
unsettled action start and a persisted unknown kernel result after restart.

The live first activity revealed a schema inconsistency: arbitrary new effect
IDs passed the field schema but failed as stale revisions. The narrow
`intention-effects-wire-v2` schema now describes `new:label`, generated existing
IDs, and their revision convention. Stored accepted versions and old failed
requests were not rewritten. The model supplies its own corrected answer.

## Validation

| Check, after relevant final changes | Result |
| --- | --- |
| `pytest Mind Nervous Execution -q` | 603 passed, 6 skipped |
| `pytest -q` maintained tree | 936 passed, 30 skipped; two existing upstream warnings |
| Docker opt-in: core loop, world model, isolated history | 31 passed, scripted model replies |
| Scope/dependency/identity, taskless, A/B, stop/revoke, query and crash regressions | Included in the maintained suites; no live-model inference from test counts |
| Whitespace, local instruction/document links and CLI help | Checked |

Docker ran before the final field-schema and cognition-query adjustments; those
changes were covered by the final core/full suites. The earlier 821/30 release
result is historical, not this verification. Baseline remains the default;
summary/masking and pursuit mode remain explicit options.

## Real run and causal evidence

Private records are under `.test-state/intention-stage1/`: preregistration,
per-launch code hashes and CLI commands, all provider wires/responses, organ
journals, measurement hashes and an independent developer audit. The business
workspace contains only copied inputs and Lumina-generated `reports/` documents.
Astra ran pytest, copied actual JUnit measurements and a current test excerpt,
then independently checked outputs; it did not choose either Task, replace a
model answer, write a business report or issue Execution's operations.

| Stage | Observed behavior |
| --- | --- |
| No initial Task/Intention and no measurements | One bounded activity accepted scoped knowledge and NoChange; attempted effects were abandoned after malformed-ID failures. No fabricated work started. |
| First genuine input | Mind read the measurement and cognition catalogue, committed one Intention, proposed Task A and registered the source Watch. |
| Task A | Original guidance entered the bound Execution request. Execution wrote the first report; Mind read source and report and accepted feedback; Run completed. |
| Unrelated PowerShell background message | One call, NoChange; Intention and Task state unchanged. |
| Later genuine measurements + current test source | Plain `resume`, with no new owner judgment, detected the watched file change. Mind read both sources, revised the existing batch belief and open question, then proposed distinct Task B under the same Intention/Mind. |
| Task B | Original guidance entered its own Execution request. Execution wrote the later report. Feedback began and read two evidence results, but final cognitive submission did not occur before the cap. |
| Recovery | Pre-input quiet restart added zero calls. At-cap restart preserved the same pending result, calls and business files; no action replay or automatic extension. |

The later report contains all five supplied record hashes and ordered count
tuples, preserves the early failure, and distinguishes injected/Docker tests
from live model semantics. Input hashes stayed unchanged. Nevertheless, both
reports retain the imprecise phrase “预期 2 条信号” where the assertion measures
`Script.wires` (model requests), not signal count. The current open question also
says the failure is resolved on the strength of later passing results; this must
not be read as proof of the original implementation's root cause. These semantic
limits were recorded after the run, not secretly sent back as runtime answers.
Task B's entire final effective cognition is therefore **not certified**.

## Cost and retained stop

Frozen allocation: 32 calls, 524,288 output tokens, 4,000,000 request bytes.
Actual: **32 calls = 19 Mind + 13 Execution + 0 Analysis/summary**; 417,792 reserved
output tokens; 1,247,950 serialized request bytes; provider-reported 290,066 input
tokens, 59,579 output tokens and 47,232 cache-read input tokens (not added again
to input usage). Provider transport failures: zero. Cognitive submissions
rejected before commit: four — three malformed effect identities and one missing
top-level `type`; the latter used existing bounded recovery.

All calls used official `deepseek-v4-pro`: Mind thinking enabled/low and 16,384
output limit; Execution thinking disabled/temperature 0 and 8,192 output limit.
No model/configuration migration or budget refresh. The six-call initial
activity consumed 31,055 output tokens. Execution's repeated inspection and
completion work also consumed the remaining margin; two reserved feedback calls
were sufficient to obtain evidence but not to submit Task B's final review.

Stop: `provider_budget_exhausted`; Execution retains `feedback_budget_reserved`;
one known `evidence.result` awaits the same active Mind. Unknown action/provider
outcomes are absent. A normal `status` is read-only; `resume` with the unchanged
allocation remains paused with zero additional calls. No further real calls are
authorized by this stage's frozen budget.

## Review and disposition

**Standards:** the scenario-read dependency defect was fixed with red/green
native-continuation tests. No remaining documented-standard blocker was found.
**Spec:** hidden-query callback/catalogue and accepted-Task selection defects
were fixed and regression-tested. Full two-task live closure remains unverified;
that limitation is not replaced by deterministic test success.

Keep the new mode opt-in. The single next recommendation is a separately
authorized continuation from this preserved Task B checkpoint to finish its
review and report correction; do not rebuild the organs or promote this small
run into a general autonomy claim. No continuation was performed beyond the cap.

## Authorized narrow repair and original Task B continuation — 2026-09-12/13

Follow-up verdict: **offline contract repairs verified; original Task B closed;
report correction and complete cognitive reconciliation remain PARTIAL**.
The full requested follow-up is not complete. No Stage2, Chat/UI/Memory wiring,
summary-default change, commit or push was performed.

### Actual diff and offline evidence

Baseline: `9bdfa1e17c8eccaee042ad628514851b156af179`, with the existing local
narrow-repair work preserved. `Mind/intention.py` checks the candidate Intention
set against the actual accepted Task: closing its Intention while the Run is
nonterminal is rejected. A latest proposal's `not_accepted` status cannot stand
in for completion of a different accepted Task. No cancellation mechanism or
Nervous action choice was added.

`Mind/cognition.py` supplies the same checks to native preflight and final
durable commit. Retiring a referenced cognitive item with `current` or
`archived` requires explicit same-commit repair of the Intention, including
Intentions not otherwise updated. Whole-state identity checks retain hidden
items without projecting their bodies; an exact current-owner read expands the
visible set before retirement. New activities freeze `pursuit-commit-v2`; old
accepted records and the original unfinished activity retain their old rules.

The original negative cases failed before repair: running/waiting closure and
both forms of dangling reference were accepted. The new regressions cover their
rejection, legal settled closure/pause, proposal-versus-actual status, hidden
items and owner reads, final-owner rejection without native preflight, and old
successful/pending activity recovery. These are structural tests, not model
semantic-success measurements.

| Actual follow-up check | Result |
| --- | --- |
| Focused contract/intention/wire suite before the two added read-composition cases | 51 passed |
| Final narrow regression file, including hidden read then retirement | 14 passed |
| Mind/Nervous/Execution core, collected before those last two test cases | 615 passed, 6 skipped |
| Final maintained tree, including all new cases | 950 passed, 30 skipped; two existing upstream warnings |
| Existing Docker computation/action/feedback/restart smoke through the affected cognition path | 1 passed; injected replies, no real provider calls |
| Standards review / Spec review | No outstanding implementation findings; suggested read-composition regression added |

Private evidence is in `.test-state/intention-stage1-followup-20260912/`:
`preregistration.json`, `backup-manifest.json`, full `backup/state` and
`backup/workspace`, JUnit reports, `owner-feedback.txt`, `audit.py`, `audit.json`
and before/after status snapshots. Original CLI launches and per-launch code
hashes remain in `.test-state/intention-stage1/live/developer/` under
`followup-resume-20260912`, `followup-owner-audit-20260912` and
`followup-quiet-20260913`. These private records are not runtime dependencies.

### What the real continuation did

Before any request, Astra copied all 104 private-state and 5 workspace files
outside the observed directory and verified an exact stable copy. The original
32 requests, same pending evidence result, same Mind/Intention/Tasks and input
hashes were checked. Docker Desktop was started because its local service was
not running; model and runtime configurations stayed fixed.

The CLI extended the original state exactly once by 12 requests, 196,608 output
allocation tokens and 2,880,000 request bytes. Calls 33–38 resumed the original
`evidence.result-ab2e74db489e9f9f434e020b` and activity
`mind-088c0cce15cc849179da83b7`. Call 33 omitted the required top-level `type`;
the existing bounded recovery accepted the model's own correction at 34.
Execution checked its existing document, wrote its completion marker and claimed
completion at calls 35–37; Mind accepted the result at 38. Original Task B Run
`execution-b0ab4e1d554c48518c690d5ea3efee07` is now durably completed. Its original
Directive receipt remains linked to that final review. This closes the original
execution/feedback chain, not the subsequently identified semantic defects.

Only after that closure, Astra sent the explicit owner audit message through
normal CLI `--message` with submission ID `stage1-followup-audit-20260912`.
It identified the request/signal confusion and the limit of causal conclusions
from later passing runs; it authorized only corrections of the existing reports
and cognition. This is recorded developer intervention, not autonomous discovery
or a new measurement. Astra did not write either business report or edit any
cognitive record.

Calls 39–42 read the actual source excerpt/first report and requested old
cognition. Two combined owner-view reads returned honest `capacity-v1` results;
the model then narrowed to one item. Call 43 revised the existing acceptance
beliefs `item-9a1aa588807f6cf9f194` and `item-f8318d1c6d238715f370` to identify
the report wording defect and submitted original high-level corrective guidance
for both reports. It did not choose a third Task.

Directive `directive-bc2162f22660ee314ae121ae` was **bound**, verbatim, to a linked
Run `execution-2d80296fc629451481e56ee2a81b9e75` under the same Task B. It was
**not received in an actual Execution model request and not adopted**: with only
one request remaining, the existing two-call feedback reserve stopped action
admission. The resulting budget event invoked Mind at call 44. That response hit
`max_tokens` (16,384), contained only a thinking block and no tool return, and
ended as `model_failed`; it was not converted to NoChange. Transport succeeded,
but cognitive submission did not.

### Final state, limits and cost

Both report byte hashes are unchanged from the backup, so their corrections
are **not delivered**. The existing open question
`item-69fc594ff323a01f0216` still contains the overclaim that later passing tests
show the failure was resolved. The initial “no material, so no task yet” advice
also remains current despite later measurements. Call 43 submitted new
request/causality claims but omitted them from its own `current` selection;
they are retained in history, not in effective cognition. The two corrected
acceptance beliefs do not certify the entire current state.

The linked correction Run has zero decisions/actions and retains
`provider_budget_exhausted`; the Directive remains bound and unreviewed. Mind
has one explicit failed activity, `mind-db25f9533d410f74cc2d77fd`, and no active
activity. Empty transport mailboxes and a null top-level foreground stop reason
do not mean all responsibility is settled: the organ-local stop, unreceived
guidance and failed cognitive review remain authoritative. The Intention stays
paused. Mind cancelled its source Watch while closing the original two-task
investment; Astra did not clear it to manufacture an empty queue.

| Cost | Original 32 | Added continuation | Cumulative |
| --- | ---: | ---: | ---: |
| Requests | 32 | 12 (Mind 9, Execution 3) | 44 (Mind 28, Execution 16) |
| Reserved output tokens | 417,792 | 172,032 | 589,824 |
| Serialized request bytes | 1,247,950 | 748,048 | 1,995,998 |
| Reported input tokens | 290,066 | 195,234 | 485,300 |
| Reported output tokens | 59,579 | 39,212 | 98,791 |
| Cache-read input tokens, already part of reported input | 47,232 | 19,328 | 66,560 |

The final allocation is 44 requests / 720,896 output tokens / 6,880,000 request
bytes. Analysis and summaries made no calls. All requests retained official
DeepSeek-V4-Pro, Mind thinking enabled/low with 16,384 output and Execution
thinking disabled/temperature 0 with 8,192 output. Provider transport errors:
zero; added rejected native submissions: one recovered missing `type`, one
unrecovered output exhaustion. Both capacity results and all failures remain.

Independent audit confirms all 32 original provider files are byte-identical;
the 22 original cognition journal records, 14 native/cognitive Trace prefixes,
two original execution log prefixes and original Nervous events are preserved.
Mind, Intention and the two Task identities are unchanged. Original inputs keep:

```text
observations.json  91caf46e9869864195183a6575963b02e4ff7bdb9e290704698ccc763b3efb14
test_excerpt.txt   77762b5b7aeab3f7509dfb04c359c06f65407cb901a1c829a5d5b007468de6ab
```

A final ordinary `resume` added zero calls and did not replay actions or change
the reports. No unknown provider or action outcome is present. No extra request,
retry-review, new session or budget extension was used to disguise the limit.

**Remaining blocker:** the authorized 44-request allocation is exhausted before
report correction, old-question repair and correction feedback closure. Keep the
preserved scene; any further real continuation requires separate explicit budget
authorization. Do not promote this result to full Stage1 behavioral acceptance
or begin Stage2. The next work remains this same unfinished correction chain.

## Structural recovery follow-up — 2026-09-13

The owner subsequently authorized further repairs and bounded automatic budget
extensions. This supersedes the earlier 44-call stop authorization, not its
recorded result. The baseline is local HEAD `9bdfa1e` plus preserved uncommitted
Stage1 changes. No commit, push, Stage2 or third Task is part of this work.

### What failed and what changed

The 45–60 continuation delivered the original corrective Directive at call 46.
Execution read the actual inputs and rewrote both reports at 48–49; Mind read
those results and accepted their scoped corrections at 54–55. The reports now
identify `Script.wires` as model requests, preserve the historical failed run,
and distinguish later passing measurements from proof of an earlier defect's
cause or repair. Astra did not write those reports.

That continuation exposed defects beyond a missing quota:

- The completed feedback did not continue the deferred completion claim; the
  model was implicitly expected to claim again. Calls 56–60 instead alternated
  completion prose and repeated checks. Execution now revalidates and continues
  the same claim at its unchanged decision boundary after review. It does not
  scan past later actions or silently discard a saved provider decision. A
  missing original request binding remains an explicit recovery block.
- A mandatory complete cognitive owner read inlined all basis text, so an old
  causal question could not fit even when requested alone. The new
  `cognitive-item-v2` view returns the complete target with original references;
  evidence and scenario assumption bodies are separate existing reads. Exact
  owner identity/version checks and old inline receipt interpretation remain.
- Unselected old entries had only nonsemantic headers. Their catalogue now
  includes at most 240 characters of original item text with a truncation flag.
  It is a selection aid, not evidence or permission to revise without reading.
- Call 43 created correct new explanations but omitted them from its separate
  `current` list. New pursuit activities freeze `mind-cognitive-interface-v2`:
  updates add/replace items and explicit `archived` updates retire them. Old
  activities keep their original selection contract; no historical state is
  retrospectively promoted.
- Calls 44 and 45 exhausted 16,384 output tokens each without a tool submission.
  A known complete text/thinking response can now receive the activity's one
  shared protocol correction. Original provider blocks, request, cost and
  failure remain; no fake tool result, semantic rewrite or unknown-call replay
  is introduced. This repairs the exit mechanism, not model reasoning quality.

Call 45 was also contaminated by **Astra's input preparation**: its quota
notification had become question marks before reaching the CLI. The actual
provider request preserved that corrupted input. It is not a clean test of a
quota change and does not explain the independent call-44 failure.

### Same-state semantic recovery at calls 61–76

The complete 60-call state and workspace were copied before mutation. A normal
owner message identified the stale items and the prior input corruption; its
UTF-8 argument and actual provider source were checked byte-for-byte, including
CRLF. This is disclosed developer audit intervention, not autonomous discovery
or a new regression measurement. One 16-call batch was registered before use.

Call 61 read the three complete old items in one bounded request. Call 62
accepted revision 11 with NoChange: the causal question `69fc` now says later
passing results do not establish the original defect's repair; `feab` was
explicitly archived with its correct historical scope; `38aa` was closed using
the supplied measurement records. The correct initial-snapshot knowledge
`5b99` and other valid knowledge remain. An independent review checked all 13
effective items and both reports; it did not supply an answer to the runtime.

This establishes repair of the retained cognition in this audited scene. It
does **not** establish execution closure. The old claim had already been
followed by actions, so the new safe-boundary continuation could not reuse it.
Call 63 legally corrected then retired the old pending provider decision after
owner change, without dispatching its stale action. Calls 64/66/68 produced
completion prose; subsequent calls repeated the same check code until 76.
The original two failed cognitive activities also remained visible.

Inspection of actual calls 64 and 70 confirmed current RUNNING and cleared-review
facts in the first message. However, six historical native rounds followed,
and the last user message contained only the original old Directive. This is
a message-ordering defect, not lost event delivery. The next bounded recovery
changes that projection: stable attributed guidance belongs in owner context,
and the current decision checkpoint follows the historical rounds. It does not
select a completion action for the model or modify a frozen request.

### Costs retained through call 76

| Calls | Mind / Execution | Request bytes | Reported input tokens | Reported output tokens |
| --- | ---: | ---: | ---: | ---: |
| 1–32, original | 19 / 13 | 1,247,950 | 290,066 | 59,579 |
| 33–44 | 9 / 3 | 748,048 | 195,234 | 39,212 |
| 45–60 | 3 / 13 | 620,110 | 134,821 | 25,902 |
| 61–76 | 2 / 14 | 537,737 | 99,863 | 12,909 |
| Cumulative | 33 / 43 | 3,153,845 | 719,984 | 137,602 |

Reserved output totals 892,928 tokens; cache-read input totals 151,168 tokens
and is already included in reported input. Builder and summary calls are zero.
Every real request uses the existing official DeepSeek-V4-Pro configuration:
Mind thinking enabled/low, Execution thinking disabled/temperature 0, baseline
working context. Provider transport errors are zero. Thinking exhaustion,
no-tool responses, capacity failures and repeated actions are retained.

The 76-call scene was separately backed up before the projection repair. The
next batch is bounded to eight calls, 131,072 output tokens and 560,000 request
bytes; it resumes this same state, then uses normal `--retry-review` for failed
activities. No new business-answer message is supplied for that comparison.

### Final recovery and outcome at call 96

Calls 77–84 all repeated the check. **The ordering change alone did not resolve
this scene's behavioral stall.** No further same-input rerun was used to select
a successful sample. A new bounded twelve-call phase instead submitted the
actual repeated-action/unchanged-file/RUNNING observations through normal CLI
`--message`. Astra reported the operational evidence and asked Mind to judge
the current phase; it did not prescribe business text or an Execution tool.

At 85, Mind distinguished business completion from the still-running lifecycle,
updated a supported stage belief, and chose NoChange. Execution then inspected
the allegedly offending phrase in context (86–88), checked actual measurement
records and acceptance (89–91), wrote the existing exact marker (92), and
submitted `claim_complete` (93). Its checks now recognized that the phrase was
being negated in the already-correct report. All business/input/marker bytes
remain equal to the 60-call backup. Mind accepted the real completed observation
at 94, updating the same stage belief; the Run reached state version 118.

This exit followed disclosed developer runtime feedback. Neither 85 nor 94
issued a Directive, so it is not evidence of benefit from a new Mind instruction
or autonomous stagnation discovery. The original report-correction Directive
was genuinely delivered and implemented at 46–49; those are separate facts.
The original deferred claim was not retroactively completed: after its later
actions, Execution legitimately made the new claim at 93.

Two ordinary `--retry-review` calls then settled the old failed activities:
95 reassessed the call-45 corrupted message without inventing meanings for its
surviving numbers; 96 reassessed the call-44 budget matter with NoChange. The
CLI selected the most recent failure first; the developer command-record label
for 95 says `44`, but the actual activity linkage identifies **45**. Both
original activities remain failed in history, with explicit `superseded_by`
links to accepted new activities. No active activity was replaced or erased.

Final state: same Mind, one paused Intention, the original two Tasks and linked
correction Run; Mind revision **15** with **15** effective items; Run
`execution-2d80296fc629451481e56ee2a81b9e75` is **completed**. Active activities,
unresolved reviews, transport mailboxes and feedback obligations are empty.
Original open root-cause uncertainty remains; it is not an unresolved runtime
responsibility. The prior Watch retirement is unchanged.

The final budget was given four unused calls of headroom before checking
quiescence. That resume and a second ordinary restart both added **zero** calls
and preserved accepted state. Thus quiet operation was not forced by exhaustion.
Limits are now 100 calls / 1,638,400 allocated output tokens / 10,800,000 request
bytes; used requests remain 96. No additional goal, Task, world-model capability
or background scheduler was created.

| Calls | Mind / Execution | Request bytes | Reported input tokens | Reported output tokens |
| --- | ---: | ---: | ---: | ---: |
| 77–84, ordering-only continuation | 0 / 8 | 250,808 | 44,752 | 2,866 |
| 85–94, disclosed runtime feedback | 2 / 8 | 425,871 | 102,320 | 9,571 |
| 95–96, failed-review recovery | 2 / 0 | 144,614 | 40,144 | 4,610 |
| This repair, 61–96 | 6 / 30 | 1,359,030 | 287,079 | 29,956 |
| Entire preserved task, 1–96 | 37 / 59 | 3,975,138 | 907,200 | 154,649 |

Final reserved output is 1,089,536 tokens. Reported cache-read input is 195,840,
already included in input; cache creation and provider transport errors are
zero. Analysis/Builder and summary calls remain zero. The new no-submission
correction mechanism is covered deterministically; these successful final Mind
calls did not require that correction, so this scene does not newly demonstrate
its real-provider behavior.

Independent audit verified all 60 earlier provider files byte-for-byte, the
38-record cognition-journal prefix, 27 original Trace/log byte prefixes and
78 original Nervous events. Input hashes remain the values recorded above.
The full final cognition and both reports were reviewed, not only the last
reply or retired IDs. Correct initial-time knowledge remains; the old causal
overclaim is corrected and the obsolete current-wait premise has exited.

### Validation and scoped decision

Focused projection/recovery/core-loop tests: **129 passed, 1 skipped**. Before
the final message-order adjustment the maintained tree had 973 passes and six
Recall test setup refusals caused by an in-repository temporary path; all six
passed at their required external temporary path without changing Recall.
The final projection run had **980 passed, 1 failed, 30 skipped**; the one test
still looked for guidance in the old trailing message. Its assertion now reads
the owner context and checks the final checkpoint, preserving the original
delivery invariant; its complete runtime test file passed **26** tests.
The subsequent final maintained run, using a fresh external temporary directory,
passed **981 tests**, skipped **30**, and emitted only the two existing upstream
deprecation warnings. Document links and `git diff --check` also passed.

The Docker core/history invocation passed **18**, including actual isolated
computation, action, feedback and restart, and had one Windows atomic-replace
access refusal in a separate prediction test. That test passed isolated
recheck. These tests use scripted model replies. The real task's Execution
actions used its existing isolated runtime; their provider use is counted above.

**Standards review:** no remaining scoped blocker after fixing scenario reads
that still inlined large assumption bodies. Sources remain owner-authenticated,
old requests are preserved, and the message-order change adds no action authority.

**Spec review:** no remaining blocker for this controlled correction and Task B
closure. Both original failed review responsibilities are settled by accepted
linked activities; current cognition, outputs, identities and quiet restart were
independently checked. Original PARTIAL and failed continuation verdicts remain.

Remaining limits are explicit: unusually escaped single cognitive records can
still exceed the bounded owner-read envelope; current belief basis arrays may
not individually cite every fact from the authoritative activity observation.
The final completion statement has actual Execution observation support in
call 94, but its basis list alone is not an exhaustive proof of every clause.
There is no new autonomous stall detector, and no broad model-reliability or
architecture-advantage claim. The sole next development recommendation is to
evaluate an explicit Execution-to-Mind reassessment event when progress stalls,
using real progress evidence; do not add another semantic gate or extend Stage2
as part of this already-completed correction.

Normal supported commands remain:

```powershell
.venv/Scripts/python.exe -m Mind status --state <existing-state-directory>
.venv/Scripts/python.exe -m Mind resume --state <existing-state-directory>
# Only for an actual later owner observation:
.venv/Scripts/python.exe -m Mind resume --state <existing-state-directory> --message '<actual evidence>' --submission-id '<new-submission-id>'
# Only for an explicitly unresolved failed cognitive review:
.venv/Scripts/python.exe -m Mind resume --state <existing-state-directory> --retry-review
```

The local command records, registration files, recovery copies and raw evidence
remain in task-owned private test state; they are not runtime dependencies.
