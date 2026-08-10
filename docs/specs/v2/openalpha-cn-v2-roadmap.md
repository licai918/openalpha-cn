# OpenAlpha CN v2 开发路线图

Status: Active — 已按 2026-07-30 四缝审计重新切片
Baseline: `main` @ `e5c6b90`
配套: `openalpha-cn-v2-prd.md`（范围与决策）· `openalpha-cn-v2-seam-audit.md`（审计证据与缺口证明）
工期口径: 单人全职 / 每周 15 小时（×2.5）
Issue ID: `V2-<阶段>-<序号>`。类型标记：**结**结构 · **产**产品 · **测**测试 · **技**技术

---

## 0. 总览

| 阶段 | 主题 | Issue 数 | 全职 | 每周 15h | 闸门 |
|---|---|---:|---:|---:|---|
| **P0.A** | 治理与探测 | 9 | 1.5 周 | 4 周 | 台账 AST 校验生效 + 能力探测报告入库 |
| **P0.B** | 结构地基与迁移机制 | 16 | 3–4 周 | 8–10 周 | 迁移机制可用 + 单一组装根 + conftest 就位 |
| **P1** | 面板数据平面 | 17 | 5–7 周 | 13–18 周 | 8 组数据集全部通过契约 + 未来数据 fail-closed |
| **P2** | **PIT 红队闸门** | 9 | 2 周 | 5 周 | **必过，否则不得进 P3** |
| **P3** | 因子层 | 15 | 5–6 周 | 13–15 周 | 首批因子出齐 raw/processed/neutralized 三档 |
| **P4** | 候选排序与模型基线 | 24 | 6–7 周 | 15–18 周 | 契约升版一次完成 + 预测先落库 |
| **P5** | 组合、验证与工作台 | 24 | 6–8 周 | 15–20 周 | 归因对账 + 多重检验 + 4 页可用 |
| | **合计** | **114** | **28–36 周** | **70–90 周** | |

> **相对上一版的变化**：上一版估 18–25 周，假设 P0 为 1 周。审计发现 39 条必须在面板层之前关闭的前置 finding（无迁移机制、两个组装根、引擎四合一、无 conftest、look-ahead 靠字符串匹配、`.parquet` 被发布拦截、seed/commit/digest 全为占位），故新增 P0.B。上调的 10–11 周全部是**原先不可见的前置债**，不是范围膨胀。

> **2026-08-01 外部评审吸收**：上游 Issue #8 收到 `initial-d` 的评审建议。其三层验收顺序（数据/证据闸门 → 因子模型只收 walk-forward artifacts → 组合层单独证明增量价值）与本路线图 P1→P2→P3→P4→P5 同构，无需调序。经 grep 核对，吸收其中六条本路线图未覆盖的具体项，新增 3 个 issue 并强化 4 个：`V2-P1-017` 标签契约、`V2-P4-023` 榜单级闸门、`V2-P5-024` buffered 对照版本，以及 `V2-P5-008` cost drag 单列、`V2-P5-009` 等权基线、`V2-P5-015/016` 股票池与数据授权 caveat。总数 110 → 113。

### 依赖图

```
P0.A ──> P0.B ──> P1 ──> P2(闸门) ──> P3 ──┬──> P4 ──> P5
 │         │                                │
 │         └── V2-P0B-004 迁移机制 ──────────┴──> P4 的硬前置
 │
 └── V2-P0A-004 能力探测 ──> 决定 P1 实际数据集清单
     V2-P0A-007 数值栈 ADR ──> 决定 P3/P4 代码归属与 mypy 配置

P3 结束即可独立使用（Jupyter 直连面板 + 因子）
```

---

## P0.A — 治理与探测（9 issues）

| ID | 标题 | 类型 | 依赖 | 关键文件 / 证据 | 测试缝 | PRD |
|---|---|---|---|---|---|---|
| `V2-P0A-001` | 台账 `#symbol` AST 校验 —— `_paths()` 在 `#` 处截断，73/77 个符号引用从未被解析 | 测 | — | `scripts/build_feature_coverage.py:33-40,55-63` | 单元：对失效符号必失败 | D29, S87, S96 |
| `V2-P0A-002` | 修正 7 处失效符号引用 | 测 | 001 | `MarketEventAgent`→`MarketAgent` 等 7 处 | 由 001 的门覆盖 | S87 |
| `V2-P0A-003` | `acceptance_test` 绑定到 pytest node id；`test_evidence` 校验可收集 | 测 | 001 | 同上（当前是自由散文，从不执行；38 个路径里 3 个不是测试） | 单元 | D29, S87 |
| `V2-P0A-004` | `doctor` 扩展为 Provider 能力探测 + 凭据存在性校验；导出 `ChainLinDataProvider` | 产 | — | `cli.py:56-88`；`providers/__init__.py`（chainlin 未导出）；`providers/tushare.py:80` | 契约：fake transport 断言探测矩阵 | S7, S6, D33 |
| `V2-P0A-005` | 分层 lint 门（import-linter 或 tach）：`domain/` 禁 numpy/pandas/sqlite3/duckdb；`storage/` 禁向上依赖 | 结 | — | `pyproject.toml:63-64` 无 banned-API 规则；全库无 import 约束配置 | CI job | D35, S97 |
| `V2-P0A-006` | ADR：双数据平面分离 | 结 | — | 新增 `docs/architecture/ADR-0002` | — | D31, S91 |
| `V2-P0A-007` | ADR：数值栈边界 + mypy/ruff/容器预案 | 技 | — | `sklearn`/`lightgbm` 不在 override（strict 即失败）；`warn_return_any` + `follow_imports=skip` ⇒ 返回 pandas 表达式必报错；补 `NPY`/`PD`/`S` 规则；固定 `OMP_NUM_THREADS` | CI: `mypy src` 通过 | D35, S97 |
| `V2-P0A-008` | Tushare 描述符表骨架（把 `daily` 重构成描述符驱动） | 结 | 007 | `providers/tushare.py:90-197`；三种时钟策略 `daily_close`/`announcement`/`calendar_static` | 契约：现有 tushare 测试不变 | D32, S92 |
| `V2-P0A-009` | 覆盖率门本地化 + 前端覆盖率配置；解除 `test_repository_assets.py` 对 CI 文件字面串的耦合 | 测 | — | `pyproject.toml` 无 `fail_under`；`web/vite.config.ts` 无 coverage；`tests/unit/test_repository_assets.py:206-223` 断言 `quality.yml` 内容 | CI | T19 |

**闸门**：`build_feature_coverage.py` 对不存在的符号会失败且当前台账全绿；能力探测报告入库；分层 lint 生效；`daily` 已走描述符路径；105 个既有测试仍全绿。

---

## P0.B — 结构地基与迁移机制（16 issues）

> 全部是 P1/P4 的前置。不做这一段，后面每一层都要付两倍成本。

