# Lumina：中断恢复与任务工作上下文生命周期

> 设计与执行依据 · 2026-09-10
> 建议存放：`docs/RECOVERY_AND_WORKING_CONTEXT_DESIGN.md`
> 源码核对点：`Execution_lab2` / `1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7`
> 状态：本设计的 R／E／M 实现与受界验证已于 2026-09-10 完成；摘要仍为可选项。下文保留实施前的源码事实与设计依据，当前行为见 [运行契约](../Mind/docs/INTEGRATED_CHAIN.md)，验证范围与剩余限制见 [CURRENT_STATUS](CURRENT_STATUS.md)。

## 1. 目标、现状与复用决策

### 目标和边界

在现有 Mind–Nervous–Execution 上，完成一个可继续开发的纵向闭环：**能够在明确边界暂停，重启后复用已知结果，保留当前目标所需的理解，并准确决定下一步。**

Mind 的目标是认知连续性和宏观判断：仍理解当前目标、局势、关键依据、条件与未知，能够用新观察修正旧判断。Execution 的目标是操作连续性：知道实际执行到了哪里、哪些结果已知、哪些动作尚未发出，以及现在具备什么执行条件。

这是各器官自有的、服务于当前目标的工作上下文。持久到磁盘是为了跨暂停和重启，不因此归属 Memory Organ。不接 Chat、Conversation Memory 或 Dream，不做跨任务经验学习、人格系统、多目标调度、OpenRouter 或数据库迁移。

北极星要求连续整体心智、独立判断和从经历中改变自身。此轮将其落到两个可验证目标：**换窗后判断仍有依据；恢复后行动不丢位置、不把未知当成功。** 不把保存文本等同于已经实现人类认知。[L1]

### 已核对的基础

| 位置 | 已有实现 | 本轮处理 |
| --- | --- | --- |
| `Nervous/organ.py`, `storage.py` | 单 writer；事件身份与因果关系；ack 与后继事件同次提交；临时文件、文件 fsync、原子替换 | 复用，不创建新的总控宿主或事件总线 |
| `Nervous/provider.py` | 调用前保存 reservation，调用后保存 response，保留成本和错误 | 补强调用身份与结果认领，供恢复及压缩共用 |
| `Mind/cognition.py`, `trace.py`, `model.py` | 认知增量提交；activity/native 日志；若干已知结果断点可无新采样恢复 | 保持语义，检查双份调用记录之间的恢复交接 |
| `Execution/execution.py`, `organ.py` | EventLog、确定性 fold、带日志前缀校验的 checkpoint；动作开始/结果分开记录 | 补齐已知但未完成收尾的后缀；未知副作用继续停住 |
| `Execution/runtime.py`, `model.py` | 指导、观察、outbox、运行状态；最近最多 6 个、合计不超过 60,000 字符的原生轮次 | 在旧轮次被淘汰前形成工作交接，不替换动作状态 |
| `Mind/cli.py` | `start/resume/status`，器官装配与前台推进 | 补可用的暂停路径和无副作用的状态查看，不接前端 |

以上是源码事实，不代表任意断点均已可靠恢复。[L2–L8]

当前两个明确的待验证恢复缺口是：`ExecutionHistory.attempt()` 遇到已保存的工具调用响应会停在 `execution_response_pending_owner_commit`；`RootAgentProcess` 对普通动作的裸 `MODEL_DECISION` 日志尾部仍拒绝构造。它们应先有失败回归，再修复。**但“未发出动作”不足以证明重启后原动作仍可直接执行：IPython 变量、文件版本和指导适用性可能已经改变。**[L4–L6]

当前 lifetime 上限还包括 Nervous 的 256 个事件、Mind 的 64 次 activation 和 4 MiB cognition journal。它们与模型上下文容量不同。本轮不通过反复换窗重置这些计数，也不承诺无限期运行。[L2,L3]

### 参考项目：具体采用什么

