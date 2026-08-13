"""The growth family's declarations and arithmetic, as functions of a window (`V2-P3-011`).

`tests/integration/panel/test_growth_family.py` drives the same three factors through the real
engine against real partitions. This file measures the five things a partition is the wrong
instrument for:

- **The quarter alignment.** A year-on-year reads two cells four apart, and whether that offset is
  twelve months depends on the window being contiguous rather than on anything the evaluator can
  see. Every assertion here that touches the arithmetic is parametrised over all four alignments,
  because "it works at the alignment my fixture happened to end on" is exactly the shape this
  repository has been caught by.
- **The helper this family deliberately does not call.** `_trailing_twelve_months` searches
  `window.periods[:-1]` for a fiscal year end, and how many that slice holds depends on the reach
  **and** the alignment: four quarters hold exactly one, seven hold one or two, eight hold exactly
  two. So the same helper is a correct identity at a five-period reach, an alignment-dependent
  *wrong number* at eight and a uniform `None` at nine. The count is asserted, not only the
  answer, and the wrong number the eight-period case produces is asserted by value.
- **The number the span bound is the only thing standing between this factor and.** A gapped
  five-period window compares three months of one year against twelve of another and returns a
  number rather than refusing. The engine will not build such a window, so the only way to see
  what it would have said is to hand the evaluator one.
- **The rule for a base that is not strictly positive**, whose whole argument is about the
  ordering of answers the rule *refuses to produce* -- which means the refused answers have to be
  computed somewhere, and that somewhere is here.
- **Real rows this repository does not store.** Whether `n_income` and `n_income_attr_p` give two
  different growth *rates* is not decided by their levels differing: a growth rate divides a
  constant scale out, and the value family's own partition separates the two columns by a
  constant. The rows that separate the rates are recorded as constants and asserted directly.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Final

import pytest

from openalpha_cn.domain.factor import FactorField
from openalpha_cn.domain.financial_statements import (
    INCOME_DATASET,
    STATEMENT_DATA_COLUMNS,
)
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    NET_PROFIT_COLUMN,
    NET_PROFIT_YOY,
    NEWEST_PERIOD,
    QUARTERS_PER_YEAR,
    REVENUE_YOY,
    REVENUE_YOY_ACCELERATION,
    TOTAL_REVENUE_COLUMN,
    TRAILING_TWELVE_MONTH_PERIODS,
    YEAR_EARLIER_PERIOD,
    YEAR_ON_YEAR_ACCELERATION_PERIODS,
    YEAR_ON_YEAR_PERIODS,
    FactorWindow,
    _net_profit_yoy,
    _revenue_yoy,
    _revenue_yoy_acceleration,
    _trailing_twelve_months,
    _year_on_year,
)

AS_OF: Final[datetime] = datetime(2026, 5, 20, 4, 0, tzinfo=UTC)

QUARTER_ENDS: Final[tuple[tuple[int, int], ...]] = ((3, 31), (6, 30), (9, 30), (12, 31))
FIRST_YEAR: Final[int] = 2022
"""Index 0 is 2022Q1, so index 11 is 2024Q4 and a nine-period window ending there fits."""

REVENUE_INCREMENTS: Final[tuple[float, ...]] = (
    101.0, 211.0, 307.0, 419.0,
    131.0, 233.0, 331.0, 439.0,
    151.0, 251.0, 353.0, 457.0,
    173.0, 269.0, 379.0, 487.0,
)  # fmt: skip
"""Sixteen quarters of *incremental* top line, 2022Q1 through 2025Q4, no two alike and none zero.

The window handed to an evaluator carries the figures these accumulate into inside each fiscal
year, which is what a stored A-share statement holds. Every expectation below is built from
explicit slices of this tuple rather than from the accumulator, so the two sides share only these
sixteen numbers -- and the four alignments are deliberately not a smooth series, so an
implementation reading the wrong offset lands somewhere else at every one of them.
"""

PROFIT_INCREMENTS: Final[tuple[float, ...]] = (
    11.0, 23.0, 37.0, 53.0,
    17.0, 29.0, 41.0, 59.0,
    19.0, 31.0, 43.0, 67.0,
    23.0, 37.0, 47.0, 71.0,
)  # fmt: skip
"""The same for `n_income_attr_p`, and deliberately **not** proportional to the revenue series.

