# 每日选股一条命令（V2-P6-011）运行手册

每个交易日收盘后运行一次。它按**已登记的研究配置**（保留期跑过的同一个登记文件）做八件事：更新面板、体检、构建当日因子截面、出当日候选榜、出目标权重、把当日打分登记为预测、打印摘要、写当日日志。

措辞保持「候选」。这条命令不下单，不给出任何预期收益；候选榜是否有价值，只由预登记的保留期结论和之后的前向跟踪（`V2-P6-012`）说明。

## 前提

1. `docs/research/p6-registration.json` 已由 `scripts/research/registry.py` 的 `register()` 写出并**提交**，磁盘上的字节与 `HEAD` 相同。
2. 当前检出的受约束代码（`src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`，以及 `scripts/daily_selection.py` 本身）与登记文件里的 `code_commit` 一致，且工作区无未提交改动。`openalpha_cn` 必须从本仓库的 `src/` 导入。任何一条不满足，命令在第 1 步拒绝，退出码 3。
3. 运行目录的面板已回填到当年，因子在训练/滚动窗口内的历史时点已构建（滚动 IC 与 walk-forward 需要窗口内每天的截面；本命令只构建当天）。
4. `.env` 里有 `TUSHARE_TOKEN`。命令本身从不读取它：`panel build` 在 `TushareProvider` 内部解析凭据，本命令只给传输层套了一个按 `api_name` 计数的外壳。

## 手动运行

在仓库根目录：

```bash
uv run --no-sync --env-file .env python scripts/daily_selection.py --runtime-dir ~/openalpha-research
```

常用选项：

| 选项 | 作用 |
|---|---|
| `--registration PATH` | 登记文件，默认 `docs/research/p6-registration.json` |
| `--as-of ISO` | 固定当天的时钟（默认现在）；该时刻已发布的最新交易日就是「当天」 |
| `--dataset T`（可重复） | 只更新这些面板目标；默认 `DAILY_TARGETS`，配置读行业时再加 `index_classify`、`index_member_all` |
| `--max-staleness-days N` | 因子构建的新鲜度上限，默认 30 |
| `--top N` | 摘要打印前 N 名候选，默认持仓数 |
| `--json` | 以 JSON 打印当天结果 |
| `--launchd-plist LOG_DIR` | 只打印 launchd 定时配置文本，不安装任何东西 |

## 八步与失败时的行为

| 步 | 做什么 | 失败时 |
|---|---|---|
| 1 registration | 读登记文件；用 `registry.admit_registered_code` 校验提交状态与代码绑定 | 文件缺失或不可读：退出 2；代码不是登记的代码：退出 3 |
| 2 panel update | 对最新已收盘交易日所在年份跑 `panel build --incremental`，全部目标一次调用、同一个 `--as-of` | 退出 1，并转述 `panel build` 自己的拒绝理由 |
| 3 panel doctor | `panel doctor` 与依赖门 `data-check`，数据集为本次更新写的数据集（不含按上市年份分区的 `stock_basic` 和行业目标），带上当天 `--session`，指数数据集带三个指数代码 | 不 clean 就停，第 4–7 步都不执行 |
| 4 factor build | 配置读到的每个因子档位，在当天 16:30（上海）信号时点构建；该时点已有构建的档位跳过 | 退出 1，转述构建拒绝 |
| 5 candidates | `strategy_view.score_day`：回测自己的读路径与打分器给当天打分排序 | 退出 1 |
| 6 target weights | 回测自己的调仓规则 `strategy_backtest.target_holdings`，上一交易日的目标持仓视为账面 | —— |
| 7 prediction | 当天打分登记进预测库，必须早于下一交易日 09:30 开盘 | 已过下一交易日开盘（晚跑、补跑过去的日子）则拒绝登记，什么也不写：回测在下一开盘成交，从那时起结果已部分揭晓 |
| 8 summary | 打印摘要并写当日日志 | —— |

任何一步失败，标准错误输出里写明「step N (名称) failed」和原因，并给出本次运行到停下时已发出的 Tushare 请求数（失败也花请求），之后的步骤不执行。

当天的 `as_of` 在第 2 步开始前就写进日志：第 2 步中途失败后重跑，问的是同一个问题，已写成的分区按字节相同，`PanelStore` 不会重写。

## 输出怎么读

摘要依次给出：

- **session**：当天交易日与固定的 `as_of`。
- **candidate list**：候选榜 ID（`dsl_` 加 24 位十六进制，是当天榜单内容的摘要），以及前 N 名与各自的组合分。榜单截到缓冲带（`buffer_rank`，无缓冲带时为持仓数）。
- **target weights**：`rebalanced`（调仓日）、`not a rebalance day`（非调仓日，沿用上一日目标）或 `held`（打分源当天没有答案，沿用上一日目标）；每只 `1 / holding_count`，其余为现金；`turnover` 是与上一日目标权重的单边换手。
- **prediction**：预测记录 ID（`prd_…`）、`standing`（应为 `forward`）与结果可知时刻。
- **tushare requests**：本次运行实际发出的请求数，按接口分列。

