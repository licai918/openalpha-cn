# 当前交接入口

当前工作区：[`specs/v2/`](specs/v2/) — OpenAlpha CN v2 智能研究与选股平台改造

- 范围、实测基线与决策依据：[`specs/v2/openalpha-cn-v2-prd.md`](specs/v2/openalpha-cn-v2-prd.md)
- 阶段、闸门与 issue 切片：[`specs/v2/openalpha-cn-v2-roadmap.md`](specs/v2/openalpha-cn-v2-roadmap.md)
- 四缝审计证据与缺口证明：[`specs/v2/openalpha-cn-v2-seam-audit.md`](specs/v2/openalpha-cn-v2-seam-audit.md)

## 当前状态

v2 已交付 P0.A 至 P5（路线图里 P5 只剩 `V2-P5-038` 一行未完成）和 **P6 选股就绪**（2026-10-07 合并，PR #2）。
P6 的工程闸门成立，但**唯一一次预登记的保留期检验没有通过**：每日榜单只是候选，没有超额收益的证据，
不应作为投资依据（见 [`research/p6-results.md`](research/p6-results.md)）。

**当前工作是 P7：决策链整合与超额收益研究**（2026-10-08 立）。事实、探索性发现、预估、决策记录、闸门和
修订记录在 [`specs/v2/openalpha-cn-v2-p7-decision-chain.md`](specs/v2/openalpha-cn-v2-p7-decision-chain.md)，
方向决定见 [`architecture/ADR-0005-single-decision-engine.md`](architecture/ADR-0005-single-decision-engine.md)，
issue 清单（`V2-P7-001` …）在路线图 §P7。顺序（2026-10-13 起）：**Step 0（惰性底座：幸存者持仓 + 每日交易清单 +
纸面账本）→ 方案 C（智能体前向登记）→ 方案 A（否决层）→ 方案 B（证据变特征）**，每一步之前有闸门；改动顺序或闸门
必须先在规格 §9 记一行。Step 0 的详细设计在
[`superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md`](superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md)
（资金约 35 万元、持股 ≤ 10 只；用 sub-agent 驱动开发 + 测试先行执行，设计与实施计划都先经用户审阅）。

**Step 0 的代码已完成（2026-10-14；2026-10-15 的最终修复波与第三次全量闸门之后，已于 2026-10-15 本地快进并入 `main`，`a292c4d`，尚未推送；集成分支已删）**
（本地 main 停在 `d9722d6`，含领域内核与适配器、纸面成交的主体，未推送）：领域内核 `domain/survivor_*.py`、
面板 → 资格事实适配器 `strategy_survivor_facts.py`、纸面账本（`backtest/paper_step.py`、`paper_state.py`、
`paper_settlement.py`、`paper_evening.py`：开盘成交、每日快照、结算、退市核销、干跑再提交的一晚与补结算）、
`survivor_book/v1` 登记与每日一天（`scripts/daily_selection.py` 按 `kind` 分派，`--morning-recheck` 是清晨复核）、
迟到记录核查与周报的幸存者分支（`scripts/forward_report.py`）。路线图 §P7：`V2-P7-005`、`008`、`016`、`017` 关闭，
`009`、`018` 的代码已交付但验收各缺一两项（行内写明；2026-11-02 补上的部分见下一段）；能力台账由 211 项增至 232 项。**这是工程完成，不是研究证据**：
还没有任何幸存者配置被登记，也没有在真实面板上跑过一天。**下一步**（2026-11-01 路线调整，用户采纳，见
[`superpowers/plans/2026-11-01-p7-route-adjustment.md`](superpowers/plans/2026-11-01-p7-route-adjustment.md)）：`V2-P7-019` 真实数据冒烟
（真实时间、一次性克隆、先按打印的批次手工补面板，输出一律标"这些单不要下"）→ 并行 `004`、`024`（2022–2023 的预登记
读数 + 停止规则）、`010` / `012` 探针 → `006a`（先做所有会改受绑定代码的事）→ `006b`（登记，争取在 2027-01-04 之前）；
`002`、`003`、`020` 移出关键路径。`V2-P7-006` 是研究决策检查点，**停下等用户明确确认**才冻结参数、写登记；真钱闸门：
`006` 经确认并登记、账户成交对账输入与现金约束上线、用户明确说"开始"，三条全部成立之前不用打印出来的清单真钱交易。
`006` 的输入清单是规格 §9 的最后几行，最终修复波另外带出的几项（同一配置重新登记就是另一本账、清晨复核与定时任务、账户与纸面账簿
的分叉和留给用户的产品选项、集合竞价限价单要向券商确认、UNVERIFIABLE 在真实行情上的占比）写在
[`runbooks/daily-selection.zh-CN.md`](runbooks/daily-selection.zh-CN.md) 的「已知局限（幸存者形态）」。

