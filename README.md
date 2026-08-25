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

**给一只证券打标签时，语料本身可能答不上来，而这有三种形态（`V2-P4-084`）。** 一个
label window 要问登记簿「这几天它上市了吗」、问 `adj_factor`「这两天的复权因子是多少」、
再让 `daily` 与 `adj_factor` 互相印证同一个会话。三个问题各自会被拒绝，而三种拒绝都
**不是** `LabelError`——它们是四个互不相干的 `ValueError` 子类，所以以前
`except LabelError` 只接住了一个，另外三个一路冲到 `exit 5` 和裸 `500`：

| 遇到什么 | 抛出什么 | 以前的结果 |
|---|---|---|
| 有行情但登记簿里没有任何生命周期行的证券 | `StockUniverseError` | `exit 5` ／ `500` |
| 因子序列在 label window 之前就断了 | `AdjustmentHorizonError` | `exit 5` ／ `500` |
| `daily` 与 `adj_factor` 对同一个会话说法不一 | `PriceDataError` | `exit 5` ／ `500` |

三种现在都是面板判定（`exit 1` ／ `409 panel_unreadable`），并点名证券、窗口、是哪个分区
的问题，以及补法：

```
688981.SH could not be labelled over 2026-01-13..2026-01-14 out of this service's panel store,
and the reason is the stored adj_factor rather than the window: 2026-01-14 is after 688981.SH's
last adjustment factor, observed 2026-01-12; carrying the last factor forward would assert that
no corporate action happened in a window this read never covered. That is a verdict about the
panel rather than a range to edit -- a series that stops short is not a security with no series
at all (that one is already left out of the label map and counted), and carrying the nearest
factor across the gap returns the unadjusted number wearing an adjusted one's name. To repair
it, extend the factor series over the window -- `openalpha panel build --dataset adj_factor
--year <year>` -- and ask this run for that year too.
```

**第三种的补法不是「重建 daily」。** 两个数据集互相矛盾，而哪一个错**恰恰是这次矛盾没说的
事**，所以拒绝里写的是「把那个会话重新取一次」，并指向
`openalpha panel doctor --session <那个会话>` —— 它在那边报 `return_path_disagreement`
（会话级的交叉检查只跑请求点名的那些会话，所以得把它点出来）。

**「没有」和「读不出来」是两回事，三处各画在不同的地方。** 一只**根本没有**复权历史的证券
仍然照旧被留在 label map 之外、计进 `ICCensus.unmatched_count`（这是 unmatched，不是拒绝）；
登记簿能定位、只是回答「那天还没上市／已退市」的代码，仍然是 `LabelRefusal` 的一个码；
某个会话**缺** K 线仍然是 `REFUSAL_MISSING_BAR`。被拒绝的只有「语料够不着」和「语料自相
矛盾」这两类。

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

**除了因子档，这条命令还硬性需要五个面板目标，而以前没有任何地方写着这件事（`V2-P4-078`）。**
它在解析出来的横截面自己的时刻上读六个面板数据集，缺任何一个都是拒绝而不是「筛出来的名字少
一点」：

```bash
uv run openalpha panel build --dataset trade_cal    --year 2026   # 交易日历
uv run openalpha panel build --dataset stock_basic  --year 2026   # 证券登记簿
uv run openalpha panel build --dataset price        --year 2026   # K 线、估值、停牌（一个目标三个数据集）
uv run openalpha panel build --dataset stk_limit    --year 2026   # 已发布的涨跌停带
uv run openalpha panel build --dataset namechange   --year 2026   # 名称历史
```

**最容易漏的是 `namechange`，而它把本仓库自己的端到端套件绊了一跤。** `factor build --tier raw`
不需要它，所以一个没有它的面板上，因子构建是绿的、榜是红的。它是 `is_st` 的唯一来源——每根
`MarketBar` 带一个风险警示位，取自定价会话当天生效的那个名字——所以没有它就等于把每一只 ST
名字按普通涨跌幅定价。`adj_factor` **不在**这张表里：`factor build` 可能要它，这条命令一次都
不打开它，把它写进来等于让人白跑一次以小时计的构建。

