# Lumina Recall 对本地 MAGMA Upstream 的算法审计

> 审计对象：当前工作区中的 Lumina Recall 生产链路，以及本地固定版本
> `Conversation_Memory/upstream/MAGMA` 的实际源码。
>
> 审计方法：以本地源码的真实可达调用链为第一事实来源；README、论文、注释和未被调用的
> helper 只作为补充，不能替代可达性证明。
>
> 审计性质：只读算法审计。本报告没有改动生产代码、测试、参数、配置、模型或 upstream。

## 1. Executive conclusion

### 1.1 最终判定

| 问题 | 判定 | 核心依据 |
|---|---|---|
| Lumina 是否完整使用 MAGMA 的原生 Recall/Query Engine？ | **NO / PARTIAL** | Lumina 复用了 `TemporalResonanceGraphMemory.query()`、图存储和 `NetworkXGraphDB.traverse()`，但没有实例化或调用 upstream `QueryEngine.query()`。 |
| Lumina 是否完整使用 MAGMA 的 query-adaptive policy？ | **NOT_USED** | `RecallPolicy` 不再暴露 rejected adaptive controls，生产固定走 BFS。Upstream 的 `detect_query_type()` / `get_adaptive_params()` 也未进入生产链。 |
| Lumina 是否完整使用“四维图自适应遍历”？ | **PARTIALLY_USED** | 当前图是一个 `MultiDiGraph` 上的四类边，不是四张独立图。Lumina 固定 BFS 可以经过实际存在且约束允许的边，但没有 query-type 路由、边型偏好、语义下降门或按查询自适应深度；生产数据又基本没有 causal 边。 |
| 现有 Recall 是否已经流经 BGE？ | **YES** | 非空候选经固定 `BAAI/bge-reranker-v2-m3` 批量重排，再经 Hindsight 修正、`final_min_score >= 0.144` 和公共 DTO 上限。空候选不会加载 BGE。 |
| 60 例 temporal 失败是否能直接归因于绕过 upstream adaptive policy？ | **PARTIAL** | 有明确机制关联，但不能单因果归因。固定一跳 BFS、没有 temporal query route/boost 是合理原因；BGE 排序、0.144 admission、图边构造和数据本身也共同决定结果。 |
| missing-private / wrong-relation 失败是否能由恢复 upstream QueryEngine 直接解决？ | **PARTIAL / INCONCLUSIVE** | wrong-relation 与缺少实体/关系型路由和 rerank 有部分机制关联；missing-private 的核心是证据充分性/无答案 admission，而 upstream QueryEngine 本身没有可靠的“不存在”判定门。 |
| 能否直接恢复 upstream 能力而不发明新算法？ | **PARTIAL** | 可复用 query type、参数表、第三路 scan anchor、部分扩展逻辑；完整 QueryEngine 依赖 benchmark 索引、session/dia 映射、英文启发式和未受 Lumina 公共 DTO/隐私边界约束，不能原样接入。 |

### 1.2 一句话架构判断

当前 Lumina 是：

```text
MAGMA 的底层 TRG dense anchor + 通用图 BFS
-> Lumina 自己的 bounded lexical + 2-list RRF
-> 固定 BGE
-> Hindsight
-> final_min_score >= 0.144
-> bounded MemoryContext
```

它不是：

```text
MAGMA QueryEngine 的 query classification
-> query-type adaptive parameters
-> 3-list anchors
-> adaptive semantic traversal
-> query-type heuristic reranking
-> multi-hop/session/QA expansion
-> upstream AnswerFormatter
```

因此，本审计的顶层标记为：

```text
MAGMA_NATIVE_RECALL: PARTIAL
MAGMA_ADAPTIVE_QUERY_POLICY: NOT_USED
MAGMA_FOUR_GRAPH_TRAVERSAL: PARTIALLY_USED
```

## 2. 审计边界与事实优先级

本报告按以下优先级判断：

1. 本地固定 upstream 的可执行源码与实际调用点；
2. Lumina 当前工作区的实际生产调用链；
3. 当前测试与已持久化的脱敏聚合结果；
4. 文档、注释和论文描述。

这一区分非常重要，因为 upstream 中同时存在三类内容：

- **真实被 `QueryEngine.query()` 调用的能力**；
- **源码存在但没有调用者的 helper**；
- **注释或论文所描述、但当前本地实现并未按该形式执行的算法**。

例如，upstream 有 `_probabilistic_beam_search()`，但本地 `QueryEngine.query()` 没有调用它；实际调用的是
`_adaptive_graph_traversal()`。后者名字和注释带有“adaptive”含义，实际却是带语义门槛的 FIFO 遍历，
并不是概率 beam search。报告后文分别标明这两条路径。

