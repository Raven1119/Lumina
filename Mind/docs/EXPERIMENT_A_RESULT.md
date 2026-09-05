# Mind Experiment A — Bounded Cognitive ReAct Result

- 日期：2026-08-30
- 范围：隔离式、确定性 cognitive-loop 机制实验
- 生产接线：无
- 最终结论：`EXPERIMENT_A_PASS`

## 1. Claim and verdict

本实验验证的唯一 claim 是：

> 在当前 Lumina seam 上，一个受界且没有执行权的 cognitive Mind，可以选择性获取最多一次只读信息，并利用 observation 继续一次认知判断。

实现、测试、回归和双路 code review 均满足任务卡 acceptance gate，因此 verdict 为：

```text
EXPERIMENT_A_PASS
```

这个 verdict 不表示 Mind 已完成、比单次模型更智能、能够改善真实 Execution、已经 production-ready，或后续 Experiment B/C/D/E 已经成立。

## 2. Hypothesis, baseline, candidate, and single variable

Hypothesis：在模型、initial input、final semantic schema 和 failure policy 保持相同的情况下，允许一次宿主介导的只读 information-acquisition round，可以形成稳定的：

```text
model judgment
→ one read-only capability
→ bounded observation
→ one continuation judgment
→ final semantic result
```

| Dimension | Baseline | Candidate |
|---|---|---|
| Activation | 同一个 immutable `ActivationInput` | 同一个 immutable `ActivationInput` |
| Model | 同一个 deterministic `ObservationAwareModel` 实例 | 同一个实例 |
| System prompt | 完全相同 | 完全相同 |
| Memory/Execution dependencies | 同一组 injected data/dependencies | 同一组 |
| Final schema | `NoChange / Directive / DecisionIntent` | 相同 |
| Information round | `allow_information_acquisition=False` | `True` |
| Model calls | 1 | 1 或 2，hard cap 2 |
| Capability calls | 0 | 0 或 1，hard cap 1 |

唯一实验变量是：

> 是否允许一次宿主介导的只读 information-acquisition round。

A4 的同模型对照结果为：

```text
Baseline
same ActivationInput
→ NoChange

Candidate
same ActivationInput
→ recall_memory("current direction assumption")
→ "The current direction depends on a disproven assumption."
→ Directive("Re-evaluate the disproven assumption before continuing.")
```

这只证明 observation-conditioned continuation 机制可成立；scripted trajectory 不证明真实模型的认知质量。

## 3. Implemented surface

新增文件：

- `Mind/experiment_a.py`
- `Mind/test_experiment_a.py`
- `Mind/docs/EXPERIMENT_A_RESULT.md`

`Mind/experiment_a.py` 的唯一 operation seam 是：

```python
run_activation(
    activation,
    *,
    model,
    memory_retriever,
    inspect_execution,
    allow_information_acquisition=True,
)
```

公共实验 DTO 均为 frozen dataclass：

```text
ActivationInput(trigger, execution_goal_snapshot, execution_status)
ExecutionObservation(goal, status, recent_outcome, failure)
NoChange()
Directive(text)
DecisionIntent(intent)
ActivationFailure(code)
```

没有修改或扩展 production `MindDecision { recall: bool }`。

## 4. Current Lumina seams reused

### Model

复用 `core.model_client.ModelClient.generate(...) -> str`。Experiment A 没有新增 provider、native tool schema 或 JSON framework。结构化行为由严格的 host-side JSON parser 实现。

### Conversation Memory

只依赖公开 Lumina-owned seam：

```text
Conversation_Memory.adapter.interfaces.MemoryRetriever.recall(...)
Conversation_Memory.adapter.models.RecallPolicy
Conversation_Memory.adapter.models.MemoryContext
```

没有 import MAGMA、FAISS、BGE、Memory backend、graph object 或 private Recall modules。

### Execution

只注入零参数 host-side callable，并要求其返回 frozen `ExecutionObservation`。模型只看到白名单 projection；模型看不到 callable identity、`ExecutionOrgan`、`ExecutionState`、EventLog、DecisionFrame、ExecutionResult、provider data、tool schemas 或 mutation methods。