| 参考 | 可核实的依据 | 本轮复用范围 |
| --- | --- | --- |
| **Kimi CLI**，固定提交 `86f136422a0aae6b217ea49e7ea1d2e8a1defcd2` | `src/kimi_cli/soul/compaction.py` 中 `should_auto_compact`、`SimpleCompaction.prepare/compact`；有对应测试；Apache-2.0 | **源码级移植小型压缩核心及适用测试**，替换消息和 provider 适配；不引入 Kimi/Kosong runtime。[R1–R3] |
| **DSH**，`packages/core/session/src/surface.ts` | append-only 日志与模型可见 surface 分离，替换投影不覆盖原始历史 | 借用投影边界；不搬 Session、插件体系或 TypeScript 服务。[R4] |
| **LangGraph Functional API / persistence** | 已保存任务结果用于恢复；重放要求确定性和副作用幂等；thread 状态与跨 thread store 分开 | 借用恢复原则和短期状态边界；不引入 LangGraph。[R5] |
| **Temporal Activity Execution** | 取消是一个过程；超时与重试由明确策略管理，不能把超时当作动作没有发生 | 借用暂停、取消和未知结果的区分；不引入 Temporal。[R6] |
| **The Complexity Trap**，arXiv:2508.21433v3 | 在所测 SWE-agent / SWE-bench 设置中，简单 observation masking 是强基线，摘要并非稳定更优 | 验证保留简单屏蔽基线，不先验宣布摘要有收益；不复制整套实验 harness。[R7] |

Kimi 不能原封不动直接 import：其摘要输入只收集 `TextPart`，尾部按 user/assistant 消息计数，token 粗估采用字符数除以 4；这些不直接适配 Lumina 的原生工具块、中文及整个请求预算。移植时应明确记录这三处修改。摘要调用保持无业务工具权限。上游默认提示词中的“删除失败尝试”也要改为保留仍影响当前工作的失败条件及来源。[R1,R2]

复用交付需记录固定提交、原文件路径、移植函数和差异，保留适用版权、完整 Apache-2.0 许可证及上游 NOTICE（若存在）。只写“参考 Kimi”不能算完成源码复用。若固定源不可取得或依赖边界与核对结果不同，记录证据，使用最小等价实现，不为复用几个函数引入整个框架。[R3]

## 2. 工程设计

### 总体结构：既有 owner，两个工作投影

```text
持久事实 / 当前权威状态                每次调用的派生 Context
──────────────────────────           ──────────────────────────
Mind cognition + activity evidence  → 当前认知 + 背景摘要 + 当前事件/证据
Execution log + state + environment → 当前操作状态 + 交接摘要 + 近期完整轮次
                 ↕ Nervous：事件、暂停续接、调用账本
```

保留 `Trace != State != Context`。摘要是派生上下文，不能修改认知真值、动作结果、指导回执、事件确认或授权。Nervous 继续只负责机械推进；共享的压缩函数不拥有全局生命周期。

首先复用现有 owner、存储原语及测试接缝。字段名、文件拆分、内部类名由实现者决定；以下契约比示例结构优先。不要新增通用 Manager、Coordinator、认知评审 Agent、摘要树或向量检索服务。

### 中断与恢复：先认清发生过什么

#### 恢复顺序与三种结果

打开状态时，先恢复和校验持久记录，再重建模型输入；**不得先用今天的上下文调用模型，再判断昨天是否已经得到结果。** 恢复过程应区分：

| 结果 | 行为 |
| --- | --- |
| 可确定续接 | 复用已知响应、提交或回执；不再次采样，不重做已完成动作 |
| 需要重新决定 | 原动作明确未执行，但环境、指导或内核已变化；保留旧尝试，并以新决策身份重新判断 |
| 需要核对 | 动作或调用结果未知、日志不完整、身份或完整性冲突；停止新副作用，保留可检查的证据 |

“重新决定”与“结果未知”必须分开。前者在权限未变时可由所属器官继续处理，后者不能通过猜测填成成功或未执行。

#### 复用已保存的 provider 响应

