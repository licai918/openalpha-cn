# OpenAlpha CN main 开发交接（2026-07-24）

## 当前状态

- 分支：`main`
- 基线发布：`v1.0.0`
- 本轮性质：`v1.0.0` 之后的已验证开发增量，尚未创建新版本 Tag
- 实现提交：
  - `ddccf4d`：持久研究记忆与节点级恢复
  - `adf95f9`：A 股组合核算与风险硬限制
  - `947aecd`：安全的 OpenAI-compatible BYOK 模型 Provider
- 功能台账：75 项全部有唯一 ID、证据和终态；真实完成 70 / 75
  （93.33%），`UNREVIEWED=0`、`UNKNOWN=0`

## 固定上游审计基线

| 项目 | Commit | 版本 | 许可证 |
|---|---|---|---|
| TradingAgents | `a33fd4c0f134485a43553a2c23a63cb14adbd88f` | `v0.3.1` | Apache-2.0 |
| AI Hedge Fund | `e7c784f118866c5dba8fc2c4ee545f08cc611c61` | `v2.0.1` | MIT |
| TradingAgents-CN | `74783e8817d6cf6de29867880631cc555153f36b` | 仓库 `VERSION=v1.1.0` | 核心 Apache-2.0；`app/`、`frontend/` 专有 |

完整结论见
[`docs/audits/three-upstream-source-audit-20260724.md`](../audits/three-upstream-source-audit-20260724.md)。
结论是：OpenAlpha CN 已覆盖三者的核心研究链思想，但没有、也不应声称包含三者
全部功能。当前可验证的差异化优势是 A 股事件语义、四时钟 PIT 证据、内容寻址、
显式弃权、同路径回放、A 股成交与组合约束、节点续跑、持久记忆和严格发布门禁。

## 本轮完成

### 可恢复研究运行

- 每个 Agent 成功后写入完整、已校验的节点结果；
- 中断后从第一个未完成节点续跑，不重复已完成节点；
- 同一 `run_id` 的请求摘要或图签名变化会触发 `RunConflictError`；
- SDK 和 HTTP API 可查询恢复状态。

### 持久研究记忆

- 研究记忆从进程内存迁移到同一 SQLite WAL 数据库；
- 以 `decision_id` 幂等写入，冲突替换被拒绝；
- SDK 和 HTTP API 可按标的查询，进程重启后仍存在。

### A 股组合账本

- 维护现金、持仓批次、估值、费用与已实现盈亏；
- 实施 100 股整手、T+1、停牌、涨跌停、佣金、印花税与 FIFO 成本；
- 买入前执行单仓和总敞口硬限制；
- 拒单返回明确原因且组合状态保持不变；
- 当前是无副作用的研究/回测状态转换，不是实盘券商接口。

### 模型接入

- 新增 OpenAI-compatible Provider，支持 JSON Schema/JSON Object 输出；
- 密钥只从指定环境变量读取，不写入运行元数据；
- 非 localhost 端点必须使用 HTTPS；
- SDK 允许注入自定义/模型驱动 Agent。

## 验证记录

- `uv run pytest -q`：89 passed
- 覆盖率门禁：89 passed，88.09%，要求不低于 80%
- `uv run ruff check .`：通过
- `uv run ruff format --check .`：104 files already formatted
- `uv run mypy src scripts`：通过
- `uv build`：sdist 和 wheel 构建成功
- Web ESLint：通过
- Web Vitest：2 passed
- Web TypeScript/Vite production build：通过
- Playwright：桌面/移动端 4 passed
- 功能台账生成器 `--check`：75 / 70 / 93.33%，未知与未审计均为 0
- 发布安全扫描：155 个文件，0 blocker
- Docker Compose 恢复实测：容器重启后证据 ID 成功恢复
- `git diff --check`：通过

## 后继优先级

### P0

1. 批量研究任务：队列、并发上限、进度事件、取消与失败恢复；
2. 模型能力注册表：Provider 能力、429/5xx 分类退避、用量和成本记录；
3. 将组合状态、订单、成交持久化为独立不可变 Ledger；
4. 链邻数据接口 API：正式合同、认证、PIT/修订语义、限流、错误分类和合约测试。

### P1

1. 可消融的 Bull/Bear 辩论与风险委员会；
2. 事件研究 CAR、显著性检验和 Bootstrap 置信区间；
3. 筛选、观察池、报告中心与批量分析工作台；
4. 多日组合回测、基准、换手、容量和暴露归因。

## 边界

- 没有修改 `jdfp-next` 的数据接口或桌面运行时代码；
- 没有接入真实券商下单、做空或回补；
- 没有复制 TradingAgents-CN 专有许可的 `app/` 或 `frontend/` 源码；
- 链邻 API 仍是待签订正式合同的规划 Provider，当前没有把它宣传成已接通；
- AI Hedge Fund 的 `app/`、`v2/` WIP 不计为稳定上游能力；
- 旧版 `docs/release/handoff-v1.0.0.md` 是历史发布证据，不修改。

## 继续工作时的读取顺序

1. 根目录 `AGENTS.md`
2. `docs/HANDOFF_CURRENT.md`
3. 本文
4. `docs/specs/openalpha-cn-v1-spec.md`
5. `docs/release/openalpha-v1-feature-ledger.md`
6. `docs/audits/three-upstream-source-audit-20260724.md`
7. 与当前任务相关的架构、数据、API 或部署文档
