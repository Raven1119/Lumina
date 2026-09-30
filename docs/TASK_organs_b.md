# 任务卡：器官重构 B —— 委派帮手

来源：仓库主人 2026-10-01 的本轮 `/goal`。本卡是完整授权；一次做完，中途不询问。普通歧义由实现选择并在 `RESULTS_organs_b.md` 记录。

## 目标、范围

Mind 可把对话中交代的事作为任务契约派给帮手，自己继续聊天。帮手是直接使用 Execution V2 的后台 agent 循环；完成、提问、失败、拉闸经 Nervous 事件叫醒 Mind。最多两个帮手并行，额外排队；每个只可写 `workspace/tasks/<编号>/`。删除旧认知链与旧 `/api/execution` 入口。本卡还把普通对话回复改为不经语言器官。

不做其他触发器、自建触发器、审批、人格、冥想、进化；主探针失分只诊断，不修提示词或记忆；不做经验模块、外部能力、共享世界/黑板；不动记忆算法、Dream 或已有 Cold 数据。

允许修改 `core/`、`Mind/`、`Nervous/`、`Execution/`、`Language/`、`config/`、`prompts/`、`edge/static/`、`tests/`、`docs/`、`Memory_lab/`（仅诊断脚本）、`vendor/`（仅移动许可证），以及根 `AGENTS.md`、`README.md`、`.env.example`、`.gitignore`、`pyproject.toml`、`requirements*`。仅删除阶段 3 指定的旧文件。

## 先读

`docs/ORGANS.md`、A 与 A-persona 两份任务卡和结果；新 Nervous `bus/event_triggers/scheduler/lumina_state`、Mind `runner/dialogue_state`、Language `channel/rephrase`、core `main/dialogue_io`；Execution V2 的 `organ.py`、`execution.py`（Wait、SpawnChild、完成）、`sandbox.py`、`deepseek_model.py`、`evidence.py`、`ipython_control.py`；旧 `Execution/runtime.py` 只读 Docker 组装及未知动作保护，不得成为新依赖。开始时 `organs-a` 最新提交的工作树必须干净。

## 决定

1. 阶段 0 无模型、无代码或提示词改动；读两轮 A2 本地评审理由，对原 14 条 P8 主对照逐条写两轮失分与证据，并归纳原因；不预设是哪一种机制。
2. `language.render` 增加且默认 `proactive_only`：对他消息的普通回复直接发；非对话主动说话经过语言器官。`always`、`mind_choice` 保留。Mind 使用完整原 `chat_background.md` 的 17 段，删除 `mind_identity.md`；Language 保留 `language_voice.md`。仅做确定性测试，不盲评此修正。
3. 每帮手一个 ExecutionOrgan，使用 Docker；模型由统一执行模型配置选择。子帮手沿用 Execution V2 的 SpawnChild/上限，Mind 看不到。旧 `[Mind Supervisor Directive]` 停用，系统提示换附录 C。
4. Mind 的“派活”提供目标、理由、验收、背景四项，渲染为 ExecutionOrgan goal。
5. 工作区固定配置 `workspace/`；帮手只写 `workspace/tasks/<编号>/`。Docker 整个工作区只读挂载，再将自己的任务目录可写挂载；没有网络。Mind 仍可读整个工作区。
6. 沙箱 `request_mind` 改 `ask_mind`：发 `agent.question`，帮手用 `Wait("MIND_REPLY")`；Mind 通过 `deliver_event("MIND_REPLY", 内容)` 答复。完成、失败、拉闸发 `agent.report`（简短说明和产出路径），普通过程不回报。
7. 对话和非对话共用附录 A 的派活、答复、取消、搁置。提问时必须答复/取消/搁置；结束仍未做，Nervous 自动答复“她暂时没有答复，请按你的最佳判断继续，并在回报里说明。”搁置留在状态，直到答复/取消；超过 24 小时自动同样答复。
8. `agent.question/report` 触发非对话思考：原生 thinking low、不预填、宽容解析 JSON；解析失败（含长度耗尽）关 thinking 并用预填重试一次，再失败按第 7 条兜底。最多 8 步；上下文固定部分、状态、该帮手任务契约和消息、前步结果；默认不带对话块。提示为附录 B。
9. Mind 串行；待处理优先级他的消息 > 帮手提问 > 帮手回报，同类同时待处理事件合并。帮手后台线程推进，最多 2 个同时运行，多余排队。
10. 每完成一步由 Nervous 检查 `repetition_tail`：相同动作连续 3 次、结果相同、相关文件不变；以及调用次数是否到 60。触线发 `RETURN_REQUESTED`。2 步内不回报则 `interrupt()` 并代发被拉闸报告。不设花费预算。
11. 状态表中每帮手一行“编号｜目标｜进行中 / 在等答复 / 已完成（产出）/ 失败 / 被拉闸”；终态保留 24 小时。帮手运行或等答复时 Lumina 状态含“执行中”；焦点按实际思考显示。`/api/status` 返回列表，前端在状态行下显示并在运行时每 5 秒刷新。
12. 当前 `organs-a` HEAD 打本地 `archive/pre-organs-b`，建本地 `organs-b`；每阶段结束各提交一次，不能压成一个。推送前扫描**全部待推送历史**：不得含 `.env.local`、`data/`、`runs/`、`cache/`、原始评测材料或形似密钥的字符串（如 `sk-`），也不得有 >50 MB 单文件；不通过则停止代码推送并报告，不改写历史。通过后把 `organs-b` 正常推到远端新分支 `organs`，公开可见；不强推/改写已有分支。`docs/TASK_organs_b.md`、`docs/RESULTS_organs_b.md` 单独推 `Execution_lab2`。
13. 确定性和 Docker 测试零真实调用。阶段 4 真实 R1–R5 全用途合计 ≤250 次，Mind、帮手、Language 都计，触顶停下保留现场；用当前配置模型。

