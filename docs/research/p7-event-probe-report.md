# Tushare 事件类接口探查报告（`V2-P7-010`）——L1 实测

> **性质**：`scripts/p7_event_probe.py` 的元数据报告，**不是研究证据**。只记接口名、权限回答、行数与页数、截断标记、字段名、见过的最早日期、名字像时间戳的字段名，以及被拒时厂商 `msg` 的原文；**不含任何行值**，也没有落盘任何数据。下面的可用性 / 深度 / 点时间判断一律是"候选"。格式与每列读法见 [`p7-event-probe-report.template.md`](p7-event-probe-report.template.md)。
>
> **状态：已运行（gated live step L1，用户 2026-11-13 明确同意）。**

## 1. 环境

| 项 | 值 |
|---|---|
| 运行日期（北京） | 2026-11-14（多伦多 2026-11-13 傍晚） |
| 运行提交 | 第 1 次 `66803c9`（旧规则，在 `anns_d` 的 `40203` 处整轮停止）；第 2 次 `8c88e2d`（`40203` 只停该接口） |
| `today` / `--depth-years` | 2026-11-13 / 默认 1–5 |
| 本次请求数 / 上限 | 第 1 次 5；第 2 次 33（`Requests spent: 33 of PROBE_CAP (300).`） |
| 累计请求数 / 上限（预算文件） | 58 / 300（起点 20 = 2026-11-12 修正轮里用假 token 的 20 次被拒尝试，保守计入） |
| 预算文件 | `~/openalpha-smoke/p7-event-probe/budget.json`（仓库外；`spent` = 58） |
| Tushare 账号档位 / 积分 | 付费账号；本次运行没有读取积分数 |
| 本机 / Python | macOS（Darwin 25.5）/ 仓库 `.venv` |

## 2. 运行方式

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv run --no-sync --env-file .env "$PWD/.venv/bin/python" \
  scripts/p7_event_probe.py --live --i-have-the-users-go --budget-file ~/openalpha-smoke/p7-event-probe/budget.json
```

第 1 次运行（5 个请求）在 `anns_d` 的第一个请求处按当时的规则停止整轮，35 个计划调用没有发出；它暴露出 `40203` 既表示限流又表示无权限、而脚本没有记 `msg`。修正合入（`8c88e2d`）后第 2 次运行用完整计划重跑：`anns_d` 花 1 个请求拿到 `msg` 后不再请求，其余四个接口跑完全部计划。下面 §3 抄的是第 2 次运行的输出。

## 3. 报告正文（第 2 次运行，照抄）

```markdown
# Tushare event-interface probe (V2-P7-010) -- metadata report

> Tooling output for the gated live step L1. Metadata only, not evidence; no row value is
> printed or persisted. 候选 / candidate wording applies to anything drawn from it.

- Requests spent: 33 of PROBE_CAP (300).
- Interfaces reported: 5.
- Cumulative budget: 58 of PROBE_CAP (300) requests spent.
- `anns_d`: stopped: 40203 -- "抱歉，您没有接口(anns_d)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。"

- Full-day paging: a `full_day` answer with `has_more=true`, or 6000 rows and
  no flag, buys one `offset` follow-up request from the same cap. The row limit is
  the vendor's documented default, to CONFIRM at the gated live step L1 -- not verified here.
- Truncation: `no` = complete; `possibly` = a page at the row limit with no flag and no
  follow-up sent; `yes` = the endpoint's flag said more rows exist (or a follow-up page
  failed) and they were not fetched. `rows` is then a lower bound, not the daily total.
  An error code beside a permitted interface is a follow-up page's refusal.
- Rejection msg: the vendor's own message, metadata only -- it is what tells a `40203`
  rate limit from a missing permission. Truncated to 200 characters, folded to one line,
  and token-redacted; a transport failure has none.

