# 方案 C 开发批次 2 收尾记录（2026-11-12）

> **状态：记录，不是验收。** 本文记下本批次交付了什么、没交付什么、哪些裁决是新做的、哪些偏离是计划没写的、哪些事留到以后、哪些事要用户来定。它只写工程状态；**工程完成不是研究证据**（`AGENTS.md`「研究诚实」），措辞仍是“候选”，没有任何一行把“通过的测试”写成别的东西。本文也不写任何闸门的通过判据或任何测试通过数以外的结论。

## 0. 先读这几句

- 本批次做的是方案 C 的第 2 批四个任务：`V2-P7-010` 的探针工具、`V2-P7-012` 的后续（thinking 开关 / json_object 提示词 / 空白内容）、`V2-P7-013` 的智能体家族（纯领域规则、前向登记胶水、晚间命令）、`V2-P7-014` 的增量 IC 评估器（纯算术、读取已存记录的评估器）。实施计划是任务书，**不在版本库内**：`.superpowers/sdd/plan-c/plan.md`（`.superpowers/` 不入库），事实文件 `.superpowers/sdd/plan-c/deepseek-endpoint-facts.md`；本文是它的版本化记录。
- 代码在分支 `feat/v2-p7-c-batch-2` 上（从 `main` 的 `d2b74f7` 分出，`d2b74f7` 已并入 `V2-P7-026`）。**7 个代码提交 + 3 个修复/守卫提交 + 1 个文档提交 + 本记录的更正提交 + 修复轮 1 的 4 个代码提交与 1 个文档提交**（修复轮见 §7）：`6b6f047`（2.1）、`75dff34`（2.2）、`49737d3`（2.3）、`c425bb0`（2.4）、`708f0c8`（2.5）、`5703f78`（2.6）、`b8702d3`（2.7）、`f4e7380`（修复：晚间命令拒绝与 Step 0 种子耦合的盐）、`5271beb`（修复：主 horizon 钉成 h=1）、`a7b6238`（守卫：用量站点普查记录受闸的晚间模型家族），然后是 `ba758e2` 文档提交与本记录的更正提交。截面：`git diff --stat d2b74f7..HEAD`（以 tip 为准）；到 `b8702d3` 为止 25 个文件、+7,134 / −22。
- **未推送、未合并。** 交付的代码**没有跑过任何真实面板、真实因子、真实读数或冒烟，也没有发过一次 DeepSeek（或任何 LLM）请求**：所有测试跑在测试时生成的面板、构造的已存记录、脚本化传输与已存模型交换上；socket 守卫在 pytest 内全程开着。计划里的两个联网步骤（L1 的 010 探针 ≤300 次请求、L3 的模型探针 ≤5 次请求）**都没有做**，各等用户明确 go。**但“一次 Tushare 请求也没有发过”这句话在本轮之后不再为真**：修复轮中探针实现者越闸运行了一次真实的 `--live`（假 token、20 次被拒绝的 HTTP 尝试，详见 §7.2），所以本记录、`OA-OPS-052` 台账行与探针模板都按“受闸的 L1 未运行 + 一次越闸的 20 次凭据尝试”改写。
- `DEEPSEEK_V4_1_FLASH` 仍是**占位**：model、base_url、structured_output_mode 与价格一个没填，`verified_on=None`（`L2` 是受闸步骤），`api_key_env="DEEPSEEK_API_KEY"` 是变量名。本批次只加了候选值的载体（`VendorEndpoint.extra_payload`）与提示词 V2，没有把占位写成已核实。
- **没有任何 P7 登记存在**，`docs/research/p7-registration.json` 没有写出，也没有任何绑定被作废；`docs/research/p7-registration.json`、`docs/research/p7-protocol.md`、`scripts/daily_selection.py`、`scripts/forward_report.py`、`scripts/p7_register.py` 与任何 `strategy_survivor_*` 模块的字节都没有被本批次改动（晚间命令只 import `daily_selection` 的辅助函数）。
- 全量 `tests/unit` **没有在本批次内跑过**（由控制者在最终 tip 上串行跑一次，结果以控制者的报告为准）；实现者只跑定向文件、临近回归与本文 §4 列出的闸门。
- 本批次的文档波改：本文、路线图三行、规格 §9 一行（外加“没有漂移”一行）、能力台账（`features.csv`、`summary.json`、`docs/release/openalpha-v1-feature-ledger.md`）与 `tests/unit/test_feature_ledger_test_tree.py` 的计数表。`AGENTS.md` 的“Current work pointer”与 `docs/HANDOFF_CURRENT.md` 不在本批次计划的任务里，留给控制者的文档波（批次 1 的同一先例），见 §6。

## 1. 交付了什么（按任务）

### 任务 2.1 —— `V2-P7-012` 后续：thinking 开关、json_object 提示词、空白内容（提交 `6b6f047`）

- `models/deepseek.py`：`VendorEndpoint` 加 `extra_payload: Mapping[str, JsonValue] = field(default_factory=dict)`。默认空，所以没有设置它的端点，记录下的请求字节与改动前完全相同；`DEEPSEEK_V4_1_FLASH` 的每个值、以及 `verified_on=None`，都没有动（只在占位注释里列出 L2 要填的字段）。
- `models/openai_compatible.py`：`OpenAICompatibleProvider(..., extra_payload=None)`，**最后合并**进请求载荷；与 `model`/`messages`/`response_format` 冲突的键在构造时按名拒绝（`ModelConfigurationError`，与 base_url/timeout 的拒绝同一类）。空白/纯空白 `content` 现在在 `json.loads` 之前按“空白答案”拒绝（`ModelResponseError`），而不是被当成解析错误；重试路径不变。
- `agents/prompts.py`：`STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT`（name `structured-signal/v2-json-object`）：system 含小写 `json` 与恰好一个示例 JSON 对象，user_format 仍恰好一个 `{payload_json}`。`STRUCTURED_SIGNAL_PROMPT_V1` 的字节与摘要都不变（测试钉住摘要 `9b70f975…cf2b`）。**修复轮 1（F13）之后**：artifact 名升为 `structured-signal/v2-json-object-examples`，system 带**两个**示例（一个 scored、一个 abstain，占位值中立），避免 temperature 0 的模型锚定在唯一的弃权示例上；见 §7。
- `agents/model.py`：`StructuredSignalAgent(..., include_facts: bool = False)`；为真时把证据载荷里的 `facts` 解冻后放进 user 载荷（默认路径逐字节不变）。
- `runtime/composition.py`：只在 `llm_provider` 里把 `endpoint.extra_payload` 透传给 provider（4 行）。
- 测试：`test_default_payload_is_byte_identical_without_extra_payload`、`test_extra_payload_thinking_disabled_reaches_the_request_body`、`test_extra_payload_cannot_override_model_or_messages`、`test_blank_content_is_an_invalid_answer_not_an_empty_object`、`test_llm_provider_passes_the_endpoint_extra_payload`、`test_v2_prompt_mentions_json_and_carries_an_example`、`test_v1_prompt_bytes_unchanged`、`test_include_facts_adds_the_evidence_facts_to_the_user_payload`、`test_blank_content_is_retried_up_to_max_attempts_and_never_accepted`（最后一条是特征化测试：该路径本来就没有洞，只把“两次尝试后失败”的文本钉住，供 2.5/D6 映射到 `model_invalid_output`）。

