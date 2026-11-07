# P7 批次 2 · `V2-P7-024` 的四个前置（`V2-P7-004` / `V2-P7-024`）

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **状态：草案，等用户审阅。** 本文只是计划；不改源码、不改测试。每个 `file:line` 都是起草者在批次 1 合并后的本地 `main`（`587a158` 的快进结果）上读过的。**本计划不读任何窗口、不发任何网络请求、不写任何登记**：所有测试都用测试时生成的面板与夹具窗口，和批次 1 Task 5 的演练同一做法。验证窗口 `2022-01-04..2023-12-29` 的唯一一次真实读仍是 `V2-P7-024` 本身，是用户的决定和之后的运营步骤；本计划只让工具安全，不做那次读。

**Goal:** 补上批次 1 终审提出的四个前置，保护**唯一一次不可逆的验证窗口读**：`V2-P7-024` 之前，一个登记如果只能读出「不可读」，必须在认领之前被拒绝；一个运行目录如果指向 P6 的研究根或主检出，必须在打开任何存储之前被拒绝；二次使用段不得先于验证窗口被读；`as_of` 必须被登记写明为「在那一刻的知识下的重算」，并在结果文本里说出来。

**Architecture:** 改动全部在既有的研究工具里，不新增模块、不加第二组装根：

- `scripts/research/p7_windows.py`：新增「运行目录不得是 P6 研究根 / 主检出」的两条判定（与冒烟 `PathPolicy` 的两条规则同义，判定函数从 `scripts/p7_smoke_env.py:188-257` 复制并交叉核对），因为 `p7_diagnostics` 与 `p7_reading` 都已经导入它，且它已在受绑定路径里。
- `scripts/research/p7_reading.py`：`minimum_periods` 的**精确**上界（`trade_cal` 经 `WindowCappedStore` 读、`plan_signal_days` 的禁运、`summarize` 的抽样规则）、`second_use` 的顺序检查、`as_of_reason` 字段与结果里的一行。
- `scripts/research/p7_diagnostics.py`：`_open_sources` 一处运行目录检查（`explore` 唯一的存储开口）。
- `tests/`：新增/改写测试；`src/openalpha_cn/strategy_survivor_registration.py` **不改**，只在 `tests/unit/test_strategy_survivor_registration.py` 补两条钉住 `_readable_instant` 回退分支的测试。

**Tech Stack:** Python 3.12、标准库、pydantic v2（本计划不新增 pydantic 用法）、pytest；门禁 `uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy src scripts`、`uv run pytest tests/unit`。面板夹具一律在测试时生成（硬规则 6）。

**Spec / 依据:** `AGENTS.md`（硬规则 1–9、「研究诚实」）；`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §5.2（G-0 的 2026-11-01 改写）、§7.2（窗口）、§9 的 2026-11-02 各行（`V2-P7-004`/`024` 读一次、`019` 退出码）；`docs/superpowers/plans/2026-11-02-p7-dev-batch-1-closeout.md` §5 第 10 条（本计划的四个前置）与 §4 的 D5 延后项；`docs/superpowers/plans/2026-11-01-p7-route-adjustment.md` §3、§4（`006a`/`006b` 与受绑定代码）；路线图 `V2-P7-004`（`docs/specs/v2/openalpha-cn-v2-roadmap.md:484`）、`V2-P7-024`（`:504`）。

---

## Global Constraints

逐字取自规格与代码；每个任务隐含这一节。

- 每个提交、分支写 issue ID：本计划全部用 `V2-P7-004` 与 `V2-P7-024`，提交标题 `fix(V2-P7-004,V2-P7-024): …` / `feat(V2-P7-004,V2-P7-024): …` / `test(V2-P7-008): …`（Task 5）。提交信息末尾：`Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- **窗口**（`scripts/research/p7_windows.py:138-158`）：研究期 `2015-01-05..2021-12-31`（年上限 2021）；验证期 `2022-01-04..2023-12-29`（年上限 2023）；二次使用段 `2024-01-02..2026-09-24`（年上限 2026）。`READ_ONCE_NOTE`（`:87-92`）：2026 整年可读，截日期是读者的责任。
- **只读一次**（`docs/specs/v2/…-p7-decision-chain.md` §9 的 2026-11-02 行）：一个读数覆盖整个窗口；每个窗口只读一次，不论读数叫什么名字；认领必须提交并留在 refs 里；拒绝浅克隆与 grafts 文件；替换 ref 被忽略。
- **登记格式**：`READING_SCHEMA = "openalpha-p7-reading-registration/v1"`（`scripts/research/p7_reading.py:210`）；`READING_DIRECTORY = Path("docs/research/p7-readings")`（`:212`）；`WINDOW_CLAIM_SUFFIX = ".window-claim.json"`（`:215`）。`docs/research/p7-readings/` 今天**不存在**（起草者核对：`ls` 退出 1），所以没有任何读数登记；本计划给登记体加字段不是硬规则 3 的迁移（它不是 `Literal[".../v1"]` + `extra="forbid"` 的契约行，而是 `p7_reading._BODY_KEYS`（`:250-262`）与 `_registration_of`（`:434-496`）手写的准入）。
- **预检数据集**（`p7_reading.py:228-246`）：`YEAR_BEFORE_DATASETS = (trade_cal, daily, adj_factor, suspend_d, namechange, stock_basic)`；`PREFLIGHT_DATASETS` 再加 `stk_limit`；合成分的 `factor_proc_*` 与 `factor_procmn_*` 由 `_preflight_datasets` 追加。
- **精确上界的算术** = `plan_signal_days`（`p7_windows.py:399-457`：`trading_days_between(first, last)[::stride]`，标签出口晚于 `window.last` 的进 `embargoed`）+ `summarize`（`scripts/research/p7_diagnostics.py:592-593`：`every = ceil(horizon / stride)`，`sampled = plan.sessions[::every]`），因此精确期数 = `ceil(len(plan.sessions) / ceil(horizon / stride))`。
- **评审者的数**（收尾 §5 第 10 条①）：验证窗口、步长 20、h20 时数据无关上界 26、真实最大 24。**24 没有离线复核过**（见「矛盾与发现」第 1 条）；工具必须自己从日历算，测试钉夹具上的数，真实数在 `024` 的运营步骤里核对。
- **运行目录的两条规则**（`scripts/p7_smoke_env.py:232-257`）：解析后不得在 `~/openalpha-research`（P6 运行根）里，也不得在主检出里；克隆（如 `~/p7-panel-clone`）**不受**拒绝。冒烟的空白/临时目录两条规则**不**搬过来（它们服务「冒烟要跑几周」，与读一次无关）。
- **`as_of` 是知识时钟**（`p7_windows.py:44-49`）：可以远晚于窗口，年上限封的是经济日期；登记必须说 `as_of` 与理由，结果文本必须写明「在那一刻的知识下的重算，不是按每个会话发布时刻的回放」。
- **不联网、不读真实面板、不发请求**（评审守卫 `tests/conftest.py`）：所有面板在测试时生成；`.parquet/.duckdb/.sqlite3/.db` 不提交（`scripts/verify_publication.py`）。
- **措辞**：一切仍是「候选」；新增文本不得出现 `significant`、`effective`、`confirmed`、`alpha`、`outperform`、`passed`、`failed`、`proven`（`tests/unit/test_p7_diagnostics.py:92-101` 的禁止词表；`reading_lines` 的文本不在 `verdict_words` 扫描里，但仍照同一口径写）。
- **受登记绑定的路径**：`src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`、`scripts/daily_selection.py`、`scripts/forward_report.py`。本计划改 `scripts/research/` 里的三个文件且只改测试与 `src/` 之外的测试文件，全部发生在 `006b` 之前，允许；**今天没有任何登记**，所以没有绑定被作废。见「受绑定代码」一节。
- 测试卫生（记忆）：worktree 里跑测试设 `PYTHONPATH=<worktree>/src`；~~每次提交前串行跑一次 `tests/unit`~~（**corrected 2026-11-07：这一条在本批次被划掉**——实现者只跑定向文件，整个 `tests/unit` 由控制者在批次末尾串行跑一次；批次 1 的七个并发全量把 40 分钟变成数小时）；变异探针用 `PYTHONDONTWRITEBYTECODE=1`，改前 `cp` 到草稿目录、改完恢复并 `cmp` 确认逐字节一致；绝不在 `git --exec`/hook 里跑测试。

## Review Focus

最可能伤到使用者（= 花掉唯一一次读）的五类输入或失败模式，每条由所在任务里的测试钉住：

1. **一个只能读出「不可读」的停止规则通过了登记，认领之后才发现**：`minimum_periods` 等于数据无关上界（真实窗口的 26）而不是日历上界（真实窗口 24；夹具窗口上界 8、精确 6/4）。→ Task 2 `test_a_minimum_above_what_the_panels_calendar_allows_is_refused_at_registration`、`test_the_preflight_refuses_a_minimum_the_windows_embargo_makes_unreachable`。
2. **一次运行开在 P6 的面板根目录或主检出上**：只读连接仍建 `catalog.duckdb.lock`/`.gate`，读一次还在整个计算期间持共享锁。→ Task 1 `test_a_runtime_directory_on_the_research_root_or_the_main_checkout_is_refused`、`test_a_clone_under_the_home_directory_is_not_refused`（克隆不能被误拒）、`test_read_once_refuses_a_runtime_directory_inside_the_main_checkout_before_any_claim`。
3. **先读二次使用段，再回去写验证窗口的停止规则数值**：2024–2026 的结果影响 2022–2023 的判定。→ Task 3 `test_a_second_use_read_before_the_validation_windows_claim_is_refused`、`test_a_validation_window_claim_on_disk_but_not_committed_does_not_open_second_use`、`test_a_committed_validation_window_claim_opens_second_use`。
4. **把 `as_of` 读成「按每个会话当时的发布时刻回放」**：窗口之后才公布的修订与迟到更名进入窗口内会话的打分，而文本没有说。→ Task 4 `test_the_result_says_which_knowledge_clock_the_reading_is_under`、`test_a_registration_says_why_its_as_of_is_the_knowledge_it_reads`。
5. **检查本身失败即开放**：日历不可读、年份缺失、面板不存在时，精确上界检查必须**拒绝**（登记时）或**写出问题**（认领前），而不是跳过。→ Task 2 `test_a_registration_whose_calendar_cannot_be_read_writes_nothing`、`test_the_preflight_refuses_when_the_calendar_cannot_be_read`。

