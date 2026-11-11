# P7 开发批次 4 收尾记录（2026-11-12）

> **状态：记录，不是验收。** 本文记下批次 4 交付了什么、没交付什么、评审与修复各走了几轮、哪些裁决改了计划、哪些事留到以后、哪些事要用户来定，以及真正对真实数据下手之前要具备的前提。它只写工程状态；**工程完成不是研究证据**（`AGENTS.md`「研究诚实」），措辞仍是“候选”，没有任何一行把“通过的测试”写成别的东西，也不写任何闸门的通过数字与任何测试通过数。

## 0. 先读这几句

- 批次 4 做的是 `V2-P7-023`（按重算等价延续确认性样本，`006a` 的一项）与批次 3 延后的几个小缺口：`V2-P7-006a` 的四个小项（`--show-book` 与 `--worktree` 的互斥、`--factor-gaps` 的冒烟横幅、纸面账簿快照与预测库的单一组合根、卖出对账行按账本单位打印）与 `V2-P7-024` 的一个书面裁决（`read_once` 不绑定两个每日脚本）。本批次的实施计划是任务书（**不在版本库内**：`.superpowers/sdd/task-brief.md`；`.superpowers/` 不入库），本文是它的版本化记录。
- 代码在分支 `feat/v2-p7-batch-4` 上（从 `main` 的 `8b00c4c` 分出）。**本文写到的 tip 是本任务提交之前的 HEAD `c59ed3c`，共 16 个提交**（`git log --format='%h %s' 8b00c4c..c59ed3c`）。**文档提交本身、以及其后的最终修复波之后的 tip，以控制者的报告为准**；`c59ed3c` 是代码末端的截面。
- **未推送；交付的代码没有跑过任何真实构建、因子、读数或冒烟，一次 Tushare 请求也没有发过**：所有测试都跑在脚本化行情与测试时生成的夹具上。那唯一一次验证读（2022–2023，一次、只读、0 次网络请求）与真实冒烟**没有做**，仍等用户明确说“开始”；**真钱闸门仍关着**。**没有任何 P7 的登记存在**（`docs/research/` 里只有已作废的 P6 旧登记 `p6-registration.json`、`p6-registration-r2.json`；本批次没有写任何新登记，`docs/research/p7-readings/` 不存在），所以**没有绑定被作废**；也没有任何一份登记用到新的 `survivor_continuation` 块——`006b` 的登记尚未发生。
- 规模（`git diff --shortstat 8b00c4c c59ed3c`）：**18 个文件、+3,241 / −54**。其中 `src/openalpha_cn/` 5 个文件（新增 `strategy_survivor_continuation.py`）、`scripts/` 2 个（`daily_selection.py`、`forward_report.py`）、`scripts/research/` 2 个（`p7_reading.py`、`registry.py`）、测试 8 个文件（新增 `tests/unit/test_strategy_survivor_continuation.py`）、文档 1 个（`docs/runbooks/p7-reading.zh-CN.md`）。
- **受登记绑定的路径**（`scripts/research/registry.REGISTERED_PATHS` = `src`、`scripts/research`、`pyproject.toml`、`uv.lock`；`scripts/daily_selection.py` 与 `scripts/forward_report.py` 由 `also_bound` 加绑）里改了字节的：`src/`（5 个文件）、`scripts/research/`（2 个）、`scripts/daily_selection.py`、`scripts/forward_report.py`；**`pyproject.toml` 与 `uv.lock` 没有变**（`git diff --name-only 8b00c4c c59ed3c -- pyproject.toml uv.lock` 为空）。仍然没有登记，所以没有绑定被作废。
- 本文所在的是一个只改 Markdown 的文档波（本记录、路线图、规格 §9、`CHANGELOG`、交接入口、`AGENTS.md`、运行手册的周报与重跑两节）；**没有一处行为改动**，也没有碰 `src/`、`scripts/`、`tests/`。`docs/runbooks/p7-reading.zh-CN.md` 在实现任务里改过（`read_once` 的裁决），不在本波里重复改。
- 各任务的“通过”数字是实现者与评审者各自最后一次**定向**运行的数字，本轮文档没有重跑。整个 `tests/unit` 由控制者在最终 tip 上串行跑一次；**结果以控制者的报告为准**，本文不写它通过。
- 账本、任务书与代码不一致的地方，以代码为准，见 §7。

## 1. 交付了什么（逐个 issue）

### `V2-P7-023` 按重算等价延续确认性样本——代码已交付、本行未关闭