### 任务 2.2 —— `V2-P7-010` 探针工具（提交 `75dff34`）

- 新 `scripts/p7_event_probe.py`：`PROBE_CAP = 300`；`ProbeCall`/`ProbeFinding`（frozen）；`probe_plan(*, today, depth_years)` 覆盖 `moneyflow`/`limit_list_d`/`top_list`/`top_inst`/`anns_d` × 模式/整日/深度/时点字段，且从不超上限；`run_probe(plan, *, transport, cap=PROBE_CAP)` 复用 `scripts/p7_smoke_requests.CappedCounter`（先计数再转发，会越过上限的请求既不构造也不发送）；`render_report` 只输出元数据（每次探测的接口名、是否允许、错误码、行数/页数、字段名、最早日期、时点字段名），绝不含行值、消息或令牌；`main` 默认 `--dry-run`（打印计划、零请求、零写入），`--live` 需 `--i-have-the-users-go` 且 `TUSHARE_TOKEN` 只在发送时从环境读取。权限错误/传输失败变成 finding，不是崩溃。**live 没有被运行。**
- 新 `docs/research/p7-event-probe-report.template.md`：L1 受闸 live 的报告模板，只写元数据、候选措辞、每列的读法；明确“行数为 0 不等于接口不存在”。
- 测试 10 条（`tests/unit/scripts/test_p7_event_probe.py`），全部脚本化传输。

### 任务 2.3 —— `V2-P7-013` 纯领域（提交 `49737d3`）

- 新 `src/openalpha_cn/domain/agent_forward.py`：`AGENT_LLM_FAMILY="agent_llm_structured/v1"`、`AGENT_RULE_FAMILY="agent_rule_baseline/v1"`、`AGENT_HORIZON="5d"`、三个封闭弃权码（`agent_not_routed`/`agent_abstained`/`model_invalid_output`）、`ScoringSet = Literal["primary","coverage"]`。
- `primary_sample(survivors, *, salt, session, size)`：`sha256(f"{salt}|{session.isoformat()}|{ts_code}")` 升序取前 `size`，输出按 `ts_code` 排序；容量 ≥ 集合大小即普查（同一条排序截断）；输入去重；空白盐、`size<1` 按名拒绝。函数不接收持仓，所以主集看不到账簿。
- `coverage_set(holdings, watch_list, survivors)`：只保留幸存者、排序去重；函数不接收盐，所以覆盖集拉不进主集。
- `score_of(direction, strength, abstention_reason)`：bullish `+strength`、bearish `-strength`、neutral `0.0`，`-0.0` 归一为 `0.0`；abstain 返回 `(None, code)`，未知方向/非有限强度/方向帧带弃权理由/弃权帧非零强度/词汇外的 reason 都按名拒绝。
- `refuse_a_salt_tied_to_the_book(salt, *, book_seed)`：规范字母数字投影相等、或盐的任一数字串包含种子数字时拒绝；故意过包含（保守方向），模块文档写明短种子会过拒。
- 测试 9 条，含 grimp 的 `test_agent_forward_imports_nothing_numeric`（AST 白名单 + 图检查：无数值栈、无兄弟子包）。

### 任务 2.4 —— `V2-P7-013` 前向登记胶水（提交 `c425bb0`）

- 新 `src/openalpha_cn/strategy_agent_forward.py`：`AgentScore`（frozen，恰好一个答案、有限分数、封闭弃权码）；`agent_declaration(*, family, scoring_set, agent_kind, registered, model_ref, prompt_ref)` → `AlphaModelDeclaration`，`name=f"c-{scoring_set}"`、horizon `5d`、`feature_version`/`seed`/`code_commit` 取自登记，hyperparameters 带 `agent_kind`（回测护栏读的那个键）、`registration_sha256`、`scoring_set`、LLM 家族的 `model`/`prompt`；LLM 与规则家族互相拒绝对方的形状（半声明的 LLM 记录不可审计，规则记录声称模型调用是假的）。
- `agent_batch(*, session, scores, declaration, predicted_at)`：`as_of = session_publication_instant(session)`（16:30 上海），artifact 的 `training_cutoff` 等于它（非拟合记录的先例：等值被泄漏下限允许），`feature_ids=("agent_evidence/v1",)`（`AlphaModelArtifact` 拒绝空列表；这是声明证据平面，不是声称读了某个面板因子），`training_example_count=len(rows)`。
- `agent_run_id(*, config_id, session, family, ts_code)`：确定性，不含 scoring_set，所以主集与覆盖集重叠的名字共用一个 run id。
- `file_agent_batch(store, batch, *, calendar, clock)`：调 `strategy_forward_session.file_forward_record`，返回 `prediction_block`，provenance 取声明里的 `registration_sha256`（缺失则按名拒绝，不写空 provenance）。
- `pyproject.toml`：`strategy_agent_forward` 进 `forward-registration-reaches-no-face` 的 `source_modules`、进 `agent-layer-reaches-no-paper-book` 的 `source_modules`、进 `backtest-no-numeric-stack-or-panel-plane` 的禁止表；`tests/unit/test_forward_and_agent_layering.py` 的目录相等辅助函数加入该模块；`tests/unit/test_panel_ingest_import_isolation.py` 补 seam 与数据集两行。
- 测试 10 条（`tests/unit/test_strategy_agent_forward.py`）：`agent_kind` 被真实护栏读、主集/覆盖集两条记录、重跑 `unchanged` 与不同答案拒绝、09:15 之后拒绝零写入、16:30 之前预测拒绝、provenance 是登记摘要、run id 确定性、恰好一个答案、grimp 层测试。

### 任务 2.5 —— `V2-P7-013` 晚间命令（提交 `708f0c8`）