| ID | 标题 | 类型 | 依赖 | 关键文件 / 证据 | 测试缝 | PRD |
|---|---|---|---|---|---|---|
| `V2-P0B-001` | 从 `runtime/engine.py` 拆出研究契约模块 | 结 | — | 380 行含 4 份职责；`backtest/validation.py:10`、`backtest/replay.py:13`、`product/research.py:10`、`runtime/batch.py:13` **只为契约**而 import | 现有集成测试不变 | D1 |
| `V2-P0B-002` | 单一组装根，替换两处手工同步的装配；顺带修 batch/单run 证据校验宽严不一 | 结/产 | 001 | `api/app.py:254-269` ≡ `sdk.py:63-113`；`api/app.py:111` 用裸 `ResearchRunRequest` 导致同 payload 在 batch 失败 | 集成：REST 与 SDK 结果等价 | D23 |
| `V2-P0B-003` | 存储 Protocol 层（当前只有 `ResearchMemory` 是 Protocol；ADR-0001:19 的声明无实现） | 结 | 001 | `runtime/engine.py:22-23,75,81,89`；`backtest/multi_day.py:16,83` 签名耦合 SQLite 类 | 单元：Protocol 可被 in-memory 替身满足 | D2 |
| `V2-P0B-004` | **迁移机制**：`PRAGMA user_version` + `schema_migrations` 表 + 备份/回滚 CLI | 技 | 003 | 全库零迁移机制（grep `migrat\|user_version\|ALTER TABLE\|alembic` 在 `src/` 零命中）；9 张表全是 `CREATE TABLE IF NOT EXISTS` | 集成：从 v1 卷升级后旧记录可读 | D36, S88, T16 |
| `V2-P0B-005` | 多版本 schema 读取：`extra="forbid"` + `Literal["/v1"]` 使新旧双向不可读 | 技 | 004 | `storage/sqlite.py:84` 遇 `/v2` 硬失败；`test_schema_export.py:19` 断言 `endswith("/v1")` | 单元：v1 与 v2 payload 并存可读 | D36 |
| `V2-P0B-006` | 配置对象 + 进程内 `.env` 加载；修死变量与 host/port 三处不一致 | 技 | — | 无 dotenv/pydantic-settings；`.env.example` 12 个变量只有 3 个被读，`LOG_LEVEL`/`HOST`/`PORT` 是死的，被读的 `WEB_DIR` 未声明；`api/app.py:247` 无守卫 `int()` 会在 import 期崩 | 单元 + 集成 | S71, D26 |
| `V2-P0B-007` | 结构化日志（`src/` 内零 logging 配置） | 技 | 006 | 调度器无日志不可运维 | 集成：断言关键事件被记录 | S70 |
| `V2-P0B-008` | `create_app()` 时钟缝（12 个 REST 测试当前跑真实墙钟） | 测 | 002 | `api/app.py:238-243` 不接受 clock，`:262/:268/:274` 硬编码 `datetime.now(UTC)` | 集成：REST 在冻结时钟下确定性 | T3, S85 |
| `V2-P0B-009` | 确定性补全：`code_commit` 从 git 取、`config_digest` 真实计算、`random_seed` 贯通、固定 BLAS 线程 | 技 | 006 | `random_seed` 记录后从未被读；`code_commit` 真实值是 `"development"`/`"web-development"`；`config_digest` 是 `"0"*64`；**注意：只有 `code_commit` 进入 `decision_id`，另两者不进（见 §9）**；`engine.py:105` 使首次运行不可仅凭输入复现 | 单元 + 集成：同输入同 ID | D11, S30, S85 |
| `V2-P0B-010` | `ValidationResult` 持久化（当前无任何 store 写它 ⇒ 工作台第 4 页无数据源） | 技/产 | 003,004 | 仅 `api/app.py:526-539` 返回 | 集成：写入后可按 run 查询 | S66, S79 |
| `V2-P0B-011` | ADR 违规修复 + 三个多职责模块拆分 | 结 | 003 | `providers/file.py:10` duckdb；`models/governance.py:3` sqlite3；`evidence/service.py:17,51` 绑死 `FileProvider`；`domain/schema.py:38-41` 做 IO；`storage/parquet.py:116-122` 内联复制 canonical JSON 选项 | 由 `V2-P0A-005` 的 lint 门覆盖 | D2 |
| `V2-P0B-012` | 解除 `storage/` 反向依赖，消除 `storage/batch`↔`runtime/batch` 循环 | 结 | 001,003 | 5 处反向 import；`runtime/batch.py:3` 是全包唯一 `from __future__ import annotations`，`:68/:97` 是唯一两处函数内 domain import | lint 门 + 导入顺序测试 | D2 |
| `V2-P0B-013` | 共享测试 fixture 层（建 `conftest.py`；当前全库无 conftest，builder 重复 5–8 次，`FakeTransport` 有 3 个互不兼容实现） | 测 | — | 21 处模块级冻结常量，两个不同小时并存 | 迁移现有 105 测试至共享 fixture | T1, S85 |
| `V2-P0B-014` | 类型化 look-ahead 异常（当前靠字符串匹配异常消息并把违规吞成计数器） | 测/技 | 013 | `backtest/replay.py:142-146` | 单元：违规抛类型化异常而非计数 | T8, S93 |
| `V2-P0B-015` | 索引与外键修复 | 技 | 004 | 只有 `SQLiteRunRepository` 开 `PRAGMA foreign_keys`，另 7 个 store 共用同文件不开；缺 `checkpoints(run_id)`、`portfolio_transitions(subject)`、`research_reports(subject)` 索引 | 集成：FK 违规被拒 | — |
| `V2-P0B-016` | `web/src/types.ts` 从 schema 生成 + 漂移测试（手工维护且已漂移：缺 `horizon`、缺 `mode`） | 测/产 | 002 | `web/src/types.ts:29-37,44-47` | CI：生成物与 schema 一致 | T14 |

**闸门**：迁移机制能从一份 v1 卷升级并回滚；只有一个组装根；`conftest.py` 就位且 105 测试已迁移；look-ahead 抛类型化异常；`code_commit`/`config_digest` 为真值；分层 lint 全绿。

---

## P1 — 面板数据平面（17 issues）

