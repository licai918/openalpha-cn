# P7 批次 3 · `V2-P7-024`：预登记停止规则、唯一一次读数（G-0 的新内容）与两条协议项

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **状态：草案，等用户审阅。** 本文只是计划；不改源码、不改测试、不写登记、不读任何窗口、不发任何网络请求。每个 `file:line` 都是起草者在本 worktree（本地 `main` @ `ba60330`，已 `--ff-only` 合入 `feat/v2-p7-batch-3`）上读过的。**`2022-01-04..2023-12-29` 的唯一一次真实读是 Task 5，是用户的操作步骤，不是实现者的任务**：实现者交付的是让那一次读安全、可核对、不可重复的工具与文档。
>
> **研究诚实：** 这次的读是「第一次样本外读数」，**不是确认性证据**（窗口被 P6 的前 N 名策略用过，不完全干净）；停止规则写在读之前；**读完之后不在该窗口上调参**；「不可测（not measurable）」是它自己的结论类别，**不读成「没有证据」**；「停止规则未触发」只**允许** `V2-P7-006` 继续，**不是证据**。

**Goal:** 把用户 2026-11-07 采纳的停止规则落成**预登记的数值**（一条数字一个具名常量），把「不可测」变成正式的第三种结论，并把那唯一一次验证窗口读数做成一条**具名、可核对、只读一次**的命令路径：它记录 `as_of_reason` 与先验的精确期数，在仓库是浅克隆或有 grafts 时拒绝，在日历算出的期数与先验不一致时**停下报告而不是读**。

**Architecture:** 改动分三块，全部在既有研究工具与其受绑定路径里，不新增组装根：

- `src/openalpha_cn/strategy_survivor_forward.py`：前向协议的停止规则块（`survivor-forward-stop-rule/v1`，与前向协议、功效块**并列**，不塞进 `survivor-forward-protocol/v1`）、一组**采纳值常量**、以及周报里的只读陈述；`scripts/forward_report.py` 只多读一块、多传一个参数。
- `scripts/research/p7_reading.py`：读一次的停止规则由同一组常量构造（`adopted_stop_rule`），`evaluate_stop_rule` 增加第三种结论 `not measurable`，登记体增加 `expected_periods` 与认领前的精确核对（`counted_periods`、`expected_periods_problem`）。
- `scripts/research/p7_read.py`（新）：那唯一一次读的命令，`register [--dry-run]` 与 `read` 两个子命令；`read` 先做期数核对（不一致退出码 4），再调用 `read_once`（唯一库路径），并打印结果与停止规则判定。

**Tech Stack:** Python 3.12、标准库、pydantic v2（新块沿用 `extra="forbid"` + `Literal[".../v1"]` 的既有写法）、pytest、argparse。门禁：`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy src scripts`、`uv run pytest tests/unit`（**实现者只跑定向文件，整个 `tests/unit` 由控制者在批次末尾串行跑一次**——批次 1 的教训）。

**Spec / 依据:** `AGENTS.md`（硬规则 1–9、「研究诚实」）；`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §5.2（G-0，`:227-235`）、§5.3、§9 的 2026-11-01 G-0 行与 2026-11-02 各行（读一次的整窗口/认领/浅克隆/grafts、`READ_ONCE_POWER_WORDING`、`018` 的功效块「工具参数不是冻结协议值」）；`docs/superpowers/plans/2026-11-01-p7-route-adjustment.md` §2.1（`:65-73`）、§2.7（`:110-113`）、§2.8（`:115-122`）；`docs/superpowers/plans/2026-11-07-p7-dev-batch-2-closeout.md` §1（`024` 四个前置）、§5 第 3、4 条（`:111-112`）、§6（`:118-130`）；路线图 `V2-P7-024`（`docs/specs/v2/openalpha-cn-v2-roadmap.md:506`）、`V2-P7-004`（`:486`）、闸门行（`:509`）；`.superpowers/sdd/batch-3/common.md` 的 2026-11-07 采纳值。

---

## Global Constraints

逐字与逐值；每个任务隐含这一节。

- 每个提交、分支写 issue ID：`V2-P7-024`（Task 1 另写 `V2-P7-006`，因为那块最终由 `006` 写进登记）。提交标题 `feat(V2-P7-024): …` / `fix(V2-P7-024): …` / `docs(V2-P7-024): …`；末尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- **采纳值（用户 2026-11-07；一个数字一个常量，不得在别处重抄数字）**：
  - 主统计量 = h20 秩 IC：`ADOPTED_PRIMARY_METRIC = "rank_ic"`，`ADOPTED_PRIMARY_HORIZON = 20`（与记录声明的 `SURVIVOR_HORIZON = "20d"`，`src/openalpha_cn/strategy_survivor_registration.py:260`，同值）。
  - 停止（无证据）条件：秩 IC 均值 **≤ 0.02**：`ADOPTED_STOP_MEAN_AT_OR_BELOW = 0.02`。
  - 或最差十分位价差均值 **≤ 0**：`ADOPTED_STOP_TAIL_METRIC = "ew_minus_worst_decile"`，`ADOPTED_STOP_TAIL_MEAN_AT_OR_BELOW = 0.0`。
  - 前向 K = **36**（h20 位置）：`ADOPTED_WINDOW_PERIODS = 36`。
  - 中期无效性查看在 **K/2 = 18** 个累积位置：`ADOPTED_INTERIM_PERIODS = 18`，`ADOPTED_INTERIM_PERIODS * 2 == ADOPTED_WINDOW_PERIODS`；停的条件是累积均值 **≤ 0**：`ADOPTED_INTERIM_MEAN_AT_OR_BELOW = 0.0`。
  - 主期限保持 **20**（与第一条同一常量）。
  - 「不可测」是正式结论：第三种 `outcome == "not measurable"`，**不是**「没有证据」。
- **两个记录不是一回事，不得合并**：(a) **读一次的停止规则** `scripts/research/p7_reading.py:360-380` 的 `StopRule(score, horizon, minimum_periods, any_of)`——它判 2022–2023 那一次读数；(b) **前向协议的停止规则**（本计划新增的 `survivor-forward-stop-rule/v1` 块）——它判 `006b` 之后的累计前向样本，K 与中期查看属于它。两者**共用**上面那组常量，但**不共用**记录：K=36 放到历史读数上没有意义（那个窗口最多 24 个 h20 非重叠期）。
- **只读一次**（`p7_reading.py:32-104`、`:1269-1384`）：一个读数覆盖整个窗口（第一天 = 窗口第一个会话，最后一天 = 最后一个会话）；每个窗口只读一次，不论读数叫什么名字；认领必须**提交**并留在 refs 里（refs + 未过期 reflog）；**拒绝浅克隆**（`:1282-1289`）与 **grafts 文件**（`:1290-1305`）；替换 ref 被忽略（`--no-replace-objects`，`:1387-1390`）。
- **顺序**：验证窗口先读、其 `<validation>.window-claim.json` 先**提交**，`second_use` 才可读（`p7_reading.py:1339-1384`，`ValidationNotSpentError`）；反方向不检查。
- **运行目录**：不得是 P6 的研究根 `~/openalpha-research` 或任何检出（`scripts/research/p7_windows.py:101-104`、`:136-169`；`p7_reading.read_once` 第一条语句 `:1099`）；只读连接也会在目录库旁建 `catalog.duckdb.lock`/`.gate`。
- **窗口与日历**：验证窗口 `2022-01-04..2023-12-29`（`p7_windows.py:213-217`），二次使用段 `2024-01-02..2026-09-24`（`:218-224`）。精确期数 = `ceil(len(plan.sessions) / ceil(horizon / stride))`（`p7_reading.py:621-643`；禁运见 `p7_windows.plan_signal_days:469`；抽样见 `p7_diagnostics.summarize:579`）。
- **先验期数**：真实日历 484 个会话、步长 20、h20 → 24 个非重叠期（最后一次信号日的标签出口越过窗口末日，被禁运）；夹具的 519 个工作日 → 数据无关上界 26、日历精确 25（`tests/unit/scripts/test_p7_reading.py:1245-1268`）。**24 没有在任何离线记录里复核过**（批次 2 收尾 §6 第 6 条），**工具必须自己算**；算出来不是先验数就停。
- **措辞**：一切仍是「候选」；任何新增文本不得出现 `significant`、`effective`、`confirmed`、`alpha`、`outperform`、`passed`、`failed`、`proven`（禁止词表 `tests/unit/test_p7_diagnostics.py:91-93`、`tests/unit/test_strategy_survivor_forward.py:196`、`:1946-1950`）。
- **不联网、不发请求、不读真实面板**：所有面板夹具在测试时生成（硬规则 6）；`.parquet/.duckdb/.sqlite3/.db` 不提交（`scripts/verify_publication.py`）。Task 5 也是 **0 次请求**（`cp -c` 克隆上的纯计算）。
- **受登记绑定**：`src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`（`scripts/research/registry.py:93-94`），外加 `scripts/daily_selection.py`、`scripts/forward_report.py`（`also_bound`）。本计划改 `src/` 一个文件、`scripts/research/` 两个文件（一个新）、`scripts/forward_report.py`；**今天没有任何登记**（每日登记自 2026-10-08 暂停），所以不改动任何登记、不使任何绑定作废。全部发生在 `006b` 之前，允许。
- 测试卫生（记忆）：worktree 里跑测试设 `PYTHONPATH=<worktree>/src`；**实现者只跑定向文件，整个 `tests/unit` 由控制者在批次末尾串行跑一次**；变异探针用 `PYTHONDONTWRITEBYTECODE=1`，改前 `cp` 到草稿目录、改完恢复并 `cmp` 确认逐字节一致；绝不在 `git --exec`/hook 里跑测试。

## Review Focus

最可能把「唯一一次不可逆的读」读错、或把结论说错的五类失败模式，每条由所在任务里的测试钉住：

1. **停止规则的数值漂移**：读一次与 `006` 各抄一份 0.02 / 0 / 36 / 18 / 20，日后改一处不改另一处。→ Task 1 `test_the_adopted_stop_rule_is_one_constant_per_number`、`test_the_adopted_block_reads_back_as_the_constants`；Task 2 `test_the_reading_and_the_forward_protocol_share_every_number`。
2. **「不可测」被读成「没有证据」**：今天 `evaluate_stop_rule` 把「不可读」折成 `triggered` 并印 `STOP_TRIGGERED_WORDING`（`p7_reading.py:904-916`），那段话说「tell the user there is no evidence」（`:252-255`）。→ Task 2 `test_a_rule_that_cannot_be_read_is_not_measurable_and_not_no_evidence`、`test_a_data_refusal_is_the_reads_recorded_error_and_closes_the_gate`（改写断言）；结果文本断言 `NOT_MEASURABLE_WORDING in text and STOP_TRIGGERED_WORDING not in text`。
3. **在一个不是计划那一天的日历上读**：先验 24、面板算出别的数时仍读下去（或者反过来，把先验数改成算出来的数以「对齐」）。→ Task 3 `test_the_command_stops_when_the_calendar_does_not_hold_the_expected_periods`、`test_the_preflight_refuses_a_mismatched_count_before_the_claim`。
4. **先读二次使用段**：2024–2026 的结果影响 2022–2023 的判定；认领只在磁盘上、没提交就回去重读验证窗口。→ Task 3 `test_the_command_refuses_second_use_before_the_validation_claim_is_committed`（`ValidationNotSpentError` 的既有实现与测试见 `p7_reading.py:1339-1384`、`tests/unit/scripts/test_p7_reading.py:1867-1903`）。
5. **浅克隆 / grafts 让「第一次」不可证**：历史停在切点，早先提交的认领看不见。→ 既有 `tests/unit/scripts/test_p7_reading.py:829-835`、`:926-935` 已钉住；Task 3 只用命令层测试确认它以退出码 3 现形。

## 用户决定（Decisions for the user）

**已采纳（2026-11-07，用户；不再重开）**，每条给一个常量：

| # | 决定 | 值（一个常量） |
|---|---|---|
| C1 | 主统计量 = h20 秩 IC | `ADOPTED_PRIMARY_METRIC = "rank_ic"`、`ADOPTED_PRIMARY_HORIZON = 20` |
| C2 | 停止（无证据）若秩 IC 均值 ≤ 0.02 | `ADOPTED_STOP_MEAN_AT_OR_BELOW = 0.02` |
| C3 | 或最差十分位价差均值 ≤ 0 | `ADOPTED_STOP_TAIL_METRIC = "ew_minus_worst_decile"`、`ADOPTED_STOP_TAIL_MEAN_AT_OR_BELOW = 0.0` |
| C4 | 前向 K = 36（h20） | `ADOPTED_WINDOW_PERIODS = 36` |
| C5 | 中期无效性在 K/2 = 18，累积均值 ≤ 0 即停 | `ADOPTED_INTERIM_PERIODS = 18`、`ADOPTED_INTERIM_MEAN_AT_OR_BELOW = 0.0` |
| C6 | 主期限保持 20 | 同 C1（记录声明的 `SURVIVOR_HORIZON = "20d"` 是它的见证） |
| C7 | 「不可测」是正式结论，绝不读成「没有证据」 | 第三种 `outcome = "not measurable"` + `NOT_MEASURABLE_WORDING` |

**仍未定（本计划按推荐写，标出代价）**：

| # | 决定 | 推荐 | 代价 / 挡住 |
|---|---|---|---|
| R1 | 读一次的 `minimum_periods`（规则至少要多少期才算「可读」） | **等于 `expected_periods`**（真实日历上是 24）：少一期就是「不可测」，不是「n=23 的弱读数」 | 改小（如 18）会允许一个缺了 6 个信号日的窗口照旧给结论；改大则规则永远不可达、认领前就被拒 |
| R2 | G-0 遇到 `not measurable` 时做什么 | **不启动前向时钟（失败即关闭），并在文本里明说这不是「没有证据」**；工具只报结论，不替用户推进 | 若用户要「不可测也继续」，改 `NOT_MEASURABLE_WORDING` 与 §5.2 的一句话；不影响任何代码路径 |
| R3 | 真实读的「开始」与那一次的 `--as-of` / `as_of_reason` | Task 5：先 `register --dry-run` 得到精确期数；**等于 24 才登记**，不等就停下来报告 | 这是不可逆的一次读（Task 5）；不等要用户另说怎么处理（重取日历、查面板），不得改先验数去对齐 |
| R4 | 读一次的请求与配置（book / transform / stride / horizons / tails / min_coverage / max_carry_sessions） | 命令行必须逐项给出（不给代码默认值），运行手册记下用户批准的那一条命令 | 本计划不冻结它们；`register` 会把它们写进登记，日后 `006a` 可以再对照 |
| R5 | 二次使用段（2024–2026）这一次是否要读 | 可选、次要、**非确认性**；只在验证窗口的认领**已提交**之后 | 不读：少一条非确认性描述，代价为零 |

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `src/openalpha_cn/strategy_survivor_forward.py`（改） | 采纳值常量；`survivor-forward-stop-rule/v1` 模型、读取与对协议的核对；`adopted_forward_stop_rule()`；文档 `stop_rule` 段与打印行 | 1 |
| `scripts/forward_report.py`（改，`also_bound`） | 周报读一块 `read_forward_stop_rule` 并传给 `survivor_forward_document`（2 行） | 1 |
| `scripts/research/p7_reading.py`（改） | `adopted_stop_rule()`；`evaluate_stop_rule` 的第三种结论与措辞；`expected_periods` 字段、`counted_periods`、`expected_periods_problem`；认领前的期数核对；模块 docstring | 2、3 |
| `scripts/research/p7_read.py`（新） | `register [--dry-run]` 与 `read` 两个子命令，退出码 0/2/3/4 | 3 |
| `tests/unit/test_strategy_survivor_forward.py`（改） | 常量、块、核对、文档段与打印行 | 1 |
| `tests/unit/scripts/test_forward_report_survivor.py`（改） | 周报读到块 / 读到「没登记」 | 1 |
| `tests/unit/scripts/test_p7_reading.py`（改） | 构造器与常量共用；登记写入的值；第三种结论与结果文本；`expected_periods` 与期数核对 | 2、3 |
| `tests/unit/scripts/test_p7_read.py`（新） | 命令的 `--dry-run`、登记、读、期数不一致、二次使用顺序、浅克隆 | 3 |
| `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md`（改） | §5.2 G-0 一句（三种结论）；§9 的 2026-11-07 行 | 4 |
| `docs/runbooks/p7-reading.zh-CN.md`（新） | 那唯一一次读的操作程序（前提、命令、判读、提交） | 4 |

## 依赖与并行

```
Task 1（前向停止规则：常量 + 块 + 周报）
   └──> Task 2（读一次的规则构造器 + not measurable）
            └──> Task 3（唯一一次读的命令 + expected_periods）
                     └──> Task 4（§9 / §5.2 / 运行手册 / docstring）
                              └──> Task 5（运营：真实读数，等用户的「开始」）
