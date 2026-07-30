# OpenAlpha CN v2 开发路线图

Status: Active
Baseline: `main` @ `8d13065`
配套文档: `openalpha-cn-v2-prd.md`（范围与决策依据）
工期口径: 单人全职 / 每周 15 小时（按 ×2.5 折算）

---

## 0. 里程碑总览

| 阶段 | 主题 | 全职 | 每周 15h | 闸门 | 阶段产出（可独立使用） |
|---|---|---:|---:|---|---|
| **P0** | 地基校正与能力探测 | 1 周 | 2–3 周 | 台账 AST 校验通过 | 知道账号能取什么数据；台账可信；架构边界写死 |
| **P1** | 面板数据平面 | 4–6 周 | 10–15 周 | 8 组数据集全部通过契约测试 | `openalpha panel build` 落出可体检的本地面板 |
| **P2** | **PIT 红队闸门** | 1–2 周 | 3–5 周 | **必过，否则不得进入 P3** | 数据可信度有测试背书 |
| **P3** | 因子层 | 4–5 周 | 10–13 周 | 首批因子出齐三档报告 | `openalpha factor run` 出 IC 报告；可在 Jupyter 直接研究 |
| **P4** | 候选排序与模型基线 | 4–5 周 | 10–13 周 | 契约变更一次性完成 + 预测落库 | 每日 top-N 候选，每个可点到证据 |
| **P5** | 组合、验证与 4 页工作台 | 4–6 周 | 10–15 周 | 归因对账 + 多重检验 + 4 页可用 | 完整闭环 |
| | **合计** | **18–25 周** | **45–64 周** | | |

**只能做一半：做 P0–P3。** 结束时得到"数据可信、PIT 有保障、带 15–25 个诊断完备因子的个人研究环境"，本身即有独立价值。P4–P5 是产品化。

**唯一不可接受的取舍：跳过 P2 抢 P3。**

### 依赖关系

```
P0 ──> P1 ──> P2(闸门) ──> P3 ──┬──> P4 ──> P5
                                 │
                          (P3 结束即可独立使用)

P0 交付物中，"能力探测报告"决定 P1 的实际数据集清单；
"数值栈 ADR" 与 "双平面 ADR" 决定 P1/P3 的代码归属；
"台账 AST 校验" 是后续每个阶段的完成判定基础设施。
```

---

## P0 — 地基校正与能力探测

**目标**：不写功能代码，只消除后续最贵的返工。
**对应 PRD**：S7、S96、S97、Decision 29/32/33/35

### 任务

- [ ] **T0.1 台账符号 AST 校验**（半天，第一个提交）
  扩展 `scripts/build_feature_coverage.py`：当前 `_paths()` 在 `#` 处截断后只做 `path.exists()`，从不校验符号。改为解析 `file#symbol` 并用 `ast` 断言符号存在于该文件。
- [ ] **T0.2 修正 v1 台账 7 处失效引用**
  `MarketEventAgent`→`MarketAgent`、`ThemeCatalystAgent`→`ThemeAgent`、`CapitalFlowAgent`→`CapitalAgent`、`StructuredModelAgent`→`StructuredSignalAgent`、`AShareExecutionModel`→`AShareExecutionPolicy`、`AShareCostModel`→`CostSchedule`、`AkShareProvider`→`AKShareProvider`
- [ ] **T0.3 Tushare 能力探测**（PRD S7）
  扩展 `openalpha doctor`：对每个候选数据集发一次最小请求，记录返回 `code`/`msg`/限流，产出账号实际可取接口的报告。**输出直接决定 T1.3 的数据集清单。**
- [ ] **T0.4 ADR：双数据平面分离**（PRD Decision 31）
  面板平面 vs 证据平面的职责、存储、禁止事项。
