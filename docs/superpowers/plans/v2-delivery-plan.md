# v2 交付批：全量测试暴露的问题，以及交付前剩下的不实声称

分支 `feat/v2-p0a`，基线 `c6d9ec2`。执行：`superpowers:subagent-driven-development`，
每个任务一个实现 agent（TDD）+ 一次任务评审，最后一次整枝评审。

## 交付标准（每一条都可核对，最后按它逐条判定）

1. **CI 七绿**，且每条腿上**没有一条 warning 来自本仓库自己的代码**；每个 skip 都有具名的、
   平台固有的理由。
2. **e2e 通过**（复用面板须在一个 horizon 的保质期内）。
3. **本地全量非 e2e 与 CI 同一依赖集**（`--all-extras`），无环境性 skip。
4. **面向用户的文档**（`README.md`、`README.en.md`、`docs/why-openalpha-cn.zh-CN.md`、
   marketing 文档）**不声称代码不提供的能力**——已知的每一处都改正。
5. **台账剩余的 `legacy-prose` 行逐行对照过代码**：没有一行描述已不存在的功能。
6. 静态门全 0：`ruff check`、`ruff format --check`、`mypy src`（strict）、`lint-imports`（8/0）、
   `scripts/build_feature_coverage.py --check`。

## 全量测试暴露的问题与根因（已查证）

上一次全量：本地 5638 passed / 1 skipped / 24 warnings；CI 四条 Python 腿 25–32 条 warning、
Windows 各 5 个 skip。

| 现象 | 根因 | 处置 |
|---|---|---|
| 24–32 条 `invalid escape sequence '\|'` | `storage/connection.py:34` 模块 docstring 里的 `\|`，**由 `c7958b3` 引入**。3.11 报 DeprecationWarning、3.12 升为 SyntaxWarning，CPython 的走向是硬错误——届时 `openalpha_cn.storage` 无法导入。`pyproject.toml` **没有任何 `filterwarnings`**，所以这一类能静默穿过 CI | D1 |
| 5 条报在 `<unknown>:1` | 五个 AST 扫描测试 `ast.parse(path.read_text(...))` **不带 `filename=`**；tests/ 里这类调用 22 处带、11 处不带 | D3 |
| ubuntu 3.12 独有 7 条 `use of fork() may lead to deadlocks` | `test_panel_store.py` 两个测试与 `test_migrations.py` 一个测试用**平台默认**的 `multiprocessing` 启动方式：Linux 是 `fork`，macOS/Windows 是 `spawn`。四处文档（`test_migrations.py:828`、`test_panel_store.py:186`、`conftest.py:130`、`conftest.py:277`）都按 spawn 语义写，**在 Linux 上都不成立**——`test_catalog_persists_across_a_fresh_process_...` 声称"全新进程"，fork 出来的却是父进程的内存副本 | D2 |
| Windows 5 个 skip | `test_paper.py:265`（sockaddr_in 仅 BSD/Linux）、`test_offline_suite.py` 四处（无 `sendmsg`、无 `AF_UNIX`） | **合理，不修** |
| 本地 1 个 numpy skip | `numpy` 经 `akshare` extra → pandas 传递进来；CI 用 `--all-extras`，本地 venv 没带 | D4（控制方） |

## 任务

### Task 1（D1）：非法转义，与一条让这一类在 CI 里变红的守卫
修 `connection.py:34`（`\|` → `\\|`，渲染后的 `__doc__` 一字不变——已核实该 docstring 只有这一个
反斜杠）。**先写守卫看它红**：在 `tests/unit/test_repository_assets.py` 已有的"遍历 src/tests/scripts
全部 `.py`"惯用法旁，加一条以 warnings-as-errors 编译每个文件的测试。已测：全仓库仅此一个文件违规。

### Task 2（D2）：进程测试显式用 `spawn`，让四处文档在所有平台同时为真
三个测试改用 `multiprocessing.get_context("spawn")`。这同时消除 3.12 的 fork 警告、消除
多线程进程里 fork 的死锁隐患、让"全新进程"在 Linux 上也名副其实，并提前适配 3.14 默认启动方式的变化。
然后核对那四处文档，确认现在逐字为真。**守卫**：一条 AST 审计，要求 tests/ 里每个
`multiprocessing.Process`/`Queue`/`Barrier` 都经显式 context 构造——这一类缺陷本仓库已出现过
（「把这台机器当成不变量」，V2-P5-063）。

### Task 3（D3）：11 处 `ast.parse` 补 `filename=`
纯可诊断性修复，不改行为。**验证方式**：临时重新引入一个非法转义，确认 warning 报出真实路径而非
`<unknown>`，再还原。不为此加守卫——它是风格，不是缺陷类。

### Task 4（D4）：本地 venv 与 CI 同一依赖集（控制方执行）
`uv sync --locked --all-extras --dev`。验证 numpy skip 消失。

### Task 5（D5）：`README.md:1152`「最多 1000 个」
对照 `batch_contracts.py` 的 `MAX_BATCH_ITEMS` 核实后改正；顺带核对同段其他数字。

### Task 6（D6）：`docs/why-openalpha-cn.zh-CN.md:26` 与 `docs/specs/v2/openalpha-cn-v2-prd.md:314`
仍声称 rule/factor/agent/model 归因。why-openalpha 被两个 README 头部链接，属面向用户。
PRD 的 S65 行标 `IN`，而同表 S54 已标降级——对齐。

### Task 7（D7）：marketing 文档约 23 处用量/成本追踪声称
与 C8 同一形状。**注意**：`test_marketing_pack_contains_100_distinct_source_grounded_plans`
钉死每节去空白后长度在 [300,420]，按该测试自己的口径 `len("".join(body.split()))` 测量。

### Task 8（D8）：台账剩余 74 行 `legacy-prose` 逐行对照代码
分四批（约 19 行一批），串行（同一个 CSV）。每行判定：属实 / 部分属实 / 描述已不存在的功能。
后两类改正；属实且有真覆盖测试的可顺带迁移。此前约 22 行已被 C4、C7 查过，可引用其报告快速复核。
棘轮 `UNVALIDATED_ACCEPTANCE_ROWS` 按实测数更新，保持 `==`。

## 不在本批（需要你决定）
- `TERMINAL_STATUSES` 缺一个表示「已窄化」的值——状态表的设计决定，不是一行能顺手发明的。
- FK 守卫三条残留（等量替换、别名×不守命名约定相乘、九个 store 只断言 pragma 读回值）——已具名记录，风险低。
