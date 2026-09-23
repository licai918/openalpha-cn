"""The value family against real partitions on disk (`V2-P3-009`, `V2-P3-017`).

`tests/unit/test_factor_value_family.py` measures the four evaluators as functions of a window.
This file measures what a function cannot answer, and every one of the five things it says is
something no factor shipped before this one could have said:

## A factor on two axes, through the whole engine

`earnings_yield_ttm` reads `income.n_income_attr_p` on the report-period axis and
`daily_basic.total_mv` on the session axis, in one build, off two partitions written by the real
writers. The two series have different lengths and different indices -- five filings and one
session -- and the evaluator divides `[-1]` by `[-1]`. That machinery landed with the
report-period axis and was exercised only by definitions the tests declared; these are the
first shipped factors that use it, so `test_the_four_factors_compute_off_two_axes_and_give_four
_different_numbers` is where the claim becomes a measurement.

## `V2-P3-017`: a fourth member, on the endpoint with nothing that orders two rows

`deducted_earnings_yield_ttm` is EPcut and reads `fina_indicator.profit_dedt`, so this corpus
gained a fourth statement partition -- the first one any shipped factor binds a column of, and
the only one written here without `f_ann_date` or `update_flag`, because that endpoint carries
neither. `test_a_contradiction_in_the_deducted_profit_codes_epcut_and_leaves_ep_computed` is what
that costs, on `000488.SZ`'s 2015 annual: `income` serves the two rows the same
`n_income_attr_p` and they collapse, `fina_indicator` serves them `profit_dedt` 2.566x apart and
they do not, so one build gives EP a number and EPcut a coverage code.

## Per-field ambiguity, on two real rows

`600739.SH`'s 2024 annual arrives as **two** rows that both carry `update_flag='1'`, disagree
about `total_revenue` by 4.6% (11,289,276,631.83 against 10,769,999,495.94) and agree exactly
about `n_income_attr_p` (209,556,865.25). `domain/financial_statements.py` says a refusal is per
field; this file drives that through the engine on those numbers, and the answer is that
`earnings_yield_ttm` computes off the same partition on which `sales_yield_ttm` reports
`ambiguous_filing`.

That is only true because `V2-P3-009` changed `_read_dataset` to collapse duplicate rows whose
projected cells agree, which is `build_statement_history`'s own rule. Before it, the engine
refused on multiplicity alone -- so it refused 372 of `income`'s 633 duplicate keys, 1,166 of
`balancesheet`'s 1,244 and 2,194 of `fina_indicator`'s 2,671 that say exactly the same thing
twice, where `ReportFiling.value_of` answers every one of them.
`test_the_engine_answers_a_column_the_duplicate_rows_agree_about_and_codes_one_they_do_not`
holds both halves against the domain's own answer over one corpus.

## `V2-P3-018`: the rows that really disagree cost one security, not the build

Until `V2-P3-018` the second half of that sentence was a `FactorEngineError`, so a single
contradictory filing anywhere in a cross section produced **no observation for anybody**. Six
tests here are that change:
`..._codes_one_they_do_not` (the code instead of the raise),
`test_one_contradictory_filing_costs_that_security_and_leaves_the_cross_section_untouched` (the
acceptance: every other name's value byte-identical to a clean build),
`test_an_ambiguous_filing_outranks_a_null_cell_in_the_same_window` (the precedence),
`test_an_ambiguous_filing_outside_the_window_this_factor_forms_is_not_this_factors_problem` (the
window scope), `test_a_superseded_ambiguous_pair_still_codes_the_security_rather_than_taking_the
_later_row` (the deliberate divergence from `filing_for`, kept), and
`test_the_disagreement_code_does_not_depend_on_the_order_the_partition_returns_its_rows` (the
property that had to survive the change).

## The three coverage codes a value factor reaches differently

| security | what it is for |
|---|---|
| `000001.SZ` | the computable case, all three factors |
| `603333.SH` | a **loss**: `earnings_yield_ttm` computed and negative, `sales_yield_ttm` positive |
| `000795.SZ` | **negative book value**: `book_to_price` computed and negative |
| `600000.SH` | two filings: `insufficient_history` for the five-period pair, `computed` for BP |
| `000063.SZ` | five filings spanning **six** quarters: the span bound, not the count |
| `002594.SZ` | a stored `total_mv` of zero: `undefined_value` for all three |
| `300750.SZ` | a null book value: `input_missing` for BP alone, the other two computed |
| `600965.SH` | two income rows for one filing that **agree**: collapsed, computed |
| `601318.SH` | outside the universe |

`600000.SH` and `000063.SZ` are the pair the `V2-P3-004` review's lesson asks for: the first is
short on the count and the second is inside the count and outside the span, so `lookback_periods`
and `max_window_periods` are separable from each other on this one partition rather than merely
rendered into it.

## Four factors sharing one denominator

Every factor here divides by `daily_basic.total_mv`, which is exactly the shape in which a
fixture stops discriminating: an evaluator wired to the wrong numerator column still produces a
plausible number. So the partition carries a **different series in every column any of the four
could have read** -- `revenue` is half of `total_revenue`, `n_income` is 1.7 times
`n_income_attr_p`, `total_assets - total_liab` is 1.4 times `total_hldr_eqy_exc_min_int`,
`profit_dedt` is 0.83 times `n_income_attr_p`, and `circ_mv` is 0.6 of `total_mv` -- and the four
answers are asserted to be pairwise distinct. The last of those separations is the one this
family most needed: EP and EPcut differ in one `(dataset, column)` and nothing else, so a wiring
that pointed EPcut back at `income` would answer EP's number exactly.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATA_COLUMNS,
    DAILY_BASIC_DATASET,
)
from openalpha_cn.domain.factor import FactorDefinition
from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATA_COLUMNS,
    BALANCE_SHEET_DATASET,
    FINANCIAL_INDICATOR_DATA_COLUMNS,
    FINANCIAL_INDICATOR_DATASET,
    INCOME_DATA_COLUMNS,
    INCOME_DATASET,
    statement_histories_from_panel_rows,
    statement_panel_columns,
)
from openalpha_cn.domain.panel_batch import (
    SUBJECT_COLUMN_NAME,
    ColumnarPanelBatch,
    PanelColumn,
    TimelineColumns,
)
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    BOOK_TO_PRICE,
    CNY_PER_MARKET_CAP_UNIT,
    DEDUCTED_EARNINGS_YIELD_TTM,
    DEDUCTED_NET_PROFIT_COLUMN,
    EARNINGS_YIELD_TTM,
    NET_PROFIT_COLUMN,
    SALES_YIELD_TTM,
    TOTAL_REVENUE_COLUMN,
    TRAILING_TWELVE_MONTH_PERIODS,
    FactorEngineError,
    FactorPanel,
    compute_factor,
    load_factor_observations,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import financial_statement_requirement, write_panel_batch

SHANGHAI_OFFSET: Final[timedelta] = timedelta(hours=8)

AS_OF: Final[datetime] = datetime(2025, 5, 20, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai on 2025-05-20: a month after the newest filing, a day after the newest
session."""

BUILT_AT: Final[datetime] = datetime(2025, 6, 1, 9, 0, tzinfo=UTC)
COMMIT: Final[str] = "c3f9a17"

STATEMENT_STALENESS: Final[timedelta] = timedelta(days=120)
"""A quarter, which is how often A-share issuers disclose; this corpus's own widest gap between
`AS_OF` and a visible filing is 30 days, so the bound is a real one rather than one sized to
clear. `read_visible_at` pools the check over `requirement.years`, which is what lets the 2024
partition be read at all."""

SESSION_STALENESS: Final[timedelta] = timedelta(days=7)

STATEMENT_YEARS: Final[tuple[int, ...]] = (2024, 2025)
"""The **announcement** years these filings land in. Five report periods, two partitions."""

SESSION_YEAR: Final[int] = 2025

SESSIONS: Final[tuple[date, ...]] = (date(2025, 5, 15), date(2025, 5, 16), date(2025, 5, 19))
"""Three `daily_basic` sessions, so a reader taking `[0]` instead of `[-1]` answers differently."""

PERIODS: Final[tuple[date, ...]] = (
    date(2023, 12, 31),
    date(2024, 3, 31),
    date(2024, 6, 30),
    date(2024, 9, 30),
    date(2024, 12, 31),
    date(2025, 3, 31),
)
"""Every report period this corpus holds. The five from 2024Q1 on are one trailing-twelve-month
window; 2023-12-31 exists only so `000063.SZ` can hold five filings that span six quarters."""

ANNOUNCED_ON: Final[dict[date, date]] = {
    date(2023, 12, 31): date(2024, 3, 20),
    date(2024, 3, 31): date(2024, 4, 20),
    date(2024, 6, 30): date(2024, 8, 25),
    date(2024, 9, 30): date(2024, 10, 25),
    date(2024, 12, 31): date(2025, 4, 20),
    date(2025, 3, 31): date(2025, 4, 20),
}
"""When each period was disclosed. The annual and the following Q1 land on **one day**, which is
what an A-share issuer routinely does and what the report-period axis exists to hold."""

