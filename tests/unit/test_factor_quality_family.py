"""The quality family's declarations and arithmetic, as functions of a window (`V2-P3-010`).

`tests/integration/panel/test_quality_family.py` drives the same four factors through the real
engine against real partitions. This file measures the six things a partition is the wrong
instrument for:

- **Why ROE is computed rather than read.** Whether `fina_indicator.roe` is a trailing figure or a
  cumulative-period one is decided by the endpoint and not by a partition written here, and so is
  whether the number it serves can be reproduced from the columns this projection carries. Both
  are recorded as real rows and asserted directly. A fixture could put any number in that column.
- **The sliding of the trailing identity, and that the alternative is not fail-closed.**
  `_trailing_twelve_month_sum` refuses unless `periods[:-1]` holds exactly one fiscal year end, and
  `periods[:-1]` of the contiguous eight `gross_margin_stability` declares is **seven** consecutive
  quarters -- two year ends at three of the four alignments and **one** at the fourth, where it
  answers a number that overstates the trailing year by an entire extra fiscal year less a quarter.
  So the identity is applied to five-period slices, and that every slice of a contiguous eight has
  exactly one year end is an enumeration rather than an observation about one fixture.
- **The branch that only fires on a window this engine will not build.** A gapped eight, on which
  a slice has no year end at all.
- **The sign rule that differs from `book_to_price`'s on the same column.** One negative equity,
  two factors, two answers -- which is the only way to state that the difference is a choice.
- **The identity `n_income = total_profit - income_tax`**, which is what makes
  "`return_on_capital_ttm`'s numerator is missing exactly the after-tax interest" a measurement.
  It holds from 2007 and fails before it, and both halves are real rows.
- **The declarations themselves**, including the two coverage facts the family is asked about and
  answers negatively: that a financial issuer publishes neither `oper_cost` nor `total_cur_liab`,
  so two of these four score the non-financial cross section and say so.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Final

import pytest

from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
    INCOME_DATASET,
    STATEMENT_DATA_COLUMNS,
)
from openalpha_cn.panel_factors import (
    ACCRUALS_TTM,
    BOOK_EQUITY_COLUMN,
    CAPITAL_TURNOVER_PERIODS,
    CONSOLIDATED_NET_PROFIT_COLUMN,
    CURRENT_LIABILITIES_COLUMN,
    FACTOR_DEFINITIONS,
    GROSS_MARGIN_OBSERVATIONS,
    GROSS_MARGIN_PERIODS,
    GROSS_MARGIN_STABILITY,
    NET_PROFIT_COLUMN,
    OPERATING_CASH_FLOW_COLUMN,
    OPERATING_COST_COLUMN,
    RETURN_ON_CAPITAL_TTM,
    RETURN_ON_EQUITY_TTM,
    TOTAL_ASSETS_COLUMN,
    TOTAL_REVENUE_COLUMN,
    TRAILING_TWELVE_MONTH_PERIODS,
    FactorWindow,
    _accruals_ttm,
    _book_to_price,
    _capital_denominator,
    _gross_margin_stability,
    _return_on_capital_ttm,
    _return_on_equity_ttm,
    _sample_stdev,
    _trailing_twelve_month_sum,
)

AS_OF: Final[datetime] = datetime(2027, 5, 20, 4, 0, tzinfo=UTC)
SUBJECT: Final[str] = "600519.SH"

QUARTER_ENDS: Final[tuple[tuple[int, int], ...]] = ((3, 31), (6, 30), (9, 30), (12, 31))


def _period(index: int) -> date:
    """The `index`-th fiscal quarter end from 2024Q1, on the calendar's own grid."""
    month, day = QUARTER_ENDS[index % 4]
    return date(2024 + index // 4, month, day)


def _cumulative(increments: tuple[float, ...], index: int) -> float:
    """What a stored A-share statement carries at `_period(index)`: the fiscal year to date."""
    start = (index // 4) * 4
    return math.fsum(increments[start : index + 1])


def _trailing(increments: tuple[float, ...], index: int) -> float:
    """The plain sum of the four quarterly increments ending at `index`.

    The independent derivation every trailing expectation in this file is built from: it shares
    only the increment table with the cumulative series the window carries, so an implementation
    that differenced the wrong pair of periods lands elsewhere.
    """
    return math.fsum(increments[index - 3 : index + 1])


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
    173.0,
    191.0,
    211.0,
)
"""Twelve quarters of *incremental* attributable profit, 2024Q1 through 2026Q4.

Twelve rather than `V2-P3-009`'s eight, because `GROSS_MARGIN_PERIODS` is eight and a window of
eight has to be formable at more than one ending quarter for the slicing to be measured at each of
them. No two alike and none zero.
"""

CONSOLIDATED_MULTIPLE: Final[float] = 1.7
"""`n_income` over `n_income_attr_p` in this file's windows.

A consolidated profit exceeds the attributable one, which is why `RETURN_ON_EQUITY_TTM` and
`RETURN_ON_CAPITAL_TTM` name different columns -- and why a factor pointed at its neighbour has to
land on a different number here rather than the same one. `600739.SH`'s 2024 annual is the real
row behind the choice, at 664,195,391.66 against 209,556,865.25.
"""

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
    1087.0,
    1201.0,
    1319.0,
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
    673.0,
    811.0,
    829.0,
)
"""Revenue and cost increments whose ratio moves quarter to quarter.

Deliberately not proportional: a constant gross margin would make `gross_margin_stability` zero
and every assertion about it vacuous, which is the shape this repository has been caught by five
times. `MARGIN_FLOOR` below refuses a fixture on which the dispersion collapses.
"""

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
    157.0,
    179.0,
    197.0,
)
"""Operating cash increments, below the profit ones so `accruals_ttm` is positive and non-zero."""

CLOSING_EQUITY: Final[float] = 4_000.0
ASSETS: Final[float] = 9_500.0
CURRENT_LIABILITIES: Final[float] = 2_750.0
"""One security's stocks at the window's last period.

`ASSETS - CURRENT_LIABILITIES` is 6,750, which is neither `CLOSING_EQUITY` nor `ASSETS` nor
`ASSETS` less any multiple of `CLOSING_EQUITY` used elsewhere here -- so a `return_on_capital_ttm`
whose denominator slipped to the equity, to the assets alone, or to `total_assets - total_liab`
lands on a different number.
"""

MARGIN_FLOOR: Final[float] = 1e-3
"""How much dispersion this file's own margin series has to carry to be evidence of anything.

A fixture whose four trailing margins coincide would satisfy every assertion about
`gross_margin_stability` with the answer zero, and would separate no implementation from any
other. The measured value on `REVENUE_INCREMENTS` / `COST_INCREMENTS` is about 1.5e-2, an order of
magnitude above this floor, and the floor is asserted rather than assumed.
"""

CONSTANT_MARGIN_FLOOR: Final[float] = 1e-12
"""And how close to zero a *constant* margin series has to come.

Four trailing margins computed from a cost series that is exactly a fixed share of the revenue one
agree only to the last bits of a double, so the dispersion is 5.6e-17 rather than 0.0. This bound
is five orders of magnitude above that and eleven below `MARGIN_FLOOR`, so nothing between the two
fixtures is a judgement call.
"""


