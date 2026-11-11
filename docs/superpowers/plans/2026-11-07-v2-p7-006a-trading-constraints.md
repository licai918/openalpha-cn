# P7 批次 3 · `V2-P7-006a` 的交易约束内核（现金定量 / 涨停价冻结 / `illiquid`）

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **状态：草案，等用户审阅。** 本文只是计划；不改源码、不改测试（本文件除外）。每个 `file:line` 都是起草者在批次 2 合并后的 `feat/v2-p7-batch-3`（`ba60330`）上读过的。**本计划不发任何网络请求、不读真实面板、不跑真实晚间、不写登记**：全部测试用测试时构造的行情条与测试时生成的面板夹具（硬规则 6）。

**Goal:** 让每晚打印出来的交易清单在**用户真实的 09:15** 可执行：买单只用 09:15 那一刻账户里的现金定量（不含当晚卖单的回款），按买单挂的**涨停价**而不是收盘价计算券商冻结的资金，并把"某只没有按等权买到 / 一只都买不起"按名字报出来；同时把 `illiquid` 逐日规则的名字、阈值与 (a)/(b) 选择固化下来并钉住。这三件都是会改受绑定代码的事，必须在 `006b` 登记之前完成（`docs/superpowers/plans/2026-11-01-p7-route-adjustment.md` §2.4 第 2–3 项）。

**Architecture:** 改动都在既有的领域内核与纸面账簿，不新增模块、不加第二个组装根：

- `src/openalpha_cn/domain/survivor_orders.py`：买单定量与现金上限（今天唯一的定量点，`build_trade_list`，`:112-201`）；新增 `FREEZE_RATIO`/`freeze_price`、`FREEZE_SHORTFALL_POLICY`、`FundingReport`/`FundingShortfall`/`funding_report`。
- `src/openalpha_cn/backtest/paper_evening.py`：`run_evening` 把现金、参考价与顺延单交给上面的函数（`:456-465`）；`EveningResult` 带上 `funding`（`:262-276`）。
- `src/openalpha_cn/strategy_survivor_summary.py`：`paper` 段带 `funding` 块；ORDERS 块的资金行改成"09:15 的现金 + 涨停价冻结 + 今晚卖单回款在竞价之后"（`_funding`，`:1218-1259`），并按名字打印短少。
- `src/openalpha_cn/domain/survivor_book.py` / `survivor_screen.py` / `survivor_policy.py`：`illiquid` 阈值与策略的命名、文档（行为不变；`0.30` 已经是配置默认值 `survivor_book.py:72`，屏幕里已经按"通过全部静态原因的名字"切 `survivor_screen.py:80-88`）。
- `tests/`：新增一个测试文件、改写与本改动语义冲突的既有测试（清单见「文件结构」）。

**Tech Stack:** Python 3.12、标准库、pydantic v2、pytest；门禁 `uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy src scripts`，全量 `uv run pytest tests/unit` 由控制者在批次末尾串行跑一次。