TRAILING_WINDOW: Final[tuple[date, ...]] = PERIODS[1:]

PROFIT_INCREMENTS: Final[tuple[float, ...]] = (11.0, 23.0, 37.0, 53.0, 71.0)
"""One quarter's profit at a time, 2024Q1 through 2025Q1. No two alike and none zero.

The partition carries the *cumulative* figures these accumulate into, which is what a stored
A-share statement holds; the expected trailing twelve months is the plain sum of the last four.
So the fixture and the expectation share only these five numbers.
"""

REVENUE_INCREMENTS: Final[tuple[float, ...]] = (101.0, 211.0, 307.0, 419.0, 521.0)
"""The same, for the top line. Deliberately not a multiple of `PROFIT_INCREMENTS`, so
`earnings_yield_ttm` and `sales_yield_ttm` cannot come out proportional on this partition."""

DEDUCTED_PROFIT_MULTIPLE: Final[float] = 0.83
"""What `fina_indicator.profit_dedt` is, per unit of `income.n_income_attr_p`, on this partition.

Below one because a deduction takes something away, and **not** any of the other separations this
file already uses (`REVENUE_SHARE` 0.5, `CIRC_MV_SHARE` 0.6, `CONSOLIDATED_PROFIT_MULTIPLE` 1.7)
nor one: EPcut and EP share a denominator, a reach, a direction and an implementation, so the
only thing that can make them two numbers on this partition is this constant, and a wiring that
read `income.n_income_attr_p` for both would land exactly on EP's answer. Real filings put the
ratio near here -- `600739.SH`'s 2024 annual is 1.045 and `600030.SH`'s 2025Q3 is 0.993 -- but
the number is chosen to separate rather than to imitate.
"""

TRAILING_PROFIT: Final[float] = math.fsum(PROFIT_INCREMENTS[1:])
TRAILING_REVENUE: Final[float] = math.fsum(REVENUE_INCREMENTS[1:])
TRAILING_DEDUCTED_PROFIT: Final[float] = math.fsum(
    value * DEDUCTED_PROFIT_MULTIPLE for value in PROFIT_INCREMENTS[1:]
)

PRIOR_YEAR_CUMULATIVE: Final[float] = 907.0
"""What a 2023-12-31 filing carries. Never inside a trailing window here -- the only security
that files it is `insufficient_history` for both flow factors -- so its value decides nothing and
is a distinct number so that a window which reached it would be visible."""

FULL: Final[str] = "000001.SZ"
LOSS: Final[str] = "603333.SH"
INSOLVENT: Final[str] = "000795.SZ"
SHORT: Final[str] = "600000.SH"
GAP: Final[str] = "000063.SZ"
ZERO_CAP: Final[str] = "002594.SZ"
NULL_BOOK: Final[str] = "300750.SZ"
COLLAPSED: Final[str] = "600965.SH"
OUTSIDE: Final[str] = "601318.SH"

SUBJECTS: Final[tuple[str, ...]] = (
    FULL,
    LOSS,
    INSOLVENT,
    SHORT,
    GAP,
    ZERO_CAP,
    NULL_BOOK,
    COLLAPSED,
    OUTSIDE,
)
UNIVERSE: Final[frozenset[str]] = frozenset(SUBJECTS) - {OUTSIDE}

PROFIT_SCALE: Final[dict[str, float]] = {name: 1.0 for name in SUBJECTS}
PROFIT_SCALE[LOSS] = -1.0
"""`603333.SH` loses money in every quarter of the window, which is the case
`daily_basic.pe_ttm` is simply null for -- 1,214 of 5,338 rows on the session
`domain/daily_prices.py` censused."""

REVENUE_SCALE: Final[dict[str, float]] = {name: 1.0 for name in SUBJECTS}
REVENUE_SCALE[LOSS] = 3.0
"""A loss-maker still sells: `sales_yield_ttm` is positive for the security whose
`earnings_yield_ttm` is negative, which is the pair that shows the two factors are not each
other's sign."""

EQUITY_BASE: Final[dict[str, float]] = {name: 4_000.0 for name in SUBJECTS}
EQUITY_BASE[INSOLVENT] = -6_000.0
EQUITY_BASE[FULL] = 5_500.0
EQUITY_BASE[SHORT] = 2_750.0

EQUITY_DRIFT: Final[float] = 130.0
"""How much the stored book value moves per period, so `book_to_price` reading anything but the
newest filing answers differently."""

MARKET_CAP_BASE: Final[dict[str, float]] = {name: 900.0 for name in SUBJECTS}
MARKET_CAP_BASE[LOSS] = 640.0
MARKET_CAP_BASE[INSOLVENT] = 1_100.0
MARKET_CAP_BASE[SHORT] = 1_450.0

MARKET_CAP_DRIFT: Final[float] = 37.0
"""How much `total_mv` moves per session. `MARKET_CAP_SESSIONS` is 1, so a reader taking the
oldest session of the partition rather than the newest is off by twice this."""

ZERO_MARKET_CAP_SESSION: Final[int] = len(SESSIONS) - 1
"""`002594.SZ` carries a stored `total_mv` of `0.0` on the session every factor here reads.

`domain/daily_prices.py::_stored_number` requires that column to be a finite float and not a
positive one -- only `DAILY_PRICE_COLUMNS`' five get the positivity check -- so this partition is
one this repository's own reader accepts, and `undefined_value` is provoked end to end rather
than declared. `AMIHUD_60`'s zero `amount` is the same argument in the other dataset.
"""

CIRC_MV_SHARE: Final[float] = 0.6
REVENUE_SHARE: Final[float] = 0.5
CONSOLIDATED_PROFIT_MULTIPLE: Final[float] = 1.7
ASSET_MULTIPLE: Final[float] = 4.0
LIABILITY_MULTIPLE: Final[float] = 2.6
"""The four separations that make a wrong column a wrong number rather than the same one.

`revenue` is half of `total_revenue`; `n_income` is 1.7 times `n_income_attr_p` (a consolidated
figure exceeds the attributable one, which is why the pair is the one `EARNINGS_YIELD_TTM`
argues about); `total_assets - total_liab` is `(4.0 - 2.6)` times `total_hldr_eqy_exc_min_int`,
so the consolidated-equity reading `BOOK_TO_PRICE` refuses lands 40% high; and `circ_mv` is 0.6
of `total_mv`, so a denominator taken from the float answers differently.
"""

# income(600739.SH, period=20241231): the two rows the endpoint really serves, both labelled
# update_flag='1'. They disagree about revenue, total_revenue and oper_cost, and agree exactly
# about n_income_attr_p. Captured 2026-08-09 and restated from
# `tests/unit/domain/test_financial_statements.py`'s `LIAONING_2024_A` / `LIAONING_2024_B`.
DUPLICATED: Final[str] = "600739.SH"
AGREED_NET_PROFIT: Final[float] = 209556865.25
DISPUTED_TOTAL_REVENUE: Final[tuple[float, float]] = (11289276631.83, 10769999495.94)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=UTC) - SHANGHAI_OFFSET


def _session_instant(day: date) -> datetime:
    """16:30 Asia/Shanghai, `DAILY_AVAILABILITY_TIME`'s shape, as UTC."""
    return datetime.combine(day, time(16, 30), tzinfo=UTC) - SHANGHAI_OFFSET


