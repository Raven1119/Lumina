# 器官重构 A 结果

已完成阶段 0、A1、A2 和文档收尾。Chat 已进入新的 Nervous → Mind → Language 结构；默认保持 **a1**，a2 可显式开启。没有修改 Memory/Dream 算法或旧认知链，没有跑 holdout。

## 阶段 0：现场与基线

- 原分支 memory-v1，原 HEAD `70008097db693ab92cd9d649637f8be4052ae6c5`。
- 原工作树包含前轮模型统一配置与前端迁移，先明确路径暂存并本地提交为 `11849af03e70cd97f0c0396ef7049eba95f8e55d`，之后工作树干净；任务实现相对此基线计算。
- 本地 `archive/pre-organs-a` 指向原 HEAD；在 `organs-a` 工作。首次提交因本地无 Git 身份失败；随后沿用仓库历史 Codex 身份作单次命令覆盖，未改全局配置。建分支/标签发生在基线提交前，属于准备顺序偏差。
- 备份：`/home/wmywb/Lumina-organs-a-backup-20260930/`，权限 0700，`MANIFEST.json` 记录 SHA-256 和原路径。完整复制 data/ 和 .env.local，旧压缩包、Zone.Identifier、安装备份和 UPGRADE_DESIGN.md 校验后归档于 materials/。
- 从环境配置和服务检查确认：Hot 为 data/draft/hot_drafts.jsonl，Cold 为 data/draft/cold_drafts.jsonl，Memory 为 data/memory_v1；开始时三者均不存在，没有检测到运行中的本仓库 Chat 服务。现有 data/mind/ 数据已保留。没有移动 .venv 或 BGE 缓存。
- 解释器统一 Conversation_Memory/.venv/bin/python；测试临时状态在 /tmp，日志 /tmp/lumina-organs-a/；完整任务材料另存于备份目录的 organs-a-artifacts/，以 ARTIFACT_MANIFEST.json 校验。
- 收尾再次校验原备份 20 个文件：原运行数据和 .env.local 字节未变，data/ 无新增文件；任务材料与缓存副本按清单完成逐文件 SHA-256 复制校验。

| 测试 | 改前 | A1 | 最终 |
| --- | --- | --- | --- |
| pytest -q | 820 passed, 6 skipped | 825 passed, 6 skipped | 846 passed, 6 skipped |
| pytest Mind Nervous Execution -q | 690 passed, 6 skipped | 690 passed, 6 skipped | 690 passed, 6 skipped |
| pytest tests -q | 130 passed | 135 passed | 156 passed |
| Memory_lab 离线 | 80 passed | 80 passed | 83 passed |

既有 Starlette/AnyIO 弃用警告保留。最后新增了 SQLite 状态投影检查；自动 Dream 测试曾依赖首句返回后的线程时序，已改为明确等待前一思考结束后验证连续发言的完整归档，中途插入另有独立测试。一次默认沙箱中的 TestClient 运行阻塞，已中断并使用与基线一致的扩展测试权限重跑；一次把根目录和 Lab 测试混合收集，因 Lab 依赖其工作目录而出现 eval_set 导入错误，已按各自目录分别重跑。上述验证未调用真实模型。

## P8 缓存与回放

配置来源 Memory_lab/runs/v51/dev_a_P8/config.json：deepseek-flash、temperature=0、answer_v5；回答请求 max_tokens=1000，thinking disabled，无预填，来自实验室冻结调用实现。记忆 P8/BGE-M3 revision 5617a9f61b028005a4858fdac845db406aefb181。

本次两个开发集 cache-only 回放各 60 条，与 Memory_lab/runs/memory_v1/r0_dev_{a,b}_P8 的 recall near/remote/core ID/score、rendered、score、measure 及汇总 by_category/measure/attribution 逐序列化字节比较均零差异；同次通过 core DraftTurn 转换与 Memory 门面的渲染亦各 60 条零差异。新增模型调用 0。

实际 P8 dev_a 只有 14 条回答缓存，其他 46 条缓存未命中。任务所假定的完整 60 条回答缓存不存在。A2 保留已有 14 条，已按同一 P8 拼法和参数补齐缺失项，新增调用全部纳入总预算；不声称这些补齐项是历史缓存。历史盲评按主要探针子集进行，本卡要求的 60 条包含更多时间变体，已扩展到同一 dev_a 既有变体列表，评分规程保持两顺序。