现在缺分区的拒绝会把命令一起报出来，标准就是面板闸口那条（`panel_view.NO_CALENDAR_REMEDY`）：

```
the name histories could not be read out of this service's panel store: the rename corpus
cannot be read at ...: ['partition_missing', 'field_missing']; ... . No namechange partition is
registered in this panel at all, and this command reads it. Build it first: `openalpha panel
build --dataset namechange --year <year>`
```

**分区建好了，也仍然可能有某个证券在某个会话上「没有名字」（`V2-P4-080`）。** `namechange` 是
按**公告年份**切片读的：一只证券在所请求年份里唯一的一次更名，如果公告在定价会话之前、生效在
它之后（本仓库专门建模的那种「两个时钟」更名），那么在那个会话上语料里就没有任何一条记录生
效。`NameHistory.record_on` 对这一天的回答是拒绝而不是回退到最早的那
个名字——「一个没有记录的名字是未知的，而不是等于在册最早的那一个」。`shortlist run`、`factor
run` 和 `POST /api/v1/shortlists/run` 现在都把它当作面板判定报出来（`exit 1` / `409
panel_unreadable`），并点名是哪只证券、哪个会话：

```
the risk-warning state of 000002.SZ on 2026-01-16 could not be read out of this service's panel
store: 2026-01-16 is before 000002.SZ's first known name, which takes effect 2026-01-20; an
unrecorded name is unknown rather than equal to the earliest one on file. `MarketBar.is_st` is
that state, so screening 000002.SZ would file a risk warning nobody knows as a known-clean one.
The rename corpus is read one announcement year at a time and this run read 2026, ... Extend the
corpus back to an announcement year that covers 2026-01-16 -- `openalpha panel build --dataset
namechange --year <year>` -- and ask this run for that year too.
```

补法就是把公告年份往前扩：多建一年 `namechange`，并在这次运行里一并 `--year` 上它。**默认成
`is_st=False` 不是补法**——那等于在一个「取自当日生效名称的风险警示状态」的字段上，写下语料并
不支持的断言，而读到这根 bar 的人无法把它和一个真的测出来是普通名字的证券区分开。（实测：
`MarketBar.is_st` 全仓只有 `backtest/execution._price_band` 一处读它，且只在没有已发布涨跌停带
时才读；两个面都用 `published_limit_fields(limit)` 建 bar、没有带就根本不建，所以今天它不改变
任何裁决——是潜伏的错，不是无害的错。）反过来，一只在所请求年份里**一条记录都没有**的证券仍
然按 `is_st=False` 处理（多数证券在任一年份内都没有更名），这条残留写在
`KNOWN_SHORTLIST_VIEW_LIMITATIONS.a_name_never_announced_inside_the_requested_years_is_screened_as_ordinary`。

**决定按哪个会话定价的，是横截面被「建」在哪个 `--as-of` 上，而那不是它落在的那一天
（`V2-P4-077`）。** 一个会话的行情在 Asia/Shanghai 16:30 才可知，所以一个盖在当天零点到 16:30
之间的构建，是按**前一个**会话定价的——即它自己的因子值算出来时最新那个已经发布的会话。以前
这里取的是那个时刻自己的日历日：于是一个 19:01Z 起跑、在上海已经翻过零点的隔夜构建，会去问
一个还没发布的会话，被行情平面正确地拒掉——而因为时刻是**存在横截面上**的，这个拒绝是永久
的，之后无论在哪个 `as_of` 上再问都是同一个拒绝。实测扫过从构建之前到四天之后的每一个
`as_of`，全部 `exit 1`，两种拒绝之间没有任何缝隙。

**这也不只是「隔夜」，而这是这条最值得读两遍的地方。** 那个窗口从零点一直到 16:30，所以一个
上海时间上午九点、开盘前建出来的横截面，同样一次都筛不出来。按半小时步长扫过 2026 全年
16,735 个时刻：新规则**没有一次**晚于旧规则，8,518 个时刻两者相同，8,217 个不同——而这 8,217
个里**没有一个**是旧规则本来就能作答的会话。也就是说，全年将近一半的时刻，建出来的横截面是
永久不可筛的。

