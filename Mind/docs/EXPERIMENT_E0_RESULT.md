# Mind Experiment E0 — Real Execution Decision Steering Seam Result

- 日期：2026-08-30
- 范围：durable Directive 到真实 Execution Root model decision 的最小集成实验
- production Chat / Mind routing：未接线
- behavioral value：未测试
- 最终结论：`EXPERIMENT_E0_PASS`

## 1. Claim and verdict

本实验只验证：

> 一个已经通过 Experiment D durable `ISSUED → APPLIED` 的固定 Mind Directive，可以作为纯数据进入一次真实 Lumina Execution Root 的 model-visible request；同一 decision 的 retry/restart 可重建它，后续不同 decision 看不到它，同时 authoritative goal、工具表面与 Execution authority 不变。

所有 E0 目标测试、A–D 回归、Execution / Execution_lab2 回归、production Mind gate 回归、root 回归与双路 code review 均通过，因此 verdict 为：

```text
EXPERIMENT_E0_PASS
```

该 verdict 不表示 Mind 提升了 Execution、trigger policy 合理、production steering 已完成、Nervous/Focus 已实现，或 Mind MVP 已具有行为价值。

## 2. Source-level integration audit

### 2.1 Supported surface and actual decision path

当前受支持 public facade 是：

| File | Symbol | Input / output |
| --- | --- | --- |
| `Execution/organ.py:21` | `ExecutionOrgan` | 拥有一个 durable Root 或 Child `AgentProcess`。 |
| `Execution/organ.py:108` | `ExecutionOrgan.run_goal` | 输入 goal、completion spec；返回 `ExecutionResult`。 |
| `Execution/execution.py:302` | `ModelRequest` | `context / available_tools / source_event_refs / native_tool_continuation / model_visible_context_limit`。 |
| `Execution/execution.py:314` | `Model.decide` | 输入真实 `ModelRequest`；输出 typed action 或 `NativeModelDecision`。 |
| `Execution/execution.py:665` | `DecisionFrame` | 记录 decision identity、goal、state version、actual request、actual tools、model/provider evidence 与 action。 |

真实 Root 路径是：

```text
ExecutionOrgan.run_goal
→ AgentProcess.run / resume
→ RootAgentProcess._drive
→ _build_model_request
→ _bounded_context
→ Model.decide(the exact ModelRequest)
→ DecisionFrame(actual_request=that same request)
→ EventLog.append(MODEL_DECISION)
```

Exact symbols：

```text
Execution/organ.py:108       ExecutionOrgan.run_goal
Execution/execution.py:3306  RootAgentProcess.run
Execution/execution.py:3967  RootAgentProcess._drive
Execution/execution.py:3003  _build_model_request
Execution/execution.py:2686  _bounded_context
Execution/execution.py:4039  DecisionFrame construction
Execution/execution.py:4070  durable MODEL_DECISION append
```

`Execution_lab2/execution.py`、`deepseek_model.py` 与 `ipython_control.py` 只是通过 `sys.modules` 指向受支持 `Execution.*` 的 compatibility aliases；它们不是第二套实现，也没有可独立接入的 wrapper seam。E0 没有依赖或修改 `Execution_lab2`。

### 2.2 Existing Root context and provider projection

`_bounded_context()` 生成受界 canonical JSON，包含：

```text
goal
completion_spec
state identity / status / decision_count
latest observation
incoming event / lifecycle
bounded Child facts when present
eligible IPython Child-delegation declaration
```

`Execution/deepseek_model.py:157` 的 `DeepSeekModel.decide()` 在 fresh decision 中把 `request.context` 放入 provider user message；native continuation 则根据前一帧 request context、raw provider response 与当前 outcome 构造 user/assistant/tool messages。provider wire request 会进入 `DecisionFrame.provider_wire_request`。

当前 frozen model surfaces 是：

```text
Root:  IPython + Wait + ClaimComplete
Child: IPython + Wait + Return
```

