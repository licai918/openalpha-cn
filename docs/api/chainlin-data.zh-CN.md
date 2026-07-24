# 链邻数据接口 API 合同

OpenAlpha CN 只提供合同优先、用户自带授权的 `ChainLinDataProvider`，不内置链邻
商业数据库、Token 或转售服务。实际服务地址和密钥分别由
`CHAINLIN_API_BASE_URL`、`CHAINLIN_API_KEY` 配置。

## 请求

```http
GET {base_url}/datasets/{dataset}?as_of={ISO-8601}&subjects={codes}
Authorization: Bearer ${CHAINLIN_API_KEY}
Accept: application/json
```

允许的数据集：`daily`、`quote`、`limit_up`、`broken_board`、
`consecutive_board`、`theme`、`capital`、`disclosure`。

## 响应

响应必须满足
[`chainlin-data-v1.schema.json`](chainlin-data-v1.schema.json)。每条记录必须携带：

- `event_time`：事件实际发生时间；
- `available_time`：研究者首次可知时间；
- `revision_time`：该版本的修订时间；
- `subject`、`kind`、`summary`、结构化 `payload`；
- 可选 `source_uri`。

OpenAlpha 在接收时添加自己的 `ingested_time`，然后仍通过统一
`ProviderBatch` 和 `EvidenceSnapshot` 管线做 PIT 校验。

## 安全、限流和错误

- 非本机地址必须使用 HTTPS；
- 密钥只从环境变量读取并通过 Bearer Header 发送；
- 默认客户端上限为每分钟 60 次，可降低但不能关闭；
- 401/403 → `authentication`；
- 429 → `rate_limit`；
- 408/429/500/502/503/504 可重试；
- Schema、时间或字段错误 → `invalid_response`，不可静默降级；
- 空结果必须返回 `records: []` 和明确的 `no_data_reason`。

该合同已经通过完全冻结的传输替身测试；真实服务联调仍需要链邻服务端按照此
Schema 提供端点，不得把尚未配置的服务宣传为已连接。
