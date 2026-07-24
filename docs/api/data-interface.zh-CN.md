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

历史查询必须同时满足：

```text
event_time <= as_of
available_time <= as_of
ingested_time <= as_of
revision_time <= as_of
```

后来修订的数据以新 Evidence Snapshot 进入，不覆盖旧内容。内容变化会生成新的哈希和 ID。
