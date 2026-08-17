# Lumina Execution Organ 分阶段开发计划

## 一、目标

在现有 Lumina 之外建立一个完全隔离的 Execution 实验系统，逐步验证以下核心假设：

> 自治 Agent + 持久状态 + Event + Recursive Spawn + Resource Constraint，能否形成一种比固定 DAG 更通用的含时执行模型。

只有经过实验验证有效的机制，才允许进入下一阶段或未来接入 Lumina。

现阶段不接入 Mind、Memory、Dream、Nervous、Evolution，不改变现有 Lumina 生产路径。当前这些执行相关器官尚未实现，因此实验系统应保持完全独立。

---

## 二、开发原则

1. **逐级验证**
   每个阶段只增加一个关键能力，并与上一阶段比较。

2. **执行结构不预设**
   不把 DAG、TDP、ReAct 或 Multi-Agent 固定为系统本体。

3. **智能与机制分离**
   Agent 负责理解任务和自主行动；Runtime 只提供可靠执行机制。

4. **从第一天保留真实轨迹**
   Trace 必须伴随最初的 Agent 实验产生，不能后补。

5. **State、Trace、Context 最终必须分离**
   - Trace：实际发生过什么；
   - State：现在是什么状态；
   - Context：当前一次模型决策应看到什么。

6. **North Star 只指导方向**
   当前开发只验证 Execution substrate，不提前实现完整数字生命架构。

---

# 三、阶段规划

## Phase 0 — 独立 Execution Lab

### 目标
建立与现有 Lumina 完全隔离的实验环境。

### 验证
确认执行实验可以独立运行、独立测试、独立失败，不影响现有 Chat、Draft、Dream 和 Conversation Memory。

### 晋升条件
实验环境边界明确且不存在对现有生产路径的侵入。

---

## Phase 1 — Tool Foundation

### 目标
建立最基本的现实行动能力。

验证 Agent 执行外部操作时：

- 行动具有明确输入和结果；
- 成功与失败均可观察；
- 一次工具失败不会破坏整个执行系统；
- 工具行为可以被记录和重放分析。

### 核心问题

> Lumina 是否已经拥有一个足够稳定的“行动层”？

### 晋升条件
工具层能够独立、稳定、可观测地工作。

---

## Phase 2 — Single-Agent ReAct Baseline

### 目标
建立最简单、可工作的自主 Agent baseline。

只验证：

```text
Goal
→ Reason
→ Action
→ Observation
→ Reason
→ ...
→ Complete
```

### 目的

它将成为以后所有复杂执行架构的基准。

后续任何：

- Recursive Agent；
- TDP；
- 并行；
- Context 管理；

都必须证明比这一 baseline 在特定任务类型上有实际收益。

### 晋升条件

单 Agent 能稳定完成一组基础、多步、需要工具反馈的任务。

---

## Phase 3 — Execution Trace Foundation

### 目标
使所有真实执行成为可研究的数据。

记录完整的执行事实，包括：

- Agent 当时看到什么；
- 做出了什么行动；
- 得到了什么反馈；
- 什么时候发生；
- 执行最终结果。

Trace 只保存事实，不承担解释、总结或状态维护。

### 核心问题

> 能否准确重建一次执行真正发生了什么？

### 晋升条件

任意一次实验都可以通过 Trace 还原其主要决策和行动过程。

---

## Phase 4 — State / Trace / Context Separation

### 目标
打破：

```text
Agent state = conversation history
```

这种短期 Agent 假设。

正式区分：

```text
Trace
State
Context
```

### 研究重点

验证长期执行过程中：

- Agent 状态是否能够独立于完整历史存在；
- Prompt 是否可以只包含当前决策真正需要的信息；
- 历史增长是否不再导致上下文同步增长。

### 晋升条件

在更长任务中，状态连续性能够维持，同时 Context 增长明显受控。

---

## Phase 5 — Single Child Recursion

### 目标
验证第一个核心架构假设：

> Agent 是否应该能够在执行过程中自主产生另一个完整 Agent。

只研究：

```text
Parent
→ spawn Child
→ Child autonomous execution
→ result
→ Parent continue
```

暂不追求复杂树结构。

### 对照实验

比较：

- Parent 自己解决全部问题；
- Parent 将一个子问题交给 Child。

关注：

- 成功率；
- context isolation；
- token 消耗；
- 重复劳动；
- 父 Agent 后续决策质量。

### 晋升条件

至少存在明确任务类别，其中递归调用具有稳定收益。

---

## Phase 6 — Event / Wait Temporal Execution

### 目标
从“递归调用”升级成真正的含时执行。

允许 Agent：

```text
运行
→ 等待某个条件
→ 休眠
→ Event 到来
→ 恢复
→ 继续
```

### 研究重点

验证：

- Child 完成；
- 工具完成；
- 消息；
- 时间事件；

是否能够统一成为执行事件。

### 核心问题

> Agent 是否可以在“不持续占用模型”的情况下继续存在？

### 晋升条件

任务可以跨越等待阶段并稳定恢复，而且恢复后的 Agent 保持正确任务状态。

---

## Phase 7 — Resource Constraint

### 目标
给递归自治加入最小硬约束。

验证：

> 有限资源是否能够自然约束 Agent 的递归深度、并行度和探索行为。

资源机制只承担限制作用，不引入复杂经济系统。

### 重点观察

- 无资源约束时的 spawn 行为；
- 有资源约束后的组织变化；
- Agent 是否学会更慎重地分化任务；
- 是否减少无价值递归。

### 晋升条件

资源约束能显著减少失控执行，同时不严重损害任务成功率。

