# P7 开发批次 1 · B 轨：空的新年名称变更年（`V2-P7-021`）与纸面账本的两条验收测试（`V2-P7-009`）— 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 2027 年第一个交易日（2027-01-04，约 9 周后）在当年还没有任何更名公告时，幸存者一天的第 2–9 步、清晨复核、迟到记录核查和周报都能走完——把"上游已回答、当年还没有更名"（经核实为空）与"缺失 / 失败 / 截断"（仍按名拒绝）分开；并给 `V2-P7-009` 补上两条缺失的验收测试（纸面路径自己的 T+1；持仓跨新年首个会话、跨除权日的结算）。

**Architecture:** 照 `V2-P6-018`（`panel_ingest.write_empty_announcement_year`）的先例，只动两处：**写**——`panel build` 的 `namechange` 分支在上游回答 `no_data` 时写一个零行分区（带观测时刻，覆盖记录照 P6-018 的形状）；**读**——`panel_ingest.name_history_requirement` 带上 `empty_admissible_through`（规则在 `domain/name_history.py`）。所有读名称语料的地方（事实适配器、第 3 步 doctor、第 4 步因子构建、清晨复核的通知、迟到记录核查的重算）都经这一个要求，**不需要改**。纸面账本两条测试不加行为（已在今天的代码上实跑通过，见 §0.3）。

**Tech Stack:** Python 3.11、pydantic v2、DuckDB（只在面板侧）、pytest；门禁 `ruff`、`ruff format --check`、`mypy src scripts`、`lint-imports`。

**Spec / 依据:** 路线图 `docs/specs/v2/openalpha-cn-v2-roadmap.md` §P7 的 `V2-P7-021`（第 499 行）与 `V2-P7-009`（第 487 行）；规格 `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §9 的 2026-10-14 A2/A3 行（"已知局限（待决）"）与 2026-11-01 各行；路线调整 `docs/superpowers/plans/2026-11-01-p7-route-adjustment.md` §1.3、§2.4 第 1 项（021 是 `006a` 的一项）；计划 2 `docs/superpowers/plans/2026-10-14-p7-step0-adapters-and-ledger.md` Part D 的 A 阶段要求 (2)(8) 与 C3 补充 (b)(c)；运行手册 `docs/runbooks/daily-selection.zh-CN.md` 「已知局限（幸存者形态）」第 1、3 条。先例代码：`src/openalpha_cn/panel_ingest.py:7332-7450`（`write_empty_announcement_year`）、`:7721-7753`（`financial_statement_requirement` 的 `empty_admissible_through`）、`src/openalpha_cn/panel/catalog.py:977-989, 1716-1739`（零行分区的就绪规则）、`src/openalpha_cn/domain/financial_statements.py:423-449`。

---

## 0. 今天的行为（实测，`main` @ `499c51e`，2026-11-01）

全部用仓库自己的脚本化行情（`tests/unit/scripts/daily_market.py`）与 `test_daily_selection_survivor_day.py` 的 `new_year` 夹具（面板到 2026-12-31、2027 日历已存），在草稿目录的探针里跑，**0 次网络请求**；探针不入库。

### 0.1 新年第一个会话（2027-01-04 晚，纪元 2026-12-31）

| 情形 | 结果 |
|---|---|
| 2027 年 `namechange` 一行都没有（上游 `served 0`） | **第 2 步拒绝，退出 1**：`step 2 (panel update) failed: \`panel build\` exited 1: the panel refused this build: cannot write a 'no_data' batch to a partition: 'Tushare served no namechange rows for the requested range: the exchange has not published it, which is a horizon and not a closed period'. 24 partition(s) were written before this build stopped …`；请求 8 次（`trade_cal`、`stock_basic`、`adj_factor`、`suspend_d`、`daily`、`daily_basic`、`stk_limit`、`namechange` 各 1）；`index_daily` 2027 **没写**（`cli._build_panel` 里 `namechange` 排在 `index_daily` 前，`cli.py:6386-6400`）；上一年 `namechange` 的重建（`daily_selection.update_panel` 2595-2623 行，在主构建之后）**从没跑**，所以 12-31 晚公告、晚到的 ST 永远读不到；日志只钉了 `as_of`/`held`，没有 `panel`。**重跑**：同一拒绝，再花 8 次。这一天丢了（下一次运行只把它补结算、不决定）。 |
| 2027 年有一行但公告日在 1-05（上游 `served 1`，晚间读不到） | 同一处拒绝，原因换成 `Tushare served 1 namechange row(s), none of which was yet knowable at as_of 2027-01-04T10:30:00+00:00 …`（`providers/tushare.py:3871-3884`） |
| 2027 年有更名，但登记簿 2027 年**没有任何上市/退市**（`stock_basic` 没有 2027 生命周期年） | **第 4 步拒绝，退出 1**：`step 4 (factor build) failed: residual_vol_60/v1 (factor 1 of 4) was refused: the security registry could not be read …: ['partition_missing']; no partition is registered for stock_basic year=2027 …`；请求 11 次；重跑 0 次、同一拒绝。**这是另一个问题，不在本计划范围内**（见 §5 C1、U3）。现有新年测试能通过，只因为 `NewYearMarket` 在 2027-01-04 埋了一次退市（`test_daily_selection_survivor_day.py:1683-1695` 的 docstring 自己写了） |

失败 / 截断的抓取今天是 `_fetch_panel` 把 `ProviderFailure`（含 10,000 行封顶的 `TushareResponseTruncated`，`providers/tushare.py:3456-3478`）转成 `PanelExit.provider_failure`（退出 4，`cli.py:3011-3025`）——修复之后必须仍是这样。

### 0.2 原型（只在探针里用 monkeypatch 模拟本计划的设计，**不是实现**）

把 `cli.write_name_history` 换成"`no_data` → 写零行分区"、把 `panel_ingest.name_history_requirement` 与 `panel_doctor.name_history_requirement` 换成"带 `empty_admissible_through={year: 1 月 31 日}`"之后：

- 2027-01-04 晚：退出 0，请求 11 次（上表 8 次 + `index_daily` 3 次），`namechange` 2027 零行、`index_daily` 2027 已写，记录已登记，第一晚的 10 张买单已计划。
- 2027-01-05 08:30 清晨复核（2027 仍为空）：退出 0，1 次请求，没有建议、没有通知。
- 2027-01-05 晚（上游给出 2027 第一条更名）：退出 0，零行分区被整份替换为 1 行。
- 2027-01-06 周报：第一条更名公告日在 1-05 → 两条记录都 **verified**；一条**公告日为 1-04、1-05 才发布**的 ST 更名 → 1-04 的记录 **UNVERIFIABLE**，`scope = recompute_differs`，`changes = ["namechange:2027 (its rows through 2027-01-04 were restated)"]`——和任何一天晚到发布的 ST 公告一样，不是新的类别。
- 计划里 B3–B6 的测试代码在这个原型上跑过，22 条全部通过；之后只做了两处小改：放宽一条 `EMPTY` 行的时间戳格式断言（原型打印的是 UTC），并给三条拒绝测试加了 `"EMPTY" not in …`（实现里先写后打印，见 B4）。在今天的代码上，它们按各任务写明的方式失败。

### 0.3 `V2-P7-009` 的两条测试在今天的代码上

B1、B2 的测试代码（下文原样）在 `499c51e` 上**直接通过**：纸面路径没有 T+1 缺陷，跨新年、跨除权日的结算也没有缺陷（1-04 的会话序号 3、每只持仓账本数量不变、账本口径收盘 = 真实收盘 × 2、`ledger_ratio` 2.0000、权益日变动 −0.69%）。**所以 `V2-P7-009` 没有修复任务**，只有两条测试和它们的变异探针。

---

## Global Constraints

逐条来自 `AGENTS.md`、规格与本轮实测，每个任务都隐含这一节。

- 提交、分支、PR 都写 issue ID：`V2-P7-021`（B3–B6）、`V2-P7-009`（B1–B2）。分支名例：`feat/v2-p7-021-empty-name-year`、`test/v2-p7-009-acceptance`。
- 新年第一个会话：**2027-01-04**（2027-01-01 休市）；夹具里同样如此（`NEW_YEAR_DAYS`、`NEW_YEAR`）。
- **"经核实为空"** = 上游回答了 `status="no_data"`：`served 0`，或 `served > 0` 但没有一行在 `as_of` 可知（`providers/tushare.py:4520-4529`）。**"缺失 / 失败"** = 没有这一年的分区，或抓取抛 `ProviderFailure`（传输失败、`code != 0`、`TushareResponseTruncated`：响应行数达到 `TUSHARE_NAMECHANGE_ROW_CAP` = 10,000）——后者仍是 `panel build` 退出 4，第 2 步按名拒绝。
- 零行 `namechange` 分区只在**四条同时成立**时写：①年份就是观测时刻（Asia/Shanghai）所在的年；②观测日不晚于 `name_year_empty_admissible_through(year)`（**用户决定 U1**，推荐 1 月 31 日）；③已存分区没有行（空回答不得覆盖已公告的更名）；④已存有某一年带行的分区可取列形状。任何一条不成立 → `PanelBatchError`（`panel build` 退出 1，"the panel refused this build: …"）。
- 读：`name_history_requirement` 带 `empty_admissible_through`；窗口之后的零行分区一律 `stale`（"stale by rule"），不管 `max_staleness`（`panel/catalog.py:1716-1739` 已有的通用规则）。**从未写过的年份仍是 `partition_missing`**。
- 不放松"上一年尾部缺失"的拒绝（`daily_selection.previous_year_sessions_missing`，2211-2248 行）；`tests/unit/scripts/test_daily_selection_survivor.py` 的 `test_the_year_befores_missed_last_sessions_are_refused_before_the_new_years_first_build`、`test_a_store_that_holds_nothing_of_the_year_before_is_not_refused_for_it`、`test_a_year_before_holding_only_some_session_scoped_partitions_is_refused_whole`、`test_the_year_befores_names_are_built_again_once_then_not_after_a_build_past_its_year_end`、`test_a_first_run_after_january_still_builds_the_year_befores_names_again` **一字不改且保持绿**。
- 登记簿（`stock_basic` 生命周期年）与主板 5% 带宽证人**不在本计划范围**；夹具保留 2027-01-04 的退市，使登记簿有 2027 年（见 U3）。
- 请求数（修复后，脚本化行情）：幸存者一天框架里 2027-01-04 = **11**；第 2 步框架（`test_daily_selection_survivor.py`）= `FIRST_SESSION_OF_A_YEAR` = **12**（T6-T7 的 11 + 上一年名称重建 1）；清晨复核 = **1**。
- 硬规则：两个数据平面分离（名称语料是面板平面）；`domain/` 不引入 numpy/pandas/sqlite3/duckdb（新规则只用 `datetime.date`）；一个组装根（本计划不新建存储）；面板夹具在测试时生成（**不新增测试文件**，全部加进已有文件，所以不触发台账的三件产物）；契约不改字段（零行分区的覆盖记录是 `PartitionCoverage` 的既有形状，`V2-P6-018` 已用）。
- 措辞："候选"；工程完成不是研究证据；本计划不产出任何收益数字。
- 测试卫生：在隔离 worktree 里跑测试一律 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src"`（venv 里的包指向主检出的 `src`），先 `python -c "import openalpha_cn; print(openalpha_cn.__file__)"` 确认；长测试文件只跑 `-k` 选择；**一次只跑一个 pytest 进程**；提交前跑一次 `tests/unit`（约 40 分钟，串行）；绝不在 `git --exec`/hook 里跑测试。
- 变异探针：`cp <file> <scratch>/orig`；改；跑目标测试，**必须红**；`cp <scratch>/orig <file>`；`cmp <file> <scratch>/orig`；全程 `PYTHONDONTWRITEBYTECODE=1`。
- 提交信息末尾：`Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`。不 push、不 squash/rebase；台账（`features.csv`、`summary.json`、`docs/release/openalpha-v1-feature-ledger.md`）**本计划不碰**，见 §7。