**Spec / 依据:** `AGENTS.md`（硬规则 1–9、「研究诚实」）；`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §5.1（`006a` → `006b` 的顺序，`:224`）、§9 的 2026-11-01 真钱闸门行（`:359`）、2026-10-13 的 `illiquid` (a)/(b) 待决行（`:341`）；`docs/superpowers/plans/2026-11-01-p7-route-adjustment.md` §2.4（`006a` 的八项）、§2.6（真钱闸门）、§2.8 第 4–5 项（券商确认、现金定量由用户冻结）；路线图 `V2-P7-006` 行（`docs/specs/v2/openalpha-cn-v2-roadmap.md:488`）；设计 `docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md:108`（流动性门槛 = 通过静态筛选的名字里成交额前 70%）；`.superpowers/sdd/batch-3/common.md`（批次 3 的决定与格式）。

---

## 用户现实（每个任务都以此为准）

- 资金约 350,000 元，≤ 10 只，只做沪深主板。
- 用户**手工**在 09:15 按打印出来的清单下单：集合竞价只收限价单；买单挂**涨停价**、卖单挂跌停价，以唯一的开盘价成交；买单冻结约 **1.1 倍**名义资金。
- 09:15 那一刻账户里只有现金：当晚卖单与买单在**同一个**集合竞价里，券商先冻结买单的钱，卖单的回款在竞价之后才到。所以清单**不能**用"现金 + 今晚卖单回款"定量。
- 10 只等权买单按涨停价冻结约 38.5 万 > 约 35 万现金（`10 × 34,825 × 1.10 = 383,075`）：清单可能根本下不出去。纸面账簿目前假设每一张打印出来的单都下了、都按开盘价成交（`strategy_survivor_summary.py:230-233` 自己就这么写），而定量的输入是"现金 + 今晚卖单回款"（`domain/survivor_orders.py:168`），印刷时只在买单下面标一句"needs the sells above to fill first"（`strategy_survivor_summary.py:245-251`）——**提示代替了约束**。

## Global Constraints

逐字取自规格、路线调整与代码；每个任务隐含这一节。

- **每个提交写 issue ID**：本计划全部用 `V2-P7-006a`。提交标题 `feat(V2-P7-006a): …` / `fix(V2-P7-006a): …` / `test(V2-P7-006a): …` / `docs(V2-P7-006a): …`；提交信息末尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- **真钱闸门**（P7 §9 2026-11-01 行，`docs/specs/v2/…-p7-decision-chain.md:359`；路线调整 §2.6）：在 ①`006` 经用户确认并登记；②**账户成交对账输入与现金约束（含涨停价冻结）已上线**；③用户明确说"开始"——三条**全部**成立之前，**不用打印出来的清单真钱交易**；冒烟与一切输出都标"这些单不要下"；组合层面的结果多年内都检验不了（P7 §3.6）。本计划只做②里的**现金约束**一半；**账户成交对账输入不在本计划内**（路线调整 §2.4 第 5 项），所以本计划完成后真钱闸门**仍然关闭**。
- **券商假设是系统的假设，不是已确认的规则**（common.md 2026-11-07 决定；路线调整 §2.8 第 4 项）：集合竞价只收限价单、买涨停价 / 卖跌停价取单一开盘价、买单冻结约 1.1 倍名义——代码与文档必须写成"假设，向券商确认"。用户尚未向券商确认；`FREEZE_RATIO` 的取值由用户确认后改一个常量。
- **现金定量**：09:15 的现金 = 纸面账簿结算后（今晚收盘）的 `settlement.book.cash`（`backtest/paper_evening.py:463`）。这是模型，不是账户真值；真正的账户对齐靠路线调整 §2.4 第 5 项的账户成交对账（不在本计划内）。**今晚卖单的估计回款不进现金**，只在报告里作为"竞价之后才到"的信息列出。
- **涨停价冻结**：买单按 `freeze_price(reference) = (reference × FREEZE_RATIO).quantize(0.01, ROUND_HALF_UP)` 计算冻结，`FREEZE_RATIO = Decimal("1.10")`。对主板非 ST 名字，这与交易所公布的涨停价**逐分相同**（`backtest/execution.derived_price_band`，`:429-454`；`_board_limit` 主板 0.10，`:457-471`；`domain/price_limits.py:73-79` 实测 2024-06-28 主板 3,174 个名字里 3,068 个符合 "ROUND_HALF_UP 10%"，其余 104 个 5%、2 个无涨跌停）。买单永远不是 ST：入选来自幸存者，ST 是资格原因（`survivor_book.py:33-40`、`survivor_policy.py:164-168`），顺延买单在名字掉出幸存者时被取消（`paper_settlement.py` 的 `_settle_dry` 用 `survivors`）。
- **不再有第四条涨停价推导**：`backtest/execution.py:438-441` 明写 `derived_price_band` 是"the only copy"；`domain/` 不能反向导入 `backtest/`（分层，`AGENTS.md` 硬规则 2 的同一方向），所以 `FREEZE_RATIO` 是主板 10% 的**命名常量**，测试钉住它与 `derived_price_band(…, board="main", is_st=False)` 对一张价格表逐分相同；`build_trade_list` 只在 `config.boards == ("main",)` 时自行推导，否则**拒绝**并要求调用者传 `freeze_prices`。
- **`illiquid` 规则（2026-11-07 冻结为 (a)）**：通过全部静态原因（板块、上市年龄、非 ST、未停牌、因子有值）的名字里，按 20 日平均成交额升序取最薄的 `floor_share(n, ILLIQUID_DROP_FRACTION)` 只，`ILLIQUID_DROP_FRACTION = Decimal("0.30")`——即设计 §6 的"成交额前 70%"（`docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md:108`）。`illiquid` 是**强制退出**（(a)：`ILLIQUID_POLICY = "forced_exit"`），与探索期实测规则一致（P7 §9 `:341`）；(b) 的持仓滞后阈值**不实现**，等前向样本里观察到真实换手再按 `006` 的流程另行预登记。**不加绝对成交额门槛，不发明数据**：规则只用排名，单位无关（`survivor_book.py:162-163`）。
- **措辞仍是"候选"**：新增文本不得出现 `significant`、`effective`、`confirmed`、`alpha`、`outperform`、`passed`、`failed`、`proven`（`tests/unit/test_p7_diagnostics.py` 的禁止词表）；工程完成不是研究证据。
- **不联网、不读真实面板、不发请求**；`.parquet/.duckdb/.sqlite3/.db` 不提交（`scripts/verify_publication.py`）。
- **受登记绑定的路径**：`scripts/research/registry.py:93` 的 `REGISTERED_PATHS = ("src", "scripts/research", "pyproject.toml", "uv.lock")`，`scripts/daily_selection.py` 由 `also_bound` 加绑。本计划改 `src/` 里的 6 个文件（见下），不改 `scripts/research/`、`pyproject.toml`、`uv.lock`，**不改 `scripts/daily_selection.py`**（见「矛盾与发现」第 1 条）。**今天没有任何登记**（每日登记自 2026-10-08 暂停），所以没有绑定被作废；`006b` 登记的是修好之后的代码。
- **测试卫生**：worktree 里跑测试设 `PYTHONPATH=<worktree>/src`（记忆：worktree 的 venv 指向主检出的包）；**实现者只跑定向文件，整个 `tests/unit` 由控制者在批次末尾串行跑一次**（批次 1 的七个并发全量把 40 分钟变成数小时；批次 2 的 R0 裁决）；变异探针用 `PYTHONDONTWRITEBYTECODE=1`，改前 `cp` 到草稿目录、改完恢复并 `cmp` 确认逐字节一致；绝不在 `git --exec`/hook 里跑测试。
- **本计划不做**：账户成交对账输入；launchd 生成器；周报的 `illiquid` 统计改动（只读它，不改）；清晨复核之后的重新定量（复核只建议、不下单，`strategy_survivor_morning`）；任何真实运行。

## Review Focus

最可能"清单打出来却下不出去、或者用户按它下了不该下的单"的五类失败模式，每条由所在任务里的测试钉住：

1. **定量又把今晚卖单的回款算进现金**：用户在 09:15 只持有现金，卖单与买单同场竞价。→ Task 1 `test_the_proceeds_of_tonights_sells_are_not_cash_at_0915`、`test_a_buy_only_tonights_sale_could_fund_is_unfunded_and_named`。
2. **冻结按收盘价算，而不是按挂单的涨停价**：10 只等权买单按收盘价 34,825 × 10 = 348,250 ≤ 350,000 看起来可下，按涨停价 383,075 > 350,000 实际下不出去。→ Task 2 `test_a_buy_is_limited_by_the_cash_its_limit_up_price_freezes`、`test_the_freeze_price_is_the_main_boards_derived_limit_up`。
3. **冻结量与用户实际要下单的限价不是同一个数**（三处推导漂移）：打印的涨停价、内核冻结的价、`derived_price_band` 各一份。→ Task 2 `test_the_freeze_price_is_the_main_boards_derived_limit_up`（内核的价 = `derived_price_band` 逐分）+ Task 3 `test_the_evening_carries_the_funding_report_of_the_plan_it_made`（真实晚间上 `funding.frozen` = 打印重算的 349,800.00）。
4. **短少被静默截断**：一只只买到 1,200/3,400 股，清单只写"about X yuan"，用户不知道它比等权少。→ Task 3 `test_the_orders_block_reports_the_freeze_and_names_a_short_buy`、`test_the_funding_report_names_every_entry_the_cash_sized_short`；Task 5 的端到端打印测试。
5. **`illiquid` 规则只存在于周报的统计里，逐日路径另有一套**：周报数的是"被 `illiquid` 强卖的出场"（`strategy_survivor_summary.py:774-793`、`strategy_survivor_paper_report.py:987-1002`），逐日路径必须用同一个具名阈值。→ Task 4 `test_the_liquidity_cut_is_the_named_fraction_of_the_statically_eligible_population`、`test_a_holding_that_falls_below_the_liquidity_line_is_a_forced_exit`。

## 用户决定（Decisions for the user）

| # | 决定 | 状态 | 推荐 / 已定值 | 代价 / 挡住 |
|---|---|---|---|---|
| U1 | 买单冻结倍率与冻结基准 | **已作为"系统假设"确认**（common.md 2026-11-07）；**向券商确认仍未做** | `FREEZE_RATIO = Decimal("1.10")`，基准 = 信号日收盘（= 下一交易日的 `pre_close`），四舍五入到分；主板非 ST 时等于公布涨停价 | 若券商说冻结还要含手续费、或倍率不是 1.10：改一个常量 + Task 2 的价格表测试；登记前冻结 |
| U2 | 短少时"减少股数"还是"整只不买" | **未定** | **减少股数**（`FREEZE_SHORTFALL_POLICY = "reduce_lots"`）：保留 10 只名字，只把最后一只买成小仓位；整只不买是 `drop_whole`，代码里已实现，改一个常量即可 | `reduce_lots` 会让等权出现一个永久的小仓位（不自动再平衡）；`drop_whole` 会让一个缺额留到以后某晚（选择规则带盐，可能换成别的名字），但每次都是满等权；Task 2 两个参数化测试都钉住 |
| U3 | 现金定量（只用现金，不用今晚卖单回款）本身 | **由本批次 brief 采纳并实现**；P7 §2.8 第 5 项仍把它留给用户在 `006a` 登记前最终冻结 | 只用现金 | 不改：Task 1、Task 5 与运行手册回退；研究账簿的持仓路径与旧模拟不可比 |
| U4 | `illiquid` 是 (a) 强制退出还是 (b) 持仓滞后 | **未定**（P7 §9 `:341`，用户选） | **(a)** `ILLIQUID_POLICY = "forced_exit"`，与探索期实测规则一致；(b) 需要另一个事先选定的阈值并在该处重测，本计划不实现 | 选 (b)：Task 4 的强制退出测试改写成"持仓在更低的线之下才退出"，并要一个新的预登记阈值；前向账簿的换手会变 |
| U5 | 等权基准用收盘价还是涨停价定量（"先按涨停价把每只缩小到 3,100 股"方案） | **未定** | **不用**：等权目标仍按收盘价（`sizing_budget` 不变），涨停价只约束"能不能同时挂出去"；否则每晚永久留约 11% 现金 | 选"按涨停价定量"：Task 2 的 `unit_budget` 改传 `freeze_price`，10 只各 3,100 股、账面永久多现金；Task 3 的 `equal_share_quantity` 定义随之改 |

U2 与 U4 各由一个具名常量承载；U1 的常量在 Task 2；U3 是本批次的任务 1；U5 不设常量（现状即推荐）。

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `src/openalpha_cn/domain/survivor_orders.py`（改） | `FREEZE_RATIO`、`freeze_price`、`FREEZE_SHORTFALL_POLICY`；`build_trade_list` 的现金上限与顺延预留改用涨停价、去掉回款；`FundingShortfall`、`FundingReport`、`funding_report` | 1、2、3 |
| `src/openalpha_cn/backtest/paper_evening.py`（改） | `run_evening` 计算并携带 `funding`；`EveningResult.funding` | 3 |
| `src/openalpha_cn/strategy_survivor_summary.py`（改） | `paper["funding"]`；`_funding` 改为 09:15 现金 + 涨停价冻结（删 `_NEEDS_SELLS`）；按名字打印短少 | 3 |
| `src/openalpha_cn/domain/survivor_book.py`（改） | `ILLIQUID_DROP_FRACTION`、`ILLIQUID_POLICY`；配置默认值引用常量（存储形态逐字节不变） | 4 |
| `src/openalpha_cn/domain/survivor_screen.py`（改，仅 docstring） | 把逐日规则、总体与周报的口径写在一处 | 4 |
| `src/openalpha_cn/domain/survivor_policy.py`（改，仅 docstring） | 写明 (a) 是冻结的选择、强制退出的原因码 | 4 |
| `tests/unit/domain/test_survivor_orders.py`（改） | 现金定量、冻结定量、顺延预留、两种短少策略、`funding_report` | 1、2、3 |
| `tests/unit/backtest/test_paper_evening.py`（改） | 初始建仓 9×3,400 + 1×1,200（2.4b）；`EveningResult.funding` | 2、3 |
| `tests/unit/test_strategy_survivor_summary.py`（改） | 资金行、短少按名字、旧文档无 `funding` 块；真实晚间的端到端打印 | 3、5 |
| `tests/unit/domain/test_survivor_illiquid_rule.py`（新） | `illiquid` 的阈值、总体、(a) 强制退出、常量不改配置身份 | 4 |
| `docs/runbooks/daily-selection.zh-CN.md`（改） | 第 539–541 行的买 / 资金说明 | 5 |

**不改**：`scripts/daily_selection.py`、`src/openalpha_cn/backtest/paper_step.py`、`backtest/paper_state.py`、`strategy_survivor_paper_report.py`、`strategy_survivor_registration.py`、`pyproject.toml`、`uv.lock`。

## 依赖与并行

```
Task 1（现金定量）──> Task 2（涨停价冻结）──> Task 3（报告与打印）──> Task 5（端到端 + 运行手册）
Task 4（illiquid 规则）  与以上全部无关
```

- **串行**：1 → 2 → 3 都改 `domain/survivor_orders.py` 或它的调用方，必须逐个提交、逐个绿；Task 5 只在 Task 3 之后，把它的行为钉在真实晚间与运行手册上。
- **可并行**：Task 4 只动 `survivor_book.py`/`survivor_screen.py`/`survivor_policy.py`（docstring 与常量）和一个新测试文件，可与 1–3 并行（隔离 worktree 后挑拣不会冲突）。
- 每个任务一个提交，评审者可单独拒绝。

---

## Task 1：买单只用 09:15 的现金定量，今晚卖单的回款不算

**Issue:** `V2-P7-006a`。**依据:** 路线调整 §2.4 第 2 项与 §2.6；P7 §9 `:359`；`src/openalpha_cn/domain/survivor_orders.py:8-17`（Sizing 段）、`:142-168`（`proceeds` 累加与 `available = cash + proceeds - reserved`）；调用点 `src/openalpha_cn/backtest/paper_evening.py:456-465`；用户现实见上。**不在 `scripts/daily_selection.py`，也不在 `paper_step.py` / `paper_state.py`**（见「矛盾与发现」第 1 条）。

**Files:**
- Modify: `src/openalpha_cn/domain/survivor_orders.py`（`build_trade_list` 的 `proceeds` 与 `available`，模块 docstring 的 Sizing 段）
- Test: `tests/unit/domain/test_survivor_orders.py`

**Interfaces（consumes / produces；名字与类型不得改）:**

```python
# src/openalpha_cn/domain/survivor_orders.py
def build_trade_list(
    *,
    config: SurvivorBookConfig,
    state: BookState,
    decision: BookDecision,
    reference_prices: Mapping[str, Decimal],
    cash: Decimal,
    carried: tuple[TradeOrder, ...] = (),
) -> TradeList: ...
```

签名不变；`cash` 的语义收紧为"09:15 那一刻的现金"，`proceeds` 不再参与。

**Steps:**

- [ ] **1.1 先写失败的测试。** 在 `tests/unit/domain/test_survivor_orders.py` 中把 `test_the_proceeds_of_a_sell_count_towards_what_a_buy_may_spend`（`:154-166`）**改写**为两条（这是语义更正，不是删除：旧测试钉的是被用户现实推翻的规则，按批次 2 的 R2 先例在提交信息里写明）：

```python
def test_the_proceeds_of_tonights_sells_are_not_cash_at_0915() -> None:
    # Cash 5,000; A sells 300 at 10.00, which releases 3,000.00 in the same call auction the
    # buys are placed in, after the broker has frozen them. Sizing on the cash alone: 4,975.00
    # buys 300 shares of B at 12.50 (4,975 // 1,250 = 3 lots). Counting the proceeds it would
    # buy 600 (7,960 // 1,250 = 6).
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("5000"),
    )

    assert [(o.side, o.quantity) for o in plan.orders] == [("sell", 300), ("buy", 300)]


