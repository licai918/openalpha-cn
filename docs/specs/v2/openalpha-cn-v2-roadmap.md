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
| **P3** | 因子层 | 18 | 5–6 周 | 13–15 周 | 首批因子出齐 raw/processed/neutralized 三档 |
| **P4** | 候选排序与模型基线 | 25 | 6–7 周 | 15–18 周 | 契约升版一次完成 + 预测先落库 |
| **P5** | 组合、验证与工作台 | 24 | 6–8 周 | 15–20 周 | 归因对账 + 多重检验 + 4 页可用 |
| | **合计** | **118** | **28–36 周** | **70–90 周** | |

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
| `V2-P3-017` | **扣非净利列进入统计投影 + EPcut**（`V2-P3-009` 的第四个因子，见下方小节） | 技 | `V2-P1-011` 存储契约 | 已交付：`fina_indicator` 的投影 11→12 列，新增 `profit_dedt`，并在其上出厂第 20 个因子 `deducted_earnings_yield_ttm`。**加哪一列不是偏好而是实测**——`income` 根本不服务这一族（85 个字段里一个都没有，且点名请求会被**静默丢弃**，放进 `income` 投影会让每一次 `income` fetch 都被 `checked_response_fields` 拒绝）；同一批原始行读五遍（101 票 / 6,138 filing，另一组不相交 101 票 / 5,980 filing）：加 `profit_dedt` 与 `dt_eps` 折叠行数与歧义 filing 数**一字未变**，加 `dt_netprofit_yoy` 则把 4 个（另一样本 1 个）filing 从折叠挪进歧义 —— **那条 limitation 的条件句对一列成立、对旁边一列不成立，只有实测能分开**；五种投影下既有 11 列的逐列拒绝数全部逐位相同。代价：每个已存 `fina_indicator` 分区以 `field_missing` 拒读并重取、以真实行钉住字段列表的契约测试同改、以及 `profit_dedt` 自己 1.075% / 0.769% 的拒绝率（EP 的列是 0.189% / 0.459%）| S16 |
| `V2-P3-018` | **`FactorCoverage` 第六个码：把「这只票的这次 filing 有歧义」变成单票覆盖码而不是整 build 拒绝**（`V2-P3-009`..`011` 共用的墙，见下方小节） | 技 | `V2-P3-002` 存储契约 | 已交付 `ambiguous_filing`，插在 `insufficient_history` 与 `input_missing` **之间**（该位置就是 `_classify` 的判定优先级，由一条读 AST 的审计对账）。标记按 `(subject, period)` 记在 `_DatasetReading` 上，只对**窗口真的覆盖到那一期**的票生效；会话轴一字未动，第二行照旧拒绝。**schema 迁移**：manifest 分区 27→28 列、transform manifest 34→35 列，旧分区在 readiness 上以 `field_missing` 拒读而不是错位解码 —— 因子分区是派生物、`manifest_id` 使其可重建，`storage/migrations.py` 只管 `state.sqlite3`。**身份**：`transform_id` 移动（覆盖码词表就是 `MissingValuePolicy` 的字段集，在 `FactorTransformSpec` 的哈希载荷里），19 个 `factor_id` 一个没动，两边都用 `04c45b8` 的字面量钉住 | S16 |
| `V2-P3-019` | **给已存因子截面盖上它自己答案的内容地址**（P3 产品验收的 Critical-1，见下方小节） | 技 | `V2-P3-002`/`003`/`004` 存储契约 | 实测：把 `factor_obs_reversal_1d_v1/2026/data.parquet` 全部 16 行的值翻号、删掉 `runtime/experiments`、跑真的 `openalpha factor run` —— `mean_ic` 从 `+1.0` 变成 `-1.0`、`mean_spread` 跨过零，`experiment_id` **逐字节相同**，退出码 0，全链无拒绝。根因三条、各自封堵一条：① build manifest 对**输入**和**标的集合**取摘要、从不对**答案**取摘要 —— `FactorBuildManifest.observation_digest` 及其两个孪生 `processed_observation_digest` / `neutralized_observation_digest` 补上，且是**进身份的**字段而不是 `FactorInputProvenance` 那种「记录但不寻址」（后者会被篡改者与它描述的值一起改掉，进了 `manifest_id` 才由解码器已有的身份自检来守）；② 唯一可能开火的守卫「同 `experiment_id` 两个 `content_digest`」**是有状态的**，只在本机先跑过诚实版本时才生效 —— 面板上的封缄是无状态的；③ `panel doctor` 按**名字**拒绝因子数据集（无发布节奏），P2 建的 fail-closed 闸门止步于原始数据平面。**不给 `DATASET_CADENCE` 加条目**（派生名按因子铸造、不可枚举），改为一条 `derived` 节奏 + 谓词，并新增两个 **blocking** 码 `factor_seal_broken` / `factor_build_unaddressed`。**分层决定了设计**：`panel_doctor` 的兄弟集被等号钉死，不能 import 它审计的三个平面，所以 `cross_section_digest` 落在 `domain/`，`FACTOR_PLANE_SEALS` 以数据声明平面形状、由一条同时 import 两边的运行期审计对账 | S16, D8 |

