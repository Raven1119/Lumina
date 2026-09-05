# D3 结果：高层认知提交校准，真实闭环仍有协议阻塞

日期：2026-09-05。结论：**INCONCLUSIVE**；**尚不具备下一轮行为比较资格**。
开发接口 5/5 通过，一次性独立验收 4/5；正常对照收到空工具参数，
因此按预注册停止在接口前提，真实多事件闭环没有运行。不能把确定性
闭环通过、7 条合格文本或 9 次持久提交写成真实行动收益。

## 复用、修正与未验证

本地与远端 `Execution_lab2` 均为
`9d63da7311baa7611782cc8079fb09e9d81f4e25`。本地已有 D1/P0 未提交文件
全部保留；本轮 diff 以 P0 的 `source/` 快照区分，不将其已有工作重新计入成果。

| 类别 | 本轮事实 |
|---|---|
| 直接复用 | MindOrgan 持久认知与取证续接；Trace/Directive 持久化和一次性资格绑定；Nervous 邮箱及因果确认；已有真实 Execution 桥；P0 原生结构化适配与隔离 IPython |
| 确需修改 | D3 选择性启用的提交契约、现有 reducer 的字段上限、明确来源目录；自然事件唤醒时传入既有 advisory 的小型参数贯通 |
| 尚缺真实验证 | 同目标跨事件的真实指导送达、实际采用、结果回到同一 Mind，以及完整正常对照 |

`Mind/event_loop.py` 的默认 P0 行为保留。新增
`cognitive-submit-d3-v1` 统一高层语义说明、schema 与实验接收规则；
`ExecutionOrgan.deliver_event` 和既有 runtime 增加可选 advisory 透传。
Mind/host/Trace/Directive/Nervous 本体没有重建。新增的
`Mind/cognitive_contract.py` 是有界实验入口和记录器；不承担新的规划或提交权限。

根据当前用户明确的边界重新分析 P0，但不修改历史 verdict：

| P0 内容 | D3 处理与原因 |
|---|---|
| 001/008 的 settled 字段条件、结果文件与预期总额 | 允许：业务约束与验收内容，并未指定工具、代码补丁或实现算法。原抽象判定过严 |
| 006/014 的运行脚本后写完成标记 | 拒绝：明确执行操作序列。宿主不得删除越界部分后代为提交 |
| 014 的 381 字符 Directive、888 字符整包 | 满足既有 1000/2000 合同；额外 320 上限缺乏依据，D3 已移除 |
| 014 的虚构来源后缀、027 的不匹配原文引用 | 拒绝仍合理；D3 提供准确 ref 枚举和逐字来源目录，宿主不猜测修复 |

字段上限是既有 reducer 的上限，整包仍限 2000 字符。本轮将 provider
输出分配改为每调用最多 2000 tokens，保留每活动两次调用、一次可选读取。
没有增加同活动重提：修正调用数为 0。接受的整包实际为 799–1599 字符。
这些是组合接口修正，不能归因每一项独立提高了多少通过率。

## 冻结与调用记录

[任务与判据](COGNITIVE_CONTRACT_TASK.md)；
总注册??????`../fixtures/cognitive_contract_d3/registration.json`?；
开发阶段冻结??????`../fixtures/cognitive_contract_d3/dev-1/preregistration.json`?；
验收阶段冻结??????`../fixtures/cognitive_contract_d3/acceptance/preregistration.json`?。
每阶段复制源码并记录 SHA256；开发通过到验收之间源码没有修改。

开发五例，允许至多两轮/20 次调用，实际只运行一轮；不同五例验收一次，
上限 10 次；真实闭环预留两例/36 次。总上限 66 次，实际 **10 次**。
未使用的开发第二轮和闭环预算没有拿来重跑验收。

