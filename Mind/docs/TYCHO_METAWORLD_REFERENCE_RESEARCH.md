# Tycho / MetaWorld reference research

Date: 2026-09-05. Research note only; no implementation or production promotion.

## Recommendation

**Mind 的长期世界理解方向更应参考 MetaWorld；当前可移植的建模与验证机制继续以 Tycho 为主要源码参考。** 这是一项分层的研究判断，不是 MetaWorld 性能优于 Tycho 的实验结论，也不替代 Lumina 自身的 Mind 架构。

如果问题是“Mind 要理解什么”，MetaWorld 对参与者、目标、环境变化和不确定性的关注更贴近 Lumina；如果问题是“现在根据谁的实现写代码”，Tycho 的可审计源码、可执行假设和验证边界更有依据。不能把开放源码等同于认知适配，也不能把产品愿景等同于已验证能力。

| 比较维度 | Tycho | MetaWorld / Decitron |
| --- | --- | --- |
| 已公开的问题设定 | ARC-AGI-3 中从交互建立任务局部模型 | 不确定条件下的复杂情景、多主体决策 |
| 世界表示 | 任务自定义潜在状态及可执行转移/观测/结果函数 | 官方描述的显式情景状态与演化；实现未审计 |
| 对 Mind 最有价值的部分 | 按需建模、可反驳预测、回放诊断、独立结果验证 | 参与者/目标/约束/环境，以及不确定判断的修订 |
| 当前复用依据 | 论文、官方 Apache-2.0 源码和公开轨迹 | 官网描述、镜像作者摘要；本次未找到目标源码 |
| 主要移植风险 | 把全部认知强制变成可执行模拟器；混入规划/执行权限 | 把未经校准的推演当成现实；照搬庞大博弈系统 |

下文分别记录证据、适配判断和未核验事项。两套系统没有共同测试集；本次也没有在 Lumina 上运行对照实验。

## Tycho: inspected implementation and evidence

Tycho 使用既有大模型在任务运行中生成、修改局部世界模型，不是一个单独预训练的通用世界模型权重。其模型接口允许自由选择潜在状态，以 `init_state → transition → render/outcome` 表达历史到未来的演化；Actor 可以按需使用或绕过模型。[官方架构](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/docs/ARCHITECTURE.md)

### Source record

