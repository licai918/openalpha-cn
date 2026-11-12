# 方案 C 开发批次 1 收尾记录（2026-11-12）

> **状态：记录，不是验收。** 本文记下本批次交付了什么、没交付什么、哪些裁决是新做的、哪些偏离是计划没写的、哪些事留到以后、哪些事要用户来定。它只写工程状态；**工程完成不是研究证据**（`AGENTS.md`「研究诚实」），措辞仍是“候选”，没有任何一行把“通过的测试”写成别的东西。本文也不写任何闸门的通过判据或任何测试通过数以外的结论。

## 0. 先读这几句

- 本批次做的是方案 C 的三个 issue：`V2-P7-011`（面板推导的涨停 / 炸板 / 连板证据生产者）、`V2-P7-012`（DeepSeek 模型接入：Provider 配置、用量记账、提示词版本化、原始响应内容寻址存储）、`V2-P7-015`（LLM 历史污染护栏）。实施计划是任务书，**不在版本库内**：`.superpowers/sdd/c-batch-1/plan.md`（`.superpowers/` 不入库），本文是它的版本化记录。
- 代码在分支 `feat/v2-p7-c-batch-1` 上（从 `main` 的 `5e5218e` 分出）。**代码提交 16 个**（`git log --format='%h %s' 5e5218e..HEAD` 里除文档提交外的全部）；代码改动 38 个文件、+5,301 / −93（`git diff --stat 5e5218e..60328b2`；其后只有一个测试改名提交 `f645950` 触及代码）。文档提交另计（本文初版、其提交清单更正与路线图/§9/台账），最终的 tip 与完整清单以控制者的报告与 `git log` 为准。
- **未推送、未合并。** 交付的代码**没有跑过任何真实构建、因子、读数或冒烟，一次 Tushare 请求也没有发过，一次 DeepSeek（或任何 LLM）请求也没有发过**：所有测试都跑在测试时生成的夹具、已存的假记录和注入的假传输上。计划里那次 ≤5 次请求的联网探针**没有做**，等用户明确授权。
- **没有任何 P7 登记存在，`docs/research/p7-registration.json` 没有写出，也没有任何绑定被作废**；issue 总数仍是 253（本批次只关闭三行的代码部分，不关闭任何一行）。
- `DEEPSEEK_V4_1_FLASH` 是**占位**：model、base_url、structured_output_mode、价格都等人从 `api-docs.deepseek.com` 填，`verified_on=None`，`api_key_env="DEEPSEEK_API_KEY"` 是变量名而不是密钥。**没有一处把占位写成已核实**，也没有任何模型 id / endpoint 被当成“已核实”引用。
- 全量 `tests/unit` **没有在本批次内跑过**（由控制者在最终 tip 上串行跑一次，结果以控制者的报告为准）；实现者只跑定向文件与临近回归。本文不写“全套通过”。
- 本批次的文档波改：本文、路线图三行、规格 §9 两行、能力台账（`features.csv`、`summary.json`、`docs/release/openalpha-v1-feature-ledger.md`）与 `tests/unit/test_feature_ledger_test_tree.py` 的计数表。`AGENTS.md` 的“Current work pointer”与 `docs/HANDOFF_CURRENT.md` 不在本批次计划的任务里，留给控制者的文档波，见 §6。

## 1. 交付了什么（逐个 issue）

### `V2-P7-011` 面板推导的涨停 / 炸板 / 连板证据生产者——代码已交付、本行未关闭