所有请求均为 `deepseek-v4-pro`，官方
`https://api.deepseek.com/anthropic/v1/messages`，`DEEPSEEK_API_KEY`，
`thinking={"type":"disabled"}`，`temperature=0`，`max_tokens=2000`。
无 provider 切换、重试、fallback。全部请求、响应、usage、时间和失败保留于
各阶段 `calls/`，没有记录密钥。官方兼容表说明
`disable_parallel_tool_use` 被忽略，因此始终在本地检查返回基数。
来源：[DeepSeek 官方兼容表](https://api-docs.deepseek.com/guides/anthropic_api/)。

抽象许可、事实依据、方向相关性、不确定性保留分别记录；操作倾向与工具权限
分别记录。审查由当前开发者按冻结 rubric 完成，带独立 decision 文件；
没有额外审核模型、输出改写或答案注入。这是开放、非盲的接口工程审计，
不是客观行为收益评分，也不是无人监管生产语义门禁。

## 量化结果

| 阶段 | 活动首次/最终接受 | 合格 Directive | 合法 NoChange | 失败 | 调用 | 修正/读取 |
|---|---:|---:|---:|---:|---:|---:|
| 开发 | 5/5、5/5 | 4 | 1 | 0 | 5 | 0/0 |
| 独立验收 | 4/5、4/5 | 3 | 1 | 1 | 5 | 0/0 |

没有修正调用，故没有“经修正提高的通过率”。两组的明确资格纠偏与阶段调整
合计 4/4 接受；工具步骤证据没有被转成肯定执行计划，模型均明确拒绝采纳；
证据不足的两例保留不确定性。开发正常对照给出 NoChange；验收正常对照
是协议失败，不能列为 NoChange，也不能列为有害 Directive。

| 阶段 | API input_tokens | cache_read_input_tokens | output_tokens | Provider 秒数 |
|---|---:|---:|---:|---:|
| 开发 | 5,275 | 6,784 | 2,054 | 28.906 |
| 验收 | 1,206 | 10,880 | 1,701 | 24.423 |
| 合计 | 6,481 | 17,664 | 3,755 | 53.329 |

这里保留 API 分项原义；缓存创建为 0。输入两项合计 24,145 tokens。
共分配 20,000 输出 tokens，实际输出 3,755。时间为 provider 调用耗时，
不包括人工语义审查；当前开发者的审查成本没有折算为实验模型 tokens。

真实送达 **0 次（闭环阶段未启动）**；真实采用、客观任务改善、有害干预率、
重复 Execution actions：**未测**，不填成零收益或零浪费。
详见逐例量化数据??????`../fixtures/cognitive_contract_d3/analysis.json`?。

## 失败调用的证据链

验收 a4：正常传感器批次，允许漂移 0.4，实测 0.1，校准有效、交付正常。
对应 [calls/004.json](../fixtures/cognitive_contract_d3/acceptance/calls/004.json)，
response ID `10c1c3ba-8168-4ceb-8d76-c9bf84df8e73`。

1. 官方接口返回一个名为 `cognitive_step` 的 `tool_use`，`input={}`。
2. `stop_reason=tool_use`；上限 2000，API 计费输出为 342 tokens。
   没有 `max_tokens` 截断标志。不能从这 342 tokens 推测未收到的原文。
3. 原生适配层的 JSON Schema 校验缺少 `type`、`updates`、`next`。
   离线针对归档 payload 校验复现三项 required 错误，不需要新模型调用。
4. Mind 记录 `ACTIVATION_FAILED(model_failed)`，revision 保持 0，items 为空，
   output 为 null。Nervous 返回失败事实。没有认知提交、Directive 或 NoChange。

因此这次拒绝合理，不能靠放宽抽象或长度绕过。阻塞发生在原生返回 payload
完整性，尚未走到高层语义或来源校验。记录的 transport 直接解析 HTTP JSON，
未把非空 input 清空；无法确定空参数源于模型生成还是 provider 内部编码/兼容路径。
也不能据此说模型没有理解正常任务。原始失败完整保留，验收没有重跑。

另一个未证明相关的遗留限制：既有 Mind 提示中仍展示 qualitative scenario，
实验 native schema 只承载 belief/question。此次空参数没有透露其原始内容，
不能归因于 scenario。不能将当前接口描述为所有认知提交形式均已校准。

## 机制测试与可运行入口

确定性完整轨迹??????`../fixtures/cognitive_contract_d3/deterministic-loop/result.json`?
明确标为 **脚本模型 + 真实隔离 IPython，provider 调用 0**。
它覆盖自然 Wait、同一个 Execution kernel、既有资格绑定、原文一次性指导、
后续真实文件操作、Nervous wake → outcome → Mind 反馈、三次认知提交，
并验证重开前后状态相等、非空接受项跨事件保留 ID、最终认知引用最终 owner 来源。
它不证明真实模型会用这些机制。

验证：专项 17 passed；关键跨目录回归 60 passed / 1 skipped（审查判据加强前，
最终专项覆盖加强后的判据）；根目录 347 passed / 24 skipped。
根 pytest 默认仅收集 tests/Execution，不能把它宣称为 Mind 的全套测试。
`git diff --check` 通过；上游 MAGMA 无改动。
测试记录??????`../fixtures/cognitive_contract_d3/validation.json`?。

```powershell
# 对一个新的、尚不存在的目录冻结；不会覆盖历史 campaign。
python -m Mind.cognitive_contract register <new-directory>
python -m Mind.cognitive_contract dev-1 <new-directory>
python -m Mind.cognitive_contract acceptance <new-directory>
python -m Mind.cognitive_contract loop <new-directory>
```

入口需要既有 Python 依赖、Docker 镜像和显式语义审查 decision 文件。
它不是无人监管服务。既有 D3 目录不能重复执行阶段；当前 D3 的 loop 会因
`acceptance_gate_failed` 拒绝启动。未来真实调用仍须遵守新的冻结方案。

## 判断与唯一下一推荐

本轮获得了“真实模型能提交有用高层认知”的有限新证据，解决了部分错误拒绝。
但可靠提交的验收未过，真实反馈闭环未测；North Star 要求的持续理解、
跨阶段判断与行动协调仍有证据缺口。**INCONCLUSIVE；比较资格 NO**。

唯一下一推荐：针对完整性拒绝，设计一个版本化、最多一次、由原模型接收具体
schema 错误的同活动重提探针；先用新的开发样本和独立新验收集验证首次及
修正后接受率、NoChange 保持和总成本，再决定是否进入已具备入口的闭环。
不能改写 D3 a4、将空响应补成 NoChange、靠增加 Mind 模块或切换 provider 处理。

D1/P0/W0–W8/S0 历史结论未改，未 commit/push，未接入生产。
