# OpenAlpha CN v2 智能研究与选股平台改造 PRD

Status: Approved for implementation planning — 范围已按实测基线与使用者约束修正
Revision: v2.1（取代 `openalpha-cn-v2-research-platform-prd.md` 的 Proposed 版）
Baseline: `main` @ `8d13065`，实测于 2026-07-29
Compatibility baseline: OpenAlpha CN v1 contracts and local-first runtime
配套文档: `openalpha-cn-v2-roadmap.md`（开发路线图与闸门）

## 使用者画像与约束

本 PRD 的范围由以下已确认约束决定，任何范围讨论都应回到这三条：

1. **使用者是个人量化研究者**，非团队、非多租户、非对外服务。
2. **数据来源是付费 Tushare Pro**（具体积分档位待 P0 能力探测确认）；**无 L1/L2/逐笔 tick 数据**。
3. **终端定位是研究终端**，不是实时行情终端。实时行情、盘口、逐笔、分时推送整体不在范围内。

Proposed 版按"可分发的开源研究平台"撰写。本版按"个人研究者自用、可选择性开源"重写范围，工程纪律不打折，平台化开销显著削减。

---

## 1. 实测基线（本版所有范围决策的事实依据）

### 1.1 规模与健康度

| 项 | 实测值 | 验证方式 |
|---|---|---|
| 后端 | 60 文件 / 6,523 行 | `find src -name '*.py'` |
| 测试 | **105 passed in 3.7s** | `uv run pytest -q` |
| 后端覆盖率 | **87%**（branch 已开启，高于 80% 门槛） | `uv run pytest --cov` |
| 前端 | 14 文件 / 997 行 / 5 组件 / **单页无路由** | `find web/src` |
| 前端运行时依赖 | **仅 `react` + `react-dom`** | `web/package.json` |
| 后端依赖 | `duckdb` `fastapi` `pydantic` `pytz` `typer` `tzdata` `uvicorn` | `pyproject.toml` |
| REST 路由 | 28 条 `/api/v1/*` + `/health` | `api/app.py` |

### 1.2 确认可复用的 v1 资产（不重建）

- **四时钟 PIT 契约**：`domain/time.py#Timeline` 强制 tz-aware，校验 `ingested_time`/`revision_time` 不早于 `available_time`；可见性统一走 `is_visible_at`。
- **内容寻址身份**：`EvidenceSnapshot.content_hash`/`evidence_id`、`SignalFrame.signal_id`、`DecisionLedger.decision_id`、`ProviderBatch.payload_digest` 全部由 canonical JSON 派生。
- **失败不能伪装成空**：`ProviderBatch.validate_result` 强制 `success` 有记录、`no_data` 有原因，并拒绝 `as_of` 时点不可见的记录。
- **恢复与幂等**：`ResearchEngine._load_or_start_recovery` 用 `request_digest` + `graph_signature` 双签名拒绝 run_id 冲突复用，agent 级 checkpoint 续跑。
- **A 股执行约束**：`backtest/execution.py` 用 `Decimal` 实现整手、T+1、涨跌停、停牌、佣金/过户费/印花税，分板块（main/star/growth/bse）。
- **契约纪律**：全域 `extra="forbid"` + `frozen=True` pydantic v2，mypy `strict`，ruff 多规则集。

### 1.3 本次扫描新发现的三项阻塞（Proposed 版未涉及）

**B1 — 付费 Tushare 数据当前无法进入证据层。**
`TushareProvider` 只产出 `kind="daily"`，而 `evidence/builder.py:55-63` 的 `_NORMALIZERS` 未注册 `daily`，`_build_one` 会抛 `unsupported evidence kind: daily`。唯一的付费数据源今天只能产出 `ProviderBatch`，进不了 `run_cycle`。

**修法不是补 normalizer** —— 该缺口暴露的是架构事实：价量与财务是面板数据（标的 × 日期 × 字段），塞进"一行一 `EvidenceSnapshot`、每行重算 sha256"的模型，量级上就是错的（单价量表 5000 × 2000 ≈ 10⁷ 条证据）。见 §3.1 的双数据平面决策。

**B2 — 证据存储无法承载因子规模。**
`storage/parquet.py` 每批写一个 `part-<hash>.parquet`（日频摄入产生海量小文件）；`query()` 用 `root.glob("*.parquet")` 全量扫描且每次新建 `duckdb.connect(":memory:")`；`_deserialize()` 对每一行重建完整 pydantic 模型并重算 sha256。对离散事件证据这是**可验证性优点**，对因子观测（5000 × 200 × 2000 ≈ 2×10⁹ 行）是硬墙。

**B3 — 能力台账校验只查文件不查符号。**
`scripts/build_feature_coverage.py` 的 `_paths()` 在 `#` 处截断路径后只做 `path.exists()`，从不校验符号。因此 v1 台账中 7 处不存在的符号一直通过校验：`MarketEventAgent`（实际 `MarketAgent`）、`ThemeCatalystAgent`（`ThemeAgent`）、`CapitalFlowAgent`（`CapitalAgent`）、`StructuredModelAgent`（`StructuredSignalAgent`）、`AShareExecutionModel`（`AShareExecutionPolicy`）、`AShareCostModel`（`CostSchedule`）、`AkShareProvider`（`AKShareProvider`）。

功能本身存在（非虚报能力），但台账是手工维护、未被机器校验的。Implementation Decision 29 要把台账当作 v2 闸门，而闸门当前会放过不存在的引用。

### 1.4 与 Proposed 版假设的其余差距

