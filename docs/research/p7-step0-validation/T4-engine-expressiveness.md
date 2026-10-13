# T4：现有每日引擎能否表达"粘性幸存者持仓"

- Issue：`V2-P7-005`（实施计划 1，Phase 0 / Task 0.2；设计 §10 的 T4）
- 日期：2026-10-13
- 代码基线：`1d7b4a3`。`src/` 与 `scripts/` 和 `feat/v2-p7-005-survivor-book-core` 的 `d25806e` 逐字相同（两者之间只改了文档），所以下文的 `file:line` 对两者都成立。
- 文档：设计 `docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md`、`docs/architecture/ADR-0005-single-decision-engine.md`、`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md`，均读自 `d25806e`（与主检出磁盘上的文件逐字节相同）。
- 领域内核参考实现：`~/openalpha-research/diagnostics/p7/step0-core-reference/src/openalpha_cn/domain/survivor_*.py`，下称"参考内核"。只读，没有复制进仓库。
- 数据：只做了一次只读计数（研究期内 5 个交易日的 `daily` 分区证券数，§2.3）。没有读取 2022 年及以后的任何数据。
- 本任务没有修改任何源文件。原型脚本放在 `~/openalpha-research/diagnostics/p7/t4/`，不入库。

---

## 0. 结论

1. **`buffer_rank` 不能表达粘性幸存者持仓。** 设计 §4.1/§6/§7 要求的能力里，现有引擎只能直接复用两项：每日决策，以及次日开盘按 T+1、涨跌停、停牌和整手规则成交。"剔除最差两个十分位"只能近似：`buffer_rank` 是登记时就固定的绝对名次，而幸存者边界是当日可投资截面的 80%，这个截面本身在变（研究期内主板加创业板从 2831 只涨到 4168 只）。"按选择层顺序补仓、入选门槛、冷却、每日替换上限、订单有效期、按权益定额、买不起就顺延到下一名"这几项，现有引擎一项都没有（§2）。
2. **推荐 (ii)：新建模块和新契约，不动 `StrategySpec`。** 每日入口仍是 `scripts/daily_selection.py` 这一条命令，在第 1 步按登记配置的顶层键 `kind` 分派。适配器放在 `src/openalpha_cn/strategy_survivor.py`，属于 `strategy_*` 研究平面家族（§3、§5）。
3. **两条路都不构成 rule 3 意义上的破坏性契约变更，都不需要 `V2-P0B-004` 迁移。** `StrategySpec` 和 `ScoreSource` 不是存储行：没有 `schema_version`，也不在任何 `ContractVersions` 注册表里。所以否定 (i) 的理由是对 P6 引擎的改动风险和语义不匹配，与迁移无关。ADR-0005 的 Consequences 一节和 P7 文档 §7.1 第 3 行都写着"策略规格新增形态字段、预测家族新增值属于契约变更，要走迁移"，这与代码事实不符，应当修正（§3.3）。
4. **`family="survivor_book_composite/v1"` 可行，不需要改任何契约。** 但有四件事不能照搬 P6 先例：`horizon`、登记范围、迟到记录核查、输入溯源，各需要一个幸存者版本（§4）。
5. **顺带发现参考内核（即 Phase 1 正在按计划逐字实现的代码）有三处问题。** (a) 强制退出的卖单没成交、替补买单成交（或挂单）后，第二天 `decide_book` 直接拒绝；如果把这只股票留在持仓里，还会重复下卖单。(b) `unaffordable_lot` 会把持仓中价格涨过一手预算的赢家强制卖掉。(c) 停牌即强制退出，而卖单永不过期，所以每次临时停牌都会触发一次复牌卖出外加 20 日冷却（§6）。计划 1 Task 7 的仿真假定"每笔订单都成交"，所以 (a) 没有被测到。

---

## 1. 读了什么

| 对象 | 位置 |
|---|---|
| 组合规则与成交 | `src/openalpha_cn/backtest/strategy_backtest.py`：`StrategySpec` 773–833、`target_holdings` 2353–2390、`_run_period` 2064–2133、`_buy`/`_sell` 2459–2586、`_ranked` 1682–1728 |
| 面板侧 | `src/openalpha_cn/strategy_view.py`：`strategy_request` 554–698、`_FactorFeed` 1380–1513、`_PanelDays.quote` 1994–2028、`score_day` 2247–2384 |
| 登记 | `src/openalpha_cn/strategy_registration.py`：`signal_day_batch` 167–216、`registered_declaration` 219–245、`session_record` 992–1014 |
| 每日入口 | `scripts/daily_selection.py`：`_STRATEGY_KEYS` 271–295、`strategy_arguments` 819–857（`buffer_rank` 在 843）、`target_weights` 2439–2484、`candidates` 2487–2513（截断名次在 2490）、`register_prediction` 2526–2567、主流程 2590–2802 |
| 契约 | `src/openalpha_cn/domain/alpha_model.py`：`AlphaModelDeclaration` 672–731、`AlphaModelArtifact` 734–794、`Prediction` 938–994、`PredictionBatch` 1002–1070；`src/openalpha_cn/domain/versioning.py`；`src/openalpha_cn/domain/prediction_record.py:388` |
| 登记文件 | `scripts/research/registry.py`（`REGISTRATION_SCHEMA` 92、`REGISTERED_PATHS` 93、`register` 154–207、读取校验 263–264）、`scripts/research/grid.py:273-275`（`config_id`）、`docs/research/p6-registration.json` |

