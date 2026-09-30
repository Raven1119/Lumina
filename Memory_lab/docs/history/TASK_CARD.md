# 任务卡：Memory Lab v1

实现 [DESIGN.md](DESIGN.md) 中重新设计的 Conversation Memory，并用 `eval_set/` 做全自动评测。这是一个实验室，不接入 Chat、Mind 或 Execution，也不追求生产化。

交给：Codex（一次性完成）。工作目录：仓库根目录下的 `Memory_lab/`。

---

## 0. 一句话目标

在模拟时钟下，把三份剧本逐轮回放进 Hot/Cold，由 Dream 调用模型把对话整合成要点记忆；每轮只用本地计算，在四层图上扩散激活来召回；按阶段 B0、B1、P1–P6 逐个加机制，每个阶段都跑完整的自动评测，最后给出对照报告。

## 1. 先读什么，冲突时听谁的

按顺序读：

1. `Memory_lab/AGENTS.md`：本目录的工作规则。
2. `Memory_lab/docs/DESIGN.md`：设计规范，说明“为什么”和“是什么”。
3. 本任务卡：实现细节、默认参数和验收标准。
4. `Memory_lab/eval_set/README.md`：评估集的格式和打分规则。
5. 仓库根目录 `AGENTS.md` 的 “Preserve the boundaries” 一节。

优先级：本任务卡 > DESIGN.md > 你的判断。三者冲突，或者某处规定明显做不到时，按本卡执行，在最终报告的“偏差”一节写明原因，不要停下来等回复。

**本卡相对 DESIGN.md 做了九项实验期的实现决定**，都是为了让各阶段可比、缓存可复用、行为确定。它们是有意的，不要改回去：

| # | DESIGN.md 的说法 | 本卡的做法 | 原因 |
| --- | --- | --- | --- |
| D1 | Dream 唤起旧记忆时，用与聊天相同的多层召回 | 用固定的、与阶段无关的检索（§8.2） | 阶段 2–6 只改召回侧；唤起方式固定，Dream 提示词才能逐字相同，缓存才能复用 |
| D2 | 唤起的旧记忆带“强度” | 提示词里的强度只按写入侧事件计算（§8.3） | 召回强化依赖阶段；若写进提示词，各阶段的 Dream 输入就不同了 |
| D3 | 在聊天中被召回就强化 0.5；一起被召回的记忆在事件层上加 β | 这两项都只算近联想和远联想，心上之事不参与；同一条记忆（或同一对记忆）6 小时内只记一次 | 心上之事每轮都在上下文里，让它参与会形成“越强越强”的正反馈；同一场对话里反复召回不应重复累加 |
| D4 | 三路起点合并后归一化 | 每一路先各自做 L1 归一化，再按 α 加权合并 | 否则时间通道一次命中几十条，会压倒其他通道 |
| D5 | 用 MMR 去掉几乎重复的 | 用余弦阈值过滤近重复 | 行为等价，更简单，结果确定 |
| D6 | “同一天”“晚上” | 逻辑日以 05:00 为界；“晚上”指 18:00 到次日 05:00 | 深夜实验、凌晨赶工都算“那天晚上” |
| D7 | 阶段 3 加时间层 | 增设辅助对照 P2g：只开扩散，只有语义层 | 扩散算子第一次出现，把它和时间层的收益分开 |
| D8 | 时间通道 | 记忆起点只用完全落在今天（逻辑日）之前的区间。与今天有交集的（今天、今晚、刚才、最近、这周末）和整个在将来的（明天、下周）都不产生记忆起点；前者截到 `now` 后仍用于原文窗按时间取 | Dream 有滞后，今天和将来的事还没有整合成记忆，按距离衰减只会把最近的记忆误当起点；当下的事由 Hot 和原文窗负责 |
| D9 | 远联想：与线索语义相似度低、激活高 | 候选必须不是任何通道的起点（`a⁽⁰⁾ = 0`），见 §10.5 | “不是起点”正是“只能靠扩散到达”；不需要依赖嵌入模型的相似度阈值 |

## 2. 范围

**要做**

- `memlab/`：记忆系统本身，包括存储、整合、强度、四层图、召回、中文时间解析和渲染。
- `lab/`：实验设施，包括 Hot/Cold 模拟、模型客户端与缓存、评测器、打分、回答层（可选）、报告和命令行。
- `tests/`：离线测试，不联网、不需要密钥、不加载真实嵌入模型。
- 真实运行：在两个开发集上跑完所有阶段，写出结果报告（§16、§18）。

**不做**

- 主动回想（由回答模型发起的结构化召回）。
- 回放，以及 Dream 中的记忆整理（DESIGN 阶段 7，已推迟）。
- 人设演化、experience 模块。
- 接入 `core/main.py`、Chat、Mind、Execution。
- 盲评前端。
- 在保留集 `holdout_c` 上做召回、打分或调参（§13.7）。唯一的例外是 §15 的两项离线测试（Hot 一致性、时间解析）会读取它的对话和金标准文件，但不在上面召回或打分。

## 3. 硬约束

- 只在 `Memory_lab/` 内新增或修改文件。可以**只读**引用仓库中的这些文件：`core/model_client.py`、`core/env_loader.py`、`prompts/chat_background.md`、`Conversation_Memory/adapter/_calibrated_index.py`（只取模型 ID 和 revision 常量）、`Conversation_Memory/ingestion/temporal.py`（仅作参考）。不要改根目录的 `pyproject.toml`、`requirements.txt` 或 `.gitignore`。
- 不要 import 旧记忆栈：`Conversation_Memory.adapter`、MAGMA、`vendor/`、`Mind`、`Dream`。
- 不要修改 `eval_set/scripts/`、`eval_set/gold/` 和 `eval_set/build.py`。发现评估集有问题，写进报告的“评估集问题”一节。
- 不要修改 `prompts/integrate_v1.md` 和 `prompts/answer_v1.md`。只有一种情况可以写 `integrate_v2.md`：v1 的输出在格式上系统性失败，例如大量 JSON 无法解析。v2 只修格式，不改写作策略，v1 的结果要保留并一起报告。
- 保护 `.env.local`、`data/` 和一切凭据。密钥不进日志、缓存、报告或异常信息。
- 未经明确授权，不 commit、push、rebase 或 reset。
- 测试必须离线：不联网，不需要 `DEEPSEEK_API_KEY`，不 import `sentence_transformers`、`torch` 和 `httpx`（放到函数内部延迟导入）。
- 聊天侧（召回）不调用模型，也不写记忆图；它唯一的写入是召回痕迹。Dream 是记忆图唯一的写者。

## 4. 目录与模块

