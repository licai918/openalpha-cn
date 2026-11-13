# 方案 C 预登记研究协议：智能体分数的增量秩信息（`agent_forward/v1`）

> **本文件在 C 的任何前向运行之前写出。** 写出时 C 的登记（`docs/research/p7-c-registration.json`）**还不存在**，
> 也没有任何一次前向晚间、干跑周或真实读数发生。本文件与那份登记一起构成预登记：登记一旦提交，判据、窗口、
> 停止规则与功效设计就固定下来，改任何一条都是另一份登记（换运行目录、换纪元）。
>
> 一切结论词的强度只到 **候选**：本文描写的是一个候选研究配置的预登记设计，不是已经成立的结果。
> **工程完成不是研究证据**（`AGENTS.md`「研究诚实」）；测试通过、命令能跑、功效能算，都不把任何一行
> 升级成「已验证」「证实有效」或「proven」。
>
> **本文在预登记之前可以按控制者的裁决更正正文，每次更正都在第 14 节留一条记录；看到结果之后不再改正文，
> 只追加第 14 节。** §6 的功效表在本批次**未运行**（没有读真实面板、没有真实数据、零请求），它以
> 「未运行」的形式留在这里，由 L5 运行后把打印的两行粘贴进来。

## 0. 状态与身份

| 项 | 值 | 出处 |
|---|---|---|
| Issue | `V2-P7-013`（晚间命令与登记）、`V2-P7-014`（评估器与判据）、`V2-P7-015`（只前向护栏） | `docs/specs/v2/openalpha-cn-v2-roadmap.md` 的对应行 |
| 协议文件 | 本文件 `docs/research/p7-c-protocol.md` | 方案 C 批次 3 任务 3.2 |
| 登记文件 | `docs/research/p7-c-registration.json`（**尚未写出**，L5 由用户提交） | `scripts/agent_evening.py` 的 `REGISTRATION_IN_REPOSITORY` |
| 登记写入器 | `scripts/p7_c_register.py`（受绑定路径之外，运行一次） | 方案 C 批次 3 任务 3.3 |
| 登记的 `config.kind` | `agent_forward/v1`：**永远不进幸存者登记链** | `scripts/agent_evening.py` 的 `AGENT_REGISTRATION_KIND` |
| 登记绑定的代码 | `scripts/agent_evening.py` 与 `scripts/agent_report.py` | `src/openalpha_cn/strategy_agent_forward.py` 的 `AGENT_SCRIPTS` |
| 评估器 | `src/openalpha_cn/strategy_agent_evaluation.py`（`agent-forward-protocol/v1`） | 方案 C 批次 2 任务 2.7 |
| 报告命令 | `scripts/agent_report.py`（与 Step 0 的周报分开，D8） | 方案 C 批次 3 任务 3.5 |
| 前向记录 | `agent_llm_structured/v1`、`agent_rule_baseline/v1` 两个家族，`standing=forward`，不交易 | `src/openalpha_cn/domain/agent_forward.py` |
| 状态 | 协议写出、登记未写出、前向窗口未开始；L1–L6 全部未运行 | 第 12 节；`docs/runbooks/agent-evening.zh-CN.md` |

本文件写于登记提交之前。登记提交、它的 sha256、`config_id`、登记里冻结的 `--anchor` 会话、钉住的 commit 与
C 时钟的起点，在 L5 之后追加到第 14 节；正文的其他文字不因此改动。

## 1. 假设（候选）

1. **主假设 H1（候选）**：在 Step 0 的幸存者内部，模型智能体家族 `agent_llm_structured/v1` 在**主集**上的
   分数，对下一会话（h=1）的前向收益，在扣除 Step 0 已存合成分（`survivor_book_composite/v1`）已经携带的
   秩信息之后，仍有**正的**增量秩信息：偏秩 IC 的均值 > 0。
2. **原假设 H0**：该增量秩 IC 的均值 ≤ 0（或者区间里看不出方向）。检验只在预登记的窗口里、按第 5 节的
   判据做一次；任何一次读数都只是**候选**。
3. **探索性方向（永远探索性，不是假设检验的对象升级）**：规则家族 `agent_rule_baseline/v1` 的读数、
   h=5 的读数、覆盖集上的读数（第 7 节）。它们不能把主结论改写成别的措辞，也不能把「主检验没通过」
   翻译成「换一个集/期限就通过了」。
4. 以上都不是已经得到的结论。**智能体层有没有增量**这个问题，只有前向窗口里的那次检验能回答；
   制度性的事实（本文件已写出、命令能跑、功效计算可运行）不是它的答案。

本协议**不检验**智能体层的组合收益，也不检验任何交易政策：C 不交易、不改持仓、不产出可下的单。
纸面账簿的权益不是证据，C 根本没有纸面账簿。

## 2. 登记与身份

- **一份独立的登记，一本独立的运行目录。** C 用自己的登记、自己的运行目录、自己的钉住 worktree
  （推荐，不是代码强制），与 Step 0 的登记和运行目录分开。理由见决策链 §5.1（2026-11-01 更正）：
  登记绑定整个 `src/`、`scripts/research/`、`pyproject.toml`、`uv.lock` 与两个每日脚本；上线后改任何受绑定
  字节都是另一份登记。
- **不进幸存者链。** `p7_register.py` 的链只收 `kind` 为 `survivor_book/v1` 的登记；C 的 `kind` 是
  `agent_forward/v1`，结构上不在那条链里，也不需要点名任何前任。C 评估**另一份**登记（Step 0 的），
  并在自己的 `settings.agent_forward_step0` 里记录那份前任（路径、`config_id`、sha256、种子），由报告读回
  （本节与第 5.8 节）。
- **登记一次写、不由写入器提交。** 写入器只打印（`--dry-run`）或写文件；提交 `docs/research/p7-c-registration.json`
  是用户在 L5 的步骤。写入器拒绝：DeepSeek 端点未核实（`verified_on` 至今为 `None`）、盐与 Step 0 的种子
  相同或由它派生、Step 0 的登记未提交（或提交后被改过/是别的 `kind`）、功效报告缺失或不合 schema、
  功效报告的 `source` 为空或不是写入器绑定的那份 Step 0 登记（`registration_sha256`、`config_id`、路径
  逐项相等）、报告 `planning` 里的窗口/可测下限/记分下限/检验水平/目标功效/可检出门槛不等于已采纳的常量、
  两行都不可测时没有 `--register-untestable`、锚点早于今天（上海）或不是工作日（未来工作日允许；
  规则见第 5.3 节）、`docs/research/p7-c-protocol.md` 未提交或工作区有改动（登记绑定它已提交字节的
  sha256）、`--path` 不在 `docs/research/*.json` 里、该路径上已有一份不同的登记、受绑定代码或两个 C 脚本
  有未提交改动、以及写入的字节与打印的 sha256 不符（刚写的文件删掉）。每条拒绝退出 2、什么都不写。
