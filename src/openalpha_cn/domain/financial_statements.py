"""What a filing said, and which of its versions a reader is allowed to believe (`V2-P1-011`).

Tushare serves company fundamentals as four endpoints -- `income`, `balancesheet`, `cashflow`
and `fina_indicator`. They share a key, `(ts_code, end_date, ann_date)`, and they share this
module's whole subject: **that key does not identify a row.** A live probe on 2026-08-09 paged
every one of them to exhaustion for a 53-security sample spread across the listed universe
(every 104th code of `stock_basic`'s 5,539, plus six long-history names), and found:

| endpoint | rows | keys | keys with >1 row | of those, byte-identical | genuinely different |
|---|---:|---:|---:|---:|---:|
| `income` | 3,836 | 3,201 | 633 | 372 | 259 |
| `balancesheet` | 4,416 | 3,170 | 1,244 | 1,166 | 76 |
| `cashflow` | 3,567 | 2,849 | 718 | 250 | 468 |
| `fina_indicator` | 5,941 | 3,270 | **2,671** | 2,194 | 477 |

Every number in this module comes from that probe.

## The dimension these datasets cannot see

Three of the four carry `update_flag`, a `0`/`1` flag Tushare documents as "corrected". It says
a correction exists. It does **not** say when the correction happened: both rows of a pair carry
the **same `ann_date`**, and usually the same `f_ann_date` too (they differ on 5 of `income`'s
633 duplicate keys and 5 of `balancesheet`'s 1,244). So the instant at which the corrected
numbers became knowable is not in the data, and `providers/tushare.py`'s `_announcement_timeline`
gives the two rows **byte-equal** four-clock timelines -- deliberately, and not as an oversight.
Inventing an instant for the correction is Task 32's error in a new dataset: the dataset cannot
see the dimension, so the answer is to say so, not to fill it in.

`fina_indicator` is worse and is the reason this issue exists. It has **no `update_flag` column
at all**, and no `f_ann_date`, and no `report_type`: its 108 fields are `ts_code`, `ann_date`,
`end_date` and 105 indicators. **81.7% of its keys carry more than one row** and nothing in the
response distinguishes them. The two versions of `603049.SH`'s 2024 annual report -- both
`ann_date=20250515` -- disagree on `bps` (22.2055 against 2.2206, a factor of ten), on `roe`
(23.9249 against 176.0751) and on `grossprofit_margin` (19.4836 against **-706.4726**).

## What this module does about it, and what it refuses to do

**It collapses what is provably one fact and refuses what is provably two.** Two rows of a key
whose stored values are all equal are one fact stated twice, and `build_statement_history`
collapses them -- that is not choosing a version, it is noticing there is only one answer. It
removes 2,194 of `fina_indicator`'s 2,671 duplicate keys and 1,166 of `balancesheet`'s 1,244.
What is left disagrees, and there `ReportFiling.value_of` **raises** rather than picking.

**The refusal is per field, not per filing.** A `cashflow` pair that disagrees only on
`free_cashflow` still answers `n_cashflow_act`, because on that field the two versions say the
same thing and a refusal would be a loss with no cause. This is the whole reason the ambiguity
is carried into the read rather than resolved at write time: at write time the only available
move is to pick a row, and picking is exactly what no column here supports.

**Nothing ranks `update_flag`.** The obvious rule -- "take the row with the higher flag" -- was
tested against the sample and does not hold three ways over. On the pairs that actually
disagree, the `1` row carries more non-null values than the `0` row 156 times on `income`, 31 on
`balancesheet` and 284 on `cashflow`, but the `0` row carries more 10, 3 and 23 times, and they
tie 90, 42 and 161 times -- so a majority rule is silently wrong 36 times in a 53-name sample,
on `revenue` and on `n_income_attr_p`. Response order is no tiebreak either: `000001.SZ`'s
`balancesheet` pair at `end_date=20260331` arrives `0` then `1` and the pair at `20251231`
arrives `1` then `0`. And on **3 of `income`'s 633 duplicate keys the two rows carry the same
flag** -- `600739.SH`'s 2024 annual is two rows both labelled `1`, disagreeing about revenue --
so on those there is nothing to rank at all. None of it could be applied to `fina_indicator`
anyway, which is where 81.7% of the duplication is.

## How wrong the wrong pick would be

Measured on the pairs that disagree, worst case per field, over the 53-security sample:

- `income.ebit`: `603333.SH` 2020Q1, **-7,579,086 against +3,427,524** -- opposite signs.
  `601398.SH` 2011H1: 159.7bn against 58.5bn. 256 of the 259 differing pairs move `ebit`.
- `income.revenue` / `total_revenue`: `600739.SH` 2024 annual, 10.77bn against 11.29bn (4.6%).
- `income.n_income_attr_p`: `920454.BJ` 2018 annual, 15,245,326.91 against 14,778,490.42 (3.1%).
- `cashflow.free_cashflow`: `300002.SZ` 2023H1, **+316,026,934 against -294,173,456**.
- `balancesheet.total_share`: `000002.SZ` 2023H1, 11,630,709,471 against 11,930,709,471 (2.5%)
  -- a 2.5% error on every per-share number computed from it.
- `fina_indicator.fcff`: `600519.SH` 2018Q1, **+843,920,834 against -966,053,502**.
- `fina_indicator.roe` / `bps` / `netprofit_margin`: the `603049.SH` row above.

So this is not a residue in note-level fields. It reaches the numerator of an earnings yield,
the denominator of a book-to-price and the sign of a cash flow.

## What the refusal costs, measured end to end

Not modelled. The 53-security corpus above was driven through `TushareProvider.fetch_panel`,
`panel_ingest.write_financial_statements` into a real `PanelStore`, back out through
`load_statement_histories`, and then every `(filing, projected column)` pair a caller could ask
for was actually read:

| dataset | stored rows | filings | collapsed | ambiguous filings | field reads | refused |
|---|---:|---:|---:|---:|---:|---:|
| `income` | 3,836 | 3,201 | 372 | 261 (8.15%) | 32,010 | 288 (**0.90%**) |
| `balancesheet` | 4,416 | 3,170 | 1,205 | 41 (1.29%) | 22,190 | 43 (**0.19%**) |
| `cashflow` | 3,567 | 2,849 | 268 | 450 (15.80%) | 14,245 | 450 (**3.16%**) |
| `fina_indicator` | 5,941 | 3,270 | 2,223 | 448 (13.70%) | 35,970 | 507 (**1.41%**) |

**1,288 refusals out of 104,415 field reads, 1.23%**, and they are concentrated rather than
spread: `ebit` is 258 of `income`'s 288, `free_cashflow` is all 450 of `cashflow`'s, `fcff` is
441 of `fina_indicator`'s 507 and `total_share` is 34 of `balancesheet`'s 43. A factor that
reads `revenue`, `total_assets` or `roe` loses 5, 0 and 5 reads respectively out of ~3,200
filings each. The per-field refusal is what makes that true: refusing the whole filing instead
would have cost 1,200 filings rather than 1,288 field reads.

The same run measured the two revision facets on the real corpus.
`PartitionCoverage.revised_row_count` -- the clock-derived count -- reads 55 / 59 / 23 / 0
across the four datasets, and those are exactly the rows where `f_ann_date` genuinely post-dates
`ann_date`. It sees **none** of the same-day corrections, which is the whole reason
`PartitionCoverage.revisions` exists beside it: the `update_flag` census over the same partitions
is 2,397 `0` and 1,439 `1` rows for `income`, 2,907 / 1,509 for `balancesheet` and 2,120 / 1,447
for `cashflow`, and empty for `fina_indicator`, which has no label.

The fetch cost is measured the same way: assembling that corpus took 847 / 859 / 731 / 1,028
(security, year) requests for 53 securities, so a whole-market backfill of all four endpoints at
`stock_basic`'s 5,539 listed codes is on the order of **3.6e5 requests**. That is the price of a
100-row cap with no cross-section, and it is a fact about the endpoint rather than about this
design.

## The clocks, and the one that is a bound rather than a fact

`available_time` is `ann_date` at midnight Asia/Shanghai -- `providers/tushare.py`'s
`_announcement_timeline`, which every dataset here shares. Two things about it are stated rather
than assumed:

- **`ann_date` carries no time of day.** A-share periodic reports are disclosed outside trading
  hours, so a report dated `D` was readable either before `D`'s open or after `D`'s close, and
  the response says which for none of them. Midnight is the earlier of the two, so a same-day
  read can be up to one session early. See
  `KNOWN_FINANCIAL_STATEMENT_LIMITATIONS.an_announcement_date_carries_no_time_of_day`; it is the
  convention `trade_cal`, `stock_basic`, `namechange` and `index_member_all` already use, and
  changing it for one dataset would mean two clocks.
- **`f_ann_date` is not always the later date.** The roadmap's section 7 recorded
  `f_ann_date >= ann_date` with "zero violations" from a 65-row window (3 stocks, 2022-2025).
  Over full histories it is violated **116 times in the 53-security sample** -- 49 of `income`'s
  3,836 rows, 16 of `balancesheet`'s 4,416 and 51 of `cashflow`'s 3,567. `000001.SZ`'s `income`
  alone carries `end_date=20060331` with `ann_date=20070426` and `f_ann_date=20060426`, and
  `end_date=20051231` with `ann_date=20070322` and `f_ann_date=20060401`. `Timeline` refuses a
  `revision_time` before its `available_time`, so every one of those rows **raised** under the
  pre-existing rule and the dataset could not be fetched at all; see `_announcement_timeline`
  for the one-word fix and
  `KNOWN_FINANCIAL_STATEMENT_LIMITATIONS.the_two_announcement_dates_are_not_ordered`.

## A restatement with its own date is a different thing, and is answerable

A period announced twice on **different** days is an ordinary point-in-time restatement: the
later announcement is invisible before its own `ann_date` and authoritative after it, and
`StatementHistory` answers it without any ambiguity. It is rare -- 3 of `income`'s 3,198 periods,
5 of `balancesheet`'s 3,165, 2 of `cashflow`'s 2,847 and **0** of `fina_indicator`'s 3,270 --
which is precisely why the same-day duplicate is the case that matters.

## Layering

Pure `dataclasses`, `datetime` and `types`, plus `domain.panel_batch` for the reserved subject
column name -- the placement `domain/industry_classification.py` and `domain/index_membership.py`
already have, and for the same reason.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Final

from openalpha_cn.domain.panel_batch import SUBJECT_COLUMN_NAME

INCOME_DATASET: Final[str] = "income"
BALANCE_SHEET_DATASET: Final[str] = "balancesheet"
CASH_FLOW_DATASET: Final[str] = "cashflow"
FINANCIAL_INDICATOR_DATASET: Final[str] = "fina_indicator"

FINANCIAL_STATEMENT_DATASETS: Final[tuple[str, ...]] = (
    INCOME_DATASET,
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
)
"""The four panel datasets (and partition directories) this contract reads back.

