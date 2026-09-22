# Lumina current state

Navigation: [code map](../README.md) · [scheme catalog](EXPERIMENTS.md).
This file records detailed contracts and version-bound evidence; the two maps
identify current entry points and explicitly selected alternatives.

The cognitive core is now `Mind <-> Nervous <-> Execution <-> Environment`.
Each organ owns its state and continuation; there is no central Session/Host.
The foreground CLI is `python -m Mind`, using a new `--state` directory.
Single-goal operation remains the default; `--pursuit` opts into the Stage1
continuing-Mind implementation described below. Production Chat keeps its separate memory read path;
the manual Execution API is also separate.

## Chat bounded memory use

The default Chat path retains the original v2 boolean gate and eight-token
budget. Its Recall policy keeps the legacy policy fields (20 nodes, depth one) and 5000-character
output bound with up to 3 whole facts; reliable-v2 adds separately item-bounded
source supplements within the same character budget. FirstHit discovery uses
its own 5-seed/64-node/256-edge defaults. The policy carries no final score floor (the
reliable reader rejects one as an explicit policy conflict) and renders source
speaking time, USER/LUMINA role and anonymous existing subject/object bindings.
`LUMINA_MIND_GATE_MODE=direct` explicitly enables a
read-first experiment: no pre-read gate or query editing, one original-question
Recall, then Answer with the original recent context and bounded historical
candidates. It keeps the same bounds
while allowing up to 20 whole evidence items.

The experiment presents source speaking time, USER/LUMINA role and anonymous
existing subject/object bindings within the same whole-group budget. The labels
do not create identity attributes or prove that distinct bindings denote distinct
real-world people. Answer guidance preserves user corrections, history and
conditions; unverified assistant guesses cannot stand in for established facts.
Memory discovery, source validation, persistence and write semantics remain
with their existing owners. Configured real-model Chat uses v6 + FirstHit +
reliable-v2 and bypasses legacy BGE/Hindsight.

