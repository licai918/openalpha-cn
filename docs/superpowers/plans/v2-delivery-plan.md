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

除回放以外不改 `src/` 的行为（`145e9c1` 另改了 `cli.py` 的一处 docstring，AST 不变）。e2e 不经过 `ReplayRunner.run`，唯一的接触是模块级导入，所以 `737beac` 上的 e2e 结果仍然适用。

**结果。** 三条通道各自经过独立评审与复审，按 cherry-pick 合进主分支，每次合并后的门禁都全绿。

- **C 通道：台账、回放确定性、发布门、D1。**
  - 提交：
    - 首轮：`211b8ee`、`4402af9`、`54c3284`、`57f50d4`、`1099010`、`17b667b`；
    - 修复轮：`249edd1`、`3d78ef9`、`933603d`、`1f7b417`、`b38d081`；
    - 小修：`73b13cc`、`52efabb`。
  - **回放确定性。** 每个 case 的第二遍改在新 engine 与空的内存存储上重算，两遍由同一份配置构造；已存结果与重算结果不一致时，按实情标注。
    - 实测：rationale 读时钟的 agent，修复前冻结语料 300/300，修复后 171/300。
    - 这是本批 `src/` 唯一的行为改动。
  - **冻结语料。** 改为检验「前视证据在加载时即被拒绝」，替换原先恒为 0 的计数。
  - **发布门。** 四类拒绝都在临时 git 仓库里逐条驱动，不再写本 checkout。临时仓库剔除 `GIT_*`，并中和 git 模板与全局 excludes。
  - **台账。**
    - notes 里的 node-id、锚点与裸测试名都必须真实存在。这条检查抓出了 MODEL-001、BOUND-003、BOUND-005、OPS-024，以及三个失效的测试名。
    - OPS-009、BOUND-004、BT-003、AGENT-009 改为引用会失败的测试。
    - 另有七行按代码改正或收窄。
  - **D1。** 断言与 Python 版本无关；大于 0o377 的八进制转义能定位；人造 token 序列在每个解释器上都钉住 3.12 的三处分支。
- **A 通道：图、Windows 换行、链邻 / AKShare、证据、前视。**
  - 提交：
    - 首轮：`01e4ec2`、`b631bcd`、`7068aa6`、`dc50a26`、`4a37161`；
    - 修复轮九个：`5d48a56` 至 `c99b46b`；
    - 小修：`8f30e3e`、`7284fb4`、`1618188`、`0ccc873`；
    - 收尾：`7caedd5`。
  - **Windows 换行。** 生成器写文件时传 `newline="\n"`，另加模拟 Windows 换行的同步测试。
  - **图的文字。** api-05、brain-01/02/03 按代码改写；归因守卫开始读图。
  - **新守卫。**
    - 证据：身份字段逐个扰动实测；Retry-After 必须有读取方；EvidenceLookupTool 必须有出厂导入方。
    - 链邻与 AKShare：AST 前提看得见注册表、partial 与别名；读四份文档、图，以及 `docs/api/chainlin-data.zh-CN.md`。
    - 前视：前提为 `ReplayCorpus.load` 拒绝前视语料。
  - **其他。** `.env.example` 写明每个凭据由谁读取；链邻合约测试的夹具改用三个互不相同的时刻。
- **B 通道：面向用户的文字与文字守卫。**
  - 提交：
    - 首轮经三次 rebase，连同修复共 14 个：`a475b0d` 至 `56b5a6c`；
    - 最后一轮：`8167931`、`d914f7b`、`778bddc`、`6456589`；
    - 委员会小修：`20fec55`。
  - **守卫。**
    - 用量守卫不再豁免能力声称与用量声称，能力选择另起一个守卫。
    - 过时数字守卫从代码、台账与 pyproject 推出 17 个计数。
    - 退役声称守卫在 `20fec55` 上共 25 条，每条都带从代码读出的前提，并读图的文字。
    - 另加 README 指向检查。
  - **文字。** marketing 改动 40 余节，正文长度仍在 [300, 420]。api-01 的画面改为只有 REST 与工作台进入 FastAPI。
- **执行中发现并修复：测试继承 `GIT_*`（`399127d`）。**
  - **起因。** 一条通道在 `git rebase --exec` 里跑门禁，测试继承了 `GIT_DIR`，把共享的 `.git/config` 写成 `core.bare=true`，并写入测试用的 `user.*`。事后已逐项恢复并核实。
  - **为什么要修。** 用户在 git hook 里跑测试时，会遇到同样的问题。
  - **修法。** `tests/conftest.py` 的 `pytest_configure` 在收集之前剥离全部 `GIT_*`，会话结束时归还。
  - **验证。** 元测试在模拟 hook 的环境下运行会启动 git 的测试，并盯住一个哨兵仓库。
  - 这个 hook 在 e2e 会话里同样生效，但它只动环境变量。
