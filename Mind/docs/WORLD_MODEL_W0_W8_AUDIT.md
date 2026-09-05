# Lumina World Model W0–W8 总体证据与架构审计

**审计日期：** 2026-09-01

**审计范围：** `Mind/` 中 W0–W8 的任务卡、预注册、实验实现、测试、原始 campaign artifact，以及与下一阶段直接相关的 `Execution/` 公共边界

**任务性质：** AUDIT / CONSOLIDATION；本文件不授权 W9、生产集成或任何新运行时能力

## 1. Executive verdict

W0–W8 已经建立了两层窄而真实的实验性证据：

1. **Phase A：有界、确定性合成 domain 上的 prediction → observation → error/revision 机制。** W0 证明先预测后观察、可重放比较和追加式证据；W1–W6 逐步证明隔离 candidate、fresh bounded Builder、provider-native tool use、多资源读取，以及受限纯 Python 表示可以在三个简单共享动力学 fixture 上形成并应用候选模型。
2. **Phase B：有界、确定性合成 trajectory 上的 model-owned latent state。** W7/W8 证明模型能够在 `double`、`reverse`、`clamp` 三类窄轨迹中自行提出 latent state，并用 state transition 解释不可直接观测的动作影响；W8 三个可解 fixture 的 public 与 hidden trajectory 均为 `1.0`。

这些证据支持的是一个**隔离、只读、影子式世界模型实验机制**，不是 production World Model。它们没有验证统一长期世界模型、真实 Execution 抽象、异构历史维护、自动 surprise trigger、主动探测或任何 steering authority。

总体结论：

```text
EXPERIMENTAL_MECHANISMS: SUPPORTED WITH NARROW SCOPE
PRODUCTION_WORLD_MODEL: NOT VALIDATED
NEXT_STAGE: GO WITH CONDITIONS — WORLD MODEL SHADOW INTEGRATION S0
SYNTHETIC_HARNESS_EXPANSION: STOP
```

`W7_INCONCLUSIVE` 与 `W8_INCONCLUSIVE` 必须原样保留。W8 的 ambiguity case 不是明显的 cognition failure：模型正确指出当前机制已解释已见证据、额外 toggle 假设尚未被证伪，并建议能区分假设的新观察；失败发生在 harness 的 terminal vocabulary——它只接受 `unresolved`，而模型选择了普通 `end_turn`。这使 **terminal semantic protocol 仅部分成立**，不能把 W8 改写成 PASS。

## 2. 审计方法与证据边界

本审计没有只复述 result 文档。结论交叉核验了：

- W0–W8 的预注册与 result；
- `world_model_*.py` 的实际状态机、验证器、tool schema、限制与 apply 条件；
- `test_world_model_*.py` 的本地 deterministic/mock seam；
- `fixtures/w1`–`fixtures/w8` 的原始 `manifest.json`、`real_campaign_result.json` 和 W8 one-shot marker；
- W7 clamp 与 W8 ambiguity 的逐 turn provider/tool evidence；
- `Execution/__init__.py`、`Execution/organ.py`、`Execution/execution.py` 的真实 public facade 与内部 event model；
- Tycho 固定提交 `f68912a764372ead0a610db2e1c011d41ce5197e` 的官方源码，而不是二手架构描述。

本文件采用四档证据分类：

- **VALIDATED / SUPPORTED**：在声明限定的实验范围内有直接 artifact、代码和测试证据；不自动外推到 production。
- **PARTIALLY SUPPORTED**：机制或部分案例成立，但可靠性、语义闭合或覆盖不足。
- **NOT VALIDATED**：W0–W8 未测试该主张；不表示已被证伪。
- **CONTRADICTED / SHOULD NOT CLAIM**：现有原始证据直接与该主张冲突，或该表述会越过证据边界。

所有 campaign 结论仍受以下共同限制：fixture 是人工构造的、动力学简单、trajectory 很短、环境确定；安全验证是冻结 threat model 下的结构约束，不是 hostile sandbox 或 production security 证明。

### 2.1 Artifact identity

下表为审计时重新计算的 SHA-256。W2–W8 的实验源文件 hash 与各 manifest 记录一致；W1 的 source hash 与 result 内记录一致。未修改任何 artifact。

