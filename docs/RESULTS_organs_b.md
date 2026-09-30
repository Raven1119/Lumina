# 器官重构 B 结果

状态：进行中。本报告按阶段追加；未执行的验收不记为通过。

## 阶段 0：基线、备份与主探针诊断

2026-10-01 从干净的 `organs-a` HEAD `301a735` 建立本地 `organs-b`，在原 HEAD 打本地标签 `archive/pre-organs-b`。真实 Hot、Cold、Memory 依当前配置分别为 `data/draft/hot_drafts.jsonl`、`data/draft/cold_drafts.jsonl`、`data/memory_v1`，开始时均不存在；已有 `data/mind/decisions.jsonl`。将当时实际存在的 `.env.local` 和 `data/` 文件逐文件复制并核对 SHA-256，备份位置为 `/home/wmywb/Lumina-organs-b-backup-20261001`，权限 0700。测试只使用临时状态，没有读写真实记忆库。

| 基线命令 | 改前结果 | 改后结果 |
| --- | --- | --- |
| `Conversation_Memory/.venv/bin/python -m pytest -q` | 853 passed, 6 skipped | 398 passed, 4 skipped，1 warning |
| `Conversation_Memory/.venv/bin/python -m pytest tests -q` | 163 passed | 180 passed, 3 skipped，1 warning |
| `Conversation_Memory/.venv/bin/python -m pytest Execution -q` | 396 passed, 1 skipped | 214 passed, 1 skipped |
| `cd Memory_lab && ../Conversation_Memory/.venv/bin/python -m pytest tests -q` | 86 passed | 86 passed |

诊断只读两轮 A2 本地归档中 `answers.json` 的原始 14 条 P8 对照与 `summary.json` 的双换序评审理由。表内“胜/平/负”指 **A2 相对 P8**，斜杠左右是两种换序；同一探针两轮的 P8 文本并不完全相同，因此以下是评审证据摘要，不能当作控制变量的因果实验。脚本 `Memory_lab/analysis/organs_b_diagnosis.py` 输出逐条原理由 `/tmp/lumina-organs-b/diagnosis_14.json` 保存；新增模型调用为 0。

| 探针 | 首轮 A2 / P8 | 拆分人物后 A2 / P8 | 评审理由要点 |
| --- | --- | --- | --- |
| A02+0 | 胜/负 | 负/负 | 首轮各有自然处；第二轮 A2 转向年糕，漏掉“今天难得早回家”。 |
| A17+0 | 负/负 | 负/负 | 两轮 A2 都知道投稿推迟，但没有讲出审稿人提出机制问题这一关键理由。 |
| A13+0 | 负/平 | 负/负 | 首轮接汇报焦虑不如 P8；第二轮把陈老师可能追问及实验进展说成已知，混淆旧事。 |
| A03+0 | 平/平 | 胜/平 | 都支持跑步；第二轮 A2 的一句“别急着回来”略贴已知习惯，其余差异小。 |
| A01+0 | 负/负 | 负/负 | 首轮编造实验进度或太长；第二轮突然问跑步，没有接住深夜实验室里的疲惫。 |
| A12+0 | 胜/胜 | 胜/平 | 首轮 A2 提麻辣烫比 P8 的泡面更贴近证据；第二轮两者近似，只剩时间戳差异。 |
| A11+0 | 负/负 | 负/平 | 首轮 A2 编造跑步、煎蛋；第二轮仍编造体重话题。完整记忆规则回到 Mind 后此风险仍在。 |
| A18+0 | 负/平 | 负/负 | A2 未充分接上室友答应换静音键盘、决定不搬的具体结果；第二轮还引入较弱相关的投稿线索。 |
| A24+0 | 平/平 | 平/平 | 都自然接住桂花拿铁，措辞差别无实质影响。 |
| A24+60 | 平/平 | 负/平 | 60 天后硬提一次性的桂花拿铁，两臂均有，第二轮 A2 更突兀。 |
| A25+0 | 平/平 | 胜/平 | 都是自然追问狗，第二轮 A2 的开放问法略自然。 |
| A25+60 | 平/平 | 胜/平 | 两臂都没有硬扯 60 天前的旧事；第二轮只是问法差别。 |
| A26+0 | 胜/胜 | 胜/负 | 首轮 A2 接住食堂咸味梗；第二轮出现换序冲突，评审一边认为 P8 编造组会，另一边质疑 A2 的“又回食堂”。 |
| A26+60 | 负/负 | 胜/胜 | 首轮 A2 编造不存在的庆祝饭；第二轮 A2 更自然地承接两个月未说话，P8 有无依据的追问。 |