def _cumulative(increments: tuple[float, ...], period: date) -> float:
    """What a stored A-share statement carries at `period`: the fiscal year to date.

    2024's four periods accumulate `increments[:1]` through `increments[:4]`; 2025Q1 restarts at
    `increments[4]`, which is the whole reason a trailing twelve months is three terms rather than
    a difference. 2023-12-31 carries a constant, because no window here reaches it.
    """
    if period.year == 2023:
        return PRIOR_YEAR_CUMULATIVE
    if period.year == 2024:
        return math.fsum(increments[: period.month // 3])
    return increments[4]


def _income_periods(subject: str) -> tuple[date, ...]:
    """Which report periods each security filed an `income` statement for."""
    if subject == SHORT:
        return (date(2024, 12, 31), date(2025, 3, 31))
    if subject == GAP:
        # Five filings, and the December in the middle of them is missing -- so the window holds
        # the count and spans six fiscal quarters rather than five.
        return (
            date(2023, 12, 31),
            date(2024, 3, 31),
            date(2024, 6, 30),
            date(2024, 9, 30),
            date(2025, 3, 31),
        )
    return TRAILING_WINDOW


def _balance_periods(subject: str) -> tuple[date, ...]:
    """`balancesheet` filings. `book_to_price` reads one period, so only the newest decides."""
    return _income_periods(subject)


def _market_cap(subject: str, index: int) -> float:
    if subject == ZERO_CAP and index == ZERO_MARKET_CAP_SESSION:
        return 0.0
    return MARKET_CAP_BASE[subject] + MARKET_CAP_DRIFT * index


def _book_value(subject: str, period: date) -> float | None:
    if subject == NULL_BOOK and period == _balance_periods(subject)[-1]:
        return None
    return EQUITY_BASE[subject] + EQUITY_DRIFT * PERIODS.index(period)


def _statement_batch(
    dataset: str,
    rows: tuple[tuple[str, date, date, dict[str, float | None]], ...],
) -> ColumnarPanelBatch:
    """`(subject, period, announcement, values)` through one endpoint's own projection and clock.

    All four clocks are the announcement instant, which is what
    `providers/tushare.py::_announcement_timeline` does for every statement row -- and is why two
    versions of one filing cannot be told apart by any of them.

    `fina_indicator` gets neither `f_ann_date` nor `update_flag`, because that endpoint carries
    neither. That is not a detail of this fixture: it is the whole reason `V2-P3-017` describes
    EPcut's endpoint as the one with nothing that orders two rows, and writing the two columns
    here anyway would make this partition wider than `statement_panel_columns` declares -- which
    the assertion below refuses.
    """
    data_columns = {
        INCOME_DATASET: INCOME_DATA_COLUMNS,
        BALANCE_SHEET_DATASET: BALANCE_SHEET_DATA_COLUMNS,
        FINANCIAL_INDICATOR_DATASET: FINANCIAL_INDICATOR_DATA_COLUMNS,
    }[dataset]
    announced = tuple(_midnight(item[2]) for item in rows)
    labelled = (
        ()
        if dataset == FINANCIAL_INDICATOR_DATASET
        else (
            PanelColumn("f_ann_date", "string", tuple(item[2].isoformat() for item in rows)),
            PanelColumn("update_flag", "string", tuple("1" for _ in rows)),
        )
    )
    columns = (
        PanelColumn("report_period", "string", tuple(item[1].isoformat() for item in rows)),
        PanelColumn("ann_date", "string", tuple(item[2].isoformat() for item in rows)),
        *labelled,
        *(
            PanelColumn(name, "float", tuple(item[3].get(name) for item in rows))
            for name in data_columns
        ),
    )
    assert tuple(column.name for column in columns) == statement_panel_columns(dataset)
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=dataset,
        kind=dataset,
        as_of=BUILT_AT,
        fetched_at=BUILT_AT,
        status="success",
        subjects=tuple(item[0] for item in rows),
        timeline=TimelineColumns(
            event_time=announced,
            available_time=announced,
            ingested_time=tuple(max(BUILT_AT, moment) for moment in announced),
            revision_time=announced,
        ),
        columns=columns,
    )


def _income_values(subject: str, period: date) -> dict[str, float | None]:
    """One `income` row, with a **different series in every column this family could have read**.

    `revenue` is half of `total_revenue` and `n_income` is 1.7 times `n_income_attr_p`, so a
    factor pointed at either neighbour answers a different number rather than the same one. The
    remaining six carry distinct constants: they are not read here and a second moving series
    would be noise.
    """
    profit = _cumulative(PROFIT_INCREMENTS, period) * PROFIT_SCALE[subject]
    revenue = _cumulative(REVENUE_INCREMENTS, period) * REVENUE_SCALE[subject]
    return {
        "total_revenue": revenue,
        "revenue": revenue * REVENUE_SHARE,
        "oper_cost": 3.0,
        "operate_profit": 5.0,
        "total_profit": 7.0,
        "income_tax": 11.0,
        "n_income": profit * CONSOLIDATED_PROFIT_MULTIPLE,
        "n_income_attr_p": profit,
        "basic_eps": 0.13,
        "ebit": 17.0,
    }


def _balance_values(subject: str, period: date) -> dict[str, float | None]:
    """One `balancesheet` row. `total_assets - total_liab` is deliberately **not** the stored
    equity, so the consolidated-equity reading `BOOK_TO_PRICE` refuses lands on a different
    number; `total_share` is a distinct constant so a `bps`-times-shares reading would too."""
    equity = _book_value(subject, period)
    reference = EQUITY_BASE[subject] + EQUITY_DRIFT * PERIODS.index(period)
    return {
        "total_assets": reference * ASSET_MULTIPLE,
        "total_liab": reference * LIABILITY_MULTIPLE,
        "total_hldr_eqy_exc_min_int": equity,
        "total_cur_assets": 19.0,
        "total_cur_liab": 23.0,
        "money_cap": 29.0,
        "total_share": 1_234.0,
    }


def _indicator_values(subject: str, period: date) -> dict[str, float | None]:
    """One `fina_indicator` row. `profit_dedt` is the only moving column.

    The eleven beside it are distinct constants for `_income_values`' reason: none of them is read
    by any shipped factor, and a second moving series would be noise. `profit_dedt` is
    `DEDUCTED_PROFIT_MULTIPLE` times the *attributable* profit `income` carries for the same
    security and period, which is what makes EP and EPcut two numbers on this partition rather
    than one -- and it carries the loss-maker's sign, because a deduction does not turn a loss
    into a profit.
    """
    profit = _cumulative(PROFIT_INCREMENTS, period) * PROFIT_SCALE[subject]
    return {
        "eps": 0.31,
        "bps": 2.9,
        "roe": 5.9,
        "roa": 6.1,
        "netprofit_margin": 6.7,
        "grossprofit_margin": 7.1,
        "debt_to_assets": 7.3,
        "or_yoy": 7.9,
        "netprofit_yoy": 8.3,
        "ocfps": 8.9,
        "fcff": 9.7,
        DEDUCTED_NET_PROFIT_COLUMN: profit * DEDUCTED_PROFIT_MULTIPLE,
    }


def _statement_rows(
    dataset: str,
) -> tuple[tuple[str, date, date, dict[str, float | None]], ...]:
    periods = _income_periods if dataset == INCOME_DATASET else _balance_periods
    values = {
        INCOME_DATASET: _income_values,
        BALANCE_SHEET_DATASET: _balance_values,
        FINANCIAL_INDICATOR_DATASET: _indicator_values,
    }[dataset]
    rows = [
        (subject, period, ANNOUNCED_ON[period], values(subject, period))
        for subject in SUBJECTS
        for period in periods(subject)
    ]
    if dataset == INCOME_DATASET:
        # `600965.SH` files its newest period twice on one day, byte-identical in every projected
        # column -- the shape 372 of `income`'s 633 duplicate keys have. The domain collapses it
        # and, since `V2-P3-009`, so does the engine.
        newest = TRAILING_WINDOW[-1]
        rows.append((COLLAPSED, newest, ANNOUNCED_ON[newest], _income_values(COLLAPSED, newest)))
    return tuple(rows)


def _daily_basic_batch() -> ColumnarPanelBatch:
    """Every security's valuations over the whole of `DAILY_BASIC_DATA_COLUMNS`.

    `total_mv` and `circ_mv` are keyed by **literal** name and carry different series, so a
    denominator taken from the float answers differently; the other fifteen are constants.
    """
    subjects: list[str] = []
    days: list[date] = []
    caps: list[float] = []
    for subject in SUBJECTS:
        for index, day in enumerate(SESSIONS):
            subjects.append(subject)
            days.append(day)
            caps.append(_market_cap(subject, index))

    instants = tuple(_session_instant(day) for day in days)
    constant = tuple(1.0 for _ in days)
    moved = {
        "trade_date": PanelColumn("trade_date", "string", tuple(day.isoformat() for day in days)),
        "total_mv": PanelColumn("total_mv", "float", tuple(caps)),
        "circ_mv": PanelColumn("circ_mv", "float", tuple(cap * CIRC_MV_SHARE for cap in caps)),
    }
    columns = tuple(
        moved.get(name, PanelColumn(name, "float", constant)) for name in DAILY_BASIC_DATA_COLUMNS
    )
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=DAILY_BASIC_DATASET,
        kind="daily_basic",
        as_of=BUILT_AT,
        fetched_at=BUILT_AT,
        status="success",
        subjects=tuple(subjects),
        timeline=TimelineColumns(
            event_time=instants,
            available_time=instants,
            ingested_time=tuple(max(BUILT_AT, moment) for moment in instants),
            revision_time=instants,
        ),
        columns=columns,
    )


def _write_statements(
    store: PanelStore,
    dataset: str,
    rows: tuple[tuple[str, date, date, dict[str, float | None]], ...],
) -> None:
    for year in STATEMENT_YEARS:
        yearly = tuple(item for item in rows if item[2].year == year)
        if yearly:
            write_panel_batch(store, _statement_batch(dataset, yearly), year=year)


@pytest.fixture
def store(tmp_path: Path) -> PanelStore:
    built = PanelStore(tmp_path / "panel")
    _write_statements(built, INCOME_DATASET, _statement_rows(INCOME_DATASET))
    _write_statements(built, BALANCE_SHEET_DATASET, _statement_rows(BALANCE_SHEET_DATASET))
    _write_statements(
        built, FINANCIAL_INDICATOR_DATASET, _statement_rows(FINANCIAL_INDICATOR_DATASET)
    )
    write_panel_batch(built, _daily_basic_batch(), year=SESSION_YEAR)
    return built


