# OpenAlpha CN 与三套高星项目源码对账

审计日期：2026-07-24

## 结论

OpenAlpha CN 已包含 TradingAgents、AI Hedge Fund 和 TradingAgents-CN 的核心思想，
但不包含三者全部功能，也不应以复制全部角色、页面和运维接口为目标。

当前已形成的真实优势是：A 股事件语义、四时钟 Point-in-Time 证据、内容寻址、
显式弃权、同路径回放、A 股成交规则、组合硬约束、节点级恢复、持久研究记忆和
严格发布门禁。仍缺少大规模批量任务中心、可视化 Agent Flow Builder、完整筛选/
自选股产品面、事件研究显著性和多模型可视化配置。

## 锁定上游

| 项目 | Commit | Release/Tag | 许可证 |
|---|---|---|---|
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | `a33fd4c0f134485a43553a2c23a63cb14adbd88f` | `v0.3.1` | Apache-2.0 |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | `e7c784f118866c5dba8fc2c4ee545f08cc611c61` | `v2.0.1` | MIT；`app/frontend` 另有 MIT 归属 |
| [hsliuping/TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN) | `74783e8817d6cf6de29867880631cc555153f36b` | 仓库 `VERSION=v1.1.0`，无 GitHub Release | 混合许可：核心 Apache-2.0，`app/` 与 `frontend/` 为专有许可 |

许可证边界很重要：TradingAgents-CN 的 FastAPI 后端和 Vue 前端不能直接复制进
MIT 项目。本项目只对其功能行为做独立实现和对账。

## 源码级能力对账

| 能力域 | TradingAgents | AI Hedge Fund | TradingAgents-CN | OpenAlpha CN 当前状态 |
|---|---|---|---|---|
| 多角色研究 | 四类分析师、多空辩论、交易员、三方风险讨论、组合经理 | 19 类投资/因子 Agent、风险经理、组合经理 | 延续 TradingAgents 图并增加中国市场分析 | 已有证据路由、三类 A 股基线 Agent、结构化模型 Agent；未原样复制全部人格 |
| Graph/Router | LangGraph 条件路由、辩论轮次、SQLite checkpoint | v1 并行 Agent 汇总；v2 基金周期仍为 WIP | 条件图、工具循环上限和中文流程 | OpenAlpha 自有确定性 `run_cycle`，新增按节点恢复、请求摘要和图签名隔离 |
| 数据 | 海外行情、新闻、社交、宏观、多 Provider | Financial Datasets 的价格、财务、新闻、内部人交易 | Tushare、AKShare、BaoStock、Yahoo、Finnhub、多级缓存 | A 股事件证据、文件/Tushare/AKShare Provider、四时钟 PIT；链邻 API 仍是规划接入 |
| 模型 | 多家云模型、Ollama、OpenAI-compatible | 多家模型及结构化调用 | DeepSeek、Qwen、Google、OpenAI 等配置中心 | 新增安全 BYOK 的 OpenAI-compatible Provider；SDK 可注入自定义 Agent |
| 决策与组合 | 研究结论、风险辩论、组合经理 | 权重、订单、组合、long/short 回测 | 研究报告和纸面账户接口 | `SignalFrame`/`DecisionLedger`/风险门；新增现金、持仓批次、T+1、FIFO、费用与敞口硬限制 |
| 记忆与恢复 | 决策日志、反思记忆、节点 checkpoint/resume | v1 主要为单次运行；v2/app 有运行持久化 WIP | Chroma/Mongo/Redis、任务状态与报告 | 新增 SQLite 持久记忆与节点级恢复；不可变决策账本继续保留 |
| 用户产品面 | 交互 CLI | CLI、回测 CLI、WIP Flow Web | 登录、批量分析、队列、SSE/WebSocket、筛选、自选、报告、调度、缓存/数据库/日志管理 | REST、SDK、CLI、React 工作台；批量任务和管理中心仍待补 |
| 质量与安全 | CI、测试、路径加固、结构化输出 | v1 回测测试较完整；app/v2 标注 WIP | 大量测试/调试脚本，但 GitHub Actions 主要做镜像发布与上游同步 | Linux/Windows、浏览器、容器恢复、安全与发布扫描均为必需检查 |

