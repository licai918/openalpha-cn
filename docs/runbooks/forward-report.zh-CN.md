# 前向跟踪周报（V2-P6-012）运行手册

> **2026-10-08 起暂停（`V2-P7`，决策 D4）**：每日登记已暂停，现有登记只有两条前向预测（2026-09-30、
> 2026-10-08），周报没有可报告的内容。新配置预登记后随每日登记一起恢复。首份周报
> （`reports/forward-2026-10-08.json`，1 个不完整的期，未检验）保留为机制已跑通的证据。

**两种登记形态。** 登记的 `config` 没有 `kind` 键时是 P6 的组合（`V2-P6-012`），下文从这里到「输出怎么读」都说它；写明 `kind: "survivor_book/v1"` 时，同一条命令在准入之后分派到幸存者周报（`V2-P7-018`），见文末「幸存者登记的周报」。两者共用「前提」1–3 与「手动运行」，输出文件名不同、互不覆盖。

每周运行一次。它把每日选股命令（`scripts/daily_selection.py`，`V2-P6-011`）实际调仓过的那些交易日接到**一本连续的账**上——调仓日与每天用的记录由追加写入的预测库自己见证（`daily_selection.forward_rebalances`），日志只用来交叉核对，不是权威来源——按登记配置自己的持仓数、调仓间隔、缓冲带、行业上限与成本（含卖出）跑一次 `strategy_view.backtest_strategy`，然后渲染 `daily_selection.forward_summary` 对这份证据的唯一解读。

本报告是 `V2-P6-011` round 9-12 落地的前向证据机制上的一层薄封装：它自己不再判定「哪些日子该进账」「一条晚登记的记录能不能信」「登记后被订正意味着什么」——这些全部来自被复用的函数，本文件不保留第二套判定逻辑。

措辞保持「结果」而非「预期」：这是已经发生、已经可知的收益，不是对未来的承诺。

## 前提

1. `docs/research/p6-registration.json`（或 `--registration` 指定的文件）已提交，磁盘字节与 `HEAD` 相同；运行的代码（`src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`）与登记文件的 `code_commit` 一致，`scripts/daily_selection.py` 和 `scripts/forward_report.py` 这两个文件也一并绑定——校验用的是 `registry.admit_registered_code`，与每日命令同一个函数，只是 `also_bound` 里换成了这两个脚本文件（前向报告的正确性和每日命令的正确性一样，都是这次登记的一部分）。任何一条不满足，退出 1，原因写在标准错误里。
2. 登记文件自身的 `config_id` 与它的 `config` 字段重新算出来的一致（防止登记文件被人手改过一半）；`settings.random_seed`（若存在）从登记文件读,本报告不另外选。
3. 运行的解释器与已安装的包，和登记文件的 `.python-version`/`uv.lock` 一致（`daily_selection.admit_environment`，每日命令用的同一个检查）。
4. 每日选股命令已经跑过至少一天，且至少完成过一次调仓：预测库（`<runtime-dir>/predictions`）里有这份登记绑定的记录，日志（`<runtime-dir>` 下每日命令自己的日志目录）里有对应的调仓日。
5. 首个调仓日之后至少还要有一个交易日，且面板已发布到那一天：账本最早从首个调仓日的下一个交易日开盘才开始持有，面板已发布的最新交易日不晚于首个调仓日时没有可以交易的一天，报告以 `ForwardReportError`（信息里写着 `is not after the first rebalance day`）退出 1。2026-09-30 是首个调仓日，紧接着国庆休市，这期间运行本报告就是这种情形；节后第一个交易日的数据入库后再跑即可，不是故障。

