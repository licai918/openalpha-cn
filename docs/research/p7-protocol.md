# V2-P7-006b 研究协议：幸存者合成分的前向检验（`survivor_book/v1`）

> **本文件的提交早于任何前向运行**；此后只追加第 12 节的版本记录，正文不因看到结果而改写。
> 本文件与 `docs/research/p7-registration.json` 一起构成预登记：登记文件一旦提交，判据、窗口、
> 停止规则与功效设计就固定下来，改任何一条都是另一份登记（换运行目录、换纪元）。
>
> 一切结论词的强度只到 **候选**：本文件描写的是一个候选研究配置的预登记设计，不是任何
> 已经成立的结果，纸面账簿的权益不是证据。

## 0. 状态与身份

| 项 | 值 | 出处 |
|---|---|---|
| Issue | `V2-P7-006b`（路线图 `V2-P7-006` 行的登记部分） | `docs/specs/v2/openalpha-cn-v2-roadmap.md` 的 `V2-P7-006` 行 |
| 登记文件 | `docs/research/p7-registration.json` | 写入器 `scripts/p7_register.py` |
| 登记写入器 | `scripts/p7_register.py`（不在受绑定路径里，见其模块文档） | 计划 §2、D-8 |
| 登记内容 | `config`、`criteria`、`code_commit`、`settings`、`false_discovery_rate`、`dependence` | `scripts/research/registry.py` 的 `register` |
| 登记的代码 | 登记文件的 `code_commit`（完整 40 位），= 冒烟阶段 1 验过的受绑定字节 | `scripts/p7_smoke_env.py` 的 `check_commit`；计划 §2 |
| 登记提交 | 见第 12 节版本记录（Task 7 之后追加） | 本文件 §12；本次提交不含登记 |

本文件的正文在登记提交之前写成；登记提交、它的 sha256、`config_id`、纪元与钉住的 commit
在登记之后追加到第 12 节，正文的其他数字不因此改动。

## 1. 假设

1. **主假设（候选）**：幸存者合成分（`survivor_book_composite/v1`）在前向期有正的 h20 秩 IC
   （Spearman 相关；单侧，均值 > 0）。
2. **次要假设（候选）**：在可交易范围内剔除最差 2 个十分位后的等权组合，相对可投资等权有正的
   20 日超额（`survivors_minus_ew`，均值 > 0）；以及等权减最差十分位（`ew_minus_worst_decile`，
   均值 > 0），后者与前者方向相同，见 `src/openalpha_cn/strategy_survivor_forward.py` 的
   `ForwardMetric` 文档。
3. 以上都不是已经得到的结论。检验只在预登记的窗口里、按本文件的判据做；任何一次读数都只是
   **候选**。

## 2. 窗口

| 段 | 日期 | 性质 | 出处 |
|---|---|---|---|
| 研究期 | 2015-01-05..2021-12-31 | 本登记之前的研究数据，不是样本外证据 | `scripts/research/grid.py:125-133` 的 `PROTOCOL_STAGE_WINDOWS` |
| 验证期 | 2022-01-04..2023-12-29 | **第一次样本外读数，已读过一次**；结果是原样引用的描述，不是确认性证据（见第 11 节） | `scripts/research/grid.py:128`；读数登记 `docs/research/p7-readings/v2-p7-024-validation.registration.json` 的 `window` |
| 二次使用 | 2024-01-02..本登记提交日 | 二次使用、非确认性（决策链 §7.2） | `scripts/research/grid.py:130`；决策链 §7.2 |
| 确认期 | 本登记提交之后的前向记录 | 唯一的确认性来源 | 决策链 §5.2 G-0（`:231`） |

确认性证据只来自本登记**提交之后**的前向记录；2022–2023 的读数是第一次样本外读数，它用掉
了那个窗口，但**不是**确认性证据。研究期 2015–2021 是研究窗口，不是本登记的前向证据。

## 3. 配置

### 3.1 `config`（恰好三个顶层键）