**前视那道闸一步都没松。** `_read_visible_price_session` 依然拒绝任何越过
`_sessions_published_through` 的会话，而定价会话现在问的就是同一个函数，两者不可能对不上；那条
拒绝仍然可以从 `openalpha panel doctor --session` 直接问到。变的是这个面不再去问一个只有「不
行」这一个诚实答案的问题。每个答案里的 `cross_section.pricing_session` 说的就是用了哪个会话。

**「等价」是逐字面量成立的，不只是「大体一样」。** 同一个字面输入在三个面上必须得到同一个
判决：`--code-commit ""` 是显式声明了一个空值，三个面都拒；把这个旗标**整个省掉**才是「由
进程自己解析」，只有命令行和 HTTP 有这条回退，而它和「显式给空」是两件事。这条以前不成立
——命令行把 `""` 当成「没给」，于是发出去的榜盖着一个调用者从没声明过的 commit。

**读的是哪个横截面，什么时候读的。** 因子档按你给的 `--as-of` 读，`read_visible_at` 会把
`available_time` 晚于它的行滤掉；而**打分之后用来定价的一切**——日历、登记簿、K 线、涨跌停
带、停牌、名称历史——按解析出来的那个横截面**自己的时刻**读。所以一个两周前建的横截面，是拿
它自己那个交易日的市场去撮合的，绝不会被丢到一个它的因子值从没见过的更晚的会话上。
`cross_section.as_of` 与 `cross_section.pricing_session` 出现在每一个答案里。

**上面这句话在 `V2-P4-076` 之前并不成立。** 横截面自己时刻上要读六个数据集，其中五个走的
是「整分区」那道闸：它拿分区里**最新的一行**的可知时刻去判 `not_yet_knowable`，而一个分区
就是一整年。于是面板只要往前多走一个交易日，同一年里**更早**的每一个横截面就都读不出来了
——`daily cannot be read at …: ['not_yet_knowable']`——能撮合的只剩最新那一个。两天的榜没法
对比，昨天的榜没法重跑，已经发出去的榜事后没法复核。

`V2-P4-061` 把 K 线与涨跌停带换成了估值面板早就在走的那条单会话读。**在真实面板上，那三句话
在它之后依然成立**：登记簿、停牌与名称历史一个都没动，而它们各自都足以单独挡住一个横截面
——实测 2026-08-19，登记簿在当天零点变得可知、停牌语料在当天 16:30，于是一个关于前一个交易日
的横截面，在读到任何一根 K 线之前就被拒了。这三个现在走的是**按事件日**的读：能看见的行，
逐日与分区自己的行普查对账。留在整分区那道门上的只有交易日历，而这是量出来的、不是漏掉的
——一整年的日历行都被标成「该年 1 月 1 日可知」，那道闸在年内根本没有触发的余地。

**前视那道闸一步都没松，而且每一种拒绝各有各的名字。** 一个会话自己的 16:30（Asia/Shanghai）
还没到，就在碰分区之前先拒——拿一个空横截面回答它，等于把前视包装成「这天数据薄」；库里有
行、但这个时刻看不见，也是拒，不是回一个空的；库里压根没有这个会话，则是对着交易日历自己的
普查报 `date_gap`；而一个会话里的行如果**不共享同一个可知时刻**（单会话读之所以成立的那条
性质），直接整个拒掉，绝不返回一个短的。

那三个整年读也各有自己的两种拒绝，理由是同一个。某个事件日上，分区自己的普查数到的行比这次
读能看见的多，直接具名拒绝——一份被扣住了一行的语料，和一份那行本来就不存在的语料，长得一模
一样；在停牌语料上这两者甚至是同一个答案，「没停牌」。而如果一行在它自己的事件日**之前**就
可见，那是另一种拒绝，并且**先报**：那是前视而不是短缺，把两者压成一个总数的比较，正是从前
让一对方向相反的错误互相抵消掉的原因。

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