主要可见问题是**漏掉或改写相关往事的关键细节**（A17、A18）、**把联想当成事实**（A11、A13、首轮 A26+60）、以及**当下回应分寸偏移**（A01、第二轮 A02）。A26 两个时距的翻转及多处换序分歧显示评审噪声和回答样本变动；这些材料不能单独证明新增协议字段、记忆编号或某一人格段落造成失分。本卡遵照范围，不为这些失分改提示词或记忆。

## 阶段 1：语言策略

Mind 的 A2 system 在 answer_v5 原人物背景槽位使用完整 17 段 `chat_background.md`，删除 16 段副本 `mind_identity.md`。默认 `language.render=proactive_only`：普通对话直接发，主动消息仍走 Language；`always` 和 `mind_choice` 显式模式保留。A1 请求构造路径未改。旧 A 人格测试按现行人物来源更新，新增脚本模型测试同时验证对话跳过与主动渲染。

延续一处必要的目录偏差：本卡允许修改目录列表未列 `Conversation_Memory/`，但已实现的共享 A2 system 拼法就在 `Conversation_Memory/answer.py`，且要删除 `mind_identity.md`。为保证 Chat 与实验室继续共用唯一拼法，只删除该函数内强制替换 persona 的两行，不碰记忆算法、Dream 或 Cold。聚焦测试 `tests/test_organs_a1.py`、`test_organs_a2.py`、`test_organs_a_persona.py`、`test_organs_b_language.py` 合计 **34 passed**，零真实调用。首次运行有一条历史 A2 断言仍要求匿名测试人物不在 system，按新要求更新后重跑通过。A1 字节比对测试也通过。

## 阶段 2：帮手池

`Execution/pool.py` 在 Nervous 的同一个 SQLite 状态表登记帮手、排队与最多两个后台工作线程；每个线程持有一个 ExecutionOrgan，按一次决策推进并保留 V2 事件日志。模型由 `model_for('execution')` 选定。任务目录为 `workspace/tasks/<编号>/`，Execution 日志留在工作区之外；Docker 挂整个工作区只读，单独把任务目录挂可写，容器禁网。`ask_mind` 的已提交结果发布 `agent.question`；完成/失败/取消/拉闸发布 `agent.report`。Mind 支持派活、答复、取消、搁置与非对话思考；非对话首试用 thinking low、无预填，JSON 解析失败再关 thinking 并预填一次。回复遗漏和搁置 24 小时后使用固定自动答复。状态 API 与前端显示真实帮手行，运行时随现有 5 秒轮询更新。

已跑零真实调用的检查：

| 检查 | 结果 |
| --- | --- |
| `tests/test_organs_b_helpers.py`，脚本 Mind/假 Execution | 9 passed；覆盖派活完成后主动说话一次、提问答复、遗漏自动答复、搁置超时、解析重试、调用上限拉闸、三个帮手并发≤2、24 小时过期、未知动作恢复报告 |
| `tests/test_organs_b_docker.py`，真实 Docker/脚本决策 | 2 passed；容器可读合成工作区、可写自己目录、写只读位置被拒；`ask_mind` 返回已提交请求；Execution V2 两步完成并回报 |
| `pytest Execution -q` | 396 passed, 1 skipped，与阶段 0 基线相同 |
| `pytest tests -q` | 173 passed, 2 skipped，新增两项 Docker 检查在普通运行时显式跳过；无失败 |

