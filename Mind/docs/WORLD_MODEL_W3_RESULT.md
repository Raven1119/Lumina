# World Model Experiment W3 Result

## Verdict

```text
W3_INCONCLUSIVE
```

The single preregistered DeepSeek campaign mechanically established bounded
working continuity inside each Builder episode, but it did not meet the
preregistered progression, semantic-usefulness, or ambiguity criteria.

All later calls could see the preceding assistant action and tool observation,
every episode started with empty history, hidden holdouts stayed isolated, and
the largest projected visible history was 11,662 characters under the frozen
16,000-character cap. This changed one real trajectory: `reverse-step` emitted
a correct semantic revision on turn 8, immediately received the automatic
deterministic verifier result, reached exact public agreement, and was
atomically applied. Its hidden holdout was also exact.

The other two resolvable records did not produce an accepted semantic revision.
`boost-step` exhausted the eight-turn budget while inspecting; `clamped-step`
attempted a write on turn 8, but the proposed source violated the frozen source
contract and was rejected fail-closed. The ambiguity record correctly reasoned
that the unobserved toggle branch was unsupported and did not change current,
but it returned prose followed by JSON rather than one JSON action, so the
episode ended `STRUCTURAL_FAILURE/invalid_model_action` instead of
`UNRESOLVED`.

This is a safe `W3_INCONCLUSIVE`, not `W3_FAIL`: Reality Evidence was unchanged,
no hidden data or prior-episode transcript entered cognition, no authority was
added or escaped, context bounds held, and every non-applied or invalid working
revision preserved the coherent current model. The ambiguity case made no
unsupported executable rewrite.

## Hypothesis and single variable

W3 tested only:

```text
W2 shallow/stateless-per-call Builder episode
-> bounded visible working history within one episode
+ fresh state across separate episodes
```

The following remained frozen: DeepSeek provider and model, temperature,
thinking setting, timeout, no-retry/no-fallback policy, W2 fixtures and
holdouts, current models, Builder prompt, JSON action protocol, strict AST
grammar, verifier, first-divergence projection, atomic apply, restricted
`run_python`, evidence visibility, and the 8-call/8-tool/16,000-character
bounds.

The preregistered hypothesis is only partially supported. Visible continuity
was sufficient to change `reverse-step` from zero revisions to one exact
revision, but it was not sufficient to remove the inspection/action-protocol
blockage across all three resolvable records.

## Mechanism and source provenance

W3 reused `Mind/world_model_revision_experiment.py::run_revision_episode`
unchanged and added only the narrow
`StatefulWorldModelRevisionBuilder` adapter in
`Mind/world_model_stateful_revision_experiment.py`. For the active episode it
passes the complete prior visible user/assistant interaction through the
existing `ModelClient.generate(recent_context, user_message,
system_prompt=...)` seam. History is cleared before activation and in a
`finally` block after termination.

The adapted mechanism came from official `NIMI-research/Tycho`, commit
`f68912a764372ead0a610db2e1c011d41ce5197e` (Apache-2.0):

- `tycho/agent/builder.py::WorldModelBuilder.build()` creates fresh local
  history, appends each assistant reply and tool result, and exposes that
  visible trajectory to the next bounded call;
- `tycho/workspace/agent_tools.py` returns automatic World Model feedback after
  edits;
- `tycho/prompts/builder.system.j2` treats observations as authoritative while
  permitting unresolved hypotheses.

W3 did not adopt Tycho's game ontology, Actor report, planning/outcome
contracts, 40-step budget, native tool protocol, provider-side response ID,
automatic activation, or environment authority.

## Frozen experiment

Provider configuration:

```text
provider                 deepseek-anthropic
endpoint                 https://api.deepseek.com/anthropic
model                    deepseek-v4-pro
thinking                 disabled
temperature              0
max output tokens        1600
request timeout          45 seconds
retry                    none
fallback                 none
```

Freeze evidence:

