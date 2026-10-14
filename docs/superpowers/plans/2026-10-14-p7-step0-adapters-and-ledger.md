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
- **可交易板块仅沪深主板**（决定 D12）：创业板、科创板、北交所都排除；板块是配置项，默认 `("main",)`；板块分类经 `backtest.execution.security_board`，**再叠加适配器里的主板前缀白名单**（沪 600/601/603/605、深 000/001/002/003，前缀与交易所配对；`security_board` 对不认识的前缀回落到主板，所以不认识的前缀在适配器里不给事实行并具名计数）。
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

**实现后的接口（2026-10-14，A1–A3 与三轮修订之后；以代码为准，下面各任务里的 Interfaces 是立项时的草稿）：** `build_facts_day(*, store, session, as_of, config, factor_ids, transform, exchange, min_coverage=0.5, max_carry_sessions=250, held=()) -> FactsDay`。`transform` 与 `exchange` 必填；`held` 是持仓名（不设结转上界，拼写必须是 `NNNNNN.SH|SZ|BJ`，否则 `MalformedHeldCodeError`）。`FactsDay` 带 `facts`（每只上市证券一行，按代码排序）、按允许板块算的 `universe_size`/`scored_size`、`FactsNotes`（含 `no_bar_within_carry`、`no_listing_row`、`not_listed_with_bar`、`unrecognised_board`/`unrecognised_board_codes`、`st_by_band` 等分项计数）、`held` 与 `held_status`（`row`/`no_bar_in_reach`/`not_listed`/`not_in_registry`/`board_unrecognised`）。错误都是 `SurvivorFactsError` 的子类：`FactsCoverageError`、`MissingFactorBuildError`、`StaleRegistryError`、`MalformedHeldCodeError`。登记簿读不晚于信号年的最新已存生命周期年（信号年没有上市/退市事件时不拒绝；回退年份的取数必须不早于该会话的 16:30 发布时刻，否则 `StaleRegistryError`）；因子值只取信号日 16:30 那一次构建的被采纳值；`MAIN_BOARD_PREFIXES` 是前缀与交易所配对的白名单。每日抓取侧：`targets_for_survivor`、`SurvivorPanelUpdate`、`update_panel(..., survivor=...)`、`panel_catch_up_bound`；上一年尾部没补齐按名拒绝、补建上限按会话数计。**这一模块在 Part C 之前要改名为 `strategy_survivor_facts`**（`strategy_` 家族，守卫 `test_every_top_level_module_is_a_declared_leaf_or_a_member_of_a_discovered_family` 要求读面板的顶层模块加入某个研究平面家族并有 `RESEARCH_PLANE_SEAM_IMPORTS`/`RESEARCH_PLANE_DATASETS` 两行）；Part C 的 `survivor_market` 相应用 `strategy_survivor_market` 的名字。

### Task A1：纯函数核心——ST 三态、上市年龄、事实行的装配

**Issue:** `V2-P7-016`。**依据:** T3 §7（读取接口与 8 条约束）、设计 §4.2。

**Files:**
- Create: `src/openalpha_cn/survivor_facts.py`（本任务只放纯函数与数据类，不读面板）
- Create: `unit/test_survivor_facts_core.py`

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
- [ ] **Step 2:** 写失败的测试 `unit/test_survivor_facts_core.py`（先 `name_status`，再 `listing_age_sessions`，再 `assemble_fact`；构造 `NameHistory` 用仓库已有的构造器或测试夹具，**不要**手写绕过校验的假对象）。运行，确认因 `survivor_facts` 不存在而失败。
- [ ] **Step 3:** 写最小实现，逐个测试变绿。
- [ ] **Step 4:** `ruff check`、`ruff format --check`、`mypy src/openalpha_cn/survivor_facts.py`、`lint-imports`，以及 `unit/test_survivor_facts_core.py`。
- [ ] **Step 5:** 提交（显式列路径）：`feat(V2-P7-016): ST three-state, listing age and the fact row, as pure functions`。

### Task A2：面板读取——每只上市证券一行事实，含停牌无日线者；覆盖率守卫；前视守卫

**Issue:** `V2-P7-016`。**依据:** T3 §7、T6/T7 §0（窄读、整年读的代价）、P7 文档 §9"计划 2 的前置条件"(1)(3)。

**Files:**
- Modify: `src/openalpha_cn/survivor_facts.py`
- Create: `unit/test_survivor_facts_panel.py`（面板夹具在测试时生成）

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
- Create: `unit/backtest/test_paper_step.py`

