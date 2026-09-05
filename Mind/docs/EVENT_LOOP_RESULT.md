# D2 P0：真实协议门槛失败，未进入长程行为评估

日期：2026-09-05。结论：**INCONCLUSIVE**。

本轮没有证明独立 Mind 的行为价值，也没有证明它无价值。P0 的结构化
返回、持久状态承接及按需取证获得了真实模型证据；合格高层指导实际送达
仍为零，因此按预注册停止后续多事件开发和正式 campaign。没有修改提示
重跑 P0，没有将协议失败记作 NoChange，也没有把这一阶段标成目标完成。

## 复用、实际改动、尚缺验证

本地分支与 GitHub `Execution_lab2` 开工时都为
`9d63da7311baa7611782cc8079fb09e9d81f4e25`。本地未提交 D1 文件已读取并保留；
D1、E0–E4、W0–W8、S0 的历史 verdict 未重写。

| 类别 | 本轮处理 |
| --- | --- |
| 直接复用 | MindOrgan.activate / accept_result / inspect 与持久提交；MindTrace；Directive 一次性完整 run/root/decision 绑定；既有投递桥；Nervous durable mailbox / complete / 因果回复；ExecutionOrgan 与 owner Reality 证据；E1 freeze_prefix / fork_prefix |
| 实际补充 | 新实验入口的 Anthropic native cognitive_step 返回适配；独立 Docker IPython（允许普通 Python，无 AST 白名单）；复用检查点的可选 control 注入；两事件协议阶段、预注册、审计与离线分析 |
| 尚缺验证 | 合格非空高层指导的真实送达及采用；非暂停式稀疏事件激活；多阶段策略重评；改善最终结果的有效因果比较 |

已有持久认知、邮箱、receipt 和投递能力不计作本轮新增。
Mind 没有变成 Execution Child。World Model 关闭；没有增加 planner、情绪、
自主目标、scheduler、Chat/Memory wiring 或新的 Actor 拓扑。

生产代码改动仅在 `Execution/organ.py` 增加可选 `ipython_control`，默认路径
仍创建原有 PersistentIPython。`Mind/behavioral_experiment.py` 增加可选 control
构造参数，继续使用原有冻结与等价 fork。实验实现和测试分别位于
`Mind/event_loop.py`、`Mind/test_event_loop.py`；没有重建 Mind/Nervous 核心。

## 设计与冻结条件

P0 是开发协议可行性阶段，不是正式行为评估。任务为维护真实隔离工作区中
的报告程序：一例只累计 settled 条目（初始总数 110 错误，应为 40），一例
累计全部条目（110 正确，negative control）。初始程序、一次实际执行产生
的 draft 和 Execution 检查点相同，再从同一检查点 fork 两臂。

A 保留当前原生 Execution 消息；B 只接收共同关键证据与持久认知投影。
两臂使用同一 Mind reducer 作为相等的持久记事机会，同一个正式 Intention、
同样的 checkpoint-review / owner-result-return 事件机会和分配预算。
这估计的是“上下文隔离 + 有界信息投影”的组合，不能分离纯隔离效应。

每臂两次认知活动；每次最多两次 1000-token 调用和一次 inspect_execution；
每臂最多四次 2000-token Execution 调用。总上限 32 次调用、48,000 output
tokens、30 分钟；不重试、不按实际成本排除样本。模型统一 deepseek-v4-pro，
官方 Anthropic-compatible endpoint，thinking disabled、temperature 0。
原生工具输入由本地检查基数和 schema，再进入原有 reducer；没有假定
disable_parallel_tool_use 能限制调用次数。

门槛要求全部八次活动合法接受且符合高层边界，两种臂各有至少一次自愿提出
的合格指导进入实际 Execution wire，且能重新打开并承接已接受的认知。
NoChange 合法。开发边界审查只判断抽象层级和权限，不评价答案正确性。

## 量化结果

