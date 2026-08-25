# OpenAlpha CN v2 四缝审计

Status: Baseline evidence — 2026-07-30 全库扫描
Baseline: `main` @ `e5c6b90`
方法: 四路并行只读扫描（结构 / 产品 / 测试 / 技术），全部结论带 `file:line`
用途: 路线图切片的事实依据，以及"不存在缺口"的证明。每条 finding 必须有一个 issue 关闭它。

统计：**103 条 finding**（F1–F103，无重复无缺号）。其中 **39 条属于 10 个标 🔴 的小节，是 P1 之前必须关闭的前置项**。

---

## 1. 结构缝（Structural）

### 1.1 分层已经反向，加一层就成环 🔴

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F1 | `storage/` 向上依赖 4 个上层包，是全库唯一的反向依赖 | `storage/recovery.py:11`→`agents.base`；`storage/batch.py:8`→`runtime.batch`；`storage/product.py:7`→`product.research`；`storage/portfolio.py:7`→`backtest.portfolio`；`storage/memory.py:7`→`runtime.memory` | `V2-P0B-012` |
| F2 | 已存在一个被 `TYPE_CHECKING` 掩盖的真实循环 | `storage/batch.py:8` ↔ `runtime/batch.py:15-16`；旁证：`runtime/batch.py:3` 是全包**唯一**的 `from __future__ import annotations`，`:68`/`:97` 是全库唯一两处函数内 domain import | `V2-P0B-012` |
| F3 | 第二个潜在环：任何 `storage/panel.py` 想引用研究契约就立即成环 | `runtime/engine.py:22`→`storage/recovery.py:11`→`agents/base.py`，而 `runtime/engine.py:11` 也→`agents/base.py` | `V2-P0B-001`、`V2-P0B-012` |
| F4 | 无任何分层 lint 门 | `pyproject.toml:63-64` `select=["E","F","I","UP","B","SIM","RUF"]`，无 banned-API 规则；全库无 import-linter / tach 配置 | `V2-P0A-005` |

### 1.2 两个手工同步的组装根 🔴

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F5 | 同样 8 个具体 store 在两处各装一遍，靠人工保持一致 | `api/app.py:254-262` ≡ `sdk.py:63-71`；`api/app.py:264-269` ≡ `sdk.py:107-113` | `V2-P0B-002` |
| F6 | v2 新增 5 层 ⇒ 10 处新装配需手工同步 | 同上 | `V2-P0B-002` |

### 1.3 `runtime/engine.py` 一个文件四份职责 🔴

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F7 | 契约 + 恢复/幂等 + 信号聚合 + 政策映射混在 380 行里；4 个下游**只为契约**而 import 它 | 契约 `:30`/`:58`；恢复 `:193-309`；聚合 `:311-358`；政策 `:360-380`。下游：`backtest/validation.py:10`、`backtest/replay.py:13`、`product/research.py:10`、`runtime/batch.py:13` | `V2-P0B-001` |

### 1.4 存储抽象不存在（ADR 声明与实现不符）🔴

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F8 | 除 `ResearchMemory` 外**没有任何** storage Protocol，所有消费者直接点名具体类 | `runtime/memory.py:22-29` 是唯一 Protocol；无 `RunRepository`/`RecoveryStore`/`EvidenceStore`/`BatchTaskStore`/`PortfolioLedger`/`ReportStore`/`WatchlistStore` 抽象。ADR-0001:19 声称"存在允许后续 PostgreSQL 实现的存储接口" | `V2-P0B-003` |
| F9 | 引擎一半持久化被抽象，一半焊死 SQLite，且自己 new 基础设施 | `runtime/engine.py:22-23` import 具体类；`:75`/`:81` 类型标注；`:89` `SQLiteRecoveryStore(repository.path)` 反向摸 `storage/sqlite.py:19` 的 `.path` | `V2-P0B-003` |

### 1.5 ADR 违规（真实的三处）

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F10 | `providers/` 半句被违反：provider 直接 import duckdb，且 DuckDB 异常类型成为其失败语义的一部分 | `providers/file.py:10`，用于 `:48`（`duckdb.Error`→`ProviderFailure`）、`:96`。`providers/__init__.py:12` 再导出 ⇒ `import openalpha_cn.providers` 传递性拉入 DuckDB | `V2-P0B-011` |
| F11 | SQLite 持久化住在 `models/` 层而非 `storage/` | `models/governance.py:3` `import sqlite3`，`SQLiteModelUsageStore` `:73-92`（`CREATE TABLE` `:83`，`connect` `:92`）。AlphaModel 若照抄就是双份违规 | `V2-P0B-011` |
| F12 | 共享证据路径被绑死在具体 `FileProvider` 而非 `DataProvider` Protocol | `evidence/service.py:17`、`:51`；`:52` 还硬编码 `dataset="events"` | `V2-P0B-011` |
| F13 | `domain/` 包内做文件系统写入并硬编码仓库布局 | `domain/schema.py:38-41`，`Path(__file__).parents[3]` | `V2-P0B-011` |
| F14 | `domain/` 目前 100% 无数值库；三处会诱使 v2 把 pandas/numpy 拖进契约 | ① `domain/validation.py:13-20` 单标量 `contribution` + `:45-52` 精确求和校验；② `domain/signal.py:12-53` 单标的标量 strength；③ `backtest/event_study.py` 纯 `math`/`statistics` 零包依赖 | `V2-P0A-005`（lint 门）、`V2-P0A-007`（ADR） |