- 提交：纯规则 `46acb46`；面板适配器 `bf236fc`；证据平面、组合根 opener 与 CLI `12ce8ae`。
- **纯领域规则**（`src/openalpha_cn/domain/limit_events.py`）：`session_state` 把一日的 `daily` 与 `stk_limit` 按证券连接（拒绝来自别的会话的行）；`board_streak` 在**同一个 `as_of`** 下回看，停牌（有带宽无 bar）跳过并计数、不破板也不续板（**D2**），无带宽的证券结束回看；`session_limit_events` 对触及上限的证券产出 `LimitEvent`：`closed_at_up` 且无前序连板 → `limit_up`（board 1）、有前序连板 → `consecutive_board`（board=连板数+1）、触及未收于上限 → `broken_board`（facts 带 `open_count=1` 与 `open_count_is_lower_bound=True`，**D3**，`prior_board_count` 是破掉的那一段）。全无涨跌停带宽的哨兵会话不产事件；`MAX_BOARD_LOOKBACK_SESSIONS=60` 到顶时把 `board_count_is_lower_bound` 置真。`domain/` 不 import 任何数值或基础设施库（`grimp` 测试钉住）。
- **面板适配器**（`src/openalpha_cn/panel_limit_events.py`）：`limit_events_on(store, *, session, as_of, exchange="SSE", max_lookback=60)`。目标会话先读，只有当日有触及的证券才回看；更早的开放会话**惰性**读，直到没有候选还能延续；每条会话（含更早的）都用调用者的同一个 `as_of` 经 `load_daily_bars` / `load_price_limits` 的点时门读；1 月的连板跨年时加载上一年的 `trade_cal` 再继续；`session` 的 16:30 之前按名拒绝（点名的是事件生产者，不是底层 loader）。模块的依赖行、跨缝 import 名与可达数据集写进了 `tests/unit/test_panel_ingest_import_isolation.py`，top-level 模块也进了 `pyproject.toml` 的 `backtest-no-numeric-stack-or-panel-plane` 禁止表。
- **证据平面**（`src/openalpha_cn/evidence/limit_events.py`）：`limit_event_snapshots` 经 `EvidenceBuilder.build` 生成 `EvidenceSnapshot`，用 builder 已经声明的 `limit_up` / `consecutive_board` / `broken_board` 三个 `market_event` 种类（没有新证据种类，也没有改任何已存行的字段）；`event_time` 是会话当日 15:00 上海、`available_time` = `revision_time` = 当日 16:30（`panel_ingest.session_publication_instant`）、`ingested_time` 是本次写入时刻；`evidence_id` 不含入库时刻，所以重跑是同一批内容寻址证据。`refuse_a_restated_session` 在 append 之前查会话发布时刻可见的 limit-kind 证据，id 集合不同就按名拒绝（`LimitEvidenceConflictError`），不写第二个互相矛盾的版本。
- **组合根与 CLI**：`runtime/composition.py` 新增 `EVIDENCE_DIRECTORY` 与 `evidence_store_at(runtime_dir)`，`build_storage` 经它打开同一路径（硬规则 5），没有给 `StorageContainer` 加字段（仍是 16 个）。`openalpha evidence limit-events --session D [--through E] [--runtime-dir] [--exchange] [--json]` 逐个开放会话在**各自的 16:30** 读、经 opener append；零事件写为空、打印 `NO limit events session=D`。台账行 `OA-EVID-010`。
- **测试**：`tests/unit/domain/test_limit_events.py`（9）、`tests/integration/panel/test_limit_event_producer.py`（7）、`tests/integration/test_limit_event_evidence.py`（6）；另有隔离表的行、`tests/panel_fixtures.py` 的 opt-in 生成器 `generate_limit_streak_panel()`（**面板夹具在测试时生成**，没有入库 `.parquet`/`.duckdb`/`.sqlite3`）。
- **未做 / 未关闭**：没有读真实面板、没有真实数据验收；本行保持打开。**偏离**：计划说夹具是 `PANEL_SHAPES` 的一个 opt-in 行，实际做成了 opt-in 构造函数——形状表有闭合性守卫（`tests/unit/test_panel_fixtures.py` 的 `EXPECTED_SHAPE_IDS`、触发矩阵，`tests/integration/panel/test_panel_shape_coverage.py`），修改它们不在本任务的写范围；构造函数是追加式、公开的，面板仍在测试时生成并经真实 writer 写入。另：CLI 多了一个 `--exchange`（默认 `TRADING_CALENDAR_DEFAULT_EXCHANGE`），计划方括号列表没写，但其它 `panel` 命令都有。

### `V2-P7-012` DeepSeek 模型接入——代码已交付、本行未关闭