**闸门**：每个因子同时出三档报告；因子合同测试使用冻结股票池/日历/公司行动/修正，证明 PIT 可见性与确定性取值；P2 红队测试仍全绿。

**风险**：首批因子中大部分 IC 不显著是**正常且有价值**的结果，不要靠调参"救活"。多重检验控制在 P5 才上，故 P3 的 IC 结论只能视为探索性，不得据此宣称发现。

---

## P4 — 候选排序与模型基线（25 issues）

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
| `V2-P4-026` | **`daily_basic` 的 as-of 敏感会话级读**（`V2-P4-013` 的硬前置，见 §11 的 `V2-P3-004` 复审小节） | 技 | P1 存储契约 | 中性化残差的四个时钟都盖构建 `as_of`，而该 `as_of` 必须 ≥ `daily_basic` 年分区的 `max_available_time`（该年最后一个会话）——于是**年 Y 任意交易日的残差在 Y 年内一律不可见**（行过滤，返回空而非报错）。`V2-P4-013` 的 walk-forward 因此只能做**年度**粒度；月度/日度做不了。不在 `V2-P3-004` 解决：修法要么给 `daily_basic` 换分区粒度，要么给 `load_daily_valuations` 一条「只读到 `day` 为止」的显式门，两条都动 `V2-P1` 的存储契约 | 集成：年内 `as_of` 能读到该日残差 | S27, S28 |

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
   `.the_two_foreign_inputs_are_read_whole_partition_so_a_mid_year_as_of_is_refused`）。
   而一年分区的 `max_available_time` 就是**该年最后一个会话**。

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

#### 年末快照那条约束写在哪

`KNOWN_EXPERIMENT_LIMITATIONS.neutralised_residuals_are_read_at_a_year_end_snapshot`
—— 与 `V2-P3-005`、`V2-P3-006` **同名同码**（同一件事，按模块改名就会变成三件事）。
本模块的措辞比它们更进一步，因为 neutralized 正是验收标准所转的那一档：
年内任何 `as_of` 经 `read_visible_at` 读回来的是**空而不是报错**，
所以它在这里现形为「neutralized 档 `ICSummary` 的 `coverage` 是 `insufficient_as_ofs`、
它参与的每一格都是 `not_measured`」，而不是一个异常。
`V2-P4-026` 是修法，且是 `V2-P4-013` 的硬前置。
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
默认 `0.0` 而非必填，因为今天每个构造点的项集本来就恰好加到 `net_active_return`，`0.0` 是它们的
**诚实值**；`V2-P5-005`/`006` 才开始产生非零的那一个。

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