## 用户决定（Decisions for the user）

本计划按推荐写；每条都标了它挡住哪个任务。**本计划不选停止规则的数值**（指标、阈值、`minimum_periods`）；那是 `V2-P7-024` 的（收尾 §5 第 4 条）。

| # | 决定 | 推荐 | 代价 / 挡住 |
|---|---|---|---|
| U1 | `register_reading` 的 `runtime_dir` 是**必填**还是**可选**（给了就做精确上界检查，不给只做数据无关上界；硬拒绝始终在 `read_once` 的认领前预检里） | **可选**（本文写法）：`read_once` 在认领之前重新施加登记的每条规则（`p7_reading.py:12-14`、`:933-938`），窗口不会因登记跳过检查而被花掉；必填会让 54 处测试调用点都要造一个小日历面板 | 选必填：Task 2 的签名与 `tests/unit/scripts/test_p7_reading.py` 的 `registered()` 全部调用点（约 54 处）加夹具参数 |
| U2 | 运行目录的**拒绝规则**是否就是冒烟的两条（研究根、主检出），不搬空白/临时目录两条 | **是**：读一次不跑几周，临时目录不是风险；克隆在任何别处都允许（Task 7 与真实读都跑克隆） | 选更多规则：Task 1 的判定表加行，可能误拒用户放的克隆 |
| U3 | 哪个产物证明「验证窗口已被读」：**已提交**的 `validation.window-claim.json`（任何 ref/reflog 可达），还是读数自己的 `*.claim.json`，还是结果文件 | **窗口认领**：`read_once` 在读数自己的认领之前无条件写它（`p7_reading.py:968-988`），它按窗口命名、不受读数名影响；要求**已提交**，否则删掉未提交的窗口认领就能在二次使用段之后重读验证窗口。结果文件不可靠（崩溃时没有结果，窗口也已花掉） | 选结果文件：Task 3 的检查改看 `.result.json`，并把「崩溃的验证读数会挡住二次使用」写进运行前提 |
| U4 | `as_of` 的理由是**登记体的新字段** `as_of_reason` 加结果文本一行，还是只有结果文本一行；`READING_SCHEMA` 是否升到 `.../v2` | **加字段 + 一行，保持 `.../v1`**：没有登记存在，`v1` 就是第一个格式；它不是硬规则 3 的契约行（见 Global Constraints） | 只加一行：理由不在登记里，审查者无法核对它写在读之前；升 v2：`test_a_hand_written_registration…`（`tests/unit/scripts/test_p7_reading.py:1225-1245`）的期望串要改 |
| U5 | `p7_diagnostics._open_sources`（`:962-994`）与 `p7_reading._panel_measure`（`:833-902`）的近似重复是否本批合并 | **不合并，留着并写明**：两者的差别正是安全点——探索路径在运行之后取哈希，读一次路径必须在 `store.reading()` 里、与读一起取；抽一个共用构造器会让以后的调用者误用「运行后取哈希」。收尾 §4 D5 已记为延后项 | 合并：`_panel_measure` 的字节行为（文档的 `partitions_read`）必须逐字节不变，演练文件（`tests/unit/test_p7_reading_rehearsal.py`）要重跑 |
| U6 | 是否补钉 `_readable_instant` 的两个回退分支（`readable is None`、`readable <= filed`） | **是**（纯测试，不改 `src/`） | 不做：收尾 §4 的延后项继续挂着，代价为零 |

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `scripts/research/p7_windows.py`（改） | `RESEARCH_RUNTIME`、`MAIN_CHECKOUT`、`RuntimeDirectoryRefusedError`、`_within`、`checked_runtime_directory` | 1 |
| `scripts/research/p7_diagnostics.py`（改） | `_open_sources` 第一行调用运行目录检查 | 1 |
| `scripts/research/p7_reading.py`（改） | 运行目录检查（`read_once`/`_panel_measure`/`_panel_preflight`/`register_reading`）；精确上界；`second_use` 顺序；`as_of_reason` 与结果行 | 1、2、3、4 |
| `tests/unit/scripts/test_p7_windows.py`（改） | 运行目录判定的新测试 + 与冒烟 `PathPolicy` 的交叉核对 | 1 |
| `tests/unit/test_p7_diagnostics.py`（改） | `explore` 对研究根的拒绝（退出码 2、什么都不建） | 1 |
| `tests/unit/scripts/test_p7_reading.py`（改） | 读一次的运行目录拒绝；精确上界（登记）；`second_use` 顺序；`as_of_reason` 与结果行 | 1、2、3、4 |
| `tests/unit/test_p7_reading_rehearsal.py`（改） | 真实 `_panel_preflight` 在生成的面板上拒绝一个禁运使其不可达的 `minimum_periods` | 2 |
| `tests/unit/test_strategy_survivor_registration.py`（改，只加测试） | `_readable_instant` 的两个回退分支 | 5 |

## 依赖与并行

```
Task 1（运行目录防护，item 2）──> Task 2（精确上界，item 1）
Task 1 ─────────────────────────> Task 3（second_use 顺序，item 3）
Task 2 ─────────────────────────> Task 4（as_of 知识时钟，item 4）
Task 5（_readable_instant 补钉，item 5）  与以上全部无关
```

- **顺序**：Task 2 与 Task 4 都改 `register_reading` 的签名与 `_Registration`，必须串行（2 → 4）；Task 3 与 Task 2 都改 `read_once` 与 `test_p7_reading.py`，建议 3 在 2 之后（否则挑拣会冲突，冲突很小但要人工解）；Task 3 与 Task 4 无逻辑依赖。
- **可并行**：Task 5 与任何任务并行（只动一个测试文件）；Task 1 与 Task 3 的代码无交集，但都动 `p7_reading.py` 的开头，隔离 worktree 并行后挑拣会出现上下文冲突——本仓库历史上这样做过（收尾 §3），代价由控制者承担；**推荐串行 1 → 2 → 3 → 4，Task 5 随时**。
- 每个任务一个提交（Task 1 可拆成 `p7_windows` 判定与三处接线两个提交），评审者可单独拒绝。

---

## Task 1：运行目录不得是 P6 的研究根或主检出（item 2）

**Issue:** `V2-P7-004`、`V2-P7-024`。**依据:** 收尾 §5 第 10 条②；`scripts/research/p7_diagnostics.py:962-994`（`_open_sources`，`explore` 唯一的存储开口，`_explore` 在 `:1059` 调用它）、`scripts/research/p7_reading.py:833-902`（`_panel_measure`）、`:812-827`（`_panel_preflight`）、`:908-1008`（`read_once`）；`src/openalpha_cn/panel/store.py:1309-1364`（`_open_lock_file` 用 `O_RDWR | O_CREAT` 打开 `.lock`/`.gate`——只读连接也会建文件）、`:2206-2222`（`reading()` 的 docstring：「Hold it across reads, not across computation」；读一次却跨了整个计算）；冒烟的规则 `scripts/p7_smoke_env.py:188-257`。

**Files:**
- Modify: `scripts/research/p7_windows.py`（新常量与判定，导入 `os`、`Path` 已有）、`scripts/research/p7_diagnostics.py`（`_open_sources` 第一行、导入名）、`scripts/research/p7_reading.py`（`read_once` 第一条语句；`_panel_measure` 与 `_panel_preflight` 的第一条语句）
- Test: `tests/unit/scripts/test_p7_windows.py`、`tests/unit/test_p7_diagnostics.py`、`tests/unit/scripts/test_p7_reading.py`

**Interfaces（consumes / produces；名字与类型不得改）:**

```python
# scripts/research/p7_windows.py
RESEARCH_RUNTIME: Final[Path] = (Path.home() / "openalpha-research").resolve()
MAIN_CHECKOUT: Final[Path] = Path(__file__).resolve().parents[2]  # scripts/research/ -> 仓库根


class RuntimeDirectoryRefusedError(DiagnosticWindowError):
    """A runtime directory a P7 diagnostic may not open: P6's research root or a checkout."""


def _within(path: Path, root: Path) -> bool: ...  # 从 p7_smoke_env.py:188-205 复制


def checked_runtime_directory(
    runtime_dir: Path,
    *,
    research_runtime: Path | None = None,
    main_checkout: Path | None = None,
) -> Path: ...
```

- `None` 默认值**在函数体内**解析成 `RESEARCH_RUNTIME` / `MAIN_CHECKOUT`，这样测试可以 `monkeypatch.setattr(p7_windows, "RESEARCH_RUNTIME", …)`。
- 抛出的是 `DiagnosticWindowError` 的子类：`p7_diagnostics._explore` 已在 `:1090-1091` 捕获 `DiagnosticWindowError` 并返回 2；`p7_reading` 不把它折成数据缺口（`_measured` 只把 `DAY_REFUSALS` 记录成错误，`DiagnosticWindowError` 原样传播，`:1310-1322`）。

### Step 1.1 — 写失败的测试（`tests/unit/scripts/test_p7_windows.py`）