- 提交：Provider 选项 `3894e41`；内容寻址记录/回放 `8ea5995`；提示词版本化 `4526bbb` + 摘要普查 `c21b5da` + 引用换行修复 `2d0ef00`；引擎侧 `f29580d`（Lead）；DeepSeek 端点与组合根装配 `286b7ce`。
- **Provider 选项**（`models/openai_compatible.py`）：`temperature: float | None`、`max_tokens: int | None`、`require_usage: bool`。默认载荷**逐字节**与改动前相同（测试按字节钉住）；只有设置时才出现对应键；`require_usage` 让缺少 `id` 或 `usage` 的成功响应变成 `ModelResponseError`。
- **新存储契约 `model-exchange/v1`**（`models/recording.py`、`storage/model_exchanges.py`）：`ModelExchange`（`extra="forbid"`、frozen；`schema_version: "model-exchange/v1"`；url、request、response、两个内容摘要、记录时刻）。`exchange_request_digest` = sha256(canonical JSON `{url, payload}`)，**不含 headers**（凭据通道不是请求身份）；`exchange_response_digest` 是**解码后**响应的 canonical JSON 摘要（不是线上字节，模块里写明）。`RecordingJsonTransport` 先查存储再决定是否调用：命中直接回放（返回深拷贝）；未命中且 `inner=None` 时抛 `ModelExchangeNotStoredError`（回放模式零调用）；否则调用 `inner`、存一次、返回。`FileModelExchangeStore` 写 `<root>/<request_digest>.json`，tmp + `os.replace` 写一次；读回经 `read_versioned` 并**重算两个摘要**，被外部编辑的文档按名拒绝；同一 `request_digest` 下的不同 `response_digest` 抛 `ModelExchangeConflictError`。
- **契约注册的一个偏离**：计划写“用 `single_version(...)` 注册 `model-exchange/v1`”，但 `single_version` 的文档与实现是给**没有 `schema_version` 字段**的模型用的（`versions={None: model}`），而本契约的行携带 `"model-exchange/v1"`；用 `single_version` 会让 `read_versioned` 把 store 自己写出的文档当成未知版本拒绝。实际注册成真正的 `ContractVersions(name="model-exchange", current_version="model-exchange/v1", versions={"model-exchange/v1": ModelExchange})`（`RUN_MANIFEST_VERSIONS` 的形状），并由一条“未知 `schema_version` 按名拒绝”的测试钉住。**这是本批次唯一的契约形状偏离**，由 Lead 在实现中授权。
- **提示词版本化**（`agents/prompts.py`、`agents/model.py`、`domain/run.py`、`runtime/engine.py`）：`PromptTemplate`（name、system、user_format）与 `version_ref(*, response_schema)` = sha256(canonical `{name, system, user_format, schema}`)，`component=f"prompt:{name}"`；`STRUCTURED_SIGNAL_PROMPT_V1` 的 system 文本是改动前逐字相同的字面量；`StructuredSignalAgent` 接受 `prompt` 模板（默认该常量）；`AgentProvenance` 新增可选 `prompt: VersionRef | None`，只有 `llm_backed` 能携带（确定性声明带 prompt 会拒绝）；引擎把实际执行智能体的 prompt 版本收进 `RunManifest.prompt_versions`（按首次使用顺序去重，和 `_model_versions` 一样），`DecisionLedger` 继承 manifest 的值。没有 LLM 智能体的运行仍记录空元组。
- **DeepSeek 端点与组合根**（`models/deepseek.py`、`runtime/composition.py`）：`VendorEndpoint`（多一个 `name` 字段，见偏离）；`DEEPSEEK_V4_1_FLASH` 是**占位**（见 §0）；`MODEL_EXCHANGE_DIRECTORY`、`model_exchange_store_at`、`model_usage_store_at`（`build_storage` 改用后者；没有加 `StorageContainer` 字段）、`llm_provider(endpoint, *, runtime_dir, clock, replay, transport=None)`：记录模式把端点传输包进 `RecordingJsonTransport`、接上用量存储、要求响应带 usage；回放模式 `inner=None`、`api_key_env=None`（无密钥、无调用）；`verified_on is None` 且没有注入 transport 的真实调用抛 `ModelConfigurationError` 并点名该常量与文档 URL。**用量只在內层传输真的被调用时入账**（评审 1 之后更正）：存储命中（重跑、崩溃后重试、第二个智能体发出同样的字节）与回放都不追加行；同一请求的重试以 1-based 尝试号作为**存储键**（不进发给厂商的 payload），所以每次尝试是各自记录的一次交换，回放复现同一串尝试。
- **测试**：`tests/unit/models/test_openai_compatible.py`（更新）、`tests/unit/models/test_recording_transport.py`（新，8 类断言）、`tests/integration/storage/test_model_exchange_store.py`（新）、`tests/unit/agents/test_prompt_versions.py`（新）、`tests/unit/agents/test_model_agent.py`（更新）、`tests/integration/test_manifest_model_provenance.py`（更新）、`tests/unit/domain/test_manifest_component_provenance.py`（更新）、`tests/unit/test_composition_llm_provider.py`（新）。
- **未做 / 未关闭**：≤5 次请求的联网探针没有做；确切模型 id 与 endpoint 未核实；没有对真实模型调用过；本行保持打开。

