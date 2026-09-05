# World Model W7 Preregistration

## Status

```text
FROZEN_AWAITING_EXPLICIT_DEEPSEEK_EXPORT_APPROVAL
```

W7 的本地实现、fixture、prompt、tool surface、bounds 与判定标准已经冻结。尚未向
DeepSeek 发送 W7 数据，尚未创建 `fixtures/w7/real_campaign_result.json`，真实
campaign 仍可且只可运行一次。

## Hypothesis and single variable

W7 只检验：当 Environment Observation 不足以构成 Markov state 时，Builder
能否从 action/observation trajectory 构造有预测价值、可持续、可再次改变的
model-owned latent state。

唯一变量是：

```text
environment-provided complete state
→ model-owned state threaded across an observation/action trajectory
```

冻结不变的 W5/W6 baseline：

- DeepSeek Anthropic-compatible native `tool_use` / `tool_result` transcript；
- provider `deepseek-anthropic`，model `deepseek-v4-pro`，thinking disabled，
  temperature `0`，无 retry/fallback；
- 8 model turns、8 tool calls、每个响应最多 3 个并行 read、16,000 可见字符；
- `read_file`、`run_python`、`write_file`、`unresolved` 四个 tool schema；
- fresh episode、bounded multi-read、automatic verifier feedback、accuracy `1.0`
  才允许 atomic apply 的 acceptance philosophy。

W7 没有新增 provider、tool、authority、retry、response continuation 或通用框架。

## Source audit

### W6 interaction pieces reused unchanged

下列 W5/W6 production-independent experiment pieces可原样复用：

- `world_model_read_batch_experiment.NATIVE_TOOL_SPECS`；
- `world_model_read_batch_experiment.NativeActionCall` /
  `NativeActionEnvelope`；
- tool schema/host validation、multi-read envelope validation、tool-result pairing；
- `RevisionBounds` 与 frozen DeepSeek model configuration；
- fresh episode lifecycle、bounded context、event append、atomic workspace write；
- provider-native transcript capture、pairing/freshness/authority campaign metrics。

`BoundedRepresentationBuilder.next_actions()` 将 W6 prompt 常量直接写入 request，
因此 W7 不能直接调用该实现同时替换 prompt。W7 的
`BoundedLatentStateBuilder.next_actions()` 保留同一 transcript 逻辑，只把 system
prompt 替换为冻结的 W7 prompt。tool specs 仍直接引用 W5 tuple，没有复制或扩展。

### W6 validator pieces reused

W7 沿用 W6 的以下安全机制与边界：

- closed AST allowlist、node/depth/source-size bounds；
- scalar shape analysis与静态整数幅度上限；
- 仅允许局部 assignment、`if`、纯 `min/max/abs`；
- 禁止 import、I/O、attribute/reflection、dunder、arbitrary calls、loop、helper class；
- `python -I -S` 隔离执行；
- input mutation 检查、输出 contract 检查、subprocess timeout。

W6 的 `predict(state, action)` validator 和 independent-row evaluator 不能原样复用，
因为它们把 Environment 提供的完整 state 当作唯一状态。W7 必须增加三方法 contract
和 trajectory-threaded evaluator；这不是 grammar authority 扩张。

### New minimal three-method source contract

精确 source surface：

```python
class CanonicalWorldModel:
    version = "wm-latent-v1"

    def init_state(self, observation):
        ...

    def transition(self, state, action):
        ...

    def observe(self, state):
        ...
```

约束：

- class 只含 `version` 与这三个同步方法；
- `init_state` 只能读取初始 `observation["value"]`；
- `transition` 只能读取 prior model state 与 action，并返回 fresh dict；
- `observe` 只能读取 model state，并精确返回 `{"value": int}`；
- model state 是最多 8 个字段的 flat dict；value 仅可为 bounded `int`、`str`、
  `bool`；
- 字段名与语义由 candidate source 表达，harness 不预设 `mode`、`phase` 或
  `counter`。

对应 symbols：

- `validate_model_source`：静态 closed contract；
- `evaluate_model_source`：隔离的 model-state replay；
- `verify_trajectories`：Reality comparison 与 first divergence；
- `analyze_latent_state`：字段名无关的 causal/persistence/toggle 判定；
- `run_latent_revision_episode`：bounded inspect/revise/verify/apply episode。

### New trajectory verifier

冻结 replay 顺序：

```text
initial_observation
→ init_state once
→ observe(initial model state)
→ transition(prior model state, action)
→ observe(new model state)
→ only now compare with actual observation
→ repeat from the model's own new state
```

传入隔离 evaluator 的 JSON 只包含：

```text
initial_observation + actions
```

later actual observations 不进入 child process；它们只在 parent verifier 收到完整
prediction replay 后用于比较。campaign 还会把所有 later actual observations 改写后
再次执行 evaluator，并要求 replay byte-semantically identical，以锁定 absence of
teacher forcing。

first divergence 仅暴露：

```text
trajectory index
step index
action
model_state
predicted observation
actual observation
```

