# Tushare 事件类接口探查报告（`V2-P7-010`）——模板

> **性质**：这是 `scripts/p7_event_probe.py` 的元数据报告模板，**不是研究证据**。本报告只记接口名、权限回答、行数与页数、字段名、见过的最早日期、名字像时间戳的字段名；**不出现任何行值**（证券代码、证券名称、价格、金额、公告标题、时间戳单元格都不写），也不落盘任何数据。由此得出的可用性 / 深度 / 点时间判断一律是"候选"，不得因为探查跑通就写成更强的结论。
>
> **状态：模板，未运行。** 真实请求是计划 C 的 gated live step L1：要用户明确同意（`--live --i-have-the-users-go`），请求上限 `PROBE_CAP` = 300（假设，可调）；报告由脚本打印，本文件只把它的输出抄进来。"待填"由真实运行填写，不由推断填写。

## 0. 这个模板对应的东西

- 脚本：`scripts/p7_event_probe.py`（默认 `--dry-run`，不发请求、不写盘；`--live` 是唯一会发请求的路径）。
- 请求预算：`PROBE_CAP` = 300，计划在构造时就拒绝超过 300 个调用，发送时 `p7_smoke_requests.CappedCounter` 再拒绝第 301 个请求（它不会被发出）。
- 五个候选接口：`moneyflow`、`limit_list_d`、`top_list`、`top_inst`、`anns_d`。
- 每个接口的计划：`schema`（单只证券，看字段与权限）、`full_day`（一个全市场交易日）、`pit_field`（另一个交易日的单只证券，看时间戳类字段），加上 `--depth-years` 每个年份一个全市场日（默认 1–5 年）。默认计划 = 5 × (3 + 5) = 40 个请求。
- 一次计划调用 = 一个请求 = 一条 finding。工具**不分页**：`pages` 为 1 表示收到一个数据页，为 0 表示被拒绝或传输失败；一个全市场日若跨多页，只会体现为该接口 `rows` 很大，跨页请求数要另做一次探查（未批准）。

## 1. 环境

| 项 | 值 |
|---|---|
| 运行日期（北京） | 待填 |
| 运行提交 | 待填（`git rev-parse HEAD`） |
| `today` / `--depth-years` | 待填（不传 `--today` 就是运行当天；默认 depth years 1–5） |
| 实际请求数 / 上限 | 待填（报告的 `Requests spent: N of PROBE_CAP (300).` 行） |
| Tushare 账号档位 / 积分 | 待填（只写档位或积分数量级，**不写 token、不写账号**） |
| 本机 / Python | 待填 |

## 2. 运行方式（先把两件事分开）

```bash
# 1) 先跑 dry run：只打印计划和调用数，不发请求，不写盘。
uv run python scripts/p7_event_probe.py --dry-run

# 2) 只有用户明确同意后才跑（唯一会发请求的路径）：
#    环境里要有 TUSHARE_TOKEN；token 从环境读，绝不硬编码、不打印、不进报告。
uv run python scripts/p7_event_probe.py --live --i-have-the-users-go
```

- 不带参数时默认就是 `--dry-run`；`--live` 与 `--dry-run` 同时给会被拒绝。
- `--live` 缺 `--i-have-the-users-go`，或环境里没有 `TUSHARE_TOKEN`，脚本拒绝并且**一个请求都不发**。
- `--live` 也只把报告打印到终端；脚本自己不写任何文件，本文件由人从终端抄录。
- 一次运行把 §3 的表整段抄下来；`spent` 与表的行数对不上时，以脚本打印的 `Requests spent` 为准并记下原因。

## 3. 报告正文（照抄 `render_report` 的输出）

```markdown
# Tushare event-interface probe (V2-P7-010) -- metadata report

> Tooling output for the gated live step L1. Metadata only, not evidence; no row value is
> printed or persisted. 候选 / candidate wording applies to anything drawn from it.

- Requests spent: N of PROBE_CAP (300).
- Interfaces reported: 5.

| interface | permitted | error code | rows | pages | earliest date seen | timestamp fields |
|---|---|---|---|---|---|---|
| `moneyflow` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `limit_list_d` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `top_list` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `top_inst` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `anns_d` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |

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
- **error code**：Tushare 返回的非零 `code`（或传输异常的类名）。已知：`40101` = 凭证错误或接口名不被识别，`40203` = 该接口按分钟限频。消息正文不保留（可能带凭证），所以报告里没有 `msg`。
- **rows**：一次全市场日请求返回的行数（一个数据页）。节假日 / 非交易日也会返回 0 行，那**不代表**接口不存在；dry run 用的 `today - 1` 周几只是离线的交易日猜测。
- **pages**：本工具一次只发一个请求，所以数据页为 1；拒绝或传输失败为 0。它**不是**"单日全市场需要几页"；那个问题要另做跨页探查（未批准，且会消耗请求）。
- **earliest date seen**：从返回行的 `*_date` 列解析出的最早日期，只保留日期本身。depth 行里最早的非空日期就是该接口历史深度的候选答案（仍要核对交易日历，见 §5）。
- **timestamp fields**：字段名匹配 `*_date` / `*time*` / `*_at` 的列。**名字匹配不等于它是可用的发布时刻**：`trade_date` 是交易日，不是披露时刻；`first_time` / `last_time` 是涨停池首次 / 最后封板时间，属于盘中事实；公告类若只有 `ann_date`，则点时间不可用。这一列只是候选，判断要人来下。

## 5. 要回答的问题（每条都给"候选"结论）

| 问题 | 本报告里的证据 | 结论（候选） |
|---|---|---|
| 哪些接口可用、权限如何 | permitted / error code | 待填 |
| 单日全市场请求数 | rows（对照该接口单次上限；pages 只说明本工具没分页） | 待填 |
| 历史起点（深度） | depth 行里最早的 `earliest date seen` | 待填 |
| 是否带发布时刻（点时间可用性） | timestamp fields 里有没有真正的发布时刻 | 待填 |
| 积分要求 | 拒绝码（`40203` 是限频不是积分；积分门槛通常表现为 `40101` 或特定拒绝） | 待填 |

## 6. 模板本身的记录

| 日期 | 改了什么 | 依据 |
|---|---|---|
| 2026-11-12 | 建模板（`V2-P7-010`，计划 C task 2.2）；由 `scripts/p7_event_probe.py` 打印元数据报告，本文件只抄录 | `.superpowers/sdd/plan-c/plan.md` Task 2.2；`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §8 |
