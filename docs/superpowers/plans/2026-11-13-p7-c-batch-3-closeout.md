# 方案 C 开发批次 3 收尾记录（2026-11-13）

> **状态：记录，不是验收。** 本文记下本批次交付了什么、没交付什么、采用了用户的哪些裁决、哪些偏离是计划没写的、哪些事留到以后、哪些事要用户来定。它只写工程状态；**工程完成不是研究证据**（`AGENTS.md`「研究诚实」），措辞仍是“候选”，没有任何一行把“通过的测试”写成别的东西。本文也不写任何闸门的新通过判据。
>
> 本批次是**最后一批不联网的代码/文档交付**：真实功效运行、C 的登记、干跑周、真实晚间与前向读数全部留在受闸的 L1–L6；本批次没有读真实面板（功效表因此是「未运行」）、没有发过任何 Tushare/DeepSeek/LLM 请求、没有写出 `docs/research/p7-c-registration.json`、没有安装任何 launchd 任务、没有改 `DEEPSEEK_V4_1_FLASH` 的占位值。

## 0. 先读这几句

- 本批次做的是方案 C 的 BATCH 3（任务 3.1–3.6）：事件级功效计算、C 协议文档、C 登记写入器、链测试、C 报告命令、运行手册与文档波；外加用户 2026-11-12「按推荐」裁决的 `D1`、`D4`、`D5`、`D7`、`D8` 落地。
- 实施计划是任务书，**不在版本库内**：`.superpowers/sdd/plan-c/plan.md`（BATCH 3 与 Global Constraints）与 `.superpowers/sdd/plan-c/batch3-brief.md`（用户的约束性裁决）。本文是它的版本化记录。
- 代码在分支 `feat/v2-p7-c-batch-3` 上，**从 `879f420` 分出**（`879f420` 是批次 2 合入后的 main）。本批次 **6 个交付提交**（含 D5 采纳与共用接缝）：`85d3270`（D5 采纳、C 的伴随块读取器、`AGENT_SCRIPTS`、评估器的 dry-week 拒绝）、`40e27a5`（3.1 功效计算）、`f221fe7`（3.2 协议文档）、`4dc45b8`（3.3/3.4 登记写入器与链测试）、`5337512`（3.5 报告与 D7 干跑周），以及本文、路线图、决策链 §9、能力台账与运行手册所在的文档提交。**修复轮 1**（独立评审 `review-batch3-1.md` 的 9 条意见，见 §8）之后另有 5 个修复提交：`c469eb5`（F4/F9 功效模型）、`b3de60c`（F7 协议文档块读取器）、`b5c535c`（F3 写入器侧/F5/F7 登记写入器）、`98979a3`（F1/F2/F3 报告侧/F8 晚间命令与报告）、`3526f95`（协议/运行手册修复轮文本 + F6 的既有探针测试）。
- **未推送、未合并。** 全部测试跑在测试时生成的面板、抛掷 git 仓库、合成登记、构造的已存记录与脚本化传输上；pytest 的 socket 守卫全程开着。**没有任何真实数据、真实面板、真实模型**：`scripts/p7_c_power.py` 只在测试里对注入的读取器运行；`scripts/agent_report.py` 只对构造的已存记录运行；`agent_evening.py --dry-week` 只用脚本化传输。
- **D5 已由用户采纳，PROVISIONAL 标签已移除**：批次 2 的评估器把 futility 形式与单侧读数标为 `PROVISIONAL pending D5`；用户 2026-11-12「按推荐」采纳后，本批次把它们改成 `adopted`（`AgentEvaluation.adopted`，`D5_WORDINGS`），**保留**其全部守卫（全部中期位置到期、按比例可测下限、sticky）。旧的「h1 ≥ 0.02 且 t ≥ 2，或 h5 ≥ 0.06」两检验措辞由决策链 §9 的**追加行**改写，旧行未动。
- **没有任何 P7 登记存在**：`docs/research/p7-c-registration.json` 没有被写出；`docs/research/p7-registration.json`、`docs/research/p7-protocol.md`、`scripts/daily_selection.py`、`scripts/forward_report.py`、`scripts/p7_register.py` 与任何 `strategy_survivor_*` 模块的字节都没有被本批次改动（C 的晚间命令只 import `daily_selection` 的辅助函数；C 的报告与 Step 0 的周报是两个文件）。
- 全量 `tests/unit` **没有在本批次内跑过**（按控制者的分工，实现者只跑定向文件与跨切面审计；全量由控制者串行跑一次，结果以控制者的报告为准）。

## 1. 交付了什么（按任务）

### 任务 3.1 —— `V2-P7-013` 事件级功效计算（提交 `40e27a5`）

- 新 `scripts/p7_c_power.py`（零请求、无 Tushare/transport 导入、不读任何标签或收益）：
  - `historical_event_counts(store, *, first, last, book, adapter)`：对 `first..last` 的每个已存交易日，用 `strategy_survivor_facts.build_facts_day` + `domain.survivor_screen.screen`（as_of = 该会话 16:30 发布时刻，Step 0 登记的 transform/min_coverage/max_carry_sessions/exchange，`held=()`）重建当日幸存者，再用 `panel_limit_events.limit_events_on` 数出“有涨停/连板/炸板事件的幸存者”。返回 `SessionCount(session, survivors, event_names)`。
  - `event_power(counts, *, sample, sessions=120, horizon=1, sd_between=0.15, abstention_rate=0.10, level=0.05, power=0.8)`（**修复轮 1 按 F4/F9 改写，见 §8**）：把历史计数投影到登记的 120 个前向会话——有效记分名数 = `round(event_names × min(sample, survivors) / survivors × (1 − abstention_rate))`（`sample=None` 或容量 ≥ 当日集合即普查）；当日低于 `MIN_SCORED_NAMES = 10` 不可测；投影可测会话 = `round(可测比例 × sessions)`；h>1 时按非重叠位置取 `periods`；每个可测会话的 sd = `min(1, sqrt(sd_between² + 1/(n−1)))`（n 为弃权后的记分名数；n=1 取 1.0），行 sd = 可测会话中的最大值（**永远 ≤ 1**），`sd_between` 是**非抽样部分**的规划假设；可检出均值 = `incremental_ic.detectable_mean(sd = 行 sd, periods, level, power)`（单侧 0.05 / 80% 功效，只数可测会话）；判定 = `testable` iff 观测到的历史会话 ≥ `MIN_OBSERVED_SESSIONS = 20`、投影可测会话 ≥ `MIN_MEASURABLE_SESSIONS = 60`、且可检出均值 ≤ `DETECTABLE_MEAN_THRESHOLD = 0.02`（G-C 旧 h1 门槛，作为**规划可检出性**阈值，仍不是已登记检验的幅度门槛）；否则 `not_testable` 并带具名 reason（观测太少 / 低于可测下限 / 可检出均值高于 0.02）。报告打印每行的 sd、可检出均值与 0.02 阈值，并声明期望计数（`E[event_names]·min(sample,S)/S`）与规划弃权率的简化。
  - `power_report` / `render_report` / `write_power_report`：`openalpha-p7-c-power/v1` 报告，`rows` 是 **以样本标签为键的映射**（`rows["n300"]`、`rows["census"]`），每行带 sample/sample_size/projected_scored_names/observed_measurable_sessions/measurable_sessions/periods/detectable_mean/verdict；写盘是原子 tmp+`os.replace`，**只有 `--json` 才写**，否则只打印。
  - `main`：`--panel-dir`、`--step0-registration`（默认 Step 0 的登记）、`--first`、`--last`、`--json`、`--sd-between`（旧 `--between-day-sd` 在修复轮 1 删除）、`--abstention-rate`、`--level`、`--power`、`--repo`；读已提交的 Step 0 登记，拒绝不可读的登记/面板/日期顺序。
