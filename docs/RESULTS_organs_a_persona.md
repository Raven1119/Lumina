# 器官重构 A 补充：人物背景与逐句语言器官结果

完成阶段 1–3。**默认已改为 `mind.protocol = "a2"`、`language.render = "always"`**，依据是三条预登记判据全部满足。它只表示这次 dev_a 比较达到了任务卡的采纳规则；原质量盲评有 31/60 条换序不一致，不构成全面质量提升的证明。未跑 holdout、回答模式以外的实验或另改记忆与 Dream 算法。

## 现场、材料与改动

在本地 `organs-a` 从 `11b330d0f7c73573c1818c83b2e403efc3d4aa6c` 开始。检查到唯一的 Git 脏项是被跟踪的 BGE-M3 嵌入 SQLite 缓存：先校验并在仓库外备份，加精确 `.gitignore` 条目，用 `git rm --cached` 停止跟踪；本地文件原样保留。准备提交为 `b582881be5cac29d28d8544f9956c57411473447`，本地标签 `archive/pre-organs-a-persona` 指向该干净准备提交。缓存原始 SHA-256 为 `1f3e4e14a6a2ce4b2f62e3cb894e860a967719fdef9ca3d09a1ae2d0393fdbde`。任务实现的比较基点是准备提交。

凭据与现有 `data/` 完整备份于 `/home/wmywb/Lumina-organs-a-persona-backup-20260930/`（权限 0700，`MANIFEST.json` 校验）。服务配置的 Hot `data/draft/hot_drafts.jsonl`、Cold `data/draft/cold_drafts.jsonl`、Memory `data/memory_v1` 在开始时均不存在；已有 `data/mind/` 已备份。没有移动 `.venv` 或 BGE-M3 权重。评测三份文本、请求、双顺序评审、SQLite 预算账本及测试日志仅存该目录下 `final-evidence/`：293 个文件按 `FINAL_MANIFEST.json` 逐个 SHA-256 验证，预算 SQLite 完整性检查为 `ok`。回放曾往本地嵌入缓存增加记录；归档其运行后副本后，已从进场备份恢复原文件并复验进场 SHA-256。凭据和已有 `data/` 的 2 个文件及 3 个原本不存在的路径也在收尾复验。

| 文件 | 本次职责 |
| --- | --- |
| `prompts/mind_identity.md`、`prompts/language_voice.md` | 从不变的 `chat_background.md` 逐字挑选 16/8 段；第 11 段仅给 Language，1、2、6、7、10、12、17 两边共有；全部 17 段至少出现一次 |
| `prompts/dialogue_a2_persona.md`、`prompts/language_persona.md` | 任务卡附录 B/C 原文，替代 A2 的旧任务提示；Mind 不再被要求选择“重组” |
| `Conversation_Memory/answer.py` | Chat 与 Lab 共用的唯一回答拼法；A2 在原人物背景槽放 Mind identity，再追加新协议；A1 分支保持原样 |
| `Language/rephrase.py`、`Language/channel.py` | 用 voice 与新任务提示构造四段请求；A2 每句走 Language，原样成功和失败回退分开留痕；Hot 和“用上”看最终文本 |
| `config/lumina.py`、`config/lumina.toml` | 校验 `language.render=always/mind_choice`；按盲评选择 `a2 + always` 默认 |
| `Memory_lab/judge/persona_prompt_organs_a.md` | 任务卡附录 D 原文，拼接完整原人物背景作人格评审，不改 `judge_prompt_v2` |
| `Memory_lab/analysis/organs_a_persona_eval.py` | 复用旧 P8 配置和 46 条回答、重生 14 条、运行生产 Runner/Channel、两种评审及 260 次预留账本 |
| `Memory_lab/analysis/organs_a_eval.py` | 随 Language 请求签名改动适配旧实验脚本调用处；原评分实现未改 |
| `tests/test_organs_a_persona.py`、`Memory_lab/tests/test_organs_a_persona_eval.py` | 逐段/逐字校验、脚本化逐句调用与回退、格式/预算/判据测试 |
| `tests/test_organs_a1.py`、`tests/test_organs_a2.py` | 固定旧行为测试的显式协议/渲染选项，使生产默认切换不改变对照目标 |
| `docs/ORGANS.md`、`docs/CURRENT_STATUS.md`、`README.md`、本任务卡与本报告 | 更新实际边界、配置、评测依据和限制 |

