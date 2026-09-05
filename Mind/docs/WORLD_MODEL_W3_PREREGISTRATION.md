# World Model Experiment W3 Preregistration

## Frozen question and single variable

W3 asks whether W2's inspection-only trajectories were primarily caused by
missing working continuity inside one Builder activation.

The only changed mechanism is:

```text
single_variable:
preserve bounded visible Builder working history within one activation episode
while retaining fresh state across separate episodes
```

W3 does not increase capability. It retains the actual visible user/assistant
interaction of the active episode so the next model request can see what was
already inspected, which action was requested, and which bounded observation or
automatic verifier result followed. The history is cleared before and after
each activation.

## W2 diagnosis and exact seam

W2 constructed every call as:

```python
ModelClient.generate([], reconstructed_context, system_prompt=BUILDER_SYSTEM_PROMPT)
```

`reconstructed_context` contained the compact verifier state, handles, budgets,
and at most the two most recent tool observations. The prior assistant JSON,
exact action arguments, and older visible observations were not present in the
next request. This is where continuity was lost.

W3 changes only the model-call seam:

```text
first call:
recent_context = []
user_message = frozen W2 context projection

later call:
recent_context = all prior visible user/assistant messages in this episode
user_message = current frozen W2 context projection, including latest tool result
```

The model interface remains `ModelClient.generate(recent_context, user_message,
system_prompt=...)`. W3 does not change `core/model_client.py`. The deep module
remains the existing W2 episode runner; W3 is a narrow adapter that supplies and
then destroys one activation's bounded visible history.

## Frozen W2 facts

The preserved W2 real result is `W2_INCONCLUSIVE`:

```text
boost-step              0 revisions, BUDGET_EXHAUSTED
reverse-step            0 revisions, BUDGET_EXHAUSTED
clamped-step            0 revisions, BUDGET_EXHAUSTED
insufficient-evidence   0 revisions, UNRESOLVED, no rewrite
provider failures       0
authority invariants    PASS
```

W2 is not rerun or modified by W3.

## Source audit

### Lumina mechanisms reused unchanged

- `Mind/world_model_revision_experiment.py::run_revision_episode`: W2 JSON
  action protocol, explicit host activation, tool dispatch, automatic verifier,
  first-divergence projection, failure semantics, and atomic apply.
- `Mind/world_model_builder_experiment.py`: exact W1/W2 DTOs, strict AST
  grammar, isolated evaluator, and frozen public evidence.
- `core/model_client.py::DeepSeekAnthropicModelClient`: visible message
  projection and the exact DeepSeek Anthropic-compatible provider seam.
- `Mind/fixtures/w2/manifest.json`: all three resolvable records, ambiguity
  record, public evidence, current models, and verdict-only hidden holdouts.

### Tycho mechanism adapted

Official source: `NIMI-research/Tycho`, commit
`f68912a764372ead0a610db2e1c011d41ce5197e`, Apache-2.0.

`tycho/agent/builder.py::WorldModelBuilder.build()` creates a fresh local
`history`, appends each assistant reply (including exposed reasoning/tool call),
executes the tool, appends tool results, and supplies that visible trajectory to
the next bounded call. `tycho/workspace/agent_tools.py` returns automatic World
Model feedback as part of the edit result. `tycho/prompts/builder.system.j2`
keeps observations authoritative and permits unresolved hypotheses.

W3 adapts only that explicit local-history mechanism. It does not copy Tycho's
game ontology, Actor report, planning, outcome, PNG/grid logic, 40-step default,
provider-side response continuation, native tool protocol, action surface, or
automatic activation.

## Frozen artifacts and hashes

