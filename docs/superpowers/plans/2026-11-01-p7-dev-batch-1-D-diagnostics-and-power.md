# P7 开发批次 1 · 轨道 D — 研究诊断工具化（`V2-P7-004`）与周报功效输出（`V2-P7-018`）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **状态：草案，等用户审阅。** 本文只是计划；不改源码、不改测试。每个 `file:line` 都是起草者在 `main` @ `499c51e` 上读过的。

**Goal:** 把规格 §3 的探索性诊断（按因子 / 合成分的前向 20 日收益十分位、剔除输家尾部、秩 IC 及其离散度、§3.6 的功效表）从仓库外的一次性脚本（`~/openalpha-research/diagnostics/p7/decile.py`、`exclusion.py`、`main_board_remeasure.py`）变成**只读、有测试、带研究窗口护栏**的工具；护栏默认拒绝读取验证期 2022-01-04..2023-12-29 与 2024-01-02 起的一切，唯一的越权是**具名、记日志、只能用一次**的"读一次"路径（`V2-P7-024` 以后用它）；再给周报（`V2-P7-018`）补上验收缺的**功效输出**：目标功效、相关性处理、多重检验族大小、最小可用期数、缺口规则，并且把"测不出"写成设计的一个结论类别，永远不读成"没有效应"。

**Architecture:** 纯统计放 `src/openalpha_cn/backtest/`（标准库、无面板依赖，`018` 与以后的 `014` 都要从 `src/` 调用它）：`decile_readout.py`（一个截面的十分位 / 尾部 / 秩 IC，一条日期序列的离散度）与 `power_analysis.py`（均值单侧检验的功效、最小可检出均值、所需期数、自相关等价期数、BY 第一步水平、中期无效性停止的运行特征）。读面板、窗口护栏、文档与命令行、读一次路径放 `scripts/research/`（与 `grid.py`、`registry.py` 并列，复用它们的阶段窗口 `grid.PROTOCOL_STAGE_WINDOWS` 和一次性保留期守卫 `registry.run_holdout` 的做法）：`p7_windows.py`（窗口与按年封顶、只读的 `WindowCappedStore`）、`p7_diagnostics.py`（面板 → 每个信号日的合格范围与分数 → 读出 → 汇总 → 文档 / 文本 → CLI `explore`、`power`）、`p7_reading.py`（读一次的预登记、认领、测量、停止规则）。分数与资格**复用每日路径本身**（`build_facts_day` + `screen`，即登记记录的打分定义），标签复用 `model_view.OutcomeLabels`。周报只加一个设置块 `survivor_forward_power` 的读取器和文档里的一个 `power` 节。**模块放置是用户决定**（见"用户决定" U9），本文按推荐写。

**Tech Stack:** Python 3.12、标准库 `statistics.NormalDist` / `math.lgamma`（不引入 scipy；`backtest-no-numeric-stack-or-panel-plane` 禁 scipy）、pydantic v2、pytest；门禁 `ruff check`、`ruff format --check`、`mypy src scripts`（`pyproject.toml:108` `files = ["src", "scripts"]`）、`lint-imports`。

**Spec / 依据:** `AGENTS.md`（硬规则 1–9、研究诚实）；`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §3（探索性发现与 §3.6 功效表）、§5.2（2026-11-01 改写的 G-0）、§7.2（窗口）、§9 的 2026-10-14 Part E 行与 2026-11-01 各行；`docs/superpowers/plans/2026-11-01-p7-route-adjustment.md` §2.1、§2.7、§2.8、§4；路线图 §P7 的 `V2-P7-004`（`docs/specs/v2/openalpha-cn-v2-roadmap.md:482`）、`V2-P7-018`（`:496`）、`V2-P7-024`（`:502`）；`docs/research/p7-step0-validation/T8-main-board-remeasure.md` §1、§3.1（口径与 (d) 行）；Part E 设计 `docs/superpowers/plans/2026-10-14-p7-step0-part-e-design.md`。

---

## Global Constraints

逐字取自规格与代码；每个任务隐含这一节。

- 每个提交、分支、PR 写 issue ID：Task 1–5、7 写 `V2-P7-004`，Task 6 写 `V2-P7-018`；分支 `feat/v2-p7-004-diagnostics`、`feat/v2-p7-018-power`。提交信息末尾：`Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`。
- **研究窗口**（规格 §7.2；`scripts/research/grid.py:125-130`）：研究期 2015-01-05..2021-12-31（年上限 2021）；验证期 2022-01-04..2023-12-29（年上限 2023）；二次使用段 2024-01-02..2026-09-24（年上限 2026；P6 的一次性保留期，规格 §1；G-0 的次要、非确认性读数）。**2026-09-24 之后的任何日期本工具永远不读**（确认性证据只来自登记之后的前向记录，那是周报的事）。
- 默认（`explore`）只开研究窗口；**没有自由形式的越权参数**：进入 2022-01-04 之后的唯一路径是 `p7_reading.read_once`（已提交的预登记、先认领后测量、只能一次、结果写明绑定）。见"用户决定" U10。
- 复现 T8 的探索性信号日：`2017-01-03` 起每 20 个交易日一个，到 `2021-10-29` 为止，共 59 个（T8 §1.1；`main_board_remeasure.py:100`）。
- 标签规则 = `build_label_window`：信号日次日收盘入场，再 `h` 个交易日收盘出场（`strategy_survivor_forward.py:63-66`）；两条标签不重叠 ⇔ 两个信号日相隔不少于 `h` 个交易日（与 `grid.non_overlapping`，`grid.py:610-616`，和周报 `anchor + h·k` 同一规则）。
- 打分定义 = 登记记录的定义：`build_facts_day`（`strategy_survivor_facts.py:766-830`）+ `screen`（`domain/survivor_screen.py:50-108`）：仅沪深主板、上市不少于 250 个交易日、非 ST、非停牌、四个因子值齐全，流动性 30% 在**通过静态原因的名字**里切（总体 B，`survivor_screen.py:80-88`，用户 2026-10-13 的决定）；合成分 = 四个方向调整后的 `processed` 值的均值（`DEFAULT_COMPOSITE_FACTORS`，`domain/survivor_book.py:46-51`）；幸存者 = 剔除最差 `n*2//10`。
- 十分位规则 = 周报规则：按 `(score, ts_code)` 最差在前，位置 `p` 的十分位 `floor(10p/n)`，十分位 0 = 最差（`strategy_survivor_forward.py:678-703`）；`ew_minus_worst_decile` > 0 支持筛选（`:315-323`）。**探索脚本的 D1 = 最好**（`main_board_remeasure.py:413`），两种写法不得混用。
- 秩 IC = `factor_ic.average_ranks`（`backtest/factor_ic.py:746`）两边取平均秩后 `statistics.correlation`，与周报 `_rank_ic`（`strategy_survivor_forward.py:933-945`）逐位相同；缺失原因码 `fewer_than_two_labelled` / `scores_tied` / `labels_tied`。
- 功效核对数（日期间 IC 标准差 0.15、单侧 0.05、正态近似；起草者用标准库复算）：K=36 → IC 0.05 时 0.63876、IC 0.037 时 0.43453、IC 0.10 时 0.99074；K=56 → IC 0.05 时 0.80222；K=12 → IC 0.05 时 0.31201、IC 0.10 时 0.74683；IC 0.05 达到 80% 需 K=56，IC 0.037 需 K=102。**t 临界值口径**（`t_{0.95, K−1}`）：K=12 → 0.26070，K=36 → 0.62188，IC 0.05 达到 80% 需 K=57。
- 周报的措辞守卫：文档与打印行里不得出现 `significant`、`effective`、`confirmed`、`alpha`、`outperform`、`passed`、`failed`、`proven`（`tests/unit/test_strategy_survivor_forward.py:188-197`；`tests/unit/scripts/test_forward_report_survivor.py:131-133` 的正则没有尾部 `\b`，所以 `effectively` 也会命中）。**因此"最小有效 n"写成 `minimum_periods`，"α"写成"test level"。**
- 措辞一律"候选"；工程完成不等于研究有效；探索性输出永远带"探索性、未登记、不是证据"。
- `domain/` 不引入数值库（硬规则 2）：本计划不碰 `domain/`。面板夹具在测试时生成（硬规则 6）：不提交 `.parquet/.duckdb/.sqlite3/.db`。单一组装根（硬规则 5）：面板存储只经 `panel_view.panel_store` 的同一路径约定（`panel_view.py:175-177`）。可复现（硬规则 8）：输出写 `code_commit`（git）、`config_digest`（算出来的）；**本工具没有随机成分**，文档写明 `"random_component": "none"`。
- 新的 `backtest/*.py` 必须加进 `pyproject.toml` 两个合约的 `source_modules`（`backtest-studies-touch-no-store`，`:308-337`；`backtest-studies-reach-no-composition-root`，`:364-392`），否则 `tests/unit/test_import_layering.py:675-716` 变红。
- 不联网（离线测试守卫 `tests/conftest.py`）；不读 `.env`；绝不在 `~/openalpha-research/panel` 原地读（读也会在面板根目录建 `catalog.duckdb.lock` 与 `.gate`，`panel/store.py:1383-1414`），操作性运行一律用 APFS 克隆（`cp -c`）。
- 测试卫生（记忆）：worktree 里跑测试设 `PYTHONPATH=<worktree>/src`；每次提交前跑 `tests/unit`；全量套件串行；绝不在 `git --exec`/hook 里跑测试；变异探针用 `PYTHONDONTWRITEBYTECODE=1`，改前 `cp` 到草稿目录，改完恢复并 `cmp` 确认逐字节一致。
- 受登记绑定的路径：`src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`、`scripts/daily_selection.py`、`scripts/forward_report.py`。本计划全部改动都在 `006b` 之前，允许；见"受绑定代码"一节。

## Review Focus

最可能伤到使用者的五类输入或失败模式，每条由所在任务里的测试钉住：

1. **意外读到验证期或保留窗口**：一个 2021 年末信号日的标签跨进 2022；日历、登记簿、复权、停牌、收益路径、因子档里任何一个读取器按年打开 2022 分区；自定义窗口。→ Task 3 `test_a_capped_store_refuses_every_door_past_its_ceiling`、`test_a_research_plan_embargoes_a_label_that_would_end_in_2022`；Task 4 `test_a_label_window_past_the_window_is_refused_before_any_label_is_asked`、`test_the_label_reader_cannot_be_built_past_the_ceiling`、`test_a_window_refusal_is_never_turned_into_a_gap`。
2. **对 2022–2023 的第二次读取**：认领后崩溃再跑、删掉认领文件、提交后改登记、登记后改受绑定代码、从别处导入包。→ Task 5 `test_a_read_that_crashed_after_its_claim_is_the_one_read`、`test_a_claim_once_committed_cannot_be_deleted_back_into_a_read`、`test_an_edit_after_the_commit_refuses_the_read`、`test_changed_bound_code_refuses_the_read`。
3. **把功效读错**：把"测不出"读成"没有效应"；正态与 t 两种口径混用；把观测到的标准差当成规划值（"观测功效"）；次要族的检验水平。→ Task 2 `test_the_power_lines_say_not_detectable_is_a_category_of_the_design`、`test_the_t_convention_needs_one_more_period_than_the_normal_one_at_ic_005`；Task 6 `test_the_observed_sd_is_described_and_never_used_for_power`、`test_a_secondary_row_is_powered_at_the_first_benjamini_yekutieli_step`。
4. **方向与切分错误**：十分位 0 是最差；`ew_minus_worst_decile` 的符号；平分跨十分位边界时按代码断开；未贴标签的名字被悄悄当成 0。→ Task 1 `test_a_loser_tail_is_read_as_hand_computed`、`test_reversing_the_scores_reverses_the_sign_of_every_screen_metric`、`test_the_cut_is_by_score_then_code_and_does_not_depend_on_input_order`、`test_an_unlabelled_name_keeps_its_decile_and_leaves_every_number`，以及与周报逐位一致的 `test_the_readout_is_the_forward_reports_arithmetic`。
5. **探索性输出被当成证据，或重跑字节不同**：横幅丢失、探索运行写出读一次的 kind、出现结论词。→ Task 4 `test_every_exploratory_document_and_line_carries_the_exploratory_wording`、`test_two_runs_write_the_same_bytes_under_two_hash_seeds`；Task 4 `test_a_binding_makes_the_read_once_kind_and_wording`；Task 5 `test_only_a_read_once_document_bound_to_this_claim_is_written`。

## 模块放置（推荐；U9 由用户定）

| 放哪 | 推荐理由 | 代价 |
|---|---|---|
| `src/openalpha_cn/backtest/decile_readout.py`、`power_analysis.py`（纯函数） | `018` 的周报在 `src/`，必须能导入功效计算；以后的 `014`（增量 IC）也要用；纯标准库、不碰面板，正好是 `backtest/` 的形状 | 加进两个合约的源列表（`pyproject.toml`，受绑定） |
| `scripts/research/p7_windows.py`、`p7_diagnostics.py`、`p7_reading.py`（读面板、护栏、读一次） | 研究工具不是产品代码；与 `grid.PROTOCOL_STAGE_WINDOWS`、`registry.run_holdout` 的保留期守卫并列并复用；`WindowCappedStore` 覆写 `query`/`read_visible_at` 并 `super()` 调用，放 `src/` 会触发 `tests/unit/panel/test_query_callers.py`（扫描 `src/`，`:49`、`:189`）与 `test_visible_read_callers.py`（`:74`、`:513`）的白名单 | `scripts/research/` 同样受绑定；`mypy src scripts` 覆盖它 |
| 备选：全放 `src/openalpha_cn/strategy_survivor_diagnostics.py` | 一个模块 | 要在 `tests/unit/test_panel_ingest_import_isolation.py` 加 `RESEARCH_PLANE_SEAM_IMPORTS` 与 `RESEARCH_PLANE_DATASETS` 两行、`pyproject.toml:263-295` 的 backtest 禁止表加一行、两个读取审计加白名单 |

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `src/openalpha_cn/backtest/decile_readout.py`（新） | 一个截面：十分位均值与超额、尾部剔除、幸存者减等权、等权减最差十分位、顶减底、秩 IC；一条序列：均值、标准差、标准误、t、为正占比、一阶自相关、按年 | 1 |
| `src/openalpha_cn/backtest/power_analysis.py`（新） | 正态 / t 临界值、功效、最小可检出均值、所需期数、自相关等价期数、BY 第一步水平、中期无效性停止概率与带停止的功效、成段文字 | 2 |
| `pyproject.toml`（改） | 两个合约的 `source_modules` 各加两行 | 1、2 |
| `scripts/research/p7_windows.py`（新） | 三个窗口、`WindowCappedStore`、`plan_signal_days`、`capped_panel_store` | 3 |
| `scripts/research/p7_diagnostics.py`（新） | 分数源（真实：事实 + 筛选）、标签源（真实：`OutcomeLabels`）、引擎、文档与文本、CLI `explore` / `power` | 4 |
| `scripts/research/p7_reading.py`（新） | 读一次的登记、准入、认领、预检、测量、停止规则、结果 | 5 |
| `src/openalpha_cn/strategy_survivor_forward.py`（改） | `survivor_forward_power` 块的读取器、文档的 `power` 节与打印行；（单独提交）过时的 G-0 条目 | 6 |
| `scripts/forward_report.py`（改） | 读功效块并交给文档（2 行 + 文档串 1 句） | 6 |
| `tests/unit/backtest/test_decile_readout.py`、`tests/unit/test_decile_readout_agreement.py`（新） | Task 1 | 1 |
| `tests/unit/backtest/test_power_analysis.py`（新） | Task 2 | 2 |
| `tests/unit/scripts/test_p7_windows.py`（新） | Task 3 | 3 |
| `tests/unit/test_p7_diagnostics.py`（新；放 `tests/unit/` 才能导入同目录的 `test_strategy_survivor_forward`、`test_survivor_facts_panel` 夹具——仓库里的跨文件导入都是同目录的） | Task 4 | 4 |
| `tests/unit/scripts/test_p7_reading.py`（新；用同目录的 `research_repo` 帮手） | Task 5 | 5 |
| `tests/unit/test_strategy_survivor_forward.py`、`tests/unit/scripts/test_forward_report_survivor.py`（改：追加测试，`reported()` 加一个关键字） | Task 6 | 6 |
| `docs/research/p7-step0-validation/T11-diagnostics-research-window.md`（新，操作性） | 研究窗口上的真实读数 | 7 |

## 依赖与并行

```
Task 1 (decile_readout) ─┐
Task 2 (power_analysis) ─┼─> Task 4 (diagnostics + CLI) ─> Task 5 (read-once) ─> [V2-P7-024 的读数：另立计划]
Task 3 (windows guard) ──┘                │
                                          └─> Task 7（操作性，等用户明确说"开始"）
Task 2 ─> Task 6 (018 功效输出)        Task 6 与 Task 3/4/5 互不依赖
```

- **可并行**：Task 1、2、3 互不依赖，文件不相交；Task 6 只依赖 Task 2，可与 3/4/5 并行。
- `V2-P7-018` 的剩余验收只依赖 Task 2 + Task 6，不必等护栏与读一次（路线图写的是 `018` 依赖 `004` 整体，见"矛盾"第 9 条）。
- 每个任务一个分支上的一个或几个提交，评审者可以单独拒绝其中一个。

---

## Task 1：`backtest/decile_readout.py` — 一个截面的读出与一条序列的离散度

**Issue:** `V2-P7-004`。**依据:** 规格 §3.3、§3.4；周报的单记录算术（`strategy_survivor_forward.py:678-703`、`:767-871`、`:933-945`）；T8 §3.1。

**Files:**
- Create: `src/openalpha_cn/backtest/decile_readout.py`
- Modify: `pyproject.toml`（`backtest-studies-touch-no-store` 与 `backtest-studies-reach-no-composition-root` 的 `source_modules` 各按字母序加 `"openalpha_cn.backtest.decile_readout"`）
- Create: `tests/unit/backtest/test_decile_readout.py`、`tests/unit/test_decile_readout_agreement.py`

**Interfaces（产出；后续任务依赖，名字与类型不得改）:**

