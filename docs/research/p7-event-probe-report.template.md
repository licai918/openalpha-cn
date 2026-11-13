# Tushare 事件类接口探查报告（`V2-P7-010`）——模板

> **性质**：这是 `scripts/p7_event_probe.py` 的元数据报告模板，**不是研究证据**。本报告只记接口名、权限回答、行数与页数、截断标记、字段名、见过的最早日期、名字像时间戳的字段名；**不出现任何行值**（证券代码、证券名称、价格、金额、公告标题、时间戳单元格都不写），也不落盘任何数据。由此得出的可用性 / 深度 / 点时间判断一律是"候选"，不得因为探查跑通就写成更强的结论。
>
> **状态：模板，未运行。** 真实请求是计划 C 的 gated live step L1：要用户明确同意（`--live --i-have-the-users-go --budget-file <路径>`），请求上限 `PROBE_CAP` = 300（假设，可调），且这 300 次是**跨多次 `--live` 调用累计**的（`--budget-file` 记录已花次数）。报告由脚本打印，本文件只把它的输出抄进来。"待填"由真实运行填写，不由推断填写。

## 0. 这个模板对应的东西

- 脚本：`scripts/p7_event_probe.py`（默认 `--dry-run`，不发请求、不写盘、忽略 `--budget-file`；`--live` 是唯一会发请求的路径，也是唯一会写文件的路径——只写 `--budget-file` 这个预算文件）。
- 请求预算：`PROBE_CAP` = 300。计划在构造时就拒绝超过 300 个**首发**调用；发送时 `p7_smoke_requests.CappedCounter` 再拒绝第 301 个请求（它不会被发出）。全市场日触发的跨页 follow-up 请求也从同一个上限里扣，所以一次调用的实际请求数 ≤ 300。
- 累计预算：`--live` 必须给 `--budget-file PATH`。文件是一个 JSON 对象（`PROBE_BUDGET_SCHEMA` = `openalpha-p7-event-probe-budget/v1`，字段 `spent` 为累计已发请求数）。每次 `--live` 前读它，运行后原子重写（同目录临时文件 + `os.replace`）。本次可用预算 = `PROBE_CAP - spent`；`spent` 到 300 就拒绝，**一个请求都不发**。`--dry-run` 既不读也不创建这个文件。文件只是预算，不是锁：同一时间只跑一次 `--live`。
- 提前停止（修正轮 2，评审 N3）：遇到第一次**凭据被拒**（Tushare `40101`）或**限流**（`40203`）就停：只记这一条 finding，不再发任何后续计划调用或 follow-up 页。报告在预算行之后加一行 `stopped: credential refused, N planned calls not sent.`（限流时 `rate limited`），`--live` 以新的具名退出码 `STOPPED_EXIT` = 3 退出并在 stderr 点名原因、接口、未发调用数与本次实际记录到预算文件的请求数。预算文件只记**实际发出**的请求（始终 40101 的情形就是 1），不是计划数。其它非零码与权限错误仍然是继续运行的 finding。
- 五个候选接口：`moneyflow`、`limit_list_d`、`top_list`、`top_inst`、`anns_d`。
- 每个接口的计划：`schema`（单只证券，看字段与权限）、`full_day`（一个全市场交易日）、`pit_field`（另一个交易日的单只证券，看时间戳类字段），加上 `--depth-years` 每个年份一个全市场日（默认 1–5 年）。默认计划 = 5 × (3 + 5) = 40 个首发请求。
- 一个计划调用 = 一条 finding；`full_day` 可能花多个请求。`full_day` 的一页如果报告了 Tushare 的 `has_more` 标志（**只有字面 `False` 表示完整**），或者页行数达到 `PAGE_ROW_LIMIT` 而响应**没有**这个标志，脚本就按 `offset`/`limit` 再发一个 follow-up 请求，findings 的 `rows` 是各页合计、`pages` 是实际页数。`PAGE_ROW_LIMIT` = 6000 是 Tushare Pro 文档默认的单次行数的**假设**（本仓库在 `daily` / `daily_basic` / `adj_factor` 上量到过 6,000 行；这五个事件接口自己的上限**没有量过**），**必须在 L1 现场确认，模板不把它当已验证事实**。`depth` / `pit_field` 不分页。

## 1. 环境

| 项 | 值 |
|---|---|
| 运行日期（北京） | 待填 |
| 运行提交 | 待填（`git rev-parse HEAD`） |
| `today` / `--depth-years` | 待填（不传 `--today` 就是运行当天；默认 depth years 1–5） |
| 本次请求数 / 上限 | 待填（报告的 `Requests spent: N of PROBE_CAP (300).` 行） |
| 累计请求数 / 上限（预算文件） | 待填（报告的 `Cumulative budget: N of PROBE_CAP (300) requests spent.` 行，以及 `--budget-file` 的路径） |
| 预算文件 | 待填（路径；`spent` 值；路径按原样使用，`~` 不展开） |
| Tushare 账号档位 / 积分 | 待填（只写档位或积分数量级，**不写 token、不写账号**） |
| 本机 / Python | 待填 |

