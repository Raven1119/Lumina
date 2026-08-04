# MAGMA Temporal Anchor Ranking 审计

## Result

**PASS — audited and deliberately not implemented**

## Decision

**B. 上游无可安全复用的通用实现，本阶段不接入 temporal ranking。**

固定上游的论文/代码意图包含 temporal signal，但当前检出的生产查询链中不存在一个能够脱离 LoCoMo、LongMemEval、session 常量和自动问题分类，并直接以 Lumina 的 aware event timestamp 与 `temporal_window=[start,end)` 为输入的独立 temporal anchor rank source。不得把已有的时间解析、时间边或 benchmark 后置重排拼装成一套新算法。

因此本次不修改生产代码、测试、RecallPolicy、DTO、持久化或上游 MAGMA。Lumina 继续使用现有的 dense + lexical RRF anchors，并保留 `temporal_window` 对 dense、lexical 和 graph expansion 的半开区间硬过滤。

## Audit identity and scope

- 固定上游目录：`Conversation_Memory/upstream/MAGMA`
- 固定提交：`467cb70b67ac337b22fdb42194d37c04ad701b62`
- 授权：MIT License，`Copyright (c) 2024 Anonymous Authors`
- 扫描边界：只读核对 `memory/query_engine.py`、`memory/temporal_parser.py`、`memory/trg_memory.py`、`memory/memory_builder.py`、`memory/graph_db.py` 及 temporal candidate/RRF 的直接定义和引用；没有重新扫描整个仓库，也没有运行 benchmark。
- 判定方法：逐项检查输入、输出、排序公式、默认值、RRF 接线、窗口语义、当前时间/session/benchmark 依赖、EventNode/provenance 兼容性、缺失数据结构和扫描上限。

## Upstream mapping

### 1. QueryEngine 的实际 RRF 链路

位置：

- `memory/query_engine.py:55`，`QueryEngine._rrf_fusion`
- `memory/query_engine.py:682`，`QueryEngine.query`
- `memory/query_engine.py:736-755`，实际 rank source 组装
- `memory/query_engine.py:878`，`_keyword_search`
- `memory/query_engine.py:970`，`_scan_all_nodes`

实际链路是：

```text
TRG vector anchors
+ keyword search
+ full-scan text matches
-> RRF(k=60)
```

它不是 `dense + lexical + temporal RRF`。`query()` 加入 `ranked_lists` 的三路分别是：

1. `trg.query()` 返回的向量 anchors；
2. `_keyword_search()` 返回的关键词结果；
3. `_scan_all_nodes()` 返回的全图文本扫描结果。

输入是查询文本和 `top_k`；查询类型会把三路的临时大小设置为 temporal 查询 `15/15/20`、multi-hop `30/30/40`、其他查询 `20/20/25`。输出是去重后的 `(node, rrf_score)` 序列。

RRF 公式为：

```text
RRF(node) = sum(1 / (60 + rank_i))
```

其中公开描述的 rank 从 1 开始；实现使用零基 `enumerate` 后计算 `1 / (k + rank + 1)`。相同 node ID 累加各路贡献，默认 `k=60`。最终只按总分降序排序；并列没有额外显式 node-ID 稳定键，而是依赖 Python 稳定排序和字典首次插入顺序。

与本任务有关的边界：

- 没有独立 temporal candidate list 进入 RRF。
- 没有读取 `temporal_window`，也没有使用区间重叠或窗口距离。
- 不使用当前时间；但后续 query-type 和 reranker 使用自动分类、session 和 benchmark 启发式。
- `_scan_all_nodes()` 在 `memory/query_engine.py:977` 先遍历 `graph_db.nodes.values()` 的全部节点，完成后才裁剪至 60；它不是受 Lumina `max_nodes` 约束的可复用 temporal source。
- 返回对象可以包含 EventNode，也可能包含 SessionNode 路由提示；这里没有 Lumina provenance 完整性检查。

结论：RRF 函数本身是通用的 rank fusion，但其当前调用没有 temporal rank source，不能据此声称上游已经实现 temporal anchor ranking。

### 2. 未接入查询链的 date helpers

位置：

- `memory/query_engine.py:472`，`extract_date_from_question`
- `memory/query_engine.py:504`，`find_nodes_by_date_range`
- `memory/query_engine.py:541`，`resolve_relative_temporal_reference`

审计结果：这些符号在固定上游中只有定义，没有被 `QueryEngine.query()` 或其他 Python 调用点使用。

