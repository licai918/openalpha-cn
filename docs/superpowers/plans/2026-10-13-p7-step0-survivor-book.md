# P7 Step 0 幸存者持仓框架 — 实施计划 1（技术验证 + 纯 Python 领域内核）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先用只读的技术验证回答设计里的未决问题（Phase 0），同时用测试先行把幸存者持仓框架的**纯 Python 领域内核**（配置、资格与幸存者筛选、选择规则、粘性持仓策略、订单与交易清单、订单生命周期）做出来并验证（Phase 1）。

**Architecture:** 领域内核全部是 `domain/` 下的纯函数与冻结的 pydantic 契约，只依赖标准库、`pydantic` 与已有 `domain/` 契约，不碰面板。三层（资格+幸存者 → 可插拔选择层 → 粘性持仓策略）对应设计 §4.1；选择层以 `SelectionRule` 协议留出方案 A/B/C 的接入口。适配器、每日入口、纸面账本、登记属于**计划 2**，要等 Phase 0 的结果（尤其 T4、T5）才能定位，故不在本计划里。

**Tech Stack:** Python 3.12+、pydantic v2、`decimal`、`hashlib`、pytest；门禁 `ruff`、`mypy src`、`lint-imports`（`domain-purity`）。

**Spec:** `docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md`（设计）；上游 `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md`（事实、决定、闸门、修订记录 §9）；`docs/architecture/ADR-0005-single-decision-engine.md`。执行方式由用户在 2026-10-13 选定：**sub-agent 驱动开发 + 测试先行**。

## Global Constraints

逐字取自 `AGENTS.md` 与设计文档，每个任务的要求都隐含这一节。

- 每个改动在分支、提交和 PR 中都写 issue ID：本计划的领域内核归 `V2-P7-005`（订单、交易清单、生命周期同时归 `V2-P7-008`）；Phase 0 的验证报告归 `V2-P7-005`。分支名 `feat/v2-p7-005-survivor-book-core`。
- `domain/` 不引入数值或基础设施库：不得 import numpy、pandas、sqlite3、duckdb（`pyproject.toml` 的 `domain-purity` 契约 + `lint-imports` 强制）；`DataFrame`/`ndarray` 只在 `panel/`、`factors/`、`models/`。**不得为了让类型检查通过而放宽这条闸门。**
- 不做破坏性契约变更：本计划只**新增**契约（`survivor-book-config/v1`、`trade-order/v1`、`trade-list/v1`），不改任何已有契约。
- 先写会失败的行为测试，再写实现；保持仓库可运行；一次只做一个 issue。
- 永不提交凭据、运行期数据库、用户输出或未授权的原始数据集；测试夹具在测试时生成，不入库 `.parquet / .duckdb / .sqlite3 / .db`。
- 不把 stub、mock、文件名或 UI 控件当成已完成的功能。
- 措辞一律"候选"；工程完成不等于研究有效；任何措辞升级只靠前向证据。
- 通过本地发布闸门之前不发布、不强推。合并方式只能是 merge commit 或 fast-forward，**不得 squash / rebase**。
- 已登记代码绑定覆盖 `src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`、`scripts/daily_selection.py`：本计划改 `src/` 会使现行登记失效。这是预期的——每日登记自 2026-10-08 起暂停，新配置在 `V2-P7-006` 一次性重新登记。
- 全量套件必须**串行**运行（并发四个套件曾把 40 分钟跑成 9 小时）；绝不在 `git --exec`/hook 里跑测试；不要在测试运行期间编辑被测检出目录（检出守卫会让这次运行判失败）。
- 提交信息末尾加：`Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`。
- 在隔离 worktree 里运行测试时，venv 里的包指向主检出，必须设 `PYTHONPATH="$PWD/src"`；解释器用主仓库 venv：

```bash
PY="/Users/licai918me.com/Documents/OpenAlpha cn/.venv/bin"   # 之后写作 $PY/python、$PY/ruff、$PY/mypy、$PY/lint-imports
```

## Review Focus

规格隐含、但没有哪个任务的主测试专门演练的输入或失败模式，最可能伤到使用者的五类（每条的测试已加到负责该代码的任务里）：

1. **持仓在筛选里查无此股**（退市、数据缺口、代码变更）：应当按"失去资格"强制卖出，而不是静默持有或崩溃。→ Task 4 `test_a_holding_the_screen_never_assessed_is_sold_as_ineligible`、`test_an_empty_screen_sells_everything_it_cannot_see_and_buys_nothing`。
2. **候选少于空位 / 全体幸存者被冷却或在途订单占住 / 当前权益买不起任何一手**：应当部分建仓或不动，并把原因报出来（`unaffordable`、`watch_list`），不得造出零股订单或超额订单。→ Task 4 `test_a_book_with_too_few_entrants_is_built_part_way`、`test_an_entrant_the_current_equity_cannot_buy_a_lot_of_is_skipped_and_reported`；Task 5 `test_a_buy_that_cannot_afford_one_lot_is_not_placed`。
3. **现金不够**（满仓时卖出款尚未到账、费用与滑点推高成本）：买单按"现金 + 同日卖出的预估款"向下取整，且每笔预留 `cost_reserve_fraction`，不得因费用把最后一笔买单挤出现金。→ Task 5 `test_a_buy_is_limited_by_the_cash_that_will_be_there`、`test_each_buy_spends_from_what_the_one_before_left`；Task 7 仿真里 `cash >= 0` 的逐日断言。
4. **输入顺序与进程差异**（集合迭代顺序、`PYTHONHASHSEED`、重复代码）：同一输入必须得到逐字节相同的输出。→ Task 2 的子进程测试、Task 3 `test_the_result_does_not_depend_on_the_order_the_facts_arrive_in`、Task 4 `test_the_decision_is_the_same_however_the_holdings_are_listed`。
5. **同一证券被重复列出 / 因子值是 NaN 或 ±inf / 价格为零或负**：应当具名拒绝，而不是算出一个看似合理的数字。→ Task 1 `test_a_security_fact_that_cannot_be_true_is_refused`；Task 3 `test_a_security_listed_twice_is_refused`；Task 1 `test_a_lot_is_not_priced_at_zero`。

## 文件结构

**新建（源码，均在 `src/openalpha_cn/domain/`）**

| 文件 | 职责 |
|---|---|
| `survivor_book.py` | 词汇与配置：`Board`、`EligibilityReason`、`SurvivorBookConfig`、`SecurityFacts`、`EligibilityVerdict`、`RankedName`、`ScreenResult`，以及 `floor_share`、`lot_quantity` |
| `survivor_selection.py` | 选择层：`SelectionRule` 协议、`SeededHashRule`（`seeded_hash/v1`）、`selection_rule()` |
| `survivor_screen.py` | 资格与幸存者筛选：`composite_of`、`screen` |
| `survivor_policy.py` | 粘性持仓策略：`BookState`、`Holding`、`Cooldown`、`BookDecision`、`decide_book` |
| `survivor_orders.py` | 订单与交易清单：`TradeOrder`、`TradeList`、`build_trade_list` |
| `survivor_lifecycle.py` | 订单生命周期：`OrderAdvance`、`advance_orders`、`update_cooldowns` |

**新建（测试，均在 `tests/unit/domain/`）：** `test_survivor_book.py`、`test_survivor_selection.py`、`test_survivor_screen.py`、`test_survivor_policy.py`、`test_survivor_orders.py`、`test_survivor_lifecycle.py`、`test_survivor_book_lifecycle.py`（逐日仿真的不变量）。

**修改（守卫与文档，没有行为变化）：** `tests/unit/domain/test_contract_identity.py`（声明新的哈希站点）、`tests/unit/domain/test_manifest_component_provenance.py`（前缀普查）、`src/openalpha_cn/backtest/candidate_ranking.py`（一句被测试计数的话）、设计文档 §6/§7/§12、`docs/specs/v2/openalpha-cn-v2-roadmap.md`、`CHANGELOG.md`。

**与设计文档的差异（实现时的取舍，已在 Task 7 回写设计文档）**

1. 设计 §7 清单列里的 `CARRIED` 不作为 `reason`：订单保留**原始**原因，是否延续由 `TradeList.carried` 与 `status` 表达（订单一旦计划就不可变，原因改了身份就变了）。
2. 新增配置项 `cost_reserve_fraction`（默认 0.005）：每笔买单预算预留费用与滑点，否则 10 笔恰好 `资金/10` 的买单会被成本推过现金。属预登记候选，冻结前可改。
3. "每日替换上限 2 笔"的精确语义：**强制退出（失去资格）不限、不顺延**；**自主退出（掉出幸存集合）每日最多 2 笔，按合成分由低到高**，其余顺延；**首次建仓之后**每日买入最多 2 笔。
4. 订单有效期内未成交而过期的**买单**也触发冷却（避免连续涨停、停牌的名字第二天又被追）。
5. 交易日用整数序号（`session`）表示：领域内核不持有日历，适配器负责日期与序号互转。

## Phase 0 — 技术验证（只读；与 Phase 1 互不依赖，可并行）

**共同规则（每个验证任务都适用）**

- 目的是回答设计 §10 里的问题，**不是**去改进策略。结果可以是"这个做不成"，那是有价值的答案。
- **只读**：研究面板 `~/openalpha-research/panel` 及其同级目录一律不写；需要写的脚本或中间结果放在 `~/openalpha-research/diagnostics/p7/`（已存放探索期的诊断脚本）或你自己的隔离 worktree 之外的临时目录。不联网，不调用任何数据供应商。不读 `.env`。
- 只用研究期窗口（2017-01-01 … 2021-10-31）里的数据下结论；**不得触碰验证期与已花掉的保持窗口（2024-01-02 … 2026-09-24）**——它们已被 P6 的唯一一次预登记用掉了，再看只能算非确认性。
- 报告写成 `docs/research/p7-step0-validation/<编号>-<主题>.md`，中文，代码标识符与路径保持原文；每个结论配 `file:line` 或可复现命令。**必须包含"对方案 A / B / C 的影响"一节**（没有影响就写"无"并说明为什么）——用户要求任何会实质影响后续方案的结果立刻提出。
- 完成后只在回复里给：结论（≤5 行）、报告路径、会影响后续方案的发现。控制者负责提交报告并回写设计 §12 与上游文档 §9。

### Task 0.1：T3 — ST 与次新的数据来源

**Files:** 读 `src/openalpha_cn/domain/name_history.py`（`risk_warning_on`，约 310 行）、`src/openalpha_cn/factor_view.py`（约 1361、1575 行，`MarketBar.is_st`）、`src/openalpha_cn/panel_ingest.py`（`load_name_histories`，约 1584 行）、`src/openalpha_cn/domain/stock_universe.py`（`list_date` / `delist_date` 语义）、`src/openalpha_cn/strategy_view.py`（约 2023 行把 `is_st` 写死为 `False`）；面板 `~/openalpha-research/panel` 的名称史与股票池数据集。
**产出:** `docs/research/p7-step0-validation/T3-st-and-new-listings.md`

- [ ] **Step 1：** 回答：(a) 研究库里有没有可靠的"某日是否 ST / *ST / PT"来源？覆盖哪些日期、多少证券？(b) 上市日期（`list_date`）能否给出"上市满 250 个交易日"？对 2017–2021 的每个信号日，能算出多少证券的上市交易日数？(c) 为什么 `strategy_view` 把 `is_st` 写死为 `False`——是缺来源，还是遗漏？
- [ ] **Step 2：** 用只读 DuckDB 查询在研究期抽 5 个信号日，统计：ST 证券数、次新（<250 个交易日）证券数、两者各占流动性前 70% 的比例。给出可复现的查询。
- [ ] **Step 3：** 写结论与决定：有可靠来源 → "照设计，来源是 X，读取接口是 Y"；没有 → "该过滤降级为已披露限制，原因 Z"。写"对 A/B/C 的影响"。

### Task 0.2：T4 — 现有引擎能否表达幸存者持仓

**Files:** 读 `src/openalpha_cn/backtest/strategy_backtest.py`（`StrategySpec` 约 773 行，`buffer_rank` 约 2362–2370 行）、`src/openalpha_cn/strategy_view.py`、`src/openalpha_cn/strategy_registration.py`（约 219–245 行的 `strategy_<kind>` 先例、`AlphaModelDeclaration.family`）、`scripts/daily_selection.py`（约 843、2490 行附近的策略配置与目标权重）、`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §2、`docs/architecture/ADR-0005-single-decision-engine.md`。
**产出:** `docs/research/p7-step0-validation/T4-engine-expressiveness.md`

- [ ] **Step 1：** 回答：用 `buffer_rank`（带缓冲的排名持有）能否表达"粘性幸存者持仓"？逐项对照设计 §4.1：(a) 剔除合成分最差两个十分位 = 幸存者；(b) 仅当失去资格或掉出幸存者才卖；(c) 补仓用选择层顺序而非排名；(d) 冷却、每日替换上限、订单有效期。哪些能表达、哪些不能，各缺什么？
- [ ] **Step 2：** 回答：新形态应当是 (i) 在现有每日入口里新增一种 `strategy.kind`（需不需要改 `StrategySpec` 或登记契约？改了是否构成破坏性契约变更，需要 `V2-P0B-004` 迁移？）还是 (ii) 一个新模块 + 新契约、不动 `StrategySpec`？给出两种做法各自要改的文件清单与对已登记配置的影响。
- [ ] **Step 3：** 回答：预测登记用 `family="survivor_book_composite/v1"` 是否可行（对照 `strategy_<kind>` 先例、`AlphaModelDeclaration.family` 是自由字符串）？每个可投资证券一个 `score`，登记容器要求哪些字段？
- [ ] **Step 4：** 结论与决定：推荐 (i) 或 (ii) 并说明理由；写出领域内核（本计划 Phase 1）被适配器调用时的接口形状建议。写"对 A/B/C 的影响"（特别是方案 A 的否决层与方案 B 的模型分数能否复用同一接入点）。

### Task 0.3：T5 — 纸面账本的步骤能否由现有行情组装

**Files:** 读 `src/openalpha_cn/backtest/paper.py`（`PaperPortfolio.advance`，约 326–387 行）、`src/openalpha_cn/backtest/multi_day.py`（`PortfolioBacktestStep`，约 88 行）、`src/openalpha_cn/backtest/portfolio.py`（`PortfolioOrder`、`PortfolioState`、`PortfolioLimits`）、`src/openalpha_cn/backtest/execution.py`（`AShareExecutionPolicy`、`security_board`，约 184 行）、`src/openalpha_cn/strategy_view.py` 里组装 `MarketBar` 的函数（约 2023 行附近）。
**产出:** `docs/research/p7-step0-validation/T5-paper-step-builder.md`

- [ ] **Step 1：** 回答：给定一个已观察交易日的开盘价、涨跌停价、停牌标记，能否直接用现有行情组装函数得到 `PortfolioBacktestStep`（`bars` + `orders` + `benchmark_close`）？缺什么字段？`benchmark_close` 在 Step 0 里取什么（账户可交易板块内的可投资等权？中证 500？）？
- [ ] **Step 2：** 回答：本计划的 `TradeOrder`（`order_id`、`side`、`ts_code`、`quantity`、`reference_price`、`execute_session`、`expires_session`）如何映射到 `PortfolioOrder`？`PaperPortfolio` 对买单现金不足、涨停买不进、跌停卖不出、停牌的行为是什么（拒绝？部分成交？静默跳过？）——据此判断"未成交订单顺延 / 过期"要在谁那里实现，并写出需要由适配器补的部分。
- [ ] **Step 3：** 在隔离环境里用手写的 3 日夹具做一次小型原型（只放在报告里或 `~/openalpha-research/diagnostics/p7/`，**不入库**）：开盘成交、涨停买单、跌停卖单，确认上面的回答。
- [ ] **Step 4：** 结论与决定：复用 / 抽共用函数（不复制）/ 需要新增什么。写"对 A/B/C 的影响"。

### Task 0.4：T6 + T7 — Step 0 每日的真实耗时与请求数

**Files:** 读 `scripts/daily_selection.py`（`run_daily_selection`，约 2577 行；各阶段）、`src/openalpha_cn/panel_ingest.py` 与 `src/openalpha_cn/panel_factors.py` 的增量更新与因子构建、`docs/runbooks/daily-selection.zh-CN.md`、`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §3.8（用量限制）。
**产出:** `docs/research/p7-step0-validation/T6-T7-daily-cost.md`

- [ ] **Step 1（T7）：** 列出 Step 0 每日**必需**的数据集（价格、复权、涨跌停、停牌、日历、登记簿、名称史、指数；**不含**财报与 walk-forward 模型），对照现有增量规则，给出每日请求数的上界，并与设计 §10 的阈值（100）比较。
- [ ] **Step 2（T6）：** 在**面板的临时副本**（只复制四个因子依赖的数据集中最近约 400 个交易日的分区到你自己的临时目录）上，用真实代码路径计时：构建 `residual_vol_60`、`return_vol_60`、`turnover_60`、`downside_vol_60` 的 `processed` 档 + 资格所需的读取 + 合成分。**不得写 `~/openalpha-research/panel`。** 报告机器规格、数据量、各阶段秒数。
- [ ] **Step 3：** 与预期（数分钟）比较：相差大 → 先查原因（哪个阶段、为什么），不要直接下结论。写"对 A/B/C 的影响"（特别是每日预算是否容得下方案 C 的智能体研究步骤）。