**开发批次 1（2026-11-02）**：路线调整之后的第一批开发——`V2-P7-004`（研究诊断工具与对受保护窗口"读一次"的路径）、`009`（两条验收测试，没有发现缺陷）、`018`（周报的功效输出）、`019`（真实数据冒烟的工具与运行手册）、`021`（空的名称变更公告年）——的代码在集成分支 `feat/v2-p7-004-dev-batch-1` 上（代码在 `22d65ae` 冻结，其后只有 docstring、注释、一条守卫字符串和闸门修复，清单见收尾记录 §0；没有任何登记存在，无绑定被作废），**未推送，没有对真实数据运行过，一次 Tushare 请求也没有发过**。批次还新增 `V2-P7-025`（登记簿 `stock_basic` 的新年缺口：**2027-01-04 之前的硬闸门**，先做 0 次请求的离线测量；issue 总数 253）。**下面这些还是用户的**：把 `V2-P7-025` 列为 `006a` 的一项并加入 `006` 的依赖（控制者的建议，待用户确认）；`021` 的暂定取值（两种 `no_data` 都算经核实为空、空到 1 月 31 日、对所有 `panel build` 生效）；冒烟的请求上限（估计，不分页：阶段 1 约 200–260 次，整个冒烟约 430–470 次；`stk_limit` 分页时更多）、谁来跑每一晚、第一晚的日期，以及 Task 6（第一次真实请求）的明确"开始"；`024` 的停止规则数值；K 与中期规则、主期限；Task 7（读研究期，零次请求）的明确"开始"；向券商确认限价单与冻结资金的假设。真实的验证读之前，主检出（今天是浅克隆）要先 `git fetch --unshallow`，读数的认领要提交并留在本仓库的 refs 里。逐项的交付、评审轮次、延后事项与运行前提见 [`superpowers/plans/2026-11-02-p7-dev-batch-1-closeout.md`](superpowers/plans/2026-11-02-p7-dev-batch-1-closeout.md)。**这些都是工程状态，不是研究证据；措辞仍是"候选"。**

**开发批次 2（2026-11-07）**：`V2-P7-025`（登记簿 `stock_basic` 的新年缺口，T1–T5）与 `V2-P7-024` 的四个前置（`V2-P7-004` 收尾 §5 第 10 条 ①–④，Tasks 1–5）的代码在分支 `feat/v2-p7-025-batch-2` 上（从 `main` 的 `587a158` 分出；代码在 `4b4ec94` 冻结的是第一次串行闸门，其后先是一轮文档/文本波、再是最终评审波的修复——后者改了可执行字节：`scripts/research/p7_reading.py` 的预检现在把无法规划的日历按名报出、结果行的知识行标明那是登记者的话，`src/openalpha_cn/domain/stock_universe.py` 的 `universe_year_may_be_empty` 把日子限制在它自己的那一年内——所以计数的那次串行全量闸门跑在最终 tip 上，结果以控制者的报告为准；没有任何登记存在，无绑定被作废），**未推送，交付的代码没有跑过任何真实构建、因子或读数，一次 Tushare 请求也没有发过；只有那次 0 次请求的只读测量打开过真实面板的 parquet**。`025`：整份 `stock_basic` 抓取成功、观测年没有上市/退市时，`panel build` 把该年记成零行分区（stderr 一行 `EMPTY stock_basic year=…`），读者只在到该年 12 月 31 日（含，上海日历日）为止读作"今年还没有上市或退市"，之后按规则陈旧、当年第一条真实事件到来时整份替换；0 次请求的测量：过去 14 年有 10 年在当年第一个会话时还没有登记事件（缺口最长到几个月，2009-05-06、2013-02-08），1990–2026 每年至少 4 行。`024` 的四个前置：运行目录不得是 `~/openalpha-research` 或任一检出、认领之前按面板自己的日历核对 `minimum_periods`、`second_use` 要求验证窗口的认领已提交、登记必须写 `as_of_reason` 并在结果文本里重述（`READING_SCHEMA` 仍是 `.../v1`）。**没有改任何顺序或闸门；issue 总数仍是 253。下面这些仍是用户的**：`025` 的取值（到 12 月 31 日，含）是暂定；`024` 的读数本身、停止规则数值、K 与中期无效性、主期限，以及明确的"开始"；把 `025` 列为 `006a` 的一项并加入 `006` 的依赖；`021` 的两个取值；冒烟的数值、分工与第一晚日期；Task 7 的明确"开始"。真实的验证读之前要先 `git fetch --unshallow`，认领要提交并留在本仓库的 refs 里（`second_use` 还要验证窗口的认领已提交）。逐项的交付、评审轮次、延后事项与运行前提见 [`superpowers/plans/2026-11-07-p7-dev-batch-2-closeout.md`](superpowers/plans/2026-11-07-p7-dev-batch-2-closeout.md)。**这些都是工程状态，不是研究证据；措辞仍是"候选"。**

