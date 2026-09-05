# D1 code review

Specification: the creator's Astra task, 2026-09-05.
Fixed baseline: `9d63da7311baa7611782cc8079fb09e9d81f4e25`, verified against
local HEAD and GitHub `Execution_lab2` before registration. These are new
worktree files; there are no task commits. Review includes the new files
against an empty baseline, not just the empty tracked-file Git diff.

## Standards — before the real campaign

Independent reviewer: `/root/d1_standards_review`.

Final finding: **pass, no remaining blocker for freezing D1**. This conclusion
is limited to the closed experiment admission policy, not general Python
sandbox certification.

Earlier hard findings and resolutions:

- File admission could be bypassed through imported aliases, stored/traversed
  Path objects, trusted-name rebinding, callbacks, and reads before local
  initialization. Static regression checks reproduced these without executing
  external payloads. The final policy admits a closed syntax, direct calls,
  literal file names, and definitely initialized local data. It refuses
  ambient namespace reads, callable values, and writes to task.json.
- A free-text Directive could contain file operations despite its prompt.
  Mandatory independent review now occurs before delivery. It may accept or
  reject the unchanged output on abstraction/authority grounds only. Rejection
  or missing admission invalidates a formal value verdict.
- Both review arms carry identical dormant native tool schemas and explicit
  `tool_choice=none`. A review tool-use block is not executable.

Actual wire evidence, matched allocated budgets, reuse of existing organ
interfaces, and preservation of historical verdicts passed review. No
production source module was changed. No additional framework, planner,
World Model, runtime topology, or background organ was introduced.

## Spec — before the real campaign

Independent reviewer: `/root/d1_spec_review`.

Final finding: **pass, no remaining pre-campaign blocker**. The machine
preregistration wording was aligned with the already corrected code before
registration.

Earlier causal findings and resolutions:

- Actual token consumption must not be a post-treatment validity gate: an
  earlier successful finish legitimately uses less budget. Both arms now have
  identical allocated ceilings; actual consumption is reported as an outcome.
- A B-only negative-control win may consist of correct NoChange preserving
  the initial answer. Only correction wins require delivered, relevant and
  adopted corrective advice; controls have their separate trace criterion.

The comparison is explicitly retained Execution messages versus their bounded
factual projection under a common review prompt, using host-induced single
checkpoints. It does not estimate naturally arising long-history failures,
native unconstrained self-reflection, or persistent Mind identity.

## TDD and regression evidence

- D1 public-seam suite: 7 passed, including real checkpoint/fork/resume,
  accepted and rejected pre-delivery admission, exact external JSON scoring,
  single-use campaign registration, and a 12-pair scripted campaign.
- Authority regression cases were first observed to fail admission tests,
  then rejected after their general rule was corrected. No external payload
  was executed. Final focused admission/wire check: 2 passed.
- Existing Mind experiment/trace and Nervous checks: 102 passed.
- Root standard suite: 347 passed, 24 skipped.
- Conversation Memory and Dream suites: 199 passed, 46 skipped.
- Skips retain existing optional/integration conditions; these counts do not
  claim that skipped real-model tests ran. Existing upstream deprecation
  warnings were unchanged.

These deterministic checks establish harness mechanics, not model compliance
with the cognitive schema or behavioral decoupling value. Real-campaign
failures must remain in the final report, even after a pre-campaign review pass.

Pre-campaign findings remaining: Standards 0; Spec 0. Post-campaign review and
the frozen campaign's behavioral verdict are reported separately in the result.

## Standards — after the real campaign

The independent admission reviewer completed 24/24 decisions while blinded to
arm and outcome: 6 allowed NoChange proposals, 18 rejected null proposals.
It did not edit advice, grade correctness, read Execution traces or retry a
model. It stopped after the last decision. No proposal was delivered as an
action. The separate evidence audit matched all 83 call files and all 24
admission receipts and verified all frozen source/artifact hashes.

The narrow admission remained closed but rejected ordinary lambda expressions
in 12 real Execution responses; one also used ordinary isinstance(). This is
an experiment suitability limitation, not evidence of malicious behavior or
a claim that these ordinary Python constructs are intrinsically unsafe.
Historical verdicts and existing production source remain unchanged.

## Spec / causal validity — after the real campaign

Independent reviewer: `/root/d1_spec_review`. Verdict: **INCONCLUSIVE**.

- No pair had two accepted reviews. A had 11 DSML-text protocol failures; B
  had 5 overlength responses and 2 cognitive-update validation failures.
- Four accepted reviews stored true correction beliefs (09A; 02B, 04B, 11B),
  but all accepted outputs were NoChange. There were zero issued/delivered
  Directives and zero observable adoptions attributable to advice.
- All 12 first Execution wires were exactly equal across arms. The two B-only
  wins, 01 and 12, had failed reviews on both sides and A was stopped by a
  normal lambda expression. These wins cannot be claimed as Mind rescues.
- All 12 authority rejections disappeared after removing lambda (and, for 05B,
  isinstance) in an in-memory AST inspection. Generated code was not executed.
- Report artifact correctness separately from owner completion: 11A wrote the
  correct deliverable but repeated checks until the decision budget ended.

The requested causal behavior comparison was not achieved in a valid pair.
The task's allowed INCONCLUSIVE outcome is therefore required. These findings
remain in D1; they are not fixed by changing the campaign after seeing results.
The single next recommendation is a separately registered review-protocol
feasibility experiment, with ordinary Execution syntax admission calibrated
using deterministic tests first. No new cognition mechanism is justified.

Post-campaign findings: Standards — 1 disclosed experiment suitability
limitation (ordinary syntax rejection); Spec/Causal — 2 validity blockers
(review protocol and Execution admission), with no delivered intervention.

Final delivery review by the independent Standards reviewer: **PASS, no
delivery blocker**. It verified the frozen source and three artifact hashes,
83 call correspondences, 24 admission receipts, the 7/12 versus 9/12 score,
zero issued/delivered Directives, and the cache-inclusive token supplement.
The report preserves both the pre-campaign review pass and the real-campaign
INCONCLUSIVE finding without rewriting either. No further real calls ran.
