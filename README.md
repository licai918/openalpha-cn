# OpenAlpha CN

面向中国 A 股的证据可追溯、时间点一致、多智能体可验证的开源投研系统。

[English](README.en.md) · [部署方案](docs/deployment/production.zh-CN.md) · [数据接口](docs/api/data-interface.zh-CN.md) · [三方源码对账](docs/audits/three-upstream-source-audit-20260724.md) · [为什么能形成优势](docs/why-openalpha-cn.zh-CN.md) · [功能台账](docs/release/openalpha-v1-feature-ledger.md)

## ✨ 核心特性

> **不只是堆叠更多 Agent 人设。** OpenAlpha CN 把 A 股研究中的数据、证据、智能体、风险、决策和回放连成一条可验证、可复现、可追溯的研究链路。

本轮已经把与高星项目对账后最关键的十类增强能力落到源码和测试中：

| 研究脑区 | 已落地能力 |
|---|---|
| 数据与证据 | 链邻合同型 Provider；Bearer 认证、客户端限流、PIT/修订语义与显式错误分类 |
| 规模化研判 | SQLite 持久批量队列；1–32 并发、逐项进度、协作式取消、重试与重启恢复 |
| 模型治理 | 模型能力注册、408/429/5xx 分类重试、Token/尝试次数/估算成本持久账本 |
| 辩论与风控 | 可消融 Bull/Bear 研究辩论；激进/中性/保守风险委员会 |
| 组合与验证 | 订单/成交/拒单不可变账本；多日收益、基准、主动收益、换手、容量与暴露归因 |
| 统计可信度 | CAR、t 统计量与确定性 Bootstrap 置信区间 |
| 研究产品 | 结构化筛选、持久观察池、内容寻址且关联证据的不可变报告中心 |

### 🇨🇳 A 股原生证据体系

- **本土市场语义**：原生规范化涨停、炸板、连板、题材、催化、公告和资金观察，不把海外市场字段生硬套用到 A 股。
- **四时钟防前视**：分别记录事件发生、首次可知、系统入库和数据修订时间，历史研究只能读取决策时刻已经可见的证据。
- **交易规则内建**：覆盖 T+1、100 股整手、停牌、涨跌停锁单和交易成本约束。
- **链邻统一数据入口**：已实现 `chainlin-data/v1` 合同型 Provider；可作为分散接口的统一替代入口，真实时效与精度由用户所配置的链邻服务、授权和上游数据决定。

### 🤖 可验证的多智能体决策

- **证据驱动协作**：市场事件、题材催化和资金流智能体经证据感知路由协作，每项输出都引用 `evidence_id`。
- **结构化决策链**：用 `SignalFrame`、`DecisionLedger`、风险门和显式弃权替代无法审计的自由文本结论。
- **双委员会研判**：Bull/Bear 研究辩论与激进/中性/保守风险委员会均可独立启停，既保留观点碰撞，也能做消融对照。
- **批量研究编排**：持久任务队列支持 1–32 并发、逐项进度、协作式取消、失败重试和进程重启恢复。
- **模型可插拔**：无 LLM 时可确定性运行；接入模型后强制结构化输出、Schema 校验和有界重试。
- **模型治理与核算**：按模型能力选择兼容端点，对 408/429/5xx 分类重试，并持久记录 Token、尝试次数与按用户单价估算的成本。
- **安全 BYOK**：内置 OpenAI-compatible Provider，支持 OpenAI、DeepSeek、Qwen、Ollama 或用户自建兼容端点；密钥只从环境变量读取。

### 🔁 同路径回放与归因

- **同一研究内核**：实时研究、历史回放与验证共用 `run_cycle`，避免线上逻辑和回测逻辑各走一套。
- **确定性回放**：内置 60 个交易日、300 个代表性事件的冻结语料，验证结果一致性和已知前视违规。
- **结果可解释**：统一计入 A 股交易约束与成本，并提供规则、因子、智能体与模型归因，未被认领的部分记在显式残差里而不是摊进最后一项。
- **不可变组合账本**：订单、成交、拒单和持仓转移全部追加记录；同时维护现金、持仓批次、估值、费用和已实现盈亏。
- **多日组合报告**：统一输出收益、基准、主动收益、换手、最大订单容量和标的暴露归因。
- **事件统计检验**：提供 CAR、t 统计量和可复现的 Bootstrap 置信区间，不用单条收益曲线代替显著性。

### 🔌 开放的数据与使用接口

- **合规数据接入**：链邻合同型 Provider 是面向 A 股数据的统一入口；用户自有 CSV、JSON、JSONL、Parquet 以及 BYOT/可选研究 Adapter 作为补充。
- **失败必须显式**：Provider 统一声明凭据、来源、许可、时效、限流与失败语义，禁止把数据错误伪装成“空结果成功”。
- **多入口一致**：同一能力通过 REST API、Python SDK、CLI 和响应式 React 研究工作台开放。
- **批量任务中心**：任务、并发上限、逐项进度、取消、重试和重启恢复均持久化。
- **研究产品接口**：提供结构化筛选、观察池和内容寻址报告中心。

### 🛡️ 可复现的工程底座

- **完整复现清单**：`EvidenceSnapshot` 内容寻址；`RunManifest` 记录代码、配置、Provider、模型、Prompt、随机种子和环境版本。
- **可恢复运行**：SQLite WAL 保存运行、决策、持久记忆和节点 Checkpoint；中断后从下一节点恢复，请求或图结构变化会拒绝误用旧状态。
- **完成度可审计**：功能台账中的每项能力都具有唯一 ID、源码证据、测试证据和终态去向，`UNREVIEWED=0`、`UNKNOWN=0`。

## 🧠 五图读懂 OpenAlpha CN

