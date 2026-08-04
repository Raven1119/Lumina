# Dream UI Code Audit

## Result

`READY`

当前已经存在可复用的同步应用边界：
`DreamRunner.run_once(policy) -> DreamRunReport`。CLI 是薄壳，因此下一步不需要
提取第二个 service 或复制 CLI 编排。

`READY` 不表示可以直接暴露一个无保护的 HTTP endpoint。首个实现任务必须同时
解决单进程 Chat/Dream 写入互斥、单 worker/禁用 reload/单写入者部署边界、应用内 Cold Draft
owner 复用，以及 Dream 写入后常驻 Recall backend 的可见性问题。

## Documentation Baseline

### 当前权威事实

- 根 `AGENTS.md`、`Dream/AGENTS.md` 与 `Conversation_Memory/AGENTS.md` 共同约束
  本次审计；显式任务卡优先于早期里程碑措辞。
- `docs/CURRENT_STATUS.md` 是当前实现事实最完整的汇总。
- `docs/COLD_DRAFT.md` 对 Cold-first、pending/consumed、不可变来源和单写入者边界
  的描述与代码一致。
- `README.md` 的生产链、手动有界 Dream 和 memory-complete-before-consumed 描述
  基本符合当前代码。
- `docs/final_goal.md` 的总体方向仍有效，但其中下一阶段排序不能覆盖当前显式任务卡。
- `Dream/README.md` 与 `Conversation_Memory/README.md` 当前不存在；上游 MAGMA 的
  README 不是 Lumina-owned 状态文档。

开始审计时 `git status --short` 为空。最近五个提交为：

```text
0217a34 docs: audit MAGMA temporal anchor ranking
8b6487d eval: compare general traversal relation weights
12c6d31 feat: linearize recalled context by intent
3a35437 feat: add adaptive MAGMA graph traversal
f213c28 feat: fuse dense and lexical recall anchors with RRF
```

### 明显滞后

- `README.md:201-205` 仍称 graph-enhanced Recall 的质量、噪声和成本尚未经过
  characterization，但仓库已有相应合成评测。
- `docs/final_goal.md:74-105` 仍把 depth 0 与 graph-enhanced 对比列为下一目标，
  而 `docs/RECALL_DEPTH_EVALUATION.md` 已记录该评测。
- 根 `AGENTS.md` 的 current-next-step 和 `Conversation_Memory/AGENTS.md` 的早期
  milestone 部分仍保留旧阶段措辞。根规则已经明确：旧措辞不得触发重复实现。

本任务不顺手修改上述文档。

## Current Dream Call Chain

正式入口的真实链路是：

```text
python -m Dream.runner
-> Dream.runner._parser
-> DreamRunPolicy
-> Dream.runner.build_default_runner
-> DreamRunner.run_once
-> ColdDraftStore.list_pending
-> ColdDraftDigestionTask.digest
-> ColdDraftSegmentConverter.convert
-> RealMemoryIngestorProvider.get
-> MagmaMemoryAdapter.ingest
-> RealMagmaBackend.add_event / persist
-> IngestionStateStore checkpoint
-> ColdDraftStore.mark_consumed
-> DreamRunReport
-> CLI JSON stdout / exit code
```

主要文件和符号：

