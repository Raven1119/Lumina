# Mind A0 — Interface Audit & Experiment Baseline

- 日期：2026-08-30
- 范围：只读 source-level audit
- 代码变更：无；本文件是本任务唯一预期产物
- 结论：`READY_FOR_EXPERIMENT_A`

## 0. Executive conclusion

当前代码库已经具备完成隔离式 Mind MVP Experiment A 所需的三个基础 seam：

1. `core.model_client.ModelClient.generate(...)` 提供现有 MiniMax 文本模型/provider seam；
2. `Conversation_Memory.adapter.interfaces.MemoryRetriever.recall(...)` 提供合法、Lumina-owned、可由调用方显式给 query 的受界 Recall seam；
3. `ExecutionOrgan.state` 提供只读的当前 `ExecutionState`，足以由宿主在 `Mind/` 内投影出 Experiment A 所需的 goal/status/latest outcome/failure 快照。

因此，Experiment A 的机制验证可以全部留在 `Mind/` 内完成，不需要修改生产 Chat、Memory 或 Execution 代码。候选实现必须是一个最多一次信息获取、最多两次模型调用的宿主驱动 cognitive loop；模型只输出严格文本 envelope，宿主只分派 `recall_memory` 或 `inspect_execution` 两个固定只读分支。

当前仍没有 Execution-owned、公开、受界、脱敏的 supervisor/inspect interface，也没有 live execution lookup。这个缺口会阻止未来生产级 Mind→Execution supervision 接线，但不阻止 Experiment A 用显式注入的执行快照/只读 callable 验证 cognitive loop。它不应在 Experiment A0 或 A 中被顺手实现。

## 1. Audit basis and current production seams

本审计先读取并以以下材料为约束：

- `Mind/AGENTS.md`
- `AGENTS.md`
- `docs/NORTH_STAR.md`
- `docs/CURRENT_STATUS.md`
- `docs/MIND_DESIGN.md`

随后检查当前工作树中的真实代码、公开导出、调用点和测试。结论反映 2026-08-30 当前 source state，而不是只依据设计文档推断。

| Concern | Current seam | Current purpose | Experiment A disposition |
|---|---|---|---|
| Old Mind | `MindGate.decide(...) -> MindDecision` | Chat 前置 Recall 二分类 | 保留，不扩写成 cognitive Mind |
| Text model | `ModelClient.generate(...) -> str` | Chat、旧 gate、Formation 的同步文本生成 | 复用 provider/测试 seam |
| Memory | `MemoryRetriever.recall(query, policy) -> MemoryContext` | Lumina-owned bounded Recall | 直接合法复用 |
| Execution | `ExecutionOrgan.state -> ExecutionState | None` | 当前 durable execution 的状态重放 | 只由宿主读取并做受界投影 |
| Old Mind audit | `JsonlDecisionLog.record(...)` | 记录 `{turn_id, recall, decided_at}` | 不作为 cognitive Mind Trace 复用 |
| Execution trace | internal `EventLog` / `DecisionFrame` | Execution 因果历史、重放和 provider/action 证据 | 只作 Experiment C 的设计参考，不直接依赖 |

## 2. Existing production Mind gate

### 2.1 Exact files and symbols

- `Mind/interfaces.py:15-17` — frozen `MindDecision(recall: bool)`。
- `Mind/interfaces.py:20-28` — `MindGate` protocol：
  `decide(user_message: str, recent_context: list[dict[str, str]]) -> MindDecision`。
- `Mind/constant_gate.py:13-19` — `ConstantMindGate`，恒定返回 `recall=True`。
- `Mind/llm_gate.py:17` — `PROMPT_VERSION = "mind-gate-v2"`。
- `Mind/llm_gate.py:19-33` — Recall 分类 system prompt 与 user template。
- `Mind/llm_gate.py:36-60` — `LlmMindGate`。
- `Mind/llm_gate.py:63-69` — `_parse(raw)`。
- `Mind/decision_log.py:18-44` — append-only `JsonlDecisionLog`。
- `core/main.py:172-193` — `_default_mind_gate(...)` 的生产选择与 rollback。
- `core/main.py:334-362` — 构造 `effective_mind_gate` / `JsonlDecisionLog` 并注入 `MessageRuntime`。
- `core/message_runtime.py:151-174` — `_decide_recall(...)`。
- `core/message_runtime.py:176-204` — gate 后的 Recall guard/injection。