**开发批次 3（2026-11-11）**：三份批次 3 计划（`V2-P7-024` 的预登记停止规则与那唯一一次读数、`V2-P7-006a` 的交易约束、`006a` 的运维面与批次 2 验收缺口 G1–G6）的代码在分支 `feat/v2-p7-batch-3` 上（从 `main` 的 `ba60330` 分出，tip `403b8f5`，29 个提交；三条轨道逐个挑拣），**未推送，交付的代码没有跑过任何真实构建、因子、读数或冒烟，一次 Tushare 请求也没有发过**；那唯一一次验证读（2022–2023，一次、只读、0 次网络请求）与真实冒烟**没有做**，仍等用户明确说"开始"；**真钱闸门仍关着**；没有任何读数登记存在，所以没有绑定被作废；串行全量闸门跑在最终 tip 上，结果以控制者的报告为准。`024`：停止规则是一个数字一个具名常量、`survivor-forward-stop-rule/v1` 与协议并列、`not measurable` 是第三种结论、唯一一次读有了具名命令（`scripts/research/p7_read.py`，退出码 0/2/3/4，`expected_periods` 在认领之前核对），G-0 的判读与`docs/runbooks/p7-reading.zh-CN.md` 已写。`006a`：买单一律用 09:15 的现金定量、按涨停价冻结（1.10 倍是系统的假设，待向券商确认）、`illiquid` 命名为强制退出、资金报告按名列出短少、只读 `--show-book`、账户记录对账（按名分歧、冲突拒绝、不改写账簿）、launchd 只打印不安装。`025`：同一观测只写一次、更早的观测不再改写已存零行年（关闭批次 2 收尾 §4 的那一项）。G3/G4/G5 三个批次 2 验收缺口关闭。**没有改任何顺序或闸门；issue 总数仍是 253。下面这些仍是用户的**：A Task 5 那次不可逆的验证读（先 `git fetch --unshallow`、R4 的确切命令行、`--exchange SSE`、期望 24 期、明确的"开始"）；真实冒烟的每一步；`025` 的取值与归属已按 2026-11-07 的一揽子采纳（值暂定，可推翻）；券商假设；合并/推送方式。逐项的交付、评审轮次、裁决、延后事项与运行前提见 [`superpowers/plans/2026-11-11-p7-dev-batch-3-closeout.md`](superpowers/plans/2026-11-11-p7-dev-batch-3-closeout.md)。**这些都是工程状态，不是研究证据；措辞仍是"候选"。**

**每日登记自 2026-10-08 起暂停**，等 `V2-P7-006` 的新配置预登记提交后恢复。launchd 从未安装；现有登记
（`docs/research/p6-registration.json`）在新登记提交后作废，保留为历史。此前计划的 P6
（[`superpowers/plans/2026-09-26-selection-ready.md`](superpowers/plans/2026-09-26-selection-ready.md)）已完成。
P3 交付了因子契约、面板因子引擎、五个因子族、四项研究（IC / 分位组合 / 冗余 / 可交易性）、
密封的三档实验产物，以及因子层的三个公开面；下面两节说的是因子层和模型层怎么用。

