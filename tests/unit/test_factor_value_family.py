"""The value family's declarations and arithmetic, as functions of a window (`V2-P3-009`,
`V2-P3-017`).

`tests/integration/panel/test_value_family.py` drives the same four factors through the real
engine against real partitions. This file measures the five things a partition is the wrong
instrument for:

- **The unit of the denominator.** `daily_basic.total_mv` is published in units of 10,000 yuan
  and nothing downstream can catch a mistake about it: a rank IC and a z-score are both
  scale-free, so an earnings yield wrong by a factor of 10,000 reaches every report that quotes
  a number and no test that only ranks. The measurement here is a chain over the four real
  `daily_basic` rows this repository already stores, and it needs no upstream field list --
  `close * total_share` reproduces `total_mv`, and `turnover_rate` reproduces from `float_share`,
  only when the share counts are read as ten-thousands.
- **The cumulative-to-trailing identity.** A-share statements accumulate within the calendar
  fiscal year, so the numerator of an EP is three sums of stored numbers rather than one stored
  number. The identity is asserted at all four quarter ends against an independent derivation --
  the window is built from *quarterly increments* and the expected answer is the sum of the four
  most recent of them, so an implementation that dropped or double-counted a term lands somewhere
  else.
- **The branch that only fires on a window this engine will not build.** `max_window_periods ==
  lookback_periods` makes the five-period window contiguous, and contiguity is what puts exactly
  one fiscal year end inside `window[:-1]`. A gapped window has none or two, and
  `_trailing_twelve_months` answers `None` rather than differencing two periods the formula does
  not name. The engine refuses such a window before an evaluator sees it, so the only way to
  drive the branch is to hand the function one directly.
- **Real rows this repository does not store.** Whether `income.total_revenue` and
  `income.revenue` are two numbers or one is decided by the endpoint and not by a partition
  written here: every stored `income` row carries them equal, and three of the four securities
  those rows belong to are `comp_type=1` names in the 97.5% that agree while the fourth is a
  bank. The rows that separate the two columns are recorded as constants and asserted directly.
- **Real rows that decide a caliber and an identity.** `V2-P3-017`'s EPcut reads a column on an
  endpoint that publishes one deducted-profit level and does not say which it is, and a fixture
  cannot answer that either -- so the answer is held against `income`'s two levels on real
  filings where they are 3.169x apart, and the cumulative-versus-ratio question is held against
  five real contiguous filings of one security.
- **The declarations themselves**, including the one the family is *asked* about: which stored
  statement projections carry a deducted-profit column. Until `V2-P3-017` the answer was "none"
  and EPcut was a disclosure; it is now "exactly one", asserted in the same falsifiable shape so
  that both widening it further and reverting it fail. And that none of the four columns the
  measured refusals are concentrated in is read here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Final

import pytest

from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATA_COLUMNS,
    DAILY_BASIC_DATASET,
    DAILY_BASIC_NULLABLE_COLUMNS,
)
from openalpha_cn.domain.factor import FactorField
from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    FINANCIAL_INDICATOR_DATASET,
    INCOME_DATASET,
    KNOWN_FINANCIAL_STATEMENT_LIMITATIONS,
    STATEMENT_DATA_COLUMNS,
)
from openalpha_cn.panel_factors import (
    BOOK_EQUITY_COLUMN,
    BOOK_TO_PRICE,
    CNY_PER_MARKET_CAP_UNIT,
    DEDUCTED_EARNINGS_YIELD_TTM,
    DEDUCTED_NET_PROFIT_COLUMN,
    EARNINGS_YIELD_TTM,
    FACTOR_DEFINITIONS,
    MARKET_CAP_COLUMN,
    MARKET_CAP_SESSIONS,
    NET_PROFIT_COLUMN,
    SALES_YIELD_TTM,
    TOTAL_REVENUE_COLUMN,
    TRAILING_TWELVE_MONTH_PERIODS,
    FactorEngineError,
    FactorWindow,
    _book_to_price,
    _deducted_earnings_yield_ttm,
    _earnings_yield_ttm,
    _market_capitalisation,
    _numeric,
    _sales_yield_ttm,
    _trailing_twelve_months,
)

AS_OF: Final[datetime] = datetime(2026, 5, 20, 4, 0, tzinfo=UTC)
SESSION: Final[date] = date(2026, 5, 19)

SHARES_PER_SHARE_COUNT_UNIT: Final[float] = 10_000.0
"""Shares per unit of `daily_basic.total_share` / `float_share`, which step one below measures.

Declared in the test rather than imported, because `panel_factors` has no such constant: the
family divides by a *capitalisation* and never by a share count. It exists here only as the
middle term of the chain that measures `CNY_PER_MARKET_CAP_UNIT`.
"""


# --- the real rows the unit chain is measured on -------------------------------------------------
#
# Copied from `tests/unit/domain/test_daily_prices.py`'s `VALUATIONS`, `SPARSE_VALUATIONS` and
# `BARS`, which captured them from the live endpoint. Restated here rather than imported: that
# module is a test and importing across two of them would make this file's evidence depend on
# another file's fixture layout, which is the coupling `panel_fixtures.py` exists to avoid for
# the shared corpus.

# (ts_code, session, close, total_share, float_share, total_mv, circ_mv, turnover_rate, vol)
# fmt: off
STORED_VALUATIONS: Final[tuple[tuple[str, str, float, float, float, float, float, float, float
                                     | None], ...]] = (
    ("000001.SZ", "2026-06-12", 11.24, 1940591.8198, 1940560.0653, 21812252.0568, 21811895.1868,
     1.0473, 2032355.46),
    ("600519.SH", "2026-06-12", 1291.91, 125008.1601, 125008.1601, 161499291.9856, 161499291.9856,
     0.4039, 50494.78),
    ("300290.SZ", "2018-01-02", 8.71, 32142.9652, 12994.6866, 279965.2269, 113183.7203,
     4.9386, None),
    ("600527.SH", "2005-01-04", 8.15, 8000.0, 3000.0, 65200.0, 24450.0, 0.3157, None),
)
# fmt: on

SHARES_PER_LOT: Final[float] = 100.0
"""What `daily.vol` counts in. `TURNOVER_60_NOTE` measured it on the first row above."""

CANDIDATE_MARKET_CAP_UNITS: Final[tuple[float, ...]] = (1.0, 100.0, 10_000.0, 100_000_000.0)
"""Yuan per unit of `total_mv`, the four readings a reader could plausibly take.

One yuan, one hundred (a lot), ten thousand (万) and one hundred million (亿) -- the three
multipliers Chinese financial reporting actually uses, plus the identity. Exactly one of them
reproduces the stored `close`.
"""

WORST_RECONCILIATION_GAP: Final[float] = 1e-8
"""How closely the reconstructed price has to match the stored `close`, and it is not a free
parameter.

