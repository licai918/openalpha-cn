# 选股就绪（P6）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 OpenAlpha CN 从「工程上能跑通」推进到「选股 ready」：有多年全市场点时间数据、每日可增量更新、21 个因子在样本外被诚实检验、选出的组合在一次性保留期上接受检验，并且每天收盘后一条命令产出当日候选榜、目标权重和先于结果登记的预测。

**Architecture:** 三条线顺序推进，每条线末尾有闸门。①取数线：先在线探查 Tushare 按期批量接口，再落地更快的财报取数与每日增量更新，然后回填 2013–2026。②计算线：把因子构建的写路径与读路径提速（结果逐字节等价），再算全量因子。③研究线：先建扣成本的策略回测模块和研究编排器，预登记研究协议，按「研究期 → 验证期 → 一次性保留期 → 前向期」执行。最后接上每日运行与前向跟踪。

**Tech Stack:** Python 3.11、DuckDB（仅限 `panel/` 与存储层）、Typer CLI、pytest；无新增运行时依赖（ADR-0003：运行时依赖固定为九个，不引入 numpy/pandas/pyarrow）。

## Global Constraints

- 每个改动在分支、提交、PR 中写明 issue 号；本计划的 issue 号为 `V2-P6-001` … `V2-P6-012`（Task 0 写进路线图）。
- `domain/` 不得导入 numpy、pandas、sqlite3、duckdb；`backtest/` 不得导入数值栈或面板平面（`lint-imports` 8 kept / 0 broken 不许变）。
- 面板数据只进分区面板存储，永不进 `ParquetEvidenceStore`。
- 面板 fixture 在测试时生成；`.parquet`、`.duckdb`、`.sqlite3`、`.db` 不得签入。
- 所有提速改动必须**结果等价**：同一输入产出的分区 `content_hash` 相同、读回行逐行相同。等价不成立就不合并。
- 研究数据只写 `~/openalpha-research`（下称 `$RT`）。不写仓库内 `runtime/`，不删 `runtime/backups/`。
- 永不读 `.env`、永不打印 token；token 只经 `uv run --env-file .env` 进入子进程，且在 `cd` 到仓库根的子 shell 脚本里用相对路径 `.env`（仓库路径含空格）。
- 联网只发生在取数任务与 e2e；非 e2e 测试零网络。每次联网任务在报告里写出实际请求数。
- 研究诚实：候选榜的对外措辞保持「候选」。工程完成不等于研究有效；只有预登记的样本外指标、扣成本增量价值、多重检验控制、结果已知前落库的预测才算证据。
- 保留期只跑一次。保留期结果出来后，不得再以它为依据调整任何参数；任何后续改动只能用前向期数据检验。
- 每次提交前跑 `tests/unit`；合并前跑合并门（`tests/unit`、9 个文档/图守卫、`tests/replay`、静态五门）。
- 不通过 git hook 或 `git rebase --exec` 跑测试；全量测试串行，不并发跑多套。
- 移动测试文件是三件套改动（`features.csv`、`summary.json`、台账同一提交重生成）。

---

## 1. 「选股 ready」的验收定义

全部满足才算完成，每一条都有可核对的产物。

| # | 条件 | 核对方式 |
|---|---|---|
| R1 | `$RT` 面板覆盖 2013-01-01 至最近一个已收盘交易日，14 个构建目标齐全 | `openalpha panel doctor` 逐年 clean；`openalpha data-check` 退出 0 |
| R2 | 每日增量更新：一个交易日的更新 ≤ 60 次 Tushare 请求、≤ 10 分钟，结果与全量重建 `content_hash` 相同 | Task 3 的等价测试 + 一次实测记录 |
| R3 | 21 个因子 × 三档在 2015-01 至今的每个交易日都有截面 | 因子 manifest 分区的时点计数 = 交易日数 |
| R4 | 研究期、验证期、保留期三阶段报告齐全；研究期每个被试配置都进入多重检验家族；保留期有早于运行的预登记提交 | `docs/research/p6-*.md` + 研究账本 JSONL + git 历史 |
| R5 | 每天一条命令产出当日候选榜、目标权重和登记预测；幂等；有 runbook | `scripts/daily_selection.py` 实跑两次，第二次无新增写入 |
| R6 | 前向跟踪：每周报告已登记预测的实际表现（扣成本超额、sign-flip p 值） | `scripts/forward_report.py` 输出 |
| R7 | 交付标准六条仍然成立（CI 七绿、e2e、全量 0 skip/0 warning、无不实声称、台账对码、静态门 0） | 同 `v2-delivery-plan.md` |

**关于 R4 的结果本身：** 「选股 ready」要求研究被诚实地做完，**不要求**结果一定显著。如果保留期不通过，报告就如实写不通过，不会降低标准，也不会回头在保留期上调参。那时不逊于原方案的替代路径只有一条：扩充假设（新因子、新组合器）并在研究期与验证期上检验，然后用前向期数据做下一次保留检验。这是本计划唯一不能用工程手段「保证成功」的环节。

## 2. 研究诚实协议（Task 12 预登记前不得改动，改动须在协议里留版本记录）

**时间切分**（交易日口径，切点之间留 `horizon` 个交易日禁区，防止标签跨界）：

| 段 | 区间 | 用途 |
|---|---|---|
| 预热 | 2013-01-01 – 2014-12-31 | 回看窗口（最长 125 个交易日）、TTM 所需的四个报告期 |
| 研究期 | 2015-01-05 – 2021-12-31 | 单因子筛选、组合与参数寻优；walk-forward 在期内进行 |
| 验证期 | 2022-01-04 – 2023-12-29 | 在研究期胜出的 ≤ 5 个配置中选一个 |
| 保留期 | 2024-01-02 – 最后一个标签完整的交易日 | 一次性检验，预登记后只跑一次 |
| 前向期 | 预登记日之后 | 每日登记预测，结果揭晓后评估 |

**什么是「参数」、什么是「测量设置」：**
- 参数（可以寻优，每个被试值都计入家族）：因子档位、预测期（1d/5d/20d）、调仓频率、组合方式与权重、模型超参、持仓数、换手缓冲带、单票与行业上限。
- 测量设置（在协议里一次定死，不寻优）：分组数 5、每组最少 20 只、单票资金 100,000 元、成交额参与上限 1%、成本（佣金万 2.5 双边、最低 5 元，印花税卖出千 0.5，滑点单边 10bp）、基准（中证 500 `000905.SH` 与全 A 等权两条并列）。按结果去挑测量设置就是 p-hacking。

