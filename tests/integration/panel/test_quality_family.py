"""The quality family against real partitions on disk (`V2-P3-010`).

`tests/unit/test_factor_quality_family.py` measures the four evaluators as functions of a window.
This file measures what a function cannot answer, and most of it is something no factor shipped
before this one could have said:

## A factor on the report-period axis **alone**, through the whole engine

Every factor shipped before `V2-P3-010` reads a session dataset -- nine read only sessions and
`V2-P3-009`'s three read a filing over a price. These four read filings and nothing else, so
`compute_factor` builds an empty `panel_sessions`, `_form_window` returns `()` for the session axis
rather than `None`, and every history question is decided on `_period_span`'s fiscal-quarter grid.
`test_the_four_factors_compute_off_the_period_axis_alone_and_give_four_different_numbers` is where
that becomes a measurement.

## The window is the **union** of the report periods of every statement dataset the factor reads

`_points_held` unions the axis's points across datasets and `_complete_series` then requires every
one of them to be populated in every dataset. No shipped factor had exercised that across two
statement endpoints; `accruals_ttm` reads three. `test_a_security_missing_one_balance_sheet_filing
_is_input_missing_for_the_factors_that_read_it` drives the consequence: a security whose `income`
history is whole and whose `balancesheet` history is one period short is `input_missing` for the
three factors that read a balance sheet and `computed` for the one that does not.

## Two reaches on one corpus, and the coverage they cost

| security | what it is for |
|---|---|
| `000001.SZ` | the computable case, all four factors |
| `601318.SH` | a **financial**: null `oper_cost` and `total_cur_liab`, so two are `input_missing` |
| `000795.SZ` | **negative equity**: `undefined_value` here, computed and negative for BP |
| `002594.SZ` | `total_assets == total_cur_liab`: zero capital employed, one factor undefined |
| `600000.SH` | five filings: three factors compute, the stability is short on the **count** |
| `000063.SZ` | eight filings spanning **nine** quarters: short on the **span**, count clear |
| `300750.SZ` | a whole `income` history and one missing `balancesheet` filing |
| `600030.SH` | a null `n_cashflow_act`: `input_missing` for `accruals_ttm` alone |
| `601988.SH` | outside the universe |

`600000.SH` and `000063.SZ` are the pair the `V2-P3-004` review's lesson asks for at the wider
reach: the first is short on the count at an unbroken span and the second is whole on the count at
a broken span.

## Four factors over three statement endpoints

Three of the four divide a trailing flow by a balance-sheet stock, and two of those three flows are
`n_income` -- which is exactly the shape in which a fixture stops discriminating. So the partition
carries a **different series in every column any of the four could have read**: `n_income` is 1.7
times `n_income_attr_p`, `revenue` is half of `total_revenue`, `total_liab` is not
`total_cur_liab`, `free_cashflow` is not `n_cashflow_act`, and the three balance-sheet stocks drift
period by period so that `[0]` and `[-1]` are different cells.

## The identity that ties this family to `V2-P3-009`'s

`earnings_yield_ttm / book_to_price` is `return_on_equity_ttm`, because the market capitalisation
cancels -- and that is why the denominator is the window's closing equity rather than the average
of its ends. Through the engine the three factors form **three different windows** (five income
periods, one balance-sheet period, five of the union), so the identity holding is a statement about
the engine's window formation and not only about three formulae.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATA_COLUMNS, DAILY_BASIC_DATASET
from openalpha_cn.domain.factor import FactorDefinition
from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    INCOME_DATASET,
    STATEMENT_DATA_COLUMNS,
    statement_panel_columns,
)
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    ACCRUALS_TTM,
    BOOK_TO_PRICE,
    CAPITAL_TURNOVER_PERIODS,
    EARNINGS_YIELD_TTM,
    GROSS_MARGIN_OBSERVATIONS,
    GROSS_MARGIN_PERIODS,
    GROSS_MARGIN_STABILITY,
    RETURN_ON_CAPITAL_TTM,
    RETURN_ON_EQUITY_TTM,
    TRAILING_TWELVE_MONTH_PERIODS,
    FactorEngineError,
    FactorPanel,
    _sample_stdev,
    compute_factor,
    load_factor_manifests,
    load_factor_observations,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import financial_statement_requirement, write_panel_batch

SHANGHAI_OFFSET: Final[timedelta] = timedelta(hours=8)

AS_OF: Final[datetime] = datetime(2025, 5, 20, 4, 0, tzinfo=UTC)
BUILT_AT: Final[datetime] = datetime(2025, 6, 1, 9, 0, tzinfo=UTC)
COMMIT: Final[str] = "c3f9a17"

STATEMENT_STALENESS: Final[timedelta] = timedelta(days=120)
SESSION_STALENESS: Final[timedelta] = timedelta(days=7)

STATEMENT_YEARS: Final[tuple[int, ...]] = (2023, 2024, 2025)
"""The **announcement** years these filings land in: nine report periods, three partitions.

