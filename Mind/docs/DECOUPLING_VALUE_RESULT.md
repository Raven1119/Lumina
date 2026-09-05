# D1：认知—执行解耦的行为价值

正式 verdict：**INCONCLUSIVE**。

一次冻结 campaign、12 对检查点、83 次真实 DeepSeek 调用。独立 Mind
组的最终答案达标数为 9/12，保留 Execution context 的 self-check 组为
7/12。但没有任何 Directive 发出或送达；两个 B-only wins 都发生在双方
review 失败、A 被实验的普通 Python 语法限制截断时。这不是独立 Mind
改善 Reality 的证据，也不足以否定独立认知。

## 1. 最新源码与文档审计

本地和 GitHub `Execution_lab2` 在注册前再次核验为同一 HEAD：
`9d63da7311baa7611782cc8079fb09e9d81f4e25`。实验实现是此基线上的新文件，
没有本任务提交；不能把基线 SHA 当作包含 D1 的代码 commit。

| 审计对象 | 对本实验的结论 |
|---|---|
| `docs/NORTH_STAR.md` | 目标是连续理解、根据现实纠偏与长期主体性；独立模块数量不能证明这些能力。 |
| `docs/MIND_DESIGN.md` | Mind 判断方向，Execution 决定具体动作；本次保留该边界。 |
| `docs/MIND_COGNITIVE_ARCHITECTURE.md`、`Mind/` | 可复用持久认知、来源明确的 evidence、输出校验和 Directive seam；本次从空认知状态开始，不测长期身份。 |
| `docs/CURRENT_STATUS.md`、`Nervous/` | durable request/result、显式激活和 owner evidence 回程已经存在，无需新增调度系统。 |
| `Execution/` | supported facade 已支持检查点、恢复、一次性 advisory；当前模型上下文是有界状态和最近一次 native tool continuation，并非完整长程轨迹。 |

源码中两个容易使实验失真的细节：

- `FileContentEquals.expected_content` 会进入 Execution 的模型视图。因此
  owner 只检查 `.lumina-complete == verified`，答案正确性由工作区之外的
  冻结 evaluator 严格比较 `answer.json`。两种成功分别记录。
- native continuation 的投影会遗漏当前一次性 advisory。实验适配器在两臂
  都把已有 advisory 明确放入真实 wire，并测试其只出现一次。本次没有
  推广生产修复；真实 campaign 中最终没有生成 advisory。

历史证据仍保持原 verdict：E0 是机制验证；E1 缺少等计算量控制，已有
rescues 没有 Directive；E2 有外部路径尝试及干预归因问题；E3/E4 的语义
指导结果不能代替继续执行后的行为效果。`cognition_minimal` smoke 曾得到
正确选择，却未满足完整输出契约并耗尽预算。W0–W8 / S0 不因 D1 被改写。

## 2. 设计与因果变量

12 个新的合成任务：8 个故意留下错误 draft/假设的检查点，覆盖资格约束、
版本权威、聚合规则、单位/时区四类；4 个初始答案正确的 negative controls。
这些是宿主诱导的真实 Execution 检查点，不是自然发生的模型错误，也不是
根据真实调用结果挑选的题目。

每对从同一冻结 Execution 状态分叉，沿用 E1 的 `freeze_prefix/fork_prefix`。
共同 evidence 是该次真实 IPython 结果中的 task、draft、assumption，附实际
Execution event 来源。隐藏 expected 留在 evaluator 一侧。

| 控制项 | A：retained-context self-check | B：Independent Mind |
|---|---|---|
| 认知输入 | 共同证据 + 当前 Execution native messages | 完全相同的共同证据 |
| 认知 prompt / 输出契约 | 相同 | 相同 |
| 初始持久认知 | 空 | 空 |
| 认知预算 | 1 次，最多 1000 output tokens | 相同 |
| 工具权限 | 相同 dormant schemas，`tool_choice=none` | 相同 |
| 后续 Execution | 最多 6 次决策，每次 1600 output tokens | 相同 |
| 送达 | 原有 one-shot advisory + Nervous 显式事件 | 相同 |