---

## 2. Step 1：`buffer_rank` 能否表达粘性幸存者持仓

### 2.1 现有规则的完整语义

`target_holdings`（`strategy_backtest.py:2353-2390`）的规则如下：

- `band = buffer_rank`，没有设置时取 `holding_count`（2370）。
- 持仓名次 `<= band` 就留下；不在 `ranked` 里的持仓按 `band + 1` 处理，也就是卖出（2372）。
- 空位按 `ranked` 的顺序依次补入，跳过已持有的，可选地限制行业名额（2379-2389）。
- `buffer_rank` 是一个整数，校验只要求不小于 `holding_count`（784、802-806）。
- 每日命令从登记配置读入 `buffer_rank`（`daily_selection.py:843`），每天用同一个值（`day_request` 877-894）。

`ranked` 来自 `_ranked`（1682-1728），即"带有全部分量的证券"按 `Σ wᵢ × 标准化值` 降序排列，其中标准化是在这些证券上重做 z-score 或名次（1709-1719）。成交只在信号日的下一个交易日开盘发生一次（2075-2092）。被拒绝的订单只记一笔就丢掉（`_Ledger.reject` 2058-2061；`_sell` 2471-2492；`_buy` 2551-2572）。每日命令的产出只有等权权重（`target_weights` 2465），前一天的状态也只有权重（`previous_targets` 1111-1127）。

### 2.2 逐项对照

| # | 设计要求（§4.1 / §6 / §7） | 现有引擎（`file:line`） | 能否表达 | 缺什么 |
|---|---|---|---|---|
| a1 | 可投资范围：可交易板块、流动性前 70%、剔除 ST / 停牌 / 次新 / 缺因子 | `_ranked` 对"带有全部分量的证券"排名（`strategy_backtest.py:1704`），范围就是因子档本身的范围（`strategy_view.py:2338-2350`）；`StrategyInputs` 没有资格输入（1213-1226）；`is_st` 写死为 `False`（`strategy_view.py:2023`） | **否**（只有"缺因子"能对上，对应 `incomplete`，1720-1724） | 整个资格层 |
| a2 | 合成分 = 四个因子 `processed` 定向值的均值 | 静态分量取 4 × 0.25 加 `zscore_sum`，会在"带有全部分量的证券"上把已经是 z-score 的 `processed` 值（`panel_factors.py:7942-7956`）再标准化一次（1709-1719） | **近似** | 数值和排序与设计不完全相同；截面是因子档范围，不是可投资范围 |
| a3 | 幸存者 = 剔除可投资范围内合成分最差 2 个十分位 | 固定整数 `buffer_rank`（784），判据是 `rank <= band`（2372） | **否**（原型 A） | 随截面大小浮动的分位边界；截面大小见 §2.3 |
| b | 只在失去资格或掉出幸存者时卖 | 机制形状一致：不在 `ranked` 里就卖（2372） | **部分** | 机制可以复用，但它的前提 a1、a3 都不成立 |
| c | 空位按选择层顺序补（`seeded_hash/v1`）；入选门槛是幸存者的 25 分位；买不起一手就顺延到下一名 | 补仓按 `ranked` 的名次顺序（2379-2389），而这同一个序列也决定谁留下；买单列表在定量之前就截到空位数（2091），买不起只记一笔拒绝，空位留到下一期（2565-2572） | **否**（原型 B） | 一个序列没法同时是"名次序"（判断是否留下）和"选择层序"（决定补谁） |
| d1 | 卖出后冷却 20 个交易日 | 两次调仓之间除持仓外不保留任何状态（`_Book` 2011-2019） | **否**（原型 C） | 冷却状态 |
| d2 | 每日最多 2 笔替换（强制退出不限，自主退出顺延） | 无 | **否**（原型 D：一天卖 10 只、买 10 只） | 替换上限与顺延队列 |
| d3 | 订单有效期：买单 3 个交易日，卖单不过期；未成交的顺延 | 拒绝即丢弃（2058-2061）；每期只在下一个交易日成交一次（2075-2092）；每日命令只输出权重（`daily_selection.py:2439-2484`） | **否** | 订单生命周期与在途订单状态 |
| e | 定额 = 当日权益 / N，整手向下取整 | 每个仓位固定 `position_capital`（786；`_buy` 的预算是 `min(position_capital, cash)`，2560；初始资金 821-824） | **否** | 按权益定额 |
| f | 每日决策 | `rebalance_every_sessions = 1`（782；`rebalance_indices` 1292-1293） | **能** | — |
| g | 次日开盘成交，受 T+1、涨跌停、停牌、整手约束 | `AShareExecutionPolicy` 作用在"压缩成开盘价"的 bar 上（模块文档 24-31；`_at_the_open` 2393-2395） | **能** | 应当复用（纸面账本一侧由 T5 定） |
| h | 交易清单：数量、原因码、内容寻址身份 | 每日命令只有候选清单和权重（2487-2513、2439-2484） | **否** | 交易清单契约 |

