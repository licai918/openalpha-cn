# T3：ST 与次新的数据来源（`V2-P7-005` / Phase 0 / Task 0.1）

> **性质**：只读技术验证，回答设计 §10 的 T3。结果不是对策略的改进，也不是研究有效性的证据。
>
> **数据边界**：结论只取自信号日 2017-01-03 … 2021-10-14（共 59 个，每 20 个交易日取一个，口径同 `diagnostics/p7/decile.py` 的 `sig`）。`namechange` / `trade_cal` / `daily` / `stk_limit` 的 2013–2016 分区只作为"历史底数"被读取（名称史、日历、首根 K 线、20 日成交额窗口），不据以下结论；验证期与已花掉的保持窗口（2022 起）**一个分区都没有读**——脚本里每张表都带 `<= 2021-10-31`，`stock_basic` 只读 1990–2021 年分区。
>
> **方法边界**：不联网、不调用数据商、不读 `.env`；不经 `PanelStore` / `catalog.duckdb`（直接读 parquet，因此没有任何锁文件或目录写入）；名称语法与注册簿重建使用仓库自己的纯函数（`name_histories_from_panel_rows`、`risk_warning_of`），不是重写的版本。可复现脚本与它的原始输出附在文末（附录 A、B），原件存放于 `~/openalpha-research/diagnostics/p7/t3_st_newlist.py`。

## 0. 结论与决定

1. **ST 有可靠来源，且在主板上有独立见证。** 来源是面板数据集 `namechange`（按公告年分区，2013–2026），读取接口是 `panel_ingest.load_name_histories`（`src/openalpha_cn/panel_ingest.py:1584`）→ `NameHistory.announced_by(d).risk_warning_on(d)`（`src/openalpha_cn/domain/name_history.py:310,325`）。主板用交易所公布的涨跌停价（`stk_limit`，5% 带宽即 ST / *ST / S 标记）核对：名称判为 ST 的 6,675 个"证券·信号日"里 6,674 个确有 5% 带（99.985%）；反向，5% 带的 6,724 个里 6,674 个被名称判为 ST，其余 50 个**全部**是同一只证券（`600182.SH`，名称史第一行晚到 2021-04-26）。
2. **上市日期可靠，且能给出"上市满 250 个交易日"。** 来源是 `stock_basic` 的 `listing` 生命周期行（`StockUniverse.security(code).listed_on`）加 `trade_cal`（`TradingCalendar.trading_days_between`）。2017-01-01 以后上市的 1,195 只沪深主板 + 创业板证券，首根日 K 线与 `list_date` 在**同一交易日**的有 1,195 只（2013 起 1,772 / 1,772）。每个信号日 99.9%–100% 的可交易板块证券能算出上市交易日数；算不出的每天只有 1–3 只，且全是"改了代码的旧代码"（见 §3.3），按"未知即不合格"处理反而是对的。
3. **`strategy_view.py:2023` 的 `is_st=False` 既不是缺来源，也不是遗漏。** 它是给一个"从不被读"的必填字段填的占位值（原文注释 `Never read: with a published band ...`，`strategy_view.py:2022`）。但这件事有一个更重要的后果：**在 `src/` 里没有找到任何 ST 或次新的资格筛选**——`is_st` 唯一的读点是 `backtest/execution.py:382` 的涨跌幅推导，`registry.listed_on` 只被用来判"那天是否在册"。所以 P6 的回测与探索期数字都包含 ST 与次新（见 §6）。
4. **决定：照设计。** 资格层按"ST（含 `*ST`、`PT`、退市整理期）"与"上市不满 250 个交易日"两个原因码做过滤，不降级为已披露限制。**但必须带着 8 条约束实施（§7）**，其中最要紧的三条：(i) 读取 `namechange` 要从 2013 年读到当年，不能只读信号日所在年（只读当年会漏掉 29%–100% 的 ST，当年加上一年仍漏 8%–10%，§2.4）；(ii) 现有 `_risk_warned_on` 遇到"语料里没有当日名称"会**拒绝整个 run**（`NameHistoryHorizonError`，每个信号日最多 11 只），适配器必须自己写一个全函数、不能复用；(iii) 主板上加一条"5% 涨跌停带宽 ⇒ 视为 ST"的并集规则，成本为零额外请求。
5. **对方案 A / B / C：无阻断性影响，但有三条必须提给控制者的发现**（§8）：探索期的 +6.6% / +7.7% 是在**没有** ST 与次新过滤的宇宙上量出来的，幸存者里有中位 3.6%（最高 8.1%）是 ST 或次新；每日抓取清单 `BASE_TARGETS` **不含 `namechange`**；创业板的 ST 没有独立见证。

## 1. 来源盘点：代码里有什么

### 1.1 ST

| 层 | 位置 | 内容 |
|---|---|---|
| 语法 | `src/openalpha_cn/domain/name_history.py:217`（`risk_warning_of`） | 名称 → `RiskWarning`（`none / st / star_st / pt / delisting_process`）。处理了股改 `S`/`G` 前缀（`SST华新`）与 2020 年前创业板的裸 `*` 前缀（`*欣泰`、`*金亚`）；`RiskWarning` 对每个成员 `__bool__` 都抛错，逼调用方写明合并方式（`name_history.py:185-215`）。 |
| 两时钟 | `name_history.py:289,310,314,325` | `record_on` / `risk_warning_on` 按**生效日**答；`pending_at` 答"已公告、未生效"；`announced_by(d)` 是点对点过滤。`record_on` 对早于第一条记录的日子**抛** `NameHistoryHorizonError`（`:159`），不外推。 |
| 读取 | `src/openalpha_cn/panel_ingest.py:1584`（`load_name_histories`） | 按"公告年"读 `namechange` 分区，`available_time == event_time ==` 公告日 0 点；生效日作为 `effective_date` 列随行存储（`:1603-1619` 的 docstring）。 |
| 写入 | `src/openalpha_cn/cli.py:1974-1990`（`PANEL_BUILD_TARGETS`） | `panel build --dataset namechange --year <year>`。 |
| 现有消费者 | `factor_view.py:1353,1617`、`shortlist_view.py:1824,1816` | **只**用来给 `MarketBar.is_st` 赋值，而 `is_st` 的唯一读点是 `backtest/execution.py:373-382`（`_price_band`：没有已公布涨跌停价时才用 `0.05 if is_st`）。两张面都用已公布的带宽，所以这个值"今天不改变任何判定"（`factor_view.py:1382-1386` 的 docstring 自己这么写）。 |

没有备选来源：`stock_basic` 在面板里**不存名称**（`domain/stock_universe.py:99` 起"What is deliberately not stored"一节），`daily` / `daily_basic` 没有 ST 标志，`providers/` 里没有 `stock_st` 之类的逐日 ST 清单端点（`grep -rn stock_st src` 无命中）。**`namechange` 是唯一的名称来源。**

### 1.2 上市日期

| 层 | 位置 | 内容 |
|---|---|---|
| 契约 | `src/openalpha_cn/domain/stock_universe.py:201`（`SecurityLifecycle.listed_on` / `delisted_on`）、`:384`（`StockUniverse.security`，未知代码抛 `StockUniverseError`）、`:399`（`status_on`） | `listed_on` **含当日**（`:49` 起实测：`688001.SH` 等首根 K 线正落在各自 `list_date`），`delisted_on` **不含**。 |
| 读取 | `panel_ingest.py:1113`（`load_stock_universe`）、`:940`（`load_trading_calendar`） | 注册簿按"生命周期年"分区，读取时自动向下补读更早的年份（`:1155-1170`）。 |
| 现有消费者 | `shortlist_view.py:1406`、`factor_view.py:3876`（`registry.listed_on(session)`） | 只做"那天是否在册"，**不做上市年龄**。 |
| 日历 | `src/openalpha_cn/domain/trading_calendar.py:339`（`trading_days_between`）、`:171`（`CalendarHorizon.first_date`） | 闭区间交易日列表；区间越出日历范围时抛 `CalendarHorizonError`，**不截断**。 |

## 2. (a) ST：来源、覆盖与可靠性

### 2.1 覆盖

`namechange` 分区在盘上是 2013–2026 共 14 个目录（按**公告年**）；到 2021-10-31 为止读入 **4,228 行、3,072 只证券**（最早公告 2013-01-08、最早生效 2013-01-14），按公告年 178 / 312 / 441 / 478 / 649 / 321 / 437 / 694 / 718（2013 … 2021-10）。其中 **476 只**在 2013–2021-10 的某个时刻名称带 ST / *ST / PT（全部是沪深主板与创业板代码）。**2013 年之前的名称一行都没有**：没有任何一只证券的最早记录生效日早于 2013-01-14（`diagnostics` 脚本 §A）。这就是整个来源的左边界，也是下面所有缺口的共同原因。

### 2.2 可靠性：独立见证

名称来源本身是自洽的，但"自洽"不等于"对"，所以用交易所公布的涨跌停价当独立见证。主板 ST / *ST（以及 `S` 标记名称）的已公布涨跌停价是 ±5%，普通名称是 ±10%（`domain/price_limits.py:82` 起在 2024-06-28 的实测，本报告在 2017–2021 的数据上重新验证，见下表）；`stk_limit.up_limit / daily.pre_close ≈ 1.05` 是交易所自己的陈述，与 `namechange` 完全无关。59 个信号日、主板（600/601/603/605、000/001/002/003）合计 163,841 个"证券·信号日"：

| 名称状态 ＼ 当日涨跌停带宽 | 5% | 10% | other | 无记录 | 合计 |
|---|---:|---:|---:|---:|---:|
| 名称 = ST / *ST / PT | **6,674** | 1 | 0 | 0 | 6,675 |
| 名称 = 退市整理期 | 0 | 54 | 0 | 0 | 54 |
| 名称 = 普通 | **0** | 74,952 | 40 | 16 | 75,008 |
| 无名称记录（`no_record`） | **50** | 81,845 | 0 | 18 | 81,913 |
| 有记录但当日无生效记录（`before_first`） | 0 | 190 | 0 | 1 | 191 |

