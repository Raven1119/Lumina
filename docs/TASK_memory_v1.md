# 任务卡（/goal）：对话记忆 v1 接入 Chat，并清理旧方案与旧实验

## 目标

把 Memory_lab 中经过验证的记忆方案（预设 P8 的记忆侧，下称“记忆 v1”）接入 Chat。它要取代：

- 旧的 Conversation Memory：MAGMA、FirstHit、reliable-v2、各个语义联想候选；
- Chat 的召回门控。

接入之后，清理旧方案与旧实验。一次做完，中途不要停下来问仓库主人。

## 授权范围

本任务卡就是仓库主人的授权，覆盖以下各项：

- **可以修改：**
  - `core/`、`Conversation_Memory/`、`Dream/`、`Mind/`、`scripts/`、`experiments/`、`tests/`、`docs/`、`Memory_lab/`、`edge/static/`、`prompts/`、`Lumina_Canvas/`；
  - 根目录的 `AGENTS.md`、`README.md`、`.env.example`、`.gitignore`、`.gitmodules`、`requirements*`、`pyproject.toml`。
- **可以删除：** 阶段 3 清单中的文件。
- **可以提交和推送：** 按“决定”第 6 条执行。

**不在范围内：**

- 不改变认知链（`python -m Mind`、Nervous、Execution）的行为；
- 不把认知链接入 Chat；
- 不改动 Hot 与 Cold 已存数据的内容。

## 先读，再动

依次读：

1. 根目录的 `AGENTS.md`、`README.md`，以及 `docs/CURRENT_STATUS.md`、`docs/EXPERIMENTS.md`、`docs/COLD_DRAFT.md`；
2. `Memory_lab/docs/` 下的 `DESIGN.md`、`DESIGN_v5.md`、`DESIGN_v5_1.md`、`DESIGN_v5_2.md`，以及 `RESULTS_v5_1.md` 与 `RESULTS_v5_2.md` 的结论部分；
3. `core/` 下的 `main.py`、`message_runtime.py`、`model_client.py`、`hot_draft_compactor.py`、`cold_draft_store.py`、`draft_context.py`、`contracts.py`，以及 `Dream/runner.py`、`edge/static/app.js`；
4. 实验室的 `lab/llm.py`、`lab/replay.py`、`lab/answer.py`、`memlab/store.py`、`memlab/embed.py`、`memlab/integrate.py`。

**用哪个分支：**

- 远端 `Execution_lab2` 只有文档，没有 v5 到 v5.2 的代码，而且与本地历史已经分叉。
- 本任务在本地那条含有 P8、P9 代码的分支上进行。先确认工作树干净，再开始。
- 实验室文档若本地版本与远端发布版本（`b4a5620`）不同，以远端为准。

## 决定

仓库主人如有不同意见，只需改本节对应的一行。

1. **记忆 v1 的范围：**
   - 记忆侧沿用 P8 的全部参数：pattern_v2 归纳、integrate_v4、render_v4、联想位按意外程度挑选。“用上”的原话核对，用 P8 的常用词表规则。
   - 实体名规则和宽容解析都属于 P9 的参数，不进入记忆 v1。
   - Chat 的回答侧：
     - 用 answer_v5；
     - 若接口支持预填，就用预填；
     - 每轮只调用一次，用宽容解析，不重试。
   - 隐性痕迹（P9）不接入 Chat。
2. **模型：**
   - 回答和滚动摘要，沿用现有运行时策略 DeepSeek-V4-Pro，现有 `LUMINA_MODEL_MODE` 机制不变。
   - 记忆侧的整合与归纳，使用 P8 验证时的 flash 模型。模型 ID 从本地实验室的 flash 配置中取，必须是 v5.1 缓存键中的那个准确字符串，不要自己猜。
   - 新增 `LUMINA_MEMORY_MODEL` 可以覆盖记忆侧模型。记忆侧要有自己的客户端工厂：`build_model_client_from_env` 遇到非 Pro 的模型会返回 Mock，不能直接复用。
   - AGENTS.md 里的模型策略按此更新。