- **控制方的提交。**
  - `7c055ea`：本计划书；
  - `145e9c1`：只改 docstring；
  - `278ed84`：把 `import os` 移到文件顶部；
  - `0ecb16a`、`2f97a63`：导入隔离测试的说明文字与两条断言提示。
- **验证。** 早期 CI `34749055324`（`56b5a6c`）七项全绿：
  - ubuntu 3.11.15 / 3.12.3：各 5905 passed，0 skip，0 warning；
  - Windows 3.11.9 / 3.12.10：各 5900 passed，5 个具名的平台 skip；
  - D1 的多行 f-string 测试在 3.12.3 上通过，C1 在真实 Windows 上确认修好。

D13 整批终审（`final-review-d13.md`，Critical 0 / Important 4 / Minor 11）的发现转入 D14。

### Task 14（D14）：D13 整批终审的修复轮（执行中发现）
- **I-A / I-B / I-C（第 4 条）。**
  - 刚退役的三类说法换了措辞，仍留在五节。
  - 文档说「RunManifest 记录 Prompt」，而 `prompt_versions` 被写死为空。
  - 两份 README 把 `run_cycle` 写成验证、回测、paper、daily 共用的内核。
  - 三者的机制相同：守卫只认被点名的原句，修文时没有把同类说法在全文里再搜一遍。所以本轮除了逐条改正，还要对每一族说法，在四份文档与图里做一遍全文清扫。
- **I-D（第 1 条）。**
  - **现象。** 两条 Windows 腿最后一步 `git diff --exit-code` 打出 4 条 git warning。
  - **原因。** 几个测试在真实 checkout 里就地改写 `src/` 下被跟踪的源文件，再以文本模式写回，Windows 上写回的就是 CRLF。
  - **为什么现在才看到。** 这是批前就有的问题，`74cee0f` 的全绿 run 里已经有。但本批的 Windows 腿一直卡在 C1，直到早期 CI 才走到这一步。
  - **同类问题。** 另有测试在 `src/` 下临时建探针模块；`build_graph` 会把 `.grimp_cache/` 写进仓库根目录。
  - **修法。** 改为在临时副本上运行，并加静态审计与会话级检查。
- **Minor。** 11 条按文件分给 A、B、C 与控制方。
- **分工**（均在隔离 worktree，基线 `20fec55`）：
  - B1：面向用户的文字、退役守卫与图的文字，外加全文清扫；
  - B2：其余文字守卫；
  - A：链邻合约文档与 A 的守卫；
  - C：台账、D1 与两处过时的 docstring；
  - 另一代理：修改写 checkout 的测试。

#### D14 结果

- **合并。** 先合 C、A（含后续）、B2 与「写 checkout 的测试」修复（11 个提交，只改 `tests/`），主分支到 `68a15f3`；随后合 B1 的 15 个提交，主分支到 `f194365`。每次合并前后都跑门禁。
- **声称普查。** 六个只读普查员以 `29e26f3` 为准，逐节通读 marketing 100 节、`README.md`、`README.en.md`、`docs/why-openalpha-cn.zh-CN.md` 与两个图生成器的文字。主表 88 条，另有 21 条补充意见。88 条全部处理，判为不成立 0，悬置 0；另有一条文档级裁定保留并写明依据（`why:5`）。由此发现一族新问题：修订数据的前视，见下节。
- **B1 七轮，15 个提交。** 修轮（守卫先收窄）→ 普查主表 → 改派项与修轮 Minor → 普查评审的 1 Critical / 5 Important / 7 Minor 与台账三行 → 终审两条 Important → 收尾三条 Minor → 提交信息更正与图的几何规则。
- **六轮独立评审加两次图子审计**：0/0/7、1/5/7、0/2/7、0/0/3、0/1/3、0/0/1；图子审计另报 0/2/6。
- **本批查实并改掉的实质错误（举其要）：**
  - 四份文档、`docs/api/data-interface.zh-CN.md` 与功能台账 `OA-TIME-003` 都声称四个时钟共同决定历史可见性，而代码只比较 `available_time`；
  - 「408/429/5xx 重试」，而代码只把六个状态码标为可重试，共 10 处；
  - 「本仓库不存 SignalFrame」，而恢复平面整份保存、一次运行汇总出的帧只存 ID，共 4 处（含 `src/` 两条 limitation）；
  - README 两条命令照抄跑不通（缺参数、地址形状非法），并补上了能抓住这类缺陷的检查；
  - 图上把 `/api/v1` 的路由说成「v2 路由」；
  - 退役守卫自己也抄了一句错话（`ResearchTool` 在 `src/` 无导入者，而 `tools/__init__.py:3` 就导入了）；
  - 十张图的箭头因依赖 SVG 2 的 `context-stroke`，在本机渲染中共 126 个变成纯黑。