**显著性：** 对每个配置取不重叠期样本（每 `horizon` 个交易日取一个时点），对「逐期 rank IC」和「逐期扣成本超额收益」分别做 `sign_flip_test`（双侧，`bootstrap_samples=100000`，`random_seed=20260926`）。同一阶段的全部 p 值交给 `control_false_discovery_rate`，`dependence="arbitrary"`（Benjamini–Yekutieli），q = 0.10。家族大小等于该阶段研究账本里的行数，由编排器自动计算，不手填。

**选择指标（预先声明）：** 验证期按「扣成本超额收益的年化信息比率」选；并列时取换手更低者。

**保留期通过标准（预先声明）：** 扣成本年化超额收益 > 0，且不重叠期超额收益的单侧 sign-flip p < 0.05，且最大相对回撤不超过验证期最大相对回撤的 2 倍。三条同时满足才写「通过」。

## 3. 今天实测的基线（2026-09-26，`eade0de`）

- 本地 `runtime/panel`：日线 5,571 只、2026-01-05 – 2026-08-28，只有 2026 一年；只建过 `reversal_1d` 一个因子。
- `panel build` 的帮助文字给出的成本：价量类约 2,900 次请求/年；财报四个接口按证券逐只拉取，约 23,500 次/年，2015–2026 约 282,000 次；单次延迟 1.1–4.6 秒；账户配额 500 次/分钟（配额不是瓶颈，延迟才是）。
- 因子构建（草稿目录中的面板副本，`reversal_5_sessions/v1` raw，20 个时点，全市场）：96 秒，约 4.8 秒/时点。剖析显示 45 秒中 24 秒花在 `duckdb.executemany`（`panel/store.py` 的 `write_partition`），12 秒花在 `panel_factors._read_dataset`（逐时点整年重读、逐行 `pytz.timezone` 查找 378 万次）。
- 写入替代方案的实测（116,880 行 × 10 列）：`executemany` 10.84 秒；多行 `VALUES`（500 行/条）2.35 秒；列式 `unnest(?::T[])`（20,000 行/批）0.88 秒；三者读回结果逐行相同。
- 中性化档构建要求 `--year` 列出 `index_member_all` 存有的全部年份（1984 起），否则按名拒绝。
- 本机 10 核、24 GB 内存、磁盘可用 62 GB。因子观测分区约 42 KB/时点/档，全量（2,900 时点 × 21 因子 × 3 档）约 7.7 GB。
- `model evaluate` 拒绝中性化档；它报的是逐折 rank IC，没有扣成本的组合收益。仓库里没有「组合打分的多年扣成本回测」。
- `jobs run` 只做无网络的健康检查，不取数；`panel build` 没有年内增量，当前年每天重建要重新拉全年。

## 4. 不 fallback 规则：每个可能失败的点、它的替代方案、以及替代方案不逊于原方案的证明

| 最优方案 | 可能失败的方式 | 替代方案 | 为什么不逊于原方案（必须实测） |
|---|---|---|---|
| 财报用按期批量接口（`income_vip` 等，每期一次拉全市场） | 账户无权限，或返回行与逐证券接口不等价 | 逐证券接口 + 有界并发（≤ 450 次/分钟令牌桶） | 同一 `(dataset, year)` 分区两种取法 `content_hash` 相同 |
| 日更财报用公告日期窗口批量拉取 | 接口不支持按公告日期过滤 | 按期拉取最近两个报告期并与已存行合并 | 增量结果与全量重建 `content_hash` 相同 |
| 全部交易日的日频因子截面 | 提速后全量仍超过 48 小时 | 子进程池并行计算、主进程单写（避开跨进程写目录） | 分区 `content_hash` 与串行版相同 |
| 用 `model evaluate` 组合因子 | 它拒绝中性化档 | 新的策略回测模块直接读任意档的已存打分做组合 | 同一套成本、整手、涨跌停、停牌口径下的扣成本收益 |
| launchd 定时每日运行 | 你不同意安装常驻配置 | 一条手动命令 | 产物完全相同 |

执行中遇到这张表之外的失败，按同样规则处理：先查根因，再找一个用实测证明不逊于原方案的替代方案，写进本表后再继续。

## 5. 成本与时间估算（执行时以实测为准，偏差超过 50% 就停下来报告）

| 项 | Tushare 请求 | 墙钟时间 |
|---|---|---|
| Task 1 在线探查 | ≤ 40 | 10 分钟 |
| Task 4 价量类回填 2013–2026（14 年） | 约 41,000 | 约 23 小时（串行，后台） |
| Task 4 财报回填，按期路径 | ≤ 1,500 | ≤ 2 小时 |
| Task 4 财报回填，并发逐证券路径（仅当按期路径不可用） | 约 330,000 | 约 13 小时 |
| Task 8 全量因子（提速后目标 ≤ 0.5 秒/时点/档） | 0 | 约 25 小时 |
| 研究三阶段 | 0 | Task 9 完成后实测再估 |
| 之后每天 | ≤ 60 | ≤ 10 分钟 |

长时间运行期间用 `request_keep_awake` 保持本机唤醒。

---

## Phase 0：立项

### Task 0：路线图 P6 与研究运行目录

**Files:**
- Modify: `docs/specs/v2/openalpha-cn-v2-roadmap.md`（总览表加一行 P6；文末加 P6 节）
- Modify: `docs/HANDOFF_CURRENT.md:9-11`（「进行到 P4」已过时：P4 114 行、P5 70/71 行已标完成）
- Test: `tests/unit` 全量（路线图与交接文档受文档守卫约束）

- [ ] **Step 1：建分支**

```bash
git switch -c feat/v2-p6-selection-ready
```

- [ ] **Step 2：路线图总览表在 P5 行下加一行，合计行改为 205**

```markdown
| **P6** | 选股就绪 | 12 | — | — | 多年面板 + 日更等价 + 预登记保留期检验 + 每日一条命令 |
```

- [ ] **Step 3：路线图文末加 P6 节，12 行，列与其他阶段相同（编号 | 标题 | 类型 | 依赖 | 证据 | 验收 | PRD）**