### Task 0.5：T1 — 分层抽样能否压低 N=10 的跟踪误差

**Files:** 读/改 `~/openalpha-research/diagnostics/p7/small_books_elig.py`、`decile.py`、`sampling.py`（探索期脚本；辅助函数 `con, days, idx, sig, load, H` 在 `decile.py`；环境变量 `BOARDS=main_chinext`）。
**产出:** `docs/research/p7-step0-validation/T1-stratified-vs-random.md`；新脚本放 `~/openalpha-research/diagnostics/p7/stratified.py`。

- [ ] **Step 1：** 在研究期、沪深主板 + 创业板、N=10、粘性（只在掉出幸存集合时才换）的设定下，对比两种入选方式：(a) 随机（种子哈希）；(b) 按"规模 × 行业"分层抽样（每层至多一只）。各用 60 个种子，报告净超额均值、跟踪误差均值、单年超额 p10·中位·p90、单年跑输概率。蒙特卡洛噪声约 ±0.7%，小于它的差异不算差异。
- [ ] **Step 2：** 决定规则：分层的跟踪误差**明显更低**（相对随机至少低 1.5 个百分点且净超额不更差）→ 记为"预登记的选择层候选之一（对照臂，不替换默认）"；否则 → "不做"。**不要**据此选默认规则。
- [ ] **Step 3：** 写"对 A/B/C 的影响"。

## Phase 1 — 领域内核（测试先行）

**所有 Phase 1 任务共用的流程。** 每个任务由一个全新的实施子代理在隔离 worktree 里执行，任务评审（规格符合性 + 代码质量）通过后由控制者 fast-forward 合并进 `feat/v2-p7-005-survivor-book-core`，再派下一个任务。每个任务：先写测试 → 运行并确认**因预期原因失败** → 写最小实现 → 运行确认通过 → 跑静态检查 → 更新该任务点名的守卫 → 提交。**测试代码和实现代码必须逐字使用本计划里的内容**——它们已在一个一次性 worktree 里验证过（全部测试通过，45 个变异全部被杀死）；若运行结果与计划写的"预期"不同，**停下来报告**，不要自己改测试去迁就实现。

### Task 1：词汇与配置 `survivor_book.py`

**Files:**
- Create: `src/openalpha_cn/domain/survivor_book.py`
- Create: `tests/unit/domain/test_survivor_book.py`
- Modify: `tests/unit/domain/test_manifest_component_provenance.py`（前缀普查）
- Modify: `src/openalpha_cn/backtest/candidate_ranking.py`（一句被计数的话）

**Interfaces:**
- Consumes: `openalpha_cn.domain._identity.stable_model_id(*, prefix, model, exclude=...)`。
- Produces（后续任务依赖，名字与类型不得改）：`Board = Literal["main","growth","star","bse"]`；`EligibilityReason`（七个原因码）；`ELIGIBILITY_REASON_ORDER: tuple[EligibilityReason, ...]`；`DEFAULT_COMPOSITE_FACTORS`；`SurvivorBookConfig`（见下）及 `.sizing_budget(equity: Decimal | None = None) -> Decimal`、`.config_id`；`SecurityFacts`、`EligibilityVerdict`（`.eligible`）、`RankedName`、`ScreenResult`；`floor_share(count: int, fraction: Decimal) -> int`；`lot_quantity(budget: Decimal, price: Decimal, lot_size: int) -> int`。

- [ ] **Step 1：写失败的测试** — 创建 `tests/unit/domain/test_survivor_book.py`：

```python
"""The survivor book's vocabulary and configuration (`V2-P7-005`).

What is pinned here is that the configuration is a *contract* -- frozen, versioned, content-
addressed, with no default for the seed -- and that the two quantities every later layer leans on,
the share of a list a fraction covers and the lots a budget buys, round the way the design says.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.survivor_book import (
    DEFAULT_COMPOSITE_FACTORS,
    ELIGIBILITY_REASON_ORDER,
    EligibilityVerdict,
    RankedName,
    SecurityFacts,
    SurvivorBookConfig,
    floor_share,
    lot_quantity,
)


def config(**overrides: object) -> SurvivorBookConfig:
    return SurvivorBookConfig(seed=7, **overrides)  # type: ignore[arg-type]


def test_the_defaults_are_the_values_the_design_names() -> None:
    held = config()

    assert held.schema_version == "survivor-book-config/v1"
    assert held.holding_count == 10
    assert held.capital == Decimal("350000")
    assert held.boards == ("main", "growth")
    assert held.liquidity_drop_fraction == Decimal("0.30")
    assert held.min_listing_sessions == 250
    assert held.loser_decile_count == 2
    assert held.entry_floor_fraction == Decimal("0.25")
    assert held.near_exit_fraction == Decimal("0.10")
    assert held.cooldown_sessions == 20
    assert held.max_replacements_per_session == 2
    assert held.buy_order_expiry_sessions == 3
    assert held.lot_size == 100
    assert held.cost_reserve_fraction == Decimal("0.005")
    assert held.selection_rule == "seeded_hash/v1"
    assert held.composite_factors == DEFAULT_COMPOSITE_FACTORS
    assert DEFAULT_COMPOSITE_FACTORS == (
        "residual_vol_60/v1",
        "return_vol_60/v1",
        "turnover_60/v1",
        "downside_vol_60/v1",
    )


def test_the_seed_has_no_default_so_a_registration_has_to_name_it() -> None:
    with pytest.raises(ValidationError, match="seed"):
        SurvivorBookConfig()  # type: ignore[call-arg]


def test_the_configuration_is_frozen_and_refuses_unknown_fields() -> None:
    held = config()

    with pytest.raises(ValidationError):
        held.holding_count = 11  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SurvivorBookConfig(seed=1, surprise=True)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"holding_count": 0}, "holding_count"),
        ({"capital": Decimal("0")}, "capital"),
        ({"liquidity_drop_fraction": Decimal("1")}, "liquidity_drop_fraction"),
        ({"loser_decile_count": 10}, "loser_decile_count"),
        ({"entry_floor_fraction": Decimal("-0.1")}, "entry_floor_fraction"),
        ({"cooldown_sessions": -1}, "cooldown_sessions"),
        ({"max_replacements_per_session": 0}, "max_replacements_per_session"),
        ({"buy_order_expiry_sessions": 0}, "buy_order_expiry_sessions"),
        ({"lot_size": 0}, "lot_size"),
        ({"cost_reserve_fraction": Decimal("0.1")}, "cost_reserve_fraction"),
        ({"boards": ()}, "boards"),
        ({"boards": ("main", "main")}, "boards"),
        ({"boards": ("main", "nasdaq")}, "boards"),
        ({"composite_factors": ()}, "composite_factors"),
        ({"composite_factors": ("a/v1", "a/v1")}, "composite_factors"),
        ({"selection_rule": "highest_composite/v1"}, "selection_rule"),
    ],
)
def test_a_value_outside_what_the_design_allows_is_refused(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        config(**overrides)


def test_the_identity_is_a_content_address_that_moves_with_every_parameter() -> None:
    base = config()

    assert base.config_id.startswith("sbc_")
    assert len(base.config_id) == len("sbc_") + 24
    assert config().config_id == base.config_id
    for changed in (
        config(holding_count=9),
        config(capital=Decimal("350001")),
        config(boards=("main",)),
        config(cooldown_sessions=21),
        SurvivorBookConfig(seed=8),
    ):
        assert changed.config_id != base.config_id


def test_the_stored_form_reads_back_without_the_computed_identity() -> None:
    base = config()
    stored = base.model_dump_json(exclude_computed_fields=True)

    assert SurvivorBookConfig.model_validate_json(stored) == base
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SurvivorBookConfig.model_validate_json(base.model_dump_json())


def test_the_sizing_budget_is_an_equal_share_less_the_cost_reserve() -> None:
    held = config()

    assert held.sizing_budget() == Decimal("34825")
    assert held.sizing_budget(Decimal("400000")) == Decimal("39800")
    assert config(cost_reserve_fraction=Decimal("0")).sizing_budget() == Decimal("35000")


@pytest.mark.parametrize(
    ("count", "fraction", "expected"),
    [
        (10, Decimal("0.30"), 3),
        (9, Decimal("0.30"), 2),
        (3, Decimal("0.30"), 0),
        (0, Decimal("0.30"), 0),
        (20, Decimal("0"), 0),
        (7, Decimal("0.999"), 6),
    ],
)
def test_a_fraction_of_a_list_rounds_down(count: int, fraction: Decimal, expected: int) -> None:
    assert floor_share(count, fraction) == expected


@pytest.mark.parametrize(
    ("budget", "price", "expected"),
    [
        (Decimal("34825"), Decimal("10"), 3400),
        (Decimal("34825"), Decimal("348.25"), 100),
        (Decimal("34825"), Decimal("348.26"), 0),
        (Decimal("999"), Decimal("10"), 0),
        (Decimal("1000"), Decimal("10"), 100),
        (Decimal("0"), Decimal("10"), 0),
    ],
)
def test_a_budget_buys_whole_lots_and_no_more(
    budget: Decimal, price: Decimal, expected: int
) -> None:
    assert lot_quantity(budget, price, 100) == expected


def test_a_lot_is_not_priced_at_zero() -> None:
    with pytest.raises(ValueError, match="price must be positive"):
        lot_quantity(Decimal("1000"), Decimal("0"), 100)


def facts(**overrides: object) -> SecurityFacts:
    settings: dict[str, object] = {
        "ts_code": "600000.SH",
        "board": "main",
        "listed_sessions": 300,
        "special_treatment": False,
        "suspended": False,
        "close": Decimal("10"),
        "avg_amount": Decimal("100"),
        "oriented_values": (1.0, 2.0, None, 4.0),
    }
    settings.update(overrides)
    return SecurityFacts(**settings)  # type: ignore[arg-type]


def test_a_security_fact_keeps_a_missing_factor_value_as_none() -> None:
    assert facts().oriented_values == (1.0, 2.0, None, 4.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"oriented_values": (float("nan"),)},
        {"oriented_values": (float("inf"),)},
        {"close": Decimal("0")},
        {"avg_amount": Decimal("-1")},
        {"listed_sessions": -1},
        {"board": "nasdaq"},
        {"ts_code": ""},
    ],
)
def test_a_security_fact_that_cannot_be_true_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        facts(**overrides)


def test_a_verdict_is_eligible_exactly_when_it_has_no_reason() -> None:
    assert EligibilityVerdict(ts_code="600000.SH", composite=1.0).eligible
    assert not EligibilityVerdict(ts_code="600000.SH", reasons=("suspended",)).eligible


def test_the_reason_order_is_the_order_the_design_lists_them_in() -> None:
    assert ELIGIBILITY_REASON_ORDER == (
        "board_not_allowed",
        "listing_too_recent",
        "special_treatment",
        "suspended",
        "missing_factor_value",
        "unaffordable_lot",
        "illiquid",
    )


def test_a_ranked_name_starts_at_rank_one_and_has_a_positive_close() -> None:
    RankedName(ts_code="600000.SH", composite=0.5, rank=1, close=Decimal("10"))
    with pytest.raises(ValidationError):
        RankedName(ts_code="600000.SH", composite=0.5, rank=0, close=Decimal("10"))
    with pytest.raises(ValidationError):
        RankedName(ts_code="600000.SH", composite=0.5, rank=1, close=Decimal("0"))
```

- [ ] **Step 2：运行，确认因模块不存在而失败**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_book.py -q -p no:cacheprovider
```
预期：收集阶段 `ModuleNotFoundError: No module named 'openalpha_cn.domain.survivor_book'`。

- [ ] **Step 3：写实现** — 创建 `src/openalpha_cn/domain/survivor_book.py`：

```python
"""Vocabulary and configuration of the survivor book (`V2-P7-005`).

A *survivor book* holds at most `holding_count` securities. It first drops every security the
account cannot or should not hold (**eligibility**), then drops the worst-scoring tail of what is
left (**survivors**), and afterwards changes its holdings only when a holding stops being
eligible or stops being a survivor. Which survivor fills a vacancy is the job of a pluggable
selection rule (`domain/survivor_selection.py`); this module only names the things the three
layers talk about.

Everything here is pure Python on `Decimal`, `float` and `str`: no numeric or infrastructure
library, so the whole core can be tested without a panel (`AGENTS.md` hard rule 2). The design is
`docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md`; every number that is a
default below is a **pre-registration candidate** there, frozen only by `V2-P7-006`.
"""

from decimal import Decimal
from typing import Annotated, Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from openalpha_cn.domain._identity import stable_model_id

Board = Literal["main", "growth", "star", "bse"]
"""The vocabulary `backtest.execution.security_board` already answers in (`growth` is ChiNext).

Reused rather than re-derived: `V2-P6-031` removed three private copies of that classifier, and a
fourth spelling here would be the same defect again. The adapter fills `SecurityFacts.board` from
that function; this module never looks at a code's digits.
"""

EligibilityReason = Literal[
    "board_not_allowed",
    "listing_too_recent",
    "special_treatment",
    "suspended",
    "missing_factor_value",
    "unaffordable_lot",
    "illiquid",
]
ELIGIBILITY_REASON_ORDER: Final[tuple[EligibilityReason, ...]] = get_args(EligibilityReason)
"""The fixed order a verdict lists its reasons in, so two runs print the same line."""

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]

DEFAULT_COMPOSITE_FACTORS: Final[tuple[str, ...]] = (
    "residual_vol_60/v1",
    "return_vol_60/v1",
    "turnover_60/v1",
    "downside_vol_60/v1",
)


class SurvivorBookConfig(BaseModel):
    """Every parameter of the survivor book. Frozen, versioned and content-addressed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["survivor-book-config/v1"] = "survivor-book-config/v1"
    holding_count: int = Field(default=10, ge=1, le=50)
    capital: Decimal = Field(default=Decimal("350000"), gt=0)
    boards: tuple[Board, ...] = ("main", "growth")
    liquidity_drop_fraction: Decimal = Field(default=Decimal("0.30"), ge=0, lt=1)
    min_listing_sessions: int = Field(default=250, ge=0)
    loser_decile_count: int = Field(default=2, ge=0, le=9)
    entry_floor_fraction: Decimal = Field(default=Decimal("0.25"), ge=0, lt=1)
    near_exit_fraction: Decimal = Field(default=Decimal("0.10"), ge=0, lt=1)
    cooldown_sessions: int = Field(default=20, ge=0)
    max_replacements_per_session: int = Field(default=2, ge=1)
    buy_order_expiry_sessions: int = Field(default=3, ge=1)
    lot_size: int = Field(default=100, ge=1)
    cost_reserve_fraction: Decimal = Field(default=Decimal("0.005"), ge=0, lt=Decimal("0.1"))
    """Held back from every buy's budget so fees and slippage cannot push the last buy past the
    cash that is left: ten buys of exactly `capital / 10` would otherwise overspend by the costs."""
    selection_rule: Literal["seeded_hash/v1"] = "seeded_hash/v1"
    seed: int = Field(ge=0)
    """No default on purpose: a registration has to name the seed it was frozen with."""
    composite_factors: tuple[str, ...] = DEFAULT_COMPOSITE_FACTORS

    @model_validator(mode="after")
    def _lists_are_nonempty_and_unique(self) -> Self:
        if not self.boards or len(set(self.boards)) != len(self.boards):
            raise ValueError("boards must be a non-empty list without repeats")
        if not self.composite_factors or len(set(self.composite_factors)) != len(
            self.composite_factors
        ):
            raise ValueError("composite_factors must be a non-empty list without repeats")
        return self

    def sizing_budget(self, equity: Decimal | None = None) -> Decimal:
        """What one new holding may spend: an equal share of `equity`, less the cost reserve.

        `equity` defaults to the starting `capital`; the daily flow passes the ledger's current
        equity, so a book that has grown or shrunk is still sized to what it is worth.
        """
        basis = self.capital if equity is None else equity
        return basis / self.holding_count * (1 - self.cost_reserve_fraction)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def config_id(self) -> str:
        return stable_model_id(prefix="sbc", model=self)


def floor_share(count: int, fraction: Decimal) -> int:
    """How many of `count` names a `fraction` of them covers, rounded down."""
    return int(Decimal(count) * fraction)


def lot_quantity(budget: Decimal, price: Decimal, lot_size: int) -> int:
    """Whole lots of `lot_size` that `budget` buys at `price`, as a share count; 0 if none."""
    if price <= 0:
        raise ValueError("price must be positive")
    return int(budget / price / lot_size) * lot_size


class SecurityFacts(BaseModel):
    """What the screen needs to know about one security on one signal day. Plain values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str = Field(min_length=1)
    board: Board
    listed_sessions: int = Field(ge=0)
    special_treatment: bool
    suspended: bool
    close: Decimal = Field(gt=0)
    """The raw (unadjusted) close: the price a lot is bought at, not a return input."""
    avg_amount: Decimal = Field(ge=0)
    """Trailing average traded amount, in the unit the adapter chose; only its order matters."""
    oriented_values: tuple[FiniteFloat | None, ...]
    """One processed factor value per `composite_factors` entry, sign-adjusted so that a higher
    number is better; `None` where the factor has no value for this security."""