在文件末尾追加（该文件已有 `importlib`、`sys`、`Path`、`pytest`、`ROOT`；`ROOT = Path(__file__).resolve().parents[3]`）：

```python
def _smoke_env() -> ModuleType:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    return importlib.import_module("p7_smoke_env")


@pytest.mark.parametrize("where", ["research", "checkout"])
def test_a_runtime_directory_on_the_research_root_or_the_main_checkout_is_refused(
    tmp_path: Path, where: str
) -> None:
    """A read-only connection still creates catalog.duckdb.lock/.gate beside the catalogue
    (panel/store.py:1309-1364), and a read-once measurement holds the catalogue's shared lock
    for its whole computation (store.py:2206-2222): neither root may be opened in place."""
    home = tmp_path / "home"
    research = home / "openalpha-research"
    checkout = tmp_path / "checkout"
    (research / "panel").mkdir(parents=True)
    (checkout / "runtime").mkdir(parents=True)
    runtime = research / "panel" if where == "research" else checkout / "runtime"

    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError) as raised:
        p7_windows.checked_runtime_directory(
            runtime, research_runtime=research, main_checkout=checkout
        )

    said = str(raised.value)
    assert str(runtime.resolve()) in said
    assert ("openalpha-research" if where == "research" else "main checkout") in said
    assert list((research / "panel").iterdir()) == []


def test_a_clone_under_the_home_directory_is_not_refused(tmp_path: Path) -> None:
    """Task 7 and the real read both run on a cp -c clone; a clone is neither P6's root nor a
    checkout, and a name that merely starts with the research root's is another directory."""
    home = tmp_path / "home"
    research = home / "openalpha-research"
    checkout = tmp_path / "checkout"
    (research / "panel").mkdir(parents=True)
    (checkout / "runtime").mkdir(parents=True)
    clone = home / "p7-panel-clone"
    (clone / "panel").mkdir(parents=True)

    assert (
        p7_windows.checked_runtime_directory(
            clone / "panel", research_runtime=research, main_checkout=checkout
        )
        == (clone / "panel").resolve()
    )
    assert (
        p7_windows.checked_runtime_directory(
            home / "openalpha-research-clone", research_runtime=research, main_checkout=checkout
        )
        == (home / "openalpha-research-clone").resolve()
    )


def test_a_runtime_directory_spelled_in_another_case_on_a_case_blind_volume_is_refused(
    tmp_path: Path,
) -> None:
    """APFS ignores case by default and `resolve()` keeps the case it is given: a path spelled
    `OpenAlpha-Research` is inside `openalpha-research` (the smoke's rule, copied)."""
    research = tmp_path / "home" / "openalpha-research"
    (research / "panel").mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    spelled = tmp_path / "home" / "OpenAlpha-Research"
    if not spelled.exists():
        pytest.skip("this volume tells case apart: another case names another directory")

    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError, match="openalpha-research"):
        p7_windows.checked_runtime_directory(
            spelled / "panel", research_runtime=research, main_checkout=checkout
        )


def test_the_two_refusals_are_the_smokes_path_policy(tmp_path: Path) -> None:
    """The rules are the smoke's, not a second copy of them: the same three paths are accepted
    and refused by both, with the smoke's whitespace and temporary-directory rules left out."""
    env = _smoke_env()
    home = tmp_path / "home"
    research = home / "openalpha-research"
    checkout = tmp_path / "checkout"
    (research / "panel").mkdir(parents=True)
    (checkout / "runtime").mkdir(parents=True)
    clone = home / "clone"
    clone.mkdir()
    policy = env.PathPolicy(
        temporary_roots=(),
        main_checkout=checkout.resolve(),
        research_runtime=research.resolve(),
    )

    for where in (research / "panel", checkout / "runtime", clone):
        try:
            policy.checked("runtime directory", where)
            smoke_refused = False
        except env.SmokeEnvironmentError:
            smoke_refused = True
        try:
            p7_windows.checked_runtime_directory(
                where, research_runtime=research, main_checkout=checkout
            )
            mine_refused = False
        except p7_windows.RuntimeDirectoryRefusedError:
            mine_refused = True
        assert mine_refused == smoke_refused, where
```

以及在 `tests/unit/test_p7_diagnostics.py` 末尾追加（该文件已导入 `p7_windows` 与 `p7`，`EXPLORE` 常量在 `:1000-1008`，`explore_2017` 在 `:1070` 起）：

```python
def test_explore_refuses_the_research_root_before_opening_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    research = tmp_path / "openalpha-research"
    (research / "panel").mkdir(parents=True)
    monkeypatch.setattr(p7_windows, "RESEARCH_RUNTIME", research.resolve())

    code = p7.main(
        [
            *EXPLORE,
            "--runtime-dir",
            str(research / "panel"),
            "--first",
            "2017-01-03",
            "--last",
            "2017-12-29",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert code == 2
    assert str((research / "panel").resolve()) in capsys.readouterr().err
    assert list((research / "panel").iterdir()) == []
    assert not (tmp_path / "out").exists()
```

以及在 `tests/unit/scripts/test_p7_reading.py` 追加（该文件已有 `p7_windows` 于 `:333`、`run()` 于 `:166`、`claim_of` 于 `:340`、`window_claim_of` 在测试文件里已有）：

```python
def test_read_once_refuses_a_runtime_directory_inside_the_main_checkout_before_any_claim(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registration = registered(repo)
    measure = Measure()
    monkeypatch.setattr(p7_windows, "MAIN_CHECKOUT", repo.resolve())

    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError, match="main checkout"):
        run(registration, repo, measure)

    assert measure.calls == []
    assert not claim_of(registration).exists()
    assert not window_claim_of(repo).exists()


def test_the_default_measure_and_preflight_refuse_a_directory_inside_the_main_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p7_windows, "MAIN_CHECKOUT", repo.resolve())

    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError, match="main checkout"):
        p7_reading._panel_measure(repo / "runtime")
    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError, match="main checkout"):
        p7_reading._panel_preflight(repo / "runtime", p7_windows.VALIDATION)
```

- 运行：`PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_windows.py tests/unit/test_p7_diagnostics.py tests/unit/scripts/test_p7_reading.py -q`
- 期望失败：`AttributeError: module 'p7_windows' has no attribute 'RuntimeDirectoryRefusedError'`（前四处），以及 `explore` 那两个测试先失败在 `AssertionError`/`Failed: DID NOT RAISE`。

### Step 1.2 — 实现

`scripts/research/p7_windows.py`：在 `READ_ONCE_NOTE`（`:87-92`）之后、`DiagnosticWindowError`（`:97`）之前或之后加（顺序不影响），并给文件加 `import os`：

```python
RESEARCH_RUNTIME: Final[Path] = (Path.home() / "openalpha-research").resolve()
"""P6's run directory: its `panel` is the store a P7 diagnostic must never open in place."""

MAIN_CHECKOUT: Final[Path] = Path(__file__).resolve().parents[2]
"""The checkout this module runs from -- `daily_selection.REPOSITORY`'s rule, read off this
file: scripts/research/p7_windows.py -> scripts/ -> the repository root."""


class RuntimeDirectoryRefusedError(DiagnosticWindowError):
    """A runtime directory a P7 diagnostic may not open: P6's research root or a checkout."""


def _within(path: Path, root: Path) -> bool:
    """Whether `path` is `root` or under it, by spelling and, for an existing `root`, by identity
    (`scripts/p7_smoke_env._within`, copied: APFS ignores case, and `Path.resolve()` keeps the
    case it is given)."""
    if any(path == base or base in path.parents for base in {root, root.resolve()}):
        return True
    try:
        identity = os.stat(root)
    except OSError:
        return False
    for ancestor in (path, *path.parents):
        try:
            if os.path.samestat(os.stat(ancestor), identity):
                return True
        except OSError:
            continue
    return False


def checked_runtime_directory(
    runtime_dir: Path,
    *,
    research_runtime: Path | None = None,
    main_checkout: Path | None = None,
) -> Path:
    """`runtime_dir` resolved, or `RuntimeDirectoryRefusedError` naming the rule and the path.

    A read-only connection still creates `catalog.duckdb.lock` and `catalog.duckdb.gate` beside
    the catalogue (`panel/store.py:1309-1364` opens both `O_RDWR | O_CREAT`), and a read-once
    measurement holds the catalogue's shared side for its whole computation
    (`panel/store.py:2206-2222`). The panel a diagnostic opens is therefore a clone (`cp -c`),
    never P6's research root and never a checkout.
    """
    research = RESEARCH_RUNTIME if research_runtime is None else research_runtime
    checkout = MAIN_CHECKOUT if main_checkout is None else main_checkout
    resolved = runtime_dir.expanduser().resolve()
    if _within(resolved, research.resolve()):
        raise RuntimeDirectoryRefusedError(
            f"the runtime directory {resolved} is inside {research.resolve()}, P6's research "
            "root: a read-only connection still creates catalog.duckdb.lock and "
            "catalog.duckdb.gate beside the catalogue, and a read-once measurement holds the "
            "catalogue's shared lock for its whole computation; read an APFS clone (`cp -c`) of "
            "the panel instead"
        )
    if _within(resolved, checkout.resolve()):
        raise RuntimeDirectoryRefusedError(
            f"the runtime directory {resolved} is inside the main checkout "
            f"{checkout.resolve()}: a panel never lives in the repository, and a run there "
            "creates catalog.duckdb.lock and catalog.duckdb.gate in it; read a clone outside "
            "every checkout"
        )
    return resolved
```

接线（三处，各一行）：

