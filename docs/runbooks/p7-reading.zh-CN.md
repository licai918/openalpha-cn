# P7 唯一一次验证窗口读（`V2-P7-024`）运行手册

> **状态：读数尚未进行。** 本手册是 `V2-P7-024` 那唯一一次读的操作页；没有用户的明确「开始」不执行。**本手册本身**不改源码、不写登记、不读窗口、不发网络请求。所有面板夹具在测试时生成（硬规则 6）；本手册说的是真实面板上的操作。**请求预算 0 次网络请求**：读在 `cp -c` 克隆上做纯计算，`trade_cal` 是本地 parquet。

## 1. 这是什么

- `V2-P7-024` 的**唯一一次验证窗口读**：`2022-01-04..2023-12-29`（`scripts/research/p7_windows.py:213-216`）。一个读数覆盖整个窗口，第一天是窗口的第一个会话，最后一天是最后一个会话；读一部分也会用掉整个窗口。
- **第一次样本外读数，不是确认性证据**（`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §5.2 的 G-0）：该窗口被 P6 的前 N 名策略用过，不完全干净。措辞仍是「候选」；这次读本身不证明任何事。
- **一次、不可逆。** 认领写进 `docs/research/p7-readings/`，**认领一写出，窗口即算已读**；只有在这些认领**提交进本仓库的 refs** 之后，另一个 clone 或 worktree 才不能把它再读一次。读之前写好的停止规则判这次读数；读完之后**不在该窗口上调参、不改停止规则、不改先验期数**。
- **0 次网络请求。** 面板是 `cp -c` 的只读克隆，读的是克隆上的本地 parquet；命令不抓取、不推送。

## 2. 前提（逐条，全部成立才做）

按顺序核对；任何一条不成立就停下，不要开始。

1. **仓库不浅。** 浅克隆的历史停在切点，早先提交的认领看不见，`read_once` 会拒绝（退出码 3）。

   ```bash
   git rev-parse --is-shallow-repository   # 必须是 false
   git fetch --unshallow                   # 只有上一条不是 false 时才需要
   ```

   `git fetch --unshallow` 需要访问 origin（网络），**先取得用户同意**再执行；这次读数本身仍然是 0 次网络请求。

2. **没有 grafts 文件。** grafts 改变 git 报告的父提交，而仓库仍说自己不是浅克隆；`read_once` 拒绝（退出码 3）。替换 ref 不用删：工具以 `--no-replace-objects` 忽略它们。

   ```bash
   test ! -e "$(git rev-parse --git-path info/grafts)"   # 必须成立
   git replace --convert-graft-file                      # 只有上一条不成立时才需要
   ```

3. **登记的 `code_commit` 与当前 `HEAD` 在四个受绑定路径上没有任何差异，且工作树干净。** 登记记录的是 `git rev-parse HEAD`；`read_once` 比较的是 `registry.REGISTERED_PATHS` 的四个路径——`src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`（`p7_reading.py:1280-1284`，调用 `registry._refuse_a_changed_source` 时**没有**传 `also_bound`）。`scripts/daily_selection.py` 与 `scripts/forward_report.py` 由每日命令/周报的准入（`also_bound`）绑定，**不由这次读数检查**（控制者已知的代码缺口，另立 issue）。

   ```bash
   git rev-parse HEAD
   git status --porcelain -- src scripts/research pyproject.toml uv.lock   # 必须为空
   ```

4. **面板是 `cp -c` 的只读克隆，放在运行目录的 `panel/` 子目录里。** 存储的位置是 `<runtime-dir>/panel`（`scripts/research/p7_windows.py:428-430`），所以 `<克隆根>` 是一个**新建的空目录**，面板克隆到 `<克隆根>/panel`。克隆根**不在** `~/openalpha-research` 里，也**不在任何检出里**（`scripts/research/p7_windows.py:101-169`）。只读连接也会在目录库旁建 `catalog.duckdb.lock` 与 `catalog.duckdb.gate`，所以不能直接指向 P6 的研究根或主检出。

   ```bash
   mkdir -p <克隆根> && cp -c -R ~/openalpha-research/panel <克隆根>/panel   # <克隆根> 是新建的空目录；APFS 上与源同卷
   ```

5. **读之前登记已写好、已提交、字节与 `HEAD` 相同。** 登记由第 3 节的 `register` 写出；`read_once` 拒绝磁盘字节与 `HEAD` 字节不一致的登记（退出码 3），也拒绝不在 `docs/research/p7-readings/` 里的登记。

   ```bash
   git diff --exit-code HEAD -- docs/research/p7-readings/v2-p7-024-validation.registration.json
   git ls-tree -r --name-only HEAD -- docs/research/p7-readings
   ```

6. **`--as-of` 不早于窗口最后一个交易日的发布时刻。** 窗口末日 `2023-12-29` 的 16:30（上海时间）之前，最后几期的标签还不可知，`read_once` 拒绝（`ReadingError`，退出码 2）。`--as-of-reason` 是一行，说明为什么这个 `as_of` 就是这次读数所处的知识。`--exchange` 必须是 `SSE`：诊断读的就是这个日历；`SZSE` 在参数校验时就被拒绝（退出码 2）。
7. **解释器环境。** 用本仓库 `uv sync --all-extras --dev` 建出的 `.venv`。注意：`uv.lock` 在绑定里只证明锁文件没变，**不证明运行环境与它一致**（`scripts/research/p7_reading.py:162-163`）。

## 3. 程序

**程序性前提：用户逐项确认整行命令。** R4 仍未定（`docs/superpowers/plans/2026-11-07-v2-p7-024-stop-rule-and-reading.md:71-78`）：下面这一行是**控制者按既有设计值提出的候选命令**，不是已批准的记录。每个请求与配置值都逐项给出，代码里没有默认值可以顶替。**在 `register` 之前、以及在 `read` 之前，必须有用户对「这一整行、逐项」的明确确认**；用户确认的与这一行不同，就先把本节改成那一行**再**登记。登记写一次之后不能改写成另一组值（同样字节返回同一个 digest，不同字节被拒）。

先核对参数（`register --dry-run` **不写登记、不写认领**；它仍要求 `--repo`、`--runtime-dir` 与全部请求/配置值；只读连接仍会在面板目录旁建 `catalog.duckdb.lock` 与 `catalog.duckdb.gate`）：

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv run --no-sync "$PWD/.venv/bin/python" scripts/research/p7_read.py register \
  --repo "$PWD" \
  --runtime-dir <克隆根> \
  --window validation \
  --reading v2-p7-024-validation \
  --issue V2-P7-024 \
  --as-of <ISO 时刻> \
  --as-of-reason "<一行：为什么这个 as_of 是这次读数所处的知识>" \
  --stride 20 \
  --horizons 20 \
  --tails 1 2 3 \
  --score composite \
  --book '{"seed": 0}' \
  --transform cross_section_standard/v1 \
  --min-coverage 0.5 \
  --max-carry-sessions 250 \
  --exchange SSE \
  --expected-periods 24 \
  --dry-run
```