```

- **串行 1 → 2 → 3 → 4**：Task 2 导入 Task 1 的常量；Task 3 用 Task 2 的构造器与新字段；Task 4 记录的正是前三件已经存在的东西。Task 1 与 Task 2 都改「同一条规则的两半」，分开提交是为了让评审能单独拒绝前向那一半。
- **Task 5 不是实现任务**：它是用户的操作步骤，前置条件与请求预算写在任务里；没有用户的明确「开始」不执行，不替用户跑。
- 每个任务一个提交（Task 3 可拆成「`expected_periods` 与核对」与「命令」两个提交）；评审者可单独拒绝。

---

## Task 1：前向协议的停止规则——采纳值、预登记块与周报陈述

**Issue:** `V2-P7-006`、`V2-P7-024`。**依据:** 用户 2026-11-07 的采纳值（`.superpowers/sdd/batch-3/common.md`）；`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §9 的 2026-11-02 `V2-P7-018` 行（「功效块的形状、默认值与 `006a` 的义务」：工具默认**不是**冻结协议值）；`docs/superpowers/plans/2026-11-01-p7-route-adjustment.md:110-113`（K=36、接受「可能测不出」、定中期无效性、把「测不出」当正式结论）；`src/openalpha_cn/strategy_survivor_forward.py:370-375`（两个 settings 键与两个 schema）、`:451-511`（协议模型，`extra="forbid"`、无默认值）、`:601-622`（功效块）、`:625-692`（`read_forward_power` 与对协议的核对）、`:1754-1825`（文档构造）、`:2110-2139`（`power` 段）、`:2404-2435`（打印）、`:1611`（文档 kind `survivor_forward_report/v1`）；`scripts/forward_report.py:517`、`:584`；`tests/unit/scripts/test_forward_report_survivor.py:106-118`（测试协议 `window_periods: 2`）、`:2341-2397`（功效块的报告测试写法）、`:255-269`（`_assert_the_wording_is_in_every_section`：**每个顶层段都要有 `wording: SURVIVOR_WORDING`**）。

**Files:**
- Modify: `src/openalpha_cn/strategy_survivor_forward.py`、`scripts/forward_report.py`
- Test: `tests/unit/test_strategy_survivor_forward.py`、`tests/unit/scripts/test_forward_report_survivor.py`

**Interfaces（consumes / produces；名字与类型不得改）:**

```python
# src/openalpha_cn/strategy_survivor_forward.py
ADOPTED_PRIMARY_HORIZON: Final = 20
ADOPTED_PRIMARY_METRIC: Final[ForwardMetric] = "rank_ic"
ADOPTED_STOP_MEAN_AT_OR_BELOW: Final = 0.02
ADOPTED_STOP_TAIL_METRIC: Final[ForwardMetric] = "ew_minus_worst_decile"
ADOPTED_STOP_TAIL_MEAN_AT_OR_BELOW: Final = 0.0
ADOPTED_WINDOW_PERIODS: Final = 36
ADOPTED_INTERIM_PERIODS: Final = 18
ADOPTED_INTERIM_MEAN_AT_OR_BELOW: Final = 0.0

SURVIVOR_FORWARD_STOP_RULE_KEY: Final = "survivor_forward_stop_rule"
SURVIVOR_FORWARD_STOP_RULE_SCHEMA: Final = "survivor-forward-stop-rule/v1"
STOP_RULE_NOT_PRE_REGISTERED: Final[str]
FORWARD_STOP_RULE_NOT_APPLIED: Final[str]


class SurvivorForwardStopRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["survivor-forward-stop-rule/v1"]
    metric: ForwardMetric
    horizon: _Horizon
    mean_at_or_below: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    tail_metric: ForwardMetric
    tail_mean_at_or_below: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    interim_periods: Annotated[int, Field(strict=True, ge=1)]
    interim_mean_at_or_below: Annotated[float, Field(strict=True, allow_inf_nan=False)]


def adopted_forward_stop_rule() -> dict[str, Any]: ...
def read_forward_stop_rule(
    settings: Mapping[str, object], *, protocol: SurvivorForwardProtocol
) -> SurvivorForwardStopRule | None: ...
def _hold_the_stop_rule_to_the_protocol(
    rule: SurvivorForwardStopRule, protocol: SurvivorForwardProtocol
) -> None: ...


def survivor_forward_document(
    ..., *, power, stop_rule: SurvivorForwardStopRule | None, integrity
) -> dict[str, Any]: ...   # 新关键字参数，必需（与 `power` 对称）
```