```
Memory_lab/
  AGENTS.md
  README.md
  requirements.txt            只列 Memory_lab 额外需要的包
  .gitignore                  runs/、__pycache__/、.pytest_cache/
  docs/
    DESIGN.md                 设计规范（已有）
    TASK_CARD.md              本文件（已有）
    RESULTS_v1.md             你写的结果报告（§18）
  prompts/
    integrate_v1.md           Dream 整合提示词（已有，不要改）
    answer_v1.md              回答层提示词（已有，不要改）
  eval_set/                   评估集（已有，不要改）
    build.py  README.md  scripts/  gold/  built/
  memlab/                     记忆系统
    __init__.py
    config.py                 所有参数与阶段预设（§12、§14）
    clock.py                  Clock 协议、SimClock、时间工具（逻辑日、周、中文日期格式）
    types.py                  数据类
    store.py                  SQLite 持久层、事务、版本号（§7）
    snapshot.py               不可变的只读快照：向量矩阵、四层稀疏矩阵、强度所需数据
    embed.py                  Embedder 协议；BgeM3、MiniLM、Hash 三种实现；嵌入缓存（§5）
    timeparse.py              中文时间表达 → 区间（§11）
    entities.py               实体规范化、对齐、自身过滤（§8.6）
    integrate.py              Dream：取窗口、唤起、渲染、解析、校验、应用（§8）
    strength.py               B_i、π、沉睡（§9）
    layers.py                 四层边的构建（§10.3）
    recall.py                 起点、扩散、打分、四部分输出（§10）
    rawwindow.py              时间窗原文检索（§10.6）
    render.py                 记忆块渲染（§10.7）
  lab/                        实验设施
    __init__.py
    __main__.py               python -m lab
    hotcold.py                Hot/Cold 模拟（与 build.py 规则一致）
    llm.py                    模型客户端、响应缓存、FakeLLM
    replay.py                 回放驱动：写 Hot、压缩、触发 Dream、写召回痕迹
    evaluator.py              探针、gap 变体、时间变换
    scoring.py                记忆层打分（§13.4）
    answer.py                 回答层（可选）
    judge.py                  评判接口；默认不启用
    report.py                 JSON、Markdown 报告与曲线数据
    cli.py
  tests/
  cache/                      模型响应缓存与嵌入缓存（是否提交由仓库主人决定，不要删除）
  runs/                       每次运行的输出（不提交）
```

模块划分可以微调，但 `memlab` 不能依赖 `lab`：记忆系统不知道评测器的存在。

运行方式：在 `Memory_lab/` 目录下执行 `python -m lab ...` 和 `python -m pytest tests -q`。用到 `core.*` 时，`lab/llm.py` 在函数内部把仓库根目录（`Path(__file__).resolve().parents[2]`）加入 `sys.path`。

## 5. 环境、依赖与外部资源

- Python 用仓库已有的 Memory 虚拟环境（`Conversation_Memory/.venv/bin/python`）。缺包时，可以在 `Memory_lab/.venv` 新建环境，并把依赖写进 `Memory_lab/requirements.txt`。
- 必需：`numpy`。可选：`scipy`（稀疏矩阵；没有就用 numpy 稠密矩阵，10³ 量级足够快）、`sentence-transformers`（真实嵌入）、`httpx`（真实模型）。

**嵌入模型**（`--embedder auto` 的解析顺序）

1. 环境变量 `LUMINA_MEMLAB_EMBED_MODEL` 指向的本地 bge-m3 目录。
2. HuggingFace 本地缓存里的 `BAAI/bge-m3`（`local_files_only=True`）。
3. 传了 `--allow-download` 且网络可用：下载一次 `BAAI/bge-m3`，把 revision 写进报告。只允许下载这一个模型；不要下载旧的 `bge-reranker` 等权重。
4. 已固定的多语言 MiniLM：模型 ID 和 revision 取自 `Conversation_Memory/adapter/_calibrated_index.py`，也支持 `LUMINA_CALIBRATED_MODEL_SNAPSHOT`。
5. 以上都没有：真实运行报错退出。`HashEmbedder` 只能通过 `--embedder hash` 显式选择，只用于测试和冒烟。

向量一律 L2 归一化。报告里记录嵌入模型的身份：模型 ID、revision、维度、权重文件的 sha256，做法参考 `_calibrated_index._model_identity`。嵌入缓存按 `(模型身份, 文本 sha256)` 存在 `cache/embed/<模型身份短哈希>.sqlite`，各阶段、各次运行共用。

`HashEmbedder`：对中文取字的二元组，对拉丁字母取整词，哈希到 256 维后计数并归一化。它必须确定、快速，并且让字面相近的文本余弦较高，以便测试。

**模型**：DeepSeek-V4-Pro，走 Anthropic 兼容接口。优先复用 `core.model_client.DeepSeekAnthropicModelClient` 的请求路径（`base_url`、`model`、`thinking: disabled`、`X-Api-Key`）；如果它没有合适的公开方法能发送任意的 system 加 messages，就在 `lab/llm.py` 里用 httpx 写一个最小客户端，常量从 `core.model_client` 导入。密钥从进程环境读取，读不到再用 `core.env_loader.load_env_file` 读仓库根目录的 `.env.local`。Dream 调用：`max_tokens=4096`，`temperature=0`（接口不接受时去掉，并在报告中说明）。

## 6. 核心接口

名字可以调整，但职责和确定性要求不变。

```python
class Clock(Protocol):
    def now(self) -> datetime: ...          # 带时区，+08:00

class Embedder(Protocol):
    identity: dict
    def encode(self, texts: list[str]) -> np.ndarray: ...   # (n, d) float32，L2 归一化

class LLM(Protocol):
    def complete(self, *, system: str, messages: list[dict], max_tokens: int,
                 purpose: str, attempt: int = 0) -> LLMResult: ...
    # LLMResult: text, usage(input_tokens, output_tokens), cache_hit: bool, cache_key: str

# memlab 对外只暴露这几个入口
def run_dream(store, cold_window: list[Turn], llm, embedder, clock, cfg) -> DreamResult
def consume_traces(store, cfg, now) -> None      # 由 run_dream 在收尾时调用
def recall(snapshot, cue: Cue, now: datetime, cfg) -> RecallResult
def render_memory_block(result: RecallResult, now: datetime, cfg) -> str
```

- `Turn`：`id, session_id, role('user'|'assistant'), time, text`。
- `Cue`：`message`（当前消息）、`recent`（最近 `cue_recent_turns` 轮 Hot 原文）、`hot_ids`（此刻在 Hot 中的轮次 ID，原文检索要排除它们）。
- `RecallResult`：`near`、`remote`、`core` 三组 `RecalledMemory(id, text, score, pi, B, parts, sources, time_label)`，`raw` 一组 `RawHit(turn_id, time, role, text, score, via)`，以及诊断信息：各通道的起点、解析出的时间区间、耗时。
- 系统内所有取时间的地方都从 `Clock` 或显式传入的 `now` 取，禁止调用 `datetime.now()`。

## 7. 存储

一个 SQLite 文件，就是本次运行目录里的 `memory.sqlite`；测试用 `:memory:`。一次 Dream 的全部写入放在一个事务里，结束时 `meta.version += 1`。