**判断：** 现有引擎表达的是"固定绝对名次缓冲带的前 N 粘性持仓"。把它扩成幸存者持仓，不是加一个参数的事，而是要改写决策函数（需要有状态）、期间模型（订单要能跨交易日存活）、定额规则和打分范围。

### 2.3 截面大小在研究期内变化很大（支持 a3）

用只读 DuckDB 统计 `~/openalpha-research/panel/daily/<year>/data.parquet` 中带 bar 的证券数，命令见 §8：

| 交易日 | 全部板块 | 沪深主板 + 创业板 |
|---|---|---|
| 2017-01-03 | 2832 | 2831 |
| 2018-12-28 | 3551 | 3550 |
| 2019-12-31 | 3742 | 3671 |
| 2020-12-31 | 4159 | 3902 |
| 2021-10-29 | 4588 | 4168 |

幸存者边界大约在"可投资截面 × 80%"。可投资截面先经过流动性前 70% 的筛选，再随停牌、ST、次新每天变化。用一个登记后就冻结的整数 `buffer_rank` 去近似，两种错误都会出现：截面变大时，会卖掉仍是幸存者的持仓；截面变小时，会留下已经不是幸存者的持仓（原型 A 的两行输出）。

### 2.4 原型输出（合成数据，不读面板）

```
== A. buffer_rank is one integer; the survivor boundary moves with the cross section ==
buffer_rank=1600: day-2 keep=[] (survivor on day 2: yes)
buffer_rank=2000: day-2 keep=['601700.SH'] (survivor on day 2: yes)
buffer_rank=2000, universe 1800, holding at rank 1500 (not a survivor): keep=['601500.SH']
== B. refill is rank order, not a selection-layer order ==
first build buys ranks: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
== C. no cooldown: a name sold yesterday is bought back today if it ranks well ==
day 1 keep X? False
day 2 buys: ('605000.SH',) (X re-bought the next session: no cooldown)
== D. no replacement cap: every name that leaves the band is replaced the same day ==
sold 10 and bought 10 in one session
```

---

## 3. Step 2：(i) 新增 `strategy.kind`，还是 (ii) 新模块 + 新契约

说明一个前提：登记配置没有名为 `strategy` 的嵌套对象。配置本身就是 `strategy_request` 的关键字参数（`docs/research/p6-registration.json`、`daily_selection.py:271-295`）。所以设计 §5 写的"`strategy.kind`"，落到配置里就是一个顶层键 `kind`。

### 3.1 (i) 在现有引擎里新增一种形态

**必须改的文件：**

- `src/openalpha_cn/backtest/strategy_backtest.py`
  - `StrategySpec`（773-833）增加 `kind`，以及幸存者参数（或者嵌入一个 `SurvivorBookConfig`）。
  - `target_holdings`（2353-2390）的签名里没有状态，要加入冷却、在途订单、权益和价格。
  - `_Book`（2011-2047）要保存冷却与在途订单。
  - `_run_period`（2064-2133）要允许订单跨交易日存活。
  - `_buy`（2541-2586）改为按权益定额，买不起顺延到下一名。
  - `_Scorer`、`_ranked`（1495-1537、1682-1728）要改为按可投资范围计算合成分。
  - `StrategyInputs`（1182-1248）要带入资格事实。
- `src/openalpha_cn/strategy_view.py`：`StrategyRequest`（526-551）、`strategy_request`（554-698，其中 `StrategySpec(...)` 在 617-627）、`load_strategy_inputs`（816）、`score_day`（2247-2384），另加读取资格事实（ST、上市日、流动性）。
- `src/openalpha_cn/strategy_registration.py`：`registered_declaration`（219-245）、`signal_day_batch`（167-216）、`input_provenance` 与 `RecordCheck`（534-554、680 起）。
- `scripts/daily_selection.py`：`_STRATEGY_KEYS`（271-295）、`strategy_arguments`（819-857）、`target_weights`（2439-2484）、`candidates`（2487-2513）、日志状态 `previous_targets`（1111-1127）。
- 其他读取者：`scripts/forward_report.py`（171、280）、`src/openalpha_cn/sdk.py`（1309-1364）、`src/openalpha_cn/cli.py`（11760-11847）、`scripts/research/p6.py`、`scripts/render_p6_results.py`。
- 测试：`tests/unit/backtest/test_strategy_backtest.py`、`tests/unit/scripts/test_daily_selection.py` 等。