- **为什么是「块」而不是协议里的字段**：协议模型是 `extra="forbid"` + `Literal["survivor-forward-protocol/v1"]`（`:451-467`），功效块已经按「与协议并列、不塞进去」的理由落在 `settings["survivor_forward_power"]`（模块 docstring `:139-143`）。停止规则同理：它是**决策规则**，不是「测什么、什么时候测」。`scripts/p7_smoke_env.py:155-164` 的占位协议（`window_periods: 2`）因此**保持可读**——冒烟登记不是协议，`read_forward_stop_rule` 在缺块时返回 `None`（与 `read_forward_power` 同一形状，`:639-640`）。
- **核对（`_hold_the_stop_rule_to_the_protocol`）**：`metric` 必须是协议的 `primary_metric`；`horizon` 必须是 `primary_horizon`；`tail_metric` 必须与 `metric` 不同；`1 <= interim_periods < protocol.window_periods`。K 只有一份（协议里的 `window_periods`），块里不重抄。

### Step 1.1 — 写失败的测试（`tests/unit/test_strategy_survivor_forward.py`）

在口径段（`PROTOCOL_BLOCK` 之后，约 `:200`）加一块测试值，并在导入表（`:101-140`）加入 `SURVIVOR_FORWARD_STOP_RULE_KEY`、`STOP_RULE_NOT_PRE_REGISTERED`、`SurvivorForwardStopRule`、`adopted_forward_stop_rule`、`read_forward_stop_rule`：

```python
ADOPTED_STOP: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-stop-rule/v1",
    "metric": "rank_ic",
    "horizon": 20,
    "mean_at_or_below": 0.02,
    "tail_metric": "ew_minus_worst_decile",
    "tail_mean_at_or_below": 0.0,
    "interim_periods": 18,
    "interim_mean_at_or_below": 0.0,
}
"""The adopted block as the 006 registration will write it (test copy; the constants are the
source, `adopted_forward_stop_rule()`)."""


def adopted_protocol() -> SurvivorForwardProtocol:
    return read_forward_protocol(
        {SURVIVOR_FORWARD_PROTOCOL_KEY: {**PROTOCOL_BLOCK, "window_periods": 36}}
    )


def test_the_adopted_stop_rule_is_one_constant_per_number() -> None:
    assert forward_module.ADOPTED_PRIMARY_HORIZON == parse_horizon(SURVIVOR_HORIZON).sessions
    assert forward_module.ADOPTED_PRIMARY_HORIZON == 20
    assert forward_module.ADOPTED_PRIMARY_METRIC == "rank_ic"
    assert forward_module.ADOPTED_STOP_MEAN_AT_OR_BELOW == 0.02
    assert forward_module.ADOPTED_STOP_TAIL_METRIC == "ew_minus_worst_decile"
    assert forward_module.ADOPTED_STOP_TAIL_MEAN_AT_OR_BELOW == 0.0
    assert forward_module.ADOPTED_WINDOW_PERIODS == 36
    assert forward_module.ADOPTED_INTERIM_PERIODS == 18
    assert forward_module.ADOPTED_INTERIM_PERIODS * 2 == forward_module.ADOPTED_WINDOW_PERIODS
    assert forward_module.ADOPTED_INTERIM_MEAN_AT_OR_BELOW == 0.0


def test_the_adopted_block_reads_back_as_the_constants() -> None:
    rule = read_forward_stop_rule(
        {SURVIVOR_FORWARD_STOP_RULE_KEY: adopted_forward_stop_rule()},
        protocol=adopted_protocol(),
    )

    assert rule == SurvivorForwardStopRule.model_validate(ADOPTED_STOP)
    assert adopted_forward_stop_rule() == ADOPTED_STOP
    assert (rule.metric, rule.horizon, rule.mean_at_or_below) == ("rank_ic", 20, 0.02)
    assert (rule.tail_metric, rule.tail_mean_at_or_below) == ("ew_minus_worst_decile", 0.0)
    assert (rule.interim_periods, rule.interim_mean_at_or_below) == (18, 0.0)


def test_a_registration_without_the_forward_stop_rule_block_says_so() -> None:
    """The smoke registration and every P6 registration carry no block: a missing one is `None`,
    never a default the code invents."""
    assert read_forward_stop_rule({}, protocol=adopted_protocol()) is None


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"metric": "survivors_minus_ew"}, "primary_metric"),
        ({"horizon": 5}, "primary_horizon"),
        ({"interim_periods": 36}, "interim"),
        ({"interim_periods": 0}, "interim_periods"),
        ({"mean_at_or_below": True}, "mean_at_or_below"),
        ({"tail_metric": "rank_ic"}, "tail_metric"),
        ({"schema_version": "survivor-forward-stop-rule/v2"}, "v1"),
        ({"anchor_ordinal": 1}, "anchor_ordinal"),
    ],
)
def test_a_forward_stop_rule_the_protocol_does_not_carry_is_refused(
    changes: dict[str, Any], said: str
) -> None:
    with pytest.raises(SurvivorForwardError, match=said):
        read_forward_stop_rule(
            {SURVIVOR_FORWARD_STOP_RULE_KEY: {**ADOPTED_STOP, **changes}},
            protocol=adopted_protocol(),
        )
```

文档段与打印行（`reported(...)` 是既有助手，`:1902-1939`；给它加 `stop_rule: SurvivorForwardStopRule | None = None` 参数并传给 `survivor_forward_document`）：

```python
def test_the_document_states_the_forward_stop_rule_and_applies_nothing() -> None:
    read = protocol()  # the test protocol: h20 primary, window_periods 3
    rule = SurvivorForwardStopRule(
        schema_version="survivor-forward-stop-rule/v1",
        metric=read.primary_metric,
        horizon=read.primary_horizon,
        mean_at_or_below=0.02,
        tail_metric="ew_minus_worst_decile",
        tail_mean_at_or_below=0.0,
        interim_periods=1,
        interim_mean_at_or_below=0.0,
    )
    outcomes = every_session(25)

    document = reported({20: outcomes, 5: ()}, checked_by(outcomes), through=25, stop_rule=rule)

    section = document["stop_rule"]
    assert section["wording"] == SURVIVOR_WORDING
    assert section["status"].startswith("pre-registered")
    assert section["rule"]["interim_periods"] == 1
    assert section["interim"] == {
        "periods": 1,
        "window_periods": read.window_periods,
        "positions_matured": 1,
        "reached": True,
    }
    assert section["applied"] is False and section["statement"] == FORWARD_STOP_RULE_NOT_APPLIED
    lines = survivor_forward_lines(document)
    assert any(line.startswith("stop rule") and "interim look after 1" in line for line in lines)
    assert "positions matured at the interim look: 1 of 1" in "\n".join(lines)
    assert not verdict_words(document)


def test_a_document_without_a_forward_stop_rule_states_that_none_was_registered() -> None:
    outcomes = every_session(25)

    document = reported({20: outcomes, 5: ()}, checked_by(outcomes), through=25)

    assert document["stop_rule"]["status"] == STOP_RULE_NOT_PRE_REGISTERED
    assert document["stop_rule"]["rule"] is None
    assert STOP_RULE_NOT_PRE_REGISTERED in "\n".join(survivor_forward_lines(document))
```

周报脚本层（`tests/unit/scripts/test_forward_report_survivor.py`，照 `:2341-2397` 的写法）：

```python
STOP_RULE: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-stop-rule/v1",
    "metric": "rank_ic",
    "horizon": 20,
    "mean_at_or_below": 0.02,
    "tail_metric": "ew_minus_worst_decile",
    "tail_mean_at_or_below": 0.0,
    "interim_periods": 1,
    "interim_mean_at_or_below": 0.0,
}
"""Test values only; the protocol here has window_periods 2, so the interim look is at 1."""


def test_the_report_states_that_no_forward_stop_rule_was_registered(
    world: tds.World, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _report(world, capsys)

    assert code == 0, err
    assert _document(world)["stop_rule"]["status"] == forward.STOP_RULE_NOT_PRE_REGISTERED
    assert forward.STOP_RULE_NOT_PRE_REGISTERED in out
    _no_verdict_word(out)


def test_the_report_reads_the_forward_stop_rule_from_the_registrations_settings(
    world: tds.World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = forward.read_forward_stop_rule

    def registered(
        settings: Mapping[str, Any], *, protocol: forward.SurvivorForwardProtocol
    ) -> Any:
        return real({**settings, forward.SURVIVOR_FORWARD_STOP_RULE_KEY: STOP_RULE}, protocol=protocol)

    monkeypatch.setattr(forward, "read_forward_stop_rule", registered)

    code, out, err = _report(world, capsys)

    assert code == 0, err
    document = _document(world)
    assert document["stop_rule"]["rule"]["interim_periods"] == 1
    assert any(line.startswith("stop rule") for line in out.splitlines())
    _no_verdict_word(out, json.dumps(document))
    _assert_the_wording_is_in_every_section(document)
```

### Step 1.2 — 运行，确认失败

- `PYTHONPATH="$PWD/src" uv run pytest tests/unit/test_strategy_survivor_forward.py tests/unit/scripts/test_forward_report_survivor.py -q -k "stop_rule or adopted"`
- 期望：`AttributeError: module ... has no attribute 'ADOPTED_PRIMARY_HORIZON'`（以及 `survivor_forward_document() got an unexpected keyword argument 'stop_rule'`），收集阶段或首个测试即红。

### Step 1.3 — 实现

`src/openalpha_cn/strategy_survivor_forward.py`：

1. 常量放在 `SURVIVOR_FORWARD_POWER_SCHEMA`（`:375`）之后，每个带一行 docstring 说明它是用户 2026-11-07 的采纳值、**不是**代码给登记的默认值（登记仍必须自己写这个块）：