**先读：** `docs/research/p7-step0-validation/T5-paper-step-builder.md` 全文；`src/openalpha_cn/backtest/paper.py`（`PaperPortfolio.advance`）、`multi_day.py`（`PortfolioBacktestStep`，约 88）、`portfolio.py`（`PortfolioOrder`、`PortfolioState`、`PortfolioLimits`）、`execution.py`（`AShareExecutionPolicy`、`security_board`）、`strategy_view.py` 里组装 `MarketBar` 的函数（`_PanelDays.quote` 一带）；计划 1 的 `domain/survivor_orders.py::TradeOrder`。

**Interfaces（实现后更正，2026-10-14；以 `backtest/paper_step.py` 为准）：**
- `AttemptedOrder`（冻结数据类）：`order: TradeOrder`、`attempt_id: str`（= `f"{order.order_id}@{session.isoformat()}"`，`attempt_id(order, session)`）、`outcome: Literal["submitted", "not_attempted"]`、`reason: str | None`。
- `build_paper_step(*, session: date, session_index: int, orders: Sequence[TradeOrder], bars: Mapping[str, MarketBar], adjustment_ratio: Mapping[str, Decimal], benchmark_close: Decimal) -> PaperStepPlan`。`bars` 直接用已有的 `MarketBar`（要带交易所公布的涨跌停价，没有就拒绝）；`session_index` 是会话序号，用来拒绝未到期的单（`execute_session > s`）与已过期的买单；`adjustment_ratio` 必须覆盖每个卖出的持仓名（没动过的显式给 1，缺了就按名拒绝）。`PaperStepPlan` = `(step: PortfolioBacktestStep, attempts: tuple[AttemptedOrder, ...])`。
- `remark_at_close(state, *, bars, adjustment_ratio) -> PortfolioState`：每个有 bar 的持仓名必须在比值表里；收盘重估由它完成。
- 行为：订单在 `session` 的开盘价成交（`execution.at_the_open` 把 bar 的高低收折成开盘价）；**卖单排在买单前，买单按信号会话先后**；没有 bar 的订单不进步骤、记为 `not_attempted`（`reason="no bar"`）；每次尝试用新 id；复权口径：把每个持仓名的整根 bar 按 `adj_t / adj_entry` 缩放，股数保持买入时的口径（拆股送转精确；现金分红按"再投资"处理、不扣红利税，手续费按含股息的名义额收——写进报告，已钉住）；`benchmark_close` 原样进步骤。

**要钉住的行为（手算 3–4 日夹具）：** 开盘成交价；涨停买不进 / 跌停卖不出 / 停牌 → `submitted` 但被账本整单拒绝并记录（或按 T5 的结论在构建器里提前判为 `not_attempted`——**以 T5 §4–§5 的实测行为为准**，在报告里写明，测试钉住）；一次 1.15 的复权事件**不产生虚假回撤**（对照：不缩放时权益出现 15% 的假跌，缩放后不出现）；顺延单第二次尝试 id 与第一次不同、账本不报冲突；卖前买后的顺序；二次构建同一输入得到同一计划。

**Steps / 提交:** `feat(V2-P7-017): a pure step builder -- open fills, adjustment scaling, sells before buys, an id per attempt`。

### Task B2：`PaperPortfolio` 的成本与滑点注入（非破坏性）

**Issue:** `V2-P7-017`。**依据:** T5 §0（默认成本远低于协议口径，一笔 3 万元的买单 9.30 对 37.50）、协议 `p6-protocol.md` §3。

**Files:** Modify `src/openalpha_cn/backtest/paper.py`（及必要处的 `execution.py`）；Create/Modify `unit/backtest/test_paper_costs.py`。

**要钉住的行为：** `PaperPortfolio(..., execution=None)`：缺省时**行为与改动前逐位相同**（对现有的 `paper` 测试全部通过、不改它们的期望）；传入协议成本（佣金万 2.5 最低 5 元、卖出印花税千 0.5、滑点单边 10bp 折入 `transfer_fee_rate`，按 T5 的做法）时，一笔 3 万元买单与一笔卖单的费用**逐分**等于 P6 的账簿（对照 `strategy_backtest` 的同一口径）；整手与现金约束照旧；运行期仍禁止联网（`refusing_outward_calls`）。

**Steps / 提交:** `feat(V2-P7-017): inject the protocol costs into the paper ledger without changing its default`。

### Task B3：每日状态快照与纸面账簿单独的账本实例

**Issue:** `V2-P7-017`。**依据:** T5 §0、P7 §9。

**Files:** Create `src/openalpha_cn/backtest/paper_state.py`、`unit/backtest/test_paper_state.py`；在组装根里给纸面账簿**单独建一个账本实例**（不与回测账本共用；找到 `runtime/composition` 里账本的装配处，沿用）。