| Experiment | Manifest / marker SHA-256 | Result SHA-256 | Source identity |
|---|---|---|---|
| W1 | `fa4fef80cd0eb80b3a7b4da31b409b483e95f7c8a2640d21b6a67926b9b987a8` | `c0de6e0ac0363019dcb4ac2b1454d746b1697bf5803c22b909cedf4ae280d76e` | `04047f…`, matches result |
| W2 | `e10024852e234d920c4d6a2e7979c6c9d26da5d606d92fb8bb3d7496030fea7f` | `8a7b4f25bf7ca66e70a3677893c3049e7c86a16320fb066df8f56abc58418370` | `22c060…`, matches manifest |
| W3 | `b5824f9609f58a30d72ce7022714804af5928167e069dfc7396bc8ffd5a9251b` | `7c2d20f4429058f56862d61f95d9141bf98bc6a29aea328bd29dcd63a5e349f6` | `809d…`, matches manifest |
| W4 | `debb82ad9ebb3eb6f522cda47376d4a61889aafc15e6f14c699a473a08e495eb` | `e4d134ae7a4f2c1f2f5c442cbc1c43841422f7f927d5f351d429ee22d82cf8de` | `044f…`, matches manifest |
| W5 | `c517590ca6fd90f39076f3c7f368de86b7543c5ebeb53114e041eda5697845ea` | `20fed04470f8a3f45153b7440f21e433802cdc3e2cec092a784727bab6c1b0fb` | `a331…`, matches manifest |
| W6 | `519f8ffd98df53aa17cf02079ec4d990c273c888c20fbcff0a8004748f211ef4` | `2676b5277b6bc9593631d3a72a42b26b77288280f0d5aaa9a6ca82746576ba6a` | `46ac…`, matches manifest |
| W7 | `70fa26e5129ff1f062422a4c2fedc740ce2f7e5952162ca54560dfec4dfe95d7` | `d940c204c51b2201934e7f82599f15f6c4c0ffc43abd8e06bff0c3b1828cbe0d` | `a1fe…`, matches manifest |
| W8 | marker `40929d8780712735284113dcf0679b0f862a19f29b8b8a5e931d4b9faf308d73`; manifest `e5cd2538312854522962eec7f5bbe96ccf9141f2ad377ac605334900a4f2802f` | `82a1a15b86aa36187e479cbc5dfd8c2a7a817da6bba5fad128a1c40d491d3850` | `5925…`, matches manifest |

## 3. W0–W8 evidence lineage

| Experiment | Single changed mechanism | Direct result | Honest interpretation |
|---|---|---|---|
| W0 | prediction/observation/error 的 append-only deterministic baseline | `WORLD_MODEL_W0_PASS` | 支持先预测、后观察、再比较；只覆盖本地合成 source，未覆盖 Execution |
| W1 | one-shot fresh Builder 生成 candidate executable model | `WORLD_MODEL_W1_INCONCLUSIVE` | 3/3 candidate 合法、安全且 current 未被破坏；候选 revision 质量不可靠 |
| W2 | bounded revision loop | `W2_INCONCLUSIVE` | 3 个可解 case 均耗尽 8/8 且 0 revision；只证明循环可运行，不证明有效修复 |
| W3 | episode 内有状态 continuation | `W3_INCONCLUSIVE` | 状态连续性机械成立；只在 reverse 有行为收益，clamp grammar 失败，ambiguity terminal 失败 |
| W4 | DeepSeek provider-native Builder tool protocol | `W4_INCONCLUSIVE` | native `tool_use` 真实成立；host 每 turn 仅容许一个 action，模型首轮批量 3 reads 全被拒绝 |
| W5 | bounded read-only multi-tool batch | `W5_PASS` | 读取批次、结果配对、continuation 成立；3 个可解 case 都写 source，但仅 reverse 通过当时 grammar |
| W6 | bounded pure-Python representation | `W6_PASS` | boost/reverse/clamp 都成功 apply，public/hidden 均 1.0；ambiguity 仍正确停在 unresolved |
| W7 | model-owned latent-state reconstruction | `W7_INCONCLUSIVE` | boost/reverse latent 成立；clamp 被 1600-token truncation 截断；ambiguity unresolved |
| W8 | response-completion headroom 1600 → 2400 | `W8_INCONCLUSIVE` | 三个可解 case 均形成 latent 且 public/hidden 1.0；ambiguity cognition 合理但 terminal action 不合法 |

W0–W8 不是九次对同一主张的重复验证。每一步改变了不同机制，因此后期成功不能倒写早期 verdict，也不能把 W8 三个简单 fixture 的成功外推为通用世界模型能力。

## 4. Claim ledger

### 4.1 Validated / Supported within the frozen experimental scope