**契约判断：**

- `StrategySpec` 没有 `schema_version`。`StrategyBacktest` 的文档写明 "An answer model, not a stored row: nothing persists it"（985-986），`ScoreSource` 写明 "Not persisted anywhere: a field added here is configuration, not a stored contract"（704）。两者都不在任何 `ContractVersions` 注册表里（注册表见 §3.3）。仓库里也没有任何地方用 `model_validate` 读回已存的 `StrategySpec` 或 `StrategyBacktest`。
- 因此，加一个带默认值的字段**不是 rule 3 意义上的破坏性变更，不需要 `V2-P0B-004` 迁移**。旧的 P6 配置没有这个键，会走默认值；它的 `config_id` 是对配置 JSON 本身做的哈希（`grid.py:273-275`），保持不变。
- 但有两处可见的变化：`strategy backtest --json` 输出的答案形状会变（`backtest_view` 2658-2668）；`_STRATEGY_KEYS` 这份读取方白名单要加键。

**风险：**

- `target_holdings` 是回测和每日命令**共用**的规则。它的文档明说两者必须同源（2366-2368）。在这里加分支，等于直接改 P6 路径，与设计 §1 的非目标"不改 P6 已登记配置的行为"冲突。
- 幸存者策略会因此落在 `backtest/` 里，而不是设计 §5 和 rule 2 要求的纯 `domain/` 内核。
- 引擎的期间模型是"一次信号、一次成交"，订单生命周期要硬塞进去。

### 3.2 (ii) 新模块 + 新契约，不动 `StrategySpec`

**新建：**

- 领域内核：`src/openalpha_cn/domain/survivor_{book,selection,screen,policy,orders,lifecycle}.py`，由计划 1 Phase 1 实现。新契约为 `survivor-book-config/v1`、`trade-order/v1`、`trade-list/v1`。
- 适配器：`src/openalpha_cn/strategy_survivor.py`（计划 2）。命名落在 `strategy_` 前缀下，会被 `RESEARCH_PLANE_PREFIXES` 自动发现（`tests/unit/test_panel_ingest_import_isolation.py:1050`），只需在同一文件的接缝导入表（约 561、623 行附近）和数据集可达表（约 838 行附近）各加一行。如果取 `survivor_view.py` 这类前缀之外的名字，就得新增第七个家族或声明为例外（同文件 1688、1748-1786）。

**只做增量、不改行为的修改：**

- `src/openalpha_cn/strategy_view.py`：新增一个公开函数，返回"某信号日的逐分量定向截面"，内部复用 `_FactorFeed`（1380-1513，定向在 1471）和 `strategy_backtest._cross_section` 的前视守卫（1641-1679）。P6 路径不受影响。
- `scripts/daily_selection.py`：第 1 步按 `config.get("kind")` 分派。没有这个键时完全走现有路径，`_STRATEGY_KEYS` 和 `strategy_arguments` 不动。幸存者路径复用 `admit_registration`（381-443），复用面板更新、体检、因子构建三步（其中 `tiers_read`/`targets_for` 需要一个只取四个因子 `processed` 档的变体），`register_prediction`（2526-2567）原样复用。
- `src/openalpha_cn/strategy_registration.py`：在 `registered_declaration` / `signal_day_batch` 旁边新增幸存者的声明与批次构造（也可以放在适配器里）。`session_record`（992-1014）、`registration_cutoff`（1017-1022）、`registered_at`（248-250）原样复用。
- `scripts/forward_report.py`：增加幸存者分支，或在 `V2-P7-009` 的周报扩展里单独处理。

**不改：** `StrategySpec`、`ScoreSource`、`target_holdings`、`run_strategy_backtest`、`PredictionRecord`、`scripts/research/registry.py`（配置在那里不透明，只检查 `schema` 字段，263-264）。

**契约判断：** 只新增契约，不改任何已有契约，不需要迁移。

### 3.3 关于"需不需要 `V2-P0B-004` 迁移"——与 ADR-0005、P7 §7.1 的出入

迁移机制已经落地：表迁移在 `storage/migrations.py`，行版本在 `domain/versioning.py`（`V2-P0B-005`，模块文档 1-46）。受版本管理的存储契约是那些注册了 `ContractVersions` 的模型，例如 `PREDICTION_RECORD_VERSIONS`（`domain/prediction_record.py:388`）、`RUN_MANIFEST_VERSIONS`（`domain/run.py:533`）。`StrategySpec`、`ScoreSource`、`StrategyBacktest` 都不在其中。