## Review Focus

最可能咬到使用者的五类输入 / 失败模式，每条由负责任务里的测试钉住：

1. **一个"成功但空"的上游回答被当成"今年没有 ST"**（之后整年新 ST 名字都读成普通，账簿可能买进 ST）。窗口、"只在观测年"、"不覆盖已有行"三道闸：B3 `test_an_empty_rename_year_is_unreadable_after_its_window_even_without_a_staleness_bound`、`test_an_empty_rename_year_is_refused_past_its_window_or_for_another_year`、`test_an_empty_rename_year_is_never_written_over_announced_renames`；B4 `test_an_empty_answer_past_the_years_window_is_a_failed_fetch`、`test_an_empty_answer_for_a_year_that_holds_renames_is_refused`、`test_an_empty_answer_for_the_year_before_is_refused`。
2. **失败或截断的抓取被当成"经核实为空"**。B4 `test_a_failed_or_truncated_name_fetch_of_the_new_year_is_still_refused_by_name`（两种）；B5 `test_a_refused_name_fetch_on_the_first_session_of_a_year_stops_the_day_at_step_two`。
3. **12-31 晚公告、元旦后才发布的 ST 在 1-04 读不到**（今天主构建先失败，上一年重建根本没跑）。B4 `test_on_a_years_first_session_before_its_first_rename_step_two_records_the_year_empty`（断言 `LATE_ST` 在 1-04 是 ST）。
4. **年初首晚之后的清晨复核**：还没有更名时不得拒绝，也不得说超出范围的话；年内第一条更名当晚晚到发布时要给出建议。B5 `test_the_morning_after_a_first_session_before_any_rename_advises_within_its_scope`、`test_the_morning_after_a_first_session_sees_the_years_first_rename_published_overnight`。
5. **持仓跨新年、跨除权日**（会话序号日历跨年、复权比、虚假回撤）。B2 `test_a_book_held_into_the_first_session_of_a_year_settles_it_across_an_ex_rights_date`。

## Bound code touched（受登记绑定的代码）

| 文件 | 任务 | 改什么 |
|---|---|---|
| `src/openalpha_cn/domain/name_history.py` | B3 | 新常量 `NAME_YEAR_EMPTY_THROUGH` 与两个纯函数 |
| `src/openalpha_cn/panel_ingest.py` | B3 | `write_empty_name_year`、私有 `_write_empty_partition`（`write_empty_announcement_year` 改用它，输出逐字节不变）、`name_history_requirement` 加 `empty_admissible_through` |
| `src/openalpha_cn/panel/store.py` | B3 | 只改 `write_partition` 的 docstring（1839-1846 行"`allow_empty` 的唯一调用者"）；模块 docstring 里列写入者的那一句也改了（B3 评审的 Minor 4） |
| `src/openalpha_cn/panel/catalog.py` | B3 | **corrected 2026-11-02: 漏了这一行。** 只改文字，没有匹配器：`empty_admissible_through` 的 docstring（977-989 行）改成各个写入者各说各的到期日，以及一条就绪消息里的片段 `its "nothing filed yet"` → `its empty answer`（"尚无登记"对名称年不对）；"stale by rule"保留 |
| `src/openalpha_cn/cli.py` | B4 | `namechange` 分支（6386-6392 行）改调新的 `_write_name_year`；两处导入 |
| `scripts/daily_selection.py` | B5 | 只改模块 docstring 第 98-107 行那一段（描述的局限已不存在） |

B1、B2、B6 只加测试。全部在 `006b` 登记之前，现行没有登记被作废（每日登记自 2026-10-08 暂停）。**运维提醒**：`V2-P7-019` 冒烟用的一次性克隆若在 021 合入之前克隆，阶段 2 一旦延长到 2027-01-04 之后，它的新年首日照旧会丢——跨年之前重新克隆。

## Decisions for the user（不由本计划决定）

| # | 决定 | 推荐 | 阻塞什么 |
|---|---|---|---|
| **U1** | "经核实为空"的语义与有效期：(a) 两种 `no_data` 回答（served 0；served 但不可知）都算经核实为空；(b) 一个名称年可以为空**到哪一天**（之后同样的空回答按抓取失败拒绝，已写的空分区按 `stale` 拒读） | (a) 两种都算；(b) **到 1 月 31 日**（日历日，上海时区）。依据：仓库里唯一的实测是 2012 年第一条公告 1-05、第一个交易日 1-04；名称语料每年约 320–380 行；窗口太短的代价是丢交易日（被拒的那天补不成决定），太长的代价只在"上游成功回答却给空"这种罕见故障里出现。**定值之前建议先做一次 0 请求的离线测量**：在 `~/openalpha-research/panel` 的 APFS 克隆（`cp -c`）上，逐年读 `namechange` 分区覆盖记录里最早的 `event_date`（2013–2026），看每年第一条公告离第一个交易日有几天 | B3 的常量值（可先按推荐实现：测试只引用 `name_year_empty_admissible_through`，改值是一行） |
| **U2** | 零行名称年是对**所有** `panel build` 生效，还是只在幸存者一天要求时生效 | 所有（与 `V2-P6-018` 对报表的先例一致；一条规则一个出口）。后果要明说：P6 的 `--full-update` 在一月初从"失败"变为"写空分区并继续"——方向是修复；P6 每日按 `V2-P7-022` 不恢复，风险可忽略 | B4 的形状（若选"只在幸存者"，要给 `panel build` 加一个显式开关并由 `update_panel(survivor=…)` 传入） |
| **U3** | **登记簿的新年缺口（新发现，不在 021 范围）**：一年的第一个会话若当年还没有任何上市或退市，`stock_basic` 没有该年的生命周期分区，第 4 步因子构建（`factor_view._PanelInputs`，`factor_view.py:1517-1520` 读 `load_stock_universe(years=request.years)`）按 `partition_missing` 拒绝，这一天同样丢；前向周报的标签读取（`model_view.py:1701-1707`）在新的一年里同理（读代码推得，未探针）。事实适配器早已绕开（`strategy_survivor_facts._registry_year`，A2-fix3） | **另立一个 issue（建议 `V2-P7-025`），同样是 2027-01-04 之前的硬闸门**，先做 0 请求的离线测量（2014–2026 每年第一个交易日时该年 `stock_basic` 分区是否已存在），再定修法（因子构建/标签读取照 `_registry_year` 读"不晚于该年的最新已存生命周期年"，或照本计划写一个经核实为空的生命周期年） | 不阻塞本计划任何任务；阻塞"真实新年首晚能走完晚间"这一结论（**corrected 2026-11-02: 已立项为 `V2-P7-025`**，规格 §9 的 2026-11-02 行，总数 253；周报的标签读取被拒也已用夹具探到，B6 的评审复现了它） |

## 与代码不符之处（给控制者）

- **C1（最重要）**：`V2-P7-021` 的验收"新年第一天、当年分区为空完成 step 2 与晨查"可以达成，但它**不等于**新年首晚能走完：若当年还没有上市/退市，第 4 步照样拒绝（§0.1 第三行，实测）。现有的 `test_the_first_session_of_a_year_runs_to_its_record_asked_about_both_years` 之所以绿，是夹具埋了 2027-01-04 的退市。见 U3。
- **C2**：issue 写"覆盖面板构建、事实读取、晨查三处"。代码里真正要改的只有**一个写入口**（`panel build` 的 `namechange` 分支）和**一个读规则**（`name_history_requirement`）；事实读取（`strategy_survivor_facts.py:886-891`）与晨查（`daily_selection._fetch_names` 5525-5542、`overnight_notices` 读 5744-5748）**一行不改**。issue 没提的两处读者也会咬：第 3 步 doctor（`panel_doctor.py:1512-1513` 用同一个要求；`check_panel(first_of_year=True)` 问 2026 与 2027）和第 4 步因子构建（`factor_view.py:1526-1530`）——都由同一个要求修好。
- **C3**：计划 2 Part D 的 A 阶段要求 (2) 写"要改 `panel/store.py` 的空分区白名单、`catalog` 的就绪规则……加一条新鲜度规则"。实际：store 没有按数据集的空分区白名单（`allow_empty` 是通用参数，`store.py:1822-1881`），catalog 已有通用的 `empty_admissible_through`（`catalog.py:977-989`）；"新鲜度规则"就是要求里的那张表。store 只需改一句 docstring。
- **C4**：今天的失败还连带两件运行手册只说了一半的事：`index_daily` 2027 不写（手册说了）、**上一年名称的重建不跑**（手册没说）——12-31 晚公告、晚到发布的 ST 在 1-04 读不到。
- **C5**：手册「已知局限」第 1 条说重跑"每次花几次请求"——实测每次 8 次。
- **C6**：周报跨年本身没有缺口（`forward_report._survivor_calendar` 读纪元年到报告年，12 月的运行已建好下一年日历）；021 与它的交集只在 `index_daily` 2027（基准水平）——今天 1-04 没写，修复后写。
- **C7（`V2-P7-009`）**：路线图说缺两条测试——属实；两条在今天的代码上都通过（§0.3），无缺陷、无修复任务。B1 的诚实结论：在纸面路径上，T+1 由三件结构保证（订单只在计划日之后执行且 `build_paper_step` 拒绝未到期单；`PaperPortfolio.advance` 不允许同一会话再活一次；快照不允许卖出未持有的名字，步骤里卖单先于买单）；模拟器自己的 T+1 那一行（`backtest/portfolio.py:211`）在这条路径上**到不了**——它的变异体不会被 B1 杀死，只有在"卖先于买"被破坏时才起作用。（**corrected 2026-11-02: 措辞按评审定为"`portfolio.py:211` 在纸面路径上到不了，除非先卖后买被破坏"**——B1 的 M4 变异体（让步骤先买后卖）单独就能让 211 行成为拒绝同日卖出的那一行；B1 测试的 docstring 第 3 点也不要写成"在 T+1 下被拒"：一个没持有的名字的卖单是被可卖数量检查拒的，原因串相同，同日批次的日期规则那一行是被接受的 M5 缺口。）

---

## 文件结构

| 文件 | 任务 | 动作 |
|---|---|---|
| `tests/unit/backtest/test_paper_settlement.py` | B1 | 加 1 条测试，加 1 个导入名 |
| `tests/unit/scripts/test_daily_selection_survivor_day.py` | B2、B5 | B2：`ExRightsNewYear`、`year_end` 夹具、1 条测试；B5：`EmptyNewYear`、`NamesRefusedNewYear`、2 条测试 |
| `src/openalpha_cn/domain/name_history.py`、`src/openalpha_cn/panel_ingest.py`、`src/openalpha_cn/panel/store.py` | B3 | 见上表 |
| `tests/unit/domain/test_name_history.py`、`tests/integration/panel/test_registry_ingest.py` | B3 | 1 + 7 条测试 |
| `src/openalpha_cn/cli.py` | B4 | `_write_name_year` |
| `tests/unit/scripts/test_daily_selection_survivor.py` | B4 | `NoRenameYet`、两个帮助函数、8 条测试 |
| `scripts/daily_selection.py` | B5 | docstring 一段 |
| `tests/unit/scripts/test_daily_selection_survivor_morning.py` | B5 | 1 个模块夹具、2 条测试 |
| `tests/unit/scripts/test_forward_report_survivor.py` | B6 | 1 个模块夹具、1 条参数化测试（2 例） |

## 任务与依赖