Three rather than `V2-P3-009`'s two, and that is the report-period axis's own cost showing up in
the request: `gross_margin_stability` reaches eight report periods and a statement partition is
filed by announcement year, so the years a caller has to name grow faster than the reach does.
`compute_factor`'s own refusal names them for that reason.
"""

SESSION_YEAR: Final[int] = 2025
SESSIONS: Final[tuple[date, ...]] = (date(2025, 5, 15), date(2025, 5, 16), date(2025, 5, 19))
"""Three `daily_basic` sessions, written only for the `EP / BP` identity test: the four factors
this file is about read no session at all."""

PERIODS: Final[tuple[date, ...]] = (
    date(2023, 3, 31),
    date(2023, 6, 30),
    date(2023, 9, 30),
    date(2023, 12, 31),
    date(2024, 3, 31),
    date(2024, 6, 30),
    date(2024, 9, 30),
    date(2024, 12, 31),
    date(2025, 3, 31),
)
"""Nine contiguous report periods. The last eight are `gross_margin_stability`'s window and the
last five are the other three factors'."""

ANNOUNCED_ON: Final[dict[date, date]] = {
    date(2023, 3, 31): date(2023, 4, 20),
    date(2023, 6, 30): date(2023, 8, 25),
    date(2023, 9, 30): date(2023, 10, 25),
    date(2023, 12, 31): date(2024, 4, 20),
    date(2024, 3, 31): date(2024, 4, 20),
    date(2024, 6, 30): date(2024, 8, 25),
    date(2024, 9, 30): date(2024, 10, 25),
    date(2024, 12, 31): date(2025, 4, 20),
    date(2025, 3, 31): date(2025, 4, 20),
}
"""When each period was disclosed. Each annual and the following Q1 land on **one day**, which is
what an A-share issuer routinely does and what the report-period axis exists to hold."""

MARGIN_WINDOW: Final[tuple[date, ...]] = PERIODS[-GROSS_MARGIN_PERIODS:]
TRAILING_WINDOW: Final[tuple[date, ...]] = PERIODS[-CAPITAL_TURNOVER_PERIODS:]

PROFIT_INCREMENTS: Final[tuple[float, ...]] = (
    11.0,
    23.0,
    37.0,
    53.0,
    71.0,
    97.0,
    113.0,
    139.0,
    151.0,
)
REVENUE_INCREMENTS: Final[tuple[float, ...]] = (
    101.0,
    211.0,
    307.0,
    419.0,
    521.0,
    631.0,
    743.0,
    859.0,
    971.0,
)
COST_INCREMENTS: Final[tuple[float, ...]] = (
    61.0,
    139.0,
    173.0,
    271.0,
    293.0,
    401.0,
    409.0,
    571.0,
    607.0,
)
CASH_INCREMENTS: Final[tuple[float, ...]] = (
    7.0,
    19.0,
    29.0,
    41.0,
    59.0,
    79.0,
    101.0,
    127.0,
    139.0,
)
"""One quarter at a time, 2023Q1 through 2025Q1. The partition carries the *cumulative* figures
these accumulate into, which is what a stored A-share statement holds, so the fixture and every
expectation in this file share only these four tables.

The cost series is deliberately not a fixed share of the revenue one: a constant gross margin would
make `gross_margin_stability` zero and every assertion about it vacuous.
"""

CONSOLIDATED_MULTIPLE: Final[float] = 1.7
REVENUE_SHARE: Final[float] = 0.5
FREE_CASH_SHARE: Final[float] = 0.4
"""The three separations that make a wrong column a wrong number rather than the same one.

`n_income` is 1.7 times `n_income_attr_p`, `revenue` is half of `total_revenue`, and
`free_cashflow` is 0.4 of `n_cashflow_act` -- so a factor pointed at any of the three neighbours
this family argues about lands somewhere else.
"""

EQUITY_BASE: Final[float] = 4_000.0
ASSET_BASE: Final[float] = 9_500.0
CURRENT_LIABILITY_BASE: Final[float] = 2_750.0
LIABILITY_BASE: Final[float] = 5_100.0
STOCK_DRIFT: Final[float] = 41.0
"""How much each balance-sheet stock moves per period, so `[0]` and `[-1]` are different cells.

`total_liab` is not `total_cur_liab` and neither is `ASSET_BASE - EQUITY_BASE`, so a capital
employed built from the wrong liability column lands elsewhere.
"""

FULL: Final[str] = "000001.SZ"
FINANCIAL: Final[str] = "601318.SH"
INSOLVENT: Final[str] = "000795.SZ"
ZERO_CAPITAL: Final[str] = "002594.SZ"
SHORT: Final[str] = "600000.SH"
GAP: Final[str] = "000063.SZ"
MISSING_BALANCE: Final[str] = "300750.SZ"
NULL_CASH: Final[str] = "600030.SH"
OUTSIDE: Final[str] = "601988.SH"

SUBJECTS: Final[tuple[str, ...]] = (
    FULL,
    FINANCIAL,
    INSOLVENT,
    ZERO_CAPITAL,
    SHORT,
    GAP,
    MISSING_BALANCE,
    NULL_CASH,
    OUTSIDE,
)
UNIVERSE: Final[frozenset[str]] = frozenset(SUBJECTS) - {OUTSIDE}

MARKET_CAP: Final[float] = 900.0
CNY_PER_MARKET_CAP_UNIT: Final[float] = 10_000.0
"""`daily_basic.total_mv`'s unit, restated here rather than imported for the reason
`tests/unit/test_factor_value_family.py` restates its own constants: this file's `EP / BP` identity
has to be derivable from the fixture without asking the module under test what the unit is.
"""


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=UTC) - SHANGHAI_OFFSET