## 3. 本地 MAGMA 固定版本与源码地图

### 3.1 固定版本

| 项目 | 结果 |
|---|---|
| Repository | `https://github.com/FredJiang0324/MAGMA.git` |
| Local path | `Conversation_Memory/upstream/MAGMA` |
| Pinned commit | `467cb70b67ac337b22fdb42194d37c04ad701b62` |
| `MAGMA_COMMIT.txt` | 与本地 HEAD 一致 |
| Upstream status | clean |
| Upstream diff | empty |
| License | MIT |
| License copyright | Copyright 2024 Anonymous Authors |

### 3.2 关键源码入口

| 责任 | 文件 / symbol |
|---|---|
| 完整 upstream 查询编排 | [`memory/query_engine.py`](../Conversation_Memory/upstream/MAGMA/memory/query_engine.py) — `QueryEngine.query` |
| TRG 检索入口 | [`memory/trg_memory.py`](../Conversation_Memory/upstream/MAGMA/memory/trg_memory.py) — `TemporalResonanceGraphMemory.query` |
| 图模型、四类边、BFS | [`memory/graph_db.py`](../Conversation_Memory/upstream/MAGMA/memory/graph_db.py) — `LinkType`, `TraversalConstraints`, `NetworkXGraphDB.traverse` |
| 向量检索 | [`memory/vector_db.py`](../Conversation_Memory/upstream/MAGMA/memory/vector_db.py) |
| 完整 benchmark 图构造 | [`memory/memory_builder.py`](../Conversation_Memory/upstream/MAGMA/memory/memory_builder.py) |
| upstream 答案上下文格式化 | [`memory/answer_formatter.py`](../Conversation_Memory/upstream/MAGMA/memory/answer_formatter.py) |
| Lumina backend 接口 | [`adapter/backend.py`](../Conversation_Memory/adapter/backend.py) |
| Lumina 固定检索执行 | [`adapter/_recall_execution.py`](../Conversation_Memory/adapter/_recall_execution.py) |
| Lumina anchor 融合 | [`adapter/_anchor_fusion.py`](../Conversation_Memory/adapter/_anchor_fusion.py) |
| Lumina BGE / Hindsight | [`recall/bge_reranker.py`](../Conversation_Memory/recall/bge_reranker.py), [`recall/hindsight_scoring.py`](../Conversation_Memory/recall/hindsight_scoring.py) |
| Lumina 公共 Recall facade | [`adapter/magma_adapter.py`](../Conversation_Memory/adapter/magma_adapter.py) |
| 产品接线 | [`core/main.py`](../core/main.py), [`core/message_runtime.py`](../core/message_runtime.py) |

## 4. MAGMA 本地原生 Recall 的实际管线

这里把“原生 Recall”定义为本地 upstream `QueryEngine.query()` 的实际可达路径，而不是论文中的理想算法。

### 4.1 完整调用链

```text
QueryEngine.query(question, top_k)
│
├─ detect_query_type(question)
├─ get_adaptive_params(query_type)
│
├─ TemporalResonanceGraphMemory.query(...)
│  ├─ KeywordEnricher.enrich_query
│  ├─ MiniLM/OpenAI encoder
│  ├─ vector_db.search
│  ├─ anchor nodes
│  ├─ graph_db.traverse
│  └─ narrative generation
│
├─ QueryEngine._keyword_search
├─ QueryEngine._scan_all_nodes
├─ QueryEngine._rrf_fusion(dense, keyword, scan)
├─ session-node routing / boost / filtering
│
├─ _adaptive_graph_traversal
├─ multi-hop: _retrieve_multi_hop_evidence
│  or normal: _rerank_and_filter
├─ _expand_qa_context
├─ _expand_session_context
├─ trim top_k
└─ AnswerFormatter.format_context_for_qa
```

### 4.2 TRG 底层查询不是完整 QueryEngine

`TemporalResonanceGraphMemory.query()` 自身做的是：

1. 对原始查询做关键词增强；
2. 编码增强后的查询；
3. 从向量库取 dense anchors；
4. 从 anchors 调用通用图遍历；
5. 生成 narrative；
6. 返回 `QueryContext(anchor_nodes, traversal_paths, narrative, search_scores)`。

这只是完整 QueryEngine 的一个下层输入。完整 QueryEngine 随后还会额外生成 keyword 和 full-scan anchors、
做三路 RRF、query-type traversal、启发式 rerank、上下文扩展和 AnswerFormatter。