| # | Claim | Verdict | Evidence and scope |
|---:|---|---|---|
| 1 | durable prediction before observation | **VALIDATED / SUPPORTED** | W0 `WorldModelTrace.record_prediction()` 要求先记录 prediction，随后才允许 observation；事件逐条 flush + `fsync`。只验证本地单进程合成 trace |
| 2 | Reality Evidence is the comparison authority | **VALIDATED / SUPPORTED** | W0–W8 的 evaluator 都以 harness 后续实际 observation/trajectory 为准，candidate 的自述不决定正确性。这里的 Reality Evidence 是 fixture authority，不是 production DTO |
| 3 | anti-hindsight ordering | **VALIDATED / SUPPORTED** | W0 的事件顺序约束直接拒绝缺少 prediction 的 comparison；W7/W8 verifier 不把 hidden trajectory 给 Builder |
| 4 | deterministic replay can falsify a prediction/model | **VALIDATED / SUPPORTED** | W0 replay 严格重建 `MATCHED / ERROR / UNVERIFIABLE`；W6–W8 对 candidate 进行独立 public/hidden replay |
| 5 | invalid candidate cannot corrupt current model | **VALIDATED / SUPPORTED** | W1–W8 candidate 在隔离 workspace 中生成；只有验证达到 apply 条件才替换 current；失败案例 `current_changed=false` |
| 6 | accepted model application is atomic/coherent within the harness | **VALIDATED / SUPPORTED** | revision harness 先完整验证 source，再通过 `_atomic_write()` apply；没有观察到 partial current source |
| 7 | Builder is fresh and bounded per episode/call | **VALIDATED / SUPPORTED** | W1 one-call Builder；W3+ `start_episode()`/`finish_episode()` 保留 episode 内 history、结束后清空；turn、read、write 与 token budget 显式有界 |
| 8 | provider-native structured tool calls are usable | **VALIDATED / SUPPORTED** | W4 原始 DeepSeek 响应含 native `tool_use`；W5 进一步证明批量 tool action 与 continuation 可闭环 |
| 9 | bounded multi-resource read batches work | **VALIDATED / SUPPORTED** | W5 共 14 reads/5 envelopes，result 按 tool id 配对；read/path/turn/batch limits 由 host 拒绝规则执行 |
| 10 | simple shared dynamics can be inferred into executable source | **VALIDATED / SUPPORTED** | W6 在 boost/reverse/clamp 3/3 形成合格 source 并通过公开 trajectory；只覆盖这三个简单 deterministic dynamics |
| 11 | narrow hidden-holdout generalization occurs | **VALIDATED / SUPPORTED** | W6 三 case hidden 均 1.0；W8 latent contract 下三 case hidden 也均 1.0。它不是跨 domain 或统计泛化证明 |
| 12 | Observation and World Model State are representationally distinct | **VALIDATED / SUPPORTED** | W7/W8 contract 分开 `init_state(observation)`、`transition(state, action)`、`observe(state)`；harness 分析 state 字段对后续行为的贡献 |
| 13 | model can invent useful latent fields | **VALIDATED / SUPPORTED** | W7 的 double/forward 与 W8 的 double/negate/clamp latent 均由 model 生成，并非 harness 预填字段名 |
| 14 | latent state can persist across steps | **VALIDATED / SUPPORTED** | W7/W8 multi-step replay 由 prior state 经 transition 推进；验证器执行 persistent-field probes |
| 15 | an unobserved action can update latent state | **VALIDATED / SUPPORTED** | W7/W8 的 transition 在没有把下一实际 observation teacher-force 回 state 的情况下推进，再由 `observe()` 产生预测 |
| 16 | latent representation can generalize to a narrow unseen trajectory | **VALIDATED / SUPPORTED** | W8 boost/reverse/clamp 的 hidden trajectory 全部 1.0；W7 为 2/3，clamp 未完成。主张只限同一 fixture family 的短轨迹 |

### 4.2 Partially supported

| # | Claim | Verdict | Evidence gap |
|---:|---|---|---|
| 17 | within-episode continuity materially improves revision | **PARTIALLY SUPPORTED** | W3 证明 message/tool history 连续，且 reverse 成功；但 boost 未显示同等收益、clamp grammar 失败，不能声称普遍改善 |
| 18 | unnecessary latent ontology is reliably avoided | **PARTIALLY SUPPORTED** | W7/W8 ambiguity 都没有添加 latent/current change；只有一个反复使用的 ambiguity fixture，且 W8 terminal 不闭合 |
| 19 | iterative revise → fail → revise can recover | **PARTIALLY SUPPORTED** | W7/W8 reverse 出现先写无效 source、接收验证反馈、再写有效 source；其他 case 未形成足够重复证据，可靠性未知 |
| 20 | terminal semantic protocol represents all justified outcomes | **PARTIALLY SUPPORTED** | `unresolved` 能表示“当前证据与候选修复相冲突且不可判定”；W8 暴露“current 与证据一致但未来行为仍欠定”的缺词问题 |
| 21 | safety under the frozen experimental threat model | **VALIDATED / SUPPORTED** | AST allowlist、受限 builtins、`-I -S` 子进程、temporary workspace、hidden-data isolation 与 tool rejection 在预注册威胁模型内均有直接证据；该 verdict 不外推为 hostile sandbox 或 production security |

### 4.3 Not validated

| # | Claim | Verdict | Reason |
|---:|---|---|---|
| 22 | one unified long-lived World Model | **NOT VALIDATED** | 每个实验都是小型独立 episode/candidate，跨 episode state 被清空 |
| 23 | heterogeneous evidence accumulation across domains | **NOT VALIDATED** | fixtures 同构、短小、确定；没有跨域 schema 或冲突 evidence |
| 24 | real Execution behavior prediction | **NOT VALIDATED** | 输入来自 synthetic fixture，不来自 supported `ExecutionOrgan` 的 Reality Evidence seam |
| 25 | automatic surprise-triggered revision | **NOT VALIDATED** | campaign 由 harness 显式调用，不存在 production trigger/Nervous |
| 26 | long-history compression/maintenance | **NOT VALIDATED** | 没有长 trace、budget pressure 或跨 activation projection 实验 |
| 27 | deletion, merge, simplification, or restructuring of a durable model | **NOT VALIDATED** | prompt 允许修改 source 不等于验证了这些维护操作；没有对应 artifact |
| 28 | regime-change handling | **NOT VALIDATED** | 所有 mechanics 在 episode 内固定，没有 concept drift/changepoint |
| 29 | competing or probabilistic beliefs | **NOT VALIDATED** | current/candidate 是单一 deterministic Python model，没有 belief weights |
| 30 | autonomous epistemic probe selection | **NOT VALIDATED** | ambiguity 模型能建议区分性 observation，但无权选择或执行探测；建议不等于 active probing |
| 31 | real-time closed-loop prediction during Execution | **NOT VALIDATED** | 当前 Execution facade 没有逐步 public observer seam；离线读取最终 result 不能证明 pre-outcome prediction |
| 32 | production crash/security/failover behavior | **NOT VALIDATED** | 实验 failure isolation 不覆盖 production lifecycle、provider retry 或 hostile environment |
| 33 | automatic Mind integration or behavioral steering value | **NOT VALIDATED** | World Model 未接入 Mind activation、Directive 或 Execution decision context |