```text
W2 manifest       e10024852e234d920c4d6a2e7979c6c9d26da5d606d92fb8bb3d7496030fea7f
W2 implementation 22c06089718bb1ef7674cd64c73f4608b6318314016cf6de52771f7413703354
W2 prompt         44e6f8bb01201d86100b9ce9622db648a1e2463ff74e46f4fe122d2dfd45c5a9

W3 fixture        e10024852e234d920c4d6a2e7979c6c9d26da5d606d92fb8bb3d7496030fea7f
W3 implementation 809d8e1ad7ea398cb187b791cd9670feffab6fb5236c977a1122695d864948e7
W3 prompt         44e6f8bb01201d86100b9ce9622db648a1e2463ff74e46f4fe122d2dfd45c5a9
W3 manifest       b5824f9609f58a30d72ce7022714804af5928167e069dfc7396bc8ffd5a9251b
```

The identical W2/W3 prompt hash is intentional. There is no stronger editing
instruction, duplicate-read rejection, forced progression, or protocol change.

## Frozen bounds

| Boundary | Value |
|---|---:|
| model turns / tool calls | 8 / 8 |
| projected visible episode context | 16,000 chars |
| one evidence read | 2 observations |
| one file read | 4,000 chars |
| verifier observation | 2,400 chars |
| model output / model source | 6,000 / 4,000 chars |
| notes | 4,000 chars |
| fresh Python runtime | 2 seconds |
| Python source / output | 2,000 / 2,000 chars |

The context calculation is the sum of visible user/assistant text passed through
the existing `ModelClient` seam, matching W2's exclusion of the separately
frozen system prompt. It is checked before a provider call. W3 does not drop,
summarize, retrieve, or semantically select old episode messages. If the full
visible trajectory would exceed 16,000 characters, the episode ends
`BUDGET_EXHAUSTED` with `context_bound_exhausted` and current remains unchanged.

A deterministic eight-turn run over the frozen clamped fixture stays within the
unchanged bound. No duplicate-read guard is installed.

## Frozen provider

```text
provider        deepseek-anthropic
endpoint        https://api.deepseek.com/anthropic
model           deepseek-v4-pro
thinking        disabled
temperature     0
max tokens      1600
timeout         45 seconds
retry           none
fallback        none
environment     DEEPSEEK_API_KEY
```

The campaign order is fixed:

```text
boost-step
reverse-step
clamped-step
insufficient-evidence
```

It is run exactly once. An existing destination is refused. Freeze checks run
at load, before every model call, before every record, and on both sides of the
atomic result write.

## Primary recorded metrics

For each resolvable record W3 records:

```text
repeated-read count
distinct evidence ranges inspected
first semantic revision turn
semantic revision count
post-edit verifier count
second semantic revision turn
termination status
public accuracy
hidden accuracy
episode history size per turn
previous action visible
previous tool observation visible
```

The result also preserves each request projection, assistant output, parsed
requested action, tool/verifier result, final current and working source, notes,
events, and provider identity. Hidden holdouts are scored only after activation
termination and never enter a request, history, verifier, or apply decision.

## Preregistered verdicts

`W3_PASS` requires all of:

- all W2 structural and authority invariants remain intact;
- first request of every activation has empty episode history;
- all subsequent requests retain the prior visible action and tool observation;
- every request stays within 16,000 characters;
- hidden holdouts remain isolated;
- all three resolvable records emit at least one semantic revision within eight
  turns;
- reverse-step reaches an automatic post-edit verifier observation;
- at least two resolvable records finish with exact public and hidden accuracy;
- ambiguity ends `UNRESOLVED` without changing current.

`W3_INCONCLUSIVE` is any safe run that misses a continuity, revision, verifier,
or semantic-usefulness threshold, including provider failure.

`W3_FAIL` is any Reality Evidence mutation, hidden leakage, current corruption,
authority escape, context-bound bypass, unsupported ambiguity rewrite, or prior
episode transcript leakage.

## Pre-campaign evidence

The TDD sequence began with a missing-module import failure, then established
13 focused W3 tests. They inspect the actual `ModelClient.generate` arguments,
not merely an internal history object. W2+W3 regression currently reports 32
passing tests. Spec review and Architecture/Safety review both returned PASS.

No production wiring, automatic trigger, Execution/Mind authority, persistent
reasoning state, planner, retrieval layer, or new framework is authorized by
this preregistration.