| Proposed 版假设 | 实测现状 |
|---|---|
| "四时钟 PIT 一致的研究底座" | 契约层成立；**数据层只有 `daily` 一类行情 + 8 类事件**，PIT 基础设施为零（全 `src/` grep `calendar\|universe\|industry\|adj_factor\|corporate_action\|balance_sheet\|fundamental\|delist\|suspend` 除字符串 `limit_up` 外零命中） |
| "多智能体研究" | 3 个确定性 agent，打分为硬编码常量（`limit_up=0.65`、`consecutive_board=0.85`、资金流仅按正负给 ±0.4） |
| "统一 `run_cycle`" | 成立，但**单标的**：`ResearchRunRequest.subject: str`，证据由调用方传入，引擎不取数；agent 循环内逐次写 SQLite recovery |
| "组合模拟" | `PortfolioSimulator` 支持多持仓 + FIFO；但 `multi_day.py#PortfolioBacktestStep` 是**一步一单一标的一根 bar** |
| "统计检验" | 仅事件研究（`event_study.py` 用标准库 `statistics` + `random.Random(seed)`，确定性可复现） |
| "归因" | **占位实现**：`backtest/validation.py:88-90` 按 rule 20% / factor 30% / agent 50% 硬编码比例切分，构造上必然对账，不是归因 |
| 数值能力 | **无 numpy / pandas / scipy / scikit-learn / polars** |

---

## 2. Problem Statement

v1 建立了 A 股原生、证据可追溯、PIT 一致的研究**契约底座**：四时钟 `EvidenceSnapshot`、统一 `ResearchEngine.run_cycle`、结构化 `SignalFrame`、`DecisionLedger`、`RunManifest`、风险门、组合模拟、回放、事件统计检验、批量任务与轻量工作台。

对希望把它作为个人日常 A 股研究与选股环境的使用者，当前存在五个缺口（前四条承自 Proposed 版，第五条为本次实测新增）：

1. 安装后缺少从 Provider 配置、数据体检、股票池建立到每日研究运行的完整路径，使用者必须自行拼装数据与工作流；
2. 内置 Market、Theme、Capital Agent 是可复现的确定性基线，不是覆盖基本面、估值、质量、成长、动量、流动性、事件与市场状态的 Alpha 研究体系；
3. 现有 `ModelProvider` 只治理结构化 LLM 调用，缺少面向量化预测模型的特征、训练、模型注册、Walk-forward 样本外评估与预测落库能力；
4. 当前 Web 工作台能展示核心证据与决策闭环，但不足以承担选股、因子分析、模型评估、组合分析、数据质量与报告工作流；
5. **付费数据无法进入系统**（§1.3 B1），且证据存储的形状无法承载因子规模（B2），能力台账不可作为闸门（B3）。第 1–4 条的任何工作在 B1/B2 解决前都无处落地。

产品不得把工程完成度、历史回测或 LLM 输出包装成"稳定荐股"或保证收益。真正要解决的是：在合法且质量可控的 PIT 数据基础上，稳定地产生带证据、预测周期、置信度、失效条件、容量与风险说明的候选排序，并用严格样本外验证判断研究方法是否具有可重复的增量价值。

对个人研究者而言，这套纪律不是合规要求而是**自我保护**：样本外 IC、扣成本收益、多重检验控制、预测先落库，是唯一能区分"我发现了 alpha"与"我在 2015–2026 数据上过拟合了 20 个因子和一个 GBDT"的手段。没有外部监督时，这些约束更容易被省掉，也更致命。

---

## 3. Solution

将 OpenAlpha CN 从"可验证研究契约底座"升级为"个人可日常使用的 A 股研究与选股环境"，保留 v1 全部核心领域合同与同路径研究原则，在其上增加**两条新架构支柱**与**五个能力域**。

### 3.1 支柱一：双数据平面分离（解决 B1 + B2）

| 平面 | 内容 | 存储 | 消费者 |
|---|---|---|---|
| **A. 面板平面**（新增） | 交易日历、股票基础与更名、复权因子、日频价量、市值估值、停牌、涨跌停、指数成分、行业分类、财务报表与指标 | 按 `dataset/year/` 分区的 Parquet + **持久** DuckDB catalog | 因子层、模型层、组合层 |
| **B. 证据平面**（沿用 v1，不改动） | 离散、可引用的事件：涨停/炸板/连板、公告披露、题材催化、资金流 | 现有 `ParquetEvidenceStore`（内容寻址、逐行完整性校验） | Agent、`run_cycle`、候选复核 |

现有证据存储的"逐行重建 + 重算 hash"在 B 平面是可验证性优点，在 A 平面是灾难。**分离之后不需要重写 `ParquetEvidenceStore`，只需禁止面板数据流入它。** B2 由此消解。

### 3.2 支柱二：两段漏斗（解决单标的引擎与横截面排序的张力）

```
全市场 ~5000 标的
   ↓  面板平面：因子计算 → 横截面打分 → 硬性可交易过滤（纯数值，不进 run_cycle）
初筛 top N（N 可配置，建议 50~200）
   ↓  证据平面：只对这 N 个跑 run_cycle
候选清单：每个候选都有 SignalFrame + DecisionLedger + 证据闭合
```

- 第一段无 per-agent SQLite 写放大，不需要为 5000 标的做引擎性能改造。
- 第二段完全复用现有引擎，`ResearchRunRequest` 的单标的形状不变。
- 全市场吞吐基准从**阻塞项**降级为**标定 N 的参数**。

### 3.3 五个能力域（原六域合并：数据就绪 + 因子 → 各自独立，工作台缩减）