Declared here rather than in `providers/tushare.py` for `INDEX_WEIGHT_DATASET`'s reason:
`panel_ingest` reads the rows back and is pinned to importing `domain` and `panel` only.
"""

REPORT_PERIOD_COLUMN: Final[str] = "report_period"
"""What the response calls `end_date`, renamed on the way into the panel.

Renamed because `end_date` is already spoken for and means something else two datasets over:
`namechange.end_date` is the last day a name was in force and `index_member_all`'s equivalent is
stored as `industry_through`. Here it is a *fiscal period*, not the end of an interval, and a
panel that used one word for both would invite a join that silently means nothing.
"""

ANNOUNCEMENT_DATE_COLUMN: Final[str] = "ann_date"
"""The disclosure date, kept under the response's own name because it means what it says."""

FIRST_ANNOUNCEMENT_COLUMN: Final[str] = "f_ann_date"
"""Tushare's 实际公告日期. Present on three endpoints, absent from `fina_indicator`, and **not**
guaranteed to be at or after `ann_date` -- see this module's docstring."""

REVISION_LABEL_COLUMN: Final[str] = "update_flag"
"""The version discriminator, stored under the response's own name.

This is the column `panel_coverage(revision_field=...)` was built to census (see
`panel/catalog.py`'s `RevisionCoverage`), and storing it under any other name would make that
wiring a translation. It is a string, not an integer: `0`/`1` today, and the catalog's label is
deliberately opaque.
"""

