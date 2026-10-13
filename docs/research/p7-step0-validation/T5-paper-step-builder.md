# T5：纸面账本的步骤能否由现有行情组装（`PortfolioBacktestStep` 构建器）

- **任务**：P7 Step 0 技术验证 T5（设计文档 `docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md` §10 第 T5 行；§4.2 第 5–6 步；§7）
- **issue**：`V2-P7-005`（领域内核）、`V2-P7-008`（交易清单与订单生命周期）、`V2-P7-009`（纸面账本每日推进）
- **代码基线**：`1d7b4a3`（本 worktree）；订单契约参考 `~/openalpha-research/diagnostics/p7/step0-core-reference/src/openalpha_cn/domain/survivor_orders.py`、`survivor_lifecycle.py`（只读，未复制入库）
- **方法**：读代码 + 一个手写 3 日夹具的一次性原型（`~/openalpha-research/diagnostics/p7/t5_paper_step_prototype.py`，不入库）+ 研究期（2017-01-01 … 2021-10-31）面板只读计数（`~/openalpha-research/diagnostics/p7/t5_panel_measure.py`）
- **性质**：工程验证。本文任何结论都不涉及收益或信号有效性。

---

## 0. 结论摘要

1. **不能"直接"复用。** 现有行情组装（`strategy_view._PanelDays.quote`，`src/openalpha_cn/strategy_view.py:1994-2028`）产出的 `MarketBar` 可以原样放进 `PortfolioBacktestStep`，字段上不缺；但 `PaperPortfolio` 推进的语义与 Step 0 的要求有三处不符：**按收盘价成交**（`backtest/execution.py:317,337`），而设计要求开盘成交；**不处理除权除息**（`paper.py`、`multi_day.py`、`portfolio.py` 中没有任何复权概念）；**成本不可注入**（`PaperPortfolio.__init__` 只收 `ledger`、`limits`，`paper.py:343-350`；`PortfolioBacktestRunner` 构造 `PortfolioSimulator(limits=limits)`，`multi_day.py:205`，执行策略恒为默认 `CostSchedule()`）。另有一处会让整日推进失败的形态：**全天停牌没有日线行**，给它下的单使 `PortfolioBacktestStep` 构造直接报错（`multi_day.py:119-121`）。
2. **`PaperPortfolio` 对不能成交的订单一律"整单拒绝并记账"，不部分成交，也不静默跳过**；它不知道顺延、过期、取消。顺延 / 过期 / 取消只能在订单生命周期（参考实现 `advance_orders`）与适配器里实现。顺延的订单若沿用原 `order_id` 重发，账本的冲突守卫会让整个 `advance` 抛错（原型 3(c)）。
3. **`benchmark_close` 对纸面账本没有任何作用**：`PaperSessionResult` 不含它（`paper.py:308-323, 377-387`），账本也不存它。Step 0 的基准对比（§8、`V2-P7-003`）必须在账本之外计算；`benchmark_close` 建议填中证 500（`000905.SH`）当日收盘，研究期 1,172 个交易日全覆盖。
4. **建议**：复用 `PaperPortfolio` / `PortfolioSimulator` / `AShareExecutionPolicy` 的全部规则；**抽共用**两处（把 `_PanelDays` 与 `StrategyRequest` 解耦后共用；把 `strategy_backtest._at_the_open` 公开后共用），不复制；**新增**一个薄的纯函数步骤构建器（`backtest/` 下，邻近 `paper.py`），并给 `PaperPortfolio` / `PortfolioBacktestRunner` 加一个**非破坏性**的可选 `execution` 参数。详见 §6–§7。
5. **对方案 A / B / C 无实质影响**（§8）；但有四条**对 Step 0 设计本身**的发现需要回写（§9），其中 §4.2 的步骤顺序与除权除息两条会改变实施计划。

---

## 1. 读到的事实

### 1.1 `PaperPortfolio.advance` 做什么

- 只做两条检查：会话日期必须严格晚于 `state.as_of`，且不晚于调用方声明的 `observed_on`（`paper.py:365-374`）；然后在 `refusing_outward_calls()` 中跑**一步** `PortfolioBacktestRunner.run`（`paper.py:375-376`）。
- 构造参数只有 `ledger`（必填）与 `limits`（`paper.py:343-350`）。**没有成本 / 执行策略参数**。
- 返回 `PaperSessionResult`：`trade_date`、`observed_on`、`before`、`after`、`transitions`、`carried_marks`、`equity`、`gross_exposure`（`paper.py:308-323`）。**没有 `benchmark_close`**（原型 §0 输出：`benchmark_close survives into the result: False`）。

