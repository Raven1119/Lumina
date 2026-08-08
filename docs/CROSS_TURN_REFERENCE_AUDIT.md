# Cross-Turn Reference Audit

## Result

`PARTIALLY_SUPPORTED`

当前真实链路能够借助局部图邻接，把指代句及其相邻前件一起召回；但它没有显式指代消解、指代绑定或上下文化 Event 表示，结果也不保证按对话顺序排列。因此，共同召回不能解释为系统已经识别出代词具体指向谁或什么。

## Current Ingestion Model

### turn → Event

`ColdDraftSegmentConverter.convert()` 保留 Cold segment 中的 turn 顺序，并生成包含 `segment_id`、`conversation_id` 和有序 turns 的 DTO（`Dream/cold_draft_digest.py:57-143`）。`MagmaMemoryAdapter.ingest()` 随后逐个遍历 `segment.turns`；每个 turn 调用一次 `backend.add_event()`，所以当前生产链路是一个 Cold turn 对应一个 MAGMA Event（`Conversation_Memory/adapter/magma_adapter.py:89-143`）。

user 与 assistant turn 走同一个循环、同一个 `add_event()` 路径。角色只作为 metadata 保存，不改变 Event 构造或 embedding 算法。

### Event text and embedding input

当前 `RealMagmaBackend` 使用 `llm_backend=None`。上游 fallback extraction 将当前 turn 截断到 500 字符作为 `content_narrative`（`Conversation_Memory/upstream/MAGMA/memory/trg_memory.py:368-384`）。本探针的 13 个短 turn 均逐字保真。

embedding 并非对原始字节串直接编码，而是对当前 Event 的 `content_narrative` 做关键词增强后再编码（`trg_memory.py:152-209`；`keyword_enrichment.py:118-153`）。增强输入仍只来自当前 turn 的简单抽取结果；没有拼接前一轮、后一轮、旧 rolling summary、完整 segment 或 Cold Draft 的其他内容。探针确认向量维度为 384。

### Metadata and provenance

| 信息 | 当前保存位置 | 是否直接参与 anchor 检索 |
|---|---|---|
| `segment_id`、`conversation_id`、`turn_id` | Event attributes 中的嵌套 `provenance` | 否；用于公开 evidence provenance 和幂等追踪 |
| `role` | Event attributes | 否 |
| Event timestamp | Event 的 `timestamp` 字段 | 可用于 temporal window、时间边和输出 |
| source timestamp/timezone/version | 嵌套 `provenance` | 否 |
| `evidence_id` | Event attributes | 否；用于稳定投影和去重 |
| entities | Event attributes | 可用于写入后的实体建边；不是 segment 上下文 |
| `segment_turn_index`、`segment_turn_count` | 未进入 MAGMA Event | 否 |
| Cold record 的 `source` | 未进入当前 DTO/Event | 否 |

metadata 的构造位于 `Conversation_Memory/adapter/magma_adapter.py:106-139`。字段存在不等于参与向量排序：当前向量内容不会加入 provenance、role、segment ID 或 turn 顺序。探针确认所有 Event 有 segment/conversation provenance，但没有 `segment_turn_index` 和 `segment_turn_count`。

## Graph Relationships

### Same-segment links

每次 `TRG.add_event()` 都在写入期立即调用 temporal 和 semantic link 创建（`trg_memory.py:152-219`）：

- temporal：按全图 timestamp 排序，新 Event 与其前一个全局时间节点建立双向 `TEMPORAL` 边，正向 subtype 为 `PRECEDES`，反向为 `SUCCEEDS`（`trg_memory.py:387-419`）；
- semantic：新 Event 与向量索引 top 3 的既有事件建立双向 `SEMANTIC/RELATED_TO` 边（`trg_memory.py:421-460`）；
- entity：完成一个 segment 的逐 turn 写入后，Lumina 对这批 memory IDs 请求已有实体建边（`magma_adapter.py:143`；`backend.py:86-104`）。

在 timestamps 递增的正常 segment 中，相邻 user/assistant turns 因全局 temporal 规则可在 1 hop 内双向到达。但这不是基于 `segment_id` 的邻接保证：代码不检查 segment、role 或最大时间间隔，也不创建显式 conversation/session/episode 边。若事件乱序插入，建立的是新节点的前一个时间节点，不能据此声称任意物理相邻 turns 都有专用关系。

### Cross-segment behavior

temporal 建边看的是同一 backend 的全图，而不是当前 segment。因此下一 segment 的首个事件也会连接到全局时间上的前一个事件。探针 E 的两个 segment 正是通过该全局邻接在 depth 1 共同返回。这个成功说明跨 segment 图可达，不说明系统理解了跨 segment 代词。

## Recall Behavior

### Anchors and traversal

Lumina 先执行 MAGMA dense query，再以有界 lexical ranking 和 RRF 融合 anchors；`top_k=1` 时最多保留一个 anchor（`Conversation_Memory/adapter/_recall_execution.py:48-109`）。

