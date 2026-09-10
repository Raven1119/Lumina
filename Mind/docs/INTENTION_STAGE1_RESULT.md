# Stage1 implementation and bounded verification — 2026-09-10

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