在现有调用账本中建立稳定关联：所属器官、目标/Run、activity/decision、阶段/尝试、用途与原始请求摘要。能从既有数据推导的字段不重复保存。

```text
保存带身份的原请求 / reservation
→ 调用 provider
→ 保存实际 response
→ owner 校验并认领 response
→ 保存逻辑提交 / DecisionFrame
```

恢复时，只认领身份、原请求、工具协议和尝试链均匹配的记录。使用**已冻结的原请求**解释已保存响应，不要求重新拼装的当前 prompt 恰好等于旧 prompt，更不能把新 prompt 和旧 response 拼在一起伪造历史。

为 Execution 补齐 `response 已持久化 → MODEL_DECISION 尚未写入`：通过当前确定性解析器恢复原 DecisionFrame，至多认领一次。全局调用账本不新增一次费用，也不重新调用 LLM。现有协议纠正尝试仍属于自己的 attempt，不与原调用混淆。

Mind/Analysis 同样检查“全局账本已有 response，局部 native/turn 记录尚未保存”的交接。能通过明确调用身份恢复才恢复；多条候选、来源冲突或只有 reservation 时不得按时间或文本相似度猜配。无需重写它们已有的恢复状态机。

没有可认领的响应时保留 UNKNOWN。显式重试创建新 attempt/activity 并累计费用，不能伪装为原响应；有可靠的 provider 幂等/查询能力后再另行扩展。

#### 恢复已经提交但尚未开始的动作

为 `MODEL_DECISION 已提交 → ACTION_STARTED 尚未提交` 补齐后缀。记录中的完整动作、原始模型响应、call ID 和决策位置应直接复用；不能重新采样“同一个决策”。

**恢复 DecisionFrame 与允许 dispatch 是两个步骤。** dispatch 前检查当前适用性：授权未撤销、未有待处理的相关新指令、引用的工作区状态仍适用、执行环境可用。沿用现有过期指导重评通路，不能为了恢复绕过“新用户事件先到 Mind”。

恢复矩阵：

| 持久边界 | 默认行为 |
| --- | --- |
| 已知 response，尚无 DecisionFrame | 认领原响应、恢复解析和提交，再检查动作适用性 |
| 已有 DecisionFrame，动作未开始且运行条件有效 | 继续同一已保存动作；不新增模型采样 |
| 动作已开始，结果尚未知 | 保留 UNKNOWN，不盲重放 |
| 动作结果已保存，事件反馈或确认未完成 | 重放持久回执/后继事件，不重做动作 |
| 已有 Wait / completion 等控制事实，收尾未完成 | 按既有状态机补齐可证明的控制后缀；不伪造外界到达或业务验收 |

同时检查构造函数、`resume()` 与 `_resume_sibling_suffix()` 的契约一致性。不要只删除构造函数的拒绝分支，就宣布全部后缀已支持。

**IPython 冷恢复的特别规则：**上下文换窗不应重启内核；进程重启则用显式的 `kernel_epoch` 或等价事实标记命名空间已经丢失。原动作引用内核变量时，即使尚未 dispatch 也不能无条件执行。首版对无法证明可独立续行的冷恢复 Python 单元，选择“明确未执行的旧计划已过期 → Execution 根据当前环境重新决定”，无需人为逐个批准普通局部修复。

部分 IPython sibling 已运行时，不自动重放整批，也不默认余下单元不依赖已丢变量。保留已知结果，对尚未执行部分重新决定；任何已经开始且结果未知的单元继续阻断。过期的未执行计划用可恢复的 owner 记录收尾，引用原决策及实际原因；不能伪造成功 tool_result，也不能重启后再次捡起同一过期后缀。跨重启必须保留的产物使用授权工作区文件，不增加通用变量 pickle、虚拟机快照或代码静态安全证明系统。

可复用已有 typed Write 的对账，但它证明的是“当前文件达到已声明的目标内容”，不能证明任意脚本只执行过一次。当前主链使用 IPython，不能将底层 typed Write 的能力误报成所有 Python 写入都可对账。

