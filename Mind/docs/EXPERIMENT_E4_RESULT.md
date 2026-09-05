# Mind Experiment E4 — Trigger-Grounded Supervisor View

## Verdict

```text
EXPERIMENT_E4_INCONCLUSIVE
```

这不是 candidate 的语义失败。DeepSeek-V4-Pro 的 candidate arm 产生了 10 条
Directive，两名 fresh blind reviewers 对 10 条均一致判为 `SUPPORTED`，且没有
provider failure 或 malformed output。预注册公式要求
`baseline CONTRADICTED >= 2`；本次 baseline 没有产生任何 Directive，因此
`baseline CONTRADICTED = 0`，必须直接判为 `INCONCLUSIVE`。

按照任务卡锁定的停止规则，本结果不授权 E5，不授权 production auto-supervision，
并暂停继续扩展 Mind semantic architecture。不得事后修改 case、prompt、rubric 或
verdict threshold 来制造 `SUPPORTED`。

## Provider correction and evidence boundary

用户在首轮 E4 real campaign 进行中明确规定：今后所有新实验和生产真实模型调用
统一使用 DeepSeek。该轮 MiniMax campaign 随即停止并封存在
`Mind/fixtures/e4/campaign/`：其中 10 次调用形成 terminal trace，第 11 次 trace
在 `MODEL_OUTPUT_RECORDED` 前停止，没有第 12 次调用；它没有 pending artifact、
blind review、metrics 或 verdict，不能与本报告的结果合并。

本报告只采用全新目录 `Mind/fixtures/e4/campaign_deepseek/` 中一次完成的
DeepSeek campaign。生产 Chat、Dream、Mind gate 与 Execution 的新真实模型路径也已
统一到 DeepSeek-V4-Pro；deterministic mock seam 保留，历史上由 MiniMax 产生的
实验 provenance 保持原样，E1/E2/E3 的历史 real-provider runner 则在任何 provider
调用前明确阻断，避免把新 provider 结果冒充为旧 campaign。

本次模型配置来自 DeepSeek 官方当前文档：`deepseek-v4-pro`，官方
Anthropic-compatible base URL `https://api.deepseek.com/anthropic`，`/v1/messages`，
`x-api-key`，并显式关闭 thinking。参考：