def test_a_buy_only_tonights_sale_could_fund_is_unfunded_and_named() -> None:
    # No cash at all: the 3,000.00 A will release arrives after the auction, so B is not placed
    # and is named instead of silently dropped.
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("0"),
    )

    assert [(o.side, o.ts_code) for o in plan.orders] == [("sell", "A")]
    assert plan.unfunded == ("B",)
```

- [ ] **1.2 跑，确认按预期失败。** `cd <worktree> && PYTHONPATH=$PWD/src uv run pytest tests/unit/domain/test_survivor_orders.py -k "proceeds or only_tonights_sale" -q`。期望：两条都失败——第一条得到 `("buy", 600)`，第二条得到一张 200 股的买单。

```text
FAILED tests/unit/domain/test_survivor_orders.py::test_the_proceeds_of_tonights_sells_are_not_cash_at_0915
FAILED tests/unit/domain/test_survivor_orders.py::test_a_buy_only_tonights_sale_could_fund_is_unfunded_and_named
2 failed
```

- [ ] **1.3 最小实现。** 在 `build_trade_list` 里删掉 `proceeds`（卖出循环只保留 `sells.append(...)`），并把现金上限改成：

```python
    unit_budget = config.sizing_budget(state.equity)
    # A buy still live from an earlier session will fill out of the same cash, so it is spent
    # before today's buys see any. Tonight's sales are not counted: they fill in the same
    # 09:15 auction the buys are placed in, after the broker has frozen the buys' cash.
    reserved = sum(
        (order.reference_price * order.quantity for order in carried if order.side == "buy"),
        Decimal(0),
    )
    available = max(cash - reserved, Decimal(0))
```

模块 docstring 的 Sizing 段（`:8-17`）改成："a buy is `lot_quantity` of the equal share (less the cost reserve) at the signal-day reference price, further limited by **the cash the account holds at 09:15** -- tonight's sales fill in the same auction, after the buys are frozen, so their proceeds are not this night's cash -- less what buys still live from earlier sessions will spend."

- [ ] **1.4 跑，确认通过。** 同 1.2 的命令，期望 `2 passed`。再跑整个文件：`uv run pytest tests/unit/domain/test_survivor_orders.py -q`，期望全绿（其余 30 条的算术在收盘价下不变；见「影响清单」）。

- [ ] **1.5 变异探针**（`PYTHONDONTWRITEBYTECODE=1`，改前 `cp` 到草稿目录，改完恢复并 `cmp`）：
  - M1：把 `available` 改回 `max(cash + proceeds - reserved, ...)`（并恢复 `proceeds` 累加）→ 两条新测试变红；其余绿。必须被杀。
  - M2：把 `available` 改成 `max(cash + proceeds, 0)`（忘了预留顺延单）→ `test_a_buy_still_live_from_an_earlier_session_is_paid_for_before_todays_buys`、`test_a_live_buy_that_uses_up_the_cash_leaves_nothing_for_a_new_one` 变红。必须被杀。

- [ ] **1.6 提交。** `git commit -m "fix(V2-P7-006a): a buy is sized on the cash at 09:15, not on tonight's sale proceeds"`（正文一句：旧测试 `test_the_proceeds_of_a_sell_count_towards_what_a_buy_may_spend` 被更正，因为它钉的规则被用户现实推翻；尾部 attribution）。

**影响清单（Task 1 之后、Task 2 之前必须全绿）：** 上面两条新测试；`test_a_sell_is_the_whole_holding_and_a_buy_is_whole_lots_of_an_equal_share`、`test_a_buy_is_limited_by_the_cash_that_will_be_there`、`test_each_buy_spends_from_what_the_one_before_left`、`test_a_buy_that_cannot_afford_one_lot_is_not_placed`、`test_the_first_build_is_sized_to_the_equity_not_to_the_cash_left_over`、`test_unfunded_names_keep_the_order_the_selection_rule_drew_them_in`、`test_an_entrant_whose_lot_costs_more_than_the_equal_share...`、`test_a_buy_still_live_from_an_earlier_session_is_paid_for_before_todays_buys` 在收盘价下数字不变（它们的现金都足够，回款从未成为必要条件）。

---

## Task 2：买单按涨停价冻结，顺延单按当天涨停价预留

**Issue:** `V2-P7-006a`。**依据:** 路线调整 §2.4 第 2 项、§2.8 第 4 项；P7 §9 `:359`；用户现实（1.1 倍）；`backtest/execution.py:429-471`（`derived_price_band` 与 `_board_limit`，主板 0.10）；`domain/price_limits.py:73-79`（主板 10% 的实测）；`domain/survivor_orders.py:161-192`（当前的 `available` 循环）。

**Files:**
- Modify: `src/openalpha_cn/domain/survivor_orders.py`（`FREEZE_RATIO`、`freeze_price`、`FREEZE_SHORTFALL_POLICY`、`build_trade_list` 的上限与预留、docstring）
- Test: `tests/unit/domain/test_survivor_orders.py`

**Interfaces（consumes / produces）:**

```python
# src/openalpha_cn/domain/survivor_orders.py
FREEZE_RATIO: Final[Decimal] = Decimal("1.10")
FREEZE_SHORTFALL_POLICY: Final[str] = "reduce_lots"  # or "drop_whole"


def freeze_price(reference_price: Decimal) -> Decimal: ...