```python
# scripts/research/p7_diagnostics.py: _open_sources 的第一条语句（:967 之前）
    checked_runtime_directory(runtime_dir)

# scripts/research/p7_reading.py: read_once 的第一条语句（:923 之前）
    checked_runtime_directory(runtime_dir)

# scripts/research/p7_reading.py: _panel_measure / _panel_preflight 的第一条语句
    checked_runtime_directory(runtime_dir)
```

`p7_diagnostics` 用 `from p7_windows import (..., checked_runtime_directory)` 加入它的 `p7_windows` 导入表（`:80-91`，按字母序放在 `capped_panel_store` 之前）。`p7_reading` 已有 `p7_windows as _windows` 与具名导入，二选一均可。

- **`read_once` 的检查放第一条语句的理由**：它检查的是参数，不需要登记；放在认领之前（`:948` 的 `_refuse_a_second_read` 之前）保证坏目录**一定**不写认领。注意顺序文档要更新（模块 docstring `:27-83` 的编号列表加第 0 条）。
- `_panel_measure` / `_panel_preflight` 的检查是纵深防御：`read_once` 传入合法目录时它们重复解析一遍（无 I/O），而直接调用默认测量/预检的调用者（演练测试就这样做）也拿不到 P6 的根。

### Step 1.3 — 运行

- `PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_windows.py tests/unit/test_p7_diagnostics.py tests/unit/scripts/test_p7_reading.py -q`
- 期望：全绿；`test_explore_refuses_the_research_root_before_opening_anything` 打印的 stderr 里含被拒目录，`research/panel` 目录保持空（没有 `.lock`/`.gate`）。

### Step 1.4 — 变异探针（必做，恢复后 `cmp`）

1. 把 `_within` 的 `{root, root.resolve()}` 改成 `{root}`：大小写测试在大小写不敏感的卷上变红（在 Linux CI 上会 skip；本机 macOS 上是红的证据）。
2. 删掉 `read_once` 的第一行检查：`test_read_once_refuses_…before_any_claim` 变成「认领已写」而红。
3. 把 `_open_sources` 的检查挪到 `capped_panel_store` 之后：`test_explore_refuses_the_research_root…` 的 `iterdir() == []` 变红（`.lock`/`.gate` 已建）。
4. 把 `research.resolve()` 换成 `research`（不 resolve）：大小写测试在 APFS 上仍可能绿（`_within` 的 identity 分支救回来），确认这条探针的结论；若绿，补一条用相对路径 `..` 的探针。

### Step 1.5 — 提交

`fix(V2-P7-004,V2-P7-024): a runtime directory on the research root or the main checkout is refused before anything is claimed`

---

## Task 2：`minimum_periods` 的精确上界（item 1）

**Issue:** `V2-P7-004`、`V2-P7-024`。**依据:** 收尾 §5 第 10 条①；`scripts/research/p7_reading.py:499-511`（`_weekdays`/`_most_periods`：只看工作日、步长与期限）、`:484-495`（登记时的上界拒绝）、`:23-25`（模块 docstring 的**错误**声明，见「矛盾」第 6 条）；`scripts/research/p7_windows.py:399-457`（`plan_signal_days` 的禁运）；`scripts/research/p7_diagnostics.py:592-593`（`summarize` 的抽样）；`src/openalpha_cn/panel_ingest.py:944-982`（`load_trading_calendar`）；批次 1 的测试把错误钉成了「26 可达」：`tests/unit/scripts/test_p7_reading.py:1114-1171`。

**Files:**
- Modify: `scripts/research/p7_reading.py`（`_Registration` 加 `settings`；`_exact_most_periods`；`_unreachable_minimum`；`register_reading(..., runtime_dir: Path | None = None)`；`_panel_preflight` 的精确检查；模块 docstring）
- Test: `tests/unit/scripts/test_p7_reading.py`（改写 `test_a_minimum_the_plan_can_reach_is_registered`，新增面板上的精确检查）、`tests/unit/test_p7_reading_rehearsal.py`（真实 `_panel_preflight` 在生成面板上的禁运拒绝）

**Interfaces（consumes / produces）:**

```python
# scripts/research/p7_reading.py
@dataclass(frozen=True, slots=True, kw_only=True)
class _Registration:
    reading: str
    window: DiagnosticWindow
    stop_rule: StopRule | None
    settings: _Settings          # 新增：精确上界要的步长/期限/窗口/all as_of


def _exact_most_periods(
    store: PanelStore, *, window: DiagnosticWindow, settings: _Settings, horizon: int
) -> int: ...


def _unreachable_minimum(
    *, window: DiagnosticWindow, settings: _Settings, rule: StopRule, most: int
) -> str: ...


def register_reading(..., stop_rule: StopRule | None, code_commit: str,
                     runtime_dir: Path | None = None) -> str: ...
```

- `runtime_dir is None`（默认）= 只做数据无关上界（今天的 `_registration_of` 行为逐字保留），模块 docstring 写明「精确上界由 `read_once` 在认领前再施加」；`runtime_dir` 给了 = 先 `checked_runtime_directory`（Task 1），再做精确上界，超了写 `ReadingError` 且**不写文件**。
- 精确上界的拒绝文案**只在一处**（`_unreachable_minimum`），登记写 `ReadingError`，预检把同一句话当问题返回，`read_once` 把它包进 `ReadingPreflightError`（`:956-963` 的现有消息前缀改为「the check before the claim」（`catalogue` 一词不再准确，见 Step 2.4））。

### Step 2.1 — 写失败的测试

**(a) `tests/unit/test_p7_reading_rehearsal.py`**（夹具窗口 `WINDOW = 2027-01-04..2027-01-22`、`body()` 的 `stride=1`、`horizons=[2]`；该文件的 `corpus` 是模块级夹具，`fixture_facts` 让探测变换可用）。追加：

```python
@pytest.mark.parametrize(("horizon", "most"), [(2, 6), (3, 4)])
def test_the_preflight_refuses_a_minimum_the_windows_embargo_makes_unreachable(
    corpus: tuple[Path, Any], fixture_facts: None, horizon: int, most: int
) -> None:
    """2027-01-04..2027-01-22 holds 15 weekday sessions; the day's label exits three (or four)
    sessions later, so the tail is embargoed and the summary samples every `ceil(h/1)`th kept
    day. The weekday bound is 8 (h=2) and 5 (h=3); the calendar makes 6 and 4, so a rule at the
    bound passes `_registration_of` and could only read 'triggered: unreadable'."""
    root, panel = corpus
    registered = body(panel)
    registered["request"]["horizons"] = [horizon]
    registered["stop_rule"] = {
        "score": "composite",
        "horizon": horizon,
        "minimum_periods": most + 1,
        "any_of": [{"metric": "ew_minus_worst_decile", "mean_at_or_below": -1.0}],
    }

    assert p7_reading._most_periods(WINDOW.first, WINDOW.last, stride=1, horizon=horizon) > most, (
        "the data-free bound would not have caught this rule"
    )
    problems = p7_reading._panel_preflight(root, WINDOW)(registered)
    assert problems and f"at most {most}" in problems[0]

    registered["stop_rule"]["minimum_periods"] = most
    assert p7_reading._panel_preflight(root, WINDOW)(registered) == ()
```

**(b) `tests/unit/scripts/test_p7_reading.py`**：把现有的 `test_a_minimum_the_plan_can_reach_is_registered`（`:1147-1171`）改名为 `test_the_data_free_bound_is_the_weekday_bound` 并只断言 `_most_periods` 与「不传 `runtime_dir` 时 26 仍可登记」（保留原三项 `== 26`），再追加模块级夹具与三个测试：

```python
@pytest.fixture(scope="module")
def calendar_panel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A panel whose only dataset is `trade_cal` 2021..2023, every weekday a session: what the
    registration's exact bound reads, written at test time (hard rule 6)."""
    root = tmp_path_factory.mktemp("calendar")
    store = PanelStore(root / "panel")
    for year in (2021, 2022, 2023):
        write_trading_calendar(store, _calendar_batch(year))
    return root


def _calendar_batch(year: int) -> Any:
    days = [date(year, 1, 1) + timedelta(days=offset) for offset in range(366)]
    days = [day for day in days if day.year == year]
    previous: list[str | None] = []
    seen: date | None = None
    for day in days:
        previous.append(None if seen is None else seen.isoformat())
        if day.weekday() < 5:
            seen = day
    published = midnight_shanghai(date(year, 1, 1))
    return batch(
        TRADING_CALENDAR_DATASET,
        subjects=["SSE"] * len(days),
        columns=[
            PanelColumn("cal_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("is_open", "boolean", tuple(day.weekday() < 5 for day in days)),
            PanelColumn("pretrade_date", "string", tuple(previous)),
        ],
        event_time=[midnight_shanghai(day) for day in days],
        available_time=[published] * len(days),
        fetched_at=published,
    )


def test_a_minimum_above_what_the_panels_calendar_allows_is_refused_at_registration(
    calendar_panel: Path, tmp_path: Path
) -> None:
    """519 weekdays at stride 20 and h=20 give the data-free bound 26; the last signal day's
    label exits past 2023-12-29, so the calendar makes 25. 26 passes `_registration_of` and
    would spend the one read on 'unreadable'; 25 is the most a rule may ask for."""
    path = tmp_path / "v2-p7-024-validation.registration.json"
    too_many = p7_reading.StopRule(
        score="composite", horizon=20, minimum_periods=26, any_of=RULE.any_of
    )
    with pytest.raises(p7_reading.ReadingError, match="at most 25"):
        register(path, runtime_dir=calendar_panel, stop_rule=too_many)
    assert not path.exists()

    reachable = p7_reading.StopRule(
        score="composite", horizon=20, minimum_periods=25, any_of=RULE.any_of
    )
    register(path, runtime_dir=calendar_panel, stop_rule=reachable)
    assert path.exists()


def test_a_registration_whose_calendar_cannot_be_read_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "partial"
    store = PanelStore(root / "panel")
    for year in (2022, 2023):
        write_trading_calendar(store, _calendar_batch(year))
    path = tmp_path / "v2-p7-024-validation.registration.json"

    with pytest.raises(p7_reading.ReadingError, match="2021"):
        register(path, runtime_dir=root)

    assert not path.exists()


@pytest.mark.parametrize(("stride", "horizon"), [(20, 20), (5, 20), (20, 5), (1, 2)])
def test_the_data_free_bound_is_never_below_what_the_calendar_allows(
    calendar_panel: Path, stride: int, horizon: int
) -> None:
    """Keeping `_most_periods` as an early refusal is sound only if it can never understate the
    calendar's maximum: sessions <= weekdays, embargoes only drop days, and ceil is monotone."""
    store = p7_windows.capped_panel_store(calendar_panel, p7_windows.VALIDATION)
    settings = p7_reading._settings_of(
        {**REQUEST, "stride": stride, "horizons": [horizon]}, CONFIG, p7_windows.VALIDATION
    )

    exact = p7_reading._exact_most_periods(
        store, window=p7_windows.VALIDATION, settings=settings, horizon=horizon
    )

    assert (
        p7_reading._most_periods(
            p7_windows.VALIDATION.first, p7_windows.VALIDATION.last, stride=stride, horizon=horizon
        )
        >= exact
    )
```

