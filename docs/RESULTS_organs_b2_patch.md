# 器官重构 B2 补丁结果

状态：阶段 0 已完成；阶段 1 后续待完成。真实调用 0/50，token 0。

## 阶段 0

起点干净 organs，HEAD 与实时远端均为 3451993955bd2529b79855ca5d7fd3ee740d6a88。未引入 organs-b。

| 基线 | 结果 |
| --- | --- |
| 全树 pytest -q | 411 passed, 4 skipped, 1 warning |
| pytest tests -q | 193 passed, 3 skipped, 1 warning |
| pytest Execution -q | 214 passed, 1 skipped |
| Memory_lab 离线 tests | 86 passed |

受限环境 Execution 首轮 31 failed、183 passed、1 skipped，原因是本地 socket 权限；允许本地 socket 后全树/Execution 通过。零模型调用。

### Docker

Linux Client 29.7.2 已有，但 /var/run/docker.sock 不存在；已有代理 socket 检查客户端 SIGBUS。Windows CLI 可见 Client 与 Docker Desktop Server 29.7.2。默认 workspace 原不存在，创建唯一合成探针后只读/禁网挂载失败：bind source path does not exist: /home/wmywb/Lumina/workspace。未进入容器，读写/网络断言均未验证。R1 记未验证（环境）；R3 使用 B2 临时目录回退。探针移至 /tmp/lumina-organs-b2-patch/ 并校验 SHA-256；移除空 workspace 恢复原状。

### 重复提问诊断

/tmp/lumina-organs-b2/real/ 已不存在。/home/wmywb/Lumina-organs-b2-backup-20261001/prior_b_real 是 B 阶段备份，不是 B2。Windows 临时 scenarios 尚保存 B2 输入/产物，没有 Nervous SQLite 或事件日志。B2 报告记载 R2_run1、R2_run2 均重复提问一次，但每次首问后的 Wait(MIND_REPLY)、答复送达及格式、第二问对应决策均 UNKNOWN。不能认定重放、重复发布或模型新决策。不猜测修改 Execution V2。pool._questions 已按 cognitive event ID 的 seen_questions 去重。

### 过时提问诊断

B2 报告记载 R5_run1、R6_run2 在 helper 终态后 answer_helper 返回 helper_unavailable。精确提问/交回时间与是否 Wait 均因日志缺失 UNKNOWN。代码显示 run_events 没有检查队列中 agent.question 对应的最新 helper/question 状态，可在交回后启动提问思考；这是可独立复现的缺口。

## 偏差与待完成

附录 C 在“发现任务本身可能有”处截断，已经请求补发；未自行续写。后续修复/测试、真实情景、推送审计和双分支交付尚未完成。