INCOME_DATA_COLUMNS: Final[tuple[str, ...]] = (
    "total_revenue",
    "revenue",
    "oper_cost",
    "operate_profit",
    "total_profit",
    "income_tax",
    "n_income",
    "n_income_attr_p",
    "basic_eps",
    "ebit",
)
"""Ten of `income`'s 85 columns, chosen rather than mirrored.

Nine of them are the inputs a P3 value or quality factor needs: the two revenue lines, cost,
operating and pre-tax profit, tax, net profit at both the consolidated and the attributable
level, and published basic EPS.

The tenth, `ebit`, is here **because** it is the field the duplicate versions disagree on most
-- 256 of `income`'s 259 differing pairs -- and dropping it would make this dataset look clean
by projecting the disagreement away. It is also a real factor input (EV/EBIT). The general rule
this follows: a column is not excluded for being inconvenient, only for being unused.

The other 75 columns are fetched by no request here. `balancesheet`'s 152 are the reason the
projection exists at all (roadmap section 6), and a contract that pinned all of them would be
152 chances to pin one wrongly.
"""

BALANCE_SHEET_DATA_COLUMNS: Final[tuple[str, ...]] = (
    "total_assets",
    "total_liab",
    "total_hldr_eqy_exc_min_int",
    "total_cur_assets",
    "total_cur_liab",
    "money_cap",
    "total_share",
)
"""Seven of `balancesheet`'s 152 columns: the leverage, liquidity and book-value inputs.

`total_share` earns its place twice -- it is the denominator of every per-share number a factor
computes, and it is this endpoint's most-disagreed field (34 of the 76 differing pairs, worst
case `000002.SZ` 2023H1 at 11,630,709,471 against 11,930,709,471).
"""

CASH_FLOW_DATA_COLUMNS: Final[tuple[str, ...]] = (
    "n_cashflow_act",
    "n_cashflow_inv_act",
    "n_cash_flows_fnc_act",
    "c_fr_sale_sg",
    "free_cashflow",
)
"""Five of `cashflow`'s 97: the three activity nets, cash collected from sales, and free cash
flow -- which, like `income.ebit`, is both a real input and this endpoint's dominant
disagreement (450 of 468 differing pairs, worst case a sign flip)."""

FINANCIAL_INDICATOR_DATA_COLUMNS: Final[tuple[str, ...]] = (
    "eps",
    "bps",
    "roe",
    "roa",
    "netprofit_margin",
    "grossprofit_margin",
    "debt_to_assets",
    "or_yoy",
    "netprofit_yoy",
    "ocfps",
    "fcff",
)
"""Eleven of `fina_indicator`'s 108, on the same rule: ten factor inputs plus `fcff`, which
carries 441 of the 477 differing pairs and would otherwise hide most of this dataset's
ambiguity behind a projection."""

STATEMENT_DATA_COLUMNS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        INCOME_DATASET: INCOME_DATA_COLUMNS,
        BALANCE_SHEET_DATASET: BALANCE_SHEET_DATA_COLUMNS,
        CASH_FLOW_DATASET: CASH_FLOW_DATA_COLUMNS,
        FINANCIAL_INDICATOR_DATASET: FINANCIAL_INDICATOR_DATA_COLUMNS,
    }
)
"""Each dataset's projected value columns, keyed by dataset name."""