- 提交：契约块 `30e5441`；逐日判定与截断 `f12657a`；报告携带并打印 `continuation` 段 `835452f`；每日命令第 1 步读整条链 `cf0f8cf`；评审修复轮 `155c870`、`805b0e5`、`79c6611`、`585ef00`、`2cffab2`；周报见证并合并 `fa552ff`；G4 修复轮 `c59ed3c`。
- **契约块（登记文件 `settings` 下的追加块）。** `settings["survivor_continuation"]`（`SURVIVOR_CONTINUATION_KEY`，`src/openalpha_cn/strategy_survivor_continuation.py:157`）是一个 `survivor-continuation/v1` 对象（`SURVIVOR_CONTINUATION_SCHEMA`，`:158`）：`supersedes_path`（仓库内相对的 POSIX `.json` 路径）、`supersedes_sha256`、`supersedes_config_id`，`extra="forbid"`、`frozen`、**没有默认值**（模型 `:207-231`；路径校验 `:218-231`；为 `006b` 的登记步骤准备的构造函数 `continuation_block` `:234-245`）。**块缺失 = 没有前任，不是错误**，返回 `None`；块存在但读不出，逐字段按名拒绝、从不补默认、从不忽略（`read_survivor_continuation` `:248-273`）。**不是 `config`，不需要迁移**：登记文件不是存储行，它的顶层只由 `registry._committed_registration` 检查 `schema`，`settings` 是自由 JSON；与 `survivor_forward_power` / `survivor_forward_stop_rule` 两个预登记块同一先例；`PredictionRecord`、快照、账本行、来源文件与裁决缓存的形状一律不改（模块 docstring「The contract」；计划 R1）。
- **链。** 每日命令第 1 步 `admit_predecessors`（`scripts/daily_selection.py:735-848`，在取数之前调用 `:3573`）从继任登记往上读，**根在最前**：每一条经组合根的只读开门 `registry.committed_registration`（`scripts/research/registry.py:273`）从 `HEAD` 读出，磁盘字节的 sha256 必须等于块里写的 `supersedes_sha256`；`config_id` 重新推导、必须同时等于登记体自己的与块里的；按 `admit_registration` 的种子规则读成这一构建能读的幸存者登记，再过 `require_continuable`。出现环（同一 sha256 第二次）、超过深度上限 `MAX_CONTINUATION_DEPTH = 16`（`:159`）、前任读不出、字节不符、`config_id` 不符、不是幸存者登记——每一种都按名拒绝，`StepFailedError("registration", …)`、退出码 `no_registration`、在任何请求与写入之前（`_predecessor_refused` `:727-732`）；周报准入时用同一个函数再读一遍（`scripts/forward_report.py:477-487`）。
- **什么能延续进什么。** `require_continuable`（`strategy_survivor_continuation.py:325-410`）按固定顺序拒绝、点名两份登记的 sha256 与出问题的字段：前任是继任自己；`adapter.exchange` 不同；`adapter.prediction_horizon` 不同；`adapter.benchmark` 不同；三个预登记协议块（`survivor_forward_protocol`、`survivor_forward_power`、`survivor_forward_stop_rule`）的 JSON 文本不相等（两边都缺算相等，一边缺是不同）；前任的 `epoch_session` 不早于继任的；冒烟前任并入非冒烟继任（`SMOKE_SETTINGS_KEY`，`:160`，两位拼写由 `tests/unit/scripts/test_daily_selection_dispatch.py::test_the_smoke_key_the_continuation_module_spells_is_the_daily_commands` 钉住）。
- **逐日判定与截断。** `continuation_bytes`（`:419-430`）是两份批次逐字节比较的口径：`PredictionBatch.model_dump(mode="json", exclude_computed_fields=True)`，声明只保留 `name`、`family`、`horizon` 三个**打分定义**字段；`feature_version`（`config_id`）、`seed`、`code_commit`、`hyperparameters` 是登记身份，不参与比较。`judge_continued_day`（`:502-572`）拿前任一个记录日、用**继任配置**从**已存面板**重算（`recompute_survivor_batch`，`provenance=None`，`held` 取自前任自己那次运行写下的溯源；零请求）：前任自己的核查拒绝了它 → `predecessor_refused`，不重算；落在 `SURVIVOR_RECOMPUTE_REFUSALS` → `recompute_failed`；字节相同 → `identical`；其余 → `differs`（原因里写有几个名字的分数不同、范围是否不同）。`continued_sample`（`:634-680`）按会话排序、最老的先问，**第一个非 `identical` 的日子被标记，样本在那里截断，之后的每一天都是 `after_truncation`、永不再问、永不重算**；一天两条前任记录、或记录落在样本纪元当日或之前，按名拒绝。等价判定**不缓存**（计划 U7）：每次周报都重算，面板后来被更正只可能把截断点往前移（测试 `tests/unit/test_strategy_survivor_continuation.py::test_the_first_day_that_is_not_identical_truncates_and_nothing_after_it_is_asked`；报告侧 `tests/unit/scripts/test_forward_report_survivor.py::test_a_session_scored_otherwise_truncates_the_sample_and_no_later_one_is_scored_again` 数重算次数）。
- **周报。** `--predecessor-runtime-dir PATH`（`scripts/forward_report.py:1190-1200`，可重复、每个前任给一次）把前任的运行目录交给报告；目录按前任纸面账簿的写一次标记（`strategy_survivor_book.PAPER_BOOK_MARKER = "registration.json"`，`src/openalpha_cn/strategy_survivor_book.py:212`；`expected_binding`，`:425-436`）与链上每个前任**逐一配对**：链上有前任配不到目录、给了目录却配不到任何前任、目录的标记读不出、一个前任配到两个目录、目录就是继任自己的运行目录——都按名拒绝（`_paired_directories` `:489-549`）。前任目录**只读**：预测库经组合根的 `prediction_store_at` 打开（不跑迁移、不建目录），前任自己的迟到记录核查不带裁决缓存（`verdicts=None`），不往前任目录写任何东西（`_continued` `:807-879`；测试 `tests/unit/scripts/test_forward_report_survivor.py::test_the_predecessors_directory_is_read_only`）。每个前任只贡献 `(它自己的纪元, 下一环的纪元]` 里的记录；样本序号从**链根**纪元起数。并入的旧记录保留**它自己核查的结论**（`UNVERIFIABLE` 照旧计入头条、在敏感性读数里剔除）。周报文档多一个**总是存在**的 `continuation` 键（没有前任时是“没有前任”的说明段；`no_continuation_section` `:683-699`，交接在 `scripts/forward_report.py:766`），打印在记录之后（`src/openalpha_cn/strategy_survivor_forward.py:2691-2722`；测试 `tests/unit/test_strategy_survivor_forward.py::test_the_document_carries_the_continuation_section_and_prints_it_after_the_records`）。整条路径零请求（`tests/unit/scripts/test_forward_report_survivor.py::test_the_continued_report_never_constructs_a_request_channel`）。
- **纸面账簿仍“换登记就换账”，但不再决定样本是否延续。** 相对收益的窗口只取继任自己记录的抽样期（`_paper_section`，`scripts/forward_report.py:956`；窗口规则在 `:978`），并入的旧记录早于继任账簿开账、拿它们的窗口去复利继任账簿没有意义；没有前任时报告逐位与今天相同。一天在并入记录与自有记录上各有一条时，报告按名拒绝，不在两者之间挑（同测试 `test_a_session_carried_by_both_a_merged_and_an_own_record_refuses_the_report`）。
- **未做 / 未关闭**：`006b` 的登记没有发生，没有任何登记用到这个块；没有对真实数据运行；本行保持打开。**前提前提**：继任登记点名的前任文件必须**已提交**（`registry.committed_registration` 从 `HEAD` 读；未提交按名拒绝），继任要有**新的运行目录与新的纪元**，周报要给每个前任一个 `--predecessor-runtime-dir`（运行手册与 §6）。
- **评审裁决 M1、M2、M4：** M1——前任运行目录丢了、搬了、标记读不出时，周报**按名拒绝，永远不会静默降级成“没有前任”**；出路只有找回目录（只读挂上）或另起一份**不点名前任**的新登记、从自己的纪元开始干净的新样本（运行手册已写，见 §3）。M2——截断之后的前任日**仍跑前任自己的核查**（保守）：候选的 `own` 是构造候选时对**每一个**前任记录做的（`scripts/forward_report.py:862-872`），`continued_sample` 只是不再问判定、不再重算；测试钉住的是重算次数不再增加，核查次数没有被计数钉住（§7 如实记）。M4——继任使用的面板（与它的回看窗口）必须从**链根纪元**往前覆盖足够的历史，否则重算会被拒绝、样本在那一天截断；写进运行手册。

