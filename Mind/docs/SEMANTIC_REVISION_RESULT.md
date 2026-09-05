# D5：条件判断与跨事件认知修订

结论：**部分修复，有明确剩余阻塞；尚不具备同预算行为比较资格。**
独立 Mind 的行为价值仍为 **INCONCLUSIVE**。本轮没有完成一条新的、成功的
Mind 指导送达并接受最终反馈的真实闭环，不能将 Execution 自行完成任务算作成功。

## 基线、复用与实际修改

本地分支 `Execution_lab2`，HEAD
`9d63da7311baa7611782cc8079fb09e9d81f4e25`。实际基线是本地 D4 增量，固定于
`Mind/fixtures/protocol_recovery_d4/loop/source/`；没有用远端旧文件覆盖脏状态。

直接复用 MindOrgan 的持久条目、activate/accept_result、Nervous 邮箱与事件续接、
Trace 原子提交和历史版本、D4 一次有界恢复、Directive 绑定与既有 Execution 桥、
隔离 IPython。新增真实调用没有使用新的 provider、World Model 或生产接线。

原始请求表明 D4 的错误 discriminator 完整进入了下一活动；`_apply_updates`
替换完整条目，遗漏的旧条目则保留。未发现字段丢失或 reducer 自动继承旧 status 的
实现缺陷，因此没有重写 Organ、Trace 或更新算法。

实际 diff（相对本地 D4，不把以前未提交文件算作本轮新增能力）：

| 文件 | 本轮变更及必要性 |
| --- | --- |
| `Mind/event_loop.py` | 增加可选 D5 认知语义契约。优先核对受影响旧条目，明确当前命题的状态、规则范围、历史时间和 discriminator 语义。v1 未解决归档错误，v2 将核对前置并增加通用 P/not-P、模式条件示例。D4 wire、验证 schema 形状及预算不变。约 +41/-5 行。 |
| `Mind/cognitive_contract.py` | 在既有实验入口增加显式脚本认知种子、三类多事件样本、整份状态审查和预算冻结；D4 原实验只覆盖固定许可证任务，不能验证已存在的错误状态被修订。复用已有 owner、事件与投递函数，约 +273/-6 行。 |
| 同一实验文件的证据投影 | 独立验收暴露了本轮引入的重复 JSON 编码缺陷。验收结束后改为三个白名单文件的有界标签＋原文，版本 `task-files-d5-v2`；引用验证保持严格。这个后置修复只有确定性验证，未补跑真实验收。 |
| `Mind/test_semantic_revision.py` | 验证 D4 wire 保持、整个条目替换和历史、重启、拒绝不抹除已提交认知、投递门槛、输入权限、执行代码隔离；覆盖三条实际失败引用。脚本响应不计为语义收益。 |

没有新增 Mind 模块、语义审核 Agent、规则库或宿主语义改写。当前的外部审查只在
认知提交后判定是否准入指导，不修改已提交状态，也不把审查意见传给运行中的 Mind。

## 预注册、变量与预算

[任务与判据](SEMANTIC_REVISION_TASK.md)；
总预注册??????`../fixtures/semantic_revision_d5/registration.json`?，SHA
`1cc0efba13a395c35a4978b757d7c431ca63b3541e88daef85b76af75e4fcdbc`。

开发固定四例：两个 D4 归档续接、温度规则适用范围、规则生效时间。错误种子明确
标注为 **SCRIPTED**，通过既有认知提交入口初始化，绝非本轮模型自然生成错误。
对相同种子和事件比较旧 D4 契约、D5-v1、D5-v2。修改属于提示和字段说明的一组
接口校准，不声称各项提示的独立因果收益，更不是 Mind/Execution 的收益 A/B。

预算：旧契约开发最多 12 次，两个候选最多各 12 次；独立验收最多 27 次 Mind
和 12 次 Execution；总计最多 75 次，每次最多 2000 输出 token。沿用 D4 每活动
两认知步、一次取证、一次协议恢复、最多三次物理调用。全部使用官方
`https://api.deepseek.com/anthropic/v1/messages`、`deepseek-v4-pro`、
`DEEPSEEK_API_KEY`、thinking disabled、temperature 0；无 provider fallback。

v1 第一例已接受响应，但人工审查等待超时导致 recorder 中断。失败 run 原样保留；
后补的审查明确判其语义失败。归档脚本 `continue_dev1.py` 仅调用余下三个未运行样本，
没有重放或重新采样第一例；v1 合计仍只有四次真实调用。