### 1.2 `PortfolioBacktestStep` 的要求（`multi_day.py:88-133`）

- 字段：`trade_date`、`bars`（至少 1 条）、`orders`（可空）、`benchmark_close`（`> 0`）（`multi_day.py:104-107`）。
- 校验：所有 bar 同一日期；bar 的 `subject` 唯一；同一会话内 `order_id` 唯一；**每个下单标的必须有当日 bar，否则构造即失败**（`multi_day.py:119-121`）。
- 运行顺序：先把**持有**的标的按当日 `bar.close` 重估（无 bar 的沿用旧价并记 `CarriedMark`，`multi_day.py:343-383`），再按 `orders` 元组顺序逐笔执行（`multi_day.py:239-248`）。**每一笔 transition（含被拒绝的）都写入账本**（`multi_day.py:246-247`）。

### 1.3 执行规则

- `AShareExecutionPolicy.execute` 以 `market.close` 成交（`execution.py:317, 337`）。
- 拒绝条件（`execution.py:345-370`）：停牌；买单整手 / 科创板 200 股；卖单 T+1；买单 `low >= upper`（一价涨停）；卖单 `high <= lower`（一价跌停）。有已公布涨跌停价时用公布价（`execution.py:373-381`）。
- `PortfolioSimulator`：买单现金不足整单拒绝（`portfolio.py:149-151`）；成交后检查 `max_position_weight` / `max_total_exposure` / `min_cash_weight`（`portfolio.py:176-194`），默认 0.25 / 0.80 / 0（`portfolio.py:70-72`）；卖单按 T+1 可卖数量检查（`portfolio.py:210-217`）；新买入的标记价 = `market.close`（`portfolio.py:166-167`）。
- 默认成本 `CostSchedule()`：佣金万 3、最低 5 元、过户费万 0.1、卖出印花税千 0.5（`execution.py:294-297`）。协议口径是佣金万 2.5、无过户费、印花税千 0.5（`strategy_view.py:385-392`），外加单边 10bp 滑点（`strategy_view.py:393`）。

### 1.4 P6 书如何做"开盘成交"

`strategy_backtest.py:24-31` 写明：执行策略按收盘价成交，所以每笔订单针对**折叠到开盘价**的 bar（`open = high = low = close = open`，同前收、同涨跌停价、同停牌标记）来判断——在这个 bar 上，策略自己的规则恰好就是集合竞价规则。实现是 `_at_the_open`（`strategy_backtest.py:2393-2395`）。滑点不在执行策略里，而是在 `_record_fill` 中按成交额另计为现金成本（`strategy_backtest.py:2398-2399, 2411-2438`）。持仓估值用 `close × adj_factor / entry_adj`（`strategy_backtest.py:2498-2507, 2581-2586`）。

### 1.5 现有的 `MarketBar` 组装：三份，停牌口径不一

| 位置 | 停牌折叠 | `is_st` |
|---|---|---|
| `strategy_view._PanelDays.quote`（`strategy_view.py:2009-2025`） | `suspended_at_the_close(...) or state is TradingState.interrupted`（`:2018-2021`），即"开盘时不能成交" | 恒 `False`（有公布价时不读，`:2022-2023`） |
| `factor_view.market_bar`（`factor_view.py:1605-1625`） | 仅 `suspended_at_the_close`（`:1614-1616`） | 由名称史判定 |
| `shortlist_view`（`shortlist_view.py:1804-1820`） | 仅 `suspended_at_the_close`（`:1813-1815`） | 由名称史判定 |

`_PanelDays` 是私有类，构造需要一个完整的 `StrategyRequest`（`strategy_view.py:1904-1911`），但实际只用到 `request.years` 与 `request.as_of`（`:1919-1937, 1955-1963, 1974-1988`）。`quote` 在缺日线、缺涨跌停行或缺复权史时返回 `None`（`:1998-1999`）。

---

## 2. Step 1：能否用现有组装函数直接得到 `PortfolioBacktestStep`？