## 2. 运行方式（先把两件事分开）

```bash
# 1) 先跑 dry run：只打印计划和调用数，不发请求，不写盘；--budget-file 被忽略。
uv run python scripts/p7_event_probe.py --dry-run

# 2) 只有用户明确同意后才跑（唯一会发请求的路径）：
#    环境里要有 TUSHARE_TOKEN；token 从环境读，绝不硬编码、不打印、不进报告。
#    --budget-file 是必须的：它记录跨调用累计已花请求数。
uv run python scripts/p7_event_probe.py --live --i-have-the-users-go \
  --budget-file <预算文件路径>
```

- 不带参数时默认就是 `--dry-run`；`--live` 与 `--dry-run` 同时给会被拒绝。
- `--live` 的退出码：0 正常；2 发请求之前就拒绝（参数/预算/凭据缺失）；**3 = 发出请求后因第一次凭据拒绝或限流而提前停止**（预算文件已经记下实际发出的请求数，重跑会被预算或凭据继续拒绝）。
- `--live` 缺 `--i-have-the-users-go`、环境里没有 `TUSHARE_TOKEN`、或缺 `--budget-file`，脚本拒绝并且**一个请求都不发**。
- `--live` 时如果预算文件读不了、不是本模板说的 JSON 形状、或 `spent` 已达 300，同样拒绝并且**一个请求都不发**。不要把预算文件当成"可以删除来重置"的东西：L1 只跑一次，耗尽就是耗尽。
- `--live` 只把报告打印到终端；脚本自己写的唯一文件是 `--budget-file`（原子重写），本文件由人从终端抄录。
- 运行结束后先看 `Cumulative budget` 行：它是预算文件里的累计值（= 运行前的 `spent` + 本次实际发出的请求数）。
- 一次运行把 §3 的表整段抄下来；若 `spent` 与表的页数 / 行数对不上，以脚本打印的 `Requests spent` 与 `Cumulative budget` 为准并记下原因（例如 follow-up 被拒绝：此时该接口的 `error code` 列会出现 follow-up 的错误码）。

### 2.1 中断 / 崩溃怎么办（`--budget-file` 的边界）

- 正常结束、半途被上限停住、或某次请求失败：脚本仍会把**实际发出的请求数**写进预算文件（计数在进入 transport 之前加，偏保守）。
- 进程在"已经发出请求"和"重写预算文件"之间被杀死（kill -9、断电）：这一笔**记不下来**，文件会少记。此时不得直接再跑 `--live`：先按最坏情况人工把这段时间的请求数补记进 `spent`（宁可多记），或者问用户怎么处理。预算文件不是锁，同一时间只允许一次 `--live`。

## 3. 报告正文（照抄 `render_report` 的输出）

```markdown
# Tushare event-interface probe (V2-P7-010) -- metadata report

> Tooling output for the gated live step L1. Metadata only, not evidence; no row value is
> printed or persisted. 候选 / candidate wording applies to anything drawn from it.

- Requests spent: N of PROBE_CAP (300).
- Interfaces reported: 5.
- Cumulative budget: 待填 of PROBE_CAP (300) requests spent.
- stopped: 待填（仅在提前停止时出现：`stopped: credential refused, N planned calls not sent.` 或 `stopped: rate limited, ...`；正常跑完没有这一行）

- Full-day paging: a `full_day` answer with `has_more=true`, or 6000 rows and
  no flag, buys one `offset` follow-up request from the same cap. The row limit is the
  vendor's documented default, to CONFIRM at the gated live step L1 -- not verified here.
- Truncation: `no` = complete; `possibly` = a page at the row limit with no flag and no
  follow-up sent; `yes` = the endpoint's flag said more rows exist (or a follow-up page
  failed) and they were not fetched. `rows` is then a lower bound, not the daily total.
  An error code beside a permitted interface is a follow-up page's refusal.

| interface | permitted | error code | rows | pages | truncated | earliest date seen | timestamp fields |
|---|---|---|---|---|---|---|---|
| `moneyflow` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `limit_list_d` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `top_list` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `top_inst` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `anns_d` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |

## Field names

- `moneyflow` (N field(s)): 待填
- `limit_list_d` (N field(s)): 待填
- `top_list` (N field(s)): 待填
- `top_inst` (N field(s)): 待填
- `anns_d` (N field(s)): 待填

## Error codes

- 待填（没有拒绝就抄 `none: every finding was permitted.`）
```

