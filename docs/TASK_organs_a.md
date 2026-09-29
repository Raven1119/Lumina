# 任务卡：器官重构 A —— Mind 接管对话

来源：仓库主人本轮任务卡（2026-09-30）。本卡是阶段 A 的完整授权；中途不询问，普通歧义自行选择合理实现并记入 RESULTS_organs_a.md。

## 目标与范围

Chat 的每一轮变成新器官结构中的一次 Mind 对话思考。消息以事件进入新 Nervous，Nervous 叫醒 Mind。Mind 可读工作区文件、主动回忆、说一句或几句话，结束留下思绪与交接。人物背景由 Language 持有，每句话是否重组由 Mind 决定。/api/status 与前端显示真实状态和焦点。

先 A1 只换结构、行为不变，再 A2 增加协议并移走人物背景，一次做完。

本卡不做帮手池、委派、Execution 改动；不删除或改变旧认知链（python -m Mind、旧认知层、Nervous Stage1、Execution/runtime.py、/api/execution）；新代码不得依赖旧链。旧链阶段 B 再删除。不做其他触发器、自建触发器、审批、人格、冥想、进化；不改记忆算法、Dream 逻辑、Cold 已有数据。

允许修改 core/、edge/static/、prompts/、tests/、docs/；Mind/、Nervous/ 只新增不同名文件；新建 Language/、config/。Conversation_Memory/ 仅回答拼法与提示词接缝；Memory_lab/ 仅新回答臂盲评。根目录仅 AGENTS.md、README.md、.env.example、.gitignore、pyproject.toml、requirements*。

## 先读，再动

1. 根 AGENTS.md、README.md、docs/CURRENT_STATUS.md、TASK_memory_v1.md、RESULTS_memory_v1.md。
2. core/main.py、message_runtime.py、model_client.py、memory_adapter.py、draft_store.py、hot_draft_compactor.py、cold_draft_store.py、contracts.py，edge/static/app.js、prompts/chat_background.md。
3. Conversation_Memory/facade.py、answer.py、answer_v5 提示词、Dream/runner.py。
4. 只读借鉴 Nervous/storage.py 的原子写与完整性检查、provider.py 的响应落盘与恢复；新代码不依赖它们。
5. 实验室盲评规程和 P8 缓存。

在包含 memory v1 的本地分支或后继分支工作，先确认工作树干净。

## 决定

1. 阶段 A；帮手池和删除旧链留给 B。
2. chat_background.md 不注入 A2 Mind，只注入 Language。Mind 通过“重组”决定，不另加说话方式要点；口吻明显变差照实报告，本卡内不补救。
3. A1 请求与 memory v1 逐字节一致，人物背景暂在，不加状态块或协议字段，零真实调用。A2 再盲评。
4. SQLite WAL，每线程独立连接，默认 data/nervous/lumina.sqlite；事件无条数上限。器官间只通过事件，读文件/回忆可直接执行但必须记痕迹。
5. 仅事件触发器，种类写注册表：user.message 交给 Mind；第一次 mind.spoke 后检查自动 Dream，沿用 v1 条件和锁。
6. Mind 一个串行工作线程；思考中新消息在下一步前插入（A2）。
7. answer_v5 JSON 增加重组、行动、思绪、带着（附录 A）。最多 4 步；读/回忆有结果则续步，否则结束；只保存末步交接。第 4 步仍有动作时跳过并补“（这次想到一半被打断了）”。
8. 重组=true 时 Language 调用一次模型（附录 B），默认继承对话模型，可配置。Hot 写最终文本，痕迹记原文与最终文本，“用上”按最终文本判断。
9. 仅第一步自动联想记忆进入强化痕迹；主动回忆不进入、不强化。
10. 开始前当前 HEAD 打本地 archive/pre-organs-a 标签，新建 organs-a 分支。代码只本地提交；远端仅 docs/TASK_organs_a.md 和 docs/RESULTS_organs_a.md 推到 Execution_lab2。禁止推代码、评测材料、缓存，不强推或改写远端历史。
11. A1 零真实调用；A2 回答、重组、评审、烟测的新增真实调用合计不超过 300 次，只用 dev_a 60 探针，超限停下、保留现场并报告。

## 约束

