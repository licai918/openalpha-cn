# V2-P7-025 登记簿的新年缺口：一个还没有任何上市、退市的年份 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一年里第一个交易日、当年还没有任何上市或退市时，幸存者一天从第 2 步走到晚间、前向周报能读到标签、`panel doctor` 与 `require_years_through` 不再被拒——做法与 `V2-P7-021` 同形：**写**一个零行的 `stock_basic` 生命周期年（第 1 次真实新年 2027-01-04 之前的硬闸门），**读**的时候只在它自己那一年之内把它读作"今年还没有上市或退市"，之后按规则过期；真正的失败抓取、已有行的一年、不是观测年自己的年仍按名拒绝。

**Architecture:** 只动三处逻辑：①`domain/stock_universe.py` 加一条规则（一个常量、两个纯函数，`domain/` 不引入 numpy/pandas/sqlite3/duckdb）；②`panel_ingest.record_empty_universe_year(store, batch)` —— 在 `write_stock_universe` **之后**调用，拿整份登记簿批次自己判断"观测年不在拆分里"，零行分区经既有的 `_write_empty_partition` 写出（**不新增第二个零行写入者**）；③`panel_ingest.stock_universe_requirement` 带上 `empty_admissible_through`（表与 `catalog.py:1719-1742` 的通用"zero-row ⇒ 过期后 `stale`"规则同一机制）。`cli` 接线并打印一行 `EMPTY stock_basic year=…`。**所有读者**（第 4 步因子构建、周报标签、doctor、事实适配器、`_stored_universe`、`_recent_listings`）都经 `load_stock_universe`，因此**没有一处调用点需要改**——除了 `panel_ingest` 自己。

**Tech Stack:** Python 3.11、pydantic v2、DuckDB（只在面板侧）、pytest；门禁 `ruff check`、`ruff format --check`、`mypy src scripts`、`lint-imports`。

**Spec / 依据:** 路线图 `docs/specs/v2/openalpha-cn-v2-roadmap.md` 第 505 行（`V2-P7-025`）；规格 `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` 第 363 行（§9 的 2026-11-02 "新增 `V2-P7-025`" 行）；0 次请求测量 `.superpowers/sdd/batch-2/measurement-registry-year.md`；批次 1 收尾记录 `docs/superpowers/plans/2026-11-02-p7-dev-batch-1-closeout.md` §5 第 2 条；先例 `docs/superpowers/plans/2026-11-01-p7-dev-batch-1-B-new-year-and-009.md` 的 B3–B6。**先例代码**：`src/openalpha_cn/panel_ingest.py:7425-7492`（`_write_empty_partition`）、`:7495-7562`（`write_empty_name_year`）、`:1564-1590`（`name_history_requirement` 的 `empty_admissible_through`）、`src/openalpha_cn/domain/name_history.py:118-145`（常量与两个纯函数）、`src/openalpha_cn/panel/catalog.py:977-992`（零行分区的读规则）与 `:1719-1742`（应用处）。

---

## 0. 今天的行为（读码与实测，`main` @ `587a158`，2026-11-07）

### 0.1 缺口在哪一环（逐条 `file:line`，全部由本计划的规划者读过）

- `stock_basic` **没有日期过滤**：一次请求就是整份登记簿（上市行从 1990-12-19，退市行从 1999-07-12；`cli.py:2139-2141` 的 `_UNPINNED_PARTITION_YEAR_TARGETS` docstring，`panel_ingest.write_stock_universe` 的 docstring `:991-1006`）。`cli` 的 `stock_basic` 分支就是 `write_stock_universe(store, _fetch_panel(provider, STOCK_BASIC_DATASET, as_of=now))`（`cli.py:6140-6148`）。
- `write_stock_universe` 把这一个批次按**生命周期年**拆成每年一个分区（`split_panel_batch_by_year`，`panel_ingest.py:703-734`；写入 `:1061-1079`）。**当年若一行事件都没有，这一年就根本不在拆分里，于是没有分区**。这不是失败：抓取成功了，其他年份都写了。
- 没有分区的一年对每个读者都是 `partition_missing`（`catalog.py:1628-1636`）。今天被拒的两个读者：
  - 第 4 步因子构建：`factor_view.py:1517-1520`（`load_stock_universe(years=request.years, as_of=…, max_staleness=None)`，`years` 是会话年）；
  - 前向周报的标签读取：`model_view.py:1701-1707`（同一 `load_stock_universe`；周报经 `scripts/forward_report.py:645-656` 的 `_survivor_outcomes` → `OutcomeLabels` → `_LabelInputs`，标签年份 = 第一条记录年…报告年）；
  - 外加 `panel_doctor.py:1501-1505`：doctor 直接构造 `stock_universe_requirement`，同一缺口也让它把这一年判成 `partition_missing`。
- **没有年份期限**：`stock_basic` 没有公告日，"今年还没有上市/退市"在一年里的任何一天都是真话。0 次请求测量（`.superpowers/sdd/batch-2/measurement-registry-year.md`）量的是另一个方向：过去 14 年里 10 年（71%）在第一个会话时**还没有**当年第一条登记簿事件，间隔可达数月（2009 年第一条是 5 月 6 日，2013 年是 2 月 8 日）。所以窗口不能像 `namechange` 那样只有一个月，必须是当年整年。
- **事实适配器早就绕开**：`strategy_survivor_facts.py:1061-1116` 的 `_registry_year` 读"不晚于会话年的最新已存生命周期年"，所以缺口只在因子构建与标签读取两处暴露（022 的 B 轨探针也是这两处）。

### 0.2 与 `V2-P7-021` 的三处关键差别（决定实现形状）

1. **触发条件不同**：`021` 是"上游回答了 `no_data`"；这里是"**整份登记簿抓取成功**，但观测年不在拆分里"。所以零行年的判断依据是 `batch.status == "success"` 且 `batch.as_of` 所在年不在 `split_panel_batch_by_year` 的年份集合里。`no_data` 的整份登记簿回答（上游一行都没给）是**失败抓取**，今天由 `write_stock_universe` 经 `write_panel_batches` 拒绝（`panel_ingest.py:645-649` 的 `cannot write a 'no_data' batch`），本次不放松。
2. **模板来源不同**：`write_empty_name_year` 从**库**里最新的有行年份取列（`:7536-7549`）；登记簿的批次自己就带着有行的年份，所以模板取**批次**自己的最新有行子批次（经 `panel_coverage`），更正确——库里最新那年可能是旧 schema 写的。
3. **没有"另一个年"这个参数**：`021` 的写入者接受 `year` 参数并拒绝"不是观测年的年"（`:7522-7528`）。登记簿这里年份只能由 `batch.as_of` 推出，"不是观测年"因此**构造不出来**，取代它的是一条必须钉住的时区规则（见 U1 与 Task 1 的 UTC 边界测试）。

### 0.3 观测到的一个连带修复（不额外写代码）

`cli._stored_universe`（`cli.py:4723-4775`）在 `panel build` 带报表目标时，用 `require_years_through=now.year` 读登记簿（`:4768-4774`）；`_recent_listings`（`cli.py:5118-5127`）同样。今天在 2027-01-04 的 `--full-update` 里，`registered_years()` 不含 2027，于是 `_registry_years` 的 `absent` 判据（`panel_ingest.py:1338-1350`）会以 `does not read [2027]` 拒绝——在到达第 4 步之前。空年写进同一个 `panel build` 的 `stock_basic` 分支之后（该分支排在 `_NEEDS_STORED_UNIVERSE` 之前，`cli.py:6140-6156`），`registered_years()` 就含 2027，这条拒绝自然消失。**这就是"空年必须由 `panel build` 自己写"而不是由别的命令补的依据之一**，Task 2 里用一个断言钉住它（不需要新代码）。

---

## Global Constraints

逐条来自 `AGENTS.md`、规格、先例与本次读码，每个任务都隐含这一节。

- Issue ID：提交、分支、PR 一律写 `V2-P7-025`。分支名例：`feat/v2-p7-025-empty-registry-year`。
- **规则的值（用户决定 U1，本计划按推荐值实现）**：`UNIVERSE_YEAR_EMPTY_THROUGH: Final[tuple[int, int]] = (12, 31)`。零行生命周期年只在**它自己那一年之内**（含 12 月 31 日，分区的 `date_timezone`，本数据集为 `Asia/Shanghai`，`catalog.py:387`）可读、可写。
- **观测年** = `batch.as_of` 在 `date_timezone` 下的日历日所在年（`batch.as_of` 就是 `panel build` 的 `--as-of`，即那次抓取的 `now`：`cli.py:6146-6148`）。**不是** UTC 年。
- **零行年只在四条同时成立时写**：①`batch.dataset == STOCK_BASIC_DATASET`；②`batch.status == "success"` 且拆分非空；③观测年**不在**拆分里；④库里该年没有行（没有分区，或已是一个零行分区——后者按较晚的观测**重新记录**，同 `021`）。任何一条不成立 → 或者什么也不写（③不成立、④的"已有行"分支见下），或者 `PanelBatchError`。
- **已有行、而新批次缺这一年**：若 `batch.as_of >= 已存覆盖的 as_of` → `PanelBatchError`（更晚的整份抓取却少了库里有的证券，是丢失的抓取）；若 `batch.as_of <` 已存 `as_of`（把过去的 `as_of` 重放到一个回填过的库）→ **返回 `()`，什么也不写**。后者是 `write_stock_universe` 已经写明的规则（`panel_ingest.py:1024-1030`：一个从批次里整年消失的年份就是不被写，它的分区保留全部行；拒绝它会挡住重放）。
- **不放松的两条**：`_refuse_to_drop_stored_subjects`（`panel_ingest.py:853-891`）与"每个年都在任何年写入之前被检查"（`:1032-1048`）**一字不改**。零行年写在行年之后，若它被拒，行年已经写盘——那是 `panel build` 的普通结果（命令会点名写了什么），**不是**两遍检查要防的"生命周期年中间缺一年"：空年是最新那一年，最新年缺席就是事件发生前库本来就在的状态，`load_stock_universe` 的 gap 规则看不见也不需要看见它。
- **`load_stock_universe` 的 gap 规则与 `require_years_through`**（`panel_ingest.py:1323-1367`）**不改**：gap 规则说的是"被点名的年之间有一个库里有、却没被读"（零行年只要被读就满足）；`require_years_through` 只看**被请求**的年（`:1338-1350`）。两条都用回归测试钉住"零行年不削弱它们"。
- **`EMPTY` 行（照 `_write_name_year` 的形状，`cli.py:5354-5380`）**，写成功**之后**打到 stderr：
  `EMPTY stock_basic year=<Y>: no listing or delisting yet at <now>; the year may be read empty through <Y-12-31>; recorded as an empty partition`
