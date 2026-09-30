# Agent 接入口

本包沿用 research-interface-language 1.0.0 的安装约定，不依赖联网发现插件。

| 选项 | 项目目录 | 指令入口 |
|---|---|---|
| 默认 / `--agent codex` | `.agents/skills/lumina-interface-language/` | 非空 `AGENTS.override.md` 优先，否则 `AGENTS.md` |
| `--agent kimi` | `.kimi/skills/lumina-interface-language/` | `AGENTS.md` |
| `--agent claude` | `.claude/skills/lumina-interface-language/` | `CLAUDE.md` |

`--global` 改为用户主目录，不修改全局指令。这里只记录本包的写入行为；未重新验证平台最新技能发现能力。

如果 Agent 没有自动加载技能，直接给出安装后的 `SKILL.md` 路径，要求读取。远程运行环境需要能访问同一套文件；本机安装不等于云端已加载。

同一项目同时有研究语言和 Lumina 语言时，明确本次前端使用 Lumina。安装不会删除或改写 Research 的技能与区块。后续任务不要混用两套视觉。