固定模式使用当前 Graph DB 的有界遍历，允许 temporal、semantic 和 causal links，受 `max_graph_depth`、`max_nodes` 与 temporal window 限制（`_recall_execution.py:48-60,147-153`）。因此：

- 指代句为 anchor 时，只要前件是允许边上的相邻 Event，depth 1 可能带回前件；
- 前件为 anchor 时，同样可能在 depth 1 带回后续指代句；
- depth 0 只返回 anchors，不会投影 graph expansions。

Adaptive GENERAL 使用 beam width 10、drop threshold 0.15，关系权重为 ENTITY 0.60、SEMANTIC 0.30、TEMPORAL 0.05、CAUSAL 0.05；transition 为 `0.6 × relation + 0.4 × query/event semantic similarity`（`Conversation_Memory/adapter/_adaptive_traversal.py:34-61,131-256`）。由于 temporal 权重很低，且父节点与邻居对查询的语义相似度下降超过 0.15 时会被剪掉，相邻 turn 并不保证进入 Adaptive 输出。本探针的地点场景 C 就在 depth 1 和 2 均被剪掉。

### Ordering and budgets

backend 总是先投影 anchors，再投影扩展节点（`Conversation_Memory/adapter/backend.py:106-138,290-294`）。adapter 又按内部候选 score、timestamp、evidence ID 排序；GENERAL rendering 保留这个检索顺序，只有 WHEN intent 会对已选 evidence 按时间排序（`magma_adapter.py:154-193`；`Conversation_Memory/recall/rendering.py:26-79`）。

因此 anchor 若是后出现的指代句，输出可能是指代句在前、前件在后；Adaptive 的扩展排序也可能把同 hop 的后续句排到中间句之前。`max_evidence_items` 和 `max_chars` 还可能裁掉某一方。当前 Context Linearization 不提供一般性的对话顺序恢复保证。

## Upstream MAGMA Mapping

| 上游机制 | 输入与算法 | 通用性 | 当前 Lumina 是否接入 |
|---|---|---|---|
| `TemporalResonanceGraphMemory._create_temporal_links` | 全图 timestamp 的前一节点；双向 PRECEDES/SUCCEEDS | 通用但不是 segment-aware | 是，Event 写入时自动执行 |
| `TemporalResonanceGraphMemory._create_semantic_links` | Event embedding 的 top-3 相似事件；双向 RELATED_TO | 通用 | 是，Event 写入时自动执行 |
| `MemoryBuilder.create_context_links(window_size=3)` | 对传入的有序 node IDs 建立 `CONTEXT_NEIGHBOR`，权重随距离下降（`memory_builder.py:570-620`） | 函数本身是可复用的局部窗口原语 | 否；Lumina 不实例化 `MemoryBuilder` |
| `MemoryBuilder.extract_event(prev_turn,next_turn)` | 将前后 turn 文本放入 LLM extraction prompt（`memory_builder.py:135-207`） | 依赖 LLM controller；在当前 build path 中依赖 LoCoMo turn/session 对象 | 否 |
| session nodes / `BELONGS_TO_SESSION` | 读取 `sample.session_summary` 并为事件连接 Session（`memory_builder.py:360-405,883-905`） | LoCoMo/session-summary 假设，非通用 drop-in | 否 |
| episode segmentation | LLM 判断边界并生成 title/summary（`episode_segmenter.py:186-327`） | 需要 LLM；由 `MemoryBuilder(use_episodes=True)` 驱动 | 否 |
| `QueryEngine` QA/session expansion | `ANSWERED_BY`、`RESPONSE_TO`、`BELONGS_TO_SESSION`、`CONTEXT_NEIGHBOR` 等启发式（`query_engine.py:586-672,1197-1260`） | 混合通用邻接与 benchmark/session 约定 | 否；Lumina 直接使用 TRG query 与自有有界遍历 |
| coreference-aware retrieval | 代词绑定、实体回指或 dialogue-window coreference | 上游未发现通用实现 | 不存在 |

固定上游提供了可复用的局部 context-link 原语，但现有 episode/session/contextual-event 完整路径带有 LLM、LoCoMo 或 session-summary 假设，不能描述为 Lumina 已可直接使用的通用指代方案。

## Probe Setup

新增的 `scripts/cross_turn_reference_probe.py` 使用 6 个合成场景、7 个 Cold segments、13 个 turns，不含真实用户数据。每个场景使用独立临时 Graph 和 Vector Index；只复用同一个已加载、无图状态的 MiniLM encoder，以避免重复模型加载。脚本结束后以 marker ownership 校验并删除临时目录。

真实路径为：

```text
Cold Draft 原始记录
→ ColdDraftSegmentConverter DTO
→ MagmaMemoryAdapter.ingest
→ RealMagmaBackend / 固定上游 MAGMA
→ MagmaMemoryAdapter 作为 MemoryRetriever.recall 实现
→ MemoryContext.evidence
```

脚本只包装现有 `_execute_fixed_recall` 以读取真实 `QueryContext.anchor_nodes` 供诊断；所有最终 evidence 均来自公开 adapter Recall 输出，没有伪造 evidence，也没有调用外部回答模型。