- [ ] **T0.5 ADR：数值栈与边界**（PRD Decision 35）
  采用 numpy + pandas；`domain/` 禁止 import 数值库；`DataFrame`/`ndarray` 限于 `panel/`、`factors/`、`models/`。加一条 CI 检查（import 静态扫描）强制该规则。
- [ ] **T0.6 描述符表骨架**（PRD Decision 32）
  把现有 `daily` 重构成描述符驱动，证明抽象成立，再在 P1 批量加数据集。三种时钟策略：`daily_close` / `announcement` / `calendar_static`。

### 完成判定（闸门）

- `scripts/build_feature_coverage.py` 对失效符号会失败，且当前台账全部通过
- 能力探测报告已产出并入库
- import 边界 CI 检查生效
- `daily` 已走描述符路径，现有 105 个测试仍全绿

### 风险

- 能力探测可能发现关键接口不可用 → 触发 §待定决策 2 的降级路径，需在 P0 结束时更新 PRD S11/S19 的状态

---

## P1 — 面板数据平面

**目标**：建立 v2 的真正地基。
**对应 PRD**：S4、S6、S8–S14、S91、S92、Decision 5/31/32

### 任务

- [ ] **T1.1 `panel/` 层骨架**
  按 `dataset/year/` 分区的 Parquet + **持久** DuckDB catalog（不是每次 `connect(":memory:")`，不是 `glob("*.parquet")`）。
- [ ] **T1.2 数据目录与就绪合同**（S8、Decision 5）
  数据集描述符记录 provider、license、再分发、时钟覆盖、标的覆盖、字段 schema、日期范围、新鲜度、修正支持、复权政策、日历、股票池版本、质量状态。
- [ ] **T1.3 按因子正确性依赖接入数据集**（顺序不可调换）

  | # | 数据集 | 为什么这个顺序 |
  |---|---|---|
  | 1 | `trade_cal` | 一切时间对齐的前提 |
  | 2 | `stock_basic`(含 `list_date`/`delist_date`) + `namechange` | 生存偏差与 ST 历史；缺它回测必然虚高 |
  | 3 | `adj_factor` | **没有它所有收益率都是错的**，因子层无从谈起 |
  | 4 | `daily` + `daily_basic` | 价量 + 市值/换手/估值 |
  | 5 | `suspend_d` + `stk_limit` | 可交易性；接入现有 `AShareExecutionPolicy` |
  | 6 | `index_weight` | 股票池与基准（沪深300/中证500/中证1000） |
  | 7 | 行业分类历史 | 中性化的前提 |
  | 8 | `fina_indicator` + 三大报表 | **必须同时取 `ann_date` 与 `f_ann_date`**，否则修正时钟是假的 |

- [ ] **T1.4 数据体检**（S13）
  按数据集汇总缺失、过期、重复、被修正记录。
- [ ] **T1.5 fail-closed 依赖**（S14）
  失败的日度数据集显式阻塞下游研究，不得降级为空成功。
- [ ] **T1.6 CLI**：`openalpha panel build --start --end`、`openalpha panel doctor`

### 完成判定（闸门）

- 8 组数据集各有：一个契约测试 + 一个"注入未来数据必须 fail-closed"测试
- `openalpha panel build --start 2015 --end 2026` 可完整跑通
- `openalpha panel doctor` 能报出人为注入的缺失/过期/重复/修正
- 面板查询路径上**不存在**逐行 pydantic 重建或 hash 重算（性能测试断言）

### 风险

- **限流与增量拉取**：全历史首次构建耗时可能以小时计。需要断点续传与本地缓存，不要设计成一次性长任务
- 若 T0.3 探测出接口不可用，第 6/7 项降级（自建静态股票池 + 市值中性化），并在数据目录中标注该限制

---

## P2 — PIT 红队闸门（独立必过）

**目标**：证明数据没有静默前视偏差。**这是全项目性价比最高的 1–2 周。**
**对应 PRD**：S9、S93、S94、Decision 34、Testing Decision 8

### 任务