def _requirement(dataset: str) -> ReadinessRequirement:
    if dataset == DAILY_BASIC_DATASET:
        return ReadinessRequirement(
            dataset=dataset,
            as_of=AS_OF,
            years=(SESSION_YEAR,),
            required_dates=None,
            required_subjects=None,
            required_fields=DAILY_BASIC_DATA_COLUMNS,
            max_staleness=SESSION_STALENESS,
        )
    return financial_statement_requirement(
        dataset=dataset,
        years=STATEMENT_YEARS,
        as_of=AS_OF,
        max_staleness=STATEMENT_STALENESS,
    )


def _compute(store: PanelStore, definition: FactorDefinition, **overrides: Any) -> FactorPanel:
    settings: dict[str, Any] = {
        "as_of": AS_OF,
        "subjects": SUBJECTS,
        "universe": UNIVERSE,
        "requirements": {name: _requirement(name) for name in definition.datasets},
        "code_commit": COMMIT,
        "built_at": BUILT_AT,
        **overrides,
    }
    return compute_factor(store, definition, **settings)


def _coverage(panel: FactorPanel) -> dict[str, str]:
    return {item.subject: item.coverage for item in panel.observations}


def _value(panel: FactorPanel, subject: str) -> float:
    values = panel.values()
    assert subject in values, f"{subject} is {_coverage(panel)[subject]}"
    return values[subject]


def _expected(subject: str, numerator: float) -> float:
    """`numerator / (the newest stored total_mv carried into yuan)`, from the fixture's own
    tables rather than from a run."""
    capitalisation = _market_cap(subject, len(SESSIONS) - 1) * CNY_PER_MARKET_CAP_UNIT
    return numerator / capitalisation


# --- the four values, off two axes ----------------------------------------------------------------


def test_the_four_factors_compute_off_two_axes_and_give_four_different_numbers(
    store: PanelStore,
) -> None:
    """The whole family through the real engine, each value pinned as a number.

    Every expectation is derived from the increment tables the partition was written from, so
    these are restatements of the fixture rather than numbers copied out of a run. The four are
    asserted pairwise distinct because four factors of one family sharing a denominator is
    precisely where the `V2-P3-004` review's finding recurs -- and the partition carries a
    different series in every neighbouring column, so an evaluator pointed at `revenue`,
    `n_income`, `circ_mv` or the consolidated equity lands somewhere else rather than here.

    **EPcut is the member that most needs this and it arrived last.** `V2-P3-017`'s
    `deducted_earnings_yield_ttm` differs from `earnings_yield_ttm` in one `(dataset, column)`
    and in nothing else, so a wiring that pointed it back at `income.n_income_attr_p` would
    produce a plausible number that happens to be EP's exactly; `DEDUCTED_PROFIT_MULTIPLE` is
    what makes that a visible failure. It is also the only member read off `fina_indicator`, so
    this is the first assertion in the repository that a factor binds a column of that partition
    at all.

    That the columns bind at all is the part only a partition can say: `FactorField` validates a
    column reference syntactically and says so, so `income.n_income_attr_p` and
    `daily_basic.total_mv` being real bindable columns of two written partitions -- read on two
    different axes in one build -- is not something a definition can assert.
    """
    answers = {
        definition.key: _value(_compute(store, definition), FULL)
        for definition in (
            EARNINGS_YIELD_TTM,
            BOOK_TO_PRICE,
            SALES_YIELD_TTM,
            DEDUCTED_EARNINGS_YIELD_TTM,
        )
    }
    newest_equity = _book_value(FULL, _balance_periods(FULL)[-1])
    assert newest_equity is not None

    assert answers["earnings_yield_ttm"] == pytest.approx(_expected(FULL, TRAILING_PROFIT))
    assert answers["sales_yield_ttm"] == pytest.approx(_expected(FULL, TRAILING_REVENUE))
    assert answers["book_to_price"] == pytest.approx(_expected(FULL, newest_equity))
    assert answers["deducted_earnings_yield_ttm"] == pytest.approx(
        _expected(FULL, TRAILING_DEDUCTED_PROFIT)
    )
    assert len({round(value, 12) for value in answers.values()}) == 4

    # The neighbouring columns this partition separates. Each is a number a wrong reader would
    # have produced, and none of them is the answer.
    assert answers["sales_yield_ttm"] != pytest.approx(
        _expected(FULL, TRAILING_REVENUE * REVENUE_SHARE)
    )
    assert answers["earnings_yield_ttm"] != pytest.approx(
        _expected(FULL, TRAILING_PROFIT * CONSOLIDATED_PROFIT_MULTIPLE)
    )
    assert answers["book_to_price"] != pytest.approx(
        _expected(FULL, newest_equity * (ASSET_MULTIPLE - LIABILITY_MULTIPLE))
    )
    assert answers["deducted_earnings_yield_ttm"] != pytest.approx(answers["earnings_yield_ttm"]), (
        "EPcut wired to income.n_income_attr_p would land exactly here"
    )
    assert answers["deducted_earnings_yield_ttm"] / answers["earnings_yield_ttm"] == pytest.approx(
        DEDUCTED_PROFIT_MULTIPLE
    )


def test_the_denominator_is_the_newest_session_and_the_market_cap_and_not_the_float(
    store: PanelStore,
) -> None:
    """Two ways to be wrong about the denominator, and a precise account of which is separable
    where.

    `total_mv` moves by `MARKET_CAP_DRIFT` per session, so the partition can tell the newest
    session from the oldest -- but the choice being made here is the **engine's** window
    formation, not the evaluator's indexing: at `MARKET_CAP_SESSIONS == 1` the window holds one
    session, so `series(...)[0]` and `series(...)[-1]` are the same cell and no fixture can
    separate them. Measured as a mutant: pointing `_market_capitalisation` at `[0]` leaves this
    whole file green. What it does separate is `_form_window` taking the most recent
    `lookback_sessions` rather than the earliest, which is what makes the answer the newest cap;
    `tests/unit/test_factor_value_family.py::
    test_the_capitalisation_is_the_newest_cell_of_whatever_window_it_is_handed` pins the
    evaluator's own half on a window this engine will not build.

    `circ_mv` is 0.6 of `total_mv` on every row, so a reader taking the float answers a different
    number and that half is separable here. Neither mistake is visible in a coverage census and
    neither would move a rank.
    """
    answer = _value(_compute(store, EARNINGS_YIELD_TTM), FULL)
    oldest = _market_cap(FULL, 0) * CNY_PER_MARKET_CAP_UNIT
    float_only = _market_cap(FULL, len(SESSIONS) - 1) * CIRC_MV_SHARE * CNY_PER_MARKET_CAP_UNIT

    assert answer == pytest.approx(_expected(FULL, TRAILING_PROFIT))
    assert answer != pytest.approx(TRAILING_PROFIT / oldest)
    assert answer != pytest.approx(TRAILING_PROFIT / float_only)


def test_the_trailing_numerator_is_not_the_latest_cumulative_filing(store: PanelStore) -> None:
    """The whole content of the TTM choice, on disk.

    The newest filing this corpus holds is a 2025 Q1 report, whose stored `n_income_attr_p` is one
    quarter of profit. A factor that read the latest cumulative figure -- which is what "the
    latest filing knowable at `as_of`" means if nobody accumulates -- would answer that, and the
    two are different numbers by construction. Reading the annual instead is the other near miss
    and is separated too.
    """
    answer = _value(_compute(store, EARNINGS_YIELD_TTM), FULL)
    latest_cumulative = _cumulative(PROFIT_INCREMENTS, TRAILING_WINDOW[-1])
    last_annual = _cumulative(PROFIT_INCREMENTS, date(2024, 12, 31))

    assert answer == pytest.approx(_expected(FULL, TRAILING_PROFIT))
    assert answer != pytest.approx(_expected(FULL, latest_cumulative))
    assert answer != pytest.approx(_expected(FULL, last_annual))


# --- the coverage codes ---------------------------------------------------------------------------


def test_a_loss_making_issuer_is_computed_and_negative_rather_than_undefined(
    store: PanelStore,
) -> None:
    """The judgement that makes this family yields rather than multiples, end to end.

    `603333.SH` loses money in all five quarters and still sells: its `earnings_yield_ttm` is
    negative and `computed`, and its `sales_yield_ttm` is positive and `computed`, so the two are
    not each other's sign. A cross section that reported `undefined_value` here would drop the
    part of the market a value factor has the most to say about -- and the published
    `daily_basic.pe_ttm`, which is what an inverted multiple would have read, is simply null for
    such a name on 1,214 of 5,338 rows of one measured session.
    """
    earnings = _value(_compute(store, EARNINGS_YIELD_TTM), LOSS)
    sales = _value(_compute(store, SALES_YIELD_TTM), LOSS)

    assert earnings == pytest.approx(_expected(LOSS, -TRAILING_PROFIT))
    assert earnings < 0.0
    assert sales == pytest.approx(_expected(LOSS, TRAILING_REVENUE * REVENUE_SCALE[LOSS]))
    assert sales > 0.0


def test_a_negative_book_value_is_computed_and_negative(store: PanelStore) -> None:
    """BP's half of the same judgement: an insolvent issuer has negative equity and a real
    price, and `higher_is_better` then ranks it below every solvent name, which is the ordering
    a book-to-price should have."""
    newest = _book_value(INSOLVENT, _balance_periods(INSOLVENT)[-1])
    assert newest is not None and newest < 0.0

    answer = _value(_compute(store, BOOK_TO_PRICE), INSOLVENT)

    assert answer == pytest.approx(_expected(INSOLVENT, newest))
    assert answer < 0.0