def _stocks(
    *,
    equity: float = CLOSING_EQUITY,
    assets: float = ASSETS,
    current_liabilities: float = CURRENT_LIABILITIES,
    count: int,
) -> dict[tuple[str, str], tuple[float, ...]]:
    """The three balance-sheet series, drifting so that `[0]` and `[-1]` are different cells.

    Every one of them is read at `[-1]` by design, and a window whose stock series were constant
    could not tell an implementation reading `[0]` from one reading `[-1]` -- which is exactly the
    mutant `V2-P3-009` found surviving its whole tree.
    """
    return {
        (BALANCE_SHEET_DATASET, BOOK_EQUITY_COLUMN): tuple(
            equity - 137.0 * (count - 1 - index) for index in range(count)
        ),
        (BALANCE_SHEET_DATASET, TOTAL_ASSETS_COLUMN): tuple(
            assets - 211.0 * (count - 1 - index) for index in range(count)
        ),
        (BALANCE_SHEET_DATASET, CURRENT_LIABILITIES_COLUMN): tuple(
            current_liabilities - 53.0 * (count - 1 - index) for index in range(count)
        ),
    }


def _window(
    *,
    last: int,
    count: int = CAPITAL_TURNOVER_PERIODS,
    periods: tuple[date, ...] | None = None,
    equity: float = CLOSING_EQUITY,
    assets: float = ASSETS,
    current_liabilities: float = CURRENT_LIABILITIES,
) -> FactorWindow:
    """A period-only window ending at `_period(last)`, contiguous unless `periods` overrides it.

    `sessions` is empty, which is what `_classify` hands an evaluator for a factor that declares no
    session reach -- so an implementation of any of these four that reached for a price would fail
    loudly against `FactorWindow.series` rather than quietly.
    """
    indices = tuple(range(last - count + 1, last + 1))
    series: dict[tuple[str, str], tuple[float, ...]] = {
        (INCOME_DATASET, NET_PROFIT_COLUMN): tuple(
            _cumulative(PROFIT_INCREMENTS, index) for index in indices
        ),
        (INCOME_DATASET, CONSOLIDATED_NET_PROFIT_COLUMN): tuple(
            _cumulative(PROFIT_INCREMENTS, index) * CONSOLIDATED_MULTIPLE for index in indices
        ),
        (INCOME_DATASET, TOTAL_REVENUE_COLUMN): tuple(
            _cumulative(REVENUE_INCREMENTS, index) for index in indices
        ),
        (INCOME_DATASET, OPERATING_COST_COLUMN): tuple(
            _cumulative(COST_INCREMENTS, index) for index in indices
        ),
        (CASH_FLOW_DATASET, OPERATING_CASH_FLOW_COLUMN): tuple(
            _cumulative(CASH_INCREMENTS, index) for index in indices
        ),
        **_stocks(
            equity=equity, assets=assets, current_liabilities=current_liabilities, count=count
        ),
    }
    return FactorWindow(
        subject=SUBJECT,
        as_of=AS_OF,
        sessions=(),
        periods=periods if periods is not None else tuple(_period(index) for index in indices),
        values=MappingProxyType(series),
    )


# --- the declarations ----------------------------------------------------------------------------


QUALITY_DEFINITIONS: Final[tuple[object, ...]] = (
    RETURN_ON_EQUITY_TTM,
    RETURN_ON_CAPITAL_TTM,
    GROSS_MARGIN_STABILITY,
    ACCRUALS_TTM,
)


def test_the_four_definitions_declare_the_reaches_and_the_single_axis_the_family_argues_for() -> (
    None
):
    """Each declared property, and the three that separate the family's members from each other.

    All four declare **no session reach**, which is the contract's own statement that a quality
    ratio reads no price -- and is the property that makes them the first shipped factors on the
    report-period axis alone. Three declare five periods because their numerator is the
    cumulative-to-TTM identity; `gross_margin_stability` declares eight because it slides that
    identity across four ending quarters.

    Asserted by value and each reach against its own constant, so a definition that lost its period
    axis -- which `FactorDefinition` would still accept, by dropping the statement column -- fails
    here rather than silently becoming something else.
    """
    for definition in (
        RETURN_ON_EQUITY_TTM,
        RETURN_ON_CAPITAL_TTM,
        GROSS_MARGIN_STABILITY,
        ACCRUALS_TTM,
    ):
        assert definition.family == "quality"
        assert definition.version == 1
        assert definition.session_datasets == ()
        assert definition.lookback_sessions is None
        assert definition.max_window_sessions is None
        assert definition.lookback_periods == definition.max_window_periods

    assert RETURN_ON_EQUITY_TTM.direction == "higher_is_better"
    assert RETURN_ON_CAPITAL_TTM.direction == "higher_is_better"
    assert GROSS_MARGIN_STABILITY.direction == "lower_is_better"
    assert ACCRUALS_TTM.direction == "lower_is_better"

    assert RETURN_ON_EQUITY_TTM.period_datasets == (INCOME_DATASET, BALANCE_SHEET_DATASET)
    assert RETURN_ON_EQUITY_TTM.columns_of(INCOME_DATASET) == (NET_PROFIT_COLUMN,)
    assert RETURN_ON_EQUITY_TTM.columns_of(BALANCE_SHEET_DATASET) == (BOOK_EQUITY_COLUMN,)
    assert RETURN_ON_EQUITY_TTM.lookback_periods == CAPITAL_TURNOVER_PERIODS

    assert RETURN_ON_CAPITAL_TTM.period_datasets == (INCOME_DATASET, BALANCE_SHEET_DATASET)
    assert RETURN_ON_CAPITAL_TTM.columns_of(INCOME_DATASET) == (CONSOLIDATED_NET_PROFIT_COLUMN,)
    assert RETURN_ON_CAPITAL_TTM.columns_of(BALANCE_SHEET_DATASET) == (
        TOTAL_ASSETS_COLUMN,
        CURRENT_LIABILITIES_COLUMN,
    )
    assert RETURN_ON_CAPITAL_TTM.lookback_periods == CAPITAL_TURNOVER_PERIODS

    assert GROSS_MARGIN_STABILITY.period_datasets == (INCOME_DATASET,)
    assert GROSS_MARGIN_STABILITY.columns_of(INCOME_DATASET) == (
        TOTAL_REVENUE_COLUMN,
        OPERATING_COST_COLUMN,
    )
    assert GROSS_MARGIN_STABILITY.lookback_periods == GROSS_MARGIN_PERIODS == 8

    assert ACCRUALS_TTM.period_datasets == (
        INCOME_DATASET,
        CASH_FLOW_DATASET,
        BALANCE_SHEET_DATASET,
    )
    assert ACCRUALS_TTM.columns_of(CASH_FLOW_DATASET) == (OPERATING_CASH_FLOW_COLUMN,)
    assert ACCRUALS_TTM.columns_of(BALANCE_SHEET_DATASET) == (TOTAL_ASSETS_COLUMN,)
    assert ACCRUALS_TTM.lookback_periods == CAPITAL_TURNOVER_PERIODS == 5


def test_the_quality_family_is_the_first_shipped_factor_on_the_period_axis_alone() -> None:
    """The claim the family's own prose makes about the registry, held against the registry.

    Every factor shipped before `V2-P3-010` reads a session dataset: nine read only sessions and
    `V2-P3-009`'s three read a filing **and** a price. So a `lookback_sessions` of `None` had no
    production reader at all until these four, and `_form_window`'s three-outcome contract -- `()`
    for an axis the factor is not on, distinct from `None` for a security short of one it is --
    had no shipped factor exercising its first branch. It is a property of `FACTOR_DEFINITIONS`
    rather than of a docstring, so it is asserted there, in both directions.
    """
    period_only = {
        definition.qualified_key
        for definition in FACTOR_DEFINITIONS.definitions
        if definition.period_datasets and not definition.session_datasets
    }

    assert period_only == {
        RETURN_ON_EQUITY_TTM.qualified_key,
        RETURN_ON_CAPITAL_TTM.qualified_key,
        GROSS_MARGIN_STABILITY.qualified_key,
        ACCRUALS_TTM.qualified_key,
    }
    for definition in FACTOR_DEFINITIONS.definitions:
        if definition.qualified_key in period_only:
            continue
        assert definition.lookback_sessions is not None
        assert definition.max_window_sessions is not None