```python
ADOPTED_PRIMARY_HORIZON: Final = 20
"""The adopted primary horizon (the user, 2026-11-07): every survivor record declares `20d`
(`SURVIVOR_HORIZON`), and the protocol's `primary_horizon` must be that."""

ADOPTED_PRIMARY_METRIC: Final[ForwardMetric] = "rank_ic"
ADOPTED_STOP_MEAN_AT_OR_BELOW: Final = 0.02
"""The adopted stop: the primary metric's mean at or below this is "no evidence"."""
ADOPTED_STOP_TAIL_METRIC: Final[ForwardMetric] = "ew_minus_worst_decile"
ADOPTED_STOP_TAIL_MEAN_AT_OR_BELOW: Final = 0.0
ADOPTED_WINDOW_PERIODS: Final = 36
"""The adopted forward window (K) at the primary horizon: 36 non-overlapping h20 periods."""
ADOPTED_INTERIM_PERIODS: Final = 18
"""The adopted interim futility look: K / 2 cumulative positions."""
ADOPTED_INTERIM_MEAN_AT_OR_BELOW: Final = 0.0
"""The adopted interim stop: the cumulative mean at or below this."""
```

2. 三个键/文案与模型（放在功效块模型 `:601` 之前或之后，两者并列）：

```python
SURVIVOR_FORWARD_STOP_RULE_KEY: Final = "survivor_forward_stop_rule"
"""The key of the registration's `settings` that holds the forward stop rule."""
SURVIVOR_FORWARD_STOP_RULE_SCHEMA: Final = "survivor-forward-stop-rule/v1"
STOP_RULE_NOT_PRE_REGISTERED: Final = (
    "the registration carries no survivor_forward_stop_rule block: no stop rule was written "
    "before the forward clock, and none is stated here"
)
FORWARD_STOP_RULE_NOT_APPLIED: Final = (
    "the stop rule is stated here and applied by the forward clock, not by a report: no interim "
    "or final reading of it is taken from this document"
)
```

```python
class SurvivorForwardStopRule(BaseModel):
    """The forward clock's stop rule, written before the clock; see the module docstring. No
    field has a default. The window's K is the protocol's `window_periods`, not repeated here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["survivor-forward-stop-rule/v1"]
    metric: ForwardMetric
    horizon: _Horizon
    mean_at_or_below: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    tail_metric: ForwardMetric
    tail_mean_at_or_below: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    interim_periods: Annotated[int, Field(strict=True, ge=1)]
    interim_mean_at_or_below: Annotated[float, Field(strict=True, allow_inf_nan=False)]
```

3. `adopted_forward_stop_rule()`：返回上面 `ADOPTED_STOP` 那个 dict（字段顺序不重要，`model_dump` 会规范化），docstring 写明「`006b` 的登记写这个函数的结果；本函数不是代码默认值」。

4. `read_forward_stop_rule` 与 `_hold_the_stop_rule_to_the_protocol`：照 `read_forward_power`（`:625-692`）的形状——缺键返回 `None`；非对象、校验失败、与协议不符都抛 `SurvivorForwardError` 并按名点出字段。核对文案：

```python
def _hold_the_stop_rule_to_the_protocol(rule, protocol) -> None:
    if rule.metric != protocol.primary_metric:
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_STOP_RULE_KEY} names {rule.metric}, and the "
            f"protocol's primary_metric is {protocol.primary_metric}: the stop rule is written "
            "for the primary test"
        )
    if rule.horizon != protocol.primary_horizon:
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_STOP_RULE_KEY} names horizon {rule.horizon}, "
            f"and the protocol's primary_horizon is {protocol.primary_horizon}"
        )
    if rule.tail_metric == rule.metric:
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_STOP_RULE_KEY} uses {rule.metric} as its "
            "tail_metric too: a second clause on the same metric is not a second condition"
        )
    if not 1 <= rule.interim_periods < protocol.window_periods:
        raise SurvivorForwardError(
            f"the registration's {SURVIVOR_FORWARD_STOP_RULE_KEY} has interim_periods "
            f"{rule.interim_periods} of the protocol's window_periods {protocol.window_periods}: "
            "the interim look must fall inside the window and before its end"
        )
```

5. 文档：`survivor_forward_document` 加必需的 `stop_rule` 关键字参数（与 `power` 并列），文档加 `"stop_rule": _stop_rule_section(stop_rule, protocol=protocol, analysis=analysis)`；`_stop_rule_section` 返回 `{"wording": SURVIVOR_WORDING, "status", "rule", "window_periods", "interim", "applied": False, "statement": FORWARD_STOP_RULE_NOT_APPLIED}`（`applied` 永远是 `False`——本报告不判读它）。`survivor_forward_lines` 在 `_power_lines`（`:2428`）之后加 `_stop_rule_lines(document["stop_rule"])`。

6. `scripts/forward_report.py`：`:517` 之后加 `stop_rule = forward.read_forward_stop_rule(admitted.settings, protocol=protocol)`；`:584` 的调用加 `stop_rule=stop_rule,`。两处各一行。

7. 模块 docstring 加一节「The forward stop rule (`survivor_forward_stop_rule`)」，写明三件事：它是**并列的块**；它由用户 2026-11-07 采纳、`006b` 写进登记；本报告只陈述、不判读（周报在窗口满足前不印 p 值也不印结论词，见 `:13-15`、`:168-172`）。

### Step 1.4 — 运行，确认通过

- `PYTHONPATH="$PWD/src" uv run pytest tests/unit/test_strategy_survivor_forward.py tests/unit/scripts/test_forward_report_survivor.py -q`
- 期望：两个文件全绿（原有文档测试因 `stop_rule=None` 走「没登记」分支，`_assert_the_wording_is_in_every_section` 仍绿）。
- `uv run ruff check src/openalpha_cn/strategy_survivor_forward.py scripts/forward_report.py` 与 `uv run mypy src scripts` 通过。

### Step 1.5 — 变异探针（必做，恢复后 `cmp`）

1. `ADOPTED_INTERIM_PERIODS` 改 19：`test_the_adopted_stop_rule_is_one_constant_per_number` 与 `test_the_adopted_block_reads_back_as_the_constants` 红（`18*2 == 36` 断言先死）。
2. 去掉 `_hold_the_stop_rule_to_the_protocol` 的 `interim_periods < window_periods` 一句：`interim_periods: 36` 那条参数化红。
3. 把 `tail_metric == metric` 的核对删掉：`{"tail_metric": "rank_ic"}` 那条红（模型本身不禁止同值）。
4. `survivor_forward_document` 里不给 `stop_rule` 参数默认 `None`（`stop_rule: ... = None`）：**全部测试仍绿**——记录这条「默认值不可分辨」的探针，随后把签名改回必需（理由：与 `power` 对称，缺块必须由调用者显式说「没有」）。
5. `_stop_rule_lines` 把 `rule['mean_at_or_below']:g` 换成常量 0.02：`test_the_document_states_...` 在把测试块阈值改成 0.03 的副本上红；若不红，说明打印行没有读文档（改这条测试加一行 `mean_at_or_below` 之外的值）。

### Step 1.6 — 提交

`feat(V2-P7-006,V2-P7-024): the forward clock's stop rule is a pre-registered block, and the report states it without applying it`

---

## Task 2：读一次的停止规则——共用常量、构造器、`not measurable`

**Issue:** `V2-P7-024`。**依据:** `scripts/research/p7_reading.py:360-380`（`StopRule`/`StopClause`，无默认值）、`:825-852`（`_rule_form`/`_rule_of`）、`:858-917`（`evaluate_stop_rule`：今天「不可读」→ `triggered`）、`:252-259`（两段措辞）、`:595-602` 与 `:646-657`（「could only ever read 'triggered: unreadable'」两处文案）、`:23-30`（模块 docstring 同一句）、`:1561-1614`（结果文本）；`tests/unit/scripts/test_p7_reading.py:79-90`（`RULE` 已经是 0.02 / 0.0 的形状）、`:315-329`（参数化的三行结论）、`:409-430`（数据拒绝 ⇒ 今天判 `triggered`）、`:1385`（`StopRule` 的字段测试）。

**Files:**
- Modify: `scripts/research/p7_reading.py`（`adopted_stop_rule`、`NOT_MEASURABLE_WORDING`、`evaluate_stop_rule`、两处文案、模块 docstring）
- Test: `tests/unit/scripts/test_p7_reading.py`

**Interfaces（consumes / produces）:**

```python
# scripts/research/p7_reading.py
NOT_MEASURABLE_WORDING: Final[str]
"""The third conclusion; never the 'no evidence' wording."""


def adopted_stop_rule(*, score: str, expected_periods: int) -> StopRule: ...
    # horizon = strategy_survivor_forward.ADOPTED_PRIMARY_HORIZON
    # minimum_periods = expected_periods  (R1's recommendation)
    # any_of = (StopClause(ADOPTED_PRIMARY_METRIC, ADOPTED_STOP_MEAN_AT_OR_BELOW),
    #           StopClause(ADOPTED_STOP_TAIL_METRIC, ADOPTED_STOP_TAIL_MEAN_AT_OR_BELOW))
```

- `evaluate_stop_rule` 的 `outcome` 取值变成三个：`"triggered"`、`"not triggered"`、`"not measurable"`。`reason` 的既有前缀 `"unreadable: "` **保留**（它说的是那几个指标读不出来），变的只是 `outcome` 与 `wording`。
- **为什么必须变**：今天 `evaluate_stop_rule` 把「读不出来」写成 `triggered`，而 `STOP_TRIGGERED_WORDING`（`:252-255`）说「the forward clock is not started, and the user is told there is no evidence」。用户 2026-11-07 的决定是「不可测是它自己的正式结论，绝不读成没有证据」。**这不是措辞问题**：`triggered` 会一路进 `result.json`、`result.txt` 与 G-0 的判读。
- 操作默认仍是失败即关闭：`NOT_MEASURABLE_WORDING` 里要写明「这个读数不能被读成任何方向」，**不**说「这是没有证据」（那四个词只以否定形式出现：「this is not a reading of no evidence」），也**不**说「可以继续」；R2 是用户要确认的那一句。

### Step 2.1 — 写失败的测试（`tests/unit/scripts/test_p7_reading.py`）

导入表加 `from openalpha_cn import strategy_survivor_forward as forward_module`；文件末尾（或 `test_a_stop_rule_has_a_score_...` 附近）加：