### `V2-P7-006a` 的四个小项——代码已交付、本行未关闭

- 提交：`--show-book` 与 `--worktree` 互斥、`--factor-gaps` 的冒烟横幅 `8f69d80`；卖出对账行按账本单位打印 `37a235e`；组合根的两个只读开门（脚本里的旧快照路径字面量随之删除）`b1fe454`。
- **`--show-book` 与 `--worktree` 同用不再是静默忽略**：互斥表里加了 `--worktree`（`scripts/daily_selection.py:6842-6853`），组合时退出 2；参数化测试 `tests/unit/scripts/test_daily_selection_book.py::test_show_book_refuses_the_modes_it_cannot_be_combined_with` 的案例里现在有 `worktree`。
- **`--factor-gaps` 在冒烟登记下印横幅**：分支在返回之前打印一次、在 `finally` 里再打印一次，文本与 JSON 都带（`with_smoke` / `bannered`，`scripts/daily_selection.py:6892-6911`）；测试 `tests/unit/scripts/test_daily_selection_book.py::test_factor_gaps_carries_the_smoke_banner_of_a_smoke_registration` 与 `::test_a_factor_gaps_refusal_under_a_smoke_registration_is_bannered`。批次 3 收尾 §4 记的这条缺口关闭。
- **单一组合根（硬规则 5）**：`runtime/composition.py` 新增 `PAPER_SNAPSHOT_DIRECTORY`（`:248`）、`paper_state_store_at`（`:255`）、`prediction_store_at`（`:263`），两个函数都**不建目录、不跑迁移**；`build_storage` 自己也经它们打开同一路径（`:313`、`:315`）。`scripts/daily_selection.py` 的旧快照路径字面量**删除**，`show_book` 改用 `paper_state_store_at`（`scripts/daily_selection.py:3304`、`store = paper_state_store_at(runtime_dir)` `:3322`）；`--show-book` 的“不加载 `build_storage`、零写入”保持。测试：`tests/unit/backtest/test_paper_state.py::test_the_paper_snapshot_store_opened_alone_is_the_composition_roots_and_creates_nothing`、`::test_the_prediction_store_opened_alone_is_the_composition_roots_and_creates_nothing`，`tests/unit/scripts/test_daily_selection_book.py::test_show_book_reads_the_snapshot_directory_the_composition_root_names`，另有 `tests/unit/scripts/test_daily_selection_book.py:620` 的一条测试断言该属性已不存在。**批次 3 收尾 §4 的 M5 关闭。**
- **卖单的账簿一侧按账本单位打印**：`Divergence.line()` 对 `side == "sell"` 在账簿数字后加 ` (ledger units)`（`src/openalpha_cn/backtest/paper_reconciliation.py:194-202`）；`Divergence.document()` 不变（它被写进日志 JSON，改它就是改已写文档的形状）；测试 `tests/unit/backtest/test_paper_reconciliation.py::test_a_diverging_sales_book_figures_are_printed_in_ledger_units`。
- **未做 / 未关闭**：没有对真实账户、真实行情运行过；真钱闸门仍关着；本行保持打开（`006a` 的其余项与 `006b` 的登记仍未发生）。