1. **数据就绪层**：Tushare 描述符驱动扩展、能力探测、数据目录、PIT 完整性检查、股票池与交易日历管理、Research/Daily 两档运行档位；
2. **因子研究层**：版本化因子定义、面板 PIT 特征计算、去极值/标准化/中性化分离、IC/Rank IC/衰减/分组收益/换手/容量/相关性；
3. **预测模型层**：独立于 `ModelProvider` 的 `AlphaModel` 合同，Walk-forward、purge/embargo、内容寻址模型制品、预测先落库；
4. **研究与选股层**：扩展 Agent 家族，统一输出可追溯 `SignalFrame`，通过两段漏斗生成 `CandidateRanking`；
5. **组合与验证层**：启发式组合构建、A 股 T+1/整手/停牌/涨跌停/成本、Paper Portfolio、真归因、多重检验控制、分段报告；
6. **研究工作台**：**4 页**（数据体检 / 候选清单含个股详情 / 因子与模型实验室 / 组合与验证）。

平台对每个候选输出方向、预测周期、候选分数、置信度或概率、主要证据、确认条件、失效条件、风险标记、流动性/容量约束、建议研究上限以及相对上次运行的变化。结果明确定义为研究与决策支持：不连接实盘券商、不代替使用者判断、不承诺收益。

---

## 4. 范围修正（相对 Proposed 版）

Proposed 版按可分发开源平台撰写。个人自用场景下，下列能力为纯开销，明确降级或移出。

| Proposed 能力 | 处置 | 理由 |
|---|---|---|
| 首启图形向导（S1–S2） | **降级为 CLI `doctor`** | 单人使用不需要图形引导；`doctor` 已存在，扩展它 |
| Demo 冻结数据集档位（S3、Decision 4） | **降级为 CI fixture，不作为产品档位** | 已有真实付费数据；Demo 的价值只在对外分发 |
| 三档运行档位 | **缩减为 Research + Daily 两档** | Demo 不再是产品档位 |
| 移动端宽度（S82） | **移出 v2** | 桌面研究场景 |
| 批量与调度监控页（S80） | **移出 v2**，CLI + 日志承担 | 单人不需要图形任务监控 |
| Schema 迁移测试（Testing Decision 16、S88） | **降级为一次性备份脚本** | 仓库内不存在可迁移的 v1 持久化卷（只有 `tests/fixtures/replay`）；自用可接受"备份 + 重建" |
| 11 区域工作台（Decision 24） | **砍到 4 页**（15–25k 行 → 5–8k 行） | 最大单项节省 |
| 组合优化器（S53 因子暴露上限、S54 优化部分） | **改启发式，不引入 cvxpy** | 个人规模下贪心足够，但必须在报告中声明为启发式 |
| 模型校准 / 比较矩阵 / 消融 / 漂移监控（S31、S33、S34） | **推迟 v2.1** | 先要有一个可信模型，再谈比较体系 |
| Agent 可靠性按周期×市场状态度量（S39） | **推迟 v2.1** | 需要足够长的样本外历史才有意义 |
| license-aware 导出 / 发布扫描（S72、Decision 27） | **保留最小版**：不导出 Tushare 原始 payload | Tushare 为 `restricted`；自用无风险，但别把原始数据写进可分享报告 |
| 多节点 / 多租户 / Kubernetes | 已在 Proposed 版 Out of Scope | — |

**保留且不打折**：PIT 正确性、fail-closed、内容寻址、Walk-forward 与 purge/embargo、预测先落库、gross/net 并列、多重检验控制、CI 不依赖密钥。砍掉这些等于把项目做成一台昂贵的过拟合机器。

---

## 5. User Stories

保留 Proposed 版全部 90 条编号以维持可追溯性，新增 7 条（S91–S97）。状态含义：

- **IN** — v2 范围内，不打折
- **IN-降级** — 保留意图，交付形态降级（多为 CLI 替代 UI，或最小版）
- **v2.1** — 推迟
- **OUT** — 移出，附理由

### 5.1 安装与运行档位（S1–S5）

| ID | Story | 状态 | 说明 |
|---|---|---|---|
| S1 | Guided setup flow to reach a valid research run | IN-降级 | CLI `openalpha doctor` 承担 |
| S2 | Choose Demo / Research / Daily mode | IN-降级 | 缩减为 Research + Daily |
| S3 | Redistributable frozen dataset + complete golden flow | OUT | 降级为 CI fixture；若决定对外发布需加回 |
| S4 | Connect own PIT historical data | **IN** | 核心 |
| S5 | Scheduled data refresh and research runs | IN-降级 | cron + CLI，无 UI 调度页 |

### 5.2 数据治理与就绪（S6–S14）

| ID | Story | 状态 | 说明 |
|---|---|---|---|
| S6 | Provider declares license/redistribution/freshness/revision/rate-limit/failure | **IN** | `ProviderMetadata` 已有，需补 revision 语义 |
| S7 | Provider capability discovery before ingestion | **IN** | **P0 关键**，直接测出 Tushare 账号实际可取接口 |
| S8 | Data catalog: subjects/fields/date/revision coverage/freshness | **IN** | P1 |
| S9 | PIT integrity checks for four clocks | **IN** | P1 + P2 |
| S10 | Historical universe membership and delisted securities preserved | **IN** | 生存偏差控制，不可省 |
| S11 | Historical industry classifications and benchmark constituents | **IN**（受积分约束可降级） | 若探测不可用，中性化退化为市值中性化并标注 |
| S12 | Corporate actions and adjustment policies versioned | **IN** | 复权因子，不可省 |
| S13 | Missing/stale/duplicated/revised records summarized by dataset | **IN** | P1 |
| S14 | Failed daily datasets block dependent research explicitly | **IN** | fail-closed |

### 5.3 因子研究（S15–S24）

