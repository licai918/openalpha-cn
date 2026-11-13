# 智能体晚间命令（`V2-P7-013` / `V2-P7-014`）运行手册

> **状态：命令已交付，C 的登记还没有写，前向窗口没有开始，所以今天不要跑真实的晚间。**
> C 的登记 `docs/research/p7-c-registration.json` 是 L5 时**由用户提交**的一个文件；在那之前命令的第 1 步
> 会按名拒绝。它还要等 Step 0 自己的登记（`V2-P7-006b`）与 Step 0 的晚间在跑——Step 0 的每日命令自
> 2026-10-08 起暂停，今天没有任何一个会话有 Step 0 已 file 的记录，第 2 步对任何一天都会拒绝。
> 受闸步骤 L1–L6 **全部没有运行**（见「登记之前必须按顺序做的事」），`DEEPSEEK_V4_1_FLASH` 仍是占位、
> `verified_on = None`，**真钱闸门仍关着**。
>
> 这条命令**不交易、不改持仓、不产出可下的单**。它打印的一切是**候选**；通过测试不是研究证据，
> 一次晚间跑通也不是。

## 这个命令是什么

`scripts/agent_evening.py`（方案 C 批次 2 任务 2.5，批次 3 补 `--dry-week` 与登记绑定）在**每个工作日晚上
20:30** 之后跑一次，把当日 Step 0 幸存者的智能体分数 file 成前向预测。它做六件事，按 `AGENT_STEPS` 的顺序：

1. **registration**：读 C 自己的登记（`kind = agent_forward/v1`），经 `registry.admit_registered_code`
   校验「跑着的代码就是登记绑定的代码」（绑定 `scripts/agent_evening.py` 与 `scripts/agent_report.py`
   两个文件）。
2. **step0_record**：以**只读**方式打开 Step 0 的预测库与面板：该会话必须恰好有**一条**受绑定、准时
   （`registered_at < 下一次开盘 09:15`）的 `survivor_book_composite/v1` 记录，且 Step 0 当日日志完整
   （有 `result`），否则拒绝并什么都不写。
3. **evidence**：用 Step 0 的已存面板在会话自己的 16:30 推出当日的涨停/炸板/连板事件，写进 **C 自己的**
   证据库；已存过的会话重跑不再追加（证据按 `evidence_id` 内容寻址）。
4. **rule_family**：规则家族在主集 ∪ 覆盖集上逐名跑一次引擎，按 `score_of` 映射，file 主集、覆盖集
   两条批次；确定性——`--no-llm` 时两次运行逐字节相同。
5. **llm_family**：`--no-llm` 时跳过并记 `no_record: llm_disabled`；否则同一个引擎带
   `StructuredSignalAgent`（V2 提示词、事实载荷、`max_attempts=2`）逐名跑模型，受「本晚 ≤600 次请求」
   与「23:30 上海墙钟期限」约束。
6. **summary**：写一次的日志与打印的候选行、计数、tokens 与请求数。

它**不读 C 的纸面账簿**（C 没有纸面账簿），不写 Step 0 的任何文件，从不改登记，从不超出请求上限。
C 的读数由**另一条**命令 `scripts/agent_report.py` 出（D8；Step 0 的周报脚本不因 C 而修改，见 L6）。

## 前提

1. **Step 0 的登记已提交，且当晚已 file。** 第 2 步要的那条记录必须受 Step 0 的登记绑定、在下一交易日
   09:15 之前登记；Step 0 的当日日志
   （`<Step 0 runtime>/daily_survivor/<config_id 前 16 位>/<会话>.json`）必须有 `result`，否则第 2 步按名
   拒绝（`Step 0 has not filed <会话>`）并且**零写入**。Step 0 的登记用 `--step0-registration`
   （默认 `docs/research/p7-registration.json`）读；Step 0 的运行目录用 `--step0-runtime-dir`，**只读**。
2. **C 的登记已提交、磁盘字节与 `HEAD` 相同。** 登记的 `config.kind` 必须是 `agent_forward/v1`；
   `config.salt`、`config.primary_size` 无默认值（抽样参数是登记的一部分，命令缺省即按名拒绝）。
   第 1 步还会用**与报告同一个共享函数**核对登记的锚点（登记提交时刻 + 交易日历，规则见 L5 第 4 条）；
   不一致即按名拒绝、**零请求零写入**。
3. **跑着的代码就是登记绑定的代码。** 绑定 `scripts/agent_evening.py` 与 `scripts/agent_report.py`
   （`AGENT_SCRIPTS`）；`HEAD` 与工作区里的这些字节都必须等于登记的 `code_commit`。登记文件不存在或读不出、
   登记自相矛盾：退出 2；登记未提交/与 `HEAD` 不同、或代码不是登记的代码：退出 3。
4. **环境**：`.env` 里的 `DEEPSEEK_API_KEY` **只在真要发请求时才需要**；已 file 完整一天的崩溃重跑、
   或 `--no-llm` 的一天，不需要 key（回放模式）。`--env-file` 用相对的 `.env`（uv 会按空格拆分路径；
   主检出的路径含空格）。