`config` 是恰好三个顶层键的封闭文档：`kind`、`survivor_book`、`adapter`（`src/openalpha_cn/strategy_survivor.py`
的 `read_survivor_registration`）。

- `kind` = `"survivor_book/v1"`（`src/openalpha_cn/strategy_survivor.py:111`）。
- `survivor_book` = `SurvivorBookConfig(seed=20260926).model_dump(mode="json",
  exclude_computed_fields=True)`，即全部默认值（`src/openalpha_cn/domain/survivor_book.py:89-110`）：
  账户 350,000 元、持股 `holding_count` = 10（`:91`）、`capital` = `"350000"`（`:92`）、`boards` = `["main"]`
  （`:93`）、`liquidity_drop_fraction` = `"0.30"`（`:95`）、`min_listing_sessions` = 250（`:96`）、
  `loser_decile_count` = 2（`:97`）、`entry_floor_fraction` = `"0.25"`（`:98`）、
  `near_exit_fraction` = `"0.10"`（`:99`）、`cooldown_sessions` = 20（`:100`）、
  `max_replacements_per_session` = 2（`:101`）、`buy_order_expiry_sessions` = 3（`:102`）、
  `lot_size` = 100（`:103`）、`cost_reserve_fraction` = `"0.005"`（`:104`）、
  `selection_rule` = `"seeded_hash/v1"`（`:107`）、`seed` = 20260926（见 §3.3）、
  `composite_factors` 为四个 `*_60/v1` 因子（`residual_vol_60/v1`、`return_vol_60/v1`、
  `turnover_60/v1`、`downside_vol_60/v1`；`src/openalpha_cn/domain/survivor_book.py` 上方的
  `DEFAULT_COMPOSITE_FACTORS`）。
- `adapter` = 设计的适配器块（`scripts/p7_smoke_env.py:134-150` 的 `DESIGN_ADAPTER`）加上
  `epoch_session`：`survivor-adapter/v1`、`exchange "SSE"`、`transform
  "cross_section_standard/v1"`、`min_coverage 0.5`、`max_carry_sessions 250`、
  `max_panel_catch_up_sessions 10`（输入清单第 10 项“登记时冻结”）、`benchmark "000905.SH"`、
  `prediction_horizon "20d"`、`cost_policy "protocol/v1"`、`paper_limits 1/1/0`（D15）、
  `unfilled_buy "reject_whole_and_carry/v1"`。正式的 `config` 与冒烟的
  `smoke_configuration(epoch)` 逐字节相同 —— 冒烟检验的就是将登记的配置。

### 3.2 `selection_rule` 是占位

`selection_rule` 只有一种取值：`seeded_hash/v1`（`src/openalpha_cn/domain/survivor_book.py:107`；
`src/openalpha_cn/domain/survivor_selection.py:49`、`:58`）。它是按交易日加盐的确定性哈希，
**不宣称任何选股能力**（`src/openalpha_cn/domain/survivor_selection.py:3`）。方案 B 若加规则，
那是配置版本问题（决策链 §9 `:329`），不属于本登记。

### 3.3 两个种子

`survivor_book.seed` = 20260926（`scripts/research/grid.py:138` 的 `PROTOCOL_RANDOM_SEED`）。
`settings.random_seed` 也来自 `grid.protocol_settings()`（`scripts/research/grid.py:220-227`），
它与书的 `seed` 不等时每日命令退出 3（`src/openalpha_cn/strategy_survivor.py:334-339`）。
验证期读数用的 `seed 0`（读数登记 `config.book.seed`）不影响分数：种子只在幸存者内挑持仓，
不进打分（`scripts/research/p7_diagnostics.py` 的 `SEED_NOTE`）。

### 3.4 资格定义

