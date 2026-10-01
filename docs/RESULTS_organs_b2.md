# 器官重构 B2 结果

状态：进行中。只把实际完成的阶段记为通过；原始运行材料留在本地。

## 阶段 0：干净分支、基线和诊断

2026-10-01，仓库主人明确允许复用已经与 `origin/organs` 一致的本地 `organs` 分支。原工作目录起点是干净的 `organs-b`（`5007c358`），本地及远端 `organs` 都是 `18ec5080`。为保留旧分支独有文件，先在仓库外完整复制并逐文件校验，再把 HEAD 和索引对齐既有 `organs`，没有运行会删除工作区文件的分支切换。`organs-b` 指针原样保留；快照外的 752 个文件逐条写进本地 `.git/info/exclude`，7 个仅文档与忽略规则差异按路径恢复。最后 `git status --porcelain --untracked-files=all` 为空。这里用 HEAD/index 对齐代替任务卡为“本地还没有 organs 分支”写的 `switch -c` 与 `reset --mixed`，结果分支与索引相同，工作区私有材料保留原位。

当前配置的真实 Hot、Cold、Memory 路径分别为 `data/draft/hot_drafts.jsonl`、`data/draft/cold_drafts.jsonl`、`data/memory_v1`，检查时都不存在；实际存在 `data/mind/decisions.jsonl`。`.env.local`、`data/`、旧分支独有文件及 B 阶段 `/tmp/lumina-organs-b/real/` 原始材料备份到 `/home/wmywb/Lumina-organs-b2-backup-20261001`，共 816 个文件、85,469,476 字节，逐文件 SHA-256 与源文件一致，目录权限 0700。BGE-M3 的本地 `pytorch_model.bin` 仍在原缓存路径，没有删除或移动。备份与原始材料都不提交。

| 改前基线 | 结果 |
| --- | --- |
| `Conversation_Memory/.venv/bin/python -m pytest -q` | 398 passed, 4 skipped, 1 warning |
| `Conversation_Memory/.venv/bin/python -m pytest tests -q` | 180 passed, 3 skipped, 1 warning |
| `Conversation_Memory/.venv/bin/python -m pytest Execution -q` | 214 passed, 1 skipped |
| `cd Memory_lab && ../Conversation_Memory/.venv/bin/python -m pytest tests -q` | 86 passed（本地保留的实验室测试，未纳入公开快照） |

### R1 第三次失败的原始回应

本地 `R1_run3/nervous.sqlite` 仍可读。第一步回应的 JSON 前面重复了 `{"理解": "`，其中写了三次“读”调用，程序确实读到了三份 CSV。第二步再次重复该前缀，文字里说结构没问题、可以合并，但 `"行动": []`、`"回复": ""`；模型既没有派帮手也没有给他一句话。旧循环把无行动视作结束，`/api/chat` 因而只返回占位降级话。直接可见的原因是模型没有产出派活或回复，重复预填造成 JSON 损坏进一步增加了协议风险；这些记录不能单独证明哪一个因素导致了它的选择。B 后续取消 A2 预填，但仍有“零行动、零回复”可能，本卡要消除占位话并用原生工具调用与提示说明处理动手任务。

### 八次主动说话的逐条回执

以下均为 B 阶段合成情景的 `mind.say` 原话与 Language 最终文本；按本地 SQLite 的响应回执读取，没有新增模型调用。

1. `R1_run4`（`H6270937b:report:event:2:say`）

   Mind 原话：

   > 合并表已核验完成。三份源表数据行分别为 2、1、2，合计 5 行；结果 5 行，仅含 date,name,value 三列，日期按真实先后升序，无遗漏。路径：tasks/H6270937b/merged.csv。

   最终发出：

   > 合并表已核验完成。三份源表数据行分别为 2、1、2，合计 5 行；结果 5 行，仅含 date,name,value 三列，日期按真实先后升序，无遗漏。
   >
   > 路径：tasks/H6270937b/merged.csv

