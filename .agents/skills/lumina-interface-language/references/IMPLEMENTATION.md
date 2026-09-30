# 接入方式

## 自有样式：直接加载

```html
<link rel="stylesheet" href="path/to/assets/tokens.css">
<link rel="stylesheet" href="path/to/assets/primitives.css">
<div class="lui" data-lui-theme="mineral">
  <section class="lui-panel">
    <p class="lui-body-text">真实业务内容</p>
    <button class="lui-btn primary">继续</button>
  </section>
</div>
```

切换主题只改应用根的 `data-lui-theme` 为 `mineral`、`depth` 或 `stone`。也可加载自有 `theme.js`，调用 `LuminaTheme.bind(root)`；返回函数用于卸载事件。它不写 localStorage，不请求网络。

现有 React/Vue/原生页面都可使用 CSS 变量，不要求迁移框架。以项目已有组件为主，不复制演示导航和布局。建议将设计包作为只读参考，实际项目的组件实现放回已有源码目录。

## 源动作：完整应用内定位

根据 motion-map 的 `file`、`script_id`、`anchor` 查看准确实现。源代码保留在完整 HTML 应用内，以与原包一致的方式提供参考；没有发布可单独分发的第三方组件合集。

在用户自身应用中按原许可接入需要的机制，保留其依赖、来源与许可：

- 图形场需要 `ril-shaders` 和 `ril-engine`；原 `Motion.field()` 返回场景与 `dispose()`。
- `Motion` 是经典脚本的顶层词法绑定，参考应用另提供 `window.ResearchMotion`。不要混入现有同名全局，项目接入可在自己的模块作用域内保留原代码。
- 像素切换需要 `source-pixel-grid` 与 `pixelated-image-card__pixel` 样式，宿主必须有定位与裁切。替换业务内容时不要把遮罩本身提前删除。
- 打字／分字需要相应 class 样式；新输入为空时直接写入空字符串，不启动原打字器。
- 角标的 `targetCursor/hideCursor` 是应用内函数，依赖角标 DOM、Motion、指针事件；不是 Motion 的导出 API。
- `stepTo` 是应用内步骤切换函数，依赖真实表单和步骤状态；只迁移运动与生命周期，不照抄演示字段。
- 字符扰动只用在短样本，不往正常长文挂 pointermove。组件卸载时取消自己的事件和计时任务。

## 开关与真实业务

主开关关闭时同时 `Motion.setEnabled(false)`、`Motion.setAmbient(false)`。单独的场景暂停只改变 ambient。聚焦输入时调用 `setWriting(true)`，离开所有输入后恢复。

不要让转场回调决定业务成功。真实提交、响应和核验由业务状态提供，动效只呈现它们。

## 默认不做

不换框架、不增加全局调度器、不引入新状态管理、不重新定义业务页面，也不下载字体。先完成一个真实垂直切片，验证后再扩展。