def test_the_columns_this_family_reads_are_columns_the_stored_contracts_declare() -> None:
    """`FactorField` validates a column reference syntactically and says so, so the binding
    between these six names and the contracts that declare them has to be asserted somewhere.

    Six columns over three of the four statement endpoints, and **none** over `fina_indicator` --
    which is `RETURN_ON_EQUITY_TTM`'s whole argument and is asserted here as a property of the
    definitions rather than as a sentence in a docstring.
    """
    for column in (NET_PROFIT_COLUMN, CONSOLIDATED_NET_PROFIT_COLUMN, TOTAL_REVENUE_COLUMN):
        assert column in STATEMENT_DATA_COLUMNS[INCOME_DATASET]
    assert OPERATING_COST_COLUMN in STATEMENT_DATA_COLUMNS[INCOME_DATASET]
    for column in (BOOK_EQUITY_COLUMN, TOTAL_ASSETS_COLUMN, CURRENT_LIABILITIES_COLUMN):
        assert column in STATEMENT_DATA_COLUMNS[BALANCE_SHEET_DATASET]
    assert OPERATING_CASH_FLOW_COLUMN in STATEMENT_DATA_COLUMNS[CASH_FLOW_DATASET]

    read = {
        (item.dataset, item.column)
        for definition in (
            RETURN_ON_EQUITY_TTM,
            RETURN_ON_CAPITAL_TTM,
            GROSS_MARGIN_STABILITY,
            ACCRUALS_TTM,
        )
        for item in definition.required_fields
    }

    assert {dataset for dataset, _ in read} == {
        INCOME_DATASET,
        BALANCE_SHEET_DATASET,
        CASH_FLOW_DATASET,
    }
    assert FINANCIAL_INDICATOR_DATASET not in {dataset for dataset, _ in read}
    assert "roe" in STATEMENT_DATA_COLUMNS[FINANCIAL_INDICATOR_DATASET], (
        "the column this family declines to read is a stored one, so declining is a choice"
    )


CONCENTRATED_REFUSAL_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    (INCOME_DATASET, "ebit"),
    (BALANCE_SHEET_DATASET, "total_share"),
    (CASH_FLOW_DATASET, "free_cashflow"),
    (FINANCIAL_INDICATOR_DATASET, "fcff"),
    (FINANCIAL_INDICATOR_DATASET, "bps"),
)
"""The columns `domain/financial_statements.py` measured the refusals pooling in.

258 of `income`'s 288 refused field reads are `ebit`, 34 of `balancesheet`'s 43 are `total_share`,
**all** 450 of `cashflow`'s are `free_cashflow` and 441 of `fina_indicator`'s 507 are `fcff`;
`bps` is the field whose two versions of one `603049.SH` filing differ by a factor of ten.
Restated here as literals rather than imported from `tests/unit/test_factor_value_family.py`,
which holds the same tuple for `V2-P3-009`: importing across two test modules would make this
file's evidence depend on another file's fixture layout.
"""


def test_the_family_reads_none_of_the_columns_the_measured_refusals_are_concentrated_in() -> None:
    """The column choice, pinned so that swapping one is a deliberate act.

    Every obvious reading of these four factors lands on one of the pooled columns: a ROIC from
    `ebit`, an accrual from `free_cashflow`, a per-share quality ratio from `bps` times
    `total_share`, a cash-flow quality ratio from `fcff`. This family reads none of them, and a
    later edit that pointed `accruals_ttm` at `free_cashflow` would still satisfy every value
    assertion in this file's integration twin, because a fixture can put any number in any column.
    """
    read = {
        (item.dataset, item.column)
        for definition in (
            RETURN_ON_EQUITY_TTM,
            RETURN_ON_CAPITAL_TTM,
            GROSS_MARGIN_STABILITY,
            ACCRUALS_TTM,
        )
        for item in definition.required_fields
    }

    assert read.isdisjoint(CONCENTRATED_REFUSAL_COLUMNS)
    for dataset, column in CONCENTRATED_REFUSAL_COLUMNS:
        assert column in STATEMENT_DATA_COLUMNS[dataset], "the census names a projected column"


# --- why ROE is computed rather than read ---------------------------------------------------------
#
# `fina_indicator.roe`, `income` and `balancesheet` rows for the six securities `V2-P3-010`'s live
# probe read to exhaustion on 2026-08-13. Recorded as literals rather than fetched, because a test
# may not reach the network -- and restated to the stored precision, which is finer than any gap
# asserted below.

# (report period, published roe, cumulative n_income_attr_p, closing total_hldr_eqy_exc_min_int)
MOUTAI_2024: Final[tuple[tuple[str, float, float, float], ...]] = (
    ("20240331", 10.5688, 24_065_262_374.15, 239_731_785_910.55),
    ("20240630", 19.2038, 41_695_610_983.37, 218_575_608_600.14),
    ("20240930", 26.8330, 60_827_552_118.51, 237_709_455_000.28),
    ("20241231", 38.4283, 86_228_146_421.62, 233_105_984_399.47),
)
"""`600519.SH`'s four 2024 filings: the published ROE beside the two columns this factor reads.

The published number quadruples through the year while the equity base barely moves, which is what
"a cumulative-period return" means and what makes it unusable in a cross section that mixes filing
schedules.
"""

CUMULATIVE_SHARE_ROUNDING: Final[float] = 0.02
"""How closely the published ROE's share of the annual has to track the profit's, and it decides
nothing at this width.

The three measured gaps are 0.004, 0.016 and 0.007, and the quantity being separated from is a
trailing-twelve-month reading, whose Q1 share of the annual would be near **1.0** rather than near
0.28. So any tolerance between about 0.02 and 0.6 gives the same verdict; this one sits at the
bottom of that interval.
"""


def test_the_published_roe_is_a_cumulative_period_return_and_not_a_trailing_one() -> None:
    """The decisive half of why this family computes ROE instead of reading `fina_indicator.roe`.

    A-share statements accumulate inside the calendar fiscal year, and the published ROE
    accumulates with them: each quarter's figure is the same share of the annual as that quarter's
    cumulative profit is. A trailing-twelve-month ROE would do nothing of the kind -- its Q1 value
    would be near the annual's, because eleven of its twelve months are the same eleven.

    **And there is no repair, which is the part that decides the issue.** The cumulative-to-TTM
    identity is an identity about *sums*: `TTM(P) = cum[P] + cum[December] - cum[P - 4 quarters]`
    holds because the three terms are sums of the same flow. A ratio is not a sum, and the same
    expression written in ROEs has three different denominators. That is asserted here as an
    arithmetic fact about these four real numbers rather than as a sentence: the identity applied
    to the published ROEs lands nowhere near the annual figure it would have to reproduce at a
    December period.
    """
    annual_roe = MOUTAI_2024[-1][1]
    annual_profit = MOUTAI_2024[-1][2]

    for period, roe, profit, equity in MOUTAI_2024:
        assert 100.0 * profit / equity == pytest.approx(roe, rel=0.15), period
        assert roe / annual_roe == pytest.approx(
            profit / annual_profit, abs=CUMULATIVE_SHARE_ROUNDING
        ), period

    first_quarter_roe = MOUTAI_2024[0][1]

    assert first_quarter_roe < 0.5 * annual_roe, (
        "a trailing-twelve-month figure at Q1 shares eleven months with the annual and could not "
        "be less than half of it"
    )
    # The identity, written in ROEs at the 2024 annual: `cum[-1] + cum[December] - cum[0]` over the
    # window 2023Q4..2024Q4 degenerates at a December period to `cum[-1]` -- which is why the
    # *value* family's numerators are correct there. Applied one period earlier it does not: at
    # 2024Q3 the trailing profit is a real sum and the trailing "ROE" is not the ratio of it to any
    # equity in the window.
    trailing_profit = MOUTAI_2024[2][2] + MOUTAI_2024[3][2] - MOUTAI_2024[0][2]
    roe_shaped = MOUTAI_2024[2][1] + MOUTAI_2024[3][1] - MOUTAI_2024[0][1]

    assert roe_shaped != pytest.approx(100.0 * trailing_profit / MOUTAI_2024[2][3], rel=0.05), (
        "differencing published ratios is not the trailing return on any equity in the window"
    )


