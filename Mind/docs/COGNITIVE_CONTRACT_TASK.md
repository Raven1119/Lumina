# D3：高层认知提交契约校准与事件闭环

Authority: creator's new `/goal`, 2026-09-05. Baseline HEAD remains
`9d63da7311baa7611782cc8079fb09e9d81f4e25`; D1/P0 worktree changes are preserved.
This is interface engineering with a combined change set, not isolated-effect A/B.

## P0 reassessment under the clarified D3 contract

Historical decisions remain untouched. Under this task's clarified criterion:

- P0 calls 001/008: allow. The settled-only predicate and expected artifact total
  express domain constraints and acceptance conditions. File/field names and a
  specific condition do not determine a tool, patch, or implementation algorithm.
  Record the operational tendency separately; the text grants no tool authority.
- P0 calls 006/014: reject the explicit run-script / write-completion-marker
  sequence. The stale-result diagnosis is useful, but the host must not rewrite
  the mixed proposal to salvage it.
- Call 014's 381-character Directive / 888-character complete return fits the
  existing 1000 / 2000 limits. D2's additional 320 limit was unjustified.
- Call 027's ungrounded quote and 014's invented source suffix are real reference
  failures. Nested JSON made quoting needlessly difficult; guessing a correction
  in the host would erase provenance and is forbidden.

## Minimal changes and reuse

The existing CognitiveModel gets an explicit opt-in `cognitive-submit-d3-v1`
contract. P0 defaults and source snapshots retain their old meaning. D3 aligns
schema field ceilings with Mind's reducer, adds a precise direction contract,
and displays an exact source catalogue with enumerated valid refs. Source text
is never repaired or expanded; ordinary new owner facts use plain text instead
of a JSON string containing another JSON string. An exact quote proves source
content, not inference validity. No source-guessing, no extra reviewer model.

Budgets remain two logical/physical model calls and one optional read per Mind
activity, 2000 total output characters. D3 allocates at most 2000 provider output
tokens per call (token allocation is not a new character limit). No same-activity
repair calls are introduced: first-pass and final-after-read rates are recorded;
repair count is explicitly zero. A new repair mechanism would require evidence.

No Mind journal, Trace reducer, Nervous mailbox, Director routing or interpreter
is rebuilt. `ExecutionOrgan.deliver_event` currently wakes and drives before any
existing one-shot advisory can be supplied. A small optional advisory parameter
passes through that existing wake operation, with the same next-decision checks.
This is the only new execution behavior; unrelated/default callers are unchanged.

Codebase-design seam: the native adapter owns wire serialization; Mind owns
acceptance and continuity; the existing recipient bridge owns identity checks;
Execution owns action selection. `Mind/cognitive_contract.py` is only the bounded
experiment entry and recorder, not another submission framework or scheduler.

## Validation stages

Before any real call, register separate development, acceptance and loop inputs.
Development: five cases (useful condition, stage priority, genuine tool steps,
normal NoChange, insufficient evidence), at most two passes / 20 calls total.
Debugging within that budget is allowed, each source version and failure retained.
Acceptance: another five frozen cases, once only, at most 10 calls. No tuning on
acceptance samples. Both sets are simple domain evidence, not D1 fixtures.

Acceptance requires legal accepted cognition, calibrated high-level output
admission, useful direction on the two clear correction cases and normal
NoChange on the normal case. Insufficient evidence must remain uncertain;
procedure-bearing evidence must not become a tool plan. NoChange is never
rewritten into a Directive to satisfy the gate.

The semantic audit records abstraction, claim warrant, direction relevance and
uncertainty preservation separately, including claims inside Directive text.
These are explicit acceptance conditions, not provider-format success. The same
development operator audits submitted text without rewriting it or adding a
reviewer model. Loop admission additionally checks the original direction
against the subsequent Execution trace. Restart equality alone is insufficient:
at least one initially accepted item must remain identifiable in the second
accepted state, and final accepted cognition must cite the final owner source.
This is a bounded continuity probe, not a general requirement to retain every item.
Owner facts bound staged-artifact text to 400 characters with explicit truncation;
complete workspace snapshots remain in the artifact.

After acceptance, run two same-goal publication-catalog tasks (license change
and normal control), each with three Mind events: initial batch result, a new
catalog revision, final owner result. Execution naturally waits for external
batch input; Mind activation itself does not pause it. Keep the live IPython
kernel across that natural wait. Reopen Mind/Nervous between events, preserving
the same formal Intention and source IDs. Guidance binds the existing run and
next decision, reaches Execution unchanged through the wake event, and final
owner facts return to that same persistent Mind. Final artifact validation is
independent of the completion marker. Ordinary action traces are not projected
wholesale into Mind. World Model remains off.

Loop ceilings: 12 Execution calls per task and two cognition calls per event,
36 provider calls total. Overall maximum: 66 calls / 132,000 allocated output
tokens. Model is exclusively deepseek-v4-pro, official Anthropic-compatible API,
DEEPSEEK_API_KEY, thinking disabled, temperature 0, no retries/fallback. Actual
input/cache/output use and all failed calls are retained. No cost-based exclusions.

Deliver a real unchanged Directive in a qualified decision, identifiable later
action consistent with it, objective output and owner feedback with persistent
revision. The control should keep NoChange. This grants eligibility to plan a
later behavior comparison, not proof of general independent-Mind advantage.
If a frozen acceptance/loop gate fails, retain the failure and report the precise
blocker without rerunning it. No commit/push or production Chat/Memory wiring.