| 需要 | 来源 | 现状 | 结论 |
|---|---|---|---|
| `bars`：开盘价、高低、收盘、前收、涨跌停价、停牌 | `_PanelDays.quote(day, code).bar` | 字段齐全，且已带公布涨跌停价与开盘口径的停牌 | **字段不缺**；但类私有、绑在 `StrategyRequest` 上 → 抽共用 |
| 开盘成交 | — | 执行按 `close` 成交；原样放入真实 bar，原型 3(a) 中 `600000.SH` 按收盘 10.50 成交而非开盘 10.10，`300001.SZ` 开盘一价涨停、盘中开板，却按收盘 23.00 **成交了** | 下单标的的 bar 必须先 `_at_the_open` 折叠 → 抽共用 |
| 当日收盘估值 | — | 折叠后，当日新买入的标的被标记在开盘价：原型 session 101 返回权益 86,094.90，按收盘重估为 86,494.90（差 400 = 1,000 × (10.50 − 10.10)）；次日运行会自动按次日收盘重估 | 新增 `remark_at_close`（纯函数）；或接受"当日新买入按开盘价估值" |
| 除权除息 | `quote.adj_factor` | 账本以原始收盘估值、数量不调整：原型 §4 中 10 转 10 的持仓权益从 10,200 掉到 5,100；按复权比缩放 bar 后保持 10,200 | **缺**，新增（见下） |
| 全天停牌的下单标的 | — | 无日线行 → step 构造报错（原型 3(b)）；研究期主板 + 创业板 102,141 条 `S` 停牌行中 **101,206 条（99.1%）当日没有日线行** | 适配器把这类订单拿出 step，自记"未尝试"（P6 书的同名理由："no bar on the execution session"，`strategy_backtest.py:2551-2553`） |
| `orders` | 交易清单 | 见 §3 | 新增映射 |
| `benchmark_close` | `index_daily` | 对纸面账本无作用 | 填 `000905.SH` 收盘 |

**除权除息的量级**（研究期，主板 + 创业板，`t5_panel_measure.py`）：每只股票每年至少一次复权因子变动的比例为 0.67–0.96；至少一次 **比值 ≥ 1.10**（送转或大额分红，价格跳空 ≥ 约 9%）的比例为 0.096–0.174（2017：0.142；2018：0.174；2019：0.129；2020：0.099；2021 年 1–10 月：0.096）。按独立近似，10 只持仓一年里至少碰上一次 ≥1.10 事件的概率约 1 − (1 − p)^10 ≈ 64%–85%。**不处理就会在大多数账簿年里出现一次虚假的大幅亏损**，现金分红则每年系统性低估约一个股息率。

面板里没有分红送转明细数据集，只有 `adj_factor`，无法把"现金分红"与"送转股"分开。所以唯一可用的做法与 P6 书一致：**按复权比 `adj_t / adj_entry` 缩放持有标的的整根 bar（含前收与涨跌停价）**。等比缩放不改变任何涨跌停 / 一价判断；Step 0 只整笔买入、整笔卖出（参考 `build_trade_list` 卖出数量 = 持有数量，`survivor_orders.py:97-111`），买入当日比值为 1，所以每个持仓只需一个 `entry_adj`。代价要披露：账本里的持股数是"买入时股数"，与真实账户在送转之后的股数不同；交易清单展示卖出股数时需要换算或注明。

**`benchmark_close` 取什么**：`PaperPortfolio` 丢弃它，单步报告的 `benchmark_return` 恒为 0（`multi_day.py:266`），账本表只有 `order_id / subject / payload`（`storage/portfolio.py:22-27`）。所以这个选择不影响账本。建议填 **中证 500（`000905.SH`）当日收盘**——一个真实存在的指数点位（研究期 1,172/1,172 个交易日有值），而不是把"可交易板块内可投资等权"的日收益链成一个虚构点位。§8 的两个基准（可投资等权、中证 500）由 `V2-P7-009` 的周报从面板单独计算。

---

## 3. Step 2：`TradeOrder` → `PortfolioOrder` 映射

`TradeOrder` 定义见参考 `survivor_orders.py:34-53`；`PortfolioOrder` 见 `src/openalpha_cn/domain/portfolio.py:132-162`。

