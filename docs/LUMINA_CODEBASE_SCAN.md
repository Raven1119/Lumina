# Lumina Codebase Scan

> 扫描日期：2026-08-05
>
> 范围：当前工作树中的 Lumina-owned 代码、测试、前端、脚本与指定文档；上游 MAGMA 仅核对固定版本、许可证、工作树状态和 Lumina 实际调用入口。
>
> 证据标记：**代码事实**来自当前文件；**测试事实**来自测试实现或本报告末尾的实际运行；**推断**会明确标注。未读取或引用 `data/` 中的真实内容，也未读取 `.env.local`。

## Executive Summary

Lumina 当前不是只有 Draft 的早期 MVP，而是一个本地、单用户、单连续会话、同步运行的对话系统。已提交代码形成了这条闭环：

```text
Browser
-> FastAPI / MessageRuntime / ModelClient
-> Hot Draft rolling summary + recent raw turns
-> Cold-first per-turn Cold archive
-> explicit Dream
-> shared Conversation Memory / MAGMA backend
-> optional bounded Recall
-> model request

Cold raw + Hot recent raw
-> read-only single-conversation History API
-> initial browser restore + upward lazy loading
```

主要结论：

- **已完成**：固定聊天背景、Mock/MiniMax 模型、滚动语义 Hot Draft、逐 turn Cold Draft、显式浏览器 Dream、同进程共享记忆 backend、进程内 Chat/Dream 写互斥、持久化历史、稠密+词法 RRF anchors、有界固定/自适应图遍历、时间硬过滤、intent-aware Context Linearization、重启恢复与真实 MAGMA 合成 E2E。
- **部分支持**：跨轮指代依赖向量/词法命中、全图时间邻接、图扩展和回答模型推理；没有显式 coreference resolution。知识更新能共同召回新旧证据，但没有事实 supersession、冲突整理或当前状态判定。无答案查询仍可能返回满额证据。
- **未完成**：自动 Dream、Recall scheduler、自动 intent/query 分类、none/light/deep 调度、证据充分性升级、Evidence Organizer/Ledger、冲突与时间线整理、多会话、多用户、多 worker、跨进程事务和生产级数据库事务。
- 当前最严重的文档漂移在 `AGENTS.md`、`docs/final_goal.md`、`Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md` 和 `Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md`：它们仍包含 Dream UI 实现前、Hot 物理重写前或 Recall graph-expansion/RRF 实现前的事实。
- 建议冻结的是 Conversation Memory 的 **v1 所有权、DTO、provenance、Cold-first 和有界 facade 边界**，不是宣称检索质量已经完成。下一开发边界可以转向独立的 Mind System 设计，由其调用现有 `MemoryRetriever` 做调度与证据理解；本报告不实现该未来层。

## Repository Baseline

### Git 基线

- 当前分支：`main`。
- 扫描开始时工作树不干净，已有且由用户拥有的改动为：
  - `M Lumina_Canvas/.obsidian/workspace.json`；
  - `?? docs/CROSS_TURN_REFERENCE_AUDIT.md`；
  - `?? scripts/cross_turn_reference_probe.py`。
- 其中 Canvas 是已跟踪但未提交的工作树修改；跨轮审计报告和探针是未跟踪文件。因此本报告的生产架构结论来自已提交代码，而“Cross-Turn Context”中的探针数值属于工作树证据，尚不是提交基线。
- 本任务只新增本文件；没有覆盖或清理上述改动。

最近十个提交为：

| Commit | 含义 |
| --- | --- |
| `5f06b3a` | 共享 Hot store 接线测试；同一提交还加入 History API、前端历史加载及其测试 |
| `79b193a` | 固定聊天背景注入 |
| `25c2f53` | rolling semantic Hot compaction、行级 Cold、前端压缩提示等大改 |
| `b352342` | 手动 Dream UI 里程碑文档对齐 |
| `0217a34` | MAGMA temporal anchor ranking 审计 |
| `8b6487d` | GENERAL 关系权重消融 |
| `12c6d31` | intent-aware Context Linearization |
| `3a35437` | adaptive MAGMA graph traversal |
| `f213c28` | dense + lexical RRF anchors |
| `e3a6adb` | Recall temporal window 硬约束 |

### 固定 MAGMA 基线

- 固定来源：`Conversation_Memory/MAGMA_COMMIT.txt`。
- 仓库：`https://github.com/FredJiang0324/MAGMA.git`。
- 标记与实际 HEAD 均为 `467cb70b67ac337b22fdb42194d37c04ad701b62`。
- `Conversation_Memory/upstream/MAGMA/LICENSE` 为 MIT License，Copyright 2024 Anonymous Authors。
- 扫描时上游 `git status --short` 和 `git diff --stat` 均为空。
- Lumina 生产代码只在 `Conversation_Memory/adapter/backend.py:59-71` 导入上游 `EventNode`、`NodeType`、`TraversalConstraints` 和 `TemporalResonanceGraphMemory`。它不运行上游 `MemoryBuilder`、`QueryEngine`、answer formatter、LLM Judge 或 benchmark 主链。

## Runtime Architecture

### 启动顺序

`core/main.py:171-379` 的 `create_app()` 是唯一应用组合根：

```text
create_app()
-> _load_chat_background(prompts/chat_background.md)
-> load_env_file(.env.local, no override)
-> build_model_client_from_env()
-> decide Recall enabled flag and RecallPolicy
-> build one MagmaMemoryAdapter / RealMagmaBackend when possible
-> build one JsonlDraftStore + one ColdDraftStore
-> build one HotDraftCompactor
-> build one DreamRunner over the same Cold store and memory adapter
-> build one MessageRuntime over the same Hot store and compactor
-> attach objects to app.state
-> register four API routes
-> mount edge/static
```

启动时的 app-owned 对象在 `core/main.py:272-279`：

- `app.state.message_runtime`：唯一 `MessageRuntime`；
- `app.state.hot_draft_store`：Chat、`DraftContextProvider`、`HotDraftCompactor` 和 History 共用的唯一 Hot store；
- `app.state.cold_draft_store`：Compactor、Dream、status 和 History 共用的唯一 Cold owner；
- `app.state.dream_runner`：若共享 memory adapter 同时支持 `ingest` 且版本为 `dream-v1`，则构造一次；
- `app.state.writer_lock`：Chat 与 Dream 共用的一个 `threading.Lock`；
- `app.state.hot_draft_compactor`：唯一 compactor；
- Chat Recall 与 in-app Dream 共享同一个 `MagmaMemoryAdapter`，所以 Dream 写入成功后常驻 retriever 无需重启即可看到新记忆。