- **名称 = ST 而带宽不是 5%**：仅 1 个——`600732.SH` 2017-06-06（`nb=1`，近 20 个交易日只有 1 根 K 线，是长期停牌后的复牌首日；该日带宽 8.44/7.67 = 1.100，次日 1.050）。
- **带宽 = 5% 而名称不是 ST**：50 个，**全部是 `600182.SH`**。它的名称史只有一行：2021-04-23 公告、2021-04-26 生效的 `SST佳通`（`change_reason=ST`）；此前的名称在语料左边界之外。2017-01-03 … 2021-04-14 共 50 个信号日带宽都是 5%。以证券计：379 只主板证券在某个信号日有 5% 带，379 只在某天被名称判为 ST；名称源在其中 **1 只**上缺了 50 天。
- 名称 = 普通却是 5% 带：**0**。名称 = 退市整理期的 54 个全是 10% 带，与 `price_limits.py:56-62` 所述"退市整理期名称沿用板块 10%"一致。

**创业板（300/301，约占宇宙的 1/4）没有独立见证**：创业板 ST 的带宽与板块相同。能做的只有内部见证——`change_reason` 与名称语法对照（4,228 行）：理由是 `ST` / `*ST` / `高风险警示` / `从ST变为*ST` / `撤消*ST并实行ST` 而名称却是普通的行数 **0**；理由是 `撤销ST` / `撤销*ST` / `撤销高风险警示` 而名称仍带标记的行数 **0**；理由为 `其他` 而名称带 ST 的 29 行是"更名但保留标记"（如 `ST爱旭`，2019-12-27）。创业板按名称为 ST 的"证券·信号日"：2018 年 2 个（`*金亚`，裸 `*`）、2020 年 11 个、2021 年 156 个（2021-10-14 当日 24 只），时间上与 `name_history.py:61` 所述"2020 年前创业板没有 ST 体制、只有裸 `*` 前缀"一致。**这是内部一致，不是独立证明**，列入 §9 的未验证项。

### 2.3 每个信号日的名称状态（主板 + 创业板，59 日）

| 状态 | 最小 / 中位 / 最大（只·日） | 含义 |
|---|---:|---|
| ST（名称） | 42 / 123 / 208 | 其中主板 42 / 123 / 200，创业板 0 / 0 / 24 |
| 退市整理期 | 0 / 0 / 8 | `RiskWarning.delisting_process`，不是 ST，但同样不该买 |
| 普通（有生效记录） | 978 / 1,788 / 2,448 | |
| `no_record`（没有任何已公告记录） | 1,516 / 1,677 / 1,790 | 2013 年起没改过名的证券，按"语料里没有就是没改过"处理 |
| `before_first`（有记录，但都晚于当日生效） | 0 / 3 / 11 | 见 §2.4(ii) |

### 2.4 已量出的缺口

**(i) 读取年份窗口决定漏掉多少。** `factor_view.py:660` 把它登记为 `a_name_never_announced_inside_the_requested_years_is_priced_as_ordinary`。这里把它量出来：同一信号日、同一语法，只改"读哪几个公告年"，主板按名称判为 ST 的数量（括号外为 5% 带宽的只数）：

| 信号日 | 5% 带宽 | 只读信号日当年 | 当年 + 上一年 | 2013 … 当年 |
|---|---:|---:|---:|---:|
| 2017-01-03 | 56 | **0** | 51 | 55 |
| 2018-02-28 | 62 | 4 | 57 | 61 |
| 2019-05-24 | 124 | 88 | 114 | 123 |
| 2020-08-14 | 195 | 137 | 176 | 194 |
| 2021-10-14 | 158 | 105 | 146 | 158 |

ST 状态可以持续数年而不产生新的更名行，所以**必须读到语料起点**（2013）。语料全部读入只有 4,228 行，没有成本理由不这么做。

**(ii) `before_first`：有记录，但当日没有生效记录。** 59 个信号日 208,675 个"证券·信号日"里 240 个（0.115%），涉及 156 只证券，每个信号日 0–11 只（中位 3）。成因全部相同：证券上市早于 2013 年，第一条语料记录是一次**在当日之前公告、当日之后生效**的更名（如 `000519.SZ`：2016-12-16 公告、2017-01-10 生效的 `中兵红箭`；此前的名称在语料外）。主板的 191 个里 190 个带宽 10%、1 个无带宽，**0 个是 5%**。`record_on` 对这种情形**抛** `NameHistoryHorizonError`（`name_history.py:289-308`），而 `factor_view._risk_warned_on` / `shortlist_view._risk_warned_on` 把它转成"整个 run 被拒"（`V2-P4-080`）。这对现有两张面是对的，对每日资格层是错的：每个信号日会有最多 11 只证券让整个 run 失败。同时这也是为什么 `tests/panel_fixtures.py:2582-2591` 的测试夹具看不到它——夹具给每只证券一条"上市名"基线记录。

**(iii) `no_record` 里藏着的 ST。** 左边界的真实风险是"2013 年之前就是 ST、此后一直没改名"。在 2017-01-03 这个最靠近左边界的信号日，主板 56 只 5% 带宽的证券里，当前生效记录的生效年为 2014 年 1 只、2015 年 3 只、2016 年 51 只，**无法解析 1 只（`600182.SH`）**。也就是 55/56 在 2013–2016 内已经发生过一次更名；窗口内只有 1 只证券落进这个缺口。**2017 年以前这个缺口只会更大，本报告没有量**（§9）。

**(iv) 预先公告的程度。** 2017-01-01 … 2021-10-29 间，沪深主板 + 创业板共 371 次"普通 → ST"的名称生效，其中 **368 次（99.2%）在生效日前一个交易日或更早就已公告**，另外 3 次（2020-04-29 × 2、2020-06-30）是公告当日生效。公告到生效的日历日差：0 日 3 次、1 日 201 次、2 日 1 次、3 日 63 次、4 日 19 次、5 日 13 次、6 日 65 次、7 日 3 次、9 日 2 次、11 日 1 次。也就是说：(1) 信号日的资格层用 `pending_at(d)`（已公告、未生效）就能挡住 99% 的"次日变 ST"；(2) 54%（201/371）是前一天晚上公告、次日生效，**每日流程什么时候抓 `namechange` 决定能不能看到它们**（§7 第 7 条）。这个窗口内信号日恰好落在"公告后、生效前"的 ST 在 59 个抽样日里为 0 只，所以 `pending_at` 在抽样上没有触发，这是抽样密度的结果，不是不会发生。

## 3. (b) 上市日期：能否给出"上市满 250 个交易日"

### 3.1 定义

`sessions_since_listing(d)` = 满足 `list_date <= s <= d` 的上交所开市日个数（上市日算第 1 天）；"次新"为 `< 250`。`trade_cal`（SSE）在 2013-01-04 … 2021-10-29 共 2,143 个开市日，与 `daily` 的不同交易日集合**完全相同**。

### 3.2 `list_date` 的可靠性

- 2017-01-01 … 2021-10-29 间上市的沪深主板 + 创业板证券共 **1,195 只**，首根日 K 线所在交易日与 `list_date` 相同的有 **1,195 只**；放宽到 2013-01-04 起为 1,772 / 1,772；晚于或早于 `list_date`、或根本没有 K 线的：**0**。
- `upstream_defects` 的 `bar_before_listing`（K 线早于登记上市日）在研究窗口内只出现在 `.BJ` 代码上（65,702 行、229 只证券），沪深主板与创业板为 0。
- `list_date` 是"过去的事实"而非快照，不随抓取日移动（`stock_universe.py:16-17`）；注册簿含已退市证券（`L,D`），不是只含幸存者。

### 3.3 能算出多少证券的天数

每个信号日可交易板块有 K 线的证券 2,831 – 4,147 只，其中**没有 `listing` 行**的只有 1–3 只（可算比例 99.9% – 100%）。这 3 只是 `000022.SZ`、`000043.SZ`、`300114.SZ`，在 1990–2021 年的 `stock_basic` 分区里既没有 `listing` 行也没有 `delisting` 行（2022 年起的分区按规则没有读）。它们的共同点由数据直接给出：每一只都有一个"孪生代码"（`001872.SZ`、`001914.SZ`、`302132.SZ`），孪生代码的 `daily` 收盘价与成交额在 1,171 / 1,610 / 2,072 个共同交易日里**逐日完全相同**，孪生代码在注册簿里的 `list_date` 是原上市日（1993-05-05、1994-09-28、2010-08-27）。即：**这是代码迁移，旧代码被注册簿丢掉了，而它迁移前的行情被完整地复制到了新代码名下**（新代码在迁移前就有 K 线，等价于存在性前视）。这与 `stock_universe.py:300` 登记的 `ts_code_is_not_a_permanent_security_identity` 同类，但那里写的是"两条独立证券"，这里实测发现的是"旧代码缺行、新代码带全史"。（"这三者是代码迁移"是由数据推断的，没有对照数据商核实。）

由此得出两条会影响实现的结论：

- **未知上市日应判"不合格"（原因码 `NO_LISTING_ROW`），不要用首根 K 线回退。** 回退会把这 3 个旧代码留在宇宙里，造成同一家公司的重复入选；判不合格恰好去掉了重复的那一份。
- 每个信号日只有 1–3 只证券因此被误剔除，且都是旧代码，量级可忽略。副作用见 §7 第 8 条（`302132.SZ` 的前缀 `302` 不在设计的 `300/301` 白名单里，这家公司在 2013–2021-10 整段被两道门同时挡掉）。

**计数是精确的吗？** 只有 19.2% – 42.1%（中位 32.6%）的证券在 `trade_cal` 起点（2013-01-04）之后上市，其计数是精确的；其余 `list_date` 早于日历起点，只能得到"自 2013-01-04 起的交易日数"这个**下界**，在 2017-01-03 已经 ≥ 972。对阈值 250，下界已经决定了答案，所以判定是精确的。**实现时必须断言日历起点早于信号日 250 个交易日以上**，否则 `trading_days_between` 会抛 `CalendarHorizonError`（`trading_calendar.py:339-360`）或悄悄把老证券算成新证券。