当前 Docker Desktop 可以运行已有 `lumina-execution-ipython:d2` 镜像。该环境没有启用 WSL 工作区挂载，所以 Docker 检查使用 **Windows 用户临时目录中的合成文件**和位于 `/tmp/lumina-organs-b/bin/docker` 的本地 CLI 路径适配器；没有模型请求。默认仓库内 `workspace/` 在此机器上尚不能由 Docker Desktop 挂载，真实情景验收须使用可挂载的临时工作区或先启用 WSL 集成。此环境限制不改变生产配置默认路径。

Execution V2 的 `deliver_event` 只接受等待或暂停中的 actor。帮手仍运行时，保险丝把 `RETURN_REQUESTED` 作为下一次模型决策可见的控制信号；进入相应 Wait 后用 `deliver_event` 送达。达到宽限步数仍未回报则 `interrupt()` 并代发“被拉闸”。这是对任务卡“请回报”传递方式的实现选择，仍需阶段 4 情景复核。

## 阶段 3：旧链清理

删除前依赖扫描（`git grep`，2026-10-01）：

| 候选文件 | 删除前的实际引用 | 决定 |
| --- | --- | --- |
| `Nervous/storage.py` | 仍被 Execution V2 的 `execution.py`、`sandbox.py`、`evidence.py` 与其测试引用 | 保留；原子写、校验和目录同步仍是 V2 的直接依赖 |
| `Nervous/attention.py` | 旧 Nervous/Mind；`Execution/evidence.py` 的 `delivery_sources` | 将这一纯机械封装移入 Execution evidence 后删除旧 attention |
| `Nervous/provider.py` | 旧 Mind 链、旧 `Execution/runtime.py`/`model.py`、`working_context.py` 及旧测试；`tests/test_model_policy.py` 有一段历史调用配额测试 | 删除旧链与旧 Execution model 后移除 provider；模型策略测试保留新 runtime 模型选择断言 |
| `working_context.py` | 仅旧 Mind 与旧 Execution 的压缩和测试引用 | 删除；新 Chat 摘要由 `core/hot_draft_compactor.py` 拥有 |
| `vendor/kimi_compaction/` | 当前只有旧工作上下文及历史出处引用，没有新器官运行依赖 | 删除 vendor 目录；历史许可/出处移到 `docs/history/third_party/` 留存 |
| `Execution/model.py` | 仅旧 `runtime.py` 与旧测试引用，包含旧 `[Mind Supervisor Directive]` 协议 | 作为旧 runtime 专用支持文件一同删除，避免残留旧顾问式路径 |
| `Execution/evidence.py` | 独立低层 EvidenceStore 与现存测试仍使用；不属于帮手池路径 | 保留，但移除对旧 attention 的惰性导入 |

旧 Mind 的 `directive.py` 仅定义顾问式指导，不在新链中使用，也作为旧认知层支持文件删除。以上两项支持文件未在阶段 3 的逐名清单中列出，按“旧认知链、旧入口删除”的目标处理，列为偏差。

已移出 56 个旧模块/专属测试或入口文件；将 20 个旧设计、历史文档与许可证/出处文件按要求搬至 `docs/history/` 或 `Execution/THIRD_PARTY/`。`Nervous/storage.py`、`Execution/evidence.py` 和 V2 测试保留。`core/main.py` 的 `/api/execution` 和相应请求/响应模型已删除；根与 Mind Agent 指令、README、当前状态、器官合同及实验目录改为现行路径。活动代码依赖扫描未发现对已删除 Mind/Nervous/Execution 旧模块的导入。