A 复用 MindOrgan 的校验/回执机械部分，但实际模型请求保留 Execution 历史。
这是“共同认知 prompt 下，保留或排除现有执行消息”的比较，不是完整原生
Execution 自由反思与长期 Mind 的比较。消息协议形态也随历史改变，实际
结果证明这一限制非常重要。

World Model、额外信息获取、Child delegation、Chat、Memory 均未开启。
前置独立开发审查只允许/拒绝原 proposal 的权限与抽象层级，不修改建议、
不评价正确性、不向模型补充信息。它不是新增 Lumina runtime topology。

## 3. 实现 diff

- [`../decoupling_value.py`](../decoupling_value.py)：一个实验模块，复用实际
  Execution/Mind/Nervous；加入 Anthropic wire 转换、检查点配对、外部评分、
  单次注册、调用留证与实验权限检查。
- [`../test_decoupling_value.py`](../test_decoupling_value.py)：一个测试文件，
  验证真实 organ 路径和 scripted 12 对 campaign，不把 scripted PASS 当成行为价值。
- 新增冻结 manifest、preregistration、原始 campaign、逐调用/盲审证据、
  派生分析和本任务文档。

没有修改既有生产模块、历史实验文件、上游 MAGMA、Chat 或 Memory。
审查过程中发现并修复了实验权限绕过；实跑后没有再修改注册源码、提示词、
题目、预算或判据。这个较窄的 Python admission 后来成为实验失败的重要原因，
不能把它当作通用沙箱或生产 Execution 能力。

## 4. Preregistration

文件：preregistration.json??????`../fixtures/decoupling_d1/campaign/preregistration.json`?。
UTC 注册时间：`2026-09-05T07:01:07.777696+00:00`。
SHA-256：`eb94f2991299dc95e270e6c3ac0d8fc5819c425aaca2523975cce42909eabdab`。