- 测试 25 条（修复轮 1 后），含“计数把当日幸存者与当日事件相交”“更大横截面能检出更小均值”“低于 10 个记分名的会话不可测”“只有普查可测而 n300 不可测”“投影可测会话低于 60 即 not_testable”“h>1 用非重叠位置”“报告是写入器契约且可 round-trip”“报告不含标签/收益”“`main` 无 `--json` 不写盘”“模块不导入 transport/无 live 路径”。
- **本批次没有运行它**：功效运行是 L5 之前的 0 次请求步骤，需要一份已存面板副本；`docs/research/p7-c-protocol.md` §6.3 的表现在是「未运行」（不是「不可测」，也不是「可测」）。

### 任务 3.2 —— `V2-P7-013/014` C 协议文档（提交 `f221fe7`）

- 新 `docs/research/p7-c-protocol.md`（14 节，正文不改、只追加 §14 版本记录）：身份（自己的 `agent_forward/v1` 登记、不属于幸存者链）、候选假设、两个评分集合（主集 N=300 随机抽样 vs 覆盖集，永不混池）、家族/名册/提示词/分数映射/弃权码（D4：v1 只有 011 的涨停/连板/炸板事件；资金流与公告到 L1 探针之后另行登记）、**一个**主统计量（LLM 家族、h=1 偏秩 IC、120 个前向会话、单侧符号翻转 α=0.05、H1 均值 > 0；中期第 60 个位置、futility 与守卫；「不可测」是自己的结论）、D1 功效（n300 与普查两行、逐行判定、三条选择规则、精确命令；表标「未运行」）、探索性 BY 家族 q=0.10（规则家族、h=5、覆盖集）、D6 失败语义、模型身份（`deepseek-flash` 移动别名、逐交换记录 model 与 `system_fingerprint`、thinking 关闭/temperature 0/错峰、`verified_on` 仍为 `None`）、只前向 + 72 小时护栏、A 的两条候选政策以**定义冻结**（阈值未设，事后选阈值需要新的前向登记）、D7 干跑周/D8 报告/L1–L6 阶梯（含 20 次被拒 Tushare 尝试计入 L1 的 300 次上限）、不是证据、只追加版本记录。
- 协议里没有任何编造数字：功效表的每个数格都是「未运行」。

### 任务 3.3 —— `V2-P7-013` C 登记写入器（提交 `4dc45b8`）

- 新 `scripts/p7_c_register.py`（在受绑定路径之外，运行一次；**从不 staging/commit/push**）：
  - `C_REGISTRATION_PATH = "docs/research/p7-c-registration.json"`、`AGENT_REGISTRATION_KIND = "agent_forward/v1"`、`PRIMARY_SIZE = 300`、`CENSUS_DRAW_SIZE = 10**6`、`C_SCRIPTS = AGENT_SCRIPTS`（`strategy_agent_forward.AGENT_SCRIPTS` 的同一拼写）。
  - `c_registration_body(*, salt, step0_registration, power, anchor)`（比计划草图的 2 参数多 `power` 与 `anchor`，见 §3）：`config = {"kind","salt","primary_size"}`；13 键 `criteria`（一个主检验、futility、中期、可测性、探索性、D4 名册、证据、D1 功效、D7 干跑周、D8 报告、模型、只前向、候选措辞）；`settings = grid.protocol_settings() + 四个封闭块`（**修复轮 1 增为五个，见 §8**）：`agent_forward_protocol`（窗口 120/中期 60/min_scored 10/min_measurable 60/level 0.05/q 0.10/三条探索臂/bootstrap 100000/seed 20260926）、`agent_forward_power`（D1 的样本、判定、`register_untestable`、功效报告 sha256、可测会话与可检出均值）、`agent_forward_step0`（已提交 Step 0 登记的相对路径、sha256、config_id、seed）、`agent_forward_anchor`（登记的第一个前向会话；修复轮 1 起写入器只要求工作日且不早于今天，精确的“提交后第一个交易日”检查在报告侧，见 §8）。
  - `c_read_back(...)`：用晚间命令的 `read_agent_registration`、评估器的四个伴随块读取器、再次的盐/种子耦合拒绝、Step 0 块的重新核对、主集合与功率判定一致、探索臂与 D5 采纳值一致、anchor 是工作日、criteria 非空且无禁区措辞，把 body 读回一遍才算通过。
  - `main`：`--salt/--step0-registration/--power-report/--anchor/--path/--dry-run/--register-untestable`；`--dry-run` 打印与 `registry.register` 同形的规范文档、config_id、sha256 与「未写入」；真写用 `registry.register(..., code_commit=HEAD)`，核对写入摘要等于打印摘要，若不一致只删自己刚写的文件；绝不 staging/commit。
  - 具名拒绝（都退出 2 且零写入）：DeepSeek 端点未核实（`verified_on` 仍为 `None`）、盐与 Step 0 种子相同或派生、Step 0 登记缺失/未提交/已编辑/不是幸存者登记、功率报告缺失/坏/来源不绑定本 Step 0 登记/计划常数不符/行判定不自洽/判定不可测（除 `--register-untestable` 记录用户的 D1 选择）、anchor 非工作日或早于今天（修复轮 1 起允许未来工作日、删去 16 天回看，见 §8）、协议文档未提交或已脏、路径不在 `docs/research/*.json`、绑定树脏、已有不同登记、写入摘要不一致。
  - **结构性保证**：`config.kind = agent_forward/v1` 不是 `committed_survivor_registrations` 的候选，C 的登记不可能进入 Step 0 的幸存者链。