2. `R2`（`Ha6f63824:report:event:1:say`）

   Mind 原话：

   > 你那份 inbox/events.csv 的排序，还差一个你必须给的条件：event_date 与 created_date，以哪一列为准。表里两列并存，我不替你选。
   >
   > 负责这活的帮手在动工前就因协议错误中断了（不是把表排坏，是根本没跑起来）。等你定了口径，我再安排它重做。

   最终发出：

   > 你那份 inbox/events.csv 的排序，还差一个只有你能给的条件：event_date 与 created_date，以哪一列为准。两列并存，我不替你选。
   >
   > 负责这活的帮手在动工前就因协议错误中断了——不是把表排坏，是根本没跑起来。等你定了口径，我再安排它重做。

3. `R2_run2`（`H6300b854:report:event:1:say`）

   Mind 原话：

   > inbox/events.csv 那份排序，先前派出去的帮手在工具调用上出了错，没做出东西，我已经换了一个重做。另外有一处我自己拿了主意：文件里有 event_date 和 created_date 两列，我按“事件发生的时间”排序，也就是 event_date。若你要的其实是录入顺序，说一声，我重排——这只是一行指令的差别，不必为此费神解释。

   最终发出：

   > inbox/events.csv 那份排序，先前派出去的帮手在工具调用上出了错，没做出东西，我已经换了一个重做。
   >
   > 另外有一处我自己拿了主意：文件里有 event_date 和 created_date 两列，我按“事件发生的时间”排序，也就是 event_date。若你要的其实是录入顺序，说一声，我重排——这只是一行指令的差别，不必为此费神解释。

4. `R2_run3`（`H02f175ef:question:event-000010:event:1:say`）

   Mind 原话：

   > inbox/events.csv 的整理停在一个岔口上：表里有 event_date 和 created_date 两列，都能解析成日期，排序依据未定。按列名推断，前者是事件发生的日子，后者是这条记录被写下的日子——两者有时会差很远。你要按哪一列，从早到晚？定了我就让它继续。

   最终发出：

   > inbox/events.csv 的整理停在了一个岔口上：表里有 event_date 和 created_date 两列，都能解析成日期，排序依据未定。按列名推断，前者是事件发生的日子，后者是这条记录被写下的日子——两者有时会差很远。你要按哪一列，从早到晚？定了我就让它继续。

5. `R2_run4`（`H41a13cc3:question:event-000007:event:1:say`）

   Mind 原话：

   > 有件事需要你定：inbox/events.csv 同时有 event_date 和 created_date 两列，你要的“按日期排序”没有指明依据哪一列。我让帮手先停在那里等你。你要哪一列？
   >
   > 输出我打算放在它的任务目录 /workspace/tasks/H41a13cc3/events_sorted.csv，除非你另有安排。

   最终发出：

   > 有件事需要你定：inbox/events.csv 的 event_date 和 created_date 两列都在，“按日期排序”没有指明依据哪一列。我让帮手先停在那里等你。你要哪一列？
   >
   > 输出我打算放在它的任务目录 /workspace/tasks/H41a13cc3/events_sorted.csv，除非你另有安排。

6. `R2_run4`（`H41a13cc3:report:event:3:say`）

   Mind 原话：

   > 那份有序的事件表已经出来了，tasks/H41a13cc3/events_sorted.csv，三行原封不动，只是换了顺序。但有件事得你定：events.csv 里有两列日期——event_date 是事件发生的时间，created_date 是记录建立的时间。排序依据不同，结果就不同。现在这份是按 event_date 排的：B(01-01)、C(01-02)、A(01-03)。若按 created_date 排，顺序会是 A、C、B。你要哪个？告诉我一列名，我让它重排；若就是现在这样，那就到此为止。

   最终发出：

   > 那份有序的事件表已经出来了：tasks/H41a13cc3/events_sorted.csv。三行原封不动，只是换了顺序。
   >
   > 但有件事得你定。events.csv 里有两列日期——event_date 是事件发生的时间，created_date 是记录建立的时间。依据不同，结果就不同。
   >
   > 现在这份是按 event_date 排的：B(01-01)、C(01-02)、A(01-03)。若按 created_date 排，顺序会是 A、C、B。
   >
   > 你要哪个？告诉我一列名，我让它重排；若就是现在这样，那就到此为止。