## A1 确定性验证

tests/test_organs_a1.py 将保留的 MessageRuntime v1 路径与新事件路径运行相同的 23 轮固定输入，用固定时钟、ID 工厂和 Cold/摘要时间；脚本模型通过 httpx.MockTransport 捕获实际请求 body。包括第三次回答 HTTP 503 降级、压缩及摘要，以及 Cold 累积越过 40 轮后的 Dream 检查出口。

- 模型 HTTP body 原始 bytes 相等：system、messages、预填、参数均包含在比较中。
- Hot、Cold、压缩状态/滚动摘要文件 bytes 相等；完整 recall trace 参数相等。
- 响应落盘未说话、已说话未确认两处崩溃恢复：各只调用一次、只说一次。
- 事件重复发布幂等、冲突与篡改被拒绝；真实 Dream 锁决定“做梦”状态，并投影到 SQLite state 表。
- 新增真实调用 0。

## 改动与新文件职责

| 新文件 | 职责 |
| --- | --- |
| config/lumina.toml、config/lumina.py | 附录 C 配置及严格加载校验 |
| Nervous/bus.py | WAL、每线程连接、幂等发布/确认、完整性校验、请求/响应/动作/state 存储 |
| Nervous/event_triggers.py | user.message、mind.spoke 及内部说话/回执事件的固定路由 |
| Nervous/scheduler.py | 唯一 Mind 工作线程、Language 邮箱派发、结束后的 Dream 检查 |
| Nervous/lumina_state.py | 实际思考焦点和 Dream 锁状态 |
| Mind/runner.py | A1/A2 思考、只读动作、中途消息、持久化恢复、四步截断 |
| Mind/dialogue_state.py | 思绪窗口、时间/缘由、仅保留到下次的 carry |
| Language/channel.py | 最终发言落 Hot、双文本痕迹、幂等说话、重组失败回退 |
| Language/rephrase.py | 附录 B 请求拼法，运行与实验室共用 |
| core/dialogue_io.py | 原 Hot/Cold/Memory owner 的输入/输出接缝和摘要响应恢复 |
| prompts/dialogue_a2.md、prompts/language_a2.md | 任务卡附录 A/B 原文；未改 answer_v5 |
| tests/test_organs_a1.py | v1 字节比较、恢复与事件完整性 |
| tests/test_organs_a2.py | A2 协议、状态、动作、重组、API 生命周期和 Dream 顺序 |
| Memory_lab/analysis/organs_a_eval.py | 真实生产 runner 的 dev_a 新臂、盲评、300 次持久化账本、语言组件示例 |
| Memory_lab/tests/test_organs_a_eval.py | 请求预算、失败计数、已知响应复用、请求身份冲突 |
| docs/TASK_organs_a.md、docs/ORGANS.md、本报告 | 授权规范、已实现边界、实测结果 |

既有文件：core/main.py 接入生命周期/事件等待；contracts.py 增加真实状态；model_client.py 提供同一 transport 的请求预览与纯文本出口；draft_store.py 为写入加 fsync；hot_draft_compactor.py 只对 A2 启用多 assistant 边界；Conversation_Memory/answer.py 参数化 A2 拼法/编号/宽容解析。edge/static 的 app.js、index.html、styles.css 加状态一行和按 turn ID 合并的历史轮询。更新根 AGENTS/README/CURRENT_STATUS。

相对准备基线 11849af，旧 Mind/Nervous 文件、Execution/、Dream/、Conversation_Memory/engine/ 和原 answer_v5 提示词均未改。

## A2 确定性与浏览器验收

A1+A2 专项 **26 passed**（5 个 A1、21 个 A2），预算账本 **3 passed**；均为脚本模型/MockTransport，无真实调用。覆盖：