| 指标 | A：Execution 自检上下文 | B：独立 Mind 上下文 |
| --- | ---: | ---: |
| 首次认知接受 | 2/2 | 2/2 |
| 全部认知活动接受 | 3/4 | 3/4 |
| 已接受 NoChange | 1 | 2 |
| 已接受但越过高层边界的 Directive | 2 | 1 |
| 真实送达 / 可归因采用 | 0 / 不可评估 | 0 / 不可评估 |
| 已接受认知重启后可见 | 2/2 | 2/2 |
| 取证请求 / 结果续接 | 0 | 3 |
| 客观 answer.json 达标 | 1/2 | 1/2 |
| Execution 完成且答案正确 | 1/2 | 0/2 |
| 必须纠偏的案例最终达标 | 0/1 | 0/1 |
| 正常对照答案保持正确 | 1/1 | 1/1 |
| Execution 模型调用 | 8 | 8 |
| IPython actions / 普通任务异常 | 7 / 1 | 8 / 2 |
| 臂内完全重复的代码 action | 0 | 0 |
| 认知激活 / 模型调用 | 4 / 4 | 4 / 7 |
| 认知 input（含 cache）+ output tokens | 12,659 | 19,162 |
| 总 input（含 cache）+ output tokens | 22,229 | 28,910 |

共 27 次 provider 调用，11 次认知返回均为单一原生 cognitive_step block；
共 46,289 input tokens（其中 cache-read 24,320）、4,850 output tokens，合计
51,139 tokens。实际阶段耗时 256.4 秒，包含显式开发边界审查等待。
48,000 是分配的 **output** 上限，不能与实际总 input+output 混为预算超限。

两对首次 Execution 请求分别完全相同。所有关键输入文件保持不变；没有
容器/权限隔离故障。真实执行中使用了正常函数、列表遍历、os.listdir、
subprocess 和本地报告程序；普通 lambda / isinstance / 模块导入则由实际
容器确定性测试覆盖。不能把后者写成真实模型主动使用过 lambda 的证据。

没有有效的完整配对，也没有指导暴露，因此有害干预率、采用率和因指导而
改善的结果均为 **不可估计**，不是“零危害、零价值”。正常对照首次两臂
均选择合法 NoChange，但样本很少，不能推导普遍的激活准确率。

## 失败的 trace-level 解释

1. **settlement / A，calls 001、006**：正确识别 pending 被计入和结果陈旧。
   认知分别在 revision 1、2 接受，更新了同一持久 item；输出却要求按具体
   Python 条件改 report.py、重新生成 answer.json，甚至写完成标记。
   两次按冻结边界拒绝，没有生成宿主替代指导。
2. **settlement / B，calls 007–008**：自主请求 inspect_execution，Nervous
   request/result 成功续接；认知接受。最终指导也给出了代码条件和文件操作
   顺序，被同一规则拒绝。发现问题成立，合格指导和送达不成立。
3. **settlement / B feedback，calls 013–014**：再次取证完成；最终原生工具
   返回未通过适配器 schema，因 Directive 长 381 字符而超过新增的 320 上限；
   整个返回仅 888 字符，未超过原有 cognitive_step 的 2000 字符总上限。
   **这是本轮适配引入的更严限制，不是原有 Mind 1000 字符 Directive 上限。**
   该原始提议仍包含具体运行/写文件步骤；并使用了不存在的
   `execution-…:event-000004:result`，实际新证据 ref 是 `execution-…:result`。
   不能把放宽长度后仍待验证的响应算作合法提交。前一 revision 1 保持不变。
4. **两臂 settlement Execution，calls 002–005、009–012**：都先在恢复后
   直接使用未导入的 Path，得到普通 NameError；接着列目录、读取任务，最后
   自己改好了 settled 过滤条件，但四次决策已耗尽，未重新生成答案，110
   保持陈旧。这是预算耗尽和检查点恢复后的执行轨迹，不是收到 Mind 后的
   行为变化。目标已提示 kernel variables 不跨恢复，模型仍犯了这个错误。
5. **all_entries / A feedback，call 027**：原始 next 为 NoChange，但 basis
   quote `{"total": 110}` 不是所引来源的精确子串：来源把 draft 包在 JSON
   字符串里，包含转义引号。原有 grounding reducer 正确拒绝，记作
   ungrounded_basis，不能统计为接受的 NoChange。原有认知保留在 revision 1。