### 2.2 Input and output

生产输入为当前 `user_message` 加当前可见 `recent_context`。`DraftContextProvider.DEFAULT_LIMIT=12`，并只投影 `role/text`（`core/draft_context.py:13-29`）；但旧 gate 自身没有字符/token 上限，`ChatRequest` 的 message/text 也没有 max-length 约束（`core/contracts.py:73-78`）。所以它不是未来所需的 bounded `MindContext`。

输出只有 `MindDecision { recall: bool }`。它不表达：

- `NoChange`；
- `Directive`；
- `DecisionIntent`；
- capability request；
- activation/failure/causal provenance。

### 2.3 Prompt and “structured output” behavior

`LlmMindGate.decide` 将可见上下文展平为 `role: text`，再把展平上下文和当前消息放进一个分类 prompt；调用方式为：

```text
ModelClient.generate(
    recent_context=[],
    user_message=<flattened classifier prompt>,
    system_prompt=<Recall classifier prompt>,
)
```

见 `Mind/llm_gate.py:51-60`。provider 没有 schema/JSON mode；所谓结构化输出只是 prompt 要求单词 `true`/`false`，然后由 `_parse` 做宿主侧严格文本解析。解析器只接受去空白、lowercase、去尾部标点后的 literal `true` 或 `false`，其他输出抛 `ValueError`（`Mind/llm_gate.py:63-69`）。

### 2.4 Model/provider seam and configuration

`LlmMindGate` 注入的是 `core.model_client.ModelClient`。生产选择位于 `core/main.py:172-193`：

- answer model 为 mock 时使用 `ConstantMindGate`；
- `LUMINA_MIND_GATE_MODE=constant` 时使用 `ConstantMindGate`；
- 其余情况通过 `build_model_client_from_env(...)` 建独立 gate client；
- override 为 `MiniMax-M3`、`max_tokens=8`、`temperature=0.0`；
- client 构造失败或得到非 real model 时回退 `ConstantMindGate`。

需要精确区分文档表述与源码事实：源码确实选择 MiniMax-M3，并且 model client 只抽取 response 中首个 `type=text` block；请求体没有显式 `thinking=false` 字段。因此 source-level 可证明的是 M3/8-token/temperature-0 配置和 thinking block 不进入返回文本，而不是一个显式 provider “non-thinking” request flag。

### 2.5 Failure semantics

调用顺序见 `core/message_runtime.py:80-107`：创建 user turn → 读取当前 context → Mind gate → Recall → Answer model。

`MessageRuntime._decide_recall` 的行为（`core/message_runtime.py:151-174`）：

| Condition | Result |
|---|---|
| 无 gate | allow Recall；无 Mind event |
| provider/parse/任意 gate exception | allow Recall；`mind_gate_failed` |
| decision log 写失败 | allow Recall；`mind_decision_log_failed` |
| audited `recall=False` | skip Recall；`mind_recall_declined` |
| audited `recall=True` | allow Recall；`mind_recall_decided` |

因此旧 gate 是 fail-open-to-Recall。特别地，无法审计的拒绝不会静默生效。该语义正确服务旧 Recall gate，但不能直接转义成 cognitive Mind failure；未来 cognitive failure 应是“不产生 Directive/DecisionIntent、不改变 Intention”，而不是“默认 recall=true”。

### 2.6 Audit log

`JsonlDecisionLog.record` 写入 compact JSONL（`Mind/decision_log.py:18-44`）：

```json
{"turn_id":"...","recall":true,"decided_at":"...Z"}
```

默认路径为 `data/mind/decisions.jsonl`，可由 `LUMINA_MIND_DECISION_LOG_PATH` 覆盖（`core/main.py:151-156`）。只有成功决策会进入这个 durable log；provider/parse failure 与 log failure 仅作为当前 `MessageRuntimeResult.events` 返回。

它没有 activation id、trigger、model-visible context、prompt version、raw model output、capability call/result、failure 或 causal refs，因而不能充当 Experiment A/C 的 Mind Trace。

### 2.7 Reusable vs Recall-gate-specific

可复用：

- `ModelClient.generate` 的生产 provider seam；
- `build_model_client_from_env` 的 model/max-token/temperature override；
- sanitized `ModelClientError`；
- 注入 canned/scripted model 并记录 calls 的测试方法；
- “严格宿主解析，错误不泄露 provider body”的模式。