`LUMINA_MIND_GATE_MODE=graph-read-v2` explicitly selects the query-driven
candidate. One structured Mind call (768 output tokens, temperature zero)
replaces the boolean call, carries source-located clues and at most two
relations to the real Memory facade, and preserves the original Chat question.
The reader allocates at most five shared entries before three shared-budget
frontiers, then checks role-supported identity joins and whole evidence bundles.
Unsupported conditions, identity alternatives and incomplete bundles stay
partial. This does not change the production default, writer, checkpoint or
the earlier `graph-read-v1` reader. See the [read-side contract](../Conversation_Memory/docs/FIRST_HIT_MEMORY.md#explicit-query-driven-graph-read-v2)
and [Chat gate contract](../Mind/docs/CHAT_RECALL_GATE.md).

`LUMINA_MIND_GATE_MODE=select` explicitly adds one semantic decision after that
same read. Memory retains a request-local immutable prepared view, and validates
selected IDs plus complete association dependencies. The selector receives the
original question, near context and exact source blocks; Answer receives their
validated subset. No pre-read gate is added. Model/parse/subset/log failures
restore the original prepared context without a second read. Source age or an
unresolved reference is not evidence that a fact is false or currently absent.

V3/v4 query-generation experiments remain frozen local evidence; their active
query editors and dedicated protocol tests are retired. Default `llm` is unchanged,
with `constant` and mock fallback still supported. Both read-first modes remain
explicit-only; independent semantic acceptance is separate from the mechanism. See the maintained [Chat Memory read contract](../Mind/docs/CHAT_RECALL_GATE.md)
for bounds and failure semantics. Experimental corpora, outputs and reports remain
local and are not runtime or CI dependencies.

## Explicit first-hit Memory

`first_hit=FirstHitPolicy()` adds local first-hit propagation and final sparse
fact competition through `recall_associative`. The same core plans at most three
semantic connections per newly verified Formation v2 fact, with a frozen stage
in the existing ingestion store. The injected Cold owner supplies a recent
segment/byte-bounded original-source view, including explicit raw-only lookup.
The reliable reader built on this activation is the production Chat/Dream read;
legacy BGE/Hindsight Recall and existing source interfaces remain for explicit
profiles.
No Chat event integration, new generated read calls or upstream change is made.
See the [maintained contract and usage](../Conversation_Memory/docs/FIRST_HIT_MEMORY.md).
Implementation correctness and recovery are distinct from semantic usefulness;
the standalone v2/first-hit-v1 profile remains explicit. Its use inside the
current reliable reader is already wired; semantic effects require separate evaluation.

## Explicit source-context prototype

Memory also exposes opt-in source indexing and complete source-context reads in
[the source prototype contract](../Conversation_Memory/docs/SOURCE_REPRESENTATION_PROTOTYPE.md).
Raw dialogue remains independently retrievable in a separate MAGMA namespace;
existing facts and entity occurrences can optionally locate exact source ranges.
The read returns original role/time-preserving dialogue within one context budget.
An additional explicit reader can search and read original turn/character ranges
with bounded native model continuation. Source lexical search uses its posting
features consistently; default fact Recall scoring is unchanged. The reader
retains exact source ranges, with no automatic production adoption.
It has no default Chat wiring, new Formation policy or automatic Cold backfill.
Memory also exposes explicit `recall_experiences(cue, policy)`: bounded original
dialogue views over the unchanged source selection, plus existing range expansion
references.
It adds no generated read calls, derived index or consumer wiring.
Local paired experiment outputs remain separate from maintained source.

## Conversation Memory entity enhancement

Production Chat and app Dream share one Memory adapter: a configured real
model writes `grounded-formation-v6` and reads through the `reliable-v2`
associative presentation (FirstHit activation, always-visible canonical bodies
plus a bounded Cold source supplement) at the ordinary Recall boundary.
Historical v2/v4/v5 writers and `first-hit-v1`/`reliable-v1` readers remain
explicit selections; mock/legacy construction keeps `grounded-span-v2`.
The v2 lineage introduced source-grounded fact
and mention extraction with batch proposition/identity verification. Exact
occurrences survive zero-fact windows; explicit subject/object roles and literal
attributes persist through the existing graph/checkpoint owners. The public
Memory facade exposes bounded occurrence lookup as well as fact Recall.

For explicitly selected legacy BGE Recall (not the default reliable reader):
name/lexical indexes cover stored history before limiting candidates; bounded
multiple-identity retrieval and two-fact relationship projection retain fixed
BGE, Hindsight weights and the production score floor. BGE raw logits receive
one stable sigmoid, independent of their range or batching. Entity-conditioned
search uses complete available EVENT membership from actual REFERS_TO edges,
with selectors maintained during load and writes over the existing FAISS index.
Returned candidates and graph expansion remain bounded; membership construction
and native vector search have nonconstant costs. Cold consumption remains explicit
and follows durable completion. Valid two-fact paths that fit BGE's fixed input
window are scored together and
returned as whole source evidence bundles; this changes scoring input while
retaining original stored facts. No task-memory expansion, automatic backfill,
Chat ingestion, provider migration or cognitive-organ wiring was added.
Invalid source citations now isolate their candidate and dependencies while
independent verified facts and mentions persist. The raw extraction response
is checkpointed before parsing; pending processing errors still prevent Cold
consumption. A later explicit retry may perform one bounded source-ref repair,
then verify only that new subset while retaining previous results and IDs.
Verified local identity links reuse the new namesake's ref despite other name
candidates; old completed bindings are not automatically rewritten.
Same-name identities can
remain unresolved, and Recall can mix evidence for different same-name people;
complete entity/attribute coverage is not guaranteed. See the
[Memory contract](../Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md).

## Current implementation

| Part | Responsibility |
| --- | --- |
| Mind | Original user/important events, persistent selective cognition, necessary evidence/analysis, NoChange or original high-level guidance. |
| Nervous | Original input transport, durable causal mailboxes, idempotent completion, mechanical foreground continuation and shared provider accounting. |
| Execution | Independent local implementation, AgentProcess/facade lifecycle, immutable environment sources, single guidance binding, persistent received advice and feedback. |
| Analysis | Mind-owned independent bounded context; structured understanding, optional isolated static/stateful computation, model reuse/revision and compact attributed reports. |
| Prediction feedback | Prospective action/condition/object/time/quantity declaration, actual observation watch, mechanical alignment/comparison and Mind reassessment. |

Baseline cognition uses `mind-cognition-v1`; explicit pursuit uses
`mind-cognition-stage1-v1`. Both use `mind-native-v1:6-calls`.
Old experiment session/schema/native replay is retired.
Historical conclusions are preserved; old raw campaigns and provider responses
are not maintained runtime or test dependencies.

Promoted mechanisms now live in formal activity/cognition/contracts/model/
analysis modules, Execution's model/sandbox/evidence/runtime, and Nervous's
provider/storage. The old chain -> event_loop -> decoupling -> behavioral imports,
experiment_a, central host and checkpoint fixture are removed.
Production Recall gates, the supported Execution facade and licenses remain.

## Intention and Nervous Stage1 implementation

### Opt-in repetition observation follow-up

`start --repetition-mode execution|mind` adds bounded exact repeated-action facts;
default remains off. Execution owns action/result evidence and sampling boundaries;
Nervous optionally routes one event per unchanged segment to the same Mind.
NoChange or new advice does not erase/rearm the fact. The existing completion,
authority, recovery and context mechanisms remain in place.

The one-scenario A/B/C comparison used 24 new real calls, eight per arm. All three
produced correct dependency indexes and none completed its Run inside the frozen
allowance. Baseline did not continue the seeded exact repetition. Frozen C also
lost the raw fact before Execution received new advice, invalidating a same-evidence
B-C claim. Verdict **INCONCLUSIVE; neither path promoted**. After-comparison v2
fixes preserve the fact across advice, persist explicit observation-query boundaries
and keep large samples out of event bodies. These fixes have deterministic
verification, not a new real-model evaluation. Original 96-call state and workspace
hashes remain unchanged. See the [result and cost report](../Mind/docs/REPETITION_REASSESSMENT_RESULT.md).

Final v2 maintained validation: **1,023 passed, 30 skipped**; related Docker
checks: **14 passed** with scripted model replies. The two existing upstream
deprecation warnings remain. Source/document review and `git diff --check` passed.
All **24** new real requests are retained separately from deterministic setup;
post-comparison fixes did not consume or claim another real-model evaluation.

### Stage1 baseline and historical result

Implemented as an explicit opt-in. The original real acceptance remains
**PARTIAL**; the same Task B's correction chain has now closed with disclosed
developer feedback and recovery. This is not acceptance of general autonomy. The
2026-09-12/13 narrow repair rejects closing an Intention with an actual unsettled
Task and rejects dangling understanding references after cognitive retirement.
Native preflight and final commit share the check; its version is frozen only
for new activities, preserving old accepted and unfinished records.

Further source/trace diagnosis repaired oversized composed cognitive reads,
unusable item selection, dual update/current submission and deferred completion
continuation. New pursuit activities freeze `mind-cognitive-interface-v2`:
selective updates plus explicit retirement; old activities retain their rules.
Compact complete item reads keep references rather than forcing all dependency
bodies inline. Known no-tool Mind responses have one shared bounded correction;
unknown outcomes and unbound historical provider decisions remain blocked.

The original **32-call PARTIAL** result remains in the
[Stage1 report](../Mind/docs/INTENTION_STAGE1_RESULT.md). A separately authorized
12-call extension completed original Task B at call 38. Later authorized batches
delivered the corrective Directive at 46, corrected both reports at 48–49 and
reviewed their results at 54–55. After explicit audit feedback and compact reads,
61–62 repaired the retained causal overclaim and obsolete initial assumptions;
correct historical knowledge remained. Current checkpoint projection now follows
native history, with prior attributed guidance retained in the owner context.
That ordering change alone did **not** stop repeated checks at 77–84.

A second disclosed owner event reported the actual repetitive execution state.
Mind updated its stage judgment with NoChange; Execution independently inspected
the reports and claimed completion at 93. Mind accepted the completion feedback
at 94. Normal retry-review settled both original failed activities at 95–96,
retaining their failures. The same Mind now has revision 15, 15 effective items,
two Tasks and the paused Intention; the correction Run is completed. No active
or unresolved cognitive responsibility or pending mailbox remains. Restart with
positive budget headroom added **zero** calls, then another ordinary resume also
added zero. No new Directive caused this final exit, and developer feedback was
necessary in this scene; autonomous stagnation discovery is not demonstrated.

All **96** calls remain: **37 Mind + 59 Execution**, 154,649 reported output
tokens and 3,975,138 request bytes. Original inputs, failure history and Task
identities are preserved. Final maintained validation: **981 passed, 30 skipped**.
An earlier run had one test assuming the old guidance message position; the
assertion now checks the owner context and final checkpoint, preserving its
delivery invariant. Its complete runtime file also passed **26** tests.
Focused projection/recovery/core-loop coverage
passed **129**, with 1 Docker opt-in skip. The real Docker loop and history
checks passed; one separate Windows atomic-write refusal in that invocation
passed isolated recheck. Standards and Spec review found no remaining scoped
code blocker. Full detail and all intermediate failures remain in the report.

Limits remain: extremely escaped single records can still exceed bounded read
envelopes; basis arrays do not always separately cite each authoritative state
observation, though original activity observations remain traceable. No Stage2,
Chat/Memory wiring, default summary change, commit or push occurred. Older
results below keep their scopes; test counts do not establish model ability.

`start --pursuit` freezes the real owner's authorized scope, Mind identity,
workspace and cumulative budget. Mind can commit versioned Intentions and propose
distinct bounded Tasks through the existing atomic cognitive submission. Execution
accepts Task versions and runs them serially; a proposal or Task A's completion
does not establish acceptance or completion of Task B.

Nervous now builds and freezes actual attention selections from bounded owner
views. Unselected cognition stays accepted; a current owner read is required
before changing an unseen item. Old Task bodies and background are available by
reference rather than automatically carried into the next Task. Registered
`read_evidence` queries return owner state or bounded history to the same activity.
`source.changed` and one-time `review.due` watches operate during foreground
checks; STOP/RESUME/REVOKE are durable owner control events in pursuit mode.

The implementation extends the existing Cognition journal, Nervous mailbox and
Execution owner state. It adds no central manager, competing persistence store,
upstream orchestration transplant or background heartbeat. Baseline context mode,
single-goal launch and the separate Chat/Memory/Dream paths retain their meaning.
See the [Stage1 runtime contract](../Mind/docs/INTENTION_STAGE1.md) for authority,
selection, Watch lifecycle, query and recovery limits; the
[approved design](plan/INTENTION_NERVOUS_STAGE1.md) records the intended acceptance.

## Evidence

V70-V73 previously demonstrated scoped correction of retained erroneous cognition,
NoChange closure and a CLI file task, with development repairs and explicit
recovery involved. Those results did not establish general autonomous reliability.
All failed/INCONCLUSIVE conclusions remain in
[EXPERIMENT_HISTORY](../Mind/docs/EXPERIMENT_HISTORY.md).

This refactor uses deterministic current-invariant regression rather than a new
model-ability campaign. A new Unicode CSV smoke has run actual isolated model
calculation and IPython action, preserved original guidance, measured real output,
compared declared byte quantities, returned to the same Mind and restarted quietly.
It used eight injected provider responses (Mind 4, analysis 2, Execution 2),
zero real provider requests. Scripted decisions do not establish model judgment.

The smoke exposed and corrected duplicate reviews when request_mind and a changed
prediction described the same committed checkpoint. Review also identified
initialization and stale-guidance continuation gaps; targeted recovery regressions
cover the repaired paths. Explicit CLI retry preserves failures and cost while
reassessing current evidence; known read/analysis errors no longer wedge delivery.

Core refactor validation on 2026-09-09 (commit `1f893d7`):

- Maintained default suite: **641 passed, 29 skipped**. Optional/environment tests
  remain opt-in; the two upstream MAGMA deprecation warnings are unchanged.
- Isolated Docker computation plus the fresh whole-loop smoke: **14 passed**.
- Standards and Spec review: no remaining findings after the recovery fixes.
- Current Python parsing/import audit, maintained document links and
  `git diff --check` passed; no retired campaign module is imported.

The full suite uses a fresh temporary directory outside the checkout: Recall's
sandbox tests intentionally reject repository paths. An initial in-repository
test directory caused six such refusals; moving test state fixed the setup
without changing Recall's safety policy. No real model/provider calls were made.

## Event semantics follow-up

The post-refactor source review found two event-delivery defects. User input
was deduplicated by text/type rather than submission identity; a second
intentional identical message could disappear. Submission now has a fresh
identity by default; explicit `submission_id` / CLI `--submission-id` reuses
only the same transport submission, with immutable content checked by Nervous.

Prediction watching had also returned early without an Execution Run, despite
accepting the watch. Registered external observations now remain monitored on
foreground resume after NoChange, even without an Actor. Subsequent review
also caught A-B-A observations being mistaken for an old notification: without
a Run, the latest accepted review now distinguishes successive observation
checks while a pending notification retains the same identity. Each new watch
retains its own registration baseline until a later accepted review covers it;
an earlier review cannot manufacture a change for that watch. Recovery and delivery
remain owned by the existing organs; no background scheduler is introduced.

Event-fix validation at `5757df7`: **323 passed, 5 skipped** in the full Mind/Nervous/Execution
suite. Regressions cover completed/pending and lost-response submission retries,
both CLI input forms, and eight no-Run observation scenarios including repeated
values, registration after an earlier review, unread sources and restart.
Standards and Spec review have no remaining findings. Real provider calls: zero;
new prediction tests inject both model responses and calculation output.
The whole-repository rerun did not start because automatic execution approval
timed out twice; Docker opt-ins were not rerun. The earlier refactor counts above
remain historical evidence, not a claim of a new full-tree validation.

The next source review found that the A-B-A fix still excluded an existing Run
at an unchanged waiting checkpoint. The runtime now uses the latest accepted
review to distinguish later observation changes both with and without a Run.
An accepted review also closes notification of that exact checkpoint without
falsely acknowledging unread prediction comparisons. Original pending events
keep their identities through delivery retries and restart. No transport store,
scheduler or cognitive authority was added.

Fixed-checkpoint validation: **331 passed, 5 skipped** in Mind/Nervous/Execution.
Before the repair, all eight added waiting-Run variants reproduced the lost
notification, while the eight no-Run variants passed. The expanded regression
checks A-B-A-B, an unchanged Actor checkpoint and deliveries, unread obligations,
publication retries, restart and quiet settled resumes. The final addition of
restart after publication but before handling passed all **16** focused cases
(one Docker opt-in skipped, one unrelated test deselected). Standards and Spec
review found no remaining issues in this diff. Real provider calls: zero;
responses and calculation output are injected. Whole-repository and Docker
checks were not rerun for this narrow repair. This is scoped regression evidence,
not a claim that the entire cognitive loop has no remaining defects.

## Recovery and working-context implementation

The current local implementation adds exact frozen-provider-response recovery,
applicability checks before dispatching recovered plans, known control/action
suffix completion, cold-kernel retirement of unstarted work, cooperative Ctrl-C
and read-only diagnostic `status`. Unknown actions and unknown provider outcomes
remain explicit stops; known results do not acquire another call/action charge.

Mind and Execution now share the small pinned Kimi-derived compression core but
own separate derived background/handoff files. Current cognition, received advice,
action state and canonical history remain authoritative. Complete native rounds
stay together; historical reads are bounded and attributed. Whole actual requests
include catalogue/tool/native/correction costs. Capacity is visible rather than
silently deleting knowledge or increasing lifetime limits. Mind also reports
used/remaining accepted-cognition characters before a normal update.

Baseline remains default. `--context-mode mask` and `--context-mode summary`
are explicit, fixed-at-start options for this separate foreground CLI. Small
real comparisons corrected the same scoped false inference in all successful
Mind arms; they did not establish a general summary advantage. The first two
summary replies were rejected by an overly narrow citation contract. The repaired
v2 contract accepted only genuine visible references from the frozen source prefix;
original failures and old protocol interpretations remain unchanged.

The normal CLI task now completed from its original history: Lumina generated a
real three-file provenance manifest, compared conditional CSV/JSON sizes through
isolated analysis, waited for an external byte/hash check, updated its report and
revised the old report-pending beliefs through the same Mind. Final Mind revision
5 has no active activity or pending feedback; quiet resume added no calls.
The actual CSV measured 278 bytes, matching that serialization's calculation;
the unused JSON candidate and full trajectories were not reality-verified.

Live repairs addressed short-history capacity, typed receipt references and
known rejected-summary continuation. New `working-context-v4` makes the local
6000-character count a drafting target; provider output, whole request capacity
and cumulative budgets remain hard. Old failures/protocols are preserved. The
complete task used **48/64 real calls**, including comparisons and all repairs.
Release validation at `734c9b3`: **821 passed, 30 skipped**. The earlier 31-test Docker
run covered computation/action/history mechanisms; the real CLI exercised v4.

This is scoped delivery and recovery evidence. Some older belief wording still
overstates decoded text as original-byte evidence, despite a later directly
cited independent measurement. Summary fidelity/general benefit is not proven,
and baseline remains default. Earlier validation counts above are historical.
See the current
[condensed validation history](../Mind/docs/EXPERIMENT_HISTORY.md) and
[source reuse record](../vendor/kimi_compaction/PROVENANCE.md) for tested scope,
retained failures and default-selection evidence. Detailed campaign reports and
raw provider records are local recovery artifacts, not published dependencies.

The read-only status follow-up fixes a diagnostic omission: an IPython failure
record can coexist with a persisted unknown action outcome. Status now combines
the owner's `unknown_action` with any unclosed action-start reference; a recorded
transport failure does not imply the action never happened. The regression closes
the runtime before querying the CLI and checks unchanged files and model calls.
Execution's existing recovery stop and the default baseline mode are unchanged.
Follow-up validation: **489 passed, 6 skipped** in Mind/Nervous/Execution, including
the focused 64-test regression. No whole-repository or Docker rerun and no real
provider calls were made for this diagnostic fix.

## Scope and limits

- Single-goal baseline, or explicit Stage1 pursuit with serial bounded Tasks;
  foreground operation, one writer and a small authorized workspace. Pursuit
  choices remain inside the original authorization and shared budget. There is
  no background scheduler or unrestricted autonomous goal creation.
- Optional models describe conditional consequences; arbitrary outside-reality
  anomaly detection and full-trajectory validation are not implemented.
- Source references, legal schema, successful computation, delivery and runtime
  markers do not establish semantic correctness or business success.
- UTF-8-sig source text is not a general proof of original bytes.
- Unknown actions/dispatches remain stopped for explicit resolution; resources
  and processing failures remain visible, without fabricated NoChange.
- Retired historical sessions are not migrated. Start a fresh current state.
- Chat, Memory algorithms, Cold-first continuity and manual Dream are unchanged.

Use [INTEGRATED_CHAIN](../Mind/docs/INTEGRATED_CHAIN.md) for setup, input, status,
resume, retry and budgets; [NORTH_STAR](NORTH_STAR.md) for long-term direction.