```python
DECILES: Final[int] = 10
RankIcAbsence = Literal["fewer_than_two_labelled", "scores_tied", "labels_tied"]


class DecileReadoutError(ValueError): ...


@dataclass(frozen=True, slots=True, kw_only=True)
class CrossSectionReadout:
    scope: int  # 打过分的名字
    labelled: int
    unlabelled: int
    scope_by_decile: tuple[int, ...]  # 10 个，最差在前，结果之前切
    labelled_by_decile: tuple[int, ...]
    rank_ic: float | None
    rank_ic_absence: RankIcAbsence | None
    equal_weight: float | None  # 有标签名字的标签均值
    decile_means: tuple[float | None, ...]  # 10 个，十分位 0 = 最差
    decile_excess: tuple[float | None, ...]  # 十分位均值 − 等权
    tail_excess: tuple[
        tuple[int, float | None], ...
    ]  # (k, 剔除最差 n*k//10 后有标签名字的均值 − 等权)
    survivors_minus_ew: float | None  # k = loser_decile_count 的那一项
    ew_minus_worst_decile: float | None
    top_minus_bottom: float | None


def read_cross_section(
    scored: Sequence[tuple[str, float]],
    labels: Mapping[str, float],
    *,
    loser_decile_count: int,
    tails: Sequence[int] = (1, 2, 3),
) -> CrossSectionReadout: ...


SeriesAbsence = Literal["no_values", "fewer_than_two_values", "no_dispersion"]


@dataclass(frozen=True, slots=True, kw_only=True)
class SeriesSummary:
    n: int
    mean: float | None
    sd: float | None  # ddof = 1
    se: float | None  # sd / sqrt(n)
    t: float | None  # mean / se
    share_positive: float | None
    lag1_autocorrelation: float | None  # n >= 3
    by_year: tuple[tuple[int, int, float], ...]  # (year, n, mean)
    absence: SeriesAbsence | None


def summarize_series(values: Sequence[tuple[date, float]]) -> SeriesSummary: ...
```

`labels` 只放**有标签**的名字（没有的就是未贴标签，计数、不进任何数）。所有数都只在有标签的名字上算；十分位与幸存者在**整个打分范围**上、在任何结果之前切（与周报同一规则）。

- [ ] **Step 1：写失败的测试** `tests/unit/backtest/test_decile_readout.py`：

```python
"""`backtest/decile_readout.py` (`V2-P7-004`): one cross section's deciles, tails and rank IC, and
one dated series' dispersion, held to hand-computed numbers on cross sections generated here."""

from __future__ import annotations

import math
import random
import statistics
from datetime import date

import pytest

from openalpha_cn.backtest.decile_readout import (
    DECILES,
    DecileReadoutError,
    read_cross_section,
    summarize_series,
)

CODES = tuple(f"6000{n:02d}.SH" for n in range(20))
"""Twenty names; `CODES[p]` scores `p`, so worst first is code order."""
SCORED = tuple((code, float(position)) for position, code in enumerate(CODES))


def tail_labels(scale: float = 1.0) -> dict[str, float]:
    """A loser tail: positions 0-1 lose 3%, positions 2-3 lose 1%, the other sixteen gain 1%."""

    def label(position: int) -> float:
        if position < 2:
            return -0.03 * scale
        if position < 4:
            return -0.01 * scale
        return 0.01 * scale

    return {code: label(position) for position, code in enumerate(CODES)}


def test_a_loser_tail_is_read_as_hand_computed() -> None:
    """EW = (2(-0.03) + 2(-0.01) + 16(0.01)) / 20 = 0.004. Decile 0 is positions 0-1, decile 1
    positions 2-3. The labels tie in three groups (average ranks 1.5, 3.5, 12.5); over centred
    ranks the covariance sum is 324, the score ranks' sum of squares 665 and the labels' 324, so
    the rank IC is sqrt(324 / 665). Dropping the worst 2 (k=1) leaves 0.14 / 18."""
    read = read_cross_section(SCORED, tail_labels(), loser_decile_count=2, tails=(1, 2, 3))

    assert (read.scope, read.labelled, read.unlabelled) == (20, 20, 0)
    assert read.scope_by_decile == (2,) * 10
    assert read.labelled_by_decile == (2,) * 10
    assert read.equal_weight == pytest.approx(0.004, abs=1e-15)
    assert read.decile_means[0] == pytest.approx(-0.03, abs=1e-15)
    assert read.decile_means[1] == pytest.approx(-0.01, abs=1e-15)
    assert all(mean == pytest.approx(0.01, abs=1e-15) for mean in read.decile_means[2:])
    assert read.decile_excess[0] == pytest.approx(-0.034, abs=1e-15)
    assert read.decile_excess[1] == pytest.approx(-0.014, abs=1e-15)
    assert all(excess == pytest.approx(0.006, abs=1e-15) for excess in read.decile_excess[2:])
    assert [k for k, _ in read.tail_excess] == [1, 2, 3]
    assert read.tail_excess[0][1] == pytest.approx(0.14 / 18 - 0.004, abs=1e-15)
    assert read.tail_excess[1][1] == pytest.approx(0.006, abs=1e-15)
    assert read.tail_excess[2][1] == pytest.approx(0.006, abs=1e-15)
    assert read.survivors_minus_ew == pytest.approx(0.006, abs=1e-15)
    assert read.ew_minus_worst_decile == pytest.approx(0.034, abs=1e-15)
    assert read.top_minus_bottom == pytest.approx(0.04, abs=1e-15)
    assert read.rank_ic == pytest.approx(math.sqrt(324 / 665), abs=1e-12)
    assert read.rank_ic_absence is None


def test_reversing_the_scores_reverses_the_sign_of_every_screen_metric() -> None:
    """Every metric is oriented so that greater than zero supports the screen."""
    forward = read_cross_section(SCORED, tail_labels(), loser_decile_count=2)
    backward = read_cross_section(
        tuple((code, -score) for code, score in SCORED), tail_labels(), loser_decile_count=2
    )

    for name in ("rank_ic", "survivors_minus_ew", "ew_minus_worst_decile", "top_minus_bottom"):
        assert getattr(forward, name) > 0, name
        assert getattr(backward, name) < 0, name


def test_an_unlabelled_name_keeps_its_decile_and_leaves_every_number() -> None:
    """The deciles are cut over the whole scope before any outcome; one of the worst and one of
    the best lose their label and are counted, never read as zero."""
    labels = tail_labels()
    del labels[CODES[0]], labels[CODES[19]]

    read = read_cross_section(SCORED, labels, loser_decile_count=2)

    assert (read.scope, read.labelled, read.unlabelled) == (20, 18, 2)
    assert read.scope_by_decile == (2,) * 10
    assert read.labelled_by_decile == (1, *(2,) * 8, 1)
    assert read.equal_weight == pytest.approx((-0.03 - 0.02 + 15 * 0.01) / 18, abs=1e-15)
    assert read.decile_means[0] == pytest.approx(-0.03, abs=1e-15)
    assert read.decile_means[9] == pytest.approx(0.01, abs=1e-15)


def test_the_cut_is_by_score_then_code_and_does_not_depend_on_input_order() -> None:
    """600001 and 600009 tie at -1.0 across the first decile boundary (positions 1 and 2 of
    20): the code breaks the tie, so 600001 is in decile 0 and 600009 is not."""
    others = tuple((f"6001{n:02d}.SH", 1.0 + n) for n in range(17))
    scored = (("600009.SH", -1.0), ("600001.SH", -1.0), ("600005.SH", -3.0), *others)
    labels = {code: 0.0 for code, _ in scored} | {"600001.SH": -0.05, "600009.SH": 0.05}

    read = read_cross_section(scored, labels, loser_decile_count=2)
    shuffled = list(scored)
    random.Random(4).shuffle(shuffled)

    assert read.decile_means[0] == pytest.approx(-0.025, abs=1e-15)
    assert read_cross_section(tuple(shuffled), labels, loser_decile_count=2) == read


def test_a_tail_drops_n_k_tenths_rounded_down() -> None:
    """Nineteen names: k=1 drops 19 // 10 = 1 name (position 0), not round(1.9) = 2."""
    scored = tuple((f"6000{n:02d}.SH", float(n)) for n in range(19))
    labels = {code: 0.0 for code, _ in scored} | {"600000.SH": -1.0, "600001.SH": 1.0}

    read = read_cross_section(scored, labels, loser_decile_count=1, tails=(1,))

    assert read.equal_weight == pytest.approx(0.0, abs=1e-15)
    assert read.tail_excess == ((1, pytest.approx(1 / 18, abs=1e-15)),)


@pytest.mark.parametrize(
    ("scored", "labels", "code"),
    [
        (SCORED[:1], {CODES[0]: 0.01}, "fewer_than_two_labelled"),
        (
            tuple((code, 1.0) for code in CODES[:3]),
            {code: float(index) for index, code in enumerate(CODES[:3])},
            "scores_tied",
        ),
        (SCORED[:3], {code: 0.01 for code in CODES[:3]}, "labels_tied"),
    ],
)
def test_a_degenerate_rank_ic_is_absent_with_a_code(
    scored: tuple[tuple[str, float], ...], labels: dict[str, float], code: str
) -> None:
    read = read_cross_section(scored, labels, loser_decile_count=2)

    assert (read.rank_ic, read.rank_ic_absence) == (None, code)


@pytest.mark.parametrize(
    ("scored", "labels", "options", "said"),
    [
        ((("600000.SH", 1.0), ("600000.SH", 2.0)), {}, {}, "more than once"),
        ((("600000.SH", math.nan),), {}, {}, "finite"),
        (SCORED[:2], {"000001.SZ": 0.01}, {}, "not scored"),
        ((), {}, {}, "empty"),
        (SCORED, {}, {"loser_decile_count": 10}, "loser_decile_count"),
        (SCORED, {}, {"tails": (0,)}, "tail"),
        (SCORED, {}, {"tails": (1, 1)}, "tail"),
    ],
)
def test_a_cross_section_that_cannot_be_read_is_refused_by_name(
    scored: tuple[tuple[str, float], ...],
    labels: dict[str, float],
    options: dict[str, object],
    said: str,
) -> None:
    arguments: dict[str, object] = {"loser_decile_count": 2, **options}
    with pytest.raises(DecileReadoutError, match=said):
        read_cross_section(scored, labels, **arguments)  # type: ignore[arg-type]


def test_a_cyclic_no_signal_panel_shows_no_structure_on_average() -> None:
    """Ten names scored 0..9 over ten sessions; on session t the name of rank r earns
    ((r + t) mod 10) / 100. Each decile holds one name and earns 4.5% on average; the rank IC of
    session t is (33 - 2 t (10 - t)) / 33, and the ten sum to zero: nothing to find."""
    codes = CODES[:10]
    scored = tuple((code, float(rank)) for rank, code in enumerate(codes))
    reads = [
        read_cross_section(
            scored,
            {code: ((rank + t) % 10) / 100 for rank, code in enumerate(codes)},
            loser_decile_count=2,
        )
        for t in range(10)
    ]

    assert [read.rank_ic for read in reads] == pytest.approx(
        [(33 - 2 * t * (10 - t)) / 33 for t in range(10)], abs=1e-12
    )
    for decile in range(DECILES):
        mean = statistics.fmean(read.decile_excess[decile] for read in reads)  # type: ignore[misc]
        assert mean == pytest.approx(0.0, abs=1e-15), decile
    for name in ("survivors_minus_ew", "ew_minus_worst_decile", "top_minus_bottom"):
        assert statistics.fmean(getattr(read, name) for read in reads) == pytest.approx(
            0.0, abs=1e-15
        ), name
    series = summarize_series(
        [(date(2021, 1, 4 + t), read.rank_ic) for t, read in enumerate(reads)]  # type: ignore[misc]
    )
    assert series.mean == pytest.approx(0.0, abs=1e-12)
    assert series.sd == pytest.approx(math.sqrt(2442 / 1089 / 9), rel=1e-12)


def test_a_series_summary_is_hand_computed() -> None:
    """[0.1, 0.2, -0.1, 0.4]: mean 0.15, squared deviations sum 0.13, so sd = sqrt(0.13 / 3);
    lag-1 products -0.0025 - 0.0125 - 0.0625 = -0.0775 over 0.13."""
    summary = summarize_series(
        [
            (date(2020, 3, 2), 0.1),
            (date(2020, 4, 1), 0.2),
            (date(2021, 3, 1), -0.1),
            (date(2021, 4, 1), 0.4),
        ]
    )

    assert summary.n == 4
    assert summary.mean == pytest.approx(0.15, abs=1e-15)
    assert summary.sd == pytest.approx(math.sqrt(0.13 / 3), abs=1e-15)
    assert summary.se == pytest.approx(math.sqrt(0.13 / 3) / 2, abs=1e-15)
    assert summary.t == pytest.approx(0.15 / (math.sqrt(0.13 / 3) / 2), abs=1e-12)
    assert summary.share_positive == 0.75
    assert summary.lag1_autocorrelation == pytest.approx(-0.0775 / 0.13, abs=1e-12)
    assert summary.by_year == ((2020, 2, pytest.approx(0.15)), (2021, 2, pytest.approx(0.15)))
    assert summary.absence is None


def test_a_degenerate_series_says_what_it_lacks() -> None:
    assert summarize_series([]).absence == "no_values"
    one = summarize_series([(date(2021, 1, 4), 0.1)])
    assert (one.n, one.mean, one.sd, one.absence) == (1, 0.1, None, "fewer_than_two_values")
    flat = summarize_series([(date(2021, 1, 4), 0.1), (date(2021, 2, 1), 0.1)])
    assert (flat.sd, flat.t, flat.absence) == (0.0, None, "no_dispersion")
    with pytest.raises(DecileReadoutError, match="ascending"):
        summarize_series([(date(2021, 2, 1), 0.1), (date(2021, 1, 4), 0.2)])
    with pytest.raises(DecileReadoutError, match="finite"):
        summarize_series([(date(2021, 1, 4), math.inf)])
```

- [ ] **Step 2：写与周报算术一致的测试** `tests/unit/test_decile_readout_agreement.py`（放在 `tests/unit/` 才能导入同目录的周报测试夹具）：

```python
"""The decile readout is the forward report's arithmetic (`V2-P7-004`): one cross section read by
`decile_readout` and by `strategy_survivor_forward.record_outcome` gives the same numbers, bit
for bit, on the forward tests' twenty-name scope with its tie across the first decile boundary."""

from __future__ import annotations

from test_strategy_survivor_forward import TWENTY_SCORES, by_position, hand_outcome

from openalpha_cn.backtest.decile_readout import read_cross_section


def test_the_readout_is_the_forward_reports_arithmetic() -> None:
    answers = by_position(
        lambda position: ("halted_session",) if position in (3, 11) else 0.001 * position
    )
    labels = {code: value for code, value in answers.items() if isinstance(value, float)}

    outcome = hand_outcome(TWENTY_SCORES, answers)
    read = read_cross_section(tuple(TWENTY_SCORES.items()), labels, loser_decile_count=2)

    assert read.rank_ic == outcome.rank_ic
    assert read.equal_weight == outcome.investable_ew
    assert read.survivors_minus_ew == outcome.survivors_minus_ew
    assert read.ew_minus_worst_decile == outcome.ew_minus_worst_decile
    assert read.top_minus_bottom == outcome.top_minus_bottom
    assert read.scope_by_decile == outcome.scope_by_decile
    assert (read.labelled, read.unlabelled) == (outcome.labelled, outcome.unlabelled)
```

- [ ] **Step 3：运行，确认失败。** `PYTHONPATH="$PWD/src" uv run pytest tests/unit/backtest/test_decile_readout.py tests/unit/test_decile_readout_agreement.py -q`。预期：两份文件都在收集时 `ModuleNotFoundError: No module named 'openalpha_cn.backtest.decile_readout'`（模块不存在）。

- [ ] **Step 4：最小实现。** 要点（非显然处给代码）：

```python
def _rank_ic(
    scores: Sequence[float], labels: Sequence[float]
) -> tuple[float | None, RankIcAbsence | None]:
    """`strategy_survivor_forward._rank_ic`'s three checks and its two calls, in its order."""
    if len(scores) < 2:
        return None, "fewer_than_two_labelled"
    if len(set(scores)) == 1:
        return None, "scores_tied"
    if len(set(labels)) == 1:
        return None, "labels_tied"
    return statistics.correlation(average_ranks(scores), average_ranks(labels)), None
```

`read_cross_section`：先校验（代码不重复、分数与标签有限、`labels` 的键都在 `scored` 里、范围非空、`0 <= loser_decile_count <= 9`、每个 tail 在 1..9 且不重复——错误信息分别含 `more than once`、`finite`、`not scored`、`empty`、`loser_decile_count`、`tail`）；`worst_first = sorted((score, code) for code, score in scored)`；十分位 `DECILES * p // n`；`labelled = [(score, code) for score, code in worst_first if code in labels]`（**最差在前的顺序**，秩 IC 与均值都按这个顺序算，才能与周报逐位相同）；均值一律 `statistics.fmean`；尾部 k：`kept = {code for _, code in worst_first[n * k // DECILES:]}`，`mean(labels[c] for _, c in labelled if c in kept) - equal_weight`；`survivors_minus_ew` 用 `loser_decile_count` 算同一个量；`ew_minus_worst_decile = equal_weight - decile_means[0]`；`top_minus_bottom = decile_means[9] - decile_means[0]`；任一边没有值就 `None`。`summarize_series`：日期严格升序否则拒绝（含 `ascending`）、值有限否则拒绝（含 `finite`）；`sd = statistics.stdev`；`sd == 0` 时 `t=None`、`absence="no_dispersion"`；一阶自相关 `math.fsum((x[t]-m)*(x[t+1]-m)) / math.fsum((x[t]-m)**2)`，n<3 或分母 0 为 `None`；`by_year` 按年升序。模块文档串写明：十分位 0 = 最差、与周报同一规则；探索脚本的 D1 = 最好、不得混用；未贴标签的名字只计数；本模块不读面板、没有随机成分。

- [ ] **Step 5：运行，确认通过。** 同一命令，预期全部通过；再跑 `uv run lint-imports`、`uv run pytest tests/unit/test_import_layering.py -q`（新模块已在两个合约里）、`ruff check`、`ruff format --check`、`mypy src scripts`。

- [ ] **Step 6：变异探针**（每个都要让至少一个测试变红；做法：`cp src/openalpha_cn/backtest/decile_readout.py "$SCRATCH/decile.bak"`，改，`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" uv run pytest tests/unit/backtest/test_decile_readout.py tests/unit/test_decile_readout_agreement.py -q`，`cp` 回来，`cmp` 确认）：(a) 十分位改成 `(DECILES * p + n - 1) // n`；(b) 排序键去掉 `code`（只按分数、保留输入顺序）；(c) 等权改成在整个范围上取（未贴标签记 0）；(d) 尾部改成 `round(n * k / DECILES)`；(e) `stdev` 改成 `pstdev`；(f) 秩 IC 按代码顺序而不是最差在前的顺序喂（观察一致性测试是否仍逐位相等——若相等，在报告里写明并保留探针结论）。