OpenAlpha CN 不是把智能体角色堆在一起，而是把 **A 股事实、时间点证据、结构化研究、风险裁决、组合会计与统计验证** 组织成一套可复现的研究操作系统。下面五张图按实际使用顺序展示系统如何工作。

**链邻数据接口 API** 已具备合同优先的 BYOK Provider，统一约束 Bearer 认证、四时钟 PIT、数据修订、客户端限流和失败分类。用户配置真实服务地址、Token 与数据授权后即可启用；仓库不内置或转售链邻商业数据。

五张图共用同一条“研究闭环导航”。图中 **实线表示系统自动执行或持久化**，**虚线表示调用方显式组合或人工反馈**；每一图都交付一个可以被下一图消费、也可以独立复核的结构化产物：

`市场事实 → EvidenceSnapshot → ResearchRunResult → DeliberationOutcome / PortfolioTransition → ValidationResult → 人工审阅后反馈到下一轮证据与规则`

### 01｜系统总览：一条由证据 ID 与不可变账本闭合的研究链

<p align="center">
  <img
    src="./assets/diagrams/openalpha-brain-01-overview.svg"
    alt="OpenAlpha CN 从 A 股事实到验证反馈的研究操作系统全景图"
    width="1200"
  />
</p>

### 02｜证据平面：先证明“当时可知”，再讨论模型是否聪明

<p align="center">
  <img
    src="./assets/diagrams/openalpha-brain-02-evidence.svg"
    alt="OpenAlpha CN 授权数据、Provider 治理、四时钟 PIT 与 EvidenceSnapshot 证据平面图"
    width="1200"
  />
</p>

### 03｜研究编排：让专业角色围绕同一证据规模化协作

<p align="center">
  <img
    src="./assets/diagrams/openalpha-brain-03-agents.svg"
    alt="OpenAlpha CN 持久批量任务、确定性研究内核、专业智能体与 ResearchRunResult 编排图"
    width="1200"
  />
</p>

### 04｜决策约束：把观点分歧压缩成可解释、可审计的状态变化

<p align="center">
  <img
    src="./assets/diagrams/openalpha-brain-04-decision.svg"
    alt="OpenAlpha CN 显式 Bull Bear 研究委员会、风险裁决与 PortfolioTransition 组合会计图"
    width="1200"
  />
</p>

### 05｜验证反馈：回答是否有效、为何有效、下一轮应该改什么

<p align="center">
  <img
    src="./assets/diagrams/openalpha-brain-05-replay-interfaces.svg"
    alt="OpenAlpha CN 同路径回放、事件统计、多日组合报告、ValidationResult 与人工反馈图"
    width="1200"
  />
</p>

## 不想本地部署

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

## 安装说明

### 安装链邻涨停复盘策略软件

适用于不希望配置 Python、Node、数据库和 OpenAlpha CN 运行环境的 64 位 Windows 用户。

#### 第 1 步：下载安装包