---

## Phase 8 — Full Recursive Execution

### 目标
解除单层、单 Child 限制。

允许执行结构运行时自由形成：

```text
A
├── B
│   ├── D
│   └── E
└── C
```

但不提供全局 Planner。

### 这一阶段主要观察，而不是优化

研究是否自然出现：

- 分工；
- 专业化；
- 并行探索；
- 动态深度；
- join；
- 重规划；
- 局部 DAG；
- 重复劳动；
- recursion loop；
- agent storm。

### 核心实验

与以下系统比较：

```text
Single ReAct
Fixed DAG / TDP
Recursive Temporal Execution
```

### 晋升条件

Recursive Runtime 至少在动态或开放任务上表现出不可由简单 baseline 替代的优势。

---

## Phase 9 — Mutable Execution

### 目标
让一个已经运行的 Execution 可以被外界改变。

支持语义层变化：

```text
Goal change
Constraint change
Priority change
Pause
Resume
Cancel
```

Agent 仍然自主决定如何响应这些变化。

### 核心实验

在任务执行中主动改变：

- 用户要求；
- 外部环境；
- 任务约束；
- 优先级。

比较：

- 从头重新执行；
- 对当前 Execution 动态修改。

### 晋升条件

动态修改能可靠传播，并明显优于完全重启任务。

---

## Phase 10 — ExecutionView

### 目标
建立未来 Mind 与 Execution 之间的认知边界。

不让 Mind 消费完整 Raw Trace。

形成：

```text
Trace + State
      ↓
ExecutionView
```

ExecutionView 只表达：

- 当前目标；
- 当前状态；
- 主要进展；
- 活跃执行过程；
- 等待事项；
- 重要变化；
- 失败与异常；
- 资源状态。

### 核心问题

> 一个高层认知系统能否在低信息量下正确理解当前 Execution？

### 晋升条件

ExecutionView 足以支持绝大部分高层判断，需要查看 Raw Trace 的情况成为少数。

---

## Phase 11 — Mind Integration Experiment

此阶段才第一次与未来 Mind 接触。

结构：

```text
Execution
    ↓
ExecutionView
    ↓
Mind
    ↓
Control Event
    ↓
Execution
```

Mind 只处理：

```text
继续
改变目标
改变约束
调整优先级
暂停
恢复
取消
```

不控制具体 Tool、Child 或下一步行动。

### 重点验证

研究双时间尺度反馈：

```text
快速：
Agent ↔ Environment

慢速：
Execution → Mind → Execution
```

是否能够改善：

- recursion loop；
- 无效探索；
- 长期任务方向偏移；
- 外部目标变化；
- 资源浪费。

---

# 四、Evolution 接口预留

当前阶段不开发 Evolution。

但从 Trace 阶段开始，必须保证未来能够回答：

```text
当时 Agent 看到了什么？
处于什么状态？
使用了什么模型/能力？
为什么产生 Child？
分配了多少资源？
采取了什么行动？
产生了什么结果？
Mind 是否干预？
最终任务结果如何？
```

未来 Evolution 才能基于真实执行学习：

```text
spawn policy
context policy
resource policy
tool policy
Method
planning strategy
```

Execution Trace 将成为 Evolution 的原始学习材料，而不是为 Evolution 提前设计优化逻辑。

---

# 五、Nervous 接口预留

当前阶段不开发 Nervous。

只要求未来能够自然形成：

```text
External World
      ↓
   Nervous
      ↓
    Event
      ↓
  Execution
```

以及：

```text
Execution
    ↓
  Action
    ↓
 Nervous
    ↓
External World
```

因此 Execution 不应该绑定特定外部环境。

---

# 六、核心对照实验体系

长期保持至少三种 baseline：

### A — Single ReAct

最低复杂度基准。

### B — Structured Planning

固定 DAG / TDP 类结构。

### C — Recursive Temporal Execution

本方案。

任务分成：

```text
静态、容易预分解任务
动态反馈任务
开放探索任务
长时间等待任务
执行中目标变化任务
```

核心指标：

```text
success
token / cost
wall time
context usage
duplicate work
spawn efficiency
adaptation
recovery
resource efficiency
```

不要求 C 在所有任务上胜出。

目标是确定：

> **什么任务分布真正需要 Recursive Temporal Runtime。**

---

# 七、停止规则

任何新增机制都必须回答：

> 当前系统出现了什么可复现失败，这个机制为什么是解决它的最小办法？

禁止因为“未来可能需要”而提前加入：

- Scheduler；
- Distributed Worker；
- 多层 Planner；
- 复杂 Trigger Framework；
- Agent Economy；
- 自动组织算法；
- 复杂 Supervisor；
- Evolution Policy；
- Mind reasoning；
- 大型 Context Retrieval；
- 完整 TDP；
- 多 Agent communication framework。

没有失败证据，就不增加机制。

---

# 八、阶段总览

```text
Isolated Lab
    ↓
Tool
    ↓
Single ReAct
    ↓
Raw Trace
    ↓
State / Trace / Context Separation
    ↓
Single Child Recursion
    ↓
Event / Wait
    ↓
Resource Constraint
    ↓
Full Recursive Execution
    ↓
Mutable Execution
    ↓
ExecutionView
    ↓
Mind Integration Experiment
```

最终要验证的不是“能否造出一个复杂 Multi-Agent 系统”，而是：

> **少量执行规则能否形成一个能够长期存在、自主分化、响应环境变化、接受心智调节并产生高质量进化数据的通用行动基座。**

只有这个假设被实验支持，Execution 才应正式晋升为 Lumina 的执行器官。