| ID | 标题 | 类型 | 依赖 | 关键文件 / 证据 | 测试缝 | PRD |
|---|---|---|---|---|---|---|
| `V2-P1-001` | `panel/` 骨架：`dataset/year/` 分区 Parquet + **持久** DuckDB catalog | 技 | P0B | 现状：每 append 一个扁平小文件、`glob("*.parquet")` 全扫、每查询新建 `":memory:"` 连接、每行 1 次 JSON 解析 + 3 次序列化 + 3 次 SHA-256 | 集成 + 性能预算 | S91, D31 |
| `V2-P1-002` | 列式批次契约（`ProviderBatch` 逐行 hash 在面板规模不可用） | 结 | 001 | `providers/base.py:92-96,139,143-150` | 契约 | S91, D32 |
| `V2-P1-003` | 数据目录与就绪合同 | 产 | 001 | 无先例 | 集成 + REST | S8, D5 |
| `V2-P1-004` | 数据集①`trade_cal` —— 一切时间对齐的前提 | 技 | 002 | 当前日历只有"周一到五"近似（`scripts/generate_replay_corpus.py:19-26`），无节假日 | 契约 + 未来数据 fail-closed | S12 |
| `V2-P1-005` | 数据集②`stock_basic`(含 `list_date`/`delist_date`) + `namechange` | 技 | 004 | 生存偏差与 ST 历史 | 同上 | S10 |
| `V2-P1-006` | 数据集③`adj_factor` —— **没有它所有收益率都是错的** | 技 | 004 | — | 同上 | S12 |
| `V2-P1-007` | 数据集④`daily` + `daily_basic` | 技 | 006 | 现有 `kind="daily"` 记录被 `evidence/builder.py:88-90` 拒绝，本 issue 改道面板 | 同上 | S4 |
| `V2-P1-008` | 数据集⑤`suspend_d` + `stk_limit` | 技 | 007 | 接入现有 `AShareExecutionPolicy` | 同上 | S47, S55 |
| `V2-P1-009` | 数据集⑥`index_weight`（沪深300/中证500/中证1000） | 技 | 005 | 实测可用（§6），无需降级 | 同上 | S11 |
| `V2-P1-010` | 数据集⑦行业分类历史 | 技 | 005 | 中性化前提；`index_classify`+`index_member_all` 实测可用（§6） | 同上 | S11 |
| `V2-P1-011` | 数据集⑧`fina_indicator` + 三大报表（取 `ann_date`、`f_ann_date` **与 `update_flag`**） | 技 | 004 | **已落地（§7）**：修正版本共享相同的 `ann_date`；`fina_indicator` 连 `update_flag` 都没有且 81.7% 的键有多行 —— 消歧改为「合并同值 + 逐字段拒答」，修正时刻具名披露为不可知 | 契约 + 修正时钟专项 + 重复版本消歧 | S6, S9 |
| `V2-P1-012` | 面板体检报告（缺失/过期/重复/被修正） | 产 | 003 | **已落地**：`panel_doctor.py` 聚合已有零件，不新建诊断。四类之外补两类 —— `inconsistent`（跨数据集才看得见）与 `unanswerable`（问题问不出来）。新鲜度按发布频率分四档：日频取所给日历自身的最长休市 + 1 天，月频取最宽的月末间隔，季频取 10-31→次年 4-30 的法定披露间隔（182 天），事件驱动**不设事件钟阈值**、改报 `fetch_age`。「重复/被修正」定级为 `notice` 而非 `warning`（§7 实测 `fina_indicator` 81.7% 的键多行，定成缺陷会在每个健康分区上报警） | 集成：人为注入缺陷可被报出 | S13 |
| `V2-P1-013` | fail-closed 依赖门（失败的日度数据集显式阻塞下游） | 技 | 012 | **已落地**：`panel_gate.py` 消费体检报告，不新增任何诊断。阻塞写成 `GATE_CODE_BLOCKS` —— 覆盖 20 个码的整表，12 个 `blocking` 与 5 个 `warning` 全阻塞、3 个 `notice` 全放行。**warning 必须阻塞**：缺 ex-rights 行的 `adj_factor` 分区 `ready` 且 `issues == []`（`required_dates` 被结构性 waive），唯一看得见那个洞的是 `return_path_disagreement`（一个 warning），否则 `adjusted_return` 答 −0.530973%（真值 +2.742251%，连符号都反）。**`checks_waived` 两难的解**：15 个数据集里 12 个结构性 waive `required_dates`，一律阻塞则门永远关着、一律放行则门比看起来弱；实测「日频 **且** waive `required_dates`」只有 `adj_factor` 一个（该实测已写成断言），对它改问「本次请求里有没有按会话的跨检查真读过它」，没有则以 `unverified_daily_coverage` 拒绝。**佐证只覆盖被点名的会话，门因此说出自己的宽度**：三条跨检查只跑在 `request.sessions` 上，把洞种在 index 6 时只有点名 6 或 7 会阻塞、点名 index 8/9 全放行（`close_disagreement`、`unexplained_unpriced` 同形），而读侧无法弥补——`panel_ingest` 自己记着这条残差「cannot be closed from the read side」。所以 `cleared` 返回 `ClearedDataset` 记录（年份 + 被佐证的会话 + 仍未决的 caveat 码）而不是裸数据集名，`unverified_daily_coverage` 在被点名会话之外作为 caveat 留在放行单上而非沉默。roadmap 的「日度」限定的是**什么算失败**（新鲜度阈值按发布频率推导），不是**谁的失败算数**。`DependencyClearance` 拒绝 `bool()`/`len()`/迭代（放行时同样拒绝），`cleared` 在阻塞时抛异常，合并形态只在 `cleared_or_none` 这个名字下；`blocks_for`/`blocked_datasets` 按 `GateBlock.datasets` 匹配，跨数据集阻塞的两边都答得出 | 集成：断言阻塞而非空成功 | S14, D5 |
| `V2-P1-014` | 面板 fixture **生成器** —— `.parquet`/`.duckdb`/`.sqlite3` 是发布拦截后缀，fixture 不能签入 | 测 | 013 | `scripts/verify_publication.py:14-28`；可复用 `tests/contract/providers/test_file_provider.py:93-127` 的 DuckDB→Parquet 写法 | 测试基础设施 | S85, T7 |
| `V2-P1-015` | CLI `panel build` / `panel doctor` / `data-check` | 产 | 013 | — | 集成 CLI | S84 |
| `V2-P1-016` | 数据就绪的 REST + SDK 面 | 产 | 003 | — | REST + SDK 等价性 | S83, D23 |
| `V2-P1-017` | **标签契约强类型化**：预测日 → 可交易日 → 收益区间 → 涨跌停/停牌处理，写成契约而非 notebook 约定 | 结 | 004,006,008 | 当前无标签契约；`SignalFrame.horizon` 是自由字符串，收益窗与 `signal.horizon` 无关联校验（`backtest/validation.py:18-19`） | 契约 + 涨跌停/停牌边界用例 | S27, S28 |

**闸门**：8 组数据集各有一个契约测试 + 一个"注入未来数据必须 fail-closed"测试；`panel build --start 2015 --end 2026` 可完整跑通并断点续传；`panel doctor` 能报出人为注入的四类缺陷；性能测试断言面板查询路径上不存在逐行 pydantic 重建或 hash 重算。

**风险**：全历史首次构建耗时以小时计 ⇒ 必须断点续传 + 本地缓存，不要做成一次性长任务。009/010 的积分风险已由 §6 实测排除。仍需在数据目录中记录每个数据集的实际限流。

---

## P2 — PIT 红队闸门（9 issues，必过）

| ID | 标题 | 依赖 | 注入内容 | PRD |
|---|---|---|---|---|
| `V2-P2-001` | 未来披露注入 | P1 | `available_time > as_of` 的披露 | S9, S93 |
| `V2-P2-002` | 后续修正注入（两种形态：`f_ann_date > ann_date`，**以及日期相同仅 `update_flag` 不同**） | P1-011 | 要求按 as-of 返回修正前的值；第二种形态今天无法区分，见 §7 | S9, S93 |
| `V2-P2-003` | 未来指数成分变更注入 | P1-009 | 要求按 as-of 解析成分 | S11, S93 |
| `V2-P2-004` | 未来行业分类变更注入 | P1-010 | 同上 | S11, S93 |
| `V2-P2-005` | 重叠标签检测 | P1 | 要求显式拒绝或标注 | S28, S93 |
| `V2-P2-006` | 复权收益率交叉对账：`adj_factor` 自算 vs `daily.pct_chg` 逐条比对 | P1-006 | 容差外报错 | S94 |
| `V2-P2-007` | 停牌日与涨跌停日收益率处理专项 | P1-008 | 现有测试中 `suspended`/`is_st` 恒为 `False` | S55 |
| `V2-P2-008` | 退市股票必须仍存在于历史股票池 | P1-005 | 生存偏差 | S10 |
| `V2-P2-009` | 参数化注入表 + CI 回归门 | 001-008 | 取代 `test_research_cycle.py:105-131` 的单向量手写 `try/except/else` | T8, D34 |

**闸门（必过）**：001–008 全部通过，零已知严重 look-ahead 违规；该套测试进入 CI 成为 P3/P4 每次提交的回归门。

> **⚠️ 前置警告（2026-08 实测，见 §10）**：不要把这个闸门建在 `ReplayReport.look_ahead_violations` 上 —— 该字段在任何真实调用路径上结构性只能为 0。P2 必须先解决信号可达性问题。

**为什么是独立闸门**：数据错了，因子越多越危险。在错数据上建 20 个因子再推翻，成本远高于这 2 周。这是唯一不允许为赶进度跳过的阶段。

---

## P3 — 因子层（15 issues）