### 3.4 "交易日数"取哪个口径

用交易所开市日（上面的口径）还是用"该证券自己的 K 线根数"？在 5 个抽样日上，两种口径对 `< 250` 的判定不同的证券只有 6 / 14 / 1 / 0 / 0 只（上市后长期停牌者，如 `300612.SZ` 255 个开市日、84 根 K 线）；阈值 ±40 个交易日内的证券 36 / 174 / 35 / 39 / 118 只。**建议用开市日口径**（不依赖单证券的行情完整性，与 `trading_days_between` 的接口一致）；两种口径的差别不会改变本报告的任何结论。

## 4. (c) `strategy_view` 为什么把 `is_st` 写死为 `False`

- `src/openalpha_cn/strategy_view.py:1994-2026`（`_PanelDays.quote`）为每个 `(day, subject)` 构造 `MarketBar`。`:1998` 的条件 `bar is None or limit is None or history is None` 为真时直接返回 `None`；否则一定带 `**published_limit_fields(limit)`（`:2024`）。`backtest/execution.py:373-382` 的 `_price_band` 在有已公布的 `up_limit` / `down_limit` 时先返回它们，**只有缺少已公布涨跌停价时才读 `is_st`**。所以这条路径上 `is_st` 不可能被读到。
- `strategy_view.py` 整个文件对 `namechange`、`NameHistory`、`risk_warning_on` 的引用数为 0（`grep -n "namechange\|NameHistory\|risk_warning" src/openalpha_cn/strategy_view.py` 无命中）：它没有加载名称史，所以也无从判断。第 2022 行的注释 `# Never read: with a published band _price_band uses the exchange's numbers.` 是这个选择的当场说明（引入于 `7672ef9 feat(V2-P6-007)`，2026-09-26）。
- 对照：链一（`factor_view.py:1617`、`shortlist_view.py:1816`）确实从 `namechange` 读 `is_st`，但同样只喂给上面那个"从不触发"的推导。仓库对此自知：`factor_view.py:1382-1386` 写明"a wrong `is_st` moves no verdict today"。

**所以回答是：不是缺来源（来源存在，链一在用），也不是漏了某个筛选（`strategy_view` 本来就没有 ST 筛选，链一也没有），而是 P6 从未设计过 ST / 次新的资格筛选。** 设计 §6 说"链二把 `is_st` 写死为 `False`"属实，但它不是 bug；真正的含义是：**P6 回测与 P7 探索期的宇宙里 ST 和次新都在**。

## 5. Step 2：五个信号日的统计

**口径**：宇宙 = 当日有 K 线（`close > 0`）的沪深主板 + 创业板证券（`600/601/603/605/000/001/002/003/300/301`）。"流动性前 70%" = 近 20 个交易日（含当日）`daily.amount` 均值 ≥ 宇宙 30 分位（与 `diagnostics/p7/small_books_elig.py:31` 相同）。ST = 名称为 `st / star_st / pt`（不含退市整理期）。次新 = `sessions_since_listing < 250`。抽样日取 59 个信号日按位置等距的 5 个。

| 信号日 | 宇宙 | 前 70% | ST | 其中主板 / 创业板 | 退市整理期 | ST ∈ 前 70% | 次新 | 次新 ∈ 前 70% | ST ∩ 次新 | 5% 带宽（主板） |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2017-01-03 | 2,831 | 1,982 | 55 | 55 / 0 | 0 | 26（47.3%） | 226 | 186（82.3%） | 0 | 56 |
| 2018-02-28 | 3,226 | 2,258 | 61 | 61 / 0 | 0 | 21（34.4%） | 368 | 281（76.4%） | 0 | 62 |
| 2019-05-24 | 3,596 | 2,517 | 123 | 123 / 0 | 0 | 49（39.8%） | 110 | 97（88.2%） | 0 | 124 |
| 2020-08-14 | 3,760 | 2,632 | 194 | 194 / 0 | 4 | 13（6.7%） | 157 | 129（82.2%） | 1 | 195 |
| 2021-10-14 | 4,147 | 2,903 | 182 | 158 / 24 | 0 | 58（31.9%） | 322 | 201（62.4%） | 0 | 158 |

"ST ∈ 前 70%"的括号是**ST 名单里**落在前 70% 的比例；下表换成**前 70% 里被剔除的比例**：

| 信号日 | 前 70% 中被 ST 剔除 | 被次新剔除 | 被两者之一剔除 |
|---|---:|---:|---:|
| 2017-01-03 | 1.3% | 9.4% | 10.7% |
| 2018-02-28 | 0.9% | 12.4% | 13.4% |
| 2019-05-24 | 1.9% | 3.9% | 5.8% |
| 2020-08-14 | 0.5% | 4.9% | 5.4% |
| 2021-10-14 | 2.0% | 6.9% | 8.9% |

**59 个信号日汇总（最小 / 中位 / 最大）**：宇宙 2,831 / 3,596 / 4,147；ST 42 / 123 / 208；次新 102 / 237 / 455；ST ∩ 次新 0 / 0 / 1；ST 名单落在前 70% 的比例 6.7% / 25.8% / 61.8%；次新名单落在前 70% 的比例 62.4% / 85.4% / 96.3%；前 70% 中被 ST 剔除 0.4% / 1.0% / 2.8%，被次新剔除 3.7% / 7.5% / 17.1%，被两者之一剔除 4.6% / 8.9% / 18.1%。

**读法**：(1) 流动性过滤**不能**代替这两个过滤：次新股成交活跃，中位 85% 在前 70% 里；ST 股虽然多半不活跃（中位 26% 在前 70%），仍有中位 1.0%（最高 2.8%）的可投资集合是 ST。(2) 次新过滤的量级是 ST 过滤的 7 倍，**真正影响宇宙大小的是次新门槛，不是 ST**。(3) ST 与次新几乎不重叠。(4) 59 个信号日的完整表见附录 B 的 §C。

## 6. 这两个过滤与"幸存者过滤"重叠吗（只看构成，不看收益）

探索期的数字来自 `diagnostics/p7/small_books_elig.py`：只有板块白名单（`:13-14`，`BOARDS=main_chinext`）与流动性前 70%（`:31`）两道门，**没有 ST，也没有上市年龄**。设计 §3 引用的 +6.6% / +7.7% 因此是在含 ST 与次新的宇宙上量出的。为了知道这有多重要，只做了构成统计（附录 A 的 §L；**没有读任何收益**，这不是效果评估）：

| 量（59 个信号日，要求四个价格类因子 `processed` 值齐全） | 最小 / 中位 / 最大 |
|---|---:|
| 可投资集合（流动性前 70%） | 1,807 / 2,493 / 2,845 |
| 幸存者（再剔合成分最差 2 个十分位） | 1,445 / 1,994 / 2,276 |
| 可投资集合里的 ST（名称或 5% 带宽） | 9 / 21 / 80 |
| 可投资集合里的次新（< 250 个交易日，且四个因子齐全，因子窗口为 60 个交易日） | 69 / 135 / 289 |
| 可投资集合里 ST 或次新 | 87 / 160 / 306 |
| 幸存者里 ST 或次新 | 30 / 68 / 143 |

- ST 名单落在被剔除的最差 2 个十分位里的比例：中位 **0.18**（0.00–0.59），随机期望 0.20。**ST 不是"最差十分位"的特征**，幸存者过滤不会替资格层挡掉 ST。
- 次新名单落在被剔除的尾部里的比例：中位 **0.64**（0.46–0.85）。幸存者过滤已经替资格层挡掉了约 2/3 的次新（它们波动与换手高），但仍有约 1/3 留下。
- 幸存者里 ST 或次新占比：中位 **3.6%**，最高 8.1%；可投资集合里：中位 6.8%，最高 14.7%。

这些数字说明两件事：过滤**不是冗余的**（幸存者里仍有 3.6% 的 ST / 次新，随机抽 10 只至少抽到一只的概率约 30%）；探索期数字**不是在最终要用的宇宙上量出的**（§8）。

## 7. 决定与读取接口（实现约束）

**决定：照设计，资格层增加两个原因码，来源与接口如下。**

| 原因码 | 判定 | 来源 / 接口 |
|---|---|---|
| `ST` | 主板：名称为 `st / star_st / pt` **或** 当日 `up_limit` 为 `pre_close` 的 1.05 倍（±0.0051）；创业板：仅名称。另含"已公告、将在下一交易日生效为 ST"（`pending_at`）。 | `load_name_histories(store, years=range(2013, signal_year + 1), as_of=<信号日收盘后>, max_staleness=…)` → `announced_by(d)` → `risk_warning_on(d)` / `pending_at(d)`；`stk_limit` 经现有的 `load_price_limits`（设计 §4.2 已把涨跌停列入每日抓取清单，零额外请求）。 |
| `DELISTING_PERIOD` | 名称为 `RiskWarning.delisting_process` | 同上。0–8 只/日，不是 ST 但同样不应买入。 |
| `NEW_LISTING` | `sessions_since_listing(d) < 250` | `load_stock_universe(...).security(code).listed_on` + `load_trading_calendar(...).trading_days_between(max(listed_on, calendar.horizon.first_date), d)`。 |
| `NO_LISTING_ROW` | 注册簿里没有该代码 | `StockUniverse.security` 抛 `StockUniverseError` 时捕获；**判不合格，不回退到首根 K 线**（§3.3）。 |

**8 条约束：**