class EligibilityVerdict(BaseModel):
    """Whether one security may be held, and every reason it may not."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str
    reasons: tuple[EligibilityReason, ...] = ()
    composite: FiniteFloat | None = None

    @property
    def eligible(self) -> bool:
        return not self.reasons


class RankedName(BaseModel):
    """A survivor and where it stands: rank 1 is the best composite among the survivors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str
    composite: FiniteFloat
    rank: int = Field(ge=1)
    close: Decimal = Field(gt=0)
    """Carried so the policy can size a lot without going back to the facts."""


class ScreenResult(BaseModel):
    """The output of the eligibility-and-survivor screen for one signal day."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdicts: tuple[EligibilityVerdict, ...]
    """Every security that was assessed, ordered by `ts_code`."""
    survivors: tuple[RankedName, ...]
    """The eligible securities that remain after the loser tail is dropped, best first."""
```

- [ ] **Step 4：运行，确认全部通过，再跑静态检查**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_book.py -q -p no:cacheprovider
"$PY/ruff" check src/openalpha_cn/domain/survivor_book.py tests/unit/domain/test_survivor_book.py
"$PY/ruff" format --check src/openalpha_cn/domain/survivor_book.py tests/unit/domain/test_survivor_book.py
"$PY/mypy" src/openalpha_cn/domain/survivor_book.py
"$PY/lint-imports"
```
预期：`46 passed`；`ruff` 与 `format` 无输出问题；`mypy` `Success`；`lint-imports` 的 `domain-purity` 为 KEPT（8 kept, 0 broken）。

- [ ] **Step 5：更新两个守卫（新增了一个 `stable_model_id` 调用点，前缀 `sbc`）**

(a) `tests/unit/domain/test_manifest_component_provenance.py`：把
```python
    assert len(prefixes) == 27
    assert len(set(prefixes)) == 24
```
改为
```python
    assert len(prefixes) == 28
    assert len(set(prefixes)) == 25
```
(b) `src/openalpha_cn/backtest/candidate_ranking.py` 里这句被测试计数的话（跨两行）：`that function has nineteen call` / `sites and no competitor` 中的 `nineteen` 改为 `twenty`。

运行：
```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_manifest_component_provenance.py tests/unit/domain/test_contract_identity.py tests/unit/test_source_counts_match_the_tree.py -q -p no:cacheprovider
```
预期：全部通过（`test_source_counts_match_the_tree.py` 约 75 秒）。

- [ ] **Step 6：提交**

```bash
git add src/openalpha_cn/domain/survivor_book.py tests/unit/domain/test_survivor_book.py tests/unit/domain/test_manifest_component_provenance.py src/openalpha_cn/backtest/candidate_ranking.py
git commit -m "feat(V2-P7-005): the survivor book's vocabulary and configuration

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 2：选择层 `survivor_selection.py`

**Files:**
- Create: `src/openalpha_cn/domain/survivor_selection.py`
- Create: `tests/unit/domain/test_survivor_selection.py`
- Modify: `tests/unit/domain/test_contract_identity.py`（`DECLARED_PLAIN_DIGESTS`）

**Interfaces:**
- Consumes: 无（只用 `hashlib`）。
- Produces：`SelectionRule`（`Protocol`，`rule_id: str`，`order(candidates: Sequence[str], *, seed: int) -> tuple[str, ...]`）；`SeededHashRule`；`SELECTION_RULES`；`selection_rule(rule_id: str) -> SelectionRule`（未知名字抛 `ValueError`，信息里列出已知名字）。

- [ ] **Step 1：写失败的测试** — 创建 `tests/unit/domain/test_survivor_selection.py`：

```python
"""The selection layer's default rule (`V2-P7-005`).

`seeded_hash/v1` claims no skill; what it owes is determinism. The order must be a function of the
candidate *set* and the seed, so every property below is a way of saying "and of nothing else":
not the order the candidates arrived in, not duplicates, not the day, and -- the one a first
prototype got wrong -- not `PYTHONHASHSEED`.
"""

import json
import os
import random
import subprocess
import sys
from hashlib import sha256

import pytest

from openalpha_cn.domain.survivor_selection import SeededHashRule, selection_rule

CODES = tuple(f"{600000 + position:06d}.SH" for position in range(40))


def test_the_order_is_each_distinct_candidate_once() -> None:
    drawn = SeededHashRule().order([*CODES, CODES[3], CODES[3]], seed=7)

    assert sorted(drawn) == sorted(CODES)
    assert len(drawn) == len(CODES)


def test_the_order_is_ascending_in_the_seeded_hash_and_nothing_else() -> None:
    drawn = SeededHashRule().order(CODES, seed=7)
    keys = [sha256(f"7|{code}".encode()).hexdigest() for code in drawn]

    assert keys == sorted(keys)


def test_the_order_does_not_depend_on_the_order_the_candidates_arrive_in() -> None:
    reference = SeededHashRule().order(CODES, seed=7)
    for shuffle_seed in range(10):
        shuffled = list(CODES)
        random.Random(shuffle_seed).shuffle(shuffled)

        assert SeededHashRule().order(shuffled, seed=7) == reference


def test_a_set_of_candidates_draws_the_same_as_the_sorted_list() -> None:
    assert SeededHashRule().order(set(CODES), seed=7) == SeededHashRule().order(
        sorted(CODES), seed=7
    )


def test_the_seed_changes_the_order() -> None:
    assert SeededHashRule().order(CODES, seed=7) != SeededHashRule().order(CODES, seed=8)


def test_the_order_is_pinned_for_a_small_fixed_input() -> None:
    """A change of canonical form (separator, encoding, algorithm) moves this and only this."""
    codes = ["000001.SZ", "000002.SZ", "600000.SH", "600519.SH", "300750.SZ"]

    assert SeededHashRule().order(codes, seed=42) == (
        "000002.SZ",
        "000001.SZ",
        "600000.SH",
        "600519.SH",
        "300750.SZ",
    )


def test_no_candidates_draw_nothing() -> None:
    assert SeededHashRule().order([], seed=7) == ()


def test_the_rule_is_looked_up_by_its_registered_name() -> None:
    assert isinstance(selection_rule("seeded_hash/v1"), SeededHashRule)
    with pytest.raises(ValueError, match=r"unknown selection rule 'nope'.*seeded_hash/v1"):
        selection_rule("nope")


def test_the_order_is_the_same_in_processes_with_different_hash_seeds() -> None:
    """The defect a prototype had: drawing from a `set` gave different numbers per process."""
    program = (
        "import json,sys;"
        "from openalpha_cn.domain.survivor_selection import SeededHashRule;"
        "codes=json.loads(sys.argv[1]);"
        "print(json.dumps(SeededHashRule().order(set(codes), seed=7)))"
    )
    answers = set()
    for hash_seed in ("0", "1", "12345"):
        completed = subprocess.run(
            [sys.executable, "-c", program, json.dumps(list(CODES))],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            check=True,
        )
        answers.add(completed.stdout.strip())

    assert answers == {json.dumps(list(SeededHashRule().order(CODES, seed=7)))}
```

- [ ] **Step 2：运行，确认因模块不存在而失败**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_selection.py -q -p no:cacheprovider
```
预期：`ModuleNotFoundError: No module named 'openalpha_cn.domain.survivor_selection'`。

- [ ] **Step 3：写实现** — 创建 `src/openalpha_cn/domain/survivor_selection.py`：

```python
"""The selection layer (`V2-P7-005`): which eligible survivor fills a vacancy, in what order.

The default rule, `seeded_hash/v1`, **claims no skill**. It exists because a book needs *some*
deterministic order, and the evidence behind this design (`docs/specs/v2/openalpha-cn-v2-p7-
decision-chain.md` section 3) says the value is in the filter and in not trading, not in a ranking
of the survivors. A better rule -- an agent's score (plan C/A), a model's (plan B) -- replaces it
by implementing `SelectionRule` and being named in the registered configuration.

**Determinism is the whole contract.** The order is a function of the candidate *set* and the
seed alone: not of the order the candidates were passed in, not of the day, and not of
`PYTHONHASHSEED` (no `set` or `dict` iteration order ever reaches the result -- candidates are
sorted before anything else happens). A first prototype that drew from a `set` produced different
numbers in different processes; `tests/unit/domain/test_survivor_selection.py` runs the rule in
child processes with different hash seeds so that cannot come back unnoticed.
"""

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Final, Protocol


class SelectionRule(Protocol):
    rule_id: str

    def order(self, candidates: Sequence[str], *, seed: int) -> tuple[str, ...]:
        """Return the distinct `candidates`, first pick first. Pure and deterministic."""
        ...


def _selection_digest(seed: int, ts_code: str) -> str:
    return sha256(f"{seed}|{ts_code}".encode()).hexdigest()


class SeededHashRule:
    """Order candidates by a seeded hash of their code, whatever the day or the input order."""

    rule_id: str = "seeded_hash/v1"

    def order(self, candidates: Sequence[str], *, seed: int) -> tuple[str, ...]:
        distinct = sorted(set(candidates))
        return tuple(sorted(distinct, key=lambda code: (_selection_digest(seed, code), code)))


SELECTION_RULES: Final[Mapping[str, SelectionRule]] = {SeededHashRule.rule_id: SeededHashRule()}


def selection_rule(rule_id: str) -> SelectionRule:
    try:
        return SELECTION_RULES[rule_id]
    except KeyError:
        known = ", ".join(sorted(SELECTION_RULES))
        raise ValueError(f"unknown selection rule {rule_id!r}; known: {known}") from None
```

- [ ] **Step 4：运行，确认通过，再跑静态检查**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_selection.py -q -p no:cacheprovider
"$PY/ruff" check src/openalpha_cn/domain/survivor_selection.py tests/unit/domain/test_survivor_selection.py
"$PY/ruff" format --check src/openalpha_cn/domain/survivor_selection.py tests/unit/domain/test_survivor_selection.py
"$PY/mypy" src/openalpha_cn/domain/survivor_selection.py
```
预期：`9 passed`（其中一个测试会起三个不同 `PYTHONHASHSEED` 的子进程）；其余无问题。

- [ ] **Step 5：声明新的哈希站点** — 新增了一处 `sha256(...).hexdigest()`（完整 64 位、从不截断，不是内容地址）。先运行守卫确认它变红：

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_contract_identity.py -q -p no:cacheprovider
```
预期：`test_every_hashlib_call_under_src_is_one_of_the_two_declared_kinds` 等三个测试失败，指出 `domain/survivor_selection.py::_selection_digest` 未声明。然后在 `tests/unit/domain/test_contract_identity.py` 的 `DECLARED_PLAIN_DIGESTS` 里，紧挨着 `"strategy_registration.py::InputProvenance.digest": (` 之前加入：

```python
    "domain/survivor_selection.py::_selection_digest": (
        "the whole 64 hex of `seed|ts_code`, used only to order candidates; never an address, "
        "never stored, and never compared to anything but another such digest (V2-P7-005)"
    ),
```
再运行同一命令，预期 `44 passed`。

- [ ] **Step 6：提交**

```bash
git add src/openalpha_cn/domain/survivor_selection.py tests/unit/domain/test_survivor_selection.py tests/unit/domain/test_contract_identity.py
git commit -m "feat(V2-P7-005): a seeded-hash selection rule that claims no skill

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 3：资格与幸存者筛选 `survivor_screen.py`

**Files:**
- Create: `src/openalpha_cn/domain/survivor_screen.py`
- Create: `tests/unit/domain/test_survivor_screen.py`

**Interfaces:**
- Consumes: Task 1 的 `SecurityFacts`、`SurvivorBookConfig`（`.sizing_budget`）、`EligibilityVerdict`、`RankedName`、`ScreenResult`、`ELIGIBILITY_REASON_ORDER`、`floor_share`。
- Produces：`composite_of(values: Sequence[float | None]) -> float | None`；`screen(facts: Sequence[SecurityFacts], config: SurvivorBookConfig, *, equity: Decimal | None = None) -> ScreenResult`（`verdicts` 按 `ts_code` 升序；`survivors` 按合成分从好到坏，`rank` 从 1 起）。

- [ ] **Step 1：写失败的测试** — 创建 `tests/unit/domain/test_survivor_screen.py`：

```python
"""The eligibility-and-survivor screen (`V2-P7-005`).

The fixtures are small enough to compute by hand. Where a number matters it is derived in the
test's own comment, not copied from a run, so a wrong implementation cannot make its own answer
the expectation.
"""

import random
from decimal import Decimal

import pytest

from openalpha_cn.domain.survivor_book import SecurityFacts, SurvivorBookConfig
from openalpha_cn.domain.survivor_screen import composite_of, screen


def fact(
    code: str,
    *,
    board: str = "main",
    listed: int = 500,
    special: bool = False,
    suspended: bool = False,
    close: str = "10",
    amount: str = "100",
    values: tuple[float | None, ...] = (1.0, 1.0, 1.0, 1.0),
) -> SecurityFacts:
    return SecurityFacts(
        ts_code=code,
        board=board,  # type: ignore[arg-type]
        listed_sessions=listed,
        special_treatment=special,
        suspended=suspended,
        close=Decimal(close),
        avg_amount=Decimal(amount),
        oriented_values=values,
    )


def isolated(**overrides: object) -> SurvivorBookConfig:
    """A configuration with the liquidity cut and the loser tail switched off."""
    settings: dict[str, object] = {
        "seed": 1,
        "liquidity_drop_fraction": Decimal("0"),
        "loser_decile_count": 0,
    }
    settings.update(overrides)
    return SurvivorBookConfig(**settings)  # type: ignore[arg-type]


def code(position: int) -> str:
    return f"{600000 + position:06d}.SH"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"board": "star"}, "board_not_allowed"),
        ({"board": "bse"}, "board_not_allowed"),
        ({"listed": 249}, "listing_too_recent"),
        ({"special": True}, "special_treatment"),
        ({"suspended": True}, "suspended"),
        ({"values": (1.0, None, 1.0, 1.0)}, "missing_factor_value"),
        ({"close": "348.26"}, "unaffordable_lot"),
    ],
)
def test_each_reason_is_reported_on_its_own(overrides: dict[str, object], reason: str) -> None:
    result = screen([fact(code(1), **overrides)], isolated())  # type: ignore[arg-type]

    assert result.verdicts[0].reasons == (reason,)
    assert not result.verdicts[0].eligible
    assert result.survivors == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"board": "growth"},
        {"listed": 250},
        {"close": "348.25"},
    ],
)
def test_the_boundary_value_of_each_reason_is_still_eligible(overrides: dict[str, object]) -> None:
    # 100 shares at 348.25 cost 34,825 -- exactly the sizing budget (350,000 / 10, less the 0.5%
    # reserve), which is affordable; one cent more is not (the case above).
    result = screen([fact(code(1), **overrides)], isolated())  # type: ignore[arg-type]

    assert result.verdicts[0].reasons == ()
    assert result.verdicts[0].eligible


def test_every_applicable_reason_is_listed_in_the_fixed_order() -> None:
    result = screen(
        [fact(code(1), board="star", listed=3, special=True, suspended=True)], isolated()
    )

    assert result.verdicts[0].reasons == (
        "board_not_allowed",
        "listing_too_recent",
        "special_treatment",
        "suspended",
    )


def test_the_lot_test_follows_the_equity_it_is_given() -> None:
    held = [fact(code(1), close="100")]

    assert screen(held, isolated()).verdicts[0].eligible
    # 100,000 / 10 less the reserve is 9,950, and 100 shares at 100 cost 10,000.
    poorer = screen(held, isolated(), equity=Decimal("100000"))
    assert poorer.verdicts[0].reasons == ("unaffordable_lot",)


def test_the_liquidity_cut_drops_the_thinnest_share_of_the_allowed_boards_only() -> None:
    main = [fact(code(position), amount=str(position)) for position in range(1, 11)]
    # Ten more names on a board the account cannot trade, thinner than every allowed name: if the
    # cut counted them it would be 6 of 20 and reach into the allowed names' middle.
    star = [fact(code(100 + position), board="star", amount="0") for position in range(10)]

    result = screen([*main, *star], isolated(liquidity_drop_fraction=Decimal("0.30")))

    illiquid = {v.ts_code for v in result.verdicts if "illiquid" in v.reasons}
    assert illiquid == {code(1), code(2), code(3)}


def test_equally_liquid_names_are_cut_by_code_so_the_cut_never_depends_on_input_order() -> None:
    names = [fact(code(position), amount="5") for position in range(10)]
    shuffled = list(names)
    random.Random(3).shuffle(shuffled)

    for facts in (names, shuffled):
        result = screen(facts, isolated(liquidity_drop_fraction=Decimal("0.30")))
        assert {v.ts_code for v in result.verdicts if "illiquid" in v.reasons} == {
            code(0),
            code(1),
            code(2),
        }


def test_the_composite_is_the_mean_of_the_oriented_values() -> None:
    assert composite_of((1.0, 2.0, 3.0, 6.0)) == 3.0
    assert composite_of((-1.0, 1.0)) == 0.0


def test_a_missing_value_makes_the_composite_missing_rather_than_a_smaller_mean() -> None:
    assert composite_of((1.0, None, 3.0)) is None
    assert composite_of(()) is None


def test_the_composite_is_summed_exactly() -> None:
    # `sum([0.1] * 10)` is 0.9999999999999999; the exactly rounded sum is 1.0.
    assert composite_of((0.1,) * 10) == 0.1


def survivors_of(composites: list[float], **overrides: object) -> tuple[list[str], list[int]]:
    facts = [fact(code(position), values=(c, c, c, c)) for position, c in enumerate(composites)]
    result = screen(facts, isolated(**overrides))
    return [name.ts_code for name in result.survivors], [name.rank for name in result.survivors]


def test_the_two_worst_deciles_are_dropped_and_the_rest_are_ranked_best_first() -> None:
    # Twenty names scored 1..20; two deciles of twenty is four, so scores 1-4 go and 5-20 stay.
    codes, ranks = survivors_of([float(c) for c in range(1, 21)], loser_decile_count=2)

    assert ranks == list(range(1, 17))
    assert codes == [code(position) for position in range(19, 3, -1)]


def test_the_loser_cut_rounds_down() -> None:
    # Fifteen names, two deciles: 15 * 2 // 10 = 3 dropped, 12 kept. Nine names: 1 dropped.
    assert len(survivors_of([float(c) for c in range(15)], loser_decile_count=2)[0]) == 12
    assert len(survivors_of([float(c) for c in range(9)], loser_decile_count=2)[0]) == 8


def test_tied_composites_are_cut_and_ranked_by_code() -> None:
    codes, ranks = survivors_of([1.0] * 10, loser_decile_count=2)

    # The two lowest codes are the ones dropped; the survivors are listed by ascending code.
    assert codes == [code(position) for position in range(2, 10)]
    assert ranks == list(range(1, 9))


def test_ineligible_names_neither_survive_nor_shift_the_cut() -> None:
    clean = [fact(code(position), values=(float(position),) * 4) for position in range(1, 21)]
    noisy = [
        *clean,
        fact(code(101), special=True, values=(99.0,) * 4),
        fact(code(102), suspended=True, values=(-99.0,) * 4),
    ]
    config = isolated(loser_decile_count=2)

    assert [n.ts_code for n in screen(noisy, config).survivors] == [
        n.ts_code for n in screen(clean, config).survivors
    ]


def test_a_survivor_carries_its_composite_and_its_close() -> None:
    result = screen([fact(code(1), close="12.34", values=(2.0, 4.0, 6.0, 8.0))], isolated())

    assert result.survivors[0].composite == 5.0
    assert result.survivors[0].close == Decimal("12.34")


def test_the_result_does_not_depend_on_the_order_the_facts_arrive_in() -> None:
    facts = [
        fact(code(position), amount=str(position % 7), values=(float(position % 11),) * 4)
        for position in range(60)
    ]
    reference = screen(facts, SurvivorBookConfig(seed=1))
    for shuffle_seed in range(5):
        shuffled = list(facts)
        random.Random(shuffle_seed).shuffle(shuffled)

        assert screen(shuffled, SurvivorBookConfig(seed=1)) == reference


def test_verdicts_are_listed_by_code() -> None:
    result = screen([fact(code(3)), fact(code(1)), fact(code(2))], isolated())

    assert [v.ts_code for v in result.verdicts] == [code(1), code(2), code(3)]


def test_a_security_listed_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="more than once"):
        screen([fact(code(1)), fact(code(1))], isolated())


def test_a_fact_with_the_wrong_number_of_factor_values_is_refused() -> None:
    with pytest.raises(ValueError, match="oriented values for 4 composite factors"):
        screen([fact(code(1), values=(1.0, 1.0))], isolated())


def test_an_empty_universe_screens_to_nothing() -> None:
    result = screen([], isolated())

    assert result.verdicts == ()
    assert result.survivors == ()
```

- [ ] **Step 2：运行，确认因模块不存在而失败**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_screen.py -q -p no:cacheprovider
```
预期：`ModuleNotFoundError: No module named 'openalpha_cn.domain.survivor_screen'`。

- [ ] **Step 3：写实现** — 创建 `src/openalpha_cn/domain/survivor_screen.py`：

```python
"""The eligibility-and-survivor screen (`V2-P7-005`): facts in, verdicts and survivors out.