| `TradeOrder` | `PortfolioOrder` | 映射 | 理由 / 证据 |
|---|---|---|---|
| `order_id`（计算字段，`tor_` + 24 位十六进制，28 字符） | `order_id`（1–128 字符） | **每次尝试一个 id**：`f"{order_id}@{trade_date.isoformat()}"`（39 字符） | 账本把被拒绝的 transition 也写入（`multi_day.py:246-247`），同一 id 再写不同内容即抛 `portfolio order_id conflicts`（`storage/portfolio.py:42-44`）。原型 3(c)：顺延卖单沿用原 id，第 2 日 `advance` 整体抛错。用后缀而非新的哈希，避免新增一个内容地址铸造点（`domain/_identity.py:31-40` 的审计） |
| `ts_code` | `subject` | 原样 | `MarketBar.subject` 即 `ts_code`（`strategy_view.py:2010`） |
| `side` | `side` | 原样 | 同为 `Literal["buy","sell"]` |
| `quantity` | `quantity` | 原样（买单是否在开盘时按现金向下截断，见 §6 待决定） | `ge=1` ⊂ `gt=0`；整手已由 `lot_quantity` 保证 |
| `reference_price` | — | 不映射，留在 `TradeList` | `PortfolioOrder` 没有限价概念；成交价由 bar 决定 |
| `execute_session`（整数序号） | → `PortfolioBacktestStep.trade_date` | 适配器经交易日历把序号映射成日期（参考 `survivor_policy.py:56` 已写明由适配器映射）；仅当 `execute_session ≤ s` 时放入第 `s` 日的 step | — |
| `expires_session` | — | 不映射；过期由 `advance_orders` 判定（`survivor_lifecycle.py:48`） | `PaperPortfolio` 不知道有效期 |
| `signal_session`、`reason`、`schema_version` | — | 不映射；靠 `order_id` 前缀回溯到 `TradeOrder` | — |
| — | `target_weight` | `None` | 放开限额后它只是声明（`portfolio.py:129-137`）；Step 0 按股数下单 |

反方向：`advance_orders(..., filled_order_ids=...)` 需要的"已成交 `TradeOrder.order_id` 集合"由适配器从 `PaperSessionResult.transitions` 中 `status == "filled"` 的 `order.order_id` 去掉 `@日期` 后缀得到（原型 `filled_trade_ids`）。

**顺序由适配器负责**：`step.orders` 的元组顺序就是执行顺序（`multi_day.py:239`），卖出所得当日可用于买入。参考 `build_trade_list` 把卖单排在前（`survivor_orders.py:138-141`），但 `advance_orders` 返回的 `live` 按 `(side, ts_code, order_id)` 排序，**买单在卖单之前**（`survivor_lifecycle.py:43`，原型 session 101 生命周期输出 `live ['000006.SZ', '300001.SZ', '600005.SH', '000002.SZ']`）。适配器每天必须重新排成"卖单 → 买单"，并约定顺延买单与新买单之间的先后（现金不够时决定谁被拒）。

---

## 4. 不能成交的订单：`PaperPortfolio` 的实际行为（原型确认）

| 情形 | `PaperPortfolio` 的行为 | 原型（§5 输出） | 代码 |
|---|---|---|---|
| 买单现金不足 | **整单拒绝** `insufficient cash`，不部分成交，状态不变，拒绝写入账本 | session 101：`000006.SZ x2000` 需 30,009.30，现金 29,894.90 → 拒绝（1,900 股本可买得起） | `portfolio.py:149-151` |
| 涨停买不进（开盘一价涨停，已折叠到开盘） | 拒绝 `buy cannot fill on a one-price limit-up bar` | session 101/102/103：`300001.SZ` 三次拒绝 | `execution.py:366-367` |
| 同上，**未折叠**（开盘涨停、盘中开板） | **按收盘价成交**——与设计语义不符 | 3(a)：`300001.SZ` filled 23.00 | `execution.py:317, 366` |
| 跌停卖不出（开盘一价跌停） | 拒绝 `sell cannot fill on a one-price limit-down bar` | session 101：`000002.SZ` | `execution.py:368-369` |
| 停牌，有 bar 且 `suspended=True`（盘中 / 定时停牌） | 拒绝 `security is suspended` | session 102：`600005.SH` | `execution.py:351-352` |
| 停牌，**无 bar**（全天停牌，99.1% 的情形） | **不是拒绝：`PortfolioBacktestStep` 构造即报错**，整日推进失败；账本看不到这笔单 | 3(b)：`ordered subjects have no bar on this session: ['600005.SH']` | `multi_day.py:119-121` |
| 持有、未下单、无 bar | 沿用旧标记并记 `CarriedMark` | （代码阅读） | `multi_day.py:359-371` |
| T+1 | 拒绝 `insufficient T+1 available quantity` | 未触发：Step 0 的卖单来自信号日之前的持仓，执行日至少晚一个交易日 | `portfolio.py:210-217` |
| 默认限额 | 10 只等权首建：第 9、10 笔拒绝 `maximum total exposure exceeded` | §5：默认 8/10 成交；`PortfolioLimits(1, 1, 0)` 时 10/10 | `portfolio.py:70-72, 183-188` |
| 顺延单沿用原 `order_id` | `ValueError: portfolio order_id conflicts`，整个 `advance` 抛错 | 3(c) | `storage/portfolio.py:42-44` |
| 同一日再推进一次 | `ValueError: ... must move the book forward` | §2 | `paper.py:365-369` |