- **请求数**：0 次增加。整份登记簿一次请求已经带着所有有行的年份，写空年是同一批次上的本地动作。脚本化行情下：幸存者一天 2027-01-04 仍是 **11** 次；第 2 步框架（`test_daily_selection_survivor.py`）仍是 `FIRST_SESSION_OF_A_YEAR` = **12**；晨查仍是 1 次。（corrected 2026-11-07：11 是批次 1 的 `new_year` 夹具的形状；批次 2 的 T3 夹具按 12 月 31 日的名称种子建库，幸存者一天 2027-01-04 是 **12** 次——第 12 次是既有的 `V2-P7-016` 年前名称重建（`namechange` 共 2 次），不是 `V2-P7-025`；登记簿的空年本身 0 次请求。）
- **硬规则**：两个数据平面分离（登记簿是面板平面，绝不进 `ParquetEvidenceStore`）；`domain/` 不引入 numpy/pandas/sqlite3/duckdb（新规则只用 `datetime.date`）；一个组装根（本计划不新建存储）；面板夹具测试时生成（`.parquet/.duckdb/.sqlite3/.db` 不入库）；契约不改字段（零行分区的覆盖记录是既有 `PartitionCoverage` 形状）；重放性字段真实（`as_of`/`fetched_at` 用抓取自己的时钟）；措辞"候选"，工程完成不是研究证据；本计划不产出任何收益数字。
- **测试卫生**：隔离 worktree 里跑测试一律 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src"`（venv 里的包指向主检出的 `src`），先 `python -c "import openalpha_cn; print(openalpha_cn.__file__)"` 确认；长文件只跑 `-k` 选择；**一次只跑一个 pytest 进程**；~~提交前跑一次 `tests/unit`（约 40 分钟，串行）~~（corrected 2026-11-07: 控制者裁定：本批实现者只跑聚焦文件，整套在收尾串行跑一次）；绝不在 `git --exec`/hook 里跑测试。
- **变异探针**：`cp <file> <scratch>/orig`；改；跑目标测试，**必须红**；`cp <scratch>/orig <file>`；`cmp <file> <scratch>/orig`；全程 `PYTHONDONTWRITEBYTECODE=1`。
- 提交信息末尾：`Co-Authored-By: Claude Code <noreply@anthropic.com>`。不 push、不 squash/rebase。台账（`features.csv`、`summary.json`、`docs/release/openalpha-v1-feature-ledger.md`）本计划不碰（新测试只加进已有文件，不触发硬规则 9）。

## Review Focus

最可能咬到使用者的五类输入 / 失败模式，每条由负责任务里的测试钉住：

1. **把一个"还没有事件"的空年读成"这一年什么都没有"**，一年之后仍然照答——那样一整个缺失的年份会让死掉的证券看起来还在上市（正是 `require_years_through` 存在的 fail-open）。Task 1：`test_an_empty_registry_year_is_unreadable_after_its_year_even_without_a_staleness_bound`。
2. **空回答覆盖掉库里已有的证券**（把一整年解约掉）。Task 1：`test_an_empty_answer_is_never_written_over_a_year_that_holds_securities`、`test_a_replay_older_than_the_stored_year_writes_nothing_for_it`。
3. **把失败的整份抓取当成"经核实为空"**（`no_data` 的登记簿回答 = 上游一行都没给）。Task 1：`test_a_no_data_registry_fetch_is_not_recorded_as_an_empty_year`；Task 2 的拒绝路径不变。
4. **把规则推广成"任何缺失的年都是空年"**（过去某年没被摄入 ≠ 那一年没有事件），或者用零行年把 gap/`require_years_through` 的拒绝弄松。Task 1：`test_a_year_the_fetch_carries_is_never_recorded_empty`、`test_the_skipped_year_refusal_still_fires_beside_an_empty_year`、`test_an_empty_year_satisfies_a_coverage_demand_but_a_hole_still_does_not`。
5. **2027-01-04 这一天真的走不完**（第 4 步停、周报标签读被拒）。Task 3 端到端（夹具 `EmptyNewYear(delisted=())`，事实适配器、doctor、gate、记录、晚间全走完），Task 4 周报跨年（并探针出 `partition_file_missing` / `partition_missing` 两种拒绝）。

## Bound code touched（受登记绑定的代码）

| 文件 | 任务 | 改什么 |
|---|---|---|
| `src/openalpha_cn/domain/stock_universe.py` | T1 | 新常量 `UNIVERSE_YEAR_EMPTY_THROUGH` 与两个纯函数（放在 `STOCK_BASIC_DATASET` 定义 `:127-132` 之后） |
| `src/openalpha_cn/panel_ingest.py` | T1 | 导入两处；`stock_universe_requirement` 加 `empty_admissible_through`（`:1106-1114`）；新函数 `record_empty_universe_year`（放在 `write_empty_name_year` 之后，`:7562` 与 `:7565` 之间）；`_write_empty_partition` 的 docstring 补第三个调用者（`:7435-7437`） |
| `src/openalpha_cn/panel/catalog.py` | T1 | **只改文字**：`empty_admissible_through` 的 docstring（`:977-992`）补上登记簿生命周期年这条规则 |
| `src/openalpha_cn/panel/store.py` | T1 | **只改文字**：`allow_empty` 的 docstring（`:1848-1854`）补上第三个门（仍然全部经 `_write_empty_partition`） |
| `src/openalpha_cn/cli.py` | T2 | 导入 `record_empty_universe_year`、`universe_year_empty_admissible_through`；新函数 `_write_registry_year`（放在 `_write_name_year` 之后，`:5380` 附近）；`stock_basic` 分支（`:6140-6148`）改为先取批次、写行年、再写空年 |
| `scripts/daily_selection.py` | T5 | **只改模块 docstring**（约 110-115 行那段"登记簿有同样的缺口、还没有修"）。受绑定：提交时必须附"去掉 docstring 后的 AST 与改前相同"的核对结果（批次 1 的同做法） |

`src/` 里**没有**其它文件需要改。特别地 **`factor_view.py`、`model_view.py`、`panel_doctor.py`、`strategy_survivor_facts.py` 一行都不改**：它们都经 `load_stock_universe`，而 `load_stock_universe` 自己构造 `stock_universe_requirement`（`panel_ingest.py:1293-1295`），`panel_doctor` 是唯一一个在外部构造它的调用者（`panel_doctor.py:1503`，同一函数，同一处修好）。见「与 issue 文本或代码不符之处」的 C1。

现有没有任何登记被作废（每日登记自 2026-10-08 暂停），`006b` 之前改动受绑定代码是合适的——但**这个 issue 是否算 `006a` 的一项、是否进 `006` 的依赖，是用户的决定**（路线图第 505 行与 §9 第 363 行都写着"待用户确认（控制者的建议）"），本计划不重新裁定。

## Decisions for the user（不由本计划决定）

| # | 决定 | 推荐 | 阻塞什么 |
|---|---|---|---|
| **U1** | **一个零行登记簿年可以读多久**（用户可见的政策）：到当年 12 月 31 日（含），还是别的窗口 | **到当年 12 月 31 日（含）**，一个常量 `UNIVERSE_YEAR_EMPTY_THROUGH = (12, 31)`（`domain/stock_universe.py`）。依据：登记簿没有公告日，"今年还没有"在一年里任何一天都成立；实测间隔可达数月（2009-05-06、2013-02-08），任何短于"全年"的窗口都会在真实年份里失败；反过来，**一整年一个事件都没有**在 1990–2026 从没出现过（测量文件对 2013–2026 逐年的表 + 全库 6,217 行 / 37 年），所以"过期后按 `stale` 拒读"是对的、代价接近零。**代价（要说清）**：若某年真的整年无事件，从次年 1 月 1 日起该库的登记簿读会被拒（`stale` by rule），恢复手段是改这个常量或删掉那个分区——这是失败即关闭，符合本仓库的判据 | Task 1 的常量值（测试只引用 `universe_year_empty_admissible_through`，改值是一行） |
| **U2** | 这个 issue 在 `006a` / `006` 依赖里的位置 | **维持控制者的建议**（硬闸门、`006a` 的一项、`006` 的依赖）。理由（复述记录里已有的）：改动在受绑定代码里，必须在 `006b` 登记之前；2027-01-04 之前不修，真实新年的第一个晚间在当年还没有事件时走不完 | 不阻塞本计划任何任务；阻塞"真实新年首晚能走完"这一结论与 `006b` 的时间安排 |
| **U3** | 空年的 `EMPTY` 行要不要打、打在什么层 | **打，写到 stderr，在写成功之后**，形状与 `_write_name_year` 的 `EMPTY namechange year=…` 一致（`cli.py:5370-5378`）。理由：`panel build` 的操作者需要知道"这一年被记成空的、能读到哪天"，这是唯一一处会打印它的地方；打在 `cli` 而不是 `panel_ingest`，因为面板层不打印 | Task 2 的文字断言（改文案只改一行 f-string） |

## 与 issue 文本或代码不符之处（给控制者）