5. **C 的运行目录是它自己的新目录**，不与 Step 0 共用，也**不能带 D7 干跑周的标记**
   （`c-dry-week-scratch.json`）；第 1 步与 `--pin-worktree` 都对带标记的目录按名拒绝——否则 C 的 120 个
   前向会话会 file 进一个 L6 报告会整份拒绝的 scratch 目录。C 的东西（`predictions/`、`evidence/`、
   `model_exchanges/`、`state.sqlite3/`、`agent_evening/`）只写在这个目录里。
6. **定时任务不安装。** `--print-launchd` 只打印，`--pin-worktree` 只建一个分离 HEAD 的 worktree 并在里面
   离线建环境；`~/Library` 下什么都不动。

## 手动运行

在 C 的**钉住 worktree** 里用它自己的 `.venv` 跑（路径以你的为准）：

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv run --no-sync --env-file .env "$PWD/.venv/bin/python" scripts/agent_evening.py --runtime-dir <C 运行目录> --step0-runtime-dir <Step 0 运行目录>
```

两份登记都用默认路径（`docs/research/p7-c-registration.json` 与 `docs/research/p7-registration.json`）；
要换就读 `--registration` / `--step0-registration`。**今天这条命令会在第 1 步拒绝**（C 的登记还不存在）。

## 选项

| 选项 | 作用 |
|---|---|
| `--runtime-dir DIR` | C 的运行目录：预测库、证据库、交换库、用量账与晚间日志都在这里 |
| `--step0-runtime-dir DIR` | Step 0 的运行目录，**只读**；缺它时第 2 步按名拒绝 |
| `--step0-registration PATH` | Step 0 的登记文件，默认 `docs/research/p7-registration.json` |
| `--registration PATH` | C 的登记文件，默认 `docs/research/p7-c-registration.json` |
| `--repo DIR` | 校验登记与代码绑定的 git 检出，默认脚本所在的检出 |
| `--as-of ISO` | 当天的钉住时钟（默认墙钟）；墙钟仍决定迟到判定、模型期限与 `predicted_at` |
| `--no-llm` | 只 file 规则家族；模型家族记 `no_record: llm_disabled` |
| `--max-model-requests N` | 本晚模型请求硬上限，默认 600（D7 的预算） |
| `--deadline HH:MM` | 模型请求的上海墙钟期限，默认 `23:30` |
| `--dry-week` | D7 干跑周模式：见「干跑周」，不需要 C 的登记，file 零预测 |
| `--pin-worktree DIR` | 在 C 登记的提交上创建或移动分离 HEAD 的 worktree，再离线建它自己的环境并链接 `.env`；脏/外来 worktree、或带 D7 干跑标记的运行目录，都按名拒绝 |
| `--uv PATH` | `--pin-worktree` 用的 uv |
| `--print-launchd` | 只打印工作日 20:30 的 launchd 配置；需要 `--worktree` 与 `--step0-runtime-dir`；**不安装、不建目录** |
| `--worktree DIR` | 打印的 launchd 要跑的钉住 worktree |
| `--log-dir DIR` | 打印的 launchd 把 stdout/stderr 写到哪里，默认 `<worktree>/logs` |

退出码：**0** 成功；**2** 拒绝（登记文件不存在/读不出/自相矛盾、某一步按名拒绝、用法错误）；
**3** 登记未提交或与 `HEAD` 不同、或代码不是登记的代码。某一步失败时标准错误写
`step N (名称) failed: <原因>`，之后的步骤不执行。整晚没有「部分写入」：
失败的那一步要么什么都没写，要么写的是可幂等重算的同一份内容。

## 六步与失败时的行为

| 步 | 做什么 | 失败时 |
|---|---|---|
| 1 registration | 读 C 的登记并准入；校验绑定代码，读 `salt`/`primary_size`，读 Step 0 的登记；在读写当天之前拒绝与 Step 0 种子耦合的盐、拒绝带 D7 干跑标记的运行目录、**用共享函数核对登记的锚点** | 登记文件不存在/读不出退出 2；登记未提交/与 `HEAD` 不同、或代码不是登记的代码退出 3；干跑标记的目录、锚点不是提交后严格意义上的第一个交易日，都按名拒绝（退出 2，零请求零写入） |
| 2 step0_record | 找恰好一条受绑定、准时的 Step 0 合成分记录；用 `record_scope` 重建当日幸存者；从 Step 0 日志的 `result` 读持仓与观察名单 | 按名拒绝 `Step 0 has not filed <会话>`，零写入 |
| 3 evidence | `limit_events_on`（as_of = 会话 16:30）→ 事件快照 → 拒绝被重述的会话 → 追加到 C 的证据库 | 步拒绝；什么都不写；同一会话重跑不追加 |
| 4 rule_family | 主集 ∪ 覆盖集逐名 `run_cycle`；两条批次 file；覆盖集为空时不 file 覆盖批次、日志记 `coverage_absent: "empty_set"` | 步拒绝（引擎/记录错误）；规则家族要么完整 file，要么没有这一晚 |
| 5 llm_family | `--no-llm` 跳过；否则逐名跑模型，上限与期限由内层传输装饰器执行；每次交换的 `response.model`、`system_fingerprint` 入日志 | 传输失败/凭据缺失/配置错误/上限/期限 → 该家族-集合**不 file**，日志记 `no_record: <原因>`（第 8 节）；两次尝试后的无效回答是编码弃权 `model_invalid_output` |
| 6 summary | 写一次的日志（`tmp + os.replace`）、打印候选行与计数 | 日志写不出或与已存日志不同：步拒绝，第一份账留下 |

## 输出怎么读

打印的行（全部是**候选**，不是建议）：

- `候选 / candidate: the agent evening of <会话>`；
- `not traded: these are candidate signals only; nothing was traded, and a passing suite is not
  evidence that the family has an increment`；
- `scored N, abstained M, failed 0`；模型家族当天没 file 时是
  `scored N, abstained M, not scored (family not filed: <原因>): K`，`K` 是两个集合里**去重后**没拿到分数的
  名字数；
- `model requests  R this run (E held for the session)`：本晚实际会到达端点的请求数（含 provider 重试），
  与当天已存交换数；
- `tokens <输入> in / <输出> out / <总数> total`：本晚已存交换的用量；
- `agent_llm_structured/v1: filed` 或 `no_record (<原因>)`；
- `journal written: <路径>` 或 `journal unchanged: <路径>`。

**日志**在 `<C 运行目录>/agent_evening/<会话>.json`（schema `openalpha-agent-evening/v1`），**写一次**：
第 6 步先写临时文件再 `os.replace`，读者看不到半份。日志记的是**当天的事实**而不是某一次运行：登记的
`config_id`/sha256/`salt`/`primary_size`、Step 0 的记录与见证、证据 id、两个评分集合、两个家族的块、
汇总计数与请求数；模型家族没 file 时是 `no_record` + 具名原因。**重跑重算出相同字节**：
`unchanged` 不重写；算出不同的日志按名拒绝，第一份账留下。

## 重跑、崩溃与已登记的一天

- **重跑已 file 的一天**：不花请求（已存交换回放），不受 23:30 期限，日志逐字节相同——包括「批次已 file、
  第 6 步之前崩了」的重跑。两个评分集的批次都在库里时命令以回放模式装配 provider，**不需要
  `DEEPSEEK_API_KEY`**；回放中遇到未记录的请求是具名拒绝，不是 key 错误。
- **当天记了 `no_record` 的一天**：日志就是当天的账，重跑按同样的原因重放那个决定，不再发请求。
- **补跑过去的一天**：第 2 步仍读得到 Step 0 当天那条记录（它当年是准时的），但 file 的截止是
  **下一交易日 09:15**：晚于它的写入按 `ForwardSessionRefused` 拒绝、零写入——C 不会为过去的一天补造记录。
  所以晚间要在 20:30 之后、下一交易日 09:15 之前跑完。
- **外来的登记**：库里别的登记在同一会话写的 C 家族批次不算「已 file 的一天」（判定要求声明里的
  登记摘要等于 C 自己的摘要），重跑不会把它们当成 C 的记录。

## 请求预算与期限（D7 的预算）

- 每晚至多 **600 次**模型请求（`--max-model-requests`，默认 600），墙钟期限 **23:30 上海**
  （`--deadline`，默认 `23:30`）。
- 上限与期限只花在**会到达端点**的请求上：一次 HTTP 尝试计一次（provider 的重试也计）；已存交换的
  重放零成本、不受期限。被上限拦住的请求不发送也不计数。
- 用尽上限、过了期限、传输失败、凭据缺失或配置错误：**该家族-集合当天不 file**，日志记具名原因
  （`request_cap_reached`、`deadline_reached`、`transport_error`、`credential_missing`、
  `configuration_error`），规则家族照常 file。部署故障是缺口，**不是答案**。

## 登记之前必须按顺序做的事（L1–L6）

每一步都等用户明确 go。**全部未运行**：

| 步骤 | 请求 | 前置 | 产出 | 状态 |
|---|---|---|---|---|
| L1 `V2-P7-010` 探针 live | ≤ 300 次 Tushare（硬上限） | 探针工具已合入；预算文件已按下面的**完整字面量**就位 | 只含元数据的报告；不落盘数据 | **未运行** |
| L2 DeepSeek 端点核实 | 0 | 用户确认事实文件 | `DEEPSEEK_V4_1_FLASH` 填值、`verified_on` 设置；四份面向用户文档的模型/用量措辞与两个散文守卫同步改 | **未运行**（值仍是占位） |
| L3 DeepSeek live 探针 | ≤ 5 次模型请求 | L2；`.env` 里的 `DEEPSEEK_API_KEY` | scratch 运行目录里的模型/指纹/用量/空白/JSON 有效性报告 | **未运行** |
| L4 LLM 干跑周（D7） | ≤ 5 × 600 模型请求；0 Tushare | `006b` 已登记、Step 0 晚间在跑；批次 2–3 已合入；L3 ok | 只含运维指标的报告；记录只留在 scratch 目录 | **未运行** |
| L5 C 登记 + 钉住 | 0 | 功效可测（或 D1 有裁定）；`docs/research/p7-c-protocol.md` 已提交；L4 通过 | `p7-c-registration.json`（**用户提交**）；`--pin-worktree`；打印 launchd；C 时钟开始 | **未运行** |
| L6 G-C 读数 | 0 | C 记录满 120 个前向会话 | `scripts/agent_report.py` 跑一次；候选 / 不可测 | **未运行**（窗口未开始） |

**L1 的 300 次上限已经花掉 20 次。** 2026-11-12 的修复轮里发生过一次受闸之外的 `--live` 运行：用假 token
向 `api.tushare.pro` 发出 **20 次**被拒绝的请求尝试（无数据返回、无仓库文件；见批次 2 收尾记录 §7.2）。
保守记账把这 20 次计入上限，所以 **L1 的预算文件必须从一开始就是下面这个完整字面量**——探针拒绝没有
`schema` 键的预算文件，缺文件又按 0 读，少了 `schema` 就等于把 20 次记账丢掉：

```json
{"schema": "openalpha-p7-event-probe-budget/v1", "spent": 20}
```

**不要自己创建那个 live 文件**（等 L1 的 go）；上面只是它必须长的样子。它们没有返回数据、没有进任何
面板/证据/研究记录，因此不影响任何研究结论。

## 干跑周（D7、L4）：`--dry-week`

登记之前先跑至多 **5 个晚上**，**只测运维**。这是**规定的做法**，不是可选项：

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv run --no-sync --env-file .env "$PWD/.venv/bin/python" scripts/agent_evening.py --dry-week --runtime-dir <scratch 运行目录> --step0-runtime-dir <Step 0 运行目录>
```