| 符号 | 输入与输出 | 规则和默认值 | 依赖与边界 |
|---|---|---|---|
| `extract_date_from_question` | question -> `{year, month, day}` 或 `None` | 只识别英文月份日年、`MM/DD/YYYY` 和 20xx 年份正则 | 月份表/正则；不是排名；不进入 RRF |
| `find_nodes_by_date_range` | target date -> node 列表 | `days_range=2`，比较 `dates_mentioned[].parsed` 的日历日绝对差 | 扫描全部 graph nodes；没有 `max_nodes`；不检查 EventNode/provenance；不使用半开窗口；不进入 RRF |
| `resolve_relative_temporal_reference` | node + target date -> `0/10/15` | 同日绝对表达 10，相对词表达 15 | 依赖 `dates_mentioned.original/parsed` 和固定相对词；是孤立分值 helper；不进入 RRF |

这些 helper 不依赖系统当前时间，但要求查询中可被有限英文正则识别的日期，且要求节点已带 `dates_mentioned` 结构。Lumina ingestion 确实会保留相应 metadata，但这不能补上它们没有调用链、窗口语义、扫描上限、稳定并列规则和 provenance 过滤的缺口。

结论：不能把三个未接线 helper 自行组合成第三路 RRF；这样做会是 Lumina 自创 temporal ranking，而不是忠实复用上游实现。

### 3. QueryEngine benchmark heuristic reranker

位置：

- `memory/query_engine.py:117-190`，`_identify_target_sessions`
- `memory/query_engine.py:247-365`，`detect_query_type`
- `memory/query_engine.py:367-469`，`get_adaptive_params`
- `memory/query_engine.py:1282-1621`，`_rerank_and_filter`

这是 RRF 和 traversal 之后的候选重排，不是独立 anchor rank source。输入是已经召回的 nodes、原始 question、自动识别的 query type、`top_k` 和 scoring weights；输出是裁剪后的 EventNode 序列。

默认综合公式为：

```text
score = keyword_score * keyword_weight
      + entity_score * entity_weight
      + temporal_score * temporal_weight
      + phrase_score * phrase_weight
      + person_boost
      + speaker_score * 2
      + context_bonus * 1.5
      + session_score
      + dia_id_score
      + similarity_score * 10 * similarity_weight
```

`_rerank_and_filter()` 自身 fallback 权重是 keyword `4.0`、entity `2.5`、temporal `2.0`、phrase `5.0`、similarity `0.8`。`get_adaptive_params('temporal')` 则提供 keyword `2.0`、entity `3.0`、temporal `4.0`、phrase `3.5`、similarity `1.2`、date_exact `10.0`，并把 traversal depth 设置为 5、相似度阈值设为 0.25。

temporal_score 并不是基于查询窗口与事件时间的通用匹配函数。它组合了以下启发式：

- 节点只要有 timestamp 就加 3；
- duration、英文月份/年份、数字日期、weekday、时钟和 `yesterday/today/...` 文本正则加固定分；
- 查询中的月份和年份与节点文本同时出现时加分；
- temporal query 中的年份先作为文本硬过滤，但候选过少会退回未过滤 nodes。

同时，它依赖与 benchmark 绑定的结构和常量：

- 自动 `detect_query_type()`；
- `_identify_target_sessions()` 中 `may -> [1]`、`july -> [6,7,8,10]` 等硬编码月份/session 表，以及 `charity race`、`lgbtq`、`adoption` 等固定主题映射；
- `session_id`、`dia_id` 和问题中的 `D<n>:<n>`/session/ordinal 正则；
- `Mel/Melanie` 和固定人名等人物启发式；
- 为每个节点扫描全部 graph links 来计算 context bonus。

它不接收 Lumina `temporal_window`，不计算 `[start,end)`，不作为一条 rank list 进入 RRF，也不验证完整 provenance。把其中 temporal_score 单独拆出或改造为第三路候选将改变算法语义，并需要移除/重定义大量 benchmark 假设。

结论：这是 benchmark-oriented 的混合后置重排器，不满足通用 temporal anchor source 的判定标准。

### 4. TemporalParser

位置：

- `memory/temporal_parser.py:16`，`TemporalParser`
- `memory/temporal_parser.py:45`，`parse_session_timestamp`
- `memory/temporal_parser.py:90`，`extract_temporal_reference`
- `memory/temporal_parser.py:303`，`is_temporal_question`
- `memory/temporal_parser.py:323`，`extract_time_constraints`

它执行英文时间表达解析和归一化，不执行候选生成或排名，也没有任何输出进入 RRF。