### 配置与降级

- `prompts/chat_background.md` 在 `.env.local` 之前读取，路径不可配置；缺失、不可读或空内容使 `create_app()` 直接失败（`core/main.py:142-150,188`）。修改后必须重启。
- 模型配置在启动时读取。只有完整且受支持的 MiniMax Anthropic-compatible 配置才构造真实客户端；否则安全退回 `MockModelClient`（`core/model_client.py:205-228`）。
- Recall 由 `LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED` 显式启用，默认关闭。该值和默认 `RecallPolicy()` 在启动时固定，变更需重启。
- 应用即使未启用 Chat Recall，也会尝试建立共享 memory adapter，以便 Dream 可用。MAGMA 初始化失败时 `effective_memory=None`、Dream 变为 unavailable、Recall 调用退化为空，但普通 Chat 仍能启动（`core/main.py:205-237`）。
- 默认 Hot 路径来自 `LUMINA_DRAFT_STORE_PATH`，缺省为 `data/draft/hot_drafts.jsonl`；Cold 和 compaction state 默认放在同一目录。`.env.example` 未列出 Recall、默认时区和 Dream/MAGMA 路径覆盖，代码仍支持这些变量。

## Chat Context Assembly

`POST /api/chat` 的真实链路位于 `core/main.py:312-328` 与 `core/message_runtime.py:65-207`：

```text
request
-> nonblocking app writer lock
-> read Hot summary + recent Hot raw
-> optional Recall(current user text)
-> ModelClient.generate(...)
-> persist user turn
-> persist assistant/fallback turn
-> optional Hot compaction
-> ChatResponse
-> release lock
```

模型看到的概念顺序是：

```text
chat_background system prompt
-> rolling Hot summary
-> recent Hot raw user/assistant turns
-> optional MAGMA Recall rendered_text block
-> current user message
```

MiniMax 的原生请求映射更精确地是（`core/model_client.py:95-118,180-195`）：

- provider `system` 字段：完整 `chat_background`，随后是 Hot rolling summary；
- provider `messages`：recent raw turns，随后是以临时 `user` 消息承载的有界 Recall block，最后是 current user；
- `summary` 伪角色不会作为普通 message 发送，而会被提升到 system 字段；
- 当前 user 在生成前不会先写入 Hot，也不会重复出现在 recent context。

持久化边界：

- `chat_background` 不进入 Hot、Cold、Dream、MAGMA、Recall query 或日志；
- Recall block 只属于本次模型输入，不写入 Draft；
- `/api/history` 的完整历史不进入模型上下文；
- Hot rolling summary 本身持久化为 Hot `summary` record，但不是原始 conversation turn，也不会进入 Cold 或 History；
- 只有本次 user 与 assistant/fallback 两个 raw turns 被写入 Hot。两次 append 不是一项成对事务；任一次 Draft 写失败都被安全吞掉，API 仍可返回模型结果。

## Hot Draft

### 路径与 schema

默认文件是 `data/draft/hot_drafts.jsonl`（`core/main.py:123-127`）。`JsonlDraftStore` 在 `core/draft_store.py:45-183` 同时管理：

- 零或一条 `record_type="summary"`：`content`、`generation`、`source_turn_count`、UTC `updated_at`；
- recent raw turn 行：V2 `turn_id`、`role`、`text`、UTC `created_at`、IANA `source_timezone`、`timezone_source`，另有 `schema_version=2`、`source="chat_draft"`、`safe=true`。

Raw append 使用普通 JSONL append，并按 `turn_id` 做幂等/冲突检查；它不是 fsync 事务。发生压缩时，store 才通过同目录临时文件、flush、fsync 和 `os.replace` 原子替换整个 Hot 文件（`core/draft_store.py:87-122`）。

### 24/12 rolling 规则

默认 `max_raw_turns_before_compression=24`、`retain_recent_raw_turns=12`（`core/hot_draft_compactor.py:30-47`）：

- `raw_count <= 24`：不压缩；
- `raw_count > 24`：只移动完整连续的 `user -> assistant` 对；
- 25 条 raw 时归档 12 条、保留 13 条；26 条时归档 14 条、保留 12 条；
- 第二次及后续摘要输入严格是“旧 rolling summary + 本轮移出的 raw turns”；不会重新发送更早 raw、recent tail、Recall、背景、当前未参与压缩的 turn 或 MAGMA 数据；
- summarizer 复用当前 `ModelClient.summarize_hot_draft()`。真实 MiniMax 走独立摘要 system prompt；默认 Mock 明确拒绝摘要，因此 Mock 会在触发点返回 compaction `failed`，不推进 Cold/Hot。

### 事务顺序与恢复

`HotDraftCompactor.maybe_compact()` 的顺序（`core/hot_draft_compactor.py:53-126`）是：

1. 生成新摘要；失败则 Hot、Cold 均不变。
2. 以本轮完整 raw turns 的稳定 hash 构造 segment ID，并先原子追加 Cold segment。
3. 原子替换 Hot 为“一条新 summary + recent raw tail”。
4. 最后原子写 `hot_draft_compaction_state.json`。

如果 Cold 成功而 Hot 替换失败，重试会使用相同 segment ID，Cold append 幂等，不产生第二个逻辑 segment。如果 state 写失败，Cold 与 Hot 已完成，结果仍为 `completed`；state 只是恢复/诊断元数据，当前生产读取路径不依赖它。

前端在 Chat 请求进行期间每 500 ms 查询 status；当 `compactor.is_running` 为真时显示“Hot Draft 正在压缩，请稍候……”，完成后根据 Chat 响应显示归档数并立即刷新 pending（`edge/static/app.js:293-341,418-438`）。

当前代码中已经不存在：

- `compressed_until_count` 生产字段；
- preservation marker；
- 物理 append-only Hot；
- `chat_drafts.jsonl` 文件名。

