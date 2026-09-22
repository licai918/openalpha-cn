# 数据接口与合规边界

## 接口定位

OpenAlpha CN 提供的是 **BYOT（Bring Your Own Token/Data）研究接口**，不是公共行情转售服务。用户对输入数据的获取、使用和再分发权负责。

## 支持的数据入口

### 本地文件

```powershell
uv run openalpha evidence build .\events.json `
  --as-of 2026-07-24T10:30:00+08:00 `
  --source-id user.file `
  --source-license user-supplied `
  --redistribution restricted
```

支持 CSV、JSON、JSONL、Parquet。每条记录必须提供标的、类型、四时间戳、来源说明、摘要和结构化 payload。

### Python Provider

实现 `DataProvider`，返回 `ProviderBatch`。Provider 必须声明：

- 凭据环境变量；
- 缓存政策；
- 再分发状态；
- 限流和新鲜度；
- 失败类别与是否可重试。

`success` 必须包含记录；无数据必须返回带原因的 `no_data`；认证、配置、限流或上游错误必须抛出 `ProviderFailure`。

### REST

```http
POST /api/v1/evidence/build
GET  /api/v1/evidence?as_of=...&subject=...&kind=...
GET  /api/v1/market/events?as_of=...&subject=...
GET  /api/v1/themes?as_of=...&subject=...
```

构建接口接收 `ProviderMetadata + ProviderBatch`，返回版本化 `EvidenceSnapshot`。查询接口执行服务端 PIT 过滤。

## 默认 Adapter

| Adapter | 默认状态 | 凭据 | 再分发 |
|---|---|---|---|
| 用户文件 | 启用 | 无 | 由用户声明 |
| 合成 Fixture | 测试启用 | 无 | 允许 |
| Tushare Pro | BYOT | `TUSHARE_TOKEN` | 服从 Tushare 条款 |
| AKShare | 可选 | 视数据源而定 | 逐来源判断 |

## 对外部署

API 默认只绑定 `127.0.0.1`，没有多租户认证。若要在局域网或公网开放：

1. 前置 Nginx/Caddy/API Gateway；
2. 配置 HTTPS 和 HSTS；
3. 增加身份认证、授权、IP/用户限流；
4. 将 Provider Token 放入密钥管理系统；
5. 禁止返回受限原始 payload；
6. 单独完成数据许可和隐私评审。

不要直接把 `0.0.0.0:8000` 暴露到互联网。

## 时间和修订规则

证据在某一时刻是否可见，要同时满足两个条件：

```text
available_time <= as_of AND revision_time <= as_of
```

记录首次可知的时刻，和手里这一版内容发布（修订）的时刻，都不能晚于 `as_of`。`event_time` 与
`ingested_time` 随证据保存，不参与可见性判断。规则本身是 `domain/time.py` 的 `is_visible_at`；
下面是按它执行的各处（证据库与面板在 SQL 里照写同一个条件），之后是唯一不执行它的那道门：

- 研究请求与回放语料：带着不可见证据的请求或案例整条拒绝；
- Provider 批次：行式 `ProviderBatch` 与面板的 `ColumnarPanelBatch` 都整批拒绝。文件与 Tushare
  在组批之前先按同一谓词丢掉当时不可见的行，所以修订晚于 `as_of` 的一行不会让整次导入失败；
  链邻客户端不自行过滤，由批次整批拒绝；
- 证据库查询（证据、市场事件与题材接口都经过它）：`storage/parquet.py` 的
  `WHERE available_time <= ? AND revision_time <= ?`；出厂 Agent 读的是请求携带的证据，而请求本身
  在构造时就按 `EvidenceSnapshot.visible_at` 整条拒绝（上面第一条），Agent 自己不再过滤；
- 面板读取：`read_visible_at` 在 SQL 里对两个时钟同时过滤，并把因修订被扣下的行计入
  `withheld_row_count`；
- 财报：`StatementHistory.filings_on` 让一版从 `max(ann_date, f_ann_date)` 起可读，与面板上这一行
  的修订时钟是同一天。

两处**不**执行这条规则。一处是面板的整分区门（`PanelStore.read_if_ready`，调用处一律写作
`assessed(...).read(...)`）：它只判分区的 `max_available_time`、不读修订时钟，只对修订时钟恒等于
可得时钟的数据集才等同于完整可见性。四张财报不是这样的数据集——对它们，这道门会交出 `as_of` 之后
才重新公告的那一版；`src/` 里没有读者对财报走这道门（由
`tests/unit/panel/test_whole_partition_doors_never_hold_a_revision.py` 钉住），库的直接调用者
请改用 `read_visible_at`。

另一处是公开的 `PanelStore.query()`：它不收 `as_of`、不查就绪判决、也没有任何行级谓词，把分区的
每一行都交出去——实测一个真实的 `stock_basic` 2024 分区返回 152 行，其中 92 行在 2024-07-01 还不
可知。`src/` 里只有两个调用者（`AssessedPanelRead.read` 与 `panel_ingest.carry_stored_rows_forward`，
后者读了只为原样写回），由 `tests/unit/panel/test_query_callers.py` 钉成允许清单；库的直接调用者
不应拿它作答。这条也写在每份 `panel doctor` 报告的已披露限制里。

修订时间晚于 `as_of` 的记录在 `as_of` 时不可见。首次可得之后、`as_of` 之前修订过的记录
（`revision_time > available_time`）照常可见，构建时带上 `revised_after_initial_availability`
风险标记，风险门据此答 `reduce` 而不是 `block`——最终动作（`watch`/`avoid`/`abstain`）不因此改变。
从未修订过的记录（`revision_time == available_time`）不带这个标记。

后来修订的数据以新 Evidence Snapshot 进入，不覆盖旧内容。内容变化会生成新的哈希和 ID。但旧版本
只有当初入过库才存在：先入库原版、修订后再入库一次，`as_of` 落在修订之前时返回原版；第一次入库
拿到的就已是修订版的，修订生效之前就没有这条证据。

面板分区整体覆盖，没有版本历史，所以还有两处剩余缺口：

- 财报只存了更正后那一版的（`f_ann_date` 晚于 `ann_date`），在 `[ann_date, f_ann_date)` 内该期
  不可见——是缺失，不是前视。上游通常只提供现行版本，只有同一键下还留有较早 `f_ann_date` 那一行
  的，窗口内才能答出较早的版本；
- 同日只差 `update_flag` 的更正对，两行的四个时钟逐字节相同，可见性谓词分不开它们：两版从同一
  时刻（`max(ann_date, f_ann_date)`）起同时可见，按字段有分歧时读取拒答（`ReportFiling.value_of`）。