STATEMENT_KEY_COLUMNS: Final[tuple[str, ...]] = (REPORT_PERIOD_COLUMN, ANNOUNCEMENT_DATE_COLUMN)
"""The two date columns every one of the four endpoints carries."""

DATASETS_WITH_REVISION_LABEL: Final[tuple[str, ...]] = (
    INCOME_DATASET,
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
)
"""The three endpoints that carry `update_flag` and `f_ann_date`.

`fina_indicator` is the fourth endpoint and carries neither, which is stated as data here so a
reader can branch on it instead of on a dataset name -- and so the absence is a declared fact
rather than a `KeyError` at ingest time.
"""


def statement_panel_columns(dataset: str) -> tuple[str, ...]:
    """The stored column order for `dataset`, keys first and values in declaration order.

    Refuses an unknown dataset rather than answering an empty tuple: "not a financial statement
    dataset" and "a financial statement dataset with no columns" are different facts, and the
    second one does not exist.
    """
    values = _require_known_dataset(dataset)
    if dataset in DATASETS_WITH_REVISION_LABEL:
        return (
            REPORT_PERIOD_COLUMN,
            ANNOUNCEMENT_DATE_COLUMN,
            FIRST_ANNOUNCEMENT_COLUMN,
            REVISION_LABEL_COLUMN,
            *values,
        )
    return (REPORT_PERIOD_COLUMN, ANNOUNCEMENT_DATE_COLUMN, *values)


class FinancialStatementError(ValueError):
    """Raised for any malformed statement history, or malformed query against one.

    A `ValueError` subclass to match `IndexMembershipError` and `IndustryClassificationError`.
    """


class FinancialStatementHorizonError(FinancialStatementError):
    """Raised when a day's answer lies outside what the read actually covers.

    Separate from its parent for `IndustryHorizonError`'s reason: the question was well formed
    and the data simply does not reach that day -- the security had announced nothing yet.
    `V2-P1-013`'s gate blocks on it; a malformed query is a bug.
    """


