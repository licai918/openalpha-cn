# T10：真实数据冒烟（`V2-P7-019`）

> **性质**：工程冒烟，不是研究证据。本报告**不出现任何收益数字**（账本权益、相对收益、IC、十分位差都不写）；记录的是这套代码在真实数据、真实时间上会做什么、在哪里拒绝、花多少请求。冒烟登记不是 `V2-P7-006` 的登记，它的输出一律"这些单不要下"。
>
> **状态：进行中。** r2 的第 1 晚停在上游重述的历史 adj_factor 上（§3、§6），用户选择先修研究面板、换新根 r3；r3 第 1 晚已完成。 下面的"待填"由真实的晚上填写，不由推断填写。怎么跑、每晚记什么、阶段 1 的 go / no-go 判据，都在 `docs/runbooks/daily-selection.zh-CN.md` 文末的「真实数据冒烟（`V2-P7-019`）」。

## 0. 结论（阶段 1 结束时填）

- go / no-go：待填（逐条对照运行手册「阶段 1 的 go / no-go」G1–G9）。
- 缺陷：待填（每条一个 issue id）。

## 1. 环境

| 项 | 值 |
|---|---|
| 克隆的 `main` 提交 | 现用 r3：`edaaa266fa3f314d68596249d89a05fdf1880b51`（受绑定路径与 `5463b08` 逐字节相同）。r2 = `5463b08`（59 次后作废，保留）；r1 = `fc0ab17`（0 次，作废） |
| 冒烟登记 sha256 / `config_id` / 纪元 | r3：`44e166621e1e…` / `977eb7814abd1e3d` / 2026-11-11（克隆内提交 `f751703`）；r2：`e6c0370c157d…` |
| 源面板 `catalog.duckdb` sha256、文件数 | r3：`dce5d997…`，1829 个文件（2026 adj_factor 整年重建之后）；r2：`06895144…`（重建之前） |
| 阶段 1 / 阶段 2 上限 | 260 / 480（用户定，2026-11-11） |
| 机器、Python、uv | macOS（Darwin 25.5.0），钉住 worktree 的 Python 3.11.14 |

## 2. 每晚

（列的来源：「退出」是驱动的退出码，调用后立刻记下 `$?`；`smoke status` 的 `exit` 是被包住命令自己的码，两者可以不同。「请求 本次/累计/上限」来自 `smoke status`。「按接口」`status` 只给累计的 `by api` 一行；某一晚的分列要在驱动的收尾行（只在终端，不进日志）里当场抄下，或读 `requests.jsonl` 里该次调用 `end` 事件的 `requests` 字段。`status` 的时刻是北京时间，「多伦多开始–结束」要换算。）