- [ ] **T2.1 注入未来披露** → 要求排除或 fail-closed
- [ ] **T2.2 注入后续修正**（`f_ann_date > ann_date`）→ 要求按 as-of 时点返回**修正前**的值
- [ ] **T2.3 注入未来指数成分变更** → 要求按 as-of 时点解析成分
- [ ] **T2.4 注入未来行业分类变更** → 同上
- [ ] **T2.5 重叠标签检测** → 要求显式拒绝或标注
- [ ] **T2.6 复权收益率交叉对账**（S94）
  用 `adj_factor` 自算的收益率 vs `daily.pct_chg` 逐条比对，容差外报错
- [ ] **T2.7 停牌日与涨跌停日收益率处理专项验证**
- [ ] **T2.8 退市股票必须仍存在于历史股票池**（生存偏差）

### 完成判定（闸门 —— 必过）

- T2.1–T2.8 全部通过，**零已知严重 look-ahead 违规**
- 该套测试进入 CI，作为后续每次 P3/P4 提交的回归门

### 为什么是独立闸门

PRD Further Notes：数据错了，因子越多越危险。在错数据上建 20 个因子再推翻，成本远高于这 1–2 周。这也是唯一一个**不允许为了赶进度跳过**的阶段。

---

## P3 — 因子层

**目标**：可信的因子研究环境。P3 结束即可独立使用（Jupyter 直连）。
**对应 PRD**：S15–S24、Decision 7/8/9

### 任务

- [ ] **T3.1 版本化因子定义注册表**（S15）
  内容寻址身份，复用 `domain/_identity.py#stable_model_id` 模式。
- [ ] **T3.2 面板特征计算引擎**（S17）
  每个因子观测记录标的、as-of、值、覆盖标记、输入数据集引用、构建 manifest。**写入面板平面，不得进 `ParquetEvidenceStore`。**
- [ ] **T3.3 预处理与原值严格分离**（S18、S19、Decision 8）
  去极值 / 标准化 / 行业+市值中性化，各为显式版本化变换。
- [ ] **T3.4 诊断报告**（S20–S23）
  IC、Rank IC、IC 衰减、稳定性、分组收益（含成本，复用 `AShareExecutionPolicy`）、换手、覆盖率、相关性冗余。
- [ ] **T3.5 首批因子 15–25 个**（S16，**不是 200 个**）

  | 家族 | 因子 |
  |---|---|
  | 价值 | EP、BP、SP、EPcut |
  | 质量 | ROE、ROIC、毛利率稳定性、应计项 |
  | 成长 | 营收同比、净利同比、同比加速度 |
  | 动量 | 20 日、60 日、120 日、行业相对动量 |
  | 反转 | 5 日反转 |
  | 波动 | 残差波动率、特质波动 |
  | 流动性 | 换手率、Amihud 非流动性 |

- [ ] **T3.6 不可变因子实验制品**（S24）
- [ ] **T3.7 CLI**：`openalpha factor run --factor <id> --start --end`

### 完成判定（闸门）

- 每个因子同时出 **raw / processed / neutralized 三档**报告（否则分不清"因子有效"与"暴露没控住"）
- 因子合同测试使用冻结股票池/日历/公司行动/修正，证明 PIT 可见性与确定性取值
- P2 红队测试仍全绿

### 风险

- 首批因子中可能大部分 IC 不显著 —— **这是正常且有价值的结果**，不要通过调参把它们"救活"。多重检验控制在 P5 才上，因此 P3 阶段的 IC 结论应视为**探索性**，不得据此宣称发现

---

## P4 — 候选排序与模型基线

**目标**：每日产出证据闭合的 top-N 候选。
**对应 PRD**：S25–S30、S32、S35–S38、S40–S51、S95、Decision 3/10–17/36

### 任务