旧 `Mind/`、`Nervous/`、`Execution/`、`Dream/`、`Conversation_Memory/engine/` 均未修改；`chat_background.md`、`answer_v5`、`judge_prompt_v2` 文字未改。阶段 B 的主动消息还不存在；本次只确保 A2 每一次实际说话（包括同一思考的后续句）都走同一个 Language 出口。

## 测试与离线对照

解释器为 `Conversation_Memory/.venv/bin/python`，测试状态只写 `/tmp`。以下改前基线在准备提交后、实现前取得；改后基线在默认切到 A2 后重跑。

| 命令 | 改前 | 改后 |
| --- | --- | --- |
| `python -m pytest -q` | 846 passed，6 skipped，1 warning | 853 passed，6 skipped，1 warning |
| `python -m pytest Mind Nervous Execution -q` | 690 passed，6 skipped | 690 passed，6 skipped |
| `python -m pytest tests -q` | 156 passed，1 warning | 163 passed，1 warning |
| 在 `Memory_lab/` 执行 `python -m pytest tests -q` | 83 passed | 86 passed |

专项脚本模型覆盖：人物段落原文/顺序与附录 B/C/D 逐字一致；`always` 下同次思考三句话全部调用 Language；原样输出记 `unchanged`、调用失败退回 Mind 原文且不重复说话；Hot 与强化痕迹记最终文本，动作痕迹同时记 Mind 和最终文本；`mind_choice` 仍按布尔字段路由；首句回复等待 Language 完成。A1 的既有固定时钟/ID、MockTransport 请求 body 原始 bytes、Hot/Cold/摘要/痕迹逐字节 oracle 仍通过。旧认知链套件无新增失败。

P8 cache-only 回放：dev_a、dev_b 各 60 条，相对 `Memory_lab/runs/memory_v1/r0_dev_{a,b}_P8` 的回放比较 **0 差异**；通过 core DraftTurn 到 Memory 门面的 Chat/Lab 渲染块各 **0 差异**；新增真实调用 **0**。缓存仍可用，两套都没有下载或改动权重。

## 盲评设置与判据

仅 dev_a 60 个已有探针/时间变体，状态与思绪为空。P8 的 46 条是上次已按同一 P8 配置新生成并留有预算记录的回答，本次只读复用；原 14 条历史缓存**没有复用**，用同一 P8 拼法和参数现场重新生成。60 条 P8 对照均来自当前后端的调用批次；46 条与本次相隔一次任务，模型别名相同不能证明底层权重修订号逐位相同。P8 与新臂记忆块逐条相同。新臂使用生产 `DialogueRunner + LanguageChannel`，每探针保存 P8、Mind 原话、Language 最终文本；原话来自动作痕迹，没有再调模型。

回答模型及 Language 均为 `deepseek-flash`、temperature 0、thinking 关闭、max_tokens 1000、无预填。原评审沿用 `judge_prompt_v2` 的批量、双顺序、原 validate/aggregate；人格评审用新提示批量、双顺序。两种评审都只在两顺序一致时计胜负，否则单列换序不一致；没有人工改分。原评审第一顺序 60 条、反向 57 条（原规程跳过首遍 3 个平局），格式失败 0；人格两组各两顺序 60 条，格式失败均 0。

| 预登记条件 | 实测 | 结论 |
| --- | --- | --- |
| 原规程最终对 P8：一致负 − 一致胜 ≤ 3 | 13 − 13 = **0**；最终胜 13 / 一致平 3 / 负 13 / 换序不一致 31 | 满足 |
| “说错往事”与 should_not 各不比 P8 多 2 条以上 | 评审标错 P8 **2/60**、最终 **3/60**，增加 1；should_not 探针 P8 **0/60**、最终 **2/60**，增加 2 | 满足，后者压线 |
| 人格评审最终对 P8：一致负 ≤ 一致胜 | 最终胜 **26** / 一致平 **0** / 负 **13** / 换序不一致 **21** | 满足 |

