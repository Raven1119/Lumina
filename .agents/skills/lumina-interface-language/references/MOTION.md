# 动效规则 · 固定为 Research Interface Language 1.0.0 / V9

## 精确来源

运行代码位于 `assets/motion-reference.html`；`assets/reference.html` 也嵌入同一 shader 和 Motion 内核，但只使用内容进场、角标等少量动作。两个应用的 `ril-shaders` 与 `ril-engine` 脚本体一致；`verify.py` 检查 SHA-256。目标角标和步骤切换保留原应用函数，定位见 motion-map。

此处的“原版”指原设计包内的原生 DOM/WebGL 适配实现，不表示它们就是未经改动的上游 React 组件。不得再用自制板片旋转冒充本套动效。

## 15 项

| ID | 动作 | 使用限制 |
|---|---|---|
| PS-06 | Dithering | 限定区域的有形双色抖色 |
| PS-10 | Metaballs | 可辨认的团块融合 |
| RB-01 | Threads | 限定区域中的线束与指针偏转 |
| RB-08 | Dot Grid | 邻域响应、点击冲击；不需要 WebGL |
| RB-16 | Particles | 有深度的粒子；不穿过正文 |
| RB-29 | Target Cursor | 有效控件的角标；保留系统指针 |
| RB-34 | Pixel Transition | 局部遮满后换内容，再退去 |
| RB-35 | Animated Content | 新片段进场；已存在正文不重复播放 |
| RB-44 | Meta Balls | 交互融合体；与 PS-10 不同 |
| RB-45 | Decrypted Text | 短编号，40ms 步进；不用在长中文 |
| RB-46 | Scrambled Text | 可选文字样本；不扰动正常正文 |
| RB-48 | Split Text | 新标题；650ms / 30ms 字间错拍 |
| RB-53 | Text Type | 新消息；25ms 步进，无删除循环 |
| RB-59 | Elastic Slider | 有真实数值的控制器；键盘保留 |
| RB-60 | Stepper | 有真实输入的分步表单；不伪造任务阶段 |

不要求一页用完 15 项，不自动把所有面板／数据状态套成动画。

## 固定参数

默认 WAAPI 缓动 `cubic-bezier(.215,.61,.355,1)`。内容进场 600ms / 24px；角标捕获 200ms、收回 300ms；分字 650ms / 30ms；解码步进 40ms；打字 25ms；滑杆回位 440ms；步骤横移 400ms。完整参数见 tokens.json。

像素转场保持 12×12 等分行列，300ms 遮挡、300ms 退去，两次独立随机顺序。完全遮挡时调用业务替换；新请求取消旧任务后最终提交最新状态。不要改成透明闪烁、扫描线或先露新内容再遮盖。

## 生命周期与开关

场景离屏、页面隐藏、暂停或正在输入时停止绘制，卸载调用 `dispose()`。换模式先销毁旧场景，保持一个活跃画布。

原包内核分别管理 `setEnabled()` 与 `setAmbient()`。应用的“全部动作关闭”必须同时调用二者；不能只关交互而让图形场继续运行。本包已在应用接线层处理，未修改冻结内核。

尊重系统 `prefers-reduced-motion`，显式开关不得覆盖系统减少动作的要求。关闭动画仍要立即显示完整文字和最终状态。触屏不强制光标。主题切换立即更新色彩，不重播标题、历史正文或输入内容。

图形场的着色参数保持原参考值。它们是被隔离的可选样本，不承担灰色主题全页面着色，也不冒充 Lumina 的心理状态。