3. **Dream 自动触发：**
   - 触发条件：
     - 回复送出之后；
     - 当前是真实模型模式；
     - 嵌入模型可用；
     - 记忆库已有 Cold 游标；
     - 未整合的 Cold 轮次 ≥ `LUMINA_DREAM_TRIGGER_TURNS`（默认 40）。
   - 满足时在后台串行跑 Dream，每次最多处理一个窗口。
   - Dream 有自己的锁，锁被占着就跳过这一次。
   - 调用模型期间不持有 Chat 的写锁；只有读取 Cold 和提交记忆写入那两小段需要协调。
   - 同一个窗口连续失败 3 次后，暂停自动 Dream，并在 `/api/status` 中显示，等仓库主人显式运行。
   - `POST /api/dream/run` 和 CLI 保留。
   - Dream 失败或忙碌，都不影响聊天。
4. **每轮都召回，不再有门控：**
   - 召回只做本地计算，只读最近一次提交的快照，失败时回退为无记忆；
   - 不再有布尔门控，也不再有召回后的模型选择。
5. **用真实 Cold 重建：**
   - 重建通过显式命令执行，执行时服务要停止，并受阶段 4 的预算闸门约束。
   - 记忆库还没有游标时，自动 Dream 不启动，以免它自己去消化整个积压的 Cold。
   - 仓库主人也可以选择不重建，直接把游标设到 Cold 末尾，从今以后开始积累。
   - 旧的 MAGMA 数据原样保留、不再使用，不删也不移动。
6. **提交与推送：**
   - 所有代码只在本地提交，放在新分支 `memory-v1` 上；
   - 在开始任何修改之前的 HEAD 上打本地标签 `archive/pre-memory-v1`，只在本地；
   - 远端沿用此前“只推文档”的做法：只把 `docs/TASK_memory_v1.md` 和 `docs/RESULTS_memory_v1.md` 推到 `Execution_lab2`；
   - 不推送代码、评测材料或缓存，不改写、不强推任何远端分支。

## 约束

- **数据和凭据：**
  - `.env.local`、密钥、`data/` 和真实记忆都不能丢，也不能提交。
  - 开始前，先找出服务实际使用的 Hot、Cold、游标和 MAGMA 路径（包括 `.env.local` 和进程环境指定的路径），把它们完整复制到仓库之外的备份目录，并在报告中写明位置。
  - 新记忆库的默认目录为 `data/memory_v1/`，不要放进 `data/conversation_memory/`。
  - 所有测试都用临时目录。
- **git 安全：**
  - 禁止 `git add -A`、`git clean -x`、`git stash -u` 和 `git stash -a`，只按路径暂存；
  - 这样可以避免误卷入 `.venv`、`data/`、`.env.local`，以及 `Memory_lab/cache`、`runs`。
- **运行环境：**
  - 解释器用 `Conversation_Memory/.venv/bin/python`。这个未跟踪的虚拟环境和 BGE-M3 权重缓存都不能删除或移动；清理 `Conversation_Memory/` 时绕开 `.venv`。
  - 删除 MAGMA 子模块前，先检查它的检出目录里有没有未跟踪的文件；有的话不强删，在报告中说明。
- **测试基线：**
  - 修改之前，先记录三个基线：`python -m pytest -q`、`python -m pytest Mind Nervous Execution -q`、实验室离线测试；
  - 环境原本就会失败或跳过的项目，照原样记下；
  - 验收标准是“没有新增失败”。
- **模型预算：**
  - 除阶段 4 的真实数据重建外，新增真实调用合计不超过 20 次；
  - 实验室回归一律 cache-only。
- **不拆出新的管理层：**
  - Chat 归 `core/`，记忆归 `Conversation_Memory/`，显式 Dream 入口仍是 `Dream/runner.py`，Hot 和 Cold 归 `core/` 的 owner。
  - 记忆库里对 Cold 轮次的镜像表，允许作为派生、可重建的索引保留，但 Cold 始终是原文唯一的来源。

## 阶段 1：把记忆 v1 提升为正式器官