必填与可选的界限：`--reading`、`--issue`、`--dry-run` 可省略（`--reading` 默认 `v2-p7-024-validation`，`--issue` 默认 `V2-P7-024`）；primary 读数的 `--expected-periods` 必填；**其余每一个请求与配置选项都是必填**。`--exchange SSE` 是这次读的硬要求。

0. **取得用户对这一整行命令的逐项确认**（R4）：确认之前不登记、不读。确认之后这一行就是登记的请求与配置；它不再改。
1. **读 `--dry-run` 的打印，核对精确期数。** stdout 里依次是：

   ```text
   window validation 2022-01-04..2023-12-29
   the panel's calendar makes exactly N non-overlapping periods
   the data-free bound is 26 periods
   the registration expects 24
   the registration that would be written is <仓库绝对路径>/docs/research/p7-readings/v2-p7-024-validation.registration.json
   dry run: nothing was written and nothing was claimed
   ```

   打印的登记路径是**绝对路径**。**N 必须等于 24。** 24 是**计划记录的先验**：484 个会话、步长 20、h20 的算术（`ceil(484 / 20) = 25` 个信号日，最后一个信号日的标签出口越过窗口末日、被禁运，剩 24 个非重叠期）。**484 这个数在本仓库无法离线复核**（见 `docs/superpowers/plans/2026-11-07-v2-p7-024-stop-rule-and-reading.md` 的「矛盾与发现」第 4 条，与 `docs/superpowers/plans/2026-11-07-p7-dev-batch-2-closeout.md` §6 第 6 条）；工具按面板自己的日历自算，算出来不是 24 就停。`26` 只是数据无关的星期几上界，不是真实日历的期数。
   **N 不等于 24 就停下来报告**：面板日历与计划不一致，或面板不完整。**不得把 24 改成 N**，不得调参、不得继续登记——那会把一次不可逆的读建立在错的日历上。退出码 4 就是这种情况，什么都没写、什么都没认领。

