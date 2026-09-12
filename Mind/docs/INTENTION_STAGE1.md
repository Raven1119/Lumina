# Intention and Nervous Stage1 runtime contract

Status: implemented, with partial bounded real acceptance. See the
[implementation and verification result](INTENTION_STAGE1_RESULT.md): original
Tasks A and B completed their feedback chains, but the separately authorized
44-call continuation stopped before report correction and complete cognitive
reconciliation. Its corrective guidance remains bound and unexecuted. Historical
verdicts keep their original scope. The
[approved design](../../docs/plan/INTENTION_NERVOUS_STAGE1.md) supplies intent and
acceptance requirements, not evidence that every behavioral claim has passed.

## Entry and authority

The separate foreground CLI opts in at initial launch:

```powershell
.venv/Scripts/python.exe -m Mind start --state .mind-state/pursuit1 --workspace <task-directory> --pursuit "<original authorized scope and permitted follow-up work>" --max-calls 40
.venv/Scripts/python.exe -m Mind status --state .mind-state/pursuit1
.venv/Scripts/python.exe -m Mind resume --state .mind-state/pursuit1
```

State and workspace must be disjoint. The original owner text, authority ref,
Mind identity, workspace and launch mode are retained before organ construction.
The text must actually authorize the scope of any chosen follow-up work. Model
claims and motivation references grant no new permissions or resources. Costs
accumulate across Tasks in the existing provider ledger; only an explicit owner
budget extension adds capacity.

`--goal` / `--goal-file` keeps single-goal operation. Baseline context remains
default in either mode; `mask` and `summary` retain their explicit opt-in meaning.
Use the [operating contract](INTEGRATED_CHAIN.md) for setup, context recovery and
budget commands. Stage1 does not wire this chain into Chat, Memory or Dream.

## Owners and atomic effects

| Owner | Authority |
| --- | --- |
| Mind | Accepted beliefs/questions/scenarios, versioned Intentions, proposed Tasks and reasons for Watch changes; Cognition commits them with final judgment. |
| Nervous | Fixed attention selection, pending responsibility, Watch lifecycle, query routing and causal delivery; no semantic interpretation or action choice. |
| Execution | Task acceptance, Run/action lifecycle, current action admission and original environment observations. |

`cognitive_step` keeps selective cognition updates and an optional `effects`
bundle containing Intention updates, one Task proposal and Watch updates. New
records use local `new:` labels and base revision zero; changes name the existing
ID and base revision. NoChange may commit understanding or effects without
creating execution work. The program checks structure, references, versions,
bounds and authority identity; the model owns semantic judgment.

Intentions carry aim, reason, source/understanding references and a commitment:
candidate, committed, paused or closed. At most one is committed. A Task's
completion does not close its Intention. `influence_refs` may identify genuine
future motivation sources; absent sources remain absent.

A Task is an immutable version with its own ID, goal, acceptance, Intention link
and original authority ref. Mind's latest proposal and Execution's actual accepted
contract remain distinct. Task B waits for A to settle; A's marker, Run or result
does not satisfy B. A current Task may receive a new version at a known waiting
boundary. Execution retains completed actions, retires inapplicable unstarted
work, and blocks unknown outcomes. Task proposals require explicit high-level
direction; Execution still chooses implementation. Closing an Intention requires
explicit treatment of its active Watches and any executing Task.

New cognitive activities freeze `pursuit-commit-v2` integrity checks. An
Intention cannot be closed while its actual accepted Task is nonterminal;
`running` and `waiting` are not settlement, and a different proposal's
`not_accepted` status does not settle that Task. Stage1 adds no cancellation
operation: settle the accepted Task before closing its Intention.

After cognition is retired, every Intention's
`understanding_refs` must still resolve. Repair affected links explicitly in the
same final submission. Hidden retained items remain valid by identity; a current
owner read expands the visible set subject to retirement. Native preflight and
the final owner use the same checks. Existing successful records and unfinished
activities without this frozen rule version keep their original interpretation;
recovery neither rewrites their records nor applies new rules retroactively.

New pursuit activities also freeze
`cognitive_interface="mind-cognitive-interface-v2"`. Their single write path is
`updates`: add new records, replace existing records or submit `status=archived`
to retire them. Unsubmitted records remain accepted. `current` is absent from the
new tool schema and rejected by the final owner; activities without this interface
version retain their original `current` semantics and frozen requests.

These records extend the existing Cognition journal, Nervous mailbox and
Execution owner state. Accepted effects and dependent dispatch use the existing
outbox/receipt boundaries. There is no new TaskManager, parallel authority store,
global transaction manager or transplanted upstream orchestration framework.

## Selection and active reads