## 手动运行

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv run --no-sync --env-file .env "$PWD/.venv/bin/python" scripts/forward_report.py --runtime-dir ~/openalpha-research
```

在每日命令的那个钉住的 worktree 里运行；`.env` 是 `--pin-worktree` 在那里建的、指向主检出 `.env` 的符号链接。不要把主检出的绝对路径传给 `--env-file`：路径含空格，uv 会按空格拆分它（见 `daily-selection.zh-CN.md` 的「手动运行」）。

常用选项：

| 选项 | 作用 |
|---|---|
| `--runtime-dir DIR` | 运行目录：面板在 `DIR/panel`，预测库在 `DIR/predictions`，报告写到 `DIR/reports` |
| `--registration PATH` | 登记文件，默认 `docs/research/p6-registration.json` |
| `--repo DIR` | 校验登记与代码绑定所用的 git 检出，默认脚本所在的检出 |
| `--as-of ISO` | 固定报告的时钟（默认现在） |
| `--json` | 以 JSON 打印本次报告 |

## 账本怎么定出来的：起点、每一天、终点

- **哪些日子调仓、每天用哪条记录**：`daily_selection.forward_rebalances` 从预测库的第一条被这份登记绑定的、准时的记录开始往后数——不是日志说了算,也不是这份登记第一次被信号覆盖的那天。日志只用来交叉核对；日志与库不一致时,报告直接拒绝（`ForwardReportError`）,不会悄悄选一边。
- **一条晚于信号时刻才登记的记录能不能读**：`daily_selection.forward_record_check`（`strategy_registration.RecordCheck`),作为 `backtest_strategy(verify_late=...)` 传入——先看它是否绑定这份登记（`record_is_bound`：组合模型比对声明本身；走前向模型比对它自己写的输入溯源),再在它自己登记的那一刻从已存的构建重新算一遍分数。算出来一样就正常计入;算出来不一样、但它读过的某个输入后来被订正过,记作 `unverifiable_inputs_corrected_after_filing`——照样计入账本（推荐了就是推荐了）,只是在汇总里单独标出、单独统计;算出来不一样又没有输入被订正的解释,直接拒绝整份报告。
- **账本对照的基准（V2-P6-024）**：账本同时计价登记里记下的 `excess_benchmark`（现行协议是 `equal_weight_all_a_held`，全 A 等权同期持有；之前的登记记的是 `equal_weight_all_a`，仍按它读）。登记的 settings 里没有 `excess_benchmark` 就直接拒绝，不拿协议默认值顶替。`equal_weight_all_a_held` 由回测自己按期计算：一个无摩擦的名义账本，每期在执行日（信号日的下一交易日）开盘卖出上期全部成员、等额买入本期成员——信号日有完整报价（日线、已发布的涨跌停价、复权因子）且执行日开盘按账本自己的规则能买进的全部股票。
- **执行日的复权因子或涨跌停价还没入库，整份报告会被拒绝**：执行日没有 `adj_factor`（或没有 `stk_limit`）时，那一天没有任何股票有报价，这一期的基准一个成员都没有；回测不把它当成 0，而是拒绝（`ForwardReportError`，信息里写着 `equal_weight_all_a_held has no member for the period starting …`）。处理：等当天的 `adj_factor`、`stk_limit` 发布后，按每日运行手册补建这两个数据集（`openalpha panel build --dataset adj_factor --year <年份>`、`openalpha panel build --dataset stk_limit --year <年份>`，或整套每日构建），再重跑本报告；不要为了出报告把基准从登记里拿掉。
- **账本的终点**：最新一次调仓那条记录的 `book_period_end`（登记的调仓间隔,下一交易日起才算收益、再往后数一个调仓周期),再取面板已发布的最新交易日与它的较小值——面板还没发布到的日子不会出现在账本里。

## 输出怎么读

`forward_report_view`/`summary_lines` 渲染的就是 `daily_selection.forward_summary` 自己的字段,本文件不重新计算、也不改名:

- **`unverifiable_inputs_corrected_after_filing`**：`count` 与逐条 `record_id`/`corrected`（它读过的、后来被订正的分区名)。这些记录仍然算在账本里,这里只是标出来。
- **`statistics.all_periods`**：账本每一期都算在内的统计——期数、复利算出的 `compounded_net_return`（`∏(1+net_return_i) - 1`,不是逐期相加)、`mean_period_net_return`、按基准复利的 `compounded_benchmark_returns`。这是报告的头条数字。
- **`statistics.excluding_unverifiable`**：同一本账,去掉被标记记录开的那些期——同一条路径的子集,不是重新跑一遍（后面几期仍然沿用它们实际发生时的持仓与成本）,用来看订正是否真的影响了结论。
- **`unprovable_holds`**：第一条记录之前、日志说「空仓」的那些日子——没有记录,无法从库里证明,只是列出来,不计入统计。
- **`integrity`**：固定的威胁模型说明（`daily_selection.INTEGRITY`）——这些检查防的是正常运行下的误差、程序缺陷、意外损坏与上游数据订正,不防有权限直接改本地磁盘的人蓄意篡改;那需要外部的、仅可追加的见证,这份报告没有。

**头条那一行的标签（`V2-P6-030`）**：打印输出里 `statistics.all_periods` 那一行的标签现在是 `headline: the candidate book as listed`——当日列出的候选组合按原样计价，是候选，不是推荐。钉在登记提交 `aebaac5` 上的 worktree 运行的是登记时的代码，仍然打印旧标签 `headline: the book as recommended`；它指的同样是当日的候选组合，不是推荐。数字与 JSON 字段都没有变，下一次登记后，钉住的 worktree 随代码一起换成新标签。

报告同时写入 `<runtime-dir>/reports/forward-<报告日期>.json`。

## 幸存者登记的周报（`V2-P7-018`）

> **状态：未启用。** 幸存者配置在 `V2-P7-006` 由用户确认之后才登记，在那之前没有任何幸存者记录，也不要运行本节的命令。登记里的协议块 `survivor_forward_protocol` 也在那里冻结；下面说的窗口、期限、指标都读自它，报告不替它选缺省值。

登记的配置写明 `kind: "survivor_book/v1"` 时，`scripts/forward_report.py` 在准入之后立即分派到幸存者周报；没有 `kind` 的登记（P6 的组合）照旧走上文的报告，两者的输出文件名不同、互不覆盖。命令行相同，只是 `--registration` 指向幸存者登记：

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv run --no-sync --env-file .env "$PWD/.venv/bin/python" scripts/forward_report.py --runtime-dir ~/openalpha-research --registration <幸存者登记文件>
```