**要钉住的行为：** 每日状态快照（持仓、现金、在途订单、冷却、已结算到的交易日）以内容寻址的 JSON 落盘，原子写、写后可读回逐字节相同；同一交易日重复保存不改文件（幂等）；读回一个旧快照 + 重放后续各日 = 直接得到的新快照；损坏或缺失的快照按名拒绝，不静默从零开始；账本实例与回测账本互不可见。

**Steps / 提交:** `feat(V2-P7-017): a daily state snapshot and a ledger of the paper book's own`。

---

## Part C — `V2-P7-009` 纸面账本的每日推进

依赖 A、B。三个任务：C1（面板读取：会话序号、当日 bar、复权因子、基准收盘）与 C2（纯结算核心）互不依赖、文件不相交，可并行；C3（一晚的事务、开账、`BookState`）依赖 C2。**每晚的顺序**（计划 1 的模拟已钉住"先结算后决定"）：事实 → `screen` → **结算当日**（用上一日快照 + 当日 bar；`advance_orders` 要用当日的幸存者集合，所以结算排在 `screen` 之后）→ `decide_book`（用结算后的 `BookState`）→ `build_trade_list` → 写快照（含今晚新计划的订单）。

**会话序号。** `TradeOrder` 数的是整数会话序号。序号 = 登记配置里 `adapter.epoch_session`（一个已收盘的开市日，序号 0）之后到该日的开市日个数；它只由日历和配置决定，与面板里日历最早存到哪一年无关（回填更早的年份不会让已落盘的快照错位）。开账 = 在 `epoch_session` 当晚存一份"什么都没结算过"的开账快照（`last_settled_session=None`，现金 = 配置里的本金，无持仓）；**第一次真正的晚间运行是 `epoch_session` 的下一个开市日**，那一晚上一份快照是开账快照，`initialized=False`，`decide_book` 出 `INITIAL_BUILD`。之后 `initialized` = 上一份快照的 `last_settled_session is not None`。

### Task C1：结算输入的面板读取与会话序号

**Issue:** `V2-P7-009`。**依据:** T5 §0、§2（复权）、§7.1（不许第四处 bar 组装）。

**Files:** Create `src/openalpha_cn/survivor_market.py`、`unit/test_survivor_market.py`。

**先读：** `shortlist_view._bars_on`（约 1730–1830 行，一个会话的 `MarketBar` 与交易所公布的涨跌停价；`_risk_warned_on` 的拒绝语义）、`factor_view._PanelInputs.market_bar`、`strategy_view._PanelDays.quote`——**这是已有的第三处 bar 组装，T5 不许有第四处**：本任务把其中一处提成共享的公共函数并让原调用方改用它（P6 的行为与测试期望逐位不变），或者直接调用已有的公共入口；做哪一种先在报告里写明理由。`domain/adjustment.py`（`ADJ_FACTOR_DATASET`、读取函数）、`domain/index_prices.py`、`survivor_facts.py` 里的 `A2` 读取方式（`_read`/`PanelStorageError` 的处理、`as_of` 前视守卫）。

**Interfaces（拟，实现者核对）：**
- `SessionOrdinals(calendar: TradingCalendar, epoch: date)`：`index_of(day: date) -> int`（`day` 必须是开市日且不早于 `epoch`，否则按名拒绝）、`day_of(index: int) -> date`；`epoch` 不是开市日按名拒绝。
- `settlement_market(store: PanelStore, *, session: date, as_of: datetime, names: Collection[str], calendar: TradingCalendar, benchmark: str = "000905.SH") -> SessionMarket`，其中 `SessionMarket` = 冻结数据类 `(bars: Mapping[str, MarketBar], adj_factor: Mapping[str, Decimal], benchmark_close: Decimal)`。`bars` 只含 `names` 里**当日有日线且有交易所公布涨跌停价**的名字（没有的不进字典，由 `build_paper_step` 记为 `not_attempted`；停牌名字有日线行但标 `suspended`）；`adj_factor` 含 `names` 里当日有复权因子的名字；`benchmark_close` 是基准指数当日收盘（缺失按名拒绝，不用前值）。

**要钉住的行为（面板夹具在测试里生成，不入库）：** 序号的边界（epoch 当日 = 0、跨周末与节假日、跨年、非开市日与早于 epoch 被拒、回填更早年份的日历不改序号——用两份日历对照）；`as_of` 前视守卫（当晚 16:30 之后才可见的数据读不到，读更晚的 `as_of` 得到逐位相同的字节）；没有公布涨跌停价的名字不进 `bars`；停牌名字的 bar 标 `suspended` 且开盘价折叠后仍被账本整单拒绝（与 `build_paper_step` 联测）；一只名字当日没有复权因子 → 不进 `adj_factor`（调用方负责拒绝持仓名缺比值）；基准缺失被拒；`names` 之外的证券一概不读不建（只构建被问到的名字）；P6 的 `test_shortlist_view`/`test_factor_view`/`test_strategy_view` 期望不变。