### `V2-P7-024` 的书面裁决：`read_once` 不绑定两个每日脚本——代码已交付、本行未关闭

- 提交：`f161eea`（守住前提的静态测试）、`d2798fa`（把运行手册与 `p7_reading.py` 的 docstring 从“已知代码缺口”改成“裁决”）。
- **裁决**：一次读数的认领绑定的是**算这次读数的代码**。读数工具（`scripts/research/p7_read.py` → `p7_reading.py` → `p7_diagnostics.py` / `p7_windows.py` → `src/`）不导入、不执行 `scripts/daily_selection.py` 与 `scripts/forward_report.py` 的任何一行，所以每日脚本之后的改动不可能改变已经读出的数，**不应作废**一次读数的认领；把它们绑进来只会让每一次与读数无关的运维修复都“作废”那唯一一次读。读数与之后的每日登记之间的联系是“同一个 `config_id` / 同一个打分定义”，由 `006b` 的登记测试负责，不由读数负责（原文见计划 R10）。
- **守住前提**：`tests/unit/scripts/test_p7_reading.py::test_no_research_script_imports_a_daily_script` 用 AST 扫 `scripts/research/*.py` 的 import 语句、`from` 与字符串常量，两个脚本名一次都不许出现；将来若有人让读数工具调用它们，测试变红，届时必须改成绑定。
- **文档同步**：`docs/runbooks/p7-reading.zh-CN.md:32`（第 3 条前提）与 `:172`（“登记之后受绑定代码变了”那一行）现在写“这是裁决，不是缺口”；`scripts/research/p7_reading.py` 模块 docstring 的 `SourceChangedError` 一条同样改写，并点名那条测试。
- **未做 / 未关闭**：读数本身没有做，没有克隆面板、没有登记、没有认领；本行保持打开。

## 2. 评审与修复轮次（每个任务一行）

本批次的任务计划与评审由控制者组织；任务评审者的型号写在括号里（opus 或 sonnet）。实现者是 DeepSeek Harness（2026-11-10 起的指令）。

- G1（Task 1–4）：提交 `8f69d80`..`d2798fa`；Opus 评审：规格 ✅、质量通过，无 Critical/Important；接受两处偏离（按 `order_id` 匹配；真实变异探针）；停放 P1、P2 到最终修复波。
- G2（Task 5–6）：`30e5441`、`f12657a`、`155c870`；Opus 评审要求修改（I1–I3、M1–M6）；修复轮 1 `805b0e5`、`79c6611`、`585ef00`；Opus 复审：全部解决。
- G3（Task 7–8）：`835452f`、`cf0f8cf`；Opus 评审要求修改（I1 环/深度未测、I2 拒绝里路径错、M1–M6；M4 裁定为过渡态、由 G4 关闭）；修复轮 1 `2cffab2`；Sonnet 复审：已解决；停放 P3、P4 到最终修复波。
- G4（Task 9）：`fa552ff`；Opus 评审要求修改（I1 窗口、I2 四种拒绝、I3 自有+并入记录、M3 零请求、M5 一天一条）；M1、M2 维持现状（理由见运行手册与 §3），M4 写进运行手册；修复轮 1 `c59ed3c`。
- 最终全分支评审与修复波：待最终评审（由控制者在之后补写）。

## 3. 覆盖了计划的裁决（R1–R12 原样搬入，加实现中新产生的）

