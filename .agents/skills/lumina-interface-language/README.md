# Lumina Interface Language · 1.0.0

**沉静的生物工业。** 视觉继承已确认的 Lumina 标本，主题为 **矿灰 / 幽青 / 岩灰**；原暮紫替换为全中性灰色系。动效使用 **research-interface-language 1.0.0 / V9** 的 15 项实现。

## 直接打开

解压后双击 **START.html**。

- **风格标本**：已确认的形态、材质、排版与三种主题。中央形态仅为标本，不规定头像或产品布局。
- **15 项动效验收**：在 Lumina 外观中检查原版动作。六种场景择一展示；WebGL 不可用时可切换“响应点阵”，其他文字和控件动作仍可用。
- **起步样本**：可复用的基础 CSS、主题切换、手动保存本地草稿。无业务依赖。

运行不需要 npm、构建器或联网。图形场景需浏览器支持 WebGL/WebGL2；字符、控件与点阵不需要 WebGL。环境若禁止打开本地文件，可在包目录运行 `python -m http.server 8000` 后访问本地 START.html。

## 安装到项目

在解压后的 `lumina-interface-language` 目录执行（Python 3.9+）：

```sh
python install.py --project "D:/Projects/Lumina"
```

与原包使用相同安装约定：默认写入 `.agents/skills/lumina-interface-language/`，在项目 `AGENTS.md` 加入专用引用区块。存在非空 `AGENTS.override.md` 时，Codex 接入口写入该文件。保留原有其他内容。

```sh
python install.py --project "D:/Projects/Lumina" --agent kimi
python install.py --project "D:/Projects/Lumina" --agent claude
python install.py --global
```

Kimi 使用 `.kimi/skills/` 与 `AGENTS.md`，Claude 使用 `.claude/skills/` 与 `CLAUDE.md`。全局安装只复制技能，不改全局规则。安装器沿用原包目录约定；本包未重新验证各平台的技能发现流程。

**安装只添加设计资料与入口，不改业务代码、依赖、Git 或现有 Research 设计包。** 同样内容重复安装不重复追加；已有不同内容时默认拒绝覆盖。升级可加 `--replace`，旧副本和指令文件会备份至 `.design-language-backups/`。

不使用脚本时，把整个文件夹放入项目技能目录，再粘贴 `templates/AGENTS.frontend.md` 或 `CLAUDE.frontend.md` 的引用区块。

## 交给开发 Agent

> 使用 lumina-interface-language。先读取 SKILL.md，查看 assets/reference.html 和 screenshots，按本包矿灰／幽青／岩灰的视觉基线实现本次前端任务。动效读取 assets/motion-reference.html，保持 Research Interface Language 1.0.0 的原算法、时序及生命周期。不要采用 CyberScientist 的外观，不恢复暮紫和旧标本的自定义形态动画，不复制演示布局、形态或假数据。只完成本次最小功能切片，并验证主题、窄屏、键盘和减少动效。

## 接入资源

| 文件 | 用途 |
|---|---|
| `SKILL.md` | Agent 的短入口 |
| `assets/reference.html` | Lumina 视觉标准及有限交互，内嵌运行依赖 |
| `assets/motion-reference.html` | 全部 15 项源动效的可运行应用与代码来源 |
| `assets/tokens.css` / `tokens.json` | 同源颜色、字体、尺寸、动效参数；`--lui-*` 命名空间 |
| `assets/primitives.css` / `theme.js` | 自有静态控件和主题工具；无第三方运行时 |
| `assets/motion-map.json` / `motion-source-lock.json` | 动效定位与冻结源块 SHA-256 |
| `examples/starter.html` | 直接可用的样式起点 |
| `references/` | 设计、动效、接入、来源与验收规则 |
| `assets/screenshots/` | 从本次成品实录的三主题／窄屏预览 |
| `install.py` / `verify.py` | 安全安装、完整性与源块校验 |
| `tests/` / `VALIDATION.md` | 测试与实际结果 |
| `licenses/` | 保留的第三方许可及新增内容范围 |

## 校验

```sh
python verify.py
python -m unittest discover -s tests -p "test_*.py"
```

浏览器测试为可选项，另需 Playwright 与 Chromium，见 `tests/browser_smoke.py`。测试输出默认放临时目录，不改包内清单。实际完成／未完成的环境测试记录在 `VALIDATION.md`。

本包没有字体文件、原作人物／场景素材。第三方动作保留在完整参考应用中，未拆成独立发行的组件库；许可见 `licenses/`。源码来自已安装的原包 1.0.0 副本，完整原 ZIP 未逐字比对；冻定位置信息见 `motion-source-lock.json`。