- **C1（最重要）**：任务书说"`stock_universe_requirement` 加 `empty_admissible_through`，**每个调用者都要传**（`factor_view.py`、`panel_doctor.py:1503`、`model_view.py:1701-1707`、`strategy_survivor_facts.py`）"。代码里**没有调用者自己构造这个要求**：`load_stock_universe` 在 `panel_ingest.py:1293-1295` 内部构造它，`panel_doctor.py:1503` 是唯一的外部调用者，签名不变、自动受益。`factor_view.py:1517-1520`、`model_view.py:1701-1707`、`strategy_survivor_facts.py:880-885` 都只调 `load_stock_universe`，**一行不改**。所以受绑定代码比任务书列的少三个文件。
- **C2**：任务书说"两个读者今天被拒：第 4 步与周报标签读，外加 `panel_doctor` 的要求"——属实，但要补一句：`load_stock_universe` 的**其余读者**（`cli._stored_universe` `:4723-4775`、`cli._recent_listings` `:5118-5127`、`feature_matrix.py:1320`、`shortlist_view.py:1386`、`panel_doctor.py:1989`）在空年写进去之后也会读到它，全部靠同一条要求放行；其中 `_stored_universe` / `_recent_listings` 的 `require_years_through=now.year` **今天**在 2027-01-04 就会先拒（`does not read [2027]`，`panel_ingest.py:1338-1350`），修好之后不再拒。见 §0.3。
- **C3**：路线图与 §9 都写"真正缺分区（**其前一年也缺**，或库里没有不晚于该年的生命周期年）仍按名拒绝"。按代码，一个**成功的整份登记簿抓取**不可能产生"前一年也缺"：它带着上游所有有事件的年份，`write_stock_universe` 会把它们全部写进去；缺的只可能是**上游也没有事件**的年份，而那些年份本来就没有数据可缺。所以本计划的实现是：**只有观测年可以被记成空**（过去的年若缺，只能是没摄入，不是"那年没有事件"），"库里没有不晚于该年的生命周期年"仍由 `strategy_survivor_facts._registry_year`（`:1061-1116`）按名拒绝。路线图那句话要据此收窄（属文档，交控制者）。
- **C4**：路线图写"目录库从未登记过该年时应是 `partition_missing`（读代码推得，**未探针**）"。本计划在 Task 4 把它**探针成测试**（`remove_partition` → `partition_missing`；只删文件 → `partition_file_missing`），两种都钉住，那句话可以改成"已探针"。
- **C5**：`021` 的验收"新年第一个交易日 step 2 与晨查能完成"在本 issue 之前**不等于**这一天能走完——B 轨的 C1 已经写过；本计划把"这一整天能走完"作为验收，`test_the_first_session_of_a_year_with_no_listing_or_delisting_runs_to_its_evening` 的 docstring 要写明它不是"021 已覆盖"的子集。
- **C6**：`scripts/daily_selection.py` 的模块 docstring（约 110-115 行）今天写着"登记簿有同样的缺口、还没有修"，本计划之后是假话；它是**受绑定代码**，Task 5 只改文字并要求 AST 不变的证据。运行手册 `docs/runbooks/daily-selection.zh-CN.md` 第 608 行（已知局限 1）与第 823 行（"不跑 2027 年第一个交易日"）同样要改。

---

## 文件结构

| 文件 | 任务 | 动作 |
|---|---|---|
| `src/openalpha_cn/domain/stock_universe.py` | T1 | 1 个常量 + 2 个函数 |
| `src/openalpha_cn/panel_ingest.py` | T1 | 2 处导入、1 张表、1 个新函数、2 段 docstring |
| `src/openalpha_cn/panel/catalog.py`、`src/openalpha_cn/panel/store.py` | T1 | 只改 docstring |
| `tests/unit/domain/test_stock_universe.py` | T1 | 加 1 条测试，导入 2 个名字 |
| `tests/integration/panel/test_registry_ingest.py` | T1 | 加 13 条测试（其中 1 条 2 例参数化），导入 2 个名字 |
| `tests/integration/panel/test_panel_doctor.py` | T1 | 加 1 条测试 + 1 个本地批次构造 |
| `src/openalpha_cn/cli.py` | T2 | 1 个新函数、1 个分支、2 处导入 |
| `tests/unit/scripts/test_daily_selection_survivor.py` | T2 | 1 个夹具市场、1 个帮助函数、3 条测试 |
| `tests/unit/scripts/test_daily_selection_survivor_day.py` | T3 | 1 个 module 夹具、1 条端到端测试 |
| `tests/unit/scripts/test_forward_report_survivor.py` | T4 | 1 个 module 夹具、1 条测试 + 1 条参数化测试（2 例），导入 1 个夹具 + 1 个名字 |
| `scripts/daily_selection.py`（docstring）、`docs/runbooks/daily-selection.zh-CN.md` | T5 | 文字 |

## 任务与依赖

```
T1 (面板层：规则·写·读·doctor)
      └─> T2 (cli 接线 + EMPTY 行)
              ├─> T3 (幸存者一天端到端，生成夹具)
              │        └─> T4 (周报跨年标签读)
              └─> T5 (运行手册 + 模块 docstring)     ← 与 T3/T4 独立，可并行
```

- T2–T5 都依赖 T1；T3 依赖 T2（第 2 步的 `panel build` 才会写空年）；T4 依赖 T3（复用 T3 的夹具 `empty_registry_year`）。
- T5 只改文字，可与 T3/T4 并行；但 `scripts/daily_selection.py` 的那段要在 T2 的 `EMPTY` 文案定稿之后写，所以排在 T2 之后。

---

## Task T1：面板层——规则、零行登记簿年的写与读（`V2-P7-025`）

**Files:**
- Modify: `src/openalpha_cn/domain/stock_universe.py`（`STOCK_BASIC_DATASET` 定义 `:127-132` 之后）
- Modify: `src/openalpha_cn/panel_ingest.py`（导入 `:437-442`；`stock_universe_requirement` `:1106-1114`；`_write_empty_partition` 的 docstring `:7435-7437`；新函数放在 `:7562` 之后）
- Modify (docstring only): `src/openalpha_cn/panel/catalog.py:977-992`、`src/openalpha_cn/panel/store.py:1848-1854`
- Test: `tests/unit/domain/test_stock_universe.py`、`tests/integration/panel/test_registry_ingest.py`、`tests/integration/panel/test_panel_doctor.py`

**Interfaces（产出，后续任务依赖，名字与类型不得改）：**
- `domain.stock_universe.UNIVERSE_YEAR_EMPTY_THROUGH: Final[tuple[int, int]] = (12, 31)`——U1。
- `domain.stock_universe.universe_year_empty_admissible_through(year: int) -> date`
- `domain.stock_universe.universe_year_may_be_empty(year: int, day: date) -> bool`——`day <= universe_year_empty_admissible_through(year)`；"是否同一年"由写入方判断（同 `name_year_may_be_empty` 的分工）。
- `panel_ingest.record_empty_universe_year(store: PanelStore, batch: ColumnarPanelBatch, *, date_timezone: str = DEFAULT_DATE_TIMEZONE) -> tuple[PartitionRef, ...]`，拒绝一律 `PanelBatchError`（已在 `cli._PANEL_WRITE_REFUSALS` 里）。
- `panel_ingest.stock_universe_requirement(...)`：签名不变，返回值多 `empty_admissible_through={year: universe_year_empty_admissible_through(year) for year in sorted(set(years))}`。

- [ ] **Step 1：写失败的测试。**

`tests/unit/domain/test_stock_universe.py`（导入加 `universe_year_empty_admissible_through, universe_year_may_be_empty`；文件已导入 `date`，若没有 `timedelta` 一并加）：

```python
def test_a_lifecycle_year_may_be_empty_through_its_own_last_day_and_not_a_day_after() -> None:
    """`V2-P7-025`: the registry has no announcement clock -- "nothing has listed or delisted yet
    this year" is true on any day of the year -- and the measured gap between a year's first
    session and its first registry event runs to months (2009's first event was 6 May, 2013's
    8 February). So the window covers the year's first session and the whole year after it, and
    its last day is the last day. The value is the user's (plan U1), so the test reads the
    function rather than the date, and demands only that the longest measured gap is inside it."""
    through = universe_year_empty_admissible_through(2027)
    assert through.year == 2027 and through >= date(2027, 5, 6)
    assert universe_year_may_be_empty(2027, date(2027, 1, 4))
    assert universe_year_may_be_empty(2027, through)
    assert not universe_year_may_be_empty(2027, through + timedelta(days=1))
```

`tests/integration/panel/test_registry_ingest.py`（导入：`record_empty_universe_year` 加进 `openalpha_cn.panel_ingest` 的导入；`universe_year_empty_admissible_through` 加进 `openalpha_cn.domain.stock_universe` 的导入；`_evening`、`SHANGHAI`、`_registry_batch`、`_seed_registry`、`REGISTRY_ITEMS`、`REGISTRY_YEARS`、`_upto` 都已在文件里）。加在 `test_an_empty_rename_year_needs_a_stored_year_to_take_its_columns_from` 之后：