**证据里的 `run_manifest_id` 会被对到本地已存的运行上（`V2-P4-049`）。** 以前它只做格式校验，
`SignalFrame` 也只需要哈希到它自己的地址——于是一个杜撰的结论配上字面量
`run_000000000000000000000000` 就能清掉 `--min-researched-ratio 1.0` 的门槛，并且带着一个解析
不到任何东西的出处记录被发布出去。现在，`run_manifest_id` 在这个 runtime 目录里找不到对应运行
的那条证据，会在建榜单之前就被丢掉：它的名字算作 `unresearched`，对 `researched_ratio` 的贡献
和「压根没给证据」完全一样，并且会具名回在 `evidence_without_a_stored_run` 里。是丢掉而不是报错，
因为一个在一整年 `as_of` 上循环的调用者必须能越过它继续跑。

**这条性质证明了什么、没证明什么：被解析的是那次运行，不是它旁边的信号。** 本仓库不存
`SignalFrame`，没有东西可以拿来对信号；一个手里有真实 `run_manifest_id` 的调用者，仍然可以在它
名下填一个杜撰的结论。交付的性质是「发出去的 `run_manifest_id` 能解析到本部署持有的一次运行」，
不是「它旁边的结论是那次运行跑出来的」。

### 同一年里再建一个时刻，以及把跑出来的榜留下来

```bash
# 昨天：建一个横截面，跑一张榜，记下它的 shortlist_id
uv run openalpha factor build --factor reversal_1d/v1 --tier raw \
  --as-of 2026-01-15T09:00:00+00:00 --year 2026 --max-staleness-days 30

# 今天：同一年里追加第二个时刻。既不用重算昨天，也不用抹掉它
uv run openalpha factor build --factor reversal_1d/v1 --tier raw \
  --as-of 2026-01-16T09:00:00+00:00 --year 2026 --max-staleness-days 30

# 把昨天那张榜按它自己的内容地址取回来，和今天的对比
uv run openalpha shortlist get sla_0123456789abcdef01234567
uv run openalpha shortlist list
```

**第二条命令在 `V2-P4-071` 之前是逐字被拒的**：`factor_manifest_reversal_1d_v1 year=2026
already holds 1 subject(s) and this write carries 1; it would drop ['fmn_…']`。一个分区是整
个替换的、没有追加，所以当时只有两个选择——把这一年建过的所有时刻在一次调用里重算，或者用
`--supersedes-raw` 抹掉昨天那次。分区的粒度没有变（一个 `(dataset, year)` 分区本来就能装任意多
个时刻），变的是写路径：写之前先把这一年已存的、本次既不作答也不声明取代的行读回来放在前面，于
是「整分区替换」就是一次追加。**掉档闸没有被放松**：它照旧在合并后的批次上跑，一次漏掉了某个
build 的合并会被它逐字拒掉——它从「让调用者去重算」变成了「审计这次合并」。在一个已存 `as_of`
上换个 `--code-commit` 重建，仍然是被拒的，仍然指向 `--supersedes-raw`：一年里对同一个横截面问题
存两个答案，是这条闸一开始就在挡的东西。

**每个答案都带 `shortlist_id`，那就是它被存起来的地址（`V2-P4-062`）。** 以前答案上有三个内容
地址（`gate_manifest_id`／`ranking_manifest_id`／`ranking_content_digest`），却没有任何东西可供
寻址：`runtime/` 下没有榜单产物，也没有 GET 路由。三个都不能当键——`ranking_manifest_id` 只寻址
「问题」，两套闸门门槛共享它；`gate_manifest_id` 寻址问题加门槛，但**证据**不在其中；
`ranking_content_digest` 只寻址已研究的候选，一次没有证据的运行候选为零，两张完全不同的榜共享
一个摘要。`shortlist_id` 是整个答案的摘要（只排除 `measurement.ranking_age_days`，它是
`built_at - as_of`，本质是一个墙钟）。所以这是一个纯内容寻址的存储：同一个答案跑两次只有一份文档，
第二次写是空操作；两个不同的答案有两个地址。它因此**说不出**一个答案被跑过几次、什么时候跑的——
那是 `RunManifest` 平面的问题。三个面等价：`openalpha shortlist get|list`、
`GET /api/v1/shortlists[/{shortlist_id}]`、`OpenAlphaSDK.held_shortlist()/list_shortlists()`。

## 从一张面板到一条被登记的预测（P4，`V2-P4-021`）