#### 正常暂停：停后续 dispatch，保留在途事实

首版完成 CLI 前台的合作式暂停及显式 `resume`，不做后台守护进程或跨进程调度 API。

第一次 Ctrl-C / 等价暂停请求应关闭后续模型和业务动作的 admission。在途调用有已知返回时先保存返回；尚未完成的认知活动、咨询或动作后缀可停在它们自己的合法边界，不必为了暂停强迫 Mind 输出 NoChange。

信号处理器只设置轻量停止标记；由现有 writer 在安全位置保存暂停事实，避免信号处理器重入锁或执行文件事务。若底层调用无法及时取消，说明仍在等待结果或已进入未知状态，不能先显示“安全暂停”再放任后台动作继续。

强制终止是 crash 路径。中断 Python 单元不保证回滚文件副作用；内核结果不明时留在 UNKNOWN。正常暂停可以在下一次显式 resume 时承接；暂停本身不会消费业务 Wait、不改变 Intention、不清空预算。

Nervous 保存机械暂停信息，各器官保存自己的阶段。CLI 仍只装配和转交请求。无需新增一个集中存储整个闭环状态的 Session。

#### 状态查看与存储约束

`status` 必须能够报告：已知恢复位置、暂停/等待/失败原因、未确认事件、未知动作引用及累计成本。它不应启动模型、启动内核、推进恢复状态或修改日志。尤其不能因为 Actor 有不支持的尾部，就让所有状态查看一起失败。

继续使用单 writer、原子替换、append-only 日志、完整性校验和 checkpoint。补做必要的读写失败回归；检查 POSIX 原子替换后的父目录同步，在支持的平台通过共享原语实现，Windows 保持明确的平台契约。不要把文件 fsync 夸大成已经覆盖全部断电/硬盘故障。

遇到半行、截断或损坏的 canonical 日志，允许提供完整前缀的诊断视图，但不得静默删掉尾部后继续副作用。已确认只是派生缓存的损坏才可从有效原始记录重建。本文不要求构建通用日志修复器或灾备系统。

### 上下文生命周期：一个小压缩器，各器官各一份滚动摘要

#### 最小状态与触发

每个 owner 保存一份当前工作摘要。实现结构可接近：

```python
{
    "scope": "task / organ / applicable run",
    "revision": 3,
    "summary": "有界的任务工作交接文本",
    "covered_until": "已覆盖的完整历史边界",
    "source_refs": ["可回查的原始记录"],
    "input_digest": "本次压缩输入的摘要",
    "call_ref": "已落盘的压缩调用"
}
```

`scope` 防止混入其他目标或 Run；`covered_until` 由 owner 决定，不让摘要模型随意宣称已覆盖；长来源清单可保存在工件中，模型只见有界目录。不存在的摘要等价于尚未压缩，可兼容当前 baseline 的有效状态。

默认不增加每轮总结调用。仅当下一请求将触及配置预算，或旧轮次即将因窗口规则被淘汰时，在**完整已结束片段**边界压缩：

```text
上一份摘要 + 本次新退出的原始片段
→ 一次有界、无业务工具的摘要调用
→ 保存新摘要
→ 摘要 + 保留的近期原文 + 当前权威状态
```

首版以压缩后保留最近 6 个完整轮次为初始配置，不能继续把“每轮硬裁成 6 轮”作为唯一历史策略。允许原生尾部在有界预算内增长，达到高水位后批量压缩、回落到保留量，例如在预算允许时由 12 轮回落到 6 轮；这些是待验证的工程初值。不要变成第 7 轮以后每轮调用一次摘要器，也不能先丢掉未覆盖片段。对照实验固定有效请求预算、模型和任务，并单独记录窗口策略的变化。

不能先从 `ExecutionHistory.history()` 或 `committed_tool_calls()` 只拿已经裁成 6 轮的数据，再声称压缩了更早历史；补一个有界范围读取，从 EventLog/DecisionFrame 提取尚未覆盖的实际动作和结果。不要将每个 DecisionFrame 中重复嵌入的整个 request history 再次递归总结。