```markdown
## P6 选股就绪

| 编号 | 标题 | 类 | 依赖 | 证据 | 验收 | PRD |
|---|---|---|---|---|---|---|
| `V2-P6-001` | Tushare 按期批量接口在线探查 | 研 | — | 财报逐证券回填约 282,000 次请求（`cli.py` `panel_build` 帮助） | 探查报告 + 决策 | S12 |
| `V2-P6-002` | 财报按期批量取数（或有界并发逐证券取数） | 技 | 001 | 同上 | 两种取法分区 `content_hash` 相同 | S12 |
| `V2-P6-003` | 面板年内增量日更 | 技 | 002 | 当前年每天重拉全年（`--resume` 无年内续传） | 增量与全量 `content_hash` 相同 | S12 |
| `V2-P6-004` | 分区写入改列式批量插入 | 技 | — | `executemany` 占因子构建 53% 耗时 | 分区逐行相同、`content_hash` 相同 | — |
| `V2-P6-005` | 因子读路径提速 | 技 | 004 | `_read_dataset` 逐时点整年重读 | 因子分区 `content_hash` 相同 | — |
| `V2-P6-006` | 因子计算并行（条件触发） | 技 | 005 | 提速后全量仍超 48 小时时才做 | 分区 `content_hash` 与串行相同 | — |
| `V2-P6-007` | 扣成本的多年策略回测 | 技 | — | 仓库无组合打分的多年扣成本回测；`model evaluate` 拒绝中性化档 | 手算夹具逐期收益相同 | S53 |
| `V2-P6-008` | 研究编排器与预登记守卫 | 技 | 007 | 家族大小不能手填；保留期只能跑一次 | 守卫拒绝未登记与重复运行 | S30 |
| `V2-P6-009` | 研究协议预登记 | 研 | 008 | — | 协议提交早于任何研究期运行 | S30 |
| `V2-P6-010` | 三阶段研究执行与报告 | 研 | 009 | — | 三份报告 + 研究账本 | S30 |
| `V2-P6-011` | 每日选股一条命令 | 技 | 003,010 | — | 实跑两次幂等 | S80 |
| `V2-P6-012` | 前向跟踪周报 | 技 | 011 | — | 对已登记预测报扣成本超额与 p 值 | S30 |
```

- [ ] **Step 4：改 `docs/HANDOFF_CURRENT.md` 的「当前状态」首段**，写成「v2 已完成 P0.A–P5（P5 仅剩 `V2-P5-038`），当前工作是 P6 选股就绪，计划见 `docs/superpowers/plans/2026-09-26-selection-ready.md`」。

- [ ] **Step 5：跑 `tests/unit` 与文档守卫**

```bash
uv run pytest tests/unit -q -p no:cacheprovider
uv run pytest $(git grep -l -E 'prose_clauses|diagram_text' -- 'tests/integration/*.py') -q -p no:cacheprovider
```
Expected: 全部通过。守卫若因路线图计数（113/193 等）变红，按守卫指向的句子改数字，不改守卫。

- [ ] **Step 6：建研究运行目录并提交**

```bash
mkdir -p ~/openalpha-research
git add docs/specs/v2/openalpha-cn-v2-roadmap.md docs/HANDOFF_CURRENT.md docs/superpowers/plans/2026-09-26-selection-ready.md
git commit -m "docs(V2-P6-001..012): P6 selection-ready phase and plan"
```

---

## Phase A：取数

### Task 1（V2-P6-001）：在线探查 Tushare 按期批量接口

纯探查，不改 `src/`。脚本放会话草稿目录，报告写 `.superpowers/sdd/p6-vip-probe.md`。

**要回答的问题：**
1. 账户能否调用 `income_vip`、`balancesheet_vip`、`cashflow_vip`、`fina_indicator_vip`（按 `period` 拉全市场）。
2. 对同一报告期，VIP 接口返回的行与逐证券 `income` 等返回的行是否**完全相同**：比较全部列，重点是 `ann_date`、`f_ann_date`、`update_flag`、`report_type`、`end_date` 以及 21 个因子读取的数值列（`n_income_attr_p`、`total_revenue`、`oper_cost`、`n_income`、`total_hldr_eqy_exc_min_int`、`total_assets`、`total_cur_liab`、`n_cashflow_act`、`profit_dedt`）。
3. 单次请求的行数上限与分页方式（`limit`/`offset`），一个报告期要几次请求。
4. VIP 接口能否按 `ann_date` 或 `start_date`/`end_date`（公告日期窗口）过滤全市场——这决定 Task 3 的日更路径。

- [ ] **Step 1：写探查脚本** `$SCRATCH/p6_probe.py`，复用仓库的 `TushareTransport`（不自己拼 HTTP、不读 `.env`），对 2019-12-31 与 2023-06-30 两个报告期：
  - 每个 VIP 接口按 `period` 拉一次（必要时分页）；
  - 在该期全市场结果中按固定种子 `20260926` 抽 30 只证券，另加 5 只已知有 `f_ann_date > ann_date` 的证券（从 VIP 结果里挑），用逐证券接口各拉一次；
  - 逐证券比对：两边行集合（以全部列组成的元组为元素）是否相等，打印差异行；
  - 用 `ann_date` 窗口（例如 20240425–20240430）试一次 `income_vip`，记录是否支持。
  - 全程计数请求数，上限 40 次，超过即停。

- [ ] **Step 2：运行**（在 `cd` 到仓库根的子 shell 里）

```bash
( cd "/Users/licai918me.com/Documents/OpenAlpha cn" && uv run --no-sync --env-file .env python "$SCRATCH/p6_probe.py" ) > "$SCRATCH/p6_probe.out" 2>&1; echo "exit=$?"
```
输出中若出现 32 位以上十六进制串，写报告前打码。

- [ ] **Step 3：写报告并做决定**，报告包含：每个接口是否可用、每期请求数、逐证券比对结果（相等 / 差异行明细）、公告日期窗口是否可用、本次实际请求数。决定规则：
  - 四个接口都可用且逐证券比对全部相等 → Task 2 走 **A 路径（按期）**；
  - 任何一个不可用，或比对有差异且差异不能由已知字段解释 → 该接口走 **B 路径（有界并发逐证券）**；
  - 公告日期窗口可用 → Task 3 财报日更走窗口；不可用 → 走「最近两个报告期按期拉取 + 合并」。

### Task 2（V2-P6-002）：财报快速取数

两条路径只实现 Task 1 决定的那条（四个接口可以分别决定）。两条路径的验收相同：**对同一组证券、同一年，新取法写出的分区与旧的逐证券串行取法写出的分区 `content_hash` 相同。**

**Files（A 路径）：**
- Modify: `src/openalpha_cn/providers/tushare.py`（新增四个 VIP 描述符，请求主体是 `period`；在描述符表附近）
- Modify: `src/openalpha_cn/cli.py:3117-3150`（`_build_statement_panel` 增加按期分支：由 `stock_basic` 已存登记簿之外的「年份 → 报告期列表」驱动，不再按证券循环）
- Test: `tests/unit/providers/` 下新建的 `test_tushare_statement_periods.py`（新建）
- Test: `tests/unit/` 下新建的 `test_cli_panel_build_statement_sweep.py`（新建）