| # | 北京交易日 | 多伦多开始–结束 | 子命令 | 退出 | 拒绝（步骤：第一行） | 请求 本次/累计/上限 | 按接口 | 补抓会话 | 因子补建 | 卖/买/顺延 | unfunded/unaffordable | facts universe/scored/coverage/UNKNOWN | 晨查差异 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| r2-1 | 2026-11-12 | 05:47:15–05:47:23 | `evening` | 1 | 第 2 步（panel update）：24 个漏掉的会话（2026-10-09 至 2026-11-11）多于一次能取的 10 个；什么都没取，打印最老 10 个的 `panel build` | 0 / 0 / 260 | `{}` | — | — | — | — | — | — | 符合运行手册「第一晚」第 1 步的预期 |
| r2-2 | 2026-11-12 | 05:47:30–05:52:30 | `remedy -`（`panel build --incremental`，as-of 2026-10-22，adj_factor+price+stk_limit） | 1 | `panel build` 自己拒绝：002851.SZ 2026-10-08 的 `pre_close` 115.72 与 2026-09-30 收盘按 adj_factor 推出的不一致；复取 2026-09-30 的 adj_factor 得 (2.3356,)，库里是 (2.3361,)；要求整年重建 2026 年 adj_factor | 59 / 59 / 260 | adj_factor 13、daily 13、daily_basic 11、stk_limit 11、suspend_d 11 | 11 个会话已取（含 10-08 再取一次）；adj_factor、suspend_d、daily、daily_basic 的 2026 分区已写到 10-22，stk_limit 未写（仍到 10-08） | — | — | — | — | — | BUDGET 打印 return-path-reproduction 56 次（14 对有争议的 pre_close/adj_factor），在第一对上即停；未丢弃根目录。**到此停下**：所要求的补救是整年重建，驱动按设计拒绝它，且约 200 次请求的代价连同剩余补抓超出阶段 1 上限，须用户决定 |
| 修面板 | — | 07:02:05–07:10:40 | 冒烟之外：`openalpha panel build --runtime-dir ~/openalpha-research --year 2026 --as-of 2026-10-08T16:30:00+08:00 --exchange SSE --dataset adj_factor`（用户 2026-11-12 同意） | 0 | — | 182（不计入冒烟） | adj_factor 182 | 2026 年 182 个会话重取 | — | — | — | — | — | 002851.SZ 改存 2.3356；研究面板 catalog sha256 由 `06895144…` 变为 `dce5d997…`；G8「前」快照重拍为 `smoke-before-g8-r3.txt` |
| r3-1 | 2026-11-12 | 07:11:17–07:11:24 | `evening` | 1 | 第 2 步：24 个漏掉的会话（10-09 至 11-11）多于 10 | 0 / 0 / 260 | `{}` | — | — | — | — | — | — | 同 r2-1 |
| r3-2 | 2026-11-12 | 07:11:31–07:17:41 | `remedy -`（`panel build --incremental`，as-of 10-22） | 0 | — | 91 / 91 / 260 | adj_factor 29、daily 29、daily_basic 11、stk_limit 11、suspend_d 11 | 10-08 至 10-22（11 个） | — | — | — | — | — | 收益路径复现补取 18 对（adj_factor/daily 各多 18 次）；写 `upstream_defects` 11 行（9 条 `pre_close_corroborated_over_adj_factor`、2 条 `superseded_after_publication` fina_indicator） |
| r3-3 | 2026-11-12 | 07:17:47–07:17:49 | `evening` | 1 | 第 2 步：14 个漏掉的会话（10-23 至 11-11） | 0 / 91 / 260 | `{}` | — | — | — | — | — | — | |
| r3-4 | 2026-11-12 | 07:17:57–07:23:33 | `remedy -`（as-of 11-05） | 0 | — | 55 / 146 / 260 | 五个接口各 11 | 10-22 至 11-05（11 个） | — | — | — | — | — | |
| r3-5 | 2026-11-12 | 07:23:40–07:28:14 | `evening` | 1 | 第 4 步（factor build）：四个 60 日因子 24 个漏掉的会话（10-09 至 11-11）多于 20 | 36 / 182 / 260 | adj_factor/daily/daily_basic/stk_limit/suspend_d 各 6、index_daily 3、namechange 1、stock_basic 1、trade_cal 1 | 6 个会话（11-05 再取一次、11-06 至 11-12；按接口次数推断） | — | — | — | — | — | 第 2 步取完剩余会话后在第 4 步拒绝 |
| — | — | 07:28:21 | `remedy -` | 5（驱动拒绝，未运行，未入账） | `no openalpha … in this line` | 0 | — | — | — | — | — | — | — | 控制者粘错了行（拒绝信息那一行而非命令行），不是缺陷 |
| r3-6 | 2026-11-12 | 07:28:32–07:34:32 | `remedy -`（`factor build`，20 个 as-of） | 0 | — | 0 / 182 / 260 | `{}` | — | 10-09 至 11-05（20 个），约 6 分钟 | — | — | — | — | processed 110534 / source_not_computed 7813 |
| r3-7 | 2026-11-12 | 07:34:42–07:40:05 | `evening` | 0 | — | 0 / 182 / 260 | `{}` | 11-06、11-09、11-10、11-11 | 11-06、11-09、11-10、11-11 | 0 / 10 / 0 | 无 / 002371.SZ、600519.SH | 3195 / 3185 / 0.996870 / 1272 | — | 第一晚，INITIAL_BUILD；002148.SZ 只配到 2,000 股（现金不足 22,260 元）；预测 `prd_05e567700046c65ae8d5f3a1` forward |
| r3-8 | 2026-11-12 | 07:40:13–07:40:15 | `evening`（G7） | 0 | — | 0 / 182 / 260 | `{}` | — | — | 同上（逐行相同） | — | — | — | `the day was already complete; nothing was run`：G7 过 |
| r3-9 | 2026-11-12 | 08:32:18–08:32:19 | `evening`（G7 文件级复核） | 0 | — | 0 / 182 / 260 | `{}` | — | — | — | — | — | — | 重跑前后 `RT/paper`、`RT/daily_survivor` 下 6 个文件的 sha256 逐一相同：G7 过（文件级） |
| r3-10 | — | 08:32:28 | `weekly`（G4，提前跑） | 0 | — | 0 / 182 / 260 | `{}` | — | — | — | — | — | — | `records: 1 witnessed … 0 missing`；`not tested: 0 of 2 pre-registered periods matured`；次要族 `not applied: … no p-value`：G4 过。G2 的「周报列出缺失」要在第 4 晚后再跑一次周报 |
| r3-11 | 2026-11-12 | 19:33:06–19:33:21（北京 11-13 08:33） | `morning`（G3） | 0 | — | 1 / 183 / 260 | namechange 1 | — | — | — | — | — | — | 复核 11-12 的计划：advice 无变化、notices 无；运行前后 `RT/paper/snapshots` 列表（2 个文件）与 `ledger.sqlite3` sha256 逐一相同：G3 过 |
| r3-12 | 2026-11-13 | 08:26:02–08:34:58 | `evening` | 0 | — | 16 / 199 / 260 | adj_factor 2、daily 2、daily_basic 2、index_daily 3、namechange 1、stk_limit 2、stock_basic 1、suspend_d 2、trade_cal 1 | — | — | 0 / 0 / 0 | 无 / 600519.SH、002371.SZ | 3194 / 3184 / 0.996869 / 1272 | — | 第二晚（周五，下单日 11-16）；昨晚 10 笔买单全部 filled；今晚无新单；screen eligible 2111 / survivors 1689；预测 `prd_3b5520fc188116af94585028` forward |
| r3-13 | 2026-11-13 | 08:35:08–08:35:10 | `evening`（G7） | 0 | — | 0 / 199 / 260 | `{}` | — | — | 同上 | — | — | — | `the day was already complete; nothing was run`；重跑前后 `RT/paper`、`RT/daily_survivor` 下 9 个文件 sha256 逐一相同：G7 过（文件级） |