| ID | 标题 | 类型 | 依赖 | 说明 | PRD |
|---|---|---|---|---|---|
| `V2-P3-001` | 版本化因子定义注册表（复用 `domain/_identity.py#stable_model_id`，不另造哈希） | 结 | P2 | 稳定身份 + 版本 + 家族 + 必需字段 + 回看窗 + 方向 | S15, D7 |
| `V2-P3-002` | 面板特征计算引擎；因子观测写面板平面，**禁止**进 `ParquetEvidenceStore` | 技 | 001 | 每观测记录标的/as-of/值/覆盖标记/输入引用/构建 manifest | S17, D7, D31 |
| `V2-P3-003` | 预处理变换：去极值 + 标准化 + 缺失值政策（显式版本化，与原值分离） | 技 | 002 | — | S18, D8 |
| `V2-P3-004` | 中性化：行业 + 市值（横截面回归） | 技 | 003 | 依赖 `V2-P1-010`；行业分类实测可用（§6），做真实行业中性化 | S19, D8 |
| `V2-P3-005` | IC / Rank IC / IC 衰减 / 稳定性 | 技 | 004 | 唯一先例是 `backtest/event_study.py`（纯 stdlib 叶子模块） | S20 |
| `V2-P3-006` | 分组组合收益（含成本，复用 `AShareExecutionPolicy`） | 技 | 005 | — | S21 |
| `V2-P3-007` | 换手 / 覆盖率 / 容量报告 | 技 | 006 | 让统计上好看但不可实施的信号显形 | S22 |
| `V2-P3-008` | 相关性与冗余分析 | 技 | 005 | — | S23 |
| `V2-P3-009` | 因子家族①价值：EP / BP / SP / EPcut | 技 | 004 | — | S16 |
| `V2-P3-010` | 因子家族②质量：ROE / ROIC / 毛利率稳定性 / 应计项 | 技 | 004 | — | S16 |
| `V2-P3-011` | 因子家族③成长：营收同比 / 净利同比 / 同比加速度 | 技 | 004 | — | S16 |
| `V2-P3-012` | 因子家族④动量与反转：20/60/120 日 + 行业相对 + 5 日反转 | 技 | 004 | — | S16 |
| `V2-P3-013` | 因子家族⑤波动与流动性：残差波动 / 特质波动 / 换手率 / Amihud | 技 | 004 | — | S16 |
| `V2-P3-014` | 不可变因子实验制品 + raw/processed/neutralized 三档报告 | 技 | 005-008 | 否则分不清"因子有效"与"暴露没控住" | S24, D8 |
| `V2-P3-015` | 因子的 CLI + REST + SDK 面（`factor run --factor <id> --start --end`） | 产 | 014 | — | S83, S84 |

**闸门**：每个因子同时出三档报告；因子合同测试使用冻结股票池/日历/公司行动/修正，证明 PIT 可见性与确定性取值；P2 红队测试仍全绿。

**风险**：首批因子中大部分 IC 不显著是**正常且有价值**的结果，不要靠调参"救活"。多重检验控制在 P5 才上，故 P3 的 IC 结论只能视为探索性，不得据此宣称发现。

---

## P4 — 候选排序与模型基线（24 issues）

| ID | 标题 | 类型 | 依赖 | 说明 | PRD |
|---|---|---|---|---|---|
| `V2-P4-001` | **破坏性契约变更打包**：mode += `paper`/`daily`；attribution += `model` + 显式残差；`horizon` → 可比枚举。**需身份重写迁移，见 §8** | 技 | P0B-004,005 | `test_schema_export.py:19` 断言 `endswith("/v1")` 按设计会失败，须同步更新；全库无 golden ID 断言，身份漂移需专门补测 | D36 |
| `V2-P4-025` | **给 `RunManifest` 建立内容寻址身份**，或把 `config_digest`/`random_seed` 纳入某个运行级 ID | 技 | 001 | **§9 实测**：这两个字段目前不进入任何内容寻址身份，故「不同配置产生相同决策 ID」在 P0.B 之后依然成立。破坏性变更，且会撞上 §8 的身份重写迁移，两者必须一起设计 | 单元：改 config_digest → 运行级 ID 变 | D11, S30 |
| `V2-P4-002` | `mode` 列化 + 索引（当前埋在 `runs.payload` TEXT，列 paper 运行要全表扫 + 逐行 JSON 解析） | 技 | 001 | `storage/sqlite.py:31-33` | S5 |
| `V2-P4-003` | mode 单一定义源（现有三处独立重复：`domain/run.py:51`、`runtime/engine.py:36`、`cli.py:42-47`；改两处漏一处会全绿通过） | 技 | 001 | 加一条断言三者一致的测试 | D36 |
| `V2-P4-004` | 两段漏斗横截面管线（面板打分 + 硬性可交易过滤，不进 `run_cycle`） | 结 | P3 | 用实测标定 N（起点 100） | S95, D3 |
| `V2-P4-005` | `CandidateRanking` 契约 | 结 | 004 | 股票池/as-of/周期/评分政策/构成 SignalFrame/预测/因子暴露/可交易性/风险标记/manifest；**绝不直接创建订单** | S43-49, D16 |
| `V2-P4-006` | 治理化筛选，取代仅按 confidence 排序的 `ResearchScreener`；顺带拆分 `product/research.py` 的三份职责 | 产 | 005 | `product/research.py:45-80,83-95,98-140` | S50, S51 |
| `V2-P4-007` | 排名对比 vs 上次运行（新增/移除/理由变化） | 产 | 005 | — | S44, S49 |
| `V2-P4-008` | 路由扩展：支持声明特征依赖的 agent —— 当前**无 evidence family 的 agent 永不被路由** | 结 | 005 | `runtime/router.py:12-23` | S38, D15 |
| `V2-P4-009` | `AgentContext` 增加特征/面板句柄（可复用已声明但全库无人用的 `tools/base.py:54-62 ResearchTool`） | 结 | 008 | `agents/base.py:12-20` | S36, S38 |
| `V2-P4-010` | manifest 第三槽：量化模型版本（当前 `model_versions` 被塞 agent id 且写死 `"baseline/v1"`，`prompt_versions` 永远为空） | 技 | 001 | `runtime/engine.py:131-135,160-161` | S40, D10 |
| `V2-P4-011` | `AlphaModel` 契约（与 LLM `ModelProvider` 严格分离，不复用 `models/governance.py` 的 LLM 专用件） | 结 | 010 | `models/base.py:32-40` 是 LLM-JSON 形状，无法表达面板 fit/predict | S25, D10 |
| `V2-P4-012` | 版本化特征矩阵 | 技 | 011 | — | S26 |
| `V2-P4-013` | Walk-forward 切分 + purge/embargo | 技 | 012 | 禁止随机切分 | S27, S28, D12 |
| `V2-P4-014` | 线性/排序基线 | 技 | 013 | — | S29, D13 |
| `V2-P4-015` | LightGBM 基线 + 容器修复（`libgomp1` 装到**运行时** stage；`shm_size`/`tmpfs` 扩容；固定 OMP 线程） | 技 | 014 | `Dockerfile:12,22`；`deploy/compose.yml:11,19,21`（`read_only:true` + 仅 64MB tmpfs 会让 joblib 溢写失败） | S29, D13 |
| `V2-P4-016` | 内容寻址模型制品（训练截止/特征版本/参数/seed/代码版本/内容哈希） | 技 | 015 | 依赖 `V2-P0B-009` 的真 seed 与真 commit | S30, D11 |
| `V2-P4-017` | **预测在结果已知前落库**（不可省） | 技 | 016 | 回溯重算存为独立制品，不能替换原件 | S32, D14 |
| `V2-P4-018` | stale 模型显式弃权 | 技 | 017 | 最小版：过期即弃权，不做完整漂移检测 | S35 |
| `V2-P4-019` | 批量上限提升/分片（1000 上限挡住 5000 标的；32 并发砸单个 SQLite 会 `database is locked`） | 技 | 004 | `runtime/batch.py:58,106`；`api/app.py:111-113` | S43, D22 |
| `V2-P4-020` | 修 O(N²) recovery 写放大（每 agent 后保存**累积**结果并全量 dump+revalidate ⇒ 12 agent = 78 次序列化 + 78 次哈希） | 技 | 004 | `runtime/engine.py:274-292` | D22 |
| `V2-P4-021` | 排序与模型的 REST + SDK + CLI 面（`model-evaluate`、`daily-run`） | 产 | 005,017 | — | S83, S84 |
| `V2-P4-022` | 已知信噪比合成数据集（含已知 alpha / 已知 null 对照） | 测 | 013 | 现有 `scripts/generate_replay_corpus.py:29-46` 是确定性算术，无噪声模型无已知 IC | T9 |
| `V2-P4-023` | **榜单级 tradable-ratio + freshness 闸门**：整榜覆盖率或新鲜度不过线时拒绝出榜，而非出一份看似完整的清单 | 技 | 005 | 现有只有个股级过滤（`V2-P4-004`）与数据集级 fail-closed（`V2-P1-013`），榜单层无闸门 | 集成：不过线时返回显式阻塞态 | S14, S48 |

