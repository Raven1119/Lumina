# Memory_lab

Conversation Memory 重设计的实验室：在 Dream 中把对话整合成会强化、会淡忘的要点记忆；每轮聊天只做本地计算，在语义、时间、实体、事件四层图上扩散激活来召回。评测全自动：模拟时钟回放剧本，在指定时刻发出探针并打分。

| 路径 | 内容 | 状态 |
| --- | --- | --- |
| `docs/DESIGN.md` | 设计规范 | 已定稿 |
| `docs/TASK_CARD.md` | 实现任务卡 | 已执行；结果见下行 |
| `docs/RESULTS_v1.md` | 两个开发集的运行结果、限制与偏差 | 已写 |
| `prompts/integrate_v1.md` | Dream 整合提示词 | 已写 |
| `prompts/answer_v1.md` | 回答层提示词（可选） | 已写 |
| `eval_set/` | 三份剧本、90 条探针、构建与校验脚本 | 已写，`python eval_set/build.py` 通过 |
| `memlab/` | 记忆系统 | 实验实现 |
| `lab/` | 评测器与实验设施 | 实验实现 |
| `tests/` | 离线测试 | 42 项通过 |
| `cache/` | 模型响应与嵌入缓存 | 运行后生成 |
| `runs/` | 运行输出（不提交） | 运行后生成 |

实现完成后的常用命令（在本目录下执行）：

```
../Conversation_Memory/.venv/bin/python eval_set/build.py                         # 构建并校验评估集
../Conversation_Memory/.venv/bin/python -m pytest tests -q                        # 离线测试
../Conversation_Memory/.venv/bin/python -m lab run --set dev_a --preset P1 --llm real
../Conversation_Memory/.venv/bin/python -m lab run --set dev_b --preset P1 --llm real
../Conversation_Memory/.venv/bin/python -m lab suite --sets dev_a,dev_b --presets B0,B1,P1,P2,P2g,P3,P4,P5,P6 --llm real --cache-only
../Conversation_Memory/.venv/bin/python -m lab inspect runs/<run> --probe A06
```

本轮未在保留集 `holdout_c` 上运行召回或打分。显式传入 `--allow-holdout` 才能运行它。
