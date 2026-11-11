# P7 开发批次 3 收尾记录（2026-11-11）

> **状态：记录，不是验收。** 本文记下批次 3 交付了什么、没交付什么、评审与修复各走了几轮、哪些裁决改了计划、哪些事留到以后、哪些事要用户来定，以及真正对真实数据下手之前要具备的前提。它只写工程状态；**工程完成不是研究证据**（`AGENTS.md`「研究诚实」），措辞仍是"候选"，没有任何一行把"通过的测试"写成别的东西。

## 0. 先读这几句

- 批次 3 是三份计划一起做的一批：`V2-P7-024`（预登记停止规则与那唯一一次读数，计划 `docs/superpowers/plans/2026-11-07-v2-p7-024-stop-rule-and-reading.md`）、`V2-P7-006a` 的交易约束（计划 `…-v2-p7-006a-trading-constraints.md`）、以及 `V2-P7-006a` 的运维面加批次 2 验收缺口 G1–G6（计划 `…-v2-p7-006a-ops-and-gaps.md`，另含 `V2-P7-025`、`V2-P7-008` 的两处收尾）。
- 代码在分支 `feat/v2-p7-batch-3` 上（从 `main` 的 `ba60330` 分出），**tip 是 `403b8f5`，共 29 个提交**（`git log --format='%h %s' ba60330..403b8f5`）。**未推送；交付的代码没有跑过任何真实构建、因子、读数或冒烟，一次 Tushare 请求也没有发过**：所有测试都跑在脚本化行情与测试时生成的夹具上。那唯一一次验证读（A Task 5）与真实冒烟**没有做**，仍等用户明确说"开始"；**真钱闸门仍关着**（三条条件没有一条由本批次满足）。**串行全量 `tests/unit` 闸门跑在最终 tip 上，结果以控制者的报告为准**——本文不写它通过，也不写任何通过数字。**没有任何读数登记存在**（`docs/research/p7-readings/` 不存在，`git ls-files` 里也没有），所以**没有绑定被作废**。
- 规模（`git diff --shortstat ba60330 403b8f5`）：**40 个文件、+8,661 / −278**。其中 `src/openalpha_cn/` 10 个文件（新增 `backtest/paper_reconciliation.py`）、`scripts/` 5 个（新增 `scripts/research/p7_read.py`）、`pyproject.toml` 1 个、测试 18 个文件（新增 4 个：`test_paper_reconciliation.py`、`test_survivor_illiquid_rule.py`、`test_daily_selection_book.py`、`test_p7_read.py`）、文档 6 个（3 份计划、新的 `docs/runbooks/p7-reading.zh-CN.md`、`docs/runbooks/daily-selection.zh-CN.md`、`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md`）。
- **受登记绑定的路径**（`scripts/research/registry.REGISTERED_PATHS` = `src`、`scripts/research`、`pyproject.toml`、`uv.lock`；`scripts/daily_selection.py` 与 `scripts/forward_report.py` 由 `also_bound` 加绑）里改了字节的：`src/`（10 个文件）、`scripts/research/`（2 个）、`pyproject.toml`、`scripts/daily_selection.py`、`scripts/forward_report.py`；**`uv.lock` 没有变**（`git diff --name-only ba60330 403b8f5 -- uv.lock` 为空）。仍然没有登记，所以没有绑定被作废。
- 集成分支由三条轨道逐个挑拣而成（A `024`、B `006a` 交易约束、C `006a` 运维面），提交在历史里交错，所以下面**列出提交**而不写区间；哈希取自 `git log ba60330..403b8f5`，账本（不在版本库内：`.superpowers/sdd/`）里的哈希是挑拣之前的，不能直接用。
- 各任务的"通过"数字是实现者和评审者各自最后一次**定向**运行的数字，来自账本，本轮文档没有重跑。整个 `tests/unit` 由控制者在集成分支上串行跑一次；**那次闸门跑在最终 tip 上，结果以控制者的报告为准**，本文不写它通过。
- 本文所在的文档/文本波在 `403b8f5` 之后：除一个提交只改两段 docstring 字符串（`panel_ingest.record_empty_universe_year`、`p7_reading.reading_lines`）外，其余只改 Markdown（把三份计划交给 `ruff format`、本记录、路线图/规格 §9、`CHANGELOG`、交接入口与 `AGENTS.md`）。两段 docstring 的可执行字节与 `403b8f5` 相同（`ast.dump` 去掉 docstring 后逐字符相等）。**该波没有一处行为改动。**
- 账本、任务书与代码不一致的地方，以代码为准，见 §7。

## 1. 交付了什么（逐个 issue）

### `V2-P7-024` 预登记停止规则、那唯一一次读数与两条协议项——代码已交付、本行未关闭