Pure functions over `SecurityFacts`. Three steps, in this order, and the order matters because
each one's population is the previous one's output:

1. **Static reasons** -- board, listing age, special treatment, suspension, a missing factor value
   and a lot the per-name budget cannot afford. Each is a fact about one security.
2. **Liquidity** -- the bottom `liquidity_drop_fraction` by trailing traded amount *within the
   allowed boards*. It is the one reason that depends on the other securities, which is why it is
   computed after the static ones and over the board-allowed population only.
3. **Survivors** -- of the eligible securities, drop the worst `loser_decile_count` tenths by
   composite. Ties break on `ts_code`, so the cut never depends on input order.
"""

import math
from collections.abc import Sequence
from decimal import Decimal

from openalpha_cn.domain.survivor_book import (
    ELIGIBILITY_REASON_ORDER,
    EligibilityReason,
    EligibilityVerdict,
    RankedName,
    ScreenResult,
    SecurityFacts,
    SurvivorBookConfig,
    floor_share,
)


def composite_of(values: Sequence[float | None]) -> float | None:
    """The mean of the oriented factor values, or `None` if any of them is missing.

    `math.fsum` rather than `sum`: it is exactly rounded, so the result does not depend on the
    platform's float summation and a pinned expectation means the same thing everywhere.
    """
    if not values or any(value is None for value in values):
        return None
    present = [value for value in values if value is not None]
    return math.fsum(present) / len(present)


def screen(
    facts: Sequence[SecurityFacts],
    config: SurvivorBookConfig,
    *,
    equity: Decimal | None = None,
) -> ScreenResult:
    """Assess every `facts` security; `equity` sizes the lot test (default: `config.capital`)."""
    codes = [fact.ts_code for fact in facts]
    if len(set(codes)) != len(codes):
        raise ValueError("a security appears more than once in the facts of one signal day")
    width = len(config.composite_factors)
    for fact in facts:
        if len(fact.oriented_values) != width:
            raise ValueError(
                f"{fact.ts_code}: {len(fact.oriented_values)} oriented values for {width} "
                "composite factors"
            )

    budget = config.sizing_budget(equity)
    reasons: dict[str, set[EligibilityReason]] = {fact.ts_code: set() for fact in facts}
    composites: dict[str, float | None] = {}
    for fact in facts:
        found = reasons[fact.ts_code]
        composites[fact.ts_code] = composite_of(fact.oriented_values)
        if fact.board not in config.boards:
            found.add("board_not_allowed")
        if fact.listed_sessions < config.min_listing_sessions:
            found.add("listing_too_recent")
        if fact.special_treatment:
            found.add("special_treatment")
        if fact.suspended:
            found.add("suspended")
        if composites[fact.ts_code] is None:
            found.add("missing_factor_value")
        if fact.close * config.lot_size > budget:
            found.add("unaffordable_lot")

    allowed = sorted(
        (fact for fact in facts if fact.board in config.boards),
        key=lambda fact: (fact.avg_amount, fact.ts_code),
    )
    for fact in allowed[: floor_share(len(allowed), config.liquidity_drop_fraction)]:
        reasons[fact.ts_code].add("illiquid")

    verdicts = tuple(
        EligibilityVerdict(
            ts_code=code,
            reasons=tuple(r for r in ELIGIBILITY_REASON_ORDER if r in reasons[code]),
            composite=composites[code],
        )
        for code in sorted(codes)
    )

    close = {fact.ts_code: fact.close for fact in facts}
    scored = [(v.ts_code, v.composite) for v in verdicts if v.eligible and v.composite is not None]
    worst_first = sorted(scored, key=lambda pair: (pair[1], pair[0]))
    dropped = len(worst_first) * config.loser_decile_count // 10
    best_first = sorted(worst_first[dropped:], key=lambda pair: (-pair[1], pair[0]))
    survivors = tuple(
        RankedName(ts_code=code, composite=composite, rank=position, close=close[code])
        for position, (code, composite) in enumerate(best_first, start=1)
    )
    return ScreenResult(verdicts=verdicts, survivors=survivors)
```

- [ ] **Step 4：运行，确认通过，再跑静态检查**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_screen.py -q -p no:cacheprovider
"$PY/ruff" check src/openalpha_cn/domain/survivor_screen.py tests/unit/domain/test_survivor_screen.py
"$PY/ruff" format --check src/openalpha_cn/domain/survivor_screen.py tests/unit/domain/test_survivor_screen.py
"$PY/mypy" src/openalpha_cn/domain/survivor_screen.py
```
预期：`27 passed`；其余无问题。

- [ ] **Step 5：提交**

```bash
git add src/openalpha_cn/domain/survivor_screen.py tests/unit/domain/test_survivor_screen.py
git commit -m "feat(V2-P7-005): the eligibility-and-survivor screen

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 4：粘性持仓策略 `survivor_policy.py`

**Files:**
- Create: `src/openalpha_cn/domain/survivor_policy.py`
- Create: `tests/unit/domain/test_survivor_policy.py`

**Interfaces:**
- Consumes: Task 1 的 `ScreenResult`、`SurvivorBookConfig`、`floor_share`、`lot_quantity`；Task 2 的 `selection_rule`、（测试里）`SeededHashRule`。
- Produces：`Holding(ts_code, quantity)`、`Cooldown(ts_code, since_session)`、`BookState(session, initialized, equity, holdings, pending_buys, cooldowns)`、`ExitDecision`、`EntryDecision`、`BookDecision(session, exits, entries, deferred_exits, near_exit, watch_list, unaffordable, target_holdings)`、`decide_book(*, config, screen, state) -> BookDecision`。

- [ ] **Step 1：写失败的测试** — 创建 `tests/unit/domain/test_survivor_policy.py`：

```python
"""The sticky holding policy (`V2-P7-005`).

The policy is exercised on screens written by hand rather than produced by `screen()`, so each
test names exactly the situation it is about: who the survivors are and in what order, who was
dropped, who is ineligible. Which entrant the selection rule draws is the rule's business and is
pinned in `test_survivor_selection.py`; here the expectation is "the first k of the rule's own
order over the entrants", so the two files cannot drift apart silently.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.survivor_book import (
    EligibilityVerdict,
    RankedName,
    ScreenResult,
    SurvivorBookConfig,
)
from openalpha_cn.domain.survivor_policy import BookState, Cooldown, Holding, decide_book
from openalpha_cn.domain.survivor_selection import SeededHashRule

SESSION = 100


def config(**overrides: object) -> SurvivorBookConfig:
    settings: dict[str, object] = {
        "seed": 1,
        "holding_count": 4,
        "capital": Decimal("40000"),
        "entry_floor_fraction": Decimal("0"),
        "near_exit_fraction": Decimal("0.25"),
        "cooldown_sessions": 5,
        "max_replacements_per_session": 2,
    }
    settings.update(overrides)
    return SurvivorBookConfig(**settings)  # type: ignore[arg-type]


def name(position: int) -> str:
    return f"{600000 + position:06d}.SH"


def screen_of(
    ranked: list[int],
    *,
    dropped: tuple[tuple[int, float], ...] = (),
    ineligible: tuple[int, ...] = (),
    close: dict[int, str] | None = None,
) -> ScreenResult:
    """Survivors best-first (`ranked` lists name positions), then dropped losers and ineligibles."""
    prices = close or {}
    total = len(ranked)
    survivors = tuple(
        RankedName(
            ts_code=name(position),
            composite=float(total - place),
            rank=place + 1,
            close=Decimal(prices.get(position, "10")),
        )
        for place, position in enumerate(ranked)
    )
    verdicts = [
        EligibilityVerdict(ts_code=survivor.ts_code, composite=survivor.composite)
        for survivor in survivors
    ]
    verdicts += [EligibilityVerdict(ts_code=name(p), composite=c) for p, c in dropped]
    verdicts += [EligibilityVerdict(ts_code=name(p), reasons=("suspended",)) for p in ineligible]
    return ScreenResult(
        verdicts=tuple(sorted(verdicts, key=lambda v: v.ts_code)), survivors=survivors
    )


def state(
    *,
    held: tuple[int, ...] = (),
    initialized: bool = True,
    pending: tuple[int, ...] = (),
    cooling: tuple[tuple[int, int], ...] = (),
    equity: str = "40000",
) -> BookState:
    return BookState(
        session=SESSION,
        initialized=initialized,
        equity=Decimal(equity),
        holdings=tuple(Holding(ts_code=name(p), quantity=100) for p in sorted(held)),
        pending_buys=tuple(name(p) for p in pending),
        cooldowns=tuple(Cooldown(ts_code=name(p), since_session=s) for p, s in cooling),
    )


def rule_order(positions: list[int], cfg: SurvivorBookConfig) -> list[str]:
    return list(SeededHashRule().order([name(p) for p in positions], seed=cfg.seed))


def test_the_first_build_buys_a_full_book_at_once_in_the_rules_order() -> None:
    cfg = config()
    decision = decide_book(
        config=cfg,
        screen=screen_of(list(range(8))),
        state=state(initialized=False),
    )

    expected = rule_order(list(range(8)), cfg)
    assert [e.ts_code for e in decision.entries] == expected[:4]
    assert {e.reason for e in decision.entries} == {"INITIAL_BUILD"}
    assert decision.exits == ()
    assert decision.watch_list == tuple(expected[4:8])
    assert decision.target_holdings == tuple(sorted(expected[:4]))


def test_a_book_whose_holdings_are_all_survivors_does_nothing() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of(list(range(8))),
        state=state(held=(0, 1, 2, 3)),
    )

    assert decision.exits == ()
    assert decision.entries == ()
    assert decision.deferred_exits == ()
    assert decision.target_holdings == tuple(name(p) for p in range(4))


def test_a_holding_that_drops_out_of_the_survivors_is_sold_and_replaced() -> None:
    cfg = config()
    decision = decide_book(
        config=cfg,
        screen=screen_of([0, 1, 2, 4, 5, 6], dropped=((3, -9.0),)),
        state=state(held=(0, 1, 2, 3)),
    )

    assert [(e.ts_code, e.reason) for e in decision.exits] == [(name(3), "EXIT_SURVIVOR_LOST")]
    entrants = rule_order([4, 5, 6], cfg)
    assert [(e.ts_code, e.reason) for e in decision.entries] == [(entrants[0], "ENTRY_REPLACEMENT")]
    assert name(3) not in decision.target_holdings
    assert entrants[0] in decision.target_holdings


def test_a_holding_that_is_no_longer_eligible_is_sold_even_with_the_best_composite() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([0, 1, 2, 4], ineligible=(3,)),
        state=state(held=(0, 1, 2, 3)),
    )

    assert [(e.ts_code, e.reason) for e in decision.exits] == [(name(3), "EXIT_ELIGIBILITY_LOST")]


def test_a_holding_the_screen_never_assessed_is_sold_as_ineligible() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([0, 1, 2, 4]),
        state=state(held=(0, 1, 2, 9)),
    )

    assert [(e.ts_code, e.reason) for e in decision.exits] == [(name(9), "EXIT_ELIGIBILITY_LOST")]


def test_forced_exits_are_never_deferred_but_the_refill_is_still_rate_limited() -> None:
    cfg = config()
    decision = decide_book(
        config=cfg,
        screen=screen_of([3, 4, 5, 6, 7], ineligible=(0, 1, 2)),
        state=state(held=(0, 1, 2, 3)),
    )

    assert [e.ts_code for e in decision.exits] == [name(0), name(1), name(2)]
    assert {e.reason for e in decision.exits} == {"EXIT_ELIGIBILITY_LOST"}
    # Three vacancies, but at most two replacements a session: the third waits for tomorrow.
    assert len(decision.entries) == 2
    assert decision.target_holdings == tuple(
        sorted([name(3), *[e.ts_code for e in decision.entries]])
    )


def test_discretionary_exits_are_capped_and_the_worst_composite_goes_first() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([3, 4, 5, 6], dropped=((0, -1.0), (1, -3.0), (2, -2.0))),
        state=state(held=(0, 1, 2, 3)),
    )

    # Three holdings fell out of the survivors; two may go today, worst first (-3.0, then -2.0).
    assert [e.ts_code for e in decision.exits] == [name(1), name(2)]
    assert decision.deferred_exits == (name(0),)
    assert name(0) in decision.target_holdings


def test_a_name_still_cooling_down_is_not_bought_but_one_whose_cooldown_ran_out_is() -> None:
    survivors = screen_of([0, 1, 2, 3])
    held = state(held=(0, 1, 2), cooling=((3, SESSION - 4),))
    assert decide_book(config=config(), screen=survivors, state=held).entries == ()

    expired = state(held=(0, 1, 2), cooling=((3, SESSION - 5),))
    decision = decide_book(config=config(), screen=survivors, state=expired)
    assert [e.ts_code for e in decision.entries] == [name(3)]