原规程第一遍有效分维度：should 平均 P8 **0.9083**、最终 **0.9083**；有此 rubric 的条目 should_not 违反均值 P8 **0**、最终 **0.0833**；alive 均值 P8 **3.7333**、最终 **3.8667**。按来源分开：14 条现场重生 P8 子集，最终 1 胜/1 平/5 负/7 换序不一致；46 条复用子集，最终 12 胜/2 平/8 负/24 换序不一致。即使总体达标，重生 14 条子集仍偏向 P8，不能略去。

人格评审的辅助比较“最终对 Mind 原话”：最终胜 **1** / 一致平 **42** / 负 **1** / 换序不一致 **16**，不参与判据。38 条原样返回全部一致平；只看 22 条改写是 1 胜/4 平/1 负/16 不一致。这组没有稳定的单组件优势；人格最终对 P8 的优势不能单独归因于语言器官的措辞改动。

## 语言器官输出与意思核查

60 条最终文本中 **38 条原样返回（63.3%）**、22 条改写（36.7%）；60 条均成功调用，无 Language 失败回退。每条新臂只有一句话；同次多句由脚本测试验证，未把单轮评测当作长期交接证据。

逐条看了 22 条改写的 Mind/最终文本，并对照原 judge 的 should/should_not 标记。下列是值得保留的具体差异，均不重新打分：

| 探针 | Mind 原话 → 最终文本 | 原评审或语义风险 |
| --- | --- | --- |
| A27，7 天 | “又是十一点到两点？” → “又是十一点到两点。” | **问句变成断言**，可能把求证变成确定陈述；原 judge 未标 should_not，仍是可疑的言语行为变化。A27，14 天也出现同样问号变句号。 |
| A10，当下 | “先看两件事：它今天有没有吐、有没有排便，精神怎么样” → “先看三件事：今天有没有吐、有没有排便、精神怎么样” | 数词从两变三，列举本来就是三项；未见新事实，但跨过了单纯润色边界，须防止今后改动真实数量。 |
| A07，当下 | “一篇要慢病毒、实验室没条件” → “一篇要慢病毒，实验室没这个条件” | 原 judge 对最终文本标“说错往事”与 should_not；相关 knockdown/文献断言在 **Mind 原话已存在**，语言改写只增“这个”和调标点，不能把错误归因于语言器官。 |
| A13，当下 | “上次是阳性对照，这次多半是机制”仅由破折号改句号 | 原 judge 同样标“说错往事”与 should_not；该判断已存在于 Mind 原话。 |
| A06，当下 | 仅把逗号改成破折号；Mind 和最终文本都没有明确“4月1日” | 原 judge 判最终的 should 要点少于 P8；遗漏在改写前已存在。 |

其余改写主要是标点、词序、近义词和条件句表达；例如 A21 的“答应了具体承诺”变成“给了具体承诺”，未见新增事实，但不能据此断言所有改写都严格保义。以上风险均保留原评审标签；“说错往事”是评审标记，不等于独立事实核验结论。

## 延迟、调用与默认选择

固定脚本模型对同一首句各跑 12 次：`mind_choice` 不调用 Language 的中位数 **43.387 ms**；`always` 增加一次固定 40 ms 的 Language 调用后首句中位数 **88.662 ms**，增加 **45.275 ms**，其中 Language 组件自身中位数 **40.079 ms**。真实评测保存的 Language HTTP 耗时中位数 **3.792 s**、Mind HTTP 中位数 **4.525 s**；因此真实首句通常会多等一个串行 Language 调用及周边开销，不能把脚本的 45 ms 当成线上延迟。未做并发负载或端到端服务 SLA 测量。

HTTP 发送前沿用 SQLite 预留账本；**207/260 次**真实生成尝试均收到响应，无格式失败、网络失败或未知尝试。全部为 `deepseek-flash`；input_tokens 不含单列的 cache_read_input_tokens，cache creation 为 0。