Lumina 调用了这一层，但没有调用外层 `QueryEngine.query()`。因此“Lumina 使用了 MAGMA”成立，
“Lumina 使用了 MAGMA 完整原生 Recall”不成立。

## 5. 四维图的真实含义与构造

### 5.1 不是四张图

本地 MAGMA 的“四维图”是一个 NetworkX `MultiDiGraph` 中的四种 `LinkType`：

| Link type | 主要 subtype |
|---|---|
| `TEMPORAL` | `PRECEDES`, `SUCCEEDS`, `CONCURRENT`, `TEMPORALLY_CLOSE` |
| `SEMANTIC` | `RELATED_TO`, `SIMILAR_TO`, `PART_OF`, `CONTAINS`, `BELONGS_TO_SESSION`, builder 中的上下文/同实体关系 |
| `CAUSAL` | `LEADS_TO`, `BECAUSE_OF`, `ENABLES`, `PREVENTS`, `RESPONSE_TO`, builder 中的 `ANSWERED_BY` |
| `ENTITY` | `REFERS_TO`, `MENTIONED_IN` |

这四类边共享同一个图和同一套节点，不存在四张分别查询再融合的物理图。

### 5.2 Upstream 完整 MemoryBuilder 能建立的边

upstream benchmark 的 `MemoryBuilder` 会批量建立：

- 顺序 temporal `PRECEDES` / `SUCCEEDS`；
- 邻近上下文 semantic 边；
- embedding 相似 semantic 边；
- 不同 speaker 的 `RESPONSE_TO` causal 边；
- 同实体 semantic `SAME_ENTITY` 边；
- 24 小时内 `TEMPORALLY_CLOSE` 边；
- 启发式 `ANSWERED_BY` 边；
- session / episode 相关边。

### 5.3 Lumina 实际构造的图是其子集

Lumina 没有使用 upstream `MemoryBuilder`。`RealMagmaBackend` 直接调用
`TemporalResonanceGraphMemory.add_event()`，随后显式调用 upstream 私有的
`_create_entity_edges()`。在当前同步配置下，实际边来源是：

- immediate previous event 的 temporal `PRECEDES` / `SUCCEEDS`；
- dense seed 相似度的 semantic `RELATED_TO`；
- exact shared entity 的 `ENTITY / REFERS_TO`；
- **没有**异步 causal extraction，因为 `llm_backend=None` 且 async 未开启；
- **没有** MemoryBuilder 的 context-neighbor、same-entity semantic、temporal proximity、QA、session 或 episode 批量边。

所以，即使固定 BFS 从约束上允许 `CAUSAL`，生产图通常没有相应 causal 边可走。

### 5.4 `NodeType.ENTITY` 与 `LinkType.ENTITY` 不能混为一谈

本地源码定义了 `NodeType.ENTITY`，但未发现 active builder 创建实体节点。当前“entity graph”主要表现为事件节点之间的
`LinkType.ENTITY` 边；完整 MemoryBuilder 的同实体关系又使用 `LinkType.SEMANTIC / SAME_ENTITY`。
因此不能把本地实现描述为成熟的独立实体节点图。

## 6. Query analysis 与 routing

### 6.1 Active：`detect_query_type`

本地 `QueryEngine.query()` 会调用 `detect_query_type()`。它用英文正则和关键词把查询分为：

- `multi_hop`
- `temporal`
- `activity`
- `entity`
- `causal`
- `location`
- `open_domain`
- `factual`
- `general`

然后 `get_adaptive_params()` 为各类型选择深度、偏好边和 rerank 权重。例如：

| Type | Depth | Preferred link types | Similarity threshold |
|---|---:|---|---:|
| temporal | 5 | TEMPORAL | 0.25 |
| entity | 4 | SEMANTIC | 0.30 |
| multi_hop | 12 | SEMANTIC, CAUSAL, TEMPORAL | 0.10 |
| activity | 4 | CAUSAL, TEMPORAL | 0.28 |
| causal | 5 | CAUSAL | 0.25 |
| factual | 5 | SEMANTIC | 0.25 |
| open_domain | 6 | SEMANTIC | 0.10 |
| general | 4 | none | 0.30 |

`location` 没有独立参数表项，会回退到 general。

### 6.2 Present but unreachable：`detect_query_intent`

`detect_query_intent()` 能返回 WHY / WHEN / ENTITY，但本地没有调用点。不能把它计为当前 upstream active query route。

### 6.3 Present but unreachable：query-time temporal parser

`TemporalParser.is_temporal_question()`、`extract_time_constraints()`，以及 QueryEngine 中的日期范围/相对时间 helper，
在当前 query path 没有调用点。`TemporalParser` 的实际使用主要位于 ingestion 的 MemoryBuilder。