1. **名称史读取窗口：2013 年到信号日所在年**，不能只读信号日当年或当年 + 上一年（§2.4(i)）。
2. **不复用 `_risk_warned_on`。** 适配器自己写一个返回三态（`ST` / `ORDINARY` / `UNKNOWN`）的全函数：`NameHistoryHorizonError`、`no_record` 都不应抛出。**建议 `UNKNOWN` 按"普通"处理、并在每日日志里单列计数 `NAME_UNKNOWN_ASSUMED_ORDINARY`**：实测主板上 `before_first` 的 190 个有带宽者全是 10%，`no_record` 里 5% 带宽的全部来自 1 只证券；若改为"未知即不合格"，每个信号日多剔除 0–11 只（`before_first`），而 `no_record` 有 1,516–1,790 只（那是大多数证券，不可能判不合格）。这是需要控制者确认的取舍（§9）。
3. **主板加 5% 带宽并集规则**（见上表）。它补上了窗口内唯一的 50 个遗漏，并且在语料左边界之外仍然成立（`stk_limit` 自 2013-01 起可用）。创业板仍然只有名称一个来源。
4. **用 `pending_at(d)`**：2017-01 … 2021-10 有 99.2%（368/371）的 ST 在生效日前一个交易日或更早已公告。
5. **`delisting_process` 单列为原因码**，不并入 `ST`。
6. **上市年龄用开市日口径**（§3.4），并**断言日历起点至少比信号日早 250 个交易日**；`list_date` 早于日历起点时用起点截断，由断言保证判定精确。
7. **每日抓取清单必须加入 `namechange`。** `scripts/daily_selection.py:216-223` 的 `BASE_TARGETS` 只有 `trade_cal / stock_basic / adj_factor / price / stk_limit / index_daily`；`namechange` 只出现在 `--full-update` 的 `FULL_UPDATE_TARGETS`（`:227-240`），而 `targets_for`（`:953-964`）只在某个已注册因子读它时才加。设计 §4.2 的"名称史"目前**没有被每日流程抓取**。请求数交给 T7 计数。**时序**：54% 的 ST 生效是前一晚公告的（§2.4(iv)），所以 `namechange` 要在公告出来之后抓（每日流程跑得越晚越好，或在次日 09:15 前补抓一次）；实际的公告入库时点本次没有（也不能）测。
8. **板块前缀白名单补 `302`**：`price_limits.py` 把 `300/301/302` 都归创业板；注册簿里有 1 只 `302xxx`（`302132.SZ`，即 `300114.SZ` 的新代码），按设计的 `300/301` 白名单它被排除，而它的旧代码因为没有 `listing` 行也被排除，于是这家公司在整个研究窗口里都不在宇宙中。

## 8. 对方案 A / B / C 的影响

**无阻断性影响。** A / B / C 都接在"资格层 / 选择层 / 评估域"上（设计 §13），而资格层的两个新原因码有可靠来源，接口不变。以下是必须提给控制者的、会实质影响后续的发现：

1. **【影响 Step 0 预登记（`V2-P7-006` 冻结前）、间接影响 A / B / C 的基线】探索期数字没有包含这两个过滤。** 幸存者里中位 3.6%（最高 8.1%）是 ST 或次新（§6）。这不能说明信号不成立，也不能说明成立——T3 没有读收益。但在 `V2-P7-006` 冻结之前，应在"含 ST 与次新过滤的宇宙"上重算一次设计 §3 的 +6.6% / +7.7% 与 N=10 的跟踪误差，否则预登记的期望值对应的宇宙不是实际买入的宇宙。**这是建议，不是本任务做的事。**
2. **【方案 A】** 否决层加在"资格层"，与 `ST` / `NEW_LISTING` 同表同优先级断言（设计 §9 A）。需要把 `UNKNOWN` 名称状态（§7 第 2 条）与每个原因码的优先级写进断言；`ST` 判定含主板 5% 带宽并集，所以 A 的否决原因码**不应**依赖 `risk_warning_on` 单独一个来源。无其他影响。
3. **【方案 B】** 模型分数替换选择层，评估域是幸存者内部。**训练样本必须施加同一套资格**（`ST`、`DELISTING_PERIOD`、`NEW_LISTING`、`NO_LISTING_ROW`），否则模型会在永远买不到的 ST 与次新上学习（次新 64% 在最差尾部，分布与可买集合明显不同）。B 若训练窗口起点早于 2017，名称史的左删失**更严重**，本次没有量，必须靠 §7 第 3 条的 5% 带宽并集补，而创业板在该段没有补救手段（2020 年前创业板没有 ST 体制，影响有限，但未量）。
4. **【方案 C】** 前向登记"每个可投资证券一个 score"，评估在幸存者内部。无阻断；唯一要求是**登记时的宇宙定义**（资格规则含并集、`UNKNOWN` 的处置、`NO_LISTING_ROW`）随 `V2-P7-006` 一起冻结，否则 C 的增量 IC 的分母会随实现细节漂移。
5. **【设计 §4.2 / T7】** 每日流程没有抓 `namechange`（§7 第 7 条）；T7 的请求数要加上它，且要定时序。
6. **【设计 §6】** "ST：排除（来源待 T3）"可改写为"来源：`namechange`（读取窗口 2013 起）∪ 主板 5% 带宽"；"次新门槛：上市满 250 个交易日"可去掉"假设，待 T3"，口径写明为"开市日口径，含上市日"。

## 9. 限制与未验证项（照实写）

1. **创业板 ST 没有独立见证。** 只做了名称语法与 `change_reason` 的内部一致性检查（§2.2）。创业板按名称为 ST 的证券·信号日合计 169 个，规模小，但这是整个结论里最弱的一环。
2. **2017 年之前未测。** 语料左边界是 2013 年，2013–2016 的 ST 遗漏率必然高于 2017-01 的 1/56，本报告没有量，也没有据以下结论。
3. **公告入库时点未测。** `available_time` 把每条更名定在公告日 0 点（`panel_ingest.py:1603-1611`），这是乐观下界；真实公告多在收盘后。这影响的是每日流程的时序（§7 第 7 条），不影响研究窗口里的计数。
4. **代码迁移的解释是推断。** §3.3 由"两个代码的价格与成交额逐日相同"与注册簿缺行推出；没有对照数据商核实，2022 起的注册簿按规则没有读。
5. **`UNKNOWN`→普通 是一个取舍，不是测出来的真理。** 依据是主板上 190 个可核对者全部不是 ST，创业板没有核对手段（§7 第 2 条）。
6. **这是资格层的可行性验证，不是研究有效性证据。** 5 个抽样日 + 59 个信号日只用于计数；"已经是 ST"的过滤挡不住"此后才变 ST"的持仓风险（窗口内 371 次，约每月 6.4 次），那部分由持仓策略的 `EXIT_ELIGIBILITY_LOST` 承担。
7. **只覆盖"有 K 线"的证券。** 全天停牌的证券没有 `daily` 行，不在上述宇宙里；停牌过滤是设计里的另一个原因码，不属于 T3。

---

## 附录 A：可复现脚本

下面是 `~/openalpha-research/diagnostics/p7/t3_st_newlist.py` 的全文。在 worktree 里运行：`PYTHONPATH="$PWD/src" <repo .venv python> ~/openalpha-research/diagnostics/p7/t3_st_newlist.py`（约 1–2 分钟，纯只读）。