# (security, report period, published roe, cumulative n_income_attr_p, closing equity, gap)
PUBLISHED_AGAINST_CLOSING_EQUITY: Final[tuple[tuple[str, str, float, float, float, float], ...]] = (
    ("600519.SH", "20241231", 38.4283, 86_228_146_421.62, 233_105_984_399.47, 0.0374),
    ("000001.SZ", "20191231", 10.1966, 28_195_000_000.0, 312_976_000_000.0, 0.1165),
    ("000002.SZ", "20251231", -55.4220, -88_556_470_495.64, 116_905_224_808.46, 0.3668),
)
"""Three annual filings where the published ROE and the closing-equity computation part.

The measured gap over `600519.SH`'s eight most recent annuals is 2.0%-9.5%, over `000001.SZ`'s
2.3%-11.7% and over `000002.SZ`'s 1.4%-36.7%. The three rows here are the widest of each, and the
third is the one that decides the argument: a 36.7% disagreement on a real name in the year its
ROE mattered most.
"""

PUBLISHED_GAP_ROUNDING: Final[float] = 5e-4
"""The rounding of the fourth decimal place of a ratio; the gaps being separated are 3.7e-2,
1.2e-1 and 3.7e-1, three orders of magnitude above it."""


def test_the_published_roe_cannot_be_reproduced_from_the_columns_this_projection_carries() -> None:
    """The second reason ROE is computed: the formula behind the served number is unstated.

    Nothing in the response says whether the denominator is opening, closing or weighted-average
    equity, whether the numerator is attributable or consolidated, or whether it is the deducted
    profit -- and `fina_indicator` carries **neither** a profit column nor an equity column, so a
    reader holding only that endpoint has nothing to reconcile against. The reconciliation is
    possible at all only because this factor's own two columns exist, which is itself the argument
    for reading them.

    Measured rather than asserted as unknowable: against the closing-equity computation the
    published annual figure is out by 3.7% on `600519.SH`, 11.7% on `000001.SZ` and **36.7%** on
    `000002.SZ`. A factor built on the served column would therefore be scoring an arithmetic this
    repository cannot state, which is `_compounded_session_return`'s objection to `pct_chg` and
    `EARNINGS_YIELD_TTM`'s to the published `pe_ttm`, on a number whose disagreement is an order of
    magnitude larger.
    """
    for security, period, published, profit, equity, gap in PUBLISHED_AGAINST_CLOSING_EQUITY:
        where = f"{security} {period}"
        computed = 100.0 * profit / equity
        assert abs(published - computed) / abs(published) == pytest.approx(
            gap, abs=PUBLISHED_GAP_ROUNDING
        ), where
        assert computed != pytest.approx(published, rel=1e-3), where

    assert "roe" not in {item.column for item in RETURN_ON_EQUITY_TTM.required_fields}
    assert "n_income_attr_p" not in STATEMENT_DATA_COLUMNS[FINANCIAL_INDICATOR_DATASET]
    assert "total_hldr_eqy_exc_min_int" not in STATEMENT_DATA_COLUMNS[FINANCIAL_INDICATOR_DATASET]


def test_the_published_roe_is_a_percentage_and_this_factor_is_a_ratio() -> None:
    """The unit, which is the shape `CNY_PER_MARKET_CAP_UNIT` was a Critical about.

    `23.9249` and `0.239249` are the same statement in two units and nothing in the projection says
    which one the column is in; downstream a rank IC and a z-score are both scale-free, so a factor
    off by a hundred reaches every report that quotes a number and no test that only ranks. A
    computed ROE has no such question: both of its terms are yuan, so the quotient is a ratio by
    construction.
    """
    window = _window(last=7)
    answer = _return_on_equity_ttm(window)

    assert answer is not None
    assert answer == pytest.approx(_trailing(PROFIT_INCREMENTS, 7) / CLOSING_EQUITY)
    for _period_name, published, profit, equity in MOUTAI_2024:
        assert published == pytest.approx(100.0 * profit / equity, rel=0.15)
        assert published != pytest.approx(profit / equity, rel=0.5)


# --- the identity that prices ROIC's missing add-back ---------------------------------------------
#
# `income` rows captured by the same probe. The first three are the widest post-2007 residuals in
# the six-security sample; the fourth is the pre-2007 counterexample.

# (security, report period, n_income, total_profit, income_tax)
NET_PROFIT_IS_PRE_TAX_LESS_TAX: Final[tuple[tuple[str, str, float, float, float], ...]] = (
    ("000001.SZ", "20090331", 1_122_077_000.0, 1_517_120_000.0, 395_042_000.0),
    ("600519.SH", "20220331", 17_952_116_145.80, 24_011_118_056.69, 6_059_001_910.90),
    ("603049.SH", "20250331", 1_151_556_073.67, 1_211_323_667.86, 59_767_594.19),
)
"""Three real `income` rows: the worst post-2007 residual in the sample, and two at precision.

Over the six securities probed the identity holds on **every** period from 2007 onward -- 446 rows,
worst relative residual 8.9e-7 (the first row here) and five of the six securities at machine
precision.
"""

PRE_2007_COUNTEREXAMPLE: Final[tuple[str, str, float, float, float, float]] = (
    "000001.SZ",
    "20050630",
    157_834_800.0,
    370_120_574.0,
    162_633_782.0,
    0.3146,
)
"""And where it fails: `000001.SZ`'s 2005 interim, off by 31.5%.

The pre-2007 CAS reported 净利润 already net of minority interest, so `total_profit - income_tax`
is the consolidated figure and `n_income` is not. `000002.SZ`'s 1996 interim is the same shape at
5.9%. The identity is therefore stated with its boundary rather than as a law, which is what keeps
`RETURN_ON_CAPITAL_TTM`'s "the missing term is exactly the after-tax interest" honest for the
periods a factor built today can reach.
"""

POST_2007_RESIDUAL: Final[float] = 1e-6
"""The worst post-2007 relative residual measured over the sample, rounded up: 8.9e-7 -> 1e-6.

Six orders of magnitude below the pre-2007 counterexample's 3.1e-1, so no tolerance between them
changes the verdict.
"""