因此，本地 upstream 并不存在一个已经接好的、可直接恢复的 query-time 时间解析硬过滤路径。

### 6.4 Lumina 当前生产路由

Lumina 的 `RecallPolicy` 不再保留 rejected query-adaptive controls。生产请求统一使用：

```text
top_k=10
max_graph_depth=1
max_nodes=20
max_evidence_items=3
max_chars=5000
final_min_score=0.144
```

所以生产没有按 query type 选择深度、边型或阈值；所有查询统一走固定一跳 BFS。

## 7. Anchor 生成与融合

### 7.1 Upstream 完整 QueryEngine

完整 QueryEngine 使用三路 anchors：

1. TRG vector dense search；
2. `_keyword_search()`；
3. `_scan_all_nodes()`。

RRF 公式是：

```text
RRF(node) = Σ 1 / (60 + rank_i)
```

QueryEngine 再把融合值映射成：

```text
similarity_score = min(1.0, RRF * 20)
```

其中 `_keyword_search()` 的主要打分为：

```text
5 * keyword-index matches
+ 1 * content word frequency
+ 3 * phrase/bigram bonus
```

`_scan_all_nodes()` 是额外的全图文字扫描，和 keyword index 列表不同。

### 7.2 Lumina 的 adapted anchor path

Lumina 保留：

- TRG 的 keyword-enriched MiniLM dense search；
- 与 upstream keyword 公式等价的 lexical score；
- RRF 常数 60。

Lumina 改造：

- lexical 只扫描最多 `max_nodes` 个 projectable Event；
- lexical 列表最多 40；
- 只有 dense + lexical 两路，没有 `_scan_all_nodes()` 第三路；
- 最终 fused anchors 严格限制 `top_k`；
- tie-break 使用稳定的 `evidence_id,node_id`，不是 upstream 的 set/insertion 结果；
- lexical 失败时显式退到 dense-only，而不是让整个 Recall 失败。

这应归类为 **adapted reuse**，不是逐字复用，也不是完全绕过。

## 8. Traversal 算法：名字、公式与真实可达性

### 8.1 Active lower-level fixed traversal：FIFO BFS

`NetworkXGraphDB.traverse()` 是 FIFO BFS：

- 从 anchors 入队；
- 同时看 incoming/outgoing neighbors；
- 用 `TraversalConstraints` 过滤 edge type/subtype/confidence/status；
- 用 visited、`max_depth`、`max_nodes` 截断；
- 不累积 path score；
- 不用 edge weight 排序；
- 最终只返回前 10 条 traversal paths。

Lumina 生产实际走的就是这一类固定 BFS，而且 depth=1、nodes=20。

### 8.2 Active full QueryEngine traversal：semantic-gated FIFO traversal

`QueryEngine._adaptive_graph_traversal()` 虽然被描述为 adaptive BFS，但实际是：

1. anchors FIFO 入队；
2. 编码 query 和候选 node；
3. 要求绝对 cosine similarity 达到 query-type threshold；
4. 要求相对 parent similarity 下降不超过 0.15；
5. 要求 neighbor 与 query 有关键词交集；
6. 每节点最多取 8 或 10 个 neighbor；
7. 总编码上限 400、总节点上限 800；
8. 最后按 cosine similarity 排序。

它没有 beam priority queue，也没有累计 edge/path score。

另外，`_get_neighbors()` 中的 `prefer_link_types` 是 `if/elif` 硬过滤，而不是软偏好。对
`[SEMANTIC, CAUSAL, TEMPORAL]` 这样的 multi-hop 参数，代码会先命中 TEMPORAL 分支，
并不会同时融合三种边。ENTITY 也没有独立 active 分支。

因此，即使完整恢复当前 QueryEngine，也不能宣称获得“完整四维概率 beam traversal”。

### 8.3 Present but unreachable：probabilistic beam search

`_probabilistic_beam_search()` 存在，但没有 active caller。它定义了更接近论文描述的公式：

```text
anchor score = 1 / 61
transition = 0.6 * structural_score + 0.4 * cosine_similarity
cumulative = parent_score + transition
beam_width = 10
max_visited = 50
```

其中 attention 分布按 WHY / WHEN / ENTITY 改变，例如 WHEN 给 TEMPORAL 0.7，ENTITY 给 ENTITY 0.6、
SEMANTIC 0.3。由于它不可达，不能作为当前 upstream query 的实际行为证据。

### 8.4 Lumina 的 fixed-only boundary