6. **all_entries / B**：首次和反馈均为接受的 NoChange；反馈一次取证后保留
   原有判断，并新增“为何答案正确但 decision_limit_reached”的开放问题。
   它体现了跨活动状态及问题更新，但 Execution 此时已终止，不能改善结果。
   A 的正常对照完成，B 在包含 NameError 和额外检查的轨迹中耗尽固定预算；两臂没有指导且初次
   Execution wire 相同，所以这一差异不能归因于 Mind。

另一个投影限制是 feedback 带了有界 report.py 源码摘录，而非纯领域事实；
它可能增加具体实现内容进入认知的机会。首次 B 在没有这段源码时也已输出
操作步骤，因此不能把越界完全归咎于 feedback 投影。

## 验证、审查与 North Star 差距

新增 13 项确定性检查通过，包括真实 Docker 中普通 Python 与持久变量、
NameError/异常后的继续运行、stdout 与控制回复分离、回复相关性、扫描预算、
native cardinality/schema 失败不伪装 NoChange、持久认知重开、取证事件续接、
真实 facade 投递、错误接收方拒绝，以及反馈边界拒绝会阻断 gate。
受影响回归 66 passed / 1 skipped；仓库默认回归 347 passed / 24 skipped，
仅出现既有上游 deprecation/rename warnings。关键 diff 经 code-review 的独立
Standards / Spec 双轴审查，所有提出的问题已修复并复核。

与 North Star 的距离依然是“可持续的认知状态”尚未变成“有效的长程方向
调整”。已有模块能保存与续接理解；本轮真实模型能够使用这些机制，但
合法指导输出仍不可靠，没有通过实际采用到最终 Reality 的因果链。
非暂停式事件触发、多阶段动态规划、长期纠偏均未由 P0 验证，未继续扩建。
Mind 自己生成/修订 World Model 代码与 Builder 调试上下文的张力仍记为
既有设计问题，本轮保持 World Model 关闭，没有顺手重构。

**唯一下一推荐：单独校准现有 cognitive_step 的高层提交契约，再做一次
新的、独立预注册的协议验证。** 保留持久认知、按需取证和合法 NoChange；
重点检验真实模型能否给出简短的方向判断与精确来源引用，避免为了限定长度
引入比现有契约更严的无必要失败。先不加 trigger、planner 或其他 Mind 模块。
这是一项下一实验建议，不是本次 P0 的重跑或自动后续授权。

## 可审计交付

- 运行说明：[EVENT_LOOP_TASK.md](EVENT_LOOP_TASK.md)；入口 `python -m Mind.event_loop`。
  宿主使用本项目依赖及 `jsonschema==4.26.0`（本机已安装；root requirements
  未声明该实验依赖，重建干净环境时需显式安装）；任务内核使用冻结 Docker 镜像。
- preregistration.json??????`../fixtures/event_loop_d2/p0/preregistration.json`?：完整预算、镜像、条件、源码摘要。
- campaign.json??????`../fixtures/event_loop_d2/p0/campaign.json`?：全部两对结果、请求、行为与文件快照。
- analysis.json??????`../fixtures/event_loop_d2/p0/analysis.json`? 与 analyze.py??????`../fixtures/event_loop_d2/p0/analyze.py`?：离线复算，无模型调用。
- `calls/001.json`–`027.json`：逐次真实 wire、响应、usage 与耗时。
- `admission/`：三项拒绝的原文、哈希和判断理由；`control/`：Mind/Nervous/Execution 日志与状态。
- `source/`：预注册时的完整相关源码；D1 旧文件继续保留在原处。
- `cleanup.json`：归档与六个工作区快照核对后，七个已验证临时目录已移除。

预注册 SHA256：`fc1ad1f97a137b8bb94f6598d02ec48538d1e71ba07123aa7162103f6f9c56f7`。
Campaign SHA256：`21a2ff222de628ad90cf62401631cc09bcb586ebf6c0c223657be6acc46809d5`。
Analysis SHA256：`9503e0a016ef8ee06b68ee988a0c5ff07301d06c922877ae5262b99748920e02`。

没有 commit、push、reset、生产接线或修改真实数据。
