# P7 Step 0 幸存者持仓框架 — 实施计划 2（适配器、纸面成交、每日入口）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把计划 1 做完的纯 Python 内核接到真实面板上：每个信号日产出一行行"资格事实"、纸面账本按开盘价真实成交并处理除权除息、先结算后决定、产出并落盘每日交易清单、登记预测、周报扩展——也就是 Step 0 的 `V2-P7-016`、`017`、`009`、`008`、`018`。

**Architecture:** 内核（`domain/survivor_*.py`）保持纯净不动；面板侧适配器放在 `src/openalpha_cn/survivor_facts.py`（面板 → `SecurityFacts`，可用数值库）；纸面成交的步骤构建器放在 `src/openalpha_cn/backtest/paper_step.py`（`paper.py` 邻近）；每日编排放在 `src/openalpha_cn/strategy_survivor.py`（T4 的推荐位置），由 `scripts/daily_selection.py` 按登记配置顶层键 `kind` 分派，P6 路径不变。每日顺序（T5）：**先结算上一日的订单与订单生命周期，再做持仓决定与交易清单。**

**Tech Stack:** Python 3.12+、pydantic v2、DuckDB / pyarrow（只在面板侧）、pytest；门禁 `ruff`、`mypy src scripts`、`lint-imports`。

**Spec / 依据:** 设计 `docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md`（§4.2、§6、§7、§12 的回写）；P7 文档 `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md`（§6 决策、§9 修订记录——**尤其"计划 2 的前置条件"与"计划 2 的新增要求"两行**）；验证报告 `docs/research/p7-step0-validation/` 的 T3（ST 与次新）、T4（引擎与登记）、T5（纸面步骤）、T6-T7（耗时与请求数）、T8（仅主板重测）；计划 1 `docs/superpowers/plans/2026-10-13-p7-step0-survivor-book.md`。

**滚动计划：** Part A（`V2-P7-016`）与 Part B（`V2-P7-017`）在这里写到可以派发的程度；Part C（`V2-P7-009`）、D（`V2-P7-008`）、E（`V2-P7-018`）依赖 A、B 的实际接口，**在 A、B 合入后、派发之前补全细节**（标注"待补全"的部分不得派发）。每次补全都在文末"修订记录"记一行。

## Global Constraints

逐字取自 `AGENTS.md`、设计文档与 P7 文档，每个任务隐含这一节。