使用 Kimi 的阈值逻辑，但预算按**最终实际请求**核算：system、tools、状态、摘要、所有原生块、证据、指导及预留输出都在内。保留现有 request bytes 上限。优先采用匹配 tokenizer 或 provider usage 校准；没有精确 tokenizer 时使用标明性质的保守估计，不声称字符数除以 4 能保证中文安全。[R1]

提前留出一份有界工具结果、一次摘要和最终判断所需空间；压缩后应有有效余量，不能在同一边界无变化地循环总结。无法在预算内保留必要信息时，进入明确的容量暂停，不静默裁掉关键字段后继续。

#### 摘要自身也需要可恢复提交

```text
固定 scope / 历史切点 / 旧摘要 revision
→ 保存压缩请求身份
→ 调用现有 provider
→ 保存摘要 response
→ 校验形状、长度、来源范围及版本
→ owner 原子提交摘要与 covered_until
→ 后续请求使用新投影
```

压缩中的崩溃与普通调用遵循同一恢复原则。response 已知时直接认领；候选未提交时旧投影继续有效；提交后不能重复覆盖同一区间。新到事件保留在原队列中，压缩不确认它们，也不把它们纳入已经冻结的覆盖范围。

若任一在途业务调用已冻结了输入，则该调用的原 wire 与所用 context revision 保持不变；不能在其恢复时悄悄采用新摘要。只影响未来新调用。

全局 provider 账本区分 `purpose=compaction` 与业务决定。**现有 ExecutionHistory 会解析 Execution 调用的状态字段；摘要记录不能直接混入它的动作历史扫描。** 可用独立计费用途或等价明确过滤，调用仍归属原器官，不形成第四个判断者。[L5]

没有工具不等于没有错误风险。摘要结果仅做机械校验；不新增 LLM Reviewer 来声称保证语义保真。摘要不足、截断、空返回、越界引用时不切换投影；旧投影若已放不下，则容量暂停。未知摘要调用也保留原 attempt，显式重试才能新增采样，费用不消失。

#### Mind：保留认知，整理背景

最终输入采用：

```text
原始目标 / 当前有效约束与授权
+ 当前 accepted cognition
+ 截至某一历史边界的背景摘要
+ 当前事件、相关原文证据、当前 Execution 投影
+ 同一 activity 内的原生咨询续接
```

**不增加第二套宏观状态数据库或注意力管理框架。** 现有 cognition 继续承接正式判断；摘要只保留旧活动的脉络及未进入正式认知、但仍对当前目标有用的背景。

Mind 摘要要求集中在：当前局势为何发展至此；此前方向与改变它的依据；仍影响判断的条件、竞争解释和未知。已结束的局部行动细节只保留关键结果与引用。不要把假设、计算、执行者自述或旧 Mind 判断改写为已验证现实。

摘要放在明确标注“派生历史背景”的独立字段中。不得以原始 source 的同一 ref 写入不同摘要文本，不得让摘要本身成为 supported belief 的独立证据。新的重要判断仍从原始证据或当前已接受认知出发；需要原文时通过现有 `read_evidence` 路径回查。

证据全文与模型可见投影可以分开：长历史证据默认显示稳定引用、长度及必要的原文片段，精确原文留在现有 source store。不得把摘要替换原文存进来源表，或只缩短一处显示却在另一个 payload 再塞回全部原文。最新事件、任务授权和仍然生效的指导不能因摘要而降格或扩权。

Mind 当前 `current` 会退休未选择的活动认知。**禁止把它解释为“本轮暂不显示”。** 保留现有语义。接近 16k 认知状态上限时，在正常认知输入中给出剩余容量，允许 Mind 用现有 `updates/current` 显式缩短、合并或退休已经过时的条目；这属于可追溯的认知修订，不由压缩器暗中完成。[L3]

