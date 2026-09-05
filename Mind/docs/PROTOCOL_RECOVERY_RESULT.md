# D4 result: bounded recovery and a real feedback chain

2026-09-05. **A supervised, traceable Mind → Execution → Mind chain ran with
real models. The full frozen loop criteria did not pass. Independent Mind's
behavioral advantage remains `INCONCLUSIVE`; comparison eligibility is
`NOT_YET`.** No campaign was repeated and no historical verdict changed.

## What was reused and what changed

Local branch and remote `Execution_lab2` were both
`9d63da7311baa7611782cc8079fb09e9d81f4e25`. The D1/P0/D3 uncommitted increments
were read and retained. The D4 diff baseline is the frozen
D3 acceptance source??????`../fixtures/cognitive_contract_d3/acceptance/source/`?,
not an assumption that everything absent from remote HEAD is new D4 work.

Direct reuse: persistent Mind cognition/reducer, inspect/read continuation,
Nervous durable causal events, logical Trace and one-time Directive binding,
qualified Execution bridge, native `cognitive_step`, isolated ordinary IPython,
and D3's three-event workspace runner and objective checker. Length/reference
alignment and legal concrete high-level directions were already D3 work.

This round corrected two demonstrated contract gaps:

1. The prompt/reducer allowed qualitative `scenario`, but the native schema did
   not. D4 represents the existing scenario shape and bounds. This does **not**
   explain D3's empty parameters or add World Model computation.
2. A clearly rejected incomplete submission had no bounded chance to complete
   its expression. D4 permits one original-model correction per activity for
   missing/type-invalid parameters, before a cognitive commit or delivery.
   Raw response, field errors, actual correction wire and result are durable.

Version `cognitive-submit-d4-v1` opts into
`cognition-native-d4:3-calls:1-repair:1-read`: two logical steps, one read, three
physical calls maximum, one correction shared across the whole activity.
Existing 2,000-character cognitive output and 2,000-output-token call limits
remain. No host supplies missing references, edits guidance or chooses its kind.

Supported package diff: `Mind/trace.py` +124/−6 lines owns a bounded native annex
and main-Trace repair reservation; `experiment_a.py` +33/−2 uses the opt-in
invocation/replay seam; `organ.py` +19 reuses known uncommitted responses during
recovery. Experimental adapters/runners change `event_loop.py` +118/−7 and
`cognitive_contract.py` +75/−17. Existing loop tests change +9/−4; one new focused
237-line test file covers the new failures. There is no new submission organ,
review model, planner, scheduler, or D4 change to Execution/Nervous owners.

## Frozen conditions and entry

[Task and contract](PROTOCOL_RECOVERY_TASK.md),
registration??????`../fixtures/protocol_recovery_d4/registration.json`?,
safety gate output??????`../fixtures/protocol_recovery_d4/mechanism.json`?,
[complete campaign](../fixtures/protocol_recovery_d4/), and
reproducible accounting??????`../fixtures/protocol_recovery_d4/analysis.json`?.