`AlphaModelDeclaration.family` 是自由字符串（`alpha_model.py:705`，长度 1–128，没有 `Literal`）。存储和读取路径都不校验它：`PredictionRecord`、`storage/predictions.py` 里没有任何地方检查 family。只有拟合路径会查 `MODEL_FAMILIES[family]`（`model_view.py:2181-2186`、`strategy_view.py:738-753`）。

因此：

- ADR-0005 Consequences 第 2 条说"The strategy specification and the prediction family set change; these are contract changes and go through the migration mechanism"，P7 文档 §7.1 第 3 行也这么写。对于 (ii)，这两条都不成立：Step 0 不改策略规格，新增 family 值也不改任何契约。
- 对于 (i)，`StrategySpec` 加字段同样不是存储契约变更。
- **建议**由控制者在 P7 文档 §9 记一行，并修正 ADR-0005 与 §7.1 的这两处表述，避免以后有人为一个不存在的迁移去等一个"统一的破坏性变更窗口"。

### 3.4 对已登记配置的影响

| | (i) | (ii) |
|---|---|---|
| P6 登记文件的形状（`openalpha-research-registration/v1`） | 不变 | 不变 |
| P6 的 `config_id` | 不变（没有新键，`grid.py:273-275`） | 不变 |
| P6 的代码绑定（`REGISTERED_PATHS`，`registry.py:93`） | 失效：改了 `src/` | 同样失效。这是预期内的（ADR-0005；P7 决定 D4 已暂停每日登记；`V2-P7-006` 会重新登记） |
| P6 路径在 HEAD 上的行为 | 引擎被改，有回归风险 | 完全不动 |
| P6 配置的读取方（`daily_selection`、`forward_report`） | 要改 `_STRATEGY_KEYS` / `strategy_arguments` | 不改 |
| 幸存者配置误交给 P6 读取方 | — | 按名拒绝（未知键，`daily_selection.py:828-830`），失败即关闭 |

---

## 4. Step 3：用 `family="survivor_book_composite/v1"` 做预测登记

**可行，不需要改契约。** 先例是 `family=f"strategy_{source.kind}"`（`strategy_registration.py:236`），P6 已经用它登记过"没有拟合任何东西"的记录。新值长度 26，满足 `alpha_model.py:705` 的约束。

### 4.1 登记容器要求的字段

| 层 | 字段与约束（`file:line`） | 幸存者记录怎么填（建议） |
|---|---|---|
| `AlphaModelDeclaration`（672-731） | `name` 1–128；`family` 1–128；`horizon` 必须匹配 `^[1-9][0-9]{0,2}d$`（706；`domain/horizon.py:112-113`）；`feature_version` 1–128；`seed >= 0`；`code_commit` 7–64；`hyperparameters` 为平铺标量、按键严格递增（712-731） | `name` 可以沿用 `COMPOSITE_MODEL_NAME`（`strategy_registration.py:142`），两类记录靠 family 区分；`family="survivor_book_composite/v1"`；`horizon="20d"`（见 4.2）；`feature_version` 取登记的 `config_id`；`seed` 取选择层种子；`code_commit` 取登记的提交；`hyperparameters` 依次为 `("registration_sha256", …)`、`("survivor_config_id", "sbc_…")` |
| `AlphaModelArtifact`（734-794） | `feature_ids` 非空、严格递增（`validate_feature_ids` 380-414）；`training_cutoff` 必须带时区，且不晚于批次的 `as_of`（1054-1064）；`training_example_count >= 1`；`parameters` 有序且为有限值 | `feature_ids` = 四个 `<factor>@processed` 键；`training_cutoff` = 当日所读输入中最新的可知时刻（同 P6 的 `knowable_through`）；`training_example_count` = 读入的值的条数；`parameters` = 四个权重各 0.25 |
| `Prediction`（938-994） | `score` 与 `abstention` 恰有一个；`score` 必须有限 | 合成分；缺因子的证券填 `ABSTAIN_INCOMPLETE_FEATURES`（270-272） |
| `PredictionBatch`（1002-1070） | `predictions` 非空，按 `ts_code` 严格递增且不重复；`predicted_at >= as_of` | `as_of` = 信号时刻（`session_publication_instant`）；必须在下一交易日 09:15 前提交（`daily_selection.py:2555-2565`） |
| `PredictionRecord`（`prediction_record.py:273` 起） | 由 store 自己的时钟盖 `recorded_at`；`outcome_known_at` 由 `horizon` 推出（247、382） | 不用改 |

### 4.2 不能照搬 P6 先例的四件事