### 1.6 v2 各层的挂接点与阻塞

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F15 | `ProviderBatch` 是逐行模型：每行 freeze + canonical-JSON hash，`payload_digest` 再全量序列化，逐行 `is_visible_at` ⇒ 面板规模不可用，需列式兄弟契约 | `providers/base.py:92-96`、`:143-150`、`:139` | `V2-P1-002` |
| F16 | 两个付费 provider 都产出 `kind="daily"`，而证据构建器不认 ⇒ 付费数据今天进不了系统 | `providers/tushare.py:185`、`providers/akshare.py:164` vs `evidence/builder.py:55-63`、`:88-90` | `V2-P1-001`、`V2-P1-004` |
| F17 | ~~`ModelProvider` 是 LLM-JSON 形状，无法表达面板 fit/predict；`ModelRegistry`/`ModelRetryPolicy`/`SQLiteModelUsageStore` 全部 LLM 专用~~ **已关闭**：`domain/alpha_model.py` 是与之并列而非派生的量化边界 —— `AlphaModel.fit(TrainingSet) -> FittedAlphaModel` 与 `FittedAlphaModel.predict(FeatureCrossSection, *, predicted_at) -> PredictionBatch`，两个 `runtime_checkable` Protocol 上 `isinstance` **双向为假**。`governance.py` 的三件 LLM 专用件**一件都没复用**，也没有被抽成「共同基类」。**形状是实测的而不是假设的**：该 Protocol 的成员就是 `metadata` 与 `generate_json`（自 AST 读出），三个形参标注恰为 `str`/`str`/`dict[str, Any]`，没有一个能承载 as-of；把最慷慨的 `generate_json` 输出交给 `PredictionBatch` 时，缺的恰是 `as_of`/`predicted_at`/`artifact`/`predictions` 四个字段。**分离已被 `lint-imports` 从另一个方向强制**：`backtest-no-numeric-stack-or-panel-plane` 的 forbidden 名单里就有 `openalpha_cn.models`，所以量化契约若放进 `models/`，`V2-P4-013` 的 walk-forward 研究会 import 不到它 | `models/base.py:32`、`:39-46`（行内 `:32-40` 覆盖到方法开头，未漂移）；`models/governance.py:13`、`:37`、`:71` —— 三处坐标各漂移两行，且 `SQLiteModelUsageStore` 本体在 `V2-P0B-011` 就已搬去 `storage/models.py`，留在 `:71` 的是 `ModelUsageStore` Protocol | `V2-P4-011` |
| F18 | ~~manifest 没有量化模型版本槽，且现有槽已被滥用~~ **已关闭**：manifest 现有三个组件面 —— `agent_versions`（谁跑了、什么种类，S40）、`model_versions`（vendor `provider_id`/`model`）、`alpha_model_versions`（`016` 的内容寻址制品，`artifact_id` 按 `stable_model_id` 的产出形状约束）。`prompt_versions` 仍空并写明理由（唯一的 prompt 是代码字面量，已由 `code_commit` 钉住）。ledger 不新增镜像字段：它经 `run_manifest_id` 一次继承全部已声明输入 | `domain/run.py:56-57`；`runtime/engine.py:131-134` 把 agent id 塞进 `model_versions` 并写死 `"baseline/v1"`，`:135` 永远 `prompt_versions=()`，`:160-161` 镜像进 ledger（实际坐标已漂移到 `:92-96`/`:128-129`）。**滥用的代价实测**：只换 vendor model 的两次运行得到同一个 `run_manifest_id` 与同一个 `decision_id` | `V2-P4-010` |
| F19 | ~~路由只按证据家族交集选 agent ⇒ **无 evidence family 的 agent 永不被路由**，因子/模型型 agent 无路可走~~ **已关闭** | `runtime/router.py:12-23` 读 `payload["family"]`；`agents/baseline.py:48`、`:84`、`:120` 声明家族集。**坐标未漂移**：改动前 `route` 正是 `agent.evidence_families & families`。`ResearchAgent` 现声明 `feature_dependencies`，路由两半都要满足、量词故意不同（家族取任一、列取全部），两半皆不声明者**具名拒绝**而非丢弃 | `V2-P4-008` |
| F20 | ~~`AgentContext` 只带单标的证据元组，没有特征/面板句柄~~ **已关闭** | `agents/base.py:12-20`。新增 `features: FeaturePlane \| None`，协议声明在消费者旁边（`ShortlistDocumentStore` 的形状），`domain/alpha_model.py::FeatureCrossSection` 无适配器即满足，故 `agents/` 对 `feature_matrix`（进而 DuckDB）零 import 边；`runtime_checkable` + `arbitrary_types_allowed` 使字段是 isinstance 而非逐行 pydantic 重建（实施决策 31）| `V2-P4-009` |
| F21 | `PortfolioOrder` 无目标权重；`PortfolioLimits` 只有 2 个字段（无行业上限/换手预算/现金下限） | `backtest/portfolio.py:131-139`、`:122-128` | `V2-P5-002` |
| F22 | 多日回测强制单标的步 ⇒ K 只股票的调仓要 K 步；归因只有逐标的 PnL | `backtest/multi_day.py:22`、`:31-35`、`:49-55`、`:73` | `V2-P5-003` |
| F23 | 批量上限 1000 挡住 5000 标的全市场 | `runtime/batch.py:58`、`api/app.py:113`；`product/research.py:20` `limit≤1000` | `V2-P4-019` |
| F24 | 纯算法模块签名耦合 SQLite 类 | `backtest/replay.py:15`；`backtest/multi_day.py:16`、`:83` `ledger: SQLitePortfolioLedger \| None` | `V2-P0B-003` |
| F25 | 已声明但全库无人使用的扩展点 —— ~~可直接用于面板/因子查询暴露给 agent~~ **仍开着，且「可直接用于」已被 `V2-P4-009` 实测否证** | `tools/base.py:54-62 ResearchTool`；`tools/` 在 `src/` 内无 importer（此事未变）。**两处度量**（`tests/unit/agents/test_feature_plane_seam.py`）：① `ToolRequest.kind` 是 `max_length=64`，而本构建最长因子键的 neutralized 拼法长 **89 字符**，`ToolRequest` 直接 `ValidationError` —— 整个 neutralized 档位问不出口；② `ToolResult` 恰有三个字段且 `extra="forbid"`，没有字段能装数字，且 `status="success"` 强制 `evidence_ids` 非空，所以「读到值但无 evidence id」只能报成 `no_data`。`V2-P4-009` 因此另立 `FeaturePlane`（并**连同消费者一起交付**，不再造第二个无人用的扩展点），而不是加宽 `ResearchTool` 去同时回答两个问题。关闭本条需要的是给 `ResearchTool` 找一个真正属于它的证据查询消费者，或删掉它 | 待定 |
| F26 | 三个模块各干两三件事，v2 前应先拆 | `models/governance.py`（注册+重试+持久化）；`product/research.py`（筛选 `:45-80` + 自选股 `:83-95` + 报告 `:98-140`）；`evidence/service.py`（通用构建 `:37-40` + FileProvider 便利 `:43-53` + 序列化校验 `:56-76`）；`storage/parquet.py`（写+读+逐行完整性校验） | `V2-P0B-011`、`V2-P4-006` |

---

## 2. 产品缝（Product Surface）

