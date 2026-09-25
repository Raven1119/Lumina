# 回答层评审工具

评审不由 Codex 执行。仓库主人把新的回答运行推到 GitHub 后，由 Claude 在会话里完成评审，然后提交本目录下的轮次结果。

## 一轮评审的步骤

1. 生成材料（在 `Memory_lab/` 下执行）：

   ```
   python judge/build_packets.py --round judge/rounds/<轮次名> --labels <甲>,<乙> \
       --pair dev_a:<甲的运行目录>:<乙的运行目录> \
       --pair dev_b:<甲的运行目录>:<乙的运行目录>
   ```

   每个运行目录里要有该回答运行的 `probes.jsonl` 和 `config.json`。评审条目是每条探针的基准时刻，加上淡忘和保留类的 +60 天变体，所以回答运行至少要用 `--answer-scope primary`。
2. 评审：每个集合的 `packet_<set>_1.json` 和 `packet_<set>_2.json`（X/Y 顺序互换）各交给一个独立的评审 agent，按 `INSTRUCTIONS.md` 执行。评审 agent 看不到系统身份，写出 `out_<set>_<1|2>.json`。
3. 汇总：`python judge/aggregate.py judge/rounds/<轮次名>`，生成 `summary.json` 和 `summary.md`。只有两遍都偏好同一方才算胜，否则计为“不一致”。

## 局限

- 两遍评审用的是同一个模型，一致率不能当作独立评审者之间的一致性。
- 评分要点由写评估集的人编写，天然偏向设计者的理解。
- 结果适合用来定位问题，不能作为最终结论。

## 轮次

| 轮次 | 比较 | 结论摘要 |
| --- | --- | --- |
| `rounds/v1_B1_vs_P6` | answer_v1 下 B1 与 P6 | P6 略好但不显著（16 胜 12 负，p≈0.57）；原因分析见该目录的 `FINDINGS.md`，后续实验见 `docs/TASK_answer_v2.md` |
