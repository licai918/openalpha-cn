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
| **P3** | 因子层 | 19 | 5–6 周 | 13–15 周 | 首批因子出齐 raw/processed/neutralized 三档 |
| **P4** | 候选排序与模型基线 | 99 | 6–7 周 | 15–18 周 | 契约升版一次完成 + 预测先落库 |
| **P5** | 组合、验证与工作台 | 24 | 6–8 周 | 15–20 周 | 归因对账 + 多重检验 + 4 页可用 |
| | **合计** | **193** | **28–36 周** | **70–90 周** | |

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

**闸门状态（P1 复验后，`V2-P1-019`）——「跑通并断点续传」这一条只满足到年粒度，据实记录而非视为已过：**

- ✅ **`--start 2015 --end 2026` 的接口存在并跑通。** 此前 `panel build --year` 是单值，
  `--year 2025 --year 2026` 只构建 2026 且**无声丢弃** 2025；现在 `--year` 可重复、
  `--start`/`--end` 是闭区间、两种形式互斥、年份由老到新执行。实测：`--dataset trade_cal
  --start 2015 --end 2026` 真实 token 26.6 秒写入 12 个分区（闰年 366 行 / 平年 365 行）。
- ✅ **年粒度断点续传（`--resume`）。** 证据是仓库自己的分区普查而不是进度文件：某年的某个
  目标，其写入的每个会话级数据集都已覆盖到本次构建会取到的最后一个会话时才跳过。
  `--start 2015 --end 2026` 中途失败的损失单位从 12 年降到 1 年，且失败信息会点名已完成的
  年份与继续的命令。实测：`adj_factor --start 2015 --end 2016` 构建后重跑 `--resume`，
  `adj_factor` 请求数为 0。
- ❌ **年内断点续传未做，这是判断而不是遗漏。** `PanelStore` 分区整体写入、没有 append，
  `panel_ingest._session_census` 要求覆盖日历上从 1 月 1 日起的每个开市日，因此「写了一半的
  年」根本无法作为可读分区存在——只能落成第二套磁盘格式，带自己的陈旧性与完整性问题，而它
  最坏的失败形态正是「看起来完整的半截」，即本模块全部守卫存在的理由。缓解措施是
  `TushareProvider._post` 的有界重试（一次瞬时 socket 错误不再作废整年）与 `--resume`。
  一年 `price` 实测约 35 分钟，这是目前单次失败的最大损失。
- ❌ **2015–2026 全量 `price` 尚未真实跑过一次。** 按已测速率约 7 小时 / 约 3.5 万次请求，
  不在本次复验的预算内；已跑通的是 `trade_cal` 的全闸门年段与 `adj_factor` 的两年段。

**P3 前置补充（2026-08-11）——「8 组数据集」这条闸门此前只在契约层成立，构建层缺 8 个数据集：**

- ✅ **`PANEL_BUILD_TARGETS` 从 5 个目标扩到 13 个，覆盖 `providers/tushare.py` 声明的全部
  15 个数据集**（`OA-PANEL-027`）。此前 `namechange` / `index_weight` / `index_classify` /
  `index_member_all` / `income` / `balancesheet` / `cashflow` / `fina_indicator` 有 writer、
  有 loader、有体检检查、有就绪合同，**没有抓取路径**：`panel build --dataset income` 按名字
  被拒，`panel doctor --dataset income` 因此永远报 `partition_missing`。P2 的 002/003/004
  三道闸门与 P3 的 009–013 五个因子家族都建在这批数据集上。
- ✅ **实测（真实 token，2026-08-11）**：`index_classify` 2 请求 → 2014/2021 两个分区
  （359 / 511 行）；`index_member_all` 62 请求 133 秒 → 41 个事件年分区；`index_weight`
  2024 年 36 请求 67 秒 → 21,600 行（12 × (300+500+1000)）；`namechange` 2024 年 1 请求
  → 330 行；`fina_indicator --start 2023 --end 2024` → 公告年 2023/2024/2025 三个分区。
- ⚠️ **量级从小时变成天。** 财报四表 `ts_code` 必填且无横截面，全市场一年一表 = **5,881 次
  请求**（实测 registry 规模），四表一年 ≈ 2.35 万次，`--start 2015 --end 2026` ≈ 28.2 万次。
  单次请求实测 1.1–4.6 秒；真跑一次全市场 `cashflow --year 2024`，前 295 次请求耗时 529 秒
  = **1.79 秒/请求**，命令自己给出的 eta 是 10,008 秒（**2 小时 47 分**，一个数据集一年）。
  按此推算 `--start 2015 --end 2026` 的四表回补 ≈ **140 小时**，即**天**而不是小时；账号
  500 次/分钟的配额（对应 9.4 小时）在该时延下**不是**瓶颈。
  本次交付的是把量级**说出来**（每个抓取循环发第一个请求前在 stderr 打 `BUDGET`）、进度步长
  随规模缩放、`--subject` 缩小扫描、以及 `index_weight` 与三个公告年财报目标的 `--resume`；
  并发**未做**，理由是 `TushareProvider` / `UrllibTushareTransport` 没有线程安全声明，且全仓
  测试注入的脚本化 transport 及其顺序断言都不是线程安全的——那是对测试接缝的改动，不是对本
  命令的改动。
- ⚠️ **`fina_indicator` 结构上无法断点续传。** 它的窗口过滤报告期、行按公告日归档，一个公告年
  至少由两个报告期年拼成，所以在全部请求的报告期年抓完之前它写不出任何分区。`--start`/`--end`
  收窄是唯一的杠杆，代价由「不允许缩小已存公告年」的拒绝守住（实测：2023–2024 建出的 2024
  分区 9 行，单跑 `--year 2024` 只能给出 7 行，被拒）。

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
| `V2-P2-007` | 停牌日与涨跌停日收益率处理专项 | P1-008 | 撮合侧（`backtest/execution.py`）与标注侧（`domain/labels.py`）对"该会话能否按收盘成交"各自作答且从未比对；用同一存储面板驱动两条路径求一致，三处结构性不一致写入 `KNOWN_EXECUTION_LIMITATIONS` 而非断言掉 | S55 |
| `V2-P2-008` | 退市股票必须仍存在于历史股票池 | P1-005 | 生存偏差 | S10 |
| `V2-P2-009` | 参数化注入表 + CI 回归门 | 001-008 | 取代 `test_research_cycle.py` 的单向量手写 `try/except/else`（该向量已成为注入表首行，原处已删） | T8, D34 |

**闸门（必过）**：001–008 全部通过，零已知严重 look-ahead 违规；该套测试进入 CI 成为 P3/P4 每次提交的回归门。

> **⚠️ 前置警告（2026-08 实测，见 §10）**：不要把这个闸门建在 `ReplayReport.look_ahead_violations` 上 —— 该字段在任何真实调用路径上结构性只能为 0。P2 必须先解决信号可达性问题。

**为什么是独立闸门**：数据错了，因子越多越危险。在错数据上建 20 个因子再推翻，成本远高于这 2 周。这是唯一不允许为赶进度跳过的阶段。

---

## P3 — 因子层（18 issues）

| ID | 标题 | 类型 | 依赖 | 说明 | PRD |
|---|---|---|---|---|---|
| `V2-P3-001` | 版本化因子定义注册表（复用 `domain/_identity.py#stable_model_id`，不另造哈希） | 结 | P2 | 稳定身份 + 版本 + 家族 + 必需字段 + 回看窗 + 方向 | S15, D7 |
| `V2-P3-002` | 面板特征计算引擎；因子观测写面板平面，**禁止**进 `ParquetEvidenceStore` | 技 | 001 | 每观测记录标的/as-of/值/覆盖标记/输入引用/构建 manifest | S17, D7, D31 |
| `V2-P3-003` | 预处理变换：去极值 + 标准化 + 缺失值政策（显式版本化，与原值分离） | 技 | 002 | — | S18, D8 |
| `V2-P3-004` | 中性化：行业 + 市值（横截面回归） | 技 | 003 | 依赖 `V2-P1-010`；行业分类实测可用（§6），做真实行业中性化 | S19, D8 |
| `V2-P3-005` | IC / Rank IC / IC 衰减 / 稳定性 | 技 | 004 | 唯一先例是 `backtest/event_study.py`（纯 stdlib 叶子模块） | S20 |
| `V2-P3-006` | 分组组合收益（含成本，复用 `AShareExecutionPolicy`） | 技 | 005 | 已交付 `backtest/factor_portfolio.py`（纯 stdlib 叶子，直接吃 `005` 的 `ICCrossSection`，所以 IC 与分组收益必然同一录取样本）。**权责分工**：毛收益取 `OutcomeLabel.realized_return`，**绝不**由两笔成交价反推（那正是 Task 30 实测 −0.5310% 对 +2.7422% 的那条路）；费用取 `AShareExecutionPolicy` 的两个 `ExecutionResult`；能否建仓由**两个契约共同**判定、标注在先。被拒成交**不记 0**，出组并按自身 `HoldingOutcome` 计数，`PortfolioCensus` 强制加总。分组按平均秩（并列同组）、组数与每组下限**均为声明值无默认**，空组是三个覆盖码而非修补。多空价差交付但**明确不是可执行组合**：策略无做空侧、无融券数据集，且空头往返正是 `KNOWN_EXECUTION_LIMITATIONS` 已实测两契约不一致的那对判定。不发累计曲线（重叠窗口），见下方小节 | S21 |
| `V2-P3-007` | 换手 / 覆盖率 / 容量报告 | 技 | 006 | 让统计上好看但不可实施的信号显形。已交付 `backtest/factor_tradeability.py`（第三个纯 stdlib 叶子）。**换手**：对多头组维护持仓态，**再平衡频率不是新参数** —— 就是调用方那串 `as_of` 的间距，本模块测量它；窗口重叠时持仓态不存在，报 `overlapping_schedule`。换手报**名字**与**钱**两读（`006` 等预算 + 手数取整让两者必然不同）。`006` 的费用是上界、`round_trip_cost - avoided_cost` 是下界，省下的腿全部取自已有 `ExecutionResult`，不模拟任何一笔单。**不发滚动收益**（空隙会话不在任何标注窗口里，且连乘正是 `006` 拒绝的那件事）。**覆盖率**：四步漏斗 `universe → valued → admissible → scored → held`，四步分属因子引擎 / 该档准入 / 标注契约 / 撮合策略，两张档位表 `import` 且**两张都吃劲**。**逐组分解**是验收标准的仪器，且在 `unfillable_after_execution` 下**照样在**（由「切法重算 ∸ 拒绝名单」推出）。**容量**：一个声明的 `participation_cap`（无默认）+ 算术，是**约束不是冲击模型**；`min` 绑定被 `006` 的等预算逼出来、`capital_multiple < 1` 就是已超容（实测 `000569.SZ` 2001-01-02 给 0.658） | S22 |
| `V2-P3-008` | 相关性与冗余分析 | 技 | 005 | 已交付 `backtest/factor_redundancy.py`（`005` 之后第二个纯 stdlib 叶子模块）：横截面值/秩相关、IC 序列相关三读，符号由**两个**因子的 `direction` 定向、冗余按**幅值**判定。**算术恒等式与实证相关分开**：`SharedInputs` 从 `required_fields` **算**出来（171 对里 42 对共列、72 对共数据集、16 对**声明完全相同**），`FactorIdentity` 则**声明后再对数据求值**，只有 `verified` 才配得上 `arithmetic` 判决；`V2-P3-012` 那条 `1 + m20 == (1 + m15)(1 + r5)` 在不跳的动量上实测残差 `4.4e-16`（verified），在**出厂**动量上 `1.7e-01`（refuted）。冗余阈值**无默认值**、由调用方声明；`undeclared_lockstep` 用 `round(abs(r), 15) == 1.0` 这条**舍入边界**（不是阈值）兜底。样本下界是 **4** 而非 IC 的 3：`n=3` 时秩相关只能取 `±0.5 / ±1`，任何阈值都判不出东西 | S23 |
| `V2-P3-009` | 因子家族①价值：EP / BP / SP / EPcut | 技 | 004 | 已交付 EP / BP / SP（本仓库第一批双轴出厂因子）；**EPcut 由 `V2-P3-017` 补齐**（`deducted_earnings_yield_ttm`，第 20 个出厂因子），当时未交付的原因——扣非净利不在任何一个统计投影里——是投影边界而不是上游缺失，见下方小节 | S16 |
| `V2-P3-010` | 因子家族②质量：ROE / ROIC / 毛利率稳定性 / 应计项 | 技 | 004 | 已交付四个：`return_on_equity_ttm` / `return_on_capital_ttm` / `gross_margin_stability` / `accruals_ttm`，**本仓库第一批只在报告期轴上的出厂因子**；ROE **不读** `fina_indicator.roe`，理由与实测见下方小节 | S16 |
| `V2-P3-011` | 因子家族③成长：营收同比 / 净利同比 / 同比加速度 | 技 | 004 | 已交付 `revenue_yoy` / `net_profit_yoy` / `revenue_yoy_acceleration`（本仓库第一批**只读 filing、不读价格**的出厂因子）。同比是**累计对四季度前的累计**（`window[-1]/window[-5]-1`，正是本文件 M-2 论证的那条），加速度是**两条相隔一年的同比之差**（`5 / 5` 与 `9 / 9`）；**全家族不调用 `_trailing_twelve_months`** —— 它按窗口末端对齐找年末，在 `N=8` 上会自信答错，见下方小节 | S16 |
| `V2-P3-012` | 因子家族④动量与反转：20/60/120 日 + 行业相对 + 5 日反转 | 技 | 004 | — | S16 |
| `V2-P3-013` | 因子家族⑤波动与流动性：残差波动 / 特质波动 / 换手率 / Amihud | 技 | 004 | — | S16 |
| `V2-P3-014` | 不可变因子实验制品 + raw/processed/neutralized 三档报告 | 技 | 005-008 | 否则分不清"因子有效"与"暴露没控住"。已交付 `backtest/factor_experiment.py`（`005` 之后第五个纯 stdlib 叶子，消费前四个而不重造它们）。**三行不够，格子才是答案**：`TierAttribution` 是一等记录，跑在**声明的**格子上 —— 三个档位步（`raw→processed`、`processed→neutralized`、`raw→neutralized`）×两个**已被上游定过号**的统计量（`ICSummary.mean_ic`、`QuantilePortfolioSummary.mean_spread`），每格带两档自己的数、比值、以及一个六成员闭集判决，判在一条**无默认值的** `retention_floor` 上；`no_baseline` 与 `removed` 分开（前一档就没赚过钱的因子没有「被拿走」这回事）、`reversed` 与 `amplified` 分开。**不可变是两件事且两件都强制**：`experiment_id` = `stable_model_id`(四个上游 spec + 线 + `code_commit` + as_of 与三档来源 build 的 `set_digest`)，`content_digest` = 同一函数打整份文档，所以**同一个 `experiment_id` 下两个 `content_digest` 不是两个实验而是不可复现**，`refuse_a_restated_experiment` 照 `_refuse_to_drop_a_stored_build` 的形状拒绝它、但放行完全一致的重算；`open_experiment` 重算摘要，落盘后被改过一个字节的文档**读不回来**而不只是「不一样」。`built_at` 与 `note` 不进任何摘要，由一条读 `inspect.signature` 的审计与 `IDENTITY_EXEMPT_PARAMETERS` 对账 | S24, D8 |
| `V2-P3-015` | 因子的 CLI + REST + SDK 面（`factor run --factor <id> --start --end`） | 产 | 014 | — | S83, S84 |
| `V2-P3-016` | **指数点位序列数据集 + 面板可达的市场收益**（`V2-P3-013` 的残差/特质波动的硬前置，见下方小节） | 技 | P1 存储契约 | 已交付：`index_daily` 成为第 16 个摄取数据集（每 `--year` 三次请求、分区年份即 `--year`、cadence `daily`），`FactorWindow` 增加 `shared` 通道（`SHARED_SUBJECT_DATASETS`），并在其上出厂第 21 个因子 `residual_vol_60`；20 个旧 `factor_id` 逐位不变。特质波动**不单独出厂**：本面板只有一条解释序列，第二个名字会是两个 id 对一个数。立项理由（`013` 实测）：15 个 descriptor 里**没有任何指数点位**（`index_weight` 是成分权重不是点位），且 `FactorWindow` 是单标的的 —— 求值器**按类型**够不到市场序列 | S16 |
| `V2-P3-017` | **扣非净利列进入统计投影 + EPcut**（`V2-P3-009` 的第四个因子，见下方小节） | 技 | `V2-P1-011` 存储契约 | 已交付：`fina_indicator` 的投影 11→12 列，新增 `profit_dedt`，并在其上出厂第 20 个因子 `deducted_earnings_yield_ttm`。**加哪一列不是偏好而是实测**——`income` 根本不服务这一族（85 个字段里一个都没有，且点名请求会被**静默丢弃**，放进 `income` 投影会让每一次 `income` fetch 都被 `checked_response_fields` 拒绝）；同一批原始行读五遍（101 票 / 6,138 filing，另一组不相交 101 票 / 5,980 filing）：加 `profit_dedt` 与 `dt_eps` 折叠行数与歧义 filing 数**一字未变**，加 `dt_netprofit_yoy` 则把 4 个（另一样本 1 个）filing 从折叠挪进歧义 —— **那条 limitation 的条件句对一列成立、对旁边一列不成立，只有实测能分开**；五种投影下既有 11 列的逐列拒绝数全部逐位相同。代价：每个已存 `fina_indicator` 分区以 `field_missing` 拒读并重取、以真实行钉住字段列表的契约测试同改、以及 `profit_dedt` 自己 1.075% / 0.769% 的拒绝率（EP 的列是 0.189% / 0.459%） | S16 |
| `V2-P3-018` | **`FactorCoverage` 第六个码：把「这只票的这次 filing 有歧义」变成单票覆盖码而不是整 build 拒绝**（`V2-P3-009`..`011` 共用的墙，见下方小节） | 技 | `V2-P3-002` 存储契约 | 已交付 `ambiguous_filing`，插在 `insufficient_history` 与 `input_missing` **之间**（该位置就是 `_classify` 的判定优先级，由一条读 AST 的审计对账）。标记按 `(subject, period)` 记在 `_DatasetReading` 上，只对**窗口真的覆盖到那一期**的票生效；会话轴一字未动，第二行照旧拒绝。**schema 迁移**：manifest 分区 27→28 列、transform manifest 34→35 列，旧分区在 readiness 上以 `field_missing` 拒读而不是错位解码 —— 因子分区是派生物、`manifest_id` 使其可重建，`storage/migrations.py` 只管 `state.sqlite3`。**身份**：`transform_id` 移动（覆盖码词表就是 `MissingValuePolicy` 的字段集，在 `FactorTransformSpec` 的哈希载荷里），19 个 `factor_id` 一个没动，两边都用 `04c45b8` 的字面量钉住 | S16 |
| `V2-P3-019` | **给已存因子截面盖上它自己答案的内容地址**（P3 产品验收的 Critical-1，见下方小节） | 技 | `V2-P3-002`/`003`/`004` 存储契约 | 实测：把 `factor_obs_reversal_1d_v1/2026/data.parquet` 全部 16 行的值翻号、删掉 `runtime/experiments`、跑真的 `openalpha factor run` —— `mean_ic` 从 `+1.0` 变成 `-1.0`、`mean_spread` 跨过零，`experiment_id` **逐字节相同**，退出码 0，全链无拒绝。根因三条、各自封堵一条：① build manifest 对**输入**和**标的集合**取摘要、从不对**答案**取摘要 —— `FactorBuildManifest.observation_digest` 及其两个孪生 `processed_observation_digest` / `neutralized_observation_digest` 补上，且是**进身份的**字段而不是 `FactorInputProvenance` 那种「记录但不寻址」（后者会被篡改者与它描述的值一起改掉，进了 `manifest_id` 才由解码器已有的身份自检来守）；② 唯一可能开火的守卫「同 `experiment_id` 两个 `content_digest`」**是有状态的**，只在本机先跑过诚实版本时才生效 —— 面板上的封缄是无状态的；③ `panel doctor` 按**名字**拒绝因子数据集（无发布节奏），P2 建的 fail-closed 闸门止步于原始数据平面。**不给 `DATASET_CADENCE` 加条目**（派生名按因子铸造、不可枚举），改为一条 `derived` 节奏 + 谓词，并新增两个 **blocking** 码 `factor_seal_broken` / `factor_build_unaddressed`。**分层决定了设计**：`panel_doctor` 的兄弟集被等号钉死，不能 import 它审计的三个平面，所以 `cross_section_digest` 落在 `domain/`，`FACTOR_PLANE_SEALS` 以数据声明平面形状、由一条同时 import 两边的运行期审计对账 | S16, D8 |

**闸门**：每个因子同时出三档报告；因子合同测试使用冻结股票池/日历/公司行动/修正，证明 PIT 可见性与确定性取值；P2 红队测试仍全绿。

**风险**：首批因子中大部分 IC 不显著是**正常且有价值**的结果，不要靠调参"救活"。多重检验控制在 P5 才上，故 P3 的 IC 结论只能视为探索性，不得据此宣称发现。

---

## P4 — 候选排序与模型基线（25 issues）

| ID | 标题 | 类型 | 依赖 | 说明 | PRD |
|---|---|---|---|---|---|
| `V2-P4-001` | ~~**破坏性契约变更打包**：mode += `paper`/`daily`；attribution += `model` + 显式残差；`horizon` → 可比枚举。**需身份重写迁移，见 §8**~~ **已完成** | 技 | P0B-004,005 | `test_schema_export.py:19` 断言 `endswith("/v1")` 按设计会失败，须同步更新；全库无 golden ID 断言，身份漂移需专门补测。**已完成（2026-08-24 合并时实测结清，此前三半已分别交付但本行未标记）**：三半各自量过 —— `RUN_MODES` 读出 `['live','replay','backtest','paper','daily']`；`AttributionTerm.category` 是 `Literal['rule','factor','agent','model']` 且 `unexplained_return: float = 0.0` 是显式残差字段，`validate_window_and_attribution` 把它算进对账等式；`domain/horizon.py` 提供 `ResearchHorizon`/`HorizonUnit`。**本行自己写的两条验收标准也都已落实**：(a) `endswith("/v1")` 已被替换 —— 换成的不是 `endswith("/v2")`（那只是把同一个会过期的字面量往后挪一格，且能让一个名为 `decision-ledger-v1.json` 的文档装着 `decision-ledger/v2` 而照样通过），而是一个不随版本号过期的三方等式；(b) 「全库无 golden ID 断言」已不成立 —— `GOLDEN_SIGNAL_IDS`（按 horizon 参数化）、`GOLDEN_RUN_MANIFEST_ID = run_b046b7e50079ee325dee4929`、`GOLDEN_ARTIFACT_ID`，且另有 `SUPERSEDED_RUN_MANIFEST_ID` 专钉迁移必须重写的那一批，配套「每个入址字段都必须移动 ID / 每个未入址字段都必须不移动」两个方向的参数化断言 | D36 |
| `V2-P4-025` | ~~**给 `RunManifest` 建立内容寻址身份**，或把 `config_digest`/`random_seed` 纳入某个运行级 ID~~ **已完成** | 技 | 001 | **§9 实测**：这两个字段目前不进入任何内容寻址身份，故「不同配置产生相同决策 ID」在 P0.B 之后依然成立。破坏性变更，且会撞上 §8 的身份重写迁移，两者必须一起设计。**已完成（2026-08-24 合并时实测结清）**：`domain/run.py` 的 `run_manifest_id` 经 `stable_model_id` 生成、受 `CONTENT_ADDRESS_PATTERN` 约束，`DecisionLedger.run_manifest_id` 把它带到台账上；`test_a_rerun_at_a_different_wall_clock_reproduces_the_same_address` 钉住「同一声明换一个墙钟仍得同一地址」 | 单元：改 config_digest → 运行级 ID 变 | D11, S30 |
| `V2-P4-002` | ~~`mode` 列化 + 索引（当前埋在 `runs.payload` TEXT，列 paper 运行要全表扫 + 逐行 JSON 解析）~~ **已完成** | 技 | 001 | `storage/sqlite.py:31-33`。**已完成（2026-08-24 合并时实测结清）**：`storage/sqlite.py` 的 `RUNS_MODE_COLUMN` 是 `runs.payload` 的**派生投影**（`$.mode`）而不是第二份拷贝 —— 这是关键的形状选择：第二份拷贝会与 payload 漂移，而投影在 `payload` 更新时自动重算；另有 `storage/migrations.py::_audit_runs_mode_projection` 用 Python 重算同一值来对账 | S5 |
| `V2-P4-003` | ~~mode 单一定义源（原述：三处独立重复 `domain/run.py:51`、`runtime/engine.py:36`、`cli.py:42-47`；改两处漏一处会全绿通过）~~ **已完成，由 `V2-P4-001` 关闭** | 技 | 001 | `V2-P4-001` 的破坏性契约变更把 mode 收敛到 `domain/run_mode.py`：全仓 `class RunMode` **只有一处**（`run_mode.py:52`），`cli.py`/`domain/run.py`/`domain/run_request.py`/`storage/sqlite.py`/`storage/migrations.py`/`backtest/replay.py` 六处全部 import 它，`cli.py` 与 `runtime/engine.py` 内**无残留 mode 字面量**。本行原开的验收条件（「加一条断言三者一致的测试」）由 `tests/unit/domain/test_run_mode.py::test_no_other_module_declares_the_mode_set` 满足，且强于原条件：它按 AST 取**排除 docstring 后**的字符串字面量（本仓文档量大，算进 prose 会让审计不可证伪），断言「拼出 ≥3 个 mode 名的模块集合」**恰好等于** `{run_mode.py, run.py}`；唯一豁免 —— `run.py` 的 `RunManifestV1` 冻结快照必须说出它冻结了什么 —— 是按**集合相等**授予的（`== {live, replay, backtest}`，即 v1 的三个），不是按文件名放行，所以把今天的五个 mode 抄回 `run.py` 的人，正好在他想用的那条豁免上失败。**本行此前是过期文档**：`001` 落地后未回填，P4 剩余数因此多算了一条 | D36 |
| `V2-P4-004` | ~~两段漏斗横截面管线（面板打分 + 硬性可交易过滤，不进 `run_cycle`）~~ **已完成** | 结 | P3 | 已交付 `backtest/cross_section.py`（第十个纯 stdlib 叶子）。**「不进 `run_cycle`」是结构而非承诺**：`backtest-studies-reach-no-composition-root` 禁 `runtime`（`run_cycle` 所在），`backtest-studies-touch-no-store` 禁 `storage`，新模块到达即入两条契约。**N 的实测标定**：绑住 N 的不是可交易性折损、也不是批次上限，而是出厂 winsorization —— `_quantile` 把 q 分位放在 `(n-1)q`，故严格高于上界的 `(n-1) - floor((n-1)q)` 个值被赋同一个界，processed 档上它们同值同 z-score，`N ≤` 该块的 top-N 不是按分数选而是按 tie-break 选。2026-08-14 全市场实测（5,545 上市 / 5,540 有价）：turnover_rate n=5,540 块 56、ps_ttm n=5,538 块 56、total_mv n=5,540 块 56、pb n=5,498 块 55、pe_ttm n=4,002 块 41，五列全对。**故全市场下界 N ≥ 57**：起点 100 越过它 44 名，而 PRD §3.2 建议区间的下端 **50 落在块内**（本行更正）。neutralized 档更隐蔽：同一 41 名在 processed 上只有 **1** 个不同值，中性化后有 **41** 个，跨全截面残差极差的 71.2%，名次 1/2/3/4/7…2,069，中性化 top10 里有 7 个来自该块 —— 那 41 个次序完全由行业均值与 log 市值决定，因子项是同一个数。**硬性过滤是正确性闸门不是缩量闸门**：同日 5,543 只按板块最小手数走真 `AShareExecutionPolicy` 买入，5,535 只成交（5 只一字涨停、3 只无 bar），只挡掉 0.14%，5,540→100 的缩量全部来自那一刀。不再声明第二条截面下界（两个派生 spec 已声明 `min_cross_section=100`）；窄样本给 `cut_exceeds_the_cross_section` 而不是把三只票当成一次筛选 | S95, D3 |
| `V2-P4-005` | ~~`CandidateRanking` 契约~~ **已完成** | 结 | 004 | 已交付 `backtest/candidate_ranking.py`（第十一个纯 stdlib 叶子）。十项各有唯一来源：**股票池** = `universe_digest`/`universe_count`（`set_digest`，由 `build_ranking_manifest` 算出而非接受，并与 `CrossSectionFunnel.scores.universe_count` 对账）；**as-of** = 一个时刻，漏斗与每个构成信号都必须相同；**周期** = 本契约新增（横截面本身没有周期），走 `COUNTABLE_HORIZON_PATTERN`；**评分政策** = `004` 的 `ShortlistSpec` 直接内嵌（不摘要），故每个权重/因子/档位自动进身份；**构成 `SignalFrame`** = **每个候选一个**，不是清单一个；**预测** = 声明式缺席（`V2-P4-011`–`017` 未落地，`models/base.py` 是 LLM-JSON 形状），但「全有或全无」规则先立；**因子暴露** = **两样东西都带**；**可交易性** = `AShareExecutionPolicy` 自己的 `ExecutionResult` + 漏斗两张普查；**风险标记** = 本契约的**闭集** + `SignalFrame.risk_flags` 原样并列；**manifest** = 清单一份 `CandidateRankingManifest`，候选各带 `run_manifest_id`（`V2-P4-025` 的内容地址，一次继承全部已声明运行输入，而不是复制 `config_digest`/`random_seed`）。**「绝不直接创建订单」由四条 `lint-imports` 契约强制，第四条是本次新增。**`004` 依赖的两条禁 `storage`/`runtime`/`agents`/`decisions`，挡住的是**落库**与**够到 `run_cycle`**，挡不住**构造**：`domain/portfolio.py` 声明 `PortfolioOrder`，是每个 `backtest/` 研究都可 import 的纯数据模块。新增 `ranking-creates-no-portfolio-order` 只作用于本模块，禁 `domain.portfolio`/`backtest.portfolio`/`backtest.multi_day` —— 全仓声明或模拟订单意图的三处。写成第四条而不是往 `backtest-studies-touch-no-store` 里加三个名字，是因为那条契约的 source 含 `backtest/portfolio.py` 本身（模拟器），合并就得先放宽。`lint-imports` 由 7 kept 变 **8 kept / 0 broken**（只加不放宽）。**层的选择是被排除出来的**：`domain/` 被 `domain-purity` 禁 `openalpha_cn.backtest`，而 D16 十项里有七项是 `004` 的类型，放 `domain/` 根本带不了；`product/` 什么都不禁（`product/research.py` 已 import `runtime.contracts`），放那里禁令就是一句话。**排序不重排**：`rank`/`score` 是 `CrossSectionScreen.select` 的，`__post_init__` 要求与 `ShortlistEntry` 逐字相等，所以证据面回来的 confidence 不能改顺序，漏斗次序的每一条已实测警告继续成立（D17：排序回答什么值得复核）。**「因子暴露」不是打分分解**：`contribution` 分解的是**分数**，其量纲是声明权重，改权重就全动；`CandidateExposure` 是 `SecurityCharacteristic`（行业码 + 市值 + 是否回填），是 `V2-P3-004` 中性化回归设计矩阵的一行，改光所有权重也不动一分。**没有第三种选择，这才是发现**：全仓不拟合任何逐股载荷 —— `FactorNeutralizationStatistics` 只存 `market_cap_slope`（**整个截面一个系数**），`NeutralizedFactorObservation` 只存残差与 `industry_code`。故 neutralized 档没有暴露截面直接**拒绝**。验收测试把 `004` 的整市发现在**一个候选**上复现：真 `apply_factor_transform` + 真 `apply_factor_neutralization` 跑 120 名截面，要求 top 名次带 `score_is_a_winsorization_bound`、它们的 processed 值只有 **1** 个不同值、中性化值**各不相同**、暴露指向**不同行业** —— 没有 `CandidateExposure` 这三件事就只是一个数加一个布尔。**风险标记闭集的理由是实测**：`SignalFrame.risk_flags` 是开放字符串集，`decisions/risk.py::RiskGate` 读五个，`agents/committee.py` 把另外三个当 severe，**两者交集为空**，而委员会自己加的 `committee-disagreement` **两边都不在** —— 即被标记分歧的信号走到运行期风险闸门返回 `pass`。**没有 `capacity` 标记**，且这是发现不是遗漏：容量需要声明 `participation_cap` 与会话成交额，本契约不取；每个候选都带的标记，是 `TradeabilityVerdict` 删掉的 `not_in_registry` 的镜像（那是没有输入能走到的分支，这是没有输入能不走的分支）。**身份按 `V2-P3-014` 拆两个**：`ranking_manifest_id` 寻址**声明**（`built_at` 具名排除，配 `model_fields` 元审计），`ranking_content_digest` 寻址**答案**（`subject`/`rank`/`score`/`signal_id`/`run_manifest_id`，即 S49 要 diff 的东西），`CandidateRanking` 本身**不再要第三个**。`risk_flags`/`exposure` 故意不进内容地址（都是已入哈希那五项的函数），并实测：同一批候选带与不带暴露截面，标记不同而地址相同。**周期消费了 `V2-P4-001` 的收窄**：清单声明一个 horizon，构成信号不同即按标的名拒绝；并如实披露该规则在 `run_cycle` 路径上恒真 —— `ResearchEngine._aggregate` 把 `horizon="5d"` 写死，无视 `MarketAgent` 的 `5d` 与 `ThemeAgent` 的 `10d`。**`KNOWN_RANKING_LIMITATIONS` 是第二十二个注册表，七条，总数 204 → 211。运行时依赖仍是九个** | S43-49, D16 |
| `V2-P4-006` | ~~治理化筛选，取代仅按 confidence 排序的 `ResearchScreener`；顺带拆分 `product/research.py` 的三份职责~~ **已完成** | 产 | 005 | 已交付 `product/{governance,screening,watchlist,reporting}.py`，`research.py` 保留为再导出门面（`api/app.py` 与 `test_import_layering.py` 的探针都按 `product.research` 点名 `ResearchScreener`，改名会打断两处仓外引用）。**三方断裂已实测复现**：`RiskGate._blocking_flags` = {`future_data`, `look_ahead_violation`}，`_reducing_flags` = {`redistribution_unknown`, `source_uri_missing`, `revised_after_initial_availability`}，而 `agents/committee.py:78` 方法体内的字面量集合是 {`regulatory`, `data-quality`, `suspension`} —— **交集为空**；且直接测得 `RiskGate().evaluate(risk_flags=("committee-disagreement",)) == "pass"`，即被委员会自己标记为分歧的信号在运行期闸门原样放行。**关键设计：`governance.py` 里没有任何风险标记字符串。**`SHIPPED_RISK_GATES` 把两个闸门登记为**可调用对象**，严重度靠逐个标记去问它们得出；模块内凡出现 `future_data` 均在 docstring，由 AST 审计强制。首选的"把三个集合的并集写成常量"被否决 —— **那就是第四个列表**，第一天正确、闸门一改就过期、而且没有东西会红。梯级 `clear → unrecognised → reduced → severe → blocked`，排序键 `(SEVERITY_RANK, -confidence, -strength, subject)`，旧键原封不动地留在治理之下；`committee-disagreement` 落在 `unrecognised`，断裂由此闭合。`max_risk_flags`（已发布 API 字段）保留，并配一条证明"计数分不开严重与良性"的测试。**新增第 23 个注册表 `KNOWN_SCREENING_LIMITATIONS`（7 条）**，其中最锋利一条是实测：把 `future_data` 拼成 `future-data`，标记降级为 `unrecognised`，候选反而**在榜单上升** —— 建立在开放字符串集上的治理筛选，可靠性等同于生产者的拼写，在 `product/` 层内无解（见新 issue `V2-P4-030`）。**受阻依赖**：把委员会那个方法体内字面量提升为模块常量才是根治，属 `agents/committee.py`。25 个变异全红；`POST /api/v1/screen` 此前**零测试覆盖**，现有 3 条 | S50, S51 |
| `V2-P4-007` | ~~排名对比 vs 上次运行（新增/移除/理由变化）~~ **已完成** | 产 | 005 | 已交付 `shortlist_compare.py` + `openalpha shortlist compare <baseline> <current>` + `OpenAlphaSDK.compare_shortlists`。**「上次运行」这件事本部署答不出来，故不假装**：`shortlist_id` 是内容地址、`list_ids` 按 sha256 升序，`the_stored_answer_is_addressed_by_content_and_not_by_when_it_was_run` 已实测说明存储里没有时钟（键里放墙钟会让同一答案每天铸一份新文档）——所以**两个地址都是参数、第一个是基准**，且 body 把两个地址都写回去让读者能核对方向。**要三样就得读两个面**：`funnel.shortlist` 给 `(subject, rank, score)`（每次答出来的运行都有），`admitted` 给 `direction`/`confidence`/`risk_flags`/`run_manifest_id`（被拒列表是 `null`、无证据运行是 `[]`）；进出读第一面，**理由变化只能来自第二面**。`status` 按**筛选面**判而不是按 `admitted` 判：两天都入选、只有一天被发布的名字是闸门在说话而不是市场，按 `admitted` 判会把被拒列表报成「所有名字都消失了」。`REASON_CHANGES` 只收四个**离散**事实（`admission`/`direction`/`risk_flags`/`backing_run`）；`rank`/`score` 排除是因为面板一动就全动（那样 `reason_changed` 恒等于 `held`），`confidence` 排除是因为连续量要阈值、而本仓在这个面上没有任何实测过的默认阈值 —— 两者照样**逐条上报**，只是不计入汇总。**问题不同的两个答案具名拒绝**并点出差异的键（跨问题做差会把每个名字都报成新增+移除，对两张表是真话、对一个市场是假象）；`as_of` **故意不在** `COMPARABLE_KEYS` 里，因为它正是应当变的那个，要求它变反而会拒掉「同一时刻、面板重建过」这个真实用例。`rank_change` 与 `score_change` **同向读**（正=名次上升 / 分数上升），符号由「与推导它的那对数字比」而不是与字面量比来钉住。**不落盘、无地址**：两个 `shortlist_id` 就是这次对比的地址。产品路径全程 `CliRunner` + `OpenAlphaSDK`（`tests/integration/test_shortlist_comparison.py`，真跑两天面板、两次 `shortlist run`），夹具形状另有一条测试与**真渲染器**逐字段对账。`KNOWN_COMPARISON_LIMITATIONS` 是第三十四个注册表（七条，把总数从 311 / 69 推到 318 / 69） **变异实测 246 个打 237 个**（`shortlist_compare.py` 214/214、`cli.py` 本命令 32/23；存活 9 个全是帮助文本、列头、`./runtime` 默认值这类展示串，外加 `ensure_ascii`（本载荷全 ASCII，字节相同，等价））。**扫描直接找出两处真缺陷**：① `COMPARABLE_KEYS` 里的 `schema_version` **是死的**（形状检查在两两比较之前已经按名拒绝），打不掉的那一条不是靠加断言而是**删掉多余检查**关闭的 —— 两处检查等于一处检查加一个后来者可以改错的地方；② 把并集换成交集的变异逼出一条测试，那条测试当场抓到 `declaration` 的比较用 `.get(key)` —— **一侧根本没有这个键、另一侧是 `null`，两者被判相等**，于是拒绝信息说「差异在 `[]`」，一个什么都没点名的拒绝。已改为哨兵默认值；`declaration.neutralization` 在本构建**永远**渲染成 `null`，所以这正是老答案与新答案相比时必然走到的那条路。 | 集成（CLI+SDK）：两天真答案对比出 `added`/`removed`/`rank_change`/`direction` 变化；反转两个地址则 `added`/`removed` 互换；自比自为全零 | S44, S49 |
| `V2-P4-008` | ~~路由扩展：支持声明特征依赖的 agent —— 当前**无 evidence family 的 agent 永不被路由**~~ **已完成** | 结 | 005 | `runtime/router.py:12-23` 坐标未漂移：改动前 `route` 正是 `agent.evidence_families & families`，空集与任何集合相交仍为空且为假，故声明特征依赖的 agent 被**丢掉**——不是拒绝：`routing_path` 无条目、manifest 无 `AgentVersion`、ledger 无弃权，「这个 agent 对这次运行无话可说」与「这个 agent 对任何运行都永远无话可说」是同一个观察。`ResearchAgent` 新增 `feature_dependencies`（**必需**而非可选，理由同 `provenance`：契约允许省略的声明就是路由必须去猜的声明），路由**两半都要满足**且**量词故意不同** —— evidence family 取**任一**（`ThemeAgent.analyze` 就是 `_family(item) in self.evidence_families`，三个家族到一个也照算，少到的是样本而不是洞），feature 取**全部**（agent 的算式按 `feature_id` 点名，缺一列是缺一项而不是样本变小）。两者皆不声明者**具名拒绝**（`UndeclaredAgentDependencyError`）而非丢弃：fail-open 更糟，因为 `SignalFrame` 拒绝任何 `evidence_ids` 为空的非弃权方向，这种 agent 唯一可达输出就是弃权，而 `_aggregate` 会把它当 0 平均进整轮结论。**路由读列表不读格子**：`FeatureRow.values` 可以是 `None`，按格子路由会把本行的缺陷降一层重演（被丢的 agent 不留弃权、不留 `routing_path`）；路由后 agent 遇到 `None` 就弃权，弃权进 ledger。**顺带修了一个先于本行存在的引擎缺陷**（在 `be262ea` 上以一个确定性弃权 agent 实测复现）：全体弃权时 `_aggregate` 先按均值算出 `neutral` 再交给 `SignalFrame`，抛 `ValidationError: directional signal requires evidence` 直接冲出 `run_cycle`；按 `V2-P4-029` 同一句话修（弃权是「证据不支持任何方向」，推翻它就是用空 `evidence_ids` 铸造方向性结论），现返回弃权并**分辨两种理由**（无人被路由 / 全体弃权），且把弃权 agent 的 `risk_flags` 带上去，否则 `block` 会静默变成 `pass`。**产品路径是 `OpenAlphaSDK(features=...)`**，`tests/integration/test_feature_dependent_routing.py` 与 `tests/integration/test_abstaining_agent_aggregate.py` 全程走 SDK；CLI 与 REST 两面不组装 feature plane，这一点写成 `KNOWN_ROUTING_LIMITATIONS` 而不是默认可用（`ResearchRunRequest` 是 `extra="forbid"` 且整体进 `request_digest`，加字段属破坏性变更，按 AGENTS.md 规则 3 只能在已关闭的 `V2-P4-001` 窗口做）。`KNOWN_ROUTING_LIMITATIONS` 是第三十三个注册表（七条，把总数从 304 / 69 推到 311 / 69；同一分支上 `V2-P4-007` 随后加了第三十四个，最终 34 / 318 / 69），运行时依赖仍**九个**，`lint-imports` 仍 **8 kept / 0 broken** **变异实测 95 个打 93 个**（`router.py` 25/24、`engine.py` 改动行 22/20、`agents/base.py` 14/14、`agents/feature.py` 32/29、`sdk.py` 改动行 2/2；存活 6 个逐条判定为等价：3+2 个是**局部变量类型标注**里的 `Literal` 成员（运行期不求值），1 个是 `@dataclass(slots=True)`（只影响内存布局））。**四个存活由加断言关闭而不是解释掉**：两个阈值的边界（恰好落在 ±0.15 上无人站过）、弃权帧的 `confidence`（`validate_conclusion` 不管它，而 `_aggregate` 会把它平均进整轮）、`__all__`（全库无 `import *`，写错的名字没人发现）、`AgentContext` 的 frozen 与两个标识符的上下界（下界从 1 提到 2 时只测空串是测不出来的）。 | 单元：无 evidence family 但声明列的 agent 被路由；声明两半只满足一半者不被路由；两半皆无者具名拒绝。集成（SDK）：feature agent 进 `agent_outputs`/`routing_path`/`agent_versions` | S38, D15 |
| `V2-P4-009` | ~~`AgentContext` 增加特征/面板句柄（可复用已声明但全库无人用的 `tools/base.py:54-62 ResearchTool`）~~ **已完成** | 结 | 008 | `agents/base.py:12-20` 坐标未漂移。**「复用 `ResearchTool`」这一条被实测否决，两处度量**（`tests/unit/agents/test_feature_plane_seam.py`）：① `ToolRequest.kind` 是 `max_length=64`，而本构建最长因子键 `deducted_earnings_yield_ttm/v1` 的 neutralized 拼法长 **89 字符**，`ToolRequest` 直接 `ValidationError` —— 整个 neutralized 档位没有一列**问得出口**；② `ToolResult` 恰有三个字段（`status`/`evidence_ids`/`no_data_reason`）且 `extra="forbid"`，没有任何字段能装数字，而且 `status="success"` **强制** `evidence_ids` 非空，所以「读到了值但没有 evidence id」只能报成 `no_data`。故新增**第二条**协议 `FeaturePlane`（`feature_ids` + `value`）声明在**消费者旁边** —— `ShortlistDocumentStore` / `ExperimentDocumentStore` 的形状与理由 —— `domain/alpha_model.py::FeatureCrossSection` **无适配器、无改动**即结构化满足，于是 `agents/` 对 `feature_matrix`（进而 DuckDB）**零 import 边**。字段用 `runtime_checkable` + `arbitrary_types_allowed`，是**方法存在性的 isinstance** 而不是逐行 pydantic 重建 —— 这是实施决策 31（整市约 5,500 行）；断言用**对象同一性**而非相等，因为重建出的副本相等而不同一。`None` 与空 plane 是两件事且第二件**不可构造**（`FeatureCrossSection` 具名拒绝空 `rows`）。**消费侧一并交付**（`agents/feature.py::FeatureScoreAgent`），刻意不重蹈 `ResearchTool` 的覆辙 —— 一个声明了却全库无人用的扩展点；它是**参考实现不是基线**（`SingleFeatureAlphaModel` 的同一条边界）。**两条限制照实写**：feature-only agent 只能 cite `feature_id`（`SignalFrame` 拒绝空 `evidence_ids` 的方向性帧），以及 clamp 让强读数与极端读数在 ledger 上同为 `1.0`。**未加宽任何请求契约**：plane 由调用方组装完再交进来，故 `shortlist_view.a_neutralized_tier_screen_needs_exposures_this_face_does_not_load` 点名的三样（成员年份、交易日历、中性化）仍在本缝之外决定，这一点单列一条限制说清楚 **变异**：本行的两块代码在 `V2-P4-008` 那一格的合计里 —— `agents/base.py` 14/14 全杀、`agents/feature.py` 32/29（存活 3 个是局部变量类型标注里的 `Literal` 成员，运行期不求值，等价）。由加断言关闭的四个存活中有三个属于本行：`AgentContext` 的 frozen 与两个标识符上下界、`__all__`、弃权帧的 `confidence`。 | 单元：句柄同一性（无重建）；半个句柄被拒；`ToolRequest`/`ToolResult` 两处度量。集成（SDK）：agent 读到组装进去的那一个 cross section | S36, S38 |
| `V2-P4-010` | ~~manifest 第三槽：量化模型版本~~ **已完成** | 技 | 001 | 行内坐标已漂移（实际在 `runtime/engine.py:92-96` 与 `:128-129`，非 `:131-135,160-161`）。**两个槽的实测内容**：`model_versions` 是每个执行过的 agent 一条 `VersionRef(component=<agent_id>, version="baseline/v1")`；`prompt_versions` 恒为 `()`。**常量的代价已经到期，不是将来时**：`StructuredSignalAgent` 接受任意 `ModelProvider`，实测两次 `run_cycle` 只换 vendor model（`qwen-max-2025-01-25` vs `deepseek-chat-v3`，其余逐字节相同）得到**同一个** `run_manifest_id` 与**同一个** `decision_id` —— §9「不同配置产生相同决策 ID」在 `V2-P4-025` 关掉配置面之后，于模型面原样重现。**分三个面而不是一个带判别式的元组**（D10「分别标识确定性、量化与 LLM 组件」）：`agent_versions`（`AgentVersion(agent_id, kind)`，kind 三值 `deterministic`/`learned`/`llm_backed`，S40）、`model_versions`（改回它本来的名字：vendor 的 `provider_id` / `model`）、`alpha_model_versions`（`AlphaModelRef(name, artifact_id)`，即本行标题的第三槽）。**第三槽是独立类型而不是第四个 `tuple[VersionRef, ...]`**：agent id 与 vendor model 是**名字**（读者只能信），`V2-P4-016` 的制品引用是**摘要**（读者可重算），二者不同类正是 `V2-P4-011` 的约束（`models/base.py` 的 `ModelProvider` 是 LLM-JSON 形状，表达不了面板 fit/predict）落到 manifest 上的样子；故 `artifact_id` 由 `domain/_identity.py::CONTENT_ADDRESS_PATTERN` 约束为「`stable_model_id` 产出过的东西」，**前缀与摘要取哪些字段留给 `016`**。**`prompt_versions` 保持空，且这是结论不是遗漏**：全仓唯一的 prompt 是 `agents/model.py` 里的字符串字面量，由 `code_commit` 钉住，填它就是同一事实的第二份拷贝；`010`–`021` 链上没有任何一条把 prompt 变成制品。**kind 由 agent 自己声明（`ResearchAgent.provenance`），不由引擎 `isinstance` 推断**：推断只对本仓自带的两个实现成立，任何第三方 `ModelProvider` agent 都会被**静默**记成 deterministic —— 正是 S40 唯一要求的那个事实上给出确信的错答案。**agent 名单必须留在地址里**：改之前它只经由 `model_versions` 进入 `run_manifest_id`，只清空而不新增槽等于悄悄把一个已声明输入移出运行身份。**代价是第二次身份重写（migration 8 `rewrite_manifest_component_planes`）**：`RunManifest` 加字段 → 每个 `run_manifest_id` 变 → 经 `DecisionLedger.run_manifest_id` 带动 `decision_id` / `validation_id` / `report_id`，而这三个契约里有两个**根本没有改版**；实测 `run_bce5768e… → run_b046b7e5…`、`dec_6d621fd9… → dec_26ea2f0a…`、`val_f898bce1… → val_67f3514e…`。D36 要求破坏性变更打包，故第三槽与前两个一起落，而不是留给 `016` 再付一次同样的迁移。**`run-manifest/v2` 因此改为拒绝读时升级**：`upgrade_run_manifest_v1` 的许可条件原文是「no stored key depends on the result」，`V2-P4-025` 之后这句话对 v2 已经不成立；v1 仍直升 v3（`DecisionLedgerV1` 无 `run_manifest_id`，该前提由断言而非回忆维持）。**迁移不给存量行编造 kind**：v2 行里的 agent id 留在 `model_versions` 原处，因为那一行并不携带它是 deterministic 还是 llm_backed 的事实，搬过去就是第二个 `"baseline/v1"`。`lint-imports` 仍 8 kept / 0 broken；运行时依赖仍九个；未新增 `KNOWN_*` 注册表（仍 25 / 234）。**明确留给后续**：`learned` 今天无仓内生产者（`011` 的 `AlphaModel` 与 `014` 的基线才会有）；`alpha_model_versions` 的前缀与摘要字段由 `016` 定；prompt 何时成为制品链上无人认领 | S40, D10 |
| `V2-P4-011` | ~~`AlphaModel` 契约（与 LLM `ModelProvider` 严格分离，不复用 `models/governance.py` 的 LLM 专用件）~~ **已完成** | 结 | 010 | **行内坐标未漂移**：`ModelProvider` 确在 `models/base.py:32`，`generate_json` 签名跨 `:39-46`（行首写的 `:32-40` 覆盖到方法开头）。**前提是实测的，不是复述**（`tests/unit/domain/test_alpha_model_boundary.py`，三路）：① 从 `models/base.py` 自己的 AST 读出该 Protocol 的**全部**成员就是 `metadata` 与 `generate_json`，后者三个形参标注恰为 `str`/`str`/`dict[str, Any]`、返回 `dict[str, Any]` —— 没有一个形参带日期或时刻类型，故 as-of 只能被渲染成散文塞进 `user`；② 让一个**最慷慨**的 stub provider 为每个标的返回分数，再把这份 `dict[str, Any]` 交给 `PredictionBatch.model_validate`，缺失字段恰为 `as_of`/`predicted_at`/`artifact`/`predictions` 四个 —— 这就是「表达不了」值多少钱；③ 两个 `runtime_checkable` Protocol 上 `isinstance` **双向为假**。**层是被排除出来的，但不是唯一幸存者，且照实写**：`tests/unit/domain/test_alpha_model_layering.py` 直接读 `pyproject.toml` 跑这场排除 —— 十三个子包收窄到**两个**（`domain`、`tools`），二选一是判断不是闸门（`tools/` 只有两个 `ResearchTool` 且已 import `domain/`；`domain/` 是 `V2-P0B-012` 把 `storage/` 要反序列化的五个契约搬去的地方，也是 `TrainingExample` 赖以表达的 `domain/labels.py` 所在）。**`models/` 是被结构性排除的，而不是靠本行那句「严格分离」**：`backtest-no-numeric-stack-or-panel-plane` 的 forbidden 名单里就写着 `openalpha_cn.models`，所以 `013` 的 walk-forward 研究**根本 import 不到**声明在 LLM provider 旁边的 `AlphaModel`；`backtest/` 则由 `storage-no-upward-deps` 排除（`017` 必须反序列化 `PredictionBatch`）。**输入是 dataclass、输出是 pydantic，这是 D31 强制的**（「禁止在面板查询路径上做逐行 pydantic 重建」）：`FeatureCrossSection`/`TrainingSet` 是面板读路径（整市约 5,500 行），`AlphaModelArtifact`/`PredictionBatch` 是要被读回来校验的制品；且 `stable_model_id` 只收 `BaseModel`，故 `016` 能寻址的那一半必须是 pydantic。**`fit` 收的不是裸数组**：`TrainingExample` 携带整个 `OutcomeLabel`（`V2-P1-017` 的 `LabelSample` 原话就是「the unit a supervised training set is built out of」），被市场拒绝的窗口在构造期即被拒 —— `realized_return` 对停牌窗口 raise 而不是读 `0.0`，而读 `0.0` 就是教模型停牌等于走平。**`training_cutoff` 取的是 exit 会话的 `close_instant`，不是最后一个 `prediction_day`**：标签的数字要到窗口收盘才可知，取 `prediction_day` 会把截止点报早两个会话（实测 6-02 的窗口 6-03 进、6-04 出）；用 `LabelWindow.close_instant` 是为了让比较是时刻对时刻，本契约自己既不持有日历也不持有时区。**泄漏地板是 `as_of >= training_cutoff`，相等允许**（用昨夜收盘训练、以昨夜收盘为 as-of 预测正是日频生产模型的做法），两个用例只差**一微秒**；这是地板不是 purge，重叠标签由 `TrainingSet.overlaps`（走 `overlapping_windows`）如实报出，留给 `013`。**制品不带地址**：`AlphaModelArtifact` 携带 D11 点名的训练截止/特征版本/超参/拟合参数/seed/代码版本而**没有 id**，因为 `010` 已把前缀与摘要字段判给 `016`；`PredictionBatch` 因此**按值**携带 artifact，这比一个地址更多而不是更少，`016` 到来时是**加一个 computed field** 而不是改一个 —— 改一个会带动 `run_manifest_id` → `decision_id`，即 `010` 已经付过一次的迁移。**数值栈今天在门外、明天不被堵死**：两个 Protocol 结构化满足（实测两个实现的 `__mro__[1:] == (object,)`），`domain-purity` 是把「契约里没有数值库」变成闸门的那条契约，跨边界只走 `float \| None`/`str`/`int`/`datetime`；并如实写明 **`015` 的 LightGBM 不能跟着参考实现放进 `backtest/`**（`backtest-studies-touch-no-store` 逐模块禁 `numpy`，整包契约禁 `sklearn`/`scipy`/`pandas`），它得自己论证一个家，而本契约一行都不用动。**弃权的形状先立**（S35）：`Prediction` 恰有分数或恰有理由，两者皆有或皆无都拒；`prediction_batch_for` 强制**每个被问到的标的都有一行**，因为 `PredictionBatch` 看不到截面、看不到就挡不住「悄悄丢名字」。**参考实现明确不是基线**：`backtest/alpha_model.py` 的 `SingleFeatureAlphaModel` 读一个特征、学一个中心和一个符号，用来证明契约能被满足与驱动（fit 改变 predict 的排序、两折产两个制品、只由制品即可重建并复现每个数字、缺值弃权），**不从 `backtest/__init__.py` 再导出**（理由同 `ComponentCrossSection`：把一个刻意不足的模型放到包的前门，下一个读者会先撞见它而不是 `014` 的基线）。**变异 57/57 全杀**（44 条打在契约与参考实现上，13 条打在前提、层、D11 缺口与注册表上）；**唯一存活的一条由改设计关闭而不是加断言**：`require_features` 在参考实现的 `predict` 与 `prediction_batch_for` 里各有一份，删掉后者全绿 —— 两份检查等于一份检查加一个未来实现可以跳过的地方，故删掉参考实现里的那份，只留共享路径上的那份。`lint-imports` 仍 **8 kept / 0 broken**（两条 backtest 逐模块契约各加一个 source，只加不放宽）；运行时依赖仍**九个**。**`KNOWN_ALPHA_MODEL_LIMITATIONS` 是第二十六个注册表，八条，25 → 26 / 234 → 242**；顺带披露：改动前 floor 与散文都写 229 而实测 234，**已落后 5**，是该模块的可执行断言而非它的散文抓到的。**明确留给下游**：**D11 点名十一样，本制品带六样**（训练截止、周期、特征版本、参数、seed、代码版本）**并把缺的五样逐条点名交人**（股票池与预处理归 `012`、切分政策归 `013`、指标归 `014`/`015`、内容哈希归 `016`；「目标」不设字段是因为全仓只有一个 —— `OutcomeLabel.realized_return`，设了就是 `010` 反对 `AgentVersion.version` 的那种常量字段）——这一条是写完之后自我证伪出来的：初稿的 docstring 写着「每个字段都是 D11 点名的」，反过来读就成了「D11 点名的都在」，而后者是假的。`016` 定前缀与摘要字段（`backtest/candidate_ranking.py` 的 `CandidatePrediction.model_artifact_id` 也因此**仍是自由文本** —— `005` 写的「由 `011` 给的身份」被 `010` 改判给了 `016`）；`012` 定 `feature_version` 的文法与生产者；`013` 定 purge/embargo；`014` 定线性/排序基线；`015` 定自己的家与是否需要嵌套超参；`017` 定「结果已知前」（要日历与库）与回溯重算不得替换原件；`018` 定弃权词表 | S25, D10 |
| `V2-P4-012` | ~~版本化特征矩阵~~ **已完成** | 技 | 011 | 已交付 `feature_matrix.py`（研究面第**四**个顶层族 `feature_*`，落地当天 `test_every_top_level_module_is_a_declared_leaf_or_a_member_of_a_discovered_family` 就先红了，按它给的两条补救里选了「加入被发现集合并领两张表的行」）。**文法**：一列不是一个因子，而是 `(factor, tier, transform, neutralization)` 四元组，`feature_id` 把四样都拼出来（`reversal_1d/v1@raw`、`…@processed:cross_section_standard/v1`、`…@neutralized:<transform>:<neutralization>`）—— 因为同一个因子在三个 tier 上各存一份且数值不同，只写 `qualified_key` 会让两列重名，正好撞上 `_validate_feature_ids` 的「严格递增」。**但可读的 handle 不能当身份，这是实测的**：`FactorDefinition.qualified_key` 自己的 docstring 就写着「两个定义可以共享 qualified_key 而 factor_id 不同（改了定义忘了 bump version）」，而 `factor_observation_dataset` 是 `factor_obs_<key>_v<n>`，即按 handle 分区 —— 所以 `lookback_sessions=2` 与 `=3` 两版 `reversal_1d/v1` 的 build 写进**同一个**分区，`load_factor_observations` 也不按内容地址过滤（它自己的 docstring：「The factor is the **dataset**, not a filter」）。于是 `FeatureSpec` 寻址的是 `factor_id`/`transform_id`/`neutralization_id` 而不是 handle，`feature_version` = `stable_model_id(prefix="feat", …)`（全仓唯一哈希，`V2-P4-037` 归档的正是第二套 canonicalisation），并且**读取时**再核一次：`_admitted_cells` 拒绝一条在本 instant 上却由别的内容地址写下的行。两半都有测：声明面 `feature_ids` 相同而 `feature_version` 不同，读取面同一分区两个 build 一答一拒。**预处理归本行**，所以它在地址里面：`abstain`（默认，唯一不断言面板没测过的东西）/ `drop_security` / `cross_section_median`（只取同一 instant 的 admitted 值，故按构造无前视；时序填充与全样本中位数都不提供，理由写在模块里）。「缺失」按 `TIER_ADMITTED_CODES` 判定而非 `null`，故 transform 自己 `imputed` 出来的数在这里算缺失 —— 两层插补叠起来那格谁也归因不了。**股票池版本 = `set_digest(registry.listed_on(session))`**，与 `candidate_ranking` 的 `universe_digest` 同义。**它扛得住 `V2-P4-059` 的向下加宽**，且是两个方向一起钉的：加宽后 `years=(2026,)` 与 `years=(1996,2026)` 读到同一个市场，故凡是从 `years` 派生的版本都是「问题的版本」而不是「答案的版本」；而单看加宽这一半杀不掉从 `years_read` 派生的变体，所以另一半是同一 store、同一 `years`、同一 `years_read`、两个会话之间退市一只名字 —— 只有对上市集合取 `set_digest` 两边都过。**接缝**：不开任何新读法，三个 tier 走 `V2-P3-002`/`V2-P3-019` 的三个 `read_visible_at` 调用者，日历与登记册走 `panel_ingest`（`V2-P4-076`/`061` 已搬到按事件日/按会话），因子 tier 读**请求的** `as_of`、日历与登记册读**解析出的** instant（严格更保守），会话由 `newest_published_session` 决定而不是 instant 自己的日历日（`V2-P4-077` 的规则，一处实现两个面各自翻译拒绝）。**与 `V2-P4-032` 的一处有意分歧**：那边不把行收窄到登记册（因为 `CrossSectionScreen._read_components` 已经收窄了），这边没有第二道过滤，所以行集合**就是**上市集合 —— 面板没有值的上市名字拿到一行全 `None` 而不是消失，即 `V2-P4-011` 的「scored or abstained, never absent」上移一层。矩阵按**会话**而不是按 instant 拒绝重复（同 instant 是同会话的子集，且 B1/B2 这种「两个 build 一个市场」只有会话规则看得见）。留给下游且点名：`013`（walk-forward/purge/embargo，以及把这些 section 变成 `TrainingSet`）、`014`（基线，`require_declared_features` 的第一个调用者）、`016`（制品内容地址，以及要不要把股票池版本也记进摘要）、`017`（落库）、`021`（模型面）。`AlphaModelDeclaration.feature_version` **仍是自由字符串**：收紧成 `CONTENT_ADDRESS_PATTERN` 会拒掉特征来自别处的模型，绑定改在 join 处（`require_declared_features`）。`KNOWN_FEATURE_MATRIX_LIMITATIONS` 五条，登记表因此到 **27 个 / 247 条**。 | S26 |
| `V2-P4-013` | ~~Walk-forward 切分 + purge/embargo~~ **已完成** | 技 | 012 | 已交付 `backtest/walk_forward.py`（第十三个 `backtest/*.py`，落地当天加入两条 per-module 契约的 source 列表；层的选择不是判断题 —— `backtest-no-numeric-stack-or-panel-plane` 把 `openalpha_cn.feature_matrix` 列在 forbidden 里，所以本模块只吃 `domain/` 的 `FeatureCrossSection`，正是 `pyproject.toml` 自己那段注释写下的缝）。**purge 是一次比较，而且比「重叠标签」那条更强**：候选被剔除当且仅当 `close_instant(exit_day) > 该 fold 首个预测时刻` —— 即「这条训练标签在模型被问到的那一刻还没收盘」。共享会话规则被它蕴含而非与它并列（共享会话 ⇒ 训练 exit ≥ 最早 test entry ⇒ 收盘晚于首个 as_of，反向不成立），故模块内**不做任何会话交集**，「存活的训练标签不读任何 test 标签读过的会话」是被断言的**结果**；代价实测恰为一个会话：session 6 的窗口 7..12 与最早 test 标签 13..18 毫无交集，但它 15:00 收盘晚于 09:00 的首个预测时刻，弱规则会把它留下。等号被承认（`>` 而非 `>=`），且**早晨语料分不出这两者** —— 15:00 的收盘永不等于 09:00 的时刻 —— 故另有一份按收盘定日的语料专门分它。**`V2-P4-011` 指定的输入被证伪**：`TrainingSet.overlaps` 按标的分组，而 fold 边界不分标的 —— 一条落在 test 期内的训练标签是 test 期内的**市场**收益，无论它是谁的；两侧实测（普通语料上它报的每一对都落在边界同侧；标的跨边界不重复的语料上它一对跨界的都不报，而边界照漏）。**embargo 与 purge 结构性不可互替**：embargo 只看 purge 放行的候选，两集合在任意宽度下不相交（0..12 全宽度跑过）；二者切在同一根轴上却由不同的量决定 —— purge 的射程是 horizon（在标签上，可测量），embargo 的射程是特征足迹（回看窗、披露滞后、修正，都不在标签上），所以前者被测量、后者被声明且**没有默认值**，0 是一句话而不是一个开关。**泄漏是种下的，并且被实测**：语料按每证券单条价格路径生成标签（各自独立指定 target 的语料根本分不出 purge 与不 purge），用一个逐会话系数翻转「特征被奖励的方向」。overlap 语料上未 purge 的拟合学到 test 期自己的方向（sign +1）、purge 后学到相反方向；而未 purge 的 fold **连第一个 batch 都被 `PredictionBatch` 拒绝** —— 这不是证据缺口而是发现：purge 与 `V2-P4-011` 的 floor 是同一次比较的两个作用域（每 fold 锚在最早时刻 + 剔除，而非每 batch 锚在自己 + 拒绝），所以「不 purge」的反事实不是一个虚高的数，是一个跑不起来的 fold。adjacent 语料上两侧都能跑，仅 purge 得 **1.0**、purge + embargo(2) 得 **0.0**，差额全部是泄漏。**随机切分：训练集成员身份不可表达，它所依赖的顺序只是被拒绝**（措辞由 `V2-P4-090` 实测收窄，原文对两者都写「不可表达」）。`WalkForwardFold` 没有任何字段命名「哪些行进训练集」，训练集由边界导出，而边界是一个日期因而整条横截面必然同侧；散布的 test block 同样不可表达（`(first_test_day, test_day_count)`）。但「block 之前的每个预测日」只有在面板 section 严格按预测日递增、且每个 section 的时刻在标签自己的时区里正是它的预测日时才是一句关于**时间**的话 —— 这两条不变量原先只活在工厂里，现已是 `LabelledPanel`/`PanelSection` 的 `__post_init__` 拒绝；block 的**位置**同样是拒绝。这条差别写在 registry 里。变异实测 **55 个全杀**，其中四处是删代码而不是补断言（`validate_feature_ids` 与 `FeatureCrossSection.__post_init__` 重复；schedule 的负 embargo 检查与 fold 的重复；`_training_set_of` 的 `AlphaModelError` 转译不可达；排序检查的 instants 半边被 days 半边蕴含 —— 后者顺带补上「同一天两份横截面」的拒绝，正是 `test_feature_matrix_reads.py` 明写要交给本行的形状）。**第一轮变异全部无效并被重跑**：当时基线里 `test_known_limitation_registries.py` 是红的（registry 已存在但未入表），59 个判定全是那一条失败；在真绿基线上重跑暴露 18 个幸存者。测试：`tests/unit/backtest/test_walk_forward.py`（切分规则）与 `tests/unit/backtest/test_walk_forward_leak.py`（种下的泄漏），语料在 `tests/walk_forward_fixtures.py`。`KNOWN_WALK_FORWARD_LIMITATIONS` 新增 11 条，审计移至 **28 registries / 258 entries** | S27, S28, D12 |
| `V2-P4-014` | ~~线性/排序基线~~ **已完成** | 技 | 013 | 已交付 `backtest/alpha_baseline.py`（`backtest/` 下第十八个文件，落地当天加入两条 per-module 契约的 source 列表，探针确认不加就红；`lint-imports` 保持 8 kept / 0 broken）。**「线性/排序」是一个模型不是两个**：交付的是**横截面秩的线性组合**。理由三条，前两条是「本仓能不能校验」而不是口味：① 分数唯一的下游是**排序**（`PredictionBatch` 不声明单位，`rank_candidates` 只按它排），线性模型相对秩模型唯一的优势是输出带收益率单位，而校验这条主张的报告是 Story **S31**，PRD 已推迟到 v2.1 —— 无法校验的单位主张比不给单位更糟；② 电平空间的系数依赖模型**看不见**的政策：`V2-P4-012` 把去极值/标准化/缺失值封进 `FeatureSpec.feature_version`，模型只拿到结果；秩对任何单调变换不变，所以这条基线在任何预处理下给同一个答案 —— D13 要的是比较**下限**，会随预处理漂移的下限不是下限；③ `V2-P4-004` 实测本市场 5,540 只里有 **56** 只被去极值压到同一个 `turnover_rate`（`pb` 55、`pe_ttm` 41），电平空间里这 56 只带着去极值器选的同一个人造数字和同样的杠杆，`average_ranks` 给它们一个共享位次 —— 即「这 56 只并列，这一列区分不了它们」，其余列照常区分。**没有数值栈这条线画在哪**：不是「可表达/不可表达」。可表达且已表达 —— 平均秩（复用 `factor_ic.average_ranks`，两处浮点缺陷已在那边修过的 `_pearson`，`factor_redundancy` 早有同样的跨模块复用先例）、`fmean`、有界加权和，全部 `O(n log n)`。**可表达而拒绝** —— **联合**最小二乘：`p x p` Gram 矩阵的高斯消元是三十行 stdlib，跑得动；stdlib 不给的是让答案可信的那样东西（QR、SVD 或诚实的条件数），而本仓的列恰是最坏情况（`V2-P4-012` 的文法把一个因子的 raw/processed/neutralized 存成一个矩阵的三列，构造上近乎重复），`V2-P4-013` 的语料更是极端：两列**恰好**秩反相关，联合解在那里奇异（行列式实测为 0），边际解答 `+1` 与 `-1`。所以系数是**边际**的（每列自己的训练期平均 rank IC，构造上落在 `[-1, 1]`），线画在「失败方式响亮」与「失败方式是一个没人分得清是不是信号的大系数」之间；边际的代价（两列冗余被算两次）正是 `V2-P4-015` 树模型要补的，也是 D13 要两个基线而不是一个的原因。**总体规则一处两用**：秩是集合内的位置（`factor_redundancy._correlate` 实测把 40 名的秩向量截到 25 名的交集，200/200 次与诚实答案不一致，最大 0.100），所以「携带**每一**列值的证券」这一条规则同时决定谁被排名、谁被打分、谁弃权 —— fit 与 predict 同一条。**弃权词汇两条常量**（`ABSTAIN_INCOMPLETE_FEATURES`、`ABSTAIN_UNRANKABLE_CROSS_SECTION`，都不插值计数，好让 `V2-P4-018` 一码对一条件）；S35 的编码词汇、stale 判定仍归 `018`。**指标五个，每个回答别的答不了的问题**：逐日 `rank_ic`（相关的是**次序**，也是唯一在并列上优雅退化的统计量 —— 基于 sort position 的指标会把 56 只并列名按排序返回的顺序排出来）、`mean_rank_ic`（挑战者要打败的那个数）、`stdev_rank_ic` + `rank_icir`（零离散度报 `None` 不报 `inf`，`ICSummary.icir` 的决定照搬）、`measured_count` 对 `len(points)`、以及 **`scored_ratio`** —— 唯一永不为 `None` 的那个，因为弃权是免费的技能，两个模型的头条数只有并排放着各自答了多大比例的市场才可比。`ICCoverage`/`ICStabilityCoverage` 直接 import：一个横截面产不出相关的四种理由，在因子上和在拟合上是同四种。**本 issue 自证伪了一条**：原以为 `mean_rank_ic` 能像 `013` 的一位模型那样把泄漏折与净折分开，实测**分不开**（两者都恰好 `-1.0`）。原因是两把尺子不同 —— 那个参考模型把样本**汇总**后比两个均值，语料 20:1 的系数比让两条泄漏标签压过四条干净的；rank 相关对量级不变，逐日 `+1`/`-1` 再平均，六天里两天泄漏就是 `-1/3` 对 `-1`。所以泄漏**可见但不在那个数里**：它是系数三倍的塌缩，而 `FoldEvaluation.artifact` 按值携带正是为这种比较；够不到的是**次序**，因为该语料两列同步缩放。**完全**泄漏（直接拿测试块拟合）则根本报不出数 —— `V2-P4-011` 的地板拒绝那个 batch，与 `013` 自己的发现一致。**确定性**：`AlphaModelArtifact` 带 seed 而本拟合不抽数；不免费的是顺序无关性 —— `_pearson` 用普通 `sum` 累积，实测每尺寸 400 个随机横截面，置换改变答案在三名时 0/400、六名 190/400、六十名 347/400，所以三名语料**根本分不开**排序过与没排序过的拟合，本文件的语料八名并在断言属性之前先断言自己的敏感性。**变异清扫两轮，54 个变异体**：第一轮绿基线 83 passed，18 活；第二轮 3 活，三个都是等价体或不可达自由度（把删掉的按天排序加回来 —— 它的存活就是删除的证据，因为 `fmean` 是 `fsum`、按天平均的顺序不可能改变任何东西，而 `rankable` 的行内排序不是；把 `score_point` 的配对反转 —— `PredictionBatch` 已拒绝任何非严格递增的行表，这个自由度在被驱动的路径上不存在；`None if d == 0.0 else m/d` 写成 `m/d if d else None` —— 对非负浮点语义相同）。删了两处检查而不是补断言：`fit` 的按天 `sorted()` 与 `score_point` 的配对 `sorted()`。补了两处语料缺陷：`quality_roe` 改成几何间距（等差列是自身秩向量的仿射像，电平相关与秩相关逐位相同，「跳过排名」的变异体因此活过第一轮），以及在 `013` 语料上挖一个 `None` 让 `scored_ratio` 第一次不是 `1.0`。**修正了 `V2-P4-012` 的指针**：那个模块两处写「`V2-P4-014` 是 `require_declared_features` 的第一个调用者」，这是**做不到**的 —— `backtest-no-numeric-stack-or-panel-plane` 把 `openalpha_cn.feature_matrix` 列在整个 `backtest` 包的 forbidden 里，本基线与 `walk_forward.py` 同处该包同为该理由。这个检查属于第一个把声明与矩阵握在一起的**组合点**（`V2-P4-017` 落库或 `V2-P4-021` 的门面，谁先到算谁），两边的措辞都已改。`KNOWN_BASELINE_LIMITATIONS` 十条，台账由 28 registries / 258 entries 变 **29 / 268**。 | S29, D13 |
| `V2-P4-015` | ~~LightGBM 基线 + 容器修复~~ **已完成（依赖被论证后拒绝）** | 技 | 014 | **本行点名了一个库，所以「这个工作量用不上它」不是可用的答案；被回答的是「本仓该不该拿这个依赖」，答案是不该，运行时依赖仍九个。**已交付 `backtest/alpha_tree.py`（`backtest/` 下第十九个文件，落地当天加入两条 per-module 契约的 source 列表；`lint-imports` 仍 **8 kept / 0 broken**，**没有任何一条契约被放宽**）。`V2-P4-011` 写的「`015` 得自己论证一个家」是第二个问题，第一个问题一答完它就消解了。**树恰好不需要 `V2-P4-014` 说 stdlib 缺的那样东西**：那一节拒绝联合最小二乘，因为 stdlib 没有 QR/SVD/诚实条件数，而本仓的列是最坏情况（`013` 语料两列恰好秩反相关，Gram 行列式实测 0）。这条论证不迁移到树上，理由是实现里的一行 —— `_grow` **唯一的除法是除以计数**（增益 `L²/n_L + R²/n_R`，`min_leaf_securities >= 2` 保证两个分母都 ≥ 2）：完全相关的两列让**解**无定义，却只是让**树**挑一列、把另一列的信息在下一层被划分条件掉。第二处差别关乎存储而非算术：树的正则化是 `max_depth`/`tree_count`/`learning_rate`/`min_leaf_securities` 四个扁平标量，进 `AlphaModelDeclaration.hyperparameters` 因而进制品、进 `016` 的地址；`lstsq` 里运行时选的 rank cutoff 哪儿都不进 —— `011` 留给本行的「要不要加宽超参字段」由此结案：**不加宽**。**模型是什么**：横截面**秩位置**上的梯度提升回归树（平方误差，直方图分裂），目标也是当日 target 的秩位置。空间沿用 `014` 的三条理由，并多一条属于树的 —— 电平上的阈值含义随行情漂移，秩位置上的阈值是**分位数**，在每个横截面上说同一句话；这正是把 `CrossSectionalRankModel.fit` 那句「pooling 会拿周一去和周二比秩」**修好**而不是绕开（它拒绝汇总的是**数值**，这里汇总的不是数值）。**树比基线多做到什么，同一折、同一个 `evaluate_fold`（`AlphaModel` 是 Protocol，两个模块互不 import）**：① 目标 = 两列之积时，边际 rank IC 构造上为零，秩基线读 **-0.0189**、树读 **+0.8465**；② 两列近重复 + 一列真信号时，秩基线系数实测 `[0.2941, 0.2934, 0.9468]`（近重复的一对合计 0.588 对真信号的 0.947），读 **+0.9735**，树读 **+0.9992** —— 这就是 `KNOWN_BASELINE_LIMITATIONS` 那条「边际系数把两列冗余算两次」被**测量**而不是被复述；③ **诚实的一半，是断言不是提及**：目标随一列单调上升时秩基线的模型**就是**真相，读 **+1.0000**，树是用阶梯函数逼近直线，读 **+0.9995** —— **树输给了与它配对的模型**，D13 要两个基线正是为此，下限是一对而不是一个赢家。**代价实测**（20 个预测日 × 5,534 只 × 3 列 = 110,680 条真 `OutcomeLabel`，三次取最小）：`BASELINE_HYPERPARAMETERS`（60 棵、深 3、900 个编码节点）拟合 **4.55 s**（76 ms/棵），`predict` 一个整市横截面 **94.0 ms**；同语料上秩基线重测 **247.8 ms** / **11.3 ms**（`014` 记的是 216.4 / 11.4）。即 **18.4×** 秩基线、**2.0×** 一次 `compute_factor`（2.24 s）、**1.26×** 喂给它的标签构建（3.62 s）、**8.0%** 五个 `write_panel_batch` 里最小的那个（56.7 s）。**本 issue 自证伪了两条**：① ADR 初稿按原型的 2.62 s 写「0.42× 标签构建」，用交付实现重测是 **1.26×** —— 而且标签构建本身 `014` 记 6.3 s、此处三次跑出 3.62/3.72/3.73 s，正是该 ADR `write_panel_batch` 修正记过的同一种现象落在更小的量上；结论幸存但强度改写为「与周围步骤同量级、比写路径低一个量级」。② 原以为分数会像秩基线那样有闭式界（两个因子都 ≤ 1 ⇒ 界 = 列数），实测**不成立**：叶值是**残差**的均值，每一步都可能把拟合带过它追的目标，实测最大 \|score\| = **1.0038**（目标在 `[-1, 1]`），所以只有有限性是结构性的，测试名与注册表条目都按实测改写。**可选 extra 被考虑并被测量为比两个答案都差**：基线躲在 flag 后面就是 D13 的验收门半数安装打不开；更要紧的是，extra 恰恰是本 ADR **会被推翻的那条路** —— `test_the_optional_and_development_dependency_tables_are_not_a_way_around_the_nine` 的 docstring 写着「extra 里放数值栈是同一次安装换个名字，一律禁止」，而它是对**声明名**查八个发行名，`lightgbm` 不在其中（其 wheel 需要 `numpy` 与 `scipy`），`akshare` 也不在其中（它从 P1 起就在那张表里，经 `pandas` 到 `numpy`）。新增 `test_a_numerical_stack_cannot_arrive_through_an_extra_that_only_names_its_wheel` 改到 `uv.lock` 的**解析图**上，两半都是实测：默认安装触达 **25** 个发行版、数值栈 **零个**（这是本 ADR 断言过九次却从未真正测过的那一半，九个名字的 pin 说不出它），`--extra akshare` 触达 **35** 个、带 `numpy` 与 `pandas`；后者写进 `EXTRAS_THAT_CARRY_A_NUMERICAL_STACK` 而不是被豁免。**容器那一半：本行点名的四条，三条不是它说的那样**。`libgomp1` 在**两个 stage 里都没有**（`Dockerfile` 全文无 `apt-get`，镜像里 `ls /usr/lib/*/libgomp*` 空）—— ADR-0003 Consequence 5 的结论对、诊断不对，且它继续不装，因为链接它的依赖没被拿。`/dev/shm` **存在**、可写、在 Docker 自己的 64 MB 默认上（F84 说没有）。`/tmp` 的 64 MB 不动且不猜数：`src/` 下没有任何 `tempfile`、没有 `TMPDIR` 消费者，F84 点名的 joblib 溢写只随被拒绝的那个依赖到来。OMP 线程 `V2-P0B-009` 早已钉死五个并由 `test_repository_assets.py` 按字面守住。**真正的溢写属于本仓已经在用的 DuckDB，与 LightGBM 无关**：DuckDB 把 `:memory:` 连接的 `temp_directory` 默认成**相对**路径 `.tmp`（解析到进程 cwd），而 `panel/store.py` 每次 `write_panel_batch` 都开一个 `duckdb.connect(":memory:")` 暂存分区。在交付镜像里同一条查询、同一个 200 MB 上限、三个 cwd 实测：`/app`（只读层）→ `IO Error: Failed to create directory ".tmp": Read-only file system`；`/tmp`（本行要求扩容的那个 64 MB tmpfs）→ `Out of Memory Error: failed to offload data block ... (57.3 MiB/57.5 MiB used). This limit was set by the 'max_temp_directory_size' setting`；`/data`（运行时卷）→ 查询完成且 `.tmp` 存在。中间那行就是**本行的处方是错的**的证据，也是没有猜任何数字的理由：**tmpfs 是内存，落在 tmpfs 里的溢写没有溢出去**，把 64 MB 调大只是把同一堵墙往后挪。修法是一行 —— runtime stage 最后一个 `WORKDIR` 是 `/data` —— 外加 `PYTHONSAFEPATH=1`，后者是让前者**安全而不是更糟**的那一半：`python -m uvicorn` 把 cwd 插到 `sys.path` 最前、在 site-packages 之前（实测 `-w /data` 下 `python -m site` 打印 `/data` 为 `sys.path[0]`，加上该变量后 `sys.path` 从 stdlib 开始），否则丢进数据卷的一个文件就能遮蔽已安装模块。两者都有测试钉住，且「DuckDB 的默认是相对路径」这一前提**钉在库上而不是钉在散文上**，哪天 DuckDB 改成绝对路径，论证是变红而不是变陈旧。**明确留给下游**：`016` 定制品内容地址（编码后的集成是几百行 `(str, float)`，秩基线只有三行，这个形状值不值得别的存储形式是 `017` 的测量）；`018` 定弃权词表（本模块两条常量是从 `alpha_baseline` **import** 的，不是第二种拼法）；`021` 定模型面与任何 re-export；`022` 定已知信噪比语料 —— 本模块三个语料无噪声模型、无随机数、只为让方向翻转；**特征重要度报告链上无人认领**（一列没被任何分裂用到就是不出现在编码集成里，读者可从 `parameters` 重算，但没有被发布的数）；**超参选择本身没有防泄漏机制**（没有内层验证、没有 early stopping、没有搜索，`013` 的 purge 管切分不管调参，这是第二个未被处理的泄漏面）。**变异两轮，绿基线上跑**（第一轮基线 60 passed / 77 passed，第二轮 67 / 77 —— `V2-P4-013` 的教训是红基线上的清扫每一条「击杀」都是同一个既有失败）。第一轮 67 个变异体、16 个幸存；**五处由删代码关闭而不是补断言**：`_pooled` 末尾那次 `rows.sort`（实测池在它跑之前**已经**是 `(prediction_day, ts_code)` 序 —— `sorted(by_day)` 排日、`rankable` 排日内，正是 `V2-P4-014` 删掉的第二次排序在这里原样重现）、`_grow` 的 `len(members) < 2 * min_leaf_securities` 闸（边循环已经在两侧各查一次，闸是第二份拷贝）、`_encode` 的 `sorted(entries)`（前序发射本就升序，且把保证交给 `AlphaModelArtifact.validate_parameters` 更响）、`_decode` 的 `sorted(by_tree)`（参数表按契约严格递增，字典插入序即排序序）、以及 leaf-only 拒绝里的 `and not trees`（分裂的行数约束不读残差，根不能分裂就永远不能）。三处是**语料缺陷**并按 `V2-P4-014` 的同一个陷阱修好：三列原本是 `[-1, 1]` 上的等距展开，即自身秩向量的仿射像，所以「跳过排名直接对电平分箱」的变异体在 `_pooled` 和 `predict` 里各活一次；列改成 `_skew`（严格单调、严格非仿射）后两条都死。四处补了**精确断言而不是笼统断言**：树深从解码后的集成里读回（原来只比较两种设置的参数条数，深度闸的 off-by-one 两边同时移动因而看不见）、分裂准则对着「总平方误差最小」的独立暴力枚举校验（并先断言该语料能把「除以计数」和「不除」分开）、tie-break 用**恰好相等**的两列（唯一能观察到它的形状）、`PYTHONSAFEPATH` 改成读 `ENV` 块而不是全文子串（原来的写法被自己的解释性注释满足，正是 `test_known_limitation_registries.py` 那条「散文不满足绑定」落在 Dockerfile 上）。第二轮 **60 个变异体、58 杀、2 活**，两条都照实分类：`sorted(by_day)` → `by_day` 是**本契约允许的任何语料都杀不掉**的（实测去掉后池序 200/200 变、制品 0/200 变，因为 `_grow` 的节点合计是 `math.fsum`、叶值精确舍入），保留是判断并写成判断；`WORKDIR /data` 挪到 COPY 之前是**等价变异体**（最后一条 `WORKDIR` 仍是 `/data`，容器行为逐字相同）。`KNOWN_TREE_LIMITATIONS` 七条，台账由 29 registries / 268 entries 变 **30 / 275** | S29, D13 |
| `V2-P4-016` | ~~内容寻址模型制品（训练截止/特征版本/参数/seed/代码版本/内容哈希）~~ **已完成** | 技 | 015 | **前缀 `mdl`，摘要取整个 `AlphaModelArtifact`**（含其内嵌 `AlphaModelDeclaration` 的全部字段），经 `domain/_identity.py::stable_model_id` —— 全仓唯一的模型规范化函数，`V2-P4-037` 立案的正是「第二个哈希」。落地为 `computed_field`，`AlphaModelArtifact.model_fields` **逐字未动**，这正是 `V2-P4-011` 那句「`016` 是增而非改」的兑现：没有任何已存行需要重新编址。**摘要输入是量出来的，不是照 D11 逐条抄的**：`tests/unit/backtest/test_artifact_address_collisions.py` 拿五个候选定义去跑 `V2-P4-013` 真折上`V2-P4-014` 真拟合出的制品，每个被否的候选都在一个**本仓真实出现**的用例上翻车 —— ①「只取声明」在同一 schedule 的两折上碰撞；②「制品减 `parameters`」在两套语料的同一折上碰撞（同 cutoff、同 32 行，系数一个 `-0.75` 一个 `0.0`）；③「制品减 `training_example_count`」在「面板晚两天开始」上碰撞（同 cutoff、同系数，24 行对 16 行）；④整个制品全过；⑤**加上 D11 的切分策略反而错在另一个方向** —— 只有 `test_day_count` 不同的两折产出**逐字节相同**的制品，加了它就是一个拟合两个地址，正是 `V2-P3-002` 的 `fetched_at` defect 换套词。**D11 两处悬案由此结案**：*切分策略*改变拟合的那一半（purge/embargo）**已经**通过它留下的 `training_cutoff` 与 `training_example_count` 进了地址，不改变拟合的那一半（测试块）不属于拟合，归 `FoldEvaluation.first_test_day`；*metrics* 是拿制品没训过的行对它下的判断，进了地址就等于让拟合的身份取决于事后怎么评它，而 `FoldEvaluation` 又按值持有制品，制品会装下装着自己的那些数 —— 这是 `V2-P3-014` 的拆法复用。**排除集是空的，且是被审计的空**：`ARTIFACT_UNADDRESSED_FIELDS` 按 `RUN_MANIFEST_UNADDRESSED_FIELDS` 的形状声明并**真的传进** `stable_model_id`，审计对 `AlphaModelArtifact` 与 `AlphaModelDeclaration` **两张**字段表做划分，第 n+1 个字段不被量到就红；`RunManifest` 排除的五类（两个墙钟、生命周期、恢复账本、宿主观测）本契约一类都没有。**本 issue 自证伪了四条**：① 以为时区会让两个 `==` 的 `training_cutoff` 得到两个地址 —— **不成立**，`ensure_aware` 早已 `astimezone(UTC)`；② `_identity.py` 写的「二十五个前缀在用」**两处都错**：那张手写表`assert len(prefixes) == 25` 是拿自己校自己（`V2-P4-012` 的 `feat` 四个 issue 无人发现），且其中七个是 `panel_doctor.FactorPlaneSeal` 的**数据集名**前缀、根本不是地址前缀（「七个带下划线」的说法即由此而来）—— 实测**26 个调用点、23 个不同前缀、无一带下划线**，且 `CONTENT_ADDRESS_PATTERN` 的形状并非 `stable_model_id` 独有（`set_digest`、`cross_section_digest` 同形），census 改为按 AST 从源码树读；③ 同理，`stable_model_id` docstring 的「十四个调用点」也已过期，改为指向活的 census；④ `V2-P4-010` 写的「`V2-P4-016` 填 `alpha_model_versions`」**填不了**：`run_cycle` 那条路上没有任何 `AlphaModel`，join 只有一行且已被断言，但真正填它的是 `021`/`017`。**发现并修好一个真洞**：`-0.0` 与 `0.0` 相等、在本仓一切算术里不可区分，`json.dumps` 却拼成两串 —— 两个 `==` 的制品两个地址，正是地址存在的意义被推翻的方向。`_unsign_zero` 在两个校验器里收口（实测两个已交付模型都到不了 `-0.0`：`_pearson` 的协方差是起始值为整数 `0` 的 `sum`，树叶值是 `math.fsum(...)/n`，都只出 `+0.0`；洞在**契约**里，因为 `AlphaModel` 是 Protocol 且 `validate_parameters` 原本收 `-0.0`）。**`V2-P0B-009` 的两个依赖当场实测**：commit 真（`resolve_code_commit()` 在本工作树返回 40 位 SHA），seed 真到「机制」为止 —— **三个模型没有一个读它**，两个 seed 拟合出逐字节相同的系数却是两个地址，F87 在模型面的复现，按实测写进注册表而不是说成已解决；`UNKNOWN_CODE_COMMIT` 是所有无 git 无 stamp 构建共享的**一个常量**，同样照实登记。**`CandidatePrediction.model_artifact_id` 收窄**为 `ALPHA_MODEL_ARTIFACT_ID_PATTERN`（`V2-P4-005` 留的自由文本、`V2-P4-011` 转交），`AlphaModelRef.artifact_id` **不收窄**（`domain/run.py` 要么 import 模型契约、把标签/日历的 import 重量压到每个 `RunManifest` 后面，要么把 pattern 写第二遍），代价写成 `the_manifest_slot_still_admits_an_address_from_another_plane`。**`V2-P4-015` 的摘要问题结案**：900 节点集成 **0.399 ms**、秩基线三行 **0.017 ms**，线性且比该模型 4.55 s 的拟合低四个数量级 —— 存储形态的问题仍是 `017` 的，摘要不构成理由。**变异两轮共 27 个、27 杀、0 活，两轮都在绿基线上跑**（334 passed）；第一轮两个幸存者都补成了断言而不是被解释掉：去掉 pattern 尾锚后 `mdl_<24hex>` 后面接任何东西都合法（`EXPERIMENT_ID_PATTERN` 记过的同类 defect），以及归一化改成收敛到 `-0.0` 时全部相等断言照样通过（`==` 看不见符号，改用 `math.copysign`）；第二轮两个幸存者暴露的是**先前无人覆盖**的两处：把 `isinstance(item, float)` 放宽到 `int` 与 `float` 的联合 会把声明的 `0` 变 `0.0`、把 `False` 变 `0.0`，以及 `AlphaModelArtifact.validate_parameters` 的两条拒绝全仓没有测试。`KNOWN_ALPHA_MODEL_LIMITATIONS` 由八条变**十一条** —— 本链第一次发现注册表里两条已经**为假**而不是不全，改写而非追加；台账由 275 / 69 变 **278 / 69**，registries 仍 30。**明确留给下游**：`017` 持久化、训练行本身的摘要（制品记的是行数不是行）、几百行 `(str, float)` 的存储形态；`018` 弃权词表；`021` 模型面与填 `alpha_model_versions`；`022` 已知信噪比语料 | S30, D11 |
| `V2-P4-017` | ~~**预测在结果已知前落库**（不可省）~~ **已完成** | 技 | 016 | 已交付 `domain/prediction_record.py` + `storage/predictions.py`。**「结果已知」是一个时刻，且不是新规则**：它就是同一 `as_of`/周期下 `OutcomeLabel` 的窗口最后一根收盘 —— `build_label_window(...).close_instant(exit_day)`，两个既有函数的复合而非对同一日历的第二次读法。**该时刻永不作参数**：`prediction_record_for` 只收 `TradingCalendar` 自己算（`artifact_for` 的规矩用在最该被撒谎的那个字段上）。**能核验什么、不能核验什么，逐条写明**：`predicted_at` 是 `predict` 的入参，全仓无一处能核验它 —— 故三档 `standing` 由计算得出而非声明：`forward`（生产与入库皆早于该时刻，唯一构成 S32 证据的一档）、`unwitnessed`（自称及时、本仓无法佐证）、`backfill`（生产于该时刻之后）。**倒填者最多走到 `unwitnessed`**：`FilePredictionStore.put` **没有 `recorded_at` 形参**，托管时刻取自 store 自己的时钟。**但对拥有磁盘的人一律无效**，`KNOWN_PREDICTION_RECORD_LIMITATIONS` 第一条即此句（照 `V2-P4-016` 对 seed 的写法），seed 的残留亦照录：两个 seed 仍给出逐字节相同的系数与两个地址，故一条记录指认的是**声明**而非一次运行。**D14 后半句最终不需要写侧规则**：回溯件与原件对 `predicted_at` 必然不同（一个早于该时刻、一个不早于），而 `predicted_at` 经 batch 进入地址 —— 两者**不可能同键**，故无物可覆盖。**读 `071`/`073` 之后才这么选**：`071` 把整分区替换改为追加并加守卫，`073` 实测该守卫只覆盖 merge 的一半，因为「携带全部 build 即携带全部证券 by construction」在构造被拆成两次之后就断了 —— 取其一：**消灭覆盖而非守卫覆盖**（本 store 无 merge，一记录一文档写一次）；取其二：`073` 的损失是在**读侧**才被发现的，故 `get` 每次重算地址、与文件名不符即拒。本设计仅存的一句 by construction 由 `PREDICTION_RECORD_UNADDRESSED_FIELDS` 对 `model_fields` 的逐字段审计守着。**层由契约排除而定，且契约条数是数出来的**：先写「四条 `backtest/` 契约禁其够到 store」，读解析后的配置发现为假 —— 只有 `backtest-studies-touch-no-store` 一条把 `openalpha_cn.storage` 列入 forbidden 且其 source 含全部三个 batch 生产者（`alpha_model`/`alpha_baseline`/`alpha_tree`），另两条 `backtest/` 契约根本不提 storage，`ranking-creates-no-portfolio-order` 只覆盖两个模块且都不产 batch。故真正隔在中间的是**两条、一个方向一条**：出向该条，回向 `storage-no-upward-deps`（后者也是被反序列化的契约必须在**下方**的理由）→ `domain/`。**`V2-P4-062` 的 Protocol 先例不适用**：那是因为契约在**上方**；此处在下方，直接 import 才换得到 `get` 的重算校验。**D12 第三条只有一半可由 store 强制**：前瞻那半即 `forward`（结果尚不存在，选择无从触碰）；回溯那半（选择过程中未碰最终 holdout）在记录里不留任何痕迹 —— store 能供的是**分母**（同一 as_of 下落过多少条），供分母不等于做校正。**`V2-P4-011` 的注册表问题就此结清**：存了行，故 `PREDICTION_RECORD_VERSIONS` 落地；但只有**一个**，因为 `read_versioned` 只按顶层 `schema_version` 分派，代价反向记明 —— 动嵌套四个契约中任何一个即是动本记录的版本。**`V2-P4-015` 的存储形态问题结清且不是靠数字**：5,545 只 × 900 参数整轮 4.9 ms / 394 KB（约为该模型 4.55 s 拟合的 1/930，取地址另计 3.5 ms），但真正定案的是**写出的字节就是取地址的那份 canonical JSON**，第二种存储形态即第二套规范化。**落地时改过一次设计、否掉过三个自己的说法**：(a) 带 `computed_field` 且 `extra="forbid"` 的记录**不能**经 `model_dump_json` 往返（`record_id`/`standing`/`artifact_id` 三个都被拒），本仓 `storage/sqlite.py:191` 早有答案 `exclude_computed_fields=True` —— 而这恰好是对的形状：写出的字节与取地址的输入同一；(b) 存储形态的三个数字先写后测，实测把 1.5/6.6 ms、421 KB 更正为 1.110/3.596 ms、394 KB；(c) 断言 `supersedes` 带尾随换行会被 pattern 拒 —— 实为被 `str_strip_whitespace=True` **规范化**掉，安全但理由不同，故 key 校验不得倚赖它。**变异两轮，两轮都在绿基线上跑**（79 passed / 82 passed）：第一轮 36 个、27 杀、9 活；九个活体中三个指向同一处设计缺陷（一条正则同时服务 pydantic 的 search 语义与 `fullmatch`，两者对锚点的需求相反）—— 拆成 `_ADDRESS_SHAPE_SOURCE` 无锚 + 有锚两份（`SHORTLIST_ID_PATTERN` 的先例与理由）；另有一个活体证明一条断言被自己消息的错误那一半满足（`UnknownSchemaVersionError` 引用的是 found_version）。第二轮 37 个、35 杀、2 活，两个都写明为等价变异（无锚 vs 有锚在 `fullmatch` 下行为相同；临时文件+改名 vs 直写在任何**已完成**的写入上不可区分，分野只在崩溃）。新增第 31 个注册表 `KNOWN_PREDICTION_RECORD_LIMITATIONS`（7 条），条目 278 → **285**；前缀普查 26/23 → **27/24**（`prd`）。`lint-imports` 仍 **8 kept / 0 broken**，无一条被放宽。**明确留给下游**：`018` 弃权词表；`021` 模型面与那次 join（故本 store 暂不进 `runtime/composition.py` —— 没有任何东西能往里放，十二个 store 中的一个填不进去就只是个字段）；`022` 已知信噪比语料；训练行的摘要仍无归属（本行原被指为其归属，实测记错了一个对象：`017` 存的是预测而非训练集）。 | S32, D14 |
| `V2-P4-018` | ~~stale 模型显式弃权~~ **已完成** | 技 | 017 | **stale measured against what**：`as_of - AlphaModelArtifact.training_cutoff`，与 `V2-P4-011` 泄漏地板**同两个瞬时、反向读**。`training_cutoff` 是**退出**会话的收盘（`V2-P4-011` 的决定），故这个间隔是「拟合消费过的最后一个结果变得可知」到「现在被问的那一刻」。**两者不是一根轴上的两个阈值**：地板是 `as_of < training_cutoff`、**拒绝**、不需要参数（消费了尚未印出的结果，谁问都是泄漏）；本条是 `as_of - training_cutoff > shelf_life`、**弃权**、离开声明的跨度就算不出来（一次拟合还能用多久是关于世界跑多快的主张，制品上没有任何字段测量它）——即本行原文的「最小版：过期即弃权，不做完整漂移检测」。**等号算新鲜**，镜像地板在另一端承认的等号。**跨度是自然日而不是会话，这是边界不是口味**：`domain/horizon.py` 明确拒绝把会话数换算成日历跨度（「乘一个没人测量过的每单位会话数常数」），而 `prediction_batch_for` 与它的三个调用方都不带日历；`a_shelf_life_is_wall_time_and_a_horizon_is_sessions` 是这句话落在读者会遇到的地方。**跨度归「问」而不归「拟合」，故不进任何制品字段**：`V2-P4-016` 已经为指标画过同一条线（把事后的判断放进拟合的身份，一个拟合会有多少个读者就有多少个地址）；实测两个都判过期的不同跨度产出**同一个** `record_id`，而过期批次与被打分批次是两个 `record_id` —— 后者是对的，它们是两个答案。代价是**门槛不留痕**：读者能从 batch 上的 `as_of` 与 `artifact.training_cutoff` 重算间隔，重算不出它没过的那道杠（`a_stale_record_carries_the_verdict_and_not_the_bar_it_failed`），答案体里以 `shelf_life_days` 呈现。**落点是 `prediction_batch_for`**，每个实现（含第三方）唯一都要经过的收窄点，正是该函数为 `require_features` 写下的理由；覆盖拒绝**先跑**，过期不是漏报证券的大赦。**「弃权不是免费」没有新建任何东西，这是实测而不是设计**：全程 stale 的 fold 一个都不打分 → 没有 `measured` 天 → `FoldEvaluation` 自己的校验器拒绝在非 `measured` 覆盖旁携带 `mean_rank_ic` → 头条是 `None` 而不是漂亮数字，`scored_ratio` 读 `0.0`。**真正的自证伪在「中途过期」**：原以为 stale 的 fold「看起来更差」，实测**不会** —— 它的头条只在新鲜的那几天上取，与一个只被问过那几天的诚实 fold 逐位相等；分开二者的是 `scored_ratio`（0.5 对 1.0）与 `test_day_count`，正是 `V2-P4-014` 拒绝让它们为 `None` 的理由。**第二次自证伪**：这条等式在 `V2-P4-013` 语料上是**空的** —— 那里每个测试日的 rank IC 恰好都是 `-1.0`，任何子集的均值都是 `-1.0`，断言分不出自己的两个答案（本链复发的那个缺陷）。有牙齿的那份在 `V2-P4-022` 的语料上，头条实测移动 0.02 以上。**弃权词汇（S35 的「编码理由」）**：三码三条件，`ABSTENTION_VOCABULARY`（`incomplete_features` / `unrankable_cross_section` / `stale_model`）+ `abstention_code`。`V2-P4-014` 的两句从 `backtest/alpha_baseline.py` **移到** `domain/alpha_model.py` 并原样 re-export（`__all__`，`alpha_tree` 仍从 baseline 读，`is` 同一性不变）：第三条必须由 `prediction_batch_for` 产出，而 `domain-purity` 禁 `domain/` import `backtest`，分两层的词汇是两个都不闭合的集合。映射方向是**码 → 句**：句子改写会让每条在存记录重新寻址，码不会。`Prediction.abstention` 保持自由文本（`V2-P4-011` 的契约），故 `abstention_code` 对没见过的理由答 `None` 而不是抛错 —— 否则「有分或有理由、绝不缺席」会对做对事情的第三方模型变回错误路径；`backtest/alpha_model.py` 的 `ABSTAIN_NO_VALUE` 就是被有意留在词汇外的那个，且被断言。**面**：`ModelRunRequest.shelf_life`（dataclass 无默认值）、`--shelf-life-days` / `shelf_life_days`（三个面，可省，省略即 `None` 且答案体记录 `null` —— `declared_feature_version` 的安排，不是「没人做的决定」）。**面上不拒绝任何东西**：过期只把 `scored_ratio` 压到 `0.0`，把它变成 exit 1／409 的是 `--min-scored-ratio`；声明 `0.0` 下限的调用方会把全弃权模型读成干净的成功 —— `an_expired_run_is_refused_only_by_the_coverage_floor_the_caller_declared`。**变异清扫两轮，39 个变异体**：第一轮绿基线 `242 passed`，35 杀 4 活；四个活体各有各的原因，且只有一个是「补断言」——① `cross_section.as_of` 换成 `predicted_at` 存活，因为**全仓每个夹具都把两者定在同一瞬时**，这是真缺口：`predicted_at` 是调用方的、`V2-P4-017` 明言本仓验证不了，按它量的 shelf life 是一个可以靠回填时间戳绕过的 shelf life，已补驱动断言；② / ③「daily 面丢掉跨度」「evaluate 面丢掉跨度」存活是**清扫自己的测试清单缺口**而不是断言缺口 —— 两个面只在 `tests/integration/test_model_interfaces.py` 端到端被驱动，把那两个 node id 加进清单后即被杀；④ 语料的列子集守卫存活，实测其中两条（未排序、空）是 `FeatureCrossSection` 自己的拒绝，于是**删掉**那两条只留「本语料没抽过的列名」。第二轮与最终树上各跑一次，均为 **39 杀 0 活 0 跳过**（绿基线 `246 passed in 23.92s`，还原后重跑仍绿）。台账由 32 registries / 301 entries 变 **32 / 304**（`KNOWN_ALPHA_MODEL_LIMITATIONS` 11→13，`KNOWN_MODEL_VIEW_LIMITATIONS` 15→16）。 | S35 |
| `V2-P4-019` | ~~批量上限提升/分片~~ **已完成** | 技 | 004 | 行内两处坐标已漂移，实际在 `api/app.py:192-193`。**1000 不是限流而是「不可表达」**：`V2-P4-004` 实测全市场 5,545 上市，超限请求在任何一项被调度前就吃 422；`BatchSubmitRequest.requests` 与 `BatchResearchTask.items` 各写了一份 1000，互不相干。现两处共读 `batch_contracts.MAX_BATCH_ITEMS = 10_000`（测试在**该上限本身**建任务并落盘读回，故是跑过的数而非打出来的数）。**`database is locked` 未能复现，且原因可测**：`open_state_connection` 全部 `timeout=10`、各 store 早已 `PRAGMA journal_mode = WAL`；32 并发写手 8 秒内完成 36,504 次事务、0 错误，单写手持锁 200ms 仍 0 错误；边界正好卡在忙等超时——持锁 9s 时 32/32 成功，11s 时 32/32 报 `database is locked`。**是时长故障不是并发故障**。**真正挡住全市场的是 O(N²)**：每次 item 迁移都整包 `model_dump_json` + 整包重解析（2N 次），空 runner 实测 N=100/250/500/1000 → 0.86/4.7/16.5/64.8 秒，外推 5,545 约 **33 分钟**。故把 item 拆成 `batch_task_items` 一行一条（migration 7 `split_batch_task_items`，带回读比对审计），单次迁移 5,545 规模实测 300ms → **0.72ms**；`json_set` 原地改 5.01MB blob 只到 25.3ms 且仍是 O(N)，故否决。**并发上限 32 → 8，明说是下调**：O(1) 化后 N=600、10ms IO runner 实测 57/114/211/184/201/216 items/s（1/2/4/8/16/32），4 之后进噪声带；真 `ResearchEngine`（GIL 密集）N=400 更早在 2 就平；修前 32 反而更慢（N=1000 空 runner：1 并发 64.8s vs 32 并发 85.8s）。**遗留依赖**：`backtest/cross_section.py::MAXIMUM_SHORTLIST` 仍写死 1_000（原意就是「照抄批次上限」），本任务无权改 `backtest/`，见该测试 docstring | S43, D22 |
| `V2-P4-020` | ~~修 O(N²) recovery 写放大~~ **已修复** | 技 | 004 | `runtime/engine.py:274-292`。**本行的 78 被逐字复现**：空 agent 上实测 N=12 → 78 次结果序列化，N=200 → 20,100 次 / 11.74 MB，N=400 → 80,200 次 / 46.68 MB，皆为 N(N+1)/2。**两半都是二次的，故两半都改**：engine 侧 `_updated_recovery` 的 `model_dump`+`model_validate` 往返 N=400 时 0.327s，store 侧 `model_dump_json` 0.082s。修法照抄本仓 `V2-P4-019` 的 `split_batch_task_items`：新表 `run_recovery_results` 一行一个 **agent 槽位**（`position`、图声明的 `agent_id`、完成前为 `NULL` 的 `payload`），`RecoveryStore` 增第三个方法 `append_result` —— 主键上一条 `UPDATE`，由 `agent_id = ?` 与 `payload IS NULL` 两个 `WHERE` 守卫，写错槽位或覆盖已完成槽位当场具名拒绝，而不是留到下一次 `get()` 变成一份读不回来的状态。`agent_ids` / `completed_results` / `next_agent_index` 三个字段不再入 header，改由槽位行**读时派生** —— `validate_progress` 的前缀不变式因此由读的构造保证而非由两个写手约定。实测修后**每个结果恰好序列化一次**（N=12/100/400/800 的 ser/N 均为 1.0），N=400 由 46.68 MB 降至 0.23 MB，循环墙钟按 agent 数持平。**不写 migration，且这是决定不是遗漏**：拆分前的行 payload 里带着 `completed_results`，`get()` 按该 key 是否存在识别（`_split_batch_task_items` 自己的判别式）整份读回，`append_result` 在第一次找不到槽位时就地拆分；recovery 是运维状态而非账本（`migrations.py` 自己的补救文案就把「删掉这些行」列为受支持的答案）。**顺带修一处会静默失效的读**：`_refuse_uncountable_stored_horizons` 只读 `run_recovery.payload`，而整个库里唯一存完整 `SignalFrame` 的地方刚刚搬家 —— 现在两张表都读，且该性质由测试分得开（把该段去掉，测试红）。验收：`tests/integration/test_recovery_write_amplification.py`（计操作不计秒，比 N 与 2N）与 `tests/integration/storage/test_recovery_result_slots.py`（两个拒绝、遗留 blob 读回并就地转换、`ON DELETE CASCADE`、横向 migration 仍看得见信号） | D22 |
| `V2-P4-021` | ~~排序与模型的 REST + SDK + CLI 面~~ **已拆分**：排序面移到 `V2-P4-033`（已完成），模型面（`model-evaluate`、`daily-run`）**本行已完成** | 产 | 017 | **P4 产品验收（2026-08-19）实测的排序期错误**：本行原把「排序面」与「模型面」捆在一起并依赖 `017`，而 `017` 在 `010→011→012→013→…→017` 这条 11 条串行链的深处；排序面真正的依赖只有 `005` 与 `023`，两条都已完成。捆绑的后果不是延期而是**用户被推上一条绕行路**：今天唯一能走通的做法是把 5,545 只全部送进 `research/batches` 跑完、再用 `/api/v1/screen` 重排 —— 恰好是「先研究后收敛」，正是 `V2-P4-004` 的两段漏斗设计出来避免的那件事；而 `V2-P4-019` 把批次抬到 10,000 让这条绕行路走得通，反而把它固化了。**模型面这一半在 `694f822` 上的实测与排序面当初逐字相同**：`010`–`017` 八条契约（`AlphaModel` 协议、版本化特征矩阵、带 purge/embargo 的 walk-forward 切分、两个基线、内容寻址制品、预测记录）在 `tests/` 之外**没有一个调用者**；`openalpha --help` 十个命令里没有 `model`，全部路由里没有一条路径含 `model` 或 `prediction`，`OpenAlphaSDK` 没有任何方法会拟合东西；而 Story S32 标了「不可省」的 `FilePredictionStore` **不在组装根里**，理由是 `017` 自己写下的「没有任何东西能往里放」 | 集成：**测试必须从 CliRunner / TestClient / SDK 出发**。**已完成**：交付 `src/openalpha_cn/model_view.py`，第五个顶层研究面族（`model_*`）。**两条命令因为是两个问题**：`model evaluate` 按 walk-forward 切分逐折拟合一次、逐折报 `V2-P4-014` 的五个统计量，**不存任何东西**；`model daily-run` 用「在它所预测的那个时刻之前**已经收盘**的每一条标签」拟合，给那个横截面打分，然后把批次交给存储 —— `recorded_at` 取自 store 自己的时钟而非调用者。**层的选择被排除出来**：`backtest/` 结构上不可能（`backtest-no-numeric-stack-or-panel-plane` 按全传递可达对整个包禁 `openalpha_cn.panel*`、`openalpha_cn.feature_matrix` 与每一个面，`backtest-studies-touch-no-store` 禁三个 batch 生产者够到 `openalpha_cn.storage`，`storage-no-upward-deps` 禁回向边 —— `storage/predictions.py` 的 docstring 早已把这个 join 按名字留在本行）；`factor_view.py` 是更近的候选但被否决 —— 那个模块在一个闭区间上回答问题却**不拟合任何东西**，而一次 walk-forward 拟合带着训练跨度、purge、逐折制品与一条落库的预测，合并会得到一份两半不相交的请求契约。落地当天 `test_every_top_level_module_is_a_declared_leaf_or_a_member_of_a_discovered_family` **就红了**（它的 docstring 早写明第五个族会长这样），按它给的第一条补救加入被发现集合并领 `RESEARCH_PLANE_SEAM_IMPORTS` / `RESEARCH_PLANE_DATASETS` 两张表的行；本行的 seam 表是**第一个不全是 `panel_*`** 的，25 个名字里 12 个来自 `openalpha_cn.feature_matrix`。**`require_declared_features` 终于有了调用者，且形状是被 `V2-P4-046` 量出来的**：`--feature-version` 省略时由本次声明的列解析（`--code-commit` 的规矩 —— 没人能手写一个 `feat_` 摘要），显式给出时被校验、不一致按名字拒，三个面都是 `bad_request`；答案记着到底是哪一种（`feature_version_source`），因为解析出来的那份只证明「制品记录了它拟合时用的配方」，**不证明有人打算用这个配方**。`V2-P4-012` 与 `V2-P4-014` 两处指针（「`014` 是第一个调用者」→「第一个把声明与矩阵握在一起的组合点」）就此兑现。**`RunManifest.alpha_model_versions` 由 `daily-run` 填上，且只由它填**：`010` 声明该槽并点名 `016`，`016` 实测 `run_cycle` 那条路上没有任何 `AlphaModel` 而转手，`017` 从存储侧得到同样结论并写「仍然无人认领」。一次 daily run 是一次 run —— 它有 `RunMode.daily`（该成员的 docstring 本来就点名 `daily-run`）、一个 `as_of`、`code_commit`、`config_digest`、声明自己的 `random_seed`，和**恰好一个**被消费的量化制品。`run_id` 由预测自己的内容地址派生（`daily-<record_id>`），所以同一天重跑在**两个**存储上都报 `unchanged`，而不是其中一个抛 `DuplicateRecordError`。**`evaluate` 不写 manifest 也不登记预测，两个「不」都是结论**：它每折拟合一个制品、一个都不据以决策，一个点名 K 个制品的 manifest 是把一次研究记成一次生产周期；而 `evaluate_fold` 把每个 batch 记为 `predicted_at = section.as_of`（一次被模拟的预测的时刻就是它模拟的那个时刻），那个时刻按构造已过去，所以它能登记的**每一条**记录都会是 `unwitnessed` —— 往 S32 的登记簿里灌回测只会把它存在的理由（`forward` 行）埋掉。留下的替代方案（把登记簿当**分母**用，即 `domain/prediction_record.py` 说多重检验政策需要的那个计数）如实写进注册表而非实现。**「被拒 ≠ 空」在两个面上各做一次**：`--min-scored-ratio` 两面都无默认值，它是 `FoldEvaluation.scored_ratio` 存在的那个理由（弃权是免费的）落到面上；同一个 store、同一条命令行、**只差一个开关** —— `0.9` 得 exit 1 / HTTP 409、`is_blocked: true`、`admitted: null`、`blocks` 带 measured 0.875 与 required 0.9 和两个计数；`0.0` 得 exit 0 / 200 与**逐字相同**的 `measurement` 体。**被拒的 `daily-run` 仍然把预测登记了**，`record_id` 就在那个 `409` 体上 —— S32 说的是预测要在结果已知之前落库（无条件），下限说的是这个答案能不能被拿去用（有条件），`run_shortlist` 存被阻塞的榜单是同一条理由。**`V2-P4-017` 的诚实必须活过渲染**：每一份被渲染的预测都带 `standing_proves` 与 `standing_does_not_prove`，`forward` 那一条在**答案体里**写明 `predicted_at` 本仓校验不了、也没有任何东西防得住拥有这块磁盘的人 —— 一个只印 `"standing": "forward"` 的面会把一条单机记账事实变成读起来像第三方背书的东西。终端渲染也带这两句。**面板前置条件与榜单面互有缺口，两个方向都实测**：本面要 `adj_factor`（标签是**两个交易日之间的收益**，`label_outcome` 要复权序列、`window_return` 拒够不到窗口的序列）而不要 `namechange`（本面不构造任何 `MarketBar`，从不问 `is_st`）；榜单面反过来。两边的 `409` 都按 `V2-P4-078` 的规矩写出修复它的那条 `panel build`。**`feature_matrix` 加了一个函数**：`stored_cross_section_instants`，取**交集**而非并集（`_resolve_instant` 的既有规则读向前方），这是「一次 walk-forward 能收一个日期区间而不是一个时刻一个 flag」的全部所需。**语料每个数都是量出来的**：horizon 取 `1d` 是因为十个会话的面板上 `5d` 会把每一折的**每一条**训练样本都 purge 掉、`walk_forward_folds` 直接拒掉那个 schedule；第一个会话没有 build 是因为 `reversal_1d` 声明 `lookback_sessions=2`；收盘要动否则每个点都是 `degenerate_returns`;特征次序按**三会话**周期扰动（先按隔会话写，实测两折得到**逐字相同**的均值与离散度 —— 那样的 fixture 分不出「逐折渲染」与「渲染第一折两次」），改后两折为 `0.9107 / 12.0208` 对 `0.9821 / 38.8909`;一只在册证券不在任何 build 的 `subjects` 里，这就是 `scored_ratio` 为 `28/32` 的来源，也是那对「只差一个开关」的驱动条件。**自证伪八条，其中五条是变异清扫逼出来的**：① 隔会话扰动上两折统计量逐字相同（上一句）；② 预估相邻对换在八名上是 Spearman `0.976`，实测 `0.964` —— 弃权那只离开总体后 n 是 **7** 不是 8；③ 组装根的注释先写「三条 lint-imports 契约隔在中间」，`storage/predictions.py` 早已把它量成**两条、一个方向一条**（它自己也曾写成四条并改过），落地前改正；④ 语料**分不出** `trainable_at` 与「拿全部样本」—— 对 2026-01-16 作预测时每一条训练标签都已收盘，两个答案重合，改为并排驱动 01-15（`training_day_count` 6 对 7）；⑤ 语料**也分不出** `scored_count` 与 `paired_count` —— 模型弃权的那只恰好也是标注器拒绝的那只，故该性质移到对聚合本身的单测（分得开的语料是 `V2-P4-022` 的）；⑥ 一条名为「没有 tier 分隔符就该被拒」的测试**够不到**那个检查：`_cli` 拼的是 `f"{factor}@{tier}"`，空 tier 仍带着 `@`，被下一个函数拒掉 —— 变异体找到了一条以自己没测的东西命名的绿测试；⑦ 泄漏测试先断言 `"data.parquet" not in body`，实测 `_without_store_path` 抹掉的是**部署位置**、保留 store 内的相对分区路径，而后者恰是可操作的那一半（每个安装都相同），断言方向反了；⑧ 以为命令行会打印带路径的本地消息（它在持有 store 的进程里），实测 `cli._model_fail` 把 **`disclosable`** 交给 `_panel_fail`，与 `_shortlist_fail` 逐字相同 —— 唯一读到本地那份的是**握着异常的 SDK 调用者**。**变异清扫 **三轮，每轮 49 个变异体，三轮都在绿基线上跑**（82 passed / 93 passed / 96 passed）：第一轮 30 杀 19 活，其中 6 个是本目录自身的锚点失效（`ruff format` 在目录写完之后重排了代码），真幸存者 13 个；第二轮 47 杀 2 活；第三轮 **49 杀 0 活**。13 个真幸存者里**两个的答案是删代码而不是补断言** —— `_prediction_instants` 的 `sorted()`（`stored_cross_section_instants` 已按升序返回、`astimezone().date()` 对时刻单调、dict 保插入序，故它不可能改变答案；真正守住这条性质的是下一层 `labelled_panel` 对非严格递增预测日的具名拒绝）与 `cli._model_instant` 的第二份 tzinfo 检查（`daily_request` 的 `_aware` 已按名字拒绝裸时刻，命令行只留下契约结构上看不到的那一半：一个根本不是时刻的字符串）；**两个下沉到该被断言的层**（`scored_count` 对 `paired_count` 是聚合的性质，语料分不开，故用构造出的 fold 做单测；`minimum_scored_ratio` 的默认值三个面都到不了，故改为「`ModelRunRequest` 没有任何字段带默认值」的结构断言，下一个参数一并被守住）；其余九个补的是面测试。第二轮的两个幸存者**都是测试驱错了形状而不是设计有错**，追它们又逼出上面第 ⑥⑦⑧ 三条自证伪**。新增第 32 个注册表 `KNOWN_MODEL_VIEW_LIMITATIONS`（9 条），台账由 31 / 285 变 **32 / 294**；`lint-imports` 仍 **8 kept / 0 broken**，无一条被放宽，且**实测新条目会响**（`backtest/` 下的探针 import `openalpha_cn.model_view` 使该契约 broken，删除后恢复）。运行时依赖仍**九个**。**明确留给下游**：`018` 的弃权词表（`Prediction.abstention` 仍是自由文本，本面原样透传）；`022` 的已知信噪比语料（本面产出的每个数都来自一份合成面板，不是关于 alpha 的任何主张）；把一次 evaluation 封存为可寻址产物（`factor run` 有 `experiment_id`，本面没有对应物）；`--start`/`--end` 之外的排程（cron 由 PRD S5 归 `openalpha` 之外） | S83, S84 |
| `V2-P4-022` | ~~已知信噪比合成数据集（含已知 alpha / 已知 null 对照）~~ **已完成** | 测 | 013 | 已交付 `tests/known_signal_corpus.py`（`alpha_model_fixtures` / `walk_forward_fixtures` / `panel_fixtures` 之后的第四个顶层夹具模块）+ `tests/unit/backtest/test_known_signal_corpus.py`。**一次抽样服务两条臂**：每个（预测日, 证券）抽三个独立标准正态 `signal`/`decoy`/`noise`，实现收益 `RETURN_SCALE * (beta * signal + noise)`；alpha 臂 `beta=0.35`，null 臂 `beta=0.0`，**其余逐位相同**。这才叫**对照**而不是第二份语料：两臂的横截面被断言逐对相等，任何差异只能归到那一次乘法。**已知 IC 是闭式而不是读数**：`signal` 与 target 联合正态 ⇒ Pearson `beta/sqrt(beta^2+1)`，Spearman 走双变量正态恒等式 `rho_s = (6/pi)·asin(rho/2)`，`known_rank_ic` 只吃 `beta`、不碰任何面板（用被测代码去读出「已知」IC 是同义反复）。alpha 臂 **0.3169**，null 臂**恰好 0**。**实测**（rank baseline，3 折 × 6 天）：alpha 臂 `0.2855 / 0.2936 / 0.3724`，系数 `signal≈0.306`、`decoy≈0.01`；**null 臂 `-0.0089 / -0.0085 / -0.0334`，系数全部 \|c\|<0.03** —— 即「在纯噪声上报出技能」这个本 issue 存在的理由所指的失败，没有发生。5×4 排程下 alpha 最低折 `0.2705` 高于 null 最高折 `0.0808`，两臂不重叠。**能分开拟合过与没拟合过的模型，三种方式**：① 在 null 臂上拟合、读 alpha 臂 = `-0.2459`（对 `+0.3442`）；② 把系数换成等权（decoy 与 signal 同权）= `0.2225`，比拟合低 0.12，且制品的声明/特征表/训练截止/样本数全部held住，差异只能归到系数；③ **同一份抽样的单列版本读数逐位相同**（`0.35012503473187` 对 `0.35012503473187`）—— 模型面产品验收测到的「单特征下报告统计量对拟合数学不变」在这里被复现并驱动，这就是语料带第二列的全部理由。**分不开什么，写在读者会遇到的地方**：① 任何 fold 统计量都分不开泄漏折与净折（`V2-P4-014` 实测两者恰好都是 `-1.0`，rank 相关对量级不变，泄漏在系数里），本语料也**不种泄漏**，`V2-P4-013` 的夹具仍是那个；② **分不开一个真实大小的 alpha**：null 臂自己的折能飘到离零 `0.1129`，比现实 IC（0.03）还远，所以按现实 beta 种的语料连自己的两条臂都分不开 —— `ALPHA_BETA` 故意大得不现实，「能分开自己对照组的语料」比「数字看着可信的语料」值钱；③ 不是关于 A 股的任何主张。**自证伪一条**：模块原先写「`1d` 周期下没有两个预测日共享会话」，**是错的** —— `build_label_window` 把 `1d` 窗口放在 `(k+1, k+2)`，相邻两日共享一个端点，purge 在每个折边界实测剔除 2 个预测日 × 60 只 = 120 条。留下的是更窄也是闭式真正需要的那句：`1d` 窗口实现的是**退出会话**的收益，没有两个预测日共享退出，共享的入场会话对两者的数都没有贡献。**不替换 `scripts/generate_replay_corpus.py`**，且这是纠正而不是推迟：那份 300 条是 agent **事件**，没有特征列、没有标签、没有模型，喂的是 `tests/replay/test_frozen_corpus.py` 的 300/300/0 **replay** 确定性断言；替换等于删掉 replay 语料去换一个那个平面用不上的 benchmark，还会为一个它本来就不建模的日历重写全部 300 个 `run_id`。该脚本的指针已改写。**住在 `tests/` 而不是 `src/`**：与前三个夹具同列，代价是 `lint-imports` 完全够不到它（契约根在 `openalpha_cn`），故只 import `domain/` 与 stdlib `random`——即它若在 `backtest/` 下本来就要守的那条线；`scripts/verify_publication.py` 是另一条约束，所以面板一律运行时生成而不是签入。`random.Random(SEED)` 而不是模块级 `random`，`backtest/event_study.py` 的先例。运行时实测 0.05 s／臂。**不新增 `KNOWN_*` 台账**：审计只扫 `src/openalpha_cn`，故边界由模块 docstring 承载并被一条断言钉住。本语料的变异体与 `V2-P4-018` 同一轮清扫（共 39 个，最终 39 杀 0 活）：闭式塌成 Pearson、arcsine 少了折半、噪声项被删、plant 落到 decoy 列、beta 被忽略、收益取反、价格路径错一个会话、两列对调、换一个 seed、两条臂分开抽、decoy 变成 signal 的副本——全部被杀，即语料的每一条构造性质都有断言在看。 | T9 |
| `V2-P4-023` | ~~**榜单级 tradable-ratio + freshness 闸门**~~ **已完成** | 技 | 005 | 已交付 `backtest/shortlist_gate.py`（第十二个纯 stdlib 叶子）。**层的选择被排除出来**：`domain/` 结构上不可能（`domain-purity` 禁 `openalpha_cn.backtest`，而 `CandidateRanking` 就是本闸门的全部输入，它连自己的参数都带不进去）；`product/` 什么都不禁，能够到组装根的闸门可以绕过它自己的拒绝；放顶层与 `panel_gate.py` 并列则会**不在任何契约的 source 集里** —— 正是 P3 装 `test_import_layering.py` 要挡的那个缺陷（`panel_gate.py` 在顶层是因为它**消费**面板层，本闸门既不碰 store 也不碰面板，放那里只会白继承那份自由）。**「被阻塞」与「空」做成结构上不可混淆**：`admitted` 在阻塞态**抛异常**、在准入且为空时返回 `()`，而 `bool()`/`len()`/迭代在**所有**状态（含已放行）下一律抛 —— 照搬 `DependencyClearance` 的设计及其实测理由；漏斗一个都没筛出来时给 `researched_ratio_not_measurable` 而不是"放行且为空"。**阈值进身份**：`ShortlistGateSpec` 整体嵌入 `ShortlistGateManifest`，逐一变动每个门槛都要求 `gate_manifest_id` 移动，反向另有一条；`GATE_MANIFEST_UNADDRESSED_FIELDS` 为空且对着 `model_fields` 审计。**一处由实测而非设计决定的选择**：原打算复用 `CrossSectionFunnel.tradeability.tradeable_rate`，实测它会被**覆盖率闸门本该抓的那些证券抬高** —— 停牌股无 bar → 价格因子无值 → 第一阶段即被剔除 → **离开分母**（3/7 = 0.4286 对 3/8 = 0.375），故改为除以全股票池；该选择还**消掉一个阻塞码**（`tradeable/universe` 恒可测，`tradable_ratio_not_measurable` 会是没有输入能走到的分支）。**新增第 24 个注册表 `KNOWN_SHORTLIST_GATE_LIMITATIONS`（7 条）**。`lint-imports` 仍 **8 kept / 0 broken**，三条契约被**加宽**（覆盖更多模块）无一放宽；其中把 `ranking-creates-no-portfolio-order` 扩到本模块**不是任何测试逼出来的**，理由是一个能构造 `PortfolioOrder` 的闸门会成为「这份清单被拒绝了」与「有人拿它下了单」可以同时为真的那个模块（契约 `id` 未动，`test_candidate_ranking.py` 按名读它）。22 个变异全红，每轮清 `__pycache__` 以打掉 `(mtime, size)` 假绿 | S14, S48 |
| `V2-P4-026` | ~~**`daily_basic` 的 as-of 敏感会话级读**（`V2-P4-013` 的硬前置，见 §11 的 `V2-P3-004` 复审小节）~~ **已完成** | 技 | P1 存储契约 | 走的是第二条路（显式门），不是换分区粒度：`load_daily_valuations` 改走 `panel_ingest._read_visible_price_session`，即 `read_visible_at` + `filters={"trade_date": day}`。安全性来自实测的形状而非论证 —— `_daily_close_timeline` 把每一行的 `available_time` 定在该 `trade_date` 当天 16:30，故**一个会话的行共享同一个可得时刻**，会话读要么全可见要么全被扣住；「被扣住」与「不存在」是两组不同的数字，而且都被具名拒绝或如实作答。换粒度被实测否决：`_session_census` 的下界是 `date(year, 1, 1)`（其 docstring 明写「三月才开始的分区正是这个守卫存在的理由」），月分区会被它整块拒绝，而放宽它是放宽一条 fail-closed 守卫 | 集成：`tests/integration/panel/test_factor_neutralizations.py::test_a_residual_built_at_a_mid_year_as_of_is_visible_at_that_same_as_of` | S27, S28 |
| `V2-P4-027` | ~~**`index_member_all` 的 as-of 敏感读**（`V2-P4-026` 之后 walk-forward 剩下的唯一存储侧瓶颈）~~ **已完成** | 技 | 026 | `V2-P4-026` 落地后，中性化在年中 `as_of` 上唯一还会被整块拒绝的输入是 `index_member_all`：`load_industry_histories` 走 `read_if_ready`，判定读的是分区的 `max_available_time`，所以一个成员年分区在其最后一次调整生效之前一律不可读。真实语料上那是年度成分股调整（613 条 2021-07-30 起、255 条 2022-07-29 起），于是一个「今天抓取、回放历史」的 walk-forward 每年撞一次。**不能照抄 `026` 的解法**：`index_member_all` 事件驱动，分不出「被扣住的行」与「不存在的行」，`SecurityIndustryHistory.answerable_through` 就是为此存在，行过滤会把 fail-closed 的拒绝变成看似合理的短答案。可行方向是给 `load_industry_histories` 一条**区间感知**的门（读到 `day` 为止且能声明 `answerable_through`），或给该数据集换分区键。外层还有 SW2021 的 2021-12-13 可得性地板，它不是本条要解的，但它决定了任何解法的最早 `as_of` | 集成：年中 `as_of` 能在一个含年内调整的成员分区上装出 cross section，且分不出的情形仍具名拒绝。**已完成（存储侧先落地，产品路径由 `V2-P4-028` 接通后本行关闭）**：新增 `panel_ingest.load_industry_cross_section(store, *, day, years, as_of, max_staleness, date_timezone)`（私有 `_read_visible_membership_rows` 支撑），`load_industry_histories` **原样不动、仍拒绝同一个读**。走**区间感知门**而非换分区键，且**否决换分区键的理由与本行原先的猜测不同**：实测 `index_member_all` 走 `write_industry_memberships → split_panel_batch_by_year → write_panel_batch`，**根本碰不到** `_session_census` 的 `date(year,1,1)` 下界（那条只从 `write_daily_panel` 与 `_refuse_missing_factor_sessions` 到达），`_validated_coverage` 的跨年检查在 2024 成员分区上测得 `[]`；真正的否决理由是：单靠闸门已足够（在未改的年键上，最新可得为 `2024-07-29T16:00Z` 的分区已能作答），而单靠细分区键**不足**（读不再*点名*后面的分区，`answerable_through` 的「跳过了一个已存分区」风险由每年一次变为**每读一次**），成本 422 处 `year=` 站点 + 六张目录表的 `year` 列。**门不返回 histories**：`answerable_through` 是**年**粒度，年中 `as_of` 没有诚实的年可报（报 2023 等于拒绝被问的那天，报 2024 等于许可一个六月的 `as_of` 答不了的十二月问题），故门收 `day`、内部解析、对答不了的日子具名拒绝。**「被扣住」与「不存在」靠每年普查计数的相等性分开**（被扣住 = 普查数到且被谓词移除；不存在 = 从未数到），任何差异即拒绝 —— 比 `026` 的「两种许可形状」更强。最早可用 `as_of` = **2021-12-13 00:00 Asia/Shanghai**（SW2021 生效时刻，从 `INDUSTRY_TAXONOMY_EFFECTIVE_FROM` 读出而非重述），此前**整库具名拒绝而非空截面**；本行不移动该地板。16 个变异全红，其中 **M13/M14 第一遍是绿的** —— M14 因为 fixture 里没有任何 `as_of` 正好落在存储事件日上，`<=` 与 `<` 分不开，补测后才转红，即本仓那个「断言存在但分不开两个答案」的形状出现在测试自身并被抓住。**本行的关闭条件（产品路径）已由 `V2-P4-028` 兑现**，见下一行；该门此前只有 `tests/` 里的调用者，`src/` 中无人走。**本轮复核未改动本行交付的任何存储侧代码**：`load_industry_cross_section` / `_read_visible_membership_rows` / `_read_visible_event_dated_rows` 逐字未动，新增的都是产品路径上的调用与测试 | S27, S28 |
| `V2-P4-028` | ~~**把中性化的产品路径接到 `V2-P4-027` 的新门上**（存储侧已有区间感知门，调用方仍走旧门，故年中 `as_of` 的边界在用户站的地方没有变）~~ **已完成** | 技 | 027 | `panel_neutralization.load_industry_market_cap_cross_section` 约 2241 行仍调 `load_industry_histories`；改为 `load_industry_cross_section(store, day=day, ...)` 并把 `_industry_answer` 的 history 查找换成 `cross_section.get(subject)`（它已经在读 `IndustryAnswer` 的 `assignment`/`is_backfilled`）。两处连带：`tests/unit/test_panel_ingest_import_isolation.py::RESEARCH_PLANE_SEAM_IMPORTS` 需把 `load_industry_histories` 换成 `load_industry_cross_section`（`test_the_neutralisation_reaches_its_two_foreign_datasets_only_across_the_seam` 按旧名断言）；`_industry_answer` 第三折会从计数型 `industry_missing` 变成具名拒绝，属**行为变更**。**这条不做，`V2-P4-027` 就是 P3 验收那个根因的重演** —— 库调通了，用户站的地方没通 | 集成：中性化在年中 `as_of` 上能读到含年内调整的成员分区。**已完成（2026-08-24）**：`load_industry_market_cap_cross_section` 改走 `load_industry_cross_section(store, day=day, years=membership_years, as_of=as_of, max_staleness=..., date_timezone=...)`，`_industry_answer` 收 `Mapping[str, IndustryAnswer]` 并只做 `cross_section.get(subject)`。**数字**：同一份 fixture（`WIDE_SHAPES`，2026 分区含 2026-01-14 起的年内调整、`max_available_time` 2026-01-13T16:00Z）上，十个会话的窗口中截面原先只在 **3/10** 装得出来（`SHAPES` 是 5/10），现在 **10/10**；`SHAPES` 上更早的历史读数是 `V2-P4-026` 之前的 1/10。**驱动面是命令行而不是库**：`openalpha factor build --tier neutralized` 在 2026-01-08 / 01-09 两个年中预测时刻上原先 exit `blocked`（`not_yet_knowable`），现在三档全写；`test_the_dead_end_the_acceptance_review_found_is_closed_end_to_end` 的第五步由「按名字拒绝」变成「三档全存」，并加了第六步 —— 同样两天上的 `factor run` **答得出来**，那条 P3 验收的死路到此闭合。**「被扣住」与「不存在」的对照语料建在产品路径上**：`SECURITIES[1]` 在 2026-01-07..01-13 有真实覆盖洞（→ `without_industry`，是数据），把它 2026-01-06 那条**关闭行**的 `available_time` 推到 `WITHHELD_UNTIL`（等于分区原有的 `max_available_time`，故两份分区的目录记录不可区分）后，裸行过滤会把它读成一条从未关闭的区间并在 01-09 给出一个它当时已经离开的行业 —— 普查按名字拒绝，且哨兵是同一份 store 在所有行可见之后又答得出来且答案与诚实 store 一致。**行为变更（已签字）**：`_industry_answer` 第三折（「这次读答不了那一天」）由计数型 `industry_missing` 变为具名拒绝，并在产品路径上驱动（`membership_years` 收窄到不含被问那天所在年 → 具名拒绝 + 哨兵）。**两个 `KNOWN_*` code 改名**（沿 `V2-P4-026` 在同一条上的先例）：`KNOWN_FACTOR_RUN_LIMITATIONS.the_builder_cannot_produce_a_residual_before_its_years_stored_horizon` → `..._for_a_session_that_has_not_closed`（第三档现在只窄一个会话：预测时刻须在其所在日收盘之后、且当日开市）；`KNOWN_NEUTRALIZATION_LIMITATIONS.the_industry_input_is_read_whole_partition_so_a_mid_year_as_of_can_be_refused` → `a_stored_membership_year_left_unread_refuses_the_day_rather_than_answering_it`（剩下的只有调用方自己的收窄成本）。台账仍 **32 / 301**，运行时依赖仍**九个**，`lint-imports` 仍 **8 kept / 0 broken**。**受阻依赖**：`src/openalpha_cn/backtest/factor_ic.py` 由同期兄弟 agent 持有，其 `a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule` 条目仍写着 「index_member_all is read whole partition (V2-P4-027, KNOWN_NEUTRALIZATION_LIMITATIONS.the_industry_input_is_read_whole_partition_so_a_mid_year_as_of_can_be_refused)」，该句现已为假且引用了一个不再存在的 code，须改为「index_member_all is read one day at a time (V2-P4-027/028) and states no bound of its own; the only refusal left from outside this module is that no cross section before 2021-12-13 is assemblable at all」。**未做且不属本行**：榜单面的 `neutralized` 档仍具名拒绝 —— `V2-P4-033` 记的理由（「其加载器经 `read_if_ready` 读 `index_member_all`」）已被本行证伪，但 `run_shortlist` 本身仍不加载任何暴露截面，缺的是**请求契约**（成员年、交易日历、中性化声明）而不是可读的分区，该条目已按此改写 | S27, S28 |
| `V2-P4-029` | ~~**`DeliberationCommittee.review` 对任何弃权信号必崩**（生产路径可达，非理论问题）~~ **已修复** | 技 | — | **两个产品面都实测复现**：`POST /api/v1/research/deliberate` 返回 `500` + `text/plain` 的 `Internal Server Error`（无 `reason`、无字段，客户端无从分支），`OpenAlphaSDK.deliberate` 抛 `ValidationError: directional signal requires evidence`。修法不是放宽注解让算术去决定 —— 弃权信号 `strength == 0`，一场真实辩论会把 `debate_net / 2` 灌进 `adjusted_strength`，照样凭空造出一个方向性结论。修法是说清「弃权对委员会意味着什么」：**弃权是「证据不支持任何方向」这一主张，推翻它等于用一个 `evidence_ids` 为空的帧铸造方向性结论**，而这正是 `validate_conclusion` 唯一明令拒绝的事。故输入弃权则 `strength` 钉死为 `0.0`、`direction` 保持 `abstain`；辩论本身不丢，`bull_case`/`bear_case`/`ablation` 照常返回，`strength_delta == 0.0` 配一个非空 `bull_case` 是一种读数而不是缺口。反向同样受测：`abstain` 只在输入弃权时可达，方向性信号绝不漂移成弃权（否则因无 `abstention_reason` 而镜像崩溃）。集成测试走 `TestClient` 与 `OpenAlphaSDK` 两面，写在修复前并确认为红（REST 侧 `assert 500 == 200`）。**连带**：`product/governance.py` 的合成单标记探针就是为绕开本缺陷而存在的，本次一并删除 | 单元：弃权信号进出委员会仍是弃权；集成：该端点对弃权信号不返回 5xx | D15 |
| `V2-P4-030` | ~~**把 `risk_flags` 的词表收敛为单一定义源的封闭枚举**（与 `V2-P4-001` 对 `mode` 做过的事同构）~~ **已完成** | 技 | 006 | 已交付 `domain/risk_flag.py::RiskFlag`：**十个成员，每个自带 severity**，由 `tests/unit/domain/test_risk_flag.py::test_no_other_module_declares_the_risk_flag_set` 按 `run_mode` 的形状做 AST 审计（阈值取 2 而非 3，因为 `_blocking_flags` 本就只有两个成员，取 3 会把最小的那份旧声明放回来）。**severity 挂在成员上而不是旁边的 `Mapping`**：并列映射可以「缺一条」，那正是三份旧列表失败的方式；写进成员自身的值里，`enum` 无法用单元素元组建成员，「有标记没 severity」这个状态就不可构造。两个闸门的集合**改为派生**，`agents/committee.py` 方法体内那个字面量集合提升为模块常量并同样派生。`SignalFrame.risk_flags` 收敛为 `tuple[RiskFlag, ...]` —— 拼错的标记现在是 `422`，`loc` 精确到 `['body','signal','risk_flags',0]` 并列出词表，而不是静默降级后**在榜单上升**。`StrEnum` 故 `signal_id` 一个都没动，对照 `146698c` 逐字节实测四组摘要相同。**闭合词表时实测到一处既有漂移（不在本 issue 描述内）**：`evidence/builder.py` 写的是 `f"redistribution_{...}"`，而 `EvidenceSnapshot.redistribution` 是 `Literal["allowed","restricted","unknown"]`，三个在产 provider 全部声明 `restricted` —— 即**本构建实际写出的唯一 redistribution 标记 `redistribution_restricted` 无任何闸门认得**，而被 `RiskGate` 点名的 `redistribution_unknown` 生产中根本产生不出来。两者均已声明为 `reduced`，f-string 换成按 `Literal` 键入的 `dict`，第四个 redistribution 取值会在 `mypy` 处红。**四个 fail-open 洞一并闭合**：`regulatory` / `data-quality` / `suspension` / `committee-disagreement` 此前在 `RiskGate` 全部返回 `pass`；改动无一处削弱，每个变化的答案都朝拒绝方向移动 | 单元：拼错的标记被具名拒绝而非静默降级；审计：无第二处模块声明该词表 | S48, D15 |
| `V2-P4-031` | ~~**`MAXIMUM_SHORTLIST` 跟上批量上限**~~ **已修复** | 技 | 019 | `backtest/cross_section.py` 的常量由 1_000 改为 10_000，配套断言由 `<=` 改回 **`==`**（先红后绿：红文案即 `MAXIMUM_SHORTLIST is 1000 and MAX_BATCH_ITEMS is 10000`）。`<=` 对 1..10,000 的每个数都成立，所以它恰好说不出这一行的名字 —— 上限不再是「照抄批次」而只是「没超过批次」，而挡住全市场的墙已经搬到这里且无人记名。**为什么是 10,000 而不是某个更小的实测数**：本行提醒上界要有自己的依据、不能照抄十倍，确实如此，而依据指向批次上限本身 —— 本模块对「榜该多长」没有任何看法，真正约束它的不是常量而是 `cut_exceeds_the_cross_section`（逐次对可交易数判定的 coverage 码，不是拒绝；`MINIMUM_SHORTLIST` 的下界注释记的是同一条推理）。更小的数就是本模块凭空发明一个没有测量支撑的限制。**与 `V2-P4-043` 的 8MB 墙实测对照，结论是它挡的是另一条路由**：那一行量的是 `POST /api/v1/screen`，请求体内联**已研究完的结果**；而 `POST /api/v1/shortlists/run` 指名一个已落盘的横截面、不带任何名字 —— 实测 `ShortlistRunApiRequest` 在 `shortlist_size=1` 时 450 字节，1,000 与 5,545 时 453，**10,000 时 454**，整个区间只长了四个字节。**长的是答案不是请求**：八名语料上实测每条榜项 53 字节、每个 admitted 候选 191 字节，外推到新上限约 0.5 MB 榜 + 至多 1.9 MB 候选，与 `V2-P4-040` 报的单个批次同量级、比它那 36.9 MB 低一个数量级 | 单元：常量与 `MAX_BATCH_ITEMS` 的关系由断言而非注释维持 | S43, S95 |
| `V2-P4-032` | ~~**面板 → `ComponentCrossSection` 适配器**~~ **已完成** | 结 | P3, 004 | **P4 产品验收（2026-08-19）实测，且此前从未被计划过 —— roadmap 全文零次提及**：`grep -rn "ComponentCrossSection(" src` **无输出**，只有 `tests/` 里 9 处构造。`CrossSectionScreen.select` 要求 `components: Sequence[ComponentCrossSection]`，`openalpha factor build` 把 processed/neutralized 档写进面板、`load_factor_observations` 能读回来，**但两者之间没有任何东西**。故 `V2-P4-033` 就算把出货面补齐也拿不到输入 —— 这条比出货面更硬。**为什么必须由本仓提供而不是让用户自己写**：这一层要在一个 `as_of` 上把多个因子档位对齐成同一个截面，是全链路**最容易引入 look-ahead 的一段**，而避免这件事正是本产品存在的理由；把它留给用户，等于把唯一真正危险的接缝外包出去。输入侧必须走 `read_visible_at`/`load_factor_observations` 的既有可见性路径，不得新开读法 | 集成：晚于 `as_of` 的因子值不得进入。**已完成（2026-08-19）**：交付 `src/openalpha_cn/shortlist_view.py`，第三个顶层 `*_view.py`。**层的选择**：`backtest/` 不可能（`backtest-no-numeric-stack-or-panel-plane` 按全传递可达禁 `openalpha_cn.panel*`，而读分区的适配器按定义就够到 store）；`factor_view.py` 是更接近的候选但被否决 —— 它回答的是一个**闭区间**上的问题（`_PanelInputs` 存在的理由就是跨窗口缓存会话级读、请求带 `--start`/`--end`、产出是封存实验），而入围名单是**一个 `as_of`**、无标签、无前向收益、无存储制品，合并会得到一份两半不相交的请求契约与一套含义双重的错误分类。**look-ahead 测试的构造**：同一 raw 分区里两次 build（09:00Z 全正、13:00Z 全负），请求卡在中间，断言两次值集**不相交**且入围名单不同 —— 只断言缺席则「什么都不返回的适配器」也能过，只断言在场则「无视 `as_of` 的」也能过。**19 个变异全红，其中四个第一遍存活的没有一个靠加断言解决**：一条与 `CrossSectionScreen._read_components` 重复的注册表过滤被删除；定价读取移到截面自身时刻并补「两周前的截面」用例（原 fixture 把每个可答 `as_of` 都塌到同一交易日）；一处静默丢弃改为具名字段 `evidence_not_shortlisted`。**新增第 25 个注册表 `KNOWN_SHORTLIST_VIEW_LIMITATIONS`（4 条），总数 24/225 → 25/229**。合并时施加了它报上的受阻依赖：`backtest-no-numeric-stack-or-panel-plane` 加入 `openalpha_cn.shortlist_view`，并**实测该条目会响**（探针 import 被具名点出，删除后恢复 8 kept / 0 broken） | S95, D3 |
| `V2-P4-033` | ~~**排序面：CLI + REST + SDK 入口**~~ **已完成** | 产 | 005, 023, 032 | **P4 产品验收（2026-08-19）的第一条 Critical，且是 P3 验收那句根因的原样复发**：`004`/`005`/`023` 三条主线交付物**没有一条**能被 CLI、REST 或 SDK 触达 —— CLI 十个命令里没有，34 条路由里 `shortlist`/`rank`/`cross`/`funnel`/`candidate` **全部为 NONE**，SDK 32 个公开方法同样为 NONE，`backtest/__init__.py` 的 `__all__` 仍是 P1–P3 的十六个名字。验收人手写六个内部 import 的脚本后**整条链跑通了**（5,545 → scored 5,540 → shortlist 50 → ranking 12 → 闸门以 `researched_ratio_below_floor` 阻塞，且拒绝信息精确到「12 of the 50 ... 0.2400 against a floor of 0.5000」并指向 `admitted_or_none`）—— **库是好的，只有写这段脚本的人看得到它**。**测量而非断言**：`tests/unit/backtest/test_{cross_section,candidate_ranking,shortlist_gate}.py` + `tests/integration/test_shortlist_gate_refusal.py` + `test_governed_screening.py` 共 **159 passed**，而 `grep -ln "CliRunner\|TestClient\|OpenAlphaSDK\|openalpha_cn.cli\|api.app"` 在这五个文件上**零命中**。**连带**：`V2-P4-023` 的「blocked ≠ empty」在库内成立，但用户唯一能碰的 `POST /api/v1/screen` 空结果只有一种形态（`{"items":[],"excluded":[],"reviewed":0}`），合法空答案与本该被拒绝的名单在那里长得一模一样 | 集成：**测试必须从 CliRunner / TestClient / SDK 出发**。**已完成（2026-08-19）**：修复前三条验收测试各自以预期理由失败 —— CLI `exit 2 / No such command shortlist`、REST **404**、SDK `AttributeError: no attribute run_shortlist`。**「被阻塞」与「空」在用户面上是两个答案**：同一 store、同一命令行、**只差一个开关**，断言两次运行的 `measurement` 体**完全相同**而结论不同 —— `--min-researched-ratio 0.5` 且无人被研究得到 **exit 1 / HTTP 409**、`is_blocked: true`、`admitted: null`、`blocks` 带 measured 0.0 与 required 0.5；`0.0` 得到 **exit 0 / HTTP 200**、`is_blocked: false`、`admitted: []`。`null` 与 `[]` 是两个答案；`refused` 是两张通道表（`SHORTLIST_EXIT`/`SHORTLIST_HTTP_STATUS`）里的一行且**不是任何东西的 `reason`** —— 拒绝的闸门是答复了不是失败了。38 条新测试跑 4.9 秒（模块级 fixture、8 只合成面板），全套件 27:11 无可测增长。**如实声明的三处边界**：(a) 生成 fixture 上 processed 档全部 `insufficient_cross_section`（出厂变换声明 `min_cross_section=100`，面板只有 8 只），故面测试走 raw 档，processed 的裁剪块规则由单测直接覆盖；(b) **neutralized 档在面上具名拒绝** —— `rank_candidates` 在该档要求 `IndustryMarketCapCrossSection`，其加载器经 `read_if_ready` 读 `index_member_all`，即 `V2-P4-027`/`028` 的边界而非本条的；(c) **本面不跑证据平面**，且这是刻意的：全仓不存储 `SignalFrame`，而一个把每个入围名都研究一遍的面会让 `researched_ratio` 恒等于 1.0，使 `V2-P4-023` 存在的那条闸门从任何出货面都不可达；答案由请求侧提供（`evidence` / `--evidence <file>`） | S83, S84, S48 |
| `V2-P4-034` | ~~**`V2-P4-027` 的普查等式是「和」，一对相消的错误能让它放行 look-ahead**~~ **已修复** | 技 | 027 | **P4 技术验收（2026-08-19）实测，本轮刚合并的代码**。`panel_ingest.py:3511-3514` 比的是两个整年总数：`happened = sum(entry.row_count for entry in coverage.dates if entry.event_date <= census_day)`，随后 `if len(outcome.rows) != happened`。两个方向相反的错误**精确抵消**。探针（经公开的 `write_industry_memberships` 写入，2024 分区两行，`as_of`=2024-06-15）：`600000.SH` 事件 02-01 可得 09-01（普查计入、谓词扣住），`600001.SH` 事件 09-01 可得 03-01（普查不计、谓词放行）；`happened=1`、`len(rows)=1`，**等式成立、读被放行**，返回的截面里含一个**成员事件晚于 `as_of` 三个月**的归属，同时**漏掉一个当时已可知的证券**。对照组：只留单侧异常时确实被具名拒绝，证明检查是活的、只在相消时失效。**修法是逐事件日对账**（拿可见行自己的 `event_date` 与 `coverage.dates` 逐条比），而这需要先改列 —— `INDUSTRY_MEMBERSHIP_PANEL_COLUMNS` 不含 `event_time`，**这个读法拿它请求的列做不了对账**。**可达性如实说明**：需要一个 `available_time` 偏离 `max(event_time, 分类法地板)` 的分区，Tushare provider 不会产生；但那正是该检查为自己声明的威胁模型 —— 它自己的 docstring 写着「the property lives in a provider one package away and nothing in the store enforces it」 | 集成：相消的一对必须被具名拒绝。**已修复（2026-08-19）**：改为**逐事件日对账** —— 可见行按各自的 `event_time`（在普查的 `date_timezone` 里解析）计数，与 `coverage.dates` 逐条相比。**列元组未加宽**：在那一处读点前置 `EVENT_TIME_COLUMN`、返回前剥掉，即 `load_factor_observations`/`load_neutralized_factor_observations` 已在用的写法；加宽会动到 `industry_histories_from_panel_rows`（按行宽校验后按位解包六个值）、`load_industry_histories`、`industry_membership_requirement.required_fields` 与一处测试，**结果一处都没动**，被否决的方案记在该常量自己的 docstring 上。**拒绝分成两条而非一条**：look-ahead 在先（其可得性早于自身事件，是 `_taxonomy_backfill_timeline` 造不出的形状），短缺在后，并保留 `V2-P4-027` 原有措辞，故其既有测试**未被修改也未被削弱**。**变异是精确的**：把逻辑退回求和，**只有**相消对那条测试变红。**两处顺带发现**：(a) 真实语料**表达不了**这个缺陷 —— 其唯一后分类法分区装的是同一只证券一次重分类的两半，对调可得性确实构成相消对，但 `build_security_industry_history` 会在普查判定可观察**之前**就以「两个未关闭归属」拒绝，故在该语料上被测检查与重叠规则**分不开**，这正是本缺陷所属的那类 fixture 陷阱，因此专造 fixture 并把理由记在原处；(b) 原 look-ahead 消息**是反的** —— 读作「holds 3 row(s) … and 4 of them were visible」，等于说分区少了负一行，现已各自具名 | S27, S28 |
| `V2-P4-035` | ~~**`ranking-creates-no-portfolio-order` 的名字与注释都比它强制的更宽**~~ **已修复** | 技 | 005, 023 | **P4 技术验收实测**。契约名是「reach no module that **declares or simulates an order**」，`pyproject.toml` 注释断言「那三个是全仓声明或模拟订单意图的**全部**」。**该句为假**：`backtest/execution.py:247` 的 `ExecutionRequest` docstring 就是 A simplified cash-equity order intent.，`AShareExecutionPolicy.execute:274` 是 Simulate a close-price fill…，两者都不在 `forbidden_modules` 里，而两个契约 source **今天已经够到**（`shortlist_gate → candidate_ranking → cross_section:227 → execution`）。探针在 `candidate_ranking.py` 里真的成交：`status=filled qty=100 filled_price=10.20 total_cost=5.01`，而 `lint-imports` 报 **8 kept / 0 broken**、`tests/unit/backtest` 加层测 **500 passed**。**不能靠把 `execution` 加进禁令来修** —— `cross_section.py` 正当地需要成交政策判可交易性；诚实的修法是把名字与注释收窄到它真正强制的东西（不得到达 `PortfolioOrder`）。附带：同一探针放进 `shortlist_gate.py` **被抓到了**，但抓它的是那条三名 import 白名单测试，**不是**订单契约；`candidate_ranking.py` 无此白名单，完全无守 | 单元：契约名与注释所述范围必须与 `forbidden_modules` 一致。**已修复（2026-08-19）**：**动的是声明不是强制** —— `source_modules` 与 `forbidden_modules` 逐字节未变，只改 `name` 与注释。**`id` 本来就是对的**：`ranking-creates-no-portfolio-order` 说的是 *portfolio* order，正是它强制的、也正是 D16 说的；漂移的是 `name`（「an order」），它从自己的 `id` 上飘走了，故 `id` 不动（两处测试按名读它）。**brief 的约束被实测而非采信**：把 `execution` 加进 `forbidden_modules` 得到 **7 kept / 1 broken**，断在 `cross_section.py:227` 的可交易性过滤上 —— 那是 `V2-P4-004` 的硬过滤，所以必须移动的是声明。**根治形状是把「那 N 个是全部」变成可执行的算术**：新增 `test_every_order_intent_is_forbidden_to_the_ranking_or_disclosed_as_reachable` 按 AST 找出 `src/` 下每个 docstring 自称 order intent 的类，断言该集合**等于**一张声明表，且每一个要么被禁且不可达、要么在注释里具名且可达性经 `grimp` 实测（今日恰好两个：`PortfolioOrder` 被禁，`ExecutionRequest` 被披露）；另两条钉住「名字里的 order 必须是 portfolio order 的一部分」与 `candidate_ranking.py` 的 import 清单（它是唯一没有 import 面钉桩的契约 source）。**如实披露的残留缺口**：没有任何契约阻止 source 声明或成交一个**单标的** `ExecutionRequest`，写在契约注释、模块 docstring 与 `KNOWN_RANKING_LIMITATIONS` 三处。4/4 变异全红 | D16 |
| `V2-P4-036` | ~~**`SHIPPED_RISK_GATES` 是装饰性的：整个清空也不改变任何严重度**~~ **已修复** | 技 | 006 | **修法是删掉它，不是把它接上。**先复验了两条探针都仍然成立：加一个什么都阻塞的第三闸门并 `cache_clear()`，`flag_severity('bogus-flag')` 仍是 `unrecognised`；把注册表清空，`flag_severity('future_data')` 仍是 `blocked`。但「按文档新增第三个闸门」这条路本身就不该存在 —— `V2-P4-030` 把词表封闭后，**闸门无权决定一个标记值多少，只决定对一个已声明其价值的标记做什么**。故 `SHIPPED_RISK_GATES`、合成单标记探针 `_probe`/`_verdicts`、以及 `_rung` 全部删除，`flag_severity` 变成对十成员枚举的一次字典查找。**`lru_cache(maxsize=512)` 也随之删除**：它当初有界（而非 `functools.cache`）是因为键来自请求体、无界备忘是调用方决定大小的泄漏；现在既无推导可备忘，键空间也从「任意字符串」变成十个成员。`assess` 改为直接问真实闸门要 `gate_decision`/`committee_decision`（`V2-P4-029` 修好委员会后才可能），严重度则取标记自身声明的最差一级。**代价已记入注册表**（`a_severity_is_declared_on_the_flag_and_is_not_a_measurement_of_either_gate`）：severity 现在是关于词表的声明，不再是对本构建行为的测量，只有 `test_both_gates_answer_about_every_declared_flag_and_agree_with_its_severity` 会发现闸门不再遵守自己那一档 | 单元：清空注册表必须让每个严重度变 `clear` 或抛错 | S48 |
| `V2-P4-037` | ~~**没有任何审计阻止第二套内容寻址规范化**~~ **已修复** | 技 | — | **P4 技术验收实测**。`domain/_identity.py::stable_model_id` 声称每一个身份都经过它，且第二套规范化会「invisible until two IDs disagreed」，但**没有审计强制它**（其散文还说 Fourteen call sites，实为 **16** —— 又一处被引用而非可执行的数字）。探针把 `ShortlistGateManifest.gate_manifest_id` 改成自造的 `json.dumps` 加 `sha256[:24]`，同一声明得到**不同地址**（`sgt_5ef4129a…` 对 `sgt_084308ea…`），而 ruff 通过、mypy 129 文件通过、`lint-imports` 8 kept/0 broken、单元 **2338 passed**。修法是本仓已用过两次的形状：AST 审计 `src/` 下每处 `sha256(...).hexdigest()[:24]` 必须位于 `domain/_identity.py` 或真内容摘要白名单（`set_digest`/`rkc_`/`chr_`/`ev_`）。**已交付，且本单的两处描述都被实测推翻。**①**本单探针今日并非全绿**：把 `gate_manifest_id` 换成自造哈希会让 `test_a_quantitative_model_reference_must_be_something_the_one_hash_function_produced` 变红，但那是**巧合**而非守卫——census 数的是 `stable_model_id`/`cross_section_digest` 的**调用点**（27 个 / 24 前缀），*替换*一处使其由 27 掉到 26，红在算术上、只字未提规范化；改用**新增**一处（同一 model 上再挂一个 `sgs_<24hex>` 的 computed field，不动任何既有调用）后 census 纹丝不动，ruff、mypy 140 文件、`lint-imports` 8 kept、`tests/unit` **2813 passed** 全绿——这才是真红。②**白名单不是四条**：按 AST 读源码树，`src/` 下把摘要截到地址宽度的共 **8 处**，本单漏掉了 `cross_section_digest`、`shortlist_view.stable_answer_digest`（`sla_`）与 `ParquetEvidenceStore.append`（后者产出的是文件名 `part-<24hex>`，`CONTENT_ADDRESS_PATTERN` 并不接受）。③**`_identity.py` 自己那句「Three builders」也是假的**：`chr_`/`rkc_`/`sla_`/`ev_` 同样匹配该 pattern，实为**七**个 builder 产出该形状、第八个产出文件名，该句已改写并指向活表。审计按 `<模块>::<类.函数>` 而非按文件建表（`domain/factor.py` 自己就有两处，按文件建表会放过第三处），两个方向都是等式；并补第二半——**六处用 `json.dumps` 的规范化必须与 `stable_model_id` 同拼写**（`ensure_ascii=False`/`separators=(",", ":")`/`allow_nan=False`，`sort_keys` 只许缺省或 `True`），因为四处哈希的是 list、`sort_keys` 在其上不改一个字节，为满足审计去改 `backtest/candidate_ranking.py` 是本末倒置。**已知边界写在测里**：审计读的是切片处字面量 `24`，写成 `hexdigest()[:_WIDTH]` 躲得过；八处今日全是字面量，而放宽到「`src/` 下每个 `sha256` 调用」会把七处 64 位纯校验和（`payload_digest`/`config_digest`/`content_hash` 等）混进来，那是另一个问题。**变异 33 个、33 杀、0 活，基线在跑变异前实测绿（61 passed）** | 单元：模块外的第二处规范化必须变红 | S30, D11 |
| `V2-P4-038` | ~~**注册表总数是下界而非等式，且 code 跨注册表不唯一**~~ **已修复** | 测 | — | **P4 技术验收实测**。`test_the_registries_together_carry_the_entry_count_the_report_folds` 断言 `sum(...) >= 225`（今日恰为 225，零余量）。探针：删掉 `KNOWN_EXECUTION_LIMITATIONS` 的 `an_absent_band_is_derived_rather_than_refused`，同时加入一条 code 为 `the_cut_is_broken_by_subject_code_when_two_scores_tie` 的条目 —— 那是 `KNOWN_CROSS_SECTION_LIMITATIONS` 的 code，因而**已经**是可执行测试代码里的字面量、满足绑定。结果 **2338 passed**：一条真实限制被静默删除，一个外来重复 code 顶了缺。修法是把 `>=` 改成 `==` 并补一条**跨注册表 code 唯一性**断言（每注册表内唯一已断言，全局未断言）。**已交付。两处描述被实测修正。**①**本单探针在 `tests/unit` 上绿、在 `tests/integration` 上红**：`tests/integration/panel/test_execution_label_parity.py::test_every_declared_limitation_is_exercised_by_a_test_named_after_it` 拿三条 code 做**集合等式**，删一条即红（实测 1 failed / 12 passed）。按 AST 普查，32 张表里 **30 张**都有一个与自己 code 集合逐项相等的字面量集合，只有 `KNOWN_INDEX_MEMBERSHIP_LIMITATIONS` 与派生的 `KNOWN_PANEL_LIMITATIONS` 没有。故改用**在那张没有等式钉的表上加一条外来 code、不删任何东西**作红：`the_cut_is_broken_by_subject_code_when_two_scores_tie` 进 `KNOWN_INDEX_MEMBERSHIP_LIMITATIONS`，`tests/unit` **2816 passed**、七个相关 integration/contract 模块 **233 passed**，台账由 301 涨到 303 而下界 301 照过。②**全局唯一性今日就是假的，且理由正当**：实测三条 code 各自跨 2~4 张表——`silent_truncation_at_the_response_cap`（4）、`no_revision_history`（2）、`a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule`（3）。故断言不是「没有 code 出现两次」，而是 `CODES_THAT_RECUR_ACROSS_REGISTRIES`：**code → 它出现在哪几张表**的精确映射，值是集合而非计数，故一条复现「换了张表」与「新出现一条」一样红。等式则做成 `REGISTRY_ENTRY_COUNTS`——**每张表一行**的精确条数（31 行，派生表故意不写，它已被 `== folded + plane_wide + 1` 钉住，写 69 就是从第一个数派生出的第二个手写数）。**按表而非按总数，正是为了扛住兄弟合并**：单一标量只看净值，A 表少一条被 B 表多两条掩盖即绿；按表则指名道姓地红。且本模块 docstring 自己记着 `V2-P4-006` 与 `V2-P4-023` 并行各写 `>= 218`、git 因两处改动逐字相同而静默合并、事后算术为假——两人改两张不同表就是两行不同改动、合并正确，改同一张表则冲突，而那正是应该让人来判的。**代价照实写在表上**：凡加条目的合并都会红；表内「一条换一条」它看不见，那要靠各注册表自己模块里的集合等式（30/32 有）。**变异 33 个、33 杀、0 活，基线在跑变异前实测绿（61 passed）** | 单元：删一条加一条外来 code 必须变红 | — |
| `V2-P4-039` | ~~**离线保证只覆盖 TCP `connect`，非 e2e 测试可以发出 UDP 数据报**~~ **已修复** | 测 | — | **P4 技术验收实测**。`tests/conftest.py` 声明了三条边界（仅本进程、不拦名字解析、不动 `AF_UNIX` 及其它族），`AF_INET` 上的 **UDP 不在其中且无守**。探针（仅打环回丢弃口，未离开本机）：TCP connect 触发 `OfflineSuiteViolation`；UDP `sendto` 返回 5 字节、无拒绝；实测被包裹的方法为 connect/connect_ex 为真，sendto/sendmsg/send/sendall 全为假。守卫**不看地址**，故可路由目的地不会比环回更受限。修法二选一：包裹 `AF_INET`/`AF_INET6` 上的 `sendto`/`sendmsg`，或把声明范围收窄为「出站 TCP」。今日无出货代码走 UDP，故 Minor。**已交付，选包裹而非收窄**：收窄只是把句子改真、把保证改小，而本项目记过的十二条 Critical 全是往那个方向去的。探针原样复现：wrapped 集合 `{connect: True, connect_ex: True, sendto: False, sendmsg: False, send: False, sendall: False}`，TCP connect 抛 `OfflineSuiteViolation`、UDP `sendto` 与 `sendmsg` 各返回 5 字节无拒绝。**四个方法就是全部出站面，这是论证而非清单**：`send`/`sendall`/`socket.sendfile` 都要求已连接的 socket，而在受守族上唯一的连接途径 `connect`/`connect_ex` 会抛，故包裹它们是任何输入都到不了的死代码；`sendto`/`sendmsg` 是仅有的两个不连接就能发的。两半都有断言（这四个被影，`send`/`sendall` 故意不被影）。**并顺手补上一个先前无法被观测的 `finally`**：install/restore 原本长在 autouse fixture 里，因而没有任何时刻能看到未被守的 `socket.socket`，「删影子是唯一能让类恢复原样的还原法」这句话下面什么都没有——实测把 `delattr` 换成 `pass`，本单三个模块 59 条全绿。现改为 `offline_guard.refusing_outbound_traffic(target)` 上下文管理器（`tests/import_linter_containment.py::raw_lint_imports_disables` 同一问题同一答法），测试拿一个继承同一 C 基类的临时子类跑完整往返，两个方向都断言。**变异 33 个、33 杀、0 活，基线在跑变异前实测绿（61 passed）**；第一轮唯一幸存者是「拒绝信息里的目的地永远解析成 `None`」，已补断言而非解释掉 | 单元：非 e2e 的 UDP 发送必须被拒绝 | — |
| `V2-P4-040` | ~~**`GET /api/v1/research/batches` 无分页，响应体膨胀到超过服务自己的入站上限**~~ **已修复** | 产 | 019 | **P4 产品验收实测**：20 个全市场批次（约一个交易月）得到 `batches: 20, items: 115,355, bytes: 36,857,096`（36.9 MB），2.35 秒；**3 个批次时已 8.7 MB，超过本服务自己的 8 MB 入站上限**。`batch_list()` 就是 `return batch_store.list()`，无分页、无摘要模式、每个批次内联全部 item。`V2-P4-019` 把批次上限抬了十倍，这个接口没跟上。**修法**：路由改答 `BatchTaskPage` —— `batches` 为 `BatchTaskSummary` 列表（`batch_id`/状态/两个时钟/`cancellation_requested`/`item_count`/五态普查 `items_by_status`），加 `limit`（默认 50，上限 500）与 `offset`，`total` 报整架而非本窗。计数走 `storage/batch.py::list_summaries` 的 `GROUP BY json_extract(payload,'$.status')`，在 SQLite 内完成，不再把 115,355 条 item 过一遍 pydantic；item 原样留在 `GET /batches/{id}`。**这是响应形状的破坏性变更**（原为 `BatchResearchTask` 裸数组），但仓内零消费方（无测试、无 SDK 方法、无 `web/` 页面 —— 这也正是缺陷得以出厂的原因），且未动任何**存储**契约，故不涉 AGENTS.md 规则 3 的迁移；CHANGELOG 已记。**本地实测**：3 个批次 × 5,545 item 修前 **17,693,518 字节（17.7 MB）/1.43 秒**（本 fixture 的 item 带证据比验收的更重）；修后同样 3 批次在 item 数放大 40 倍（150 → 6,000）时响应体不随之增长，且普查数仍与真实 item 总数相符 —— `test_the_listing_size_does_not_follow_the_item_count` 是能把「摘要」和「丢数据」分开的那条断言 | 集成：列表返回摘要（id/状态/计数），item 走 `GET /batches/{id}` | S43 |
| `V2-P4-041` | ~~**`POST /api/v1/screen` 的 422 把三种具体原因压成一句话**~~ **已修复** | 产 | 006 | **P4 产品验收实测**：5,545 条结果里改坏一条的 confidence，得到 422 与一句 Research result failed integrity validation.。`_parse_research_result` 内部明明分三种情况抛了三条不同的话（`signal_id` / `decision_id` / `run_manifest_id` 各自不匹配其内容），`api/app.py:810` 的 `except` 全压成一句 —— 用户拿着 5,545 条结果，既不知是哪一条，也不知是三个 ID 里的哪一个。**同一服务内有范本**：面板闸门的 409 会给出 `reason`、具体缺失项与可执行的补救命令，`openalpha data-check` 同级。差距是接口内部的，不是能力问题。**修法**：新增 `ResearchIntegrityError`（`ValueError` 子类，故三处调用点原有的 `except` 仍捕获），`_parse_research_result` 三处分别带上 `reason`/`field`/`claimed`/`derived`/`subject` 抛出；`_research_refusal` 按面板闸门 409 的 `{"reason", "message"}` 形状组装 422，另带 `index`、`subject`、`field`、以及 `claimed` 与 `derived` **两个值** —— 记录被改和 ID 被改需要不同的修法，只有两值并列才分得开。`reason` 取 `signal_id_mismatch`/`decision_id_mismatch`/`run_manifest_id_mismatch`，第四个 `malformed_research_result` 专给「根本不是研究结果」。`/screen` 改为逐条解析（生成式会丢掉下标），`/reports` 与 `/backtests/validate` 共用同一修法（`index` 为 `null`）。**fixture 的坑，实测撞到过**：最初用 `risk_decision="warn"` 制造 decision_id 不符，而 `"warn"` 不在 `Literal["pass","reduce","block"]` 内，pydantic 在比对地址之前就拒了 —— 那条测试会以「校验失败」的名义假装证明了 decision_id。改用 `"reduce"` 后三种故障各自独立成立 | 集成：422 必须点名是哪条记录的哪个 ID | S48 |
| `V2-P4-042` | ~~**`MAX_BATCH_WORKERS` 32 → 8 是一次未进任何用户文档的破坏性变更**~~ **已修复** | 产 | 019 | **P4 产品验收实测**：`max_concurrency` 传 16 得到 422 与 Input should be less than or equal to 8。边界本身说清楚了，但 `grep -rn max_concurrency docs README.md README.en.md web CHANGELOG.md` **零命中** —— 昨天还能用的请求今天 422，用户在任何他会去看的地方都查不到原因。`batch_contracts.py:78` 的 docstring 把下调理由写得极好（实测 1/2/4/8/16/32 的吞吐平台期），但那是源码注释不是用户文档；CHANGELOG 的 Unreleased 段记了 `V2-P4-002` 与 factor build，**唯独漏了这条唯一会让既有调用方失败的改动**。**修法**：`docs/api/http.md` 新增「批量研究」一节，写清 `max_concurrency` 上限 8、原为 32、以及下调的**实测依据**（1/2/4/8/16/32 的吞吐表，真实引擎在 2 就到平台期），并说明 8 是持久化方式的性质而非可调回的节流阀；CHANGELOG 的 `### Fixed` 补上这条 —— 它是 `V2-P4-019` 里唯一会让既有调用方失败的改动。**测试不写死数字**：文档断言全部读 `MAX_BATCH_WORKERS`/`MAX_BATCH_ITEMS`/`config.max_request_bytes` 的活值，并与 TestClient 打出的真实 422 对齐 —— 只 grep 字面量 `"8"` 的测试正好会在有人改了上限却漏改散文时继续绿，而那恰恰就是本行说的缺陷本身 | 文档：CHANGELOG 与 HTTP API 文档记录该收窄及其理由 | S43 |
| `V2-P4-043` | ~~**批量上限与筛选请求体上限互相矛盾**~~ **已修复** | 产 | 019, 033 | **P4 产品验收实测**：5,545 只全市场 screen 请求体 7.81 MB 得到 200；6,000 只 8.43 MB 得到 413 Request body exceeds configured limit.。`V2-P4-019` 明确为「市场是移动的数字」把批次抬到 10,000，而唯一的市场到名单路由在约 5,700 只就撞上 8 MB 默认体积上限 —— A 股再上市几百家即触发。413 文案未点名 `OPENALPHA_MAX_REQUEST_BYTES`（该变量在部署与 HTTP API 文档里有记载，故 Minor）。**修法**：`config.max_request_bytes` 默认由 8 MiB 抬到 **33554432（32 MiB）**；413 改带 `{"reason": "request_too_large", "message", "declared_bytes", "limit_bytes"}` 并点名 `OPENALPHA_MAX_REQUEST_BYTES`；`ScreeningApiRequest.research` 补上与批量同一个 `max_length=MAX_BATCH_ITEMS`，使超出一只时是点名数字的 422 而非只谈字节的 413；`.env.example` 同步。**本行的实测比原文更糟，是这次定档的依据**：每请求带一条证据快照时，`MAX_BATCH_ITEMS`（10,000）的批量请求体是 **9,840,054 字节**，在 8 MiB 下被 413 —— 即 `V2-P4-019` 明确为「市场是移动的数字」抬上去的那个上限，**经由唯一能表达它的接口根本够不到**；而 `test_batch_whole_market_scale.py` 只在进程内构造该规模的任务，从不 POST，所以没有测试看得见这件事。10,000 只的 screen 请求体 **14,770,051 字节**，5,545 只 **8,190,016 字节**（距 8 MiB 仅剩 198,592 字节，约 134 家新上市）。32 MiB 是「两个已声明上限各留一倍余量」，而非贴着实测值裁 —— 上述两数都只带**一条**证据，真实调用方更重，贴着裁等于把同一个缺陷延后。上限仍在，且仍按 `Content-Length` 在读体之前拒绝 | 集成：全市场规模的 screen 请求必须可表达，或 413 点名该环境变量 | S43 |
| `V2-P4-044` | ~~**processed 档把成因告诉错了用户**（声明的边界从未到达用户面）~~ **已修复** | 产 | 032, 033 | **P4 复验收（2026-08-19）实测**：真实 processed 档（`compute_factor` → `apply_factor_transform(cross_section_standard/v1)` → `write_processed_factor_panels`）在出厂 8 只面板上经 `TestClient` 提问，得到 409 且唯一的 block 是 `researched_ratio_not_measurable` —— 一条关于**证据平面**的阻塞，其隐含补救（去研究这些名字）**不可执行，因为一个名字都没有**。存储行全部带 `insufficient_cross_section`，`ScoreCensus.excluded_by_coverage` 记着 `('not_valued', 8)`，而 `shortlist_view` **两者都不渲染**，还把 `row_count: 8` 与 `scored_count: 0` 并排输出。答案里 `insufficient_cross_section` 与 `min_cross_section` **都搜不到**。真正的补救（筛 ≥100 只，或换一个 `min_cross_section` 更低的变换）无处可寻。同一 block code 带同样的 `measured: null / required: 0.0` 至少服务三种互不相关的成因（`no_scored_candidate`/`cut_exceeds_the_cross_section`/`no_tradeable_candidate`）。`shortlist_view.__doc__` 声称被拒名单带着「the sentence that says what to do」—— 它说的是**另一件事**该怎么做。且这是**被声明的**配置，故每个在小面板上试 processed 档的用户必然撞上 | 集成：processed 档的拒绝必须点名 `insufficient_cross_section` 与 `min_cross_section` | S48 |
| `V2-P4-045` | ~~**调用方传入的一个数字产生裸 HTTP 500**~~ **已修复** | 产 | 033 | **P4 复验收实测**：`ShortlistSpec.position_capital: Decimal = Field(gt=0)` **无上界**，而每个同侪数值都有（`shortlist_size` `le=1000`、权重 `le=1000`、比率 `le=1`）。实测 `capital=1e25 → 200`；`1e26 → REST 500 text/plain 'Internal Server Error'，CLI exit=5`；`1e400` 同。抛点是 `backtest/factor_portfolio.py:688` 的 `int(capital // (market.close * SHARE_LOT))` 抛 `decimal.InvalidOperation` —— 它是 `ArithmeticError`，因而穿过 `run_shortlist` 的 `except TwoStageFunnelError` 与三个面上的每一处 `except ShortlistViewError`。`SHORTLIST_HTTP_STATUS` 自己的 docstring 写着 `internal_error`（500）**"Not raised anywhere in this module."** —— 它被一个用户键入的值抬了起来。REST 响应体无 `detail`、无 `reason`、无 `message` | 集成：任何调用方数值都不得产生裸 500 | S48 |
| `V2-P4-046` | ~~**同一个字面输入在 CLI 上发布、在 REST 与 SDK 上被拒**~~ **已修复** | 产 | 033 | **P4 复验收实测**：`code_commit = ""` 时 REST 得到 422 与具名理由（"must be at least 7 characters… Different code may cut a different list from the same panel"），而 **CLI exit=0 并出榜** —— `cli.py` 写的是 `code_commit or None`，故空串在一个面上是「省略、从 git 解析」、在另两个面上是「非法」。**CLI 发布了一份标着调用方从未声明过的 commit 的名单**。`config_digest = ""` 同样。而 `README.md:499` 声称三面等价、`docs/api/http.md:288` 称 REST 是「the HTTP twin」。附带：README 的退出码表没有 exit 2（Click 用法错误，缺 `--component` 时发出）与 exit 5 的行 | 集成：三面对同一字面输入必须给同一判定 | S83, S84 |
| `V2-P4-047` | ~~**`V2-P4-035` 指定用来替代其已披露缺口的守卫是失效的，且其中一种绕过对 import-graph 不可守**~~ **已修复** | 技 | 035 | **P4 复验收实测，两条独立绕过均已复现**。(a) **那条 pin 只看顶格行**：`test_candidate_ranking.py:494` 与 `test_shortlist_gate.py:942` 的判据是 `line.startswith(("import ", "from "))`，故**函数内**的 `from openalpha_cn.backtest.execution import ...` 会产生真实 grimp 新边（`line_number: 1267`）而 pin 完全看不见 —— 探针从两个 source 各成交一单（`RANKING FILLED: filled buy 100 10.20 5.01`、`GATE FILLED: filled sell 200 10.20 6.04`），`Contracts: 8 kept, 0 broken`、`103 passed`。pin 自己的 docstring 说它阻止「this module quietly growing an edge of its own into the order machinery, which is the step the probe actually took」—— 那正是它没拦住的那一步。(b) **根本不需要新边**（已由主线独立复核）：`cross_section` 再导出 `ExecutionRequest`/`MarketBar`/`AShareExecutionPolicy` 三者，而 `cross_section` **本就在 pin 的白名单首位**；把这三个名字加进已有的 `from openalpha_cn.backtest.cross_section import (...)` 块，`line.split()[1]` 逐字节不变、grimp 图上零新边，成交照旧、全绿照旧。**故任何 import-graph 规则都挡不住形式 (b)，修法必须是行为断言而非图断言**。另：`importlib.import_module("openalpha_cn." + "domain." + "portfolio")` 能在 `candidate_ranking.py` 里真的构造出 `PortfolioOrder` 且全绿，故契约注释里那句绝对化的「neither source can construct a `PortfolioOrder`」**如实说应为「cannot statically import」** | 单元：行为断言 —— 两个 source 都不得成交任何订单，无论 import 写在哪里 | D16 |
| `V2-P4-048` | ~~**order intent 普查会静默丢弃同文件碰撞，且 AST 匹配面远窄于其声明**~~ **已修复** | 技 | 035 | **P4 复验收实测**。(a) `_order_intent_declarations`（`test_import_layering.py:1005`）把结果收进 `found: dict[str, str]`，**以模块为键** —— 同一文件里第二个 order intent 会覆盖或被覆盖，取决于它在源文件里的**位置**：同样的类插在 `ExecutionRequest` **之上**则通过、附在**之下**则失败。该表自称「a third declaration cannot arrive unnoticed」，不成立。修法：收成 `set[tuple[module, class]]`。(b) 匹配器要求 `ClassDef` + 字面量首句 docstring + 精确 ASCII 子串 `"order intent"`，且只扫 `src/openalpha_cn/**/*.py`。**看不见**：函数、`.pyi`、包外、`__doc__ = ...`、f-string docstring、中文（`订单意图`）以及每一个英文同义词（buy instruction / trade ticket / sell order）。探针往两个 source 都够得到的模块里加了**七个**这样的意图，审计仍报 `MATCHES THE DECLARED TABLE: True`。故「a third order intent added anywhere under `src/` fails the equality」这句是假的。(c) 披露检查是对一段切到 EOF 的文本做裸子串 grep（`assert module in disclosure`），故整段 `V2-P4-035` 理由可以删除、模块名改写在一个无关 `[tool.*]` 段里而审计仍绿 | 单元：普查按 (模块, 类) 收集；匹配面与其声明一致或收窄声明 | D16 |
| `V2-P4-049` | ~~**`researched_ratio` 是自证的：伪造的证据能清掉 1.0 的门槛**~~ **已修复** | 技 | 023, 033 | **P4 复验收实测**：`run_manifest_id` 只做格式校验，`SignalFrame` 只需哈希到自己的地址，**没有任何东西把两者对到一次已存运行上**。探针用一个杜撰的信号与字面量 `run_000000000000000000000000` 清过了 `1.0` 的门槛：`is_blocked=False`、`admitted` 里带着那个 `run_manifest_id`、`measurement.researched_ratio = 1.0`。已声明的边界只说证据是**传入而非跑出来的**，**没有说它不可验证**；出榜答案把一个解析不到任何东西的 `run_manifest_id` 当作出处记录发布。**且那条不跑证据平面的理由是建立在一个已立项缺陷之上的**：「一个把每个入围名都研究一遍的面会让 `researched_ratio` 恒为 1.0」之所以成立，是因为 `V2-P4-029` 让弃权的运行抛异常而不是留下一个未被研究的名字；`029` 一旦修好，该理由失效，这个选择应重新评估 | 集成：无法对到已存运行的证据不得计入 `researched_ratio` | S48, D16 |
| `V2-P4-050` | ~~**`--neutralization` 在 raw/processed 上被接受并静默丢弃；出榜答案不记录选中它的那些输入**~~ **已修复** | 产 | 033 | **P4 复验收实测**：`_resolve_neutralization` 只在 `tier == "neutralized"` 时被到达，故要求中性化筛选的调用方拿到的是 raw 结果、`200 OK`，且答案里**没有任何东西说明这一点** —— 带与不带该开关的响应体完全相同。相关：渲染出的答案记了 `tier`，却**没记** `transform`、`neutralization`、`exchange`、`years` 与构成；而 `CandidateRankingManifest.scoring_policy` 是一个不带 transform 的 `ShortlistSpec` —— 故在 processed 档上，**选定那些数字的变换不进任何一个已发布的内容地址** | 集成：无效开关必须被拒绝或记录；答案须携带选定数字的全部输入 | S49 |
| `V2-P4-051` | ~~**`422` 携带两套不兼容的响应体结构，而文档教的判据会抛异常**~~ **已修复** | 产 | 033 | **P4 复验收实测**：`docs/api/http.md:342` 教客户端按 `"detail" in body` 分支，而该判据对本模块的 `{"reason","message"}` 字典与 FastAPI 原生校验**列表**同时为真。照文档写的客户端在以下输入上抛 `TypeError: list indices must be integers`：无法解析的 `as_of`、拼错的字段名、非数值 `position_capital`、错误 `Content-Type`、畸形 JSON | 集成：422 的结构单一，或文档给出真正可分辨的判据 | S48 |
| `V2-P4-052` | ~~**两个 factor 命令带着与 `V2-P4-046` 完全相同的缺陷**~~ **已修** | 产 | 046 | `cli.py:3663` 与 `cli.py:4099` 都是 `code_commit: Annotated[str, ...] = ""` 配 `code_commit or None`，即空串在 CLI 上意为「省略、从 git 解析」而在契约层意为「非法」—— 与 `046` 同类，产出的是**标着调用方从未声明过的 commit 的因子制品**。**对照证明这是离群而非惯例**：`openalpha run` 与 `openalpha replay`（`cli.py:666,699`）用的是 `str | None = None`，本来就是对的；`shortlist run` 是离群，这两条也是。确切编辑：把两处默认值改成 `None` 并相应调整类型标注，去掉 `or None`。**未在 wave 3 顺手修**，因为按本仓纪律需先有一条从 `CliRunner` 出发、先红的测试，而那需要摸清这两个命令的产出路径。**行文里的两个行号已过期**（本行写下后 `cli.py` 移位），实际是 `@factor_app.command("run")` 与 `@factor_app.command("build")` 各自的 `--code-commit`；**`046` 的修法形状原样转移，无一处不适用**：`Annotated[str, ...] = ""` → `Annotated[str \| None, ...] = None`，`_resolved_code_commit(code_commit or None)` → `_resolved_code_commit(code_commit)`（两处各两行，`cli.py` 上本行只动这四行）。**先红实测**：`factor run --code-commit ""` 在 CLI 上 exit 0 并**封存**了 `experiment_id: fxp_226e922081f64cf9b21bf276`，同一字面量在 SDK/REST 上是 `bad_request`（exit 3 / 422，消息 `--code-commit must be at least 7 characters`）；`factor build --code-commit ""` exit 0 并**落了四个分区**（`factor_obs_*`、`factor_manifest_*`、`factor_proc_*`、`factor_procmn_*`），而 `code_commit` 就在每条观测的 build 列里，故错戳比造它的那条命令活得更久。**`factor build` 的命令行是手写而非过 `_cli_arguments` 的，这一点承重**：那个 helper 会丢掉任何取值为 `""` 的参数（对 `--neutralization`/`--transform` 是对的规则），过它写出来的测试断言的其实是一条**根本没有 `--code-commit`** 的命令行，即断言在 fallback 上，缺陷与修法两边都绿 —— 正是本仓最常见的那类假绿。**回退那一半分别驱动**：`factor run` 省略该 flag 后封存的 `document.artifact.spec.code_commit` 长度 ≥7 且不等于本文件字面量；`factor build` 省略后与显式传 `resolve_code_commit()` 的另一个 store **`manifest_ids` 逐字相同**、与传本文件字面量的第三个 store **不同**（`manifest_id` 是含 commit 的内容地址，这是 `test_the_command_line_build_reproduces_the_fixtures_own_stored_tiers` 自己的论证；`build_view` 根本不渲染 `code_commit`，故断言只能落在地址上） | 集成：空串在三面与契约层含义一致，或被具名拒绝 | S83 |
| `V2-P4-053` | ~~**行为断言被一次 C 层派发跳跃击穿，且导入期成交同样不可见**~~ **已修复** | 技 | 047 | **P4 第三轮技术验收（2026-08-19）实测**。把成交丢进 `ThreadPoolExecutor().submit(policy.execute, ...).result()`，Python 栈上就没有被守护的帧 —— `Thread.run`/`_WorkItem.run` 是仅有的中间帧且都在 stdlib：`PROBE-B FILLED FROM candidate_ranking.py: filled buy 100 10.20 5.01`，而 `6 passed`、`lint-imports 8 kept / 0 broken`、`tests/unit 2373 passed`、ruff 与 mypy 全绿。**隔离对照**：同样的名字、同样的成交，改回同步直调立刻红并点名文件 —— 故**问题不是运行时拼名字（docstring 已承认那一条），是栈遍历本身**。**同类第二条，无需线程**：在 `candidate_ranking.py` **导入期**成交同样不可见，因为守卫在收集阶段就 import 了该模块，早于 `monkeypatch.setattr` 存在。这逐字反驳该文件写下的边界「code the pipeline never runs fills no order in production」—— 导入期代码是运行的、在生产里、每次 import。**验收人的结构判断（本行的真正内容）**：三轮下来每一次都用更强的守卫替换被绕过的守卫，而**每次新守卫都在第一次接触时被绕过**；稳定的是这个模式 —— **每次替换都是在同一个方法（`AShareExecutionPolicy.execute`）与同一条可见性规则（只看公开名）上撒更大的网**，而写在它上面的声明每次都比实测更宽。**建议方向**：要么给成交侧留可归因的审计痕迹（记录意图的构造点），要么 `sys.setprofile`/`threading.setprofile` 加导入期扫描；**主线补充的第三条路是架构而非审计** —— `cross_section` 为可交易性过滤 import `execution` 并**再导出**它，若停止再导出，形式 (b) 就需要一条真边，可移走一整类 | 单元：线程内成交与导入期成交都必须被具名点出 | D16 |
| `V2-P4-054` | ~~**「被发现」的禁用集丢掉所有下划线开头的名字，一个顶格 import 就能成交**~~ **已修复** | 技 | 047 | **P4 第三轮技术验收实测**。`_order_machinery()` 只保留 `not name.startswith("_")`，而第一层恰好只包住 `AShareExecutionPolicy.execute`，两者对一个覆写 `execute` 的私有策略都是盲的。探针是三处**纯明文源码**：`execution.py` 加 `class _SweepExecutionPolicy(AShareExecutionPolicy)` 覆写 `execute`，`cross_section.py` 把三个名字加进已有 import 块与 `__all__`，`candidate_ranking.py` **顶格、按最正常写法** import 后调用 —— `PROBE-N FILLED FROM candidate_ranking.py: filled sell 200 10.20 5.00`，`6 passed`、8 kept / 0 broken、ruff、mypy、`2373 passed` 全绿。**故该文件「a class added to that module is banned from both sources on the day it lands and no table has to be remembered」对任何 `_` 开头的类为假**。今日被发现的集合恰好是七个公开名。附带实测：**不加 `__all__` 条目时** ruff `F401` 与 mypy 的 `no_implicit_reexport` 确实会拒绝那次再导出跳跃 —— 但那是偶然（一行 `__all__` 同时解除两者），且都不是声称拥有该属性的守卫 | 单元：禁用集要么覆盖私有名，要么把声明收窄到公开名并写明 | D16 |
| `V2-P4-055` | ~~**第一层的「非空洞哨兵」数的是策略调用次数而非成交次数**~~ **已修复** | 技 | 047 | **P4 第三轮技术验收实测**。`fills.append(tuple(stack))` 在 `original(...)` **之前**执行且从不读 `result.status`。把 fixture 里 `_bar()` 的 `suspended=False` 改成 `True`：`execute` 调用 12 次、**实际成交 0 次**、`funnel.shortlist size = 0`，而守卫 `6 passed`。即排序与闸门两段跑在一个空名单上，哨兵被十二次**拒绝**满足。该 docstring 写的正是相反的话：「Fills **do** happen on this run … so a version of this test whose pipeline quietly stopped filling would be asserting nothing at all, which is this repository's recurring defect」—— 那个缺陷就在这条哨兵自己身上。修法：哨兵断言 `result.status == "filled"`，不是断言调用发生过 | 单元：零成交的 fixture 必须让守卫变红 | D16 |
| `V2-P4-056` | ~~**order intent 普查在本仓最常用的两种 docstring 形式上失明（839 个站点）**~~ **已修复** | 技 | 048 | **P4 第三轮技术验收实测**。两条声明加进 `backtest/cross_section.py`（两个契约 source 都够得到的模块），各自带着字面 ASCII 的 `order intent`，普查仍报 `MATCHES DECLARED TABLE: True`、`31 passed`：其一是**按本仓 100 列换行**折断的类 docstring，其二是 **PEP-258 属性 docstring**（`grep -n "order intent"` 一行就能找到）。隔离探针五处未命中且都含齐两个词：换行折断、属性 docstring、类属性 docstring、**字面量模块 docstring**、双空格。注意其中的不一致：**赋值式**模块 `__doc__` 看得见，**字面量**模块 docstring 看不见。**规模**：`src/openalpha_cn/*.py` 124 个文件全部有字面量模块 docstring，PEP-258 属性 docstring **715 处**，合计 **839 个站点普查读不到**。而 `V2-P4-048` 把 docstring 形式这条轴声明为**已封闭**并枚举为「字面量、f-string、赋值 `__doc__`」，还写着「Measured on the clean tree, the widened matcher finds exactly the same two declarations, so this cost no false positives」—— 零误报是真的，覆盖面不是 | 单元：覆盖属性与模块 docstring，或停止把该轴称为封闭 | D16 |
| `V2-P4-057` | ~~**披露检查的「活指针」从不绑定它自己那个指针，守卫文件可以被整个删除**~~ **已修复** | 技 | 048 | **P4 第三轮技术验收实测**。`pointers` 收集注释块里**任意** `tests/**/test_*.py` 子串并只断言各自能解析；该块本就另带三个指针，故守卫自己那个对断言是冗余的。探针：`pyproject.toml` 改一行把守卫路径从句子里去掉，**并把守卫文件整个移出仓库** —— `ls tests/unit/backtest/ | grep -c ranking_sources` 得 0，而披露测试 `31 passed`、`lint-imports 8 kept / 0 broken`。其 docstring 写着「A disclosure whose named guard was deleted or renamed is worse than none… **This is the assertion that would have caught `V2-P4-035`'s pin being replaced without the comment following.**」—— 它不会。修法：绑定**特定**指针，而非「存在某个测试路径」 | 单元：删除具名守卫文件必须变红 | D16 |
| `V2-P4-058` | ~~**`V2-P4-045` 的天花板推导所依赖的前提无人强制；另有三处小账**~~ **已修复** | 技 | 045 | **P4 第三轮技术验收实测**。`notional <= capital` 那一半是稳的（八个收盘价从 `0.01` 到 `10000.00` 在 `CEILING-1` 上全部成交且成立），但 roadmap 自己点名的抛点 `int(capital // (market.close * SHARE_LOT))` 其**商**的位数是 `close` 的函数而非 notional 的：`close=1e-12, capital=1e20`（**比天花板低四个数量级**且通过两处上界）仍 `InvalidOperation: [DivisionImpossible]`。`MarketBar.close` 是 `Field(gt=0)` 无下界、`DailyBar.close` 是裸 `float`。两位小数的真实行情下不可达，故出货面无恙 —— 但「The ceiling is therefore the same at every close price」比成立范围更宽，而实测范围恰好就是它成立的范围。**同行三条小账**：(a) 天花板是两个互不钉住的字面量（`shortlist_view.POSITION_CAPITAL_CEILING` 与 `ShortlistSpec` 的 `lt`），`grep -rn POSITION_CAPITAL_CEILING tests/` **无输出**，且该常量 docstring 仍写着「**This is the wrong file for it** … 应放在 `ShortlistSpec.position_capital`」而 `3e83587` 已经放了；(b) 给 `ShortlistGateSpec` 新增第四个门槛 `minimum_probe_ratio` 仍 **35 passed** —— 它确实会移动 `gate_manifest_id`（`stable_model_id` 转储整个模型），但门槛测试只变三个硬编码名字、`model_fields` 元审计钉的是 `ShortlistGateManifest` 而非 `ShortlistGateSpec`，故「bar n+1 red until somebody argues for it」不成立；(c) 往 `runtime/batch.py` 加 `_PROBE_SECOND_CAP = 10_000` 与 `_PROBE_SECOND_WORKERS = 8` 仍 **93 passed** —— `MAX_BATCH_*` 各只出现一次是真的，但由任何东西保证 | 单元：三条各自补断言，或把声明收窄到实测范围 | S43 |
| `V2-P4-059` | ~~**`--year` 是一个全局作用域，单年 build 把全市场静默缩成十一只，且 README 给的补救不可达**~~ **已修复** | 产 | 032, 033 | **P4 第三轮产品验收（2026-08-19）实测**，合成上游经 CLI 唯一声明的注入缝 `cli._panel_transport` 注入，其余（provider、PIT 过滤、`panel_ingest` 全部写入守卫、REST、SDK）**全部真跑**。`stock_basic` 按**上市生命周期年**分区，故 `panel build --year 2026`（README 自己的例子）把注册簿摊进 36 个年分区，2026 分区只剩当年有生命周期事件的 12 只。随后 `factor build --year 2026` 得 `{"coverage": {"raw": {"computed": 11, "insufficient_history": 1}}, "subject_count": 12, "universe_counts": [12]}` 且 **exit=0** —— 而同一 store 里有 **5,545 只证券、141,411 根 K 线**。`shortlist run` 接着从这 11 只里出榜，唯一痕迹是 `funnel 12 listed -> 11 scored` 与 `measurement.universe_count: 12`，两个计数，不是告警，且**从不与面板真实规模对照**。README 写了「只给一个前缀会静默缩小 universe」，**但补救不可达**：`--year` 是**一个**全局作用域同时管注册簿、日历与行情，而日历读要求**连续跨度**（补 2010 得 `needs 6209 days; 2011-01-01 is the first one absent`）、行情读要求**每年都有分区**（补齐日历后得 `daily year=1991 cannot be read; 9133 required date(s) are absent`）。故在全市场上算一个 1 日反转因子，必须先把行情回补到 1991 —— 正是 `panel build --help` 自己标价 ~282,000 次请求、「days rather than hours」的那次回补。**已修复**：`load_stock_universe` 现在把「请求区间**下方**、store 里已有的每个生命周期年」一并读进来 —— `--year` 仍然是日历与行情的分区年，登记簿则自己解析它要读哪些分区。向**下**扩不可能引入前视（更早的生命周期事件严格更可知），且 `resolved[-1] is requested[-1]`，故快照上界、跨年缺口拒绝两条规则判的还是原来那个跨度（两个变异探针确认等价）。一处改动同时修好 `factor_view` 的两个读取点与 `shortlist_view`（本轮不可编辑），验收：`tests/integration/test_cli_factor_universe_scope.py`，合成 5,545 只市场经 `cli._panel_transport` 注入，`universe_counts` 由 `[12]` 变 `[5545]`。README 那条不可达的补救已删，改写为「登记簿是唯一一个 `--year` 不必数全的数据集」。**新测得的代价**：36 个分区的登记簿读一次 **4.0 s**，其中 4.59 s 是 1,296 次 `_read_coverage` —— `read_if_ready` 每次都重评整个 requirement，N 个分区 N² 次查表。该 docstring 原写「milliseconds」，已按实测改写；修在 `PanelStore` 一侧，另开一条 | 集成：单年 build 缩窄股票池必须具名告警或拒绝；`--year` 对三类数据集的作用域可分 | S95 |
| `V2-P4-060` | ~~**一只盘中退市的证券让 `factor build` 崩溃，且原因被吞掉**~~ **已修复** | 产 | P1 | **P4 第三轮产品验收实测**（3 只 2010 年上市、2026-01-26 退市）：`factor build` 得 *"did not finish: it raised an unhandled StockUniverseError… The exception's own message is withheld because an unanticipated failure can carry whatever the frame it escaped was holding, including the credential"*。**被吞掉的那句话本身是好的**：`600001.SH has a delisting row and no listing row (with 3 such security/securities in this read); a listing is what gives a security a start date, so this is a partial read, not a security that appeared already delisted`。成因是 `factor_view._computed` 对 `universe.listed_on()` 抛的 `StockUniverseError` 做了妥帖包装（`FactorRunBlockedError`），**却漏了紧邻上面那次 `load_stock_universe` 的读**。用户看到的是「命令有缺陷、什么都没检查」，且 `panel doctor` **不报**这个问题。真实市场每年都有退市，故这是常态输入不是边角。**已修复**，与 `V2-P4-059` 同根：退市行在死亡年分区、上市行在上市年分区，只读一年就是一次残缺读取，故上面那次加宽本身就消掉了成因。另一半是报告：`factor_view._PANEL_FAULTS` 只列了四个，而 `cli._PANEL_WRITE_REFUSALS` 与 `panel_doctor._LOAD_FAILURES` 对同一个问题列了十一个并互相钉死，缺的正是 `StockUniverseError` 与 `PanelBatchError` —— 登记簿读**唯二**会抛的两个。新增 `_REGISTRY_FAULTS` 只给这一次读（而不是并进那个也守着 `compute_factor` 的元组，否则等于把本仓自己的 batch 缺陷洗成「面板读不了」），并把两个读取点收进一个 `_read_registry`，故只有一处可能写错。`shortlist_view._PANEL_FAULTS` 的相等断言因此仍然成立且仍有意义。**吞掉消息的行为本身是对的、保留不动** —— 变的是这次失败不再是「未预料」。残余（早年分区从未落盘的中断回补）仍会抛，但现在具名退 1 并带出那句诊断，有测试钉住 | 集成：盘中退市的证券必须给具名拒绝而非未捕获异常 | S95 |
| `V2-P4-061` | ~~**面板前进一个交易日，之前的横截面就永远无法再筛**~~ **已修复**（存储侧三个数据集由本行，其余四个由 `V2-P4-076`） | 产 | 032, 033 | **P4 第三轮产品验收实测**。同一 store、两个横截面均已落盘（`universe_counts: [5545, 5545]`）：`--as-of 2026-02-09`（面板最新会话）正常出榜 `5545 listed -> 5542 scored -> 5533 tradeable -> 25 shortlisted`，exit 0；`--as-of 2026-02-06`（昨天那张）得 `the price bars for 2026-02-06 could not be read…: ['not_yet_knowable']; daily holds information that first became available at 2026-02-09T08:30:00+00:00`，exit 1。定价读走的是**整分区就绪门**，分区里只要有比 `as_of` 新的行就整读拒绝。**与两处明文承诺正好相反**：`docs/api/http.md` 写 *"a fortnight-old cross section is offered to the market of its session and never to a later one its factor values never saw"*，README 同义。**后果落在本产品的核心用途上**：两天的榜无法并存比较、昨天的榜今天无法重跑、也无法复核。相关摩擦：第二天再 build 一个时刻会被 `factor_manifest_… already holds 1 subject(s)… it would drop [...]` 拒绝，守卫说得明白，但代价是每天要把该年建过的**所有**时刻在一次调用里重算，否则须 `--supersedes-raw` 抹掉昨天 | 集成：一个历史 `as_of` 的横截面必须能按它自己那个会话的行情撮合 | S27, S28 |
| `V2-P4-062` | ~~**候选榜跑完什么都不留，任何面都查不回来**~~ **已修复** | 产 | 033 | **P4 第三轮产品验收实测**：`runtime/` 下无任何 shortlist/ranking 产物；OpenAPI 里相关路由只有 `POST /api/v1/shortlists/run` 一条，**没有 GET**，也没有 `openalpha shortlist get\|list`。答案里的 `gate_manifest_id`/`ranking_manifest_id`/`ranking_content_digest` 是三个内容地址，**却没有任何东西可供寻址**。同一命令两次跑出的三个地址逐字相同（这点是对的，已核），但用户想留档只能自己存 `--json`。对一个以「可追溯、可复现」为标题的系统，这是缺口，且与 `V2-P4-061` 合起来使「跑两次、比较、解释变化」不可行 | 集成：出榜结果可按其内容地址取回 | S44, S49 |
| `V2-P4-063` | ~~**`panel build --as-of T` 造出的面板被 `panel doctor --as-of T` 判为 BLOCKING**~~ **已修** | 产 | P1 | **P4 第三轮产品验收实测**：与 build 完全同一个 `--as-of 2026-02-10T09:00Z` 做健康检查得三条 `BLOCKING … date_gap: 1 required date(s) are absent from daily, starting at 2026-02-10`，exit 1；改用面板自己的最新会话 `2026-02-09` 则八个数据集全部 READY、exit 0。成因：`panel build` 的会话循环跑到「该时刻的 Asia/Shanghai 日期**减一天**」，而 `panel doctor` 要求该日期**当天**有会话。按文档「一个 `--as-of` 贯穿整次 build」照做、再照做健康检查，**必然**拿到红色与 exit 1 —— 在 CI 里就是硬失败。**错的是 build，三比一**：`cli._build_sessions` 无条件减一天，而 `panel_ingest._sessions_published_through`（16:30 `DAILY_AVAILABILITY_TIME` 那条）是价格面**读侧**的唯一规则 —— `_price_requirement` 用它裁 `required_dates`（doctor **要求**那个会话）、`_read_visible_price_session` 只拒它之后的天（一次读会**给出**那个会话）、`newest_published_session` 用它定榜单的定价会话（`shortlist run` 会**按**那个会话定价）。**修法**：`_build_sessions` 直接 import 该函数，上界写成 `min(date(year, 12, 31), published_through)` —— 与 `_price_requirement` 逐字同一个表达式，故「build 取哪些会话」与「健康检查要求哪些会话」自此**按构造相同**而非靠约定相同。**为什么此前分不出来**：`tests/integration/test_cli_panel_horizon.py` 的两个时钟 `EARLY_CLOCK`/`LATE_CLOCK` **都是中午**，16:30 以下两条规则逐字一致，整个缺陷只活在那半天之上；新增 `CLOSE_CLOCK`（2026-01-20T17:00+08，开市会话，publish 后半小时）。**实测复现**（同一 `--as-of` 一次 build 一次 doctor，都走 `CliRunner`）：build exit 0、11 个会话、末 2026-01-19；doctor exit 1、`blocking date_gap 1 required date(s) are absent from stk_limit, starting at 2026-01-20`。修后 12 个会话、末 2026-01-20、doctor exit 0。**用 `stk_limit` 而非 `adj_factor` 是量出来的**：`adj_factor` 豁免 `required_dates`（`panel_doctor` 自己叫它「the gap」），在它上面两种规则都绿、分不出任何东西。`_pinning_remedy` 的散文一并改正（它此前写「bounds at the local date minus one」，而它给出的 remedy 是次日**正午**，新规则下仍然正确且现在有理由） | 集成：同一 `--as-of` 下 build 与 doctor 的会话上界一致 | S14 |
| `V2-P4-064` | ~~**`--max-staleness-days` 是一根横跨六个不同节奏数据集的杆**~~ **已修注册簿这一半、另两半具名披露** | 产 | P1 | **P4 第三轮产品验收实测**：在一份**只有 1 天旧**的行情面板上算因子，被 `the security registry cannot be read…: ['stale']; stock_basic reaches 2026-01-19T16:00Z, which is 17 days, 17:00:00 behind … (tolerance 5 days)` 拒绝 —— 注册簿是**事件驱动**的，其「新鲜度」等于最后一次有证券上市/退市的时间，故必须把杆放到 20–25 天，而这根杆存在的理由（其 docstring）恰恰是「一个最新会话是一个月前的行情面板，已经错过了一个月的市场」。**同仓已有正确做法**：`panel doctor` 对 `cadence=event_driven` 的数据集会 `waived=max_staleness`，`factor build` 不会。**修法**：`factor_view.CADENCE_WAIVED_READS` + `_event_clock_bound(dataset, requested)`，事件驱动的读拿 `None`、其余原样拿调用方的杆。该集合由 `test_the_waived_reads_are_a_named_subset_of_the_doctors_event_driven_set` 与 `panel_doctor.DATASET_CADENCE` 钉住：**真包含**（不得豁免任何非事件驱动的数据集）+ **补集写成字面量**（`DATASET_CADENCE` 里新增第六个 event_driven 会让它红并点名自己）。该集合**不 import 而是重述** —— `test_factor_view_layering.py` 按相等钉死 `factor_view` 的兄弟集且 `panel_doctor` 蓄意不在其中，这正是 `FactorPlaneSeal` 的老办法：两张不能互相 import 的表，由一个可以同时 import 两边的测试守住。**实测复现**（`CliRunner` 驱 `factor build --tier raw --max-staleness-days 5`）：exit 1、`the security registry cannot be read ...: ['stale']; stock_basic reaches 2026-01-01T16:00:00+00:00, which is 6 days, 17:00:00 behind ... (tolerance 5 days)`；修后 exit 0。**生成语料与真实语料的差**：fixture 的注册簿每只只有一条 `LISTED_ON`(2026-01-02) 事件，对 2026-01-08T17:00+08 是 6d17h；真实语料量到的是 17d17h —— 同号同因、量级三倍，因为真注册簿的最新事件是交易所上一次接纳/剔除某个名字，而非 fixture 窗口的第一个会话。**五个 event_driven 只放进一个，另外四个各有量出来的理由**（写在该常量的 docstring 里）：`namechange`/`suspend_d` 在 run 路径上本就传 `max_staleness=None`，杆够不到；`index_member_all` 与 `daily_basic` 共用 `load_industry_market_cap_cross_section` 的**同一个** `max_staleness` 形参，拆开是 `panel_neutralization.py` 的编辑；`index_classify` 本面根本够不到（`RESEARCH_PLANE_DATASETS` 如此写）—— 顺带**实测到本次修改踩了那张审计**：先前版本 import 了三个数据集名常量把 `factor_view` 的 `named` 集撑过其声明 `reached`，`tests/unit` 5 红，故集合改写成只用本模块已有的常量。**没修的两半具名披露**为 `KNOWN_FACTOR_RUN_LIMITATIONS.the_freshness_bar_is_waived_by_cadence_only_where_the_read_is_outside_the_engine`（该注册表 7→8，`REGISTRY_ENTRY_COUNTS` 同步）：①四个季度报表数据集仍原样吃这根会话节奏的杆，**不能在这里豁免** —— `compute_factor._validate_requirements` 对因子**读**的每一个数据集都拒绝被豁免的 `max_staleness`（`test_the_waiver_this_command_offers_is_refused_by_the_engine_that_reads_the_bound` 是那堵墙），要关它得改交给 `compute_factor` 的 requirement 而非本 flag；②`index_member_all` 那一条在生成语料上**量不出来**（实测 `--tier neutralized` 在 5 与 30 天都 exit 0），因为 fixture 的成分股分区最新一次归属就落在 build 窗口内，而真实语料是上一次年度成分审查。**本修改让两条既有守卫露了底 —— 它们此前正撑在这个缺陷上，故重新落地而非放松**（由更宽的回归跑出来，2 red）：①`test_the_registry_is_read_at_each_prediction_instant_and_not_once_for_the_build` 当初就是因为「把注册簿读固定到 `request.as_ofs[0]`」这个变异体活了下来才写的，而它分辨两个时刻靠的**正是这根杆** —— 杆一撤它就分不出来了。改用实质后果分辨：`universe.termination_on_the_newest_session` 落一条 2026-01-16 的退市、`available_time` 为当天零点，故在 2026-01-08T17:00+08 被扣下、在 2026-01-16T17:00+08 可见，`delist_date` 又是开区间 —— `universe_counts` 得 `[8, 7]`；~~实测固定到 `as_ofs[0]` 得 `[8, 8]`、固定到 `as_ofs[-1]` 得 `[7, 7]`，**两个方向都会红**~~ —— **这两个数字与「两个方向都会红」均为假，`V2-P4-113` 在 `037ffa8` 上逐条重测并就地更正**：固定到 `as_ofs[0]` 确实红，但走的是**具名拒绝**而非计数 —— 快照定在 2026-01-08，`listed_on(2026-01-16)` 越过快照，构建直接 blocked（`the stored registry cannot say who was listed on 2026-01-16`），**根本到不了 `universe_counts`**，故 `[8, 8]` 从来不是它的答案；固定到 `as_ofs[-1]` **在整份文件上全绿**（`36 passed`，与基线逐字节相同），它给出的是 `[8, 7]`，**正是正确答案**，`[7, 7]` 是另一个变异体（钉住挂牌日）的答案。**故本行只保住了「早读」一个方向；「晚读」方向才是前视，且当时无人守**。`universe_counts` 对晚读**结构性失明**、并非夹具太薄：`stock_basic` 是 `calendar_static`，一条生命周期行「日期 ≤ 读取日」时才可见，而它也恰在同一条件下才会改变 `listed_on(该日)` —— 两个条件逐字相同，故任何夹具上晚读都移不动更早时刻的成员数。补守见 `V2-P4-113`（改测 `subjects`/`not_in_universe`）。不加该 shape 的语料两个时刻都是 `[8, 8]`（该对照仍然成立）；重放原变异体确认被杀。②`test_every_declared_build_parameter_reaches_the_answer_on_both_faces[max_staleness_days-1]`：那根杆此前之所以「够到答案」，靠的就是拒掉注册簿读。修后在 `BUILD_INSTANTS` 上面板只有半小时旧，而 `_build_staleness` 拒绝任何 `< 1`，故**没有任何合法取值还能移动答案** —— 把该行降为豁免就是 Task 39 的发现在为防它而写的测试里复现。改为量出窗口内唯一一个会话杆还能作数的时刻：`STALE_PANEL_INSTANT`（2026-01-17T17:00+08，最后一个会话之后的周六），`daily` 落后 1d2h，`1` 得 `['stale']`、`2` 出货 —— 同一个 store、同一个时刻、只差一个取值；sweep 因此新增按行的 `common`，**同时**施加于基线与变体，故被比较的两个答案仍只差一个参数。**周六而非下一个会话是量出来的**：`_price_requirement` 把 `required_dates` 拉到 `_sessions_published_through`，故 `as_of` 落在 2026-01-19（周一，开市）会让那个会话**变成应有**，1/2/3/5 四个取值实测**全部** exit 1，那是 `date_gap` 而非 `stale` —— 答案会因为与这根杆无关的理由移动。**变异清扫（063/064/052/108 四行共用一次）**：22 个变异体、**在绿基线上跑**（100 passed），第一轮 **20 杀 2 活**；两个幸存者都是**选套件的产物而非覆盖漏洞**，第二轮用本该覆盖它们的文件重跑（基线 51 passed 绿）把两个都杀掉 —— `063.03`（去掉 `min(date(year, 12, 31), ...)` 那层夹）由 `test_cli_panel_years.py` 杀（它在 2026 的时钟上建 2025，去掉夹之后 `trading_days_between` 会把两年的会话一起扫进 2025 的分区，逐年会话数断言即红）；`108.04`（注册簿读改用 `_CROSS_SECTION_FAULTS`，丢掉 `StockUniverseError` 与 `PanelBatchError`）由 `tests/unit/test_shortlist_view.py` 与 `test_partial_registry_faces.py` 杀。第二轮带**对照**：把 `_CROSS_SECTION_FAULTS` 退回 `_PANEL_FAULTS`（即撤销 `108` 的修复）在第二轮**存活**、在第一轮被杀 —— 证明第二轮那套文件不是「什么都杀」。两轮合计 22 个变异体全部被杀 | 集成：事件驱动数据集不受会话节奏的杆约束 | S14 |
| `V2-P4-065` | ~~**`--config-digest ""` 读完整个 store 之后才被拒，且被归因到「证据 join」** —— **被阻塞：修法整条落在 `shortlist_view.py`，不在本次分工的文件里**~~ **已完成** | 产 | 046 | **P4 第三轮产品验收实测**：三个面**彼此一致**（跨面 parity 成立，`V2-P4-046` 那一半是真修好了），但内容是 `the shortlist … could not be joined to the evidence this request supplied: 1 validation error for CandidateRankingManifest / config_digest / String should match pattern '^[0-9a-f]{64}$'` —— **本次请求没有提供任何证据**。而 `shortlist_view.py:628-631` 的注释恰好写明了 `code_commit` 为什么要提前校验：*"`build_ranking_manifest`, which raises the same objection after a store has already been read and would therefore report a mistyped flag as a fact about the panel"*。`code_commit` 修了，`config_digest` 留在旧路径上：泄漏内部模型名、错误归因、且要等整次面板读完。**本轮定位（未改一行）**：`config_digest` 在 `src/` 里只出现在 `shortlist_view.py` 的四处（`ShortlistRunRequest` 字段、`shortlist_request` 形参、`.strip()` 落进请求、`run_shortlist` 交给 `build_ranking_manifest`），`factor_view.py` 里**一次都没有**；`cli._resolved_config_digest` 只做「省略则解析」，三面 parity 已由 `046` 修好并经本行实测复述，故这是一处**视图层**编辑，落在共享的 `shortlist_view.py` 上而非任何一个面上。**确切编辑**：在 `shortlist_request` 里、紧接那条 `if len(code_commit.strip()) < 7:` 之后，加一条同形状的请求期检查 —— `if not re.fullmatch(r"[0-9a-f]{64}", config_digest.strip()): raise ShortlistRequestError(...)`；模式取自 `backtest/candidate_ranking.py:681` 的 `config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")`（`domain/run.py:233` 是同一条），消息按 `code_commit` 那条的体例写明「不同配置可能从同一面板切出不同名单」。理由就写在该检查上方本行已逐字引用的那句注释里：只有这样才不会落到 `build_ranking_manifest`「which raises the same objection after a store has already been read」。**已完成（2026-08-25，由集成者在合并第 9 波时施加该行给出的确切编辑）**：`shortlist_request` 里紧接 `code_commit` 那条检查之后加入 `re.fullmatch(r"[0-9a-f]{64}", config_digest.strip())`，模式取自契约本身。**分离器是空 store 而不是建好的面板**：请求期拒绝不可能依赖面板，所以空 store 把两个答案拉到最远 —— 修复前空 store 先答（`['partition_missing', 'field_missing']`、exit 1），digest 从未被检查；修复后 digest 先答（exit 3），一个分区都不会被打开。若用建好的面板，两种次序都会给出拒绝，测试就分不开它们。**顺带修掉一处该行未提及、由 P4 诊断路报告的相邻缺陷**：`code_commit` 只校验了 `< 7` 而契约是 `min_length=7, max_length=64`，故一个 65 字符的 commit 仍会晚到 `build_ranking_manifest` 并被报成关于面板的事实 —— 与 digest 同一形状。**六个坏输入（digest 空 / 非 hex / 63 位 / 大写、commit 6 位 / 65 位）在 CLI、REST、SDK 三面各被具名拒绝，第七组合法输入则必须落到面板读取**（exit 1、`partition_missing`），这一组是关键分离器：只断言「坏输入被拒」的测试，在一个「什么都拒」的检查上同样会绿。19 条测试 | 集成：`config_digest` 与 `code_commit` 同在请求期校验 | S48 |
| `V2-P4-066` | ~~**可交易那一档一个名字都不点**~~ **已修复** | 产 | 023, 033 | **P4 第三轮产品验收实测**：`funnel 5545 listed -> 5542 scored -> 5533 tradeable -> 25 shortlisted`、`measured tradable=0.9978`。验收人核对过当日停牌恰好 9 只、被丢掉的恰好 9 只、且这 9 只在因子分区里 `coverage=computed`（即**第二档剔除逻辑是对的**）。但答案里 `funnel` 只有 `clip_block/coverage/excluded_by_coverage/scored_count/shortlist/tied_at_the_cut/tradeable_count`，而 `excluded_by_coverage` 只覆盖**第一档**；全文搜不到 `halted`/`below_board_minimum`/`up_limit`/`not_tradable` 任何一个字。`--min-tradable-ratio` 这道闸测的就是这个比率，一旦跌破门槛把榜拒掉，**用户无从知道是谁、为什么** —— **`V2-P4-066` 实测定案（2026-08-25）**：在 `price_limits.one_price_limit_up` 面板上以 `--position-capital 2000` 逐字复现 —— `funnel` 的键恰好是本行点名的那七个，`8 scored -> 6 tradeable`、`tradable=0.75`，两只消失且无规则无名字。**已交付**：`TradeabilityCensus` 新增 `refused`（每只被拒证券具名 + 判定规则 + 仅 `rejected` 带执行策略自己的原话），`__post_init__` 四个方向把名字与既有计数互校（计数不符、判定不符、原因不符、顺序不符各自具名拒绝）；`shortlist_view` 渲染 `funnel.refused_by_verdict`（四格恒全出，`ScoreCensus` 的「没人是」≠「没看」规则）、`funnel.rejection_reasons`、`funnel.untradeable`（具名，`MAX_NAMED_UNTRADEABLE=50` 封顶）与 `funnel.untradeable_not_named`（残差）；`tradable_ratio_below_floor` 拒绝语点名规则、每条规则下的前几只与最常见的策略原话；终端新增 `untradeable` 段落（`unscored` 的姊妹，无人被拒时整段省略）。三面同源，`shortlist_id` 一致 | 集成：`tests/integration/test_shortlist_tradability_reasons.py` 六个测试全程走 `CliRunner`/`TestClient`（含「干净会话四格全零而非省略」「封顶与残差」「终端面在 exit 0 上也解释第二档」）；单元：`tests/unit/backtest/test_cross_section.py` 的普查互校与 `RefusedSecurity` 双向规则 | S48 |
| `V2-P4-067` | ~~**HTTP 参考文档里的 `run_manifest_id` 前缀是错的；拒绝语从不点名能修好它的那条命令**~~ **已完成（(a) 前缀；(b) 两个面的三个档全部带 remedy）** | 产 | — | **P4 第三轮产品验收实测**。(a) `docs/api/http.md:302` 写 `"run_manifest_id": "rmf_…"`，照抄被拒：`carries run_manifest_id 'rmf_…', which is not stable_model_id(prefix='run', ...)'s own output` —— 正确前缀是 `run_`。(b) `factor_obs_reversal_1d_v1 year=2026 … ['partition_missing','field_missing']` 不说「去跑 `openalpha factor build`」；`namechange` 缺分区也不说「去跑 `openalpha panel build --dataset namechange`」，而 `shortlist run` **需要** `namechange` 且 README 的候选榜示例未提。这些提示都在 `--help` 里，唯独不在用户真正撞上的那行错误里 —— 与面板闸门那条被全仓引为范本的拒绝语正好相反。**(a) 已修复并加了钉子**：`be262ea` 上全仓 `rmf_` 已零命中，`docs/api/http.md` 现写 `"run_…"` —— 是 `V2-P4-032`/`V2-P4-049` 改写候选榜那节时**顺手**修掉的，没有任何东西把它钉在铸造函数上。现补 `tests/integration/test_http_doc_identifier_prefixes.py`：期望前缀不写死，而是现造一个 `RunManifest` 读它自己的 `run_manifest_id`；再把文中每一处 `<前缀>_…` 例子对 `live_prefixes()`（按 AST 读源码树的前缀普查）校验。**这条审计自身被证明有牙**：把 `rmf_` 写回真实文件后，两条断言分别以 `documents ['rmf'] where this build mints 'run'` 与 `['rmf'] ... minted nowhere in this repository` 变红，随后文件已还原。**(b) 的 `namechange` 那半已由 `V2-P4-078` 修掉**（见该行）。**(b) 的因子那半仍未修，且不在本次文件归属内**：`panel_factors.py:5056-5060` 抛 `FactorEngineError(f"{dataset} year={year} cannot be read at ...: {codes}; {details}")`，全程不提 `openalpha factor build`；该拒绝语的补救应挂在 `factor_view.py`（其 `_written` 的 docstring 自己说「在这里封装才让拒绝语能带上 remedy」），而 `factor_view.py` 是本次被明确禁止编辑的文件。范本已在仓内成型：`shortlist_view.py:2549-2559` 的 `_build_remedy`，且它只在「该数据集一个分区都没有」时触发（`store.registered_years(dataset)` 为空）—— 因子分区的补救应照抄这条界。**已完成（2026-08-25）**：(a) 半在 `be262ea` 上**已被 `V2-P4-032`/`049` 顺手修好且无人钉住**（`rmf_` 全仓零命中），第 9 波补了 `test_http_doc_identifier_prefixes.py`，并把 `rmf_` 写回真实文件、看着两条断言按本行原措辞变红再还原，证明审计有牙。(b) 半由集成者施加，**并把本行的定位修正了一处**：`_read` 的 docstring 声称因子档「已经带着 `openalpha factor build ...`（见 `_resolve_instant`）」，该句为假 —— `_resolve_instant` 拒绝的是**读取成功却返回空**，而空 store 会先撞上**读取抛异常**这条路，实测得到 `the raw reversal_1d/v1 observations could not be read out of ...: ['partition_missing', 'field_missing']`，**通篇不点名任何命令**。豁免覆盖了类、实例没被覆盖。新增 `_unbuilt_factor_remedy`，照抄 `_unbuilt_dataset_remedy` 的界（仅当该因子一个分区都没注册时触发）。**只覆盖 raw 档，这是边界不是遗漏**：`neutralized` 有两种分区拼法（`factor_neut_*` / `factor_neutmn_*`），对错的那个问 `registered_years` 会对着装着另一个的面板回答「什么都没存」并给出用户不需要的重建 —— 正是 `V2-P4-078` 记过的那个陷阱（点名一条帮不上忙的命令比不点名更糟）。该不对称记入 `KNOWN_SHORTLIST_VIEW_LIMITATIONS.only_the_raw_tiers_unreadable_factor_refusal_names_the_command_that_builds_it`（注册表 7→8），并由两个方向的测试钉住：raw 必须点名命令，另两档必须仍不点名。**2026-08-25 更正：本行此前被我（集成者）错误地整条标为已完成。** P4 第 9 波产品验收实测推翻：`_unbuilt_factor_remedy` **只存在于 `shortlist_view.py`**，而本行自己的复现命令是 `openalpha factor run`，走 `factor_view.py` —— 那个面**三档全都没有 remedy，包括 raw**。交付该受阻依赖的 agent 在其提交信息里已明写「(b)'s factor half remains open」，我却划掉了标题。我实际做的是：**在另一个面上复现了同类缺陷、修好那个面、然后结清了一条复现命令在别的面的行** —— 正是本仓反复记录的「覆盖了实例、没覆盖类」，这次由我犯。**(a) 前缀半确已完成**（`rmf_` 全仓零命中，且 `test_http_doc_identifier_prefixes.py` 已钉住并被证明有牙）。**(b) 在 shortlist 面确已完成**（raw 档带 remedy，另两档按 `KNOWN_SHORTLIST_VIEW_LIMITATIONS.only_the_raw_tiers_unreadable_factor_refusal_names_the_command_that_builds_it` 声明的边界不带），**但 factor 面一行未动，仍开着**。验收另指出该不对称的论证也宽了一档：docstring 自己承认「`processed` has one dataset name per definition」，所以 processed 被排除是被顺带打包、没有自己的理由。**已完成（2026-08-25 第 10 波）**：(b) 的 factor 面已修，且**边界不是收窄一档而是整个作废** —— 实测推翻了原来那条理由本身。`panel_neutralization.neutralized_factor_dataset` **只吃 definition、根本不吃 neutralization**（其 docstring 原话：「Keyed by the factor and **not** by the neutralisation」），`factor_neutmn_*` 是**清单**数据集、是 `factor_procmn_*` 的结构孪生，而同一段并没有因为 `factor_procmn_*` 就说 processed 有歧义；`load_neutralized_factor_observations` 自己写着「the neutralisation is a filter here and the factor is the dataset」。**三个档各自只有一个观测数据集名，都能从 definition 单独算出**，所以「问错了拼法」这个失败模式不存在。**该论证由产品面实测钉住而非重述**：`test_a_tier_stored_under_one_neutralisation_is_found_by_a_request_naming_another` 用 `probe_neutral/v1` 写入残差、用 `industry_and_size/v1` 读回，同一个分区被打开、rows 被过滤掉、拒绝语里没有任何 build 命令。**顺带实测到两处死代码/死断言，都是本行原修法自带的**：①`_unbuilt_factor_remedy` 里的 `tier != "raw"` 分支**不可达** —— 唯一调用点在 `if tier == "raw":` 里侧、传字面量 `tier="raw"`，所以那条承载了全部「只覆盖 raw」论证的子句从来没运行过；另两档没有命令是因为它们的 `_read` 压根没传 `remedy=`。②钉住该边界的 `test_the_other_two_tiers_keep_the_unremedied_message_as_declared` **在那个 fixture 上分不出两个答案** —— 它不带 `--transform` 驱 `--tier processed`，实测 exit **3**、`a processed-tier screen needs a --transform`，`shortlist_request` 在打开任何 store 之前就拒了，所以 `assert "openalpha factor build" not in result.stderr` 是对着一句在任何实现下都不可能包含它的话断言的，边界往哪边倒它都绿。这正是本仓反复记录的那个形状。**修法**：`factor_view._read` 加 `remedy=`（两条消息都带，disclosable 半边不含 store 路径），新增 `factor_view.FACTOR_TIER_DATASETS`（三档→三个 dataset 函数）与 `_unbuilt_factor_remedy`；`shortlist_view` 因 `lint-imports` 不能 import `factor_view`，故**重述**该表（`_PANEL_FAULTS` 的老办法），由 `tests/unit/test_shortlist_view.py::test_both_faces_name_a_tiers_partition_with_the_same_table` 按**函数对象**相等钉住（按 key 集合相等看不出两边指向不同 dataset 函数）。**shortlist 面只有 raw / processed 两档可达**：`run_shortlist` 第一句无条件拒 `tier == "neutralized"`（本面不加载行业市值截面），故那条 `_rows_for` 分支带着 remedy 但**不声称被覆盖**（`_declared_transform` 的规矩：frozen dataclass 仍可直接构造，故前提写在读处），并由 `test_the_neutralized_tier_is_refused_by_this_face_before_any_partition_is_opened` 钉住那道 guard —— 谁哪天放开它，那条测试会红并告诉他多出一档欠一个测试。**注册表**：删掉 `KNOWN_SHORTLIST_VIEW_LIMITATIONS.only_the_raw_tiers_unreadable_factor_refusal_names_the_command_that_builds_it`（8→7，那条记的是一个理由为假的边界），新增 `KNOWN_FACTOR_RUN_LIMITATIONS.the_unbuilt_factor_remedy_fires_only_when_no_year_of_the_tier_is_registered`（8→9），记的是**幸存下来的那条边界** —— 该 remedy 只在「该档一年分区都没注册」时触发，别的短法（年份不在、读不出、过期）都不是 `factor build` 能全答的，`V2-P4-078` 已量过点名一条帮不上忙的命令的代价。`REGISTRY_ENTRY_COUNTS` 两行同步 | 文档 + 集成：修正前缀；缺分区的拒绝点名补救命令 | S48 |
| `V2-P4-068` | ~~**一条测试的通过依赖于选择顺序**（ 与  同时被选中时， 失败）~~ **已修复（由 `V2-P4-089` 的结构性修法关闭，此处复核而非采信）** | 测 | — | **wave 4 审计 agent 在干净的 `ce70d07` 上用 stash 复核过，与其改动无关**。全量套件里**不触发**，故是收集顺序 / logging 交互。本仓已有同类前科：`test_import_layering.py` 里那次刻意的裸 `lint_imports` 调用同样只靠收集顺序才不互相污染（可复现为 2 failed）。两者应一起看 —— 一个只在特定选择下红的测试，等于在 CI 之外不可信 | 单元：任意子集选择下结果一致。**根因已定位（2026-08-20，由 `V2-P4-012` 在干净基线上复现）**：`test_import_layering.py` 运行 `lint-imports`，**这会让 `openalpha_cn` 的 logger 保持禁用**，随后依赖 caplog 的测试便看不到任何记录。第二个受害者已实测：`pytest tests/unit tests/integration` 作两个显式参数时 `tests/integration/test_batch_research.py` **4 条失败**，在 `a58f924` 上把改动 stash 掉可同样复现。**项目自己的门禁看不见它** —— `uv run pytest -q` 无参数时先收集 integration 再收集 unit。且 `test_import_layering.py` **已经带着两条关于恢复 logging 的守卫测试**（`test_running_the_import_linter_leaves_an_existing_logger_enabled`、`test_no_test_in_this_module_calls_lint_imports_without_restoring_logging`），故那次恢复是**不完整**而非缺席 —— 两条守卫各自为真，合起来不足。**复核结论：本行已由 `V2-P4-089` 关闭，且是量出来的、不是假设的。**原始失败选择两个方向都绿：`pytest tests/unit/test_import_layering.py tests/unit/runtime/ -q` **70 passed / 1 skipped**，两个路径对调后同样 **70 passed / 1 skipped**；只取被点名的那条模块 `tests/unit/test_import_layering.py tests/unit/runtime/test_composition_migrations.py` **37 passed**。`V2-P4-089` docstring 里逐字记下的两次复现也都绿了：`test_import_layering.py + test_migrations.py + test_batch_research.py`（原 **4 failed / 54 passed**）今日 **59 passed**，`test_model_view.py + 同两个 integration`（原 **6 failed / 46 passed**）今日 **53 passed**。**绿是因为容器而不是因为运气**：机制至今仍然活着 —— `test_running_the_import_linter_leaves_every_existing_logger_enabled` 经 `raw_lint_imports_disables` 断言**裸** CLI 依旧会禁掉既有 logger，故把任一调用点退回裸调，上述选择会立刻回红。本轮未为此行改动任何代码 | — |
| `V2-P4-069` | ~~**`PanelStore.read_if_ready` 的逐年就绪重评估是 O(N²)，且其 docstring 把代价说小了三个数量级**~~ **已修复** | 技 | 059 | **`V2-P4-059` 的数在合成语料上被逐字复现**：每分区仅 20 只证券的 store 上，36 个分区 = **1,296** 次 `_read_coverage`、**4.087s**（6/12/18/24 → 36/144/324/576 次，即精确的 N²）—— 与 5,545 只真实市场上 profile 出的 1,296 与 4.0s 完全一致，**这正是复现最锋利的地方：这些时间里没有一点是数据**（真正的 Parquet 读只占 0.21s）。修法就是 `V2-P4-059` 预言的那一个：`PanelStore.assessed()` 取一次裁决、交回它所许可的逐年读；`read_if_ready` 与 `read_visible_at` 变成它上面的各一行，**故那 14 个调用方的契约一个字没动**。`panel_ingest` 里 7 个按年循环的 loader 改用该 scope。实测修后 36 分区 = 36 次 / **0.727s**，72 分区 = 72 次 / 1.256s —— 是线性而非更小的常数（故意测到缺陷报告规模之外的一个点）。**`read_visible_at` 必须一起修**：`V2-P4-076` 把五个 loader 从前门搬到后门，自己就写明二次项原样保留；只修行标题点名的那扇门，被实测到的那个调用方（36 年注册簿，如今走后门）会一秒都不变快。**两处 docstring 的「milliseconds」都补上了数**：`load_stock_universe` 那处（本行已记）；以及 `load_daily_bars` 那处 —— 实测走一年 244 个会话的重评估是 **5.367s**（每次 22ms），对比它真正想要的 244 次 `query` 的 3.025s；那一处是**线性**不是二次，`assessed()` 治不了它（每次都是不同的 requirement），故如实留下并附上数字而非顺手改小。**scope 的代价如实入册**：新增 `KNOWN_STORAGE_LIMITATIONS.an_assessed_read_scope_checks_each_partition_file_once_and_not_once_per_read` —— 分区文件的三个物理事实（存在、Parquet 首尾魔数、footer 行数）在一个 scope 内只读一次，故「读到第 k 年之前有人往第 k 年文件里追加一行」这一窗口由零变成一个循环宽；扫描本身仍逐次包裹，检查仍在它所针对的那一年被读之前发生。台账由 31 项 5 条变 **6 条**，`REGISTRY_ENTRY_COUNTS` 同步 | 单元：读路径的就绪评估次数不随分区数平方增长（`tests/integration/panel/test_readiness_assessment_cost.py`，两扇门各一次，比 N 与 2N，计次不计秒）。**四行合并做了一次变异清扫：52 个变异体、41 杀 11 活，基线在生成第一个变异体之前先证明为绿**（四套目标测试各跑一遍）。11 个幸存者里 **5 个落在 docstring 散文行上、根本不是变异体**（生成器的行过滤太宽，如实记下而非从分母里剔除），1 个是错误消息里年份的排序（`sorted`→`list`），1 个是本次**原样搬运**的既有逻辑（`pooled_years` 的条件），**4 个是真的、且每一个都指向本项目最常记的那种缺陷 —— 断言存在但在该语料上分不出两个答案**：① ②③ 三个把扫描钉到 `requirement.years[0]` 的变异体全部存活，因为每个分区的 `close` 都是同一组值，「读了第 k 年」与「把第 0 年读了 N 遍」**行数完全相同** —— 已让每个分区的值自报年份（`_close`）并改为逐年比对内容，三个全部转为被杀（其中一个还需要补一条对**未加 scope 的两扇公开门**在多年 requirement 上的测试，因为原测试只驱动 `AssessedPanelRead`）；④ 把 `_read_visible_event_dated_rows` 里的 `assessed.read_visible_at(...)` 换成 `store.read_visible_at(filtered, ...)` —— **语义完全相同、答案逐字相同，只是把二次项装回去**，而这正是本行所针对的那个调用方；任何关于答案的断言都分不出它，故补一条结构断言：在同一个函数里开了 scope 就不得再对同一个 store 取逐次门（`test_a_function_that_opens_a_scope_takes_every_read_from_it`，按**接收者**而非属性名判别，因为 `assessed.read_visible_at` 与 `store.read_visible_at` 同名）。四个真幸存者补完后重跑，均转为被杀 | S5 |
| `V2-P4-070` | ~~**`shortlist_view` 仍缺 `_REGISTRY_FAULTS`，残缺注册簿在出榜面上仍是 exit 5 且消息被吞**~~ **已修复** | 产 | 060 | **`V2-P4-060` 修复时的受阻依赖**：`factor_view` 加了 `_REGISTRY_FAULTS`（把注册簿读会抛的两个故障码补进去，`_PANEL_FAULTS` 原有四个而 `cli._PANEL_WRITE_REFUSALS` 有十一个、且与 `panel_doctor._LOAD_FAILURES` 钉成相等），故 `factor build` 现在给 exit 1 与可执行的诊断。**`shortlist run` 没有** —— 该文件由同轮另一个 agent 拥有，未能编辑。用户在出榜面上遇到中断的回补，仍看到「命令有缺陷、消息因可能携带凭据而被withheld」 | 集成：出榜面对残缺注册簿给具名拒绝 | S48 |
| `V2-P4-071` | ~~**同一年里建第二个时刻会被拒，这是「明天再跑一次并比较」路上仅存的一堵墙**~~ **已修复** | 产 | 061 | **`V2-P4-061` 修复后由其实施者标出**：`openalpha factor build` 在更晚一天用第二个 `as_of` 仍被逐字拒绝 —— `factor_manifest_reversal_1d_v1 year=2026 already holds 1 subject(s) and this write carries 1; it would drop ['fmn_…']`。那是 `write_factor_panels` 的**写侧契约**（`write_partition` 整分区替换、不追加），由 `_refuse_to_drop_a_stored_build` 守卫，具名逃生口是 `--supersedes-raw`；读路径没有任何东西碰它，故 `061` 未受影响也未改善它。**为什么现在它变关键**：`V2-P4-061` 之前，「两天的榜无法比较」有两堵墙（历史横截面不可筛 + 第二个时刻建不出来）；`061` 拆掉了第一堵，**这是仅存的一堵**。用户今天的选择仍是二选一：把该年建过的所有时刻在一次调用里重算，或用 `--supersedes-raw` 抹掉昨天那次。**与 `V2-P4-062`（出榜结果不落库、无 GET 路由）合起来，「跑两次、比较、解释变化」仍然做不到** | 集成：同年内追加一个新时刻不得要求重算或抹除既有时刻 | S44, S49 |
| `V2-P4-072` | ~~**P4 交付的整条出货面在 e2e 上零覆盖**~~ **已补齐** | 测 | 032, 033, 061 | **2026-08-19 实测**：e2e 套件在 `6400679` 上跑真实 Tushare 端点得 **33 passed / 1:44:21**，而在 `d703905`（约两百个提交之前）同样是 **33 passed** —— 中间落地了 `shortlist_view.py`、`backtest/{cross_section,candidate_ranking,shortlist_gate}.py`、CLI/REST/SDK 三个出货面、面板→截面适配器与 `V2-P4-061` 的可见时刻定价读，**e2e 一条都没长**。`grep -rn "shortlist\|CandidateRanking\|cross_section" tests/e2e/` **无输出**：现有 33 条全部在 `test_panel_chain_online.py`（23 条）与 `test_pit_injection_online.py`（10 条），即面板摄入与 PIT 注入，**没有一条从用户站的地方走到候选榜**。**这正是 P3 验收那句根因在 e2e 层的复现**：单元与集成层已经被三轮验收逼着从 `CliRunner`/`TestClient`/SDK 出发，而唯一打真实数据的那一层仍然只测面板。一个「可进生产」的判断若建立在这 33 条上，它证明的是面板链路可用，不是产品可用。**已补齐**：`tests/e2e/test_shortlist_workflow_online.py` 12 条，e2e 由 33 涨到 45，全部对真实语料跑 `panel build → factor build → shortlist run` —— 出榜、同年内追加第二个时刻而第一个的答案与其 `shortlist_id` 原样存活（`071`）、按 `shortlist_id` 从 CLI / REST / SDK 三面取回同一份文档并比较两次结果（`062`）、证据的 `run_manifest_id` 能否解析到已存 run 的双向（`049`），外加两条只有真实注册簿才产得出的具名拒绝。**同时实测到两条与仓库自述相悖的事实，应各自另开一行**：(a) `stock_basic`/`adj_factor`/`suspend_d` 都是整年分区，且 `load_shortlist_cross_section` 在**截面自身的时刻**上通过 `read_if_ready` 读它们 —— 于是真实面板上**可筛的交易日永远只有最新那一个**，更早的任何时刻一律 `not_yet_knowable`。`V2-P4-061` 把 `daily`/`daily_basic`/`stk_limit` 换成按时刻可见的会话读，正是为了让它自己写下的「两天的榜可比较、昨天的可重跑、已发布的榜可事后审计」成立；在真实面板上这三条**仍然不成立**，墙只是从价格面挪到了旁边三个面。(b) `namechange` 按公告日整体取，而 `built_panel` 用 `datetime.now(UTC)` 建它，跨过 Asia/Shanghai 午夜的夜间构建会取到**次日生效**的更名行；此时 `shortlist run` 在「`namechange` 尚不可知」与「`daily` 该交易日尚未发布」两条拒绝之间**没有任何时刻可站**，整条出货面完全不可达。补救是既有的 `--as-of`：`panel build --dataset namechange --as-of <可筛时刻>` 由 `ColumnarPanelBatch` 的可见性检查剔掉那行（389→388 行），同一条 `shortlist run` 随即出十只。`_refuse_split_horizon` 看不到这类不一致，因为 `namechange` 不在按会话取的目标里 | e2e：真实语料上从 `panel build` 走到 `shortlist run` 并出榜或具名拒绝 | T9 |
| `V2-P4-073` | ~~**`V2-P4-071` 的 drop guard 只覆盖 merge 的一半；observation 面丢失一整个已存横截面会静默 exit 0**~~ **已修复** | 技 | 071 | **P4 第四轮验收（2026-08-19）实测**。`appended_to_the_stored_year` 的 docstring 与其 commit 都把这句当作整个设计的安全论证：*"a `retain` rule with a hole in it -- one that mis-reads `build_column`, or that drops a build it meant to keep -- produces exactly the refusal it produced before, naming the builds that went missing."* 验收在 `retain` 首行加一句把洞限定在 observation 面（`if build_column != SUBJECT_COLUMN_NAME: return False`），第二次调用**写入成功、exit 0、无任何提示**，而 manifest 分区里两个 build 都在、observation 分区里 day one 的 8 行横截面**已消失**；损失只在下一次读时才由 `_refuse_rows_that_are_not_the_answers_their_manifest_addresses`（读侧）发现。**根因**：`write_factor_panels` 只对 `kind == FACTOR_MANIFEST_KIND` 跑 guard，而 `_refuse_to_drop_a_stored_build` 的 docstring 以「a write carrying every stored build carries every stored security **by construction**」为 observation 分区免于单独 guard 辩护 —— 那在 `071` 之前成立（到达的 batch 就是整个分区、两数据集出自同一 `panels` 序列），**`071` 之后两个分区各自由独立的 `appended_to_the_stored_year` 调用组装，这条蕴含断了**。**注意范围**：出厂的 `retain` 是正确的，验收用多组探针验证了真实行为；本条是关于 guard 与 claim，不是关于代码。**并且这是同一个缺口的另一面**：`panel_ingest.py` 新进入 `test_query_callers.py` allowlist 时，写侧兜底援引的正是这句 —— 验收判定其**读**的论证成立（实测追加 day 2 后 day 1 答案逐字节不变、无前视泄漏），但**写**的替代性保障只覆盖一半，两者应一并修 | 单元：observation 面丢失一个 build 的 merge 必须按名拒绝于写侧 | S30 |
| `V2-P4-074` | ~~**`V2-P4-061` 新增两个 `read_visible_at` 调用者，而那份存在意义就是审查调用者的 allowlist 未更新**~~ **已修复** | 测 | 061 | **P4 第四轮验收实测**。`_read_visible_price_session` 的 docstring 亲自指名该文件：*"The objection `tests/unit/panel/test_visible_read_callers.py` makes every new caller of `read_visible_at` answer is: can this caller tell a withheld row from an absent one"*，而该文件在 `d109109` 仍写着「`V2-P4-026` moved one of those two, **and only one**」「It has **two** call sites, one per dataset」「The first is `_read_visible_price_session`, reached only from `load_daily_valuations`」—— 三句全部失真，现在是 `load_daily_bars`/`load_daily_valuations`/`load_price_limits` 三个 loader。allowlist 以**模块**为粒度（`FILTERED_READ_CALLERS` 里是 `panel_ingest.py`），故在已放行模块内新增调用者不触发它 —— 而这正是该文件宣称要制造的「a deliberate act with a review attached」。同一提交里 `panel/catalog.py` 的平行叙述**被更新了**，故是遗漏而非有意划界。性质本身实测为真（三个数据集各 10 个会话、每会话 1 个 `available_time`、违例 0），故 Minor。**已按验收行的第一条补救落地，但本行对「粒度」的说法被实测证否，且这正是修法的形状**：`V2-P4-061` 加的**不是两个 `read_visible_at` 调用点** —— `panel_ingest.py` 自 `V2-P4-027` 起一直是**恰好两个**调用点、现在仍是两个；它加的是既有私有 helper `_read_visible_price_session` 的两个**到达者**（`load_daily_bars`、`load_price_limits`）。所以按**语法调用点**做粒度的 allowlist **在 `V2-P4-061` 上同样会保持沉默**，那会是同一个缺陷下沉一层。故新表 `FILTERED_READ_REACHERS` 按**函数**记名，闭包沿模块内调用传递，`FILTERED_READ_CALLERS` 由它派生（两表不可能再互相走样）。**跑起来还查出本行未提到的第三个未经审查的到达者**：`load_statement_histories`（`V2-P4-083`）—— 同一提交里 `panel/catalog.py` 的平行叙述更新了、这里没有，与本行诊断的成因完全相同。**三个到达者本身都是正当的**，各自 docstring 里都带着自己的实测依据（三个价格数据集同为 `daily_close`、逐会话全有或全无；四个报表数据集为 `announcement`、逐事件日普查精确对账且已带显式 `answerable_through`）—— 缺的是审查而不是论证，它们的答案现在写进本文件。附带修正三句失真行文与「两个调用点、一数据集一个」的说法（现为两个调用点、**八个 loader**、跨十一个数据集）。**实测该门确实会响**：往 `panel_ingest.py` 里加一个新到达者，测试红并逐名点出它；探针已撤 | 单元：allowlist 粒度到调用点，或行文与实际调用者一致 | S27 |
| `V2-P4-075` | ~~**`V2-P4-049`：failed 与 interrupted 的运行也能解析，并清掉 1.0 门槛**~~ **已修复** | 技 | 049 | **P4 第四轮验收实测**：存入 `RunManifest(status="failed")` 与 `status="interrupted")`，用其 `run_manifest_id` 为全部入围名字造证据、`--min-researched-ratio 1.0` —— 两者均 `exit 0, researched_ratio=1.0, unresolvable=[], is_blocked=False`。`stored_run_manifest_ids` 的**字面** claim（"every `run_manifest_id` this deployment holds a run for"）精确成立 —— 失败的运行确实被持有；被夸大的是闸门自己的拒绝文案：`researched_ratio` 被称作 "a fact about **which runs finished**"。紧贴已披露残余 `a_resolved_run_manifest_is_not_a_resolved_signal`，故 Minor —— **`V2-P4-075` 实测定案（2026-08-25）**：经 CLI 逐字复现（failed / interrupted / succeeded 三臂同一 store、同一命令行、只换证据文件），修复前 `[0, 0, 0]`、修复后 `[1, 1, 0]`。**取的是较强那一支而非改文案**：`domain/run.py` 新增 `RunStatus` 别名与 `FINISHED_RUN_STATUSES`（只有 `succeeded`；两个 frozen 历史 shape 仍逐字写出，不共享别名，published schema 逐字节不变），`stored_run_manifest_ids` 改为返回 `StoredRunAddresses(held, finished)`，证据 join 认 `finished`。**量词落在地址而非行上**：`status` 在 `RUN_MANIFEST_UNADDRESSED_FIELDS` 里，故「中断后重跑成功」是同一份声明同一个地址。**两种不解析分开上报**：`evidence_without_a_stored_run`（没人跑过）与新增 `evidence_from_an_unfinished_run`（跑过但没跑完），补救不同；终端相应两行而非一行。顺带实测到一条本仓未写下的事实：`runs` 以 `run_id` 为主键且 `run_id` 进地址，`append_run` 拒绝同 `run_id` 第二行，故经 `SQLiteRunRepository` 一个地址至多一行 | 集成：`tests/integration/test_shortlist_workflow.py::test_a_run_that_did_not_finish_does_not_resolve_the_evidence_filed_under_it`（三臂 + 终端面文案） | S48 |
| `V2-P4-076` | ~~**`V2-P4-061` 在真实面板上不成立：墙搬到了与价格并排读的另外三个面**~~ **已修复** | 产 | 061 | **`V2-P4-072` 的 e2e 覆盖一落地就实测到，这正是补它的理由**。`load_shortlist_cross_section` 在横截面自己的时刻上读注册簿、复权、停牌与更名，**四者全部走 `read_if_ready`**（整分区就绪门）。而 `adj_factor` 与 `suspend_d` 是**整年分区**，其最新行就是最新会话的，故它们的可得时刻**就是那个会话的收盘**。实测：`stock_basic` 2026-08-19T00:00+08，`adj_factor`/`suspend_d` 2026-08-19T16:30+08。**后果**：`V2-P4-061` 把 `daily`/`daily_basic`/`stk_limit` 挪到 as-of 敏感的会话读上，是为了让「两天的榜无法比较、昨天的榜无法重跑、已发布的榜无法事后复核」这三句停止为真 —— **在真实面板上三句依然全部为真**，只有最新的已存会话可筛。**合成 fixture 看不到这个形状**（没有「整年分区且最新行落在最新会话」），所以四轮离线验收全部漏过它。**注意 `V2-P4-061` 本身没有做错**：它改的三个数据集确实按会话共享可得时刻、确实修好了；未覆盖的是并排读的另外四个。修法需要逐数据集判断：`adj_factor`/`suspend_d`/`namechange` 的时钟形状与 `daily` 不同，不可照搬 —— `V2-P4-027` 已经为 `index_member_all` 证明过「照抄 `026` 的解法」是错的 | 集成：真实语料上，一个更早的已存会话必须可筛，且分不出的情形仍具名拒绝 | S27, S28 |
| `V2-P4-077` | ~~**通宵构建会留下一个完全无法作答的时刻**~~ **已修复**（成因与报单不同，见下） | 产 | P1 | **`V2-P4-072` 的 e2e 实测**：`namechange` 按公告日期、在构建的 `--as-of` 上**整体抓取**，而 `built_panel` 传的是 `datetime.now(UTC)` —— 19:01Z 启动时上海已是次日。语料因此带进一条 2026-08-20 的更名，而 `daily` 停在 2026-08-19，**上下两条拒绝之间没有缝隙**：往下 `namechange … not_yet_knowable`，往上 `daily cannot be read for 2026-08-20 … that session had not published yet`。**下面那半在 `33d52e6` 上已经复现不出来了，这是 `V2-P4-076` 的功劳**：`load_name_histories` 改走按事件日过滤的读之后，在语料自己 `max_available_time`（2026-01-15T16:00Z）**之下**的两个时刻都正常作答 8 条历史，整分区那道门已经不在了。**上面那半复现，而且比报单更宽**：楔子不是 `panel build` 的 `--as-of`，是 `factor build` 的。`_pricing_session` 把横截面时刻解析成**它自己那个上海日历日**，而一个会话的行情 16:30 才发布 —— 于是从零点到 16:30 之间建的每一个横截面，都会去问一个还没发布的会话；又因为时刻是**存在横截面上**的，这个拒绝是永久的。实测：一个盖 2026-01-15T16:30Z（上海周五 00:30）的横截面，从构建之前扫到四天之后的每一个 `as_of` 全部 exit 1，两条拒绝之间没有任何缝隙；上海时间上午九点建的那个一样。**修法**：定价会话改为「该时刻上最新那个**已发布**的会话」（`panel_ingest.newest_published_session`，与 `_read_visible_price_session` 用同一个 `_sessions_published_through`）。它**永远不晚于**原规则，且只在原规则会点到一个未发布会话时才与之不同 —— 前视那道闸一步没松，仍可从 `panel doctor --session` 直接问到 | 集成：跨日构建后必须存在至少一个可作答时刻，或具名指出该做什么 | S14 |
| `V2-P4-078` | ~~**`namechange` 是 `shortlist run` 的硬性依赖且完全未文档化**~~ **已修复** | 产 | 033 | **`V2-P4-072` 的 e2e 实测**：`shortlist run` 经 `_bars_on → NameHistory.risk_warning_on` 判 `is_st`，故**必须**有 `namechange`；而 `factor build --tier raw` 不需要它，`BUILD_TARGETS` 也不抓它。README 的候选榜示例未提，缺分区时的拒绝语也不点名补救命令。这是 `V2-P4-067`(b) 从另一侧测到的同一件事。**修法**：`shortlist_view.SHORTLIST_PANEL_DATASETS` 把这个面读的**六个**数据集映到写它们的**五个** `panel build` 目标（`daily`/`suspend_d` → `price`，因为 `--dataset daily` 本身就是被具名拒绝的）；面板一个分区都没有时，`_read` 在拒绝语尾部追加 `Build it first: \`openalpha panel build --dataset <target> --year <year>\``，本地串与 `disclosable` 都带；`shortlist run --help`、README 与 `docs/api/http.md` 在用户碰到拒绝**之前**就把这五条命令列全。**`adj_factor` 实测不在其列**：省掉它之后 `factor build --tier raw` 与 `shortlist run` 都照常作答，把它写进清单等于让人白跑一次以小时计的构建。补救语**只在「这个面板一个该数据集的分区都没有」时出现**，这是有意的界：`stock_basic` 按生命周期年分区，「请求的那一年缺席」是健康登记簿的常态，按年触发会经常给出错误的建议 | 文档 + 集成：`shortlist run` 的依赖清单完整，缺分区的拒绝点名补救命令 | S48 |
| `V2-P4-079` | ~~**`adj_factor` 具备同一个筑墙形状，但它不在出榜面的读里 —— 挡的是 `factor_view` 与 `panel_doctor`**~~ **已实测定案：墙是真的，但 `076` 那扇门不适配这套语料，故以测量与记录结案、未搬门** | 技 | 076 | **验收驱动 `panel doctor`（`factor build` 实测不读 `adj_factor`：`panel_factors` 明写「不走 `adj_factor` 路径」，故 `factor_view` 侧的读点是 `run_factor_experiment` 的 `_PanelInputs`，不是 `build_factor_panels`）**。**墙可达，且实测到位**：在 `generate_panel(shapes=HEALTHY_SHAPES)` 上站在 2026-01-09T20:00+08、问 **2026-01-06**（三天前就已发布的会话），八个 cross-check 里 `close_agreement` 正常跑（`V2-P4-061` 的会话门），而 `unpriced_explained` 与 `return_paths` **双双 SKIPPED**，理由是本分区的 `['not_yet_knowable']` —— 因为库里已经推进到 2026-01-16。报告能谈那个会话的收盘、不能谈它的未定价名字是否停牌，而原因与那个会话毫无关系。**但 `_read_visible_event_dated_rows` 是错的门，原因是 `compress_adjustment_batch`**：时钟合（`daily_close`，与 `suspend_d` 同），语料不合 —— 那扇门上的每个数据集都是「一事件一行」，这个数据集存的是**阶梯函数**（年首锚点、每个变点、年末锚点）。实测：写入 64 行、压缩后存 18 行，八只证券里**六只只剩两行**（2026-01-05 与 2026-01-16）；在较早时刻做行级过滤，六只只看得见**一行**，八只的 `covered_through` 全部从 2026-01-16 掉到 **2026-01-05**，搬门要答的每一个问题都变成 `AdjustmentHorizonError`。**且对账无法补救**：`PartitionCoverage.dates` 的条目只有 `event_date` 与 `row_count`、**没有 subject 轴**，而「这只证券的序列真的结束了」与「它的尾巴被扣住了」是**逐证券**的问题（`KNOWN_ADJUSTMENT_LIMITATIONS.suspension_is_invisible`：`000024.SZ` 末根 K 线 2015-12-07、因子序列到 2015-12-29）。**被阻依赖（两处，均不在本次可改文件内）**：`domain/adjustment.py` 需给 `AdjustmentHistory` 一个不等于 `max(observed_on)` 的可答边界（即 `statement_histories_from_panel_rows` 的 `answerable_through` 挪一个数据集），`panel/catalog.py` 需给 `PartitionCoverage.dates` 一条 subject 轴来定这个边界 | 集成：`tests/integration/panel/test_whole_partition_doors.py` 两半都钉住 —— 墙可达、行谓词不可替 | S27, S28 |
| `V2-P4-080` | ~~**`NameHistory.risk_warning_on` 的调用在所有 `_read` 守卫之外**~~ **已修复** | 产 | 070 | **P4 第五轮验收（2026-08-19）实测**，与 `V2-P4-070` 刚关掉的是同一类，只是在隔壁那次读上。`shortlist_view.py:1533` 与 `factor_view.py:1045` 都直接 `history.risk_warning_on(session)` 判 `is_st`，而 `domain/name_history.py:272-287` 的 `record_on` 在 `day < first_effective_date` 时抛 `NameHistoryHorizonError` —— **该调用不在任何 `_read` 内**，故 `_REGISTRY_FAULTS`/`_PANEL_FAULTS` 看不见它。CLI `exit 5`「raised an unhandled NameHistoryHorizonError … message is withheld」，REST **500 text/plain**。**并非奇异语料**：`load_name_histories` 按年取，故任何「在计价会话之前公告、之后生效」的更名 —— 本仓专门建模的两时钟更名 —— 都令 `first_effective_date > session`。**三处已发布句子因此为假**，其中 `docs/api/http.md:496` 的「Nor should anything in the store produce one, and until `V2-P4-070` something did」**写下一个提交后即被证否**；而 `NameHistoryHorizonError` 自己的 docstring 说它「不是调用方错误…`V2-P1-013` 的闸门会因它阻塞」—— **它本就被设计成判定，两个产品面把它洗成了内部错误**。**fixture 盲点**：`tests/panel_fixtures.py::_name_records`（约 2162 行）给**每一只**证券一条 `LISTED_ON = 2026-01-02` 生效的基线记录，早于窗口首个会话三天；真实 `namechange` 没有这种合成的「上市名」行，**就这一条无条件记录让该拒绝在每个生成面板上都不可达**。修法照 `070` 给 `StockUniverseError` 的处理：调用点具名拒绝（exit 1 / 409 `panel_unreadable`）点名证券与语料 —— 静默默认 `is_st=False` 正是 `record_on` 拒绝去做的事 | 集成：良构 `namechange` 上的两时钟更名必须给具名拒绝而非 5/500 | S48 |
| `V2-P4-081` | ~~**`test_query_callers.py` 的空洞替换在一半上更强、在另一半上被一次顺带调用击穿**~~ **已修复** | 测 | 076 | **P4 第五轮验收实测**。`V2-P4-076` 把「至少十个 `read_if_ready` 调用点」换成 function→door 映射加不动点检查。**精确映射那一半确实不可漂移**（`_gated_reads(ingest) == GATED_READERS`），但不动点是**朝调用方加宽**的（`_plain_calls(node) & reaching`），故一个新函数只要调用了任意受门加载器就被放行，**无论它对 `query()` 做什么**。裸探针 `load_probe_mutant_a` 直接 `store.query` → **RED**（如声称）；而 `load_probe_mutant_b` 先 `load_trading_calendar(...)` 再 `store.query` —— **12 passed，全单元套件 2387 passed**。真实加载器正是 mutant B 的形状（它们都要日历）。次要：`_gated_reads` 与不动点都只走 `ast.FunctionDef`，`async def load_*` 对两者皆不可见（今日无此函数） | 单元：调用受门加载器不得为未受门的 `query()` 背书 | S5 |
| `V2-P4-082` | ~~**逐事件日对账的「逐事件日」性无人守卫；`V2-P4-034` 的语料是被 look-ahead 分支抓住的**~~ **已修复** | 测 | 034, 076 | **P4 第五轮验收实测**。`_refuse_a_slice_the_census_disagrees_with`（`panel_ingest.py:4019`）有两个分支。把 `V2-P4-034` 的求和缺陷**只施加在第二个分支**（保留 look-ahead 分支完好）：`tests/unit` 加 `tests/integration/panel` **3,251 条全绿**（19:40），其中包括 `test_a_compensating_pair_of_census_errors_is_refused_rather_than_cancelling_out` 本身。原因：`034` 的语料第二行日期晚于 `census_day` 且可见，被 `ahead` 分支抓住；而所有短缺测试都只用一个日期、计数 1 对 0，求和比较同样能抓。**性质在代码里仍然成立**（逐日比较严格更强），但**把它与求和分开的语料无处存在**：需要两个 ≤ `census_day` 的日期在相反方向上不一致。`V2-P4-076` 把这个变异列在「八个全红」里，在此形式下它是绿的 | 单元：两个 ≤ census_day 的日期反向不一致必须被拒绝 | S27 |
| `V2-P4-083` | ~~**另有四扇整分区门无人记名，且两个受门读点在 `src/` 里没有任何调用方**~~ **已处理**（一扇搬门、两扇以测量结案、无调用方那个补上了调用方） | 技 | 076 | **`load_statement_histories` 搬门**：唯一的 `src/` 读点是 `panel_doctor._ambiguity_check`，实测在 2026-01-09T20:00+08 被 `income cannot be read … ['not_yet_knowable']` SKIPPED，为的是一条 2026-01-12 公告、没人问过的报表。`ClockStrategy.announcement` 令 `event_time == available_time ==` 自身 `ann_date` 的午夜，逐事件日对账因此精确，边界取 `_knowable_through_the_same_day`；分区不压缩、且本读点本就带显式 `answerable_through`，故短读是**答得窄**而非答得错 —— 这正是 `adj_factor` 不具备的性质。**`load_industry_trees` 以测量结案**：`index_classify` 是 `calendar_static` on `taxonomy_date`，而 `industry_trees_from_panel_rows` 会拒绝与 `INDUSTRY_TAXONOMY_EFFECTIVE_FROM` 不符的 `taxonomy_date`，故一个 vintage 分区里**每一行的可得时刻都相同**（实测：存储的 `available_time` 列去重后为 1 个值），`not_yet_knowable` 因此是全有全无 —— 只可能意味着「你问在这套分类存在之前」，永远不可能意味着「面板往前走了」，**没有可以被移除的拒绝**。这是 `trade_cal` 那条论证从另一侧到达。**`load_index_membership` 以测量结案**：`src/` 里确无任何调用方（AST 断言钉住，将来有人来取时这条会红）。**`load_index_prices` 补上调用方**：它此前在 `src/` 与 `tests/` 里都无人调用，却占着 `GATED_READERS` 一行 —— 一个永远不可能失败的守卫条目。理由 `panel_ingest._refuse_unrebuildable_index_prices` 早就写好了：`compute_factor` 直接读分区的 `close`/`pre_close`、从不重建，故一个重复会话会把两个市场收益放到市场只有一个的地方、改掉那个窗口里每一次回归的样本量；而那个守卫只在**写**时跑，写在它之前的分区永不复检 —— 这恰是 `panel_doctor._rebuild_check` 的本职（「我们即将判为可读的分区，读得动吗」）。现为该 check 的第六个数据集。**顺带查出并修掉一个可达缺陷**：`_requirement_for` 把 `index_daily` 派给日历型 builder，而无日历（或日历够不到该年）时回落的 `_PRICE_SHAPED_FIELDS` **没有 `index_daily` 这一行** —— `openalpha panel doctor --dataset index_daily --no-calendar` 直接抛裸 `KeyError`，与 `panel_health_report` 自己「除未声明 cadence 外一切皆成 finding」的承诺相悖。两张表现已互为闭包并有断言。**与报单不符的两处**：(a) 报单称 `load_industry_trees` 在较早时刻给 `not_yet_knowable`，但同一行又说它无法由任何生成面板驱动 —— 在真实 tree 分区上实测它是全有全无，那条 NYK 不是「面板往前走了」那一类；(b) `write_generated_panel` 存 14 个数据集是对的，但缺的是**两个**（`index_classify` 与 `index_daily`）而非一个。**实测确认、本轮未改的一处**：`balancesheet`/`cashflow`/`fina_indicator` 三个 endpoint 的公告日确实全部停在 `BASE_ANNOUNCEMENT`（2026-01-05），没有任何形状能把它们推到最新会话上 —— 即 `V2-P4-076` 那类「整年分区最新行落在最新会话」的盲点在这三个数据集上仍然成立。搬门之后 statements 的读已按事件日过滤，故这道墙对读点已不再立着；留着的是**分区级 readiness** 仍会给 `not_yet_knowable` 这半，以及一个第五形状的位置 | 技：`test_whole_partition_doors.py`、`test_industry_ingest.py`、`test_panel_doctor_rules.py` | S27 |
| `V2-P4-084` | ~~**同一个缺陷隔一个接缝：`_PanelInputs.label` 只捕 `LabelError`**~~ **已修复**（四处接缝中三处修复，第四处实测不可达） | 产 | 080 | **`V2-P4-080` 修复时扫描发现，未在该轮修**。`factor_view._PanelInputs.label` 用 `except LabelError` **单独**包裹 `label_outcome`，而 `StockUniverseError`/`AdjustmentError`/`PriceDataError` 经 import 验证**全部是普通 `ValueError`** —— 与 `080` 同类：一个被设计成判定的领域错误，在守卫之外抛出，会被洗成 exit 5 / 500。另有 `shortlist_view.py:1249` **裸调** `registry.listed_on(session)`，而 `factor_view.py:2717` 对同一个调用**是有守卫的**。**为什么此前不可达**：`tests/panel_fixtures.py` 的 `_universe_batch` 给每只证券一行上市记录且与其它构造器共用同一个 `SECURITIES` 元组，故**没有任何生成面板能带出「有因子行、无 `stock_basic` 行」的证券**；`_factor_batch` 是完全叉积、无 `omit`。这正是 `V2-P4-080` 记下的那条推广的又一个实例 —— fixture 让数据集比真实语料更规整 | 集成：三个同侪错误各自必须给具名拒绝而非 5/500 | S48 |
| `V2-P4-085` | ~~**另有四处 fixture 构造器让数据集比真实语料更规整（仅缺口，未遮活缺陷）**~~ **已修（四处全修）** | 测 | 080 | 四处各得一个形状，每个都**丢行或替换语料**而非追加 —— `V2-P4-080` 的教训，且每个都带真实语料的实测出处：`industry.first_assignment_after_the_window`（**替换** `securities[4]` 的语料，令其首个 assignment 晚于每个计价会话；实测 `600841.SH` 1994-03-11 上市、直到 2022-07-29 才有分类，隔 6,903 个会话，而 `920038.BJ` 干脆一条都没有）；`price_limits.bar_without_a_published_band`（**丢掉**一个 `(证券, 会话)` 的涨跌停行而 K 线仍在；实测 `daily` 是 `stk_limit` 的子集只从 2023-01-03 起成立，2022-12-26 有 4 根 K 线无对应带；`panel_doctor.SUBJECT_CONTAINMENTS` 声明的正是这条包含关系）；`financials.security_has_filed_nothing`（**丢掉** `securities[5]` 在 `income` 上的全部行，另三个 endpoint 保留 —— 实测四个 endpoint 对同一只证券覆盖的年份并不相同，`cashflow` 要到 2003-12-31 才开始）；`index.constituent_absent_from_the_registry`（把末位成分**替换**为 `990018.SH` —— 实测它出现在 000300.SH 的十八次发布里，却不在 `stock_basic` 的 L 与 D 任一半，`constituent_listing_report` 正为此而存在；替换而非追加，否则与「三格 33.33」那个形状叠加时权重会变成 133.32 被写入器直接拒绝）。**四个全部实测为 healthy（`provokes=()`）**，与 `V2-P4-084` 那两个必须进 `DEFECT_SHAPES` 的不同：语料有洞不等于面板不健康。**第一稿被自家测试证否一次**：`industry.first_assignment_after_the_window` 原取 `RECLASSIFIED_FROM`（2026-02-02），那晚于 `AS_OF`，于是它变成 `industry.reclassification_after_the_as_of` 换了个名字 —— `test_no_detector_answers_true_on_a_shape_that_is_not_its_own` 抓住了，改为 2026-01-17（晚于每个会话、早于 `AS_OF` 十二小时）。**一处连带修正**：`financials.announced_after_the_as_of` 的 `provokes` 从 `("not_yet_knowable", "check_unavailable")` 减为 `("not_yet_knowable",)` —— 那个 warning 是 `083` 搬门前 `statement_ambiguity` 被整分区拒绝掉的产物，它消失是修复而非回归 | 测试：`test_panel_fixtures.py`（35 个形状）与 `test_panel_shape_coverage.py`（27 healthy）双向对真实报告 | — |
| `V2-P4-086` | ~~**复权语料的墙需要一条 subject 轴才能移动；两条受阻依赖各有确切编辑**~~ **编辑 (a) 已交付；编辑 (b) 由实测确认确属必需，而本行「普查那一半」的理由被测量证伪** | 技 | 079 | **`V2-P4-079` 结案时实测得出，且实施者先写了移动、跑过、再据实测撤回**。`compress_adjustment_batch` 存的是**阶跃函数**：写入 64 行存下 18 行，八只证券中六只恰好保留两行（2026-01-05 与 2026-01-16）。在较早时刻加行谓词会让**六只只剩一行**，并把 `covered_through` 从 2026-01-16 拉回 2026-01-05 —— **这次移动想回答的每个问题都变成 `AdjustmentHorizonError`**。**且不能靠普查修**：`PartitionCoverage.dates` 只带 `event_date`/`row_count`，**没有 subject 轴**，而「序列结束」与「尾部被扣住」是**逐证券**的区别（`suspension_is_invisible`）。**两条受阻依赖，编辑已确切给出**：(a) `domain/adjustment.py` —— `adjustment_histories_from_panel_rows(rows, *, answerable_through: date \| None = None)` 与 `build_adjustment_history(..., answerable_through=...)`，`covered_through` 在给定时返回它（照 `statement_histories_from_panel_rows` 的既有形状）；(b) `src/openalpha_cn/panel/**` —— `PartitionCoverage.dates` 需要一条 subject 轴，否则该地平线无法逐证券判定。**墙本身可达且已实测**：`panel doctor` 在一个三天前发布的会话上，`unpriced_explained` 与 `return_paths` 死掉而 `close_agreement` 仍在跑 —— **`V2-P4-086` 实测定案（2026-08-25）**。**编辑 (a) 已交付**：`domain/adjustment.py` 的 `build_adjustment_history(..., answerable_through=...)` 与 `adjustment_histories_from_panel_rows(rows, *, answerable_through=...)` 已按 `statement_histories_from_panel_rows` 的既有形状落地，`covered_through` 在给定时返回它，另新增 `observed_through`（最新一条真实观测）—— 两者分开正是要点；并反向拒绝一个落在已持有行之后的地平线。**门被真的搬过、跑过、再据实测撤回**，且 `V2-P4-079` 的两半理由只有一半站得住：**(1) 普查那一半被证伪** —— `adj_factor` 是 `ClockStrategy.daily_close`，行的可得性是其自身 event date 的函数，`_read_visible_event_dated_rows` 的逐 event-date 对账在压缩语料上**逐字通过**（无需任何 subject 轴），`panel doctor` 在 `EARLIER_AS_OF` 上 `unpriced_explained` 与 `return_paths` 由 SKIPPED 变为真跑，库面与 CLI 两面都驱动过。**(2) 逐证券那一半成立，且现在可由出货面复现** —— 装上读级地平线后，`tests/integration/panel/test_panel_shape_coverage.py` 的 `adjustment.factor_series_stops_inside_the_window` 由 `['return_path_disagreement']` 变为 `[]`：一只**真的在窗口内结束**的序列不再被拒，而是被一个它自己从未覆盖过的窗口上的旧因子作答 —— 正是 `AdjustmentHistory` 上界与 `suspension_is_invisible` 要防的 fail-open。**「前沿规则」也检验过并否掉**（只放宽最新可见 event date 上的证券）：阶跃函数上「自开盘锚点以来没动过」的证券同样落在前沿之后，会被当成已结束而拒掉，即在另一方向失败。故本行未搬门，编辑 (b) 按 `V2-P4-094` 修正后的形状留下（逐 subject 一条 `last_event_date`，基数 = `PartitionCoverage.subjects`）。**另留一条此前无人写下的观察**：逐 subject 的 `last_event_date` 在 `as_of` 上读，回答的是「该证券在分区后段是否还有行」，这本身是 `as_of` 之后的事实；在 `as_of` 上真正可知的是「该证券是否仍在册」，而这由 `StockUniverse` 已能回答、此 loader 从不查 —— 可能是更便宜的那扇门，且与本行点名的两条编辑都不是同一条 | **已交付**（单元）：`tests/unit/domain/test_adjustment.py` 三个测试逐字驱动 `answerable_through` —— 两个地平线分开、越界文案同时点两个日期、反向拒绝、逐 security 承载；（集成，本行的实测锚）：`tests/integration/panel/test_whole_partition_doors.py::test_a_horizon_the_read_declares_cannot_carry_the_per_security_half` 在 defect shape 自己的 store 上钉住「读级地平线把已结束的序列往前带」，故编辑 (b) 的必要性是测量而非散文。**未做**：搬门本身与「较早时刻的 `panel doctor` 不给 SKIPPED」，理由已实测（见左） | S27, S28 |
| `V2-P4-087` | ~~**`panel doctor --dataset index_daily --no-calendar` 抛裸 `KeyError`**~~ **已修（本轮补上产品面）** | 产 | 083 | **`V2-P4-083` 给 `load_index_prices` 接调用方时挖出**：`_PRICE_SHAPED_FIELDS` 没有 `index_daily` 行，故该命令抛裸 `KeyError`，违背 `panel_health_report` 自己写下的承诺。已在同一提交内修复（两张表都成为模块常量并由相等性钉住），本行仅作记录以便追溯 —— **它是接线一个「无调用方的受门读者」时才暴露的，即那条守卫条目此前不可能失败这件事本身就在遮蔽缺陷**。**本轮复验**：`be262ea` 上两张表确已由 `test_every_calendar_scoped_requirement_has_a_census_free_fallback` 相等钉住，缺陷不复现，`panel_doctor.py` 本轮**一行未改**。**但本行的实测是一条命令行，而当时立的断言直接调 `panel_health_report`** —— `test_the_report_survives_being_asked_about_index_levels_with_no_calendar` 是库调用，`_panel_request` 的分发与 `_panel_command` 的信封在它之下全无断言，而本仓复现率最高的根因正是「绿的单测 + 没有绿的产品路径」。故补 `test_the_shipped_command_survives_being_asked_about_index_levels_with_no_calendar`，逐字驱 `openalpha panel doctor --dataset index_daily --year 2026 --no-calendar --json`。**该测试能分辨，且是量出来的**：临时删掉 `_PRICE_SHAPED_FIELDS` 的 `index_daily` 行后它红（exit **5**），装回后绿（exit **1** —— 空 store 本来就不健康，而这正是那个 fallback 存在的理由：对一份有麻烦的面板继续给出判决，而不是抛异常） | 单元：两张表由相等性钉住；**集成：那条命令行本身必须被驱动** | S48 |
| `V2-P4-088` | ~~**`run_daily` 在所有守卫之外算预测的结果窗口；一个可达的日历条件在三个面上被洗成内部错误**~~ **已修复** | 产 | 017, 021 | **模型链验收（2026-08-21）实测**，与已修的 `080`/`084` 同类，但在 `017`/`021` 引入的**新接缝**上。`model_view._LabelInputs.window` 在**训练**侧包住 `build_label_window` 并把日历地平线故障转成具名 `blocked` 加补救；**预测**侧同样的计算跑在 `predictions.put → prediction_record_for → outcome_known_at_for → build_label_window` 里，而 `run_daily` 在其唯一 `try` 块**关闭之后**才调 `put`。实测 `put` 抛 `CalendarHorizonError`（mro 含 `ValueError`），REST 路由只捕 `ModelViewError`/`PredictionStoreError`、CLI 只有 `_panel_command` 兜底 → **exit 5 / 裸 500**，而 `MODEL_HTTP_STATUS` 的 `internal_error` 行 docstring 写着本模块不抛它。**可达性**：`daily_request` 强制 `predict_at` 的日期**严格晚于** `end`，故预测日总是晚于每个训练日 —— 受守卫的那条路对它**永远不会触发**，不受守卫的那条**总是**看到伸得最远的窗口。条件是预测日的结果窗口越过所加载日历的最后一个会话，即任何年键分区最后 `horizon.sessions + 1` 个会话，**也就是一次常规的年末 daily run**，或任何 `trade_cal` 未向前构建的运行时。`CalendarHorizonError` 自己的 docstring 写着它「不是调用方错误」，而两个产品面把它当成了 。**修法在接缝而非只在调用点**：`_OUTCOME_WINDOW_FAULTS` 与 `_outcome_window_refusal` 各只一份，训练侧的 `_LabelInputs.window` 与 `run_daily` 交给存储的那一步共用，故两条路一句话一个判定；守在**调用**上而非先自行推一遍窗口再比对——后者是同一日历的第二次读取，两次读法哪天不再一致，守卫就会不声不响地不再守。补救语句改为点名 `openalpha panel build --dataset trade_cal --year <次年>`。**语料**：`panel_fixtures.generate_panel` 新增 `window`，批次的抓取时刻与面板 `as_of` 随最后一个会话推导（`_index_weight_batch` 早有此规则，此次扩到另外五个构造器），默认窗口逐位不变；`tests/integration/test_year_end_daily_run.py` 以整年 2026（259 个会话）驱动三个面。**修前实测**：REST `500 text/plain`，SDK 裸抛 `CalendarHorizonError`。**一个残留且已钉住的不可达臂**：`LabelError` 只在 zone 西于 `MINIMUM_LABEL_ZONE_OFFSET` 时抛出，而两处调用都传常量 `MODEL_DATE_ZONE`（+08:00），故变异体存活；照 `V2-P4-084` 的先例保留守卫并把理由钉在测试里 | 集成：年末 daily run 给具名 `blocked` 与 `panel build --dataset trade_cal --year <next>` | S48 |
| `V2-P4-089` | ~~**`test_model_view.py` 关掉进程级 logging，六条既有守卫变空转；CI 仅因收集顺序而绿**~~ **已修复**（并扫出同类第三处：证明污染的那条测试自己只还原它点名的那一个 logger） | 测 | 021, 068 | **模型链验收实测，1.9 秒复现**。`tests/unit/test_model_view.py:22` 直接 import **raw** 的 `importlinter.cli.lint_imports` 并别名为 `_lint_imports` —— **那正是 `test_import_layering.py:96` 那个安全包装器的名字**，故该文件读起来像被包装过；第 378、392 行未经包装地调用它，而 `lint_imports` 跑 `dictConfig` 且默认 `disable_existing_loggers=True`。实测 `pytest tests/unit/test_model_view.py tests/integration/storage/test_migrations.py tests/integration/test_batch_research.py -q` → **6 failed**（全为 `assert 0 == 1`）；换成 integration 在前 → **52 passed**。全量扫 `tests/unit tests/integration tests/contract` → **6 failed / 4582 passed**。**而 `test_import_layering.py` 包装器的 docstring 逐字记录了这次失败、点名同样那两条 migration 测试、并写明「CI 之所以绿只是因为 pytest 默认把 integration 收集在 unit 之前」** —— 这是 `V2-P4-068` 的根因被在隔壁文件重新引入。修法：两个调用点都走既有包装器 。**实测出四个文件 import 原始 CLI 而非两个**，其中 `test_candidate_ranking.py` 与 `test_shortlist_gate.py` 各抄了一份包装器与一条只扫自己源文件的正则守卫。**并扫出同类第三处，就在守卫自己身上**：`test_running_the_import_linter_leaves_an_existing_logger_enabled` 调用原始 CLI 后只手工还原它点名的那一个 logger，在 `test_model_view.py` 已改走容器之后实测（故与该文件无关，且这条代码路径与 `fadf72d` 逐字相同）`pytest tests/unit/test_import_layering.py tests/integration/storage/test_migrations.py tests/integration/test_batch_research.py -q` → **4 failed / 54 passed**；即本单六条里有四条**另有一个成因**，只按原单改法会仍红。**结构性修法**：`tests/import_linter_containment.py` 成为全树唯一一处 `importlinter.cli` import，两个导出函数都还原整份快照（不再有把原始 CLI 交出去的出口，因而也不需要「谁可以裸调」的名单）；三条按调用拼写扫单文件的正则守卫换成一条按 **import** 扫整棵 `tests/` 树的 AST 普查——别名躲不过 import，`rglob` 躲不过作用域，而这正是前两次失守的两个盲点 | 单元：任意子集顺序下六条日志守卫都为真 | — |
| `V2-P4-090` | ~~**`V2-P4-013` 的「乱序切分不可表达」是假的：出货的 `walk_forward_folds` 会切出一个散布且泄漏的切分**~~ **已修复**（另测出第二条同类旁路并一并关闭） | 技 | 013 | **模型链验收实测**。该声明所依赖的是 `test_a_fold_carries_no_field_naming_which_rows_train`，一条 `dataclasses.fields(WalkForwardFold)` 断言 —— **它约束的是声明，不是调用方能拼出来的东西**。真正使推导安全的不变量（section 按预测日严格递增）只活在 `labelled_panel()` 工厂里，而 `LabelledPanel` 与 `PanelSection` 是**导出的 frozen dataclass 且无 `__post_init__`**，故直接构造、`dataclasses.replace`（本仓测试里到处在用）或将来任何反序列化都能绕过。探针把一个早期日移到元组末尾再交给**出货的** `walk_forward_folds`：**接受**，返回 2 个 fold，其中 fold 1 的 test block 散布在三周内且 `leaked_sessions` 报出 **6 个**训练标签与 test 标签共读的会话 —— **报出它的正是模块自己的独立测量**。**今日非生产泄漏**：`src/` 里唯一的 `LabelledPanel` 构造在 `model_view.py:1541` 且走工厂。**已按建议修**：排序检查进 `LabelledPanel.__post_init__`，工厂那份删掉（不留第二处会漂移的拷贝）。**实施中另测出第二条旁路，同属「不变量在工厂而不在类型上」**：把 fold 0 首个 section 的 `as_of` 前移一年、预测日不动 —— 日序仍严格递增、照样被接受，purge removed **0 of 48**、`leaked_sessions` 报 5 个。故 `PanelSection.__post_init__` 一并声明「一个预测日就是一个时刻」：examples 非空、`as_of == cross_section.as_of`、每行都是这一天的、行与行在 zone/exchange/horizon 上一致、且 `as_of` 在**标签自己的时区**里解析恰为 `prediction_day`（purge 的整个前提）。`LabelledPanel.__post_init__` 收下六条：至少一个 section、预测日严格递增、一个特征表、一个交易所（含字段本身）、一个时区、一个 horizon；工厂只留 `LabelledPanel` 看不见的那部分 —— 被**提供**却从未成为 example 的标签。**声明收窄**：registry code 由 `an_unordered_split_is_unrepresentable_and_a_badly_placed_block_is_only_refused` 改名为 `train_membership_is_unrepresentable_and_the_order_behind_it_is_only_refused`，detail 重写而非追加；模块 docstring、`WalkForwardFold`/`LabelledPanel`/`PanelSection`/`labelled_panel` 四处 docstring、`test_a_fold_carries_no_field_naming_which_rows_train` 的 docstring 与本表 `V2-P4-013` 行同步收窄。**变异 15 个全杀**（11 条守卫逐条停用 + 4 处语义削弱），其中一个幸存者暴露的是**语料缺陷**而非设计问题：把 `_prediction_day_of` 换成 `as_of.date()` 全套单元照绿，因为本语料每个时刻都写在上海时区 —— 补了一个 UTC 表述、上海 07:00（UTC 前一日）的横截面，该变异体随即被杀 | 单元：绕过工厂构造的乱序面板必须被拒绝 | D12 |
| `V2-P4-091` | ~~**三个面不持同一判定：混合类型的重复超参在 REST 上给 500**~~ **已修复** | 产 | 021, 046 | **模型链验收实测**，十七个坏输入逐个跑过三个面，**十六个一致**（`ModelRequestError` / exit 3 / 422），**一个分岔**：`ModelRunApiRequest.declared_hyperparameters` 排序整个 `(name, value)` 元组，而 `cli._model_hyperparameters` 只按 name 排；两个同名不同类型的超参让 REST 的排序去比 `1 < "a"` → `TypeError`，且它在**参数求值期**抛出、落在路由的 `except (ModelViewError, PredictionStoreError)` **之外** → **500 `text/plain "Internal Server Error"`**，不是 `{"detail": {...}}` 信封，故按 `isinstance(detail, dict)` 分支的客户端什么都拿不到。`/daily-run` 同样。**一个调用方错误被报成服务故障**：5xx 会呼叫运维并触发重试。修法一行：按 `item.name` 排序，即 CLI 已经在用的规则 。**修法不是那一行**：排序规则移入 `model_view.declared_hyperparameters`，CLI 与 HTTP 两面共用同一次调用，因为出事的根子是「一条规则被写了两遍」——HTTP 那份的注释还写着「`cli._model_hyperparameters`' rule」，而它并不是那条规则。`tests/integration/test_model_interfaces.py` 新增一条对 `/evaluate` 与 `/daily-run` 各驱动 CLI/REST/SDK 三面的测试，同一字面输入必须给同一判定 | 集成：三面对同一字面输入给同一判定 | S83, S84 |
| `V2-P4-092` | ~~**`KNOWN_BASELINE_LIMITATIONS` 自相矛盾，且假的那半被实测为假**~~ **已修复**（审计盲区实测无廉价解，已在读者会碰到的地方写明） | 测 | 014 | **模型链验收实测**。`a_minority_leak_moves_this_baselines_coefficient_and_not_the_order_it_produces` 说泄漏与已 purge 的 fold「both read exactly `-1.0`」；`every_number_this_module_has_produced_was_measured_on_a_leak_fixture` 却说那些 `+1.0` 与 `-1.0` 的 mean rank IC「**that separate** a leaked fold from a purged one」。实测两份语料的全部四种配置：`mean_rank_ic` **每次都是 -1.0**，泄漏只体现在系数上（`-0.333` 对 `-1.0`）。**第一条对，第二条把 concordance 数（1.0/0.0）与 rank IC 混为一谈。**注册表审计只检查每个 **code** 出现在测试代码里，**看不见一个假的 `detail`** —— 这是该审计的已知边界第一次产生实际后果。**已修**：第二条 detail 重写而非追加（照 `V2-P4-016` 发现两条为假时的做法），第一条补一句点明两者曾互相矛盾、实测它是真的那半；新增 `test_no_configuration_of_either_corpus_lets_a_rank_ic_separate_a_leak_from_a_purge` 驱动**全部四种配置**（红的那一步先按注册表的说法断言「两者不等」，得到 `assert -1.0 != -1.0`）。**审计盲区：实测没有廉价解，故写明而非硬修**。最便宜的候选是「detail 里的每个小数必须是测试代码求值过的数」，两个方向同时失败：那条假 detail 的两个小数是 `-1.0` 与 `1.0`，测试代码**都**求值过 —— 规则会被它正为之而设的那句话满足；而 61 条带小数的条目里有 **38** 条至少含一个测试从不求值的数（`0.14%`、`3/7` 对 `3/8`、`795.78` 等散文里的实测记录）。结论写进 `tests/unit/test_known_limitation_registries.py` 的模块 docstring，读者在那里会碰到它 | 单元：`detail` 里的可测量断言被驱动 | — |
| `V2-P4-093` | ~~**四处更小的账：`Prediction.score` 漏了 `_unsign_zero`、`supersedes` 从任何面都不可达、两处计数与一处守卫归属失真**~~ **已修复**（(b) 定案为契约专用并具名记入注册表） | 技 | 016, 017, 021 | **模型链验收实测**。(a) `AlphaModelArtifact.parameters` 与 `AlphaModelDeclaration.hyperparameters` 都规范化带符号零，**`Prediction.score` 没有** —— `-0.0` 会进转储载荷，故 `record_id` 可能因一个非差异而移动（今日无实现产生该对，但这是唯一破例的地方，而 `sign * (value - centre)` 确实能产生 `-0.0`）。(b) `017` 的 `supersedes` 世系**从每个面都不可达** —— `run_daily` 调 `put` 时不传它，CLI/REST/SDK 均无对应入口，故 `put` 里那条 `V2-P4-049` 式的指涉检查在出货代码里**永不触发**。(c) `feature_matrix._PANEL_FAULTS` 说「the six loaders this module calls」，**实为五个**。(d) **`lint-imports` 单独并不能挡住一个新的 `backtest/*.py`**：只含 `import numpy` 的探针模块给 `8 kept, 0 broken` —— `numpy` 与 `openalpha_cn.storage` 刻意不在整包契约里、只在两条枚举契约里；真正会响的是 pytest 断言 `test_the_two_backtest_study_contracts_cover_every_module_in_the_package`。该声明对 CI 流水线为真，**对孤立的 `lint-imports` 为假**。**四处已修**：(a) `Prediction.score` 加 `field_validator` 走 `_unsign_zero`，两条测试分别钉住转储载荷与 `record_id`（红：`assert '-0.0' not in ...` 命中载荷）。**并且本行「今日无实现产生该对」被实测证否**：参考模型 `predict` 是 `sign * (float(value) - centre)`，`fit` 在下半组实现更高均值目标时学到 `sign = -1.0`，而 `-1.0 * 0.0` 就是 `-0.0` —— 任何一只申报特征恰落在学得中心上的证券，**出货的 `predict`** 就会交出 `-0.0`，由 `test_a_security_on_the_learned_centre_under_a_negative_sign_scores_positive_zero` 驱动（既有的同位测试只覆盖 `sign = +1`，那里乘积是 `+0.0`、无从看见，缺口正由此存活）。真实面板上浮点恰好命中训练均值是巧合而非必然，故「稀有」的判断没错，错的是**来源**：这是模型自己的算术，不是手搓的载荷；(b) **不暴露，定案为契约专用**：三个面都无处安放「被更正的是哪一条」，唯一诚实的来源是从更早一次运行读回的 `record_id` —— 那是 `held_prediction` 的地址、不是 daily run 的输入；`KNOWN_PREDICTION_RECORD_LIMITATIONS` 新增 `the_supersedes_edge_is_contract_only_because_no_face_offers_a_record_to_name`（台账 294 → **295**，registries 仍 32），并由 `test_the_supersedes_lineage_is_contract_only_and_no_shipped_face_can_supply_one` 按 AST 钉住调用点，有人接线即变红（两个变异体实测全杀）；(c) 「六个」改「五个」，且数目由 AST 数出的 loader 集合与 docstring 里的数词**互相钉住**，不再是散文；(d) 新增 `test_lint_imports_alone_does_not_stop_a_new_backtest_module_reaching_numpy_or_a_store` —— 只含 `import numpy` 与 `openalpha_cn.storage` 的探针给 `8 kept, 0 broken`，随后 pytest 断言按 `does not cover` 变红；`backtest/{cross_section,alpha_model,shortlist_gate,candidate_ranking}.py`、`domain/alpha_model.py` 与 `tests/unit/test_import_layering.py` 六处强式措辞同步收窄 | 单元：四处各自补上 | — |
| `V2-P4-094` | ~~**`--as-of` 只有一个可达取值；`--help` 里印的两个示例逐字跑都失败；回溯评估不可能**~~ **已部分修复**：两个示例现可运行且有测试逐字驱动、消息不再把最大值说成 `first became available`；**整分区门未搬**，理由已实测（见下） | 产 | 021, 079 | **模型面产品验收（2026-08-21）实测**，合成市场经 CLI 唯一声明的注入缝注入、其上全部真跑。`model evaluate` 与 `daily-run` 的 `--help` 示例**逐字执行均 exit 1**：`adj_factor holds information that first became available at 2026-01-30T08:30Z, after the requested as_of 2026-01-20T04:00Z`。扫描确认 `--as-of` 的可达集是「最新已建会话的 16:30 或之后」一个点（`03-31T08:29` 拒、`08:30` 过）；一个完全落在一月内的 schedule 在二月的 `--as-of` 上同样被拒。根在 `panel/catalog.py:1553` 的 `max_available = max(coverage.max_available_time ...)` —— **整分区作用域**，一行发布晚于 `as_of` 就挡住整个数据集。**这正是 `V2-P4-079` 结案时说「留给一次驱动 `factor_view`/`panel_doctor` 路径的验收」的那堵墙**，其产品后果现已实测：(a) **回溯评估不可能**，`--as-of` 被钉在最新会话上，而**每次 `panel build` 都会作废昨天的复现**；(b) 承载全部 PIT 承诺的必填参数**只有一个合法值、须由用户二分法发现**。附带：消息把**最大值**描述成 "first became available"，使用户读成「整个数据集是新的」而实际只有一行是 **`V2-P4-094` 实测定案（2026-08-21）**：墙是真的，门没搬，理由是量出来的。在 `generate_panel(window=(2026-01-05, 2026-03-20))` 的 55 个会话上重现：`--as-of 2026-03-20T08:29:59Z` 拒、`08:30:00Z` 过，二月的 `--as-of` 对一个完全落在一月内的 schedule 同样拒 —— **可达集确实是一个点**。**并且上界也是墙，此前无人写下**：`daily_requirement` 要求 `--as-of` 之前的每个会话都**在库**，故晚于最新已建会话的 `--as-of` 得 `date_gap`（十会话面板上 `2026-01-20T04:00Z` 缺 1 天、`2027-01-01` 缺 249 天）；`--as-of` 省略时取墙钟，因此在任何不是「建到今天」的面板上都落在区间外。**把 `adj_factor` 换成 `read_visible_at`（不做 `answerable_through` 修补）从产品面精确重现了 `V2-P4-079` 的塌陷**：`000001.SZ's outcome over 2026-01-07..2026-01-08 could not be priced … 2026-01-08 is after 000001.SZ's last adjustment factor, observed 2026-01-05`。**`V2-P4-086` 的第二条编辑按其字面不可负担，且形状被改正**：`DateCoverage` 为所有数据集共用，给它加 subject 轴等于每**行**一条普查条目 —— 整市场 `daily` 一年 244 x 5,545 = 1,352,980 行，普查由 244 条变 1,352,980 条，而 `_read_coverage` 在**每次就绪判定**都物化普查：实测单条读取 0.0001 s → 0.29 s，而一次 `model daily-run` 仅 `daily` 就读 98 次（八证券 fixture 上共 476 次物化、32,681 条）。该地平线真正要问的是**逐证券**而非逐（证券, 日期）：一条 `last_event_date` per subject，基数等于已存的 `PartitionCoverage.subjects`（~5,545 行而非 135 万），仍需迁移与 `record_coverage` 交叉校验，故本行未取、按实测转记。**已交付**：`panel/catalog.py` 的 `not_yet_knowable` 消息改写 —— 明说该时刻是分区内的**最大**可知时刻而非数据集的首次可得、明说判定按分区而非按行、并点名**能读到它的最早 `as_of`**（此前该数字已在消息里但被写成故障而非边界，验收正是因此用二分法找可达集）；两条 `--help` 示例改为 `--as-of 2027-01-01T00:00:00+08:00` 并写明「读一年意味着站在它之后」与上界的 `date_gap`；`model evaluate` 的示例另有**与 `--as-of` 无关的第三个缺陷** —— `--horizon 5d` 在 `2026-01-06..2026-01-14` 的七个预测日上会把第一折的训练集 purge 到空、`walk_forward_folds` 直接拒掉该 schedule（本仓自己的语料早已记下这条理由），改为 `1d` | 集成：`tests/integration/test_model_help_examples.py` 从两条 docstring **解析出**命令行并逐字执行（只替换 `./runtime` 与交易所），单元：`not_yet_knowable` 的措辞。**未做**：历史 `as_of` 上的可复现评估 —— 需 `V2-P4-086` 的两条编辑，理由已实测 | S27, S28 |
| `V2-P4-095` | ~~**当天 `daily-run` 必须把 `--end` 往回拉 `horizon + 1` 个会话，而没有任何东西说这件事**~~ **已修复** | 产 | 021 | **模型面产品验收实测**（面板建到当天、预测当天、17:30 本地）：`--end 2026-03-13` → exit 1 `the price bars for 2026-03-23 could not be read …: that session had not published yet`；`--end 2026-03-12` → exit 0、`standing=forward`、48 个训练日 2880 个样本。墙恰好是 horizon：`1d` 需 2 个会话间隔、`5d` 需 6、`20d` 需 21。**与命令自己的契约矛盾** —— 它写着「训练集是每一条结果窗口在 `--predict-at` 时已收盘的样本；未收盘的不会被交给拟合」，**purge 就是为此规定的，而标注读在 purge 运行之前拒绝了整个区间**。代价只是猜：`--end 2026-03-13` 本会 purge 回到同样的 48 天。三个面一致（exit 1 / 409 / `ModelPanelUnreadableError`），**无任何消息、开关或 limitations 条目点名补救**。S32 自己的工作流对最直观的调用方式不可达 **已修复**：`run_daily` 在**标注之前**丢掉结果窗口尚未收盘的横截面，判据是 `trainable_at` 自己的那条不等式（提取为 `_outcome_had_closed`，一条规则两个调用点，照 `_OUTCOME_WINDOW_FAULTS` 的做法），所以没有任何东西再去问面板要它没有的价格。**在十会话语料上重现并钉住**：`--end 2026-01-15`（预测日的前一个会话）此前 exit 1 `the price bars for 2026-01-19 could not be read`，现 exit 0，且与 `--end 2026-01-14` 产出**同一个 `artifact_id` 与同一个 `record_id`** —— 用户失去的确实只有猜。**`V2-P4-088` 的那条不动**：日历根本放不下的窗口仍是具名拒绝加 `panel build --dataset trade_cal --year <次年>`，因为「日历把结果定在截止之后」与「日历定不了」是两件事，后者被静默丢弃就成了把缺分区变成悄悄变短的训练区间。**自证伪一条**：先写了第二个测试断言「purge 之后不再移除任何东西」，实测该性质**在任何面上都不可观测**（更松的过滤器报出的 `day_count` 逐字相同），且其中的算式（天数 x 证券数）本身是错的（语料是 54 条而非 56，两只无结果），故删除，两个方向改由那一条并排测试钉住。`--help` 与 `the_daily_fit_purges_and_does_not_embargo` 各加一句点名此事 | 集成：`--end` 只差一个会话的两次运行必须给出同一个制品 | S32 |
| `V2-P4-096` | ~~**被中断的写入被报成「命令有缺陷」；索引与可提供内容静默不一致**~~ **已修复**（列表半边定案为不改，代价已实测） | 产 | 017, 021 | **模型面产品验收实测**，把已存预测截断一半（断电 / 磁盘满）：CLI **exit 5**「did not finish: it raised an unhandled JSONDecodeError… The exception's own message is withheld」、REST **500 text/plain**、SDK **未封装的 `JSONDecodeError`**。**这是 `V2-P4-080`/`084`/`088` 那一类的第四个实例**，出现在 Story S32 唯一依赖的那个 store 上。**对照极其锋利**：一份**被编辑过**的文档处理得完美（exit 1、精确消息、点名重新推导出的地址）—— **store 检查地址却不检查解析**。更糟：`openalpha model predictions` 与 `GET /api/v1/predictions` **仍把该记录列为持有**，故注册表的索引与它能提供的东西静默不一致 **已修复，且修在接缝而非第四个调用点**。**先实测这一类到底有多大**：`read_versioned` 是本包每个反序列化 store 的唯一入口，而**四种文档打到三种异常**，三种在修前**全部**是 exit 5 / 裸 500 / 未封装 —— 截断一半 `JSONDecodeError`、新版本 `schema_version` 与「是数组不是对象」`UnknownSchemaVersionError`、改了一个字段类型 `pydantic.ValidationError`。所以 `except json.JSONDecodeError` 只盖住四分之一，而那正是本行被报上来的那一个。**修法**：`domain/versioning.py` 新增 `STORED_DOCUMENT_FAULTS`，在 `read_versioned` 自己身边具名一次（`_OUTCOME_WINDOW_FAULTS` 的形状）；`FilePredictionStore.get` 在**已经重新推导地址的那一处**转成 `PredictionStoreError`，于是三个面上早已存在的那条 arm 接住它 —— CLI exit 1、REST 404 `not_held`、SDK 具名异常。**一个 `except` 盖住两个读者**：`put` 经由 `get` 读，故「同一条预测再登记一次」这条路（此前从 `put` 里裸抛 `JSONDecodeError`）一并具名，且**拒绝而不覆盖** —— 「只往没有东西的地方写」是本 store 唯一的保证。消息点名记录、点名该删的文件（本 store 无 `delete`）。**`IdentityRewriteRequiredError` 是一条今日不可达的臂**（本注册表只有一个版本、没有会拒绝的升级），照 `V2-P4-084` 的先例保留并把理由钉在测试里。**本 store docstring 有一句被本次实测证否并改写**：原写 *「`PredictionRecordError` 与 pydantic 的 `ValidationError` … propagate unchanged」* —— 那句对 `put`（拿活对象构造）为真，对 `get`（对本 store 自己磁盘上的字节）为假，而它描述的正是本缺陷。**「不出现在列表里」这半边定案为不做，代价已实测**：`list_ids` 是目录扫描，要它排除读不出来的文档就得解析每一份 —— 本模块 docstring 实测整市场每份 3.6 ms，一个模型一年的日跑是 0.9 s、五个模型五年是 22 s，而这条命令存在的理由就是当便宜的那一半；并且那样会**藏起**受损文档，恰与拿着坏盘的运维需要相反。改为：`get` 具名（记录、诊断、该删的文件都在句子里），并把 `list_ids` docstring 里那句它从未强制过的承诺（*「a store that returned one of those would hand a caller a key `get` then refuses」*）收窄到它真正管的东西——名字 | 集成：三种文档 x 三个面给具名拒绝；再登记同一条预测同样具名（列表半边见左） | S48 |
| `V2-P4-097` | ~~**单特征声明下报出的统计量与拟合数学无关，而唯一会响应的那个数是终端面唯一不打印的**~~ **已修复（终端印系数 + 逐次运行自述不变性）** | 产 | 014, 021 | **模型面产品验收实测**：`--embargo-sessions` 从 0 扫到 15，训练集 780 → 2640 个样本，`mean_rank_ic` **到小数点后十二位完全相同**；`openalpha model evaluate --embargo-sessions 0` 与 `--embargo-sessions 10` 的**终端输出逐字节相同**。成因：`CrossSectionalRankModel` 打分 `c·rank(x)`，而秩相关对正单调变换不变 —— **单特征时只有拟合的符号能到达统计量**。用户却被展示 `training_example_count`、`training_cutoff` 与逐 fold 的 `artifact_id`，一整套 purge 过的 walk-forward 仪式，**盖在一个它们都动不了的 headline 上**。双特征时会动，在第三位小数。**唯一会响应的是系数**（`folds[].parameters`，该扫描下 +0.180 → +0.212），而 `evaluation_rows` **完全省略它** —— 终端只显示 block / coverage / `mean_rank_ic` / `rank_icir` / reach。`V2-P4-014` 实测泄漏体现在**系数**上，**默认界面正是唯一看不到它的地方**。九条 `limitations` 无一提及单特征不变性。**已修，两处而非一处**：(1) `evaluation_rows` 增第六列 `fit`，`_fit` 按 **artifact 自己的 `feature_ids`** 取参数而非按 family 分支（`MODEL_FAMILIES` 存在的理由照搬到渲染上）—— 秩基线印 `reversal_1d/v1@raw=-0.9107`，树集成印 `40 parameter(s), none on a declared column` 而**不截断**；实测该夹具两折系数为 -0.9107 与 -0.9464，而旧的五列**逐字节相同**。第一稿还有第三臂（列系数 + `+n not on a declared column`），因两个出厂 family 都到不了、变异扫不出对错而**删除**并把理由钉在 docstring 里。(2) 不变性**同时**是声明与逐次自述，因为这是两种不同的陈述：注册表第十条 `a_rank_statistic_sees_only_the_ordering_this_fit_induces` 对 family 为真，而新键 `evaluation_view["invariances"]`（`blocks` 的形状，空列表是承重情形）对**这一次运行**为真，把本次的列数渲染进句子而非描述它 —— 反证由树 family 提供：单列 boosted 集成是该列的阶跃函数而非单调变换，实测 `mean_rank_ic` 0.9274 对秩基线同折的 0.9107，故其 `invariances == []`。**一条自证否**：原打算按列数分支写复数（`'' if count == 1 else 's'`），该分支在本仓夹具上只能驱动一侧（两列需 100+ 只证券的面板），遂改写成 `column(s)` 把分支**删掉**而不是留一个变异体扫不动的臂 | 集成：终端面呈现能反映拟合差异的量；单特征不变性被声明 | S29 |
| `V2-P4-098` | ~~**注册表答不出「我先承诺了哪一个」，且存储的记录说不出模型是什么**~~ **已修（三处，其中一处认定原判断有误）** | 产 | 017, 021 | **模型面产品验收实测**。(a) `openalpha model predictions` 与 REST 列表**只给地址、按内容哈希排序** —— 无日期、无模型名、无 standing、无 horizon；实测五条记录里**最后创建的排第一、最先创建的排第三**，而用户的既定需求正是「事后证明我先说了」，**排序与时间不相关且具误导性**。(b) 更根本：存储的记录带 `model_name`、`artifact_id`、分数，**不带特征、训练区间、代码版本、模型训练时的 `as_of`**，且没有任何面能解析 `mdl_…` 或 `run_…`（`openalpha model` 恰好四条命令）。**一条一年后被读到的记录会说「reversal-rank 预测了这 60 个数字」，而它说不出 reversal-rank 是什么。** (c) 一条 `forward` 记录可以由一个在**结果已可知之后**才读面板的模型产生（`training.as_of` 2026-04-01 对 `outcome_known_at` 2026-03-30，standing 仍为 `forward`），而那个读取时刻**不在存储文档的任何字段里** —— 诚实恰好止步于记录所携带的边界。**(a) 已修**：`model_view.held_predictions` 读出全部记录并按 **`recorded_at`（地址破平）** 排序，三个面全部改走它 —— 按托管戳而非 `predicted_at`，理由与 `standing` 同一条：`predicted_at` 是调用方交给 `predict` 的、本仓查不了，托管戳是调用方唯一设不了的那个；一个按被排序对象自选字段排序的注册表就是同意被告知。**store 的 `list_ids` 一行未动**（`V2-P4-096` 的兄弟单持有该文件）：目录按名排序对**归档系统**是对的，错的是**注册表的索引**拿它当时间序，两个问题在 `held_predictions` 分开。夹具上五条记录实测「第三条排第一、第二条排最末」，与实测形状同类。代价是每条一次 `get`（含重新推导地址，市场宽度 3.5 ms），列表是稀疏读而错误的顺序是永久的。**(b) 原判断有误，已按实测改写**：记录**带得动**模型是什么 —— `PredictionRecord.batch.artifact` 按值携带整份 `AlphaModelArtifact`（family、`feature_ids`、解析出的 `feature_version`、`code_commit`、seed、超参、训练截止、样本数、系数），是 `prediction_view` **把它全丢了**只渲染 `model_name` 与 `artifact_id`。新增 `model` 键渲染全部，于是 `mdl_…` 无需任何面去解析 —— 它是用来**比对**两次拟合的地址，不是查表键。真正缺的是训练区间与读面板的时刻。**(c) 决定：不进记录，进披露与两个持有两数的面**。前两条理由：那个 `as_of` 与 `predicted_at` 同属调用方给定值，放进文档会被读成「拟合看得到什么」的界而没有任何东西核过它 —— 正是 `V2-P4-017` 拒绝的「看着像证明的字段」；加它要把 `alpha-prediction-record` 推到 v2 并走一次**拒绝式**迁移，移动每一个已落库地址。**第三条是本单自证否的一处**：初稿写「在面上拦下矛盾要 `run_daily` 自己再推一遍 `outcome_known_at`，即同一日历的第二次读取」，**这是错的** —— `put` 交回的记录本身就带 `outcome_known_at`，写后比对不需要第二次读日历。真正的理由是**那个 block 判的是一句话而不是一次泄漏**：晚的 `as_of` 实际放进来的是今天的注册簿、日历与复权**形状**，那条已由 `the_evaluation_reads_its_labels_at_one_as_of_and_that_is_not_a_point_in_time_fit` 声明；结果本身由 `trainable_at` 的 purge 与「每个截面在自己的预测时刻上读」挡住，与 `--as-of` 无关。故一次落到这个形状的运行**可能是干净的**，为了防读者过度解读一个徽章而拒绝一个干净的答案是错的价钱。改为：注册表第十一条 `a_forward_standing_does_not_bound_the_instant_the_fit_read_the_panel` 带全部论证，`daily_view.training.as_of` 与终端面新行 `panel read at` 紧挨 `outcome_known_at` 打印。夹具上把该形状复现出来了：2026-01-08 的预测其结果 2026-01-12T07:00Z 可知，时钟 2026-01-08T10:00Z 故 standing 为 `forward`，而每一次面板读取都在 2026-01-17T04:00Z —— 晚五天 | 集成：列表可按时间排序并带可读字段；记录可解析回它的声明 | S44, S49, S32 |
| `V2-P4-099` | ~~**三处更小的账：horizon 墙不点名 horizon、`supersedes` 的披露悬空、`unwitnessed` 实际不可达**~~ **已修（四处）** | 产 | 021 | **模型面产品验收实测**。(a) `--horizon 8d/10d/12d/20d/60d/250d` 全部给出**同一句**关于某个会话 16:30 发布规则的话；`5d` 能过、`--horizon 10d --end 2026-02-20` 也能过，**故那堵墙是 horizon 与 schedule 末端的联合函数而没有任何东西说这件事**。对照同仓那条堪称范本的 schedule 拒绝：「this panel's 54 prediction day(s) cannot carry the declared schedule of 40 fold(s) of 5 test day(s)」。(b) 截止后重跑正确产生 `backfill`，其渲染句写着「a backfill naming no earlier record corrects nothing」，而 `supersedes: null` **且任何面都没有开关能命名它** —— 本仓知道（`domain/prediction_record.py` 的 `the_supersedes_edge_is_contract_only_because_no_face_offers_a_record_to_name`），但那条**不在 `KNOWN_MODEL_VIEW_LIMITATIONS` 里**，故永远不会进入用户粘进报告的那份正文。(c) 三个面的 `predicted_at` 与 `recorded_at` **都由同一个时钟导出**（实测相等），故 `unwitnessed` 的窗口只有一次写入的时长 —— **standing 词表的三分之一描述了一个产品无法产生的状态**。(d) `evaluate` 的终端面**不带那九条 limitations 中的任何一条**（全部 `--json` 限定），而 `daily-run` 的终端面**会**打印 standing 双句，这个不对称看起来非有意。**四处全修**：(a) `_window_reach_refusal` 在标注读取的两个价格读之外包一层，把 `feature_matrix`/`panel_ingest` 的原句**逐字保留在前**（它点名会话、时刻与发布规则），后接一句点名**两个 flag**：`This run reached it because the 2d outcome window for the prediction day 2026-01-14 opens on 2026-01-15 and exits on 2026-01-19 -- the reach is the declared horizon and the last prediction day in the range together`，补救同时给出 `--horizon` 与 `--start/--end` 两条路。夹具实测：`2d` 与 `5d` 从此**给出不同的句子**，且第一个未发布会话在两者都是 2026-01-19（那是面板的事实），差在**预测日** —— `5d` 从区间的**第一天**就越界，`2d` 只在最后一天越界，故 `5d` 的读者会知道缩短区间救不了自己，而旧句子说不出这件事。`disclosable` 同步扩写（该从句无路径）。**与 `V2-P4-095` 是同一堵墙的两侧**，那条单归兄弟 agent。(b)(c) 各进注册表一条 —— `the_supersedes_edge_is_unreachable_from_every_face_this_module_serves` 与 `no_face_here_can_produce_an_unwitnessed_record_because_one_clock_stamps_both_instants`：本仓在 `domain/prediction_record.py` 早已知道这两件事，缺的是**进到用户粘进报告的那份正文**，故是把已知的话搬到能被读到的地方而非发现新事实；两条都不修行为，`unwitnessed` 不可折叠的论证照 `V2-P4-017` 保留。(d) `limitation_pointer()` 一行，`evaluate` 与 `daily-run` 两个终端面共用，报**注册表自己的长度**加取全文的 flag —— 数字是 `len(KNOWN_MODEL_VIEW_LIMITATIONS)` 而非手写，故加一条而忘了这行会红，而「见文档」永远不会红；终端不印十五段，那会把它们所解释的那张折表淹掉 | 文档 + 集成：四处各自补上 | S48 |
| `V2-P4-100` | ~~**模型链（`010`–`017`、`021`）在 e2e 上零覆盖**~~ **已补齐** | 测 | 072, 088, 093 | **2026-08-21 实测**：`V2-P4-072` 把出榜链补进 e2e 之后，模型链停在与它当初完全相同的位置 —— 九个 issue、一轮技术验收、**没有一条测试打真实数据**。`grep -rn "model\|prediction" tests/e2e/` 在 `8fd132d` 上返回 **9 条且全部是顺带命中**（`model_dump_json`、一个叫 `prediction_day` 的局部量、出榜模块自己的 `_prediction_instants`），**没有一条到达 `openalpha model`**。**已补齐**：`tests/e2e/test_model_chain_online.py` **15 条**，e2e 由 45 涨到 **60**，全部对真实语料跑 `panel build → factor build → model evaluate / daily-run → model prediction` —— 逐折统计（两折均 `measured`，各带 `mean_rank_ic` 与 `rank_icir`）、`daily-run` 落库并按 `record_id` 从 CLI / REST / SDK 三面取回同一份文档（整体相等，非逐字段）、同一交易日晚间的第二个时刻追加而第一份在第二次写入后仍逐字节存活（`071`）、真实预测拿到 **`standing=forward`**，外加四条具名拒绝。**同时实测到三条与仓库自述相悖的事实，应各自另开一行**：(a) **`factor build --subject` 不收窄 `model evaluate` 的打标范围** —— `feature_matrix.py` 自述「the rows are the universe」，截面行集是注册簿的挂牌集，故六十只的因子在 **33,090** 个证券-日里只得 **348** 个分数（**1.05%**），**任何有意义的 `--min-scored-ratio` 都不可达**；在出榜链上 `--subject` 真能按比例省下工作量，这里只省下了 build。(b) **`model daily-run --help` 声称「re-running an identical day is `unchanged` on both stores」，在该命令行面上不成立且不可能成立** —— `predicted_at` 进入记录的内容地址（`prediction_record.py` 自述 "`predicted_at` reaches the address"），而 CLI 从**本进程时钟**取它，故同一天每重跑一次就多落一份记录，定时任务在瞬时失败后重试会为一天留下两条。`unchanged` 只在注入固定时钟时可达，本模块用 SDK 跨面钉住了它：SDK **复现出命令行落的同一个地址**。(c) **`factor build --help` 说 staleness「state it or waive it ... there is no third option」，但两个选项里只有一个能用** —— `--waive-max-staleness` 被读 `daily` 的引擎逐字拒绝（"State a bound"）。**另有一条只有真实数据产得出的拒绝**：`daily` 的 `pre_close` 与 `adj_factor` 对同一次公司行动各执一词时，`session_returns` 拒绝**整条**运行而非那一只 —— 用仓库自己的 `pre_close_tolerance` 全年扫描，**151 个交易日中 5 个**各有 **1 只**（`300091.SZ`/`002012.SZ`/`688109.SH`/`689009.SH`/`603221.SH`）；合成夹具的收盘价与复权因子是一起生成的、按构造必然自洽，故四轮线下验收从未见过这个形状。本模块的窗口必须绕开这五天才拿得到统计量，并用「同样二十天、把结果窗口拉长一档」把同一条拒绝再打一次作为反向控制。**最后**：`V2-P4-088` 的跨年 horizon 拒绝在年中构建的真实面板上**不可达** —— `trade_cal` 存到年底、日历完全能给出结果窗口，先拦下的是价格面的 `date_gap`（`89 required date(s) are absent from daily`）；`088` 真正要保的那半仍然成立：exit 1 加一句可判定的话，不是 500、不是 traceback | e2e：真实语料上从 `panel build` 走到 `model evaluate`/`daily-run` 并给出逐折统计或具名拒绝 | T9, S32 |
| `V2-P4-101` | ~~**`V2-P4-030` 关闭词表却没给拒绝一条出路：`POST /api/v1/research/run` 对未声明的 flag 返回 500 `text/plain`**~~ **已修（具名异常 + 与 pydantic 同一方言的 422）** | 技 | 030 | **产品验收在 `d748796` 上实测**，evidence payload 形如 `{"schema", "family", "facts", "quality_flags"}`（前两个键缺失则该条 evidence 在到达这段代码前就被 `MarketAgent` 按 family 滤掉，断言会假绿）：`['future_data']` → `200`、`signal.risk_flags=['future_data']`；`['future-data']` 与 `['totally_made_up']` → **`500`，`content-type: text/plain`，正文 `Internal Server Error`**。`agents/baseline.py::_quality_flags` 的 docstring 自己点名了五条从进程外可达的路径，而**没有一条接住它**。这正是 `api/app.py` 模块 docstring 早已用散文写下的那个失败形状（"caught by Starlette, not by `_shortlist_refusal`, and the caller gets `text/plain` `Internal ...`"），落在另一条路由上。**不恢复 fail-open**：拒绝是对的，`V2-P4-030` 实测过拼错的 `future-data` 在治理阶梯上排在 `future_data` **之上**，把带 typo 的候选往上抬；错的只是投递方式。**修法：具名异常而非 `except ValueError`** —— 整条路由包一个 `except ValueError` 会把无关的算术/解析缺陷报成调用方的拼写错误，那是 `V2-P4-045` 在出榜面上记过一次的过宽捕获。`domain/risk_flag.py` 新增 `UndeclaredRiskFlagError(ValueError)`，带 `value`/`declared`/`evidence_id`/`flag_index`；**仍继承 `ValueError` 是承重的**（`LookAheadViolationError` 的先例），下一行说明为什么。`api/app.py::_undeclared_risk_flag_refusal` 把它封成 FastAPI 自己的字段错误 **list**（而非面板拒绝的 `{reason, message}` **对象** —— 本模块 docstring 记着 422 有两种正文且 `"detail" in body` 分不开它们），`msg` 用 `UndeclaredRiskFlagError.expected` 套 pydantic 的 `"Input should be ..."` 前缀、**按声明序**列全十个 flag，与 `POST /api/v1/research/deliberate` 上 pydantic 自己写的那句**逐字相等**。**`loc` 是调用方无法自行还原的那一半**：`_quality_flags` 重抛时带上 `evidence_id` 与位置，路由把前者映射回请求 `evidence` 数组里的下标，得到 `["body", "evidence", 1, "payload", "quality_flags", 1]`。跨 agent 边界传 `evidence_id` 而非下标，因为 agent 只看得到自己 family 的条目（`MarketAgent` 只留 `market_event`），在那里取下标会在任何混 family 的请求上点错条目 | 集成：`tests/integration/test_undeclared_risk_flag_surfaces.py` 用 `TestClient` 驱动 200 对照、两种拼错、非零下标的 `loc`、与 deliberate 面同方言 | S40 |
| `V2-P4-102` | ~~**同一条拒绝在另外两个面上同样无法投递：CLI 打 traceback、批处理只留 `error_type: "ValueError"`**~~ **已修（两处，其中一处认定验收报告有误）** | 产 | 101 | **同一次验收实测**。(a) `openalpha research run <evidence.json>` 渲染出 Typer 的富文本 Python 栈追踪并 exit 1 —— **消息本身是对的，呈现方式是栈追踪**；`create_app` 自己的 docstring 就是本仓这条家规的出处："naming the specific variable, never a bare traceback"。(b) `POST /api/v1/research/batches` 退化成 `{"status":"failed","error_type":"ValueError"}` —— **无消息、无 flag 名、无词表**，恰好丢掉 `parse_risk_flag` docstring 承诺的那个诊断（"a producer learns which flag it spelled wrong"）；`ValueError` 是 Python 里信息量最低的名字，一个五千条的整市场批次只学到「有一条失败了」。**(a) 已修**：`research run` 捕获 `UndeclaredRiskFlagError`（且只捕获它 —— 裸 `except ValueError` 会连 `parse_serialized_evidence` 自己的拒绝一起吞掉，对着一个 `content_hash` 被篡改的人打印 risk-flag 词表），把消息写到 **stderr** 并保持 **exit 1 不变**：本行说的是呈现，同时改退出码会让已经按 1 分支的 CI 因第二个无关原因变红。**(b) 已修，且没有动任何已落库契约**：`error_type` 现在记具体子类名而非基类（`ValueError` 从来不是这段代码的选择，只是当时那条拒绝恰好抛的基类），完整理由写进 `item_failed` 进度事件的 `detail` —— 那是一个早已为此存在的自由 `str \| None` 字段，且由 `GET /api/v1/research/batches/{batch_id}/events` 发布，故**无需给 `BatchTaskItem` 加字段、无需迁移**（AGENTS.md v2 硬规则 3：`extra="forbid"` 下加一个键就是破坏性变更）。**默认仍然只写类型名**：`runtime/batch.py` 新增 `DISCLOSABLE_ITEM_FAULTS` 白名单，理由与 `cli._panel_command` 同一条、且在一个 **append-only 且持久**的边界上更重 —— 一个未被预期的异常携带着它逃出的那个栈帧当时持有的一切（路径、查询、从环境读到的凭据），写进进度事件就收不回来。`UndeclaredRiskFlagError` 入选是因为它消息里的每一部分要么是调用方自己发来的字符串，要么是本 build 已发布的十个 flag（`docs/api/schemas/signal-frame-v1.json`）。**一处认定验收报告有误，已实测**：报告称被点名的五条路径「none of them catches it」，对 `POST /api/v1/backtests/replay` **为假** —— `ReplayRunner.run()` 早已逐 case 捕获 `(RuntimeError, ValueError)` 并记下 `f"{case.run_id}: {type(error).__name__}: {error}"`，实测返回 `200`、`succeeded=0`、`failures[0]` 完整携带拼错的字符串与全部十个 flag。它是这四个面里**唯一从未坏过**的那个，也正是另外三个现在照抄的模板；它能对，靠的就是新异常仍继承 `ValueError`，故那条对照测试作为回归守卫保留而非删除 | 集成：同一文件用 `CliRunner` 驱动 stderr 无 traceback、用 `TestClient` 驱动批次记录与事件 `detail`，并把 replay 面钉成对照 | S40 |
| `V2-P4-103` | ~~**`factor build --tier` 的选项帮助保留了 `V2-P4-028` 已经撤回的界，与同一份 `--help` 上两段之前的正文自相矛盾**~~ **已修（先实测哪一半为真）** | 文档 | 028 | **产品验收实测**：在面板自己的 horizon **之前**成功构建了 neutralized tier。`cli.py:4104-4105` 的 `_BUILD_TIER_HELP` 写着 *「`--tier neutralized` only succeeds at a prediction instant at or after the panel's own stored horizon」*，而**同一份 `--help` 输出里两段之上**的命令 docstring 写着相反的话：那条界「IS GONE, and with it the reason the neutralised tier was a year-end operation」，剩下的只有一个会话宽、且是算术而非政策 —— 残差必须携带 processed 面板自己的时刻，两处外部读取都按该时刻落在的那**一天**取，故只在该日 16:30 收盘之前、或交易所当日休市时才拒绝。**先测后改**：陈旧的是选项帮助那一半，代码是对的。`factor_view.py::_neutralized_panel` 已完全按后者实现，`V2-P4-026` 拿掉了 `daily_basic` 的整分区界、`V2-P4-028` 把 `index_member_all` 换成 `panel_ingest.load_industry_cross_section` 这扇按**天**取参数的门。本行在 `test_factor_build.py` 里**把散文和一次真实构建绑在同一个测试里**：先用 `CliRunner` 在 `2026-01-08`/`2026-01-09` 两个时刻写出 neutralized tier（该夹具的 horizon 是 `2026-01-16`，故 build 严格早于它，断言里显式 `max(BUILD_INSTANTS) < HORIZON_INSTANT` 免得夹具漂移后测试变空），再断言 `--help` 里 `stored horizon` 这个说法**以任何拼法都不再出现**、且换成了已被度量的那句。只 grep `--help` 的测试只能证明句子变了、证明不了句子为真，而本文件存在的理由正是「一句曾经为真的话不再为真而没有任何东西变红」。**一处把验收报告的数字改准**：报告说验收「eight sessions before the panel's horizon」，实测那是**日历天**（01-08 → 01-16）；按**会话**数是 4 与 5，与 `test_the_neutralised_tier_builds_at_the_mid_window_instants_it_used_to_refuse` 的自述一致 | 集成：`test_the_tier_option_help_states_the_bound_the_builder_actually_applies` —— 先在 horizon 前真构建，再断言 `--help` 两半一致 | S17 |
| `V2-P4-104` | ~~**`--min-securities` 的帮助说下限是 3、验证说 4，而拒绝语点名的是 pydantic 模型而不是那个 flag**~~ **已修（帮助改、拒绝语改，下限不动）** | 产 | — | **产品验收实测**：`--min-securities 3` 得到 `1 validation error for RedundancySpec / min_securities / Input should be greater than or equal to 4 [type=greater_than_equal, input_value=3] / For further information visit https://errors.pydantic.dev/2.13/v/greater_than_equal`，exit 3。四行里两个缺陷：帮助写着 *「the contract's own floor is 3」*，而这里根本没有「the contract」单数 —— `factor_view.factor_request` 把**同一个整数**同时交给 `FactorICSpec.min_securities`（下限 `MINIMUM_IC_SECURITIES = 3`）与 `RedundancySpec.min_securities`（下限 `MINIMUM_REDUNDANCY_SECURITIES = 4`），**一个 flag 喂两个研究、高的那个绑定**；而拒绝语点名的是一个调用方从未听说过的内部模型类，整句话里**没有 `--min-securities` 这个字符串**，也没有任何路径能从 `RedundancySpec` 回到那个选项。**先测后改：错的是帮助，不是验证。** 两个下限都是算术且都动不了 —— 三个点是 `\|r\| < 1` 首次可达的截面大小；而 `n = 3` 时三个秩的六种排列下未平局的秩相关只能取 `±0.5` 与 `±1`，故**任何 ≤ 0.5 的 `--redundancy-threshold` 都区分不出任何东西**，把冗余下限降到 3 会让 survival 行（验收判据读的那一行）把每一对都判成 redundant。**已修两处**：(a) `factor_request` 在构造两个 spec **之前**显式拒绝，消息点名 `--min-securities`、给出两个下限、并说明为什么高的那个绑定 —— 此前是「哪个 spec 先被构造哪个先报错」，实测 `2` 报 `FactorICSpec`、`3` 报 `RedundancySpec`，两条臂都不含那个选项名；因为写在共享的 resolver 里，`openalpha factor run` 与 `POST /api/v1/factors/run` 同时得到它。(b) `_FACTOR_MIN_SECURITIES_HELP` 改成从两个常量插值而非写死数字，故常量再动一次帮助不会掉队；**同一命令的 docstring 两段之上早已把 3 和 4 都写对了**，与 `V2-P4-103` 是同一形状的第二个实例 | 集成：`tests/integration/test_factor_min_securities_floor.py` —— CLI 与 REST 两面的拒绝语、`--help` 的两半一致、下限本身可用（exit 1 而非 3）作为边界对照 | S17 |
| `V2-P4-105` | ~~**离线守卫影的是 `socket.socket` 这个包装类，而四个被守方法一个都不属于它**~~ **已修** | 测 | 039 | **P4 技术验收实测**。`socket.socket` 是 Python 包装类，`connect`/`connect_ex`/`sendto`/`sendmsg` **全部继承自 C 基类 `_socket.socket` 且自己一个都不定义**，影名字影不到基类，而 `import _socket` 只有一行。验收在 autouse 守卫下、非 e2e 测试里、**仅打环回**走出三条路，本行在 `46253c4` 逐条复现：(a) `_socket.socket` 的 `connect`+`sendall` —— 监听端收到 `b'ESCAPED-TCP'`；(b) `_socket.socket.sendto` —— 返回 11，监听端收到 `b'ESCAPED-UDP'`；(c) 最利的一条，**不 import 任何新类**：拿一个**被守**socket 自己的 `detach()` 文件描述符重新包进 `_socket.socket`，投递 `b'ESCAPED-DETACH'`。`test_the_guarded_methods_are_the_whole_of_what_leaves_this_process` 断言在 `vars(socket.socket)` 上，**对整件事结构性失明**：那四个名字全是继承来的，类字典说不出 C 基类会做什么。本行首选的修法是把影扩到 `_socket.socket`，**实测做不到**：`setattr(_socket.socket, 'connect', ...)` 抛 `TypeError: cannot set 'connect' attribute of immutable type '_socket.socket'`，且 C 类的可达拼法太多（`import _socket`、`socket.socket.__bases__[0]`、`__mro__[1]`、`type(sock).__mro__[1]`），任何名字层面的安排都关不上。**故守卫下沉到类图之下而非横向铺开**：改为 PEP 578 审计钩子，守 `socket.connect`/`socket.sendto`/`socket.sendmsg` 三个事件 —— 事件在 `_socket` 自己的 C 实现里抛出，调用方拿到哪个类对象都逃不掉，三条逃逸用同样三行全部拒掉，收窄声明（改为「出站 TCP」）仍按 `039` 的理由拒绝。**三个而非四个是实测而非遗漏**：CPython 为 `connect_ex` 抛的也是 `socket.connect`，**没有 `socket.connect_ex` 事件**。**代价明写**：审计钩子装上就卸不掉，`_depth` 计数是开关，e2e 在钩子已装而失效的状态下跑；补偿是旧设计要还原的东西不复存在 —— `socket.socket` 从此**完全不被改动**，没有类字典要放回、没有 `delattr` 可以被变异悄悄跳过。**开销实测**：`tests/unit` 装钩子 33.58s、不装 35.49s，同机相隔数分钟，本套件分辨不出。**「这三个就是全部出站面」仍是论证并已可执行**：`send`/`sendall`/`sendfile` 不抛审计事件也不需要，三者都要求**已连接**的 socket，而受守族上唯一的连接途径会拒 —— `test_the_unaudited_sends_are_unreachable_because_connecting_is_what_is_refused` 在**C 类上**把它跑出来：connect 被拒、`sendall` 以未连接的方式失败、环回监听端一个字节没收到。**DNS 明确留在外面且没有被悄悄扩进去**：`UNGUARDED_RESOLUTION_EVENT` 具名，并由子进程实测「解析一个名字确实抛这个事件、且不抛任何被守事件」钉住 —— 顺带实测到 `socket.getaddrinfo` 的参数形状是 `(host, port, family, type, protocol)`，`args[0]` 是 `str` 不是 socket，故把它加进被守集合不是「守得更宽」而是在审计钩子里抛 `AttributeError`。**新增一条声明边界**：不走 `_socket` 的代码（`ctypes.CDLL(None).connect(...)`）不抛事件、看不见 —— 与子进程同类，是蓄意规避而非本守卫要抓的漂移。还原从「类字典往返」换成**子进程跑完整周期**：块内被拒、块外投递成功，这比子类往返强，它观测的是保证而不是类字典的形状 | 单元：非 e2e 测试里 `_socket.socket` 的 TCP、UDP 与 detach 重包三条路都必须被拒，且环回监听端收不到 | — |
| `V2-P4-106` | ~~**内容地址铸造审计披露的盲区远窄于真实盲区**~~ **已修** | 测 | 037 | **P4 技术验收实测**。`037` 的抽取器读的是切片处**字面量 `24`**，只披露了一条规避（`hexdigest()[:_WIDTH]`，非字面量切片）。验收找到**另外两条、都没被披露**，每条都铸出 `CONTENT_ADDRESS_PATTERN` 认的 `sgs_<24 hex>`、每条都违反全部三个规范化关键字。本行在 `46253c4` 逐条复现（单跑该模块）：`sha256(c).hexdigest()[:24]` 对照组 **2 failed**；`hexdigest()[:_WIDTH]`（已披露）**39 passed**；`sha256(c).digest()[:12].hex()`（**未披露**）**39 passed**；`blake2b(c, digest_size=12).hexdigest()`（**未披露、根本没有切片**）**39 passed**。**第三条最要命**：`.digest()[:12].hex()` 是**同一个哈希函数、同样的字节**，实测铸出与对照组**逐字节相同**的地址 `sgs_2d711642b726b04401627ca9` —— 它不是语义不同的漏洞，是同一次铸造挪了一个 token，一个伸手去拿字节而非十六进制的人会自然写成这样、毫无规避意图。**故扩抽取器而非扩披露**：不再找切片、不再找字面量 `24`、也不再按名字找 `sha256`，改为找 **`src/` 下每一次 `hashlib` 构造器调用**，再按其摘要是否被**收窄到算法全宽以下**分流 —— `digest()`/`hexdigest()` 上的下标（**括号里是什么一概不看**）、给这两者的长度参数（`shake_128(...).hexdigest(12)`，第三种收窄法，一并加进探针）、或构造器上的 `digest_size=`/`digest_length=`/`dklen=`。**`037` 不肯扩的理由由构造而非跳过清单回答**：那些 64 位十六进制校验和不是被排除，而是**同样以等式在 `DECLARED_PLAIN_DIGESTS` 里自立一张表**，由收窄测量而非名单决定一个站点属于哪张表 —— 把铸造塞进校验和表会红（实测：3 failed），校验和开始截断也会红（变异 106.19 实测被杀）。**顺带实测到本行自己的数字**：警告里的「七条」对**调用数**是对的、对**函数数**差一 —— 七次返回全宽的 `hexdigest()`，落在**六个**函数里，`runtime/engine.py::ResearchEngine._load_or_start_recovery` 一个函数里哈希两次（`request_digest` 与 `graph_signature`），故另立 `DIGESTS_PER_SITE` 记这唯一的例外，顺手关上两张按函数取键的表都看不见的方向：**在已声明函数内部再加一次铸造**。全树实测 **14 个站点、15 次调用 = 8 铸造 + 6 校验和函数**。抽取器带**自己的测试**：四条探针拼法各自作为完整模块被解析并断言判为收窄，同时被 `exec` 出来、其输出对活的 `CONTENT_ADDRESS_PATTERN` 匹配 —— 只能指着自己审计的那棵树跑的审计，无法证明它抓得住树里还没有的东西 | 单元：`src/` 下任何 `hashlib` 调用都必须落在两张声明表之一，且收窄与否由测量决定归属 | — |
| `V2-P4-107` | ~~**阈值 2 的风险标记声明审计，拆掉一个字面量就能绕过**~~ **已修一半、另一半具名披露** | 测 | 030 | **P4 技术验收实测**。`decisions/risk.py` 里 `frozenset({"future" "_data", "look_ahead" + "_violation"})` 过审计（**1 passed**）：相邻字面量在解析期就被折叠、**是**能抓到的（`"data-" "quality"` 当初被抓到），显式 `+` 是 `ast.BinOp`、两半各自都不是标记名，抓不到；而 `blocked` 档**恰好只有两个成员**，故一个回归的 `_blocking_flags` 只需拆掉一个字面量就能藏住。本行在 `46253c4` 四种拼法逐条复现：写全 **1 failed**、隐式拼接 **1 failed**、显式 `+` **9 passed**、`"".join([...])` **9 passed**。**折 `+`，并把它折的理由说清楚**：这不是宣称关掉了这一类，而是关掉那**一条与已抓到的拼法之间的差别纯属偶然**的规避 —— 两者对读者与对解释器是同一件事，只有 CPython 在哪里做常量折叠这一点把其中一条变得可见。**剩下的一类具名披露且可执行**：`KNOWN_RUNTIME_ASSEMBLY_EVASION` 保存的是**源码本身**而非散文，`test_a_flag_set_assembled_at_run_time_is_invisible_to_this_audit` 把三种拼法各写进一个临时模块跑真抽取器 —— 写全与 `+` 都必须被看见、`"".join([...])` 必须**看不见**，故任何人把这一类关上时它会红，而这正是要的信号（改披露，不是让它通过）。这一类无界（`.replace()`、`chr(...)`、f-string、`bytes.decode()`、查表），且每一个成员都是蓄意的；该测试的 docstring 不像 `037` 那样声称抗对抗，故**本行的定性是「关掉便宜的一条 + 精确披露剩下的」而非「关掉这一类」**。**不入 `KNOWN_*` 注册表且这是刻意的**：三十二张注册表每一条都是 `src/` 里**出货产品**的局限，这是一条**测试**的局限，先例是 `037` 自己的披露 —— 就住在拥有该审计的测试模块里；差别在于这一条**可执行**，故不会烂成一句曾经为真的话。**`REGISTRY_ENTRY_COUNTS` 未动**（未增删任何注册表条目）。**同一个 helper 在 `tests/unit/domain/test_run_mode.py` 里有一份逐字复制、缺陷完全相同，一并修并一并补测**：只在两份拷贝之一里修好的缺陷，会从没人测的那份回来。**另外实测到本仓一句自述为假**：该 helper 的 docstring 说排除散文是必要的、否则「`product/governance.py` 与 `product/screening.py` 都会成为违规者」—— 实测**在今天这棵树上一个都不会**：比较是整个 `ast.Constant` 与标记名的**精确相等**，而 docstring 是一整条永远不等于 `"future_data"` 的常量，`governance.py` 是在一句话**内部**提到那个标记、两种算法下都得零分；把 docstring 计入不改变这两个审计在 `src/` 上**任何一个模块**的答案。过滤器保留（一行**恰好就是**标记名的 docstring 会命中），但它此前是**无人驱动的代码带着一句树不支持的理由**，现补上专测把它变成会被驱动的代码。**`DECLARATION_THRESHOLD` 从比较处提出来具名**：阈值本身就是 `030` 的全部，而它此前**没有任何测试钉住** —— `domain/risk_flag.py` 写全五个名字，任何阈值它都满足，故阈值往上漂全套仍绿（变异 107.05/107.06 现已被杀） | 单元：`+` 拼接的声明必须被抓到并点名 `decisions/risk.py`；运行期拼装的必须被披露为看不见 | — |
| `V2-P4-108` | ~~**`factor build --tier neutralized --as-of <非交易日>` 抛未处理的 `PriceDataError`**~~ **已修** | 产 | 060 | **P4 产品验收挖出、此前无行**。`_neutralized` 在 `load_industry_market_cap_cross_section` 外面 `except _PANEL_FAULTS`，而 `PriceDataError` 不在那四个里 —— 该异常正是 `panel_ingest._read_visible_price_session` 对「这天交易所没开市」的**判决**（`2026-01-10 is not an open session on the SZSE calendar, so there are no daily_basic rows to read for it`），于是一条设计成答案的拒绝以未预期异常的身份到达 `cli._panel_command`：exit **5**、`it raised an unhandled PriceDataError. This is a defect in the command, not a verdict about the panel -- nothing was checked`，且它自己那句话被扣下（未预期的栈帧可能握着凭据）。**扣下是对的，「未预期」不对** —— 与 `V2-P4-060` 逐字同形，只是换了一条拒绝。**是既有缺陷而非本轮引入**：`daily_basic` 自 `V2-P4-026` 起就走按日的门并从同一个函数抛同一条拒绝，故 `V2-P4-028` 之前同一个调用就已可达。**修在读处而非常量上**，这是 `060` 自己定下的安排：`_PANEL_FAULTS` 被 `shortlist_view` 重述，且 `test_this_face_calls_the_same_panel_faults_unreadable_as_the_factor_face` 拿两个模块元组的**并集**去驱**两个面**的 `_read`，故单独往这边加一个成员会让那条钉子在另一个面上红；新增 `_CROSS_SECTION_FAULTS = (*_PANEL_FAULTS, PriceDataError)` 只给那一个 `except`。**记录下的两个设计问题各有答案**：①`_REGISTRY_FAULTS` **不**跟着变宽 —— 它是 `(*_PANEL_FAULTS, ...)`，常量变宽会自动带上它，而 `_read_registry` 包的是 `load_stock_universe`，读的是按事件定期的生命周期分区、从不问日历某天是否开市，一条读不出来的 fault 列在那张表里正是 `test_the_registry_read_is_the_only_site_either_face_widens_for` 要拒的死条目；②`shortlist_view` **未在本轮结案**（不属本次分工）：从这边能说的是它的价格读经 `panel_ingest.newest_published_session` 解析会话，该函数按构造返回**开市**会话，故这一条具体的臂在那边不像这边这样可达 —— 但那不是完整审计，留给持有该文件的人。**tier 就是全部，且是量出来的**：同一时刻 `--tier raw` 与 `--tier processed` 都 exit 0（两者都不为被定价的那一天读会话级价格分区），只有残差会碰到；驱动默认 tier 的测试会是绿的。修后 exit **1**（`blocked`，即 `FACTOR_EXIT["blocked"] = PanelExit.unhealthy`）—— **本行原写 exit 3，是错的，由 P4 第 9 波产品验收实测纠正**：信封名对、数字错，`bad_request` 才是 3，而这条拒绝从来走 `blocked`。`openalpha factor build --help` 自己写的也是「1 when the panel could not answer」。**顺带记下该数字所暴露的问题，已开 `V2-P4-109`**：周六（永远不会变成会话）与「该会话尚未收盘」（等到下午即可）共用 exit 1，且共用同一句把两种补救并列的消息，消息是这条拒绝本来就有的那一条 —— `the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed`，它自己的句子早就写着「on a day the exchange was open」，所以变的是那条臂可达，不是它说什么 | 集成：非交易日的预测时刻必须是 `blocked` 信封而非裸栈 | S48 |
| `V2-P4-109` | ~~**一个 exit 码带两条互斥的补救：周六与「尚未收盘」共用 exit 1，且共用同一句话**~~ **已修消息、显式不拆码** | 产 | 108 | **P4 第 9 波产品验收挖出，由 `V2-P4-108` 那行记错的数字牵出**。`PanelExit` 自己的 docstring 说这些码存在的理由是让 CI 分辨「去重新取数」与「改你的命令行」；实测 `factor build --tier neutralized` 在 2026-01-10（周六，**永远不会变成会话**）与 2026-01-12T01:00Z（周一，**当天下午就收盘**）**都 exit 1**，且拒绝语逐字相同地并列四条补救：`Build --tier processed at this instant, or move --as-of to after the session's close, or name the missing year, or fetch the later sessions first.`。对周六，其中三条是错的 —— 没有任何 fetch、没有任何等待能让 2026-01-10 变成会话。**修的是消息不是码**：`RESIDUAL_REMEDIES` 以 `CalendarDayStatus` 三值判决为键（`closed` / `trading` / `beyond_horizon`），日历在 `_neutralized` 里抛出前就已加载，故判别不花一次读。三条补救各自独立：`closed` 只说「换一个会话，交易所那天从来没开过」，`trading` 只说「移到 16:30 收盘之后，或现在先建 processed」，`beyond_horizon` 才说「先取更晚的会话」。`--year` 那条与日历判决正交，故作为后缀而非第四行。**不拆 exit 码是有理由的决定而非省事**：`bad_request` 的含义是「再取多少次数据都没用」，而日历回答 `closed` 的那天**也可能**只是 `trade_cal` 分区不够长 —— 日历分不出「交易所关门」与「本面板不知道它开过」，后者的补救恰恰就是重新取数。在那里答 3 会让定时任务停止重试一个重试就能修好的面板，这是两种错误里更贵的一种。该共用记入 `KNOWN_FACTOR_RUN_LIMITATIONS.a_closed_day_and_an_unclosed_session_share_one_exit_code`（注册表 9→10，`REGISTRY_ENTRY_COUNTS` 同步）。**分离器是两个时刻而不是一个**：两个方向各断言对方的句子不出现，因为一句「把所有补救都说一遍」的消息能满足任何单方向检查 —— 那正是被替换掉的那句 | 集成：两种状态各只说自己的补救 | S48 |
| `V2-P4-110` | ~~**`panel doctor --dataset <one> --json` 的 84.8% 是与面板无关的静态散文，且无法拒收**~~ **已修（新增 `--no-limitation-detail`，默认不变）** | 产 | — | **P4 第 9 波产品验收挖出**。实测（生成面板、`--dataset index_daily --year 2026`）：stdout 共 **16,936 字节**，其中 `limitations` **14,359 字节（84.8%）/ 10 条**，而调用方真正问的 `findings` 只有 1,340 字节。**验收的措辞被实测推翻了一半，如实记下，因为照它修就会修错**：报告写的是「大部分是与所问数据集**无关**的整本台账」；`panel_doctor.known_limitations` 早已按 `wanted & set(item.datasets)` 过滤 —— 那 10 条里 **4 条**正是 `KNOWN_INDEX_PRICE_LIMITATIONS`（`index_daily` 自己的），另 6 条是 `storage_limitations()`（不点名任何数据集，因为对每个数据集一律成立，由 `panel_health_report` 有意附加）；问 `daily` 得另外 12 条，问三个数据集得 23 条。**台账是有作用域的**。**成立的是另一半，也就是缺陷本身**：那 84.8% 与面板状态无关 —— 健康的面板与损坏的面板逐字节相同，第一次跑与第一千次跑相同 —— 而**文本面**从写下起就只渲染一个计数，理由写在 `_echo_report` 的注释里（「一份把自己的 findings 埋在它们底下的人类报告，会教会读者两边都跳过」），机器面却没有同样的选择。**修法**：`health_report_payload(report, *, limitation_detail=True)`，CLI `--limitation-detail/--no-limitation-detail`、REST `?limitation_detail=`，三面一致（`clearance_payload` 取默认值不动）。关掉后每条仍留 `code`/`datasets`/`dates`，只掉 `detail` 段落 —— **一个让大的诚实答案变成小的不诚实答案的开关比原缺陷更糟**，故按 key 集合断言而非按三个名字。**默认不变是决定而非谨慎**：只在被索要时才提供的注册表就是没人再读的注册表 | 集成：三面同一决定、findings 逐字不变 | S48 |
| `V2-P4-111` | ~~**`import openalpha_cn.api.app` 会往用户真实 `runtime/` 里跑迁移并写备份；且备份无上限、无清理、无终止条件**~~ **已修（惰性 `app` + 空跑不留备份 + 显式清理命令）** | 技 | — | **P4 第 9 波产品验收挖出，且实测出验收未指出的真正成因**。①`app = create_app()` 在模块顶层，故一次裸 import 就 `build_storage` → `run_migrations` → 写一份 ~139 KB 备份；`create_app` 的 docstring 特意声明该行「对 `.env` 是 filesystem-free」，它对 `runtime/` 不是。②**目录一直在长的原因不是 import**：拿用户真实 `state.sqlite3` 的**副本**（原件未动）连跑三次 `run_migrations`，得 `from=4 to=4 applied=[] backup=True` ×3 —— 那个库停在 `user_version=4`，其 `schema_migrations` 是 `[(1,baseline),(2,demo…),(3,demo…),(4,create_query_path_indexes)]`，早于 `create_validation_results` 的排序修复，故**没有 `validation_results` 表**，而 `_rewrite_contract_identities` 以该表为前提，抛 `MigrationNotYetApplicable` 后**永远 pending**；备份却在循环**之前**取，于是**每一次进程启动都拷一份整库、什么都不应用**。用户仓库里 128 个文件中的 **125 个**正是它：全是 `v4`、全是 139,264 字节、无终止条件。`run_migrations` 自己的 docstring 早就写对了空集那一半（「if nothing is pending … takes no backup」），一个 defer 掉的迁移只是同一情形晚一步到达。**三处修改与一处明确拒绝**：(a) `app` 改为 PEP 562 模块 `__getattr__` 惰性构造 —— `uvicorn openalpha_cn.api.app:app`（`Dockerfile` 与 `cli.serve` 用的就是这个字符串）与 `from … import app` 都走 `getattr`，行为不变，变的只是求值时机；`api/__init__.py` 的 `from …app import app` 是**属性访问**，会把下一层的惰性整个抵消，故两处必须一起改。(b) `run_migrations` 在 `applied` 为空时**删掉自己刚取的那份备份**：备份只能在循环前取（迁移是从 `apply()` 内部抛异常来宣告前提不满足的，事前无从得知谁会写），故删除放在末尾；该文件可证是本次调用自己的（`_take_backup` 用 `O_CREAT|O_EXCL` 占名）且可证是冗余的（什么都没应用，逐字节等于旁边那个库）；**失败的迁移保留备份**，那是 `MigrationFailedError` 指给调用者的东西，且那条路径抛异常、根本到不了删除。(c) 新增 `openalpha migrate prune-backups --keep N [--dry-run]`。**明确拒绝的是自动保留上限**：那会在用户下次跑任何命令时删掉他们已有的 128 份，而预迁移备份是用户的数据 —— **本次一份都没删**，清理是人主动跑的命令 | 集成：裸 import 不落盘；空跑不留备份；清理命令 dry-run 不删 | S48 |
| `V2-P4-112` | ~~**`AgentRouter` 的「任一 evidence family」量词只有散文、没有守卫**~~ **已修（补测，未动源码）** | 测 | 008 | **P4 技术验收提出、本行在 `037ffa8` 上复现**。`runtime/router.py:230` 的 `declared_families & families` 是**任一**语义，`AgentRouter` 与 `tests/unit/runtime/test_agent_routing.py` 两处 docstring 都点名 `agents/baseline.py::ThemeAgent`（声明 `{theme, catalyst, disclosure}`）作为「为什么是任一而不是全部」的理由。把它变异成 `declared_families <= families`（**全部**）：`tests/unit/runtime tests/unit/agents tests/unit/evidence` 加三个集成文件 **111 passed, 1 skipped**，与基线逐字节相同 —— 该文件里六处 `evidence_families=` **每一处都只声明一个 family**，而单 family 声明下两条规则对任何运行都同解。**同一文件早有 feature 半边的对照**（`test_every_declared_column_must_be_on_the_plane_and_not_merely_one_of_them`），family 半边没有。**源码是对的，故补测不补码**：新增两条 —— 一条 `DeclaringAgent` 声明三个 family、只到一个，与 feature 半边严格对称；一条**直接路由真的 `ThemeAgent`**，让两处 docstring 的引用可执行（有人把 `ThemeAgent.evidence_families` 收窄成单个也会红）。**同时证伪验收报告一条主张**：报告称三 family agent「never routed by any test that could tell `any` from `every`」，**为假** —— `tests/integration/test_research_cycle.py::test_multi_agent_cycle_persists_evidence_linked_decision_idempotently` 的语料正是 `market_event`/`theme`/`capital`，`<=` 下 `theme-agent` 被丢掉，`evidence_ids` 与 `routing_path` 双双变红，实测**该变异体在那个文件上被杀**；验收给出的「四个集成文件」恰好没包含它。故本行的定性是**「守卫存在但是偶然的、且不在拥有该规则的单元文件里」**，而非「无人能分辨」 | 单元：三 family 只到一个必须被路由，且 `<= families` 会红；真 `ThemeAgent` 亲自跑一遍 | S38 |
| `V2-P4-113` | ~~**`V2-P4-064` 的「两个方向都会红」为假，漏掉的方向是前视**~~ **已修（补守 + 就地更正两处自述）** | 测 | 064 | **P4 技术验收提出、本行在 `037ffa8` 上逐条重测**。只变异 `factor_view.py::_computed` 里注册簿那一次读的 `as_of`：`as_ofs[0]` **红**（但走具名拒绝而非计数，见 `064` 行更正）；`as_ofs[-1]` **整份文件全绿 `36 passed`**，与基线逐字节相同，且它答的就是 `[8, 7]` —— 与正确答案相同。**晚读注册簿是前视**：一条生命周期行在它可被知晓之前就进了更早的截面。**根因不是夹具薄，是 `universe_counts` 结构性失明，且这一条比「换个夹具」更值得写下**：`stock_basic` 是 `ClockStrategy.calendar_static`，`panel_ingest._knowable_through_the_same_day` 让日期为 `D` 的行「在 `D` 当天及之后可见、之前不可见」；而 `listed_on(day)` 只在 `listed_on <= day` 且 `day < delisted_on` 时留人 —— 一条行**能改变** `day` 的答案的充要条件恰是「它自己的日期 ≤ `day`」，也正是「它在 `day` 当天那个时刻已经可见」。两个条件逐字相同，**故在任何夹具上，把注册簿读推后都移不动更早时刻的成员数**。**验收给出的补法（`delist_date` 早于时刻一、`available_time` 晚于它）实测无效并已证伪**：该数据集的可见性走的是**生命周期日期**经普查界，**不看存下来的 `available_time`**，故这样一行在时刻一照样可见，什么都分不出。**真正会动的是 `subjects`** —— `_computed` 取自 `universe.securities`，那是**每一条可见行**、不按日期过滤。故新增 `test_a_registry_read_at_the_last_instant_hands_an_earlier_one_a_security_that_had_not_listed`：一个价格面从不报价的注册簿专属代码，挂牌 2026-01-12、退市 2026-01-14，**整段生命周期夹在两个时刻之间**（`dataclasses.replace` 就地加两行，不进 `PANEL_SHAPES`，真写入器与全部写时守卫照跑）。正确实现下时刻一根本不知道这个名字、时刻二知道且 `listed_on` 排除它 → `not_in_universe == 1`；`as_ofs[-1]` 下时刻一也拿到它 → `== 2`，**变异体被杀**（`universe_counts` 两边都是 `(8, 8)`，正是上面那条失明的实证）。**同时就地更正两处自述**：`V2-P4-064` 行、`test_the_registry_is_read_at_each_prediction_instant_and_not_once_for_the_build` 的 docstring | 集成：晚钉注册簿必须让更早的截面多出一个尚未挂牌的 subject | S48 |
| `V2-P4-114` | ~~**`panel_ingest._session_census` 仍在重述 `V2-P4-063` 删掉的规则，留下一个会话宽的写时缺口**~~ **已修** | 技 | 063 | **P4 技术验收提出、本行复现并修复**。`_session_census` 的上界是 `batch.fetched_at ... - timedelta(days=1)`，其 docstring 用 16:30 发布规则为这个**无条件减一天**背书 —— 那句话只在 16:30 **以下**为真，而它正是 `V2-P4-063` 从 `cli._build_sessions` 里删掉的同一句；`cli.py` 的 `panel build` 至今仍称两者「are the same rule applied at two layers」，`063` 之后**已不成立**。**实测复现**（周中开市的 2026 年 1 月日历、`fetched_at = 2026-01-20T17:00+08`、语料持有除 2026-01-20 外的每一个会话）：`_sessions_published_through` 答 `2026-01-20`，普查答 `([], 2026-01-01, 2026-01-19)` —— **写时普查接受了一个恰好缺最新会话的分区**。于是构建取到 D、`panel doctor` 与 `_price_requirement` 要求 D、而写时拒绝停在 D−1，缺口**正好压在 `063` 补进来的那个会话上**，且没有任何东西钉住这段松弛。**修法与 `063` 同形**：上界改调 `_sessions_published_through`，共享**函数**而非重述算术。**修后八个 panel/CLI 文件 2 failed, 270 passed**（基线 272 passed 全绿），**两条都是渲染出来的日期字面量、不是行为** —— `2026-08-07` → `2026-08-08`，而那条缺失会话 `2026-06-12` 在两套规则下都被找到、日历越界拒绝也照旧触发；两处字面量已更新。**并证伪验收自己一条主张**：报告说「every clock in that module was noon」，`tests/integration/panel/test_daily_panel_ingest.py:235` 的 `FETCHED_AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)` 其实是 **20:00 Asia/Shanghai、在 16:30 之上**；它分不出来的真正原因是**普查从来没有被问到过取数当天那个会话** —— 该模块其余测试的会话都在 6 月，7、8 月在日历上整月关闭，两套规则唯一分歧的那一天谁都不要求。**故新增测试把取数日在日历上开市、并从语料里扣掉它**，这是唯一能把分歧塞进普查自己问题里的形状；两半分别落在 16:30 两侧（20:00 必须拒、08:00 必须收），后者防止修法退化成「永远要求今天」，那会让每一次盘中构建误报。同时更正 `cli.py` 那句「same rule」的注释。**变异扫描**（先证基线全绿再开跑，判据为四个 panel/CLI 文件）：`_session_census` 与 `_sessions_published_through` 两个函数的可执行行共 **9 个变异体、8 个被杀**。唯一存活体 `if closes_on < opens_on:` → `<=` **实测不可达、判为等价**（非贴标签）：要分辨两者，语料必须是 2026 年、取数于 2026-01-01T16:30+08、且**缺** 2026-01-01；而 `ColumnarPanelBatch._check_visible_at_as_of` 拒绝任何 available_time 晚于取数时刻的行 —— 实测 2026-01-02 与 2026-06-10 两行都被拒、只有 2026-01-01 那行能构造出来，故该批要么带着 01-01（两条规则同解），要么根本没有 2026 年的行可普查。**16:30 边界那一个此前也活着，已补测杀掉**：恰好 16:30 必须算已发布（`>=` 而非 `>`），这是 `DAILY_AVAILABILITY_TIME` 自己的措辞决定的一侧，且在当天其余任何时刻都看不出来 | 集成：取数日已发布时，缺该会话的分区必须在写时被拒；未发布时同一批必须被接受；恰好 16:30 算已发布 | S48 |
| `V2-P4-115` | ~~**`V2-P4-007/008/009` 变异扫描把两个存活体判为「provably equivalent」，两个都不是**~~ **已修（各自补测/更正，不重贴标签）** | 测 | 007 | **P4 技术验收提出、本行三个逐条重测**。该扫描 **341 mutants / 326 killed**，七个存活体被判为可证等价。(a) **`agents/feature.py:117` 局部变量类型标注里的 `Literal` 成员**：`Literal["bullish", "bearish", "neutral"]` → `["bullish", "XXXXXX", "neutral"]`，实测 `pytest tests/unit/agents` **30 passed**（确实活）而 `uv run mypy src scripts` **报 2 个错**（`[assignment]` 与 `[arg-type]`，基线是 `Success: no issues found in 143 source files`）。`mypy src scripts` 是本仓出货的闸门，**故这是「扫描工具」存活体而非等价变异体** —— 扫描的分母排除了一道会杀死它的闸门。**已在扫描自己的记录（CHANGELOG）里写下这条一般结论**：**当 pytest 之外还有闸门时，只以 pytest 为判据的变异扫描会系统性低报**。(b) **`cli.py` `shortlist compare --json` 上的 `ensure_ascii=False` → `True`**：**只在夹具上等价**。载荷逐字携带 `declaration`，而 `declaration.exchange` 在请求期只被要求「非空、无首尾空白」，故 `--exchange 上交所` 在变异体下渲染成 `\u4e0a\u4ea4\u6240`。同文件既有的 `test_the_json_face_emits_one_deterministic_byte_sequence` 也抓不到 —— 它把解析回来的正文用 `ensure_ascii=False` 再编码后比较，对全 ASCII 输入是恒真式。**已补测**：取夹具自己的两份文档，把 `declaration.exchange` 改成 `上交所`、用 `stable_answer_digest`（即 `held_shortlist` 用来校验的同一个函数）重新寻址后写进一个全新的 store —— **两份一起改**，因为 `declaration` 在 `COMPARABLE_KEYS` 里，只改一份会被判成「两个问题」而根本到不了渲染。实测变异体**被杀**。(c) **`@dataclass(frozen=True, slots=True)` → `slots=False`**：**确实等价**，维持原判 | 单元/集成：`mypy` 在 `Literal` 变异体上必须报错；非 ASCII 交易所名必须原样打印 | S40 |

**闸门**：排序测试覆盖确定性排序、平局政策、弃权、缺失依赖、过期数据、风险/可交易性标记，且每个入选候选证据闭合；模型评估测试用已知信噪比数据验证 walk-forward 切分、purge/embargo、制品身份、前瞻预测落库；契约升版后从 v1 卷迁移的记录仍可读；新 agent 全部经 `run_cycle` 缝验收。

**风险**：`V2-P4-001` 是唯一的破坏性变更窗口。开工前必须把三项变更的完整字段清单写定 —— 审计已列出全部影响点（3 处 mode 定义、`validation.py:45-52` 精确求和校验、5 处硬编码 horizon、5 个 checked-in schema、`web/src/types.ts`、11 个测试文件），漏一项就是第二轮迁移。

---

## P5 — 组合、验证与工作台（25 issues）

| ID | 标题 | 类型 | 依赖 | 说明 | PRD |
|---|---|---|---|---|---|
| `V2-P5-001` | ~~启发式组合构建政策（分层排序 + 上限裁剪 + 换手预算），报告显式标注 `heuristic, not optimized`~~ **已完成** | 技 | P4 | 已交付 `backtest/portfolio_policy.py`（第十二个纯 stdlib 叶子，同时进两条 `backtest-studies-*` 契约的 source 清单）。**标注是可校验字段**：`PortfolioConstruction.method` 是 `Literal["heuristic, not optimized"]`，终端渲染与 `--json` 两面都印，说不出这句话的构建根本过不了校验。三步各自可分辨：**分层**按 rank 切连续块、块内**等权**（分数只带给读者、从不当量纲用，因为 `KNOWN_CROSS_SECTION_LIMITATIONS` 已实测那些分数没拟合任何东西），余数归靠前的层；**裁剪**是 clamp → 按 headroom 按比例回配 → 再 clamp 的**有界**迭代，最后一步永远是 clamp，故返回值无条件满足全部上限；**放不下的重量变现金**并以 `unallocated_weight` 报出 —— 绝不摊到最后一名，那正是 `V2-P5-005` 要从 `backtest/validation.py` 删掉的把戏，不在此提前重演。**换手预算**按 `budget / turnover` 整体缩放，`turnover` 与 `turnover_before_budget` 并列上报。**不引入 cvxpy 不是省略而是 ADR-0003 的结论**：九个运行时依赖、无数值栈，协方差与求解器不可发。**产品面走 `CliRunner` 与 `OpenAlphaSDK`**（`openalpha portfolio construct` / `OpenAlphaSDK.construct_portfolio`），集成测试从生成面板 + 真 `factor build` + 真 `shortlist run` 一路跑到按内容地址取回的答案上；**被闸门拒绝的清单两面都不给权重**（`admitted` 为 `null` 与 `[]` 是 `V2-P4-032` 分开的两个答案）。`KNOWN_CONSTRUCTION_LIMITATIONS` 是第三十五个注册表（七条，总数 323 → 330，表行 33 → 34），运行时依赖仍**九个**，`lint-imports` 仍 **8 kept / 0 broken**（只加 source，不放宽任何禁令） | 单元：三层权重必须三个不同数；上限吃不下的重量必须以现金报出而非摊派；预算必须让实际换手落在预算上。集成（CLI/SDK）：被拒清单不可构建；行业上限在本面具名拒绝 | S52, D18 |
| `V2-P5-002` | ~~`PortfolioOrder` 增加目标权重；`PortfolioLimits` 扩展行业上限/换手预算/现金下限~~ **已完成** | 结 | 001 | `PortfolioLimits` 由 2 个字段变 **5** 个；`PortfolioOrder.target_weight` 是**声明**不是定价输入 —— 模拟器拒绝「声明目标已超单票上限」的买单，成交后仍照旧校验**实际**权重，两者是不同的事实，在漂移过的账簿上正好不同。**哪个消费者读哪个字段写成集合而不是靠发现**：`LIMITS_ENFORCED_BY_THE_SIMULATOR` 与 `LIMITS_ENFORCED_BY_THE_CONSTRUCTION_POLICY` 对 `PortfolioLimits.model_fields` 做**覆盖**相等，契约上多一个没人读的上限即红 —— 正是 `V2-P4-030` 在风险闸门里抓到四例的那种 fail-open。模拟器不读的两个是**结构性**读不到：`MarketBar` 没有行业、单笔订单没有账簿历史。**本行两条前提被实测证伪**：(a) **「现金下限」不是第三条约束** —— 长仓无杠杆下 `equity == cash + market_value`，`cash/equity >= f` 与 `market_value/equity <= 1-f` 是同一个不等式，30% 现金下限与 70% 敞口上限实测给出**逐字节相同的权重**；字段照发（行要求、且按下限声明意图更易读），但代码不假装两者可叠加，取更紧的那个并在拒绝理由里说明是哪一个绑住的。(b) **它不是对已存行的破坏性变更，且这是量出来的不是假设的** —— `PortfolioTransition` 内嵌本模型且**确实入库**（`single_version()`），故 AGENTS.md 规则 3 适用而 `V2-P4-001` 窗口已关；实测：**旧 payload 原样读回**（默认值补上缺键，`read_versioned` 与账本用的是同一条路径），**会动的是字节** —— `SQLitePortfolioLedger.append` 逐字节比较的 payload 现在带 `"target_weight":null`，故**重放一条旧构建存过的 transition 会触发冲突守卫**。这就是迁移代价：一次账本重写，而不是契约升版（本模型没有第二个版本，且五个 checked-in schema 里没有任何 portfolio 契约）。**依赖方向与本行所写相反**：`001` 的上限裁剪与换手预算需要 `002` 的三个字段才能存在，两行实为一次交付 | 单元：声明目标超上限必须具名拒绝且同一笔在上限内必须成交；现金下限必须在敞口上限放开时独立生效；旧行必须读回、且重放必须撞冲突守卫 | S53, D18 |
| `V2-P5-003` | ~~组合级多日回测（现有 `PortfolioBacktestStep` 强制单标的步，K 只股票要 K 步）~~ **已完成** | 技 | 002 | **一步 = 一个交易日的整本账**：`trade_date` + `bars` + `orders` + 单一 `benchmark_close`。「K 步」只是表象，**改前用两只股票实测出三个缺陷**：(a) 净值曲线两点同日 `2026-07-24`，其中一点在盘中（第一笔成交后、第二笔之前）；(b) **持有而不交易的名字无法被告知今日收盘价** —— 唯一的入口是把 bar 挂在**订单**上，实测市值报 `21000.00` 而真值 `31000`；(c) 于是**风控上限拿昨日价格判今日买单**，`max_gross_exposure` 报 `0.210032` 而真值约 `0.31`。**先按当日收盘重估整本账，再执行当日订单** —— 这个顺序在同一组 fixture 上给出两个不同答案：先重估则该买单被 `maximum total exposure exceeded` 拒绝，后重估（或不重估）则**成交**；变异实测两个方向各自被杀。`orders` 可为空，「只持有、不交易」的一天终于可表达。**当日无 bar 的持仓沿用旧价，但具名上报**：A 股停牌当日 `daily` 不发行，拒绝该日会让停牌不可表达，故新增 `PortfolioBacktestReport.carried_marks`（含会话与已连续沿用几个会话）；**沉默地沿用**才是被拒绝的那件事。乱序会话在入口整体拒绝，不再落成「曲线倒流而其上每笔都写 rejected」的报告。**`max_industry_weight` 未被本行改变，仍是具名拒绝** —— K 根 `MarketBar` 并不比一根多带任何行业，本步供不出 exposures，故不悄悄满足一条本在拒绝的上限。**两个产品面零字节改动即随之改形**（`POST /api/v1/backtests/portfolio` 与 `OpenAlphaSDK.run_portfolio_backtest` 都是本模型的直通），而这两面正是 `F38` 所列 22 条无人消费的路由之一：**本行之前它们没有任何测试**，现由 `TestClient` 与 `OpenAlphaSDK` 各自驱动 | 单元：同日 K 名一步一点；未交易的持仓必须重估到今日收盘；重估必须落在当日订单之前（同一 fixture 两个答案）；无 bar 的持仓必须具名上报。集成（REST/SDK）：两面必须逐字段相等；`carried_marks` 必须出现在响应体里；订单在本会话无 bar 必须是 `422` | S55 |
| `V2-P5-004` | Paper Portfolio（前瞻模拟，绝不连券商） | 技 | 003 | 复用不可变订单/转换记账 | S57, D19 |
| `V2-P5-005` | ~~**替换占位归因**：删除 `backtest/validation.py:88-90` 的 20/30/50 硬编码与 `:106-116` 的末项吸收残差技巧~~ **已修** | 技 | P4-001 | **本行三处行号引用在 `c847295` 上全部过期，已就地更正**：20/30/50 实为 `:201-203`（`:88-90` 是 `observation_from_label` 的函数签名）；末项吸收实为 `:218-229`、关键行 `:221` 的 `agent_total - allocated if is_last else ...`（`:106-116` 是同一函数的 docstring）。**先测量再设计**：契约半边（`unexplained_return` 字段与含残差的对账）`V2-P4-001` 已交付且为真，占位的是**计算**，不是契约。替换后只留两个**可测**项：`transaction-cost`（`-transaction_cost`，零成本时仍发出，否则「本次无成本」与「本构建不建模成本」不可分辨）与仅属空仓决策的 `no-position-versus-benchmark`（值为 `realized_return - benchmark_return`，空仓时恰为 `-benchmark_return`，唯一认领者、无余项）。**持仓决策的整段选股收益进 `unexplained_return`** —— 一个已完成的 `ResearchRunResult` 只带信念、置信度与版本串，没有一个是收益，故任何 rule/factor/agent/model 份额都无法被证明。新增第 35 个注册簿 `KNOWN_ATTRIBUTION_LIMITATIONS`（4 条），两处审计同步（`REGISTRY_ENTRY_COUNTS` 与 `DOCSTRING_TOTALS`：34→35 / 323→327 / 33→34 / 253→257）。**闭式对照两臂**（一臂分辨不出任何东西）：全部取二进分数，故两臂都用 `==` 而非 `approx` —— 持仓臂 `net 0.1796875 / 残差 0.1875 / 单项 −0.0078125`，空仓臂 `net −0.0703125 / 残差 0.0 / 两项`；「全塞进残差」的实现过持仓臂、死在空仓臂，留任何虚构切分的实现死在持仓臂。**变异扫描**（先证基线 `2970 passed, 1 skipped` 再开跑）：**24 个变异体、24 个被杀**；唯一存活体**实测非等价而非贴标签**，见 `006` 行 | S65, D21 |
| `V2-P5-006` | ~~归因残差显式化（不静默分摊）~~ **已修** | 技 | 005 | **引用同样过期**：`abs_tol=1e-9` 在 `c847295` 上是 `domain/validation.py:82`，`:45` 是 `transaction_cost` 字段。**并证实路线图另一条主张为真**：`V2-P4-001` 自述「说错残差或不说，照样失败，两个方向都断言了」—— 实测 `test_an_unreconciled_attribution_is_still_refused_now_that_a_residual_exists` 确实两个方向都断言。**故 006 的真实缺口不在契约，在生产者与出口**：(a) 生产者从不写残差，每个结果都吃默认 `0.0`，现在按测量值写入；(b) 出口 —— `web/src/types.ts` 从未镜像 `unexplained_return`，`AttributionPanel` 只打印各项与合计，**非零残差会在产品面上被静默丢弃**，已补为 `未归因残差` 一栏并由 `App.test.tsx` 驱动（面上各项合计 +1.50% 对 +7.50% 净主动收益，差额上屏而非并入末项）。**`abs_tol=1e-9` 的脆弱性实测不适用于本实现**：`(realized-benchmark) + (-cost)` 与 `realized-benchmark-cost` **逐位相同**（减即加负、取负精确），故两臂用 `==` 断言。**唯一变异存活体在此**：把空仓项写成 `-benchmark_return` 而非 `realized_return - benchmark_return`，在一切可达取值上同解**除了** `benchmark_return == 0.0` —— 那里前者是 `-0.0`、后者是 `+0.0`，规范 JSON 会写符号、`validation_id` 哈希该 JSON，于是同一结果拿到两个内容地址 （`val_dba127649bf529e77e53d6aa` 对 `val_470895b1ba7335601a265760`）。已补测驱动该差异，扫描转为 24/24 | S65, D21 |
| `V2-P5-007` | 多重检验控制（BH）+ 记录被检验假设数 | 技 | 006 | 不可省 | S63, D20 |
| `V2-P5-008` | gross/net 并列 + **cost drag 单列** + 置信区间 + 样本数 | 技 | 007 | 只报 gross/net 会让成本来源不可归因 | S61, S62 |
| `V2-P5-009` | 分段报告（行业/市值/流动性/市场状态）+ 多市场状态 walk-forward + 基准对照（**等权基线**、naive factor、v1 基线三者并列） | 技 | 008 | 等权基线是最容易被跳过也最能证伪的对照 | S59, S60, S64 |
| `V2-P5-010` | 调度原语：持久作业表（next-fire-time + lease/lock + 按交易日幂等键 + catch-up 政策 + 日历依赖 + 崩溃恢复） | 技 | P0B-004 | **原语已建成，但尚无任何调用方，故本行只关一半、另一半明写在此**。三个新模块：`job_contracts.py`（耐久形状，理由与 `batch_contracts.py` 逐字相同 —— `storage.jobs` 要持久化它而 `storage-no-upward-deps` 禁止向上）、`storage/jobs.py`（`SQLiteJobStore`）、`scheduler.py`（`TradingDayScheduler`）。**零新增运行时依赖**，ADR-0003 的九个不动；锁是 `BEGIN IMMEDIATE` 而不是 broker。**幂等键就是主键**（`job_id@YYYY-MM-DD`），同一交易日的第二次开跑是 SQLite 的 `IntegrityError`，而不是两个进程都能赢的 `SELECT`-then-`INSERT` 竞态。**崩溃恢复即租约过期**，不设清扫器 —— 清扫器自己也需要被调度；`claim()` 对过期租约与无租约一视同仁。**`due()` 刻意不读 `next_fire_time`**：存下来的触发时刻是从一个会变的日历派生出来的，交易所事后宣布一个假期就让它变成一句无人复核的旧话；真正的判据是问 `panel_ingest.newest_published_session`（拥有 16:30 `DAILY_AVAILABILITY_TIME` 那条规则的唯一函数）再与 `last_fired_session` 比对，该列只作轮询索引、每次推进都重算。**面板面新增唯一一个函数 `session_publication_instant`**：`_sessions_published_through` 的逆，就放在它旁边、读同一个常量 —— `V2-P4-063` 发现该规则被复述三处且两处不一致，`V2-P4-114` 一行之后又发现第四处，调度器自己写 `time(16, 30)` 就是第五处；两者由一年 17,520 个半小时刻度的往返恒等式互钉，而不是钉在字面量上。**建造过程中的一条实测，并且它决定了形状**：**全新** `state.sqlite3` 上 `create_app()` 停在 `schema_version: 2` —— 迁移 3（`demo_add_runs_archived_at`）因 `runs` 尚不存在而抛 `MigrationNotYetApplicable`，`run_migrations` 就此 `break`，**迁移 4 到 8 在新库上从不执行**；若把这两张表写成第九个迁移，它同样永远不会跑。故用 `CREATE TABLE IF NOT EXISTS`，这也正是 `_baseline_apply` 的 docstring 早就写下的规则。**未做且明说**：三个模块没有 CLI 命令、没有 REST 路由、不在 `build_storage` 里，出货产品中没有任何东西调用它们，故本行不作任何产品面主张，需要后续行给它一个面 | 单元：两进程不可同持一租约；过期租约被回收且未到期不被抢；同一交易日第二次开跑被主键拒；跨假期的 catch-up 数的是会话不是自然日；存下的旧触发时刻不得左右答案；比日历更早的 `last_fired` 必须具名拒绝而非只答它看得见的部分 | S5, S67, D22 |
| `V2-P5-011` | ~~CORS 方法扩展（当前硬编码只允许 GET/POST，v2 若用 PUT/DELETE/PATCH 会被 CORS 挡）~~ **已修** | 技 | — | **本行在 `c847295` 上实测，并把原文修正得更糟**：手写清单**已经**落后于它所守的路由表，不是「v2 才会」。用 `TestClient` 驱 `create_app()` 做预检，`GET`/`POST` 得 `200`，而 `HEAD`、`PUT`、`PATCH`、`DELETE` 四个一律 `400 Disallowed CORS method` —— 应用今天就声明**四条 `HEAD` 路由**（FastAPI 给每个 `GET` 自动加一条）。实际没坏，因为 `HEAD` 是 CORS 安全列表方法、浏览器根本不预检它；但这正是本行的要害：两处「本服务提供什么」的陈述，没有任何东西让它们保持一致。**修法**：`CORS_ALLOWED_METHODS` 定为 `DELETE/GET/HEAD/PATCH/POST/PUT`（`OPTIONS` 由 Starlette 自己追加）。**不从 `application.routes` 推导**：CORS 不是授权（本仓无任何认证，见 `F101`），多广告一个方法的代价是 `405` 而不是浏览器层拒绝，那是更好的失败；真正不能漂的是反方向，由 `test_every_method_the_route_table_declares_survives_a_preflight` **从运行中的应用读方法**钉住。**刻意不写的断言**：`["*"]` 与显式六元组经 HTTP 面**不可分辨**（Starlette 把 `"*"` 展开成同样六个加 `OPTIONS` 并原样渲染进 `Access-Control-Allow-Methods`），写一条声称能分辨的测试就是「断言存在但分不出两个答案」。**改写成断言的是放宽方法不得顺带放宽的两样**：来源白名单与凭据开关 —— `DELETE` 从表外来源仍是 `400 Disallowed CORS origin`，`access-control-allow-credentials` 在四个方法上一律缺席。**顺带修的排序缺陷**：`SecurityHeadersMiddleware` 原本在 `CORSMiddleware` **外**层，于是它短路的每一条拒绝（包括 `V2-P4-043` 精心措辞的 `413`）都绕过了加 `Access-Control-Allow-Origin` 的那一层，跨源浏览器调用方只看得到一个不透明的网络错误。两者已对调；代价只是预检响应不再带加固头，而预检不渲染任何东西、也没有正文 | 集成：`PUT/PATCH/DELETE` 预检必须 `200` 且方法出现在 `Access-Control-Allow-Methods`；路由表声明的每个方法都必须过预检；表外来源与凭据两条负控必须仍红 | D23 |
| `V2-P5-012` | ~~请求体流式计量 + 补安全头（当前只看 `content-length`，chunked 可完全绕过；缺 HSTS/COEP/CORP；`cli.py:180` 不传 `--no-server-header`）~~ **已修** | 技 | — | **`F100` 在 `c847295` 上复现，且比本行原文更糟**：对着**故意调小的 1,024 字节**上限，一条 chunked 的 `POST /api/v1/research/batches` 携 **36,000,030 字节**得到 `422 json_invalid` —— 那是 **JSON 解析器**的判决，只有在整个正文**已经被读完**之后才够得着；`tracemalloc` 实测该单次请求峰值 **108,346,472 字节**，是正文的三倍（Starlette 把分片攒进 list 再 join）。**修法是真流式计量而非「先读完再看大小」**：未声明长度的正文逐片计数，越顶即**停止调用 `receive`**，传输层不再被要求下一片。**这两者的分辨靠客户端选型**：`TestClient` 会在应用运行前把生成器正文物化（`starlette.testclient` 的 `receive` 调 `httpx.Request.read()`），整份正文作为**一条** `http.request` 消息到达，所以经它「计数器数得对」可证、「读到一半就停」**不可证** —— 拉取次数在任何实现下都一样，那会是一条分不出两个答案的断言。故流式那半改用 `httpx2.ASGITransport`（每次 `receive` 拉一片）驱同一个应用：修后实测 **400 片只拉了 1 片**，读 100 KB 而非 40 MB。声明了 `Content-Length` 的那道闸不变，仍在读体之前拒。**拒绝形状**：两道闸共用 `reason`/`limit_bytes`（`docs/api/http.md` 让客户端 switch 的就是 `reason`），新增 `measured_bytes` 与 `declared_bytes` 并列且恒有其一非空；`measured_bytes` 是正文的**下界**而非大小，措辞里明说，因为其余部分从未被索取。**刻意仍不计量的一种**：发给根本不读正文的路由（`404`、带正文的 `GET`）—— 没有东西去要，也就没有东西被攒起来，已写进文档。**`F102` 三个头**：`Strict-Transport-Security: max-age=31536000; includeSubDomains`（不带 `preload` —— 那是运营者对一个域做的、近乎不可逆的承诺，库不能替一个它没见过的域做）、`Cross-Origin-Embedder-Policy: require-corp`、`Cross-Origin-Resource-Policy: same-origin`。**同一条 finding 的后半也修了**：这些头原是**追加**而非替换，一条自设 `x-frame-options: SAMEORIGIN` 的路由实测产生两行原始头、浏览器读作 `SAMEORIGIN, DENY`；今天全库没有这样的路由，所以追加与替换在出货路由表上不可分辨 —— 这正是它没被发现的原因，测试因此在真 `create_app()` 上加一条这样的路由把两个答案分开。**`cli.py` 的 `--no-server-header`**：`uvicorn.run(..., server_header=False)`；测试打桩的是 `uvicorn.Server.run` 而非 `uvicorn.run`，于是 `uvicorn.Config` 仍被真实构造 —— 拼错的关键字在这里是真的 `TypeError`，而 `server_header` 是从 uvicorn 本会拿去服务的那个对象上读回来的 | 集成：chunked 超限必须是 `413` 且带 `reason=request_too_large`；`ASGITransport` 下拉取片数必须 ≤2；恰好等于上限的 chunked 正文必须完整到达并由路由自己拒（防止「拒绝一切 chunked」）；九个加固头齐备；自设策略头的路由只留一行且是本服务的值；`413` 必须带 `Access-Control-Allow-Origin`；`openalpha serve` 的 `Config.server_header is False` | T17 |
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
| `V2-P5-025` | ~~前端契约漂移守卫自己停止了守卫：`ResearchResult.manifest` 的 schema 文件名停在不存在的 `run-manifest-v2.json`~~ **已完成** | 测 | P4-010 | **肇事行是 `V2-P4-010`（`9f68d65`，2026-08-20），不是 `V2-P4-001`**：它把 `docs/api/schemas/run-manifest-v2.json` 改名为 `run-manifest-v3.json`，并且**没有碰 `web/` 下任何文件**；该行按它自己列出的验收条目关闭，前端契约镜像不在那张单子上，集成者的双序门只跑 pytest，所以没有任何东西报告过它。**归因更正**：`V2-P4-001`/`V2-P4-025`（`5b3383f`，2026-08-18）**做对了**——它同步改了 `web/src/types.ts` 与 `web/src/typesContractDrift.test.ts`（v1→v2）；失手的是下一次升版，也就是说这条同步一直是**手工习惯**而非测试，习惯撑过一次、第二次就漏了，这正是本行补测试而不是补记忆的理由。**实测后果**：`cd web && pnpm test` 自 2026-08-20 起红了五天（`readSchema` 在 `findFieldDrift` 之前抛 `ENOENT`，`1 failed \| 51 passed (52)`），即 manifest 镜像在这段时间内**完全没有**漂移保护。**改名指向 v3 之后实测漂移为零**：镜像只声明 `run_id` 与 `status`，两者在 v2→v3 中都没动；且 `findFieldDrift` 按设计**单向**（只遍历 `tsFields`，`schemaDrift.ts:277`；`schemaDrift.ts:13-14` 与 `typesContractDrift.test.ts:29-33` 都写明了），v3 多出的 16 个属性（`agent_versions`/`alpha_model_versions`/`mode`/`run_manifest_id`/`model_versions` 等）从不被访问。**所以 ENOENT 掩盖的不是镜像漂移，而是守卫自身的存活性**——镜像没有说谎，`run_id`/`status` 都是真话，声明子集是该模块明文许可的；不因此把 16 个 UI 从不渲染的字段抄进 `types.ts`。补的是一条前置测试 `no DriftCheckSpec silently stops checking`：`DRIFT_CHECKS` 里每个 `schemaFile` 必须存在**且**其 `schema_version.const` 经 `schema_document_name` 的同一变换后仍等于文件名。后半条抓的是改名抓不到的那一格：一份留在原地、内容却已升版的文档会正常加载，并**静默地**对着错误的契约做漂移检查。**三次变异实测**：①改回 v2 文件名 → 新测试点名报错并列出实际发行的五份文档（漂移测试同时 ENOENT）；②文件在但版本不符（把 v3 文档复制成 `run-manifest-v2.json` 并指向它）→ **只有新测试红，漂移测试全绿**，正是它单独抓不到的情形；③把 `manifest.run_id` 改名为 `run_ident` → 漂移测试红在 `missing_in_schema: run_ident`。**另四份 schema 全部复核通过**：五份文档的 `schema_version.const` 与文件名逐一相符，`DRIFT_CHECKS` 引用的 5 个文件名全部存在，无第二处失效引用。顺带修 `domain/run_mode.py:36` 的散文（它写着 `run-manifest-v2.json` 承载 mode 全集，而那份文件已不存在）；`domain/schema.py:59` 的 `"run-manifest/v2"→"run-manifest-v2"` 保留不动，因为那是对函数变换的正确举例，不是对文件存在的断言。实测门：`pnpm test` 53 passed、`pnpm lint`/`tsc -b` 干净、`pnpm test:e2e` 4 passed（**离线可跑已复核**：浏览器已在本机 `ms-playwright` 缓存、spec 用 `page.route` stub 了全部三个接口、`webServer` 是本地 vite 127.0.0.1:5173，无任何外部主机）、`pytest tests/unit` 2959 passed | T14 |
| `V2-P5-026` | ~~**版本号被改编号后，已迁移的库永久卡死**：`create_validation_results` 被插进已被占用的版本 2，导致真实库停在 `user_version=4` 再也无法前进~~ **已修（按 schema 实测的对账 + 不可判定即上报 + 冻结版本号→名字映射）** | 技 | P0B-004, P0B-010, P4-111 | **在用户真实 `runtime/state.sqlite3` 的副本上实测（原件全程只读拷贝，从未打开写入）**：`user_version=4`；`schema_migrations` 为 `[(1,baseline),(2,demo_add_runs_archived_at),(3,demo_add_runs_archived_at),(4,create_query_path_indexes)]`；无 `validation_results` 表；`create_app(runtime_dir=…)` 连跑三次仍是 `4,4,4`。**成因已定位到提交与时刻**：`1e54104`（2026-08-07 08:40 EDT）发布的注册表里 `DEMO_ADD_RUNS_ARCHIVED_AT_VERSION = 2`，用户库在 12:33:46 UTC 就是按这份编号盖上 `(1,baseline)+(2,demo)` 的；`6eba39c`（同日 15:01 EDT）把 `create_validation_results` **插进版本 2** 并把 demo 改编号 2→3，于是同一个库在 18:50:18 UTC 把 demo **按新编号 3 又记了一次**（幂等空操作），08-08 13:02:20 记下 4，此后 `_pending()` 只按 `version > user_version` 过滤，版本 2 永远在水位线以下、`validation_results` 永远不会被建，而 `_rewrite_contract_identities` 正以该表为前提 —— **每次进程启动都 defer，无终止条件**。**三条原始判断被实测推翻**：①「重复行说明 `schema_migrations` 没约束住它 docstring 假设的东西」——**错**，`version` 是 PRIMARY KEY 且完好，没有任何版本被记两次；重复的是**名字**，出现在两个不同版本上，因为同一个迁移确实按两套编号各跑过一次，该表约束的正是它声明要约束的。②「版本计数器与审计表对历史各执一词」——**它们彼此完全一致**：`user_version=4`，审计表恰好是连续的 1–4；不一致的是审计表里的**名字**与当前注册表对同一批数字的命名，这只有拿注册表来比才看得见。③「是否是一次性事故」——**不是**：`DROP TABLE validation_results` 后重启会到达同一状态（已作为 CLI 用例固化），任何把迁移**插入**已发布编号的改动都会复现。**同时复核并否定了另一个 agent 的说法**「迁移 4–8 对任何新库都不生效」：全新 runtime 目录实测 `start1→user_version=2 applied=[baseline,create_validation_results]`、`start2→8`、`start3→8`，`require_table` 的延迟按设计工作，**未做任何改动**。**修法与明确拒绝**：(a) `Migration` 增加 `effect_present` 谓词，只许查 `sqlite_master`/`PRAGMA table_xinfo`，**不许信任任何一个计数器**；三个数据改写迁移（`rewrite_contract_identities`/`split_batch_task_items`/`rewrite_manifest_component_planes`）**故意留 `None`**，因为它们的效果是payload 字节，schema 看不见。(b) `run_migrations` 在 pending 循环**之前**跑 `_reconcile`：效果缺失才真跑 DDL 并记 `applied`，效果已在则只记 `verified` 不重跑，**绝不写 `user_version`**（版本早已越过，没有可前进的目标）。(c) 修复记在**新表 `schema_repairs`**，不是塞进 `schema_migrations` —— 后者 `version` 是 PRIMARY KEY，版本 2 **物理上不可能**同时叫 `create_validation_results`；审计表的表结构本身就编码了「版本号含义永不变」这个被打破的假设，**一行历史都没有删改或重写**。(d) `migrate status` 新增 `repaired`/`unrecorded` 两段（文本与 `--json` 同步）；`migrate run` 打印修了什么。**明确拒绝**：不为不可判定的迁移猜答案 —— 重跑身份改写可能损坏记录，不查就记又正是本引擎存在的意义所在，两个都比卡死更糟，于是停下来并上报，交给人。**根因守卫**：`test_no_shipped_migration_may_be_renumbered_or_renamed` 冻结版本号→名字映射；既有的「唯一且递增」守卫在 `6eba39c` 当天是**全绿**的，因为唯一性是单个快照的性质，而编号含义是**跨版本**、由已落盘的库持有的性质，全库没有任何东西在检查它。**顺带修掉自己的一个真 bug**（由变异存活项挖出）：`PRAGMA table_info` **不列生成列**，`runs.mode` 正是 `GENERATED ALWAYS AS … VIRTUAL`，谓词若按 `table_info` 写就永远只会答「缺失」并对着已存在的列反复重跑 —— 改用 `table_xinfo`，并补一条专门钉这条 SQLite 行为的测试。**实测门**：`pytest tests/unit` 3040 passed、1 skipped；`pytest tests/integration` 全绿；`ruff`/`mypy` 干净；`lint-imports` 8 kept / 0 broken；**变异清扫 22 个变异体、22 个被杀、0 存活**（基线 43 passed 先证绿，每个变异体 180s 硬超时，`finally`+`atexit`+信号处理保证不留残体）；真实库副本实测 `4 → 8`，第一次启动即到位，第二三次稳定 | S48, T16 |

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
| S27, S28 | `V2-P4-013`, `V2-P4-026`, `V2-P2-005`, `V2-P1-017` |
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

**已落地（2026-08-18，`V2-P4-001`）**：这条迁移是 `storage/migrations.py` 的第 5 号
`rewrite_contract_identities`。三项升版里只有两项移动存储键（`decisions.decision_id`、
`validation_results.validation_id`），`run-manifest/v1` 因为存储键是调用者给的 `run_id` 而**可以**
读时透明升级 —— 这个不对称本身就是本节结论的用法。另外两项的 upgrade 按名拒绝
（`domain/versioning.py::IdentityRewriteRequiredError`）。**本节没有点到的第四张表**是
`research_reports`：它连 `schema_version` 都没有、形状一字未改，却因为 `report_id` 哈希的 payload
里有 `decision_id` 而同样被重新标识 —— 「升版清单」和「重新标识清单」不是同一份清单。
详见本文件末尾的 `V2-P4-001` + `V2-P4-025` 交付记录。

**更正（2026-08，`V2-P1-017` 落地后）**：上面点名的「horizon 改动经 `SignalFrame`」这一项
**没有**发生身份变更，因此不进入 P4 的重写迁移清单。`V2-P1-017` 把 `horizon` 从
`Field(min_length=1, max_length=64)` **收窄**为 `Field(pattern=HORIZON_PATTERN)`
（`^[1-9][0-9]{0,2}[dwmy]$`，见 `domain/horizon.py`），既没有 bump `schema_version`，
也没有对任何已接受的取值做归一化 —— 收窄一个字段的**定义域**不改变仍然合法的取值的
canonical JSON，所以 `5d`/`10d`/`3m` 三个本仓在用的字面量逐字节不变，`signal_id` 一个都没动
（`tests/unit/domain/test_horizon.py::test_constraining_the_horizon_field_did_not_restate_any_accepted_value`）。
唯一对外可见的变化是 `docs/api/schemas/signal-frame-v1.json` 现在发布了这条 pattern。
本节的结论对 attribution 经 `ValidationResult` 的那一项仍然成立。

这条更正带两个附注（Task 40 审查补记）：

- **收窄的是一个已发布的 v1 schema，且没有 bump 版本。** ID 一个没动，本仓也从未写过
  文法外的取值，但同一个 `signal-frame/v1` 之下，**昨天合法的 `horizon: "whenever"`
  今天经 `read_versioned` 会抛**。对本仓的存量为零风险；对任何在本仓之外按 v1 写过
  `signal-frame` 的存量不是。修法只有两条 —— 迁移那些行，或 bump 到
  `signal-frame/v2`（后者会重写 `signal_id`，就是本节说的那种迁移）。记在这里，
  是因为「收窄不动 ID」成立并不等于「收窄没有对外后果」。
- **若日后把 `horizon` 从 `str` 换成结构化类型**（例如直接存 `ResearchHorizon` 或
  `{"count": 5, "unit": "d"}`），canonical JSON 会变，这一项就**重新回到**
  P4 的身份重写清单。上面的「不进入」只对「仍是同一个 `str`，只是定义域更窄」成立。

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

**已结清（2026-08-18，`V2-P4-025`，与 `V2-P4-001` 同一窗口）**：`RunManifest` 得到
`run_manifest_id`（`stable_model_id`，排除五个「记录但不寻址」的字段），`DecisionLedger` 携带
这个地址。上表的四行在 `tests/integration/test_run_identity.py` 里按同样的方法重跑，后两行由
「不变 ❌」变成「变 ✅」。**本节的方法论结论保留**并且被再次证实：`config_digest` 之所以到不了
`decision_id`，不是哈希写漏了，而是它不是被哈希模型的字段 —— 修法因此是契约形状而不是哈希。
`run_manifest_id` 派生时排除的五个字段（两个墙钟、`status`、`checkpoints`、`environment`）
每一个都在 `RUN_MANIFEST_UNADDRESSED_FIELDS` 里写明理由，并由一条读 `model_fields` 的元审计守着。

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

## 11. `not_yet_knowable` 是分区级判定，一个分区是一整年（P1 阶段验收实测）

第 10 节的方案 B 说「另选一个可达信号建闸门」，而面板平面上最现成的那个信号是
`evaluate_readiness` 的 `not_yet_knowable`：它确实可达（REST 上实测触发过），确实 fail-closed，
也确实由 `as_of` 而非墙钟决定。P2 若采用它，必须先知道它的**判定粒度**。

`catalog.py` 拿一个分区的 `max_available_time`（该分区里最晚变为可知的那一刻）跟 `as_of` 比，
晚于 `as_of` 就**整块拒绝**。它不过滤行。后果：

**任何早于分区自身 `max_available_time` 的 `as_of` 都读不出该分区；而一整年的数据，
那一刻在 12 月。** 完整的 2015 年分区因此在 2015 年内的每一个 `as_of` 上都是 `blocked`，
最早 2016-01-01 才读得出。界限是 `max_available_time` 本身而不是日历年 ——
只装了 1 月的分区从 2 月起就可读 —— 但 `panel_ingest` 的 session census 会拒绝任何缺了
日历所报开市日的分区，所以这个平面真正产出的分区就是整年的，实际规则就是上面这条。
这不是数据问题 —— 同一个分区在下一刻就是 `ready` —— 而是判定的粒度。

对下游的硬约束：

- **P3 因子层**：无法通过 `read_if_ready` 在年中 `as_of` 上算因子；任何 2024-06-30 的因子
  只能由已经收口的年份分区拼出来。
- **P4 walk-forward**：无法让 `as_of` 在一年之内逐步推进并同时读该年分区，年内的每一步都 `blocked`。

**这是刻意的 fail-closed 设计，且有测试背书**（`tests/unit/panel/test_readiness_rules.py::
test_not_yet_knowable_is_partition_level_so_an_as_of_inside_a_year_reads_nothing`）：
`evaluate_readiness` 是纯函数，只看目录元数据，够不到行，所以给行级答案等于对没筛过的行作出承诺。
但**产品后果此前没有在任何地方披露过**，这里补上。

**P2 的第一个决策**（不在 P1 做）：是否把它拆成「分区级闸门 + 行级 `available_time` 过滤」。
这会改变 `read_if_ready` **承诺**什么，而不只是它拒绝什么 —— 因此是一次接口决策，
不是一次实现改动。在做出这个决策之前，把 `not_yet_knowable` 当作 P2 闸门信号是可以的，
但闸门的 `as_of` 必须取在分区年份之外，否则拒绝的原因是粒度而不是被注入的违规。

### 决策（`V2-P3-002`，2026-08-11）：**第二扇门**，不是改第一扇

以上三个候选（拆 `read_if_ready` / 因子层自己过滤 / 只在年边界算）**都没有采用**。落地的是第四条：

- **`read_if_ready` 一字未改**，承诺不变，`panel_ingest` 的十四个 loader 一个都没动，
  `not_yet_knowable` 在那条路径上仍然整块拒绝。
- **新增 `PanelStore.read_visible_at`**：跑**同一张** `evaluate_readiness` 规则表，
  只有当它找到的问题**全部**落在新常量 `ROW_FILTERABLE_ISSUE_CODES`（今天只有
  `not_yet_knowable` 一个）里时，才改用 `WHERE available_time <= as_of` 扫描而不是拒绝；
  任何其它 code（单独或并存）照旧整块拒绝。没有第二张规则表，
  以后加的 readiness code 默认在两条路径上都阻塞。
- **「短读」被说出来而不是被推断**：返回类型是**另一个**类型 `PanelVisibleReadOutcome`，
  带 `withheld_row_count`（拿到行就必然同时拿到这个数）。P2 拒绝行级过滤的理由是
  「过滤后的读交回一个短分区，而这个平面之上每个消费者都把短读成缺数据」——
  那个理由针对的是**沉默的**短读，且对 `build_index_membership` /
  `load_industry_histories` / `build_stock_universe` 依然成立，它们仍走原来的门。
- **可审计**：`tests/unit/panel/test_visible_read_callers.py` 钉住哪些 `src/` 文件可以调它
  （今天一个：`panel_factors.py`），形状与 `test_query_callers.py` 一致；
  且因子引擎**不**调 `query()`，所以 PIT 保证仍在存储层，没有搬到因子层。

代价与残差都已量化并落盘：

- **量级**：fixture 面板上 `read_if_ready` 在年中 `as_of` 整块拒绝，`read_visible_at`
  交回 39 行、扣下 40 行，两者之和等于分区行数（断言如此）。ADR-0002 口径的真实规模上实测：
  5,534 只 × 122 个会话 = 675,148 行的合成 `daily` 分区，`compute_factor` 跑完整个横截面
  （读 + 分组 + 定级 + 求值）**冷 1.95 秒、热 1.91 秒**，约 2.9 微秒/行；身份契约补救后同一形状
  重测为**冷 2.24 秒、热 2.26 秒**（约 3.3 微秒/行，多出的是会话并集与每只标的两次二分查找）。
  写路径远贵于读路径（同一分区的 `write_panel_batch` 至少贵一个数量级，五次测量里有四次贵两个），
  这条**序**是结论；绝对秒数不是 —— 见下方修订。
  「读远便宜于写、且没有矩阵运算」正是「不引入 numpy/pandas」的依据
  （见 ADR-0003 的 2026-08-11 更新与其中的五次测量表）。
  被放弃的两条代价：每个 `as_of` 重建一次面板是单次年度构建的 120 倍（P2 技术验收实测）；
  只在年边界算则 `V2-P3-005` 的 IC 衰减与 `V2-P4-013` 的 walk-forward 每年只有一个观测。
- **不能承诺的部分**：过滤一个**事后**写成的分区，复现的是「已存储的行说当时可知什么」，
  不是「当时真去抓会拿到什么」——上游只要不是 append-only 就会不同（§7 实测
  `fina_indicator` 81.7% 的键多行且四个时钟逐字节相同，`available_time` 分不开版本）。
  这条作为第三条 `KNOWN_STORAGE_LIMITATIONS`
  （`a_visibility_filtered_read_replays_a_partition_that_was_not_there_yet`）落盘，
  因此出现在每一份 `panel doctor` 报告里。

**对 P4 的后果**：walk-forward 现在可以让 `as_of` 在年内逐步推进，
条件是走 `read_visible_at` 并把自己加进那张 allowlist —— 那是一次有评审的动作，不是障碍。

### 修订（`V2-P3-002` 评审，2026-08-11）：上面这个决策有一条被实测证伪的安全性声明

上面写着「任何其它 code 照旧整块拒绝，所以结构性检查一点没削弱」。**这句话是错的**，
而且错在一个一般性的地方，值得连同修法一起留在这里。

`evaluate_readiness` 判的是**分区元数据**；`read_visible_at` 交回的是**那个分区的行子集**。
十三个 code 里有十个的判决只读元数据或 requirement 本身，过滤前后一样；
另外三个（`stale` / `subject_missing` / `date_gap`）的**通过结论**是关于行的，
而那些行可能正好被谓词删掉了 —— 于是「没报错」被搬给了一个它并不支持的答案。
这三个现在由 `SCOPE_SENSITIVE_ISSUE_CODES` 具名，判据写在常量的 docstring 里。

`stale` 是最锋利的一例，而且它不只是范围错，是**数学上打不响**：
`stale` 比的是 `as_of - coverage.last_event_time`，而 `last_event_time` 是整个分区的；
`not_yet_knowable` 触发恰恰意味着分区里有 `as_of` 之后才可知的行，
所以最新事件也在 `as_of` 之后，差值为负。仓库自己的 fixture 面板实测：
`max_staleness` 给 1 小时 / 1 天 / 2 天，三次都不阻塞，而可见切片最新事件落后 as_of **2 天 21 小时**。
端到端更难看：一个 14 行分区（10 行可见、4 行扣下），因子盖 `as_of=2026-06-30` 的戳，
最新输入会话是 2026-01-09 —— **172 天**陈旧度，调用方声明的 `max_staleness=7 天` 从未被看到，
每条观测都写着 `coverage="computed"`。

修法不是改措辞：

- **`read_visible_at` 在扫描之后，对「即将交回的那些行」重跑可重跑的范围敏感检查**
  （`VISIBLE_SLICE_RECHECKS` = `stale` + `subject_missing`），
  走的是 `evaluate_readiness` 调的**同两个函数**（`staleness_issue` / `subject_gap_issue`），
  所以仍然只有一处在算判决 —— 变的是输入（可见切片而不是 coverage 普查），不是规则。
  不合格就**整块拒绝**，理由落在 `PanelVisibleReadOutcome.visible_slice_issues` 上。
- **`withheld_row_count` 不能替代「答案伸到哪里」**，这两个数不相关：
  172 天那个例子只扣下 4 行。所以 outcome 上新增 `visible_last_event_time`，
  它是重跑 `stale` 用的那个证据本身，也是 `max_staleness` 被显式弃权时调用方手里唯一的数。
- **`date_gap` 没有重跑**，理由是结构性的（在 SQL 里把 `event_time` 还原成会话日期
  等于把 `panel_ingest._date_census` 的时区换算抄第二份，而 `openalpha_cn.panel`
  连导入它都不允许），并且有实测边界：今天所有会走到 `read_visible_at` 的路径上，
  重跑都是**空操作** —— `panel_ingest` 里只有 `_price_requirement` 声明 `required_dates`，
  它用 `_sessions_published_through` 夹在 16:30（正是 provider 给 `available_time` 的同一时刻），
  所以要求的会话按构造必然可见（fixture 面板 2026-01-12T04:00Z：要 5 个、回 5 个）。
  残差作为第四条 `KNOWN_STORAGE_LIMITATIONS`
  （`date_gap_clears_on_partition_rows_the_filtered_read_withholds`）落盘。
- **「类型不同，mypy 会挡」被高估了**：`PanelVisibleReadOutcome.rows` 与
  `PanelReadOutcome.rows` 的静态类型**完全相同**，而 P2 点名的三个消费者吃的是 rows 不是
  outcome，所以 `stock_universe_from_panel_rows(list(filtered.rows), ...)`
  在 `mypy --strict` 下零报错。类型只挡「把整个 outcome 传给另一个 outcome 的读者」这一种错。
  **真正的障碍是那张 AST allowlist**，措辞已按此改准，并由
  `test_the_two_read_outcomes_expose_rows_at_the_same_static_type` 钉住。
- **`available_time` 为 NULL 的行**曾经从「可见」和「扣下」两半里同时消失
  （三值逻辑：`NULL <= x` 与 `NULL > x` 都不成立），于是被断言的
  `visible + withheld == row_count` 恒等式为假（实测 2 + 0 ≠ 3），且该行无声蒸发。
  扣下侧的谓词改成 `> ? OR IS NULL`：fail-closed（这种行在任何 `as_of` 都不可见）
  且恒等式恢复，代价是 `withheld_row_count` 里包含**永久**扣下的行，这一点已写进 docstring。
- **allowlist 检测器自述的残差说反了**：实测「参数被 splat」**照样抓得到**
  （`*args` / `**kwargs` / 两者并用 / `self._store.read_visible_at(...)` 全部命中，
  因为匹配的是被调用表达式而不是参数形状）；真正漏网的是绑定方法别名
  （`reader = store.read_visible_at; reader(...)`）与 `getattr`。两个方向都已写成断言。
- **那条新 `KNOWN_STORAGE_LIMITATIONS` 的严重性补齐了两处**：一是**量级**与机制写在一起
  （81.7% 是 `fina_indicator` 受影响键的**占比**，且偏差是单向的 —— 每个这样的键读回的都是
  重述后的值），二是明说**这条路径是该偏差第一次变得可达**（`read_if_ready` 在年内每个
  `as_of` 都整块拒绝，所以在 `read_visible_at` 之前，年中重放不是「会做错」而是「做不到」）。
  对应测试从「`"81.7%"` 出现在字符串里」改成要求这两件事都在。
- **写路径 288 秒这个绝对数字不再被引用**。同一个量现在有五次测量：288 秒（原始）、
  56.7 秒（评审，同样 675,148 行）、234 秒（另一名评审在 1/5 规模外推）、
  350.6 秒（身份契约补救中复测）、617.9 秒（本次修复中复测，10 个存储列）。
  列数与机器不同可以解释一部分，一个数量级以上的离散解释不完，
  而且五次都不是在写明的受控条件下取的。
  对 1.95 秒的读来说这五次分别是 148 倍 / 29 倍 / — / 180 倍 / 317 倍，所以现在的说法是
  「至少一个数量级，五次里四次是两个数量级」，而不是原来那句平铺的「两个数量级」。
  「写 ≫ 读」这条序成立并保留，绝对秒数不做承诺。读路径那一侧是可复现的
  （评审自建同规模分区实跑 1.61 秒冷 / 1.60 秒热，比自述的 1.95 秒更快）。

**因子引擎侧的后果**：`compute_factor` 现在**拒绝**一个弃权了 `max_staleness` 的输入
requirement，理由与它早就拒绝弃权 `required_fields` 的理由同形 ——
弃权会让 readiness 放行一个回答不了这个因子的分区，只是这次没有下游的 binder 错误兜底：
构建会成功，每条观测写着 `computed`，而戳在上面的 `as_of` 比背后最新的会话晚几个月。
`factor_observation_requirement`（读回**派生**分区）的弃权保持不变，它的理由（派生分区没有上游可落后）
仍然成立。

### 合并补救（P3 两组并行补救合流，2026-08-11）：那条重跑的界原本判在错误的范围上

上一节的重跑修好了「界打不响」，但把它判在了**一个分区**上，而 `max_staleness` 是
`ReadinessRequirement` 的字段，那个 requirement 点名的是**一组年份**。
`evaluate_readiness` 一直是在这**整组**上判的（`max(coverage.last_event_time)` 取跨年最大、
subjects 取并集），于是同一个 requirement 在两条路径上得到了相反的判决 ——
两年探针、`as_of=2026-01-08`、界 5 天实测：
`assess_readiness(years=(2025, 2026))` 是 `ready`（答案伸到 2026-01-07，落后 21 小时），
而 `read_visible_at(..., year=2025)` 报 `stale`，引的是 7 天 21 小时 ——
那是**回看窗的跨度**，不是**答案的年龄**。

**只有跨年 requirement 能看见它**：重跑落地时存在的每一个测试都只点名一个年份，
而单年份下「按分区判」与「按整组判」恰好重合。暴露它需要两件事同时成立 ——
一个跨年 requirement（另一组的 `V2-P3-003` 探针），加上那个 requirement 必须声明界
（这一组刚把弃权设成拒绝）。两组各自都对，合起来才有反例。

**后果直接落在 `V2-P3-012`**：一月求值的 120 会话动量因子必须把上一年放进
`requirement.years`；若界按分区判，唯一能让这个构建通过的界必须宽到覆盖整个回看窗
（约六个月），而那个宽度足以让上面 172 天那个构建重新通过 —— 修好的 C1 会被重新打开。

**修法是范围修正，不是放松**：`read_visible_at` 把重跑的两项检查按 `requirement.years`
汇总后再判，用的是 `evaluate_readiness` 本来就在用的同两个归约；每多一个年份多一次聚合
（走已经打开的那条连接），投影仍然只做被点名的那一年。两项检查**都**弃权的 requirement
不汇总任何东西（`evaluate_visible_slice` 那时无论拿到什么都返回 `()`），所以读回派生分区
那条路径的开销与汇总出现之前完全一致。两个方向都钉住了：

- `test_the_freshness_bound_is_decided_over_every_year_the_requirement_names` ——
  跨年读得出答案，而**同一批分区**在 `years=(2025,)` 下两条路径依旧都报 `stale`；
- `test_a_bound_still_refuses_a_multi_year_answer_that_is_old_in_every_partition` ——
  172 天那个形状搬到两年范围上，requirement 的**每一个**年份都仍被拒；
- 引擎一侧的成对断言在
  `test_a_declared_freshness_bound_survives_the_cross_year_window_it_has_to_allow`。

`VISIBLE_SLICE_SCOPE` 的措辞从「the rows this read returns」改成
「the rows its requested years return」：旧措辞会在一次 `year=2025` 的拒绝里，
把另一个分区的 reach 说成「这次读返回的行」。

### 排序约束（`V2-P3-001`/`002` 复审，2026-08-11）：因子契约的字段必须在 `V2-P3-014` **之前**加完

`FactorDefinition` 与 `FactorBuildManifest` 都由 `stable_model_id` 对**自己声明的字段**做内容寻址，
而 `domain/versioning.py` 的 `ContractVersions` **刻意没有**为它们注册（它们从不以 JSON 行存储）。
两件事合起来意味着：**给任一模型增删一个字段，就作废所有已存储的 `factor_id` 与 `manifest_id`，
且没有回迁路径** —— 存储的观测行里那一列没有任何本仓库拥有的东西能改写。

因此这是一条**排序**约束而不是禁令：这个契约将来需要的字段必须在 `V2-P3-014`
写下第一份不可变实验制品**之前**落地。今天没有任何生产因子分区，代价为零；`014` 之后代价是一个
没有回头路的语料。已知还欠一个字段：`V2-P3-009`..`011` 的 EP / ROE 需要一个**报告期**维度
（"as_of 时可知的最新一期财报"不是会话计数），它今天缺席是因为没有读者 —— 这条约束的第一个考验。

本次复审已经用掉了这个窗口一次：`max_window_sessions`（回看窗允许跨越多少个面板会话）、
`subject_digest` / `universe_digest`（横截面与 universe 的集合，不只是计数）、`direction`
与 `max_window_sessions` 落到 manifest 上，都是在这次一起加的。

### 复审结论（`V2-P3-001`/`002`，2026-08-11）：身份契约的两半

原实现只测了一半 —— 「声明的字段都进身份」。另一半「决定输出的东西都被声明了」以及
「没变的东西不能移动身份」各自都有实测的反例：

- **碰撞**：manifest 只记 `subject_count` / `universe_count` 不记集合，两次横截面不相交的构建
  `manifest_id` 逐字节相同，后写的**静默覆盖**前写的观测。
- **漂移**：`FactorInputRef.batch_digest` 哈希了 `fetched_at`，输入分区**行完全没变**的一次重抓
  就会移动所有派生的 `manifest_id`；而既有构建不许被丢弃，于是重算后的构建**永久写不进去**，
  旧的也已无法从存储再推导 —— 没有受支持的恢复路径。
- **不可见**：可见面板会话数少于回看窗时，整个横截面 `insufficient_history`
  的构建不抛不告警照写（实测：120 会话因子、`years=(2027,)`、只读到 36 行）。
  这正是跨年窗口的形状 —— 一月份算 120 会话窗必须把上一年放进 `requirement.years`。
- **写入粒度**：一份共享的 `factor_observations` 数据集让一个分区等于「一年 × 全部因子」，
  17 个因子 × 244 个 as_of × 5,534 只 = 22,955,032 条观测，实测经
  batch/merge/`to_rows()` 峰值 **14.9 GB**。因子进数据集名后回到一因子一年（0.9 GB 峰值）。

修复后 `compute_factor` 的**签名本身**成为审计对象：每个参数要么被证明会移动 `manifest_id`，
要么带着理由出现在豁免表里，加第十个参数会让审计变红。

### `V2-P3-004` 复审（2026-08-12）：中性化残差在其覆盖年内不可见，`V2-P4-013` 因此只能做年度

第 11 节开头那条「分区级判定」在因子层由 `read_visible_at` 解掉了一半，但**中性化把它变回了整块**，
而且是从**输出**这一侧变回的 —— 这条此前只被记成「两个外部输入在年中读不出来」，
漏掉了真正致命的第二跳。

两条事实相乘：

1. `neutralized_observation_batch` 把每一行的 `available_time`（以及 `event_time` /
   `ingested_time` / `revision_time`）都设成**构建的 `as_of`**。派生行没有自己的事件时刻，
   这个设计本身是对的，与 raw / processed 两层一致。
2. 而构建的 `as_of` **必须** ≥ 该年 `daily_basic` 分区的 `max_available_time` ——
   因为 `load_daily_valuations` 走 `read_if_ready`，年中任何 `as_of` 都被整块拒绝
   （`KNOWN_NEUTRALIZATION_LIMITATIONS`
   `.the_industry_input_is_read_whole_partition_so_a_mid_year_as_of_can_be_refused`）。
   而一年分区的 `max_available_time` 就是**该年最后一个会话**。

   > **后续（2026-08-24，由 P4 技术验收实测点出）**：这一条的两半都已被撤回，原文按本节惯例保留。
   > `V2-P4-026` 把 `load_daily_valuations` 改到 `_read_visible_price_session` 上，估值那一半不再
   > 整块拒绝；`V2-P4-027`/`028` 把 `index_member_all` 也放到按日的门上。所引 code 已两次改名，
   > 今名 `a_stored_membership_year_left_unread_refuses_the_day_rather_than_answering_it`。
   > **这是 `d748796` 漏掉的同一类缺陷**：那次提交只扫了 `src/` 与 `tests/`，却在提交信息里写成
   > 「全树仅此一处」，实为 3 个文件 5 处；另两处（第 246、1046 行）是正当的改名史，本处不是。
   > 没有任何可执行的东西会抓到它 —— `docs/` 不在任何 code-binding 审计的扫描范围内。

合起来：**年 Y 中任意交易日的残差，其 `available_time` 都落在 Y 年最后一个 session 之后。**
而 `load_neutralized_factor_observations` 走的是 `read_visible_at`（**行过滤**），
所以年内任何 `as_of` 读回来的是**空**，不是报错 ——
`tests/integration/panel/test_factor_neutralizations.py::`
`test_a_neutralised_row_is_invisible_before_the_as_of_it_was_computed_at` 正好断言了 `earlier == ()`。
这是最坏的一种失败形状：一个看起来合理的短答案。

**下游后果是分级的**：

- **`V2-P3-005`（IC 衰减）不阻塞。** 残差的**内容**是干净的 —— 每一行用的是 `day` 当天的行业、
  当天的市值、当天的 processed 值 —— 所以 IC 与衰减曲线本身不会被前视污染。
  丢掉的是**时间戳的诚实性**：没法在 `as_of=2026-06-30` 问「当时我手上有哪些残差」，答案永远是零。
  **但 `005` 必须在自己的文档里写明它读的是年末快照**，否则它的曲线会被读成逐日可得的。
- **`V2-P4-013`（walk-forward）是真正的阻塞。** 年内每个 rebalance 点读到空集；
  唯一能读到东西的 `as_of` 是 ≥ 年末，此时该年 12 个月的残差**同时**出现 ——
  粒度从「逐日可见」塌成「逐年可见」。**目前只能做年度 walk-forward。**

**为什么不在 `V2-P3-004` 解决**：两条可行的修法都动 `V2-P1` 的存储契约，不是因子层的事 ——

- 给 `daily_basic` 换分区粒度（年 → 月/日），让 `max_available_time` 不再是整年的末端；或
- 给 `load_daily_valuations` 一条「只读到 `day` 为止」的显式门，即一个 as-of 敏感的会话级读。

**不能采用的第三条**：对这两个外部数据集直接改用 `read_visible_at`。
`index_member_all` 分不出「被扣住的行」和「不存在的行」——
`SecurityIndustryHistory.answerable_through` 就是为这个存在的 ——
行过滤会把一个 fail-closed 的拒绝变成一个看起来合理的短答案，
正是 `tests/unit/panel/test_visible_read_callers.py` 要求每个新调用方回答的那个问题上答「不能」。
这条不松。

**已立为 `V2-P4-026`，且是 `V2-P4-013` 的硬前置。**
在它落地之前，**`V2-P3-005` 与 `V2-P3-009`..`013` 都不得在这条上再叠代码**
（例如按「残差逐日可见」写调度或读路径），否则等 `P4` 发现做不了月度 walk-forward 时要改六个地方。

#### `V2-P4-026` 结案（2026-08-17）：走了显式门，换粒度被实测否决，瓶颈搬到 `index_member_all`

上面两条修法**实测比过了**，选了第二条。

- **换分区粒度（年 → 月/日）被否决，理由是一条 fail-closed 守卫。**
  `panel_ingest._session_census` 的下界写死 `date(year, 1, 1)`，其 docstring 自己解释了为什么：
  「下界是该年自身的开始，不是批次的第一行 —— 一个三月才开始的分区正是这个守卫存在的理由，
  按自身第一行截断会把这个洞定义掉」。所以一个只装六月的分区会被
  `_refuse_missing_price_sessions` 以「缺 1..5 月每一个开市日」整块拒绝。
  要换粒度就必须把这条下界从「年」改成「分区自身的跨度」，那是**放宽一条既有 fail-closed 守卫**。
  代价还不止于此：`year` 是 `panel_partitions` / `panel_partition_coverage` /
  `..._subjects` / `..._fields` / `..._dates` / `..._revisions` 六张表的列，
  是 `ReadinessRequirement.years`、`read_if_ready(year=)`、`write_panel_batch(year=)` 的轴，
  `src` 与 `tests` 合计 406 处 `year=`、22 个 requirement 构造器、16 个数据集全部要跟着动。
- **显式门（选中）**：`load_daily_valuations` → `panel_ingest._read_visible_price_session` →
  `store.read_visible_at(requirement, year=day.year, columns=..., filters={"trade_date": day})`。
  这是 `src/` 里**第一个**给 `read_visible_at` 传 `filters` 的调用方，而这正是它安全的原因。

**为什么行过滤对 `daily_basic` 安全、对 `index_member_all` 不安全 —— 这是形状差异，实测得到：**
`providers/tushare.py::_daily_close_timeline` 把每一行的 `available_time` 定在其 `trade_date`
当天的 16:30，所以**一个会话的所有行共享同一个可得时刻**；而 `_build_visible_census_sql`
是在调用方自己的 `filters` 内部数被扣住的行。于是会话读是**全有或全无**：
实测生成面板、`as_of = 2026-01-12T04:00Z` —— 2026-01-09 返回 7 行、`withheld_row_count == 0`
（第八只当天停牌，**根本没有行**），而 2026-01-12 / 13 / 16 各返回 0 行、`withheld_row_count == 8`。
「被扣住」与「不存在」是两组不同的数字。而且不是只看见差别就算数：
0 行且有扣住 → **具名拒绝**；部分可见部分被扣住（全有或全无被打破）→ **具名拒绝**；
`day` 自身的 16:30 还没到 → 碰分区之前就**具名拒绝**（`_sessions_published_through`，
与 provider 和 `_price_requirement` 用的是同一个常量）。
`index_member_all` 没有这个形状，所以那一半**没有**动。

**第一版把 `max_staleness` 的语义悄悄换了，全量套件把它证伪，已改回来**：
`read_visible_at` 会在它将要返回的行上重判 `stale`（`VISIBLE_SLICE_RECHECKS`），
而这里的行只有一个会话，于是 `max_staleness` 的判据从「`as_of` − 分区最新事件」
变成了「`as_of` − `day` 当天 15:00」—— 那不是 `daily_requirement` 让调用方上记录的那个界
（它说的是「这个面板没有落后于市场」），而且会让一对孪生 loader 对同一个参数在两个尺度上作答。
实测后果：`tests/integration/panel/test_panel_gate.py::test_naming_the_session_after_the_hole_still_blocks_and_the_window_is_two_sessions_wide`
在 `panel doctor` 自己推导的 daily 界下，把 2026-01-12 这个会话在 `as_of=2026-01-17` 上判成
`stale`，而同一个 store 上的 `load_daily_bars` 照答不误 —— 一条凭空造出来的 finding。

**改法是两步而不是一步**：先用调用方自己的 requirement 跑 `assess_readiness`
（同一个函数、同一张规则表、同一个分区尺度，即 `read_if_ready` 会给出的那个判决），
`ROW_FILTERABLE_ISSUE_CODES` 以外的任何 code 一律拒；再把
`replace(requirement, max_staleness=None)` 交给 `read_visible_at` 做行过滤。
**在这里免掉那次重判不是 `V2-P3-002` 复审关掉的那种 fail-open**，理由是这个数据集特有的：
`_price_requirement` 声明了 `required_dates` 并按同一个 16:30 截断，
所以「分区落后于市场」由 `date_gap` 在分区尺度上、在任何 `as_of` 上（含年中）抓住，
比一个时长界更锐利；
`tests/integration/panel/test_daily_panel_ingest.py::test_a_partition_that_has_fallen_behind_the_market_is_refused_at_a_mid_year_as_of`
驱动这一条，
`test_the_declared_freshness_bound_is_decided_at_the_scope_the_other_door_decides_it`
则在同一个 store 上让两扇门对 60 天 / 30 天两个界给出**一致**的答案。
真正没有被守住的只剩「答案本身有多旧」，即 `as_of - day` —— 那是调用方自己传进来的两个参数的函数，
不需要任何 store 就能算。

**结果**：年中 `as_of` 上能端到端建出并读回中性化残差
（`tests/integration/panel/test_factor_neutralizations.py::test_a_residual_built_at_a_mid_year_as_of_is_visible_at_that_same_as_of`），
一个覆盖年内可以有**两个以上**独立的 `as_of` 点，`min_as_ofs = 2` 因此在**单个年份内**可满足
（`test_two_in_year_builds_give_the_ic_floor_of_two_as_ofs_two_points_inside_one_year`）。

**瓶颈搬家了，且是搬到了本条明确不解的那一半。** 实测：同一份生成面板，
只要加一条 2026-01-14 起效的成员关系，`index_member_all` 的 2026 分区
`max_available_time` 就变成 2026-01-13T16:00Z，`as_of = 2026-01-12T04:00Z` 依旧
`not_yet_knowable` 整块拒绝 —— 此时 `daily_basic` 已经不再贡献任何拒绝。
真实语料上那是年度成分股调整（613 条 2021-07-30 起、255 条 2022-07-29 起）。
**已立 `V2-P4-027`。** SW2021 的 2021-12-13 可得性地板仍在，它现在是最外层的界
（`KNOWN_NEUTRALIZATION_LIMITATIONS.no_cross_section_is_neutralisable_before_2021_12_13`），
但**在该地板之内真正卡住月度/日度粒度的是上面那条分区级判定**，所以 `027` 覆盖两者。

**受影响的声明已逐条处理**：四个注册表里的
`neutralised_residuals_are_read_at_a_year_end_snapshot` 改名为
`a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule`（`KNOWN_IC_LIMITATIONS`、
`KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS`、`KNOWN_EXPERIMENT_LIMITATIONS` 各一条，
`KNOWN_REDUNDANCY_LIMITATIONS` 的
`a_cross_tier_pair_correlates_one_point_in_time_side_against_one_snapshot_side` 收窄），
`KNOWN_NEUTRALIZATION_LIMITATIONS` 的
`the_two_foreign_inputs_are_read_whole_partition_so_a_mid_year_as_of_is_refused` 改名为
`the_industry_input_is_read_whole_partition_so_a_mid_year_as_of_can_be_refused`。
每一条都保留了被撤回的原文，因为「哪句话被实测证伪」本身是记录的一部分。
（**后续**：`V2-P4-028` 把这条的剩下一半也撤了，该 code 第二次改名为
`a_stored_membership_year_left_unread_refuses_the_day_rather_than_answering_it`；
`KNOWN_FACTOR_RUN_LIMITATIONS` 的
`the_builder_cannot_produce_a_residual_before_its_years_stored_horizon` 同时改名为
`the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed`。）

### `V2-P3-004` 复审（2026-08-12）：三个被六处引用的「实测」数字复现不出，已撤回

`market_cap_measure` 与 `market_cap_scale` 两个声明字段，原先各带一个点估计
——「换 measure 动 **0.0196**」「换 scale 动 **0.195**」，都挂在「残差 rms **0.995**」上 ——
出现在 `MarketCapMeasure` / `MarketCapScale` 的 docstring、
`KNOWN_NEUTRALIZATION_LIMITATIONS.the_residual_is_orthogonal_to_the_design_and_not_to_size_itself`、
`_refuse_a_cross_section_that_is_not_this_panels` 的报错文案、台账 `notes`，
以及**出厂 `INDUSTRY_AND_SIZE.summary`（该字段进 `neutralization_id` 内容地址）**，共六处。

**复现不出**。这三个数只可能来自 `tests/unit/test_factor_neutralization_rules.py::_panel`
这个合成探针，而该探针在它自己的种子（`_panel(19)` + `Random(23)`）上给出的是
rms **1.0021**、measure gap **0.0561**、scale gap **0.7924**。
扫 200 个 panel seed × 200 个 circ seed，没有任何一组同时给出 `(0.995, 0.195)`。
更要命的是这个量本身不稳定：scale gap 在 200 个种子上 min **0.0037** / median **0.2314** /
max **1.6738**，**跨两个数量级**。而它的来源是合成的 ——
所谓 `log(circ_mv)` 是探针里现编的 `Normal(0.7, 0.25)` 流通比例，**从没读过真实 `circ_mv`**；
而 `MarketCapMeasure` 的 docstring 先陈述 51,708 行真实探针（那是关于**空值**的），
紧接着给出 0.0196，读起来像是同一个真实语料的结论。

**修法是撤回而不是重测**（真实语料需要联网 token，非 e2e 测试不得联网）：

- 六处全部改成「这是一个会实质移动残差的声明选择，量级以**地板**断言而非以数字陈述」，
  并点名断言它的测试；
- 测试从单种子两条地板（`> 0.005` / `> 0.05`）扩成 **8 个固定种子的扫描**，
  断言每个种子都过地板，**并断言离散度本身**（`max > 10 * min`）——
  把「这个数不稳定」变成一条会红的断言而不是一句说明；
- 实测区间写进 `MEASURE_GAP_FLOOR` 的 docstring：measure `0.0067..0.0591`、
  scale `0.0210..1.2221`，残差 population sd `0.985..1.005`。

**`INDUSTRY_AND_SIZE.summary` 改动会移动 `neutralization_id`**，
而 `summary` 进身份是 `V2-P3-001` 起的继承缺陷。此刻做代价为零：
`panel_neutralization` 在 `cli.py` 与 `scripts/` 里零引用，**全库没有任何已存的中性化分区**。
这也是这条**必须现在做**的原因 —— `V2-P3-014` 写下第一份不可变制品之后就没有回头路。

**顺带修正两条措辞级的过度一般化**（同一次复审）：

- 「仿射重标定至多移动 4.44e-16」不是对整个仿射族成立的界。z-score 化与 `1000x+7` 确实是
  **4.44e-16**，但 `1e-6*x - 3` 实测 **8.4e-12** —— 把回归元缩小六个数量级，
  就在 `x - mean_x[g]` 这一步损失六个数量级的相对精度。声明改成「重标定不改变**答案**，
  不是不改变**比特**」，并由 `EXTREME_RESCALING_BOUND` 与一条新断言钉住。
- Gram 对角线比 `1.37e15` 是 `_panel(19)` 的，而引用它的
  `test_the_closed_form_reproduces_a_dense_least_squares_solve` 驱动的是 `_panel(7)`（实测 **2.35e15**）。
  数字本身是真的，归属错了；两个种子现在都写明。

### `V2-P3-014` 前置（2026-08-12）：报告期维度落地，`summary` 退出内容地址

上面「排序约束」那一节的窗口到期了。两件事**一起**做，因为两件都移动 `factor_id`，
并级联移动 `manifest_id` / `transform_manifest_id` / `neutralization_manifest_id`；
`ContractVersions` 刻意不为这四个契约注册，存量行没有回迁路径，分两次就要付两次。
**今天代价为零**：全库没有任何因子 / 预处理 / 中性化分区，`cli.py` 与 `scripts/` 对三者零引用。

#### 一、报告期 reach（`lookback_periods` / `max_window_periods`）

**为什么会话轴不够 —— 一次单引擎 A/B 实测**（`tests/integration/panel/test_factor_report_periods.py`）：
财报行的四个时钟全是 `ann_date`（`providers/tushare.py#_announcement_timeline`），
所以在会话轴上，「年报与一季报同日披露」就是同一 `(subject, event_time)` 键的两行，
`compute_factor` **整个构建直接抛**。同一份语料换个数据集名写第二遍（落到报告期轴），同样的行算得出来。
这不是关于某个已不可运行的旧版本的论证，是同一个引擎的两次调用。

**加了四个字段，两两成对，全部可空**：

| 字段 | 读者 |
|---|---|
| `lookback_sessions` / `max_window_sessions`（改为可空） | `_classify` 的会话窗形成与跨度检查；`_refuse_a_panel_narrower_than_the_lookback`；manifest 投影列 |
| `lookback_periods` / `max_window_periods`（新增） | 同上，报告期轴；`FactorBuildManifest` 的两个新投影列 |

**每条 reach 当且仅当 `required_fields` 把该因子放到那条轴上时才允许声明**
（`validate_each_axis_is_declared_exactly_when_it_is_read`）。
两个方向都会红：为没读的轴声明 reach，就是一个 `factor_id` 里没有任何分支能读的数
（`001` 拒绝报告期维度的原话「a field with no reader」）；为读了的轴不声明，就是无界窗口。
`V2-P3-010` 的四个质量因子只读财报、**不声明会话 reach** —— 这是把 `lookback_sessions`
改成可空的唯一理由，而且它们是本仓库第一批真正走这条分支的出厂因子。
（本行原文写的是「ROE 只读 `fina_indicator.roe`」；`010` 交付时论证并实测否掉了那个读法，
理由见下方 `V2-P3-010` 小节 —— 结论不变，只是列换了。）

**同比不是第三个维度**：`lookback_periods=5` 的窗口里 `[-5]` 就是去年同期，加速度再退一步；
让 `window[-5]` 真的是去年同期的，是 `max_window_periods == lookback_periods`
这条「窗口内不缺期」。**单季 vs 累计是因子自己的事**，而它需要的连续性前提是契约的事 —— 同一条约束。

**多行键的 fail-closed 没有被削弱，实测**：同一 `(subject, period)` **同日**两行仍抛
（`fina_indicator` 81.7% 的键是这个形状，且没有 `update_flag` / `f_ann_date` 可排序）；
**不同日**的两行按 `StatementHistory.filing_for` 的规则取较晚公告。这条规则在引擎里是第二份实现 ——
复用 `statement_histories_from_panel_rows` 会让只读一列的因子拉全部十列 ——
所以配了一条**运行期审计**把两份实现放在同一份语料上对齐
（`test_the_engines_period_selection_is_the_domains_filing_for`）。
停止抛的只有「同日两个**不同期次**」，那本来就不是重复键，正是它让普通的 `income` 输入读不了。

**报告期轴不为自己新增覆盖码**：期次数量不足与期次跨度超限都是 `insufficient_history`，
靠 `input_period_first/last` 在存储行上区分（前者无窗口，后者带着被拒的窗口）——
`max_window_sessions` 的先例原样复用。（后来 `V2-P3-018` 确实加了第六个码 `ambiguous_filing`，
但它回答的是另一个问题 —— 「发布方说了两遍且不一致」而不是「历史不够」——
所以这一段的判断没有被推翻，见下方 `V2-P3-018` 小节。）
**面板期次日历是可见读返回的 `report_period` 并集**，理由与 `max_window_sessions` 数面板会话一样：
自造财季算术会是第二本与分区打架的日历，还得裁决非季末 `end_date` 属于哪一季。

#### 二、`summary` 移出三个内容地址

`FactorDefinition` / `FactorTransformSpec` / `FactorNeutralizationSpec` 的 `summary`
都进各自的内容地址，于是**改一个错别字会移动每个已存 build 的身份而不改变任何一个数字** ——
正是 `FactorTransformManifest` 拿来拒绝 `date_timezone` 的那条
「reaches the identity and decides nothing」。
`V2-P3-003` 复审记为「继承约定」放过；`V2-P3-004` 为撤回三个复现不出的数字**有意违反**了
`FactorNeutralizationSpec.summary` 自己那条「改串必须升版本」，并记下了例外与到期时间
（`V2-P3-014`）。**这里就是到期。**

**修法是契约形状的改变而不是哈希的改变**（`exclude=True` 正是第 9 节 `config_digest` 的形状）：
prose 变成 `domain/factor.py::FactorNote`，一个 `stable_model_id` 从不作用其上的冻结 dataclass，
由三个注册表以 `notes=` 携带，`validate_notes` 审计（为未声明契约写的 note 是「关于虚无的散文」；
同一契约两条 note 让 `note_for` 变得任意）。三者处理一致，因此不再有
「三个身份契约互相不一致」这个顾虑。

**两者现在可区分，且都有实测**：只差散文的两个注册表，`factor_ids` **逐字节相同**；
只差一个设置的，`factor_id` 移动。并且散文**根本进不来** —— `extra="forbid"` 让
`FactorDefinition(summary=...)` 直接被拒，所以这是结构性质而不是约定。

`FactorNeutralizationSpec.summary` docstring 里那条例外条款随字段一起删除；
`INDUSTRY_AND_SIZE_NOTE` 逐字保留了当时的撤回文字。

### `V2-P3-014` 前置复审（2026-08-12）：契约形状成立，三条引擎行为被实测证伪

上一节的字段那一半站得住 —— 轴等价的四个失败角全挡、四个上界两侧全钉死、
`None`/`0` 三处不混淆、身份迁移三条逐字节可复算、全树无硬编码身份字面量。
**被证伪的三条全在引擎里，修它们不需要动任何字段，因此没有第二次身份迁移。**

#### M-1：同日多行的 fail-closed 依赖分区行序

上一节写的是「同一 `(subject, period)` **同日**两行仍抛」。实测：只有当两个同日行都在
该期次的**较晚公告之前**被扫到时才抛。判重坐在重述分支**后面**
（`announcement < previous` 先 `continue`，`announcement == previous` 才抛），
所以 `announced[key]` 一旦被更晚的公告抬高，两个同日行都被静默丢弃。
同一份三行语料，只改写入顺序：

```
[dup, dup, later] -> 抛
[later, dup, dup] -> computed 555.0
[dup, later, dup] -> computed 555.0
```

`panel/store.py::read_visible_at` 的可见读是 `SELECT … FROM read_parquet(?) WHERE … <= ?`，
**没有 `ORDER BY`**，分区是多 row-group、DuckDB 可并行扫描 —— 行序既不由调用方也不由 provider 决定。
**不抛的那两支给出的值（555.0）是对的**，所以非确定的不是数字而是**构建成败**，
而 `V2-P3-014` 的不可变制品必须可复现。

判重改为按 `(subject, point, announcement)` 三元组决定，构造上与扫描序无关；
会话轴上 `point == announcement`，三元组退化成原来的键，行为逐字不变。

#### M-2：`max_window_periods == lookback_periods` 不等于「窗口内无缺报」

这是 `V2-P3-011` 整个 `window[-5]` 论证的前提。跨度原本量在**面板期次集**上 ——
可见读返回的 `report_period` 并集 —— 而**没有任何证券填过的那一期不在这个集合里**。实测：

```
lookback_periods=5, max_window_periods=5，某证券漏报 2024-12-31
[没有别人填过那期] -> coverage=computed value=0.5555555555555556
    window 2023-12-31..2025-03-31，[-5] 到 [-1] 相隔 15 个月
    报出的「同比」是 140/90-1，连续时应是 140/100-1
[有一个证人填了那期] -> coverage=insufficient_history
```

也就是说这条契约买到的是「相对于本次读到的那批行没有缺报」——
**一个 build 安全与否取决于分区里别人的构成**。上一节那句
「面板期次日历是可见读返回的并集，理由与 `max_window_sessions` 数面板会话一样」
就是把这个洞盖住的句子，**类比本身是错的**：会话并集**就是**交易日历（每只证券每个开市日都有报价，
并集里缺的一天是市场没开），期次并集没有任何东西保证稠密。

`panel_factors::_period_span` 改为在**财季网格**上计数（`FISCAL_QUARTER_ENDS`）：
A 股会计年度即公历年度，两个期末之间隔几个季度是**不读任何一行就能知道**的。
`_report_period` 同时拒绝非季末的 `report_period` —— 把它归入某一季正是引擎拒绝自造的那本日历，
而且会静默把 2024-05-15 与 2024-06-30 并成一个点。
新度量**恒不弱于旧的**：每个面板期次都是季末，所以面板点是网格点的子集。
会话轴保留面板会话度量，非对称是论证过的而不是将就。

#### M-3：引擎的 `filing_for` 复制品没有 `answerable_through` 视界

`panel_ingest.load_statement_histories` 把 `store.registered_years(dataset) - requested`
的第一个未读年份减一作为 `answerable_through`，`filings_on` 据此拒绝其后的日子
（`KNOWN_FINANCIAL_STATEMENT_LIMITATIONS.a_partial_year_read_answers_from_inside_its_window`）。
`compute_factor` 从不比对 `registered_years`，只读 `requirement.years`，于是把**重述前的旧值**当作
`computed` 报出：实测 110.0，而 domain 读两个年份给的是 999.0 且 `answerable_through=2024`
直接拒答那天。`max_staleness` 顶不上，用本引擎自己测试论证的 120 天界限复现过（1 月重述）。

`_refuse_a_read_that_cannot_see_what_as_of_holds` 是引擎侧的对等物，且在一个方向上**故意比
domain 的规则窄**：只有**位于本次读到的最早年份及其之后**的已存年份才可能藏住公告；
更早的已存年份是因子自己选择不去够的历史，代价只是 `insufficient_history` —— 诚实且可见。

上一节说这条对齐是「**运行期审计**」，措辞是错的：它只在 pytest 下跑，
`_refuse_table_drift` 那种才是 import 期的。而且它的语料让两边读**相同**的年份，
结构上看不见唯一已经存在的分歧。审计语料已扩到两边读不同年份，措辞已改。

#### 三条较小的

- **三个时钟只因 provider 让它们相等才一致。** 可见性由 `available_time` 裁决，
  引擎按 `event_time` 排序与索引，domain 的 `filing_for` 按 `ann_date` **列**排序。
  把某期较晚公告的 `available_time` 提前到较早公告之前，引擎会取用**公告日在 `as_of` 之后**的那一行
  （实测 `computed 999.0`，`as_of` 早于公告两个月）。`event_time` 晚于 `as_of` 的行现在两条轴上都抛。
  今天不可构造（`_announcement_timeline` 让四个时钟全等），**这恰恰是逐字节相等的语料
  结构上看不见它的原因**。
- **落盘 reach 列没有能把它和邻居分开的 fixture。** 两条轴的往返 fixture 都用相等的一对
  （期次 5/5，会话 `REVERSAL_1D` 的 2/2），两个列互换变异**全树存活**；
  新增一条两轴同测、四个数取 2/3/4/5 的往返。
- **docstring 里指向测试的引用会失效。** 本次引入一条、更早还有一条，
  `tests/unit/test_source_cited_tests.py` 现在把包内每一条 `tests/…::name` 引用对着代码树解析
  （`OA-OPS-031`）；`SHIPPED_REGISTRIES` 这张手写表也改为对着 AST 扫描核对，
  第四个身份注册表不带散文会红而不是根本不被问到。


### `V2-P3-013` 复审记录（2026-08-12）：残差波动与特质波动**未交付**，原因不是数值栈

roadmap 给 `V2-P3-013` 写的四个因子是「残差波动 / 特质波动 / 换手率 / Amihud」。
实际交付的是 `return_vol_60`（样本标准差）、`downside_vol_60`（下行半离差）、
`turnover_60`、`amihud_60`。**前两个不是残差类因子**，因子自己的 note 显式否认了这一点。

**阻塞是实测的，且是两条独立的**：

1. **面板里没有任何指数点位序列。** 15 个 descriptor 全部列出核对过 ——
   `index_weight` 是成分**权重**，不是点位。没有市场收益序列就没有可回归的对象
2. **`FactorWindow` 是单标的的。** `compute_factor` 每只标的调一次求值器，
   `FactorWindow.subject: str`，`values` 只按 `(dataset, column)` 键 ——
   **即使把 `000300.SH` 的行存进面板，求值器也够不着它**

**这不是「要不要装 numpy」的问题。** 单因子时序回归是**单变量**的
（`β = cov(r, r_m)/var(r_m)`，残差标准差再一遍），闭式解、`O(n)`、纯 Python 足够。
ADR-0003 警告的是 **k 个相关连续回归变量**，那是 `V2-P3-004` 的形状，不是这个。
ADR-0003 的 `V2-P3-012` 小节原本把 013 描述成「per-security regressions」，
已在同日的更正段里改掉。

**已立为 `V2-P3-016`**，是残差/特质波动的硬前置。两条障碍由
`tests/unit/test_factor_volatility_liquidity.py::test_the_reason_no_residual_ships_is_a_property_of_the_panel_and_of_the_window`
钉住 —— **谁哪天接入了指数点位序列，那条测试就红，这个声明就必须重审**。

**那一天到了，是 `V2-P3-016`。** 那条测试按设计变红，改名为
`test_exactly_one_dataset_carries_a_level_and_exactly_one_channel_reaches_it` 并**反向**钉住
（恰好一个点位数据集、恰好一条共享通道、恰好一个可达指数、恰好一个因子声明它），
所以再加宽一次和悄悄退回去都会红。下面是交付记录。

#### 交付（2026-08-17）：端点与抓取计划是实测的，可达性的设计由 `factor_id` 决定

**端点是 `index_daily`，抓取计划是每个 `(指数, 年)` 一次请求 —— 一个 `--year` **三次**。**
2026-08-17 实测：`{ts_code, start_date, end_date}` 一年一次，`000300.SH` 2025 年
**243 行**、2026 年至今 150 行，对着 **8,000** 的行上限。整段发布史也放得下
（`19900101..20261231` 返回 5,972 行、`has_more=False`）。
**另一条轴是错的**：`index_daily(trade_date=20260630)` 返回**恰好 8,000 行且 `has_more=True`**，
覆盖 8,000 个不同 `ts_code` —— Tushare 服务上千个指数，本面板要三个。
`limit` **只能收窄**：`8001`/`10000`/`12000` 都回 8,000，`limit=100` 回 100（`index_weight` 的发现，第二个端点上复现）。
逗号拼接**返回零行**而不是报错，也是 `index_weight` 的发现：
`ts_code='000300.SH,000905.SH,000001.SH'` 在一个有 63 行的窗口上返回 0 行。
对比：`index_weight` 同样三个指数要 **36** 次（成分按月发布，点位按会话发布）。
**分区粒度**：一个指数年就是一个分区里一个 subject 的份额，所以分区年份**就是** `--year`，
不进 `_UNPINNED_PARTITION_YEAR_TARGETS`；三个指数共用一年、只靠 subject 列区分，
必须一次写入，`_refuse_to_drop_stored_subjects` 挡住逐指数循环。
**cadence 是 `daily` 而不是邻居的 `monthly`**，实测：2025 年 243 行对 12 次成分发布。

**八列里七列可为空，第八列不可 —— 这条决定了 parser 不能复用。** 三个指数都在上市前若干年就有
回算序列（`000300.SH` 2005-04-08 上市、2002-01-04 起有值，5,972 行；另两个 2004-12-31 基点起，各 5,252 行）。
`000300.SH` 2005 年前的 **721** 行只有 `close`，`open`/`high`/`low` **全为 `None`**；
基点行没有 `pre_close`/`pct_chg`/`vol`/`amount`。`close` 在三段历史共 **16,476** 行里
从不为空、从不非正，是唯一必填列。`pct_chg` **可空且有符号** —— `000300.SH` 5,972 行里
**2,872 行为负** —— 所以 `_index_daily_panel_column` 单独存在而不是复用 `_price_panel_column`：
后者会把这一列送进 `_positive_price`，拒掉历史上每一个下跌日。

**指数的 `pre_close` 就是上一日收盘，个股的不是 —— 这是实测的，不是假设。**
`domain/daily_prices.py` 的核心测量是个股 `pre_close` 被除权除息重述，
所以 `close[t]/close[t-1]-1` 在除权日**符号都反**（`000001.SZ` 2026-06-12：+2.7422% 对 −0.5310%）。
三个指数**全部发布史 15,753 个相邻对里，`pre_close[t] == close[t-1]` 无一例外**，两条路径差**恰好 0.0**。
仍然走 `close/pre_close − 1`：残差回归的另一边走这条，两边定义不同就是在测量那个差。
这一条作为 limitation 记录而不是当便利条件 —— 今天按朴素路径算的读者会对，靠的是本仓库不钉的性质。
对账界是**推导出来再校验**的：`pct_chg` 发布到百分数四位小数，末位半个单位是 **5e-7**，
16,476 行实测最大偏差 **4.99995e-7**，声明界 1e-6 —— 比 `daily_prices` 的 1e-4 紧两个数量级。

**可达性选了「共享 subject 数据集表」而不是给 `FactorDefinition` 加字段，理由是 `factor_id`。**
`factor_id` 是 `stable_model_id(model_dump(mode='json'))`，**任何**新字段都会移动全部 20 个已出厂地址，
包括那 19 个会把它留在默认值的 —— 每个已存 `factor_obs_*` 分区都会被重新标识。给 `FactorField` 加字段同理。
所以声明用**定义已有的词汇**做：`FactorField(dataset="index_daily", ...)`，
`SHARED_SUBJECT_DATASETS` 给它第二重含义。`FactorWindow` 是普通 frozen dataclass、不被任何东西哈希，
**加宽它一分钱地址都不花** —— 实测：20 个旧 `factor_id` 逐位不变。
**代价明写**：整个 build 的市场就是 `000300.SH`，因子不能改选中证500，
这是 `KNOWN_INDEX_PRICE_LIMITATIONS` 的一条而不是一句设计说明。
通道是**第二条**而不是往 `values` 里加键：把 `000300.SH` 的序列塞进个股自己的行旁边，
会让 dataclass 形状不变而 `subject` 对自己的一部分内容说谎。字段集按**相等**断言，所以这条捷径也是红的。
**唯一的不对称就是正确性论证本身**：`_complete_series` 与 `_stored_rows` 替换共享 subject，
`_points_held` **不替换** —— 窗口由个股自己的点构成，替换会让每只停牌票的窗口含有它没交易的会话，
整个截面变 `input_missing`。用一只停牌五个会话的票驱动。

**只交付一个因子，而且这是拒绝不是缺口。** roadmap 那行写的「残差波动」与「特质波动」
在文献里是同一个构造，靠**回归右手边**区分（CAPM 对三因子）。本面板只有**一条**解释序列，
所以第二个名字会是「两个 `factor_id` 对一个数」—— 而且是同一个求值器算出来的同一个数，
本仓库没有任何 fixture 能把两者分开。这正是 `V2-P3-004` 复审的那条发现，提前避免而不是事后发现。
`residual_vol_60` 与 `return_vol_60` 的分离用**下界**钉而不是指望：恒等式
`residual² (N−2) = total² (N−1) (1−R²)` 在两者相等的 fixture 上也成立，所以旁边加一条下界 ——
市场必须解释每只票方差的两成以上（实测 0.70 / 0.69 / 0.42），残差不到总量的 80%（实测 0.55 / 0.56 / 0.77）。
`var(r_m) == 0` 是 `undefined_value` 而不是总波动：斜率在那里是 `0/0`，
「没有可解释的所以残差等于总量」是另一个因子的答案，靠一次未定义的除法得到。
**窗口与家族同为 60/80，且是论证过的**：这个估计量在 N=60 的相对标准误是
`1/sqrt(2(N−2))` = 9.28%，对 `return_vol_60` 的 `1/sqrt(2(N−1))` = 9.21% ——
回归多花的那一个自由度**没有跨过**家族当初选 60 时用的那条 10% 线。

**两个旧因子的 note 有一半变成假话，是改而不是留。** `return_vol_60` 与 `downside_vol_60`
原本都声明「本 build 里两个残差都算不出来」。前半句（两者是同一构造）仍真，后半句在 `V2-P3-016` 之后为假。
两条 note 都改了，`REQUIRED_DISCLOSURES` 里要求的短语也从「算不出来」换成「什么时候不再算不出来」——
只加第五行而不改前两行，会留下两个出厂因子在断言一句代码已经证伪的话。改 note **不移动 `factor_id`**（散文不在哈希载荷里），这一点也断言了。


**台账口径**：`V2-P3-013` 记为已交付的是它实际交付的四个因子；
`016` 落地后才谈得上「残差波动/特质波动」。


### `V2-P3-009` 复审记录（2026-08-13 记，2026-08-17 由 `V2-P3-017` 结清）：EPcut 的阻塞是投影边界而不是上游缺失

roadmap 给 `V2-P3-009` 写的四个因子是「EP / BP / SP / EPcut」。实际交付三个：
`earnings_yield_ttm`、`book_to_price`、`sales_yield_ttm`，都是**同时落在两条轴上**的因子
（会话轴 `daily_basic.total_mv` 1/1，期次轴 5/5 或 1/1）—— 这是本仓库第一批真正使用期次轴的
出厂因子，此前那两对 reach 字段只被 test-local 定义驱动。

**EPcut 的分子不在任何一个已存投影里。** `income` 的十列是两条收入线、成本、营业利润、
利润总额、所得税、两个层级的净利、基本 EPS 和 `ebit`；`fina_indicator` 的十一列是比率与每股数。
扣非净利（`profit_dedt`）与 `dt_eps` / `dt_netprofit_yoy` 都不在其中。

**这是投影边界，不是上游缺失，而且这一点是实测的**：复审把 `profit_dedt` 加进
`fina_indicator` 的投影后**直接读回了这一列本身**（101 只票 / 4,423 filing），
比只能引用 `dt_netprofit_yoy` 强一档 —— 后者是
`KNOWN_FINANCIAL_STATEMENT_LIMITATIONS.the_merge_rule_is_agreement_in_the_projection` 记录的
76 只票全字段探针里出现的、端点服务而 `providers/tushare.py` 的 `response_fields` 不请求的
97 个 `fina_indicator` 字段之一。**`V2-P3-017` 的前提因此是一条测量而不是一个预期。**

**加列是有价的，但代价不是原先写的那一条。** 本小节最初的理由是那条 limitation 的
「widening `STATEMENT_DATA_COLUMNS` would move keys out of the collapsed column and into the
refused one」，并据此断言「已记录的每个数据集拒绝率都会变」。复审把同一批原始行量了两遍：

```
stored_projection  : rows 7686  filings 4423  collapsed_rows 2566  ambiguous 697  refused 833
widened_projection : rows 7686  filings 4423  collapsed_rows 2566  ambiguous 697  refused 874
                     新增 41 条全部来自 profit_dedt 自己，既有 11 列逐列一字未变
```

**折叠行数不变、歧义 filing 数不变、既有列拒绝数不变**；数据集级 rate 从 1.71% 降到 1.65%，
变化只来自分母变大。37 票的小样本同结论。那条 limitation 作为**条件句**依然成立
（`fina_indicator` 投影外的分歧确实集中在 `assets_yoy` / `turn_days` / `eqt_yoy` 上），
但把它当成「加任意一列必然发生」是把条件句读成了必然。

**真实代价是迁移与契约**：`statement_panel_columns` 决定分区 schema，四个统计数据集的每个
已存分区都要重写，`tests/contract/providers/test_tushare_financials.py` 里以真实行钉住的
字段列表也要一起改；外加 `profit_dedt` 自己在那个样本上就吃掉 41 条拒绝，
即 EPcut 的可读性天然低于 EP。**已立为 `V2-P3-017`**。

**`earnings_yield_ttm` 曾占住 EPcut 这个槽位，口径与 `RETURN_VOL_60` 相同**：EP 就是
「非经常性损益那一项减不掉时，EPcut 退化成的东西」，因子自己的 note 显式说明它不是 EPcut。
声明由 `tests/unit/test_factor_value_family.py::test_no_stored_statement_projection_carries_a_deducted_profit_column`
钉住 —— **哪天有人把扣非列加进任一投影，那条测试就红，这个声明就必须重审**。

**那一天到了，是 `V2-P3-017`。** 那条测试按设计变红，改名为
`test_exactly_one_stored_statement_projection_carries_a_deducted_profit_column` 并**反向**钉住
（恰好一个投影、恰好三个被服务的扣非字段里的一个），所以再加宽一次和悄悄退回去都会红。
下面是交付记录。

#### 交付（2026-08-17）：加哪一列由实测决定，加进哪个投影根本不是选择

**`income` 不服务这一族，而且失败方式比报错更坏。** 2026-08-17 对**四个**端点各要一次全字段：
`income` 返回 85 个字段名、`balancesheet` 152、`cashflow` 97、`fina_indicator` 108，
扣非族只出现在其中**一张**表里（`profit_dedt` / `dt_eps` / `dt_netprofit_yoy`）。
向 `income` 点名请求 `profit_dedt` **不报错** —— 响应带回它确实服务的那十五列，
把请求的名字**静默丢掉** —— 所以把这一列写进 `income` 的投影，会让
`providers/tushare.py::_response_rows` 按 `checked_response_fields` 拒绝**每一次** `income` fetch。
`009` 复审列的第四个名字 `dt_profit_to_profit` 四个端点都不服务。

**加哪一列，靠把加宽本身量出来。** 同一批原始行读五遍（101 票按步长取自全市场，11,131 行 /
6,138 filing；另一组不相交的 101 票 10,801 行 / 5,980 filing 在括号里）：

| 投影 | 折叠行数 | 歧义 filing | 新增拒绝 |
|---|---:|---:|---:|
| 已存 11 列 | 4,255 (4,174) | 738 (647) | — |
| `+ profit_dedt` | 4,255 (4,174) | 738 (647) | 66 (46)，全是它自己的 |
| `+ dt_eps` | 4,255 (4,174) | 738 (647) | 58 (39)，全是它自己的 |
| `+ dt_netprofit_yoy` | **4,251 (4,173)** | **742 (648)** | 72 (47)，全是它自己的 |
| `+ 三列一起` | **4,251 (4,173)** | **742 (648)** | 196 (132) |

**既有 11 列的逐列拒绝数在五种投影、两个样本下全部逐位相同**（`fcff` 698、`roe` 64、`roa` 64、
`eps` 59、`bps` 31 …… `ocfps` 6），所以 `009` 复审「加宽不重新定价已记录的东西」这条结论
在两个新样本上被复核为真。**但同一张表也让那条 limitation 的条件句真的开了火**：
`dt_netprofit_yoy` 把 4 个（另一样本 1 个）filing 从折叠挪进歧义，`profit_dedt` 与 `dt_eps` 一个都不挪。
**条件句对一列成立、对旁边一列不成立，只有实测能分开它们** —— 这正是
`the_merge_rule_is_agreement_in_the_projection` 记录的、`fina_indicator` 把分歧藏起来的三列之一。
`dt_eps` 与 `dt_netprofit_yoy` 因**形状**被排除而不是因代价：前者是每股数，本面板唯一的股数是
`balancesheet.total_share`（本仓库最不一致的字段，`BOOK_EQUITY_COLUMN` 拒绝 `bps × 股数` 就是这个理由），
后者是**比率**，而 `RETURN_ON_EQUITY_TTM` 已论证过 TTM 恒等式是关于**和**的恒等式。

**分子口径是扣非归母，实测而不是读字段名。** 端点只服务一个扣非口径且不说是哪个，所以拿
`income` 的两列去量：`600739.SH` 2024 年报 `n_income` 664,195,391.66 对 `n_income_attr_p`
209,556,865.25（3.169 倍），同一份 filing 的 `profit_dedt` 是 **218,927,918.51** ——
归母的 1.045 倍、合并的 0.330 倍。六只大少数股东权益的票 × 四期，
`profit_dedt / n_income_attr_p` 落在 `[0.917, 1.120]`，`profit_dedt / n_income` 跟着少数股东占比走
（0.330 到 1.067）。所以 `(profit_dedt, total_mv)` 两边覆盖同一个索取权，
正是 `earnings_yield_ttm` 选 `n_income_attr_p` 的那条规则。

**TTM 恒等式够得着这一列，因为它是和不是率。** `600519.SH` 2018 年四期 `profit_dedt` 读
8,510,778,903.45 / 15,884,168,512.98 / 24,929,011,158.67 / 35,585,443,648.60 —— 一条以元计价、
每年 Q1 归零的累计曲线；而把同一端点的 `roe` 与它自己的 `profit_dedt` 配对，
四期隐含的权益基数跨度 **10.87%**（93.128bn 到 103.253bn），所以两个累计 `roe` 相减是
两个分母不同的商相减。恒等式在真实五期连续窗口（止于 2018Q3）上给出 32,066,085,945.21，
比最新累计高 28.6%。

**代价是端点，而且是可驱动的。** `profit_dedt` 只发布在没有 `update_flag`、没有 `f_ann_date`、
没有 `report_type` 的那个端点上，它 81.7% 的键带不止一行。这一列自己的拒绝率是
**66 / 6,138（1.075%）与 46 / 5,980（0.769%）**（合计 112 / 12,118 = 0.924%），
而 `income.n_income_attr_p` 已被量到 0.189% 与 0.459%。两组数都不通用（彼此差 1.4 倍，
「率是样本的性质」第 N 次落地），但**次序在每一组上都一样**。
`000488.SZ` 2015 年报把两边同时摆出来：`income` 的两行给出**相同**的
`n_income_attr_p`（2,148,153,529.51，`update_flag` `'0'` 与 `'1'`）因而折叠，
`fina_indicator` 的两行给出 `profit_dedt` 719,891,359.63 对 1,846,820,211.10（2.566 倍，
分别是归母利润的 0.86 倍与 0.34 倍）因而不折叠 —— 同一次 build 里 EP 是 `computed`、
EPcut 是 `ambiguous_filing`。**所以两个因子同时出厂**：EP 就是扣非项减不掉时 EPcut 退化成的东西，
现在这句话后面挂着一个量出来的频率。

**schema 迁移：拒读 + 重取，没有新代码。** `financial_statement_requirement` 早就把
`required_fields` 声明为数据集自己的投影，所以每个在此之前写下的 `fina_indicator` 分区现在
以 readiness 码 `field_missing` **拒读**，而不是给新列答 `None` —— 后者正是
`ReportFiling.value_of` 赖以区分「上游真的空」的那条界线。财报分区是原始摄取物且可重取，
所以修法是 `openalpha panel build --dataset fina_indicator` 而不是从 manifest 重建；
`storage/migrations.py` 与 `018` 一样一字未动，它管的是 `state.sqlite3` 而不是 Parquet。

**身份**：`FactorDefinition` 字段集未动，19 个既有 `factor_id` 与 `04c45b8` 的字面量逐字节相同，
第 20 个是 `fct_1bb4ab44a031477643cc6c85`；不新增覆盖码，`transform_id` 不动。

**台账口径**：`V2-P3-009` 记为已交付的仍是它实际交付的三个因子（`OA-FACTOR-008`）；
EPcut 与这次加列记在 `OA-FACTOR-026`。


### `V2-P3-009` 复审更正（2026-08-13）：`total_revenue` vs `revenue` 的理由原本方向写反了

选 `total_revenue` **本身是对的**（顶行、每个 `comp_type` 都填充、是更 inclusive 的那一列），
但交付时给出的理由是「金融类的顶行不是一个数所以两列分裂，普通工商业上两列相等，
因此 fixture 判定不了这件事」。复审按 `comp_type` 实测了服务行：

| `comp_type` | 行数 | 相等 | 不等 |
|---|---:|---:|---:|
| `2` 银行 | 647 | 647 | **0** |
| `3` 保险 | 358 | 358 | **0** |
| `4` 券商 | 345 | 345 | **0** |
| `1` 普通工商业 | 1,468 | 1,431 | **37** |

**三句全反**：分歧只出现在 `comp_type=1`（原文说这里相等），而原文说会分裂的金融类
（14 只票全历史，1,350 行）**每一行都相等**；被引作「ordinary industrial」例子的
`000001.SZ` 2018H1 其 `comp_type='2'`，**是银行**；「a fixture cannot decide this」也是假的 ——
`600519.SH` **全部 42 个已存期次都不等**，2024Q3 是 `total_revenue` 123,122,542,625 对
`revenue` 120,776,131,875，差 `revenue` 的 1.94%；`002208.SZ` 2022Q3 差 0.47%。

散文已换成这组实测，并落成一条会红的测试
`tests/unit/test_factor_value_family.py::test_the_top_line_and_revenue_are_two_different_numbers_on_a_real_industrial_row`。
**原教训第六次出现**：断言存在但那个 fixture 分不开两个答案 —— 本仓库存的 4 只 `income` 票
里 3 只是 `comp_type=1` 的低分歧样本、1 只是银行，两列在**每一行**都相等，所以那个语料
从来没有能力判定这件事，无论朝哪个方向。


### `V2-P3-018`（2026-08-13 立，2026-08-17 交付）：歧义 filing 曾经拒绝整个 build，现在只给那只票一个覆盖码

`V2-P3-009` 让引擎折叠了「可证是同一个事实」的重复行，但**证明是两个事实的那些还在**，
且一只票撞上就拒绝整个横截面。这不是 `009` 能顺手修的，理由和 `016` / `017` 同规格：

- `FACTOR_CENSUS_COLUMNS` 由 `FACTOR_COVERAGE_ORDER` 推导，进 `FACTOR_MANIFEST_DATA_COLUMNS`，
  分区 schema 宽度被 `tests/unit/test_factor_engine_rules.py::test_a_stored_manifest_row_of_the_wrong_width_is_refused`
  以 `match="expected 27"` 钉死 —— 加第六个码要改**每一个已存 manifest 分区**。
- 复用现有五个码在语义上是错的：`input_missing` 说「行不在」，
  `undefined_value` 说「算出来没有定义」，而这里的事实是「行在、但发布方说了两遍且不一致」。

**量级不是边角**：`009` 复审实测 `income` 的歧义 filing 占 **8.2% / 8.7%**
（`domain/financial_statements.py` 记录 8.15%），`fina_indicator` 记录 **13.7%**；
`009` 当时据此预判「`V2-P3-010` 的 ROE 要读 `fina_indicator.roe`」会更早撞墙。

**`010` 交付后这段的前提换了，结论更强。** 它没有读 `fina_indicator.roe`（见下方小节），
但四个因子里 `accruals_ttm` 读**三个**财报数据集 × 五个连续期次，
`gross_margin_stability` 读**八个**连续期次 —— 每一个 `(filing, column)` 都能拒掉整个横截面。
`010` 自己的两份不相交实测（185 只票）给出的歧义 filing 率是
`income` 8.51% / `balancesheet` 0.95% / `cashflow` **17.11%** / `fina_indicator` 11.80%，
其中 `cashflow` 比记录的 15.80% 更差，而 `accruals_ttm` 正好读它。
**所以 `010` / `011` 比 `009` 更早、更频繁地撞上同一堵墙这句话仍然成立，只是理由从「读哪一列」
换成了「读几列 × 几期」。**

声明曾落在 `panel_factors._read_dataset`、该模块 docstring 的 "The value family" 与
"The quality family" 两节，以及两个家族各自的
`test_the_engine_answers_a_column_the_duplicate_rows_agree_about_and_refuses_one_they_do_not`
（交付时改名为 `..._and_codes_one_they_do_not`，因为它现在断言的是覆盖码而不是拒绝）。
下面是交付记录。

#### 交付（2026-08-17）：第六个码叫 `ambiguous_filing`，插在词表中间而不是末尾

**语义边界是可测的，不靠命名。** 同一个分区、同一只票、同一期：
删掉那个单元格 → `input_missing`；放回去 → `computed`；
再加一行携带**相同**单元格 → 仍然 `computed`（一个事实说两遍会折叠）；
让第二行**不同** → `ambiguous_filing`。分界是「**什么能修好它**」：
重新 fetch 修得好第一个，对最后一个只会把同样的两行再取回来一次。
所以两者同时成立时 `ambiguous_filing` **压过** `input_missing`
（`test_an_ambiguous_filing_outranks_a_null_cell_in_the_same_window`，带控制组）。
`undefined_value` 与两者都不同：它的输入齐全，修法是改因子自己的定义。

**插在中间而不是追加在末尾**，因为 `FACTOR_COVERAGE_ORDER` 的 docstring 一直声称
「`computed` 之后的顺序就是判定优先级」。追加是更小的 diff，但会让那句话变成假话。
`tests/unit/test_factor_engine_rules.py::
test_the_census_order_is_the_order_classify_decides_the_codes_in`
直接读 `_classify` 的 AST 对账，所以这句声明现在是可执行的。

**只对窗口覆盖到的那一期生效。** 标记按 `(subject, period)` 记在 `_DatasetReading` 上，
`_classify` 拿它与自己形成的 `periods` 求交 —— 2023 年那次自相矛盾没有进入
2024Q1..2025Q1 的 TTM，为它给这只票判一个码，就是对一个不依赖它的答案报缺陷。

**会话轴一字未动。** `daily` / `daily_basic` 没有版本机制，第二行照旧拒绝整个 build，
相等与否都拒。`test_two_identical_session_rows_still_refuse_the_build_because_the_axis_has_no_versions`
仍然是 `pytest.raises`。

**与 `filing_for` 那条有意分歧保留了，代价降级了。** 被更晚公告取代的同日矛盾对仍然被标记，
因为把检查放在 `(subject, period, announcement)` 三元组上正是让判决不依赖 DuckDB 行序的原因
（实测三种写序：raised / computed / computed）。现在它的代价是一只票一个码，而不是一个 build。
`test_a_superseded_ambiguous_pair_still_codes_the_security_rather_than_taking_the_later_row`
用两个控制 store 证明「被拒绝的那个答案确实存在且是一个具体的数」。

#### schema 迁移：加一列，旧分区拒读而不是错位解码

`FACTOR_CENSUS_COLUMNS` 由词表推导进 `FACTOR_MANIFEST_DATA_COLUMNS`，所以
**manifest 分区 27 → 28 列**、**transform manifest 34 → 35 列**
（`MISSING_VALUE_COLUMNS` 同样由词表推导）。三处宽度断言随之改：
`expected 27` → `expected 28`、`expected 34` → `expected 35`、
`_UNREASSEMBLED_MANIFEST_COLUMNS` 的 `23` → `24`。

**已存分区怎么办：重建，而这条路是安全的，因为读会拒。**
`factor_manifest_requirement` 的 `required_fields` 就是 `FACTOR_MANIFEST_PANEL_COLUMNS`，
所以五码 build 写下的分区在 readiness 上直接 `field_missing`（阻断码，不可被行过滤修复），
`load_factor_manifests` 拒读而不是按 27 列错位解码；即便绕过 readiness，
`_manifest_cells` 的宽度检查也会拒。**本仓库没有面板层迁移先例，也不需要**：
`storage/migrations.py` 建在 `PRAGMA user_version` 上、只管 `state.sqlite3`，DuckDB 没有那个东西
（`panel/catalog.py::PANEL_CATALOG_SCHEMA_VERSION` 已经把这条分界写下来了）；
而因子分区是**派生物** —— `manifest_id` 记着 definition、as_of、cross section digest 与每个输入
分区的 `partition_content_hash`，重跑一次就是同一份数字。

**哪些身份移动了、哪些没有。**
`transform_id` **移动**（`ftx_b74cef3befc2e315b89bf901` → `ftx_87ea4f34d1e76e129076f967`），
连带 `transform_manifest_id` 与任何携带 transform spec 的 `experiment_id`：覆盖码词表就是
`MissingValuePolicy` 的字段集，而它在 `FactorTransformSpec` 的哈希载荷里。
19 个 `factor_id` **一个没动** —— `FactorDefinition` 的字段集不含任何覆盖码，
覆盖码是存储列能装的**值**，不是身份契约的**字段**。两边都用 `04c45b8` 的字面量钉住：
`tests/unit/test_factor_transform_rules.py::
test_the_coverage_vocabulary_moves_transform_id_and_leaves_every_factor_id_where_it_was`。

#### 两处 import 期审计与一处不挡路的第三处

- `domain/factor_transform.py::_refuse_a_policy_that_cannot_answer_every_missing_code`
  **真的挡住了**：`MissingValuePolicy` 少一个字段该模块就不能 import。
  出厂 `CROSS_SECTION_STANDARD` 声明 `ambiguous_filing="exclude"`。
  **`refuse` 是被实测否掉的**：在 8.51%（`income`）到 17.11%（`cashflow`）的歧义率下，
  它会把本 issue 刚拆掉的整 build 拒绝原样搬到下一层，
  `test_the_shipped_policy_would_refuse_a_whole_cross_section_for_one_ambiguous_filing_if_it_said_so`
  在同一个 panel 上把这个反事实跑出来。**填充**被否是因为两个候选值跨零
  （`income.ebit` −7,579,086 对 +3,427,524），横截面中位数是发布方没说过的第三个数，
  且落在数据本身定不了的符号边界的一侧。
- `backtest/factor_ic.py::_refuse_a_tier_table_that_disagrees_with_its_own_contract`
  **自愈**：`RAW_COVERAGE_ORDER` 由 `MISSING_VALUE_COVERAGE_ORDER` 推导，
  raw 档词表就是 `FACTOR_COVERAGE_CODES`。
- `domain/factor_neutralization.py::_refuse_a_participation_table_that_cannot_answer_every_valued_processed_code`
  **不挡路**：它对的是 `ProcessedCoverage` 不是 `FactorCoverage`。新码只会作为
  `source_not_computed` 行上的 `source_coverage` 到达中性化，`not_a_participant` 已经覆盖，
  **不参与横截面回归**（它没有值，`PROCESSED_VALUE_CODES` 不含 `source_not_computed`）。

#### 新码**不进** `TIER_ADMITTED_CODES`，但**在普查里可见**

`TIER_ADMITTED_CODES["raw"]` 仍是 `frozenset({"computed"})`，而且这不是一次选择而是一条约束：
同一个审计要求 `admitted <= valued`，`TIER_VALUE_CODES["raw"]` 也是 `{computed}`，
所以一个不带值的码根本无法被录取。可见性是自动的 —— `RAW_COVERAGE_ORDER` 推导自词表，
所以 `ICCensus.excluded_by_coverage`、`FactorVector.excluded_by_coverage`
与 `factor_tradeability` 的漏斗各自都多了一格，
「因为发布方自相矛盾而变窄的横截面」在每一档都是可数的。

#### 验收：全市场能跑了

`tests/integration/panel/test_value_family.py::
test_one_contradictory_filing_costs_that_security_and_leaves_the_cross_section_untouched`
给 `000001.SZ` 的最新一期加第二行 `income`（只在 `total_revenue` 上不一致，
正是 `income` 里 8.51% 的 filing 的形状），与干净分区上的同一次构建对照：
那一只票 `ambiguous_filing` 且无值、census 恰好记 1，
**其余每一只票的覆盖码与数值与干净构建逐位相等**（用 `==` 不用 `approx`）。
改动前同一次调用抛 `FactorEngineError`，横截面里**没有任何一只票**拿到观测。
干净横截面本身跨 `computed` / `insufficient_history` / `undefined_value` / `not_in_universe`
四个码，所以对照的不是一个只有一种答案的面板。


### `V2-P3-010` 交付记录（2026-08-13）：质量家族四个因子，ROE **不读** `fina_indicator.roe`

出厂 `return_on_equity_ttm` / `return_on_capital_ttm` / `gross_margin_stability` /
`accruals_ttm`，family = `quality`，**四个都只在报告期轴上**（`lookback_sessions is None`），
是本仓库第一批走这条分支的出厂因子。
并行的 `V2-P3-011` 成长家族同批交付、同样只在这条轴上，所以「只在报告期轴上」这个划分
合并后是**七个**因子而不是四个；两侧各自对同一个精确集合断言，见
`tests/unit/test_factor_quality_family.py::test_the_quality_family_is_on_the_period_axis_alone`
与
`tests/unit/test_factor_growth_family.py::test_the_growth_family_reads_a_filing_and_no_price_at_all`。

#### 本 issue 唯一必须自己论证的题：ROE 自己算还是读上游

`V2-P3-009` 与本文档此前都写着「`V2-P3-010` 的 ROE 只读 `fina_indicator.roe`」。
**否掉了，决定性理由只有一条，而且它跟 `009` 拒绝 `pe`/`pb`/`ps` 的理由不是同一条**：

**published ROE 是「本财年累计」口径的收益率，而且没有任何算术能把它转成 TTM。**
A 股财报在自然年内累计，所以 Q1 的 `roe` 是三个月利润 / 净资产、Q3 是九个月，
一个横截面上只要两只票报送节奏不同就混了两种口径 —— 这正是
`TRAILING_TWELVE_MONTH_PERIODS` 为 EP / SP 解决的缺陷。区别在于 **这里无解**：
cumulative→TTM 是一条关于**和**的恒等式，而比率不是和，
`roe[P] + roe[12-31] − roe[P−4]` 的三项分母互不相同，不是任何东西的 TTM 净资产收益率。

实测（2026-08-13 活探针，`600519.SH` 的 2024 四期）：
`roe` = 10.5688 / 19.2038 / 26.8330 / 38.4283，
对应 `n_income_attr_p` = 240.65 / 416.96 / 608.28 / 862.28 亿 ——
`roe` 各期占全年的比例（0.275 / 0.500 / 0.698）与累计利润占全年的比例
（0.279 / 0.484 / 0.705）逐期吻合到 0.02 以内。它跟着累计利润走，不是四个对同一个数的估计。

第二条理由：**公式不可核对**。响应里没有任何一列说分母是期初、期末还是加权平均净资产，
分子是归母还是合并、是否扣非；`fina_indicator` 本身既没有利润列也没有净资产列，
所以只拿这个端点的读者无从对账。实测把 published `roe` 与期末净资产口径
`n_income_attr_p / total_hldr_eqy_exc_min_int × 100` 对比（2018 起的各年年报）：
`600519.SH` 差 2.0%–9.5%，`000001.SZ` 差 2.3%–11.7%，
`000002.SZ` 差 1.4%–**36.7%**（2025 年报：published `-55.4220` vs 期末口径 `-75.7507`）。

第三条：`fina_indicator` 是**没有 `update_flag` / `f_ann_date` / `report_type`** 的那个端点，
81.7% 的键多行，`603049.SH` 2024 年报的两个版本给出的 `roe` 就是 **23.9249 与 176.0751**。

**第四条被自己的实测证伪，保留原文并标注。** 原本要写的是「`roe` 记录里 53 票丢 5、76 票丢 33，
所以读它更危险」。实测两份不相交样本（185 只票）之后顺序是反的：
`fina_indicator.roe` 29 / 10,865（**0.267%**），
`income.n_income_attr_p` 24 / 10,595（**0.227%**），
`balancesheet.total_hldr_eqy_exc_min_int` 35 / 10,393（**0.337%**）——
自己算要**同时**读后两列、且读**五期**，上游列只需一列一期。
**自己算的拒绝面更大（约 0.56% vs 0.27%，还没乘 reach），这条代价是为前三条付的。**

#### 活探针实测（2026-08-13，两份不相交样本，93 + 92 = 185 只票）

每 60 只取一只（offset 0 与 offset 30），四个端点 offset 分页取尽，
按 `_read_dataset` 用的「**投影内相等则折叠**」规则统计：

| 端点 | filings | 歧义 filing | 实测 | 既有记录 |
|---|---:|---:|---:|---:|
| `income` | 10,595 | 902 | 8.51% | 8.15% |
| `balancesheet` | 10,393 | 99 | 0.95% | 1.29% |
| `cashflow` | 9,602 | 1,643 | **17.11%** | 15.80% |
| `fina_indicator` | 10,865 | 1,282 | 11.80% | 13.70% |

| 列 | A | B | 合计 | 既有记录 |
|---|---:|---:|---:|---|
| `income.n_income_attr_p` | 7 / 5,372 | 17 / 5,223 | 24 / 10,595 = **0.227%** | 0.189% |
| `income.n_income` | 7 | 17 | 24 / 10,595 = **0.227%** | — |
| `income.total_revenue` | 11 | 9 | 20 / 10,595 = 0.189% | 0.123% |
| `income.oper_cost` | 14 | 11 | 25 / 10,595 = **0.236%** | — |
| `income.ebit`（**不读**） | 464 | 424 | 888 / 10,595 = 8.38% | 288 里 258 |
| `balancesheet.total_hldr_eqy_exc_min_int` | 15 | 20 | 35 / 10,393 = **0.337%** | 0.203% |
| `balancesheet.total_assets` | 16 | 19 | 35 / 10,393 = **0.337%** | **0**，再测 18 |
| `balancesheet.total_cur_liab` | 4 | 10 | 14 / 10,393 = 0.135% | — |
| `cashflow.n_cashflow_act` | 1 | 4 | 5 / 9,602 = **0.052%** | **0** |
| `cashflow.free_cashflow`（**不读**） | 835 | 808 | 1,643 / 9,602 = 17.11% | 450 里 450 |
| `fina_indicator.roe`（**不读**） | 16 | 13 | 29 / 10,865 = 0.267% | 5，再测 33 |

**两个记录里的 `0` 都被证伪**：`total_assets` 35（它此前已经从 `0` 动到过 18，这是第二次），
`n_cashflow_act` 5（第一次）。两个绝对量都不大，但都不是零 ——
`domain/financial_statements.py` 自己写的「那个 `0` 才是最该怀疑的」再次成立，
而且这次两个被怀疑的都是本家族要读的列。
**`009` 复审那三列在这两份样本上也都比它自己测的高**，所以那组数同样是样本性质。
`cashflow` 比记录的更差，而 `accruals_ttm` 正好读它 —— 且它那 1,643 个歧义 filing
**每一个**都是 `free_cashflow` 的分歧。

#### 另外三题的答案

- **ROIC 的分子分母**：`ebit` 实测占 `income` 歧义的 8.38 个百分点（888 / 10,595），
  且没有任何一列携带利息费用，所以 NOPAT 的加回**做不到**。投影里能精确表述的最宽资本口径是
  **capital employed = `total_assets` − `total_cur_liab`**（全部权益 + 非流动负债），
  与之配对的收益是**合并**净利 `n_income`（不是归母），
  因为这个资本由母公司股东、少数股东与非流动债权人共同提供。
  代价写明而不是绕开：**本因子相对 NOPAT 口径低估，低估额是非流动借款的税后利息，杠杆越高越低估。**
  「缺的那一项恰好是利息」不是读报表形状读出来的，是实测的恒等式
  `n_income = total_profit − income_tax`：六只票 2007 年起的 446 行最大相对残差 **8.9e-7**，
  其中五只到机器精度；2007 之前不成立（`000001.SZ` 2005H1 差 31.5%、`000002.SZ` 1996H1 差 5.9%），
  那是旧准则下净利润已扣少数股东损益。断言只写在现代行上，并把边界一起写进去。
- **毛利率稳定性**：统计量取**样本标准差**（不是变异系数 —— 毛利率本身无量纲，
  而均值可能接近零或为负，`stdev / mean` 会爆炸或反号）。
  观测量取**滚动十二个月毛利率**：按期次报的累计毛利率混三个月与九个月口径，
  单季毛利率则天然被季节性支配（Q4 强的零售商会年年被判为不稳定），
  只有 TTM 口径每个观测都跨满一年、季节项在四个观测里等量出现从而在离散度里抵消。
  `k` 个 TTM 观测要 `k + 4` 期，取 `k = 4` ⇒ **`lookback_periods = max_window_periods = 8`**。
  方向 `lower_is_better`；key 叫「stability」而值是它的离散度，理由写在定义 docstring 里。
- **多个 12-31 的窗口怎么算 TTM**：**不把恒等式喂给整个八期窗**，而是喂给它的**五期切片**。
  这里发现一件比 `009` 记录的更糟的事：`periods[:-1]` 对连续八期是**七个**连续季度，
  含 **1 或 2** 个 12-31 —— 窗口结束在 Q1/Q2/Q3 时是 2 个，`_trailing_twelve_month_sum` 返回 `None`；
  **结束在 Q4 时只有 1 个，它会返回一个数，而那个数是错的**
  （等于真 TTM 加上「前一个完整财年减掉该年 Q1」）。也就是说直接复用**不是 fail-closed**，
  四个对齐里有一个会自信地答错。切片后每个切片的 `[:-1]` 都是四个**连续**季度，
  任何对齐下都恰好含一个年末。四个对齐全部枚举在
  `tests/unit/test_factor_quality_family.py::
  test_the_whole_eight_period_window_is_no_trailing_year_and_does_not_always_refuse`。
- **应计项**：取**现金流口径** `(TTM n_income − TTM n_cashflow_act) / total_assets`。
  资产负债表口径（非现金营运资本变动）在本投影里**可证被污染**：
  `total_cur_liab` 含短期借款而没有任何一列能扣掉它，指标会随融资决策走；
  折旧加回也不在任何一列里。`free_cashflow` 直接否掉（1,643 / 1,643）。
  `n_cashflow_act` 记录里的 `0` 见上，实测 5 / 9,602。

#### 两个因子对金融企业是瞎的，而且是实测的

银行 / 保险 / 券商不披露营业成本，也不做流动/非流动划分。2015 年起的每一个已存期次上：
`total_cur_liab` 在 `000001.SZ` 68/68、`601318.SH` 67/67、`600030.SH` 64/64 为空
（最后一个非空期次分别是 2006-03-31 / 2006-09-30 / 2006-09-30），
`oper_cost` 在这三只是 59/59、57/57、56/56 为空；
同期两只 `comp_type=1` 工业股（`600519.SH` / `000002.SZ`）两列都是 0/62 与 0/63 为空。
所以 `return_on_capital_ttm` 与 `gross_margin_stability` 对这三类给 `input_missing`，
这是「资本回报率 / 毛利率对这个公司类型本就无定义」的正确答案，
另外两个因子在同一次 build 里照常 `computed`。

#### 与 `009` 的一条恒等式

`earnings_yield_ttm / book_to_price` **就是** `return_on_equity_ttm`（市值约掉）。
这正是分母取窗口末期净资产而不是两端平均的理由：`009` 的两个因子已经出厂，
改成平均会让同一个 build 里对「账面价值」有两种互不相容的说法。
偏差方向写明：当期内增发过股本的公司，期末净资产大于平均净资产，
**本 ROE 因此比教科书口径偏低**，回购则偏高。
恒等式在真引擎上（三个不同 reach 形成的三个窗口）由
`tests/integration/panel/test_quality_family.py::
test_the_return_on_equity_is_the_earnings_yield_over_the_book_to_price` 钉住。

### `V2-P3-011` 交付记录（2026-08-13）：成长家族不调用 TTM，因为那个 helper 是**对齐相关**的

`V2-P3-009` 记录的那句「窗口有缺口时 `_trailing_twelve_months` 返回 `None`」
**只描述了四分之三的情形**，并行的 `V2-P3-010` 实测到了剩下那四分之一。

该 helper 在 `window.periods[:-1]` 里找年末，而 `[:-1]` 是 `N-1` 个连续季度，
**`K` 个连续季度含 `K // 4` 或 `K // 4 + 1` 个年末，取决于窗口末端落在哪个季度**：

| 窗口长度 `N` | `[:-1]` 季度数 | 年末个数 | 结果 |
|---:|---:|---|---|
| `5` | 4 | **恒为 1** | `009` 的恒等式成立 |
| `8` | 7 | **1 或 2** | 三种对齐 `None`，Q4 对齐**自信答错** |
| `9` | 8 | **恒为 2** | 四种对齐都 `None` |

`N=8` 的 Q4 对齐找到的是**上一年**的 12-31，返回「真 TTM + 整个财年 − 那年的 Q1」。
本仓库的 fixture 上是 `1,212 + 1,134 − 131 = 2,215` 对真值 `1,212`，
写成常量 `EIGHT_PERIOD_WRONG_TRAILING_SUM` 并逐个对齐驱动
（`tests/unit/test_factor_growth_family.py::test_an_eight_period_window_gets_a_wrong_trailing_sum_at_one_alignment`）。
`N=9` 恰好安全，但那是「9 对 4 取模」的算术巧合而不是任何人写的守卫，
所以成长家族**根本不调用它**：`_year_on_year` 按**固定下标偏移**读两格、不做任何搜索，
四种对齐逐个驱动。

**这个家族唯一的守卫是 `max_window_periods == lookback_periods`（即 M-2 修好的那条）**，
而它挡住的是一个**数字**而不是一次崩溃：把 `000063.SZ` 形状的缺口窗口直接交给求值器，
`window[-1]` 是 2025Q1 的三个月、`window[-5]` 是 2023 年报的十二个月，
报出 **−84.7%**，而真实的 2025Q1 对 2024Q1 是 **+14.6%** —— 不是模糊而是**反号加放大**。
两个数都按值钉在 `test_a_gapped_window_handed_to_the_evaluator_returns_a_wrong_number`。

#### 零/负分母：`undefined_value`，理由是**排序被反转**而不是「不好看」

同比的分母是去年同期的累计。`num / base - 1` 对 `num` 的导数是 `1 / base`，
**base 为负时单调反向**。活探针抓到的三只真实票（2023H1 → 2024H1 的 `n_income_attr_p`）：

| 证券 | 去年同期 | 今年 | 发生了什么 | 被拒绝的那个数 |
|---|---:|---:|---|---:|
| `002714.SZ` | −2,779,217,657.24 | +829,288,208.44 | 亏损转盈利（最好） | **−1.2984** |
| `000506.SZ` | −81,618,163.07 | −54,931,580.75 | 亏损收窄（居中） | **−0.3270** |
| `002921.SZ` | −1,746,813.60 | −18,805,440.07 | 亏损扩大 10.8 倍（最差） | **+9.7656** |

**排名恰好完全颠倒**。`higher_is_better` 的横截面会把亏损扩大十倍的那只放在最上面。
除以 `abs(base)` 是另一个可选规则，也被拒绝并写明理由：它把 `−1 → +1` 记成 `+2.0`，
和 `100 → 300` 同分，而本仓库没有任何实测支持这两件事同一个秩。

#### 活探针（2026-08-13）：两个不相交的 60 票样本，4,359 条 `income` filing

抽样是对 5,543 只上市证券按 `ts_code` 排序后的等距抽取（步长 92，偏移 0 与 45，**零重叠**），
2016–2026 全部 filing，按本引擎自己的规则归并，在 2024-06-30 与 2025-06-30 两个 `as_of` 上评估
（240 个 (证券, as_of) 对）：

| 量 | 实测 |
|---|---|
| `1` / `5` / `9` 期连续窗口能形成 | 240 / 240、**230 / 240**、**220 / 240** |
| 9/9 相对 5/5 的覆盖率代价 | 230 里少 10，**4.3%**（晚 as_of 2/116，早 as_of 8/114） |
| 去年同期 `n_income_attr_p` 非正 | 49 / 230，**21.3%** |
| 去年同期 `total_revenue` 非正 | 0 / 230，**0%** |
| 加速度两个基期任一非正（净利） | 61 / 220，**27.7%** |
| 同上（营收）——这就是加速度放在营收上的理由 | 0 / 220，**0%** |
| `n_income` 与 `n_income_attr_p` 给出**不同**同比 | 139 / 181，**76.8%** |
| `total_revenue` 与 `revenue` 给出不同同比 | 4 / 230，**1.7%** |
| 加速度退化成它自己的近期同比 | 0 / 220 |

最后两行要一起读：**成长率会把常数比例除掉**，所以 `600739.SH` 上 `n_income` 是
`n_income_attr_p` 的 3.169 倍这件事**判定不了**这个选择 —— 判定它的是那 76.8%。
反过来，`total_revenue` vs `revenue` 在**水平**上最能分开（`600519.SH` 全 42 期、1.94%），
在**比率**上几乎分不开（1.7%）。**每一个列选择都要按它自己要用的口径单独实测。**

**并且：`009` 复审记录的两列拒绝率在更大样本上都更高。**
`total_revenue` 实测 17 / 4,359（**0.390%**）对记录的 0.123%，**3.2 倍**；
`n_income_attr_p` 20 / 4,359（**0.459%**）对记录的 0.189%，**2.4 倍**。
两者仍然很小、都不改变本 issue 的任何决定，改变的是**任何被引用的拒绝率能走多远**——
`domain/financial_statements.py` 自己那句「how little is a property of the sample and not of
the dataset」再次成立。集中度未被动摇：同一语料里 `ebit` 一列就占 599 条歧义 filing 中的
**595** 条（13.65%），本家族一列都不读。

#### 未做的取舍：TTM 对 TTM 的同比

把同比定义成 `TTM(P) / TTM(P-4) - 1` 会让横截面里每只票的口径都是十二个月，
消掉本家族披露的那条**口径异质性**（最新期是 Q1 的票报三个月成长、还没披露 Q1 的票报十二个月，
两者同框排序）。代价是平凡同比要 9 期、加速度要 13 期。
**9 对 5 这一半是实测的（上表），13 期那一半没有实测，只写成它本来的算术。**
本 issue 取了另一边并把代价写进出厂 note，而不是把它论证掉。
### `V2-P3-006` 交付记录（2026-08-13）：逐笔撮合怎么接到组合级，以及被拒成交值多少

`backtest/factor_portfolio.py`，纯 stdlib 叶子，先例是 `backtest/factor_ic.py`。
输入直接是 `005` 的 `ICCrossSection` —— 不是图省事：**IC 和分组收益因此结构上落在同一录取样本上**，
一个 IC 0.05 而价差为负的因子，无法用「两边样本不同」解释掉。

#### 六个必答问题

1. **分组**：按因子值分位，成员由 `factor_ic.average_ranks` 决定
   （`group = int((rank - 0.5) * group_count / n)`）。并列值共享秩、因此**必然同组**，
   与调用方传入的行顺序无关；排序切片的实现会让边界变成排序器 tie-break 的函数，
   而离散化因子几乎总是并列。`group_count` 与 `min_securities_per_group` **均无默认值**，
   下界 2 与 1 都是算术而非口味（一组的价差对任何因子恒为 0；空组的分母不存在）。
   7 只票分 10 组 = `insufficient_sample` 并附计数，**不是**三个空组。
2. **组合构建**：每个仓位同一笔**声明预算**（`position_capital`），按板块最小单位取整
   （非科创板 100 股整手，科创板 200 股起、之上任意股数），组收益是
   `sum(net_proceeds) / sum(entry_outlay) - 1` —— 组真正在它真正花掉的钱上赚到的，
   也是唯一能让整手取整**可见**而非被抹平的读法。等**名义金额**不可构造：
   300 元的票最小仓位 3 万元、3 元的票最小 300 元。
   **不做再平衡**：一个 `as_of` = 一次往返 = 标注自己的窗口，
   所以再平衡频率完全是调用方对 `as_of` 间距与 horizon 的选择，本模块拒绝替它发明一个。
3. **成本怎么进去（本任务的核心）**：不是把逐笔平均掉，而是**分权**——
   - 毛收益 ← `domain/labels.py`（`OutcomeLabel.realized_return` = `WindowReturn.adjusted`）
   - 费用 ← `AShareExecutionPolicy`（每仓位两个 `ExecutionResult`：入场日买、出场日卖）
   - 能否成交 ← **两者都判**，标注在先

   `entry_outlay = notional + 买方费`，`gross_value = notional * (1 + realized)`，
   `net_proceeds = gross_value - 卖方费`。
   **无公司行动的窗口上 `gross_value` 与卖单 `notional` 精确到分相等**，
   除权窗口上分开且方向是对的：现金分红是现金、不缴印花税，
   所以按已公布收盘价收卖方费是**正确处理**而不是它的近似。
   收益**绝不**由两笔成交价反推 —— 那正是 `close_exit/close_entry`，
   Task 30 实测在 `000001.SZ` 2026-06-12 给出 **−0.5310%** 而真值 **+2.7422%**，连符号都反。
4. **三档**：整套 `TIER_ADMITTED_CODES` 从 `factor_ic` 继承，不重述，
   所以 `imputed` 仍然不进样本，import 期对账仍然只跑一次。
5. **`direction`**：与 `005` **一致**，并有一处被算术逼出来的差别，写明而非含混——
   组下标永远按**原始因子值**升序（组 0 = 最低值），所以存下来的组表始终能与它切自的值对账；
   声明决定的是**哪一组做多**而不是某个数的符号（相关系数可以取负，组合不能）。
   `long_short_spread` 在 `lower_is_better` 下是 `raw_spread` 的**逐位精确**取负。
6. **多空与融券**：价差**交付**，并明确标注为**两个多头组合收益之差、不是可执行组合**。
   理由是契约级的：`ExecutionRequest` 根本没有做空侧（没有融券费、没有保证金、没有券源），
   A 股融券是标的名单 + 券商费率 + 不总有的库存，三者本仓库都没有数据集；
   而且 `KNOWN_EXECUTION_LIMITATIONS.a_one_price_session_refuses_one_side_here_and_both_ends_there`
   已经实测记录：撮合侧与标注侧**只在「入场买 + 出场卖」这一对上一致**，
   而空头往返恰好是**另外那一对**，且撮合侧在那对上是 fail-open（一字涨停它让你卖）。
   把空头腿建在那对判定上，等于用本仓库已实测为不可互换的一对判定造组合。

#### 被拒绝的成交在组合收益里是什么：**什么都不是，绝不是 0**

0 是一个收益。一组里每只涨跌停锁死的票都读成平盘，这组的收益就有一部分是在测锁死率
—— 与 `factor_ic` 对 `ICCensus.unlabelled_count` 的论证同一条。
每个开不了仓或平不了仓的标的**离组并按自身 `HoldingOutcome` 计数**
（`unbarred` / `below_board_minimum` / `rejected_entry` / `rejected_exit`），
`PortfolioCensus` 强制 `held + 各排除格 + unattempted == offered`，掉一个就自证失败。
代价是明写的：被拒的恰恰是动得最狠的那些票，所以组收益是**以可交易为条件的**，
`a_group_return_is_conditioned_on_the_names_that_could_be_traded` 就是这条。

三个「组空了」的覆盖码是分开的而不是一个：`degenerate_scores`（因子在这天只给出一个值）、
`unfillable_groups`（并列块对上声明的切法填不满）、
`unfillable_after_execution`（切法成立、**市场**把组掏空了）。
第三个正是 `V2-P3-007` 要让其显形的读数，所以它也是唯一一个**保留**逐票判定的拒绝码
（订单确实下了），另外两个把每只票记为 `unattempted` —— 没下过的单不该有逐票判词。

#### 与 `execution.py:135` 那段接缝的关系

除上面第 6 条外，另外两条也落在本模块上：
`the_registry_verdict_is_not_an_input` 由「标注在先」化解（退市/未上市/超快照都被标注侧先拒）；
`an_absent_band_is_derived_rather_than_refused` **化解不掉**，只能计数 ——
`MarketBar` 不带公布涨跌停时策略按推导带判定，而推导带在 2024-06-28 与公布带在 5,338 只中错 159 只。
`PeriodPortfolio.unpublished_band_legs` 就是这个数：报 0 的期间，每一笔成交都是对着交易所自己的数判的。

#### 不发累计曲线

`QuantilePortfolioSummary` 给均值、离散度、`spread_ir` 和命中率，
**不给累计收益、不给净值曲线、不给年化**。理由与 `005` 不给 t 统计量是同一条：
horizon `h` 上相邻一个交易日的两个预测日共享 `h + 1` 个会话中的 `h` 个
（`domain/labels.py::overlapping_windows` 实测），
把重叠期间连乘会把同一天的涨跌数进去好几次，出来的曲线随**采样频率**增长而不是随因子增长。
不重叠的排程是让累计数有意义的前置条件，而本模块从一串期间里看不出调用方用了没有。

#### 实测（ADR-0003 已加 Update 小节）

- 全市场一期往返（5,534 只 × 2 笔）：**35.9 ms**（七次取最好），
  与 `apply_factor_transform` 在同样 5,534 只上的 35.9–37.6 ms 同量级，
  比它前面那次 2.24 s 的 `compute_factor` 小 **62 倍**。
- `CostSchedule.minimum_commission` 的 5 元下限在名义额 **16,666.67 元**以下起作用：
  往返总摩擦在 2 万元及以上是 **0.1120%**（佣金 6bp + 过户 0.2bp + 印花 5bp），
  在 1 万元是 **0.1520%**，同一笔交易贵 **35.7%**。
  这正是 `position_capital` 必须声明并随报告一起存的理由。
- 钱是 `Decimal` 不是 `float`，因为 `backtest/execution.py` 是；
  numpy 没有 `Decimal` dtype，所以本 issue 连「要不要向量化」这个问题都不成立。

### `V2-P3-007` 交付记录（2026-08-16）：换手怎么定义、覆盖率是什么的覆盖率、容量假设在哪

已交付 `backtest/factor_tradeability.py`（`005` 之后第三个纯 stdlib 叶子模块，
吃 `006` 的 `PeriodPortfolio` + `005` 的 `ICCrossSection` + 一个调用方给的会话成交额）。
这条 issue 的一句注解就是验收标准：**让统计上好看但不可实施的信号显形**，
所以每一个报出的数都是按「能不能把两个 IC 相同的因子分开」挑的。

#### 六个必答问题

1. **换手怎么定义 —— 建了滚动组合，但再平衡频率不是新参数。**
   `006` 的换手按构造是 100%/期，本模块就是它那条具名限制
   （`every_period_is_an_independent_round_trip_so_turnover_is_total`）说的解：
   对**多头组**（`top_group_index`，由因子自己的 `direction` 决定，不是参数）维护持仓态。
   **再平衡频率就是调用方已经声明过的东西 —— 那串 `as_of` 的间距**，
   本模块**测量**它而不是声明它（`006` 明确拒绝发明它，本模块也不发明）。
   它拒绝的是「持仓态根本不存在」的排程：horizon `h` 上相邻两个预测日的窗口共享
   `h + 1` 个会话中的 `h` 个，被「带过去」的票是**同时被持有两次**而不是一次，
   所以相邻两个 measured 期间必须满足 `entry_day(k) >= exit_day(k-1)`，
   否则是覆盖码 `overlapping_schedule`（不是异常 —— 按日测 IC 是对的、测换手是不可能的，
   报告该说清是哪一个）。换手报**两读**：
   `name_turnover = (进 + 出) / (前端数 + 后端数)`、
   `money_turnover = (卖出市值 + 买入名义额) / (前端市值 + 后端名义额)`，
   都取**对称**形式而不是 `进 / 后端数` —— 两端**不一样大**（市场每天掏空的量不同），
   单边分母会把「组缩小了」读成「组换手了」。两者按构造都在 `[0, 1]`。
2. **覆盖率是什么的覆盖率 —— 四步漏斗，四个不同的权责方。**
   一个「覆盖率」在被分解之前是有歧义的，而分解本身就是发现：

   ```
   universe   -> valued       因子引擎              （FactorCoverage / 该档变换）
   valued     -> admissible   该档自己的准入政策    （TIER_ADMITTED_CODES）
   admissible -> scored       domain/labels.py      （停牌、涨跌停锁死、无公布区间、退市）
   scored     -> held         AShareExecutionPolicy （停牌、手数、T+1、推导区间）
   ```

   `implementable_rate = held / universe` 是这四个的乘积（四次除法的舍入内）。
   中间两步正是「报一个覆盖率数」会丢掉的：**「因子给全市场打了分但这些票标不了」
   和「因子谁也没打分」是两个缺陷、两种补法**。两张档位表是 `import` 的不是重抄的，
   而且**两张都吃劲**：`ICCensus.excluded_by_coverage` 按「该档不录取的码」建键，
   要还原 `valued_count` 就得走该档整个词汇表、留下「带值且不录取」的格
   —— 那正好是 `TIER_VALUE_CODES` 与 `TIER_ADMITTED_CODES`，
   删掉前一个会把 `input_missing` 数进去、删掉后一个会在 `processed` 上 `KeyError`。
   三档里两张表只差**一格**（`processed` 的 `imputed` 带值且不录取），
   所以那一格是唯一可分辨的 fixture。
3. **容量怎么量 —— 一个声明的参与率上限，其余全是算术。**
   `participation_cap ∈ (0, 1]`，**无默认值**，是「本研究最多占一个会话成交额的多少」。
   它是**约束不是冲击模型**：`CostSchedule` 没有任何随单量变化的项，
   本仓库也没有任何能拟合冲击系数的数据集，所以这里不估、不用、也不隐含任何冲击系数。
   成交额取 `daily.amount`，单位**千元**（实测：十一条真实行上
   `amount * 1000 / (vol * 100)` 是唯一让隐含 VWAP 落在当日 `[low, high]` 内的读法，
   **11/11**，另外三种读法 **0/11**）。日期必须**不晚于** `entry_day`，晚于则拒绝。
   `min` 绑定是被 `006` 的等预算建仓**逼出来的而不是选出来的**：每个仓位同一笔声明资金，
   所以整组能不能实施取决于**最不流动的那一只**，
   `capital_multiple = binding_capacity / position_capital`，**小于 1 就是已经超容**。
   同时报 `liquidity_weighted_capacity`（预算跟着流动性走的上界，本仓库不建那个组合）
   与 `concentration = 等权 / 流动性加权`，后者说的是「一只票毁掉了多少容量」。
4. **「让统计上好看但不可实施的信号显形」怎么显形 —— 实测演示见下一节。**
5. **样本量与边界 —— 两个统计都会在窄组上退化，两个退化都没有藏。**
   两组各 `n` 只的换手按 `1 / (前端数 + 后端数)` 计量，
   三只对三只只有**七个可取值**，`Rebalance.resolution` 就是那个单位、报在比值旁边；
   容量是 `min`，所以 `binding_capacity` 无论组里有几只都是**一只票的数**，
   `binding_subject` 点名、`held_count` 并排、`concentration` 说它离其余有多远。
   **不另立第二个下限**：`QuantilePortfolioSpec.min_securities_per_group` 已经是那个下限、
   已经无默认值，多一个地方声明同一件事就是多一个地方让两者打架。
6. **三档** —— 与 `005`/`006` 一致，`TIER_ADMITTED_CODES` 与 `TIER_VALUE_CODES`
   都是 `import` 的。raw 与 neutralized 两张表相等，所以它们的 `admission_rate` 恒为 `1.0`；
   `processed` 是唯一能观察到这一步的档。

#### 验收标准的实测演示

`test_the_same_ic_and_the_same_funnel_are_told_apart_by_the_top_groups_execution_rate`：
**一个**横截面（十二只票、打分 1..12、远期收益 `rank/100`，秩相关
`0.9999999999999998`），配**两个市场** —— 一个把打分最高的三只的入场会话停牌，
另一个把最低的三只停牌。

- **相关系数分不开**：`MarketBar` 根本不是它的输入，两边是同一个 `ICCrossSection`。
- **期间级漏斗也分不开**：两边都拒了十二分之三，`execution_rate` 都是 `0.75`，
  上面每一个计数都相同（断言 `bad.funnel == good.funnel`）。
- **分开它们的是一张表的一行**：多头组自己的 `execution_rate` 是 `0.25` 对 `1.0`，
  `top_group_execution_shortfall`（整体率减多头组率）是 **`+0.5` 对 `-0.25`**。

这就是「为什么要出逐组分解而不是一个覆盖率数」的全部论证：发现活在聚合会抹掉的那一层。
更狠的一档在 `test_a_long_group_the_market_refuses_entirely_is_a_shortfall_and_not_a_division`：
多头组被拒到空，期间码正是 `006` 的 `unfillable_after_execution`
（`groups == ()`），而逐组表**照样在**——
因为逐组计数是「切法重算 ∸ 拒绝名单」推出来的，不是从 `PeriodPortfolio.groups` 读的。
`006` 说第三个空组码是本 issue 要显形的读数，这就是它显形的样子。
`shortfall` 取**差**而不是比，正是为了这一档：比要除以一个 `min_securities_per_group=1`
能取到的零。

#### 成本区间：`006` 的费用是上界，这里给下界

每个被留住的票在 `006` 里付过前一期的卖出费和后一期的买入费，滚动组合两笔都不付。
`avoided_cost` 就是这些腿的和，**全部取自已经存在的 `ExecutionResult`，
本模块不模拟任何一笔单、不发明任何一个价**。每条腿在整串序列里最多被省一次
（第 k 期的买入腿属于「进入 k」那次再平衡、卖出腿属于「离开 k」那次），
所以 `avoided_cost <= round_trip_cost` 是 `TurnoverSeries` 的**校验**而不是论断。

    round_trip_cost                    006 的费用，上界（100% 换手）
    round_trip_cost - avoided_cost     rolling_cost，下界

是**下界**而不是答案：被留住的仓位在 k 期的股数会按 k 的入场收盘价重算，
真正的滚动组合要按声明资金**补/减那点差额**并为之付费，这里没收。
方向已定号、已被它不做的那笔交易界住（差额最多一个仓位）。

**本模块不发滚动收益，也发不了**：`exit_day(k-1)` 到 `entry_day(k)` 之间被留住的仓位
跨过了任何标注窗口都没盖到的会话，那段收益不是本仓库任何契约持有的量；
而就算排程没有空隙，把盖到的那些期间连乘也正是 `006` 已经拒绝的那件事。
持仓态能诚实回答的是「它换掉了自己的多少」和「那省了多少钱」，净值不是。

#### 容量的建模假设（逐条，哪些是声明的参数）

| 假设 | 形态 |
|---|---|
| 参与率上限 | **声明的参数**，`TradeabilitySpec.participation_cap`，无默认，`(0, 1]` |
| 每仓预算 | **声明的参数**，继承 `QuantilePortfolioSpec.position_capital`，不在本模块重声明 |
| 会话成交额 | **调用方输入**，`SessionLiquidity`，单位在字段名里，`liquidity_from_amount` 换算 |
| 流动性会话 | **调用方选**，必须 `<= entry_day` 且全期同一天，`liquidity_day` 与 `entry_day` 并排报 |
| 千元 → 元 | **硬编码常量** `CNY_PER_TURNOVER_UNIT = 1000`，实测 11/11，对着 `panel_factors` 的同名常量钉住 |
| 一个会话一笔成交 | **硬编码判断**（`AShareExecutionPolicy` 就是这样撮合的），不跨日拆单 |
| `min` 绑定 | **被 `006` 的等预算逼出来的**，不是选的；`binding_subject` 点名 |
| 只估入场腿 | **具名限制** `capacity_is_estimated_on_the_entry_leg_only`，**不声明偏差方向** —— 本仓库不测两腿成交额的联合分布，写一个「高估」或「低估」就是又一条没人测过的安全性声明 |
| 无冲击模型 | **具名限制** `capacity_is_a_declared_participation_cap_and_not_a_market_impact_model` |

真实数量级（本仓库已存的十一条真实行里的两条）：
`000001.SZ` 2026-06-12 成交额 **2,263,042,930.57 元**、
`000569.SZ` 2001-01-02 成交额 **6,579,577.80 元**，相差 **344 倍**。
同时持有这两只的组，容量被后者定死；1% 上限下那只票只吃 **65,795.78 元**，
所以一个声明 `006` 自己那笔 10 万元每仓的研究，`capital_multiple` 是 **0.658**
—— 在数第一笔费用之前就已经超容了。

#### 实测（ADR-0003 已加 Update 小节）

- 全市场一期（5,534 只、十分位）报告：**2.7 ms**（七次取最好），
  同进程内的参照 `QuantilePortfolioStudy.measure`（bar 预建）是 58.2 ms
  —— **报告比它所报告的那一期便宜 21 倍**。
- 两期滚动换手（每端 553 只持仓）：**0.7 ms**。
  `006` 的 ADR 小节把「每期成本随**上一期持仓**增长」列为未测形状，
  这条测了：成本是两端持仓的两次集合运算，被声明的 `group_count` 界成 `n / group_count`。
- 本模块没有任何一处是数值数组上的归约：逐组是整数计数、漏斗是两个 census 的五个整数、
  换手是两次集合差、容量是 `Decimal` 上的一个 `min` 和一个 `sum`。
  **运行时依赖仍是九个。**
### `V2-P3-014` 交付记录（2026-08-17）：制品的键是什么、三档怎么排、不可变靠什么强制

已交付 `backtest/factor_experiment.py`（`005` 之后第**五**个纯 stdlib 叶子模块，
吃 `005` 的 `ICSummary`、`006` 的 `QuantilePortfolioSummary`、`007` 的 `TurnoverSeries`、
`008` 的 `RedundancySummary`，一个数都不重算）。
这条 issue 的一句注解就是验收标准：**否则分不清「因子有效」与「暴露没控住」**。

#### 六个必答问题

1. **「不可变」是什么意思 —— 两个读法都要，因为它们堵的不是同一个洞。**
   - **内容寻址**：`FactorExperimentSpec.experiment_id` 是 `stable_model_id` 打在**声明**上，
     改任何一项不是改一份制品、是另铸一份；`FactorExperimentArtifact.content_digest`
     是同一个函数打在**答案**上。
   - **写一次**：`panel_factors._refuse_to_drop_a_stored_build` 是先例，
     `refuse_a_restated_experiment` 抄的是它的形状 ——
     `experiment_id` 撞上而 `content_digest` 不同的到达件**被拒**（错误里点名两个摘要），
     而**完全一致的重算被放行**（`FactorInputRef` 丢过又找回来的那个方向：
     身份为无事而移动 = 重建永远写不进去 + 前一版再也推不出来）。

   两者不冗余：只有内容寻址，等于「改了就是另一份」而对「新的把旧的顶掉」一句话没有；
   只有拒绝，等于让一个会为墙钟而移动的身份去当判据。合起来说的是
   **同一份声明在同一批输入上只有一个答案，别的答案要么是另一个实验、要么是 bug**。
   而**封条**是值对象唯一能在进程边界上拿到的强制手段：
   `FactorExperimentRecord.sealed_digest` 是**存下来的字段**，
   校验器要求它等于重算出来的 `content_digest`，
   所以被改过的文档不是「和原件不同」而是**读不回来**。
   `the_seal_detects_an_edit_and_does_not_authenticate_one` 说清它不是签名。

2. **键是什么 —— 声明 + 输入，不含答案；变了必须换键的与不必的，用 `002` 那条签名审计答。**

   ```
   进 experiment_id：四个上游 spec（整个带进去）、retention_floor、code_commit、
                     horizon_sessions、as_of_digest、三档各自的 source_digest
   不进：            built_at（FactorBuildManifest.built_at 的理由）、
                     note（FactorNote 的理由）
   进 content_digest：整份文档，spec 也在内
   ```

   四个 spec **整个**带进去而不是抽出四个下限：四条下限的算术极小值是四个不同的数
   （IC 的 3、冗余的 4、每组的 1、再平衡的 1），各由各自的契约定，
   抽一个投影出来就是给四件事立第二个真相源。
   **审计而不是列表**：`test_every_parameter_of_the_builder_moves_the_identity_or_is_exempted
   _by_name` 读 `inspect.signature(build_factor_experiment).parameters`，
   要求每个参数要么**实测**能移动 `experiment_id`、要么在
   `IDENTITY_EXEMPT_PARAMETERS` 里带着理由具名豁免。**第十二个参数会红。**
   反方向也钉：`test_a_measurement_that_changes_moves_the_content_and_not_the_identity`
   改一档的数字，要求 `content_digest` 动而 `experiment_id` 不动。

3. **三档报告的形状 —— 一份制品、三行、外加一张格子；三行不够。**
   三行仍然把「这一跌够不够算数」留给读者，而这正是本 issue 说要消灭的那个动作。
   所以 `attributions` 是**声明的六格**（`ATTRIBUTION_CELL_ORDER`，步长优先、统计量次之），
   **永远六格全在**（`ICCensus.excluded_by_coverage` 那条规矩：
   少一格和一格写着 `not_measured` 是两种说法）。每格：

   ```
   retention = to_value / from_value

   not_measured  两档之一根本没有统计量
   no_baseline   都有，但 from_value <= 0：前一档就没赚过钱，没有「被拿走」这回事
   reversed      to_value < 0：这一步把赌注掉了个头
   amplified     retention > 1
   removed       retention < 声明的线      <-- 验收标准那一格
   survives      其余
   ```

   两个统计量而不是一个：`mean_ic` 与 `mean_spread` 会**分家**（见下一节的实测），
   「因子有效」本来就有两种读法。两者都是上游**已经定过号**的量，这里**不再定第二次**
   （再定一次会把 `lower_is_better` 的因子负两遍，正是 `ICSeriesCorrelation` 点名的那个错）。
   判决**写错就构造不出来**：`TierAttribution` 的校验器重算比值和整条阶梯（`ICPoint` 的先例）。
   第四行是 `survival`：一个因子的 `raw` 对本档的横截面相关
   —— `factor_redundancy` 自己说「跨档自配对是被支持的读法」，
   而 `raw` 行必须是 `None`，因为同档自配对被那个模块拒。

4. **四个上游模块怎么装订 —— 各带各的 spec、各带各的下限，本模块不立第五套。**
   `TierReport` 原样携带四个上游对象，连同它们**自己的覆盖码**；
   `TierReport.coverage_codes` 把四个码并排读出来，不合并。
   跨对象的一致性是校验器的活：一档、一因子、一方向、一 horizon、一切法、**一份样本**。
   最后一条是验收标准所依赖的那条 —— 两档在两串日子上测出来的落差不是归因
   （`_refuse_rungs_over_different_samples` 的论证升一层）。

5. **制品存哪 —— 不在面板平面，也不在证据平面，落成一份规范化 JSON 文档。**
   - **不在面板平面**：`factor_*` 数据集的 `subject` 列已经有两个含义（证券、`manifest_id`），
     按 `experiment_id` 键控的制品会是第三个含义。
   - **`002` 那条 `ParquetEvidenceStore` 禁令对报告不成立**，这点必须说清：
     那条禁令的论证是「`FactorObservation` 不是 `EvidenceSnapshot`、树里没有转换、没有 import 边」，
     而 `EvidenceSnapshot(kind="factor_experiment", ...)` 是**能构造的**、store **会收**。
     所以本模块不走那条路的真实理由是**分层**而不是类型：
     `backtest/` 不许 import `storage/`，因子的对外面孔是 `V2-P3-015`。
   - 因此制品交付为**文档**：`experiment_payload` 用 `stable_model_id` 自己那套规范化
     （排序键、固定分隔符、`ensure_ascii=False`、`allow_nan=False`），任何字节存储都放得下。
     计算字段被排除**不是风格选择**：本仓库每一个内容寻址模型都是 `extra="forbid"` +
     `computed_field` 身份，带着计算字段的 dump **根本读不回来**
     （`FactorDefinition.factor_id` 会被产生它的那个模型当成多余输入拒掉）。
   - 代价具名：`nothing_in_this_module_stores_an_artifact_or_can_be_made_to`
     —— 从没被交给拒绝函数的制品也就从没被它检查过，
     正如从没被交给 `write_factor_panels` 的 build 从没被那条守卫检查过。

6. **样本量不足时报告长什么样 —— 四套拒绝码并存，靠的是不去调和它们。**
   本模块**不新增第五种「N/A」**。每一档的四个上游对象带着自己的码原样在那里，
   唯一被综合的地方是格子，而那里只有一个码 `not_measured`，
   旁边两档自己的码照样读得到。一个制品级的「insufficient」会把
   「IC 序列差两个 as_of」（`insufficient_as_ofs`）和
   「排程重叠所以持仓态不存在」（`overlapping_schedule`）变成一个发现一种补法，
   而它们是两个发现两种补法 —— `FactorCoverage` 为此花了六个成员、`TurnoverCoverage` 三个。

#### 验收标准的实测演示：raw 档 IC 高、neutralized 档归零，在报告里长这样

`test_a_factor_whose_edge_is_its_exposure_reports_removed_on_the_neutralisation_step`：
一个因子、四只票、两个 as_of，三档全部跑真实的四个上游 study。
raw 与 processed 的秩序完美预测远期收益；neutralized 残差取的是那个
**与恒等排列 Spearman 恰为 0** 的四元置换（`sum(d²) = 10`、`1 - 6×10/(4×15) = 0`），
而这个 0 是**从 `ICSummary.mean_ic` 上用 `==` 读出来的**、不是从公式抄来的
—— `factor_ic._pearson` 算的是**缩放过的**积矩相关而不是秩差公式，
「这两个在最后一位上相等」是关于浮点的断言而不是关于代数的。

制品里长这样（`retention_floor = 0.4`）：

| 档 | `ic.mean_ic` | `portfolio.mean_spread` | `survival`（对 raw） |
|---|---|---|---|
| `raw` | `1.0` | `0.019977606941848025` | `None`（同档自配对被拒） |
| `processed` | `1.0` | `0.019977606941848025` | `1.0`，`undeclared_lockstep` |
| `neutralized` | **`0.0`** | `0.009988803470923902` | `0.0`，`distinct` |

| 步 | 统计量 | `retention` | 判决 |
|---|---|---|---|
| `raw → processed` | `mean_ic` | `1.0` | `survives` |
| `processed → neutralized` | `mean_ic` | **`0.0`** | **`removed`** |
| `raw → neutralized` | `mean_ic` | `0.0` | `removed` |
| `raw → processed` | `mean_spread` | `1.0` | `survives` |
| `processed → neutralized` | `mean_spread` | `0.49999999999999445` | `survives` |
| `raw → neutralized` | `mean_spread` | `0.49999999999999445` | `survives` |

读者做的动作是**一次按名查表**，不是比较两个数：
`artifact.attribution(from_tier="processed", to_tier="neutralized", statistic="mean_ic").verdict`
返回 `"removed"`，而能让读者放过的两档（`survives`、`amplified`）是不同的字符串。

**`survival` 行独立佐证同一件事**（`test_the_survival_row_corroborates_the_verdict_instead
_of_repeating_it`）：processed 与 raw 处于**秩锁步**（幅值恰 `1.0`，判决
`undeclared_lockstep` —— 单调标准化本来就必须这样），
neutralized 与 raw 相关恰 `0.0`（`distinct`）。
所以「赚的是暴露的钱」是同一份制品的**两个互不依赖的读数**，不是一个数戴两顶帽子。

**两个统计量在同一个 fixture 上分家**，这正是它们都要报的理由：
同一步里净多空价差保住了自己的 `0.49999999999999445`
—— 二分位切法比排序**粗**，把四只票全部重排之后仍有两只高收益票留在多头组。
声明的线取 `0.4` 而**故意躲开** `0.5`：一个由两个含费商末位决定的判决，
会在 `CostSchedule` 的默认值变动时改口。

#### 不可变性怎么强制的（实测）

- **逐字段篡改**：`test_every_field_of_a_sealed_record_refuses_to_reopen_after_a_single_edit`
  走遍序列化制品的**全部 335 个标量叶子**（`turnover` 75、`portfolio` 60、`survival` 51、
  `attributions` 48、`ic` 48、`spec` 43、`source_manifest_ids` 6、`tier` 3、`schema_version` 1），
  每次只扰动一个，要求**每一个**都让 `open_experiment` 拒绝。
  这是把「每个字段都要有断言」从纪律变成性质：封条不知道是哪个叶子，
  所以四个上游 summary 里新加的字段**落地当天**就被保护，不需要谁去扩一张表。
- **封条的另一侧**：改摘要而不改内容，同样被拒。
- **不会空过**：未被动过的文档必须读得回来、两个地址不变、**再序列化回同样的字节**。
- **重述守卫**：同 `experiment_id` 不同 `content_digest` 被拒；
  只差墙钟与散文的重算被放行；已经自相矛盾的 `held` 集合按**自己**报错而不是赖到来者头上。

#### 中性化时间戳那条约束写在哪

`KNOWN_EXPERIMENT_LIMITATIONS.a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule`
—— 与 `V2-P3-005`、`V2-P3-006` **同名同码**（同一件事，按模块改名就会变成三件事）。
**该码原名 `neutralised_residuals_are_read_at_a_year_end_snapshot`，`V2-P4-026` 连同它的内容一起改了名**：
「年末快照」那半句是存储契约逼出来的，而那个逼迫已经解除，
剩下的是盖章规则本身 —— 残差带的是**其构建被运行的那一刻**，不多不少，
所以一条中性化序列的 point-in-time 程度等于它的构建排期的 point-in-time 程度，
而**存下来的行里没有任何东西说明是哪一种排期**。
本模块的措辞比另外两处更进一步，因为 neutralized 正是验收标准所转的那一档：
没有任何构建盖过的 `as_of` 经 `read_visible_at` 读回来的是**空而不是报错**，
所以它在这里现形为「neutralized 档 `ICSummary` 的 `coverage` 是 `insufficient_as_ofs`、
它参与的每一格都是 `not_measured`」，而不是一个异常 —— 这正是 `factor_view` 具名拒绝的理由。
`test_a_tier_that_never_cleared_its_floors_reports_not_measured_and_not_a_refusal` 驱动这一形态。

#### 数值栈

本模块没有任何数值数组归约：判决是两个浮点的一个商和四次比较，
身份是一次 `json.dumps` 加一次 sha256。
ADR-0003 因此**没有被重新打开**，也不需要第七条同类结论 —— 没有工作负载可谈。
**运行时依赖仍是九个。**

### `V2-P3-019`（2026-08-17 立并交付）：一次行级 Parquet 篡改曾经变成一份看起来正常的因子报告

P3 产品验收的 Critical-1，也是本项目第十四条「一条声明的安全性质被实测证伪」的记录 ——
而这一条证伪的是**「不可变制品」这个词本身**。

#### 实测

翻转 `factor_obs_reversal_1d_v1/2026/data.parquet` 全部 16 行的值，删掉 `runtime/experiments`
（这样没有任何已存答案能反驳新答案），再跑真的 `openalpha factor run`：

| | 诚实 store | 篡改 store |
|---|---|---|
| `mean_ic`（raw） | `+1.0` | `-1.0` |
| `mean_spread` | `+0.00812` | `-0.00794` |
| `experiment_id` | 一个串 | **同一个串** |
| 退出码 | 0 | 0 |
| 有无拒绝 | — | **无** |

`subject_digest`、`universe_digest`、`input_partition_hash`、五个 `census_*` 在两个 store 里
**逐字节相同**，因为它们描述的都是这次 build **读了什么**，而不是它**说了什么**。

#### 摘要盖在哪一层：进 `manifest_id`，不是记录在旁边

这是一条判断而不是顺手。摘要若在 `manifest_id` 之外，它就是一列篡改者会与它所描述的值
**在同一次改写里一起改掉**的列 —— 两个分区各自自洽、每个身份都不动，等于把同一个洞挪到隔壁表。
进了 `manifest_id`，`panel_factors._manifest_from_rows` 已有的「重装出来的身份必须等于行被归档
的那个身份」就顺带守住了摘要列本身。`FactorInputProvenance` 的 `batch_digest` 被移**出**身份是
相反方向的同一条规则：**当且仅当一个字段是内容的性质时，它才属于内容地址** —— 而摘要就是内容。

`V2-P3-015` 踩过的「按字节比较把内容相同的重跑判成冲突」在这里不成立：摘要打的是**内容**
（标的、覆盖码、值），用 `set_digest` 与 `stable_model_id` 同一套规范化，没有墙钟、没有文件布局、
没有 provider 批次。同样输入的重算逐字重现它，所以 `manifest_id` 仍可重现，
`write_factor_panels` 的丢弃守卫仍有重建通路。

#### 摘要覆盖什么

`(subject, coverage, value)` 三元组 —— 值**与**覆盖码，因为把 `computed` 改成 `input_missing`
必须被抓到（`validate_factor_observation` 强制非 `computed` 码带 `None`，所以这种改动同时动两个
位置），而把 `input_missing` 改成 `not_in_universe` **只动覆盖码**，这正是覆盖码必须是一个位置
而不是点缀的原因。窗口列与 `input_row_count` 在地址之外，且这一残留是**披露**而不是推断：
`KNOWN_FACTOR_SEAL_LIMITATIONS` 三条分别说明它覆盖到哪、它只证明「行是那个 build 自己的」
而不证明「那个 build 是对的」、以及它只够到六个派生分区。

#### `panel doctor` 怎么延伸到因子平面：不给 `DATASET_CADENCE` 加条目

`V2-P3-002` 当时说「派生数据集没有诚实的 cadence」，评审确认「洞是响亮的不是静默的」。
到 `V2-P3-019` 这个洞不再响亮了：拒绝发生在**数据集名字**上，后面的一切从来没有跑过 ——
够不到一个平面的闸门不是一个响亮的洞，是一个有借口的缺席闸门。

两条出路只有一条诚实。**逐因子往 `DATASET_CADENCE` 里加条目写不出来** —— 那张表的全部价值在于
一条测试断言「`panel_ingest` 写的每个数据集都在里面」，而派生名按 (因子 × 档位) 铸造、契约上无界，
通配符会终结这张表存在的理由。所以是**一条 `derived` 节奏 + 谓词**：它不是假的排期，
`Cadence` 另外五个成员各自蕴含一个界限，这一个蕴含界限的**不存在**，写在
`FreshnessPolicy.basis` 上并在下游显示为 `checks_waived`。`event_driven` 是最接近的误选并被拒绝 ——
它的意思是「没有排期，某一年没有行是正常的一年」，那是关于一个**不规律发布的上游**的断言，
而派生分区根本没有上游。

而因子平面真正需要这份报告做的**根本不是新鲜度判定**：报告其余每一项检查都是「取来的面板落后了」
或「与兄弟数据集互相矛盾」，派生平面的故障模式在**种类**上不同 —— 一个已存的答案不再是它的
manifest 所寻址的那个答案。所以它是**单独一条体检路径**（`_factor_seal_check`），
因为它是一个单独的问题。

#### 分层是设计的决定因素

`tests/unit/test_panel_ingest_import_isolation.py::test_panel_doctor_joins_domain_panel_and_panel_ingest_and_nothing_else`
以**等号**钉住 `panel_doctor` 的兄弟集，所以 doctor 不能 import 它要审计的三个平面 ——
这不是需要绕开的障碍，正是 `panel_gate`、`panel_view` 乃至 HTTP app 能 import doctor 而不拖进
三个因子平面的原因。于是：`cross_section_digest` 落在 `domain/`（写地址的引擎与重算地址的报告
都可以看的地方），`FACTOR_PLANE_SEALS` 把平面形状声明成**数据**，再由一条**同时 import 两边**
的运行期审计对账 —— 这正是本仓库对「表与实现漂移」的标准答案。

`panel_doctor` 因此成为 `FILTERED_READ_CALLERS` 的第三个成员，它的理由也是三个里最锋利的：
它是唯一一个读**不是自己写的**分区的调用者，而一个**短**截面的哈希会变成**冤枉**而不是缺行。
使它成立的性质写死在写路径上 —— 三档的每一条写路径都把一行的四个时钟全部盖成该 build 自己的
`as_of`，所以一个 build 要么整体可见、要么整体被挡，`read_visible_at` 永远不会返回它的真子集。

#### 已存分区怎么办：拒读并重建

与 `V2-P3-018` 同一个答案，早一列。manifest 分区 28→29 列、transform manifest 35→36 列、
neutralisation manifest 各加一列，旧 build 写的分区在 readiness 上以 `field_missing` 拒读而不是
错位解码。**这正是 `manifest_id` 的用途**：因子分区是派生物。

#### 哪些身份移动了、哪些没有（实测）

19 个 `factor_id` **一个没动**（`FactorDefinition` 的字段集一字未改）；
`manifest_id`、`transform_manifest_id`、`neutralization_manifest_id`、`experiment_id` 全部移动 ——
夹具上的 `experiment_id` 从 `fxp_d6b0c8465d4e5826700fdddf` 变成 `fxp_42ae1c6d22db08f5308402a6`。
**抽出共用原语没有移动任何已存地址**：`observation_digest` 与 `processed_observation_digest`
保留各自的 `obs_` / `prc_` 前缀并委托给 `cross_section_digest`，一条测试双向钉住这个逐字节等式。

#### 数值栈

没有任何数值数组归约：地址是一次 `json.dumps` 加一次 sha256，比较是字符串相等。
ADR-0003 **没有被重新打开**。**运行时依赖仍是九个。**

---

### `V2-P4-001` + `V2-P4-025` 交付记录（2026-08-18）：三项契约升版、一次身份重写、`V2-P4-003` 顺带结清

两条一起设计，因为两条都撞上 §8 的身份重写迁移。下面按「必须显式回答的十个问题」逐条记。

#### 一、`mode` 的三处重复：顺手做掉，而且是**构造性**地做掉

`V2-P4-003` 记的三处（`domain/run.py`、`runtime/engine.py`（已随 `ResearchRunRequest`
迁到 `domain/run_request.py`）、`cli.py`）**必须全改**，否则 `--mode paper` 会被 CLI 挡住而两个契约
放行 —— 也就是那条 issue 描述的失败本身。既然三处都必须动，留给 `003` 只会让它维护三份清单。

做法是**一个声明**：`domain/run_mode.py::RunMode`（`StrEnum`），两个契约与 CLI 都引用它。
选 `StrEnum` 而不是 `Literal` 别名，是因为 Typer 需要真的 `Enum` 类来渲染 `--mode`，
`Literal` 别名会留下第三处。代价为零并且**是实测的**：`model_dump(mode="json")` 输出成员的
**值**，所以 `runs` 表的 payload 与 `ResearchEngine._load_or_start_recovery` 的 `request_digest`
逐字节不变（`tests/unit/domain/test_run_mode.py::
test_the_enum_serialises_to_the_bare_string_the_literal_did`）。
唯一对外可见的变化是生成的 schema 里 `mode` 变成 `$defs` 的 `$ref`；`web/src/types.ts` 从未镜像
`RunManifest.mode`。

`003` 建议的「加一条断言三者一致的测试」在单一声明下是**空的**（自己跟自己比）。装的是另一条：
`test_no_other_module_declares_the_mode_set` 读源码树，任何模块把三个以上模式名写成
**可执行**字符串字面量就红（散文排除，否则在这个仓库里不可证伪）。**一条豁免，而且以等式给出**：
`domain/run.py` 允许写 v1 的三个（`RunManifestV1` 必须说清它冻的是什么），
且**只允许**那三个 —— 把今天的五个抄回 `run.py` 会红在它想援引的那条豁免上。

#### 二、`attribution` 的形状：`model` 类目 + `unexplained_return`

- `AttributionTerm.category` 加 `model`。它**不是** `agent` 的同义词：agent 是 LLM 审议步骤，
  model 是拟合出的量化预测器（`V2-P4-011` 的 `AlphaModel`）。没有它，P4 由模型驱动的排名只能
  记到另外三个类目之一，报告会说「某个 agent 赚了模型赚的钱」。
- `ValidationResult` 加 `unexplained_return`，对账规则变成
  `sum(terms) + unexplained_return == net_active_return`。

**残差对谁而言**：对本条 `ValidationResult` 的 `net_active_return` 而言，是归因**自己没认领**
的那部分。不是回归残差，**更不是** `domain/factor_neutralization.py` 的中性化残差（那是逐名逐日的
截面量，这是一个决策一个窗口的一个数）。

**为什么值得一个字段**：`backtest/validation.py` 的末项吸收让对账校验**按构造**永远通过 ——
它永远不可能失败，因此从未测量过任何东西。残差独立成字段之后，加不起来的项集必须把差额说成一个
报告能打印的数。**校验仍然是校验**：说错残差或不说，照样失败，两个方向都断言了。
默认 `0.0` 而非必填，因为当时每个构造点的项集本来就恰好加到 `net_active_return`，`0.0` 是它们的
**诚实值**。`V2-P5-005`/`006` 已交付，非零的那一个开始出现：`OutcomeValidator` 不再吃这个默认值，
每个结果都写入测量出来的残差，持仓决策写的是非零的那一个（闭式对照上是 `0.1875` 对 `0.1796875` 的
净主动收益）。上面「两个方向都断言了」这句**被 `V2-P5-006` 实测复核为真**，也因此定位出真正的缺口
不在契约而在生产者与产品出口 —— 详见那两行。

#### 三、`horizon` → 可比枚举：收窄到**可数**的那一个单位

现状是 `V2-P1-017` 之后的 `str` + `HORIZON_PATTERN`（四单位）。「可比」需要两者共用的**一个度量**，
而 `domain/horizon.py` 实测过四个单位里只有 `d` 有：日历跨度含多少个交易日是变的，
**未来**那一段的数目根本不可知（`KNOWN_CALENDAR_LOOKAHEAD` 有一条 2020 年把已公告开市日改成休市的
修订）。所以：

- `SignalFrame.horizon` 收窄到 `COUNTABLE_HORIZON_PATTERN`（`^[1-9][0-9]{0,2}d$`）。
- `ResearchHorizon` 变成 `@total_ordering`，序建立在 `sessions` 上。按 `(unit, count)` 字典序会把
  `999d` 排在 `1w` 之下 —— 那是一个**错的**全序；跨单位比较改为按 `sessions` 已有的理由拒绝。
  **等式仍对四个单位成立**（`parse_horizon(h.text) == h` 是把 horizon 写回字段的安全前提）。
- `HORIZON_PATTERN` 本身不动：它是**标签窗口**的文法，`factor_view`/`labels`/`factor run --horizon`
  在用，调用者可以要一个 `sessions` 随后拒绝去数的窗口；**信号**不行。

**与 §8 的对齐是逐字的**：这是收窄**定义域**而不是换**类型**，仍是同一个 `str`，
所以 `signal-frame` **不升版**，`signal_id` 一个没动 —— 实测把 `d703905` 的树 checkout 到
scratch 目录跑出 `5d` = `sig_56c99d03db9841eb6da3fa18`、`10d` = `sig_ce51fb2fc77c9953f8560797`，
在 `tests/unit/domain/test_contract_identity.py` 里钉成字面量。

**这不只是省事**：一次运行产出的**聚合** `SignalFrame` 从不落库（落的只有它的 ID，在
`decisions.signal_ids` 里），所以 `signal-frame` 升版会移动一个**数据库无法重算**的身份，
迁移会按构造漏迁。这一条本身有测试
（`tests/unit/domain/test_contract_identity.py::
test_the_aggregate_signal_frame_is_referenced_but_never_stored`）。

#### 四、`test_schema_export.py:19` 改成了什么

那条 `endswith("/v1")` **在此之前就已经不在了**（`V2-P0B-005` 把它换成了从版本注册表读
`current_version`）。留下的弱点是文件名：`CONTRACT_MODELS` 的键是手写的 `"<contract>-v1"`，
升版后 `decision-ledger-v1.json` 会装着 `decision-ledger/v2` 的文档而全绿。

所以本次把**文档名也派生掉**：`domain/schema.py::schema_document_name` 由注册表的
`current_version` 生成文件名，`CONTRACT_MODELS` 走 `CONTRACT_REGISTRIES` 生成。测试里**没有任何
版本字面量**，绑的是四件事互相一致：磁盘上的文件集合、文档里的 `schema_version.const`、
注册表的 `current_version`、模型自己的 `schema_version` 默认值。再加一条
`test_no_superseded_schema_document_is_left_behind`（升版会**改名**，旧文件留在旁边就是一份
没有模型产出、没有测试重生成的已发布契约）。

`docs/api/schemas/` 因此变成 `decision-ledger-v2.json` / `run-manifest-v2.json` /
`validation-result-v2.json`（v1 三份删除，v1 形状由 `*V1` 快照类与 git 历史承载），
`evidence-snapshot-v1.json` / `signal-frame-v1.json` 不动。`web/src/typesContractDrift.test.ts`
的三个文件名同步。

#### 五、迁移：哪些存量被重新标识（实测）

`storage/migrations.py` 加第 5 号 `rewrite_contract_identities`，只管 `state.sqlite3`
（`panel/catalog.py` 那条分界不动，因子平面没有一个契约在本次升版清单里）。逐表：

| 表 | payload 升版 | 键移动 | 为什么 |
|---|---|---|---|
| `runs` | v1 → v2 | 否 | `run_id` 是调用者给的 |
| `decisions` | v1 → v2 | **是** | `DecisionLedger` 多了 `run_manifest_id` |
| `validation_results` | v1 → v2 | **是** | 多了 `unexplained_return`，且 `decision_id` 指向刚移动的键 |
| `research_reports` | 无版本 | **是** | `report_id` 哈希的 payload 里有 `decision_id` |
| `research_memory` | 无版本 | 否 | `decision_id` 是 `UNIQUE` 列 + payload 字段，重指 |
| `batch_tasks` | 无版本 | 否 | `BatchResultRef.decision_id` 嵌在 payload 两层下，重指 |
| `run_recovery` | 不动 | 否 | 只扫描：见下 |
| `checkpoints` / `portfolio_transitions` / `watchlist` / `batch_events` | 不动 | 否 | 不含决策引用 |

**读时透明 upcast 只给 `run-manifest/v1`**，因为它的存储键不是内容地址；
`decision-ledger/v1` 与 `validation-result/v1` 的 upgrade **按名拒绝**
（`IdentityRewriteRequiredError`）—— 这正是 §8 说的「不能靠读时透明 upcast」。
决策那条另有一个算术理由：`run_manifest_id` 在 `runs` 表里，行级 reader 根本没有它。

**收尾是一条运行期审计而不是一句 docstring**：`_audit_identity_rewrite` 在同一事务里重读整库，
任何 payload 仍停在旧版本、任何内容地址键与旁边的 payload 对不上、任何被取代的 `decision_id`
还活着，就整体回滚。完备性测试断言旧 ID 在**整库 SQL dump 里不存在**，而不只是新 ID 在。

**唯一拒绝迁移的东西**：`run_recovery` 里存着完整 `SignalFrame`，若某条的 horizon 是日历单位
（`3m`），它现在在契约外，而换算成会话数需要一个本仓从未测过的常数。所以整条迁移
`UnmigratableHorizonError` 拒绝并回滚，消息点名 run 并给两条补救；`openalpha migrate run`
把这条（且只有这条已知类型的）原因原文打出来。

#### 六、`RunManifest` 的内容寻址身份：两条路都论证过，走的是「两条都走」

- **只把 `config_digest`/`random_seed` 纳入某个既有运行级 ID**：本仓没有「既有运行级 ID」可纳入 ——
  §9 实测过 `stable_model_id` 的使用者里没有 `RunManifest`。要么把两个字段抄进
  `DecisionLedger`，那只修 §9 那两行，`RunManifest` 后来新增的输入还得再抄一次。
- **给 `RunManifest` 建内容寻址身份**：`run_manifest_id` = `stable_model_id(prefix="run")`，
  然后 `DecisionLedger` 携带**这个地址**。一个字段让**每一个**已声明运行输入一次性抵达
  `decision_id`，包括以后新增的。

走后者，并且把前者的目标也一并达成。**五个字段是「记录但不寻址」**，理由写在
`RUN_MANIFEST_UNADDRESSED_FIELDS` 里（是 mapping 不是 set，因为理由才是承重的那一半）：

- `started_at` / `finished_at` —— 墙钟。重跑同一份声明必须复现同一个地址，否则地址无法用来认出
  同一次运行。这条本仓付过两次学费：`FactorBuildManifest` 的 `built_at` **刻意不是字段**；
  `V2-P3-002` 的 `FactorInputRef` 因为 `batch_digest` 含 `fetched_at`，一次逐字节相同的重抓
  移动了每一个 `manifest_id`，后来被移进不哈希的 `FactorInputProvenance`。
- `status` —— 结果，不是声明。中断后重跑成功的是**同一份**声明。
- `checkpoints` —— 运行中增长的恢复记账，寻址它会让地址在运行途中变、并取决于进程崩过几次。
- `environment` —— 观测到的宿主事实。`platform.python_version()` 会随解释器补丁版本变，
  那会在没有任何研究输入变化的情况下移动每一个已存 `decision_id`。

`exclude` 是 `stable_model_id` 的一个**参数**而不是第二个哈希：全仓十四个身份派生点都走这一个
helper，另造一个 sha256 正是本仓到处避免的事。默认值原样透传成 pydantic 自己的 `exclude=None`，
所以**没有任何已存地址移动** —— 实测对 `d703905`，21 个 `factor_id` 加 1 个 `transform_id`
加 1 个 `neutralization_id` 逐字节相同。

#### 七、§9 的结论现在**不成立**了（实测）

`tests/integration/test_run_identity.py` 把 §9 的实验按原样重跑（真 `run_cycle`、固定时钟与
`run_id`、逐个变量单独变），每个变体自己的 `runtime_dir`：

| 变更 | `run_manifest_id` | `decision_id` |
|---|---|---|
| 无变更，重复运行 | 不变 ✅ | 不变 ✅ |
| 单独改 `code_commit` | **变** | **变** ✅ |
| 单独改 `config_digest`（`a*64` → `b*64`） | **变** ✅ | **变** ✅ |
| 单独改 `random_seed`（7 → 99999） | **变** ✅ | **变** ✅ |

另记一条 §9 没记的：引擎**本来就**拒绝在同一个 `run_id` 下写入请求摘要不同的第二次运行
（`RunConflictError`），所以那两个撞在一起的 `decision_id` 在**同一个库内**并不会真的相撞 ——
它们相撞的地方是跨库、研究记忆、导出，以及任何把内容地址当内容地址用的引用。

#### 八、`RunManifest` 里哪些是「记录但不寻址」的

见第六条的五个字段与理由。测试形状是两个方向 + 一条元审计：

- 九个被寻址字段**逐个单独变** → 地址必须变；
- 五个不被寻址字段**逐个单独变**（含墙钟的**两个方向**）→ 地址必须不变；
- `test_every_run_manifest_field_is_addressed_or_excluded_by_name` 把
  `RunManifest.model_fields` 划成两组（`schema_version` 是唯一豁免，单成员 `Literal` 无从变，
  改为断言它在被哈希的 payload 里），**第 n+1 个字段会红**；
- `test_every_exclusion_states_a_reason_rather_than_being_a_bare_name` 给理由设了长度下界，
  防 `"clock"` 这种把 mapping 变回 set 的写法；
- `test_no_two_field_variations_produce_the_same_address` 断言产出的地址**两两不同** ——
  P3 出现十次以上的「断言存在但在那个 fixture 上分不开两个答案」，在身份测试上风险最高。

#### 九、身份漂移的补测形状：三种，各挡一类失败

roadmap 说「全库无 golden ID 断言」。补的是**三种形状**而不是一种：

1. **Golden 钉子**（新）：四个固定夹具的精确 ID 字符串。**只有它**能挡「后来某个不相干的改动
   移动了某个没人想移动的地址」—— 两方向表做不到，它比的是两个 ID 而不是历史。
   `signal_id` 的两个 golden 取自 `d703905` 的树，因此是历史答案而不是今天的答案。
2. **两个方向 / 逐字段**（P3 的形状）。
3. **元审计**读 `model_fields`（P3 的形状）。

#### 十、升版之后哪些既有断言红了 —— 两类

**A. 契约升版的必然后果**（改的是版本字面量、字段清单或迁移链长度，不是行为）：

- `tests/unit/domain/test_records.py`：三处 `schema_version` 字面量；`DecisionLedger` 构造补
  `run_manifest_id`。
- `tests/integration/storage/test_versioned_reads.py`：两处「未知版本」探针原本用
  `run-manifest/v2`/`decision-ledger/v2` 当「这个 build 不认识的版本」，现在这两个版本它认识了
  → 改用 `/v3`；`test_registries_current_version_matches_each_model_default` 从比字面量改成
  读模型自己的 `schema_version` 默认值（**下次升版不用再改**）；夹具的 `horizon="3m"` → `"10d"`。
- `tests/integration/storage/test_migrations.py`、`tests/unit/runtime/test_composition_migrations.py`、
  `tests/unit/backtest/test_replay.py`、`tests/integration/test_cli_migrate.py`：迁移链从 4 条变 5 条。
- `tests/unit/domain/test_horizon.py`：`REPOSITORY_HORIZONS` 拆成「文法认的」与「信号认的」两个元组。
- `tests/integration/storage/test_sqlite_repository.py`：`DecisionLedger` 构造补 `run_manifest_id`。
- `web/src/types.ts` 的 `category` 联合加 `"model"`；`web/src/typesContractDrift.test.ts` 三个文件名。

**B. 行为改变（我改变了行为，逐条说明）**：

- `tests/integration/test_replay_persistence.py` 两处 `mode="live"` → `"replay"`。
  原因：`mode` 是 `RunManifest` 的已声明输入，因此经 `run_manifest_id` 抵达 `decision_id`，
  一次 live 运行与一次 replay 运行**不再共享** `decision_id`。这是**有意的读法** ——
  replay 下得到的决策不是 live 下得到的同一个决策，此前没有任何东西能把两者分开 ——
  而 `ReplayRunner` 的 case 就是以 `replay` 跑的，所以直接运行也应当用 `replay`。
- `api/app.py::_parse_research_result` 现在也剥离并**校验** `manifest.run_manifest_id`。
  不改会让 `POST /api/v1/backtests/validate` 对自己刚返回的 `research` 报 422
  （`RunManifest` 是 `extra="forbid"`）。校验而不是只剥离，理由与 `signal_id`/`decision_id`
  相同：能交回一个未校验的清单地址，就能交回一个与旁边的清单对不上的地址。
- `cli.py::migrate_run` 在原因是 `UnmigratableHorizonError` 时把原文打出来（只这一个自有类型）。

#### 数值栈与依赖

没有任何数值数组归约；地址是一次 `json.dumps` 加一次 sha256。ADR-0003 未重新打开。
**运行时依赖仍是九个。** `lint-imports` 7 kept / 0 broken（`storage/migrations.py` 新增的
`domain.*` 与 `batch_contracts` 依赖都是向下的）。