Only `deepseek-v4-pro`, official
`https://api.deepseek.com/anthropic/v1/messages`, `DEEPSEEK_API_KEY`, explicitly
disabled thinking and temperature 0 were used. All 34 request and response model
fields match. No provider fallback, ordinary network retry, or failed-case rerun.
The [official compatibility table](https://api-docs.deepseek.com/guides/anthropic_api/)
supports `tool_result.tool_use_id` and body content but ignores `is_error` and
`disable_parallel_tool_use`; local cardinality checks and explicit body feedback
therefore remain necessary.

The campaign froze development 2 cases / 6 calls, independent acceptance 4 cases
/ 12 calls, and both three-event loop cases / 42 calls; 60 calls maximum, each
with 2,000 output tokens. Safety failures were hard stops; individual protocol
or cognitive failures did not cancel other independent cases. All cases ran
once. Every stage's source snapshots and current source hashes still match.

To reproduce **a separately authorized new campaign**, use a new directory:

```powershell
./.venv/Scripts/python.exe -m Mind.cognitive_contract register-d4 <new-directory>
./.venv/Scripts/python.exe -m Mind.cognitive_contract dev-1 <new-directory>
./.venv/Scripts/python.exe -m Mind.cognitive_contract acceptance <new-directory>
./.venv/Scripts/python.exe -m Mind.cognitive_contract loop <new-directory>
```

The runner announces `<hash>.pending.json`. A developer reads the frozen rubric,
original output/items and exact sources, then writes the matching
`<hash>.decision.json` with `request_sha256` and the required fields listed in the
request. The deadline is 300 seconds. This is a **supervised experimental entry**;
review accepts/rejects original text and is not an extra runtime model or an
autonomous semantic validator. Semantic warrant review occurs after the owner
has structurally accepted cognition; withholding a Directive does not erase an
accepted but semantically flawed belief. The existing campaign directories refuse
reuse. Offline accounting needs no API:

```powershell
./.venv/Scripts/python.exe Mind/fixtures/protocol_recovery_d4/analyze.py
```

## Quantitative results

"Without repair" means an activity completed its allowed logical steps without
using the correction credit; an ordinary evidence read is not a correction.

| Stage | Activities | Without repair | Final structural acceptance | Full frozen result | Provider calls |
| --- | ---: | ---: | ---: | --- | ---: |
| Development | 2 | 1/2 | 2/2 | 2/2 pass | 4 |
| Independent interface acceptance | 4 | 2/4 | 4/4 | 4/4 pass | 7 |
| Real loop | 6 | 6/6 | 6/6 | 1/2 task cases pass | 23 |
| Total | 12 | 9/12 | 12/12 | No aggregate behavioral PASS | 34/60 |

Three natural empty `{}` inputs occurred: development d2 and acceptance a3/a4.
Each was one complete `tool_use` with no truncation, missing `type`, `updates`,
and `next`. Each recovered on its sole correction and independently chose
`NoChange` with nonempty cognition. The feedback preserved the entire rejected
assistant response and matched its tool ID; it contained only field errors and
contract instructions, no task answer or direction hint. All original failures
remain in call files and native annexes. There were zero exhausted corrections,
unknown provider outcomes or final protocol failures. This small sample does not
establish a general 100% recovery rate.

There were 14 logical Mind steps, 11 accepted on their first physical attempt;
the three corrections brought final structural acceptance to 14/14 steps.
At activity level this is 75% without repair and 100% after bounded repair.
Semantic warrant passed 10/12 activities; the two failures are normal-control
events 2 and 3, not protocol failures disguised as `NoChange`.

| Cost group | Calls | Reported input tokens | Cache-read input tokens | Output tokens | Provider seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mind | 17 | 24,169 | 29,568 | 6,245 | 83.704 |
| Execution | 17 | 9,626 | 8,704 | 2,473 | 42.781 |
| All calls | 34 | 33,795 | 38,272 | 8,718 | 126.485 |
| Correction subset | 3 | 778 | 8,064 | 846 | 10.891 |

Token fields are preserved separately as reported by the provider; cache creation
was zero. Provider time excludes developer review and Docker/host time. Actual
cost never removed a sample. The correction subset is part of Mind/all totals,
not additional to them.

## Trace-level behavior

### License change: the controlled chain ran

[Full record](../fixtures/protocol_recovery_d4/loop/license_change/result.json),
Nervous records??????`../fixtures/protocol_recovery_d4/loop/license_change/nervous/`?,
Mind records??????`../fixtures/protocol_recovery_d4/loop/license_change/mind/`?.

One unchanged `publication-release` Intention, revision 1, spans all three events:

1. Execution independently writes the revision-1 release containing alpha/beta
   and naturally waits for `catalog_update`. Mind records two nonempty items and
   chooses `NoChange`.
2. The input owner supplies revision 2: eligibility changes to `release_time`
   and alpha's license is revoked. The same reopened Mind revises previous item
   IDs and explicitly submits a Directive: reassess current eligibility, exclude
   alpha and use current revision fields. Its original 286-character text is
   bound to the existing run and the next eligible root decision. The exact text
   plus the existing advisory wrapper appears in Execution call index 4's
   `wire.messages[3].content`. No private belief is converted into an instruction.
3. Execution says it will re-evaluate current inputs, reads the files, chooses
   its own Python filtering implementation, changes the release from alpha/beta
   at revision 1 to beta at revision 2, and completes. Owner event 3 returns
   through Nervous. Mind cites that source, promotes the prior open eligibility
   belief to supported, closes its earlier question and chooses `NoChange`.

The independent checker validates the full artifact against current owner inputs;
the marker alone is insufficient. Inputs remain unchanged by Execution. Cognition
revisions are 1 → 2 → 3, both prior IDs survive event 2, and owner reopen views
match the previous accepted views. Actual loop restart evidence is closing and
reopening the owners between events; process-exit recovery during a correction
was exercised separately with deterministic fault injection.

**Important limit:** before Mind's first activation, Execution call index 2 had
already implemented correct `approval_time` and `release_time` branches. The
observed change is current-input re-evaluation with advisory uptake, not proof
that Mind repaired an erroneous algorithm or was necessary for success. There
is no matched self-check arm and no counterfactual gain estimate.

A retained semantic defect also remains: event 2 rewrites the old claim to say
the staged release is stale, but labels that new statement `contradicted`.
The delivered direction is correct, and subsequent feedback updates a separate
open belief, but persistent IDs do not establish flawless belief revision.
This caveat was recorded in the original admission decision, not added to hide a
failed structural check.

### Normal control: completed task, failed cognitive criteria

Full record??????`../fixtures/protocol_recovery_d4/loop/normal_control/result.json`?.
The output sequence was `NoChange → Directive → NoChange`, rather than three
`NoChange`s. The proposed Directive correctly notes stale revision fields while
preserving alpha/beta; filenames and field values are legal high-level content.
It repeats routine work already required by the goal. Because the fixture really
does have a revision-number gap, this failure is **not proof of harmful advice**.

Separately, a supported belief's discriminator says that an inactive license
would require removal under `approval_time`. The source requires an active
license only under `release_time`. This is a concrete cognitive overgeneralization,
so the developer warrant gate withheld the original Directive. It was neither
edited nor turned into `NoChange`. The cognitive item had already passed the
owner's structural reducer and remains visible in the journal.

Execution continued without that guidance and independently produced the correct
revision-2 alpha/beta artifact. Final feedback updated the prior staged-result
claim, but the incorrect discriminator persisted and failed warrant review again.
Nonempty continuity and feedback use both passed; semantic self-correction did not.

Across the loop: objective task completion 2/2, full frozen criteria 1/2, two
Directive proposals, one actual delivery, one observed uptake, and zero observed
harmful delivered directions. The withheld proposal prevents any claim about its
unmediated effects. Positive Execution used 6 IPython actions + Wait + completion;
normal used 7 + Wait + completion. Both re-read/recomputed after input change;
neither had a failed IPython action. These counts do not measure avoided waste
without an equal-task counterfactual.

## Safety, validation and remaining limits

Registration hard gate: **38 passed**, including real Docker tests with scripted
Mind responses. Final targeted recovery/legacy-runner checks: **66 passed**.
Root regression: **347 passed, 24 skipped**. `git diff --check` passed; pinned
MAGMA has no status changes. See [review](PROTOCOL_RECOVERY_REVIEW.md) for test
attempts and the defects found/fixed before freezing, rather than treating all
intermediate attempts as successful.

The durable repair reservation prevents consumed credit from reappearing after
whole annex-tail loss. Unknown reserved calls are not resampled. A known response
must belong to the currently awaited logical phase; the narrow crash window
after saving a read but before reserving phase 2 conservatively fails. Storage
failure stops the campaign as a mechanism failure. Semantic errors, invalid
references, unsupported kinds, truncation and network uncertainty never enter
the correction path. Mind's native tool only submits data or requests the existing
read-only inspection; Execution's isolated Python authority is separate.

North Star gap: event attention, nonempty cross-event continuity and an original
direction/action/feedback chain now have real-model evidence. Reliable
conditional reasoning, coherent claim/status revision, selective non-intervention,
unsupervised semantic acceptance and long-horizon value are still unproved.
Only three events and one correction direction were tested. The task does not
establish independent Mind's general superiority or autonomous long-term operation.

**Only next recommendation:** freeze the D4 protocol and conduct one new small
cross-event semantic acceptance campaign, testing policy applicability and
claim/status consistency with a negative control that has no outstanding
delivery gap. Resolve that evidence gap before an equal-budget behavioral
comparison; add no new Mind mechanism for now.

## Artifact identities

- D4 registration: `809504d4dcc1d7f47e96eb452e8b8fa6c2c2a4791b1ee268546802d1ed85cea1`
- Development: `8006030fc0f2cb056a1775d22a206d51fce812d6db13134a144aa9c1d5f37ec5`
- Acceptance: `8253fd0f38aa659983074e0fc3167b2cf4d81be4065a3b78e20fb0d6a5e35ee8`
- Loop: `187ec5b7c1d944762575e260e62ad32141709f107243e958cb6b70509ca17a10`

These are the records' canonical self-hashes. D3 registration/development/
acceptance hashes still match their original values. D1, P0 and D3 verdicts and
raw records were retained. No commit, push, production promotion or wiring.