- 保存 .env.local、密钥、data/、真实 Hot/Cold/记忆库，禁止提交。开始前查实际路径，完整复制到仓库外，报告位置。测试全用临时目录。
- 禁止 git add -A、git clean -x、git stash -u/-a，仅按路径暂存。
- 用 Conversation_Memory/.venv/bin/python，保留 .venv 与 BGE-M3 缓存。
- 改前记录 pytest -q、pytest Mind Nervous Execution -q、pytest tests -q、实验室离线测试四套基线。记录原失败/跳过；验收无新增失败，旧链测试通过。
- Hot/Cold owner 仍在 core，Memory 仍在 Conversation_Memory。回答拼法只有一份，Chat/Lab 共用，A2 参数区分；不复制实现。
- 旧 Nervous/triggers.py、organ.py、Mind/organ.py、model.py 等原样保留；新文件不得同名。

## 阶段 0：准备

记录四基线，打标签建分支，备份数据；检查 dev_a P8 回答及盲评缓存，从实验室配置记录真实模型与参数，A2 对照一致。

## 阶段 1：A1

新增 config/lumina.toml 与加载器、Nervous/bus.py、event_triggers.py、scheduler.py、lumina_state.py、Mind/runner.py、Language/channel.py（文件名可调整，各司其职）。调度随 FastAPI 生命周期启动停止。

/api/chat 发布 user.message，等待第一句（默认 90 秒），超时返回 v1 降级回复。思考开始写用户轮，请求按写前 Hot 快照构造；每次说话写 assistant 轮；压缩、摘要、Dream 检查置于思考结束之后，顺序同 v1。痕迹键为第一句 assistant 轮 ID，内容同 v1。

每步请求、响应在动作前写 SQLite；动作幂等键为思考 ID+步号+序号；整次结束才确认触发事件。重启复用响应，不重新调用、不重复说话。

状态有空闲、Dream 锁占用时做梦，可并存；执行中只留 B 接口。思考焦点“在回你的消息”，空闲为空。API 新增状态/焦点，前端一行展示。

验收：脚本化固定对话覆盖压缩、Dream、降级；用注入时钟/ID 逐字节比较新旧 system/messages/预填/参数及 Hot/Cold/摘要/痕迹。dev_a/dev_b P8 cache-only 与 R0 记忆结果零差异。分别在响应落盘未说话、已说话未确认处中断恢复，只说一次。四基线无新增失败。

## 阶段 2：A2

沿用宽容解析；新字段缺失或坏格式视为没有，不重试；回复可为空，可多次说话。

读仅允许配置 workspace/ 下相对路径，默认最多 20000 字，截断告知模型并记痕迹。回忆调用门面只读召回，短编号进下一步，不进强化痕迹。

SQLite 状态表：自身状态/手头任务本卡留空；真实状态/焦点来自 Nervous；交接为最近 5 条思绪、最多 5 样带着的东西，仅到下一次。思绪补时间与缘由、显示相对时间，默认截到 200 字，空不存；引用旧思绪可在滑出窗口后再显示一轮，不继续带就失效。思绪只在状态表和痕迹，绝不进入 Hot/Cold/Memory。

上下文顺序：answer_v5+附录 A（无人物背景）；原对话；状态块（空则省略）；记忆块与当前消息；本次前几步结果。思绪/记忆/文件编号在模型文本里可见。

Language 输入为人物背景、表达的意思、记忆块、状态行、最近默认 6 轮对话；只输出对他说的话。失败用 Mind 原文并记痕迹。新消息下一步前插入并写 Hot，对应 API 等插入后的第一句话。

前端思考中每 5 秒拉 /api/history 展示后续发言，同时显示状态/焦点。mind.protocol=a1/a2，默认由盲评决定。

确定性测试覆盖：读后说话、主动回忆不强化、思绪窗口与淡出/旧引用仅保留一轮/空思绪、重组最终 Hot/双文本/最终用上/失败回退、中途消息、第四步截断、崩溃不重复、空状态省略、多 assistant 压缩/摘要/Dream、做梦状态切换。

盲评：dev_a 60 探针，P8 缓存回答对照，A2+按需 Language 新臂；记忆/模型/参数一致，状态/思绪为空，不评思绪。沿用实验室双顺序规程，报告胜平负与各维度、重组比例、重组前后各 3 例及口吻观察。不设通过线；明显变差默认 a1，a2 显式选项，报告原因。