### 2.3 Root / Child sharing and the missing seam

Root 与 Child 共用同一 `_drive → _build_model_request → Model.decide → DecisionFrame` 路径，role 只改变 state projection、tool surface 与 delegation eligibility。

E0 前没有 decision-scoped advisory input：

- `ExecutionOrgan` 不接受 optional decision context；
- `_build_model_request` 是 private；
- `ModelRequest` 不携带 pre-call `decision_id`；
- `DecisionFrame` 仅在 model call 之后构造；
- `capability_declarations` 属于 capability exposure，不是 advisory seam。

Mind-side `Model` wrapper 不可用：wrapper 可以向下游 model 发送改写后的 request，但 `DecisionFrame.actual_request` 仍会记录 `_drive` 构造的原 request，从而形成双重 request authority。全局 monkeypatch private builder 同样不满足 concurrency、ownership 与 audit fidelity。

因此现有 seam 不足，但缺口只需要一个窄的、optional、Root-only data input；不需要 Execution refactor、Runtime hook、provider change 或新 framework。

## 3. Codebase-design decision

在编码前并行比较了三个接口：

1. **Static call data**：一次 Root drive 接收 `(decision_id, rendered advisory)`；
2. **Host callback**：Execution 在 pre-model seam 调用 `decision_id → str | None`；
3. **Explicit request envelope**：引入专用 DTO，作为一次 Root decision input。

选中的是第 1 个的最小形态，并吸收第 3 个方案的 continuation-cleanup 规则：

```text
caller-prepared immutable strings
→ one ExecutionOrgan run_goal/resume call
→ exact current Root decision only
→ real ModelRequest
```

理由：

- callback 是可执行 hook，可阻塞、抛错或逐步演化成 trigger/Nervous；
- DTO 在当前只有两个 frozen string fields 时没有额外收益；
- tuple data 不包含 callable 或 object authority；
- real request construction 与 one-shot cleanup 仍由 Execution 深模块拥有；
- `default=None` 可走 byte-identical baseline；
- 不需要修改 provider、EventLog schema、DecisionFrame schema、Actor state 或 Intention。

## 4. Baseline, candidate, and single variable

| Dimension | Baseline | Candidate |
| --- | --- | --- |
| Execution model/provider | 同一 capture model / 同一 DeepSeek adapter fake transport | 不变 |
| durable Execution prefix | 相同 `EXECUTION_STARTED` facts | 不变 |
| goal / completion spec | 相同 | 不变 |
| state / source refs | 相同 | 不变 |
| tools / IPython surface | 相同 | 不变 |
| Actor lifecycle / Runtime | 相同 | 不变 |
| Mind cognition | 不运行 | 不运行 |
| D lifecycle | fixed ISSUE → APPLIED(A) | 相同 |
| model-visible request | existing context | existing context + one advisory field |

唯一变量是：

> 当前真实 Execution Root decision 的 model-visible request 是否包含一条已绑定 Directive advisory block。

没有比较 success rate、tokens、quality、stagnation 或 task completion behavior。

## 5. Implemented surface

### 5.1 Mind experiment bridge

`Mind/execution_steering_experiment.py:15`：

```python
decision_advisory_from(application) -> tuple[str, str] | None
```

它只接受 exact `DirectiveApplication`，验证既有 D bounds，再调用唯一 D rendering：

```text
Mind/directive.py:31  DirectiveApplication.as_model_context()
```

输出只是：

```text
(application.decision_id, application.as_model_context())
```

该模块不 import `Execution`，不接收 callable，也没有 `ExecutionOrgan`、`AgentProcess`、`ToolHost`、Runtime、IPython、shell、filesystem 或 process method。

固定 E0 Directive 是：

```text
Re-check assumption X before continuing.
```

### 5.2 Supported Execution optional data seam

`Execution/organ.py` 最小新增：