点击上方“下载 Windows x64 安装版”，或进入 [链邻桌面软件 Release 页面](https://github.com/ss8875/openalpha-cn/releases/tag/chainlin-desktop-v1.0.9) 下载：

`Lianlin-LimitUp-Review-Setup-1.0.9-x64.exe`

只使用 `github.com/ss8875/openalpha-cn` 发布的文件；不要运行从网盘、聊天群或不明网站转发的同名安装包。

#### 第 2 步：校验文件

打开安装包所在目录，在 PowerShell 中执行：

```powershell
Get-FileHash .\Lianlin-LimitUp-Review-Setup-1.0.9-x64.exe -Algorithm SHA256
```

确认以下两项完全一致：

- 文件大小：`144,902,921 bytes`
- SHA-256：`0DDD3AF69C671C3AF0F7AEC90D57B77363705E38E871B49D640C7A2D0D05838B`

如果大小或哈希不一致，请删除该文件并从 GitHub Release 重新下载，不要继续安装。

#### 第 3 步：运行安装程序

1. 双击 `Lianlin-LimitUp-Review-Setup-1.0.9-x64.exe`。
2. 当前安装包未做数字签名，Windows 可能显示 SmartScreen 提示。
3. 只有在下载地址、文件大小和 SHA-256 均已核验正确后，才点击“更多信息”并选择“仍要运行”。
4. 如果出现 Windows 用户账户控制提示，请再次确认文件名和来源，然后允许安装程序运行。

#### 第 4 步：完成安装

按照安装向导选择安装位置和快捷方式选项，确认后等待安装完成。安装结束后，可从安装完成页、Windows 开始菜单或桌面快捷方式启动软件；具体入口以安装向导实际创建的项目为准。

#### 第 5 步：遇到问题

- 无法下载：进入 [Release 页面](https://github.com/ss8875/openalpha-cn/releases/tag/chainlin-desktop-v1.0.9) 重新下载。
- 哈希不一致：不要运行，删除后重新下载。
- SmartScreen 拦截：先完成哈希校验，再按上面的安全步骤处理。
- 安装或使用仍有问题：扫描上方微信二维码，咨询安装、数据接入和产品使用。

### 安装 OpenAlpha CN

适用于希望连接自己的数据源、扩展 Provider 或智能体、使用 API/SDK/CLI/Web 的开发者和研究者。

#### 方式一：Docker Compose

准备 Git、Docker Desktop 或 Docker Engine，并确认 `docker compose version` 可以正常执行。

```powershell
git clone https://github.com/ss8875/openalpha-cn.git
Set-Location openalpha-cn
docker compose -f deploy/compose.yml up -d --build
docker compose -f deploy/compose.yml ps
Start-Process http://127.0.0.1:8000
```

浏览器打开 `http://127.0.0.1:8000`。接口文档位于 `http://127.0.0.1:8000/docs`，健康检查地址为 `http://127.0.0.1:8000/health`。

停止服务但保留研究数据：

```powershell
docker compose -f deploy/compose.yml down
```

运行数据保存在独立 Docker 卷中。不要执行 `down --volumes`，除非明确要删除本地研究证据、运行记录和决策账本。

#### 方式二：Python 源码环境

准备 Python 3.11 或 3.12，并安装 `uv`：

```powershell
git clone https://github.com/ss8875/openalpha-cn.git
Set-Location openalpha-cn
Copy-Item .env.example .env
uv sync --locked --all-extras --dev
```

编辑 `.env`，填入要用到的变量。`openalpha` 命令行（`doctor`/`serve`/`evidence`/`research`/...）
在每次执行时都会自动从当前工作目录读取 `.env` 并加载进入进程环境——但只补齐当前 Shell 里
还没有的变量：如果同名变量已经在 Shell 中导出（PowerShell: `$env:TUSHARE_TOKEN = "..."`；
bash/zsh: `export TUSHARE_TOKEN=...`），Shell 里的值优先于 `.env` 里的同名值，`.env` 里的值
又优先于内置默认值。这个自动加载只发生在 `openalpha` 命令行入口；直接以 Python 方式实例化
`OpenAlphaSDK` 或调用 `create_app()` 不会触发它，读到的仍然只是真实进程环境变量：

```powershell
uv run openalpha doctor
uv run openalpha serve
```

`openalpha doctor --probe` 会对**每一个**已声明的数据集发一次最小请求，并按数据集记录结果
（Tushare 现为 15/15；面板专供的四个走 `fetch_panel`，需要 `index_code`/`ts_code`/报告期年
的五个由 provider 自己给出最小主体）。凭证被端点拒绝（`authentication`）时命令**非零退出**，
`--json` 也一样——它不再在打印完 payload 之后直接返回；而「这个接口这个账号取不到」
（`upstream`）和「限流」（`rate_limit`）按数据集如实上报且**不**影响退出码，因为那正是这份
报告要交付的内容本身。

`serve` 绑定地址与端口的优先级是命令行参数 `--host`/`--port` > `OPENALPHA_HOST`/
`OPENALPHA_PORT`（含 `.env`）> 内置默认值 `127.0.0.1:8000`。每个数据源变量的用途见
[数据源与 Provider 边界](docs/data/providers.zh-CN.md)；完整配置、备份、恢复和升级方法见
[详细部署方案](docs/deployment/production.zh-CN.md)。

> 这里的 `.env` 自动加载目录是 `openalpha` 命令的当前工作目录（通常就是仓库根目录），与
> “方式一：Docker Compose”的 `.env` 自动加载目录 `deploy/` 不是同一个文件、也不是同一套
> 加载机制——两者互不影响，各自维护各自的 `.env`。

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

## 面板数据平面的三个命令

面板数据平面（16 个数据集：trade_cal / stock_basic / adj_factor / daily / daily_basic /
suspend_d / stk_limit / namechange / index_weight / index_daily / index_classify /
index_member_all / income / balancesheet / cashflow / fina_indicator）有三个命令：`openalpha panel build`
抓取并写入，`openalpha panel doctor` 体检已存数据，`openalpha data-check` 跑读取前的
fail-closed 依赖门。

**退出码就是交付物。** 三个命令共用同一张退出码表，各码对应的补救动作不同，所以不合并：

| 退出码 | 含义 | 补救 |
|---|---|---|
| `0` | 命令答完了，答案是「没有问题」 | — |
| `1` | **面板**有问题：依赖门拒绝、体检发现 `blocking`/`warning`、写时守卫拒收 | 重新抓取 |
| `2` | 由 click 保留（拼错选项、缺必填项），本模块永不使用 | 改命令行 |
| `3` | **请求**根本立不住：数据集无声明周期、`--as-of` 无法解析、构建目标不在闭表内 | 改命令行 |
| `4` | 抓取压根没发生：认证、配额、传输、响应无法解码 | 修凭证 |
| `5` | **命令本身崩了**，什么都没判定 | 提缺陷单 |

`5` 与 `1` 分开是刻意的：没有它，CLI 自身的异常会走 Typer 默认处理并以 `1` 退出，
「CLI 崩了」和「面板体检不过」在 CI 里就是同一个数字。

`panel doctor` 只在报告 `is_clean` 为假时非零退出 —— 即出现 `blocking` 或 `warning`。
`notice` 永不非零：`ambiguous_filing` 在真实财报上命中 8.15%/1.29%/15.80%/13.70% 的申报，
让 notice 非零等于让每次诚实体检都失败，然后这个命令会在第一条流水线里被 `|| true` 掉。

```bash
# 构建：十三个目标，按依赖序执行，与 --dataset 出现顺序无关
uv run openalpha panel build --dataset trade_cal --dataset price --year 2026

# 行业分类（P3 中性化的前置）：树按 vintage 年落盘，成分按 l1_code 切片全量扫描
uv run openalpha panel build --dataset index_classify --dataset index_member_all --year 2026

# 财报：ts_code 必填且没有横截面，所以是「每只证券一次请求」，默认取自已存的 stock_basic
uv run openalpha panel build --dataset stock_basic --dataset income --year 2024
uv run openalpha panel build --dataset income --year 2024 --subject 000001.SZ

# 多年：--year 可重复，或用闭区间 --start/--end；年份由老到新依次构建
uv run openalpha panel build --dataset trade_cal --dataset price --start 2015 --end 2026 --resume

# 体检：--json 输出与 REST/SDK 面序列化同一个 PanelHealthReport
uv run openalpha panel doctor --dataset daily --year 2026 --session 2026-01-16 --json

# 依赖门：阻塞时以 1 退出，这是它在 CI 里的全部意义
uv run openalpha data-check --dataset daily --dataset adj_factor --year 2026 \
  --session 2026-01-16
```

几点必须知道的语义：

- `panel build` 的目标是**工作单元**而不总是单个数据集。`price` = `daily` + `daily_basic`
  + `suspend_d` 一次会话循环取完，因为 `write_daily_panel` 必须同时收到前两者，且它的
  `halts` 参数没有默认值。所以 `--dataset daily` 会**按名字被拒**并告知原因，而不是被
  click 当作未知选项拒掉（后者读起来像「本仓库没有 daily 面板」，与事实相反）。
- **十四个目标覆盖 `providers/tushare.py` 声明的全部 16 个数据集。** 早先只有五个，
  `namechange`、`index_weight`、两个行业数据集和四个财报接口有 writer、有 loader、有体检
  检查，却没有抓取路径 —— `panel build --dataset income` 按名字被拒，`panel doctor
  --dataset income` 因此永远报 `partition_missing`。表里不存在的名字仍然**按名字被拒**，
  而不是给一个空的成功；`_audit_written_partitions` 会在运行期堵住「表里加了键、没加实现」
  这条缝（两个构建阶段都堵）。
- **三个目标的工作单元是「整次调用」而不是「一个 `--year`」**（`index_classify`、
  `index_member_all`、`fina_indicator`）。前两个的请求根本没有日期维度；`fina_indicator`
  的窗口过滤的是**报告期**、行却按**公告日**归档，所以一个公告年至少由两个报告期年拼成
  （上一年的年报 + 本年的三个季报），按年循环写会把前一年写进去的年报**静默替换掉**。
  跨调用的那一半由「不允许缩小已存公告年」的拒绝守住。
- **财报是全市场 5,881 次请求／数据集／年**（2026-08-11 实测）。每个抓取循环在发第一个
  请求之前先在 stderr 打一行 `BUDGET`，`--subject` 把这个扫描缩到点名的几只。不做上市
  生命周期过滤，因为两个方向都实测反例：`688981.SH` 2020 年才上市却有 2015 年公告的报表，
  `000003.SZ` 2002 年就退市却有 2024 年公告的报表。
- `--no-halts` 是一次**记录在案的弃检**，不是默认值：它让 `write_daily_panel` 拿到
  `halts=None`，从而关掉「缺失的行情没有任何东西解释」这条守卫。
- `panel doctor` 与顶层 `openalpha doctor` 是两个命令：后者探的是 **provider 凭证与能力**，
  前者读的是**面板本身**。
- 分区年份由**数据行自身的日期**决定，`--year` 只界定抓取范围；两者不一致时 `panel build`
  会拒绝并点名。四个目标是例外，每个都有自己的理由：`stock_basic` 按上市生命周期年拆分，
  `index_classify` 按 vintage 年（SW2014 → 2014，SW2021 → 2021），`index_member_all`
  按成分变更**事件年**（一次 62 请求的扫描落进约 38 个分区），`fina_indicator` 按公告年。
- 构建是一串「整分区写入」，之间没有事务。中途被拒时命令会**列出已经落盘的分区**，而不是
  声称什么都没写。
- **一次 `panel build` 只读一次时钟，`--as-of` 把这个时钟钉在多次调用之间。** 会话循环的
  上界是该时刻的 Asia/Shanghai 日期减一天，而一个面板往往是多次调用；跨过本地零点的构建会让
  一部分目标停在昨天、另一部分停在今天，产出的面板在**任何** `as_of` 都体检不干净
  （早于最新分区的最后一行 → `not_yet_knowable`，不早于它 → 旧分区 `date_gap`）。
  每次构建都会把用到的时刻打印出来（`--json` 里的 `as_of`，人类输出里的 `AS-OF` 行），
  之后单独重取某一个目标时把它传回来即可；而 `_refuse_split_horizon` 会在**取第一个会话
  之前**拒绝一个会造成这种分裂的构建，并给出能修好它的那个 `--as-of`。要把面板整体往前推
  一天，就在**一次调用里**同时点名所有会话级目标（分区是整体替换，没有追加）。
- 每个抓取循环都往 **stderr** 打进度（`FETCHING <目标> 40/145 sessions elapsed=89s
  eta=233s`），间隔是 `max(10, ceil(总数/40))` —— 145 个交易日仍然每 10 个一行，5,881 只
  证券则每 148 个一行；循环开始前还会先打一行 `BUDGET` 说明这次要发多少个请求。
  stdout 留给 `--json`，所以脚本调用不受影响。
- `stk_limit` 的横截面离它 7,800 行的每响应上限只剩 66 行（2026-08-10 实测 7,734 行，
  +2.231 行/交易日 ≈ 29.6 个交易日）。撞上后单次请求仍然**拒收**而不是存半截，但
  descriptor 声明了实测的 `page_size=4000`，provider 会用 `limit`/`offset` 分页重取同一个
  请求——分页顺序与整页响应逐元素一致，所以分区的 `content_digest` 与 store 的
  `content_hash` 都不变。
- **`--as-of` 钉住的是这次构建给数据打的时间戳，不是「什么算已知」的上界。** provider 的
  时点过滤器保留一行的条件是它在请求的 `as_of` **和这次抓取真正发生的时刻**都已可知，
  第二个时刻永远是墙钟，任何 flag 都够不到。`V2-P1-018` 曾把解析后的 `--as-of` 当作
  provider 的 `clock` 传进去，两个时刻塌成一个：把 `--as-of` 钉在墙钟之前方两天，
  当天 16:30 才发布的横截面就会被写进分区。现在 `stamped_at`（可钉）与 `clock`（墙钟）
  是两个入参，重建仍然是真正的 no-op（`content_hash`/`written_at`/文件 mtime 全不变），
  而两道下界都真的守。
- **`--year` 可重复，`--start`/`--end` 是闭区间，两种形式不能混用。** 此前 `--year` 是单值，
  click 只保留最后一个并**静默丢弃**其余：`--year 2025 --year 2026` 只构建 2026，
  没有任何输出提到 2025 被丢掉了。年份按从老到新执行。
- **`--resume` 是年粒度的断点续传，证据是数据本身而不是进度文件。** 某一年的某个目标，
  只要它写的每个会话级数据集都已经覆盖到这次构建会取到的最后一个会话，就跳过；写时的
  会话普查（`_session_census`）已经保证了这样的分区从 1 月 1 日起没有洞。`trade_cal` 与
  `stock_basic` 永不跳过（各一次请求）。**年内没有断点续传**：分区是整体写入、没有追加，
  半年份只能落成第二套磁盘格式，而它最坏的失败形态正是「看起来完整的半截」。
- 凭证不经过 CLI：`TushareProvider` 在自己的构造函数里解析 `TUSHARE_TOKEN`，
  `ProviderFailure` 的原始消息（可能带着 token 或整条 query string）永不打印、永不入日志。

## 因子库与三档因子实验（P3）

面板平面之上是**因子平面**：19 个声明因子（动量反转 5 / 波动流动性 4 / 价值 3 / 质量 4 /
成长 3）、1 个截面变换、1 个行业与市值中性化，以及把三档拿去打分并密封成不可变产物的
实验流程。四个命令，按操作者遇到它们的顺序：

```bash
# 1. 这个 build 声明了什么 —— 19 个因子的 handle、各自读哪些列、六个判决各是什么意思
uv run openalpha factor list

# 2. 一条声明的全文，含它自己承认不度量什么（每条注解 705～4830 字符，一字不删）
uv run openalpha factor describe --factor return_vol_60/v1

# 3. 计算并写入因子档（raw / processed / neutralized 三档，--tier 指到哪档就写到哪档）
uv run openalpha factor build --factor reversal_1d/v1 --tier processed \
  --transform cross_section_standard/v1 \
  --as-of 2026-01-08T09:00:00+00:00 --as-of 2026-01-09T09:00:00+00:00 \
  --year 2026 --max-staleness-days 30

# 4. 三档实验：IC、分位组合、周转与容量、冗余，六格归因，密封成内容寻址的产物
uv run openalpha factor run --factor reversal_1d/v1 \
  --start 2026-01-08 --end 2026-01-09 \
  --transform cross_section_standard/v1 --neutralization industry_and_size/v1 \
  --horizon 1d --ic-method spearman --min-securities 4 --min-as-ofs 2 \
  --group-count 2 --min-securities-per-group 2 --position-capital 100000 \
  --min-periods 2 --participation-cap 0.01 --min-rebalances 1 \
  --redundancy-threshold 0.8 --retention-floor 0.4
```

三个面等价：`openalpha factor *` ／ `GET /api/v1/factors` + `POST /api/v1/factors/run` ／
`OpenAlphaSDK.factor_catalog()` + `.run_factor_experiment()`。`factor build` 只有命令行与
SDK 两个面，与 `panel build` 一致——它写面板分区，而服务本身不带鉴权。

### 怎么读那张六格网格

`factor run` 打三行档位和六格归因。**六格不是平等的**：

| 步骤 | 它回答什么 | 补救 |
|---|---|---|
| `raw->processed` | 缩尾、标准化与缺失值策略拿走了多少 | 换一个 `FactorTransformSpec` |
| `processed->neutralized` | **回归掉行业与市值之后还剩多少 —— 验收判据读的就是这一行** | 没有变换设置能救回来 |
| `raw->neutralized` | 端到端。单独carry是因为一环 `not_measured` 时两比之积不等于合成之比 | — |

六个判决的含义（`openalpha factor list` 会打全，`GET /api/v1/factors` 的 `verdicts` 里有同一份）：

| 判决 | 含义 |
|---|---|
| `survives` | 留存率在声明的 `--retention-floor` 与 1 之间：这一步保住了统计量 |
| `removed` | 留存率低于声明下限。在 `processed->neutralized` 上就是验收判据触发：因子挣的是行业与规模暴露 |
| `reversed` | 后一档统计量为负：这一步把方向掉了个个儿，与"缩水"是两种发现 |
| `amplified` | 留存率大于 1：这一步把统计量放大了，说明暴露原本在拖后腿 |
| `no_baseline` | 两档都度量了，但**前一档**统计量小于等于零：本来就没有东西可留 |
| `not_measured` | 两档中有一档根本没有统计量。**这不是通过** |

**`exit 0` 同时包含「六格全是 `removed`」和「六格全是 `not_measured`」，后者更危险。**
前者是报告干成了它的活；后者是**什么发现都没有** —— 三档里可能有两档一个数都没算过，
而 grep `removed` 没命中就收工的人（或 CI）会读成「这个因子扛过了中性化」。所以
`factor run` 在这种情形下往 **stderr** 打一行具名 `WARNING`（`--json` 模式也打，stdout
仍然只有密封信封），并且 `document.artifact.tiers[].ic.coverage` 是每一档的真相：
只有 `measured` 才代表有数。它**不是**第四个退出码——这样的实验确实组装成功了，
产物值得留存，每档自己的四个覆盖码已经说明了原因，再加一个更粗的信号就是第五套
「数据不够」的词汇表。

### 有名字的边界（不是遗漏）

- **中性化档在覆盖年内建不出来**（`V2-P4-026` 要修的）。残差只能在「读到的每一年最后一个
  已存会话」当天或之后的预测时刻上算出来，而分区是整块替换的——所以一个 store 无法同时
  持有年中的 raw 观测和年末时刻的残差。`factor build --tier neutralized` 在更早的时刻上
  **按名字拒绝**，并且什么都不写：写了两档、放弃第三档，正好留下让 `factor run` 在下一条
  命令里以另一个理由拒绝的那种 store 形态。
- **出厂变换与中性化的 `min_cross_section=100` 高于稀薄市场**：窄截面上两个派生档每个名字
  只有覆盖码没有值，六格全 `not_measured`。这是那份配置的诚实答案，不是面的缺陷。
- 完整清单在 `openalpha factor list --json` 的 `run_limitations`，或
  `openalpha_cn/factor_view.py#KNOWN_FACTOR_RUN_LIMITATIONS`。

### `factor build` 的几点语义

- **`--as-of` 是预测时刻而不是日期，可重复。** 一条存储观测的四个面板时钟都打这个时刻，
  `factor run` 也按它给样本分组；日期会把时分秒留给某个面去发明。`factor run --start/--end`
  再按这些时刻的 Asia/Shanghai **日期**去选。
- **一个分区年的全部时刻必须在一次调用里给全。** 分区是整体替换，所以同年的第二次构建会被
  「不允许丢弃已存构建」的守卫拒绝，除非用 `--supersedes-raw` / `--supersedes-processed` /
  `--supersedes-neutralized` 点名它要替换掉哪些 `manifest_id`。三个选项而不是一个，因为三档
  是三份不同的 manifest 分区，每个 writer 都会拒绝一个「它触碰的分区都不持有」的名字。
- **`--year` 是分区年，不是 `factor run` 的 `--start/--end`（那是预测日）。** 一年一年数：
  125 个会话回看的因子在年初需要上一年；财报分区按**公告年**归档，五个报告期通常要两个公告年。
- **登记簿是唯一一个 `--year` 不必数全的数据集，因为它数不全。** `trade_cal` 与 `daily` 按
  数据**所属**的年份分区，`stock_basic` 按一只证券**生命周期变动**的年份分区 —— 2026 分区是
  「2026 年上市或退市的证券」，不是「2026 年的市场」。所以 `load_stock_universe` 会把请求区间
  **下方**、store 里已有的每个生命周期年一并读进来，`--year 2026` 拿到的是整个市场而不是当年
  的新股。这不是便利，是必需：一只 1996 年上市、2026 年退市的证券，上市行在 1996 分区、退市行
  在 2026 分区，只读 2026 就是一次**残缺读取**，而不是一只「一出生就已退市」的证券。
  `V2-P4-059`／`V2-P4-060` 之前这两件事分别表现为：5,545 只的 store 上只筛了 11 只并 `exit 0`，
  以及一次普通的年中退市让 `factor build` 抛出未捕获异常。补救方式**不是**把早年份也写进
  `--year`：一个 `--year` 同时约束三个数据集，`--year 2026 --year 2010` 会去要 2010 年的
  交易日历分区和行情分区，而把它们建出来是 `panel build --help` 标价「以天计而不是以小时计」的
  约 282,000 次请求 —— 只为算一个**一日**反转。
- **`--max-staleness-days N` 与 `--waive-max-staleness` 二选一，没有默认值。**
  `panel_ingest` 的每个 requirement builder 都拒绝替调用者选这个界，理由写在各自的 docstring
  里：一个最新会话是一个月前的行情面板，已经错过了一个月的市场。
- 不给 `--subject` 时，主体是登记簿知道的**全部**代码（含已退市的），universe 是当天的上市
  横截面 —— 于是退市名会被评估并落成 `not_in_universe`，而不是从普查里静默消失。

## 从整个市场到一张可发布的候选榜（P4）

因子平面之上是**候选榜平面**。PRD §3.2 把两条路画成两条：整个市场（约 5,000 只）在面板平面
上打分、过滤，**不进 `run_cycle`**；只有切出来的 N 只才值得花一次证据运行。

```bash
# 从已建好的因子档切一张榜，并对它开闸
uv run openalpha shortlist run --component reversal_1d/v1=1.0 --tier raw \
  --shortlist-size 50 --position-capital 100000 --year 2026 --horizon 5d \
  --min-tradable-ratio 0.30 --min-researched-ratio 0.50 --max-ranking-age-days 1 \
  --as-of 2026-01-16T09:00:00+00:00
```

因子档必须先存在：`openalpha factor build` 是放它进去的那条命令。三个面等价：
`openalpha shortlist run` ／ `POST /api/v1/shortlists/run` ／ `OpenAlphaSDK.run_shortlist()`。

**「等价」是逐字面量成立的，不只是「大体一样」。** 同一个字面输入在三个面上必须得到同一个
判决：`--code-commit ""` 是显式声明了一个空值，三个面都拒；把这个旗标**整个省掉**才是「由
进程自己解析」，只有命令行和 HTTP 有这条回退，而它和「显式给空」是两件事。这条以前不成立
——命令行把 `""` 当成「没给」，于是发出去的榜盖着一个调用者从没声明过的 commit。

**读的是哪个横截面，什么时候读的。** 因子档按你给的 `--as-of` 读，`read_visible_at` 会把
`available_time` 晚于它的行滤掉；而**打分之后用来定价的一切**——日历、登记簿、K 线、涨跌停
带、停牌、名称历史——按解析出来的那个横截面**自己的时刻**读。所以一个两周前建的横截面，是拿
它自己那个交易日的市场去撮合的，绝不会被丢到一个它的因子值从没见过的更晚的会话上。
`cross_section.as_of` 与 `cross_section.pricing_session` 出现在每一个答案里。

**“被拒绝的榜”和“本来就空的榜”是两个答案。** 这是这条命令存在的理由：

| 情况 | 退出码 / HTTP | `is_blocked` | `admitted` |
|---|---|---|---|
| 开闸放行 | `0` / `200` | `false` | 一个数组，可以是 `[]` |
| 开闸**拒绝** | `1` / `409` | `true` | `null` |
| 声明的成分在 `--as-of` 之前没有任何已存横截面，或几个成分的最新时刻不一致 | `1` / `409` | — | — |
| 某个声明的成分横截面里没有任何一行带着这一档认的值（比如 processed 档整片写着 `insufficient_cross_section`） | `1` / `409` | — | — |
| 需要的分区缺失、损坏、过期，或持有在 `--as-of` 时还不可知的行 | `1` / `409` | — | — |
| 这个问题根本提不出来（未声明的因子、非正权重、processed 档没给 `--transform`、非 neutralized 档却给了 `--neutralization`、`--position-capital` 到了 `10**26`、无时区 `--as-of`） | `3` / `422` | — | — |
| 命令行本身就写错了：漏了 `--component`、拼错了旗标 —— Click 自己的用法错误，不归这张表管 | `2` / — | — | — |
| 命令自己崩了，什么都没判 | `5` / `500` | — | — |

`admitted: []` 是「一张榜上每个名字都还没被研究过，而调用者声明的
`--min-researched-ratio` 是 0，所以它通过了」；`admitted: null` 配 `409` 才是被拒绝，
`blocks[]` 里每条都带 `code`、`detail`、实测值 `measured` 与声明的门槛 `required`。
`measurement`（universe / scored / tradeable / shortlist / candidate 五个计数、两个比率、
榜的天数）**两种判决上都有**，所以「险险过线」和「远远过线」是能分开的。

不给 `--evidence` 就是「还没有任何名字被研究过」，这是最常见的第一个答案：榜说的是哪些名字
值得花一次证据运行，闸口在那些运行发生之前拒绝把它们当结论发布。`--evidence <file.json>` 按
`{subject: {"signal": <SignalFrame>, "run_manifest_id": "..."}}` 提供证据平面的答案；没进榜的
那些不会被静默丢掉，而是回在 `evidence_not_shortlisted` 里。

## 核心独特优势

OpenAlpha CN 整合 TradingAgents 和 AI Hedge Fund 的优势，接入 A 股数据源，更适合 A 股涨停量化分析。OpenAlpha CN 的竞争重点不是复制更多“投资大师人格”，而是：

1. A 股原生事件语义和交易约束；
2. 证据首次可知时间与防未来函数；
3. 每个结论、决策、回放和归因都能追溯；
4. 无 LLM 也能确定性运行，接入 LLM 时强制结构化输出和有界重试；
5. 同一核心路径贯通 API、SDK、CLI、Web 与回放；
6. 功能状态必须有源码和测试证据，愿景、Stub、按钮不算完成。

源码审计基线、差异化结论和后续边界见[竞争优势说明](docs/why-openalpha-cn.zh-CN.md)。

## 🧭 五张 API 数据与功能关系图

OpenAlpha CN 的公开 API 不是一组彼此孤立的地址，而是围绕同一份可验证数据逐层展开：
调用方先把合法来源的数据整理为统一 Provider 合同，系统再生成时间点证据；单次研究和批量
研究复用同一 `ResearchEngine.run_cycle`；研究结果由调用方显式送入委员会、筛选、报告、
观察池或组合核算；最后由回放、事件统计、组合报告和结果归因完成验证闭环。

图中**实线表示服务端自动调用或持久化**，**虚线表示调用方显式组合或人工反馈**。这个区别
非常重要：研究结果不会自动变成组合订单，验证结果也不会自动训练模型。

### API 关系图 01｜四类入口共享五条功能链

REST、Python SDK、Typer CLI 和 React 工作台最终进入同一 FastAPI 公共边界。请求经过
Pydantic Schema、请求大小限制与安全响应头后，分别流向证据、研究、研究产品、组合和验证
五条功能链；运行数据统一沉淀到 Parquet 与 SQLite WAL，而不是由各入口维护不同状态。

<p align="center">
  <img
    src="./assets/diagrams/openalpha-api-01-landscape.svg"
    alt="OpenAlpha CN 四类调用入口与五条公开 API 功能链全景图"
    width="1200"
  />
</p>

### API 关系图 02｜Provider 数据如何变成可研究证据

链邻 API、用户文件、Tushare 和可选 AKShare Adapter 位于调用方或 Provider 侧。
`POST /api/v1/evidence/build` 只接收结构化 `ProviderMetadata + ProviderBatch`，不会在
服务端自动抓取数据。记录通过 Schema、四时钟 PIT 和 A 股事件规范化后生成内容寻址的
`EvidenceSnapshot`，写入 Parquet，再由证据、市场事件和题材查询接口按 `as_of` 提供给研究。

<p align="center">
  <img
    src="./assets/diagrams/openalpha-api-02-evidence-dataflow.svg"
    alt="OpenAlpha CN ProviderBatch、四时钟、EvidenceSnapshot 与证据查询 API 数据链"
    width="1200"
  />
</p>

### API 关系图 03｜单次与批量研究如何汇入同一内核

`POST /api/v1/research/run` 直接运行一次研究；批量 API 则把最多 1000 个不可变请求放入
持久队列，以 1–32 的受控并发逐项调用同一 `run_cycle`。证据感知路由选择市场、题材和
资金 Agent，节点级 Checkpoint 支持恢复，聚合后的 `SignalFrame` 经过风险门生成
`ResearchRunResult`，同时持久化运行清单、决策账本、研究记忆和恢复状态。

<p align="center">
  <img
    src="./assets/diagrams/openalpha-api-03-research-orchestration.svg"
    alt="OpenAlpha CN 单次研究、批量任务、run_cycle、Agent 路由和持久恢复关系图"
    width="1200"
  />
</p>

### API 关系图 04｜研究结论如何转化为产品与组合资产

`ResearchRunResult` 是下游功能的可信交汇点。调用方可把 `signal + agent_results` 送入
Bull/Bear 与三视角风险委员会，把完整研究结果送入筛选或不可变报告，也可以明确选择标的
加入观察池。组合 API 还要求调用方单独提交 `PortfolioState`、`PortfolioOrder`、
`MarketBar` 和限制条件，通过 T+1、整手、停牌、涨跌停、费用与敞口检查后，才产生
不可变 `PortfolioTransition`；它不会根据研究结论自动下单，也不连接实盘券商。

<p align="center">
  <img
    src="./assets/diagrams/openalpha-api-04-decision-products.svg"
    alt="OpenAlpha CN 双委员会、筛选、报告、观察池与 A 股组合转移 API 关系图"
    width="1200"
  />
</p>

### API 关系图 05｜回放、统计和归因如何形成验证闭环

冻结语料回放再次调用同一 `run_cycle`，用于检查确定性和前视问题；多日组合 API 计算
收益、基准、换手、容量和暴露；事件研究 API 给出 CAR、t 统计量和确定性 Bootstrap
置信区间；结果验证 API 则先复核内容派生 ID，再把未来观察归因到规则、因子和 Agent。
这些结果由研究者审阅后用于调整数据质量、路由、风险阈值和筛选条件，不会自动修改模型。

<p align="center">
  <img
    src="./assets/diagrams/openalpha-api-05-validation-loop.svg"
    alt="OpenAlpha CN 回放、多日组合、事件统计、结果归因与下一轮研究反馈闭环图"
    width="1200"
  />
</p>

## 公开 API

| 能力 | 入口 |
|---|---|
| 健康检查 | `GET /health` |
| 构建/查询证据 | `POST /api/v1/evidence/build` / `GET /api/v1/evidence` |
| 市场事件/题材 | `GET /api/v1/market/events` / `GET /api/v1/themes` |
| 多智能体研究 | `POST /api/v1/research/run` |
| 批量研究与进度 | `POST /api/v1/research/batches` / `GET /api/v1/research/batches/{batch_id}/events` |
| 多空辩论与风险委员会 | `POST /api/v1/research/deliberate` |
| 筛选、观察池、报告 | `POST /api/v1/screen` / `GET /api/v1/watchlist` / `GET /api/v1/reports` |
| 持久研究记忆 / 运行恢复 | `GET /api/v1/memory/{subject}` / `GET /api/v1/runs/{run_id}/recovery` |
| 冻结语料回放 | `POST /api/v1/backtests/replay` |
| A 股组合账本 | `POST /api/v1/portfolio/execute` |
| 多日组合 / 事件研究 | `POST /api/v1/backtests/portfolio` / `POST /api/v1/backtests/event-study` |
| 结果与归因 | `POST /api/v1/backtests/validate` |
| 已落库的验证结果 | `GET /api/v1/backtests/validations/by-decision/{decision_id}` / `by-signal/{signal_id}` |
| 面板就绪 / 体检 / 依赖门 | `GET /api/v1/panel/readiness` / `GET /api/v1/panel/health` / `GET /api/v1/panel/gate` |
| OpenAPI | `GET /docs` / `GET /openapi.json` |

面板三个端点与 `OpenAlphaSDK.panel_readiness` / `panel_health` / `panel_clearance`
一一对应且被断言等价。只有 `/panel/gate` 的 `200` 是一种许可，被门拒绝时返回 `409`；
`/panel/readiness` 与 `/panel/health` 是报告，面板有病也返回 `200`，结论在响应体的
`all_ready` / `is_clean` / `counts_by_severity` 里——**`panel doctor` 的退出码 1 在
HTTP 上没有对应的状态码**。`409` 有两种互不兼容的响应体，用 `detail.reason` 区分。
细节见 [`docs/api/http.md`](docs/api/http.md)。

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

## 开发方向

活跃开发方向是 v2：把 OpenAlpha CN 从可验证的研究契约底座，升级为个人可日常使用的 A 股研究与选股环境 —— 面板数据平面、PIT 因子库、Walk-forward 预测模型、候选排序、组合与验证，以及四页研究工作台。

工作区在 [docs/specs/v2/](docs/specs/v2/)：

| 文档 | 内容 |
|---|---|
| [PRD](docs/specs/v2/openalpha-cn-v2-prd.md) | 范围、实测基线与实现决策 |
| [开发路线图](docs/specs/v2/openalpha-cn-v2-roadmap.md) | 七个阶段 110 个 issue，含依赖、闸门与需求映射 |
| [四缝审计](docs/specs/v2/openalpha-cn-v2-seam-audit.md) | 103 条带 `file:line` 证据的 finding，逐条对应关闭 issue |

参与开发前先读 [AGENTS.md](AGENTS.md) 的 v2 硬性规则与 [CONTRIBUTING.md](CONTRIBUTING.md)。

v2 的输出定位为研究与决策支持：不连接实盘券商、不代替使用者判断、不承诺收益。工程完成度不等于研究有效性 —— 只有预先登记的样本外指标、扣成本增量价值、多重检验控制，以及在结果已知之前落库的预测，才构成信号真实性的证据。

## 许可证

OpenAlpha CN 源码采用 [MIT License](LICENSE)。第三方数据、模型、服务、品牌素材与链邻桌面安装程序保留各自授权边界，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