`source="chat_draft"` 仍是 raw record 的来源标签，不是旧文件路径。

## Cold Draft

`core/cold_draft_store.py:62-509` 是 Cold 的唯一 owner。默认路径为 `data/draft/cold_drafts.jsonl`。

每个原始 turn 独占一行 `record_type="cold_turn"`，包含：

- turn 原文和 V2 provenance；
- `segment_id`；
- 从 0 连续递增的 `segment_turn_index`；
- 全段一致的 `segment_turn_count`；
- `schema_version`、`segment_created_at`、`source`；
- `state="pending_digest"`，消费后变为 `state="consumed"` 并增加全段一致的 `consumed_at`。

语义边界：

- `append_segment()` 先验证完整 segment，再把旧文件与所有新 turn 行写入临时文件并 fsync/replace；因此一次 segment append 是整段原子的，不是逐行可见。
- `list_pending()`、`count_pending_bounded()` 和 Dream 都按**完整 segment**工作；14 行是 1 个 pending job。
- `mark_consumed(segment_id)` 原子重写文件中该 segment 的每一行，只改变 state metadata；原始 turn、顺序、时间和 provenance 不变。重复消费幂等。
- 缺行、重复 index、count/state/source/timestamp 冲突或其他不完整 group 不会被列出、计数或消费；旧嵌套 `turns[]` 物理格式不兼容且不自动迁移。
- Cold 保存 pending 与 consumed 的完整 raw evidence。History 会读取二者；Dream 只读取 pending。
- Cold 不直接进入模型上下文，也不在 Recall 时扫描。只有 Dream 派生出的 MAGMA memory 经 Recall 后，才可能以有界文本进入模型。

## Dream

当前 Dream 同时有浏览器/API 入口和停服 CLI 入口；它不是 CLI-only。

浏览器链路（`core/main.py:330-369`、`Dream/runner.py:45-83`、`Dream/cold_draft_digest.py:200-303`）：

```text
Run Dream button
-> POST /api/dream/run (no strategy body)
-> shared nonblocking writer lock
-> DreamRunner.run_once(DreamRunPolicy())
-> ColdDraftStore.list_pending(limit=10)
-> reconstruct complete segments
-> ColdDraftSegmentConverter
-> shared MagmaMemoryAdapter.ingest
-> graph/vector persistence
-> completed ingestion checkpoint
-> ColdDraftStore.mark_consumed(segment_id)
-> safe aggregate response
-> frontend refreshes pending/result
```

默认 policy 是 `max_segments=10`、`stop_on_error=False`、`ingestion_version="dream-v1"`（`Dream/models.py:14-32`），HTTP 客户端不能覆盖。执行严格串行，单段失败默认不阻断后续段。

关键保证：

- 只有 memory result 已 `completed`、segment/version 匹配且 memory ID 数量与 source turns 相等，才请求 Cold owner 消费。
- memory 已完成但 Cold 状态转换失败时，下一次相同 key 返回 `already_ingested`，只重试 consumed transition。
- Dream/Chat 共享同一进程 memory adapter/backend；成功写入对已启用 Chat Recall 立即可见。
- Chat 和 Dream 使用同一个非阻塞锁：冲突请求返回 409；锁覆盖完整 Chat 或完整 Dream，并在异常路径释放。
- Dream 初始化不可用返回 503；未捕获运行异常对外为安全 500；响应只含 aggregate counts。
- Dream 不在启动、Chat、Recall、定时器或后台自动运行。外部 `python -m Dream.runner` 使用独立 backend owner，只允许服务停止时运行。

## Conversation Memory and Recall

### 写入与持久化

`MagmaMemoryAdapter.ingest()` 位于 `Conversation_Memory/adapter/magma_adapter.py:89-152`。每个 Cold turn 成为一个 MAGMA event：

```text
validate ColdDraftSegment
-> checkpoint pending / in_progress
-> for each turn:
   normalize temporal mentions from that turn timestamp/timezone
   + deterministic entity fallback
   + stable evidence ID and provenance
   -> RealMagmaBackend.add_event / TRG.add_event
   -> persist graph + vectors
   -> checkpoint current memory IDs
-> create entity relationships
-> persist graph + vectors again
-> checkpoint completed
```

事件保留原文、原始 event timestamp、role、entities、normalized temporal metadata 和完整 source provenance。内部 MAGMA UUID 只是 backend handle；公开 evidence ID 是 segment/turn/version 的稳定 SHA-256。

### RecallPolicy

`Conversation_Memory/adapter/models.py:64-136` 的当前字段：

| 字段 | 默认值 | 当前语义 |
| --- | ---: | --- |
| `top_k` | 5 | dense/lexical RRF 最终最多产生的 fused anchors 数 |
| `max_chars` | 2000 | `MemoryContext.rendered_text` 最大字符数 |
| `max_evidence_items` | 5 | anchors + graph expansions 的公开 evidence 总上限 |
| `max_graph_depth` | 5 | 图扩展深度；0 合法且表示 anchor-only |
| `max_nodes` | 100 | 词法扫描、遍历与内部候选的硬预算之一 |
| `intent` | `None` | 调用者提供的 `GENERAL/WHY/WHEN/ENTITY`；系统不自动分类 |
| `temporal_window` | `None` | aware datetime 半开区间 `[start,end)` 的硬过滤 |
| `beam_width` | `None` | 可选 adaptive beam 宽度 |
| `drop_threshold` | `None` | 可选 adaptive 剪枝阈值 `[0,1]` |

### 检索链路

当前生产实现（`Conversation_Memory/adapter/_recall_execution.py:48-153`、`backend.py:106-296`、`magma_adapter.py:154-192`）：

```text
query
-> MiniLM dense anchors (bounded)
+ bounded lexical event scan/rank
-> RRF(k=60), stable fused top_k anchors
-> temporal_window hard filtering
-> fixed traversal OR caller-opted adaptive traversal
-> valid event-only graph expansion projection
-> anchor-first BackendCandidates
-> provenance/evidence validation
-> max_evidence_items
-> intent-aware max_chars rendering
-> MemoryContext
```