最后候选修改后冻结另一组三个样本，每例三次活动、同一正式 Intention、同一个持久
Mind，每次活动之间关闭并重开。独立任务为策略变化的实际工作区汇总；正常和证据
不足对照使用明确标注的合成 owner 记录与真实 Mind。触发为 outcome/input_changed，
不读取评测标签。真实 Execution 自然 Wait 后进入合格决策位置，未验证无暂停并发运行。

独立冻结样本与源码??????`../fixtures/semantic_revision_d5/acceptance-cases.json`?，SHA
`36e32cc452066ce2ced7f2311a941a7c76fdc9e51cad8143b68da684d3615f94`。
冻结后只运行一次；后置投影修复没有改写这份冻结或其失败结果。

## 开发修复前后

| 开发样本 | D4 契约 | D5-v1 | D5-v2 |
| --- | --- | --- | --- |
| 归档命题改写 | 改写为“已过时”却标 contradicted | 同一错误；另有审查超时记录 | 2123 字符，超过 2000 被拒，不能算修复 |
| 归档条件错误 | approval_time 下保留 inactive-license 排除条件 | 同一错误仍在 | 主动重新提交同一错误 discriminator |
| 温度模式变体 | 两次空参数，恢复耗尽 | 正确区分 chilled/ambient，保留 Celsius 知识 | 同样正确 |
| 时间变体 | 正确保留历史 R1，按现行 R2 判断 | 正确 | 正确 |

有效语义通过数分别为 **1/4、2/4、2/4**，没有把协议失败剔除。温度例的旧契约
没有合法认知响应，不能把后续通过解释为已识别的纯语义因果优势。

D4 最终成功结果本身没有测试 inactive-license 的分支，不能单凭成功要求模型否定
该假设。但已有普通规则明确区分两种策略，足以要求修订错误 discriminator；D5
两版仍未做到。这个失败不是引用或上下文遗漏问题。

D4 所谓正常案例实际存在版本字段更新缺口，其 Directive 不能直接定性为有害干预。
本轮纠正这一解读，保留 D4 历史 verdict 原文；新正常对照从一开始就满足全部要求。

## 独立验收：按整份有效状态报告

完整 campaign??????`../fixtures/semantic_revision_d5/acceptance/result.json`?，SHA
`cecc722de0519056957b63413017d1b52a8ffef4d20518aa672b4bcc9de2961b`。

| 过程 | 条件、命题/状态/依据/判别条件 | 旧错误与正确知识 | 干预和持续反馈 |
| --- | --- | --- | --- |
| net_summary_revision | 首事件一致；后两次引用拒绝，不能评分为接受态修复 | 首事件把错误 contradicted 改为 supported；把“所有模式排除非正值”改为仅 positive_only，保留 unapproved 排除规则 | 第一次 NoChange 合理；随后有原始 Directive 候选但未被提交、送达为 0；最终结果已回流却未被 Mind 接受 |
| complete_handoff | 三次整份状态均一致 | 正确交付知识保留，更新依据和完整性条件 | 三次均 NoChange，无多余工作；最终 owner 引用已提交 |
| ambiguous_recovery | 三次均保留两个 open 竞争解释，联合恢复未被误当确定原因 | 旧假设没有被编造成 supported；“未区分原因”命题合理 | 三次均 NoChange；最后沿用有效旧引用，未强制提出额外工作 |

共 **9 次活动，7 次 owner 接受**；这 7 次整份状态的四项语义审查均通过。
正常对照无依据干预 **0/3**；证据不足对照三次均保留未知。本轮没有真实取证调用，
只验证了“保留未知”这一合法分支；既有取证续接继续由确定性回归覆盖。

冻结脚本的聚合结果仍是 **1/3**。其中 ambiguous_recovery 的三次认知都已接受，
语义审查也通过，但最后没有引用最新的重复反馈，未满足脚本额外的
`final_owner_source_used` 门槛。沿用仍有效的依据是合法行为，不能把它算作语义错误。
本 campaign 只记录这个门槛局限，不回写分数；两个语义对照分别均通过（2/2）。

三个案例每次重开后的**完整条目内容**与前次持久状态一致。正例最终的三个条目
均为正确、明确作用域的策略规则，初始两个错误没有复活；然而 revision 停在 2，
没有接受现行 net 阶段和最终结果。状态恢复正确不能代替对新现实的持续修订。

## 真实链路失败的 trace 级证据

[真实工作区记录](../fixtures/semantic_revision_d5/acceptance/net_summary_revision/result.json)
包含全部请求、模型原文、Nervous/Trace、Execution actions、前后文件快照。

1. Execution 按 revision 1 的 positive_only 规则生成 count=1、total=12，然后自然
   Wait(source_update)。Mind 修订脚本植入的两项错误，提交 NoChange。
