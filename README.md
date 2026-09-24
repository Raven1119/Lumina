# Lumina 代码地图

这里回答「现在怎样运行、功能在哪里」。替代方案、独立运行方法和历史去向统一见
[方案目录](docs/EXPERIMENTS.md)。以下依据当前 `Execution_lab2` 源码整理；
实现事实补充在 [CURRENT_STATUS](docs/CURRENT_STATUS.md)，设计愿景在
[NORTH_STAR](docs/NORTH_STAR.md)。文档中的历史成绩不代表当前版本的新测量。

## 三个实际入口

| 入口 | 主流程 | 运行边界 |
| --- | --- | --- |
| `python -m uvicorn core.main:app --workers 1` | 浏览器 → FastAPI → MessageRuntime → Recall 门控 → Memory → Answer → Hot Draft → Cold-first 压缩 | 单进程；Chat 不运行认知链，也不自动 Dream |
| `python -m Mind start …` | Mind 判断 ↔ Nervous 持久事件 ↔ Execution 行动 ↔ 环境证据 | 独立前台链；默认单目标；各器官拥有自己的状态，没有中央 Host |
| `python -m Dream.runner --max-segments 1` | Cold 待消化段 → 有界写入 → checkpoint/MAGMA 持久化 → Cold consumed | 显式、同步、串行；独立 CLI 使用同一状态时须先停止服务写入，并继承已配置的进程环境 |

服务还提供 `POST /api/dream/run`（共用写锁）和独立的 `POST /api/execution`。
独立 Dream CLI 不自动加载 `.env.local`；真实模型模式/密钥须在调用环境中配置，
否则会走 mock/legacy。API 的真实路由和组装都在 [core/main.py](core/main.py)，请求 DTO 在
[core/contracts.py](core/contracts.py)。认知 CLI 参数及恢复用法见
[Mind/cli.py](Mind/cli.py)、[集成链契约](Mind/docs/INTEGRATED_CHAIN.md)。

## 当前默认的记忆链

配置真实模型时，Chat 和服务内 Dream 共用同一 Memory adapter：

```text
显式 Dream
Cold 原始段 → grounded-formation-v6（F1 提取 / F2 验证 / G1 绑定 / G2 验证）
  → 持久化阶段响应、已核验正文和绑定 → FirstHit 连边计划 → MAGMA → Cold consumed

Chat
原问题 → v2 布尔门控（失败放行）→ Memory.recall
  → FirstHit 有界种子/局部传播/事实竞争
  → reliable-v2：保留 canonical 正文 + 有界 Cold 原文补充
  → MemoryContext → Answer → Hot Draft
```

**这条默认路径不运行 BGE、旧 Hindsight 或旧分数阈值。** Chat 缺省输出最多
3 个事实、5000 字符；reliable-v2 原文补充有独立条数上限、共用字符预算。
FirstHit 自己的缺省发现预算是 5 seeds / 64 nodes / 256 edges；不要把
`RecallPolicy` 中为旧路径保留的 depth/node 字段说成它的遍历规则。
具体投递及预算见 [可靠记忆契约](Conversation_Memory/docs/RELIABLE_MEMORY.md)。

真实运行模型保持 DeepSeek-V4-Pro。未配置真实模型时使用 mock/legacy
`grounded-span-v2`；旧 BGE 路径仍供明确选择的历史接口和兼容调用，代码与结果位置见方案目录。
读取空或失败不会阻断普通聊天。Cold 原文不可变，消费状态只由 Cold owner 改动。

## 按功能找实现、状态和测试