def build_trade_list(
    *,
    config: SurvivorBookConfig,
    state: BookState,
    decision: BookDecision,
    reference_prices: Mapping[str, Decimal],
    cash: Decimal,
    carried: tuple[TradeOrder, ...] = (),
    freeze_prices: Mapping[str, Decimal] | None = None,
) -> TradeList: ...
```

`freeze_prices` 为 `None` 时自行按 `freeze_price` 推导，并要求 `config.boards == ("main",)`；给了就逐只使用（给了一个非主板配置的未来调用者）。`mypy` 需要 `from decimal import ROUND_HALF_UP` 与 `typing.Final`。

**Steps:**

- [ ] **2.1 先写失败的测试。** 在 `tests/unit/domain/test_survivor_orders.py` 里加（顶部补 `from openalpha_cn.backtest.execution import derived_price_band`、`from openalpha_cn.domain.survivor_orders import FREEZE_RATIO, FREEZE_SHORTFALL_POLICY, freeze_price`）：

```python
def test_a_buy_is_limited_by_the_cash_its_limit_up_price_freezes() -> None:
    # Ten equal shares of 350,000 at 10.00: 34,825.00 buys 3,400 shares each. A buy is placed at
    # the next session's limit-up, 11.00, so the broker freezes 37,400.00 for it. Nine freeze
    # 336,600.00; 13,400.00 is left, which freezes 1,200 shares of the tenth. Ten full ones
    # would freeze 374,000.00, more than the 350,000.00 the account has.
    names = tuple(f"6001{n:02d}.SH" for n in range(10))
    plan = build_trade_list(
        config=SurvivorBookConfig(seed=7),
        state=BookState(session=SESSION, initialized=False, equity=Decimal("350000")),
        decision=decision(entries=tuple((name, "INITIAL_BUILD") for name in names)),
        reference_prices=dict.fromkeys(names, Decimal("10")),
        cash=Decimal("350000"),
    )

    assert [(o.ts_code, o.quantity) for o in plan.orders] == [
        *((name, 3400) for name in names[:9]),
        (names[9], 1200),
    ]
    assert plan.unfunded == ()
    assert sum(
        (freeze_price(Decimal("10")) * o.quantity for o in plan.orders), Decimal("0")
    ) == Decimal("349800.00")
    assert sum(
        (freeze_price(Decimal("10")) * o.quantity for o in plan.orders), Decimal("0")
    ) <= Decimal("350000.00")


@pytest.mark.parametrize("close", ["9.99", "10.00", "10.03", "10.05", "1234.56"])
def test_the_freeze_price_is_the_main_boards_derived_limit_up(close: str) -> None:
    band, _ = derived_price_band(Decimal(close), board="main", is_st=False)
    assert freeze_price(Decimal(close)) == band


def test_a_normal_night_is_unchanged_when_the_freeze_does_not_bind() -> None:
    # Four equal shares of 40,000 at 12.50: the equal share buys 700 shares; the limit-up 13.75
    # freezes 9,625.00 of the 20,000.00 cash, so the cash cap never binds and the list is the one
    # the reference price alone gives.
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("20000"),
    )

    assert [o.quantity for o in plan.orders] == [300, 700]