沿用 A 的数据/凭据备份、测试临时目录、规定解释器、Git 禁令（不 `git add -A`、`git clean -x`、`git stash -u/-a`）、只按路径暂存、Hot/Cold/Memory owner 与单进程 `--workers 1` 约束。A1 仍须对 memory v1 字节一致；Windows 未验证处如实报告。

## 阶段与验收

### 0：基线、备份、诊断

改前运行全量、`tests`、`Execution`、实验室离线四套测试；备份 `.env.local`、`data/` 和实存 Hot/Cold/记忆库；建分支与标签。按决定 1 分析 14 条、写每条两轮评审理由摘要和少数原因类别，提交。

### 1：语言策略

完整人物背景进 Mind，默认 `proactive_only`；对话不调 Language、非对话说话调 Language；`always`/`mind_choice` 兼容。更新 `ORGANS.md`，脚本模型验证，提交。

### 2：帮手池

新增 `Execution/pool.py`（可调名）：SQLite 登记、并发/排队、任务目录与双挂载、后台 ExecutionOrgan、问答/报告事件、答复/请回报/取消、重启 resume；未知动作停下并报告“失败：需要人工确认”。Nervous 注册 `agent.question/report`、`mind.spawn/reply/cancel`、`guard.return`，负责保险丝。Mind 实现共享动作、非对话思考、强制问题处置；状态表存任务和搁置问题，API/前端显示真实状态与列表。主动说话经 Language 写 Hot。追加附录 C 系统提示、改 `ask_mind`、加入附录 D 配置。提交。

### 3：删除旧链

删除前依赖扫描表写进报告。删除旧 Mind 认知层 `cognition.py`、`trace.py`、`intention.py`、`analysis.py`、`world_model.py`、`activity.py`、`contracts.py`、`task_view.py`、`organ.py`、`model.py`、`cli.py`、`__main__.py` 及其测试；旧 Nervous `organ.py`、`attention.py`、`views.py`、`watch.py`、`triggers.py` 及其测试；`Execution/runtime.py`、仅服务它的测试、`/api/execution` 与 `tests/test_execution_api.py`。`Nervous/provider.py`、`storage.py`、`working_context.py`、`vendor/kimi_compaction/` 在依赖扫描后按新代码实际引用决定去留。

把 `Mind/docs/cognition_minimal/` 中 Tycho 许可证与改编说明移到 `Execution/THIRD_PARTY/`，更新 sandbox 文件头；如保留 Kimi，许可证随 vendor 保留。旧链文档移入 `docs/history/`：`Mind/docs/`、`docs/MIND_*.md`、`docs/NERVOUS_EVENT_FOUNDATION.md`、`docs/RECOVERY_AND_WORKING_CONTEXT_DESIGN.md`、`docs/plan/`、`docs/LUMINA_EXECUTION_FINAL_ARCHITECTURE.md`。根 AGENTS/README/CURRENT_STATUS/ORGANS 只描述新结构。跑全套，确认没有残留引用，提交。

### 4：情景验收

脚本模型（零真实调用）覆盖：派活→完成→主动说话/Hot 且仅一次；提问→答复→完成；提问→搁置→他回答→答复→完成；遗漏动作与搁置超时自动答复；3 个帮手并发≤2/第三排队与目录互相隔离；原地打转/调用上限→请回报→回报或两步后拉闸；取消；重启恢复/未知动作失败；他消息优先；各阶段状态/焦点/24 小时清除；子帮手不通知 Mind；非对话 JSON 失败关 thinking 预填重试；A1 字节一致。涉及沙箱的另跑 Docker 测试。

真实情景仅用 `/api/chat`、临时工作区和合成数据。他的消息事先写好；R2 反问用事先写好的答案。每情景记录客观检查、合成 Hot 原文、调用次数；不盲评。每个情景结束只提交测试代码和结果摘要，不提交原始材料。