- 每个改动在分支、提交和 PR 里写 issue ID（`V2-P7-016`、`017`、`009`、`008`、`018`）；分支名 `feat/v2-p7-016-eligibility-facts` 之类，一个 issue 一个分支。
- **可交易板块仅沪深主板**（决定 D12）：创业板、科创板、北交所都排除；板块是配置项，默认 `("main",)`；板块分类只经 `backtest.execution.security_board`。
- 两个数据平面分离：面板数据（价格、基本面、日历、股票池、行业、复权因子）只进分区面板存储，**绝不进** `ParquetEvidenceStore`（硬规则 1）。
- `domain/` 不引入数值或基础设施库（硬规则 2；`lint-imports` 的 `domain-purity`）；`DataFrame`/`ndarray` 只在 `panel/`、`factors/`、`models/` 与本计划点名的适配器模块里。**不得为了让类型检查通过而放宽这条闸门。**
- 契约变更要迁移（硬规则 3）：本计划新增的契约（`trade-order/v1`、`trade-list/v1` 的落盘形式、日志行形状）都是**新契约**；**不得**给已存储行的已有契约加字段。若发现必须改，停下来报告。
- 单一组装根（硬规则 5）：新增的存储与登记器经已有的组装根装配，不新造第二个。
- 面板夹具在测试时生成（硬规则 6）：测试里**不得**提交 `.parquet`、`.duckdb`、`.sqlite3`、`.db`；复用仓库已有的合成面板夹具构造器（用 `git grep` 在 `tests/` 里找 `test_strategy_view`、`test_panel_factors`、`test_daily_selection` 怎么搭面板，**不要复制一份**）。
- 台账引用要解析（硬规则 7）：新能力加台账行，`acceptance_test` 绑定真实 node id；移动测试文件是三件产物的改动（硬规则 9）——本计划只新增测试文件，不移动。
- 可复现声明要真（硬规则 8）：`code_commit` 来自 git、`config_digest` 是算出来的、`random_seed` 贯穿所有随机组件。
- **前视守卫**：任何一个信号日的事实、因子值、成交，都不得读到该信号日（含收盘后的合法部分）之后才可见的数据；每个读取函数都要有一条"信号日之后的数据被塞进夹具也读不到"的测试。
- 先写会失败的行为测试，再写实现；每个任务汇报 RED（命令 + 失败输出 + 为什么预期失败）与 GREEN；保持仓库可运行；一次只做一个 issue。
- 永不提交凭据、运行期数据库、用户输出或未授权的原始数据集；不读 `.env`。
- 研究期窗口 2017-01-01…2021-10-31 之外的数据（验证期与已花掉的保留窗口 2024-01-02…2026-09-24）**不得**用于任何研究性结论；工程测试用合成夹具，不读研究库。
- 措辞一律"候选"；工程完成不等于研究有效。
- 已登记代码绑定覆盖 `src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`、`scripts/daily_selection.py`：本计划改这些会使现行登记失效——这是预期的，每日登记自 2026-10-08 暂停，新配置在 `V2-P7-006` 一次性重新登记。
- 全量套件**串行**运行；绝不在 `git --exec`/hook 里跑测试；测试运行期间不要编辑被测检出目录。
- 合并方式只能是 merge commit 或 fast-forward，**不得 squash / rebase**；通过本地发布闸门之前不发布、不强推。
- 提交信息末尾加：`Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`。
- 在隔离 worktree 里：先 `git merge --ff-only <特性分支>`（worktree 的基点是旧 main）；venv 里的包指向主检出，测试要导入自己 worktree 的 `src`——用 `-o pythonpath=src`，必要时在 worktree 根建 `.venv` 软链指向主仓库 venv，完成前删除、绝不提交；`.superpowers/` 是草稿目录，不入库。

## Review Focus

规格隐含、但任务测试不一定演练的五类输入或失败模式（每条的测试加到负责该代码的任务里）：

1. **停牌且当日没有日线行的证券**（每个信号日 46–52 只）：事实行必须仍然产出（沿用最近收盘价、`suspended=True`），否则持仓里的停牌股会被内核当作"未评估"而强制卖出。→ A1、A2。
2. **名称史缺口**（`namechange` 读窗口不够、语料里没有当日名称）：三态 `ST / ORDINARY / UNKNOWN` 的全函数，`UNKNOWN` 按"普通"处理并单列计数，**不得**抛错拒绝整个 run。→ A1。
3. **面板数据故障让筛选残缺**：空的或覆盖率过低的筛选会让内核卖出一切它看不到的持仓——适配器要有覆盖率守卫，低于阈值具名拒绝。→ A2。
4. **除权除息**：每年 9.6%–17.4% 的股票出现复权比 ≥ 1.10 的事件；纸面账本若不处理，10 只的账簿一年里 64%–85% 会碰上一次虚假大回撤。→ B1。
5. **不能成交的订单**：涨停买不进、跌停卖不出、停牌、现金不足都被 `PaperPortfolio` 整单拒绝并写入账本；顺延单必须换 `order_id`，没有日线行的订单必须被移出步骤并记为"未尝试"，而不是让整个 `advance` 抛错。→ B1、B2。

## 文件结构（拟）