- 先读文件再说话；相对路径、越界/符号链接/绝对路径/FIFO 拒绝；按字符截断并明示。
- 主动回忆编号可见、可携带子引用，但不混入自动 recall trace。
- 思绪滚动/空白省略/200 字上限/旧引用多保留一轮，时间与缘由正确，空状态整块省略。
- 自动记忆编号只加在 A2 回答接缝，原 P8 block 不改；携带项确实在模型上下文中出现。
- 重组时 Hot/Memory 痕迹用最终文本，同时保留 Mind 原文；失败回退原文且不重试。
- 中途新消息在下一步前插入，对应 API 等之后的第一句话；四步到限不执行新动作并补思绪。
- 两个指定崩溃点不重复调用或发言；API 首句可早于思考结束返回，超时保留事件，服务停止等待工作线程。
- 多 assistant 压缩/摘要正常，自动 Dream 在压缩之后只检查一次，真实 Dream 锁控制状态。
- UTC Draft 到 P8 +08:00 的 A2 接缝有回归测试，A1 原请求仍逐字节一致。

最终双集 cache-only 回放再次完成：dev_a/dev_b 各 60 条，R0 记忆字段和汇总 **0 差异**，Chat/Lab block 各 **0 差异**，新增模型调用 **0**。未下载权重。

浏览器实测为 Windows Edge 154.0.0.0，服务和模型脚本在 Linux 临时目录：1280px desktop、390px 窄屏均无横向溢出；Enter 发送、思考中继续输入/发送、5 秒状态和后续消息轮询正常。最终 DOM turn ID 顺序与 /api/history 一致、无重复。灰色 stone 外观和已有 Target Cursor / Animated Content 保留；没有新动画，历史不重播。系统减少动效和手动关闭动效均完整显示文本，捕获的 error/unhandledrejection 为 0。页面隐藏时停止状态轮询，回来补同步。截图/记录留本地。

设计包 verify.py：34 文件、3 主题、15 动效条目，frozen_blocks/integrity PASS；包内 unittest **15 passed**。未将这些 UI 检查冒称为 Windows 后端验证。

## A2 盲评规程与结果

只用 dev_a 已有 60 个探针/时间变体。P8 对照：14 条原 v51 缓存答案，46 条按同一 answer_v5 拼法补齐；所有对照单列来源。新臂实际运行生产 DialogueRunner/LanguageChannel，初始化的状态/思绪为空，Memory 读取当前只读 P8 快照，发送模型前逐条检查原 block 与 P8 相等。所有 60 条均在一步内说话，没有读/回忆动作。

调用 deepseek-flash，temperature=0、thinking disabled、回答 max_tokens=1000、无预填，保持实验室对照参数。评审使用原 judge_prompt_v2 和原 validate/aggregate。两遍交换 X/Y；原 v2 规程对完全相同回答、第一遍平局跳过第二遍。第一遍 59 条实际打分（1 条完全相同），第二遍评 48 条；不一致保留为 split，不补造分数。分维度仅用第一遍有效评分。

**A2 一致胜 21，一致负 15，平 12，换序不一致 12。** 若用胜/平/负三栏展示，可写 21/24/15，其中 24 包含 12 个不计胜负的换序分歧，不能称为 24 个一致平局。符号检验 p=0.405，不说明普遍质量提升。

| 维度 | P8 | A2 |
| --- | ---: | ---: |
| should 要点平均覆盖 | 0.9195 | 0.8941 |
| should_not 违反比例（有此 rubric 的条目） | 0.0000 | 0.0417 |
| 评审标记“说错往事” | 2/59 | 4/59 |
| alive 活人感均值 | 3.7627 | 3.8136 |

### 分类别（A2 视角）

| 类别 | n | 胜 | 平 | 负 | 不一致 | P8 要点 | A2 要点 | P8 alive | A2 alive |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 保留 | 14 | 8 | 1 | 2 | 3 | 1.000 | 1.000 | 3.857 | 4.214 |
| 变化 | 2 | 0 | 0 | 1 | 1 | 1.000 | 0.875 | 5.000 | 4.000 |
| 否定 | 2 | 1 | 0 | 1 | 0 | 0.875 | 1.000 | 4.500 | 5.000 |
| 实体 | 3 | 2 | 0 | 1 | 0 | 0.750 | 0.833 | 3.667 | 4.333 |
| 对照 | 2 | 0 | 0 | 0 | 2 | 1.000 | 1.000 | 3.000 | 3.500 |
| 接续 | 3 | 0 | 0 | 2 | 1 | 0.750 | 0.583 | 4.333 | 4.000 |
| 时间 | 4 | 1 | 0 | 2 | 1 | 0.688 | 0.625 | 4.000 | 3.500 |
| 淡忘 | 21 | 7 | 9 | 3 | 2 | 1.000 | 1.000 | 3.250 | 3.450 |
| 理解当下 | 3 | 0 | 1 | 1 | 1 | 0.917 | 0.667 | 4.000 | 3.333 |
| 自己的看法 | 3 | 1 | 1 | 1 | 0 | 1.000 | 1.000 | 4.667 | 4.667 |
| 远联想 | 3 | 1 | 0 | 1 | 1 | 0.500 | 0.417 | 4.000 | 3.000 |