### 2.1 v2 能力：四个面 + 后端全部为零

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F27 | 15 项 v2 能力在 REST / CLI / SDK / Web **及后端**全部缺失 | grep `FactorDefinition\|FactorRun\|ICReport\|FeatureMatrix\|ModelArtifact\|TrainingJob\|WalkForward\|Prediction\|CandidateRanking\|TargetWeight\|PaperPortfolio\|Schedule\|JobStatus` 全库唯一命中是 `backtest/execution.py:47 CostSchedule`（交易费率表，无关）。无 `panel/`、`factors/`、`predictions/`、`rankings/`、`jobs/` 包 | P1–P5 全部 |
| F28 | 可复用的最近邻（起点而非实现） | 目标权重/Paper：`PortfolioSimulator`+`SQLitePortfolioLedger`；作业状态：`BatchResearchTask`+`BatchProgressEvent`+`SQLiteBatchTaskStore`（durable-task 模式已存在且可泛化）；候选排序：`ScreeningResult`/`ScreeningItem`；Walk-forward：`PortfolioBacktestRunner` | `V2-P5-010`、`V2-P4-005` |

### 2.2 现有能力的面不对称（真实产品缺口）

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F29 | **结果验证在 SDK 中完全缺失**，而 REST + Web 都有 —— 最尖锐的一处不对称 | `sdk.py` 从不 import `openalpha_cn.backtest.validation`；`OutcomeValidator` 只经 `api/app.py:526-539` 与 Web `AttributionPanel` 暴露。SDK docstring 声称"完整研究流程" | `V2-P5-013` |
| F30 | 7 项能力只在 REST，SDK 与 CLI 都没有 | `market/events`（过滤逻辑内联在 `api/app.py:326-329`，Python 侧不可复用）、`themes`（`:340`）、batch list/get/events/cancel/retry、`watchlist/{subject}/remove`、`reports/{report_id}` | `V2-P5-013` |
| F31 | ~~同名操作语义分叉：CLI `evidence build` **不落库**，SDK `build_file_evidence` 落库~~ **已关闭（`V2-P5-013`）**。坐标已漂移：实测在 `cli.py:728-754`，`typer.echo(response.model_dump_json())` 之后什么也不做；`POST /api/v1/evidence/build` 与 SDK **都**追加，所以是三面中两面一致、命令行落单。现在 `evidence build` 收 `--runtime-dir`（默认 `./runtime`，与本 CLI 其它每条命令同义）并经组合根追加。**测试从第二个面读回**而不是断言 stdout：旧命令本来就把正确的快照打印出来了，对 stdout 的断言在修前修后都是绿的 | `cli.py:728-754`；`sdk.py:141-158`; `api/app.py:1719-1724` | `V2-P5-013` |
| F32 | 批量提交与单次运行的证据校验宽严不一 ⇒ **同一 payload 在 `/research/run` 成功、在 `/research/batches` 失败** | `api/app.py:73 ResearchApiRequest` 带 `verify_serialized_evidence`；`:111 BatchSubmitRequest.requests` 用裸 `ResearchRunRequest` | `V2-P0B-002` |
| F33 | 同能力默认值分叉 —— **前半条已被实测证伪，后半条成立且本行明确不动它** | **前半条（`limits`）不是分叉**：`PortfolioSimulator.__init__` 写着 `self.limits = limits or PortfolioLimits()`（`backtest/portfolio.py:100`），所以 SDK 的 `None` 与 REST 的 `PortfolioLimits()` 构造出**同一个** simulator；两个签名不同、行为逐字节相同，这是类型标注的差异而不是能力的分叉。**后半条（`criteria`）成立**：`ScreeningApiRequest.criteria` 有默认（`api/app.py:303`），`OpenAlphaSDK.screen` 必填（`sdk.py:227`）。**按本仓库自己的规则，该改的是 REST 一侧而不是 SDK**——`ShortlistRunApiRequest` 的 docstring 已经把这条写下来了：「a browser that omitted `minimum_researched_ratio` would otherwise get a bar nobody chose」，同一句话逐字适用于一次没人选过标准的筛选。**`V2-P5-013` 不做这个改动并说明理由**：删掉默认值会让一个此前被接受的请求体开始返回 `422`，那是对一条出货路由的破坏性改动，而消费这些路由的 `web/` 此刻由另一个并行 agent 持有；这个决定应当与前端一起做，不能单方面做 | `api/app.py:303`；`sdk.py:227`；`backtest/portfolio.py:100` | `V2-P5-013`（前半条关闭为「非缺陷」，后半条转出）|
| F34 | 已实现的 provider 零产品面 | `providers/chainlin.py` `provider_id="chainlin.api"` 未在 `providers/__init__.py` 导出，REST/CLI/SDK/Web 均不可达 | `V2-P0A-004` |
| F35 | CLI 是最弱面：20 个能力域只覆盖 4 个；13 项 REST+SDK 都有的能力零 CLI 命令 | `cli.py` 仅 `version`/`doctor`/`serve` + `evidence build`/`research run`/`replay run` | `V2-P5-013` |
| F36 | `doctor` 不调用任何服务，与 `/health` 无对等；`/health` 与 `SDK.health()` 只返回 `{status, version}` | `cli.py:56-88` 只查 Python 版本与时区 | `V2-P0A-004` |

### 2.3 前端

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F37 | 无路由库，1 个页面，前端只消费 28 条路由中的 6 条（21%） | `web/package.json` 依赖只有 `react`/`react-dom`；grep `Router\|useNavigate\|createBrowserRouter` 零命中；`web/src/api/client.ts` 6 个函数 | `V2-P5-014` |
| F38 | 未被消费的 22 条路由 | market/events、themes、deliberate、screen、3×watchlist、3×reports、6×batch、memory、recovery、portfolio execute/ledger、backtests/portfolio、event-study | `V2-P5-015`…`018` |
| F39 | `web/src/types.ts` 是手工维护的 Python 契约镜像，**已经漂移**，且无漂移测试 | `ResearchResult.signal` 缺 `horizon`（`:29-37`）；`manifest` 缺 `mode`（`:44-47`）；无从 `docs/api/schemas/*.json` 或 `/openapi.json` 生成 | `V2-P0B-016` |
| F40 | 前端硬编码 provenance 占位值 | `web/src/api/client.ts:54` `mode:"live"`、`:58` `code_commit:"web-development"`、`:59` `config_digest:"0".repeat(64)`、`random_seed:7` | `V2-P0B-009` |

---

## 3. 测试缝（Test）