```
B1 (009 T+1) ──────────────┐
B2 (009 跨新年·除权) ───────┤   互相独立；可与 B3/B4 并行
B3 (021 面板层：规则·写·读) ──> B4 (021 panel build / 第 2 步) ──> B5 (021 幸存者一天·清晨复核) ──> B6 (021 迟到记录核查·周报)
```

- B1、B2 与 021 的任何任务都无依赖。
- B2 与 B5 改同一个测试文件（`test_daily_selection_survivor_day.py`）：各自加在「New Year」一节末尾、互不改对方的帮助函数；并行时合并按追加处理，**推荐串行 B2 → B5**。
- B6 用 B5 定义的 `EmptyNewYear`，所以 B6 依赖 B5。

---

## Task B1：纸面路径自己的 T+1（`V2-P7-009`，只加测试）

**Files:**
- Modify (test only): `tests/unit/backtest/test_paper_settlement.py`

**Interfaces（只消费，不产出）：** `backtest.paper_step.build_paper_step(*, session, session_index, orders, bars, adjustment_ratio, benchmark_close) -> PaperStepPlan`（`paper_step.py:162-235`）；`backtest.paper.PaperPortfolio.advance(*, state, session, observed_on) -> PaperSessionResult`（`paper.py:409-444`）；`backtest.paper_settlement.settle_session`（经本文件的 `settle` 帮助函数）；`PaperBookSnapshot` 的校验（`paper_state.py:270-295`）。

- [ ] **Step 1：写测试。** 在文件顶部把导入改为 `from openalpha_cn.backtest.paper_step import NO_BAR_REASON, AttemptedOrder, attempt_id, build_paper_step`，在文件末尾加：

```python
# ---- T+1 on the paper path (V2-P7-009 acceptance) -----------------------------------------------


def test_a_name_bought_at_an_open_is_never_sold_in_that_session_on_the_paper_path(
    tmp_path: Path,
) -> None:
    """A-share T+1 held by the paper path itself, not by the execution policy alone (`V2-P7-009`
    acceptance; the policy's own test is `tests/unit/backtest/test_execution.py::
    test_t_plus_one_and_locked_price_limits_are_enforced`). 600030.SH is bought at the 10.10 open
    of S1, a lot opened on S1.

    1. The evening of S1 can plan its sale only for S2: a `TradeOrder` executes after the session
       that planned it, and handed to S1's own step the sale is refused as not due.
    2. The paper book never lives S1 a second time, so no step dated S1 can follow the purchase.
    3. The one remaining same-session shape -- a sale and a purchase of one name due together --
       is refused by the snapshot (a pending sell is of a held name); built below the snapshot, the
       step runs sells first, so the sale meets no position and the ledger refuses it under T+1
       while the purchase fills.
    4. On S2 the sale fills at the open: 1,000 x 10.40 = 10,400.00, commission max(3.12, 5.00) =
       5.00, transfer 0.104 -> 0.10, stamp duty 5.20, so 10,389.70 back; cash 9,894.90 (S1's
       20,000.00 less the purchase's 10,105.10) + 10,389.70 = 20,284.60.
    """
    order = buy(C, 1000)
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    s1 = settle(
        previous("20000.00", orders=(order,)),
        S1,
        market(OPEN_C_S1, adj={C: "1.000"}),
        portfolio,
        survivors={C},
    )
    assert [(lot.open_date, lot.quantity) for p in s1.book.positions for lot in p.lots] == [
        (S1, 1000)
    ]

    # 1. Planned on the evening of S1 (ordinal 101), the sale executes on S2 (102).
    exit_c = sell(C, 1000, signal=101)
    assert exit_c.execute_session == 102
    evening = tonight(s1, exit_c)
    with pytest.raises(ValueError, match="orders not due yet on session 101"):
        build_paper_step(
            session=S1,
            session_index=101,
            orders=(exit_c,),
            bars={C: OPEN_C_S1},
            adjustment_ratio={C: D(1)},
            benchmark_close=BENCHMARK,
        )

    # 2. No second step dated S1: the book does not live a session twice.
    same_day = build_paper_step(
        session=S1,
        session_index=101,
        orders=(sell(C, 1000),),
        bars={C: OPEN_C_S1},
        adjustment_ratio={C: D(1)},
        benchmark_close=BENCHMARK,
    )
    with pytest.raises(ValueError, match="must move the book forward"):
        portfolio.advance(state=s1.book, session=same_day.step, observed_on=S1)
    assert rows(ledger) == [(attempt_id(order, S1), "filled", None)]

    # 3. A sale and a purchase of one name due on one session.
    with pytest.raises(ValueError, match="pending sells for names not held"):
        previous("20000.00", orders=(order, sell(C, 1000)))
    fresh_ledger, fresh = ledger_at(tmp_path / "below-the-snapshot.sqlite3")
    both = build_paper_step(
        session=S1,
        session_index=101,
        orders=(order, sell(C, 1000)),
        bars={C: OPEN_C_S1},
        adjustment_ratio={C: D(1)},
        benchmark_close=BENCHMARK,
    )
    lived = fresh.advance(state=previous("20000.00").book, session=both.step, observed_on=S1)
    assert [(t.order.side, t.status, t.reason) for t in lived.transitions] == [
        ("sell", "rejected", "insufficient T+1 available quantity"),
        ("buy", "filled", None),
    ]
    assert [row[1:] for row in rows(fresh_ledger)] == [
        ("rejected", "insufficient T+1 available quantity"),
        ("filled", None),
    ]

    # 4. On S2 the sale fills at the open.
    s2 = settle(
        evening,
        S2,
        market(
            bar(C, S2, "10.50", "10.40", "10.60", "10.30", "10.45", "11.55", "9.45"),
            adj={C: "1.000"},
        ),
        portfolio,
    )
    assert rows(ledger) == [
        (attempt_id(order, S1), "filled", None),
        (attempt_id(exit_c, S2), "filled", None),
    ]
    assert s2.filled == (exit_c,)
    assert s2.book.positions == ()
    assert s2.book.cash == D("20284.60")
```

- [ ] **Step 2：运行。** `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" uv run pytest tests/unit/backtest/test_paper_settlement.py -k never_sold_in_that_session -q`。**预期：通过**（验收测试，钉住已交付的行为；在 `499c51e` 上实跑通过）。若失败，这是一个真实缺陷：停下，报告 NEEDS_CONTEXT，不要改源码。
- [ ] **Step 3：变异探针**（每个都必须让测试变红；改完恢复并 `cmp`）：
  - M1 `src/openalpha_cn/backtest/paper_step.py` `_check_due`：`if order.execute_session > session_index` → `> session_index + 1`（第 1 段的 `pytest.raises` 失败）。
  - M2 `src/openalpha_cn/backtest/paper.py` `advance`：`session.trade_date <= state.as_of` → `<`（第 2 段失败）。
  - M3 `src/openalpha_cn/backtest/paper_state.py` `_check_pending_orders`：删掉 `"pending sells for names not held"` 那条 `_refuse`（第 3 段第一个 `pytest.raises` 失败）。
  - M4 `src/openalpha_cn/backtest/paper_step.py` `_in_execution_order`：`return (*sells, *buys)` → `return (*buys, *sells)`（第 3 段的转移顺序断言失败）。
  - 记录但不要求变红：M5 `src/openalpha_cn/backtest/portfolio.py:211` `lot.open_date < market.trade_date` → `<=`——这条在纸面路径上到不了（C7），报告里写明"存活，原因如此"。
- [ ] **Step 4：门禁。** `uv run ruff check tests/unit/backtest/test_paper_settlement.py`、`uv run ruff format --check tests/unit/backtest/test_paper_settlement.py`、整个文件 `uv run pytest tests/unit/backtest/test_paper_settlement.py -q`，提交前 `tests/unit`。
- [ ] **Step 5：提交**（显式列路径）：`test(V2-P7-009): T+1 on the paper path -- a name bought at an open is sold no earlier than the next session`。

---

## Task B2：持仓跨新年首个会话、跨除权日的结算（`V2-P7-009`，只加测试）

**Files:**
- Modify (test only): `tests/unit/scripts/test_daily_selection_survivor_day.py`（加在 `test_the_first_session_of_a_year_runs_to_its_record_asked_about_both_years` 之后）

**Interfaces（只消费）：** 本文件的 `NewYearMarket`、`NEW_YEAR_DAYS`、`FIRST_2027`、`SEED_TARGETS`、`survivor_config`、`_world`、`_completed`、`_snapshot`、`_journal`；`PaperBookSnapshot`（`session_index`、`book`、`entry_factors`、`pending_orders`）；晚间文档 `result.paper.book.holdings[*]` 的 `ledger_ratio`、`ledger_mark`、`real_close`。

- [ ] **Step 1：写测试。**

```python
YEAR_END_EPOCH: Final[date] = date(2026, 12, 29)
"""The year-end world's epoch: the first evening is 30 December, whose buys fill at the open of
31 December, so the book holds them into 2027's first session."""
EX_RIGHTS: Final[date] = FIRST_2027


@dataclass
class ExRightsNewYear(NewYearMarket):
    """The New Year market with every security ex 10-for-10 on 2027's first session: the real
    closes halve from it, that session's published previous close is the adjusted one (half of
    31 December's), and the adjustment factor doubles. Nothing changes before it, so a panel
    seeded from `NewYearMarket` serves this market too."""

    def _close(self, code: str, day: date) -> float:
        close = super()._close(code, day)
        return round(close / 2, 2) if day >= EX_RIGHTS else close

    def _pre_close(self, code: str, day: date) -> float:
        before = super()._pre_close(code, day)
        return round(before / 2, 2) if day >= EX_RIGHTS else before

    def _factor(self, code: str, day: date) -> float:
        return 2.0 if day >= EX_RIGHTS else 1.0


@pytest.fixture(scope="module")
def year_end(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The New Year market's panel through 29 December 2026 -- the build at noon on the 30th --
    name corpus included: the store the evening of 30 December starts from."""
    runtime = tmp_path_factory.mktemp("survivor-year-end") / "runtime"
    market = NewYearMarket(open_days=NEW_YEAR_DAYS)
    market.today = date(2026, 12, 29)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TUSHARE_TOKEN", tds.SECRET_TOKEN)
        patch.setattr(cli, "_panel_transport", lambda: market)
        patch.setattr(cli, "_panel_clock", lambda: tds.WALL_CLOCK)
        arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", "2026"]
        arguments += ["--as-of", "2026-12-30T12:00:00+08:00", "--exchange", "SSE", "--json"]
        for target in (*SEED_TARGETS, "namechange"):
            arguments += ["--dataset", target]
        built = daily.invoke(arguments)
    assert built.exit_code == 0, built.reason()
    return runtime


def test_a_book_held_into_the_first_session_of_a_year_settles_it_across_an_ex_rights_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    year_end: Path,
) -> None:
    """`V2-P7-009` acceptance: the first session of a year settled for a book that holds
    positions, across an adjustment event, on a calendar that spans the new year. Epoch 29
    December: the evening of the 30th plans the initial build, its buys fill at the open of the
    31st, and the evening of the 31st counts its sessions on 2026's calendar alone (the runbook's
    year-end gap). 4 January 2027 is ordinal 3 on the calendar of 2026 and 2027, and every holding
    goes ex 10-for-10 on it. Settled, each keeps its ledger quantity and its entry factor, is
    marked at twice its real close (the ledger's units, ratio 2), and the book's equity moves by
    what the market moved -- not by half."""
    world = _world(
        tmp_path,
        monkeypatch,
        year_end,
        config=survivor_config(epoch=YEAR_END_EPOCH),
        market=ExRightsNewYear(open_days=NEW_YEAR_DAYS),
    )
    last = date(2026, 12, 31)
    for day in (date(2026, 12, 30), last, FIRST_2027):
        _completed(world, capsys, day)

    december, january = _snapshot(world, last), _snapshot(world, FIRST_2027)
    assert (december.session_index, january.session_index) == (2, 3)
    assert december.pending_orders == ()
    held = [(position.subject, position.quantity) for position in december.book.positions]
    assert len(held) == 10
    assert [(position.subject, position.quantity) for position in january.book.positions] == held
    assert {entry.ts_code: entry.entry_adj for entry in january.entry_factors} == {
        code: Decimal(1) for code, _quantity in held
    }
    holdings = _journal(world, FIRST_2027)["result"]["paper"]["book"]["holdings"]
    assert {item["ts_code"] for item in holdings} == {code for code, _quantity in held}
    for item in holdings:
        assert item["ledger_ratio"] == "2.0000"
        assert Decimal(item["ledger_mark"]) == Decimal(item["real_close"]) * 2
    # Measured: -0.69% on the ex-date. Unscaled, the marks would have halved.
    assert abs(january.book.equity / december.book.equity - 1) < Decimal("0.02")
```

