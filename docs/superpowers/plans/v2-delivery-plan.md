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
| 5 条报在 `<unknown>:1` | 五个 AST 扫描测试 `ast.parse(path.read_text(...))` **不带 `filename=`**。（计划写成时记作"tests/ 里 22 处带、11 处不带"——那是控制方数错了：按 AST 实测，D3 之前全仓 `ast.parse` 51 处带、34 处不带。） | D3 |
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
多线程进程里 fork 的死锁隐患、让"全新进程"在 Linux 上也名副其实，并提前适配 3.14 默认启动方式的变化
（3.14 在 Linux 上改为 `forkserver`，不是 `spawn`）。然后核对那四处文档，确认现在逐字为真。
**守卫**：一条 AST 审计——这一类缺陷本仓库已出现过（「把这台机器当成不变量」，V2-P5-063）。
最终形态（两轮评审后）：把导入绑定解析成点分路径，扫描 src/tests/scripts；原语只能经 `get_context("spawn")`
返回的对象构造，`get_context` 必须带字面量 `"spawn"`，`set_start_method` 一律标记，`ProcessPoolExecutor`
须给非 `None` 的 `mp_context`，`multiprocessing.dummy` 除外；能与不能看见的边界写在审计的 docstring 里。

### Task 3（D3）：11 处 `ast.parse` 补 `filename=`
纯可诊断性修复，不改行为。**验证方式**：临时重新引入一个非法转义，确认 warning 报出真实路径而非
`<unknown>`，再还原。不为此加守卫——它是风格，不是缺陷类。（执行中 AST 扫描另见 3 处多文件循环同形，
一并补上；另 14 处各只解析一个写死文件、6 处解析内存串，刻意不改。）

### Task 4（D4）：本地 venv 与 CI 同一依赖集（控制方执行）
`uv sync --locked --all-extras --dev`。验证 numpy skip 消失。

### Task 5（D5）：`README.md:1152`「最多 1000 个」
对照 `batch_contracts.py` 的 `MAX_BATCH_ITEMS` 核实后改正；顺带核对同段其他数字。

### Task 6（D6）：`docs/why-openalpha-cn.zh-CN.md:26` 与 `docs/specs/v2/openalpha-cn-v2-prd.md:314`
仍声称 rule/factor/agent/model 归因。why-openalpha 由 `README.md` 链接（:5、:1109；计划原写"被两个
README 头部链接"有误，`README.en.md` 不链接它），属面向用户。
PRD 的 S65 行标 `IN`，而同表 S54 已标降级——对齐。

### Task 7（D7）：marketing 文档约 23 处用量/成本追踪声称
与 C8 同一形状。**注意**：`test_marketing_pack_contains_100_distinct_source_grounded_plans`
钉死每节去空白后长度在 [300,420]，按该测试自己的口径 `len("".join(body.split()))` 测量。

### Task 8（D8）：台账剩余 74 行 `legacy-prose` 逐行对照代码
分四批（约 19 行一批），串行（同一个 CSV）。每行判定：属实 / 部分属实 / 描述已不存在的功能。
后两类改正；属实且有真覆盖测试的可顺带迁移。此前约 22 行已被 C4、C7 查过，可引用其报告快速复核。
棘轮 `UNVALIDATED_ACCEPTANCE_ROWS` 按实测数更新，保持 `==`。

### Task 9（D9）：部署文档声称的容器安全属性，逐条要有东西验证

由收尾阶段查 CI 非 Python 腿发现。起因是 container 腿的一条 warning，查下去是一组没被验证的安全声称。

**(a) 那条 warning。** `Dockerfile:58-59`：
`RUN addgroup --system --gid 10001 openalpha && adduser --system --uid 10001 --ingroup openalpha --home /nonexistent openalpha`。
`--system`（系统账户，uid 应 ≤ `SYS_UID_MAX` 999）与 `--uid 10001`（刻意避开宿主系统 uid 的安全做法）自相矛盾，
CI 构建日志：`useradd warning: openalpha's uid 10001 is greater than SYS_UID_MAX 999`。`addgroup` 那一步未报警，
但同样是"系统组 + 高 gid"的矛盾。runtime 基础镜像 `python:3.12-slim-bookworm`（Debian）。
Debian 的 `adduser --system` 默认给 `/usr/sbin/nologin`、禁用密码、不建 home——**去掉 `--system` 时这些必须显式补回**，
否则安全姿态会悄悄退化。必须保持不变：uid 10001、gid 10001、home `/nonexistent`（不创建）、shell nologin、无密码。