- [ ] **Step 7：提交前跑 `tests/unit`（串行），再提交：** `git add src/openalpha_cn/backtest/decile_readout.py pyproject.toml tests/unit/backtest/test_decile_readout.py tests/unit/test_decile_readout_agreement.py && git commit -m "feat(V2-P7-004): one cross section's deciles, tails and rank IC, read as the forward report reads them"`（带 trailer）。

---

## Task 2：`backtest/power_analysis.py` — 功效计算器

**Issue:** `V2-P7-004`（产出给 `018`、`024`、`006a`）。**依据:** 路线调整 §2.7、V8；规格 §4.5、§9 的 2026-11-01 第 4 行；Part E 设计里"K=12 在 IC=0.05 时功效仅约 25%"那一行（与正态近似的 31% 不同，原因见"矛盾"第 3 条）。

**Files:** Create `src/openalpha_cn/backtest/power_analysis.py`；Modify `pyproject.toml`（两个合约的 `source_modules` 各加 `"openalpha_cn.backtest.power_analysis"`）；Create `tests/unit/backtest/test_power_analysis.py`。

**Interfaces（产出）:**

```python
CriticalValue = Literal["normal", "t"]
NOT_DETECTABLE_WORDING: Final = (
    'a one-sided p-value above the test level reads "not detectable at this design": a '
    'conclusion category of the design, never "no effect"'
)
POWER_BASIS: Final = (
    "the probability, under the plan's assumptions (period values normally distributed with the "
    "planning sd, independent unless a serial correlation is stated), that a one-sided test of "
    "the mean at the test level gives a p-value at or below it; a property of the design, not a "
    "result"
)


class PowerError(ValueError): ...


def t_quantile(probability: float, df: float) -> float: ...  # 0.5 <= p < 1, df >= 1
def critical_value(*, test_level: float, periods: float, kind: CriticalValue) -> float: ...
def power_of_mean_test(
    *, mean: float, sd: float, periods: float, test_level: float, kind: CriticalValue
) -> float: ...
def smallest_mean_at_power(
    *, power: float, sd: float, periods: float, test_level: float, kind: CriticalValue
) -> float: ...
def periods_for_power(
    *, mean: float, power: float, sd: float, test_level: float, kind: CriticalValue
) -> int: ...
def autocorrelation_equivalent_periods(*, periods: float, lag1: float) -> float: ...
def by_first_step_level(*, q: float, family_size: int) -> float: ...
def futility_stop_probability(
    *, mean: float, sd: float, interim_periods: int, threshold: float
) -> float: ...
def power_with_futility(
    *,
    mean: float,
    sd: float,
    periods: int,
    interim_periods: int,
    threshold: float,
    test_level: float,
    kind: CriticalValue,
) -> float: ...
def power_table_lines(
    *,
    periods: int,
    sd: float,
    test_level: float,
    means: Sequence[float],
    target_power: float,
    kind: CriticalValue,
) -> tuple[str, ...]: ...
```

规则：`test_level` 在 (0, 0.5)；`power` 在 (0, 1)；`sd > 0`；`periods >= 2`（t 的自由度 `periods - 1 >= 1`）；`-1 < lag1 < 1`；`1 <= interim_periods < periods`；`periods_for_power` 要 `mean > 0`；任何非有限值拒绝。`t` 口径 = 正态近似里把临界值换成 `t_{1-level, periods-1}`（"t 调整的正态近似"），模块文档串写明：登记的检验是符号翻转随机化检验，小样本行为更接近 t 检验，所以 t 口径在小 K 时更保守；两者都只是近似。

- [ ] **Step 1：写失败的测试** `tests/unit/backtest/test_power_analysis.py`：

```python
"""`backtest/power_analysis.py` (`V2-P7-004`): the power of the one-sided test of a mean over
non-overlapping periods, held to the arithmetic the P7 route was decided on (sd 0.15, one-sided
0.05) and to published t quantiles."""

from __future__ import annotations

import math
import re

import pytest

from openalpha_cn.backtest.power_analysis import (
    NOT_DETECTABLE_WORDING,
    PowerError,
    autocorrelation_equivalent_periods,
    by_first_step_level,
    critical_value,
    futility_stop_probability,
    periods_for_power,
    power_of_mean_test,
    power_table_lines,
    power_with_futility,
    smallest_mean_at_power,
    t_quantile,
)

SD = 0.15
LEVEL = 0.05
FORBIDDEN = re.compile(
    r"\b(significant|effective|confirmed|alpha|outperform|passed|failed|proven)", re.IGNORECASE
)


def power(mean: float, periods: float, kind: str = "normal", level: float = LEVEL) -> float:
    return power_of_mean_test(mean=mean, sd=SD, periods=periods, test_level=level, kind=kind)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("periods", "mean", "expected"),
    [
        (36, 0.05, 0.6387600313123356),
        (36, 0.037, 0.43452959661329793),
        (36, 0.10, 0.9907422946265046),
        (56, 0.05, 0.8022219701621711),
        (12, 0.05, 0.3120127868184547),
        (12, 0.10, 0.746830004913777),
    ],
)
def test_the_normal_power_is_the_routes_arithmetic(
    periods: int, mean: float, expected: float
) -> None:
    """Phi(sqrt(K) * mean / sd - z_0.95): K=36 at 0.05 is Phi(2 - 1.6448536) = 0.639."""
    assert power(mean, periods) == pytest.approx(expected, abs=1e-12)


def test_the_normal_answers_to_the_routes_questions() -> None:
    assert critical_value(test_level=LEVEL, periods=36, kind="normal") == pytest.approx(
        1.6448536269514715, abs=1e-12
    )
    assert smallest_mean_at_power(
        power=0.8, sd=SD, periods=36, test_level=LEVEL, kind="normal"
    ) == pytest.approx(0.062161871513109646, abs=1e-12)
    needed = {
        mean: periods_for_power(mean=mean, power=0.8, sd=SD, test_level=LEVEL, kind="normal")
        for mean in (0.037, 0.05, 0.07, 0.10)
    }
    assert needed == {0.037: 102, 0.05: 56, 0.07: 29, 0.10: 14}


@pytest.mark.parametrize(
    ("probability", "df", "expected", "tolerance"),
    [
        (0.95, 1, 6.313752, 1e-4),
        (0.95, 11, 1.795885, 1e-5),
        (0.95, 23, 1.713872, 1e-5),
        (0.95, 35, 1.689572, 1e-5),
        (0.95, 55, 1.673034, 1e-5),
        (0.80, 11, 0.875530, 1e-5),
    ],
)
def test_t_quantiles_are_the_published_ones(
    probability: float, df: int, expected: float, tolerance: float
) -> None:
    assert t_quantile(probability, df) == pytest.approx(expected, abs=tolerance)


def test_the_t_convention_needs_one_more_period_than_the_normal_one_at_ic_005() -> None:
    """With t_{0.95, K-1}: K=12 gives 0.261 (Part E's "about 25%"), K=36 0.622, K=56 0.794 and
    K=57 0.801 -- so 80% at IC 0.05 needs 57 periods, not 56."""
    assert power(0.05, 12, "t") == pytest.approx(0.260701, abs=1e-6)
    assert power(0.05, 36, "t") == pytest.approx(0.621882, abs=1e-6)
    assert power(0.037, 36, "t") == pytest.approx(0.417001, abs=1e-6)
    assert power(0.05, 56, "t") == pytest.approx(0.794292, abs=1e-6)
    assert periods_for_power(mean=0.05, power=0.8, sd=SD, test_level=LEVEL, kind="t") == 57


def test_serial_correlation_shrinks_the_independent_periods() -> None:
    assert autocorrelation_equivalent_periods(periods=36, lag1=0.1) == pytest.approx(
        36 * 0.9 / 1.1, abs=1e-12
    )
    assert autocorrelation_equivalent_periods(periods=36, lag1=0.0) == 36


def test_a_secondary_is_powered_at_the_first_benjamini_yekutieli_step() -> None:
    """q / (m H_m) with m = 3: 0.10 / (3 * 11/6) = 0.0181818..., where K=36 at 0.05 has 0.463."""
    level = by_first_step_level(q=0.10, family_size=3)
    assert level == pytest.approx(0.10 / (3 * (1 + 1 / 2 + 1 / 3)), abs=1e-15)
    assert power(0.05, 36, level=level) == pytest.approx(0.4630162109014593, abs=1e-12)


@pytest.mark.parametrize(
    ("mean", "periods", "threshold", "expected"),
    [
        (0.05, 18, 0.0, 0.07864960352514258),
        (0.0, 18, 0.0, 0.5),
        (0.037, 18, 0.0, 0.1476609610726271),
        (0.0, 24, 0.02, 0.7431854433034379),
        (0.037, 24, 0.02, 0.28937289034228963),
        (0.05, 24, 0.02, 0.16359343889515288),
        (0.10, 24, 0.02, 0.004490335620949417),
    ],
)
def test_the_stop_probability_of_a_rule_on_the_mean_is_hand_computed(
    mean: float, periods: int, threshold: float, expected: float
) -> None:
    """Phi((threshold - mean) / (sd / sqrt(k))): the second block is a G-0-shaped rule (mean IC
    at or below 0.02 over about 24 periods) at four true means."""
    assert futility_stop_probability(
        mean=mean, sd=SD, interim_periods=periods, threshold=threshold
    ) == pytest.approx(expected, abs=1e-12)


def test_an_interim_futility_look_costs_less_than_a_point_of_power_at_ic_005() -> None:
    """One look at K/2 = 18 stopping when the mean is at or below 0: P(continue and the final
    test at K=36 has p <= 0.05). A seeded 200,000-draw simulation gave 0.6312 (not run here)."""
    with_look = power_with_futility(
        mean=0.05,
        sd=SD,
        periods=36,
        interim_periods=18,
        threshold=0.0,
        test_level=LEVEL,
        kind="normal",
    )
    assert with_look == pytest.approx(0.6308415396468372, abs=1e-6)
    assert power_with_futility(
        mean=0.0,
        sd=SD,
        periods=36,
        interim_periods=18,
        threshold=0.0,
        test_level=LEVEL,
        kind="normal",
    ) == pytest.approx(0.04875, abs=1e-6)
    assert power_with_futility(
        mean=0.05,
        sd=SD,
        periods=36,
        interim_periods=18,
        threshold=-10.0,
        test_level=LEVEL,
        kind="normal",
    ) == pytest.approx(power(0.05, 36), abs=1e-6)


def test_the_power_lines_say_not_detectable_is_a_category_of_the_design() -> None:
    lines = power_table_lines(
        periods=36,
        sd=SD,
        test_level=LEVEL,
        means=(0.037, 0.05, 0.1),
        target_power=0.8,
        kind="normal",
    )

    assert lines == (
        "power of a one-sided test of a mean over 36 non-overlapping periods (test level 0.05, "
        "per-period sd 0.15, normal critical value 1.6449; a property of the design, not a "
        "result):",
        "  a true mean of 0.037: probability 0.435 of a one-sided p at or below 0.05; 102 "
        "periods for 0.8",
        "  a true mean of 0.05: probability 0.639 of a one-sided p at or below 0.05; 56 periods "
        "for 0.8",
        "  a true mean of 0.1: probability 0.991 of a one-sided p at or below 0.05; 14 periods "
        "for 0.8",
        "  the smallest mean with probability 0.8: 0.0622",
        f"  {NOT_DETECTABLE_WORDING}",
    )
    assert 'never "no effect"' in NOT_DETECTABLE_WORDING
    assert FORBIDDEN.search("\n".join(lines)) is None


@pytest.mark.parametrize(
    ("call", "said"),
    [
        (lambda: power(0.05, 1), "periods"),
        (
            lambda: power_of_mean_test(
                mean=0.05, sd=0.0, periods=36, test_level=LEVEL, kind="normal"
            ),
            "sd",
        ),
        (lambda: power(0.05, 36, level=0.5), "test level"),
        (lambda: power(0.05, 36, kind="z"), "critical"),
        (lambda: power(math.nan, 36), "finite"),
        (
            lambda: periods_for_power(mean=0.0, power=0.8, sd=SD, test_level=LEVEL, kind="normal"),
            "mean",
        ),
        (
            lambda: smallest_mean_at_power(
                power=1.0, sd=SD, periods=36, test_level=LEVEL, kind="normal"
            ),
            "power",
        ),
        (lambda: autocorrelation_equivalent_periods(periods=36, lag1=1.0), "lag"),
        (
            lambda: power_with_futility(
                mean=0.05,
                sd=SD,
                periods=36,
                interim_periods=36,
                threshold=0.0,
                test_level=LEVEL,
                kind="normal",
            ),
            "interim",
        ),
        (lambda: t_quantile(0.4, 10), "probability"),
        (lambda: t_quantile(0.95, 0.5), "df"),
        (lambda: by_first_step_level(q=0.1, family_size=0), "family"),
    ],
)
def test_a_question_without_an_answer_is_refused_by_name(call: object, said: str) -> None:
    with pytest.raises(PowerError, match=said):
        call()  # type: ignore[operator]
```

- [ ] **Step 2：运行，确认失败。** `PYTHONPATH="$PWD/src" uv run pytest tests/unit/backtest/test_power_analysis.py -q`。预期：收集时 `ModuleNotFoundError: No module named 'openalpha_cn.backtest.power_analysis'`。

- [ ] **Step 3：最小实现。** 非显然部分的代码（确定性：固定网格、固定迭代次数、`math.fsum`）：

```python
_STANDARD: Final = NormalDist()
_T_PANELS: Final[int] = 4096  # Simpson panels on [0, |x|]
_T_BISECTIONS: Final[int] = 80
_T_UPPER: Final[float] = 64.0
_FUTILITY_PANELS: Final[int] = 4000
_FUTILITY_SPAN: Final[float] = 24.0


def _t_density(x: float, df: float) -> float:
    log_c = math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi)
    return math.exp(log_c - (df + 1) / 2 * math.log1p(x * x / df))


def _t_cdf(x: float, df: float) -> float:
    if x == 0:
        return 0.5
    width = abs(x) / _T_PANELS
    terms = (
        (1 if index in (0, _T_PANELS) else 4 if index % 2 else 2) * _t_density(index * width, df)
        for index in range(_T_PANELS + 1)
    )
    area = math.fsum(terms) * width / 3
    return 0.5 + area if x > 0 else 0.5 - area


@functools.lru_cache(maxsize=512)
def t_quantile(probability: float, df: float) -> float:
    # refuse: not 0.5 <= probability < 1 ("probability"), not finite or df < 1 ("df")
    if probability == 0.5:
        return 0.0
    low, high = 0.0, _T_UPPER
    for _ in range(_T_BISECTIONS):
        middle = (low + high) / 2
        if _t_cdf(middle, df) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def power_with_futility(*, mean, sd, periods, interim_periods, threshold, test_level, kind):
    """P(interim mean > threshold and final mean > the critical mean), the two standardised
    means bivariate normal with correlation sqrt(k / K): one fixed-grid Simpson integral."""
    r = math.sqrt(interim_periods / periods)
    c1 = (threshold - mean) / (sd / math.sqrt(interim_periods))
    c2 = (
        critical_value(test_level=test_level, periods=periods, kind=kind)
        - math.sqrt(periods) * mean / sd
    )
    lower = max(c1, -_FUTILITY_SPAN / 2)
    width = _FUTILITY_SPAN / _FUTILITY_PANELS
    scale = math.sqrt(1 - r * r)
    terms = (
        (1 if index in (0, _FUTILITY_PANELS) else 4 if index % 2 else 2)
        * _STANDARD.pdf(lower + index * width)
        * (1 - _STANDARD.cdf((c2 - r * (lower + index * width)) / scale))
        for index in range(_FUTILITY_PANELS + 1)
    )
    return math.fsum(terms) * width / 3
```

`critical_value`：`normal` → `_STANDARD.inv_cdf(1 - test_level)`；`t` → `t_quantile(1 - test_level, periods - 1)`；其他 → `PowerError("... critical value ...")`。`power_of_mean_test = Φ(√periods·mean/sd − critical)`；`smallest_mean_at_power = (critical + z_power)·sd/√periods`；`periods_for_power`：正态 `max(2, ceil(((z_{1-level} + z_power)·sd/mean)²))`，t 从正态答案起逐个加一直到 `power_of_mean_test(..., kind="t") >= power`（上限 100,000，超过拒绝）；`by_first_step_level = q / (family_size * multiple_testing.harmonic_number(family_size))`（复用 `backtest/multiple_testing.py:302`）；`power_table_lines` 的格式见测试（`{:g}` 打印输入、`{:.3f}` 打印概率、`{:.4f}` 打印临界值与最小均值）。

- [ ] **Step 4：运行，确认通过**；`lint-imports`、`tests/unit/test_import_layering.py`、`ruff`、`mypy src scripts`。

- [ ] **Step 5：变异探针**（同 Task 1 的做法）：(a) `1 - test_level` 改成 `1 - test_level / 2`（双侧）；(b) 去掉 `math.sqrt(periods)`；(c) 正态的 `ceil` 改成 `round`（`0.07 → 29` 那一条必须变红）；(d) t 的自由度改成 `periods`；(e) BY 的 `harmonic_number(family_size)` 改成 `1`；(f) 无效性积分的相关系数改成 `0`（`0.6308` 那一条必须变红）。

- [ ] **Step 6：** 跑 `tests/unit`，提交：`feat(V2-P7-004): the power of the one-sided test of a mean, normal and t, with an interim futility look`。

---

## Task 3：`scripts/research/p7_windows.py` — 窗口、按年封顶的只读面板、信号日计划

**Issue:** `V2-P7-004`。**依据:** 规格 §7.2；路线图 `004` 验收"默认拒绝读取验证期与保留窗口"；`grid.py:125-130`、`:582-590`；`panel/store.py` 的公开读取门（`query` `:2238`、`profile_query` `:2322`、`read_coverage` `:2464`、`registered_years` `:2482`、`partition_stamps` `:2560`、`assess_readiness` `:2624`、`assessed` `:2642`、`read_if_ready` `:2692`、`read_visible_at` `:2731`、`partition_content_hash` `:2219`）与写入门（`write_partition` `:1815`、`write_partitions` `:1989`、`record_coverage` `:2388`、`remove_partition` `:2499`、`merge_base` `:1748`）；`PanelStore.__init__` 会 `mkdir`（`:1693-1694`）。

**Files:** Create `scripts/research/p7_windows.py`、`tests/unit/scripts/test_p7_windows.py`。

**Interfaces（产出）:**