冻结后的生产实现只保留固定有界遍历。曾评估的 optional beam 模块和 caller controls 已因无稳定净收益而删除；历史结果保留在
[`docs/MEMORY_EXPERIMENT_HISTORY.md`](MEMORY_EXPERIMENT_HISTORY.md)。

## 9. Upstream query-type reranking

完整 QueryEngine 的 `_rerank_and_filter()` 不是 BGE。它是一个大型启发式公式：

```text
S = w_keyword  * keyword_score
  + w_entity   * entity_score
  + w_temporal * temporal_score
  + w_phrase   * phrase_score
  + person_boost
  + 2.0 * speaker_score
  + 1.5 * context_bonus
  + session_score
  + dia_id_score
  + 10.0 * w_similarity * similarity_score
```

权重由 query type 决定。代码还包含英文人名、年份、speaker、session、dia ID 和 benchmark 文本启发式。
person/year hard filter 在候选不足时又会回退到未过滤全集。

multi-hop 查询另走 `_retrieve_multi_hop_evidence()`：按首字母大写实体分组，每个实体先取最多 3 个，再填充候选。
随后还有 QA context 和 session context expansion。

这些能力全部被 Lumina 当前生产链绕过，并被固定 BGE + Hindsight + bounded DTO 取代。

## 10. Lumina 当前生产 Recall 的逐步链路

### 10.1 Product entry

当前工作区 `core/main.py` 的实际值是：

- Recall source default：开启；
- `top_k=10`；
- `max_graph_depth=1`；
- `max_nodes=20`；
- `max_evidence_items=3`；
- `max_chars=5000`；
- `final_min_score=0.144`。

仓库根 `AGENTS.md` 与当前代码现在一致：Recall source default 开启，并使用 inclusive
`final_min_score=0.144`。

### 10.2 Retrieval

```text
MessageRuntime
-> MagmaMemoryAdapter.recall(query, fixed policy)
-> RealMagmaBackend.recall
-> _execute_fixed_recall
   -> TRG.query: enriched MiniLM dense anchors
   -> bounded lexical anchors
   -> dense + lexical RRF
   -> fixed graph_db.traverse from fused anchors
-> candidate projection + provenance validation
-> optional fail-open ControlledRelationResolver gate
   (only when a structured caller supplies relation_surfaces)
```

一个容易忽略的细节是：`TRG.query()` 内部先从 dense anchors 遍历一次；随后 Lumina 用 dense+lexical RRF 生成新 anchors，
又直接调用 `graph_db.traverse()`，并用第二次 traversal paths 替换第一次结果，同时清空 narrative。

### 10.3 BGE and final admission

非空候选进入：

1. 单例、懒加载固定 BGE reranker；
2. 对整批候选计算 raw logits；
3. 如果整个 batch 不在 `[0,1]`，对 logits 取 sigmoid；
4. 用最新来源时间作为可复现 `now` 做 Hindsight recency 修正；
5. 按 final score 降序稳定排序；
6. 使用 inclusive `final_score >= 0.144`；
7. 投影为 Lumina-owned `MemoryEvidence`；
8. 截断到 top 3、总渲染字符 5000。

Hindsight 当前有效的主要修正是：

```text
final = normalized_bge * (1 + 0.2 * (recency - 0.5))
```

其中 recency 在 365 天内线性衰减、最低 0.1；缺失时间取 0.5；future clamp 为 1。
temporal/proof 因子当前都是中性 0.5，因此乘数为 1。

### 10.4 Answer injection

`MessageRuntime` 把非空 `MemoryContext.rendered_text` 原样放入固定的 internal historical-evidence system block。
Recall 为空或安全失败时不添加该 block，正常对话继续。recent context 不被 Recall 改写，当前 query 保持最后一条 native user message。

## 11. 逐步对照表

