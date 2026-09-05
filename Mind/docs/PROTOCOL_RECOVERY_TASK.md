# D4: one bounded protocol correction and a real event loop

Authority: creator's new goal, 2026-09-05. Local HEAD is
9d63da7311baa7611782cc8079fb09e9d81f4e25 on Execution_lab2, with D1/P0/D3 dirty
increments retained. D3 remains INCONCLUSIVE and is not rerun or relabeled.

## Scope and seam

Reuse MindOrgan, Nervous, logical Trace, Directive binding, native adapters,
isolated IPython and the existing three-event runner. Mind decides direction;
Execution implements it. No new planner, scheduler, organ, World Model or wiring.
Qualitative scenarios already supported by the reducer are now represented in
the native schema with the same bounds (1–3 assumptions/steps, 200-char step
fields, at most three unknowns). This removes the prompt/schema mismatch; it is
not evidence that scenarios caused D3's empty native input.

The only supported-package changes are Mind/trace.py, experiment_a.py and
organ.py. Trace owns a bounded native wire annex under the same activation ID;
the existing invocation seam supplies Trace to the opt-in adapter; Organ may
resume only its known, uncommitted native response. This extra surface is needed
because an in-memory retry counter cannot survive restart and the old owner
conservatively terminates interrupted inference. Execution/Nervous owners need
no new changes. Experimental adapter, runner and focused tests are incremental.

## Contract and durability

Version: cognitive-submit-d4-v1; native budget:
cognition-native-d4:3-calls:1-repair:1-read. At most two logical cognitive steps,
one read, three physical provider calls, and one correction for the ENTIRE
activity. Existing 2000-character serialized cognitive limit and field ceilings
remain. Each physical request allocates at most 2000 output tokens.

The annex has at most six records, 64 KiB per record. A reservation is fsynced
before each dispatch, then its actual response and validation assessment are
fsynced. Before repair, a one-time reservation in the main Trace anchors the
annex prefix length/hash. Loss of whole trailing annex records cannot refresh
credit; a reservation without a known result remains conservatively unusable.
Native activities permit this one additional main event (nine instead of eight);
legacy activities keep their eight-event ceiling. The Mind owner retains
its existing single-writer lock. Annex `accepted` means native shape validation
only, never cognitive acceptance, source warrant or Directive delivery.
Legacy logical Trace/projector versions stay readable and keep their two-step
meaning; actual D4 wire and physical budgets are versioned in the annex.

Eligible correction: explicitly rejected missing required fields or wrong field
types, before cognitive commit/delivery. Response must be one complete native
cognitive_step with a usable tool ID. The correction preserves the event,
evidence and rejected assistant response; a correctly paired native tool_result
BODY gives the actual field errors and says nothing about which direction to
choose. The original model supplies the entire new submission. No host rewrite.

Unknown/mismatched sources or quotes, unsupported output kinds, semantic
abstraction failures, truncation, multiple tool calls, storage errors and
unknown network outcomes are not repair candidates. A reserved call with no
durable result is never resampled. A known rejected response may use its one
remaining credit after process restart; a known valid response is replayed
without calling the provider. Failed correction ends with failure, not NoChange.
Previously accepted cognition/direction is not changed by rejected proposals.
Restart only resumes a known response for the currently awaited logical phase.
An earlier phase's response cannot initiate a new phase after restart, preventing
lost phase-2 annex tails from creating a fourth physical call. This deliberately
also fails conservatively in the narrow crash window after a read observation
was saved but before the next call was reserved; no uncertain call is resampled.

Official API: https://api-docs.deepseek.com/guides/anthropic_api/ —
tool_result.tool_use_id and content are supported; is_error and
disable_parallel_tool_use are ignored. Therefore body feedback and local
cardinality checks are mandatory. Only deepseek-v4-pro, official endpoint,
DEEPSEEK_API_KEY, thinking disabled, temperature 0. No ordinary retry/fallback.

## Safety gate and campaign

Before registration, run the archived-empty-response regression, process-exit
fault injection at rejected/reserved/valid/exhausted positions, one credit
across a read and restart, zero partial commit, non-repairable errors, storage
failure, whole-record annex tail loss, campaign hard-stop and scenario acceptance.
Run the existing real Docker three-event test
with both D3 and D4 adapters; D4 includes a scripted empty first submission.
`register-d4` executes these tests and saves the output before freezing cases.
No real provider failure is injected or waited for.

New development: two fresh cases, one pass, maximum six calls. Development
fixtures concern building permission and normal ventilation. Four independent
acceptance cases: withdrawn retention consent, missing custody acknowledgment,
normal archive handover, unverified latency complaint. Maximum 12 calls, once.
Source hashes/snapshots freeze before each stage; no tuning on acceptance.

Then run BOTH existing three-event publication tasks, license change and normal
control, once: initial understanding → input revision → final owner feedback.
Same formal Intention; reopen Mind/Nervous between events; keep the Execution
kernel across its natural external-input Wait. Trigger on owner outcome or
new input revision, not task labels/gold scores. No per-action Mind polling.
Each task: at most 12 Execution decisions and three Mind calls per event,
maximum 21 provider calls; loop total 42. Overall maximum 60 calls / 120,000
allocated output tokens. Ordinary network retries: zero. Protocol corrections
are separately counted. Each stage is at most 1800 seconds, requests 45 seconds.

Hard safety failures (authority leakage, wrong delivery, illegal partial commit,
corrupt owner persistence) stop execution. An individual protocol or judgment
failure does NOT cancel remaining independent cases. This D4 rule is separate
from D3's frozen acceptance stop. Formal interface success is a reliability
result, not an all-or-nothing precondition for running the loop.

## Evidence and decisions

Retain raw calls, native annex, logical Trace, cognition journals, Nervous causal
events, source hashes, audit decisions and workspace snapshots. Reuse D3's
separate abstraction, claim warrant, relevance, uncertainty and behavior checks.
The developer may accept/reject original text, never revise it. The native
adapter has no Execution tool authority; semantic review is not a sandbox.

For a corrective loop require qualified original Directive text in the actual
Execution request, identifiable subsequent actions consistent with it, objective
final file validation, owner result returning to the same Mind and a final
accepted item citing that source. Restart equality alone is insufficient:
nonempty initial cognition must remain identifiable across the second event.
Normal control should remain NoChange across all events; intervention is never
forced. D3's executable checker and these constraints are reused, not new value
evidence by themselves.

Report first-pass/final acceptance, correction attempts/successes/exhaustion,
unknown outcomes, actual token costs and Mind activation counts. If no natural
repair occurs, state real repair was not observed. Separate protocol, cognitive,
Execution and environment failures. Real delivery/adoption can establish a
controlled feedback chain, not an independent-Mind advantage over self-check.
No large A/B. All criteria satisfied gives eligibility for a later comparison;
otherwise report the precise remaining gaps without rerunning the campaign.

Entry: python -m Mind.cognitive_contract register-d4 <new-directory>, then
dev-1, acceptance, loop with that directory. The developer supplies matching
admission decision JSON files; this remains an experimental supervised entry.
No commit/push or production promotion.