- `parse_session_timestamp(date_str)` 输出 datetime；支持多种固定格式，其中包含代码明确标注的 LoCoMo 专用 `"<time> on <date>"` 格式。缺值或解析失败返回 `datetime.now()`，因此依赖当前时间且结果不是严格确定的 aware UTC 时间。
- `extract_temporal_reference(text, base_timestamp)` 输出单个 datetime 或 `None`；相对时间依赖调用者提供的 base timestamp。`last month`/`last year` 分别按固定 30/365 天处理，不等同于 Lumina 的真实日历边界语义。
- `is_temporal_question(question)` 是关键词分类器；本任务禁止引入自动 query classifier。
- `extract_time_constraints(query, reference_date)` 返回 `start_date/end_date` 字典；它在固定上游只有定义、没有调用点。`yesterday` 会得到相同起止时刻，不是 Lumina 所需的半开日历区间。

MemoryBuilder 在 `memory/memory_builder.py:270` 用 `extract_temporal_reference()` 生成 `dates_mentioned`，并在 `memory/memory_builder.py:984` 用 `parse_session_timestamp(session.date_time)` 处理数据集 session。该链依赖 session/date 字符串和 build-time metadata，不产生有界 temporal anchors。

结论：TemporalParser 是解析工具，不是可接入 RRF 的 ranking implementation。

### 5. TRG temporal links

位置：

- `memory/trg_memory.py:234`，`TemporalResonanceGraphMemory.query`
- `memory/trg_memory.py:256-285`，vector anchors 和 traversal
- `memory/trg_memory.py:387`，`_create_temporal_links`

`TRG.query()` 对 enriched query 做 embedding/vector search，获得最多 `max_results` 个 anchors，然后从这些 anchors 做受 `TraversalConstraints` 限制的图遍历。temporal links 参与 traversal，但不形成独立 temporal-ranked anchor list。

`_create_temporal_links(event_node)` 会读取并按 timestamp 排序当前全部 graph nodes，把新事件和前一节点建立 PRECEDES/SUCCEEDS 双向时间边；边属性记录秒级 `time_delta`。输入是 EventNode，输出是图边，没有 ranking score、top_k、temporal_window 或 RRF 接线。创建时会扫描/排序全部节点，也不做 Lumina provenance 校验。

EventNode 在 `memory/graph_db.py:65-97` 确实有 `timestamp`、`content_narrative`、`attributes`，并用 ISO 格式持久化 timestamp。Lumina 可以把 evidence ID 和 provenance 放进 `attributes`，但 upstream temporal-link 方法本身并不知道哪些 provenance 字段是必需的。

结论：时间边是图结构与 traversal signal，不是 temporal anchor ranking。

### 6. MemoryBuilder temporal and proximity links

位置：

- `memory/memory_builder.py:539`，`create_temporal_links`
- `memory/memory_builder.py:765`，`create_temporal_proximity_links`
- `memory/memory_builder.py:961-999`，dataset/session build path

`create_temporal_links(nodes)` 假定输入 node ID 已按时间顺序排列，在相邻节点之间建立 PRECEDES/SUCCEEDS；它不读取 query，也不返回 ranked candidates。

`create_temporal_proximity_links(nodes, max_time_diff_hours=24)` 最多向前查看 9 个列表位置。若两个带 timestamp 的节点相差不超过 24 小时，则建立 `TEMPORALLY_CLOSE` 边，并写入：

```text
time_diff_hours = abs(next.timestamp - current.timestamp) / 3600
weight = 1 / (1 + time_diff_hours)
```

这看似包含通用时间距离公式，但它是 build-time link weight，不是 query-time temporal ranking：

- 输入是 MemoryBuilder 的 node ID 列表，而不是 query temporal constraint；
- 输出是图边，不是 rank list；
- 没有进入 RRF；
- 不使用 `temporal_window`；
- 候选只受列表中“向前 9 个”隐式限制，不受 Recall `max_nodes/top_k` 控制；
- build path 依赖 dataset `sample.conversation.sessions`、session 顺序、`session.date_time`，并可构建 SessionNode/EpisodeNode；Lumina 当前生产 ingestion 不运行这套 MemoryBuilder 数据集流程。

把此边权转换成 query-time recency、窗口中心距离或 temporal RRF source，会是任务明确禁止的自创算法。

### 7. TraversalConstraints.time_window

位置：

- `memory/graph_db.py:259-300`，`TraversalConstraints` 与 `allows_link`

`time_window` 在 dataclass 中声明为可选 `(datetime, datetime)`，但审计没有发现 `allows_link()`、NetworkX traversal 或其他固定上游 Python 路径读取该字段。`allows_link()` 实际只检查 link type/subtype、confidence、status 和 follow flags。

因此不能把这个字段描述为 upstream 已实现的事件时间过滤，更不能据此推导出 temporal ranking。Lumina 当前的时间硬过滤由 Lumina-owned adapter 明确执行，而不是依赖此上游声明。