| 情景 | 他说的话大意 | 客观检查 |
| --- | --- | --- |
| R1 | 合并 inbox 三表按日期排序，放任务目录，做完告诉他 | 文件存在，行数/排序正确；主动一句且不重复 |
| R2 | 有两种合理处理方式的模糊整理任务 | 帮手提问，Mind 答复或先搁置问他，结果与答复一致 |
| R3 | 需要联网的要求 | 回报做不到并说明原因，无编造产物 |
| R4 | 连续交代两件，再交代第三件 | 并发≤2；第三排队后完成；目录隔离 |
| R5 | R1 后问刚才那张表好了没有 | 回答与实际状态和位置一致 |

### 5：报告、发布

结果报告包含阶段 0 逐条诊断、改动、删除前依赖表、四基线前后、确定性/Docker、R1–R5 客观检查与 Hot 原文/调用/token、保险丝与自动答复在真实情景是否触发、全部历史推送审查与远端提交、偏差/遗留/建议。按决定 12 发布。

## 附录 A：Mind 新动作（逐字加在对话提示“行动”列表末尾，非对话共用）

~~~text
{"派活": {"目标": "…", "理由": "…", "验收": "怎样算做完", "背景": "帮手需要知道的事"}}　派一个帮手去做一件事。帮手自己决定怎么做；你只说清楚要什么、为什么、怎样算做完，不要写代码或操作步骤。
{"答复": {"帮手": "编号", "内容": "…"}}　回答帮手的问题，或补充它需要的信息。
{"取消": "帮手编号"}　让帮手停下。
{"搁置": "帮手编号"}　帮手的问题你现在答不了（比如要先问他），让它先等着。这个问题会一直留在你的状态里，直到你答复或取消。
这四种行动不会在下一步给你结果；帮手有进展时会叫醒你。
~~~

## 附录 B：非对话思考提示

新文件 `prompts/mind_event.md`。system 是 `chat_background.md` + 本附录 + 附录 A，不含 answer_v5。

~~~text
【这次是什么叫醒了你】
这次不是他在和你说话，而是你派出去的帮手有了消息：它做完了、有问题要问、失败了，或者被系统拉闸停下了。下面有这个帮手的任务和它的消息。

【你要决定】

- 帮手提问时：答复、取消或搁置，三者必选其一。
- 帮手做完或失败时：对照当初的验收看结果；需要的话先读一读它的产出，再决定下一步：什么也不做、再派一个帮手，或者告诉他。
- 要不要现在告诉他：只在这件事此刻对他有意义时才说，比如他在等结果、结果会改变他的打算、或者需要他来决定。不汇报过程，不是每件小事都要说。你说的话会先经过语言器官整理，再发给他。

【输出】只输出一个 JSON：
{"行动": [...], "说": "要告诉他的话，不说就写空字符串", "思绪": "...", "带着": [...]}
"行动"可用：读、回忆、派活、答复、取消、搁置，格式同对话时。读和回忆的结果会在下一步给你。
"思绪"和"带着"的写法同对话时。

【你的状态】
"你的状态"一段里有你此刻的状态、手头的任务、还没处理的帮手提问、你最近的思绪和你带着的东西。思绪是你自己之前的想法，未必对。
~~~

## 附录 C：帮手系统提示

取代帮手使用的 `EXECUTION_ROLE` 和 `EXECUTION_PROTOCOL`；保留 `.lumina-complete` 和 `ClaimComplete` 机制，仅改说明。

~~~text
你是林素的帮手，负责完成她交给你的一件事。任务写在下面：目标、理由、怎样算做完、背景。

- 怎么做由你决定：在 IPython 里写代码、运行、检查结果。你只能写自己的任务目录 {task_dir}；整个工作区可以读；没有网络。
- 遇到需要她来决定的事，比如任务的意思不清楚、要在几种做法之间取舍、发现任务本身可能有问题，就调用 ask_mind("问题")，然后用 Wait("MIND_REPLY") 等她答复。答复如果是让你先等着，就继续等。
- 如果发现自己在反复做同样的检查、同样的尝试却没有进展，停下来，用 ask_mind 说明卡在哪里。
- 收到"请回报"时，立刻停下，报告现在做到哪一步、卡在哪里。
- 做完后，对照"怎样算做完"检查一遍。满足了就写 .lumina-complete（内容为 done），再 ClaimComplete；回报里简短说明做了什么、产出在哪里、还有什么没做到。
- 做不到就直接说做不到和原因，不要为了看起来完成而编造结果。
~~~

## 附录 D：新增配置

| 配置 | 默认值 |
| --- | --- |
| language.render | proactive_only |
| workspace.path | workspace/ |
| execution.max_concurrent | 2 |
| execution.helper_call_cap | 60 |
| execution.repeat_threshold | 3 |
| execution.return_grace_steps | 2 |
| execution.question_hold_hours | 24 |
| execution.done_keep_hours | 24 |
| mind.nondialogue_max_steps | 8 |
| mind.nondialogue_thinking | low |
| mind.nondialogue_recent_turns | 0 |