1. **基线（改动任何代码之前）：**
   - 记录上面的三个测试基线；
   - 在未改动的 HEAD 上，用 cache-only、不带 `--answer`，在 dev_a 和 dev_b 上各回放一次 P8，作为参考结果 R0；
   - 把 R0 与 v5.1 归档的 P8 运行目录比较，把目录名写进报告；
   - 比较范围只包括记忆层，不含回答、耗时和 git 版本：
     - `probes.jsonl` 每条的 recall（近联想、联想位、心上之事的 ID 和分数）、`rendered`、`score`、`measure`；
     - `summary.json` 的 `by_category`、`measure` 和归因部分。
   - R0 与 v5.1 归档不一致时，不要在本任务里修，只在报告中记录差异。之后所有逐字节比较都以 R0 为参照。
2. **标签与分支：** 在同一个 HEAD 打本地标签 `archive/pre-memory-v1`，然后建立分支 `memory-v1`。
3. **盘点（先不删任何东西）：**
   - 对阶段 3 清单中的每个文件做依赖扫描：谁 import 它、哪些测试覆盖它、哪些文档链接它；
   - 把结果写成报告的第一张表：保留、删除还是改写，以及理由；
   - 清单里没写、但扫描发现只被旧方案使用的文件，一并列入，按同样标准处理。
4. **搬迁：**
   - 用 `git mv` 把 `Memory_lab/memlab/` 搬进一个具名子包，例如 `Conversation_Memory/engine/`。不要平铺到 `Conversation_Memory/` 下，那里还有旧的 `recall/` 等包，import 会冲突。
   - 所有依赖目录位置的路径改为显式传入：HF 缓存目录、嵌入缓存、提示词目录、记忆库目录。
   - 嵌入模型：
     - Chat 只接受 BGE-M3（固定 revision），不许退回 MiniLM、auto 或哈希嵌入；
     - 模型标识不依赖本地目录名。
   - 新的缓存目录加入 `.gitignore`。
   - P8 用到的提示词（`integrate_v4`、`pattern_v2`、`answer_v5`），移到记忆器官下的 `prompts/`，文本逐字不变。
   - 实验室改为引用这些文件。缓存键里的 `purpose`、`max_tokens`、`temperature`、`attempt` 和模型名都不改。
   - 实验室的摘要提示词在实验室内冻结一份副本，不再从 `core/` 导入，以免阶段 2 修改摘要后，实验室的缓存失效。
5. **门面：** 只暴露 Chat 和 Dream 需要的接口。
   - **召回并渲染：** 不写任何东西。输入消息、Hot、当前时刻，返回记忆块，以及进入上下文的记忆 ID。
   - **记录痕迹：** 回答生成之后调用。以 assistant 轮次 ID 为键，记录进入上下文的记忆 ID 和三栏内容。
   - **一次 Dream：** 处理一个窗口。构建提示词时，把本次要消费的痕迹 ID 快照下来，只消费这些，Dream 进行中新追加的痕迹留给下一次。
   - **只读查看：** 列出记忆、规律，以及 Dream 日志。
6. **线程与副作用：**
   - SQLite 每个线程用各自的连接，开启 WAL；
   - 导入模块时不打开记忆库，也不加载嵌入模型，都等到第一次使用时再做；
   - 缺少依赖或加载失败时，召回按空处理，不能让应用在导入时崩溃；
   - 测试不能创建或改动真实的 `data/`。
7. **实验室改为依赖正式器官：**
   - `Memory_lab/lab/` 改为 import 新器官；
   - 实验室的 P8 预设只引用记忆侧参数；
   - `Memory_lab/AGENTS.md` 改写为“实验室是记忆器官的评测设施”。
8. **回归（零模型调用）：**
   - 实验室离线测试相对基线没有新增失败；
   - 两集的 P8 cache-only 回放，记忆层与 R0 逐字节一致。

## 阶段 2：接入 Chat