**(b) 文档声称、却没有任何东西验证的属性。** `docs/deployment/production.zh-CN.md` §8「安全边界」：
| 声称 | `deploy/compose.yml` 里有 | 有测试钉住 |
|---|---|---|
| UID/GID `10001` | Dockerfile `USER 10001:10001`（compose 无 `user:`） | **无** |
| 只读根文件系统 | `read_only: true`（:11） | 子串断言（`test_repository_assets.py:539`） |
| `cap_drop: ALL` | ✓（:57-58） | **无** |
| `no-new-privileges:true` | ✓（:59-60） | 子串断言（:540） |
| 仅 `/data` 可写、`/tmp` 受限 tmpfs | ✓（:39、:55-56） | `/data` 有（:538），**tmpfs 无** |
全仓库搜 `10001`、`cap_drop`、`tmpfs`、`security_opt`：tests/ 与 scripts/ 里零命中（`10001` 唯一命中是无关的 `min_cross_section=10001`）。
`scripts/verify_compose_recovery.py`（CI container 任务的唯一运行时验证）**不检查用户身份**。

**现有断言的弱点**：`"read_only: true" in compose` 是子串匹配——注释掉写成 `# read_only: true` 或挪到别处都照绿。
要么解析 YAML，要么如实写明这层局限；由实现者权衡并说明。

**TDD 顺序**：先写刻画测试钉住现有姿态（今天应为绿）→ 变异确认它会红（uid 改 10002、删 `cap_drop`）→
再去掉 `--system` 矛盾（刻画测试保持绿、构建日志里 warning 消失）。
运行时验证（真实镜像上 `id -u`/`id -g` = 10001、根文件系统不可写、`/proc/self/status` 的 `CapEff` 为 0）应放进
`verify_compose_recovery.py`：它通过 `_compose()` 驱动唯一服务 `openalpha`。本地 Docker 29.2.1 可用、75 GiB 空闲，可本地构建前后对比。
（实现时实测推翻了这里的两条建议：去掉 `cap_drop` 后 `CapEff` 仍为 0、只有 `CapBnd` 变，故改查全部五个能力集；
去掉 `read_only` 后写 `/` 仍失败、只是错误从 EROFS 变成 EACCES，故要求 EROFS。评审又补一条：除 `/data` 外，
每个持久挂载都必须是 `ro`，按挂载本身判定，不看挂载点目录能否写入。）

### Task 10（D10）：REST 研究请求吞掉了证据的防篡改拒绝（执行中发现）
由 D8 第 1 块核对台账行 OA-EVID-003 时发现。`ResearchApiRequest.verify_serialized_evidence` 用
`except ValueError` 包住 `parse_serialized_evidence`，而 pydantic 的 `ValidationError` 是 `ValueError`
子类——结构错误与防篡改拒绝一起被吞。篡改条目回退后仍以 `extra_forbidden` 被拒：**不是完整性漏洞，是一条
安全相关检查的报错失真**。修为只原样上抛新增的 `SerializedEvidenceMismatchError`，与 CLI 逐字一致；
批量路由用同一个请求模型，一并修好；其余响应逐字不变。

### Task 11（D11）：证据标识符的覆盖面写精确，拒绝带下标（执行中发现）
D10 评审发现 `evidence_id`/`content_hash` 只覆盖 `subject`、`kind`、`source_id`、`available_time`
与载荷，docstring 却写"由来源与内容派生"。逐字段写明（并用导出器重生成已提交的 schema），
`docs/api/http.md` 写明这条拒绝；拒绝消息带条目下标，且与条目顺序无关。**不改标识符方案**（见下）。