主板（`boards ["main"]`）、上市 ≥250 会话（`min_listing_sessions 250`）、非 ST、非停牌、因子有值、
流动性前 70%（`liquidity_drop_fraction "0.30"`，即剔除最薄的第 30%）。`illiquid` 的处置是
**(a) 强制退出**（`src/openalpha_cn/domain/survivor_book.py:65` 的 `ILLIQUID_POLICY =
"forced_exit"`；`V2-P7-006a` 冻结，2026-11-07）。名称状态 `unknown` 按普通处理并逐日计数
（`src/openalpha_cn/strategy_survivor_facts.py:150-155`；Part D 回答 (7)，决策链 `:348`）；
2025-07-07 之后主板风险警示股的 5% 涨跌停带宽证人恒为 0（`BAND_WITNESS_ENDS`，
`src/openalpha_cn/strategy_survivor_summary.py:191`；`inert_from` `:530`），这一条也逐日打印。

### 3.5 成本、现金与涨停价冻结

成本口径是协议的（`cost_policy "protocol/v1"`，`PROTOCOL_COSTS` 与折入 transfer fee 的滑点，
`src/openalpha_cn/strategy_survivor.py` 的 `make_paper_portfolio`）；纸面账簿的买单按 **09:15 的
现金**定量（今晚卖单回款不算，`src/openalpha_cn/domain/survivor_orders.py:266`），并按下一交易日
**涨停价**冻结资金（`FREEZE_RATIO = 1.10`，`src/openalpha_cn/domain/survivor_orders.py:48`），
资金不足时按整手缩小（短少策略 `FREEZE_SHORTFALL_POLICY`）。**“集合竞价只收限价单、买涨停价 /
卖跌停价取单一开盘价、买单按约 1.1 倍名义冻结”是本系统的假设，没有向任何券商核对过**；
下单前须向券商确认。纸面账簿的单据不是建议，也不应被下达。

### 3.6 已知局限（D-6，接受）

受绑定代码里有两处已知接缝，本登记**接受**它们并记录为已知局限；它们只影响中断与补救路径的
退出码/分类，不改变分数、不改变订单：

1. `scripts/daily_selection.py` 的 `_step` 把一次真实中断报成第 2 步的拒绝；
2. `scripts/daily_selection.py` 的 `invoke` 捕获的 click 错误类型与 typer 0.27 自带的 click
   抛出的不是同一个类型。

登记之后再修这两处就是换登记；若用户要在冒烟阶段 1 之前修，则必须重做阶段 1（决策链 `:368`）。

## 4. 判据

- **主检验**：h20 秩 IC，单侧符号翻转，均值 > 0，p ≤ 0.05，族大小 1，锚点序号 1，
  K = 36 个非重叠位置。符号翻转 100000 次（`bootstrap_samples`，上限 100000，
  `src/openalpha_cn/strategy_survivor_forward.py:514`；取值 `scripts/research/grid.py:137`）；窗口达到
  K 之前只报计数与描述性均值，不给 p 值、不给结论词。
- **次要族**（{h20 `survivors_minus_ew`、h20 `ew_minus_worst_decile`、h5 `rank_ic`}，大小 3）用
  Benjamini–Yekutieli（任意相依），q = 0.10（`PROTOCOL_FALSE_DISCOVERY_RATE`，`scripts/research/grid.py:139`；
  协议的 `q` 字段 `src/openalpha_cn/strategy_survivor_forward.py:517`）；次要族等主窗口满足后才检验，
  打印为“q 值 ≤ q”，
  从不当作结论。**本登记的主检验只有一条**，族大小 1。
- 被拒绝的记录排除并列出；样本内出现拒绝记录时检验标“受损”。
- 缺口只减少期数 n，不移动窗口（§7）。

## 5. 停止规则与中期无效性

用户 2026-11-07 采纳的停止规则，一个数字一个具名常量（`src/openalpha_cn/strategy_survivor_forward.py:412-425`）：
主统计量 h20 秩 IC（`:412`、`:416`）；秩 IC 均值 ≤ 0.02 或 `ew_minus_worst_decile` 均值 ≤ 0
即停止（`:417`、`:419-420`）；前向窗口 K = 36（`:421`）；中期无效性在 K / 2 = **18** 个累积
位置、累积均值 ≤ 0 即停止（`:423`、`:425`）。登记块是 `adopted_forward_stop_rule()` 原样
（`src/openalpha_cn/strategy_survivor_forward.py:777-792`）。

