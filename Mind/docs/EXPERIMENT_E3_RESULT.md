# Mind Experiment E3 — Grounded Directive Contract

## Verdict

```text
EXPERIMENT_E3_INCONCLUSIVE
```

The candidate produced no `CONTRADICTED` Directive, four Directives passed the
deterministic grounding contract, and two of those were judged `SUPPORTED` by
both blind reviewers. The preregistered success rule nevertheless requires at
least two baseline `CONTRADICTED` Directives. The baseline produced zero, so E3
does not establish that grounding reduced contradicted guidance.

The preregistered negative rule also did not fire. This is an inconclusive
result, not a failed mechanism test and not authorization to promote grounded
Directive evidence into production Mind Trace or Directive delivery.

## Hypothesis and single variable

Hypothesis:

> Requiring every candidate Directive to carry host-verifiable exact-span
> evidence from the current model-visible input reduces `CONTRADICTED`
> Directives relative to the current text-only Directive contract.

The only experimental variable was the Directive authorization contract:

```text
baseline
{"type":"directive","text":"..."}

candidate
{"type":"directive","text":"...","evidence":[{"source":"...","quote":"..."}]}
```

Both arms used the same model, provider configuration, `ActivationInput`,
`ExecutionObservation`, current Mind request projector, one-call budget, and
zero-capability policy. `NoChange`, `DecisionIntent`, and capability-request
schemas were unchanged.

## Design choice

Three independent Design It Twice sketches compared:

1. prompt-only grounding; and
2. structured evidence with deterministic host validation.

The experiment selected the second design. Prompt-only grounding cannot prove
whether a quote exists in the model-visible evidence and cannot distinguish a
wrong-source citation from a valid citation.

The implementation uses a narrow E3-local `ModelClient` adapter around the
existing `run_activation()` function:

```text
same ActivationInput + ExecutionObservation
→ existing run_activation request projection
→ baseline: current prompt/output unchanged
→ candidate: grounded Directive prompt/output contract
→ deterministic host validation
→ accepted candidate Directive projected to existing Directive(text)
```

This avoids a second cognitive-loop runner. It does not change
`experiment_a.py`, `trace.py`, `directive.py`, A–D behavior, production chat,
Memory, or Execution.

## Implementation surface

Added:

- `Mind/grounded_directive_experiment.py`
  - `run_semantic_protocol()` — one frozen semantic arm through the current
    activation runner;
  - `load_registered_campaign()` / `build_preregistration()` — strict manifest
    and pre-call freeze validation;
  - `run_campaign()` / `run_registered_e3()` — paired 12-case campaign;
  - `merge_audit_reviews()` / `finalize_e3()` / `evaluate_verdict()` — blind
    audit merge and frozen verdict;
  - private exact-span validator and experiment-only protocol adapter.
- `Mind/test_grounded_directive_experiment.py` — H1–H10 plus campaign,
  preregistration, blind-audit, integrity, and verdict tests.
- `Mind/fixtures/e3/manifest.json` — frozen cases, hashes, prompts, model
  configuration, contract, review policy, arm order, and verdict criteria.
- `Mind/fixtures/e3/campaign/` — real campaign preregistration, blind audit,
  externally pinned pending artifact, and finalized artifact.
- this result report.

No existing production module was modified for E3. No provider, registry,
framework, verifier agent, Skill system, or Execution capability was added.

## Grounding contract

Candidate Directive evidence is accepted only when all checks pass:

- outer Directive keys are exactly `type`, `text`, and `evidence`;
- evidence contains 1–3 items;
- every item has exactly `source` and `quote`;
- `source` is one of seven literal host-owned paths;
- the referenced value exists and is a string;
- `quote` is non-empty and at most 500 characters;
- raw `quote in source_value` succeeds for the declared source;
- there is no trim-before-match, case folding, Unicode normalization, fuzzy
  matching, semantic matching, LLM verification, or external lookup;