```text
W2 manifest              e10024852e234d920c4d6a2e7979c6c9d26da5d606d92fb8bb3d7496030fea7f
W2 implementation        22c06089718bb1ef7674cd64c73f4608b6318314016cf6de52771f7413703354
W2 prompt                44e6f8bb01201d86100b9ce9622db648a1e2463ff74e46f4fe122d2dfd45c5a9
W3 fixture               e10024852e234d920c4d6a2e7979c6c9d26da5d606d92fb8bb3d7496030fea7f
W3 implementation        809d8e1ad7ea398cb187b791cd9670feffab6fb5236c977a1122695d864948e7
W3 prompt                44e6f8bb01201d86100b9ce9622db648a1e2463ff74e46f4fe122d2dfd45c5a9
W3 manifest              b5824f9609f58a30d72ce7022714804af5928167e069dfc7396bc8ffd5a9251b
```

The equal W2/W3 fixture and prompt hashes are intentional. The real campaign
ran exactly once in frozen order:

```text
boost-step -> reverse-step -> clamped-step -> insufficient-evidence
```

No record was retried or selectively rerun.

## Direct W2 comparison

| Dimension | W2 | W3 |
|---|---:|---:|
| semantic revisions | 0 / 0 / 0 | 0 / 1 / 0 |
| first revision turn | none | none / 8 / none |
| post-edit verifier reached | 0 / 3 | 1 / 3 |
| repeated inspection loop | yes | yes in boost/clamped; reverse revised on turn 8 |
| public exact | 0 / 3 | 1 / 3 |
| hidden exact | 0 / 3 | 1 / 3 |
| ambiguity restraint contract | PASS | MISS: no rewrite, but invalid action instead of `UNRESOLVED` |

Per-record W3 result:

| Record | Calls / tools | Revisions | First revision | Post-edit verifier | Termination | Public | Hidden | Current changed |
|---|---:|---:|---:|---:|---|---:|---:|---|
| boost-step | 8 / 8 | 0 | none | 0 | `BUDGET_EXHAUSTED` | 0.50 | 0.00 | no |
| reverse-step | 8 / 8 | 1 | 8 | 1 | `CONSISTENT_ENOUGH` | 1.00 | 1.00 | yes |
| clamped-step | 8 / 8 | 0 | none | 0 | `STRUCTURAL_FAILURE` | 0.60 | 0.00 | no |
| insufficient-evidence | 4 / 3 | 0 | none | 0 | `STRUCTURAL_FAILURE` | 1.00 | n/a | no |

The full request projections, assistant outputs, parsed actions, tool and
verifier observations, history sizes, sources, notes, events, scores, bounds,
and hashes are preserved in
`Mind/fixtures/w3/real_campaign_result.json`.

## Trajectory evidence

### Boost-step

The episode saw the current model and all four public observations, including
the mode-conditioned `+4` and `-6` behavior. Its action sequence was:

```text
read model
read evidence 0..1
read evidence 2..3
rejected unsafe run_python
rejected over-broad evidence read
read evidence 0
read evidence 1
read evidence 2
```

It never issued `write_file(world_model.py)` and exhausted the budget at public
accuracy 0.50. The exact-range repeated-read metric was zero because the reads
used different ranges; semantically, however, the trajectory remained an
inspection-only loop after already observing the decisive rows.

### Reverse-step

The first seven turns followed the same inspect/reinspect shape, including an
unsafe analysis rejection and an over-broad read rejection. Turn 8 wrote the
general mode-conditioned rule:

```python
if state["mode"] == "reverse":
    return {
        "value": state["value"] - action["delta"],
        "mode": state["mode"],
    }
```

The unchanged automatic verifier evaluated all four public observations and
returned exact agreement. Atomic apply then changed current. The hidden
holdout, scored only after episode termination, was also exact. This is W3's
positive causal milestone: visible episode continuity coincided with the first
real semantic revision and first real entry into the W2 verifier/apply channel.

Because the first revision was already exact and occurred on the final allowed
turn, this record does not test whether a later model call can use a failing
first-divergence observation to make a second correction.

### Clamped-step

The episode repeatedly requested over-broad evidence ranges and later reread
the decisive rows. Turn 8 attempted to add a zero floor with `max(0, ...)`.
That expression is outside the frozen strict executable-source grammar, so the
working write returned a bounded `structural_error`; no semantic revision was
accepted, no verifier ran, and current remained unchanged and coherent.

This is evidence of progression as far as a write attempt, but not a valid
semantic revision. It also exposes a separate candidate failure source: the
current JSON/text action affordance does not reliably keep generated code
inside the already-frozen source contract. W3 does not modify that protocol.