> 本节此前写着「v2 实现尚未开始，下一步是 `V2-P0A-001`」，那句话在 P3 合并后仍然留在这里。
> 一个按仓库指引阅读的新用户会据此得出「因子层不存在」的结论——而 `openalpha factor list`
> 当时已经能列出 19 个因子。交接入口写错方向比不写更危险，所以这份文件现在由
> `scripts/build_feature_coverage.py --check` 的台账数字来对账，而不是由记忆维护。

因子层现在**对操作者可达**，一条命令链：

```bash
openalpha factor list                       # 这个 build 声明了哪些因子/变换/中性化
openalpha factor describe --factor reversal_1d/v1   # 一条声明的全文，含它承认不度量什么
openalpha panel build --dataset ... --year 2026     # 点时间面板（价量、财报、登记簿、日历、行业）
openalpha factor build --factor reversal_1d/v1 --tier processed \
  --transform cross_section_standard/v1 \
  --as-of 2026-01-08T09:00:00+00:00 --as-of 2026-01-09T09:00:00+00:00 \
  --year 2026 --max-staleness-days 30               # 计算并写入 raw / processed 两档
openalpha factor build --factor reversal_1d/v1 --tier neutralized \
  --transform cross_section_standard/v1 --neutralization industry_and_size/v1 \
  --as-of 2026-01-08T09:00:00+00:00 --as-of 2026-01-09T09:00:00+00:00 \
  --year 2026 --max-staleness-days 30               # 第三档，`factor run` 要读它
openalpha factor run --factor reversal_1d/v1 --start 2026-01-08 --end 2026-01-09 ...
```

**这个 block 里有两处是被 `V2-P5-048` 逐字跑出来的（两处都是「源头改了、拷贝没改」）。**
`--waive-max-staleness` 曾经在这里：`V2-P4-100` 实测它 exit 1 并把它从 `factor build --help`
的示例里去掉，本文件的拷贝原样留着。第二条 `factor build` 也是新加的——`--tier` 只写它点名的
那一档，只跑 `processed` 那条，`factor run --neutralization …` 随即 exit 1。

三个面等价：`openalpha factor *`、`GET /api/v1/factors` + `POST /api/v1/factors/run`、
`OpenAlphaSDK.factor_catalog()` / `.run_factor_experiment()`。
`factor build` 只有命令行与 SDK 两个面，与 `panel build` 一致——它写面板分区，
而服务本身不带鉴权。

**模型层现在也对操作者可达（`V2-P4-021`）。** `V2-P4-010`–`V2-P4-017` 那八条契约在此之前
`tests/` 之外没有任何调用者；现在两条命令把它们接了起来：

```bash
openalpha model evaluate --feature reversal_1d/v1@raw --name reversal-rank \
  --family cross_sectional_rank --horizon 1d --seed 7 \
  --start 2026-01-06 --end 2026-01-14 --year 2026 \
  --folds 2 --test-days-per-fold 2 --embargo-sessions 0 \
  --min-scored-ratio 0.5 --as-of 2027-01-01T00:00:00+08:00   # 逐折拟合，逐折报数
openalpha model daily-run --feature reversal_1d/v1@raw ... \
  --predict-at 2026-01-16T09:00:00+00:00 --min-scored-ratio 0.5 \
  --as-of 2027-01-01T00:00:00+08:00                          # 在结果已知前把预测落库
openalpha model predictions        # 这个 runtime 目录登记过的每一个地址
openalpha model prediction prd_…   # 其中一条，按它登记时的样子
```

三个面等价：`openalpha model *`、`POST /api/v1/models/{evaluate,daily-run}` +
`GET /api/v1/predictions[/{record_id}]`、`OpenAlphaSDK.evaluate_model()` /
`.run_daily_model()`。`FilePredictionStore` 由此进了组装根（第十二个 store），
`RunManifest.alpha_model_versions` 由 `daily-run` 填上——这个槽 `010` 声明、`016` 与 `017`
先后实测自己填不了。

## 已知边界（不是遗漏，是有名字的披露）