```python
import grid as _grid


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagnosticWindow:
    name: str
    first: date
    last: date
    ceiling_year: int  # 不读这一年之后的任何分区


SECOND_USE_LAST: Final[date] = date(2026, 9, 24)
RESEARCH: Final[DiagnosticWindow]  # discovery 阶段的首尾，ceiling 2021
VALIDATION: Final[DiagnosticWindow]  # validation 阶段的首尾，ceiling 2023
SECOND_USE: Final[DiagnosticWindow]  # holdout 阶段的首日 .. SECOND_USE_LAST，ceiling 2026
WINDOWS: Final[Mapping[str, DiagnosticWindow]]  # {"research", "validation", "second_use"}


class DiagnosticWindowError(ValueError): ...


class WindowCappedStore(PanelStore):
    def __init__(self, root: Path, *, window: DiagnosticWindow) -> None: ...
    @property
    def window(self) -> DiagnosticWindow: ...
    @property
    def served(self) -> tuple[tuple[str, int], ...]: ...  # 每个被读过的 (dataset, year)，排序
    def partitions_read(
        self,
    ) -> tuple[tuple[str, int, str | None], ...]: ...  # 加 content hash（经 super()，不记为读）


def capped_panel_store(runtime_dir: Path, window: DiagnosticWindow) -> WindowCappedStore:
    ...
    # runtime_dir / panel_view.PANEL_SUBDIRECTORY


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalPlan:
    window: DiagnosticWindow
    first: date
    last: date
    stride: int
    horizons: tuple[int, ...]
    sessions: tuple[date, ...]  # 留下的信号日
    embargoed: tuple[date, ...]  # 最长期限的标签出场晚于 window.last 的信号日（具名，不静默丢）


def plan_signal_days(
    calendar: TradingCalendar,
    *,
    window: DiagnosticWindow,
    first: date,
    last: date,
    stride: int,
    horizons: Sequence[int],
) -> SignalPlan: ...
```

`WindowCappedStore` 的规则：构造前若 `root / "catalog.duckdb"` 不是文件就拒绝（不调用 `super().__init__`，所以不建目录）；每个读取门在委托 `super()` 之前检查 `year <= ceiling_year`（`ReadinessRequirement` 则检查 `requirement.years` 每一年），超过就 `DiagnosticWindowError`（消息含数据集、年份、窗口名与"the validation window and the holdout are read only through p7_reading.read_once"）；通过的读写进 `served`；`registered_years` 与 `partition_stamps`（`PartitionStamp = tuple[str, int, str, ...]`，`panel/store.py:937`）只返回 `<= ceiling_year` 的；所有写入门一律拒绝（"read-only"）。`plan_signal_days`：`first < window.first`、`last > window.last`、`last < first`、`stride < 1`、期限不在 `1..MAX_HORIZON_COUNT`、`first` 不是交易日——都拒绝；信号日 = 从 `first` 起每 `stride` 个交易日、不晚于 `last`；对每个信号日算最长期限的出场 `calendar.shift(calendar.next_trading_day(day), max(horizons))`，晚于 `window.last` 或抛 `CalendarHorizonError`（日历在窗口末尾结束）→ 记入 `embargoed`。

- [ ] **Step 1：写失败的测试** `tests/unit/scripts/test_p7_windows.py`：

```python
"""`scripts/research/p7_windows.py` (`V2-P7-004`): the windows a P7 diagnostic may read, a panel
store that serves nothing past its window's last year and writes nothing, and the signal days a
window allows. Every store here is written at test time under `tmp_path`."""

from __future__ import annotations

import hashlib
import importlib
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import ColumnSpec, PanelStore

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


grid = _research_module("grid")
p7_windows = _research_module("p7_windows")
PROBE: Final[str] = "probe"
AS_OF: Final[datetime] = datetime(2026, 10, 9, tzinfo=UTC)


def weekday_calendar(first: date, last: date) -> TradingCalendar:
    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    return build_trading_calendar(
        "SSE", [CalendarDay(calendar_date=day, is_trading=day.weekday() < 5) for day in days]
    )


@pytest.fixture
def three_years(tmp_path: Path) -> Path:
    """A store holding one probe partition in 2021, 2022 and 2024."""
    store = PanelStore(tmp_path / "panel")
    for year in (2021, 2022, 2024):
        store.write_partition(PROBE, year, (ColumnSpec("subject", "VARCHAR"),), [(str(year),)])
    return tmp_path / "panel"


def requirement(*years: int) -> ReadinessRequirement:
    return ReadinessRequirement(
        dataset=PROBE,
        as_of=AS_OF,
        years=years,
        required_dates=None,
        required_subjects=None,
        required_fields=None,
        max_staleness=None,
    )


def test_the_three_windows_are_the_protocols_stages_and_the_spent_holdout() -> None:
    stages = grid.PROTOCOL_STAGE_WINDOWS
    research, validation, second = (
        p7_windows.WINDOWS[name] for name in ("research", "validation", "second_use")
    )
    assert (research.first, research.last, research.ceiling_year) == (
        stages["discovery"].first,
        stages["discovery"].last,
        2021,
    )
    assert (validation.first, validation.last, validation.ceiling_year) == (
        stages["validation"].first,
        stages["validation"].last,
        2023,
    )
    assert (second.first, second.last, second.ceiling_year) == (
        stages["holdout"].first,
        date(2026, 9, 24),
        2026,
    )
    assert (research.last, validation.first) == (date(2021, 12, 31), date(2022, 1, 4))


def test_a_capped_store_serves_years_up_to_its_ceiling_and_records_them(three_years: Path) -> None:
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)

    assert store.query(PROBE, year=2021, columns=("subject",)) == [("2021",)]
    assert store.served == ((PROBE, 2021),)
    assert store.registered_years(PROBE) == (2021,)
    assert all(stamp[1] <= 2021 for stamp in store.partition_stamps())


@pytest.mark.parametrize(
    "door",
    [
        lambda s: s.query(PROBE, year=2022, columns=("subject",)),
        lambda s: s.profile_query(PROBE, year=2022, columns=("subject",)),
        lambda s: s.read_coverage(PROBE, 2022),
        lambda s: s.partition_content_hash(PROBE, 2024),
        lambda s: s.read_visible_at(requirement(2021, 2022), year=2021, columns=("subject",)),
        lambda s: s.read_if_ready(requirement(2022), year=2022, columns=("subject",)),
        lambda s: s.assessed(requirement(2021, 2024)),
        lambda s: s.assess_readiness(requirement(2022)),
    ],
    ids=["query", "profile", "coverage", "hash", "visible", "ready", "assessed", "readiness"],
)
def test_a_capped_store_refuses_every_door_past_its_ceiling(
    three_years: Path, door: object
) -> None:
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)

    with pytest.raises(p7_windows.DiagnosticWindowError, match="read_once"):
        door(store)  # type: ignore[operator]
    assert store.served == ()


def test_a_capped_store_writes_nothing(three_years: Path) -> None:
    catalog = three_years / "catalog.duckdb"
    before = hashlib.sha256(catalog.read_bytes()).hexdigest()
    store = p7_windows.WindowCappedStore(three_years, window=p7_windows.RESEARCH)

    for write in (
        lambda: store.write_partition(PROBE, 2021, (ColumnSpec("subject", "VARCHAR"),), [("x",)]),
        lambda: store.write_partitions(()),
        lambda: store.remove_partition(PROBE, 2021),
        lambda: store.merge_base(PROBE, 2021),
    ):
        with pytest.raises(p7_windows.DiagnosticWindowError, match="read-only"):
            write()
    store.query(PROBE, year=2021, columns=("subject",))
    assert hashlib.sha256(catalog.read_bytes()).hexdigest() == before


def test_a_capped_store_refuses_a_root_with_no_catalog_and_creates_nothing(tmp_path: Path) -> None:
    root = tmp_path / "nowhere" / "panel"

    with pytest.raises(p7_windows.DiagnosticWindowError, match="catalog"):
        p7_windows.WindowCappedStore(root, window=p7_windows.RESEARCH)
    assert not root.exists() and not root.parent.exists()


def test_a_research_plan_embargoes_a_label_that_would_end_in_2022() -> None:
    """Weekdays from Monday 2021-11-01: sessions 0, 20, 40 are 11-01, 11-29 and 12-27. At h=20
    the exits are sessions 21, 41 and 61; session 61 is in January 2022, past the research
    window, so 12-27 is embargoed -- on a calendar running into 2022 (the window decides) and on
    one ending 2021-12-31 (the calendar's end decides)."""
    for calendar in (
        weekday_calendar(date(2021, 11, 1), date(2022, 2, 28)),
        weekday_calendar(date(2021, 11, 1), date(2021, 12, 31)),
    ):
        plan = p7_windows.plan_signal_days(
            calendar,
            window=p7_windows.RESEARCH,
            first=date(2021, 11, 1),
            last=date(2021, 12, 31),
            stride=20,
            horizons=(20,),
        )
        assert plan.sessions == (date(2021, 11, 1), date(2021, 11, 29))
        assert plan.embargoed == (date(2021, 12, 27),)


def test_the_longest_horizon_decides_the_embargo() -> None:
    calendar = weekday_calendar(date(2021, 11, 1), date(2022, 2, 28))

    plan = p7_windows.plan_signal_days(
        calendar,
        window=p7_windows.RESEARCH,
        first=date(2021, 11, 1),
        last=date(2021, 12, 31),
        stride=10,
        horizons=(5, 20),
    )

    assert all(
        calendar.shift(calendar.next_trading_day(day), 20) <= date(2021, 12, 31)
        for day in plan.sessions
    )
    assert plan.embargoed and all(day not in plan.sessions for day in plan.embargoed)


@pytest.mark.parametrize(
    ("options", "said"),
    [
        ({"first": date(2014, 12, 31)}, "before"),
        ({"last": date(2022, 1, 4)}, "validation"),
        ({"first": date(2021, 11, 10), "last": date(2021, 11, 1)}, "before its first"),
        ({"first": date(2021, 11, 6)}, "not a session"),
        ({"stride": 0}, "stride"),
        ({"horizons": (0,)}, "horizon"),
    ],
)
def test_a_plan_outside_its_window_is_refused_by_name(
    options: dict[str, object], said: str
) -> None:
    arguments: dict[str, object] = {
        "window": p7_windows.RESEARCH,
        "first": date(2021, 11, 1),
        "last": date(2021, 12, 31),
        "stride": 20,
        "horizons": (20,),
        **options,
    }
    with pytest.raises(p7_windows.DiagnosticWindowError, match=said):
        p7_windows.plan_signal_days(
            weekday_calendar(date(2014, 12, 1), date(2022, 2, 28)), **arguments
        )  # type: ignore[arg-type]
```

（`last=2022-01-04` 的拒绝消息必须点名验证期与读一次路径；`first` 早于 2015-01-05 的消息含 `before`。）

- [ ] **Step 2：运行，确认失败。** `PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_windows.py -q`。预期：`ModuleNotFoundError: No module named 'p7_windows'`。

- [ ] **Step 3：实现。** 覆写的形状（每个读取门一样）：

```python
def query(
    self,
    dataset: str,
    *,
    year: int,
    columns: Sequence[str],
    filters: Mapping[str, object] | None = None,
) -> list[tuple[object, ...]]:
    self._admit(dataset, (year,))
    return super().query(dataset, year=year, columns=columns, filters=filters)


def read_visible_at(
    self,
    requirement: ReadinessRequirement,
    *,
    year: int,
    columns: Sequence[str],
    filters: Mapping[str, object] | None = None,
    event_time_as_naive_utc: bool = False,
    newly_visible_since: datetime | None = None,
) -> PanelVisibleReadOutcome:
    self._admit(requirement.dataset, (*requirement.years, year))
    return super().read_visible_at(
        requirement,
        year=year,
        columns=columns,
        filters=filters,
        event_time_as_naive_utc=event_time_as_naive_utc,
        newly_visible_since=newly_visible_since,
    )


def _admit(self, dataset: str, years: Iterable[int]) -> None:
    late = sorted({year for year in years if year > self._window.ceiling_year})
    if late:
        raise DiagnosticWindowError(
            f"{dataset} {late} is past the {self._window.name} window's last year "
            f"{self._window.ceiling_year} ({self._window.first}..{self._window.last}); "
            "the validation window and the holdout are read only through p7_reading.read_once"
        )
    self._served.update((dataset, year) for year in years)
```

`assessed`/`assess_readiness`/`read_if_ready` 按 `requirement.years` 检查；`partition_content_hash`、`read_coverage`、`profile_query` 按参数年份检查；`partitions_read()` 用 `super().partition_content_hash` 取哈希（不经 `_admit`）。模块文档串写明：这是按年的护栏；研究期与验证期的边界恰好落在年界（2021-12-31 / 2022-01-04，2023-12-29 / 2024-01-02），二次使用段的 2026 年分区里还有 2026-09-25 之后的行，那一段只靠"标签出场不晚于 2026-09-24"挡住；本类不替代 `plan_signal_days` 的日期检查，两者都要。

- [ ] **Step 4：运行，确认通过**；`ruff`、`mypy src scripts`（覆写的签名与 `PanelStore` 一致）。

- [ ] **Step 5：变异探针：** (a) `>` 改成 `>=`（2021 的读取被拒，`serves` 测试变红）；(b) `read_visible_at` 只检查 `year` 不检查 `requirement.years`（`visible` 参数变红）；(c) 去掉 `registered_years` 的过滤；(d) `CalendarHorizonError` 不记入 `embargoed` 而是让它抛出（第二个日历变红）；(e) 嵌入期用最短期限而不是最长期限。

- [ ] **Step 6：** 跑 `tests/unit`，提交：`feat(V2-P7-004): the windows a P7 diagnostic may read, and a panel store that serves nothing past its window's last year`。

---

## Task 4：`scripts/research/p7_diagnostics.py` — 引擎、文档、CLI `explore` 与 `power`

**Issue:** `V2-P7-004`。**依赖:** Task 1、2、3。**依据:** 规格 §3.1–§3.6；T8 §1、§3.1；`build_facts_day`（`strategy_survivor_facts.py:766-830`，`as_of` 早于 16:30 才拒绝，`:811-816`，所以较晚的 `as_of` 是迟到记录核查已在用的读法）；`screen`；`OutcomeLabels` + `LabelReach`（`model_view.py:1645-1875`）；按信号年建标签读取器的先例 `strategy_view._measure_year`（`strategy_view.py:1288-1315`）；周报的 `LabelSource` 与 `NO_ADJUSTMENT_HISTORY`（`strategy_survivor_forward.py:709-717`、`:340`）；`grid.canonical_json`（`grid.py:249`）；`runtime.provenance.resolve_code_commit`（`runtime/provenance.py:90`）。

**Files:** Create `scripts/research/p7_diagnostics.py`、`tests/unit/test_p7_diagnostics.py`。

**Interfaces（产出）:**

```python
EXPLORATORY_KIND: Final = "p7-diagnostic-exploratory/v1"
READ_ONCE_KIND: Final = "p7-diagnostic-read-once/v1"
EXPLORATORY_WORDING: Final = (
    "exploratory and unregistered: not evidence -- it decides what to measure next and says "
    "nothing about returns"
)
IN_SAMPLE_WORDING: Final = (
    "in-sample: the research window was read to choose these factors and thresholds (P7 spec "
    "section 3), so nothing here is out of sample"
)
READ_ONCE_WORDING: Final = (
    "the one pre-registered read of this window: a first out-of-sample reading, not confirmatory "
    "evidence; confirmatory evidence comes only from forward records registered under V2-P7-006"
)
SECOND_USE_WORDING: Final = (
    "2024-01-02..2026-09-24 was P6's one-time holdout and has been used: a secondary, "
    "non-confirmatory reading"
)
CANDIDATE_WORDING: Final = "every score is a candidate research signal (候选), never a verdict"
EXPLORATORY_POWER_WORDING: Final = (
    "if the planning sd were this exploratory value (in-sample, measured after the factors were "
    "chosen): never a planning value by itself"
)
COMPOSITE: Final = "composite"
METRICS: Final = ("rank_ic", "ew_minus_worst_decile", "survivors_minus_ew", "top_minus_bottom")


class ScoreSource(Protocol):
    def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]: ...

    # 键：COMPOSITE 与合成分的各因子 id；值：合格范围内 (ts_code, 分数)，按代码排序


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelScoreSource:  # 真实的：build_facts_day + screen
    store: PanelStore
    book: SurvivorBookConfig
    transform: FactorTransformSpec
    exchange: str
    as_of: datetime
    min_coverage: float
    max_carry_sessions: int

    def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]: ...


def panel_label_source(
    store: PanelStore, *, years: Sequence[int], exchange: str, as_of: datetime, cached_sessions: int
) -> OutcomeLabels: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagnosticRequest:
    plan: SignalPlan
    scores: tuple[str, ...]
    tails: tuple[int, ...]
    loser_decile_count: int
    power_means: tuple[float, ...]
    power_periods: tuple[int, ...]
    power_target: float
    power_test_level: float


@dataclass(frozen=True, slots=True, kw_only=True)
class DayReading:
    session: date
    score: str
    horizon: int
    readout: CrossSectionReadout | None
    unlabelled_by_code: tuple[tuple[str, int], ...]
    refusal: str | None  # 当日被数据拒绝时的具名原因（缺口）；窗口违规从不在这里


def run_diagnostics(
    request: DiagnosticRequest, *, scores: ScoreSource, labels_for: Callable[[int], LabelSource]
) -> tuple[DayReading, ...]: ...
def summarize(request: DiagnosticRequest, readings: Sequence[DayReading]) -> dict[str, Any]: ...
def diagnostic_document(
    request: DiagnosticRequest,
    readings: Sequence[DayReading],
    *,
    config: Mapping[str, Any],
    code_commit: str,
    partitions_read: Sequence[tuple[str, int, str | None]],
    binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]: ...
def diagnostic_lines(document: Mapping[str, Any]) -> list[str]: ...
def main(argv: Sequence[str] | None = None) -> int: ...  # 子命令 explore、power
```

引擎规则：对计划里的每个信号日：`scores.scores_on(day)`；把当日的数据拒绝（`strategy_survivor_registration.SURVIVOR_RECOMPUTE_REFUSALS` 加 `model_view.ModelRunBlockedError`、`ModelPanelUnreadableError`）记成具名缺口、继续；**`DiagnosticWindowError` 永远不是缺口，原样抛出**。对每个期限：`labels.window(session_publication_instant(day), horizon=ResearchHorizon(count=h, unit=HorizonUnit.trading_days))`，出场晚于 `plan.window.last` 就在**问任何一个标签之前**抛 `DiagnosticWindowError`（纵深防御；正常情况下嵌入期已经排除）；对范围里每个名字问标签：`None` → 计 `no_adjustment_history`，有拒绝码 → 计各码，否则取 `realized_return`；`read_cross_section(...)`。`labels_for(year)` 每个信号年建一个读取器，年份 `y-1..min(y+1, ceiling)`（`strategy_view._measure_year` 的形状），`cached_sessions = max(horizons) + 2`。