前一节把整个市场收敛成一张候选榜。这一节是另一条链：**拿声明好的特征去拟合一个模型，量它在
样本外排得怎么样，然后把今天的预测在结果已知之前落库。**

```bash
# 1) 先把要读的档建出来（每个预测日一个时刻）
uv run openalpha factor build --factor reversal_1d/v1 --tier raw \
  --as-of 2026-01-06T09:00:00+00:00 --year 2026 --max-staleness-days 30
# …每天一次，一直建到 2026-01-16

# 2) 量一个声明：按 walk-forward 切，逐折拟合，逐折报数
uv run openalpha model evaluate --feature reversal_1d/v1@raw \
  --name reversal-rank --family cross_sectional_rank --horizon 5d --seed 7 \
  --start 2026-01-06 --end 2026-01-14 --year 2026 \
  --folds 2 --test-days-per-fold 2 --embargo-sessions 0 \
  --min-scored-ratio 0.5 --as-of 2026-01-20T04:00:00+00:00

# 3) 把今天的预测登记下来（结果还没发生）
uv run openalpha model daily-run --feature reversal_1d/v1@raw \
  --name reversal-rank --family cross_sectional_rank --horizon 5d --seed 7 \
  --start 2026-01-06 --end 2026-01-14 --year 2026 \
  --predict-at 2026-01-16T09:00:00+00:00 --min-scored-ratio 0.5

# 4) 取回来
uv run openalpha model predictions
uv run openalpha model prediction prd_0123456789abcdef01234567
```

三个面等价：`openalpha model evaluate|daily-run`、`POST /api/v1/models/{evaluate,daily-run}`、
`OpenAlphaSDK.evaluate_model()／.run_daily_model()`。三者都经同一个
`model_view.model_evaluation_request`／`daily_request` 解析、同一个 `evaluate_model`／`run_daily`
执行，所以不可能对同一份声明拟合出三个模型。

### 面板要先有六个数据集，而且**不是**榜单那六个

`trade_cal`、`stock_basic`、`daily`、`suspend_d`、`stk_limit`、**`adj_factor`**，由五个
`panel build` 目标写入。`adj_factor` 是榜单不需要而这里必须有的那个：一个标签是**两个交易日之间
的收益**，`label_outcome` 要一份复权序列，`window_return` 会拒掉够不到窗口的序列。反过来
`namechange` 是榜单要而这里不要的——本面不构造任何 `MarketBar`，从不问 `is_st`。**两个方向都会
少**，所以两条命令的 `409` 都会把修复它的那条命令写在拒绝信息里（`V2-P4-078` 的规矩）。

### 两个时钟，以及请求只提供其中一个

- `--as-of` 是**读标签**的时刻，必须在 `--end` 当天或之后。一次运行里所有面板读取都在它上面发生。
  这不是把点时间保证放松了：每个横截面仍然是它**自己那个预测时刻**上可见的那个 build，晚于它的
  build 在下一层就被 `read_visible_at` 滤掉了。在这一个 `as_of` 上读到的是语料的**形状**——登记簿
  今天列了谁、日历今天有哪些交易日、复权序列今天覆盖到哪。结果按定义在它被预测的那一刻不可知，
  所以一次在每个预测时刻上读标签的运行会一个已闭合的窗口都找不到。残留写在
  `the_evaluation_reads_its_labels_at_one_as_of_and_that_is_not_a_point_in_time_fit`。
- `--predict-at`（只有 `daily-run` 有）是这条预测**关于**哪个时刻，必须严格晚于 `--end`。它**不是**
  批次被产出的时刻：那是本进程自己的时钟，任何请求字段都不带它——因为存储对同一个时钟的读数，正是
  `standing` 全部机制之所在。

### `--feature-version` 省略与显式是两件事

省略：由本次声明的列解析出来（`--code-commit` 的规矩，因为没人能手写一个 `feat_` 摘要）。
显式：由 `feature_matrix.require_declared_features` 校验，不一致就按名字拒（`422`／exit 3）。
**这是 `V2-P4-012` 造出那个函数之后第一个调用者**——`V2-P4-014` 曾被指定为第一个调用者而结构上
做不到（`backtest-no-numeric-stack-or-panel-plane` 禁止整个 `backtest` 包 import
`openalpha_cn.feature_matrix`）。答案里记着到底是哪一种（`declaration.feature_version_source`），
因为解析出来的那份只证明「制品记录了它拟合时用的配方」，**不证明有人打算用这个配方**：把一个
`--feature` 打错，得到的是另一个自洽的摘要，而不是一次拒绝。

