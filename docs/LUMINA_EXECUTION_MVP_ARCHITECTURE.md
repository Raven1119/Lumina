# Lumina Execution MVP — 最小可行执行器官架构

> 状态：设计冻结稿\
> 目标：验证 Lumina Execution 的最小本体是否成立。\
> 范围：单一目标、单一 Root Actor、真实环境连续交互、外部状态、事件驱动、中断/恢复、IPython 可编程控制面、完整执行痕迹。\
> 当前明确不实现：Mind、Child Agent、递归、Blackboard、Capability Search、多目标、多 Agent 并发、Self-Cognition、Evolution。

---

## 1. MVP 目标

MVP 不验证多 Agent、DAG、Swarm、递归拓扑或 Mind。

它只验证：

> 一个持续存在的 Root AgentProcess 能否在真实环境中围绕单一目标持续行动，并在中断、崩溃和重启后基于外部真实状态继续执行，同时留下可重建的完整执行事实。

核心闭环：

```text
Goal
  ↓
Root AgentProcess
  ↓
bounded Context
  ↓
Model
  ↓
Generated Python
  ↓
IPython
  ↓
typed capability request
  ↓
ExecutionRuntime
  ↓
ActionHost / ToolHost
  ↓
Shared Environment
  ↓
Observation / Result
  ↓
ExecutionRuntime
  ↓
EventLog / State
  ↓
wake Root
  ↺
```

形式上：

\[
Context_t
\rightarrow Decision_t
\rightarrow Action_t
\rightarrow Environment_{t+1}
\rightarrow Event_t
\rightarrow State_{t+1}
\rightarrow Context_{t+1}
\]

Execution 是持续的感知—行动闭环，不以一次 ReAct loop 作为系统本体。

---

## 2. MVP 一等对象

第一版只实现五个一等对象：

```text
RootAgentProcess
ExecutionRuntime
IPythonControlPlane
ToolHost
SharedEnvironment
```

其中 EventLog、StateReducer、ExecutionState、Checkpoint 都属于 ExecutionRuntime 内部机制，不拆成独立 Service。

### 2.1 RootAgentProcess

Root 是唯一执行 Actor。

它不是 Planner，而是完整的局部认知—行动过程：

```text
observe
reason
act
wait
inspect
claim_complete
submit_final
```

Root 默认自己执行任务。

MVP 不存在 Child Agent，因此不实现 `spawn()`。

Root 的身份与模型上下文分离：

```text
Root identity
!=
Root persistent state
!=
Current model context
```

每次被唤醒时，根据当前 Goal、ExecutionState、相关 Event、Actor local state 和按需获得的环境 Observation 重构 bounded Context。

---

### 2.2 ExecutionRuntime

Runtime 只负责机械执行语义：

```text
event append
state fold
dispatch
actor lifecycle
interrupt
wait / wake
checkpoint
restart
action authority
completion verification
```

Runtime 不负责：

```text
semantic planning
task decomposition
tool selection
reasoning
global strategy
```

核心边界：

```text
AgentProcess owns local semantic execution.
Runtime owns mechanical lifecycle and authority.
```

---

### 2.3 IPythonControlPlane

IPython 是模型可编程的执行控制面，不是状态权威。

模型可以生成 Python：

```python
result = await tool(...)
if condition:
    ...
```

但所有真实能力调用必须经过 typed Runtime capability：

```text
Model-written Python
        ↓
IPython
        ↓
typed capability request
        ↓
ExecutionRuntime validation
        ↓
real action
```

IPython namespace 只是 working state。

崩溃恢复时不要求恢复 Python 指令指针、await continuation 或任意内存对象。

---

### 2.4 ToolHost

MVP 只提供极少、真实、稳定的能力，例如：

```text
read
write
shell
```

当前不实现 Capability Search。

ToolHost 的职责：

```text
validated input
→ real action
→ observable result/failure
```

ToolHost 不拥有执行状态。

---

### 2.5 SharedEnvironment

Environment 是真实世界当前状态。