### 3.1 无共享测试基础设施 🔴

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F41 | **全库无 `conftest.py`**，无共享 fixture 模块，无任何 `pytest.fixture` 定义 | `find . -name conftest.py -not -path ./.venv/*` → 空 | `V2-P0B-013` |
| F42 | builder helper 各文件重复 5–8 次，且形态不一致 | `timeline()`/`snapshot()`/`evidence()`（4 处）/`bar()`（3 处，两种风格）/`metadata()`（3 处）/`request()`（3 处）；`FakeTransport` 有**三个互不兼容**的实现（`test_openai_compatible.py:12` `post_json`、`test_chainlin_provider.py:15` `get_json`、`test_tushare_provider.py:10` `post`） | `V2-P0B-013` |
| F43 | 确定性时间靠 21 处模块级常量手工重复，两个不同小时（10:00/10:30）并存 | `NOW = datetime(2026,7,24,10,30,tzinfo=UTC)` 等；`clock=lambda: NOW` 在 26 处调用点 | `V2-P0B-013` |
| F44 | **`create_app()` 没有时钟缝**，12 个 REST 测试全部跑真实墙钟 | `api/app.py:238-243` 只接受 `runtime_dir`/`web_dir`/`max_request_bytes`，`:262`/`:268`/`:274` 硬编码 `datetime.now(UTC)` | `V2-P0B-008` |
| F45 | 无 golden ID / content-hash 断言（只有 `startswith("ev_"/"sig_"/…)`）⇒ 契约变更引发的身份漂移无人察觉 | 全测试库 | `V2-P4-001` |

### 3.2 PIT 红队所需基础设施不存在 🔴

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F46 | 现有 look-ahead 检测靠**字符串匹配异常消息**，并把违规吞成计数器而不是失败 | `backtest/replay.py:142-146` 匹配 `"look-ahead"`/`"not visible"` | `V2-P0B-014` |
| F47 | 唯一的负例是手写 `try/except/else`（非 `pytest.raises`），只覆盖 1 个向量 | `tests/integration/test_research_cycle.py:105-131` | `V2-P2-009` |
| F48 | PRD 列的 5 个注入向量全部无 harness，无参数化注入表 | 未来披露 / `f_ann_date>ann_date` / 未来成分 / 未来行业变更 / 重叠标签 | `V2-P2-001`…`008` |
| F49 | 交易日历只有"周一到周五"近似，无节假日、无半日 | `scripts/generate_replay_corpus.py:19-26 trading_days()`；`tests/fixtures/replay/a-share-v1-corpus.json` 60 个扁平日期 | `V2-P1-004` |
| F50 | 唯一的公司行动相邻标记在所有现有测试中恒为 `False` | `MarketBar.suspended`/`.is_st`（`test_portfolio.py:24-25`、`test_execution.py:21-22`） | `V2-P2-007` |
| F51 | 无已知信噪比合成数据集、无 IC 诊断 fixture、无 purge/embargo 测试数据 | `scripts/generate_replay_corpus.py:29-46 facts()` 是确定性算术，无噪声模型、无已知 IC | `V2-P4-022` |

### 3.3 CI 硬约束（会直接挡住 v2 的两处）🔴

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F52 | **`.parquet`/`.duckdb`/`.sqlite3`/`.db` 是发布拦截后缀 ⇒ 任何签入的面板 fixture 都会让 `security` job 失败**，面板 fixture 必须运行时生成 | `scripts/verify_publication.py:14-28 BLOCKED_SUFFIXES` | `V2-P1-014` |
| F53 | `features.csv` 点名全部 34 个 Python 测试文件 + 2 个 web 测试文件 ⇒ **任何测试树重组都是三制品同步变更**，否则 `security` 与 `python` 两个 job 同时红 | `artifacts/openalpha-v1-feature-coverage/features.csv`；`build_feature_coverage.py --check` 要求 `summary.json` 与 ledger md 逐字节一致（`:155-159`） | `V2-P5-023` |
| F54 | `test_schema_export.py:19` 断言每个 `schema_version.const` **endswith `/v1`** ⇒ 升到 `/v2` 按设计必然失败 | 同上 | `V2-P4-001` |
| F55 | `test_repository_assets.py` 断言 `quality.yml`/`Dockerfile`/`pnpm-workspace.yaml` 内的字面串，并断言 10 张 SVG **不得**包含 `"Tushare"`/`"AKShare"`/`"规划目标"` ⇒ 改 CI 会让单元测试红 | `:206-223`、`:52-155` | `V2-P0A-009` |
| F56 | `pyproject.toml` 无 `fail_under`，80% 门只存在于 CI 命令行；本地 `pytest` 完全不收覆盖率 | `[tool.coverage.report]` 无 `fail_under`；`addopts` 无 `--cov` | `V2-P0A-009` |
| F57 | 前端零覆盖率配置 | `web/vite.config.ts` 的 `test` 块无 `coverage` 键；`pnpm test` 是裸 `vitest run` | `V2-P5-020` |

### 3.4 台账校验的真实覆盖面 🔴

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F58 | `#symbol` 片段在 `:36` 被丢弃 ⇒ **77 个源码引用中 73 个带 `#symbol`，无一被解析**，改名或删除符号照样绿 | `scripts/build_feature_coverage.py:33-40 _paths`，检查在 `:55-63` | `V2-P0A-001` |
| F59 | `acceptance_test` 是自由散文，从不匹配 pytest node id、从不执行 | 同上 | `V2-P0A-003` |
| F60 | `test_evidence` 只查文件存在，从不检查其中有测试、能被 pytest 收集或能通过；38 个路径里有 3 个根本不是测试 | `docs/specs/openalpha-cn-v1-spec.md`、`scripts/verify_compose_recovery.py` | `V2-P0A-003` |
| F61 | 非 `TRUE_COMPLETE` 行完全不查证据；`generated_on` 是硬编码字面量 `"2026-07-24"`；`reconciliation` 块只复查 `_load` 已强制的同义反复（永不为 false） | `:55`、`:76`、`:84-89` | `V2-P0A-001` |