- [ ] **T4.1 两段漏斗**（S95、Decision 3、PRD §3.2）
  横截面打分 + 硬性可交易过滤（面板管线，不进 `run_cycle`）→ top N → `run_cycle` → `CandidateRanking`。
  用实测标定 N（建议起点 100）。
- [ ] **T4.2 `CandidateRanking` 合同**（S43–S49、Decision 16）
  股票池、as-of、周期、评分政策、构成 `SignalFrame`、预测、因子暴露、可交易性、风险标记、manifest。**绝不直接创建订单。**
- [ ] **T4.3 治理化筛选**（S50、S51）
  取代现有仅按 confidence 排序的 `ResearchScreener`；组合因子、Agent 信号、模型预测、证据、风险与可交易性。
- [ ] **T4.4 Agent 家族扩展**（S36 首批 4 类、S38、S40）
  实现现有 `ResearchAgent` 边界；声明证据**与特征**依赖；manifest 区分确定性/量化/LLM 组件。
- [ ] **T4.5 `AlphaModel` 最小集**（S25–S30、S32）
  - 版本化特征矩阵（S26）
  - Walk-forward + purge/embargo（S27、S28）
  - 线性/排序基线 + LightGBM（S29）
  - 内容寻址模型制品：训练截止、特征版本、参数、seed、代码版本（S30）
  - **预测在结果已知前落库**（S32，不可省）
  - stale 模型显式弃权（S35 最小版）
- [ ] **T4.6 破坏性契约变更一次性打包**（Decision 36）
  - `RunManifest.mode` += `paper` / `daily`
  - `AttributionTerm.category` += `model`，并放宽 `ValidationResult` 的精确求和约束以支持显式残差
  - `SignalFrame.horizon` 规范化为可比较枚举
  - 配套：备份脚本 + 现有制品可读性验证（Testing Decision 16 降级版）

### 完成判定（闸门）

- 排序测试覆盖确定性排序、平局政策、弃权、缺失依赖、过期数据、风险/可交易性标记，**每个入选候选证据闭合**
- 模型评估测试用已知信噪比合成数据验证 Walk-forward 切分、purge/embargo、制品身份、前瞻预测落库
- 契约升版后现有制品仍可读，105+ 测试全绿
- 新 Agent 全部通过 `run_cycle` 缝，无孤立实现级验收

### 风险

- **T4.6 是唯一的破坏性变更窗口。** 若遗漏字段将导致第二轮迁移 —— 在 P4 开始前把三项变更的完整字段清单先写定
- LightGBM 引入需确认 local-first 部署体积可接受

---

## P5 — 组合、验证与 4 页工作台

**目标**：完整闭环。
**对应 PRD**：S52–S72、S73–S79、S81、S83–S90、Decision 18–28

### 任务

- [ ] **T5.1 启发式组合构建**（S52–S54、Decision 18）
  分层排序 + 个股上限 + 行业上限 + 换手预算 + 现金下限。**每份报告显式标注 `heuristic, not optimized`。** 不引入 cvxpy。因子暴露上限推迟 v2.1。
- [ ] **T5.2 组合级多日回测**（S55）
  `multi_day.py#PortfolioBacktestStep` 从"一步一单一标的"扩到组合级换手。
- [ ] **T5.3 Paper Portfolio**（S57、Decision 19）
  复用现有不可变订单/转换记账；绝不连接券商。
- [ ] **T5.4 替换占位归因**（S65、Decision 21）
  **删除 `backtest/validation.py:88-90` 的硬编码 rule 20% / factor 30% / agent 50% 实现**，改为可辩护的归因政策 + 显式未分配残差。
- [ ] **T5.5 验证门**（S59–S64、S66、Decision 20）
  BH 多重检验控制、gross/net 并列、置信区间与样本数、按行业/市值/流动性/市场状态分段、制品链接到 `RunManifest`。
- [ ] **T5.6 作业与调度最小版**（S67、S69、S70）
  复用 `runtime/batch.py` + cron；CLI 报告承担健康度；退出码 + 日志承担通知。**无 UI。**