上游源码证据示例：

- TradingAgents 图与 checkpoint：
  [`tradingagents/graph/trading_graph.py`](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/graph/trading_graph.py)
- AI Hedge Fund v1 入口与组合回测：
  [`src/main.py`](https://github.com/virattt/ai-hedge-fund/blob/e7c784f118866c5dba8fc2c4ee545f08cc611c61/src/main.py)、
  [`src/backtesting/portfolio.py`](https://github.com/virattt/ai-hedge-fund/blob/e7c784f118866c5dba8fc2c4ee545f08cc611c61/src/backtesting/portfolio.py)
- TradingAgents-CN 图、批量任务和纸面账户：
  [`tradingagents/graph/setup.py`](https://github.com/hsliuping/TradingAgents-CN/blob/74783e8817d6cf6de29867880631cc555153f36b/tradingagents/graph/setup.py)、
  [`app/routers/analysis.py`](https://github.com/hsliuping/TradingAgents-CN/blob/74783e8817d6cf6de29867880631cc555153f36b/app/routers/analysis.py)、
  [`app/routers/paper.py`](https://github.com/hsliuping/TradingAgents-CN/blob/74783e8817d6cf6de29867880631cc555153f36b/app/routers/paper.py)

## 本轮补齐的关键差距

1. **节点级恢复**：每完成一个 Agent 就持久化结构化结果；进程中断后从下一节点继续。
   同一 `run_id` 的请求摘要或图签名变化会硬失败，避免把旧 checkpoint 套到新图。
2. **持久研究记忆**：决策摘要以 `decision_id` 幂等写入 SQLite，SDK/API 重启后可查询，
   冲突写入会被拒绝。
3. **A 股组合账本**：维护现金、持仓批次、估值、累计费用和已实现盈亏；执行 100 股、
   T+1、停牌、涨跌停、佣金、印花税、FIFO 和单仓/总敞口限制。
4. **模型接入**：提供不保存密钥值的 OpenAI-compatible BYOK Provider，支持严格
   JSON Schema 或 JSON Object 模式；SDK 可以注入模型 Agent。

本地证据：

- `src/openalpha_cn/storage/recovery.py`
- `src/openalpha_cn/storage/memory.py`
- `src/openalpha_cn/backtest/portfolio.py`
- `src/openalpha_cn/models/openai_compatible.py`
- `tests/integration/test_recovery_and_memory.py`
- `tests/unit/backtest/test_portfolio.py`
- `tests/unit/models/test_openai_compatible.py`

## “超越”应如何判断

OpenAlpha CN 不以 Agent 数量或页面数量取胜，而以以下可验收指标取胜：

1. 历史结论只读取当时已知信息，已知严重前视违规为零；
2. 中断后不重复已完成节点，输入/图变化不能误用旧状态；
3. 数据不足必须弃权，基础设施错误不得伪装为空结果；
4. 研究结论必须经过 A 股可成交性、费用、现金和敞口约束；
5. 每个结果能追溯到证据、代码、配置、模型、Prompt 和恢复状态；
6. Linux、Windows、Web、容器和发布安全检查持续通过。

## 尚未完成的优先级

### P0

- 批量研究任务、取消、进度事件和并发上限；
- 模型 Provider 能力表、退避/429/5xx 分类重试和用量记录；
- 将组合状态、订单和成交持久化为独立不可变 Ledger；
- 链邻数据接口 API 的正式合同、认证、时效、限流与合约测试。

### P1

- Bull/Bear 可消融辩论和风险委员会，但必须证明比单 Agent 有增量；
- 事件研究 CAR、t 检验、Bootstrap 置信区间；
- 筛选、观察池、报告中心和批量分析工作台；
- 组合多日回测报告、基准、换手、容量和暴露归因。

### 明确不复制

- 真实券商下单、做空/回补和托管资金；
- TradingAgents-CN 专有许可的后端/前端源码；
- AI Hedge Fund `app/`、`v2/` 中没有稳定测试证据的 WIP 界面；
- 只增加人格名称、但没有独立数据、决策权或消融增量的 Agent。