**Files（B 路径）：**
- Create: `src/openalpha_cn/providers/rate_limit.py`（令牌桶：`TokenBucket(capacity: int, per_seconds: float, clock, sleep)`，`acquire() -> None`）
- Modify: `src/openalpha_cn/cli.py` 的 `_subject_batches`（用 `ThreadPoolExecutor(max_workers=8)` 并发，令牌桶限流 450/60s，结果按原证券顺序归并）
- Test: `tests/unit/providers/` 下新建的 `test_rate_limit.py`、`tests/unit/` 下新建的 `test_cli_subject_batches_concurrent.py`（新建）

**Interfaces:**
- Produces: `openalpha panel build --dataset income --year Y` 的行为与参数不变，只是更快；`--subject` 语义不变（点名证券时仍走逐证券）。

- [ ] **Step 1：写失败测试（A 路径）**——用仓库现有的假传输（`tests/` 里的 `FakeTransport` 共享 fixture），给定一期全市场响应 `R` 与同一批证券的逐证券响应 `R_i`（`R` 是 `R_i` 的并集），断言：

```python
def test_period_sweep_and_subject_sweep_write_the_same_partition(tmp_path):
    by_subject = build_statement_year(tmp_path / "a", transport=fake_by_subject, sweep="subject")
    by_period = build_statement_year(tmp_path / "b", transport=fake_by_period, sweep="period")
    assert by_period.content_hash == by_subject.content_hash
    assert read_rows(by_period) == read_rows(by_subject)
```
另写两条：分页响应（第二页非空）被完整拼接；某一期全市场返回 0 行时按现有规则拒绝（与 `_build_statement_panel` 的「全部证券都没有 filing」同一个退出码）。

- [ ] **Step 1'：写失败测试（B 路径）**

```python
def test_token_bucket_never_exceeds_its_rate():
    clock = FakeClock()
    bucket = TokenBucket(capacity=450, per_seconds=60.0, clock=clock.now, sleep=clock.advance)
    stamps = []
    for _ in range(1000):
        bucket.acquire()
        stamps.append(clock.now())
    for i in range(len(stamps) - 450):
        assert stamps[i + 450] - stamps[i] >= 60.0


def test_concurrent_subject_sweep_returns_batches_in_subject_order_and_same_partition(tmp_path):
    serial = build_statement_year(tmp_path / "a", transport=fake_slow_random_latency, workers=1)
    parallel = build_statement_year(tmp_path / "b", transport=fake_slow_random_latency, workers=8)
    assert parallel.content_hash == serial.content_hash
```
另写：一个证券抛 `rate_limit`（40203）时按现有退避规则重试且不丢行；一个证券抛不可重试错误时整次构建按现有规则失败，已完成的证券不写半个分区。

- [ ] **Step 2：跑测试确认失败**

```bash
uv run pytest tests/unit/providers tests/unit -k 'statement_periods or statement_sweep' -q -p no:cacheprovider
```
Expected: FAIL（函数或参数不存在）。

- [ ] **Step 3：实现到测试通过**，遵守：凭据仍只在 `TushareProvider` 构造函数里解析；`ProviderFailure` 的消息仍不打印；`_echo_budget` 打印新的请求数估算；帮助文字里「~282,000」那段按新成本改写（它受文档守卫约束，数字要与实现一致）。

- [ ] **Step 4：跑测试通过，再跑 `tests/unit` 全量**

- [ ] **Step 5：在线等价实证**（进入合并前必须做）：在一个临时运行目录里，对 Task 1 抽样的 35 只证券、2019 与 2023 两个公告年，旧取法与新取法各建一次 `income`、`balancesheet`、`cashflow`、`fina_indicator`，打印两边分区 `content_hash`，必须逐一相等。记录请求数。

- [ ] **Step 6：独立评审后提交**

```bash
git add <本任务改动的文件，逐个列出>
git commit -m "feat(V2-P6-002): statement backfill by period sweep, same partitions"
```

### Task 3（V2-P6-003）：面板年内增量日更

**Files:**
- Modify: `src/openalpha_cn/cli.py`（`panel build` 新增 `--incremental`：对按交易日取数的目标——`price`、`adj_factor`、`stk_limit`、`index_daily`——只拉已存分区最后一个交易日之后、到本次 horizon 为止的交易日，并把已存行与新行合并后整分区写回；财报目标按 Task 1 的决定走公告日期窗口或最近两个报告期）
- Modify: `src/openalpha_cn/panel_ingest.py`（合并复用 `carry_stored_rows_forward` 的已有语义，不另写一套）
- Test: `tests/unit/` 下新建的 `test_cli_panel_build_incremental.py`（新建）

**Interfaces:**
- Produces: `openalpha panel build --dataset price --year 2026 --incremental --as-of <T>`；与不带 `--incremental` 的全量构建在同一 `--as-of` 下写出相同分区。

- [ ] **Step 1：写失败测试**

```python
def test_incremental_equals_full_rebuild_at_the_same_as_of(tmp_path):
    full = run_build(tmp_path / "full", as_of=T2, incremental=False)
    run_build(tmp_path / "inc", as_of=T1, incremental=False)  # 先建到 T1
    inc = run_build(tmp_path / "inc", as_of=T2, incremental=True)  # 再增量到 T2
    for target in ("daily", "daily_basic", "suspend_d", "adj_factor", "stk_limit"):
        assert inc[target].content_hash == full[target].content_hash


def test_incremental_fetches_only_sessions_after_the_stored_horizon(tmp_path):
    run_build(tmp_path, as_of=T1, incremental=False)
    transport = CountingFakeTransport()
    run_build(tmp_path, as_of=T2, incremental=True, transport=transport)
    assert transport.sessions_requested == sessions_between(T1, T2)


def test_incremental_refuses_a_gap_rather_than_bridging_it(tmp_path):
    # 已存分区缺中间一个交易日时，增量构建按名拒绝并给出全量重建命令
    ...
```
第三条测试的断言写成：退出码为 `PanelExit.unhealthy`，消息包含缺失的交易日与一条可直接执行的全量 `panel build` 命令。

- [ ] **Step 2：确认失败 → Step 3：实现 → Step 4：通过 + `tests/unit` 全量**

- [ ] **Step 5：在线实证**：在临时目录建 2026 年到 T1（取最近第 5 个交易日），再增量到最近一个交易日，与同一 `--as-of` 的全量构建比对 `content_hash`；记录增量请求数（验收：≤ 60）与用时（≤ 10 分钟）。

- [ ] **Step 6：独立评审后提交**（`feat(V2-P6-003): intra-year incremental panel update`）

### Task 4（ops）：回填 2013–2026

不改代码。脚本 `$SCRATCH/p6_backfill.sh`，在 `cd` 到仓库根的子 shell 里运行，后台执行，进度写日志。