若仍有效的认知和必要证据无法放入预算，保持容量暂停。本轮不以隐藏删除、每次活动清空、无限增加上下文或新建认知检索系统回避上限。普通 activity 内继续沿用现有最多 6 次原生调用和增量 source 返回；先治理跨 activity 背景，不另建 activity 中途的第二套状态机。

#### Execution：精确状态之外，补早期工作交接

最终输入采用：

```text
原始任务 / 当前适用指导
+ 真实 ExecutionState、未决动作、当前环境及内核重建事实
+ 工作交接摘要
+ 最近完整工具轮次
+ 下一步需要的精确证据
```

Execution 摘要要求集中在：已实际做过什么及结果；失败尝试的条件；重要产物与精确结果位置；尚未完成的局部工作。可以有下一步意图，但它只是待核对的计划，不是重启后必须自动执行的命令。

动作是否完成、哪条指导已投递、哪次 Wait 被满足，只取自现有执行日志与 owner 状态，绝不取自摘要。路径、参数、数值、单位、文件版本等决定下一步正确性的内容，要么逐字保留，要么从原始引用重新读取。

保留近期完整 tool_use/tool_result 配对，压缩边界不能切开 sibling 批次或未决调用；保留原生 thinking/signature 所需结构，不伪造或截断受协议约束的块。压缩不会计作一个业务 decision，不会重启 Python 内核，也不会清空尚未消费的用户事件或指导。

#### 最小回查能力与输出外置

补一个**当前目标内、只读、有界的历史片段读取能力**即可，不做向量数据库、跨目标搜索或独立分析服务。提供有界目录及事件/决策范围，让模型知道哪些片段可查；只保存一个已经退出上下文的未知 ID 不足以构成回查。

Mind 复用 `read_evidence` 及 Nervous 的请求/结果通路；Execution 回查自己的历史不应每次唤醒 Mind。优先保留现有 `IPython + Wait + ClaimComplete` 工具外形，采用最小只读 helper 或经过筛选的只读历史投影。不得把 provider 账本、其他器官私有目录或完整 state 根目录挂进执行容器。具体传输方式由 Astra 在现有 sandbox 接缝中选择。

历史片段来自 owner 已保存的动作/结果；模型请求里的重复上下文、凭据及不属于本目标的数据不作为可读历史导出。必要时可复用同一片段 DTO 给 Mind 查询，不为此建立通用观察框架。

当前 DockerIPython 已在输出捕获层限制 stdout，截断后的剩余内容不一定仍可取得。回查必须保留 `truncated/original_output_chars`，不能把已经丢失的尾部说成“原文可展开”。若验证任务确需完整输出，只增加受限的工具结果落盘：先保存有界正文，再给引用；超过存储限额明确报告，不构建无限 stdout 仓库。[L7]

### 持久状态与恢复点的保留

当前运行的日志、调用结果、未决动作和摘要来源需要在该任务的恢复期内保留；它们与已经退休的实验原始 Trace 不同。本轮不恢复旧实验目录、不重写旧失败结论。

缺少新增摘要字段的现有有效 state 应能按“尚未压缩”打开；当前 schema 改动需要最小显式迁移或兼容读取。不要为了功能开发破坏用户已建立的 baseline 恢复点，不做 V1–V72 的普遍兼容。

上下文窗口容量、持久日志容量、任务调用预算分别计量。保持现有硬上限并可见报告，测试可在隔离配置中降低压缩阈值来反复触发整理；不因新机制增加内部事件就悄悄清除去重身份、重置 activity 计数或放大生产预算。

## 3. 实施、验证与交付

### 工作方式与授权范围

先读 root/局部 AGENTS、`docs/NORTH_STAR.md`、CURRENT_STATUS 和实际入口。发现并使用适用的 codebase-design、TDD、code-review 或等价 skills；没有同名 skill 不构成停工理由。