- 提交：前向停止规则块 `9c9ebe8`；读一次的停止规则 `37ca1ae`；唯一一次读的命令与 `expected_periods` `e258e22`、`b319577`，修复轮 `cc055b3`；文档（§5.2 的 G-0、§9 的两行、新运行手册）`3baece4`、`9f4f0cd`、`a74414e`；G3/G4 `a7e0a24`；G5 的 `reading_lines` 文档与 `as_of_reason` 分支 `842bd32`；附加项 A/B/C 的 `403b8f5`；本轮的 docstring 补全见本文写作之后的 `docs(...)` 提交。计划在 `43d15e2`。
- **前向协议的预登记块**：用户 2026-11-07 采纳的数值一个数字一个具名常量，明确写"是用户的采纳值，不是代码默认"（`src/openalpha_cn/strategy_survivor_forward.py:409-422`）；与协议并列的 `survivor-forward-stop-rule/v1` 块（`SURVIVOR_FORWARD_STOP_RULE_KEY` `:745`，模型 `:758-771`，`adopted_forward_stop_rule` `:774`，`read_forward_stop_rule` `:792`）：没有字段默认值、`extra="forbid"`、K 用协议的 `window_periods`、缺块返回 `None` 而不是发明一个默认；报告只陈述（`applied=False`）、不判读；模块 docstring 写明 `006b` 的登记会把它写进 `settings`。
- **读一次的停止规则**：`p7_reading.adopted_stop_rule`（`scripts/research/p7_reading.py:419`）从同一组常量构造，`minimum_periods=expected_periods`；`evaluate_stop_rule`（`:1025`）的 `outcome` 变成三值，读不出来的指标给出 `"not measurable"`，措辞 `NOT_MEASURABLE_WORDING`（`:283`）**不是**"没有证据"，而是它自己的第三种结论；两处旧文案与模块 docstring 同步更正。
- **那唯一一次读的命令**：`scripts/research/p7_read.py`（新文件；`register [--dry-run]` 与 `read`，退出码 0/2/3/4 见 `:46-49`，`_register` `:139`、`_read` `:252`）；primary 登记记录 `expected_periods`，在认领之前按面板自己的日历核对（`register_reading` 里 `scripts/research/p7_reading.py:616` 的检查、`_panel_preflight` 的 `:1173` 检查、命令自己的 `:139`/`:252` 检查），一处算术是 `_exact_most_periods`（`:754`），消息由 `expected_periods_problem`（`:797`）给出；先验 24 是计划记录的算术（484 个会话、步长 20、h20；484 未在仓库离线复核），**工具算出来不是 24 就停在退出码 4、报告，绝不把先验改成算出来的数**。所有请求/配置 flag 必填（含 `--exchange`，只接受 `SSE`）。
- **G-0 门禁文本与运行手册**：`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` 的 §5.2 G-0 行（`:231`）现在写明三种结论（触发 / 未触发 / 不可测）及各自后果；§9 的 2026-11-07 行（`:373`）记录数值、两个记录不得合并与命令；新运行手册 `docs/runbooks/p7-reading.zh-CN.md`（175 行，六节：这是什么、前提、程序、判读、顺序、失败即关闭）给出逐字完整命令（含 `--exchange SSE`、`--expected-periods 24`）与"不是 24 就停"的硬步骤。
- **G3/G4/G5（批次 2 验收缺口）**：G3 —— 运行目录检查钉在打开存储之前（`scripts/research/p7_diagnostics.py:973`；新测试 `tests/unit/test_p7_diagnostics.py::test_the_runtime_directory_is_checked_before_the_store_is_opened`），`test_p7_windows.py` 的目录断言改成有内容的哨兵、路径表加一条逃出研究根的 `..` 行；G4 —— 抽出 `_refuse_a_shaped_history`（`p7_reading.py:1447`），`_refuse_a_second_read`（`:1499`）与 `_require_the_validation_window_spent`（`:1551`）各自先调用它，于是 `second_use` 自己就拒浅克隆/grafts（两条 `…_by_itself` 测试）；G5 —— `reading_lines` 的 docstring 现在完整列出它打印的每一行（本轮提交 2），`as_of_reason` 空/空白/换行三种拒绝各有一条自己的测试。
- **未做 / 未关闭**：读数本身没有做，没有克隆面板、没有登记、没有认领；R4 的确切命令行仍是控制者的候选（运行手册写的是完整命令，但标明必须先由用户确认）；484 个会话无法离线复核，工具自算并在不一致时停下；读数之前要先 `git fetch --unshallow`（需联网与用户同意）。停止规则的**数值**已按用户 2026-11-07 的采纳值实现，本行保持打开是因为读数没有发生。

### `V2-P7-006a` 交易约束内核（现金定量 / 涨停价冻结 / `illiquid` / 资金报告）——代码已交付、本行未关闭