只属于旧 Recall gate，不应复用为 cognitive contract：

- `MindGate` / `MindDecision`；
- `LlmMindGate` prompt/template/parser；
- 8-token gate 配置；
- `ConstantMindGate`；
- `_default_mind_gate` 与 `MessageRuntime._decide_recall`；
- fail-open-to-Recall 语义；
- 当前 decision-log schema；
- Chat 前置接线位置。

Experiment A 应新增独立实验 contract，不能把 `MindDecision` 扩成多职责 union，也不能替换现有生产 gate。

## 3. Conversation Memory public Recall seam

### 3.1 Public callable and DTOs

合法 seam 是：

```python
# Conversation_Memory/adapter/interfaces.py:15-16
class MemoryRetriever(Protocol):
    def recall(self, query: str, policy: RecallPolicy) -> MemoryContext: ...
```

具体 implementation 是 `Conversation_Memory/adapter/magma_adapter.py:71` 的
`MagmaMemoryAdapter`；`create_real` 位于 `:96-116`，`recall` 位于 `:160-263`。
生产构造点是 `core/main.py:206-225` 的 `_build_memory_retriever`，再通过
`create_app` 注入 `MessageRuntime`。Mind 应依赖 `MemoryRetriever` protocol，不能依赖
同时拥有 ingest 能力的 concrete adapter。

相关 Lumina-owned frozen DTO 位于 `Conversation_Memory/adapter/models.py`：

- `SourceProvenance`：`:42-58`；
- `RecallPolicy`：`:72-119`；
- `MemoryEvidence`：`:122-126`；
- `MemoryContext`：`:130-137`。

`MemoryContext` 只公开：

```text
query
evidence: tuple[MemoryEvidence, ...]
rendered_text
truncated
safe_error_code
```

没有公开 BGE/Hindsight/backend score、embedding、graph node、MAGMA UUID、FAISS handle 或 graph path。

### 3.2 Explicit caller query

可以。`query` 是明确的 caller input，不是从 Chat 隐式读取，也不是由 Memory 自己解析 Intention。`MagmaMemoryAdapter.recall` 会验证并 `strip()` query（`Conversation_Memory/adapter/magma_adapter.py:160-163`）。因此 cognitive Mind 可以在第一轮判断后提出一个受界 query，再由宿主合法调用 Recall。

### 3.3 Boundedness

`RecallPolicy` 的通用 defaults（`adapter/models.py:72-79`）为：

| Field | Default |
|---|---:|
| `top_k` | 5 |
| `max_chars` | 2000 |
| `max_evidence_items` | 5 |
| `max_graph_depth` | 5 |
| `max_nodes` | 100 |
| `final_min_score` | `None` |
| `relation_surfaces` | `None` |

`__post_init__` 要求 count/node/char limits 为正、depth 非负、score 有限、relation surfaces 为非空字符串 tuple（`:81-117`）。但它没有 hard upper maxima，所以 Experiment A 必须使用 code-owned fixed policy；绝不能让模型选择 `top_k`、depth、node 或 char budgets。

实际受界点包括：

- dense/lexical/RRF/traversal budgets：`adapter/_recall_execution.py:24-95`；
- backend expansion `max_nodes` cap：`adapter/backend.py:394-553`；
- inclusive `final_min_score`：`adapter/magma_adapter.py:231-237`；
- final count/chars：`adapter/magma_adapter.py:256-260` 和 `recall/rendering.py:23-61`。

生产 Chat 的 policy 是 `core/main.py:54-61` 的私有 `_CHAT_RECALL_POLICY`：top-10、depth-1、nodes-20、evidence-3、chars-5000、score ≥ 0.144。Mind 不应 import 这个 `core.main` 私有常量；Experiment A 应在实验宿主中显式传入自己的固定 policy，同时不修改 Memory 算法、BGE、Hindsight 或 threshold。

### 3.4 Failure semantics

`MagmaMemoryAdapter.recall`（`adapter/magma_adapter.py:160-263`）是 fail-soft implementation：

| Condition | Public result |
|---|---|
| non-string / blank query | empty `MemoryContext`, `invalid_query` |
| entity target classification/lookup failure | fail open to untargeted Recall |
| backend failure | empty context, `recall_unavailable` |
| relation/rerank/BGE/Hindsight/render failure | empty context, `recall_unavailable` |
| no candidates | successful empty context, no error |
| success | bounded evidence/rendered text |