def test_the_consolidated_profit_is_the_pre_tax_profit_less_the_tax_on_real_rows() -> None:
    """What makes `RETURN_ON_CAPITAL_TTM`'s disclosure a measurement rather than a reading.

    That factor says its numerator is NOPAT **less the after-tax net interest that cannot be added
    back**, and the whole content of that sentence is that `n_income` is an after-tax figure of the
    whole consolidated entity -- which is checkable inside the projection, because
    `total_profit` and `income_tax` are both stored. If the identity did not hold, the missing term
    would be something other than interest and the disclosure would be wrong about what it is
    missing.

    Stated with its own boundary rather than as a law: it holds from 2007 and fails before it,
    because the pre-2007 CAS reported 净利润 already net of minority interest. Both halves are real
    rows, so a reader can see the boundary rather than take it on trust.
    """
    for security, period, net, pre_tax, tax in NET_PROFIT_IS_PRE_TAX_LESS_TAX:
        where = f"{security} {period}"
        assert period >= "20071231", where
        assert abs(net - (pre_tax - tax)) / abs(net) < POST_2007_RESIDUAL, where

    security, period, net, pre_tax, tax, gap = PRE_2007_COUNTEREXAMPLE

    assert period < "20071231"
    assert abs(net - (pre_tax - tax)) / abs(net) == pytest.approx(gap, abs=1e-4)
    assert "ebit" not in {item.column for item in RETURN_ON_CAPITAL_TTM.required_fields}
    assert "ebit" in STATEMENT_DATA_COLUMNS[INCOME_DATASET], (
        "the column the add-back would need is stored and is declined, not absent"
    )


# --- the two columns a financial issuer does not publish ------------------------------------------
#
# Nulls counted over every stored period from 2015-01-01 by the same probe, per security. The three
# financials are `comp_type` 2, 3 and 4; the two industrials are `comp_type=1`.

# (security, what it is, oper_cost nulls, total_cur_liab nulls, rows since 2015 in each dataset)
FINANCIAL_NULLITY: Final[tuple[tuple[str, str, int, int, int, int], ...]] = (
    ("000001.SZ", "bank", 59, 68, 59, 68),
    ("601318.SH", "insurer", 57, 67, 57, 67),
    ("600030.SH", "broker", 56, 64, 56, 64),
    ("600519.SH", "industrial", 0, 0, 60, 62),
    ("000002.SZ", "industrial", 0, 0, 60, 63),
)
"""Two columns, five securities, every stored period since 2015.

A bank, an insurer and a broker publish **neither** a cost of sales nor a current / non-current
balance-sheet split: the null counts are the row counts. The two industrials publish both on every
row. The financials' newest non-null `total_cur_liab` periods are 2006-03-31, 2006-09-30 and
2006-09-30, so this is a standing property of the company type rather than a recent gap.
"""


def test_two_of_these_factors_are_blind_to_financial_issuers_and_the_nullity_is_measured() -> None:
    """The coverage disclosure `RETURN_ON_CAPITAL_TTM` and `GROSS_MARGIN_STABILITY` both make.

    `_complete_series` answers `input_missing` for a null cell, so a factor reading `oper_cost` or
    `total_cur_liab` scores the non-financial cross section and nothing else. That is the right
    answer -- a bank has no cost of sales and no current / non-current split, so its gross margin
    and its capital employed are not defined quantities and a number for them would be invented --
    but it is a coverage fact a reader has to be told rather than discover, and it is the reason
    both factors say so in their own prose.

    Asserted as the probe's counts rather than as a claim about company types in general: the
    evidence is that three financials carry the columns null on every row of the last decade while
    two industrials carry them populated on every row, and the two directions together are what
    make it a property of the type rather than of one name.
    """
    for security, kind, cost_nulls, liability_nulls, cost_rows, liability_rows in FINANCIAL_NULLITY:
        where = f"{security} ({kind})"
        assert cost_rows > 50 and liability_rows > 50, where
        if kind == "industrial":
            assert cost_nulls == 0, where
            assert liability_nulls == 0, where
        else:
            assert cost_nulls == cost_rows, where
            assert liability_nulls == liability_rows, where

    assert OPERATING_COST_COLUMN in GROSS_MARGIN_STABILITY.columns_of(INCOME_DATASET)
    assert CURRENT_LIABILITIES_COLUMN in RETURN_ON_CAPITAL_TTM.columns_of(BALANCE_SHEET_DATASET)
    # The other two read no column any of the three financials leaves null, so the family is not
    # uniformly blind -- which is what makes the disclosure specific to two of its four members.
    for definition in (RETURN_ON_EQUITY_TTM, ACCRUALS_TTM):
        assert OPERATING_COST_COLUMN not in definition.columns_of(INCOME_DATASET)
        assert CURRENT_LIABILITIES_COLUMN not in definition.columns_of(BALANCE_SHEET_DATASET)


# --- the trailing identity, slid across a wider window --------------------------------------------


def test_every_slice_of_a_contiguous_eight_period_window_holds_exactly_one_fiscal_year_end() -> (
    None
):
    """What makes the slicing legitimate, enumerated rather than observed on one fixture.

    `_trailing_twelve_month_sum` finds the fiscal year end inside `periods[:-1]` and answers `None`
    unless there is exactly one. `GROSS_MARGIN_STABILITY` hands it four five-period slices of a
    contiguous eight, and every one of their `[:-1]`s is four consecutive quarters -- so exactly one
    of them ends a year, by the same construction `TRAILING_TWELVE_MONTH_PERIODS` argues for the
    five-period case. Run at all four possible ending quarters, so the claim is about the window
    shape rather than about where in the calendar this file happens to start.
    """
    for last in range(7, 11):
        indices = tuple(range(last - GROSS_MARGIN_PERIODS + 1, last + 1))
        periods = tuple(_period(index) for index in indices)
        assert len(periods) == GROSS_MARGIN_PERIODS

        slices = [
            periods[start : start + TRAILING_TWELVE_MONTH_PERIODS]
            for start in range(GROSS_MARGIN_OBSERVATIONS)
        ]

        assert len(slices) == GROSS_MARGIN_OBSERVATIONS == 4
        assert slices[-1][-1] == periods[-1]
        for span in slices:
            year_ends = [period for period in span[:-1] if period.month == 12]
            assert len(year_ends) == 1, f"{last}: {span}"