停止规则在读数之前写下；它被写成“未触发”**不是证据**，只表示不触发停止规则。触发、未触发与
“不可测”是三个不同的结论（`V2-P7-024` 的 G-0 判读）；本文件只登记前向规则。

## 6. 功效

登记块的原文：

```text
test_level 0.05、target_power 0.80、critical_value "t"、assumed_lag1_autocorrelation 0.0、
minimum_periods 30；planned 只有 h20 rank_ic：planning_sd 0.18、planning_means
[0.037, 0.05, 0.10]、sd_source “研究期约 0.14 与验证读观测 0.182 取较大者向下取两位；
单次观测不是规划值”。
```

`minimum_periods` = 30 是 D-2：36 个位置里至多丢 6 个（约 1/6）；它只给结果贴标签，不阻止检验
（`src/openalpha_cn/strategy_survivor_forward.py:652-673` 的模型文档；登记读取 `:676-745`）。
`planning_sd` = 0.18 是 D-1：研究期实测约 0.14（工具默认 0.15，决策链 §3.6），验证读观测 0.182
（读数文本自己说“单独一次的观测值不是规划值”），取较大者向下取两位，偏保守。只规划主检验
（D-3）：h5 的离散度从未测过，写一个没有来源的 sd 等于编造。

**推算**（不是测量）：在这个设计下，K = 36、单侧 α = 0.05，IC = 0.05 时功效约 64%（σ = 0.15）
或约 48%（σ = 0.182，验证读的观测值）；IC = 0.037 时更低。读法是：**在这个设计下检测不到**
大不等于“没有效应”。这些数字是设计性质的推算，不是任何读数。

## 7. 缺口、UNVERIFIABLE 与未贴标签

- **缺口**只减少期数 n，从不移动窗口：没有记录、被拒、未核查（敏感性里还包括 UNVERIFIABLE）的
  日历位置都是缺口，K_h 个位置全部到期才算窗口满足。
- **UNVERIFIABLE** 计入头条读数，敏感性读数中剔除；两种读法都打印，占比每次都报。
- **未贴标签**的名字（窗口内退市、停牌、涨停冻结、没有复权史等）从秩 IC 与每个均值里剔除，
  并按代码与按十分位计数；退市通常是很大的亏损、被剔除后缺失，所以最差十分位的均值很可能
  偏高，这会**低估** `ew_minus_worst_decile` 与 `top_minus_bottom`
  （`src/openalpha_cn/strategy_survivor_forward.py:438-446` 的 `UNLABELLED_WORDING`）。
- 每次核销（退市终局）附“按最后收盘回收”的敏感性一行，并标“疑似吸收合并”（D16）。

## 8. 前向起点与磨合期

**纪元规则**：`epoch_session` = 第一次计划运行的**前一个**交易日，必须是已存日历的开市日；
第一晚距纪元最多 `1 + max_panel_catch_up_sessions = 11` 个会话，否则第 1 步按名拒绝、0 次请求
（`scripts/daily_selection.py:4166` 的 `bound = 1 + adapter.max_panel_catch_up_sessions`；
决策链 `:348`）。所以纪元在登记当天才定，登记与第一晚
相隔不得超过约两周；目标在 2027-01-04 之前（`V2-P7-025` 的硬闸门）。

**磨合期**：登记后约前 22 个会话为预声明磨合期（`docs/superpowers/plans/2026-11-01-p7-route-adjustment.md:98`；
决策链 §9 2026-11-01 行）。发现受绑定代码缺陷则换运行目录与纪元、重新登记
（必须点名本登记）；无缺陷则这些会话直接属于确认样本。磨合期内的缺陷清单写在运行手册
`docs/runbooks/daily-selection.zh-CN.md` 的「正式登记（`V2-P7-006b`）」一节。