2. owner 将 policy revision 改为 2、mode 改为 net。Mind 识别了策略切换和旧产物
   过时，原始指导写道：

   > The current policy.json is revision 2 with mode net, superseding revision 1 positive_only. The summary must be recomputed against policy_revision 2: net includes all approved samples regardless of sign (values 12, -7, 0), excluding the unapproved sample (99), giving count 3 and total 5.

   这是模型实际原文，宿主没有补入答案。但两条 basis 引用用了文件 JSON 原文，
   source 却是再次编码后的字符串，严格校验返回 `ungrounded_basis`。
   **发现问题 ≠ 认知提交；候选指导 ≠ 已提交 Directive；没有投递请求。**
3. Execution 收到普通 source_update，advisory 为空，自行读取新策略、重算为
   count=3、total=5、写完成标记并 ClaimComplete。它在第一阶段代码中已经支持 net
   分支。客观任务成功为 1/1，无法归因于 Mind。
4. 新结果通过 Nervous 返回同一个 Mind。模型候选中正确判断最终汇总，但 count/total
   引文的换行和引号同样不匹配，整个认知提交再次被拒。没有隐式 NoChange 替代，
   也没有部分接受新条目。

独立验收的两个拒绝均属于**引用/来源投影失败**，不是空参数、token 截断或未知网络
结果，不能使用冻结 D4 协议恢复免费重试。没有发生机制性权限或持久化安全失败。

验收后 `task-files-d5-v2` 改为保留文件原文。三条原失败引用在未修改任何字词或
source ref 的情况下均变为严格 substring；白名单、800 字符界限、截断标记、代码
和日志隔离不变。这项修复有确定性证据，**没有修复后真实模型闭环证据**。

## 成本、回归和可复查入口

| 阶段 | 真实调用 | API input_tokens | API cache_read_input_tokens | output_tokens |
| --- | ---: | ---: | ---: | ---: |
| 旧契约开发 | 5 | 8328 | 8704 | 2257 |
| D5-v1（含未重复采样的续跑） | 4 | 11359 | 3968 | 1874 |
| D5-v2 | 4 | 12335 | 3584 | 2037 |
| 独立验收（9 Mind + 7 Execution） | 16 | 23761 | 19456 | 4404 |
| 合计 | **29/75** | **55783** | **35712** | **10572** |

usage 按 API 原字段分别报告；不据成本事后剔除样本。21 个真实 Mind 活动共 22 次
物理调用（旧契约一次恢复失败），另有 7 次 Execution。脚本种子、确定性测试、
Codex 人工审查不冒充这些真实调用。全部 call 文件保留配置、原始响应和用量。

- 修复后针对性测试：**46 passed**，含真实 Docker、脚本模型的机制测试。
- 根回归：**347 passed, 24 skipped**；最终投影改动由新增针对性测试覆盖。
- `git diff --check` 通过；上游 MAGMA 无 diff；历史 D1/P0/D3/D4 哈希复核通过。
- 唯一真实任务临时目录已与归档最终快照逐文件核对后清理。仓库、真实数据、历史
  Trace 和原脏状态保留；未 commit/push。

无模型调用的复查入口：

```powershell
./.venv/Scripts/python.exe -m Mind.fixtures.semantic_revision_d5.analyze
./.venv/Scripts/python.exe -m pytest Mind/test_semantic_revision.py Mind/test_protocol_recovery.py Mind/test_event_loop.py -q
```

现有真实入口为 `Mind.cognitive_contract` 的 `register-d5 / d5-dev-1 / freeze-d5 /
d5-acceptance`，每次需全新的 campaign 目录和预注册预算。已冻结目录拒绝覆盖；
当前投影源码与旧验收冻结不同，也会拒绝将旧验收重跑成修复版结果。
量化分析??????`../fixtures/semantic_revision_d5/analysis.json`?和
[代码审查](SEMANTIC_REVISION_REVIEW.md)保留完整结论。

## 北极星差距与唯一下一推荐

“持续理解”仍有两个缺口：语义上，旧 discriminator 即使面对区分规则仍可能残留；
机制使用上，真实模型能看见正确事实却可能无法合法提交。后者的已证实投影缺陷
已修，但还没有真实复验。已经恢复 ID、保持历史或最终任务达标都没有填补这些缺口。

**唯一下一步：使用原文证据投影，做一次专门针对旧 discriminator 修订的有界开发
闭环，再决定是否进入行为比较。** 普通规则与实际区分性观察应同时可见，继续检查
整份最终状态；不增加 Mind 模块，不开展大规模 A/B，不将本轮局部成功包装为独立
Mind 的普遍优势。
