# D6：认知链根因修复与独立跨事件验证

日期：2026-09-06。**工程缺陷已修复；语义修复未全面成立；本轮指导送达闭环未成立。**
行为比较资格 **NOT_YET**，独立 Mind 行为价值 **INCONCLUSIVE**。
这不是新的收益 A/B，也不改变 D1–D5 的任何 verdict。

## 实际基线与复用

收尾只读核验：本地和远端 `Execution_lab2` 均为
`9d63da7311baa7611782cc8079fb09e9d81f4e25`。真正的修改基线是本地 D5 增量，
包括已完成的 `task-files-d5-v2`，保存在
diagnostic/source??????`../fixtures/cognitive_chain_d6/diagnostic/source`?。没有 reset、commit 或 push。

直接复用持久 Mind、Nervous 请求/结果续接、Trace 重放、一次有界协议恢复、
Directive 一次性决策绑定、现有 Execution facade 和隔离普通 Python。
没有新增 Mind 模块、调度器、语义审核 Agent、World Model 能力或生产接线。

## 沿实际链路的根因与排除项

| 位置 | 最小复现/证据 | 归属与处理 |
| --- | --- | --- |
| Native schema → owner reducer | `supported` belief 的空 `basis` 被旧 schema 接受，随后 owner 报 `assessment_needs_evidence` | 确认合同漂移。当前 schema 和 reducer 共用字段、枚举、上限及条件性非空依据定义 |
| Owner 文件 → activation Evidence | 三个各允许 800 字符的文件合并成 2535 字符，却装入上限 1000 的单个 Evidence | 确认聚合边界不一致。利用原有三个 Evidence 名额逐文件投影，不扩大文件白名单或每文件读取量 |
| 证据正文 → wire → grounding | D5 二次编码缺陷已在本轮前修复 | 复用原有 literal file text；新增引号、换行、反斜杠、中文、截断边界经过真实构造/序列化/提交/重放的回归，错误引用仍严格拒绝 |
| `inspect_execution` 引用 | 原来 catalogue 与 owner 同为 canonical DTO，并非两边文本不相等 | 不把它算作历史根因；D6 版本统一使用 owner 提供的带字段名逻辑文本，旧版本仍用原文格式 |
| 旧认知 → raw output → commit | 归档条件/状态错误可在原始模型输出中直接看到，接受后完整进入下次上下文 | 排除 reducer 自动沿用旧标签、正确输出被改坏、丢失旧 discriminator，以及本例缺少规则来源 |
| 最终评分 | D5 曾要求引用最新事件，即使旧证据仍有效 | 仅 D6 去掉这个代理判据；仍检查整个有效认知，不把相同 ID、哈希或新增成功条目视为语义正确 |

机械复现见 [engineering_reproduction.json](../fixtures/cognitive_chain_d6/engineering_reproduction.json)。
代码只认证结构、引用、版本和提交完整性，不认证自然语言推断的真值。

## 有区分力的语义诊断

修改前，用当前 D5-v2 进行了两个规则域、每域有/无错误旧条目的四次真实调用。
每对保持规则来源、事件、预算一致；移除旧条目仅用于诊断，未用作运行修复。

- 许可证域：有旧错时重申错误；无旧错时仍生成把 `release_time` 条件带入
  `approval_time` 的 discriminator。
- 快照域：两臂都正确理解 `cached_snapshot` 不要求可达性，但所写测试把
  “实现排除了合格记录”当作反驳“规则允许该记录”的证据。

因此，**错误旧条目不是错误出现的必要条件**。不能归因于记忆锚定这一个解释；
也不能由四次调用确定模型内部根因。主条件理解、规则与产物的区分、判别测试的语义
是不同失败维度，诊断 aggregate 0/4 不等于四个主结论都错。

## 实际 diff 与两个有界候选

implementation.diff??????`../fixtures/cognitive_chain_d6/implementation.diff`?
以本地 D6 基线生成，不混入此前 D1–D5 工作：

- `Mind/organ.py`：共享已有认知字段合同，删除重复的手工形状/长度判断；
  保留严格依据、ID/假设绑定和原子提交。增加显式上下文契约版本与共享引用文本。
- `Mind/event_loop.py`：D6 native schema 使用 owner 定义；替换叠加提示。
  v1 明确命题、范围、状态与未观察的测试；v2 测试“从来源推导条件 → 核对受影响旧字段
  → 按最终句子选择状态”的顺序，并在字段说明中明确 assertion/negation。
- `Mind/cognitive_contract.py`：逐文件有界证据、版本化活动入口、有效旧引用评分、
  D6 对未知 transport outcome 停止后续调用/事件。
