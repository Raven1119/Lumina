# 任务卡：回答层实验 v2（时间感知、记忆用法、召回阈值）

第一轮回答层评审的结果和原因分析见 `judge/rounds/v1_B1_vs_P6/summary.md` 与 `FINDINGS.md`。已由数据确认的原因有：

- 回答模型不知道 Hot 里每句话是什么时候说的，把 13 小时甚至 60 天前的话当成刚说的；
- 召回给出的联想，回答没有用上；
- 没有召回阈值，60 天前的闲聊仍排第 2。

本任务做三个实验，每个实验只改一个变量；另外补一处记录，用来核对滚动摘要是否导致了 B1 的错误。

- **E1 时间感知**：回答提示词 answer_v2，Hot 和当前消息带上时间。与 answer_v1 对照。
- **E2 记忆用法**：answer_v3，只改提示词里“怎样用想起的事”那一句。与 answer_v2 对照。
- **E3 召回阈值**：π 低于阈值的记忆不能被想起，但仍在扩散中当桥梁。预设为 P6r10，与 answer_v3 下的 P6 对照。

回答层的评判不在本任务内。跑完后提交回答结果，由仓库主人另行评审。

## 1. 约束

- **Dream 和滚动摘要一律命中缓存，只有回答允许新调用。** 缓存就是上一轮恢复到 `Memory_lab/cache/` 的那份。出现 Dream 或摘要的 `CacheMiss` 就停下并报告，不要放开限制去重新调用模型。
- 可以修改的文件：
  - `lab/`：llm、answer、replay、hotcold、cli、report 等；
  - `memlab/config.py`、`memlab/recall.py`：只做 §2.4 的召回阈值；
  - `tests/`。
- 不要修改的文件：
  - `prompts/integrate_v1.md`、`prompts/answer_v1.md`，以及本任务提供的 `answer_v2.md`、`answer_v3.md`；
  - `eval_set/`、`judge/`；
  - `memlab/` 中与召回阈值无关的部分；
  - 仓库其他目录。生产端 `core/model_client.py` 同样不带时间，但本任务不改它。
- answer_v1 的请求必须与原来逐字节相同。已有缓存的键不能变：不要改缓存键的计算方式，也不要改 v1 的消息拼法。
- 不跑保留集，不做评判，不接入 Chat。
- 离线测试不联网，不需要密钥。

## 2. 代码改动

### 2.1 缓存：只放开指定用途的新调用

- `CachedLLM` 增加 `allow_new: set[str] | None`，`None` 表示全部允许。
- 缓存未命中时，如果 `cache_only` 为真，或者本次调用的 `purpose` 不在 `allow_new` 里，就抛出 `CacheMiss`。
- 命令行增加 `--new-calls answer`（逗号分隔的用途列表）。不传时行为不变。

### 2.2 回答：answer_v2 和 answer_v3 的消息拼法

- 命令行增加 `--answer-prompt v1|v2|v3`，默认 v1。
- system 提示词分别读 `prompts/answer_v1.md`、`answer_v2.md`、`answer_v3.md`，占位符替换方式与 v1 相同。
- v1 的消息拼法保持不变。
- v2 和 v3 的 messages 按以下规则拼。时间都用 +08:00 的模拟时间；“周X”用一、二、三、四、五、六、日；时、分补足两位。
  1. **滚动摘要**：`近期对话摘要（截至 {M} 月 {D} 日）：{摘要}`，角色为 user。日期取最近一次被摘要的轮次（即最近一次压缩时移出 Hot 的最后一轮）的时间。`HotCold` 需要记录这个时间。没有摘要时不加这条。
  2. **Hot 中他说的话**：`（{M}月{D}日 周{X} {HH:MM}）{原文}`。
  3. **Hot 中林素说的话**：原文，不加时间。
  4. **当前消息**：`（{M}月{D}日 周{X} {HH:MM}，距上一句{间隔}）{消息}`。间隔从 Hot 最后一轮（不论角色）的时间算到当前时刻：
     - 不足 1 小时：省略“，距上一句{间隔}”；
     - 1 到 47 小时：写“N小时”，向下取整；
     - 48 小时及以上：写“N天”，向下取整；
     - Hot 为空时省略。
