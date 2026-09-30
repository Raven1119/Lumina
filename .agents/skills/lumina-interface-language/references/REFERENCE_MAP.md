# 文件与基线

视觉源：本对话中已确认的 `Lumina_Quiet_Bioindustrial.html`。保留静态形态、矿灰与幽青颜色、材质、排版；暮紫替换为岩灰；旧自定义动画被原包动作替换。

动效源：`Raven1119/CyberScientist/.design/research-interface-language/` 中的 1.0.0 副本。其 manifest 声明 reference.html SHA-256 为 `7d29f98c9c7881b7fc47b7299008e3f3b2bc3294d02283d102960575b575e7c1`，Git blob 为 `3231d674f5ab7fc58b12e281b75fe91ff7df147d`。

读取方式：通过 GitHub 连接器按行读取 shader、Motion 内核及角标／步骤函数。原 Library 压缩包未获得 raw-byte 导出路径，因此没有声称新包与原 ZIP 逐字相同。新包对恢复的脚本体和函数单独计算并冻结 SHA-256，防止皮肤迁移误改动作。

| 目标 | 文件 |
|---|---|
| 判断 Lumina 的视觉 | `assets/reference.html` / `screenshots/` |
| 检查全套动作 | `assets/motion-reference.html` |
| 找某动作代码 | `assets/motion-map.json` |
| 检查冻结来源 | `assets/motion-source-lock.json` |
| 接入静态组件 | `tokens.css` / `primitives.css` / `examples/starter.html` |
| 确定当前包完整性 | `manifest.json` / `verify.py` |

未来更新视觉时不修改冻结动作；确认动作要变更时，应明确提升版本并记录差异。源索引中的上游 main 链接仅作溯源，不是自动下载或锁定版本。