测试文件需要的新导入：`from datetime import timedelta`（已有 `date`）、`from panel_fixtures import _batch as batch`、`from panel_fixtures import _midnight_shanghai as midnight_shanghai`、`from openalpha_cn.domain.panel_batch import PanelColumn`、`from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET`、`from openalpha_cn.panel.store import PanelStore`、`from openalpha_cn.panel_ingest import write_trading_calendar`；`register()`（`:344-357`）的参数字典加 `"runtime_dir": runtime_dir` 并把签名改成 `def register(path: Path, *, runtime_dir: Path | None = None, **changes: Any) -> str:`。

- 运行：`PYTHONPATH="$PWD/src" uv run pytest tests/unit/test_p7_reading_rehearsal.py tests/unit/scripts/test_p7_reading.py -q`
- 期望失败：演练测试 `assert problems and …` 失败（`problems == ()`，因为今天预检只问 `registered_years`）；登记测试 `Failed: DID NOT RAISE`（26 今天被接受）。

### Step 2.2 — 实现

```python
def _exact_most_periods(
    store: PanelStore, *, window: DiagnosticWindow, settings: _Settings, horizon: int
) -> int:
    """The most non-overlapping periods `store`'s own calendar can summarise, computed exactly as
    the measure plans and `summarize` samples: `plan_signal_days` over the calendar (which
    embargoes every signal day whose label would exit past `window.last`), then every
    `ceil(horizon / stride)`-th kept signal day (`p7_diagnostics.summarize`)."""
    explore = settings.explore
    calendar = load_trading_calendar(
        store,
        exchange=EXCHANGE,
        years=tuple(range(explore.first.year - 1, window.ceiling_year + 1)),
        as_of=explore.as_of,
    )
    plan = plan_signal_days(
        calendar,
        window=window,
        first=explore.first,
        last=explore.last,
        stride=explore.stride,
        horizons=explore.horizons,
    )
    return math.ceil(len(plan.sessions) / math.ceil(horizon / explore.stride))


def _unreachable_minimum(
    *, window: DiagnosticWindow, settings: _Settings, rule: StopRule, most: int
) -> str:
    explore = settings.explore
    return (
        f"the stop rule's minimum_periods {rule.minimum_periods} cannot be reached: the panel's "
        f"calendar makes at most {most} non-overlapping periods at stride {explore.stride} and "
        f"h={rule.horizon} over {explore.first}..{explore.last} (a signal day whose label would "
        f"exit past {window.last} is embargoed, and the summary reads every "
        f"ceil({rule.horizon}/{explore.stride})th kept signal day); such a rule could only ever "
        "read 'triggered: unreadable'"
    )
```

`_Registration` 加 `settings: _Settings`，`_registration_of` 的 `return _Registration(reading=reading, window=window, stop_rule=rule, settings=settings)`。`register_reading`：

```python
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
    runtime_dir: Path | None = None,
) -> str:
```

在 `_registration_of(body)` 之后、写文件之前（`path.exists()` 检查之后最省事：已存在则先按原样拒绝）：

```python
    runtime = None if runtime_dir is None else _checked_runtime(runtime_dir)
    if registered.stop_rule is not None and runtime is not None:
        try:
            store = capped_panel_store(runtime, registered.window)
            most = _exact_most_periods(
                store,
                window=registered.window,
                settings=registered.settings,
                horizon=registered.stop_rule.horizon,
            )
        except (DiagnosticWindowError, TradingCalendarError, PanelStorageError) as error:
            raise ReadingError(
                f"the panel at {runtime} cannot be read to check the stop rule's "
                f"minimum_periods: {error}"
            ) from error
        if registered.stop_rule.minimum_periods > most:
            raise ReadingError(
                _unreachable_minimum(
                    window=registered.window,
                    settings=registered.settings,
                    rule=registered.stop_rule,
                    most=most,
                )
            )
```

`_checked_runtime` 是 `p7_reading` 里的一行包装（把 `p7_windows.RuntimeDirectoryRefusedError` 折成 `ReadingError`，因为 `register_reading` 的契约是「抛 `ReadingError`」）：

```python
def _checked_runtime(runtime_dir: Path) -> Path:
    try:
        return checked_runtime_directory(runtime_dir)
    except RuntimeDirectoryRefusedError as error:
        raise ReadingError(str(error)) from error
```

新导入：`from openalpha_cn.domain.trading_calendar import TradingCalendarError`、`from openalpha_cn.panel.catalog import PanelStorageError`、从 `p7_windows` 加 `checked_runtime_directory`、`RuntimeDirectoryRefusedError`、`capped_panel_store`（已有）。

`_panel_preflight`（`:812-827`）改成：

```python
def preflight(body: Mapping[str, Any]) -> Sequence[str]:
    checked_runtime_directory(runtime_dir)
    panel = runtime_dir / PANEL_SUBDIRECTORY
    if not (panel / CATALOG_FILE).is_file():
        return (f"{panel} holds no {CATALOG_FILE}: there is no panel catalogue to read",)
    settings = _settings_of(body["request"], body["config"], window)
    store = capped_panel_store(runtime_dir, window)
    problems = preflight_problems(
        store, window=window, datasets=_preflight_datasets(settings.explore.book)
    )
    if problems:
        return problems
    form = body.get("stop_rule")
    if form is None:
        return ()
    try:
        rule = _rule_of(form)
        most = _exact_most_periods(store, window=window, settings=settings, horizon=rule.horizon)
    except ReadingError as error:
        return (f"the stop rule cannot be read: {error}",)
    except (TradingCalendarError, PanelStorageError) as error:
        return (
            "the calendar the stop rule's bound needs cannot be read before the claim "
            f"({error}); the rule's minimum_periods cannot be shown reachable",
        )
    if rule.minimum_periods > most:
        return (_unreachable_minimum(window=window, settings=settings, rule=rule, most=most),)
    return ()
```

（`settings = _settings_of(...)` 取代原来的 `book = _settings_of(...).explore.book`；其余不变。）

### Step 2.3 — 运行

- `PYTHONPATH="$PWD/src" uv run pytest tests/unit/test_p7_reading_rehearsal.py tests/unit/scripts/test_p7_reading.py -q`
- 期望：全绿；精确数字 6/4 在演练文件、25 在日历夹具文件。（corrected 2026-11-07：实际钉住的是——验证夹具的数据无关上界 **26**（不传 `runtime_dir` 时 26 仍可登记，`test_the_data_free_bound_is_the_weekday_bound`）、日历精确上界 **25**（26 在给了 `runtime_dir` 的登记与认领前预检都被拒，`test_a_minimum_above_what_the_panels_calendar_allows_is_refused_at_registration`、`test_the_preflight_refuses_a_minimum_the_windows_embargo_makes_unreachable`），演练夹具的精确 **6 / 4**（数据无关上界 8 / 5）；真实窗口的 24 没有离线复核，仍由 `024` 运行时核对。）

### Step 2.4 — 模块 docstring 与消息（同一提交）

- `p7_reading.py:23-25` 的第三条从「at most the bound `_most_periods` … a larger one could only ever be 'triggered: unreadable'」改成两段：数据无关上界（`_most_periods`，工作日/步长/期限）是**必要不充分**；**精确上界**要在认领之前从受限存储读 `trade_cal`（日历不是结果数据，收尾 §5 第 10 条①批准），按 `plan_signal_days` 的禁运与 `summarize` 的抽样算，超了在登记时（给了 `runtime_dir`）或认领前（`read_once` 的默认预检）拒绝，消息里给精确数。
- 编号列表第 5 条（`:58-62`）从「it asks `registered_years` and reads no partition」改为「它问每个数据集的 `registered_years`；**主读数还要读窗口各年与年前一年的 `trade_cal`**（日历不是结果数据）以核对 `minimum_periods`；其它数据集仍然只列目录，列出但不完整的分区、或没有因子构建的信号日，仍要到认领之后的测量才暴露」。
- `_panel_preflight` 的 docstring（`:815-817`）同步；`read_once` 的 `ReadingPreflightError` 消息（`:959-962`）把「the catalogue check before the claim」改成「the check before the claim」。
- `register_reading` 的 docstring（`:387-399`）写明 `runtime_dir` 的两档行为与「不写文件」。

### Step 2.5 — 变异探针（必做，恢复后 `cmp`）