### `V2-P7-015` LLM 历史污染护栏——代码已交付、本行未关闭

- 提交：领域规则与引擎测试 `1e35b2d`；引擎调用点 `2170c9f`（Lead）；验证与策略回测路径 `f592cd9`。
- **规则**（`domain/llm_contamination.py`）：`HISTORICAL_RUN_MODES={replay, backtest}`、`LLM_FORWARD_MAX_LAG=timedelta(hours=72)`（**D4**，相等 72 小时仍放行）、`AGENT_FAMILY_PREFIX="agent_"`、`AGENT_KIND_HYPERPARAMETER="agent_kind"`、`HistoricalLLMRefusedError`。`refuse_llm_agents_in_history(*, path, mode, as_of, ran_at, agents)`：roster 里没有 `llm_backed` 就返回；模式是历史模式、或 `ran_at - as_of > 72h` 时按名拒绝，消息含路径、模式、`as_of`、运行时刻与滞后。`refuse_llm_prediction_in_backtest(*, prediction_id, declaration)`：非 `agent_*` 家族原样放行；`agent_*` 只有声明 `agent_kind="deterministic"` 放行，缺失 kind 失败即关闭（**D5**）。
- **引擎**（`runtime/engine.py`）：在 `router.route(...)` 选出 roster 之后、**在 `_load_or_start_recovery` 之前**调用第一个入口，所以被拒绝的周期不写恢复行（测试断言 `recovery_store` 仍为空）；选中的 roster 而不是装配的全部智能体——路由丢掉的智能体不污染任何东西。
- **验证与策略回测路径**：`backtest/validation.py` 的 `OutcomeValidator.validate` 第一条语句调用它（`path="validation"`，`mode/as_of/ran_at` 与 agent roster 来自 `research.manifest`），该模块**仍只 import `domain`**（`backtest-studies-touch-no-store` 10/10 保持）；`strategy_view._prediction_rows` 在记录读出之后、读任何行之前调用第二个入口；CLI 的 `validation record` 与 `strategy backtest` 把 `HistoricalLLMRefusedError` 映射成 `PanelExit.bad_request`（3，而不是 5 的兜底）；REST `validate` 路由返回 422 并带同一句拒绝文本。
- **测试**：`tests/unit/runtime/test_llm_history_guard.py`（9）、`tests/integration/test_llm_history_guard_paths.py`（新，11）、`tests/unit/test_cli.py`（加一条映射测试）。
- **未做 / 未关闭**：没有对真实模型或真实历史数据运行过；本行保持打开。

## 2. TDD 与评审轮次

- 三条轨道（011 / 012 / 015）并行实现，每一条都按计划的 Task 顺序一个一个做，每个行为都是**先写失败的测试、跑出正确的失败原因、再最小实现、再跑**。每个任务的“第一次失败输出”记在 `.superpowers/sdd/notes-<track>.md`（不版本化），最终报告 `task-report.md` 汇总。
- `runtime/engine.py` 是本批次唯一的跨轨道共享文件（`V2-P7-012` 的 `_prompt_versions` 与 `V2-P7-015` 的 guard 调用点）。为免两个实现者互相覆盖，它由控制者（Lead）单独编辑、单独提交（`f29580d`、`2170c9f`），各任务的领域代码与测试仍由原实现者提交。
- 任务评审：09 个计划任务各自在其轨道的最终 tip 上做定向运行；Lead 在合流后的 tip 上跑全部门槛（见 §4）。计划中的 Review Focus 5 条（前视、重跑证据、请求/响应身份、护栏位置、散文漂移）逐条核对，见 `task-report.md`。

## 3. 本批次的裁决与计划未覆盖处的选择