- 提交（B 轨）：09:15 现金定量 `c492d4f`；涨停价冻结 `35aacc8`；资金报告与 ORDERS `0fe91f4`；`illiquid` 命名与 (a) 强制退出 `8db3290`、`ee31bb8`；运行手册与端到端 `6c11854`。计划在 `1ec9344`。
- **买单一律用 09:15 的现金定量**：`build_trade_list` 不再把今晚卖单的回款算进 `available`（`src/openalpha_cn/domain/survivor_orders.py:266`；`proceeds` 累加器删除），`reserved`（在途买单的预留）不变；`TradeList.unfunded` 的语义跟着改成"09:15 的现金不够"，今晚回款在竞价之后才到。旧测试 `test_the_proceeds_of_a_sell_count_towards_what_a_buy_may_spend` 被**改写而不是删除**成两条新测试（`test_the_proceeds_of_tonights_sells_are_not_cash_at_0915`、`test_a_buy_only_tonights_sale_could_fund_is_unfunded_and_named`）。
- **按涨停价（1.10 倍参考价，系统假设）冻结**：一个具名常量 `FREEZE_RATIO = Decimal("1.10")`（`survivor_orders.py:48`）、`FREEZE_SHORTFALL_POLICY`（`:58`，`reduce_lots` / `drop_whole` 两种都实现）、`freeze_price`（`:66`）；现金上限与在途买单的预留按涨停价，**定量价仍是收盘价**（计划 U5 的裁决）；非主板配置必须自己传 `freeze_prices`。冻结价与主板的 `derived_price_band` 逐分一致（测试 `test_the_freeze_price_is_the_main_boards_derived_limit_up`）。
- **`illiquid` 规则与强制退出**：规则本来就在（`floor_share(总体, 0.30)` 的底部切掉、资格丧失即强制退出），本批次只把它命名、写清、钉住：`ILLIQUID_DROP_FRACTION = Decimal("0.30")`（`src/openalpha_cn/domain/survivor_book.py:53`）、`ILLIQUID_POLICY = "forced_exit"`（`:65`，选项 (b) 没实现，要它得先预登记另一个阈值）；`config_id`、`ScreenResult`/`BookDecision` 在相同输入上与改前逐字节相同（评审独立复算）。
- **资金报告与 ORDERS**：`FundingShortfall`（`survivor_orders.py:140`）、`FundingReport`（`:152`）、`funding_report`（`:309`）；`EveningResult`/`CatchUpResult` 带 `funding`，日志的 `paper["funding"]` 打印冻结总额（按每张买单的涨停价重算作交叉核对）、今晚卖单的回款（"竞价之后才到，不是之前"）并**按名列出每一只被现金缩小的买单**；旧日志没有 `funding` 块时打印"shortfalls not recorded"，不升 schema、不加迁移（它是 dict 组装的 JSON 键，不是 pydantic 契约行）。
- **`--show-book`（只读账簿视图，C 轨）**：`scripts/daily_selection.py:3167`（`show_book`）与 `:3239`（`book_view_lines`）；读最新快照、不创建运行目录、不建 SQLite/lock，`--pin-worktree` 在钉住之前就被拒（`4c51764`），与 `--morning-recheck` / `--factor-gaps` / `--launchd-plist` / `--launchd-morning-plist` 互斥；末行是 `read-only` 加一段空白再接 `this listing wrote nothing and made no request`（`scripts/daily_selection.py:3270`）。
- **账户记录对账（C 轨）**：新模块 `src/openalpha_cn/backtest/paper_reconciliation.py`（`account-record/v1`，`:55`）；四种分歧按名记（`:58-61`：`not_placed`、`filled_where_the_book_did_not`、`quantity_differs`、`price_differs`），对账在不一致的记录上**拒绝**（`AccountRecordConflict`，名字指出哪一条）；`--account-record PATH`（`scripts/daily_selection.py:4546`）把记录接进晚间与补跑，**拒绝先于 `_commit`**；摘要的 RECONCILIATION 块（`src/openalpha_cn/strategy_survivor_summary.py:1387`）没有记录时明写 "no account record for …: the paper book's assumption that every printed order was placed stands unverified"（`:1400-1401`），**不改写账簿**（真正的同步是 `006b` 之后的设计，§4）。
- **launchd 生成器（C 轨）**：两个任务（晚间 18:30、早晨复查 08:30 **本机时间**；`LAUNCHD_MORNING_LABEL` `scripts/daily_selection.py:6338`、`MORNING_PLIST_HOUR` `:6339`、`launchd_registration` `:6347`、`launchd_morning_plist` `:6470`）；`--registration` 是参数、必须是钉住 worktree 内部的路径，晚间与早晨两个 flag 一次只生成一个；**只打印、不安装**（没有写 `~/Library/LaunchAgents` 的路径，`--install` 是未知参数）；`--launchd-morning-plist` 也进了冒烟驱动的保留 flag 表（`scripts/p7_smoke.py:151`），冒烟晚间不会把一个未被要求的早晨任务参数当成普通晚间跑下去。
- **未做 / 未关闭**：没有对真实账户、真实行情运行过；真钱闸门仍关着；账户同步（让账簿跟随账户）不在本批次；`--factor-gaps` 不打印冒烟横幅、`--worktree` 与 `--show-book` 同用被静默忽略等见 §4。

### `V2-P7-025` 同一观测只写一次空年、更早的观测不再改写已存零行年——代码已交付、本行未关闭

- 提交：`5d20bef`（G1 去重 + G6 删除不可达分支 + G2 两条否定面测试）、`2d44e2e`（C4 修正轮：更早的观测什么都不写、一次观测只印一行 `EMPTY`）。计划在 `822ad15`。
- **同一观测只写一次**：`record_empty_universe_year` 在读到库里的覆盖记录之后短路：存的正是零行年、且 `stored.as_of >= batch.as_of` 时返回 `()`（`src/openalpha_cn/panel_ingest.py:7669`），分区与新鲜度记录不再重写，`EMPTY` 行只在真正写入时打印；**更晚**的观测仍然重写（新鲜度是重写空年的唯一理由）。
- **删除不可达分支**：`if not by_year: raise PanelBatchError(...)` 删除，换成证据注释（`domain/panel_batch.py` 对 `success` 且零行的批次直接抛 "success requires at least one row"，`split_panel_batch_by_year` 对非成功批次抛错，所以成功的抓取至少一行、`by_year` 非空）。这比批次 2 验收说的"保留为防御或文档化"更强，且删掉的分支本来就无法被构造出来测试。
- **更早的观测不再改写已存零行年**：这**关掉了批次 2 收尾 §4 里"一个更早的 `as_of` 重放到已存的零行年上会重写它、把 `as_of` 往回拨"那一项**（来源：`docs/superpowers/plans/2026-11-07-p7-dev-batch-2-closeout.md` §4，"025 面板层"与"025 接线"两处；`2d44e2e` 把条件收紧到 `>=`，与已存有行年份的"更早抓取不写"同一规则）。**该行为现在有测试**：`tests/integration/panel/test_registry_ingest.py::test_an_older_observation_does_not_move_the_empty_year_back`。
- **一次观测只印一行 `EMPTY`**：`test_one_observation_prints_one_empty_line_however_many_years_ask` 把"同一观测不会打印第二条"钉在 CLI 层（`c4fix` 之后）；另有 `test_the_same_observation_does_not_write_the_empty_year_again`、`test_a_later_observation_still_rewrites_the_empty_year`、`test_a_fetch_carrying_the_observation_year_writes_nothing_and_prints_nothing`、`test_a_refused_write_prints_nothing`。
- **未做 / 未关闭**：没有真实运行（尤其 2027-01-04）；取值（空年只在它自己那一年之内可读，到 12 月 31 日含）仍是**暂定的、待用户确认**；把 `025` 列为 `006a` 的一项并加入 `006` 的依赖仍是控制者的建议、待用户确认。

### `V2-P7-008` `_readable_instant` 回退的两条测试——只改测试，代码未动