1. `_exact_most_periods` 里把 `math.ceil(len(plan.sessions) / math.ceil(...))` 的外层 `ceil` 换成 `len(...) // ...`：演练 h=3 的 4 变 3，测试红。
2. 把 `plan_signal_days` 换成 `calendar.trading_days_between(...)[::stride]`（跳过禁运）：演练 6 变 8/7，测试红。
3. 把 `_panel_preflight` 的精确检查删掉：演练测试 `problems == ()` 红。
4. 把 `register_reading` 的精确检查删掉：`test_a_minimum_above_what_the_panels_calendar_allows…` 的 `DID NOT RAISE` 红。
5. 把 `load_trading_calendar` 的 `years` 改成从 `explore.first.year`（不含年前一年）：日历夹具上 2021 仍被读（窗口年），但真实窗口的禁运边界需要年前一年吗？（`plan_signal_days` 只用窗口内的会话；年前一年是 `_panel_measure` 的同一形状。）探针只确认测试没有因此变红，并记录：保持与测量相同的 years 是「同一条规则」的理由。
6. 把 `_panel_preflight` 的 `except (TradingCalendarError, PanelStorageError)` 改成 `pass`：`test_a_registration_whose_calendar_cannot_be_read_writes_nothing` 的预检侧（如加了）或 `read_once` 的真实路径会抛裸异常；确认有测试红。

### Step 2.6 — 提交

`feat(V2-P7-004,V2-P7-024): the stop rule's minimum is bounded by the panel's own calendar, embargo included`

---

## Task 3：`second_use` 必须等验证窗口的认领已提交（item 3）

**Issue:** `V2-P7-024`、`V2-P7-004`。**依据:** 收尾 §5 第 10 条③；`scripts/research/p7_reading.py:1090-1157`（`_refuse_a_second_read` 只看同一窗口）、`:1165-1182`（`_LOG_EVERYTHING`）、`:1190-1264`（`_refuse_any_claim_of_the_window`，所有认领文件都记窗口）、`:968-988`（窗口认领先写、读数认领后写）；反向顺序今天不需要检查（验证窗口的读径从不看二次使用段）。

**Files:**
- Modify: `scripts/research/p7_reading.py`（新异常、`_require_the_validation_window_spent`、`read_once` 调用点、模块 docstring）
- Test: `tests/unit/scripts/test_p7_reading.py`

**Interfaces（consumes / produces）:**

```python
class ValidationNotSpentError(registry.HoldoutRefusedError):
    """A second-use read before the validation window's claim is committed."""


def _require_the_validation_window_spent(root: Path) -> None: ...
```

- **证明物（U3）**：`docs/research/p7-readings/validation.window-claim.json`，**必须已提交且任何 ref/reflog 可达**（用 `_LOG_EVERYTHING`，与 `_refuse_a_second_read` 同一组参数）。只读、在磁盘上都**不够**：未提交的认领可以被删掉，然后验证窗口就能在 2024–2026 之后重读——正是这一条要关的门。
- 调用点：`read_once` 在 `_refuse_a_second_read(...)`（`:948-955`）之后、默认预检（`:956`）之前：`if window.name == WINDOWS["second_use"].name: _require_the_validation_window_spent(root)`。
- **不**加反向检查（验证窗口读不看二次使用段）：评审的「the reverse order is already fine」；反向顺序只可能来自手写认领，而手写认领已在「它只约束流程、不约束数据访问」的免责条款里（模块 docstring `:117-121`）。

### Step 3.1 — 写失败的测试（`tests/unit/scripts/test_p7_reading.py`）

该文件已有 `SECOND_USE_REQUEST`（`:336`）、`commit_file`/`git`/`head`（`research_repo`）、`commit_file` 的 `at=` 用法（`:130`）。追加：

```python
def registered_second_use(repo: Path) -> Path:
    path = repo / "docs" / "research" / "p7-readings" / "v2-p7-024-second-use.registration.json"
    p7_reading.register_reading(
        path=path,
        reading="v2-p7-024-second-use",
        issue="V2-P7-024",
        role="secondary",
        window="second_use",
        request=SECOND_USE_REQUEST,
        config=CONFIG,
        stop_rule=None,
        code_commit=head(repo),
    )
    commit_file(repo, path, "register the secondary reading", at=COMMITTED)
    return path


def write_validation_window_claim(repo: Path, *, commit: bool) -> Path:
    path = repo / "docs" / "research" / "p7-readings" / "validation.window-claim.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "reading": "v2-p7-024-validation",
                "registration_sha256": "0" * 64,
                "window": "validation",
                "claimed_at": COMMITTED.isoformat(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    if commit:
        commit_file(repo, path, "the validation window's claim", at=COMMITTED)
    return path


def test_a_second_use_read_before_the_validation_windows_claim_is_refused(repo: Path) -> None:
    """2024-2026 seen first could still change the numbers of the validation stop rule written
    afterwards: the secondary read waits for the validation window's committed claim."""
    registration = registered_second_use(repo)
    measure = Measure()

    with pytest.raises(p7_reading.ValidationNotSpentError, match="validation"):
        run(registration, repo, measure)

    assert measure.calls == []
    assert not claim_of(registration).exists()
    assert not window_claim_of(repo, "second_use").exists()


def test_a_validation_window_claim_on_disk_but_not_committed_does_not_open_second_use(
    repo: Path,
) -> None:
    """An uncommitted claim can be deleted, and the validation window read again after
    2024-2026 was seen, so 'on disk' is not the artifact: the commit is."""
    registration = registered_second_use(repo)
    write_validation_window_claim(repo, commit=False)

    with pytest.raises(p7_reading.ValidationNotSpentError, match="commit"):
        run(registration, repo, Measure())


def test_a_committed_validation_window_claim_opens_second_use(repo: Path) -> None:
    registration = registered_second_use(repo)
    write_validation_window_claim(repo, commit=True)

    result = run(registration, repo, Measure())

    assert "stop_rule" not in result
    assert claim_of(registration).exists()


def test_a_validation_read_does_not_wait_for_the_second_use_window(repo: Path) -> None:
    """The other order needs nothing: a claim of the secondary window does not stop the
    validation window's read (only a claim naming validation could)."""
    write_validation_window_claim(repo, commit=False)  # named validation, not second_use
    claim_of_second = repo / "docs/research/p7-readings/second_use.window-claim.json"
    claim_of_second.write_text(
        json.dumps({"reading": "some-secondary", "window": "second_use"}) + "\n",
        encoding="utf-8",
    )

    registration = registered(repo)
    result = run(registration, repo, Measure())

    assert result["stop_rule"]["outcome"] == "not triggered"
```

（`window_claim_of(repo, "second_use")` 是文件里已有的辅助——若签名不同，按其定义调用；`claim_of` 在 `:340`。）

- 运行：`PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_reading.py -q`
- 期望失败：前两条 `DID NOT RAISE`（今天二次使用段不看验证窗口）；第三条今天也通过（是防回归）；第四条今天通过。

### Step 3.2 — 实现

```python
class ValidationNotSpentError(registry.HoldoutRefusedError):
    """A second-use read before the validation window's claim is committed."""


def _require_the_validation_window_spent(root: Path) -> None:
    """Refuse unless the validation window's claim is committed and reachable from some ref.

    The artifact is `<validation>.window-claim.json`, written unconditionally by the first
    `read_once` of that window before its reading's claim (`p7_reading.py:968-988`), and named by
    window rather than by reading. It must be committed: a claim only on disk can be deleted, and
    the validation window read again after 2024-2026 was seen -- the order this check exists to
    prevent. The commit is looked for exactly as `_refuse_a_second_read` looks for a spent claim
    (`_LOG_EVERYTHING`): every ref, every unexpired reflog entry, every parent of every merge.
    """
    relative = (READING_DIRECTORY / f"{WINDOWS['validation'].name}{WINDOW_CLAIM_SUFFIX}").as_posix()
    log = registry._git(root, *_LOG_EVERYTHING, "--format=%H", "--", relative)
    if log.returncode != 0:
        raise ValidationNotSpentError(
            f"git cannot tell whether {relative} was ever committed "
            f"({log.stderr.decode(errors='replace').strip()}); whether the validation window was "
            "read first cannot be shown, so the second_use read is refused"
        )
    commits = log.stdout.decode().split()
    on_disk = root / relative
    if commits:
        shown = registry._git(root, _NO_REPLACEMENTS, "show", f"{commits[-1]}:{relative}")
        try:
            named = json.loads(shown.stdout).get("window")
        except (ValueError, AttributeError):
            named = None
        if shown.returncode != 0 or named != WINDOWS["validation"].name:
            raise ValidationNotSpentError(
                f"{relative} was committed at {commits[-1]} but does not read as a claim naming "
                "the validation window; the ordering cannot be shown, so the second_use read is "
                "refused"
            )
        return
    if on_disk.is_file():
        raise ValidationNotSpentError(
            f"{on_disk.relative_to(root).as_posix()} is on disk but was never committed: a "
            "claim that is not committed can be deleted, and the validation window read again "
            "after 2024-2026 was seen; commit it and keep that commit in this repository's refs, "
            "then read the second_use window"
        )
    raise ValidationNotSpentError(
        "the second_use window is read only after the validation window: no committed "
        f"{relative} exists. A 2024-2026 result seen first could still change the validation "
        "stop rule's numbers written afterwards; read the validation window first (its "
        "registration and stop rule committed before it), then commit its window claim"
    )
```

`read_once` 的调用点（在 `_refuse_a_second_read(...)` 之后）：

```python
    if window.name == WINDOWS["second_use"].name:
        _require_the_validation_window_spent(root)
```

### Step 3.3 — 运行

