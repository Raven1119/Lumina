> **Status: HISTORICAL — SUPERSEDED SCAN**
>
> This July 2026 scan predates rolling physical Hot compaction, line-level Cold
> storage, browser Dream, adaptive/RRF/linearized Recall completion, fixed
> background injection, and single-conversation History. Use
> `docs/LUMINA_CODEBASE_SCAN.md` for the current codebase-wide baseline.
>
> The body below is retained for historical architecture and debt context.

# Lumina 记忆系统全量代码扫描报告

扫描日期：2026-07-30  
扫描对象：当前工作树中的 Lumina 记忆相关源码，包含固定的上游 MAGMA  
报告性质：代码级静态扫描与现有测试意图审阅，不是新的功能设计或生产改动

## 1. 结论摘要

当前记忆系统可以概括为：

```text
浏览器聊天
→ 带原生 turn provenance 的 Hot Draft
→ Cold-first 成对逻辑压缩
→ immutable Cold Draft segment
→ 手动 Dream
→ Lumina Conversation Memory Adapter
→ MAGMA Event Graph + Vector Index
→ 有界 anchor / graph-expansion Recall
→ 仅将 bounded rendered_text 可选注入聊天模型
```

代码体现了四个明确的系统选择：

1. **Draft 是聊天连续性的第一层事实来源。**
   新消息先以带稳定 `turn_id`、独立时间戳和来源时区的 turn 表示，
   Hot Draft 物理上只追加；压缩只改变逻辑视图。
2. **长期记忆写入与聊天路径分离。**
   只有手动 Dream 消费 `pending_digest` Cold Draft；聊天请求本身不运行
   Dream，也不向 MAGMA 写入。
3. **MAGMA 被当作私有图/向量后端，而不是 Lumina 的公共接口。**
   Lumina 自己拥有 DTO、provenance、checkpoint、Recall policy、裁剪和安全
   错误；上游 UUID、分数、路径、图对象和 narrative 不公开。
4. **当前 Recall 已能公开合格的非 anchor 图扩展事件，但仍是固定检索管线。**
   `top_k` 限制向量 anchors，`max_evidence_items` 限制最终 anchors 加扩展
   的总量；系统尚无 Recall scheduler，也没有检索后的 Evidence Organizer。

从代码完整性看，本次扫描没有发现 Python 语法错误，也没有发现上游 MAGMA
工作树被修改。当前主链的 provenance、Cold-first、Dream checkpoint、
Recall 边界和安全降级均有对应测试。

从架构成熟度看，主要限制集中在：

- 文件存储与 MAGMA graph/vector/checkpoint 之间没有跨文件事务；
- 本地 JSONL/JSON 没有多进程写锁，读取多为全文件扫描；
- 图扩展能提高目标 evidence 覆盖，但会显著放大无关证据；
- Recall 没有不召回/轻召回/图增强调度，也没有证据充分性升级；
- 最终模型只看到扁平拼接的 evidence 文本，没有状态、冲突、时间线或
  Evidence Ledger 整理。

## 2. 扫描范围与方法

### 2.1 源码覆盖

本次逐文件完整读取了以下代码：

| 区域 | 文件数 | 代码行数 | 含义 |
| --- | ---: | ---: | --- |
| `edge/static` | 3 | 302 | 浏览器聊天与客户端时区入口 |
| `core` | 11 | 1,017 | API、聊天运行时、Hot/Cold Draft 与压缩 |
| `Dream` | 7 | 964 | 手动 Cold Draft 消费和记忆写入编排 |
| `Conversation_Memory` | 16 | 2,100 | Lumina-owned DTO、adapter、temporal、Recall |
| `scripts` | 3 | 1,388 | Recall E2E 与 depth 评测 |
| `tests` | 8 | 1,297 | 根级聊天、Draft 和 E2E 回归 |
| `Conversation_Memory/upstream/MAGMA` | 23 | 11,963 | 上游 MAGMA 全部 Python 源码 |
| **总计** | **71** | **19,031** | |