class AmbiguousReportError(FinancialStatementError):
    """Raised when the versions of one filing disagree about the field being read.

    Separate from its parent because it is not a defect in the query or in the read: the
    upstream dataset genuinely serves two answers under one key and carries no column that
    orders them. A caller that wants to see both asks `ReportFiling.values_of`.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class FinancialStatementLimitation:
    """One named boundary on what a stored financial statement can be trusted to answer."""

    code: str
    detail: str


KNOWN_FINANCIAL_STATEMENT_LIMITATIONS: Final[tuple[FinancialStatementLimitation, ...]] = (
    FinancialStatementLimitation(
        code="a_correction_carries_no_instant_of_its_own",
        detail=(
            "income, balancesheet and cashflow mark a corrected filing with update_flag=1 and "
            "serve it alongside the update_flag=0 original. Both rows carry the SAME ann_date, "
            "and the same f_ann_date on all but 5 of income's 633 and 5 of balancesheet's 1,244 "
            "duplicate keys, so the instant the correction became knowable is not in the "
            "dataset. providers/tushare.py's _announcement_timeline therefore gives the two "
            "rows byte-equal four-clock timelines and this module keeps both versions rather "
            "than dating one later. Anything else would be inventing the instant. The "
            "consequence is that a point-in-time read cannot ask 'what did this look like "
            "before the correction' -- see V2-P2-002, which owns that question -- and that "
            "PartitionCoverage.revised_row_count reads 0 on exactly this data, which is why "
            "PartitionCoverage.revisions (the update_flag label census) exists beside it."
        ),
    ),
    FinancialStatementLimitation(
        code="fina_indicator_has_no_version_column_at_all",
        detail=(
            "fina_indicator carries ts_code, ann_date, end_date and 105 indicators -- no "
            "update_flag, no f_ann_date, no report_type. Measured over a 53-security sample "
            "paged to exhaustion on 2026-08-09: 5,941 rows over 3,270 (security, period, "
            "announcement) keys, of which 2,671 (81.7%) carry more than one row. 2,194 of those "
            "are byte-identical across all 108 fields and collapse; the remaining 477 disagree "
            "and NOTHING in the response orders them. Every one of its 3,270 periods has "
            "exactly one ann_date, so the multiplicity is not a restatement with its own date "
            "either. ReportFiling.value_of raises AmbiguousReportError on the fields that "
            "disagree rather than taking the first row, because the two versions of 603049.SH's "
            "2024 annual report give bps as 22.2055 and as 2.2206."
        ),
    ),
    FinancialStatementLimitation(
        code="the_disagreement_reaches_headline_numbers",
        detail=(
            "Worst case per field over the sample's differing pairs, all of them both-non-null: "
            "income.ebit -7,579,086 against +3,427,524 (603333.SH 2020Q1, opposite signs; "
            "601398.SH 2011H1 is 159.7bn against 58.5bn); cashflow.free_cashflow +316,026,934 "
            "against -294,173,456 (300002.SZ 2023H1); fina_indicator.fcff +843,920,834 against "
            "-966,053,502 (600519.SH 2018Q1); income.revenue 10.77bn against 11.29bn (600739.SH "
            "2024 annual, 4.6%); income.n_income_attr_p 15,245,326.91 against 14,778,490.42 "
            "(920454.BJ 2018 annual, 3.1%); balancesheet.total_share 11,630,709,471 against "
            "11,930,709,471 (000002.SZ 2023H1, 2.5%); fina_indicator.roe 23.9249 against "
            "176.0751 and grossprofit_margin 19.4836 against -706.4726 (603049.SH 2024 annual). "
            "So 'pick either version' is not a rounding decision."
        ),
    ),
    FinancialStatementLimitation(
        code="update_flag_does_not_say_which_version_is_current",
        detail=(
            "Ranking the pair by update_flag was tested against the sample and does not hold. "
            "Counting, per differing pair, which row carries more non-null values among the "
            "fields that differ: on income the update_flag=1 row wins 156 times, the 0 row 10 "
            "times and they tie 90; on balancesheet 31 / 3 / 42; on cashflow 284 / 23 / 161. "
            "Nor is the response order a tiebreak -- for 000001.SZ's balancesheet the pair at "
            "end_date=20260331 arrives 0 then 1 and the pair at 20251231 arrives 1 then 0. And "
            "on 3 of income's 633 duplicate keys BOTH rows carry update_flag=1, so there is no "
            "rank to take: 600739.SH's 2024 annual is two such rows, disagreeing about revenue "
            "by 4.6%. A rule that is right most of the time is silently wrong 36 times in a "
            "53-name sample, and it has nothing at all to say about fina_indicator, where 81.7% "
            "of the duplication is."
        ),
    ),
    FinancialStatementLimitation(
        code="an_announcement_date_carries_no_time_of_day",
        detail=(
            "available_time is ann_date at midnight Asia/Shanghai. A-share periodic reports are "
            "disclosed outside trading hours, so a report dated D was readable either before "
            "D's open or after D's close, and no column says which. Midnight is the earlier of "
            "the two, so a same-day read can be up to one session early. This is the convention "
            "trade_cal, stock_basic, namechange and index_member_all already use "
            "(ClockStrategy.calendar_static); stating a different one for this dataset alone "
            "would mean two clocks in one panel. A consumer that cannot afford the session "
            "should read as_of the session after the announcement."
        ),
    ),
    FinancialStatementLimitation(
        code="the_two_announcement_dates_are_not_ordered",
        detail=(
            "Roadmap section 7 recorded f_ann_date >= ann_date with zero violations, measured "
            "over 65 rows (3 securities, 2022-2025). Full histories violate it 116 times in the "
            "53-security sample: 49 of income's 3,836 rows, 16 of balancesheet's 4,416 and 51 "
            "of cashflow's 3,567, against 55 / 59 / 23 rows where f_ann_date is genuinely "
            "later. 000001.SZ's income carries end_date=20060331 with ann_date=20070426 and "
            "f_ann_date=20060426, and end_date=20051231 with ann_date=20070322 and "
            "f_ann_date=20060401; its balancesheet and cashflow carry the same 20051231 row. "
            "Timeline refuses a revision_time before its available_time, so every one of those "
            "rows raised under the rule that read revision_time straight off f_ann_date -- the "
            "dataset could not be fetched. _announcement_timeline now takes max(ann_date, "
            "f_ann_date), which is unchanged wherever the old assumption held. Neither column "
            "was ever null: 0 of the 11,819 statement rows sampled."
        ),
    ),
    FinancialStatementLimitation(
        code="the_corpus_starts_before_the_exchanges_did",
        detail=(
            "The earliest report period in the 53-security sample is 1989-12-31 and its "
            "announcement is 1990-03-21 -- 000001.SZ's balancesheet and fina_indicator, nine "
            "months before the Shanghai exchange opened on 1990-12-19. income starts at "
            "1993-12-31 (announced 1995-03-10) and cashflow only at 2003-12-31 (announced "
            "2006-02-27), because the cash flow statement is a later accounting requirement, so "
            "the three statements do not cover the same years for the same security and a "
            "reader must not treat a missing cashflow filing as a missing disclosure. A "
            "backfill loop whose year range starts at 1990 silently drops the 1989 rows: that "
            "is measured, not hypothetical -- a 1990..2026 window sweep of 000001.SZ's "
            "fina_indicator returned 154 distinct rows against the 155 offset-paging returns, "
            "and the missing one is end_date=19891231."
        ),
    ),
    FinancialStatementLimitation(
        code="only_the_consolidated_report_is_served",
        detail=(
            "Every row the three statement endpoints return for a bare request carries "
            "report_type=1 (合并报表) -- 127 of 127 for 000001.SZ's income, 162 of 162 for its "
            "balancesheet, 90 of 90 for its cashflow. The parent-company and adjusted-quarter "
            "report types exist behind an explicit report_type parameter and are not fetched, "
            "so nothing stored here is a parent-company figure and a consumer must not read it "
            "as one. comp_type likewise rides along at the company's own type (2, 银行, for "
            "000001.SZ), which is why a bank's rows populate a different subset of the 85/152/97 "
            "columns than an industrial's -- the projection here holds only columns every "
            "company type publishes."
        ),
    ),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportRow:
    """One projected response row, before any collapsing.

    `values` holds only the dataset's projected value columns; a missing upstream number is
    `None` and is *not* the same as a disagreement, which is why `value_of` can return `None`
    while `values_of` still shows what each version said.
    """

    period: date
    announced_on: date
    values: Mapping[str, float | None]
    first_announced_on: date | None = None
    revision_label: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportVersion:
    """One distinct answer under a filing's key, and every label that carried it.

    `labels` is a tuple rather than a single value because collapsing is by *values*: when the
    `update_flag=0` and `update_flag=1` rows say exactly the same thing -- 1,166 of
    `balancesheet`'s 1,244 duplicate keys -- they become one version carrying `('0', '1')`. The
    labels are kept rather than discarded so `PartitionCoverage.revisions` and this contract
    agree about what was stored.
    """

    values: Mapping[str, float | None]
    labels: tuple[str, ...] = ()
    first_announced_on: date | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportFiling:
    """Everything one `(security, period, announcement)` key says, versions and all.

    Build it through `build_statement_history`; this constructor is not a boundary. `versions`
    holds at least one entry and never two entries with equal `values` and equal
    `first_announced_on`.
    """

    security: str
    period: date
    announced_on: date
    versions: tuple[ReportVersion, ...]
    row_count: int
    """How many rows the endpoint served under this key, before collapsing.

    Kept rather than recomputed from `versions` because `update_flag` cannot carry it:
    `fina_indicator` has no label column at all, so its 2,194 collapses would otherwise be
    invisible, and `600739.SH`'s 2024 annual `income` filing arrives as two rows that **both**
    carry `update_flag='1'`.
    """

    @property
    def collapsed_versions(self) -> int:
        """Rows that folded away because they said exactly the same thing."""
        return self.row_count - len(self.versions)

    @property
    def is_ambiguous(self) -> bool:
        """Whether more than one distinct version survived collapsing."""
        return len(self.versions) > 1

    @property
    def disagreeing_fields(self) -> tuple[str, ...]:
        """The projected value columns the surviving versions do not agree on, sorted.

        Empty when the filing is unambiguous, and also empty when the surviving versions differ
        only in `first_announced_on` -- which is a real disagreement about the clock and not
        about any number, and is reported by `announcement_is_ambiguous` instead.
        """
        if not self.is_ambiguous:
            return ()
        first = self.versions[0].values
        return tuple(
            sorted(
                name
                for name in first
                if any(version.values.get(name) != first[name] for version in self.versions[1:])
            )
        )

    @property
    def announcement_is_ambiguous(self) -> bool:
        """Whether the surviving versions disagree about `f_ann_date`."""
        return len({version.first_announced_on for version in self.versions}) > 1

    def values_of(self, field: str) -> tuple[float | None, ...]:
        """Every surviving version's value for `field`, in stored order.

        The escape hatch from `value_of`'s refusal, and deliberately plural: a caller that wants
        the disagreement has to hold both numbers, not receive one of them.
        """
        self._require_field(field)
        return tuple(version.values.get(field) for version in self.versions)

    def value_of(self, field: str) -> float | None:
        """The one value every surviving version gives for `field`.

        Raises `AmbiguousReportError` when they disagree. Returning either one would be a coin
        toss the caller could not see, and the coin lands on `roe = 23.9249` against
        `roe = 176.0751` (`603049.SH`, 2024 annual). `None` is a real answer -- the upstream
        cell was empty on every version -- and is not a refusal.
        """
        values = self.values_of(field)
        first = values[0]
        if any(other != first for other in values[1:]):
            raise AmbiguousReportError(
                f"{self.security} {self.period.isoformat()} announced "
                f"{self.announced_on.isoformat()}: {len(self.versions)} versions disagree about "
                f"{field!r} ({', '.join(repr(value) for value in values)}) and the dataset "
                "carries no column that orders them"
            )
        return first

    def _require_field(self, field: str) -> None:
        if field not in self.versions[0].values:
            raise FinancialStatementError(
                f"{field!r} is not a stored column of this filing; stored columns are "
                f"{sorted(self.versions[0].values)}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class StatementHistory:
    """One security's filings for one dataset, ordered and answerable by day.

    Build it with `build_statement_history`; this constructor is not a boundary and validates
    nothing. `filings` is ascending by `(announced_on, period)` and holds at least one entry.
    """

    security: str
    dataset: str
    filings: tuple[ReportFiling, ...]

    @property
    def covered_from(self) -> date:
        """The first day this history can answer for -- its earliest announcement."""
        return self.filings[0].announced_on

    def filings_on(self, day: date) -> tuple[ReportFiling, ...]:
        """Every filing announced on or before `day`, in stored order.

        The plain point-in-time filter, and the thing every other reader here is built from.
        """
        _require_plain_date(day, "day")
        return tuple(filing for filing in self.filings if filing.announced_on <= day)

    def periods_on(self, day: date) -> tuple[date, ...]:
        """The distinct fiscal periods that had been announced by `day`, ascending."""
        return tuple(sorted({filing.period for filing in self.filings_on(day)}))

    def latest_filing_on(self, day: date) -> ReportFiling:
        """The most recent period announced on or before `day`.

        Ties on period -- a period announced twice on different days, which is a real
        restatement and happens on 3 of `income`'s 3,198 periods -- resolve to the later
        announcement, because that is the one a reader standing on `day` would have.

        Raises `FinancialStatementHorizonError` when the security had announced nothing yet,
        rather than answering with the first filing it ever made.
        """
        visible = self.filings_on(day)
        if not visible:
            raise FinancialStatementHorizonError(
                f"{self.security} had announced no {self.dataset} filing by "
                f"{day.isoformat()}; its first is {self.covered_from.isoformat()}"
            )
        return max(visible, key=lambda filing: (filing.period, filing.announced_on))

    def filing_for(self, period: date, day: date) -> ReportFiling:
        """The version of `period` a reader standing on `day` would have.

        Raises `FinancialStatementHorizonError` when that period had not been announced by then
        -- which is the look-ahead this whole module exists to make loud.
        """
        _require_plain_date(period, "period")
        candidates = [filing for filing in self.filings_on(day) if filing.period == period]
        if not candidates:
            raise FinancialStatementHorizonError(
                f"{self.security} had not announced its {period.isoformat()} {self.dataset} "
                f"by {day.isoformat()}"
            )
        return max(candidates, key=lambda filing: filing.announced_on)


def build_statement_history(
    *, security: str, dataset: str, rows: Iterable[ReportRow]
) -> StatementHistory:
    """Group `rows` into filings, collapse the redundant versions, and refuse the malformed.

    The collapsing rule is the whole point and is stated here rather than buried: two rows of
    one `(period, announcement)` key that agree on **every projected value and on
    `f_ann_date`** are one fact stated twice and become one version carrying both labels. Two
    that do not agree stay two, and every read of a field they disagree on raises.

    Refuses an empty row set, a dataset name that is not one of the four, and a row whose
    `values` do not carry exactly the dataset's projected columns -- the last because a filing
    silently missing a column would answer `None` for it, which is indistinguishable from an
    empty upstream cell.
    """
    _require_text(security, "security")
    expected = frozenset(_require_known_dataset(dataset))
    grouped: dict[tuple[date, date], list[ReportRow]] = {}
    for row in rows:
        _require_plain_date(row.period, "period")
        _require_plain_date(row.announced_on, "announced_on")
        if frozenset(row.values) != expected:
            raise FinancialStatementError(
                f"{security} {row.period.isoformat()}: {dataset} rows must carry exactly the "
                f"columns {sorted(expected)}, got {sorted(row.values)}"
            )
        grouped.setdefault((row.announced_on, row.period), []).append(row)
    if not grouped:
        raise FinancialStatementError(f"{security}: a {dataset} history needs at least one row")
    filings = tuple(
        _collapse(security=security, announced_on=announced_on, period=period, rows=group)
        for (announced_on, period), group in sorted(grouped.items())
    )
    return StatementHistory(security=security, dataset=dataset, filings=filings)


def _collapse(
    *, security: str, announced_on: date, period: date, rows: Sequence[ReportRow]
) -> ReportFiling:
    """One key's rows, with the byte-identical ones folded together and their labels kept."""
    versions: list[ReportVersion] = []
    labels: list[list[str]] = []
    for row in rows:
        for index, version in enumerate(versions):
            if (
                version.values == row.values
                and version.first_announced_on == row.first_announced_on
            ):
                if row.revision_label is not None and row.revision_label not in labels[index]:
                    labels[index].append(row.revision_label)
                break
        else:
            versions.append(
                ReportVersion(
                    values=MappingProxyType(dict(row.values)),
                    first_announced_on=row.first_announced_on,
                )
            )
            labels.append([row.revision_label] if row.revision_label is not None else [])
    return ReportFiling(
        security=security,
        period=period,
        announced_on=announced_on,
        row_count=len(rows),
        versions=tuple(
            ReportVersion(
                values=version.values,
                labels=tuple(sorted(label_group)),
                first_announced_on=version.first_announced_on,
            )
            for version, label_group in zip(versions, labels, strict=True)
        ),
    )