汇总规则：每个 (分数, 期限) 的非重叠样本 = 计划信号日里每隔 `m = ceil(h / stride)` 个取一个（从第一个起；缺口不移动样本）；四个指标各一个 `SeriesSummary`；十分位超额与尾部超额各一个；`unlabelled_by_code` 合计；缺口列表；秩 IC 的 `power_if_planning_sd_were_this`：当 `sd` 存在时，对 `normal` 与 `t` 两种口径、`power_periods` 里每个 K、`power_means` 里每个均值给功效与所需期数，带 `EXPLORATORY_POWER_WORDING`。

文档：`kind`（`binding is None` → 探索，否则读一次）、`wording`（探索：`[EXPLORATORY_WORDING, IN_SAMPLE_WORDING, CANDIDATE_WORDING]`；读一次：`[READ_ONCE_WORDING, CANDIDATE_WORDING]`，二次使用段再加 `SECOND_USE_WORDING`）、`window`、`plan`（含 `embargoed`）、`config`、`provenance`（`code_commit`、`config_digest = sha256(grid.canonical_json({"config": config, "request": ...}))`、`random_component: "none"`、`partitions_read`）、`binding`、`days`（每日读出，供审计）、`summary`。`summary[score][f"h{h}"][metric]` 是 `{"n","mean","sd","se","t","share_positive","lag1_autocorrelation","by_year","absence"}`——Task 5 的停止规则读这里。`json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"`。文本第一行是横幅：探索 `EXPLORATORY, UNREGISTERED -- NOT EVIDENCE: <name> window <first>..<last>`；读一次 `PRE-REGISTERED READ-ONCE <reading> -- A FIRST OUT-OF-SAMPLE READING, NOT CONFIRMATORY EVIDENCE: <name> window <first>..<last>`。

CLI：`explore --runtime-dir DIR --as-of ISO --first D --last D --stride N --horizons 20[,5] --transform KEY [--book JSON] [--scores composite,...] [--min-coverage 0.5] [--max-carry-sessions 250] --out DIR`：窗口固定为 `WINDOWS["research"]`，**没有其他窗口选项**；日期越界在打开任何存储之前拒绝（退出码 2）；输出 `p7-diagnostic-research-<config_digest[:16]>.json` 与 `.txt`，同字节则不写、不同则拒绝。`power --periods K --sd S --test-level L --means a,b,c --target-power P --critical-value normal|t [--interim k --futility-threshold t] [--family-size m --q q]`：只算，不读数据，打印 `power_table_lines` 及可选的停止概率 / 带停止的功效 / BY 第一步水平。`--book` 缺省为 `SurvivorBookConfig(seed=0)`（种子不进入筛选，文档写明）。

- [ ] **Step 1：写失败的测试** `tests/unit/test_p7_diagnostics.py`（关键部分；剩余测试按下面的清单同样写出）：

```python
"""`scripts/research/p7_diagnostics.py` (`V2-P7-004`): the diagnostics engine over a score source
and a label source, its document and its lines, held to hand-computed numbers on generated
inputs; and the two real sources wired over panels generated at test time (the survivor facts
frame for the scores, the forward tests' label panel for the labels). No research store is read."""

from __future__ import annotations

import dataclasses
import importlib
import json
import math
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import AS_OF, EXCHANGE
from test_strategy_survivor_forward import (
    BOOK as FORWARD_BOOK,
    CALENDAR,
    PANEL_SCORES,
    SESSIONS,
    panel_store,  # noqa: F401  (module fixture)
    reader_of,
    witnessed_on,
)
from test_survivor_facts_panel import (
    CONFIG as FRAME_BOOK,
    IDENTITY,
    SIGNAL,
    SIGNAL_INDEX,
    evening,
    full_store,  # noqa: F401  (module fixture)
    read_day,
)

from openalpha_cn.backtest.decile_readout import read_cross_section, summarize_series
from openalpha_cn.domain.horizon import ResearchHorizon
from openalpha_cn.domain.labels import LabelWindow, OutcomeLabel, WindowReturn, build_label_window
from openalpha_cn.domain.survivor_screen import screen
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.strategy_survivor_facts import SurvivorFactsError
from openalpha_cn.strategy_survivor_forward import record_outcome

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"
SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


p7_windows = _research_module("p7_windows")
p7 = _research_module("p7_diagnostics")
FIXTURE_WINDOW = p7_windows.DiagnosticWindow(
    name="fixture", first=date(2026, 1, 5), last=date(2026, 12, 31), ceiling_year=2026
)
"""The generated panels are 2026's; the engine takes any window, the CLI only the research one."""
CODES: Final[tuple[str, ...]] = tuple(f"6000{n:02d}.SH" for n in range(20))
TAIL: Final[Mapping[int, float]] = {0: -0.03, 1: -0.03, 2: -0.01, 3: -0.01}


class TableLabels:
    """A label source on a calendar (the generated 2026 one unless told) answering from
    `answer(entry_day, code)`, counting every question."""

    def __init__(
        self, answer: Callable[[date, str], float | None], *, calendar: Any = CALENDAR
    ) -> None:
        self.calendar = calendar
        self.answer = answer
        self.asked: list[tuple[str, date]] = []

    def window(self, instant: datetime, *, horizon: ResearchHorizon) -> LabelWindow:
        return build_label_window(
            as_of=instant, zone=SHANGHAI, horizon=horizon, calendar=self.calendar
        )

    def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel | None:
        self.asked.append((ts_code, window.entry_day))
        value = self.answer(window.entry_day, ts_code)
        if value is None:
            return None
        return OutcomeLabel(
            ts_code=ts_code,
            window=window,
            refusals=(),
            entry_touch=None,
            exit_touch=None,
            window_return=WindowReturn(
                ts_code=ts_code,
                entry_day=window.entry_day,
                exit_day=window.exit_day,
                published=value,
                adjusted=value,
                unadjusted=value,
                entry_adjusted_close=1.0,
                exit_adjusted_close=1.0 + value,
                per_session=(),
            ),
        )


class FixedScores:
    def __init__(
        self, scored: Mapping[str, float], *, refuse_on: frozenset[date] = frozenset()
    ) -> None:
        self.scored = tuple(sorted(scored.items()))
        self.refuse_on = refuse_on
        self.asked: list[date] = []

    def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]:
        self.asked.append(session)
        if session in self.refuse_on:
            raise SurvivorFactsError(f"no 16:30 build on {session.isoformat()}")
        return {p7.COMPOSITE: self.scored}


def request(
    *,
    first: date = SESSIONS[0],
    last: date = date(2026, 6, 30),
    stride: int = 20,
    horizons: tuple[int, ...] = (20,),
) -> Any:
    plan = p7_windows.plan_signal_days(
        CALENDAR, window=FIXTURE_WINDOW, first=first, last=last, stride=stride, horizons=horizons
    )
    return p7.DiagnosticRequest(
        plan=plan,
        scores=(p7.COMPOSITE,),
        tails=(1, 2, 3),
        loser_decile_count=2,
        power_means=(0.037, 0.05, 0.1),
        power_periods=(12, 36, 56),
        power_target=0.8,
        power_test_level=0.05,
    )


def tail_answer(entry: date, code: str) -> float:
    """The loser tail of `test_decile_readout`, doubled on every second signal's entry."""
    position = CODES.index(code)
    scale = 2.0 if CALENDAR.trading_days.index(entry) % 40 == 21 else 1.0
    return TAIL.get(position, 0.01) * scale


def test_the_engine_reads_each_signal_day_as_the_readout_does() -> None:
    read = request(last=date(2026, 3, 31))
    labels = TableLabels(tail_answer)
    readings = p7.run_diagnostics(
        read,
        scores=FixedScores({code: float(p) for p, code in enumerate(CODES)}),  # type: ignore[arg-type]
        labels_for=lambda year: labels,
    )

    assert [item.session for item in readings] == list(read.plan.sessions)  # type: ignore[attr-defined]
    first = readings[0].readout
    assert first is not None
    assert first.rank_ic == pytest.approx(math.sqrt(324 / 665), abs=1e-12)
    assert first.ew_minus_worst_decile == pytest.approx(0.034, abs=1e-15)
    assert readings[1].readout is not None
    assert readings[1].readout.ew_minus_worst_decile == pytest.approx(0.068, abs=1e-15)


def test_the_summary_is_the_series_summary_of_the_non_overlapping_readings() -> None:
    read = request(stride=5, horizons=(5, 20), last=date(2026, 4, 30))
    readings = p7.run_diagnostics(
        read,
        scores=FixedScores({code: float(p) for p, code in enumerate(CODES)}),  # type: ignore[arg-type]
        labels_for=lambda year: TableLabels(tail_answer),
    )
    summary = p7.summarize(read, readings)

    sessions = read.plan.sessions  # type: ignore[attr-defined]
    assert summary[p7.COMPOSITE]["h5"]["sampled"] == [day.isoformat() for day in sessions]
    assert summary[p7.COMPOSITE]["h20"]["sampled"] == [day.isoformat() for day in sessions[::4]]
    h20 = [item for item in readings if item.horizon == 20 and item.session in sessions[::4]]
    expected = summarize_series(
        [(item.session, item.readout.ew_minus_worst_decile) for item in h20]
    )  # type: ignore[union-attr]
    assert summary[p7.COMPOSITE]["h20"]["ew_minus_worst_decile"]["mean"] == expected.mean
    assert summary[p7.COMPOSITE]["h20"]["ew_minus_worst_decile"]["n"] == expected.n


def test_a_cyclic_no_signal_input_shows_no_structure() -> None:
    """Ten names over ten consecutive sessions at h=1: mean IC 0, every decile's mean excess 0."""
    codes = CODES[:10]
    read = request(stride=1, horizons=(1,), last=SESSIONS[9])

    def cyclic(entry: date, code: str) -> float:
        t = CALENDAR.trading_days.index(entry) - 1
        return ((codes.index(code) + t) % 10) / 100

    summary = p7.summarize(
        read,
        p7.run_diagnostics(
            read,
            scores=FixedScores({code: float(r) for r, code in enumerate(codes)}),  # type: ignore[arg-type]
            labels_for=lambda year: TableLabels(cyclic),
        ),
    )

    block = summary[p7.COMPOSITE]["h1"]
    assert block["rank_ic"]["n"] == 10
    assert block["rank_ic"]["mean"] == pytest.approx(0.0, abs=1e-12)
    assert all(row["mean"] == pytest.approx(0.0, abs=1e-15) for row in block["decile_excess"])


def test_a_refused_day_is_a_named_gap_that_never_moves_the_sample() -> None:
    read = request(last=date(2026, 6, 30))
    sessions = read.plan.sessions  # type: ignore[attr-defined]
    readings = p7.run_diagnostics(
        read,
        scores=FixedScores(
            {code: float(p) for p, code in enumerate(CODES)}, refuse_on=frozenset({sessions[1]})
        ),  # type: ignore[arg-type]
        labels_for=lambda year: TableLabels(tail_answer),
    )
    summary = p7.summarize(read, readings)

    assert summary[p7.COMPOSITE]["h20"]["sampled"] == [day.isoformat() for day in sessions]
    assert summary[p7.COMPOSITE]["h20"]["gaps"] == [
        {
            "session": sessions[1].isoformat(),
            "reason": f"SurvivorFactsError: no 16:30 build on {sessions[1].isoformat()}",
        }
    ]
    assert summary[p7.COMPOSITE]["h20"]["rank_ic"]["n"] == len(sessions) - 1


def test_a_window_refusal_is_never_turned_into_a_gap() -> None:
    class Leaking(FixedScores):
        def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]:
            raise p7_windows.DiagnosticWindowError("daily [2022] is past the research window")

    with pytest.raises(p7_windows.DiagnosticWindowError):
        p7.run_diagnostics(
            request(), scores=Leaking({}), labels_for=lambda year: TableLabels(tail_answer)
        )  # type: ignore[arg-type]


def test_a_label_window_past_the_window_is_refused_before_any_label_is_asked() -> None:
    """A plan built by hand, skipping `plan_signal_days`' embargo (a caller's error): the 20-session
    label of 2026-02-16 exits in March, past a window ending 2026-02-27. The engine refuses it
    before it asks a single label."""
    short = p7_windows.DiagnosticWindow(
        name="fixture", first=date(2026, 1, 5), last=date(2026, 2, 27), ceiling_year=2026
    )
    forged = p7_windows.SignalPlan(
        window=short,
        first=date(2026, 2, 16),
        last=date(2026, 2, 16),
        stride=1,
        horizons=(20,),
        sessions=(date(2026, 2, 16),),
        embargoed=(),
    )
    labels = TableLabels(tail_answer)

    with pytest.raises(p7_windows.DiagnosticWindowError, match="2026-02-27"):
        p7.run_diagnostics(
            dataclasses.replace(request(), plan=forged),
            scores=FixedScores({code: float(p) for p, code in enumerate(CODES)}),
            labels_for=lambda year: labels,
        )
    assert labels.asked == []
```

其余必须写出的测试（每条一个函数，期望值写在注释里）：

1. `test_the_panel_score_source_is_the_screens_eligible_composites(full_store)`：`p7.PanelScoreSource(store=p7_windows.WindowCappedStore(full_store.root, window=FIXTURE_WINDOW), book=FRAME_BOOK, transform=IDENTITY, exchange=EXCHANGE, as_of=evening(SIGNAL_INDEX), min_coverage=0.5, max_carry_sessions=250).scores_on(SIGNAL)[p7.COMPOSITE]` 等于 `tuple(sorted((v.ts_code, v.composite) for v in screen(read_day(full_store).facts, FRAME_BOOK).verdicts if v.eligible))`；每个成分因子的分数等于对应 `oriented_values[i]`。
2. `test_the_panel_label_source_is_the_forward_reports_reader(panel_store)`：用 `p7.panel_label_source(WindowCappedStore(panel_store.root, window=FIXTURE_WINDOW), years=(2026,), exchange=EXCHANGE, as_of=AS_OF, cached_sessions=4)` 在 `SESSIONS[1]`、h=2 上跑引擎（分数 `PANEL_SCORES`），与 `record_outcome(reader_of(panel_store), witnessed_on(SESSIONS[1], PANEL_SCORES), horizon=2, book=FORWARD_BOOK, csi500=(), as_of=AS_OF)` 的 `rank_ic`、`investable_ew`、`survivors_minus_ew`、`ew_minus_worst_decile`、`top_minus_bottom` **完全相等**（`==`），`unlabelled_by_code` 相等。若最后一位不同，改实现的迭代顺序，**不得放宽断言**。
3. `test_the_label_reader_cannot_be_built_past_the_ceiling(panel_store)`：上限 2025 的 `WindowCappedStore` 上 `panel_label_source(..., years=(2026,), ...)` 抛 `DiagnosticWindowError`。
4a. `test_a_binding_makes_the_read_once_kind_and_wording()`：同一组输入、`binding={"reading": "probe", "registration_sha256": "0"*64, "registration_commit": "0"*40, "claim_sha256": "1"*64}` 的文档 `kind == READ_ONCE_KIND`、`wording == [READ_ONCE_WORDING, CANDIDATE_WORDING]`、`binding` 原样写入、第一行以 `PRE-REGISTERED READ-ONCE probe` 开头；窗口名为 `second_use` 时 `wording` 多出 `SECOND_USE_WORDING`。
4. `test_every_exploratory_document_and_line_carries_the_exploratory_wording()`：`binding=None` 的文档 `kind == EXPLORATORY_KIND`、`wording == [EXPLORATORY_WORDING, IN_SAMPLE_WORDING, CANDIDATE_WORDING]`、`provenance.random_component == "none"`、`diagnostic_lines(document)[0].startswith("EXPLORATORY, UNREGISTERED -- NOT EVIDENCE")`、JSON 与文本里没有 `significant|effective|confirmed|alpha|outperform|passed|failed|proven`、`power_if_planning_sd_were_this` 带 `EXPLORATORY_POWER_WORDING`。
5. `test_two_runs_write_the_same_bytes_under_two_hash_seeds()`：模块级 `_document_bytes() -> bytes`（固定输入、`code_commit="0"*40`、`partitions_read=()`）；同进程两次相等；`subprocess.run([sys.executable, "-c", "import sys; sys.path[:0]=[...]; import test_p7_diagnostics as t; sys.stdout.buffer.write(t._document_bytes())"], env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT / "src")}, check=True, capture_output=True)` 在 `seed="0"` 与 `"7"` 下的字节与同进程相等（`sys.path` 前插 `tests/unit`、`tests`）。
6. `test_the_config_digest_moves_with_any_request_or_config_field()`：改 `stride`、`horizons`、`as_of`、`transform` 任一项，`config_digest` 都变。
7. `test_explore_refuses_a_date_past_the_research_window_before_opening_anything(monkeypatch, tmp_path)`：把 `p7._open_sources` 换成一旦被调用就失败的函数；`p7.main(["explore", "--runtime-dir", str(tmp_path), "--as-of", "2026-10-09T00:00:00+08:00", "--first", "2021-11-01", "--last", "2022-01-04", "--stride", "20", "--horizons", "20", "--transform", "cross_section_standard/v1", "--out", str(tmp_path / "out")])` 返回 2，stderr 含 `validation` 与 `read_once`，`out` 目录不存在。
8. `test_explore_writes_once_and_a_rerun_is_a_no_op(monkeypatch, tmp_path)`：`p7._open_sources` 换成返回（2016-12-01..2017-12-29 的周日历——用 `build_trading_calendar("SSE", [CalendarDay(...)])` 构造、`FixedScores`、`lambda year: TableLabels(lambda entry, code: TAIL.get(CODES.index(code), 0.01), calendar=that_calendar)`、`partitions_read=()`），参数 `--first 2017-01-03 --last 2017-06-30 --stride 20 --horizons 20`；第一次写出 `.json`/`.txt`；第二次返回 0 且字节不变；手改文件后再跑返回非 0 并说明"already holds a different document"。
9. `test_the_power_subcommand_prints_the_calculators_lines(capsys)`：`p7.main(["power", "--periods", "36", "--sd", "0.15", "--test-level", "0.05", "--means", "0.037,0.05,0.1", "--target-power", "0.8", "--critical-value", "normal"])` 的输出等于 `power_table_lines(...)` 的行；加 `--interim 18 --futility-threshold 0` 时，每个均值多两行，0.05 的两行逐字为 `  interim look after 18 periods, stopping at a mean at or below 0: stop probability 0.079 at mean 0.05` 与 `  power with the interim look: 0.631 at mean 0.05`（0.037、0.1 同形，数值取自 `futility_stop_probability` / `power_with_futility`）；加 `--family-size 3 --q 0.1` 时多一行 `  a secondary under Benjamini-Yekutieli (family 3, q 0.1) is powered at the first-step level 0.0182, a lower bound`。
10. `test_only_the_cli_names_the_research_window()`：`p7.main(["explore", "--help"])` 的帮助里没有 `--window` 选项（`SystemExit(0)`，捕获 stdout 检查）。