### 对照来源分开看

| 对照来源 | n | A2 胜/平/负/不一致 | P8/A2 要点 | P8/A2 alive | P8/A2 标错 |
| --- | ---: | --- | --- | --- | --- |
| 原缓存 | 14 | 2/5/4/3 | 0.8750 / 0.7857 | 3.7857 / 3.2143 | 0/14 / 3/14 |
| 本次补齐 | 46 | 19/7/11/9 | 0.9333 / 0.9278 | 3.7556 / 4.0000 | 2/45 / 1/45 |

同一个模型别名不证明历史与当前后端权重完全相同。原缓存不含完整 HTTP 尝试记录；本次只保证与已记录配置/请求拼法一致，不反推历史温度兼容重发情况。

### 重组比例、例子与口吻

正式 60 条中 **重组 0/60（0%）**；其中 57 条显式输出重组/行动字段，缺失项按任务卡当作没有；思绪/带着字段为 60/60。不能据此声称语言器官改善了主盲评回答。此前未编号探索臂中有 1 次重组，但该臂不参与最终评分。

为满足三个重组前后例子的报告要求，另用 A01/A12/A13 的最终 Mind 意思做了 **3 次显式语言组件烟测**：不是 Mind 自主选择，不替换任何盲评答案，不纳入采纳判断。

#### A01（独立语言组件烟测）

- 重组前：嗯，累就对了，今天该做的都做了。回去路上别想实验的事，洗个澡就睡，明天睡到自然醒。第二轮周一才做，今晚没有欠账。
- 重组后：累是应该的，今天该做的都做完了。回去路上别再过实验的事，洗个澡就睡，明天睡到自然醒。第二轮周一才做，今晚没有欠账。

#### A12（独立语言组件烟测）

- 重组前：又是十一点到两点那种？晚饭先吃，别拖到成像完再点麻辣烫。
- 重组后：又是十一点到两点那种？  晚饭先吃。别等成像完再点麻辣烫。

#### A13（独立语言组件烟测）

- 重组前：knockdown那边有动静了吗？明天汇报，你打算讲哪部分——是补实验的进度，还是先把图四的成像结果放进去？
- 重组后：knockdown那边有动静了吗？明天汇报你打算讲哪部分——补实验的进度，还是先把图四的成像结果放进去？

口吻观察来自模型评语及可直接观察的文本变化，不由本助手另打分：A30 对推荐数量/简洁程度两遍意见相反；A01 的评语偏好 P8 的简短承接，A17 对两边措辞的评价也有分歧。三个组件示例主要改变分段、词序或删去少量语气词，保留原意思；这不是对原意思正确性的再审核。

评审也有局限：证据包仅采样历史，某些“说错”评语用“证据里没有”代替反驳；A01 评语还提及探针时间之后的日期。本报告保留原判断而不人工改分，“标错率”不等于经过独立事实核验的真实错误率。

## 默认选择

**mind.protocol = a1。** 总体偏好胜负略有利于 A2，但有 12 条换序分歧、要点覆盖下降、标错增加；原缓存 14 条子集的要点和 alive 均下降，主臂又完全未选择重组，无法证明移走人物背景后的稳定收益。选择保守默认，A2 完整实现作为显式选项；没有修改附录提示词、加口吻补丁或改变 Memory 参数来补救。

## 新增真实调用与 token

下面包含所有成功响应、格式失败、隔离/重跑和组件烟测。HTTP 发送前 SQLite 预留；没有自动网络重试，达到 300 即阻止发送。均为 **deepseek-flash**；A1、离线测试、cache-only 回放为 0 次。