**状态写法：**任务书要求 §1 的每个 issue 写"代码已交付、本行未关闭"；**这一行不适用**——这次改动没有代码可交付（`V2-P7-008` 的提交对 `src/` 的改动为零，逐字节不变；批次 3 的 `src/` 改动全部来自其他任务），且 `V2-P7-008` 是 Step 0 的每日入口、在路线图里已于 2026-10-14 标为完成。照实写成"只改测试、行已关闭"，与路线图一致。

- 提交 `a5a2ba6`：两条回退分支的测试改成按**原来的拒绝消息**与**调用次数**钉住，而不是按调用形状（`tests/unit/test_strategy_survivor_registration.py`，三条 `…_readable…` 测试）；`src/` 逐字节不变（`src/openalpha_cn/strategy_survivor_registration.py` 的 `if readable is None or readable <= filed:` 在 `:981`，`_readable_instant` 在 `:1214`）。

## 2. 评审与修复轮次（每个任务一行）

三份计划由控制者在 2026-11-07 派给三个规划子 agent（A `024` 与 B `006a` 用 Opus，C 运维面用 Sonnet）。任务评审者的型号写在括号里（opus 或 sonnet）；2026-11-10 起按用户指示，实现者改为 DeepSeek Harness、验收改为 Opus（C4 本身仍由 Sonnet 验收，见下）。

- A1（前向停止规则块）：实现者 Claude 子 agent（opus），评审 opus，1 轮通过；两处既有的行序测试必须最小改写（`secondary family < power < stop rule < paper ledger`），两处 ruff 驱动的排版偏离；3 个 Minor 延后。
- A2（读一次的停止规则）：Claude（opus），评审 opus，1 轮通过；`unreadable`→`triggered` 的折叠被证明在结构上不可能（未知取值是 `KeyError`，不是回退），数据拒绝测试被加强；1 个 Minor（演练文件的 docstring）延后、后来并入 C 轨的文档扫尾。
- A3（唯一一次读的命令）：Claude（opus），评审 opus，2 轮；首轮判 Needs fixes —— I1 退出码契约有洞（未保护的文件读会以 1 + traceback 逃出 0/2/3/4）、I2 请求/配置 flag 有默认值（裁决：全部必填，含 `--exchange`）、Minor a/b/c；修补 `82b7668` → 挑拣为 `cc055b3` 后定点重审通过；残余（非阻塞）：`subprocess.TimeoutExpired` 不是 `OSError`。
- A4（G-0、§9、运行手册）：Claude 子 agent（sonnet），评审 sonnet，3 轮；首轮 4 个 Important（把命令称作"用户已批准"、`cp -c` 缺 `-R` 且面板布局错、`read_once` 不检查两个 `also_bound` 脚本却在手册里说会拒、退出码 2/3 不总是"什么都没写"）+ 10 个 Minor；修复轮 1 之后第二轮抓到一个 Critical 反转（G-0 的"未通过时"列把"未触发"写成了"回到形态设计"）；修复轮 2 后定点重审通过。
- B1（09:15 现金定量）：Claude 子 agent（opus），评审 opus，1 轮通过；3 个 Minor 延后。
- B2（涨停价冻结）：Claude（opus），评审 opus，1 轮通过；三个计划没点名的既有测试被这次改红并同批修好（初始建仓的 10 名、`_written_off_to_nothing`、"25 个粗晚间"里反向的现金流不足断言），7 个 Minor 延后。
- B3（资金报告 + ORDERS）：Claude（opus），评审 opus，1 轮通过；4 个 Minor 延后（资金行写死 "1.10x" 而不是读常量、journal 的 `funding` 数值没有断言等）。
- B4（`illiquid` 命名）：Claude 子 agent（sonnet），评审 sonnet，2 轮；首轮 1 个 Important —— 任务说"平局按 ts_code"，但原测试的金额互不相同、没有钉住**哪几个名字**被切掉；修补 `cf41b3c` → 挑拣为 `ee31bb8` 后定点重审通过；brief 建议的 M3 变异按字面是 no-op（已记录）。
- B5（真实晚间端到端 + 运行手册）：Claude（opus），评审 opus，1 轮通过；偏差：它的 worktree 里没有 Task 3，只能先 cherry-pick `1a0bab4`（集成为 `0607906`），集成分支已有 Task 3 的 `0fe91f4`，所以只挑拣 `28754b0` → `6c11854`，重复的那一个跳过。
- C1（`--show-book`）：Claude（sonnet），评审 opus，2 轮；首轮 I1：`--show-book` 与 `--pin-worktree` 的互斥检查是死在 pin 分支之后的代码，组合时写 worktree、同步环境并静默忽略 `--show-book`；另有 5 个 Minor；修补 `989c716` → `4c51764` 后定点重审通过。brief 的夹具前提是错的（第一晚零持仓），实现者多跑一晚让断言非空。
- C2（账户记录对账）：Claude（opus），评审 opus，2 轮；首轮 I1：只结算日**不**打印对账行（消费了记录却没印），I2：新的对账文本没有测试；另有 5 个 Minor；裁决见 §3；修补 `f417b10` → `f80d15c` 后定点重审通过。brief 的一条测试自相矛盾（"对全部三笔都不成交达成一致"实际是 3 笔里 2 笔成交），实现者按测试自己的 docstring 收窄并写明。
- C3（launchd 生成器）：Claude（sonnet），评审 sonnet，1 轮 Needs fixes + 修复轮；I1：手册与报告说 `--launchd-morning-plist` 是驱动保留 flag，但 `p7_smoke.PATH_FLAGS` 里没有它，驱动会把一个 0 请求的晚间当成正常跑完；另有 2 个 Minor（两个 launchd flag 同时给出未测、拒绝消息的路径写法）；修补 `5861d4e` → `1d028cd` 后定点重审通过；既有优先序（`--factor-gaps`/`--pin-worktree` 先于 launchd flag）明确延后。
- C4（G1/G2/G6）：Claude 子 agent（sonnet）实现，Sonnet 评审（当时指令）；Spec 全 ✅、接收并挑拣 `5d20bef`；Minor (1)——更早的 `as_of` 会覆盖已存零行年、把新鲜度往回拨——被判定为真实的行为/文档不符，不随接收放过，另开 C4 修正轮（见下）；(3)(4) 停放。
- C4 修正轮 1（更早观测不改写 + 一行 `EMPTY`）：实现者 **DeepSeek Harness**（2026-11-10 起的指令），Opus 定点重审；全部 ✅，`>=` 不能被拿来抑制一次需要的写入、旧观测测试在 `==` 变异下变红；修补 `37094c5` → `2d44e2e`；评审停车一条 Minor："Returns" 行漏了"同一观测覆盖已存空年"这一种情形（由本轮的提交 2 补上）。
- C5（G3/G4）：实现者 **DeepSeek Harness**，评审 Opus，1 轮通过；一处必要偏离：brief 的单一 `consequence` 写法会在 grafts 的两段消息上改变原文（见 §7），helper 因此保留 brief 的必需签名、另加两个**仅 grafts 分支使用**的可选关键字，`_refuse_a_second_read` 的三段拒绝消息逐字不变（机械对照证明）；Minor 1–3 并入 C6 的附加项 A/B/C，Minor 4 记录在案。
- C6（G5 + 附加项 A/B/C）：实现者 **DeepSeek Harness**，评审 Opus，1 轮通过；实现者报 DONE_WITH_CONCERNS：brief 预期的红（两行 `as_of_reason` 那条）在 `a7e0a24` 上本来就是绿的（产品已把两个分支分开，测试是补钉）、brief 的 `-k` 漏选了一条 readable 测试、附加项 B/C 没有可制造的红；Minor 1（`reading_lines` docstring 不全）并入本轮的提交 2，(2)(3) 停放。