```text
next_root_decision_id                     read-only Actor-local next identity
run_goal(..., decision_advisory=None)    one-call optional Root data
resume(..., decision_advisory=None)      retry/restart input; Child rejects non-None
```

`Execution/execution.py` 最小新增：

```text
DecisionAdvisory = tuple[str, str]
_decision_advisory_for(...)
_context_with_decision_advisory(...)
_context_without_decision_advisory(...)
```

`_drive()` 统一生成一次：

```python
decision_id = f"decision-{state.decision_count + 1:06d}"
```

同一个 value 同时用于 input match 与 `DecisionFrame.decision_id`。只有 exact tuple、exact built-in strings、matching ID、non-blank bounded block 和 Root role 才能进入当前 request。数据在第一次 loop iteration 后清除；它不能漂移到 B。

model-visible field 是：

```json
{
  "mind_supervisor_directive": "[Mind Supervisor Directive]\n..."
}
```

这是现有 canonical JSON 的一个 top-level field；它不是 system prompt、tool schema、goal、state 或 persistent transcript。

没有修改：

```text
Execution/deepseek_model.py
Execution EventLog / DecisionFrame schema
Execution ToolHost / IPython
Execution_lab2
core/ / MessageRuntime / API
Memory / Dream
production Mind gate
```

## 6. Fixed D lifecycle without Mind cognition

E0 没有调用 `run_activation()`，没有 model generation，也没有让 cognitive Mind 决定是否介入。

测试 host 直接用现有 `MindTrace` grammar 写入固定、合法、可重放的事实：

```text
ACTIVATION_STARTED
→ MODEL_OUTPUT_RECORDED(fixed Directive envelope)
→ MIND_DIRECTIVE_ISSUED
```

然后只调用 D 已验证的 public delivery operation：

```text
ExecutionOrgan.next_root_decision_id
→ prepare_for_execution_decision(trace, decision_id, eligible=True)
→ durable MIND_DIRECTIVE_APPLIED(A)
→ DirectiveApplication
→ decision_advisory_from(application)
→ ExecutionOrgan.run_goal(...)
```

因此 E0 测的是 D delivery data 到真实 Execution request 的 seam，不是 Mind intelligence。

## 7. Exact request and DecisionFrame evidence

Baseline 与 candidate 从两个 byte-equivalent `EXECUTION_STARTED` prefixes 恢复，使用相同：

```text
execution_id
root_actor_id
goal
completion spec
state version
source event refs
model identifier
tool surface
workspace evidence
```

`test_e0_1_to_3_*` 证明：

```text
model.received_requests[0] is DecisionFrame.actual_request
```

并且：

```text
decoded candidate context
- mind_supervisor_directive
= decoded baseline context
```

候选 raw context 也精确等于：

```python
json.dumps(
    baseline_document | {
        "mind_supervisor_directive": application.as_model_context()
    },
    ensure_ascii=False,
    separators=(",", ":"),
)
```

将 candidate `ModelRequest.context` 替换为 baseline context 后，整个 frozen `ModelRequest` 与 baseline exact equal；将 candidate frame 的 request 替换后，整个 `DecisionFrame` 也 exact equal。因此不存在隐藏的第二变量。

实际 block 完全复用 D rendering：

```text
[Mind Supervisor Directive]
Treat this high-level guidance as a strong advisory prior, not an order or execution plan.
Re-check assumption X before continuing.
```

## 8. Provider-level one-shot evidence

只删除 decision B 自己的新 field 不够：`DeepSeekModel` native continuation 会使用 A 的 `frame.actual_request.context` 作为 previous user context。

因此 `_build_model_request()` 在形成 `NativeToolContinuation` 时只移除上一 request 的 `mind_supervisor_directive` field，再把 unchanged base context 交给现有 provider adapter。provider 未修改。

使用真实 `DeepSeekModel.decide()` 与 local fake transport 的两决策证据是：

```text
decision A actual ModelRequest: block present
decision A provider wire request: block present

decision B actual ModelRequest: block absent
decision B NativeToolContinuation.previous_model_context: block absent
decision B provider wire request: block absent from every message
```

