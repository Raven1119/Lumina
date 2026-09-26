# Memory Lab v5 实验结果

本报告按 `TASK_v5.md` 的阶段顺序记录。v4 的 P6d 运行、回答与 DeepSeek 盲评只作为历史基线；本轮不重跑 holdout_c。所有新增实验调用使用 `deepseek-flash`，缓存以模型名区分；本轮代码与实验产物只在本地提交，远端只更新本报告、任务卡和设计文档。

## 阶段 1：模型切换

### 执行情况与调用

DeepSeek [官方模型与价格文档](https://api-docs.deepseek.com/quick_start/pricing/)将 V4.1 Flash 的 API 模型名列为 `deepseek-flash`，Anthropic 兼容地址为 `https://api.deepseek.com/anthropic`。实验室 `RealLLM` 默认使用该模型，支持 `LUMINA_MEMLAB_MODEL` 环境变量；`python -m lab` 另支持 `--model` 覆盖。整合写入的模型元数据来自本次实际使用的模型。原 `deepseek-v4-pro` 缓存键及缓存文件未修改，也没有重跑 v4 的基线结果。

阶段开始预计 `model_verify` 1 次，约 100 输入 token、40 以下输出 token。实际由真实 API 返回并解析 `{"ok": true}`，模型名 `deepseek-flash`；响应在模型缓存中先落盘。验证只检查 JSON 结构与 API 可用性，不测试记忆或回答质量。

| 用途 | 新调用 | 输入 token | 输出 token |
| --- | ---: | ---: | ---: |
| model_verify | 1 | 23 | 5 |

离线测试：`../Conversation_Memory/.venv/bin/python -m pytest tests -q`，86 passed；`git diff --check` 通过。新增测试覆盖模型默认值、环境变量、显式参数及模型名参与缓存键。阶段 1 没有偏离任务卡。

## 阶段 2–4

后续阶段的实测结果按完成顺序追加。