def test_the_whole_eight_period_window_is_no_trailing_year_and_does_not_always_refuse() -> None:
    """Why the identity is *sliced* rather than applied to the window, and why that is not optional.

    `_trailing_twelve_month_sum` refuses unless `periods[:-1]` holds exactly one fiscal year end,
    and `periods[:-1]` of a contiguous eight is **seven** consecutive quarters. Seven consecutive
    quarters hold two year ends at three of the four alignments and **one** at the fourth, so
    handing the identity a whole eight is not fail-closed: at a window ending in December it finds
    the year end of the year *before* the one the identity names, returns a number, and that number
    is the true trailing twelve months plus a whole extra fiscal year less that year's first
    quarter.

    That is the failure this repository books Criticals for -- a guard that reads as fail-closed
    and answers confidently on one case in four -- so it is enumerated over all four ending
    quarters and the wrong number is compared against the right one rather than merely observed to
    exist.
    """
    refusing = 0
    for last in range(7, 11):
        indices = tuple(range(last - GROSS_MARGIN_PERIODS + 1, last + 1))
        periods = tuple(_period(index) for index in indices)
        values = tuple(_cumulative(REVENUE_INCREMENTS, index) for index in indices)
        year_ends = [period for period in periods[:-1] if period.month == 12]
        answer = _trailing_twelve_month_sum(periods, values)
        where = periods[-1].isoformat()

        if periods[-1].month == 12:
            # The one year end it finds is the December of the year *before* the one the identity
            # names, so the answer is the window's own last cumulative plus a whole extra fiscal
            # year less that year's first quarter -- derived here rather than observed, so the
            # assertion says what the wrong number is and not merely that it is wrong.
            stale_year_end = indices[periods.index(year_ends[0])]
            overstatement = _cumulative(REVENUE_INCREMENTS, stale_year_end) - _cumulative(
                REVENUE_INCREMENTS, indices[0]
            )

            assert len(year_ends) == 1, where
            assert answer is not None, where
            assert answer != pytest.approx(_trailing(REVENUE_INCREMENTS, last)), where
            assert overstatement > 0.0, where
            assert answer == pytest.approx(_trailing(REVENUE_INCREMENTS, last) + overstatement), (
                where
            )
        else:
            assert len(year_ends) == 2, where
            assert answer is None, where
            refusing += 1

        # The five-period slice ending at the same period is the one the identity is for, and it
        # answers the trailing twelve months exactly.
        assert _trailing_twelve_month_sum(
            periods[-TRAILING_TWELVE_MONTH_PERIODS:], values[-TRAILING_TWELVE_MONTH_PERIODS:]
        ) == pytest.approx(_trailing(REVENUE_INCREMENTS, last)), where

    assert refusing == 3, "three of the four alignments refuse and one does not"


def test_a_gapped_five_period_window_refuses_in_each_of_the_three_flow_over_stock_ratios() -> None:
    """The identity's own refusal, reached through each of the three factors that call it.

    `_trailing_twelve_month_sum` answers `None` when `periods[:-1]` holds no fiscal year end, and
    the three flow-over-stock ratios each turn that into `undefined_value` for the security rather
    than into a difference between two periods the formula does not name. Driven through all three
    rather than through the helper, because each one has its own `if profit is None` after its own
    denominator guard and a missing return there is a `None` divided by a float.

    The window is one the engine will not build -- `_overruns_its_span` refuses a five-period
    window spanning six quarters -- so this is `_sample_stdev`'s precedent again. Every denominator
    in it is strictly positive, so the refusal is the numerator's and not the guard's.
    """
    gapped = (_period(0), _period(1), _period(2), _period(4), _period(5))
    window = _window(last=5, periods=gapped)

    assert [period for period in gapped[:-1] if period.month == 12] == []
    assert window.series(BALANCE_SHEET_DATASET, BOOK_EQUITY_COLUMN)[-1] > 0.0
    assert window.series(BALANCE_SHEET_DATASET, TOTAL_ASSETS_COLUMN)[-1] > 0.0
    assert _return_on_equity_ttm(window) is None
    assert _return_on_capital_ttm(window) is None
    assert _accruals_ttm(window) is None
    # And the contiguous window ending at the same period answers, so the `None`s are the gap.
    contiguous = _window(last=5)
    for evaluator in (_return_on_equity_ttm, _return_on_capital_ttm, _accruals_ttm):
        assert evaluator(contiguous) is not None


def test_a_gapped_eight_period_window_has_no_margin_series_rather_than_a_short_one() -> None:
    """The branch `max_window_periods == lookback_periods` makes unreachable through the engine.

    A window that skips a quarter puts a slice's `[:-1]` across five quarters or three, and the
    slice then holds two year ends or none. `_gross_margin_stability` answers `None` for the whole
    factor rather than dropping the slice and taking a dispersion over three -- because a
    dispersion over a variable number of observations would be a different statistic per security,
    which is exactly what a declared reach exists to prevent.

    `_overruns_its_span` refuses such a window before an evaluator sees it, so the branch is driven
    on the function directly, which is `_sample_stdev`'s precedent and its reason.
    """
    contiguous = tuple(_period(index) for index in range(1, 9))
    gapped = (
        _period(1),
        _period(2),
        # `_period(3)` -- 2024-12-31 -- is the missing filing, so the first slice's `[:-1]` holds
        # no fiscal year end at all rather than the one the identity names.
        _period(4),
        _period(5),
        _period(6),
        _period(7),
        _period(8),
        _period(9),
    )

    assert len(gapped) == len(contiguous) == GROSS_MARGIN_PERIODS
    assert _gross_margin_stability(_window(last=8, count=GROSS_MARGIN_PERIODS)) is not None
    assert (
        _gross_margin_stability(_window(last=8, count=GROSS_MARGIN_PERIODS, periods=gapped)) is None
    )


# --- the four values ------------------------------------------------------------------------------


def _expected_margins(last: int) -> tuple[float, ...]:
    """The four trailing gross margins, derived from the increment tables rather than from a run.

    Each is the plain sum of four quarterly revenue increments less the plain sum of four quarterly
    cost increments, over the sum of the revenue ones -- so this derivation and the evaluator share
    only `REVENUE_INCREMENTS` and `COST_INCREMENTS`.
    """
    first = last - GROSS_MARGIN_OBSERVATIONS + 1
    return tuple(
        (_trailing(REVENUE_INCREMENTS, index) - _trailing(COST_INCREMENTS, index))
        / _trailing(REVENUE_INCREMENTS, index)
        for index in range(first, last + 1)
    )


def test_the_four_evaluators_give_four_different_numbers_on_one_corpus() -> None:
    """Four factors reading overlapping columns is exactly where a fixture stops discriminating.

    Two of them divide a trailing profit by a balance-sheet stock and two of those three profits
    are `n_income`; a fixture on which any pair coincided would let an evaluator wired to the wrong
    column or the wrong denominator pass. Every value is pinned to a derivation from the increment
    tables, and the four are asserted pairwise distinct.

    **Two windows and not one**, because the four do not declare one reach: `_classify` hands each
    factor exactly `lookback_periods` of the security's own filings, so the three five-period
    factors get the last five of the same eight `gross_margin_stability` gets. Handing the
    five-period factors the eight-period window would be handing `_trailing_twelve_months` a
    seven-quarter `periods[:-1]`, which is the wrong-number case the test above enumerates -- and
    that is a fact about this file's helpers rather than about the engine, which cannot do it.
    """
    wide = _window(last=11, count=GROSS_MARGIN_PERIODS)
    narrow = _window(last=11)
    trailing_profit = _trailing(PROFIT_INCREMENTS, 11)
    trailing_cash = _trailing(CASH_INCREMENTS, 11)
    margins = _expected_margins(11)

    assert narrow.periods == wide.periods[-CAPITAL_TURNOVER_PERIODS:]
    answers = {
        "return_on_equity_ttm": _return_on_equity_ttm(narrow),
        "return_on_capital_ttm": _return_on_capital_ttm(narrow),
        "gross_margin_stability": _gross_margin_stability(wide),
        "accruals_ttm": _accruals_ttm(narrow),
    }

    assert answers["return_on_equity_ttm"] == pytest.approx(trailing_profit / CLOSING_EQUITY)
    assert answers["return_on_capital_ttm"] == pytest.approx(
        trailing_profit * CONSOLIDATED_MULTIPLE / (ASSETS - CURRENT_LIABILITIES)
    )
    assert answers["accruals_ttm"] == pytest.approx(
        (trailing_profit * CONSOLIDATED_MULTIPLE - trailing_cash) / ASSETS
    )
    assert answers["gross_margin_stability"] == pytest.approx(_sample_stdev(margins))
    assert all(value is not None for value in answers.values())
    assert len({round(value, 12) for value in answers.values() if value is not None}) == 4

    # Each of the four near misses a wrong reader would have produced, and none of them is the
    # answer: the attributable profit for ROIC, the assets alone for its denominator, the equity
    # for the accrual scaler, and the newest cell of a drifting stock series taken from `[0]`.
    assert answers["return_on_capital_ttm"] != pytest.approx(
        trailing_profit / (ASSETS - CURRENT_LIABILITIES)
    )
    assert answers["return_on_capital_ttm"] != pytest.approx(
        trailing_profit * CONSOLIDATED_MULTIPLE / ASSETS
    )
    assert answers["accruals_ttm"] != pytest.approx(
        (trailing_profit * CONSOLIDATED_MULTIPLE - trailing_cash) / CLOSING_EQUITY
    )
    oldest_equity = narrow.series(BALANCE_SHEET_DATASET, BOOK_EQUITY_COLUMN)[0]
    assert oldest_equity != CLOSING_EQUITY
    assert answers["return_on_equity_ttm"] != pytest.approx(trailing_profit / oldest_equity)