### 被拒 ≠ 空

`--min-scored-ratio` 在两个面上都没有默认值。它是 `打分数 / 被问到数` 的下限，存在的理由就是
`FoldEvaluation.scored_ratio` 存在的理由：**弃权是免费的**，所以一个头条统计量只有并排放着它是在
多大比例的市场上取的才可比。

- 过线：`exit 0`／`200`，`is_blocked: false`，`admitted` 带着制品地址（evaluate）或被打分的证券
  （daily-run）。
- 不过线：`exit 1`／`409`，`is_blocked: true`，**`admitted: null`**，`blocks` 里带 `measured`、
  `required` 和写清两个计数的 `detail`。

### stale 模型显式弃权

`--shelf-life-days` 是「一次拟合在其训练截止之后还能被问多少天」。超过它，整个横截面上每只证券都
**带理由弃权**而不是被打分；答案里 `declaration.shelf_life_days` 记录声明了哪个跨度，没声明就是
`null` —— 与 `feature_version_source` 同一套安排：读者要能在答案上看见，而不是从命令行推断。

两点值得单说：

- **它是自然日，不是交易日。** 本仓的周期按**开市会话**计数，而 `domain/horizon.py` 明确拒绝把会话
  数换算成日历跨度，所以 `5` 就是五个自然日，想要五个会话的调用方自己加上周末与假期。
- **它自己拒绝不了任何东西。** 过期的运行报 `scored_ratio: 0.0`，把它变成 `exit 1`／`409` 的是
  `--min-scored-ratio`。声明了 `0.0` 下限的调用方，会把一个全弃权的模型读成一次干净的成功。两个开关
  是**一套机制**。

`null` 和一个列表是两个答案，而两次运行的 `measurement` 体**逐字相同**——只差一个开关。这是一条
**覆盖度**判决，永远不是质量判决。

**被拒的 `daily-run` 仍然把预测登记了**，`record_id` 就在那个 `409` 体上。Story S32 说的是预测要在
结果已知之前落库，这是无条件的；下限说的是这个答案能不能被拿去用，这是有条件的。

### `standing` 到底证明了什么

每一份被渲染的预测都带 `standing`、`standing_proves` 和 `standing_does_not_prove`，第二个不是装饰：

- **`forward`** —— 批次自称在结果可知之前产出，**并且**本存储在那之前就持有了这些字节。它**不证明**
  批次是在它自称的时刻产出的：`predicted_at` 是调用者传给 `predict` 的任意值，本仓没有任何东西能校验
  它；**也没有任何东西防得住拥有这块磁盘的人**。一个第三方能校验的声明需要一个别人控制的时间戳，本仓
  没有。
- **`unwitnessed`** —— 声称及时，收到得晚。可能是慢磁盘，也可能是被回填的 `predicted_at`，这条记录
  分不出是哪个。
- **`backfill`** —— 在结果可知的时刻或之后产出，如实登记为一次重算。回填不得替换原件。

### 它填上了三个 issue 留着的那个槽

`model daily-run` 会写一条 `mode=daily` 的 `RunManifest`，`alpha_model_versions` 里正是它消费的那一个
制品——`V2-P4-010` 声明了这个槽、`V2-P4-016` 实测自己填不了（`run_cycle` 那条路上没有任何
`AlphaModel`）、`V2-P4-017` 从存储侧得到同样结论。`run_id` 由预测自己的内容地址派生，所以同一天重跑
在**两个**存储上都报 `unchanged`，而不是其中一个报重复。

`model evaluate` **不写** manifest 也**不登记**任何预测：它每折拟合一个制品、一个都不据以决策，而且
它能登记的每一条记录都会是 `unwitnessed`——因为一次被模拟的预测的时刻就是它模拟的那个时刻，早已过去。
往 Story S32 的登记簿里灌回测，只会把它存在的理由（那些 `forward` 行）埋掉。