- 词法检索只扫描前 `max_nodes` 个 graph entries；按小写 token、部分词和 bigram 做简单确定性打分。词法路径失败会退回 dense-only。
- `temporal_window` 对 dense、lexical 和 graph expansion 都由 Lumina-owned 代码按 aware UTC 的 `start <= timestamp < end` 执行。窗口也传入上游 constraints，但不依赖上游真正消费它。
- 默认所有 optional 字段为 `None`，因此默认路径是固定 traversal；当前 `/api/chat` 通过环境启用 Recall 时也使用该默认 policy。
- `intent`、`beam_width` 或 `drop_threshold` 任一个非 `None` 才启用 adaptive traversal；单独提供 `temporal_window` 不会启用 adaptive。Adaptive 失败会回退 fixed；fixed 也失败则 facade 返回安全空结果。
- Adaptive 综合 `0.6 * relation_weight + 0.4 * cosine_similarity`。GENERAL/ENTITY 使用 `ENTITY=.60, SEMANTIC=.30, TEMPORAL=.05, CAUSAL=.05`；WHEN 使用 `TEMPORAL=.70`、其余各 `.10`；WHY 当前映射 GENERAL，没有 causal 特化。
- 固定 traversal 从 `traversal_paths` 提取非-anchor event；adaptive 使用私有 expansion list。两者只公开有完整文本、aware 时间、稳定 evidence ID 和 provenance 的 event node。实体 node、路径、边、统计、embedding、score、UUID 和 `narrative_context` 均不公开。
- Anchors 始终优先；固定 expansion 以最小 hop 和稳定键去重排序。内部 expansion ordering score 只为保持顺序，不是相关性分数，不进入 DTO。
- `max_evidence_items` 是最终公开总量边界；`top_k` 只限制 anchors。

### Context Linearization

`Conversation_Memory/recall/rendering.py:26-81`：

- `intent=None` 保持检索顺序并渲染纯 evidence text，以兼容旧行为；
- 任意显式 intent 都给每行增加标准化 UTC timestamp；
- `WHEN` 在先取 `max_evidence_items` 后按 timestamp/evidence ID 稳定升序；
- `GENERAL`、`ENTITY`、`WHY` 保持 retrieval order；WHY 不生成关系解释；
- 字符截断不会把内部 score/path/metadata 带入公开文本。

### 安全与能力边界

- 无结果是合法空 `MemoryContext`；backend/投影失败返回空 context + `recall_unavailable`。Chat 将两者都视为普通无 Recall 对话。
- 公开 `MemoryEvidence` 只含 evidence ID、text、timestamp 和 `SourceProvenance`。`MemoryContext` 只含 query、evidence、rendered text、truncated 和 safe error code。
- 当前没有 relevance threshold、cosine gate、cross-encoder、LLM Judge 或 abstention。现有 no-answer 合成控制在 depth 和 GENERAL 评测中都返回了 evidence，因此“有界返回”不等于“知道没有答案”。
- 已验证的 Recall 能力主要来自 unit/integration tests、48-turn/20-question 合成 depth 评测、GENERAL 权重消融和真实 MAGMA 合成 E2E；没有真实私有长期记忆语料上的检索质量或最终回答质量结论。

## Cross-Turn Context

**代码事实**：

- `MagmaMemoryAdapter.ingest()` 按 `segment.turns` 逐条调用一次 `add_event()`，所以一条 conversation turn 对应一个 MAGMA event。
- 上游 TRG 在同一 backend 全图上建立 temporal adjacency；该邻接不是 segment-aware，因此相邻时间事件可跨 Cold segment 建边。
- Dense/lexical anchors 加 fixed/adaptive graph expansion 可以共同召回前件与含指代表达的后件。
- 代码中没有 coreference parser、代词到实体的显式链接、指代专用 edge 或 segment context link 接线。

**工作树实验事实**：未跟踪的 `scripts/cross_turn_reference_probe.py` 和 `docs/CROSS_TURN_REFERENCE_AUDIT.md` 使用 7 个 synthetic segments、13 turns、6 个场景。报告记录 fixed depth 1 为 6/6 antecedent/anaphor co-retrieval、5/6 正确先后；Adaptive GENERAL 为 5/6 co-retrieval、3/6 正确先后。它没有运行回答模型，不能证明答案理解。

准确产品表述应是：**跨轮指代部分支持**。系统经常能通过语义 anchor 和时间图邻接把相关 turns 一起交给模型，但不执行显式指代消解；adaptive 剪枝和 evidence order 仍可能丢掉前件或反转先后。该结论在提交或正式采用前仍需审阅当前未跟踪审计产物。

## Single-Conversation History

`GET /api/history` 位于 `core/main.py:48-109,301-310`，只服务一个连续会话：

```text
read Hot recent raw first
+ read all Cold raw (pending + consumed)
-> project native V2 turns only
-> concatenate Cold then Hot
-> stable turn_id dedup (Cold wins overlap)
-> cursor page
-> browser initial restore / prepend older page
```

实现细节：

- Hot 先读是为了在 Cold-first compaction 并发窗口中最多看到重复、不出现缺口；合并时 Cold 放前并赢得同 ID 去重。
- 排序不是每次按 timestamp 重排，而是依赖 Cold JSONL segment/turn 顺序加 Hot raw 顺序维持 chronology。
- rolling summary、Cold state、segment ID、timezone、source、metadata 都不进入响应；无完整 native provenance 的 legacy turn 被跳过。
- 默认 `limit=40`，范围 1..100。`before` 是排他 turn-ID cursor；下一页 cursor 是当前页第一项 ID。无效 cursor 返回稳定 400。
- 首次加载最后 40 条且页内按时间正序；`scrollTop <= 125` 时向上取更老页并 prepend。前端用 `oldScrollTop + (newScrollHeight-oldScrollHeight)` 保持视口位置（`edge/static/app.js:124-199`）。
- 每次 History 请求都扫描完整 Hot 与 Cold JSONL；没有索引。接口只读，不调用 ModelClient、Recall、Dream、summarizer，也不写 compaction state。
- 重启后由两个 JSONL 重新构建历史；Dream 从 pending 转 consumed 不改变历史输出。
- History 是展示面，不进入 Chat 模型上下文。