- [ ] **Step 2：运行，确认失败。** `PYTHONPATH="$PWD/src" uv run pytest tests/unit/test_p7_diagnostics.py -q`。预期：`ModuleNotFoundError: No module named 'p7_diagnostics'`。

- [ ] **Step 3：实现。** `_open_sources(runtime_dir, *, window, config) -> tuple[TradingCalendar, ScoreSource, Callable[[int], LabelSource], WindowCappedStore]` 是 CLI 唯一打开存储的接缝（测试替换它）：`store = capped_panel_store(runtime_dir, window)`；日历 `load_trading_calendar(store, exchange=..., years=range(first.year - 1, ceiling + 1), as_of=...)`；`PanelScoreSource(...)`；`labels_for = lambda year: panel_label_source(store, years=tuple(range(year - 1, min(year + 1, window.ceiling_year) + 1)), ...)`。分数源从 `screen(...).verdicts` 取合格者（`verdict.eligible`），成分因子分数取 `facts` 里同一代码的 `oriented_values[i]`（合格意味着每个值都在）。`main` 用 `argparse` 子命令；`explore` 先校验日期（`plan_signal_days` 前先比 `WINDOWS["research"]` 的首尾，越界退出码 2），再 `_open_sources`；写文件用"同字节则不写、不同则拒绝"（`registry.register` 的写一次规则，`registry.py:199-206`）。

- [ ] **Step 4：运行，确认通过**；`ruff`、`mypy src scripts`。

- [ ] **Step 5：变异探针：** (a) 引擎把 `DiagnosticWindowError` 也当成缺口；(b) 非重叠子样本的 `m` 用 `h // stride`（h=20、stride=5 仍是 4——再加一个 stride=3 的断言让它变红：`ceil(20/3)=7` 对 `20//3=6`）；(c) 出场检查放到问标签之后（`labels.asked` 非空变红）；(d) 文档 `binding is None` 时写读一次的 kind；(e) `labels_for` 的年份上界不取 `min(..., ceiling)`（上限测试变红）。

- [ ] **Step 6：** 跑 `tests/unit`，提交：`feat(V2-P7-004): read-only P7 diagnostics -- deciles, tails, rank IC and its dispersion by score and horizon, exploratory by construction`。

---

## Task 5：`scripts/research/p7_reading.py` — 具名、记日志、只能用一次的读数路径

**Issue:** `V2-P7-004`（供 `V2-P7-024` 使用；`024` 的读数本身不在本计划里）。**依赖:** Task 3、4。**依据:** G-0（规格 §5.2）；路线调整 §2.1；`registry.run_holdout`（`registry.py:420-475`：先写认领再测量，崩溃后认领就是那一次）、`_committed_registration`（`:224-270`）、`_refuse_a_changed_source`（`:273-295`）、`_refuse_a_foreign_package`（`:303-312`）、`_refuse_foreign_scripts`（`:320-329`）、`REGISTERED_PATHS`（`:93`）、`registry.register` 的写一次规则（`:199-206`）；`registry` 文档串里写明的局限（提交时间是自报的、账本不在 git 里，`:49-54`）。

**Files:** Create `scripts/research/p7_reading.py`、`tests/unit/scripts/test_p7_reading.py`。（**corrected 2026-11-02: 另有 `tests/unit/test_p7_reading_rehearsal.py`**——在生成的两年面板、夹具窗口上演练默认的测量与预检。）

（**corrected 2026-11-02: 下面这一任务经过四轮修复，出货的行为与本节的草稿在五处不同；以 `scripts/research/p7_reading.py` 的模块文档串为准。**①**整个窗口**：一个读数必须从窗口的第一个会话读到最后一个会话（`first`、`last` 就是窗口的第一天与最后一天），因为部分读数也会用掉整个窗口，而第一天若不是会话就不能登记；一个永远达不到的 `minimum_periods`（超过数据无关的上界 `_most_periods`）在登记时拒绝。②**窗口级认领**：原草稿"一份认领一个读数名"，换一个读数名就能再读一次；出货的版本在读数自己的认领之前先以 `O_EXCL` 写 `<window>.window-claim.json`，并且 `READING_DIRECTORY` 里任何一个点名该窗口的 `*.claim.json` / `*.window-claim.json`，在磁盘上或曾在任何 ref（`--all --full-history`、合并提交的每个父提交、`--reflog`、stash）里提交过，就拒绝；读不通的认领文件、点名不在 `WINDOWS` 里的窗口一律拒绝；**认领必须提交并留在本仓库的 refs 里**，浅克隆（`git rev-parse --is-shallow-repository` 必须恰好答 `false`）与 grafts 文件拒绝，替换 ref 被忽略。认领与结果的路径跟着 `HEAD` 所持的登记，符号链接找到同样的认领。③`READ_ONCE_POWER_WORDING`：读一次的文档的功效块不再说"样本内"（那个标准差是这一次读数自己的值），由 `p7_diagnostics` 按文档种类选措辞，探索性输出的字节不变。④**按数据集的年前预检**：预检对窗口各年的所有数据集，只对读年前一年的数据集（`YEAR_BEFORE_DATASETS`：日历、日线、复权因子、停牌、名称、登记簿）再要年前一年；`stk_limit` 与因子数据集只为信号日读，只要窗口各年。⑤`loser_decile_count` 必须等于书里的，读之前认领之前核对。运行前提见 `docs/superpowers/plans/2026-11-02-p7-dev-batch-1-closeout.md` §6。）

**Interfaces（产出）:**

```python
READING_SCHEMA: Final = "openalpha-p7-reading-registration/v1"
STOP_RULE_SCHEMA: Final = "p7-stop-rule/v1"
READING_DIRECTORY: Final = Path("docs/research/p7-readings")
Role = Literal["primary", "secondary"]


class ReadingError(ValueError): ...  # 写不出这份登记


class ReadingConfigurationError(registry.HoldoutRefusedError): ...  # 登记的内容不能读


class ReadingAlreadyClaimedError(
    registry.HoldoutRefusedError
): ...  # 已认领 / 已有结果 / 认领曾被提交


class ReadingPreflightError(registry.HoldoutRefusedError): ...  # 认领前的目录检查不通过


class ForeignP7ScriptsError(registry.HoldoutRefusedError): ...


@dataclass(frozen=True, slots=True, kw_only=True)
class StopClause:
    metric: Literal["rank_ic", "ew_minus_worst_decile", "survivors_minus_ew", "top_minus_bottom"]
    mean_at_or_below: float


@dataclass(frozen=True, slots=True, kw_only=True)
class StopRule:
    score: str
    horizon: int
    minimum_periods: int
    any_of: tuple[StopClause, ...]


STOP_TRIGGERED_WORDING: Final[str]  # 见下面的规则
NOT_TRIGGERED_WORDING: Final[str]


def register_reading(
    *,
    path: Path,
    reading: str,
    issue: str,
    role: Role,
    window: str,
    request: Mapping[str, Any],
    config: Mapping[str, Any],
    stop_rule: StopRule | None,
    code_commit: str,
) -> str: ...  # 返回 sha256
def evaluate_stop_rule(rule: StopRule, document: Mapping[str, Any]) -> dict[str, Any]: ...
# -> {"outcome": "triggered" | "not triggered", "clauses": [{"metric", "n", "mean",
#     "mean_at_or_below", "met"}], "reason": str | None, "wording": str}
# read_once 的返回值 = 测量的文档 + {"stop_rule": evaluate_stop_rule(...)}（只在 primary）
def preflight_problems(
    store: PanelStore, *, window: DiagnosticWindow, datasets: Sequence[str]
) -> tuple[str, ...]: ...  # 只问目录：registered_years


Measure = Callable[[Mapping[str, Any], DiagnosticWindow, Mapping[str, Any]], dict[str, Any]]


def read_once(
    registration: Path,
    *,
    repo: Path,
    runtime_dir: Path,
    clock: Callable[[], datetime] | None = None,
    measure: Measure | None = None,
    preflight: Callable[[Mapping[str, Any]], Sequence[str]] | None = None,
) -> dict[str, Any]: ...
```

规则：
- 登记写一次（同字节返回同 sha，不同则拒绝），文件名必须是 `<reading>.registration.json`，`reading` 匹配 `[a-z0-9][a-z0-9-]*`；`window` 只能是 `validation` 或 `second_use`（`research` → `ReadingError("research ... explore")`）；`primary` 必须带停止规则，`secondary` 必须不带；停止规则的 `horizon` 与 `score` 必须在 `request` 里；`code_commit` 必须是 40 位十六进制（`-dirty`、`unknown` 拒绝）。**停止规则与 `minimum_periods` 没有缺省值**（数值由用户定，见 U8）。
- `read_once` 的顺序：(1) 已提交且与 `HEAD` 字节一致（照 `registry._committed_registration` 的做法，但 schema 是本模块的；用 `registry._git`）；(2) `registry._refuse_a_changed_source(root, body["code_commit"], purpose="a read-once reading measures with the registered code only")`；(3) `registry._refuse_a_foreign_package(root)`、`registry._refuse_foreign_scripts(root)`、本模块 `_refuse_foreign_p7_scripts(root)`（`p7_windows`、`p7_diagnostics`、`p7_reading` 必须从 `root/scripts/research` 导入）；(4) 认领文件、结果文件已存在，或 `git log --all --format=%H -- <认领文件的相对路径>` 非空 → `ReadingAlreadyClaimedError`（"a claim was committed at <sha>; deleting the file does not give the read back"）；(5) 预检（默认：`preflight_problems(PanelStore(runtime_dir/"panel")...)` 只看 `registered_years`，检查窗口内每一年在管线要读的数据集里都有分区；有问题 → `ReadingPreflightError`，**不写认领**）；(6) 以 `O_CREAT | O_EXCL` 写认领 `{"reading","registration_sha256","registration_commit","window","claimed_at"}`；(7) 测量（默认：在 `capped_panel_store(runtime_dir, window)` 上跑 Task 4 的引擎，`binding = {"reading","registration_sha256","registration_commit","claim_sha256"}`）；测量抛出数据拒绝（同 Task 4 的拒绝集合）→ 结果写 `{"error": ...}`，这一次读数就用掉了；其他异常原样抛出、认领留下；(8) `primary` 求停止规则；(9) 以 `O_EXCL` 写 `<reading>.result.json` 与 `.result.txt`。
- 停止规则：`summary[score][f"h{horizon}"][metric]` 的 `n < minimum_periods` → `{"outcome": "triggered", "reason": "unreadable: ..."}`（**读不出来的闸门按关闭处理**，U8）；否则任一条款 `mean <= mean_at_or_below` → `triggered`（列出条款），都不满足 → `not triggered`。措辞：`STOP_TRIGGERED_WORDING = "the stop rule written before the read is triggered: under G-0 the forward clock is not started, and the user is told there is no evidence"`；`NOT_TRIGGERED_WORDING = "the stop rule written before the read is not triggered: this permits V2-P7-006 to proceed and is not evidence"`。
- 模块文档串照 `registry` 写明局限：提交时间是提交者时钟自报的；认领文件在用户提交之前不在 git 里（删掉它不会骗过第 (4) 步的 `git log --all` 检查，只要它曾被提交过）；推送到别人持有的远端才让时间成为见证，本模块不推送。

- [ ] **Step 1：写失败的测试** `tests/unit/scripts/test_p7_reading.py`：

```python
"""`scripts/research/p7_reading.py` (`V2-P7-004`): the one way into the validation window and the
spent holdout -- a committed registration, a claim written before the read, one read. Every git
repository is a throwaway under `tmp_path` (`research_repo`); the measure is a stand-in, so no
panel is read here."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from research_repo import commit_file, git, head

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


registry = _research_module("registry")
p7_reading = _research_module("p7_reading")
COMMITTED: Final[datetime] = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
BOUND: Final[tuple[str, ...]] = (
    "src/openalpha_cn/strategy.py",
    "scripts/research/grid.py",
    "pyproject.toml",
    "uv.lock",
)
REQUEST: Final[dict[str, Any]] = {
    "first": "2022-01-04",
    "last": "2023-12-29",
    "stride": 20,
    "horizons": [20],
    "scores": ["composite"],
    "tails": [1, 2, 3],
    "loser_decile_count": 2,
}
CONFIG: Final[dict[str, Any]] = {
    "book": {"seed": 0},
    "transform": "cross_section_standard/v1",
    "exchange": "SSE",
    "min_coverage": 0.5,
    "max_carry_sessions": 250,
    "as_of": "2026-10-09T00:00:00+08:00",
}
RULE = p7_reading.StopRule(
    score="composite",
    horizon=20,
    minimum_periods=3,
    any_of=(
        p7_reading.StopClause(metric="rank_ic", mean_at_or_below=0.02),
        p7_reading.StopClause(metric="ew_minus_worst_decile", mean_at_or_below=0.0),
    ),
)
"""Test values only: the stop rule's numbers are the user's (V2-P7-024)."""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    for name in BOUND:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("RULES = 1\n", encoding="utf-8")
        git(repo, "add", name)
    git(repo, "commit", "-q", "-m", "initial", at=COMMITTED - timedelta(days=1))
    research = repo / "scripts" / "research"
    monkeypatch.setattr(
        registry, "_imported_package", lambda: repo / "src" / "openalpha_cn" / "__init__.py"
    )
    monkeypatch.setattr(
        registry, "_imported_scripts", lambda: (research / "grid.py", research / "registry.py")
    )
    monkeypatch.setattr(
        p7_reading,
        "_imported_p7_scripts",
        lambda: tuple(
            research / f"{name}.py" for name in ("p7_windows", "p7_diagnostics", "p7_reading")
        ),
    )
    return repo


def registered(
    repo: Path,
    *,
    commit: bool = True,
    role: str = "primary",
    rule: Any = RULE,
    window: str = "validation",
    reading: str = "v2-p7-024-validation",
) -> Path:
    path = repo / "docs" / "research" / "p7-readings" / f"{reading}.registration.json"
    p7_reading.register_reading(
        path=path,
        reading=reading,
        issue="V2-P7-024",
        role=role,
        window=window,  # type: ignore[arg-type]
        request=REQUEST,
        config=CONFIG,
        stop_rule=rule,
        code_commit=head(repo),
    )
    if commit:
        commit_file(repo, path, "register the reading", at=COMMITTED)
    return path


def document(*, n: int = 24, ic: float = 0.05, spread: float = 0.01) -> dict[str, Any]:
    return {
        "kind": "p7-diagnostic-read-once/v1",
        "summary": {
            "composite": {
                "h20": {
                    "rank_ic": {"n": n, "mean": ic},
                    "ew_minus_worst_decile": {"n": n, "mean": spread},
                }
            }
        },
    }


class Measure:
    """A stand-in measure: records the call and whether the claim already existed."""

    def __init__(self, result: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.result = document() if result is None else result
        self.fail = fail
        self.calls: list[tuple[str, bool]] = []
        self.claim: Path | None = None

    def __call__(
        self, body: Mapping[str, Any], window: Any, binding: Mapping[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((window.name, self.claim is not None and self.claim.exists()))
        if self.fail:
            raise RuntimeError("the process died mid-read")
        return {**self.result, "binding": dict(binding)}


def run(registration: Path, repo: Path, measure: Measure, *, hours: int = 1) -> dict[str, Any]:
    measure.claim = registration.with_name(
        registration.name.replace(".registration.json", ".claim.json")
    )
    return p7_reading.read_once(
        registration,
        repo=repo,
        runtime_dir=repo / "runtime",
        clock=lambda: COMMITTED + timedelta(hours=hours),
        measure=measure,
        preflight=lambda body: (),
    )


def test_a_registration_is_written_once_and_names_its_code_commit(repo: Path) -> None:
    path = registered(repo, commit=False)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["schema"] == p7_reading.READING_SCHEMA
    assert (body["window"], body["role"], body["code_commit"]) == (
        "validation",
        "primary",
        head(repo),
    )
    with pytest.raises(p7_reading.ReadingError, match="already"):
        p7_reading.register_reading(
            path=path,
            reading="v2-p7-024-validation",
            issue="V2-P7-024",
            role="primary",
            window="validation",
            request={**REQUEST, "stride": 10},
            config=CONFIG,
            stop_rule=RULE,
            code_commit=head(repo),
        )


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"window": "research"}, "explore"),
        ({"role": "secondary"}, "no stop rule"),
        ({"rule": None}, "stop rule"),
    ],
)
def test_a_registration_that_could_not_be_read_once_is_refused(
    repo: Path, changes: dict[str, Any], said: str
) -> None:
    with pytest.raises(p7_reading.ReadingError, match=said):
        registered(repo, commit=False, **changes)


def test_the_claim_is_written_before_the_read_and_the_result_after(repo: Path) -> None:
    registration = registered(repo)
    measure = Measure()

    result = run(registration, repo, measure)

    assert measure.calls == [("validation", True)]
    claim = json.loads(measure.claim.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    assert claim["registration_sha256"] == hashlib.sha256(registration.read_bytes()).hexdigest()
    assert (
        result["binding"]["claim_sha256"] == hashlib.sha256(measure.claim.read_bytes()).hexdigest()
    )  # type: ignore[union-attr]
    assert result["stop_rule"]["outcome"] == "not triggered"
    assert registration.with_name("v2-p7-024-validation.result.json").exists()


def test_a_read_that_crashed_after_its_claim_is_the_one_read(repo: Path) -> None:
    registration = registered(repo)
    with pytest.raises(RuntimeError, match="died"):
        run(registration, repo, Measure(fail=True))

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="one read"):
        run(registration, repo, Measure(), hours=2)


def test_a_claim_once_committed_cannot_be_deleted_back_into_a_read(repo: Path) -> None:
    registration = registered(repo)
    measure = Measure(fail=True)
    with pytest.raises(RuntimeError):
        run(registration, repo, measure)
    commit_file(repo, measure.claim, "the claim", at=COMMITTED + timedelta(hours=1))  # type: ignore[arg-type]
    git(repo, "rm", "-q", str(measure.claim.relative_to(repo)))  # type: ignore[union-attr]
    git(repo, "commit", "-q", "-m", "drop the claim", at=COMMITTED + timedelta(hours=2))

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="committed"):
        run(registration, repo, Measure(), hours=3)


def test_an_uncommitted_registration_refuses_the_read(repo: Path) -> None:
    with pytest.raises(registry.RegistrationNotCommittedError):
        run(registered(repo, commit=False), repo, Measure())


def test_an_edit_after_the_commit_refuses_the_read(repo: Path) -> None:
    registration = registered(repo)
    registration.write_text(
        registration.read_text(encoding="utf-8").replace("0.02", "0.01"), encoding="utf-8"
    )
    with pytest.raises(registry.RegistrationNotCommittedError, match="bytes"):
        run(registration, repo, Measure())


def test_changed_bound_code_refuses_the_read(repo: Path) -> None:
    registration = registered(repo)
    (repo / BOUND[0]).write_text("RULES = 2\n", encoding="utf-8")
    commit_file(repo, repo / BOUND[0], "change the code", at=COMMITTED + timedelta(minutes=5))
    with pytest.raises(registry.SourceChangedError):
        run(registration, repo, Measure())


def test_a_preflight_problem_refuses_before_any_claim(repo: Path) -> None:
    registration = registered(repo)
    measure = Measure()
    measure.claim = registration.with_name("v2-p7-024-validation.claim.json")
    with pytest.raises(p7_reading.ReadingPreflightError, match="2023"):
        p7_reading.read_once(
            registration,
            repo=repo,
            runtime_dir=repo / "runtime",
            clock=lambda: COMMITTED,
            measure=measure,
            preflight=lambda body: ("daily has no 2023 partition",),
        )
    assert not measure.claim.exists() and measure.calls == []


@pytest.mark.parametrize(
    ("result", "outcome", "said"),
    [
        (document(ic=0.02), "triggered", "rank_ic"),
        (document(spread=0.0), "triggered", "ew_minus_worst_decile"),
        (document(n=2), "triggered", "unreadable"),
        (document(ic=0.021, spread=0.001), "not triggered", "not evidence"),
    ],
)
def test_the_stop_rule_is_applied_as_written(
    result: dict[str, Any], outcome: str, said: str
) -> None:
    evaluated = p7_reading.evaluate_stop_rule(RULE, result)
    assert evaluated["outcome"] == outcome
    assert said in json.dumps(evaluated)


def test_the_result_text_opens_with_the_read_once_banner(repo: Path) -> None:
    registration = registered(repo)
    run(registration, repo, Measure())
    text = registration.with_name("v2-p7-024-validation.result.txt").read_text(encoding="utf-8")
    assert text.startswith("PRE-REGISTERED READ-ONCE v2-p7-024-validation")
    assert p7_reading.NOT_TRIGGERED_WORDING in text


def test_only_a_read_once_document_bound_to_this_claim_is_written(repo: Path) -> None:
    """A measure that hands back an exploratory document (or one bound to another claim) is not
    this read's result: refused by name, and the read is spent -- the claim stays."""
    registration = registered(repo)
    explored = {**document(), "kind": "p7-diagnostic-exploratory/v1"}

    with pytest.raises(p7_reading.ReadingConfigurationError, match="read-once"):
        run(registration, repo, Measure(explored))
    assert registration.with_name("v2-p7-024-validation.claim.json").exists()
    assert not registration.with_name("v2-p7-024-validation.result.json").exists()
```

