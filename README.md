# OpenAlpha CN

面向中国 A 股的证据可追溯、时间点一致、多智能体可验证的开源投研系统。

[English](README.en.md) · [部署方案](docs/deployment/production.zh-CN.md) · [数据接口](docs/api/data-interface.zh-CN.md) · [为什么能形成优势](docs/why-openalpha-cn.zh-CN.md) · [功能台账](docs/release/openalpha-v1-feature-ledger.md)

## ✨ 核心特性

> **不只是堆叠更多 Agent 人设。** OpenAlpha CN 把 A 股研究中的数据、证据、智能体、风险、决策和回放连成一条可验证、可复现、可追溯的研究链路。

### 🇨🇳 A 股原生证据体系

- **本土市场语义**：原生规范化涨停、炸板、连板、题材、催化、公告和资金观察，不把海外市场字段生硬套用到 A 股。
- **四时钟防前视**：分别记录事件发生、首次可知、系统入库和数据修订时间，历史研究只能读取决策时刻已经可见的证据。
- **交易规则内建**：覆盖 T+1、100 股整手、停牌、涨跌停锁单和交易成本约束。

### 🤖 可验证的多智能体决策

- **证据驱动协作**：市场事件、题材催化和资金流智能体经证据感知路由协作，每项输出都引用 `evidence_id`。
- **结构化决策链**：用 `SignalFrame`、`DecisionLedger`、风险门和显式弃权替代无法审计的自由文本结论。
- **模型可插拔**：无 LLM 时可确定性运行；接入模型后强制结构化输出、Schema 校验和有界重试。

### 🔁 同路径回放与归因

- **同一研究内核**：实时研究、历史回放与验证共用 `run_cycle`，避免线上逻辑和回测逻辑各走一套。
- **确定性回放**：内置 60 个交易日、300 个代表性事件的冻结语料，验证结果一致性和已知前视违规。
- **结果可解释**：统一计入 A 股交易约束与成本，并提供规则、因子和智能体归因。

### 🔌 开放的数据与使用接口

- **合规数据接入**：支持用户自有 CSV、JSON、JSONL、Parquet，用户自带 Token 的 Tushare Pro，以及默认关闭的可选 AKShare Adapter。
- **失败必须显式**：Provider 统一声明凭据、来源、许可、时效、限流与失败语义，禁止把数据错误伪装成“空结果成功”。
- **多入口一致**：同一能力通过 REST API、Python SDK、CLI 和响应式 React 研究工作台开放。

### 🛡️ 可复现的工程底座

- **完整复现清单**：`EvidenceSnapshot` 内容寻址；`RunManifest` 记录代码、配置、Provider、模型、Prompt、随机种子和环境版本。
- **可恢复运行**：SQLite WAL 保存运行、决策和 Checkpoint，研究任务支持幂等恢复；Docker 卷验证重启后证据仍然存在。
- **完成度可审计**：72 项功能全部具有唯一 ID、源码证据、测试证据和终态去向，`UNREVIEWED=0`、`UNKNOWN=0`。

## 两种使用方式

### 1. 自托管 OpenAlpha CN

推荐使用 Docker Compose：

```powershell
git clone https://github.com/ss8875/openalpha-cn.git
Set-Location openalpha-cn
docker compose -f deploy/compose.yml up -d --build
Start-Process http://127.0.0.1:8000
```

运行数据保存在独立 Docker 卷中。仓库包含：

- 四时间戳 Point-in-Time 证据与防前视查询；
- 涨停、炸板、连板、题材、催化、公告、资金等 A 股证据语义；
- 可复现的市场、题材和资金智能体，外加结构化模型扩展边界；
- `SignalFrame`、`DecisionLedger`、`RunManifest`、风险门和显式弃权；
- 实时研究与历史回放共用的 `run_cycle`；
- A 股 T+1、整手、停牌、涨跌停锁单和交易成本约束；
- 60 个交易日、300 个事件的冻结回放语料；
- 规则、因子、智能体归因；
- REST API、Python SDK、CLI 和响应式研究工作台。

不使用 Docker 时，开发者可以执行：

```powershell
uv sync --locked --all-extras --dev
uv run openalpha doctor
uv run openalpha serve
```

### 2. 不想本地部署