```python
def test_the_adopted_stop_rule_is_the_users_numbers_and_nothing_else() -> None:
    rule = p7_reading.adopted_stop_rule(score="composite", expected_periods=24)

    assert (rule.score, rule.horizon, rule.minimum_periods) == ("composite", 20, 24)
    assert [(clause.metric, clause.mean_at_or_below) for clause in rule.any_of] == [
        ("rank_ic", 0.02),
        ("ew_minus_worst_decile", 0.0),
    ]


def test_the_reading_and_the_forward_protocol_share_every_number() -> None:
    """One named constant per number, used by both records: the reading's rule and the forward
    clock's block cannot drift apart."""
    rule = p7_reading.adopted_stop_rule(score="composite", expected_periods=24)

    assert (rule.horizon, rule.any_of[0].metric, rule.any_of[0].mean_at_or_below) == (
        forward_module.ADOPTED_PRIMARY_HORIZON,
        forward_module.ADOPTED_PRIMARY_METRIC,
        forward_module.ADOPTED_STOP_MEAN_AT_OR_BELOW,
    )
    assert (rule.any_of[1].metric, rule.any_of[1].mean_at_or_below) == (
        forward_module.ADOPTED_STOP_TAIL_METRIC,
        forward_module.ADOPTED_STOP_TAIL_MEAN_AT_OR_BELOW,
    )


def test_a_rule_that_cannot_be_read_is_not_measurable_and_not_no_evidence() -> None:
    evaluated = p7_reading.evaluate_stop_rule(RULE, document(n=2))

    assert evaluated["outcome"] == "not measurable"
    assert evaluated["reason"].startswith("unreadable")
    assert evaluated["wording"] == p7_reading.NOT_MEASURABLE_WORDING
    assert p7_reading.NOT_MEASURABLE_WORDING != p7_reading.STOP_TRIGGERED_WORDING
    assert "no evidence" in p7_reading.NOT_MEASURABLE_WORDING
    assert "not a reading of no evidence" in p7_reading.NOT_MEASURABLE_WORDING
```

改写两处既有断言（**这是更正，不是放宽**：旧断言钉的正是用户 2026-11-07 否掉的那条折叠）：

- `:315-322` 的参数化行 `(document(n=2), "triggered", "unreadable")` → `(document(n=2), "not measurable", "unreadable")`。
- `:423-428`（`test_a_data_refusal_is_the_reads_recorded_error_and_closes_the_gate`）：
  ```python
    assert result["stop_rule"]["outcome"] == "not measurable"
    assert result["stop_rule"]["reason"].startswith("unreadable")
    ...
    assert result["error"] in text and p7_reading.NOT_MEASURABLE_WORDING in text
    assert p7_reading.STOP_TRIGGERED_WORDING not in text
  ```

登记体写入的正是这些值（`expected_periods` 是 Task 3 的字段；本测试只钉停止规则块）：

```python
def test_the_registration_writes_the_adopted_stop_rule(repo: Path) -> None:
    path = repo / "docs" / "research" / "p7-readings" / "v2-p7-024-validation.registration.json"
    p7_reading.register_reading(
        path=path,
        reading="v2-p7-024-validation",
        issue="V2-P7-024",
        role="primary",
        window="validation",
        request=REQUEST,
        config=CONFIG,
        stop_rule=p7_reading.adopted_stop_rule(score="composite", expected_periods=3),
        expected_periods=3,
        code_commit=head(repo),
        as_of_reason=AS_OF_REASON,
    )

    body = json.loads(path.read_text(encoding="utf-8"))

    assert body["stop_rule"] == {
        "schema": p7_reading.STOP_RULE_SCHEMA,
        "score": "composite",
        "horizon": 20,
        "minimum_periods": 3,
        "any_of": [
            {"metric": "rank_ic", "mean_at_or_below": 0.02},
            {"metric": "ew_minus_worst_decile", "mean_at_or_below": 0.0},
        ],
    }
    assert body["expected_periods"] == 3
```

（这条测试在 Task 3 之前会因 `expected_periods` 关键字而红——正确：Task 2 与 Task 3 同一批次、串行；若实现者先做 Task 2，把该测试留到 Task 3 的提交里，其余 Task 2 测试不受影响。）

### Step 2.2 — 运行，确认失败

- `PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_reading.py -q -k "adopted or not_measurable or applied_as_written or data_refusal"`
- 期望：`AttributeError: module 'p7_reading' has no attribute 'adopted_stop_rule'`；参数化那条与数据拒绝那条报 `assert 'triggered' == 'not measurable'`。

### Step 2.3 — 实现

1. `p7_reading.py` 的 `openalpha_cn.strategy_survivor_forward` 导入表（`:241`）加五个常量（`ADOPTED_PRIMARY_HORIZON`、`ADOPTED_PRIMARY_METRIC`、`ADOPTED_STOP_MEAN_AT_OR_BELOW`、`ADOPTED_STOP_TAIL_MEAN_AT_OR_BELOW`、`ADOPTED_STOP_TAIL_METRIC`）。
2. 在 `NOT_TRIGGERED_WORDING`（`:256-259`）之后加：

```python
NOT_MEASURABLE_WORDING: Final[str] = (
    "the reading cannot be measured: the stop rule decided nothing, and this is not a reading of "
    "no evidence -- it is its own conclusion, and what follows is the user's to say"
)
```

3. `evaluate_stop_rule`（`:858-917`）：`unreadable` 分支的 `outcome` 改为 `"not measurable"`，`wording` 按三种结论取（一个 dict 查表，缺键即 bug）。
4. `adopted_stop_rule` 放在 `StopRule` 之后、`_rule_form` 之前：

```python
def adopted_stop_rule(*, score: str, expected_periods: int) -> StopRule:
    """The user's adopted stop rule (2026-11-07), for the validation reading: the score's h20
    rank IC and its worst-decile spread, at the numbers the forward protocol also uses
    (`strategy_survivor_forward.ADOPTED_*`), with `minimum_periods` the exact count the plan
    expects (`expected_periods`). No number is written here twice."""
    return StopRule(
        score=score,
        horizon=ADOPTED_PRIMARY_HORIZON,
        minimum_periods=expected_periods,
        any_of=(
            StopClause(
                metric=ADOPTED_PRIMARY_METRIC, mean_at_or_below=ADOPTED_STOP_MEAN_AT_OR_BELOW
            ),
            StopClause(
                metric=ADOPTED_STOP_TAIL_METRIC,
                mean_at_or_below=ADOPTED_STOP_TAIL_MEAN_AT_OR_BELOW,
            ),
        ),
    )
```

5. 两处 `'triggered: unreadable'` 文案（`:601`、`:656`）与模块 docstring（`:29-30`）改成 `'not measurable'`。
6. 模块 docstring 第 8 条（`:98-100`）补一句：「一个读不出来的指标给出 `not measurable`——这是它自己的结论；它意味着这个读数不能被读成任何方向，**不**意味着没有证据」。

### Step 2.4 — 运行，确认通过

- `PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_reading.py -q -k "adopted or not_measurable or applied_as_written or data_refusal or registration_writes"`
- 期望：全绿（`test_the_registration_writes_the_adopted_stop_rule` 除外——它要 Task 3 的 `expected_periods`，见 Step 2.1 末）。
- `uv run mypy src scripts` 与 `uv run ruff check .` 通过。

### Step 2.5 — 变异探针（必做，恢复后 `cmp`）

1. `NOT_MEASURABLE_WORDING = STOP_TRIGGERED_WORDING`：`test_a_rule_that_cannot_be_read_is_not_measurable_and_not_no_evidence` 与结果文本断言同时红。
2. `evaluate_stop_rule` 的 `outcome` 改回 `"triggered"`：参数化行与数据拒绝那条红。
3. `adopted_stop_rule` 把 `0.02` 直接写成字面量（不从常量取）：`test_the_reading_and_the_forward_protocol_share_every_number` 仍绿（值相同）——记录这条「值相同不可分辨」的探针；换一个探针：把常量导入改成 `ADOPTED_PRIMARY_HORIZON + 0`，值仍相同，同样绿。真正的探针是**同时**把常量与构造器改到 0.03 与 0.021，两组测试同时红（证明它们读的是同一处）；随后恢复并 `cmp`。
4. `adopted_stop_rule` 的 `minimum_periods=expected_periods` 改成 `= 18`：`test_the_adopted_stop_rule_is_the_users_numbers_and_nothing_else` 的 `("composite", 20, 24)` 红。

### Step 2.6 — 提交

`feat(V2-P7-024): the reading's stop rule is built from the adopted constants, and a rule it cannot read is "not measurable", not "no evidence"`

---

## Task 3：那唯一一次读的命令——登记、先验期数、只读一次的路径

**Issue:** `V2-P7-024`。**依据:** `scripts/research/p7_reading.py:421-520`（`register_reading`：整窗口、认领前的一切检查、`minimum_periods` 的两道上界）、`:531-603`（`_registration_of`，登记体的事前规则）、`:958-1002`（`_panel_preflight`，认领前的默认预检）、`:1084-1187`（`read_once` 的顺序与拒绝）、`:1269-1336`（浅克隆 `:1282`、grafts `:1290`）、`:1339-1384`（`second_use` 顺序）、`:621-643`（`_exact_most_periods`）；`scripts/research/p7_windows.py:136-169`（运行目录）、`:425`（`capped_panel_store`）、`:469`（`plan_signal_days`）；`tests/unit/scripts/test_p7_reading.py:92-160`（`repo`/`registered` 两个助手）、`:1210-1270`（`calendar_panel` 夹具与精确上界测试）、`:829-835`、`:926-935`（浅克隆/grafts）、`:1867-1903`（`second_use` 顺序）；路线图 `V2-P7-004` 行（`:486`）：「是库函数 `register_reading` / `read_once`，没有命令行入口」。

**Files:**
- Modify: `scripts/research/p7_reading.py`（`expected_periods` 字段与校验、`counted_periods`、`expected_periods_problem`、`_panel_preflight` 的核对、模块 docstring）
- Create: `scripts/research/p7_read.py`
- Test: `tests/unit/scripts/test_p7_reading.py`（`expected_periods` 与预检）、`tests/unit/scripts/test_p7_read.py`（新）

**Interfaces（consumes / produces）:**

```python
# scripts/research/p7_reading.py
# `_BODY_KEYS`（:289-302）加 "expected_periods"

class ExpectedPeriodsMismatch(ReadingError):
    """The panel's calendar does not hold the number of periods the plan expects."""


def counted_periods(
    runtime_dir: Path,
    *,
    window: DiagnosticWindow,
    request: Mapping[str, Any],
    config: Mapping[str, Any],
    horizon: int,
) -> int: ...


def expected_periods_problem(
    expected: int | None, most: int, *, window: DiagnosticWindow, horizon: int
) -> str | None: ...


def register_reading(..., stop_rule: StopRule | None, expected_periods: int | None = None, ...) -> str: ...


# scripts/research/p7_read.py
def main(argv: Sequence[str] | None = None) -> int: ...   # 0 / 2 / 3 / 4
```