- `requirements.txt`：声明已有代码使用的 `jsonschema==4.26.0`。
  新增回归集中在 `Mind/test_cognitive_chain.py`。

三个既有代码文件共增加 200 行、删除 56 行；另外有依赖声明和测试。
这不是每项改动独立收益的因果实验。v1/v2 均保留历史请求重建，未增加字段、模型轮次、
恢复资格、权限或输出预算。`DecisionIntent` 保留，但未开启目标切换。

| 开发阶段 | 合法接受 | 整份认知正确 | 完整案例通过 | 失败实质 |
| --- | ---: | ---: | ---: | --- |
| v1 | 3/4 | 1/4 | 1/4 | 许可证条件重申；时间案例保留倒置状态；一例 2129 字符被拒 |
| v2 | 2/4 | 1/4 | 1/4 | 时间案例状态修正；许可证旧错未改；两例 2439/2234 字符被拒 |

两版只是通过案例发生变化，**不能宣称总体语义修复有效**。
v1 的超长原始输出还把“产物已过期”标为 `contradicted`；v2 的超长原始输出
仍保留无时间限定的旧“当前产物正确”命题。这些提案未提交，不能记作修复成功。
2000 字符是提示和 owner 一致的既有总上限；单字段上限是各自天花板，不能相加后
假定总输出合法。本轮未改变 D4 仅恢复特定结构错误的策略，三次超长失败不伪装成 NoChange。

开发严格止于两次有依据的候选；未耗尽的调用额度没有用于再抽样找 PASS。

## 冻结与调用预算

所有真实请求：`deepseek-v4-pro`、官方 Anthropic-compatible endpoint、
`DEEPSEEK_API_KEY`、thinking disabled、temperature 0、每次最多 2000 output tokens。
活动仍最多两个 cognitive steps、一次取证和一次协议修正，共最多三次物理调用。

| 工件 | SHA-256 字段 |
| --- | --- |
| [总预注册](../fixtures/cognitive_chain_d6/registration.json) | `856153aaf24150fe0abc9a96f27331a8f43d4abc38daec8afdd67af04d6d2aad` |
| [独立样本冻结](../fixtures/cognitive_chain_d6/acceptance-cases.json) | `9e7710ca32adc4bb73171b5acfab4a0b33f880ce19111c4925ec57c1f01fe779` |
| 独立 campaign??????`../fixtures/cognitive_chain_d6/acceptance/result.json`? | `306bd54848b6b5c27cebad1fd289bbf5fac6291f51a7c79a1c963781c5245e73` |
| [量化与完整性汇总](../fixtures/cognitive_chain_d6/analysis.json) | `9ad008174563f3ad1ce62308264b4e871e50de17eca075a3e4c62ae826f50eb9` |

每阶段有请求前源码快照、完整 wire/response、接受状态和独立审查记录。
全部 D6 实际 **28/75 次物理调用**：诊断 4、开发 4+4、独立 16。
独立阶段为 **9 Mind + 7 Execution**，低于分配的 27+12。
provider usage 原字段分别累计：`input_tokens=54757`、
`cache_read_input_tokens=23296`、`output_tokens=10447`，cache creation 为 0。
独立阶段分别为 25886、11136、4904；不将这些字段擅自换算价格。
无新 provider、fallback、transport 未知结果或真实协议修正；取证/恢复路径由确定性回归验证，
不能把本轮 0 次真实使用称为可靠性的新增统计证明。

## 独立验收：整份最终状态和真实行为分别评价

样本在最后代码候选后新建并冻结，一次运行。真实任务使用旧 owner oracle，
但把旧 net 汇总转换方向反转为 `net → positive_only`，并使用新数据；错误种子显式标为 SCRIPTED。
另外两例使用普通 synthetic owner reports，未伪装成真实物理工作区。

| 独立案例（三事件） | 条件/状态/依据/测试及旧错处理 | 不干预 | 送达与结果 | 完整判据 |
| --- | --- | --- | --- | --- |
| reverse_scope_summary | 3/3；正确旧知识保留，错误全称规则成为 contradicted；最终 4 条一致认知 | 第 1、3 事件 NoChange | Directive 1，实际送达 0；Execution 自行达标，结果回流并被接受 | FAIL |
| complete_offline_receipt | 3/3；仪表盘不是收据有效性条件；最终 2 条一致认知 | 3/3 NoChange | 完整交付条件没有缺口，不产生额外工作 | PASS |
| confounded_preview_recovery | 3/3；缓存与 worker pool 两个原因仍 open，保留区分性测试；最终 3 条 | 3/3 NoChange | 反复联合变更成功不证明单一原因；最后仍合法引用旧证据 | PASS |