两个 `DecisionFrame.provider_wire_request` 与 fake transport 实际捕获 payload 的 durable snapshots exact equal。

## 9. Stable decision identity, retry, and restart

当前真实 `DecisionFrame` identity 是 Actor/EventLog-local：

```text
decision-{state.decision_count + 1:06d}
```

`decision_count` 只在 durable `MODEL_DECISION` 被 fold 时增长。因此：

- model call 前或 `MODEL_DECISION` append 前失败：count 不变；
- restart 从 durable EventLog 恢复同一个 count；
- 同一 Actor 的 retry 重新得到同一 ID；
- A durable 后，B 得到下一 ordinal。

E0 直接复用这个现有 identity；没有为 retry 生成 random ID，也没有新增 registry/event identity service。其 scope 明确是一个 Root Actor/EventLog；没有声称单独的 `decision-000001` 在全仓全局唯一。

Retry/restart fixture 证明：

```text
ISSUE
→ APPLIED(decision-000001)
→ discard Mind objects
→ reopen_for_delivery
→ same application, no second APPLIED
→ real Execution request built
→ simulated model failure before MODEL_DECISION persistence
→ reopen Execution EventLog
→ next_root_decision_id == decision-000001
→ reopen Mind trace
→ same application
→ exact same Directive-bearing ModelRequest
```

Mind JSONL bytes 在第一次 APPLIED 后保持不变；APPLIED event 始终恰好一条。

## 10. Intention, tools, and authority invariants

### Intention / goal

Baseline 与 candidate 的：

```text
DecisionFrame.goal
ExecutionState.goal
completion_spec
state_version
source_event_refs
```

全部 exact equal。E0 没有 Intention mutator、Activity goal change、pause/resume policy 或 DecisionIntent application。

### Tools / capabilities

Baseline 与 candidate 的 `available_tools` 和 `actual_tools_exposed` exact equal。Directive 没有进入 tool schema；DeepSeek provider tools payload 不变。Root 仍只有：

```text
IPython + Wait + ClaimComplete
```

### Mind authority

静态 AST 与 dynamic boundary tests 证明 Mind bridge：

- 不 import Execution；
- 不持有 callable；
- 不包含 `run_goal / resume / interrupt / spawn_child`；
- 不包含 AgentProcess、ExecutionOrgan、ToolHost、Runtime 或 PersistentIPython；
- 只返回 exact tuple of two built-in strings。

`run_child()` 与 `open_child()` 不接受 advisory。public `ExecutionOrgan.resume()` 和 underlying process `resume()` 在 Child 收到 non-None advisory 时都会在 model construction 前显式拒绝。因此虽然 Root/Child 共用 request builder，E0 data seam 是 structural Root-only。

## 11. Conservative failure and exact baseline

以下 input 都不注入，并继续原 baseline request：

```text
None / unavailable application
non-DirectiveApplication object
wrong decision ID
non-tuple / tuple subclass
non-built-in string fields
blank or non-normalized text
over E0 rendered-data bound
valid advisory that cannot fit the current Execution context budget
```

`default=None` 对照不只比较 semantic values；从相同 durable start facts 执行后，完整 `ModelRequest`、`ExecutionEvent` tuples 与 persisted Execution JSONL bytes 全部 exact equal。

Context-budget overflow 在第一次 review 中曾抛 `ValueError` 并阻断 Execution。该 blocker 被转成 RED test；现在直接返回原 context，保留 exact baseline。这个 fail-soft 分支不是成功 delivery 证据；E0 PASS 使用的是能完整装入 bound 的固定 Directive。

## 12. Acceptance evidence mapping