- **`expected_periods` 是登记体的字段**（与 `as_of_reason` 同一类：手写准入 `_BODY_KEYS`，不是硬规则 3 的契约行；今天是第一个格式，`READING_SCHEMA` 仍是 `.../v1`，见 `:454-462` 的同一论证）。一份 **primary** 登记必须带它；**secondary** 必须不带（`None`）。
- **三道核对，一处算术**：`_exact_most_periods`（`:621-643`）是唯一算期数的地方；`register_reading`（给了 `runtime_dir` 时）、`_panel_preflight`（认领前）与命令的 `read` 前检查都调它或 `counted_periods`（同一实现的公开壳）。
- **退出码**：0 完成（登记写出 / 结果写出，判定照常打印）；2 用法或 `ReadingError`（登记写不成、参数读不通）；3 认领之前就拒（运行目录、登记未提交/字节不符、代码变了、外来包/脚本、已认领、`second_use` 在验证窗口之前、浅克隆/grafts、预检问题）；4 **日历的精确期数不等于登记的先验数**——停、报告，什么都不写、不认领。

### Step 3.1 — 写失败的测试

**(a) `expected_periods` 的登记规则**（`tests/unit/scripts/test_p7_reading.py`；两个助手 `registered`/`register` 的默认参数加 `expected_periods=3`）：

```python
@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"expected_periods": None}, "expected_periods"),
        ({"expected_periods": 2}, "minimum_periods"),
        ({"expected_periods": 0}, "expected_periods"),
        ({"role": "secondary", "rule": None, "window": "second_use",
          "request": SECOND_USE_REQUEST, "expected_periods": 3}, "expected_periods"),
    ],
)
def test_a_primary_registration_records_the_period_count_the_plan_expects(
    repo: Path, changes: dict[str, Any], said: str
) -> None:
    """A primary reading records the exact count it expects; a rule that could never be read at
    that count (minimum above it) is refused; a secondary has no count."""
    with pytest.raises(p7_reading.ReadingError, match=said):
        registered(repo, commit=False, **changes)
```

**(b) 认领前的期数核对**（用 `calendar_panel` 夹具；它的日历是 2021–2023 每个工作日，精确 25）：

```python
def test_the_preflight_refuses_a_count_that_is_not_the_one_the_registration_expects(
    repo: Path, calendar_panel: Path
) -> None:
    registration = registered(repo, expected_periods=24)
    body = json.loads(registration.read_text(encoding="utf-8"))

    problems = p7_reading._panel_preflight(calendar_panel, p7_windows.VALIDATION)(body)

    assert problems and "exactly 25" in problems[0] and "expects 24" in problems[0]
    assert not claim_of(registration).exists()
```

（这条在 `_panel_preflight` 里排在数据集预检之后，所以还要 `monkeypatch.setattr(p7_reading, "preflight_problems", lambda *a, **k: ())`，照 `:1287-1290` 的写法。）

**(c) 命令**（新文件 `tests/unit/scripts/test_p7_read.py`）：与 `test_p7_reading.py` 同样的 `_research_module` 导入法，并复用它的 `repo`/`registered` 思路；日历夹具直接 import `_calendar_batch` 自写一份（30 行，见 `:1210-1243`）：

```python
def _command(*argv: str) -> int:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module("p7_read").main(list(argv))
```

```python
def test_the_command_registers_the_adopted_rule_and_the_expected_count(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _command(
        "register",
        "--repo", str(repo),
        "--runtime-dir", str(calendar_panel),
        "--window", "validation",
        "--as-of", "2026-10-09T00:00:00+08:00",
        "--as-of-reason", AS_OF_REASON,
        "--expected-periods", "25",
        "--stride", "20",
        "--horizons", "20",
        "--score", "composite",
        "--transform", "cross_section_standard/v1",
    )

    assert code == 0, capsys.readouterr().err
    path = repo / "docs" / "research" / "p7-readings" / "v2-p7-024-validation.registration.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["expected_periods"] == 25
    assert body["stop_rule"]["horizon"] == 20
    assert body["stop_rule"]["minimum_periods"] == 25
    assert [c["metric"] for c in body["stop_rule"]["any_of"]] == [
        "rank_ic", "ew_minus_worst_decile"
    ]
    assert "exactly 25" in capsys.readouterr().out


def test_the_command_stops_when_the_calendar_does_not_hold_the_expected_periods(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The plan's prior is 24 (484 real sessions); this fixture's calendar makes 25. A number
    that is not the prior stops the procedure: nothing is written, nothing is claimed, and the
    operator is told to report it -- never to edit 24 to match."""
    code = _command(
        "register", "--repo", str(repo), "--runtime-dir", str(calendar_panel),
        "--window", "validation", "--as-of", "2026-10-09T00:00:00+08:00",
        "--as-of-reason", AS_OF_REASON, "--expected-periods", "24",
        "--stride", "20", "--horizons", "20", "--score", "composite",
        "--transform", "cross_section_standard/v1",
    )

    said = capsys.readouterr()
    assert code == 4
    assert "exactly 25" in said.err and "expects 24" in said.err
    assert "stop and report" in said.err
    assert not (repo / "docs" / "research" / "p7-readings").exists()


def test_the_command_reads_the_validation_window_once_and_writes_the_verdict(
    repo: Path, calendar_panel: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _command("register", ... "--expected-periods", "25", ...) == 0
    registration = repo / "docs" / "research" / "p7-readings" / "v2-p7-024-validation.registration.json"
    commit_file(repo, registration, "the registration", at=COMMITTED)

    measure = Measure()  # test_p7_reading.Measure's stand-in, imported or copied
    monkeypatch.setattr(p7_reading, "_panel_measure", lambda runtime_dir: measure)
    monkeypatch.setattr(p7_reading, "preflight_problems", lambda *a, **k: ())

    code = _command(
        "read", "--repo", str(repo), "--runtime-dir", str(calendar_panel),
        "--registration", str(registration),
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "PRE-REGISTERED READ-ONCE" in out and "stop rule not triggered" in out
    assert registration.with_name("v2-p7-024-validation.result.json").exists()
    assert registration.with_name("validation.window-claim.json").exists()
    assert _command("read", "--repo", str(repo), "--runtime-dir", str(calendar_panel),
                    "--registration", str(registration)) == 3


def test_the_command_refuses_second_use_before_the_validation_claim_is_committed(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _command(
        "register", "--repo", str(repo), "--runtime-dir", str(calendar_panel),
        "--window", "second_use", "--reading", "v2-p7-024-second-use",
        "--as-of", "2026-10-09T00:00:00+08:00", "--as-of-reason", AS_OF_REASON,
        "--stride", "20", "--horizons", "20", "--score", "composite",
        "--transform", "cross_section_standard/v1",
    )
    assert code == 0
    registration = repo / "docs" / "research" / "p7-readings" / "v2-p7-024-second-use.registration.json"
    commit_file(repo, registration, "the registration", at=COMMITTED)

    assert _command("read", "--repo", str(repo), "--runtime-dir", str(calendar_panel),
                    "--registration", str(registration)) == 3
    assert "validation window" in capsys.readouterr().err


def test_the_command_refuses_the_research_window_by_name(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _command("register", "--repo", str(repo), "--runtime-dir", str(calendar_panel),
                    "--window", "research", ...) == 2


def test_the_command_surfaces_a_shallow_repository_as_a_refusal(
    repo: Path, calendar_panel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _command("register", ... "--expected-periods", "25", ...) == 0
    registration = ...
    commit_file(repo, registration, "the registration", at=COMMITTED)
    (repo / ".git" / "shallow").write_text(head(repo) + "\n", encoding="utf-8")

    assert _command("read", "--repo", str(repo), "--runtime-dir", str(calendar_panel),
                    "--registration", str(registration)) == 3
    assert "git fetch --unshallow" in capsys.readouterr().err
```

### Step 3.2 — 运行，确认失败

- `PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_reading.py tests/unit/scripts/test_p7_read.py -q -k "expected_periods or expects or command"`
- 期望：`ModuleNotFoundError: No module named 'p7_read'`；`_registration_of` 不认识 `expected_periods`（`ReadingError: the registration carries ['expected_periods'], which the read does not use`）。

### Step 3.3 — 实现

1. `p7_reading.py`：`_BODY_KEYS` 加 `"expected_periods"`；`_registration_of` 在停止规则之后加（顺序让「primary 没带」先于「secondary 带了」报出来）：

```python
    expected = body.get("expected_periods")
    if role == "primary":
        if type(expected) is not int or expected < 1:
            raise ReadingError(
                "a primary reading records expected_periods, the exact number of non-overlapping "
                f"periods the plan expects this window's calendar to hold; got {expected!r}"
            )
        if expected < rule.minimum_periods:
            raise ReadingError(
                f"the stop rule's minimum_periods {rule.minimum_periods} is more than "
                f"expected_periods {expected}: the rule could never be read at the count the "
                "plan expects"
            )
        explore = settings.explore
        most = _most_periods(
            explore.first, explore.last, stride=explore.stride, horizon=rule.horizon
        )
        if expected > most:
            raise ReadingError(
                f"expected_periods {expected} is more than the {window.name} window's weekdays "
                f"can hold ({most} at stride {explore.stride} and h={rule.horizon})"
            )
    elif expected is not None:
        raise ReadingError(
            "a secondary reading carries no expected_periods: it has no stop rule and no count "
            f"to check; got {expected!r}"
        )
```

（放在 `:580-602` 那段「规则在请求里」的检查之后。`_Registration` **不加字段**：`register_reading` 与 `_panel_preflight` 都从已经过 `_registration_of` 校验的 `body` 读 `body.get("expected_periods")`。）

2. `ExpectedPeriodsMismatch(ReadingError)` 与两个函数：

```python
def counted_periods(
    runtime_dir: Path, *, window: DiagnosticWindow, request: Mapping[str, Any],
    config: Mapping[str, Any], horizon: int,
) -> int:
    """The exact non-overlapping periods the panel's own calendar can summarise for this window,
    read before anything is claimed (the calendar is not outcome data)."""
    checked_runtime_directory(runtime_dir)
    settings = _settings_of(request, config, window)
    store = capped_panel_store(runtime_dir, window)
    return _exact_most_periods(store, window=window, settings=settings, horizon=horizon)


def expected_periods_problem(
    expected: int | None, most: int, *, window: DiagnosticWindow, horizon: int
) -> str | None:
    if expected is None or expected == most:
        return None
    return (
        f"the panel's calendar makes exactly {most} non-overlapping periods over the "
        f"{window.name} window {window.first}..{window.last} at h={horizon}, and the "
        f"registration expects {expected}: the count is not what the plan expects -- stop and "
        "report it; nothing was claimed and the window was not read"
    )
```