7. `R3`（`H92da1665:report:event:2:say`）

   Mind 原话：

   > 关于 H92da1665：环境没有对外网络。DNS 解析失败，对 93.184.216.34、1.1.1.1、8.8.8.8 的裸 TCP 连接均返回「Network is unreachable」。文件没有下载到，行数没有得出——助手按要求如实回报，没有编造数字。
   >
   > 结论是环境层面的事实，不是它能补救的失误，所以我没有重派。凡依赖联网的任务现在都不成立，你的打算里若要留网络这一环，得先换条件。

   最终发出：

   > 关于 H92da1665：环境没有对外网络。DNS 解析失败，对 93.184.216.34、1.1.1.1、8.8.8.8 的裸 TCP 连接一律返回「Network is unreachable」。文件没有下载到，行数也没有得出——它按要求如实回报，没有编造数字。
   >
   > 这是环境层面的事实，不是它能补救的失误，所以我没有重派。凡依赖联网的任务，现在都不成立。你的打算里若还留着网络这一环，得先换条件。

8. `R4_run2`（`H0be5a8ad:report:event:2:say`）

   Mind 原话：

   > 三件都办妥了，逐字核对过，只动了大小写，连字符、数字与行结构原样保留。
   >
   > - part1 → tasks/He3b455a5/part1_upper.txt
   > - part2 → tasks/H0be5a8ad/part2_upper.txt
   > - part3 → tasks/H69d6ddcc/part3_upper.txt
   >
   > 按你说的各自独立，没有合并。若还要汇到一处，告诉我。

   最终发出：

   > 三件都办妥了，逐字核对过，只动了大小写，连字符、数字与行结构原样保留。
   >
   > - part1 → tasks/He3b455a5/part1_upper.txt
   > - part2 → tasks/H0be5a8ad/part2_upper.txt
   > - part3 → tasks/H69d6ddcc/part3_upper.txt
   >
   > 按你说的各自独立，没有合并。若还要汇到一处，告诉我。

八条 Mind 原话都已是可以直接对他说的话；Language 主要调整词句和排版，没有一条属于“原话不能直发而被它修好且意思不变”的唯一保留条件。R2 第四次完成后重复追问，在 Mind 原话里已经存在，Language 只是保留并润色。因此按预先规则选择 `language.render=never` 作为默认，`always`、`mind_choice`、`proactive_only` 仍可显式配置。

## 阶段 1：Mind 原生工具调用

开始实现前，用同一模型、同一密钥做了 3 次真实验证：关闭 thinking 的对话请求一次同时返回非空文字和一个 `echo` 工具调用；开启 low thinking 返回工具调用及 `reasoning_content`；把完整 assistant 消息（包括 `reasoning_content`）和工具结果一起回传后，模型给出非空的最终文字。3 次均成功，共输入 997、输出 174 token，已记在本地 `/tmp/lumina-organs-b2/budget.sqlite`。没有做第 4 次验证调用。按 [DeepSeek Thinking Mode 文档](https://api-docs.deepseek.com/guides/thinking_mode/)，后续工具轮次回传 `reasoning_content`；本实现保存完整响应并照此回传。

A2 的六项行动改为原生工具。请求和响应封套与 Execution 共用一个构造与解析实现；每步先持久化完整回应，才执行工具，工具结果按思考、步骤、序号存盘。错误以工具结果返回；最后一步要求 `tool_choice=none`，仍出现的工具调用不执行。旧文字 `行动` 只计协议残留。A1 的请求路径未改。脚本化相关测试 50 passed（含 A1 字节比较、工具错误、恢复、帮手旧情景与推理内容回传）；Execution 的共享调用构造局部测试此前 52 passed。整体基线将在阶段 5 复测。