def test_a_zero_market_capitalisation_is_undefined_value_for_every_factor_in_the_family(
    store: PanelStore,
) -> None:
    """`undefined_value`, provoked through the whole engine on a partition the writers accept.

    `domain/daily_prices.py` guards `DAILY_PRICE_COLUMNS`' five columns against a non-positive
    value and `total_mv` only against a non-finite one, so a stored zero is readable -- which is
    what makes this code a branch that runs rather than a table entry. All three factors share
    the denominator, so all three answer `undefined_value` for this security and every one of
    them answers `computed` for `000001.SZ` in the same build, which is what separates a fault in
    the fixture from the code under test.
    """
    for definition in (EARNINGS_YIELD_TTM, BOOK_TO_PRICE, SALES_YIELD_TTM):
        coverage = _coverage(_compute(store, definition))

        assert coverage[ZERO_CAP] == "undefined_value"
        assert coverage[FULL] == "computed"


def test_a_null_book_value_is_input_missing_for_that_factor_alone(store: PanelStore) -> None:
    """`input_missing` on one dataset, and the other two factors unaffected in the same corpus.

    `300750.SZ`'s newest `balancesheet` row carries a null in the one column `book_to_price`
    reads, so the window cannot be completed and the remedy is a fetch. Its `income` rows are
    whole, so the two flow factors compute -- which is what makes this an answer about a column
    rather than about a security.
    """
    assert _coverage(_compute(store, BOOK_TO_PRICE))[NULL_BOOK] == "input_missing"
    assert _coverage(_compute(store, EARNINGS_YIELD_TTM))[NULL_BOOK] == "computed"
    assert _coverage(_compute(store, SALES_YIELD_TTM))[NULL_BOOK] == "computed"


def test_the_one_period_reach_and_the_five_period_reach_answer_differently_for_the_same_security(
    store: PanelStore,
) -> None:
    """The coverage difference inside the family, which is the cost of the trailing window.

    `600000.SH` has filed twice. That is fewer than `TRAILING_TWELVE_MONTH_PERIODS`, so both flow
    factors report `insufficient_history` and record no period window at all; it is at least one,
    so `book_to_price` computes off the newest filing. The same security, the same partition, the
    same `as_of` -- so the reach is separable from everything else about the build.
    """
    assert len(_income_periods(SHORT)) < TRAILING_TWELVE_MONTH_PERIODS
    earnings = _compute(store, EARNINGS_YIELD_TTM)
    book = _compute(store, BOOK_TO_PRICE)
    newest = _book_value(SHORT, _balance_periods(SHORT)[-1])
    assert newest is not None

    assert _coverage(earnings)[SHORT] == "insufficient_history"
    assert _coverage(_compute(store, SALES_YIELD_TTM))[SHORT] == "insufficient_history"
    assert _value(book, SHORT) == pytest.approx(_expected(SHORT, newest))

    short_row = next(item for item in earnings.observations if item.subject == SHORT)
    assert short_row.input_period_first is None
    assert short_row.input_period_last is None
    assert short_row.input_session_first == SESSIONS[-1]


def test_a_missed_filing_is_the_span_bound_and_not_the_count(store: PanelStore) -> None:
    """`max_window_periods` separated from `lookback_periods` on one partition.

    `000063.SZ` has filed exactly five times, so the count clears; the five span **six** fiscal
    quarters, because the December in the middle is missing. That is the case
    `TRAILING_TWELVE_MONTH_PERIODS` argues cannot be allowed through -- `window[:-1]` would hold
    no fiscal year end and the trailing identity's three terms would not be the periods it names.

    Asserted with the count check beside it, so the refusal cannot be satisfied by the shortfall
    branch: `600000.SH` above is short on the count at an unbroken span and this security is
    whole on the count at a broken span, and both answer `insufficient_history`.
    """
    filed = _income_periods(GAP)
    assert len(filed) == TRAILING_TWELVE_MONTH_PERIODS
    assert date(2024, 12, 31) not in filed

    coverage = _coverage(_compute(store, EARNINGS_YIELD_TTM))

    assert coverage[GAP] == "insufficient_history"
    assert coverage[FULL] == "computed"


def test_a_security_outside_the_universe_is_not_in_universe_on_every_factor(
    store: PanelStore,
) -> None:
    """The code that must not be confused with a data fault, on a security whose rows are all
    present: `601318.SH` has every filing and every session and is simply not in the cross
    section the caller declared."""
    for definition in (EARNINGS_YIELD_TTM, BOOK_TO_PRICE, SALES_YIELD_TTM):
        panel = _compute(store, definition)
        observation = next(item for item in panel.observations if item.subject == OUTSIDE)

        assert observation.coverage == "not_in_universe"
        assert observation.input_row_count == 0
        assert observation.input_period_first is None
        assert observation.input_session_first is None


# --- the duplicate rows: collapsed when they agree, coded when they do not ------------------------


def test_one_contradictory_filing_costs_that_security_and_leaves_the_cross_section_untouched(
    store: PanelStore, tmp_path: Path
) -> None:
    """`V2-P3-018`'s acceptance criterion, as a cross section rather than as a single security.

    A second `income` row is added for `000001.SZ` at the newest period, disagreeing about
    `total_revenue` and agreeing about everything else -- the shape 8.51% of `income`'s real
    filings have. Before `V2-P3-018` this build raised `FactorEngineError` and **no security in
    the cross section got an observation**, which is why none of `V2-P3-009`..`011`'s six factors
    could be built over a real whole-market statement partition at all.

    Three things are asserted and the third is the one with teeth:

    1. the contradicting security is `ambiguous_filing` and carries no value;
    2. the census counts exactly one of them, so the build's own manifest row says how much of the
       market this cost;
    3. **every other security's coverage code and value are byte-identical to the clean build's**
       -- not merely "present", which a change that quietly re-read the partition differently
       would also satisfy. `pytest.approx` is deliberately not used on the values: the two builds
       read the same rows through the same arithmetic, so anything but equality is a difference
       this test should see.

    The clean panel is the same `store` fixture the rest of this file uses, so the comparison is
    against nine securities spanning four of the other five codes at once -- `computed`,
    `insufficient_history`, `undefined_value` and `not_in_universe`. `input_missing` is the fifth
    and is `book_to_price`'s answer on this corpus rather than `sales_yield_ttm`'s (the null cell
    is `balancesheet.total_hldr_eqy_exc_min_int`, which this factor does not read);
    `test_an_ambiguous_filing_outranks_a_null_cell_in_the_same_window` is where the two meet.
    """
    contradicted = PanelStore(tmp_path / "one_bad_filing")
    newest = TRAILING_WINDOW[-1]
    rows = [
        *_statement_rows(INCOME_DATASET),
        (
            FULL,
            newest,
            ANNOUNCED_ON[newest],
            {**_income_values(FULL, newest), "total_revenue": DISPUTED_TOTAL_REVENUE[1]},
        ),
    ]
    _write_statements(contradicted, INCOME_DATASET, tuple(rows))
    _write_statements(contradicted, BALANCE_SHEET_DATASET, _statement_rows(BALANCE_SHEET_DATASET))
    write_panel_batch(contradicted, _daily_basic_batch(), year=SESSION_YEAR)

    clean = _compute(store, SALES_YIELD_TTM)
    partial = _compute(contradicted, SALES_YIELD_TTM)
    others = tuple(name for name in SUBJECTS if name != FULL)

    assert _coverage(clean)[FULL] == "computed"
    assert _coverage(partial)[FULL] == "ambiguous_filing"
    assert partial.values().get(FULL) is None
    assert partial.coverage_census()["ambiguous_filing"] == 1
    assert clean.coverage_census()["ambiguous_filing"] == 0
    assert {name: _coverage(partial)[name] for name in others} == {
        name: _coverage(clean)[name] for name in others
    }
    assert {name: partial.values().get(name) for name in others} == {
        name: clean.values().get(name) for name in others
    }
    assert set(_coverage(clean).values()) == {
        "computed",
        "insufficient_history",
        "undefined_value",
        "not_in_universe",
    }
    assert set(_coverage(partial).values()) == set(_coverage(clean).values()) | {"ambiguous_filing"}


def test_two_income_rows_of_one_filing_that_agree_collapse_instead_of_refusing_the_build(
    store: PanelStore,
) -> None:
    """The change `V2-P3-009` made to `_read_dataset`, on the shape most of the duplication has.

    `600965.SH` files its newest period twice on one day with identical values in every projected
    column. `build_statement_history` folds such a pair -- 372 of `income`'s 633 duplicate keys,
    1,166 of `balancesheet`'s 1,244 and 2,194 of `fina_indicator`'s 2,671 are byte-identical --
    and until this issue the engine refused the whole build for it, which made the engine
    strictly stricter than the domain it says it restates.

    The value is asserted as well as the coverage, because a collapse that kept the wrong row
    would still be `computed`: both rows carry this security's own series, so the answer is the
    same number `000001.SZ`'s scale would give.
    """
    rows = _statement_rows(INCOME_DATASET)
    newest = TRAILING_WINDOW[-1]
    duplicated = [item for item in rows if item[0] == COLLAPSED and item[1] == newest]
    assert len(duplicated) == 2
    assert duplicated[0][3] == duplicated[1][3]

    panel = _compute(store, EARNINGS_YIELD_TTM)

    assert _coverage(panel)[COLLAPSED] == "computed"
    assert _value(panel, COLLAPSED) == pytest.approx(_expected(COLLAPSED, TRAILING_PROFIT))


