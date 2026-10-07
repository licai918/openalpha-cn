# 前向跟踪周报（V2-P6-012）运行手册

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