## 从一张被开闸的榜到一组目标权重（P5，`V2-P5-001`/`V2-P5-002`）

候选榜平面之上是**组合平面**。它接的是一张**已经被闸门放行**的榜，输出一组目标权重，并且在
答案上原样写着这组权重是什么：`heuristic, not optimized`。

```bash
# 把 shortlist run 打印出来的那个 sla_ 地址喂给它
uv run openalpha portfolio construct sla_0123456789abcdef01234567 \
  --tier-weight 0.5 --tier-weight 0.3 --tier-weight 0.2 \
  --max-position-weight 0.10 --turnover-budget 0.30 \
  --previous-weight 000001.SZ=0.05
```

三个面等价：`openalpha portfolio construct` ／ `OpenAlphaSDK.construct_portfolio()` ／ `POST /api/v1/portfolio/construct`（`V2-P5-013` 补上了第三个）。REST 请求体的 `policy` 字段就是 `PortfolioConstructionPolicy` 本身——SDK 收的那个模型、CLI 用参数拼出来的那个模型——所以三个面读的是同一份校验、同一句拒绝；`200` 正文与 `--json` 逐字节相等，两条拒绝与 CLI 打在 stderr 的那句逐字相等，由 `tests/integration/test_portfolio_construction_interfaces.py` 钉住。

### 三步都是算术，而且每一步都说得出自己没做什么

**分层排序**：按 rank 切成连续的块，每块拿走声明的那一份、块内**等权**。所以同一层里排第 1 和排
第 10 的名字权重相同，分数是给人看的、从不当量纲用——因为
`KNOWN_CROSS_SECTION_LIMITATIONS.the_shortlist_is_not_a_ranking_of_expected_return` 已经实测过
那些分数没有拟合任何东西，拿没拟合过的数去乘资金，就是让一个复合权重长得像一个预测。

**上限裁剪**：clamp 到单票上限 → 把腾出来的重量按 headroom 按比例回配 → 再 clamp。循环**有界**，
最后一步永远是 clamp，所以返回的权重无条件满足全部上限。**放不下的重量变成现金**并以
`unallocated_weight` 单独报出——绝不摊到最后一个名字上，那正是 `V2-P5-005` 要从
`backtest/validation.py` 里删掉的把戏。

**换手预算**：`turnover` 是证券权重变化的绝对值之和，**两边都算**（卖掉一个 5% 的名字、买进另一
个是 `0.10` 而不是 `0.05`）。超预算时整体按 `budget / turnover` 缩放，`turnover` 与
`turnover_before_budget` 并列上报。代价也写在答案上：缩放是从**你声明的那本账簿**出发的部分移动，
所以一本本来就超限的账簿，缩放之后可能仍然超限——这时 `caps_breached_after_turnover_damping`
会点名说出是哪一条超了，而**不会**再裁一次（再裁就花掉了刚刚被预算拒绝的换手）。

### 不引入求解器不是省略，是 ADR-0003 的结论

九个运行时依赖、不发任何数值栈。均值方差或风险平价需要一个协方差估计和一个求解器，那不是这个仓库
能发的东西。PRD 在另一个方向上做了同一个决定，并且只附了一个条件：**报告必须自称启发式**。所以
`PortfolioConstruction.method` 是一个 `Literal`，终端渲染和 `--json` 两面都印它，说不出这句话的构建
根本通不过校验。

### 被拒绝的榜没有权重

`admitted` 为 `null` 是「闸门拒了这张榜」，为 `[]` 是「闸门放行了、但一个名字都没有」——这是
`V2-P4-032` 特意分开的两个答案。对第一种构建组合，等于把一次拒绝洗成一组数字，所以两个面都具名
拒绝。

### 行业上限在这条路上会被拒绝，而这是量出来的

`shortlist_view` 用 `exposures=None` 建榜，存下来的答案里**没有任何名字带行业**。一条看不见行业的
上限，是每一本账簿都满足的上限——报告说它守住了，既为真又无用。所以声明了
`--max-industry-weight` 而候选没有 `industry_code` 时，这条命令**具名拒绝**。
`OpenAlphaSDK.construct_portfolio_from_ranking()` 是等 `V2-P5-015` 载入暴露截面那天它开始生效的那
条路，它的算术今天已经有单元测试。