- 例：A01 的当前消息应为 `（4月5日 周日 01:30，距上一句13小时）好累`；A26+60 为 `（6月11日 周四 18:00，距上一句61天）食堂今天的菜又咸了`。数字与汉字之间都不加空格。

### 2.3 记录与范围

- 回答运行时，在 `probes.jsonl` 每行的 `answer` 里增加 `context`：
  - `prompt_version`；
  - `system_sha256`；
  - `summary`：摘要原文；
  - `summary_until`；
  - `messages`：实际发送的 messages 列表，原样记录。
- 记忆块已经记录在 `rendered`，不必重复。v1 运行也记录 `context`；这只改日志，不改请求。
- 命令行增加 `--answer-scope primary|all`，默认 all。primary 只为基准时刻和金标准里的 gap 变体生成回答，即淡忘和保留类的 +60 天。gap-sweep 的其他偏移照常召回、照常打分，但 `answer` 为 null。

### 2.4 召回阈值

- `Config` 增加 `pi_recall: float = 0.0`。
- 在 `recall()` 选取近联想、远联想和心上之事时，排除 π < `pi_recall` 的记忆，由后面的候选补位。起点和扩散不变：低于阈值的记忆仍然传导激活。
- 新增预设 `P6r05`、`P6r10`、`P6r20`：分别是 P6 加 `pi_recall` = 0.05、0.10、0.20。
- 回答模式目前只允许 B1 和 P6，要放开到 `P6r10`。

### 2.5 测试

至少新增以下测试，全部离线：

- 消息拼法：
  - 给定 Hot、摘要时间和当前时刻，v2 的 messages 与预期逐字相同。覆盖不足 1 小时、13 小时、61 天三种间隔，以及空 Hot。
  - v1 的 messages 与改动前逐字相同。
- `--new-calls`：answer 可以新调用；dream 和 summary 缓存未命中时抛出 `CacheMiss`。
- `pi_recall`：
  - 低于阈值的记忆不出现在三个输出部分，并且被下一个候选补位；
  - 扩散后的激活总和仍为 1；
  - `pi_recall = 0` 时与 P6 的召回结果完全相同。
- `--answer-scope primary`：只有基准时刻和金标准 gap 变体有回答。
- 原有 46 个测试继续通过。

## 3. 运行

在 `Memory_lab/` 下执行，嵌入模型固定为本地 `BAAI/bge-m3`，revision `5617a9f61b028005a4858fdac845db406aefb181`。

1. **离线测试**：`python -m pytest tests -q`。
2. **召回阈值扫描**（只算记忆层，不调用模型）：

   ```
   python -m lab suite --sets dev_a,dev_b --presets P6,P6r05,P6r10,P6r20 --llm real --cache-only \
       --embedder bge-m3 --out runs/pi_recall_sweep
   ```

   这一步 P6 的 `summary.json` 和 `probes.jsonl` 必须与测量 v2 里 P6 的同名文件逐字节相同（`config.json` 会多出 `pi_recall` 字段，不在比较之列）。
3. **回答运行**：共 10 次，每次都带以下公共参数：

   ```
   --answer --answer-scope primary --new-calls answer --llm real --embedder bge-m3
   ```

   | 实验 | 预设 | 回答提示词 | 集合 | 输出目录 |
   | --- | --- | --- | --- | --- |
   | E1 | B1、P6 | v2 | dev_a、dev_b | `runs/answers_v2/e1_<set>_<预设>` |
   | E2 | B1、P6 | v3 | dev_a、dev_b | `runs/answers_v2/e2_<set>_<预设>` |
   | E3 | P6r10 | v3 | dev_a、dev_b | `runs/answers_v2/e3_<set>_P6r10` |

   例：`python -m lab run --set dev_a --preset P6 --answer-prompt v2 <公共参数> --out runs/answers_v2/e1_dev_a_P6`。

   每次运行约 35 次回答调用，合计约 350 次，预计输入 60 万到 70 万 token。每次运行的 `timing.json` 里，新增调用都必须只有 answer 一种用途。

