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

## 阶段 2：记忆升级

阶段开始预计：归纳 0、整合 0、回答 0、评审 0 次真实调用；本阶段只实施与离线验证。实际新增模型调用和 token 均为 0。

P7 在 P6d 基础上启用规律归纳、lift 联想位、render_v3、answer_v4、integrate_v3 和剧本回复来源。每次成功整合后从本次新增或修改的记忆出发，按共现、relate 路径和异主题相似情形选组；每组跨至少两个逻辑日，去重并截到 6 组。规律是普通记忆，沿用记忆的来源、出现时间和写入事件，并建立 merge 事件边；`existing` 只补来源、一次 touch 和边，`none` 仅写日志。候选为空时不调用模型。归纳解析失败时单独记录，已完成的整合不回滚。

联想位以当前激活除以均匀平时激活，加入中位数平滑；平时激活按快照版本与逻辑日缓存。候选须非起点、清醒、未近重复，并通过可及度和绝对激活门槛；无候选就留空。render_v3 只给联想位附原因。answer_v4 把首个“回复：”之后的文本作为回答，保存“想起：”一行；缺标记时同请求 attempt 1 重试，仍缺则清除“想起：”行并记录回退。

离线测试 `../Conversation_Memory/.venv/bin/python -m pytest tests -q`：97 passed；覆盖三类候选、两日约束、上限、规律继承、实例不变、existing/none、lift 缓存和筛选、answer_v4 重试回退。P6 召回 JSON 字节的 SHA-256 与阶段 1 的旧实现 `ede2681` 一致：`b0a7abf39b884769ad51eca20a052c639d8bb821225e5a20747d9931a8079818`。此处是固定样例的逐字节回归，完整 v4 实验不重跑。

## 阶段 3–4

后续阶段的实测结果按完成顺序追加。