def position_of(ts_code: str) -> int:
    return int(ts_code[:6]) - 600000


def test_a_live_buy_order_fills_a_slot_and_its_name_is_not_drawn_again() -> None:
    cfg = config()
    everyone = screen_of(list(range(8)))
    decision = decide_book(config=cfg, screen=everyone, state=state(held=(0, 1), pending=(2, 3)))

    assert decision.entries == ()

    # The name the rule would draw first is the one already on order: it must not be drawn twice.
    first = position_of(rule_order(list(range(8)), cfg)[0])
    others = tuple(p for p in range(8) if p != first)[:2]
    decision = decide_book(config=cfg, screen=everyone, state=state(held=others, pending=(first,)))

    drawn = [e.ts_code for e in decision.entries]
    assert len(drawn) == 1
    assert drawn[0] not in {name(first), *[name(p) for p in others]}


def test_an_entrant_the_current_equity_cannot_buy_a_lot_of_is_skipped_and_reported() -> None:
    cfg = config()
    # Equity 40,000 over four names, less the reserve, is 9,950 a name: a 5,000 share price buys
    # none; a 10 share price buys 900.
    decision = decide_book(
        config=cfg,
        screen=screen_of([0, 1, 2, 3], close={3: "5000"}),
        state=state(held=(0, 1, 2)),
    )

    assert decision.entries == ()
    assert decision.unaffordable == (name(3),)
    assert decision.watch_list == ()


def test_an_unaffordable_name_is_skipped_in_favour_of_the_next_draw() -> None:
    cfg = config()
    prices = {4: "5000"}
    screen = screen_of([0, 1, 2, 4, 5], close=prices)
    decision = decide_book(config=cfg, screen=screen, state=state(held=(0, 1, 2)))

    assert [e.ts_code for e in decision.entries] == [name(5)]
    assert decision.unaffordable == (name(4),)


def test_the_entry_floor_keeps_the_worst_survivors_out_of_the_draw() -> None:
    # Eight survivors and a floor of a quarter: only the best six are entrants.
    for seed in range(25):
        cfg = config(seed=seed, entry_floor_fraction=Decimal("0.25"))
        decision = decide_book(
            config=cfg, screen=screen_of(list(range(8))), state=state(initialized=False)
        )

        assert {e.ts_code for e in decision.entries} <= {name(p) for p in range(6)}


def test_a_book_with_too_few_entrants_is_built_part_way() -> None:
    decision = decide_book(
        config=config(), screen=screen_of([0, 1]), state=state(initialized=False)
    )

    assert len(decision.entries) == 2
    assert decision.watch_list == ()


def test_holdings_in_the_worst_share_of_the_survivors_are_reported_as_near_exit() -> None:
    # Eight survivors and a quarter: the worst two (ranks 7 and 8) are the near-exit band; the
    # holding at rank 6 is the last one outside it.
    decision = decide_book(
        config=config(),
        screen=screen_of(list(range(8))),
        state=state(held=(0, 5, 6, 7)),
    )

    assert decision.near_exit == (name(6), name(7))


def test_a_name_leaving_today_is_not_also_reported_as_near_exit() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([0, 1, 2, 3, 4, 5, 6, 7], ineligible=(8,)),
        state=state(held=(0, 1, 7, 8)),
    )

    assert name(8) not in decision.near_exit
    assert decision.near_exit == (name(7),)


def test_the_decision_is_the_same_however_the_holdings_are_listed() -> None:
    screen = screen_of([0, 1, 2, 4, 5, 6], dropped=((3, -9.0),))
    forward = state(held=(0, 1, 2, 3))
    backward = forward.model_copy(update={"holdings": tuple(reversed(forward.holdings))})

    assert decide_book(config=config(), screen=screen, state=forward) == decide_book(
        config=config(), screen=screen, state=backward
    )


def test_a_book_holding_more_than_it_may_is_refused() -> None:
    with pytest.raises(ValueError, match="exceed holding_count 4"):
        decide_book(
            config=config(),
            screen=screen_of(list(range(8))),
            state=state(held=(0, 1, 2), pending=(3, 4)),
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"holdings": (Holding(ts_code="a", quantity=100), Holding(ts_code="a", quantity=100))},
        {"pending_buys": ("a", "a")},
        {"holdings": (Holding(ts_code="a", quantity=100),), "pending_buys": ("a",)},
    ],
)
def test_a_book_state_that_contradicts_itself_is_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BookState(session=1, initialized=True, equity=Decimal("1"), **bad)  # type: ignore[arg-type]


def test_an_empty_screen_sells_everything_it_cannot_see_and_buys_nothing() -> None:
    empty = ScreenResult(verdicts=(), survivors=())

    decision = decide_book(config=config(), screen=empty, state=state(held=(0, 1)))

    assert [(e.ts_code, e.reason) for e in decision.exits] == [
        (name(0), "EXIT_ELIGIBILITY_LOST"),
        (name(1), "EXIT_ELIGIBILITY_LOST"),
    ]
    assert decision.entries == ()
    assert decision.target_holdings == ()


def test_an_empty_screen_on_the_first_day_builds_nothing() -> None:
    empty = ScreenResult(verdicts=(), survivors=())

    decision = decide_book(config=config(), screen=empty, state=state(initialized=False))

    assert decision.entries == ()
    assert decision.exits == ()
```

- [ ] **Step 2：运行，确认因模块不存在而失败**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_policy.py -q -p no:cacheprovider
```
预期：`ModuleNotFoundError: No module named 'openalpha_cn.domain.survivor_policy'`。

- [ ] **Step 3：写实现** — 创建 `src/openalpha_cn/domain/survivor_policy.py`：

```python
"""The sticky holding policy (`V2-P7-005`): who is sold, who is bought, and nothing else.

A holding is sold only when it **stops being eligible** (forced, never deferred) or **drops out of
the survivors** (discretionary, at most `max_replacements_per_session` a day, the worst composite
first; the rest wait for tomorrow). A vacancy is filled from the **entrants** -- survivors above
the entry floor that are not already held, not already on order and not in cool-down -- in the
order the selection rule gives, skipping any name a lot of which the current equity cannot buy.
The first build fills every vacancy at once; after that at most `max_replacements_per_session`
buys a day.

This is the half of the design the evidence rests on: staying put is what keeps the loser tail out
and the cost low (`docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md` section 3).
The function is pure -- state and screen in, decision out -- so the whole policy is testable with
hand-written fixtures and no panel.
"""

from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.domain.survivor_book import (
    ScreenResult,
    SurvivorBookConfig,
    floor_share,
    lot_quantity,
)
from openalpha_cn.domain.survivor_selection import selection_rule

ExitReason = Literal["EXIT_ELIGIBILITY_LOST", "EXIT_SURVIVOR_LOST"]
EntryReason = Literal["INITIAL_BUILD", "ENTRY_REPLACEMENT"]


class Holding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class Cooldown(BaseModel):
    """A security that may not be bought again until `cooldown_sessions` have passed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str = Field(min_length=1)
    since_session: int = Field(ge=0)


class BookState(BaseModel):
    """Everything the policy needs to know about the book as of the signal session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session: int = Field(ge=0)
    """Ordinal of the signal session in the trading calendar; the adapter maps dates to it."""
    initialized: bool
    """False until the first build has been decided; the first build is not rate-limited."""
    equity: Decimal = Field(gt=0)
    holdings: tuple[Holding, ...] = ()
    pending_buys: tuple[str, ...] = ()
    """Securities with a live buy order. They fill a slot and cannot be chosen again."""
    cooldowns: tuple[Cooldown, ...] = ()

    @model_validator(mode="after")
    def _no_repeats_and_no_overlap(self) -> Self:
        held = [holding.ts_code for holding in self.holdings]
        if len(set(held)) != len(held):
            raise ValueError("a security is held more than once")
        if len(set(self.pending_buys)) != len(self.pending_buys):
            raise ValueError("a security has more than one pending buy")
        if set(held) & set(self.pending_buys):
            raise ValueError("a security is both held and on a pending buy")
        return self


class ExitDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str
    reason: ExitReason


class EntryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str
    reason: EntryReason


class BookDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session: int
    exits: tuple[ExitDecision, ...]
    """Forced exits first (by code), then discretionary ones (worst composite first)."""
    entries: tuple[EntryDecision, ...]
    """In the order the selection rule drew them."""
    deferred_exits: tuple[str, ...]
    """Survivor-lost holdings kept today because the replacement cap was reached."""
    near_exit: tuple[str, ...]
    """Holdings still in the survivors but in its worst `near_exit_fraction`."""
    watch_list: tuple[str, ...]
    """The next names the rule would draw, after today's entries."""
    unaffordable: tuple[str, ...]
    """Entrants skipped because current equity cannot buy a lot of them."""
    target_holdings: tuple[str, ...]
    """Holdings after today's exits and entries, sorted; pending buys are not included."""


def decide_book(
    *, config: SurvivorBookConfig, screen: ScreenResult, state: BookState
) -> BookDecision:
    held = sorted(holding.ts_code for holding in state.holdings)
    if len(held) + len(state.pending_buys) > config.holding_count:
        raise ValueError(
            f"{len(held)} holdings and {len(state.pending_buys)} pending buys exceed "
            f"holding_count {config.holding_count}"
        )
    verdicts = {verdict.ts_code: verdict for verdict in screen.verdicts}
    survivors = {name.ts_code: name for name in screen.survivors}
    cap = config.max_replacements_per_session

    def composite_of(code: str) -> float:
        value = verdicts[code].composite
        return 0.0 if value is None else value

    forced = [code for code in held if code not in verdicts or not verdicts[code].eligible]
    lost = sorted(
        (
            code
            for code in held
            if code in verdicts and verdicts[code].eligible and code not in survivors
        ),
        key=lambda code: (composite_of(code), code),
    )
    exits = tuple(
        ExitDecision(ts_code=code, reason="EXIT_ELIGIBILITY_LOST") for code in forced
    ) + tuple(ExitDecision(ts_code=code, reason="EXIT_SURVIVOR_LOST") for code in lost[:cap])
    deferred = tuple(lost[cap:])
    leaving = {decision.ts_code for decision in exits}

    occupied = len(held) - len(exits) + len(state.pending_buys)
    vacancies = max(config.holding_count - occupied, 0)
    allowance = vacancies if not state.initialized else min(vacancies, cap)

    cooling = {
        cooldown.ts_code
        for cooldown in state.cooldowns
        if state.session - cooldown.since_session < config.cooldown_sessions
    }
    blocked = set(held) | set(state.pending_buys) | cooling
    ranked = screen.survivors
    entrant_count = len(ranked) - floor_share(len(ranked), config.entry_floor_fraction)
    entrants = [name for name in ranked[:entrant_count] if name.ts_code not in blocked]

    unit_budget = config.sizing_budget(state.equity)
    affordable: list[str] = []
    unaffordable: list[str] = []
    drawn = selection_rule(config.selection_rule).order(
        [name.ts_code for name in entrants], seed=config.seed
    )
    for code in drawn:
        if lot_quantity(unit_budget, survivors[code].close, config.lot_size) == 0:
            unaffordable.append(code)
        else:
            affordable.append(code)

    reason: EntryReason = "ENTRY_REPLACEMENT" if state.initialized else "INITIAL_BUILD"
    entries = tuple(EntryDecision(ts_code=code, reason=reason) for code in affordable[:allowance])
    watch_list = tuple(affordable[allowance : allowance + config.holding_count])

    near_cutoff = len(ranked) - floor_share(len(ranked), config.near_exit_fraction)
    near_exit = tuple(
        code
        for code in held
        if code in survivors and code not in leaving and survivors[code].rank > near_cutoff
    )
    target = sorted((set(held) - leaving) | {entry.ts_code for entry in entries})
    return BookDecision(
        session=state.session,
        exits=exits,
        entries=entries,
        deferred_exits=deferred,
        near_exit=near_exit,
        watch_list=watch_list,
        unaffordable=tuple(unaffordable),
        target_holdings=tuple(target),
    )
```

- [ ] **Step 4：运行，确认通过，再跑静态检查**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_policy.py -q -p no:cacheprovider
"$PY/ruff" check src/openalpha_cn/domain/survivor_policy.py tests/unit/domain/test_survivor_policy.py
"$PY/ruff" format --check src/openalpha_cn/domain/survivor_policy.py tests/unit/domain/test_survivor_policy.py
"$PY/mypy" src/openalpha_cn/domain/survivor_policy.py
```
预期：`22 passed`；其余无问题。

- [ ] **Step 5：提交**

```bash
git add src/openalpha_cn/domain/survivor_policy.py tests/unit/domain/test_survivor_policy.py
git commit -m "feat(V2-P7-005): the sticky holding policy

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 5：订单与交易清单 `survivor_orders.py`

**Files:**
- Create: `src/openalpha_cn/domain/survivor_orders.py`
- Create: `tests/unit/domain/test_survivor_orders.py`
- Modify: `tests/unit/domain/test_manifest_component_provenance.py`（前缀普查）
- Modify: `src/openalpha_cn/backtest/candidate_ranking.py`（一句被计数的话）

**Interfaces:**
- Consumes: Task 1 的 `SurvivorBookConfig`、`lot_quantity`；Task 4 的 `BookDecision`、`BookState`、`EntryDecision`、`ExitDecision`、`Holding`。
- Produces：`Side`、`OrderReason`；`TradeOrder`（`.order_id`，前缀 `tor`）；`TradeList`（`.trade_list_id`，前缀 `trl`；`orders`、`carried`）；`build_trade_list(*, config, state, decision, reference_prices: Mapping[str, Decimal], cash: Decimal, carried: tuple[TradeOrder, ...] = ()) -> TradeList`。

- [ ] **Step 1：写失败的测试** — 创建 `tests/unit/domain/test_survivor_orders.py`：