Protocol 本身不能保证任意 adapter 都不抛异常。`MemoryContext.safe_error_code` 可以投影成一个 bounded unavailable observation；但 adapter 真正抛出的 exception 应结束为 conservative activation failure。两种情况都不能把 traceback/provider/backend detail 放入下一轮模型 context。

### 3.5 Ownership verdict

以下用法不绕过 Memory ownership：

- 只依赖/inject `MemoryRetriever`；
- 只使用 `RecallPolicy`、`MemoryContext`、`MemoryEvidence`、`SourceProvenance`；
- 调用 `recall(explicit_query, fixed_policy)`；
- 给模型只投影 bounded rendered evidence、安全状态和必要 provenance。

Mind 禁止 import/use：

- `adapter.backend` 或 `MagmaMemoryAdapter.backend`；
- `adapter._recall_execution` / `_anchor_fusion`；
- `recall.bge_reranker` / `hindsight_scoring` / internal rendering；
- `upstream/MAGMA/**`；
- MAGMA、NetworkX、FAISS、embedding/vector/graph objects；
- private scores、paths、raw backend candidates。

结论：Memory 不缺 Experiment A 所需 seam，也不需要外部修改。

## 4. Supported Execution surface

### 4.1 Public exports and facade

`Execution/__init__.py:1-15` 只公开：

```text
ChildRef
ExecutionOrgan
ExecutionResult
ExecutionState
FileContentEquals
```

`ExecutionOrgan` 位于 `Execution/organ.py:21-160`。构造器内部拥有 `SharedEnvironment`、`ToolHost`、`PersistentIPython`、`AgentProcess`、`EventLog` 和默认 `DeepSeekModel`。它是 authority-bearing Actor facade，不是 supervisor handle。

公开读取：

- `ExecutionOrgan.state`（`organ.py:79-91`）：从 durable events/checkpoint 重建 frozen `ExecutionState | None`；
- `ExecutionOrgan.result`（`:93-95`）：只返回本进程里最近一次 mutating call 缓存的 `_result`。

同一对象还公开 `run_goal`、`run_child`、`open_child`、`accept_child`、`deliver_event`、`interrupt`、`resume` 和 `shutdown`（`:97-156`）。因此模型绝不能收到 `ExecutionOrgan` 对象本身。

### 4.2 Requested supervisor information

`ExecutionState` 位于 `Execution/execution.py:2039-2070`。

| Required information | Current source | Verdict |
|---|---|---|
| current Intention / goal | `ExecutionState.goal` | 每个 run 可读；代码中没有独立 `Intention` model/global source of truth |
| coarse status | `ExecutionState.status` | 可读：running/waiting/suspended/child_pending/completed/failed |
| recent outcome/failure | `latest_observation`, `last_result`, `completion`, `failure`, `child_outcomes` | 部分可读；是 latest/raw state，不是 “important outcome” 语义投影 |
| bounded trace/event information | 无 public supervisor interface | 不存在 |

`state` 是只读 property，但这条读取路径不是完整的 fail-soft interface：corrupt EventLog 会在 `ExecutionOrgan` 构造时由 `EventLog.load` 抛出（`Execution/execution.py:851-866`）；corrupt checkpoint/state inconsistency 会在读取 `state`、执行 `restore_execution_state` 时抛出（`:2488-2512`）。宿主投影必须转换为 conservative activation failure，不能把 raw exception 暴露给模型。

### 4.3 Why full result/trace is not a legal Mind inspect surface

`ExecutionResult`（`Execution/execution.py:3005-3019`）包含完整 `events` 与 `decision_frames`。`DecisionFrame`（`:663-676`）包含 exact `ModelRequest`、exposed tools、raw model response、resulting Actor action、provider wire request/response 和 tool-call IDs。它们既不受界也不脱敏，可能泄露 code、tool inputs/results、provider data 与 Actor authority evidence。

Execution 内部确实已有 `_text_projection`、observation projections 和 `_bounded_context`（`Execution/execution.py:2515-2878`），但这些是 underscore-private、为 Actor model context 服务的 implementation，不是公开 supervisor seam。Mind import 它们会越过 Execution ownership。