- [ ] **Step 2：运行，确认失败。** `PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_reading.py -q`。预期：`ModuleNotFoundError: No module named 'p7_reading'`。

- [ ] **Step 3：实现**（上面的规则）。补充三条：(a) 未提交、或磁盘字节与 `HEAD` 不同，抛 `registry.RegistrationNotCommittedError`，消息照 `registry.py:247-258` 的写法（含 "bytes"），不新造异常类；(b) 测量返回后先核对 `result["kind"] == p7_diagnostics.READ_ONCE_KIND` 且 `result["binding"] == binding`，否则 `ReadingConfigurationError("... is not a read-once document bound to this claim ...")`，此时认领已写、结果不写（这一次读数已用掉）；(c) `.result.txt` 由 `reading_lines(result, body)` 写：第一行 `PRE-REGISTERED READ-ONCE <reading> -- A FIRST OUT-OF-SAMPLE READING, NOT CONFIRMATORY EVIDENCE: <window> window <first>..<last>`（取自登记），然后是停止规则的结论与措辞（`STOP_TRIGGERED_WORDING` / `NOT_TRIGGERED_WORDING`），文档带 `plan` 键（真实测量）时再接 `diagnostic_lines(result)[1:]`。

- [ ] **Step 4：运行，确认通过**；`ruff`、`mypy src scripts`；再跑 `tests/unit/scripts/test_research_registry.py`（没改 `registry.py`，应逐位不变）。

- [ ] **Step 5：变异探针：** (a) 去掉 `git log --all` 检查（"committed" 测试变红）；(b) 认领改成在测量之后写（`calls == [("validation", True)]` 变红）；(c) 预检放到认领之后（"before any claim" 变红）；(d) `n < minimum_periods` 改成 `not triggered`；(e) `<=` 改成 `<`（`ic=0.02` 那一条变红）；(f) 去掉 `_refuse_a_changed_source`。

- [ ] **Step 6：** 跑 `tests/unit`，提交：`feat(V2-P7-004): one named, logged, single-use read of the validation window, claimed before it measures`。

---

## Task 6：`V2-P7-018` 的功效输出（受绑定代码，保持最小）

**Issue:** `V2-P7-018`。**依赖:** Task 2。**依据:** 路线图 `:496` 未满足的一项"样本数与功效同口径输出"、`:161` 的"功效输出要写目标功效、相关性、多重检验、最小有效 n、缺口规则"；周报协议块（`strategy_survivor_forward.py:370-458`，`extra="forbid"`、无缺省）；文档组装（`:1551-1620`）、次要族（`:1795-1871`，`family_size = len(secondary_metrics) + len(secondary_horizons)`）、打印行（`:1954-1984`）；脚本 `scripts/forward_report.py:515`（读协议）与 `:582-594`（建文档）。

**设计（推荐，U7 由用户定）：** 不动 `survivor-forward-protocol/v1`（改它要么破坏已写出的冒烟登记、要么升 v2 并改两份测试的协议常量）；新增一个**并列**的设置块 `settings["survivor_forward_power"]`，schema `survivor-forward-power/v1`，`extra="forbid"`，**块内每个字段都没有缺省值**；块缺失时周报照常产出，`power` 节明说"没有预登记的功效计划，功效无从陈述"。功效永远用**规划值**算；观测到的标准差与一阶自相关只在窗口检验之后作为描述打印，**从不进入功效**。

**Files:** Modify `src/openalpha_cn/strategy_survivor_forward.py`、`scripts/forward_report.py`；Modify（追加）`tests/unit/test_strategy_survivor_forward.py`、`tests/unit/scripts/test_forward_report_survivor.py`。

**Interfaces（产出）:**

```python
SURVIVOR_FORWARD_POWER_KEY: Final = "survivor_forward_power"
SURVIVOR_FORWARD_POWER_SCHEMA: Final = "survivor-forward-power/v1"
POWER_NOT_PRE_REGISTERED: Final = (
    "the registration carries no survivor_forward_power block: no power is stated, and a result "
    "above the test level cannot be read against a pre-registered design"
)
OBSERVED_WORDING: Final = "descriptive, over the tested periods; never used for power"


class PlannedMetric(BaseModel):  # extra="forbid", frozen
    horizon: int
    metric: ForwardMetric
    planning_sd: float  # strict, > 0, finite
    planning_means: tuple[float, ...]  # 非空，每个 > 0
    sd_source: str  # 非空：规划值从哪来


class SurvivorForwardPowerPlan(BaseModel):  # extra="forbid", frozen
    schema_version: Literal["survivor-forward-power/v1"]
    test_level: float  # (0, 0.5)
    target_power: float  # (0, 1)
    critical_value: Literal["normal", "t"]
    assumed_lag1_autocorrelation: float  # (-1, 1)
    minimum_periods: int  # >= 2
    planned: tuple[PlannedMetric, ...]  # 非空，(horizon, metric) 不重复


def read_forward_power(
    settings: Mapping[str, object], *, protocol: SurvivorForwardProtocol
) -> SurvivorForwardPowerPlan | None: ...


# survivor_forward_document(..., power: SurvivorForwardPowerPlan | None)  -- 新的必填关键字
```

`read_forward_power`：键不在 → `None`；在但不是对象或校验不过 → `SurvivorForwardError`（含 `survivor_forward_power`）；每个 `(horizon, metric)` 必须是协议在该期限检验的（`protocol.tested_metrics(h)`，期限不在协议里时它自己就拒绝）；主指标 `(primary_horizon, primary_metric)` 必须在 `planned` 里；`minimum_periods <= protocol.window_positions(primary_horizon)`。

文档 `power` 节（顶层，带 `wording: SURVIVOR_WORDING`，满足 `test_forward_report_survivor.py:249-266` 的"每节都有措辞"）：`status`（`"pre-registered (survivor-forward-power/v1)"` 或 `POWER_NOT_PRE_REGISTERED`）、`basis: POWER_BASIS`、`reading_rule: NOT_DETECTABLE_WORDING`、`plan`（`model_dump(mode="json")` 或 `None`）、`rows`。每行：`horizon`、`metric`、`role`、`test_level`（主：计划的；次：`by_first_step_level(q=protocol.q, family_size=次要族大小)`，写明"a lower bound under Benjamini-Yekutieli"）、`critical_value`、`planning_sd`、`sd_source`、`planned_periods`（`window_positions(h)`）、`planned`（`[{"mean","power"}]`）、`smallest_mean_at_target`、`periods_for_target`（`[{"mean","periods"}]`）、`autocorrelation`（`assumed_lag1`、`independent_period_equivalent`、该等价期数下的功效）、`headline` 与 `excluding_unverifiable` 两个窗口块：`positions`、`matured`、`gaps`（窗口内 `no_record + refused + unchecked + unverifiable` 的个数）、`usable_at_most`（`positions - gaps`）、`tested`（窗口已检验时该指标 `MetricTest.values`，否则 `None`）、`n` 与 `n_basis`（`"tested"` 或 `"usable_at_most"`）、`at_n`（`n >= 2` 时每个规划均值的功效，否则 `None`）、`minimum_periods`、`below_minimum`（`n < minimum_periods`）、`observed`（`{"wording": OBSERVED_WORDING, "sd", "lag1_autocorrelation"}`：窗口检验之后，`sd` 需 ≥2 个值，自相关需 ≥3 个值，否则 `None`）。打印行插在次要族之后、纸面账簿之前，第一行以 `power (survivor-forward-power/v1` 开头（或打印 `POWER_NOT_PRE_REGISTERED`），每行用"test level"而不是"alpha"，用"minimum periods"而不是"effective"，最后一行是 `NOT_DETECTABLE_WORDING`。（**corrected 2026-11-02: 最后一行只在主窗口满足之后才是 `NOT_DETECTABLE_WORDING`；窗口之前是中性的一行 `reading rule: printed once the primary window is satisfied`。**原因：`NOT_DETECTABLE_WORDING` 写的是"a one-sided p at or below …"，有计划的报告在窗口之前不印任何单侧 p 值，已有的"one-sided p"守卫此前只覆盖没有计划的登记，评审（Task 6，I1）发现这一条；守卫没有放宽。JSON 的 `power.reading_rule` 在窗口之前仍带这条规则——它是登记即定、与数据无关的常量，每份报告都一样。`backtest/power_analysis.py` 的 `power_table_lines` 总在末尾加上这条规则，只有 `p7_diagnostics power` 调用它，**不能**拿去印窗口之前的周报。）

- [ ] **Step 1：写失败的测试**——追加到 `tests/unit/test_strategy_survivor_forward.py`（`reported()` 先加一个关键字 `power: SurvivorForwardPowerPlan | None = None` 并传给 `survivor_forward_document(..., power=power)`；`:2424` 那处直接调用加 `power=None`）：

```python
from openalpha_cn.backtest.power_analysis import (
    NOT_DETECTABLE_WORDING,
    POWER_BASIS,
    by_first_step_level,
    power_of_mean_test,
)
from openalpha_cn.strategy_survivor_forward import (
    OBSERVED_WORDING,
    POWER_NOT_PRE_REGISTERED,
    SURVIVOR_FORWARD_POWER_KEY,
    SurvivorForwardPowerPlan,
    read_forward_power,
)

POWER_BLOCK: Final[Mapping[str, Any]] = {
    "schema_version": "survivor-forward-power/v1",
    "test_level": 0.05,
    "target_power": 0.8,
    "critical_value": "normal",
    "assumed_lag1_autocorrelation": 0.0,
    "minimum_periods": 2,
    "planned": [
        {
            "horizon": 20,
            "metric": "rank_ic",
            "planning_sd": 0.15,
            "planning_means": [0.037, 0.05, 0.1],
            "sd_source": "test value",
        },
        {
            "horizon": 20,
            "metric": "ew_minus_worst_decile",
            "planning_sd": 0.03,
            "planning_means": [0.01],
            "sd_source": "test value",
        },
    ],
}
"""Test values only (module docstring): not the V2-P7-006 numbers."""


def power_plan(
    read: SurvivorForwardProtocol | None = None, **changes: object
) -> SurvivorForwardPowerPlan:
    plan = read_forward_power(
        {SURVIVOR_FORWARD_POWER_KEY: {**POWER_BLOCK, **changes}},
        protocol=protocol() if read is None else read,
    )
    assert plan is not None
    return plan


def test_a_registration_without_a_power_block_has_no_plan_and_one_with_a_bad_block_is_refused() -> (
    None
):
    assert read_forward_power({}, protocol=protocol()) is None
    for changes, said in (
        ({"extra": 1}, "survivor_forward_power"),
        ({"critical_value": "z"}, "critical_value"),
        ({"minimum_periods": 4}, "minimum_periods"),
        ({"planned": [POWER_BLOCK["planned"][1]]}, "primary"),
        ({"planned": [*POWER_BLOCK["planned"], POWER_BLOCK["planned"][0]]}, "repeat"),
        ({"planned": [{**POWER_BLOCK["planned"][0], "metric": "top_minus_bottom"}]}, "not tested"),
        ({"planned": [{**POWER_BLOCK["planned"][0], "horizon": 7}]}, "horizon 7"),
    ):
        with pytest.raises(SurvivorForwardError, match=said):
            power_plan(**changes)


def test_the_power_section_states_the_planned_power_at_k_as_hand_computed() -> None:
    """K = 36 at sd 0.15, one-sided 0.05, normal: 0.435, 0.639, 0.991; the mean reaching 0.8 is
    0.0622; 0.8 needs 102, 56 and 14 periods (the route's arithmetic)."""
    read = protocol(window_periods=36)
    document = reported(
        {20: (), 5: ()},
        {},
        through=1,
        power=power_plan(read, minimum_periods=30),
        window_periods=36,
    )

    power = document["power"]
    assert (power["wording"], power["status"]) == (
        SURVIVOR_WORDING,
        "pre-registered (survivor-forward-power/v1)",
    )
    assert (power["basis"], power["reading_rule"]) == (POWER_BASIS, NOT_DETECTABLE_WORDING)
    primary = power["rows"][0]
    assert (primary["horizon"], primary["metric"], primary["role"], primary["planned_periods"]) == (
        20,
        "rank_ic",
        "primary",
        36,
    )
    assert [row["power"] for row in primary["planned"]] == pytest.approx(
        [0.43452959661329793, 0.6387600313123356, 0.9907422946265046], abs=1e-12
    )
    assert primary["smallest_mean_at_target"] == pytest.approx(0.062161871513109646, abs=1e-12)
    assert [row["periods"] for row in primary["periods_for_target"]] == [102, 56, 14]


def test_the_t_convention_is_computed_as_registered() -> None:
    read = protocol(window_periods=36)
    document = reported(
        {20: (), 5: ()},
        {},
        through=1,
        power=power_plan(read, critical_value="t", minimum_periods=30),
        window_periods=36,
    )

    primary = document["power"]["rows"][0]
    assert [row["power"] for row in primary["planned"][:2]] == pytest.approx(
        [0.417001, 0.621882], abs=1e-6
    )
    assert primary["periods_for_target"][1] == {"mean": 0.05, "periods": 57}


def test_a_secondary_row_is_powered_at_the_first_benjamini_yekutieli_step() -> None:
    """Family {h20 survivors_minus_ew, h20 ew_minus_worst_decile, h5 rank_ic}: m = 3, q = 0.2."""
    document = reported({20: (), 5: ()}, {}, through=1, power=power_plan())

    secondary = document["power"]["rows"][1]
    level = by_first_step_level(q=0.2, family_size=3)
    assert secondary["role"] == "secondary"
    assert secondary["test_level"] == level == pytest.approx(0.2 / 5.5, abs=1e-15)
    assert secondary["planned"][0]["power"] == power_of_mean_test(
        mean=0.01, sd=0.03, periods=3, test_level=level, kind="normal"
    )


def test_a_gap_reduces_the_usable_periods_and_the_power_and_is_held_to_the_minimum() -> None:
    """Position 2 of 3 (ordinal 21) has no record: n = 2 of 3, below a minimum of 3."""
    outcomes = every_session(61, skip=frozenset({21}))
    document = reported(
        {20: outcomes, 5: ()}, checked_by(outcomes), through=61, power=power_plan(minimum_periods=3)
    )

    window = document["power"]["rows"][0]["headline"]
    assert (window["positions"], window["gaps"], window["usable_at_most"]) == (3, 1, 2)
    assert (window["tested"], window["n"], window["n_basis"]) == (2, 2, "tested")
    assert [row["power"] for row in window["at_n"]] == [
        power_of_mean_test(mean=mean, sd=0.15, periods=2, test_level=0.05, kind="normal")
        for mean in (0.037, 0.05, 0.1)
    ]
    assert (window["minimum_periods"], window["below_minimum"]) == (3, True)


def test_the_observed_sd_is_described_and_never_used_for_power() -> None:
    """After the window: the rank ICs of ordinals 1 and 41 are -0.01 and 0.04; before it, nothing
    is observed. The planned and the window powers are the plan's whatever was observed."""
    before = reported(
        {20: every_session(25), 5: ()},
        checked_by(every_session(25)),
        through=25,
        power=power_plan(),
    )
    observed_before = before["power"]["rows"][0]["headline"]["observed"]
    assert (observed_before["sd"], before["power"]["rows"][0]["headline"]["n_basis"]) == (
        None,
        "usable_at_most",
    )

    outcomes = every_session(61, skip=frozenset({21}))
    after = reported({20: outcomes, 5: ()}, checked_by(outcomes), through=61, power=power_plan())
    observed = after["power"]["rows"][0]["headline"]["observed"]
    assert observed["wording"] == OBSERVED_WORDING
    assert observed["sd"] == pytest.approx(statistics.stdev([-0.01, 0.04]), abs=1e-15)
    assert observed["lag1_autocorrelation"] is None
    assert after["power"]["rows"][0]["planned"] == before["power"]["rows"][0]["planned"]


def test_without_a_power_plan_the_section_says_power_cannot_be_stated() -> None:
    document = reported({20: (), 5: ()}, {}, through=1)

    assert document["power"] == {
        "wording": SURVIVOR_WORDING,
        "status": POWER_NOT_PRE_REGISTERED,
        "basis": POWER_BASIS,
        "reading_rule": NOT_DETECTABLE_WORDING,
        "plan": None,
        "rows": [],
    }
    assert any(POWER_NOT_PRE_REGISTERED in line for line in survivor_forward_lines(document))


def test_the_power_lines_carry_the_reading_rule_and_no_verdict_word() -> None:
    outcomes = every_session(61)
    document = reported({20: outcomes, 5: ()}, checked_by(outcomes), through=61, power=power_plan())

    lines = survivor_forward_lines(document)
    assert any(line.startswith("power (survivor-forward-power/v1") for line in lines)
    assert any(NOT_DETECTABLE_WORDING in line for line in lines)
    assert verdict_words(document) == []
```