源码统计排除了 `.venv`、缓存、生成数据和 `__pycache__`。同时核对了根依赖、
MAGMA 依赖和上游提交。

### 2.2 固定上游状态

扫描时 MAGMA 提交为：

```text
467cb70b67ac337b22fdb42194d37c04ad701b62
```

`git -C Conversation_Memory/upstream/MAGMA status --short` 输出为空，因此报告
描述的是未被 Lumina 修改的上游实现。

### 2.3 静态验证

- 对 68 个 Python 文件执行了 AST 解析；
- 结果为 `syntax_errors = 0`；
- 本次没有运行模型、Dream、全量 pytest 或真实 MAGMA E2E；
- 运行时行为结论来自实际代码、对应测试和仓库内已记录的 Recall depth
  评测，未把未执行的上游实验脚本视为生产验证。

## 3. 总体架构

```text
┌──────────────────────────────── Chat / Draft ────────────────────────────────┐
│ Browser                                                                    │
│   └─ message + client IANA timezone                                        │
│      └─ FastAPI /api/chat                                                  │
│         └─ MessageRuntime                                                  │
│            ├─ load logical Draft context                                   │
│            ├─ optional bounded Recall                                      │
│            ├─ ModelClient.generate                                         │
│            ├─ append user / assistant Hot Draft turns                      │
│            └─ Cold-first pair-aware logical compaction                     │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │ pending_digest Cold Draft
                                     ▼
┌──────────────────────────────── Manual Dream ───────────────────────────────┐
│ DreamRunner                                                                 │
│   └─ ColdDraftDigestionService                                             │
│      ├─ convert production record to Conversation Memory DTO               │
│      ├─ MagmaMemoryAdapter.ingest                                          │
│      ├─ require completed checkpoint                                       │
│      └─ ColdDraft owner marks segment consumed                             │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     ▼
┌──────────────────────────── Conversation Memory ────────────────────────────┐
│ Lumina Adapter                                                             │
│   ├─ deterministic evidence_id                                             │
│   ├─ temporal normalization                                                │
│   ├─ source provenance                                                     │
│   ├─ durable ingestion checkpoint                                          │
│   └─ RealMagmaBackend                                                      │
│       ├─ NetworkX MultiDiGraph                                             │
│       ├─ FAISS or NumPy vector index                                       │
│       └─ graph.json + vectors                                               │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     ▼
┌────────────────────────────────── Recall ───────────────────────────────────┐
│ query → MiniLM embedding → vector anchors → bounded graph traversal         │
│       → eligible EventNode expansions → Lumina MemoryEvidence               │
│       → count/character bounds → MemoryContext.rendered_text                │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 4. 代码区域的含义

## 4.1 浏览器入口：`edge/static`

`app.js` 只负责同源聊天 UI 和安全显示。对记忆系统最重要的行为是：

- 读取 `Intl.DateTimeFormat().resolvedOptions().timeZone`；
- 将 IANA timezone 作为 `client_timezone` 随消息提交；
- 不直接访问 Draft、Dream 或 MAGMA；
- 只显示 API 返回的模型文本和 mock/model/fallback 标签。

这意味着来源时区从浏览器开始进入 provenance，但浏览器不是记忆存储者。

## 4.2 Chat 与 Draft：`core`

### `contracts.py`

定义聊天请求、响应和 `DraftTurn`/`MemoryTurn`。新 turn 必须包含：

```text
turn_id
role
text
created_at
source_timezone
timezone_source
```

新写入要求 aware UTC 时间和原生 provenance；旧 role/text 记录仍可读。

### `turn_provenance.py`

为 user 与 assistant/fallback turn 在首次持久化前生成不同 ID 和时间戳，
并验证客户端 IANA 时区。缺失或无效客户端时区会如实标记为
`configured_default`。

### `draft_store.py`

Hot Draft 是 append-only JSONL：

- 新 turn 以 schema V2 写入；
- 相同 `turn_id` 与相同内容重试视为幂等；
- 相同 ID 但不同内容视为冲突；
- 读取时跳过损坏行和不支持的角色；
- 读取最近 turn 仍需扫描 JSONL。

### `cold_draft_store.py`

Cold Draft 以 segment 为单位：

- `append_segment` 创建 `pending_digest`；
- 显式 `segment_id` 支持幂等重试；
- `mark_consumed` 只改变状态和消费时间；
- 原 turn 文本和 provenance 不被摘要或改写；
- 状态更新通过临时文件替换整个 Cold Draft 文件；
- 没有多进程 writer lock。

### `hot_draft_compactor.py`

压缩是逻辑压缩，不删除物理 Hot Draft：

1. 只选择完整 user/assistant pair；
2. 先把原始 turns 保存成 Cold Draft segment；
3. Cold 保存成功后才推进 Hot 逻辑压缩 checkpoint；
4. 如果 Cold 写入失败，逻辑上下文完全不前移；
5. 如果 Cold 已写成功但状态推进失败，重试复用同一 segment ID。

这就是项目的 Cold-first 不变量。

### `draft_context.py`

模型上下文由两部分组成：

- 已压缩区的 preservation marker；
- 尚未压缩的最近原始 turns。

最近 raw tail 有界，但 marker 总量尚无全局上限。

### `message_runtime.py`

单次聊天顺序是：

```text
创建 user turn provenance
→ 读取旧的逻辑 Draft 上下文
→ 可选 Recall(query = 当前用户消息)
→ 仅把 MemoryContext.rendered_text 加入临时模型上下文
→ ModelClient.generate
→ 生成 assistant turn provenance
→ 尝试写 user turn
→ 尝试写 assistant turn
→ 尝试 compaction
```

需要注意：

- 当前 user turn 在模型生成前尚未写入旧上下文；
- Recall 注入块只存在于本次模型输入，不会写入 Hot/Cold Draft；
- user 写入失败不会阻止 assistant 写入，因此 pair 写入不是事务；
- Draft 失败对外仍返回安全响应，内部只记录安全事件标签。

### `main.py`

FastAPI 负责：

- `/api/status`、`/api/chat`；
- 静态前端；
- 构造唯一 `MessageRuntime`；
- 默认关闭 Recall；
- Recall 开启但初始化失败时退化为普通聊天；
- 从环境构造 MAGMA persistence 和 ingestion state 路径。

Dream 和 MAGMA 都不是默认聊天启动成功的硬依赖。

### `model_client.py`

支持：

- 默认 mock；
- 明确配置的 MiniMax Anthropic-compatible HTTP adapter；
- 过滤 provider thinking block；
- 网络、状态码或响应格式失败时抛出清洗后的错误；
- `MessageRuntime` 将错误转成安全 fallback。

它不是记忆后端，只消费 Draft/Recall 构造好的上下文。

## 4.3 手动长期记忆写入：`Dream`

### `models.py` 与 `interfaces.py`

定义 Dream policy、每 segment 结果和运行结果，以及 Cold Draft owner /
Conversation Memory ingestor 的窄接口。

### `cold_draft_digest.py`

这是 Cold Draft 到 Conversation Memory 的协调边界：

- V2 turn 原样保留 ID、timestamp、timezone 和 timezone source；
- legacy turn 生成确定性 fallback ID，并标记
  `legacy_segment_fallback`；
- 只有 `pending_digest` 可进入；
- 只有 memory ingestion 返回 completed 且数量一致时，才允许消费；
- 标记 consumed 必须通过 Cold Draft owner，Dream 不直接编辑 JSONL。

### `runner.py`

Dream 保持：

- 手动；
- 同步；
- 按 Cold Draft 顺序；
- 由 `max_segments` 限界；
- 可选择首错停止或逐段隔离；
- 输出安全错误码，不暴露路径、原文或 traceback。

一个重要恢复窗口是：

```text
memory completed
→ consumed 状态写入失败
→ 下次 Dream 命中 completed checkpoint
→ already_ingested
→ 只重试 consumed
```

因此消费重试不会重复创建逻辑记忆。

## 4.4 Lumina Conversation Memory

### `adapter/models.py`

公开模型分为三类：

- ingestion 输入：`ColdDraftSegment`、`ColdDraftTurn`；
- provenance/output：`SourceProvenance`、`MemoryEvidence`、
  `MemoryContext`；
- 内部后端候选：`BackendCandidate`。

`BackendCandidate.score` 和 metadata 是私有排序载体，不属于公开 evidence。

当前 `RecallPolicy` 语义：

| 参数 | 真实含义 |
| --- | --- |
| `top_k` | 向量搜索最多产生多少 anchors |
| `max_graph_depth` | `0` 为 anchor-only；大于 0 允许该深度内的图扩展 |
| `max_nodes` | 后端候选总节点预算 |
| `max_evidence_items` | 最终公开 anchors + expansions 总量 |
| `max_chars` | 最终 `rendered_text` 字符上限 |

### `magma_adapter.py`

写入路径：

1. 校验 segment 和 turn；
2. 读取 `(segment_id, ingestion_version)` checkpoint；
3. 为每个 turn 生成 SHA-256 `evidence_id`；
4. 按 evidence ID 查询已有 MAGMA 事件；
5. 生成 Lumina-owned temporal metadata、entities 和 provenance；
6. 每个 conversation turn 写成一个 MAGMA event；
7. 每个 event 后持久化并更新 in-progress checkpoint；
8. 全段完成后建立 entity relationships；
9. 再次持久化并写 completed checkpoint。

这里的“每 turn 一个 event”保留了来源证据粒度，避免 Dream 把一段对话先摘要
成一个不可追溯大节点。

Recall 路径：

1. 调用私有 backend；
2. 只接受含 `evidence_id` 和可构造 `SourceProvenance` 的 candidate；
3. 按内部 score、timestamp、evidence ID 稳定排序；
4. 使用 `max_evidence_items` 和 `max_chars` 裁剪；
5. 返回 `MemoryContext`；
6. 任意异常返回安全的 `recall_unavailable`。

### `backend.py`

`RealMagmaBackend` 是上游 MAGMA 的防腐层：

- 只在私有目录导入固定上游；
- 强制 Hugging Face offline 模式；
- 使用 MiniLM，不启用 MAGMA LLM backend；
- 初始化时加载 graph，vector backend 由 MAGMA 自己按 persistence path 加载；
- persistence 保存 `graph.json` 和 `vectors`；
- Lumina 代码不直接向外暴露 NetworkX、FAISS 或 MAGMA 类型。

写入关系包括：

- MAGMA `add_event` 自动产生的时间边；
- MAGMA `add_event` 自动产生的语义相似边；
- Lumina 调用上游 entity-edge helper 产生的共享实体事件边。

这里没有创建独立实体节点；entity relationship 仍是事件到事件的边。

当前 graph expansion projection 的真实实现是：

1. 保留 `QueryContext.anchor_nodes`；
2. 读取 MAGMA 已完成遍历返回的 `traversal_paths`；
3. 从 path 中提取非 anchor node ID 和最小 hop；
4. 再从 MAGMA graph 解析节点；
5. 只接受 `EventNode` 且必须有文本、aware timestamp、evidence ID 和完整
   provenance；
6. 跳过 episode/session/narrative/entity 类节点和 malformed path；
7. anchor 始终在 expansion 前；
8. expansion 按 hop、timestamp、evidence ID、node ID 排序；
9. 内部占位 score 只维持排序，不是相关性评分，也不会公开；
10. `narrative_context`、路径、图、统计、embedding 均不投影。

### `recall/rendering.py`

最终渲染非常简单：

```text
evidence 1 的原文
evidence 2 的原文
...
```

它按换行拼接文本，并执行 count/character bound。timestamp 和 provenance
存在于 `MemoryEvidence`，但模型注入只使用 `rendered_text`，因此模型不会直接
看到 evidence ID、来源段、turn ID 或结构化时间线。

### `ingestion/temporal.py`

这是 Lumina 自己的确定性时间规范化，不是上游 MAGMA parser：

- 基于每个 turn 的 `created_at` 和 `source_timezone`；
- 支持指定范围内的中英文相对日/周/月/年、定向 weekday 和绝对日期；
- 输出 aware UTC 半开区间 `[start, end)`；
- 使用真实 IANA 本地日历和 DST；
- 保留原表达；
- 多 mention 按最长 span 规则处理；
- 不使用 Dream 运行时间，不调用 LLM 或网络。

规范化结果目前作为 event metadata 持久化。生产 Recall 的 anchor 选择仍主要
依赖 query embedding，图遍历也没有按这些区间执行显式时间过滤。

### `ingestion/state_store.py`

checkpoint 是单个 JSON 字典：

- key 为 `segment_id:ingestion_version`；
- 写入使用临时文件、flush、fsync 和 replace；
- 支持 pending/in_progress/failed/completed；
- 文件损坏会安全失败；
- 整个文件读写，没有多进程锁或增量索引。

## 4.5 上游 MAGMA 的代码含义

## 4.5.1 Lumina 当前真正使用的部分

### `memory/graph_db.py`

核心结构是 NetworkX `MultiDiGraph`：

- node：EVENT、EPISODE、NARRATIVE、ENTITY、SESSION；
- link：TEMPORAL、SEMANTIC、CAUSAL、ENTITY；
- 允许双向邻居读取；
- `traverse` 使用受 `max_depth` 和 `max_nodes` 限制的 BFS；
- 返回 nodes、links、paths、node depths 和 stats；
- persistence 为 JSON graph export/import。

注意：`traverse` 只把前 10 条 paths 放入返回值。Lumina 当前从
`QueryContext.traversal_paths` 投影 expansion，因此公开图扩展的候选覆盖还
间接受这个上游 path cap 限制，而不等价于“所有 visited nodes 都可投影”。

### `memory/vector_db.py`

提供：

- FAISS `IndexFlatL2` 等实现；
- 无 FAISS 时的 NumPy cosine search；
- MiniLM 或 OpenAI embedding encoder；
- 自动选择 FAISS/NumPy；
- vector metadata 和 persistence。

FAISS 路径把 L2 distance 转成 `1 / (1 + distance)`，NumPy 路径使用 cosine。
两种 backend 的 score 标度不完全相同。Lumina 没有 active threshold，因此
它目前主要影响排序，不构成相关性 gate。

### `memory/keyword_enrichment.py`

在 embedding 前把启发式关键词、实体、topic、speaker 附加到原文。查询也会
追加提取的关键词。这是生产 anchor 搜索的一部分，但它以英文正则和 stop-word
为主，不是多语言语义理解器。

### `memory/trg_memory.py`

`add_event`：

```text
简单事件抽取
→ EventNode
→ keyword-enriched MiniLM embedding
→ graph node + vector entry
→ 临近时间双向边
→ top-k 相似事件双向语义边
```

`query`：

```text
keyword-enriched query
→ query embedding
→ vector anchors
→ constrained graph traversal
→ narrative synthesis
→ QueryContext
```

Lumina 使用 anchors、search scores 和 traversal paths；不使用 MAGMA
生成的 narrative。

## 4.5.2 上游存在、但 Lumina 生产路径未使用的部分

| 上游模块 | 代码含义 | Lumina 当前状态 |
| --- | --- | --- |
| `memory_builder.py` | LoCoMo turn/session/episode 构建、批量关系和关键词索引 | 未调用 |
| `query_engine.py` | 查询分类、RRF、keyword/full scan、adaptive traversal、rerank | 未调用 |
| `episode_segmenter.py` | LLM 话题边界和 episode 摘要 | 未调用 |
| `temporal_parser.py` | 英文、近似天数的上游时间解析 | 被 Lumina parser 替代 |
| `answer_formatter.py` | QA prompt、答案清洗和上下文格式化 | 未调用 |
| `best_of_n_selector.py` | 多次回答和投票/judge 选择 | 未调用 |
| `llm_judge.py` | OpenAI 语义评分 | 未调用 |
| `evaluator.py` | EM/F1/BLEU/LLM Judge 汇总 | 未调用 |
| `longmemeval_evaluator.py` | LongMemEval rubric judge | 未调用 |
| `test_harness.py` | LoCoMo QA 执行和并行评测 | 未调用 |
| `utils/memory_layer.py` | 另一套 agentic memory/evolution 实验代码 | 未调用 |
| `test_fixed_memory.py` | LoCoMo 实验入口 | 非生产 |
| `test_longmemeval_chunked.py` | 大量数据集定制检索和 prompt heuristics | 非生产 |

因此不能因为这些类存在，就描述 Lumina 已具备：

- 查询分类或 none/light/deep Recall；
- adaptive edge/depth routing；
- RRF、全图扫描或模型重排序；
- episode/session memory；
- LLM causal inference；
- memory evolution/consolidation；
- LLM Judge 或 answer-quality validation。

这些能力只存在于上游研究/评测代码中，没有穿过 Lumina-owned facade。

## 5. 三条真实运行链路

## 5.1 聊天与 Cold-first Draft

```text
用户消息 + client timezone
→ 创建 user DraftTurn
→ 读取旧逻辑 Draft context
→ 可选 Recall
→ 模型或 fallback
→ 创建 assistant DraftTurn
→ Hot Draft 依次追加两条 turn
→ 达阈值时选择完整 pair
→ Cold Draft pending segment 先成功
→ Hot logical checkpoint 后推进
```

## 5.2 手动 Dream 与 MAGMA 写入

```text
用户手动运行 Dream
→ 按顺序选择有限 pending segments
→ V2/legacy record 转换
→ checkpoint lookup
→ 每 turn 生成 evidence_id + provenance + temporal metadata
→ MAGMA add_event
→ graph/vector persist
→ in-progress checkpoint
→ entity links + final persist
→ completed checkpoint
→ Cold Draft mark_consumed
```

## 5.3 Recall 与聊天注入

```text
query
→ MiniLM anchor search(top_k)
→ constrained graph traversal(max_graph_depth/max_nodes)
→ anchors
→ traversal_paths 中合格 EventNode expansions
→ anchor-first stable candidates
→ Lumina provenance projection
→ max_evidence_items
→ max_chars
→ MemoryContext.rendered_text
→ 可选临时注入模型
```

失败或空 Recall：

```text
Recall unavailable / empty
→ 不添加 memory block
→ 普通聊天继续
```

## 6. 数据与持久化

| 数据 | 位置/格式 | 写入者 | 主要不变量 |
| --- | --- | --- | --- |
| Hot Draft | append-only JSONL | `JsonlDraftStore` | 新 turn V2 provenance；物理不压缩 |
| Compaction state | JSON | `HotDraftCompactor` | Cold 成功后才推进 |
| Cold Draft | segment JSONL | `ColdDraftStore` | pending/consumed；原 turns 保留 |
| Ingestion checkpoint | JSON | `IngestionStateStore` | `(segment_id, ingestion_version)` |
| MAGMA graph | `graph.json` | `RealMagmaBackend` | event/link 持久化 |
| MAGMA vectors | `vectors` | MAGMA vector backend | node ID 对应 embedding |

当前 persistence 不是一个跨文件事务：

```text
graph.json
vectors
ingestion_state.json
cold_drafts.jsonl state
```

分别写入。现有幂等设计覆盖了多数重试窗口，但不能把四份文件变成原子提交。

一个值得后续专门验证的边界是：如果 graph 保存成功而 vector 保存失败，并在
进程重启后重试，`find_memory_id` 可能因 graph 已存在而跳过 `add_event`；
缺失的 vector entry 是否能够自动修复，当前代码没有显式 reconciliation。
这是静态扫描发现的恢复风险，不代表已在真实运行中复现。

## 7. 测试与评测代码表达的保证

### Draft / Chat

测试覆盖：

- append-only、restart、corrupt line skip；
- turn ID 幂等和冲突；
- user/assistant provenance 不同；
- Cold-first 失败不推进；
- compaction state 失败后不重复 Cold segment；
- Recall 默认关闭；
- Recall 只注入 rendered text；
- empty/failure Recall 不阻断；
- memory DTO 和内部字段不进入模型或 Draft；
- provider failure 安全 fallback。

### Dream / Ingestion

测试覆盖：

- pending 顺序和 bounded selection；
- V2 provenance 与 legacy fallback；
- partial failure、restart、idempotent retry；
- memory complete before consumed；
- consume failure 后只重试状态转移；
- 原始 Cold turns 不被改写；
- safe error 不泄露路径或原文。

### Real MAGMA / Recall

测试覆盖：

- 真实事件/向量 persistence；
- restart 后 Recall；
- `top_k` anchor 上限；
- `max_evidence_items` 总 evidence 上限；
- `max_chars`；
- depth 0 anchor-only；
- depth 1 能返回非 anchor traversal event；
- 多路径去重和最小 hop；
- malformed path、非 EventNode、缺 provenance 跳过；
- anchor-first 稳定顺序；
- UUID/path/hop/score/graph/metadata 不公开；
- 上游 MAGMA 工作树保持干净。

### Recall depth 评测的代码含义

现有 synthetic ablation 使用 48 turns、20 questions 比较 depth 0/1/2：

- depth 1 比 depth 0 明显提高 gold evidence recall；
- evidence 数量、上下文长度和 strict-gold noise 同时上升；
- 两个 no-answer control 在所有深度都返回 evidence；
- depth 2 在该数据集与 depth 1 返回完全相同的有序 evidence。

这支持继续研究“anchor-only / depth-1 graph-enhanced”两档选择，但不证明
生产 scheduler 已经存在，也不证明可直接从小型 synthetic 数据外推。

## 8. 代码级问题与风险

## 8.1 高优先级：Recall 找到了证据，但还不会组织证据

当前从 `BackendCandidate` 到 `MemoryContext` 只做：

- provenance 合法性过滤；
- anchor-first 排序；
- count/character 裁剪；
- 原文换行拼接。

没有：

- 语义重复合并；
- 当前状态与历史状态区分；
- 冲突证据并列；
- 事件时间线；
- Evidence Ledger；
- 证据充分性判断；
- 关系解释。

因此它解决的是“安全、有限地找到并返回记忆”，还没有解决“让模型正确理解一组
可能重复、过时或冲突的记忆”。

## 8.2 高优先级：固定 Recall 会在无答案查询上继续给出证据

生产 Recall 当前固定执行：

```text
embedding
→ vector anchors
→ 配置深度的 graph traversal
```

没有 relevance gate、none/light/deep scheduler 或 insufficiency escalation。
depth 评测中的 no-answer controls 仍返回 anchors，图扩展时还会扩到总量上限。
这不是安全泄漏，但会给模型制造“必须从这些记忆里找答案”的错误暗示。

## 8.3 高优先级：图扩展增加覆盖，也显著增加噪声

当前 graph traversal 默认允许 temporal、semantic、causal，并实际还可经过
entity links。没有按 query 选择 edge type，也没有 relation weight 或扩展节点
重排。depth 1 在 synthetic 评测中提高 recall，同时提高 irrelevant ratio。

此外公开 expansion 来自 `traversal_paths`，而上游只保留前 10 条 path。它不是
完整 visited-node 投影，也没有在公开 evidence 中说明节点为什么被扩展出来。

## 8.4 中优先级：跨文件 durability 是可恢复流程，不是事务

Cold Draft、MAGMA graph、vector index、checkpoint 和 consumed state 分开落盘。
设计通过 evidence ID、segment/version checkpoint 和重试窗口实现“最终收敛”，
但以下情况仍依赖单进程和具体失败时点：

- graph/vector 部分保存；
- checkpoint replace 失败；
- memory completed 后 consumed 失败；
- 进程在任意两个持久化点之间退出。

已有测试覆盖多种模拟失败，但没有通用的 graph/vector reconciliation。

## 8.5 中优先级：本地文件模型限制扩展性

- Hot/Cold JSONL 读取扫描全文件；
- `find_memory_id` 线性扫描全部 graph nodes；
- ingestion state 整体读取/替换；
- Cold consumed 更新整体重写；
- 没有多进程 writer lock；
- Dream 和 checkpoint 假定单 active writer。

这些选择适合当前 local-first、单用户、最小系统，但不是多进程或大规模历史的
最终存储形态。

## 8.6 中优先级：上游导入面较重

Python 导入 `memory.graph_db` 或 `memory.trg_memory` 时会先执行上游
`memory/__init__.py`，它又导入 QueryEngine、Evaluator、LLMJudge 等非生产模块。
`LLMJudge` 还在 import time 构造 OpenAI client。Lumina backend 用临时占位
`OPENAI_API_KEY` 绕过这一上游耦合，然后显式 `llm_backend=None`。

当前做法保持上游不修改，但说明 MAGMA primitive 与研究栈尚未在包级隔离；
初始化失败面大于 Lumina 实际需要的最小依赖面。

## 8.7 中优先级：时间 metadata 已写入，但 Recall 没有显式利用

Lumina 的 temporal normalization 比上游 parser 更严格，能够保留真实日历和
DST 区间。不过当前生产 query：

- 不构造 `TraversalConstraints.time_window`；
- 不按 normalized interval 做过滤或排序；
- 不把结构化时间区间放进模型可见 rendered text。

时间信息目前主要改善 provenance、metadata 和可验证性，尚未成为完整的
temporal Recall 证据组织层。

## 8.8 低优先级：上游研究代码不适合作为生产事实

上游 QueryEngine/LongMemEval 代码包含大量：

- 数据集专用实体、月份和 session 常量；
- regex query classification；
- heuristic weights 和 hard-coded prompts；
- full graph scan；
- debug print；
- import-time model/client 初始化；
- 宽泛异常 fallback。

这些代码可以作为实验参考，但不应直接进入 Lumina facade，也不应被文档描述为
当前生产能力。

## 9. 已确认不存在或未进入生产的内容

当前代码没有在 `/api/chat` 中执行：

- Dream；
- memory ingestion；
- MAGMA event write；
- background consolidation；
- LLM causal inference；
- evaluator 或 LLM Judge；
- upstream QueryEngine；
- Cold Draft scan Recall；
- injected-memory Draft persistence。

当前公开输出也不包含：

- MAGMA node UUID；
- vector/embedding；
- backend score；
- traversal path/hop；
- NetworkX/FAISS 对象；
- `narrative_context`；
- raw Cold Draft record；
- provider credential/body/traceback。

## 10. 当前系统的准确定位

当前 Lumina 记忆系统已经是一个具备清晰所有权边界的最小长期记忆闭环：

```text
可追溯对话事实
→ Cold-first 保存
→ 手动、幂等长期写入
→ 图/向量持久化
→ 有界、可恢复、可选注入的 Recall
```

它还不是：

```text
自动决定何时想起什么
→ 根据问题动态选择 Recall 深度和关系
→ 整理状态、冲突、时间线和证据充分性
→ 让模型得到经过解释的记忆结论
```

用一句话概括代码含义：

> 这套代码已经建立了“记忆可以被可靠保存、追溯、有限召回并安全进入聊天”的
> 基础设施，但尚未建立“系统知道何时需要哪类记忆，以及怎样把多条记忆组织成
> 可正确理解的证据”的认知调度与证据整理层。

## 11. North Star 对齐

**服务的 North Star 属性**

跨时间的记忆连续性、身份事实可追溯性和整体心智未来可整合性。

**当前设计为何有帮助**

Cold-first、turn provenance、幂等 Dream、Lumina-owned facade 和 bounded Recall
使长期记忆不会依赖一次模型调用或不可审计的摘要，并为以后建立状态、冲突和
自我叙事层保留了可信来源。

**本次刻意没有实现**

没有新增 scheduler、Evidence Organizer、Conversation Graph、自动 Dream、
agents/workers、LLM Judge、上游 MAGMA 修改或新的生产抽象。本报告只描述当前
代码事实和静态风险。