3. `register_reading(..., expected_periods: int | None = None, ...)`：写进 `body`；给了 `runtime_dir` 时，在 `minimum_periods` 上界检查之后：

```python
            expected = body.get("expected_periods")
            if registered.stop_rule is not None:
                problem = expected_periods_problem(
                    expected,
                    most,
                    window=registered.window,
                    horizon=registered.stop_rule.horizon,
                )
                if problem is not None:
                    raise ExpectedPeriodsMismatch(problem)
```

（`most` 与 `registered` 都是 `:495-517` 那段里已有的局部名。）

4. `_panel_preflight`：在停止规则的可达性检查（`:998-1000`）之后，用同一个 `most` 与 `body.get("expected_periods")` 调 `expected_periods_problem`，非空就返回 `(problem,)`。这样**任何**调用 `read_once` 的路径（包括直接调用库函数）都在认领之前被同一句话挡住。

5. `scripts/research/p7_read.py`（新，模块 docstring 说明「这是那唯一一次读的命令；库路径仍是 `p7_reading.read_once`」）：

```python
EXIT_OK: Final = 0
EXIT_USAGE: Final = 2
EXIT_REFUSED: Final = 3
EXIT_PERIODS: Final = 4


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return _register(arguments) if arguments.command == "register" else _read(arguments)
    except p7_reading.ExpectedPeriodsMismatch as error:
        return _refuse(arguments.command, error, EXIT_PERIODS)
    except p7_reading.ReadingError as error:
        return _refuse(arguments.command, error, EXIT_USAGE)
    except (
        registry.HoldoutRefusedError,
        p7_windows.DiagnosticWindowError,
        TradingCalendarError,
        PanelStorageError,
    ) as error:
        return _refuse(arguments.command, error, EXIT_REFUSED)
    except ValueError as error:
        # A date, a book or a transform the arguments cannot parse; `ReadingError`,
        # `ExpectedPeriodsMismatch`, `DiagnosticWindowError` and `TradingCalendarError` are
        # ValueErrors too and are caught above, in that order.
        return _refuse(arguments.command, error, EXIT_USAGE)
```

`_register`：

- `window = p7_windows.WINDOWS[arguments.window]`；`role = "primary" if window.name == "validation" else "secondary"`；
- `book = SurvivorBookConfig.model_validate(json.loads(arguments.book))`；`request` 的 `first`/`last` **取自窗口**（命令没有 `--first/--last`：一个读数覆盖整个窗口，`p7_reading.py:19-21`），`loser_decile_count` 取 `book` 的；
- `stop_rule = p7_reading.adopted_stop_rule(score=arguments.score, expected_periods=...)`（primary）或 `None`；
- `code_commit`：`registry._git(arguments.repo, "rev-parse", "HEAD")` 一次，写出前解析（`register_reading` 的 docstring `:445-446`）；
- `path = arguments.repo / p7_reading.READING_DIRECTORY / f"{arguments.reading}.registration.json"`；`--reading` 缺省 `f"v2-p7-024-{window.name.replace('_', '-')}"`；`--issue` 缺省 `V2-P7-024`；
- `--dry-run`：先 `most = p7_reading.counted_periods(...)`，`problem = expected_periods_problem(...)`；有 problem 抛 `ExpectedPeriodsMismatch`（退出码 4）；否则打印 `window / exactly N / data-free bound / expected N / the registration that would be written`，**不写文件**；
- 正式登记：调 `register_reading(..., runtime_dir=arguments.runtime_dir, expected_periods=...)`，打印路径、sha256，以及「commit it before the read; the read refuses bytes not in HEAD」。

`_read`：

```python
    body = json.loads(arguments.registration.read_text(encoding="utf-8"))
    window = p7_windows.WINDOWS[body["window"]]
    rule = body.get("stop_rule")
    if rule is not None:
        most = p7_reading.counted_periods(
            arguments.runtime_dir, window=window, request=body["request"],
            config=body["config"], horizon=rule["horizon"],
        )
        problem = p7_reading.expected_periods_problem(
            body.get("expected_periods"), most, window=window, horizon=rule["horizon"]
        )
        if problem is not None:
            raise p7_reading.ExpectedPeriodsMismatch(problem)
    result = p7_reading.read_once(
        arguments.registration, repo=arguments.repo, runtime_dir=arguments.runtime_dir
    )
    print("\n".join(p7_reading.reading_lines(result, body)))
    return EXIT_OK
```

- 先做期数核对（读日历、不读结果数据、不认领），再进 `read_once` 的全部拒绝；`read_once` 的预检里还有同一句话作纵深防御。**命令的核对在任何认领之前**，所以「停、报告」不会花掉窗口。

6. 模块 docstring（`p7_reading.py`）补：`expected_periods` 是什么、三道核对、`ExpectedPeriodsMismatch` 的语义、命令的位置与退出码；`read_once` 的编号拒绝列表（`:32-101`）不需要新条目（期数不一致发生在第 6 条预检里，写成预检问题）。

### Step 3.4 — 运行，确认通过

- `PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_reading.py tests/unit/scripts/test_p7_read.py -q`
- 期望：全绿；`test_the_command_stops_when_...` 退出 4 且 `p7-readings/` 不存在；`test_the_command_reads_...` 的第二次是 3；`second_use` 那条 3 且 stderr 含 "validation window"。
- `uv run mypy src scripts`、`uv run ruff check .`、`uv run ruff format --check .` 通过。

### Step 3.5 — 变异探针（必做，恢复后 `cmp`）

1. 删掉 `_read` 里的期数核对：`test_the_command_stops_...` 从 4 变成 0 或 3（红）。
2. `EXIT_PERIODS` 改成 3：该测试红（退出码是操作员的信号）。
3. `register` 在 mismatch 时仍写文件：该测试的 `not p7-readings/ 存在` 红。
4. `_registration_of` 的 `expected < minimum_periods` 检查删掉：参数化 `{"expected_periods": 2}` 红。
5. `counted_periods` 把 `horizon` 换成 `request["stride"]`：`exactly 25` 的算术变（夹具上 stride 20 / h20 恰好相同——记录这条「不可分辨」的探针；换成 `--horizon 5` 的副本让 25 变 5，测试红）。
6. `p7_read` 的 `--window` 选项加 `research` 并接受：`test_the_command_refuses_the_research_window_by_name` 从 2 变成 0/3（红）。

### Step 3.6 — 提交

`feat(V2-P7-024): the one read is a command that records the expected period count, stops on a mismatch, and refuses before any claim`

（若拆两个提交：先 `fix(V2-P7-024): a primary registration records the exact period count the plan expects, checked before the claim`，再上一条。）

---

## Task 4：文档——§9 的两行、§5.2 的一句话、运行手册与模块 docstring

**Issue:** `V2-P7-024`。**依据:** `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md:227-235`（G-0 表）、`:313-372`（§9，改顺序/闸门/路线图必须加行）、§9 的 2026-11-02 「`V2-P7-004`/`024`：对受保护窗口的『读一次』按整个窗口、窗口级认领执行，并写明运行前提」行；`.superpowers/sdd/batch-3/common.md` 的采纳值与「Deliverable」；`docs/superpowers/plans/2026-11-07-p7-dev-batch-2-closeout.md:105-130`（未决清单与运行前提）；`docs/runbooks/forward-report.zh-CN.md:65`（「未启用」的前向周报）；`tests/unit/test_p7_diagnostics.py:91-93`（禁止词）。

**Files:**
- Modify: `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md`（§5.2 的一句、§9 一行）
- Create: `docs/runbooks/p7-reading.zh-CN.md`
- Modify: `scripts/research/p7_reading.py`、`src/openalpha_cn/strategy_survivor_forward.py` 的模块 docstring（若 Task 1/2/3 已写够，本任务只补运行手册需要的那几句）

**Steps:**

- [ ] **Step 4.1 — §5.2 G-0 的一句话。** 在「通过读数前写好的停止规则」之后加：「停止规则有三种结论：触发（不启动前向时钟，如实告诉用户没有证据）、未触发（只允许 `V2-P7-006` 继续，不是证据）、**不可测**（它自己的结论，绝不读成没有证据；不启动前向时钟，下一步由用户说）。数值与常量见 §9 的 2026-11-07 行」。改闸门文字**必须**在同一提交里加 §9 行（§9 开头那句：「不在这里记录的改动视为漂移」）。
- [ ] **Step 4.2 — §9 一行（2026-11-07）。** 记录：用户采纳的停止规则（主统计量 h20 秩 IC；均值 ≤ 0.02 或最差十分位价差均值 ≤ 0 即停；K=36；中期无效性在 K/2=18、累积均值 ≤ 0 即停；主期限 20）；**两个记录不得合并**（读一次的 `StopRule` vs 前向协议的 `survivor-forward-stop-rule/v1` 块），常量在 `src/openalpha_cn/strategy_survivor_forward.py`；「不可测」是第三种正式结论；唯一一次读的命令在 `scripts/research/p7_read.py`，`expected_periods` 在认领前核对，先验 24 而工具自算。依据列：`.superpowers/sdd/batch-3/common.md`、本计划、`p7_reading.py`/`strategy_survivor_forward.py` 的行号。**不写「已通过」「已验证」**——读数本身还没做。
- [ ] **Step 4.3 — 运行手册 `docs/runbooks/p7-reading.zh-CN.md`（新）。** 写成给用户的操作页，至少六节：
  1. **这是什么**：`V2-P7-024` 的唯一一次验证窗口读（`2022-01-04..2023-12-29`），第一次样本外读数、**不是确认性证据**；一次、不可逆；**0 次网络请求**（只读克隆）。
  2. **前提（逐条，带核对命令）**：`git rev-parse --is-shallow-repository` 必须是 `false`（否则 `git fetch --unshallow`）；没有 `.git/info/grafts`（有就 `git replace --convert-graft-file`，替换 ref 被工具忽略）；`HEAD` 就是登记里的 `code_commit`，`src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`、`scripts/daily_selection.py`、`scripts/forward_report.py` 与它一致；面板是 `cp -c` 的克隆，不在 `~/openalpha-research` 里、不在任何检出里；登记覆盖整个窗口、带停止规则、`expected_periods`、一行 `as_of_reason`，且**已提交**、字节与 `HEAD` 相同。
  3. **程序**（每一步一条命令）：`register --dry-run` → 读打印的 `exactly N`；**N 必须等于 24**（484 个会话、步长 20、h20 的算术）；不等于就停下来报告，**不得把 24 改成 N**；`register` → `git add`/`git commit`（登记）→ `read` → `git add`/`commit`（`validation.window-claim.json`、`<reading>.claim.json`、`.result.json`、`.result.txt`）。命令必须与用户批准的那一条逐字一致（请求与配置是用户确认的，见 R4）。
  4. **判读**：退出码 0 = 读完成（判定照常打印）；2 = 用法/登记写不成；3 = 认领之前被拒（什么都没花掉）；4 = 日历期数不是先验数（停、报告）。三种结论的措辞与 G-0 的后果逐条列出（同 Step 4.1）。
  5. **顺序**：验证窗口先读、认领先提交，`second_use` 才可读；二次使用段是次要、非确认性。
  6. **失败即关闭**：浅克隆、grafts、未提交的认领、别的克隆/worktree、删掉的认领文件——每一种都说明为什么窗口仍算已读/为什么读不了（引 `p7_reading.py:106-140` 的「What this does not establish」）。
