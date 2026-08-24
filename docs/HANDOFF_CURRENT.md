# 当前交接入口

当前工作区：[`specs/v2/`](specs/v2/) — OpenAlpha CN v2 智能研究与选股平台改造

- 范围、实测基线与决策依据：[`specs/v2/openalpha-cn-v2-prd.md`](specs/v2/openalpha-cn-v2-prd.md)
- 阶段、闸门与 issue 切片：[`specs/v2/openalpha-cn-v2-roadmap.md`](specs/v2/openalpha-cn-v2-roadmap.md)
- 四缝审计证据与缺口证明：[`specs/v2/openalpha-cn-v2-seam-audit.md`](specs/v2/openalpha-cn-v2-seam-audit.md)

## 当前状态

v2 实现已进行到 **P4（模型与排序层）**。P0.A / P0.B / P1 / P2 阶段已合并，P3 交付了因子契约、
面板因子引擎、五个因子族、四项研究（IC / 分位组合 / 冗余 / 可交易性）、密封的三档实验
产物，以及因子层的三个公开面。

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
  --year 2026 --waive-max-staleness                 # 计算并写入 raw / processed 两档
openalpha factor run --factor reversal_1d/v1 --start 2026-01-08 --end 2026-01-09 ...
```

三个面等价：`openalpha factor *`、`GET /api/v1/factors` + `POST /api/v1/factors/run`、
`OpenAlphaSDK.factor_catalog()` / `.run_factor_experiment()`。
`factor build` 只有命令行与 SDK 两个面，与 `panel build` 一致——它写面板分区，
而服务本身不带鉴权。

**模型层现在也对操作者可达（`V2-P4-021`）。** `V2-P4-010`–`V2-P4-017` 那八条契约在此之前
`tests/` 之外没有任何调用者；现在两条命令把它们接了起来：

```bash
openalpha model evaluate --feature reversal_1d/v1@raw --name reversal-rank \
  --family cross_sectional_rank --horizon 5d --seed 7 \
  --start 2026-01-06 --end 2026-01-14 --year 2026 \
  --folds 2 --test-days-per-fold 2 --embargo-sessions 0 \
  --min-scored-ratio 0.5 --as-of 2026-01-20T04:00:00+00:00   # 逐折拟合，逐折报数
openalpha model daily-run --feature reversal_1d/v1@raw ... \
  --predict-at 2026-01-16T09:00:00+00:00 --min-scored-ratio 0.5  # 在结果已知前把预测落库
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