```python
"""Orders and the daily trade list (`V2-P7-005`, `V2-P7-008`).

Quantities are worked out in the comments from the sizing rule -- an equal share of equity less
the 0.5% cost reserve, in whole lots of 100, further limited by the cash that will be there -- so
the expectation is arithmetic, not a transcription of what the code returned.
"""

import re
from decimal import Decimal

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_orders import TradeList, TradeOrder, build_trade_list
from openalpha_cn.domain.survivor_policy import (
    BookDecision,
    BookState,
    EntryDecision,
    ExitDecision,
    Holding,
)

SESSION = 100


def config(**overrides: object) -> SurvivorBookConfig:
    settings: dict[str, object] = {
        "seed": 1,
        "holding_count": 4,
        "capital": Decimal("40000"),
        "buy_order_expiry_sessions": 3,
    }
    settings.update(overrides)
    return SurvivorBookConfig(**settings)  # type: ignore[arg-type]


def book(*holdings: tuple[str, int], equity: str = "40000") -> BookState:
    return BookState(
        session=SESSION,
        initialized=True,
        equity=Decimal(equity),
        holdings=tuple(Holding(ts_code=code, quantity=quantity) for code, quantity in holdings),
    )


def decision(
    *,
    exits: tuple[tuple[str, str], ...] = (),
    entries: tuple[tuple[str, str], ...] = (),
) -> BookDecision:
    return BookDecision(
        session=SESSION,
        exits=tuple(ExitDecision(ts_code=c, reason=r) for c, r in exits),  # type: ignore[arg-type]
        entries=tuple(EntryDecision(ts_code=c, reason=r) for c, r in entries),  # type: ignore[arg-type]
        deferred_exits=(),
        near_exit=(),
        watch_list=(),
        unaffordable=(),
        target_holdings=(),
    )


PRICES = {"A": Decimal("10"), "B": Decimal("12.5"), "C": Decimal("40")}


def test_a_sell_is_the_whole_holding_and_a_buy_is_whole_lots_of_an_equal_share() -> None:
    # Share per name: 40,000 / 4 = 10,000, less the 0.5% reserve = 9,950. At 12.5 that is 796
    # shares, which is 700 in whole lots. Cash 20,000 plus 3,000 from selling A is plenty.
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("20000"),
    )

    sell, buy = plan.orders
    assert (sell.side, sell.ts_code, sell.quantity, sell.reason) == (
        "sell",
        "A",
        300,
        "EXIT_SURVIVOR_LOST",
    )
    assert (buy.side, buy.ts_code, buy.quantity, buy.reason) == (
        "buy",
        "B",
        700,
        "ENTRY_REPLACEMENT",
    )
    assert sell.reference_price == Decimal("10")
    assert buy.reference_price == Decimal("12.5")


def test_orders_execute_the_next_session_and_only_buys_expire() -> None:
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_ELIGIBILITY_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("20000"),
    )
    sell, buy = plan.orders

    assert (sell.signal_session, sell.execute_session, sell.expires_session) == (100, 101, None)
    assert (buy.signal_session, buy.execute_session, buy.expires_session) == (100, 101, 103)


def test_sells_come_first_by_code_and_buys_follow_in_the_decisions_order() -> None:
    plan = build_trade_list(
        config=config(),
        state=book(("C", 100), ("A", 100)),
        decision=decision(
            exits=(("C", "EXIT_SURVIVOR_LOST"), ("A", "EXIT_SURVIVOR_LOST")),
            entries=(("B", "ENTRY_REPLACEMENT"), ("C", "ENTRY_REPLACEMENT")),
        ),
        reference_prices=PRICES,
        cash=Decimal("20000"),
    )

    assert [(o.side, o.ts_code) for o in plan.orders] == [
        ("sell", "A"),
        ("sell", "C"),
        ("buy", "B"),
        ("buy", "C"),
    ]


def test_a_buy_is_limited_by_the_cash_that_will_be_there() -> None:
    # Cash 3,000 and nothing sold: at 12.5 that is 240 shares, 200 in whole lots, which is less
    # than the 700 an equal share would buy.
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("B", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("3000"),
    )

    assert [o.quantity for o in plan.orders] == [200]


def test_the_proceeds_of_a_sell_count_towards_what_a_buy_may_spend() -> None:
    # Cash 0; A sells 300 at 10 for 3,000, enough for 200 shares of B at 12.5.
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("0"),
    )

    assert [(o.side, o.quantity) for o in plan.orders] == [("sell", 300), ("buy", 200)]


def test_each_buy_spends_from_what_the_one_before_left() -> None:
    # An equal share would buy 700 shares of B, but 5,000 of cash buys at most 5,000 / 12.5 = 400:
    # B takes all of it, nothing is left for C at 40 (a lot costs 4,000), and C is not placed.
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("B", "ENTRY_REPLACEMENT"), ("C", "ENTRY_REPLACEMENT"))),
        reference_prices=PRICES,
        cash=Decimal("5000"),
    )

    assert [(o.ts_code, o.quantity) for o in plan.orders] == [("B", 400)]


def test_a_buy_that_cannot_afford_one_lot_is_not_placed() -> None:
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("C", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("3999"),
    )

    assert plan.orders == ()


def test_the_first_build_is_sized_to_the_equity_not_to_the_cash_left_over() -> None:
    plan = build_trade_list(
        config=config(),
        state=BookState(session=SESSION, initialized=False, equity=Decimal("40000")),
        decision=decision(
            entries=(("A", "INITIAL_BUILD"), ("B", "INITIAL_BUILD"), ("C", "INITIAL_BUILD"))
        ),
        reference_prices=PRICES,
        cash=Decimal("40000"),
    )

    # 9,950 buys 995 shares of A (900 in lots), 796 of B (700) and 248 of C (200).
    assert [(o.ts_code, o.quantity, o.reason) for o in plan.orders] == [
        ("A", 900, "INITIAL_BUILD"),
        ("B", 700, "INITIAL_BUILD"),
        ("C", 200, "INITIAL_BUILD"),
    ]


def test_every_quantity_is_a_whole_number_of_lots() -> None:
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("20000"),
    )

    assert all(order.quantity % 100 == 0 for order in plan.orders)


def test_a_missing_reference_price_is_a_named_failure() -> None:
    with pytest.raises(ValueError, match="no reference price for Z"):
        build_trade_list(
            config=config(),
            state=book(),
            decision=decision(entries=(("Z", "ENTRY_REPLACEMENT"),)),
            reference_prices=PRICES,
            cash=Decimal("20000"),
        )


def test_negative_cash_is_refused() -> None:
    with pytest.raises(ValueError, match="cash must not be negative"):
        build_trade_list(
            config=config(),
            state=book(),
            decision=decision(),
            reference_prices=PRICES,
            cash=Decimal("-1"),
        )


def planned(cash: str = "20000") -> TradeList:
    return build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal(cash),
    )


def test_the_same_inputs_give_the_same_orders_and_the_same_identities() -> None:
    first, second = planned(), planned()

    assert first == second
    assert first.trade_list_id == second.trade_list_id
    assert [o.order_id for o in first.orders] == [o.order_id for o in second.orders]


def test_the_identities_are_content_addresses_that_move_with_the_content() -> None:
    first = planned()
    assert re.fullmatch(r"trl_[0-9a-f]{24}", first.trade_list_id)
    assert all(re.fullmatch(r"tor_[0-9a-f]{24}", o.order_id) for o in first.orders)

    smaller = planned(cash="3000")  # the same decision, a different buy quantity
    assert smaller.trade_list_id != first.trade_list_id
    assert smaller.orders[0].order_id == first.orders[0].order_id  # the sell did not change
    assert smaller.orders[1].order_id != first.orders[1].order_id


def test_a_carried_order_is_listed_without_being_planned_again() -> None:
    earlier = planned().orders[1]
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(),
        reference_prices=PRICES,
        cash=Decimal("0"),
        carried=(earlier,),
    )

    assert plan.orders == ()
    assert plan.carried == (earlier,)


def test_the_stored_form_reads_back_without_the_computed_identities() -> None:
    held = planned()
    stored = held.model_dump_json(exclude_computed_fields=True)

    assert TradeList.model_validate_json(stored) == held
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TradeList.model_validate_json(held.model_dump_json())


@pytest.mark.parametrize(
    "overrides",
    [{"quantity": 0}, {"reference_price": Decimal("0")}, {"execute_session": 0}],
)
def test_an_order_with_a_nonsense_field_is_refused(overrides: dict[str, object]) -> None:
    base: dict[str, object] = {
        "signal_session": 1,
        "execute_session": 2,
        "side": "buy",
        "ts_code": "A",
        "quantity": 100,
        "reference_price": Decimal("10"),
        "reason": "INITIAL_BUILD",
    }
    base.update(overrides)
    with pytest.raises(ValidationError):
        TradeOrder(**base)  # type: ignore[arg-type]
```

- [ ] **Step 2：运行，确认因模块不存在而失败**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_orders.py -q -p no:cacheprovider
```
预期：`ModuleNotFoundError: No module named 'openalpha_cn.domain.survivor_orders'`。

- [ ] **Step 3：写实现** — 创建 `src/openalpha_cn/domain/survivor_orders.py`：

```python
"""Orders and the daily trade list (`V2-P7-005`, `V2-P7-008`): a decision becomes whole lots.

The trade list is **the product the user reads each evening**: what to sell, what to buy, how many
shares, at what reference price, and why. It is a pure function of the decision, the state and the
reference prices, and its identity is a content address, so two runs on the same inputs are
byte-identical and a stored list can be compared to a recomputed one.

Sizing: a buy is `lot_quantity` of the equal share (less the cost reserve) at the signal-day
reference price, and is further limited by the cash that will be there -- today's cash plus the
estimated proceeds of today's sells -- so a book that is nearly fully invested cannot over-order.
A buy that cannot be afforded even one lot with what is left is **not placed**; the name stays on
the watch list and is drawn again tomorrow.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.survivor_book import SurvivorBookConfig, lot_quantity
from openalpha_cn.domain.survivor_policy import BookDecision, BookState

Side = Literal["buy", "sell"]
OrderReason = Literal[
    "INITIAL_BUILD",
    "ENTRY_REPLACEMENT",
    "EXIT_SURVIVOR_LOST",
    "EXIT_ELIGIBILITY_LOST",
]


class TradeOrder(BaseModel):
    """One order, executed at the open of `execute_session`; immutable once planned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trade-order/v1"] = "trade-order/v1"
    signal_session: int = Field(ge=0)
    execute_session: int = Field(ge=1)
    expires_session: int | None = None
    """The last session a buy may still fill; `None` for a sell, which never expires."""
    side: Side
    ts_code: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    reference_price: Decimal = Field(gt=0)
    reason: OrderReason

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def order_id(self) -> str:
        return stable_model_id(prefix="tor", model=self)


class TradeList(BaseModel):
    """The orders planned from one signal session, sells before buys, each by `ts_code`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trade-list/v1"] = "trade-list/v1"
    signal_session: int = Field(ge=0)
    orders: tuple[TradeOrder, ...]
    carried: tuple[TradeOrder, ...] = ()
    """Orders planned on an earlier session that are still live; listed, not re-planned."""

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def trade_list_id(self) -> str:
        return stable_model_id(prefix="trl", model=self)


def _price_of(code: str, prices: Mapping[str, Decimal]) -> Decimal:
    try:
        return prices[code]
    except KeyError:
        raise ValueError(f"no reference price for {code}") from None


def build_trade_list(
    *,
    config: SurvivorBookConfig,
    state: BookState,
    decision: BookDecision,
    reference_prices: Mapping[str, Decimal],
    cash: Decimal,
    carried: tuple[TradeOrder, ...] = (),
) -> TradeList:
    """Turn `decision` into orders for the session after `state.session`."""
    if cash < 0:
        raise ValueError("cash must not be negative")
    execute = state.session + 1
    held = {holding.ts_code: holding.quantity for holding in state.holdings}

    sells: list[TradeOrder] = []
    proceeds = Decimal(0)
    for decided in decision.exits:
        price = _price_of(decided.ts_code, reference_prices)
        quantity = held[decided.ts_code]
        proceeds += price * quantity
        sells.append(
            TradeOrder(
                signal_session=state.session,
                execute_session=execute,
                side="sell",
                ts_code=decided.ts_code,
                quantity=quantity,
                reference_price=price,
                reason=decided.reason,
            )
        )

    unit_budget = config.sizing_budget(state.equity)
    available = cash + proceeds
    buys: list[TradeOrder] = []
    for entry in decision.entries:
        price = _price_of(entry.ts_code, reference_prices)
        quantity = min(
            lot_quantity(unit_budget, price, config.lot_size),
            lot_quantity(available, price, config.lot_size),
        )
        if quantity == 0:
            continue
        available -= price * quantity
        buys.append(
            TradeOrder(
                signal_session=state.session,
                execute_session=execute,
                expires_session=state.session + config.buy_order_expiry_sessions,
                side="buy",
                ts_code=entry.ts_code,
                quantity=quantity,
                reference_price=price,
                reason=entry.reason,
            )
        )

    return TradeList(
        signal_session=state.session,
        orders=tuple(sorted(sells, key=lambda o: o.ts_code)) + tuple(buys),
        carried=carried,
    )
```

- [ ] **Step 4：运行，确认通过，再跑静态检查**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_orders.py -q -p no:cacheprovider
"$PY/ruff" check src/openalpha_cn/domain/survivor_orders.py tests/unit/domain/test_survivor_orders.py
"$PY/ruff" format --check src/openalpha_cn/domain/survivor_orders.py tests/unit/domain/test_survivor_orders.py
"$PY/mypy" src/openalpha_cn/domain/survivor_orders.py
```
预期：`18 passed`；其余无问题。

- [ ] **Step 5：更新两个守卫（又新增了两个 `stable_model_id` 调用点，前缀 `tor`、`trl`）**

(a) `tests/unit/domain/test_manifest_component_provenance.py`：`assert len(prefixes) == 28` / `assert len(set(prefixes)) == 25` 改为 `== 30` / `== 27`；并把同一函数文档字符串里 "`V2-P4-017`'s `prd` is the newest, and it was chosen by asking this census which three-letter prefixes were free." 之后补一句 "`V2-P7-005` added `sbc`, `tor` and `trl`: 30 call sites, 27 distinct prefixes."（保持每行 ≤ 100 列）。
(b) `tests/unit/domain/test_contract_identity.py` 里 `live_prefixes` 一段文字 "27 of them carrying 24 distinct prefixes, none containing an underscore, as of this issue." 改为 "30 of them carrying 27 distinct prefixes, none containing an underscore, as of `V2-P7-005` (`sbc`, `tor` and `trl` joined the 24 that `V2-P4-017` left)."（重新折行，每行 ≤ 100 列）。
(c) `src/openalpha_cn/backtest/candidate_ranking.py`：把 `twenty call` 改为 `22 call`（该测试的计数词表只到 twenty，超过用阿拉伯数字）。

运行：
```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_manifest_component_provenance.py tests/unit/domain/test_contract_identity.py tests/unit/test_source_counts_match_the_tree.py -q -p no:cacheprovider
"$PY/ruff" check src tests/unit/domain && "$PY/ruff" format --check src tests/unit/domain
```
预期：全部通过。

- [ ] **Step 6：提交**

```bash
git add src/openalpha_cn/domain/survivor_orders.py tests/unit/domain/test_survivor_orders.py tests/unit/domain/test_manifest_component_provenance.py tests/unit/domain/test_contract_identity.py src/openalpha_cn/backtest/candidate_ranking.py
git commit -m "feat(V2-P7-008): orders and the daily trade list

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 6：订单生命周期 `survivor_lifecycle.py`

**Files:**
- Create: `src/openalpha_cn/domain/survivor_lifecycle.py`
- Create: `tests/unit/domain/test_survivor_lifecycle.py`

**Interfaces:**
- Consumes: Task 1 `SurvivorBookConfig`；Task 4 `Cooldown`；Task 5 `TradeOrder`。
- Produces：`OrderAdvance(live, filled, expired, cancelled)`；`advance_orders(orders, *, session: int, filled_order_ids: Collection[str], survivors: Collection[str]) -> OrderAdvance`；`update_cooldowns(cooldowns, *, session: int, sold: Collection[str], expired_buys: Collection[str], config) -> tuple[Cooldown, ...]`。

- [ ] **Step 1：写失败的测试** — 创建 `tests/unit/domain/test_survivor_lifecycle.py`：

```python
"""What becomes of an order after its session, and who is cooling down (`V2-P7-008`)."""

from decimal import Decimal

from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_lifecycle import advance_orders, update_cooldowns
from openalpha_cn.domain.survivor_orders import TradeOrder
from openalpha_cn.domain.survivor_policy import Cooldown


def order(code: str, side: str, *, signal: int = 100, expires: int | None = None) -> TradeOrder:
    return TradeOrder(
        signal_session=signal,
        execute_session=signal + 1,
        expires_session=expires if side == "buy" else None,
        side=side,  # type: ignore[arg-type]
        ts_code=code,
        quantity=100,
        reference_price=Decimal("10"),
        reason="ENTRY_REPLACEMENT" if side == "buy" else "EXIT_SURVIVOR_LOST",
    )


def test_a_filled_order_is_done_whatever_else_is_true_of_it() -> None:
    buy = order("A", "buy", expires=103)

    # Observed on its last session, filled, and no longer a survivor: the fill is what happened.
    result = advance_orders([buy], session=103, filled_order_ids={buy.order_id}, survivors=set())

    assert result.filled == (buy,)
    assert result.live == result.expired == result.cancelled == ()


def test_an_unfilled_sell_is_carried_for_as_long_as_it_takes() -> None:
    sell = order("A", "sell")

    for session in (101, 103, 150):
        result = advance_orders([sell], session=session, filled_order_ids=set(), survivors=set())
        assert result.live == (sell,)


def test_a_buy_is_carried_until_its_last_session_and_then_expires() -> None:
    buy = order("A", "buy", expires=103)

    carried = advance_orders([buy], session=102, filled_order_ids=set(), survivors={"A"})
    assert carried.live == (buy,)

    expired = advance_orders([buy], session=103, filled_order_ids=set(), survivors={"A"})
    assert expired.expired == (buy,)
    assert expired.live == ()


def test_a_buy_for_a_name_that_is_no_longer_a_survivor_is_cancelled_not_carried() -> None:
    buy = order("A", "buy", expires=103)

    result = advance_orders([buy], session=101, filled_order_ids=set(), survivors={"B"})

    assert result.cancelled == (buy,)
    assert result.live == ()


def test_a_sell_is_carried_even_when_its_name_is_not_a_survivor() -> None:
    sell = order("A", "sell")

    result = advance_orders([sell], session=101, filled_order_ids=set(), survivors=set())

    assert result.live == (sell,)
    assert result.cancelled == ()


def test_the_outcome_is_ordered_and_does_not_depend_on_the_order_given() -> None:
    orders = [order("B", "buy", expires=103), order("A", "buy", expires=103), order("C", "sell")]

    forward = advance_orders(orders, session=101, filled_order_ids=set(), survivors={"A", "B"})
    backward = advance_orders(
        list(reversed(orders)), session=101, filled_order_ids=set(), survivors={"A", "B"}
    )

    assert forward == backward
    assert [o.ts_code for o in forward.live] == ["A", "B", "C"]


def config() -> SurvivorBookConfig:
    return SurvivorBookConfig(seed=1, cooldown_sessions=5)


def test_a_sale_and_an_expired_buy_each_start_a_cooldown() -> None:
    result = update_cooldowns((), session=100, sold={"B"}, expired_buys={"A"}, config=config())

    assert result == (
        Cooldown(ts_code="A", since_session=100),
        Cooldown(ts_code="B", since_session=100),
    )


def test_a_cooldown_that_has_run_out_is_dropped_and_one_still_running_is_kept() -> None:
    existing = (
        Cooldown(ts_code="OLD", since_session=95),
        Cooldown(ts_code="NEW", since_session=96),
    )

    result = update_cooldowns(
        existing, session=100, sold=set(), expired_buys=set(), config=config()
    )

    # Five sessions have passed for OLD (100 - 95 = 5, not less than 5): it is free again.
    assert result == (Cooldown(ts_code="NEW", since_session=96),)


def test_selling_a_name_again_restarts_its_cooldown() -> None:
    existing = (Cooldown(ts_code="A", since_session=98),)

    result = update_cooldowns(
        existing, session=100, sold={"A"}, expired_buys=set(), config=config()
    )

    assert result == (Cooldown(ts_code="A", since_session=100),)
```

- [ ] **Step 2：运行，确认因模块不存在而失败**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_lifecycle.py -q -p no:cacheprovider
```
预期：`ModuleNotFoundError: No module named 'openalpha_cn.domain.survivor_lifecycle'`。

- [ ] **Step 3：写实现** — 创建 `src/openalpha_cn/domain/survivor_lifecycle.py`：

```python
"""What happens to an order after the session it was meant for (`V2-P7-008`).