- [ ] **T5.7 API/SDK 扩展**（S83、Decision 23）
- [ ] **T5.8 CLI 完整化**（S84）
  `doctor` / `data-check` / `factor-run` / `model-evaluate` / `daily-run`。**个人场景下 CLI 是主界面，优先级高于 UI。**
- [ ] **T5.9 4 页工作台**（S73–S79、Decision 24/25）

  | 页面 | 内容 | 对应 Story |
  |---|---|---|
  | 数据体检 | 面板覆盖、新鲜度、缺失/修正、就绪状态 | S73 |
  | 候选清单 + 个股详情 | 排序、分数、置信度、排名变化、证据链、失效条件、可交易性告警 | S74、S75、S78 |
  | 因子与模型实验室 | 因子定义、IC/分组/衰减、相关性矩阵、raw/processed/neutralized 对比、模型样本外指标 | S76、S77 |
  | 组合与验证 | 权重、暴露、换手、容量、Paper Portfolio 净值、归因、分段报告 | S79 |

  技术选型：**React Router + TanStack Query + ECharts + TanStack Table**。不引入设计系统，沿用现有 `styles.css` 扩展。
- [ ] **T5.10 报告导出最小版**（S72、S81、Decision 27）
  含证据引用与许可摘要；**不导出 Tushare 原始 payload**。

### 完成判定（闸门）

- 归因对账通过且残差显式（不静默分摊）
- 广泛因子/模型搜索记录被检验假设数与多重检验政策
- 4 页各状态（loading / ready / empty / degraded / stale / blocked / failed / succeeded）有组件测试
- Playwright 桌面 golden 流程通过：Provider 就绪失败、每日候选复核、Paper Portfolio 与报告检视
- 后端覆盖率 ≥ 80%；安全测试通过；确定性回放成功

---

## 1. 双层验收（贯穿全程）

| 层 | 门槛 | 判定者 | 时间 |
|---|---|---|---|
| **工程成功** | 可复现性、证据闭合、就绪检查、恢复、可用工作流、覆盖率、零 look-ahead 违规 | CI 自动判定 | P5 结束 |
| **研究成功** | 预先登记的样本外指标、扣成本增量价值、稳定性、容量、前瞻 Paper Portfolio 观测 | 只能由时间判定 | P5 之后 **6–12 个月** |

**纪律**：任何使用者可见的措辞升级（从"候选"到更强表述）必须由第二层证据触发，绝不由第一层完成度触发。

对个人研究者，第二层的判定者是**你自己在 6–12 个月后回看这份预先落库的预测**（PRD S32）。没有外部监督时，这条最容易被省掉，也最致命。

---

## 2. 每阶段的能力台账义务

自 P0 的 T0.1 起，每个阶段结束时：

1. 新能力在台账中获得稳定 ID、实现去向（`file#symbol`）、行为测试证据、终态状态
2. `scripts/build_feature_coverage.py` 的 AST 校验必须通过 —— **符号不存在即阻断**
3. UI 控件、schema、mock 与文档本身**不计**完成（PRD Decision 29）

---

## 3. 待定决策（不阻塞 P0 启动）

| # | 决策 | 默认取值 | 若改变则影响 |
|---|---|---|---|
| 1 | 是否继续维护开源分发 | **个人研究优先**：Demo 档位、发布扫描、完整迁移测试降级 | 加回三项约 **+3–4 周**；Demo 冻结数据集须重新设计为不含 Tushare 原始数据；影响 T0.4 ADR 内容 |
| 2 | Tushare 积分档位 | 不猜，由 **T0.3 能力探测**测定 | 若 `index_weight` 或行业分类历史不可用：T1.3 第 6/7 项降级为自建静态股票池 + 市值中性化，PRD S11/S19 状态需更新，且每份因子报告须标注该限制 |