Experiment A 没有实现正式 Execution supervisor interface，也没有 live execution lookup。

## 5. Straight-line candidate architecture

实现没有 loop/state machine/registry：

```text
validate and explicitly project ActivationInput
→ ModelClient.generate #1
   ├─ final semantic envelope → return
   └─ one validated capability request
        ├─ MemoryRetriever.recall(explicit query, fixed policy)
        └─ inspect_execution() → ExecutionObservation
              ↓
        bounded safe observation
              ↓
        rebuild continuation request
              ↓
        ModelClient.generate #2
              ↓
        final semantic envelope only
```

第二轮再请求 capability 时返回 `ActivationFailure("capability_limit_exceeded")`，没有第三次调用路径。

## 6. Hard bounds

所有数值均为 experiment-local、code-owned constants，模型不能提供或修改。

| Limit | Value |
|---|---:|
| model calls | 2 |
| capability calls | 1 |
| trigger | 1,000 chars |
| execution goal snapshot / observed goal | 2,000 chars |
| execution status | 200 chars |
| raw model output | 2,000 chars，JSON parse 前检查 |
| Memory query | 500 chars |
| capability observation JSON | 3,000 chars |
| Directive text | 1,000 chars |
| DecisionIntent text | 1,000 chars |
| recent execution outcome | 1,000 chars |
| execution failure | 500 chars |
| incoming Memory safe-error code | 100 chars；模型侧统一投影 |

固定 `RecallPolicy`：

```text
top_k=5
max_chars=2000
max_evidence_items=3
max_graph_depth=1
max_nodes=20
final_min_score=0.144
relation_surfaces=None
```

Continuation 只由已受界的 original activation、validated request、safe observation 和 `further_capability_allowed=false` 重建；`recent_context` 始终是空列表，不累积隐藏 transcript。

## 7. Strict protocol and semantic results

第一轮闭合 union：

```json
{"type":"capability_request","capability":"recall_memory","query":"..."}
{"type":"capability_request","capability":"inspect_execution"}
{"type":"no_change"}
{"type":"directive","text":"..."}
{"type":"decision_intent","intent":"..."}
```

第二轮只接受后三种 final envelope。

Parser 行为：

- JSON object only；
- exact、case-sensitive `type`；
- exact allowed keys；
- duplicate keys rejected；
- non-standard JSON constants rejected；
- extra/missing fields rejected；
- blank/oversized query or semantic text rejected；
- unknown capability rejected；
- deeply nested JSON 的 platform-dependent `RecursionError` 被保守转换为 failure；
- 无 `eval`、`exec`、dynamic import、reflection dispatch 或 arbitrary callable lookup。

`Directive` 与 `DecisionIntent` 只是 return values。没有 persistence、application、Execution injection、Intention mutation、pause/resume 或 Chat routing。

## 8. Capability and authority proof

模型可选择的 capability 名称恰好是：

```text
recall_memory
inspect_execution
```

宿主只有两个 literal branches：

```text
if recall_memory:
    MemoryRetriever.recall(...)
elif inspect_execution:
    inspect_execution()
else:
    ActivationFailure
```

测试证明 `shell` 等未知名称只能得到 protocol failure，不能到达 Memory 或 Execution fake。模型不能接触或调用：

```text
shell
filesystem mutation
IPython
browser/process
run_goal/spawn_child
interrupt/resume/deliver_event
Intention mutator
```

Authority 结论严格限定为 Experiment A host/model surface。注入 callable 是否由未来 production host 正确实现为只读 seam，仍需独立生产集成审查和 Experiment B；本实验没有把任意 callable 交给模型，也没有把这种未来工作伪装成已完成。

## 9. Safe observation and failure semantics

Memory success 只投影：

```text
status
rendered_evidence
truncated
safe_error_code
```

当 `MemoryContext.safe_error_code` 非空时，原 code 和任何 evidence 都不直接进入模型；它统一成为：

```json
{
  "capability": "recall_memory",
  "rendered_evidence": "",
  "safe_error_code": "memory_unavailable",
  "status": "unavailable",
  "truncated": false
}
```