The measured worst case over the four rows and both capitalisation columns is **2.4e-9** -- the
rounding of two columns published to two and four decimals -- and the nearest wrong reading is
out by a factor of 10,000. So any tolerance between about 3e-9 and 1e-4 accepts exactly one
candidate and rejects the other three; this one sits four orders of magnitude inside that
interval on each side, which is what keeps the test from being a statement about the tolerance.
"""


# --- the fiscal-period corpus the trailing identity is measured against ---------------------------

QUARTERLY_INCREMENTS: Final[tuple[float, ...]] = (
    11.0,
    23.0,
    37.0,
    53.0,
    71.0,
    97.0,
    113.0,
    139.0,
)
"""Eight quarters of *incremental* profit, 2024Q1 through 2025Q4, no two alike and none zero.

The window handed to `_trailing_twelve_months` is built by accumulating these inside each fiscal
year, which is what a stored A-share statement carries; the expected answer is the plain sum of
the four most recent increments. So the assertion compares two derivations that share only these
eight numbers -- an implementation that took `cumulative[-1] - cumulative[0]`, or that reached for
the wrong December, lands on a different number for every one of the four quarter ends.
"""

QUARTER_ENDS: Final[tuple[tuple[int, int], ...]] = ((3, 31), (6, 30), (9, 30), (12, 31))


def _period(index: int) -> date:
    """The `index`-th fiscal quarter end from 2024Q1, on the calendar's own grid."""
    month, day = QUARTER_ENDS[index % 4]
    return date(2024 + index // 4, month, day)


def _cumulative(index: int) -> float:
    """What a stored statement carries at `_period(index)`: the fiscal year to date."""
    start = (index // 4) * 4
    return sum(QUARTERLY_INCREMENTS[start : index + 1])


def _period_window(
    *,
    last: int,
    count: int = TRAILING_TWELVE_MONTH_PERIODS,
    dataset: str = INCOME_DATASET,
    column: str = NET_PROFIT_COLUMN,
    periods: tuple[date, ...] | None = None,
    values: tuple[float, ...] | None = None,
    capitalisation: float | None = None,
) -> FactorWindow:
    """A window ending at `_period(last)`, contiguous unless `periods` overrides it.

    `capitalisation` adds the session half in `total_mv`'s own stored unit, so a window built here
    is the shape `_classify` hands an evaluator for a factor on both axes.
    """
    indices = tuple(range(last - count + 1, last + 1))
    series: dict[tuple[str, str], tuple[float, ...]] = {
        (dataset, column): values
        if values is not None
        else tuple(_cumulative(index) for index in indices)
    }
    sessions: tuple[date, ...] = ()
    if capitalisation is not None:
        series[(DAILY_BASIC_DATASET, MARKET_CAP_COLUMN)] = (capitalisation,)
        sessions = (SESSION,)
    return FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=sessions,
        periods=periods if periods is not None else tuple(_period(index) for index in indices),
        values=MappingProxyType(series),
    )


# --- the declarations ----------------------------------------------------------------------------


def test_the_four_definitions_declare_the_reaches_and_the_axes_the_family_argues_for() -> None:
    """Each declared property, and the two that separate the family's members from each other.

    The flow factors declare five report periods because a trailing twelve months is three sums
    of cumulative figures and needs the window to be contiguous to find them; the stock factor
    declares one because a balance-sheet line is already the answer at its own date. All four
    declare one session, because the denominator of a value ratio is the price now.

    Asserted by value rather than read off the definitions, and each reach against its own
    constant, so a definition that lost its period axis -- which `FactorDefinition` would still
    accept, by dropping the statement column -- fails here rather than silently becoming a
    session-only factor.

    `V2-P3-017`'s EPcut is the fourth and the only one whose period dataset is not `income` or
    `balancesheet`: it is the family's -- and the registry's -- first read of `fina_indicator`,
    which is asserted here rather than left to the constant's prose.
    """
    for definition in (
        EARNINGS_YIELD_TTM,
        BOOK_TO_PRICE,
        SALES_YIELD_TTM,
        DEDUCTED_EARNINGS_YIELD_TTM,
    ):
        assert definition.family == "value"
        assert definition.direction == "higher_is_better"
        assert definition.version == 1
        assert definition.session_datasets == (DAILY_BASIC_DATASET,)
        assert definition.lookback_sessions == MARKET_CAP_SESSIONS == 1
        assert definition.max_window_sessions == MARKET_CAP_SESSIONS
        assert definition.columns_of(DAILY_BASIC_DATASET) == (MARKET_CAP_COLUMN,)
        assert len(definition.period_datasets) == 1

    assert EARNINGS_YIELD_TTM.period_datasets == (INCOME_DATASET,)
    assert EARNINGS_YIELD_TTM.columns_of(INCOME_DATASET) == (NET_PROFIT_COLUMN,)
    assert EARNINGS_YIELD_TTM.lookback_periods == TRAILING_TWELVE_MONTH_PERIODS == 5
    assert EARNINGS_YIELD_TTM.max_window_periods == TRAILING_TWELVE_MONTH_PERIODS

    assert SALES_YIELD_TTM.period_datasets == (INCOME_DATASET,)
    assert SALES_YIELD_TTM.columns_of(INCOME_DATASET) == (TOTAL_REVENUE_COLUMN,)
    assert SALES_YIELD_TTM.lookback_periods == TRAILING_TWELVE_MONTH_PERIODS
    assert SALES_YIELD_TTM.max_window_periods == TRAILING_TWELVE_MONTH_PERIODS

    assert BOOK_TO_PRICE.period_datasets == (BALANCE_SHEET_DATASET,)
    assert BOOK_TO_PRICE.columns_of(BALANCE_SHEET_DATASET) == (BOOK_EQUITY_COLUMN,)
    assert BOOK_TO_PRICE.lookback_periods == 1
    assert BOOK_TO_PRICE.max_window_periods == 1

    assert DEDUCTED_EARNINGS_YIELD_TTM.period_datasets == (FINANCIAL_INDICATOR_DATASET,)
    assert DEDUCTED_EARNINGS_YIELD_TTM.columns_of(FINANCIAL_INDICATOR_DATASET) == (
        DEDUCTED_NET_PROFIT_COLUMN,
    )
    assert DEDUCTED_EARNINGS_YIELD_TTM.lookback_periods == TRAILING_TWELVE_MONTH_PERIODS
    assert DEDUCTED_EARNINGS_YIELD_TTM.max_window_periods == TRAILING_TWELVE_MONTH_PERIODS
    assert {
        dataset
        for definition in FACTOR_DEFINITIONS.definitions
        for dataset in definition.period_datasets
    } & {FINANCIAL_INDICATOR_DATASET} == {FINANCIAL_INDICATOR_DATASET}
    assert [
        definition.qualified_key
        for definition in FACTOR_DEFINITIONS.definitions
        if FINANCIAL_INDICATOR_DATASET in definition.datasets
    ] == [DEDUCTED_EARNINGS_YIELD_TTM.qualified_key]


def test_the_value_family_is_the_only_shipped_factor_on_both_axes_at_once() -> None:
    """The claim the family's own prose makes about the registry, held against the registry.

    Every factor shipped before `V2-P3-009` reads sessions and no filing, so the report-period
    reach fields had no production reader at all. That is what makes these four the first real
    users of the second axis, and it is a property of `FACTOR_DEFINITIONS` rather than of a
    docstring -- so it is asserted there, in both directions. The fourth arrived with
    `V2-P3-017`, and the set literal below is what made adding it a deliberate act.

    **The converse used to be "every other factor declares no period reach at all", and two
    families falsified it in the same build.** `V2-P3-010`'s quality family and `V2-P3-011`'s
    growth family both read filings and no price, so they are on the period axis and not on the
    session one, which the old form counted as seven violations. What was actually being asserted
    is that *these three* are the only factors on **both**, and the honest converse of that is
    that every other factor is on exactly one axis, which is what
    `FactorDefinition.validate_each_axis_is_declared_exactly_when_it_is_read` makes checkable
    from the reach fields alone. `tests/unit/test_factor_quality_family.py::
    test_the_quality_family_is_on_the_period_axis_alone` and
    `tests/unit/test_factor_growth_family.py::
    test_the_growth_family_reads_a_filing_and_no_price_at_all` are the other partition of the
    same registry, asserted from those two families' own side.
    """
    on_both = {
        definition.qualified_key
        for definition in FACTOR_DEFINITIONS.definitions
        if definition.session_datasets and definition.period_datasets
    }

    assert on_both == {
        EARNINGS_YIELD_TTM.qualified_key,
        BOOK_TO_PRICE.qualified_key,
        SALES_YIELD_TTM.qualified_key,
        DEDUCTED_EARNINGS_YIELD_TTM.qualified_key,
    }
    for definition in FACTOR_DEFINITIONS.definitions:
        if definition.qualified_key in on_both:
            continue
        declared = (definition.lookback_periods is None, definition.lookback_sessions is None)
        assert declared in {(True, False), (False, True)}, definition.qualified_key
        assert (definition.max_window_periods is None) == declared[0]
        assert (definition.max_window_sessions is None) == declared[1]


def test_the_two_yield_factors_differ_only_in_the_income_column_they_name() -> None:
    """EP and SP share a reach, a denominator, a direction and an implementation.

    So the *only* thing that can make them two factors rather than one is the column, and if it
    ever stopped doing that they would be a redundant pair that `V2-P3-008`'s analysis would
    report as a finding about the market. Asserted on the content addresses as well as on the
    fields, because that is what a stored observation carries.
    """
    assert EARNINGS_YIELD_TTM.factor_id != SALES_YIELD_TTM.factor_id
    assert EARNINGS_YIELD_TTM.required_fields != SALES_YIELD_TTM.required_fields
    assert NET_PROFIT_COLUMN != TOTAL_REVENUE_COLUMN

    derived = {"key", "required_fields", "factor_id", "qualified_key"}
    shared = {
        name: value
        for name, value in EARNINGS_YIELD_TTM.model_dump(mode="json").items()
        if name not in derived
    }

    assert shared == {
        name: value
        for name, value in SALES_YIELD_TTM.model_dump(mode="json").items()
        if name not in derived
    }
    assert set(shared) == {
        "schema_version",
        "version",
        "family",
        "direction",
        "lookback_sessions",
        "max_window_sessions",
        "lookback_periods",
        "max_window_periods",
    }


def test_the_columns_this_family_reads_are_columns_the_stored_contracts_declare() -> None:
    """`FactorField` validates a column reference syntactically and says so, so the binding
    between these four names and the contracts that declare them has to be asserted somewhere.

    Both directions for the denominator: it is a `daily_basic` column, and it is one of the six
    that module refuses a null in -- which is why a value factor's denominator is never
    `input_missing` on a row that exists, and why the zero is the case worth guarding.
    """
    assert MARKET_CAP_COLUMN in DAILY_BASIC_DATA_COLUMNS
    assert MARKET_CAP_COLUMN not in DAILY_BASIC_NULLABLE_COLUMNS
    assert NET_PROFIT_COLUMN in STATEMENT_DATA_COLUMNS[INCOME_DATASET]
    assert TOTAL_REVENUE_COLUMN in STATEMENT_DATA_COLUMNS[INCOME_DATASET]
    assert BOOK_EQUITY_COLUMN in STATEMENT_DATA_COLUMNS[BALANCE_SHEET_DATASET]
    assert DEDUCTED_NET_PROFIT_COLUMN in STATEMENT_DATA_COLUMNS[FINANCIAL_INDICATOR_DATASET]
    assert DEDUCTED_NET_PROFIT_COLUMN not in STATEMENT_DATA_COLUMNS[INCOME_DATASET]
    for definition in (
        EARNINGS_YIELD_TTM,
        BOOK_TO_PRICE,
        SALES_YIELD_TTM,
        DEDUCTED_EARNINGS_YIELD_TTM,
    ):
        assert FactorField(dataset=DAILY_BASIC_DATASET, column=MARKET_CAP_COLUMN) in (
            definition.required_fields
        )


CONCENTRATED_REFUSAL_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    (INCOME_DATASET, "ebit"),
    (BALANCE_SHEET_DATASET, "total_share"),
    ("cashflow", "free_cashflow"),
    ("fina_indicator", "fcff"),
    ("fina_indicator", "bps"),
)
"""The columns `domain/financial_statements.py` measured the refusals pooling in.

258 of `income`'s 288 refused field reads are `ebit`, 34 of `balancesheet`'s 43 are `total_share`,
**all** 450 of `cashflow`'s are `free_cashflow` and 441 of `fina_indicator`'s 507 are `fcff`;
`bps` is the field whose two versions of one `603049.SH` filing differ by a factor of ten. Listed
here as literals rather than derived, because there is nothing to derive them from -- the census
is prose in that module, and a test that read it back out of the prose would be asserting about a
sentence.
"""