`EventLog`、`ExecutionEvent`、`DecisionFrame`、`Checkpoint`、`fold_execution_state`、`restore_execution_state` 都没有从 `Execution/__init__.py` 导出。`Execution_lab2` 对这些 internals 的使用只是历史/regression evidence，不能当成生产依赖先例。

### 4.4 Current API limitation

`POST /api/execution` 在 `core/main.py:428-474` 中为每个 request 创建一个局部 `ExecutionOrgan`，run 后 shutdown，只返回 `ExecutionResponse {execution_id,status,result,verified}`（`core/contracts.py:52-70`）。当前没有：

- live organ registry；
- execution-id lookup；
- read-only inspect endpoint；
- Mind/Chat 到 Execution 的接线。

### 4.5 Experiment A disposition

Experiment A 不把 organ 传给模型。宿主可接受一个明确注入的只读 callable，或在模型调用前/ capability 分派时读取一个已持有 organ 的 `state`，然后只返回以下受界 projection：

```text
execution_goal_snapshot     <- state.goal
status                      <- state.status
recent_outcome              <- selected bounded public state value
failure                     <- bounded state.failure
```

这足以验证“模型判断信息不足 → 请求 inspect → 观察后继续判断”。它不声称已有生产 supervisor interface。

`execution_goal_snapshot` 只是 activation 时刻的只读来源快照，不是新的 Mind goal，也不是一个已存在的 authoritative `Intention` object；当前代码库没有后者。Experiment A 不持久化或修改这个 snapshot。

未来生产接线的最小缺口是一个 Execution-owned、公开、受界、脱敏、fail-soft 的 read-only inspection DTO/callable，以及由调用者显式定位目标 execution 的方式。该缺口不在 A0/A 中实现。

## 5. Model seam for Experiment A

### 5.1 Selected interface

应复用 `core.model_client.ModelClient`（`core/model_client.py:32-47`）：

```python
def generate(
    recent_context: list[dict[str, str]],
    user_message: str,
    *,
    system_prompt: str,
) -> str: ...
```

它的 protocol 还包含与 Chat compaction 有关的 `summarize_hot_draft`，这使 interface 略宽；但现有 tests 已经通过 generate-only duck-typed doubles 使用它。这个轻微不匹配不是重构理由。

### 5.2 Configuration path

`build_model_client_from_env` 位于 `core/model_client.py:208-246`：

- `LUMINA_MODEL_MODE=real`；
- provider 必须是 `minimax-anthropic`；
- 读取 `LUMINA_MODEL_API_KEY`、`LUMINA_MODEL_BASE_URL`、`LUMINA_MODEL_NAME`；
- 支持 model name、max tokens、temperature override；
- 缺少配置时返回 `MockModelClient`。

Experiment A 可使用同一 builder/provider，并为 cognitive JSON envelope 设置独立、受界的实验 output budget；不能复用旧 gate 的 8-token client，因为它不足以表达 capability request 或 semantic result。此处不新增 provider。

### 5.3 Structured output and capability calls

`MiniMaxAnthropicModelClient.generate`（`core/model_client.py:97-122`）只构造 model/max_tokens/messages/system/optional temperature，并返回文本。`_extract_text`（`:194-205`）只取第一个 text block。

当前 seam 不支持：

- provider-enforced response schema；
- JSON mode；
- native tools/tool_choice；
- capability-call continuation。

因此 Experiment A 的最小机制是宿主介导的严格文本 envelope，不是把 Execution tool system 搬进 Mind。建议只允许以下闭合 union：

```json
{"type":"capability_request","capability":"recall_memory","query":"..."}
{"type":"capability_request","capability":"inspect_execution"}
{"type":"no_change"}
{"type":"directive","text":"high-level guidance"}
{"type":"decision_intent","intent":"semantic state-change request"}
```

宿主执行 exact-key/type/length validation；未知 capability、额外字段、malformed JSON 或第二次 capability request 都结束 activation failure。不存在动态 registry、import、eval 或 arbitrary callable name。

### 5.4 Mock/test seam and failure

`MockModelClient`（`core/model_client.py:50-67`）返回普通 Chat mock text，不是 cognitive protocol mock。确定性测试应沿用 `tests/test_mind_llm_gate.py:8-30` 的做法：注入一个 queue/scripted generate-only fake，返回预置 envelope 并记录每次完整请求。