- [ ] **Step 2：运行。** `… uv run pytest tests/unit/scripts/test_daily_selection_survivor_day.py -k across_an_ex_rights_date -q`（约 45 秒）。**预期：通过**（在 `499c51e` 上实跑通过；持有的 10 只里没有 2027-01-04 退市的 `SECURITIES[-2]`——若种子变化使它被持有，它会被核销，测试的 `held` 断言就会失败：那是夹具问题，换一个 `YEAR_END_EPOCH` 之前先报告）。
- [ ] **Step 3：变异探针：**
  - M1 `src/openalpha_cn/backtest/paper_settlement.py:364` `ratios = {code: market.adj_factor[code] / entry[code] for code in kept}` → `ratios = {code: Decimal(1) for code in kept}`（`ledger_mark == real_close * 2` 与权益比失败）。
  - M2 `scripts/daily_selection.py` `_survivor_calendar`：`years = tuple(range(epoch.year, session.year + 1))` → `range(epoch.year, session.year)`（1-04 晚被拒，`_completed` 失败）。（**corrected 2026-11-02: 这条改成 M2b**——`years = (epoch.year,)`。原来的 M2 在 `epoch.year == session.year` 时是空区间，会让每一个 2026 年的晚间失败，而不是只让 2027-01-04 失败，所以它不证明测试读到了跨年的日历；M2b 只丢掉会话年，2026 年的晚间不受影响。）
- [ ] **Step 4：门禁**（同 B1，文件换成本文件，用 `-k` 跑 New Year 一节：`-k "first_session_of_a_year or ex_rights"`）。
- [ ] **Step 5：提交：** `test(V2-P7-009): a book held into the first session of a year settles it across an ex-rights date`。

---

## Task B3：面板层——规则、零行名称年的写入与读取（`V2-P7-021`）

**Files:**
- Modify: `src/openalpha_cn/domain/name_history.py`（`NAMECHANGE_DATASET` 定义之后，约 120 行）
- Modify: `src/openalpha_cn/panel_ingest.py`（导入约 401 行；`name_history_requirement` 1560-1581；`write_empty_announcement_year` 7332-7450；新函数放在它之后）
- Modify (docstring only): `src/openalpha_cn/panel/store.py:1839-1846`
- Test: `tests/unit/domain/test_name_history.py`、`tests/integration/panel/test_registry_ingest.py`

**Interfaces（产出，后续任务依赖，名字与类型不得改）：**
- `domain.name_history.NAME_YEAR_EMPTY_THROUGH: Final[tuple[int, int]] = (1, 31)`——`(month, day)`，**U1 待定**。
- `domain.name_history.name_year_empty_admissible_through(year: int) -> date`
- `domain.name_history.name_year_may_be_empty(year: int, day: date) -> bool`——`day <= name_year_empty_admissible_through(year)`；"是否同一年"由写入方判断（同 `announcement_year_may_be_empty` 的分工）。
- `panel_ingest.write_empty_name_year(store: PanelStore, *, year: int, observed_at: datetime, date_timezone: str = DEFAULT_DATE_TIMEZONE) -> PartitionRef`，拒绝一律 `PanelBatchError`（已在 `cli._PANEL_WRITE_REFUSALS` 里，`cli.py:2212-2224`）。
- `panel_ingest._write_empty_partition(store, *, dataset, year, observed_at, template: PartitionCoverage, reason: str, date_timezone: str) -> PartitionRef`（私有，两个空年写入方共用）。
- `panel_ingest.name_history_requirement(...)`：签名不变，返回值多 `empty_admissible_through={year: name_year_empty_admissible_through(year) for year in sorted(set(years))}`。

- [ ] **Step 1：写失败的测试。**

`tests/unit/domain/test_name_history.py`（导入里加 `name_year_empty_admissible_through, name_year_may_be_empty`）：

```python
def test_a_rename_year_may_be_empty_through_its_window_and_not_a_day_after() -> None:
    """`V2-P7-021`: the window always covers the year's first session (4 January 2027), and its
    last day is the last day -- the value is the user's (plan U1), so the test reads the
    function rather than the date."""
    through = name_year_empty_admissible_through(2027)
    assert through.year == 2027 and through >= date(2027, 1, 4)
    assert name_year_may_be_empty(2027, date(2027, 1, 4))
    assert name_year_may_be_empty(2027, through)
    assert not name_year_may_be_empty(2027, through + timedelta(days=1))
```

`tests/integration/panel/test_registry_ingest.py`（导入：`from datetime import UTC, date, datetime, time, timedelta`、`from zoneinfo import ZoneInfo`、`name_year_empty_admissible_through` 加进 `openalpha_cn.domain.name_history` 的导入、`from openalpha_cn.panel.catalog import PanelStorageError`、`write_empty_name_year` 加进 `openalpha_cn.panel_ingest` 的导入），加在 `test_the_2026_namechange_window_stores_nothing_because_its_only_row_is_unannounced` 之后：

```python
# --- V2-P7-021: a rename year with nothing announced in it yet --------------------------------

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _evening(day: date) -> datetime:
    """18:30 Asia/Shanghai on `day`: the survivor day's clock."""
    return datetime.combine(day, time(18, 30), tzinfo=SHANGHAI)


FIRST_EVENING_2027 = _evening(date(2027, 1, 4))
"""The evening of 2027's first session, before any 2027 rename is announced. After every fetch of
`_seed_namechange` (`FETCHED_AT`), so no stored partition is younger than the read."""


def test_a_rename_year_with_nothing_announced_yet_is_recorded_empty_and_read_as_no_rename_yet(
    tmp_path: Path,
) -> None:
    """Recorded at the evening's clock: no rows, the dataset's own columns (taken from the newest
    stored year with rows), as fresh as the observation and knowable from the year's start. Read
    with an earlier year it adds nothing to any history -- the 2021 ST rename is still the newest
    record -- where the same read refuses a year that was never written."""
    store = _seed_namechange(tmp_path)

    ref = write_empty_name_year(store, year=2027, observed_at=FIRST_EVENING_2027)

    assert (ref.dataset, ref.year, ref.row_count) == (NAMECHANGE_DATASET, 2027, 0)
    coverage = store.read_coverage(NAMECHANGE_DATASET, 2027)
    template = store.read_coverage(NAMECHANGE_DATASET, 2021)
    assert coverage is not None and template is not None
    assert coverage.row_count == 0
    assert coverage.as_of == coverage.fetched_at == coverage.last_event_time == FIRST_EVENING_2027
    assert coverage.max_available_time == datetime(2027, 1, 1, tzinfo=SHANGHAI)
    assert coverage.fields == template.fields
    histories = load_name_histories(
        store, years=(2021, 2027), as_of=FIRST_EVENING_2027, max_staleness=None
    )
    assert set(histories) == {"000005.SZ"}
    assert histories["000005.SZ"].name_on(date(2027, 1, 4)) == "ST星源"


def test_a_rename_year_never_written_is_still_refused_as_missing(tmp_path: Path) -> None:
    """Verified empty is a partition that says so; a year with no partition is still missing."""
    store = _seed_namechange(tmp_path)
    with pytest.raises(PanelStorageError, match="partition_missing"):
        load_name_histories(store, years=(2021, 2027), as_of=FIRST_EVENING_2027, max_staleness=None)


@pytest.mark.parametrize(("offset", "readable"), [(0, True), (1, False)])
def test_an_empty_rename_year_is_unreadable_after_its_window_even_without_a_staleness_bound(
    tmp_path: Path, offset: int, readable: bool
) -> None:
    """The writer's rule, applied at read time: recorded empty on 4 January, the year reads as "no
    rename announced yet" through its window's last day and not a day later, with
    `max_staleness=None` -- a store that never fetched the year again must not answer for it."""
    store = _seed_namechange(tmp_path)
    write_empty_name_year(store, year=2027, observed_at=FIRST_EVENING_2027)
    day = name_year_empty_admissible_through(2027) + timedelta(days=offset)

    def read() -> Any:
        return load_name_histories(
            store, years=(2021, 2027), as_of=_evening(day), max_staleness=None
        )

    if readable:
        assert set(read()) == {"000005.SZ"}
    else:
        with pytest.raises(PanelStorageError, match="stale by rule"):
            read()


@pytest.mark.parametrize(
    ("year", "day"),
    [
        (2027, name_year_empty_admissible_through(2027) + timedelta(days=1)),
        (2026, date(2027, 1, 4)),
        # corrected 2026-11-02: a third case, the other side of the year rule, and the ids below
        # replace "another year than the observation's" (a name that named neither case).
        (2027, date(2026, 12, 31)),
    ],
    ids=["past the window", "a year that has ended", "a year not yet begun"],
)
def test_an_empty_rename_year_is_refused_past_its_window_or_for_another_year(
    tmp_path: Path, year: int, day: date
) -> None:
    """A whole market that has announced no rename by the window's end -- or in a year that has
    ended -- is a failed fetch, not a timetable: refused by name, nothing written."""
    store = _seed_namechange(tmp_path)
    with pytest.raises(PanelBatchError, match=f"announcement year {year} served no rename"):
        write_empty_name_year(store, year=year, observed_at=_evening(day))
    assert year not in store.registered_years(NAMECHANGE_DATASET)


def test_an_empty_rename_year_is_never_written_over_announced_renames(tmp_path: Path) -> None:
    """An empty answer where renames are stored is a lost fetch, not a fact about the year."""
    store = _seed_namechange(tmp_path)
    with pytest.raises(PanelBatchError, match=r"year=2021 already holds 1 rename row\(s\)"):
        write_empty_name_year(store, year=2021, observed_at=_evening(date(2021, 1, 5)))
    coverage = store.read_coverage(NAMECHANGE_DATASET, 2021)
    assert coverage is not None and coverage.row_count == 1


def test_an_empty_rename_year_needs_a_stored_year_to_take_its_columns_from(
    tmp_path: Path,
) -> None:
    with pytest.raises(PanelBatchError, match="build the year before 2027 first"):
        write_empty_name_year(_store(tmp_path), year=2027, observed_at=FIRST_EVENING_2027)
```

- [ ] **Step 2：运行，确认失败。** `… uv run pytest tests/unit/domain/test_name_history.py tests/integration/panel/test_registry_ingest.py -q`。**预期：** 两个文件都在收集时 `ImportError: cannot import name 'name_year_empty_admissible_through' …` / `… 'write_empty_name_year' …`——函数还不存在。
- [ ] **Step 3：最小实现。**

`src/openalpha_cn/domain/name_history.py`（只用 `datetime.date`，已导入）：