| 文件 | 职责 | 任务 |
|---|---|---|
| `src/openalpha_cn/survivor_facts.py` | 面板 → `SecurityFacts`：ST 三态、上市年龄、因子窄读、每只上市证券一行事实、覆盖率守卫 | A1–A3 |
| `src/openalpha_cn/backtest/paper_step.py` | 纯函数步骤构建器：开盘折叠、复权比缩放、卖前买后、无 bar 的订单记为"未尝试"、每次尝试一个 id、收盘重估 | B1 |
| `src/openalpha_cn/backtest/paper.py`（改） | `PaperPortfolio` 增加非破坏性的 `execution` 参数（成本与滑点注入） | B2 |
| `src/openalpha_cn/backtest/paper_state.py` | 每日状态快照与纸面账簿单独的账本实例 | B3 |
| `src/openalpha_cn/strategy_survivor.py` | 每日编排：先结算后决定、清单落盘、摘要、登记 | D |
| `scripts/daily_selection.py`（改） | 按 `kind` 分派；`namechange` 进每日目标；补建上限；清晨复核模式 | A3、D |
| `tests/unit/test_survivor_facts*.py`、`tests/unit/backtest/test_paper_step*.py` 等 | 对应的测试 | 各任务 |

---

## Part A — `V2-P7-016` 面板 → 资格事实适配器与每日抓取

### Task A1：纯函数核心——ST 三态、上市年龄、事实行的装配

**Issue:** `V2-P7-016`。**依据:** T3 §7（读取接口与 8 条约束）、设计 §4.2。

**Files:**
- Create: `src/openalpha_cn/survivor_facts.py`（本任务只放纯函数与数据类，不读面板）
- Create: `tests/unit/test_survivor_facts_core.py`

**先读（动手前，把你的理解写进报告第一节，不要先写代码）：**
- `docs/research/p7-step0-validation/T3-st-and-new-listings.md` §1（来源盘点）、§7（决定与 8 条约束）；
- `src/openalpha_cn/domain/name_history.py`（`NameHistory.announced_by`、`risk_warning_on`、`pending_at`、`RiskWarning`、`NameHistoryHorizonError` 的语义）；
- `src/openalpha_cn/domain/stock_universe.py`（`listed_on`、`delist_date` 的开闭语义）、`src/openalpha_cn/domain/trading_calendar.py`（`trading_days_between`）；
- `src/openalpha_cn/domain/survivor_book.py`（`SecurityFacts` 的字段与校验）与 `src/openalpha_cn/backtest/execution.py::security_board`。

**Interfaces（本任务产出，后续任务依赖，名字与类型不得改）：**
- `NameStatus = Literal["st", "delisting", "ordinary", "unknown"]`。
- `name_status(*, history: NameHistory | None, band_is_five_percent: bool | None, day: date) -> NameStatus`：**全函数**——名称史能判就按名称（`risk_warning_on` 与 `pending_at`，已公告、将在下一交易日生效为 ST 的算 `st`；`delisting_process` 单列为 `delisting`）；名称史没有当日记录或没有这只证券（`NameHistoryHorizonError` 的情形）时：主板上若当日涨跌停带宽是 5%（`band_is_five_percent=True`）判 `st`（并集规则），否则 `unknown`；`unknown` 不抛错。
- `listing_age_sessions(*, listed_on: date | None, calendar_first: date, day: date, sessions_between: Callable[[date, date], int]) -> int | None`：开市日口径含上市日；`listed_on is None`（"无上市行"）返回 `None`；`listed_on` 早于 `calendar_first` 时用 `calendar_first` 截断，**并**要求调用方断言日历起点至少比信号日早 `min_listing_sessions` 个交易日（本函数在截断且 `sessions_between(calendar_first, day) < 250` 时抛具名错误，不静默给出偏小的年龄）。
- `FactsNotes`（冻结数据类）：`name_unknown_assumed_ordinary: int`、`suspended_without_bar: int`、`no_listing_row: int`、`delisting: int`、`st: int`。
- `assemble_fact(*, ts_code: str, board: Board, name_status: NameStatus, listed_sessions: int | None, suspended: bool, close: Decimal, avg_amount: Decimal, oriented_values: Sequence[float | None]) -> SecurityFacts`：`unknown` 名称状态按"普通"（`special_treatment=False`）；`delisting` 与 `st` 都 `special_treatment=True`；`listed_sessions is None` 按 0（即 `listing_too_recent`，"无上市行判不合格"）；其余字段原样。