**Steps / 提交:** `refactor(V2-P7-009): one bar assembly for the three faces`（如要提取）与 `feat(V2-P7-009): a session's bars, adjustment factors and benchmark close for the paper book, and the session ordinals`。

### Task C2：结算核心（纯函数 + 账簿推进）

**Issue:** `V2-P7-009`。**依据:** 计划 1 的 `advance_orders`/`update_cooldowns`，B1 的 `build_paper_step`/`remark_at_close`，B3 的 `PaperBookSnapshot`，T5 §4–§6。

**Files:** Create `src/openalpha_cn/backtest/paper_settlement.py`、`unit/backtest/test_paper_settlement.py`；pyproject 的 import-linter 合约清单里加这个模块（照 B1/B3 的做法）。

**Interfaces（拟）：**
- `settle_session(*, previous: PaperBookSnapshot, session: date, session_index: int, market: SessionMarket, survivors: Collection[str], portfolio: PaperPortfolio, config: SurvivorBookConfig, observed_on: date) -> Settlement`。`market` 的类型由 C1 定义——**为并行起见，C2 在自己的文件里声明一个结构相同的 `Protocol`/数据类 `SessionMarketLike`（三个属性：`bars`、`adj_factor`、`benchmark_close`），C1 的 `SessionMarket` 结构上满足它，双方都不 import 对方**。
- `Settlement`（冻结数据类）：`book: PortfolioState`（已用真实收盘重估，`as_of == session`）、`entry_factors`、`live: tuple[PendingOrder, ...]`（`attempts` 已加一）、`cooldowns`、`filled/expired/cancelled: tuple[TradeOrder, ...]`、`attempts: tuple[AttemptedOrder, ...]`、`result: PaperSessionResult`；`with_plan(trade_list: TradeList) -> PaperBookSnapshot`：把今晚新计划的订单作为 `attempts=0` 的待办并入、`last_settled_session=session_index`，得到当晚要存的快照（`TradeList.carried` 必须与 `live` 一致，否则按名拒绝）。
- `book_state(settlement_or_snapshot, *, config, equity?) -> BookState`：持仓数量 = 各持仓唯一一手的 `quantity`，`pending_buys/sells` 来自待办订单，`equity` = 现金 + 持仓按收盘价的市值，`initialized` 的推导见本 Part 开头。

**行为：** (1) 到期订单 = 上一份快照里 `execute_session <= session_index` 的全部待办（含上次没成交的顺延单）；全部交给 `build_paper_step`（含 `session_index`），没有 bar 的由它记为 `not_attempted`；(2) 比值表 = 每个持仓名与每个到期卖单名的 `adj_factor[session] / entry_adj`：**持仓名当日没有复权因子 → 按名拒绝当日**（不用 1、不沿用前值），不动的名字显式给 1；(3) `PaperPortfolio.advance(state=previous.book, session=plan.step, observed_on)`，再用 `remark_at_close` 以真实收盘重估；(4) 成交的订单 = 账本转移里 `status == "filled"` 的尝试 id 对回原订单 id；`advance_orders(..., filled_order_ids, survivors)` 得 live/expired/cancelled；(5) 买单成交的名字：`entry_adj` = 成交会话的 `adj_factor`；卖单成交的名字：去掉；(6) `update_cooldowns(..., session=session_index, sold=成交的卖出名, expired_buys=过期买单的名, config)`；(7) 每个到期且仍在 live 的订单 `attempts += 1`（`PaperBookSnapshot` 会校验这条恒等式）；(8) 不改上一份快照、不写任何存储（存储与账本之外的副作用只有 `portfolio.advance` 对**注入的**账本的追加，这一点由 C3 管）。

**要钉住的行为（手算 3–5 日夹具，每条一个测试）：** 开盘成交 + 收盘重估；先卖后买与现金约束；涨停买不进（`submitted` 但被账本整单拒绝）后顺延、第二次尝试 id 不同、`attempts` 为 1 → 2；买单到期（3 个会话）→ `expired` + 冷却；买单对应的名字不再是幸存者 → `cancelled`、无冷却；卖单从不过期、停牌的卖单留在 live；一次 10 送 10 之后：权益无虚假回撤、持仓的 `reference_price` 口径与比值（`entry_adj` 在买入成交日取）、被卖出的卖单名义额按比值正确；持仓名缺复权因子被拒；二次结算同一输入同一结果（含字节）；冷却起始会话 = 结算会话（不是信号会话）；一个名字在同一晚既卖出成交又被计划买回（`cooldown_sessions=0`）；`PaperBookSnapshot` 的校验在结算结果上全部通过（用 `with_plan` 构出快照）。