| ID | Evidence |
| --- | --- |
| E0-1 | Baseline `ModelRequest` 被 capture model 与 `DecisionFrame.actual_request` 作为同一对象捕获。 |
| E0-2 | Candidate decoded/raw request 是 baseline + exactly one D-rendered field；替换该字段后整个 request/frame exact equal。 |
| E0-3 | Directive 进入真实 `ExecutionOrgan → AgentProcess → Root Model.decide` 路径。 |
| E0-4 | B 的 request、native continuation 与 provider wire messages 均无 Directive。 |
| E0-5 | `MODEL_DECISION` 前失败后，same A 重新构造 exact same request；无第二 APPLIED。 |
| E0-6 | 丢弃并 reopen Mind + Execution runtime objects 后，same A 仍重建；durable bytes 不变。 |
| E0-7 | `DecisionFrame.goal`、`ExecutionState.goal`、completion spec 与 state version 不变。 |
| E0-8 | Root tools、provider tools、IPython surface 与 source refs 不变。 |
| E0-9 | Mind bridge AST 无 Execution authority；Child model paths不能接收 advisory。 |
| E0-10 | Omitted keyword 与 explicit `None` 的 request/events/JSONL byte exact。 |
| E0-11 | malformed/unavailable/mismatch/over-bound/over-budget 均 conservative baseline；Child input显式拒绝。 |
| E0-12 | A/B/C/D、Execution、Execution_lab2、production Mind gate 与 root regressions 全通过。 |

## 13. TDD progression

`/tdd` 使用了垂直 RED → GREEN：

1. First RED：

   ```text
   ModuleNotFoundError: Mind.execution_steering_experiment
   ```

   最小 GREEN：只增加 inert `DirectiveApplication → tuple[str, str]` bridge。

2. Request seam RED：

   ```text
   TypeError: ExecutionOrgan.run_goal() got an unexpected keyword argument
   ```

   最小 GREEN：Root-only call-scoped data 进入真实 builder/frame。

3. One-shot / retry / restart slices：锁定 provider continuation、stable identity 与 durable APPLIED reconstruction。

4. Review-driven RED：

   ```text
   valid over-budget Directive → ValueError
   Child resume(non-None advisory) → not explicitly rejected
   ```

   最小 GREEN：over-budget 返回 original context；Root facade 与 process seam 都拒绝 Child advisory。

最终 E0 target：

```text
14 passed
```

## 14. Required skills and Ponytail

### `/codebase-design`

先完成 source audit 与 user-visible problem frame，再用 Design-It-Twice 并行比较三种不同接口。选中的 deep seam 把 request fidelity、identity match、bounds、one-shot cleanup 与 Child boundary 留在 Execution；Mind 只拥有 D lifecycle/rendering 与 inert projection。

### `/tdd`

按 exact baseline → one field → real frame/provider → one-shot → retry/restart → authority/failure 顺序推进。review finding 先转成 failing tests，再修改实现。

### `/code-review`

Standards 与 E0 Spec 两个独立 reviewer 使用任务开始时保存的 `Execution/execution.py`、`organ.py` snapshots 作为 fixed point。

Initial review：

```text
Standards: 1 blocking + 1 nonblocking
Spec:      1 blocking
```

共同 blocker 是 valid-but-over-budget advisory 会阻断 Execution；Standards 另指出 Child public resume seam 应显式收紧。两项均已 RED → GREEN 修复。

Re-review：

```text
Standards: PASS — 0 remaining findings
Spec:      PASS — 0 blocking findings
```

### Ponytail

遵循 Think Before Coding、最小可证伪 vertical slice、stdlib-first、optional default、测试先行、无 speculative abstraction。最终没有 Manager、Registry、Factory、callback hook、event bus、scheduler、trigger、Nervous、provider 或新 persistence layer。

## 15. Validation

Review fixes 后的最终命令：