- [ ] **Step 4.4 — 查一遍没有别处复述旧说法。** `command grep -rn "triggered: unreadable\|not measurable\|停止规则" docs scripts src | cat`——每一处要么是历史记录（§9 的旧行、批次 1/2 的计划与收尾记录，**不改**），要么是本任务改过的；`docs/runbooks/forward-report.zh-CN.md` 与前向块有关的那一节若说「没有停止规则」要指向新块。
- [ ] **Step 4.5 — 禁止词与格式。** 新增文本不得出现 `tests/unit/test_p7_diagnostics.py:91-93` 的禁止词；若本仓的 `ruff format` 覆盖 Markdown 代码块（批次 2 的两份计划曾被 `ruff format` 重排），同一提交里重跑 `uv run ruff format .` 并接受它对本文的排版改动。
- [ ] **Step 4.6 — 提交：** `docs(V2-P7-024): the gate records the adopted stop rule, its three conclusions and the one-read procedure`

**不在本任务**：`docs/HANDOFF_CURRENT.md`、`CHANGELOG.md`、路线图 `V2-P7-024` 行的状态格——由控制者在合入时统一改（沿用批次 2 的约定，`docs/superpowers/plans/2026-11-07-v2-p7-025-registry-new-year.md:1078`）。路线图行在真实读数完成之前**保持打开**。

---

## Task 5（运营，不是实现任务）：那唯一一次读数

> **这是用户的步骤。** 没有用户的明确「开始」不执行；实现者不跑、不代替用户判断、不在真实面板上做任何写操作。**请求预算：0 次网络请求**（`cp -c` 克隆上的纯计算；`trade_cal` 的读取是本地 parquet）。

**前置条件（全部成立才做，缺一条就停）：**

1. 本计划的 Task 1–4 已合入，且合入后的 tip 就是登记要钉的 `code_commit`；工作树干净（受绑定的六个路径与它一致）。
2. 仓库不浅（`git rev-parse --is-shallow-repository` → `false`），没有 grafts（或已用 `git replace --convert-graft-file` 转换）；替换 ref 被忽略，不需要删。
3. 面板克隆：`cp -c ~/openalpha-research/panel <克隆根>`，克隆根**不在** `~/openalpha-research` 里、**不在**任何检出里（`p7_windows.checked_runtime_directory`，`:136-169`）。APFS 卷与源同卷。
4. `register --dry-run` 打印的 `exactly N` **等于 24**。不等：停下来报告（面板日历与计划不一致、或不完整），**不改先验数、不调参、不读**。
5. 用户批准 `register` 的请求与配置（R4）与那一行 `as_of_reason`（为什么这个 `as_of` 是这次读数所处的知识）。
6. 用户的明确「开始」（R3）。

**步骤：**

1. `register --dry-run …`（不写任何文件）→ 核对 N == 24。
2. `register …` → 打印登记路径与 sha256 → 审阅 → 提交（登记必须先在 `HEAD` 里：`read_once` 拒绝「磁盘字节 ≠ `HEAD` 字节」）。
3. `read --repo <repo> --runtime-dir <克隆根> --registration <登记路径>`：
   - 退出 4 → 停、报告（什么都没写、没认领）；
   - 退出 3 → 按消息处理前置条件（浅克隆、运行目录、代码变了、已认领）；
   - 退出 0 → 结果已写出；打印的文本里读停止规则判定。
4. 立刻提交四个文件：`validation.window-claim.json`、`<reading>.claim.json`、`.result.json`、`.result.txt`，并让这个提交留在本仓库的 refs 里（`p7_reading.py:112-126`：不提交的认领可以被删掉、窗口可以被再读）。
5. 把判定原话告诉用户：
   - **触发** → G-0 不过：不启动前向时钟，如实说「没有证据」；
   - **未触发** → 只允许 `V2-P7-006` 继续，**不是证据**，措辞不升级；
   - **不可测** → 它自己的结论：既不是「没有证据」，也不是放行；不启动前向时钟，下一步由用户说（R2）。
6. （可选，R5）二次使用段：仅在步骤 4 的提交存在之后，按同一程序登记、读、提交；它是**次要、非确认性**读数，唯一用途是描述，不参与 G-0。

**不要做**：不在该窗口上调参、不改停止规则、不改 `expected_periods` 去对齐算出来的数、不把这份读写成「确认」、不用它评价组合收益（组合层面的结果多年内都检验不了，规格 §3.6）。

---

## 受绑定代码（Bound code touched）

`scripts/research/registry.py:93-94` 的 `REGISTERED_PATHS = ("src", "scripts/research", "pyproject.toml", "uv.lock")`，`scripts/daily_selection.py` 与 `scripts/forward_report.py` 由 `also_bound` 加绑。**本计划改的可执行字节：**

| 路径 | 任务 | 说明 |
|---|---|---|
| `src/openalpha_cn/strategy_survivor_forward.py` | 1 | 常量、新块模型、读取与核对、文档段与打印行 |
| `scripts/forward_report.py` | 1 | 读一块、传一个参数（2 行） |
| `scripts/research/p7_reading.py` | 2、3 | 构造器、第三种结论、`expected_periods`、`counted_periods`、预检核对 |
| `scripts/research/p7_read.py` | 3 | **新增**，在 `scripts/research/` 下，自动受绑定 |
| `pyproject.toml`、`uv.lock`、`scripts/daily_selection.py` | — | **不动** |

**今天没有任何登记**（`docs/research/p7-readings/` 不存在；每日登记自 2026-10-08 暂停），所以本计划**不使任何绑定作废**。注意两点：①这些改动全部发生在 `006b` 之前，允许；②Task 5 的真实读会把它当时的 `HEAD` 钉成登记的 `code_commit`——**读之后**再改这些路径会让那份登记「过期」，但那次读已经做完（`read_once` 只在读的时候核对代码），不影响已写出的结果。

## 矛盾与发现（Contradictions found）

1. **`evaluate_stop_rule` 把「不可读」折成 `triggered`**（`scripts/research/p7_reading.py:904-916`），而 `STOP_TRIGGERED_WORDING`（`:252-255`）说「the forward clock is not started, and the user is told there is no evidence」。用户 2026-11-07 的决定是「不可测是它自己的正式结论，绝不读成没有证据」——**代码与决定直接冲突**。Task 2 改它；旧断言（`tests/unit/scripts/test_p7_reading.py:320`、`:423-428`）与两处文案（`p7_reading.py:601`、`:656`、`:29-30`）是**更正**，不是放宽。
2. **「K=36」不适用于 2022–2023 的读数**：那个窗口最多 24 个 h20 非重叠期（484 会话 → 25 个信号日、末一个被禁运）。K=36 与中期查看 18 属于**前向协议**；把两者混成一个 `StopRule` 会让历史读数永远「不可测」。本计划把它们做成两个记录、共用常量。
3. **「主期限在代码里写死 20」不准确**（批次 2 收尾 §5 第 4 条）：`SurvivorForwardProtocol.primary_horizon` **没有默认值**（`src/openalpha_cn/strategy_survivor_forward.py:451-458`），20 的见证是记录声明的 `SURVIVOR_HORIZON = "20d"`（`strategy_survivor_registration.py:260`），由 `read_forward_protocol` 强制相等（`:531-538`）。计划按「`ADOPTED_PRIMARY_HORIZON = 20` 且必须等于记录的 `20d`」实现，并在 §9 行里说清。
4. **先验 24 没有离线复核过**：批次 2 §6 第 6 条说批次 1 评审的 24「没有复核」，批次 2 评审按夹具手算的是 25（不同的日历）；**「484 个会话」这个数在仓库里没有任何记录**（`grep -rn "484" docs scripts src tests` 只命中无关数字）。Task 5 因此把「工具自己算」写成硬步骤，且把「不是 24 就停」写成规则。
5. **前向模块 docstring 说「registrations already written under `survivor-forward-protocol/v1` stay readable」（`:139-143`），但仓库里没有任何登记 JSON 带这个块**（`grep -rl survivor_forward_protocol --include="*.json" .` 为空；唯一的协议块是 `scripts/p7_smoke_env.py:155-164` 的冒烟占位，`window_periods: 2`，在一次性克隆里生成）。所以新增一个**并列**块是免费的；同时必须记明：冒烟占位**不是**协议，`window_periods: 2` 不是 K。
6. **`p7_reading` 的读一次路径没有命令行入口**（路线图 `V2-P7-004` 行，`:486`）。本计划新增一个**命令**而不是把子命令塞进 `p7_diagnostics`：后者在模块层导入 `p7_reading`（`:182-206`），反向 import 会成环，只能做函数内惰性导入，命令会与它的诊断子命令混在一起。命令放 `scripts/research/p7_read.py`，库路径仍是 `read_once`。
7. **`minimum_periods` 仍是未决的**（批次 2 收尾 §5 第 3 条），而用户 2026-11-07 的采纳值没有给它。本计划推荐「等于 `expected_periods`」（R1），并把它写成构造器的一条规则，不另造一个数字常量。
8. **`expected_periods` 是登记体的新字段**：`READING_SCHEMA` 仍是 `.../v1`（与 `as_of_reason` 同一论证：今天没有登记，且这个体是 `_BODY_KEYS` + `_registration_of` 手写准入，不是硬规则 3 的契约行，`p7_reading.py:454-462`）。一旦写下第一份登记，**再加字段就要迁移**——这是这批改动必须赶在 Task 5 之前的理由。
9. **`NOT_MEASURABLE_WORDING` 里的「no evidence」是引用，不是结论**：它必须说「this is not a reading of no evidence」，所以文本里**会**出现这四个词。测试因此断言整段措辞常量，而不是「不含 no evidence」——否则会禁掉唯一正确的说法。