An order is *filled*, or it stays *live*, or it is *expired* (a buy past its last session) or
*cancelled* (a buy whose name is no longer a survivor). A sell is live until it fills; there is no
state in which a holding that must go is quietly kept. The functions take what the paper ledger
observed -- which orders filled -- and decide nothing about prices or fills themselves.
"""

from collections.abc import Collection, Sequence

from pydantic import BaseModel, ConfigDict

from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_orders import TradeOrder
from openalpha_cn.domain.survivor_policy import Cooldown


class OrderAdvance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    live: tuple[TradeOrder, ...]
    filled: tuple[TradeOrder, ...]
    expired: tuple[TradeOrder, ...]
    cancelled: tuple[TradeOrder, ...]


def advance_orders(
    orders: Sequence[TradeOrder],
    *,
    session: int,
    filled_order_ids: Collection[str],
    survivors: Collection[str],
) -> OrderAdvance:
    """Settle `orders` after `session` has been observed.

    `survivors` is today's survivor set: a buy for a name outside it is cancelled rather than
    carried -- it was chosen yesterday and nothing today still recommends it.
    """
    live: list[TradeOrder] = []
    filled: list[TradeOrder] = []
    expired: list[TradeOrder] = []
    cancelled: list[TradeOrder] = []
    for order in sorted(orders, key=lambda o: (o.side, o.ts_code, o.order_id)):
        if order.order_id in filled_order_ids:
            filled.append(order)
        elif order.side == "sell":
            live.append(order)
        elif order.expires_session is not None and session >= order.expires_session:
            expired.append(order)
        elif order.ts_code not in survivors:
            cancelled.append(order)
        else:
            live.append(order)
    return OrderAdvance(
        live=tuple(live),
        filled=tuple(filled),
        expired=tuple(expired),
        cancelled=tuple(cancelled),
    )


def update_cooldowns(
    cooldowns: Sequence[Cooldown],
    *,
    session: int,
    sold: Collection[str],
    expired_buys: Collection[str],
    config: SurvivorBookConfig,
) -> tuple[Cooldown, ...]:
    """Start a cool-down for names sold or whose buy expired; drop the ones that have run out.

    An expired buy cools down too: a name that could not be bought for three sessions (a run of
    limit-ups, a halt) is not chased again the next morning.
    """
    kept = {
        cooldown.ts_code: cooldown
        for cooldown in cooldowns
        if session - cooldown.since_session < config.cooldown_sessions
    }
    for code in sorted(set(sold) | set(expired_buys)):
        kept[code] = Cooldown(ts_code=code, since_session=session)
    return tuple(kept[code] for code in sorted(kept))
```

- [ ] **Step 4：运行，确认通过，再跑静态检查**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_lifecycle.py -q -p no:cacheprovider
"$PY/ruff" check src/openalpha_cn/domain/survivor_lifecycle.py tests/unit/domain/test_survivor_lifecycle.py
"$PY/ruff" format --check src/openalpha_cn/domain/survivor_lifecycle.py tests/unit/domain/test_survivor_lifecycle.py
"$PY/mypy" src/openalpha_cn/domain/survivor_lifecycle.py
```
预期：`9 passed`；其余无问题。

- [ ] **Step 5：提交**

```bash
git add src/openalpha_cn/domain/survivor_lifecycle.py tests/unit/domain/test_survivor_lifecycle.py
git commit -m "feat(V2-P7-008): order lifecycle and cool-downs

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 7：逐日仿真不变量、变异检查、文档回写与门禁

**Files:**
- Create: `tests/unit/domain/test_survivor_book_lifecycle.py`
- Create: `docs/research/p7-step0-validation/V2-P7-005-mutation-checks.md`
- Modify: `docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md`（§6 表增行、§7 `CARRIED`、§11 回写、§12 写入"领域内核已实现"）
- Modify: `docs/specs/v2/openalpha-cn-v2-roadmap.md`（`V2-P7-005`、`V2-P7-008` 两行写上已落地的内核与证据）
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1–6 的全部公开名字。
- Produces: 无新接口；产出的是对内核的"逐日不变量"测试与变异报告。

- [ ] **Step 0：修复波 3**（控制者在派发时给出补丁路径 `.superpowers/sdd/fix-wave-3.patch`；它只改三个文件，是前面三轮评审里剩下的小项）：`git apply` 之后运行 `tests/unit/domain/test_survivor_selection.py` 与 `tests/unit/domain/test_survivor_policy.py`，预期全过；它做三件事：(a) 选择层的子进程测试把被测树的 `src` 放进子进程的 `PYTHONPATH`，使它在隔离 worktree 里也导入被测的那份代码；(b) `survivor_policy.py` 的 `composite_of` 去掉一个因 `EligibilityVerdict` 已拒绝"合格但无合成分"而不可达的防御分支，改用带注释的 `cast`；(c) `update_cooldowns` 的文档写明 `session` 是**结算日**（卖出成交或买单过期的那一天），不是下单的信号日。提交信息：`fix(V2-P7-005): run the hash-seed children against the tree under test, and say what a cool-down counts from`。

- [ ] **Step 1：写测试** — 创建 `tests/unit/domain/test_survivor_book_lifecycle.py`（这条测试在内核全部就位后**第一次运行就应当通过**——它不是为新代码驱动实现，而是把前面六个任务的承诺在 120 个交易日的合成市场里逐日验证；它的失败能力由 Step 3 的变异检查证明）：

```python
"""The survivor book over many sessions of a simulated market (`V2-P7-005`, `V2-P7-008`).