- SOURCE: [NIMI-research/Tycho](https://github.com/NIMI-research/Tycho)。
- VERSION / COMMIT: `f68912a764372ead0a610db2e1c011d41ce5197e`；本次 GitHub `commits/main` 核验仍指向该提交。
- LICENSE: [Apache-2.0](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/LICENSE)。
- SOURCE SYMBOL: [`WorldModelBuilder.build`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/agent/builder.py)、[`TriggerDispatcher.should_fire`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/agent/dispatcher.py)、[`builder.system.j2`](https://github.com/NIMI-research/Tycho/blob/f68912a764372ead0a610db2e1c011d41ce5197e/tycho/prompts/builder.system.j2)。
- ORIGINAL INPUT / OUTPUT: Builder 接收请求、持久化工作区证据、已有验证状态及偏差信息；可修订模拟代码并返回有限长度的建议报告。
- ORIGINAL RULE: Builder 每次使用新的局部对话，受调用预算约束；无法调用游戏 `take_action`，但能写模型文件、运行 Python。trigger 模式依据动态/终局预测偏差唤醒 Builder；最佳 orchestrator 模式由 Actor 按需请求 Builder，两者不能混称。
- LUMINA ADAPTATION: 参考有界辅助认知、证据优先、假设可被推翻和独立终局验证。实际 Mind 只读权限仍由 Lumina 契约约束；Tycho Builder 的 Python/写文件权限不能直接移植。本文没有增加任何运行时能力。

### What the experiment does and does not establish

同一 Opus 4.8 的公开 25 游戏比较中，无世界模型、single、orchestrator、trigger 的 RHAE 分别为 79.07、85.36、88.49、83.07。trigger 的动作前可评估转移匹配率为 88.1%，高于 orchestrator 的 16.2%，但任务分数更低；Builder 调用分别为 1192 和 147。终局回放拟合不能冒充动作前预测准确性。[Tycho 论文 §5.1，表 7–8](https://arxiv.org/html/2607.28287v1)

这些是公开任务、单次配置运行的结果，包含开发用游戏，不能证明对真实开放世界或 DeepSeek 的迁移效果。对 Lumina 的研究启示是：**模型预测能力、判断收益、认知成本必须分别测量**；机械地在每次误差后修模未必是更好的认知策略。

Tycho 的概念并不限于精确网格模拟器；作者也讨论信念状态、关系图等其他表示。真正不能直接外推的是当前代码与实验覆盖范围，而非“程序绝不可能表达不确定性”。[Tycho 论文 §6](https://arxiv.org/html/2607.28287v1)

## MetaWorld: identity and evidence boundary

这里的 MetaWorld 是中科闻歌 Decitron 决策框架中的显式世界模型。它不是 Farama 的机器人强化学习 [Meta-World benchmark](https://metaworld.farama.org/)，也不是同名多视角视频生成论文。[中科闻歌产品页](https://www.wenge.com/site/69c9202de4b03b4745b93856)将整个 Decitron 定义为结合世界建模、多智能体推演与决策求解的系统。

定位到的作者技术报告是 Decitron Team, *Decitron: A Unified Framework for Decision Intelligence via World Modeling, Multi-Agent Simulation, and Strategic Reasoning*, ChinaXiv 202608.00064。[原始报告入口](https://chinaxiv.org/abs/202608.00064)目前无法读取；[原始 PDF](https://chinaxiv.org/user/download.htm?filetype=pdf&uuid=3803170c91d8447ab7e6d9326c39c722)分别返回 HTTP 405 或站点的页面失效提示。第三方 [ChinaRxiv 镜像](https://chinarxiv.org/items/chinaxiv-202608.00064)保存了标为英文原文的作者摘要，并标注 v1 提交于 2026-08-03。镜像仅作为原文定位和摘要访问渠道；该日期不能与产品首次发布或 9 月媒体报道日期混用。本次没有读到论文全文。

### 本次能够支持的技术判断

| 内容 | 证据类型与边界 |
| --- | --- |
| 世界状态建模 | 官方产品页列出参与方、目标、利益、角色偏好、行动、事件/因果图谱及初始环境快照。是公开设计描述，未检查源码。 |
| 推演与更新 | 官方宣称比较多种干预下的状态演化，并随新信息修订判断；不是仅生成一段分析文字。实现细节及运行效果未独立验证。 |
| 因果与不确定性 | 官方宣称使用结构因果模型、概率信念及贝叶斯更新。不能由此推定自动学出的因果关系正确或预测概率已被充分校准。 |

以上三项来自[官方产品页技术架构与核心技术说明](https://www.wenge.com/site/69c9202de4b03b4745b93856)，应称“官方描述/宣称”，不应称“复现证实”。

作者摘要进一步将 SAO（State–Action–Outcome）与 MetaWorld 作为结构化状态演化基础；模拟层使用有限理性、部分可观测的异质主体，另有博弈、形式优化和多轨迹概率校准。摘要声称改善情景预测，但摘要本身不提供足以审计的实验设置。[作者摘要的镜像副本](https://chinarxiv.org/items/chinaxiv-202608.00064)

### 没有核验到的事项

- MetaWorld / Decitron 的官方源码、权重、许可证、可复现实验包和本地部署说明。本次检查了公司自述归属的 [wenge-research GitHub 账号全部 7 个仓库](https://github.com/wenge-research?tab=repositories)，以及 [Hugging Face 模型列表](https://huggingface.co/wenge-research)；列表中没有目标系统。检索无结果不等于证明它永久闭源。
- 具体底座型号、训练目标、训练数据、参数量、训练成本。不能把该公司的 YAYI / ScienceOne 训练信息直接套到 MetaWorld。
- 媒体提到的“三层世界状态”、固定因果 DAG 的更新规则、AutoABM 算子数量及具体 benchmark 数字；本次没有可访问全文或代码去核验，因此不以这些细节决定 Lumina 架构。
- 商业产品页可访问，且官网链接到 [Decitron 应用](https://decitron.wenge.com/)，但本次未登录、未运行推演，未验证 SLA、隐私部署或可用 API。

## MetaWorld 对 Lumina Mind 的启发（研究判断，不是上游事实）

MetaWorld 的问题设定更接近长期 Mind 的“理解现实”：信息不完整、多个主体有各自目标、外界会变化，过去的判断可能失效。它因此值得作为世界状态语义的参考。当前只读认知监督的权限边界仍由 [Mind 设计](../../docs/MIND_DESIGN.md) 和 [Mind 工作区契约](../AGENTS.md)决定。

适合借鉴的最小思想：

1. 将观察到的事实、对事实的解释、未来预测分别表达；预测不能回写为已发生的 Memory 事实。
2. 让判断指向有限的状态与来源证据，并说明什么新证据会使判断失效。
3. 把“预期动作结果”与“实际观察结果”的偏差用于决定是否重新检查，保留 NoChange。
4. 需要推演时先明确局部问题、参与者、约束与观察范围；没有证据的关系保留未知，不把每次自然语言理解都强行编译成完整模拟器。

这些是根据 Lumina 的持久化、来源和权限约束提出的适配建议。它们不要求引入 Decitron 的整套多智能体博弈、概率引擎、常驻监测或新数据库；也没有现成证据证明这类系统能提升 Lumina 的执行监督质量。

在 Tycho 与 MetaWorld 之间，需要分别回答“参考哪套可检查机制”和“长期要表达什么世界知识”。MetaWorld 在第二个问题上更相关；在第一个问题上，目前公开证据不足以支持直接移植。选择时不能把可开源复用性误当作认知适配性，也不能把愿景适配当作实际有效性。

## Fit to the current Lumina repository

以下来自本地设计与已记录实验，未在本次调研中重跑测试：

- [Mind 设计](../../docs/MIND_DESIGN.md)定义的是获取信息、整合判断并给出 `NoChange / Directive / DecisionIntent` 的有界认知器官。详细规划属于 Execution；状态变更由代码拥有。MetaWorld 和 Tycho 都只能为其提供局部机制。
- [W0–W8 审计](WORLD_MODEL_W0_W8_AUDIT.md)已经检验预测先于观察、模型修订、潜在状态等 Tycho 相关机制。W5/W6 支持有限机制，W8 在终止协议上仍为 INCONCLUSIVE；这些不能外推为持续开放世界理解。审计已要求停止扩张 synthetic harness。
- [S0 结果](WORLD_MODEL_SHADOW_S0_RESULT.md)表明实际 Execution 的只读证据投影与零行动权限 shadow 可以工作，但终态历史为防事后预测而隐藏 PRE，导致原 PRE 引用之后无法由 Execution 重新解析。结论仍是 `S0_INCONCLUSIVE / DO_NOT_PROCEED_TO_S1`。它是证据可回放性与预测资格之间的契约缺口，不是更换模型或加入博弈模拟可以解决的问题。

### Proposed direction, not an implementation task

未来值得验证的认知链是：

```text
Memory / Execution 所有者提供的有限证据
  → 区分现实事实、局部观察、当前模型假设
  → 形成带来源、适用范围与未知项的局部判断
  → 必要时预测有关变化；可执行模拟只是其中一种方法
  → 新证据到来后比较并修订
  → 判断维持方向、请求核实，还是提出高层调整
```

这里“局部判断”不能退化成一张静态状态表：只有能作出可检验预测、遇到相反证据会更新，才检验到了世界模型的作用。同样，模拟结果仍是假设推演，不能写成 Cold/Memory 中已经发生的事实。

举例：连续几次 Execution 尝试未成功，Mind 应结合证据判断是信息不足、前提错误还是方向失效，并判断是否值得干预。MetaWorld 提醒我们考虑参与者与环境约束；Tycho 提醒我们提出能被后续观察反驳的假设。下一步的代码操作仍由 Execution 决定。

近期顺序应延续已有审计：先用单独任务解决 S0 的证据回放/资格契约，再验证真实证据下的预测与高层判断收益。之后如需比较世界表示，固定 DeepSeek-V4-Pro、证据权限、预算与输出契约，一次只改变表示或修订机制。对照至少保留直接认知基线，分别观察预测正确性、不可验证率、判断收益、错误干预及成本。

本次只新增本研究笔记，没有修改 Mind 架构、Memory、Execution、模型配置或生产状态，也没有发起任何模型实验。