- [ ] **Step 1：固定一个 `--as-of`**（取启动时刻，写进日志第一行），所有目标用同一个值。

- [ ] **Step 2：按依赖顺序构建**

```bash
AS_OF="<Step 1 的值>"
RT=~/openalpha-research
uv run --no-sync --env-file .env openalpha panel build --runtime-dir "$RT" --as-of "$AS_OF" --start 2013 --end 2026 --resume \
  --dataset trade_cal --dataset stock_basic --dataset namechange --dataset adj_factor --dataset price \
  --dataset stk_limit --dataset index_daily --dataset index_weight
uv run --no-sync --env-file .env openalpha panel build --runtime-dir "$RT" --as-of "$AS_OF" --start 2013 --end 2026 --resume \
  --dataset income --dataset balancesheet --dataset cashflow --dataset fina_indicator \
  --dataset index_classify --dataset index_member_all
```
中断时用同一命令续跑（`--resume` 以年为粒度）。

- [ ] **Step 3：逐年体检**

```bash
for y in $(seq 2013 2026); do
  uv run --no-sync openalpha panel doctor --runtime-dir "$RT" --dataset daily --year $y --json > "$SCRATCH/doctor-$y.json"; echo "$y exit=$?"
done
uv run --no-sync openalpha data-check --runtime-dir "$RT"; echo "exit=$?"
```
Expected：每年退出 0，`data-check` 退出 0。任何一年不 clean：按 systematic-debugging 查根因，修数据或修代码（修代码走 TDD 与评审），不跳过、不加 `--no-halts`。

- [ ] **Step 4：记录**：实际请求数、用时、各数据集行数与分区大小，写进 `.superpowers/sdd/progress.md`。

**闸门 A：** R1 成立；Task 3 的日更实证通过。否则不进 Phase C 之后的全量计算。

---

## Phase B：因子计算提速

### Task 5（V2-P6-004）：分区写入改列式批量插入

**Files:**
- Modify: `src/openalpha_cn/panel/store.py:1003-1009`（`write_partition` 的 staging 插入）
- Test: `tests/unit/panel/` 下新建的 `test_write_partition_columnar_insert.py`（新建）

**Interfaces:**
- Produces: `PanelStore.write_partition(dataset, year, columns, rows) -> PartitionRef`，签名、返回值、幂等与覆盖语义全部不变。

- [ ] **Step 1：写失败测试**（先写等价与边界测试；「更快」用一条宽松的性能断言守住量级）

```python
import time
from datetime import UTC, date, datetime

import duckdb
import pytest

from openalpha_cn.panel.store import ColumnSpec, PanelStore

TYPES = {
    "BOOLEAN": [True, False, None],
    "DOUBLE": [1.5, -0.0, None, 1e-300],
    "BIGINT": [0, -(2**62), None],
    "VARCHAR": ["000001.SZ", "", None, "中文"],
    "TIMESTAMPTZ": [datetime(2026, 5, 6, 9, tzinfo=UTC), None],
    "DATE": [date(2026, 5, 6), None],
}


@pytest.mark.parametrize("duckdb_type", sorted(TYPES))
def test_every_panel_type_round_trips_through_the_columnar_insert(tmp_path, duckdb_type):
    store = PanelStore(tmp_path)
    values = TYPES[duckdb_type]
    rows = [(f"s{i}", values[i % len(values)]) for i in range(50_000)]
    ref = store.write_partition(
        "t", 2026, (ColumnSpec("subject", "VARCHAR"), ColumnSpec("v", duckdb_type)), rows
    )
    with duckdb.connect() as con:
        got = con.execute("SELECT subject, v FROM read_parquet(?)", [str(ref.path)]).fetchall()
    assert got == rows  # 顺序与取值都相同，跨越多个插入批次的边界


def test_content_hash_is_unchanged_by_the_insert_path(tmp_path):
    # content_hash 由 Python 侧的行计算，插入方式不应影响它；钉住一个已知值
    ...


def test_a_row_of_the_wrong_arity_is_refused_and_nothing_is_written(tmp_path):
    store = PanelStore(tmp_path)
    with pytest.raises(Exception):
        store.write_partition(
            "t", 2026, (ColumnSpec("a", "BIGINT"), ColumnSpec("b", "BIGINT")), [(1, 2), (3,)]
        )
    assert not (tmp_path / "t" / "2026" / "data.parquet").exists()


def test_a_hundred_thousand_rows_write_in_under_three_seconds(tmp_path):
    store = PanelStore(tmp_path)
    rows = [(f"{i:06d}.SZ", float(i)) for i in range(100_000)]
    start = time.perf_counter()
    store.write_partition("t", 2026, (ColumnSpec("s", "VARCHAR"), ColumnSpec("v", "DOUBLE")), rows)
    assert time.perf_counter() - start < 3.0  # executemany 实测约 9 秒
```
第二条测试的实现：先在未改动的代码上用固定行算出 `content_hash`，把这个值写进断言，再改代码。第三条先确认当前代码对错列数抛什么异常，把 `pytest.raises` 收紧到那个异常类型，改后保持同一类型。

- [ ] **Step 2：确认第一、四条失败（性能）或通过（等价）；性能那条必须先红**

```bash
uv run pytest tests/unit/panel -k columnar_insert -q -p no:cacheprovider
```

- [ ] **Step 3：实现**，替换 `store.py` 中 `staging.executemany(...)` 那一行：

```python
_INSERT_CHUNK_ROWS: Final[int] = 20_000


def _insert_columnar(
    connection: duckdb.DuckDBPyConnection,
    columns: Sequence[ColumnSpec],
    rows: Sequence[tuple[object, ...]],
) -> None:
    """Insert `rows` into `staging` one column-list per parameter, `_INSERT_CHUNK_ROWS` at a time.

    `executemany` binds one row per statement and was 53% of a factor build's wall clock
    (V2-P6-004). Binding each column as one typed list and letting `unnest` zip them back is
    the same rows in the same order -- the test round-trips every type `PANEL_DUCKDB_TYPES`
    names across chunk boundaries -- at about a twelfth of the cost.
    """
    arity = len(columns)
    for row in rows:
        if len(row) != arity:
            raise PanelStorageError(f"a row has {len(row)} values for {arity} columns")
    selects = ", ".join(f"unnest(?::{column.duckdb_type}[])" for column in columns)
    statement = f"INSERT INTO staging SELECT {selects}"
    for start in range(0, len(rows), _INSERT_CHUNK_ROWS):
        chunk = rows[start : start + _INSERT_CHUNK_ROWS]
        connection.execute(statement, [list(values) for values in zip(*chunk, strict=True)])
```
并把调用处改为 `_insert_columnar(staging, columns, rows)`。如果 Step 1 查到当前错列数抛的不是 `PanelStorageError`，行数检查抛同一类型，保持行为不变。