### 3.5 前端测试

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F62 | ~~只有 1 个面板用判别式 `state` 联合，其余 3 个用 ad-hoc `loading`/`error` 布尔；`degraded`/`stale`/`blocked` 在 `web/src` 中**根本不存在**~~ **已关闭**：四个面板现在都取 `PanelState<T>`（`web/src/panelState.ts`），`role="alert"` 由 `web/src/components/PanelNotice/` 一处发出。**本条原文的「判别式 8 态」不准确**：`EvidencePanel.tsx:9` 上的是**五**态，八态从未在 `web/src` 出现过；八个名字出自 PRD 决策 14，且**不是**那五个的超集（`idle`/`error` 不在其中）。落地为**九**态 = PRD 的八个 + `idle`（与 `empty` 是两个不同答案，不并），`error` 更名 `failed`。三个新态均由真实契约字段构造（`risk_decision === "block"`、`look_ahead_violations > 0`、`redistribution !== "allowed"`、`direction === "abstain"`、`risk_flags`、`failures[]`、空 `attribution`、代次/表单不符），而非只在测试里构造得出的装饰态 | 好模式（审计时）：`EvidencePanel.tsx:9`（`"idle"\|"loading"\|"ready"\|"empty"\|"error"`，分支于 `:66`/`:73`/`:74`/`:77`/`:82`）；其余：`DecisionPanel.tsx:6-7`、`ReplayPanel.tsx:7-8`、`AttributionPanel.tsx:8-9`。**行号均为审计时快照，已被 `V2-P5-019` 改写** | `V2-P5-019` |
| F63 | **无任何组件被隔离渲染测试**；4 个面板都实现了 `role="alert"` 分支但 `error` 态从未被渲染过；`App.tsx:33-49` 16 个扁平 `useState`、3 组独立 loading/error，无状态机可 fixture | 唯一前端测试文件是 `web/src/App.test.tsx`（2 个测试，全局 fetch stub）。`vite.config.ts` 的 `test.include` 已能收集同级 `*.test.tsx`，无需改配置 | `V2-P5-020` |
| F64 | Playwright 只有 1 个文件、无 page object；其 `page.route` **不 stub `/api/v1/backtests/validate`** ⇒ e2e 流程从未走到归因 | `web/e2e/golden-flow.spec.ts:28-57`；`playwright.config.ts` 已有 `chromium`+`mobile-chromium`、`forbidOnly`、`trace` 与 webServer | `V2-P5-021` |

---

## 4. 技术缝（Technical）

### 4.1 无迁移机制，且契约设计使升版双向不可读 🔴 —— 最严重的一条

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F65 | **全库没有任何迁移机制**：无 `PRAGMA user_version`、无 `schema_migrations` 表、无 `ALTER TABLE`、无 alembic。grep `migrat\|user_version\|ALTER TABLE\|alembic` 在 `src/` 零命中，唯一命中是 `docs/api/contracts.md:20-22` 的政策散文 | 9 张表全部 `CREATE TABLE IF NOT EXISTS`，建表是构造 store 的副作用（`api/app.py:254-261` 每次 `create_app()` 跑 9 次 DDL） | `V2-P0B-004` |
| F66 | 行是不透明 JSON + `extra="forbid"` + `Literal[".../v1"]` ⇒ **任何新增字段都使旧行对新代码不可读、新行对旧代码不可读**，且没有任何代码路径能区分或升级版本 | `storage/sqlite.py:84` `RunManifest.model_validate_json(row[0])` 遇 `/v2` 直接硬失败 | `V2-P0B-005` |

**这条决定了 P4 的可行性**：PRD Decision 36 要求把三项破坏性契约变更打包一次做掉，但当前没有任何机制执行这次升版。迁移机制必须在 P0.B 建好，否则 P4 无法落地。

### 4.2 持久化层缺陷

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F67 | **`ValidationResult` 不被任何 store 持久化** —— 只由 REST 端点返回 ⇒ 工作台第 4 页（归因看板）没有数据源 | 无 store 写入；仅 `api/app.py:526-539` 返回 | `V2-P0B-010` |
| F68 | 只有 `SQLiteRunRepository` 开 `PRAGMA foreign_keys = ON`，另外 7 个 store 共用同一文件却不开 ⇒ `decisions→runs` 外键在它们的连接上不被强制 | `storage/sqlite.py:25` vs `memory.py:17`、`batch.py:18`、`portfolio.py:17`、`product.py:21`/`:60`、`recovery.py:69` | `V2-P0B-015` |
| F69 | 三处缺索引，导致全表扫描 | `checkpoints(run_id)`（`sqlite.py:134` 全扫）、`portfolio_transitions(subject)`（`portfolio.py:71`）、`research_reports(subject)`（`product.py:110`） | `V2-P0B-015` |
| F70 | `mode` 埋在 `runs.payload` TEXT 里，无列无索引 ⇒ "列出所有 paper 运行"要全表扫 + 逐行 JSON 解析 | `storage/sqlite.py:31-33` | `V2-P4-002` |

### 4.3 规模墙（架构性，非参数性）

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F71 | 每次 `append()` 一个 Parquet 文件，**扁平单目录、无 Hive 分区**；行级 `executemany` 绑定而非向量化摄入 | `parquet.py:34-35`、`:62-65`、`:66-69` | `V2-P1-001` |
| F72 | **零分区裁剪**：每次查询 `sorted(root.glob("*.parquet"))` 全目录列举 + Python 排序，然后把整个文件列表绑进 `read_parquet(?)`；`(? IS NULL OR subject = ?)` 的空值守卫模式还破坏谓词下推；`fetchall()` 无 LIMIT 无分页 | `parquet.py:81`、`:103`、`:105-106`、`:107`、`:110` | `V2-P1-001` |
| F73 | 每次查询新建一个 `":memory:"` DuckDB 连接，无持久 catalog、无缓存统计信息；HTTP 单页渲染最多调 3 次，其中 2 次全量查完在 Python 里过滤 | `parquet.py:85`（还有 `:41`、`providers/file.py:96`）；`api/app.py:314`/`:323`/`:338`，过滤在 `:325-330`/`:340` | `V2-P1-001` |
| F74 | **每行读取代价：1 次 JSON 解析 + 3 次 canonical JSON 序列化 + 3 次 SHA-256 + 1 次深度 freeze**（`computed_field` 不缓存，每次访问重算） | `parquet.py:111`→`:140-162`；`evidence.py:41-45`、`:51-70` | `V2-P1-001` |
| F75 | 5000 标的 × 10 年具体墙：文件数最坏 1.22×10⁷ 个于单目录（超 ext4/APFS 实用上限，且每次查询前先多秒 `readdir`）；单标的单日查询需打开全部 footer（乐观 100µs/文件 ⇒ **约 20 分钟/查询**）；全面板读约 3.6×10⁷ 次序列化 + 同量 SHA-256 于单线程 Python（**约 10 分钟 CPU**）且 `fetchall()` materialize 前先 OOM；横截面切片因值埋在 `payload_json` 字符串里，每次都要全量扫描 + 逐行 JSON 解析 | 综合上述 | `V2-P1-001`、`V2-P1-002` |
| F76 | 每次研究运行写库 = **N+5**，连接数 = **N+11**（每个 store 方法自开自关，无连接池、无跨 store 事务）；且**写放大是 O(N²)**：`:274-282` 每个 agent 后保存**累积** `completed_results`，`_updated_recovery` 每次全量 dump+revalidate ⇒ 12 个 agent = 78 次 `AgentResult` 序列化+校验 + 78 次 signal-ID 哈希 | `engine.py:93`–`:183` 全链；`:285-292`；`sqlite.py:23-26` 等 7 处 | `V2-P4-020` |
| F77 | 批量把上述乘开：`ThreadPoolExecutor` 最多 32 并发 × 最多 1000 项全部砸同一个 `state.sqlite3`（`timeout=10`），WAL 单写者 ⇒ 高负载下 `database is locked`；且跑在 FastAPI `BackgroundTasks` 里即 API 进程内 | `runtime/batch.py:106`；`api/app.py:111-112`、`:432`、`:469` | `V2-P4-019`、`V2-P5-010` |