- 测试 41 条 + 链测试 7 条（其中 3 条真文件检查在 `p7-c-registration.json` 缺失时跳过并按设计保持跳过，直到用户的 L5 提交）；抛掷仓库测试证明 body、D1 选项分支、全部拒绝与“不在幸存者链”。

### 任务 3.4 —— 链测试（同上提交）

- `tests/unit/test_p7_registration_chain.py` 增加：抛掷仓库里已提交的 `agent_forward/v1` 登记不是幸存者链候选（非跳过，今天即执行）；真 `docs/research/p7-c-registration.json` 的三条检查在文件缺失时 `pytest.skip`（写出后自动生效，无需改测试）：kind、非幸存者链成员、四个伴随块可读且协议值等于评估器的 `ADOPTED_*` 常量、功率块与 body 规则一致。

### 任务 3.5 —— `V2-P7-014/013` C 报告与 D8（提交 `5337512`，D5 采纳在 `85d3270`）

- 先做 D5 采纳（`85d3270`）：`AgentEvaluation.provisional` 改为 `adopted`（`D5_WORDINGS`：futility 形式与单侧读数都是已采纳规则）；新增 `ADOPTED_WINDOW_SESSIONS = 120`、`ADOPTED_INTERIM_SESSION = 60`、`ADOPTED_MIN_SCORED = 10`、`ADOPTED_MIN_MEASURABLE_SESSIONS = 60`、`ADOPTED_LEVEL = 0.05`、`ADOPTED_Q = 0.10`、`ADOPTED_SECONDARY_ARMS`（规则家族、h=5、覆盖集三条探索臂）；新增四个伴随块读取器（`read_agent_forward_power`/`read_agent_forward_step0`/`read_agent_forward_anchor`，协议块仍由 `read_agent_protocol` 读）；新增 `DRY_WEEK_SCRATCH_MARKER/SCHEMA` 与 `refuse_a_dry_week_runtime`，`evaluate` 新增可选 `agent_runtime_dir`（带 marker 的 dry-week 目录按名拒绝）；`evaluate` 的其余签名与守卫不变（F4/F5 的守卫全保留）。评估器集成测试 45 条通过。
- 新 `scripts/agent_report.py`（D8：独立的报告命令；Step 0 的 `scripts/forward_report.py` 字节不动）：
  - 参数：`--runtime-dir`（C 的运行目录）、`--registration`（默认 C 的登记）、`--step0-runtime-dir`（只读）、`--repo`、`--as-of`、`--through`、`--json PATH`。
  - 顺序：先按名拒绝 dry-week 运行目录 → `registry.admit_registered_code(..., also_bound=AGENT_SCRIPTS)` → 四个伴随块读取器 → 从 `agent_forward_step0.path` 读已提交的 Step 0 登记并核对 sha256/config_id/seed → `read_survivor_registration` 取书 → `prediction_store_at` 两个存储（Step 0 只读）→ `OutcomeLabels` →（修复轮 1 起）从登记提交时刻推导并逐字核对 anchor（提交后第一个交易日；不符按名拒绝）→ `evaluate(..., agent_runtime_dir=C)`；弃权率与模型别名只统计 anchor..through（修复轮 1 F8，见 §8）。
  - 输出（打印与 `--json` 同事实）：候选/未交易/“通过的运行不是证据”；身份与窗口；见证/缺失记录与评估器原因码；弃权率（从 C 已存 LLM 主集记录读出，弃权永不是 0）；模型别名与 `system_fingerprint` 变化（从晚间日志读出，缺失也报）；主臂：窗口满足前只印计数与描述性均值、**无 p 值**，满足后印一次检验（均值、双侧、单侧、水平、exact/randomized、符号图案）；**总是**印 n 与可检出均值/依据与登记的 D1 判定；五值结论的固定措辞（`not_measurable` 用评估器的 `NOT_MEASURABLE_WORDING`，绝不写成“没有证据”）；采纳的 D5 规则；探索性块逐臂标 **EXPLORATORY**，按协议的 q 读作 below q / at or above q，永不与主臂混池、永不当结论。`--json` 原子写 `openalpha-agent-report/v1`，否则只打印。
  - 不 import 任何 `agents`/`models` 模块、零模型调用（AST 测试）；Step 0 运行目录只读（测试快照只允许 DuckDB 目录锁/侧写文件）。
- 测试 22 条（报告）+ 9 条（干跑周），含最早一条 “`ModuleNotFoundError: No module named 'agent_report'`” 的 collection red。

### D7 干跑周（同 `5337512`，`scripts/agent_evening.py --dry-week`）

- 模式：`--dry-week --runtime-dir SCRATCH --step0-runtime-dir DIR`（**不需要 C 的登记**，step 1 跳过；没有任何预测被 file，所以没有可登记的对象）；必须 `--step0-runtime-dir`，且 scratch runtime 不能是 C 的正向运行目录（已持有晚间日志或任何已 file 预测即拒绝）。
- 约束：每晚 ≤ 600 次模型请求（`--max-model-requests` > 600 是具名拒绝而非静默截断）、≤ 5 个晚间（marker 的 `evenings` 列表）、已记录过的晚间重跑拒绝；**不 file 预测、不写 C 的晚间日志/证据、Step 0 存储字节不动**（四条都有测试）。
- 每晚上与真实晚间同路：读 Step 0 登记与当日、迟到检查；在内存里从 Step 0 面板派生当天事件（不写 C 证据库、不做 `refuse_a_restated_session`）；census = 有事件的幸存者；经真实 `research_engine(agents=[StructuredSignalAgent(...)])` 逐名运行，`wrap_transport = 请求上限/期限 ∘ 运维计量器`；计量器统计 HTTP 尝试、传输失败、空白内容、非 JSON 内容、每次延迟、usage tokens；每名计 scored/abstained/not_routed/invalid_output；传输错误/上限/期限/配置错误以具名 status 停止。
- 产物：`<scratch>/dry_week/<session>.json`（`openalpha-c-dry-week-evening/v1`：schema、session、status、names、requests、scored、abstained、not_routed、invalid_output、blank_responses、invalid_json_responses、transport_failures、latency_ms{p50,p95,mean}、tokens{input,output,total}、cap、deadline；**无任何分数/IC/收益/标签/结论字段**）与 `<scratch>/c-dry-week-scratch.json`（`openalpha-c-dry-week-scratch/v1`，`evenings` 列表；**修复轮 1 起每个开始的晚间在第一次模型请求之前原子写/刷新，失败的晚间也计数**，见 §8）。报告与评估器按 marker 按名拒绝该目录（测试：`refuse_a_dry_week_runtime` 与报告命令都拒绝）。