```python
# --- V2-P7-025: a lifecycle year with no listing and no delisting in it yet -------------------

# `FIRST_EVENING_2027` (18:30 Asia/Shanghai on 4 January 2027) and `SHANGHAI` are the `021`
# tests' own, defined above at `:647-655`; reuse them, do not redefine them.
LISTED_2027: Final[list[list[Any]]] = [
    *REGISTRY_ITEMS,
    ["600999.SH", "新名字", "SSE", "主板", "L", "20270215", None],
]
"""The same registry, with one security listing on 15 February 2027."""


def test_a_lifecycle_year_with_no_event_yet_is_recorded_empty_and_read_across_the_history(
    tmp_path: Path,
) -> None:
    """`V2-P7-025`: the whole-registry fetch at 4 January 2027 carries no 2027 row, so the year is
    recorded empty -- no rows, observed at the fetch, its columns taken from the fetch's own
    newest year -- and the read that names 2027 is served: `years_read` says 2027 was read, and
    the membership it answers for 4 January is the one it answered for 31 December, because a
    year with no event in it adds nothing to the universe."""
    store = _seed_registry(tmp_path)
    batch = _registry_batch(FIRST_EVENING_2027)

    refs = record_empty_universe_year(store, batch)

    assert [(ref.dataset, ref.year, ref.row_count) for ref in refs] == [
        (STOCK_BASIC_DATASET, 2027, 0)
    ]
    coverage = store.read_coverage(STOCK_BASIC_DATASET, 2027)
    template = store.read_coverage(STOCK_BASIC_DATASET, 2026)
    assert coverage is not None and template is not None
    assert coverage.row_count == 0
    assert coverage.as_of == coverage.fetched_at == coverage.last_event_time == FIRST_EVENING_2027
    assert coverage.max_available_time == datetime(2027, 1, 1, tzinfo=SHANGHAI)
    assert coverage.fields == template.fields
    universe = load_stock_universe(
        store, years=(2027,), as_of=FIRST_EVENING_2027, max_staleness=None
    )
    assert universe.years_read[-1] == 2027
    assert universe.listed_on(date(2027, 1, 4)) == universe.listed_on(date(2026, 12, 31))


def test_a_lifecycle_year_never_written_is_still_refused_as_missing(tmp_path: Path) -> None:
    """Verified empty is a partition that says so; a year with no partition is still missing."""
    store = _seed_registry(tmp_path)
    with pytest.raises(PanelStorageError, match="partition_missing"):
        load_stock_universe(store, years=(2027,), as_of=FIRST_EVENING_2027, max_staleness=None)


@pytest.mark.parametrize(("offset", "readable"), [(0, True), (1, False)])
def test_an_empty_registry_year_is_unreadable_after_its_year_even_without_a_staleness_bound(
    tmp_path: Path, offset: int, readable: bool
) -> None:
    """The writer's rule, applied at read time: recorded on 4 January, the year reads as "nothing
    yet" through its window's last day and not a day later, with `max_staleness=None` -- a store
    that never fetched the year again must not answer for it."""
    store = _seed_registry(tmp_path)
    record_empty_universe_year(store, _registry_batch(FIRST_EVENING_2027))
    day = universe_year_empty_admissible_through(2027) + timedelta(days=offset)

    def read() -> Any:
        return load_stock_universe(store, years=(2027,), as_of=_evening(day), max_staleness=None)

    if readable:
        assert read().years_read[-1] == 2027
    else:
        with pytest.raises(PanelStorageError, match="stale by rule"):
            read()


def test_an_empty_registry_year_is_the_shanghai_year_of_its_observation(tmp_path: Path) -> None:
    """00:30 on 1 January 2027 in Shanghai is still 31 December 2026 in UTC. The observation year
    is the partition's own zone: the year that has only just begun has no listing, and 2026 --
    which holds fourteen lifecycle rows in this fixture -- is never touched by an answer about
    2027."""
    store = _seed_registry(tmp_path)
    early = datetime(2027, 1, 1, 0, 30, tzinfo=SHANGHAI)
    before = store.read_coverage(STOCK_BASIC_DATASET, 2026)

    refs = record_empty_universe_year(store, _registry_batch(early))

    assert [ref.year for ref in refs] == [2027]
    assert store.read_coverage(STOCK_BASIC_DATASET, 2026) == before


def test_an_empty_registry_year_is_recorded_again_at_the_later_observation(
    tmp_path: Path,
) -> None:
    """A stored empty year is not a fact that stops being observed: the evening of 1 June
    records it again at its own clock, so a reader with a staleness bound stays served."""
    store = _seed_registry(tmp_path)
    record_empty_universe_year(store, _registry_batch(FIRST_EVENING_2027))
    later = _evening(date(2027, 6, 1))

    refs = record_empty_universe_year(store, _registry_batch(later))

    coverage = store.read_coverage(STOCK_BASIC_DATASET, 2027)
    assert [ref.year for ref in refs] == [2027]
    assert coverage is not None and coverage.row_count == 0 and coverage.as_of == later


def test_a_year_the_fetch_carries_is_never_recorded_empty(tmp_path: Path) -> None:
    """2026 is in the fetch, so this writer has nothing to do -- and could only do harm."""
    store = _seed_registry(tmp_path)
    before = store.read_coverage(STOCK_BASIC_DATASET, 2026)

    assert record_empty_universe_year(store, _registry_batch(_evening(date(2026, 12, 31)))) == ()
    assert store.read_coverage(STOCK_BASIC_DATASET, 2026) == before


def test_the_first_lifecycle_row_of_the_year_replaces_the_empty_partition_whole(
    tmp_path: Path,
) -> None:
    """A security lists on 15 February 2027: the whole-registry write replaces the empty partition
    whole, and from then on the writer has nothing to record for the year."""
    store = _seed_registry(tmp_path)
    record_empty_universe_year(store, _registry_batch(FIRST_EVENING_2027))

    write_stock_universe(store, _registry_batch(_evening(date(2027, 2, 16)), LISTED_2027))

    coverage = store.read_coverage(STOCK_BASIC_DATASET, 2027)
    assert coverage is not None and coverage.row_count == 1
    assert (
        record_empty_universe_year(store, _registry_batch(_evening(date(2027, 2, 17)), LISTED_2027))
        == ()
    )


def test_an_empty_answer_is_never_written_over_a_year_that_holds_securities(
    tmp_path: Path,
) -> None:
    """A later fetch that carries no 2027 row while the store holds one is a lost fetch, not news
    about the year: refused, and the stored row stays."""
    store = _seed_registry(tmp_path)
    write_stock_universe(store, _registry_batch(_evening(date(2027, 2, 16)), LISTED_2027))
    before = store.read_coverage(STOCK_BASIC_DATASET, 2027)

    with pytest.raises(PanelBatchError, match=r"year=2027 already holds 1 lifecycle row"):
        record_empty_universe_year(store, _registry_batch(_evening(date(2027, 3, 1))))

    assert store.read_coverage(STOCK_BASIC_DATASET, 2027) == before


def test_a_replay_older_than_the_stored_year_writes_nothing_for_it(tmp_path: Path) -> None:
    """A whole-registry fetch at an `as_of` older than the stored year's rows carries none of them
    -- `write_stock_universe`'s own rule for a year that vanishes from a batch -- so replaying one
    over a backfilled store must not replace them with an empty partition. Nothing is written and
    nothing is refused."""
    store = _seed_registry(tmp_path)
    write_stock_universe(store, _registry_batch(_evening(date(2027, 2, 16)), LISTED_2027))
    before = store.read_coverage(STOCK_BASIC_DATASET, 2027)

    refs = record_empty_universe_year(store, _registry_batch(FIRST_EVENING_2027))

    assert refs == ()
    assert store.read_coverage(STOCK_BASIC_DATASET, 2027) == before


def test_a_no_data_registry_fetch_is_not_recorded_as_an_empty_year(tmp_path: Path) -> None:
    """A whole-registry answer with no row at all is a failed fetch -- an empty *market* -- not a
    year in which nothing happened. Refused by name; no partition appears."""
    store = _seed_registry(tmp_path)
    empty = _registry_batch(FIRST_EVENING_2027, [])
    assert empty.status == "no_data"

    with pytest.raises(PanelBatchError, match="cannot record a lifecycle year empty"):
        record_empty_universe_year(store, empty)

    assert 2027 not in store.registered_years(STOCK_BASIC_DATASET)
```

再加三条把"零行年不许削弱既有守卫"钉住的回归测试（放在同一节末尾）：

```python
def test_the_registry_requirement_carries_the_empty_year_window_every_reader_needs() -> None:
    """One requirement, built by `load_stock_universe` itself, so `factor_view`, `model_view`, the
    doctor, the statement sweep and the shortlist all get the rule without a call site changing."""
    requirement = stock_universe_requirement(
        years=(2026, 2027), as_of=FIRST_EVENING_2027, max_staleness=None
    )
    assert requirement.empty_admissible_through == {
        year: universe_year_empty_admissible_through(year) for year in (2026, 2027)
    }


def test_the_skipped_year_refusal_still_fires_beside_an_empty_year(tmp_path: Path) -> None:
    """The gap rule is about the years a read names, not about how many rows a year holds: with
    2027 recorded empty, a read that drops 2020 -- which the store holds -- is still refused."""
    store = _seed_registry(tmp_path)
    record_empty_universe_year(store, _registry_batch(FIRST_EVENING_2027))
    years = tuple(year for year in REGISTRY_YEARS if year != 2020)

    with pytest.raises(PanelBatchError, match=r"skip \[2020\], which the store holds"):
        load_stock_universe(store, years=years, as_of=FIRST_EVENING_2027, max_staleness=None)


def test_an_empty_year_satisfies_a_coverage_demand_but_a_hole_still_does_not(
    tmp_path: Path,
) -> None:
    """`require_years_through` asks which years the read *names*, so an empty year that is named
    satisfies it and a year that is not named is still a hole -- the demand is neither tightened
    nor loosened by `V2-P7-025`."""
    store = _seed_registry(tmp_path)
    record_empty_universe_year(store, _registry_batch(FIRST_EVENING_2027))

    demanded = load_stock_universe(
        store,
        years=(*_upto(2026), 2027),
        as_of=FIRST_EVENING_2027,
        max_staleness=None,
        require_years_through=2027,
    )
    assert demanded.years_read[-1] == 2027

    with pytest.raises(PanelBatchError, match="does not read"):
        load_stock_universe(
            store,
            years=_upto(2026),
            as_of=FIRST_EVENING_2027,
            max_staleness=None,
            require_years_through=2027,
        )
```

`tests/integration/panel/test_panel_doctor.py`（导入 `record_empty_universe_year`；文件已有 `STOCK_BASIC_DATASET`、`seed`、`report_for`、`_batch`、`_midnight_shanghai`），加在 `test_each_dataset_can_be_asked_about_its_own_years` 之后：

```python
FIRST_EVENING_2027_UTC: Final[datetime] = datetime(2027, 1, 4, 10, 30, tzinfo=UTC)
"""18:30 Asia/Shanghai on 2027's first session, as this file's clocks are UTC instants."""


def _registry_batch_fetched_at(as_of: datetime) -> ColumnarPanelBatch:
    """The seeded registry's own rows, fetched a year later: the same 2026 listings and no 2027
    lifecycle event."""
    batch = universe_batch()
    return _batch(
        STOCK_BASIC_DATASET,
        subjects=batch.subjects,
        columns=batch.columns,
        event_time=batch.timeline.event_time,
        available_time=batch.timeline.available_time,
        fetched_at=as_of,
    )


def test_an_empty_registry_year_is_a_normal_state_and_the_year_after_it_is_stale(
    tmp_path: Path,
) -> None:
    """`V2-P7-025`: the doctor asks the registry through `stock_universe_requirement`, the same
    requirement every reader uses, so a lifecycle year recorded empty on its first session is
    `ready` with no finding and no warning -- nothing was unavailable, so a `check_unavailable`
    note would be false -- and the day after that year it is `stale` like any other read."""
    store = seed(tmp_path)
    record_empty_universe_year(store, _registry_batch_fetched_at(FIRST_EVENING_2027_UTC))

    healthy = report_for(
        store,
        as_of=FIRST_EVENING_2027_UTC,
        datasets=(STOCK_BASIC_DATASET,),
        years_by_dataset={STOCK_BASIC_DATASET: (2027,)},
    )
    assert healthy.dataset(STOCK_BASIC_DATASET).readiness.state == "ready"
    assert [
        finding.code for finding in healthy.findings if STOCK_BASIC_DATASET in finding.datasets
    ] == []

    after = report_for(
        store,
        as_of=datetime(2028, 1, 4, 10, 30, tzinfo=UTC),
        datasets=(STOCK_BASIC_DATASET,),
        years_by_dataset={STOCK_BASIC_DATASET: (2027,)},
    )
    assert after.dataset(STOCK_BASIC_DATASET).readiness.state == "blocked"
    assert [
        finding.code for finding in after.findings if STOCK_BASIC_DATASET in finding.datasets
    ] == ["stale"]
```