```python
NAME_YEAR_EMPTY_THROUGH: Final[tuple[int, int]] = (1, 31)
"""`(month, day)`: the last calendar day (Asia/Shanghai, the zone every `ann_date` is in) on
which a rename year with nothing announced in it may be recorded and read empty (`V2-P7-021`).

Until a year's first rename announcement the endpoint serves no row for the year -- 2012's first
is dated 5 January, after its first session on 4 January -- and a year with no partition is
`partition_missing` to every reader of the corpus. Recorded empty, it answers "no rename
announced yet"; past this day a whole market that has announced no rename is a failed fetch, and
an empty partition still stored is stale by rule. The value is the user's (decision U1 of
`docs/superpowers/plans/2026-11-01-p7-dev-batch-1-B-new-year-and-009.md`)."""


def name_year_empty_admissible_through(year: int) -> date:
    """The last day on which rename year `year` may be read as "no rename announced yet"."""
    month, day = NAME_YEAR_EMPTY_THROUGH
    return date(year, month, day)


def name_year_may_be_empty(year: int, day: date) -> bool:
    """Whether a rename year with nothing announced in it is admissible on calendar `day` (an
    Asia/Shanghai date): through `name_year_empty_admissible_through(year)` itself, not after.
    Whether `day` is in `year` at all is the writer's question (`write_empty_name_year`)."""
    return day <= name_year_empty_admissible_through(year)
```

`src/openalpha_cn/panel_ingest.py`：把 `name_year_empty_admissible_through`、`name_year_may_be_empty` 加进 `openalpha_cn.domain.name_history` 的导入；`name_history_requirement` 的 `ReadinessRequirement(...)` 里加：

```text
        # `V2-P7-021`: a rename year recorded empty is readable only through its window -- the
        # writer's own rule (`write_empty_name_year`).
        empty_admissible_through={
            year: name_year_empty_admissible_through(year) for year in sorted(set(years))
        },
```

把 `write_empty_announcement_year` 7406-7450 行（`template = max(...)` 之后的 `reason`、`marker`、`store.write_partition(...)`）收进共用的私有函数，本函数改为 `return _write_empty_partition(store, dataset=dataset, year=year, observed_at=observed_at, template=template, reason=reason, date_timezone=date_timezone)`——`reason` 文本、`marker`、覆盖记录**逐字节不变**：

```python
def _write_empty_partition(
    store: PanelStore,
    *,
    dataset: str,
    year: int,
    observed_at: datetime,
    template: PartitionCoverage,
    reason: str,
    date_timezone: str,
) -> PartitionRef:
    """One zero-row partition of `dataset` for `year`, in `template`'s columns, observed at
    `observed_at`: the write `write_empty_announcement_year` (`V2-P6-018`) and
    `write_empty_name_year` (`V2-P7-021`) share. Every check is the caller's; this only writes."""
    zone = _resolve_timezone(date_timezone)
    marker = ColumnarPanelBatch(
        provider_id=template.provider_id,
        dataset=dataset,
        kind=template.kind,
        as_of=observed_at,
        fetched_at=observed_at,
        status="no_data",
        no_data_reason=reason,
    )
    # The partition and its coverage in one exclusive hold; see `write_panel_batch`.
    return store.write_partition(
        dataset,
        year,
        tuple(
            ColumnSpec(field.name, PANEL_DUCKDB_TYPES[cast(PanelColumnKind, field.kind)])
            for field in template.fields
        ),
        (),
        allow_empty=True,
        coverage=PartitionCoverage(
            dataset=dataset,
            year=year,
            provider_id=template.provider_id,
            kind=template.kind,
            schema_version=template.schema_version,
            batch_digest=marker.content_digest,
            as_of=observed_at,
            fetched_at=observed_at,
            row_count=0,
            date_timezone=date_timezone,
            last_event_time=observed_at,
            max_available_time=datetime.combine(date(year, 1, 1), time(0, 0), tzinfo=zone),
            revised_row_count=0,
            subjects=(),
            fields=template.fields,
            dates=(),
            revisions=(),
        ),
    )


def write_empty_name_year(
    store: PanelStore,
    *,
    year: int,
    observed_at: datetime,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> PartitionRef:
    """Record that no rename has been announced in announcement year `year` yet (`V2-P7-021`).

    `write_empty_announcement_year`'s door, for the rename corpus and for its reason: until a
    year's first rename the endpoint serves no row for the year, and a year with no partition is
    `partition_missing` to every reader -- the facts adapter, the factor build, the doctor and
    the morning re-check read the corpus through the session's year -- so a year's first sessions
    could not run (the survivor runbook's limitation 1). "No rename announced yet" is a true
    answer on those days, given honestly by a partition that says so and says when it was
    observed; the first write of a real row replaces it whole. The coverage record is
    `_write_empty_partition`'s.

    Refused (`PanelBatchError`) when it could hide something: a year other than `observed_at`'s
    own, a day after the year's window (`name_year_may_be_empty`), a stored partition that holds
    rows (an empty answer over announced renames is a lost fetch), a store with no year whose
    columns it can take.
    """
    zone = _resolve_timezone(date_timezone)
    day = observed_at.astimezone(zone).date()
    through = name_year_empty_admissible_through(year)
    if day.year != year or not name_year_may_be_empty(year, day):
        raise PanelBatchError(
            f"{NAMECHANGE_DATASET} announcement year {year} served no rename knowable as of "
            f"{observed_at.isoformat()}, and an empty year is admissible only within that year "
            f"and only through {through.isoformat()}; after it, a whole market that has "
            "announced no rename is a failed fetch"
        )
    stored = store.read_coverage(NAMECHANGE_DATASET, year)
    if stored is not None and stored.row_count:
        raise PanelBatchError(
            f"{NAMECHANGE_DATASET} year={year} already holds {stored.row_count} rename row(s); "
            "an empty answer now would replace them with nothing, which is a lost fetch and not "
            "a fact about the year"
        )
    shaped = [
        coverage
        for coverage in (
            store.read_coverage(NAMECHANGE_DATASET, other)
            for other in store.registered_years(NAMECHANGE_DATASET)
        )
        if coverage is not None and coverage.row_count
    ]
    if not shaped:
        raise PanelBatchError(
            f"{NAMECHANGE_DATASET} has no stored year with rows to take the partition's columns "
            f"from; build the year before {year} first"
        )
    template = max(shaped, key=lambda coverage: coverage.year)
    reason = (
        f"no {NAMECHANGE_DATASET} rename announced in {year} as of {observed_at.isoformat()}; "
        f"the year may be read empty through {through.isoformat()}"
    )
    return _write_empty_partition(
        store,
        dataset=NAMECHANGE_DATASET,
        year=year,
        observed_at=observed_at,
        template=template,
        reason=reason,
        date_timezone=date_timezone,
    )
```

`src/openalpha_cn/panel/store.py:1839-1846` 的 docstring 改为：`allow_empty` 的调用者是 `panel_ingest._write_empty_partition`，经 `write_empty_announcement_year`（报表公告年，`V2-P6-018`）与 `write_empty_name_year`（名称公告年，`V2-P7-021`）各自的规则；其他调用者照旧得到 "cannot write an empty partition batch"。

- [ ] **Step 4：运行，确认通过。** 同 Step 2 的命令全绿；**先例的回归**——`uv run pytest tests/unit/test_cli_panel_build_statement_sweep.py tests/integration/panel/test_statement_reach_years.py tests/integration/panel/test_panel_catalog.py -q` 改动前后都跑一次，结果一致（证明 `write_empty_announcement_year` 的抽取逐字节不变）。
- [ ] **Step 5：变异探针：**
  - M1 `name_year_may_be_empty`：`<=` → `<`（域测试与读窗口测试 `offset=0` 失败）。
  - M2 `write_empty_name_year`：删掉 `day.year != year or`（**corrected 2026-11-02:** 原文写"`another year than the observation's` 失败"，但那个用例〔2026 年、观测日 2027-01-04〕本来就被窗口规则拒绝，删掉这一条什么也不变红——变异体活到评审；改为 `a year not yet begun`〔2027 年、观测日 2026-12-31；窗口还没过，只有年份规则能拒绝它〕失败，`a year that has ended` 由窗口规则拒绝，不是它的证人）。
  - M3 `write_empty_name_year`：`if stored is not None and stored.row_count:` → `if False:`（`never_written_over_announced_renames` 失败）。
  - M4 `name_history_requirement`：删掉 `empty_admissible_through=…`（`recorded_empty_and_read_as_no_rename_yet` 与 `offset=0` 失败）。
  - M5 `name_history_requirement`：表里的日期 `+ timedelta(days=1)`（`offset=1` 失败）。
- [ ] **Step 6：门禁。** `ruff check`、`ruff format --check`（三个源文件 + 两个测试文件）、`uv run mypy src scripts`、`uv run lint-imports`（`domain-purity` 必须绿）；提交前 `tests/unit` 与 `tests/integration/panel`。
- [ ] **Step 7：提交：** `feat(V2-P7-021): a rename year with nothing announced yet is recorded empty and read as such through its window`。

（**corrected 2026-11-02: 评审之后 B3 另有三处，计划原文没有。**①观测时刻按**上海日历日**判断，而不是 UTC 日或本地日：加了四个 UTC 边界用例（`test_an_empty_rename_year_is_judged_on_the_shanghai_day_of_its_observation`；Tokyo +9 与 Bangkok +7 的变异体也变红）；②窗口之内的第二次空写是允许的，按较晚的观测时刻重新记下（`test_an_empty_rename_year_is_recorded_again_inside_its_window_at_the_later_observation`）；③`catalog.py` 的文字改动（见上面"Bound code touched"表里补的一行）和 `write_panel_batch`、`store.py` 两个被这条规则弄错的 docstring，按评审的要求在本任务里改，不留给文档任务。B3 评审还证明了 P6 先例的抽取行为不变：`write_empty_announcement_year` 的 AST 切片在抽取前后比对，加先例测试集 104 条前后一致。）

---

## Task B4：`panel build` 记录空的名称年；第 2 步在新年首个会话走完（`V2-P7-021`）

依赖 B3。

**Files:**
- Modify: `src/openalpha_cn/cli.py`（导入：114 行的 `openalpha_cn.domain.name_history` 加 `name_year_empty_admissible_through`；263-269 行的 `panel_ingest` 导入加 `write_empty_name_year`；6386-6392 行的 `namechange` 分支；新函数 `_write_name_year` 放在 `_write_statement_year`（5329 行）之后）
- Test: `tests/unit/scripts/test_daily_selection_survivor.py`（「New Year」与「the year before's last sessions」两节之间）

**Interfaces：**
- 消费：`panel_ingest.write_name_history`（不变：对 `no_data` 仍抛 `PanelBatchError`，`tests/integration/panel/test_registry_ingest.py::test_the_2026_namechange_window_stores_nothing_because_its_only_row_is_unannounced` 照旧绿）、`write_empty_name_year`、`name_year_empty_admissible_through`。
- 产出：`cli._write_name_year(store: PanelStore, batch: ColumnarPanelBatch, *, year: int, now: datetime) -> PartitionRef`；`panel build` 的 stderr 在写空年时多一行，前缀固定为 `EMPTY namechange year=<year>: no rename announced yet at <now>`。

- [ ] **Step 1：写失败的测试。** 导入：`from datetime import UTC, date, datetime, time, timedelta`、`from daily_market import NAMECHANGE_FIELDS, SECURITIES, Market, compact, response, weekdays`、`from openalpha_cn.domain.name_history import NAMECHANGE_DATASET, name_year_empty_admissible_through`。