- 新 `scripts/agent_evening.py`（1,417 行，六步、`AGENT_STEPS`、写一次的日志、`--print-launchd`、`--pin-worktree`）：
  1. 登记：读 C 自己的登记（`kind="agent_forward/v1"`，`config` 里 `salt: str` 与 `primary_size: int` 无默认），经 `registry.admit_registered_code(also_bound=C_SCRIPTS)`；`C_SCRIPTS=("scripts/agent_evening.py",)`。
  2. Step 0：`--step0-runtime-dir` 只读；`prediction_store_at` 读记录，要求恰好 **1 条** 受绑定、按时（`registered_at < 下一次开盘 09:15`）的 `survivor_book_composite/v1` 记录，否则 `Step 0 has not filed {session}` 且什么都不写；存活者经 `record_scope`，持仓与观察名单从 Step 0 当日日志的 `result` 经 `plan_names` 读出；Step 0 的登记用 `registry.committed_registration` 读（它绑的是 Step 0 的代码，不是本进程的）。
  3. 证据：`limit_events_on`（as_of = 会话 16:30）→ `limit_event_snapshots` → `refuse_a_restated_session` → C 自己的 `evidence_store_at` append（011 的幂等）。
  4. 规则家族：`research_engine(agents=baseline_agents())` 在 C 的 runtime 上，主集 ∪ 覆盖集逐名 `run_cycle`（`agent_run_id`），`score_of`，两条批次分别 file。`--no-llm` 时规则家族仍确定性、两次运行逐字节相同。
  5. LLM 家族：`llm_provider(DEEPSEEK_V4_1_FLASH, runtime_dir=C, replay=False)`，V2 提示词、`max_attempts=2`、`evidence_families={"market_event"}`、`--max-model-requests 600`、`--deadline 23:30` 上海；传输错误/上限/期限 → 该家族不 file 并且日志写 `no_record: <reason>`（D6）；`ModelProviderFailure` → `model_invalid_output` 弃权；每次交换的 `response.model`、`system_fingerprint` 入日志。未核实端点按名拒绝（`llm_provider` 的闸），规则家族已 file、日志不写，修好部署后重跑补完。
  6. 摘要：日志（`tmp + os.replace`，写一次；重跑重算出相同字节，不同的日志按名拒绝）+ 打印行（候选、“not traded”、scored/abstained/failed、tokens、requests）。
  - Review Focus 2 的耦合在运行侧也拒：读完两份登记后、读或写当天之前，`refuse_a_salt_tied_to_the_book(C 的盐, book_seed=Step 0 的种子)` 失败即拒（`f4e7380`），与批次 3 的登记写入器同一规则。
  - `--pin-worktree DIR`：`registry.registered_checkout(also_bound=C_SCRIPTS)` + `daily_selection.link_env_file`/`sync_pinned_environment`；脏/外来/移动 worktree 按名拒绝。
  - `--print-launchd`：label `com.openalpha.agent-evening`，工作日 20:30，**只打印、不安装**。
- `runtime/composition.py`：只在文件末尾增 `AGENT_JOURNAL_DIRECTORY` 与 `agent_journal_dir_at(runtime_dir)`（`*_at` opener，没有 `StorageContainer` 字段、没有 `build_storage` 接线）。
- 测试 14 条（12 条计划点名 + deadline + 未核实端点）。

### 任务 2.6 —— `V2-P7-014` 纯算术（提交 `5703f78`）

- 新 `src/openalpha_cn/domain/incremental_ic.py`：`partial_rank_ic(agent, factor, label)` = 平均并列 Spearman 秩 → rank(agent) 与 rank(label) 对 rank(factor) 的带截距 OLS 残差 → Pearson；样本 <4、长度不符、非有限输入、factor 秩无变化、残差零方差 → `None`（伴生 `partial_rank_ic_with_reason` 给具名 reason；计划点名的公开签名 `float | None` 不变）。`incremental_ic` = 秩尺度上 IC(agent+factor) − IC(factor)，**探索性**、无 p 值无门槛。`SessionIC`、`non_overlapping(sessions, *, horizon, anchor_ordinal)`（从锚点位置起每 h 个，输入顺序无关）。`detectable_mean(*, sd, periods, level, power)` = `(z_{1-level}+z_power)·sd/√periods`，与 `backtest.power_analysis.smallest_mean_at_power(kind="normal")` 由测试钉到 1e-12 相等（domain 不能 import backtest）。纯标准库（`statistics.NormalDist`），无兄弟子包。
- 测试 9 条，含植入增量被还原、agent=factor 的退化情形（返回 `None`+`zero_residual_variance`，而不是编造 0.0）、h=5 按位置取第五个、幂公式交叉核对、数字化导入测试。

### 任务 2.7 —— `V2-P7-014` 评估器（提交 `b8702d3`）

- 新 `src/openalpha_cn/strategy_agent_evaluation.py`：`AGENT_FORWARD_PROTOCOL_KEY="agent_forward_protocol"`、`AGENT_FORWARD_PROTOCOL_SCHEMA="agent-forward-protocol/v1"`；`AgentProtocol`（计划点名的 12 个字段，`__post_init__` 钉 D2：主家族必须是 `agent_llm_structured/v1`、主集必须是 `primary`）；`ExploratoryArm`；`read_agent_protocol(settings)` 读无默认值、键缺失/多余/越界都按名拒绝；`evaluate(*, protocol, agent_store, step0_store, labels, book, through, as_of)`（存储与标签都是结构 Protocol，`FilePredictionStore` 与 `model_view.OutcomeLabels` 结构满足）。每次会话：域 = Step 0 存储的 `record_scope` 幸存者 ∩ 该臂记分名；factor = Step 0 存储的合成分；标签只在自身退出会话 16:30 之后经 `labels.label` 读，未到期只计数不打标签。主臂 = 登记的 LLM/主集/h=1；其余登记臂合成 **一个** Benjamini-Yekutieli 家族（`q` 来自协议、`dependence="arbitrary"`）并永远标探索性；主集与覆盖集永不混池。窗口满足且每个位置都到期才做一次 `outcome_statistics.sign_flip_test`；单侧读数按 `strategy_survivor_forward` 的先例从双侧导出。五种结论：`not_yet`（窗口未满或位置未到期，只印计数与描述性均值、无 p 值）、`futility_stop`（中期位置累计均值 ≤ 0）、`not_measurable`（完整窗口内可测会话 < `min_measurable_sessions`，与“没有证据”分开的措辞）、`candidate_increment`（均值 > 0 且单侧读数 ≤ level）、`no_increment_detected`。样本数与可检出均值总是存在（`DetectableIC`，说明是设计性质、不是观测功效）。不 import `agents`、不 import `models`（grimp 测试钉住）。
- `pyproject.toml`：`strategy_agent_evaluation` 进 `forward-registration-reaches-no-face` 的 `source_modules`、进 `backtest-no-numeric-stack-or-panel-plane` 的禁止表；**不进** `agent-layer-reaches-no-paper-book`（见 §3 的更正）；`tests/unit/test_forward_and_agent_layering.py` 的目录相等辅助函数加入该模块；`tests/unit/test_panel_ingest_import_isolation.py` 补两行（seam 四个名字、数据集空行）。
- 测试 27 条（`tests/integration/test_agent_incremental_ic.py`）：8 条计划点名的行为 + 协议读取（13 个缺键参数化）、D2 主臂、futility、真实面板 + `model_view.OutcomeLabels` 接线、grimp 层测试；`5271beb` 之后还有 `test_the_primary_horizon_is_pinned_to_one_session`（见 §3 的评审发现）。

## 2. TDD 与并行实现

- 四个实现轨道并行：`endpoint`（2.1）、`probe`（2.2）、`evening`（2.5）、`ic`（2.6→2.7），Lead 自己写 2.3、2.4 并做全部集成编辑与提交。每个轨道都按计划的 Task 顺序一个一个做，每个行为**先写失败的测试、跑出正确的失败原因、再最小实现、再跑**；“第一次失败输出”记录在 `.superpowers/sdd/task-report.md`（不版本化）与各轨道的报告里。
- 提交顺序按计划的任务顺序：2.1 → 2.2 → 2.3 → 2.4 → 2.5 → 2.6 → 2.7 → 2.8。并行实现没有改变提交顺序（先完成的 2.6 等 2.5 落库后才提交）。
- Lead 的名字级/契约级集成编辑（不在任何实现者的写范围）：`pyproject.toml` 的三个契约、`tests/unit/test_forward_and_agent_layering.py` 的目录相等辅助函数、`tests/unit/test_panel_ingest_import_isolation.py` 的四行。每次编辑后重跑这两个守卫；2.7 的集成编辑由 ic 报告后 Lead 执行。
- 提交信息按 `type(V2-P7-0xx): …` 并带 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