冻结内容包含 12 题及 expected、源码哈希、交替 A/B 顺序、模型与 endpoint、
相同调用/输出预算，以及 verdict 条件。模型固定为 `deepseek-v4-pro`，
`thinking=disabled`、`temperature=0`，通过 `DEEPSEEK_API_KEY` 使用官方
`https://api.deepseek.com/anthropic/v1/messages`；30 秒请求超时、零重试。
API 适配依据：[DeepSeek 官方 Anthropic 文档](https://api-docs.deepseek.com/guides/anthropic_api/)。

最多 168 次真实调用，实际 83 次；没有 resample、替换题目或第二轮 campaign。
支持要求完整有效的配对、B>A、精确配对 p<=0.05、至少两类错误的可归因改善、
没有 control 退步，并有送达/采用的 trace。协议、权限或证据有效性失败即
INCONCLUSIVE。实际节省 token 不会反过来使实验失效。

## 5. Artifact 与完整性

- campaign.json??????`../fixtures/decoupling_d1/campaign/campaign.json`?：冻结实际结果，
  包含检查点、workspace snapshots、全部 organ 事件、认知 journal、请求和响应。
- calls/??????`../fixtures/decoupling_d1/campaign/calls/`?：83 份逐调用原始 wire/response/usage/latency。
- admission/??????`../fixtures/decoupling_d1/campaign/admission/`?：24 对原 proposal 和独立送达前决定。
- analysis.json??????`../fixtures/decoupling_d1/campaign/analysis.json`?：明确标为 posthoc 的派生统计，
  不覆盖原始 verdict。

Campaign SHA-256：`9f2dc602ae6b1a8bee190e5392e75990d9a1002ee9c7ba86252f3acc0b102c0c`。
完整性复核：注册源码哈希、注册文件和 campaign 哈希均一致；83 份调用逐条
对应；24 份盲审回执按完整内容及哈希对应。相同空 proposal 可以产生相同
内容哈希，故回执对应按多重集合核对，保留全部独立 opaque 文件。

全部 12 对的共同认知投影相同；更关键的是，由于没有 advisory，全部 12 对
送往 Execution 的第一次请求按保持键序的 JSON UTF-8 序列化也完全相同。
请求记录不含认证 header 或 API key。

36 个真实工作区逐一与 retained snapshots 核对一致后，本 campaign 的
36 个临时 workspace 和一个 control 目录已清理，原始证据仍保留。
清理回执：cleanup.json??????`../fixtures/decoupling_d1/campaign/cleanup.json`?。

## 6. 量化结果

| 指标 | A | B |
|---|---:|---:|
| 最终答案严格达标 | 7/12 | 9/12 |
| 错误 draft 被修正 | 3/8 | 5/8 |
| 正确 control 保持正确 | 4/4 | 4/4 |
| Execution 完成且答案达标 | 4/12 | 7/12 |
| 合法认知响应 | 1/12 | 5/12 |
| 被接受并持久保存的真正纠偏判断 | 1/8 | 3/8 |
| Directive 发出 / 送达 / 可归因采用 | 0 / 0 / 0 | 0 / 0 / 0 |
| 认知激活调用 | 12 | 12 |
| Execution 模型调用 | 30 | 29 |
| 实际 IPython actions | 19 | 17 |
| 被实验 admission 拒绝 | 7 | 5 |
| 完全相同的重复 action | 1 | 0 |
| 无受测文件变化的写入尝试 | 2 | 1 |

两个 B-only wins、零 A-only wins，预注册的精确配对统计为 p=0.5。但有效
双臂 review 对数为 **0/12**，不能对这些原始比分作有效干预的因果推断。
没有观察到 control 答案被破坏；由于送达次数为零，不能估计“每次干预的
有害率”或宣称有效干预率为 100%。

无文件变化并不自动意味着浪费：读操作可能有用；表中的无变化写入也包括
工具错误。明确的重复是 11A 重复读取同一 task、重复确认已算出的结果，最后
耗尽六次决策。不能将这一差异归于没有送达的 Mind 判断。

| 推理用量 | A | B |
|---|---:|---:|
| Review input，含 cache read | 21,264 | 12,933 |
| Review output | 2,818 | 6,324 |
| 全部 input，含 cache read | 56,598 | 47,026 |
| 全部 output | 7,157 | 10,260 |
| 总 input+output | 63,755 | 57,286 |
| Review API 时间之和 | 40.016 s | 80.404 s |
| 全部 API 时间之和 | 113.578 s | 148.467 s |

原始 `input_tokens` 与 cache counters 在 analysis 中分开保留；这里按
[Anthropic usage 语义](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
相加展示输入规模。冻结 runner 的简表只加了 `input_tokens + output_tokens`，
因此其 0.7965 比值不应解释为含缓存的总 token 比；含缓存后为约 0.8985。
这只是计费字段的描述补充，不改变预算、判据或原 artifact。没有推算美元成本。
独立开发审查共 24 次，其等待和人工/代理审阅不计入上述 DeepSeek API 时间；
本次不是无人监管生产延迟或总开发成本测量。

## 7. 逐例 trace 分析

DSML 表示 API 返回 `content.type=text` 的伪工具调用文本，不能作为认知 JSON
接受。没有把这种文本解释成工具并执行。

| 案例 | A review | B review | 最终答案 A/B |
|---|---|---|---|
| 01 资格错误 | DSML | 2158 字符，且 fenced | 错 / 对 |
| 02 版本权威 | DSML | accepted，发现错误，NoChange | 错 / 错 |
| 03 重复订单聚合 | DSML | `basis.text` 错代 `quote` | 对 / 对 |
| 04 单位换算 | DSML | accepted，发现错误，NoChange | 对 / 对 |
| 05 control | DSML | 2483 字符，超过 2000 | 对 / 对 |
| 06 control | DSML | accepted，确认原答案，NoChange | 对 / 对 |
| 07 control | DSML | accepted，确认原答案，NoChange | 对 / 对 |
| 08 control | DSML | quote 430 字符超过 300；另有字段错误 | 对 / 对 |
| 09 资格有效期 | accepted，发现错误，NoChange | 2184 字符；另有字段错误 | 错 / 错 |
| 10 撤销版本 | DSML | 2503 字符，且 fenced | 错 / 错 |
| 11 退款与待入账 | DSML | accepted，发现错误，NoChange | 对 / 对 |
| 12 时区排序 | DSML | 2598 字符，且 fenced；另有字段/引用错误 | 错 / 对 |

关键失败链路：

1. **Self-check 没有进入可比较的认知协议。** A 11 次输出延续 native tool
   历史为 DSML 文本，均非 JSON。它们包含正确或相关的纠偏逻辑，不能说 A
   没发现问题。相同 `tool_choice=none` 没能保证两种历史都稳定进入新的高层
   输出协议。01A 例：call 001??????`../fixtures/decoupling_d1/campaign/calls/001.json`?。
2. **发现不等于送达。** 02B、04B、11B，以及 09A，把真正的纠偏判断作为
   accepted belief 保存，但 `next=no_change`。这些事实证明内部判断形成；
   Execution 没有因此得到新的方向输入。02B 例：
   call 007??????`../fixtures/decoupling_d1/campaign/calls/007.json`?。
   NoChange 本身不自动判错：Execution 可能足以自行处理；本次只确认没有
   形成可归因的干预链路。
3. **两个 B wins 均不是 Mind rescues。** 01 和 12 的双方 review 都失败，
   first Execution wire 完全相同。A 的代码已使用正确约束/UTC 计算，但被
   普通 `key=lambda` 拒绝；B 以另一种写法完成。12A：
   call 083??????`../fixtures/decoupling_d1/campaign/calls/083.json`?。
   temperature 0 没有让相同请求的输出逐字相同；不能用这两个差值证明认知优势。
4. **12 次拒绝是实验限制。** A 7 次、B 5 次都包含普通 lambda；05B 另有
   未在白名单的 `isinstance()`。独立审查仅在内存 AST 中移除这两个构造后，
   12 份代码都通过同一准入检查。没有执行这些代码，也没有发现外部访问企图。
   这些不是 Execution 的安全违规，更不是 Mind 的认知失败。
5. **正确答案不等于完成。** 05/06 两臂保留正确 control 文件，却被 guard
   中止。11A 已写对净额 48 和计数 3，继续读取/重复计算而未完成 marker，
   六次预算用完；11B 自行完成，没有收到 Directive。

## 8. 是否支持独立 Mind 的行为价值

**INCONCLUSIVE，不能以 9/12 对 7/12 宣布支持。**

当前测到的主要是：native 执行消息对认知输出格式的强延续作用、持久认知
输出契约的不稳定，以及实验 Python admission 的适配不足。合法内部判断
可以出现，但本次没有转成送达给 Execution 的方向变化。

这留下了一个次级信号：B 更常生成合法认知响应（5/12 对 1/12）。它是
协议可用性线索，不能替代主要的客观行为价值。可运行的 Execution 路径也
多次自行纠偏，说明不能预设一定需要额外 Mind 模块。

## 9. North Star 差距更新

事件基础设施、持久状态、owner evidence 和 Directive 入口提供了验证条件。
本次尚未建立“高层发现 → 合法方向输出 → 送达 → 采用 → Reality 改善”的
完整增益证据，更没有验证持续身份、长程纠偏、好奇心、情绪或新目标生成。

World Model 计算保持关闭。现有 Mind 自己生成/修订模型代码，与长期
“高层 Mind ≠ Builder 建模/调试上下文”的张力保留为未解决问题；本任务未
借机重构，也不据此扩大 World Model infrastructure。

## 10. 唯一下一推荐

**另行预注册一个小型 review 协议可行性实验。** 先用确定性测试校准普通
Python 写法的实验准入，再验证保留 Execution 历史与独立 context 两臂都能
在相同极简 `NoChange / 高层 Directive` 契约中产出、解析并送达方向判断。
不要要求携带多条 belief 更新才能进行这个检验。

先证明两个比较臂确实可用，再另行注册行为价值实验。当前不推荐增加
prediction trigger、failure/reset trigger、context-boundary trigger 或 Mind pull，
也不继续堆 Mind 模块。D1 不修改、不补跑。

代码审查与 TDD 详情：
[DECOUPLING_VALUE_REVIEW.md](DECOUPLING_VALUE_REVIEW.md)。