- [DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- [DeepSeek Anthropic API compatibility](https://api-docs.deepseek.com/guides/anthropic_api/)

E4 冻结配置：

```text
provider: deepseek-anthropic
model: deepseek-v4-pro
base_url: https://api.deepseek.com/anthropic
max_tokens: 1000
temperature: provider_default
request_timeout_seconds: 30
```

## Reference-source audit

### Tycho

**SOURCE:** official [`NIMI-research/Tycho`](https://github.com/NIMI-research/Tycho)

**COMMIT / REVISION:** current default branch was shallow-cloned and inspected at
[`f68912a764372ead0a610db2e1c011d41ce5197e`](https://github.com/NIMI-research/Tycho/commit/f68912a764372ead0a610db2e1c011d41ce5197e).
The temporary source clone was removed after inspection; Tycho is not a Lumina dependency.

| File / symbol | Actual source behavior | E4 adaptation |
|---|---|---|
| [`tycho/agent/events.py::Event`, `AgentSpec`, `builder_trigger`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/agent/events.py) | Uses a small explicit event vocabulary and plain Python trigger predicates. `builder_trigger()` fires on `ACTION_SUBMITTED` plus a host-computed world-model inaccuracy signal. `AgentSpec.sees_full_history=False` means a fresh bounded context slice rather than the Actor conversation. | Keep the trigger host-owned, append one explicit evidence fact, and project a bounded slice rather than exposing the Execution transcript. |
| [`tycho/agent/dispatcher.py::TriggerDispatcher`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/agent/dispatcher.py) | `should_fire()` derives firing from verifier/falsification state, records the cause used by `trigger_reason()`, does not add the old arbitrary cooldown, and supports `snapshot()` / `restore()` of trigger counts. | E4 borrows only evidence-driven wake and explicit trigger cause. The 12 triggers are frozen host inputs; no Nervous policy or trigger discovery is implemented. |
| [`tycho/agent/modes.py::ModeSpec`, `MODES`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/agent/modes.py) | Separates Actor-pull builder access (`include_builder`) from harness-push firing (`harness_fires_builder`). | Confirms that auxiliary cognition may be invoked externally without giving it Actor authority. Lumina does not import the fixed Actor/Builder/Scribe topology. |
| [`tycho/agent/builder.py::WorldModelBuilder.build`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/agent/builder.py) | Starts each build with a fresh short conversation, auto-attaches current verification evidence, runs under a call limit, and returns/persists a compact report. | E4 uses one bounded model call over a sanitized evidence bundle. It does not copy Tycho's workspace tools, executable world model, verifier, planner, or write authority. |
| [`tycho/agent/agent.py::_invoke_builder`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/agent/agent.py) | Returns the Builder report to the Actor explicitly as advisory; the Actor still decides the action. | Mind output remains advisory semantic guidance outside the Execution Actor tree. |

Tycho's strong idea for E4 is therefore:

```text
objective falsification/evidence
→ host-owned trigger cause
→ fresh bounded auxiliary cognition
→ advisory result
```

E4 does not copy Tycho's ARC world model, Builder ontology, planner, workspace mutation, or
tool surface.

### AVO boundary

Experiment D's locked AVO audit remains authoritative:

- NVIDIA paper: [AVO: Agentic Variation Operators for Autonomous Evolutionary Search](https://arxiv.org/abs/2603.24517v1), especially §3.3;
- independent reproduction: [`gatordevin/avo` at `f6dad9e639d3c9e5d5ac9ccacbe076d82cfa39d2`](https://github.com/gatordevin/avo/commit/f6dad9e639d3c9e5d5ac9ccacbe076d82cfa39d2).

E4 retains only the broader-trajectory, sparse advisory redirect concept. It does not import
`steps_since_best`, population/evolutionary lineage, continuous scheduling, git mutation, or the
reproduction's mutable delivery state. E4 also does not retest Experiment D's one-shot delivery
mechanism.

## Hypothesis and single variable

Hypothesis:

> After a host-established strong objective Execution trigger, showing Mind the trigger cause and a
> small bounded recent-evidence slice will make its high-level guidance more factually reliable than
> showing only the current coarse `ExecutionObservation`.

The single variable was model-visible Execution evidence richness:

```text
Baseline:
  same ActivationInput
  + same initial ExecutionObservation

Candidate:
  same ActivationInput
  + same initial ExecutionObservation
  + exactly one supervisor_evidence block
```

Held fixed across arms:

```text
case and frozen Execution prefix
DeepSeek-V4-Pro provider/model
system prompt and output contract
recent_context
allow_information_acquisition = false
max_model_calls = 1
max_capability_calls = 0
temperature and output budget
Memory behavior (not used)
Directive / NoChange / DecisionIntent semantics
```

No E3 grounding contract, critique call, verifier, prompt strengthening, or Execution continuation
was introduced.

## Design choice and implementation surface

`/codebase-design` compared two designs:

1. Enlarge the existing `ExecutionObservation` DTO.
2. Add an experiment-local `SupervisorEvidenceBundle` alongside the unchanged observation.

Option 2 was selected. Enlarging the established A/E2 observation seam would turn an E4-only
variable into a wider contract change and make baseline fidelity harder to demonstrate.

Implemented symbols:

- `Mind/experiment_a.py`
  - `SupervisorTrigger`
  - `SupervisorEvidence`
  - `SupervisorEvidenceBundle`
  - `run_activation_with_supervisor_evidence(...)`
- `Mind/trace.py`
  - `SUPERVISOR_EVIDENCE_OBSERVED`
  - validation and deterministic `project_model_request(...)` support
- `Mind/trigger_grounded_supervision_experiment.py`
  - frozen manifest loading and preregistration
  - paired real-model campaign
  - blinded audit construction/merge
  - frozen verdict formula
- `Mind/test_trigger_grounded_supervision_experiment.py`
- `Mind/fixtures/e4/manifest.json`

No `Execution/` API, production routing, automatic trigger policy, Memory algorithm, or runtime
authority was added. Cross-directory changes made in the same worktree belong to the separately
authorized DeepSeek-only provider migration, not to the E4 experimental variable.

## Trace and request contract

The candidate bundle first becomes the host-owned append-only event
`SUPERVISOR_EVIDENCE_OBSERVED`. The existing deterministic projector derives the model-visible
request from trace facts. The experiment does not concatenate raw Execution data directly into a
prompt.

The exact request invariant is:

```text
candidate_request
= baseline_request
+ one top-level supervisor_evidence field
```

The overall candidate request retains the same `initial_execution_observation`; the added
`supervisor_evidence` field contains one trigger and bounded recent evidence. Baseline remains
byte-equivalent to the current E2 PUSH-style request apart from frozen experiment identity
metadata. Raw `EventLog`, `DecisionFrame`, IPython transcript, provider request, tool internals,
workspace paths, and full Actor history are absent.

## Trigger, evidence, and hard bounds

Host-owned trigger vocabulary:

```text
completion_rejected
action_failure
```

Evidence vocabulary:

```text
action_result
action_failure
completion_rejected
observation
```

Frozen host-enforced limits:

| Item | Bound |
|---|---:|
| trigger summary | 500 chars |
| trigger source refs | 3 |
| one source ref | 128 chars |
| recent evidence items | 3 |
| one evidence item | 1,000 chars |
| whole supervisor bundle | 3,500 canonical-JSON chars |

The model cannot select the trigger, supply evidence, enlarge a bound, or request another
capability in this campaign.

## Frozen campaign

The manifest contains 12 synthetic but realistic prefixes, six ending in
`completion_rejected` and six in observable `action_failure`. Coverage is exactly two cases per
required stratum:

| Coverage | Cases |
|---|---:|
| wrong completion assumption | 2 |
| missed existing artifact | 2 |
| correct output failed protocol | 2 |
| repeated observable failure | 2 |
| direction sound after one local failure | 2 |
| ambiguous failure | 2 |

Frozen identities:

```text
manifest_sha256:
47d8577e616b4e755653b3460c9b3b77839f650de88513b8b98280bea3ae87c8

preregistration_sha256:
16fb4f0a471a1affebc8a943cee9a4375837d757d4b0c5476199231977cc5beb

pending_artifact_sha256:
efd935d7ca286f88a0b8c8b5b48a7689132b1827ef849e54f7d48b65e0f1d159

blinded_audit_sha256:
5be772f5eb77a05f64cfec1ff72dcbd0a81d0ee9ed1cdeb460ac1244e01c1329

final_artifact_sha256:
28a77194db7b734dc669af90e00a41801129ecfb63066dfe203ecfb47067ab65
```

All 24 planned model calls produced `MODEL_OUTPUT_RECORDED`; provider failures were zero. The three
`ActivationFailure` results below were two invalid baseline envelopes and one baseline request for
a capability disabled by the frozen campaign, not transport/provider failures.

## Results

| Metric | Baseline | Candidate |
|---|---:|---:|
| Directive | 0 | 10 |
| SUPPORTED Directive | 0 | 10 |
| UNCERTAIN Directive | 0 | 0 |
| CONTRADICTED Directive | 0 | 0 |
| NoChange | 5 | 1 |
| DecisionIntent | 4 | 1 |
| ActivationFailure | 3 | 0 |

Two fresh reviewers received only `directive-audit-input.json`. Each item exposed an opaque review
ID, full frozen supervisor evidence, Directive text, and the frozen rubric. It omitted arm, case ID,
aggregate result, output metadata, source code, and execution outcome. Both reviewers returned
identical complete mappings: all 10 items `SUPPORTED`. No disagreement fallback was needed.

```text
paired_semantic_improvements = 0
paired_semantic_regressions  = 0
```

The paired-improvement definition requires a reviewed baseline Directive classified
`CONTRADICTED/UNCERTAIN` and an improved candidate result. Because baseline emitted zero
Directives, none of its 12 outputs entered the semantic Directive audit. Candidate's 10 supported
Directives therefore cannot count as preregistered paired improvements.

Frozen formula application:

```text
baseline CONTRADICTED = 0
0 < required minimum 2
→ EXPERIMENT_E4_INCONCLUSIVE
```

## TDD and validation

The maintained tests follow the requested seam order:

1. exact E2 PUSH baseline request;
2. candidate's one-field request delta;
3. bounds and raw-object rejection;
4. trace reopen/reconstruction and conservative corruption behavior;
5. host ownership of trigger/evidence;
6. A–E3 regression and no-Execution-authority check;
7. frozen 12-case/24-call campaign, blind merge, and verdict formula.

The implementation was developed as small red/green vertical slices at the public activation and
trace projection seams. No generic ContextBuilder, Registry, Manager, trigger framework, or
production supervisor API was introduced.

Validation after the DeepSeek migration and campaign:

```text
E4 focused tests:       29 passed
Mind suite:            200 passed
production focused:    128 passed, 1 skipped
repository suite:      338 passed, 24 skipped
py_compile:            passed
git diff --check:      passed (line-ending/access warnings only)
provider calls:        24 outputs, 0 provider failures
```

Two independent pre-campaign code-review passes both returned `PASS` after identified provider and
historical-provenance issues were corrected.

## Code-review gate

1. **Only evidence richness changed?** Yes. The paired requests share case, observation, model,
   prompt, bounds, and call policy.
2. **Baseline preserves E2 PUSH?** Yes; direct regression asserts the exact current request.
3. **Candidate adds only one block?** Yes; structural differential test removes
   `supervisor_evidence` and requires equality.
4. **Trigger host-owned?** Yes; it is frozen input recorded before the model call.
5. **Recent evidence bounded?** Yes; closed vocabulary plus count/item/aggregate hard limits.
6. **Raw EventLog/DecisionFrame leaked?** No.
7. **Trace remains sole Context authority?** Yes; projector consumes the recorded evidence fact.
8. **Mind prompt changed?** No; both arms have the same prompt hash.
9. **E3 grounding contract introduced?** No.
10. **Automatic trigger policy introduced?** No; cases mechanically provide frozen triggers.
11. **Execution authority introduced?** No; zero capabilities, no Execution imports/tool surface in
    the runtime Mind seam.
12. **Holdout genuinely frozen?** Yes; manifest, case hashes, arm order, source hashes, model config,
    rubric, and criteria were persisted before calls.
13. **Review blind to arm?** Yes; reviewers saw only the generated anonymous audit file.
14. **Post-hoc case/evidence editing?** No; finalization revalidated pending, audit,
    preregistration, manifest, and source hashes.

## Limitations and promotion decision

- This was a semantic-output campaign, not a continued paired Execution behavior test.
- Synthetic cases demonstrate only the frozen strong-trigger distribution.
- All blind-reviewed candidate Directives were supported, but the planned causal comparison was
  underidentified because baseline produced no auditable Directives.
- The experiment does not establish trigger discovery quality, behavior improvement, production
  scheduling, multi-activation value, or real-user generalization.
- The high candidate Directive rate is not itself a promotion criterion and must not be treated as
  proof that more intervention is better.

Promotion decision:

```text
NO E5 AUTHORIZATION
NO PRODUCTION AUTO-SUPERVISION
PAUSE FURTHER MIND SEMANTIC ARCHITECTURE EXPANSION
```

The experiment-local mechanism and its evidence remain useful source-level artifacts, but the
pre-registered hypothesis was not decisively tested.