### 4.4 Contradicted / should not claim

| # | Claim | Verdict | Contradicting evidence |
|---:|---|---|---|
| 34 | W7 passed | **CONTRADICTED / SHOULD NOT CLAIM** | clamp 因 response truncation 未完成，正式 verdict 是 `W7_INCONCLUSIVE` |
| 35 | W8 passed | **CONTRADICTED / SHOULD NOT CLAIM** | ambiguity 以非法 `end_turn` 结束，正式 verdict 是 `W8_INCONCLUSIVE` |
| 36 | W8 proves clamp needed more than 1600 output tokens | **CONTRADICTED / SHOULD NOT CLAIM** | W8 clamp 的成功响应实际 `output_tokens=941`；只能说 2400 配置消除了该次截断，不能推导最小所需预算 >1600 |
| 37 | ambiguity was a failed cognition/reasoning case | **CONTRADICTED / SHOULD NOT CLAIM** | 原始 notes 对证据、未验证 toggle、无须修复及区分性观察的判断是 coherent；失败是 terminal action contract mismatch |
| 38 | latent state is ontological hidden truth | **CONTRADICTED / SHOULD NOT CLAIM** | latent 仅由预测效用探针支持；字段名与解释来自 candidate，可存在多个观测等价表示 |
| 39 | Builder is an Actor, Planner, persona, or permanent organ | **CONTRADICTED / SHOULD NOT CLAIM** | 实现是 fresh bounded auxiliary cognition，仅能操作隔离模型文件/notes；无 Execution、Reality、Intention 或环境行为 authority |
| 40 | experimental source safety equals production sandbox security | **CONTRADICTED / SHOULD NOT CLAIM** | source grammar/static checks 和 reduced runtime 不是 OS-level security proof |
| 41 | terminal protocol is reliable | **CONTRADICTED / SHOULD NOT CLAIM** | W3 与 W8 都出现语义克制但 terminal action 不被 harness 接受；只能声称现有 terminal semantic protocol **PARTIALLY SUPPORTED** |

## 5. Architecture reconstructed from actual code

### 5.1 Phase A: bounded deterministic synthetic domains

W0 的最小闭环位于 `world_model_experiment.py`：

```text
source-defined current model
→ record_prediction()
→ later record_observation()
→ resolve/comparison
→ append-only JSONL + strict replay
```

关键 symbol：

- `WorldModel` protocol：合成预测接口；
- `WorldModelTrace._append()`：append、flush、`os.fsync()`；
- `record_prediction()` / `record_observation()`：强制因果顺序；
- `resolve_prediction()`：确定性产生 `MATCHED / ERROR / UNVERIFIABLE`。

W1–W6 在此基础上增加 candidate revision：

```text
current source + bounded evidence
→ fresh Builder
→ isolated candidate source
→ static/source contract checks
→ deterministic public replay
→ hidden replay outside Builder visibility
→ exact acceptance gate
→ atomic current apply or unchanged
```

主要实现 seam：

- `world_model_builder_experiment.py`
  - `WorldModelBuilder`
  - `run_builder_case()`
  - candidate validator/evaluator
- `world_model_revision_experiment.py`
  - `RevisionBounds`
  - `EpisodeStatus`
  - `run_revision_episode()`
  - `_builder_context()`、`_read_resource()`、`_verify_source()`、`_run_bounded_analysis()`、`_atomic_write()`
- `world_model_stateful_revision_experiment.py`
  - `StatefulWorldModelRevisionBuilder.start_episode()` / `finish_episode()`
- `world_model_native_tool_experiment.py`
  - `NativeToolModel` protocol
  - `DeepSeekAnthropicNativeToolClient`
  - `NATIVE_TOOL_SPECS`
  - `NativeWorldModelRevisionBuilder`
- `world_model_read_batch_experiment.py`
  - `BoundedReadBatchBuilder`
  - `NativeActionEnvelope`
  - `_envelope_rejection()`
- `world_model_representation_experiment.py`
  - `validate_model_source()`
  - `evaluate_model_source()`
  - `BoundedRepresentationBuilder`

Phase A 的正确结论是：**受限 harness 能在三个 frozen synthetic domains 上安全地评估并选择 executable candidate。** 它不证明 candidate language 能覆盖真实世界，也不证明任意 Python 安全。

### 5.2 Phase B: bounded deterministic synthetic trajectories

W7 把 representation contract 从直接 observation mapping 改为：

```python
init_state(observation) -> state
transition(state, action) -> next_state
observe(state) -> predicted_observation
```

`world_model_latent_state_experiment.py` 的 `evaluate_model_source()` 只向 candidate 提供 initial observation 与 action sequence；后续真实 observations 留在 parent verifier 中比较。这避免了把答案逐步 teacher-force 给 candidate。`analyze_latent_state()` 不依赖预设字段名，而通过 causal/persistence/toggle probes 检查 state 是否确实参与预测。