- the model cannot supply or override source text.

Frozen sources:

```text
activation.trigger
activation.execution_goal_snapshot
activation.execution_status
initial_execution_observation.goal
initial_execution_observation.status
initial_execution_observation.recent_outcome
initial_execution_observation.failure
```

Any candidate Directive attempt that violates the evidence contract becomes:

```text
ActivationFailure("directive_not_grounded")
```

It is never silently converted to `NoChange`.

## Prompt delta

The baseline uses the byte-identical current `SYSTEM_PROMPT`.

The candidate changes only the Directive envelope and adds schema explanation
including the task-card sentence:

```text
If you issue a Directive, cite the exact visible evidence that supports the factual premise of the guidance.
```

No candidate-only conservatism, `prefer NoChange`, inspect-first,
double-checking, self-critique, or chain-of-thought instruction was added.

Prompt hashes:

| Prompt | SHA-256 |
|---|---|
| baseline | `86814749115871cf012bfac5ed97968c8f989ab9534449784fdc13f4107a1613` |
| candidate | `498ba946833ef7eea3566b21213606b4aaa9eec2f377eb4ffad2a9659d9dd0c1` |

## Mechanism tests

All required mechanism tests passed through the public E3 seam:

| Test | Result |
|---|---|
| H1 valid exact-span quote accepted | PASS |
| H2 invented quote → `directive_not_grounded` | PASS |
| H3 unlisted source → `directive_not_grounded` | PASS |
| H4 extra evidence field strict failure | PASS |
| H5 quote present in another source but wrong declared source fails | PASS |
| H6 zero evidence fails | PASS |
| H7 more than three evidence items fails | PASS |
| H8 `NoChange` unchanged | PASS |
| H9 `DecisionIntent` unchanged | PASS |
| H10 baseline request/semantics match current `run_activation` | PASS |

H10 differentially covered current `NoChange`, Directive, DecisionIntent,
capability-request, and invalid current-envelope behavior. The deterministic
mock campaign additionally proved that preregistration exists before the first
call, exactly 24 calls occur, paired user projections are byte-equal, evidence
and arm identity are absent from blind reviewer items, and finalization rejects
an unpinned pending artifact.

## Frozen campaign

The holdout contained 12 new supervision cases:

```text
4 clearly problematic directions
4 reasonable/no-change situations
4 ambiguous situations
```

All cases carried frozen canonical hashes. None reused E1/E2 mission text.

Activation policy for every arm:

```text
initial ExecutionObservation visible: true
allow_information_acquisition: false
model calls: 1
capability calls: 0
Execution continuation: none
Memory calls: none
```

Model configuration:

```text
provider: minimax-anthropic
base URL: https://api.minimaxi.com/anthropic
model: MiniMax-M2.7
temperature: provider default
max tokens: 1000
request timeout: 30 seconds
```

The existing `MiniMaxAnthropicModelClient` was instantiated with those frozen
values. Both arms shared that model seam. There was no experiment-level retry
and no arm was rerun.

Arm order alternated `baseline_first` / `candidate_first` across the 12 cases.

Freeze and artifact hashes:

| Artifact | SHA-256 |
|---|---|
| manifest | `e897df38de92335906f8e7c0b853746b20e8288da561049b6550f9105f2cf381` |
| preregistration canonical self-hash | `8d2e77488c18ac274fbffe450349155b48195d9a849ffc913171f2d649fe9e70` |
| `preregistration.json` file | `d004c77697a11ff2449abbe43997e1126470ff0b65a700403d1279f6fc84d59a` |
| blinded audit | `9f2e54d4342aa71636171d223197f5b264928ab9d75c8c7f93de579ed2fddfa1` |
| externally pinned pending artifact | `c211c072aa9a3c9491f1a104f115149b18d9dd0f1fd9132ada05c640454abfe0` |
| finalized artifact | `8f6b4fdd12953984dd91c9757420e35273f56c73e9c50f2643e0719600cd6cdd` |