```python
"""T3 (V2-P7-005, Phase 0 / Task 0.1): where do 'is this stock ST' and 'trading days since listing' come from?

READ-ONLY, research window only. Every table is bounded to <= 2021-10-31 (signals 2017-01-03 .. 2021-10-14).
Opens the parquet partitions directly (no PanelStore, hence no catalog / lock / gate writes) and rebuilds the
domain objects with the repo's own pure functions, so the special-treatment grammar is the repository's:
    openalpha_cn.domain.name_history.name_histories_from_panel_rows / risk_warning_of / NameHistory
No network, no vendor calls, no .env.

Run (from the worktree):
    PYTHONPATH="$PWD/src" <repo .venv python> ~/openalpha-research/diagnostics/p7/t3_st_newlist.py

Definitions used throughout (each is stated again in the report):
  tradable boards  = SH 600/601/603/605 + SZ 000/001/002/003/300/301   (design 2026-10-13 sec. 6)
  universe(d)      = tradable-board securities with a daily bar (close > 0) on signal day d
  liquidity top70  = 20-session mean of daily.amount (<= d, inclusive) >= 30th percentile of universe(d)
                     (same cut as diagnostics/p7/small_books_elig.py)
  name-ST(d)       = RiskWarning in {st, star_st, pt} from NameHistory.announced_by(d).risk_warning_on(d)
  band-ST(d)       = main board only: stk_limit.up_limit == 1.05 * daily.pre_close (+-0.0051), i.e. a published
                     5% band, the exchange's own witness for ST / *ST / S-marked names before 2025-07
  sessions_since_listing(d) = #SSE open sessions s with list_date <= s <= d   (new listing: < 250)
Signal days = every 20th session in [2017-01-03, 2021-10-29], as in decile.py (59 days).
"""

import bisect
import collections
import datetime as dt
import os
import sys

import duckdb
import numpy as np

from openalpha_cn.domain.name_history import (
    NameHistoryHorizonError,
    RiskWarning,
    name_histories_from_panel_rows,
    risk_warning_of,
)

R = os.path.expanduser("~/openalpha-research/panel")
END = "2021-10-31"
TRADABLE = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301")
MAIN = ("600", "601", "603", "605", "000", "001", "002", "003")
THRESH = 250
ST_SET = (RiskWarning.st, RiskWarning.star_st, RiskWarning.pt)

con = duckdb.connect()  # in-memory
con.execute("set timezone='UTC'")


def glob(ds, yrs):
    return "[" + ",".join(f"'{R}/{ds}/{y}/data.parquet'" for y in yrs) + "]"


iso = dt.date.fromisoformat

# ------------------------------------------------------------------ load (all bounded by END)
sessions = [
    r[0]
    for r in con.execute(f"""select cal_date from read_parquet({glob("trade_cal", range(2013, 2022))})
    where subject='SSE' and is_open and cal_date <= '{END}' order by cal_date""").fetchall()
]
sess_idx = {d: i for i, d in enumerate(sessions)}
con.execute(f"""create temp table dd as select subject, trade_date, pre_close, close, amount from (
  select *, row_number() over (partition by subject, trade_date order by revision_time desc) r
  from read_parquet({glob("daily", range(2013, 2022))}) where trade_date <= '{END}') where r=1 and close>0""")
con.execute(f"""create temp table lim as select subject, trade_date, up_limit from (
  select *, row_number() over (partition by subject, trade_date order by revision_time desc) r
  from read_parquet({glob("stk_limit", range(2013, 2022))}) where trade_date <= '{END}') where r=1""")
bar_days = [r[0] for r in con.execute("select distinct trade_date from dd order by 1").fetchall()]
sb = con.execute(f"""select subject, lifecycle_event, lifecycle_date from read_parquet({glob("stock_basic", range(1990, 2022))})
    where lifecycle_date <= '{END}'""").fetchall()
list_date = {s: iso(d) for s, e, d in sb if e == "listing"}
delist_date = {s: iso(d) for s, e, d in sb if e == "delisting"}
nc_rows = con.execute(f"""select subject, name, effective_date, announcement_date, change_reason
    from read_parquet({glob("namechange", range(2013, 2022))}) where announcement_date <= '{END}'""").fetchall()
histories = name_histories_from_panel_rows(nc_rows)
first_bar = dict(con.execute("select subject, min(trade_date) from dd group by 1").fetchall())
sig_all = [d for d in bar_days if "2017-01-03" <= d <= "2021-10-29"][::20]
pick = sorted({sig_all[round(i * (len(sig_all) - 1) / 4)] for i in range(5)})


def board_ok(code):
    return code[:3] in TRADABLE and code.endswith((".SH", ".SZ"))


def sessions_since_listing(code, d):
    """(n, exact) or None. exact=False: listing predates the calendar start, n is only a lower bound."""
    ld = list_date.get(code)
    if ld is None:
        return None
    if ld.isoformat() < sessions[0]:
        return (sess_idx[d] + 1, False)
    return (sess_idx[d] - bisect.bisect_left(sessions, ld.isoformat()) + 1, True)


def name_state(code, d, hmap=None):
    """'st' | 'delisting' | 'none' | 'no_record' (no row announced by d) | 'before_first' (rows exist, none in force)."""
    h = (histories if hmap is None else hmap).get(code)
    if h is None:
        return "no_record", False
    day = iso(d)
    try:
        v = h.announced_by(day)
    except NameHistoryHorizonError:
        return "no_record", False
    pend = any(r.risk_warning in ST_SET for r in v.pending_at(day))
    try:
        w = v.risk_warning_on(day)
    except NameHistoryHorizonError:
        return "before_first", pend
    if w in ST_SET:
        return "st", pend
    if w is RiskWarning.delisting_process:
        return "delisting", pend
    return "none", pend


def band_state(pre, up):
    if up is None or pre is None or pre <= 0:
        return "na"
    if abs(up - pre * 1.05) <= 0.0051 and abs(up - pre * 1.10) > 0.0051:
        return "5"
    if abs(up - pre * 1.10) <= 0.0051:
        return "10"
    return "other"


def analyse(d):
    i = bar_days.index(d)
    d0 = bar_days[max(0, i - 19)]
    rows = con.execute(
        """
      with w as (select subject, avg(amount) a20, count(*) nb from dd where trade_date between ? and ? group by 1)
      select t.subject, w.a20, w.nb, t.pre_close, l.up_limit
      from dd t join w using(subject) left join lim l on l.subject=t.subject and l.trade_date=t.trade_date
      where t.trade_date = ?""",
        [d0, d, d],
    ).fetchall()
    xs = [r for r in rows if board_ok(r[0])]
    cut = np.quantile(np.array([r[1] for r in xs]), 0.30)
    out = []
    for code, a, nb, pre, up in xs:
        ns, pend = name_state(code, d)
        sl = sessions_since_listing(code, d)
        main = code[:3] in MAIN
        out.append(
            dict(
                code=code,
                top70=bool(a >= cut),
                name=ns,
                pend=pend,
                main=main,
                band=band_state(pre, up) if main else "-",
                nsess=None if sl is None else sl[0],
                exact=None if sl is None else sl[1],
                nb=nb,
            )
        )
    return out


def is_new(o):
    return o["nsess"] is not None and o["nsess"] < THRESH


def pct(a, b):
    return f"{100.0 * a / b:.1f}%" if b else "n/a"


# ====================================================================== A. coverage of the sources
print("=== A. source coverage (<= %s)" % END)
print("namechange partitions on disk:", sorted(os.listdir(f"{R}/namechange")))
print(
    "namechange rows read (announced <= END):",
    len(nc_rows),
    "| securities with a history:",
    len(histories),
    "| earliest effective/announced:",
    con.execute(
        f"select min(effective_date), min(announcement_date) from read_parquet({glob('namechange', range(2013, 2022))})"
    ).fetchone(),
)
print(
    "rows by announcement year:",
    con.execute(
        f"select substr(announcement_date,1,4), count(*) from read_parquet({glob('namechange', range(2013, 2022))}) where announcement_date <= '{END}' group by 1 order by 1"
    ).fetchall(),
)
ever_st = {c for c, h in histories.items() if any(r.risk_warning in ST_SET for r in h.records)}
print(
    "securities whose history ever carries an ST/*ST/PT name (2013..END):",
    len(ever_st),
    "| of which tradable boards:",
    sum(board_ok(c) for c in ever_st),
)
print(
    "stock_basic listing rows <= END:",
    len(list_date),
    "| delisting rows:",
    len(delist_date),
    "| tradable-board listings:",
    sum(board_ok(c) for c in list_date),
)
print(
    "trade_cal SSE sessions 2013-01..2021-10:",
    len(sessions),
    "| daily bar dates equal:",
    sessions == bar_days,
)
print("signal days:", len(sig_all), sig_all[0], "..", sig_all[-1], "| 5 sampled:", pick)

# ====================================================================== B/C. per-day tables
per_day = []
confusion = collections.Counter()
state_share = []
chinext_by_year = collections.Counter()
for d in sig_all:
    out = analyse(d)
    n = len(out)
    top = [o for o in out if o["top70"]]
    st = [o for o in out if o["name"] == "st"]
    new = [o for o in out if is_new(o)]
    either = [o for o in out if o["name"] == "st" or is_new(o)]
    per_day.append(
        dict(
            d=d,
            n=n,
            top=len(top),
            st=len(st),
            st_main=sum(o["main"] for o in st),
            st_chinext=sum(not o["main"] for o in st),
            delisting=sum(o["name"] == "delisting" for o in out),
            st_top=sum(o["top70"] for o in st),
            new=len(new),
            new_top=sum(o["top70"] for o in new),
            stnew=sum(1 for o in st if is_new(o)),
            top_either=sum(o["top70"] for o in either),
            band5=sum(o["band"] == "5" for o in out),
            pend_st=sum(1 for o in out if o["pend"] and o["name"] != "st"),
            nolist=sum(o["nsess"] is None for o in out),
            exact=sum(bool(o["exact"]) for o in out),
            no_record=sum(o["name"] == "no_record" for o in out),
            before_first=sum(o["name"] == "before_first" for o in out),
            none=sum(o["name"] == "none" for o in out),
        )
    )
    for o in out:
        if o["main"]:
            confusion[
                (
                    {
                        "st": "ST",
                        "delisting": "DELISTING",
                        "none": "NONE",
                        "no_record": "NO_RECORD",
                        "before_first": "BEFORE_FIRST",
                    }[o["name"]],
                    o["band"],
                )
            ] += 1
        else:
            chinext_by_year[(d[:4], o["name"])] += 1

print("\n=== B. five sampled signal days")
hdr = [
    "d",
    "n",
    "top",
    "st",
    "st_main",
    "st_chinext",
    "delisting",
    "st_top",
    "new",
    "new_top",
    "stnew",
    "top_either",
    "band5",
    "nolist",
]
print(*hdr, sep="\t")
for p in per_day:
    if p["d"] in pick:
        print(*[p[h] for h in hdr], sep="\t")
print(
    "share of ST names inside liquidity top70 / share of new-listing names inside top70 / share of top70 removed by ST, new, either:"
)
for p in per_day:
    if p["d"] in pick:
        print(
            p["d"],
            pct(p["st_top"], p["st"]),
            pct(p["new_top"], p["new"]),
            pct(p["st_top"], p["top"]),
            pct(p["new_top"], p["top"]),
            pct(p["top_either"], p["top"]),
        )

print("\n=== C. all 59 signal days: min / median / max")
for k in [
    "n",
    "top",
    "st",
    "st_main",
    "st_chinext",
    "delisting",
    "new",
    "stnew",
    "nolist",
    "pend_st",
    "before_first",
    "no_record",
    "none",
]:
    a = np.array([p[k] for p in per_day], dtype=float)
    print(f"{k:13s} {a.min():8.0f} {np.median(a):8.0f} {a.max():8.0f}")
for name, f in [
    ("ST share inside top70", lambda p: p["st_top"] / p["st"]),
    ("new share inside top70", lambda p: p["new_top"] / p["new"]),
    ("top70 removed by ST", lambda p: p["st_top"] / p["top"]),
    ("top70 removed by new", lambda p: p["new_top"] / p["top"]),
    ("top70 removed by ST or new", lambda p: p["top_either"] / p["top"]),
    ("names with computable listing-day count", lambda p: 1 - p["nolist"] / p["n"]),
    ("names with EXACT (listing>=2013-01-04) count", lambda p: p["exact"] / p["n"]),
]:
    a = np.array([f(p) for p in per_day])
    print(f"{name:46s} {a.min():.3f} {np.median(a):.3f} {a.max():.3f}")
print("full table:")
print(*hdr, sep="\t")
for p in per_day:
    print(*[p[h] for h in hdr], sep="\t")

print("\n=== D. main-board name state x published band, subject-days over the 59 signal days")
bands = ["5", "10", "other", "na"]
print("%-14s" % "name\\band", *["%8s" % b for b in bands], "%8s" % "total")
for nm in ["ST", "DELISTING", "NONE", "NO_RECORD", "BEFORE_FIRST"]:
    row = [confusion[(nm, b)] for b in bands]
    print("%-14s" % nm, *["%8d" % v for v in row], "%8d" % sum(row))
print(
    "ChiNext (band is not informative) name states by signal year:", sorted(chinext_by_year.items())
)

print(
    "distinct main-board securities that had a published 5% band on >=1 signal day:",
    len({o["code"] for d in sig_all for o in analyse(d) if o["main"] and o["band"] == "5"}),
    "| of which ST by name on >=1 such day:",
    len(
        {
            o["code"]
            for d in sig_all
            for o in analyse(d)
            if o["main"] and o["band"] == "5" and o["name"] == "st"
        }
    ),
)

# ====================================================================== K. does the announcement-year window matter?
print(
    "\n=== K. name-ST count on a signal day when only some announcement years are read (main board; band-5 count beside it)"
)
for d in pick:
    y = int(d[:4])
    out = analyse(d)
    codes = [o["code"] for o in out if o["main"]]
    res = {}
    for label, yrs in [
        ("signal year only", {y}),
        ("signal year + previous", {y - 1, y}),
        ("2013..signal year", set(range(2013, y + 1))),
    ]:
        hm = name_histories_from_panel_rows([r for r in nc_rows if int(r[3][:4]) in yrs])
        res[label] = sum(1 for c in codes if name_state(c, d, hm)[0] == "st")
    print(d, "band-5:", sum(o["band"] == "5" for o in out), res)

# ====================================================================== E. the disagreement cases
print("\n=== E. every disagreement between name-ST and band-5 (main board)")
who = collections.defaultdict(list)
for d in sig_all:
    for o in analyse(d):
        if o["main"] and (
            (o["name"] != "st" and o["band"] == "5") or (o["name"] == "st" and o["band"] != "5")
        ):
            who[(o["code"], o["name"], o["band"])].append((d, o["nb"]))
for k, v in who.items():
    print(k, "days:", len(v), v[0], "..", v[-1])
h = histories.get("600182.SH")
if h:
    print(
        "600182.SH records:",
        [
            (r.effective_from.isoformat(), r.announced_on.isoformat(), r.name, r.change_reason)
            for r in h.records
        ],
    )

# ====================================================================== F. list_date: coverage + reconciliation
print("\n=== F1. tradable-board securities with a bar on a signal day but no listing row (<= END)")
nolist = collections.defaultdict(list)
for d in sig_all:
    for o in analyse(d):
        if o["nsess"] is None:
            nolist[o["code"]].append(d)
for c, ds in nolist.items():
    lb = con.execute("select max(trade_date), count(*) from dd where subject=?", [c]).fetchone()
    print(
        c,
        "signal days",
        len(ds),
        ds[0],
        "..",
        ds[-1],
        "| first bar",
        first_bar.get(c),
        "| last bar/count",
        lb,
        "| delisting row",
        delist_date.get(c),
        "| name history",
        c in histories,
    )

print(
    "\n=== F1b. every pair of securities whose daily (close, amount) agree on >= 30 common sessions (code migrations)"
)
for r in con.execute("""
  select a.subject, b.subject, count(*) n, min(a.trade_date), max(a.trade_date)
  from dd a join dd b on a.trade_date=b.trade_date and a.close=b.close and a.amount=b.amount and a.subject<b.subject
  where a.amount>0 group by 1,2 having count(*) >= 30 order by n desc""").fetchall():
    print(r, "| list_date:", list_date.get(r[0]), list_date.get(r[1]))

print("\n=== F2. list_date vs first daily bar, tradable-board listings 2013-01-04 .. 2021-10-29")
diffs = []
for c, ld in list_date.items():
    if board_ok(c) and iso("2013-01-04") <= ld <= iso("2021-10-29"):
        fb = first_bar.get(c)
        diffs.append(
            None if fb is None else sess_idx[fb] - bisect.bisect_left(sessions, ld.isoformat())
        )
strict = [
    None
    if first_bar.get(c) is None
    else sess_idx[first_bar[c]] - bisect.bisect_left(sessions, ld.isoformat())
    for c, ld in list_date.items()
    if board_ok(c) and iso("2017-01-01") <= ld <= iso("2021-10-29")
]
print(
    "listings dated 2017-01-01..2021-10-29 only:",
    len(strict),
    "| first bar == list_date session:",
    sum(x == 0 for x in strict),
)
print(
    "all listings from 2013-01-04 (2013-16 used only as extra evidence):",
    len(diffs),
    "| no bar at all:",
    sum(x is None for x in diffs),
    "| first bar == list_date session:",
    sum(x == 0 for x in diffs),
    "| first bar later:",
    sum(x is not None and x > 0 for x in diffs),
    "| earlier:",
    sum(x is not None and x < 0 for x in diffs),
)
print(
    "upstream_defects (research window) -- bar_before_listing by board:",
    con.execute(f"""select case when subject like '%.BJ' then 'BJ' when subject like '688%' then 'STAR' when substr(subject,1,3) in ('300','301') then 'ChiNext' else 'main' end b,
         count(*), count(distinct subject) from read_parquet({glob("upstream_defects", range(2013, 2022))})
         where defect_kind='bar_before_listing' and trade_date <= '{END}' group by 1""").fetchall(),
)

print(
    "\n=== F3. 250-session threshold: sessions-since-listing vs own-bars-since-listing, 5 sampled days"
)
for d in pick:
    flips, near = [], 0
    for o in analyse(d):
        if o["nsess"] is None or not o["exact"]:
            continue
        c = o["code"]
        nb_ = con.execute(
            "select count(*) from dd where subject=? and trade_date between ? and ?",
            [c, list_date[c].isoformat(), d],
        ).fetchone()[0]
        near += THRESH - 40 <= o["nsess"] < THRESH + 40
        if (o["nsess"] < THRESH) != (nb_ < THRESH):
            flips.append((c, o["nsess"], nb_))
    print(
        d,
        "verdict differs between the two definitions:",
        len(flips),
        flips[:4],
        "| names within +-40 sessions of the threshold:",
        near,
    )

# ====================================================================== G. unresolved names
print(
    "\n=== G. 'before_first' (rows announced by d exist, none in force on d), 59 signal days, tradable boards"
)
bf = collections.defaultdict(list)
tot = 0
for d in sig_all:
    out = analyse(d)
    tot += len(out)
    for o in out:
        if o["name"] == "before_first":
            bf[o["code"]].append(d)
print("subject-days:", sum(len(v) for v in bf.values()), "of", tot, "| securities:", len(bf))
kinds = collections.Counter()
for c in bf:
    hh = histories[c]
    kinds[
        "listed before first record"
        if list_date.get(c) and list_date[c] < hh.records[0].effective_from
        else "other"
    ] += 1
    kinds[
        "listed pre-2013"
        if list_date.get(c) and list_date[c] < iso("2013-01-04")
        else "listed 2013+"
    ] += 1
print(dict(kinds))
for c in list(bf)[:5]:
    r0 = histories[c].records[0]
    print(
        " ",
        c,
        bf[c][:2],
        "first record effective",
        r0.effective_from,
        "announced",
        r0.announced_on,
        r0.name,
        r0.change_reason,
        "| list",
        list_date.get(c),
    )

# ====================================================================== H. reason witness
print("\n=== H. change_reason witness against the grammar (rows announced 2013..2021-10)")
imposing = {"ST", "*ST", "高风险警示", "从ST变为*ST", "撤消*ST并实行ST", "叠加ST", "叠加*ST", "PT"}
lifting = {"撤销ST", "撤销*ST", "撤销高风险警示", "撤销PT"}
bad = collections.Counter()
for subj, name, eff, ann, reason in nc_rows:
    w = risk_warning_of(name)
    if reason in imposing and w in (RiskWarning.none, RiskWarning.delisting_process):
        bad["imposing reason, ordinary name"] += 1
    if reason in lifting and w in ST_SET:
        bad["lifting reason, still-marked name"] += 1
    if reason == "其他" and w in ST_SET:
        bad["reason 其他 but ST name (a rename that kept its marker)"] += 1
print(dict(bad), "| reasons:", dict(collections.Counter(r[4] for r in nc_rows)))

# ====================================================================== I. ST entries: how early is the signal day told?
print(
    "\n=== I. ST *entries* effective 2017-01-01..2021-10-29 (name none -> ST), tradable boards: known at the previous session's signal?"
)
lead_known = lead_unknown = 0
leads = collections.Counter()
rows_ = []
for code, hh in histories.items():
    if not board_ok(code):
        continue
    prev_state = None
    for r in hh.records:
        cur = r.risk_warning
        if (
            cur in ST_SET
            and (prev_state is None or prev_state not in ST_SET)
            and "2017-01-01" <= r.effective_from.isoformat() <= "2021-10-29"
        ):
            e = r.effective_from.isoformat()
            j = bisect.bisect_left(sessions, e)  # first session >= effective date
            prev_sess = sessions[j - 1] if j > 0 else None
            known = prev_sess is not None and r.announced_on.isoformat() <= prev_sess
            lead_known += known
            lead_unknown += not known
            leads[(r.effective_from - r.announced_on).days] += 1
            rows_.append((code, r.name, r.announced_on.isoformat(), e, known))
        prev_state = cur
print(
    "entries:",
    lead_known + lead_unknown,
    "| announced on/before the previous session (a signal on that day could see it):",
    lead_known,
    "| announced after it (a signal could not):",
    lead_unknown,
)
print("calendar-day lead (effective - announced) distribution:", sorted(leads.items())[:10])
print("examples of entries a signal could not see:", [x for x in rows_ if not x[4]][:5])

# ====================================================================== J. left edge
print(
    "\n=== J. 2017-01-03: year of the record in force for each main-board name with a published 5% band"
)
d0 = sig_all[0]
yrs = collections.Counter()
for o in analyse(d0):
    if o["main"] and o["band"] == "5":
        hh = histories.get(o["code"])
        try:
            yrs[hh.announced_by(iso(d0)).record_on(iso(d0)).effective_from.isoformat()[:4]] += 1
        except Exception:
            yrs["unresolved"] += 1
print(sorted(yrs.items()))

# ====================================================================== L. composition only (NO returns are read here)
# Question: do the ST / new-listing exclusions overlap with the "drop the worst two composite deciles" survivor
# filter that the exploratory +6.6% / +7.7% numbers were measured under?  Those numbers
# (small_books_elig.py) were computed WITHOUT any ST or listing-age exclusion.  Composition, not performance.
print(
    "\n=== L. composition of the investable set and of the survivor tail (no returns) -- exploratory, not evidence"
)
FACS = (
    "residual_vol_60",
    "return_vol_60",
    "turnover_60",
    "downside_vol_60",
)  # all lower-is-better in small_books_elig.py
fvals = {}
for f in FACS:
    rows = con.execute(f"""select subject, strftime(timezone('Asia/Shanghai', event_time), '%Y-%m-%d') td, value from (
        select *, row_number() over (partition by subject, event_time order by revision_time desc) r
        from read_parquet({glob("factor_proc_" + f + "_v1", range(2016, 2022))})) where r=1 and value is not null""").fetchall()
    for s, td, v in rows:
        if td <= END:
            fvals.setdefault(td, {}).setdefault(f, {})[s] = -v  # sign: lower vol/turnover is better
comp_rows = []
for d in sig_all:
    out = {o["code"]: o for o in analyse(d)}
    have = [c for c in out if all(c in fvals.get(d, {}).get(f, {}) for f in FACS)]
    if len(have) < 1000:
        continue
    # liquidity cut over the factor-complete tradable names, exactly as small_books_elig.py does
    a20 = {
        r[0]: r[1]
        for r in con.execute(
            """select subject, avg(amount) from dd where trade_date between ? and ? group by 1""",
            [bar_days[max(0, bar_days.index(d) - 19)], d],
        ).fetchall()
    }
    names = [c for c in have if c in a20]
    amt = np.array([a20[c] for c in names])
    comp = np.mean([[fvals[d][f][c] for c in names] for f in FACS], axis=0)
    inv = np.where(amt >= np.quantile(amt, 0.30))[0]
    cut = np.quantile(comp[inv], 0.20)
    surv = {names[j] for j in inv if comp[j] >= cut}
    invs = {names[j] for j in inv}
    tail = invs - surv
    isst = lambda c: out[c]["name"] == "st" or out[c]["band"] == "5"
    isnew = lambda c: is_new(out[c])
    comp_rows.append(
        dict(
            d=d,
            inv=len(invs),
            tail=len(tail),
            surv=len(surv),
            st_inv=sum(isst(c) for c in invs),
            new_inv=sum(isnew(c) for c in invs),
            st_tail=sum(isst(c) for c in tail),
            new_tail=sum(isnew(c) for c in tail),
            either_inv=sum(isst(c) or isnew(c) for c in invs),
            either_surv=sum(isst(c) or isnew(c) for c in surv),
        )
    )
print("signal days with a factor-complete cross section:", len(comp_rows))
for k in ("inv", "surv", "st_inv", "new_inv", "either_inv", "either_surv"):
    a = np.array([r[k] for r in comp_rows], dtype=float)
    print(f"{k:12s} min {a.min():.0f} median {np.median(a):.0f} max {a.max():.0f}")
sh = lambda num, den: np.array([r[num] / r[den] for r in comp_rows if r[den]])
print(
    "share of ST names (investable) that fall in the dropped worst-2-decile tail: median %.2f (min %.2f max %.2f)"
    % (
        np.median(sh("st_tail", "st_inv")),
        sh("st_tail", "st_inv").min(),
        sh("st_tail", "st_inv").max(),
    )
)
print(
    "share of new (<250) names (investable) that fall in the dropped tail: median %.2f (min %.2f max %.2f)"
    % (
        np.median(sh("new_tail", "new_inv")),
        sh("new_tail", "new_inv").min(),
        sh("new_tail", "new_inv").max(),
    )
)
print(
    "share of the SURVIVOR set that is ST or new (what the eligibility filter would still remove): median %.3f max %.3f"
    % (np.median(sh("either_surv", "surv")), sh("either_surv", "surv").max())
)
print(
    "share of the investable set that is ST or new: median %.3f max %.3f"
    % (np.median(sh("either_inv", "inv")), sh("either_inv", "inv").max())
)
print("for reference, a name drawn at random would be in the tail with probability 0.20")
```