- [ ] **Step 4：测试通过；跑 `tests/unit` 全量与 `tests/replay`**

- [ ] **Step 5：真实数据等价**：在 `runtime/panel` 的草稿副本上，改前改后各对 21 个因子的 raw 档构建 10 个时点，比较全部 `factor_obs_*` 与 `factor_manifest_*` 分区的 `content_hash`，必须全部相同；记录前后用时。

- [ ] **Step 6：独立评审后提交**（`perf(V2-P6-004): columnar partition insert, identical partitions`）

### Task 6（V2-P6-005）：因子读路径提速

**Files:**
- Modify: `src/openalpha_cn/panel_factors.py`（`_read_dataset` 约 4877 行、`_session_date` 约 5284 行、5112 行附近的逐行时区转换）
- Test: `tests/unit/panel/` 下新建的 `test_factor_read_path_equivalence.py`（新建）

两处改动，分两次提交，每次都要求等价：

1. **时区对象只取一次。** 剖析中 `pytz.timezone` 被调用 378 万次、`localize`/`fromutc` 各 189 万次。把循环里的 `pytz.timezone(...)` 提到循环外（或模块级常量），转换公式不变。
2. **每个时点只读回看窗口需要的行。** 现在每个时点整年重读（约 67 万行）。在 SQL 里加上 `trade_date >= <该时点所需最早交易日>` 的下界（最早交易日由因子声明的回看长度与已存交易日历算出），可见性谓词（`available_time <= as_of AND revision_time <= as_of`）与就绪评估一字不改。

- [ ] **Step 1：写失败测试**：用测试时生成的面板 fixture（仓库已有生成器），对 21 个因子各取 3 个时点（年初回看跨年、年中、存在修订行的时点），改前改后各构建一次，断言分区 `content_hash` 相同；另写一条性能断言：生成的 600 只 × 250 个交易日面板上，单因子单时点 raw 构建 < 0.3 秒。

- [ ] **Step 2：确认性能断言失败 → Step 3：实现第 1 处 → Step 4：通过、`tests/unit` 全量、提交 → 重复 Step 3–4 做第 2 处**

- [ ] **Step 5：真实数据等价与测速**：同 Task 5 Step 5，21 个因子 × 三档 × 10 个时点，`content_hash` 全部相同；报告每时点每档的平均用时。目标 ≤ 0.5 秒。

- [ ] **Step 6：闸门判断**：按实测用时算全量（2015-01 至今全部交易日 × 21 × 3）的墙钟时间。≤ 48 小时 → 跳过 Task 7；> 48 小时 → 做 Task 7。

### Task 7（V2-P6-006，条件触发）：因子计算并行

只在 Task 6 Step 6 判定超过 48 小时时执行。

**Files:**
- Modify: `src/openalpha_cn/factor_view.py`（`build_factor_panels`：时点截面的**计算**交给 `ProcessPoolExecutor(max_workers=8)`，子进程只读、只返回截面行；主进程按时点顺序归并后**单次**写分区——面板存储的目录不支持跨进程写，所以写入必须只在主进程）
- Test: `tests/unit/` 下新建的 `test_factor_build_parallel.py`（新建）

- [ ] **Step 1：写失败测试**：`workers=1` 与 `workers=4` 构建同一组时点，分区 `content_hash` 相同；一个时点计算抛错时整次构建失败且不写任何分区（与串行语义相同）。
- [ ] **Step 2–4：确认失败、实现、通过 + `tests/unit` 全量**
- [ ] **Step 5：真实数据等价与测速；独立评审后提交**

### Task 8（ops）：全量因子截面

- [ ] **Step 1：生成时点清单**：从 `$RT` 的交易日历取 2015-01-05 至最近一个已收盘交易日，每个交易日一个时点，时刻取当日 16:30 Asia/Shanghai（`08:30:00+00:00`），这是仓库可得性时钟的保守收盘后时刻。
- [ ] **Step 2：按年分批构建**，每个因子每年一次调用，三档一起（`--tier neutralized` 会同时写 raw 与 processed）：

```bash
for f in $(uv run --no-sync openalpha factor list --json | python3 -c 'import json,sys;print(" ".join(e["key"] for e in json.load(sys.stdin)["factors"]))'); do
  for y in $(seq 2015 2026); do
    uv run --no-sync openalpha factor build --runtime-dir "$RT" --factor "$f" --tier neutralized \
      --transform cross_section_standard/v1 --neutralization industry_and_size/v1 \
      $(as_of_flags_for_year $y) $(year_flags_for $y) --max-staleness-days 30 --json > "$LOG/$f-$y.json"
    echo "$f $y exit=$?"
  done
done
```
`year_flags_for $y` 输出 `--year $((y-1)) --year $y` 加上 `index_member_all` 存有的全部年份（见第 3 节实测）。`factor list --json` 的实际键名以运行时输出为准，脚本先打印一次确认。
- [ ] **Step 3：核对 R3**：每个因子每档的 manifest 时点数等于交易日数；任何缺口按根因处理，不跳过。

**闸门 B：** R3 成立。

---

## Phase C：研究基础设施

### Task 9（V2-P6-007）：扣成本的多年策略回测

**Files:**
- Create: `src/openalpha_cn/backtest/strategy_backtest.py`
- Create: `src/openalpha_cn/strategy_view.py`（读面板、把已存打分和行情整理成回测输入；面板平面的读取只能在这一层，因为 `backtest/` 不得导入面板平面）
- Modify: `src/openalpha_cn/cli.py`（`openalpha strategy backtest`）、`src/openalpha_cn/sdk.py`（`OpenAlphaSDK.run_strategy_backtest()`）
- Test: `tests/unit/backtest/` 下新建的 `test_strategy_backtest.py`、`tests/unit/` 下新建的 `test_strategy_view.py`、`tests/unit/` 下新建的 `test_cli_strategy_backtest.py`（新建）

**Interfaces（Produces，后续任务按这些名字调用）：**