| interface | permitted | error code | rows | pages | truncated | earliest date seen | timestamp fields | msg |
|---|---|---|---|---|---|---|---|---|
| `moneyflow` | yes | - | 5580 | 1 | no | 2021-11-12 | `trade_date` | - |
| `limit_list_d` | yes | - | 162 | 1 | no | 2021-11-12 | `trade_date`, `first_time`, `last_time`, `open_times`, `limit_times` | - |
| `top_list` | yes | - | 122 | 1 | no | 2021-11-12 | `trade_date` | - |
| `top_inst` | yes | - | 1220 | 1 | no | 2021-11-12 | `trade_date` | - |
| `anns_d` | no | 40203 | 0 | 0 | no | - | - | 抱歉，您没有接口(anns_d)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。 |

## Field names

- `moneyflow` (20 field(s)): `ts_code`, `trade_date`, `buy_sm_vol`, `buy_sm_amount`, `sell_sm_vol`, `sell_sm_amount`, `buy_md_vol`, `buy_md_amount`, `sell_md_vol`, `sell_md_amount`, `buy_lg_vol`, `buy_lg_amount`, `sell_lg_vol`, `sell_lg_amount`, `buy_elg_vol`, `buy_elg_amount`, `sell_elg_vol`, `sell_elg_amount`, `net_mf_vol`, `net_mf_amount`
- `limit_list_d` (18 field(s)): `trade_date`, `ts_code`, `industry`, `name`, `close`, `pct_chg`, `amount`, `limit_amount`, `float_mv`, `total_mv`, `turnover_ratio`, `fd_amount`, `first_time`, `last_time`, `open_times`, `up_stat`, `limit_times`, `limit`
- `top_list` (15 field(s)): `trade_date`, `ts_code`, `name`, `close`, `pct_change`, `turnover_rate`, `amount`, `l_sell`, `l_buy`, `l_amount`, `net_amount`, `net_rate`, `amount_rate`, `float_values`, `reason`
- `top_inst` (10 field(s)): `trade_date`, `ts_code`, `exalter`, `buy`, `buy_rate`, `sell`, `sell_rate`, `net_buy`, `side`, `reason`
- `anns_d` (0 field(s)): -

## Error codes

- `40203`: rate limit or no permission for this interface (read its msg)
```

## 4. 结论（候选）

| 问题 | 本报告里的证据 | 结论（候选） |
|---|---|---|
| 哪些接口可用、权限如何 | permitted / error code / msg | `moneyflow`、`limit_list_d`、`top_list`、`top_inst` 全部计划调用都被允许；`anns_d` 被拒，`msg` 明说是**没有该接口的访问权限**（不是限流），需要单独开通（Tushare 文档 doc_id=108）。 |
| 单日全市场请求数 | `pages` + `rows` + `truncated` | 四个可用接口的全市场单日答案都是 **1 页、未截断**：最大答案 `moneyflow` 5580 行、`top_inst` 1220 行、`limit_list_d` 162 行、`top_list` 122 行。即每个接口每个交易日 1 个请求。没有一页达到 6000 行，所以 `PAGE_ROW_LIMIT` = 6000 这个假设**仍未被现场确认**；`moneyflow` 的 5580 行离它很近，全市场股票数增长后可能需要第 2 页。 |
| 历史起点（深度） | depth 行里最早的 `earliest date seen` | 四个可用接口在计划最深的一年（2021-11-12，5 年前）都有数据，所以深度**至少 5 年**；这是下界，更早的起点本次没有探。 |
| 是否带发布时刻（点时间可用性） | timestamp fields | 四个接口都只有 `trade_date`；`limit_list_d` 另有 `first_time` / `last_time`（盘中首次 / 最后封板时间，是盘中事实，不是发布时刻）。**没有一个接口给出发布时刻**，所以点时间只能按"交易日收盘后某个时刻才可得"的规则保守设定，不能从数据本身读出。 |
| 积分要求 | 拒绝码 + `msg` | 四个可用接口在当前账号下可用；`anns_d` 是单独的接口权限，不是积分门槛问题。 |