def test_the_family_reads_none_of_the_columns_the_measured_refusals_are_concentrated_in() -> None:
    """The column choice, pinned so that swapping one is a deliberate act.

    `domain/financial_statements.py` records that the refusals are pooled rather than spread, and
    every one of the obvious readings of these four factors lands on one of the pooled columns:
    a book-to-price from `bps` times `total_share`, a cash-flow yield from `free_cashflow`, an
    EV/EBIT from `ebit`. This family reads none of them, which is what makes the residual budget
    arithmetic in its own prose meaningful -- and a later edit that pointed BP at `bps` would
    still satisfy every value assertion in this file's integration twin, because a fixture can
    put any number in any column.
    """
    read = {
        (item.dataset, item.column)
        for definition in (
            EARNINGS_YIELD_TTM,
            BOOK_TO_PRICE,
            SALES_YIELD_TTM,
            DEDUCTED_EARNINGS_YIELD_TTM,
        )
        for item in definition.required_fields
    }

    assert read.isdisjoint(CONCENTRATED_REFUSAL_COLUMNS)
    for dataset, column in CONCENTRATED_REFUSAL_COLUMNS:
        assert column in STATEMENT_DATA_COLUMNS[dataset], "the census names a projected column"


DEDUCTED_PROFIT_FIELDS_SERVED: Final[tuple[str, ...]] = (
    "profit_dedt",
    "dt_eps",
    "dt_netprofit_yoy",
)
"""Tushare's deducted-non-recurring-profit family -- 扣除非经常性损益 -- as the endpoint serves it.

`V2-P3-009`'s review listed four names and `V2-P3-017` measured them against the endpoint on
2026-08-17: three are among `fina_indicator`'s 108 served field names and the fourth,
`dt_profit_to_profit`, is not served at all -- an explicit request for it comes back without it,
the same way `income` drops any of them. So the family this repository can reach is three names
and it is one endpoint's, which is the whole of `DEDUCTED_NET_PROFIT_COLUMN`'s "which endpoint is
not a choice" argument.
"""