- **中性化档只差一个会话**（`V2-P4-026` + `V2-P4-028`）。这条曾经写作「中性化档在覆盖年内
  建不出来」：两个外部数据集都整块读，残差只能在「读到的每一年的最后一次已存**调整**」当天
  或之后算出来，真实语料上那就是年度成分股调整。`V2-P4-026` 把 `daily_basic` 撤了，
  `V2-P4-028` 把 `index_member_all` 也撤了（`load_industry_market_cap_cross_section` 改走
  `panel_ingest.load_industry_cross_section`，该门收**日期**作参数）。**剩下的边界只有一个
  会话宽**：残差必须带处理档面板自己的时刻，而两个外部读都按该时刻所在的那一天取，所以只有
  在那天自己的收盘之后、且那天开市，才建得出来。`openalpha factor build --tier neutralized`
  在更早的时刻上**按名字拒绝**并且不写任何东西，代码是
  `the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed`；另有一条来自调用
  方自己的收窄（`--year` 少报了截止到那天的某个已存成员年），见
  `KNOWN_NEUTRALIZATION_LIMITATIONS
  .a_stored_membership_year_left_unread_refuses_the_day_rather_than_answering_it`。
- **出厂的变换与中性化下限（`min_cross_section=100`）高于一个稀薄市场**，所以窄截面上
  两个派生档每个名字都只有覆盖码、没有值，六格归因全是 `not_measured`。
- 完整清单：`openalpha factor list --json` 的 `run_limitations`，或
  `openalpha_cn/factor_view.py#KNOWN_FACTOR_RUN_LIMITATIONS`。
- **模型面的十五条边界随每个答案一起返回**（`--json` 的 `limitations`，或
  `openalpha_cn/model_view.py#KNOWN_MODEL_VIEW_LIMITATIONS`；两个终端面各印一行报数与取全文的
  flag）。最容易被误读的四条：`--min-scored-ratio` 是**覆盖度**下限、永远不是质量判决；
  `standing: forward` 只说明本存储在结果可知之前持有了这些字节，**不说明**批次是在它自称的时刻
  产出的——`predicted_at` 本仓校验不了，也没有任何东西防得住拥有这块磁盘的人；它同样**不限定**
  拟合读面板的那个时刻，`--as-of` 可以晚于结果可知的时刻而 standing 仍是 `forward`，那个时刻
  只在 `daily-run` 自己的答案上（`training.as_of` 与终端的 `panel read at`）而不在记录里；
  单列的 `cross_sectional_rank` 运行，其 `mean_rank_ic` 只看得见拟合的**符号**，会动的数在
  `folds[].parameters`，也是终端折表的最后一列。前两句随答案一起给出，不只写在文档里。
- **`openalpha model predictions` 按托管顺序（`recorded_at`）列出，不是按内容哈希**，每行自带
  日期、standing、horizon、打分数与模型名，故不必逐条打开就能挑。`model prediction <id>` 的正文
  带 `model` 键，即记录按值携带的整份拟合制品（特征列、`feature_version`、`code_commit`、seed、
  超参、训练截止、样本数、系数），所以一年后读到的记录能自己说出模型是什么。
- **同一天重跑会多落一条记录**：`predicted_at` 取自本进程时钟并进入内容地址，定时任务重试因此
  为一天留下两条；这是记为约束而非缺陷，三种「修法」各自被本仓已有的论证挡住，见
  `a_re_run_of_one_day_files_a_second_record_because_predicted_at_reaches_the_address`。
- **模型面与榜单面的面板前置条件互有缺口**：模型面要 `adj_factor`（标签是两个交易日之间的收益）
  而不要 `namechange`；榜单面反过来。两边的 `409` 都会写出修复它的那条 `panel build`。

## 维护者依次读取

1. 根目录 `AGENTS.md`
2. `specs/v2/openalpha-cn-v2-roadmap.md`（当前阶段与闸门）
3. `specs/v2/openalpha-cn-v2-prd.md`（范围与决策依据）
4. `specs/openalpha-cn-v1-spec.md`（v1 契约基线）
5. `release/openalpha-v1-feature-ledger.md`（能力台账，由
   `scripts/build_feature_coverage.py` 生成并在 CI 中逐文件复核）
6. `release/handoff-main-20260724-ten-capabilities.md`（v1 历史交接）
7. `audits/three-upstream-source-audit-20260724.md`
8. 与当前任务相关的架构、数据、API 或部署文档

历史发布交接不删除；新版本新建交接文件后只更新本入口。