准入与 P6 相同（`also_bound` 里加上 `scripts/forward_report.py`：之后改动这个文件，登记的报告就作废）；准入或读幸存者块失败时，报告以 `ForwardReportError: …` 退出 1（不是每日命令的 2 或 3）。

**什么时候跑。** 每周一次，周末或开盘前最好。**不要在每日晚间命令运行期间跑**（约 18:30 起，到它打印完摘要为止）。报告不拿纸面账簿的锁；它按晚间命令写入的相反顺序读——先列出纸面账簿的快照（这一步定下账簿的「头」），再读每日日志，最后扫预测库——所以晚间命令中途写入的东西不会让报告自相矛盾，也不会让报告被误拒。仍剩一个窗口：某一晚的快照已经写入、它的日志还没写 `result` 的那几秒里，报告会把这一天列进 `paper_ledger.coverage.refused_days`（看起来像一天被拒；磁盘上，一个在快照之后被拒的日子看起来也一样，两者分不开）。晚间命令跑完之后用同一个 `--as-of` 重跑即可，不是故障。日志目录里只读 `YYYY-MM-DD.json` 这样名字的文件，清晨复核的 `<交易日>.morning.json` 不读。

**`--as-of` 是什么意思。** 报告的时钟（默认现在）；报告只读那一刻已经存在的东西：

1. 只计入在这一刻之前登记的记录（`registered_at <= as_of`，即记录批次与预测库盖章两者中较晚的那个）；
2. 每日日志按那一刻的样子读：钉住时刻（日志顶层的 `as_of`）晚于它的日志整份不读；日志的 `prediction` 所指的记录登记晚于它时，`prediction`、`paper`、`result` 三个阶段不读；
3. 纸面账簿的头是那一刻已经提交了预测或结果的最新一晚，快照、账本行与日志都只读到这个头；
4. 标签只给退出日 16:30 不晚于 `as_of` 的记录（`label_as_of`）；还没成熟的只计数，不读标签。