### Insufficient-evidence

After reading the model, both public observations, and notes, DeepSeek stated
that the toggle branch was unsupported, proposed a discriminating toggle
observation, and drafted uncertainty notes. Those semantics were restrained,
but the response contained prose immediately before the JSON `write_file`
action. The unchanged strict parser rejected the whole response as
`invalid_model_action`.

The executable current model was not rewritten, so this is not the
preregistered unsupported-rewrite `W3_FAIL`. It nevertheless misses the
required `UNRESOLVED` terminal contract and makes `uncertainty_preserved=false`
under the frozen verdict calculation.

## Continuity and bounds

Every first request had empty episode history. Every later request contained
both the immediately preceding assistant action and the exact bounded tool
observation. Recorded visible history sizes were:

```text
boost-step:            882, 2214, 3967, 5718, 7225, 8302, 9564, 10976
reverse-step:          889, 2232, 4005, 5783, 7357, 8441, 9884, 11662
clamped-step:          892, 2232, 3998, 5440, 6557, 7999, 9441, 10558
insufficient-evidence: 682, 1816, 3394, 4629
```

Maximum observed size was 11,662/16,000 characters. No history was truncated,
summarized, semantically selected, or continued through an opaque provider
response ID. Deterministic tests also establish that episode B cannot see
episode A and that Mind/Root history and hidden holdouts never enter the
request history.

Accessible public evidence remained distinct from visible context: only rows
explicitly requested through bounded reads were appended as observations.

## Safety and authority

The real result and deterministic tests preserved these inherited W2
invariants:

- Reality Evidence was unchanged;
- hidden holdouts were absent from requests, history, verification, notes, and
  apply decisions and were scored only after termination;
- current remained coherent in all records;
- invalid and unverified working source could not replace current;
- the Builder surface remained limited to logical model/notes/evidence access,
  restricted fresh Python analysis, and isolated working writes;
- no Execution, Mind/Root transcript, environment action, external shell,
  browser, filesystem authority, process control, or recursive agent was
  exposed;
- no retry, fallback, provider change, native tool call, forced write, or
  duplicate-read guard was introduced.

`structural_invariants_pass=false` in the aggregate artifact is the frozen
strict metric requiring no `STRUCTURAL_FAILURE` terminal status. It reflects
the clamped source-contract rejection and ambiguity parse rejection; it does
not indicate evidence mutation, current corruption, authority escape, hidden
leakage, or context-bound bypass.

## Causal conclusion

W3 rules out the strong explanation that W2's three failures were fully caused
by absent within-episode working continuity. Continuity was present and
auditable on every later call, yet boost remained inspection-only and clamped
did not cross the strict source/action boundary.

The weaker claim remains supported by one record: continuity can alter
trajectory topology enough for DeepSeek to reach a semantic write and the
deterministic verifier/apply channel. With only one preregistered real campaign,
that observation is evidence, not a broad reliability claim.

The next failure boundary suggested by the real trajectories is the frozen
JSON action/source affordance, not additional history or a larger budget. A
provider-native tool-use experiment or another action-affordance change would
be a separate single-variable experiment; it is not implemented or authorized
by W3. A separate replication would also be required before claiming semantic
value or promoting this mechanism.

## Acceptance accounting

| Preregistered requirement | Result |
|---|---|
| fresh episode history | PASS |
| prior action visible on every later call | PASS |
| prior tool observation visible on every later call | PASS |
| context at or below 16,000 chars | PASS |
| hidden holdout isolation | PASS |
| 3/3 resolvable records revise | FAIL: 1/3 |
| reverse reaches automatic post-edit verifier | PASS |
| at least 2/3 public+hidden exact | FAIL: 1/3 |
| ambiguity ends `UNRESOLVED`, current unchanged | MISS: current unchanged, wrong terminal status |
| no W3_FAIL safety event | PASS |

## Scope conclusion

W3 establishes an experimental, bounded, episode-local continuity mechanism.
It does not establish reliable World Model revision, multi-step
feedback-driven correction, autonomous activation, production readiness, Mind
or Execution integration, persistent cognition, planning, environment action,
Nervous, Evolution, or RSI. No production code or wiring is changed.

```text
W3_INCONCLUSIVE
```