| Stage | MAGMA local full QueryEngine | Lumina current production | Classification |
|---|---|---|---|
| Query analysis | `detect_query_type` active；按类型参数化 | 无自动类型识别或 query intent contract | **BYPASSED** |
| Query intent helper | WHY/WHEN/ENTITY helper 存在但不可达 | 无 query intent contract | upstream **DEAD**, production **NOT USED** |
| Query-time temporal parser | helper 存在但未接入 query | 无 query-time temporal-window contract | **NOT RESTORABLE AS ACTIVE PATH** |
| Dense anchor | enriched query + MiniLM/vector DB | 同一 TRG lower-level path | **REUSED** |
| Keyword anchor | 全 node index keyword search | bounded projectable Event lexical | **ADAPTED** |
| Full-scan anchor | `_scan_all_nodes` | 无 | **BYPASSED** |
| Fusion | 3-list RRF, k=60 | 2-list bounded deterministic RRF, k=60 | **ADAPTED** |
| Initial graph traversal | generic graph BFS | 调 TRG 后又从 fused anchors 重做 BFS | **REUSED + REPLACED INPUTS** |
| Adaptive traversal | active semantic-gated FIFO by query type | 固定 depth=1 BFS；无自有 adaptive branch | **BYPASSED** |
| Four edge dimensions | 约束上可见；active preference 实现不完整 | 可走实际存在边；causal 通常为空 | **PARTIALLY_USED** |
| Query-type rerank | 大型英文/benchmark 启发式公式 | 固定 BGE cross-encoder | **REPLACED** |
| Multi-hop grouping | capitalized entity grouping | 无专用 multi-hop；仅图 expansion | **BYPASSED** |
| QA/session expansion | active | 无对应逻辑 | **BYPASSED** |
| Upstream formatting | AnswerFormatter by query type | Lumina bounded MemoryContext renderer | **REPLACED** |
| Admission | 没有可靠 no-answer gate | BGE/Hindsight fixed floor 0.144 | **LUMINA-SPECIFIC** |
| Public boundary | benchmark nodes/context | strict Lumina DTO/provenance/count/chars | **LUMINA-SPECIFIC** |

## 12. 被绕过、被改造、被替换的能力

### 12.1 BYPASSED

- `QueryEngine.detect_query_type()`；
- `QueryEngine.get_adaptive_params()`；
- `_scan_all_nodes()` 第三路 anchors；
- active `_adaptive_graph_traversal()`；
- `_rerank_and_filter()`；
- `_retrieve_multi_hop_evidence()`；
- QA/session context expansion；
- upstream `AnswerFormatter`；
- upstream `MemoryBuilder` 的完整批量边构造。

### 12.2 ADAPTED

- upstream keyword 打分公式被改成 bounded、projectable lexical rank；
- upstream RRF 公式保留，但由三路变两路并加入稳定 tie-break；
- generic graph traversal 保留，但 anchors、预算、projectability 和 DTO 投影由 Lumina 控制；

### 12.3 REPLACED

- query-type heuristic reranker 被 BGE 替换；
- upstream context formatter 被 bounded `MemoryContext` 替换；
- upstream narrative 被丢弃；
- 结果选择被 Hindsight + `final_min_score=0.144` + top3/chars5000 替换。

## 13. 60 例 0.144 数据集结果与失败解释

来源结论已合并到
[`docs/MEMORY_EXPERIMENT_HISTORY.md`](MEMORY_EXPERIMENT_HISTORY.md)。这是合成开发/调参集，
不是 blinded holdout，也没有调用 Answer Model。

### 13.1 聚合结果

| Stratum | Correct | Total | Failed |
|---|---:|---:|---:|
| Ordinary positive | 13 | 18 | 5 |
| Temporal positive | 4 | 12 | 8 |
| Public closed-form negative | 12 | 12 | 0 |
| Missing-private-attribute negative | 4 | 9 | 5 |
| Wrong-entity/relation negative | 4 | 9 | 5 |
| Positive total | 17 | 30 | 13 |
| Negative total | 20 | 30 | 10 |
| Combined | 37 | 60 | 23 |

该数据集在 BGE 层的判定是：

- positive correct：所有 required source turns 都进入最终 evidence；
- negative correct：最终 evidence 为空；
- 没有检查 Answer 是否自然、是否拒答、是否泄露或是否能利用正确证据。

### 13.2 Temporal：8/12 失败

**机制关联：PARTIAL。**

可合理关联的差异：

- 生产所有查询都是 depth=1 固定 BFS；
- 没有 `detect_query_type=temporal`；
- 没有 temporal depth=5、TEMPORAL preference、query-type temporal weights；
- 图中 temporal 主要是相邻事件顺序边，没有 MemoryBuilder 的 24 小时 proximity 批量边；
- 最终 BGE+recency+0.144 仍可能删除旧/新配对中的一侧。

但不能写成“恢复 upstream temporal policy 就会修好 8 例”，原因是：

- upstream query-time `TemporalParser` 自身未接入；
- active upstream query type 是英文启发式，不能直接证明覆盖当前中文/混合查询；
- `_get_neighbors()` 的 edge preference 是硬过滤且有 `if/elif` 局限；
- upstream reranker 没有 Lumina 的 provenance、隐私和稳定性边界；
- 当前失败可能同时来自 anchors、图形成、BGE 和 admission。

### 13.3 Wrong entity/relation：5/9 失败

**机制关联：PARTIAL。**