**Steps / 提交:** `feat(V2-P7-009): settle one session of the paper book -- fills, order lifecycle, cool-downs, entry factors`。

### Task C3：一晚的事务（崩溃安全）、开账与会话间的衔接

**Issue:** `V2-P7-009`。**依据:** B3（`latest_snapshot`/`save_snapshot`）、T5 §6、P7 §9。

**Files:** Create `src/openalpha_cn/backtest/paper_evening.py`、`unit/backtest/test_paper_evening.py`；必要时改 `runtime/composition.py`（只加装配，不改既有装配）。

**Interfaces（拟）：** `open_book(*, store, ledger_path?, session: date, session_index: int, cash: Decimal) -> PaperBookSnapshot`（写开账快照；已存在同一份则零写入，存在不同的则拒绝）；`evening_settlement(*, store, portfolio, ...) -> Settlement` 做"读上一份快照（只走 `latest_snapshot(settling_session=, settling_index=)`）→ `settle_session`"；`commit_evening(*, store, settlement, trade_list) -> PaperBookSnapshot` 存当晚快照（`save_snapshot`）。

**要钉住的行为：** **崩溃安全**——账本追加发生在 `advance` 里、快照在其后才存，所以"账本写了、快照没写"是真实会出现的状态：用会在 `save_snapshot` 处抛错的存储做故障注入，重跑同一晚必须收敛到同一份快照字节、账本里每条转移恰好一次（先读账本里本会话各尝试 id 的转移；若实现者发现 `SQLitePortfolioLedger.append` 对内容相同的重复转移是幂等的，就用它并用测试钉住，否则要在 `advance` 之前检测并复用——先在报告里写明账本的实测行为）；"快照写了、什么都没动"的二次运行零写入；快照缺一天被 `latest_snapshot` 按名拒绝而不是从上一份悄悄接着走（含：前一晚的运行崩溃在账本写入之前）；开账不能覆盖已有的账；`epoch_session` 之前的日期被拒；整晚的顺序（`screen` 之后结算、结算之后 `decide_book`）由一个"晚间"测试驱动真实的 `decide_book`/`build_trade_list`，连跑 25 个会话的合成夹具，最终账簿与计划 1 的模拟（`test_survivor_book_lifecycle.py`）在同一组输入上给出一致的持仓与冷却轨迹。

**Steps / 提交:** `feat(V2-P7-009): one evening of the paper book as a crash-safe transaction`。

**A、B 阶段在合入前留下的要求（Part C 必须全部落实，2026-10-14）：**