冻结总体 **2/3**。合法认知接受 **9/9**，整份认知语义检查 **9/9**，
Direction 抽象边界 **8/9**，NoChange **8**，Directive **1**，送达 **0**。
没有归档全部条目、删除所有 discriminator 或清空历史。
每例都关闭并重建 Mind/Nervous；最终报告逐项检查语义，重启状态相等只是额外机械证据。

### 为什么真实闭环仍失败

完整记录在 [reverse_scope_summary/result.json](../fixtures/cognitive_chain_d6/acceptance/reverse_scope_summary/result.json)。

1. Execution 在第一次 Mind 活动前就已经写出了两个模式的正确算法，完成 net 的
   `count=3,total=9`，然后 Wait。算法来自其原始目标和输入，不能归功于 Mind。
2. Mind 第一次活动正确修复两个脚本错误，认知 revision 从 1 到 2。
3. 新 policy revision 5 到达后，Mind 正确判断仅 13 合格、旧汇总过期，并提交 Directive。
   其末尾原文为：
   > Prepare summary.json with policy_revision=5, sample_revision=4, count=1, total=13, then write verified to .lumina-complete and ClaimComplete.
4. 具体验收值本身合法，但“写文件 → 写标记 → ClaimComplete”是执行操作序列，
   违反冻结的边界。外部审查仅扣住投递；没有改写它、抹除已提交认知或把它改成 NoChange。
   `application=null`、`advisory=null`，实际 Execution 请求无该 Directive。
5. Execution 收到原有事件后，自行读取输入、重算、写汇总、写标记、claim_complete。
   客观结果为 revision 5、sample revision 4、`count=1,total=13`；owner completion verified。
6. 结果经 Nervous 回流，第三次 Mind 活动接受，认知 revision 到 4，保留正确范围与状态。
   这是结果承接，**不是指导被采用，也不是 Mind 改善行为的证据**。
   最终四条认知只记录策略知识，没有明确记录最终 count/total；能确认的是反馈活动被接受、
   有效状态与结果相容，不能进一步声称模型已在持久认知中确认产物的全部细节。

实际工具权限从未授予 Mind；文本的操作倾向和结构性权限隔离分别记录。
此处也观察到初始 `goal` 含有相同完成协议措辞，并出现在 Mind wire 中。
它是可检验的上下文诱因，尚不能凭措辞重合认定是唯一因果根因。

## 验证、局限和唯一下一建议

[validation.json](../fixtures/cognitive_chain_d6/validation.json) 保留全部测试结果：
最终 Mind/Nervous 共覆盖 528 项通过、5 项跳过；其中受限运行 518 通过、10 个 Docker 失败，
确认命名管道权限问题后在有 Docker 访问权环境原样重跑这 10 项全部通过。
根套件 347 通过、24 跳过，不能替代上述 Mind 回归。
`git diff --check` 通过；MAGMA 无修改。历史哈希与 D4/D5/D6 v1 请求重建检查通过。
实际临时工作区四个最终文件逐一校验、归档，随后仅删除本轮那个已核验 TEMP 目录。

**已确认并修复**：结构合同漂移、证据聚合上限冲突、有效旧引用被评分误拒；
依赖与未知结果停调由 code-review 补齐。
**已排除的本例解释**：单纯 reducer 标签继承、缺少可见规则、错误旧条目是必要原因。
**未解决**：归档作用域错误、复杂有限更新的总字符预算可靠使用、指导夹带执行协议。
独立样本的语义通过不能覆盖归档失败，更不能证明独立 context 的普遍优势。

距 North Star 的差距仍是：有持续状态和可追溯事件，并不保证持续理解正确；
本轮更没有获得“合格指导送达 → 执行采用 → 行为改善”的证据。

**唯一下一建议**：先做一个有界、同预算的 Intention 投影对照，检验把高层验收语义与
Execution 的 marker/ClaimComplete 协议分开呈现，是否降低指导中的操作序列。
保持相同规则证据、认知种子、现有投递桥和严格评分；归档语义错误继续作为必须报告的回归，
不能先扩大 Mind 模块或进入收益比较。这是下一实验建议，本轮没有偷改已冻结请求来重跑。

实际运行入口（已完成目录防重复执行）：

```powershell
python -m Mind.cognitive_contract d6-acceptance Mind/fixtures/cognitive_chain_d6
```

离线可复核入口，不调用模型或重写原始评分：

```powershell
python -m Mind.fixtures.cognitive_chain_d6.analyze
```