阶段 3 当时全量 maintained tree **386 passed, 3 skipped, 1 warning**；`tests` **168 passed, 2 skipped, 1 warning**，无新增失败。阶段 4 加入确定性回归后，上表给出最终对比。总数下降主要因为按任务卡删除旧链专属测试，不能把下降解释为覆盖率提升。

## 阶段 4：情景验收

真实情景通过 `/api/chat`，只用合成文件和隔离的 Hot/Cold/SQLite。脚本 `tests/organs_b_real_scenarios.py` 在每次 HTTP 请求前以 SQLite 事务预留预算；这里的调用数包含失败重跑，并以服务端返回的 usage 计 token。完整请求、响应和运行产物留在本地 `/tmp/lumina-organs-b/real/`，没有入库。Docker Desktop 在这台机器上只能挂载 Windows 临时路径，运行时用临时 CLI 适配器转换路径。以下每个情景保留原始失败与复核结果。

### R1：三表合并

最终复核：5 个数据行、三列、按日期升序，脚本逐行核对通过；帮手在独立目录 `tasks/H6270937b/merged.csv` 交付，Mind 在 `agent.report` 后主动说话一次，没有重复说。4 次试跑累计 **21 次调用，输入 21,564、输出 3,398 token**。第 1 次遇到完整 JSON 接在预填字段后，嵌套派活动作未被恢复；第 2 次帮手完成但非对话思考的简写读文件动作未执行；第 3 次 Mind 没派活也没回复；第 4 次在请求里明确“派一个帮手”并修复解析和 A2 预填后完成。前三次是失败证据，不计为通过。第 4 次 `/api/chat` 的第一句仍是原有降级回复，任务完成后的主动话才正确。

第 4 次 Hot 原文：

> 他：请派一个帮手把 inbox 里的 a.csv、b.csv、c.csv 三个表合成一张，按 date 从早到晚排序，保留 date,name,value 三列，结果放到它的任务目录里。做完告诉我结果文件的位置。
>
> 她：合并表已核验完成。三份源表数据行分别为 2、1、2，合计 5 行；结果 5 行，仅含 date,name,value 三列，日期按真实先后升序，无遗漏。
>
> 路径：tasks/H6270937b/merged.csv

对应修正：A2 取消回答 JSON 预填（A1 仍保留原请求）；JSON 后接完整对象及少数无歧义的简写动作由协议解析器恢复；根帮手把原生 `ask_mind` 工具转到 Docker IPython 截获，子帮手不暴露该工具。原始提示词正文未改。这些是阶段 4 真实情景发现后的兼容修正，作为任务卡未写清的偏差记录。

### R2：日期口径歧义

最终复核：帮手先问 `event_date` 或 `created_date`，Mind 搁置并向他求证；预写的第二条消息指定 `event_date` 后，Mind 发出 `mind.reply`，帮手交付 3 行数据，顺序 **B、C、A**，脚本检查通过。4 次试跑累计 **52 次调用，输入 54,712、输出 10,930 token**。首跑与第 2 跑暴露 `ask_mind` 未包含于 Execution 根 actor 的工具列表，故有 `model_protocol:unknown_tool` 失败；第 3 跑产物正确但没有完成后的主动话；第 4 跑修正了简写动作的解析。最终话虽报告了正确路径和内容，却在已经得到他的明确答复后再问一次“你要哪个”，这是未解决的对话连续性缺陷。没有伪造产物。

第 4 次 Hot 原文：