## 3. 裁决、偏离与计划未覆盖处的选择

- **采用的计划裁决**：D2（唯一主统计量家族 `agent_llm_structured/v1`、h=1 偏 IC、主集；规则家族、h=5 与覆盖集永远探索性）、D3（弃权不进 IC；`min_scored`、`min_measurable_sessions`；「不可测」是正式结论）、D6（传输错误/上限/期限 → 该家族当日不 file 并写 `no_record: <reason>`；无效答案 → `model_invalid_output` 弃权；规则家族总是 file）。D1/D4/D5/D7/D8 按计划留给用户与批次 3/联网步骤。
- **计划的一处更正（本批次唯一的架构级偏离）**：计划 Global Constraints 写 `strategy_agent_forward.py` 与 `strategy_agent_evaluation.py` 同时加入两个 import-linter 契约。实测（grimp）`strategy_agent_evaluation` 经 `strategy_survivor_forward.record_scope` 与 `SurvivorBookConfig` **传递可达** `domain.survivor_book`，而 `agent-layer-reaches-no-paper-book` 的禁止表点名 `openalpha_cn.domain.survivor_book`，所以「评估器也进 agent 契约」在全传递检查下不可能成立。**选择**：评估器只进 `forward-registration-reaches-no-face`（它确实不 reach face/engine/composition），并进 `backtest` 契约的禁止表（既是 `strategy_*` 目录相等的要求，也让回测见不到它）；Task 2.7 的真实规则（不 import `agents`/`models`）由评估器自己的 grimp 测试钉住。§9 已记。
- **任务级偏离（每个都写进了模块/脚本 docstring）**：
  1. 2.1：空白内容在改动前已由 `json.loads` 抛 `ModelResponseError`，所以“会抛”的测试不可能先红；改为钉住更强的分类事实（失败原因是「空白」而不是「不是合法 JSON」），再最小实现；agent 重试测试是特征化测试（路径无洞）。
  2. 2.1：`include_facts` 只加证据载荷的 `facts`（不加 quality_flags 或整份 payload），因为计划 Finding 2 说的是“事实/证据”，且没有 facts 的证据渲染保持不变；`EvidenceSnapshot.payload` 是冻结映射，必须解冻后才能 `json.dumps`。
  3. 2.2：`run_probe` 直接用 `p7_smoke_requests.CappedCounter`，不用 `capped_requests` 上下文管理器（后者会改 `cli._panel_transport`，把 transport 从调用者手里拿走）；上限语义相同。
  4. 2.2：`pages` 是这次 finding 消耗的页数（数据页 1、拒绝/失败 0）；一次 `ProbeCall` 就是一次请求，探针**不分页**，所以“单日全市场请求数”目前只能回答“一次请求覆盖一天，直到端点行数上限”；真正的分页计数列为以后的工作，模板里写明。
  5. 2.2：无网络情况下日期只能按工作日启发式（`today` 之前的最近工作日等），节假日表现为 rows=0、模板写明「0 行不等于接口不存在」；参数集是文档给出的、在 L1 之前故意未核实；额外 CLI 参数 `--today`、`--depth-years`（57 个深度年 = 恰好 300 次，58 拒绝）。
  6. 2.3：`score_of` 的 abstention reason 只接受 C 的封闭词汇（自由文本会进到评估器要解读的已存记录）；`refuse_a_salt_tied_to_the_book` 的过包含规则（短种子会过拒）是保守方向；`primary_sample` 拒绝空白盐与 `size<1`。
  7. 2.4：`AGENT_FEATURE_IDS=("agent_evidence/v1",)` 是一个合成声明列，因为 `AlphaModelArtifact` 拒绝空 `feature_ids`，而智能体的输入是证据族而不是因子列；`agent_declaration` 拒绝半声明的 LLM 家族与带模型/提示词的规则家族；`file_agent_batch` 要求声明里带 `registration_sha256`，否则按名拒绝而不是写空 provenance。
  8. 2.5：C 的抽样参数从 `config` 读（`kind="agent_forward/v1"`、`salt: str`、`primary_size: int`，无默认）；批次 3 的登记写入器必须写这三个键（`c_registration_body`）。
  9. 2.5：增加了 `--step0-registration`（默认 `docs/research/p7-registration.json`）：计划只给 `--step0-runtime-dir`，但 Step 2 需要 Step 0 的书才能 `record_scope`/判断记录受绑定；用 `registry.committed_registration` 读（Step 0 的登记绑 Step 0 的代码，不是本进程的）。
  10. 2.5：增加 `--as-of` 钉住的时钟，并沿用 `daily_selection` 的两钟分工：`--as-of` 钉住当日的读，墙钟决定迟到、模型期限与 `predicted_at`。
  11. 2.5：请求上限由 provider 包装器 `_BudgetedProvider` 执行（`CappedCounter` 先计数再转发），不是包 inner transport——后者会让 `llm_provider` 把包装器看作 live transport，绕过未核实端点闸。后果：上限约束 provider 调用；provider 在一次调用内重试的可重试传输错误不会被数两次（写进 docstring）。
  12. 2.5：`--pin-worktree` 本地重实现了 `daily_selection.pin_worktree` 的 add/move/refuse 逻辑，因为那个 helper 硬绑 `THIS_SCRIPT`（`daily_selection.py`）而不是 `C_SCRIPTS`；仍用 `registered_checkout(also_bound=C_SCRIPTS)` 与 `link_env_file`/`sync_pinned_environment`。
  13. 2.5：日志的写一次语义排除随运行变化的事实（prediction block 的 `outcome`/`recorded_at`、不带墙钟），所以重跑重算出相同字节；模型交换身份与 tokens 从已存交换读回，回放日志同一列表；已存日志说 `llm_family.no_record` 时重跑重放该决定（不花请求）。
  14. 2.5：覆盖集为空时 `agent_batch` 拒绝空批次，所以不 file 覆盖批次，日志记 `coverage_absent: "empty_set"`（不是 no_record 缺口）；规则家族其余情况总是 file。
  15. 2.5：引擎的 `config_digest` = C 登记 config 的 canonical JSON sha256（manifest 要求一个；C 的登记不带网格摘要）。
  16. 2.5：Step 0 只读测试排除 `panel/` 的 DuckDB catalog 锁/侧写文件（与幸存者周报的前例同一让步），Step 0 的数据文件（预测、日志）一个都不写。
  17. 2.6：`partial_rank_ic_with_reason` 是追加的伴生函数（计划的签名不变），因为 `SessionIC.reason` 需要理由；`MIN_NAMES=4` 公开；`periods` 要求 int ≥ 2（backtest 的 `power_analysis` 接受 float）；精确复制因子（agent=factor）时残差零方差 → `None`+具名 reason，而不是编造 0.0（计划测试名说“等于因子则偏 IC 为 0”，数学上无定义，测试按“无定义”钉住并另测近复制约为 0）。
  18. 2.6：`non_overlapping` 的“每 h 个”按**位置**（从锚点起）而不是日历日，跨节假日不会让标签重叠；输入顺序无关。
  19. 2.7：窗口没有锚字段（计划签名没有）→ 规则是“从 agent store 第一个已 file 的智能体会话起，Step 0 已 file 的前 `window_sessions` 个会话，截至 `through`”；缺 LLM 的日子因此只减 n、不移窗口。
  20. 2.7：`sign_flip_test` 是双侧，单侧读数按幸存者周报先例从双侧导出（D5 要单侧；若将来 §9 改成真正的单侧符号翻转，那是新增一个检验函数，不是重新读数）。
  21. 2.7：`power` 的 sd 用窗口内测得的离散度（只描述、不进 p 值），目标功效是常量 0.8；将来若登记规划 sd/功效块，只改 `_power_of`。
  22. 2.7：Step 0 只用声明家族 `survivor_book_composite/v1` 识别（不做登记绑定）；同一会话两条 Step 0 记录按名拒绝；智能体记录按家族 ∈ 两个 C 家族且带 `scoring_set` 过滤，同 (family, set, session) 重复按名拒绝。续任链（`V2-P7-023`）的登记绑定规则是以后的事。