```python
class ScoreSource(BaseModel):  # 打分从哪里来
    components: tuple[
        tuple[str, str, Decimal], ...
    ]  # (factor key, tier, weight)，tier ∈ raw/processed/neutralized
    combine: Literal["zscore_sum", "rank_sum"]
    prediction_ids: tuple[str, ...] = ()  # 或者直接用已登记的模型预测；与 components 二选一


class StrategySpec(BaseModel):
    rebalance_every_sessions: int  # 调仓间隔
    holding_count: int  # 等权持有前 N 名
    buffer_rank: int | None  # 缓冲带：已持有的票排名不低于 buffer_rank 就不卖；None 为无缓冲
    max_industry_weight: Decimal | None
    position_capital: Decimal  # 测量设置：100000
    participation_cap: Decimal  # 测量设置：0.01
    costs: CostSchedule  # 复用 backtest/execution.py
    benchmarks: tuple[str, ...]  # ("000905.SH", "equal_weight_all_a")


class PeriodResult(BaseModel):
    start: date
    end: date
    gross_return: Decimal
    cost: Decimal
    net_return: Decimal
    benchmark_returns: Mapping[str, Decimal]
    turnover: Decimal
    rejected_orders: int  # 涨跌停锁单、停牌、整手不足、超参与上限


class StrategyBacktest(BaseModel):
    spec: StrategySpec
    source: ScoreSource
    periods: tuple[PeriodResult, ...]
    limitations: tuple[str, ...]


def run_strategy_backtest(inputs: StrategyInputs, spec: StrategySpec) -> StrategyBacktest: ...
```
执行规则复用 `AShareExecutionPolicy`：T 日收盘后出信号、T+1 开盘价成交；涨停不能买、跌停不能卖；停牌不能交易；整手（主板 100 股整数倍、科创板不少于 200 股）；卖出后资金可用于同日买入但股票 T+1 才能卖。

- [ ] **Step 1：写失败测试**（手算夹具：4 只股票 × 6 个交易日，每一步的成交、费用、净值都在测试里手算写出）

```python
def test_a_two_period_backtest_matches_the_hand_computed_ledger():
    result = run_strategy_backtest(HAND_FIXTURE_INPUTS, HAND_FIXTURE_SPEC)
    assert [p.net_return for p in result.periods] == [Decimal("0.012345"), Decimal("-0.004321")]
    # 以上两个数在测试 docstring 里逐步手算：成交价 × 股数、佣金（不足 5 元按 5 元）、卖出印花税、滑点


def test_a_limit_up_name_is_not_bought_and_is_counted_as_rejected(): ...
def test_a_suspended_holding_is_carried_not_sold(): ...
def test_buffer_rank_keeps_a_holding_that_slipped_within_the_band(): ...
def test_signals_are_read_at_t_and_traded_at_t_plus_one_open_never_at_t(): ...
def test_a_score_row_not_visible_at_its_as_of_is_refused(): ...  # 前视守卫
def test_neutralized_tier_scores_are_accepted(): ...  # model evaluate 拒绝的那一档
```
- [ ] **Step 2：确认失败 → Step 3：实现 → Step 4：通过**
- [ ] **Step 5：`lint-imports` 8/0、`mypy` 0；CLI 与 SDK 两个面的等价测试（同一输入同一输出）**
- [ ] **Step 6：台账**：`features.csv` 新增一行指向 `strategy_backtest.py#run_strategy_backtest` 与其验收测试 node id，重生成 `summary.json` 与台账，同一提交
- [ ] **Step 7：独立评审后提交**（`feat(V2-P6-007): multi-year net-of-cost strategy backtest`）

### Task 10（V2-P6-008）：研究编排器与预登记守卫

**Files:**
- Create: `scripts/research/grid.py`（配置网格展开、不重叠期抽样、调用 SDK、写研究账本）
- Create: `scripts/research/registry.py`（预登记与保留期守卫）
- Test: `tests/unit/scripts/` 下新建的 `test_research_grid.py`、`tests/unit/scripts/` 下新建的 `test_research_registry.py`（新建）

**Interfaces:**

```python
def expand_grid(grid: Mapping[str, Sequence[object]]) -> tuple[Mapping[str, object], ...]  # 笛卡尔积，顺序确定
def non_overlapping(as_ofs: Sequence[date], horizon_sessions: int) -> tuple[date, ...]      # 每 horizon 取一个
def append_ledger(path: Path, stage: str, config: Mapping[str, object], result: Mapping[str, object]) -> None
def stage_family(path: Path, stage: str) -> int                                            # 家族大小 = 该阶段行数
def fdr_table(path: Path, stage: str, q: float) -> MultipleTestingReport                   # 调 control_false_discovery_rate(dependence="arbitrary")
def register(config: Mapping[str, object], criteria: Mapping[str, object], path: Path) -> str  # 写登记文件，返回 sha256
def assert_holdout_allowed(registration: Path, ledger: Path, repo: Path) -> None
```
`assert_holdout_allowed` 三条拒绝：登记文件不在 git 已提交的历史里；登记文件的提交时间晚于账本里任何一条保留期记录；账本里已经有保留期记录（只能跑一次）。

- [ ] **Step 1：写失败测试**

```python
def test_family_size_counts_every_row_of_the_stage_including_failures(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    for i in range(7):
        append_ledger(ledger, "discovery", {"i": i}, {"p_ic": 0.5})
    append_ledger(ledger, "discovery", {"i": 7}, {"error": "refused"})
    assert stage_family(ledger, "discovery") == 8


def test_non_overlapping_takes_every_horizon_th_session():
    days = tuple(date(2026, 1, 1) + timedelta(days=i) for i in range(10))
    assert non_overlapping(days, 3) == (days[0], days[3], days[6], days[9])


def test_holdout_is_refused_without_a_committed_registration(tmp_git_repo): ...
def test_holdout_is_refused_the_second_time(tmp_git_repo): ...
def test_fdr_uses_arbitrary_dependence(tmp_path): ...
```
- [ ] **Step 2–4：确认失败、实现、通过 + `tests/unit` 全量**
- [ ] **Step 5：独立评审后提交**（`feat(V2-P6-008): research grid runner and one-shot holdout guard`）

**闸门 C：** Task 9、10 合并；用 2015 年的一个月跑一次端到端冒烟，确认账本行、家族大小、FDR 表都生成；按冒烟用时估算三阶段总时长，写进进度账本。

---

## Phase D：研究执行

### Task 11（V2-P6-009）：预登记研究协议

**Files:**
- Create: `docs/research/p6-protocol.md`（把本计划第 2 节原样定稿，另写入：研究期网格的完整取值、验证期入围规则、保留期通过标准、随机种子、代码提交号）

- [ ] **Step 1：写协议，列出研究期完整网格**