DEDUCTED_PROFIT_FIELDS_NOT_SERVED: Final[tuple[str, ...]] = ("dt_profit_to_profit",)
"""The name from that list the endpoint does not serve. Kept as a literal so that a later reader
who finds it in Tushare's own documentation has this repository's measurement to contradict."""


def test_exactly_one_stored_statement_projection_carries_a_deducted_profit_column() -> None:
    """`V2-P3-017`'s widening, as the assertion that used to say the opposite.

    Until this issue the claim here was that **no** stored projection carried a deducted-profit
    column, and the disclosure it supported was that `earnings_yield_ttm` occupied EPcut's slot.
    That test was written to go red "the day a stored projection gains one", and this is that day:
    `fina_indicator` now projects `profit_dedt` and `deducted_earnings_yield_ttm` reads it. The
    assertion is kept in the same falsifiable shape rather than deleted, pointing the other way --
    it is **exactly one** projection and **exactly one** of the three served names, so both
    widening it further and quietly reverting it fail here.

    The three-way split is what makes this more than a spelling check: `income` carries none of
    the family (it does not serve any of it), `fina_indicator` carries `profit_dedt` and not the
    two beside it (`DEDUCTED_NET_PROFIT_COLUMN` measures why: `dt_eps` needs a share count and
    `dt_netprofit_yoy` is a rate the TTM identity cannot touch -- and it is the one whose addition
    moves four filings out of the collapse), and `dt_profit_to_profit` is not upstream at all.
    """
    by_dataset = {
        dataset: sorted(set(columns) & set(DEDUCTED_PROFIT_FIELDS_SERVED))
        for dataset, columns in STATEMENT_DATA_COLUMNS.items()
    }
    projected = {column for columns in STATEMENT_DATA_COLUMNS.values() for column in columns}

    assert by_dataset == {
        INCOME_DATASET: [],
        BALANCE_SHEET_DATASET: [],
        "cashflow": [],
        FINANCIAL_INDICATOR_DATASET: [DEDUCTED_NET_PROFIT_COLUMN],
    }
    assert projected.isdisjoint(DEDUCTED_PROFIT_FIELDS_NOT_SERVED)
    detail = next(
        item.detail
        for item in KNOWN_FINANCIAL_STATEMENT_LIMITATIONS
        if item.code == "the_merge_rule_is_agreement_in_the_projection"
    )
    assert "dt_netprofit_yoy" in detail
    note = FACTOR_DEFINITIONS.note_for(EARNINGS_YIELD_TTM.qualified_key)
    assert note is not None and "EPcut" in note


# --- the deducted profit itself: the two things a field name would have to be trusted for --------

DEDUCTED_PROFIT_AGAINST_INCOME: Final[tuple[tuple[str, str, float, float, float], ...]] = (
    # (security, period, profit_dedt, income.n_income_attr_p, income.n_income)
    ("600739.SH", "2024-12-31", 218_927_918.51, 209_556_865.25, 664_195_391.66),
    ("601318.SH", "2025-03-31", 30_259_000_000.0, 27_016_000_000.0, 35_159_000_000.0),
    ("600030.SH", "2025-09-30", 23_000_488_312.03, 23_158_914_572.54, 23_915_616_390.88),
    ("000002.SZ", "2024-12-31", -45_393_719_289.83, -49_478_429_211.96, -48_703_934_402.33),
)
"""Real served rows, captured on 2026-08-17, chosen where the minority interest is large.

Four of the twenty-four `(security, period)` pairs `V2-P3-017` probed -- six securities over
four periods each -- and not an arbitrary four: `601318.SH` 2025Q1 and `000002.SZ`'s 2024 annual
are the **extremes** of `profit_dedt / n_income_attr_p` over the whole probe (1.120 and 0.917),
so the band asserted below is pinned at both ends by the rows that come closest to breaking it
rather than by comfortable ones.

The first is the decisive one and it is the security this repository already uses to separate
`n_income` from `n_income_attr_p`: on `600739.SH`'s 2024 annual the two differ by a factor of
3.169, so a `profit_dedt` that were the *consolidated* deducted profit would sit near the larger
number. It sits on the smaller. `000002.SZ` is also a loss-maker, where the ordering of the three
numbers reverses and the ratio test still has to come out the same way.
"""

MOUTAI_DEDUCTED_PROFIT: Final[tuple[tuple[str, float, float], ...]] = (
    # (period, profit_dedt, the published roe on the same filing)
    ("2017-09-30", 20_087_008_841.63, 25.4166),
    ("2017-12-31", 27_224_083_628.17, 32.9542),
    ("2018-03-31", 8_510_778_903.45, 8.8887),
    ("2018-06-30", 15_884_168_512.98, 17.0563),
    ("2018-09-30", 24_929_011_158.67, 25.5220),
)
"""`600519.SH`'s `profit_dedt` and `roe` on five contiguous filings, captured live 2026-08-17.

Five contiguous quarter ends ending at 2018Q3, which is exactly the window shape
`TRAILING_TWELVE_MONTH_PERIODS` describes: `window[:-1]` holds one December (2017-12-31) and
`window[0]` is the same quarter one year before `window[-1]`. So the identity's three terms are
all real numbers off one endpoint rather than a fixture's arithmetic, and the answer it produces
is a quantity nothing in the response carries.

The `roe` column rides along because it is the counter-example the quality family already argued
for and this window is the first place both can be driven side by side: both series rise through
the fiscal year, so monotonicity does not separate them, and the property that does is that one
is a sum of yuan and the other is a quotient whose denominator moves.
"""

