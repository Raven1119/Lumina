# Shell-Surface Cross-Experiment Fixture Audit

## Verdict

**INCONCLUSIVE**

The fixture landscape changed in a direction that plausibly explains the
different Shell-visible/Shell-hidden trajectories, but the retained evidence
cannot distinguish that effect from early censoring, model stochasticity, and
other cross-experiment changes. The first experiment's exact per-decision
requests and EventLogs expired, so fixture dependence is a supported candidate
explanation, not an established cause.

## Evidence

This audit is read-only and uses only:

- `SHELL_SURFACE_AB_ARTIFACT.json` and `SHELL_SURFACE_AB_RESULT.md` for the
  exploratory six-run experiment;
- `SHELL_SURFACE_REPLICATION_ARTIFACT.json` and
  `SHELL_SURFACE_REPLICATION_RESULT.md` for the independent six-run
  replication;
- the two frozen fixture builders and experiment harnesses in
  `test_topology_emergence.py`, `test_shell_surface_ab.py`, and
  `test_shell_surface_replication.py`.

The replication evidence is decision-complete: it retains all 48
DecisionFrames, actual provider requests, exposed capabilities, ordered calls,
and observations. Its validity checks establish that each A/B pair differed
only by Shell visibility within that experiment.

The exploratory evidence is materially weaker. Its raw EventLogs and full
per-decision action shapes were not retained. The surviving artifact contains
aggregate actions, observations, outcomes, capability views, and the
classification made during the post-run EventLog audit. It explicitly says
that exact request fingerprints and complete per-decision shapes cannot be
backfilled. `NESTED_TRAJECTORY_AUDIT.md` covers different real executions and
is therefore not used to manufacture the missing exploratory trajectories.

## Pairwise structural comparison

The three cases within each experiment vary names and payloads but share one
structural template. The table nevertheless lists every paired fixture so the
comparison does not collapse case-level outcomes into one aggregate claim.

| Pair / fixture | Fixture structure | Required evidence | Available branches | Direct Read path | Possible Shell shortcut | Possible IPython shortcut | Completion cue |
|---|---|---|---|---|---|---|---|
| 1 / exploratory N1 | `task.txt` names `amber.module` and `cobalt.module`; each module names one leaf | Two module mappings plus integer values in `orchid.leaf` and `cedar.leaf` | Two independent sibling branches immediately after the task | task -> both modules -> both leaves | Directory enumeration or bulk text search could discover/read the same files, but exact next paths are already named | None in the actual experiment: native mode did not expose IPython | Sum the two integers, write only the sum to `answer.txt`, then claim completion |
| 1 / replication R1 | `mission.md` names one `routes/atlas.k17`; the route orders three records | Route ordering plus payloads from `rust.q1`, `tide.q2`, and `puma.q3` | One bottleneck until the route, then three fully enumerated ordered leaves | mission -> route -> three records | Recursive listing/search or bulk file reads are possible, but add little once the route is read | Persistent IPython could use `pathlib`, `os`, or `subprocess` to enumerate/read; it was exposed and unused | Join three ordered payloads with `/`, write only the result to `outcome.txt`, then claim completion |
| 2 / exploratory N2 | `task.txt` names `lunar.module` and `solar.module`; each module names one leaf | Two module mappings plus integer values in `delta.leaf` and `echo.leaf` | Two independent sibling branches immediately after the task | task -> both modules -> both leaves | Same optional enumeration/bulk-search path as N1; it is not required | None in the actual experiment | Sum the two integers, write `answer.txt`, claim completion |
| 2 / replication R2 | `mission.md` names one `routes/ledger.m42`; the route orders three records | Route ordering plus payloads from `jade.r4`, `finch.r5`, and `umber.r6` | One bottleneck, then three ordered leaves | mission -> route -> three records | Same optional recursive/bulk-search path as R1 | Same exposed but unused persistent-IPython path as R1 | Ordered `/` join, write `outcome.txt`, claim completion |
| 3 / exploratory N3 | `task.txt` names `north.module` and `south.module`; each module names one leaf | Two module mappings plus integer values in `birch.leaf` and `maple.leaf` | Two independent sibling branches immediately after the task | task -> both modules -> both leaves | Same optional enumeration/bulk-search path as N1 | None in the actual experiment | Sum the two integers, write `answer.txt`, claim completion |
| 3 / replication R3 | `mission.md` names one `routes/chart.v08`; the route orders three records | Route ordering plus payloads from `fern.s7`, `meteor.s8`, and `pearl.s9` | One bottleneck, then three ordered leaves | mission -> route -> three records | Same optional recursive/bulk-search path as R1 | Same exposed but unused persistent-IPython path as R1 | Ordered `/` join, write `outcome.txt`, claim completion |

The meaningful contrast is not file count or text length. The exploratory task
presents two symmetric unresolved branches and an integration operation as
soon as `task.txt` is read. The replication presents one named route followed
by an explicit ordered list, making direct Read the obvious serial local
action. That geometry could make Shell compete with branch abstraction in the
first landscape while direct Read dominates both arms in the second.