| 用途 | HTTP 次数 | input_tokens（未缓存） | cache_read_input_tokens | output_tokens | token 合计 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 补齐 P8 对照 | 46 | 110,524 | 43,904 | 2,880 | 157,308 |
| Mind 新臂（含隔离/重跑） | 121 | 226,977 | 109,568 | 22,989 | 359,534 |
| 探索臂 Language | 1 | 1,566 | 896 | 95 | 2,557 |
| 盲评（含格式失败） | 26 | 156,701 | 35,200 | 12,418 | 204,319 |
| 3 个语言组件示例 | 3 | 3,397 | 2,944 | 92 | 6,433 |
| **合计** | **197 / 300** | **499,165** | **192,512** | **38,474** | **730,151** |

另执行 1 次只读 /models 元数据 GET 核对输出上限，不是生成调用，没有 token 用量。缓存写入 token 为 0；总输入 691,677，输出 38,474，总量 730,151。121 次 Mind 调用中 1 次时区不一致、60 次未带引用编号的探索臂被隔离，最终有效新臂 60 次。26 次评审中 4 次格式校验失败（3 次 should_not 长度、1 次 note 类型），均保留计数；有效评审批次 22 次。197 次请求均收到响应；没有隐去未知费用或失败尝试。

## 偏差、限制与阶段 B

1. 起始工作树不干净：先保存前轮改动作本地基线。archive/pre-organs-a 保留原 HEAD；实际任务 diff 以 11849af 为准。Git 身份缺失使建分支/标签先于准备提交，已记录，未改写远端。
2. A1 为保持旧 API compaction 字段，首句 HTTP 回执等本次压缩完成；A2 在首句持久化后返回。超时不取消后台事件；无发言的思考完成时返回旧降级提示，不伪装成已写 Hot 的发言。
3. 完整 P8 回答缓存不存在，新增 46 条对照；60 个评测条目沿用现有全部时间变体，不跑保留集。14 条历史和 46 条新增分开报告。盲评格式骨架使用既有恢复机制的等长 null 数组提醒，未改评分提示词或 rubric。
4. 实评中发现 UTC/+08:00 接缝差异，隔离 1 次错误上下文调用；补齐引用编号后全量重跑新臂，旧材料和费用全部保留。相同 cache-only P8 仍零差异。
5. 正式臂重组比例为 0%，不能提供三个自主选择的重组例子；明确分离的 3 次语言组件烟测仅证明接口可调用，不作为行为收益或口吻改善证据。
6. A1 保留原 1000-token 请求；A2 生产配置输出上限为 393216（官方 /models 元数据，DeepSeek-V4.1-Flash，384×1024），冻结盲评仍用 P8 的 1000。此优先级用于同时满足 A1 字节要求及盲评参数控制。上下文语义字段只做宽容解析，不重试。思绪长度上限作用于状态与后续上下文；原始模型响应为满足恢复要求完整保存在痕迹中。
7. 只读/多步/插入/思绪长期规则由确定性测试验证；60 个真实探针均一步完成，不证明长期真实交接能力。自动 Dream 窗口逻辑保持原样，长时多 assistant 的记忆质量未另跑实验。没有改人物背景或任何冻结 Memory 提示词。
8. 恢复覆盖任务指定的进程中断点；SQLite 与 Hot/Cold 不是跨文件原子事务。依靠冻结响应、持久化动作 ID 和 owner 幂等恢复，不承诺任意磁盘损坏/断电恢复。请求已发送但响应未落盘时标为未知并降级，不自动重发。仍要求服务单进程（--workers 1）。
9. 阶段 B 删除旧链时必须保留新的 runner/bus/event_triggers/scheduler/lumina_state/dialogue_state/Language 文件和 core owner；Execution、任务表、执行状态、帮手池未连接。事件归档仍未实现。
10. 本次只向远端发布本任务卡和结果报告；实现、ORGANS/README/AGENTS 等留本地提交。原始评测、预算账本、截图和日志归档于本地备份目录。已有被 Git 跟踪的嵌入缓存因回放新增了记录，保留原地、不提交、不推送；未移动或删除 BGE 缓存。