MOUTAI_2018Q3_TRAILING_DEDUCTED_PROFIT: Final[float] = 32_066_085_945.21
"""`24,929,011,158.67 + 27,224,083,628.17 - 20,087,008,841.63`, computed by hand from the rows
above rather than off a run -- the trailing twelve months of deducted profit ending 2018Q3.

It is 28.6% above the latest stored cumulative (24.93bn), which is the whole reason the identity
exists: a cross section read at one `as_of` that used the latest cumulative would be mixing a
nine-month numerator with somebody else's twelve-month one.
"""

MOUTAI_2018_ANNUAL: Final[tuple[str, float, float]] = ("2018-12-31", 35_585_443_648.60, 34.4643)
"""The filing that closes 2018, captured in the same probe.

Outside the window above -- which ends at 2018Q3 by construction, so that `window[:-1]` holds
exactly one December -- and needed beside it for the equity spread, which is a statement about a
whole fiscal year.
"""

MOUTAI_IMPLIED_EQUITY_SPREAD: Final[float] = 0.10
"""How far apart the equity implied by pairing this endpoint's own `roe` with its own
`profit_dedt` gets across one fiscal year, as a floor on the measured figure.

Measured on 2018's four filings: 95.748bn, 93.128bn, 97.677bn and 103.253bn, a spread of
**10.87%** between the smallest and the largest. That is the falsifiable form of "a ratio is not
a sum" -- differencing two cumulative `roe` figures would be differencing two quotients with
different denominators, so the result is not any quarter's return. `profit_dedt` has no such
problem because it has no denominator, which is the whole of why `V2-P3-017` could put a factor
on this endpoint where `V2-P3-010` could not.
"""


def test_the_deducted_profit_is_the_attributable_level_and_not_the_consolidated_one() -> None:
    """EPcut's numerator caliber, decided by measurement rather than by the field's name.

    `NET_PROFIT_COLUMN` makes this choice for EP between two columns of one endpoint and can
    point at both. EPcut cannot: `fina_indicator` serves one deducted-profit level and does not
    say which it is, so the question is answered by holding it against `income`'s two on real
    filings where they are far apart.

    The bound is one-sided and both sides are asserted, because "closer to" is the shape of claim
    that passes on any fixture: `profit_dedt / n_income_attr_p` is required to stay inside
    `[0.90, 1.13]` on every row, and `profit_dedt / n_income` is required to leave it on the row
    where the minority interest is large. On `600739.SH`'s 2024 annual those are 1.045 and 0.330,
    a factor of 3.17 apart, which is the same 3.169 `NET_PROFIT_COLUMN` records between the two
    `income` columns themselves -- so the deduction moves this number by 4.5% and the choice of
    level moves it by 217%.
    """
    attributable_band = (0.90, 1.13)
    ratios = {
        security: (deducted / attributable, deducted / consolidated)
        for security, _, deducted, attributable, consolidated in DEDUCTED_PROFIT_AGAINST_INCOME
    }

    for security, (to_attributable, _) in ratios.items():
        assert attributable_band[0] <= to_attributable <= attributable_band[1], security
    assert not attributable_band[0] <= ratios["600739.SH"][1] <= attributable_band[1]
    assert round(ratios["600739.SH"][0], 3) == 1.045
    assert round(ratios["600739.SH"][1], 3) == 0.330
    assert 3.169 < 664_195_391.66 / 209_556_865.25 < 3.170
    assert 3.16 < ratios["600739.SH"][0] / ratios["600739.SH"][1] < 3.17
    assert DEDUCTED_EARNINGS_YIELD_TTM.columns_of(FINANCIAL_INDICATOR_DATASET) == (
        DEDUCTED_NET_PROFIT_COLUMN,
    )


def test_the_deducted_profit_is_a_cumulative_sum_the_identity_reaches_and_the_roe_beside_it_is_not() -> (  # noqa: E501
    None
):
    """Why the TTM identity may touch this `fina_indicator` column when it may not touch `roe`.

    `RETURN_ON_EQUITY_TTM` refuses `fina_indicator.roe` on the ground that the cumulative-to-TTM
    identity is an identity about **sums**. Monotonicity does not separate the two -- both series
    rise through the fiscal year -- so both halves are measured on the same five real filings.

    For `profit_dedt`: it resets at each Q1 and accumulates inside the year, and
    `_trailing_twelve_months` over the contiguous window produces `MOUTAI_2018Q3_TRAILING
    _DEDUCTED_PROFIT`, which is 28.6% away from the latest stored cumulative. So the identity is
    doing arithmetic rather than passing a number through, and the number it produces is one the
    endpoint does not serve.

    For `roe`: pairing the endpoint's own `roe` with its own `profit_dedt` on each of 2018's four
    filings implies four different equity bases spanning 10.87%, so differencing two cumulative
    `roe` figures differences two quotients with different denominators. That is the falsifiable
    content of "a ratio is not a sum", and it is why `deducted_earnings_yield_ttm` may read this
    endpoint while `return_on_equity_ttm` may not.
    """
    periods = tuple(date.fromisoformat(day) for day, _, _ in MOUTAI_DEDUCTED_PROFIT)
    deducted = tuple(value for _, value, _ in MOUTAI_DEDUCTED_PROFIT)
    within_2018 = [
        *(row for row in MOUTAI_DEDUCTED_PROFIT if row[0].startswith("2018")),
        MOUTAI_2018_ANNUAL,
    ]
    implied = [value / (roe / 100.0) for _, value, roe in within_2018]

    assert deducted[2] < deducted[0], "Q1 resets rather than continuing the previous year"
    assert list(deducted[2:]) == sorted(deducted[2:]), "and accumulates from there"

    window = FactorWindow(
        subject="600519.SH",
        as_of=AS_OF,
        sessions=(),
        periods=periods,
        values=MappingProxyType(
            {(FINANCIAL_INDICATOR_DATASET, DEDUCTED_NET_PROFIT_COLUMN): deducted}
        ),
    )
    trailing = _trailing_twelve_months(
        window, dataset=FINANCIAL_INDICATOR_DATASET, column=DEDUCTED_NET_PROFIT_COLUMN
    )

    assert trailing is not None
    assert round(trailing, 2) == MOUTAI_2018Q3_TRAILING_DEDUCTED_PROFIT
    assert abs(trailing / deducted[-1] - 1.0) > 0.28, "not the latest cumulative"
    assert max(implied) / min(implied) - 1.0 > MOUTAI_IMPLIED_EQUITY_SPREAD


