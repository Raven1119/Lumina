# Shell Surface A/B Result

## 1. Scope

This experiment tested one variable on the frozen Nested N1/N2/N3 fixtures:

- A: the current native tool surface;
- B: the same surface with only Shell hidden from the model request.

Runtime semantics, DeepSeek-V4-Pro non-thinking settings, Goal, system prompt,
tool descriptions, fixtures, Context bound, eight-decision limit, four-call
limit, depth/Actor limits, sequential drive, verifier and ToolHost were held
fixed. Exactly six fresh executions ran once in A1, A2, A3, B1, B2, B3 order.
No Flat execution or replacement run was made.

## 2. Evidence seam

DeepSeekModel has one default-on shell-visible switch. When false, it removes
only Shell from the existing model-visible tool-contract projection and wire
schema. The default A path is unchanged. ToolHost, ShellRequest, cwd,
timeouts, bounds and Windows behavior are unchanged.

The existing sequential Child driver passes the same switch to every model it
creates. Deterministic public-seam tests establish the actual Root, depth-one
Child and depth-two Child requests. Read, Write, Wait, Spawn where depth
permits, ClaimComplete for Root, Return for Child, and the configured IPython
surface remain available. This is an experimental visibility treatment, not a
security boundary or general policy framework.

The sanitized v1 artifact records EventLog-derived Action counts, failures,
the original semantic-target repeat count, dependency observations, topology,
completion, request sizes, provider usage, wall time and actual model-visible
tool-name views. It stores no arguments, file/user content, credentials,
headers, hidden reasoning or full provider response. It now also records the
already-audited per-run classification and B3 control-batch shape.

The original pytest EventLogs were transient and expired after the run. Under
the frozen no-rerun rule, exact request-fingerprint repeats and complete
per-decision action shapes cannot be backfilled. The maintained helper now
separates exact repeated observations (request fingerprint plus outcome) from
semantic repeated local-search decisions and records sanitized ordered action
shapes. Deterministic tests cover distinct Shell requests and a two-Spawn
batch; these tests improve future evidence fidelity but do not manufacture
new facts about the six completed real runs.

## 3. Per-run evidence

The observation partition follows the prior trajectory audit. A first
successful read of task, module or leaf dependency is relevant. A repeated
semantic target was counted as repeated by the v1 projection. A first failed
or unused local-search target is irrelevant/unused. Failures are reported
separately and may overlap the last two categories. Because v1 grouped Shell
requests into coarse semantic categories, its repeat numbers can conflate
distinct commands; the classifications below come from the post-run EventLog
trajectory audit rather than that aggregate count alone.

| Run | Classification | Terminal | Decisions | Read / Shell / IPython | Failed | Relevant / irrelevant / repeated | Task rereads | Leaf evidence |
|---|---|---|---:|---:|---:|---:|---:|---|
| A1 | REPETITIVE_LOCAL_SEARCH | decision limit | 8 | 5 / 6 / 0 | 4 | 3 / 1 / 7 | 2 | no |
| A2 | REPETITIVE_LOCAL_SEARCH | 6 recorded ordinary Reads over cap | 6 | 2 / 5 / 0 | 4 | 1 / 1 / 5 | 1 | no |
| A3 | REPETITIVE_LOCAL_SEARCH | decision limit | 8 | 6 / 4 / 0 | 3 | 3 / 1 / 6 | 3 | no |
| B1 | REPETITIVE_LOCAL_SEARCH | decision limit | 8 | 12 / 0 / 0 | 4 | 5 / 3 / 4 | 1 | yes |
| B2 | PRODUCTIVE | 6 recorded ordinary Reads over cap | 4 | 5 / 0 / 0 | 0 | 5 / 0 / 0 | 0 | yes |
| B3 | OTHER | two-Spawn child-limit rejection | 2 | 1 / 0 / 0 | 0 | 1 / 0 / 0 | 0 | no |

All six runs had one Root Actor at depth zero and no committed Child. B3 is
the sole Spawn intent: the adapter accepted two homogeneous Spawn actions with
present, unique provider call IDs, but the frozen depth-two mode permits one
Child per Actor, so whole admission failed before identity creation. This is
neither evidence of a malformed response nor a new tool-surface failure.

| Run | Model calls | Input / output tokens | Wall seconds | Spawn intent / committed | Verified |
|---|---:|---:|---:|---:|---|
| A1 | 8 | 6,945 / 571 | 12.914 | 0 / 0 | no |
| A2 | 6 | 5,389 / 564 | 10.614 | 0 / 0 | no |
| A3 | 8 | 7,700 / 519 | 12.770 | 0 / 0 | no |
| B1 | 8 | 6,740 / 542 | 13.741 | 0 / 0 | no |
| B2 | 4 | 2,970 / 426 | 6.556 | 0 / 0 | no |
| B3 | 2 | 1,522 / 186 | 3.606 | 2 / 0 | no |

Every A DecisionFrame exposed Read, Write, Shell, Wait, SpawnChild and
ClaimComplete. Every B DecisionFrame exposed the same set except Shell. No
run used IPython because the frozen experiment used native mode; deterministic
coverage establishes that the separate configured IPython surface was not
removed by the treatment.

## 4. Preregistered verdict

**SHELL_AFFORDANCE_INTERFERENCE_SUPPORTED**

- A repetitive local search: 3/3, satisfying at least 2/3.
- B repetitive local search: 1/3, satisfying at most 1/3.
- B reached a stage absent from every A run in 2/3 cases: B1 and B2 both
  obtained all required leaf evidence, while A reached leaf evidence in 0/3.

B1 remained a residual read-based local stall after its first three productive
decisions. B2 was productive through all five dependencies, then its fourth
response contained six recorded ordinary Reads with present unique IDs, JSON
object arguments and action-schema-valid arguments, crossing the unchanged
local cardinality cap. The pre-admission v1 seam did not retain the provider
envelope type field, so this report does not upgrade the batch to fully protocol-
legal. B3 selected delegation, but selected two siblings where the frozen
mode admitted one; no Child was committed.

## 5. Scientific interpretation

The result supports the narrow causal claim that model-visible Shell
affordance interfered with progress on these frozen Nested landscapes. With
Shell visible, all three trajectories repeatedly pursued directory search and
none reached leaves. With Shell hidden, direct named-path Reads replaced Shell
entirely and two trajectories reached all leaves.

The result does not establish that Shell is generally harmful, that hiding it
improves completion, or that the model prefers recursion. Completion remained
0/3 in both conditions. No Child was committed, so the topology-emergence
verdict remains NOT VALIDATED. B2 still exposes the independent local
cardinality boundary, and B3 exposes an existing one-Child admission boundary;
neither is evidence of a malformed provider response or a broken hidden-tool
schema. The experiment does not justify changing those bounds, adding a
search tool, strengthening prompts, adding Spawn heuristics or promoting this
research switch into a security policy.

The six EventLogs were canonical during the gated test and post-run audit; the
pytest copies were not retained as permanent research artifacts. The surviving
maintained projection is SHELL_SURFACE_AB_ARTIFACT.json, whose evidence note
states that limitation explicitly.

## 6. Exactly one recommended next action

Run one preregistered independent-fixture replication of the same current
versus Shell-hidden A/B, holding every non-fixture variable fixed, before
changing Runtime or resuming topology claims.
