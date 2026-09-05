# D5 code review

固定基线：本地 D4 冻结源码 `Mind/fixtures/protocol_recovery_d4/loop/source/`，
对应工作区 HEAD `9d63da7311baa7611782cc8079fb09e9d81f4e25`。
只审本轮 D5 增量，不把以前的脏文件重新归为本轮实现。
规范：根/Mind AGENTS、Ponytail、North Star/Mind 设计、用户 D5 任务和
`SEMANTIC_REVISION_TASK.md`。按 code-review 技能，由独立 Standards/Spec
审查者并行审查，审查意见没有进入运行中的模型上下文。

## Standards

最终无新增代码阻断。开发期发现并修复：

- 新工作区输入投影须只暴露 policy/samples/summary，不能把辅助代码和日志当作
  Mind 证据。保留三个文件白名单、长度限制和截断标记，新增泄漏回归。
- 投递前须同时检查方向相关性、未知保留与整份状态语义字段，不能只看合法文本。
- 必须在宿主写入新输入前核对 Execution 未改写初始输入，否则可能掩盖权限破坏。

实跑后确认一项投影缺陷：再次 JSON 编码文件原文，使模型原始引文与来源中的
反斜杠/换行不一致。验收结束后仅改为标签＋原文，记录 `task-files-d5-v2`。
三条历史失败引文在完全不改写引用的情况下均严格匹配；没有放宽 `_basis`。
最终针对性回归 46 passed；根回归 347 passed、24 skipped。

这是已确认缺陷的最小修复。它不改变冻结 verdict，也不证明修复后的真实模型效果。

## Spec

代码保持职责边界和冻结纪律，但任务的语义修复与真实闭环验收**仍未完整达到**：

- `archived_condition` 在旧契约和两个候选中都保留错误 discriminator；v2 主动
  重新提交它。不能归因于上下文丢失或引用失败。
- `archived_proposition` v2 返回 2123 字符被拒。未接受文本中的修订意图不能算
  修复；尾部 `before writing verified and ClaimComplete` 还带有实施操作倾向。
  首要失败分类仍为 serialized_limit，且从未送达。
- 独立真实工作区首事件修复两个脚本种子错误；后两次 `ungrounded_basis`，
  revision 保持 2、送达为零。Execution 自行完成不能作 Mind 反馈链成功证据。
- `ambiguous_recovery` 三次均已接受，整份状态语义也通过。沿用仍有效的旧引用
  合法；最终未引用重复反馈只违反冻结脚本的额外门槛。保留 frozen 1/3，另外
  报告两个语义对照 2/2、认知接受 7/9，不进行事后改分。
- 后置投影修复只获得确定性证据；没有新调用、隐藏重跑或模型答案改写。

## 专项检查

| 维度 | 审查结论 |
| --- | --- |
| SPEC | 部分达到；旧语义错误和真实反馈链阻塞已明确保留。 |
| CAUSAL VALIDITY | 种子明确为脚本；开发/冻结样本分开。没有合法完整 A/B，也没有送达，不能宣称行为收益。 |
| MIND/EXECUTION BOUNDARY | Mind 只认知提交/只读取证，Execution 自选实现；失败 Directive 候选未被宿主改写或送达。 |
| CONTEXT CONTAMINATION | Mind 不继承 Execution transcript；白名单任务原文有界，代码和日志留在审计。审查意见不是运行时输入。 |
| AUTHORITY | 初始和更新后输入不可被 Execution 改写；方向绑定仍适用 run/decision；严格引用、原子提交和 D4 恢复边界不变。 |
| PONYTAIL / OVERENGINEERING | 扩展现有实验入口和提示，没有新 Organ、语义审核 Agent、全局规则系统或生产拓扑。 |

Standards：最终 0 个未修复代码阻断；Spec：仍有语义与闭环验收缺口。
比较资格 **NOT_YET**；独立 Mind 行为价值 **INCONCLUSIVE**。