def test_ep_is_what_epcut_reduces_to_and_the_two_are_not_the_same_number() -> None:
    """The pair, held apart on one window rather than in two docstrings.

    EP and EPcut share `_yield_on_market_capitalisation`, a denominator, a reach and a direction,
    and differ in exactly one `(dataset, column)`. That is the shape in which an assertion stops
    discriminating, so the two evaluators are driven over **one** window carrying both series and
    the answers are required to differ -- and to differ by the fraction the numerators do, which
    is what makes this a statement about the wiring rather than about the fixture's magnitudes.

    The numerator series here are `600739.SH`'s two real 2024 figures scaled into a five-period
    cumulative shape: the deducted profit is 1.0447 times the attributable one, so a wiring that
    read `income.n_income_attr_p` for both would land 4.47% away rather than on a different
    order of magnitude -- which is exactly the size of error a plausible-looking fixture hides.
    """
    attributable = tuple(_cumulative(index) for index in range(4, 9))
    deducted = tuple(value * 1.0447 for value in attributable)
    window = FactorWindow(
        subject="600739.SH",
        as_of=AS_OF,
        sessions=(SESSION,),
        periods=tuple(_period(index) for index in range(4, 9)),
        values=MappingProxyType(
            {
                (INCOME_DATASET, NET_PROFIT_COLUMN): attributable,
                (FINANCIAL_INDICATOR_DATASET, DEDUCTED_NET_PROFIT_COLUMN): deducted,
                (DAILY_BASIC_DATASET, MARKET_CAP_COLUMN): (500.0,),
            }
        ),
    )

    ep = _earnings_yield_ttm(window)
    epcut = _deducted_earnings_yield_ttm(window)

    assert ep is not None and epcut is not None
    assert ep != epcut
    assert round(epcut / ep, 4) == 1.0447
    assert EARNINGS_YIELD_TTM.factor_id != DEDUCTED_EARNINGS_YIELD_TTM.factor_id
    assert EARNINGS_YIELD_TTM.required_fields != DEDUCTED_EARNINGS_YIELD_TTM.required_fields