- **修复轮 1 对上面 19–22 条的更正**：上面 19–22 条描述的是交付时的设计；修复轮按评审 F4/F5 改了它们——窗口锚点改为登记参数 `anchor_session`（不再是“从 agent store 第一个已 file 的会话”）；中期无效性加了“全部中期位置到期 + 按比例可测下限 + sticky”，单侧读数与中期无效性形式都标为 **PROVISIONAL（待用户裁决 D5）**；Step 0 记录加了绑定/按时检查，智能体记录加了 C 的登记摘要检查。当前行为以 §7 与代码为准。
- **Review Focus 2 的运行侧加固**：任务 2.3 的 `refuse_a_salt_tied_to_the_book` 由任务 2.5 的晚间命令在读完两份登记后、读或写当天之前调用（`f4e7380`），所以即使登记写入器（批次 3）出了问题，运行时也不会用与 Step 0 种子耦合的盐抽样；测试 `test_a_salt_tied_to_step0s_registered_seed_is_refused_and_writes_nothing` 钉住「step 1 拒绝、零写入」。计划 2.5 没有写这一步，它按 Review Focus 2 的句子补上，属于更保守的一侧。
- **主 horizon 钉成 h=1（`5271beb`，评审发现）**：D2 把唯一主统计量登记为 LLM 家族在主集上的 h=1 偏秩 IC，但 `read_agent_protocol` 与公开的 `AgentProtocol` 只钉了家族与集合、没钉 horizon，于是 `primary_horizon: 5` 会被接受——那会把重叠的 h=5 读数变成主统计量、把 h=1 读数漏掉，等于从后门多一个统计量。现在两处都拒绝 1 以外的 `primary_horizon` 并说明理由；h=5 仍可作 `secondary` 里的探索性臂（测试钉住）。这是只读验收子代理对照 D2/Review Focus 5 找到的，交付时它不在计划点名的测试清单里。
- **用量站点普查的守卫扩展（`a7b6238`，评审发现）**：`scripts/agent_evening.py` 是第一条「能调用模型」的出厂命令，四份面向用户文档的「出厂路径不调用模型」两句守卫的前提不能再是一个空的静态扫描。今天该陈述仍然成立，理由更窄：智能体只在 `_llm_family` 里、在 `runtime/composition.llm_provider` 之后构造，而 `DEEPSEEK_V4_1_FLASH.verified_on is None` 时该装配按名拒绝真实调用。`USAGE_SITES` 现在记录晚间命令的 5 个受闸站点与批次 1 修复轮留下的 2 个 `agents/model.py` 站点（它们只是命名可调用对象、不调用），各自带次数与理由；新增的 `test_the_agent_evening_model_family_is_gated_on_the_unverified_endpoint` 钉住闸门：L2 填 `verified_on` 的那天它会失败，并要求改写四份文档的模型/用量措辞、退役或反转这两个守卫。没有改任何文档措辞，也没有把「其实会调用」写成「不调用」——闸门是被断言而不是被假定。
- **本批次没有覆盖、按保守一侧选择并记录的**：`AGENTS.md` 的 “Current work pointer” 与 `docs/HANDOFF_CURRENT.md` 没有改（批次 1 同一先例）；`CHANGELOG.md` 没有改（发布波维护）；四份面向用户文档的模型/用量措辞没有改（见 §6 开放项 1）。

## 4. 顺序与闸门不变