MVP 可以先限定为隔离工作区中的：

```text
filesystem
git repository
shell/process state
test/build outputs
```

Runtime 可以观察、作用于环境，但不拥有 Environment。

---

## 3. 权威状态模型

必须严格区分：

```text
Shared Environment
    = 现实当前是什么

EventLog
    = 已经真实发生过什么

ExecutionState
    = 根据 EventLog 推导出的当前执行状态

Checkpoint
    = ExecutionState 的恢复快照

Context
    = 当前一次模型采样真正看到什么

IPython namespace
    = 临时可编程 working state
```

核心不变量：

\[
Trace \neq State \neq Context
\]

\[
Past\ is\ immutable;\ Future\ action\ is\ mutable
\]

### 3.1 EventLog

EventLog 是 Execution 历史事实权威。

最低事件集合：

```text
EXECUTION_STARTED
MODEL_DECISION
CODE_EXECUTED

ACTION_STARTED
ACTION_RESULT
ACTION_FAILED
ACTION_RECONCILED

ENVIRONMENT_OBSERVED

ACTOR_STATE_CHANGED

CHECKPOINTED

COMPLETION_CLAIMED
COMPLETION_VERIFIED
COMPLETION_REJECTED

EXECUTION_COMPLETED
EXECUTION_FAILED
```

所有真实行动必须形成：

```text
ACTION_STARTED
      ↓
real action
      ↓
ACTION_RESULT / ACTION_FAILED
```

外部 side effect 前先持久化 `ACTION_STARTED`。

---

### 3.2 ExecutionState

ExecutionState 是 EventLog 的 materialized view：

\[
ExecutionState = Fold(EventLog)
\]

它可以包含：

```text
activity status
actor status
current / pending actions
waiting conditions
latest observations
completion state
recovery state
```

ExecutionState 可以重建，不是历史真相本身。

---

### 3.3 Checkpoint

Checkpoint 只用于加速恢复。

正常路径：

```text
EventLog
  ↓
StateReducer
  ↓
ExecutionState
  ↓
safe-point Checkpoint
```

恢复路径：

```text
Latest Checkpoint
      +
EventLog tail replay
      +
current Environment reconciliation
      ↓
current ExecutionState
```

形式上：

\[
State_{restart}
=
Checkpoint
+
Replay(EventTail)
+
RealityReconciliation
\]

---

## 4. DecisionFrame

每一次真正的模型决策都记录一个 DecisionFrame。

最小结构：

```text
DecisionFrame
- model / version
- goal / activity
- ExecutionState ref
- incoming Event refs
- actual Context seen
- capabilities exposed
- relevant Environment observation refs
- model output
```

目的不是记录模型隐藏思维，而是忠实保存：

> 模型在这一刻实际看到了什么状态、什么事件、什么能力，并输出了什么行动。

这些记录以后可以用于 Self-Cognition，但 MVP 不实现 Self-Cognition。

---

## 5. 正常执行闭环

正式闭环：

```text
Single Goal / Activity
        ↓
Root AgentProcess
        ↓
reconstruct bounded Context
        ↓
Model
        ↓
Generated Python
        ↓
IPython
        ↓
typed capability request
        ↓
ExecutionRuntime
        ↓
append ACTION_STARTED
        ↓
ToolHost
        ↓
Shared Environment
        ↓
Observation / Result
        ↓
ExecutionRuntime
        ↓
append ACTION_RESULT
        ↓
StateReducer
        ↓
ExecutionState
        ↓
wake Root
        ↺
```

Root 不维持无限 transcript。

上下文来自当前状态重构：

```text
Goal / Activity
ExecutionState
Incoming Events
Actor local state
Relevant observations
        ↓
bounded Context
```

环境信息按需获取，不扫描整个 World。

---

## 6. 中断与暂停

### 6.1 正常中断

语义：

> 尽快停止未来动作，而不是强制把现实世界冻结在同一瞬间。

流程：

