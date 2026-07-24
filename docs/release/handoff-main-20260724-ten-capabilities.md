# OpenAlpha CN 十大能力补齐交接

日期：2026-07-24  
分支：`main`  
性质：`v1.0.0` 后未发布的兼容增量

## 已完成

1. SQLite 批量任务队列、1–32 并发上限、进度事件、合作式取消、失败重试和重启恢复；
2. 模型能力注册表、408/429/5xx 指数退避、401 立即失败、Token/成本持久记录；
3. 组合订单/成交/拒单的不可变 Ledger；
4. 链邻 `chainlin-data/v1` 合同、Bearer 认证、PIT/修订、客户端限流和错误分类；
5. 可消融 Bull/Bear 研究辩论和激进/中性/保守风险委员会；
6. CAR、t 统计量、确定性 Bootstrap 置信区间；
7. 结构化研究筛选；
8. SQLite 持久观察池；
9. 内容寻址、证据关联的报告中心；
10. 多日组合收益、基准、主动收益、换手、容量和暴露归因。

## 关键边界

- 链邻 Provider 是已实现、已做冻结合约测试的客户端适配器；真实调用仍要求用户
  配置服务端地址、Token 和数据授权，仓库不提供或转售商业数据。
- 批量取消不会强杀正在进行的外部 Provider/模型 HTTP 调用，只取消未开始项；
  正在运行项结束后记录明确终态。
- 模型成本是按用户配置单价计算的估算值，不替代 Provider 发票。
- 组合仍为 A 股多头研究/回测账本，不连接实盘券商。
- 任意 Agent 图形化 Flow Builder 仍为唯一 `DEFERRED` 能力。

## 当前台账

- 总功能：90
- 真实完成：85（94.44%）
- `NATIVE_COMPLETE=64`
- `ADAPTER_COMPLETE=1`
- `ENHANCED_REPLACEMENT=20`
- `EXCLUDED=4`
- `DEFERRED=1`
- `UNREVIEWED=0`
- `UNKNOWN=0`

## 验证记录

- 后端：103 passed
- 覆盖率：87.16%（门禁 80%）
- Ruff：通过
- Ruff format：125 files already formatted
- mypy：64 个源码模块无问题
- Python sdist/wheel：构建成功
- Web ESLint：通过
- Web Vitest：2 passed
- TypeScript/Vite production build：通过
- Playwright 桌面/移动端：4 passed
- Docker Compose 重启持久化：通过，临时容器、网络和卷已清理
- 功能台账：90 / 85 / 94.44%，`UNREVIEWED=0`、`UNKNOWN=0`
- 发布安全扫描：178 个文件，0 blocker

## 实现提交

- `4d3151c`：批量研究编排
- `9f95214`：模型治理与用量核算
- `f2e3fe2`：组合 Ledger 与多日报告
- `3e7b35d`：链邻数据 Provider
- `6a377c8`：可消融委员会与事件统计
- `c6cab47`：筛选、观察池与报告中心

## 继续工作

依次读取：

1. 根目录 `AGENTS.md`
2. `docs/HANDOFF_CURRENT.md`
3. 本文
4. `docs/release/openalpha-v1-feature-ledger.md`
5. `docs/api/http.md`
6. `docs/api/chainlin-data.zh-CN.md`