**闸门**：排序测试覆盖确定性排序、平局政策、弃权、缺失依赖、过期数据、风险/可交易性标记，且每个入选候选证据闭合；模型评估测试用已知信噪比数据验证 walk-forward 切分、purge/embargo、制品身份、前瞻预测落库；契约升版后从 v1 卷迁移的记录仍可读；新 agent 全部经 `run_cycle` 缝验收。

**风险**：`V2-P4-001` 是唯一的破坏性变更窗口。开工前必须把三项变更的完整字段清单写定 —— 审计已列出全部影响点（3 处 mode 定义、`validation.py:45-52` 精确求和校验、5 处硬编码 horizon、5 个 checked-in schema、`web/src/types.ts`、11 个测试文件），漏一项就是第二轮迁移。

---

## P5 — 组合、验证与工作台（24 issues）

| ID | 标题 | 类型 | 依赖 | 说明 | PRD |
|---|---|---|---|---|---|
| `V2-P5-001` | 启发式组合构建政策（分层排序 + 上限裁剪 + 换手预算），报告显式标注 `heuristic, not optimized` | 技 | P4 | 不引入 cvxpy | S52, D18 |
| `V2-P5-002` | `PortfolioOrder` 增加目标权重；`PortfolioLimits` 扩展行业上限/换手预算/现金下限 | 结 | 001 | 现有 `PortfolioLimits` 只有 2 个字段 | S53, D18 |
| `V2-P5-003` | 组合级多日回测（现有 `PortfolioBacktestStep` 强制单标的步，K 只股票要 K 步） | 技 | 002 | `backtest/multi_day.py:22,31-35` | S55 |
| `V2-P5-004` | Paper Portfolio（前瞻模拟，绝不连券商） | 技 | 003 | 复用不可变订单/转换记账 | S57, D19 |
| `V2-P5-005` | **替换占位归因**：删除 `backtest/validation.py:88-90` 的 20/30/50 硬编码与 `:106-116` 的末项吸收残差技巧 | 技 | P4-001 | 末项吸收是当前对账永远通过的原因 | S65, D21 |
| `V2-P5-006` | 归因残差显式化（不静默分摊） | 技 | 005 | `domain/validation.py:45-52` 的 `abs_tol=1e-9` 在多项求和下本已脆弱 | S65, D21 |
| `V2-P5-007` | 多重检验控制（BH）+ 记录被检验假设数 | 技 | 006 | 不可省 | S63, D20 |
| `V2-P5-008` | gross/net 并列 + **cost drag 单列** + 置信区间 + 样本数 | 技 | 007 | 只报 gross/net 会让成本来源不可归因 | S61, S62 |
| `V2-P5-009` | 分段报告（行业/市值/流动性/市场状态）+ 多市场状态 walk-forward + 基准对照（**等权基线**、naive factor、v1 基线三者并列） | 技 | 008 | 等权基线是最容易被跳过也最能证伪的对照 | S59, S60, S64 |
| `V2-P5-010` | 调度原语：持久作业表（next-fire-time + lease/lock + 按交易日幂等键 + catch-up 政策 + 日历依赖 + 崩溃恢复） | 技 | P0B-004 | 全库零调度原语；可泛化 `BatchResearchTask`+`SQLiteBatchTaskStore` 的 durable-task 模式 | S5, S67, D22 |
| `V2-P5-011` | CORS 方法扩展（当前硬编码只允许 GET/POST，v2 若用 PUT/DELETE/PATCH 会被 CORS 挡） | 技 | — | `api/app.py:281-287` | D23 |
| `V2-P5-012` | 请求体流式计量 + 补安全头（当前只看 `content-length`，chunked 可完全绕过；缺 HSTS/COEP/CORP；`cli.py:180` 不传 `--no-server-header`） | 技 | — | `api/app.py:180-196,154-168` | T17 |
| `V2-P5-013` | SDK/CLI 补齐 REST 缺口 | 产 | P4-021 | **`OutcomeValidator` 在 SDK 中完全缺失**；7 项 REST-only（market/events、themes、batch×5、watchlist remove、report get）；CLI 只覆盖 20 个能力域中的 4 个；`evidence build` CLI 不落库而 SDK 落库；limits/criteria 默认值 REST 与 SDK 分叉 | S83, S84 |
| `V2-P5-014` | 前端路由 + 数据层（React Router + TanStack Query；当前依赖只有 react/react-dom，1 个页面，只消费 6/28 条路由） | 产 | — | `web/package.json` | D24 |
| `V2-P5-015` | 页面①数据体检 | 产 | 014, P1-012 | 面板覆盖/新鲜度/缺失修正/就绪状态；**显式 caveat：当前股票池 vs PIT 股票池、公开数据 vs 授权数据** | S73, S48, S72 |
| `V2-P5-016` | 页面②候选清单 + 个股详情 | 产 | 014, P4-005 | 排序/分数/置信度/排名变化/证据链/失效条件/可交易性告警；**每榜标注股票池与数据授权来源** | S74, S75, S78, S72 |
| `V2-P5-017` | 页面③因子与模型实验室 | 产 | 014, P3-015 | 因子定义/IC/分组/衰减/相关性/三档对比/模型样本外指标 | S76, S77 |
| `V2-P5-018` | 页面④组合与验证 | 产 | 014, P0B-010 | 权重/暴露/换手/容量/Paper 净值/归因/分段 | S79 |
| `V2-P5-019` | 判别式 8 态联合扩到全部面板（当前只有 `EvidencePanel.tsx:9` 用；`degraded`/`stale`/`blocked` 在 `web/src` 根本不存在） | 产 | 014 | 其余 3 个面板用 ad-hoc loading/error 布尔 | D25, T14 |
| `V2-P5-020` | 组件隔离测试 + 前端覆盖率门（当前**无任何组件被隔离渲染**；4 个面板都有 `role="alert"` 分支但 `error` 态从未被渲染；`vite.config.ts` 已能收集同级 `*.test.tsx`，无需改配置） | 测 | 019 | `web/vite.config.ts` 无 coverage 键 | T14 |
| `V2-P5-021` | Playwright page object + 4 页金流（当前 1 个文件无 page object，且 `page.route` 不 stub `/backtests/validate` ⇒ e2e 从未走到归因） | 测 | 015-018 | `playwright.config.ts` 已有 chromium + webServer | T15 |
| `V2-P5-022` | 报告导出许可过滤（不导出 Tushare 原始 payload） | 产 | 018 | — | S72, S81, D27 |
| `V2-P5-024` | **buffered / turnover-controlled 对照版本**：默认与无缓冲版并列出报，避免把高换手因子的 gross edge 误读为可执行 alpha | 技 | 001,008 | 全库无缓冲区排序概念 | 集成：缓冲版换手显著低于无缓冲版 | S53, S56 |
| `V2-P5-023` | 测试树重组的三制品同步（`features.csv` 点名全部 34+2 个测试文件 ⇒ 任何移动都要同步 `features.csv`+`summary.json`+ledger，否则两个 CI job 同时红） | 测 | P0A-001 | — | S87 |