2. **正式登记**（同一条命令，去掉 `--dry-run`）。stdout 打印登记路径、它的 sha256，以及一行 `commit it before the read; the read refuses bytes not in HEAD`。

3. **提交登记。** 登记必须先在 `HEAD` 里：

   ```bash
   git add docs/research/p7-readings/v2-p7-024-validation.registration.json
   git commit -m "docs(V2-P7-024): the validation reading's registration, written before the read"
   ```

4. **读一次。** 这一步不可逆；执行前再核对第 2 节全部前提与第 3 步的提交。

   ```bash
   UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv run --no-sync "$PWD/.venv/bin/python" scripts/research/p7_read.py read \
     --repo "$PWD" \
     --runtime-dir <克隆根> \
     --registration "$PWD/docs/research/p7-readings/v2-p7-024-validation.registration.json"
   ```

5. **立刻提交四个文件，并让这个提交留在本仓库的 refs 里。** 不提交的认领可以被删掉，窗口可以被再读；另一个 clone 或 worktree 只有拿得到这个提交才看得到认领（`scripts/research/p7_reading.py:122-142`）。

   ```bash
   git add docs/research/p7-readings/validation.window-claim.json \
           docs/research/p7-readings/v2-p7-024-validation.claim.json \
           docs/research/p7-readings/v2-p7-024-validation.result.json \
           docs/research/p7-readings/v2-p7-024-validation.result.txt
   git commit -m "docs(V2-P7-024): the validation window's one read, its claim and its result"
   ```

## 4. 判读

**退出码**（`scripts/research/p7_read.py:14-25`）：

| 退出码 | 含义 | 已经花掉了什么 |
|---|---|---|
| 0 | 登记写出，或读数完成并写出结果；停止规则的判定照常打印 | 读完成则窗口已认领 |
| 2 | 用法，或登记写不成的 `ReadingError`（参数、窗口名、交换所、`as_of`、登记体）；**认领之后**，写结果时遇到 `OSError` 也是 2 | 认领之前：什么都没写、没认领；认领之后：认领已写出，窗口已花掉 |
| 3 | 认领之前被拒（运行目录、登记未提交、代码变了、外来包或脚本、已认领、`second_use` 在验证窗口之前、浅克隆或 grafts、预检问题）；**认领之后**，测量返回的文档不绑定认领（`ReadingConfigurationError`）或认领文件被另一个读抢先（`FileExistsError`）也是 3 | 认领之前：什么都没花掉；认领之后：认领留在磁盘上，窗口已花掉 |
| 4 | 日历算出的精确期数不是登记的先验数：停、报告 | 什么都没写、没认领 |

**退出 2 或 3 之后，先看 `docs/research/p7-readings/` 有没有认领文件。** 只要存在 `validation.window-claim.json` 或 `v2-p7-024-validation.claim.json`，窗口就已经花掉：**不要因为退出码删认领，也不要再读**；把退出码、消息与认领文件一起报告给用户。只有在确认没有任何认领文件时，2 或 3 才等于「什么都没花掉」。

**读完之后（退出码 0）停止规则的三种结论**（措辞常量与判据见 `scripts/research/p7_reading.py:275-287`、`:1025-1090`）：

| 结论 | 打印的措辞 | G-0 的后果 |
|---|---|---|
| **触发**（`triggered`） | `the stop rule written before the read is triggered: under G-0 the forward clock is not started, and the user is told there is no evidence` | G-0 不过：**不启动前向时钟**，如实告诉用户没有证据 |
| **未触发**（`not triggered`） | `the stop rule written before the read is not triggered: this permits V2-P7-006 to proceed and is not evidence` | 只**允许** `V2-P7-006` 继续；**不是证据**，措辞不升级 |
| **不可测**（`not measurable`） | `the reading cannot be measured: the stop rule decided nothing, and this is not a reading of no evidence -- it is its own conclusion, and what follows is the user's to say` | 它自己的结论：**既不是「没有证据」，也不是放行**；不启动前向时钟，下一步由用户说 |