依赖 B。目标：`settle_session(...)`——读取状态快照 → 取上一日的订单 → 用 B1 构建步骤 → `PaperPortfolio.advance`（B2 的成本）→ 用计划 1 的 `advance_orders` / `update_cooldowns` 结算订单生命周期 → 得到今天的持仓、在途买单 / 卖单、冷却 → 写快照。要钉住：先结算后决定的顺序；停牌的名字的单保持在途；3 日后买单过期且触发冷却；二次运行零写入；跨年；缺数据按名拒绝。**B 阶段评审留下的、本任务必须落实的要求（2026-10-14）：** (1) 调用 `build_paper_step` 时传入 `session_index`，且只传当日到期的订单；(2) 每个持仓都要有一个**买单成交日**的复权因子（`entry_adj`，B3 的快照里存），`adjustment_ratio` 必须覆盖每个持仓名（没动过的显式给 1）；(3) 交给 `build_trade_list` 的参考价，对**持仓名**要换成账本口径（真实收盘 × 比值）——否则 10 送 10 之后估算的卖出所得只有一半、买单定量偏小；(4) 复权事件之后手续费是按缩放后的名义额收的，而 P6 账簿按原始名义额收（只有比值为 1 时两者才逐分相等），日志里要写明；(5) 账本里经过复权的持仓，`filled_price` 是账本口径、不是任何人成交过的价格，读它的报告要换算；(6) 在成本注入的折算下，账本的 `transfer_fee` 实际是滑点，配置与日志里要这样写。**A、B 阶段在合入前又留下的要求（2026-10-14）：** (7) 快照的读写只走 `latest_snapshot(store, *, settling_session, settling_index)`（它拒绝日期不早于结算日的快照、拒绝缺了中间一天），而"写入早于已持有的最新会话的会话"在存储层被拒；缺失日的检测只在读取时，所以**写快照之前必须先读过上一日的快照**，不得越过缺口直接存；`attempts` 要等于已结算的会话数；(8) 每晚调用 `build_facts_day(..., held=<上一份快照里的全部持仓名，含待卖的>)`（代码拼写必须与面板一致，拼写不对会被按名拒绝）：持仓名不受 `max_carry_sessions` 约束；**`FactsDay.held_status` 给出每个持仓名的去向**——`row`（有事实行）、`no_bar_in_reach`（登记簿在册、从未有过日线 → **拒绝当日**，具名，并说明怎么补面板）、`not_listed`（登记簿认识但当日不在册，即已退市 → **不拒绝**，交给内核卖出，见 (11)）、`not_in_registry`（登记簿没有这个代码 → 拒绝当日，具名）、`board_unrecognised`（前缀不在主板白名单 → 拒绝当日）；`held` 会改变事实日的输出字节（长期停牌的持仓只有被持有时才有行），所以每日入口要把它写进当日的输入溯源，清晨复核传同一集合；(9) `FactsNotes.not_listed_with_bar` 与 `unrecognised_board` 要显示在每日摘要里；(10) `FilePredictionStore.get` 对非法字节可能让 `UnicodeDecodeError` 逃逸（既有缺陷，未测），写预测登记之前先看一眼；(11) **退市结算规则（待用户决定，C2 派发前定）**：一个持仓名在登记簿里退市之后不再有日线，内核会计划卖出，但这笔卖单永远成交不了；持仓名也不会再有收盘价可估值。纸面账簿要有一条明文的终局规则，否则账簿里留着永远挂着的卖单和永远按最后收盘估值的持仓、权益虚高。选项：(a) 在登记簿退市日之后的第一个无日线会话把该持仓**核销**（以账本合法的方式记一笔已实现损失，不编造成交价；这是保守口径）；(b) 按最后收盘继续估值并标记 `stuck`，从槽位里剔除、在摘要里单列——权益虚高，只适合短期。我倾向 (a)。更正：实际上进入退市整理期的名字先会被 `delisting_process` 状态触发强制退出，走到"已退市还持有"的情形需要整个整理期里每个会话都跌停卖不出，应当很少；C2 的报告里要用面板历史数一下这类情形有多少

**整分支评审之后的修订（2026-10-14；用户已决定 D13、D14，D15 是工程默认、待用户异议）：**

- **D13 退市终局 = 核销（用户决定）。** 一个持仓名在登记簿里退市之后（`held_status == "not_listed"` 且当日没有日线），结算在 `decide_book` **之前**把它核销：从持仓里移除，记一笔已实现损失，不编造成交价。这样内核根本不会被要求去卖一个没有价格的持仓（评审指出 `build_trade_list` 对无事实行的持仓会抛 "no reference price"）。机制由 C2 的实现者先读账本的契约再定：优先在账本里记一笔带标签的转移（原因 `delisting_write_off`，零回收的约定价取最小可表示的正价 0.01、不收费、明说这是约定而不是成交）；**如果账本的类型装不下、只能编造成交，停下来报 NEEDS_CONTEXT**。核销的名字出现在 `Settlement.written_off` 与摘要里；不加进快照字段。C2 的报告里要用面板历史数一下这类情形有多少。
- **D14 契约身份（用户决定：两个都加）。** `PaperBookSnapshot.config_digest`（`SurvivorBookConfig` 的 sha256）与 `TradeList.unfunded`（入选但没有资金下单的名字）由 B3-fix2 在落盘之前加好；`latest_snapshot` 现在要求关键字 `config_digest`，一个 runtime 目录里只有一本纸面账簿，用另一套配置接着走、或在已有历史上再开账，都按名拒绝；写入端也只接受"开账 → 逐个会话号 +1"的链。C3 的所有读取都传当前配置的摘要；对照组（`V2-P7-002`）用单独的 runtime 目录，不与实盘纸面账簿共用。
- **D15 持仓限额与买单现金截断（工程默认，待用户异议）。** T5 §5–§7：默认 `PortfolioLimits` 单名 0.25 / 总仓 0.80 会让 10 个等权买单只成交 8 个，所以纸面账簿必须用 `PortfolioLimits(1, 1, 0)`；T5 §6 "买单在开盘时按现金截断"当时记为待决，已建成的行为是**整单拒绝后顺延**（买单 3 个会话内有效），默认保持。构造点：`PaperPortfolio(ledger=storage.paper_ledger, limits=PortfolioLimits(1, 1, 0), execution=slippage_folded_execution(PROTOCOL_COSTS, slippage_rate=PROTOCOL_SLIPPAGE_RATE))` 在 Part D 的 `strategy_survivor.py`（`PROTOCOL_COSTS` 在 `strategy_view.py`，而导入契约 `backtest-no-numeric-stack-or-panel-plane` 禁止 `backtest/` 里导入 `strategy_view`，所以 C2/C3 的函数都**接收**一个 `PaperPortfolio`，不自己构造）；用一个测试钉住这个构造。两条都写进 `V2-P7-006` 要登记的配置。
- **C1 的补充。** 先实测 Tushare `adj_factor` 在停牌日是否有行：`build_paper_step` 要求每个卖单名有比值，如果停牌名没有 `adj_factor`，一个停牌且有待卖单的持仓会让整晚停下来；若缺，放宽 B1 对"当日没有 bar 的卖单"的比值要求（非破坏性）。模块名用 `strategy_survivor_market.py`（家族与守卫行同上）。
- **C2 的补充。** B1 要求 `bars` 非空（`PortfolioBacktestStep.bars` 也是 `min_length=1`）；开账后的第一晚没有持仓也没有订单，没有名字可查，所以 C2 要有明确的"无可结算"分支——不经过运行器，把账簿推进到当日（现金不变、日期前进），从开账快照起手测。
- **C3 的补充。** (a) `open_book` 不接 `ledger_path`，用 `StorageContainer` 里的 `paper_ledger`/`paper_state_store`（硬规则 5）；(b) **缺失会话的补结算路径**：一个晚上没有完成（事实被拒、面板没更新、机器没开）就不会有那一天的快照，之后每晚的 `latest_snapshot` 都会按名拒绝，账簿永久停住；新年第一个会话在没有更名公告时会让这变成每年的事件。要有 `catch_up_session`：对每个缺失的会话按序结算——不做决定、不计划新订单，`survivors` 取"所有待买单的名字"使得没有买单被取消，行情与基准取更晚写入的数据（`build_facts_day` 支持后读，见其"晚读得到晚间的字节"测试），存快照；C3 的测试里要结算一个晚间失败的会话；(c) 面板层"允许空的 `namechange` 公告年"任务是 `V2-P7-008` 上线前（第一次真实新年之前）的**硬闸门**，不再是备选项；(d) 读取快照时两个错误类都要接（`PaperStateError` 与 `storage.paper_state.PaperStateStoreError`，类层次不能改，`backtest/` 不得导入 `storage/`）。