## 9. 延续（U1–U7）

- **U1**：本登记之后，每一份幸存者登记**必须**在 `settings.survivor_continuation` 里点名它所
  取代的那一份（路径、sha256、`config_id`）；`V2-P7-006b` 的登记测试检查整条链：恰好一个链根，
  每一环读通且指向集合内，没有分叉。这是**登记测试**（链规则由代码无法推断出“本该有前任”）。
- **U2**：“逐字节相同”比较批次的 `as_of`、`predicted_at`、`artifact.feature_ids`、
  `artifact.parameters`（权重）、`artifact.training_cutoff`、`artifact.training_example_count`、
  每只证券的 `(ts_code, score)`，以及声明里的 `name`、`family`、`horizon`；**不比较**因登记而必然
  不同的 `feature_version`（`config_id`）、`seed`、`code_commit`、`hyperparameters`。
- **U3**：前任与继任的 `survivor_forward_protocol`、`survivor_forward_power`、
  `survivor_forward_stop_rule` 三个块按 JSON 文本逐一相等（都缺也算相等），否则按名拒绝；样本
  序号从**链根**的纪元起数。
- **U4**：前任的记录先用**前任自己的配置**跑迟到记录核查；`refused` 的那天直接作为截断点，不重算；
  `verified` 与 `UNVERIFIABLE` 的都进入等价比较，并入后保留它自己的核查结论。
- **U5**：截断之后到继任第一条记录之间的交易日是窗口里的**缺口**（`no_record`），减少期数 n、
  不移动窗口。
- **U6**：冒烟登记**永远不并入**非冒烟登记；冒烟输出不是证据。
- **U7**：每次周报都重算等价判定，不做缓存；面板后来被更正只可能把截断点往前移，并打印出来。

## 10. 家族与智能体分数映射（方案 C，占位）

本登记的家族只有 `survivor_book_composite/v1` 一个打分定义；`settings` 不带任何 `agent_*` 块（D-4）。
方案 C 的 `agent_*` 分数映射暂定 `score = strength`、弃权 → `abstention`，G-C 暂定判据（决策链 §8
第 5 项、`:232`）。**这一段是占位文字，不是本登记的判据**；它由 C 自己的登记在第一条 `agent_*`
前向记录之前冻结（D-4）。

## 11. 不是证据的数字

以下每个数字都**不是证据**，引用时必须带抽样 se，且不得用来支持本登记的结论：

- T8 的仅主板、N=10、样本内：净超额 +6.1–6.3%/年、跟踪误差 11.7%、单年跑输约 27%
  （`docs/research/p7-step0-validation/T8-main-board-remeasure.md:16` 的 +6.33%/+6.06%、11.70%、27%）。
  **不是证据。**
- T9 的无前视口径重测把 T8 的期望数字下调约 0.9pp/年（−0.91pp/年，样本 se 0.30；
  `docs/research/p7-step0-validation/T9-delisted-while-held.md:21`）；路线文档的口径是
  “样本内 +5.9%，抽样 se 约 ±2.9pp”（`docs/superpowers/plans/2026-11-01-p7-route-adjustment.md:77`）。
  **不是证据。**
- 验证期的一次读数：h20 秩 IC 均值 0.09459（sd 0.1821、n 24、t 2.544），
  `ew_minus_worst_decile` 均值 0.008688
  （`docs/research/p7-readings/v2-p7-024-validation.result.txt:6-9`）。它是一次读数，
  **不是证据**，也不是规划值。
- 停止规则的“未触发”只允许 `V2-P7-006` 继续，**不是证据**。

## 12. 版本记录（只追加）

- 2026-11-12：本文件随 `V2-P7-006b` 的实施提交写出，早于登记、早于任何前向运行。登记尚未发生；
  登记提交、sha256、`config_id`、纪元与钉住的 commit 在 Task 7 之后追加于此，正文不改。