def _session_instant(day: date) -> datetime:
    return datetime.combine(day, time(16, 30), tzinfo=UTC) - SHANGHAI_OFFSET


def _cumulative(increments: tuple[float, ...], period: date) -> float:
    """What a stored A-share statement carries at `period`: the fiscal year to date."""
    index = PERIODS.index(period)
    start = 0 if period.year == 2023 else (4 if period.year == 2024 else 8)
    return math.fsum(increments[start : index + 1])


def _trailing(increments: tuple[float, ...], period: date) -> float:
    """The plain sum of the four quarterly increments ending at `period`.

    The independent derivation every expectation here is built from: it shares only the increment
    tables with the cumulative series the partition carries.
    """
    index = PERIODS.index(period)
    return math.fsum(increments[index - 3 : index + 1])


def _expected_margins() -> tuple[float, ...]:
    """The four trailing gross margins `gross_margin_stability` should be taking a dispersion of."""
    return tuple(
        (_trailing(REVENUE_INCREMENTS, period) - _trailing(COST_INCREMENTS, period))
        / _trailing(REVENUE_INCREMENTS, period)
        for period in MARGIN_WINDOW[-GROSS_MARGIN_OBSERVATIONS:]
    )


def _periods_of(subject: str) -> tuple[date, ...]:
    """Which report periods each security filed for, on every statement dataset it files at all."""
    if subject == SHORT:
        return TRAILING_WINDOW
    if subject == GAP:
        # Eight filings, and the second period is missing -- so the count clears at the wider reach
        # and the eight span **nine** fiscal quarters. The newest five stay contiguous, so the
        # five-period factors compute for the same security in the same build.
        return (PERIODS[0], *PERIODS[2:])
    return PERIODS


def _balance_periods(subject: str) -> tuple[date, ...]:
    if subject == MISSING_BALANCE:
        return _periods_of(subject)[:-1]
    return _periods_of(subject)


def _equity(subject: str, period: date) -> float:
    base = -6_000.0 if subject == INSOLVENT else EQUITY_BASE
    return base + STOCK_DRIFT * PERIODS.index(period)


def _assets(subject: str, period: date) -> float:
    return ASSET_BASE + STOCK_DRIFT * PERIODS.index(period)


def _current_liabilities(subject: str, period: date) -> float | None:
    if subject == FINANCIAL:
        # A bank, an insurer and a broker publish no current / non-current split. Measured on the
        # served rows: null on 68 of 68, 67 of 67 and 64 of 64 stored periods since 2015 for
        # 000001.SZ, 601318.SH and 600030.SH.
        return None
    if subject == ZERO_CAPITAL:
        return _assets(subject, period)
    return CURRENT_LIABILITY_BASE + STOCK_DRIFT * PERIODS.index(period)


def _income_values(subject: str, period: date) -> dict[str, float | None]:
    """One `income` row, with a different series in every column this family could have read."""
    profit = _cumulative(PROFIT_INCREMENTS, period)
    revenue = _cumulative(REVENUE_INCREMENTS, period)
    cost: float | None = _cumulative(COST_INCREMENTS, period)
    if subject == FINANCIAL:
        # `tests/contract/providers/test_tushare_financials.py` records the reason in its own
        # fixture comment: a bank publishes no cost of sales.
        cost = None
    return {
        "total_revenue": revenue,
        "revenue": revenue * REVENUE_SHARE,
        "oper_cost": cost,
        "operate_profit": 5.0,
        "total_profit": 7.0,
        "income_tax": 11.0,
        "n_income": profit * CONSOLIDATED_MULTIPLE,
        "n_income_attr_p": profit,
        "basic_eps": 0.13,
        "ebit": 17.0,
    }


def _balance_values(subject: str, period: date) -> dict[str, float | None]:
    return {
        "total_assets": _assets(subject, period),
        "total_liab": LIABILITY_BASE + STOCK_DRIFT * PERIODS.index(period),
        "total_hldr_eqy_exc_min_int": _equity(subject, period),
        "total_cur_assets": 19.0,
        "total_cur_liab": _current_liabilities(subject, period),
        "money_cap": 29.0,
        "total_share": 1_234.0,
    }


def _cash_values(subject: str, period: date) -> dict[str, float | None]:
    operating: float | None = _cumulative(CASH_INCREMENTS, period)
    if subject == NULL_CASH and period == _periods_of(subject)[-1]:
        operating = None
    return {
        "n_cashflow_act": operating,
        "n_cashflow_inv_act": 3.0,
        "n_cash_flows_fnc_act": 5.0,
        "c_fr_sale_sg": 7.0,
        "free_cashflow": (operating or 0.0) * FREE_CASH_SHARE,
    }


_VALUES = {
    INCOME_DATASET: _income_values,
    BALANCE_SHEET_DATASET: _balance_values,
    CASH_FLOW_DATASET: _cash_values,
}


def _statement_rows(dataset: str) -> tuple[tuple[str, date, date, dict[str, float | None]], ...]:
    periods = _balance_periods if dataset == BALANCE_SHEET_DATASET else _periods_of
    values = _VALUES[dataset]
    return tuple(
        (subject, period, ANNOUNCED_ON[period], values(subject, period))
        for subject in SUBJECTS
        for period in periods(subject)
    )