W8 的 `world_model_completion_headroom_experiment.py` 复用 W7 prompt、tools、verifier 与数据，仅把 response budget 从 1600 改为 `W8_MAX_TOKENS = 2400`，并用 one-shot marker 固定唯一 campaign。因此：

- W8 是一个单变量 completion-headroom 实验；
- 三个可解 case 的 latent reconstruction 是真实证据；
- 不能因 W8 的局部成功把 W7/W8 整体 verdict 改成 PASS；
- 不能从 clamp 的 941-token 成功响应推断 1601–2400 tokens 是必需区间。

### 5.3 Three distinct kinds of state

必须保持：

```text
World Model State != Reality State != Observation
```

- **Reality State**：系统实际演化的权威状态；实验中由 harness/fixture 持有，未来只能由所属 organ 投影。
- **Observation / Reality Evidence**：对 Reality 的有界、有来源的可见证据，不等于 Reality 全貌。
- **World Model State**：candidate 为预测而构造的假设性内部表示；只要提高可检验预测即可，不享有本体论权威。

因此 `double_count`、`negated`、`clamped` 等字段是**predictively useful hypothesis state**，不是“发现了真实隐藏变量”的证明。多个内部表示可以对当前 evidence 观测等价；新的 Reality Evidence 有权证伪它们。

## 6. Builder boundary

W0–W8 支持的 Builder 是：

```text
fresh bounded auxiliary cognition
```

它可以：

- 读取明确暴露的 current model、trajectory/evidence、verification feedback；
- 在隔离 workspace 内写 candidate world-model representation/dynamics/notes；
- 接收 deterministic verifier 的失败反馈并在同一 episode 内修订；
- 在显式 turn/read/write/token budget 内结束。

它不可以：

- 成为 Execution Actor、Child Actor 或 recursive spawn target；
- 规划或执行环境动作；
- 改写 Reality Evidence；
- 修改 Execution、Mind、Intention、Focus、Directive 或 Environment；
- 影响任务 completion、阻止 Actor 继续、调用 shell/IPython/任意 filesystem；
- 因长期存活而变成 persona/permanent autonomous organ。

W6–W8 的“写 Python”必须准确描述为**写入隔离的、受 grammar 与 deterministic verifier 约束的世界模型 candidate**，不是赋予 runtime Mind 或 Builder 通用 Python authority。

## 7. W7/W8 deep audit

### 7.1 W7 clamp: real reasoning, incomplete artifact

W7 clamp 的第三个 assistant turn：

- `stop_reason=max_tokens`；
- `output_tokens=1600`；
- tool call 的 `write_file` input 退化为 `{}`，因此没有可验证 candidate；
- 被截断文本已经推导出 clamp 的核心 boolean/state mechanism。

所以该 case 是 **completion truncation 导致的结构性未完成**，不是“模型完全没有形成正确假设”。但没有 source、没有 verifier PASS、没有 apply，不能计作成功。

### 7.2 W8 clamp: headroom removed the observed truncation

W8 唯一改变 `max_tokens: 1600 → 2400`。clamp 随后：

- `stop_reason=tool_use`；
- 实际 `output_tokens=941`；
- 写入完整 source；
- public 与 hidden 均 1.0；
- accepted/applied。

证据仅支持：“在 W8 那次采样中，较高上限没有发生 W7 的截断，并允许协议完成。”由于实际输出低于 1600，不能声称正确答案本质上需要超过 1600 tokens，也不能把两次随机 provider trajectory 的差异全部归因于最低预算阈值。

### 7.3 W8 ambiguity: cognition/terminal split

原始 W8 ambiguity trace 显示：

1. Builder 完成三次只读 resource 调用；
2. 一次 `run_python` 被结构性拒绝，没有获得额外 authority；
3. Builder 写入 notes，明确记录：现有 step 机制完整解释已见 trajectory；toggle 未被验证但不与证据冲突；没有证据授权修复或增加 latent；下一条 discriminating observation 应观察 toggle 条件；
4. Builder 随后使用普通 `end_turn` 文本结束，而 harness 只接受 native `unresolved` terminal；
5. 最终为 `STRUCTURAL_FAILURE / invalid_model_action`、`current_changed=false`，没有 latent addition。

这里有两个不同语义：

```text
A. current 与 evidence 冲突，但多个 repair 都可解释，无法决定
   → UNRESOLVED

B. current 完整匹配已见 evidence，但未见条件下的行为仍未知
   → RETAIN_CURRENT / CONSISTENT_BUT_UNDERDETERMINED
```

W7/W8 harness 把二者都压到 `unresolved`，造成 W8 的模型判断与允许 terminal action 不匹配。故：

- cognition：SUPPORTED for this fixture；
- avoidance of unjustified latent/current mutation：SUPPORTED for this fixture；
- terminal protocol completion：FAILED；
- overall W8 verdict：仍为 `W8_INCONCLUSIVE`。

本审计只记录缺口，不修改 terminal vocabulary，不重跑 W8。

## 8. Tycho source-faithful comparison