### Task 12（D12）：把"模型调用"写成出厂能力的声称，以及两个守卫说多了的地方（执行中发现）
D7 实现者报出、控制方核实：**出厂路径从不发起模型调用**——`src/`、`scripts/` 里没有模型客户端的构造点，
没有代码读取模型 Provider 的环境变量。于是 README「模型治理」、marketing 里"408/429/5xx 分类重试 / 能力注册"
的说法、以及嵌在 README 的 brain-03 脑图，按出厂行为读都不成立（数据源客户端的限流重试是真的，逐句区分）；
`deploy/compose.yml` 与 `.env.example` 里的模型变量是没有读者的配置——如实注明，**不删**（见下）。
同一任务修 D6/D7 联合评审的三条：用量守卫漏掉 D7 自己删掉的两句声称；前提测试对七种注入写法不敏感；
两份白名单按摘录豁免整句，追加进去的真声称照样通过——改为按完整规范化子句精确钉住。

**结果。**
- `2f489b0`：前提测试按模块、作用域、类别计数，并解析别名；两份白名单按整句钉住。
- `8d53724`：README、README.en、why-openalpha 与 marketing 里有关模型调用的说法，改为按出厂行为描述；五个模型变量如实注明没有读取方，未删。
- `d4ef5e4`：经生成器重新生成内嵌脑图与 API 图的文字；三个守卫开始读图；新增生成器与 SVG 的逐字节同步测试。

D12 独立评审（Important 2 / Minor 8）与整批终审（Critical 1 / Important 5 / Minor 10）的发现，一并转入 D13。

### Task 13（D13）：D12 评审与整批终审的修复轮（执行中发现）
整批终审的结论是未就绪。

- **Critical。** D12 新增的图同步测试在两条 Windows 腿上必然失败。
  - 两个生成器的 `write_text` 没传 `newline="\n"`，在 Windows 上写出 CRLF；已提交的 SVG 按 `.gitattributes` 是 LF。
  - 最终 CI `34732831479` 的结果印证了这一点：两条 Windows 腿各只红这一条，其余五项全绿。
- **Important。**
  - 内嵌图仍画着代码不提供的能力：api-05 的因子 / Agent 归因；brain-02 的证据身份公式、Retry-After 与只读证据工具。而归因守卫不读图。
  - 链邻被写成统一数据入口，出厂路径却只有 `doctor` 构造它。
  - 台账有三行所引的测试在结构上不可能失败。其中回放确定性的根因在 `src/`：`ReplayRunner.run` 两次 `run_cycle`
    共用同一 engine、recovery store 与 `run_id`，第二次直接复用存下的结果。
  - 另有一批与代码不符的说法和过时的数字。
  - 本计划的待决项有缺漏、有表述不准。

按文件与句子独占，切成三条并行通道，均在隔离的 worktree 里进行：
- A：图、Windows 换行与链邻；
- B：面向用户的文字与文字守卫；
- C：台账、回放确定性、发布门与 D1 测试卫生。

除回放以外不改 `src/`。e2e 不经过 `ReplayRunner.run`，唯一的接触是模块级导入，所以 `737beac` 上的 e2e 结果仍然适用。

## 不在本批（需要你决定）
- **`TERMINAL_STATUSES`**（台账 `coverage_status` 的取值集合，`scripts/build_feature_coverage.py:18`）缺一个表示「已窄化」的值。
  这是状态表的设计决定，不是一行能顺手发明的。
- **FK 守卫三条残留**（等量替换、别名×不守命名约定相乘、九个 store 只断言 pragma 读回值）：已具名记录，风险低。
- **证据标识符的覆盖面。** `evidence_id`/`content_hash` 不覆盖 `summary`、`source_uri`、`source_license`、
  `redistribution` 与另外三个时钟。D10 评审实测的后果：只改存储 Parquet 里的许可/再分发两列，读取时不报完整性错误，
  `export_report` 照样放出受限载荷；同一文件以不同许可证重建时，存储静默保留旧的许可文本。没有远程利用路径。
  修法：A 不动身份，补文档并在导出与存储处加廉价防护；B 另加一个覆盖全部字段的摘要（改 schema、迁移存储）；
  C 把这些字段并入 `evidence_id`（所有 id 都会变，连带 `signal_id`、`report_id` 与存储文件名）。