**据此的分工**：

- `PaperPortfolio`：逐笔判定"今天这一笔能不能成"，能则记账，不能则记一条拒绝。**不顺延、不过期、不部分成交、不静默跳过**（无 bar 的情形连尝试都做不了）。
- 订单生命周期（参考 `advance_orders`，`survivor_lifecycle.py:27-59`）：用"已成交 id"与"今日幸存者"决定 `filled / live / expired / cancelled`。原型中 `300001.SZ` 三次涨停后在 session 103 被判 `expired`，`000002.SZ` 跌停卖单顺延一日后成交，`600005.SH` 停牌两日后成交——生命周期在这一层工作正常。
- 适配器：把 `live` 订单变成当日 step（过滤、排序、每次尝试的 id、无 bar 的拿出并记"未尝试"），把 transition 映射回 `TradeOrder` id。

---

## 5. 原型（Step 3）

手写 3 日夹具，价格虚构；不读面板、不联网；临时目录里的 `SQLitePortfolioLedger`，运行结束即删。复现命令（在本 worktree 根目录）：

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" \
    "/Users/licai918me.com/Documents/OpenAlpha cn/.venv/bin/python" -P \
    ~/openalpha-research/diagnostics/p7/t5_paper_step_prototype.py
```

开局：`as_of = 2021-03-01`，现金 40,000；持有 `000002.SZ` 2,000 股（标记 20.00）、`600004.SH` 1,000 股（标记 10.00）。信号日（序号 100）的交易清单：卖 `000002.SZ` 2,000；买 `600000.SH` 1,000、`300001.SZ` 500、`600005.SH` 1,000、`000006.SZ` 2,000（买单 `expires_session = 103`）。适配器做法：下单标的用 `_at_the_open` 折叠，持有未下单的用真实 bar，持有标的按复权比缩放，无 bar 的订单拿出 step，`order_id` 加 `@日期`，限额 `PortfolioLimits(1, 1, 0)`。

输出摘录（逐字）：

```
== 0. PaperPortfolio surface ==
  PaperPortfolio.__init__ (self, *, ledger: 'PortfolioLedger', limits: 'PortfolioLimits | None' = None) -> 'None'
  benchmark_close survives into the result: False
  TradeOrder.order_id example: tor_36789ae255259f8addb92529 len 28

== 1. three sessions, open fills, per-attempt ids, unbarred orders dropped ==
  opening equity 90000.00
  session 101 2021-03-02:
    sell 000002.SZ x2000  -> rejected price=None fees=None reason='sell cannot fill on a one-price limit-down bar'
    buy  600000.SH x1000  -> filled   price=10.10 fees=5.10 reason=None
    buy  300001.SZ x500   -> rejected price=None fees=None reason='buy cannot fill on a one-price limit-up bar'
    buy  000006.SZ x2000  -> rejected price=None fees=None reason='insufficient cash'
    ---- 600005.SH: not put into the step (no bar on the execution session); the ledger never sees it
    cash=29894.90 equity(as returned)=86094.90 marks=[('000002.SZ', '18.00'), ('600000.SH', '10.10'), ('600004.SH', '10.10')]
    equity re-marked at the close (adapter) = 86494.90
    lifecycle: filled ['600000.SH'] live ['000006.SZ', '300001.SZ', '600005.SH', '000002.SZ'] expired [] cancelled []
  session 102 2021-03-03:
    sell 000002.SZ x2000  -> filled   price=17.50 fees=28.35 reason=None
    buy  000006.SZ x2000  -> filled   price=15.10 fees=9.36 reason=None
    buy  300001.SZ x500   -> rejected price=None fees=None reason='buy cannot fill on a one-price limit-up bar'
    buy  600005.SH x1000  -> rejected price=None fees=None reason='security is suspended'
    ...
    lifecycle: filled ['000006.SZ', '000002.SZ'] live ['300001.SZ', '600005.SH'] expired [] cancelled []
  session 103 2021-03-04:
    buy  300001.SZ x500   -> rejected price=None fees=None reason='buy cannot fill on a one-price limit-up bar'
    buy  600005.SH x1000  -> filled   price=10.05 fees=5.10 reason=None
    cash=24602.09 equity(as returned)=86352.09 marks=[('000006.SZ', '15.40'), ('600000.SH', '10.70'), ('600004.SH', '10.20'), ('600005.SH', '10.05')]
    equity re-marked at the close (adapter) = 86502.09
    lifecycle: filled ['600005.SH'] live [] expired ['300001.SZ'] cancelled []
  ledger rows: 10 (rejections are recorded too)