## 阶段 3：收尾

AGENTS 改为新边界：每轮 Chat 是一次 Mind 对话思考；Mind 直接只读工作区文件/回忆；器官事件通话；旧链 B 删除，新代码不依赖。更新 README 地图、CURRENT_STATUS 实测事实；新增 ORGANS.md 仅写已实现边界、事件、状态表、思绪和配置。

RESULTS_organs_a.md 包含改动和新文件职责、四基线前后、A1 字节验证、A2 确定性及盲评、真实调用/token 按模型用途、默认开关理由、偏差、未解决与 B 注意事项。

## 附录 A：对话思考新增提示（逐字追加 answer_v5）

```text
【你是谁、做什么】
你是 Lumina 的思考中枢。你负责想清楚他说了什么、意味着什么、你要表达什么、要不要先查点什么。怎么措辞可以交给语言器官：它熟悉 Lumina 的人物背景和说话方式。

【输出】除了"理解""借鉴""顺带""回复"，你还可以给出下面四项：

"重组"：true 或 false。
  true：请语言器官把你的"回复"按 Lumina 的口吻重新组织后再发出，这时"回复"写你要表达的意思即可。
  false："回复"原样发出。

"行动"：数组，可以为空。每一项是下面之一：
  {"读": "工作区里的相对路径"}　读一个文件。
  {"回忆": "一句线索"}　主动回忆和这条线索有关的记忆。
  这两种行动的结果会在下一步给你。需要先读或先回忆再回答时，"回复"可以留空。
  没有行动时，这次思考就结束了。

"思绪"：这次想完，你心里还留着的一两句话，写给下一刻的自己。
  用第一人称，像自言自语，可以不完整，最多三句。
  可以写：你现在怎么看这件事；还悬着什么（在等的、没想通的、拿不准的）；接下来倾向怎么做；此刻的感受。不用每样都写。
  不要写：你刚做了什么（这些有记录）；给自己定的长期规矩；为写而写的情绪；大段推理。
  心里没有留下什么，就写 ""。

"带着"：数组，最多 5 项。下一次你还想继续看到的东西，填它们的编号（编号见状态和记忆里的标注）。不填就放下。

还有行动要做时，"思绪"和"带着"可以先空着；只有这次思考最后一步写的才会保留。

【你的状态】
如果有"你的状态"一段，里面是你此刻的状态、你最近的思绪和你带着的东西。思绪是你自己之前的想法，未必对。
```

状态块示例（格式可按实现调整）：

```text
你的状态：
· 此刻：空闲
你最近的思绪（你自己的想法，未必对）：
· 思绪3｜昨晚 23:10，和他聊天时：他这周好像一直在赶实验，别再给他添别的事。
你带着的东西：
· 文件:notes/plan.md
```

## 附录 B：语言器官提示

system 由人物背景全文加以下原文组成；user 依次是最近对话、记忆块、状态行、要表达的意思。

```text
【你的任务】
你是 Lumina 的语言器官。Lumina 已经想好了要对他表达的意思，写在"要表达的意思"里。请用她的口吻，把这层意思说出来。
- 意思不变：不加她没打算说的事实、承诺或问题，也不删掉她要表达的内容。
- 可以自然地用上下面的记忆和最近的对话，让话更像她；不要背诵或罗列记忆。
- 长短跟着意思走，不要加客套和总结。
- 只输出要发给他的话本身。
```

## 附录 C：配置

| 配置 | 默认 |
| --- | --- |
| nervous.db_path | data/nervous/lumina.sqlite |
| chat.first_reply_timeout_s | 90 |
| mind.protocol | 盲评决定 a1/a2 |
| mind.dialogue_max_steps | 4 |
| mind.thought_window | 5 |
| mind.thought_max_chars | 200 |
| mind.carry_max | 5 |
| mind.read_max_chars | 20000 |
| language.recent_turns | 6 |
| language.model | 与对话模型相同 |
| frontend.poll_interval_s | 5 |
| model.max_output_tokens | 模型允许最大值，仅容错 |
| workspace.path | workspace/ |