This mechanism remains only a candidate. Both fixture families have finite,
fully named dependency graphs and explicit write/verification conditions.
Shell is unnecessary in both. The replication's directory layout actually
makes recursive Shell or IPython search at least plausible, yet all six runs
ignored both surfaces.

## Trajectory divergence analysis

| Pair | Exploratory A/B first divergence | Exploratory repetitive-search onset | Replication A/B first divergence | Replication repetitive-search onset |
|---|---|---|---|---|
| 1 | Exact decision not retained. Aggregate evidence shows A used Shell 6 times and never reached leaves; B used only Read, reached all leaves, then stalled. | A exact onset unknown. B made three productive decisions before a residual Read stall; the exact first repeated request is unavailable. | None through D1-D8. Both arms produced the same ordered Read actions and observations. | First repeat at D4 (`mission.md`); the preregistered two-no-progress threshold was reached at D5 (`routes/atlas.k17`). |
| 2 | Exact decision not retained. A used Shell 5 times and missed the leaves; B reached all five dependencies through direct Reads. | A exact onset unknown. B had no executed repetitive-search classification before its D4 response was rejected for six Reads over the four-call cap; the six arguments were not retained. | None through D1-D8. | First repeat at D4; threshold reached at D5 (`routes/ledger.m42`). |
| 3 | Exact decision not retained. A used Shell 4 times; B read one relevant object, then emitted two Spawn calls that whole-admission rejected. | A exact onset unknown. B terminated at D2, before a repetitive pattern could be observed. | None through D1-D8. | First repeat at D4; threshold reached at D5 (`routes/chart.v08`). |

Every replication run followed:

```text
D1 mission (new)
D2 route (new)
D3 three records (new; complete leaf evidence)
D4 mission (repeat)
D5 route (repeat)
D6 three records (repeat)
D7 mission (repeat)
D8 route (repeat; decision limit)
```

The two exploratory B runs classified as avoiding repetitive search need
separate treatment:

- **B2 versus replication:** B2 obtained complete evidence, then ended on its
  D4 over-cap six-Read response. Because the ordered target arguments were not
  retained, this can be genuine productive acquisition or early censoring at
  the same point where every replication run began rereading. It cannot prove
  avoidance of the replication's basin.
- **B3 versus replication:** B3 selected two sibling Spawns at D2 and was
  rejected before Child identity creation. The old two-branch layout plausibly
  contributed to that decomposition intent, whereas the single-route
  replication emitted no Spawn. One truncated run cannot separate fixture
  geometry from stochastic model choice.

Thus the exploratory `B repetitive = 1/3` and replication `B repetitive =
3/3` are not exposure-equivalent observations: only exploratory B1 survived
for eight decisions, and that run was repetitive.

## Best-supported explanation

The best-supported candidate is an **affordance-landscape interaction**:

```text
exploratory: two unresolved sibling branches
             -> several salient resolution strategies
             -> visible Shell co-occurred with search; hidden Shell exposed
                direct Read or Spawn trajectories

replication: one named route followed by an ordered leaf list
             -> direct Read is the dominant local action in both arms
             -> after complete evidence, both arms remain in the same Read
                reread basin
```

This account explains the direction of the observed actions without claiming
that Shell caused the difference. In the exploratory A runs, Shell was
actually selected 4-6 times and no leaves were reached. In the replication,
Shell visibility changed request size but never action choice; paired actions
were identical.

## What remains unexplained

- Why every replication run reread the complete dependency chain instead of
  writing and claiming completion. The fixture supplies a closed completion
  cue, so further search was not required by task structure.
- Whether exploratory B2 would have entered the same reread loop if its D4
  six-Read response had not crossed the fixed cardinality cap.
- Whether exploratory B3's Spawn batch was induced by the two-branch fixture
  or was a stochastic choice.
- The exploratory experiment used native mode, while the replication used a
  hybrid surface with persistent IPython in both arms. Goal wording, entry and
  output names, directory layout, synthesis operation, source bytes, and run
  order also changed. Within-experiment A/B validity does not remove these
  cross-experiment confounds.
- The exploratory classifier used a coarser v1/post-run projection, whereas
  the replication used retained marker-level decisions. The recorded verdicts
  remain valid under their preregistrations, but exact onset is not comparable.

These gaps prevent both `FIXTURE_DEPENDENCE_SUPPORTED` and
`FIXTURE_DEPENDENCE_NOT_SUPPORTED`.

## Implication for next experiment

Do not proceed automatically to a Wide-versus-Prime-like surface A/B. If a
further experiment is authorized, isolate fixture geometry first with one
preregistered paired transform that holds lexical content, provider surface,
prompt, operation, budgets, and run order fixed while changing only
two-sibling branching versus one ordered route. Until then, fixture dependence
remains a candidate explanation rather than a causal result.