| 阶段 | 文件与符号 | 输入 | 返回/异常处理 |
| --- | --- | --- | --- |
| CLI | `Dream/runner.py:111-139`, `_parser`, `main` | 三个 CLI 参数 | 打印 `DreamRunReport` JSON；初始化失败转 `dream_initialization_failed` |
| 默认依赖 | `Dream/runner.py:29-42,86-108`, `RealMemoryIngestorProvider`, `build_default_runner` | 环境路径、ingestion version | 构造并缓存真实 adapter |
| 运行编排 | `Dream/runner.py:45-83`, `DreamRunner.run_once` | `DreamRunPolicy` | `DreamRunReport`；owner/单段异常转安全结果 |
| pending 读取 | `core/cold_draft_store.py:64-72,90-105`, `list_pending` | `limit` | pending records；I/O 错误目前退化为空 |
| 单段处理 | `Dream/cold_draft_digest.py:200-303`, `ColdDraftDigestionTask.digest` | Cold record、ingestion version | `SegmentDigestResult`；失败不 consumed |
| 转换 | `Dream/cold_draft_digest.py:54-149`, `ColdDraftSegmentConverter.convert` | pending Cold record | Conversation Memory DTO；schema/provenance 错误安全失败 |
| memory 写入 | `Conversation_Memory/adapter/magma_adapter.py:85-148`, `ingest` | `ColdDraftSegment` | `IngestionResult`；checkpoint 驱动恢复 |
| MAGMA 持久化 | `Conversation_Memory/adapter/backend.py:83-104`, `add_event`, `persist` | 单 turn event | graph/vector 落盘 |
| checkpoint | `Conversation_Memory/ingestion/state_store.py:14-45` | segment/version/status | 临时文件、fsync、replace |
| consumed | `core/cold_draft_store.py:74-114`, `mark_consumed` | segment ID | 整文件读改写后 replace |

### 当前运行语义

- `DreamRunPolicy` 位于 `Dream/models.py:13-32`。
- 默认 `max_segments=10`、`stop_on_error=False`、
  `ingestion_version="dream-v1"`。
- `DreamRunner.run_once` 使用串行 `for`，不并行写入。
- 默认单段失败后继续；`stop_on_error=True` 在首个 failed result 后停止。
- conversion、ingestion、结果校验或 consumed 转换失败都使该 segment 保持 pending。
- memory 已 completed、consumed 转换失败时，下一次运行会跳过重复 ingestion，只重试
  consumed。
- 幂等键是 `segment_id + ingestion_version`，单 turn 另有稳定 evidence ID 查重。
- `DreamRunReport` 的实际字段是 `attempted`、`ingested`、`consumed`、`skipped`、
  `failed`、`results`，见 `Dream/models.py:48-73`；不存在 `succeeded` 或
  `remaining_pending`。

持久化顺序严格为：MAGMA 逐事件持久化与 checkpoint 更新，关系与最终持久化，
checkpoint `completed`，Dream 验证完整结果，最后才 `mark_consumed`。

## Reusable Application Boundary

CLI 判定为任务卡分支 A：薄壳。

```python
DreamRunner.run_once(policy: DreamRunPolicy) -> DreamRunReport
```

该函数不读取环境变量、不依赖 CLI 全局状态、不打印，使用注入依赖并返回结构化、
安全的 Lumina-owned result，因此 FastAPI 可以调用它。

HTTP handler 不应每次调用 `build_default_runner()`：该工厂读取独立 Dream 环境路径，
并创建另一套 Cold owner、MAGMA adapter 和内存 backend。安全接入应在应用构造阶段
复用 `app.state.cold_draft_store`，缓存一个显式构造的 runner，并让 Dream ingestion
与 Chat Recall 在启用 Recall 时共享同一 adapter/backend 或采用等价的受锁刷新策略。

否则 Dream 虽然成功写盘，已经启动的 Chat retriever 仍持有旧 graph/vector 内存快照，
新记忆通常直到 backend 重建或进程重启才对 Recall 可见。证据位于
`core/main.py:49-61,100-136`、`Dream/runner.py:29-42,86-108` 和
`Conversation_Memory/adapter/backend.py:50-75`。

## State Availability

| 状态 | 当前分类 | 代码事实 |
| --- | --- | --- |
| pending segments | 有公开函数，但没有合格的有界 count | `ColdDraftStore.list_pending(limit)` 先全量读取、解析并物化含正文的 JSONL，最后才 slice，见 `core/cold_draft_store.py:64-72,90-105` |
| running | 当前无法安全获得 | 没有 flag、lock、registry 或运行状态 |
| current result | 已有公开返回值 | `run_once()` 返回 `DreamRunReport` |
| last result | 需要最小进程内状态 | 当前不保存；重启丢失可接受，不应建立历史库 |
| recall enabled | 需要最小公开 wiring | 仅有 `create_app` 局部值和 `MessageRuntime._recall_enabled` 私有字段；`/api/status` 未公开 |
| Conversation Memory available | 当前无法安全获得 | 初始化失败可被包装为 unavailable backend；没有 health/getter，不能从私有类型或一次 Recall 猜测 |