这些裁决由控制者在当时做出；若用户不同意，改动的范围在括号里。前 12 条是本批次计划 §0b 的工程裁决原文，只有其中的 `file:line` 按当前 tip 校正（计划写它们时读的是 `8b00c4c` 与当时的行号）：R1 的 `registry.py:263-264` 与 `strategy_survivor.py:117`/`:298-307`、R2 的 `daily-selection.zh-CN.md:609`/`:668`、R10 的 `p7-reading.zh-CN.md:32`/`:172`；其余文字未改。

- **R1 契约位置与迁移。** 前任引用写在登记的 `settings["survivor_continuation"]`，`SurvivorContinuation`（`schema_version: Literal["survivor-continuation/v1"]`、`supersedes_path`、`supersedes_sha256`、`supersedes_config_id`），`extra="forbid"`、`frozen`、**没有默认值**；块缺失读作 `None`（没有前任），块存在但读不出按名拒绝。**不需要迁移，也不是硬规则 3 意义上的破坏性改动**，理由：(1) 登记文件不是存储行：它的顶层只由 `registry._committed_registration` 检查 `schema`（`scripts/research/registry.py:263-264`），`settings` 是自由 JSON，`survivor_forward_power` 与 `survivor_forward_stop_rule` 两个块就是这样加进来的（`src/openalpha_cn/strategy_survivor_forward.py` 模块 docstring 的 Power 与 stop rule 两节）；(2) 幸存者的 `config` 顶层是封闭的三个键（`src/openalpha_cn/strategy_survivor.py:117`、`:298-307`），把前任写进 `config` 会被今天的读者拒绝、而且会让 `config_id`（打分定义的身份）随一个与打分无关的引用而变，所以**不放进 `config`**；(3) 旧代码读新登记会静默忽略这个块——但旧代码根本跑不了新登记：准入要求运行的受绑定代码就是登记的 `code_commit`（`registry.admit_registered_code`），而那个 commit 含有本批次的读者；即便发生，忽略的后果是"不延续"（更保守），不是误并；(4) 新代码读旧登记：块缺失 → `None` → 行为逐位不变。`PredictionRecord`、快照、账本行、来源文件、裁决缓存文件一律不改。
- **R2 前任的运行目录。** 一份新登记要新的运行目录与新的纪元（运行手册 `docs/runbooks/daily-selection.zh-CN.md:609`、`:668`），所以前任的记录与来源文件在别的目录里。周报加可重复的 `--predecessor-runtime-dir PATH`，**按目录里纸面账簿的绑定标记**（`RT/paper/registration.json`，`strategy_survivor_book._binding` 的五个键）与链上每个前任逐一配对：链上有前任配不到目录、给了目录却配不到任何前任、目录的标记读不出、目录就是继任自己的运行目录——都按名拒绝。前任目录**只读**：预测库经组合根的新函数 `prediction_store_at` 打开（不跑迁移、不建目录），迟到记录核查不带裁决缓存（`verdicts=None`，不往前任目录写裁决文件）。
- **R3 链。** 前任自己也可以点名前任；读到链根为止，深度上限 `MAX_CONTINUATION_DEPTH = 16`，出现环（同一个 sha256 第二次）按名拒绝。每个前任只贡献 `(它自己的纪元, 下一环的纪元]` 里的记录。
- **R4 在两处检查链。** 每日命令第 1 步（`admit_predecessors`，任何请求之前，退出码 2），使一份前任读不出的继任登记从第一天起就跑不了；周报准入时再查一遍（同一个函数）。
- **R5 不缓存等价判定**（见 U7）。代价：每次周报每个前任记录日一次重算（设计备忘录估计 2026 年规模约 2.1 s/天）+ 一次前任自己的核查（不带缓存）；写进运行手册。
- **R6 周报文档加一段 `continuation`。** `survivor_forward_report/v1` 文档多一个键（总是存在：没有前任时是"没有前任"的说明段）。这是追加键：报告文件整份写出、没有任何读者用 `extra="forbid"` 读它（`git grep survivor_forward_report/v1` 只有写方与测试），`kind` 不变。
- **R7 纸面账簿的相对收益窗口只取继任自己的记录。** 并入的旧记录早于继任账簿开账，拿它们的窗口去复利继任账簿没有意义（会被 `paper_record` 拒绝）；`_paper_section` 只用继任自己记录的抽样期。没有前任时与今天逐位相同。
- **R8 不核对前任的日志。** 预测库是见证（既有规则）；前任的日志只属于前任的运行，不读。
- **R9 M5 的单一来源。** `runtime/composition.py` 新增 `PAPER_SNAPSHOT_DIRECTORY`、`paper_state_store_at(runtime_dir)`、`prediction_store_at(runtime_dir, *, clock)`；`build_storage` 自己也用它们；`scripts/daily_selection.py` 删除 `PAPER_STATE_DIRECTORY`，`show_book` 改用 `paper_state_store_at`。两个函数都不建目录、不跑迁移（`show_book` 的"不加载 `build_storage`、零写入"保持）。
- **R10 `read_once` 不传 `also_bound`（裁决，不是缺口）。** 一次读数的认领绑定的是**算这次读数的代码**：读数工具（`scripts/research/p7_read.py` → `p7_reading.py` → `p7_diagnostics.py` / `p7_windows.py` → `src/`）不导入、不执行 `scripts/daily_selection.py` 与 `scripts/forward_report.py` 的任何一行（规划者在 `8b00c4c` 上 `grep` 过：`scripts/research/` 里只有 `p7_windows.py:105` 一处 docstring 提到 `daily_selection`）。所以每日脚本之后的改动不可能改变已经读出的数，**不应作废**一次读数的认领；把它们绑进来只会让每一次与读数无关的运维修复都"作废"那唯一一次读。读数与之后的每日登记之间的联系是"同一个 `config_id` / 同一个打分定义"，那由 `006b` 的登记测试负责，不由读数负责。用一条静态测试守住前提：`scripts/research/*.py` 里不得出现对这两个脚本的导入（若将来有人让读数工具调用它们，测试变红，届时必须改成绑定）。运行手册 `:32`、`:172` 从"已知代码缺口"改成这条裁决。
- **R11 `Divergence.line()` 只改打印文本。** 卖单的账簿一侧印成 `book filled N @ P (ledger units)`；`Divergence.document()` 不变（它被写进日志 JSON；改它就是改已写文档的形状）。
- **R12 前任读者用本构建的 `read_survivor_registration`。** 前任若由更早的构建写成、其账簿块本构建读不出，就按名拒绝（"不能延续"），而不是放宽读者；真遇到时那是一次迁移任务。