```text
interrupt requested
    ↓
stop issuing new actions
    ↓
cancel safely cancelable in-flight work
    ↓
settle / mark remaining actions
    ↓
checkpoint
    ↓
SUSPENDED
```

已经 committed 的现实 side effect 不回滚。

---

### 6.2 意外中断 / 关机

意外死亡没有机会执行正常暂停流程。

重启：

```text
process restart
    ↓
load latest Checkpoint
    ↓
replay durable EventLog tail
    ↓
inspect relevant real Environment
    ↓
reconcile uncertain actions
    ↓
reconstruct Context
    ↓
wake Root
```

不恢复过去控制流，只恢复现实状态。

---

## 7. UNKNOWN Action 与现实对账

典型情况：

```text
ACTION_STARTED
      ↓
real side effect happened
      ↓
power loss
      X
no ACTION_RESULT
```

重启后：

```text
Action = UNKNOWN
```

Runtime 不能自动判断成功或失败。

必须：

```text
inspect current Environment
    ↓
determine observable reality
    ↓
ACTION_RECONCILED
    ↓
EventLog
```

核心原则：

> 恢复的是与现实世界的闭环，而不是过去的 Python 控制流。

---

## 8. Actor 生命周期（MVP）

MVP 只有 Root，先支持：

```text
ACTIVE
WAITING
SUSPENDED
IDLE
```

定义：

- `ACTIVE`：当前正在承担执行活动；
- `WAITING`：等待环境事件、timer、process/tool 结果；
- `SUSPENDED`：被显式暂停；
- `IDLE`：当前无活动。

Root identity 长期存在，不随一次 Goal 或一次 Model call 销毁。

完整 Child Actor 生命周期留到正式 Execution System。

---

## 9. 完成验证

Root 拥有唯一最终提交权限，但：

\[
ClaimedCompletion \neq VerifiedCompletion
\]

流程：

```text
Root
 ↓
COMPLETION_CLAIMED
 ↓
ExecutionRuntime
 ↓
verify against current Environment
 ├─ fail
 │   ↓
 │ COMPLETION_REJECTED
 │   ↓
 │ wake Root
 │
 └─ pass
     ↓
   COMPLETION_VERIFIED
     ↓
   EXECUTION_COMPLETED
     ↓
   Root submit_final
```

完成必须以当前现实状态和明确验收证据为依据。

---

## 10. MVP 明确不实现

第一版禁止提前加入：

```text
Mind
Child Agent
recursive spawn
multi-agent communication
Blackboard
Agent Directory
Capability Search
tool ontology
multi-goal focus
resource economy
worktree isolation framework
global Planner
DAG executor
Swarm manager
Reviewer Agent
Method
Self-Cognition
Evolution
automatic actor retirement
complex locking / transaction / CRDT
universal plugin framework
```

若没有可复现失败，不增加机制。

---

## 11. MVP 成功条件

MVP 只有在以下条件全部成立后才算通过：

1. Root 能完成基础多步真实工具任务；
2. 每次行动都获得真实 Environment feedback；
3. EventLog 能重建执行主要事实；
4. State 与 Context 不依赖无限 transcript；
5. 正常 pause/resume 工作；
6. crash/restart 后能从 Checkpoint + EventLog + Environment 恢复；
7. 不要求恢复 Python 指令指针；
8. uncertain side effect 能进入 `UNKNOWN` 并重新对账；
9. Root completion 经过现实状态验证；
10. DecisionFrame 能重建模型当时实际可见的决策条件；
11. Runtime 不承担语义 planning；
12. MVP 不依赖 Mind、Child Agent 或多 Agent 机制。

---

## 12. MVP 核心定义

\[
\boxed{
Execution\ MVP
=
Persistent\ Root\ Actor
+
Programmable\ IPython
+
Event\text{-}sourced\ Runtime
+
Shared\ Environment
}
\]

MVP 验证的不是一个更强的 ReAct Harness，而是：

> Lumina 是否拥有一个持续存在、与现实环境闭环交互、可中断、可恢复、可审计的最小行动身体。