**要钉住的行为（每条一个测试，期望值手算，写在测试注释里）：**
1. `name_status`：普通 / ST / `*ST` / PT / 退市整理 / 已公告次日生效为 ST（`pending_at`）各一条；名称史缺当日 + 主板 5% 带宽 → `st`；缺当日 + 10% 带宽 → `unknown`；缺这只证券 → `unknown`；`unknown` 不抛。
2. `listing_age_sessions`：上市当日 = 1（含上市日）；上市后第 250 个交易日 = 250；`listed_on=None` → `None`；上市早于日历起点且日历起点不足 250 日 → 具名错误；足够 → 用起点截断给出确切数。
3. `assemble_fact`：每个字段的映射；`unknown` → `special_treatment=False`；`delisting` → `True`；`listed_sessions=None` → 0；`oriented_values` 含 `None` 原样保留；无效输入（价格 ≤ 0）由 `SecurityFacts` 的校验拒绝。
4. 同一输入逐字节相同的输出；输入顺序无关（打乱后装配同一批事实得到同一集合）。

**Steps:**
- [ ] **Step 1:** 读上面的材料，在报告第一节写出：每个接口依赖的现有 API（`file:line`）、你对"全函数三态"的理解、任何与上面 Interfaces 冲突之处。**有冲突先停下来报告（NEEDS_CONTEXT），不要自己改接口。**
- [ ] **Step 2:** 写失败的测试 `tests/unit/test_survivor_facts_core.py`（先 `name_status`，再 `listing_age_sessions`，再 `assemble_fact`；构造 `NameHistory` 用仓库已有的构造器或测试夹具，**不要**手写绕过校验的假对象）。运行，确认因 `survivor_facts` 不存在而失败。
- [ ] **Step 3:** 写最小实现，逐个测试变绿。
- [ ] **Step 4:** `ruff check`、`ruff format --check`、`mypy src/openalpha_cn/survivor_facts.py`、`lint-imports`，以及 `tests/unit/test_survivor_facts_core.py`。
- [ ] **Step 5:** 提交（显式列路径）：`feat(V2-P7-016): ST three-state, listing age and the fact row, as pure functions`。

### Task A2：面板读取——每只上市证券一行事实，含停牌无日线者；覆盖率守卫；前视守卫

**Issue:** `V2-P7-016`。**依据:** T3 §7、T6/T7 §0（窄读、整年读的代价）、P7 文档 §9"计划 2 的前置条件"(1)(3)。

**Files:**
- Modify: `src/openalpha_cn/survivor_facts.py`
- Create: `tests/unit/test_survivor_facts_panel.py`（面板夹具在测试时生成）

**先读：** T3 §1–§3、§7；T6-T7 §3（窄读 0.13 s 对整年读 25 s）；`src/openalpha_cn/panel_ingest.py` 的 `load_name_histories`（约 1584）、`load_stock_universe`（约 1113）、`load_trading_calendar`（约 940）、`load_daily_bars`（约 3358）、`load_price_limits`（约 3792）、`load_suspensions`（约 3635）；`src/openalpha_cn/panel_factors.py::load_processed_factor_observations`（约 9541）及其窄读的做法（T6/T7 说"窄读同样的值只要 0.13 s"——**找到那条窄读路径并复用，不要再写一条**）；`src/openalpha_cn/strategy_view.py` 里怎么按信号日取因子值与方向（`_PanelDays`、`lower_is_better` 之类）；仓库里已有的合成面板测试夹具（`git grep` 找）。

**Interfaces（本任务产出）：**
- `FactsDay`（冻结数据类）：`session: date`、`facts: tuple[SecurityFacts, ...]`（按 `ts_code` 升序，每只**上市**证券恰好一行，含当日无日线的停牌证券）、`notes: FactsNotes`、`universe_size: int`、`scored_size: int`。
- `build_facts_day(*, store: PanelStore, session: date, as_of: datetime, config: SurvivorBookConfig, factor_ids: Sequence[str]) -> FactsDay`：只读；`as_of` 之后可见的数据不可见（沿用面板的点时可见性机制，**不要**自己写时间过滤）；`factor_ids` 的方向从因子定义读出（不要硬编码"越小越好"）：`oriented_values[i]` = 方向调整后的 `processed` 档值（越大越好），缺值为 `None`。
- 覆盖率守卫：`scored_size / universe_size` 低于 `min_coverage`（`build_facts_day` 的关键字参数，默认 0.5，**由调用方写入登记配置**）时抛 `FactsCoverageError`（具名，消息里写出两个数与阈值）——不是返回残缺的事实让内核卖出一切。