def test_two_identical_session_rows_still_refuse_the_build_because_the_axis_has_no_versions(
    tmp_path: Path,
) -> None:
    """The asymmetry the collapse must not have spread to the other axis.

    The period axis folds a duplicate row that agrees because these four endpoints have a
    *measured* version structure -- two rows of one filing under one announcement, counted in
    `domain/financial_statements.py` -- and `build_statement_history` folds it too. No
    session-indexed dataset here has anything of the kind: `daily` and `daily_basic` serve one row
    per security per session, so a second one is a fault rather than a version and folding it
    silently would hide the fault.

    Driven on a `daily_basic` partition carrying the same security's same session twice with
    **byte-identical** cells, which is exactly the case the period axis now collapses -- so a
    collapse written without the axis test would pass every other assertion in this file and fail
    here.
    """
    built = PanelStore(tmp_path / "duplicated_session")
    _write_statements(built, INCOME_DATASET, _duplicated_rows(DISPUTED_TOTAL_REVENUE)[:-1])
    days = (*SESSIONS, SESSIONS[-1])
    instants = tuple(_session_instant(day) for day in days)
    constant = tuple(1.0 for _ in days)
    moved = {
        "trade_date": PanelColumn("trade_date", "string", tuple(day.isoformat() for day in days)),
        "total_mv": PanelColumn("total_mv", "float", tuple(900.0 + 37.0 * i for i in (0, 1, 2, 2))),
    }
    write_panel_batch(
        built,
        ColumnarPanelBatch(
            provider_id="openalpha-cn/tests",
            dataset=DAILY_BASIC_DATASET,
            kind="daily_basic",
            as_of=BUILT_AT,
            fetched_at=BUILT_AT,
            status="success",
            subjects=tuple(DUPLICATED for _ in days),
            timeline=TimelineColumns(
                event_time=instants,
                available_time=instants,
                ingested_time=tuple(max(BUILT_AT, moment) for moment in instants),
                revision_time=instants,
            ),
            columns=tuple(
                moved.get(name, PanelColumn(name, "float", constant))
                for name in DAILY_BASIC_DATA_COLUMNS
            ),
        ),
        year=SESSION_YEAR,
    )

    with pytest.raises(FactorEngineError, match="one row per security per session"):
        compute_factor(
            built,
            EARNINGS_YIELD_TTM,
            as_of=AS_OF,
            subjects=(DUPLICATED,),
            universe=frozenset({DUPLICATED}),
            requirements={name: _requirement(name) for name in EARNINGS_YIELD_TTM.datasets},
            code_commit=COMMIT,
            built_at=BUILT_AT,
        )


def _duplicated_rows(
    revenues: tuple[float, float],
) -> tuple[tuple[str, date, date, dict[str, float | None]], ...]:
    """`600739.SH`'s five filings, the newest of which arrives as two rows.

    The two carry the real numbers: the same `n_income_attr_p` and, when `revenues` says so, the
    two `total_revenue` figures the endpoint really served for that period.
    """
    rows = [
        (
            DUPLICATED,
            period,
            ANNOUNCED_ON[period],
            {
                **_income_values(FULL, period),
                "n_income_attr_p": AGREED_NET_PROFIT,
                "total_revenue": revenues[0],
            },
        )
        for period in TRAILING_WINDOW
    ]
    newest = TRAILING_WINDOW[-1]
    rows.append(
        (
            DUPLICATED,
            newest,
            ANNOUNCED_ON[newest],
            {
                **_income_values(FULL, newest),
                "n_income_attr_p": AGREED_NET_PROFIT,
                "total_revenue": revenues[1],
            },
        )
    )
    return tuple(rows)


def _write_prices_for_the_duplicated_security(
    store: PanelStore, *, subject: str = DUPLICATED
) -> None:
    """The three `daily_basic` sessions every store in this section needs, written once.

    Extracted from `disputed_store` when `V2-P3-018` gave this section five more stores to build:
    a factor on two axes needs the price partition even when the test is entirely about the
    filing, and five copies of this batch would be five places for the session series to drift
    from `MARKET_CAP_BASE`. `subject` is a keyword with a default because `V2-P3-017` needed the
    same three sessions under a second name and a copy would have been the sixth place.
    """
    subjects = tuple(subject for _ in SESSIONS)
    instants = tuple(_session_instant(day) for day in SESSIONS)
    constant = tuple(1.0 for _ in SESSIONS)
    moved = {
        "trade_date": PanelColumn(
            "trade_date", "string", tuple(day.isoformat() for day in SESSIONS)
        ),
        "total_mv": PanelColumn("total_mv", "float", tuple(900.0 + 37.0 * i for i in range(3))),
    }
    write_panel_batch(
        store,
        ColumnarPanelBatch(
            provider_id="openalpha-cn/tests",
            dataset=DAILY_BASIC_DATASET,
            kind="daily_basic",
            as_of=BUILT_AT,
            fetched_at=BUILT_AT,
            status="success",
            subjects=subjects,
            timeline=TimelineColumns(
                event_time=instants,
                available_time=instants,
                ingested_time=tuple(max(BUILT_AT, moment) for moment in instants),
                revision_time=instants,
            ),
            columns=tuple(
                moved.get(name, PanelColumn(name, "float", constant))
                for name in DAILY_BASIC_DATA_COLUMNS
            ),
        ),
        year=SESSION_YEAR,
    )


@pytest.fixture
def disputed_store(tmp_path: Path) -> PanelStore:
    """One partition holding the two rows `600739.SH`'s 2024 annual really arrives as."""
    built = PanelStore(tmp_path / "disputed")
    _write_statements(built, INCOME_DATASET, _duplicated_rows(DISPUTED_TOTAL_REVENUE))
    _write_prices_for_the_duplicated_security(built)
    return built


def _compute_one(
    store: PanelStore, definition: FactorDefinition, *, subject: str = DUPLICATED
) -> FactorPanel:
    """`_compute` narrowed to one security, which is the only name these stores hold."""
    return compute_factor(
        store,
        definition,
        as_of=AS_OF,
        subjects=(subject,),
        universe=frozenset({subject}),
        requirements={name: _requirement(name) for name in definition.datasets},
        code_commit=COMMIT,
        built_at=BUILT_AT,
    )


def test_the_engine_answers_a_column_the_duplicate_rows_agree_about_and_codes_one_they_do_not(
    disputed_store: PanelStore,
) -> None:
    """Per-field ambiguity, driven through the engine on two rows the endpoint really serves.

    `600739.SH`'s 2024 annual is two rows that **both** carry `update_flag='1'` -- so there is
    nothing to rank even for a reader willing to rank the flag -- disagreeing about
    `total_revenue` by 4.6% and agreeing exactly about `n_income_attr_p`.
    `domain/financial_statements.py` says the refusal is per field and `ReportFiling.value_of`
    implements it; this is that sentence as one pair of calls over one partition.

    **`V2-P3-018` changed the second half from a build refusal into a coverage code, and this test
    with it.** Before it the `sales_yield_ttm` call raised and no observation existed for any
    security in the cross section; now it returns a panel in which this security carries
    `ambiguous_filing` and `value is None`. The per-field half is unchanged and is what makes the
    pair worth asserting together: one partition, two factors, two different answers, decided by
    which column each one reads.

    Asserted as a *mapping over the two factors* rather than as two independent lines, so the
    failure mode a per-security code invites -- every factor answering `ambiguous_filing` -- cannot
    pass by satisfying half of it.
    """
    answers = {
        definition.key: _compute_one(disputed_store, definition)
        for definition in (EARNINGS_YIELD_TTM, SALES_YIELD_TTM)
    }
    coverage = {key: _coverage(panel)[DUPLICATED] for key, panel in answers.items()}
    sales = answers["sales_yield_ttm"]

    assert coverage == {"earnings_yield_ttm": "computed", "sales_yield_ttm": "ambiguous_filing"}
    assert sales.observations[0].value is None
    assert sales.values() == {}
    assert sales.coverage_census()["ambiguous_filing"] == 1
    # The window the ambiguity sits in is still recorded, which is what keeps the filing
    # locatable from the stored row: it is this observation's `input_period_last`.
    assert sales.observations[0].input_period_last == TRAILING_WINDOW[-1]