- **登记内容**：`config` 里的 `kind`、`salt`、`primary_size` 是 C 的抽样参数（无默认值，命令读到缺省即按名拒绝）；
  `settings` 里有五个封闭的块，评估器、报告与测试都按名读、都没有默认值：
  `agent_forward_protocol`（`agent-forward-protocol/v1`，第 5.8 节）、
  `agent_forward_power`（`agent-forward-power/v1`，D1 的选择：样本、判定、`register_untestable`、
  报告 sha256、可测会话与可检出均值）、
  `agent_forward_step0`（`agent-step0-registration/v1`，C 评估的 Step 0 登记：路径、sha256、`config_id`、
  种子）、
  `agent_forward_anchor`（`agent-forward-anchor/v1`，登记的第一个前向会话，窗口的锚点）与
  `agent_forward_protocol_document`（`agent-forward-protocol-document/v1`，本协议文件的**已提交字节**：
  路径 `docs/research/p7-c-protocol.md` 与 sha256——登记就是靠它绑定它声称冻结的那份文字）；
  `criteria` 是第 4–7 节判据的自由文本；`code_commit` 是登记绑定提交的完整 40 位。
- **盐的规则。** 登记的盐是一个人工选定的字符串，命令与写入器都拒绝它与 Step 0 的登记种子相等或由它派生
  （`domain.agent_forward.refuse_a_salt_tied_to_the_book`：规范化字母数字投影相等、或盐里任一数字串包含种子
  数字即拒绝；过包含是保守方向）。盐只决定主集抽到哪些名字（普查时只决定排序），不改变任何判据。

## 3. 评分集合

### 3.1 主集（唯一主统计量的集合）

- 每个会话，从 Step 0 **当日已存记录**重建出的幸存者集合里抽：`sha256(f"{salt}|{session.isoformat()}|{ts_code}")`
  升序取前 `primary_size` 名，输出按 `ts_code` 排序（`domain/agent_forward.py::primary_sample`）。
- 抽的是**会话的函数**，不是一份固定名单：集合随 Step 0 的幸存者逐日变；同一个 (盐, 会话) 重算逐字节相同。
- 容量 ≥ 幸存者数时是**普查**（同一条排序截断）：这就是第 6 节 D1 的「只有普查可测就用普查」落进登记的方式。
- 抽样函数不接收持仓，所以主集看不到账簿；空盐、`size < 1` 按名拒绝。

### 3.2 覆盖集（永远探索性）

- Step 0 当日日志 `result` 里的**持仓与观察名单**（`plan_names`），只保留当日幸存者，排序去重
  （`domain.agent_forward.py::coverage_set`）。覆盖集不接收盐，所以拉不进主集。
- 入样概率不同，**永不与主集混成总体**：两条声明（`c-primary`、`c-coverage`）、两条记录、分开评估。
  主统计量只在主集上做。
- 覆盖集为空是受支持的状态：当天不 file 覆盖集批次，日志记 `coverage_absent: "empty_set"`——
  那不是缺口（`no_record`），是「这个集合今天没有名字」。

### 3.3 评分域（评估器怎么把集合落成横截面）

每次会话的评分域 = **Step 0 当日存储记录重建的幸存者** ∩ 该臂当天记了分的名字。Step 0 没留下的名字
不进主统计量；没有分数的名字（弃权）不是 0，而是离开该次会话的横截面（第 4.4 节）。

## 4. 家族、名册、提示词、分数映射、弃权码

### 4.1 两个家族

| 家族 | 性质 | 期限 | `agent_kind` | 记录 |
|---|---|---|---|---|
| `agent_llm_structured/v1` | **唯一主统计量的家族**（D2） | 记录声明 `5d`；主检验读 h=1 | `llm_backed` | 主集、覆盖集各一条（覆盖集为空时只有主集） |
| `agent_rule_baseline/v1` | 永远探索性（D2） | 记录声明 `5d` | `deterministic` | 主集、覆盖集各一条（覆盖集为空时只有主集） |

两条记录的名字是 `c-primary`、`c-coverage`；`feature_ids=("agent_evidence/v1",)` 是「证据平面」的声明，
不是声称读了某个面板因子列；`as_of` = 会话 16:30 上海发布时刻，`training_cutoff` 等于它（非拟合记录的先例）；
provenance 取 C 登记的 sha256，缺失即按名拒绝（`strategy_agent_forward.file_agent_batch`）。

### 4.2 证据名册 v1（D4）

**v1 只有 `V2-P7-011` 的涨停/炸板/连板事件**：`limit_up`、`consecutive_board`、`broken_board` 三个
`market_event` 种类，由 Step 0 的已存面板在会话自己的 16:30 时刻推出、写进 C 自己的证据库
（只写有事件的证券，从不写面板行；硬规则 1）。

- 路由按证据族：`MarketAgent`（`market_event`）是 v1 里唯一会被路由的智能体；`ThemeAgent`
  （`theme`/`catalyst`/`disclosure`）与 `CapitalAgent`（`capital`）仍在 `baseline_agents()` 的名册里，
  但 v1 没有任何生产者喂它们，**documented as never routed**。
- **不在 v1 里、也不在本登记里**：资金流（`moneyflow`）、龙虎榜（`top_list`/`top_inst`）、公告（`anns_d`）
  等事件数据。它们要等 `V2-P7-010` 的受闸探针（L1）报告权限、单日请求量、历史起点与时点字段之后，
  由**另一份登记**（下一次前向登记）决定要不要进名册；不是本登记的修订。理由：没有核实过的接口不能
  写成设计，事后扩名册会改变样本的横截面与成本模型。

### 4.3 提示词与调用

- 提示词 `structured-signal/v2-json-object-examples-v2`（`agents/prompts.py`）：system 含小写 `json` 与
  恰好两个示例（一个有方向的 scored、一个 abstain，占位值中立），user 载荷带**证据事实**
  （`include_facts=True`）；两个示例都由真实的 `StructuredAgentPayload` 校验接受。
- `max_attempts=2`：一次无效回答重试一次；**空白或纯空白的内容**同样是无效回答，不是空对象。
  V1 提示词的字节与摘要不变；本命令用 V2，每次 V2 交换按内容寻址记录，回放只读 V2 的已存响应。
- 模型家族走 `research_engine`/`run_cycle`（与规则家族同一个引擎），所以 `V2-P7-015` 的历史/72 小时护栏
  与运行清单的 model/prompt provenance 在结构上落在模型路径上；**引擎在这条路径上是为了护栏、清单与
  回放记录，不是为了数字**：每个名字的分数取单个 agent 自己的 `SignalFrame`（见 4.4），不取引擎聚合。