- `PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_reading.py -q` → 全绿。
- `PYTHONPATH="$PWD/src" uv run pytest tests/unit/test_p7_reading_rehearsal.py tests/unit/test_p7_diagnostics.py -q` → 仍全绿（措辞常量未动）。

### Step 3.4 — 文档串（同一提交）

- 模块 docstring 的编号顺序（`:27-83`）在 4 与 5 之间插入一条：`ValidationNotSpentError` —— 二次使用段的读数要求 `<validation>.window-claim.json` 已提交；未提交在磁盘上不够；反向顺序不需要任何检查。
- 「What this does not establish」（`:87-128`）加一条：这是**流程**上的顺序守卫，不是对数据访问的约束；一个手写的、已提交的验证窗口认领会满足它（但它同时也让验证窗口按「只读一次」作废，伪造的代价是那次读）；它也不要求验证窗口的**测量**已经完成——一个认领后崩溃的验证读数同样满足，因为那正是「窗口已花掉」的定义。
- **`READ_ONCE_WORDING`、`SECOND_USE_READ_ONCE_WORDING`、`SECOND_USE_WORDING`（`p7_diagnostics.py:144-160`）不改**：它们说的是每个窗口的读数**是什么**（第一次样本外 / 花掉的保留期的次要读数），不是**何时**读；而且 `tests/unit/test_p7_diagnostics.py:879-917` 按字节钉住它们。顺序只写进 `p7_reading` 的模块 docstring 与新错误消息。这一条要在提交信息里写明「checked, no change」。
- 运行前提（收尾 §6）里「认领必须提交」已有；本任务让它在代码里被强制。

### Step 3.5 — 变异探针（必做，恢复后 `cmp`）

1. 把 `log.stdout.split()` 的判断改成「磁盘上存在就算」：`test_a_validation_window_claim_on_disk_but_not_committed…` 红。
2. 删掉 `read_once` 的调用点：两条 `DID NOT RAISE` 红。
3. 把 `_LOG_EVERYTHING` 换成 `(_NO_REPLACEMENTS, "log", "--all")`：把验证窗口认领只放在 reflog 不可达的状态下的测试没有（当前测试用普通提交），确认三条测试仍绿并记录这一探针未被钉住（可接受：与 `_refuse_a_second_read` 用同一组参数，那条路径已有自己的测试）。
4. 把 `named != WINDOWS["validation"].name` 改成只认文件名：加一条「文件名对、内容写 second_use」的测试（推荐顺手加上），否则探针不红。

### Step 3.6 — 提交

`feat(V2-P7-024): the second-use reading waits for the validation window's committed claim`

---

## Task 4：`as_of` 作为知识时钟——登记说明、结果写明（item 4）

**Issue:** `V2-P7-024`、`V2-P7-004`。**依据:** 收尾 §5 第 10 条④；`scripts/research/p7_reading.py:548-554`（`as_of` 只有下界）、`:1334-1382`（`reading_lines`，结果文本）、`:250-262`（`_BODY_KEYS`）；`scripts/research/p7_windows.py:44-49`（`as_of` 是知识时钟、年上限封经济日期）；`src/openalpha_cn/panel_ingest.py:2992-3014`（`session_publication_instant`，下界用的就是它）。

**Files:**
- Modify: `scripts/research/p7_reading.py`（`_BODY_KEYS`、`_registration_of` 的理由校验、`register_reading(..., as_of_reason=...)`、`reading_lines` 一行、模块 docstring）
- Test: `tests/unit/scripts/test_p7_reading.py`

**Interfaces（consumes / produces）:**

```python
# scripts/research/p7_reading.py
_BODY_KEYS = frozenset({..., "as_of_reason"})          # 新增一个键

KNOWLEDGE_LINE: Final[str] = (  # corrected 2026-11-07: the delivered name is `_KNOWLEDGE_LINE`
    "read under the knowledge at {as_of}: {reason} -- every partition is read as it stood then, "
    "so a revision or name published after a session enters that session's scores; this is a "
    "recomputation at that instant, not a replay at each session's publication instant"
)
# corrected 2026-11-07: the delivered `_KNOWLEDGE_LINE` (scripts/research/p7_reading.py:260-265) is
# "read under the knowledge at {as_of} (-- the registrant's words: {reason}) -- ..."; the block above
# lacks the "(-- the registrant's words: …)" marker.


def register_reading(..., code_commit: str, as_of_reason: str,
                     runtime_dir: Path | None = None) -> str: ...
```

- **格式**：`READING_SCHEMA` 保持 `"openalpha-p7-reading-registration/v1"`。理由（写进 docstring）：没有登记存在（`docs/research/p7-readings/` 不存在）；这个体不是硬规则 3 的契约行（手写准入 `_registration_of`/`_BODY_KEYS`，没有 pydantic model、没有 `Literal[".../v1"]` + `extra="forbid"`），所以加字段不需要迁移；`v1` 就是第一个格式。
- **理由的约束**：`str`、`strip()` 后非空、无 `\n`/`\r`（结果文本里必须是一行）；错误消息含 `as_of_reason`（测试能 `match="as_of"`）。
- **`reading_lines`** 的兼容：`body.get("as_of_reason")` 与 `body.get("config", {}).get("as_of")` 都存在才印这一行——演练文件用 `NAMED`（`tests/unit/test_p7_reading_rehearsal.py:91-104`，没有 `config`）直接调 `reading_lines`，不能 KeyError。
- **不**改 `diagnostic_document` / `diagnostic_lines` / 任何措辞常量：`tests/unit/test_p7_diagnostics.py:903-917` 按字节钉住验证窗口的读一次文本。

### Step 4.1 — 写失败的测试（`tests/unit/scripts/test_p7_reading.py`）

在 `CONFIG` 旁加常量：

```python
AS_OF_REASON: Final[str] = (
    "the 2026-10-09 08:00 panel is the newest snapshot before the code freeze, and every "
    "revision published before it is part of what the reading may know"
)
```

`register()`（`:344-357`）的参数字典加 `"as_of_reason": AS_OF_REASON`；`registered()`（`:108-131`）加 `as_of_reason: str = AS_OF_REASON` 并传给 `register_reading`；Task 3 新增的 `registered_second_use()` 也加 `as_of_reason=AS_OF_REASON`（它是 `register_reading` 的直接调用，不经 `register()`）。追加：

```python
@pytest.mark.parametrize("reason", ["", "   ", "one line\nand a second"])
def test_a_registration_says_why_its_as_of_is_the_knowledge_it_reads(
    repo: Path, reason: str
) -> None:
    with pytest.raises(p7_reading.ReadingError, match="as_of"):
        registered(repo, commit=False, as_of_reason=reason)


def test_the_result_says_which_knowledge_clock_the_reading_is_under(repo: Path) -> None:
    registration = registered(repo)

    run(registration, repo, Measure())

    text = registration.with_name("v2-p7-024-validation.result.txt").read_text(encoding="utf-8")
    assert "read under the knowledge at 2026-10-09T00:00:00+08:00" in text
    assert AS_OF_REASON in text
    assert "not a replay at each session's publication instant" in text
```

- 运行：`PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_reading.py -q`
- 期望失败：`test_a_registration_says_why…` 的 `TypeError: register_reading() got an unexpected keyword argument 'as_of_reason'`（或 `DID NOT RAISE`）；结果文本断言失败。

### Step 4.2 — 实现

```python
_KNOWLEDGE_LINE: Final[str] = (
    "read under the knowledge at {as_of}: {reason} -- every partition is read as it stood then, "
    "so a revision or name published after a session enters that session's scores; this is a "
    "recomputation at that instant, not a replay at each session's publication instant"
)
```

`_BODY_KEYS` 加 `"as_of_reason"`；`register_reading` 的签名与 `body` 加 `"as_of_reason": as_of_reason`；`_registration_of` 在 `issue` 检查之后：

```python
    reason = body["as_of_reason"]
    if not isinstance(reason, str) or not reason.strip() or reason != reason.strip():
        raise ReadingError(
            "the registration's as_of_reason must be one non-empty line saying why its as_of is "
            "the knowledge the reading is taken under"
        )
    if "\n" in reason or "\r" in reason:
        raise ReadingError(
            "the registration's as_of_reason must be one line: the result text prints it as one"
        )
```

`reading_lines`（`:1339-1350` 的 `lines` 列表）在角色行之后：

```python
    reason = body.get("as_of_reason")
    as_of = body.get("config", {}).get("as_of") if isinstance(body.get("config"), Mapping) else None
    if reason is not None and as_of is not None:
        lines.append("  " + _KNOWLEDGE_LINE.format(as_of=as_of, reason=reason))
```

### Step 4.3 — 运行

- `PYTHONPATH="$PWD/src" uv run pytest tests/unit/scripts/test_p7_reading.py tests/unit/test_p7_reading_rehearsal.py tests/unit/test_p7_diagnostics.py -q` → 全绿（演练文件的 `NAMED` 仍工作；`test_the_validation_windows_read_once_text_is_unchanged_byte_for_byte` 仍绿）。

### Step 4.4 — 模块 docstring（同一提交）

「What this does not establish」加一条：

```
* The reading is taken under the knowledge at its registered `as_of`, not replayed at each
  session's publication instant: every partition is read as it stood at `as_of`, so a revision or
  a late name announcement published after a session -- or after the window -- enters that
  session's scores. `as_of` may be long after the window (the year cap, not `as_of`, enforces the
  economic date); the registration says why that instant is the one, and the result's text repeats
  it.
```

`register_reading` 的 docstring 写明 `as_of_reason` 的位置与约束，以及「这个体是新的格式：没有登记存在，`.../v1` 是第一个格式」。

### Step 4.5 — 变异探针（必做，恢复后 `cmp`）

