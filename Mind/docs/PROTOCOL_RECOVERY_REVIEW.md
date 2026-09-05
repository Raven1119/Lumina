# D4 code review and evidence review

2026-09-05. Uses the `code-review` skill's two independent axes. Fixed baseline:
`Mind/fixtures/cognitive_contract_d3/acceptance/source/`; this distinguishes D4
from pre-existing uncommitted D1/P0/D3 work. Git HEAD remains
`9d63da7311baa7611782cc8079fb09e9d81f4e25`, with no new commits. Spec:
[PROTOCOL_RECOVERY_TASK.md](PROTOCOL_RECOVERY_TASK.md) and the creator's D4 goal.
Standards: root/Mind AGENTS, Ponytail and the existing owner boundaries.

## Standards

Independent reviewer: `/root/d1_standards_review`.

PASS for final source and campaign records, with no blocking finding. All three
stage source manifests match both snapshots and current source. All 34 real calls
are accounted for; 17 Mind wire/response pairs match their native annex records.
Three corrections preserve original assistant content, correctly pair tool IDs,
and report only field errors. The one delivered Directive matches its original
text in the actual Execution request. No new execution authority, production
wiring or framework was introduced.

Ponytail scope: three existing supported Mind modules change because correction
credit and known-response recovery must live with the existing durable owner.
The adapter/runner changes extend existing experimental files. No extra planner,
manager, review model, scheduling service or task-specific Python syntax exception.
The post-campaign accounting script only reads artifacts and writes its summary;
it does not call models or modify verdicts.

The normal-control cognitive error is already committed under structural owner
validation. The developer review withholds delivery, not the cognition itself.
The error remains at final feedback. The report must not describe it as rejected
before commit or successfully corrected.

## Spec

Independent reviewer: `/root/d1_spec_review`.

The bounded recovery mechanism and positive supervised feedback chain are
supported; the full frozen criteria are not. The review identified the following
limits in the final actual evidence:

- **CAUSAL VALIDITY:** Execution's third call already implements both correct
  eligibility branches before any Mind activity. Post-delivery actions and final
  artifact match the guidance, but this is not repair of a pre-existing wrong
  algorithm and does not establish Mind's necessity or superiority.
- **COGNITIVE CONTINUITY:** the positive event-2 item rewrites its claim to say
  the file is stale while using `contradicted` status. That ambiguity remains.
  Persistent item IDs prove historical linkage, not fully correct belief revision.
- **NEGATIVE CONTROL:** normal event 2 emits a Directive rather than NoChange.
  Its revision update is factually correct because this fixture has stale version
  fields; the frozen failure cannot establish harmful intervention. Separately,
  the supported discriminator incorrectly imports release-time license eligibility
  into approval-time policy, and survives event 3. Warrant rejection is justified.
- **AUTHORITY / MIND–EXECUTION BOUNDARY:** Mind returns data and read requests;
  Execution chooses its Python operations. Semantic developer review never edits
  the original direction or grants Python permission. The experiment remains
  supervised and is not production wiring.
- **CONTEXT CONTAMINATION:** Mind receives bounded owner facts, accepted cognitive
  items and optional read observations, not Execution's full transcript. Only an
  explicitly submitted, qualified original Directive crosses back. Private beliefs
  do not become host-authored instructions.

Both tasks reached correct objective artifacts with nonempty continuity and final
owner-source references. Frozen loop result: 1/2 cases; comparison qualification:
NO. `INCONCLUSIVE` is the supported behavioral-value conclusion.

## Safety defects fixed before the campaign freeze

The earlier code review found three real mechanism defects, not model-quality
failures. Each was fixed and covered before any D4 real call:

1. Native persistence exceptions could be downgraded to ordinary model failure.
   Trace now raises a storage error; the owner records `trace_failed`, which
   hard-stops the D4 runner.
2. Removing a whole native annex tail could refresh correction credit. A main
   Trace reservation now anchors the pre-repair prefix. A further phase-2 tail
   case is blocked by requiring a known response for the currently awaited phase.
   Unknown reservations never dispatch again. The documented narrow post-read,
   pre-reservation crash window deliberately fails conservatively.
3. Whitespace-only native tool IDs could be treated as usable for feedback.
   They are rejected without repair; no invalidly paired result is sent.

The final targeted review found these resolved. Deterministic tests cover the
archived D3 empty input, actual process exit/reopen at rejected/reserved/accepted/
exhausted positions, shared credit across read continuation, no partial commit,
correct delivery, storage failure, whole-tail loss and conservative phase recovery.
Non-repairable semantic/source/unsupported-kind/multiple-block/truncated/unknown
transport cases remain failures, not NoChange or free retries.

## Verification record

- Frozen safety gate: **38 passed**, including actual Docker execution with
  deterministic model responses; raw command/stdout/stderr are in
  mechanism.json??????`../fixtures/protocol_recovery_d4/mechanism.json`?.
- Final targeted `Mind/test_protocol_recovery.py` and `Mind/test_experiment_a.py`:
  **66 passed**. This includes the last phase-2-tail regression and the legacy
  B8 prohibition on dynamic authority lookup in the bounded runner.
- Root suite: **347 passed, 24 skipped**, two pre-existing MAGMA dependency
  warnings; temporary test workspace, no real campaign repetition.
- `git diff --check`: pass (line-ending warnings only). Pinned MAGMA status has
  no changed entries. No Recall behavior changed, so no new real-MAGMA campaign.
- Post-campaign accounting revalidates every request's model, thinking mode,
  temperature/output budget, native call limits, source freezes and call counts.

Retained development attempts: an initial sandbox TEMP permission failure; a
recursive test collection attempt that accidentally included frozen source
snapshots; and a properly collected broad owner run with **503 passed, 11 skipped,
1 failed**. That last failure was the legacy B8 AST check after an added `getattr`
in the runner. The lookup moved to the existing Organ boundary and the subsequent
targeted runs passed. The broad run was not relabeled as passing. An intermediate
focused run had **112 passed, 1 skipped**; it preceded the last tail-loss test and
does not replace the final checks above. No history directory was deleted to make
test collection pass.

Standards: 0 final blockers. Spec: 0 remaining code-safety blockers, 3 explicit
evidence/quality limits (causal attribution, claim/status revision, negative-control
reasoning). A working mechanism is not an all-criteria behavioral PASS.

Both independent reviewers subsequently checked the final RESULT, REVIEW and
CURRENT_STATUS text against the raw artifacts and found no factual/scope mismatch.