**验收状态**：`tests/test_history_api.py` 自动验证空/Hot-only、summary 排除、真实 compaction+Dream、Cold-wins 去重、90-turn 分页、重启、非法 cursor 和只读性；前端静态测试验证初始加载与上滑逻辑。此前工作会话中用户已声明真实浏览器历史功能验收完成，但当前仓库文档尚未记录独立的 History 浏览器验收结果；因此它不是可由 committed 文档单独复核的事实。

## Frontend

前端是三个无框架静态文件：`edge/static/index.html`、`edge/static/app.js`、`edge/static/styles.css`。

当前可见能力：

- 单一聊天流、立即显示本地 user bubble、同步显示 assistant/fallback；
- backend/model 状态与 Recall enabled/disabled；
- bounded pending segment 数和截断标志；
- 显式“运行 Dream”按钮、运行状态和 aggregate result；
- Chat/Dream 期间相互禁用控件；
- Hot compaction 运行与 completed/failed 提示，完成后立即刷新 pending；
- 初始恢复最近 40 条 raw history；
- 向上滚动按 cursor 懒加载更早历史并保持视口。

当前没有：多会话列表、conversation/thread ID、登录/用户系统、Memory Viewer、Dream 自动触发/队列/进度流、WebSocket、SSE、后台任务、History 搜索或前端 prompt 编辑。

除 compaction 进行中的 500 ms 临时 status polling 外，没有常驻轮询。Chat 和 Dream 都是普通同步 HTTP `fetch`。

## Public API and Python Surface

### HTTP API

| 接口 | 请求 | 响应 | 主要状态码 | 状态读写与降级 | 明确不返回 |
| --- | --- | --- | --- | --- | --- |
| `GET /api/status` | 无 body | app/status、model mode、Recall flag、compaction running、Dream available/running、bounded pending count | 200 | 扫描 Cold 至最多 100 个完整 pending segment；读失败表现为不精确/截断状态，不写数据 | turn text、segment IDs、路径、backend、模型配置、异常 |
| `POST /api/chat` | `message` 或兼容 `text`，可选 `client_timezone`；extra forbidden | phase、assistant type/text、`message_consumed`、compaction result | 200、400 empty、409 writer busy、422 schema | 持锁生成并依次写 user/assistant，可能压缩；模型失败返回并持久化安全 fallback；Recall/存储失败不阻断 200 | Recall DTO/score/path/UUID、prompt、provider body/key、Draft records |
| `POST /api/dream/run` | 无业务参数 | attempted/ingested/consumed/skipped/failed | 200、409 busy、503 unavailable、500 safe failure | 持锁同步 ingest pending segments 并整段 consumed；单段失败由 aggregate 表示 | results 明细、segment IDs/text、memory IDs、路径、score、traceback |
| `GET /api/history` | query `limit=1..100`（默认 40）、可选 `before` | turns + `has_more` + `next_before` | 200、400 bad cursor、422 bad limit | 只读扫描 Cold+Hot、去重分页，无业务副作用 | summary、Cold state/segment、timezone/provenance 内部字段、MAGMA |

注意：`ChatResponse.message_consumed` 当前固定为 true，即便 Draft append 失败也不能完整表达持久化结果；这是已知契约限制，不应从该字段推断 user/assistant 两行都已 durable。

### 主要 Lumina-owned Python 表面

| 符号 | 位置 | 当前入口/职责 |
| --- | --- | --- |
| `MessageRuntime` | `core/message_runtime.py:35` | `handle_chat(ChatRequest) -> MessageRuntimeResult`；同步 Chat 编排，不拥有 Dream/MAGMA 算法 |
| `DreamRunner` | `Dream/runner.py:45` | `run_once(DreamRunPolicy) -> DreamRunReport`；有界串行 pending orchestration |
| `DreamRunPolicy` | `Dream/models.py:14` | 10/false/dream-v1 的不可变运行策略 |
| `MemoryRetriever` | `Conversation_Memory/adapter/interfaces.py:15` | `recall(str, RecallPolicy) -> MemoryContext` Protocol |
| `RecallPolicy` | `Conversation_Memory/adapter/models.py:64` | Recall 的九个有界/可选调用者参数 |
| `MemoryContext` | `Conversation_Memory/adapter/models.py:145` | 唯一公开 Recall 容器；无 MAGMA 对象 |
| `MagmaMemoryAdapter` | `Conversation_Memory/adapter/magma_adapter.py:23` | 同时实现 `ingest` 与 `recall`，是唯一 Lumina facade |
| `PendingCount` | `core/cold_draft_store.py:32` | bounded pending status 的 `count/truncated` 值对象，不携带正文 |

这些类没有集中 re-export；“公共”指当前跨模块实际 import/use 的 Lumina-owned 类型，而不是一个稳定版本化 SDK。

## Persistence and Concurrency

### 持久化表面

| 路径 | 内容/分类 | 写者 | 读者 | 原子性 | 可重建性 |
| --- | --- | --- | --- | --- | --- |
| `data/draft/hot_drafts.jsonl` | 一条 rolling summary + recent raw；工作记忆 | `JsonlDraftStore.append_turn`、Compactor replace | Chat context、Compactor、History | raw append 非事务/fsync；compaction 整文件原子 replace | recent raw 来自当前文件；summary 不能无模型精确重建，但移出原文在 Cold |
| `data/draft/cold_drafts.jsonl` | pending/consumed per-turn raw；原始证据 | Compactor append、Cold owner consumed transition | Dream、status、History | 每次 segment append 或 consumed 转换是整文件原子 replace | 是当前完整历史原文 authority；无自动迁移 |
| `data/draft/hot_draft_compaction_state.json` | generation、last compaction/segment；恢复诊断 metadata | `HotDraftCompactor` | 当前生产主链不读取 | 单文件原子 replace | 可丢失；Hot/Cold 成功后 state 失败不回滚 |
| `data/conversation_memory/ingestion_state.json` | `(segment_id, ingestion_version)` 状态与 private memory IDs；幂等 checkpoint | `IngestionStateStore`/adapter | adapter、Dream 间接读取 | 每次 map 更新单文件原子 replace | 不应静默重建；损坏安全失败。adapter 可按 evidence ID 复用已写 event 以收敛部分失败 |
| `data/conversation_memory/magma/` | `graph.json` + `vectors/`；派生长期记忆 | `RealMagmaBackend` | ingest retry、Recall | graph save 与 vector save 顺序执行，不是跨文件事务 | 理论上源 Cold raw 仍在，但没有生产自动 rebuild/re-ingest consumed 数据 |