| 用途 | 次数 | 未缓存输入 | 缓存读取输入 | 输出 | 合计 token |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mind 新臂 | 61 | 181,705 | 49,920 | 10,811 | 242,436 |
| Language | 60 | 66,144 | 22,784 | 1,858 | 90,786 |
| 现场重生 P8 | 14 | 2,612 | 44,287 | 797 | 47,696 |
| 原盲评 | 24 | 155,529 | 22,528 | 11,433 | 189,490 |
| 人格：最终对 P8 | 24 | 140,764 | 35,200 | 6,635 | 182,599 |
| 人格：最终对 Mind | 24 | 104,962 | 71,424 | 3,768 | 180,154 |
| **合计** | **207** | **651,716** | **246,143** | **35,302** | **933,161** |

Mind 61 次对应 60 个探针，其中一条需要第二步才说话。先前 46 条 P8 已计入上次任务的预算，不重复算作本次新增调用。脚本测试与 cache-only 回放新增真实调用 0。

三条规则全部满足，故配置默认切到 **A2 + always**。`mind_choice` 仍是显式对照选项，A1 仍可显式选择；默认变更不意味着放宽对具体事实或长期记忆正确性的要求。

## 偏差、未解问题与阶段 B

1. 起始并非干净工作树，唯一脏项是已跟踪嵌入缓存。为同时满足“开始前干净”和“取消跟踪但保留文件”，先作上述缓存准备提交，再打任务标签；标签因此指向干净准备提交，而非进场时原始 `11b330d`。未 reset/rebase/stash/clean，原文件仍在，备份有校验。
2. 第一次基线脚本把 `.venv/bin/python` 的符号链接解析成系统解释器，得到 `No module named pytest`；改成原样执行规定路径后四套基线全部成功。此轮不计作测试失败或真实模型调用。
3. 默认改成 A2 后，旧 A1 oracle 有 3 个测试因隐式继承新默认而失败；测试夹具固定显式 A1 后同一字节 oracle 与整套 Chat 测试通过。A2 旧行为测试也显式选 `mind_choice`。这是测试目标的固定，不是更改 A1 生产请求。
4. 原评审批次第一次在**本地汇总**遇到新评测行缺少旧分析器期待的 `rephrase/choices` 字段；补齐元数据并从已保存的 24 次评审响应重算，模型请求、评分与判据未变，**新增调用 0**。预算账本保留原响应与异常现场。
5. 上次补齐的 46 条记录与本次配置一致，旧 14 条已现场重生；无法从公开模型别名确认底层后端权重 revision。评审对 31 条原质量比较换序不一致，14 条新生子集相对不利，should_not 增加恰达允许上限。人格最终对 Mind 几乎打平；日常长期对话仍需继续观察，不能追加未授权实验来解释。
6. 阶段 B 接入主动消息时，应复用同一 Language 出口，并让消息标记 A2 协议以触发 `always`；当前没有主动消息、帮手池、Execution 连接或旧链删除。读/回忆、思绪与 Dream 的原边界不变。
7. 第一次改后全套回归在默认沙箱中未产出测试结果，符合本仓库 TestClient 以往阻塞情况；已中断，改用与改前基线相同的扩展测试权限重跑。不把中断算作通过或失败数。
8. 实验室改后测试首次从仓库根目录启动，因 `eval_set` 模块以实验室工作目录为导入根而在收集时失败；改回 `Memory_lab/` 工作目录后 86 passed。失败日志与成功日志均保留；没有改实验室导入路径或评测数据。
9. 首次本地提交因环境缺少 Git author identity 未创建提交；沿用前一提交的 `Codex` 身份，仅在下一次提交命令中指定，没有修改全局或仓库身份配置。

本地评测与日志归档位置为 `/home/wmywb/Lumina-organs-a-persona-backup-20260930/final-evidence/`；远端只发布本任务卡与本报告，代码和其他文档只在本地提交，`runs/`、`cache/`、原始评测材料均不推送。