## 3. 覆盖了计划的裁决（账本里的 `Ruling:` 与 2026-11-07 的 R 系列）

这些裁决由控制者在当时做出，代价写在账本里；若用户不同意，改动的范围在括号里。

- **R1（编排）**：A（`024`）与 B（交易约束）立即并行，C（运维面）等两者都合入之后再开始。理由：A×C 在 `scripts/research/p7_reading.py` 与它的测试上重叠、B×C 在 `paper_evening.py`/`summary.py`/运行手册上重叠，同时挑拣会冲突。代价：墙钟时间，没有正确性代价。
- **R2（采纳计划建议，不重开）**：B 的 U2 `reduce_lots`、U4 (a) `forced_exit`、U5 定量仍按收盘价、U1 `FREEZE_RATIO` 1.10 是系统假设；A 的 R1 `minimum_periods = expected_periods`、R2 不可测即失败即关闭且说"不是没有证据"、R3 先 dry-run、只有 24 才登记且在用户明确"开始"之后、R4 请求/配置逐项批准、R5 `second_use` 可选；C 的对账只记录与按名分歧、不阻断账簿，早晨任务 08:30 本机时间，记录可选但缺记录时必须明写"账簿假设未核对"。理由：用户 2026-11-07 的一揽子采纳（`common.md`）。代价：各是一个常量/一个分支，全部列在计划里。
- **A3 评审的两条裁决**：I1 退出码有洞 → 修（把 `OSError` 映射到退出码 2，两个子命令都测）；I2 请求/配置 flag 有默认值 → 全部必填（含 `--exchange`，运行手册记录完整命令）。理由：0/2/3/4 是操作者唯一的信号，默认值会被当成冻结的协议值。代价：命令行更长。
- **C2 的 ruling 3（"每个消费记录的日子"）**：只结算日也必须打印当天的对账（只打印 `session ==` 本次结果那一日的条目，较老的补跑条目保持安静），并补文本测试。理由：消费了记录却不印，等于把"账簿假设未核对"藏起来。代价：一行文本 + 两条测试。
- **C3 的 I1 裁决**：`--launchd-morning-plist` 进 `p7_smoke.PATH_FLAGS`（并加参数化测试）。理由：手册承诺驱动会拒绝驱动不了的 flag，代码没有它就会把一个 0 请求的晚间当成正常跑完。代价：一处表 + 一条测试。
- **C4 的接收裁决**：按 Sonnet 的结论接收并挑拣，但 Minor (1)（更早的 `as_of` 重写已存零行年）是真实的行为/文档不符，用 DeepSeek Harness 立刻做修正轮；(3)(4) 停放（重复的 later-observation 测试、负向测试只读 stderr）。理由：行为已经偏离它自己的 docstring，下一批再修没有额外代价但会带着错的说明走。代价：若错，只是多一条小测试。
- **C5 的偏离接受**：C5 报的偏离（helper 的两个可选 grafts 关键字）判为**必要**——brief 的字面写法会改变 `_refuse_a_second_read` 的两条消息，而"拒绝消息逐字不变"是评审的要求；收下并说明。理由：两段 grafts 尾部文本本来就不同，单一 `consequence` 表达不了。代价：若错，helper 多两个只有一处用的参数（外观，不是行为）。
- **C5→C6 的停放裁决**：C5 的 Minor 1–3（spent 消息双 "so"、逃出根的 `..` 行、死的 `resolve()` 条件）不单独开修复轮，并入 C6 的附加项 A/B/C，一个评审面。理由：同一批文件、同一个评审者，拆开只多一轮往返。代价：Task 6 的 diff 略大。
- **C6 的停放裁决**：Minor (2)(3)（计数测试删掉了 filing-instant 检查、`', so whether'` 断言窄）停放，判为外观；(1)（`reading_lines` docstring 不全）并入本轮文档波。理由：(1) 是文档的完整性问题，另两条只是钉得略弱。代价：若错，实例时刻的钉子略弱。
- **R-DSH-1…4（DeepSeek Harness 的规程）**：R-DSH-1 提交带逐字的 `Implemented-by: DeepSeek-V4.1-Flash (DeepSeek Harness)` 尾部，绝不冒充 Claude 的 `Co-Authored-By`；R-DSH-2 控制器为每个 DSH 任务建独立 worktree（DSH 自己没有隔离），并把任务书拷进它的 gitignored `.superpowers/`；R-DSH-3 每份 DSH 报告先由控制器用自己的 `git log`/`git diff --stat` 核对，DSH 的输出不被直接信任；R-DSH-4 修复轮沿用同一个 DSH session。理由：DSH 没有 worktree 隔离，且它的自我报告不能当作验收。代价：若违反，出处与评审基线都不可核。
- **2026-11-10 的指令变更**：实现者继续用 DeepSeek Harness（TDD），但**规划与验收（所有任务评审、重审、终审）改由 Opus 负责**，取代"Sonnet 5.5 做验收"。C4 本身按旧指令由 Sonnet 验收，C5/C6/C4 修正轮由 Opus 验收。理由：用户指示。代价：C4 的验收模型与后面几个不同，已在 §2 如实注明。
- **B5 的集成裁决**：只挑拣 `28754b0` → `6c11854`，跳过它为补 Task 3 而 cherry-pick 的 `0607906`（集成分支已有 Task 3 的 `0fe91f4`，再挑就是重复提交）。理由：避免同一改动进两次。代价：无。