- **守卫终态。** 46 条目、208 条退役措辞、339 句真句、42 条 premise。否定处理收到「命中所在的逗号分段」，并把三条真实整句收进 `retired` 作为回归防线。
- **`src/` 改动。** 本批 `src/` 只有 `backtest/replay.py` 的行为改动（早于 B1 各轮），其余全是文字：`cli.py` 的 docstring 示例、`runtime/router.py` 的 detail、`shortlist_view.py` 两条 limitation、`domain/panel_batch.py` 的函数 docstring。AST 比对确认零逻辑改动。
- **合并门禁（`f194365`）：** `tests/unit` 3536；九个文档守卫与四个点名文件、`tests/replay`、链邻合约共 432；五道静态门全 0；台账 185 / 180 / legacy 26 / unknown 0 / unreviewed 0；会话级 checkout 检查零报告，工作区干净，无 `.grimp_cache`。
- **过程教训（供后续批次用）：**
  - 守卫只认被点名的原句，所以每一族都要做全文清扫，而且要由独立的人做第二遍；
  - 模式测试若只喂 `retired` 片段，就测不出「整句上漏检」这类回归，要按分句喂；
  - 几何改动会互相牵连：加宽一张卡片使一条连线的起点落进卡内，是本批自己引入又自己抓到的回归；
  - 按抽样点去量 24u 见方的箭头头部会得出相反的结论，需要整幅逐像素比对。


## 不在本批（需要你决定）
- **修订过的数据，要不要按 as_of 挡掉。**
  - 可见性只比较 `available_time`：`domain/time.py:34-36` 的 `is_visible_at`，以及证据存储的查询 `storage/parquet.py:104`。
  - `revision_time` 晚于 `available_time` 的快照，会被 `evidence/builder.py:134-135` 标上 `revised_after_initial_availability`。这个标记的严重度是 `reduced`（`domain/risk_flag.py:167`），风险门对它给出 reduce，不给 block（`decisions/risk.py:48-51`）。
  - 所以，as_of 之后才修订的版本，在 as_of 当时照样可见，只是带着标记、被降一级。
  - 四份面向用户的文档、`docs/api/data-interface.zh-CN.md` 与功能台账 `OA-TIME-003` 原先都声称四个时钟共同决定可见性，现已全部改为如实描述。
  - 二选一：
    - 保持今天的「标记加降级」；
    - 在可见性判断里加上 `revision_time <= as_of`。若修订前的版本没有另存为独立快照，这样做等于在修订生效之前把整条数据藏起来；它会改变研究与回放的结果，需要重跑 e2e。
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
- **Prompt 版本要不要真正记录。**
  - `RunManifest` 有 `prompt_versions` 字段，但唯一的构造点把它写死成空元组（`runtime/engine.py:136`），`AgentProvenance` 也没有 Prompt 字段。
  - 仓库内的 prompt 由 `code_commit` 钉住；用户自己接入的 prompt 钉不住。
  - 文档已改为不声称记录 Prompt。要不要实现记录，是产品决定。
- **链邻与 AKShare 要不要接进出厂数据路径。**
  - 链邻客户端合同是真的：Bearer 认证、客户端限流、错误分类、冻结合约测试都在。AKShare Adapter 也已实现。
  - 但出厂路径只有 `doctor` 构造它们（`cli.py` 的 `_default_providers()`）：证据构建用 `FileProvider`，面板构建用 `TushareProvider`，REST 只接收调用方送来的批次。
  - 文档已按此如实改写。要不要把它们接进证据或面板的数据路径，是产品决定。
- **链邻只配地址、不配 key 时的探测。**
  - 链邻的 `fetch` 在发请求之前先查 key，缺 key 就抛 `authentication`（`providers/chainlin.py:155-162`），于是 `doctor --probe` 非零退出。
  - 而 `PROBE_FAILURE_STATES` 的 docstring 说 authentication 是「端点拒绝了一个已有的凭据」，缺凭据不应让命令失败（`cli.py:634-655`）。
  - 二选一：缺 key 改报 `configuration`，或让 doctor 区分这两种情况。文档已按今天的行为如实写明。
- **非 Python 验收的粒度。**
  - 台账已经有 `ci-job` 验收类型（`scripts/build_feature_coverage.py:38`，OA-OPS-005 在用）。
  - OA-OPS-002「Frozen install lint test and build pass」正是 `quality.yml` 里 `web` job 的四步；OA-IFACE-006、OA-IFACE-007 的真实覆盖在 web 单测与 Playwright。
  - 所以要决定的是验收粒度，即以整条 CI job 作为一行的验收是否足够，而不是缺类型。定下来之前，这三行留 `legacy-prose`。
- **`scripts/generate_brain_diagrams.mjs` 删不删。**
  - 它写出的五个脑图文件名与 Python 生成器相同，但文字是旧的，其中包括：
    - 已被推翻的「规则 · 因子 · 智能体归因」「验证与三层归因」；
    - EvidenceLookupTool；
    - 仓库里并不存在的类名 `StructuredModelAgent`；
    - 「规划：替代分散第三方接口」「目标：实时获取 / 更精准」这类规划文字。
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