（`report_for` 的类型标注是 `object`；测试文件里既有测试直接用 `report.dataset(...)` / `report.findings`，照用即可，不需要 `# type: ignore`——测试文件不进 `mypy src scripts`。若 `datasets=(STOCK_BASIC_DATASET,)` 触发某个 cross-check 的"没跑"告警，把 `datasets` 去掉、只把断言限定在 `STOCK_BASIC_DATASET` 这条 finding 上，断言本身不变。）

- [ ] **Step 2：运行，确认失败。** `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" uv run pytest tests/unit/domain/test_stock_universe.py tests/integration/panel/test_registry_ingest.py tests/integration/panel/test_panel_doctor.py -q`。**预期：** 三个文件都在收集时 `ImportError: cannot import name 'universe_year_empty_admissible_through'` / `… 'record_empty_universe_year' …`——函数还不存在。

- [ ] **Step 3：最小实现。**

`src/openalpha_cn/domain/stock_universe.py`（`STOCK_BASIC_DATASET` 之后，只用已导入的 `date` 与 `Final`）：

```python
UNIVERSE_YEAR_EMPTY_THROUGH: Final[tuple[int, int]] = (12, 31)
"""`(month, day)`: the last calendar day (Asia/Shanghai, the zone every lifecycle date is in) on
which a lifecycle year with no listing and no delisting in it yet may be recorded and read empty
(`V2-P7-025`).

`stock_basic` is fetched whole -- no date filter -- and filed by the year each lifecycle event
falls in (`panel_ingest.write_stock_universe`), so a year in which nothing has happened yet has no
partition, and a year with no partition is `partition_missing` to every reader. Recorded empty,
the year answers "no listing or delisting yet"; the first real row replaces it whole, and a stored
empty year is recorded again at the later observation.

There is no statutory deadline to borrow: "nothing has happened yet this year" is true on any day
of the year, and the measured gap between a year's first session and its first registry event runs
to months (2009's first event was 6 May, 2013's 8 February). So the window is the year itself.
Past it, a whole market that has held no listing and no delisting for a year is a failed fetch --
every year 1990..2026 has events -- and an empty partition still stored is stale by rule. The
value is the user's (decision U1 of
`docs/superpowers/plans/2026-11-07-v2-p7-025-registry-new-year.md`)."""


def universe_year_empty_admissible_through(year: int) -> date:
    """The last day on which lifecycle year `year` may be read as "nothing yet this year"."""
    month, day = UNIVERSE_YEAR_EMPTY_THROUGH
    return date(year, month, day)


def universe_year_may_be_empty(year: int, day: date) -> bool:
    """Whether a lifecycle year with no event in it yet is admissible on calendar `day` (a
    `date_timezone` date): through `universe_year_empty_admissible_through(year)` itself, not
    after. Whether `day` is in `year` at all is the writer's question
    (`panel_ingest.record_empty_universe_year`)."""
    return day <= universe_year_empty_admissible_through(year)
```

`src/openalpha_cn/panel_ingest.py`：

①导入（`:437-442` 的 `openalpha_cn.domain.stock_universe` 块）：

```text
from openalpha_cn.domain.stock_universe import (
    STOCK_BASIC_DATASET,
    UNIVERSE_PANEL_COLUMNS,
    StockUniverse,
    stock_universe_from_panel_rows,
    universe_year_empty_admissible_through,
    universe_year_may_be_empty,
)
```
②`stock_universe_requirement` 的 `ReadinessRequirement(...)` 里加（`:1112-1113` 之间）：

```text
        # `V2-P7-025`: a lifecycle year recorded empty is readable only within that year -- the
        # writer's own rule (`record_empty_universe_year`).
        empty_admissible_through={
            year: universe_year_empty_admissible_through(year) for year in sorted(set(years))
        },
```

并在它的 docstring 末尾补一段：

```text
    `empty_admissible_through` is the third thing this requirement states, and it is not a
    waiver: a lifecycle year the writer recorded empty (`record_empty_universe_year`,
    `V2-P7-025`) is readable through its own last day and `stale` after it, because a whole
    market with no listing and no delisting for a year is a failed fetch rather than a quiet
    year (`catalog.ReadinessRequirement.empty_admissible_through`).
```

③新函数，放在 `write_empty_name_year`（`:7562`）之后：

```python
def record_empty_universe_year(
    store: PanelStore,
    batch: ColumnarPanelBatch,
    *,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> tuple[PartitionRef, ...]:
    """Record the observation year of a whole-registry fetch as empty, when the fetch carries no
    lifecycle row for it (`V2-P7-025`).

    `stock_basic` has no date filter: one request is the whole registry and
    `write_stock_universe` files it by the year each lifecycle event falls in. A year in which
    nothing has listed and nothing has delisted is therefore simply absent from the split -- the
    fetch succeeded and every year that did have events was written -- and a year with no
    partition is `partition_missing` to every reader, so a year's first sessions could not build
    factors (`factor_view._PanelInputs`) or read the weekly report's labels
    (`model_view._LabelInputs`). "No listing or delisting yet this year" is a true answer on
    those days, given honestly by a partition that says so and says when it was observed; the
    first write of a real row replaces it whole, and a stored empty year is recorded again at the
    later observation. What its coverage record says, and why it cannot answer stale data, is
    `_write_empty_partition`'s; the writer's expiry is
    `domain.stock_universe.universe_year_empty_admissible_through`.

    **Called after `write_stock_universe`, never instead of it.** That function keeps one job --
    write the years the fetch carries -- and its "every year vetted before any year is written"
    pass is untouched. A refusal here therefore leaves the fetched years written, which is an
    ordinary `panel build` outcome (the command names what it wrote) and not the hole in the
    lifecycle years that pass exists to prevent: the empty year is the newest, and an absent
    newest year is the state a store is in before its year's first event -- never a gap between
    two years a read covers.

    Refused (`PanelBatchError`) when it could hide something: a batch that is not a successful
    whole-registry fetch (a `no_data` answer carries no row at all -- an empty *market*, which
    `write_stock_universe` refuses first), a fetch whose observation day is past the year's
    window, and a year the store already holds rows for when this fetch is at or after the
    observation that wrote them (an empty answer over stored securities would unlist them). A
    fetch *older* than the stored year writes nothing for it and is not refused: that is the rule
    `write_stock_universe` states for a year that vanishes from a batch, and replaying an earlier
    `as_of` over a backfilled store must not destroy what a later one wrote.

    Returns the partitions it wrote -- none when the fetch carries the observation year, or when
    it is older than what the store holds for it.
    """
    if batch.dataset != STOCK_BASIC_DATASET:
        raise PanelBatchError(
            f"expected the {STOCK_BASIC_DATASET!r} dataset, got {batch.dataset!r}"
        )
    if batch.status != "success":
        raise PanelBatchError(
            f"cannot record a lifecycle year empty from a {batch.status!r} batch: "
            f"{batch.no_data_reason!r}; a whole-registry answer with no row at all is a failed "
            "fetch, not a year in which nothing happened"
        )
    zone = _resolve_timezone(date_timezone)
    day = batch.as_of.astimezone(zone).date()
    observation_year = day.year
    if not universe_year_may_be_empty(observation_year, day):
        raise PanelBatchError(
            f"{STOCK_BASIC_DATASET} lifecycle year {observation_year} would be recorded empty at "
            f"{batch.as_of.isoformat()}, which is past the year's window "
            f"({universe_year_empty_admissible_through(observation_year).isoformat()}); a whole "
            "market that has held no listing and no delisting for a year is a failed fetch"
        )
    by_year = split_panel_batch_by_year(batch, date_timezone=date_timezone)
    if not by_year:
        raise PanelBatchError(
            f"this {STOCK_BASIC_DATASET} fetch carries no lifecycle row at all, so it has no "
            f"year to take year={observation_year}'s columns from; a whole-registry answer with "
            "nothing in it is a failed fetch"
        )
    if any(year == observation_year for year, _yearly in by_year):
        return ()
    stored = store.read_coverage(STOCK_BASIC_DATASET, observation_year)
    if stored is not None and stored.row_count:
        if batch.as_of >= stored.as_of:
            raise PanelBatchError(
                f"{STOCK_BASIC_DATASET} year={observation_year} already holds "
                f"{stored.row_count} lifecycle row(s) observed at {stored.as_of.isoformat()}, and "
                f"this fetch at {batch.as_of.isoformat()} carries none for it; an empty answer "
                "now would replace them with nothing, which is a lost fetch and not a fact about "
                "the year"
            )
        return ()
    newest_year, newest = by_year[-1]
    template = panel_coverage(newest, year=newest_year, date_timezone=date_timezone)
    reason = (
        f"no {STOCK_BASIC_DATASET} listing or delisting in {observation_year} as of "
        f"{batch.as_of.isoformat()}; the year may be read empty through "
        f"{universe_year_empty_admissible_through(observation_year).isoformat()}"
    )
    return (
        _write_empty_partition(
            store,
            dataset=STOCK_BASIC_DATASET,
            year=observation_year,
            observed_at=batch.as_of,
            template=template,
            reason=reason,
            date_timezone=date_timezone,
        ),
    )
```

④两处 docstring：`_write_empty_partition` 的 `:7435-7437` 改成"`write_empty_announcement_year`（`V2-P6-018`）、`write_empty_name_year`（`V2-P7-021`）与 `record_empty_universe_year`（`V2-P7-025`）共享"；`panel/catalog.py:977-992` 的 `empty_admissible_through` docstring 补一句"a registry lifecycle year only through `domain.stock_universe.universe_year_empty_admissible_through` of its year (`panel_ingest.record_empty_universe_year`, `V2-P7-025`)`；`panel/store.py:1848-1854` 的 `allow_empty` docstring 补第三个门（仍全部经 `_write_empty_partition`）。

- [ ] **Step 4：运行，确认通过。** 同 Step 2 全绿。**先例的回归**：`uv run pytest tests/integration/panel/test_registry_ingest.py -k "rename_year or namechange" -q` 与改动前一致（`write_empty_name_year` 一行没改）；`uv run pytest tests/integration/panel/test_panel_readiness.py tests/integration/panel/test_panel_catalog.py -q` 全绿（`empty_admissible_through` 的通用规则没动）。