| ID | Story | 状态 | 说明 |
|---|---|---|---|
| S15 | Versioned factor definitions | **IN** | 内容寻址身份 |
| S16 | Value/quality/growth/momentum/reversal/volatility/liquidity/event families | **IN-缩减** | 首批 **15–25 个**，不是 200 个 |
| S17 | Every factor observation tied to PIT inputs and build manifest | **IN** | |
| S18 | Configurable winsorization/standardization/missing-value policy | **IN** | |
| S19 | Industry and market-cap neutralization | **IN**（依赖 S11） | |
| S20 | IC, Rank IC, IC decay and stability reports | **IN** | |
| S21 | Quantile portfolio returns with realistic costs | **IN** | 复用 `AShareExecutionPolicy` |
| S22 | Factor turnover, coverage and capacity reports | **IN** | |
| S23 | Correlation and redundancy analysis | **IN** | |
| S24 | Factor experiments saved as immutable artifacts | **IN** | |

### 5.4 预测模型（S25–S35）

| ID | Story | 状态 | 说明 |
|---|---|---|---|
| S25 | Dedicated quantitative `AlphaModel` contract | **IN** | 与 `ModelProvider` 严格分离 |
| S26 | Reproducible feature matrices with feature and universe versions | **IN** | |
| S27 | Time-ordered Walk-forward training and evaluation | **IN** | 禁止随机切分 |
| S28 | Purging and embargo for overlapping labels | **IN** | |
| S29 | Simple linear and tree-model baselines | **IN** | 线性/排序 + LightGBM |
| S30 | Model artifacts content-addressed with cutoff/features/params/seed/code version | **IN** | |
| S31 | Probability and score calibration reports | v2.1 | |
| S32 | Out-of-sample predictions persisted before outcomes are known | **IN** | **不可省** —— 区分发现与事后重构的唯一手段 |
| S33 | Model comparison and ablation reports | v2.1 | |
| S34 | Feature and prediction drift monitoring | v2.1 | |
| S35 | Failed or stale models abstain explicitly | **IN-最小** | stale 即弃权，不做完整漂移检测 |

### 5.5 Agent 与研究（S36–S42）

| ID | Story | 状态 | 说明 |
|---|---|---|---|
| S36 | Fundamental/valuation/quality/growth/momentum/liquidity/event/regime Agent contracts | **IN-缩减** | 首批 4 类，其余随因子库增长 |
| S37 | Every Agent emits a validated `SignalFrame` | **IN** | 已有 |
| S38 | Evidence-family and feature dependencies declared for routing | **IN** | 需扩展到 feature 依赖 |
| S39 | Agent reliability measured by horizon and market regime | v2.1 | 需足够长样本外历史 |
| S40 | Deterministic / learned / LLM-backed Agents distinguishable in manifests | **IN** | |
| S41 | Bull/Bear and risk committees remain optional | **IN** | `agents/committee.py` 已有 |
| S42 | Explicit abstention when evidence insufficient or contradictory | **IN** | 已有 |

### 5.6 候选排序与筛选（S43–S51）

| ID | Story | 状态 | 说明 |
|---|---|---|---|
| S43 | Ranked candidate list for a defined universe and horizon | **IN** | 两段漏斗产出 |
| S44 | Each candidate shows score/direction/confidence/horizon/rank change | **IN** | |
| S45 | Each candidate shows supporting `evidence_id` references | **IN** | |
| S46 | Confirmation and invalidation conditions | **IN** | `SignalFrame` 已有 |
| S47 | Liquidity, tradability and capacity warnings | **IN** | |
| S48 | Data freshness and completeness badges | **IN** | |
| S49 | Compare current candidate list with prior runs | **IN** | |
| S50 | Screening combines factors/signals/predictions/evidence/risk/tradability | **IN** | 取代现有仅按 confidence 排序的 `ResearchScreener` |
| S51 | Saved screens and watchlists | **IN** | watchlist 已有 |

### 5.7 组合构建（S52–S58）

| ID | Story | 状态 | 说明 |
|---|---|---|---|
| S52 | Candidate scores → target weights via explicit construction policy | **IN** | 启发式，版本化 |
| S53 | Long-only, position/sector/factor-exposure limits, turnover budgets | **IN-缩减** | 个股/行业/换手/现金下限 IN；**因子暴露上限推迟 v2.1** |
| S54 | Benchmark-aware optimization and attribution | **IN-降级** | 归因 IN；优化改启发式并显式声明 |
| S55 | A-share T+1, board lots, suspension, limit-lock, transaction costs | **IN** | 已有，扩到组合级 |
| S56 | Capacity and market-impact assumptions versioned | **IN-最小** | |
| S57 | Paper Portfolio recording intended orders and realized simulated outcomes | **IN** | |
| S58 | Rejected transitions leave state unchanged and retain a reason | **IN** | 已有 |

### 5.8 验证（S59–S66）

| ID | Story | 状态 | 说明 |
|---|---|---|---|
| S59 | Walk-forward portfolio reports across multiple market regimes | **IN** | |
| S60 | Simple benchmark, naive factor and v1 baseline comparisons | **IN** | |
| S61 | Gross and net results reported together | **IN** | |
| S62 | Confidence intervals, effect sizes and sample counts | **IN** | |
| S63 | Multiple-testing controls for broad factor and model searches | **IN** | **不可省** |
| S64 | Performance segmented by industry, size, liquidity and market regime | **IN** | |
| S65 | Rule, factor, model and Agent attribution reconciled to final result | **IN** | **替换 `validation.py:88-90` 占位实现** |
| S66 | All validation artifacts linked to their `RunManifest` | **IN** | |

### 5.9 运维与安全（S67–S72）