完整 QueryEngine 的 entity/multi-hop routing、entity/group rerank、same-entity/session/QA expansion，确实是生产链没有的区分信号。
Lumina 生产图又只使用有限的 exact shared-entity 边；当前 deterministic entity extraction 对非拉丁实体或关系表达并不等于关系验证。

不过 upstream active path 也没有一个“查询实体必须与属性关系严格相符”的事实授权器。它的 entity type 主要偏向 semantic traversal，
multi-hop 又按首字母大写实体分组；这些 heuristic 可能改善候选，也可能扩大错误邻居。故只能判 PARTIAL，不能判直接修复。

### 13.4 Missing private attribute：5/9 失败

**机制关联：INCONCLUSIVE。**

这类负例要求判断“记忆中没有该实体的该私有属性”。dense、BFS、adaptive traversal 或更多 graph expansion 都是召回算法，
通常会提高返回邻近记忆的概率，而不是证明属性不存在。upstream QueryEngine 没有可靠的 evidence sufficiency / no-answer admission gate。

因此：

- production 0.144 失败说明单一全局 BGE score floor 不能可靠完成属性级 admission；
- 恢复 QueryEngine 可能改变排序，但没有源码证据能保证将这 5 例变为空；
- 不能把 missing-private failure 当作“MAGMA 原生能力被绕过”的直接证据。

## 14. 可直接复用的 upstream surface

以下“可复用”只表示源码已有，不表示本任务建议立刻接入。

### 14.1 Query type 与参数表

```text
CAPABILITY: Query type classification and per-type parameters
SOURCE FILE: Conversation_Memory/upstream/MAGMA/memory/query_engine.py
SYMBOL: QueryEngine.detect_query_type / get_adaptive_params
DEPENDENCIES: English regex/keywords, LinkType, benchmark query taxonomy
CURRENT LUMINA EQUIVALENT: fixed bounded traversal; no query intent contract
REUSE DIFFICULTY: MEDIUM
```

原因：调用面小，但类型规则偏英文且参数与 upstream 非 bounded QueryEngine 绑定；直接接入会改变生产公共检索合同。

### 14.2 Active semantic-gated traversal

```text
CAPABILITY: Query-type semantic-gated graph traversal
SOURCE FILE: Conversation_Memory/upstream/MAGMA/memory/query_engine.py
SYMBOL: QueryEngine._adaptive_graph_traversal / _get_neighbors
DEPENDENCIES: encoder, graph internals, KeywordEnricher, QueryEngine node objects
CURRENT LUMINA EQUIVALENT: fixed BFS; no Lumina adaptive branch
REUSE DIFFICULTY: HIGH
```

原因：依赖 upstream 私有 node/graph，默认预算高达 400 次编码/800 节点，不遵守 Lumina projectability；edge preference 还有硬过滤语义。

### 14.3 三路 anchors

```text
CAPABILITY: Dense + keyword-index + full-scan anchors with RRF
SOURCE FILE: Conversation_Memory/upstream/MAGMA/memory/query_engine.py
SYMBOL: _keyword_search / _scan_all_nodes / _rrf_fusion
DEPENDENCIES: node_index, full graph scan, original_text metadata
CURRENT LUMINA EQUIVALENT: dense + bounded lexical RRF
REUSE DIFFICULTY: MEDIUM
```

原因：公式可复用，但 full scan 与 Lumina 的 bounded/privacy 边界冲突，upstream set/tie 行为也不够确定。

### 14.4 Query-type heuristic reranker

```text
CAPABILITY: Query-type weighted heuristic reranking
SOURCE FILE: Conversation_Memory/upstream/MAGMA/memory/query_engine.py
SYMBOL: QueryEngine._rerank_and_filter
DEPENDENCIES: session map, dia IDs, speaker metadata, benchmark-specific English heuristics
CURRENT LUMINA EQUIVALENT: fixed BGE + Hindsight
REUSE DIFFICULTY: HIGH
```

原因：它不是通用关系真值模型，且包含大量 benchmark 假设；接入会与当前固定 BGE 边界形成第二评分系统。

### 14.5 Multi-hop、QA 与 session expansion

```text
CAPABILITY: Multi-hop evidence grouping and context expansion
SOURCE FILE: Conversation_Memory/upstream/MAGMA/memory/query_engine.py
SYMBOL: _retrieve_multi_hop_evidence / _expand_qa_context / _expand_session_context
DEPENDENCIES: capitalized-entity heuristics, QA/session links, session nodes/maps
CURRENT LUMINA EQUIVALENT: generic bounded graph expansion only
REUSE DIFFICULTY: HIGH
```

原因：Lumina 当前 ingestion 没有建立所需的完整 QA/session 图，启发式也不适合直接泛化到中文。