1. **消息流程只保留一条路径（`core/message_runtime.py`）：**
   1. 读取 Hot（全部原始轮次，与实验室一致）和滚动摘要；
   2. 召回并渲染；
   3. 用共享的回答请求拼法组织请求，system 用 answer_v5，人设为 `prompts/chat_background.md`；
   4. 调用模型，解析回复；
   5. 写入 Hot 和逐轮来源；
   6. 记录痕迹；
   7. 按原规则压缩进 Cold，并更新滚动摘要；
   8. 检查是否要触发 Dream。

   **回答请求的拼法只能有一份实现**，实验室和 Chat 共用。
   - **拼法规则同 answer_v2：**
     - Hot 中他说的每句话带时间；
     - 当前消息注明距离上一句过了多久；
     - 摘要作为一条带“截至某日”的用户消息。
   - **摘要日期：** 滚动摘要要记录它截止到哪一轮的时间（新增字段）。旧摘要没有这个字段，就省略日期那一行。
   - **人设文本：** 实验室按原样读取人设，Chat 按现在的处理方式读取（去空白、处理 BOM）。共享拼法只接收处理好的文本，这样实验室的缓存键不会变。
2. **把 DraftTurn 转换成记忆器官的输入（在 `core` 一侧）：**
   - 时间统一换算到 +08:00（取自 `LUMINA_DEFAULT_TIMEZONE`）；
   - 时钟一律注入，不直接读系统时间。
3. **answer_v5 的输出格式：**
   - 先用 1 次真实调用验证，Anthropic 兼容接口是否接受“最后一条 assistant 消息作为预填”。验证时预填 `{"理解": "`。
   - 支持就启用预填。
   - **解析：**
     - 有 json 且有“回复”，就取“回复”；
     - 有键但格式坏了，用宽容解析取出“回复”；
     - 完全是自然语言时，整段就是回复。
   - 不重试。三栏内容永远不进入发给他的回复。
4. **Dream：**
   - `Dream/runner.py` 保留为显式入口，内部改为调用新器官；
   - **游标：** 新器官自己记录“已整合到 Cold 的哪一轮”，不再依赖 Cold 旧的 pending 和 consumed 状态，Cold 原文不变。`Dream/runner.py` 提供两个显式命令：
     - 从 Cold 开头重建（阶段 4）；
     - 把游标直接设到 Cold 末尾（不重建，从现在开始积累）。

     两个命令都要求服务处于停止状态。
   - **自动触发：** 按“决定”第 3 条执行，不持有 Chat 写锁调用模型，只在提交写入时短暂协调；
   - **失败：** 解析失败时窗口保持未整合，按上限重试；
   - **轮次 ID：** 在提示词里，Chat 的 32 位十六进制轮次 ID 换成窗口内的短别名（如 `t01`），解析时再换回原 ID。实验室本来就是短 ID，别名对它必须是恒等映射，缓存键不变。
5. **修复滚动摘要截断（只在 Chat 中生效）：**
   - 摘要的 `max_tokens` 与回答分开设置：摘要 2000，回答保持原值；
   - 只按接口返回的停止原因判断是否截断，不按标点判断；
   - 截断时，以上限 3000 重试 1 次；仍然截断，就照常压缩，但保留上一版摘要，并在状态中记录。被移出 Hot 的原文已经进了 Cold，仍可通过原文窗口被召回。
6. **状态、接口与前端（`core/contracts.py`、`core/main.py`、`edge/static/app.js` 一起改）：**
   - **`/api/status`：**
     - 去掉 MAGMA 相关的 `writer_version`、`reader_profile` 和 Cold 的 `pending_segments`；
     - 改为报告：记忆条数、规律条数、未整合的 Cold 轮次数、最近一次 Dream 的时间与结果、自动 Dream 是否暂停、嵌入模型是否可用。
   - **`/api/dream/run`：** 响应改为新 Dream 的结果，前端同步修改。
   - **`GET /api/memory`：**
     - 只读；
     - 列出记忆（正文、时间标签、π、是否规律、来源条数）、规律，以及最近的 Dream 日志；
     - 必须注册在 `/` 静态文件挂载之前；
     - 不暴露密钥、路径和调用栈。
   - **前端与 CLI：** 前端加一个最简的只读页面，并提供一个输出相同内容的 CLI 命令。
7. **配置：**
   - **删除：** `LUMINA_MEMORY_PROFILE` 的各个候选值、`LUMINA_MIND_GATE_MODE`、MAGMA 目录等旧配置，`.env.example` 一并更新。
   - **新增：**
     - `LUMINA_MEMORY_MODEL`；
     - `LUMINA_MEMORY_DIR`，默认 `data/memory_v1/`；
     - `LUMINA_DREAM_TRIGGER_TURNS`，默认 40；
     - `LUMINA_EMBED_MODEL_PATH`，BGE-M3 的本地路径。
   - **保留：** `LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED`。
