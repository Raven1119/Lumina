# Lumina Execution System — 最终执行系统架构

> 状态：目标架构冻结稿\
> 定位：Lumina 持续把整体心智的 Intention 作用于现实世界的行动基座。\
> 设计来源：结合 Codex 的持续真实执行 Runtime、DeepSeek Harness 的 event sourcing / capability seam，以及 Prime Agent 的 programmable execution / recursive AgentProcess 思路，并遵循 Lumina North Star 的连续整体心智与递归自进化方向。\
> 当前文档定义目标架构，不代表所有机制立即实现。

当前认知链通过 `Execution/runtime.py` 将用户授权工作区、执行生命周期、指导投递与结果反馈归属 Execution；`Execution/organ.py` 仍是该链复用的单次运行 facade，也是独立 Execution API 的入口。Nervous 负责事件续接，没有中央 Host/Session。默认 `--goal` 保持同一个正式目标；显式 `--pursuit` 允许同一 Mind 在原授权范围内提出串行 Task，由 Execution 接受其不可变版本后执行。当前可运行范围与命令以 [认知链契约](../Mind/docs/INTEGRATED_CHAIN.md) 和 [Stage1 契约](../Mind/docs/INTENTION_STAGE1.md) 为准；下文的长期目标与能力规划不应直接视为当前实现。