def test_an_ambiguous_filing_outranks_a_null_cell_in_the_same_window(tmp_path: Path) -> None:
    """The precedence `FACTOR_COVERAGE_ORDER` declares, on the one pair where it is load-bearing.

    A security whose window holds **both** a contradictory filing and a null cell could honestly
    be called either. It is `ambiguous_filing`, because the two codes name different repairs and
    only one of them works: a reader told `input_missing` re-fetches, and a re-fetch returns the
    same two contradictory rows. `_classify` decides them in `FACTOR_COVERAGE_ORDER`'s order and
    `tests/unit/test_factor_engine_rules.py::
    test_the_census_order_is_the_order_classify_decides_the_codes_in` reads that order off the
    source; this is the behaviour behind it.

    The control is the same corpus with the duplicate row removed, and it must answer
    `input_missing` -- without it, a `_classify` that returned `ambiguous_filing` for every
    security with a null cell anywhere would pass this test.
    """
    nulled = tuple(
        (item[0], item[1], item[2], {**item[3], "total_revenue": None})
        if item[1] == TRAILING_WINDOW[0]
        else item
        for item in _duplicated_rows(DISPUTED_TOTAL_REVENUE)
    )
    both = PanelStore(tmp_path / "both")
    _write_statements(both, INCOME_DATASET, nulled)
    _write_prices_for_the_duplicated_security(both)
    null_only = PanelStore(tmp_path / "null_only")
    _write_statements(null_only, INCOME_DATASET, nulled[:-1])
    _write_prices_for_the_duplicated_security(null_only)

    assert _coverage(_compute_one(both, SALES_YIELD_TTM))[DUPLICATED] == "ambiguous_filing"
    assert _coverage(_compute_one(null_only, SALES_YIELD_TTM))[DUPLICATED] == "input_missing"


def test_an_ambiguous_filing_outside_the_window_this_factor_forms_is_not_this_factors_problem(
    tmp_path: Path,
) -> None:
    """The mark is scoped to the window, so an old contradiction does not condemn a fresh number.

    `_classify` intersects the ambiguous periods with the window it actually formed. A filing the
    publisher contradicted itself about in 2023 did not enter a trailing-twelve-month number taken
    over 2024Q1..2025Q1, and coding the security for it would report a defect in an answer that
    does not depend on it -- `not_in_universe`'s argument, one axis over.

    2023-12-31 is the period this file already keeps out of every window (see
    `PRIOR_YEAR_CUMULATIVE`), so the corpus separates "in the partition" from "in the window"
    without inventing a second one. The control is the same disagreement on the newest period,
    which must be coded -- otherwise a `_classify` that marked nothing at all would pass.
    """
    stale = PERIODS[0]
    outside = PanelStore(tmp_path / "outside")
    rows = [
        *_duplicated_rows(DISPUTED_TOTAL_REVENUE)[:-1],
        (DUPLICATED, stale, ANNOUNCED_ON[stale], _income_values(FULL, stale)),
        (
            DUPLICATED,
            stale,
            ANNOUNCED_ON[stale],
            {**_income_values(FULL, stale), "total_revenue": DISPUTED_TOTAL_REVENUE[1]},
        ),
    ]
    _write_statements(outside, INCOME_DATASET, tuple(rows))
    _write_prices_for_the_duplicated_security(outside)
    inside = PanelStore(tmp_path / "inside")
    _write_statements(inside, INCOME_DATASET, _duplicated_rows(DISPUTED_TOTAL_REVENUE))
    _write_prices_for_the_duplicated_security(inside)

    panel = _compute_one(outside, SALES_YIELD_TTM)

    assert _coverage(panel)[DUPLICATED] == "computed"
    assert panel.observations[0].input_period_first == TRAILING_WINDOW[0]
    assert _coverage(_compute_one(inside, SALES_YIELD_TTM))[DUPLICATED] == "ambiguous_filing"


RESTATED_ON: Final[date] = date(2025, 5, 12)
"""A later announcement of the newest period, inside `AS_OF`'s visibility and after the pair.

Two weeks after `ANNOUNCED_ON[2025-03-31]` and eight days before `AS_OF`, so `filing_for`'s
`max(announced_on)` really does prefer it and `read_visible_at` really does return it.
"""

RESTATED_TOTAL_REVENUE: Final[float] = 12_400_000_000.0
"""What the restatement says, distinct from both members of the pair it supersedes so that a
value computed off it is separable from one computed off either of them."""


def test_a_superseded_ambiguous_pair_still_codes_the_security_rather_than_taking_the_later_row(
    tmp_path: Path,
) -> None:
    """The one corner where this engine and `ReportFiling.filing_for` deliberately part.

    Three rows for one period: two announced on the same day that disagree, and a **later**
    restatement that supersedes both. `filing_for` takes `max(announced_on)` and never consults
    the superseded pair, so the domain answers off the restatement; this engine marks the period
    ambiguous anyway. `_columns_two_versions_disagree_about` argues why -- the same-day check sits
    on the `(subject, period, announcement)` triple precisely so that whether the pair was
    discarded cannot depend on the order DuckDB returned the rows in, which was measured to change
    the answer on one corpus in three write orders.

    What `V2-P3-018` changed is the *price* of that divergence and not the divergence: it used to
    cost the whole cross section a build and now costs this security a coverage code. So the
    declined answer is shown to exist and to be a specific number rather than merely unavailable:
    two control stores compute off the restatement and off the superseded row, and their two
    values differ -- which is what makes "the engine is declining an answer a later announcement
    supplies" a measurement instead of a claim.
    """
    newest = TRAILING_WINDOW[-1]
    base = list(_duplicated_rows(DISPUTED_TOTAL_REVENUE))
    restatement = (
        DUPLICATED,
        newest,
        RESTATED_ON,
        {
            **_income_values(FULL, newest),
            "n_income_attr_p": AGREED_NET_PROFIT,
            "total_revenue": RESTATED_TOTAL_REVENUE,
        },
    )
    superseded = PanelStore(tmp_path / "superseded")
    _write_statements(superseded, INCOME_DATASET, (*base, restatement))
    _write_prices_for_the_duplicated_security(superseded)
    later_only = PanelStore(tmp_path / "later_only")
    _write_statements(later_only, INCOME_DATASET, (*base[:-2], restatement))
    _write_prices_for_the_duplicated_security(later_only)
    earlier_only = PanelStore(tmp_path / "earlier_only")
    _write_statements(earlier_only, INCOME_DATASET, tuple(base[:-1]))
    _write_prices_for_the_duplicated_security(earlier_only)

    later = _compute_one(later_only, SALES_YIELD_TTM)
    earlier = _compute_one(earlier_only, SALES_YIELD_TTM)

    assert _coverage(_compute_one(superseded, SALES_YIELD_TTM))[DUPLICATED] == "ambiguous_filing"
    assert _coverage(later)[DUPLICATED] == "computed"
    assert _coverage(earlier)[DUPLICATED] == "computed"
    assert _value(later, DUPLICATED) != pytest.approx(_value(earlier, DUPLICATED))


def test_the_engines_collapse_is_the_domains_and_they_agree_on_the_same_duplicate_filing(
    disputed_store: PanelStore,
) -> None:
    """The engine's answer held against `ReportFiling.value_of`'s over the identical rows.

    `_read_dataset` states the domain's rule a second time rather than calling into it, because
    `statement_histories_from_panel_rows` needs the dataset's whole projected column set and a
    factor reading one column would then fetch all ten. A second statement of one rule is only
    safe with an audit against the first, and the audit that existed could not see this case: its
    corpus has no key whose duplicate rows agree, which is exactly the case where the two
    implementations disagreed.

    Both directions are asserted here on one corpus. On `n_income_attr_p` the domain answers and
    the engine computes; on `total_revenue` the domain raises `AmbiguousReportError` and the
    engine codes the security `ambiguous_filing`. Those are the same verdict at two granularities,
    which is the whole of `V2-P3-018`: the domain declines *that field of that filing* and the
    engine declines *that security's value*, where it used to decline the build.
    """
    rows = [
        (
            item[0],
            item[1].isoformat(),
            item[2].isoformat(),
            item[2].isoformat(),
            "1",
            *(item[3][name] for name in INCOME_DATA_COLUMNS),
        )
        for item in _duplicated_rows(DISPUTED_TOTAL_REVENUE)
    ]
    histories = statement_histories_from_panel_rows(
        dataset=INCOME_DATASET,
        columns=(SUBJECT_COLUMN_NAME, *statement_panel_columns(INCOME_DATASET)),
        rows=rows,
    )
    filing = histories[DUPLICATED].filing_for(TRAILING_WINDOW[-1], date(2025, 5, 20))

    assert filing.is_ambiguous
    assert filing.disagreeing_fields == ("total_revenue",)
    assert filing.value_of(NET_PROFIT_COLUMN) == AGREED_NET_PROFIT
    assert filing.values_of(TOTAL_REVENUE_COLUMN) == DISPUTED_TOTAL_REVENUE

    earnings = _compute_one(disputed_store, EARNINGS_YIELD_TTM)
    observation = earnings.observations[0]

    assert observation.coverage == "computed"
    assert observation.input_period_last == TRAILING_WINDOW[-1]
    # The collapse did not inflate the row count either: five filings and one session, not six.
    assert observation.input_row_count == TRAILING_TWELVE_MONTH_PERIODS + 1