### 4.4 依赖与容器

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F78 | numpy/pandas 已在 mypy override 里的原因：`akshare`→`pandas`→`numpy` 传递依赖，且 CI 跑 `--all-extras` ⇒ **CI 环境物理存在 numpy/pandas 而项目未声明**；本地 `.venv` 里没有 | `pyproject.toml:71-85`、`:72-73` 注释；`uv.lock:42`、`:1059-1060`；`quality.yml:38` | `V2-P0A-007` |
| F79 | `sklearn`/`lightgbm` **不在** override 块 ⇒ strict 模式下首个 import 即 `mypy src` 失败 | `pyproject.toml:74-83` | `V2-P0A-007` |
| F80 | `follow_imports="skip"` 使 numpy/pandas 全部符号退化为 `Any`，配合 strict 的 `warn_return_any` ⇒ **任何返回 pandas/numpy 表达式的函数都报错**。因子层（横截面回归→系数、rank 相关→float IC）正是这个形状，几乎每个公开因子函数都要显式 `float(...)`/`cast(...)` 收口 | 现有先例：`parquet.py:111`、`:142-158` 共 11 处 `cast()` | `V2-P0A-007` |
| F81 | strict 还开 `disallow_any_generics`（裸 `np.ndarray` 报错）与 `warn_unused_ignores`（若移除 `follow_imports="skip"` 换取真 stub，现有 7 处 `# type: ignore` 全部变成错误） | 现有 ignore：`signal.py:49`、`decision.py:56`、`evidence.py:51`/`:57`、`validation.py:54`/`:60`、`storage/batch.py:96` | `V2-P0A-007` |
| F82 | ruff 规则集对数值代码零覆盖：无 `NPY`、无 `PD`、无 `S`（`pickle`/`joblib.load` 模型加载正相关）⇒ 最高风险的新代码面零 lint | `pyproject.toml:63-64` | `V2-P0A-007` |
| F83 | **`lightgbm` 的 manylinux wheel 动态链接 `libgomp.so.1`，而 `python:3.12-slim` 不含** ⇒ `import lightgbm` 抛 `OSError`。需在**运行时** stage 加 `apt-get install -y libgomp1` | `Dockerfile:12`、`:22`、`:20` | `V2-P4-015` |
| F84 | `read_only: true` + 仅 `tmpfs /tmp:size=64m`、无 `/dev/shm`、无 `shm_size` ⇒ sklearn/joblib 并行后端 memmap 溢写必失败；DuckDB 持久模式的 WAL 与 `temp_directory` 也无处可写 | `deploy/compose.yml:11`、`:21`、`:19` | `V2-P4-015`、`V2-P1-001` |
| F85 | **未固定 `OMP_NUM_THREADS`/`OPENBLAS_NUM_THREADS`** ⇒ BLAS/OpenMP 的浮点归约顺序随线程数变化，对一个以内容寻址确定性为核心卖点的系统是直接的可复现性危害 | `Dockerfile:24-31` | `V2-P0A-007`、`V2-P0B-009` |
| F86 | mypy 假设 3.11 而容器跑 3.12 ⇒ numpy 解析到不同版本（2.4.6 vs 2.5.1）；Windows 矩阵腿是 lightgbm/scipy wheel 可用性与 BLAS 线程差异最大处，无 wheel 会退化为源码构建并在 CI 失败 | `pyproject.toml:67` vs `Dockerfile:12`；`quality.yml:23-24`；`pyproject.toml:17` 宣称支持 Windows | `V2-P0A-007` |

### 4.5 可复现性声明目前部分是空的 🔴

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F87 | **`random_seed` 被记录但从未被读取**。全库唯一实际播种是 `event_study.py:71`，用的是另一个字段。没有任何地方播种 `numpy.random`、`PYTHONHASHSEED`、sklearn `random_state`、lightgbm `seed`/`deterministic`/`num_threads` ⇒ v2 ML 训练的 manifest 会声称它并不具备的可复现性 | `engine.py:42`→`:136` 之后再无读取 | `V2-P0B-009` |
| F88 | **`code_commit` 从不从 git 取**，真实值是字面量 `"development"` / `"web-development"`；而它是 `decision_id` 的输入 ⇒ 不同代码产生相同 decision id。**（F89 的同类主张对 `config_digest`/`random_seed` 不成立，见 PRD §1.3 B6 更正）** | `cli.py:131`、`:160`；`web/src/api/client.ts:58`。`src/` 内无 `git rev-parse`、无 `subprocess` | `V2-P0B-009` |
| F89 | **`config_digest` 从不计算**，CLI 默认 `"0"*64`、Web `"0".repeat(64)`；同样是 `decision_id` 输入 | `cli.py:132`、`:161`；`client.ts:59` | `V2-P0B-009` |
| F90 | `DecisionLedger.created_at` 在 `decision_id` 内，而 `engine.py:105` 只在 run 行已存在时复用 `started_at` ⇒ **首次运行无法仅凭输入复现** | `decision.py:31`；`engine.py:105`、`:188`/`:251`/`:266`/`:280` 用墙钟 | `V2-P0B-009` |
| F91 | `engine.py:327-328` 按迭代顺序求 `sum(...)/len(...)` ⇒ 路由顺序变化改变浮点位型 → 改变 `strength` → 改变 `signal_id`；引入 BLAS 归约后这条链变成真正不确定 | `engine.py:327-328`；`router.py:23`；`validation.py:50` `abs_tol=1e-9` 同样对顺序敏感 | `V2-P0B-009`、`V2-P4-001` |
| F92 | `storage/parquet.py:116-122` 内联复制了 canonical JSON 的四个选项而不调用 `canonical_json_bytes` ⇒ 漂移风险 | 对比 `domain/json_value.py:29-37` | `V2-P0B-011` |
| F93 | 两套并存的身份算法；且 24 hex（96 bit）截断 ID 同时是 `PRIMARY KEY`/`UNIQUE` ⇒ 碰撞表现为 `DuplicateRecordError` 而非静默损坏 | `domain/_identity.py:9-19`（`sig_`/`dec_`/`val_`）vs `evidence.py:57-70`（`ev_`，手写）；`sqlite.py:39`、`memory.py:22`、`product.py:65` | 记录，不修 |