| ID | Story | 状态 | 说明 |
|---|---|---|---|
| S67 | Schedules/dependencies/retries/cancellation/progress for jobs | **IN-最小** | 复用 `runtime/batch.py` + cron，无 UI |
| S68 | Restart recovery without reusing incompatible state | **IN** | 已有 |
| S69 | Job health, data freshness, model status and cost in one place | **IN-降级** | CLI 报告 + 数据体检页 |
| S70 | Local notifications for blocked or degraded runs | **IN-最小** | CLI 退出码 + 日志 |
| S71 | Credentials read only from approved local secret sources | **IN** | 已有 |
| S72 | Reports preserve source and redistribution restrictions | **IN-最小** | 不导出 Tushare 原始 payload |

### 5.10 工作台（S73–S82）

| ID | Story | 状态 | 落地页面 |
|---|---|---|---|
| S73 | Data status page | **IN** | 页 1 数据体检 |
| S74 | Stock overview with price/financial/factor/event/evidence timelines | **IN** | 页 2 个股详情 |
| S75 | Interactive screener with saved criteria | **IN** | 页 2 |
| S76 | Factor laboratory | **IN** | 页 3 |
| S77 | Model evaluation page | **IN-合并** | 页 3（因子与模型实验室） |
| S78 | Candidate review page | **IN** | 页 2 |
| S79 | Portfolio and attribution dashboards | **IN** | 页 4 |
| S80 | Batch and schedule monitoring | OUT | CLI + 日志 |
| S81 | Immutable reports exportable without restricted raw payloads | **IN-最小** | |
| S82 | Mobile-width usable daily candidate and alert views | OUT | 桌面研究场景 |

### 5.11 接口与工程治理（S83–S90）

| ID | Story | 状态 | 说明 |
|---|---|---|---|
| S83 | Versioned REST and SDK contracts | **IN** | |
| S84 | CLI doctor / data-check / factor-run / model-evaluate / daily-run | **IN** | 个人场景下 CLI 是主界面，优先级高于 UI |
| S85 | Synthetic fixtures and deterministic clocks; CI never needs secrets | **IN** | **不可省** |
| S86 | Public behavior tested at API, SDK and end-to-end seams | **IN** | |
| S87 | Every capability recorded with source evidence, test evidence, terminal status | **IN** | **必须先修 B3 的 AST 校验** |
| S88 | Schema migrations explicit, backed up and recoverable | **IN-降级** | 一次性备份脚本 |
| S89 | Every candidate page and report states research not guaranteed advice | **IN-最小** | 自用场景下为提醒而非合规 |
| S90 | Platform refuses claims of guaranteed or stable returns | **IN** | 对自己同样重要 |

### 5.12 新增 Stories（S91–S97）

| ID | Story | 状态 | 来源 |
|---|---|---|---|
| S91 | As a researcher, I want panel data (prices, fundamentals, calendar, universe, industry, adjustments) stored in a partitioned panel store separate from the evidence store, so that factor-scale data never enters the per-row content-verified evidence path. | **IN** | B1 + B2 |
| S92 | As a data owner, I want Tushare datasets defined as declarative descriptors (params shape, subject/date fields, clock strategy, kind, URI template) over the shared HTTP envelope, so that adding a dataset does not mean writing a new adapter. | **IN** | §1.3 / 成本优化 |
| S93 | As a researcher, I want an independent PIT red-team gate that injects future disclosures, later revisions, future index constituents, future industry changes and overlapping labels, so that no factor work begins on silently look-ahead-biased data. | **IN** | Further Notes 第 2 条 |
| S94 | As a researcher, I want adjusted returns computed from `adj_factor` cross-checked against `daily.pct_chg` row by row, so that adjustment errors surface before they contaminate every factor. | **IN** | P2 闸门 |
| S95 | As a researcher, I want cross-sectional screening to run as a panel pipeline outside `run_cycle` and only the top-N shortlist to enter `run_cycle`, so that evidence closure is preserved without a 5000-subject write amplification. | **IN** | §3.2 |
| S96 | As a maintainer, I want the capability ledger to AST-verify that every `file#symbol` reference resolves, so that the completion gate cannot pass on symbols that do not exist. | **IN** | B3 |
| S97 | As a maintainer, I want an explicit numerical-stack boundary rule (no numerical imports in `domain/`; `DataFrame`/`ndarray` confined to `panel/`, `factors/`, `models/`), so that adding pandas does not erode ADR-0001 contract purity. | **IN** | §1.4 |

**统计**：IN 或 IN-降级/缩减/最小 **83** 条；v2.1 推迟 **6** 条（S31、S33、S34、S39，及 S53 的因子暴露上限、S54 的优化部分）；OUT **4** 条（S3、S80、S82，及 S2 的 Demo 档位）。

---

## 6. Implementation Decisions

保留 Proposed 版 1–30 的编号。**加粗**标注本版的修正或新增。