- [ ] **Step 5：变异探针**（每个都必须让指明的测试变红；改完恢复并 `cmp`）：
  - M1 `universe_year_may_be_empty`：`<=` → `<`（域测试与 `offset=0` 的读测试失败）。
  - M2 `record_empty_universe_year`：删掉 `if any(year == observation_year …): return ()`（`test_a_year_the_fetch_carries_is_never_recorded_empty` 与 `test_the_first_lifecycle_row_…` 失败）。
  - M3 `record_empty_universe_year`：`if stored is not None and stored.row_count:` → `if False:`（`…holds_securities` 失败，且那个年会被清空——这是本任务最贵的一条）。
  - M4 `record_empty_universe_year`：把 `if batch.as_of >= stored.as_of` 整块改成无条件 `raise`（`test_a_replay_older_than_the_stored_year_writes_nothing_for_it` 失败）。
  - M5 `stock_universe_requirement`：删掉 `empty_admissible_through=…`（`…recorded_empty_and_read_across_the_history`、`offset=0` 与 doctor 的 `ready` 失败）。
  - M6 `stock_universe_requirement`：表里的日期 `+ timedelta(days=1)`（`offset=1` 失败）。
  - M7 观测年改成 UTC：`day = batch.as_of.date()`（`…is_the_shanghai_year_of_its_observation` 失败）。
  - M8 `record_empty_universe_year` 的 `batch.status != "success"` 分支 → `if False:`（`…no_data…` 失败）。
  - **M9（记录为存活，原因如此）** `record_empty_universe_year` 里 `if not universe_year_may_be_empty(observation_year, day)` 那条拒绝：观测年由 `day.year` 推出，推荐值 `(12, 31)` 下它**不可能触发**，因此没有任何测试能杀死它。它存在是为了让 U1 的**其它取值**安全（用户若把窗口改成（1, 31），6 月的观测会被按名拒绝，而不是写一个读者立刻判 `stale` 的分区）。报告里写明"存活，原因如此"，不要为了让变异体变红去改常量。
  - M10 模板改成从库取最新有行年（`021` 的 `max(shaped, …)`）而不是批次自己的最新年——**不要求**，只记录：本实现取批次的列，与库的 schema 无关（§0.2 第 2 条）。

- [ ] **Step 6：门禁。** `uv run ruff check`（三个源文件 + 三个测试文件）、`uv run ruff format --check`、`uv run mypy src scripts`、`uv run lint-imports`（`domain-purity` 必须绿）；提交前 `tests/unit` 与 `tests/integration/panel`。

- [ ] **Step 7：提交：** `feat(V2-P7-025): a registry lifecycle year with no listing or delisting yet is recorded empty and read within its year`

---

## Task T2：`panel build` 记录空的登记簿年；第 2 步在新年首个会话写它（`V2-P7-025`）

依赖 T1。

**Files:**
- Modify: `src/openalpha_cn/cli.py`（导入：`:133` 的 `openalpha_cn.domain.stock_universe` 行加 `universe_year_empty_admissible_through`；`:263-269` 的 `panel_ingest` 导入块加 `record_empty_universe_year`；两个新函数 `_write_registry_year`、`_stock_universe_year` 放在 `_write_name_year`（`:5354-5380`）之后；`stock_basic` 分支 `:6140-6148`）
- Test: `tests/unit/scripts/test_daily_selection_survivor.py`（「New Year」一节）

**Interfaces：**
- 消费：`panel_ingest.write_stock_universe`（不变）、`panel_ingest.record_empty_universe_year`、`domain.stock_universe.universe_year_empty_admissible_through`。
- 产出：`cli._write_registry_year(store: PanelStore, batch: ColumnarPanelBatch, *, now: datetime) -> list[PartitionRef]`、`cli._stock_universe_year(store: PanelStore, batch: ColumnarPanelBatch, *, now: datetime) -> list[PartitionRef]`；`panel build` 的 stderr 在写空年时多一行，前缀固定为 `EMPTY stock_basic year=<Y>: no listing or delisting yet at <now>`。

- [ ] **Step 1：写失败的测试。** 在 `tests/unit/scripts/test_daily_selection_survivor.py` 的「New Year」一节末尾（`test_a_failed_or_truncated_name_fetch_of_the_new_year_is_still_refused_by_name` 之后）加。导入：`from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET, universe_year_empty_admissible_through`（若文件里已有 `STOCK_BASIC_DATASET` 就只补第二个名字）。

```python
# --- V2-P7-025: the first session of a year before its first listing or delisting ------------


def _registry_year_2027(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """`_new_year`'s store, but on a market in which nothing lists or delists in 2027: the
    registry has no 2027 lifecycle partition, which is the gap this issue closes. The name corpus
    keeps its 2027 rename, so only the registry is empty."""
    shop = NamesAcrossNewYear(open_days=NEW_YEAR, delisted=())
    return _seeded(tmp_path, monkeypatch, shop, as_of="2026-12-24T12:00:00+08:00", year=2026)


def _registry_build(store: Store, *, year: int, as_of: datetime) -> Any:
    """`openalpha panel build --dataset stock_basic --year <year>` at `as_of`, in process."""
    store.market.clear()
    store.market.today = as_of.astimezone(daily.SHANGHAI).date()
    arguments = ["panel", "build", "--runtime-dir", str(store.runtime), "--year", str(year)]
    arguments += ["--as-of", as_of.isoformat(), "--exchange", EXCHANGE, "--incremental", "--json"]
    arguments += ["--dataset", STOCK_BASIC_DATASET]
    return daily.invoke(arguments)


def test_the_first_session_of_a_year_with_no_registry_event_records_the_year_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P7-025`. 4 January 2027, and the whole registry carries no 2027 listing or delisting:
    step 2 records the lifecycle year empty -- no rows, observed at the evening's clock, its
    columns taken from the fetch's own newest year -- in the same 12 requests as before (the
    whole-registry answer already carries every year it has rows for), and the read every later
    step makes of 2027 is served. Before this, step 4's factor build refused
    `partition_missing ... stock_basic year=2027`."""
    store = _registry_year_2027(tmp_path, monkeypatch)
    assert 2027 not in store.panel.registered_years(STOCK_BASIC_DATASET)

    _panel, counts = _update(
        store,
        FIRST_2027,
        targets=daily.targets_for_survivor(SURVIVOR_BUILDS),
        survivor=_survivor(),
    )

    assert sum(counts.values()) == FIRST_SESSION_OF_A_YEAR
    coverage = store.panel.read_coverage(STOCK_BASIC_DATASET, 2027)
    assert coverage is not None and coverage.row_count == 0
    assert coverage.as_of == _evening(FIRST_2027)
    facts = _facts(store, FIRST_2027, monkeypatch)
    assert facts.facts


def test_the_build_that_records_an_empty_registry_year_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One request, exit 0, an `EMPTY` line naming the year and how long it may be read empty --
    printed after the write, so a refused write never prints it -- and the partition recorded."""
    store = _registry_year_2027(tmp_path, monkeypatch)
    through = universe_year_empty_admissible_through(2027)

    built = _registry_build(store, year=2027, as_of=_evening(FIRST_2027))

    assert built.exit_code == 0, built.reason()
    assert f"EMPTY {STOCK_BASIC_DATASET} year=2027: no listing or delisting yet at " in built.stderr
    assert f"read empty through {through.isoformat()}" in built.stderr
    assert store.market.payloads == [STOCK_BASIC_DATASET]
    coverage = store.panel.read_coverage(STOCK_BASIC_DATASET, 2027)
    assert coverage is not None and coverage.row_count == 0


def test_the_empty_year_is_registered_before_the_build_reads_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cli._stored_universe` demands a contiguous lifecycle window through the build's own year
    (`require_years_through`, `cli.py:4768-4774`), and the `stock_basic` branch runs before it
    (`cli.py:6140-6156`). Without the empty year that read refuses `does not read [2027]` before
    the build reaches a single statement -- which is why the record has to happen inside this
    build and not in some later command. The assertion calls the same function the statement
    targets call, on the store the build just left."""
    store = _registry_year_2027(tmp_path, monkeypatch)

    built = _registry_build(store, year=2027, as_of=_evening(FIRST_2027))

    assert built.exit_code == 0, built.reason()
    subjects = cli._stored_universe(store.panel, now=_evening(FIRST_2027))
    assert subjects and set(subjects) <= set(SECURITIES)
```

- [ ] **Step 2：运行，确认失败。** `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_daily_selection_survivor.py -k "registry_event or empty_registry_year or before_the_build_reads" -q`。**预期：** `records_the_year_empty` 的 `coverage is None`；`before_the_build_reads` 里 `cli._stored_universe` 的 `does not read [2027]`；`says_so` 的 `EMPTY` 断言失败（今天没有这一行）。

- [ ] **Step 3：最小实现**（`cli.py`）：

```python
def _write_registry_year(
    store: PanelStore, batch: ColumnarPanelBatch, *, now: datetime
) -> list[PartitionRef]:
    """Record the whole-registry fetch's observation year empty when the fetch carries no row for
    it (`panel_ingest.record_empty_universe_year`, `V2-P7-025`), and say so.

    The write of the years the fetch carries is `write_stock_universe`'s and runs before this, in
    `_stock_universe_year`; this function is the one addition, made after it because the empty
    year is not a year of the fetch -- a fetch that carries no row for the year is a fetch that
    says the year has had no event yet, and that is a partition, not an absence. A refused or
    truncated fetch never reaches here (`_fetch_panel` raises it as `provider_failure`), and a
    `no_data` whole-registry answer is refused by the writer by name. The line is printed after
    the write, so a refused write never prints it (`021`'s B4 ruling)."""
    refs = record_empty_universe_year(store, batch)
    for ref in refs:
        typer.echo(
            f"EMPTY {STOCK_BASIC_DATASET} year={ref.year}: no listing or delisting yet at "
            f"{now.isoformat()}; the year may be read empty through "
            f"{universe_year_empty_admissible_through(ref.year).isoformat()}; recorded as an "
            "empty partition",
            err=True,
        )
    return list(refs)


def _stock_universe_year(
    store: PanelStore, batch: ColumnarPanelBatch, *, now: datetime
) -> list[PartitionRef]:
    """`write_stock_universe` and then the empty observation year, as one step of `panel build`:
    the years the fetch carries first, the year it does not carry second."""
    return [*write_stock_universe(store, batch), *_write_registry_year(store, batch, now=now)]
```

`_build_targets` 的 `stock_basic` 分支改为：