1. **`horizon`。** P6 声明的是调仓间隔（`strategy_registration.py:237`；`daily_selection.py:968-972`），幸存者是每日决策，照搬就成了 `"1d"`。设计 §8 评估的是 h=20 与 h=5。一条记录只有一个 `horizon`（`alpha_model.py:1072-1075`）。建议声明 `"20d"` 为主口径；h=5 作为同一条记录的次口径，并在 `V2-P7-006` 的协议里预先写明。这条记录在 09:15 前提交，比任何 5 日结果都早，所以它对 h=5 同样是前向记录。
2. **登记范围。** P6 登记的是因子档范围内全部被排名的证券（`signal_day_batch` 206-215）。设计 §8 要求的是"可投资范围内全部证券"。但在参考内核里，资格判定含 `unaffordable_lot`，它依赖账本权益（`survivor_screen.py:61, 77-78`）。如果照此登记，记录就会依赖纸面账本状态，而不只是面板加配置。建议登记范围取"判定原因 ⊆ {`unaffordable_lot`} 的证券"，与权益无关；更好的做法是从筛选层去掉这个原因（§6.2）。
3. **迟到记录核查。** `late_record_check` 和 `RecordCheck` 是靠 `score_day` 重算来核对的（680-828），只能处理 P6 的 `ScoreSource`。幸存者记录需要用自己的适配器重算。
4. **输入溯源。** `input_provenance` 依赖 `StrategyRequest` 和 `input_datasets`（534-554；`strategy_view.py:342-372`），只覆盖因子档。幸存者的输入还包括 `daily`、`stk_limit`、停牌、`stock_basic`，以及 `namechange`（若 T3 采用）。数据集清单要换成幸存者版本，可以把 `_partition_state` 泛化成"数据集 + 窗口"，而不是绑死在 `StrategyRequest` 上。

---

## 5. Step 4：决定、理由与适配器调用形状

### 5.1 决定：推荐 (ii)

理由：

1. **表达差距太大。** §2.2 的对照里，只有 f、g 两项能直接复用。在引擎里补齐其余各项，就要改写 P6 共用的 `target_holdings`（2366-2368）、期间模型和定额规则，这与设计 §1 的非目标"不改 P6 已登记配置的行为"冲突。
2. **符合 rule 2。** 有状态的持仓策略属于纯 `domain/` 内核（设计 §5）。现有引擎在 `backtest/`，而且是"期间账本"模型，容纳不了订单生命周期。
3. **契约成本为零。** (ii) 只新增契约。(i) 虽然也不需要迁移，却要改动 P6 的读取方和对外答案的形状。
4. **不是第二个引擎（ADR-0005 决定 1）。** 同一条每日命令、同一个面板和因子档、同一个 `AShareExecutionPolicy`、同一个预测登记容器、同一个组装根（`panel_view.panel_store` / `runtime.composition.build_storage`）。换掉的只是 `target_holdings` 表达不了的那条调仓规则，以及合成分的计算。不新增任何 store 构造（rule 5）。

### 5.2 登记配置的形状（建议）

```json
{
  "kind": "survivor_book/v1",
  "start": "<anchor session>",
  "exchange": "SSE",
  "transform": "cross_section_standard/v1",
  "survivor_book": { "...": "SurvivorBookConfig 的全部字段（extra=forbid，含 seed）" }
}
```

`registry.register` 把配置原样写入（`registry.py:188-198`），`config_id` 照常是对配置 JSON 的哈希。`daily_selection` 第 1 步的分派规则：没有 `kind`，走现有 P6 路径；`kind == "survivor_book/v1"`，执行 `SurvivorBookConfig.model_validate(config["survivor_book"])`；其他值按名拒绝。

### 5.3 适配器调用领域内核的形状（建议）

```python
# src/openalpha_cn/strategy_survivor.py —— 计划 2；面板侧，可以使用数值库
def survivor_day(
    store: PanelStore,
    config: SurvivorBookConfig,
    registered: RegisteredConfiguration,
    *,
    day: date,
    as_of: datetime,
    anchor: date,
    ledger: LedgerView,   # 由纸面账本（T5）推进后得到：权益、现金、持仓、在途订单、冷却、是否已建仓
) -> SurvivorDay:
    # 1. 面板 -> 纯值事实；按 ts_code 排序，板块用 backtest.execution.security_board
    facts = security_facts(store, config, day=day, as_of=as_of)        # tuple[SecurityFacts, ...]
    # 2. 资格与幸存者（纯函数）
    result = screen(facts, config, equity=ledger.equity)              # ScreenResult
    # 3. 持仓决定（纯函数）
    state = BookState(
        session=ordinal(anchor, day),   # trading_days_between(anchor, day) 中的位置，同 schedule_of 的取法
        initialized=ledger.initialized,
        equity=ledger.equity,
        holdings=tuple(h for h in ledger.holdings if h.ts_code not in ledger.live_sell_codes),  # 见 §6.1
        pending_buys=ledger.live_buy_codes,
        cooldowns=ledger.cooldowns,
    )
    decision = decide_book(config=config, screen=result, state=state)  # BookDecision
    # 4. 交易清单（纯函数）
    trades = build_trade_list(
        config=config,
        state=state,
        decision=decision,
        reference_prices={fact.ts_code: fact.close for fact in facts},  # 信号日收盘价（不复权）
        cash=ledger.cash,
        carried=ledger.live_orders,
    )
    # 5. 预测登记批次（family="survivor_book_composite/v1"），范围与权益无关（§4.2 第 2 条）
    batch = survivor_batch(result, config, registered, day=day, as_of=as_of)
    return SurvivorDay(facts=facts, screen=result, decision=decision, trades=trades, batch=batch)
```