**闸门**：归因对账通过且残差显式；广泛搜索记录被检验假设数与多重检验政策；4 页各 8 态有组件测试；Playwright 桌面金流通过（含归因）；后端覆盖率 ≥80% 且前端覆盖率门生效；确定性回放成功。

---

## 1. PRD Story 覆盖矩阵（缺口证明）

PRD 中 83 条 IN / IN-降级 story，逐条落到 issue。

| Story | Issue |
|---|---|
| S1 | `V2-P0A-004` |
| S2, S5 | `V2-P4-001`, `V2-P4-002`, `V2-P5-010` |
| S4 | `V2-P1-007` |
| S6 | `V2-P0A-004`, `V2-P1-011` |
| S7 | `V2-P0A-004` |
| S8 | `V2-P1-003` |
| S9 | `V2-P1-011`, `V2-P2-001`, `V2-P2-002` |
| S10 | `V2-P1-005`, `V2-P2-008` |
| S11 | `V2-P1-009`, `V2-P1-010`, `V2-P2-003`, `V2-P2-004` |
| S12 | `V2-P1-004`, `V2-P1-006` |
| S13 | `V2-P1-012` |
| S14 | `V2-P1-013`, `V2-P4-023` |
| S15 | `V2-P3-001` |
| S16 | `V2-P3-009`…`013` |
| S17 | `V2-P3-002` |
| S18 | `V2-P3-003` |
| S19 | `V2-P3-004` |
| S20 | `V2-P3-005` |
| S21 | `V2-P3-006` |
| S22 | `V2-P3-007` |
| S23 | `V2-P3-008` |
| S24 | `V2-P3-014` |
| S25 | `V2-P4-011` |
| S26 | `V2-P4-012` |
| S27, S28 | `V2-P4-013`, `V2-P2-005`, `V2-P1-017` |
| S29 | `V2-P4-014`, `V2-P4-015` |
| S30 | `V2-P4-016`, `V2-P0B-009` |
| S32 | `V2-P4-017` |
| S35 | `V2-P4-018` |
| S36 | `V2-P4-009` |
| S37 | 已有（`agents/base.py`），回归门在 `V2-P4-008` |
| S38 | `V2-P4-008`, `V2-P4-009` |
| S40 | `V2-P4-010` |
| S41, S42 | 已有（`committee.py` / `SignalFrame`），回归门在 `V2-P4-005` |
| S43 | `V2-P4-005`, `V2-P4-019` |
| S44 | `V2-P4-005`, `V2-P4-007` |
| S45, S46 | `V2-P4-005` |
| S47 | `V2-P1-008`, `V2-P4-005` |
| S48 | `V2-P5-015`, `V2-P4-023` |
| S49 | `V2-P4-007` |
| S50, S51 | `V2-P4-006` |
| S52 | `V2-P5-001` |
| S53 | `V2-P5-002`, `V2-P5-024` |
| S54 | `V2-P5-005`, `V2-P5-009` |
| S55 | `V2-P1-008`, `V2-P5-003`, `V2-P2-007` |
| S56 | `V2-P3-007`, `V2-P5-001` |
| S57 | `V2-P5-004` |
| S58 | 已有，回归门在 `V2-P5-003` |
| S59, S60 | `V2-P5-009` |
| S61, S62 | `V2-P5-008` |
| S63 | `V2-P5-007` |
| S64 | `V2-P5-009` |
| S65 | `V2-P5-005`, `V2-P5-006` |
| S66 | `V2-P0B-010` |
| S67 | `V2-P5-010` |
| S68 | 已有，回归门在 `V2-P0B-004` |
| S69 | `V2-P1-012`, `V2-P5-015` |
| S70 | `V2-P0B-007` |
| S71 | `V2-P0B-006` |
| S72 | `V2-P5-022`, `V2-P5-015`, `V2-P5-016` |
| S73 | `V2-P5-015` |
| S74, S75, S78 | `V2-P5-016` |
| S76, S77 | `V2-P5-017` |
| S79 | `V2-P5-018`, `V2-P0B-010` |
| S81 | `V2-P5-022` |
| S83 | `V2-P1-016`, `V2-P3-015`, `V2-P4-021`, `V2-P5-011` |
| S84 | `V2-P1-015`, `V2-P3-015`, `V2-P4-021`, `V2-P5-013` |
| S85 | `V2-P0B-008`, `V2-P0B-013`, `V2-P1-014` |
| S86 | `V2-P0B-002`, `V2-P5-020`, `V2-P5-021` |
| S87 | `V2-P0A-001`…`003`, `V2-P5-023` |
| S88 | `V2-P0B-004` |
| S89, S90 | `V2-P5-016`, `V2-P5-022`（措辞门）+ 台账文档测试 |
| S91 | `V2-P1-001`, `V2-P1-002` |
| S92 | `V2-P0A-008` |
| S93 | `V2-P0B-014`, `V2-P2-001`…`009` |
| S94 | `V2-P2-006` |
| S95 | `V2-P4-004` |
| S96 | `V2-P0A-001` |
| S97 | `V2-P0A-005`, `V2-P0A-007` |

**未覆盖的 IN story：0。** 推迟至 v2.1 的 6 条（S31、S33、S34、S39，及 S53 因子暴露上限、S54 优化部分）与移出的 4 条（S3、S80、S82、S2 的 Demo 档位）不在本矩阵内，属预期。

四缝 finding → issue 的覆盖矩阵见 `openalpha-cn-v2-seam-audit.md` §5：**103 条 finding，0 条未关闭**（F93 为显式接受的已知限制）。

---

## 2. 只能做一半时的取舍

| 做到哪 | 得到什么 | 全职 |
|---|---|---:|
| P0.A + P0.B | 一个可迁移、可测、有确定性保证的干净地基。**本身不产生研究价值**，但没它后面每层付两倍成本 | 4.5–5.5 周 |
| **+ P1 + P2** | 数据可信、PIT 有测试背书的本地面板。可在 Jupyter 直接做研究 | 11.5–14.5 周 |
| **+ P3 ⇒ 建议的最小可用终点** | 带 15–25 个诊断完备因子的个人研究环境 | **16.5–20.5 周** |
| + P4 | 每日 top-N 候选，每个可点到证据 | 22.5–27.5 周 |
| + P5 | 完整闭环 + 4 页工作台 | 28–36 周 |

**唯一不可接受的取舍：跳过 P2 抢 P3。**
**第二不可接受：跳过 P0.B 抢 P1** —— 没有迁移机制，P4 的契约升版会把已积累的研究记录全部作废。

---

## 3. 双层验收（贯穿全程）