```python
if STOCK_BASIC_DATASET in targets:
    # `--year` does not scope this one and cannot: ... (原注释不动)
    # `V2-P7-025`: the fetch's observation year is recorded empty when it carries no row for
    # it -- the year has had no listing and no delisting yet -- in the same invocation, so
    # every later step of this build (and `_stored_universe` below) reads a registry that
    # reaches the build's own year.
    written.setdefault(STOCK_BASIC_DATASET, []).extend(
        _stock_universe_year(store, _fetch_panel(provider, STOCK_BASIC_DATASET, as_of=now), now=now)
    )
```

- [ ] **Step 4：运行，确认通过。** 同 Step 2 全绿；再跑本文件的 New Year 与上一年两节 `-k "new_year or year_before or first_session"`（既有测试一字不改）与 `tests/unit/scripts/test_daily_selection_survivor_day.py -k "first_session_of_a_year_runs"`（`NewYearMarket` 有 2027 退市行，空年分支不触发）。

- [ ] **Step 5：变异探针：**
  - M1 `_stock_universe_year`：只返回 `write_stock_universe(...)`（不调 `_write_registry_year`）——三条测试都失败：前两条 `coverage is None`，第三条 `does not read [2027]`。
  - M2 `_write_registry_year`：把 `typer.echo(...)` 挪到 `record_empty_universe_year(...)` 之前——`says_so` 的"被拒时不打印"那一半没有测试，**接受**：本任务只为成功路径钉了打印；记录的缺口见 §8 的 C7。
  - M3 `_stock_universe_year`：先调 `_write_registry_year` 再调 `write_stock_universe`——`records_the_year_empty` 会通过（空年先写、行年后写覆盖它？不会：行年不含 2027，空年保留），所以**这条变异体存活**，报告里写明"顺序不影响结果，只影响 `_stored_universe` 与读者看到的时间点；本实现固定为先写行年，理由是空年是最新年、失败时留下的状态与事件发生前一致"。

- [ ] **Step 6：门禁**（`ruff`、`format`、`mypy src scripts`、`lint-imports`；提交前 `tests/unit`）。

- [ ] **Step 7：提交：** `feat(V2-P7-025): panel build records a lifecycle year with no listing or delisting yet, so a new year's first session completes`

---

## Task T3：幸存者一天在新年首个会话走完（`V2-P7-025`，端到端，生成夹具）

依赖 T2。

**Files:**
- Test: `tests/unit/scripts/test_daily_selection_survivor_day.py`（「New Year」一节末尾；新 module 夹具 `empty_registry_year`）

**Interfaces：** 只消费（`daily.main` 一天）；产出夹具 `empty_registry_year`（T4 复用，经 `from test_daily_selection_survivor_day import empty_registry_year`）。

- [ ] **Step 1：写失败的测试。**

```python
@pytest.fixture(scope="module")
def empty_registry_year(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The New Year market's panel through 31 December 2026, and 2027's calendar -- built on 31
    December's clock, because the seed's own observation year must be 2026: a seed made on 4
    January would record the empty 2027 registry year itself and the day would prove nothing.

    Nothing lists and nothing delists in 2027 and no 2027 rename is announced (`V2-P7-025`): the
    state the batch-1 fixture could only reach by removing the 4 January delisting and which the
    existing `new_year` fixture buries. The last line asserts the property this fixture exists
    for: the store holds no 2027 `stock_basic` partition."""
    runtime = tmp_path_factory.mktemp("survivor-empty-registry") / "runtime"
    market = EmptyNewYear(open_days=NEW_YEAR_DAYS, delisted=())
    market.today = date(2026, 12, 31)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TUSHARE_TOKEN", tds.SECRET_TOKEN)
        patch.setattr(cli, "_panel_transport", lambda: market)
        patch.setattr(cli, "_panel_clock", lambda: tds.WALL_CLOCK)
        for year, targets, as_of in (
            (2026, (*SEED_TARGETS, "namechange"), "2026-12-31T18:00:00+08:00"),
            (2027, ("trade_cal",), "2027-01-04T12:00:00+08:00"),
        ):
            arguments = ["panel", "build", "--runtime-dir", str(runtime), "--year", str(year)]
            arguments += ["--as-of", as_of, "--exchange", "SSE", "--json"]
            for target in targets:
                arguments += ["--dataset", target]
            built = daily.invoke(arguments)
            assert built.exit_code == 0, built.reason()
    assert 2027 not in PanelStore(runtime / "panel").registered_years(STOCK_BASIC_DATASET)
    return runtime


def test_the_first_session_of_a_year_with_no_listing_or_delisting_runs_to_its_evening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    empty_registry_year: Path,
) -> None:
    """`V2-P7-025`'s acceptance, and it is not a subset of `V2-P7-021`'s: 4 January 2027, a year
    in which nothing has listed, delisted or been renamed. Step 2 records both corpora's years
    empty in the same 11 requests -- the registry's costs none, because the whole-registry answer
    already carries every year it has rows for -- the doctor and the gate clear, step 4 builds the
    four factors against a registry read that names 2027 (it refused `partition_missing` without
    the empty year, which is what the batch-1 probe measured), the facts day is served, and the
    day runs to its record and its first evening. The next evening the empty registry year is
    recorded again at its clock."""
    market = EmptyNewYear(open_days=NEW_YEAR_DAYS, delisted=())
    world = _world(
        tmp_path,
        monkeypatch,
        empty_registry_year,
        config=survivor_config(epoch=date(2026, 12, 31)),
        market=market,
    )
    doctor = _calls(monkeypatch, daily, "check_panel")
    evening = evening_of(FIRST_2027)

    code, _out, err = _run(world, capsys, as_of=evening, clock=evening)

    assert code == 0, err
    assert Counter(world.market.payloads) == Counter(
        {
            TRADING_CALENDAR_DATASET: 1,
            STOCK_BASIC_DATASET: 1,
            ADJ_FACTOR_DATASET: 1,
            SUSPENSION_DATASET: 1,
            DAILY_DATASET: 1,
            DAILY_BASIC_DATASET: 1,
            PRICE_LIMIT_DATASET: 1,
            "namechange": 1,
            INDEX_DAILY_DATASET: 3,
        }
    )
    assert [(call["session"], call["first_of_year"]) for call in doctor] == [(FIRST_2027, True)]
    panel = PanelStore(world.runtime / "panel")
    coverage = panel.read_coverage(STOCK_BASIC_DATASET, 2027)
    assert coverage is not None and coverage.row_count == 0 and coverage.as_of == evening
    journal = _journal(world, FIRST_2027)
    assert journal["prediction"]["registered"] is True
    assert journal["prediction"]["scored"] > 0
    assert "result" in journal

    second = date(2027, 1, 5)
    _completed(world, capsys, second)
    again = panel.read_coverage(STOCK_BASIC_DATASET, 2027)
    assert again is not None and again.row_count == 0
    assert again.as_of == evening_of(second)
```

- [ ] **Step 2：运行，确认失败。** `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_daily_selection_survivor_day.py -k "no_listing_or_delisting" -q`（约 1–2 分钟）。**预期：** `exit_code == 1`，`err` 第一行以 `step 4 (factor build) failed: ` 开头，里面是 `the security registry could not be read …: ['partition_missing']` / `no partition is registered for stock_basic year=2027`（批次 1 的 B 轨在脚本化行情上量到的正是这一条）；夹具的 `assert 2027 not in …registered_years` **通过**（它钉的就是缺口本身）。**若它停在别处**（第 2 步、第 3 步 doctor、或别的错误码），停下报告 NEEDS_CONTEXT：那是本计划没预料到的路径，不要改夹具、也不要加豁免。

- [ ] **Step 3：实现。** 本任务**没有生产代码**——T1、T2 已经交付；若 Step 2 之后的实现需要改动，说明 T1/T2 有缺口，回到那两个任务补，不要在这里加豁免。

- [ ] **Step 4：运行，确认通过。** 同 Step 2 全绿；再跑本文件 New Year 一节 `-k "first_session_of_a_year or new_year or ex_rights"`（既有测试一字不改，`year_end`/`new_year` 夹具仍有 2027 行）。

- [ ] **Step 5：变异 / 夹具探针：**
  - M1 去掉空年（T1 的 M5 或 T2 的 M1）：本测试必须回到 Step 2 的拒绝。
  - M2 把夹具的市场改回 `EmptyNewYear(open_days=NEW_YEAR_DAYS)`（即保留 `NewYearMarket` 默认的那次 2027-01-04 退市）：夹具最后的 `assert 2027 not in registered_years` 必须变红——这是"夹具不再证明任何东西"的证人。（corrected 2026-11-07：M2 按原样**不生效**——脚本化行情按当天 `today` 过滤 `delisted`，把市场改回带 2027-01-04 退市的默认形态不会让这次退市进入当天的回答；实现者以 M2' 代替它，保留同一意图，替代探针记在实现者报告里，本计划的 M2 文字按原样不可执行。）
  - M3 把夹具的 2026 建库改成 `as_of="2027-01-04T12:00:00+08:00"`（原 `new_year` 的写法）：同一个断言必须变红，因为种子自己写了 2027 空年。**这条是本任务最容易踩的坑**，写成探针而不是注释。

- [ ] **Step 6：门禁**（本文件 `-k` 跑一节；提交前 `tests/unit`）。

- [ ] **Step 7：提交：** `test(V2-P7-025): the first session of a year with no listing or delisting runs to its evening`

---

## Task T4：前向周报跨年读到登记簿（`V2-P7-025`）

依赖 T3（复用 `empty_registry_year`）。

**Files:**
- Test: `tests/unit/scripts/test_forward_report_survivor.py`（导入块 `:56-59` 的 `from test_daily_selection_survivor_day import (…)` 加 `empty_registry_year`；`:69` 附近加 `from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET`；加在「the first session of a year, before its first rename (V2-P7-021)」一节之后）

**Interfaces：** 只消费 `_report`、`_document`、`_repository`、`settings_with`、`tds._bind`、`survivor_day.EmptyNewYear`、`survivor_day.FIRST_2027`、`PanelStore`。

- [ ] **Step 1：写失败的测试。**