实现中新产生的裁决：

- **`registry.committed_registration` 公开化（G3）。** 链的每一环都要“从 `HEAD` 读出登记、按写下的 sha256 对上”，这正是 `registry` 原有的只读准入逻辑；抽成公开函数（`scripts/research/registry.py:273`）而不是在脚本里复制一份。代价：`registry` 多一个公开面（外观，不是行为）。
- **`strategy_survivor_book.expected_binding` 公开化（G4）。** 周报要把“某个运行目录的纸面账簿标记”与链上每一环配对，标记的构造必须与 `bind_paper_book` 写出的**逐字节相同**；抽成公开函数（`src/openalpha_cn/strategy_survivor_book.py:425`），两处用同一份构造。代价：`strategy_survivor_book` 多一个公开面。
- **周报一天两条前任记录按日期拒绝（G4 M5）。** 并入的前任记录与继任自己的记录落在同一会话时，报告按名拒绝、不挑一条（`scripts/forward_report.py:695-707`）。理由：样本里一个会话只能有一条记录，挑一条就是替用户选样本。
- **前任目录缺失永久拒绝（M1）。** 继任登记点名前任、周报拿不到前任的运行目录时，拒绝是**永久**的，只有找回目录或另起一份不点名前任的新登记两条出路；不提供“没有目录就当没前任”的降级。理由：静默降级会让人看了结果再决定样本要不要延续。
- **截断后的前任日仍跑前任核查（M2，保守）。** 候选的 `own` 在构造候选时就对每一个前任记录做了，截断只停判定与重算；这样“前任自己拒绝了某天”这种截断原因在任何一天都可知，而不是要重跑才知道。代价：截断点之后的每一天仍付一次核查的代价。

## 4. 延后的事项（带来源）

以下都是计划或评审提出、控制者裁定“这一批不做”的。

- **等价判定的缓存 / 冻结（U7）。** 今天每次周报都重算（计划 R5）：面板后来被更正，较早判“相同”的一天可能在之后的周报里变成“不同”，截断点只会前移、而且会被打印出来。冻结第一次的判定需要一个新的、只写一次的证据文件，等需要时另立任务。
- **清晨复核不读前任链。** `--morning-recheck` 不读 `survivor_continuation`，也不重算前任记录；它复核的是当晚的计划，与确认性样本无关。若将来要让晨查也见证延续，那是另一条读取路径与另一组请求预算。
- **`006b` 的登记测试要检查 U1 的“必须点名前任”。** 代码无法知道“本该有前任”（块缺失就是新样本）；把“`006b` 之后的每一份幸存者登记必须点名它所取代的那一份”变成**登记测试**，是 `006b` 的事，本批次只把它写进运行手册的硬步骤。
- **批次 3 §4 里本批次仍未做的**（来源：`docs/superpowers/plans/2026-11-11-p7-dev-batch-3-closeout.md` §4）：账户同步（让账簿跟随账户，`006b` 之后的设计）、launchd 与 `--factor-gaps` / `--pin-worktree` 的优先序、G1 的每个 `--year` 重抓整份登记簿的请求浪费、C2 的较早补跑条目带对账的钉子、C4 停放的 Minor 3/4、C6 停放的 Minor 2/3、B3 的资金行把 `1.10x` 写死在排版文本里而不是读 `FREEZE_RATIO`。
- **M2 的核查次数没有测试钉住。** 截断后仍跑前任核查由代码的构造顺序保证，测试只钉了重算次数不增加；要不要为此加一条计数测试，留给之后的评审决定（§7）。
- **账本里的 P1–P4、P3/P4（评审停放项）**：由控制者在最终修复波里处置，本文不重复（§2）。