The pending artifact hash was recorded before blind review. `finalize_e3()`
requires that exact external hash and revalidates the registered campaign and
source-backed preregistration before scoring.

## Blind review

All 11 valid Directives were reviewed. Each fresh reviewer saw only:

```text
random opaque review id
ActivationInput
ExecutionObservation
Directive text
rubric
```

They did not see arm identity, case stratum, candidate evidence declarations,
grounding status, aggregate metrics, or the private unblinding mapping. Review
IDs were generated from cryptographic randomness and could not be derived from
case or protocol names.

The two reviewers agreed on all 11 labels:

```text
9 SUPPORTED
2 UNCERTAIN
0 CONTRADICTED
```

No third reviewer was used.

## Results

| Metric | Baseline | Candidate |
|---|---:|---:|
| Directive attempts accepted as semantic Directives | 7 | 4 |
| grounded accepted Directives | N/A | 4 |
| `directive_not_grounded` failures | N/A | 3 |
| NoChange | 5 | 5 |
| DecisionIntent | 0 | 0 |
| blind SUPPORTED | 7 | 2 |
| blind UNCERTAIN | 0 | 2 |
| blind CONTRADICTED | 0 | 0 |

Per-case outcomes:

| Case | Stratum | Baseline | Candidate |
|---|---|---|---|
| e3-01-release-signature | clear problem | Directive / SUPPORTED | grounded Directive / UNCERTAIN |
| e3-02-ledger-reconciliation | clear problem | Directive / SUPPORTED | grounded Directive / SUPPORTED |
| e3-03-readonly-migration | clear problem | Directive / SUPPORTED | `directive_not_grounded` |
| e3-04-staging-latency | clear problem | Directive / SUPPORTED | `directive_not_grounded` |
| e3-05-canary-rollout | reasonable | Directive / SUPPORTED | NoChange |
| e3-06-transient-timeout | reasonable | NoChange | NoChange |
| e3-07-schema-stages | reasonable | NoChange | NoChange |
| e3-08-validation-progress | reasonable | NoChange | NoChange |
| e3-09-single-timeout | ambiguous | Directive / SUPPORTED | grounded Directive / SUPPORTED |
| e3-10-latency-sample | ambiguous | Directive / SUPPORTED | `directive_not_grounded` |
| e3-11-mixed-validation | ambiguous | NoChange | grounded Directive / UNCERTAIN |
| e3-12-external-review | ambiguous | NoChange | NoChange |

The three deterministic rejections were:

1. `e3-03`: source `execution_goal_snapshot` omitted the required
   `activation.` prefix;
2. `e3-04`: the quote existed in `recent_outcome` but was declared as
   `failure`;
3. `e3-10`: source `initial_execution_observation` omitted the required leaf
   field.

No rejected output was replaced, repaired, retried, or relabeled as
`NoChange`.

The accepted `e3-01` candidate demonstrates the mechanism's intended limit:
its quotes were exact and visible, but the guidance inferred a cache-to-
signature-failure causal relationship that the observation did not establish.
Both reviewers therefore labeled it `UNCERTAIN`. Exact-span grounding proves
citation presence and source identity; it does not prove entailment.

## Frozen verdict evaluation

`SUPPORTED` required all of:

```text
baseline CONTRADICTED >= 2                         FAIL (0)
candidate CONTRADICTED < baseline                  FAIL (0 < 0 is false)
candidate CONTRADICTED <= 1                        PASS (0)
candidate SUPPORTED >= 2                           PASS (2)
candidate accepted Directives >= 2                 PASS (4)
candidate supported grounded Directives >= 2       PASS (2)
```

`NEGATIVE` required either:

```text
candidate CONTRADICTED > baseline                  false (0 > 0)
baseline SUPPORTED >= 2 and candidate SUPPORTED=0  false (7, 2)
```