真实 adapter 将 transport/non-2xx 转成 sanitized `ModelClientError("Provider request failed.")`，将 invalid JSON/shape/empty text 转成 `ModelClientError("Provider response was invalid.")`（`core/model_client.py:154-205`）。Experiment A runner 应把这些变成 safe activation failure：无 semantic intervention、无 Intention mutation、无 traceback/provider body。

### 5.5 Why not reuse Execution's model/tool seam

Execution 内部 `ModelRequest` / `Model.decide`（`Execution/execution.py:300-329`）与 `DeepSeekModel` 支持 native Actor tools，但不是 `Execution/__init__.py` 的公开 seam，而且语义是对 environment 采取行动。复用它会把 IPython/tool contracts、Actor actions 和另一个 provider implementation 带入 Mind，直接破坏 runtime authority boundary。

结论：复用 core text-model seam；capability interaction 由 `Mind/` 的固定宿主分支实现。

## 6. Trace and persistence audit

| Existing mechanism | What it preserves | Directly reusable for A? | Reason |
|---|---|---|---|
| `Mind/decision_log.py::JsonlDecisionLog` | successful Recall boolean + time + turn | No | schema 是旧 gate 特化；无 activation/context/capability/failure |
| `Execution.execution.EventLog` | append-only typed Execution events + causal refs | No | internal、固定 Execution schemas、含 Actor/provider evidence |
| `Execution.execution.DecisionFrame` | exact Actor model request/response/action | No | internal、unbounded/sensitive、Actor-specific |
| `Execution.execution.Checkpoint` | `ExecutionState` snapshot + durable prefix validation | No | ExecutionState-specific，不是 generic persistence utility |
| `core.draft_store.JsonlDraftStore` | Hot Draft turns/summary | No | Draft owner-specific |
| `core.cold_draft_store.ColdDraftStore` | immutable Cold records/state | No | Cold owner-specific |

`EventLog` 的 append-before-fold、contiguous ID、causal refs、replay validation 是 Experiment C 的有价值参考；`DecisionFrame.actual_request` 证明“记录 exact model-visible request”是可行模式。但 A 不应 import/copy这些类，也不应现在抽取 generic EventLog。

Experiment A 只需返回或在 tests 中记录一个 bounded in-memory trajectory：initial model-visible request、capability request、safe bounded observation、subsequent request、final permitted output。它只作为实验检查证据，不承诺 durable Trace/restart reconstruction；后者属于 Experiment C。

## 7. Minimal Experiment A architecture

### 7.1 Hypothesis

在相同模型、相同初始 activation input 和相同 semantic result schema 下，加入一个受界、只读的 request → observation → continuation 机制，可以让模型在信息不足时主动获取 Memory 或 Execution 信息，并在硬上限内结束 activation。

### 7.2 Activation path

```text
bounded ActivationInput
  {trigger/source ref, execution goal snapshot, coarse status}
        |
        v
ModelClient.generate call #1
        |
        +--> final envelope
        |      NoChange / Directive / DecisionIntent
        |
        +--> one validated capability_request
                 |
                 +--> recall_memory(explicit query, fixed RecallPolicy)
                 |       or
                 +--> inspect_execution() -> bounded projection
                 |
                 v
          safe bounded observation
                 |
                 v
          ModelClient.generate call #2
                 |
                 v
          final semantic envelope only
```

最小候选 limits：

- `max_model_calls = 2`；
- `max_capability_calls = 1`；
- second response 必须是 final；
- activation input、model output、capability query、observation 和 directive/intent 各自有 code-owned char limit；
- Memory policy 由宿主固定；
- provider HTTP timeout 继续由现有 client 保证；
- 不保留无限对话，只从 bounded initial input + one observation 重建第二次 request。

具体数值仍是 Experiment A task card 的实验参数，不应在 A0 中冻结为生产常量。

### 7.3 Structural runtime authority

模型可见的只是 envelope schema 和两个 capability 名称。模型不接触：

- Python callable/object identity；
- `ExecutionOrgan`；
- `ExecutionResult` / `EventLog` / `DecisionFrame`；
- `MagmaMemoryAdapter` 或 backend；
- shell/filesystem/IPython/process/browser；
- pause/resume/interrupt/run/deliver-event；
- Intention mutator；
- generic Tool Registry 或 Skill system。

宿主使用固定 `if/elif` 分派两个只读分支。即使模型输出 `shell`、`ipython`、`write`、`interrupt` 或 arbitrary name，严格 parser 也只能产生 protocol failure，不能转化为执行。