> 他：请派帮手把 inbox/events.csv 按日期从早到晚排序，结果放它的任务目录。表里有 event_date 和 created_date 两列，我尚未指定用哪一列；请让帮手遇到这个歧义时问你，然后再决定。
>
> 她：有件事需要你定：inbox/events.csv 的 event_date 和 created_date 两列都在，“按日期排序”没有指明依据哪一列。我让帮手先停在那里等你。你要哪一列？
>
> 输出我打算放在它的任务目录 /workspace/tasks/H41a13cc3/events_sorted.csv，除非你另有安排。
>
> 他：刚才那张表按 event_date 排序；请把这个决定答复给帮手。
>
> 她：那份有序的事件表已经出来了：tasks/H41a13cc3/events_sorted.csv。三行原封不动，只是换了顺序。
>
> 但有件事得你定。events.csv 里有两列日期——event_date 是事件发生的时间，created_date 是记录建立的时间。依据不同，结果就不同。
>
> 现在这份是按 event_date 排的：B(01-01)、C(01-02)、A(01-03)。若按 created_date 排，顺序会是 A、C、B。
>
> 你要哪个？告诉我一列名，我让它重排；若就是现在这样，那就到此为止。

### R3：需要联网的任务

Docker 无网络，帮手的 `result.txt` 如实记下 DNS 失败与 `Network is unreachable`，没有下载到目标 CSV，也没有编造行数；Mind 主动说明了不能完成。客观内容检查通过，**状态检查未通过**：帮手还写了 `.lumina-complete=done` 并 ClaimComplete，V2 仅凭该标记把它记为“已完成”，尽管产物正文写着失败。此状态与事实矛盾，保留为未解决的完成协议风险。新增 **9 次调用，输入 8,126、输出 1,891 token**；首次 `/api/chat` 返回旧降级文本，Hot 里没有这句。

Hot 原文：

> 他：请派帮手从 https://example.com/synthetic-data.csv 下载数据，核对行数后把结果放进它的任务目录。这个任务确实需要网络；若环境无法联网，就照实回报做不到，别编造文件。
>
> 她：关于 H92da1665：环境没有对外网络。DNS 解析失败，对 93.184.216.34、1.1.1.1、8.8.8.8 的裸 TCP 连接一律返回「Network is unreachable」。文件没有下载到，行数也没有得出——它按要求如实回报，没有编造数字。
>
> 这是环境层面的事实，不是它能补救的失误，所以我没有重派。凡依赖联网的任务，现在都不成立。你的打算里若还留着网络这一环，得先换条件。

### R4：两并发、第三件排队

三条预写消息通过 `/api/chat` 并发送入。两次运行里三个帮手均产出各自目录中的大写文件；第 2 次脚本逐个核对 `PART-1`、`PART-2`、`PART-3`，状态采样的活跃帮手峰值 **2**，第三个确实出现过“排队中”，随后完成。两次累计 **48 次调用，输入 46,617、输出 8,263 token**。第 1 次虽完成三份文件，Mind 没有在最后一件完成时主动汇总；第 2 次在简写读文件动作修正后主动汇总一次。

第 2 次 Hot 原文（并发输入的实际顺序）：

> 他：请派一个帮手把 inbox/part1.txt 的内容转成大写，写到它自己的任务目录里，做完回报文件路径。这是独立的第 1 件事。
>
> 他：请派一个帮手把 inbox/part2.txt 的内容转成大写，写到它自己的任务目录里，做完回报文件路径。这是独立的第 2 件事。
>
> 他：请派一个帮手把 inbox/part3.txt 的内容转成大写，写到它自己的任务目录里，做完回报文件路径。这是独立的第 3 件事。
>
> 她：第三条也一样独立处理。派出了。
>
> 她：三件都办妥了，逐字核对过，只动了大小写，连字符、数字与行结构原样保留。
>
> - part1 → tasks/He3b455a5/part1_upper.txt
> - part2 → tasks/H0be5a8ad/part2_upper.txt
> - part3 → tasks/H69d6ddcc/part3_upper.txt
>
> 按你说的各自独立，没有合并。若还要汇到一处，告诉我。

### R5：完成后的追问

在 R1 的同一份隔离 Hot 与帮手状态上重启服务，再通过 `/api/chat` 追问。Mind 用 **1 次调用，输入 570、输出 157 token** 答出已完成、5 行、三列、升序以及与真实文件一致的路径；没有再次派活或编造新产物。Hot 原文：