8. **一致性验收（零模型调用）：**
   - 在实验室回放 `run_set` 的每个探针时刻，用不写痕迹的门面调用，走 Chat 一侧的转换路径：把实验室的 Hot 转成 DraftTurn，注入时钟，再经 core 的适配构造输入。
   - 生成的记忆块，必须与实验室该探针的 `rendered` 逐字节相同。
   - 两集全部探针都要通过。
9. **离线测试至少覆盖：**
   - **流程：**
     - 单一路径的消息流程；
     - API 层面，有记忆时记忆块非空（防止召回静默失败）；
     - 召回失败时回退；
     - 痕迹在回答之后以 assistant 轮次 ID 写入。
   - **Dream：**
     - 触发条件，逐条覆盖“决定”第 3 条；
     - 忙则跳过；
     - Dream 运行期间，聊天请求不会返回 409；
     - 连续失败后暂停；
     - 游标与重试；
     - 痕迹快照消费；
     - 短别名的双向映射。
   - **摘要：** 截断的检测、重试与保留旧摘要；摘要日期字段。
   - **回答：** 预填开关；三种解析路径；三栏不进入回复；共享拼法对实验室的输出不变。
   - **接口：**
     - `/api/status`、`/api/memory`、`/api/dream/run` 的新契约，以及与之对应的前端字段；
     - 多线程访问 SQLite；
     - 导入时没有副作用。

## 阶段 3：清理旧方案与旧实验

按阶段 1 的盘点表执行。下面是基础清单，盘点发现的同类文件照此处理。所有删除的内容都保留在 git 历史和本地标签 `archive/pre-memory-v1` 中。

**删除：**

- **旧记忆器官：** `Conversation_Memory/` 下的 `adapter/`、`ingestion/`、`recall/`、`fixtures/`、`docs/`、旧的 `tests/` 和 `MAGMA_COMMIT.txt`。
  - `upstream/MAGMA` 子模块连同 `.gitmodules` 一起删除，没有其他子模块时，整个文件删掉。
  - 删除时绕开 `.venv`。
- **旧 Dream：** `Dream/cold_draft_digest.py`，以及只服务于旧消化流程的 `models.py`、`interfaces.py`、`docs/`、`tests/`。
- **Chat 召回门控：** `Mind/` 下的 `llm_gate.py`、`constant_gate.py`、`evidence_selector.py`、`decision_log.py`、`interfaces.py`、`docs/CHAT_RECALL_GATE.md`。经核查，认知链不 import 它们；执行前再确认一次。
- **core 中的旧路径：**
  - `message_runtime.py` 中各候选分支和 grounding 检测；
  - `cold_draft_store.py` 中依赖旧 adapter 的原文检索，这段不删会让应用启动时崩溃；
  - `evidence_acquisition.py`（如果只服务于旧路径）；
  - `main.py` 中旧 adapter 的组装。
- **脚本与实验：**
  - `scripts/` 下的 `first_hit_memory_check.py`、`mind_gate_*.py`、`mind_promotion_controls.py`、`recall_e2e_test.py`；
  - `experiments/mind_relation.py`。
- **测试：**
  - 只覆盖已删代码的测试，比如：
    - `tests/` 下的 `test_semantic_associative*_chat.py`、`test_calibrated_memory_chat.py`、`test_body_memory_chat.py`、`test_query_mind_*.py`；
    - `test_memory_evidence_selection.py`、`test_mind_gate*.py`、`test_mind_llm_gate.py`、`test_mind_promotion_controls.py`、`test_mind_relation_shadow.py`；
    - `test_first_hit_memory_check.py`、`test_recall_e2e_script.py`、`test_grounded_historical_claim_guard.py`。
  - **需要保留但依赖已删模块的测试，要改写，不能删：**
    - `test_execution_api.py`（AGENTS 要求保留）；
    - `test_chat_api.py`（去掉按门控模式的参数化）；
    - `test_message_runtime.py`、`test_history_api.py`、`test_evidence_acquisition.py`；
    - `test_cold_draft_progress.py`、`test_cold_source_window.py`。

    其中只测旧代码的部分删掉，其余改写到新路径上。