**要钉住的行为：**
1. 夹具：一个小型合成面板（30–50 只证券、60+ 个交易日、含一只停牌证券的若干日、一只 ST、一只次新、一只创业板、一只科创板），逐只手算期望的事实行（板块、上市年龄、ST、停牌、收盘价、20 日平均成交额、四个方向调整后的因子值）。
2. 停牌且当日无日线行的证券：事实行存在，`suspended=True`，`close` 沿用最近一个有日线的收盘价，`avg_amount` 用其最近 20 个**有成交的**交易日（与设计一致：沿用最近值），因子值沿用最近值或 `None`——**选一种并在报告里写明依据，测试钉住**。
3. 板块：`security_board` 的分类原样进入 `board`；`config.boards` 不含的板块照样产出事实行（由内核给 `board_not_allowed`），不在适配器里丢掉。
4. 前视：把信号日之后的日线、名称史、因子行塞进夹具，读到的事实不变。
5. 窄读与整年读的因子值逐位相同（对夹具里的一个因子年）。
6. 覆盖率守卫：夹具里把大部分证券的因子值删掉 → `FactsCoverageError`，消息含两个数；阈值边界（恰好等于阈值不拒绝）。
7. 同一输入逐字节相同；`PYTHONHASHSEED` 不同的子进程结果相同（沿用计划 1 的做法：子进程把被测树的 `src` 放进 `PYTHONPATH`）。

**Steps:** 同 A1 的五步（Step 1 的报告要列出每个读取函数的 `file:line` 与它的可见性语义）；提交信息：`feat(V2-P7-016): one fact row for every listed security, with a coverage guard and a look-ahead guard`。

### Task A3：每日抓取清单与补建上限

**Issue:** `V2-P7-016`。**依据:** T3 §7 第 7 条、T6/T7 §0（`namechange` 不在每日目标；补建没有上限）。

**Files:**
- Modify: `scripts/daily_selection.py`（`BASE_TARGETS` 一带、`update_panel`）与必要处的 `src/`
- Create/Modify: 对应测试（先找 `tests/` 里现有的 `daily_selection` 测试如何断言抓取目标与请求数）

**先读：** `scripts/daily_selection.py` 的 `BASE_TARGETS`（约 216）、`FULL_UPDATE_TARGETS`（约 227）、`targets_for`（约 953）、`update_panel`（约 1902）、`MAX_CATCH_UP_SESSIONS`（只管因子档位）；T6-T7 §1（请求数的逐项计数）。

**要钉住的行为：**
1. 幸存者形态的每日目标集合 = 现行 `BASE_TARGETS` + `namechange`（P6 形态的目标集合**逐位不变**）；请求数名义稳态 16 次的计数测试（沿用现有的"请求计数"夹具）。
2. 补建上限：面板更新要补的交易日数超过上限（默认 10，写入登记配置）时具名拒绝并说明补救命令，而不是静默发出 > 100 次请求；上限以内照旧。
3. P6 形态的所有现有测试照旧通过（不得修改它们的期望）。

**Steps:** 同上；提交信息：`feat(V2-P7-016): the daily fetch includes namechange and refuses an unbounded catch-up`。**本任务触及 `scripts/daily_selection.py`**，P6 路径的回归由现有测试证明——改动前后各跑一次相关测试文件并写进报告。

---

## Part B — `V2-P7-017` 纸面成交的真实性

### Task B1：纯函数步骤构建器（开盘成交、复权比缩放、卖前买后、"未尝试"、每次尝试一个 id）

**Issue:** `V2-P7-017`。**依据:** T5 全文（尤其 §0、§4–§6）。