限制：面板的读取按整个分区判断。`--as-of` 早于面板里最新一个交易日的 16:30 时（例如晚间命令已经把今天的数据入库，却把报告钉在今天下午之前），标签与中证 500 读不出来，报告按名拒绝（退出 1，信息里有 `not_yet_knowable`），什么也不写。`--as-of` 只能把报告钉在面板已经到达的地方，不能回看面板更新之前的样子。

**退出码。**

- **0**：写出 `<runtime-dir>/reports/forward-survivor-<报告日期>.json`，打印摘要，最后两行是 `this run: <n> records checked, <m> verdicts served from the verdict cache` 与 `written to <文件>`；`--json` 时只打印文档本身，外加一个 `this_run` 键（`{"checked": n, "served_from_cache": m}`）。
- **1**：原因写在标准错误里，什么也不写。大多数写作 `SurvivorReportError: the survivor forward report is refused: …`，少数直接写 `SurvivorReportError: …`（日历、日志、中证 500 的拒绝），准入阶段的是 `ForwardReportError: …`。常见原因：
  - 登记的 settings 里没有 `survivor_forward_protocol` 块，或者它读不通（协议在 `V2-P7-006` 冻结，报告不替它选缺省值）；
  - 同一个交易日有两条绑定且准时的记录（`<交易日> carries 2 records …`，报告不在两者之间选）；
  - 某天的日志说它登记了一条预测库里看不到的记录，或反过来（`the store is the witness`）；
  - 某份日志是另一份登记写的（`was written under another registration`），或没有带时区的钉住时刻；
  - 报告所在年份的交易日历还没建（例如一月初跑报告，`calendar of <年> is not built`：先让每日命令的面板更新建好新一年的日历，`the daily command's panel update builds it`）；
  - 本登记的某个溯源文件读不出来，或两份溯源指向同一次登记——整份报告被拒，因为读不出的溯源可能属于任何一条记录；
  - 核查被问了一个它答不了的问题（`SurvivorCheckArgumentError`）、会话序号读不出（`SessionOrdinalError`）、预测库里的记录读不回来（`PredictionStoreError`）；
  - 标签读取器读不出面板（`ModelPanelUnreadableError`、`ModelRunBlockedError`，例如某个分区不在，或上面说的 `not_yet_knowable`）；
  - 中证 500 的点位读不出来（面板拦截、点位行读不出、批次规则拒绝）。
- **2**：命令行参数错误（argparse，例如缺 `--runtime-dir`）。
- 面板目录锁被别的进程占用（`PanelCatalogBusyError`）时按设计不捕获，以回溯结束（退出 1）：等占用者结束后重跑。

**纸面账簿那一节被拒（退出码仍是 0）。** 纸面账簿只是记录层，不是证据；它读不出来时，只有 `paper_ledger` 与 `relative` 两节写着 `refused` 和原因，预测层照常输出、数字不受影响。会这样的情形：还没有哪一晚提交过预测或结果（账簿还没有可报告的记录）；某个快照读不出来（不是 UTF-8、解析失败、内容与地址不符）；账本里某一行读不出来（未知的 schema 版本、模型拒绝、不是 JSON）或账本表本身读不出来；某份日志点名的快照与账簿里的不一致（`JournalSnapshotMismatchError`）。修好之后重跑即可。

**怎么读 `forward-survivor-<报告日期>.json`。** `kind` 为 `survivor_forward_report/v1`，每一节都带 `wording`（已登记研究配置的候选，没有下单，账面权益不是证据）；文档末尾是 `wording`（一个列表，含 `no verdict before the pre-registered window`）与 `integrity`（威胁模型说明，与 P6 报告相同）。