- 本批次**不改** `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §5 的顺序，也**不改** §5.2 的任何闸门：`006a` → `006b`、G-0、真实数据冒烟、真钱闸门与 2027-01-04 之前的硬闸门都不动；issue 总数 254（`V2-P7-026` 在 base 上）。§9 加了两行（本批次交付了什么、以及“没有漂移”）。
- 修复轮 1 之后，本批次内的定向门槛重跑过（见 §7.4）：touched 测试文件 + 五个点名文件 + 源计数/引用/表面三件套共 254 passed；`ruff check`/`ruff format --check` 干净（701 文件）、`mypy src scripts`（213 文件）、`lint-imports`（10/0）、`build_feature_coverage.py --check` exit 0。
- 本批次内跑过的门槛（定向，不是全量）：本批次新增/改动的测试文件全部；Lead 最终闸门跑 `tests/unit/test_panel_ingest_import_isolation.py`、`tests/unit/test_source_counts_match_the_tree.py`、`tests/unit/test_source_cited_tests.py`、`tests/unit/test_feature_ledger_test_tree.py`、`tests/unit/test_surface_parity.py`；`PYTHONPATH=src lint-imports`（10 kept / 0 broken）；`ruff check` / `ruff format --check`；`mypy --cache-dir=/dev/null src scripts`；`python scripts/build_feature_coverage.py --check`。全量 `tests/unit` 由控制者在最终 tip 上串行跑一次，结果以控制者的报告为准。

## 5. 未做与前置条件

- **没有真实数据、没有真实的联网研究步骤**：没有读真实面板、没有跑真实构建/因子/读数/冒烟、没有 DeepSeek（或任何 LLM）调用；受闸的 L1 没有运行。**但修复轮中发生过一次越闸的真实 `--live` 调用**（假 token、向 `api.tushare.pro` 发出 20 次被拒绝的 HTTP 尝试，无数据返回、无仓库文件，见 §7.2/§9），所以本文件不再写“一次 Tushare 请求也没有发过”；保守记账把 20 次计入 300 次上限，L1 的预算文件须从 `spent: 20` 开始。
- **L1（010 live 探针，≤300 次 Tushare 请求）**：工具已交付，**没有跑**；它等任务 2.2 合入与用户明确 go。
- **L2（DeepSeek 端点核实，0 请求）**：`DEEPSEEK_V4_1_FLASH` 的 model/base_url/structured_output_mode/价格仍空、`verified_on=None`；本批次只交付了候选值的载体（`extra_payload`）与提示词 V2；填值与 `verified_on` 是受闸步骤，等用户确认事实文件。
- **L3（≤5 次模型请求的 live 探针）**：没有跑，等 L2 与 `DEEPSEEK_API_KEY`。
- **C 的协议与登记（批次 3 的 3.1–3.6）**：本批次没有写 `docs/research/p7-c-protocol.md`、没有写 `p7-c-registration.json`、没有做事件级功效计算；`D1`/`D4`/`D5`/`D8` 仍是用户的。
- 真实答案的合成分、智能体分数的真实增量、真实前向窗口（120 个会话）都还没有发生；`014` 的评估器没有对真实记录运行过，`013` 的晚间命令没有对真实 Step 0 运行过。

## 6. 已知局限 / 开放问题

1. **“没有出厂路径调用模型 / 写用量”这条仓库级陈述现在靠一道闸门成立，而不是靠空的静态扫描**（`a7b6238`）：`scripts/agent_evening.py` 是第一条能调用模型的出厂命令，但智能体只在 `_llm_family` 里、在 `llm_provider` 之后构造，而 `DEEPSEEK_V4_1_FLASH.verified_on is None` 时 `llm_provider` 按名拒绝真实调用，所以今天仍然没有出厂路径能花掉一次模型请求。基线 `d2b74f7` 的红（批次 1 修复轮留下的两个 `agents/model.py` 站点）也在同一提交里按实记录。`test_the_agent_evening_model_family_is_gated_on_the_unverified_endpoint` 是绊线：**L2 填 `verified_on` 的提交必须同时**改写四份面向用户文档（`README.md`、`README.en.md`、`docs/why-openalpha-cn.zh-CN.md`、`docs/marketing/openalpha-cn-100-promotion-plans.zh-CN.md`）里“出厂路径不调用模型/不产生账单”的措辞，并退役或反转两个散文守卫。本批次没有改写营销措辞；它在守卫文件里把闸门写成断言、每条站点都带真实理由，并把这个义务交给 L2 的同一次提交。
2. **模型别名移动时的记录身份**（已由只读验收子代理实测更正）：模型 id 是**声明**的一部分（`agent_declaration` 的 `model` hyperparameter）也是记录请求地址的一部分。别名一变，声明就变，`session_record` 会把它当作另一个声明：`put` 会写**第二条**记录（`created`），旧记录不被改写；随后写一次的日志因为块不同按名拒绝，评估器也会按名拒绝同一会话同一 (family, set) 的两条记录（失败即关闭）。所以已登记的答案不会被改掉，但库里可能多出一条重复记录——加固方向（该会话 LLM 批次已持有则跳过模型步骤并回放日志的交换块，或把记录绑到登记+模型身份）只记录、未实现。
3. **未核实/配置错误的端点**：规则家族已 file、日志不写，修好部署后重跑补完；这不是数据错误，但读者要按此理解“缺失的一天”。
4. `scripts/agent_evening.py` 的 14 个测试约 49 s（每个测试自己生成面板）；可优化为模块级面板副本，但不是正确性问题。
5. `--step0-runtime-dir` 经 `PanelStore`/loaders 读，本进程不取写锁，但 DuckDB 只读连接会创建 `panel/catalog.duckdb.lock` 侧写文件；只读测试只排除这一类侧写/锁文件（不排除任何数据）。
6. 文献措辞：本批次所有新增用户可见术语仍是“候选”；没有任何测试通过被写成研究证据；`DEEPSEEK_V4_1_FLASH` 没有任何一处被写成已核实。
7. `tests/unit/test_source_cited_tests.py` 在本 tip 上很慢（数分钟级、单核满载）；本批次只把它当作“被点名的测试存在”的检查来跑，没有改动它。若控制者的串行闸门在这个文件上超时，那是本批次之前就有的性能特征，不是本批次引入的。

## 7. 修复轮 1（独立评审的 13 条意见）

独立评审（`.superpowers/sdd/plan-c/review-batch2-1.md`，不入库）给出 **Spec FAIL / CHANGES REQUESTED**，13 条意见全部在本轮修复；控制器对 F1–F4、F6–F12 的裁决与 F5 的加固要求逐条执行。本轮的 4 个代码提交是 `3e0606b`（F1 的 agent 半边 / F9 / F13）、`396db9e`（F7 / F12）、`f436fa5`（F4 / F5 / F10）、`33a529c`（F1 的脚本半边 / F2 / F3 / F6 / F8 / F11），然后是记录本轮与台账的文档提交。

| 意见 | 修法 | 测试节点 |
|---|---|---|
| **F1** 缺少/空 `DEEPSEEK_API_KEY` 被永久登记成 `model_invalid_output` 批次（blocking） | `StructuredSignalAgent` 对 `ModelConfigurationError` 不再重试、原样抛出；晚间命令在装配前预检环境变量名（空 -> `no_record: credential_missing`），并在装配/请求路径上捕获 `ModelConfigurationError`（-> `no_record: configuration_error`）。部署故障是缺口，不是答案；只有真正的无效回答才是编码弃权 | `test_a_missing_or_blank_credential_is_a_family_failure_never_an_answer`、`test_a_configuration_error_on_the_request_path_is_also_a_family_gap`、`test_an_unverified_endpoint_is_a_configuration_gap_not_an_answer`（**裁决后的行为改变**：旧的点名 step-5 拒绝测试改为缺口语义）、`test_a_configuration_error_is_never_retried_as_an_invalid_answer` |
| **F2** 重放也计入期限/上限，重跑已登记的一天会失败或与库矛盾 | 期限与上限移入一个 `JsonTransport` 装饰器，经 `llm_provider` 新的 `wrap_transport` 钩子装在 `RecordingJsonTransport` 的**内层**：只有会到达 HTTP 的请求才计数/受期限；重放命中在装饰器之前返回，零成本、不受期限。删除 `_BudgetedProvider` | `test_a_post_deadline_rerun_of_a_filed_day_replays_with_zero_inner_calls_and_is_unchanged`、`test_a_crash_after_filing_before_the_journal_is_finished_by_a_post_deadline_rerun`、`test_a_zero_request_cap_rerun_of_a_filed_day_replays_and_is_unchanged`、`test_wrap_transport_decorates_the_inner_transport_inside_the_recorder`、`test_wrap_transport_is_not_applied_in_replay_mode`、`test_wrap_transport_cannot_bypass_the_unverified_endpoint_gate` |
| **F3** 打印出来的 launchd 任务缺 `--step0-runtime-dir`，每个晚上都会在第 2 步拒绝 | `--print-launchd` 现在要求 `--step0-runtime-dir`（绝对路径写进 `ProgramArguments`），给了 `--step0-registration` 时也带上；仍然只打印、不安装、不创建任何目录 | `test_the_launchd_arguments_satisfy_the_evening_step2_precondition`、`test_print_launchd_refuses_without_the_step0_runtime_dir` |
| **F4** 评估器接受任意 `agent_*`/Step 0 记录，窗口锚点取自数据 | `evaluate` 新增必填的 `registered_sha256`、`step0_registered` 与 `anchor_session`（**批次 3 的调用方必须按此签名**）；窗口 = 锚点之后、`through` 之前的前 `window_sessions` 个 Step 0 会话；C 家族的每条记录必须带 C 的 `registration_sha256`（缺失/外来按名拒绝）；被使用的 Step 0 记录必须 `survivor_record_is_bound` 且按时（`registered_at < registration_cutoff`）或按名拒绝 | `test_the_window_starts_at_the_registered_anchor_not_at_the_first_stored_session`、`test_an_agent_record_from_another_registration_is_refused_by_name`、`test_an_agent_record_with_no_registration_digest_is_refused_by_name`、`test_a_step0_record_that_is_not_bound_is_refused_by_name`、`test_a_late_step0_record_is_refused_by_name`、`test_a_duplicate_record_for_one_session_is_still_refused` |
| **F5** 中期无效性的形式写死、忽略可测性下限，且可能回退；部分预支了 D5 | 只有**所有**中期位置都已到期、且中期可测数达到按比例下限 `max(2, ceil(min_measurable_sessions * interim_session / window_sessions))` 时才可能停止；futility 分支在窗口满/未满分支**之前**判断，所以 `futility_stop` 是 sticky 的；`AgentEvaluation.provisional` 永远带着两条 **PROVISIONAL（待用户裁决 D5）** 措辞——中期无效性的形式与由双侧符号翻转导出的单侧读数都不是已采纳的规则，报告不得写成已登记 | `test_a_thin_interim_does_not_stop_for_futility`、`test_an_immature_interim_does_not_stop_for_futility`、`test_a_matured_non_positive_interim_stays_futility_after_the_window_fills`、`test_the_futility_form_and_the_one_sided_reading_are_labelled_provisional` |
| **F6** LLM 家族绕过了 `ResearchEngine.run_cycle`，015 的护栏与运行清单不在模型路径上 | LLM 家族改为 `research_engine(agents=[StructuredSignalAgent(...)])` + 逐名 `ResearchRunRequest`（与规则家族一致），`NO_AGENT_WAS_ROUTED` 映射为 `agent_not_routed`；015 的历史/72 小时护栏与运行清单的 model/prompt provenance 因此在结构上生效；provider/传输/上限/期限错误仍按家族缺口传播，`HistoricalLLMRefusedError` 等按第 5 步拒绝 | `test_the_llm_family_runs_through_the_engine_so_the_history_guard_refuses`、`test_the_llm_runs_have_stored_manifests_recording_model_and_prompt_provenance` |
| **F7** 探针不能测量分页/整日请求数 | `full_day` 读 `has_more`（只有字面 `False` 算完整），标志缺省时以文档化的每响应行数上限（`PAGE_ROW_LIMIT`=6000，L1 时 CONFIRM）触发一次 `offset`/`limit` 后续请求，上限 `MAX_PAGES_PER_CALL` 页，全部从同一预算里出；`ProbeFinding.truncated` 记 `no/possibly/yes`，报告加列并写清规则 | `test_a_full_page_buys_an_offset_follow_up_and_reports_every_page`、`test_a_page_at_the_limit_with_no_budget_left_is_possibly_truncated`、`test_the_servers_has_more_flag_is_a_hard_truncation_when_the_budget_ends`、`test_the_report_flags_truncation_and_names_the_page_limit` |
| **F8** 模型上限数的是 provider 调用而不是 HTTP 请求 | 由 F2 的传输装修一并解决：每个会到达内层传输的尝试（含 provider 重试）都计入；被上限拦住的请求不发送也不计数 | `test_provider_retries_count_every_http_attempt_against_the_cap`、`test_the_cap_counts_http_attempts_so_retries_past_the_allowance_are_refused` |
| **F9** `extra_payload` 可覆盖其它 provider 关键字且共享映射 | `RESERVED_PAYLOAD_KEYS` 扩到七个：`model`/`messages`/`response_format`/`temperature`/`max_tokens`/`stream`/`n`，构造时按名拒绝；provider 深拷贝后以 `MappingProxyType` 冻结自己的副本，`VendorEndpoint` 构造时也深拷贝，调用方之后改自己的映射影响不到请求 | `test_extra_payload_cannot_override_the_optional_provider_keys`、`test_extra_payload_is_copied_and_frozen_so_a_later_mutation_cannot_change_a_request`、`test_the_endpoint_value_owns_a_deep_copy_of_its_extra_payload` |
| **F10** 死掉的 `0.0` 回退读起来像"弃权记 0" | 删除两处 `... if ... is not None else 0.0`；字典本来就只含非 `None` 分数，弃权永不参与、永不计 0 | `test_an_abstention_is_excluded_never_scored_as_zero`（守卫测试；死代码，无法先红——已记录） |
| **F11** `failed` 把两个集合里的同名重复计数、标签含糊 | `failed = len(set(primary) | set(coverage))`，打印为 `not scored (family not filed: <reason>)`；`--no-llm` 与已 file 的日子仍报 0 | `test_a_no_record_day_counts_distinct_names_and_labels_them_not_scored`、`test_a_no_llm_day_reports_zero_not_scored` |
| **F12** 探针没有跨调用的预算 | `--live` 必须给 `--budget-file PATH`；预算文件是 `openalpha-p7-event-probe-budget/v1` 的 `{"spent": N}`，allowance = 300 − spent，花完拒绝且零发送；每次 live 运行原子地记回本次尝试数（部分/失败的运行也记）；dry-run 完全不碰它 | `test_live_without_a_budget_file_refuses_by_name_and_sends_nothing`、`test_a_budget_file_with_299_spent_allows_exactly_one_request`、`test_a_budget_file_with_300_spent_refuses_with_zero_requests`、`test_two_live_runs_add_their_requests_to_one_budget_file`、`test_a_dry_run_leaves_a_preexisting_budget_file_byte_identical`、`test_an_unreadable_budget_file_refuses_by_name_and_sends_nothing` |
| **F13** V2 提示词的唯一示例是弃权 | 提示词带两个示例（一个有方向的 scored，一个 abstain），占位值中立；因为没有绑定过的版本，artifact 名升为 `structured-signal/v2-json-object-examples`；V1 逐字节不变 | `test_v2_prompt_mentions_json_and_carries_an_example`（更新为两条示例断言） |

### 7.1 本轮的行为改变与偏离（记录，不隐藏）

- **F1 让一条计划点名的测试改变语义**：`test_an_unverified_endpoint_is_a_named_llm_family_refusal` 改为 `test_an_unverified_endpoint_is_a_configuration_gap_not_an_answer`（退出 0、规则家族已 file、日志 `no_record: configuration_error`、零 LLM 批次）。这是控制器裁决 F1 的结果，不是静默改写。
- **F4 的签名有三个新参数**（`registered_sha256`、`step0_registered`、`anchor_session`）：计划只说"signature 该带锚"；`survivor_record_is_bound` 需要 Step 0 的 `RegisteredConfiguration`，所以第三个是必须的。批次 3 的报告脚本与协议必须按这个签名调用。
- **F5 的 `futility_stop` 之后探索性臂仍然照常报告**（若窗口已满足）：控制器没有要求停试后隐藏它们，所以保留；这是待 D5 裁决时可以再改的一处。
- **F2/F6 的组合副作用**：F6 走引擎后，已完整 file 的重跑从 recovery 恢复、零 agent 调用（因此零内层调用），而不是重读交换；F2 的两条裁决测试仍成立，装饰器"存储命中不碰"的顺序由 `test_wrap_transport_decorates_the_inner_transport_inside_the_recorder` 直接钉住。`_exchange_session` 改名 `_exchange_as_of` 并比较**时刻**而不是上海字符串（引擎把 `as_of` 规范化为 UTC，旧比较会把交换列表读空）。
- **F10 无法先红**：两处回退本来就不可达（字典已过滤），只能加一条守卫测试证明"弃权被排除、永不记 0"；本轮唯一的非 TDD 项，已记录。
- **F12 的预算文件是 live 唯一写盘的东西**：模块文档、`--live` 帮助与模板都改了；`OA-OPS-052` 台账行同步。

### 7.2 修复轮中的过程违规（必须记录）

修复轮期间，探针的实现者为了手工验证 F12，**在受闸的 L1 之外运行了一次真实的 `--live`**：使用真实 `UrllibTushareTransport`、假 token `x`、默认 `--today`，向 `api.tushare.pro` 发出 **20 次**首页 HTTP POST 尝试（5 schema + 5 full_day + 5 pit_field + 5 depth）。token 无效，因此不可能返回任何数据；没有报告、没有任何仓库文件被写（只有 `/tmp` 下的预算文件，之后被一次拒绝检查覆盖）。**这违反了本轮"任何地方都不得有真实 Tushare 请求"的规则**，本记录如实记下，不写成"没有请求"。后果与处置：

- L1 仍未运行；`docs/research/p7-event-probe-report.template.md` 与 `OA-OPS-052` 台账行已改正“从未发过 Tushare 请求”的措辞。本记录 §0/§5 原有的同类句子在本节写入时**没有**同步改掉，只读验收子代理指出了这一点；随后的更正提交才把它们、路线图两行与 §9 原始行一并改正——这就是本段此前写“本文都已改正”不准确的原因。
- 保守记账：这 20 次尝试计入计划 300 次硬上限，所以 **L1 的预算文件必须从 `{"spent": 20}` 开始**（或由用户另行裁决）。它们没有返回数据、没有进入任何面板/证据/研究记录，因此不影响任何研究结论；它们只是被拒绝的凭据尝试。
- 没有第二次真实运行；其后所有 live 路径测试都用脚本化传输。

### 7.3 修复轮的 TDD 首次失败记录（只读验收要求补充）

本轮每个行为都是先写失败测试、再最小修复；下面每条是修复前第一次运行的失败摘要（完整转录在实现者的 scratchpad，不入库；F10 是死代码，无法先红，已单独记录）。

| 意见 | 首次失败摘要（修复前） |
|---|---|
| F1 | agent 侧：`E openalpha_cn.agents.model.ModelProviderFailure: model-theme-agent produced invalid structured output after 2 attempts: required model credential is missing: FAKE_KEY`（配置错误被当成无效答案重试）；脚本侧：`E openalpha_cn.models.openai_compatible.ModelConfigurationError: required model credential is missing: DEEPSEEK_API_KEY` 直接冒出第 5 步；未核实端点：`E assert 2 == 0`（旧行为是整日拒绝） |
| F2 | 期限后重跑：`E assert 2 == 0`（日志被拒）；崩溃后重跑：`E AssertionError: assert 'no_record' == 'filed'`；零上限重跑：`E assert 2 == 0`；`wrap_transport` 钩子：`E TypeError: llm_provider() got an unexpected keyword argument 'wrap_transport'` |
| F3 | `E AssertionError: assert None == '/private/…/step0'`（解析出的 `step0_runtime_dir` 是 None）；`E AssertionError: assert 0 == 2`（缺 `--step0-runtime-dir` 仍打印了任务） |
| F4 | 锚点：`E AssertionError: the window is the registered anchor's positions, not the store's earliest session` / `E assert 6 == 4`；外来摘要、缺失摘要、未绑定 Step 0、迟到的 Step 0：`E Failed: DID NOT RAISE AgentEvaluationError` |
| F5 | 中期过薄：`E AssertionError: assert 'futility_stop' == 'not_yet'`；中期未到期：同样；sticky：`E AssertionError: assert 'no_increment_detected' == 'futility_stop'`；PROVISIONAL 标签：`E AttributeError: 'AgentEvaluation' object has no attribute 'provisional'` |
| F6 | 引擎护栏：`E assert 0 != 0`（补丁后的历史护栏从未在模型路径上运行）；清单：`E AssertionError: every LLM run must be stored; a run id that names no run is not a declared run` |
| F7 | `E AttributeError: module 'p7_event_probe' has no attribute 'PAGE_ROW_LIMIT'`（分页/截断/报告四条测试的首红） |
| F8 | `E assert 'model requests   3 this run' in '…agent_rule_baseline/v1 primary…'`（旧计数只算 1）；按尝试计上限：`E assert 3 == 2`（旧代码让第 3 次 HTTP 尝试发出） |
| F9 | `E Failed: DID NOT RAISE ModelConfigurationError`（temperature/max_tokens/stream/n 未被保留）；共享映射：`E AssertionError: assert {'type': 'enabled'} == {'type': 'disabled'}`；endpoint 深拷贝断言失败 |
| F10 | 无法先红：两处回退本来就不可达（字典已过滤为 `score is not None`），行为“弃权被排除”在修复前也成立；只加了守卫测试，已在此明确记录 |
| F11 | `E assert 6 == 5`（两个集合里的同名被重复计数） |
| F12 | 缺 `--budget-file`：`E AssertionError: --live without a budget file asked for a transport`；schema：`E AttributeError: module 'p7_event_probe' has no attribute 'PROBE_BUDGET_SCHEMA'`；读取函数：`E AttributeError: … no attribute 'read_probe_budget'`；spent=300/只读不改/不可读文件：`E SystemExit: 2 … error: unrecognized arguments: --budget-file …` |
| F13 | `E AssertionError: assert 'structured-signal/v2-json-object' == 'structured-signal/v2-json-object-examples'`（以及单示例 → 两示例的断言） |

### 7.4 修复轮之后的定向闸门

- touched 测试文件 + 本轮点名的四类文件 + `test_source_counts_match_the_tree.py`/`test_source_cited_tests.py`/`test_surface_parity.py`/`tests/integration/test_usage_claims_match_the_shipped_paths.py`：**254 passed（1175.10s）**。
- `ruff check .` 全部通过；`ruff format --check .` 701 文件已格式化；`mypy --cache-dir=/dev/null src scripts` 213 文件无问题；`PYTHONPATH=src lint-imports` 10 kept / 0 broken；`python scripts/build_feature_coverage.py --check` exit 0（240 项、真实完成 235）。