1. **保留 v1 核心合同。** `EvidenceSnapshot`、`SignalFrame`、`DecisionLedger`、`RunManifest`、`ResearchRunResult`、`PortfolioTransition`、`ValidationResult` 仍是稳定边界。新增因子、模型、排序与组合制品引用它们，不复制 provenance / decision / validation 语义。
2. **保留 local-first ADR。** 默认单节点 FastAPI + Parquet/DuckDB 分析数据 + SQLite WAL 元数据与持久作业 + 文件系统卷 + Docker Compose。域合同不得 import 具体 SQLite/DuckDB 类型。本 PRD 不引入多租户或多节点。
3. **【修正】保持一条研究路径，但明确其边界。** 原文"live/replay/backtest/Paper Portfolio/scheduled 全部复用 `run_cycle`"修正为：**所有产生证据闭合结论的路径复用 `ResearchEngine.run_cycle`；横截面初筛是不产生结论的前置过滤，运行在面板管线中，不进入 `run_cycle`。** 模式可选择时钟、数据集、模型与政策，但不得分叉证据到决策的内核。
4. **【修正】运行档位缩减为两档。** Research 要求使用者自控的 PIT 历史数据；Daily 额外要求新鲜度 SLA、调度与预检。**Demo 降级为 CI fixture，不再是产品档位。** 档位改变就绪要求，不改变研究语义。
5. **增加数据目录与就绪合同。** 数据集描述符记录 provider、license、再分发状态、时钟覆盖、标的覆盖、字段 schema、日期范围、新鲜度、修正支持、复权政策、日历、股票池版本与质量状态。依赖在必需就绪缺失时 fail-closed。
6. **不捆绑受限数据。** 使用者凭据、付费原始数据集、ChainLin 私有数据与未授权抓取内容不进入源码发布。安装可引导配置，但不得绕过上游许可。
7. **【修正】增加版本化因子合同，并明确其存储归属。** 因子定义有稳定身份、版本、家族、必需字段、回看窗、可得性政策、预处理政策、方向与输出 schema。因子观测记录标的、as-of 时间、值、覆盖标记、输入数据集引用与构建 manifest。**因子观测存入面板平面，不得写入 `ParquetEvidenceStore`。**
8. **原始因子值与预处理分离。** 去极值、缺失值处理、标准化与中性化是显式版本化变换。报告可比较 raw / processed / neutralized 表现而不覆盖源观测。
9. **使用 PIT 安全的股票池与标签。** 股票池成分、行业、基准成分、公司行动与标签均按研究时间戳解析。历史上适用的退市与停牌标的必须保留。
10. **建立独立于 `ModelProvider` 的量化模型边界。** `AlphaModel` 消费版本化特征数据集并产出不可变预测批次。`ModelProvider` 继续治理结构化 LLM 推理。Manifest 分别标识确定性、量化与 LLM 组件。
11. **训练是持久化、可复现的作业。** 模型制品记录训练截止、目标与周期、股票池、特征版本、预处理、切分政策、参数、seed、代码版本、指标与内容哈希。相同输入幂等；制品 ID 冲突复用失败。
12. **Walk-forward 评估对预测性主张是强制的。** 时间相关 Alpha 主张不接受随机训练/测试切分。重叠标签需要 purging 与 embargo。最终 holdout 或前瞻 Paper Portfolio 观测在选择过程中保持未触碰。
13. **【修正】先交付可理解基线。** 线性/排序与树基线建立最低比较。更复杂模型仅在预定义样本外、扣成本标准上改进且不违反稳定性与容量阈值时才被接受。**完整的模型比较矩阵与消融推迟 v2.1；v2 只要求"新模型必须战胜基线"这一条门槛。**
14. **预测先于结果落库。** Daily 与 Paper Portfolio 预测批次在观测窗关闭前不可变且带时间戳。回溯重算存为独立制品，不能替换原件。
15. **通过现有合同扩展 Agent 家族。** 新 Agent 实现现有 `ResearchAgent` 边界并产出经校验的 `AgentResult`/`SignalFrame`。声明所需证据**与特征**家族用于路由与就绪检查。
16. **通过专用产品合同排序候选。** `CandidateRanking` 由股票池、as-of 时间、周期、评分政策、构成 `SignalFrame`、预测、因子暴露、可交易性、风险标记与 manifest 定义。它绝不直接创建组合订单。
17. **分离排序、组合构建与模拟执行。** 排序回答什么值得复核；构建把已复核信号映射为目标研究权重；`PortfolioSimulator` 判定拟议转换是否满足 A 股规则与约束。
18. **【修正】增加版本化组合构建政策，实现方式为启发式。** 政策支持多头权重、个股/行业上限、换手预算、现金下限、基准感知、流动性与容量约束。**不引入凸优化求解器；构建为确定性启发式（分层排序 + 上限裁剪 + 换手预算），并在每份报告中显式标注 `heuristic, not optimized`。因子暴露上限推迟 v2.1。**
19. **Paper Portfolio 是前瞻模拟，不是券商对接。** 使用现有不可变订单/转换记账与市场观测，绝不连接券商、存储券商凭据或把模拟成交表示为真实成交。
20. **强化验证门。** 每份因子、模型、排序与组合报告包含样本数、gross/net 结果、成本、换手、容量、基准、置信区间与适用分段。广泛搜索记录被检验假设数量与多重检验政策。
21. **【修正】归因必须对账，且允许显式残差。** 规则、因子、量化模型与 Agent 贡献必须在文档化归因政策下对账到报告总量。**未分配残差显式存在，绝不静默分摊。这要求 `ValidationResult` 放宽当前"归因项精确求和"约束并新增 `AttributionTerm.category = "model"`；`backtest/validation.py:88-90` 的硬编码 20/30/50 比例实现必须删除。**
22. **在不改变有界本地运行的前提下泛化持久作业。** 数据刷新、因子构建、模型训练、Walk-forward 评估、排序与报告作业共享持久状态、进度、取消、重试、依赖与重启恢复。并发保持可配置且对 SQLite/本地硬件有界。
23. **暴露版本化 API 与 SDK 资源。** 公开接口覆盖数据就绪、因子定义/运行、模型制品/评估、候选排序、组合构建、Paper Portfolio、调度与作业状态。现有 v1 端点保持兼容或获得显式版本化迁移路径。
24. **【修正】Web 应用演进为 4 个路由区域。** 工作台增加**数据体检、候选清单（含个股详情）、因子与模型实验室、组合与验证**四个区域，同时保留证据、决策、回放与归因视图。**setup / jobs / settings 区域由 CLI 与 `.env` 承担；移动端宽度移出范围。**
25. **UI 使用渐进披露。** 先呈现就绪状态、候选与风险；可下钻到因子、证据、manifest 与统计诊断。缺失、过期与降级状态与合法空结果视觉上必须可区分。
26. **凭据与受限载荷不进入浏览器持久化。** 浏览器只接收状态与脱敏元数据。密钥值留在服务端批准的环境/本地密钥配置中，读 API 绝不返回。
27. **【修正】导出为许可感知的最小版。** 报告包含证据引用与许可摘要，但排除受限原始载荷。**具体到本部署：不导出 Tushare 原始 payload。**
28. **保留显式人工控制。** 研究结果不自动成为订单；验证结果不自动重训或晋升模型；候选变化不自动进入自选股或 Paper Portfolio。每次转换是显式的使用者或配置政策动作，并记入台账。
29. **【修正】使用能力台账治理，且台账本身必须机器可验证。** 每项 PRD 能力获得稳定 ID、实现去向、行为测试证据与终态状态。UI 控件、schema、mock 与文档本身不计完成。**`scripts/build_feature_coverage.py` 当前仅校验文件存在（`_paths()` 在 `#` 处截断后 `path.exists()`），必须扩展为 AST 符号断言，并修正现有 7 处失效引用（见 §1.3 B3）。这是 v2 第一个提交。**
30. **【修正】通过垂直切片交付，顺序按数据正确性依赖排。** 推荐顺序：**P0 地基校正与能力探测 → P1 面板数据平面 → P2 PIT 红队闸门（独立必过）→ P3 因子层 → P4 候选排序与模型基线 → P5 组合、验证与 4 页工作台**。每个切片保持可安装并行使一条完整的公开使用者行为。详见 `openalpha-cn-v2-roadmap.md`。