@pytest.mark.parametrize("stored", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_stored_cell_is_refused_because_the_collapse_is_built_on_equality(
    stored: float,
) -> None:
    """`domain/financial_statements.py::_require_finite_values`, applied at the engine's read.

    That function refuses a non-finite cell before the domain's collapse because both of its
    rules are built on `==` and `nan != nan`: two rows byte-identical apart from carrying `nan`
    in the same column would become two versions rather than one, and a read of that column would
    raise on a field where the publisher said the same non-answer twice. `V2-P3-009` gave the
    engine the same collapse, so it needs the same guard -- and it needs it more, because
    `domain/panel_batch.py::_encode_column` encodes `nan` and the infinities **on purpose** ("a
    missing or non-finite panel observation is ordinary data here"), so a partition may hold one
    where the domain's own writers cannot.

    Driven directly on `_numeric` for `_reversal_1d`'s stated reason: nothing this repository
    writes can produce a non-finite statement cell, since `providers/tushare.py::_finite_number`
    refuses one at the boundary, and a guard whose only evidence was a docstring is the
    declaration this test exists to replace.
    """
    with pytest.raises(FactorEngineError, match="must be a finite number"):
        _numeric(
            stored,
            dataset=INCOME_DATASET,
            column=NET_PROFIT_COLUMN,
            subject="000001.SZ",
            point=date(2025, 3, 31),
        )

    # The finite path is unchanged, so the refusal above is about the value and not about the
    # column: a stored `None` is a real answer -- an empty upstream cell -- and stays one.
    assert (
        _numeric(
            12.5,
            dataset=INCOME_DATASET,
            column=NET_PROFIT_COLUMN,
            subject="000001.SZ",
            point=date(2025, 3, 31),
        )
        == 12.5
    )
    assert (
        _numeric(
            None,
            dataset=INCOME_DATASET,
            column=NET_PROFIT_COLUMN,
            subject="000001.SZ",
            point=date(2025, 3, 31),
        )
        is None
    )


# --- the two revenue columns, on the rows that separate them -------------------------------------
#
# `income.total_revenue` against `income.revenue`, captured 2026-08-13 by `V2-P3-009`'s review
# from the served rows -- the whole history of 14 comp_type 2/3/4 names and a 47-name comp_type=1
# sample -- and recorded here to the yuan, which is four orders of magnitude finer than the gap
# being asserted.
#
# (ts_code, comp_type, report_period, total_revenue, revenue, (total_revenue - revenue) / revenue)
TWO_REVENUE_COLUMNS: Final[tuple[tuple[str, str, str, float, float, float], ...]] = (
    ("600519.SH", "1", "20240930", 123_122_542_625.0, 120_776_131_875.0, 0.0194),
    ("002208.SZ", "1", "20220930", 1_612_572_587.0, 1_604_974_825.0, 0.0047),
    ("000001.SZ", "2", "20180630", 57_241_000_000.0, 57_241_000_000.0, 0.0),
)
"""Three real rows: two ordinary industrials where the columns differ, one bank where they do not.

The third is the row this file's own source used to cite as an "ordinary industrial" on which
the two are equal. It is `comp_type='2'` -- a bank -- so it was evidence for the opposite of what
it was quoted for, and the two industrials are where the endpoint actually separates the columns.
"""

WORST_TWO_COLUMN_GAP_ROUNDING: Final[float] = 5e-5
"""How closely the recorded gap has to reproduce, and it decides nothing.

The gaps being asserted are 1.94e-2 and 4.7e-3 and the two are three orders of magnitude apart,
so any tolerance below about 1e-3 separates them from each other and from the bank row's zero.
This one is the rounding of the third decimal place of a percentage and nothing else.
"""


def test_the_top_line_and_revenue_are_two_different_numbers_on_a_real_industrial_row() -> None:
    """`total_revenue` and not `revenue`, decided by rows rather than by a reading of the statement.

    This test used to assert that no fixture could decide the choice, on the ground that the two
    columns are equal on an ordinary industrial and part only for the company types whose top line
    is not one number. `V2-P3-009`'s review measured it and every clause of that is false: over
    the whole history of 14 banks, insurers and brokers the two columns carry the same number on
    all 1,350 rows, the only place this endpoint separates them is `comp_type=1` -- 37 of 1,468
    rows in a 47-name sample, including **all 42** of `600519.SH`'s -- and the `000001.SZ` row the
    argument named as its ordinary industrial is one of the banks.

    So the choice is decidable on a real row and this is that assertion. What it does not measure
    is which way an evaluator points: `tests/integration/panel/test_value_family.py` builds a
    partition whose `revenue` is half its `total_revenue` and drives all three factors through the
    engine over it, which is where a `_sales_yield_ttm` reading the wrong column lands somewhere
    else.
    """
    for security, comp_type, period, top_line, revenue, gap in TWO_REVENUE_COLUMNS:
        where = f"{security} comp_type={comp_type} {period}"
        assert top_line >= revenue, where
        assert (top_line - revenue) / revenue == pytest.approx(
            gap, abs=WORST_TWO_COLUMN_GAP_ROUNDING
        ), where
        assert (comp_type == "1") == (top_line != revenue), where

    assert TOTAL_REVENUE_COLUMN == "total_revenue"
    assert "revenue" in STATEMENT_DATA_COLUMNS[INCOME_DATASET]
    assert SALES_YIELD_TTM.columns_of(INCOME_DATASET) == ("total_revenue",)
    assert "revenue" not in SALES_YIELD_TTM.columns_of(INCOME_DATASET)


# --- the unit of the denominator ------------------------------------------------------------------


@pytest.mark.parametrize("row", STORED_VALUATIONS, ids=lambda row: f"{row[0]}@{row[1]}")
def test_the_market_cap_unit_is_ten_thousand_yuan_and_no_other_reading_reproduces_the_close(
    row: tuple[str, str, float, float, float, float, float, float, float | None],
) -> None:
    """`CNY_PER_MARKET_CAP_UNIT`, measured rather than taken from a field list.

    The chain has two links and neither consults the upstream's documentation:

    1. `total_share` and `float_share` are in units of 10,000 shares, which the turnover column
       witnesses -- `vol * SHARES_PER_LOT / (float_share * 10,000)` is the stored `turnover_rate`
       as a percentage. That link is asserted here on the two rows that carry a `vol`.
    2. `total_mv` is the capitalisation in whatever unit makes `capitalisation / share count`
       equal the session's own `close`. Of the four multipliers Chinese financial reporting uses,
       exactly one does -- on every stored row -- and the others miss by two, four and eight
       orders of magnitude.

    Parametrised so a failure names the row, and run over all four rather than the two recent
    ones, because `600527.SH`'s 2005 session and `300290.SZ`'s 2018 one are the era where a
    convention could plausibly have differed.
    """
    _, _, close, total_share, float_share, total_mv, circ_mv, turnover_rate, vol = row
    shares = total_share * SHARES_PER_SHARE_COUNT_UNIT

    if vol is not None:
        traded = vol * SHARES_PER_LOT
        assert traded / (float_share * SHARES_PER_SHARE_COUNT_UNIT) * 100.0 == pytest.approx(
            turnover_rate, rel=1e-4
        )

    reproduced = [
        unit
        for unit in CANDIDATE_MARKET_CAP_UNITS
        if total_mv * unit / shares == pytest.approx(close, rel=WORST_RECONCILIATION_GAP)
    ]

    assert reproduced == [CNY_PER_MARKET_CAP_UNIT]
    assert CNY_PER_MARKET_CAP_UNIT == 10_000.0
    # `circ_mv` is the same statement over the float, which is what makes the pair coherent:
    # reading `total_mv` in one unit and `circ_mv` in another would be unnoticeable downstream.
    assert circ_mv * CNY_PER_MARKET_CAP_UNIT / (
        float_share * SHARES_PER_SHARE_COUNT_UNIT
    ) == pytest.approx(close, rel=WORST_RECONCILIATION_GAP)


def test_the_capitalisation_an_evaluator_divides_by_is_the_stored_column_carried_into_yuan() -> (
    None
):
    """`_market_capitalisation` applies the unit rather than leaving it to the caller.

    Stated as a number rather than as `stored * CNY_PER_MARKET_CAP_UNIT`, which would be the
    implementation restated: `000001.SZ`'s stored 21,812,252.0568 is 218.1 billion yuan, and that
    is the figure a reader can check against the company.
    """
    window = _period_window(last=7, capitalisation=21812252.0568)

    assert _market_capitalisation(window) == pytest.approx(218_122_520_568.0)


def test_the_capitalisation_is_the_newest_cell_of_whatever_window_it_is_handed() -> None:
    """The evaluator's own indexing, which no partition can separate at a one-session reach.

    `MARKET_CAP_SESSIONS` is 1, so `_classify` hands this function a one-cell series and `[0]` and
    `[-1]` are the same cell -- measured as a mutant, pointing `_market_capitalisation` at `[0]`
    leaves `tests/integration/panel/test_value_family.py` entirely green. That is the shape this
    repository has been caught by four times: a column asserted on a fixture that cannot tell two
    answers apart.

    So the convention is pinned here instead, on a two-cell window the engine will not build. It
    is the same convention every other evaluator in this module follows and the same one
    `FactorWindow` documents -- `[-1]` is the most recent point knowable at `as_of` -- so a
    widened `MARKET_CAP_SESSIONS` would keep meaning "the newest cap" rather than quietly
    becoming "the cap at the start of the window".
    """
    window = FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=(date(2026, 5, 18), SESSION),
        periods=tuple(_period(index) for index in range(3, 8)),
        values=MappingProxyType(
            {
                (INCOME_DATASET, NET_PROFIT_COLUMN): tuple(
                    _cumulative(index) for index in range(3, 8)
                ),
                (DAILY_BASIC_DATASET, MARKET_CAP_COLUMN): (11.0, 23.0),
            }
        ),
    )

    assert _market_capitalisation(window) == pytest.approx(23.0 * CNY_PER_MARKET_CAP_UNIT)
    assert _earnings_yield_ttm(window) == pytest.approx(
        sum(QUARTERLY_INCREMENTS[4:8]) / (23.0 * CNY_PER_MARKET_CAP_UNIT)
    )


@pytest.mark.parametrize("stored", [0.0, -1.0, -21812252.0568])
def test_a_capitalisation_that_is_not_positive_is_undefined_rather_than_a_division(
    stored: float,
) -> None:
    """The branch behind `undefined_value`, and unlike this module's siblings it is reachable.

    `domain/daily_prices.py` requires `total_mv` to be a finite float and not a positive one --
    only `DAILY_PRICE_COLUMNS`' five get the positivity check -- so a stored zero is readable
    through this repository's own writers, and the integration twin provokes it end to end. A
    negative is not reachable and is refused on the cheaper of the two options: it would divide
    into a value whose sign is the numerator's reversed.
    """
    window = _period_window(last=7, capitalisation=stored)

    assert _market_capitalisation(window) is None
    assert _earnings_yield_ttm(window) is None
    assert (
        _sales_yield_ttm(_period_window(last=7, column=TOTAL_REVENUE_COLUMN, capitalisation=stored))
        is None
    )


# --- the cumulative-to-trailing identity ----------------------------------------------------------