### 14.6 Query-time temporal helper

```text
CAPABILITY: Temporal phrase parsing and date-range helpers
SOURCE FILE: Conversation_Memory/upstream/MAGMA/memory/temporal_parser.py and query_engine.py
SYMBOL: TemporalParser.extract_time_constraints / QueryEngine date helpers
DEPENDENCIES: reference date, naive datetime assumptions, English temporal vocabulary
CURRENT LUMINA EQUIVALENT: none on the query side; ingestion temporal provenance remains
REUSE DIFFICULTY: HIGH
```

原因：helper 在 upstream query path 不可达，且不能直接满足 Lumina aware-UTC、source timezone 与中文解析合同。

### 14.7 Generic fixed graph traversal

```text
CAPABILITY: Constraint-bounded graph BFS
SOURCE FILE: Conversation_Memory/upstream/MAGMA/memory/graph_db.py
SYMBOL: NetworkXGraphDB.traverse
DEPENDENCIES: graph nodes/links and TraversalConstraints
CURRENT LUMINA EQUIVALENT: already used by RealMagmaBackend
REUSE DIFFICULTY: LOW
```

原因：这正是当前 Lumina 已经复用的稳定 lower-level surface。

### 14.8 Probabilistic beam helper

```text
CAPABILITY: Intent-weighted probabilistic beam search
SOURCE FILE: Conversation_Memory/upstream/MAGMA/memory/query_engine.py
SYMBOL: QueryEngine._probabilistic_beam_search
DEPENDENCIES: intent enum, encoder, edge types, private graph nodes
CURRENT LUMINA EQUIVALENT: none; rejected optional implementation removed
REUSE DIFFICULTY: HIGH
```

原因：它在当前 upstream 不可达。把它接入属于启用新算法，而不是“恢复 upstream 当前 active behavior”。

## 15. 不应从本审计推出的结论

本审计不能支持以下说法：

- “MAGMA 有四张图，Lumina 只用了其中一张”；
- “upstream 当前 Recall 使用概率 beam search”；
- “upstream 已经接好 query-time TemporalParser”；
- “启用完整 QueryEngine 就能解决 missing-private/no-answer”；
- “0.144 已证明 Answer 质量、真实用户泛化或 production correctness”；
- “BGE 是 admission model”；
- “增加 traversal depth 必然改善正确率”；
- “现有 temporal/wrong-relation 失败全部由绕过 QueryEngine 导致”。

## 16. 当前采用结论

后续实验已经完成并合并到
[`docs/MEMORY_EXPERIMENT_HISTORY.md`](MEMORY_EXPERIMENT_HISTORY.md)。当前采用的边界仍是 bounded
fixed BFS；rejected adaptive policy 和 query temporal window contract 已删除。关系兼容门只消费 caller-supplied
`relation_surfaces`，任一侧无法解析时 fail open；正常 Chat 不解析自然语言查询关系。

任何后续实现都必须继续保留：

- Lumina-owned DTO 和 provenance；
- bounded anchors/depth/nodes/evidence/chars；
- BGE failure fail-closed；
- BGE private scores 不外泄；
- Background prompt 隔离；
- pinned upstream 不修改。

## 17. 最终回答

```text
MAGMA_NATIVE_RECALL: PARTIAL
MAGMA_ADAPTIVE_QUERY_POLICY: NOT_USED
MAGMA_FOUR_GRAPH_TRAVERSAL: PARTIALLY_USED

LUMINA_REUSES:
- TRG ingestion and storage
- keyword-enriched MiniLM dense anchors
- vector backend
- generic graph traversal
- temporal/semantic/entity edge primitives that actually exist in its graph

LUMINA_ADAPTS:
- lexical retrieval
- RRF fusion
- bounded graph expansion and projectability

LUMINA_BYPASSES:
- QueryEngine query classification and adaptive parameters
- third full-scan anchor list
- active semantic-gated adaptive traversal
- query-type heuristic reranking
- multi-hop, QA and session expansion
- AnswerFormatter
- full MemoryBuilder graph construction

LUMINA_REPLACES:
- upstream final ranking with fixed BGE + Hindsight
- upstream result formatting with bounded MemoryContext
- upstream selection with inclusive final_min_score=0.144

TEMPORAL_FAILURE_LINK: PARTIAL
RELATION_FAILURE_LINK: PARTIAL
MISSING_ATTRIBUTE_FAILURE_LINK: INCONCLUSIVE
DIRECT_UPSTREAM_REUSE_POSSIBLE: PARTIAL
UPSTREAM_CLEAN: YES
UPSTREAM_LICENSE: MIT
```