## 4. 延后的事项（带来源）

以下都是评审或实现者提出、控制者裁定"这一批不做"的。来源写作"计划 · 任务"或账本条目（账本不在版本库内：`.superpowers/sdd/`，这份收尾用的是 `.superpowers/sdd/inputs/ledger.md`，只读）。

- **`read_once` 不传 `also_bound`**：它检查 `registry.REGISTERED_PATHS` 的四个路径（`src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`），**不检查** `scripts/daily_selection.py` 与 `scripts/forward_report.py`（这两个由每日命令/周报的准入绑定）。运行手册已经把这写成已知的代码缺口（`docs/runbooks/p7-reading.zh-CN.md:32`、`:170`），**后续 issue 候选**；本批次只把文档改成不再声称它拒。来源：A Task 4 评审 I3、账本"CODE GAP to ledger as a follow-up issue"。
- **`--worktree` 与 `--show-book` 同用时被静默忽略**：`--show-book` 的互斥表没有列 `--worktree`（它列的是 `--morning-recheck`、`--pin-worktree`、`--factor-gaps`、launchd 两个 flag 与 `--account-record`，但没有 `--worktree`；`daily_selection.py:6662-6671`）；组合时 `--show-book` 仍然生效、`--worktree` 被忽略。来源：C Task 1 评审延后项、账本 C1 条目。
- **G1 的每个 `--year` 重抓整份登记簿的请求浪费**：去重只做到"同一观测不重写、只印一行"，`panel build` 的多 `--year` 调用仍然每年重新抓一次整份 `stock_basic`（请求数不变，因为抓取本来就每 `--year` 一次）；把这次浪费去掉是后续小任务。来源：ops-and-gaps 计划 §7 第 2 条与"本轮明确不做"。
- **`--factor-gaps` 不打印冒烟横幅**：`scripts/daily_selection.py` 的 `--factor-gaps` 分支在 `banner = smoke_banner(...)` 之前返回，所以冒烟登记下它不印横幅。这是既有的缺口，与本批次的 `--show-book` 无关（后者会印），不在本批次范围。来源：ops-and-gaps 计划 §7 第 5 条。
- **账户同步（让账簿跟随账户）是 `006b` 之后的设计**：本批次只做"输入 + 对照 + 按名分歧 + 冲突拒绝 + 不改写账簿"；让纸面账簿真正跟随真实账户要改账簿状态与见证规则。来源：ops-and-gaps 计划 §7 第 3 条、C2 的设计边界。
- **`--factor-gaps` / `--pin-worktree` 对 launchd flag 的优先序**：既有代码里 `--factor-gaps`、`--pin-worktree` 先于 launchd 分支，所以 `--factor-gaps --launchd-plist ...` 打印 gap 而不是 plist；这是既有优先序，C3 明确延后。来源：C Task 3 评审延后项、账本 C3 条目。
- **C2 较早补跑条目带对账的情形只有评审者的探针、没有测试**：只结算日的同会话对账有测试，较老的补跑条目 + `account` 的对账只有评审手探，可选补一条钉子。来源：C2 定点重审的 Deferred observation、账本 C2 条目。
- **C4 停放的小项 3/4**：新测试与既有的 later-observation 测试语义重复（照 brief 保留、未去重）；两条负向测试只读 stderr、不读 stdout。来源：账本 C Task 4 entry、C4 接收裁决。
- **C6 停放的小项 2/3**：计数测试丢掉了 filing-instant 检查；`', so whether'` 断言较窄。来源：C6 评审、账本 C6 条目。
- **计划 Task 6 brief 的 `-k` 漏选一条测试**：`-k "… or readable_instant"` 选不中 `test_the_recompute_keeps_the_original_refusal_when_nothing_readable_is_stored`（名字不含 `readable_instant`），实现者改用 `-k "readable"`；brief 若复用要改。来源：C6 报告"偏离与疑虑"第 2 条、账本 C6 条目。
- **其它零散 Minor**（都在账本里逐条记着，这里只列不修的原因）：A3 的 `subprocess.TimeoutExpired` 不是 `OSError`；B3 的资金行把 "1.10x" 写死在排版文本里而不是读 `FREEZE_RATIO`（计划要求的确切文本）；B4 的新测试文件没有进 `artifacts/openalpha-v1-feature-coverage/features.csv`（硬规则 9 管的是**移动**测试文件，新增文件不在已执行的检查里；要不要进台账是另一次三产物修改）。来源：账本对应条目，计划 · 任务。

