# 前向跟踪周报（V2-P6-012）运行手册

每周运行一次。它把每日选股命令（`scripts/daily_selection.py`，`V2-P6-011`）已登记的预测记录接到**一本连续的账**上，按登记配置自己的持仓数、调仓间隔、缓冲带、行业上限与成本（含卖出）跑一次 `strategy_view.backtest_strategy`，然后用研究网格给保留期用的同一套统计（`scripts/research/grid.strategy_result`）算逐期净超额与符号翻转检验。

措辞保持「结果」而非「预期」：这是已经发生、已经可知的收益，不是对未来的承诺。

## 前提

1. `docs/research/p6-registration.json`（或 `--registration` 指定的文件）已提交，磁盘字节与 `HEAD` 相同；运行的代码（`src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`，以及 `scripts/forward_report.py` 本身）与登记文件的 `code_commit` 一致——校验用的是 `registry.admit_registered_code`，与每日命令绑定 `scripts/daily_selection.py` 的同一个函数，只是把 `scripts/forward_report.py` 换成了 `also_bound` 里的那一个文件。任何一条不满足，退出 1，原因写在标准错误里。
2. 登记文件自身的 `config_id` 与它的 `config` 字段重新算出来的一致（防止登记文件被人手改过一半）；`settings` 里有整数的 `bootstrap_samples` 与 `random_seed`——这两个数从登记文件读，本报告不另外选。
3. 每日选股命令已经跑过至少一天：预测库（`<runtime-dir>/predictions`）里有登记的记录。

## 手动运行

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv run --no-sync --env-file "<主检出>/.env" "$PWD/.venv/bin/python" scripts/forward_report.py --runtime-dir ~/openalpha-research
```

常用选项：

| 选项 | 作用 |
|---|---|
| `--runtime-dir DIR` | 运行目录：面板在 `DIR/panel`，预测库在 `DIR/predictions`，报告写到 `DIR/reports` |
| `--registration PATH` | 登记文件，默认 `docs/research/p6-registration.json` |
| `--repo DIR` | 校验登记与代码绑定所用的 git 检出，默认脚本所在的检出 |
| `--as-of ISO` | 固定报告的时钟（默认现在） |
| `--json` | 以 JSON 打印本次报告 |

## 一条记录何时不进这本账，以及为什么从不悄悄丢弃

每条登记记录先被判定一次，不能进账的都在报告的 `excluded` 里列出并写明原因：

1. **登记晚于登记截止时刻**（`declares_a_different_horizon_or_model_than_the_registration` 之外的另一条独立规则）：账把一天的分数在下一交易日开盘成交，开盘价由 09:15 起的集合竞价定；一条在那一刻或之后才登记的记录（晚跑、补跑过去的日子）是事后诸葛亮，不论 `PredictionRecord.standing` 对更晚的「结果可知」时刻怎么说。判定函数与每日命令登记时用的是同一个（`strategy_registration.registration_cutoff`）。
2. **结果还不可知**（`outcome_not_yet_knowable`）：这条记录自己的观测窗口在报告的 `--as-of` 时还没关闭。一条记录可能登记得很及时、但结果还没到——两把尺子量的是两件事。
3. **不是这份登记的记录**（`declares_a_different_horizon_or_model_than_the_registration`）：预测库若被另一份登记共用，声明着别的 `feature_version`/家族/周期的记录不算这份登记的证据。

## 输出怎么读

- **periods**：连续账本自己的逐期结果——起止交易日、净收益、成本、毛收益、换手、当期是否成交、持仓。这些数字全部来自 `run_strategy_backtest`（`V2-P6-007`），本报告不重算，也不拆成互相独立的单笔交易。
- **excluded**：被排除的记录，逐条给出原因与判定时用的具体时刻。
- **statistics**：按 `000905.SH` 与全 A 等权两个基准分别给出：累计净超额（逐期净超额的直接相加——账本连续复利，逐期收益已经把复利算过一次，相加不会重复计息）、符号翻转检验的双边 p 值与单边换算（均值为正取 `p/2`，为负取 `1-p/2`）、样本是穷举还是抽样、以及穷举的模式数或抽样的随机种子。

报告同时写入 `<runtime-dir>/reports/forward-<报告日期>.json`。