def test_the_trailing_numerators_are_not_the_latest_cumulative_filing() -> None:
    """The whole content of the TTM choice, on the two factors that make it.

    The newest filing in this window is a Q4 report, so its stored cumulative figure is a full year
    -- which is the case where reading the latest filing is accidentally right. The assertion is
    therefore made at a **Q3** ending period, where the latest cumulative is nine months and the
    trailing twelve is a real sum, and the two are different numbers by construction.
    """
    window = _window(last=10)
    trailing = _trailing(PROFIT_INCREMENTS, 10)
    latest_cumulative = _cumulative(PROFIT_INCREMENTS, 10)
    answer = _return_on_equity_ttm(window)

    assert _period(10).month == 9
    assert trailing != pytest.approx(latest_cumulative)
    assert answer == pytest.approx(trailing / CLOSING_EQUITY)
    assert answer != pytest.approx(latest_cumulative / CLOSING_EQUITY)
    assert answer != pytest.approx(
        (latest_cumulative - _cumulative(PROFIT_INCREMENTS, 6)) / CLOSING_EQUITY
    )


def test_the_return_on_equity_is_the_earnings_yield_divided_by_the_book_to_price() -> None:
    """The identity that decides the denominator, at the level a window can state it.

    `earnings_yield_ttm` is this trailing profit over a market capitalisation and `book_to_price` is
    this closing equity over the same capitalisation, so their quotient is this factor exactly --
    which is the reason the denominator is the window's last period rather than the average of its
    ends. An averaged denominator would break it, and the repository would then carry two
    incompatible statements of what a book value is.

    Asserted here on the arithmetic and in
    `tests/integration/panel/test_quality_family.py::
    test_the_return_on_equity_is_the_earnings_yield_over_the_book_to_price` through the real engine
    over one corpus, because the two say different things: this one is about the formulae and that
    one is about the windows the engine forms for three definitions with different reaches.
    """
    capitalisation = 3.0
    window = _window(last=7)
    priced = FactorWindow(
        subject=SUBJECT,
        as_of=AS_OF,
        sessions=(date(2027, 5, 19),),
        periods=window.periods,
        values=MappingProxyType(
            {
                **dict(window.values),
                ("daily_basic", "total_mv"): (capitalisation,),
            }
        ),
    )
    book_to_price = _book_to_price(priced)
    equity = window.series(BALANCE_SHEET_DATASET, BOOK_EQUITY_COLUMN)[-1]
    trailing = _trailing(PROFIT_INCREMENTS, 7)
    answer = _return_on_equity_ttm(window)

    assert book_to_price is not None and book_to_price != 0.0
    assert answer is not None
    assert answer == pytest.approx(trailing / equity)
    # `earnings_yield_ttm`'s own arithmetic, restated from the fixture rather than called, so the
    # identity is between two derivations rather than between one function and itself.
    earnings_yield = trailing / (capitalisation * 10_000.0)

    assert earnings_yield / book_to_price == pytest.approx(answer)
    # The averaged denominator this factor declines, which lands somewhere else on this window.
    average_equity = (equity + window.series(BALANCE_SHEET_DATASET, BOOK_EQUITY_COLUMN)[0]) / 2.0
    assert answer != pytest.approx(trailing / average_equity)


# --- the sign rules -------------------------------------------------------------------------------


@pytest.mark.parametrize("stock", [0.0, -1.0, -4_000.0])
def test_a_stock_denominator_that_is_not_positive_is_undefined_rather_than_a_division(
    stock: float,
) -> None:
    """The branch behind `undefined_value` for all three ratios, driven on one guard.

    Non-positive rather than zero, and the negative half is the one that matters: zero raises and
    negative answers confidently and backwards. `_market_capitalisation` refuses a non-positive
    denominator on the session axis for the same reason and this is that refusal on the other one.
    """
    assert _capital_denominator(stock) is None
    assert _return_on_equity_ttm(_window(last=7, equity=stock)) is None
    assert _accruals_ttm(_window(last=7, assets=stock)) is None
    assert _return_on_capital_ttm(_window(last=7, assets=stock, current_liabilities=0.0)) is None
    # And capital employed can be non-positive with both of its terms positive, which is the case
    # a guard on `total_assets` alone would miss.
    assert _return_on_capital_ttm(_window(last=7, assets=100.0, current_liabilities=100.0)) is None
    assert _capital_denominator(1e-9) == 1e-9


def test_a_negative_equity_is_a_numerator_for_book_to_price_and_a_refusal_for_roe() -> None:
    """The same column, two factors, two rules -- which is the only way to state it as a choice.

    `book_to_price` reports an insolvent issuer as `computed` and negative, on the argument that
    `B/P` is monotone through zero. `return_on_equity_ttm` reports the same equity as
    `undefined_value`, because as a *denominator* it inverts the ordering: a profitable insolvent
    issuer would rank below a loss-making solvent one, and the two would swap places in every rank
    IC. Driven on one number so that a later edit which "made the two consistent" has to delete an
    assertion rather than merely change a guard.
    """
    insolvent = -6_000.0
    window = _window(last=7, equity=insolvent)
    priced = FactorWindow(
        subject=SUBJECT,
        as_of=AS_OF,
        sessions=(date(2027, 5, 19),),
        periods=window.periods,
        values=MappingProxyType(
            {**dict(window.values), ("daily_basic", "total_mv"): (2.0,)},
        ),
    )
    book_to_price = _book_to_price(priced)

    assert book_to_price is not None
    assert book_to_price == pytest.approx(insolvent / (2.0 * 10_000.0))
    assert book_to_price < 0.0
    assert _return_on_equity_ttm(window) is None
    # The profit is positive, so the refused answer would have been a *negative* return on equity
    # for a profitable issuer -- which is the ordering the guard exists to refuse.
    assert _trailing(PROFIT_INCREMENTS, 7) > 0.0