## 5. 要用户来定的事（全部还没有定）

本节只列批次 3 带出或重新点名的决定；批次 1、批次 2 仍未定的各项仍在各自收尾记录的 §5（`docs/superpowers/plans/2026-11-02-p7-dev-batch-1-closeout.md`、`docs/superpowers/plans/2026-11-07-p7-dev-batch-2-closeout.md`），三份记录合起来才是完整的未决清单。在用户定之前，下面每一项都按"未定"处理；代码里的值只是按采纳值/计划推荐值实现的默认。

1. **A Task 5 那一次不可逆的验证读**（2022-01-04..2023-12-29，一次、只读、0 次网络请求）——前提与命令都在 `docs/runbooks/p7-reading.zh-CN.md`，但**用户还没有说"开始"**。开始之前要定/确认：先 `git fetch --unshallow`（需联网、需用户同意）；R4 的**确切命令行**目前仍是"控制者的候选"（手册记录的是完整一行，标明必须用户确认后才登记）；`--exchange SSE`；**期望 24 期**（先验算术）；以及明确的"开始"。
2. **真实冒烟的每一步**（`V2-P7-019`）：每个花请求的步骤、每个真实晚上之前都要用户的明确同意；阶段上限 260 / 220 次（采纳值）；用户手工跑每一晚、不安装 launchd。第一次真实请求的"开始"仍未给。
3. **U1 的确认**：`V2-P7-025` 的空年只在它自己那一年之内（到 12 月 31 日，含，上海日历日）可读——`UNIVERSE_YEAR_EMPTY_THROUGH = (12, 31)`，计划按推荐值实现，**暂定、待用户确认**（代价：一个常量与几处文档 + 重跑定向测试）。
4. **`025` 的归属确认**：把 `V2-P7-025` 列为 `006a` 的一项并加入 `006` 的依赖，是控制者自批次 1 起的建议，**仍未确认**。
5. **券商假设**：集合竞价只接受限价单、买单挂涨停价 / 卖单挂跌停价取单一开盘价、买单约 1.1 倍名义冻结——代码与文档都写成**本系统的假设**（`FREEZE_RATIO = 1.10` 是系统的，不是券商的）；用户仍要**向券商确认**这三条。
6. **合并 / 推送方式**：批次 3 未推送；分支 `feat/v2-p7-batch-3` 怎么合入、是否推送，由用户定。
7. **批次 1/2 遗留且与本批次冲突的取值**：`021` 的两个暂定取值（两种 `no_data` 都算经核实为空、空名称年读到 1 月 31 日、规则对所有 `panel build` 生效）仍未确认；Task 7（读研究期，零次请求）的明确"开始"仍未给。

## 6. 运行前提

**真实的验证读（`V2-P7-024`，A Task 5）之前**——逐条与核对命令在 `docs/runbooks/p7-reading.zh-CN.md` §2：

1. 仓库不浅：`git rev-parse --is-shallow-repository` 必须答 `false`；否则先 `git fetch --unshallow`（**要联网、先取得用户同意**；读本身仍是 0 次请求）。
2. 没有 grafts 文件（`test ! -e "$(git rev-parse --git-path info/grafts)"`）；替换 ref 被工具以 `--no-replace-objects` 忽略。
3. 登记的 `code_commit` 与 `HEAD` 在四个受绑定路径上无差异、工作树干净（四个路径就是 `REGISTERED_PATHS`；两个 `also_bound` 脚本**不由这次读数检查**，见 §4）。
4. 面板是 `cp -c -R` 的只读克隆，放在**新建空目录**的 `<克隆根>/panel`（存储位置是 `<runtime-dir>/panel`），克隆根**不在** `~/openalpha-research`、**不在任何检出里**（只读连接也会建 `catalog.duckdb.lock`/`.gate`）。
5. 登记已写好、已提交、磁盘字节与 `HEAD` 相同；登记在 `docs/research/p7-readings/` 里。
6. `--as-of` 不早于窗口末日 `2023-12-29` 的 16:30（上海时间）；`--as-of-reason` 一行说明为什么这个时刻就是这次读数所处的知识；`--exchange SSE`（`SZSE` 在参数校验时退出 2）。
7. **期数**：`--expected-periods 24` 是计划记录的先验（484 个会话、步长 20、h20 的算术；484 未离线复核）；工具按面板自己的日历自算，算出来不是 24 就以退出码 4 停下、报告，**绝不把 24 改成算出来的数**。
8. 先读验证窗口、提交它的窗口认领，再读 `second_use`（`second_use` 要求验证窗口的认领已提交且可达）。
9. 解释器环境：本仓库 `uv sync --all-extras --dev` 的 `.venv`；`uv.lock` 只证明锁文件没变，**不证明运行环境与它一致**（`docs/runbooks/p7-reading.zh-CN.md:53`）。

**真实冒烟（`V2-P7-019`）之前**：见 `docs/runbooks/daily-selection.zh-CN.md`「真实数据冒烟」与批次 2 收尾记录 §6——先看是不是浅克隆（`prepare` 只警告、不拒绝）；源面板是 `~/openalpha-research/panel`；卷必须 APFS 且与根目录同卷；根目录不含空白、不在临时目录或主检出里；纪元是第一晚的前一个交易日；每个花请求的步骤与每个真实晚上之前先得到用户明确同意；**2027 年第一个交易日仍不跑**，除非 `V2-P7-025` 已合入、用户同意、且真实运行确认了它的行为。

**真钱闸门（三条全部成立之前不用打印出来的清单真钱交易）**：① `006` 经用户确认并登记；② 账户成交对账输入与现金约束（含买涨停价按约 1.1 倍名义冻结资金）已上线；③ 用户明确说"开始"。本批次交付了第②条的代码，但**没有一条被本记录写成"已上线"**——它没有在真实账户、真实行情上运行过，`006` 也没有登记。

## 7. 账本、任务书与代码不一致之处