### 任务 3.6 —— 运行手册与文档波

- 新 `docs/runbooks/agent-evening.zh-CN.md`：命令是什么、六步、前提、手动运行、选项、退出码、失败表、输出怎么读、重跑/崩溃/补跑、请求预算与期限、**L1–L6 阶梯**（L1 预算从 `{"spent": 20}` 开始、不创建 live 文件）、D7 干跑周、L5 登记/钉 worktree/只打印的 launchd、L6 报告、已知局限。
- 路线图 `V2-P7-013`、`V2-P7-014` 两行追加批次 3 说明（**两行未关闭**）；决策链 §9 追加三行：D5 改写（两检验 → 一检验，以及为什么）、批次 3 交付、以及「不改顺序/闸门」的无漂移行；能力台账加 4 行（`OA-OPS-053` 功效、`OA-OPS-054` 登记写入器、`OA-EVAL-002` 报告、`OA-OPS-055` 干跑周），并重新生成 `summary.json` 与 `docs/release/openalpha-v1-feature-ledger.md`（240 → 244 项、真实完成 235 → 239）；`tests/unit/test_feature_ledger_test_tree.py` 的测试文件计数表同步（`tests/unit/scripts` 12 → 15、`tests/unit` 50 → 51、总数 253 → 257）。

## 2. TDD 与并行实现

- 四条轨道并行：`power`（3.1）、`register`（3.3/3.4）、`report`（3.5+D7）、`protocol`（3.2/3.6 文档）；每条行为先写失败测试、跑出正确的失败原因、再最小实现、再跑。第一次失败证据（实现者报告）：`power` 整文件 collection red（`ModuleNotFoundError: No module named 'p7_c_power'`），随后 `PowerRow.__init__() got an unexpected keyword argument 'measurable_sessions'` 等真实行为红，且每个行为另用“变异 → 单测红 → 逐字节恢复（sha 核对）”复证；`register` collection red（`ModuleNotFoundError: No module named 'p7_c_register'`），随后两条真实行为红（探索臂 dataclass 与 tuple 比较、拒绝消息未点名 “future”）；`report` collection red（`ModuleNotFoundError: No module named 'agent_report'`），随后 20 条行为红（Step 0 夹具的 exchange、helper 遮蔽、kw-only `StatisticTest`、记录摘要），干跑周 9 条首红是 `unrecognized arguments: --dry-week`；Lead 的 D5 采纳测试首红是 `evaluate() got an unexpected keyword argument 'agent_runtime_dir'`（为取证临时 stash 评估器改动后跑，再恢复）。
- 提交顺序按计划任务顺序：`85d3270`（D5/接缝）→ `40e27a5`（3.1）→ `f221fe7`（3.2）→ `4dc45b8`（3.3/3.4）→ `5337512`（3.5/D7）→ 文档波提交。并行实现不改变提交顺序。**“每任务一个提交”的偏差，如实记录**：3.3 与 3.4 共用一个提交（链测试是同一写入体读取器的验收面，二者一起才通过），3.5 与 D7 共用一个提交（干跑周与报告共用dry-week 拒绝、运维日志与测试夹具），D5 采纳与共用接缝先单独提交。每个提交主题都点名 `V2-P7-013`/`V2-P7-014`，文档波提交也点名 `V2-P7-015`（协议 §10 记录该护栏；护栏代码本批次未改）。
- Lead 的集成编辑（不在任何实现者的写范围）：`src/openalpha_cn/strategy_agent_forward.py` 的 `AGENT_SCRIPTS`、`src/openalpha_cn/strategy_agent_evaluation.py` 的 D5 采纳/伴随块/dry-week 拒绝、`scripts/agent_evening.py` 的 `C_SCRIPTS = AGENT_SCRIPTS`、`tests/unit/test_strategy_agent_forward.py` 与 `tests/integration/test_agent_incremental_ic.py` 的对应测试、`tests/integration/test_usage_claims_match_the_shipped_paths.py` 的两个新 `USAGE_SITES` 与门控断言（第二处受门控的构造 `_dry_week_family`）。
- 提交信息按 `type(V2-P7-0xx): …` 并带 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

## 3. 裁决、偏离与计划未覆盖处的选择

- **采用的用户裁决（2026-11-12「按推荐」）**：
  - **D1**：功效计算打印 N=300 与全幸存者普查两行，逐行 `testable` / `not_testable`；协议 §6 写明“只有普查可测则用普查；两行都不可测时登记为不可测或干脆不登记——这个最后选择是用户 L5 的”；登记写入器在所选集合判定为 `not_testable` 时**拒绝登记**，除非显式 `--register-untestable`（此时 body 记录该选择）。
  - **D4**：v1 证据名册只有涨停/跌停事件（`limit_up`/`consecutive_board`/`broken_board`）；资金流、公告等不在 v1，等 010 探针（L1）之后另行登记。协议与 criteria 都写明。
  - **D5**：唯一主检验 = LLM 家族、主集、h=1 偏秩 IC、120 个前向会话、单侧符号翻转 α=0.05（H1 均值 > 0）；中期第 60 个位置、累积均值 ≤ 0 即停；批次 2 的 PROVISIONAL 改为**采纳**并保留守卫；旧两检验措辞由 §9 追加行改写；其余（规则家族、h=5、覆盖集）永远探索性、BY q=0.10。
  - **D7**：干跑周实现为 `agent_evening.py --dry-week` 模式（scratch runtime、≤5 晚、≤600 次/晚、只测运维）；另有“记录永不被报告/评估器读取”的结构性保证（marker + `refuse_a_dry_week_runtime`）。
  - **D8**：C 的报告是独立 `scripts/agent_report.py`；Step 0 的周报脚本字节不变。