def _statement_batch(
    dataset: str, rows: tuple[tuple[str, date, date, dict[str, float | None]], ...]
) -> ColumnarPanelBatch:
    """`(subject, period, announcement, values)` through one endpoint's own projection and clock."""
    announced = tuple(_midnight(item[2]) for item in rows)
    columns = (
        PanelColumn("report_period", "string", tuple(item[1].isoformat() for item in rows)),
        PanelColumn("ann_date", "string", tuple(item[2].isoformat() for item in rows)),
        PanelColumn("f_ann_date", "string", tuple(item[2].isoformat() for item in rows)),
        PanelColumn("update_flag", "string", tuple("1" for _ in rows)),
        *(
            PanelColumn(name, "float", tuple(item[3].get(name) for item in rows))
            for name in STATEMENT_DATA_COLUMNS[dataset]
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


def _write_statements(
    built: PanelStore,
    dataset: str,
    rows: tuple[tuple[str, date, date, dict[str, float | None]], ...],
) -> None:
    for year in STATEMENT_YEARS:
        yearly = tuple(item for item in rows if item[2].year == year)
        if yearly:
            write_panel_batch(built, _statement_batch(dataset, yearly), year=year)


def _daily_basic_batch() -> ColumnarPanelBatch:
    subjects: list[str] = []
    days: list[date] = []
    for subject in SUBJECTS:
        for day in SESSIONS:
            subjects.append(subject)
            days.append(day)
    instants = tuple(_session_instant(day) for day in days)
    constant = tuple(1.0 for _ in days)
    moved = {
        "trade_date": PanelColumn("trade_date", "string", tuple(day.isoformat() for day in days)),
        "total_mv": PanelColumn("total_mv", "float", tuple(MARKET_CAP for _ in days)),
    }
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
        columns=tuple(
            moved.get(name, PanelColumn(name, "float", constant))
            for name in DAILY_BASIC_DATA_COLUMNS
        ),
    )


@pytest.fixture
def store(tmp_path: Path) -> PanelStore:
    built = PanelStore(tmp_path / "panel")
    for dataset in (INCOME_DATASET, BALANCE_SHEET_DATASET, CASH_FLOW_DATASET):
        _write_statements(built, dataset, _statement_rows(dataset))
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


def _compute(built: PanelStore, definition: FactorDefinition, **overrides: Any) -> FactorPanel:
    settings: dict[str, Any] = {
        "as_of": AS_OF,
        "subjects": SUBJECTS,
        "universe": UNIVERSE,
        "requirements": {name: _requirement(name) for name in definition.datasets},
        "code_commit": COMMIT,
        "built_at": BUILT_AT,
        **overrides,
    }
    return compute_factor(built, definition, **settings)


def _coverage(panel: FactorPanel) -> dict[str, str]:
    return {item.subject: item.coverage for item in panel.observations}


def _value(panel: FactorPanel, subject: str) -> float:
    values = panel.values()
    assert subject in values, f"{subject} is {_coverage(panel)[subject]}"
    return values[subject]


QUALITY_FAMILY: Final[tuple[FactorDefinition, ...]] = (
    RETURN_ON_EQUITY_TTM,
    RETURN_ON_CAPITAL_TTM,
    GROSS_MARGIN_STABILITY,
    ACCRUALS_TTM,
)


# --- the four values, off one axis ----------------------------------------------------------------


def test_the_four_factors_compute_off_the_period_axis_alone_and_give_four_different_numbers(
    store: PanelStore,
) -> None:
    """The whole family through the real engine, each value pinned as a number.

    Every expectation is derived from the increment tables the partition was written from, so these
    are restatements of the fixture rather than numbers copied out of a run. The four are asserted
    pairwise distinct because three of them divide a trailing flow by a balance-sheet stock -- and
    the partition carries a different series in every neighbouring column, so an evaluator pointed
    at `n_income_attr_p` where it wanted `n_income`, at `total_liab` where it wanted
    `total_cur_liab`, or at `free_cashflow` where it wanted `n_cashflow_act`, lands somewhere else.

    **That the read happens on one axis is the part only a partition can say.** These are the first
    shipped factors whose `lookback_sessions` is `None`, so the engine forms an empty session window
    and every reach question is decided on the period grid. A definition can declare that; only a
    build can show it answering.
    """
    newest = TRAILING_WINDOW[-1]
    trailing_profit = _trailing(PROFIT_INCREMENTS, newest)
    trailing_cash = _trailing(CASH_INCREMENTS, newest)
    equity = _equity(FULL, newest)
    assets = _assets(FULL, newest)
    current = _current_liabilities(FULL, newest)
    assert current is not None
    margins = _sample_stdev(_expected_margins())
    assert margins is not None

    answers = {
        definition.key: _value(_compute(store, definition), FULL) for definition in QUALITY_FAMILY
    }

    assert answers["return_on_equity_ttm"] == pytest.approx(trailing_profit / equity)
    assert answers["return_on_capital_ttm"] == pytest.approx(
        trailing_profit * CONSOLIDATED_MULTIPLE / (assets - current)
    )
    assert answers["accruals_ttm"] == pytest.approx(
        (trailing_profit * CONSOLIDATED_MULTIPLE - trailing_cash) / assets
    )
    assert answers["gross_margin_stability"] == pytest.approx(margins)
    assert len({round(value, 12) for value in answers.values()}) == 4

    # The neighbouring columns this partition separates, each a number a wrong reader would have
    # produced and none of them the answer.
    assert answers["return_on_capital_ttm"] != pytest.approx(
        trailing_profit / (assets - current)
    ), "the attributable profit where the consolidated one belongs"
    assert answers["return_on_capital_ttm"] != pytest.approx(
        trailing_profit
        * CONSOLIDATED_MULTIPLE
        / (assets - (LIABILITY_BASE + STOCK_DRIFT * PERIODS.index(newest)))
    ), "total_liab where total_cur_liab belongs"
    assert answers["accruals_ttm"] != pytest.approx(
        (trailing_profit * CONSOLIDATED_MULTIPLE - trailing_cash * FREE_CASH_SHARE) / assets
    ), "free_cashflow where n_cashflow_act belongs"
    assert answers["return_on_equity_ttm"] != pytest.approx(
        trailing_profit / _equity(FULL, TRAILING_WINDOW[0])
    ), "the oldest cell of a drifting stock series"


def test_every_factor_here_declares_no_session_reach_and_the_stored_row_says_so(
    store: PanelStore,
) -> None:
    """The axis, as it survives to Parquet and back.

    A factor on one axis leaves the other window pair null on the stored observation, and every
    shipped factor before `V2-P3-010` left the *period* pair null. This is the first family that
    leaves the **session** pair null, so a writer that filled it from a default, or that swapped
    the two pairs, was invisible in storage until now.
    """
    write_factor_panels(store, [_compute(store, RETURN_ON_EQUITY_TTM)])
    observations = load_factor_observations(
        store, RETURN_ON_EQUITY_TTM, years=(AS_OF.year,), as_of=AS_OF
    )
    computed = next(item for item in observations if item.subject == FULL)

    assert computed.coverage == "computed"
    assert computed.value == pytest.approx(
        _trailing(PROFIT_INCREMENTS, TRAILING_WINDOW[-1]) / _equity(FULL, TRAILING_WINDOW[-1])
    )
    assert computed.input_session_first is None
    assert computed.input_session_last is None
    assert computed.input_period_first == TRAILING_WINDOW[0]
    assert computed.input_period_last == TRAILING_WINDOW[-1]
    # Five income filings and five balance-sheet filings over the union window, and no session row.
    assert computed.input_row_count == 2 * CAPITAL_TURNOVER_PERIODS


def test_the_trailing_numerator_is_not_the_latest_cumulative_filing(store: PanelStore) -> None:
    """The whole content of the TTM choice, on disk.

    The newest filing this corpus holds is a 2025 Q1 report, whose stored `n_income_attr_p` is one
    quarter of profit. A factor that read the latest cumulative figure -- which is what "the latest
    filing knowable at `as_of`" means if nobody accumulates -- would answer that, and it is the
    quantity `fina_indicator.roe` is a ratio of. Reading the last annual instead is the other near
    miss and is separated too.
    """
    newest = TRAILING_WINDOW[-1]
    equity = _equity(FULL, newest)
    answer = _value(_compute(store, RETURN_ON_EQUITY_TTM), FULL)

    assert answer == pytest.approx(_trailing(PROFIT_INCREMENTS, newest) / equity)
    assert answer != pytest.approx(_cumulative(PROFIT_INCREMENTS, newest) / equity)
    assert answer != pytest.approx(_cumulative(PROFIT_INCREMENTS, date(2024, 12, 31)) / equity)


def test_the_return_on_equity_is_the_earnings_yield_over_the_book_to_price(
    store: PanelStore,
) -> None:
    """The identity that decides this family's denominator, through three windows the engine forms.

    `earnings_yield_ttm` divides this trailing profit by a market capitalisation over a **five**
    income-period window and a one-session window; `book_to_price` divides this closing equity by
    the same capitalisation over a **one** balance-sheet-period window; `return_on_equity_ttm`
    divides the first numerator by the second over a **five**-period window that is the union of
    the two statement datasets. The capitalisation cancels, so the quotient is this factor exactly
    -- and that is why the denominator is the window's closing equity rather than the average of
    its ends, which would break it and leave the repository carrying two incompatible statements of
    what a book value is.

    Three different windows is what makes this an engine test rather than an arithmetic one: the
    identity holds only because `_form_window` takes the most recent points on each axis, so all
    three land on the same newest filing.
    """
    earnings = _value(_compute(store, EARNINGS_YIELD_TTM), FULL)
    book = _value(_compute(store, BOOK_TO_PRICE), FULL)
    quality = _value(_compute(store, RETURN_ON_EQUITY_TTM), FULL)
    newest = TRAILING_WINDOW[-1]

    assert earnings == pytest.approx(
        _trailing(PROFIT_INCREMENTS, newest) / (MARKET_CAP * CNY_PER_MARKET_CAP_UNIT)
    )
    assert book == pytest.approx(_equity(FULL, newest) / (MARKET_CAP * CNY_PER_MARKET_CAP_UNIT))
    assert quality == pytest.approx(earnings / book)
    # And the averaged denominator this family declines lands somewhere else on this corpus.
    average = (_equity(FULL, newest) + _equity(FULL, TRAILING_WINDOW[0])) / 2.0
    assert quality != pytest.approx(_trailing(PROFIT_INCREMENTS, newest) / average)
    assert EARNINGS_YIELD_TTM.lookback_periods == TRAILING_TWELVE_MONTH_PERIODS
    assert BOOK_TO_PRICE.lookback_periods == 1
    assert RETURN_ON_EQUITY_TTM.lookback_periods == CAPITAL_TURNOVER_PERIODS


# --- the coverage codes ---------------------------------------------------------------------------


def test_a_financial_issuer_is_input_missing_for_exactly_the_two_factors_that_need_its_splits(
    store: PanelStore,
) -> None:
    """The coverage disclosure two of these four make, end to end on a partition.

    `601318.SH` publishes no cost of sales and no current / non-current balance-sheet split, which
    is the shape the served rows really have: over every stored period since 2015, `oper_cost` is
    null on 57 of 57 of its `income` rows and `total_cur_liab` on 67 of 67 of its `balancesheet`
    ones. So `gross_margin_stability` and `return_on_capital_ttm` answer `input_missing` -- the
    right answer for a company type whose gross margin and capital employed are not defined
    quantities -- while `return_on_equity_ttm` and `accruals_ttm` compute for the same security in
    the same build, which is what makes this a fact about two columns rather than about one name.
    """
    coverage = {
        definition.key: _coverage(_compute(store, definition))[FINANCIAL]
        for definition in QUALITY_FAMILY
    }

    assert coverage == {
        "return_on_equity_ttm": "computed",
        "return_on_capital_ttm": "input_missing",
        "gross_margin_stability": "input_missing",
        "accruals_ttm": "computed",
    }


def test_a_negative_equity_is_undefined_for_return_on_equity_and_computed_for_book_to_price(
    store: PanelStore,
) -> None:
    """The same column, two factors, two rules -- driven through the engine over one partition.

    `000795.SZ` has negative equity in every period. As `book_to_price`'s **numerator** that is a
    real answer and the security ranks below every solvent name, which is the ordering a
    book-to-price should have. As `return_on_equity_ttm`'s **denominator** it would turn a positive
    trailing profit into a negative return on equity and rank the insolvent issuer below a
    loss-maker, so it is `undefined_value` instead. The two are asserted in one test because the
    claim is that the difference is a choice rather than an inconsistency.
    """
    newest = TRAILING_WINDOW[-1]
    equity = _equity(INSOLVENT, newest)
    assert equity < 0.0
    assert _trailing(PROFIT_INCREMENTS, newest) > 0.0

    book = _compute(store, BOOK_TO_PRICE)
    quality = _compute(store, RETURN_ON_EQUITY_TTM)

    assert _value(book, INSOLVENT) == pytest.approx(equity / (MARKET_CAP * CNY_PER_MARKET_CAP_UNIT))
    assert _value(book, INSOLVENT) < 0.0
    assert _coverage(quality)[INSOLVENT] == "undefined_value"
    assert _coverage(quality)[FULL] == "computed"
    # The other three are unaffected, so the refusal is this factor's denominator and not the row.
    for definition in (RETURN_ON_CAPITAL_TTM, GROSS_MARGIN_STABILITY, ACCRUALS_TTM):
        assert _coverage(_compute(store, definition))[INSOLVENT] == "computed"


def test_a_zero_capital_employed_is_undefined_for_the_one_factor_that_divides_by_it(
    store: PanelStore,
) -> None:
    """`undefined_value` provoked with both of its terms positive, which a guard on one would miss.

    `002594.SZ`'s `total_cur_liab` equals its `total_assets` in every period, so capital employed is
    exactly zero while every stored cell is an ordinary positive number that this repository's own
    writers accept. Only `return_on_capital_ttm` divides by that difference; `accruals_ttm` divides
    by `total_assets` alone and computes for the same security in the same build.
    """
    newest = TRAILING_WINDOW[-1]
    assert _current_liabilities(ZERO_CAPITAL, newest) == _assets(ZERO_CAPITAL, newest)

    for definition in QUALITY_FAMILY:
        coverage = _coverage(_compute(store, definition))
        expected = "undefined_value" if definition is RETURN_ON_CAPITAL_TTM else "computed"

        assert coverage[ZERO_CAPITAL] == expected, definition.key
        assert coverage[FULL] == "computed", definition.key


def test_the_eight_period_reach_and_the_five_period_reach_answer_differently_for_one_security(
    store: PanelStore,
) -> None:
    """The coverage difference inside the family, which is the cost of the wider window.

    `600000.SH` has filed five times. That is `CAPITAL_TURNOVER_PERIODS`, so the three flow-over-
    stock factors compute; it is fewer than `GROSS_MARGIN_PERIODS`, so `gross_margin_stability` is
    `insufficient_history` and records no period window at all. The same security, the same
    partition, the same `as_of` -- so the reach is separable from everything else about the build.
    """
    assert len(_periods_of(SHORT)) == CAPITAL_TURNOVER_PERIODS < GROSS_MARGIN_PERIODS
    stability = _compute(store, GROSS_MARGIN_STABILITY)

    assert _coverage(stability)[SHORT] == "insufficient_history"
    for definition in (RETURN_ON_EQUITY_TTM, RETURN_ON_CAPITAL_TTM, ACCRUALS_TTM):
        assert _coverage(_compute(store, definition))[SHORT] == "computed", definition.key

    short_row = next(item for item in stability.observations if item.subject == SHORT)
    assert short_row.input_period_first is None
    assert short_row.input_period_last is None


def test_a_missed_filing_is_the_span_bound_and_not_the_count_at_the_wider_reach(
    store: PanelStore,
) -> None:
    """`max_window_periods` separated from `lookback_periods` on the eight-period factor.

    `000063.SZ` has filed exactly eight times, so the count clears; the eight span **nine** fiscal
    quarters, because the second period is missing. That is the case the slicing argument cannot
    survive -- a slice's `[:-1]` would hold no fiscal year end or two -- and `_overruns_its_span`
    refuses it before an evaluator is reached.

    Its newest five filings are contiguous, so the same security computes for the three five-period
    factors in the same build. That is what makes this the span bound rather than a fact about the
    security, and it is the pair `600000.SH` above completes from the other side.
    """
    filed = _periods_of(GAP)
    assert len(filed) == GROSS_MARGIN_PERIODS
    assert PERIODS[1] not in filed
    assert filed[-CAPITAL_TURNOVER_PERIODS:] == TRAILING_WINDOW

    coverage = _coverage(_compute(store, GROSS_MARGIN_STABILITY))

    assert coverage[GAP] == "insufficient_history"
    assert coverage[FULL] == "computed"
    for definition in (RETURN_ON_EQUITY_TTM, RETURN_ON_CAPITAL_TTM, ACCRUALS_TTM):
        assert _coverage(_compute(store, definition))[GAP] == "computed", definition.key


def test_a_security_missing_one_balance_sheet_filing_is_input_missing_for_what_reads_it(
    store: PanelStore,
) -> None:
    """The union window, which no shipped factor had exercised across two statement endpoints.

    `_points_held` unions the report periods of every period-indexed dataset a factor reads, and
    `_complete_series` then requires every dataset to have a row at every point of the window. So
    `300750.SZ`, whose `income` history is whole and whose `balancesheet` history stops one period
    early, is `input_missing` for the three factors that read a balance sheet -- the window is
    formed from the income periods and the balance sheet has no row at the newest of them -- and
    `computed` for `gross_margin_stability`, which reads `income` alone.

    That is the difference between a factor's window being *its own* datasets' union and being one
    dataset's, and it is the reason `_QUALITY_AXIS_PROSE` states it in every note here.
    """
    assert _balance_periods(MISSING_BALANCE) == PERIODS[:-1]
    assert _periods_of(MISSING_BALANCE) == PERIODS

    for definition in (RETURN_ON_EQUITY_TTM, RETURN_ON_CAPITAL_TTM, ACCRUALS_TTM):
        assert _coverage(_compute(store, definition))[MISSING_BALANCE] == "input_missing", (
            definition.key
        )
    assert _coverage(_compute(store, GROSS_MARGIN_STABILITY))[MISSING_BALANCE] == "computed"


def test_a_null_operating_cash_flow_is_input_missing_for_the_accrual_alone(
    store: PanelStore,
) -> None:
    """`input_missing` on the third dataset, with the other three factors unaffected.

    `600030.SH`'s newest `cashflow` row carries a null in the one column `accruals_ttm` reads, so
    the window cannot be completed and the remedy is a fetch. Its `income` and `balancesheet` rows
    are whole, so the other three compute -- which is what makes this an answer about a column
    rather than about a security, and what makes `accruals_ttm`'s third dataset a real cost rather
    than a nominal one.
    """
    assert _cash_values(NULL_CASH, _periods_of(NULL_CASH)[-1])["n_cashflow_act"] is None

    assert _coverage(_compute(store, ACCRUALS_TTM))[NULL_CASH] == "input_missing"
    for definition in (RETURN_ON_EQUITY_TTM, RETURN_ON_CAPITAL_TTM, GROSS_MARGIN_STABILITY):
        assert _coverage(_compute(store, definition))[NULL_CASH] == "computed", definition.key


def test_a_security_outside_the_universe_is_not_in_universe_on_every_factor(
    store: PanelStore,
) -> None:
    """The code that must not be confused with a data fault, on a security whose rows are all
    present: `601988.SH` has every filing and is simply not in the cross section the caller
    declared."""
    for definition in QUALITY_FAMILY:
        panel = _compute(store, definition)
        observation = next(item for item in panel.observations if item.subject == OUTSIDE)

        assert observation.coverage == "not_in_universe", definition.key
        assert observation.input_row_count == 0
        assert observation.input_period_first is None
        assert observation.input_session_first is None


def test_a_request_that_names_too_few_announcement_years_is_refused_rather_than_answered_emptily(
    store: PanelStore,
) -> None:
    """The refusal `compute_factor` makes when the panel cannot satisfy a reach for anybody.

    A statement partition is filed by **announcement** year while a reach is counted in report
    periods, so the eight-period factor here needs three announcement years named and a caller who
    names one gets a visible panel of at most three report periods. Every observation would be
    `insufficient_history`, which is a fault in the request rather than an answer about the data --
    and the message leads with the years for that reason.

    This is the report-period half of a guard whose session half `V2-P3-002` measured; it has a
    longer lever here, which is why `GROSS_MARGIN_PERIODS` is the first reach to make it easy to
    trip.
    """
    narrow = {
        name: financial_statement_requirement(
            dataset=name, years=(2025,), as_of=AS_OF, max_staleness=STATEMENT_STALENESS
        )
        for name in GROSS_MARGIN_STABILITY.datasets
    }

    with pytest.raises(FactorEngineError, match=r"needs 8 report periods"):
        _compute(store, GROSS_MARGIN_STABILITY, requirements=narrow)


# --- the duplicate rows, at this family's own reaches ---------------------------------------------


DISPUTED_ASSETS: Final[tuple[float, float]] = (1_684_196_409_372.7, 1_281_551_927_215.46)
"""Two `total_assets` figures for one filing.

`balancesheet.total_assets` is the column `domain/financial_statements.py` recorded losing **0**
reads in its 53-security corpus and 18 in a 76-security one; `V2-P3-010`'s own 93-security probe
reads it back at 16 of 5,250 filings. The two numbers here are real `000002.SZ` 2023H1 figures --
its `total_assets` and its `total_liab` -- used as a pair that differs by 24%, which is far wider
than any rounding and is the point: a build that picked one would be 24% wrong about the
denominator of two of these four factors.
"""


@pytest.fixture
def disputed_store(tmp_path: Path) -> PanelStore:
    """One partition whose newest `balancesheet` filing arrives as two disagreeing rows."""
    built = PanelStore(tmp_path / "disputed")
    for dataset in (INCOME_DATASET, CASH_FLOW_DATASET):
        _write_statements(built, dataset, _statement_rows(dataset))
    rows = list(_statement_rows(BALANCE_SHEET_DATASET))
    newest = TRAILING_WINDOW[-1]
    for index, item in enumerate(rows):
        if item[0] == FULL and item[1] == newest:
            rows[index] = (
                item[0],
                item[1],
                item[2],
                {**item[3], "total_assets": DISPUTED_ASSETS[0]},
            )
    rows.append(
        (
            FULL,
            newest,
            ANNOUNCED_ON[newest],
            {**_balance_values(FULL, newest), "total_assets": DISPUTED_ASSETS[1]},
        )
    )
    _write_statements(built, BALANCE_SHEET_DATASET, tuple(rows))
    return built


def test_the_engine_answers_a_column_the_duplicate_rows_agree_about_and_codes_one_they_do_not(
    disputed_store: PanelStore,
) -> None:
    """Per-field ambiguity at this family's reaches, on the column two of its members share.

    The newest `balancesheet` filing arrives as two rows that agree about
    `total_hldr_eqy_exc_min_int` and disagree about `total_assets` by 24%. So
    `return_on_equity_ttm`, which reads the equity, computes off the same partition on which
    `return_on_capital_ttm` and `accruals_ttm` report `ambiguous_filing` -- decided by which
    column each factor reads, over one partition.

    **`V2-P3-018` is what this test used to be the small case of, and now is the delivery of.**
    The two refusing factors used to raise `FactorEngineError` and produce no observation for
    anybody; they now produce a panel in which this security carries a code and every other one is
    unaffected. This family is the sharper case for it than the value family:
    `accruals_ttm` reads three statement datasets over five contiguous periods and
    `gross_margin_stability` reads eight, so a build here meets more `(filing, column)` reads and
    therefore more ambiguity than one that reads a filing and a price.

    Asserted as a mapping over all three factors rather than as one positive and a loop of
    raises, so a change that made every factor answer `ambiguous_filing` cannot pass.
    """
    answers = {
        definition.key: _coverage(
            compute_factor(
                disputed_store,
                definition,
                as_of=AS_OF,
                subjects=(FULL,),
                universe=frozenset({FULL}),
                requirements={name: _requirement(name) for name in definition.datasets},
                code_commit=COMMIT,
                built_at=BUILT_AT,
            )
        )[FULL]
        for definition in (RETURN_ON_EQUITY_TTM, RETURN_ON_CAPITAL_TTM, ACCRUALS_TTM)
    }

    assert answers == {
        "return_on_equity_ttm": "computed",
        "return_on_capital_ttm": "ambiguous_filing",
        "accruals_ttm": "ambiguous_filing",
    }


def test_a_multi_dataset_build_reads_its_own_manifest_back(store: PanelStore) -> None:
    """The round trip that could not close, found by putting a manifest read on the value path.

    `FactorBuildManifest.inputs` is a tuple, so its order is inside `manifest_id`. `compute_factor`
    used to collect its refs in `FactorDefinition.datasets` order while `_manifest_from_rows` has
    always reassembled them sorted by `(dataset, year)` -- a Parquet scan has no order to preserve
    -- so for any factor reading **two** datasets the two ends produced two different identities
    and `load_factor_manifests` raised on a partition it had just written. Every shipped statement
    factor is in that class; the bug was invisible only because nothing on a read path decoded a
    build until `V2-P3-019` put that read on `load_factor_observations`.

    Asserted on a factor that reads `income` **and** `balancesheet`, in that declared order, so
    the sorted order is genuinely different from the declared one -- on a one-dataset factor the
    two agree and this test would pass against the defect.
    """
    definition = RETURN_ON_EQUITY_TTM
    built = _compute(store, definition)
    write_factor_panels(store, [built])

    (stored,) = load_factor_manifests(store, definition, years=(AS_OF.year,), as_of=AS_OF)

    assert len(definition.datasets) > 1
    assert tuple(dict.fromkeys(definition.datasets)) != tuple(sorted(set(definition.datasets)))
    assert stored.manifest_id == built.manifest.manifest_id
    assert stored.inputs == built.manifest.inputs
    assert [ref.dataset for ref in stored.inputs] == sorted(ref.dataset for ref in stored.inputs)