- **文档与图：** `docs/MAGMA_RECALL_ALGORITHM_AUDIT.md`、`docs/RECALL_E2E_ACCEPTANCE.md`、`Lumina_Canvas/Lumina_MAGMA.canvas`。
- **实验室的旧实验材料：**
  - `Memory_lab/answers_v1/`、`answers_v2/`、`judge/rounds/`；
  - B1、P8、P9 以外的全部预设，以及只被它们使用的代码路径、提示词和测试。
  - 删完之后，阶段 1 的逐字节回归必须仍然通过。

**保留：**

- `Memory_lab/eval_set/`、评测框架 `lab/`、盲评工具（证据包、`judge_prompt_v2`、汇总脚本）；
- 预设 B1、P8、P9。P9 是唯一保留的候选，在 Chat 中关闭。
- 实验室全部 TASK、DESIGN、RESULTS 文档，移到 `Memory_lab/docs/history/`；
- Cold、Hot、逐轮来源的数据和 owner；
- Execution、Mind、Nervous 认知链。

**改写：**

- **`Conversation_Memory/docs/DESIGN.md`：** 记忆 v1 的权威设计，按实际实现写，合并 DESIGN、v5、v5.1 中生效的部分；已废弃的机制只在“历史”一节里各留一句。
- **新的 `Conversation_Memory/AGENTS.md`**。
- **`Memory_lab/docs/HISTORY.md`：** 一页讲清 v1 到 v5.2 的关键决定、结论和废弃原因，链接到 `history/` 下的原文档。
- **`docs/MEMORY_EXPERIMENT_HISTORY.md`：** 压缩成一节“旧方案（MAGMA 路线）”：写明它是什么、为什么被取代、本地归档标签；另加一节，指向 `Memory_lab/docs/HISTORY.md`。
- **描述记忆、门控或 Dream 的文档，更新其中的记忆部分：**
  - `docs/` 下的 `EXPERIMENTS.md`、`CURRENT_STATUS.md`、`COLD_DRAFT.md`、`DRAFT_TURN_PROVENANCE_V2.md`、`final_goal.md`、`plan/` 下的相关文件；
  - `README.md`、`Dream/AGENTS.md`、`Mind/AGENTS.md`、`Mind/docs/INTEGRATED_CHAIN.md`、`Lumina_Canvas/Lumina_Memory_Architecture.canvas`。
  - Cold 的 consumed 状态不再驱动 Dream，这一点要写明。
- **根目录 `AGENTS.md`：**
  - **替换：** 与旧方案绑定的规则，包括固定 MAGMA、`(segment_id, ingestion_version)`、真实 MAGMA 验收、“Dream 只显式同步运行”、“Chat 不自动 Dream”、模型策略。
  - **新规则写明：**
    - Dream 是记忆唯一的写者，Chat 只追加召回痕迹；
    - Dream 有自己的锁，调用模型时不持有 Chat 写锁；
    - 提示词按版本号管理；
    - 召回的改动必须通过阶段 1 的逐字节回归和阶段 2 的一致性验收。
- **`requirements*`：**
  - 去掉 MAGMA 相关依赖，加入记忆 v1 需要的依赖；
  - 只固定顶层依赖的版本，不要把 Linux 的 `pip freeze` 结果照搬进去。Windows 目标平台本次没有验证，在报告中写明。
- **`pyproject.toml`：** 测试收集范围随目录变化更新。

**清理后的验证：**

- 三个测试基线都没有新增失败，认知链的行为不变；
- 在代码和配置文件中（历史文档除外），`git grep` 查不到这些词的运行时引用：`MAGMA`、`FirstHit`、`semantic-associative`、`LUMINA_MIND_GATE_MODE`、`Conversation_Memory.adapter`；
- `git diff --check` 通过。

## 阶段 4：冒烟与真实数据