### 4.4 分数映射与弃权码

- `score_of(direction, strength, abstention_reason)`：bullish → `+strength`，bearish → `-strength`，
  neutral → `0.0`（`-0.0` 归一为 `0.0`）。**没有中性带**：一个方向答案保留它自己的强度，`|strength|`
  不因小于任何阈值被中和；这就是 `V2-P7-013` 交付时 D3 的语义。
- 弃权是编码弃权，不是数字：`None` + 一个封闭的码。三个码：
  - `agent_not_routed`：引擎没有路由出任何 agent 结果（该名字当天没有 agent 可用的证据/产物）；
  - `agent_abstained`：agent 自己弃权（无论它引用了多少证据）；
  - `model_invalid_output`：两次尝试之后仍是无效回答（含空白内容）。
- **弃权永不记 0**，也不参与 IC：它离开横截面，弃权率单独报告。词汇外的 reason、未知方向、
  非有限强度、方向帧带弃权理由、弃权帧带非零强度都按名拒绝（自由文本会进到评估器要解读的已存记录）。

### 4.5 一天里的写入

一个完整晚上写：两条规则家族批次（主集 ∪ 覆盖集逐名一次 `run_cycle`）、两条模型家族批次（同一引擎，
`--no-llm` 时跳过并按 D6 记 `no_record: llm_disabled`）、证据批与一份写一次的日志。重跑重算同一天的
字节相同；不同的日志按名拒绝，第一份账留下。

## 5. 唯一的主统计量、检验、窗口、中期与可测性（D5 已采纳）

### 5.1 统计量

**每会话一个偏秩 IC**：`domain/incremental_ic.partial_rank_ic(agent, factor, label)`——
平均并列的 Spearman 秩之后，把 rank(agent) 与 rank(label) 都对 rank(factor) 做带截距 OLS 取残差，
再求 Pearson。factor 是 **Step 0 当日存储的合成分**，label 是 **h=1** 的前向收益，只在它自己的退出会话
16:30 之后经 `model_view.OutcomeLabels` 读（未到期只计数、从不打标签）。样本 < 4、长度不符、非有限输入、
factor 秩无变化、残差零方差 → `None`（伴生函数给具名 reason），**绝不补 0**。

### 5.2 主臂

LLM 家族 `agent_llm_structured/v1`、主集 `primary`、h=1。协议读取器与公开的 `AgentProtocol` 都钉住这三项：
主家族不是 LLM 家族、主集不是 `primary`、主期限不是 1 的协议按名拒绝；h>1 只能作为探索性臂出现在
`secondary`（`PRIMARY_HORIZON`，`strategy_agent_evaluation.py`）。这样「主统计量只有一条」不是口号，
读完登记即成立。

### 5.3 窗口

- **窗口 = C 登记提交之后的前 120 个前向会话**（D5）：位置是 Step 0 已 file 的、`anchor_session <= session <= through`
  的会话按升序取前 `window_sessions = 120` 个。
- **锚点在登记时冻结，且严格在提交时刻之后。** `anchor_session` 由用户在 L5 用 `--anchor` 给定、写进 C 的
  登记（`settings.agent_forward_anchor`），报告从登记读回它。它是 **C 的登记提交时刻之后严格意义上的
  第一个交易日**：一个交易日 d 是锚点，当且仅当它的 16:30 上海发布时刻**严格晚于**提交时刻——
  提交落在周末或节假日 → 下一个交易日；提交落在交易日 16:30 之前 → 当天就是锚点；16:30 之后 → 下一个
  交易日。**永远不是存储里最早的会话。** 写入器只检查工作日与不早于今天（上海）：它允许一个未来工作日
  （提交之后才到的那天），没有回看窗口；报告在 L6 用提交时刻精确核对锚点，不一致即按名拒绝。
  锚点之前存着的记录（别的登记写的、干跑写的、窗口开之前写的）不能移动窗口。
- **缺口只减少 n，不移动窗口**：模型家族某天没 file（第 8 节）是一个 `no_record` 缺口；它减少可测会话数，
  后面的位置不往内挪。窗口在**每个位置都到期**之后才做一次检验；未满只印计数与描述性均值，**不给 p 值、
  不给结论词**。

### 5.4 检验（D5 采纳的唯一一次检验）

- 检验是符号翻转随机化检验（`outcome_statistics.sign_flip_test`，双侧随机 p），**单侧读数**按
  幸存者周报的先例从双侧导出：均值为正时 `p/2`，均值为负时 `1 - p/2`；检验水平 `alpha = 0.05`，
  登记的备择方向是「均值 > 0」。
- **通过 iff 窗口均值 > 0 且单侧读数 ≤ 0.05**。量级（0.02、0.05 之类）**只描述、不是已登记检验的门槛**：
  D5 没有把任何效应量阈值裁成这条检验的判据。（第 6.2.2 节的 **0.02 是规划检出阈值**，它只决定
  「这个设计能不能测」，**不是**这条检验的通过条件；两者不要混读。）
- **这一条取代 G-C 的旧措辞**「h1 ≥ 0.02 且 t ≥ 2，或 h5 ≥ 0.06」：旧措辞是**两条检验**（h1 的幅度加
  t 值，以及 h5 的另一条路），会把假设家族悄悄放大成两条。改写以**决策链 §9 追加的新行**记录（旧行不改）；
  本文件只引用那次改写，规则是「一个主检验、单侧、α=0.05」。
- 临界值的实现：符号翻转随机化检验的抽样次数 `bootstrap_samples = 100000` 与随机种子
  `random_seed = 20260926` 是协议块的两个必填字段，由写入器在 L5 填进登记并冻结：前者取
  `scripts/research/grid.py` 的 `PROTOCOL_BOOTSTRAP_SAMPLES`，后者取同一模块的 `PROTOCOL_RANDOM_SEED`
  （本仓库登记的随机种子，**不是** C 的抽样盐——盐是另一个登记值，与 Step 0 的种子耦合即拒绝）。
- 检验的样本数与可测性口径：h=1 的窗口位置是 120 个会话，可测会话达到 `min_measurable_sessions` 才做
  那一次检验；样本数与可检出均值永远随读数一起打印。

### 5.5 中期无效性（D5 采纳，保留全部守卫）

- 中期看一次，在**前向第 60 个位置**（`interim_session = 60`）。
- 只有在**前 60 个位置全部到期**、且其中至少 `max(2, ceil(min_measurable_sessions × 60 / 120)) = 30`
  个位置携带数值（**按比例的可测下限**）时，才可能停止。前 60 个位置的**累积均值 ≤ 0** → `futility_stop`，
  停止词是「no increment detected (futility)（没有检出增量）」。薄的或未到期的中期**不是停止**。