== 2. re-advancing a session already lived ==
  ValueError: paper session 2021-03-04 must move the book forward from 2021-03-04

== 3. same session-1 inputs, three naive variants ==
  (a) raw bars, no open collapse -> fills at the CLOSE, limit-up-at-open buy fills:
    buy  600000.SH x1000  -> filled   price=10.50 fees=5.11 reason=None
    buy  300001.SZ x500   -> filled   price=23.00 fees=5.12 reason=None
  (b) order for a name with no bar left in the step -> ValueError: Value error, ordered subjects have no bar on this session: ['600005.SH'] ...
  (c) carried order re-sent under its planned id -> ValueError: portfolio order_id conflicts: tor_36789ae255259f8addb92529

== 4. corporate action on a held name (10-for-10 bonus, session 103) ==
  adjust=False: equity 10200.00 -> 5100.00
  adjust=True : equity 10200.00 -> 10200.00

== 5. default PortfolioLimits on a 10-name equal-weight first build (350,000) ==
  default (0.25 / 0.80): filled 8/10, rejected [('600018.SH', 'maximum total exposure exceeded'), ('600019.SH', 'maximum total exposure exceeded')]
  explicit (1 / 1): filled 10/10, rejected []

== 6. costs: what PaperPortfolio charges vs the protocol (commission 2.5bp, stamp 5bp, slippage 10bp) ==
  buy 000006.SZ x2000 @ 15.00: notional 30000.00
    PaperPortfolio today (CostSchedule() default): commission 9.00 transfer 0.30 total 9.30
    protocol (P6 book: PROTOCOL_COSTS + slippage): commission 7.50 + slippage 30.00 = 37.50
    protocol via transfer_fee_rate=slippage: total 37.50 (equal: True)
