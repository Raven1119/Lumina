# 回答层评审工具

历史轮次由 Claude 盲评。v3 阶段 0 加入缓存式 DeepSeek 盲评工具；仓库主人随后要求由 Codex 直接评测，因此此后的评审不再调用 DeepSeek，结论待独立复核。详见 `CALIBRATION.md` 与 `docs/RESULTS_v3.md`。

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
| `rounds/v2_P6_v1_vs_e1` | P6：answer_v1 → answer_v2（时间感知） | E1 21 胜 13 负，p≈0.23；修好了 A01 和 A26+60 |
| `rounds/v2_P6_e1_vs_e2` | P6：answer_v2 → answer_v3（记忆用法那一句） | 无净收益（E1 16，E2 12） |
| `rounds/v2_e1_B1_vs_P6` | answer_v2 下 B1 与 P6 | P6 23 胜 4 负，p≈0.0003；说错往事 2.1% 对 10.7% |
| `rounds/v2_e2_B1_vs_P6` | answer_v3 下 B1 与 P6 | 持平（18 比 17） |
| `rounds/v3_p1_P6_vs_P6u` | P6 与回答使用门控 P6u | Codex 直接审阅：P6u 17 胜、P6 16 胜、37 平；采纳 P6u，待独立复核 |
| `rounds/v3_p1_P6u_vs_P6u10` | P6u 与 `pi_recall=0.10` | 70 平；未采纳 P6u10 |
| `rounds/v3_p2_int1_vs_int2` | integrate_v1 与 v2 | v2 11 胜、v1 24 胜、35 平；未采纳 v2 |
| `rounds/v3_p3_render1_vs_render2` | render_v1 与 v2 | v2 12 胜、v1 13 胜、45 平；未采纳 v2 |
| `rounds/v3_p4_long_B1_vs_final` | 长集 B1 与最终配置 | Codex 直接审阅：final 19 胜、B1 10 胜、39 平；说错往事 1/68 对 3/68，待独立复核 |

第二轮的总结见 `rounds/v2_FINDINGS.md`。