调用顺序：先推进纸面账本（T5），也就是对上一日的订单调用 `advance_orders(orders, session=…, filled_order_ids=…, survivors=…)` 和 `update_cooldowns(…)`；再调用 `survivor_day`；最后把 `batch` 交给现有的 `register_prediction`（`daily_selection.py:2526`）。

`ordinal` 复用 `TradingCalendar.trading_days_between` 的位置算法（`strategy_registration.py:868-873`），不另写日历。

`config.config_id`、`code_commit`、`seed` 进入登记声明和决策身份（rule 8）。

---

## 6. 顺带发现：参考内核（Phase 1）里的三处问题

这些不属于 T4 本身，但都会影响 Phase 1 正在逐字实现的代码，也会影响 §5.3 的调用形状。按共同规则立即报告。复现见 §8 的原型 E–H。

### 6.1 强制退出的卖单没成交，第二天 `decide_book` 直接拒绝

场景：持仓 `600091.SH` 停牌，被判失去资格，第 1 日给出"卖 600091、买替补 600065"。第 2 日开盘，停牌股卖不出：

- 如果替补买单成交，账本里有 11 只持仓，`decide_book` 抛出 `11 holdings and 0 pending buys exceed holding_count 10`（`survivor_policy.py:115-119`）。
- 如果替补买单因现金不足挂单，同样抛出 `10 holdings and 1 pending buys exceed holding_count 10`。
- 如果适配器把这只股票留在 `holdings` 里，同时把它的卖单作为 `carried` 带入，策略会再判一次同一个退出，`build_trade_list` 于是对同一持仓生成第二张卖单（两个 `order_id`：`tor_6c7b…`、`tor_5352…`）。

根源是 `BookState` 无法表达"已持有、但卖单在途"（50-74）。卖单又不会过期（`survivor_lifecycle.py:46-47`），所以这种状态会持续到复牌之后。计划 1 Task 7 的逐日仿真写明"every order assumed to fill"（计划文件 2548、2662 行），因此没有覆盖到这一点。

修法有两种：

- **在内核里补齐。** `BookState` 增加 `pending_sells`，占位按 `held − pending_sells + pending_buys` 计算，并且不再对这些股票重复判退出。
- **最小做法是适配器约定**（原型 H 已验证可行）：传给内核的 `holdings` 不含有在途卖单的持仓。

无论哪种，都要写成测试。

### 6.2 `unaffordable_lot` 被当作资格原因，会强卖持仓中的赢家

`screen` 对价格 × 100 超过单票预算的证券加上 `unaffordable_lot`（`survivor_screen.py:77-78`），`decide_book` 又把任何不合格的持仓强制卖出（`survivor_policy.py:128, 137-139`）。原型 F：持仓价格涨到 360 元（预算 34,825 元 / 100），被判 `EXIT_ELIGIBILITY_LOST` 卖掉。设计 §6 只说这类证券"不可入选"。

同一原因还让幸存者的十分位边界随账本权益漂移，因为被排除的证券不参与切分（`survivor_screen.py:97-99`）。策略层本来已经会跳过买不起的入选候选（`survivor_policy.py:163-167`）。建议从筛选层去掉这个原因，让资格与幸存者成为面板加配置的纯函数。这同时修好了 §4.2 第 2 条和对方案 C 的影响（§7）。

### 6.3 停牌即强制退出，且卖单永不过期

设计 §4.1 把停牌列在资格层。参考内核对持仓也照此执行：强制退出，不顺延（`survivor_policy.py:128`）。卖单只在成交时才离开 `live`，不会被取消（`survivor_lifecycle.py:46-47`）。

结果是，持仓遇到任何一次临时停牌（哪怕只停一天），复牌开盘都会被卖出，并触发 20 日冷却，即使复牌时它仍是合格的幸存者。这与"粘性"相悖，换手和成本都会上升。是否就是本意，需要用户确认。备选做法：停牌只阻止入选；对持仓则在复牌后重新判定。

---

## 7. 对方案 A / B / C 的影响

**总体：** 采用 (ii) 后，三个方案的接入点都在领域内核与登记容器上，不在 `StrategySpec` 上，也都**不需要 `V2-P0B-004` 迁移**（§3.3）。不过，凡是改变决策规则的改动，都会改变 `SurvivorBookConfig` 的内容，因而 `config_id` 变化，需要重新登记，前向窗口重新起算。下面三条都有实质影响。