```python
# --- V2-P7-021: the first session of a year before the year's first rename ---------------------


@dataclass
class NoRenameYet(NamesAcrossNewYear):
    """`NamesAcrossNewYear` before 2027's first rename: the endpoint serves no 2027 row -- or, with
    `pending`, one announced on 5 January, served but not knowable on the 4th. `silent` years
    answer nothing at all; `fault` makes every 2027 request fail -- `"failing"`, its connection
    reset on every attempt, or `"truncated"`, an answer at the endpoint's 10,000-row cap."""

    pending: bool = False
    silent: frozenset[str] = frozenset()
    fault: str = ""

    def _names(self, params: Mapping[str, Any]) -> list[list[Any]]:
        year = str(params["start_date"])[:4]
        if year in self.silent:
            return []
        if year != "2027":
            return super()._names(params)
        if self.pending:
            return [[RENAMED_2027, "新名称", "20270105", None, "20270105", "改名"]]
        return []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = payload["params"]
        if (
            payload["api_name"] == NAMECHANGE_DATASET
            and self.fault
            and str(params["start_date"])[:4] == "2027"
        ):
            self.requests.append((NAMECHANGE_DATASET, dict(params)))
            if self.fault == "failing":
                raise ConnectionError("the namechange connection was reset")
            capped = [
                [f"{600000 + index:06d}.SH", "名称", "20270104", None, "20270104", "改名"]
                for index in range(10_000)
            ]
            return response(NAMECHANGE_FIELDS, capped)
        return super().post(payload)


def _december_no_rename_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **market: Any
) -> Store:
    """`_new_year`'s store on `NoRenameYet`, with 31 December's step run.

    (corrected 2026-11-02: this helper was drafted as `_december`; the test file already defines
    a `_december` of its own, further down, with other parameters, which a second definition
    here would have shadowed. The shipped name is `_december_no_rename_yet`, here and at every
    call site below.)"""
    shop = NoRenameYet(open_days=NEW_YEAR, delisted=((DELISTED, FIRST_2027),), **market)
    store = _seeded(tmp_path, monkeypatch, shop, as_of="2026-12-24T12:00:00+08:00", year=2026)
    _update(
        store,
        LAST_2026,
        targets=daily.targets_for_survivor(SURVIVOR_BUILDS),
        survivor=_survivor(),
        next_year=True,
    )
    return store


def _names_build(store: Store, *, year: int, as_of: datetime) -> Any:
    """`openalpha panel build --dataset namechange --year <year>` at `as_of`, in process: the one
    build the morning re-check makes (`daily_selection._fetch_names`)."""
    store.market.clear()
    store.market.today = as_of.astimezone(daily.SHANGHAI).date()
    arguments = ["panel", "build", "--runtime-dir", str(store.runtime), "--year", str(year)]
    arguments += ["--as-of", as_of.isoformat(), "--exchange", EXCHANGE, "--incremental", "--json"]
    arguments += ["--dataset", NAMECHANGE_DATASET]
    return daily.invoke(arguments)


def _morning(day: date) -> datetime:
    return datetime.combine(day, time(8, 30), tzinfo=daily.SHANGHAI)


@pytest.mark.parametrize("pending", [False, True], ids=["nothing served", "served, not knowable"])
def test_on_a_years_first_session_before_its_first_rename_step_two_records_the_year_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pending: bool
) -> None:
    """`V2-P7-021`. 4 January 2027, and the endpoint has no 2027 rename to give: nothing at all,
    or one announced on the 5th. Both are the provider's answer, so the year is recorded empty --
    no rows, observed at the day's clock -- and the step goes on: `index_daily` is built, the year
    before's names are built again (the ST rename announced on 31 December and published late is
    read on the day it takes effect), and every read of `build_facts_day` is served. Before this
    `panel build` refused the year ("cannot write a 'no_data' batch"), stopped short of
    `index_daily` and of the year before's names, and the session was lost."""
    store = _december_no_rename_yet(tmp_path, monkeypatch, pending=pending)
    targets = daily.targets_for_survivor(SURVIVOR_BUILDS)

    january, counts = _update(store, FIRST_2027, targets=targets, survivor=_survivor())

    assert january["caught_up"] == []
    assert sum(counts.values()) == FIRST_SESSION_OF_A_YEAR
    assert _years_asked(store, NAMECHANGE_DATASET) == ["2027", "2026"]
    coverage = store.panel.read_coverage(NAMECHANGE_DATASET, 2027)
    assert coverage is not None and coverage.row_count == 0
    assert coverage.as_of == _evening(FIRST_2027)
    assert 2027 in store.panel.registered_years(INDEX_DAILY_DATASET)
    after = {fact.ts_code: fact for fact in _facts(store, FIRST_2027, monkeypatch).facts}
    assert after[LATE_ST].special_treatment is True
    assert after[RENAMED_2027].special_treatment is False
    assert set(after) == set(SECURITIES) - {DELISTED}


@pytest.mark.parametrize("fault", ["failing", "truncated"])
def test_a_failed_or_truncated_name_fetch_of_the_new_year_is_still_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Only the provider's answer is "verified empty". A 2027 request whose connection is reset
    on every attempt, or whose answer reaches the measured 10,000-row cap (a truncated answer
    cannot be told from a complete one), is a failed fetch: `panel build` exits 4
    (`provider_failure`), step 2 is refused naming the dataset, and no 2027 partition of the
    corpus is written."""
    store = _december_no_rename_yet(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "TushareProvider", partial(cli.TushareProvider, sleep=lambda _s: None))
    store.market.fault = fault

    with pytest.raises(daily.StepFailedError, match="refused dataset namechange") as refused:
        _update(
            store,
            FIRST_2027,
            targets=daily.targets_for_survivor(SURVIVOR_BUILDS),
            survivor=_survivor(),
        )

    assert "exited 4" in str(refused.value)
    assert 2027 not in store.panel.registered_years(NAMECHANGE_DATASET)


def test_the_build_that_records_an_empty_rename_year_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The morning's build on 5 January, the year still empty: one request, exit 0, an `EMPTY`
    line naming the year and how long it may be read empty, and the partition recorded."""
    store = _december_no_rename_yet(tmp_path, monkeypatch)
    through = name_year_empty_admissible_through(2027)

    built = _names_build(store, year=2027, as_of=_morning(date(2027, 1, 5)))

    assert built.exit_code == 0, built.reason()
    assert f"EMPTY {NAMECHANGE_DATASET} year=2027: no rename announced yet at " in built.stderr
    assert f"read empty through {through.isoformat()}" in built.stderr
    assert store.market.payloads == [NAMECHANGE_DATASET]
    coverage = store.panel.read_coverage(NAMECHANGE_DATASET, 2027)
    assert coverage is not None and coverage.row_count == 0


def test_an_empty_answer_for_a_year_that_holds_renames_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2027 holds a rename (written on the 6th); on the 7th the endpoint answers nothing for the
    year: a lost fetch, refused by the panel, and the stored rename stays."""
    store = _december_no_rename_yet(tmp_path, monkeypatch, pending=True)
    first = _names_build(store, year=2027, as_of=_morning(date(2027, 1, 6)))
    assert first.exit_code == 0, first.reason()
    store.market.pending = False

    again = _names_build(store, year=2027, as_of=_morning(date(2027, 1, 7)))

    assert again.exit_code == 1
    assert "already holds 1 rename row(s)" in again.reason()
    assert "EMPTY" not in again.stderr
    coverage = store.panel.read_coverage(NAMECHANGE_DATASET, 2027)
    assert coverage is not None and coverage.row_count == 1


def test_an_empty_answer_past_the_years_window_is_a_failed_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day after the window's end, 2027 still answers nothing: a failed fetch, not a
    timetable. Refused by the panel, nothing written, and no `EMPTY` line."""
    through = name_year_empty_admissible_through(2027)

    store = _december_no_rename_yet(tmp_path, monkeypatch)
    late = _names_build(store, year=2027, as_of=_morning(through + timedelta(days=1)))

    assert late.exit_code == 1
    assert f"only through {through.isoformat()}" in late.reason()
    assert "EMPTY" not in late.stderr
    assert 2027 not in store.panel.registered_years(NAMECHANGE_DATASET)


def test_an_empty_answer_for_the_year_before_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The year before's names built again in January, and the endpoint answers nothing for it:
    a year that has ended with no rename is a failed fetch; its stored partition is untouched.
    (`silent` is turned on after 31 December's step, which asks 2026 itself.)"""
    store = _december_no_rename_yet(tmp_path, monkeypatch)
    store.market.silent = frozenset({"2026"})
    before = store.panel.read_coverage(NAMECHANGE_DATASET, 2026)

    past = _names_build(store, year=2026, as_of=_morning(date(2027, 1, 5)))

    assert past.exit_code == 1
    assert "announcement year 2026 served no rename" in past.reason()
    assert "EMPTY" not in past.stderr
    assert store.panel.read_coverage(NAMECHANGE_DATASET, 2026) == before
```

（`_december_no_rename_yet`〔corrected 2026-11-02: 草稿里叫 `_december`〕的返回类型是 `Store`，其 `market` 的静态类型是 `Market`；给 `fault`、`pending`、`silent` 赋值的几行若 mypy/ruff 有意见，就在 `_december_no_rename_yet` 里返回 `tuple[Store, NoRenameYet]`——测试本身不进 `mypy src scripts`，原型照上面原样跑通。）

- [ ] **Step 2：运行，确认失败。** `… uv run pytest tests/unit/scripts/test_daily_selection_survivor.py -k "first_rename or rename_year or renames or truncated or year_before_is_refused or past_the_years_window" -q`。**预期：** `records_the_year_empty` 两例 `StepFailedError: … cannot write a 'no_data' batch to a partition …`；`says_so` 退出 1（同一句）；`holds_renames`、`past_the_years_window`、`year_before_is_refused` 的文字断言失败（今天的拒绝是 "cannot write a 'no_data' batch"）；`failed_or_truncated` 两例**今天就绿**——它们是回归钉，修复前后都必须绿。
- [ ] **Step 3：最小实现**（`cli.py`）：

```python
def _write_name_year(
    store: PanelStore, batch: ColumnarPanelBatch, *, year: int, now: datetime
) -> PartitionRef:
    """Write one announcement year of the rename corpus -- or, when the endpoint answered it with
    no row knowable at `now`, record it empty (`panel_ingest.write_empty_name_year`, `V2-P7-021`).

    Both of `fetch_panel`'s no-data answers are answers -- "served nothing for the year" and
    "served rows none of which was knowable yet" -- while a refused or truncated fetch never
    reaches here: `_fetch_panel` raises it as `provider_failure`. The writer refuses by name a
    year that may not be empty: not `now`'s own, past its window, already holding renames."""
    if batch.status == "success":
        return write_name_history(store, batch)
    ref = write_empty_name_year(store, year=year, observed_at=now)
    typer.echo(
        f"EMPTY {NAMECHANGE_DATASET} year={year}: no rename announced yet at {now.isoformat()} "
        f"({batch.no_data_reason}); the year may be read empty through "
        f"{name_year_empty_admissible_through(year).isoformat()}; recorded as an empty partition",
        err=True,
    )
    return ref
```

`_build_panel` 的分支改为 `written.setdefault(NAMECHANGE_DATASET, []).append(_write_name_year(store, _fetch_panel(provider, NAMECHANGE_DATASET, as_of=_year_end_as_of(year, now)), year=year, now=now))`。`EMPTY` 行在写入成功**之后**才打印（先例在写之前打印；这里写入可能被拒，先打印会误导）。

- [ ] **Step 4：运行，确认通过。** 同 Step 2 全绿；再跑本文件的 New Year 与上一年两节 `-k "new_year or year_before or first_session"`（既有测试一字不改）与 `tests/integration/panel/test_registry_ingest.py`。
- [ ] **Step 5：变异探针：**
  - M1 `_write_name_year`：`if batch.status == "success":` → `if True:`（`records_the_year_empty` 回到今天的拒绝）。
  - M2 `_fetch_panel`：把 `except ProviderFailure` 改为返回一个 `no_data` 批次（`return ColumnarPanelBatch(provider_id=failure.provider_id, dataset=dataset, kind=dataset, as_of=as_of, fetched_at=as_of, status="no_data", no_data_reason="x")`）——`failed_or_truncated` 两例必须变红（证明它们真的在区分"失败"与"空"）。
  - M3 把 `typer.echo(...)` 挪到 `write_empty_name_year(...)` 之前（先例的顺序）：`past_the_years_window`、`holds_renames`、`year_before_is_refused` 的 `"EMPTY" not in …` 断言失败。