```python
# --- a first session in a year with no listing or delisting (V2-P7-025) ----------------------

EMPTY_REGISTRY_REPORT: Final[datetime] = datetime(2027, 1, 6, 10, 0, tzinfo=SHANGHAI)
"""After 5 January's registration cutoff: the record of 4 January closed."""


@pytest.fixture(scope="module")
def first_session_with_an_empty_registry_year(
    tmp_path_factory: pytest.TempPathFactory,
    empty_registry_year: Path,
) -> tds.World:
    """The evening of 4 January 2027 under a registration whose epoch is 31 December 2026, on a
    market in which nothing has listed, delisted or been renamed: the first session of a year in
    which the registry has no lifecycle year of its own (`V2-P7-025`). The evening records the
    year empty; the panel it leaves is what the report reads."""
    root = tmp_path_factory.mktemp("survivor-empty-registry-report")
    repo, registration = _repository(
        root, survivor_day.survivor_config(epoch=date(2026, 12, 31)), settings=settings_with()
    )
    market = survivor_day.EmptyNewYear(open_days=survivor_day.NEW_YEAR_DAYS, delisted=())
    runtime = root / "runtime"
    shutil.copytree(empty_registry_year, runtime)
    world = tds.World(repo=repo, registration=registration, runtime=runtime, market=market)
    with pytest.MonkeyPatch.context() as patch:
        tds._bind(patch, repo, market)
        code, _out, err = _daily(world, survivor_day.FIRST_2027)
        assert code == 0, err
    return world


def test_a_report_across_a_new_year_reads_the_registry_year_the_first_evening_recorded(
    first_session_with_an_empty_registry_year: tds.World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`V2-P7-025`'s second reader: the report labels its records through
    `model_view._LabelInputs`, which reads the registry over the years from the first record's
    through the report's -- 2027 alone here -- and a year with no partition is
    `partition_missing`. With the empty year the first evening recorded, the report runs and its
    one record is witnessed across the year boundary."""
    runtime = tmp_path / "runtime"
    shutil.copytree(first_session_with_an_empty_registry_year.runtime, runtime)
    market = survivor_day.EmptyNewYear(open_days=survivor_day.NEW_YEAR_DAYS, delisted=())
    world = dataclasses.replace(
        first_session_with_an_empty_registry_year, runtime=runtime, market=market
    )
    tds._bind(monkeypatch, world.repo, market)

    code, _out, err = _report(world, capsys, as_of=EMPTY_REGISTRY_REPORT)

    assert code == 0, err
    records = _document(world, EMPTY_REGISTRY_REPORT)["records"]
    assert (records["first_session"], records["witnessed"]) == ("2027-01-04", 1)


@pytest.mark.parametrize(
    ("damage", "code_word"),
    [("file", "partition_file_missing"), ("catalog", "partition_missing")],
    ids=["the file deleted, the catalog still naming it", "the year never registered"],
)
def test_a_report_across_the_year_names_a_registry_year_it_cannot_read(
    first_session_with_an_empty_registry_year: tds.World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    damage: str,
    code_word: str,
) -> None:
    """The roadmap's probe, made a test, and the state it could only infer: deleting the partition
    file while the catalog still names it is `partition_file_missing`; forgetting the year in the
    catalog is `partition_missing`. Either way the report is refused by name, exit 1, and writes
    no report file."""
    runtime = tmp_path / "runtime"
    shutil.copytree(first_session_with_an_empty_registry_year.runtime, runtime)
    panel = PanelStore(runtime / "panel")
    if damage == "file":
        (panel.root / STOCK_BASIC_DATASET / "2027" / "data.parquet").unlink()
    else:
        panel.remove_partition(STOCK_BASIC_DATASET, 2027)
    market = survivor_day.EmptyNewYear(open_days=survivor_day.NEW_YEAR_DAYS, delisted=())
    world = dataclasses.replace(
        first_session_with_an_empty_registry_year, runtime=runtime, market=market
    )
    tds._bind(monkeypatch, world.repo, market)

    refused, out, err = _report(world, capsys, as_of=EMPTY_REGISTRY_REPORT)

    assert refused == 1
    assert out == ""
    assert code_word in err
    assert not (runtime / "reports" / report_name(EMPTY_REGISTRY_REPORT)).exists()
```

（`code_word` 而不是 `code`：参数化名与 `_report` 的返回值区分开；`report_name` 是本文件已有的帮助函数，`:173` 附近用到过。）

- [ ] **Step 2：运行，确认失败。** `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_forward_report_survivor.py -k "empty_registry or across_the_year" -q`。**预期：** 夹具在 `_daily(world, FIRST_2027)` 的 `assert code == 0` 处失败（也就是 T3 修复前的第 4 步拒绝）——**先确认这个夹具失败与 T3 的 Step 2 是同一条**，再实现；实现（T1、T2 已交付）之后，参数化的两例应是 `partition_file_missing` / `partition_missing`，第一例通过。

- [ ] **Step 3：实现。** 本任务**没有生产代码**。

- [ ] **Step 4：运行，确认通过。** 同 Step 2 全绿；再跑 `-k "first_session_of_a_year or new_year"` 一节（`first_session_evening` 仍用 `new_year` 的夹具、仍有 2027 退市行，必须一字不改地绿）。

- [ ] **Step 5：变异探针：**
  - M1 T1 的 M5（删掉 `empty_admissible_through`）：**晚间的第 2 步先以 `stale` 拒绝**，周报根本不会运行（corrected 2026-11-07：原文写"周报被拒"，实测是晚间停下；三个新测试都被杀掉）。
  - M2 T2 的 M1：**晚间的第 4 步以 `partition_missing` 拒绝**，周报根本不会运行（corrected 2026-11-07：原文写"周报被拒"；拒绝点是晚间的第 4 步）。
  - M3 把 `EMPTY_REGISTRY_REPORT` 改成 `datetime(2028, 1, 6, …)`：第一例**到不了 `stale`**——2028 的日历检查先触发（corrected 2026-11-07：原文的预期不可达），**不要求**，只记录：那是"一年之后仍照答"的失败模式的一个旁证。

- [ ] **Step 6：门禁**（本文件 `-k` 一节；提交前 `tests/unit`）。

- [ ] **Step 7：提交：** `test(V2-P7-025): the weekly report reads the registry across a new year with no registry event`

---

## Task T5：运行手册与模块 docstring（`V2-P7-025`，只有文字）

依赖 T2（`EMPTY` 文案定稿）。可与 T3/T4 并行。

**Files:**
- Modify: `docs/runbooks/daily-selection.zh-CN.md`（第 608 行「已知局限（幸存者形态）」第 1 条、第 823 行「不跑 2027 年第一个交易日」）
- Modify (docstring only, **受绑定**): `scripts/daily_selection.py`（约 110-115 行那段）

- [ ] **Step 1：改运行手册。** 第 608 行末段"**登记簿 `stock_basic` 没有这个豁免**……还没有测过"改成：登记簿的同一缺口已由 `V2-P7-025` 关闭——整份登记簿抓取成功、观测年没有上市/退市时，`panel build` 把该生命周期年记成零行分区（输出里有一行 `EMPTY stock_basic year=…`），所有读者（第 3 步 doctor、第 4 步因子构建、周报的标签读取、事实适配器之外的一切）**只在到当年 12 月 31 日（含，上海日历日）为止**把它读作"今年还没有上市或退市"，当年第一条真实事件到来时零行分区被整份替换；失败或截断的抓取、`no_data` 的整份回答、过了窗口的空回答、已有行的年仍按名拒绝。第 823 行"**不跑 2027 年第一个交易日**，除非 `V2-P7-025` 已合入"改成"`V2-P7-025` 已合入，2027-01-04 可以跑；登记簿当年若已有分区则空年分支不触发"（corrected 2026-11-07：这句改写把合并状态写进了运行手册，而分支未推送、issue 未关闭；运行手册保持条件句——仍不跑 2027 年第一个交易日，除非 `V2-P7-025` 已并入主线、用户同意且真实运行确认了它的行为；合并状态只在收尾记录与路线图里说）。
- [ ] **Step 2：改 `scripts/daily_selection.py` 的模块 docstring**（约 110-115 行）：把"登记簿有同样的缺口、`V2-P7-021` 没有关闭它"改成已关闭的一段，保留"重跑撞同一 `--as-of`"的教训，并写明窗口与 `EMPTY` 行。**只改 docstring**。
- [ ] **Step 3：证明 AST 不变。** 用 `python - <<'PY'` 在**改前**的字节上取 `ast.dump(ast.parse(source))`（跳过模块 docstring：`tree.body[0]` 若是 `ast.Expr` 且 `ast.Constant`，从 `tree.body[1:]` 起算），存到 `<scratch>/before.txt`；改后同样算一次，`diff` 必须为空。命令与批次 1 的 B5 相同（`PYTHONDONTWRITEBYTECODE=1`）。把这次 `diff` 为空写进提交信息。
- [ ] **Step 4：检查没有别处复述旧说法。** `command grep -rn "V2-P7-025" docs scripts src | cat`——每一处要么是历史记录（路线图、规格 §9、批次 1 的计划与收尾记录，**不改**），要么是本次改过的两处；`docs/runbooks/forward-report.zh-CN.md` 若提到该缺口一并改。`docs/HANDOFF_CURRENT.md` 与 CHANGELOG 由控制者统一改，本任务不碰（本文件末节最后一条）。
- [ ] **Step 5：提交：** `docs(V2-P7-025): the runbook and the daily command's module notes record that the registry's new-year gap is closed`

---

## §8 本计划明确不做的

- **C7**：`EMPTY` 行"被拒时不打印"只有一半被测试钉住（成功路径打了、拒绝路径没有测试）。要补需要一个新的拒绝夹具（一个库里已有 2027 行、抓取更晚的市场），成本高于价值；记录的代价是：若实现把打印挪到写入之前，成功路径仍绿，而一条被拒的构建会先印一行 `EMPTY`。Task 2 的 M2 把它列为"接受"。
- **台账**：新测试只加进已有文件，不触发硬规则 9 的三件产物；建议的新台账行（`OA-PANEL-044`，"面板允许空的登记簿生命周期年"）与 `OA-OPS-050`（新年首晚的运行前提）按批次 1 的先例留给收尾记录。
- **`docs/HANDOFF_CURRENT.md`、`CHANGELOG.md`、路线图的行状态**：由控制者在合入时统一改。
- **不是本 issue 的范围**：`002`/`003`（`_registry_year` 的更一般化）、`021` 的取值确认、`024` 的读数。
- **不跑任何真实请求**：本计划全部在脚本化行情与测试时生成的夹具上；0 次请求的测量已经完成（`.superpowers/sdd/batch-2/measurement-registry-year.md`），不需要新的测量。