## 5. 要用户来定的事（本计划 §0a 的 U1–U8 原样；代码按建议实现、值为暂定）

代码按每条的建议实现；推翻任何一条都只是改一个常量或一处检查 + 重跑定向测试，**但必须在 `006b` 登记之前定**，因为它们决定确认性样本里有哪些天。

- **U1 延续是“可选”还是“强制”。** 代码层面：登记里有 `survivor_continuation` 块才延续，没有就是新样本（代码无法知道“本该有前任”）。**建议**：在协议里规定——`006b` 之后的每一份幸存者登记**必须**点名它所取代的那一份（运行手册写成硬性步骤，`006b` 的登记测试检查）；否则“看了旧样本的结果再决定要不要换个干净样本”就成了一个可以按结果挑的自由度。
- **U2 什么算“逐字节相同”。** **建议**（代码按此）：比较批次的 `as_of`、`predicted_at`、`artifact.feature_ids`、`artifact.parameters`（权重）、`artifact.training_cutoff`、`artifact.training_example_count`、每只证券的 `(ts_code, score)`，以及声明里的 `name`、`family`、`horizon`；**不比较**声明里因登记而必然不同的 `feature_version`（`config_id`）、`seed`、`code_commit`、`hyperparameters`（登记摘要与账簿 `config_id`）。也就是说“同样的打分定义在同样的数据上给出同样的分数与范围”才算相同。
- **U3 协议在整条链上必须完全一致。** **建议**（代码按此）：前任与继任的 `survivor_forward_protocol`、`survivor_forward_power`、`survivor_forward_stop_rule` 三个块按 JSON 文本逐一相等（都缺也算相等），否则按名拒绝；延续时样本的序号从**链根**的纪元起数（`anchor_ordinal`、窗口 K、中期查看都按链根算），所以继任的协议块里的 `anchor_ordinal` 是相对链根纪元的。
- **U4 前任自己的核查结论怎么处理。** **建议**（代码按此）：前任的记录先用**前任自己的配置**跑迟到记录核查（与周报对任何记录做的一样）；`refused` 的那天直接作为截断点（标 `predecessor_refused`），不重算；`verified` 与 `UNVERIFIABLE` 的都进入等价比较，并入后**保留它自己的核查结论**（`UNVERIFIABLE` 照旧计入头条、在敏感性读数里剔除，P6 惯例）。
- **U5 截断之后到继任第一条记录之间的交易日。** **建议**：按“样本是日历的性质”既有规则，它们是窗口里的**缺口**（`no_record`），减少检验的期数 n、不移动窗口；不补、不重排。
- **U6 冒烟登记永远不并入非冒烟登记。** **建议**（代码按此）：前任带 `settings.smoke_run` 而继任不带，按名拒绝——冒烟输出不是证据。
- **U7 每次周报都重算，还是第一次判定后冻结。** **建议**（代码按此）：每次周报都重算（与迟到记录核查同一立场：面板后来被更正，较早判“相同”的一天可能在之后的周报里变成“不同”，截断点只会前移、而且会被打印出来）；不做缓存。冻结第一次的判定需要一个新的只写一次的证据文件，等需要时另立任务。
- **U8（确认）`read_once` 不绑定两个每日脚本。** 工程裁决见 R10；因为它关系到“什么会作废一次读数的认领”，请用户确认。**建议**：确认裁决（不绑定，但用测试守住“读数工具不导入它们”）。这条现在按裁决实现，运行手册与 `p7_reading.py` 的 docstring 按裁决写（§1 的 `V2-P7-024`）；**用户确认与否不改变代码，只改变今后要不要再开一次绑定**。

## 6. 运行前提

**真实的验证读（`V2-P7-024`）之前**：与批次 3 收尾 §6 相同（仓库不浅、无 grafts、登记的 `code_commit` 与 `HEAD` 在四个受绑定路径上无差异、面板是 `cp -c -R` 的只读克隆、登记已提交、`--as-of` 与 `--as-of-reason`、`--exchange SSE`、期望 24 期、先读验证窗口再 `second_use`、解释器环境）；两个 `also_bound` 脚本**不由这次读数检查**（R10 的裁决），读数工具不导入它们的前提由 `tests/unit/scripts/test_p7_reading.py::test_no_research_script_imports_a_daily_script` 守住。

**真实冒烟（`V2-P7-019`）之前**：与批次 3 收尾 §6 相同（浅克隆只警告、源面板路径、APFS 与同卷、根目录不含空白且不在临时目录或主检出、纪元是第一晚的前一个交易日、每个花请求的步骤与每个真实晚上之前先得到用户明确同意、2027 年第一个交易日仍不跑）。

