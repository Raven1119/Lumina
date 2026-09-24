# Lumina 方案目录

从 [代码地图](../README.md) 找现役功能；本页回答替代方案保留什么、怎样单独跑、
证据属于哪个版本。**最新实现不等于最佳方案。候选均须显式选择，生产默认未晋升。**

## 整理基准与分类

- 开始时是 `main` / `d701f2bfbfd31d77a5d6a1b56232aca0104f7051`，仅
  `AGENTS.md`、`docs/final_goal.md` 有平台约定改动，无未追踪材料。
- 用户确认改以 `origin/Execution_lab2` 为基准：
  `f92ea8e7b1370b5f7b05e0627fa87ee66d5b1e25`。当前整理分支
  `organize/lumina-current`；平台约定已保留。旧 main 的本次整理曾另存于会话临时 patch/tar，未混入新版算法；
  这些临时副本不属于持久交付，当前目录已失效。
- 整理过程未改写既有 Git 历史；提交与推送按用户后续授权执行。原始对话、长期记忆、执行状态、密钥和既有研究材料
  不用作试验输入。本次临时验证在 `/tmp/lumina-cleanup-*`。
- **现役**：当前默认和仍明确支持的调用路径；**候选**：显式选择的独立机制；
  **历史**：被完整承接且无必要兼容职责的实现/阶段材料。旧格式读取不算候选副本。

## 现役基线与必要兼容

| 功能 | 当前实现 | 保留的兼容边界 |
| --- | --- | --- |
| 真实模型记忆写入 | `grounded-formation-v6`，`reliable_formation.py` / `_entity_ingestion.py` | v1（adapter 直接调用）、v2/v4/v5 显式写入和各自 receipt/checkpoint 仍受支持；不能按版本号删掉或用 v6 解释旧状态 |
| 真实模型记忆读取 | FirstHit + `reliable-v2`，canonical 正文始终可见，原文为补充；无 BGE/Hindsight | `reliable-v1` 的来源替代式呈现、`first-hit-v1` 和 legacy BGE 门面仍有明确调用/恢复测试 |
| mock / legacy 消化 | `grounded-span-v2`；原始来源与确定性投影 | 旧 ingestion key、来源字段与读取职责保留，不自动重建记忆 |
| Chat | v2 布尔门控 → 有界读 → Answer | `constant` 与 mock 回退；关闭 Recall；`direct/select` 见下文 |
| 认知链 | `python -m Mind`；单目标、context=baseline、repetition=off | `ExecutionOrgan` 是链和 API 仍调用的 facade，不由 `runtime.py` 完整替代 |
| 恢复 | 各器官 journal/receipt、未知结果停止 | 旧 working-context-v1/v2/v3 冻结尝试，旧认知活动的 current/commit 规则都保留；新摘要 v4、新 pursuit 活动接口 v2 |

v4 → v5 把正文持久化与图绑定解耦；v5 → v6 移除归属关键词筛选，使用 origin + F2
核验。`reliable-v1` → `reliable-v2` 改进呈现，不改 FirstHit 的选择。
这些替代关系见 [可靠记忆契约](../Conversation_Memory/docs/RELIABLE_MEMORY.md)。
当前推荐入口只展示 v6/reliable-v2；旧分支集中在同一 owner 的版本分派及检查函数里，
没有复制器官。测试继续覆盖它们的状态兼容。

## 可运行候选

所有命令从仓库根目录执行。以下 `pytest` 入口使用合成输入、注入响应和临时状态，
是无需 API 密钥的最小运行材料。真实 CLI 和 API 候选可能调用模型；本次只跑离线验证。
`python` 指已安装相应依赖的环境，Linux Memory 环境可用
`Conversation_Memory/.venv/bin/python`。不为这些冒烟下载 BGE。

### G1：Chat 在何时读、如何选择证据

同一替换位置：`create_app` / `MessageRuntime` 的记忆消费方式。