- **一个一次性的 scratch 运行目录**，与 C 的正式运行目录分开；`--dry-week` 在该目录根上写标记文件
  `c-dry-week-scratch.json`（schema `openalpha-c-dry-week-scratch/v1`，`evenings` 列出已开始的会话），
  **这正是它永远不进确认性样本的凭据**。标记在**第一个模型请求之前**写/刷新，所以一个失败或中途崩溃的
  晚上也留下标记：失败或崩溃的运行目录不会因为「没跑完」而被当成一个干净的 C 前向目录。
- 每晚在 `<scratch 运行目录>/dry_week/<会话>.json` 写一份**运维日志**（schema
  `openalpha-c-dry-week-evening/v1`），键：`schema`、`session`、`status`、`names`、`requests`、`scored`、
  `abstained`、`not_routed`、`invalid_output`、`blank_responses`、`invalid_json_responses`、
  `transport_failures`、`latency_ms {p50, p95, mean}`、`tokens {input, output, total}`、`cap`、`deadline`。
  它**没有**分数、IC、收益字段。
- **不需要 C 的登记**（它还没有写）；仍需要 `--step0-runtime-dir`，因为干跑周也读 Step 0 当日已 file
  的一天与它的证据。`--dry-week` 拒绝一个已经有登记晚间日志或已 file 预测记录的运行目录——它**不会**
  把 C 的正式运行目录标成一次性 scratch。