def test_a_negative_accrual_is_computed_and_negative_rather_than_undefined() -> None:
    """The other sign judgement: an issuer whose operating cash exceeds its reported profit.

    That is the case this factor exists to find -- earnings backed by more cash than they claim --
    so it is a real answer and not a missing one, on `EARNINGS_YIELD_TTM`'s monotonicity argument.
    The magnitude is asserted and not only the sign, because a factor that returned `-1` for
    "negative accrual" would satisfy a sign-only assertion and be a different factor.
    """
    window = _window(last=7)
    profit = _trailing(PROFIT_INCREMENTS, 7) * CONSOLIDATED_MULTIPLE
    cash = _trailing(CASH_INCREMENTS, 7)
    ordinary = _accruals_ttm(window)

    assert ordinary is not None and ordinary > 0.0
    assert ordinary == pytest.approx((profit - cash) / ASSETS)

    # The same window with the two flows exchanged: the cash series becomes the larger one.
    swapped = FactorWindow(
        subject=SUBJECT,
        as_of=AS_OF,
        sessions=(),
        periods=window.periods,
        values=MappingProxyType(
            {
                **dict(window.values),
                (CASH_FLOW_DATASET, OPERATING_CASH_FLOW_COLUMN): window.series(
                    INCOME_DATASET, CONSOLIDATED_NET_PROFIT_COLUMN
                ),
                (INCOME_DATASET, CONSOLIDATED_NET_PROFIT_COLUMN): window.series(
                    CASH_FLOW_DATASET, OPERATING_CASH_FLOW_COLUMN
                ),
            }
        ),
    )
    reversed_answer = _accruals_ttm(swapped)

    assert reversed_answer is not None
    assert reversed_answer == pytest.approx(-ordinary)


# --- the stability statistic ----------------------------------------------------------------------


def test_the_stability_is_the_dispersion_of_four_trailing_margins_and_not_of_the_filed_ones() -> (
    None
):
    """The three candidate statistics, and that this fixture separates all of them.

    A cumulative-as-filed margin series and a single-quarter one are both computable from the same
    window, and both are refused -- the first mixes a three-month margin with a nine-month one, the
    second is dominated by seasonality by construction. The assertion is that the answer is the
    trailing one and that the other two land elsewhere, so a re-implementation reaching for either
    changes a number here rather than passing.
    """
    window = _window(last=11, count=GROSS_MARGIN_PERIODS)
    revenue = window.series(INCOME_DATASET, TOTAL_REVENUE_COLUMN)
    cost = window.series(INCOME_DATASET, OPERATING_COST_COLUMN)
    trailing = _sample_stdev(_expected_margins(11))
    filed = _sample_stdev(
        [(revenue[index] - cost[index]) / revenue[index] for index in range(len(revenue))]
    )
    quarterly = _sample_stdev(
        [
            (REVENUE_INCREMENTS[index] - COST_INCREMENTS[index]) / REVENUE_INCREMENTS[index]
            for index in range(4, 12)
        ]
    )
    answer = _gross_margin_stability(window)

    assert trailing is not None and filed is not None and quarterly is not None
    assert trailing > MARGIN_FLOOR, "a fixture whose margins coincide separates no implementation"
    assert answer == pytest.approx(trailing)
    assert answer != pytest.approx(filed)
    assert answer != pytest.approx(quarterly)
    assert len(_expected_margins(11)) == GROSS_MARGIN_OBSERVATIONS


def test_a_constant_gross_margin_has_a_stability_at_the_floating_point_floor() -> None:
    """The floor of the statistic, which is what "lower is better" points at.

    An issuer whose cost is a fixed fraction of its revenue in every quarter has a trailing margin
    that never moves. The answer is not *exactly* zero and this test does not claim it is: the four
    margins are computed through two trailing sums and a division, so they agree only to the last
    bits of a double, and the measured dispersion here is 5.6e-17. What is pinned is that it is at
    that floor rather than at the 1.5e-2 the moving fixture produces -- fifteen orders of magnitude
    apart, so `CONSTANT_MARGIN_FLOOR` separates them without being a tuned number.
    """
    share = 0.62
    base = _window(last=11, count=GROSS_MARGIN_PERIODS)
    window = FactorWindow(
        subject=SUBJECT,
        as_of=AS_OF,
        sessions=(),
        periods=base.periods,
        values=MappingProxyType(
            {
                **dict(base.values),
                (INCOME_DATASET, OPERATING_COST_COLUMN): tuple(
                    value * share for value in base.series(INCOME_DATASET, TOTAL_REVENUE_COLUMN)
                ),
            }
        ),
    )
    answer = _gross_margin_stability(window)
    moving = _gross_margin_stability(_window(last=11, count=GROSS_MARGIN_PERIODS))

    assert answer is not None and moving is not None
    assert answer == pytest.approx(0.0, abs=CONSTANT_MARGIN_FLOOR)
    assert moving > MARGIN_FLOOR > CONSTANT_MARGIN_FLOOR
    assert GROSS_MARGIN_STABILITY.direction == "lower_is_better"


def test_a_negative_average_margin_still_has_a_positive_dispersion() -> None:
    """Why a standard deviation and not a coefficient of variation, as an arithmetic fact.

    A gross margin is already dimensionless, so the usual reason to divide by the mean does not
    apply -- and dividing by it is actively wrong here: an issuer selling below cost has a negative
    mean margin, and `stdev / mean` would then be **negative**, ranking the most erratic loss-maker
    below every stable name in a `lower_is_better` cross section. The dispersion this factor
    reports is non-negative by construction whatever the level is.
    """
    base = _window(last=11, count=GROSS_MARGIN_PERIODS)
    revenue = base.series(INCOME_DATASET, TOTAL_REVENUE_COLUMN)
    window = FactorWindow(
        subject=SUBJECT,
        as_of=AS_OF,
        sessions=(),
        periods=base.periods,
        values=MappingProxyType(
            {
                **dict(base.values),
                (INCOME_DATASET, OPERATING_COST_COLUMN): tuple(
                    value * 3.0 + 100.0 * index for index, value in enumerate(revenue)
                ),
            }
        ),
    )
    answer = _gross_margin_stability(window)
    margins = [
        (
            _trailing_twelve_month_sum(
                window.periods[start : start + TRAILING_TWELVE_MONTH_PERIODS],
                revenue[start : start + TRAILING_TWELVE_MONTH_PERIODS],
            ),
            _trailing_twelve_month_sum(
                window.periods[start : start + TRAILING_TWELVE_MONTH_PERIODS],
                window.series(INCOME_DATASET, OPERATING_COST_COLUMN)[
                    start : start + TRAILING_TWELVE_MONTH_PERIODS
                ],
            ),
        )
        for start in range(GROSS_MARGIN_OBSERVATIONS)
    ]
    levels = [
        (top - bottom) / top for top, bottom in margins if top is not None and bottom is not None
    ]

    assert len(levels) == GROSS_MARGIN_OBSERVATIONS
    assert math.fsum(levels) / len(levels) < 0.0, "the fixture's mean margin has to be negative"
    assert answer is not None
    assert answer > 0.0
    coefficient_of_variation = answer / (math.fsum(levels) / len(levels))
    assert coefficient_of_variation < 0.0, (
        "which is the ordering the coefficient of variation would have produced"
    )


def test_a_slice_whose_trailing_revenue_is_not_positive_is_undefined_rather_than_inverted() -> None:
    """The margin's own zero-denominator guard, on a window whose revenue nets to zero.

    A trailing revenue of zero divides and a negative one inverts the margin's sign, so the whole
    factor answers `None` rather than contributing an inverted observation to the dispersion. It is
    a slice-level test rather than a factor-level one: one bad slice of four refuses the factor,
    which is what keeps the statistic over a fixed number of observations.
    """
    base = _window(last=11, count=GROSS_MARGIN_PERIODS)
    zeroed = tuple(0.0 for _ in base.periods)
    window = FactorWindow(
        subject=SUBJECT,
        as_of=AS_OF,
        sessions=(),
        periods=base.periods,
        values=MappingProxyType(
            {**dict(base.values), (INCOME_DATASET, TOTAL_REVENUE_COLUMN): zeroed}
        ),
    )

    assert _gross_margin_stability(window) is None
