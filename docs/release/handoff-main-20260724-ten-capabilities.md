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

## 公众介绍同步

- README 核心特性新增十大增强能力与五脑区的对应关系；
- 五张 SVG 脑图已按“证据感知 → 批量研判 → 双委员会与组合决策 →
  回放验证与研究产品”重新串联；
- 图中链邻数据接口由“规划目标”更正为已实现的合同型 Provider，并明确
  真实时效与精度取决于用户配置的服务、授权和上游数据；
- 五图已按 1440×900 实际渲染检查，未发现文字越界、卡片遮挡或断链；
- 仓库资产测试会阻止图稿退回过时的“规划目标”表述，并检查批量任务、
  双委员会、组合账本、事件统计和研究产品等关键能力。
- `docs/marketing/openalpha-cn-100-promotion-plans.zh-CN.md` 提供 100 条约
  350 字的中文推广方案，按十类传播角度编排；100 个开场钩子完全唯一，
  每条同时说明 TradingAgents、AI Hedge Fund 的借鉴点、A 股补强点与下载行动。
- 推广文案已做结构化校验：编号 1–100 连续，正文 313–408 字、平均
  354.3 字；不把链邻客户端 Provider 写成仓库自带商业数据，也不承诺收益或实盘能力。
- GitHub 在推广文案提交后新增 `brace-expansion` 高危 DoS 公告；Web 使用
  `pnpm-workspace.yaml` override 将两条 ESLint 间接依赖统一到已修复的
  `5.0.8`，冻结安装、audit、lint、Vitest、构建和 Playwright 均通过。

## API 关系脑图补充（2026-07-26）

- README 的“公开 API”表格上方新增五张 1440×900 SVG 关系图，依次解释：
  API 全景、Provider 到时间点证据、单次/批量研究编排、决策到研究产品、
  回放统计与归因闭环。
- 图中实线只表示 `create_app` 内部真实发生的调用或持久化；虚线表示调用方
  必须显式完成的组合，避免把委员会、报告、观察池或组合订单误画成
  `run_cycle` 的自动副作用。
- 数据图明确 `/api/v1/evidence/build` 接收 `ProviderMetadata + ProviderBatch`，
  服务端不自动抓取 Provider 数据；组合图明确研究结果不会自动下单，公开接口
  不连接实盘券商。
- `scripts/generate_api_relationship_diagrams.py` 可确定性重建五图；仓库资产测试
  检查顺序、README 位置、SVG 合法性、关键端点、源码合同名称和安全边界。
- 五图均已使用 Chromium 按 1440×900 实际渲染复核，未发现文字越界、卡片遮挡
  或错误的自动调用关系。

## “五图读懂 OpenAlpha CN”专业化重构（2026-07-27）

- README 原五张功能脑图已升级为同一套深色机构研究终端视觉系统，叙事顺序统一为：
  系统总览 → 证据平面 → 研究编排 → 决策约束 → 验证反馈。
- 五图不再只列功能名，而是以结构化交付物串联真实调用链：
  `EvidenceSnapshot → ResearchRunResult → DeliberationOutcome /
  PortfolioTransition → ValidationResult`。
- 图例统一约定：实线表示系统自动执行或持久化；虚线表示调用方显式组合或
  人工反馈。研究委员会、组合会计和验证反馈均未被误画为 `run_cycle` 的自动副作用。
- 决策图明确主研究链不自动下单、不连接券商；验证图明确统计归因只有经过
  人工复核后才进入下一轮规则，不自动训练模型。
- 新增 `scripts/generate_brain_diagrams.py`，可确定性重建五张 1440×900 SVG；
  资产测试同时检查图序、SVG 合法性、专业字体栈、核心交付物和系统边界。
- 五图均已使用 Chromium 按 1440×900 实际渲染审查，修正了查询交付标签、
  反馈标签和显式委员会连线的遮挡或语义问题。

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

- 后端：104 passed
- 覆盖率：87.16%（门禁 80%）
- Ruff：通过
- Ruff format：127 files already formatted
- mypy：64 个源码模块无问题
- Python sdist/wheel：构建成功
- Web ESLint：通过
- Web Vitest：2 passed
- TypeScript/Vite production build：通过
- Playwright 桌面/移动端：4 passed
- Docker Compose 重启持久化：通过，临时容器、网络和卷已清理
- 功能台账：90 / 85 / 94.44%，`UNREVIEWED=0`、`UNKNOWN=0`
- 发布安全扫描：180 个文件，0 blocker

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