`DecisionIntent` 只是返回值；Experiment A 没有应用它的代码路径。`Directive` 也不持久化、不注入 Execution、不改变 Intention。

### 7.4 Minimum future files

Experiment A 的最小预期 diff：

| File | Change |
|---|---|
| `Mind/experiment_a.py` | 新增 isolated DTO/parser/bounded runner 与两个固定只读 capability adapters/projections |
| `Mind/test_experiment_a.py` | 新增 scripted model、fake Memory/Execution capabilities 和 deterministic tests |

不修改任何既有 `Mind/*.py`，也不修改 `core/`、`Conversation_Memory/`、`Execution/`、Dream、Chat wiring、`docs/CURRENT_STATUS.md` 或 provider code。

当前 `pyproject.toml:1-2` 的 pytest `testpaths` 只有 `tests` 和 `Execution`，所以若测试留在 `Mind/`，targeted command 应显式运行：

```text
python -m pytest Mind/test_experiment_a.py -q
```

随后仍运行 root regression。为只验证 A 而修改 pytest discovery 没有必要。

如果未来生产 supervision 被单独授权，最小外部修改才是 Execution-owned inspect seam/DTO 和 target lookup；那不是 Experiment A 的前置实现。

## 8. Baseline, candidate, and single variable

### 8.1 Repository baseline

当前 `LlmMindGate` 是“单次模型调用、没有 capability observation”的真实生产证据，但它只能输出 Recall boolean。它可以作为历史/架构 baseline，不能直接作为 cognitive A/B implementation，因为 result schema 与任务不同。

### 8.2 Valid Experiment A comparison

| Dimension | Baseline A0-single | Candidate A-cognitive |
|---|---|---|
| Initial input | 相同 bounded activation input | 相同 |
| Model/provider/config | 相同 | 相同 |
| Final schema/parser | 相同 NoChange/Directive/DecisionIntent | 相同 |
| Capability availability | 无 | fixed read-only Memory/Execution request |
| Model calls | exactly 1 | 1 or 2，hard cap 2 |
| Observation feedback | 无 | 最多一个 bounded observation |
| State mutation | 无 | 无 |

单一变量：**是否允许一次宿主介导的只读 information-acquisition round，并把该 observation 反馈给同一个模型后继续判断。**

必须保持不变：model/provider、temperature、output budget、initial input、fixtures、final semantic schema、failure policy，以及 Memory/Execution 返回的数据内容。不要同时改变 prompt family、provider、Memory ranking、threshold、Execution architecture 或 Directive delivery。

## 9. Deterministic/mock test design

使用 generate-only scripted fake：

```text
responses = [capability_request_envelope, final_envelope]
recorded_calls = [exact call #1, exact call #2]
```

使用 structural fake `MemoryRetriever` 记录 `(query, policy)` 并返回 fixed `MemoryContext`；使用 zero-argument execution inspect fake 返回 fixed bounded projection。两者都没有 mutation methods。

至少覆盖：

1. 首轮直接 `NoChange`：一次 model call、零 capability call。
2. Memory path：首轮显式 query → fake 恰好调用一次且 policy 完全匹配 → 第二轮看到 bounded observation → final result。
3. Execution path：首轮 inspect → 第二轮只看到 goal/status/outcome/failure projection，不看到 organ/events/provider data。
4. Observation effect：同一 fixture 的 baseline 只能单轮结束；candidate 的 final semantic result 由第二轮 observation 改变。
5. 三种合法 final output：`NoChange`、`Directive`、`DecisionIntent`。
6. malformed JSON、extra keys、unknown capability、空/超长 query、超长 output：safe failure。
7. 第二次仍请求 capability：立即 limit failure；绝不发生第三次 model call。
8. model/provider exception：无 semantic result、无 mutation、safe failure detail。
9. `MemoryContext.safe_error_code`：形成 bounded unavailable observation；真正的 Memory/Execution callable exception：结束为 conservative activation failure，不泄露 traceback。
10. 同一 scripted input 重跑得到 byte-stable semantic result 与相同 call sequence。

## 10. Concrete acceptance criteria for Experiment A

Experiment A 只有在以下全部满足时 PASS：