@pytest.mark.parametrize("order", ["as_served", "reversed"])
def test_the_disagreement_code_does_not_depend_on_the_order_the_partition_returns_its_rows(
    tmp_path: Path, order: str
) -> None:
    """The property the per-security code must not have cost: order independence.

    `read_visible_at` issues its scan with no `ORDER BY` over a partition DuckDB may read in
    parallel row groups, so nothing downstream of the provider decides which of two duplicate
    rows arrives first. A comparison against "whatever was stored so far" is exactly the shape
    that was measured answering on one order and refusing on another, before the same-day check
    was moved onto the triple. Comparing on equality keeps that property -- equality is symmetric
    -- and `V2-P3-018` had to keep it while turning the verdict into a *stored value*, which is
    the harder half: a refusal is a control-flow event a caller cannot miss, and a coverage code
    reaches Parquet, so a residual order-dependence would now be silent rather than loud.
    """
    first, second = DISPUTED_TOTAL_REVENUE
    revenues = (first, second) if order == "as_served" else (second, first)
    built = PanelStore(tmp_path / order)
    _write_statements(built, INCOME_DATASET, _duplicated_rows(revenues))
    _write_prices_for_the_duplicated_security(built)

    panel = _compute_one(built, SALES_YIELD_TTM)

    assert _coverage(panel)[DUPLICATED] == "ambiguous_filing"
    assert panel.observations[0].value is None


# --- `V2-P3-017`: the endpoint EPcut had to be put on, and what that costs -----------------------

DEDUCTED_DISPUTED: Final[str] = "000488.SZ"
"""The security whose 2015 annual separates EP from EPcut on real rows served by two endpoints.

Already named in `KNOWN_FINANCIAL_STATEMENT_LIMITATIONS
.the_merge_rule_is_agreement_in_the_projection` for the `dt_netprofit_yoy` its two
`fina_indicator` rows disagree about; `V2-P3-017` re-fetched the same filing with `profit_dedt`
requested and found the disagreement reaching that column too.
"""

DISPUTED_DEDUCTED_PROFIT: Final[tuple[float, float]] = (719_891_359.63, 1_846_820_211.10)
"""`000488.SZ` 2015 annual (`ann_date` 2016-03-31): the two `profit_dedt` figures, verbatim.

A factor of **2.566**, both non-null, under one announcement on the endpoint with no
`update_flag`, no `f_ann_date` and no `report_type`. Captured live 2026-08-17. It straddles the
caliber band as well as the magnitude: against the `n_income_attr_p` the same issuer's `income`
rows carry for that filing (2,148,153,529.51), the two versions are 0.86x and 0.34x -- so picking
one is not a rounding decision, which is `domain/financial_statements.py`'s own sentence about
this endpoint arriving on the column EPcut reads.
"""

AGREED_DISPUTED_NET_PROFIT: Final[float] = 2_148_153_529.51
"""The same filing's `income.n_income_attr_p`, which its **two** `income` rows -- `update_flag`
`'0'` and `'1'` -- carry identically. Captured in the same probe, and it is what makes the pair
below an asymmetry between two endpoints rather than between two fixtures."""


def _deducted_dispute_rows(
    deducted: tuple[float, float],
) -> tuple[tuple[str, date, date, dict[str, float | None]], ...]:
    """`000488.SZ`'s five `fina_indicator` filings, the newest of which arrives as two rows."""
    rows = [
        (
            DEDUCTED_DISPUTED,
            period,
            ANNOUNCED_ON[period],
            {**_indicator_values(FULL, period), DEDUCTED_NET_PROFIT_COLUMN: deducted[0]},
        )
        for period in TRAILING_WINDOW
    ]
    newest = TRAILING_WINDOW[-1]
    rows.append(
        (
            DEDUCTED_DISPUTED,
            newest,
            ANNOUNCED_ON[newest],
            {**_indicator_values(FULL, newest), DEDUCTED_NET_PROFIT_COLUMN: deducted[1]},
        )
    )
    return tuple(rows)


def _agreed_income_rows() -> tuple[tuple[str, date, date, dict[str, float | None]], ...]:
    """The same security's five `income` filings, the newest of which also arrives as two rows --
    carrying the **same** `n_income_attr_p`, which is what that endpoint really served."""
    rows = [
        (
            DEDUCTED_DISPUTED,
            period,
            ANNOUNCED_ON[period],
            {**_income_values(FULL, period), "n_income_attr_p": AGREED_DISPUTED_NET_PROFIT},
        )
        for period in TRAILING_WINDOW
    ]
    return (*rows, rows[-1])


def test_a_contradiction_in_the_deducted_profit_codes_epcut_and_leaves_ep_computed(
    tmp_path: Path,
) -> None:
    """What putting a factor on `fina_indicator` costs, driven on one security's two endpoints.

    `000488.SZ`'s 2015 annual is served twice by both endpoints. `income` gives the two rows the
    **same** `n_income_attr_p` under `update_flag` `'0'` and `'1'`, so they collapse and
    `earnings_yield_ttm` computes. `fina_indicator` gives them `profit_dedt` 719,891,359.63 and
    1,846,820,211.10 -- 2.566x apart, with nothing in that endpoint to order them -- so they do
    not collapse and `deducted_earnings_yield_ttm` is `ambiguous_filing`.

    That asymmetry is the whole content of `DEDUCTED_NET_PROFIT_COLUMN`'s "the cost is the
    endpoint", and it is one partition set rather than two arguments. The measured *frequency*
    behind it is recorded there (1.075% and 0.769% of filings against 0.189% and 0.459%); what a
    partition can add is that the two factors reach different verdicts on one build, so a reader
    who sees EPcut refuse a security is not seeing a defect in the security.

    The control is the second half: the same corpus with the two `fina_indicator` rows agreeing
    computes both, so a `_classify` that coded `ambiguous_filing` for anything reading this
    dataset would fail here.
    """
    contradicting = PanelStore(tmp_path / "contradicting")
    _write_statements(contradicting, INCOME_DATASET, _agreed_income_rows())
    _write_statements(
        contradicting, FINANCIAL_INDICATOR_DATASET, _deducted_dispute_rows(DISPUTED_DEDUCTED_PROFIT)
    )
    _write_prices_for_the_duplicated_security(contradicting, subject=DEDUCTED_DISPUTED)

    agreeing = PanelStore(tmp_path / "agreeing")
    _write_statements(agreeing, INCOME_DATASET, _agreed_income_rows())
    _write_statements(
        agreeing,
        FINANCIAL_INDICATOR_DATASET,
        _deducted_dispute_rows((DISPUTED_DEDUCTED_PROFIT[0], DISPUTED_DEDUCTED_PROFIT[0])),
    )
    _write_prices_for_the_duplicated_security(agreeing, subject=DEDUCTED_DISPUTED)

    split = {
        definition.key: _coverage(
            _compute_one(contradicting, definition, subject=DEDUCTED_DISPUTED)
        )[DEDUCTED_DISPUTED]
        for definition in (EARNINGS_YIELD_TTM, DEDUCTED_EARNINGS_YIELD_TTM)
    }
    control = {
        definition.key: _coverage(_compute_one(agreeing, definition, subject=DEDUCTED_DISPUTED))[
            DEDUCTED_DISPUTED
        ]
        for definition in (EARNINGS_YIELD_TTM, DEDUCTED_EARNINGS_YIELD_TTM)
    }

    assert split == {
        "earnings_yield_ttm": "computed",
        "deducted_earnings_yield_ttm": "ambiguous_filing",
    }
    assert control == {
        "earnings_yield_ttm": "computed",
        "deducted_earnings_yield_ttm": "computed",
    }
    assert round(DISPUTED_DEDUCTED_PROFIT[1] / DISPUTED_DEDUCTED_PROFIT[0], 3) == 2.565


# --- the round trip -------------------------------------------------------------------------------


def test_both_window_pairs_survive_the_round_trip_for_a_factor_on_two_axes(
    store: PanelStore,
) -> None:
    """The stored observation of a two-axis factor, read back off Parquet.

    A factor on one axis leaves the other pair null, and every shipped factor before this one was
    on one axis -- so a writer that put a session date into the period column, or dropped either
    pair, was invisible in storage. Here both pairs are populated with **different** dates and
    both are asserted by value.
    """
    write_factor_panels(store, [_compute(store, EARNINGS_YIELD_TTM)])
    observations = load_factor_observations(
        store, EARNINGS_YIELD_TTM, years=(AS_OF.year,), as_of=AS_OF
    )
    computed = next(item for item in observations if item.subject == FULL)

    assert computed.coverage == "computed"
    assert computed.value == pytest.approx(_expected(FULL, TRAILING_PROFIT))
    assert computed.input_period_first == TRAILING_WINDOW[0]
    assert computed.input_period_last == TRAILING_WINDOW[-1]
    assert computed.input_session_first == SESSIONS[-1]
    assert computed.input_session_last == SESSIONS[-1]
    assert computed.input_period_first != computed.input_session_first
    assert computed.input_row_count == TRAILING_TWELVE_MONTH_PERIODS + 1