- **计划未覆盖、由 Lead 裁决并在这里记录的选择**：
  1. **C 的登记用四个伴随块**（协议/功效/Step 0/anchor）而不是把字段塞进协议块：`read_agent_protocol` 的块是封闭的（`extra="forbid"`），塞进去会破坏批次 2 的读取器；四个块各有封闭读取器，登记写入器、报告与链测试共用。anchor 进登记是批次 2 F4 的 `evaluate(anchor_session=...)` 要求的（计划 3.5 没点名它从哪来）。
  2. **`c_registration_body` 比计划草图多两个必填关键字**（`power`、`anchor`）：D1 的判定与窗口锚必须进 body，否则它们会被代码事后选择。模块 docstring 与本文记录。
  3. **census 分支的编码**是 `primary_size = 10**6`（`CENSUS_DRAW_SIZE`），因为 `primary_sample` 的规则是“容量 ≥ 集合大小即普查”，不需要第二个哨兵值；协议 §6.4 与运行手册都写明。
  4. **探索臂取三条**（规则家族主集 h=1、LLM 主集 h=5、LLM 覆盖集 h=1）：D5 的“规则家族、h=5 与覆盖集是探索性”枚举的是维度，不是笛卡尔积；三条各覆盖一个类别，比七条更不容易把同一读数重复计入 BY 家族。记录在评估器常量与协议。
  5. **`historical_event_counts` 多一个必填 `adapter`**：计划草图只写 `book`，但它自己的每会话配方需要登记的 transform/min_coverage/max_carry_sessions/exchange；默认它们会悄悄用错设置重建当日。
  6. **报告不拿 `--step0-registration`**：Step 0 的路径/摘要/config_id/seed 记在 C 的登记 `agent_forward_step0` 块里，报告读登记而不是读命令行，避免在 L6 手工改前任。
  7. **干跑周的失败晚间仍写运维日志并退出 0**（运维账户是产物），但 marker 只列**完成**的晚间，所以失败不消耗 5 晚上限；已经记录过的晚间（无论完成与否）重跑被拒。`--max-model-requests > 600` 是具名拒绝而非静默截断；新增 `credential_missing` 作为第六种失败 status（与晚间的 `no_record` 理由同一语义）。
  8. **登记写入器不要求 HEAD 在 main 上**（计划没有这条；`p7_register` 要求是因为冒烟必须在 main 上跑）。C 的登记可以写在一个分支上再合入；跑的是绑定字节。
- **对批次 2 交付的两处更正（在 `85d3270`）**：评估器的 `provisional` 字段改名 `adopted` 并换成采纳措辞（D5 已裁决）；`strategy_agent_evaluation` 仍是只读评估器，新增的 `refuse_a_dry_week_runtime` 只读一个 runtime 目录里的 marker 文件，不 import `runtime.composition`（`forward-registration-reaches-no-face` 契约禁止），契约未放宽。

## 4. 顺序与闸门不变