**`V2-P7-023` 的延续生效之前**（本批次新增，三条都由代码强制）：

1. **继任登记点名的前任文件必须已提交**：每一环都经 `registry.committed_registration` 从 `HEAD` 读出、磁盘字节的 sha256 必须等于块里写的 `supersedes_sha256`；未提交或字节不符，每日命令第 1 步在取数之前退出码 2，周报同样拒绝。
2. **继任的运行目录是新的、纪元是新的**：续的是确认性样本，不是纸面账簿；账簿仍“换登记就换账”，旧目录的账簿、日志与记录留给旧登记。
3. **周报要给每个前任一个 `--predecessor-runtime-dir`**：目录按纸面账簿的标记与链上每一环配对；链上有前任配不到目录即按名拒绝，不静默降级（M1）。继任的面板必须从**链根纪元**往前覆盖足够历史，否则那天的重算被拒绝、样本在那里截断（M4）。

**真钱闸门（三条全部成立之前不用打印出来的清单真钱交易）**：① `006` 经用户确认并登记；② 账户成交对账输入与现金约束（含买涨停价按约 1.1 倍名义冻结资金）已上线；③ 用户明确说“开始”。本批次没有一条被满足，也**没有一条被本记录写成“已上线”**。

## 7. 账本、任务书与代码不一致之处

- **任务书的 `git grep -n "PAPER_STATE_DIRECTORY"` 应为空，字面上做不到。** 可执行路径里它确实没有了（`git grep -n PAPER_STATE_DIRECTORY -- src scripts` 为空），但全仓还有 9 处、分三类：一条测试**断言它不存在**（`tests/unit/scripts/test_daily_selection_book.py:620` 的 `assert not hasattr(daily, "PAPER_STATE_DIRECTORY")`）；历史计划/收尾记录**引用被删掉的字面量**（6 处：`docs/superpowers/plans/2026-11-07-v2-p7-006a-ops-and-gaps.md:127`、`:292`、`:318`、`:323` 的代码片段，`docs/superpowers/plans/2026-11-11-p7-dev-batch-3-closeout.md:86`、`:120` 记录它的去留）；以及**本收尾记录自己**的两处——§3 的 R9 按计划原文搬入时照抄了这个标识符，本条 §7 为解释这条检查也点名它。这些都不在 Task 10 的改动范围内，所以本轮按“可执行路径为空 + 测试钉住属性不存在”验收；若控制者要的是全仓为空，需要另一次改动（测试与历史计划都不归本任务改）。
- **“周报一节”被写进了 `docs/runbooks/daily-selection.zh-CN.md`，而幸存者周报的完整一节在 `docs/runbooks/forward-report.zh-CN.md`（「幸存者登记的周报」）。** 任务书只列了前者，控制者补充又明确说“运行手册 `docs/runbooks/daily-selection.zh-CN.md` 周报一节”，所以新增的 `--predecessor-runtime-dir` 一段、M1 与 M4 写在 daily-selection 的「前向周报与 `--factor-gaps`」一节；`forward-report.zh-CN.md` 不在本任务的文件清单里，本轮没有改它。**下一批应当把同一段同步进 `forward-report.zh-CN.md`**，否则从周报那一份手册读到的仍是旧的“没有延续”。（这是本轮唯一一处按任务书字面、而非按文档拓扑落笔的地方。）
- **任务书的行号取自 `8b00c4c`，实现之后有位移。** 本文与各处文档引用的是**当前 tip** 的行号：例如 `admit_predecessors` 在 `scripts/daily_selection.py:735`、`show_book` 在 `:3304`（批次 3 记录写 `:3167`，那是批次 3 的 tip）、`--show-book` 的互斥表在 `:6842-6853`、`--factor-gaps` 的横幅在 `:6892-6906`、`forward_report` 的 `--predecessor-runtime-dir` 在 `:1190-1200`、`_paired_directories` 在 `:489-549`、`_continued` 在 `:807-879`。写之前逐条用 `grep -n` 核对。
- **M2 的口径比测试更宽。** “截断后的前任日仍跑前任核查”由代码的构造顺序保证（候选的 `own` 在 `continued_sample` 之前对每一个前任记录做完，`scripts/forward_report.py:862-872`），但没有任何测试数过核查次数；测试数的是**重算**次数（`test_a_session_scored_otherwise_truncates_the_sample_and_no_later_one_is_scored_again`）。本文按代码写、不把“有测试”说成“有测试钉住核查次数”——若控制者要更硬的钉子，那是另一条测试（不在本任务的文件范围内）。
- **批次 3 收尾 §4 记的 M5 与 `--factor-gaps` 横幅两条缺口在本批次关闭**，本文 §1 的对应条目给出了代码与测试出处；批次 3 记录保持原样作为历史，不改写。