**Files:**
- Create: `src/openalpha_cn/backtest/paper_step.py`
- Create: `tests/unit/backtest/test_paper_step.py`

**先读：** `docs/research/p7-step0-validation/T5-paper-step-builder.md` 全文；`src/openalpha_cn/backtest/paper.py`（`PaperPortfolio.advance`）、`multi_day.py`（`PortfolioBacktestStep`，约 88）、`portfolio.py`（`PortfolioOrder`、`PortfolioState`、`PortfolioLimits`）、`execution.py`（`AShareExecutionPolicy`、`security_board`）、`strategy_view.py` 里组装 `MarketBar` 的函数（`_PanelDays.quote` 一带）；计划 1 的 `domain/survivor_orders.py::TradeOrder`。

**Interfaces（拟，实现者在 Step 1 核对并如有冲突先报告）：**
- `AttemptedOrder`（冻结数据类）：`order: TradeOrder`、`attempt_id: str`（= `f"{order.order_id}@{session.isoformat()}"`）、`outcome: Literal["submitted", "not_attempted"]`、`reason: str | None`。
- `build_paper_step(*, session: date, orders: Sequence[TradeOrder], bars: Mapping[str, PaperBar], adjustment_ratio: Mapping[str, Decimal], benchmark_close: Decimal) -> PaperStepPlan`，其中 `PaperBar` 含开盘、收盘、涨停价、跌停价、停牌标记；`PaperStepPlan` = `(step: PortfolioBacktestStep, attempts: tuple[AttemptedOrder, ...])`。
- 行为：订单在 `session` 的开盘价成交（开盘折叠：把 `MarketBar` 的 close 位放开盘价，收盘重估由状态推进完成，见 B3）；**卖单排在买单前**；没有 bar 的订单（整日停牌）不进步骤，记为 `not_attempted`（`reason="no bar"`）；每次尝试用新 id；`adjustment_ratio` 把前一日持仓成本与数量折算成当日口径（拆股送转按比例增加数量、除息按比例调整成本）——具体口径写进报告并钉住测试；`benchmark_close` 原样进步骤（取中证 500 当日收盘，由调用方提供）。

**要钉住的行为（手算 3–4 日夹具）：** 开盘成交价；涨停买不进 / 跌停卖不出 / 停牌 → `submitted` 但被账本整单拒绝并记录（或按 T5 的结论在构建器里提前判为 `not_attempted`——**以 T5 §4–§5 的实测行为为准**，在报告里写明，测试钉住）；一次 1.15 的复权事件**不产生虚假回撤**（对照：不缩放时权益出现 15% 的假跌，缩放后不出现）；顺延单第二次尝试 id 与第一次不同、账本不报冲突；卖前买后的顺序；二次构建同一输入得到同一计划。

**Steps / 提交:** `feat(V2-P7-017): a pure step builder -- open fills, adjustment scaling, sells before buys, an id per attempt`。

### Task B2：`PaperPortfolio` 的成本与滑点注入（非破坏性）

**Issue:** `V2-P7-017`。**依据:** T5 §0（默认成本远低于协议口径，一笔 3 万元的买单 9.30 对 37.50）、协议 `p6-protocol.md` §3。

**Files:** Modify `src/openalpha_cn/backtest/paper.py`（及必要处的 `execution.py`）；Create/Modify `tests/unit/backtest/test_paper_costs.py`。

**要钉住的行为：** `PaperPortfolio(..., execution=None)`：缺省时**行为与改动前逐位相同**（对现有的 `paper` 测试全部通过、不改它们的期望）；传入协议成本（佣金万 2.5 最低 5 元、卖出印花税千 0.5、滑点单边 10bp 折入 `transfer_fee_rate`，按 T5 的做法）时，一笔 3 万元买单与一笔卖单的费用**逐分**等于 P6 的账簿（对照 `strategy_backtest` 的同一口径）；整手与现金约束照旧；运行期仍禁止联网（`refusing_outward_calls`）。

**Steps / 提交:** `feat(V2-P7-017): inject the protocol costs into the paper ledger without changing its default`。