早期单 Root MVP 设计已退出维护。其行动记录、状态折叠、Checkpoint、DecisionFrame、暂停恢复和完成验证职责，由本文件第 20–23 节、[恢复契约](RECOVERY_AND_WORKING_CONTEXT_DESIGN.md)及现役 `Execution/execution.py`、`Execution/organ.py` 承接。MVP 当时排除 Mind、Child 和递归的阶段限制不再适用于当前实现；原设计仍可从 [9d63da7 的文件](https://github.com/Raven1119/Lumina/blob/9d63da7311baa7611782cc8079fb09e9d81f4e25/docs/LUMINA_EXECUTION_MVP_ARCHITECTURE.md) 追溯，其历史验证结论保留在 [Mind 实验简史](../Mind/docs/EXPERIMENT_HISTORY.md)。

---

## 1. 定义

Lumina Execution 不是：

```text
Task -> Planner -> Executor -> Result
```

也不是一个固定：

```text
ReAct
DAG
TDP
Swarm
Manager / Worker
```

系统。

其目标定义为：

\[
\boxed{
Execution
=
Mind\text{-}guided
+
Event\text{-}sourced
+
Shared\text{-}world
+
Persistent\ Actors
+
Programmable\ Topology
}
\]

它是 Lumina 持续存在的行动身体。

Root / Child Agent 都不是独立人格，而是同一个 Lumina 整体心智下动态形成的局部认知—行动过程。

---

## 2. 总体架构

```text
                              Mind
                    persona / values / Intention
                         ↑                 │
             significant events           │ Intention
                         │                 ↓
                         │             Root AgentProcess
                         │            ↙       ↓       ↘
                         │         act      spawn      wait
                         │                    │
                         │       ┌────────────┼────────────┐
                         │       ▼            ▼            ▼
                         │    Actor A      Actor B      Actor C
                         │       │            │            │
                         │     spawn          act         spawn
                         │       ▼            │            ▼
                         │    Actor A1        │         Actor C1
                         │       │            │            │
                         └───────┼────────────┼────────────┘
                                 │
                     Programmable IPython Control Plane
                                 │
                        typed capability requests
                                 │
                                 ▼
                          Runtime Authority
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
          Shared Environment                Event System
                 │                               │
                 │                    ┌──────────┴──────────┐
                 │                    ▼                     ▼
                 │              low-level reflex      Root / Mind
                 │
                 ▼
             Blackboard
       intent / status / targets
```

底层始终存在：

```text
Append-only Execution Event Log
              ↓
          StateReducer
              ↓
        ExecutionState
              ↓
           Checkpoint
```

---

## 3. 单一自我与权力边界

### 3.1 Mind

Mind 是 Lumina 整体人格、价值观和全局 Intention 的唯一来源。

\[
\boxed{Mind\ owns\ Intention}
\]

Mind 负责：

```text
人格连续性
价值判断
整体目标
优先级
整体方向变化
是否形成新的全局目标
```

Mind 不负责：

```text
具体 Tool call
局部任务分解
spawn 数量
Child 微操
具体代码/文件操作
固定 Planner
```

---

### 3.2 Root AgentProcess

Root 是 Lumina 唯一持续存在的主执行 Actor。

\[
Root\ lifetime \approx Lumina\ Execution\ lifetime
\]

Root 不是 Planner。

它本身具备完整执行能力：

```text
observe
reason
act
spawn
wait
inspect
capability.search
return / integrate
submit_final
```

简单任务：

```text
Mind
 ↓
Root
 ↓
Tool
 ↓
Environment
 ↓
Root
 ↓
Final
```

复杂任务才按需产生 Child。

Root 唯一的结构性特权是：

```text
submit_final
```

Root 往往自然承担较高执行中心性，是因为 Child 结果最终向其汇聚，而不是因为 Runtime 预设 Root 为 Manager。

---

### 3.3 Child AgentProcess

Child 与 Root 使用同一种 AgentProcess 本体。

Child 是局部执行过程，不拥有独立人格、价值观或全局 Intention。

它可以：

```text
observe
reason
act
spawn
wait
inspect
capability.search
return
```

Child 可以继续递归产生 Child。

任何可能改变 Lumina 整体目标的新发现必须：

```text
Child
 ↓
Root
 ↓
Mind
 ↓
IntentionRevision
```

Child 可以自行形成 local subgoal / local Activity，但不能静默修改全局 Intention。

---

## 4. Activity：Intention 与 Actor 之间的最薄归属层

形式：

```text
Intention
   ↓
Activity
   ↓
AgentProcesses
```

Activity 只回答：

```text
这个 Actor 当前服务哪个目标？
这个 wait 属于哪个活动？
这个 Activity 当前是否 active？
是否 satisfied？
是否 suspended？
是否 abandoned？
```

Activity 不做：

```text
任务分解
DAG planning
resource scheduling
semantic coordination
```

第一阶段只实现单一 Activity。

多目标 / Focus 以后单独设计。

---

## 5. AgentProcess 生命周期

必须区分：

\[
Actor\ identity\ lifetime
\neq
runtime\ residency
\neq
Activity\ lifetime
\]

正式生命周期：

```text
CREATED
   ↓
ACTIVE
 ↙   ↘
WAITING SUSPENDED
   \   /
    IDLE
      ↓
   DORMANT
      ↓
   RETIRED
```

### 状态语义

- `CREATED`：Actor identity 已建立；
- `ACTIVE`：当前正在认知/行动；
- `WAITING`：等待事件、环境、Child、timer、tool；
- `SUSPENDED`：被显式暂停；
- `IDLE`：当前无 Activity，但 Runtime/session 仍驻留；
- `DORMANT`：身份与持久状态保留，昂贵运行资源释放；
- `RETIRED`：不再承担新工作，但历史 identity / Trace 永不删除。

### 转移规则

`IDLE -> DORMANT`：

由 Runtime 在：

```text
无进行中的 Activity
无 pending Event
无需要保活的 action
```

时机械执行，不需要 LLM/Mind。

`DORMANT -> ACTIVE`：

收到相关任务/事件或 Root 显式唤醒。

`DORMANT -> RETIRED`：

当前阶段只允许显式决定。

未来是否自动退休交给 Self-Cognition / Evolution。

---

## 6. Actor Directory

Lumina 是单一连续 Root，历史 Child 数量可能长期增长，因此不能让 Root 永远把所有 Actor 放在 Context 中。

需要最小 Actor Directory：

```text
actor_id
parent_id
status
current / last activity
local-state refs
last-active time
```

Root 按需：

```text
actor.search(...)
actor.inspect(...)
actor.wake(...)
```

核心原则：

\[
Actor\ exists \neq Root\ currently\ sees\ it
\]

Actor Directory 不是 Manager，只是可查询的持久身份目录。

---

## 7. Shared World：一个身体，一个现实

所有 AgentProcess 默认操作同一个现实环境。

\[
\boxed{Shared\ Body / Shared\ World}
\]

不是默认：

```text
Agent A -> worktree A
Agent B -> worktree B
```

而是：

```text
Agent A ─┐
Agent B ─┼→ Shared Environment
Agent C ─┘
```

因此 A 的真实修改立即成为 B 所处现实的一部分。

信息主路径：

\[
\boxed{Agent\ A \rightarrow World \rightarrow Agent\ B}
\]

优先于：

\[
Agent\ A \rightarrow Agent\ B
\]

即环境痕迹协作 / stigmergic coordination。

---

## 8. Git 的定位

Git 不表示整个 World，也不为每个文件额外建立 Lumina 修改日志。

Git 只是文件环境天然已有的 provenance / diff / commit / rollback 机制之一。

整体 World Projection 包括：

```text
filesystem
git
shell/process state
test/build outputs
external APIs
browser/tool state
Execution events
```

Execution Event Log 仍然是行为历史权威。

---

## 9. Blackboard

Blackboard 是共享执行协调状态。

它只表达：

```text
agent_id
current intention / activity
status
targets / touched areas
possible conflict
updated_at
```

例如：

```text
Agent A
  intention: 修改 parser
  targets:
    - src/parser.py

Agent B
  intention: 修 tokenizer tests
  targets:
    - tests/tokenizer/*
```

### Blackboard 不承担

```text
长期记忆
人格
价值观
历史事实
完整 Agent 内部状态
全局知识库
重要发现永久存储
```

历史事实属于 EventLog。

重要发现、警告或可能改变整体方向的信息走：

```text
Child
 ↓
Root
 ↓
Mind
```

---

## 10. Shared-world 冲突语义

共享世界默认采用乐观协作。

写入前 Runtime 可以做 Blackboard preflight：

```text
write request
    ↓
inspect relevant Blackboard entries
    ↓
possible conflict?
 ├─ no
 │   ↓
 │ execute
 │
 └─ yes
     ↓
   CONFLICT_NOTICE
     ↓
   Agent decides
```

默认：

\[
\boxed{detect + notify,\ no\ mandatory\ lock}
\]

不提前加入：

```text
global lock
transaction manager
CRDT
automatic merge framework
ownership economy
```

Agent 自己观察现实并调整。

---

## 11. AgentView

Agent 可以读取其他 Agent 的外部执行状态，但不直接读取其完整内部“大脑”。

稳定 AgentView 可以表达：

```text
actor_id
parent_id
goal / current activity
current focus
status
current action
recent world changes
touched artifacts
children
waiting_on
```

不默认暴露：

```text
完整 prompt
隐藏 reasoning
完整 IPython namespace
完整 mailbox
无限 transcript
```

Agent 内部认知保持局部隔离。

---

## 12. 默认通信路径

默认协调优先级：

\[
\boxed{
World\ observation
>
Blackboard
>
AgentView
>
Direct\ Message
}
\]

第一性信息流：

```text
Agent A
 ↓ Action
Shared World
 ↓ provenance / observation
Agent B
```

以及：

```text
Agent
 ↓ coordination intent
Blackboard
 ↓
other Agent
```

直接 Agent-to-Agent messaging 不是默认协作机制。

如果未来实验发现仅 World + Blackboard + AgentView 不足，再引入受限显式 messaging。

---

## 13. 递归 Actor 与可编程拓扑

系统不提供固定：

```text
TreeExecutor
DAGManager
SwarmManager
Planner
Reviewer
```

只提供局部原语：

```text
observe
act
spawn
wait
inspect
return
```

模型通过 IPython 自己组织：

```python
children = []
for hypothesis in hypotheses:
    children.append(await spawn(hypothesis))
```

也可以形成：

```text
linear execution
branching
parallel exploration
recursive tree
local DAG
join
backtracking
```

因此：

\[
\boxed{
local\ autonomy
+
recursive\ spawn
+
shared\ world
+
environment\ feedback
\rightarrow
emergent\ execution\ topology
}
\]

Tree / DAG / Swarm / ReAct 都是运行结果或策略，不是 Execution ontology。

---

## 14. IPython：Programmable Control Plane

每个 AgentProcess 可以拥有自己的 IPython working environment。

模型可以动态写程序组织行动。

但：

```text
IPython
!= Runtime
!= State authority
!= Agent identity
```

所有真实能力调用必须：

```text
Model-written Python
        ↓
IPython
        ↓
typed host capability
        ↓
Runtime validation / authority
        ↓
real action
        ↓
Event
```

崩溃后恢复外部真实状态，不恢复 arbitrary Python continuation。

---

## 15. Capability System 与 Tool Explosion

Lumina 可以拥有大量能力，但 Agent 当前 Context 中只暴露极少相关能力。

核心原则：

\[
\boxed{available \neq visible}
\]

整体：

```text
Capability Registry
        ↓
semantic discovery
        ↓
Agent-local Capability View
        ↓
IPython namespace
        ↓
Runtime Authority
```

Root 和 Child 都按需：

```text
capability.search(...)
capability.inspect(...)
```

不要求 Parent 预先知道 Child 所需所有具体工具。

Child 可以只收到：

```text
goal="检查论文实验条件"
```

随后自己发现：

```text
web
pdf
literature
```

### 权限原则

\[
\boxed{
discover\ broadly,\ execute\ under\ Runtime\ authority
}
\]

知道能力存在不等于被允许实际调用。

这避免：

```text
tool explosion
Root 退化成 tool router
Child 无限扩大现实权限
```

---

## 16. Perception / Context：按需感知

一个 Agent 理论上可以访问：

```text
Shared World
Blackboard
ExecutionState
AgentView
Event history
Capabilities
Children
Mind Intention
```

但：

\[
\boxed{accessible \neq currently\ visible}
\]

默认 Context 只包含：

```text
current Activity / local goal
relevant ExecutionState
incoming Events
local persisted state
directly relevant observations
relevant Blackboard conflicts
```

其他信息全部按需：

```text
inspect
search
observe
```

这与 capability discovery 使用同一注意力原则。

---

## 17. Root / Child 上下文连续性

Lumina 只有一个连续 Root，但：

\[
Root\ lifetime \neq Root\ transcript\ lifetime
\]

Root 连续性来自：

```text
Mind 中的人格 / 自我连续性
+
当前 Environment
+
外部 persisted state
+
ExecutionState
+
相关历史/事件
```

每次重新构造有限 Context。

Child 采用同样原则：

\[
\boxed{
Actor\ identity
+
persisted\ local\ state
+
current\ environment
\rightarrow
reconstructed\ context
}
\]

不依赖无限增长 transcript。

---

## 18. Event-driven Fast / Slow Loop

### Fast loop

普通执行：

```text
Environment
   ↓
Event
   ↓
Execution / AgentProcess
   ↓
Action
   ↓
Environment
```

用于：

```text
tool completed
child result
timer wake
ordinary failure
normal environment feedback
```

### Slow loop

当 Root 判断：

> 当前事件可能意味着 Lumina 的整体 Intention 应改变

则：

```text
Execution
   ↓
Root
   ↓
Mind
   ↓
IntentionRevision
   ↓
Execution
```

同时少数系统级重大事件可以直接：

```text
Event
 ↓
Mind
```

不必经过 Root。

因此不是两个高频嵌套 Agent loop，而是：

\[
\boxed{
continuous\ fast\ execution
+
sparse\ semantic\ Intention\ interruption
}
\]

---

## 19. 运行中事件：Steer / Follow-up

Agent 正在运行时，事件分两种处理语义。

### Invalidating / urgent event

如果事件使当前行动依据失效或必须立即改向：

```text
Event
 ↓
steer / interrupt current reasoning
 ↓
Agent adapts
```

### Ordinary event

普通新信息：

```text
Event
 ↓
mailbox / follow-up
 ↓
current action/turn boundary
 ↓
Agent handles
```

不为每种事件建立独立 loop。

---

## 20. 中断 / 重启

### 正常暂停

```text
stop issuing future actions
→ cancel safely cancelable work
→ settle in-flight actions
→ checkpoint
→ SUSPENDED
```

### 意外死亡

```text
restart
→ latest Checkpoint
→ EventLog replay
→ inspect real Environment
→ reconcile UNKNOWN actions
→ reconstruct Agent context
→ continue
```

不恢复 arbitrary Python 指令位置。

---

## 21. EventLog / ExecutionState / DecisionFrame

### EventLog

执行历史的不可变事实层：

```text
event id / sequence / time
execution / activity / actor identity
component / component version
event type
payload refs
source event refs
```

### ExecutionState

由 EventLog 推导出的当前行动状态。

### DecisionFrame

每次模型决策记录：

```text
model/version
Intention ref
Activity ref
ExecutionState ref
incoming Events
actual Context seen
available capabilities
environment observation refs
decision/output
```

以后 Self-Cognition 可以分析这些记录，但不能覆盖 Raw Event history。

---

## 22. 完成验证

Root 唯一拥有：

```text
submit_final
```

但：

\[
ClaimedCompletion \neq VerifiedCompletion
\]

Root 提出完成后，必须依据当前真实环境中的权威证据验证。

验证失败：

```text
COMPLETION_REJECTED
→ wake Root
```

验证成功：

```text
COMPLETION_VERIFIED
→ final submission
```

---

## 23. Runtime 与模块化边界

最终系统需要明确 capability seam：

```text
Model.sample
Tool.execute
Environment.observe

Execution.start
Execution.inspect
Execution.interrupt

Actor.spawn
Actor.inspect
Actor.wake

Capability.search
Capability.invoke

Blackboard.read
Blackboard.publish
```

但不要因此提前开发 Universal Plugin Framework。

原则：

\[
\boxed{stable\ semantic\ interface > generic\ plugin\ machinery}
\]

当未来 Evolution 真正需要替换模块时，再验证更强插件机制。

---

## 24. Self-Cognition / Evolution 接口

Execution 当前只负责两件事：

### 真实留下自己

```text
Environment
 ↓
Action
 ↓
Event
 ↓
Trace
```

确保未来能够忠实重建：

```text
模型看到了什么
当时世界是什么
使用了什么能力
谁采取了什么行动
行动造成什么结果
哪些过程产生/消失
```

### 能力可拆分

各模块通过 explicit capability boundaries 交互。

未来：

```text
Raw Execution Trace
       ↓
Self-Cognition
       ↓
Evolution
       ↓
candidate capability / Method / Reflex / policy
       ↓
evaluation
       ↓
promotion / rejection
```

当前 Execution 不自行修改自己。

---

## 25. 可能的长期能力分化

AgentProcess 不是系统最终本体。

一个反复出现并被证明有效的局部认知—行动过程，未来可能：

```text
LLM AgentProcess
       ↓
repeated validated pattern
       ↓
Method
       ↓
specialized program / model
       ↓
low-level reflex
```

因此长期研究对象不仅是 Agent topology，而可能是：

\[
\boxed{dynamic\ cognitive\ organization}
\]

执行轨迹拓扑只是这种组织方式的可观察行为表型。

---

## 26. 当前明确延后的问题

当前不提前冻结：

```text
多目标并存
Mind Focus 调度
复杂 Actor 自动退休策略
复杂 direct messaging
distributed workers
global locking
transaction framework
CRDT
resource economy
stable specialization promotion
Self-Cognition implementation
Evolution implementation
Evolution 是否可修改底层不变量
```

这些必须等待真实实验失败或后续阶段。

---

## 27. 最终核心不变量

当前目标架构的核心边界：

1. Mind owns persona, values and global Intention.
2. Execution owns action state.
3. AgentProcess owns local semantic execution.
4. Runtime owns mechanical lifecycle and authority.
5. Root is an executing Actor, not a Planner.
6. Child Agents are execution processes within one Lumina identity.
7. Shared World is the default reality for all Actors.
8. Blackboard carries coordination intent, not historical truth.
9. EventLog is immutable execution historical truth.
10. State, Trace and Context remain separate.
11. Agent perception and capability access are on-demand.
12. Capability discovery is broad; execution remains under Runtime authority.
13. Shared-world conflicts are detected and surfaced, not automatically locked.
14. Global Intention changes must go through Mind.
15. Some system-level events may directly trigger Mind.
16. IPython is a programmable control plane, not state authority.
17. Arbitrary Python continuation is not required for crash recovery.
18. Actor identity lifetime is distinct from compute/activity lifetime.
19. ReAct, DAG, TDP, Swarm and Multi-Agent are strategies/topologies, not substrate ontology.
20. Completion must be verified against reality.
21. Raw execution facts must remain suitable for future Self-Cognition.
22. Current architecture is evolution-ready, not evolution-implementing.

---

## 28. 最终定义

MVP：

\[
\boxed{
Persistent\ Root\ Actor
+
Programmable\ IPython
+
Event\text{-}sourced\ Runtime
+
Shared\ Environment
}
\]

最终系统：

\[
\boxed{
Mind\text{-}guided\ Persistent\ Actor\ Runtime
+
Shared\ World
+
Blackboard
+
Recursive\ Programmable\ AgentProcesses
+
On\text{-}demand\ Capabilities
+
Persistent\ Causal\ Trace
}
\]

最终目标不是构造一个更复杂的 Harness。

而是：

> 为 Lumina 提供一个持续存在的数字身体：整体 Mind 保持人格、价值观和 Intention 的连续性，Execution 将这些 Intention 持续作用于一个真实、变化的世界；局部 AgentProcess 可以动态产生、休眠、恢复、递归和重新组织，复杂执行结构由局部认知、共享环境、事件反馈和未来进化共同形成。