- **D1–D6 全部按计划推荐实现**：011 的事件只写**证据平面**、只写有事件的证券（约 10² 行/日），从不写面板行；停止/跳过规则按 D2；炸板 `open_count=1` 且标为下界（D3）；前向滞后 72 小时（D4）；`agent_*` 预测在策略回测里一律拒绝、缺失 kind 失败即关闭（D5）；能力台账行在本批次加上（D6）。
- **计划与实现的偏离（全部记录在此）**：
  1. `model-exchange/v1` 用 `ContractVersions` 注册，而不是 `single_version(...)`——理由与证据见 §1 `V2-P7-012`。
  2. `VendorEndpoint` 多一个 `name: str`：通用的 `llm_provider` 要在“未核实端点”的拒绝里点名 `DEEPSEEK_V4_1_FLASH`，Python 变量名不是它本来能取回的值。
  3. 面板夹具做成 opt-in 构造函数 `generate_limit_streak_panel()` 而不是 `PANEL_SHAPES` 的一行（见 §1 `V2-P7-011`）。
  4. `evidence limit-events` 多一个 `--exchange` 选项（与其它 `panel` 命令一致）。
  5. 三处机制上必须的既有测试文件改动（计划文件列表没写，Lead 在实现中批准并记录）：`tests/integration/test_retired_claims_stay_retired.py` 删掉“一次运行记录 prompt 版本”这条 RetiredClaim 及其 premise（该断言在本批次变成真的）；`src/openalpha_cn/model_view.py` 的 daily-run docstring 把“prompt 只是字面量、`code_commit` 已钉住”这一已被本批次撤销的理由换成“该路径没有 LLM 智能体，槽位仍为空”；`tests/unit/domain/test_contract_identity.py` 在 `DECLARED_PLAIN_DIGESTS` 里登记三个新的全宽摘要点（只加表项，不改 mint 表或每点摘要数）。
  6. `tests/unit/test_surface_parity.py`：新 CLI 命令是 CLI-only 能力（读本机面板、追加本机证据库），声明进 `CLI_ONLY`，`cli_commands` 38 → 39；由 Lead 提交 `9152bf0`。
  7. 最终闸门上 `tests/unit/test_source_counts_match_the_tree.py` 报出计数漂移：`evidence/limit_events.refuse_a_restated_session` 的参数名是 `store`，而该审计把 `store.query(...)` 计成面板门 `PanelStore.query` 的调用者，于是 `panel_ingest.py` 里“four callers”的句子与树上的计数不符。修法是把参数改名为 `evidence`（它本来就是证据平面的库），而不是把 `panel_ingest.py` 的句子改成 five——后者会让一句关于面板读门的句子变成假话。提交 `60328b2`。另：`tests/unit/test_cli_help.py` 的着色前提在本沙箱里因 `TERM=dumb`/`NO_COLOR=1` 不成立（Rich 不输出转义），在 `TERM=xterm-256color`、不设 `NO_COLOR` 时 7/7 通过；这不是本批次的代码改动，见 `task-report.md` 的开放问题。
- **计划没有覆盖、按更保守一侧选择并记录的**：`AGENTS.md` 的“Current work pointer”与 `docs/HANDOFF_CURRENT.md` 不在本批次任务里，没有改（留给控制者的文档波）；`model_view.py` 只改那一条已失效的 docstring 理由，不动代码；`CHANGELOG.md` 没有改（本仓库的 CHANGELOG 由发布波维护，计划没写）。

## 4. 顺序与闸门不变