- [ ] **Step 6：门禁**（`ruff`、`format`、`mypy src scripts`、`lint-imports`；提交前 `tests/unit`）。
- [ ] **Step 7：提交：** `feat(V2-P7-021): panel build records a rename year with nothing announced yet, and the year's first session's step 2 completes`。

---

## Task B5：幸存者一天与清晨复核在新年首个会话走完（`V2-P7-021`）

依赖 B4（及 B2，若串行同一文件）。

**Files:**
- Modify (docstring only): `scripts/daily_selection.py` 第 98-107 行
- Test: `tests/unit/scripts/test_daily_selection_survivor_day.py`（New Year 一节末尾）、`tests/unit/scripts/test_daily_selection_survivor_morning.py`（「New Year」一节末尾）

**Interfaces：** 只消费（一天：`daily.main`；清晨：`--morning-recheck`）；产出测试用的 `EmptyNewYear`、`NamesRefusedNewYear`（B6 用 `EmptyNewYear`）。

- [ ] **Step 1：写失败的测试。**

`test_daily_selection_survivor_day.py`（导入加 `from collections import Counter`）：

```python
@dataclass
class EmptyNewYear(NewYearMarket):
    """The New Year market before 2027's first rename (`V2-P7-021`): the endpoint serves no 2027
    row until a rename in `renames` is published. `renames` are `(code, name, announced,
    effective, published)`. The registry's 4 January delisting stays, so 2027 has a lifecycle
    partition -- the registry's own New Year gap is another issue (plan U3)."""

    renames: tuple[tuple[str, str, date, date, date], ...] = ()

    def _names(self, params: Mapping[str, Any]) -> list[list[Any]]:
        if str(params["start_date"])[:4] != "2027":
            return super()._names(params)
        today = self.today or self.open_days[-1]
        return [
            [code, name, compact(effective), None, compact(announced), "ST"]
            for code, name, announced, effective, published in self.renames
            if today >= published
        ]


@dataclass
class NamesRefusedNewYear(EmptyNewYear):
    """`EmptyNewYear`, its 2027 `namechange` connection reset on every attempt."""

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = payload["params"]
        if payload["api_name"] == "namechange" and str(params["start_date"])[:4] == "2027":
            self.requests.append(("namechange", dict(params)))
            raise ConnectionError("the namechange connection was reset")
        return super().post(payload)


def test_the_first_session_of_a_year_before_its_first_rename_runs_to_its_record_and_evening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    new_year: Path,
) -> None:
    """`V2-P7-021`: 4 January 2027 with no rename announced in 2027. Step 2 records the year
    empty and builds the rest -- `index_daily` included -- in 11 requests; the doctor and the gate
    clear, asked about both years; the record is filed and the evening run. Run again, the day is
    printed from its journal with no request. The next evening, the year's first rename replaces
    the empty partition whole."""
    market = EmptyNewYear(open_days=NEW_YEAR_DAYS)
    world = _world(
        tmp_path,
        monkeypatch,
        new_year,
        config=survivor_config(epoch=date(2026, 12, 31)),
        market=market,
    )
    doctor = _calls(monkeypatch, daily, "check_panel")
    evening = evening_of(FIRST_2027)

    code, _out, err = _run(world, capsys, as_of=evening, clock=evening)

    assert code == 0, err
    assert Counter(world.market.payloads) == Counter(
        {
            TRADING_CALENDAR_DATASET: 1,
            STOCK_BASIC_DATASET: 1,
            ADJ_FACTOR_DATASET: 1,
            SUSPENSION_DATASET: 1,
            DAILY_DATASET: 1,
            DAILY_BASIC_DATASET: 1,
            PRICE_LIMIT_DATASET: 1,
            "namechange": 1,
            INDEX_DAILY_DATASET: 3,
        }
    )
    assert [(call["session"], call["first_of_year"]) for call in doctor] == [(FIRST_2027, True)]
    assert "namechange" in doctor[0]["datasets"]
    panel = PanelStore(world.runtime / "panel")
    coverage = panel.read_coverage("namechange", 2027)
    assert coverage is not None and coverage.row_count == 0 and coverage.as_of == evening
    assert 2027 in panel.registered_years(INDEX_DAILY_DATASET)
    journal = _journal(world, FIRST_2027)
    assert journal["prediction"]["registered"] is True
    assert journal["prediction"]["scored"] > 0
    assert "result" in journal

    world.market.clear()
    code, out, _err = _run(
        world, capsys, as_of=evening, clock=evening + timedelta(minutes=10), json_output=False
    )
    assert code == 0
    assert REPRINTED in out.splitlines()
    assert world.market.payloads == []

    second = date(2027, 1, 5)
    market.renames = (
        (SECURITIES[5], f"*ST{SECURITIES[5][:6]}", second, second + timedelta(days=1), second),
    )
    _completed(world, capsys, second)
    coverage = panel.read_coverage("namechange", 2027)
    assert coverage is not None and coverage.row_count == 1


def test_a_refused_name_fetch_on_the_first_session_of_a_year_stops_the_day_at_step_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    new_year: Path,
) -> None:
    """A failed fetch is not "verified empty": the day stops at step 2 naming the dataset, the
    journal keeps its pin and no panel stage, no 2027 name partition is written, and the account
    block says no orders were produced."""
    world = _world(
        tmp_path,
        monkeypatch,
        new_year,
        config=survivor_config(epoch=date(2026, 12, 31)),
        market=NamesRefusedNewYear(open_days=NEW_YEAR_DAYS),
    )
    monkeypatch.setattr(cli, "TushareProvider", _without_backoff(cli.TushareProvider))
    evening = evening_of(FIRST_2027)

    code, _out, err = _run(world, capsys, as_of=evening, clock=evening)

    assert code == 1
    assert err[0].startswith("step 2 (panel update) failed: `panel build` exited 4:")
    assert "refused dataset namechange" in err[0]
    assert "panel" not in _journal(world, FIRST_2027)
    assert 2027 not in PanelStore(world.runtime / "panel").registered_years("namechange")
    assert _aftermath(err)[0] == NO_NEW_ORDERS
```

（`TRADING_CALENDAR_DATASET`、`STOCK_BASIC_DATASET`、`ADJ_FACTOR_DATASET`、`SUSPENSION_DATASET`、`DAILY_DATASET`、`DAILY_BASIC_DATASET`、`PRICE_LIMIT_DATASET`、`INDEX_DAILY_DATASET`、`PanelStore`、`compact`、`SECURITIES`、`_calls`、`_without_backoff`、`_aftermath`、`NO_NEW_ORDERS`、`REPRINTED` 本文件都已导入或定义。）

`test_daily_selection_survivor_morning.py`（导入加 `from openalpha_cn.panel.store import PanelStore` 与 `from test_daily_selection_survivor_day import new_year  # noqa: F401  (a module fixture, reused)`）：

```python
@pytest.fixture(scope="module")
def empty_new_year_evening(
    tmp_path_factory: pytest.TempPathFactory,
    new_year: Path,  # noqa: F811
) -> Source:
    """The runtime directory after the evening of 4 January 2027 -- the first session of the
    year, run before 2027's first rename (`V2-P7-021`): the year's name corpus recorded empty, ten
    buys planned for the open of the 5th."""
    base = tmp_path_factory.mktemp("morning-empty-new-year")
    market = sd.EmptyNewYear(open_days=sd.NEW_YEAR_DAYS)
    runtime = base / "runtime"
    shutil.copytree(new_year, runtime)
    with pytest.MonkeyPatch.context() as patch:
        repo, registration = tds._repository(base, sd.survivor_config(epoch=LAST_OF_2026))
        tds._bind(patch, repo, market)
        world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)
        _evening(world, FIRST_OF_2027)
    return Source(repo, registration, runtime)


def test_the_morning_after_a_first_session_before_any_rename_advises_within_its_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    empty_new_year_evening: Source,
) -> None:
    """08:30 on 5 January, 2027 still without a rename: one request for the session's year, the
    year recorded empty again at the morning's pin, and the advice line with its scope -- never a
    refusal, and never more than "no change among announcements dated through the session"."""
    world = _world(
        tmp_path, monkeypatch, empty_new_year_evening, sd.EmptyNewYear(open_days=sd.NEW_YEAR_DAYS)
    )

    code, out, err = _morning(world, capsys, FIRST_OF_2027, json_output=False)

    assert code == 0, err
    assert world.market.payloads == ["namechange"]
    assert [params["start_date"][:4] for params in world.market.asked("namechange")] == ["2027"]
    assert no_change(FIRST_OF_2027) in out.splitlines()
    document = _filed(world, FIRST_OF_2027)
    assert document["requests"] == {"namechange": 1}
    assert document["differences"] == [] and document["notices"] == []
    coverage = PanelStore(world.runtime / "panel").read_coverage("namechange", 2027)
    assert coverage is not None and coverage.row_count == 0
    assert coverage.as_of == morning_of(FIRST_OF_2027)


def test_the_morning_after_a_first_session_sees_the_years_first_rename_published_overnight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    empty_new_year_evening: Source,
) -> None:
    """2027's first rename: an ST name for one of the evening's planned buys, announced on the
    session (4 January), effective the 5th, published overnight. The morning screens it -- dated
    through the session, so advice, not a notice -- and the empty partition gives way to it."""
    market = sd.EmptyNewYear(open_days=sd.NEW_YEAR_DAYS)
    world = _world(tmp_path, monkeypatch, empty_new_year_evening, market)
    named = _bought(world, FIRST_OF_2027)[0]
    second = FIRST_OF_2027 + timedelta(days=1)
    market.renames = ((named, f"*ST{named[:6]}", FIRST_OF_2027, second, second),)

    code, out, err = _morning(world, capsys, FIRST_OF_2027, json_output=False)

    assert code == 0, err
    assert f"  do not place BUY {named}: special_treatment" in out.splitlines()
    document = _filed(world, FIRST_OF_2027)
    assert document["notices"] == []
    coverage = PanelStore(world.runtime / "panel").read_coverage("namechange", 2027)
    assert coverage is not None and coverage.row_count == 1
```

- [ ] **Step 2：运行。** `… pytest tests/unit/scripts/test_daily_selection_survivor_day.py -k "before_its_first_rename or refused_name_fetch" -q` 与 `… pytest tests/unit/scripts/test_daily_selection_survivor_morning.py -k "first_session" -q`。这是一天与清晨两层的**验收测试**，行为由 B3、B4 提供：B4 合入之后它们就该绿（本任务的实现只是一段 docstring）。RED 的证据有两份，都写进报告：§0.1 的实测（今天第 2 步退出 1，`cannot write a 'no_data' batch …`），和 Step 5 的 M1 变异体让这三条变红。`refused_name_fetch` 修复前后都绿（回归钉）。不要为了"看到红"去 revert 或 stash 已提交的 B3/B4。
- [ ] **Step 3：实现**——`scripts/daily_selection.py` 第 98-107 行换成：