按以下三个纵向切片推进。每片先验证再进入下一片，必要的内部调整由 Astra 自主判断。本文不限制文件数量，也不要求照抄示例类名；更简单的等效实现可以采用，但要保留契约并记录偏离原因。

| 切片 | 最小交付 | 进入下一片的条件 |
| --- | --- | --- |
| **R：恢复补强** | 稳定调用认领、已知后缀恢复、合作式暂停、只读状态查看、冷内核边界 | 新断点回归先失败后通过；未知副作用不重放；现有核心回归不退化 |
| **E：Execution 工作交接** | 移植 Kimi 小压缩核心；一份摘要；完整近期轮次；有界回查；压缩自身可恢复 | 多次压缩与重启后不重做动作、不丢操作依据；与简单屏蔽基线比较 |
| **M：Mind 背景整理** | 相同压缩核心、不同摘要要求；当前 cognition 保留；背景/证据投影有界 | 跨活动与换窗仍保留关键条件及未知，新证据可以推翻旧摘要中的判断 |

本任务授权修改这些切片必需的器官、存储/调用适配、CLI、测试和文档；不授权重写 Git 历史、覆盖用户数据、迁移运行模型或接入未列功能。commit/push 等继续遵循项目已有明确授权。

### 验证必须证明什么

机械正确性先用注入模型、故障注入及真实临时磁盘完成；不能只检查异常被捕获。至少有子进程强制退出后的重新打开，不能全部靠同一对象抛异常再继续。

| 场景组 | 关键断点/情形 | 必须成立的断言 |
| --- | --- | --- |
| **恢复与暂停** | response 后、DecisionFrame 后、动作前后、回执确认前后；在途暂停；冷内核；损坏尾部 | 不新增已知调用费用；不重复已完成动作；新指令不被绕过；未知动作停住且可查看；pause 不伪造 NoChange |
| **压缩事务** | 摘要响应后提交前、摘要切换后、压缩期间新事件到达；原生 sibling 配对；无效摘要/预算不足 | 不丢覆盖区间；重启不重复总结已提交区间；不改旧 wire；新事件仍待处理；不混淆摘要和动作调用 |
| **认知与续行** | 至少三次压缩＋一次重启；早期约束、条件性失败、精确参数、旧判断被新证据否定 | Mind 保留不确定性和宏观目标；Execution 精确续行；旧摘要不能压过当前原文；超限不静默丢关键状态 |

在有真实模型参与的验证中，采用同一任务、同一模型配置、同一历史前缀和独立工作区快照；摘要只能看到当时已有内容，不能看未来答案。对比当前基线、简单旧结果屏蔽、滚动摘要。先在少量固定案例测“恢复后的下一次判断/动作”，再用一个可审计的完整任务检查多轮组合效果；不为比较引入永久 campaign 框架。

报告任务正确性、早期约束保留、重复工作、精确参数错误、未知变确定的错误，以及总 token、调用和耗时。成本包括摘要和回查。脚本响应证明协议，真实模型证明这些案例中的行为，少量案例不支持普遍优势结论。[R7]

真实调用继续使用项目已配置的 `deepseek-v4-pro` 与原官方接口。先完成免费机械回归，再做有界真实验证；本轮建议上限为新增 **64 次 provider 调用、1,000,000 reserved output tokens**，汇总所有角色和摘要，不自动购买服务或扩额。此为实验控制上限，不修改生产默认配额；若项目已有更严格授权，以更严格者为准。到限完成可做部分并给出真实证据，不伪造统计充分性。

恢复修复可独立晋升。摘要机制在机械测试通过后可以作为显式实验选项交付；只有目标案例满足预先记录的正确性条件且无新增关键退化，才启用默认。若简单屏蔽已经满足需求且更合算，保留更简单默认，记录摘要为何未晋升，不为“完成方案”强行开启。

### 文档和开发基线

完成实现后更新 `docs/CURRENT_STATUS.md`、`Mind/docs/INTEGRATED_CHAIN.md`，以及实际受影响的架构说明与 root/局部 AGENTS。AGENTS 只保留新 owner 边界、使用入口和验证命令的简短指引；本文件承载设计细节，实验结论文档承载结果。删除冲突或失效说明，不把计划写成已实现。