## 3. 逐类拒绝

（按 `step N (名称)` 分类：次数、补救是否走通、花了多少请求。）

- 第 2 步（panel update）：补抓过多 1 次（预期内）；补救 `panel build --incremental` 1 次，在 r2 上**没有走通**——上游重述了一个历史 adj_factor（见 §4、§6），面板要求整年重建 adj_factor，59 次请求。研究面板整年重建 adj_factor 后，r3 上两批补救都走通（91 + 55 次）。
- 第 4 步（factor build）：因子补建过多 1 次（预期内），补救走通，0 次请求，约 6 分钟。

## 4. 与脚本化市场的差异

（`stk_limit` 是否分页、撤回复核 REFETCH、收益路径复现 BUDGET、停牌无日线、`namechange` 重述、行业以外的任何新形状。）

- **上游重述历史 adj_factor（新形状）**：脚本化市场里没有这一种。2026-11-12 Tushare 对 002851.SZ 2026-09-30 的 adj_factor 返回 2.3356，研究面板（2026-10-08 写入）存的是 2.3361。收益路径复现的 BUDGET 一行报出 2026 年有 14 对 pre_close/adj_factor 争议（56 次请求的预算），第一对就因此拒绝。
- `stk_limit` 11 个会话 11 次请求：本批未分页。

## 5. UNVERIFIABLE 的粗略占比（阶段 2）

（周报 late-record check 一节的条数 / 已核查条数；注明是粗测，不是协议读数。）

## 6. 缺陷清单

| 缺陷 | 现象（输出原文） | issue |
|---|---|---|
| 上游重述一个历史 adj_factor 后，每日链的唯一补救是整年重建该年 adj_factor（每个会话 1 次请求：到 2026-11-11 约 206 次，年末约 243 次）；冒烟驱动按设计拒绝整年重建，阶段 1 上限也放不下。真实的前向运行会遇到同样的情形（`~/openalpha-research` 面板存的是同一个 2.3361） | `a re-fetch of adj_factor on 2026-09-30 answered (2.3356,) where the store holds (2.3361,) … rebuild adj_factor for the year` | 待开（用户已选择先修研究面板，冒烟继续；代码上的窄补救——只按证券补取复权因子——留作后续 issue） |
| 一次被拒的增量构建留下不一致的面板：adj_factor/suspend_d/daily/daily_basic 写到 10-22，stk_limit 停在 10-08（输出自己说明了这一点，是已知的无事务写入） | `4 partition(s) were written before this build stopped and are still stored` | 记录，不另开（设计如此，下一次构建按各自的存储日期续取） |

## 7. 没有测到的

（例如：新年第一天（`V2-P7-021`）、长假后的全年补课、账户与纸面账簿的对账。）