- 停止是 **sticky** 的：它在窗口满/未满的分支之前判断，因为它是用前 60 个位置不可变的数据做出的决定；
  窗口后来填满或成熟，都不能把它抹掉。
- 触发、未触发与「不可测」是三个不同的结论；「未触发」只表示不触发停止规则，**不是证据**。

### 5.6 可测性（D3/D5）

- 一个会话**可测** iff 在把 agent 记录、Step 0 合成分与标签 join 之后，主集上至少有 `min_scored = 10` 个
  记了分的名字。
- 窗口**不可测** iff 120 个位置里可测的少于 `min_measurable_sessions = 60` 个。
- **「不可测」是它自己的正式结论**（`not_measurable`），它自己的措辞（`NOT_MEASURABLE_WORDING`：
  「fewer than 60 … this is not 'no evidence', and no p-value is computed」），**绝不读成「没有证据」**。
  不可测时没有 p 值，也没有方向词。

### 5.7 五种结论与措辞

| 结论 | 什么时候 | 允许说的 | 不允许说的 |
|---|---|---|---|
| `not_yet` | 窗口未满，或有位置未到期 | 计数、描述性均值、缺口 | p 值、结论词 |
| `candidate_increment` | 窗口满足且主检验通过 | 候选的、正增量（按登记判据） | 已验证、证实有效、效应 |
| `no_increment_detected` | 窗口满足且检验未通过 | 没有检出增量（按登记判据） | 「证明没有增量」 |
| `futility_stop` | 第 60 个位置的中期按 5.5 触发 | 按停止规则停止（futility） | 用停止时的数字做结论 |
| `not_measurable` | 窗口满足但可测会话 < 60 | 「不可测」及其原因 | 「没有证据」、p 值、方向 |

### 5.8 评估器要读的登记块

`settings["agent_forward_protocol"]` = `agent-forward-protocol/v1`，`extra="forbid"`、**每个字段都必填、
没有默认值**（默认值就是代码替用户选的协议值）。字段与登记值：

| 字段 | 登记值 | 来源 |
|---|---|---|
| `schema_version` | `agent-forward-protocol/v1` | `strategy_agent_evaluation.py` 的 `AGENT_FORWARD_PROTOCOL_SCHEMA` |
| `primary_family` | `agent_llm_structured/v1` | D2 |
| `primary_set` | `primary` | D2 |
| `primary_horizon` | `1` | D2（记录声明 `5d`，检验读 h=1） |
| `secondary` | 三个探索性臂（第 7 节） | D2/D5 |
| `window_sessions` | `120` | D5 |
| `min_scored` | `10` | D3 |
| `min_measurable_sessions` | `60` | D3 |
| `interim_session` | `60` | D5 |
| `level` | `0.05` | D5 |
| `q` | `0.10` | D5（探索性族） |
| `bootstrap_samples` | `100000`（`scripts/research/grid.py` 的 `PROTOCOL_BOOTSTRAP_SAMPLES`） | 写入器硬编码；符号翻转的抽样次数 |
| `random_seed` | `20260926`（`scripts/research/grid.py` 的 `PROTOCOL_RANDOM_SEED`） | 写入器硬编码；符号翻转的随机种子，不是抽样盐 |

读取器还拒绝：`primary_family` 不是 LLM 家族、`primary_set` 不是 `primary`、`primary_horizon` 不是 1、
`window_sessions < 2`、`min_scored < 4`、`min_measurable_sessions < 2` 或大于窗口、中期不在窗口内部、
`secondary` 点名主臂或重复臂、未知键。

登记里另外四个块与协议块一起冻结（同一个 `settings`，同样封闭）：

| 块 | schema | 记录什么 | 谁读 |
|---|---|---|---|
| `agent_forward_power` | `agent-forward-power/v1` | D1 的选择：`sample`（`n300`/`census`）、`verdict`（`testable`/`not_testable`）、`register_untestable`、功效报告的 sha256、可测会话数与可检出均值 | 写入器、报告、测试（`read_agent_forward_power`） |
| `agent_forward_step0` | `agent-step0-registration/v1` | C 评估的 Step 0 登记：`path`、`registration_sha256`、`config_id`、`seed` | 报告（`read_agent_forward_step0`） |
| `agent_forward_anchor` | `agent-forward-anchor/v1` | `session`：登记的第一个前向会话，窗口的锚点（第 5.3 节） | 报告（`read_agent_forward_anchor`） |
| `agent_forward_protocol_document` | `agent-forward-protocol-document/v1` | 本协议文件的已提交字节：`path`（`docs/research/p7-c-protocol.md`）与 `sha256` | 写入器、报告、测试（`read_agent_forward_protocol_document`） |

`agent_forward_power` 的两个语义约束是失败即关闭的：`verdict = not_testable` 时必须同时记录
`register_untestable = true`（不可测的集合只能是用户明确的选择）；`verdict = testable` 时不得带
`register_untestable`（那个开关只为不可测分支存在）。

`agent_forward_protocol_document` 把登记绑到它冻结的那份文字：写入器拒绝一个未提交或工作区有改动的
`docs/research/p7-c-protocol.md`，并把已提交字节的 sha256 写进登记；登记提交之后再改本文件的正文
（不是追加第 14 节）就是与登记不符，读者按登记里的 sha256 核对。

## 6. 功效（D1）：事件级、两个样本、逐行判定

### 6.1 为什么是事件级

证据只写有事件的证券（v1 的市场级涨停事件约每天 10² 个名字），而智能体**只会被路由到有证据的名字**：
在 300 只的幸存者样本里，一天可能只有几个名字带分数。所以决定 C 能不能测的，不是「300 只够不够」，
而是**每个会话有多少个记了分的名字**。功效计算因此在事件级做，并同时给出两个样本的行。

### 6.2 命令（零请求、只读、不读标签与收益）

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" "$PWD/.venv/bin/python" scripts/p7_c_power.py \
  --panel-dir <已存面板目录> \
  --step0-registration docs/research/p7-registration.json \
  --first <首个会话 YYYY-MM-DD> --last <最后会话 YYYY-MM-DD> \
  --sd-between 0.15 --abstention-rate 0.10