### 新增 Implementation Decisions

31. **【新增】双数据平面强制分离。** 面板数据（价量、财务、日历、股票池、行业、复权）进入按 `dataset/year/` 分区的面板存储，使用**持久** DuckDB catalog；离散可引用事件继续进入 `ParquetEvidenceStore`。禁止面板数据流入证据存储。禁止在面板查询路径上做逐行 pydantic 重建与 hash 重算。
32. **【新增】Provider 数据集以声明式描述符定义。** Tushare HTTP 为统一信封（`api_name` / `token` / `params` / `fields`），解码逻辑通用。数据集描述符声明：params 形状、标的字段、日期字段、时钟策略（`daily_close` / `announcement` / `calendar_static`）、`kind`、`source_uri` 模板。新增数据集是新增一行描述符，不是新增一个适配器。
33. **【新增】能力探测先于摄入。** `openalpha doctor` 对每个候选数据集发一次最小请求并记录返回 `code`/`msg`/限流，产出账号实际可取接口的报告。P1 的数据集清单由该报告确定，而不是由假设的积分档位确定。
34. **【新增】PIT 红队是独立闸门。** P2 必须通过才能进入 P3。注入未来披露、后续修正（`f_ann_date > ann_date`）、未来指数成分、未来行业变更、重叠标签，全部要求 fail-closed 或正确排除；并对 `adj_factor` 自算收益率与 `daily.pct_chg` 逐条交叉对账。
35. **【新增】数值栈边界。** 采用 numpy + pandas。`domain/` 禁止 import 任何数值库；`DataFrame` / `ndarray` 只允许出现在 `panel/`、`factors/`、`models/` 层。ADR-0001 的合同纯度由此规则保护。
36. **【新增】破坏性契约变更集中一次完成。** `RunManifest.mode` 增加 `paper` / `daily`；`AttributionTerm.category` 增加 `model` 并支持显式残差；`SignalFrame.horizon` 规范化为可比较枚举。三项在 P4 一次性打包升版，避免多轮迁移。

---

## 7. Testing Decisions

保留 Proposed 版 1–20 编号。**加粗**为本版修正。

1. 好的测试断言可观察合同、返回制品、持久状态、显式失败与使用者可见状态。不断言私有辅助方法、附带 SQL 布局、内部调用顺序或特定 React 组件树。
2. **【修正】最高端到端缝是 golden 使用者流程：** 通过能力探测 → 通过就绪检查 → 构建面板与证据 → 横截面初筛 → top-N 研究运行 → 产出候选排序 → 复核证据 → 构建 Paper Portfolio → 运行验证 → 检视不可变报告。**Demo Provider 由合成 fixture 承担，不再是产品档位的一部分。**
3. REST 集成测试是数据就绪、因子、模型、排序、组合构建、作业与报告的主要公开缝。
4. Python SDK golden-path 测试验证同样工作流不泄漏 HTTP 特定的域细节，并返回与服务层相同的版本化合同。
5. `ResearchEngine.run_cycle` 集成测试仍是 Agent 路由、证据可见性、结构化信号、风险决策、幂等、记忆与恢复的最高内核缝。新 Agent 必须通过该缝，而不是接受孤立的实现级验收。
6. 共享 Provider 合同套件验证元数据、时钟、修正、显式失败、限流与许可语义。CI 使用确定性 fake 与合法 fixture，绝不要求密钥或实时市场 API。
7. 因子合同测试使用冻结的股票池、日历、公司行动与修正，证明 PIT 可见性、确定性取值、预处理行为与内容派生身份。
8. **【提升为独立闸门】Look-ahead 测试** 故意包含未来披露、后续修正、未来基准成分与重叠标签，并要求 fail-closed。**这些测试构成 P2 独立闸门，必须整体通过才能进入 P3，而非分散在各域测试中。**
9. 模型评估测试使用具有已知信噪比性质的确定性合成数据集，验证 Walk-forward 切分、purge/embargo、制品身份与前瞻预测落库。测试不主张市场盈利能力。
10. 排序测试验证确定性排序、平局政策、弃权、缺失依赖、过期数据、风险/可交易性标记，以及每个入选候选的证据闭合。
11. 组合测试扩展现有不可变转换与多日报告先例，覆盖目标权重政策、上限、换手预算、容量与 Paper Portfolio 前瞻记账。
12. 验证测试对照简单基准，对账 gross/net 结果与归因，并验证置信区间、样本数、分段与多重检验元数据。
13. 持久作业集成测试扩展现有批量与恢复先例，覆盖依赖、有界并发、进度、取消、重试、重启恢复与不兼容请求/图拒绝。
14. **【修正】Web 组件测试** 使用公开 API fixture 验证 loading / ready / empty / degraded / stale / blocked / failed / succeeded 状态。证据链接、告警与显式人工动作是使用者可见验收标准。**范围限于 4 个页面。**
15. **【修正】Playwright 测试** 覆盖桌面 golden 流程：Provider 就绪失败、每日候选复核、Paper Portfolio 与报告检视。**移动端宽度流程移出范围。**
16. **【降级】迁移** 提供一次性备份脚本并验证备份后现有 `EvidenceSnapshot`、`DecisionLedger`、`RunManifest`、memory、watchlist、报告与组合转换仍可读。**不构造 v1 持久化卷做完整迁移测试**（仓库内不存在此类卷）。
17. 安全测试验证请求限额、schema 拒绝、安全响应头、密钥脱敏、默认仅本地绑定、导出许可过滤与发布扫描。
18. 性能测试为文档化的股票池与日期范围建立有界本地预算。结果报告硬件与数据集假设，不暗示分布式规模。
19. **【修正】发布门** 保留后端覆盖率不低于 80%（当前实测 87%）、时间/证据/决策路径更严格的分支覆盖、零已知严重 look-ahead 违规、零静默 Provider 降级与成功的确定性回放。
20. **【修正】能力台账与文档测试** 阻止"stable recommendations"、"automatic trading"、"included commercial data"、"completed Alpha model" 之类主张，除非存在对应实现与行为证据。**台账测试必须 AST 校验每个 `file#symbol` 引用可解析（见 Decision 29）。**