Execution 只投影 `goal/status/recent_outcome/failure`。

以下均返回只有 safe code 的 `ActivationFailure`：

- invalid activation / invalid experiment flag；
- provider/model exception；
- malformed、unknown、oversized model output；
- capability unavailable or limit exceeded；
- Memory callable exception；
- Execution inspect exception；
- invalid/oversized Memory or Execution observation。

Exception message、traceback、provider body、credentials、local paths 和 backend detail 不进入 result 或 continuation。任何 failure 都没有 semantic intervention 或 state-mutating path。

## 10. Deterministic trajectories and acceptance evidence

| ID | Evidence |
|---|---|
| A1 | Direct `NoChange`；1 model，0 capability |
| A2 | explicit Memory query、exact fixed policy、bounded observation 进入 call #2、final Directive |
| A3 | Execution inspect 恰好一次；call #2 只有白名单 projection |
| A4 | 同一个 conditional fake、activation、dependencies 和 system prompt；只切换 information round；`NoChange → Directive` |
| A5 | `NoChange`、`Directive`、`DecisionIntent` 三种 legal final 均覆盖 |
| A6 | unknown `shell` capability；0 dispatch |
| A7 | invalid JSON、Markdown fence、non-object/type、extra/missing key、blank/oversized query、oversized Directive、duplicate key、deep nesting 均保守失败 |
| A8 | 第二轮再请求 capability；2 model、1 capability、0 third call |
| A9 | provider exception 只返回 `model_failed` |
| A10 | Memory/Execution exception 只返回 safe code，不泄露 sentinel detail |
| A11 | Memory safe unavailable 形成 bounded observation，允许 call #2 完成 `NoChange` |
| A12 | 两次相同 scripted run 的 semantic result、完整 model call sequence 和 capability sequence 相同 |

额外边界证据：

- frozen `ActivationInput`；
- exact-limit input accepted，blank/limit+1 input 在 model call 前失败；
- raw output、Memory observation、Execution observation 和 DecisionIntent limit 检查；
- baseline 即使收到合法 capability request 也保持零分派；
- dataclass subclass 的额外字段不会进入 model-visible activation projection。

## 11. Skills actually used

### `/codebase-design`

用于冻结一个 deep module seam：依赖注入、return value、单 operation、private parser/projection。它排除了 Runner/Manager/Registry/ContextBuilder、production wiring 和 future-facing extension points。

### `/tdd`

按 acceptance behavior 逐片推进。主要 red → green 记录：

| Slice | Red evidence | Green evidence |
|---|---|---|
| 1 | `Mind.experiment_a` 不存在 | 1 passed |
| 2 | Memory policy/Directive surface 不存在 | 2 passed |
| 3 | `ExecutionObservation` surface 不存在 | 3 passed |
| semantic finals | `DecisionIntent` 不存在 | 5 passed |
| strict protocol | duplicate JSON key 被错误接受 | 19 passed |
| safe unavailable | `safe_error_code` 被错误当作 invalid observation | 24 passed |
| final bounds | non-bool experiment flag 被错误接受 | 36 passed |
| review regression | activation subclass extra 实际进入 prompt | explicit projection test red，修复后 38 passed |

最初按任务卡运行默认 `python -m pytest ...` 时，PATH 上的 Conda Python 没有 pytest；这不是 test red。后续全部证据使用仓库现有 `.venv\Scripts\python.exe`。

### `/code-review`

按照 task-start fixed point（两个实现文件原本不存在）并行运行 Standards 与 Spec review。只审本任务两个新增代码文件，排除工作区既有 dirty changes。

### Ponytail

应用的实际规则：Think Before Coding、最小 vertical slice、小而可逆、先实验/测试再 promotion、stdlib 优先、避免 speculative abstraction、保留仓库 ownership boundary。

结果是直线的两调用控制流，没有 loop、framework、registry、manager、factory、trace system 或 generalized configuration object。任务卡、AGENTS 与 Ponytail 无实质冲突。

## 12. Code-review findings and resolution