### 方案 A（否决层）

- 接入点是 `screen` 的资格层，即新增一个 `EligibilityReason`（`survivor_book.py:31-39`），这与设计 §13 一致。
- 还缺两样东西：一是输入通道，`screen` 只吃 `SecurityFacts`（`survivor_screen.py:43-48`），需要新增字段或一个 `vetoes` 参数；二是开关，要在 `survivor-book-config` 上加字段，等于新配置版本加重新登记。
- P7 §4.3 写"策略规格新增否决叠加层，属契约变更，走迁移"。在 (ii) 下这一条不成立，应改为"新配置版本 + 新 `EligibilityReason` + 重新登记"。
- **实质影响一：** 否决会被当成强制退出，不受替换上限约束，也不顺延（`survivor_policy.py:128, 137-139`）。智能体否决一只持仓就立即卖出，可能带来高频进出。方案 A 必须先决定否决是"只阻止入选"还是"强制卖出"。
- **实质影响二：** 如果否决会改变 Step 0 的登记范围，Step 0 自己的预测记录也会跟着变。建议 Step 0 的登记范围不含否决层（它只看面板事实）；否决的价值用它自己的 `agent_*` 家族去度量（ADR-0005 决定 2）。

### 方案 B（模型分数替换选择层）

- 现有 `SelectionRule.order(candidates, *, seed)` 没有分数输入（`survivor_selection.py:22-27`）。`decide_book` 又是按配置里的 `Literal["seeded_hash/v1"]` 去查规则（`survivor_book.py:74`；`survivor_policy.py:160-162`）。
- 所以方案 B 目前**接不进同一个接入点**，要么改协议，要么改 `decide_book` 的签名。
- **建议在 Phase 1 落地之前**就把协议改成 `order(candidates, *, seed, scores: Mapping[str, float] | None = None)`。或者让 `decide_book` 接收一个注入的规则对象，由适配器按日构造并带入分数。代价很小，能避免 B（以及 A、C 若要排序）以后再改内核签名。
- `selection_rule` 的取值扩展是配置版本问题，不是迁移。
- 方案 B 的模型分数仍然要作为自己的家族登记，并在**幸存者内部**证明有增量（设计 §13）。

### 方案 C（智能体前向登记）

- 登记容器可以直接复用：`agent_*` 是自由字符串，无需迁移。`session_record` 按声明去重（`strategy_registration.py:1005`），所以同一交易日的 `survivor_book_composite/v1` 记录和 `agent_*` 记录可以并存，`register_prediction`（`daily_selection.py:2526`）可以原样复用。
- **实质影响：** C 的评估域是"幸存者内部"（`V2-P7-014`）。这个域要能单凭 Step 0 的记录和配置重建（按合成分剔除最差两个十分位），前提是登记范围与账本权益无关。按参考内核现状，`unaffordable_lot` 会让幸存者集合依赖权益（§6.2），C 的评估域就会跟着纸面账本漂移。所以 §6.2 的修正，或至少 §4.2 第 2 条的登记范围约定，是 C 的前置条件。

---

## 8. 复现

```bash
# 研究期截面大小（只读；只读 2017–2021 的 daily 分区）
cd "<worktree>" && ../../../.venv/bin/python -I ~/openalpha-research/diagnostics/p7/t4/universe_size.py

# 原型 A–H：先把仓库 src 复制到临时目录，再放入参考内核，然后运行
TMP=$(mktemp -d) && cp -R src/openalpha_cn "$TMP/" \
  && cp ~/openalpha-research/diagnostics/p7/step0-core-reference/src/openalpha_cn/domain/survivor_*.py "$TMP/openalpha_cn/domain/" \
  && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$TMP" ../../../.venv/bin/python ~/openalpha-research/diagnostics/p7/t4/proto_t4.py
```

原型 E–H 的输出：

```
== E. reference core: an unfilled sell plus a filled replacement buy ==
day 1 orders: [('sell', '600091.SH', 'EXIT_ELIGIBILITY_LOST'), ('buy', '600065.SH', 'ENTRY_REPLACEMENT')]
day 2: decide_book refuses -> 11 holdings and 0 pending buys exceed holding_count 10
day 2 sells for 600091.SH : [(11, 'tor_535284e539f9e299ea81eeab'), (10, 'tor_6c7b54cb828128feaddc88cd')]
== G. the same day 2, with the buy unfilled for want of cash (pending) ==
day 2: decide_book refuses -> 10 holdings and 1 pending buys exceed holding_count 10
== H. adapter convention: a holding with a live sell is left out of BookState.holdings ==
exits: [] entries: []
== F. reference core: a holding whose price rose past one lot of the budget ==
verdict: ('unaffordable_lot',) | exits: [('600095.SH', 'EXIT_ELIGIBILITY_LOST')]
```