def statement_histories_from_panel_rows(
    *,
    dataset: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> Mapping[str, StatementHistory]:
    """Rebuild one history per security from stored panel rows.

    `columns` names the row tuples' fields, which must include the subject column and every
    column `statement_panel_columns(dataset)` declares. Extra columns are ignored rather than
    refused, so a partition that gained a column stays readable.
    """
    required = (SUBJECT_COLUMN_NAME, *statement_panel_columns(dataset))
    index = {name: position for position, name in enumerate(columns)}
    missing = [name for name in required if name not in index]
    if missing:
        raise FinancialStatementError(
            f"{dataset} panel rows are missing column(s) {missing}; got {list(columns)}"
        )
    values_columns = STATEMENT_DATA_COLUMNS[dataset]
    has_label = dataset in DATASETS_WITH_REVISION_LABEL
    collected: dict[str, list[ReportRow]] = {}
    for position, row in enumerate(rows):
        security = _require_stored_text(row[index[SUBJECT_COLUMN_NAME]], position, "subject")
        collected.setdefault(security, []).append(
            ReportRow(
                period=_parse_iso_date(
                    row[index[REPORT_PERIOD_COLUMN]], position, REPORT_PERIOD_COLUMN
                ),
                announced_on=_parse_iso_date(
                    row[index[ANNOUNCEMENT_DATE_COLUMN]], position, ANNOUNCEMENT_DATE_COLUMN
                ),
                first_announced_on=(
                    _parse_optional_iso_date(
                        row[index[FIRST_ANNOUNCEMENT_COLUMN]], position, FIRST_ANNOUNCEMENT_COLUMN
                    )
                    if has_label
                    else None
                ),
                revision_label=(
                    _require_stored_text(
                        row[index[REVISION_LABEL_COLUMN]], position, REVISION_LABEL_COLUMN
                    )
                    if has_label
                    else None
                ),
                values=MappingProxyType(
                    {
                        name: _optional_float(row[index[name]], position, name)
                        for name in values_columns
                    }
                ),
            )
        )
    return MappingProxyType(
        {
            security: build_statement_history(
                security=security, dataset=dataset, rows=security_rows
            )
            for security, security_rows in sorted(collected.items())
        }
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class StatementAmbiguityReport:
    """How much of a stored dataset a reader cannot be given a single answer for.

    Four counts rather than one ratio, because they answer four different questions and only
    the third is a cost a caller pays:

    - `filings` / `securities`: the denominator, so a percentage can be recomputed rather than
      trusted.
    - `collapsed_versions`: rows that folded away because they said the same thing. Not a cost
      -- it is the part of the duplication that carried no information.
    - `ambiguous_filings`: filings where more than one distinct version survived. This is where
      a read *can* be refused.
    - `ambiguous_field_reads`: per projected column, how many filings would refuse a read of
      *that* column. The refusal is per field, so this is the only number that says what a
      given factor would actually lose, and it is what `financial_ambiguity_report` is for.
    """

    dataset: str
    securities: int
    filings: int
    collapsed_versions: int
    ambiguous_filings: int
    ambiguous_announcements: int
    ambiguous_field_reads: Mapping[str, int]

    @property
    def is_clean(self) -> bool:
        """Whether every filing has exactly one surviving version."""
        return self.ambiguous_filings == 0


def financial_ambiguity_report(
    *, dataset: str, histories: Mapping[str, StatementHistory]
) -> StatementAmbiguityReport:
    """Count what the stored corpus cannot answer, by filing and by field.

    Runs over the same objects a reader uses, so the number it reports is the number a reader
    pays -- not a model of one. `V2-P1-012`'s health report is the intended consumer.
    """
    columns = _require_known_dataset(dataset)
    per_field = dict.fromkeys(columns, 0)
    filings = 0
    collapsed = 0
    ambiguous = 0
    ambiguous_announcements = 0
    for history in histories.values():
        if history.dataset != dataset:
            raise FinancialStatementError(
                f"history for {history.security} is {history.dataset!r}, not {dataset!r}"
            )
        for filing in history.filings:
            filings += 1
            collapsed += filing.collapsed_versions
            if filing.is_ambiguous:
                ambiguous += 1
                for name in filing.disagreeing_fields:
                    per_field[name] += 1
            if filing.announcement_is_ambiguous:
                ambiguous_announcements += 1
    return StatementAmbiguityReport(
        dataset=dataset,
        securities=len(histories),
        filings=filings,
        collapsed_versions=collapsed,
        ambiguous_filings=ambiguous,
        ambiguous_announcements=ambiguous_announcements,
        ambiguous_field_reads=MappingProxyType(per_field),
    )


def _require_known_dataset(dataset: str) -> tuple[str, ...]:
    columns = STATEMENT_DATA_COLUMNS.get(dataset)
    if columns is None:
        raise FinancialStatementError(
            f"{dataset!r} is not one of the financial-statement datasets "
            f"{list(FINANCIAL_STATEMENT_DATASETS)}"
        )
    return columns


def _require_text(value: str, role: str) -> None:
    if not value or not value.strip():
        raise FinancialStatementError(f"{role} must be a non-empty string")


def _require_plain_date(value: date, role: str) -> None:
    """Refuse a `datetime` where a `date` is required.

    `datetime` is a `date` subclass, so an aware instant would compare against stored days by
    its own rules and silently shift the answer across a timezone. `domain/index_membership.py`
    refuses it for the same reason.
    """
    if not isinstance(value, date) or type(value) is not date:
        raise FinancialStatementError(f"{role} must be a datetime.date, got {type(value)!r}")


def _require_stored_text(value: object, index: int, column: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FinancialStatementError(
            f"row {index} column {column!r} must be a non-empty string, got {value!r}"
        )
    return value


def _parse_iso_date(value: object, index: int, column: str) -> date:
    text = _require_stored_text(value, index, column)
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise FinancialStatementError(
            f"row {index} column {column!r} is not an ISO date: {text!r}"
        ) from error


def _parse_optional_iso_date(value: object, index: int, column: str) -> date | None:
    if value is None:
        return None
    return _parse_iso_date(value, index, column)


def _optional_float(value: object, index: int, column: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FinancialStatementError(
            f"row {index} column {column!r} must be a number or None, got {value!r}"
        )
    return float(value)
