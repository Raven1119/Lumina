# Mind 定义计划 V1

> 状态：**阶段二已晋升**（阶段一落地 + 阶段二 LlmMindGate 生产晋升均完成；
> 生成：Kimi；审批：GPT5.6Sol，由创造者转述结论）。
> 生产默认门控为 `LlmMindGate`（`LUMINA_MIND_GATE_MODE=llm`），
> `constant` 为一键回滚；晋升证据见
> `docs/experiments/mind_stage2_promotion/RESULT.md`。
> 后续阶段（Mind 新职责）未授权，须另行审批与实验对照。
>
> 本文档由一轮 grilling 烤问整理而成，定义 Lumina 的 Mind 器官。
> 全部内容在审批通过前不构成实现授权。

## 0. 定位原则

- `docs/NORTH_STAR.md` 是**终局方向**，不是下一阶段的说明书。
- 远期 Mind（画布 `Lumina_Nervous_Organ.canvas` 中的中枢器官）是方向；
  本计划只定义**下一阶段的最小 Mind**。
- 远期形态包括：两段式结构化回应计划、向 Execution 派发 Action Request、
  被 Nervous Organ 触发器唤起会话以保证连续性、受一个能影响行动的自我系统
  驱动。这些**现阶段不实现、不写入当前定义**。

## 1. Mind 是什么

Mind 是四器官架构（Mind / Execution / Memory / Nervous）中的**决策中枢**。

- 它不是整体心智本身——整体心智是所有器官的 emergent 结果，属于北极星。
- Nervous Organ 的最小形态是**消息等待队列**。
- Memory Organ ≈ 现有 Hot/Cold Draft + Dream + MAGMA + Recall 全体。
- Execution Organ 的本阶段边界留白（先把 chatbot 跑通）。

## 2. 下一阶段的最小 Mind

唯一职责：**决定要不要 Recall**。不是路由器，不携带 Execution 字段。

### 2.1 在聊天路径上的位置

Mind 位于 Recall 守卫**之前**，是消息进入后的第一个器官：

```text
user message
-> Mind decision（recall_enabled=false 或记忆不可用时也照常经过）
-> 现有 Recall capability/enabled guard
-> capability 存在且 Mind.recall=true -> Recall
-> 否则 -> 普通 Chat
```

阶段一恒真，聊天行为不变。所有权关系是 **User -> Mind -> Memory**，
不是 User -> Memory plumbing -> Mind gate。

### 2.2 输出形态

```python
@dataclass(frozen=True)
class MindDecision:
    recall: bool
```

与远期结构化回应计划共享同一条 schema 接缝，将来追加 action / tone
等字段不破坏接口。**第一阶段不提前扩 schema**：reason、时间戳等属于
审计元数据，由决策日志在写入时生成，不进 Mind 的决策 contract。

### 2.3 输入

当前用户消息 + 有界的最近若干轮 Hot 尾部文本（界要小，只传文本）。

### 2.4 决策机制

- 复用已配置的 MiniMax provider（与 Grounded Formation 同族），
  non-thinking 模式；
- `max_tokens` 极小，输出约束为单个布尔；
- temperature 固定为 0，追求近似确定性；
- mock 模式下 Mind **恒真**（`recall: true`），保持测试确定性。

### 2.5 故障语义（决策失败与日志失败分开记录）

- `decide()` 调用失败 / 超时 / 输出非法 → 事件 `mind_gate_failed`，
  fail-open 为 `recall: true`，不阻塞聊天；
- 决策日志写入失败 → 事件 `mind_decision_log_failed`，同样 fail-open：
  即使原决策为 `recall: false`，该**不可审计的拒绝**不得静默生效，
  退化为放行 Recall。两类失败必须能从事件流区分——失败的是
  Mind 推理还是 audit persistence。

### 2.6 代码形态

新建 `Mind/` 顶层器官包（对齐 Nervous Organ 画布的器官划分），
通过 `core/` 中的接缝注入生产链路。**Mind 的装配不依赖 Recall
wiring**：即使 Recall 被关闭或不可用，Mind 照常构造、照常经过，
其 `recall: true` 只是不触发实际 Recall。

### 2.7 审计

Mind 的每次决策写入独立的 append-only JSONL 决策日志，记录
`{turn_id, recall, decided_at}`；`decided_at` 由日志写入方在写入时
生成，不属于决策 contract。

这是原始形态：远期整个 Lumina 的行动轨迹由统一的 **Trace** 系统管控
（本计划不定义 Trace）。

## 3. 负面清单（Mind 永远不做）

1. 不直接写记忆——写入路径只属于 Dream / Cold 拥有者；
2. 不自己执行 action——只派发；
3. 故障必须 fail-soft 退化到现有直通链路，不得阻塞聊天；
4. 不自造记忆证据——只读 Memory 器官的 DTO；
5. 决策必须可持久化、可审计，不是黑盒。

## 4. 证据纪律（分两阶段验收）

- **阶段一：恒真占位版**（行为零变化）。接缝、审计日志、fail-soft 全部
  就位，但门控恒为 `recall: true`。可直接进入。
- **阶段二：真实 LLM 门控**。必须在授权对齐的 36 例开发集上跑
  baseline vs candidate 对照（正负分层、回归、确定性、重启幂等），
  达标并经审批后才晋升为生产行为。
- 任何门控行为变更适用 AGENTS.md 第 5 节"一次只改一个变量"与第 12 节
  的报告要求。

## 5. 明确留白（本计划不回答）

- 主动性（Internal Signals）信号的来源机制——归 Nervous Organ，待实现期；
- 自我状态（人格 / 情感 / 欲望 / 自我叙事）影响回答的通道——远期自我系统
  的职责，载体定为混合式（持久叙事文档 + 瞬时推导），实现留白；
- Mind 与 Execution 的分界细节；
- Trace 系统的具体形态。

## 6. 与现有契约的关系

- 本计划不修改 Cold-first、Dream、Recall、MAGMA 固定的任何既有边界；
- 不违反 AGENTS.md 第 10 节的未授权清单：`Mind/` 包的唯一职责是 Recall
  门控决策，不包含调度器、worker、自主 ingestion 或 ContextBuilder；
- 阶段一落地时应遵守最小变更规则并补齐对应测试。