### 一致性边界

- **Chat/Chat、Chat/Dream、Dream/Dream（同一 app 进程）**：同一个非阻塞 mutex 串行化，冲突返回 409。
- **History/status 与 writer**：不获取 writer lock。History 采用 Hot-first read + Cold-wins 去重来缩小 compaction overlap 风险；它不是跨两文件 snapshot transaction。
- **单 worker**：当前安全部署假定一个进程、一个 worker、无 `--reload` 重叠。
- **多 worker/多实例**：每个进程有自己的锁和内存 MAGMA snapshot，不能互相协调。
- **外部 Dream CLI**：构造独立 Cold/MAGMA owner，不受 app lock 保护；服务运行时并发执行会破坏单 writer 假设，并可能让 resident retriever 陈旧。
- **跨文件事务**：Cold、Hot、compaction state、graph、vectors、ingestion state 分别持久化，不构成统一原子事务。当前依靠 Cold-first 顺序、稳定 IDs、per-event persistence 和 checkpoint retry 收敛，而不是数据库事务。

## Test Coverage and Validation

### 测试盘点

| 区域 | 测试文件数 | 主要覆盖 |
| --- | ---: | --- |
| 根 `tests/` | 9 | API、ModelClient、背景、Hot/Cold stores、rolling compaction、MessageRuntime、History、Dream/Chat lock、Recall E2E harness 安全 |
| `Dream/tests/` | 1（另有 conftest） | segment conversion、顺序/边界、失败隔离、checkpoint-before-consumed、CLI、HTTP 显式触发、真实 MAGMA Dream |
| `Conversation_Memory/tests/` | 2（另有 conftest） | EN/ZH temporal、schema/provenance/idempotency、RRF、temporal window、fixed/adaptive traversal、graph expansion、linearization、真实 MAGMA |
| `scripts/*e2e*` | 1 | `scripts/recall_e2e_test.py` 是唯一 marker-owned 全链 Recall E2E |

真实 MAGMA E2E 使用 synthetic data 和 marker-owned sandbox，覆盖：真实 Hot compaction、per-turn Cold、Dream、6 event/vector persistence、9 个 Recall checks、中英时间规范化、公开边界、restart Recall、第二次 Dream 幂等和清理安全。它不调用真实回答模型，也不测真实用户长期语料。

`pytest` 中若不在 `Conversation_Memory/.venv`，标有解释的真实 MAGMA tests 会 skip；这是隔离环境选择，不是功能 skip。最终验证使用了该现有隔离解释器，以让真实 MAGMA tests 实际执行。

### 已有验收层级

- **真实浏览器已记录 PASS**：两轮 rolling compaction running/completed、pending 0→1→2、行级 Cold、Dream attempted=2/segment consumption、pending→0 和即时 Recall；记录在 `README.md`、`docs/CURRENT_STATUS.md`、`docs/COLD_DRAFT.md`。
- **History 浏览器**：此前用户工作会话明确声明已验收，但仓库权威文档尚未记录；代码仓库内可复核的是 API/前端自动化测试。
- **Synthetic-only**：Recall depth、GENERAL weights、cross-turn probe、temporal ranking audit、真实 MAGMA E2E 都不是现实用户/生产规模质量验收。
- **尚未验收**：多 worker、跨进程并发、真实长期记忆上的 answer quality、无答案 abstention、自动调度和 Evidence Organizer，因为这些能力本身尚不存在。

### 本次最终验证