| 表 | 主要字段 |
| --- | --- |
| `meta` | `key, value`（version、schema_version、embedder identity） |
| `cold_turns` | `turn_id PK, session_id, role, time, text, seq, integrated_by`（整合它的 dream_id，未整合为 NULL） |
| `memories` | `id PK`（`m1, m2, ...` 按创建顺序）、`text, salience, created_at, created_by_dream, embedding BLOB` |
| `memory_versions` | `memory_id, version, text, at, dream_id, op_index, kind('new'|'revise'|'merge_child')`：lineage |
| `memory_merges` | `child_id, parent_id, dream_id` |
| `memory_sources` | `memory_id, turn_id, dream_id` |
| `occurrences` | `memory_id, at, dream_id`（同一记忆、同一时刻去重） |
| `strength_events` | `memory_id, at, weight, kind('new'|'revise'|'merge'|'touch'|'recall'), ref`（继承来的事件保留原 kind，`ref` 记为 `inherit:<父条目ID>`） |
| `entities` | `id PK`（`e1, e2, ...`）、`name, created_at` |
| `entity_aliases` | `entity_id, alias` |
| `memory_entities` | `memory_id, entity_id` |
| `event_edges` | `a, b, component('relate'|'merge'|'corecall'), weight, directed, last_reinforced, note` |
| `dream_runs` | `id, at, first_turn, last_turn, status, cache_key, prompt_version, model, n_ops, n_rejected, usage, error` |
| `dream_ops` | `dream_id, op_index, op_json, status('applied'|'rejected'), reason, result_id` |
| `recall_traces` | `id, at, near_json, remote_json, core_json, consumed_by` |

- 语义层、时间层和共现分量**不入库**：每次构建快照时，由向量、出现时间和 `event_edges` 确定性地算出。
- `snapshot.py` 从某个已提交版本构建不可变快照，并按版本号缓存。召回只接受快照。Dream 提交新版本后，旧快照照常可用；召回方下一次取快照时，再整体换入新版本。
- 记忆从不删除。事件层里有效权重低于 ε 的边，在 Dream 收尾时删除。

## 8. Dream 整合

### 8.1 触发与窗口

- 每写入一轮助手回复，就检查一次 Hot 压缩。规则与 `eval_set/build.py` 的 `simulate_hot` 完全一致：Hot 原文超过 24 轮时，保留最近 12 轮，按完整的一问一答对齐；移出的轮次按原样写入 `cold_turns`。
- 每次压缩后，如果未整合的 Cold 轮次 ≥ `dream_trigger_turns`（40），就立即执行 Dream。循环执行：每次取最早的、连续的未整合轮次，至多 `dream_window_max_turns`（40）轮，并在一问一答处对齐；直到剩余不足 40 轮。剩余部分等下次触发。
- 不设空闲触发，也不在探针前强制整合。每个探针时刻都要记录当时未整合的 Cold 轮数。
- Dream 的“现在”取触发时的模拟时间，也就是触发压缩的那一轮的时间。

### 8.2 唤起（固定检索，见 D1）

输入：当前记忆库（含沉睡记忆和已被合并的父条目）和窗口。输出：至多 30 条旧记忆。整个过程只用向量和字符串匹配，不看强度、可及度和召回痕迹。

1. 把窗口按连续 6 轮切块。每块的文本是这几轮的“说话人：原文”拼接，然后编码。
2. 每块取余弦最高的 8 条记忆；再把与任一块余弦 ≥ 0.80 的记忆标为“近重复”。
3. 实体：找出名字或别名作为子串出现在窗口原文中的已知实体（中文至少 2 个字，拉丁字母至少 3 个，同 §10.2）。每个实体取它最近出现的 3 条记忆（按最后一次出现时间排序），总数不超过 10 条。
4. 合并三类候选，排序键为（近重复优先，实体相关优先，与各块的最大余弦降序，ID 升序），截取前 30 条。
5. 在提示词里按首次出现时间升序排列。

### 8.3 提示词渲染

读取 `prompts/integrate_v1.md`，去掉 HTML 注释，按 `=== system ===` 和 `=== user ===` 切成两段，再填入以下四个占位符：

- `{{now}}`：`2026 年 3 月 5 日 周四 00:10`
- `{{memories}}`：每条一行：`m12｜首次约 3 周前，最近一次 2 天前｜常提｜正文`。时间是相对 Dream 当时的粗略说法，统一用一个函数 `rough_age(days)`：不到 1 天为“今天”；1 天到不足 2 天为“昨天”；2–13 天为“N 天前”（向下取整）；14–55 天为“约 N 周前”（N = round(days/7)）；56 天及以上为“约 N 个月前”（N = max(2, round(days/30))）。§10.7 的相对时间也用这个函数。强度只按写入侧事件的次数计，即 kind 为 new、revise、merge、touch 的事件，包括继承来的，不含 recall：1 次为“一次”，2–3 次为“几次”，≥4 次为“常提”（D2）。没有旧记忆时填“（无）”。
- `{{entities}}`：窗口中出现的已知实体，加上被唤起记忆涉及的实体，至多 40 个。超出时，先按在窗口中首次出现的顺序取窗口里的实体，再按被唤起记忆的排序取它们的实体。每行一个：`e3｜小周（别名：周同学、小周同学）`。没有时填“（无）”。
- `{{window}}`：每轮一行：`[s03-t01] 3月4日 周三 23:50 他：今晚抢到成像机时了，十点到一点`。用户的说话人写“他”，Lumina 写“我”。

渲染结果必须是确定的：同样的库、窗口、时钟和提示词文件，渲染出逐字节相同的文本。

### 8.4 输出解析与校验