### Finding 1 — strict parser recursion boundary

- Review：Spec
- Severity：High / blocking
- 原问题：长度仍在 raw-output limit 内的 deeply nested JSON 在部分 Python/runtime boundary 上可抛 `RecursionError`；parser 只捕获 `TypeError/ValueError`。
- 风险：异常可能逃离 `run_activation` 并暴露 traceback，违反 conservative failure。
- 修复：`_parse_output` 同时捕获 `RecursionError`；A7 保留 1,999-char nested JSON regression。
- Re-review：closed。

### Finding 2 — dataclass subclass projection leak

- Review：Standards
- Severity：Medium / blocking
- 原问题：`asdict(activation)` 会序列化 frozen dataclass subclass 增加的任意字段；这些字段没有 activation limits，并可能不可 JSON 序列化。
- 风险：突破 model-visible whitelist/bounds，或在 safe failure path 外抛异常。
- 修复：移除 `asdict`，两次请求均使用三字段 `_activation_payload` explicit projection；新增 public-seam subclass regression。
- Re-review：closed。

Final review state：

```text
Standards: PASS — 0 blocking, 0 non-blocking findings
Spec:      PASS — 0 blocking, 0 non-blocking findings
```

## 13. Validation and regressions

使用仓库 `.venv` 解释器的最终结果：

```text
.venv\Scripts\python.exe -m pytest Mind/test_experiment_a.py -q
38 passed

.venv\Scripts\python.exe -m pytest \
  tests\test_mind_gate.py \
  tests\test_mind_llm_gate.py \
  tests\test_mind_gate_shadow.py \
  tests\test_mind_gate_operational.py \
  tests\test_mind_promotion_controls.py -q
24 passed

.venv\Scripts\python.exe -m pytest -q
338 passed, 24 skipped

.venv\Scripts\python.exe -m py_compile \
  Mind\experiment_a.py Mind\test_experiment_a.py
PASS
```

Root regression 只有两条既有 upstream warning：MAGMA 的 `ast.Str` deprecation 与 sentence-transformers method rename；没有新增 failure。

`git diff --check` 返回成功；输出仅包含任务开始前已有 tracked changes 的 LF→CRLF warning。

## 14. Git/scope audit

本任务造成的预期文件变化只有：

```text
?? Mind/experiment_a.py
?? Mind/test_experiment_a.py
?? Mind/docs/EXPERIMENT_A_RESULT.md
```

`Mind/docs/EXPERIMENT_A0_INTERFACE_AUDIT.md` 是前序 A0 任务产物。仓库在本任务开始前已有大量 Execution、core、docs、Canvas 等 tracked/untracked changes；本任务没有修改它们。没有 commit、push、rebase 或 reset。

没有修改：

```text
core/
Execution/
Conversation_Memory/
Dream/
production Mind gate
docs/CURRENT_STATUS.md
pytest discovery config
```

## 15. Limitations and non-claims

- 测试使用 deterministic scripted/conditional fake，不测真实 provider 的 semantic quality。
- 没有 production trigger、MessageRuntime routing 或 live Execution lookup。
- 没有 Execution-owned supervisor DTO/interface；当前 injected callable 是实验宿主 seam。
- 没有 durable Mind Trace、restart reconstruction 或 hidden-CoT storage。
- 没有 Directive persistence/application，也没有 Intention mutation。
- 严格 JSON 能禁止 Planner structured fields，但不能证明自由文本永远不是 planner-like；本实验只通过 high-level prompt、fixtures 和人工 code review 检查，不加入 fixture-specific regex。
- 没有验证 Mind 对真实 Execution 的行为价值；这属于 Experiment E。
- 没有进入 Experiment B、C 或 D。

## 16. Final verdict

```text
EXPERIMENT_A_PASS
```

通过理由：一次可选只读 information-acquisition round 已在现有 model/Memory seams 和 injected Execution projection 上形成受界、确定、保守失败的完整 trajectory；模型最多调用两次、能力最多调用一次、observation 确实改变 scripted final judgment，并且没有生产耦合、执行 authority、状态 mutation 或未授权架构扩张。