- 每晚至多 **600 次**模型请求（`--max-model-requests` 在干跑周里是 0..600，默认 600）、同一个
  23:30 上海期限；**至多 5 个已开始的晚上**——在读完 Step 0 当日记录、发出第一个请求之前就算开始；
  **失败、超上限、过期限、中途崩溃的晚上都计入**（不能靠失败重来把 5 个上限变成更多），第 6 个已开始的
  晚上按名拒绝；**已记录过的那个晚上重跑也拒绝**（一个晚上只测一次）。连 Step 0 当日记录都读不到的晚上
  没有花过请求，不算一次测量、不消耗一个 scratch 晚上。
- **file 零预测、写零 C 晚间日志与零 C 证据**。它只测：失败率、空白率、弃权率、无效输出率、延迟、tokens。
  **绝不算任何 IC、任何收益、任何结论词**；干跑周的任何数字都不是证据，也不进协议文档。
- 这些记录**永远不被读成 C 的前向样本**：`scripts/agent_report.py` 与 `strategy_agent_evaluation.evaluate`
  对带该标记的运行目录**按名拒绝**（标记读不出也是拒绝，失败即关闭）。**正式晚间与 `--pin-worktree`
  也在第 1 步拒绝带标记的运行目录**：否则 C 会把 120 个前向会话 file 进一个 L6 会整份拒绝的目录。
- 干跑周跑完后 **C 才登记（L5）**；顺序不能倒过来。

## L5：写 C 的登记、钉住 worktree、打印 launchd