### Task B3：每日状态快照与纸面账簿单独的账本实例

**Issue:** `V2-P7-017`。**依据:** T5 §0、P7 §9。

**Files:** Create `src/openalpha_cn/backtest/paper_state.py`、`tests/unit/backtest/test_paper_state.py`；在组装根里给纸面账簿**单独建一个账本实例**（不与回测账本共用；找到 `runtime/composition` 里账本的装配处，沿用）。

**要钉住的行为：** 每日状态快照（持仓、现金、在途订单、冷却、已结算到的交易日）以内容寻址的 JSON 落盘，原子写、写后可读回逐字节相同；同一交易日重复保存不改文件（幂等）；读回一个旧快照 + 重放后续各日 = 直接得到的新快照；损坏或缺失的快照按名拒绝，不静默从零开始；账本实例与回测账本互不可见。

**Steps / 提交:** `feat(V2-P7-017): a daily state snapshot and a ledger of the paper book's own`。

---

## Part C — `V2-P7-009` 纸面账本的每日推进（待补全）

依赖 B。目标：`settle_session(...)`——读取状态快照 → 取上一日的订单 → 用 B1 构建步骤 → `PaperPortfolio.advance`（B2 的成本）→ 用计划 1 的 `advance_orders` / `update_cooldowns` 结算订单生命周期 → 得到今天的持仓、在途买单 / 卖单、冷却 → 写快照。要钉住：先结算后决定的顺序；停牌的名字的单保持在途；3 日后买单过期且触发冷却；二次运行零写入；跨年；缺数据按名拒绝。**在 A、B 合入后补全接口与测试细节，再派发。**

## Part D — `V2-P7-008` 每日入口 `strategy_survivor`（待补全）

依赖 A、C。目标：登记配置按顶层键 `kind` 分派（P6 配置零影响）；每日流程 = 更新面板 → 构建四个因子的 `processed` 档 → `build_facts_day` → `settle_session` → `screen` → `decide_book` → `build_trade_list` → 清单与订单落盘（契约身份先定：要不要含账簿身份、`TradeList` 里是否记录"已入选但因现金不足没下单"的名字——**落盘前由用户在 P7 §9 的待定项里选**）→ 摘要与日志（持仓、候补、临近退出、冻结、清单、账本权益）→ 预测登记 `survivor_book_composite/v1`（horizon `20d`，范围与账本权益无关，先于次日 09:15）；清晨复核模式；幸存者版的迟到记录核查与输入溯源（只增量）。**在 A、C 合入后补全。**

## Part E — `V2-P7-018` 周报扩展（待补全）

依赖 D。目标：预测层评估（合成分的前向十分位 / IC，h=20 与 h=5）、账本记录、对可投资等权与中证 500 的相对收益、`illiquid` / 冻结 / 未成交的统计；逐份写明"账本权益不是证据""N=10 时单年结果主要由运气决定"；措辞"候选"。**在 D 合入后补全。**

---

## 收尾（控制者，不派给子代理）

1. 每个任务合入特性分支后做**任务评审**（规格符合性 + 代码质量），Critical/Important 派修复子代理，Minor 记进进度账本。
2. Part A、B 合入后：整分支评审（最强模型）+ **完整闸门串行**（`tests/unit tests/integration tests/replay tests/contract` + `ruff` + `format` + `mypy src scripts` + `lint-imports` + `build_feature_coverage --check` + `verify_publication`）。
3. 之后按 `superpowers:finishing-a-development-branch` 处理，**把合并方式交给用户**；不自行 push。
4. **`V2-P7-006`（协议与预登记）是研究决策检查点**：在 `002`、`003`、`004`、`008`、`016`、`018` 完成后，由用户确认假设、窗口、判据、家族、分数映射与冻结的参数，**才**重新预登记并恢复每日登记。期望数字取 T8 的一组（+6.1–6.3%、TE 11.7%、单年跑输 27%）。

## 修订记录

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-10-14 | 立项：Part A、B 写到可派发；C、D、E 待补全 | 滚动计划：C、D、E 依赖 A、B 的实际接口 |