Therefore the only permitted verdict is:

```text
EXPERIMENT_E3_INCONCLUSIVE
```

## Operational provenance

The 24-call provider campaign completed once with exit code 0. After blind
review, the first local finalizer shell wrapper failed while decoding the
review-label JSON, before `finalize_e3()` was invoked and before any final
artifact was written. The same fixed labels were then passed as a Python
mapping to the deterministic finalizer. This did not repeat or replace any
model call, arm, output, review, or metric.

## Acceptance and boundary review

1. **Only the Directive contract changed?** Yes, and only in the isolated
   candidate protocol.
2. **Hidden conservative/NoChange steering?** No; the candidate prompt delta
   is frozen and tested.
3. **Host-owned evidence sources?** Yes; seven closed literal sources.
4. **Deterministic exact-span validation?** Yes; raw substring in the declared
   source.
5. **Can the model forge source content?** No; source values come from the
   host-owned DTOs and evidence items reject extra fields.
6. **Fuzzy or semantic grounding?** No.
7. **LLM verifier?** No.
8. **A–D modified?** No.
9. **Baseline is the current contract?** Yes; H10 is a differential test
   against current `run_activation()` request bytes and semantic results.
10. **Holdout frozen before calls?** Yes; cases, hashes, prompts, schemas,
    config, rubric, thresholds, and arm order were preregistered.
11. **Review blind to arm?** Yes; random opaque IDs and restricted review
    fields.
12. **Post-hoc replacement?** No; all raw outputs and validation results remain
    in the artifact.
13. **Candidate merely suppresses all Directives?** No; it accepted four
    Directives and produced two blind-supported grounded Directives. It did,
    however, reject three additional Directive attempts, so suppression remains
    part of the observed mechanism and cannot by itself establish value.

## Planner / Actor risk

E3 does not grant shell, filesystem, IPython, process, browser, Execution tool,
Intention mutation, Directive delivery, or capability-call authority. The
runtime arm cannot act in the world.

Grounding also does not enforce the Mind/Planner semantic boundary. Some model
texts still contained relatively concrete advice such as clearing a cache,
retrying a request, or reconfiguring an endpoint. E3's blind rubric assessed
factual support, not abstraction level. Any later promotion would need a
separate semantic-boundary acceptance task; E3 must not be used to authorize a
Planner, task DAG, tool plan, or Actor authority.

## Skill use

- **codebase-design**: Design It Twice produced three independent sketches;
  the selected seam reuses `run_activation()` and hides validation behind one
  E3-local protocol interface.
- **tdd**: H1–H10 were written RED before the implementation; campaign and
  artifact-integrity tests followed the same RED/GREEN loop.
- **code-review**: pre-campaign Standards and Spec reviews found weak review-ID
  opacity, an unpinned pending artifact, and an implicitly rather than
  explicitly bound timeout. All three were fixed and the focused re-reviews
  passed before the real campaign.
- **Ponytail**: the experiment uses one isolated module, one test module, one
  manifest, existing DTOs/runner/provider seam, and standard library support;
  no registry, manager, verifier framework, or new provider was introduced.

## Validation

```text
Mind suite:
168 passed

production Mind gate + chat API:
57 passed, 2 upstream warnings

repository suite:
338 passed, 24 skipped, 2 upstream warnings

py_compile:
PASS
```

The warnings are unchanged upstream MAGMA deprecation/future warnings.

## Promotion decision

Do not promote grounded Directive evidence into production Mind Trace or
Directive delivery from this result. E3 established that the deterministic
authorization mechanism works and exposed useful failure modes, but the
behavioral hypothesis was not tested under a baseline with enough contradicted
guidance to satisfy the frozen success threshold.

Any follow-up must be a separately preregistered experiment. It must not rerun
or rewrite this campaign, and it must preserve E3 as `INCONCLUSIVE` evidence.