```

原型确认了 §2–§4 的回答：开盘成交需要折叠；涨停 / 跌停 / 停牌（有 bar）都是逐笔拒绝；无 bar 是构造错误；现金不足整单拒绝；顺延 / 过期在生命周期层工作；每次尝试必须用新 id；默认限额与默认成本都不适合 Step 0。

---

## 6. 适配器要补什么：复用 / 抽共用 / 新增

| 部分 | 处理 | 说明 |
|---|---|---|
| `PaperPortfolio.advance`、`PortfolioBacktestRunner`、`PortfolioSimulator`、`AShareExecutionPolicy` 的全部规则（整手、T+1、涨跌停、停牌、FIFO、现金） | **复用** | 不重写任何执行规则 |
| `security_board`、`published_limit_fields`、`suspended_at_the_close` | **复用** | `execution.py:148-204, 411-433` |
| 参考 `advance_orders`、`update_cooldowns` | **复用**（进入 `V2-P7-008` 后） | 生命周期在领域层 |
| 会话行情读取（日线 + 公布涨跌停 + 停牌语料 + 复权史 → `SessionQuote`） | **抽共用**：把 `_PanelDays` 的构造从 `StrategyRequest` 改为 `(store, calendar, years, as_of)`，公开后由 P6 书与 Step 0 适配器共用 | 它只用 `request.years`、`request.as_of`；停牌口径（含 `interrupted`）正是开盘成交需要的口径。不另写第四份 `MarketBar` 组装 |
| `_at_the_open` | **抽共用**：公开并移到 `MarketBar` 旁（`backtest/execution.py`），`strategy_backtest` 与步骤构建器共用 | 一行函数，但它承载"集合竞价语义"，两处必须同一份 |
| 成本可注入 | **新增（非破坏性）**：`PaperPortfolio(..., execution: AShareExecutionPolicy \| None = None)`，透传给 `PortfolioBacktestRunner` → `PortfolioSimulator`（后者已支持，`portfolio.py:93-100`） | 默认值不变，现有调用与测试不受影响；不涉及持久化契约 |
| 滑点 | **新增配置，不改契约**：纸面专用 `CostSchedule`，`transfer_fee_rate = PROTOCOL_SLIPPAGE_RATE`（协议无过户费），其余取 `PROTOCOL_COSTS` | 原型 §6：与 P6 书的"费用 + 滑点"逐分相等（37.50 = 37.50）。代价：账本里的 `transfer_fee` 实为滑点，须在配置与日志中写明。**不要**给 `CostSchedule` 加字段——它嵌在已登记的 `StrategySpec` 里（`strategy_backtest.py:788`），加字段会改变 P6 登记配置的序列化（硬规则 3） |
| 限额 | **新增配置**：`PortfolioLimits(max_position_weight=1, max_total_exposure=1, min_cash_weight=0)` | 默认 0.80 会拒绝 10 只等权的第 9、10 笔；放开后只有"现金不足"约束，成交与估值顺序无关 |
| 步骤构建器（纯函数，无面板） | **新增** | 输入：当日 `SessionQuote` 映射、`live` 订单、`PortfolioState`、`entry_adj`、`benchmark_close`。做：按 `execute_session ≤ s` 过滤；卖单在前；下单标的 `_at_the_open`；持有标的按 `adj_t / adj_entry` 缩放；无 bar 的订单拿出并返回"未尝试"记录；`order_id@日期` |
| transition → `TradeOrder` id | **新增** | 去掉 `@日期` 后缀；供 `advance_orders` 使用 |
| 收盘重估 | **新增**（纯函数） | 当日新买入的标的按收盘重估后作为当日账本状态与日志权益 |
| 账本状态持久化 | **新增** | 账本只存 transition；无订单的交易日不写任何行（`multi_day.py:239-247`），所以 `PortfolioState`（含 `as_of` 与标记）无法从账本还原。需要每个交易日落一份 `PortfolioState` 快照，并以 `state.as_of ≥ 当日` 判定"已推进"，实现 §7 的幂等（`advance` 本身对重放只会抛错） |
| 独立账本实例 | **新增（在单一组装根里）** | 现有 `portfolio_ledger` 位于共享的 `state.sqlite3`（`runtime/composition.py:247`），`sdk.run_portfolio_backtest` 也往同一表写回测 transition（`sdk.py:1296-1307`），表里没有账簿标识列。纸面账簿应有自己的账本路径，在 `runtime/composition.py` 中构造（硬规则 5） |
| 买单在开盘时按现金截断 | **待决定** | 现在是整单拒绝后顺延；参考的订单数量按信号日收盘与 0.5% 预留定量（`survivor_book.py:71, 89-96`），开盘跳空或卖单未成交时最后几笔买单会因现金不足被拒、并可能连续 3 日被拒后过期。P6 书在开盘时按现金向下取整（`strategy_backtest.py:2516-2538, 2559-2561`）。若采用，截断由适配器做并记录，`PaperPortfolio` 不变 |

---

## 7. 建议

**结论：不能直接复用现有行情组装函数得到 `PortfolioBacktestStep`，但能由现有部件组装；按"抽共用函数（不复制）+ 新增一个薄的纯函数步骤构建器"实施，`PaperPortfolio` 只加一个非破坏性参数。**

1. 抽共用：`_PanelDays` 与 `StrategyRequest` 解耦后公开；`_at_the_open` 公开到 `backtest/execution.py`。两处都先补"P6 书行为不变"的回归测试再搬。
2. 新增 `backtest/` 下的步骤构建器模块（位置邻近 `paper.py`），全部是纯函数，可用手写 `MarketBar` 夹具测试，不需要面板；测试清单按 §4 表格逐行写（含"无 bar 不进 step""顺延换 id""卖单在前""复权缩放""收盘重估"）。
3. `PaperPortfolio` / `PortfolioBacktestRunner` 增加可选 `execution`；纸面成本 = 协议成本 + 滑点折入 `transfer_fee_rate`；限额放开到 1 / 1 / 0。
4. 账本状态快照 + 独立账本实例，经组装根构造。
5. `benchmark_close` 填 `000905.SH` 当日收盘；基准对比在周报里单独算。
6. "买单开盘按现金截断"由用户 / 计划决定（倾向：截断并记录，与 P6 书一致）。

---

## 8. 对方案 A / B / C 的影响

**无实质影响。** 理由：

- **方案 C**：不交易、不改持仓，评估在幸存者内部的预测层（设计 §13；上游文档 §4.2）。纸面账本不在其路径上。
- **方案 A**：否决层加在资格层，产生的是 `EXIT_ELIGIBILITY_LOST` 一类的强制卖出，走的仍是本文的卖单路径（卖单不过期、持续顺延）。唯一需要知道的是：被否决的持仓若遇跌停或全天停牌，卖出在账本里会晚于否决日；A 的闸门 G-A 按预测层判据评估（上游文档 §5.2），所以不影响闸门，只影响账本记录的解读。
- **方案 B**：模型分数替换选择层，不碰账本。提示一条：上游文档 §4.4 已列"涨停名买不进"的风险；按本文口径，开盘一价涨停的买单被拒、3 日后过期并进入冷却（`survivor_lifecycle.py:62-82`），所以若 B 的模型偏好强势股，账本里会表现为买单大量过期。届时应把"买单成交率 / 过期率"作为 B 的附带报告，但这不改变 B 在预测层的评估方式。

## 9. 对 Step 0 设计本身的影响（建议回写设计 §12 与上游文档 §9）

1. **§4.2 的步骤顺序需要调整。** 现写"5. 交易清单（与纸面账本的当前持仓取差）→ 6. 纸面账本推进（上一日的订单）"。但第 T 日晚上的交易清单需要的 `BookState`（持仓、待成交买单、权益）和 `carried` 订单，都取决于第 T 日开盘的成交（即第 6 步）与生命周期结算。正确顺序应是：**先推进账本（用 T 日已观察 bar 执行 T−1 日的清单）→ 结算订单生命周期（需要 T 日幸存者）→ 持仓策略 → 交易清单**。按现写顺序会用昨天的持仓生成清单。
2. **§7 "执行……全部沿用 `AShareExecutionPolicy`"成立，但成本与滑点不能只靠沿用**：`PaperPortfolio` 今天收的是默认成本（佣金万 3、过户费万 0.1、无滑点）。需要 §6 的非破坏性参数 + 纸面成本配置；否则账本成本远低于协议口径：一笔 30,000 元的买单 9.30 对 37.50（原型 §6），一笔 35,000 元的卖单 28.35（原型 session 102）对按协议计算的 61.25（佣金 8.75 + 印花税 17.50 + 滑点 35.00）。
3. **除权除息必须处理**（§2）：否则约 64%–85% 的账簿年会出现一次虚假的大幅回撤，且分红被系统性漏记。建议在 §7 增加一条"持仓按复权比估值；账本股数为买入时股数，展示时换算"，并作为已披露限制写进报告。
4. **参考 `build_trade_list` 没有给顺延买单预留现金**：`available = cash + proceeds`（`survivor_orders.py:113-115`）未减去 `carried` 中仍有效买单的金额，而 `BookState.pending_buys` 会占位置。次日顺延买单与新买单争同一笔现金，排在后面的会被整单拒绝。这是 `V2-P7-008` 领域内核的问题，不是适配器能单独解决的；建议在内核测试里加一条"有顺延买单时新买单的预算"。

## 10. 局限与披露

- 原型夹具是手写的、价格虚构，只验证机制，不代表任何真实交易日。
- 原型里的适配器函数是一次性代码，引用了私有的 `strategy_backtest._at_the_open`，只为说明可复用；正式实现按 §7 先公开再用。
- 面板计数的第一次运行把日期边界写成了 `'20170101'…'20211031'`，与面板的 `YYYY-MM-DD` 格式做字符串比较，实际覆盖了 2018-01-01 … 2021-12-31，**包含研究期之外的 2021-11、12 月**（未触及 2022 年及以后，未计算任何收益，只计了复权事件与停牌行数）。发现后已改正边界重跑，本文只采用改正后的数字。
- 计数里有 3,342 次复权因子**下降**的变动未作解释；它们不影响结论（等比缩放对任何方向的比值都成立）。
- "10 只持仓一年至少一次 ≥1.10 事件"的 64%–85% 是按独立近似推算，不是模拟。