1. 删掉 `reading_lines` 的新行：`test_the_result_says_which_knowledge_clock…` 红。
2. 把 `_registration_of` 的理由校验改成 `if not isinstance(reason, str)`：三个参数化的空/空白/多行用例中至少两个红。
3. 把 `_BODY_KEYS` 的 `"as_of_reason"` 去掉：`_require_keys` 报「carries ['as_of_reason']」，登记测试红。
4. 把 `_KNOWLEDGE_LINE` 里的 "not a replay" 改成 "replay"：文本测试红（并确认禁止词扫描不受影响）。

### Step 4.6 — 提交

`feat(V2-P7-024): the registration names the knowledge clock its reading is taken under`

---

## Task 5：补钉 `_readable_instant` 的两个回退分支（收尾 §4 延后项）

**Issue:** `V2-P7-008`（该函数属于幸存者登记的晚到记录检查；本任务只加测试）。**依据:** `src/openalpha_cn/strategy_survivor_registration.py:973-983`（`_not_yet_knowable` 后调用 `_readable_instant`；`readable is None or readable <= filed` 时重新抛出原错误）、`:1214-1224`（`_readable_instant` 的定义）；已有测试只钉成功分支：`tests/unit/test_strategy_survivor_registration.py:1518-1534`。

**Files:**
- Test: `tests/unit/test_strategy_survivor_registration.py`（只加测试；`src/` 不改）

**Interfaces:** 无新接口；`monkeypatch.setattr(registration_module, "_readable_instant", …)`（该文件已 `from openalpha_cn import strategy_survivor_registration as registration_module`，`:80`；`registered_at` 已导入）。

### Step 5.1 — 写测试

在 `test_the_recompute_reads_at_the_readable_instant_when_its_filing_instant_cannot_be_read`（`:1518`）之后追加：

```python
def test_the_recompute_keeps_the_original_refusal_when_nothing_readable_is_stored(
    evening_store: PanelStore,
    late_clock_store: PanelStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_readable_instant` answers `None` when no partition the day reads has a coverage record:
    the refusal that sent the recompute looking is then the one to raise, not a second read."""
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    monkeypatch.setattr(registration_module, "_readable_instant", lambda *args, **kwargs: None)

    with pytest.raises(PartitionNotYetKnowableError):
        recompute(late_clock_store, record)


def test_the_recompute_keeps_the_original_refusal_when_the_readable_instant_is_not_later(
    evening_store: PanelStore,
    late_clock_store: PanelStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newest instant at or before the filing instant would re-read at the same refusing
    clock: the original refusal stands."""
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    filed_at = registered_at(record)
    monkeypatch.setattr(registration_module, "_readable_instant", lambda *args, **kwargs: filed_at)

    with pytest.raises(PartitionNotYetKnowableError):
        recompute(late_clock_store, record)
```

- 运行：`PYTHONPATH="$PWD/src" uv run pytest tests/unit/test_strategy_survivor_registration.py -q -k readable_instant`
- 期望：**两条今天已经通过**（这是补钉，不是修缺陷）；先记录「没有测试时这两条不存在」——用 `--collect-only` 证明文件里此前只有一条相关测试。

### Step 5.2 — 变异探针（必做）

1. 把 `strategy_survivor_registration.py:981` 的 `if readable is None or readable <= filed:` 改成 `if readable is None:`：第二条红。
2. 改成 `if False:`（并让 `readable` 可能为 None）：第一条红（`scored_at(None)` 的 `TypeError`）。
3. 恢复并 `cmp` 确认 `src/` 逐字节未变。

### Step 5.3 — 提交

`test(V2-P7-008): the readable-instant fallback keeps the original refusal in its two other branches`

---

## 受绑定代码（bound code touched）

全部在 `006b` 之前；**今天没有任何读数登记**（`docs/research/p7-readings/` 不存在），所以没有绑定被作废。

| 文件 | 任务 | 改动性质 |
|---|---|---|
| `scripts/research/p7_windows.py` | 1 | 新常量、`RuntimeDirectoryRefusedError`、`_within`、`checked_runtime_directory` |
| `scripts/research/p7_diagnostics.py` | 1 | `_open_sources` 一行调用 + 导入 |
| `scripts/research/p7_reading.py` | 1、2、3、4 | 运行目录检查（4 处）、`_Registration.settings`、`_exact_most_periods`、`_unreachable_minimum`、`register_reading` 的两个新关键字参数、`_panel_preflight`、`ValidationNotSpentError`、`_require_the_validation_window_spent`、`_BODY_KEYS`/`_registration_of`/`reading_lines`、模块 docstring |

**不碰**：`src/`（Task 5 只加测试）、`pyproject.toml`、`uv.lock`、`scripts/daily_selection.py`、`scripts/forward_report.py`、`scripts/p7_smoke_env.py`（决定：不导入它、也不改它，用交叉核对测试钉住两条规则同义）。

**不做**：`_open_sources` 与 `_panel_measure` 的近似重复（U5，收尾 §4 D5 延后项）；浅克隆/grafts/逃生口（收尾 §5 第 9 条）；任何真实读数、任何网络请求、任何登记写入。

## 本计划不做的（留给之后的运营步骤）

- `V2-P7-024` 的真实读本身：克隆面板（`cp -c`，路径无空格、不在主检出、不在临时目录）、`git fetch --unshallow`、用户给的停止规则数值、登记、认领、提交——全部等用户明确说开始。运营步骤里要核对：精确上界消息给出的数（评审预期 24）与数据无关上界（26）；若消息给的不是 24，停下来报告，不猜、不调参。
- 收尾 §5 第 10 条以外的延后项（A1–A4、B1–B6、D1–D6 的其余条目）、台账行、`docs/runbooks` 的更新。
- 收尾 §6「预检只看目录库列出了哪些年份」这句话在本计划之后不再完全成立（主读数会读 `trade_cal` 分区）；收尾是历史记录，不改；`024` 的运行前提在真正读之前由运营者按新 docstring 更新。

## 矛盾与发现（计划起草时核对到的，按价值排序）

1. **「真实最大 24」没有离线复核过，且与纯工作日日历上的 25 不同。** `_most_periods`（`p7_reading.py:505-511`）在 519 个工作日的验证窗口、步长 20、h20 上给 26；用同一窗口的纯工作日日历模拟 `plan_signal_days` + `summarize` 得 26 个信号日、禁运 1 个、精确 25。24 需要真实的 484 个左右交易日（`ceil(484/20)=25` 个信号日，禁运 1 个）。所以：**缺陷是真的**（数据无关上界高于日历上界，26 能通过登记），但 24 是评审在真实日历上的算术、**本计划没有也不该离线复核**；测试钉夹具上的数（日历夹具 25；演练窗口 6 与 4），真实数由 `024` 的消息核对。
2. **批次 1 的一个通过的测试把错误钉成了规范。** `tests/unit/scripts/test_p7_reading.py:1147-1171` 断言 `_most_periods(...) == 26` 并**登记** `minimum_periods=26` 为「可达」；`:1114-1124` 还把 36 的拒绝消息钉成「at most 26」。Task 2 必须改写这两个测试的语义：26 只是**数据无关**上界，日历上界更低。
3. **模块 docstring 的第三条是假的**（`p7_reading.py:23-25`）：「a stop rule's `minimum_periods` can be reached: it is at most the bound `_most_periods` reads off the weekdays … a larger one could only ever be 'triggered: unreadable'」。等于上界、但高于日历上界的规则今天能通过，然后被读成不可读——正是要防的失败。Task 2.4 修正它。
4. **只读连接确实会建锁文件**（评审第 2 条核实）：`panel/store.py:1352` 的 `_open_lock_file` 用 `os.O_RDWR | os.O_CREAT` 打开 `.lock` 与 `.gate`（`:1421`、`:1426`），与 `:1389-1399` 的 docstring 一致；读一次还在整个计算期间持共享侧（`:2206-2222` 自己的 docstring 说「hold it across reads, not across computation」）。评审正确。
5. **行号有漂移**（收尾写在 `22d65ae`，`cc7dc60` 之后文件变长）：`_open_sources` 在 `:962`（评审 ~942）、`_explore` 在 `:1052`（~1032）、`_panel_measure` 在 `:833`（~807）、`read_once` 在 `:908`（~903）、`as_of` 下界在 `:548-554`（~545）。行为与评审描述一致。
6. **`_readable_instant` 的「回退」本身已被钉住**（`tests/unit/test_strategy_survivor_registration.py:1518-1534`），未钉的是它的两个**拒绝**分支（`readable is None`、`readable <= filed`，`strategy_survivor_registration.py:981-982`）。收尾把它写成「回退没被钉」，精确说法应是「回退的成功分支被钉，两个重新抛出的分支没有」。Task 5 补后两条。
7. **`_open_sources` 的近似重复不是纯粹的复制**：读一次版本必须在 `store.reading()` 里读日历、取分区哈希（`p7_reading.py:854-892`），探索版本在运行之后取哈希（`p7_diagnostics.py:1086-1088` 的注释自己写明「not to be copied by the read-once path」）。抽取共用构造器会让两者共用「何时取哈希」的接缝，风险大于收益——U5 建议不合并。
8. **`p7_diagnostics` 的 `explore` 只有一处存储开口**（`_open_sources`，`_explore` 在 `:1059` 调用），所以 Task 1 只接一处；`power` 不读数据、不接运行目录。
9. **登记体不是契约行**：`READING_SCHEMA` 只出现在 `p7_reading.py` 与它的测试（`grep` 全仓），没有 pydantic model、没有 `Literal[.../v1]` 的行级契约；`docs/research/p7-readings/` 不存在。所以 Task 4 加字段不需要迁移，`v1` 可以是第一个格式（U4）。