## Part D — `V2-P7-008` 每日入口 `strategy_survivor`（待补全）

依赖 A、C。目标：登记配置按顶层键 `kind` 分派（P6 配置零影响）；每日流程 = 更新面板 → 构建四个因子的 `processed` 档 → `build_facts_day` → `settle_session` → `screen` → `decide_book` → `build_trade_list` → 清单与订单落盘（契约身份先定：要不要含账簿身份、`TradeList` 里是否记录"已入选但因现金不足没下单"的名字——**落盘前由用户在 P7 §9 的待定项里选**）→ 摘要与日志（持仓、候补、临近退出、冻结、清单、账本权益）→ 预测登记 `survivor_book_composite/v1`（horizon `20d`，范围与账本权益无关，先于次日 09:15）；清晨复核模式；幸存者版的迟到记录核查与输入溯源（只增量）。**A 阶段留下的要求（2026-10-14）：** (1) 更新面板一步用 `update_panel(..., survivor=SurvivorPanelUpdate(max_catch_up_sessions=panel_catch_up_bound(config)))` 与 `targets_for_survivor`（含 `namechange`，上一年的 `namechange` 在其已存构建的 `as_of` 早于该年年末时重建，按状态而不是按一月判断），登记配置里的键是 `adapter.max_panel_catch_up_sessions`（缺省 10；**写入登记配置属于 `V2-P7-006`**）；(2) **已知局限（待决，A3 Q1）**：新年第一个会话如果当年还没有任何 `namechange` 公告，第 2 步被拒（provider 返回 `no_data`，面板层拒写空分区；同一次调用里排在 `namechange` 后面的 `index_daily` 也不会写入），`build_facts_day` 也读不到当年的名称史分区。2012 年第一条公告是 01-05、晚于第一个交易日 01-04，所以这不是理论问题。处理：先按已知局限写进幸存者形态的运行手册（与 P6 局限 6 并列，每年开头可能有一两天没有决定），并在 `V2-P7-008` 上线前另立面板层任务——允许空的 `namechange` 公告年（照 `V2-P6-018` 的 `write_empty_announcement_year`，要改 `panel/store.py` 的空分区白名单、`catalog` 的就绪规则、`panel_ingest`、`cli`，加一条新鲜度规则）；(3) 第 3 步的 doctor 会收到 `namechange`（`doctor_datasets()`），会话年的 `namechange` 能否通过 doctor/data-check 未验证，本任务要验证；(4) 用实测的覆盖率校准 `min_coverage`（覆盖率现按允许板块的行数算）；(5) 登记簿首日的 `load_suspensions`、登记簿读取也要求信号年有分区——一月初同类问题，运行手册一并写明；(6) 面板补建上限超过时第 2 步按名拒绝并给出补救命令，每日入口的摘要里要显示 `caught_up`。
**A3 评审留下的要求（2026-10-14）：** (7) 上一年尾部（A3-fix）：新年第一个会话若上一年的会话级分区没补齐，第 2 步在发出任何请求之前按名拒绝并给出补救命令；P6 路径有同样的潜在问题（一次漏掉 12-31 就静默发出约 4×5000 次请求，之后一月每天都被拒），在"逐位不变"的约束下不能在这里改，P6 的每日若要恢复，要先立一个独立任务；(8) 一个因为"当年第一条更名公告晚于该会话"而被拒的会话**永远补不成**（日志钉住 `as_of`，`namechange` 按 `min(年末, as_of)` 抓取），所以 `V2-P7-008` 的"先结算后决定"必须能容忍一个没有完成日志的会话，或者先把面板层的空公告年任务做完；(9) 自举要求：幸存者形态的存储里 `namechange` 与 `trade_cal` 必须从各自第一个已存年起逐年连续（`_years_through`），`V2-P7-006`/`008` 要保证；(10) 登记簿读取：信号年没有生命周期事件时 `build_facts_day` 读不到当年分区——**改读最新的已存生命周期年（不晚于信号年）**，由独立任务 A2-fix3 完成，不并入面板层任务；(11) P6 的 `--full-update` 在一月初同样会因 `namechange` 没有当年公告而失败（既有，A3 之前就有）；(15) **重启要先补面板**：每日登记自 2026-10-08 暂停，面板停在那时；幸存者形态第一次运行时，补建上限与上一年尾部守卫都会按名拒绝并印出手工补建命令（补完需要数百次请求），运行手册（重启一节）必须把"先手工补面板，再第一次运行"写成步骤；(16) A3-fix 留下的小项：当存储的各会话目标 horizon 不一致、批次越过其中一个时，补救命令目前打印**完整重建**（会抓当年到批次末尾的全部会话，实盘可能数百个会话 × 5–7 个请求），更便宜且 `_incremental_start` 也接受的做法是 `--incremental`、`--as-of` 钉在最远的存储 horizon，再在下一次运行补其余——另派一个小任务；上一年只存了部分 census 分区的路径没有专门测试；(13) 清晨复核对比晚间字节时要容忍一类差异：一个在登记簿上市行发布之前就有日线的代码（T3 §3.2 的 `.BJ` 形状），晚间读得到一行（年龄 0），上市日之后再读没有行、只在 `not_listed_with_bar` 里计数；(14) 台账行（`features.csv`、`summary.json`、能力台账同一提交）在能力上线时加（`V2-P7-008`/`009` 落地时一并补 005、016、017、009），不在 Part A、B 的分支上加；(12) `max_panel_catch_up_sessions` 没有上界（10**6 也被接受，等于关掉守卫），`V2-P7-006` 登记时要给一个上限。
**在 A、C 合入后补全。**

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
| 2026-10-14 | A、B 合入前的更正与要求：B1 的接口段改为实现后的签名；Part C 加要求 (7)–(10)（快照写入须经读取、持仓名无事实行的两种情形、被排除的代码计数、预测登记的非法字节）；Part D 加 A 阶段要求 (1)–(6)（`update_panel(survivor=…)`、新年无更名公告的已知局限、doctor 核对、覆盖率校准） | A2 评审与修订、A3 的 Q1/Q2、B3 评审 |
| 2026-10-14 | Part C 补全：三个任务（C1 面板读取与会话序号、C2 结算核心、C3 一晚的事务与开账）；会话序号由 `adapter.epoch_session` 与日历决定；每晚的顺序 = 事实 → `screen` → 结算 → `decide_book` → 计划 → 存快照 | A、B 合入前的接口与评审结论已齐 |
| 2026-10-14 | 整分支评审之后：Part A 加"实现后的接口"块、板块白名单与状态式名称规则更正；Part C 加 D13（退市核销）、D14（`config_digest` 与 `unfunded`）、D15（`PortfolioLimits(1,1,0)`、整单拒绝顺延、构造点）与 C1/C2/C3 补充（`adj_factor` 停牌日实测、"无可结算"分支、补结算路径、`StorageContainer`）；顶层模块改名进入 `strategy_` 家族 | 整分支评审（opus）、完整闸门的守卫失败、用户的两个决定 |
