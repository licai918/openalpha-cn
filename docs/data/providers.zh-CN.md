# 数据源与 Provider 边界

OpenAlpha CN 只提供数据接入能力，不出售、不打包转售第三方原始行情。每条记录都必须保留
来源、四类时间、许可证/条款、再分发状态和内容摘要。

## 默认可用

### 用户自有文件

`FileProvider` 支持 CSV、JSON、JSONL/NDJSON 和 Parquet。输入记录需要包含：

- `subject`、`kind`
- `event_time`、`available_time`、`ingested_time`、`revision_time`
- `summary`
- `payload`（JSON/JSONL）或 `payload_json`（CSV/Parquet）
- 可选 `source_uri`

历史查询只返回 `available_time <= as_of` 的记录。格式错误会抛出结构化
`ProviderFailure`，不会伪装成“成功但没有数据”。

### Tushare Pro（BYOT）

用户自行申请并持有 Token，通过环境变量提供：

```powershell
$env:TUSHARE_TOKEN = "your-token"
```

v1 首个白名单接口为 `daily`。适配器直接调用 Tushare Pro HTTP API，不保存、不打印 Token。
日线记录使用保守的中国标准时间 16:30 作为可用时间；账户权限、积分、频率和数据使用范围以
[Tushare Pro 服务条款](https://tushare.pro/document/1?doc_id=405)为准。

## 可选且默认关闭

### AKShare 研究适配器

安装可选依赖：

```powershell
uv sync --extra akshare
```

v1 只允许调用 `stock_zh_a_hist`，不接受任意函数名。适配器面向本地研究使用，数据权利、
频率和稳定性仍取决于各上游来源。使用前请阅读
[AKShare 安装文档](https://akshare.akfamily.xyz/installation.html)及项目说明。

## 明确失败语义

Provider 只能：

1. 返回含记录的 `success`；
2. 返回带原因的 `no_data`；
3. 抛出带类别和可重试标记的 `ProviderFailure`。

认证失败、配置错误、限流、上游异常和响应结构错误都不得转换为空列表成功。

## 再分发规则

- 仓库只包含合成测试夹具，不包含付费或抓取的真实原始数据。
- 用户 Token、Cookie、账号、运行数据库和研究结果不得提交到 Git。
- `restricted` 或 `unknown` 数据只能按来源条款在用户本地使用。
- 新增数据源前必须记录来源 URL、条款、缓存策略、频率、新鲜度和失败语义。