以下结果在报告文件生成后各运行一次。为确保真实 MAGMA 条件测试不被
interpreter guard 跳过，表中的 `python` 使用现有
`Conversation_Memory/.venv/Scripts/python.exe`：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q` | **PASS** — 169 passed，0 skipped，6 warnings |
| `python -m pytest Conversation_Memory/tests -q` | **PASS** — 70 passed，0 skipped，5 warnings |
| `python -m pytest Dream/tests -q` | **PASS** — 32 passed，0 skipped，3 warnings |
| `python -m scripts.recall_e2e_test` | **PASS** — 1 Cold segment consumed，Dream failures 0，Recall 9/9，restart 与 idempotency PASS |
| `git diff --check` | **PASS** — exit 0；仅提示任务前已有 `Lumina_Canvas/.obsidian/workspace.json` 将来可能 LF→CRLF |
| MAGMA upstream status/diff | **PASS** — `status --short` 与 `diff --stat` 输出均为空 |

warnings 全部来自固定上游 MAGMA：`ast.Str` deprecation 和 Sentence
Transformers embedding-dimension API rename；没有 Lumina test failure。

## Known Limitations

### 已完成

- 本地单用户、单连续会话 Chat；固定背景；Mock/MiniMax；同步安全 fallback。
- 物理 rolling Hot working memory 和 Cold-first 完整 raw archive。
- 显式、同步、有界 Dream；当前 app 内共享 backend 和进程内 writer lock。
- 一 turn 一 event、稳定 provenance/idempotency、持久 graph/vector Recall。
- 有界 dense+lexical RRF、fixed/adaptive graph expansion、时间硬过滤、Context Linearization。
- Cold+Hot raw History、重启恢复、cursor pagination、前端上滑加载。

### 部分支持

- 跨轮指代：共同召回常可发生，但无显式 coreference，顺序和剪枝不稳定。
- 知识更新：能召回旧/新事件，不能判断 superseded/current state。
- Adaptive GENERAL：可用 ENTITY-biased 关系权重；时间边权很低，WHY 无 causal 特化。
- 无答案控制：只保证来源真实和输出有界，不保证空返回或拒答。
- 故障恢复：Cold-first + idempotency 可收敛多个窗口，但不是跨文件 ACID。

### 未完成或结构性限制

- 没有 Recall scheduler、自动 query/intent 分类、none/light/deep、动态预算、edge routing 或证据不足升级。
- 没有 Evidence Organizer/Ledger、语义去重、状态/冲突/时间线整理、关系解释或事实 supersession。
- 没有自动/后台/启动/模型触发 Dream。
- 没有多 conversation/thread identity、多用户、跨进程锁、多 worker 或数据库事务。
- user/assistant Hot writes 不是 pair transaction；`message_consumed` 不能真实反映 Draft 持久化失败。
- Hot raw append 不 fsync；History/status 是 JSONL scan；History 没有索引或一致 snapshot。
- 模型-facing 总上下文没有统一全局 token/char cap；背景、summary、recent raw、Recall 和 current user 分别受不同或无显式边界。
- Mock 模式不能生成 rolling summary；超过阈值时压缩安全失败并保留 Hot，而不是完成 Cold archive。
- MAGMA graph、vectors 和 ingestion checkpoint 跨文件不原子；多进程写会竞争。
- 没有 relevance threshold、LLM Judge、cross-encoder 或可靠 abstention；已失败的 cosine gate 已移除。
- 没有真实用户长期语料、最终答案质量或生产规模性能验证。

## Documentation Drift Matrix

| 文档 | 状态 | 仍然正确 | 已过时/遗漏 | 文档称已完成但代码不支持 | 建议 |
| --- | --- | --- | --- | --- | --- |
| `README.md` | `PARTIALLY_STALE` | 当前 Chat、background、rolling Hot、line Cold、Dream UI、共享 backend/lock、Recall 和部署限制基本正确 | 未记录 `/api/history`、重启历史恢复、cursor pagination、上滑加载；测试命令也未突出 History | 未发现主要虚假完成声明 | 窄增 History 和当前 API 表面；保留其余 |
| `AGENTS.md` | `PARTIALLY_STALE`（高风险） | Cold-first、provenance、Dream/Recall ownership、安全和最小改动原则仍正确 | 仍称 Hot append-only、Dream CLI-only、无 lock/API/UI，且把已完成 Dream UI 当下一目标；Known limits 与当前代码冲突 | 未把未来器官写成已完成 | 优先更新 Repository state、capabilities、next step、Hot invariant、known limits；同步审查 `Dream/AGENTS.md` |
| `MVP_GOAL.md`（实际文件为 `docs/MVP_GOAL.md`；根文件不存在） | `HISTORICAL` | 已明确自称 completed historical milestone，Cold-first 目标仍是基础约束 | 没有 History 等后续能力，但历史文档无需追平 | 无 | 保留并在链接处使用真实路径；不作为当前 objective |
| `docs/CURRENT_STATUS.md` | `PARTIALLY_STALE` | background、rolling Hot、line Cold、Dream UI、共享 lock/backend、Recall 能力/限制和浏览器 Dream 验收大体准确 | 未记录单会话 History；Cross-turn 审计尚为工作树产物，不应直接写入已提交状态 | 未发现主要虚假完成声明 | 补 History；待跨轮产物审阅/提交后再补准确“部分支持”结论 |
| `docs/final_goal.md` | `PARTIALLY_STALE`（目标段落失效） | 长期方向、Cold-first 主链、manual Dream、bounded Recall 和非目标仍有效 | “Next Production Objective”仍是已完成的 Dream button/shared lock/immediate visibility | 把它们写作目标而非已完成，不是虚假完成；但会误导下一任务 | 必须重新定义下一生产边界；移除已完成 Dream UI 作为 next objective |
| `docs/COLD_DRAFT.md` | `CURRENT` | 当前 Hot/Cold schema、24/12、Cold-first、browser notices、per-line Cold、segment Dream 与单进程限制均与代码一致 | 未发现本任务范围内实质遗漏 | 无 | No change |
| `docs/DREAM_UI_CODE_AUDIT.md` | `HISTORICAL` | 对实现前风险、最小接线、锁、共享 backend 和安全 DTO 的分析仍有档案价值 | “Current”部分描述无 API/UI/lock、resident backend stale 等实现前状态；其建议已经完成 | 无；它是审计/建议，不是当前状态 | 增加 completed/superseded 标记，作为里程碑归档，不再作为 next-step authority |
| `docs/MEMORY_SYSTEM_CODE_SCAN.md` | `HISTORICAL` | 早期 Cold-first、provenance、one-turn-one-event、Dream/Recall ownership 和 graph expansion 结论仍有价值 | 仍称 Hot append-only/逻辑压缩、固定 Recall、无显式 temporal usage，且不包含后续 RRF/adaptive/linearization/Dream UI/History | 无主要未来能力夸大；主要是把已完成能力写成不存在 | 归档为旧扫描，并由本报告取代作为当前全景 |
| `docs/CROSS_TURN_REFERENCE_AUDIT.md` | `CURRENT`（worktree-only） | 与当前代码/探针一致，明确 partial support、无 explicit coreference 和 synthetic-only 限制 | 尚未提交，不能代表 committed baseline；未来代码变化后需重跑或归档 | 无 | 审阅探针与报告后决定提交；不要把 6-case 结果泛化为生产质量 |

补充发现：`Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md` 仍称无 public API/UI 且展示旧嵌套 `turns[]` Cold schema；`Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md` 仍称 `max_graph_depth` 必须为正且最终裁剪为 `min(top_k,max_evidence_items)`。二者都是当前代码的事实错误，应进入 Priority 1，即使不在任务指定矩阵九项内。

## Recommended Documentation Updates

### Priority 1 — 必须修正的事实错误

1. `AGENTS.md`
   - 更新 Repository state、Existing capabilities、Current authorized next step、Hot invariants 和 Known limits。
   - 删除 Dream CLI-only、无 HTTP/UI/lock、物理 Hot append-only、Chat/Chat 同进程仍可竞态等过期断言。
2. `docs/final_goal.md`
   - 将 Dream UI/shared writer/shared backend immediate visibility 从“下一目标”移到 completed baseline。
   - 用单一、受限的下一边界替换，不把 Mind System 未来设计写成已完成。
3. `Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md` 与 `Dream/AGENTS.md`
   - 更新 line-level `cold_turn` schema、HTTP/UI 手动入口、共享 app-owned runner/backend/lock；保留 CLI 仅停服运行限制。
4. `Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md`
   - 更新 `max_graph_depth=0`、九字段 policy、`top_k=anchors`、`max_evidence_items=total`、RRF、temporal hard filter、fixed/adaptive、graph expansion 和 linearization。

### Priority 2 — 应补充的当前能力

1. `README.md`
   - 增加 `GET /api/history`、Cold+Hot raw、summary exclusion、40/100 cursor pagination、restart 和 upward lazy loading。
2. `docs/CURRENT_STATUS.md`
   - 增加 committed History 能力和 JSONL full-scan/单会话限制。
   - 仅在跨轮审计产物被审阅并提交后，加入“跨轮指代部分支持、无 explicit coreference”的窄结论。
3. `.env.example`/运行说明（只在后续文档任务授权时）
   - 考虑补充 Recall enable、default timezone 和 app-owned MAGMA path 的非秘密示例，避免 README 与示例配置分散。

### Priority 3 — 历史文档标记

- `docs/DREAM_UI_CODE_AUDIT.md`：标为 completed milestone / preimplementation audit。
- `docs/MEMORY_SYSTEM_CODE_SCAN.md`：标为 superseded historical scan，指向本报告。
- `docs/MVP_GOAL.md`：保持 completed historical milestone；修正任何错误的根路径链接。
- `docs/CROSS_TURN_REFERENCE_AUDIT.md`：审阅后提交，或保留为未采用 worktree experiment；不要悬置为隐式 authority。

### No change

- `docs/COLD_DRAFT.md` 当前与实现和已记录浏览器验收一致。
- `docs/NORTH_STAR.md` 是长期方向，不应为追逐当前实现而改写。
- `Conversation_Memory/docs/RELEVANCE_GATE_DESIGN.md` 已明确是失败实验的历史记录，与当前“无 gate”代码一致。

## Recommended Next Development Boundary

建议采用“**冻结 Memory v1 边界，转向 Mind System 的窄设计阶段**”，但要准确解释“冻结”：

- 冻结：Cold-first 原始证据、per-turn provenance、one-turn-one-event、segment/version checkpoint、Lumina-owned DTO/facade、bounded Recall、安全失败和 MAGMA upstream pin。
- 不冻结为“质量已完成”：无答案、冲突、supersession、证据组织、跨轮指代和真实数据质量仍有明确缺口。
- 下一边界应位于 Conversation Memory **调用者侧**：基于现有 `MemoryRetriever.recall(query, policy)`，设计何时不召回/anchor-only/graph-enhanced、如何判断证据充分性，以及如何把 evidence 整理成模型可理解的状态/冲突/时间线。
- 不应把 scheduler、Evidence Organizer 或自动 intent 分类塞回 `MagmaMemoryAdapter`；`intent` 当前明确由调用者提供，正好保留了该所有权边界。
- 第一阶段应先形成契约与合成验收，不在没有单独任务授权时引入 agent、worker、后台 Dream、数据库、LLM Judge 或自动行为。

North Star 对齐：

- **服务属性**：连续的整合心智与未来可演化性。
- **为何有帮助**：固定原始证据和记忆 facade 可保护身份连续性；把调度/理解放在上层，避免把检索后端永久等同于完整心智。
- **刻意不实现**：自主目标、自动 Dream、后台任务、自我修改、通用 agent 框架或任何未来器官。

## Web Handoff

1. 当前生产入口是 `core.main:create_app`，四个 API 为 status/chat/dream/history。
2. Lumina 是本地、单用户、单连续会话、单 worker 假设下的同步应用。
3. `prompts/chat_background.md` 启动时必读，完整进入每次正常 Chat 的 provider system 字段。
4. MiniMax system 顺序是 background 后 rolling summary；messages 是 recent raw、Recall block、current user。
5. background、Recall block、完整 History 不写入 Draft；rolling summary 只写 Hot summary record。
6. 唯一 Hot 默认路径是 `data/draft/hot_drafts.jsonl`。
7. Hot 是一条 summary 加 recent raw，不再物理 append-only。
8. 默认超过 24 raw 才压缩，目标保留 12，并只在完整 user/assistant pair 边界移动。
9. Mock 不支持摘要；触发压缩时会安全失败并保留 Hot/Cold 不变。
10. Cold 是每原始 turn 一条 `cold_turn` 行；pending/count/consume 都按完整 segment。
11. Cold-first 顺序是摘要成功、Cold 原子保存、Hot 原子替换、最后写非权威 compaction state。
12. Dream 有浏览器按钮和同步 `POST /api/dream/run`，默认一次最多 10 segments。
13. Dream 不是自动任务；Chat、Recall、启动都不会触发 ingestion。
14. Chat 与 in-app Dream 共享 Cold owner、memory adapter/backend 和一个进程内 mutex。
15. Dream 完成后当前 Chat Recall 无需重启即可看到新 memory。
16. Memory 写入是一 Cold turn 对应一 MAGMA event，带稳定 evidence ID 和完整 provenance。
17. 当前 anchors 是 MiniLM dense + bounded lexical，经 RRF(k=60) 融合；`top_k` 只限 anchors。
18. `max_evidence_items` 限公开 anchors+expansions 总数；`max_chars` 限 rendered text。
19. 默认 Recall 是 fixed traversal；intent/beam/drop 任一显式值才启用 adaptive。
20. GENERAL/ENTITY 使用 ENTITY-biased weights；WHEN 偏 temporal；WHY 没有 causal 特化。
21. Recall 不公开 UUID、score、path、graph、embedding、metadata 或 `narrative_context`。
22. 系统没有 abstention：合成 no-answer controls 仍返回 evidence。
23. 跨轮指代只能表述为部分支持；没有显式 coreference resolution。
24. History 是 Cold raw + Hot recent raw、Cold-wins turn-ID dedup；summary 不显示，也不进入模型。
25. History 默认 40、最大 100，cursor 是排他 turn ID；每次请求仍完整扫描 JSONL。
26. 前端支持 Chat、状态、Recall 状态、pending、Dream、compaction 提示、历史恢复和上滑分页；无 WebSocket/后台任务/多会话。
27. 上游 MAGMA 固定在 `467cb70...`、MIT、零本地修改；Lumina 只调用 TRG/graph/vector 的窄入口。
28. 当前主要文档漂移是 `AGENTS.md` 和 `docs/final_goal.md` 仍把已完成 Dream UI 当未来目标。
29. `docs/CROSS_TURN_REFERENCE_AUDIT.md` 与 probe 尚未跟踪；其数值不能当 committed baseline。
30. 建议先更新事实文档，再以单独任务设计 Mind System 调度/证据组织；不要重做 Memory 存储和 MAGMA facade。
