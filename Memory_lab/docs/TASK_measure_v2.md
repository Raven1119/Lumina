# 任务卡：测量 v2 重算

v1 的对照表从 P1 开始几乎全是 1.0，看不出 P2–P6 各个机制的作用。原因在测量，不在系统：

- 只要召回里任何一条记忆的来源碰到金标准的某一轮，就算命中；
- revise 累积出来的长主线记忆，一条就覆盖整个故事；
- 心上之事与线索无关，却也计入命中。

本任务只改测量，然后用现有的 Dream 缓存重算一遍。记忆系统、参数、提示词和评估集都不动。

## 已经完成的代码

| 文件 | 改动 |
| --- | --- |
| `lab/measure.py` | 新增。每条探针的测量 v2、汇总，以及 suite 的 `comparison_v2` 表 |
| `lab/replay.py` | 每个探针和变体调用 `measure_probe`，结果写入 `probes.jsonl` 的 `measure` 字段和 `summary.json` 的 `measure` |
| `lab/report.py` | `report.md` 增加“测量 v2”一节 |
| `lab/cli.py` | `suite` 结束时额外写 `comparison_v2.json` 和 `comparison_v2.md`；新增 `python -m lab comparison-v2 <suite目录>`，用于从已有 suite 重新生成这两份表 |
| `tests/test_measure.py` | 新增 4 个测试：两种随机基线与暴力枚举一致；各视图、名次、具体命中、时间区间的手算例子；汇总只取主变体 |

原有的 v1 字段和 `comparison.md` 保持不变。`memlab/` 一行没改，Dream 输入不变，所以缓存完全可以复用。

测量 v2 的指标定义见 `lab/measure.py` 开头的说明，`report.md` 和 `comparison_v2.md` 的表头下面也附了口径。

## 要做的事

1. 把 v1 归档里的缓存恢复到 `Memory_lab/cache/`：包括 `cache/llm/` 和 `cache/embed/`，来源是 `/home/wmywb/Lumina-experiment-archive/Memory_lab_2026-09-25/Memory_lab/cache/`。复制，不要移动；归档保持原样。
2. 离线测试：`python -m pytest tests -q`，应为 46 passed。
3. 重算：

   ```
   python -m lab suite --sets dev_a,dev_b --presets B0,B1,P1,P2,P2g,P3,P4,P5,P6 \
       --llm real --cache-only --embedder bge-m3 --out runs/measure_v2_suite
   ```

   嵌入模型必须和 v1 相同，即本地 `BAAI/bge-m3`，revision `5617a9f61b028005a4858fdac845db406aefb181`。`--cache-only` 下新增模型调用必须为 0；出现 `CacheMiss` 就停下，报告是哪个窗口没命中，不要去掉 `--cache-only` 重新调用模型。
4. 核对：
   - 18 个运行的 v1 指标（`comparison.md`）与 v1 归档里的 `bge_real_suite_v1/comparison.md` 完全一致。不一致说明缓存或嵌入模型没有对上，要先查清。
   - 每个运行的 `timing.json` 中 `new_model_calls` 为 0。
5. 在 `docs/RESULTS_v1.md` 末尾追加一节“测量 v2”（不改动前面已有的内容）：
   1. 两个开发集的“有组探针合计”表，直接取自 `comparison_v2.md`。
   2. 用三五句话回答以下几个问题，每句都要引用表里的数：
      - P1 的 cue 完全命中比随机记忆高多少（提升）？
      - @1 和 @3 是多少？
      - 已写入是多少，也就是有多少失败发生在写入侧？
      - 具体命中比 cue+原文低多少，即有多少命中来自长主线记忆的顺带覆盖？
   3. P1→P2→P2g→P3→P4→P5→P6 相邻阶段在 @1、@3、MRR 上的配对差值。列出区间不含 0 的项；如果没有，就如实写“没有”。
   4. “时间”类探针的区间内来源占比和区间内完全命中，并说明时间通道和时间层（P3）是否改变了它们。
   5. 淡忘（+60 天）的干扰记忆名次。
   6. 不要从这些数推出系统层面的结论，比如“某机制无效”。只写测到了什么，以及哪些东西仍然测不出来。
6. 不改 `memlab/`、`prompts/`、`eval_set/` 和参数；不跑保留集；不跑回答模式。

## 验收

- [ ] 46 个离线测试通过。
- [ ] `runs/measure_v2_suite/` 下有 18 个运行目录，以及 `comparison.md`、`comparison_v2.md`、`comparison_v2.json`。
- [ ] 新增模型调用为 0，v1 指标与归档一致。
- [ ] `RESULTS_v1.md` 追加了“测量 v2”一节。