```text
.venv\Scripts\python.exe -m pytest Mind\test_execution_steering.py -q
14 passed

.venv\Scripts\python.exe -m pytest Mind -q
119 passed

.venv\Scripts\python.exe -m pytest Execution -q
5 passed

.venv\Scripts\python.exe -m pytest tests\test_execution_api.py -q
5 passed

.venv\Scripts\python.exe -m pytest Execution_lab2 -q
147 passed

.venv\Scripts\python.exe -m pytest \
  tests\test_mind_gate.py \
  tests\test_mind_llm_gate.py \
  tests\test_mind_gate_shadow.py \
  tests\test_mind_gate_operational.py \
  tests\test_mind_promotion_controls.py -q
24 passed

.venv\Scripts\python.exe -m pytest -q
338 passed, 24 skipped, 2 existing upstream warnings

.venv\Scripts\python.exe -m py_compile \
  Mind\execution_steering_experiment.py \
  Mind\test_execution_steering.py \
  Execution\execution.py \
  Execution\organ.py
PASS
```

两条 root warnings 是既有 upstream warning：MAGMA `ast.Str` deprecation 与 sentence-transformers method rename。

## 16. Files and scope

本任务新增：

```text
Mind/execution_steering_experiment.py
Mind/test_execution_steering.py
Mind/docs/EXPERIMENT_E0_RESULT.md
```

本任务最小修改：

```text
Execution/execution.py
Execution/organ.py
```

没有修改：

```text
Execution/deepseek_model.py
Execution_lab2/
core/
Conversation_Memory/
Dream/
production Mind gate
docs/CURRENT_STATUS.md
API / Chat / MessageRuntime
```

仓库在 E0 开始前已有大量 unrelated tracked/untracked changes，且当前 `Execution/` 与前序 `Mind/` experiment tree 在 root Git 视图中本来就是 untracked。E0 保存了两个受改 Execution files 的 task-start snapshots，并用 `git diff --no-index` 限定 review scope；未归因或改写其他工作。

没有 commit、push、rebase、reset 或 history rewrite。

Final Git safety evidence：

```text
git diff --check
PASS (exit 0; output only contains pre-existing LF→CRLF warnings)

git status --short
captured; repository remains dirty from the pre-task baseline

git diff --no-index --check task-start-execution.py Execution/execution.py
git diff --no-index --check task-start-organ.py Execution/organ.py
git diff --no-index --check NUL Mind/execution_steering_experiment.py
git diff --no-index --check NUL Mind/test_execution_steering.py
git diff --no-index --check NUL Mind/docs/EXPERIMENT_E0_RESULT.md
NO whitespace errors
```

上述 no-index commands 的 exit 1 只表示比较对象内容不同；没有 whitespace-error output。

## 17. Limitations and non-claims

- `decision-N` 是一个 Actor/EventLog 内稳定，不是全局唯一；E0 没有建立 generic identity registry。
- E0 使用 deterministic capture model 与真实 DeepSeek adapter + fake transport；没有网络 provider call，也不测试 semantic quality。
- E0 fixed Directive 能完整进入 2,000-char context。valid-but-over-budget input fail-soft 为 baseline，不构成 successful-delivery evidence。
- APPLIED 仍遵循 D 定义：durably bound to a decision context，不表示 model call completed、Actor accepted advice 或 task succeeded。
- 没有 multiple Directive arbitration、priority、TTL、cancellation、replacement、actor targeting 或 concurrent multi-writer claiming。
- eligibility 由 experiment host 显式提供；没有 trigger、stagnation detector、AVO policy、scheduler、Nervous 或 Focus。
- 没有 behavioral A/B、success-rate、token、quality 或 task-completion claim。
- 没有 Intention mutation、DecisionIntent application、Chat→Execution routing、production Mind wiring、Memory change 或 provider change。

## 18. Permitted claim and final verdict

允许的结论仅为：

> 已验证 Lumina 的 durable one-shot Mind Directive 可以通过纯数据边界真实进入一次 Execution Root model decision context，并在 retry/restart 下保持 decision-scoped、one-shot 和 advisory，同时不改变 Intention、工具权限或 Execution authority。

Final verdict：

```text
EXPERIMENT_E0_PASS
```