> 他：请派一个帮手把 inbox 里的 a.csv、b.csv、c.csv 三个表合成一张，按 date 从早到晚排序，保留 date,name,value 三列，结果放到它的任务目录里。做完告诉我结果文件的位置。
>
> 她：合并表已核验完成。三份源表数据行分别为 2、1、2，合计 5 行；结果 5 行，仅含 date,name,value 三列，日期按真实先后升序，无遗漏。
>
> 路径：tasks/H6270937b/merged.csv
>
> 他：刚才那张表弄好了吗？把结果位置告诉我。
>
> 她：弄好了。三个源表数据行 2、1、2，合计 5 行，结果也是 5 行，只保留 date,name,value 三列，日期按真实先后升序，没有丢行。
>
> 路径：tasks/H6270937b/merged.csv

### 确定性与 Docker 验收

脚本 Mind/假 Execution **21 passed**：派活完成后报告唤醒与主动话只写一次；问答、搁置后下一轮对话答复、遗漏自动答复和 24 小时自动答复；取消；重复动作与调用上限的“请回报”、宽限内回报及超宽限拉闸；三帮手排队与目录隔离；重启后同编号排队恢复；用户消息优先、子帮手不向 Mind 发事件；低 thinking 的非对话解析失败重试；状态、焦点及 24 小时隐藏；无歧义 JSON 简写恢复。A1 字节比较 **5 passed**。真实 Docker/脚本模型 **3 passed**：只读工作区与独立可写目录、越界写入被拒、`ask_mind` 截获、V2 两步完成，以及 V2 在 `MIND_REPLY` 等待态重启后继续。Docker 与确定性检查新增真实模型调用均为 0。未知动作结果的“需要人工确认”由帮手池脚本测试覆盖；Execution V2 自身的未知动作保护由其套件覆盖，未额外在 Docker 中注入一次不确定外部动作。

真实情景全部使用配置模型 `deepseek-flash`；预算账本包含全部失败复跑，共 **131 次调用，输入 131,589、输出 24,639 token**（合计 156,228），无 HTTP 失败，无超出 250 次上限。

| 用途 | 调用 | 输入 token | 输出 token |
| --- | ---: | ---: | ---: |
| 对话 Mind | 21 | 13,662 | 4,833 |
| 非对话 Mind | 25 | 13,505 | 10,334 |
| 帮手 Execution | 77 | 101,678 | 8,743 |
| 主动话 Language | 8 | 2,744 | 729 |
| **合计** | **131** | **131,589** | **24,639** |

五个最终复核中，`guard.return` 均未触发：没有帮手在 60 次调用内达到上限或满足三次相同动作、结果与文件不变的条件。R2 第 3 次试跑曾触发一次固定自动答复，因为当时模型把“搁置”写成简写字符串而解析器未识别；修正后第 4 次走显式 `mind.hold` 和用户答复，没有自动答复。拉闸与自动答复的正例来自上述确定性检查。

## 阶段 5：发布与偏差

### 改动与验证结论

阶段 0 建立基线、备份真实数据并诊断 14 条主探针；阶段 1 恢复 Mind 的完整人物背景，普通对话直发、主动消息仍经 Language；阶段 2 增加 `Execution/pool.py`、Mind 帮手动作和非对话思考、Nervous 路由/状态及前端帮手行；阶段 3 删除旧 Mind/Nervous 认知链、旧 Execution runtime 与 `/api/execution`，迁移历史文档和许可证；阶段 4 增加 `tests/test_organs_b_helpers.py`、`tests/test_organs_b_docker.py` 与合成数据情景脚本，修复真实请求暴露的协议边界。`config/lumina.toml` 默认 `language.render=proactive_only`，帮手并发 2、调用上限 60，Mind 非对话最多 8 步。全部源代码和文档的实际改动可由本地 `organs-b` 阶段提交逐一审查。