状态查询不得返回 Cold Draft 正文。若首版必须显示 pending 数量，下一任务需要在唯一
Cold Draft owner 内增加流式、只计数、可早停的有界读取；不能把现有 `limit` 描述为
有界磁盘扫描，也不应在 HTTP handler 中复制 JSONL 解析。

`last_result` 不是首版必需字段：同步 POST 已能返回本次 result。若状态接口保留
last result，它只能是进程内 aggregate，并明确重启后丢失。

## Concurrency and Writer Safety

当前生产路径没有 `threading.Lock`、`asyncio.Lock`、文件锁或 store 级互斥。
Cold consumed、compaction state 和 checkpoint 的 replace 只保护单次替换，不保护此前
的 read-modify-write，也不形成跨文件事务。

共享写入文件包括：

- Hot：`data/draft/hot_drafts.jsonl`；
- Cold：`data/draft/cold_drafts.jsonl`；
- compaction state：`data/draft/hot_draft_compaction_state.json`；
- ingestion checkpoint：`data/conversation_memory/ingestion_state.json`；
- MAGMA：`graph.json`、FAISS/NumPy vector index 和 metadata。

### Chat vs Chat

不安全。`core/message_runtime.py:155-166` 分别 append user 与 assistant/fallback，两个
请求可能交错。`core/hot_draft_compactor.py:48-87,122-141` 的 compaction 可让两个
请求同读旧 Hot/state、重复 Cold append、竞争固定 `.tmp` 或覆盖 state。

`POST /api/chat` 是同步 handler，FastAPI 会在线程池并发执行，所以单 worker 并不等于
单请求。

### Chat vs Dream

不安全。Chat compactor append Cold；Dream `mark_consumed` 全量读取旧 Cold 快照后
replace 主文件。如果 Chat 在快照之后 append 新 segment，Dream 的旧快照可以覆盖并
丢失该新 segment，而 Chat 仍可能推进 compaction state，破坏 Cold-first。

### Dream vs Dream

不安全。两个 run 可选择同一个 pending，并竞争 Cold JSONL、checkpoint、MAGMA graph
和 vector 文件。checkpoint 的原子 replace 不能消除 get/put TOCTOU；两套 MAGMA 内存
快照可能互相覆盖图、向量和对应 checkpoint。

### Multiple workers / external CLI

不安全。每个 worker 都创建独立 runtime/store/backend，却写同一默认磁盘文件。普通
进程内 Lock 不能保护其他 worker，也不能保护服务运行时另起的
`python -m Dream.runner`。

`README.md:82` 只展示 `python -m uvicorn core.main:app --reload`，仓库没有
`--workers` 配置，也没有代码级单 worker 强制。第一版必须明确并验证单 worker、
禁用 `--reload`、保持单写入者，并禁止服务运行期间并发运行外部 Dream CLI；否则
需要另一个跨进程文件锁任务。

上游 MAGMA graph/vector 保存是直接覆盖、无锁且非原子，见
`Conversation_Memory/upstream/MAGMA/memory/graph_db.py:666-674` 与
`vector_db.py:340-360`。

### 最小进程内保护

同一应用级 writer mutex 必须同时覆盖：

```text
完整 chat handle（首版可保守串行化）
互斥于
完整 Dream run（pending selection 到 consumed）
```

只锁 Dream-Dream 不够。首版可在 `core/main.py` 中围绕同步 route 使用一个共享
`threading.Lock`，并在 busy 时返回稳定、无内部信息的 `409 Conflict`。这会保守地
串行化 model call，但避免为了细粒度锁修改 MessageRuntime 内部事务；后续若有延迟证据，
再用独立任务把 model call 与 Draft commit 分离。

前端禁用聊天只是交互提示，不能保护其他标签页、直接 API、外部 CLI 或其他 worker。

## Current API Shape

- `core/main.py:64-137` 的 `create_app` 同步构造 model、可选 retriever、Hot/Cold stores、
  compactor 和单个 `MessageRuntime`。