追加到 `tests/unit/scripts/test_forward_report_survivor.py`：

```python
from openalpha_cn import strategy_survivor_forward as forward

POWER: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-power/v1",
    "test_level": 0.05,
    "target_power": 0.8,
    "critical_value": "normal",
    "assumed_lag1_autocorrelation": 0.0,
    "minimum_periods": 2,
    "planned": [
        {
            "horizon": 20,
            "metric": "rank_ic",
            "planning_sd": 0.15,
            "planning_means": [0.05],
            "sd_source": "test value",
        }
    ],
}
"""Test values only (module docstring)."""


def test_the_report_states_that_no_power_plan_was_registered(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _report(world, capsys)

    assert code == 0, err
    assert _document(world)["power"]["status"] == forward.POWER_NOT_PRE_REGISTERED
    assert forward.POWER_NOT_PRE_REGISTERED in out
    _no_verdict_word(out)


def test_the_report_reads_the_power_plan_from_the_registrations_settings(
    world: tds.World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = forward.read_forward_power
    seen: list[tuple[dict[str, Any], forward.SurvivorForwardProtocol]] = []

    def planned(settings: Mapping[str, Any], *, protocol: forward.SurvivorForwardProtocol) -> Any:
        seen.append((dict(settings), protocol))
        return real({**settings, forward.SURVIVOR_FORWARD_POWER_KEY: POWER}, protocol=protocol)

    monkeypatch.setattr(forward, "read_forward_power", planned)
    code, _out, err = _report(world, capsys)

    assert code == 0, err
    ((settings, read),) = seen
    assert settings == settings_with()
    assert read.window_periods == PROTOCOL["window_periods"]
    row = _document(world)["power"]["rows"][0]
    assert (row["horizon"], row["metric"], row["planned_periods"]) == (20, "rank_ic", 2)
```

- [ ] **Step 2：运行，确认失败。** `PYTHONPATH="$PWD/src" uv run pytest tests/unit/test_strategy_survivor_forward.py tests/unit/scripts/test_forward_report_survivor.py -q`。预期：导入 `OBSERVED_WORDING` 等名字时 `ImportError`（整份文件收集失败）——先确认这一点，再把新导入挪到新测试函数内部（局部导入）以便看到逐条失败：`survivor_forward_document() got an unexpected keyword argument 'power'` 与 `KeyError: 'power'`。

- [ ] **Step 3：最小实现。** 只在 `strategy_survivor_forward.py` 加：两个常量与两个模型、`read_forward_power`、`survivor_forward_document` 的 `power` 关键字与 `"power": _power_section(power, protocol=protocol, by_horizon=by_horizon)`、`_power_section`/`_window_power`/`_power_lines`、`survivor_forward_lines` 里在 `_family_lines` 之后插 `lines += _power_lines(document["power"])`；模块文档串在"Left open for the V2-P7-006 checkpoint"一段之后加一段"## Power (`survivor_forward_power`)"说明规划值、观测值、两种口径、次要族的下界水平、缺口与最小期数。`scripts/forward_report.py` 的 `_survivor_report` 里在 `:515` 之后加 `power = forward.read_forward_power(admitted.settings, protocol=protocol)`，`:582` 的调用加 `power=power`，`_survivor_forward_report` 文档串（`:444`）补一句"and the power plan beside it, its absence said in the document"。`SurvivorForwardError` 已在脚本的拒绝映射里（`:481-497`），不用改。

- [ ] **Step 4：运行，确认通过**；再跑 `tests/unit/test_strategy_survivor_forward.py` 与 `tests/unit/scripts/test_forward_report_survivor.py` 全文件（原有测试逐位不变，除了 `reported()` 的新关键字）、`ruff`、`mypy src scripts`。

- [ ] **Step 5：变异探针：** (a) 次要行用计划的 `test_level` 而不是 BY 下界；(b) `usable_at_most` 不减 `unverifiable`（敏感性块应变红——补一条带 UNVERIFIABLE 的断言：`checked_by(outcomes, prd_0041=("unverifiable", "recompute_differs", ("x",)))` 时 `excluding_unverifiable.gaps == 1`）；(c) 窗口检验后功效改用观测 `sd`（"never used for power" 变红）；(d) `below_minimum` 用 `<=`；(e) 打印行里写 `alpha`（措辞测试变红）。

- [ ] **Step 6：** 跑 `tests/unit`，提交：`feat(V2-P7-018): the weekly report states the pre-registered power beside its sample counts, and what a gap costs`。

- [ ] **Step 7（单独提交，评审可单独拒绝；U12）：过时的 G-0 条目。** `strategy_survivor_forward.py:1921-1923` 的 `("g0_needs_research_and_validation_conclusions", "G-0 needs conclusions on the research and validation windows first")` 与 2026-11-01 改写的 G-0（规格 §5.2）矛盾，每份周报都会打印。改成 `("g0_is_one_preregistered_read_of_2022_2023", "G-0 rests on one pre-registered, read-once reading of 2022-01-04..2023-12-29 against a stop rule written before it (V2-P7-024): a first out-of-sample reading, never confirmatory evidence")`；同步 `tests/unit/test_strategy_survivor_forward.py:1862-1871` 与 `tests/unit/scripts/test_forward_report_survivor.py:277-286` 的 `ALWAYS`。先改测试（红），再改代码（绿）；提交：`fix(V2-P7-018): the report's G-0 item says the gate the 2026-11-01 route adopted`。

---

## Task 7（操作性）：研究窗口上的真实读数 → `T11-diagnostics-research-window.md`

> **等用户明确说"开始"才做。** 不联网（零次请求）；只读研究窗口（上限 2021）；不碰原面板。

**Issue:** `V2-P7-004`。**依赖:** Task 1–4 合入。**产出:** `docs/research/p7-step0-validation/T11-diagnostics-research-window.md`（探索性、样本内、不是证据；输出 JSON/文本留在运行目录，不入库，报告里引用它们的 sha256，与 T8 引用脚本 sha 的做法一样）。

- [ ] **Step 1：克隆。** `mkdir -p ~/openalpha-runs/p7-diag-2026-11/runtime && cp -c -R ~/openalpha-research/panel ~/openalpha-runs/p7-diag-2026-11/runtime/panel`（APFS 写时复制；路径无空格、不在主检出、不在 `/tmp`）。之后只用克隆。
- [ ] **Step 2：探针（3 个信号日）。** `PYTHONPATH="$PWD/src" uv run python scripts/research/p7_diagnostics.py explore --runtime-dir ~/openalpha-runs/p7-diag-2026-11/runtime --as-of 2026-10-09T00:00:00+08:00 --first 2017-01-03 --last 2017-03-31 --stride 20 --horizons 20 --transform cross_section_standard/v1 --out ~/openalpha-runs/p7-diag-2026-11/probe`。记录每日耗时；被按名拒绝（例如 `MissingFactorBuildError`：处理档不是这个变换）就停下来报告，不猜。
- [ ] **Step 3：T8 的 59 个信号日。** `--first 2017-01-03 --last 2021-10-29 --stride 20 --horizons 5,20 --scores composite,residual_vol_60/v1,return_vol_60/v1,turnover_60/v1,downside_vol_60/v1`。与 T8 (d)（总体 B、过滤开：秩 IC +0.1062，se 0.0193；最差十分位超额 −1.49%/20 日，se 0.37；幸存者毛超额 +2.84%/年）逐项对照，**解释差别**（标签的拒绝码对 T8 的"前向两根 K 线"要求；成员资格不要求前向 K 线；成交额窗口），不设"通过线"。
- [ ] **Step 4：h=1/h=5 的离散度。** `--stride 5 --horizons 1,5,20`（约 240 个信号日；按 Step 2 的耗时先报预计时长，超过 4 小时先问）。得到 h=1、h=5、h=20 的日期间 IC 标准差、一阶自相关、按年；用 `power` 子命令按两种口径打印功效表。
- [ ] **Step 5：写 T11**（每个数字带"样本内、探索性"），并列出要更新的地方：周报的 `h5_ic_dispersion_unmeasured` 条目（`strategy_survivor_forward.py:1936-1942`）、`006` 的规划标准差（U4）。提交：`docs(V2-P7-004): T11 -- the diagnostics on the research window, in-sample and exploratory`。

---

## 后续文档任务要加的台账行（本计划不碰台账）

- 新能力行（`artifacts/openalpha-v1-feature-coverage/features.csv`、`summary.json`、`docs/release/openalpha-v1-feature-ledger.md` 同一提交，硬规则 7、9）：`OA-BT-0xx` 十分位读出（`tests/unit/backtest/test_decile_readout.py::test_a_loser_tail_is_read_as_hand_computed`、`tests/unit/test_decile_readout_agreement.py::test_the_readout_is_the_forward_reports_arithmetic`）；`OA-BT-0xx` 功效计算（`tests/unit/backtest/test_power_analysis.py::test_the_normal_power_is_the_routes_arithmetic`）；`OA-RES-0xx` P7 诊断与窗口护栏（`tests/unit/scripts/test_p7_windows.py::test_a_capped_store_refuses_every_door_past_its_ceiling`、`tests/unit/test_p7_diagnostics.py::test_every_exploratory_document_and_line_carries_the_exploratory_wording`）；`OA-RES-0xx` 读一次路径（`tests/unit/scripts/test_p7_reading.py::test_a_read_that_crashed_after_its_claim_is_the_one_read`）。
- 更新 `OA-OPS-046`…`049`（周报）中预测层那一行的验收，加 `tests/unit/test_strategy_survivor_forward.py::test_the_power_section_states_the_planned_power_at_k_as_hand_computed`。
- 路线图 `V2-P7-004` 关闭、`V2-P7-018` 关闭（若 `009` 之外没有别的未满足项）；运行手册 `docs/runbooks/forward-report.zh-CN.md` 加 `power` 节；规格 §9 加一行（`018` 只依赖 `004` 的功效计算器，见矛盾第 9 条；若用户采纳 U3 的 t 口径，更正 §4.5 与 §9 2026-11-01 第 4 行的"K≈56"）。

## 用户决定（推荐，不由本计划决定）

| # | 决定 | 推荐 | 挡住什么 |
|---|---|---|---|
| U1 | 目标功效 | 0.80 | `006b` 的功效块数值；不挡代码 |
| U2 | 检验水平（单侧） | 0.05（与 P6 判据一致） | 同上 |
| U3 | 临界值口径：正态还是 t | **t**（登记的符号翻转检验在小样本上更接近 t 检验；K=12 时正态 31% 对 t 26%，IC 0.05 达到 80% 需 K=56 对 57） | 同上；规格 §4.5 的措辞 |
| U4 | 规划标准差 | h20 秩 IC 用 0.15（T8 (d)：0.0193×√59≈0.148，探索性）；h5 用 Task 7 的实测（向上取整）；其他指标用 Task 7 的实测 | 同上；h5 在 Task 7 之前无值 |
| U5 | 规划均值 | 0.037（2021 年水平）、0.05、0.10（研究期水平） | 同上 |
| U6 | 中期无效性规则的形状 | 在 K/2 看一次，"均值 ≤ 0 即停"：IC 0.05 时功效 0.639 → 0.631（−0.8 个点），真值为 0 时停下的概率 0.5，第一类错误 0.05 → 0.04875；工具可算任何别的形状 | `006a` 第 6 项 |
| U7 | 功效计划放哪 | 并列的 `survivor_forward_power` 块（不动协议 v1）；块缺失时周报照常、明说"功效无从陈述"；`006b` 的登记必须带它 | Task 6 的形状 |
| U8 | `024` 的停止规则数值、`minimum_periods`、读不出时怎么算 | 由用户定；工具在登记时打印规则的运行特征，例如"均值 IC ≤ 0.02、约 24 期、σ 0.15"：真值 0 时停 0.74、0.037 时停 0.29、0.05 时停 0.16、0.10 时停 0.004；推荐"少于 `minimum_periods` 就按触发处理（读不出来的闸门是关着的闸门）"、`minimum_periods = ceil(0.8×K)` | `024` |
| U9 | 模块放置 | 纯统计进 `src/openalpha_cn/backtest/`；读面板 / 护栏 / 读一次进 `scripts/research/`（两处都受绑定） | Task 1–5 的文件位置 |
| U10 | 是否提供自由形式的"越权参数" | **不提供**：进 2022-01-04 之后的唯一路径是读一次（路线图 `004` 的字面"越权要显式参数"按这个解释，见矛盾第 5 条） | Task 3–5 |
| U11 | `024` 的口径与计划 | 用每日路径的资格定义（总体 B、不要求前向 K 线），不用 T8 的 harness（总体 A、要求前向 K 线）；信号日 2022-01-04 起每 20 个交易日，出场不晚于 2023-12-29（约 24 期）；2024-01-02..2026-09-24 的次要读数与主读数**在同一次提交里登记、两次读都在之后** | `024` |
| U12 | Task 6 Step 7（修过时的 G-0 条目）是否一起做 | 做（每份周报都在打印一条与规格矛盾的闸门描述） | 无 |
| U13 | Task 7 开工 | 零请求、只读研究窗口、APFS 克隆约 6.1 GB 的元数据 | T11 与 U4 |

## 受绑定代码（任何在这些改动之前写下的登记都会失效；本计划全部在 `006b` 之前）

- `src/openalpha_cn/backtest/decile_readout.py`（新，Task 1）、`src/openalpha_cn/backtest/power_analysis.py`（新，Task 2）
- `pyproject.toml`（两个导入合约的源列表各加两行，Task 1、2）
- `scripts/research/p7_windows.py`、`p7_diagnostics.py`、`p7_reading.py`（新，Task 3、4、5）
- `src/openalpha_cn/strategy_survivor_forward.py`（改，Task 6，含 Step 7）
- `scripts/forward_report.py`（改 2 行 + 文档串 1 句，Task 6）
- 不改：`scripts/daily_selection.py`、`uv.lock`、`scripts/research/grid.py`、`registry.py`、`domain/`。`019` 的冒烟克隆钉住它自己的提交，不受影响。

## 与代码或规格相矛盾之处（发现）

1. **路线图 §P7 表下的闸门摘要仍是旧 G-0**（`docs/specs/v2/openalpha-cn-v2-roadmap.md:504`："空信号对照量清基准拖累……Step 0 形态在研究期与验证期按预登记判据得出结论"），与规格 §5.2 的 2026-11-01 改写和同一节 `:475` 矛盾。
2. **每份周报都在打印旧 G-0**：`strategy_survivor_forward.py:1921-1923` 的 `g0_needs_research_and_validation_conclusions` 写"G-0 needs conclusions on the research and validation windows first"，被两份测试的 `ALWAYS` 钉住（`tests/unit/test_strategy_survivor_forward.py:1862-1871`、`tests/unit/scripts/test_forward_report_survivor.py:277-286`）。Task 6 Step 7。
3. **功效有两种口径混在文档里**：路线调整 V8、规格 §4.5 与本简报（K=12 → 31%、K=56 → 80%）是正态近似；规格 §9 2026-10-14 Part E 行的"K=12 在 IC=0.05 时功效仅约 25%"是 t 临界值（`t_{0.95,11}=1.796` → 26%）。按 t，IC 0.05 达到 80% 要 K=57，不是 56。
4. **规格 §3.6 的"需要的期数"是"期望 t 统计量 = 2"**（σ 取 0.14），单侧 0.05 下对应功效约 64%，不是"能检出"；而且用的 σ（0.14）与 T8 和路线（0.15）不同。容易被读成检出保证。
5. **路线图 `004` 验收"越权要显式参数并写进输出"与 `024`"只读一次"冲突**：一个可重复使用的越权参数就能反复偷看验证期。本计划只提供单次、先认领后测量的路径（U10）。
6. **"参数沿用 T8"有两种读法**：T8 的任务口径 (c) 用流动性总体 A，且可投资集合要求前向两根 K 线（`main_board_remeasure.py:252-255`，一个对成员资格的前视）；登记的内核用总体 B（用户 2026-10-13）且没有前视。与内核一致的是 T8 (d)（秩 IC +0.1062）。工具量内核的定义（U11）。
7. **验收措辞与周报的措辞守卫相撞**：`018` 的"最小有效 n"在英文里就是 `effective`，`alpha` 也在禁用词里（两份测试的正则）；本计划写成 `minimum_periods` 与 "test level"。
8. **规格 §7.2 把二次使用段写成"2024-01-02 到登记日"，G-0 的次要读数只到 2026-09-24**：工具对 2026-09-25 起的日期一律不读（没有任何路径），两者并不冲突，但"二次使用段"的终点在文档里有两种写法。
9. **`018` 依赖 `004` 被写成整体依赖**（路线图 `:496`、`:161`），实际只依赖 `004` 的功效计算器（Task 2）；`018` 可在 Task 2 + Task 6 后关闭，不必等护栏与读一次。
10. **周报协议没有检验水平**：`SurvivorForwardProtocol`（`strategy_survivor_forward.py:370-385`）只给 p 值、不设阈值（`:1016-1017`）；功效需要一个水平，所以功效块第一次把检验水平写进登记（只用于功效，周报仍不印结论词）。
11. **只读也会写面板根目录**：每次读都会在面板根目录建 `catalog.duckdb.lock` 与 `.gate`（`panel/store.py:1383-1414`），构造时还会 `mkdir`（`:1693-1694`）；任何对 `~/openalpha-research/panel` 的"只读"原地运行都会改动它。Task 7 与 `024` 一律用克隆（`019` 已是这样）。
12. **`h5_ic_dispersion_unmeasured`**（`strategy_survivor_forward.py:1936-1942`）在 Task 7 之后就不再成立，需要一个后续小任务改成"规划值来自 T11（探索性）"。

## 修订记录

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-11-01 | 立项（草案）：Task 1–6 写到可派发，Task 7 为操作性 | 计划 3 轨道 D 简报 |