- **Task 6 brief 预期的红实际是绿**：brief 说 `test_a_registration_says_why…` 的第三条（两行 `as_of_reason`）在实现前应为红；实测**绿**——产品在 `a7e0a24` 上已经对空/空白分支与换行分支给出不同消息，`match="must be one line"` 本来就满足。实现者按要求**没有改测试去制造红**，那条是补钉。brief 也与自己矛盾：Task 6.2 只改 docstring，任何实现前红都不可能由 docstring 转绿。来源：C6 报告的"偏离与疑虑"第 1 条。
- **Task 5 brief 的单一 `consequence` 写法会改变两条消息**：brief 只给 `_refuse_a_shaped_history(root, *, refusal, consequence)` 一个 `consequence`，又要求 `_refuse_a_second_read` 的消息逐字不变；但 grafts 的两个分支在今天尾部文本**不同**（`a read that cannot be shown to be the first is refused` / `a claim it hides cannot be seen`），单一 `consequence` 只能代入同一句。实现保留 brief 的必需签名与前三个参数，另加两个**仅 grafts 分支**使用的可选关键字（默认取 `consequence`），三段拒绝消息逐字不变（`git show` 旧版与工作树在同一 fake `registry._git` 下渲染，三者 `IDENTICAL`）。这是本轮唯一一处对 brief 字面接口的主动偏离。来源：C5 报告 §6.1、账本 C5 条目。
- **brief 的 `-k` 漏选一条测试**：见 §4（C6 条目）。
- **ops-and-gaps 计划 §7 列出的六条不一致，现状**：(1) 运行手册把定时的决定记在 `006`、路线图记在 `006a` —— T3 已把运行手册的两处（原 `:392-409`、`:631-632`、limitation #13 一带）改写成"由 `V2-P7-006` 决定参数、本批次只生成文本"，不再描述一个已不存在的行为；(2) G1 的"request count unchanged"有两种读法 —— 本批次只做去重/只写一次，**不改请求数**，多 `--year` 的重复抓取记为后续小任务（§4）；(3) 开放项文档里的"账户同步"比计划大 —— 本批次按计划只做输入 + 对照 + 按名分歧 + 冲突拒绝，**不改写账簿**，真正的同步是 `006b` 之后的设计（§4、§5）；(4) 验收记录说 G6"保留为防御或文档化" —— 本批次读码证明它不可达且可证，**删除**并留证据注释（比保留更强，见 §1）；(5) `--factor-gaps` 不打印冒烟横幅 —— 既有缺口，**未动**（§4）；(6) `REGISTRATION_IN_REPOSITORY` 的默认让"显式传了与默认相同的绝对路径"无法与"默认"区分 —— 实现按 `arguments.registration == DEFAULT_REGISTRATION` 比较，并在运行手册与该函数的 docstring 里写明为什么默认仍是 P6 路径（既有测试与 P6 向后兼容）。
- **A Task 4 首轮评审的四个 Important 已在修订里改正**（手册不再称命令"用户已批准"；`cp -c` 改为 `-R` 且面板布局改为 `<克隆根>/panel`；`read_once` 不检查两个 `also_bound` 脚本的事实写进手册而不是写成"会拒"；退出码 2/3 在认领之后不再等于"什么都没写"的段落拆开说清）。
- **C3 brief 的行号是旧的**：brief 给的 392–408、603–604、620 不对，真正过时的句子在 393–409（"生成 launchd 配置文本"）、631–632（"定时"）与 limitation #13（约 648）；按实际位置改写。
- **B2 的影响面比 brief 说的宽**：brief 点名一个 live 测试会红，实际有三个更多（初始建仓的 catch-up、`_written_off_to_nothing`、25 个粗晚间里那条反向的现金流不足断言），都在同一个提交里按新规则修好；其中"全部花光每一分钱"在 1.1 倍冻结下结构上不可能，夹具改到一个 0.50 的名字 + 5.00 最低佣金上重建，这条对佣金下限的耦合值得下一次改动时留意。
- **B4 brief 的 M3 变异是 no-op**：`illiquid` 加进 `FREEZE_COMPATIBLE_REASONS` 不会让测试红——`decide_book` 的 `frozen` 只从带 `suspended` 的持仓构造；按 brief 括号里的**意图**变异（让 `forced` 排除任何带 `illiquid` 的判断）才被杀。记录在案，brief 复用时要改。
- **`test_source_cited_tests.py` 的旧失败已消失**：A Task 4 当时报的 10 条未解析引用全部来自 ops-and-gaps 计划（指向当时还不存在的 `test_daily_selection_book.py` 与 `test_paper_reconciliation.py`）；那两个文件在本批次实现后存在，文档波里 `tests/unit/test_source_cited_tests.py` 与 `tests/unit/test_source_counts_match_the_tree.py` 13 passed。
- **C4 修正轮点名的 docstring 遗漏已补**：评审停车一条"Returns 行漏了同一观测覆盖已存空年"；本轮提交 2 把 `record_empty_universe_year` 的 Returns 末段补全（`src/openalpha_cn/panel_ingest.py:7632`），`reading_lines` 的 docstring 也在同一提交里按实际输出顺序写全。
- **账本里的提交哈希是挑拣前的**：§1 的集成哈希取自 `git log ba60330..403b8f5`；§2 写修复轮时引的是**挑拣前的 worktree 哈希**（账本记的就是这些，例如 `82b7668`、`cf41b3c`、`f417b10`），两者不能混用。另：`a5a2ba6`（挑拣前的 `d677286`）是 C6 的 `V2-P7-008` 两条测试，关的是批次 1/批次 2 留下的延后项；A Task 5 是那次**未做**的真实读数，两者不是一件事。B5 还为补 Task 3 挑过一个 `0607906`（它是 `1a0bab4` 的 worktree 前置挑拣；集成时跳过，见 §2 的 B5）。提交标题原样不改。