- `core/main.py:139-142` 的 `app.state` 只保存 runtime、Hot store、Cold store。
- `core/main.py:144-151` 集中定义 `GET /api/status`。
- `core/main.py:153-158` 集中定义同步 `POST /api/chat`。
- `core/main.py:160-165` 最后 mount 静态目录，因此不遮蔽 API。
- 没有 `APIRouter`、`Depends`、lifespan、后台任务或全局异常 handler。
- `core/main.py:170` 在模块导入时执行 `app = create_app()`；每个进程各建一套对象。

当前 `/api/status` 的 `StatusResponse` 只有 `app`、`status`、`mode`、
`draft_enabled`，见 `core/contracts.py:15-21`。空 chat 显式返回 400；Pydantic 校验返回
422；model/Recall 失败通常安全降级为 200；没有现成 409/503 模式。

Dream 是同步阻塞链，未来 handler 应继续使用同步 `def`，由 FastAPI 线程池执行；不能
在 `async def` 中直接调用。公开响应只应包含 aggregate Dream result 和稳定安全码，
不得包含路径、traceback、Cold 正文、MAGMA 对象/UUID、provider 配置或 per-segment
原文。

推荐接入点仍是现有 `core/main.py`，不需要新 router。runner、writer lock、running flag、
truthful `recall_enabled` 和可选 last aggregate 应放在同一 `app.state` 生命周期中，而不应
放在模块级第二套 registry。

## Current Frontend Shape

当前是无框架、无构建层的单页原生前端：

- `edge/static/index.html`；
- `edge/static/app.js`；
- `edge/static/styles.css`。

HTML 只有 header backend status（`index.html:12-15`）、chat log（17）、chat notice
（19）和 chat form（21-31），没有侧边栏、状态卡或 Dream 元素。

`app.js:70-98` 封装聊天 POST，`100-117` 在页面加载时调用一次 `/api/status`，
`119-161` 处理 submit。发送期间只禁用 chat input/send，错误、Sending 和 fallback 共用
chat notice；成功普通回复会清空 notice。没有轮询、WebSocket、EventSource 或状态库。

最小 Dream 控件位置是 `</header>` 后、chat log 前的独立紧凑 maintenance row，包含
一个按钮、一段状态和本次 aggregate result。不要虚构侧边栏，也不要把 Dream 结果写成
聊天消息。

可以复用 `.chat-status`、`.is-ok`、`.is-down` 的颜色语义；`.chat-notice` 是聊天流程
拥有且偏错误色，不应承载 Dream 成功结果。现按钮样式限定于 `.chat-form button`，可
窄扩展明确的 `.dream-button`，无需 UI 重构。文本继续使用 `textContent`，不插入 HTML。

一次同步 POST 足够：点击时本地置 running，结束后显示本次 aggregate 并重新 GET
status。首版没有后台/自动 Dream，不需要轮询、WebSocket、队列或历史。Dream 运行期间
同一页面同时禁用 Dream 与 chat controls；chat 发送期间也禁 Dream，但后端锁仍是唯一
安全边界。

最小显示字段：

- `pending_segments`，必须明确为有界/可能截断的计数；
- `running`；
- `recall_enabled`，含义只能是配置启用，不能冒充 memory available；
- 本次 POST 的 `attempted`、`ingested`、`consumed`、`skipped`、`failed`。

不需要把 `results`、segment ID 或 error 细节写入 DOM。`remaining_pending` 不是现有
Dream DTO 字段；若下一任务需要，应只在 HTTP 边界从 owner 的安全计数结果派生。

## Recommended Minimal Implementation

结论是分支 A：复用现有应用函数，不做业务函数提取。

### 建议允许修改的生产文件

- `core/main.py`
  - 在 `create_app` 中构造并缓存单例 Dream runner；
  - 复用当前 app 的 Cold owner；
  - 在 Recall 启用时共享 memory adapter/backend，或采用等价受锁刷新；
  - 创建 Chat/Dream 共用 writer lock 与 Dream running/current-result 状态；
  - 增加同步 `POST /api/dream/run`；
  - 扩展现有 `GET /api/status`；
  - busy 时返回稳定 `409`，初始化不可用时使用安全 `503` 或结构化失败。