调仓日按登记配置的 `start` 起算，每 `rebalance_every_sessions` 个交易日一次，与回测一致；第一次运行（还没有上一日目标）总是建仓。walk-forward 模型的重训日也从 `start` 起算，当天使用的是最近一个重训日训练的模型，与从 `start` 回测到当天时回测使用的模型相同。

## 为什么不是 `shortlist run` 和 `portfolio construct`

计划原文写的是这两条命令，但它们实现的不是登记的策略：

- `shortlist run` 按已存档位值的加权和排序；策略是对每个成分在「所有成分都有值的证券」上标准化（`zscore_sum` 或 `rank_sum`）后再求和。它拒绝中性化档，也表达不了滚动 IC 加权和 walk-forward 模型，且没有研究证据时会被研究比例门拒绝。
- `portfolio construct` 按名次分档给权重，单票上限 25%、总敞口 80%，没有缓冲带；策略持有 `holding_count` 只、等资金、排名在缓冲带内的持仓保留、可按行业限只数。

所以候选榜就是策略自己的排序，目标权重就是策略自己的调仓规则，两者都通过 `run_strategy_backtest` 使用的同一组函数得到。测试 `tests/unit/scripts/test_daily_selection.py` 对静态、滚动 IC、walk-forward 三种打分源分别证明：读取登记记录的回测与登记配置本身的回测逐期成交、持仓、收益完全相同。

## 登记的预测是什么

- **walk-forward 模型**：就是当天在用模型给当天截面打的那一批分（回测把它们作为打分行），重新盖上登记时刻。
- **静态权重、滚动 IC**：没有拟合的模型，登记的批次声明的是组合分本身：`family` 为 `strategy_static` 或 `strategy_trailing_ic`，`feature_version` 为登记配置的 `config_id`，`seed`、`code_commit` 取自登记文件，超参数写明组合方式与登记文件摘要。制品里的「测量字段」来自当天的输入而不是手填：`feature_ids` 是成分键，`parameters` 是当天使用的权重，`training_cutoff` 是当天读到的任一打分行或计入的 IC 最晚变为可知的时刻，`training_example_count` 是这些行和 IC 的个数。每只被排序的证券带组合分；只有部分成分有值的证券以 `ABSTAIN_INCOMPLETE_FEATURES` 弃权。

同一天、同一声明下已经登记过记录时：分数相同则复用，不再写；分数不同则拒绝（已登记的记录有效，不允许第二个答案）。

## 幂等

当日日志：`<runtime-dir>/daily_selection/<config_id 前 16 位>/<交易日>.json`。

- 当日日志完整：直接再打印一次摘要，不发请求、不写任何东西。
- 只记录了第 2 步：从第 3 步继续，沿用日志里的 `as_of`。
- 其余各步自身幂等：内容相同的分区不会重写（`PanelStore`），当时点已构建的因子档位跳过，当天已登记的预测复用。

## 请求预算

默认目标一天约 93 次请求（`V2-P6-003` 实测：逐日目标约 22 次，财报横扫每天约 71 次）。配置读行业（行业上限或中性化档）时，`index_member_all` 每次再加 62 次。重跑同一天：0 次。

## 定时（需要你同意后才安装）

launchd 不知道交易日历，所以配置为每个工作日 18:30 触发，由命令自己判断：节假日里「最新已收盘交易日」的日志已经完整，于是只打印摘要，零请求、零写入。

生成配置文本（只打印，不安装）：

```bash
uv run --no-sync python scripts/daily_selection.py --runtime-dir ~/openalpha-research --launchd-plist ~/openalpha-research/logs
```

把输出保存为 `~/Library/LaunchAgents/com.openalpha.daily-selection.plist` 并用 `launchctl` 加载，是安装常驻配置，只在你明确同意后做。不同意就保留手动命令，产物完全相同。

## 已知局限

1. 错过的交易日不能补登记预测：下一交易日一开盘，那天打分的结果就开始揭晓，命令拒绝登记。当天的目标权重仍可通过 `--as-of` 补算，但第 7 步会失败并说明原因。
2. 预测在收盘后（约 18:30）登记，晚于回测使用的 16:30 信号时点。回测的前视守卫以 16:30 为界，所以前向报告（`V2-P6-012`）读取这些记录时需要按「T 日收盘后登记、T+1 开盘成交」的口径处理，而不是直接把记录当作 T 日 16:30 的打分行。
3. 目标权重是等权 `1 / holding_count` 的决策，不是成交后的持仓：回测里受参与率上限、涨跌停、停牌影响而没成交的单，这里不模拟；上一交易日的「账面」取的是上一日目标，而不是实际成交结果。
4. 中性化档的因子构建依赖行业分类的年份覆盖（`V2-P6-015`）；在它合入之前，读中性化档的配置可能在第 4 步被按名拒绝。
5. **上游撤回已存会话的行时，第 2 步会停。** `--incremental` 会重新拉取已存的最后一个会话（一个会话的重叠）。2026-09-28 实测：Tushare 对 2026-08-28 的 `stk_limit` 已不再返回三只基金（`158008.SZ`、`159096.SZ`、`561730.SH`，此前存下时各只有这一天一行），写入端的丢弃守卫按设计拒绝（更窄的截面被当作不完整的拉取），命令停在第 2 步并说明。这需要面板层的处理（`V2-P6-003` 的后续），不是这条命令能替面板决定的。