Nervous builds the actual Frame from bounded owner views using stable identity
rules: current Task version, committed Intention understanding refs and related
event sources. Scenario headers expose assumption IDs for dependency closure.
Selected IDs, source refs and inclusion/omission reasons are retained with the
Frame; omitted item headers provide exact retrieval references.

Unselected cognition stays accepted. Catalogue entries include an at-most
240-character text preview with an explicit `truncated` flag. These previews help
select reads; they are neither new evidence nor complete editable records.
Updating an unseen item first requires `view:mind.cognition?item_ref=ID` at its
current owner version. The `cognitive-item-v2` envelope contains the complete target
record, retaining its basis and assumption references without inlining source or
assumption bodies. Read those original sources or related items separately when
needed; reading a judgment does not certify its premises. Exact correlated reads
extend editable IDs and preflight without making unread bodies visible. The final
owner still checks the full accepted state and immutable source identity.

Old receipts that inlined basis sources and dependencies remain interpretable.
For old activities that still accept `current`, its selection retires only within
the activity's visible set. The existing read envelope remains bounded: a single
unusually large or heavily escaped item can still exceed it. A capacity/read
failure does not count as a complete read or authorize an unseen update; the
interface does not promise that every possible item fits in one response.

Previous Task goal/acceptance bodies become a compact ID/version/view-ref
catalogue in the actual request. Current authority and accepted Task constraints
remain visible. Working history is scoped to its Task; earlier native requests,
source records and judgments retain their original identities and remain readable.
An owner read of a past judgment establishes what Mind recorded, not its truth.

`read_evidence` supports the registered views:

| Query | Meaning |
| --- | --- |
| `view:execution.state` | Saved current or selected execution state. |
| `view:execution.history` | Bounded projected execution history or an exact retained range. |
| `view:execution.environment` | Explicitly request a fresh authorized environment observation. |
| `view:mind.intentions` | Current commitment/Task records, optionally selected by identity. |
| `view:mind.cognition` | Cognition catalogue or one current complete item. |
| `view:nervous.attention` | Resources, pending/deferred catalogue or a retained event. |

Parameter names and bounds are declared by [the view registry](../../Nervous/views.py).
Each result retains owner, ref, revision, scope and missing/truncation status;
query aliases become immutable `view-result:` identities. Different owner samples
are not a global atomic snapshot. Capacity or failed reads report their actual
condition. Query results resume the original activity; they do not create a new
judgment or replace its frozen Frame/request. Historical truncation cannot be
reconstructed by a later query.

## Watches, controls and recovery

Trusted TriggerSpecs are code; Watch submissions are bounded data. Only
`source.changed` and one-time `review.due` are added. Execution supplies source
observations; Nervous compares their versions. A due Watch persists an absolute
time and opportunity identity, records its actual foreground check time, and does
not replay an opportunity on every restart. An owner can schedule a bounded review
even with no current Task or Intention:

```powershell
.venv/Scripts/python.exe -m Mind resume --state .mind-state/pursuit1 --review-at "<absolute time with timezone>" --review-reason "<authorized review reason>"
.venv/Scripts/python.exe -m Mind resume --state .mind-state/pursuit1 --event STOP --data "<owner reason>"
```

STOP, RESUME and REVOKE are explicit durable owner events in pursuit mode.
Execution checks admission before further actions; ordinary process `resume`
does not clear STOP, and a RESUME event does not undo REVOKE. Ctrl-C retains its
separate cooperative foreground-pause behavior.

Controls take priority over continuation, then ordinary events; equal priorities
retain ready order. Busy events remain pending. Paused intentions defer linked
Watch work; cancelled, closed or superseded versions cannot trigger new applicable
work. Multiple reasons for one observed occurrence retain a single responsibility;
separate observed A-to-B-to-A changes keep separate occurrence identities.

Checks run only in the foreground. `status` neither observes files nor starts a
model. Offline unobserved source changes, periodic heartbeat and unrestricted
exploration are outside this implementation. Received results, effects and
acknowledgements recover through their original owner records; known model
responses keep their frozen requests and charges. Unknown action/provider
outcomes remain stopped. Selection and successful transport do not establish
semantic correctness, general autonomy or subjective experience.

Under the new cognitive interface, a durably received text/thinking-only response
ending in `max_tokens` or `end_turn` may use the activity's one protocol correction.
All protocol corrections, including complete-tool field corrections, share that
single allowance and the existing six-call activity/global budgets. A no-tool
response continues with its original assistant blocks and ordinary user feedback;
no `tool_result` ID, judgment or NoChange is invented. Saved requests/responses
remain unchanged. A second invalid submission remains a failure; unknown dispatch
outcomes are never retried by this mechanism. Old activities keep their original
recovery rules.

For mechanical validation use the affected Mind/Nervous/Execution suites with
isolated state. Behavioral claims require the bounded, persisted acceptance in
the approved design; deterministic responses alone do not establish them.