```

- `--sd-between`：**非抽样部分**的日间标准差（默认 0.15）。它是一个**规划假设**，不是测量值。
- `--abstention-rate`：规划用的弃权率（默认 0.10）。它同样是**规划假设**：D7 干跑周测真实弃权率
  （第 12.1 节），L5 用实测值覆盖它并重跑本节的命令；本文件从不把它写成测量值。
- `--json PATH` 写一份 `openalpha-p7-c-power/v1` 报告——那份报告就是写入器 `--power-report` 要的文件；
  不加只打印、什么都不写。
- 只读已存面板与 Step 0 的**已提交**登记；**不构造任何传输、不发任何请求、不读任何标签或收益**；
  每个会话按它自己的 16:30 点时间重放（幸存者与事件是那晚记录会显示的样子，不是今天的重述）。
- 它打印**两行**：`n300`（主集 N=300）与 `census`（全部幸存者普查），每行给：观测历史会话数、
  期望记分数（投影）、可测会话（投影）、期数、**会话 sd（可测会话里的最大值）**、**可检出均值**、
  **可检出门槛 0.02**、判定 `testable` / `not_testable`，以及判定为 not_testable 时的具名原因。

#### 6.2.1 两分量 sd 与可检出均值

- 一个可测会话在**扣除弃权后**有 n 个记了分的名字：会话 sd = `min(1, sqrt(sd_between^2 + 1/(n-1)))`
  （n ≥ 2；n = 1 时取 1.0）。`sd_between` 是**非抽样部分**，`1/(n-1)` 是记分名不足带来的抽样项：
  名字越少，单会话 IC 越噪。
- 一行的 sd = 该行**可测会话里的最大值**，恒 ≤ 1（秩 IC 的 sd 不可能超过 1）。
- 可检出均值 = `(z_{1-α} + z_power) · sd / sqrt(periods)`：单侧 α = 0.05、目标功效 0.80，
  期数只数**可测会话**（h=1 时一期一会话），结果再封顶 1。它是**设计信息**，不是观测功效，也不是任何读数。
- 这一条替换了旧模型。旧模型把整个 planning sd（其中含有真实的日间 IC 变化）按
  `sqrt(mean_survivors/mean_scored)` 一起放大，能打印出 > 1 的 sd 与对秩 IC 不可能的可检出均值；
  新模型把两部分分开，并把 sd 上界钉在 1。

#### 6.2.2 判定与三个具名原因

- **`testable` iff 观测历史会话 ≥ 20 且投影可测会话 ≥ 60 且可检出均值 ≤ 0.02**：三个条件全部满足
  才是 `testable`。
- **每一行都打印可检出均值与 0.02 阈值**；判定是这三项检查的结论，不是某个单独字段的回声。
- `testable` 的意思是：在这个投影上，**设计能以 80% 功效检出均值 0.02 的效应**；它**不是**说已登记的
  检验对后来观察到的效应有足够功效（那取决于真实样本的离散度，本文件不作此声称）。
- 0.02 的出处：它是**旧 G-C 的 h1 门槛**，在这里只作为**规划可检出门槛**使用；它**不是**对已登记检验的
  量级门槛——已登记的检验仍按第 5.4 节：均值 > 0 且单侧 p ≤ 0.05 才通过。
- `not_testable` 的三个具名原因：`too_few_observed_sessions`（观测历史会话 < 20）、
  `below_measurable_floor`（投影可测会话 < 60，或期数 < 2 算不出可检出均值）、
  `detectable_above_threshold`（可检出均值 > 0.02）。行里随判定打印原因；多个条件同时不满足时按这个
  优先级取第一个。

#### 6.2.3 默认参数下的后果（如实的设计结果）

- 在默认的 `--sd-between 0.15` 下，`session sd >= 0.15`；窗口至多 120 个期，所以可检出均值至少是
  `2.4865 × 0.15 / sqrt(120) ≈ 0.034`，**大于 0.02 的规划门槛**。
- 因此，对任何「观测历史会话 ≥ 20 且投影可测会话 ≥ 60」的输入，**默认参数下两行（n300 与 census）
  都是 `not_testable`，原因都是 `detectable_above_threshold`**。
- 0.02 的门槛只有在给出更小的 `--sd-between` 时才可能达到：L5 可以用 D7 干跑周实测的离散度/弃权率
  重跑第 6.2 节的命令，再决定登记。对**普查规模**的截面（每会话记分名数很多、采样项很小），
  `sd_between` 约 0.071 时跨过 0.02；对**主样本那样的小截面**，采样项自己就太大——每会话约 54 个记分名时
  `1/sqrt(53) ≈ 0.137`，已超过 `0.02·sqrt(120)/2.4865 ≈ 0.088`，无论 `sd_between` 多小都达不到。
- 这是**如实的设计结果，不是缺陷**：它说的是「用默认的保守规划假设，这个设计检不出均值 0.02 的
  增量」，不是「智能体没有增量」，更不是任何读数。它**不改变已登记的检验**：量级仍不设门槛（第 5.4 节）。
- 于是按 D1 的规则，默认跑法会把 L5 带到「两行都不可测」的分支（第 6.4 节第 3 条）：用户要么以
  `--register-untestable` 登记为不可测，要么不登记，要么先用干跑周实测的值重跑。选择是用户的。

### 6.3 本批次的功效表：**NOT RUN / 未运行**

**下表在方案 C 批次 3 中没有运行：没有读真实面板、没有真实数据、零请求。** 表里的每个数字都来自 L5
运行 6.2 的命令后把打印的两行粘贴进来；粘贴之前它们一律是「未运行」。**不得**用任何估计值、模拟值或
别处的读数填这张表。

| 行 | 样本 | 观测会话数 | 期望记分数（投影） | 可测会话（投影） | 期数 | 会话 sd（最大） | 可检出均值 | 阈值 | 判定 / 原因 |
|---|---|---|---|---|---|---|---|---|---|
| `n300` | 主集 N=300（登记的 `primary_size`） | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |
| `census` | 全部幸存者普查 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |

「未运行」不是「不可测」，也不是「可测」：它只表示本批次没有做这件事。L5 之前没有任何人可以把这张表
读成任何结论。

### 6.4 D1 的判定规则（写入器自动执行）

1. `n300` 行判定 `testable` → 主集就用 N=300：登记 `config.primary_size = 300`。
2. 只有 `census` 行 `testable` → 主集用**普查**：登记 `config.primary_size = 1000000`
   （`CENSUS_DRAW_SIZE`，一个不小于任何一天幸存者数的抽样容量；`primary_sample` 的规则「容量 ≥ 当日集合
   大小即返回普查」于是给出整个幸存者集合，盐只用来**排序**）。这是换评分域，所以它作为 D1 的裁定先于
   登记写入，写入器按功效报告自动选，不需要也不接受 `--sample` 之类的参数。
3. 两行都 `not_testable` → 两条出路，**选择是 L5 时用户的**：把 C 登记为「不可测」
   （写入器只有 `--register-untestable` 才写，且登记正文记录这一点），或者**不登记**。
   写入器在未传 `--register-untestable` 时**拒绝写**，不会静默登记一个测不了的配置。
   **默认的 `--sd-between 0.15` 会让第 1、2 条都不成立**（第 6.2.3 节），所以这条分支不是理论上的。

**写入器不信任报告里的结论字段，它逐项校验后再重算：**

- `--power-report` 的 `source` 必须非空，且它记录的 Step 0 `registration_sha256`、`config_id` 与
  Step 0 登记的路径逐项等于写入器绑定的那份登记（`settings.agent_forward_step0`），`source.code_commit`
  也必须非空——在别的书上算出来的报告不算 D1 的裁定；
- 报告 `planning` 里的 `window_sessions`（120）、`min_scored_names`（10）、`min_measurable_sessions`（60）、
  `test_level`（0.05）、`target_power`（0.80）、`detectable_mean_threshold`（0.02）、
  `min_observed_sessions`（20）必须等于已采纳的常量；`sd_between` 与 `abstention_rate` 是**规划假设**，
  允许 L5 用 D7 干跑周实测的值覆盖，报告里记下的就是那次登记采用的值（不要求等于 0.15 / 0.10）；
- 写入器按行自己的字段（观测会话数、投影可测会话、可检出均值与门槛）**重算**三条件与判定，
  重算结果与报告里的 `verdict` 不一致即拒绝；
- 被选中的行、它的 `measurable_sessions`、`detectable_mean` 与报告 sha256 写进
  `settings.agent_forward_power`；报告缺失、不合 schema、校验失败都退出 2、什么都不写。

### 6.5 期望计数与弃权：声明的简化（F9）

- 投影用**期望计数**：`E[event_names] · min(sample, S) / S`（S 是该会话的幸存者数；普查时就是
  `E[event_names]`），不是逐会话的超几何抽样计数。期望刚好在 10 名下限之上的会话，实际上约一半的时候
  低于下限——这是**声明的简化**，不是测量值，也不保证任何一天。
- 投影在套 10 名下限之前，先按 `(1 - abstention_rate)` 收缩：D3 只数 join（agent 记录、Step 0 合成分、
  标签）之后仍记了分的名字，弃权不记分。默认的 0.10 是**规划假设**，不是测量值；D7 干跑周测真实弃权率，
  L5 用 `--abstention-rate` 覆盖它并重跑第 6.2 节的命令；本文件从不把它写成测量值。
- 若真实弃权率显著高于规划值，投影偏乐观；这个方向写在报告里，读者不要把它当成保证。

## 7. 探索性块（永远探索性）

登记的 `secondary` 恰好三个臂，把 D2 的三句话（规则家族、h=5、覆盖集）各点名一次：

| 臂 | 家族 | 集合 | 期限 | 标签 |
|---|---|---|---|---|
| 1 | `agent_rule_baseline/v1` | `primary` | 1 | 探索性 |
| 2 | `agent_llm_structured/v1` | `primary` | 5 | 探索性 |
| 3 | `agent_llm_structured/v1` | `coverage` | 1 | 探索性 |

- 三个臂合成**一个** Benjamini–Yekutieli 家族（任意相依），`q = 0.10`；只在主窗口满足**之后**才检验；
  打印为 `q-value <= q`（`below_q`）——按 q 的逐步判定，**不是结论**。
- 探索性结果**从不改变**主结论的措辞，也**从不被升级**成第二个主统计量；主臂从不出现在 `secondary` 里。
- 臂的清单由 L5 的登记给出。登记之后新增一个臂就是另一份登记（读者没有别的入口），所以事后看到结果
  再加臂在结构上不可能悄悄发生。
- h=5 的读数按**位置**取每第五个会话（`non_overlapping`，从锚点起），标签不重叠；120 个位置的窗口
  在 h=5 上给出 24 个非重叠期（这个数字是算术，不是读数）。

## 8. 失败语义（D6 已采纳；部署故障不是答案）

一个家族某天没 file，是当天的一个 `no_record` 缺口：它只减少期数 n、不移动窗口（第 5.3 节），
日志写明原因。**绝不 file 一个部分的批次**，也**绝不**把部署故障记成答案。

| 情形 | 当日发生什么 | 日志里的原因 |
|---|---|---|
| 传输失败（到不了端点） | 该家族-集合当天不 file | `no_record: transport_error` |
| 命名的凭据环境变量缺失或为空 | 该家族-集合当天不 file | `no_record: credential_missing` |
| 其他 `ModelConfigurationError`（未核实端点、坏 base URL/额外载荷、运行中凭据消失） | 该家族-集合当天不 file | `no_record: configuration_error` |
| 请求上限（≤600/晚）用尽 | 该家族-集合当天不 file | `no_record: request_cap_reached` |
| 墙钟期限（23:30 上海）已过 | 该家族-集合当天不 file | `no_record: deadline_reached` |
| 两次尝试后仍是无效回答（含空白） | **答案**：编码弃权 | 该名字 `model_invalid_output` |
| `--no-llm` | 不跑模型家族 | `no_record: llm_disabled` |
| 覆盖集为空 | 不 file 覆盖集批次（不是缺口） | `coverage_absent: "empty_set"` |

- 上限与期限只花在**会到达端点**的请求上：一次 provider 重试就是一次 HTTP 尝试、计一次；已存交换的
  重放零成本、不受期限（重跑已登记的一天不花请求）。
- 规则家族永远是确定性的，**总是 file**（除非它真的失败——那时整晚按步拒绝，写一次也没有发生）。
- 部署故障不会花掉一整晚的额度：凭据预检在发出任何请求之前，缺失或空白的凭据直接记
  `credential_missing`，不发送请求；上限按**会到达端点的 HTTP 尝试**计，被上限拦住的请求不发送也不计数。

## 9. 模型身份

- 模型是 **`deepseek-flash` 的移动别名**：同一别名今天指哪个具体权重，供应商可以变。每一次模型交换都把
  响应里的 `response.model` 与 `system_fingerprint` 记进 C 的运行目录（`model_exchanges/`，按内容寻址，
  含模型 id、提示词版本摘要、响应哈希），日志汇总每次交换的身份。**别名变化是报告里的一行变化，
  不是重新登记的理由**：已 file 的答案不被改写，评估仍然读已存记录。
- 已知局限（批次 2 收尾记录 §6 第 2 条，未修复）：模型 id 是声明的一部分、也是记录请求地址的一部分；
  别名一变，声明就变，同一天可能多出一条声明不同的记录。已登记的答案不被改写；评估器对同一会话
  同一 (family, scoring_set) 的重复记录**按名拒绝**（失败即关闭）。这不是数据错误，也不是重登记的
  充分理由；它的处理方式在 L6 之前保持为「报告如实列出模型身份变化」。
- 端点字段（`DEEPSEEK_V4_1_FLASH`）：model、base_url、`structured_output_mode`、输出上限、价格
  **仍是占位**，`verified_on = None`；`api_key_env` 是变量名，密钥从不入库，存储的交换排除请求头。
  `thinking` 关闭（`extra_payload = {"thinking": {"type": "disabled"}}`，因为 thinking 默认开启且会让
  temperature 失效）、`temperature = 0`、价格按每百万 token 输入/输出记（错峰口径按供应商文档在 L2
  核实并在 `verified_on` 里记录）。**L2（端点核实，0 请求）与 L3（≤5 次模型请求的 live 探针）都还没有做。**
- 用量账：每次真实请求进 `state.sqlite3` 的用量账（tokens 与价格），运行日志打印本次的 tokens；
  回放不花请求、也不新增用量。

## 10. 只前向：历史回放与 72 小时滞后护栏（`V2-P7-015`）

- LLM 派生信号**只允许前向使用**：模型训练时可能见过评估期之后的信息，历史回测不可信。
  护栏是两条纯函数入口（`domain/llm_contamination.py`）：
  `refuse_llm_agents_in_history`（运行模式为 `replay`/`backtest`，或运行时刻晚于 `as_of` 超过
  **72 小时**，按名拒绝并点名路径、模式、`as_of`、运行时刻与滞后）；
  `refuse_llm_prediction_in_backtest`（回测里读 `agent_*` 记录，除声明 `agent_kind="deterministic"` 外
  一律拒绝；缺失 kind 失败即关闭）。
- 模型家族在晚间命令里走 `research_engine`/`run_cycle`，所以第一个入口在**唯一的生产模型路径**上结构性
  生效；引擎在 router 之后、恢复行之前调用它，拒绝不写恢复行。
- C 的晚间命令按真实时间一晚一晚跑：它读 Step 0 当日已 file 的记录、在会话自己的 16:30 之后才写证据与
  分数；历史会话不能当晚间重放（面板的发布时刻与墙钟差 72 小时以上会被护栏拒绝）。
- 已存交换是回放的唯一来源：重跑读已存响应，不再发请求；回放不绕过 72 小时护栏，也不绕过未核实端点闸。

## 11. A 的候选政策：定义冻结（不是检验）

方案 A（智能体否决层）的两条候选政策在本协议里**冻结定义**，以便它们不能事后调参；它们**不在本协议里
被检验**，A 的 issue 只在 G-C 之后才切（决策链 §5.1）。

| 候选政策 | 定义 | 输入 | 状态 |
|---|---|---|---|
| 阻止入选（block entry） | 当登记的 LLM 主集分数对一只 Step 0 当日候选低于某阈值时，阻止它入选（只阻止入选，不卖已有持仓） | **登记时点已 file 的** LLM 主集分数（h=1 主臂的分数本身，不是本协议的检验结果） | 定义冻结；阈值**未设** |
| 强制卖出（force sell） | 对持仓名，当其分数低于某阈值时走强制退出（不受每日替换上限约束，会带来高频进出） | 同上 | 定义冻结；阈值**未设** |

- **阈值今天不设**：在没看到 C 的结果之前写阈值，就是替 A 事后选参；阈值只能在 A 自己的前向登记里定。
  冻结的是**定义与分数输入**：两政策的输入是「登记时点已 file 的 LLM 主集分数」，不能换成未来的读数、
  不能换成探索性臂、不能换成别的集合。
- 冻结是**记录**，不是测试：本协议不为这两条政策产生任何 p 值、任何组合收益，也不把「冻结完成」写成
  任何「有效」的证据。A 的评估（与 C 同窗口的配对影子账簿）在开闸之后由 A 自己的登记与 issue 承担。

## 12. 登记之前的顺序：D7 干跑周、D8 报告、L1–L6

### 12.1 D7 干跑周（登记之前，只测运维）

- 至多 **5 个已开始的晚上**；每晚至多 **600 次模型请求**（与正式晚间同一个上限与同一个墙钟期限 23:30 上海）；
  **只测运维指标**：失败率、空白率、弃权率、无效输出率、延迟、tokens。**绝不算任何 IC、任何收益、
  任何结论词。**
- 计数按**已开始的晚上**算：在读完 Step 0 当日记录、发出第一个模型请求之前就算开始；此后无论成功、失败、
  超上限、过期限还是中途崩溃都计入 5 个上限；第 6 个已开始的晚上按名拒绝。连 Step 0 当日记录都读不到
  （或已过 09:15 截止）的晚上没有花过任何请求，不算一次测量、不消耗一个 scratch 晚上。
  标记文件 `c-dry-week-scratch.json` 在**第一个模型请求之前**写/刷新，所以失败或中途崩溃的晚上也留下标记
  （它不会被误当成 C 的前向运行目录）。
- 落在一个**一次性的 scratch 运行目录**，根上有标记 `c-dry-week-scratch.json`
  （schema `openalpha-c-dry-week-scratch/v1`，`evenings` 列出已开始的会话，至多 5 个）。
  `scripts/agent_evening.py --dry-week` 以这个模式运行：它需要 scratch 的 `--runtime-dir` 与 Step 0 的
  `--step0-runtime-dir`，**不需要 C 的登记**；**不 file 任何预测批次**、**不写 C 的晚间日志与证据**。
  每晚上一份运维日志 `<scratch>/dry_week/<会话>.json`（schema `openalpha-c-dry-week-evening/v1`），
  只有运维字段（计数、失败/空白/无效分类、延迟分位、tokens、上限与期限），**没有分数、IC 或收益字段**。
- 这些记录**永远不进确认性样本**：`scripts/agent_report.py` 与 `strategy_agent_evaluation.evaluate`
  对带该标记的运行目录**按名拒绝**，不把它读成 C 的前向样本。干跑周的任何数字都不是证据，也不进本协议。

### 12.2 D8 报告（L6）

C 的报告是**单独的命令** `scripts/agent_report.py`，不是 Step 0 的 `scripts/forward_report.py` 里的一节；
**Step 0 的周报脚本从不因 C 而修改**。报告：见证/缺失记录与原因、弃权率、模型别名变化、主检验（窗口之前
只印计数与描述性均值，窗口满足之后才印那一次检验）、带标签的探索性块、样本数与可检出均值、以及五种结论的
固定措辞。它只读已存记录（零模型调用），Step 0 的运行目录只读。报告里的**弃权率与模型别名**统计只覆盖
`anchor_session <= session <= through`（与主臂同一个窗口），不读窗口之外的记录。它先拒绝带 D7 干跑标记的
运行目录（第 12.1 节）。

### 12.3 受闸步骤 L1–L6（每一步等用户明确 go）

| 步骤 | 请求 | 前置 | 产出 | 状态 |
|---|---|---|---|---|
| L1 `V2-P7-010` 探针 live | ≤ 300 次 Tushare（硬上限） | 探针工具已合入 | 只含元数据的报告；不落盘数据 | **未运行**（2026-11-12 的 20 次被拒尝试计入上限，见运行手册） |
| L2 DeepSeek 端点核实 | 0 | 用户确认事实文件 | `DEEPSEEK_V4_1_FLASH` 填值、`verified_on` 设置 | **未运行** |
| L3 DeepSeek live 探针 | ≤ 5 次模型请求 | L2；`DEEPSEEK_API_KEY` | scratch 运行目录里的模型/指纹/用量/空白/JSON 有效性报告 | **未运行** |
| L4 LLM 干跑周（D7） | ≤ 5 × 600 模型请求；0 Tushare | `006b` 已登记、Step 0 晚间在跑；批次 2–3 已合入；L3 | 只含运维指标的报告 | **未运行** |
| L5 C 登记 + 钉住 | 0 | 功效有裁定（`testable`，或用户选择登记为不可测）；本协议已提交且写入器绑定了它的 sha256；L4 通过 | `p7-c-registration.json`（**用户提交**）；`--pin-worktree`；打印 launchd；C 时钟开始 | **未运行**；本文件是它的前置之一 |
| L6 G-C 读数 | 0 | C 记录满 120 个前向会话 | `agent_report.py` 跑一次；候选 / 不可测 | **未运行**（窗口未开始） |

L1 的 300 次上限里已经花掉 20 次：2026-11-12 修复轮中发生过一次受闸之外的 `--live` 运行，用假 token 向
`api.tushare.pro` 发出 20 次被拒绝的请求尝试（无数据返回、无仓库文件）。保守记账把这 20 次计入上限，
**L1 的预算文件必须从一开始就是 `{"schema": "openalpha-p7-event-probe-budget/v1", "spent": 20}`**：
探针拒绝没有 `schema` 键的预算文件，缺文件又按 0 读，所以只有这个完整字面量才把 20 次记账带进去
（不自己创建那个 live 文件）。这条记录在运行手册与批次 2 收尾记录 §7.2 里也有。

## 13. 不是证据

- 本文件描写的是一个**候选**研究配置的预登记设计。今天**没有结果**：没有前向晚间、没有干跑周、
  没有真实读数、没有一次真实的 DeepSeek 请求（全部测试跑在脚本化传输、测试时生成的面板与已存交换上）。
- 通过的测试、能跑的命令、能算的功效，都是**工程完成**，不是研究有效性。任何「已验证」「证实有效」
  「proven」的措辞都不允许出现在 C 的任何输出里。
- §6 的「未运行」不是「不可测」，也不是「可测」；§5.6 的「不可测」不是「没有证据」；§5.5 的「未触发」
  不是证据。这三条是本协议最容易被读错的地方，写在这里给所有读者。
- 一行字的强度只到「候选」。如果 L6 的结论是 `candidate_increment`，那个词也仍然是「候选的」：
  一次单侧检验通过不等于效应存在，也不等于智能体层有可交易的价值。

## 14. 版本记录（只追加）

- 2026-11-12：本文件随方案 C 批次 3（`V2-P7-013`/`V2-P7-014`/`V2-P7-015`）写出，**早于 C 的登记**
  （登记尚未写出）、早于任何前向运行与干跑周。D1（双样本行、逐行判定、不可测时的两条出路）、
  D4（v1 只收 011 的事件）、D5（一个主检验、单侧、α=0.05；中期在第 60 个位置且保留按比例下限与 sticky）、
  D7（干跑周 ≤5 晚、≤600 次/晚、只测运维）、D8（报告是单独命令）是用户 2026-11-12「按推荐」的裁决。
  §6 的功效表**未运行**（零请求、无真实面板），以「未运行」的形式留在 §6.3；L5 运行 6.2 的命令后把打印的
  两行粘贴进来。D5 对 G-C 旧措辞的改写由**决策链 §9 追加的新行**记录（旧行不改）；§9 与路线图的那些行由
  控制者在本次文档波写，本文件只引用。登记提交、sha256、`config_id`、`--anchor` 冻结的会话、钉住的 commit
  与 C 时钟起点，在 L5 之后追加于此，正文不改。
- 2026-11-13：**修复轮 1（独立评审 9 条意见，正文更正，设计/顺序/闸门不变）。** 更正的内容：
  ①**锚点**（F3）改为「提交时刻之后严格意义上的第一个交易日」：交易日 d 的 16:30 发布时刻严格晚于提交时刻；
  周末/节假日提交 → 下一个交易日，交易日 16:30 前提交 → 当天，16:30 后 → 下一个交易日。写入器只检查工作日
  与不早于今天（上海，允许未来工作日），删去「星期一日 / 16 个日历日」的旧说法；报告在 L6 用提交时刻精确
  核对锚点，不一致即拒绝。②**功效模型**（F4）：改成两分量 sd（`min(1, sqrt(sd_between^2 + 1/(n-1)))`，
  `sd_between` 是非抽样部分的规划假设 0.15），判定改为「观测历史会话 ≥ 20 且投影可测会话 ≥ 60 且可检出
  均值 ≤ 0.02」，每行打印 sd、可检出均值与 0.02 阈值，not_testable 有三个具名原因；0.02 是旧 G-C 的 h1
  门槛，只作规划的检出阈值，**不**成为已登记检验的量级门槛；并写明默认 `--sd-between 0.15` 下两行都会
  是 `not_testable`（`detectable_above_threshold`）这一如实的设计结果。③**期望计数与弃权**（F9）：投影用
  期望记分数并在 10 名下限之前按规划的弃权率（默认 0.10，`--abstention-rate`）收缩，两者都是声明的简化/
  规划假设，不是测量值。④**写入器校验**（F5）：`--power-report` 的 `source` 必须绑定同一份 Step 0 登记，
  `planning` 的窗口/下限/水平/功效/阈值必须等于已采纳常量，写入器按行字段重算判定。⑤**协议文件绑定**
  （F7）：新增 `settings.agent_forward_protocol_document`（`agent-forward-protocol-document/v1`：
  `docs/research/p7-c-protocol.md` 的路径与已提交字节 sha256），写入器拒绝未提交或有改动的协议文件。
  ⑥**L1 预算字面量**（F6）：写成探针接受的完整 `{"schema": "openalpha-p7-event-probe-budget/v1", "spent": 20}`。
  ⑦**干跑周计数**（F1/F2）：干跑周按**已开始的晚上**计数（失败也算），标记在第一个请求之前写。⑧**运行期
  隔离**（F2）：正式晚间在第 1 步与 `--pin-worktree` 也拒绝带干跑标记的运行目录（写在运行手册）。
  ⑨文首的修改规则改为：**预登记之前可以按控制者裁决更正正文，每次更正记入本节；看到结果之后只追加本节、
  不改正文**。设计、顺序与 §5.2 的闸门不变；C 的登记仍未写出，前向窗口未开始，功效表仍是未运行。