1. **冒烟（真实调用 ≤ 20 次，隔离的临时状态）：**
   - **准备：** 用注入的时钟和较低的压缩阈值（例如保留 2 轮、超过 4 轮就压缩）。直接通过 Cold owner 预置一段跨 3 个逻辑日的合成 Cold，并把游标设在它的开头。
   - **预计调用，开跑前先列出：**
     - 预填验证 1 次；
     - 回答 4 次；
     - 摘要约 2 次；
     - Dream 整合约 2 次；
     - 归纳至多 2 次。
   - **断言：**
     - 回复正常，三栏和痕迹都已记录；
     - Dream 被自动触发并成功应用；
     - 记忆在 `/api/memory` 中可见；
     - Dream 期间聊天没有返回 409；
     - 有候选组时写出了规律；没有候选组时，写明原因。
2. **真实 Cold 重建（显式命令，服务停止时执行）：**
   - **统计：** 统计真实 Cold 的轮次数和总字数。没有时间戳的旧片段不能用于重建，统计后跳过，并写进报告。
   - **估算：** 按真实字数估算 Dream 窗口数和输入 token，不要套用实验室的每窗口平均值。
   - **闸门：**
     - 估算 ≤ 50 万输入 token：执行重建。
       - 每个窗口的“现在”取该窗口最后一轮的时间（模拟时钟）；
       - 历史上没有痕迹，所以没有“用上”强化，这是预期的；
       - 实际用量一旦超过 50 万，立即停止，并保留已完成部分的游标。
     - 估算超出，或工作区里没有真实 Cold：不执行，只把估算和重建命令写进报告。
   - **失败：** 同一个窗口失败 3 次，就停止重建并报告，不许跳过内容。
   - **完成后报告：** 记忆条数、规律条数、随机 10 条记忆，以及全部规律原文。

## 阶段 5：报告与提交

新建 `docs/RESULTS_memory_v1.md`，必须包含：

1. **盘点表：** 每个文件保留、删除还是改写，以及理由；
2. **基线：** 三个测试基线；R0 与 v5.1 归档的比较（含运行目录名）；
3. **代码结构：** 搬迁后的结构，以及新旧入口对照；
4. **零调用验收：** 阶段 1 逐字节回归（搬迁后、清理后各一次）和阶段 2 一致性验收的结果；
5. **接入说明：**
   - Chat 的新流程；
   - 配置表；
   - 自动 Dream 的行为和锁的设计；
   - 预填验证的结论；
   - 摘要修复；
   - 状态与接口的变化；
6. **冒烟：** 结果、调用次数和 token 数；
7. **真实数据：** 备份位置，以及重建结果或估算；
8. **清理后的测试结果：** 与基线对照；
9. **偏差和未解决的问题：** 每条附证据，并说明 Windows 没有验证；
10. **下一步建议：** 只写有证据支持的。

**提交与推送：**

- 所有改动按阶段在本地提交到 `memory-v1`，只按路径暂存；
- 标签 `archive/pre-memory-v1` 只留在本地；
- 按此前只推文档的做法，把 `docs/TASK_memory_v1.md` 和 `docs/RESULTS_memory_v1.md` 推送到 `Execution_lab2`，并用 `git ls-remote` 确认。
- **不提交：** `data/`、备份、`runs/`、`cache/`、`.env.local`、虚拟环境、模型权重、压缩包。

## 验收

- [ ] 阶段 1：P8 cache-only 回放的记忆层与 R0 逐字节一致，搬迁后、清理后各验一次；R0 与 v5.1 归档的比较写进了报告。
- [ ] 阶段 2：Chat 的记忆块在两集全部探针上与实验室逐字节一致；Chat 只有一条记忆路径。
- [ ] 自动 Dream 满足“决定”第 3 条的全部条件；Dream 期间聊天不会返回 409；摘要截断已修复并有测试。
- [ ] 旧方案已按盘点表删除；三个测试基线都没有新增失败；认知链行为不变。
- [ ] 真实数据已备份到仓库外，没有丢失；旧 MAGMA 数据原样保留；新记忆库在 `data/memory_v1/`。
- [ ] 真实调用：冒烟不超过 20 次；重建受预算闸门约束。
- [ ] `docs/RESULTS_memory_v1.md` 满足阶段 5 的要求；远端只多了两份文档；代码和标签只在本地。