固定参考为 NIMI-research/Tycho commit [`f68912a764372ead0a610db2e1c011d41ce5197e`](https://github.com/NIMI-research/Tycho/commit/f68912a764372ead0a610db2e1c011d41ce5197e)，该提交的 repository license 为 Apache-2.0。

从该提交可忠实借鉴的机制：

- model-owned state，由 initial observation 初始化；
- `transition(state, action)` 线程化推进；
- 从 state 投影 observation；
- 用 Reality trajectory replay 验证 candidate，并定位 first divergence；
- fresh bounded Builder 对模型 representation/dynamics 进行隔离修订；
- 持久化 evidence/notes，而非依赖无限增长的隐藏对话。

W7/W8 的三方法 contract 与 Tycho 的 [`seed_world_model.py.tmpl`](https://raw.githubusercontent.com/NIMI-research/Tycho/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/templates/seed_world_model.py.tmpl) 在机制上同源，但 Lumina 当前实验有意没有复制：

- ARC/grid/game ontology；
- `actions`、`subgoals`、`heuristic`、planner key 与游戏 outcome；
- 固定 Actor/Builder 拓扑；
- arbitrary workspace Python authority；
- 把 World Model 变成行动者或任务规划器。

Tycho 的 [`agent_tools.py`](https://raw.githubusercontent.com/NIMI-research/Tycho/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/workspace/agent_tools.py) 给 agent 较宽的 workspace read/write/edit/run authority，同时把 Builder 作为 advisory helper。Lumina 不应照搬这个 authority surface；W0–W8 的价值恰恰在于把 candidate modification 收窄到隔离表示，并让 Reality verifier 保持权威。

## 9. Production boundary audit

### 9.1 Mind status

`docs/MIND_DESIGN.md` 把 dynamic world model 与 Tycho-style executable World Model 列为 future/non-goal。这个生产事实没有过时：

```text
PRODUCTION: NOT IMPLEMENTED
EXPERIMENTAL: MECHANISM SUPPORTED IN NARROW SYNTHETIC HARNESSES
```

文档未来可增加这句成熟度区分，以避免“non-goal”被误读为“从未有实验”，但本任务不修改 authoritative design/status 文档。实验成功不自动构成 promotion authority。

### 9.2 Existing Execution public surface

`Execution/__init__.py` 当前只公开：

```text
ChildRef
ExecutionOrgan
ExecutionResult
ExecutionState
FileContentEquals
```

`ExecutionOrgan` 在 `Execution/organ.py` 中拥有内部 `SharedEnvironment`、`EventLog`、`AgentProcess` 与 persistent IPython。它公开：

- `.state`：重建只读 `ExecutionState`；
- `.result`：最后一个 `ExecutionResult`；
- `run_goal()` / `resume()`：会推进真实 Execution，属于 mutation authority，不是 World Model read capability。

`Execution/execution.py` 内部已有可复用的事实基础：

- `ExecutionEvent(event_id, sequence, payload, source_event_refs)`；
- append-only、`fsync` 且验证 causal refs 的 `EventLog`；
- `DecisionFrame`；
- 包含 goal、status、decision count、latest observation、last action/result、completion/failure 等字段的 `ExecutionState`；
- `_observation_projection()` 与 `_bounded_context()` 等有界投影先例。

这里的 `SharedEnvironment` 只是受控 execution workspace 的 owner，不是通用 Reality database；当前也没有一个名为 `environment_observation_ref` 的公开 DTO 字段。可用的 provenance 基础是 `ExecutionEvent.event_id` 与 `source_event_refs`，必须由 Execution-owned projection 解释后才能成为 future Reality Evidence。

但 `EventLog`、`DecisionFrame` 和这些 projection helper 不是 World Model 的公开 facade。`core` 的 `/api/execution` 也只投影 execution id、status、result、verified，明确隐藏 EventLog、DecisionFrame、provider body、代码和路径。

### 9.3 Missing seam

底层数据足以投影 current goal、coarse status、recent important outcome/failure 和有界 event facts，但当前**没有 supported public read-only Reality Evidence seam**，尤其缺少：

- 在对应 outcome 出现前，让 shadow predictor 获得一个 durable pre-outcome evidence point；
- outcome 出现后，用稳定 event reference 进行因果配对；
- 从 Execution-owned internals 中做 bounded/redacted/deterministic projection；
- restart 后按相同 cursor/ref 重放相同 projection；
- 不暴露 provider body、raw code、本地路径、任意文件内容或执行工具 handle。

直接让 `Mind/` import/解析内部 `EventLog`、`DecisionFrame`、Runtime/IPython 对象，会绕过 `ExecutionOrgan` 的 ownership boundary。仅在 `run_goal()` 完成后读取最终 `ExecutionResult` 也不足以验证 anti-hindsight，因为 prediction 已经可能看到 outcome。

稳定的责任分离应明确写成：

```text
Execution EventLog = historical execution truth
World Model        = compressed, falsifiable predictive hypothesis
```

World Model 不得覆盖 EventLog，也不得被提升为真实状态数据库。

因此下一阶段若进入 shadow integration，需要 **一个小而深的 Execution-owned adapter/seam**，而不是 Tool Registry、Manager、服务层或通用事件框架。

## 10. Minimal shadow architecture

允许进入下一阶段的最小数据流为：

```text
Execution
→ Execution-owned bounded RealityEvidence projection
→ isolated shadow prediction
→ later Execution-owned RealityEvidence outcome
→ deterministic reality comparison
→ append-only shadow trace
→ optional isolated Builder revision after mismatch
```

边界：

- World Model 与 Builder 均为 shadow/read-only；
- prediction 必须 durable 地先于 outcome evidence；
- RealityEvidence 必须由 Execution 投影并带稳定 event/source references；
- World Model 不能调用 `run_goal()`、`resume()`、IPython、tool host、shell 或 filesystem；
- 不得使用现有 `decision_advisory` 把预测或 revision 注入 Actor；
- 不得改变 Intention、Focus、Directive、Execution state、Environment 或 completion decision；
- World Model failure 必须 fail-soft，不能延迟或阻止 Execution；
- Shadow on/off 的 Execution outcome 必须相同；
- 初始 S0 不需要 DeepSeek campaign，应该用现有 deterministic `ScriptedModel`/mock trajectory 验证结构和因果顺序。

这一结构遵循 small deep interface：World Model 只看到稳定、有限、去敏的 RealityEvidence，而 Execution 保留事件内部结构、provider trace 和行动 authority。

## 11. Stop/go decision

### 11.1 Synthetic harness

```text
STOP SYNTHETIC HARNESS EXPANSION
```

理由：

- W6 已证明 simple executable dynamics；
- W7/W8 已证明窄范围 model-owned latent state 与 hidden trajectory replay；
- 再增加 synthetic domains、prompt、budget、tool 或 source grammar 会同时改变新变量，并继续优化已知 benchmark；
- 当前最大未知已经不是“能否再解一个 fixture”，而是“真实 Execution 事实能否在不泄露 authority/答案的情况下形成可预测、可比较、可重放的 Reality Evidence”。

停止扩展不等于修复 W8 terminal mismatch，也不等于否认合成 regression 的价值。现有 fixtures 应冻结为 regression evidence；本任务不重跑 campaign。

### 11.2 Shadow integration gate

```text
GO WITH CONDITIONS
```

条件：

1. 只做 read-only shadow observation，不做 steering；
2. 单一变量从 synthetic evidence producer 切换到 Execution-owned RealityEvidence projection；
3. predictor、comparison、bounds、mock model 与已有 trace invariants 保持固定；
4. prediction 事件必须先 durable，outcome 后到且有 causal refs；
5. 不直接依赖 Execution internal EventLog/DecisionFrame/IPython；
6. shadow failure 不影响 Execution；
7. 不修改 prompt、budget、tool protocol、source grammar、terminal semantics；
8. 不进行 DeepSeek campaign；若未来需要真实模型，只能复用现有 DeepSeek provider，并作为另一个预注册、另行授权的单变量实验。

## 12. Exact next task

建议下一任务名：

```text
World Model Shadow Integration S0 — Execution-derived Reality Evidence Projection
```

**Hypothesis**

在一个隔离、deterministic、scripted Execution trajectory 中，Execution 可以通过一个 owner-controlled、bounded、redacted、read-only projection 产生足够的 pre-outcome 与 outcome Reality Evidence，使现有 W0 predictor/comparator 能在 outcome 前提交预测、在 outcome 后确定性比较，并且 shadow on/off 不改变 Execution。

**Single variable**

```text
synthetic fixture Reality Evidence
→ Execution-derived Reality Evidence
```

保持不变：prediction/error schema、append-only trace、comparison rule、model mock、turn/budget、无 Builder 或原样隔离的 Builder、无 steering。

**Minimal implementation surface anticipated, not authorized here**

- `Execution/`：增加一个最小 public RealityEvidence DTO 与 owner-controlled read-only projection/observer seam；这是唯一可能必须修改的 `Mind/` 外 surface。
- `Mind/`：增加一个 S0 shadow harness/adapter 和对应 test/artifact；复用 W0 trace/comparator，不创建 generic registry/framework。
- 初始 S0 不应把 W7 的 `{value}/step/toggle` 合成 contract 冒充真实 Execution abstraction；先验证 evidence ownership、causality、boundedness 与 non-interference。

**Acceptance criteria**

1. 使用 supported `ExecutionOrgan` 与 deterministic scripted model 运行一个 isolated execution；
2. prediction durable event 的 sequence/timestamp/order 明确早于对应 outcome evidence；
3. RealityEvidence DTO 有 stable evidence/event refs、source refs、kind、bounded payload，并排除 raw provider body、code、path、credentials 和任意文件内容；
4. projection 有固定数量/字符预算与稳定排序；
5. restart/reopen 后相同 cursor/ref 得到 byte-equivalent 或规范化等价 projection；
6. outcome 到达后现有 comparator 生成 `MATCHED / ERROR / UNVERIFIABLE` 并追加 shadow trace；
7. shadow enabled/disabled 的 `ExecutionResult`、completion/failure 与外部 workspace effect 等价；
8. shadow/trace/projection failure fail-soft，不阻塞 Execution；
9. World Model 进程/对象没有 shell、filesystem、IPython、tool host、pause/resume、Intention mutation 或 `decision_advisory` authority；
10. 测试能证明 Mind/World Model 无法取得 internal EventLog、DecisionFrame mutable handle 或 Execution methods；
11. 不调用 DeepSeek，不改 provider/prompt/budget/tool/source grammar，不实现 trigger/Builder expansion；
12. 相关 Execution 与 Mind regression 全部通过，git diff 只包含预注册明确授权的最小 surface。

若无法在 outcome 前得到 owner-controlled durable projection，或只能通过解析 Execution internals/最终 result 获得数据，则 S0 应判定 BLOCKED，而不是放宽 anti-hindsight 或 ownership boundary。

## 13. Risks of accidental architectural expansion

- **Planner drift**：让 World Model 输出下一步工具/文件/命令，会把预测机制变成 Planner。
- **Actor drift**：暴露 IPython、tool host、shell、filesystem 或 `run_goal/resume`，会把 shadow cognition 变成 Execution Actor。
- **Supervisor drift**：把 model prediction/revision 注入 `decision_advisory`，会在尚未测量预测质量前引入 steering。
- **Reality ownership drift**：让 candidate 重写/补造 Reality Evidence，comparison 将失去权威。
- **Hindsight leakage**：从最终 `ExecutionResult` 反推“先前预测”，会产生不可证伪的伪准确率。
- **Ontology inflation**：把模型自行命名的 latent field 当真实隐藏状态，会阻止等价模型竞争和未来证伪。
- **Framework drift**：为一个 projection 添加 Registry/Manager/event bus，会扩大 public surface 而不增加实验辨识力。
- **Benchmark overfitting**：继续添加类似 arithmetic/string fixtures 或调整 prompt/budget，会把真实集成未知留在原地。
- **Completion coupling**：让 shadow failure 阻止 Execution complete，会给 World Model 获得未授权的 veto authority。

## 14. Final A–J disposition

### A. Overall verdict

W0–W8 支持一个有界、隔离、可重放的实验性 World Model/Builder 机制；不支持 production World Model。`W7_INCONCLUSIVE`、`W8_INCONCLUSIVE` 保持不变。

### B. Claim ledger summary

- Supported：先预测后观察、Reality comparison、确定性 replay、candidate isolation/atomic apply、fresh bounded Builder、native tools/bounded reads、简单 executable dynamics、窄 latent reconstruction 与 hidden replay，以及冻结 threat model 内的 safety。
- Partially supported：continuity 的普遍价值、避免无必要 ontology、迭代修复可靠性、terminal semantic closure。
- Not validated：统一长期模型、真实 Execution、跨域/长历史/regime change/主动探测/自动 trigger/production security/behavioral steering。
- Should not claim：W7/W8 PASS、>1600 必需、ambiguity cognition failure、latent 是真实隐藏状态、Builder 是 Actor、grammar 是 production sandbox。

### C. Phase A

**SUPPORTED for bounded deterministic synthetic domains.**

### D. Phase B

**SUPPORTED for bounded deterministic synthetic trajectories, while W7 and W8 experiment-level verdicts remain INCONCLUSIVE.**

### E. W8 ambiguity diagnosis

**Primarily terminal semantic mismatch, not cognition failure.** 当前 evidence 与 current 一致、未来条件欠定，需要 `RETAIN_CURRENT / CONSISTENT_BUT_UNDERDETERMINED` 一类语义；现有 `unresolved` contract 没有表达它。

### F. Synthetic harness decision

**STOP SYNTHETIC HARNESS EXPANSION.** 冻结现有 fixtures 作为 regression evidence。

### G. Shadow decision

**GO WITH CONDITIONS**：零行动权、零 steering、Execution-owned RealityEvidence、严格 anti-hindsight、fail-soft、单变量 synthetic → Execution-derived evidence。

### H. Exact next task

**World Model Shadow Integration S0 — Execution-derived Reality Evidence Projection.** 先验证 projection/causality/non-interference；不做真实模型 campaign 或 Builder 新能力。

### I. Files changed by this audit

```text
Mind/docs/WORLD_MODEL_W0_W8_AUDIT.md  (new; this document only)
```

未修改 production、test、fixture、manifest、result、authoritative status/design 文档或 upstream source。

### J. Validation and git disposition

实际验证结果：

- `python -m pytest -q` 加九个 `test_world_model_*.py` focused suites：`208 passed in 152.09s`；这些是本地 deterministic/mock tests，不是 W7/W8 campaign；
- `git diff --check`：通过，无 whitespace error；只有既有工作树的 LF→CRLF warning；
- `git diff --no-index --check -- NUL docs/WORLD_MODEL_W0_W8_AUDIT.md`：没有 whitespace diagnostic；exit 1 仅表示新增文件与 `NUL` 不同；
- `git status --short -- docs/WORLD_MODEL_W0_W8_AUDIT.md`：`?? docs/WORLD_MODEL_W0_W8_AUDIT.md`；
- 完整 `git status --short` 显示仓库在本任务前已经存在大量 tracked modifications 与 untracked files，并把整个 `Mind/docs/` 汇总为 untracked；本任务没有清理、覆盖或认领这些既有改动；
- 本任务实际造成的文件变化仅为本审计文档。没有运行 W7/W8 campaign，没有 provider/DeepSeek call，没有修改 runtime、fixture、manifest、result 或 preregistration。

## Final verdict

```text
STOP_SYNTHETIC_HARNESS_EXPANSION
GO_WITH_CONDITIONS_FOR_WORLD_MODEL_SHADOW_INTEGRATION_S0
PRODUCTION_PROMOTION_NOT_AUTHORIZED
```