| 路线 / 保留版本 | 与默认差异、实际入口 | 无模型冒烟 |
| --- | --- | --- |
| 生产 v2 boolean gate | `LUMINA_MIND_GATE_MODE=llm`，8-token 门控；[llm_gate.py](../Mind/llm_gate.py) | `python -m pytest tests/test_mind_llm_gate.py -q` |
| direct，当前源码 | `LUMINA_MIND_GATE_MODE=direct`，取消预读门控，以原问题读一次后回答；[message_runtime.py](../core/message_runtime.py) | `python -m pytest tests/test_memory_evidence_selection.py tests/test_chat_api.py -q` |
| select，当前 selector 协议 | `LUMINA_MIND_GATE_MODE=select`，同一次准备好的读取后增加一次证据 ID 选择；失败返回原 prepared context；[evidence_selector.py](../Mind/evidence_selector.py) | `python -m pytest tests/test_memory_evidence_selection.py Conversation_Memory/tests/test_prepared_recall.py -q` |
| semantic-associative-v1，独立候选 | `LUMINA_MEMORY_PROFILE=semantic-associative-v1`，保留 `LUMINA_MIND_GATE_MODE=llm` 缺省值；实际运行时由读后一次 Mind 选择取代布尔门控，失败注入空记忆；[候选入口](../Conversation_Memory/adapter/_semantic_recall.py)、[Chat 接线](../core/main.py) | `Conversation_Memory/.venv/bin/python -m pytest Conversation_Memory/tests/test_calibrated_first_hit.py Conversation_Memory/tests/test_reliable_recall_dispatch.py tests/test_semantic_associative_chat.py -q`；[完整契约](../Conversation_Memory/docs/RELIABLE_MEMORY.md#semantic-associative-v1-explicit-reader) |
| semantic-associative-v2，显式后继候选 | `LUMINA_MEMORY_PROFILE=semantic-associative-v2`，缺省 `LUMINA_MIND_GATE_MODE=llm`；同一 Memory facade 准备完整卡片，经一次 Mind 排序建议和确定性三 Fact 装包后进入真实 Chat；失败注入空记忆，默认不变 | `Conversation_Memory/.venv/bin/python -m pytest Conversation_Memory/tests/test_calibrated_first_hit.py tests/test_semantic_associative_v2_chat.py -q`；[v2 契约与限制](../Conversation_Memory/docs/RELIABLE_MEMORY.md#semantic-associative-v2-explicit-reader) |

完整模式启动沿用 `LUMINA_MIND_GATE_MODE=direct python -m uvicorn core.main:app --workers 1`
（select 同理），每臂必须使用独立 Hot/Cold、审计和 MAGMA 路径，且相同初始记忆。
实现/失败回退机制已验证，语义效果没有在本次重测；已有原始实验仍保留在原版本本地资料，
契约见 [CHAT_RECALL_GATE](../Mind/docs/CHAT_RECALL_GATE.md)。

公平比较须固定原问题、近上下文、预先写好的记忆快照、模型 DeepSeek-V4-Pro、温度、
Answer 提示/预算和总调用/输出/request-byte 上限。默认 gate 是 3 facts，direct/select
是 20；直接比较其默认配置不能归因于门控机制。机制比较通过现有
`create_app(recall_policy=RecallPolicy(max_evidence_items=3, max_chars=5000,
include_source_context=True))` 对齐三臂，并使用相同 FirstHit 参数。
评价必要来源是否保留、未知/歧义是否正确处理、最终回答是否有来源支持、附带错误与总成本；
select 的 1024-token 选择开销计入总预算。

### G2：结构化 relation 提示（独立于 G1）

保留最新独立路线 **v3rel**：[experiments/mind_relation.py](../experiments/mind_relation.py)。
它扩展一次门控输出 `{recall, relations}`，把自然语言 relation 交给已有
`ControlledRelationResolver`，未接入 Chat。v3/v4 查询生成路线的退休不构成对此路线的替代证明。

它验证 **legacy structured Recall** 的 relation seam，固定 stub reranker，不加载 BGE。
它不证明当前 reliable-v2 的端到端效果；要接到新 reader 仍缺独立契约/能力验证。

```bash
work=$(mktemp -d /tmp/lumina-relation.XXXXXX)
cp experiments/fixtures/mind_relation_replay.jsonl "$work/replay.jsonl"
python -m experiments.mind_relation \
  --labels experiments/fixtures/mind_relation_cases.json \
  --log "$work/replay.jsonl" --analyze
python -m pytest tests/test_mind_relation_shadow.py -q
```

四个共享 fixture 来自原有效测试；replay 是明确标注 `model=none` 的手写合成响应，
不是历史模型输出。未来真实对比以相同 labels、DeepSeek-V4-Pro、temperature=0、
`--runs 3 --max-tokens 64` 分别跑 `--gate v2` / `--gate v3rel`，用不同 `--log`。
不指定预算仍保留旧默认 8/64。评价 recall 错放/错拦、协议通过、relation 正确性、OOV
失败开放和最终来源约束，不能只看 JSON 成功率。
历史结论绑定 `2fcc817` 的原模型/语料/提示，见 [Memory 历史](MEMORY_EXPERIMENT_HISTORY.md)，
不得移到新版 DeepSeek harness 或这四个合成用例名下。

### M：记忆发现、原文组织与主动获取

下列接口分工不同；不是一列按日期互相淘汰的版本，也不全部属于同一比较组。
实现都共用 Memory/Cold owner，没有另建器官副本。

| 独立路线 / 当前保留入口 | 解决什么问题、与生产差异 | 无模型最小运行材料 |
| --- | --- | --- |
| standalone FirstHit / `first-hit-v1`：`MagmaMemoryAdapter(..., ingestion_version="grounded-formation-v2", first_hit=FirstHitPolicy()).recall_associative` | 直接返回稀疏事实竞争/可选来源；现生产 reliable-v2 在同一激活上增加 direct/associated 分配和呈现约束 | `python -m pytest Conversation_Memory/tests/test_first_hit.py Conversation_Memory/tests/test_first_hit_recall.py -q` |
| 完整读侧候选 / `graph-read-v1`：同一 adapter 显式 `associative_read_profile="graph-read-v1"`，调用 `recall(str 或 GraphReadQuery, policy)` | 更新探索队列、保留线索贡献、按真实角色选择原 Fact；复用 reliable-v2 0.6 direct 和正文/原文预算。仅读配置，写入和生产默认保持原实现；自然语言未确认方向/组合时开放降级，不冒充精准解析 | `python -m pytest Conversation_Memory/tests/test_first_hit_read.py Conversation_Memory/tests/test_graph_read_query.py Conversation_Memory/tests/test_graph_read_facade.py -q`；[实际入口与限制](../Conversation_Memory/docs/FIRST_HIT_MEMORY.md#explicit-graph-read-v1-candidate) |
| 问题驱动读侧候选 / `graph-read-v2`：Chat 显式 `LUMINA_MIND_GATE_MODE=graph-read-v2` | 用一次 768-token 结构化 Mind 门控替换一次布尔门控；原问题、最多三条线索和两条带共享变量的关系交给实际 Memory facade。先覆盖入口、再共享预算探索，按真实角色与一致身份共同呈现完整 Fact。旧默认及 v1 复现入口保留 | `python -m pytest tests/test_query_mind_gate.py tests/test_query_mind_chat.py Conversation_Memory/tests/test_query_first_hit.py Conversation_Memory/tests/test_query_graph_read.py -q`；[自然输入、A/B/C 与限制](../Conversation_Memory/docs/FIRST_HIT_MEMORY.md#explicit-query-driven-graph-read-v2)。合成回归是机制证据，真实自然输入效果必须另看冻结结果，未晋升 |
| 保幅且可弃权的读侧候选 / `calibrated-first-hit-v1`：显式 `LUMINA_MEMORY_PROFILE=calibrated-first-hit-v1`，保留缺省 `LUMINA_MIND_GATE_MODE=llm` | 同一 v6 图与 writer；多语言只读派生索引保留原始 cosine，种子总幅度等于最大入口支持，直接/关联统一阈值且允许空返回。独立合成验证未能兼顾有用记忆保留，当前仅用于复现与后续比较，**不晋升** | `Conversation_Memory/.venv/bin/python -m pytest Conversation_Memory/tests/test_calibrated_first_hit.py tests/test_calibrated_memory_chat.py -q`；[具体配置、参数出处与限制](../Conversation_Memory/docs/RELIABLE_MEMORY.md#calibrated-first-hit-v1-explicit-reader)。本地冻结评估命令见最终报告；不提交旧图或实验结果 |
| 多语言候选＋一次语义用途选择 / `semantic-associative-v1`：显式 `LUMINA_MEMORY_PROFILE=semantic-associative-v1` | 复用同一 v6 图、跨语言索引、保幅 FirstHit 和 Memory owner；旁路失败的最终数值阈值与 0.6 输出占位，最多 20 条完整 Fact 供 Mind 选 `history` / `analogy` / 空集，合法最多 3 条进入 Answer。选择失败不恢复整批面板。与 calibrated 数值规则是同一读侧问题的独立比较路线；当前冻结语义评估未证明整体收益或图独有收益，**不晋升** | [候选契约、入口、预算](../Conversation_Memory/docs/RELIABLE_MEMORY.md#semantic-associative-v1-explicit-reader)；`Conversation_Memory/.venv/bin/python -m pytest Conversation_Memory/tests/test_calibrated_first_hit.py tests/test_semantic_associative_chat.py -q`；真实图实验材料只在本地，不入库 |
| 多语言覆盖面板＋关系用途选择 / `semantic-associative-v2`：显式 `LUMINA_MEMORY_PROFILE=semantic-associative-v2` | v1 的同机制后继，保留 v1 复现入口；同一 v6 writer、跨语言索引、保幅 FirstHit 与 5/64/256 预算。最多 32 条完整卡片进一次 Mind，最多 12 条合法排序建议可被解析，代码最终装包最多 3 条完整 Fact，并给 Answer 固定的 history/analogy 作用域指引。旧32题定位面板与 5/9 ID 协议问题已修复；新6故事24题中 5/6 类比错标 history，图独有真实回答收益只见一簇，**不晋升** | [候选契约与入口](../Conversation_Memory/docs/RELIABLE_MEMORY.md#semantic-associative-v2-explicit-reader)；`Conversation_Memory/.venv/bin/python -m pytest Conversation_Memory/tests/test_calibrated_first_hit.py tests/test_semantic_associative_v2_chat.py -q`；冻结图、gold、回执和实际回答仅在本地，不入库 |
| 图导航与有源转述正文 / `body-recall-v1`：显式 `LUMINA_MEMORY_PROFILE=body-recall-v1` | v7 直接由原始对话形成分组核验单元；同一 backend 持久化不可变正文，原 FirstHit 激活后先归组、后按真实单元及字节预算呈现；不启用关系硬过滤。与 v1/v2 是独立机制路线，不替代它们 | [入口、四臂与边界](../Conversation_Memory/docs/BODY_MEMORY.md)；`python -m pytest Conversation_Memory/tests/test_body_memory.py Conversation_Memory/tests/test_body_memory_persistence.py tests/test_body_memory_chat.py -q`；实现和机制测试不等于语义收益，未晋升 |
| source-window-v1 原文索引：`ingest_sources` / `recall_sources` | 原始对话独立 MAGMA namespace，不靠 Formation 事实覆盖 | `python -m pytest Conversation_Memory/tests/test_source_memory.py Conversation_Memory/tests/test_source_lexical.py -q` |
| 完整来源上下文：`recall_source_context` | 用事实/实体定位来源，返回完整角色/时间保真的原文上下文 | `python -m pytest Conversation_Memory/tests/test_source_context.py Conversation_Memory/tests/test_source_context_rendering.py -q` |
| 有界范围读取：`open_source_reader(...).search/read` + `core.evidence_acquisition.acquire_sources` | 模型显式申请原始 turn/字符范围；acquire_sources 管模型调用和 request 字符限额，reader 管 search/read/node-read 次数与字符限额；另记录 request 字节数，尚未接 Chat | `python -m pytest Conversation_Memory/tests/test_source_reader.py tests/test_evidence_acquisition.py -q` |
| experience 视图：`recall_experiences` | 同一 source selection 的原始对话与范围扩展引用；没有新模型生成或派生索引 | `python -m pytest Conversation_Memory/tests/test_source_experiences.py -q` |

实际模块分别在 [first_hit.py](../Conversation_Memory/adapter/first_hit.py)、
[source_memory.py](../Conversation_Memory/adapter/source_memory.py)、
[source_context.py](../Conversation_Memory/adapter/source_context.py)、
[source_reader.py](../Conversation_Memory/adapter/source_reader.py)、
[evidence_acquisition.py](../core/evidence_acquisition.py)、
[source_experiences.py](../Conversation_Memory/adapter/source_experiences.py)。
原文四路线的构造参数/返回 DTO 见 [原文契约](../Conversation_Memory/docs/SOURCE_REPRESENTATION_PROTOTYPE.md)。
这些路线部分仍复用旧 BGE：保留其调用方式和已有证据，但本次不下载或重跑其神经模型实验。

FirstHit 写入计划若需独立旧 v2 入口，使用 `python -m Dream.runner
--ingestion-version grounded-formation-v2 --first-hit --max-segments 1`；仅 `--first-hit`
不会把新版默认退回 v2。使用已准备本地 embedding 的 provider-free 实链检查为
`python -m scripts.first_hit_memory_check --help` 所示入口；它仍需本地 embedding，
不是完全无依赖的 mock。详细规则见 [FirstHit 契约](../Conversation_Memory/docs/FIRST_HIT_MEMORY.md)。

比较读取机制时固定同一已写入图、来源文件、query、embedding 版本、FirstHit seeds/nodes/edges
和输出字符/条数预算，写入策略保持不变。写入 v6 与旧写入比较则从同一合成 Cold 字节开始，
分别使用空状态、同一真实模型和 F1/F2/G1/G2 总预算，并固定读取路线。
评价来源完整性、事实支持、混淆/未知、成本、确定性、重启与重试收敛。
只有返回同一消费环节的路线才进行配对；`SourceReader` 的主动多轮获取与纯视图格式
不能仅凭相同 top-k 排出胜者。目前证据是实现与机制验证，未补做能力效果实验。

### C1：器官工作上下文

同一替换位置：给 Mind/Execution 的历史轮次投影。保留 **baseline / mask / summary**：
基线不压缩，mask 隐去旧观察正文但留结构与来源，summary 新压缩使用
`working-context-v4`。旧 v1/v2/v3 只承担冻结尝试恢复，非并列候选。
实现：[working_context.py](../working_context.py)、各器官 `prepare_background` / `execution_context`。

真实入口（此命令会调用模型，本次未执行）：

```bash
mkdir -p /tmp/context-baseline-work
# 三臂 workspace 预先放入相同的合成任务输入。
python -m Mind start --state /tmp/context-baseline-state \
  --workspace /tmp/context-baseline-work --goal '同一明确任务' \
  --context-mode baseline --repetition-mode off \
  --max-calls 40 --max-output-tokens 200000 --max-request-bytes 2800000
```

另两臂分别用 mask/summary、各自新 state 和内容相同的 workspace。
模式在启动时冻结，resume 不能换。无模型冒烟：

```bash
python -m pytest Execution/test_working_context.py Execution/test_working_history.py \
  Mind/test_working_background.py -q
```

机制含多次压缩、原始来源读取、冻结请求、失败、重启；历史固定前缀小样本未证明 summary
普遍优势，baseline 仍默认。旧结果及 48/64 次调用归属见
[认知历史](../Mind/docs/EXPERIMENT_HISTORY.md)。比较固定输入字节、同一安全前缀、
workspace、模型及上例总预算（摘要也计费）；评价任务结果、早期条件保持、错误确定化、
重复动作、总成本与完成状态。不向某臂提前提供其他臂的未来观察。

### C2：重复执行事实的投递

保留 **off / execution / mind**，当前 `execution-repetition-2`。
同一入口加 `--context-mode baseline --repetition-mode off|execution|mind`；默认 off。
Execution 观察同 Run/Task 下连续三个相同成功完整 IPython 单元/结果；execution 臂提供给
Execution，mind 臂再通过已有事件通知同一个 Mind。这不是通用“没有进展”判断器。
实现：[Execution/runtime.py](../Execution/runtime.py)、[Nervous/triggers.py](../Nervous/triggers.py)。

```bash
python -m pytest Execution/test_repetition.py Execution/test_repetition_lifecycle.py \
  Mind/test_repetition_loop.py -q
```

三臂固定任务、实际原文、相同安全前缀、上下文模式、模型与总预算，各自独立状态；
检查实际 request 是否带相同重复事实，再比较后续重复、产物验收、完成状态、指导到达和成本。
现存 24-call A/B/C 是 **v1、INCONCLUSIVE**；v2 修复只有确定性验证，不得继承 v1 的效果数字。
见 [结果报告](../Mind/docs/REPETITION_REASSESSMENT_RESULT.md)。

### C3：持续授权下的串行 Task

`--pursuit '原始授权范围'` 替换同次启动的 `--goal`，显式允许选取串行 Task；
它改变运行授权契约，不和 C1/C2 排同一个实验名次。
最新新活动使用 `mind-cognitive-interface-v2` / `pursuit-commit-v2`，共用
[Mind/intention.py](../Mind/intention.py)、Cognition journal 和各器官现役状态。

```bash
python -m pytest Mind/test_pursuit_integration.py Mind/test_stage1_loop.py Execution/test_stage1.py -q
```

未来运行沿用 C1 的三项总预算、分离 state/workspace，先约定相同授权范围和验收。
[Stage1 契约](../Mind/docs/INTENTION_STAGE1.md)给出完整入口。
[原始结果](../Mind/docs/INTENTION_STAGE1_RESULT.md)从 32-call PARTIAL 到后续 96-call
受控闭合，明确有开发者反馈和修复参与；不能解释成通用自治或方案优势。

## 只有设计、尚不可比较的条目

[ReAct 两图](../Lumina_Canvas/Lumina_ReAct.canvas)、
[带 Draft 的图](<../Lumina_Canvas/Lumina_ReAct 1.canvas>)、
[TDP/Supervisor/DAG](../Lumina_Canvas/task-decoupled-planning-agent-flow.canvas)、
[Execution Organ 概念图](../Lumina_Canvas/Lumina_Execution_Organ.canvas)保留其独立设计信息。
尚缺完整实现/可调用接口、相同任务边界和机制验证，不能给出可运行比较命令。
两幅 ReAct 同时引入且关注不同层次，未证实谁完整承接谁；先标状态，不凭名字删掉。
长期 Blackboard、能力发现、Self-Cognition/Evolution 同样是方向，不是本次候选代码。

## 移动、退役与历史出处

历史文件可用 `git show <commit>:<path>` 读取；退休前版本用 `<retirement>^:<path>`。
不要为重放旧结果把整套 archive/v1/v2 再复制进仓库。历史 CLI 必须在独立副本和合成状态中运行。

| 原位置 / 版本 | 当前去向与理由 | 历史出处 |
| --- | --- | --- |
| `scripts/mind_relation_shadow.py` | [experiments/mind_relation.py](../experiments/mind_relation.py)，单一独立候选入口，录制 client 复用原 shadow harness | `f92ea8e:scripts/mind_relation_shadow.py`；原命令 `python -m scripts.mind_relation_shadow --labels <labels> --gate v3rel --log <log>` |
| `tests/test_grounded_formation_mvp.py` | [Memory 的 formation 测试](../Conversation_Memory/tests/test_grounded_formation.py)，移除过时 MVP 名称，所有有效断言保留 | `f92ea8e:tests/test_grounded_formation_mvp.py` |
| `tests/test_identity_coverage.py` / `test_user_self_binding.py` | 同名迁入 [Memory/tests](../Conversation_Memory/tests)，按器官归属 | `f92ea8e:tests/<原名>` |
| `docs/FORMATION_VALIDATOR_AUDIT_CASES.json` / `EXACT_9_VALIDATOR_REJECTION_AUDIT_CASES.json` | [formation_validator_audit_cases.json](../Conversation_Memory/fixtures/formation_validator_audit_cases.json) / [exact_9_validator_rejection_audit_cases.json](../Conversation_Memory/fixtures/exact_9_validator_rejection_audit_cases.json)，字节不变 | `f92ea8e:docs/<原名>` |
| Memory / Dream `tests/conftest.py` | 仅 sys.path 注入、无 fixture；由统一 `Conversation_Memory.*` 导入替代。旧 namespace 恢复场景在对应兼容测试中局部搭建；显式跨旧 checkout 的验收工具保留必要路径适配 | `f92ea8e:Conversation_Memory/tests/conftest.py`、`f92ea8e:Dream/tests/conftest.py` |
| `Conversation_Memory/tests/entity_memory_chain_probe.py` | 旧两事实 B2 诊断，已由 `test_association_selection.py` 的完整打包/评分/桥接断言及 `test_memory_read_foundation.py` 承接；无生产/动态调用或状态读取职责 | `f92ea8e:Conversation_Memory/tests/entity_memory_chain_probe.py`，引入 `2a00908`；历史命令见下 |
| `Conversation_Memory/tests/memory_reliability_retry_probe.py` | 旧损坏 JSON / source-ref 两场景已由 `test_memory_retryability.py`、`test_entity_ingestion_v2.py` 更完整覆盖；无 pytest 测试被删除 | `f92ea8e:Conversation_Memory/tests/memory_reliability_retry_probe.py`，引入 `07226c0`，恢复规则后续 `d6b6733`；历史命令见下 |
| `docs/LUMINA_EXECUTION_MVP_ARCHITECTURE.md` | [最终架构](LUMINA_EXECUTION_FINAL_ARCHITECTURE.md) §20–23 + [恢复契约](RECOVERY_AND_WORKING_CONTEXT_DESIGN.md)承接现行职责；旧“排除 Child/Mind”的阶段限制退役 | `9d63da7311baa7611782cc8079fb09e9d81f4e25:docs/LUMINA_EXECUTION_MVP_ARCHITECTURE.md`，至 f92ea8e 未改 |
| 旧 `Execution_lab2/` 包 | 已在历史提交迁入 `Execution/`；本次不复活旧包/活动数据 | 退休 `2548bb00b279a1aa3ddd805d2cb738c9caa2390b` |
| 旧 Mind chain / host / event_loop / campaigns | 已由 activity/cognition/model/cli、Nervous transport、Execution runtime 承接 | 退休 `1f893d72790fff345d39e0582d412fdef4ff0714`；结论见认知历史 |
| Memory adaptive/depth/weight 与 cosine-gate 实验 | 先前已退休，失败结论仍绑定旧实现；不同于仍保留的 source / FirstHit 路线 | `f6743a7^` / `7a261ff^`；具体机制和既有数据结论见 [Memory 历史](MEMORY_EXPERIMENT_HISTORY.md) |

两个退役诊断脚本原调用（只在对应历史源码副本中使用，依赖当时已捕获输入/缓存）：

```bash
python Conversation_Memory/tests/entity_memory_chain_probe.py \
  --source-root /path/to/historical-source --input /path/to/captured-A.json --output /tmp/chain-report.json
python Conversation_Memory/tests/memory_reliability_retry_probe.py \
  --source-root /path/to/historical-source --output /tmp/retry-report.json
```

`Conversation_Memory/tests/entity_memory_acceptance.py` 继续保留：它有独立的旧 v2/BGE
能力评分与精确 replay 职责，且被维护测试动态加载。`--replay-only` 需要当时资料，不代表
v6 acceptance；本次未运行它。许可证和原始算法来源也保留。
[旧任务卡](plan)仍是阶段设计/决策出处；相关现役入口指向本页与代码地图，不能将旧任务卡当成未完成能力清单。

其余版本标记没有仅因未发现 import 而删除：已检查 CLI、显式配置、动态/跨源码探针、
有效测试与旧状态读取。没有完整承接证据的设计先留；有冻结状态恢复职责的代码继续维护。

## 本次实际验证

执行环境：Linux，Python 3.12，已准备的 Memory venv；`HF_HUB_OFFLINE=1`、
`TRANSFORMERS_OFFLINE=1`，临时合成状态。没有真实生成模型请求；用户纠正后停止 BGE 下载，
后续不为旧结果补缓存。真实模型语义实验未执行。

| 验证 | 实际结果与含义 |
| --- | --- |
| 根 `python -m pytest -q -ra`（现在含 Memory/Dream） | **2140 passed / 29 failed / 10 skipped**。29 项均来自旧 BGE 无缓存导致的 Recall 不可用：19 项旧 E2E 连带失败、10 项旧真实 BGE Recall。不能宣称全绿 |
| v6/reliable-v2、来源候选、写入恢复、生产组装 | 在上述全收集中通过，使用固定响应/临时状态；只证明实现及机制 |
| 上下文、重复观察、pursuit 相关集中回归 | **125 passed，0 skipped**；包括重启、来源保持和未重发语义 |
| v3rel / v2 shadow、候选 CLI | **41 passed**；四个原案例与共享 fixture 一致，离线 replay 成功 |
| 历史跨 checkout 验收工具 | 从实际 CLI 导入代码加载 f92ea8e archive 的新进程检查通过；现有 acceptance 回归 **6 passed**，普通生产不受此路径适配影响 |
| Dream + FirstHit 平台/namespace 兼容 | **61 passed，0 skipped**；包含使用已有 MiniLM 缓存的真实 MAGMA 手动 Dream |
| 代码及结构核验 | 生产函数除 import/路径处理外 AST 等价；迁移的有效断言和 fixture 原字节保留；MAGMA 固定提交、status/diff 为空；`git diff --check` 通过 |

10 个跳过均为原有条件：Node 不可用 1、显式 Docker 验证 6、旧 BGE tokenizer 缓存 3。
没有新增 skip、删断言或减少收集；专用环境识别从只认 Windows executable 改为 venv prefix，
Linux 实际执行了原先被挡住的集成检查。新增 5 个候选入口/等预算收集用例（2 个测试函数）。逐 Node ID 对照原全器官收集：
2174 → 2179，无丢失；增长全部来自这些用例。
未修改源码的 f92ea8e 归档按原根收集规则为 1161 passed / 31 skipped；其专用环境判断和
收集范围不同，不能与新全树计数直接比较优劣。

上述结果记录自整理阶段的实际执行。原始日志和独立审查使用会话临时目录，
未纳入版本库；提交准备时该临时目录已不在当前环境，不能将其作为持久附件。
历史语义证据仍绑定原提交，维护的运行命令、断言与输入保留在仓库。

未解决项是验证边界：本机缺旧 BGE 缓存/Node，未启用 Docker，未在 Windows 验证；
新候选能力收益和设计图的实现/替代关系仍需各自证据。没有用这些限制来改默认策略或选实验胜者。
