# Working in Memory_lab

Memory_lab 是 Conversation Memory 重设计的实验室。这里的规则补充仓库根目录的 `AGENTS.md`；两者冲突时，本目录内以这里为准，根目录的“Preserve the boundaries”一节仍然有效。

- **先读**：[docs/DESIGN.md](docs/DESIGN.md)（设计规范），[docs/TASK_CARD.md](docs/TASK_CARD.md)（当前任务、实现细节与验收），[eval_set/README.md](eval_set/README.md)（评估集与打分）。
- **边界**：只改 `Memory_lab/` 内的文件。不要 import 旧记忆栈（`Conversation_Memory.adapter`、MAGMA、`vendor/`、`Mind`、`Dream`），不要接入 Chat。可以只读引用任务卡 §3 列出的几个文件。
- **记忆系统与评测分开**：`memlab/` 不依赖 `lab/`。
- **时间**：所有取时间的地方都用注入的时钟或显式的 `now`，禁止调用 `datetime.now()`。
- **写者**：Dream 是记忆图唯一的写者；聊天侧召回不调用模型，只追加召回痕迹。
- **可复现**：模型响应先写缓存再应用；缓存键包含完整提示词。同一输入、同一缓存、同一参数，结果必须逐字节相同。
- **提示词**：`prompts/*_v1.md` 不原地修改；要改就复制成新版本号。
- **评估集**：`eval_set/scripts/`、`gold/` 和 `build.py` 只读；发现问题写进报告。保留集 `holdout_c` 只在仓库主人明确要求时运行（`--allow-holdout`）。
- **一次只验证一个变量**：阶段之间只加一个机制；召回侧阶段用 `--cache-only` 复用 Dream 缓存。
- **测试离线**：`python -m pytest tests -q` 不联网、不需要密钥、不加载真实嵌入模型。
- **凭据与提交**：保护 `.env.local`、`data/` 和密钥；未经明确授权不 commit、不 push。