- 本批次**不改** `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §5 的顺序，也**不改** §5.2 的任何闸门：`006a` → `006b`、G-0、真实数据冒烟、真钱闸门与 2027-01-04 之前的硬闸门都不动；issue 总数 254（`V2-P7-026` 在 base 上）。§9 加了三行（D5 改写、批次 3 交付、“没有漂移”）。
- **没有任何一行被关闭**：`V2-P7-013` 与 `V2-P7-014` 的路线的“真实数据/真实模型”那一半没有发生（没有真实面板读取、没有真实记录、没有模型请求、没有 C 的登记），两行保持打开。台账里的 4 行是 `NATIVE_COMPLETE` 的工程条目，notes 都写明 ROW NOT CLOSED 与原因。
- 本批次内跑过的门槛（定向，见 §7）：本批次新增/改动的全部测试文件、`test_usage_claims_match_the_shipped_paths.py`、`test_feature_ledger_test_tree.py`/`test_feature_ledger_symbols.py`、`test_strategy_agent_forward.py`、`test_forward_and_agent_layering.py`；`ruff check`、`ruff format --check`、`mypy --cache-dir=/dev/null src scripts`、`PYTHONPATH=src lint-imports`、`scripts/build_feature_coverage.py --check`、`scripts/verify_publication.py`；跨切面 `pytest tests/unit/domain tests/unit/test_*.py tests/unit/scripts`（控制者在最终 tip 上串行跑一次，结果以控制者报告为准）。
- 全量 `tests/unit` 由控制者在最终 tip 上串行跑一次，结果以控制者的报告为准（本批次实现者没有跑全量）。

## 5. 未做与前置条件（全部留在 L1–L6）

- **没有真实数据、没有真实的联网研究步骤**：没有读真实面板、没有跑真实构建/因子/读数/冒烟，没有 DeepSeek（或任何 LLM）调用，没有发过任何 Tushare 请求（本批次内）。**L1（010 live 探针 ≤300 次 Tushare 请求）**没有运行；批次 2 的 20 次被拒尝试仍计入上限，L1 的预算文件从 `{"spent": 20}` 开始（文档写了，**不要**创建 live 预算文件）。
- **L2（DeepSeek 端点核实，0 请求）**：`DEEPSEEK_V4_1_FLASH` 仍是占位——`model`/`base_url` 是占位字符串、`structured_output_mode="json_schema"` 是占位值、价格是 `Decimal("0")`、`verified_on=None`（与 base `879f420` 逐字节相同，本批次没有填任何值）；登记写入器与报告都按名拒绝未核实端点。填值与改写四份面向用户文档的模型措辞是 L2 的同一个提交。
- **L3（≤5 次模型请求的 live 探针）**没有运行，等 L2 与 `DEEPSEEK_API_KEY`。
- **L4（D7 干跑周，≤5 × 600 次模型请求）**没有运行：`--dry-week` 模式已交付并有测试（修复轮 1 起五晚上限数**每个开始的晚间**，失败的晚间也计入），但一个晚间也没有跑，一次请求也没有发。
- **L5（功效运行 + C 登记 + 钉 worktree）**没有发生：功效运行（3.1）没跑（协议 §6.3 表是「未运行」）；`docs/research/p7-c-registration.json` 没有写出（写入器只交付，用户执行并提交）；钉 worktree 与 launchd 只打印不安装。
- **L6（G-C 读数）**没有发生：C 的前向窗口（120 个会话）没有开始，报告只对构造记录跑过测试。
- 真实提示词/模型身份/别名变化、真实弃权率、真实事件稀疏度、真实增量 IC 都还没有观测；`docs/research/p7-c-protocol.md` §14 的登记 sha256/config_id/anchor/commit 与 C 时钟起点要在 L5 之后追加。
- **真钱闸门仍关着**；本批次没有打印/安装任何要下的单，也没有任何交易路径。

## 6. 已知局限 / 开放问题

1. **功效表的数不是数**：§6.3 两行全部「未运行」。没有真实面板读取，本批次不能把任何估计写成「可测」或「不可测」；L5 运行 `scripts/p7_c_power.py` 后由用户把打印的两行粘贴进 §6.3（只追加、不改正文）。这也是登记写入器 `--power-report` 的前置。
2. **没有登记时链测试会跳过**：`tests/unit/test_p7_registration_chain.py` 的三条真文件检查在 `docs/research/p7-c-registration.json` 缺失时 `pytest.skip`；文件写出后它们自动生效。这是设计（计划 3.4 的“文件缺失时跳过”）。
3. **模型别名的重复记录局限**（批次 2 收尾 §6 已记，本批次未修）：模型 id 是声明的一部分，别名一变 `session_record` 会把它当另一个声明写第二条记录；评估器与写一次日志会按名拒绝同一会话的两条记录。本批次的报告会把 `response.model`/`system_fingerprint` 的变化报出来（这正是 D5/协议要求的“报告而不是重登记”），但重复记录的加固仍未实现。
4. **干跑周与报告/评估器的隔离是“按名拒绝 + marker”**：一个被手工删除 marker 的 scratch runtime 看起来就是普通 C runtime；这是文档化已知边界的失败方向（删除 marker 是人的动作，不是运行产生的）。
5. **报告窗口前不印采纳规则**：`ADOPTED_ONE_SIDED_WORDING` 本身含 “p-value” 字样，而窗口前的要求是“没有任何检验措辞”，所以报告只在 `outcome != not_yet` 时印采纳规则；这是测试钉住的行为，不是遗漏。
6. **`AGENTS.md` 的 “Current work pointer” 与 `docs/HANDOFF_CURRENT.md` 没有改**（批次 1/2 的同一先例）：本批次没有真实数据、没有登记、没有关闭行，交给控制者的交接波。
7. **`scripts/agent_evening.py` 现在有两种模型路径**（正常六步与 `--dry-week`）：用量站点普查登记了两处受门控构造（`_llm_family`、`_dry_week_family`）；L2 填 `verified_on` 的那天，`test_the_agent_evening_model_family_is_gated_on_the_unverified_endpoint` 会失败并要求改写四份文档的模型措辞、退役或反转那两个散文守卫——这是绊线，不是已放宽的声明。
8. **协议版本记录的编号是 §14，不是计划写的 §12**：计划 3.2 的句子是“先写、之后只追加 §12”；本文件把 §12 用于顺序与运行阶梯，把只追加的版本记录放在末尾的 §14（内容与“只追加、不改正文”的约束一致）。这是编号偏差，不是内容缺失。
9. **测试耗时**：`tests/unit/scripts/test_agent_evening.py` 约 160 s（每个测试自己生成面板）；`tests/unit/scripts/test_p7_c_register.py` 约 30 s（抛掷 git 仓库）；`tests/integration/test_usage_claims_match_the_shipped_paths.py` 约 128 s（扫描全树）。这是既有结构，不是正确性问题。

## 7. 本批次跑过的门槛（定向）

- 新增/改动的实现测试：`tests/unit/scripts/test_p7_c_power.py`（18）、`tests/unit/scripts/test_p7_c_register.py` + `tests/unit/test_p7_registration_chain.py`（45 passed、3 skipped）、`tests/unit/scripts/test_agent_report.py` + `tests/unit/scripts/test_agent_evening.py`（65 passed）、`tests/integration/test_agent_incremental_ic.py`（45 passed）、`tests/unit/test_strategy_agent_forward.py` + `tests/unit/test_forward_and_agent_layering.py`（21 passed）、`tests/integration/test_usage_claims_match_the_shipped_paths.py`（48 passed）。
- 台账守卫：`tests/unit/test_feature_ledger_test_tree.py` + `tests/unit/test_feature_ledger_symbols.py`（36 passed）；`python scripts/build_feature_coverage.py --check` 通过（244 项、真实完成 239）。
- `ruff check .` 全部通过；`ruff format --check .` 干净；`mypy --cache-dir=/dev/null src scripts` 无问题（216 源文件）；`PYTHONPATH=src lint-imports` 10 kept / 0 broken；`python scripts/verify_publication.py` 通过。
- **跨切面审计（必须项）**：`pytest tests/unit/domain tests/unit/test_*.py tests/unit/scripts` 在最终 tip 上跑完：**1 failed / 5597 passed / 3 skipped in 14233.17s（3:57:13）**。唯一的失败是 `tests/unit/test_cli_help.py::test_the_rendering_is_the_same_whether_or_not_the_terminal_takes_colour`，这是本机环境依赖（本 shell 是 `TERM=dumb`、`NO_COLOR=1`，Rich 不输出转义），与批次 2 收尾记录记录的同一现象；在 `TERM=xterm-256color`、不设 `NO_COLOR` 时该文件 **7 passed**（本批次已复跑证明）。3 个 skip 是 `tests/unit/test_p7_registration_chain.py:200` 的三条真文件检查（C 的登记是用户 L5 的提交，文件缺失时按设计跳过）。除此之外没有失败，没有与本批次改动相关的红。
- 文档守卫：`tests/unit/test_documentation_tables.py`（3 passed）、`tests/unit/test_source_counts_match_the_tree.py`、`tests/unit/test_source_cited_tests.py`、`tests/unit/test_surface_parity.py` 已包含在跨切面审计里并通过。
- **只读验收子代理**（独立、read-only）对照批次 3 的每条要求逐条核对，结论 **PASS**：10 条要求全部 met，没有 blocking 缺陷；它提出的 3 条低风险观察（收尾记录未写跨切面审计结果、3.3+3.4 与 3.5+D7 合并提交、没有提交点名 `V2-P7-015`）与 3 条信息项（协议版本记录编号 §14、真文件链测试按设计跳过、DeepSeek 价格是 `Decimal("0")` 占位）都已在本记录与文档波提交里处理或记录。
- 文档波没有改任何受绑定代码；C 的登记、钉 worktree、任何 launchd 安装都没有发生。

## 8. 修复轮 1（独立评审的 9 条意见）

独立评审 `.superpowers/sdd/plan-c/review-batch3-1.md`（不入库）在 tip `2c79a50` 上给出 **Spec FAIL / CHANGES REQUESTED**：一条 blocking（D7 的五个晚间上限可被失败晚间绕过）、一条关于窗口锚点可早于登记提交（与 D5 的“登记提交之后 120 个前向会话”矛盾）、以及七条重要/次要意见。九条全部在本轮修复；代码提交 `c469eb5`（F4/F9）、`b3de60c`（F7 读取器）、`b5c535c`（F3 写入器侧/F5/F7）、`98979a3`（F1/F2/F3 报告侧/F8）、`3526f95`（协议/运行手册文本 + F6 的既有探针测试）。**本轮没有网络、没有真实数据、没有写出 C 的登记、没有发过一次模型请求。**

| 意见 | 修法 | 测试节点 |
|---|---|---|
| **F1（blocking）** D7 五晚上限只数完成的晚间；全失败运行目录不被标记 | `_dry_week` 在第一次模型请求**之前**把本会话写/刷新进 `c-dry-week-scratch.json`，**所有 status** 都写；`_dry_week_evenings` 现在读“开始的晚间”（marker ∪ `dry_week/*.json` 的会话名，marker 丢失时以日志为第二见证），第六个开始的晚间在发请求之前拒绝；旧的“仅 completed 才写 marker”删除 | `tests/unit/scripts/test_agent_evening.py::test_five_failed_evenings_exhaust_the_scratch_runtime_and_the_sixth_spends_nothing`、`::test_a_dry_week_evening_stops_with_the_named_status_it_met` |
| **F2** 已登记的晚间不拒绝 dry-week 运行目录 | `_run` 在 `_admit` 之前（step 1）调用 `refuse_a_dry_week_runtime(runtime)`，按名拒绝；`--pin-worktree` 在给了 `--runtime-dir` 时也拒绝 | `::test_the_registered_evening_refuses_a_dry_week_marked_runtime`、`::test_pin_worktree_refuses_a_dry_week_marked_runtime_and_changes_nothing` |
| **F3** 锚点可早于登记提交，且“报告侧精确检查”不存在；协议写“星期一日” | 写入器改为“工作日且 `anchor >= today`（允许未来工作日）”，删除 16 天回看与“不能是未来”的规则；报告从 `admitted.committed_at` 推导期望锚点：`calendar.is_trading_day(d)` 且 `session_publication_instant(d) > committed_at`（上海），即**提交时刻之后第一个交易日**，不符按名拒绝；周末/节假日/收盘前后提交都有测试；协议 §5.3/§2 与运行手册 L5 改成同一规则 | `test_p7_c_register.py::test_a_future_weekday_anchor_is_accepted`、`::test_refuses_an_anchor_before_today_or_on_a_weekend`；`test_agent_report.py::test_the_registered_anchor_is_the_first_trading_session_after_the_commit`、`::test_a_holiday_commit_anchors_the_next_trading_session`、`::test_an_anchor_that_is_not_the_first_trading_session_after_the_commit_is_refused` |
| **F4** 功效判定不看功效；sd 模型可打印不可能的秩 IC sd；一次观测就能投影 120 | sd 改为 `min(1, sqrt(sd_between² + 1/(n−1)))`（可测会话取最大，永远 ≤ 1），`sd_between` 是非抽样规划部分；判定改为“观测 ≥ 20 且投影可测 ≥ 60 且可检出均值 ≤ 0.02”；每行打印 sd、可检出均值与 0.02 阈值；三个具名 not_testable reason；0.02 来源记为 G-C 旧 h1 门槛（规划可检出性阈值，不是已登记检验的幅度门槛） | `test_p7_c_power.py::test_the_row_sd_is_bounded_by_one_at_the_thousand_survivor_small_cross_section`、`::test_the_verdict_flips_at_the_detectable_mean_threshold`、`::test_fewer_than_twenty_observed_sessions_is_not_testable`、`::test_a_detectable_mean_above_one_is_never_printed` |
| **F5** 写入器接受任何功效报告，不核对其是否描述本 Step 0 登记 | `_read_power` 要求 `source` 非空且 `step0_registration`/`config_id`/`registration_sha256` 与本次绑定的 Step 0 一致、`code_commit` 非空；planning 的 window/floors/level/target power/threshold/min observed 必须等于采纳常数；每行的 verdict 从该行自己的数字重算（手写的 verdict 被拒） | `test_p7_c_register.py::test_refuses_a_power_report_that_does_not_name_this_step_zero_registration`、`::test_refuses_a_power_report_that_does_not_use_the_adopted_plan`、`::test_refuses_a_power_row_whose_verdict_its_own_fields_do_not_support` |
| **F6** 文档里的 L1 起始预算字面量不是探针能读的预算文件 | 协议 §12.3 与运行手册改为精确字面量 `{"schema": "openalpha-p7-event-probe-budget/v1", "spent": 20}`；新增测试用探针自己的 `read_probe_budget` 解析文档里的字面量并得到 spent=20（**不创建 live 文件**） | `tests/unit/scripts/test_p7_event_probe.py::test_the_documented_l1_budget_literal_is_a_file_the_probe_reads` |
| **F7** 登记不绑定它声称冻结的协议文档 | 新增封闭块 `agent_forward_protocol_document`（`agent-forward-protocol-document/v1`：`docs/research/p7-c-protocol.md` 的仓库相对路径 + 其**已提交**字节的 sha256），读取器在评估器；写入器要求协议文件已提交且干净、`git show HEAD:` 字节与工作区逐字节相同，`c_read_back` 再核对；链测试在真登记写出后核对 | `test_agent_incremental_ic.py::test_the_frozen_protocol_document_block_is_read_with_no_defaults`、`test_p7_c_register.py::test_refuses_an_uncommitted_protocol_document`、`::test_refuses_a_dirty_protocol_document`、`::test_c_read_back_refuses_a_mismatched_protocol_document` |
| **F8** 弃权率与模型别名读了窗口外的记录 | `_stored_primary` 与 `_model_aliases` 都只统计 `anchor <= session <= through`；缺日志集合仍是窗口内 C 的主集记录 | `test_agent_report.py::test_the_abstention_and_model_alias_figures_are_window_bounded` |
| **F9** 投影用期望计数、忽略模型弃权 | 投影改为 `round(E·min(sample,S)/S·(1−abstention_rate))`；`--abstention-rate`（默认 0.10，规划假设，干跑周测真实值、L5 可覆盖）；报告与协议 §6.5 写明期望计数（非逐会话超几何）与弃权的简化 | `test_p7_c_power.py::test_model_abstention_reduces_the_scored_names_and_can_make_a_session_unmeasurable`、`::test_the_projection_is_the_expected_post_abstention_scored_count` |

### 8.1 本轮的行为改变与记录

- **F1 的“开始”口径**：marker 在本会话的 Step 0 读取与迟到检查之后、第一次模型请求之前写。因此“Step 0 还没 file 这一天”或“已过集合竞价”的拒绝不消耗五晚上限（它们没有发出任何请求、也不是一次测量）；而模型路径能产生的任何 status（cap/期限/传输/配置/凭据失败）都在 marker 之后，全部计数。这是对“每个开始的晚间”的可辩护读法，写在模块 docstring 与运行手册里。
- **F4 的诚实后果**：默认 `sd_between = 0.15` 时行 sd ≥ 0.15、期数 ≤ 120，可检出均值下界约 0.034 > 0.02，所以只要观测/可测下限满足，两行在默认值下都会是 `not_testable (detectable_above_threshold)`（n300 与普查都是）。0.02 只在更小的 `--sd-between` 下可达（普查规模约 0.071；n300 那样每会话约 54 个记分名的横截面在任何 sd_between 下都到不了）。因此 L5 的默认运行很可能落在 D1 的 both-not_testable 分支（用户的 `--register-untestable` 或不登记），或按干跑周观测覆盖 `--sd-between`。这是设计层面的诚实结果，不是缺陷；已登记检验本身不变（幅度仍不设门槛）。
- **F3 的提交时刻语义**：期望锚点是“16:30 发布时刻严格晚于提交时刻”的第一个交易日——交易日 16:30 之前提交则当天就是锚点，之后提交或周末/节假日提交则顺延到下一个交易日。没有“星期一日”规则。写入器只能检查工作日与不早于今天；精确检查在报告侧（L6 之前就把错锚点拒掉）。
- **F5 的路径兼容**：功效命令记录的是它被给定的 Step 0 路径（CLI 解析为绝对路径），写入器接受记录路径与仓库相对路径逐项相等，或绝对路径以 `/` + 仓库相对路径结尾；真正的绑定是 sha256 与 config_id。
- **F7 的绑定范围**：协议文档的 sha256 记录的是**已提交**字节；写入器拒绝未提交/脏的协议文件，`c_read_back` 再按已提交字节核对。协议正文在修复轮里被修改（控制器裁定的更正），§14 追加了一条记录。登记文件仍是用户 L5 的提交，本轮没有写。
- **F6**：文档里保留了旧字面量作为“会被探针拒绝”的反例引用，测试只要求至少一个**有效**字面量存在并能被探针读取（被拒绝的旧字面量不是 L1 的起始文件）。
- **本轮没有改**：§5 的顺序、§5.2 的闸门、issue 总数（254）、D5 的检验规则与阈值语义（0.02 只是规划可检出性阈值）、L1–L6 的受闸状态；协议正文的其它冻结数字（窗口 120、中期 60、min_scored 10、min_measurable 60、q=0.10 等）不变。决策链 §9 追加了一行记录本轮更正（不改旧行）。

### 8.2 修复轮 1 的 TDD 首红（实现者报告）

- `power`：整文件 `19 failed, 6 passed`，首两条是 `AttributeError: module 'p7_c_power' has no attribute 'MIN_OBSERVED_SESSIONS'` 与 `TypeError: event_power() got an unexpected keyword argument 'sd_between'`；每个行为另用“变异 → 单测红 → 逐字节恢复（sha 核对）”复证（sd 上限、0.02 判定、观测下限、弃权、可检出均值上限、报告契约）。
- `register`：`48 failed, 21 passed, 4 skipped` 对旧实现：12 条 `c_registration_body() got an unexpected keyword argument 'protocol_document'`、20 条“旧写入器接受了必须拒绝的报告/协议文档”、未来锚点被旧规则拒绝、`c_read_back` 缺新块/新异常。
- `report`：F1/F2 首红 `4 failed`（失败晚间无 marker、五失败晚间后 marker 仍 0、已登记晚间不拒绝 marked runtime、`--pin-worktree` 仍调用 `registered_checkout`）；F3/F8 首红 `3 failed`（锚点与提交不符仍返回 0、窗口外记录仍计入）。
- `Lead`（F7 读取器）：`ImportError: cannot import name 'AGENT_FORWARD_PROTOCOL_DOCUMENT_KEY'`；F6：文档里的 `{"spent": 20}` 被探针按名拒绝（2 failed）。

### 8.3 修复轮 1 之后跑过的门槛（定向）

- 新增/改动测试一起：`tests/unit/scripts/test_p7_c_power.py`（25）、`test_p7_c_register.py` + `test_p7_registration_chain.py`（71 passed、4 skipped）、`test_agent_report.py` + `test_agent_evening.py`（73 passed）、`tests/integration/test_agent_incremental_ic.py`（47）、`tests/integration/test_usage_claims_match_the_shipped_paths.py`（48）、`tests/unit/scripts/test_p7_event_probe.py`、`tests/unit/test_documentation_tables.py`、`tests/unit/test_feature_ledger_test_tree.py`：合计 **311 passed、4 skipped in 498.15s**。
- `ruff check .` 通过；`ruff format --check .` 710 文件；`mypy --cache-dir=/dev/null src scripts` 216 文件无问题；`PYTHONPATH=src lint-imports` 10 kept / 0 broken；`scripts/verify_publication.py` ok（838 文件）；`scripts/build_feature_coverage.py --check` 通过（244 项、真实完成 239；台账 4 行的正文与备注已补修复轮说明）。
- **跨切面审计**（`pytest tests/unit/domain tests/unit/test_*.py tests/unit/scripts`）在最终 tip 上重跑；结果见 §8.4（本记录在审计完成前写下这一节，随后补充）。

### 8.4 修复轮 1 之后的串行跨切面审计

（待补充：`pytest tests/unit/domain tests/unit/test_*.py tests/unit/scripts` 的最终 summary line 与唯一的环境依赖失败说明。）