---

## 8. Out of Scope

承自 Proposed 版：

- 实盘券商对接、真实订单路由、资金托管或券商凭据；
- 保证收益、"稳定荐股"、个性化投资建议或自动适当性判定；
- 卖空、融券、平仓执行、衍生品与高频/tick 级执行；
- 无明确权利地捆绑、代理或再分发商业数据；
- 托管的公开商业数据 API；
- 自动模型晋升、由验证结果自动重训或自我修改的生产规则；
- 默认实现中的多节点、Kubernetes、多区域或公开多租户部署；
- 与 Wind、Bloomberg 或券商终端全部功能对等；
- 把 LLM 叙述质量、合成回放成功或样本内回测当作 Alpha 证据；
- 替代专业法律、合规、数据许可或投资审查。

本版新增排除：

- **实时行情能力**：L1/L2、逐笔、盘口、分时推送、WebSocket 实时订阅。使用者无此数据，且现有架构的可得性时钟是保守的 16:30 Asia/Shanghai，整体为批处理 PIT 导向。若未来需要，须作为独立 PRD 增量，并重新评估与 local-first + BYOD + PIT 取向的张力。
- **移动端界面**（S82）；
- **图形化首启向导**（S1 的 UI 形态）与**图形化任务监控**（S80）；
- **Demo 作为产品运行档位**（S3）——降级为 CI fixture；
- **凸优化组合求解器**——改启发式（Decision 18）；
- **模型校准/比较矩阵/消融/漂移监控**（S31/S33/S34）与 **Agent 按周期×市场状态可靠性度量**（S39）——推迟 v2.1。

---

## 9. Further Notes

- 本 PRD 是增量 v2 方向，不是重写。最强的现有资产是 PIT 证据、内容寻址身份、共享 `run_cycle`、不可变台账、恢复、A 股执行约束与验证原语。
- **关键路径是数据正确性先于模型复杂度。** 没有历史可得性/修正时钟、股票池历史、公司行动与生存偏差控制，更大的因子库或更多 Agent 只会提高错误的信心，而不是产品价值。这是 P2 被提升为独立闸门的唯一理由：在错数据上建 20 个因子再推翻，成本远高于闸门本身的 1–2 周。
- 使用者可见语言中的"推荐"应替换为"候选"、"研究观点"或"决策支持输出"。
- **产品成功分两层度量，且第二层不能由第一层触发。**
  - 工程成功：可复现性、证据闭合、就绪、恢复与可用工作流。**CI 可自动判定。**
  - 研究成功：预先登记的样本外指标、扣成本增量价值、稳定性、容量与前瞻 Paper Portfolio 观测（≥6–12 个月）。**只能由时间判定。**
  - 纪律：任何使用者可见的措辞升级必须由第二层证据触发，绝不由第一层完成度触发。
- 对个人研究者，第二层的判定者不是合规而是**你自己在 6–12 个月后回看这份预先落库的预测**。没有外部监督时，这套约束更容易被省掉，也更致命。
- 初始验收使用合成 fixture。进入 Research 或 Daily 档需要使用者配置自有合法数据并显式接受 Provider 条款。
- **两项待定决策**（不阻塞 P0 启动，但会改变 P0 的 ADR 内容）：
  1. **是否继续维护开源分发。** 仓库为 MIT 且有公开地址与推广文档。本版默认个人研究优先，将 Demo 档位、发布扫描与完整迁移测试降级。若决定对外发布 v2，需加回这三项（约 +3–4 周），且 Demo 冻结数据集须重新设计为不含 Tushare 原始数据。
  2. **Tushare 积分档位未知。** 本 PRD 不猜接口门槛；P0 能力探测将测出账号实际可取接口。若 `index_weight` 或行业分类历史不可用，S11/S19 降级为自建静态股票池 + 市值中性化，并在每份因子报告中标注该限制。