### 4.6 配置、调度与安全

| # | Finding | 证据 | 关闭 issue |
|---|---|---|---|
| F94 | **无配置对象、无进程内 `.env` 加载**（无 dotenv / pydantic-settings，grep 零命中）⇒ `.env` 只对 Compose 生效，`openalpha serve`、SDK、pytest 全部无视它 | 全库仅 3 处 `os.getenv` 读配置：`api/app.py:245`、`:247`、`:542-543` | `V2-P0B-006` |
| F95 | `.env.example` 声明 12 个变量，只有 3 个被代码读；`OPENALPHA_LOG_LEVEL`/`HOST`/`PORT` 是**死变量**；被实际读取的 `OPENALPHA_WEB_DIR` 反而未声明 | `.env.example` vs 上条 | `V2-P0B-006` |
| F96 | `int(os.getenv("OPENALPHA_MAX_REQUEST_BYTES", ...))` 无守卫 ⇒ 非数字值在 import 期抛 `ValueError`，进程带裸 traceback 崩溃（`app = create_app()` 在 `:557` 执行） | `api/app.py:247`、`:251-252`（只校验 `<1`） | `V2-P0B-006` |
| F97 | host/port 三条互不一致的硬编码路径：`cli.py:174-180` 无 env 回退（两个 env 变量对 `openalpha serve` 完全无效）；`Dockerfile:26-28` 设了 ENV 但 `:47` CMD 又硬编码；`compose.yml:13` 只有宿主侧可配 | 同上 | `V2-P0B-006` |
| F98 | ~~**无任何调度原语**（grep `cron\|scheduler\|apscheduler\|celery` 全库零命中）。`daily` 模式需要：带 next-fire-time 与 lease/lock 列的持久作业表、按交易日的幂等键、错过窗口的 catch-up/skip 政策、市场日历依赖、崩溃恢复 —— **全部不存在，且没有迁移机制去加这些表**~~ **原语已关闭，调用方未关闭**：`job_contracts.py` + `storage/jobs.py` + `scheduler.py` 给出六件中的六件（持久表、next-fire-time、lease/lock、按交易日幂等键即主键、catch-up 政策、日历依赖与崩溃恢复），零新增运行时依赖。**「且没有迁移机制去加这些表」这半句本身也被实测证伪、但结论反而更强**：迁移机制存在，可它在**全新库上根本走不到** —— 迁移 3 抛 `MigrationNotYetApplicable`、`run_migrations` 就此 break，4 到 8 从不执行；故正确做法本就不是加迁移，而是本包既有的 `CREATE TABLE IF NOT EXISTS`。~~**仍未关闭的是调用方**：无 CLI、无 REST、不在 `build_storage` 里~~ **调用方已由 `V2-P5-013` 关闭**：`openalpha jobs register|list|due|run`、`GET /api/v1/jobs`／`/{job_id}`（只读，因为全库仍零认证）、`build_storage` 第十三个 store。作业体只有一种——每个欠下的会话在**它自己的发布时刻**跑一次点位健康报告——理由是实测的：其它按会话的动作都要 8–20 个声明参数，`scheduled_jobs` 没有列装得下，加列即改已落盘契约。**这个面立刻挖出一条从建成之日就在的死路**：`finish_session` 的 docstring 点名的 `retry_session` **不存在**，失败的会话被 `due()` 永远欠着又被主键永远拒绝，该会话之后的每一个会话也再到不了；已补上（原地重开**终态**的 run，绝不重开 `running` 的，也绝不删了再插——那会在重试期间腾空主键），并由 `--retry-failed` 显式声明而非自动重试 | 唯一异步是 `BackgroundTasks` + `runtime/batch.py:106` ThreadPoolExecutor，进程内、重启即失（除 `storage/batch.py:120-141` 的一次性 `recover_interrupted` 扫描） | `V2-P5-010` |
| F99 | **`src/` 内零 logging 配置** ⇒ 无日志的调度器不可运维 | grep `logging` 无配置 | `V2-P0B-007` |
| F100 | ~~请求体大小检查**只看 `content-length` 头** ⇒ chunked 请求完全绕过，且流式过程中从不计数~~ **已关闭**：未声明长度的正文现在逐片计数、越顶即停止调用 `receive`。**实测比原文更糟**：1,024 字节上限下一条 36,000,030 字节的 chunked 正文被读完并由 JSON 解析器答 `422`，`tracemalloc` 峰值 108,346,472 字节（正文的三倍）；修后经 `httpx2.ASGITransport` 实测 400 片只拉 1 片 | 坐标未漂移 | `V2-P5-012` |
| F101 | ~~CORS 硬编码只允许 `GET`/`POST` ⇒ v2 REST 面若用 `PUT`/`DELETE`/`PATCH` 会在 CORS 层被挡~~ **已关闭**：`CORS_ALLOWED_METHODS` 覆盖 `DELETE/GET/HEAD/PATCH/POST/PUT`。**本条实测应当写得更重 —— 清单已经落后于路由表，不是「v2 才会」**：预检 `HEAD` 在 `c847295` 上就是 `400 Disallowed CORS method`，而应用当时已声明四条 `HEAD` 路由。「全库无任何认证授权」**仍然成立、未关闭**，且正是不从路由表推导方法的理由：CORS 不是授权 | `api/app.py:281-287` | `V2-P5-011` |
| F102 | ~~缺 `Strict-Transport-Security`、`cross-origin-embedder-policy`、`cross-origin-resource-policy`；header 是 append 而非 replace；`--no-server-header` 只在 `Dockerfile:47` 传，`cli.py:180` 不传 ⇒ `openalpha serve` 泄漏 `server:` 头~~ **已关闭**：三个头补齐（HSTS 不带 `preload`，那是运营者对域的承诺）；改为按名替换 —— 追加与替换在出货路由表上**不可分辨**（没有路由自设策略头），故测试在真 `create_app()` 上加一条自设 `x-frame-options: SAMEORIGIN` 的路由把两个答案分开，旧行为实测产生两行原始头；`cli.serve` 传 `server_header=False` | 坐标未漂移 | `V2-P5-012` |
| F103 | 凭据读取零校验：无脱敏 helper、无存在性检查、`doctor` 不验证凭据 ⇒ 定时摄入会在凌晨两点以空 token 的 HTTP 错误失败，而不是拒绝启动 | `providers/tushare.py:80`、`chainlin.py:141`、`models/openai_compatible.py:157`；`cli.py:56-88` | `V2-P0A-004`、`V2-P0B-006` |