| 阶段 | 维度 | 取值 |
|---|---|---|
| 1 单因子 | 因子 | 21 个 |
| | 档位 | raw、processed、neutralized |
| | 预测期（= 调仓间隔） | 1、5、20 个交易日 |
| 2 组合与策略 | 组合方式 | 阶段 1 幸存因子的 `zscore_sum` 等权；按研究期内滚动 36 个月 IC 加权（只用当时已知的 IC）；`boosted_rank_trees`（raw/processed 档） |
| | 树模型超参（`alpha_tree.py` 的四个名字，全部必填、无默认） | `tree_count` {50, 200}（上限 500）、`max_depth` {2, 3}（上限 6）、`learning_rate` {0.05, 0.1}、`min_leaf_securities` {200, 1000} |
| | 持仓数 | 30、50、100 |
| | 调仓间隔 | 5、10、20 个交易日 |
| | 缓冲带 | 无；持仓数的 1.5 倍 |
| | 行业上限 | 无；0.2 |

- [ ] **Step 2：提交协议**，提交号写进协议文件末尾的下一次提交（协议本身的提交必须早于任何研究期运行）

```bash
git add docs/research/p6-protocol.md
git commit -m "docs(V2-P6-009): pre-registered research protocol"
```

### Task 12（V2-P6-010）：三阶段研究

- [ ] **Step 1：阶段 1 单因子（研究期）**：21 × 3 × 3 = 189 个配置，每个配置跑 `factor run`（测量设置按协议）与策略回测（单因子打分、持有 50 只、调仓间隔 = 预测期），账本记 IC 序列、扣成本超额序列与两个 p 值。FDR（BY，q = 0.10）后得到幸存因子表。
- [ ] **Step 2：阶段 2 组合与策略（研究期内 walk-forward：以 2 年训练、6 个月测试滚动）**：按协议网格跑，账本记每个配置的测试段拼接结果与 p 值；FDR 后按信息比率取前 5 名入围。阶段 1 无幸存因子时，阶段 2 仍用全部 21 个因子做组合（组合可能在单因子都不显著时显著），这一点写在协议里，不是事后决定。
- [ ] **Step 3：阶段 3 验证期**：5 个入围配置在 2022–2023 上各跑一次，按协议的选择指标选出 1 个；报告全部 5 个的结果。
- [ ] **Step 4：预登记保留期**：用 `register()` 写 `docs/research/p6-registration.json`（所选配置、通过标准、种子、代码提交号），**先提交**。
- [ ] **Step 5：保留期一次性运行**：`assert_holdout_allowed()` 通过后运行，结果原样写进账本。
- [ ] **Step 6：报告** `docs/research/p6-results.md`：三阶段全部表格、家族大小、FDR 表、保留期结论（按预声明标准写「通过」或「不通过」）、局限（幸存者偏差已被退市股入池处理与否、成本假设、容量）。
- [ ] **Step 7：独立评审**（评审员核对：账本行数 = 家族大小；登记提交早于保留期记录；报告里的每个数能从账本复算）后提交。

---

## Phase E：每日运行

### Task 13（V2-P6-011）：每日选股一条命令

**Files:**
- Create: `scripts/daily_selection.py`
- Create: `docs/runbooks/daily-selection.zh-CN.md`
- Test: `tests/unit/scripts/` 下新建的 `test_daily_selection.py`（新建，网络全部用假传输）

**行为（按顺序，任何一步失败即停并以非零退出，已完成的步骤由各自的幂等语义保证可重跑）：**
1. 读 `docs/research/p6-registration.json`，取登记配置。
2. `panel build --incremental` 更新当前年各目标到最近一个已收盘交易日。
3. `panel doctor` + 依赖门；不 clean 就停。
4. 对登记配置用到的因子，`factor build` 当日时点。
5. `shortlist run` 按登记配置出当日候选榜。
6. `portfolio construct` 以上一交易日目标权重为 `--previous-weight` 生成当日目标权重。
7. 当日打分作为预测登记（`model daily-run` 或策略打分的登记路径，二者按登记配置的组合器选）。
8. 打印摘要：交易日、候选榜 ID、前 N 名、目标权重、预测记录 ID、本次 Tushare 请求数。

- [ ] **Step 1：写失败测试**：同一交易日跑两次，第二次不新增任何分区、榜单或预测记录（幂等）；第 3 步不 clean 时不执行第 4–7 步；登记文件缺失时退出码 2。
- [ ] **Step 2–4：确认失败、实现、通过 + `tests/unit` 全量**
- [ ] **Step 5：实跑**：在 `$RT` 上对最近一个交易日跑两次，贴出两次摘要；记录请求数与用时（R2、R5）。
- [ ] **Step 6：定时（需要你同意）**：生成 `~/Library/LaunchAgents/` 的 plist 文本给你看，交易日 18:30 触发。**安装前先问你**；不同意就保留手动命令，产物相同。
- [ ] **Step 7：独立评审后提交**

### Task 14（V2-P6-012）：前向跟踪周报

**Files:**
- Create: `scripts/forward_report.py`
- Test: `tests/unit/scripts/` 下新建的 `test_forward_report.py`（新建）

- [ ] **Step 1：写失败测试**：给定 3 条已登记预测与揭晓后的行情，周报的逐期扣成本超额与累计值与手算相同；揭晓前的预测不进入统计；预测登记时间晚于其标签起点的记录被拒绝并单列。
- [ ] **Step 2–4：确认失败、实现、通过**
- [ ] **Step 5：独立评审后提交**

---

## Phase F：收口

### Task 15：文档、台账与全量验证

- [ ] **Step 1：README 与 README.en 加「选股工作流」一节**：从回填、每日一条命令到周报；措辞保持「候选」，写明保留期结论与它的含义，不写任何收益承诺。
- [ ] **Step 2：台账**：为 V2-P6-002/003/004/005/007/008/011/012 的能力各加一行，`acceptance_test` 绑定真实 node id；重生成 `features.csv`、`summary.json`、台账，`build_feature_coverage --check` 通过。
- [ ] **Step 3：全量**：合并门 → 本地全量（`--all-extras`，0 failed / 0 skipped / 0 warnings）→ 推送 → CI 七绿 → e2e（复用面板仅限一个 horizon）。
- [ ] **Step 4：按第 1 节 R1–R7 逐条判定并报告**；任何一条不成立，回到对应任务。

---

## 执行顺序与并行

```
Task 0 ─┬─ Task 1 ─ Task 2 ─ Task 3 ─ Task 4(后台约 1 天) ─┐
        └─ Task 5 ─ Task 6 ─(Task 7) ──────────────────────┴─ Task 8(后台约 1 天) ─┐
        └─ Task 9 ─ Task 10 ──────────────────────────────────────────────────────┴─ 闸门 C ─ Task 11 ─ Task 12 ─ Task 13 ─ Task 14 ─ Task 15
```
Task 5/6 与 Task 9/10 不依赖取数，可以在 Task 4 后台回填期间做。代码任务用 `isolation: "worktree"` 的实现者，逐个独立评审后按合并门 cherry-pick 进分支。