## Generality assessment

| 能力 | 通用部分 | benchmark-specific/不适配部分 | 是否适合直接接入 Lumina |
|---|---|---|---|
| RRF | `k=60`、按 rank 融合、node ID 去重 | 当前第三路是 full-scan text，不是 temporal；并列无显式稳定键 | RRF 已由 Lumina 现有 anchor fusion 复用；没有 temporal source 可新增 |
| Date helpers | 可从部分日期字符串读取日期；可比较日历日 | 无调用点；英文正则；全图无界扫描；不使用窗口/provenance | 否 |
| Heuristic reranker | 能混合关键词、实体、时间文本和相似度 | 自动分类、月份/session/人物/dia_id 常量、后置混合原始分值 | 否 |
| TemporalParser | 基于 base timestamp 的部分英文时间归一化 | LoCoMo session 格式、`datetime.now()` fallback、30/365 天近似、非 rank source | 否 |
| TRG temporal links | EventNode timestamp 和 PRECEDES/SUCCEEDS 关系 | 图结构而非 anchor rank；创建时全节点排序 | 否 |
| Proximity links | `1/(1+hours)` 的 build-time 边权 | dataset/session builder、列表邻近、无 query/window/RRF | 否 |
| `TraversalConstraints.time_window` | 类型上声明了窗口 | 未发现执行路径消费该字段 | 否；不能算已实现能力 |

Lumina 当前已经具备的 compatible inputs 是 aware EventNode timestamp、明确的半开 `temporal_window`、EventNode 类型检查和 provenance metadata。上游候选实现仍缺少至少一项关键结构或语义：独立 query-time temporal rank list、半开窗口过滤、受 `max_nodes/top_k` 约束的候选生成、稳定并列键、完整 provenance 过滤，以及无需 session/episode/benchmark metadata 的调用路径。

结论并非“时间信息完全不可用”，而是“没有满足本任务全部通用性条件的 temporal anchor ranking 实现”。

## Production impact

- Production diff：none。
- Test diff：none。
- Public surface：none。
- New abstractions：none。
- 默认 Recall 行为：不变，仍为 dense + bounded lexical -> RRF -> top_k fused anchors -> existing traversal。
- `temporal_window`：现有硬过滤保持不变。Lumina 在 `_recall_execution.py` 中以 UTC 比较 `start <= timestamp < end`，过滤 dense anchors，并把同一窗口应用到 bounded lexical candidates；`backend.py` 还会过滤 graph expansion events。
- 上游 `TraversalConstraints.time_window` 会继续被传入以保持接口一致，但报告不声称上游真正执行该字段；正确性由 Lumina-owned 显式过滤保证。

本次没有新增 temporal score，也没有让窗口外事件凭任何分数重新进入候选。

## Limitations

- 当前窗口内 events 之间不会根据时间匹配程度重新排序。
- 当前没有第三路 temporal RRF anchors。
- 当前不能使用 upstream date helpers 为 dense/lexical 未命中的节点补回 temporal candidates。
- 时间边可以参与现有图遍历，但这不等于 temporal anchor ranking。
- `TraversalConstraints.time_window` 在固定上游仅有声明，未发现生效实现。
- 上游 query_engine 的 scan fallback 和部分时间边构建会扫描全部节点；它们不满足本任务的 `max_nodes` 硬上限要求。
- 上游 TemporalParser 和 heuristic reranker 包含 current-time fallback、自动分类、session/dia_id/month/person regex 或 benchmark metadata，不能作为通用实现接入。
- 本审计是静态、窄范围代码审计，不是对论文结果的复现，也不评价 LoCoMo/LongMemEval 分数。
- 没有轻量 A/B 评测：Decision B 下没有合格实现可比较，构造自定义 temporal source 再评测会违反唯一算法来源约束。

## Deliberately omitted

本阶段刻意不实现：

- 自定义时间距离、衰减、recency、区间中心距离或区间重叠算法；
- 将 `TEMPORALLY_CLOSE` 边权改造成 temporal RRF source；
- 自动时间解析或 intent/query-type 路由；
- Recall scheduler、动态 anchor source 或新关系权重；
- WHY causal 特化、causal edge 写入或 Context Linearization 修改；
- Evidence Organizer、无答案拒绝或 LLM Judge；
- 时间索引、新数据库、参数搜索或上游 MAGMA 修改。

如果未来要增加 temporal anchor ranking，必须有新的明确授权和可引用的算法来源；不能以本报告列出的孤立 helper、时间边或 benchmark 启发式为由自行发明生产排序公式。