A walk through 120 sessions of a made-up market (a seeded random walk of scores and prices, with
suspensions and special treatment) drives the whole core in the order the daily flow will use
it: **settle** the orders planned the session before (a sell on a suspended name does not fill, so
it stays live), then `screen`, `decide_book` and `build_trade_list`. It asserts the properties
the design promises on **every** session, not on the last one: the book never exceeds its size,
never overspends, never churns faster than the cap, never sells what should be kept, never sells
a name twice, leaves a suspended holding alone, and never buys what is cooling. The mutation
checks recorded in the plan are run against these tests.
"""

import hashlib
import json
import os
import random
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from openalpha_cn.domain.survivor_book import SecurityFacts, SurvivorBookConfig
from openalpha_cn.domain.survivor_lifecycle import advance_orders, update_cooldowns
from openalpha_cn.domain.survivor_orders import TradeOrder, build_trade_list
from openalpha_cn.domain.survivor_policy import BookState, Cooldown, Holding, decide_book
from openalpha_cn.domain.survivor_screen import screen

UNIVERSE = 80
SESSIONS = 120
EXERCISED: Counter[str] = Counter()
"""What the simulated markets actually made the book do, so a test can assert that a branch it
claims to cover was reached rather than assume it was."""


def code(position: int) -> str:
    return f"{600000 + position:06d}.SH"


def walk(market_seed: int) -> list[list[SecurityFacts]]:
    """`SESSIONS` cross sections whose scores and prices drift and whose problems come and go."""
    rng = random.Random(market_seed)
    scores = [rng.gauss(0, 1) for _ in range(UNIVERSE)]
    prices = [rng.uniform(5, 40) for _ in range(UNIVERSE)]
    amounts = [rng.uniform(1, 100) for _ in range(UNIVERSE)]
    halted: dict[int, int] = {}
    days: list[list[SecurityFacts]] = []
    for _ in range(SESSIONS):
        for position in range(UNIVERSE):
            scores[position] = 0.9 * scores[position] + rng.gauss(0, 0.5)
            prices[position] = max(2.0, prices[position] * (1 + rng.gauss(0, 0.02)))
            if rng.random() < 0.02:
                halted[position] = 4
        facts = []
        for position in range(UNIVERSE):
            suspended = halted.get(position, 0) > 0
            if suspended:
                halted[position] -= 1
            facts.append(
                SecurityFacts(
                    ts_code=code(position),
                    board="growth" if position % 5 == 0 else "main",
                    listed_sessions=400,
                    special_treatment=position % 37 == 0,
                    suspended=suspended,
                    close=Decimal(f"{prices[position]:.2f}"),
                    avg_amount=Decimal(f"{amounts[position]:.2f}"),
                    oriented_values=(scores[position],) * 4,
                )
            )
        days.append(facts)
    return days


def run(market_seed: int, config: SurvivorBookConfig) -> list[dict[str, object]]:
    """Play the market through the book; return one record per session, asserting as it goes."""
    cash = config.capital
    held: dict[str, int] = {}
    live: list[TradeOrder] = []
    cooldowns: tuple[Cooldown, ...] = ()
    initialized = False
    sold_at: dict[str, int] = {}
    log: list[dict[str, object]] = []
    for session, facts in enumerate(walk(market_seed)):
        close = {fact.ts_code: fact.close for fact in facts}
        halted = {fact.ts_code for fact in facts if fact.suspended}
        result = screen(facts, config)
        survivors = {name.ts_code for name in result.survivors}
        eligible = {verdict.ts_code for verdict in result.verdicts if verdict.eligible}

        # 1. Settle what was planned the session before, at its reference price. A halted name
        #    cannot trade, so its order does not fill and stays live; a buy needs the cash.
        filled: set[str] = set()
        sold: set[str] = set()
        for order in sorted(live, key=lambda o: (o.side != "sell", o.ts_code)):
            value = order.reference_price * order.quantity
            if order.ts_code in halted or (order.side == "buy" and value > cash):
                EXERCISED["unfilled"] += 1
                continue
            if order.side == "sell":
                cash += value
                del held[order.ts_code]
                sold.add(order.ts_code)
                sold_at[order.ts_code] = session
            else:
                cash -= value
                held[order.ts_code] = order.quantity
            filled.add(order.order_id)
        settled = advance_orders(
            live, session=session, filled_order_ids=filled, survivors=survivors
        )
        live = list(settled.live)
        EXERCISED["expired"] += len(settled.expired)
        EXERCISED["cancelled"] += len(settled.cancelled)
        for expired in settled.expired:
            sold_at[expired.ts_code] = session
        cooldowns = update_cooldowns(
            cooldowns,
            session=session,
            sold=sold,
            expired_buys={order.ts_code for order in settled.expired},
            config=config,
        )
        assert cash >= 0

        # 2. Decide, from the state after settlement.
        equity = cash + sum(Decimal(q) * close[c] for c, q in held.items())
        pending_buys = tuple(sorted(o.ts_code for o in live if o.side == "buy"))
        pending_sells = tuple(sorted(o.ts_code for o in live if o.side == "sell"))
        state = BookState(
            session=session,
            initialized=initialized,
            equity=equity,
            holdings=tuple(Holding(ts_code=c, quantity=q) for c, q in sorted(held.items())),
            pending_buys=pending_buys,
            pending_sells=pending_sells,
            cooldowns=cooldowns,
        )
        decision = decide_book(config=config, screen=result, state=state)
        plan = build_trade_list(
            config=config,
            state=state,
            decision=decision,
            reference_prices=close,
            cash=cash,
            carried=tuple(live),
        )
        # The plan itself must fit in the cash: what today's buys cost plus what buys already live
        # will cost cannot exceed the cash plus the proceeds of today's sells. (Live buys alone may
        # exceed it -- then nothing new is planned.)
        planned_buys = sum(
            (o.reference_price * o.quantity for o in plan.orders if o.side == "buy"), Decimal(0)
        )
        live_buys = sum(
            (o.reference_price * o.quantity for o in live if o.side == "buy"), Decimal(0)
        )
        planned_sells = sum(
            (o.reference_price * o.quantity for o in plan.orders if o.side == "sell"), Decimal(0)
        )
        assert planned_buys == 0 or planned_buys + live_buys <= cash + planned_sells
        EXERCISED["frozen"] += len(decision.frozen)
        EXERCISED["pending_sells"] += len(pending_sells)

        # 3. The promises, on every session.
        for exited in decision.exits:
            assert exited.ts_code in held
            assert exited.ts_code not in pending_sells, "a name is sold twice"
            if exited.reason == "EXIT_SURVIVOR_LOST":
                assert exited.ts_code in eligible and exited.ts_code not in survivors
            else:
                assert exited.ts_code not in eligible
                assert exited.ts_code not in decision.frozen
        assert {c for c in held if c in survivors}.isdisjoint(e.ts_code for e in decision.exits)
        assert set(decision.frozen) <= halted
        assert set(decision.frozen).isdisjoint(e.ts_code for e in decision.exits)

        discretionary = [e for e in decision.exits if e.reason == "EXIT_SURVIVOR_LOST"]
        assert len(discretionary) <= config.max_replacements_per_session
        if initialized:
            assert len(decision.entries) <= config.max_replacements_per_session
        for entry in decision.entries:
            assert entry.ts_code not in held
            assert entry.ts_code not in pending_buys
            assert entry.ts_code in survivors
            last = sold_at.get(entry.ts_code)
            assert last is None or session - last >= config.cooldown_sessions

        active = [c for c in held if c not in pending_sells]
        assert len(decision.target_holdings) + len(pending_buys) <= config.holding_count
        assert len(active) + len(pending_buys) <= config.holding_count
        assert all(o.quantity % config.lot_size == 0 for o in plan.orders)

        live.extend(plan.orders)
        initialized = True
        log.append(
            {
                "trade_list": plan.trade_list_id,
                "held": sorted(held),
                "live": sorted(o.order_id for o in live),
                "cash": str(cash),
            }
        )
    return log


def config(**overrides: object) -> SurvivorBookConfig:
    return SurvivorBookConfig(seed=3, **overrides)  # type: ignore[arg-type]


def test_the_book_keeps_its_promises_on_every_session_of_several_markets() -> None:
    for market_seed in range(6):
        run(market_seed, config())


def test_the_markets_really_do_reach_the_branches_the_promises_are_about() -> None:
    before = Counter(EXERCISED)
    for market_seed in range(6):
        run(market_seed, config())
    reached = EXERCISED - before

    # A halted holding that is left alone, a sell that stays live because its name is halted, and
    # an order that could not fill: if the markets never got here the assertions above are idle.
    assert reached["frozen"] > 0
    assert reached["pending_sells"] > 0
    assert reached["unfilled"] > 0


def test_the_first_session_builds_the_book_and_most_later_sessions_do_nothing() -> None:
    log = run(0, config())

    assert log[0]["held"] == []  # the first orders fill at the next session's open
    # A name halted on its first day cannot be bought then, so the first fill may be a name short;
    # the book is whole soon after.
    assert len(log[1]["held"]) >= 8  # type: ignore[arg-type]
    assert max(len(record["held"]) for record in log) == 10  # type: ignore[arg-type]
    quiet = sum(1 for before, after in pairwise(log[1:]) if before["held"] == after["held"])
    assert quiet > len(log) // 2


def test_the_whole_run_is_reproducible() -> None:
    assert run(2, config()) == run(2, config())


def test_a_smaller_book_with_a_longer_cooldown_keeps_the_same_promises() -> None:
    run(1, config(holding_count=5, cooldown_sessions=40, max_replacements_per_session=1))


def test_the_whole_chain_gives_the_same_bytes_in_processes_with_different_hash_seeds() -> None:
    """Not only the selection rule: screen, policy, orders and lifecycle together, end to end."""
    here = Path(__file__).resolve()
    program = (
        "import hashlib, importlib.util, json, sys;"
        "spec = importlib.util.spec_from_file_location('sim', sys.argv[1]);"
        "sim = importlib.util.module_from_spec(spec);"
        "spec.loader.exec_module(sim);"
        "log = sim.run(2, sim.config());"
        "print(hashlib.sha256(json.dumps(log, sort_keys=True).encode()).hexdigest())"
    )
    source_root = str(here.parents[3] / "src")
    reference = hashlib.sha256(json.dumps(run(2, config()), sort_keys=True).encode()).hexdigest()

    for hash_seed in ("0", "1", "424242"):
        completed = subprocess.run(
            [sys.executable, "-c", program, str(here)],
            env={
                **os.environ,
                "PYTHONHASHSEED": hash_seed,
                "PYTHONPATH": os.pathsep.join(
                    part for part in (source_root, os.environ.get("PYTHONPATH")) if part
                ),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == reference
```

- [ ] **Step 2：运行**

```bash
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain/test_survivor_book_lifecycle.py tests/unit/domain/test_survivor_*.py -q -p no:cacheprovider
```
预期：`166 passed`（`test_survivor_*.py` 七个文件合计，无跳过）。

- [ ] **Step 3：变异检查** — 把下面的脚本存到 **仓库之外**（例如你的临时目录 `mutate.py`），对本分支的检出运行：

```bash
python3 mutate.py "$PWD" "$PY/python"
```
```python
"""Apply each mutation to the survivor-book source, run its tests, expect them to fail."""

import pathlib, shutil, subprocess, sys, os

ROOT = pathlib.Path(sys.argv[1])  # worktree root
PY = sys.argv[2]
DOMAIN = ROOT / "src/openalpha_cn/domain"
TESTS = sorted(str(p) for p in (ROOT / "tests/unit/domain").glob("test_survivor_*.py"))

MUTATIONS = [
    (
        "survivor_policy.py",
        "state.session - cooldown.since_session < config.cooldown_sessions",
        "state.session - cooldown.since_session < 0",
        "cooldown never blocks",
    ),
    (
        "survivor_policy.py",
        "state.session - cooldown.since_session < config.cooldown_sessions",
        "state.session - cooldown.since_session <= config.cooldown_sessions",
        "cooldown boundary off by one",
    ),
    ("survivor_policy.py", "lost[:cap]", "lost", "discretionary exits uncapped"),
    (
        "survivor_policy.py",
        "deferred = tuple(lost[cap:])",
        "deferred = ()",
        "deferred exits not reported",
    ),
    ("survivor_policy.py", "min(vacancies, cap)", "vacancies", "refill not rate-limited"),
    (
        "survivor_policy.py",
        "allowance = vacancies if not state.initialized",
        "allowance = vacancies if state.initialized",
        "first build rate-limited",
    ),
    (
        "survivor_policy.py",
        "affordable[:allowance]",
        "affordable[::-1][:allowance]",
        "draws the end of the queue",
    ),
    (
        "survivor_policy.py",
        "verdicts[code].eligible and code not in survivors",
        "verdicts[code].eligible and code in survivors",
        "sells survivors instead of losers",
    ),
    (
        "survivor_policy.py",
        "code not in verdicts or not verdicts[code].eligible",
        "code not in verdicts or verdicts[code].eligible",
        "forced exit inverted",
    ),
    (
        "survivor_policy.py",
        "key=lambda code: (composite_of(code), code)",
        "key=lambda code: (-composite_of(code), code)",
        "best composite exits first",
    ),
    (
        "survivor_policy.py",
        "entrant_count = len(ranked) - floor_share(len(ranked), config.entry_floor_fraction)",
        "entrant_count = len(ranked)",
        "entry floor ignored",
    ),
    (
        "survivor_policy.py",
        "blocked = set(held) | set(state.pending_buys) | cooling",
        "blocked = set(held) | cooling",
        "pending names redrawn",
    ),
    (
        "survivor_policy.py",
        "- len(exits) + len(state.pending_buys)",
        "- len(exits)",
        "pending buys ignored in occupancy",
    ),
    (
        "survivor_policy.py",
        "if lot_quantity(unit_budget, survivors[code].close, config.lot_size) == 0:",
        "if lot_quantity(unit_budget, survivors[code].close, config.lot_size) < 0:",
        "unaffordable never skipped",
    ),
    (
        "survivor_policy.py",
        "survivors[code].rank > near_cutoff",
        "survivors[code].rank >= near_cutoff",
        "near-exit band off by one",
    ),
    (
        "survivor_policy.py",
        "blocked = set(held) | set(state.pending_buys) | cooling",
        "blocked = set(state.pending_buys) | cooling",
        "held names redrawn",
    ),
    (
        "survivor_screen.py",
        "config.loser_decile_count // 10",
        "(config.loser_decile_count + 1) // 10",
        "loser cut off",
    ),
    (
        "survivor_screen.py",
        "if fact.board not in config.boards:",
        "if fact.board in config.boards:",
        "board test inverted",
    ),
    (
        "survivor_screen.py",
        "fact.listed_sessions < config.min_listing_sessions",
        "fact.listed_sessions <= config.min_listing_sessions",
        "listing boundary",
    ),
    (
        "survivor_screen.py",
        "allowed[: floor_share(len(allowed), config.liquidity_drop_fraction)]",
        "allowed[floor_share(len(allowed), config.liquidity_drop_fraction) :]",
        "keeps the thin names out of the cut",
    ),
    ("survivor_screen.py", "math.fsum(present)", "sum(present)", "inexact sum"),
    (
        "survivor_screen.py",
        "any(value is None for value in values)",
        "all(value is None for value in values)",
        "partial composite",
    ),
    (
        "survivor_screen.py",
        "(fact for fact in facts if fact.board in config.boards)",
        "(fact for fact in facts)",
        "liquidity over all boards",
    ),
    (
        "survivor_selection.py",
        "distinct = set(candidates)",
        "distinct = list(candidates)",
        "duplicates not collapsed",
    ),
    (
        "survivor_selection.py",
        'f"{seed}|{session}|{ts_code}"',
        'f"{session}|{ts_code}"',
        "seed ignored",
    ),
    ("survivor_orders.py", "quantity = held[decided.ts_code]", "quantity = 100", "sells one lot"),
    ("survivor_orders.py", "proceeds += price * quantity", "pass", "proceeds ignored"),
    (
        "survivor_orders.py",
        "            lot_quantity(available * (1 - config.cost_reserve_fraction), price, config.lot_size),\n",
        "            10**9,\n",
        "cash ignored",
    ),
    ("survivor_orders.py", "available -= price * quantity", "pass", "cash not consumed"),
    (
        "survivor_orders.py",
        "state.session + config.buy_order_expiry_sessions",
        "state.session + 1",
        "wrong expiry",
    ),
    ("survivor_orders.py", "sorted(sells, key=lambda o: o.ts_code)", "sells", "sells unsorted"),
    (
        "survivor_lifecycle.py",
        "session >= order.expires_session",
        "session > order.expires_session",
        "expiry off by one",
    ),
    (
        "survivor_lifecycle.py",
        "elif order.ts_code not in survivors:",
        "elif order.ts_code in survivors:",
        "cancels survivors",
    ),
    (
        "survivor_lifecycle.py",
        "if session - cooldown.since_session < config.cooldown_sessions",
        "if session - cooldown.since_session <= config.cooldown_sessions",
        "cooldown prune boundary",
    ),
    (
        "survivor_lifecycle.py",
        "set(sold) | set(expired_buys)",
        "set(sold)",
        "expired buys do not cool down",
    ),
    ("survivor_policy.py", "and code not in frozen", "", "a suspended holding is sold"),
    (
        "survivor_policy.py",
        "active = [code for code in held if code not in set(state.pending_sells)]",
        "active = list(held)",
        "a live sell is sold again",
    ),
    (
        "survivor_policy.py",
        "occupied = len(active) - len(exits)",
        "occupied = len(held) - len(exits)",
        "a live sell still holds a slot",
    ),
    (
        "survivor_policy.py",
        '        and "suspended" in verdicts[code].reasons\n',
        "",
        "a thin name freezes without any suspension",
    ),
    (
        "survivor_selection.py",
        'f"{seed}|{session}|{ts_code}"',
        'f"{seed}|{ts_code}"',
        "session ignored",
    ),
    (
        "survivor_orders.py",
        "available = max(cash + proceeds - reserved, Decimal(0))",
        "available = cash + proceeds",
        "live buys not reserved",
    ),
    (
        "survivor_orders.py",
        'raise ValueError(f"cannot sell {decided.ts_code}: it is not held")',
        "pass",
        "selling an unheld name is silent",
    ),
    (
        "survivor_book.py",
        "if positions != sorted(set(positions)):",
        "if False:",
        "verdict reasons unchecked",
    ),
    ("survivor_book.py", "if codes != sorted(set(codes)):", "if False:", "verdict order unchecked"),
    (
        "survivor_policy.py",
        "if not set(self.pending_sells) <= set(held):",
        "if False:",
        "pending sell of an unheld name",
    ),
    (
        "survivor_policy.py",
        "and set(verdicts[code].reasons) <= FREEZE_COMPATIBLE_REASONS",
        "",
        "any halt with any problem freezes",
    ),
    (
        "survivor_orders.py",
        "lot_quantity(available * (1 - config.cost_reserve_fraction), price, config.lot_size)",
        "lot_quantity(available, price, config.lot_size)",
        "cash cap without reserve",
    ),
    (
        "survivor_orders.py",
        "if live_buys != set(state.pending_buys) or live_sells != set(state.pending_sells):",
        "if False:",
        "carried orders not checked against the state",
    ),
    (
        "survivor_orders.py",
        "if self.expires_session is None or self.expires_session < self.execute_session:",
        "if False:",
        "a buy that never expires",
    ),
    (
        "survivor_orders.py",
        'raise ValueError("an order executes after the session that planned it")',
        "pass",
        "an order that executes before it is planned",
    ),
    (
        "survivor_book.py",
        "if len({name.ts_code for name in self.survivors}) != len(self.survivors):",
        "if False:",
        "a survivor listed twice",
    ),
    (
        "survivor_lifecycle.py",
        "        elif order.expires_session is not None and session >= order.expires_session:\n            expired.append(order)\n        elif order.ts_code not in survivors:\n            cancelled.append(order)\n",
        "        elif order.ts_code not in survivors:\n            cancelled.append(order)\n        elif order.expires_session is not None and session >= order.expires_session:\n            expired.append(order)\n",
        "cancel is checked before expiry",
    ),
]

survived, killed, bad = [], [], []
table: list[str] = []
for filename, old, new, label in MUTATIONS:
    path = DOMAIN / filename
    original = path.read_text()
    if original.count(old) != 1:
        bad.append((label, original.count(old)))
        continue
    path.write_text(original.replace(old, new))
    try:
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        done = subprocess.run(
            [PY, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *TESTS],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
    finally:
        path.write_text(original)
    (killed if done.returncode != 0 else survived).append(label)
    table.append(
        f"| {len(table) + 1} | {label} | `{filename}` | {'killed' if done.returncode != 0 else 'SURVIVED'} |"
    )

if os.environ.get("MUTATE_TABLE"):
    print("\n".join(table))
print(f"killed {len(killed)} / {len(MUTATIONS)}")
print("SURVIVED:", survived)
print("PATTERN NOT UNIQUE:", bad)
```
预期输出 `killed 45 / 45`、`SURVIVED: []`、`PATTERN NOT UNIQUE: []`。若有变异存活：**先判断它是不是等价变异**（改了代码但行为不可能变）；若不是，说明测试有缺口——补一条会让它变红的测试（测试先行：先让变异存活 → 写测试 → 变异被杀死），不要删变异。把最终的 45 行（标签 → 文件 → 被杀死/存活）写进 `docs/research/p7-step0-validation/V2-P7-005-mutation-checks.md`，开头说明："这是对 `domain/survivor_*.py` 的手工变异检查；脚本不入库；每个变异逐个应用、跑 `tests/unit/domain/test_survivor_*.py`、还原。"

- [ ] **Step 4：回写设计文档** — 控制者已在设计文档里写过 §4.2、§4.3、§6（可买性、选择层、冷却、停牌的持仓）与 §12（T1/T4/T5 与内核修订）。你只补下面三处，**其余不要改动**：
  1. §6 参数表末尾加一行：`| 成本预留 | 每笔买单预算预留 0.5% | 避免费用与滑点把最后一笔买单挤出现金（`cost_reserve_fraction`，Task 1） |`。
  2. §7 的"清单列"一条里把原因枚举里的 `CARRIED` 去掉，并在下面加一行："延续的订单保留原始原因，列在 `TradeList.carried` 中；`status` 由账本推进给出。"
  3. §6 "每日替换上限"一行的"依据"改为 "强制退出不限；自主退出（掉出幸存集合）每日 ≤ 2 笔、按合成分由低到高；首次建仓之后每日买入 ≤ 2 笔"。
  并把 §12 回写区里"**领域内核**"一行末尾的"（修复波 1、2，见计划 1 的"修订记录"）"之后补上最终数字："最终：`tests/unit/domain/test_survivor_*.py` 共 166 个测试通过，45/45 个手工变异被杀死；报告 `docs/research/p7-step0-validation/V2-P7-005-mutation-checks.md`。"

- [ ] **Step 6：本任务相关的全部静态与守卫检查**

```bash
"$PY/ruff" check .
"$PY/ruff" format --check .
"$PY/mypy" src
"$PY/lint-imports"
PYTHONPATH="$PWD/src" "$PY/python" scripts/build_feature_coverage.py --check
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/unit/domain -q -p no:cacheprovider
PYTHONPATH="$PWD/src" "$PY/python" -m pytest tests/integration/test_documented_counts_match_the_code.py tests/unit/test_source_counts_match_the_tree.py tests/unit/test_import_layering.py -q -p no:cacheprovider
```
预期：全部通过（`tests/unit/domain` 约 1400 个、十余秒）。

- [ ] **Step 7：提交**

```bash
git add tests/unit/domain/test_survivor_book_lifecycle.py docs/research/p7-step0-validation/V2-P7-005-mutation-checks.md docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md docs/specs/v2/openalpha-cn-v2-roadmap.md CHANGELOG.md
git commit -m "test(V2-P7-005): the survivor book keeps its promises on every session of a simulated market

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### 门禁（控制者在全部任务评审通过后做，不派给子代理）

1. 在主检出里把 Phase 1 的提交合进 `feat/v2-p7-005-survivor-book-core`，**串行**跑完整门禁：`ruff check .`、`ruff format --check .`、`mypy src`、`lint-imports`、`pytest`（全量，约 45 分钟，用 `gate-full.sh` 形式，后台轮询）、`scripts/build_feature_coverage.py --check`、`scripts/verify_publication.py`。
2. 派一个最强模型的整分支评审（`superpowers:requesting-code-review`），把进度账本里的 Minor 问题清单交给它做取舍。
3. 全部通过后按 `superpowers:finishing-a-development-branch` 处理；**不要**自行 push / 合并到 main，把选择交给用户。

## 修订记录（实施期间的更正；计划里 Task 1–6 的代码是**最初**版本，最终代码以特性分支为准）

Task 1–6 按本计划的原文逐字实现并通过评审后，评审与 Phase 0 验证发现的缺口用三个补丁修复；每个补丁都先在一次性 worktree 里验证（全部测试通过、变异全部被杀死），再交给修复代理逐字应用并证明"新测试在旧代码上变红"。**对应提交：**

| 补丁 | 提交 | 内容 |
|---|---|---|
| 修复波 1 | `cbc6248` | `lot_quantity` / `floor_share` / `sizing_budget` 的取值守卫；`EligibilityVerdict` 强制"原因唯一且有序、合格者带合成分"；`Board` 与 `security_board` 的钉死测试；`config_id` 对 Decimal 排版敏感的文档与测试；选择层文档不再把探索性发现称为"evidence"；`SELECTION_RULES` 只读；子进程测试在失败时报告 stderr |
| 修复波 2 | `66270c3` | **选择层按交易日加盐**（T1 §5）；**"买不起一手"移出资格层**，`screen()` 不再有 `equity`（T4 §6.2）；**在途卖单** `BookState.pending_sells`（T4 §6.1）；**仅因停牌的持仓冻结**（T4 §6.3，待用户确认）；`ScreenResult` 一致性校验；订单构建为在途买单预留现金、卖出未持有的代码给出具名错误（T5）；Task 3/4 评审的测试加强（顺序无关、先抽到买不起的名字、混合强制与自主退出、平局、候补名单长度、先流动性后输家的手算用例） |
| 修复波 3 | `dd0d17c` | 子进程测试导入被测树；去掉不可达分支；写明冷却的起算日 |
| 修复波 4（整分支评审后） | 见特性分支上标题为 `fix(V2-P7-005): keep the cost reserve on a cash-limited buy, ...` 的提交 | 现金受限的买单也留成本预留；`build_trade_list` 拒绝自相矛盾的输入、携带订单按固定顺序；`TradeOrder` 拒绝账簿不可能下的单；冻结规则放宽到"所有问题都由停牌派生"；`ScreenResult` 拒绝重复幸存者；`lot_quantity` 精确整除；仿真改为先卖后买并核对计划本身放得进现金；整条链的哈希种子测试；过时文字更正 |

**变化后的最终数字（修复波 4 之后）：** `domain/survivor_*.py` 六个模块，`tests/unit/domain/test_survivor_*.py` 七个文件共 182 个测试，52 个手工变异全部被杀死（变异清单随 Task 7 的脚本；脚本本身已随修复波 4 增补）。Task 7 一节里嵌入的仿真测试与变异脚本是**第三轮**的版本，最终版本以特性分支为准。设计文档与上游文档的对应修订见 `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §9。

## 计划 2（本计划之外，等 Phase 0 结果）

适配器（面板 → `SecurityFacts`，板块用 `backtest.execution.security_board`）、纸面步骤构建器（T5）、每日入口与 `strategy.kind`（T4）、交易清单的落盘与摘要、纸面账本的每日推进与周报扩展（`V2-P7-008`/`V2-P7-009`）、对照与基准（`V2-P7-002`/`V2-P7-003`）、诊断工具（`V2-P7-004`）、协议与预登记（`V2-P7-006`，需要用户在研究决策检查点确认）。计划 2 在 Phase 0 报告回写设计 §12 之后写。
