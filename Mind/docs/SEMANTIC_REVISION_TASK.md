# D5: repair conditional reasoning and coherent persistent revision

Authority: creator's 2026-09-05 goal. Baseline is the local D4 increment on
Execution_lab2, HEAD 9d63da7311baa7611782cc8079fb09e9d81f4e25. No history rewrite,
commit/push, production wiring or new Mind mechanism.

## Diagnosis and minimum change

D4 raw input/output audit, not just verdicts:

- license_change event 2 changes a previously supported claim into the assertion
  that the staged file is stale, but marks that new assertion contradicted.
  Event 3 retains it while updating another belief and closing a question.
- normal_control event 2 adds a supported belief with a discriminator requiring
  removal for an inactive license even under approval_time. The next request
  contains that exact item, including its discriminator. Event 3 replaces the
  basis with the final owner report but explicitly submits the same bad condition.
- The final artifact success never tested an inactive license in approval_time.
  It alone cannot refute that counterfactual rule. However the visible owner
  source still states that release_time additionally requires active license;
  the rule evidence, not successful delivery, distinguishes the policy scopes.
- `_apply_updates` replaces the full item supplied by Mind and retains omitted
  items. The next context includes all active items and the cited source texts.
  No observed projection omission or field-update implementation defect explains
  these failures. Structural validation correctly cannot decide their meaning.

Reuse MindOrgan, Nervous, logical/native Trace, D4 correction credit, Directive
binding, native tool serialization, read continuation, Execution facade and
isolated ordinary IPython. D4 transport/recovery/authority/delivery behavior stays
unchanged. The opt-in `cognitive-submit-d5-v1` adds a domain-independent revision
instruction and field descriptions to the existing native adapter. It requires
checking affected old items and the resulting state, not another output format or
host-authored correction. Legacy D4 wire remains byte-for-byte reproducible from
its recorded projections. No reducer or context change without contrary evidence.

## Development and independent evaluation

Before real calls, freeze development cases and budgets. Development uses two
archived D4 state/error continuations plus two new condition/time variants.
Seeds are explicitly SCRIPTED persisted cognition built through the normal owner,
never reported as new naturally generated model errors. Their raw source and
construction are retained. New D4-contract baseline continuations belong to D5
development, not reruns or replacements of the historical D4 campaign.

Budget: baseline four activities (at most 12 calls), candidate four activities per
pass, at most two passes (24 calls total). Development maximum 36 calls. A second
candidate pass is allowed only after a recorded diagnosis and versioned change,
not to obtain a luckier sample. No sample retries within a pass.

After the final candidate change, freeze independent cases and source hashes,
then run them once. Three cases, each three events under one persistent Mind:
new evidence requiring revision; complete delivery with no changed effective
condition; observations unable to distinguish competing explanations. At least
the revision case uses real Execution in an isolated synthetic workspace with
objective artifact acceptance. Maximum 27 Mind calls + 12 Execution decisions =
39 calls. Entire campaign maximum 75 calls / 150,000 allocated output tokens.

All calls: deepseek-v4-pro, official Anthropic-compatible endpoint,
DEEPSEEK_API_KEY, thinking disabled, temperature 0, 2,000 output tokens. D4's
three-call/one-repair/one-read activity ceiling is unchanged. No ordinary retry,
provider fallback or semantic retry. Semantic reviewers never feed corrections
back to runtime Mind. Unknown outcomes and safety failures follow D4 policy.

## Acceptance and evidence

Review the entire resulting active state, including retained/contradicted items
and discriminators, against visible rule evidence and actual observations.
Separately record condition correctness; claim/status/basis/discriminator
consistency; retirement/revision of refuted old errors; appropriate direction;
remaining uncertainty; original-text delivery; actual Execution actions; final
owner evidence and post-reopen semantic content. Historical scoped truths may
remain supported; an unknown branch need not become settled. An archived old
error is not repaired if its unsupported rule remains elsewhere in active state.

Developer semantic admission is explicit, accept/reject only and after structural
cognitive commit. It may withhold a direction but cannot remove an accepted bad
belief or supply an answer. Protocol failures and semantic failures are separate.
IDs, hashes, APPLIED and task completion alone do not pass semantic criteria.
Normal controls must have no revision/acceptance gap. The model is never forced
to emit NoChange, and domain details alone do not make a direction a tool plan.

Deterministic regression proves wire preservation, actual update persistence and
history, not model semantics. Real development compares baseline/candidate
continuations; independent results determine whether the repair generalizes to
these new samples. This is no broad behavioral-value A/B and cannot demonstrate
Mind's general superiority. All failures and old D1/P0/D3/D4 verdicts remain.

## Versioned development revision (before independent samples)

D4-contract baseline: two archived semantic failures, one protocol failure, one
correct temporal revision. D5-v1: both archived semantic failures remain; the
temperature-scope and temporal variants pass. The first v1 response was committed
but the external review wait expired before the case recorder completed. Keep
that failed run unchanged. `continue_dev1.py` runs only the three never-called
cases with the remaining 11-call ceiling; it does not replay the first call.
The late review of that accepted state records its polarity error explicitly.

There is no evidence of a reducer/projection bug: the v1 requests visibly include
the affected old claim and discriminator. Version d5-v2 changes only semantic
instructions/field descriptions: prioritize affected old items before progress
summaries, place reconciliation before the base instructions, explain P/not-P
status semantics and conditional discriminators with domain-independent examples.
These changes are one interface calibration bundle, not independently identified
causal effects. D4 transport, schema validation shape, budgets and recovery stay
fixed. This is the second and final candidate development pass; no further model
or prompt tuning after independent sample freeze.