1. **功效先跑**（零请求，只读已存面板；D1）。命令与表格见 `docs/research/p7-c-protocol.md` §6；
   把打印的两行粘贴进协议 §6.3，替代那里的「未运行」。要写登记时给功效命令加 `--json <路径>`，
   那份 `openalpha-p7-c-power/v1` 报告就是后面的 `--power-report`。命令只带**弃权率**这一个规划输入：

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" "$PWD/.venv/bin/python" scripts/p7_c_power.py \
     --panel-dir <已存面板目录> \
     --step0-registration docs/research/p7-registration.json \
     --first <首个会话 YYYY-MM-DD> --last <最后会话 YYYY-MM-DD> \
     --abstention-rate 0.10 \
     --json <功效报告 JSON>
   ```

   **`sd_between` 冻结在 0.15，没有命令行开关，也不能用 D7 的数覆盖**：D7 只测运维、绝不算 IC，给不了
   单会话 IC 的离散度，C 跑起来之前也没有测量来源（用户 2026-11-13 的裁决）。D7 能给 L5 的规划输入至多是
   **弃权率**（`--abstention-rate`）。
   **后果要提前知道**：可检出均值的下界约 `2.4865 × 0.15 / sqrt(120) ≈ 0.034`，大于 0.02 的规划门槛，
   所以只要观测历史会话 ≥ 20 且投影可测会话 ≥ 60，n300 与 census **两行都会是 `not_testable`
   （原因 `detectable_above_threshold`）**——n300 更不可能（采样项本身已超门槛上界）。这是如实的规划结果，
   不是缺陷：**D1 的预期结果是「两行都不可测」**，用户在 L5 要么用 `--register-untestable` 把 C 登记为
   **探索性候选**，要么不登记。**登记为探索性候选时，120 个前向会话的读数是描述性的，不是一次检验**：
   **不被读成通过/不通过**、不把 p 值写成结论、不升级措辞。
2. **先提交协议文件。** `docs/research/p7-c-protocol.md` 必须已提交且工作区里没有未提交改动：写入器把它的
   **已提交字节 sha256** 记进登记（`settings.agent_forward_protocol_document`），并拒绝未提交或有改动的
   协议文件。若第 1 步把功效表粘贴进了协议，先把那一处改动按 `V2-P7-013`/`014` 提交，再跑写入器。
   **登记提交之后本文件完全冻结**：连追加 §14 也会改变绑定字节，协议修订是另一份文件加新登记。
3. **写登记（先 `--dry-run`）**：

   ```bash
   PYTHONPATH="$PWD/src" "$PWD/.venv/bin/python" scripts/p7_c_register.py \
     --salt <人工选定的盐> \
     --step0-registration docs/research/p7-registration.json \
     --power-report <功效报告 JSON> \
     --anchor <提交时刻之后严格意义上的第一个交易日 YYYY-MM-DD> \
     --dry-run
   ```

   `--dry-run` 把规范化正文写到标准输出，再打印 `config_id`、`sha256` 与
   `未写入：--dry-run only prints; nothing was written`——**什么都不写**；确认之后去掉 `--dry-run`
   才写 `docs/research/p7-c-registration.json`（`--path` 默认就是它；成功时打印 `wrote <路径>`、
   `config_id`、`sha256` 与下一句「只 stage/commit 这一个文件」）。**写入器从不 stage、从不 commit**。
   它写出的 `settings` 带五个封闭的块（评估器与报告都按名读、都有失败即关闭的语义）：
   `agent_forward_protocol`（第 5 节的判据）、`agent_forward_power`（D1 的样本与判定、
   `register_untestable`、功效报告 sha256）、`agent_forward_step0`（C 评估的 Step 0 登记）、
   `agent_forward_anchor`（窗口锚点）与 `agent_forward_protocol_document`（协议文件的已提交 sha256）。
4. **`--anchor` 的规则**：它是**C 的登记提交时刻之后严格意义上的第一个交易日**——交易日 d 的 16:30 上海
   发布时刻严格晚于提交时刻：周末/节假日提交 → 下一个交易日；交易日 16:30 之前提交 → 当天；16:30 之后 →
   下一个交易日。
   - **写入器**能检查的部分：锚点必须是工作日，并且它的 **16:30 发布时刻在写入时还没有过去**（一个已经
     过去的锚点一定不是「提交之后」的会话，因为提交只会更晚）；它接受一个未来工作日（以及今天 16:30 之前）。
     写入时提交时刻还不存在，所以写入器给不出最后一句判断。
   - **登记的晚间在第 1 步核对**：与报告**同一个共享函数**（登记的提交时刻 + 交易日历）比较，不一致就按名
     拒绝，**零请求、零写入**——排错的提交时间在**第一天**就暴露，而不是六个月后的 L6。
   - **报告在 L6 再核对一次**，用同一个函数；不一致同样按名拒绝。

   所以：先计划好登记提交的时间，再据此选锚点；不要在登记之后改锚点。
5. **写入器会拒绝**（每条退出 2、什么都不写）：DeepSeek 端点未核实（`verified_on` 仍是 `None`——所以 L2
   之后才能写）、盐与 Step 0 的种子相同或由它派生、Step 0 的登记未提交/提交后被改过/是别的 `kind`、
   功效报告缺失或不合 `openalpha-p7-c-power/v1`、功效报告的 `source` 为空或不是本写入器绑定的那份 Step 0
   登记（`registration_sha256`、`config_id`、路径不符）、报告的 `planning` 窗口/记分下限/可测下限/检验水平/
   目标功效/可检出门槛/最小观测会话数不等于已采纳常量、`planning.sd_between` 不等于冻结的 0.15、
   `planning.abstention_rate` 不在 [0, 1) 内、行里的 `detectable_mean` 与它自己的 sd/periods 算出的不一致
   （写入器会从 `sd` 与 `periods` 重算可检出均值，再按行字段重算判定，与报告的 `verdict` 不一致即拒绝）、
   报告里没有任何一行 `testable` 而没有传 `--register-untestable`（两行都不可测时，登记为「不可测」还是
   不登记，是用户的选择）、锚点不合上一条、协议文件未提交或有改动、`--path` 不在 `docs/research/*.json`
   里、该路径上已有一份不同的登记、受绑定代码或两个 C 脚本有未提交改动、写入的字节与打印的 sha256 不符
   （刚写的文件删掉）。写入器按 D1 自动选样本（`n300` 可测则 `primary_size = 300`；只有普查可测则
   `primary_size = 1000000`，即 `CENSUS_DRAW_SIZE`，抽样函数在容量 ≥ 当日幸存者数时返回普查），
   **没有** `--sample` 参数。
6. **用户提交登记文件**（提交信息点名 `V2-P7-013`/`014`）；提交本身就是 C 时钟的起点，锚点是它之后严格
   意义上的第一个交易日。
7. **钉住 worktree**（从提交了登记的检出）：

   ```bash
   "$PWD/.venv/bin/python" scripts/agent_evening.py --pin-worktree <目录> --repo "$PWD"
   ```

   它在**登记的提交**上建分离 HEAD 的 worktree，再用它自己的 `uv.lock` **离线**建环境，并把 `.env`
   符号链接过去；已有的目录必须是同一仓库的干净 worktree，带 D7 干跑标记的运行目录也按名拒绝，
   **从不覆盖**。
8. **打印 launchd**（只打印、不安装）：

   ```bash
   "$PWD/.venv/bin/python" scripts/agent_evening.py --print-launchd --worktree <钉住 worktree> --runtime-dir <C 运行目录> --step0-runtime-dir <Step 0 运行目录>
   ```

   任务是工作日 20:30（label `com.openalpha.agent-evening`），参数里带绝对的 `--step0-runtime-dir`。
   安装与否是运维的决定，本仓库只打印。

**第一个晚间必须在 `--anchor` 指定的那个会话上跑**：窗口的位置从锚点起数，锚点之前的记录不进窗口；
第一个晚上的会话必须与锚点一致，**晚间第 1 步与 L6 的报告都会核对**。

## L6：读数报告

```bash
"$PWD/.venv/bin/python" scripts/agent_report.py --runtime-dir <C 运行目录> --step0-runtime-dir <Step 0 运行目录>
```

`--registration` 默认 `docs/research/p7-c-registration.json`；`--repo` 默认脚本所在的检出；
`--as-of` 钉住报告的时钟（默认墙钟），`--through` 把窗口截到某个会话（`YYYY-MM-DD`）；
`--json PATH` 额外把同一份事实原子地写成一份 JSON 文档（不加只打印、不写文件）。
**它不需要 `--step0-registration`**：它评估的 Step 0 登记（路径、`config_id`、sha256、种子）写在 C 的登记
`settings.agent_forward_step0` 里，窗口锚点在 `settings.agent_forward_anchor` 里，D1 选的样本在
`settings.agent_forward_power` 里——报告读登记的这几个块，不另设命令行参数。

退出码：**0** 打印（并按 `--json` 写文件）；**2** 按名拒绝——带 D7 干跑标记的运行目录（在打开任何东西之前
就拒绝）、未提交/读不出的登记、`kind` 不是 `agent_forward/v1`、C 的登记冻结的 Step 0
`registration_sha256`/`config_id`/`seed` 与 Step 0 的登记不符、或评估器拒绝。**拒绝时不打印半份报告、
不写文件。**

**怎么读**：

- 先给**候选**措辞（not traded、通过的运行不是证据）与登记身份、窗口（锚点、`through`、`as-of`）；
  然后是见证与缺失的记录（缺失逐条给评估器的原因）、样本数 `n`、功效（0.8 功效下可检出的均值，设计的性质）
  与登记里的 D1 判定；再是弃权率（读 LLM 主集记录）、模型别名变化（C 的晚间日志里出现的
  `(response.model, system_fingerprint)` 身份、是否跨会话变化、哪些应有而缺失的日志）。
  **弃权率与模型别名只统计 `anchor_session <= session <= through` 的记录**：它们与主臂同一个窗口，
  不读窗口之前或 `--through` 之后的记录。
- **锚点核对**：报告用登记提交的时刻精确核对登记的锚点（提交之后严格意义上的第一个交易日），
  不一致就按名拒绝——不会把错位的锚点当成窗口的一部分继续算。**第一晚的第 1 步用同一个共享函数做同一个
  检查**（不一致即拒绝、零请求零写入），所以排错的提交时间在第一天就暴露，不用等到 L6。
- 窗口满、每个位置到期之前**没有 p 值、没有结论词**，只有计数与描述性均值；窗口满足之后才印那一次检验
  （均值、双侧与单侧读数、检验水平、精确/随机）与五种结论之一。
- 五种结论的固定措辞：`not_yet`（未满/未到期）、`candidate_increment`（候选的增量）、
  `no_increment_detected`（没有检出增量）、`futility_stop`（中期无效性停止，sticky）、
  `not_measurable`（**不可测**，用评估器自己的措辞，**绝不读成「没有证据」**）。
- 主检验只有一条（LLM 家族、主集、h=1、单侧、α=0.05）；探索性块（规则家族、h=5、覆盖集）每一臂都标
  `EXPLORATORY`，给均值与 Benjamini–Yekutieli 的 `q` 读数（「低于 q」/「等于或高于 q」），只在主窗口满足
  之后检验，**永不与主臂混池、永不升级**。
- 报告总是给样本数（期数、可测会话）与可检出均值；可检出均值是**设计的性质**，不是观测功效、不是结论。
- 最后印出这次评估采用的 D5 规则（中期形式与单侧读法）。
- 模型别名变化**列为一行变化**，不因此重登记；已 file 的答案不被改写。
- 结论词只到**候选**。一次单侧检验通过不等于效应存在，也不等于智能体层有可交易的价值。

## 已知局限与开放项

1. **C 的登记还不存在，前向窗口没开始**；L1、L2、L3、L4、L5、L6 全部未运行。今天跑晚间会在第 1 步拒绝，
   Step 0 今天也没有任何已 file 的会话（Step 0 的每日命令仍暂停）。
2. **`DEEPSEEK_V4_1_FLASH` 是占位**：model、base_url、`structured_output_mode`、输出上限、价格都没有值，
   `verified_on = None`。未核实时 `llm_provider` 按名拒绝真实调用；这让「出厂路径不花一次模型请求」
   今天仍然成立，但 L2 填 `verified_on` 的那次提交必须同时改写四份面向用户文档的模型/用量措辞，
   并退役或反转两个散文守卫（有一个绊线测试钉住这件事）。
3. **别名移动的重复记录**（批次 2 收尾记录 §6 第 2 条，未修复）：模型 id 是声明的一部分，别名一变声明就变，
   同一天可能多出一条声明不同的记录。评估器对同一会话同一 (family, scoring_set) 的重复记录按名拒绝；
   加固方向（已 file 则跳过模型步骤并回放日志）只记录、未实现。
4. **评估器的协议块用仓库登记的两个常量**：`bootstrap_samples = 100000`（`scripts/research/grid.py` 的
   `PROTOCOL_BOOTSTRAP_SAMPLES`）与 `random_seed = 20260926`（`PROTOCOL_RANDOM_SEED`），由写入器硬编码进
   登记；它们**不是** C 的抽样盐（盐是另一个登记值，与 Step 0 的种子耦合即拒绝）。
5. **C 的脚本是登记绑定的一部分**：`scripts/agent_report.py` 与 `scripts/agent_evening.py` 在登记提交之后
   改任何字节，第 1 步准入就拒绝——修报告要走新登记，不是热修。
6. **2026-11-12 的 20 次被拒 Tushare 尝试**计入 L1 的 300 次上限（预算文件从
   `{"schema": "openalpha-p7-event-probe-budget/v1", "spent": 20}` 开始），是本仓库的一次过程违规记录，
   不是 C 的请求；详见批次 2 收尾记录 §7.2。
7. **2027-01-04 之前的硬闸门**属于 Step 0（登记簿/年份分区），不属于 C；但它决定 C 的时钟最早能什么时候
   开始，所以排在一起看。
8. **真钱闸门仍关着**：C 不交易，本手册的任何输出都不是下单建议；打印出来的单（来自 Step 0 的晚间命令）
   一律标「这些单不要下」。
9. **D1 的预期结果是「两行都不可测」**：`sd_between` 冻结在 0.15（没有测量来源、L5 不得覆盖），可检出均值
   的下界 ≈0.034 > 0.02，所以 C 计划以**探索性候选**登记（`--register-untestable`），或者不登记；登记为
   探索性候选时，120 个前向会话的读数是**描述性的**，不是一次检验——**不被读成通过/不通过**，也不升级措辞。
   已登记的检验本身不变（均值 > 0 且单侧 p ≤ 0.05），但设计登记为不可测时，它的读数不能被写成检验结果。

## 修复轮 1（2026-11-13，评审 9 条意见）

初版评审是 Spec FAIL / CHANGES REQUESTED。本轮的正文与行为更正（设计、顺序与闸门不变；C 的登记仍未写出，
前向窗口未开始）：

- **F3 锚点**：改为「提交时刻之后严格意义上的第一个交易日」（交易日 16:30 发布时刻严格晚于提交时刻；
  周末/节假日提交 → 下一个交易日，16:30 前提交 → 当天，16:30 后 → 下一个交易日）。写入器只检查工作日与
  不早于今天（上海），允许未来工作日，删去旧的「星期一日 / 16 个日历日」；L6 报告用提交时刻精确核对，
  不一致即拒绝。
- **F4 功效**：两分量 sd `min(1, sqrt(sd_between^2 + 1/(n-1)))`（`sd_between` 是非抽样部分的规划假设 0.15），
  判定 = 观测历史会话 ≥ 20 且投影可测会话 ≥ 60 且可检出均值 ≤ 0.02；每行打印 sd、可检出均值与 0.02 阈值；
  not_testable 的原因有 `too_few_observed_sessions` / `below_measurable_floor` /
  `detectable_above_threshold`。0.02 只是规划的检出阈值（旧 G-C 的 h1 门槛），不是已登记检验的量级门槛。
  **冻结的 `sd_between = 0.15` 下两行都会 not_testable**（修复轮 2 冻结该值；见 L5 第 1 步）——如实的
  设计结果，不是缺陷。
- **F9 期望计数与弃权**：投影用期望记分数，并在 10 名下限之前按规划弃权率（默认 0.10，`--abstention-rate`）
  收缩；两者都是声明的简化 / 规划假设，不是测量值。真实弃权率由 D7 干跑周测，L5 可以覆盖。
- **F5 功效报告校验**：写入器要求报告的 `source` 绑定同一份 Step 0 登记（sha256、`config_id`、路径），
  `planning` 的窗口/下限/水平/功效/阈值等于已采纳常量，并按行字段重算判定。
- **F7 协议文件绑定**：登记新增 `agent_forward_protocol_document`（`docs/research/p7-c-protocol.md` 的
  已提交 sha256）；写登记前先提交协议，写入器拒绝未提交或有改动的协议文件。
- **F6 L1 预算字面量**：预算文件必须是 `{"schema": "openalpha-p7-event-probe-budget/v1", "spent": 20}`
  （旧的 `{"spent": 20}` 会被探针拒绝）。
- **F1/F2 干跑周计数与隔离**：干跑周按**已开始的晚上**计数（失败的也计入 5 个上限），标记在第一个模型请求
  之前写；正式晚间在第 1 步、`--pin-worktree` 也拒绝带干跑标记的运行目录。
- **F8 报告窗口**：弃权率与模型别名统计只覆盖 `anchor..through`。

评审原文在 `.superpowers/sdd/plan-c/review-batch3-1.md`（不入库）；本轮没有真实数据、没有网络请求，
`DEEPSEEK_V4_1_FLASH.verified_on` 仍是 `None`，真钱闸门仍关着。

## 修复轮 2（2026-11-13，复审 N1/N2）

- **N1(a)：`sd_between` 冻结。** 用户 2026-11-13 裁决：`sd_between` 冻结在 **0.15**，它**没有测量来源**
  （D7 只测运维、绝不算 IC，给不了单会话 IC 的离散度），**L5 不得覆盖**；功效命令因此删去 `--sd-between`，
  只保留 `--abstention-rate`（D7 能给 L5 的规划输入至多是弃权率）。协议 §6 同步：冻结参数下 n300 与 census
  两行都会 `not_testable`（可检出均值下界约 `2.4865 × 0.15 / sqrt(120) ≈ 0.034 > 0.02`；n300 的采样项在约
  54 个记分名时 `1/sqrt(53) ≈ 0.137`，已超过门槛上界 ≈0.088）。**D1 的预期结果是「两行都不可测」**：
  C 以 `--register-untestable` 登记为**探索性候选**（或者不登记）；登记为探索性候选时，120 个前向会话的
  读数是**描述性的，不是一次检验**——**不被读成通过/不通过**、不把 p 值写成结论、不升级措辞。已登记的检验本身
  不变（通过 iff 均值 > 0 且单侧读数 ≤ 0.05），但设计登记为不可测时，它的读数不能被写成检验结果。
  写入器校验：`planning.sd_between` 必须**恰好** 0.15、`abstention_rate` 必须在 [0, 1) 内、从行自己的
  `sd` 与 `periods` **重算** `detectable_mean` 与判定，篡改即拒绝。
- **N2：锚点的第一天检查。** 登记的晚间在第 1 步用**与报告同一个共享函数**核对锚点（登记的提交时刻 +
  交易日历；规则见 L5 第 4 条），不一致就按名拒绝、**零请求零写入**——排错的提交时间在**第一天**暴露，
  而不是六个月后的 L6。写入器另外拒绝一个 16:30 发布时刻在写入时已经过去的锚点（这样的锚点一定不是
  「提交之后」的第一个交易日）。
- **协议文件冻结。** `docs/research/p7-c-protocol.md` 的已提交 sha256 绑定在登记里：**登记提交之后本文件
  不能再改**，连追加 §14 也会改变绑定字节；登记之后的协议修订是另一份文件加新登记。
- 本轮没有真实数据、没有网络请求；`DEEPSEEK_V4_1_FLASH.verified_on` 仍是 `None`，真钱闸门仍关着。