| 层 | 门槛 | 判定者 | 时间 |
|---|---|---|---|
| **工程成功** | 可复现性、证据闭合、就绪检查、恢复、可用工作流、覆盖率、零 look-ahead 违规 | CI 自动判定 | P5 结束 |
| **研究成功** | 预先登记的样本外指标、扣成本增量价值、稳定性、容量、前瞻 Paper Portfolio 观测 | 只能由时间判定 | P5 之后 **6–12 个月** |

**纪律**：任何使用者可见的措辞升级必须由第二层证据触发，绝不由第一层完成度触发。第二层的判定者是你自己在 6–12 个月后回看 `V2-P4-017` 预先落库的那批预测。

---

## 4. 每阶段的台账义务

自 `V2-P0A-001` 起，每个 issue 关闭时：

1. 在台账获得稳定 ID、实现去向（`file#symbol`）、行为测试证据、终态状态
2. `build_feature_coverage.py` 的 **AST 符号校验**必须通过 —— 符号不存在即阻断
3. `acceptance_test` 必须绑定到真实 pytest node id（`V2-P0A-003`）
4. UI 控件、schema、mock 与文档本身**不计**完成（PRD Decision 29）

---

## 5. 待定决策（不阻塞 P0.A 启动）

| # | 决策 | 默认取值 | 若改变则影响 |
|---|---|---|---|
| 1 | 是否继续维护开源分发 | 个人研究优先：Demo 档位、发布扫描、完整迁移测试降级 | 加回约 **+3–4 周**；Demo 冻结数据集须重设计为不含 Tushare 原始数据；影响 `V2-P0A-006` ADR 内容 |
| 2 | ~~Tushare 积分档位~~ | **已解决** —— 2026-07-30 实测 | 见下方 §6 |

## 6. Tushare 能力实测（2026-07-30）

用真实 token 对 P1 全部候选数据集各发一次最小请求，**16/16 返回 `code=0`**。原先标注的积分风险不存在，`V2-P1-009`/`V2-P1-010`/`V2-P3-004` 无需降级。

| 组 | 数据集 | 字段数 | 关键观测 |
|---|---|---:|---|
| ① | `trade_cal` | 4 | 真实交易日历可用，取代周末近似 |
| ② | `stock_basic` / `namechange` | 3 / 6 | **5,534 只在市**；更名史可查（ST/退市重建的前提） |
| ③ | `adj_factor` | 3 | 复权因子可用 |
| ④ | `daily` / `daily_basic` | 11 / 18 | 价量 + 市值/换手/估值 |
| ⑤ | `suspend_d` / `stk_limit` | 4 / 4 | 10 个交易日内 112 条停牌记录 |
| ⑥ | `index_weight` | 4 | **沪深300 单月 300 条**，基准与股票池历史可用 |
| ⑦ | `index_classify` / `index_member_all` | 7 / 11 | **SW2021 L1 共 31 个行业**，行业中性化可做真行业 |
| ⑧ | `fina_indicator` / `income` / `balancesheet` / `cashflow` | 108 / 85 / 152 / 97 | **`balancesheet` 单 `ts_code` 单期返回 2 行 ⇒ 修正版本真实存在**；`V2-P1-011` 实测补充：`balancesheet`/`fina_indicator` 单响应上限 **100 行**（全表最低，`index_member_all` 3,000 的 1/30），`income`/`cashflow` 无法从外部测出上限；四个端点 `ts_code` 均为必填，逗号拼接在 `income`/`balancesheet`/`cashflow` 三个端点上**都**静默返回 0 行（2026-08-09 复测：单只 4/6/5 行 vs 拼接 0/0/0 行），只有 `fina_indicator` 例外，会返回两只股票的行（5 行 vs 9 行） |
| + | `dividend` | 14 | 分红送转 53 条，公司行动可版本化 |

探测方式是一次性脚本（scratchpad，未入库）。`V2-P0A-004` 仍需把它做成 `doctor` 的正式能力，因为限流与积分会随账号变化，且需要在每次 `panel build` 前 fail-closed。

规模含义：全市场 5,534 只 × 约 2,440 交易日 ≈ **1.35×10⁷ 行/字段**，与 `V2-P1-001` 的分区与列裁剪要求一致；`balancesheet` 的 152 字段说明财务面板必须做列投影，不能整表读。

## 7. 修正时钟实测更正（2026-08，Task 6 期间）

原计划假设财务数据的修正时钟可由 `ann_date` 与 `f_ann_date` 的差异导出。**真实数据证伪了这一点。**

用真实 token 探 `balancesheet`（3 只股 × 2022–2025，共 65 行）：

| 观测 | 结果 |
|---|---|
| `f_ann_date >= ann_date` | ~~**0 违例**，假设安全~~ **已被 Task 34 推翻，见下** |
| 两个日期相等的行 | 65 行中 62 行 |
| 同一 `(ts_code, end_date)` 的多版本 | 存在。`000001.SZ` / `end_date=20231231` 返回 **2 行**，`ann_date` 与 `f_ann_date` **均为 20240315**，仅 `update_flag` 为 0 与 1 |

**后果**：仅凭两个公告日期，原始申报与其修正版产生**逐字段相等的 `Timeline`** —— `available_time` 与
`revision_time` 都无法区分二者。下游 `ProviderBatch` 对 `(subject, kind, date)` 无唯一性约束，
两行会共存；PIT 消费者取"当前值"时只能依赖 Tushare 的响应顺序，而那不是契约。
`evidence/builder.py` 的 `revised_after_initial_availability` 质量标记也永不触发。

**已做**：`_announcement_timeline` 的 docstring 记录了实测数字；
`test_announcement_clock_cannot_yet_distinguish_restatement_via_update_flag` 把这个缺口钉成测试，
消歧策略落地时该测试必须被改写而非删除。

**已决策（`V2-P1-011` / Task 34，2026-08-09 全历史实测）**：**两者都不是**。
53 只股票（`stock_basic` 5,539 只中每 104 只取 1，另加 6 只长历史）四个端点全量翻页实测：

| 端点 | 行数 | `(ts_code, end_date, ann_date)` 键 | 多行键 | 其中逐字节相同 | 真正不同 |
|---|---:|---:|---:|---:|---:|
| `income` | 3,836 | 3,201 | 633 | 372 | 259 |
| `balancesheet` | 4,416 | 3,170 | 1,244 | 1,166 | 76 |
| `cashflow` | 3,567 | 2,849 | 718 | 250 | 468 |
| `fina_indicator` | 5,941 | 3,270 | **2,671（81.7%）** | 2,194 | 477 |

- **"取最高 `update_flag`" 不成立**：在真正不同的配对里，`1` 行更完整的次数是 156 / 31 / 284，
  `0` 行更完整的是 10 / 3 / 23，打平 90 / 42 / 161；响应顺序也不是 tiebreak；
  且 `income` 有 **3 个键的两行 `update_flag` 都是 `1`**（`600739.SH` 2024 年报，营收差 4.6%）。
  `fina_indicator` **根本没有 `update_flag` 列**，而 81.7% 的重复都在它身上。
- **落地方案**：`domain/financial_statements.py` 先**合并"存储值完全相同"的行**
  （这不是选版本，是发现只有一个答案 —— 消掉 `fina_indicator` 2,671 个多行键中的 2,194 个），
  剩下的**逐字段拒答**（`ReportFiling.value_of` 抛 `AmbiguousReportError`，
  只对真正不一致的字段抛，其余字段照常回答）。
  差异会打到头条数字上：`income.ebit` 正负号相反、`revenue` 差 4.6%、
  `n_income_attr_p` 差 3.1%、`balancesheet.total_share` 差 2.5%、`fina_indicator.bps` 差 10 倍。