- 本批次**不改** `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §5 的顺序，也**不改** §5.2 的任何闸门：`006a` → `006b`、G-0（一次预登记、只读一次、读数前写好的停止规则）、真实数据冒烟、真钱闸门与 2027-01-04 之前的硬闸门都不动；issue 总数仍是 253。这些是既有 issue 的代码交付，不是新闸门。§9 的修订记录加了两行（本批次交付了什么、以及“没有漂移”）。
- 本批次内跑过的门槛（定向，不是全量）：所有被触及的测试文件 + 四个点名文件（`test_panel_ingest_import_isolation.py`、`test_source_counts_match_the_tree.py`、`test_source_cited_tests.py`、`test_feature_ledger_test_tree.py`）、`lint-imports`（10 kept / 0 broken）、`ruff check`、`ruff format --check`、`mypy --cache-dir=/dev/null src scripts`、`python scripts/build_feature_coverage.py --check`。全量 `tests/unit` 由控制者在最终 tip 上串行跑一次，结果以控制者的报告为准。

## 5. 未做与前置条件

- **没有真实数据、没有网络**：没有读真实面板、没有 Tushare 请求、没有 DeepSeek（或任何 LLM）调用、没有跑真实构建/因子/读数/冒烟。`tests/conftest.py` 的 socket 守卫全程开着；`tests/checkout_guard.py` 的检查在实现阶段因共享 worktree 被降级为 `report`（另见 `task-report.md` 的偏差记录），最终闸门在安静的工作树上按默认 `fail` 模式跑。
- **`V2-P7-012` 的联网探针**是用户授权的单独步骤，不是本批次的一部分：一个人从 `api-docs.deepseek.com` 填 `DEEPSEEK_V4_1_FLASH` 的 model / base_url / structured_output_mode / 价格并设 `verified_on`，然后（用户说开始之后）≤5 次记录模式请求、写入一个 scratch 运行目录、产出一份探针报告；密钥只经 `DEEPSEEK_API_KEY`。在此之前，真实请求按名拒绝。
- **`V2-P7-011` / `V2-P7-015` 的真实数据验收**同样没有发生：011 需要真实面板上的手标会话复核；015 的评估路径没有对真实历史/真实模型跑过。三行保持打开。
- 真钱闸门仍关着；没有任何登记，所以没有绑定被作废。

## 6. 已知局限 / 开放问题

- `tests/unit/test_source_cited_tests.py` 在本 tip 的一次运行里非常慢（数分钟级、单核满载）；本批次只把它当作“被点名的测试存在”的检查来跑，没有改动它。若控制者的串行全量闸门在这个文件上超时，那是本批次之前就有的性能特征，不是本批次引入的（见 `task-report.md` 的开放问题）。
- 011 的跨年连板测试必须构造上一整年的 `daily`/`stk_limit` 分区（loader 本身要求所读年份的每个开放会话都在），这是 loader 的既有规则，不是夹具偷懒。
- `model_view.py` 的那条 docstring 之外，`RunManifest.agent_versions` 对 LLM 智能体的记录仍只有 kind 与 model；本批次把 prompt 加到 `prompt_versions`，但没有把每个智能体的 prompt 与它的结果一一绑定（那是 `V2-P7-013` 的批次面）。
- 台账：本批次加了 5 行（`OA-EVID-010`、`OA-OPS-050`、`OA-MODEL-006`、`OA-AGENT-010`、`OA-OPS-051`）；`summary.json` / `openalpha-v1-feature-ledger.md` 由 `scripts/build_feature_coverage.py` 重新生成，`features.csv` 的每行都有 AST 可解析的 `file#symbol` 与真实 pytest node id。

- **被改写的面板与已写出的证据（评审 1，F4）**：面板存储不保留旧版本，所以一个被改写的历史会话不能"按原样重放"——在原来的 `as_of` 上读会被 loader 按名拒绝，而不是返回原来的事件；`evidence limit-events` 对这样的会话退出 `unhealthy`。同理，**规则或摘要文本的编辑会冻结已写出会话的重跑**：`LimitEvent.summary()` / `facts()` 一改，所有内容哈希都变，`refuse_a_restated_session` 从此拒绝每一个已经写过的会话，而本批次没有"给更正后的会话一个新身份"的机制。真实数据运行之前应当先知道这两条；这不是缺陷，是"内容寻址 = 同一内容同一身份"的代价，记在这里而不是留给运行者去发现。

## 7. 提交与文件清单（本批次）

- 分支 `feat/v2-p7-c-batch-1`，从 `5e5218e` 分出；代码提交（按时间倒序，tip 以 `git log` 为准）：
  `f645950`（011/012 两个测试改用计划里的原名，Lead）、`60328b2`（011 证据读门的参数改名，Lead，最终闸门发现）、`9152bf0`（011 表面奇偶声明，Lead）、`f592cd9`（015 验证/回测路径）、`286b7ce`（012 DeepSeek/组合根）、`12ce8ae`（011 证据平面/CLI）、`2d0ef00`（012 引用换行修复）、`bf236fc`（011 面板适配器）、`f29580d`（012 引擎侧，Lead）、`c21b5da`（012 摘要普查）、`4526bbb`（012 提示词版本化）、`2170c9f`（015 引擎调用点，Lead）、`46acb46`（011 纯领域）、`1e35b2d`（015 规则）、`8ea5995`（012 记录/回放）、`3894e41`（012 Provider 选项）；文档提交 `890e1dc`（本文、路线图、§9、台账）。