## 4. 每列怎么读（抄之前先读这一节）

- **permitted**：该接口所有调用是否都被允许。`yes` = 全部允许；`partial` = 有的允许有的被拒（常见于按用途限权或限频）；`no` = 全部被拒。
- **error code**：Tushare 返回的非零 `code`（或传输异常的类名）。已知：`40101` = 凭证错误或接口名不被识别，`40203` = 该接口按分钟限频。消息正文不保留（可能带凭证），所以报告里没有 `msg`。**`permitted=yes` 但 `error code` 非空**时，那是跨页 follow-up 那一页被拒 / 失败：`rows` 是已收到页的合计，`truncated` 会标 `yes` 或 `possibly`。
- **rows**：该接口这一组调用里**最大的一次答案**的总行数（跨页时是各页合计）。节假日 / 非交易日也会返回 0 行，那**不代表**接口不存在；dry run 用的 `today - 1` 周几只是离线的交易日猜测。
- **pages**：该答案实际收到的数据页数。拒绝或传输失败为 0；普通一页为 1；跨页时为真实页数。**注意**：`pages` 是"这次探查实际花了几页"，不一定是"这个交易日全市场共需几页"——只有 `truncated = no` 时它才是完整的页数。
- **truncated**：`rows` 是不是下界。`no` = 完整（最后一页 `has_more=False`，或行数低于 `PAGE_ROW_LIMIT` 且无标志）；`possibly` = 某页正好达到 `PAGE_ROW_LIMIT` 且无 `has_more` 标志，而预算 / 单调用页上限不允许再发确认页，所以"可能还有一页"；`yes` = 服务端自己的 `has_more=true` 说了还有行（或 follow-up 页失败），但没取到。`possibly` / `yes` 时，`rows` 只能当**下界**，不能当单日全市场行数写进结论；`truncated = yes` 时 `pages` 也偏小。
- **earliest date seen**：从返回行的 `*_date` 列解析出的最早日期，只保留日期本身。depth 行里最早的非空日期就是该接口历史深度的候选答案（仍要核对交易日历，见 §5）。若某 depth 行 `truncated` 不是 `no`，行数只是下界，但"该日有没有数据"这一点不受影响（该接口仍以 `rows > 0` 表示答出了那一天）。
- **timestamp fields**：字段名匹配 `*_date` / `*time*` / `*_at` 的列。**名字匹配不等于它是可用的发布时刻**：`trade_date` 是交易日，不是披露时刻；`first_time` / `last_time` 是涨停池首次 / 最后封板时间，属于盘中事实；公告类若只有 `ann_date`，则点时间不可用。这一列只是候选，判断要人来下。

## 5. 要回答的问题（每条都给"候选"结论）

| 问题 | 本报告里的证据 | 结论（候选） |
|---|---|---|
| 哪些接口可用、权限如何 | permitted / error code | 待填 |
| 单日全市场请求数 | `pages` + `rows` + `truncated`：`truncated = no` 时请求数 = `pages`、行数 = `rows`；`possibly` / `yes` 时两者都只是下界，要写明"至少" | 待填 |
| 历史起点（深度） | depth 行里最早的 `earliest date seen`（注意该行是否 truncation） | 待填 |
| 是否带发布时刻（点时间可用性） | timestamp fields 里有没有真正的发布时刻 | 待填 |
| 积分要求 | 拒绝码（`40203` 是限频不是积分；积分门槛通常表现为 `40101` 或特定拒绝） | 待填 |

## 6. 模板本身的记录

| 日期 | 改了什么 | 依据 |
|---|---|---|
| 2026-11-12 | 建模板（`V2-P7-010`，计划 C task 2.2）；由 `scripts/p7_event_probe.py` 打印元数据报告，本文件只抄录 | `.superpowers/sdd/plan-c/plan.md` Task 2.2；`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §8 |
| 2026-11-12 | 修正轮 2（评审 N3）：遇到第一次凭据拒绝（`40101`）或限流（`40203`）即停止，报告加 `stopped:` 行，`--live` 以 `STOPPED_EXIT` = 3 退出并只把实际发出的请求记进累计预算文件；其它非零码仍是继续运行的 finding | `.superpowers/sdd/plan-c/review-batch2-2.md` N3 |
| 2026-11-12 | 修正轮 1（评审 F7/F12）：加 `truncated` 列与跨页语义（`PAGE_ROW_LIMIT` 为待 L1 确认的文档默认值）、`Cumulative budget` 行与 `--budget-file` 的累计预算、路径 / 中断语义 | `.superpowers/sdd/plan-c/review-batch2-1.md` F7、F12 |