## 4. 核对

- E1 和 E2 中 B1、P6 的记忆层结果（`summary.json` 的 `by_category` 和 `measure`），与测量 v2 的 B1、P6 相同，因为回答不影响召回。E3 的记忆层结果与阈值扫描里的 P6r10 相同。
- 随机抽 3 条回答，检查 `answer.context.messages`：时间和间隔格式要符合 §2.2，林素的话没有被加上时间。
- 如果回答里出现了模仿时间标注的写法，比如以“（4月5日”开头，统计出现了多少次，写进报告，不要修改提示词。

## 5. 报告：新建 `docs/RESULTS_answer_v2.md`

只陈述测到了什么，不做回答质量的评判。评判由仓库主人另做。内容：

1. **实际执行**：测试结果、各运行的新调用次数和 token 数、缓存命中情况、偏离本任务卡的地方。
2. **召回阈值扫描表**：两个集合，P6、P6r05、P6r10、P6r20 各一行。列：
   - 淡忘的 +60 天通过率（v1 的 forgetting_pass）；
   - 淡忘干扰名次（v2）；
   - 保留类的 +60 天完全命中；
   - 有组探针合计的 cue 完全命中、@1、@3、MRR@20。
3. **回答原文对照**：以下条目在 v1（`answers_v1/`）、E1、E2、E3 下的回答原文并排列出，不加评价。
   - dev_a：A01、A02、A11、A12、A13、A20、A24+60、A26+60、A28+60；
   - dev_b：B04、B08、B11、B12、B13、B21、B22。
4. **两个核对项**：
   - B1 的 B04 和 A20 在 v2 运行中记录下来的摘要原文；
   - 回答长度的中位数：v1 与各实验分别统计。

## 6. 提交到 GitHub 的内容

**提交：**

- 代码：`Memory_lab/lab/`、`Memory_lab/memlab/config.py`、`Memory_lab/memlab/recall.py`、`Memory_lab/tests/`。
- 提示词：`Memory_lab/prompts/answer_v2.md`、`Memory_lab/prompts/answer_v3.md`。
- 文档：
  - `Memory_lab/docs/TASK_answer_v2.md`（本文件）；
  - `Memory_lab/docs/RESULTS_answer_v2.md`；
  - `Memory_lab/README.md`：在文件表中补上新文档和 `answers_v2/`、`judge/`。
- 评审工具：`Memory_lab/judge/` 整个目录，包括第一轮评审的材料和结果，由仓库主人随本任务提供。
- 回答结果：10 次回答运行各自的 `probes.jsonl` 和 `config.json`，复制到 `Memory_lab/answers_v2/<运行目录名>/`。评审只需要这两个文件。
- 阈值扫描的对照表：`runs/pi_recall_sweep/` 下的 `comparison.md`、`comparison_v2.md`、`comparison_v2.json`，复制到 `Memory_lab/answers_v2/pi_recall_sweep/`。

**不提交：**

- `runs/` 下的其他任何内容：`memory.sqlite`、`dream_log.jsonl`、`memories.md` 等；
- `cache/`，即模型响应和嵌入缓存；
- `.env.local` 和任何密钥；
- 本机归档目录里的内容；
- 补丁文件、压缩包。

提交后 push 到 `Execution_lab2`。

## 7. 验收

- [ ] 新旧测试全部离线通过。
- [ ] 阈值扫描里 P6 的 `summary.json`、`probes.jsonl` 与测量 v2 逐字节相同；Dream 和摘要的新增调用为 0。
- [ ] 10 次回答运行完成，每次新调用只有 answer 一种用途；E1、E2 的记忆层结果与测量 v2 一致。
- [ ] `RESULTS_answer_v2.md` 写完，包含 §5 的四部分。
- [ ] 按 §6 提交并 push，没有提交不该提交的内容。