- 文件面：`src/` 新增 `domain/limit_events.py`、`panel_limit_events.py`、`evidence/limit_events.py`、`models/recording.py`、`models/deepseek.py`、`storage/model_exchanges.py`、`agents/prompts.py`、`domain/llm_contamination.py`；修改 `runtime/engine.py`、`runtime/composition.py`、`cli.py`、`models/openai_compatible.py`、`agents/model.py`、`domain/run.py`、`backtest/validation.py`、`strategy_view.py`、`api/app.py`、`model_view.py`；测试新增 9 个文件、修改 10 个；另有 `pyproject.toml`（禁止表一行）、`tests/panel_fixtures.py`（opt-in 生成器）与台账/文档。

## 8. Fix round 1（评审 1 之后，2026-11-12）

评审 1（`/Users/licai918me.com/Documents/OpenAlpha cn/.superpowers/sdd/c-batch-1/review-1.md`，0 critical / 1 important / 7 minor）与控制器裁决（`ledger.md`）之后的一轮修复。每条都先写失败测试、看到正确的失败原因，再修：

- **F1（`V2-P7-012`，important）用量与重试**：`RecordingJsonTransport` 现在把 1-based 尝试号作为**请求摘要（存储键）**的一部分（`ModelExchange.attempt`，不进厂商 payload），每次尝试是独立存储的交换，回放复现同一串尝试；`last_answer_was_replayed` 让 provider 只在**内层传输真的被调用**时入账，所以记录模式的存储命中与回放都不写第二行用量；`StructuredSignalAgent` 的重试把尝试号交给 provider，第一次答案校验失败后的重试拿到的是新答案，全败时报告真实的校验错误而不是用量冲突。
- **F2（`V2-P7-011`）**：`refuse_a_restated_session` 只比较本生产者（`source_id == "openalpha-panel-derived"`）的限价事件，别的 provider（如 `chainlin` 的同三种 kind）同一时刻的行不再被读成"被改写"。
- **F3（`V2-P7-011`）**：连板回看走到面板存下的最早年份边缘（上一年日历/分区读不出）时，结束回看并把仍活着的候选的 `board_count_is_lower_bound` 标真，不再丢掉整场的所有事件；目标年之内缺分区仍是拒绝。
- **F4（`V2-P7-011`）**：测试改名为 `test_a_restated_earlier_bar_is_refused_at_the_original_as_of`；上一节（§6）补上"被改写的会话不能按原样重放"和"规则/摘要编辑会冻结已写出会话的重跑"两条后果。
- **F5（`V2-P7-012`）**：`verified_on is None` 时，真实/默认传输（`None` 或 `UrllibJsonTransport`）都拒绝；测试传输必须显式注入，并有一条 `transport=UrllibJsonTransport()` 被拒的测试。
- **F6（`V2-P7-011`）**：`halted_sessions_skipped` 只数"被连板跨过"的停牌：停下来当待定，只有更早的 `closed_at_up` 真的跨过它们时才计入；下方便是破板/普通交易的停牌不计数。
- **F7（`V2-P7-012`）**：`PromptTemplate.__post_init__` 用 `string.Formatter().parse` 要求 `user_format` 恰好含一个 `{payload_json}` 字段（字面花括号、别的占位符、空模板都在构造时按名拒绝），不再让 `KeyError`/`IndexError` 在周期内炸掉。
- **F8（`V2-P7-011`）**：`evidence limit-events --json` 在区间中途被拒时，错误文档仍在 `status`/`exit_code`/`detail` 之外带上已经写出的 `events` 与 `sessions`；命令 docstring 写明"重跑同一区间是幂等的"。

同一轮还修了两处本轮改动暴露出的既有守卫（都不是评审 1 的条目，但不修则闸门红）：`models/base.ModelProvider.generate_json` 新增的可选 `attempt: int = 1` 让 `test_alpha_model_boundary.py` 的协议形状断言需要确认它是重试序号而不是数据槽；`runtime/composition.llm_provider` 是 `src/` 里第一个构造 `OpenAICompatibleProvider` 与用量存储的地方，`test_usage_claims_match_the_shipped_paths.py` 的 `USAGE_SITES` 与控制断言、台账 `OA-MODEL-001`/`003`/`004` 的说明随之改成"组装但不调用/不入账"。本轮不改 §5 的顺序，也不改 §5.2 的闸门；issue 总数仍是 253。
