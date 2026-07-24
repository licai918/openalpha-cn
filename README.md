# OpenAlpha CN

面向中国 A 股的证据可追溯、时间点一致、多智能体可验证的开源投研系统。

> 当前版本：`v0.1.0` 开发中。OpenAlpha CN 仅用于研究、教学与复盘，不构成任何投资建议，也不承诺收益。

[English](README.en.md) · [开发规格](docs/specs/openalpha-cn-v1-spec.md) · [实施计划](docs/specs/openalpha-cn-v1-implementation-plan.md) · [安全政策](SECURITY.md)

## 选择你的使用方式

### 自托管 OpenAlpha CN

适合开发者、量化研究人员和需要接入自有数据或模型的团队：

- Point-in-Time 数据与防前视回放；
- Evidence Snapshot 证据快照；
- 多智能体研究、SignalFrame 与 DecisionLedger；
- 实时研究和历史回放共用运行路径；
- Provider、Agent、Tool、Risk、Validator 插件接口；
- REST API、Python SDK、CLI 与研究工作台。

当前开发入口：

```powershell
git clone https://github.com/ss8875/openalpha-cn.git
Set-Location openalpha-cn
uv sync --all-extras --dev
uv run openalpha doctor
```

### 不想本地部署？

可以直接使用 **链邻涨停复盘策略软件**。Windows x64 安装版不要求用户配置 Python、Node、数据库和开源项目运行环境。

[下载链邻涨停复盘策略软件 1.0.9（正式发布后生效）](https://github.com/ss8875/openalpha-cn/releases/download/chainlin-desktop-v1.0.9/Lianlin-LimitUp-Review-Setup-1.0.9-x64.exe)

安装包发布信息：

- 版本：`1.0.9`
- 平台：Windows x64
- 发布标签：`chainlin-desktop-v1.0.9`
- 数字签名：当前安装包未签名，Windows 可能显示 SmartScreen 提示
- SHA-256：正式上传前复核，并在 Release 中公布
- 商业桌面软件不自动适用本仓库的 MIT 许可证

<p align="center">
  <img
    src="./assets/brand/platform-wechat-banner.png"
    alt="链邻软件与 OpenAlpha CN 部署微信咨询"
    width="720"
  />
</p>

<p align="center">扫码咨询安装、部署和产品使用问题。</p>

## 为什么是 OpenAlpha CN

通用多智能体项目通常从模型对话开始。OpenAlpha CN 从“当时实际可见的 A 股证据”开始：

```text
数据与公告
→ Point-in-Time 事件
→ Evidence Snapshot
→ Agent 分析
→ SignalFrame
→ DecisionLedger
→ 历史验证
→ 归因与改进
```

重点覆盖涨停、炸板、连板、竞价、异动、题材、催化、公告、龙虎榜、资金和市场情绪等 A 股研究语义。

## 数据边界

默认支持：

- 用户自有 CSV、JSON、JSONL、Parquet；
- 仓库自带的合成测试数据；
- 用户自带 Token 的 Tushare Pro。

AKShare、交易所公告和商业数据源通过可选 Provider 接入。第三方代码许可证不自动授予第三方数据的再分发权；未经确认的数据不会作为仓库数据集或公共数据服务发布。

真实 `.env`、Token、Cookie、运行数据库、用户研究结果和链邻桌面数据库不会进入 Git。

## 当前状态

项目按可验证的垂直切片建设。每项功能只有同时具备源码、实际调用链、测试和文档证据后，才会标记为完成。

查看：

- [v1 规格](docs/specs/openalpha-cn-v1-spec.md)
- [v1 实施计划](docs/specs/openalpha-cn-v1-implementation-plan.md)
- [本地优先架构决策](docs/architecture/ADR-0001-local-first-runtime.md)
- [部署快速开始](docs/deployment/quickstart.zh-CN.md)

## 开发验证

```powershell
uv sync --all-extras --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
```

## 许可证

OpenAlpha CN 源码使用 [MIT License](LICENSE)。第三方数据、模型、品牌素材以及链邻桌面安装程序遵循各自的授权边界，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