共同参数：`top_k=1`、depth 为 0/1/2、`max_nodes=100`、`max_evidence_items=6`、`max_chars=4000`。Adaptive GENERAL 使用默认 beam 10 和 drop threshold 0.15。所有组合重复两次，结果 fingerprint 一致。

## Results

### Per scenario

下面的 evidence 顺序均为 turn 语义顺序的简写。无关证据数在所有隔离场景中均为 0。

| 场景 | depth 0 anchor | Fixed 最小共同召回 depth / depth 1 evidence | Fixed 顺序 | Adaptive 最小 depth / depth 1 evidence | Adaptive 顺序 |
|---|---|---|---|---|---|
| A 显式实体 | 前件 | 1 / 前件 → 显式追问 | 正确 | 1 / 前件 → 显式追问 | 正确 |
| B 同 segment 代词 | 前件 | 1 / 前件 → 代词句 | 正确 | 1 / 前件 → 代词句 | 正确 |
| C 地点指代 | 前件 | 1 / 前件 → 地点指代句 | 正确 | depth 0-2 均未共同召回 / 仅前件 | 不适用 |
| D 方案指代 | 前件 | 1 / 前件 → 选择句 → 后续句 | 正确 | 1 / 前件 → 后续句 → 选择句 | 错误 |
| E 跨 segment | 前件 | 1 / 前件 → 跨 segment 代词句 | 正确 | 1 / 前件 → 跨 segment 代词句 | 正确 |
| F 弱语义承接 | 指代句 | 1 / 指代句 → 前件 | 错误 | 1 / 指代句 → 前件 | 错误 |

### Aggregate metrics

| 模式 / depth | 前件召回率 | 指代句召回率 | 共同召回率 | 正确顺序率 | 平均 evidence | 平均无关 evidence |
|---|---:|---:|---:|---:|---:|---:|
| Fixed / 0 | 83.3% | 16.7% | 0.0% | 0.0% | 1.00 | 0.00 |
| Fixed / 1 | 100.0% | 100.0% | 100.0% | 83.3% | 2.17 | 0.00 |
| Fixed / 2 | 100.0% | 100.0% | 100.0% | 83.3% | 2.17 | 0.00 |
| Adaptive GENERAL / 0 | 83.3% | 16.7% | 0.0% | 0.0% | 1.00 | 0.00 |
| Adaptive GENERAL / 1 | 100.0% | 83.3% | 83.3% | 50.0% | 2.00 | 0.00 |
| Adaptive GENERAL / 2 | 100.0% | 83.3% | 83.3% | 50.0% | 2.00 | 0.00 |

depth 1 的 scope 切分：

| 模式 | same-segment 共同召回 / 正确顺序 | cross-segment 共同召回 / 正确顺序 |
|---|---:|---:|
| Fixed | 100.0% / 80.0%（5 个场景） | 100.0% / 100.0%（1 个场景） |
| Adaptive GENERAL | 80.0% / 40.0%（5 个场景） | 100.0% / 100.0%（1 个场景） |

跨 segment 只有一个诊断样本，不能据此估计真实成功率。

## Interpretation

有效的部分是 Event 文本保真、稳定 provenance、全图双向 temporal adjacency，以及固定有界遍历在这些小型隔离图中将相邻轮共同带回。概率性部分包括 anchor 选择、semantic links、Adaptive semantic drop、beam 排序和最终预算裁剪。

不受支持的部分包括：代词到实体的显式绑定、segment-aware 邻接、上下文化 Event embedding、session/episode grouping、通用 dialogue-window retrieval，以及 GENERAL 下的稳定对话顺序恢复。

当前系统依赖局部图邻接和最终 LLM 推理缓解指代问题，不构成显式指代消解保证。

## Recommendation

保持当前生产行为，并在产品文档中把它描述为局部上下文共同召回，而不是 coreference resolution。当前没有证据支持立即改变 Event 粒度、重写文本或加入自定义指代算法。

若后续真实失败样本证明仅靠 temporal adjacency 不够，可单独立项评估固定上游 `MemoryBuilder.create_context_links(window_size=3)`：限定为每个 Cold segment 的有序 Event IDs、明确禁止跨 segment 泄漏，并验证 Recall 预算与输出顺序。该建议只是上游对齐的后续评估方向；本次没有接线，也没有引入新 relation。

不建议直接接入上游完整 `build_memory()`、session nodes 或 episode summarization，因为当前实现依赖 LoCoMo/session-summary 对象或额外 LLM，超出 Lumina 当前真实 DTO 和本任务边界。

## Limitations

- 只有 6 个小型合成场景，没有统计显著性，也没有覆盖长图、高噪声、多实体、多语言混合或大时间间隔。
- 每个场景独立建图以避免重复实体查询相互污染；无关证据为 0 不能外推到生产图。
- 只使用一个跨 segment 样本。
- 没有外部回答模型；实验只判断 evidence 可用性和顺序，不判断模型能否正确回答。
- 共同召回不证明正确的指代绑定，更不证明完整 coreference resolution。
- 结果只适用于当前固定上游、当前 MiniLM、本脚本参数和当前合成 fixture。