```text
Until a year's first rename announcement Tushare serves no `namechange` row for the year (2012's
first is dated 5 January, after the first session on 4 January: the A-phase notes of the P7
step-0 plan). `panel build` records such a year empty -- a partition of no rows observed at the
build's clock -- and every reader takes it as "no rename announced yet" only through the year's
window (`domain.name_history.name_year_may_be_empty`, `V2-P7-021`), so the name corpus no longer
stops a year's first sessions, and the year before's names are still built again after it. A
refused or truncated fetch is still a refusal of step 2, and so is an empty answer for a year
past its window, for a year that already holds renames, or for any year but the build clock's own.
Before `V2-P7-021` such a session was lost: the journal pins the day's `--as-of`, so every retry
asked the same empty window. P6's `--full-update` (`FULL_UPDATE_TARGETS`) takes the same
`panel build`; its `namechange` build is no longer refused in early January.

The registry has the same gap for its own lifecycle year, which `V2-P7-021` does not close
(`V2-P7-025`, a hard gate before 2027-01-04): a year with no listing or delisting yet has no
`stock_basic` partition, step 4 refuses it (`partition_missing`), and a retry meets the same
refusal. The year's first session completes only when the registry already holds an event of
that year.
```

（**corrected 2026-11-02: 出货的模块文档串与上面的草稿不同。**草稿写"所以一年的第一个会话照常运行"，对登记簿是假话——评审（B5）复现了：`EmptyNewYear` 去掉 2027-01-04 的退市后，晚间在同样 11 次请求之后停在第 4 步，`partition_missing … stock_basic year=2027`。出货的版本把那句收窄到**名称语料**（"名称语料不再拦一年的第一个会话"），补了 2012 年的出处，把 `--full-update` 那句限定在它的 `namechange` 构建上，并加上登记簿缺口那一段；那一段先指向本计划的 U3，`V2-P7-025` 立项之后改为指向 `V2-P7-025`。）

- [ ] **Step 4：运行，确认通过**（两个文件的 `-k` 选择；再跑既有的 `-k "first_session_of_a_year or new_year"` 不变绿）。
- [ ] **Step 5：变异探针：** B4 的 M1 在本任务的三条测试上也必须红（一天退出 1；清晨夹具失败）；M2（B3）删掉 `empty_admissible_through`：一天在第 3 步或第 4 步被拒（`stale`），记下是哪一步写进报告。
- [ ] **Step 6：门禁**（含 `ruff format --check scripts/daily_selection.py`、`mypy src scripts`）；提交前 `tests/unit`。
- [ ] **Step 7：提交：** `test(V2-P7-021): the first session of a year before its first rename runs to its evening, and its morning re-check advises`（含 docstring 改动）。

---

## Task B6：年初首晚的记录过迟到记录核查；周报跨年（`V2-P7-021`）

依赖 B5（`EmptyNewYear`）。

**Files:**
- Test: `tests/unit/scripts/test_forward_report_survivor.py`（「the three evenings」一节之后）

**Interfaces：** 只消费：`forward_report.main`（经本文件 `_report`）、`_repository`、`settings_with`、`_daily`、`_document`。

**这一条要回答的问题（实测答案，见 §0.2）：** 在当年分区为空时登记的 1-04 记录——若当年第一条更名公告日在会话之后，核查给 **verified**；若一条公告日不晚于会话、却在晚间读取之后才发布的 ST 更名改变了当天的合格名单，核查给 **UNVERIFIABLE**，`scope = recompute_differs`，`changes = ["namechange:2027 (its rows through 2027-01-04 were restated)"]`——与任何一天晚到发布的 ST 相同（`strategy_survivor_check.py:59-72`），不是新类别。零行分区的溯源普查是 `[]`，之后只加日期在会话之后的行时普查不变（`strategy_survivor_registration.py:1134-1160`）。

- [ ] **Step 1：写失败的测试**（导入加 `from test_daily_selection_survivor_day import new_year  # noqa: F401`）：

```python
# --- the first session of a year, before its first rename (V2-P7-021) --------------------------

NEW_YEAR_REPORT: Final[datetime] = datetime(2027, 1, 6, 10, 0, tzinfo=SHANGHAI)
"""After 5 January's registration cutoff: the records of 4 and 5 January closed."""


@pytest.fixture(scope="module")
def first_session_evening(
    tmp_path_factory: pytest.TempPathFactory,
    new_year: Path,  # noqa: F811
) -> tds.World:
    """The evening of 4 January 2027 -- the first session of the year, before its first rename --
    under a registration whose settings carry the test protocol (epoch 31 December 2026)."""
    root = tmp_path_factory.mktemp("survivor-new-year-records")
    repo, registration = _repository(
        root, survivor_day.survivor_config(epoch=date(2026, 12, 31)), settings=settings_with()
    )
    market = survivor_day.EmptyNewYear(open_days=survivor_day.NEW_YEAR_DAYS)
    runtime = root / "runtime"
    shutil.copytree(new_year, runtime)
    world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)
    with pytest.MonkeyPatch.context() as patch:
        tds._bind(patch, repo, market)
        code, _out, err = _daily(world, survivor_day.FIRST_2027)
        assert code == 0, err
    return world


@pytest.mark.parametrize(
    ("announced", "verdicts"),
    [
        (date(2027, 1, 5), {"verified": 2, "unverifiable": 0}),
        (date(2027, 1, 4), {"verified": 1, "unverifiable": 1}),
    ],
    ids=["first rename after the session", "first rename dated the session, published late"],
)
def test_a_record_filed_on_the_first_session_before_any_rename_is_checked_like_any_other(
    first_session_evening: tds.World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    announced: date,
    verdicts: dict[str, int],
) -> None:
    """The record of 4 January was filed while 2027's corpus was recorded empty. The year's first
    rename -- an ST name for one of that evening's eligible names, effective the 5th -- is
    published on the 5th. Announced on the 5th, it is dated after the session: both records are
    scored again equal and verified. Announced on the 4th, it is a row dated on the session that
    no evening could read, and it moves the session's screen: UNVERIFIABLE, naming the restated
    partition -- the ordinary late-ST case, not a new one. The report reads the calendar from the
    epoch's year, 2026, through 2027."""
    runtime = tmp_path / "runtime"
    shutil.copytree(first_session_evening.runtime, runtime)
    market = survivor_day.EmptyNewYear(open_days=survivor_day.NEW_YEAR_DAYS)
    world = dataclasses.replace(first_session_evening, runtime=runtime, market=market)
    tds._bind(monkeypatch, world.repo, market)
    first, second = survivor_day.FIRST_2027, date(2027, 1, 5)
    named = next(
        order["ts_code"]
        for order in survivor_day._journal(world, first)["result"]["paper"]["open_orders"]
    )
    market.renames = ((named, f"*ST{named[:6]}", announced, second, second),)
    code, _out, err = _daily(world, second)
    assert code == 0, err

    code, _out, err = _report(world, capsys, as_of=NEW_YEAR_REPORT)

    assert code == 0, err
    records = _document(world, NEW_YEAR_REPORT)["records"]
    assert (records["first_session"], records["newest_session"], records["witnessed"]) == (
        "2027-01-04",
        "2027-01-05",
        2,
    )
    assert {key: records["by_verdict"][key] for key in ("verified", "unverifiable")} == verdicts
    if verdicts["unverifiable"]:
        (item,) = records["unverifiable"]
        assert (item["session"], item["scope"]) == ("2027-01-04", "recompute_differs")
        assert item["changes"] == ["namechange:2027 (its rows through 2027-01-04 were restated)"]
```

- [ ] **Step 2：运行。** `… pytest tests/unit/scripts/test_forward_report_survivor.py -k first_session_before_any_rename -q`（约 2 分钟）。在 B4 之前：夹具失败（1-04 晚退出 1）。在 B5 之后：**预期通过**（原型上两例都通过）。若第二例给的是 `refused` 而不是 UNVERIFIABLE：这是一个真实发现（核查没能点名名称普查的变化），停下报告，不要改核查。
- [ ] **Step 3：变异探针：** `src/openalpha_cn/strategy_survivor_registration.py` `_partition` 的普查窗口 `entry.event_date <= last` → `< last`（会话当天的行被排除在普查之外）：第二例应变为 `refused`（没有可点名的变化）——证明测试真的读到了普查。
- [ ] **Step 4：门禁**（`ruff`、`format`；提交前 `tests/unit`）。
- [ ] **Step 5：提交：** `test(V2-P7-021): a record filed on a year's first session before any rename is checked like any other, and the report spans the year`。

---

## 7. 收尾：后续文档任务要改的行（本计划不改）

- **路线图 §P7**：`V2-P7-009`（第 487 行）关闭，写两个 node id：`tests/unit/backtest/test_paper_settlement.py::test_a_name_bought_at_an_open_is_never_sold_in_that_session_on_the_paper_path`、`tests/unit/scripts/test_daily_selection_survivor_day.py::test_a_book_held_into_the_first_session_of_a_year_settles_it_across_an_ex_rights_date`，并写明"两条在已交付代码上通过，无修复"与 C7 的 T+1 结论；`V2-P7-021`（第 499 行）关闭，写 U1 的取值与 B4/B5/B6 的 node id；若 U3 被采纳，加 `V2-P7-025` 一行，总数 252 → 253，P7 24 → 25。（**corrected 2026-11-02: 只有最后一句做了——加了 `V2-P7-025` 一行，总数 253，P7 25。**`V2-P7-009` 与 `V2-P7-021` 两行**都没有关闭**，仍然打开：009 的两条测试已补，要等台账 `OA-BT-021`、`OA-BT-023` 的说明补上；021 的 U1 / U2 还是暂定、要等用户确认，台账行也还没加。两行的状态文字已按此写在路线图里。）
- **规格 §9**：一行记 021 的完成（"经核实为空"的语义、窗口取值、"缺失 / 失败"仍拒绝、需要改的只有一写一读）；一行记 009 关闭；一行记登记簿的新年缺口（C1/U3）。§6 若 U1/U2 是用户决定，按 D 编号记入。
- **运行手册 `docs/runbooks/daily-selection.zh-CN.md`**：「已知局限（幸存者形态）」第 1 条改写为：当年第一条更名之前，第 2 步把当年记为空分区（`EMPTY namechange year=…` 一行在 `panel build` 的输出里），读者在窗口内按"尚无更名"读；失败、截断、窗口之后、已有行之上的空回答仍按名拒绝；**登记簿的同类缺口仍在**（C1/U3）。「清晨复核」的新年一条补"年初首晚之后的清晨也只问会话年，空年照样写空、不拒绝"。P6 一节的局限（`--full-update` 一月初失败）改为已修。
- **能力台账**（`docs/release/openalpha-v1-feature-ledger.md` + `artifacts/openalpha-v1-feature-coverage/features.csv` + `summary.json`，同一提交、用 `build_feature_coverage` 重生成）：新增一行（建议 `OA-PANEL-043`，"一个尚无更名的名称公告年记为空分区并在窗口内读作尚无更名"，`local_source_evidence` = `src/openalpha_cn/panel_ingest.py#write_empty_name_year;src/openalpha_cn/panel_ingest.py#name_history_requirement;src/openalpha_cn/domain/name_history.py#name_year_may_be_empty;src/openalpha_cn/cli.py#_write_name_year`，`acceptance_test` 用不带参数化的 B5 `tests/unit/scripts/test_daily_selection_survivor_day.py::test_the_first_session_of_a_year_before_its_first_rename_runs_to_its_record_and_evening`——参数化 id 里有空格，绑定 node id 时容易出错）；`OA-OPS-039`、`OA-OPS-043`、`OA-OPS-045`、`OA-OPS-046` 的 notes 提一句 021；`OA-BT-021`（T+1 的 node id）与 `OA-BT-023`（跨新年的 node id）的 notes 补 009 的验收。没有新增或移动测试文件，`test_evidence` 列不变。
- `CHANGELOG.md`、`docs/HANDOFF_CURRENT.md` 的 P7 状态行。

## 修订记录

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-11-01 | 立项：B1–B6；§0 的实测与原型；U1–U3；C1–C7 | 计划 3 · B 轨（控制者的 `plan3-common.md` 与 `plan3-trackB-brief.md`） |