---

## 5. 覆盖矩阵一：四缝 → issue（缺口证明）

每条 finding 都必须被至少一个 issue 关闭。下表按 issue 汇总。

| Issue | 关闭的 finding | 数量 |
|---|---|---:|
| `V2-P0A-001` | F58, F61 | 2 |
| `V2-P0A-003` | F59, F60 | 2 |
| `V2-P0A-004` | F34, F36, F103 | 3 |
| `V2-P0A-005` | F4, F14 | 2 |
| `V2-P0A-007` | F14, F78, F79, F80, F81, F82, F85, F86 | 8 |
| `V2-P0A-009` | F55, F56 | 2 |
| `V2-P0B-001` | F3, F7 | 2 |
| `V2-P0B-002` | F5, F6, F32 | 3 |
| `V2-P0B-003` | F8, F9, F24 | 3 |
| `V2-P0B-004` | F65 | 1 |
| `V2-P0B-005` | F66 | 1 |
| `V2-P0B-006` | F94, F95, F96, F97, F103 | 5 |
| `V2-P0B-007` | F99 | 1 |
| `V2-P0B-008` | F44 | 1 |
| `V2-P0B-009` | F40, F85, F87, F88, F89, F90, F91 | 7 |
| `V2-P0B-010` | F67 | 1 |
| `V2-P0B-011` | F10, F11, F12, F13, F26, F92 | 6 |
| `V2-P0B-012` | F1, F2, F3 | 3 |
| `V2-P0B-013` | F41, F42, F43 | 3 |
| `V2-P0B-014` | F46 | 1 |
| `V2-P0B-015` | F68, F69 | 2 |
| `V2-P0B-016` | F39 | 1 |
| `V2-P1-001` | F16, F71, F72, F73, F74, F75, F84 | 7 |
| `V2-P1-002` | F15, F75 | 2 |
| `V2-P1-004` | F16, F49 | 2 |
| `V2-P1-014` | F52 | 1 |
| `V2-P2-001`…`008` | F48 | 1 |
| `V2-P2-007` | F50 | 1 |
| `V2-P2-009` | F47 | 1 |
| `V2-P4-001` | F45, F54, F91 | 3 |
| `V2-P4-002` | F70 | 1 |
| `V2-P4-005` | F28 | 1 |
| `V2-P4-006` | F26 | 1 |
| `V2-P4-008` | F19 | 1 |
| `V2-P4-009` | F20, F25 | 2 |
| `V2-P4-010` | F18 | 1 |
| `V2-P4-011` | F17 | 1 |
| `V2-P4-015` | F83, F84 | 2 |
| `V2-P4-019` | F23, F77 | 2 |
| `V2-P4-020` | F76 | 1 |
| `V2-P4-022` | F51 | 1 |
| `V2-P5-002` | F21 | 1 |
| `V2-P5-003` | F22 | 1 |
| `V2-P5-010` | F28, F77, F98 | 3 |
| `V2-P5-011` | F101 | 1 |
| `V2-P5-012` | F100, F102 | 2 |
| `V2-P5-013` | F29, F30, F31, F33, F35 | 5 |
| `V2-P5-014` | F37 | 1 |
| `V2-P5-015`…`018` | F38 | 1 |
| `V2-P5-019` | F62 | 1 |
| `V2-P5-020` | F57, F63 | 2 |
| `V2-P5-021` | F64 | 1 |
| `V2-P5-023` | F53 | 1 |
| P1–P5 全部 | F27 | 1 |
| 记录不修 | F93（96-bit 截断 ID 碰撞表现为显式错误，非静默损坏；成本高于收益） | 1 |

**未关闭的 finding：0**（F93 为显式接受的已知限制，已记录理由）。

---

## 6. 审计对排期的影响

审计前路线图假设 P0 是 1 周。审计发现 **39 条前置 finding**，分布在 10 个标 🔴 的小节：§1.1（F1–F4）、§1.2（F5–F6）、§1.3（F7）、§1.4（F8–F9）、§3.1（F41–F45）、§3.2（F46–F51）、§3.3（F52–F57）、§3.4（F58–F61）、§4.1（F65–F66）、§4.5（F87–F93）。它们必须在面板层之前关闭，否则：

- 没有迁移机制（F65/F66）⇒ P4 的破坏性契约变更无法落地；
- 两个组装根（F5）⇒ 每个新层要手工同步两处；
- 引擎四合一（F7）⇒ 新增横截面入口会连带失效四个下游；
- 无 conftest（F41）⇒ 面板/因子/模型测试会把 builder 再重复十几次；
- look-ahead 靠字符串匹配（F46）⇒ P2 红队闸门没有可靠的失败信号；
- `.parquet` 被发布拦截（F52）⇒ 面板 fixture 若按常规签入会直接红 CI；
- seed/commit/digest 全是占位（F87–F89）⇒ 模型层的可复现声明会是空的。

因此 P0 拆为 **P0.A 治理与探测** 与 **P0.B 结构地基与迁移机制**，后者是新增的约 3–4 周工作。总工期由 18–25 周上调为 **28–36 周**（单人全职）。详见 `openalpha-cn-v2-roadmap.md`。