可直接下载 **链邻涨停复盘策略软件 1.0.9**：

[下载 Windows x64 安装版](https://github.com/ss8875/openalpha-cn/releases/download/chainlin-desktop-v1.0.9/Lianlin-LimitUp-Review-Setup-1.0.9-x64.exe)

- 文件名：`Lianlin-LimitUp-Review-Setup-1.0.9-x64.exe`
- 大小：`144,902,921 bytes`（约 `138.19 MiB`）
- SHA-256：`0DDD3AF69C671C3AF0F7AEC90D57B77363705E38E871B49D640C7A2D0D05838B`
- 当前安装包未做数字签名，Windows 可能显示 SmartScreen 提示
- 链邻桌面软件是单独发行物，不自动适用本仓库 MIT 许可证

```powershell
Get-FileHash .\Lianlin-LimitUp-Review-Setup-1.0.9-x64.exe -Algorithm SHA256
```

<p align="center">
  <img
    src="./assets/brand/wechat-contact-qr.jpg"
    alt="扫码添加微信，咨询链邻软件与 OpenAlpha CN"
    width="360"
  />
</p>

<p align="center">扫码添加微信，咨询安装、部署、数据接入和产品使用问题。</p>

## 数据优势如何体现

OpenAlpha CN 不把“多接几个行情 API”当作数据优势。优势落在可验证的数据合同：

```text
合法来源 / 用户自有数据
→ event_time / available_time / ingested_time / revision_time
→ 内容寻址 Evidence Snapshot
→ 仅使用决策时刻可见信息
→ 智能体、风险与决策完整引用 evidence_id
→ 同路径回放、结果验证与归因
```

默认支持用户自有 CSV、JSON、JSONL、Parquet，用户自带 Token 的 Tushare Pro，以及可选、受限的 AKShare Adapter。项目提供数据接入接口，但不提供商业数据转售代理，也不把受限原始数据放进 GitHub。

详见[数据接口与合规边界](docs/api/data-interface.zh-CN.md)。

## 与高星项目竞争的核心

TradingAgents 和 AI Hedge Fund 强在角色编排与社区影响力。OpenAlpha CN 的竞争重点不是复制更多“投资大师人格”，而是：

1. A 股原生事件语义和交易约束；
2. 证据首次可知时间与防未来函数；
3. 每个结论、决策、回放和归因都能追溯；
4. 无 LLM 也能确定性运行，接入 LLM 时强制结构化输出和有界重试；
5. 同一核心路径贯通 API、SDK、CLI、Web 与回放；
6. 功能状态必须有源码和测试证据，愿景、Stub、按钮不算完成。

源码审计基线、差异化结论和后续边界见[竞争优势说明](docs/why-openalpha-cn.zh-CN.md)。

## 公开 API

| 能力 | 入口 |
|---|---|
| 健康检查 | `GET /health` |
| 构建/查询证据 | `POST /api/v1/evidence/build` / `GET /api/v1/evidence` |
| 市场事件/题材 | `GET /api/v1/market/events` / `GET /api/v1/themes` |
| 多智能体研究 | `POST /api/v1/research/run` |
| 冻结语料回放 | `POST /api/v1/backtests/replay` |
| 结果与归因 | `POST /api/v1/backtests/validate` |
| OpenAPI | `GET /docs` / `GET /openapi.json` |

默认只绑定 `127.0.0.1`。如果要跨机器开放，必须在前置网关配置 HTTPS、认证、访问控制和限流。

## 验证

```powershell
uv sync --locked --all-extras --dev
uv run pytest --cov=openalpha_cn --cov-report=term-missing --cov-fail-under=80
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts
uv build
uv run python scripts/build_feature_coverage.py --check
uv run python scripts/verify_publication.py
```

```powershell
Set-Location web
pnpm install --frozen-lockfile
pnpm audit --audit-level high --registry https://registry.npmjs.org
pnpm lint
pnpm test
pnpm build
pnpm test:e2e
```

容器持久化恢复实测：

```powershell
uv run python scripts/verify_compose_recovery.py
```

## 许可证

OpenAlpha CN 源码采用 [MIT License](LICENSE)。第三方数据、模型、服务、品牌素材与链邻桌面安装程序保留各自授权边界，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