- 从响应里取第一个完整的 JSON 对象，允许它被 ```json 围栏包着。取不到或解析失败，这个窗口就标为 `failed`（§8.7）。
- 逐条校验操作，不合法的只跳过这一条，并把原因记入 `dream_ops`：
  - `op` 不是五种之一；
  - `sources` 过滤掉不在本窗口里的轮次 ID 后为空（relate 以外的操作）；
  - `id` 或 `ids` 不在本次唤起的列表里（模型没见过的 ID 一律拒绝）；
  - merge 去重后少于两个 ID；
  - `text` 为空或超过 300 字；
  - relate 的 `a`、`b` 解析不了、指向被拒绝的操作，或者两者相同；
  - `new:k` 指向的不是一条成功的 new 或 merge。
- `salience` 不合法时按 1 处理，并记录一条警告，不拒绝整条操作。

### 8.5 应用语义

分两遍执行：第一遍按数组顺序执行 new、revise、merge、touch，第二遍执行 relate。以下 `T(op)` 指这条操作有效 sources 中最晚一轮的时间。

- **new**：创建 `m{n}`，写入正文、`salience`（限制在 1–3）、嵌入和 sources；出现时间取每个 source 轮次的时间；在 `T(op)` 记一个权重 1.0 的 `new` 事件；再挂上实体。
- **revise**：把旧正文写进 `memory_versions`，换成新正文并重算嵌入；追加 sources 和出现时间；在 `T(op)` 记一个 1.0 的 `revise` 事件；实体取并集。如果带 `salience`，取新旧中较大的一个。
- **merge**：创建子条目 `m{n}`。正文取操作给的；`salience` 取操作给的，没有就取各父条目合并前的最大值。子条目继承所有父条目的出现时间、sources 和强化事件（复制，保留原时间、权重和原来的 kind，`ref` 记为 `inherit:<父条目ID>`），再追加本次的 sources 和出现时间，并在 `T(op)` 记一个 1.0 的 `merge` 事件。实体取父条目与操作的并集。父条目保留，`salience` 减半，在 `memory_merges` 里登记。事件层为子条目与每个父条目各加一条 `merge` 分量的无向边，权重 1.0，`last_reinforced = T(op)`。
- **touch**：追加 sources 和出现时间，在 `T(op)` 记一个 1.0 的 `touch` 事件。
- **relate**：`a`、`b` 可以是旧 ID，也可以是 `new:k`。写入或更新 `relate` 分量：权重更新为 `max(w·exp(−Δ/T_e), 1.0)`，Δ 为距上次强化的时间，`directed` 按操作给出（有向时 a 在前、b 在后），`last_reinforced` 取窗口最后一轮的时间，`note` 只供调试。

所有写入都在同一个事务里完成：先把原始响应和解析结果持久化（§8.7），再应用。

### 8.6 实体对齐

- 规范化：去掉首尾空白，全角转半角，拉丁字母转小写。
- `{"id": "eN"}` 存在就直接用。否则按 `name` 和 `aliases` 与所有已知名字、别名做精确匹配：匹配上就复用，并补上新别名；匹配不上就新建 `e{n}`。
- 自身过滤：名字或别名落在 `SELF_NAMES = {我, 你, 他, 她, 用户, 林素, lumina}` 中的实体丢弃，并记录。
- 不做模糊匹配。同名不同人的区分交给模型：它会看到已知实体及其别名。

### 8.7 收尾、缓存与失败

1. 以（模型、`max_tokens`、`temperature`、提示词版本、完整的 system 文本、完整的 messages、attempt）的 sha256 作为缓存键，查 `cache/llm/<key>.json`。命中就直接用；没有命中且 `--cache-only`，就抛出 `CacheMiss` 并中止运行；否则调用模型，**先写缓存文件**，再继续。
2. 解析、校验、应用（§8.4–8.6）。
3. 消费召回痕迹（§10.8）。
4. 在事件层删除有效权重 < ε 的边。
5. 把窗口内的轮次标为 `integrated_by = dream_id`，版本号加 1，提交事务。

**失败**：JSON 无法解析时，窗口不标为已整合，`dream_runs.status = 'failed'`，运行中止并在报告中说明。只有传了 `--dream-retry-failed K`，才对失败窗口做至多 K 次重试：每次 `attempt + 1`，所以缓存键不同，结果仍然可以复现。之后用 `--cache-only` 跑其他阶段时必须传同样的 K，`suite` 会把它传给每个预设。模型调用本身出错（网络、限流）时，按指数退避重试至多 3 次，这不算 attempt。

## 9. 强度、可及度与沉睡

时间以天为单位，间隔下限为 1 小时，即 1/24 天。只计 `t_k ≤ t` 的事件。

```
B_i(t) = ln Σ_k w_k · max(t − t_k, 1/24)^(−d) + ln s_i
π_i(t) = σ((B_i(t) − τ) / θ)
```

- 权重：new、revise、merge、touch 为 1.0；召回为 0.5；继承来的事件保留原权重。
- τ 和 θ 按 DESIGN 的标定原则取初值：一条 salience 为 1、只出现过一次的记忆，21 天后 π = 0.5，60 天后 π = 0.1。由此得出 τ = −0.5·ln 21 ≈ −1.522，θ = (−0.5·ln 60 − τ) / ln(1/9) ≈ 0.239。在 `config.py` 里写成由这两个锚点算出的值，并注明来历。
- 沉睡：在 `(t − T_d, t]` 内没有任何强化事件，并且 `π_i(t − T_d) < π_min`，就算沉睡。因为两次事件之间 B 单调下降，这等价于“持续低于阈值超过 T_d”，而且不需要保存状态。沉睡的记忆不做起点，也不进心上之事；扩散中仍然传导，最终分数照常乘 π。
- 实现要向量化：快照里保存每条记忆的事件时间和权重数组，召回时按 `now` 一次算完。
- 注意：按这组参数，只出现过一次、salience 为 1 的记忆，约 86 天后 π 才低于 0.05，约 116 天后才会沉睡。所以 +60 天的“淡忘”变体测的不是沉睡，而是 π 加权后的排序能否把闲聊压到近联想的 k 条之外。解读结果时要记住这一点，不要为了让它沉睡而改参数。

## 10. 召回

### 10.1 线索

- 编码：`q = normalize(embed(message) + 0.3·embed(recent 拼接) + 0.2·embed(time_phrase))`。`recent` 是 Hot 中最近的 `cue_recent_turns = 2` 轮，为空时去掉这一项。`time_phrase` 是当前时间的星期加时段，如“周日凌晨”，时段划分同 §10.7。这对应 DESIGN §6 线索中的“当前时间”，§8 深夜场景的路径一依赖它。
- 时间表达只从当前消息里解析（§11）。
- 实体既从当前消息里匹配（权重 1.0），也从 `recent` 里匹配（权重 0.3）。

### 10.2 起点三通道

每个通道先得到一个非负向量，再各自做 L1 归一化（D4），最后合并：`a⁽⁰⁾ = Σ_c α_c · r̂_c / Σ_{有信号的 c} α_c`，所以 `a⁽⁰⁾` 的总和为 1。只有未沉睡的记忆能做起点：先剔除沉睡记忆，再取前 S 条、计算各通道、做归一化。

- **语义 r_S**（记忆节点）：取与 q 余弦最高的 S 条，在这 S 条内做 softmax(cos / T_s)，其余为 0。
- **时间 r_T**（记忆节点）：把解析出的若干区间取并集。对每条记忆取它所有出现时间上的最大值：落在区间内为 1，落在区间外为 `exp(−距离小时数 / 12)`，低于 0.05 的记为 0。按 D8，只用终点不晚于今天（逻辑日）起点的区间。没有区间，或者没有一条记忆大于 0，这个通道就没有信号。
- **实体 r_E**（实体节点）：在文本中查找实体名和别名。中文至少 2 个字，拉丁字母至少 3 个；重叠时取最长匹配，比如“林晓师姐”优先于“林晓”。值取所在文本的权重。

### 10.3 四层转移矩阵

节点包括全部记忆节点和实体节点。每一层先构建带权的边，每个节点在每层只保留权重最高的 M 条，再按行归一化，得到 P⁽ˡ⁾。

- **语义层**（记忆–记忆）：每条记忆取余弦最高的 `k_sem` 个邻居，且余弦 ≥ `c_sem`；两个方向取并集，权重为余弦。
- **时间层**（记忆–记忆）：Δ 为两条记忆出现时间之间的最小差。Δ ≤ 3σ 时连边，权重 `exp(−Δ / σ)`。用排序后的扫描线实现。
- **实体层**（二部图）：记忆 → 实体的概率为 `1 / deg(记忆)`；实体 → 记忆的概率为 `1 / deg(实体)`。
- **事件层**（记忆–记忆）：一对记忆的权重是以下分量之和，每个分量先乘上衰减 `exp(−(now − last_reinforced) / T_e)`，有效值 < ε 的记为 0：
  - `relate`：有向边 a→b 正向为 w，反向为 ρ·w；无向边两个方向都是 w。
  - `merge`：无向，权重 w。
  - `cooccur`（在快照构建时计算）：按逻辑日分时段，时段以 05:00 为界、时区 +08:00。N 是出现过任何记忆的时段数，n_i 是记忆 i 出现过的时段数，n_ij 是 i、j 同时出现的时段数。权重为 `max(0, ln(n_ij·N / (n_i·n_j))) · (1 − exp(−n_ij/κ))`，`last_reinforced` 取最近一次共同出现的时段。有父子关系的两条记忆不计共现。
  - `corecall`：见 §10.8。
- **逐节点混合**：L(i) 是节点 i 在已启用的层中有出边的那几层，`P_i = Σ_{ℓ∈L(i)} w_ℓ·P⁽ˡ⁾_i / Σ_{ℓ∈L(i)} w_ℓ`。实体节点只有实体层。所有层都没有出边的节点加一个自环。

### 10.4 扩散与打分

```
a⁽ᵏ⁺¹⁾ = (1 − λ)·a⁽⁰⁾ + λ·Pᵀ·a⁽ᵏ⁾,   k = 0..K−1
score_i = a_i⁽ᴷ⁾ · π_i      （只对记忆节点）
```

每一步都保持总和为 1，测试要检查这一点。阶段没有开启扩散时（`diffusion = False`，即 B1、P1、P2），`score_i = a_i⁽⁰⁾ · π_i`。

### 10.5 四部分输出

依次选出，后选的部分要排除已选中的记忆，也要排除与已选中记忆余弦 ≥ `dup_cos` 的近重复（D5）：

1. **近联想**：按分数降序，并列时按 ID 升序，取前 k 条，分数必须大于 0；与已选入的近联想余弦 ≥ `dup_cos` 的跳过。
2. **远联想**（`remote_slot = True` 时）：候选必须满足 `a_i⁽⁰⁾ = 0`（不是任何通道的起点，D9）且未沉睡，取分数最高的一条。它的分数要 ≥ `remote_ratio` × 近联想最后一条的分数才给出；近联想为空时不给。
3. **心上之事**（`salience_core = True` 时）：在未沉睡的记忆中按 `B_i(now)` 取前 m 条，与线索无关。
4. **时间窗原文**：见 §10.6。

### 10.6 时间窗原文

候选：`cold_turns` 中时间落在 `[now − W, now)` 内、且此刻不在 Hot 里的轮次，不论是否已整合。

- **按时间取**：当前消息解析出了区间（终点截到 `now`；与今天有交集的区间也算，D8），且区间与时间窗有交集时，取落在区间内的轮次，按混合分数取至多 `raw_interval_cap` 条，输出时按时间排序。
- **按内容取**：对全部候选计算混合分数 `0.5·cos(q, 轮次向量) + 0.5·bigram_overlap`，取前 `raw_topk` 条。`bigram_overlap = |B(q) ∩ B(轮次)| / |B(q)|`，B 是中文字二元组加拉丁整词，去掉一张小停用表（我们、今天、什么、一下、这个、那个、就是、还是、没有、可以、然后、感觉）。`B(q)` 取自当前消息的文本；为空时 bigram 项记为 0。
- 两部分合并去重，每条标明来自 `interval` 还是 `content`。轮次向量走嵌入缓存。

### 10.7 渲染（记忆块）

`render.py` 输出纯文本，供回答层使用，也写进报告：

```
【此刻想起的】
- （4 月 1 日 周三 晚上，5 天前）他在做成像，补图四的一个时间点。
- （3 月初起多次，最近一次 3 天前）他常在深夜做实验。
【顺带想到的】
- （……）……
【最近心上的事】
- （……）……
【最近两周的原话】
- 4 月 1 日 周三 23:20 他：……
- 4 月 1 日 周三 23:21 我：……
```

- 时间标签由出现时间生成：所有出现时间都在同一逻辑日内时，写“日期 周几 时段”再加相对时间；否则写“首次那段时间起多次，最近一次 相对时间”；过去 14 天里有 3 次以上出现的，写“最近常提”。时段划分：凌晨 00–05，早上 05–08，上午 08–12，中午 12–14，下午 14–18，晚上 18–24。
- 空的部分整节省略；全部为空时输出“（此刻没有想起什么）”。

### 10.8 召回痕迹与消费

- 回放时，每写入一轮用户消息（从第一次 Dream 之后开始），就用当前阶段的配置对它做一次召回：`message` 是这一轮，`recent` 是它之前的 Hot 原文。召回结果写入一行 `recall_traces`。探针不写痕迹。
- Dream 收尾时消费上次以来的所有痕迹：
  - `recall_strengthening = True` 时，近联想和远联想中的每条记忆在痕迹时刻记一个 0.5 的 `recall` 事件；如果该记忆 6 小时内已经有过 `recall` 事件，就跳过（D3）。心上之事不强化。
  - `corecall = True` 时，同一条痕迹中近联想和远联想的每一对记忆，把事件层的 `corecall` 分量更新为 `w·exp(−Δ/T_e) + β`（Δ 为距上次强化的时间），`last_reinforced` 取痕迹时刻；同一对 6 小时内只加一次（D3）。
- 召回强化要等下一次 Dream 才生效。这符合“Dream 是唯一写者”的原则。

## 11. 中文时间解析

`timeparse.parse(text, now) -> list[Interval]`，区间左闭右开，时区 +08:00。凡是“天”都指逻辑日，即从 05:00 到次日 05:00（D6）。

**约定**

- 一周从周一开始。“上周X”是上一个自然周里的周X，“这周X”和单独的“周X”是本周的周X。“上周末”是上一周的周六和周日，“这周末”是本周的周六和周日。
- 时段修饰：凌晨 00–05（按日历日取，“昨天凌晨”就是昨天那个日期的 00:00–05:00），早上 05–08，上午 08–12，中午 11–14，下午 12–18，傍晚 17–19，晚上 18:00 到次日 05:00，“放学那会儿” 16–19。修饰不认识就取整天。这里的范围有意放宽并允许重叠，与 §10.7 渲染用的时段划分不是一回事。
- 月份各段：“初”和“上旬”是 1–10 日，“中旬”是 11–20 日，“下旬”是 21 日到月底，“月底”是最后 7 天。只有月份没有年份时，取不晚于现在的最近一次；不带月份的“月初”“月底”不解析。
- 节日：国庆是 10 月 1–7 日，五一是 5 月 1–5 日。如果今年的节日在今天之后 7 天以上才开始，就取去年的。“国庆前一天”是 9 月 30 日；“后半段”是后 4 天，“前半段”是前 3 天；“那几天”是整段。
- “最近”是 `[now − 14 天, now)`；“前几天”是今天之前的 5 个逻辑日，不含今天；“刚才”是 `[now − 3 小时, now)`；“N 天前”是那一个逻辑日；“X 月 X 号/日”是那一天。
- 今天、今晚、今早、刚才、现在、这会儿这类指当下的表达，照常解析成区间。
- `parse` 返回的是未截断的原始区间，即下表的写法。截到 `now`、按 D8 过滤，都由使用方（时间通道、原文窗）负责。
- 解析不了就返回空列表，不能抛异常。

**必须通过的用例**（参考时刻全部取自评估集探针；每条探针所在的集合见 §15）

| 探针 | 参考时刻 | 表达 | 期望区间 |
| --- | --- | --- | --- |
| A04 | 03-29 周日 21:30 | 昨天下午 | [03-28 12:00, 03-28 18:00) |
| A05 | 03-31 周二 13:00 | 上周二 | [03-24 05:00, 03-25 05:00) |
| A06 | 04-06 周一 12:00 | 上周三晚上 | [04-01 18:00, 04-02 05:00) |
| A07 | 04-12 周日 20:00 | 三月初 | [03-01 05:00, 03-11 05:00) |
| A17 | 03-28 周六 20:00 | 这周末 | [03-28 05:00, 03-30 05:00)，与今天有交集，不产生记忆起点 |
| B04 | 05-21 周四 23:30 | 前天晚上 | [05-19 18:00, 05-20 05:00) |
| B05 | 05-26 周二 13:00 | 上周三 | [05-20 05:00, 05-21 05:00) |
| B06 | 06-13 周六 20:00 | 五月上旬 | [05-01 05:00, 05-11 05:00) |
| B07 | 06-08 周一 12:00 | 上周四 | [06-04 05:00, 06-05 05:00) |
| B12 | 06-10 周三 15:00 | 明天下午 | [06-11 12:00, 06-11 18:00)，整段在将来，时间通道忽略 |
| C04 | 09-24 周四 21:00 | 前天晚上 | [09-22 18:00, 09-23 05:00) |
| C05 | 10-13 周二 12:00 | 上周五放学那会儿 | [10-09 16:00, 10-09 19:00) |
| C06 | 10-18 周日 20:00 | 九月中旬 | [09-11 05:00, 09-21 05:00) |
| C07 | 10-06 周二 20:00 | 国庆前一天晚上 | [09-30 18:00, 10-01 05:00) |
| C18 | 10-03 周六 12:00 | 国庆后半段 | [10-04 05:00, 10-08 05:00)，整段在将来，时间通道忽略 |
| C22 | 10-18 周日 12:00 | 国庆那几天 | [10-01 05:00, 10-08 05:00) |
| C23 | 10-02 周五 20:00 | 上周六 | [09-26 05:00, 09-27 05:00) |

另加一条一致性测试：对所有“时间”类探针，解析出的区间至少要包含某一组 `must_surface` 中的一轮（用 `built/*.gold.json` 和 `built/*.dialogue.json` 核对）。现有数据全部满足。

可以参考 `Conversation_Memory/ingestion/temporal.py` 的写法，但不要 import 它。

## 12. 阶段预设

每个预设都是 `config.py` 里一组开关的组合。阶段 2–6 只改召回侧，Dream 输入逐字相同，所以都用 `--cache-only` 运行，复用 P1 生成的缓存。

| 预设 | 写入与 Dream | 召回强化 | 通道 | 扩散与层 | 心上之事 | 远联想 | 共现 / 共同召回 | 原文窗 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B0 | 否 | — | — | — | — | — | — | 否 |
| B1 | 否 | — | — | — | — | — | — | 是 |
| P1 | 是 | 是 | S | 无扩散 | 否 | 否 | 否 | 是 |
| P2 | 是 | 是 | S | 无扩散 | 是 | 否 | 否 | 是 |
| P2g（辅助） | 是 | 是 | S | 扩散；语义层 | 是 | 否 | 否 | 是 |
| P3 | 是 | 是 | S、T | 扩散；语义层、时间层 | 是 | 否 | 否 | 是 |
| P4 | 是 | 是 | S、T、E | 扩散；加实体层 | 是 | 否 | 否 | 是 |
| P5 | 是 | 是 | S、T、E | 扩散；加事件层的 relate、merge 分量 | 是 | 否 | 否 | 是 |
| P6 | 是 | 是 | S、T、E | 扩散；事件层四个分量全开 | 是 | 是 | 是 | 是 |

- B0 的记忆层召回为空，只在回答层有意义。它仍要跑完回放，用来检查 Hot 模拟。
- 没开启的层，在逐节点混合时视为不存在。
- 共现只影响召回，不影响 Dream 输入，所以 P6 也能复用缓存。

## 13. 评测器

### 13.1 回放

读取 `eval_set/built/<set>.dialogue.json` 和 `<set>.gold.json`。文件不存在，或者比 `scripts/`、`gold/` 旧时，先运行 `eval_set/build.py`。把所有轮次和探针事件按时间合并成一条时间线；时间相同时，探针排在轮次前面：探针只看严格早于它的轮次，与 `simulate_hot` 一致。回放步骤：

1. 把时钟设为这一轮的时间，写入 Hot。
2. 如果是用户轮，并且阶段开启了召回强化，就做一次召回并写痕迹（§10.8）。
3. 如果是助手轮，就检查压缩和 Dream（§8.1）。
4. 遇到探针时刻，执行探针（§13.2）。

一致性检查：每个探针时刻，评测器模拟出的 Hot 集合必须与 `build.py` 的 `simulate_hot` 结果完全相同（直接 import `eval_set/build.py` 比较，只读），否则报错。

### 13.2 探针与 gap 变体

- 探针是只读分支：用当前的快照、Hot 和 Cold 召回一次，不写痕迹，不改任何状态。可以用断言检查存储版本号在前后没有变化。
- `gap_variants`：状态完全不变，只把 `now` 设为探针时刻加 `offset_days`，然后再召回一次。Hot 内容不变；原文时间窗、π、沉睡和事件边衰减都按新的 `now` 计算。变体可以覆盖 `must_surface` 和 `must_not_surface`。
- `--gap-sweep 0,1,7,14,30,60,120`：对“淡忘”“保留”两类探针，在这些偏移上各召回一次，得到遗忘曲线。偏移为 0 时用基准期望，偏移大于 0 时一律用该探针 gap 变体的期望。
- 防泄漏断言：所有被召回记忆的 sources 和原文命中，时间都必须早于探针时刻。

### 13.3 时间变换

`--time-shift-days N` 和 `--time-scale X` 对所有轮次和探针时刻做同一个仿射变换：`t' = t₀ + X·(t − t₀) + N 天`，t₀ 为第一轮的时间。gold 里的轮次 ID 不变。只要不是恒等变换（X = 1 且 N = 0），所有“时间”类探针，以及消息里能解析出时间区间的探针，报告中一律标为“不计分”：平移会改变星期、月份各段和节日的对应关系。时间戳变了，Dream 提示词也就变了，所以会产生新的模型调用；运行前要打印预计的调用次数。

### 13.4 记忆层打分（确定性）

严格按照 `eval_set/README.md` 执行，并做以下扩展：

- `S_mem` 是近联想、远联想和心上之事中所有记忆的 sources 并集，`S_raw` 是原文命中，`S = S_mem ∪ S_raw`。对 S 和 `S_mem` 分别报告。
- 组命中分严格和宽松两种口径（宽松：同一会话内相差 ±2 轮）。完全命中是指所有组都命中。
- 只命中干扰：在记忆条目上按 README 的定义统计；原文命中另外统计，指落在 `must_not_surface` 中、但不在任何 must 组里的原文轮次。
- 淡忘类：只在 gap 变体上判定，没有任何“只命中干扰”即为通过。保留类：基准时刻和 gap 变体都报告，以 gap 变体为主。
- `soft` 探针单独列出。
- 归因：每个命中的组，记录是哪一部分最先命中的（近联想、远联想、心上之事或原文）。
- 诊断：如果命中某组的只有一条“泛化记忆”（它的 sources 跨 3 个以上会话），单独计数。这样可以看出命中是靠具体记忆还是靠笼统的规律。
- 置信区间：对完全命中率，按探针做 bootstrap（2000 次，seed 0），给出 95% 区间；相邻两个阶段之间做配对 bootstrap，报告差值的区间。

### 13.5 回答层（可选，`--answer`）

- system 用 `prompts/answer_v1.md`：`{{persona}}` 填仓库根目录的 `prompts/chat_background.md`，`{{now}}` 填模拟时间，`{{memory_block}}` 填 §10.7 渲染的记忆块。
- messages：Hot 的滚动摘要，加上最近的 Hot 原文，再加探针消息，做法与 `core/model_client.py` 的 `generate()` 一致。回答模式下默认开启滚动摘要，使用 `core/model_client.py` 里现有的摘要提示词，同样走缓存；只做记忆层时默认关闭。报告要记录这个开关。
- 同一次对照里，各组的回答提示词版本、摘要开关和模型必须完全相同。
- 评判：`judge.py` 提供一个接口，包括逐条判断 rubric 的 `should` 和 `should_not`，以及交换 A、B 顺序各评一次的成对比较。默认 `--judge none`。可以实现一个 `deepseek` 评判器，但报告要标明“评判模型未定，结果仅供参考”。

### 13.6 报告

每次运行输出到 `runs/<时间>_<set>_<preset>/`：

- `config.json`：全部参数、预设、嵌入模型身份、提示词版本和 git HEAD。
- `memory.sqlite`：最终状态。
- `dream_log.jsonl`：每个窗口一行，含轮次范围、缓存键、是否命中缓存、token 用量、各类操作的数量、被拒绝的操作及原因、新建的实体。
- `probes.jsonl`：每个探针和每个变体各一行，含起点（各通道前 5）、时间区间、四部分的条目（ID、正文、分数、π、B、sources、时间标签）、原文命中和打分明细。
- `memories.md`：最终的全部记忆，给人读：正文、时间标签、强度、实体，以及 lineage（历次改写、合并来源）。按首次出现时间排列，沉睡的另起一节。
- `curves.json`：每次 Dream 后的记忆条数（总数、未沉睡、被合并的父条目、实体数）与累计轮数的关系；gap-sweep 的遗忘曲线。
- `summary.json`：按类别列出严格和宽松的组命中率、完全命中率、只命中干扰次数、soft 探针、归因、泛化命中、置信区间、Dream 的 token 用量，以及每个探针时刻未整合的 Cold 轮数。
- `timing.json`：每个探针的召回耗时、召回耗时的 p50 和 p95、Dream 的缓存命中与新增调用次数。这些值每次运行都可能不同，所以不放进 `summary.json`。
- `report.md`：`summary.json` 和 `timing.json` 的可读版本。

`python -m lab suite` 在 `runs/<时间>_suite/` 下另外生成一张对照总表：行是阶段，列是类别，并给出相邻阶段的差值及其区间。

报告中的数值保留 6 位小数；JSON 用 `sort_keys=True, ensure_ascii=False`。同一剧本、同一缓存、同一参数跑两次，`summary.json`、`probes.jsonl` 和 `memories.md` 必须逐字节相同。

### 13.7 命令行

```
python -m lab build-eval
python -m lab run   --set dev_a --preset P3 [--llm fake|real] [--cache-only]
                    [--embedder auto|bge-m3|minilm|hash] [--allow-download]
                    [--answer] [--judge none|deepseek] [--gap-sweep 0,1,7,14,30,60,120]
                    [--time-shift-days N] [--time-scale X] [--dream-retry-failed K]
                    [--out runs/...] [--allow-holdout]
python -m lab suite --sets dev_a,dev_b --presets B0,B1,P1,P2,P2g,P3,P4,P5,P6 [同上的选项]
python -m lab compare RUN_A RUN_B
python -m lab inspect RUN --probe A06 [--variant 60]
python -m lab memories RUN [--at 2026-04-01T00:00+08:00]
```

- `--set holdout_c`（以及 `split` 为 `holdout` 的任何集合）没有 `--allow-holdout` 就拒绝运行。**本任务不要传这个参数。**
- `--llm fake` 使用 `FakeLLM`：对窗口中每条不少于 8 个字的用户消息，生成一条 new，正文为“他说：”加原文前 60 字；再对余弦 ≥ 0.8 的旧记忆各生成一条 touch。它必须确定，不联网。
- `inspect`：打印某个探针的起点、扩散后分数的前 20、四部分条目，以及各组命中了没有、被谁命中。用于调试。
- `memories --at`：从 `memory.sqlite` 和 `dream_runs` 还原某个时刻的记忆清单，只算这之前完成的 Dream。

## 14. 参数初值（`config.py`）

以下都是工程起点。除了 §9 的 τ、θ 标定原则，只允许在开发集上调，而且要在报告里写明调了什么、为什么。

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `d` | 0.5 | 衰减指数 |
| `min_interval` | 1 小时 | B_i 的间隔下限 |
| `tau`、`theta` | −1.522、0.239 | 按 §9 的锚点算出 |
| `pi_min`、`T_d` | 0.05、30 天 | 沉睡 |
| `w_write`、`w_recall` | 1.0、0.5 | 强化权重 |
| `recall_dedup` | 6 小时 | D3 |
| `merge_parent_salience_factor` | 0.5 | |
| `alpha_S / alpha_T / alpha_E` | 1 / 1 / 1 | |
| `S`、`T_s` | 20、0.05 | 语义起点数、softmax 温度 |
| `time_seed_decay` | 12 小时，低于 0.05 截断 | 区间外的时间起点 |
| `entity_recent_weight` | 0.3 | |
| `w_sem / w_time / w_ent / w_event` | 0.15 / 0.25 / 0.3 / 0.3 | 层权重 |
| `lambda_`、`K` | 0.5、3 | |
| `k_sem`、`c_sem` | 10；bge-m3 0.60，MiniLM 0.50，hash 0.30 | 报告余弦分布，供复核 |
| `sigma_time` | 6 小时，3σ 截断 | 时间层 |
| `logical_day_start` | 05:00 | D6 |
| `kappa` | 3 | 共现小样本折扣 |
| `rho` | 0.5 | 事件层反向系数 |
| `beta` | 0.3 | 共同召回增量 |
| `T_e`、`epsilon` | 60 天、0.02 | 事件边衰减、删除阈值 |
| `M` | 32 | 每节点每层的度上限 |
| `k`、`m` | 6、4 | 近联想条数、心上之事条数 |
| `dup_cos` | bge-m3 0.90，MiniLM 0.90，hash 0.85 | 近重复 |
| `remote_ratio` | 0.5 | 远联想门槛 |
| `W` | 14 天 | 原文时间窗 |
| `raw_topk`、`raw_interval_cap` | 6、16 | |
| `cue_recent_turns`、`cue_recent_weight`、`cue_time_weight` | 2、0.3、0.2 | §10.1 |
| `dream_trigger_turns`、`dream_window_max_turns` | 40、40 | |
| `remind_cap`、`remind_chunk_turns`、`remind_per_chunk`、`remind_dup_cos`、`remind_entity_cap` | 30、6、8、0.80、10 | |
| `entities_in_prompt_cap` | 40 | |
| `llm_model`、`dream_max_tokens`、`temperature` | deepseek-v4-pro、4096、0 | |

## 15. 测试（离线）

`python -m pytest tests -q` 必须在没有网络、没有密钥、没有真实嵌入模型的情况下全部通过，并在一分钟内跑完。至少覆盖：

- **Hot/Cold**：压缩规则与 `build.py` 的 `simulate_hot` 一致；三份集合中每个探针时刻的 Hot 集合都相同。
- **时间解析**：§11 表中全部用例；“时间”类探针的区间包含金标准轮次；时间通道忽略将来的区间和与今天有交集的区间（D8）；无法解析时返回空列表。探针 ID 以 A 开头的属于 `dev_a`，B 属于 `dev_b`，C 属于 `holdout_c`。读取 `holdout_c` 的对话和金标准只用于 Hot 一致性和时间解析两项测试，不在它上面召回或打分。
- **整合**：用手写的模型响应测试五种操作的应用；`new:k` 引用（包括 relate 引用排在它后面的 new）；各种非法操作被拒绝并给出原因；merge 的继承与父条目 salience 减半；revise 的 lineage；实体对齐（按 ID、按名字、按别名、新建）与自身过滤；无法解析的响应让窗口保持未整合；提示词渲染逐字节确定。
- **缓存**：同一输入不重复调用；`--cache-only` 缓存未命中时抛出 `CacheMiss`；attempt 进入缓存键；密钥不出现在缓存文件里。
- **唤起**：与阶段无关。P1 和 P6 配置下渲染出的 Dream 提示词逐字相同，即使召回痕迹不同，也包括 merge 发生在召回事件之后的情形。
- **强度**：B_i 的数值例子；τ、θ 满足两个锚点；沉睡判定；召回强化的 6 小时去重；心上之事不被强化；继承来的事件保留原 kind。
- **各层**：时间核与 3σ 截断；PPMI 的数值例子，包括 κ 折扣和父子排除；实体按度均分；有向边的反向为 ρ；度上限 M；事件边衰减与删除。
- **扩散**：总和守恒；λ = 0 时等于起点；无出边节点的自环；逐节点混合只用已启用的层。
- **输出**：近重复过滤；远联想只从非起点中选并受门槛约束；心上之事排除沉睡记忆；原文窗排除 Hot 与窗外轮次；按区间取原文。
- **快照**：Dream 提交后，旧快照的召回结果不变；新快照能看到新版本。
- **打分**：严格和宽松口径、完全命中、只命中干扰（包括“合并记忆同时含有正确来源和干扰来源时不计干扰”）、淡忘只在变体上判定，都用手工构造的小例子测试。
- **端到端**：`FakeLLM` 加 `HashEmbedder`，在 `dev_a` 上跑 P6，两次运行的 `summary.json`、`probes.jsonl`、`memories.md` 逐字节相同；探针前后存储版本号不变；防泄漏断言成立。
- **保护**：没有 `--allow-holdout` 时，对 `holdout_c` 执行 `run` 会被拒绝。

## 16. 执行顺序

1. **骨架**：config、clock、types、store、embed（Hash）、timeparse，以及它们的测试。
2. **写入**：hotcold、llm（缓存与 FakeLLM）、integrate，以及它们的测试。
3. **召回**：strength、layers、snapshot、recall、rawwindow、render，以及它们的测试。
4. **评测**：replay、evaluator、scoring、report、cli；FakeLLM 端到端，确定性测试。
5. **真实运行**：需要密钥和真实嵌入模型。
   1. 解析嵌入模型（§5），记录身份。
   2. 在 `dev_a`、`dev_b` 上跑 P1，生成 Dream 缓存，并检查 `dream_log`：被拒绝操作的比例、JSON 失败次数、记忆条数曲线。拒绝率超过 20%，或者有窗口 JSON 失败，先查原因；只有属于格式问题时，才按 §3 写 v2。
   3. 在两个开发集上用 `--cache-only` 跑 B0、B1、P2、P2g、P3、P4、P5、P6，然后跑 suite。
   4. 回答层：只对 B1 和 P6 在两个开发集上跑 `--answer --judge none`，供人阅读。
   5. 如果没有密钥或者模型调用不通，第 5 步只做第 1 小步，然后用 FakeLLM 跑完 suite，并在报告中说明。
6. **报告**：写 `docs/RESULTS_v1.md`（§18）。

## 17. 验收标准

- [ ] `git status` 显示只有 `Memory_lab/` 下有改动，没有 commit。
- [ ] 离线测试全部通过，并满足 §15 的覆盖要求。
- [ ] FakeLLM 端到端确定性测试通过。
- [ ] 真实运行：P1 在两个开发集上完成；P2–P6 和 B0、B1 在 `--cache-only` 下完成，新增模型调用次数为 0。没有真实条件时，按 §16 第 5.5 步处理并写明。
- [ ] 每次运行都有 §13.6 列出的全部文件，suite 有对照总表。
- [ ] 没有在 `holdout_c` 上运行召回或打分。
- [ ] 聊天侧召回不调用模型、不写记忆图（有测试或断言保证）。
- [ ] `docs/RESULTS_v1.md` 写完。

## 18. 结果报告 `docs/RESULTS_v1.md`

用中文写，给仓库主人看，不要超过需要的长度。内容：

1. 实际跑了什么：集合、预设、嵌入模型身份、模型、提示词版本、缓存命中情况、总 token 数。
2. 对照总表（来自 suite），以及每一步新增的机制带来了什么变化，差值要附置信区间。样本小，结论要写得克制。
3. 记忆长什么样：从 `memories.md` 各挑 5–8 条有代表性的记忆原样贴出，包括好的、坏的和有漂移的，再给出记忆条数曲线。
4. 失败案例：每个类别挑 1–2 个没命中的探针，用 `inspect` 的结果说明原因，比如没写进记忆、写进了但没被唤起、被干扰压过，或者时间解析失败。
5. 偏差：凡是与本任务卡不同的做法，都要列出，并说明原因。
6. 评估集问题：如果有，列出来，不要自己改。
7. 未解决的问题和下一步建议：只列有证据支持的。

## 19. 不要自行决定的事

遇到以下问题，按默认做法执行，并在报告中列出，不要自作主张地扩展：

- 回答层用哪个评判模型。
- 是否增加空闲触发，或探针前强制整合（§8.1 规定不做）。
- 超出 §14 允许范围的调参，尤其不能根据保留集调参。
- 修改评估集，或者修改 v1 提示词的写作策略。
- 主动回想、回放、记忆整理，以及接入 Chat。