- **模型调用要不要接进出厂路径。**
  - 今天的出厂路径不调用模型。
  - 模型客户端（分类重试、用量账本）是 `openalpha_cn.models` 里的库代码。`OpenAlphaSDK` 没有任何模型客户端方法，要用户自己写代码构造、接线。
  - 其中的能力注册表与能力元数据，即使接上也没有读者：Provider 从不查询 `ModelRegistry`，也不按能力选择端点。
  - `deploy/compose.yml`、`.env.example` 里的模型 Provider 变量没有读取方。要么接线，要么删掉这些变量，二选一。
  - 删变量须连带改 `tests/unit/test_repository_assets.py::test_compose_passes_through_declared_provider_credentials`，它钉住了这五个变量的透传。
- **链邻要不要接进出厂数据路径。**
  - 链邻客户端合同是真的：Bearer 认证、客户端限流、错误分类、冻结合约测试都在。
  - 但出厂路径只有 `doctor` 构造它（`cli.py` 的 `_default_providers()`）：证据构建用 `FileProvider`，面板构建用 `TushareProvider`，REST 只接收调用方送来的批次。
  - 文档已按此如实改写。要不要把它接进证据或面板的数据路径，是产品决定。
- **非 Python 验收的粒度。**
  - 台账已经有 `ci-job` 验收类型（`scripts/build_feature_coverage.py:38`，OA-OPS-005 在用）。
  - OA-OPS-002「Frozen install lint test and build pass」正是 `quality.yml` 里 `web` job 的四步；OA-IFACE-006、OA-IFACE-007 的真实覆盖在 web 单测与 Playwright。
  - 所以要决定的是验收粒度，即以整条 CI job 作为一行的验收是否足够，而不是缺类型。定下来之前，这三行留 `legacy-prose`。
- **`scripts/generate_brain_diagrams.mjs` 删不删。**
  - 它写出的五个脑图文件名与 Python 生成器相同，文字却是旧的，其中有已被推翻的「规则 · 因子 · 智能体归因」「验证与三层归因」、EvidenceLookupTool、「统一替代入口」。
  - 全仓库没有任何地方引用它。
  - 有人运行它时，图同步测试会变红，但它本身仍会画回旧说法。
- **Python 支持范围。**
  - `pyproject.toml` 写的是 `requires-python = ">=3.11"`，没有上限。
  - CI 的 Python 测试矩阵只有 3.11 与 3.12（ubuntu 与 windows，共四腿；实测补丁版本为 Windows 3.11.9、ubuntu 3.12.3、Windows 3.12.10）。
  - D1 的一条测试曾只在 3.14.5 上变红（D13 修）。
  - 两个选择：给 CI 加 3.14 腿，或给 `requires-python` 设上限。
- **遗留（已具名，低风险）。**
  - `.dockerignore` 第 15–25 行的文件名模式（`__pycache__`、`*.pyc`、`*.db`、`*.sqlite*`、`*.parquet`、`*.exe`、`.env.*` 等）不带 `**/`，按 Docker 的规则只匹配构建上下文根目录。
    - 后果：`COPY src/ ./src/`（`Dockerfile:19`）会把本地 `src/` 下嵌套的 `__pycache__` 带进构建阶段，`COPY web/ ./`（`Dockerfile:7`）也会带进 `web/` 下嵌套的同类文件。
    - CI 的全新 checkout 里没有这些文件，也没有任何文档声称它们会被排除。
  - 回放报告的「前视违规」计数在结构上恒为 0，因为含前视证据的语料在加载时就被整体拒绝。
    - Web 回放面板（`web/src/components/ReplayPanel/ReplayPanel.tsx:50-51`）仍展示这个计数。
    - `web/src/contractState.ts:214` 里「计数大于 0 即判定结果不可用」的分支不可达。