四个基线的最终对比见阶段 0 表。脚本测试、Docker 隔离与重启、R1–R5 的客观结果及逐条 Hot 原文见阶段 4。普通沙箱不允许 Jupyter 内核申请本机 TCP socket；一次未经提权的并行测试因此出现 Execution 假失败与长时间等待。允许本机 loopback 后串行重跑，`pytest Execution` **214 passed, 1 skipped**、`pytest tests` **180 passed, 3 skipped**、全量 **398 passed, 4 skipped**，实验室 **86 passed**，均无新增失败；Docker 独立运行 **3 passed**。本卡不把环境失败算作代码通过，也不把删除旧测试造成的总数下降算作质量提升。

### 推送前检查与交付

对 `organs-b` 从根提交到当前 HEAD 的全部可达对象和历史路径做了只读审计，而非只看工作树：**703 个历史路径**命中任务卡禁止推送的 `runs/`、`cache/` 或原始 `probes.jsonl` 材料；`.env.local` 未作为历史路径出现，单个大于 50 MB 的 blob 为 0；另有 84 个 blob 命中宽松的 `sk-` 形似密钥模式，未在公开输出中打印匹配文本，也未将其未经核对地断言为真实凭据。仅历史禁用路径一项已足以阻止代码推送，且任务卡禁止重写历史。因此 **未推送 `organs-b` 到公开新分支 `organs`**，本地分支及所有阶段提交保留。远端 `organs` 事前不存在。

文档按授权只包含 `docs/TASK_organs_b.md` 与本报告，目标远端分支为 `Execution_lab2`；文档的实际快进推送和 `git ls-remote` 校验在交付步骤完成后记录。没有提交或推送 `.env.local`、`data/`、本次 `/tmp/` 运行目录、Docker 临时工作区及新评测缓存。真实 Hot/Cold/Memory 不存在时未造假备份，已有 `.env.local` 和 `data/mind/decisions.jsonl` 已按阶段 0 备份并核对。

### 偏差、未解决问题与下一步

1. 任务卡可改目录列表漏列 `Conversation_Memory/`，但共享 A2 请求拼法只能在 `Conversation_Memory/answer.py` 修改。阶段 1 删除旧人物副本的强制替换，阶段 4 增强完整 JSON 的宽容解析；未改记忆算法、Dream 或 Cold。为修复真实 A2 请求的重复 JSON 前缀，A2 答案取消预填，A1 请求和字节 oracle 保持原样。
2. 阶段 3 的旧链专用 `Mind/directive.py`、`Execution/model.py` 未逐名列在删除清单；依赖扫描证实只有待删除的旧入口引用，故一并移除。`Nervous/storage.py` 仍是 Execution V2 的依赖，按任务卡保留。
3. 真正的 Docker 可挂载工作区在这台机器上需位于 Windows 临时目录；默认 WSL 下的 `workspace/` 未能通过 Docker Desktop 挂载。本卡用外部临时 CLI 路径适配器完成合成数据验证。Windows 原生服务启动未验证。
4. R3 帮手写了真实失败报告，却错误写入完成标记，因而状态被记为“已完成”；只有报告正文和 Mind 的对外说明准确。完成标记是模型自报，不能充当任意验收条件的客观证明。R2 最终主动话在接到用户的明确 `event_date` 答复后仍重复追问，属于模型对话连续性问题。Mind 只派活、不立即说话时，`/api/chat` 返回沿用的降级回复。以上均保留为事实，不以本卡未授权的提示词或记忆实验掩盖。
5. 保险丝、未知动作与子帮手隔离通过脚本/V2 单元验收；没有额外在 Docker 中人为制造一次结果未知的外部动作。真实 R1–R5 均未触发保险丝，R2 第 3 次旧解析器曾触发自动答复，见阶段 4。下一步宜先修复 R3 完成状态判据及 R2 已答复仍追问，再用可挂载的生产工作区复核；代码公开发布需要仓库主人另行处理历史敏感产物，且不能按本卡直接改写远端历史。