其中 `model_state` 是 candidate 自己产生的 hypothesis state，不是 fixture truth。

### Exact W6 → W7 diff

| Surface | W6 | W7 |
|---|---|---|
| Environment input | complete `{value, mode}` state per row | initial `{value}` observation, then actions |
| Model contract | `predict(state, action)` | `init_state` / `transition` / `observe` |
| State owner | Environment | executable World Model |
| Evaluation | independent rows | state threaded within trajectory |
| Reality after initialization | provided as next complete row | comparison only; never state reset |
| Source safety | bounded pure Python | same philosophy, bounded flat scalar state |
| Prompt change | W6 pure-computation doctrine | only four model-owned-state principles plus new exact contract |
| Provider/tools/bounds | frozen W5/W6 | byte/config identical |
| Acceptance | exact public verifier agreement | exact public trajectory agreement |

没有新增 latent registry、state estimator、POMDP、belief distribution、planner 或 runtime
actor authority。

## Tycho source-first audit

Primary source：NIMI Research Tycho，固定 commit
`f68912a764372ead0a610db2e1c011d41ce5197e`。

- [`seed_world_model.py.tmpl`](https://raw.githubusercontent.com/NIMI-research/Tycho/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/templates/seed_world_model.py.tmpl)
  的 `State`、`init_state(grid0, level)`、`transition(state, action)`、`render(state)`
  明确区分 agent-owned state 与 observation projection，并要求不能从 planner key
  遗漏 hidden state。
- [`workspace.py`](https://raw.githubusercontent.com/NIMI-research/Tycho/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/workspace.py)
  将 harness observation 与 agent-authored `world_model.py` 分离；harness 不自动提供
  object/latent decomposition。

Lumina adaptation 只取：model-owned State、trajectory transition、observation projection、
reality replay。没有复制 grid、NumPy rendering、level、outcome/actions、subgoals、
heuristic、planning、planner key、PNG evidence 或 Tycho workspace runtime。

## Frozen baseline and candidate

所有 case 的 seed 都是同一 observable-only source：

```text
state = {value}
toggle leaves value unchanged
step(delta) adds delta
observe returns {value}
```

它在 ambiguity public trajectory 上 exact，但在三个 resolvable trajectory 上按预注册
失败。focused tests 已证明 observable-only baseline 不能解释 invisible toggle 之后同类
action 的不同长期效果。

Candidate 是同一个 W5/W6 Builder interaction，在 W7 三方法 source contract 下修改
model-owned representation。成功必须是 representation revision：final state 中至少一个
Environment Observation 不含的字段，对 future prediction 有因果作用，跨至少两个连续
step 持续，并被 invisible toggle 改变。字段名不参与判定。

## Frozen fixtures

Builder-visible public documents只含 `initial_observation` 与各步 `action`、
`observation`。以下名称、机制解释和 hidden trajectories 仅存在于 manifest/result 与
本 preregistration，不进入 Builder context。

### `latent-boost`

Public：

```text
5 → step(+2)=7 → toggle=7 → step(-1)=5 → step(+2)=9
  → step(-2)=5 → toggle=5 → step(+2)=7
```

Hidden：

```text
13 → toggle=13 → step(+5)=23 → step(-2)=19 → step(+3)=25
   → toggle=25 → step(-4)=21
```

### `latent-reverse`

Public：

```text
5 → step(+2)=7 → toggle=7 → step(+2)=5 → step(+2)=3
  → toggle=3 → step(+2)=5
```

Hidden：

```text
10 → toggle=10 → step(+5)=5 → step(-2)=7 → step(+3)=4
   → toggle=4 → step(-4)=0
```

### `latent-clamped`

Public：

```text
0 → step(-3)=-3 → step(+3)=0 → toggle=0 → step(-3)=0
  → step(+2)=2 → step(-5)=0 → toggle=0 → step(-3)=-3
```

Hidden：

```text
4 → toggle=4 → step(-10)=0 → step(-3)=0 → step(+5)=5
  → toggle=5 → step(-9)=-4
```

### `insufficient-latent-evidence`

Public only：

```text
4 → step(+1)=5 → step(+2)=7
```

没有 toggle、矛盾或 hidden holdout。要求 native `unresolved`、current unchanged，且不能
凭空新增 latent field。

## Hidden isolation and anti-memorization

- `LatentRevisionRequest` 没有 `record_ref`、fixture name、generator rule 或 hidden
  trajectory 字段；
- Builder context只给三个 handles、remaining budget、verifier summary与最近 public
  observations；
- `evidence.json` 只投影 public trajectory；
- hidden replay 在 episode 完成、provider transcript 固定后才执行；
- `_hidden_trajectories_isolated` 逐一比较 hidden-only compound surfaces 与所有 provider
  request surfaces；任何交集直接 `W7_FAIL`；
- first divergence 的 state 来自 candidate replay，不含 Reality latent truth；
- fixtures 改变 initial value、delta、action sequence、连续 latent steps 和 toggle 位置，
  hidden exact replay 是主要 anti-memorization 防线；
- source contract 不允许 trajectory index、clock、filesystem、global mutable state、I/O
  或 case table helper。

## Safety and authority

Runtime Builder 只有冻结的四个 bounded experiment tools。`read_file` 只能读
`world_model.py`、`notes/world_model.md`、`evidence.json`；`write_file` 只能写 working
model/notes，经 W7 validator 与 trajectory verifier 后才可能 atomic apply 到 isolated
current model。`run_python` 是无 import、无 attribute、无 I/O 的 bounded analysis
subprocess。

Candidate executable source在 `python -I -S` child 中运行，只获得
`__build_class__`、`min`、`max`、`abs`。它没有 shell、filesystem、network、process、
Execution、Memory、IPython 或 provider authority。state ownership 是数据表示权，不是
行动权。

## Frozen verdict

`W7_PASS` 要求同时满足：

- Reality/public evidence unchanged；
- hidden truth never leaked，hidden holdout isolated；
- authority/source safety、episode freshness、context bound、tool pairing 全部通过；
- no retry/fallback，anti-teacher-forcing self-check通过；
- 3/3 resolvable 均检测到 causal、persistent、toggle-updated 的额外 state component；
- 3/3 public trajectory accuracy `1.0`；
- 3/3 hidden trajectory accuracy `1.0`；
- ambiguity 为 native `UNRESOLVED`，current unchanged，无不必要 latent rewrite。

安全成立但 reconstruction、public/hidden exact、budget progression、provider availability
或 ambiguity restraint 任一不足，判为 `W7_INCONCLUSIVE`。evidence mutation、hidden
leak、teacher forcing、authority escape、current corruption、cross-episode leak、context
bypass、dangerous source acceptance 或 unsupported ambiguity rewrite 任一出现，判为
`W7_FAIL`。

Hidden replay 自身若对 validator-accepted source 发生 bounded evaluator failure，会记录
`hidden_accuracy=0.0` 与 `hidden_replay_error=evaluation_failed`，保留 coherent current，
并安全地产出 `W7_INCONCLUSIVE`；不会让一次性 campaign 在写 result 前异常终止。

## Mandatory code review

SPEC 与 SAFETY 双轴 review 各发现并关闭一个 blocker：

- SPEC：child runner 原先为记录 `initial_state` 二次调用 `init_state`。现已在唯一一次
  initialization 后立即快照，并由 call-count regression 锁定；
- SAFETY：public-exact candidate 原先可能只在 hidden replay 失败并中断 campaign。
  现已 fail-soft 记录 hidden evaluation failure，并由 adversarial hidden-initial-state
  regression 锁定为 `W7_INCONCLUSIVE`。

复审范围内未发现剩余 latent-state spec、AST/runtime authority 或 hidden-isolation
问题。

## Frozen artifacts

```text
W6 manifest SHA256        519f8ffd98df53aa17cf02079ec4d990c273c888c20fbcff0a8004748f211ef4
W6 implementation SHA256 46ac449dd797e14997bd9dbf338e861751e8a43cf822b7cd2b0c3d55266292e2
W6 prompt SHA256         69f9abbff92b9ac39883a6d1f0d88182755eb61d8092ddd7772ef5889e425652
W6 tools SHA256          31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086
W6 result SHA256         2676b5277b6bc9593631d3a72a42b26b77288280f0d5aaa9a6ca82746576ba6a

W7 implementation SHA256 a1fe488e135a8fff716187ec8c90125b4f21c7b781b3dd30e1f1db25a3ca5541
W7 prompt SHA256         361510590c8c51c6253ee8241b94fceff92de95ae89c5bff34e36ce39e45e25a
W7 tools SHA256          31bc41350f77ab256aac89e8ae5f2d315adebb7244afa734bcda0ea2c45cb086
W7 fixture SHA256        90cf429bab21488a7fab3326653e92e9621faabd4aeb0eba9f7cf1373bae980c
W7 manifest SHA256       70fa26e5129ff1f062422a4c2fedc740ce2f7e5952162ca54560dfec4dfe95d7
```

Focused deterministic/mock validation at freeze time：

```text
python -m pytest Mind/test_world_model_latent_state_experiment.py -q
32 passed
```

The scripted candidate campaign reaches `W7_PASS`; this validates harness mechanics and
acceptance logic only. It is not the real DeepSeek result and creates no result artifact.

## Real campaign gate

下一步必须先取得对以下 W7 public export 的明确授权：frozen system prompt、四个 tool
schemas、observable-only current source、三个 public resolvable trajectories、一个 public
ambiguity trajectory，以及 campaign 中产生的 bounded tool results/transcript。

不会发送：hidden trajectories、fixture names/record refs、generator rules、true hidden
state、本地路径、凭据或 repository data。

获得明确授权后才可执行 canonical command，且只执行一次；随后另建
`docs/WORLD_MODEL_W7_RESULT.md` 回答任务卡 A–J。没有授权时本任务停在 preregistration，
不得试跑 provider。