- `protocol`：登记里的协议块原样，加上 `periods_matured`（主期限的窗口位置中已成熟的个数）与 `window_satisfied`。
- `records`：`witnessed`（见证的记录数）、`first_session`/`newest_session`、`through`（报告覆盖到的交易日）、`missing_sessions`（应有记录却没有的交易日）；`checked`；晚到记录核查的结论计数 `by_verdict`（`verified`、`unverifiable`、`refused`，以及主期限预测层里没核查的 `unchecked`）与按范围的计数 `by_scope`（`not_bound`、`no_provenance`、`recompute_differs`、`recompute_failed`）；每条 UNVERIFIABLE 列出变化（`unverifiable`），每条 refused 列出原因（`refused`）；`shares` 给出全部记录（`overall`）与样本内（`in_sample`）的 UNVERIFIABLE、refused 占比；`headline_basis` 写明头条含 verified 与 UNVERIFIABLE、敏感性去掉 UNVERIFIABLE、refused 两边都不含。**没有 `cached` 键**：多少条结论取自核查缓存只在 `this run` 一行与 `--json` 的 `this_run` 里，不写进文件，所以同一个 `--as-of` 重跑写出的字节相同。
- `prediction_layer`：唯一的证据层，带 `forecast_wording`（已登记研究配置的候选预测，不是业绩声明）。`h<主期限>`、`h<次期限>` 各一节：`witnessed`、`mature`、`immature`、`label_as_of`、按原因与按十分位统计的无标签名字（`unlabelled_by_code`、`unlabelled_by_decile`，附 `unlabelled_wording` 说明偏差方向）、描述性均值（`descriptive`，标着 `descriptive, not a test`，没有标准误），以及不重叠样本 `non_overlapping`：`anchor_ordinal`、`sampled`、`gaps`、`pending`、`values`、`window_positions`（这个期限的窗口位置数 K_h）、`window_span_sessions`（窗口跨的交易日数，所有期限相同）、`window_sessions`、`in_window`（窗口位置按 `periods`/`pending`/`no_record`/`refused`/`unchecked`/`unverifiable` 计数）、`matured`、`window_satisfied`、`refused_in_sample`、`test`（窗口满足之前为 `null`）与 `status`。指标里 `ew_minus_worst_decile` 是**等权减最差十分位**（每个被检验的指标都朝同一方向：大于 0 支持筛选）。`excluding_unverifiable` 是同一层去掉 UNVERIFIABLE 记录后的敏感性，检验的是同样的窗口位置。登记的窗口满足之前没有任何 p 值，也没有任何结论用语。`secondary_family` 在主窗口满足之后才对次要假设做 Benjamini-Yekutieli 校正，每项给 `q_value` 与 `below_q`（打印为 `q-value <= q`：按 q 的逐步判定，不是结论）；次要结果从不改变「候选」的措辞。
- `paper_ledger`：纸面账簿的记录——`coverage`（晚间、只结算日、补结算的交易日、被拒的日子 `refused_days`）、`equity`、`holdings`（低于持仓数 N 的天数按登记的 N 算）、`turnover`、`costs`（分项成本与 `fees_paid` 的核对）、`write_offs`（核销及其 D16「按上一次估值回收」的敏感性）、`attempts`、`decisions`、`illiquid_exits`、`reconciliation`。只是描述，不是检验；金额是账本单位。
- `relative`：账簿在主期限样本窗口上的收益，对照投资域等权与中证 500。账簿扣了协议的成本、含现金，在 D+1 开盘成交；基准是同样两个收盘之间的毛收益、买入持有——两边差一腿时间。缺快照的窗口 `status` 为 `unavailable`，断开的地方列在 `gaps`，缺基准的期列在 `windows_left_out`。
- `cannot_be_evaluated_yet`：仍不能评估的事项，`{"wording", "items": [{"code", "statement"}]}`，每份报告都列（例如 `no_survivor_record_yet`、`first_outcome_not_matured`、`window_not_satisfied`、`paper_equity_is_not_evidence`、`unlabelled_names_left_out`、`no_null_signal_control`）。
- 本次运行核查了多少条记录、多少条直接取自核查缓存（`reports/survivor_record_verdicts/`，只是缓存，不是证据），只打印在 `this run` 一行（`--json` 时为 `this_run`），不写进文件。

（这一节以 `V2-P7-018` E-FIX-B 之后的代码为准，相对最初的草稿改了几处：缓存计数从文档的 `records.cached` 移到了 `this_run`；最差十分位的指标由「最差十分位减等权」改为 `ew_minus_worst_decile`，方向翻转；窗口由 `window_periods` 计数改为日历位置 `window_positions`/`window_span_sessions`/`in_window`；次要家族的 `rejected` 改名 `below_q`；`cannot_be_evaluated_yet` 改为 `{wording, items}`。）