**退出码 0 不等于通过。** 它只说读数完成并写出了结果；结论看上面三行。`不可测` 的原因是某个指标的可读期数少于 `minimum_periods`（等于 `expected_periods`）、没有有限的均值，**或者测量被数据拒绝、文档里根本没有这个汇总**；它**绝不**读成「没有证据」。

## 5. 顺序

- **验证窗口先读。** 它的 `<validation>.window-claim.json` 必须**已提交**、并且能从某个 ref 或未过期的 reflog 到达，`second_use` 才能读；否则 `read_once` 以 `ValidationNotSpentError` 拒绝（退出码 3，`scripts/research/p7_reading.py:1517-1562`）。反方向不检查：验证窗口的读从不看 2024–2026。
- **二次使用段是次要、非确认性的。** `2024-01-02..2026-09-24` 是 P6 已经花掉的保留期，P7 是在看过它之后设计的；它的读数只用于描述，不参与 G-0。它没有停止规则、没有 `expected_periods`（secondary 登记带这两样会被拒），是否读由用户决定。
- **不要反过来做。** 先读二次使用段、或在验证窗口的认领提交之前读它，都会让 2024–2026 的结果影响 2022–2023 的判定。

## 6. 失败即关闭

`read_once` 的拒绝都不写任何东西；但从认领开始，读就算花掉了。下面每一种都要知道「窗口还算不算已读」：

| 情形 | 结果 | 为什么 |
|---|---|---|
| 浅克隆 | 退出码 3，读不了 | 历史停在切点，切点之前的认领看不见；`git fetch --unshallow` 之后再来 |
| 有 grafts 文件 | 退出码 3，读不了 | 它改变 git 报告的父提交，而仓库仍说自己不浅；`git replace --convert-graft-file` 之后再来 |
| 认领从未提交 | 删掉未提交的认领，窗口就被还回来 | 认领文件是窗口规则的唯一见证；**必须提交并留在 refs 里** |
| 另一个 clone 或 worktree | 看不到本仓库的认领，可以再读一次 | 认领不在 git 里就不跟着仓库走；本仓库的提交必须在另一处 fetch 得到 |
| 认领提交后文件被删 | 窗口仍算已读 | `git log --all`、reflog、merge 的每个父提交都能找到它；只有认领的提交不再被任何 ref 或未过期 reflog 到达、或被 prune、或历史被改写并 gc 之后才会丢 |
| 某个认领文件读不出来 | 只要它的提交还被任何 ref 或未过期 reflog 到达，**每个窗口**都读不了 | 它可能点名任何一个窗口；今天没有逃生口（设计中的办法尚未实现，`scripts/research/p7_reading.py:143-151`） |
| 登记未提交、或磁盘字节与 `HEAD` 不同 | 退出码 3，什么都没花掉 | 读只接受 `HEAD` 里的登记字节 |
| 登记之后受绑定代码变了 | 退出码 3，什么都没花掉 | `src/`、`scripts/research/`、`pyproject.toml`、`uv.lock` 必须与登记的 `code_commit` 一致；两个 `also_bound` 脚本只由每日/周报准入绑定，**不由这次读数检查** |
| 运行目录在 P6 研究根或任何检出里 | 退出码 3，什么都没花掉 | 只读连接也会在目录库旁写锁与 gate 文件；用 `cp -c` 的克隆 |
| 这个窗口已经被认领过 | 退出码 3，不能再读 | 一个窗口只读一次，不论读数叫什么名字；磁盘上、或任何 ref 或未过期 reflog 上曾经提交过的认领都算 |
| 预检发现面板缺年、或停止规则不可达 | 退出码 3，什么都没花掉 | 这些检查都在认领之前；缺分区、信号日没有因子构建等留给测量，测量之后读就算花掉了 |

**它没有确立什么**（`scripts/research/p7_reading.py:122-181`）：认领绑定的是一条**程序**，不是对数据的访问——自己构造 `WindowCappedStore`、直接调用 `read_once` 的测量、或直接打开 parquet 的代码，都能不经过认领去读同一个窗口；认领只能说这条路径走过一次，不能说明没有别的路径。读数是在登记的 `as_of` 那一刻的知识下重算的，不是在每个会话的发布时刻重放，所以之后发布的订正或更名会进入那些会话的分数。一次验证窗口的读是**第一次样本外读数，不是确认性证据**；二次使用段的读连这个都不是。