def test_a_carried_buy_is_reserved_at_tonights_limit_up_price() -> None:
    # The live buy of 300 B is placed again tomorrow at B's limit-up, 13.75: 4,125.00 of the
    # 5,000.00 is frozen before today's buy sees any. The 875.00 left is not a lot of A at its
    # own 11.00 limit-up, so A is unfunded and named.
    plan = build_trade_list(
        config=config(),
        state=book(pending_buys=("B",)),
        decision=decision(entries=(("A", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("5000"),
        carried=(live_buy(),),
    )

    assert plan.orders == ()
    assert plan.unfunded == ("A",)


def test_a_config_that_trades_another_board_must_pass_its_own_freeze_prices() -> None:
    # The 10% is the main board's band; a config that also trades ChiNext must say what its
    # limit-up prices are rather than have the main board's ratio applied to it.
    with pytest.raises(ValueError, match="main board"):
        build_trade_list(
            config=config(boards=("main", "growth")),
            state=book(),
            decision=decision(entries=(("B", "ENTRY_REPLACEMENT"),)),
            reference_prices=PRICES,
            cash=Decimal("20000"),
        )


def test_the_drop_whole_policy_refuses_an_entry_it_cannot_freeze_in_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The decision this plan recommends against, implemented so the user's choice is one named
    # constant: with `drop_whole` the tenth name is not bought at all, where `reduce_lots`
    # buys it 1,200 shares.
    from openalpha_cn.domain import survivor_orders

    monkeypatch.setattr(survivor_orders, "FREEZE_SHORTFALL_POLICY", "drop_whole")
    names = tuple(f"6001{n:02d}.SH" for n in range(10))
    plan = build_trade_list(
        config=SurvivorBookConfig(seed=7),
        state=BookState(session=SESSION, initialized=False, equity=Decimal("350000")),
        decision=decision(entries=tuple((name, "INITIAL_BUILD") for name in names)),
        reference_prices=dict.fromkeys(names, Decimal("10")),
        cash=Decimal("350000"),
    )

    assert [o.quantity for o in plan.orders] == [3400] * 9
    assert plan.unfunded == (names[9],)
```

（`D` = `Decimal`：该文件顶部已经 `from decimal import Decimal`，测试里请写 `Decimal(...)`，不要引入新的别名；上面为了排版用 `D`，实现时逐个写成 `Decimal`。`FREEZE_RATIO`、`FREEZE_SHORTFALL_POLICY` 的 import 只为钉住常量存在，见 2.4。）

- [ ] **2.2 跑，确认按预期失败。** `uv run pytest tests/unit/domain/test_survivor_orders.py -k "freeze or normal_night or carried_buy_is_reserved or another_board or drop_whole" -q`。期望：`ImportError: cannot import name 'freeze_price'`（收集阶段），随后删除该 import 只保留行为测试时：第一条得到 10×3,400 且总额 374,000 > 350,000（断言失败）。

- [ ] **2.3 最小实现。** 在 `survivor_orders.py` 顶部：

```python
FREEZE_RATIO: Final[Decimal] = Decimal("1.10")
"""The multiple of its reference price a buy freezes when it is placed at the next session's
limit-up: the Shanghai/Shenzhen main board's 10% band (``backtest.execution._board_limit``, and
``domain/price_limits.py`` measured 3,068 of 3,174 main-board names matching ROUND_HALF_UP at 10%
on 2024-06-28). ``freeze_price`` rounds to the fen exactly as ``derived_price_band`` does, so for
a main-board non-ST name -- every buy: ST is an eligibility reason and a carried buy is cancelled
when its name leaves the survivors -- the frozen price *is* the limit price printed in ORDERS.
The system's assumption, to confirm with the broker (route adjustment 2026-11-01, section 2.8(4))."""

FREEZE_SHORTFALL_POLICY: Final[str] = "reduce_lots"
"""What a buy does when the cash freezes fewer lots than its equal share: ``reduce_lots`` takes
what fits (the entry stays, smaller); ``drop_whole`` places no order and names the entry in
``unfunded``. The user's decision (plan U2); one constant, two tested behaviours."""

_CENT: Final[Decimal] = Decimal("0.01")


def freeze_price(reference_price: Decimal) -> Decimal:
    """The limit-up price a buy is placed at, from its reference (signal-day) close."""
    if not reference_price.is_finite() or reference_price <= 0:
        raise ValueError(f"the reference price must be positive: {reference_price}")
    return (reference_price * FREEZE_RATIO).quantize(_CENT, rounding=ROUND_HALF_UP)
```

`build_trade_list` 里：

```text
    buy_prices = {
        entry.ts_code: _price_of(entry.ts_code, reference_prices) for entry in decision.entries
    }
    buy_prices.update(
        {
            order.ts_code: reference_prices.get(order.ts_code, order.reference_price)
            for order in carried
            if order.side == "buy"
        }
    )
    freeze = _freeze_prices(config, buy_prices, freeze_prices)
    ...
    reserved = sum(
        (freeze[order.ts_code] * order.quantity for order in carried if order.side == "buy"),
        Decimal(0),
    )
    available = max(cash - reserved, Decimal(0))
    ...
    for entry in decision.entries:
        price = _price_of(entry.ts_code, reference_prices)
        equal_share = lot_quantity(unit_budget, price, config.lot_size)
        quantity = min(
            equal_share,
            lot_quantity(available * (1 - config.cost_reserve_fraction), freeze[entry.ts_code],
                         config.lot_size),
        )
        if FREEZE_SHORTFALL_POLICY == "drop_whole" and quantity < equal_share:
            quantity = 0
        if quantity == 0:
            unfunded.append(entry.ts_code)
            continue
        available -= freeze[entry.ts_code] * quantity
        ...
```

和一个私有辅助：

```python
def _freeze_prices(
    config: SurvivorBookConfig,
    buy_references: Mapping[str, Decimal],
    given: Mapping[str, Decimal] | None,
) -> dict[str, Decimal]:
    """The limit-up price each buy freezes at: the caller's, or the main board's 10%."""
    if given is not None:
        missing = sorted(set(buy_references) - set(given))
        if missing:
            raise ValueError(f"no freeze price for {missing}")
        return {code: given[code] for code in buy_references}
    if tuple(config.boards) != ("main",):
        raise ValueError(
            f"the freeze price is the main board's 10% limit-up and this configuration trades "
            f"{config.boards}: pass freeze_prices, one per buy"
        )
    return {code: freeze_price(price) for code, price in buy_references.items()}
```

- [ ] **2.4 跑，确认通过。** 先跑 2.2 的命令，期望 `9 passed`（5 条新加 4 条参数化）；再跑整个文件，期望全绿。补一条钉常量的测试：

```python
def test_the_freeze_ratio_and_the_shortfall_policy_are_the_ones_the_plan_froze() -> None:
    assert FREEZE_RATIO == Decimal("1.10")
    assert FREEZE_SHORTFALL_POLICY == "reduce_lots"
    assert freeze_price(Decimal("10.00")) == Decimal("11.00")
```

- [ ] **2.4b 同一次提交里修好被这一步弄红的实盘测试。** `tests/unit/backtest/test_paper_evening.py` 的 `test_the_first_evening_after_the_opening_is_the_initial_build_of_up_to_ten_names`（`:575-615`）在 Task 2 之后必然变红（10 × 3,400 按 11.00 冻结 374,000 > 350,000）。把它的期望与 docstring 改成：十只等权各 34,825.00 在 10.00 买 3,400 股；挂 11.00 时九只冻结 336,600.00，第十只只剩 13,400.00，冻结 1,200 股（13,200.00），全部 349,800.00 ≤ 350,000.00；`unfunded == ()`。

```python
    assert [
        (o.side, o.quantity, o.reference_price, o.reason, o.execute_session, o.expires_session)
        for o in result.trade_list.orders
    ] == [
        *([("buy", 3400, D("10.00"), "INITIAL_BUILD", 2, 4)] * 9),
        ("buy", 1200, D("10.00"), "INITIAL_BUILD", 2, 4),
    ]
    assert result.trade_list.unfunded == ()
```

`result.funding` 的断言留到 Task 3（那时字段才存在）。跑 `uv run pytest tests/unit/backtest/test_paper_evening.py -q`，期望全绿；`test_strategy_survivor_book.py` 的初始建仓只断言 10 张买单（`:1203`），不依赖股数，不应变红。

- [ ] **2.5 变异探针：**
  - M1：现金上限与预留用 `price`（收盘价）而不是 `freeze[...]` → `test_a_buy_is_limited_by_the_cash_its_limit_up_price_freezes`（374,000 的总额）、`test_a_carried_buy_is_reserved_at_tonights_limit_up_price` 变红。必须被杀。
  - M2：`freeze_price` 改成 `ROUND_DOWN` → `test_the_freeze_price_is_the_main_boards_derived_limit_up` 在 `10.05`（11.055 → 11.05 ≠ 11.06）变红。必须被杀。
  - M3：把 `drop_whole` 分支删掉 → `test_the_drop_whole_policy_refuses_an_entry_it_cannot_freeze_in_full` 变红（得到 1,200 股）。必须被杀。
  - M4：`_freeze_prices` 去掉主板检查 → `test_a_config_that_trades_another_board...` 变红。必须被杀。

- [ ] **2.6 提交。** `feat(V2-P7-006a): a buy is sized and reserved at its limit-up price (1.10x the reference)`。

**影响清单（Task 2 之后必须全绿，除已改写者外）：** 下列既有测试的**期望不变**，注释里的价格应改成涨停价口径：`test_a_buy_is_limited_by_the_cash_that_will_be_there`（200）、`test_each_buy_spends_from_what_the_one_before_left`（300）、`test_a_buy_that_cannot_afford_one_lot_is_not_placed`（0）、`test_the_first_build_is_sized_to_the_equity_not_to_the_cash_left_over`（900/700/200）、`test_an_entrant_whose_lot_costs_more_than_the_equal_share...`（D 不可买）、`test_unfunded_names_keep_the_order_the_selection_rule_drew_them_in`（`("D","C")`）、`test_a_buy_limited_by_cash_still_leaves_the_cost_reserve`（按收盘价算的花费 ≤ 现金 × 0.995 由冻结 ≤ 现金蕴含）。`test_the_proceeds_of_tonights_sells_are_not_cash_at_0915`（Task 1 新增）的注释也要换成涨停价算术（数字仍是 300：4,975 // 1,375 = 3）。**domain 测试文件里必须改写期望**的只有 `test_a_buy_still_live_from_an_earlier_session_is_paid_for_before_todays_buys`（`:338-362`）：`with_live` 从 100 股变成 0 股，`without` 仍是 400 股；注释按 4,125 的预留与 875 的剩余重写。实盘测试的改写见 2.4b。

---

## Task 3：把冻结额与短少按名字报出来（`EveningResult.funding` + ORDERS 块）

**Issue:** `V2-P7-006a`。**依据:** 路线调整 §2.4 第 2 项（"the shortfall is reported by name"）；`backtest/paper_evening.py:456-483`（`run_evening` 的两个返回分支）；`strategy_survivor_summary.py:551-585`（`_paper_section`）、`:1218-1259`（`_funding`）、`:1262-1300`（`_orders_lines`）；既有打印测试 `tests/unit/test_strategy_survivor_summary.py:2267-2320`。

**Files:**
- Modify: `src/openalpha_cn/domain/survivor_orders.py`（`FundingShortfall`、`FundingReport`、`funding_report`）、`src/openalpha_cn/backtest/paper_evening.py`（`EveningResult.funding` 与 `run_evening`）、`src/openalpha_cn/strategy_survivor_summary.py`（`paper["funding"]`、`_funding`、短少行）
- Test: `tests/unit/domain/test_survivor_orders.py`、`tests/unit/backtest/test_paper_evening.py`、`tests/unit/test_strategy_survivor_summary.py`

**Interfaces（consumes / produces）:**

```python
# src/openalpha_cn/domain/survivor_orders.py
class FundingShortfall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ts_code: str = Field(min_length=1)
    equal_share_quantity: int = Field(ge=0)
    placed_quantity: int = Field(ge=0)
    missing_amount: Decimal = Field(ge=0)


class FundingReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cash: Decimal = Field(ge=0)
    sale_proceeds_pending: Decimal = Field(ge=0)  # lands after the auction, not before it
    reserved_for_carried: Decimal = Field(ge=0)
    frozen: Decimal = Field(ge=0)
    freeze_ratio: Decimal = FREEZE_RATIO
    shortfalls: tuple[FundingShortfall, ...] = ()


def funding_report(
    *,
    config: SurvivorBookConfig,
    state: BookState,
    decision: BookDecision,
    plan: TradeList,
    reference_prices: Mapping[str, Decimal],
    cash: Decimal,
    freeze_prices: Mapping[str, Decimal] | None = None,
) -> FundingReport: ...


# src/openalpha_cn/backtest/paper_evening.py
@dataclass(frozen=True, slots=True, kw_only=True)
class EveningResult:
    outcome: Outcome
    snapshot: PaperBookSnapshot
    trade_list: TradeList
    decision: BookDecision
    settlement: Settlement
    funding: FundingReport
```

**Steps:**

- [ ] **3.1 先写失败的测试。** `tests/unit/domain/test_survivor_orders.py`：

```python
def test_the_funding_report_names_every_entry_the_cash_sized_short() -> None:
    names = tuple(f"6001{n:02d}.SH" for n in range(10))
    config_ = SurvivorBookConfig(seed=7)
    state = BookState(session=SESSION, initialized=False, equity=Decimal("350000"))
    decision_ = decision(entries=tuple((name, "INITIAL_BUILD") for name in names))
    prices = dict.fromkeys(names, Decimal("10"))
    plan = build_trade_list(
        config=config_,
        state=state,
        decision=decision_,
        reference_prices=prices,
        cash=Decimal("350000"),
    )

    report = funding_report(
        config=config_,
        state=state,
        decision=decision_,
        plan=plan,
        reference_prices=prices,
        cash=Decimal("350000"),
    )

    assert (report.cash, report.frozen, report.reserved_for_carried) == (
        Decimal("350000.00"),
        Decimal("349800.00"),
        Decimal("0.00"),
    )
    assert report.sale_proceeds_pending == Decimal("0.00")
    assert report.freeze_ratio == Decimal("1.10")
    assert [item.model_dump() for item in report.shortfalls] == [
        {
            "ts_code": names[9],
            "equal_share_quantity": 3400,
            "placed_quantity": 1200,
            "missing_amount": Decimal("24200.00"),
        }
    ]


def test_the_funding_report_says_what_tonights_sells_release_and_what_the_cash_sized_short() -> (
    None
):
    # Cash 5,000 and a sale of A for 3,000: the 3,000 lands after the auction and is reported as
    # such; the buy of B is sized on the 5,000 alone, which buys 300 of the 700 the equal share
    # asks, so 400 x 13.75 = 5,500.00 is the named shortfall.
    state = book(("A", 300))
    decision_ = decision(
        exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
    )
    plan = build_trade_list(
        config=config(),
        state=state,
        decision=decision_,
        reference_prices=PRICES,
        cash=Decimal("5000"),
    )

    report = funding_report(
        config=config(),
        state=state,
        decision=decision_,
        plan=plan,
        reference_prices=PRICES,
        cash=Decimal("5000"),
    )

    assert report.sale_proceeds_pending == Decimal("3000.00")
    assert report.frozen == Decimal("4125.00")  # 300 B at the 13.75 limit-up
    assert [item.model_dump() for item in report.shortfalls] == [
        {
            "ts_code": "B",
            "equal_share_quantity": 700,
            "placed_quantity": 300,
            "missing_amount": Decimal("5500.00"),
        }
    ]
```

`tests/unit/backtest/test_paper_evening.py`：

```python
def test_the_evening_carries_the_funding_report_of_the_plan_it_made(tmp_path: Path) -> None:
    book = book_at(tmp_path, cash="350000.00")
    names = [f"6001{n:02d}.SH" for n in range(16)]

    result = evening(
        book,
        S1,
        NOTHING,
        screened(*((name, "10.00") for name in names)),
        dict.fromkeys(names, "10.00"),
    )

    assert result.funding.cash == D("350000.00")
    assert result.funding.frozen == D("349800.00")
    assert [item.ts_code for item in result.funding.shortfalls] == [
        result.decision.entries[-1].ts_code
    ]
    assert result.funding.shortfalls[0].missing_amount == D("24200.00")
```

`tests/unit/test_strategy_survivor_summary.py`（在 `NEEDS_SELLS` 一带改写）：

```python
def test_the_orders_block_reports_the_freeze_and_names_a_short_buy(s3: WrittenOff) -> None:
    document = with_orders(
        s3,
        cash="13400.00",
        orders=[open_order("buy", "600199.SH", 1200, "10.00")],
    )
    document["paper"]["funding"] = {
        "cash": "13400.00",
        "sale_proceeds_pending": "0.00",
        "reserved_for_carried": "0.00",
        "frozen": "13200.00",
        "freeze_ratio": "1.10",
        "shortfalls": [
            {
                "ts_code": "600199.SH",
                "equal_share_quantity": 3400,
                "placed_quantity": 1200,
                "missing_amount": "24200.00",
            }
        ],
    }

    lines = summary_lines(document)

    assert (
        row(
            "funding",
            "cash at 09:15 13,400.00 yuan; buys freeze 13,200.00 yuan at their limit-up prices "
            "(1.10x the reference, the main board's band); sells tonight release about 0.00 yuan "
            "after the auction, not before it",
        )
        in lines
    )
    assert (
        "  600199.SH: 1,200 of the 3,400 shares the equal share asks; 24,200.00 yuan short at "
        "the limit-up price"
    ) in lines


def test_a_document_filed_before_the_funding_block_says_the_shortfalls_were_not_recorded(
    s2: Day,
) -> None:
    document = document_of(s2)
    document["paper"].pop("funding", None)

    assert line_with(summary_lines(document), "funding").endswith(
        "; shortfalls not recorded (a plan filed before the funding block)"
    )
```

- [ ] **3.2 跑，确认按预期失败。** `uv run pytest tests/unit/domain/test_survivor_orders.py -k funding -q` 期望 `ImportError`（`funding_report` 不存在）；`uv run pytest tests/unit/test_strategy_survivor_summary.py -k funding -q` 期望资金行不匹配、`paper["funding"]` 未被打印。

- [ ] **3.3 最小实现。**
  - `survivor_orders.py`：按上面的模型与 `funding_report`。计算：`unit_budget = config.sizing_budget(state.equity)`；对每个 `entry` 取 `buy_prices`（同 Task 2 的映射）与 `freeze[...]`，`equal = lot_quantity(unit_budget, price, lot)`，`placed = {o.ts_code: o.quantity for o in plan.orders if o.side == "buy"}.get(code, 0)`，`missing = (equal - placed) * freeze` 只在 `equal > placed` 时收进 `shortfalls`（保持 `decision.entries` 的顺序）；`frozen = Σ freeze × qty`（`plan.orders` 的买单）；`reserved_for_carried = Σ freeze × qty`（`plan.carried` 的买单，价格同 Task 2）；`sale_proceeds_pending = Σ reference × qty`（`plan.orders` 的卖单）。`freeze_prices` 的处理复用 `_freeze_prices`。
  - `paper_evening.py`：`run_evening` 在 `build_trade_list` 之后调一次 `funding_report(...)`，两个返回分支都带上；`EveningResult` 加字段。
  - `strategy_survivor_summary.py` 的模块 docstring（`:100-123` 的 ORDERS 段）与 `_orders_lines` 的 docstring：把"a buy the cash before the sales does not cover is marked: place it after the sales fill (I1)"改成"the kernel sizes every buy on the cash at 09:15 and freezes it at its limit-up price; the block prints the frozen total and names every buy the cash sized short, and a buy past the cash is a defect of the plan, not something to place after the sales"。
  - `strategy_survivor_summary.py`：`_paper_section` 的返回 dict 加 `"funding": evening.funding.model_dump(mode="json")`；`_funding` 改成"按买单在内核顺序（顺延在前）以各自的涨停价累加，`running > cash` 的买单下面标 `_UNCOVERED`（新文本见下）"；删 `_NEEDS_SELLS`。资金行的**确切文本**：`_row("funding", f"cash at 09:15 {_money(cash)} yuan; buys freeze {_money(at_limit)} yuan at their limit-up prices (1.10x the reference, the main board's band); sells tonight release about {_money(released)} yuan after the auction, not before it")`；`at_limit` 有一张买单算不出涨停价时省略括号里的总额（沿用现有规则）。`_orders_lines` 在资金行之后按 `paper.get("funding")` 的 `shortfalls` 每只打印一行 `f"  {ts_code}: {placed:,} of the {equal:,} shares the equal share asks; {_money(missing)} yuan short at the limit-up price"`，`placed == 0` 时 `f"  {ts_code}: no order; the {equal:,} shares the equal share asks are {_money(missing)} yuan short at the limit-up price"`；没有 `funding` 块时在资金行末尾追加 `"; shortfalls not recorded (a plan filed before the funding block)"`。`_UNCOVERED` 新文本：`"    is not covered by the cash at 09:15 (the plan asks for more than the account holds; do not place it)"`。
  - `_funding` 的 docstring 重写：内核只用 09:15 的现金、按涨停价冻结，打印重算同一个总额作为交叉核对；今晚卖单的回款在竞价之后才到。

- [ ] **3.4 跑，确认通过。** `uv run pytest tests/unit/domain/test_survivor_orders.py tests/unit/backtest/test_paper_evening.py tests/unit/test_strategy_survivor_summary.py -q`。期望全绿；其中 `test_a_buy_the_cash_before_the_sells_does_not_cover_says_to_place_it_after_they_fill`（`:2267`）与 `test_the_running_total_funds_the_carried_buys_first_as_the_kernel_does`（`:2300`）**必须改写**：删掉 `NEEDS_SELLS` 断言，改成 `_UNCOVERED`（新文本）与新的资金行文本、`at_limit` 总额；两个测试的名字也改（`..._says_it_is_not_covered_at_0915`）。**参数化的 `marked` 列不是换个字符串**：它现在必须是"累计超过现金"的每一张买单（旧列是"超过现金 + 回款"），所以多数用例的期望集合会变大，按 `cash` 而不是 `cash + released` 重算。

- [ ] **3.5 变异探针：**
  - M1：`funding_report` 只对 `placed == 0` 报短少（漏掉被缩小到 1,200 的那只）→ 3.1 的第一条与 summary 的短少行变红。必须被杀。
  - M2：`FundingReport.freeze_ratio` 写死 `Decimal("1")` → 3.1 的 `freeze_ratio` 断言变红。必须被杀。
  - M3：`_funding` 不读 `paper["funding"]`（只打印现金行）→ summary 的短少行变红。必须被杀。
  - M4：`_paper_section` 不放 `funding` 键 → `test_the_evening_carries...` 与 summary 的两条变红。必须被杀。

- [ ] **3.6 提交。** `feat(V2-P7-006a): the evening reports the frozen cash and names every buy the cash sized short`。

**旧日志兼容：** `result.paper.funding` 是新增的 JSON 键，不是 pydantic 契约行；结果文档由 `strategy_survivor_summary.evening_document` 以 dict 组装（`scripts/daily_selection.py:4741-4781`），读取端逐键取用，所以磁盘上更早的日志（没有 `funding`）仍可读，打印时说"not recorded"（3.1 的第二条测试）。**不升 schema、不加迁移**；这一条要在提交正文里写明。

---

## Task 4：`illiquid` 的具名阈值与 (a) 强制退出

**Issue:** `V2-P7-006a`。**依据:** 路线调整 §2.4 第 3 项；P7 §9 `:341`（(a)/(b) 待用户选）；设计 `docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md:108`（通过静态筛选的名字里成交额前 70%）；`domain/survivor_book.py:72`（`liquidity_drop_fraction=0.30` 已是默认）、`survivor_screen.py:10-16, 80-88`（切的总体与顺序）、`survivor_policy.py:157-168`（资格丧失 = 强制退出）；周报的统计 `strategy_survivor_summary.py:774-793`、`strategy_survivor_paper_report.py:987-1002`。**行为不变**：本任务只把已有的规则命名、写清、钉住，并冻结 (a)。

**Files:**
- Modify: `src/openalpha_cn/domain/survivor_book.py`（`ILLIQUID_DROP_FRACTION`、`ILLIQUID_POLICY`、配置默认值引用常量）、`src/openalpha_cn/domain/survivor_screen.py`（docstring）、`src/openalpha_cn/domain/survivor_policy.py`（docstring）
- Test: `tests/unit/domain/test_survivor_illiquid_rule.py`（新）

**Interfaces（consumes / produces）:**

```python
# src/openalpha_cn/domain/survivor_book.py
ILLIQUID_DROP_FRACTION: Final[Decimal] = Decimal("0.30")
ILLIQUID_POLICY: Final[str] = "forced_exit"


class SurvivorBookConfig(BaseModel):
    ...
    liquidity_drop_fraction: Decimal = Field(default=ILLIQUID_DROP_FRACTION, ge=0, lt=1)
```

**Steps:**

- [ ] **4.1 先写失败的测试。** 新文件 `tests/unit/domain/test_survivor_illiquid_rule.py`：

```python
"""The daily `illiquid` rule (`V2-P7-006a`): one named threshold, the same one the weekly report
counts, and option (a) -- a holding below it is a forced exit.

The rule is a *rank* rule, not an absolute turnover band: the design fixes "the top 70% by 20-day
average traded amount among the names that pass every static reason", and `SecurityFacts.avg_amount`
is only ordered (`domain/survivor_book.py:162-163`, "only its order matters"), so no unit or data
beyond the panel's own is invented here.
"""

from decimal import Decimal

from openalpha_cn.domain.survivor_book import (
    ILLIQUID_DROP_FRACTION,
    ILLIQUID_POLICY,
    SecurityFacts,
    SurvivorBookConfig,
    floor_share,
)
from openalpha_cn.domain.survivor_policy import BookState, Holding, decide_book
from openalpha_cn.domain.survivor_screen import screen

D = Decimal


def _fact(code: str, amount: int, *, suspended: bool = False) -> SecurityFacts:
    return SecurityFacts(
        ts_code=code,
        board="main",
        listed_sessions=1000,
        special_treatment=False,
        suspended=suspended,
        close=D("10.00"),
        avg_amount=D(amount),
        oriented_values=(0.5,),
    )


def _config(**overrides: object) -> SurvivorBookConfig:
    settings: dict[str, object] = {
        "seed": 1,
        "composite_factors": ("f/v1",),
    }
    settings.update(overrides)
    return SurvivorBookConfig(**settings)  # type: ignore[arg-type]


def test_the_liquidity_cut_is_the_named_fraction_of_the_statically_eligible_population() -> None:
    # 40 statically eligible names with amounts 1..40, plus a halted name with no amount at all.
    # The cut is the bottom floor(40 x 0.30) = 12 taken over the eligible 40 only; the halted
    # name is not in the population, so its zero amount cannot enter the cut.
    n = 40
    facts = [_fact(f"6001{i:02d}.SH", i) for i in range(1, n + 1)]
    halted = _fact("600999.SH", 0, suspended=True)
    result = screen([*facts, halted], _config())

    illiquid = [v.ts_code for v in result.verdicts if "illiquid" in v.reasons]
    assert len(illiquid) == floor_share(n, ILLIQUID_DROP_FRACTION) == 12
    assert set(illiquid) == {fact.ts_code for fact in facts[:12]}
    assert halted.ts_code not in illiquid


def test_the_named_fraction_is_what_the_shipped_configuration_already_stores() -> None:
    # The constant names the value the content-addressed configuration already had: re-typing the
    # default through it must not change a configuration's identity.
    default = SurvivorBookConfig(seed=1)
    explicit = SurvivorBookConfig(seed=1, liquidity_drop_fraction=Decimal("0.30"))

    assert default.liquidity_drop_fraction == ILLIQUID_DROP_FRACTION
    assert default == explicit
    assert default.config_id == explicit.config_id


def test_a_holding_that_falls_below_the_liquidity_line_is_a_forced_exit() -> None:
    # The held name is the thinnest of the eligible population tonight. The screen gives it
    # `illiquid` and nothing else; the policy sells the whole holding at the next open, with
    # `EXIT_ELIGIBILITY_LOST` -- option (a), the rule the exploratory period measured.
    thin = "600199.SH"
    facts = [_fact(f"6001{i:02d}.SH", i) for i in range(1, 11)] + [_fact(thin, 0)]
    config = _config(holding_count=3, loser_decile_count=0)
    screened = screen(facts, config)
    verdict = next(v for v in screened.verdicts if v.ts_code == thin)
    assert verdict.reasons == ("illiquid",)

    state = BookState(
        session=10,
        initialized=True,
        equity=D("350000"),
        holdings=(Holding(ts_code=thin, quantity=100),),
    )
    result = decide_book(config=config, screen=screened, state=state)

    assert [(exit_.ts_code, exit_.reason) for exit_ in result.exits] == [
        (thin, "EXIT_ELIGIBILITY_LOST")
    ]
    assert ILLIQUID_POLICY == "forced_exit"
```

（`loser_decile_count` 只接受整数；`SurvivorBookConfig` 的 `holding_count` 必须 ≥ 1；`BookState.session` 是整数序号。`screen` 对 `facts` 按 `ts_code` 排序输出，`_fact` 的 11 只里最薄的 3 只是 `thin`（0）、`600101`（1）、`600102`（2），所以 `thin` 确实带 `illiquid`。）

- [ ] **4.2 跑，确认按预期失败。** `uv run pytest tests/unit/domain/test_survivor_illiquid_rule.py -q` 期望 `ImportError: cannot import name 'ILLIQUID_DROP_FRACTION'`。

- [ ] **4.3 最小实现。**
  - `survivor_book.py`：加两个常量（docstring 里写设计 §6 的"前 70%"、切的总体、周报的 `illiquid_exits` 统计、以及"(b) 是持仓滞后阈值，未实现：等前向样本里观察到真实换手再按 `006` 的流程另行预登记"），并把 `liquidity_drop_fraction` 的 `default=Decimal("0.30")` 改成 `default=ILLIQUID_DROP_FRACTION`。存储形态仍是 `"0.30"`，`config_id` 不变（4.1 的第二条钉住）。
  - `survivor_screen.py`：在模块 docstring 的第 2 点后补一句"this is the `illiquid` rule `V2-P7-006a` names (`ILLIQUID_DROP_FRACTION`); the weekly report counts exits whose verdict carries it (`strategy_survivor_summary._illiquid_exits`), so the daily and weekly readings are the same cut."；行为不动。
  - `survivor_policy.py`：在 `FREEZE_COMPATIBLE_REASONS` 的注释后补一句"`illiquid` is a forced exit -- `V2-P7-006a` froze option (a) (`ILLIQUID_POLICY`); the holding-hysteresis alternative (b) is not implemented."；行为不动。

- [ ] **4.4 跑，确认通过。** `uv run pytest tests/unit/domain/test_survivor_illiquid_rule.py tests/unit/domain/test_survivor_screen.py tests/unit/domain/test_survivor_book_lifecycle.py tests/unit/test_strategy_survivor_summary.py tests/unit/test_strategy_survivor_paper_report.py -q`，期望全绿。

- [ ] **4.5 变异探针：**
  - M1：`ILLIQUID_DROP_FRACTION` 改成 `Decimal("0.50")` → 4.1 第一条（12 只变 20 只）与第二条（配置身份）变红。必须被杀。
  - M2：`screen` 的候选总体改成"全部名字"（去掉 `if not reasons[...]` 过滤）→ 4.1 第一条在一个含停牌名字的夹具上变红；若 4.1 的夹具全静态合格则 M2 存活——实现时在第一条夹具里放一只 `suspended=True` 的名字，并把期望的切出名单按"通过静态原因的 40 只"算。必须被杀。
  - M3：`decide_book` 的 `forced` 排除 `illiquid`（把 `illiquid` 加进 `FREEZE_COMPATIBLE_REASONS`）→ 4.1 第三条变红。必须被杀。

- [ ] **4.6 提交。** `feat(V2-P7-006a): the illiquid rule is a named fraction, and a holding below it is a forced exit`。

---

## Task 5：真实晚间的端到端打印与运行手册

**Issue:** `V2-P7-006a`。**依据:** 用户现实（10 只等权 × 1.1 > 350,000）；`tests/unit/backtest/test_paper_evening.py:575-615`（初始建仓测试，Task 2 的 2.4b 已改期望）；`docs/runbooks/daily-selection.zh-CN.md:536, 539-542`（买、资金、账户分叉的说明）；`strategy_survivor_summary.py:222-251`（限价假设与旧 `_NEEDS_SELLS` 的文本来源）。

**Files:**
- Modify: `tests/unit/test_strategy_survivor_summary.py`（端到端打印测试）
- Modify: `docs/runbooks/daily-selection.zh-CN.md`（第 539、541、542 行与「已知局限」；第 536 行的限价假设保持）
- Test: 同上（只改测试与文档，不改 `src`）

**Interfaces:** 无新接口；本任务把 Task 1–3 的行为在一个真实 `run_evening` 上钉住，并把文本写进运行手册。

**Steps:**

- [ ] **5.1 先写一条端到端打印测试（会红）。** 在 `tests/unit/test_strategy_survivor_summary.py` 里，用**真实的** evening 走一遍 35 万的初始建仓，并断言 ORDERS 块按名字报出短少（这一步需要 Task 1–3 已合入；它在 Task 3 之前收集失败是预期的）：

```python
def test_the_initial_build_of_ten_equal_weight_buys_names_the_one_the_limit_up_freezes_short(
    tmp_path: Path,
) -> None:
    names = [f"6001{n:02d}.SH" for n in range(16)]
    screen_ = evenings.screened(*((name, "10.00") for name in names))
    prices = dict.fromkeys(names, "10.00")
    result = evenings.evening(
        evenings.book_at(tmp_path, cash="350000.00"), S1, evenings.NOTHING, screen_, prices
    )
    day = Day(
        result,
        facts_of(S1, dict.fromkeys(names, D("10.00"))),
        screen_,
        S1,
        1,
        AS_OF_S1,
    )

    lines = summary_lines(document_of(day))

    assert line_with(lines, "funding").startswith(
        row("funding", "cash at 09:15 350,000.00 yuan; buys freeze 349,800.00 yuan")
    )
    short = result.decision.entries[-1].ts_code
    assert (
        f"  {short}: 1,200 of the 3,400 shares the equal share asks; 24,200.00 yuan short at "
        "the limit-up price"
    ) in lines
    assert "  the paper book assumes every order above is placed and fills at the open" in (
        " ".join(lines)
    )
```

- [ ] **5.2 跑，确认按预期失败。** `uv run pytest tests/unit/test_strategy_survivor_summary.py -k initial_build_of_ten -q`。在 Task 3 合入后此测试**应当直接通过**（它只是把 Task 3 的行为钉在真实晚间上）；若失败，说明 Task 3 的 `paper["funding"]` 没有接到真实 `EveningResult`，回到 Task 3 修。这一步的"失败"是文档：`grep -n "cash before the sells" docs/runbooks/daily-selection.zh-CN.md` 仍命中第 541 行。

- [ ] **5.3 更新运行手册。**
  - 第 539 行（买）：补一句"股数是**在 09:15 用现金、按下一交易日涨停价能冻结的整手数**；可能少于等权（34,825 元）本可买到的股数，少多少在资金行按名字写出来。"
  - 第 541 行（资金）整段改写为：资金行 `cash at 09:15 <X> yuan; buys freeze <Y> yuan at their limit-up prices (1.10x the reference, the main board's band); sells tonight release about <Z> yuan after the auction, not before it`；内核只用 09:15 的现金定量，今晚卖单的回款在同一个集合竞价里、在买单被冻结**之后**才到，所以不计入；买单按涨停价冻结，10 只等权在 35 万上挂不出去第十只时，清单把它写成更小的股数（或按 `FREEZE_SHORTFALL_POLICY` 整只不买）并在资金行下面按名字打印短少；`is not covered by the cash at 09:15 … do not place it` 只应该出现在清单与账户现金不一致的缺陷场景，正常晚间不会出现。
  - 第 542 行（账户与纸面账簿的分叉）保持；在末尾加一句"清单可执行不等于账户一定与账簿一致：现金是账簿的模型，账户对齐靠 `006a` 的账户成交对账（尚未做）。"——这句同时重述真钱闸门。
  - 在「已知局限」一节加一条：**本次改动之前打开的 P7 纸面账簿不能沿用**（规则变了，已结算的会话不能再逐字节复现其计划），必须重开；本仓库没有任何登记、没有真实晚间，所以没有要迁移的账簿。

- [ ] **5.4 跑定向文件（本批次实现者最后一次）。** `uv run pytest tests/unit/domain/test_survivor_orders.py tests/unit/domain/test_survivor_illiquid_rule.py tests/unit/backtest/test_paper_evening.py tests/unit/test_strategy_survivor_book.py tests/unit/test_strategy_survivor_summary.py tests/unit/test_strategy_survivor_paper_report.py -q`。期望全绿；`test_strategy_survivor_book.py` 的初始建仓只断言 10 张买单（`:1203`），不依赖股数。

- [ ] **5.5 变异探针：**
  - M1：把 `run_evening` 的 `funding=...` 换成 `funding=FundingReport(cash=..., frozen=Decimal(0), ...)` → 5.1 的 `funding:` 行与短少行变红。必须被杀。
  - M2：把 `build_trade_list` 的 `available` 改成 `cash + 3_000` → 5.1 的 `1,200 of the 3,400` 变红（十只全是 3,400）。必须被杀。

- [ ] **5.6 提交。** `docs(V2-P7-006a): the runbook says the list is sized on the cash at 09:15 and frozen at the limit-up`（测试与文档同一个提交；提交正文写"工程完成不是研究证据，措辞仍是候选"）。

**控制者在批次末尾**串行跑一次整个 `tests/unit`（R0 裁决）；这是唯一一次全量。

---

## 矛盾与发现（起草时核对代码的结果）

1. **brief 猜的代码位置不对。** 定量不在 `scripts/daily_selection.py` 的幸存者日、也不在 `backtest/paper_step.py` / `paper_state.py`：它是 `src/openalpha_cn/domain/survivor_orders.py:112-201` 的 `build_trade_list`，由 `src/openalpha_cn/backtest/paper_evening.py:458-465` 调用；`paper_step.py` 只决定"卖单先、买单后"的执行顺序（`:55-64`），`paper_state.py` 只存快照，`daily_selection.py:5079-5359` 的幸存者日只把 facts / screen 交给 `paper_book.evening`（现金来自账簿的结算，`paper_evening.py:463`）。所以 `scripts/daily_selection.py` **不需要改**，本计划不动它（它仍是受绑定路径，列在「受绑定代码」里）。
2. **打印层已经在说"可能要用卖单回款"，内核却在这么定量。** `strategy_survivor_summary._funding`（`:1218-1259`）的 docstring 明写"the kernel sizes tonight's buys on the cash plus the estimated proceeds of tonight's sales … at 09:15 the account holds the cash alone"，然后只在买单下面标一句 `needs the sells above to fill first`（`:245-251`）。也就是说系统**知道**清单在 09:15 可能下不出去，但把它当警告而不是约束。本计划把这个约束移进内核，打印层改成交叉核对。
3. **"1.1 倍"不是新的数。** `stk_limit` 的主板 10% 已在 `domain/price_limits.py:73-79` 实测（3,068/3,174 符合 ROUND_HALF_UP 10%），`backtest/execution.derived_price_band`（`:429-454`）已经是"下一交易日涨停价"的唯一推导，打印层也用它（`strategy_survivor_summary:1135-1153`）。本计划没有发明倍率，只是把同一个价从"打印用"升为"定量用"，并把 1.10 命名为常量；`_board_limit` 的注释明说这是"the only copy"，不能再抄第四份。
4. **`illiquid` 在逐日路径里已经存在。** 路线图的 `006a` 项写作"`illiquid` 规则 (a)/(b)"，读起来像逐日路径没有规则；实际上 `survivor_screen.screen`（`:80-88`）从 `V2-P7-005` 起就按 `liquidity_drop_fraction=0.30` 切，且用户 2026-10-13 已采纳"在通过静态筛选的名字里切"（P7 §9 `:339`）。缺的不是机制，是**名字、文档与 (a)/(b) 的冻结**。Task 4 因此只命名 + 钉住，不改行为；把它写成"新规则"会与代码不符。
5. **`006a` 的八项里本计划只覆盖两项半。** 路线调整 §2.4 还有 `021`、`025`、launchd 生成器、账户成交对账输入的落点、中期停止规则、延续规则 `023`、主期限。其中账户成交对账是真钱闸门的第②条的一半，**不在本计划内**；所以本计划完成后真钱闸门仍关闭（见 Global Constraints）。
6. **"账户在 09:15 的现金"只能是模型。** 纸面账簿的 `cash` 是账簿结算后的数，不是券商账户的真值；用户可能在白天手工交易、分红、费用。Task 1 的 `cash` 语义收紧到"09:15 的现金"，但真值对齐要靠路线调整 §2.4 第 5 项。
7. **旧账簿不能在新规则下重新推导。** `run_evening` 对已结算的会话会用 `_refuse_unless_reproduced` 重新推导并与持有的快照逐字节比较（`paper_evening.py:467-469`）；规则一变，旧快照的挂单会被判为"没有复现"而拒绝。本仓库里没有任何登记、没有跑过真实晚间，所以没有要迁移的账簿；如果用户在别处有一个 P7 纸面账簿，它必须重开（写进提交正文与运行手册的已知局限）。
8. **预算/请求：零。** 本计划不发请求；所有夹具在测试时构造（`MarketBar` 字面量、生成的面板）。