@pytest.mark.parametrize("last", [4, 5, 6, 7])
def test_the_trailing_twelve_months_is_the_sum_of_the_last_four_quarterly_increments(
    last: int,
) -> None:
    """The identity at every one of the four fiscal quarter ends.

    The window carries *cumulative* figures, which is what a stored A-share statement holds, and
    the expected answer is built from the increments they were accumulated from -- so the two
    sides share only `QUARTERLY_INCREMENTS` and an implementation that differenced the wrong pair
    lands elsewhere at every `last`.

    The 12-31 case is the one that looks like it might be special and is not: there the December
    inside the window *is* `window[0]`, the two terms cancel, and the answer is the annual
    cumulative figure itself -- which is correct, because an annual cumulative already is the
    trailing twelve months.
    """
    window = _period_window(last=last)
    expected = sum(QUARTERLY_INCREMENTS[last - 3 : last + 1])

    assert _period(last) == window.periods[-1]
    assert len(window.periods) == TRAILING_TWELVE_MONTH_PERIODS
    assert _trailing_twelve_months(
        window, dataset=INCOME_DATASET, column=NET_PROFIT_COLUMN
    ) == pytest.approx(expected)


def test_the_trailing_sum_is_not_the_window_difference_nor_the_latest_cumulative() -> None:
    """The two wrong answers a fixture has to be able to tell apart from the right one.

    `cumulative[-1] - cumulative[0]` and `cumulative[-1]` are the two implementations somebody
    reaches for first, and on a window ending at an annual period the second is even correct. At
    `last=6` (2025Q3) all three are different numbers, which is what makes the assertion above a
    measurement rather than a coincidence.
    """
    window = _period_window(last=6)
    cumulative = window.series(INCOME_DATASET, NET_PROFIT_COLUMN)
    answer = _trailing_twelve_months(window, dataset=INCOME_DATASET, column=NET_PROFIT_COLUMN)

    assert answer is not None
    assert answer != pytest.approx(cumulative[-1])
    assert answer != pytest.approx(cumulative[-1] - cumulative[0])


def test_a_window_with_a_gap_has_no_trailing_twelve_months_rather_than_a_wrong_one() -> None:
    """The branch `max_window_periods == lookback_periods` makes unreachable through the engine.

    Contiguity is what puts exactly one fiscal year end inside `window[:-1]`. A window that skips
    the December has none and a window that reaches back far enough has two, and in both cases the
    formula's three terms are not the periods it names -- so the answer is `None`, hence
    `undefined_value`, rather than a difference between two periods that are not a year apart.

    Neither window is one this engine will build: `_overruns_its_span` refuses a five-period
    window spanning six quarters before an evaluator sees it. That is why the branch is driven on
    the function directly, which is `_sample_stdev`'s precedent and its reason.
    """
    without_a_year_end = _period_window(
        last=6,
        periods=(
            date(2024, 3, 31),
            date(2024, 6, 30),
            date(2024, 9, 30),
            date(2025, 3, 31),
            date(2025, 6, 30),
        ),
        values=(11.0, 34.0, 71.0, 71.0, 168.0),
    )
    with_two = _period_window(
        last=4,
        periods=(
            date(2023, 12, 31),
            date(2024, 3, 31),
            date(2024, 6, 30),
            date(2024, 12, 31),
            date(2025, 3, 31),
        ),
        values=(7.0, 11.0, 34.0, 124.0, 71.0),
    )

    for window in (without_a_year_end, with_two):
        assert (
            _trailing_twelve_months(window, dataset=INCOME_DATASET, column=NET_PROFIT_COLUMN)
            is None
        )
        assert (
            _earnings_yield_ttm(
                _period_window(
                    last=6,
                    periods=window.periods,
                    values=window.series(INCOME_DATASET, NET_PROFIT_COLUMN),
                    capitalisation=100.0,
                )
            )
            is None
        )


# --- the sign -------------------------------------------------------------------------------------


def test_a_loss_making_issuer_gets_a_negative_earnings_yield_rather_than_no_answer() -> None:
    """The judgement that makes this family yields rather than multiples, driven on real numbers.

    `603333.SH`'s 2020Q1 is the loss this repository already stores: `n_income_attr_p` is
    -15,047,436.31. A cross section that reported `undefined_value` for every such name would
    exclude the part of the market a value factor has the most to say about, and the published
    `daily_basic.pe_ttm` -- which is what an inverted multiple would have read -- is simply null
    for it, on 1,214 of 5,338 rows of one measured session.

    The magnitude is asserted, not only the sign: a factor that returned `-1` for "loss" would
    satisfy a sign-only assertion and would be a different factor.
    """
    loss = -15047436.31
    window = _period_window(
        last=7,
        values=(0.0, 0.0, 0.0, 0.0, loss),
        capitalisation=100_000.0,
    )
    answer = _earnings_yield_ttm(window)

    assert answer is not None
    assert answer < 0.0
    assert answer == pytest.approx(loss / (100_000.0 * CNY_PER_MARKET_CAP_UNIT))


def test_a_negative_book_value_is_computed_and_negative_rather_than_undefined() -> None:
    """BP's half of the same judgement: an insolvent issuer has negative equity and a real price.

    The stock case needs no trailing sum, so `book_to_price` reads `[-1]` of a one-period window
    -- asserted here with a five-entry window so that an implementation summing or differencing
    the series lands somewhere else.
    """
    window = FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=(SESSION,),
        periods=tuple(_period(index) for index in range(5)),
        values=MappingProxyType(
            {
                (BALANCE_SHEET_DATASET, BOOK_EQUITY_COLUMN): (
                    500.0,
                    400.0,
                    300.0,
                    200.0,
                    -1_000.0,
                ),
                (DAILY_BASIC_DATASET, MARKET_CAP_COLUMN): (2.0,),
            }
        ),
    )
    answer = _book_to_price(window)

    assert answer is not None
    assert answer == pytest.approx(-1_000.0 / (2.0 * CNY_PER_MARKET_CAP_UNIT))


def test_the_three_evaluators_give_three_different_numbers_on_one_window() -> None:
    """Three factors sharing a denominator is exactly where a fixture stops discriminating.

    One window carrying a distinct series in each of the three numerator columns, and three
    answers that are pairwise different -- so an evaluator wired to the wrong column, which is the
    one mistake a shared denominator hides, changes a number here.
    """
    periods = tuple(_period(index) for index in range(3, 8))
    window = FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=(SESSION,),
        periods=periods,
        values=MappingProxyType(
            {
                (INCOME_DATASET, NET_PROFIT_COLUMN): tuple(
                    _cumulative(index) for index in range(3, 8)
                ),
                (INCOME_DATASET, TOTAL_REVENUE_COLUMN): tuple(
                    _cumulative(index) * 7.0 for index in range(3, 8)
                ),
                (BALANCE_SHEET_DATASET, BOOK_EQUITY_COLUMN): (
                    1.0,
                    2.0,
                    3.0,
                    4.0,
                    9_999.0,
                ),
                (DAILY_BASIC_DATASET, MARKET_CAP_COLUMN): (3.0,),
            }
        ),
    )
    answers = (
        _earnings_yield_ttm(window),
        _sales_yield_ttm(window),
        _book_to_price(window),
    )

    assert all(value is not None for value in answers)
    assert len({round(value, 12) for value in answers if value is not None}) == 3
