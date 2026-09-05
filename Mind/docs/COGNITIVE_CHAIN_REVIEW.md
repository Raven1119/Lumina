# D6 code-review

固定基线：本地 D5 后置投影修复，快照位于
`Mind/fixtures/cognitive_chain_d6/diagnostic/source`；不是远端旧 HEAD。
具体 diff：`Mind/fixtures/cognitive_chain_d6/implementation.diff`。
Spec：用户 D6 任务与 `COGNITIVE_CHAIN_TASK.md`。采用 Ponytail / codebase-design，
code-review 的 Standards 和 Spec 两轴由两个独立 reviewer 并行复核。

## Standards

PASS，无剩余代码阻断项。先前发现的两项已修复：

- owner 顶层使用的 `jsonschema` 必须在 requirements 中显式声明，已固定 4.26.0。
- D6 已发出调用却未知结果时必须停止后续物理调用和事件；新增硬停及实际路径回归。

共享字段定义留在现有 owner；没有契约框架、语义审核 Agent 或额外能力。
v2 仅替换提示与说明，D4/D5/v1 请求重建和旧结果不变。

## Spec

工程实现与有界诊断符合范围；**不能报告任务所需的语义修复全面通过或真实指导闭环通过**。

- CAUSAL VALIDITY：有/无错误旧条目只排除其“必要原因”解释；没有识别模型内部唯一根因。
  两开发版本均 1/4，不能由换了一个通过题宣称总体改善。
- MIND/EXECUTION BOUNDARY：独立 Directive 末尾包含写 marker 和 ClaimComplete，
  原文因此未投递；此失败必须保留。正确条件/数值不使执行序列变成高层指导。
- CONTEXT CONTAMINATION：Mind 未继承执行 transcript 或建模代码；但共同 goal 含执行完成协议，
  其可能诱导复述的作用尚待独立检验。
- AUTHORITY：严格来源引用、原子提交、未知结果不重试、一次性绑定和 Docker 隔离保持。
  文本越过抽象边界没有产生工具权限；审查仅测量/扣住投递，未替模型修改认知。
- PONYTAIL：复用已有 owner/Trace/host/bridge，未扩充模块。当前 schema 的结构限制共用定义；
  历史 schema 保留用于重建，不为统一命名删除历史。
- 验收：有效旧引用、替换 ID 可合法；整份最终认知必须正确。拒绝/超长不是 NoChange。
  独立 2/3，认知语义 9/9，但送达 0，不能把 Execution 独立成功算作采用。
  独立 reviewer 复核全部七条 Execution wire，无主要评分异议。最终四条只保留策略知识，
  没有明确记录最终 count/total；报告已限定为反馈接受、状态与结果相容。

Standards 剩余工程发现 0；Spec 的行为验收仍有明确未达项：指导送达闭环及全面语义修复。
资格 NOT_YET；价值 INCONCLUSIVE。完整原始依据和限制见 `COGNITIVE_CHAIN_RESULT.md`。