增加一份简短实施记录，包含：实际 HEAD、复用来源及修改、各切片变更、默认启用状态、测试与真实调用结果、恢复所需目录和剩余限制。执行当前核心回归，再执行维护中的全库回归及可用 Docker smoke；无法运行的环境测试明确列出，不把历史结果当本轮结果。

**完成标准：**现有 baseline 可被保留；已知响应和动作后缀有明确恢复路径；未知副作用继续受保护；两个器官各有可恢复的轻量工作上下文；观察和摘要都可追溯；没有重新引入 Host，没有跨入 Memory Organ，也没有为了压缩换掉模型或执行体系。

### 来源索引

下列源码链接用于核对实现，外部文献只支撑相应机制和比较边界。第 2 节是针对 Lumina 的设计提案，不是声称上游已经实现全部组合能力。

**Lumina 固定源码快照**

[L1] [NORTH_STAR.md](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/docs/NORTH_STAR.md)；[AGENTS.md](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/AGENTS.md)。

[L2] [Nervous/organ.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Nervous/organ.py)；[storage.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Nervous/storage.py)；[provider.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Nervous/provider.py)。

[L3] [Mind/cognition.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Mind/cognition.py)；[trace.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Mind/trace.py)；[model.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Mind/model.py)。

[L4] [Execution/execution.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Execution/execution.py)；[organ.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Execution/organ.py)。

[L5] [Execution/model.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Execution/model.py)，尤其 `ExecutionHistory.attempt/history` 和 `ExecutionModel.decide`。

[L6] [Execution/runtime.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Execution/runtime.py)，尤其 `open_actor/preserve_unknown_action/advance`。

[L7] [Execution/sandbox.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Execution/sandbox.py)；[evidence.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Execution/evidence.py)。

[L8] [Mind/cli.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Mind/cli.py)；[organ.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Mind/organ.py)；[analysis.py](https://github.com/Raven1119/Lumina/blob/1ca2fce248c5b7cac6ef57cb0218e5f7f09086a7/Mind/analysis.py)。

**外部实现与文献**

[R1] Kimi CLI：[compaction.py](https://github.com/MoonshotAI/kimi-cli/blob/86f136422a0aae6b217ea49e7ea1d2e8a1defcd2/src/kimi_cli/soul/compaction.py)。核对 blob SHA：`177c5edce899ccd31d111aeb7e0409dde64f3a07`。

[R2] Kimi CLI：[compact.md](https://github.com/MoonshotAI/kimi-cli/blob/86f136422a0aae6b217ea49e7ea1d2e8a1defcd2/src/kimi_cli/prompts/compact.md)；[test_simple_compaction.py](https://github.com/MoonshotAI/kimi-cli/blob/86f136422a0aae6b217ea49e7ea1d2e8a1defcd2/tests/core/test_simple_compaction.py)。测试 blob SHA：`b2fa80dbb342b1e8b10eb542525cefef837c61bd`。

[R3] Kimi CLI：[LICENSE](https://github.com/MoonshotAI/kimi-cli/blob/86f136422a0aae6b217ea49e7ea1d2e8a1defcd2/LICENSE)。

[R4] DSH：[surface.ts](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/surface.ts)。2026-09-10 核对 blob SHA：`5d8ce74fe2461cb2f777a7bc7556795f337f0c03`；此链接为活动分支，仅作机制参考。本轮未决定复制 DSH 源码；若新增复制，先固定提交并核对相应许可证。

[R5] LangGraph：[Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api)；[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。

[R6] Temporal：[Activity Execution](https://docs.temporal.io/activity-execution)，尤其 Cancellation 与超时/重试语义。

[R7] Lindenbauer et al.：[The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management](https://arxiv.org/html/2508.21433v3)，2025；[作者代码](https://github.com/JetBrains-Research/the-complexity-trap)。