### 现金下限不是第三条约束

长仓无杠杆下 `equity == cash + market_value`，于是 `cash / equity >= f` 和
`market_value / equity <= 1 - f` 是同一个不等式：30% 的现金下限和 70% 的敞口上限实测给出**逐字节
相同**的权重。`--min-cash-weight` 照样提供（行要求、且按下限声明意图更好读），取更紧的那个绑住，
拒绝理由会说是哪一个绑的——代码不假装两者可以叠加。

## 结果的统计口径：家族有多大，区间假设了什么（P5，`V2-P5-007`/`V2-P5-008`）

组合平面之上是**结果平面**。`openalpha validation statistics` 把已经落库的 `ValidationResult`
按 signal 聚成同期群，每个同期群就是一个假设。

```bash
# signal ID 来自 openalpha research run；--family-size 是「一共检验了几个」，不是 --signal 的个数
uv run openalpha validation statistics \
  --signal sig_0123456789abcdef01234567 --signal sig_89abcdef0123456701234567 \
  --family-size 40 --false-discovery-rate 0.10 \
  --dependence independent-or-positively-dependent
```

两个面等价：`openalpha validation statistics` ／ `OpenAlphaSDK.outcome_statistics()`。

### gross 与 net 并排，成本自成一列

`gross`（仓位相对基准赚到的）、`drag`（交易成本，负号自带）、`net`（真正留下的），三列各自独立求
均值而**不是**从另两列推出来——一个推导出来的列永远和它的父列一致，因此永远测不出任何东西，那正是
`V2-P5-005` 从归因里拿掉的那个自由变量。第四列是 `unexplained`：`V2-P5-006` 的残差聚合上来，而不
是在出口被悄悄丢掉（那正是 `V2-P5-006` 修的那个缺陷，只是高了一层）。

### 区间说得出它假设了什么，样本太小就直接不给

ADR-0003 不发数值栈，t 分布分位数无从取得。所以这里给的是**百分位 bootstrap**：`method`、
`confidence_level`、`bootstrap_samples`、`random_seed` 全部印在区间上，任何人都能复算。它做不到的
事也写在答案上——重采样均值是样本的凸组合，区间**永远出不了 `[min, max]`**；三个相同的收益率会得到
一个宽度为零的「95% 区间」，`distinct_bootstrap_means` 会老实报告这时候的分辨率是 `1`。

**少于两个观测就没有区间、也没有 p 值**：n = 1 时每一次重采样都是那个样本本身，任何置信水平下
`lower == upper`，那不是一个精确的估计而是一个缺席披着区间的外衣。这一行照样打印它的五列和样本
数，`absence_reason` 说明为什么没有推断，并且**不进入被检验的家族**——没被检验过的假设不是「没被
拒绝」的假设。

### `--family-size` 是这一行存在的理由

p 值来自**符号翻转随机化检验**（n ≤ 12 时穷举全部 `2**n` 种符号，故 p 值是分母为 `2**n` 的有理
数），零假设写在每一行上：净主动收益关于零对称。BH 拿这一族 p 值排序、比较、step up。

**同一批 p 值，在 `--family-size 2` 与 `--family-size 8` 下是两个答案**：前者拒绝一个假设，后者一个
都不拒绝，而没有任何一个被测量出来的数字改变过。所以家族大小是**存下来的字段**，不是读的时候数行
数数出来的——数行数在有一行被过滤掉的那天就是另一个数了。可检查的方向只有一个（声明的家族不得小于
报出来的行数，这一条在契约上拒绝），另一个方向不可检查，`--json` 里的
`the_family_size_is_declared_and_no_check_can_confirm_it` 直说了这件事。

`--dependence` 没有默认值：`independent-or-positively-dependent` 走 BH，`arbitrary` 加上
Benjamini–Yekutieli 的调和惩罚 `H_m`。它改的是算术而不是标签——同一族在 `H_2 = 1.5` 下临界值减半，
上面那个恰好压线的发现就消失了。宽松的读法不该同时是最省事的那个读法。

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