- **修正时刻仍不可知**，因此 `_announcement_timeline` **刻意**让两行的四个时钟逐字节相同，
  `test_announcement_clock_cannot_yet_distinguish_restatement_via_update_flag` 从"记录缺陷"
  变为"钉住决策"，未被改写。`PartitionCoverage.revised_row_count` **看不见任何一条同日修正**
  （它数的是 `f_ann_date` 真正晚于 `ann_date` 的行：全历史下 55 / 59 / 23 / 0 行，
  而不是恒为 0 —— 只有 `fina_indicator` 因为没有 `f_ann_date` 才恒为 0），
  `PartitionCoverage.revisions`（`update_flag` 标签普查）才是能看见同日修正的那一面。

**同批推翻的另一条**：上表的 `f_ann_date >= ann_date`「0 违例」是 65 行窗口的结论，
全历史下 **53 只股票里有 116 行违例**（`income` 3,836 行中 49 行、`balancesheet` 4,416 行中 16 行、
`cashflow` 3,567 行中 51 行），例如 `000001.SZ` `end_date=20060331` 的 `ann_date=20070426`
而 `f_ann_date=20060426`。`Timeline` 拒绝 `revision_time < available_time`，
因此这些真实行在改成 `max(ann_date, f_ann_date)` 之前**会直接抛异常**，数据集根本抓不下来。

## 8. 契约升版会改变内容寻址身份（2026-08 实测）

Task 12 建多版本读取时发现并经评审实测验证：**`schema_version` 是真实字段而非 `computed_field`**，
因此被 `domain/_identity.py#stable_model_id` 的 `model_dump(exclude_computed_fields=True)` 哈希进 ID。

实测对照（同一实例，仅 bump `schema_version`）：

| 契约 | 原 ID | 升版后 ID | 变化 |
|---|---|---|---|
| `SignalFrame.signal_id` | `sig_4b5bef549c176fcadd11da0e` | `sig_d62c79bcf5dc0d4f8fb303b0` | **变** |
| `DecisionLedger.decision_id` | `dec_46314a613cc7cace244e432c` | `dec_76db84ac023d15f80caa75aa` | **变** |
| `ValidationResult.validation_id` | `val_d153fc97d0ec4470040686b0` | `val_2d427eeed0f048ed8a5a432a` | **变** |
| `ProviderRecord.record_id` | `rec_5855b85464c1f5bfe4926677` | `rec_b027492175506e03092880e3` | **变** |
| `EvidenceSnapshot.evidence_id` | `ev_86a4f53ad8f906b5d01fe0c9` | 同左 | 不变 |

`EvidenceSnapshot.evidence_id` 是手写派生且不含 `schema_version`，是唯一安全的反例 ——
也是其余契约应参照的模式。

**对 `V2-P4-001` 的后果**：三项变更里有两项落在 ID 承载契约上
（attribution 改动经 `ValidationResult`、horizon 改动经 `SignalFrame`）。
**不能靠读时透明 upcast** —— 那会静默改变已存储记录的主键，让 `decisions.decision_id`、
`research_memory.decision_id`（UNIQUE）等引用全部失配。

P4 必须写一次**显式的身份重写迁移**：读旧行 → 升版 → 重算 ID → 同事务内更新所有引用它的行。
这不是 Task 12 的范围（Task 12 只建机制），但没有这条记录，P4 会踩。

## 9. `config_digest` 与 `random_seed` 不进入任何内容寻址身份（2026-08 实测更正）

Task 17 的评审驱动真实 `run_cycle`，固定时钟与 `run_id`，逐个变量单独变更后读 `decision_id`：

| 变更 | `decision_id` |
|---|---|
| 无变更，重复运行 | 不变 ✅ |
| 单独改 `code_commit` | **变** ✅ |
| 单独改 `config_digest`（`a*64` → `b*64`） | **不变** ❌ |
| 单独改 `random_seed`（7 → 99999） | **不变** ❌ |

根因：`domain/decision.py` 的 `DecisionLedger` 字段表是
`schema_version, run_id, created_at, agent_outputs, routing_path, risk_decision, final_action,
evidence_ids, signal_ids, code_commit, model_versions, prompt_versions` ——
**没有 `config_digest`，没有 `random_seed`**。这两个只存在于 `RunManifest`，
而全库 `stable_model_id` 仅有 4 个使用者（`ProviderRecord` / `ResearchReport` / `SignalFrame` /
`DecisionLedger`），**`RunManifest` 不在其中，它没有内容寻址身份**。

**后果**：PRD §1.3 B6 原本把「不同配置产生相同决策 ID」列为 `V2-P0B-009` 要解决的问题，
但该任务的硬约束又禁止改 `domain/` 的 ID 派生 —— 所以这一条在 P0.B 之后**依然成立**。

**待办（需新立 issue，建议排在 `V2-P4-001` 契约变更窗口内一并做）**：
给 `RunManifest` 建立内容寻址身份，或把 `config_digest`/`random_seed` 纳入某个运行级 ID。
这是破坏性契约变更，且会触发 roadmap §8 记录的身份重写迁移问题，两者应一起设计。

**这条错误的来源值得记下**：最初的技术审计断言「三者都是 `decision_id` 的输入」，
该说法未经实测就被写进 PRD、审计文档与 Task 17 的 brief，直到评审用实验推翻。
审计 agent 的结论在被当作事实引用前需要核验。

## 10. `look_ahead_violations` 目前不是活的探测器（2026-08 实测）

Task 22 把前视违规的分类从字符串匹配改成了类型化异常，这部分做对了且经变异验证。
但评审在验收时用三层递进实验发现了一件更重要的事：

| 注入方式 | 结果 |
|---|---|
| 直接构造 `ReplayCase`（含不可见证据） | 构造时即被 `validate_point_in_time` 拒绝 |
| `ReplayCase.model_construct()` 绕过后传给 `ReplayCorpus(...)` | pydantic 仍重新校验嵌套模型并拒绝 |
| **同时** `model_construct()` 绕过 `ReplayCase` 与 `ReplayCorpus` | 才进得去，计数正确递增为 1 |

而真实入口 —— `cli.py` 的 `ReplayCorpus.load(path)`、REST 的 `ReplayApiRequest.corpus`、SDK 的
replay 方法 —— **全部从原始数据构建**，必然触发 `ReplayCase` 自己的校验器。
全库 grep 确认没有任何 `model_construct` 式的绕过路径。

**后果**：

1. `tests/replay/test_frozen_corpus.py` 里的 `look_ahead_violations == 0` 是**同义反复** ——
   不论分类逻辑是否正确它都成立。这条断言目前不提供任何保护。
2. **P2 的必过闸门不能建在这个信号上。** 九个注入 issue（`V2-P2-001` 到 `008`）
   若指望通过这个计数器观测违规，会得到恒为 0 的结果而误以为闸门通过。

**这是既有状况，不是 Task 22 引入的** —— 评审对 base commit 验证过，旧的字符串匹配版本
同样够不到。Task 22 只改了分类方式。

**P2 开工前必须先决策**（二选一，或提出第三条）：

- **A**：调整点位，让前视检查不在 runner 上游被重复强制 —— 例如让 `ReplayCase` 保留证据但标记，
  由 runner 做唯一的判定点。这会改变 `ReplayCorpus` 的语义，需要评估对冻结语料的影响。
- **B**：接受这个信号为恒零的纵深防御，**另选一个可达信号**建闸门 ——
  例如直接在 `ResearchEngine.run_cycle` 或面板查询层注入，那里没有上游筛选。

不做这个决策就开工 P2，会得到一个永远绿、但什么都没测的闸门。