Two columns in a constant ratio give the identical growth rate, so a fixture built the way
`tests/integration/panel/test_value_family.py` builds its neighbouring columns -- `revenue` at a
flat half of `total_revenue`, `n_income` at a flat 1.7 of `n_income_attr_p` -- cannot tell any
growth factor from any other. That is the exact shape of this repository's recurring finding and
this tuple exists to avoid it.
"""


def _period(index: int) -> date:
    """The `index`-th fiscal quarter end from 2022Q1, on the calendar's own grid."""
    month, day = QUARTER_ENDS[index % 4]
    return date(FIRST_YEAR + index // 4, month, day)


def _cumulative(increments: tuple[float, ...], index: int) -> float:
    """What a stored statement carries at `_period(index)`: the fiscal year to date."""
    start = (index // 4) * 4
    return math.fsum(increments[start : index + 1])


def _window(
    *,
    last: int,
    count: int,
    series: tuple[tuple[str, tuple[float, ...]], ...] | None = None,
    periods: tuple[date, ...] | None = None,
) -> FactorWindow:
    """A window of `count` periods ending at `_period(last)`, contiguous unless overridden.

    Carries both income columns by default, so one window can be handed to all three evaluators
    and the three answers compared -- which is the assertion a family sharing one dataset needs.
    """
    indices = tuple(range(last - count + 1, last + 1))
    values = dict(series or ())
    if not values:
        values = {
            TOTAL_REVENUE_COLUMN: tuple(_cumulative(REVENUE_INCREMENTS, i) for i in indices),
            NET_PROFIT_COLUMN: tuple(_cumulative(PROFIT_INCREMENTS, i) for i in indices),
        }
    return FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=(),
        periods=periods if periods is not None else tuple(_period(i) for i in indices),
        values=MappingProxyType({(INCOME_DATASET, name): cells for name, cells in values.items()}),
    )


# --- the declarations ----------------------------------------------------------------------------


def test_the_three_definitions_declare_the_reaches_and_the_one_axis_the_family_reads() -> None:
    """Each declared property, and the two that separate the family's members from each other.

    All three read `income` and nothing else, so all three declare **no** session reach -- which
    `FactorDefinition` enforces as an equivalence rather than accepting as a default, and which
    makes these the first shipped factors on the report-period axis alone. The two plain rates
    declare five periods because a year is four quarters back plus the point it is measured from;
    the acceleration declares nine because it differences two such rates a year apart.

    Asserted by value and each reach against its own constant, so a definition that acquired a
    session column -- which `FactorDefinition` would accept -- fails here rather than silently
    becoming a two-axis factor.
    """
    for definition in (REVENUE_YOY, NET_PROFIT_YOY, REVENUE_YOY_ACCELERATION):
        assert definition.family == "growth"
        assert definition.direction == "higher_is_better"
        assert definition.version == 1
        assert definition.session_datasets == ()
        assert definition.lookback_sessions is None
        assert definition.max_window_sessions is None
        assert definition.period_datasets == (INCOME_DATASET,)
        assert definition.lookback_periods == definition.max_window_periods

    assert REVENUE_YOY.columns_of(INCOME_DATASET) == (TOTAL_REVENUE_COLUMN,)
    assert REVENUE_YOY.lookback_periods == YEAR_ON_YEAR_PERIODS == 5
    assert NET_PROFIT_YOY.columns_of(INCOME_DATASET) == (NET_PROFIT_COLUMN,)
    assert NET_PROFIT_YOY.lookback_periods == YEAR_ON_YEAR_PERIODS
    assert REVENUE_YOY_ACCELERATION.columns_of(INCOME_DATASET) == (TOTAL_REVENUE_COLUMN,)
    assert REVENUE_YOY_ACCELERATION.lookback_periods == YEAR_ON_YEAR_ACCELERATION_PERIODS == 9

    assert QUARTERS_PER_YEAR == 4
    assert YEAR_ON_YEAR_PERIODS == QUARTERS_PER_YEAR + 1
    assert YEAR_ON_YEAR_ACCELERATION_PERIODS == 2 * QUARTERS_PER_YEAR + 1
    assert (NEWEST_PERIOD, YEAR_EARLIER_PERIOD) == (-1, -5)
    # The five is `TRAILING_TWELVE_MONTH_PERIODS`' number for a different reason, so the two are
    # separate constants; this pins that they are separate rather than aliases.
    assert YEAR_ON_YEAR_PERIODS == TRAILING_TWELVE_MONTH_PERIODS
    assert REVENUE_YOY.factor_id != NET_PROFIT_YOY.factor_id
    assert REVENUE_YOY.factor_id != REVENUE_YOY_ACCELERATION.factor_id


def test_the_family_is_exactly_three_definitions_and_they_are_this_builds_only_members() -> None:
    """Both directions: the three this issue owns are declared, and nothing else claims the family.

    The second half is what a per-factor test cannot cover. `FactorFamily` is a closed set because
    `V2-P3-008` groups by it and `V2-P3-014` reports per family, so a fourth member arriving from
    somewhere else -- a `V2-P3-010` quality factor mis-labelled, say -- would silently join this
    family's redundancy group and its report tier. Read off the live registry rather than off this
    file's own tuple, so it covers definitions this file does not import.
    """
    declared = tuple(
        item.qualified_key for item in FACTOR_DEFINITIONS.definitions if item.family == "growth"
    )

    assert declared == (
        "revenue_yoy/v1",
        "net_profit_yoy/v1",
        "revenue_yoy_acceleration/v1",
    )
    for handle in declared:
        note = FACTOR_DEFINITIONS.note_for(handle)
        assert note is not None and len(note) > 100


def test_the_growth_family_reads_a_filing_and_no_price_at_all() -> None:
    """The claim the family's own prose makes about the registry, held against the registry.

    Every factor shipped before `V2-P3-011` reads a session dataset -- the nine session-only ones
    and `V2-P3-009`'s three, which are on both axes. These three are the first that read a filing
    and nothing else, so `lookback_sessions is None` has a production reader for the first time.
    A property of `FACTOR_DEFINITIONS` rather than of a docstring, asserted in both directions.
    """
    period_only = {
        definition.qualified_key
        for definition in FACTOR_DEFINITIONS.definitions
        if definition.period_datasets and not definition.session_datasets
    }

    assert period_only == {
        REVENUE_YOY.qualified_key,
        NET_PROFIT_YOY.qualified_key,
        REVENUE_YOY_ACCELERATION.qualified_key,
    }
    for definition in FACTOR_DEFINITIONS.definitions:
        if definition.qualified_key in period_only:
            continue
        assert definition.lookback_sessions is not None
        assert definition.max_window_sessions is not None


def test_the_columns_this_family_reads_are_columns_the_stored_contract_declares() -> None:
    """`FactorField` validates a column reference syntactically and says so, so the binding
    between these two names and the contract that declares them has to be asserted somewhere.

    The pair `n_income` / `n_income_attr_p` is asserted both ways: the column read is the stored
    one and the column refused is stored too, so the choice between them is a choice rather than
    an availability.
    """
    assert TOTAL_REVENUE_COLUMN in STATEMENT_DATA_COLUMNS[INCOME_DATASET]
    assert NET_PROFIT_COLUMN in STATEMENT_DATA_COLUMNS[INCOME_DATASET]
    assert "n_income" in STATEMENT_DATA_COLUMNS[INCOME_DATASET]
    assert "revenue" in STATEMENT_DATA_COLUMNS[INCOME_DATASET]
    assert NET_PROFIT_YOY.required_fields == (
        FactorField(dataset=INCOME_DATASET, column=NET_PROFIT_COLUMN),
    )
    for definition in (REVENUE_YOY, REVENUE_YOY_ACCELERATION):
        assert definition.required_fields == (
            FactorField(dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN),
        )


# --- the arithmetic, at every one of the four quarter alignments ----------------------------------

# (last index, the numerator's own quarters, the year-earlier denominator's own quarters)
YEAR_ON_YEAR_ALIGNMENTS: Final[tuple[tuple[int, slice, slice], ...]] = (
    (8, slice(8, 9), slice(4, 5)),
    (9, slice(8, 10), slice(4, 6)),
    (10, slice(8, 11), slice(4, 7)),
    (11, slice(8, 12), slice(4, 8)),
)
"""The four windows ending at 2024Q1, Q2, Q3 and Q4, each with the increments it accumulates.

Written as explicit slices rather than derived, so each alignment's expectation names the quarters
it is a growth *of*: 2024Q3 over 2023Q3 is nine months against nine months, and the slices say so.
"""


@pytest.mark.parametrize(
    ("last", "recent", "earlier"), YEAR_ON_YEAR_ALIGNMENTS, ids=["Q1", "Q2", "Q3", "Q4"]
)
def test_the_year_on_year_reads_the_same_quarter_one_year_earlier_in_all_four_alignments(
    last: int, recent: slice, earlier: slice
) -> None:
    """The whole of the arithmetic, at each of the four places a window can end.

    A cumulative A-share figure is the fiscal year to date, so `window[-1]` and `window[-5]` cover
    the *same span of the same fiscal year* -- and that is what makes this a year-on-year without
    any accumulation. The expected value is built from explicit increment slices, so an
    implementation reading `[-4]`, or accumulating first, or taking the newest cumulative over the
    newest annual, lands elsewhere at every alignment rather than at one of them.

    The four answers are asserted pairwise distinct at the end of this file's
    `test_the_four_alignments_are_four_different_numbers_so_this_parametrisation_discriminates`,
    without which passing at one alignment would say nothing about the others.
    """
    window = _window(last=last, count=YEAR_ON_YEAR_PERIODS)
    expected = math.fsum(REVENUE_INCREMENTS[recent]) / math.fsum(REVENUE_INCREMENTS[earlier]) - 1.0

    assert window.periods[-1] == _period(last)
    assert window.periods[0] == _period(last - QUARTERS_PER_YEAR)
    assert window.periods[0].month == window.periods[-1].month
    assert window.periods[-1].year - window.periods[0].year == 1
    assert _revenue_yoy(window) == pytest.approx(expected)
    assert _year_on_year(
        window, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN, index=NEWEST_PERIOD
    ) == pytest.approx(expected)


def test_the_four_alignments_are_four_different_numbers_so_this_parametrisation_discriminates() -> (
    None
):
    """The sentinel the alignment parametrisation needs.

    If the four windows happened to give one number, the four cases above would be one case run
    four times and an implementation that ignored the alignment entirely would pass all of them.
    They are four distinct numbers by construction -- `REVENUE_INCREMENTS` is not a smooth series
    -- and this is that construction asserted rather than assumed.
    """
    answers = [
        _revenue_yoy(_window(last=last, count=YEAR_ON_YEAR_PERIODS))
        for last, _, _ in YEAR_ON_YEAR_ALIGNMENTS
    ]

    assert all(value is not None for value in answers)
    assert len({round(value, 12) for value in answers if value is not None}) == 4


@pytest.mark.parametrize("last", [8, 9, 10, 11], ids=["Q1", "Q2", "Q3", "Q4"])
def test_the_acceleration_is_the_difference_of_two_rates_a_year_apart_in_all_four_alignments(
    last: int,
) -> None:
    """`YoY(P) - YoY(P-4)`, off a nine-period window, at each alignment.

    Three of the nine periods are read -- `[-1]`, `[-5]`, `[-9]` -- and each pair is four quarters
    apart, so both rates are same-season and the horizon cancels along with the seasonality. The
    expectation is two explicit ratios of increment slices, which is what makes an implementation
    differencing *adjacent* periods (`YoY(P) - YoY(P-1)`, the six-period construction the
    definition refuses) land somewhere else.
    """
    window = _window(last=last, count=YEAR_ON_YEAR_ACCELERATION_PERIODS)
    cells = tuple(
        _cumulative(REVENUE_INCREMENTS, index)
        for index in (last - 2 * QUARTERS_PER_YEAR, last - QUARTERS_PER_YEAR, last)
    )
    expected = (cells[2] / cells[1] - 1.0) - (cells[1] / cells[0] - 1.0)

    assert len(window.periods) == 9
    assert window.periods[0] == _period(last - 2 * QUARTERS_PER_YEAR)
    assert _revenue_yoy_acceleration(window) == pytest.approx(expected)


def test_the_acceleration_is_not_its_own_first_term_and_three_evaluators_give_three_numbers() -> (
    None
):
    """The degeneracy a family of three sharing one dataset has to be checked against.

    An acceleration whose earlier rate happened to be zero **is** its own recent rate, and a
    fixture with flat growth in the earlier year would make the two factors one. So the earlier
    rate is asserted non-zero and the difference asserted different from the recent rate. The
    third answer is `net_profit_yoy`, which reads a column whose increments are deliberately not
    proportional to the revenue ones -- the mistake a shared dataset hides.
    """
    window = _window(last=11, count=YEAR_ON_YEAR_ACCELERATION_PERIODS)
    recent = _year_on_year(
        window, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN, index=NEWEST_PERIOD
    )
    earlier = _year_on_year(
        window, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN, index=YEAR_EARLIER_PERIOD
    )
    answers = (_revenue_yoy(window), _net_profit_yoy(window), _revenue_yoy_acceleration(window))

    assert recent is not None and earlier is not None
    assert earlier != pytest.approx(0.0)
    assert answers[2] == pytest.approx(recent - earlier)
    assert answers[2] != pytest.approx(recent)
    assert all(value is not None for value in answers)
    assert len({round(value, 12) for value in answers if value is not None}) == 3


# --- the helper this family does not call, and the wrong number it would give ---------------------


@pytest.mark.parametrize("last", [8, 9, 10, 11], ids=["Q1", "Q2", "Q3", "Q4"])
def test_a_nine_period_window_holds_two_year_ends_in_every_alignment(
    last: int,
) -> None:
    """Why this family reads no trailing twelve months, asserted on the **count** and not the
    answer.

    `_trailing_twelve_months` finds its December by searching `window.periods[:-1]`, which for a
    contiguous window of `N` periods is `N - 1` consecutive quarters -- and `K` consecutive
    quarters hold `K // 4` or `K // 4 + 1` fiscal year ends depending on where the window ends.
    At `N = 9` the slice is eight quarters, exactly two calendar years, so it holds exactly two in
    every alignment and the helper answers `None` in all four. That is nine being odd about a
    four-cycle rather than a guard anybody wrote, which is why this family does not rely on it --
    and why the count is what is asserted: the day somebody narrows this reach to eight, the
    failure names the reason instead of the symptom.

    The five-period contrast is asserted beside it, because a test that only found `None`
    everywhere would pass against a helper that always returned `None`.
    """
    nine = _window(last=last, count=YEAR_ON_YEAR_ACCELERATION_PERIODS)
    five = _window(last=last, count=YEAR_ON_YEAR_PERIODS)

    assert len([item for item in nine.periods[:-1] if item.month == 12]) == 2
    assert (
        _trailing_twelve_months(nine, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN) is None
    )
    assert len([item for item in five.periods[:-1] if item.month == 12]) == 1
    assert (
        _trailing_twelve_months(five, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN)
        is not None
    )


EIGHT_PERIOD_WRONG_TRAILING_SUM: Final[float] = 2215.0
"""What `_trailing_twelve_months` returns on an **eight**-period window ending at a Q4, in yuan.

The true trailing twelve months at 2024Q4 is the annual cumulative itself, 1,212. Seven
consecutive quarters ending at 2024Q3 hold exactly **one** December -- 2023-12-31 -- so the helper
finds it, and returns `cumulative[2024Q4] + cumulative[2023Q4] - cumulative[2023Q1]`, which is the
true answer plus a whole fiscal year less that year's Q1: `1,212 + 1,134 - 131 = 2,215`. Recorded
as a literal because "not equal to the right answer" is the assertion this repository has already
been caught making; the magnitude is the finding.
"""


def test_an_eight_period_window_gets_a_wrong_trailing_sum_at_one_alignment() -> None:
    """Why the alignment count above is a measurement rather than a note.

    Eight is the reach at which `[:-1]` is seven quarters, which hold one year end or two
    depending on where the window ends -- so the same helper answers `None` at three alignments
    and a **wrong number** at the fourth, confidently. The wrong number is asserted by value
    against the true trailing twelve months, because a factor family that recorded only "the
    helper is not reused" would be one sentence away from reusing it.

    Nothing in this repository builds an eight-period window; this drives the helper directly,
    which is `_sample_stdev`'s precedent and its reason.
    """
    wrong = _window(last=11, count=8)
    true_trailing = _cumulative(REVENUE_INCREMENTS, 11)

    assert len([item for item in wrong.periods[:-1] if item.month == 12]) == 1
    assert _trailing_twelve_months(
        wrong, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN
    ) == pytest.approx(EIGHT_PERIOD_WRONG_TRAILING_SUM)
    assert true_trailing == pytest.approx(1_212.0)
    # A whole fiscal year, less that year's Q1, added to the right answer -- which is the shape
    # the previous year's December being found instead of this one's has.
    overstated = _cumulative(REVENUE_INCREMENTS, 7) - _cumulative(REVENUE_INCREMENTS, 4)
    assert pytest.approx(true_trailing + overstated) == EIGHT_PERIOD_WRONG_TRAILING_SUM
    for last in (8, 9, 10):
        other = _window(last=last, count=8)
        assert len([item for item in other.periods[:-1] if item.month == 12]) == 2
        assert (
            _trailing_twelve_months(other, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN)
            is None
        )


# --- the number the span bound is the only guard against ------------------------------------------

GAPPED_PERIODS: Final[tuple[date, ...]] = (
    date(2023, 12, 31),
    date(2024, 3, 31),
    date(2024, 6, 30),
    date(2024, 9, 30),
    date(2025, 3, 31),
)
"""Five filings spanning **six** fiscal quarters: the December in the middle is missing.

The shape `tests/integration/panel/test_value_family.py`'s `000063.SZ` has, and the shape
`_period_span`'s own docstring records the engine answering `computed` for before the quarter grid
replaced the panel's period set.
"""

GAPPED_WRONG_YEAR_ON_YEAR: Final[float] = 173.0 / 1134.0 - 1.0
"""What `_year_on_year` returns when handed `GAPPED_PERIODS`: **-84.7%**.

`window[-1]` is 2025Q1, three months of 2025 at 173; `window[-5]` is the 2023 annual, twelve
months at 1,134. The true 2025Q1-over-2024Q1 growth is `173 / 151 - 1`, a **positive** 14.6%. So
the gap does not blur the answer, it reverses its sign and multiplies its size -- which is what
`max_window_periods == lookback_periods` is buying and is the only thing that buys it.
"""


def test_a_gapped_window_handed_to_the_evaluator_returns_a_wrong_number() -> None:
    """The evaluator has no internal contiguity guard, and this is what that costs without one.

    `_year_on_year` reads two cells at a fixed offset and cannot see the periods, so on a window
    whose ends are six quarters apart it compares three months of 2025 against twelve months of
    2023 and answers -84.7% where the truth is +14.6%. The engine never builds such a window --
    `_overruns_its_span` refuses it on the fiscal-quarter grid before an evaluator is called, and
    `tests/integration/panel/test_growth_family.py::
    test_a_missed_filing_is_the_span_bound_and_not_the_count` drives that end to end -- so the
    only way to see the number is to hand the function the window directly.

    Asserted by value in both directions, because "different from the right answer" is the
    assertion whose weakness this repository has already recorded five times: the wrong answer is
    negative, the right one positive, and the gap between them is the guarantee.
    """
    gapped = _window(
        last=13,
        count=YEAR_ON_YEAR_PERIODS,
        periods=GAPPED_PERIODS,
        series=(
            (
                TOTAL_REVENUE_COLUMN,
                (
                    _cumulative(REVENUE_INCREMENTS, 7),
                    _cumulative(REVENUE_INCREMENTS, 8),
                    _cumulative(REVENUE_INCREMENTS, 9),
                    _cumulative(REVENUE_INCREMENTS, 10),
                    _cumulative(REVENUE_INCREMENTS, 12),
                ),
            ),
        ),
    )
    truthful = _window(
        last=12,
        count=YEAR_ON_YEAR_PERIODS,
        series=(
            (
                TOTAL_REVENUE_COLUMN,
                tuple(_cumulative(REVENUE_INCREMENTS, index) for index in range(8, 13)),
            ),
        ),
    )

    assert gapped.periods[-1].year - gapped.periods[0].year == 2
    assert _revenue_yoy(gapped) == pytest.approx(GAPPED_WRONG_YEAR_ON_YEAR)
    assert pytest.approx(-0.8474427, abs=1e-6) == GAPPED_WRONG_YEAR_ON_YEAR
    assert _revenue_yoy(truthful) == pytest.approx(173.0 / 151.0 - 1.0)
    assert _revenue_yoy(truthful) == pytest.approx(0.145695, abs=1e-6)
    assert GAPPED_WRONG_YEAR_ON_YEAR < 0.0 < 173.0 / 151.0 - 1.0


# --- the two profit columns, on the rows that separate their *rates* ------------------------------
#
# `income.n_income_attr_p` against `income.n_income`, captured 2026-08-13 by `V2-P3-011`'s live
# probe from the served rows -- two disjoint 60-security stride samples of the listed universe --
# and recorded here to the fen, which is far finer than the gaps being asserted.
#
# (ts_code, base period, newest period, attributable base, attributable newest,
#  consolidated base, consolidated newest)
TWO_PROFIT_COLUMNS: Final[tuple[tuple[str, str, str, float, float, float, float], ...]] = (
    (
        "002023.SZ", "20240630", "20250630",
        47_079_457.35, 63_704_068.86, 36_491_151.15, 63_367_060.77,
    ),
    (
        "000931.SZ", "20240630", "20250630",
        36_389_974.60, 38_808_886.99, 43_658_940.65, 44_729_844.56,
    ),
    (
        "002314.SZ", "20230630", "20240630",
        25_486_325.05, -118_194_403.73, 167_082_178.40, -47_014_797.80,
    ),
)  # fmt: skip
"""Three real securities whose two profit columns give two **different growth rates**.

`002023.SZ` grows 35.3% on the attributable column and 73.7% on the consolidated one, which is the
shape `NET_PROFIT_YOY` argues about -- a consolidated figure moving further than the parent's share
of it. `000931.SZ` runs the other way, 6.6% against 2.5%. `002314.SZ` is the case where the two
are not even the same order of magnitude: -563.8% against -128.1%, because the attributable column
crosses zero and the consolidated one does not fall as far.
"""


def test_the_two_profit_columns_are_two_different_growth_rates_on_real_rows() -> None:
    """`n_income_attr_p` and not `n_income`, decided by rows rather than by a reading of the pair.

    That the two columns carry different *levels* is already recorded -- `600739.SH`'s 2024 annual
    is 664,195,391.66 against 209,556,865.25, a factor of 3.169 -- and **it does not settle this
    choice**, because a growth rate divides a constant scale out: two columns in a fixed ratio give
    the identical year-on-year, and `tests/integration/panel/test_value_family.py`'s partition puts
    them in exactly such a ratio. So the claim that has to be measured is about the rates, and
    `V2-P3-011`'s live probe measured it: on the securities of two 60-security samples that have a
    comparable pair, most give two different rates.

    Asserted on real rows with the gap's magnitude, not only its existence -- `002023.SZ`'s
    consolidated growth is 2.09 times its attributable one -- because "the two are not equal" is
    the assertion whose weakness this repository has recorded five times over.
    """
    rates: dict[str, tuple[float, float]] = {}
    for (
        security,
        base,
        newest,
        attr_base,
        attr_now,
        consolidated_base,
        consolidated_now,
    ) in TWO_PROFIT_COLUMNS:
        where = f"{security} {base}..{newest}"
        assert attr_base > 0.0 and consolidated_base > 0.0, where
        rates[security] = (attr_now / attr_base - 1.0, consolidated_now / consolidated_base - 1.0)
        assert rates[security][0] != pytest.approx(rates[security][1]), where

    assert rates["002023.SZ"][0] == pytest.approx(0.35312, abs=1e-5)
    assert rates["002023.SZ"][1] == pytest.approx(0.73650, abs=1e-5)
    assert rates["002023.SZ"][1] / rates["002023.SZ"][0] == pytest.approx(2.0857, abs=1e-4)
    assert rates["000931.SZ"][0] == pytest.approx(0.06647, abs=1e-5)
    assert rates["000931.SZ"][1] == pytest.approx(0.02453, abs=1e-5)
    assert rates["002314.SZ"][0] == pytest.approx(-5.63756, abs=1e-5)
    assert rates["002314.SZ"][1] == pytest.approx(-1.28139, abs=1e-5)

    assert NET_PROFIT_YOY.columns_of(INCOME_DATASET) == ("n_income_attr_p",)
    assert "n_income" not in NET_PROFIT_YOY.columns_of(INCOME_DATASET)


# --- the base that is not strictly positive -------------------------------------------------------

# (ts_code, 2023H1 n_income_attr_p, 2024H1 n_income_attr_p), captured 2026-08-13 by the same probe.
LOSS_BASE_ROWS: Final[tuple[tuple[str, float, float], ...]] = (
    ("002714.SZ", -2_779_217_657.24, 829_288_208.44),
    ("000506.SZ", -81_618_163.07, -54_931_580.75),
    ("002921.SZ", -1_746_813.60, -18_805_440.07),
)
"""Three real securities whose year-earlier profit is a loss, ordered **best outcome first**.

`002714.SZ` swung from a 2.78bn loss to an 829m profit -- a 3.61bn improvement. `000506.SZ`
narrowed its loss by a third. `002921.SZ` deepened its loss by a factor of 10.8. That is the whole
ranking a growth factor is supposed to produce, and the next test is what the refused arithmetic
produces instead.
"""

REFUSED_LOSS_BASE_RATES: Final[tuple[float, ...]] = (
    -1.2983890830859048,
    -0.32696867114140005,
    9.76556769995379,
)
"""`newest / base - 1` for `LOSS_BASE_ROWS`, in the same order: the numbers this family refuses.

Recorded as literals rather than recomputed in the assertion, because the finding is their
**order**: the security that recovered scores lowest, the one that lost ten times as much scores
+976%, and the whole ranking is the exact reverse of the one above.
"""


def test_a_base_that_is_not_strictly_positive_is_undefined_rather_than_a_sign_inverted_rate() -> (
    None
):
    """The family's hardest judgement, with the refused answers computed on real securities.

    The derivative of `num / base - 1` in `num` is `1 / base`, so at a negative base the ratio is
    monotonically *decreasing* in this year's outcome. That is not a rounding concern: on three
    real securities from `V2-P3-011`'s live probe the refused arithmetic produces **exactly the
    reverse** of the ranking a growth factor exists to produce -- an issuer that swung from a
    2.78bn loss to an 829m profit scores -129.8%, one that narrowed its loss scores -32.7%, and
    one that deepened its loss by a factor of 10.8 scores **+976.6%** and would sit at the top of
    a `higher_is_better` cross section. So `undefined_value` is the answer and not a number.

    The refused values are asserted by magnitude and by order, because the order is the finding;
    a test asserting only that the evaluator returns `None` would pass against a rule chosen for
    any reason at all.

    A zero base is refused with it, as an ordinary division; `_market_capitalisation` guards a
    non-positive denominator in this module on the same argument stated in the same shape.
    """
    refused = [newest / base - 1.0 for _, base, newest in LOSS_BASE_ROWS]
    swings = [newest - base for _, base, newest in LOSS_BASE_ROWS]

    assert refused == [pytest.approx(value) for value in REFUSED_LOSS_BASE_RATES]
    assert swings == sorted(swings, reverse=True), "the rows are ordered best outcome first"
    assert refused == sorted(refused), "and the refused rate ranks them exactly backwards"
    assert refused[2] > 0.0 > refused[0]

    for base in (0.0, -100.0, -1e-9):
        window = _window(
            last=11,
            count=YEAR_ON_YEAR_PERIODS,
            series=((TOTAL_REVENUE_COLUMN, (base, 1.0, 2.0, 3.0, 50.0)),),
        )
        assert _revenue_yoy(window) is None
        assert (
            _year_on_year(
                window, dataset=INCOME_DATASET, column=TOTAL_REVENUE_COLUMN, index=NEWEST_PERIOD
            )
            is None
        )

    # A positive base with a negative result is the opposite case and stays `computed`: a shrinking
    # issuer is a real answer and the ratio is monotone through it.
    shrinking = _window(
        last=11,
        count=YEAR_ON_YEAR_PERIODS,
        series=((TOTAL_REVENUE_COLUMN, (100.0, 1.0, 2.0, 3.0, 40.0)),),
    )
    assert _revenue_yoy(shrinking) == pytest.approx(-0.6)


def test_the_acceleration_refuses_when_either_of_its_two_bases_is_not_positive() -> None:
    """The guard compounding, which is why the acceleration is on revenue and not on profit.

    This factor needs `window[-5]` and `window[-9]` both strictly positive where a plain rate
    needs one, so it is `undefined_value` for a security that lost money in *either* year-earlier
    period. Both halves are driven, because a guard written on one index alone would pass whichever
    of the two a single fixture happened to exercise.
    """
    positive = tuple(float(index + 1) * 10.0 for index in range(9))
    for spoiled in (0, 4):
        cells = list(positive)
        cells[spoiled] = -1.0
        window = _window(
            last=11,
            count=YEAR_ON_YEAR_ACCELERATION_PERIODS,
            series=((TOTAL_REVENUE_COLUMN, tuple(cells)),),
        )
        assert _revenue_yoy_acceleration(window) is None

    whole = _window(
        last=11,
        count=YEAR_ON_YEAR_ACCELERATION_PERIODS,
        series=((TOTAL_REVENUE_COLUMN, positive),),
    )
    assert _revenue_yoy_acceleration(whole) is not None