1. 一个 activation 从明确、受界、immutable input 开始，并在至多两次 model calls 内结束。
2. 模型能够在信息足够时直接返回 `NoChange`，不会被强迫干预。
3. 模型能够在信息不足时请求且只请求一个合法只读 capability。
4. capability query/observation 在宿主侧受界；Memory 使用 exact fixed `RecallPolicy`。
5. 下一次 exact model-visible request 包含 safe observation，并且该 observation 能改变 scripted trajectory 的最终判断。
6. final result 是 strict `NoChange | Directive | DecisionIntent`；failure 不伪造其中任何一种。
7. `Directive`/`DecisionIntent` 不触发持久化、Execution action、Intention mutation 或 Chat routing。
8. model-visible capability set 恰好是 `recall_memory` / `inspect_execution`；没有 shell/filesystem/IPython/process/browser/Execution mutation surface。
9. 未知/恶意 capability name 不能到达任何 callable。
10. 任何 model、parse 或 capability failure 都保留当前 Intention，并返回 safe、可检查的 activation failure。
11. tests 能检查 exact initial request、capability request/result、subsequent request 与 final permitted output，但不要求或存储 hidden CoT。
12. targeted deterministic tests、现有 Mind gate regressions、root regressions 和 `git diff --check` 通过。
13. 无 `Mind/` 外生产改动；无 generic registry/framework；无 Nervous/Focus/Directive persistence/Intention mutation。

本实验明确不证明：真实模型“更聪明”、生产 live Execution supervision、durable Mind Trace、crash/restart reconstruction、one-shot Directive delivery 或行为价值。这些分别属于后续真实-model evidence、生产 integration、Experiment C、D、E。

## 11. Risks of accidentally turning Mind into Planner or Actor

| Risk | How it happens | Required guardrail |
|---|---|---|
| Planner drift | Directive 变成文件/命令/步骤清单 | final schema 不提供 steps/code/tool fields；prompt 与 fixtures 要求 high-level steering；不加 benchmark regex patch |
| Actor authority leak | 把 `ExecutionOrgan`、ToolHost、IPython 或 callable registry 传给模型 | 模型只见固定 capability names 与数据 projection；宿主 fixed branch dispatch |
| Hidden execution through DecisionIntent | 自然语言 intent 被立即应用 | Experiment A 只返回 typed semantic value；没有 mutator/application path |
| Unbounded cognition | 重复 capability calls、累积 transcript | one capability、two model calls、fresh bounded continuation |
| Memory ownership bypass | import backend/MAGMA/BGE/FAISS | 只依赖 `MemoryRetriever` + public DTOs |
| Execution ownership bypass | import `_bounded_context`/EventLog/DecisionFrame | 只读 exported `ExecutionState`，在宿主做最小 projection |
| Model-selected budgets | 模型输出 top_k/depth/nodes/chars | capability envelope 不含 policy fields；policy 完全 code-owned |
| Trace becomes hidden CoT store | 要求模型输出或保存 reasoning | 只记录 externally observable requests/responses/calls/results |
| Old gate regression | 替换/扩写 `MindDecision` 或 MessageRuntime wiring | Experiment A 新建独立 module；不接生产 Chat |
| Semantic “high-level” false confidence | strict JSON 仍可在 text 中写低层步骤 | A 只能以 prompt/fixture/human review 检查语义；不要声称 parser 能证明不是 Planner |

## 12. Missing seams and promotion boundary

非阻塞、但必须显式记录的缺口：

1. 没有 Execution-owned bounded/redacted supervisor DTO/callable。
2. 没有 live execution lookup/registry；当前 API run 后 shutdown。
3. core `ModelClient` 没有 native structured output/tool calls。
4. `MockModelClient` 不是 cognitive protocol mock；A 需 scripted fake。
5. 旧 Mind audit 不能重建 cognitive activation。
6. 没有 durable Mind Trace、Directive delivery、Intention transition。
7. 没有生产 Mind activation trigger/Nervous seam。

这些缺口分别属于后续 production seam、Experiment B/C/D 或更晚阶段，不应为了让 Experiment A “看起来完整”而一次实现。

## 13. Verdict

```text
READY_FOR_EXPERIMENT_A
```

理由：现有 core model seam 与 Memory facade 足够；Execution 的 exported state 足以由宿主做隔离实验所需的只读受界 projection；scripted model 和 capability fakes 可以确定性证明 request → observation → continuation → semantic result。Experiment A 不需要也不应修改 `Mind/` 外生产代码。