## 附录 B：脚本原始输出

```text
=== A. source coverage (<= 2021-10-31)
namechange partitions on disk: ['2013', '2014', '2015', '2016', '2017', '2018', '2019', '2020', '2021', '2022', '2023', '2024', '2025', '2026']
namechange rows read (announced <= END): 4228 | securities with a history: 3072 | earliest effective/announced: ('2013-01-14', '2013-01-08')
rows by announcement year: [('2013', 178), ('2014', 312), ('2015', 441), ('2016', 478), ('2017', 649), ('2018', 321), ('2019', 437), ('2020', 694), ('2021', 718)]
securities whose history ever carries an ST/*ST/PT name (2013..END): 476 | of which tradable boards: 476
stock_basic listing rows <= END: 4739 | delisting rows: 146 | tradable-board listings: 4317
trade_cal SSE sessions 2013-01..2021-10: 2143 | daily bar dates equal: True
signal days: 59 2017-01-03 .. 2021-10-14 | 5 sampled: ['2017-01-03', '2018-02-28', '2019-05-24', '2020-08-14', '2021-10-14']

=== B. five sampled signal days
d	n	top	st	st_main	st_chinext	delisting	st_top	new	new_top	stnew	top_either	band5	nolist
2017-01-03	2831	1982	55	55	0	0	26	226	186	0	212	56	3
2018-02-28	3226	2258	61	61	0	0	21	368	281	0	302	62	2
2019-05-24	3596	2517	123	123	0	0	49	110	97	0	146	124	2
2020-08-14	3760	2632	194	194	0	4	13	157	129	1	141	195	1
2021-10-14	4147	2903	182	158	24	0	58	322	201	0	259	158	1
share of ST names inside liquidity top70 / share of new-listing names inside top70 / share of top70 removed by ST, new, either:
2017-01-03 47.3% 82.3% 1.3% 9.4% 10.7%
2018-02-28 34.4% 76.4% 0.9% 12.4% 13.4%
2019-05-24 39.8% 88.2% 1.9% 3.9% 5.8%
2020-08-14 6.7% 82.2% 0.5% 4.9% 5.4%
2021-10-14 31.9% 62.4% 2.0% 6.9% 8.9%

=== C. all 59 signal days: min / median / max
n                 2831     3596     4147
top               1982     2517     2903
st                  42      123      208
st_main             42      123      200
st_chinext           0        0       24
delisting            0        0        8
new                102      237      455
stnew                0        0        1
nolist               1        2        3
pend_st              0        0        0
before_first         0        3       11
no_record         1516     1677     1790
none               978     1788     2448
ST share inside top70                          0.067 0.258 0.618
new share inside top70                         0.624 0.854 0.963
top70 removed by ST                            0.004 0.010 0.028
top70 removed by new                           0.037 0.075 0.171
top70 removed by ST or new                     0.046 0.089 0.181
names with computable listing-day count        0.999 0.999 1.000
names with EXACT (listing>=2013-01-04) count   0.192 0.326 0.421
full table:
d	n	top	st	st_main	st_chinext	delisting	st_top	new	new_top	stnew	top_either	band5	nolist
2017-01-03	2831	1982	55	55	0	0	26	226	186	0	212	56	3
2017-02-07	2888	2021	55	55	0	0	20	275	214	0	234	56	3
2017-03-07	2910	2037	53	53	0	0	17	303	253	0	270	54	3
2017-04-06	2941	2059	42	42	0	0	15	334	307	0	322	43	3
2017-05-05	2966	2076	55	55	0	0	34	365	276	0	310	56	3
2017-06-06	2975	2082	48	48	0	1	23	390	315	0	338	48	3
2017-07-04	3025	2117	49	49	0	1	17	407	302	0	319	50	3
2017-08-01	3054	2138	49	49	0	1	18	425	301	0	319	49	3
2017-08-29	3116	2181	49	49	0	0	24	434	294	0	318	49	3
2017-09-26	3176	2223	55	55	0	0	22	442	326	0	348	56	3
2017-10-31	3214	2250	54	54	0	0	17	450	371	0	388	55	3
2017-11-28	3233	2263	52	52	0	0	22	455	387	0	409	53	2
2017-12-26	3248	2273	56	56	0	0	26	441	338	0	364	57	2
2018-01-24	3279	2295	62	62	0	0	20	409	320	0	340	63	2
2018-02-28	3226	2258	61	61	0	0	21	368	281	0	302	62	2
2018-03-28	3242	2269	58	58	0	0	11	345	291	0	302	59	2
2018-04-27	3271	2290	67	67	0	0	21	309	290	0	311	68	2
2018-05-29	3308	2315	66	66	0	0	13	278	264	0	277	67	2
2018-06-27	3312	2318	68	67	1	3	14	256	245	0	259	68	2
2018-07-25	3388	2371	73	72	1	0	9	237	226	0	235	73	3
2018-08-22	3426	2398	76	76	0	0	18	210	179	0	197	77	3
2018-09-19	3452	2416	76	76	0	0	27	190	170	0	197	77	3
2018-10-25	3472	2430	79	79	0	0	21	164	157	0	178	80	3
2018-11-22	3525	2467	73	73	0	1	23	135	127	0	150	74	3
2018-12-20	3550	2485	76	76	0	1	25	115	110	0	135	77	3
2019-01-21	3560	2492	80	80	0	0	25	110	105	1	129	81	2
2019-02-25	3570	2499	82	82	0	0	21	103	99	1	119	83	2
2019-03-25	3565	2495	80	80	0	0	19	102	96	1	114	81	2
2019-04-23	3585	2509	83	83	0	0	32	105	94	0	126	84	1
2019-05-24	3596	2517	123	123	0	0	49	110	97	0	146	124	2
2019-06-24	3606	2524	128	128	0	3	33	109	105	0	138	129	2
2019-07-22	3616	2531	129	129	0	0	30	110	101	1	131	130	2
2019-08-19	3624	2537	129	129	0	0	31	118	111	1	141	130	2
2019-09-17	3637	2546	131	131	0	2	20	121	115	1	135	132	2
2019-10-22	3639	2547	129	129	0	3	21	118	113	0	134	130	1
2019-11-19	3654	2558	127	127	0	3	20	124	113	0	133	128	2
2019-12-17	3665	2565	129	129	0	2	33	131	122	0	155	130	1
2020-01-15	3670	2569	130	130	0	1	33	135	127	0	160	131	1
2020-02-20	3691	2584	132	132	0	0	21	131	116	0	137	133	1
2020-03-19	3690	2583	130	130	0	0	14	134	112	0	126	131	1
2020-04-17	3703	2592	131	131	0	1	22	134	113	0	135	132	1
2020-05-20	3709	2596	183	183	0	2	37	139	119	0	156	184	1
2020-06-17	3722	2605	182	182	0	4	31	143	126	1	156	182	1
2020-07-17	3739	2617	196	196	0	8	16	149	124	1	139	197	1
2020-08-14	3760	2632	194	194	0	4	13	157	129	1	141	195	1
2020-09-11	3793	2655	197	197	0	1	17	191	159	1	176	198	1
2020-10-19	3842	2689	203	200	3	1	21	226	193	1	214	201	1
2020-11-16	3865	2705	204	200	4	1	26	233	208	1	233	201	1
2020-12-14	3883	2718	202	198	4	1	31	239	204	1	235	199	1
2021-01-12	3910	2737	199	195	4	1	38	259	206	1	244	196	1
2021-02-09	3937	2756	200	194	6	2	44	275	236	1	280	195	1
2021-03-16	3950	2765	198	192	6	2	45	284	224	1	269	193	1
2021-04-14	3980	2786	186	180	6	4	45	299	268	1	313	181	1
2021-05-17	4007	2805	208	187	21	1	77	316	275	1	351	187	1
2021-06-15	4039	2827	187	165	22	5	61	331	257	1	317	165	1
2021-07-13	4072	2850	183	161	22	5	79	345	253	1	331	161	1
2021-08-10	4101	2871	179	157	22	1	57	350	243	1	299	157	1
2021-09-07	4120	2884	181	158	23	0	59	339	249	0	308	158	1
2021-10-14	4147	2903	182	158	24	0	58	322	201	0	259	158	1

=== D. main-board name state x published band, subject-days over the 59 signal days
name\band             5       10    other       na    total
ST                 6674        1        0        0     6675
DELISTING             0       54        0        0       54
NONE                  0    74952       40       16    75008
NO_RECORD            50    81845        0       18    81913
BEFORE_FIRST          0      190        0        1      191
ChiNext (band is not informative) name states by signal year: [(('2017', 'before_first'), 21), (('2017', 'delisting'), 1), (('2017', 'no_record'), 3630), (('2017', 'none'), 4171), (('2018', 'before_first'), 16), (('2018', 'no_record'), 3307), (('2018', 'none'), 5076), (('2018', 'st'), 2), (('2019', 'before_first'), 2), (('2019', 'no_record'), 3365), (('2019', 'none'), 5738), (('2020', 'before_first'), 6), (('2020', 'delisting'), 10), (('2020', 'no_record'), 3296), (('2020', 'none'), 6592), (('2020', 'st'), 11), (('2021', 'before_first'), 4), (('2021', 'delisting'), 1), (('2021', 'no_record'), 2621), (('2021', 'none'), 6808), (('2021', 'st'), 156)]
distinct main-board securities that had a published 5% band on >=1 signal day: 379 | of which ST by name on >=1 such day: 379

=== K. name-ST count on a signal day when only some announcement years are read (main board; band-5 count beside it)
2017-01-03 band-5: 56 {'signal year only': 0, 'signal year + previous': 51, '2013..signal year': 55}
2018-02-28 band-5: 62 {'signal year only': 4, 'signal year + previous': 57, '2013..signal year': 61}
2019-05-24 band-5: 124 {'signal year only': 88, 'signal year + previous': 114, '2013..signal year': 123}
2020-08-14 band-5: 195 {'signal year only': 137, 'signal year + previous': 176, '2013..signal year': 194}
2021-10-14 band-5: 158 {'signal year only': 105, 'signal year + previous': 146, '2013..signal year': 158}

=== E. every disagreement between name-ST and band-5 (main board)
('600182.SH', 'no_record', '5') days: 50 ('2017-01-03', 20) .. ('2021-04-14', 20)
('600732.SH', 'st', '10') days: 1 ('2017-06-06', 1) .. ('2017-06-06', 1)
600182.SH records: [('2021-04-26', '2021-04-23', 'SST佳通', 'ST')]

=== F1. tradable-board securities with a bar on a signal day but no listing row (<= END)
000043.SZ signal days 34 2017-01-03 .. 2019-11-19 | first bar 2013-01-04 | last bar/count ('2019-12-13', 1610) | delisting row None | name history False
300114.SZ signal days 59 2017-01-03 .. 2021-10-14 | first bar 2013-01-04 | last bar/count ('2021-10-29', 2072) | delisting row None | name history False
000022.SZ signal days 17 2017-01-03 .. 2018-12-20 | first bar 2013-01-04 | last bar/count ('2018-12-20', 1171) | delisting row None | name history False

=== F1b. every pair of securities whose daily (close, amount) agree on >= 30 common sessions (code migrations)
('300114.SZ', '302132.SZ', 2072, '2013-01-04', '2021-10-29') | list_date: None 2010-08-27
('000043.SZ', '001914.SZ', 1610, '2013-01-04', '2019-12-13') | list_date: None 1994-09-28
('000022.SZ', '001872.SZ', 1171, '2013-01-04', '2018-12-20') | list_date: None 1993-05-05

=== F2. list_date vs first daily bar, tradable-board listings 2013-01-04 .. 2021-10-29
listings dated 2017-01-01..2021-10-29 only: 1195 | first bar == list_date session: 1195
all listings from 2013-01-04 (2013-16 used only as extra evidence): 1772 | no bar at all: 0 | first bar == list_date session: 1772 | first bar later: 0 | earlier: 0
upstream_defects (research window) -- bar_before_listing by board: [('BJ', 65702, 229)]

=== F3. 250-session threshold: sessions-since-listing vs own-bars-since-listing, 5 sampled days
2017-01-03 verdict differs between the two definitions: 6 [('300488.SZ', 370, 230), ('300441.SZ', 417, 246), ('002779.SZ', 262, 218), ('603936.SH', 262, 247)] | names within +-40 sessions of the threshold: 36
2018-02-28 verdict differs between the two definitions: 14 [('603559.SH', 302, 227), ('300582.SZ', 284, 242), ('300590.SZ', 275, 171), ('603345.SH', 250, 211)] | names within +-40 sessions of the threshold: 174
2019-05-24 verdict differs between the two definitions: 1 [('300740.SZ', 310, 210)] | names within +-40 sessions of the threshold: 35
2020-08-14 verdict differs between the two definitions: 0 [] | names within +-40 sessions of the threshold: 39
2021-10-14 verdict differs between the two definitions: 0 [] | names within +-40 sessions of the threshold: 118

=== G. 'before_first' (rows announced by d exist, none in force on d), 59 signal days, tradable boards
subject-days: 240 of 208675 | securities: 156
{'listed before first record': 156, 'listed pre-2013': 156}
  002692.SZ ['2017-01-03'] first record effective 2017-02-14 announced 2016-12-21 睿康股份 其他 | list 2012-08-08
  000906.SZ ['2017-01-03'] first record effective 2017-01-19 announced 2016-12-16 浙商中拓 其他 | list 1999-07-07
  002309.SZ ['2017-01-03', '2017-02-07'] first record effective 2017-02-08 announced 2016-12-23 中利集团 其他 | list 2009-11-27
  000519.SZ ['2017-01-03'] first record effective 2017-01-10 announced 2016-12-16 中兵红箭 其他 | list 1993-10-08
  300128.SZ ['2017-01-03', '2017-02-07'] first record effective 2017-02-23 announced 2016-09-28 锦富技术 其他 | list 2010-10-13

=== H. change_reason witness against the grammar (rows announced 2013..2021-10)
{'reason 其他 but ST name (a rename that kept its marker)': 29} | reasons: {'其他': 3026, '撤销*ST': 314, '撤销ST': 94, 'ST': 106, '撤消*ST并实行ST': 119, '*ST': 421, '从ST变为*ST': 98, '退市整理期': 46, '高风险警示': 2, '撤销高风险警示': 2}

=== I. ST *entries* effective 2017-01-01..2021-10-29 (name none -> ST), tradable boards: known at the previous session's signal?
entries: 371 | announced on/before the previous session (a signal on that day could see it): 368 | announced after it (a signal could not): 3
calendar-day lead (effective - announced) distribution: [(0, 3), (1, 201), (2, 1), (3, 63), (4, 19), (5, 13), (6, 65), (7, 3), (9, 2), (11, 1)]
examples of entries a signal could not see: [('600090.SH', '*ST济堂', '2020-06-30', '2020-06-30', False), ('002496.SZ', '*ST辉丰', '2020-04-29', '2020-04-29', False), ('600530.SH', '*ST昂立', '2020-04-29', '2020-04-29', False)]

=== J. 2017-01-03: year of the record in force for each main-board name with a published 5% band
[('2014', 1), ('2015', 3), ('2016', 51), ('unresolved', 1)]

=== L. composition of the investable set and of the survivor tail (no returns) -- exploratory, not evidence
signal days with a factor-complete cross section: 59
inv          min 1807 median 2493 max 2845
surv         min 1445 median 1994 max 2276
st_inv       min 9 median 21 max 80
new_inv      min 69 median 135 max 289
either_inv   min 87 median 160 max 306
either_surv  min 30 median 68 max 143
share of ST names (investable) that fall in the dropped worst-2-decile tail: median 0.18 (min 0.00 max 0.59)
share of new (<250) names (investable) that fall in the dropped tail: median 0.64 (min 0.46 max 0.85)
share of the SURVIVOR set that is ST or new (what the eligibility filter would still remove): median 0.036 max 0.081
share of the investable set that is ST or new: median 0.068 max 0.147
for reference, a name drawn at random would be in the tail with probability 0.20
```