| 功能 / 状态所有者 | 实现 | 验证与详细契约 |
| --- | --- | --- |
| API、前端、依赖组装 | [core/main.py](core/main.py)、[edge/static](edge/static) | [test_chat_api](tests/test_chat_api.py)、[test_execution_api](tests/test_execution_api.py) |
| 消息流程、记忆注入、回答 | [message_runtime.py](core/message_runtime.py)、[model_client.py](core/model_client.py)、[chat_background.md](prompts/chat_background.md) | [test_message_runtime](tests/test_message_runtime.py)、[test_model_client](tests/test_model_client.py) |
| Chat 的布尔门控 / 显式后读选择 | [Mind/llm_gate.py](Mind/llm_gate.py)、[evidence_selector.py](Mind/evidence_selector.py)、[decision_log.py](Mind/decision_log.py) | [读契约](Mind/docs/CHAT_RECALL_GATE.md)、[选择回归](tests/test_memory_evidence_selection.py)；与持久认知 Mind 是不同接口 |
| Hot、Cold、压缩游标、逐轮来源 | [draft_store.py](core/draft_store.py)、[cold_draft_store.py](core/cold_draft_store.py)、[hot_draft_compactor.py](core/hot_draft_compactor.py)、[turn_provenance.py](core/turn_provenance.py) | [Cold 契约](docs/COLD_DRAFT.md)、[来源契约](docs/DRAFT_TURN_PROVENANCE_V2.md)、[Cold 测试](tests/test_cold_draft_store.py) |
| 手动消化协调；不拥有记忆数据库 | [Dream/runner.py](Dream/runner.py)、[cold_draft_digest.py](Dream/cold_draft_digest.py) | [Dream 测试](Dream/tests)、[消化契约](Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md) |
| Memory 门面 / DTO / 幂等阶段 checkpoint | [magma_adapter.py](Conversation_Memory/adapter/magma_adapter.py)、[models.py](Conversation_Memory/adapter/models.py)、[state_store.py](Conversation_Memory/ingestion/state_store.py) | [幂等契约](Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md)、[Memory 测试](Conversation_Memory/tests) |
| v6 写入及实体绑定 | [reliable_formation.py](Conversation_Memory/adapter/reliable_formation.py)、[_entity_ingestion.py](Conversation_Memory/adapter/_entity_ingestion.py) | [v6 回归](Conversation_Memory/tests/test_reliable_v6_ingestion.py)、[完整组装回归](Conversation_Memory/tests/test_reliable_profile_integration.py) |
| FirstHit / reliable-v2 | [first_hit.py](Conversation_Memory/adapter/first_hit.py)、[_first_hit_ingestion.py](Conversation_Memory/adapter/_first_hit_ingestion.py)、[_reliable_recall.py](Conversation_Memory/adapter/_reliable_recall.py) | [FirstHit 测试](Conversation_Memory/tests/test_first_hit.py)、[可靠读取 v2](Conversation_Memory/tests/test_reliable_recall_v2.py) |
| 可弃权读侧候选 `calibrated-first-hit-v1` | [派生跨语言索引](Conversation_Memory/adapter/_calibrated_index.py)、[保幅激活与统一选择](Conversation_Memory/adapter/_calibrated_recall.py)、[模型及参数](Conversation_Memory/adapter/calibrated_first_hit_parameters.json)；由 [Memory facade](Conversation_Memory/adapter/magma_adapter.py) 与 [Chat 组装](core/main.py) 显式选择 | [数学及门面回归](Conversation_Memory/tests/test_calibrated_first_hit.py)、[Chat 回归](tests/test_calibrated_memory_chat.py)、[候选契约](Conversation_Memory/docs/RELIABLE_MEMORY.md#calibrated-first-hit-v1-explicit-reader)；独立验证未达到保留有用记忆要求，未晋升 |
| 语义联想候选 `semantic-associative-v1` | [完整 Fact 面板](Conversation_Memory/adapter/_semantic_recall.py) → [PreparedRecall 子集](Conversation_Memory/adapter/models.py) → [Mind 一次用途选择](Mind/evidence_selector.py) → [Chat](core/message_runtime.py)；复用多语言索引和 FirstHit | [候选契约](Conversation_Memory/docs/RELIABLE_MEMORY.md#semantic-associative-v1-explicit-reader)、[方案目录](docs/EXPERIMENTS.md)、[门面与 Chat 回归](tests/test_semantic_associative_chat.py)；独立候选，未晋升 |
| 语义联想候选 `semantic-associative-v2` | 同一 [Memory owner](Conversation_Memory/adapter/magma_adapter.py) 的 [覆盖式 Fact 面板](Conversation_Memory/adapter/_semantic_recall.py) → [排序建议与确定性装包](Conversation_Memory/adapter/models.py) → [Mind 一次关系用途选择](Mind/evidence_selector.py) → [显式 Chat](core/message_runtime.py) | [v2 契约](Conversation_Memory/docs/RELIABLE_MEMORY.md#semantic-associative-v2-explicit-reader)、[实际 Chat 回归](tests/test_semantic_associative_v2_chat.py)；保留 v1 与生产默认，冻结评估显示关系用途误判，未晋升 |
| 语义联想候选 `semantic-associative-v3` | [独立基础面板与图追加](Conversation_Memory/adapter/_semantic_recall_v3.py) → [Memory owner](Conversation_Memory/adapter/magma_adapter.py) → [Mind 事件身份选择](Mind/evidence_selector.py) → [Chat 有界缺失约束](core/message_runtime.py) | [v3 契约](Conversation_Memory/docs/RELIABLE_MEMORY.md#semantic-associative-v3-explicit-reader)、[面板回归](Conversation_Memory/tests/test_calibrated_first_hit.py)、[实际 Chat 回归](tests/test_semantic_associative_v3_chat.py)；显式候选，生产默认不变 |
| 语义联想候选 `semantic-associative-v4` | [请求内 Base 锁定与按需图面板](Conversation_Memory/adapter/_semantic_recall_v4.py) → [Mind 两阶段选择](Mind/evidence_selector.py) → [Chat 追加与缺失审计](core/message_runtime.py) | [v4 契约](Conversation_Memory/docs/RELIABLE_MEMORY.md#semantic-associative-v4-explicit-reader)、[Memory 回归](Conversation_Memory/tests/test_semantic_recall_v4.py)、[实际 Chat 回归](tests/test_semantic_associative_v4_chat.py)；仅显式候选，生产默认不变 |
| 语义联想候选 `semantic-associative-v5` | [完整 Base 锁定与 gap 引导的图补充](Conversation_Memory/adapter/_semantic_recall_v5.py) → [Mind 单源协议](Mind/evidence_selector.py) → [显式 Chat 与 grounding 审计](core/message_runtime.py) | [v5 契约](Conversation_Memory/docs/RELIABLE_MEMORY.md#semantic-associative-v5-explicit-reader)、[Memory 回归](Conversation_Memory/tests/test_semantic_recall_v5.py)、[实际 Chat 回归](tests/test_semantic_associative_v5_chat.py)；未晋升，生产默认不变 |
| 语义联想候选 `semantic-associative-v6` | [完整 Base 与双补充池](Conversation_Memory/adapter/_semantic_recall_v6.py) → [Mind 共用补充协议](Mind/evidence_selector.py) → [显式 Chat](core/message_runtime.py)；每次健康读取本地探索图，最多追加一条，不扩张文本预算 | [v6 契约](Conversation_Memory/docs/RELIABLE_MEMORY.md#semantic-associative-v6-explicit-reader)、[Memory 回归](Conversation_Memory/tests/test_semantic_recall_v6.py)、[实际 Chat 回归](tests/test_semantic_associative_v6_chat.py)；Answer grounding 仍有失败，未晋升 |
| 语义记忆使用候选 `semantic-associative-v7` | 逐字复用 [v6 候选池](Conversation_Memory/adapter/_semantic_recall_v6.py) → [Mind v7 用途选择](Mind/evidence_selector.py) → [Chat 单一 CURRENT/HISTORY/RESPONSE 协议](core/message_runtime.py)；只改变选择语义与 Answer 指引 | [v7 契约及限制](Conversation_Memory/docs/RELIABLE_MEMORY.md#semantic-associative-v7-explicit-reader)、[Memory 回归](Conversation_Memory/tests/test_semantic_recall_v7.py)、[实际 Chat 回归](tests/test_semantic_associative_v7_chat.py)；冻结评估观察到新的 grounding 退化，未晋升 |
| 显式图读取候选 `graph-read-v1` | [_graph_read.py](Conversation_Memory/adapter/_graph_read.py)、[_first_hit_read.py](Conversation_Memory/adapter/_first_hit_read.py)、[查询条件](Conversation_Memory/adapter/graph_read_query.py) | [门面回归](Conversation_Memory/tests/test_graph_read_facade.py)、[调用和降级契约](Conversation_Memory/docs/FIRST_HIT_MEMORY.md#explicit-graph-read-v1-candidate)；未切换生产默认 |
| 问题驱动候选 `graph-read-v2` | [一次结构化门控](Mind/llm_gate.py)、[入口与证据组合](Conversation_Memory/adapter/_query_graph_read.py)、[共享多前沿](Conversation_Memory/adapter/_first_hit_read.py)、[Chat 传递](core/message_runtime.py) | [自然输入与配置](Conversation_Memory/docs/FIRST_HIT_MEMORY.md#explicit-query-driven-graph-read-v2)、[组合回归](Conversation_Memory/tests/test_query_graph_read.py)、[实际 Chat 回归](tests/test_query_mind_chat.py)；显式 `LUMINA_MIND_GATE_MODE=graph-read-v2`，默认不变 |
| 图导航与重写正文候选 `body-recall-v1` | [_body_formation.py](Conversation_Memory/adapter/_body_formation.py)、[backend 正文载荷](Conversation_Memory/adapter/body_payload.py)、[正文聚合与预算](Conversation_Memory/adapter/_body_recall.py) | [显式写入、恢复、Chat 与四臂入口](Conversation_Memory/docs/BODY_MEMORY.md)；`LUMINA_MEMORY_PROFILE=body-recall-v1`，v7/F1-F2 正文与原 FirstHit，未晋升 |
| 图、向量和原文索引 | [backend.py](Conversation_Memory/adapter/backend.py)、[_source_backend.py](Conversation_Memory/adapter/_source_backend.py)、[固定上游 MAGMA](Conversation_Memory/upstream/MAGMA) | MAGMA gitlink `467cb70b67ac337b22fdb42194d37c04ad701b62`；一个 MultiDiGraph，不是四个物理图 |
| 原文候选接口 | [source_memory.py](Conversation_Memory/adapter/source_memory.py)、[source_context.py](Conversation_Memory/adapter/source_context.py)、[source_reader.py](Conversation_Memory/adapter/source_reader.py)、[source_experiences.py](Conversation_Memory/adapter/source_experiences.py) | [原文契约](Conversation_Memory/docs/SOURCE_REPRESENTATION_PROTOTYPE.md)；独立候选如何跑见方案目录 |
| Mind 判断、认知提交、活动和分析 | [organ.py](Mind/organ.py)、[cognition.py](Mind/cognition.py)、[trace.py](Mind/trace.py)、[activity.py](Mind/activity.py)、[analysis.py](Mind/analysis.py)、[world_model.py](Mind/world_model.py) | [认知架构](docs/MIND_COGNITIVE_ARCHITECTURE.md)、[Mind 测试](Mind)；Mind 不拥有业务文件系统操作权限 |
| Nervous 邮箱、确认、前台续接和预算 | [organ.py](Nervous/organ.py)、[storage.py](Nervous/storage.py)、[provider.py](Nervous/provider.py)、[attention.py](Nervous/attention.py)、[watch.py](Nervous/watch.py) | [事件契约](docs/NERVOUS_EVENT_FOUNDATION.md)、[Nervous 测试](Nervous)；传递事实，不替 Mind 决策 |
| Execution 行动、来源、恢复 | [runtime.py](Execution/runtime.py)（事件入口）、[organ.py](Execution/organ.py)（现役单次 facade）、[execution.py](Execution/execution.py)、[evidence.py](Execution/evidence.py)、[sandbox.py](Execution/sandbox.py) | [执行架构](docs/LUMINA_EXECUTION_FINAL_ARCHITECTURE.md)、[Execution 测试](Execution) |
| 器官自己的工作上下文投影 | [working_context.py](working_context.py) | [恢复契约](docs/RECOVERY_AND_WORKING_CONTEXT_DESIGN.md)、[来源许可](vendor/kimi_compaction/PROVENANCE.md)；不拥有原始历史或权威状态 |

Memory 内外统一使用 `Conversation_Memory.*`。只有上游 MAGMA loader、显式历史兼容测试及跨旧 checkout 的验收工具
保留必要的路径适配；器官不再依赖测试收集顺序建立隐式 import。
现有 Canvas 的状态说明和链接随本次整理修正，示意图不能替代以上实际调用关系。

## 配置与本地运行

开发和 Codex 运行在 Linux；Noespire 应用目标是 Windows，详见
[平台约定](docs/final_goal.md#target-platform)。本次 Linux 验证不等于 Windows 验证。

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn core.main:app --workers 1
```

打开 `http://127.0.0.1:8000/`。Memory 的额外依赖见
[Conversation_Memory/requirements.txt](Conversation_Memory/requirements.txt)；有已准备环境时直接复用。
上游新检出用 `git submodule update --init Conversation_Memory/upstream/MAGMA`，保持固定提交。
模型缓存按实际选择的路线准备；不要为 v6/reliable-v2 下载旧 BGE。

| 配置入口 | 消费者 / 作用 |
| --- | --- |
| [.env.example](.env.example) → 私有 `.env.local`；进程环境优先 | [env_loader.py](core/env_loader.py)；`LUMINA_MODEL_MODE`、`DEEPSEEK_API_KEY` 由模型适配器读取 |
| `LUMINA_MIND_GATE_MODE=llm` | Chat 缺省门控；`constant` 是支持的常量模式；`direct`、`select` 是显式候选 |
| `LUMINA_MEMORY_PROFILE=semantic-associative-v1` | 显式语义联想候选；替换布尔前门控，读取完整 Fact 面板后一次选择，再交付最多 3 条；与其他门控模式冲突即拒绝启动，默认仍为 `production` |
| `LUMINA_MEMORY_PROFILE=semantic-associative-v2` | 显式 v2 读侧候选；最多 32 条完整 Fact 卡片进入一次 Mind 选择，确定性装包最多 3 条给 Answer；与其他门控模式冲突即拒绝启动，默认仍为 `production` |
| `LUMINA_MEMORY_PROFILE=semantic-associative-v3` | 显式 v3 读侧候选；最多 32 条图无关基础卡片后追加最多 8 条图独有卡片，Mind 一次选择，Answer 最多 3 条；默认仍为 `production` |
| `LUMINA_MEMORY_PROFILE=semantic-associative-v4` | 显式 v4 读侧候选；Mind 先锁定基础选择，仅在 `seek_graph=true` 时读取图并尝试追加一个图 Fact；最终最多 3 条，默认仍为 `production` |
| `LUMINA_MEMORY_PROFILE=semantic-associative-v5` | 显式 v5 读侧候选；完整锁定最多 3 条 Base Fact，有真实空位且 `seek_graph=true` 才按 `graph_need` 对已访问图候选重排并尝试追加；默认仍为 `production` |
| `LUMINA_MEMORY_PROFILE=semantic-associative-v6` | 显式 v6 读侧候选；Base 先锁定最多 3 条，健康读取都本地探索一次 FirstHit，图独有 Fact 经第二次 Mind 选择后最多追加 1 条，总计最多 4 条且仍限 5000 字符/20000 字节；默认仍为 `production` |
| `LUMINA_MEMORY_PROFILE=semantic-associative-v7` | 显式 v7 读侧候选；与 v6 同一读取、FirstHit、候选和容量，Mind 调整有用历史判断，Answer 使用单一 CURRENT/HISTORY/RESPONSE 协议；默认仍为 `production` |
| `LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED` | 缺省开启 Recall，可显式关闭 |
| `LUMINA_DRAFT_STORE_PATH` / `LUMINA_MIND_DECISION_LOG_PATH` / `LUMINA_DEFAULT_TIMEZONE` | Hot 路径、门控审计、时区后备；服务 Cold 和压缩游标默认跟随 Hot 父目录 |
| `LUMINA_DREAM_MAGMA_PERSIST_DIR` | 服务与独立 Dream 的 MAGMA 目录 |
| `LUMINA_DREAM_COLD_DRAFT_PATH` / `LUMINA_DREAM_INGESTION_STATE_PATH` | 独立 Dream CLI 的 Cold / checkpoint 路径；不覆盖服务 Cold owner |
| `python -m Mind --help` | 认知链的 state、workspace、模型调用/输出/request-byte 预算及固定启动模式；不共用 Chat 状态 |

## 验证

```bash
python -m pytest -q
python -m pytest Conversation_Memory/tests Dream/tests -q
python -m pytest Mind Nervous Execution -q
git diff --check
git -C Conversation_Memory/upstream/MAGMA status --short
```

根 `pytest` 现在包含所有维护的器官套件。测试使用合成数据和临时目录；部分旧真实
MAGMA/BGE 测试依赖专用环境和已有缓存，Docker 检查仍需原有显式开关。
不通过新增 skip 或减少收集范围隐藏环境限制。本次实际结果、候选冒烟及历史映射见
[方案目录](docs/EXPERIMENTS.md)。