- `core/cold_draft_store.py`
  - 增加只计数、不返回正文、可早停的有界 pending 查询；
  - 保持该 owner 为 Cold Draft 唯一读写权威。
- `core/contracts.py`
  - 只增加 endpoint 必需的窄响应字段；
  - Dream aggregate 字段沿用 `DreamRunReport` 术语，不创建第二套业务含义。
- `edge/static/index.html`
  - 增加 header 下方 maintenance row。
- `edge/static/app.js`
  - 增加同步 run/status 请求、控件禁用和纯文本结果展示。
- `edge/static/styles.css`
  - 增加最小 Dream row/button/result 样式，复用现有状态颜色。

不需要修改 `Dream/runner.py`、`Dream/cold_draft_digest.py`、RecallPolicy、Memory DTO、
Draft 状态机或上游 MAGMA。

### 建议复用的现有测试文件

- `tests/test_chat_api.py`：Dream endpoint、status、409、安全响应、静态资源/API 路由；
- `tests/test_cold_draft_store.py`：有界 pending count、坏记录和 no-body-leak；
- 仅在共享 runner wiring 改变既有 Dream 契约时窄改
  `Dream/tests/test_dream_cold_draft_digest.py`。

### 第一版固定行为

1. 固定使用 `DreamRunPolicy` 当前默认值：10、false、`dream-v1`；客户端不传策略。
2. 用户不能选择 segment，不能修改 ingestion version 或 stop policy。
3. 同步 POST；不创建 background task。
4. 第二个 Dream 或与当前 writer 冲突的请求返回 409；锁在所有成功/失败路径释放。
5. 部署限定一个 worker、禁用 reload，服务运行时禁止并发执行外部 Dream CLI。
6. Dream 成功后当前进程的 Recall 能立即看到同一 backend 的新记忆。
7. status 只提供安全 aggregate/config 状态，不扫描或暴露正文。
8. UI 禁用只作为 UX，不作为互斥证明。

## Risks

已确认风险：

- Cold append 与 consumed snapshot replace 可直接丢失新 segment；
- chat/chat 可破坏 turn 成对顺序和 compaction state；
- Dream/Dream 可让 checkpoint、MAGMA 图/向量和 Cold 状态互相覆盖；
- 当前常驻 retriever 在 Dream 后可能保持旧快照；
- 精确 pending count 当前需要无界全扫并物化正文；
- 进程内锁无法保护多 worker、reload 重叠或外部 CLI；
- ChatResponse 当前可能在 Draft append 失败时仍返回 `message_consumed=True`，这是既有
  限制，不应由 Dream UI 任务顺手重构。

第一版不应解决：全局 Draft 事务、跨进程 writer lock、物理 Hot 截断、Dream 历史、
任务队列、WebSocket、自动触发、Memory Viewer 或多 worker 架构。这些需要独立任务和
明确迁移/部署策略。

## Next Task Boundary

下一张实现任务应明确完成：

```text
现有 DreamRunner.run_once
+ app-owned Cold store 和 memory adapter
+ Chat/Dream 共享单进程 writer lock
+ 单 worker / 禁用 reload / 禁止并发 CLI 约束
+ POST /api/dream/run
+ 扩展 GET /api/status
+ 有界、no-body pending count
+ 原生前端 maintenance row
+ 同步本次 aggregate result
```

必须验证：chat/chat、chat/Dream、Dream/Dream 不会并发写；第二个 run 返回 409；异常后
锁释放；Dream 后当前 Recall 立即可见；部署不使用 reload；状态与响应不泄漏正文、
路径、traceback、UUID 或 MAGMA 对象；前端和直接 API 都不能绕过后端保护。

下一任务明确不做：后台 Dream、定时/自动 Dream、任务队列、WebSocket、Dream 历史、
Memory Viewer、segment 选择、策略编辑、多 worker 支持、跨进程锁、Recall 算法修改、
MAGMA 修改、Draft 状态机重构或 UI 框架迁移。
