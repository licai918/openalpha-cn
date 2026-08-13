"""The growth family against real partitions on disk (`V2-P3-011`).

`tests/unit/test_factor_growth_family.py` measures the three evaluators as functions of a window.
This file measures what a function cannot answer, and every one of the four things it says is
something no factor shipped before this one could have said:

## A factor on the report-period axis **alone**, through the whole engine

`V2-P3-009`'s value family was the first on two axes at once; these three are the first on one
axis that is not the session one. `FactorDefinition` refuses a session reach for a factor whose
`required_fields` are all filings, so `lookback_sessions is None` here has a production reader for
the first time -- and `_classify` has to form an empty session window, bound nothing on that axis,
and still record a period pair on the stored observation.
`test_the_three_factors_compute_off_the_period_axis_alone_and_give_three_different_numbers` and
`test_the_period_window_survives_the_round_trip_with_no_session_window_beside_it` are where that
becomes a measurement.

## Two reaches inside one family, on one partition

| security | what it is for |
|---|---|
| `000001.SZ` | the computable case, all three factors |
| `600000.SH` | six filings: the two five-period rates compute, the acceleration cannot |
| `000063.SZ` | nine filings spanning **ten** quarters: the span bound, not the count |
| `603333.SH` | a year-earlier **loss**: `undefined_value` for `net_profit_yoy` alone |
| `000795.SZ` | a year-earlier **zero** top line: `undefined_value` for both revenue factors |
| `600519.SH` | a zero top line two years back: `undefined_value` for the acceleration alone |
| `300750.SZ` | a null cell in the window: `input_missing` |
| `002594.SZ` | newest filing is the **annual**, not Q1: `computed` over a different horizon |
| `601318.SH` | outside the universe |

`600000.SH` and `000063.SZ` are the pair the `V2-P3-004` review's lesson asks for at the wider
reach: the first is short on the count and the second is inside the count and outside the span.

## Three factors reading one dataset, two of them reading one column

That is the sharpest form of the shape in which a fixture stops discriminating, and it is sharper
here than for the value family: a growth rate divides a constant scale out, so the value family's
own partition -- `revenue` at a flat half of `total_revenue`, `n_income` at a flat 1.7 of
`n_income_attr_p` -- would give **the identical number** for a factor pointed at either neighbour.
So this partition moves every neighbouring column by a *changing* ratio, and the wrong-column
answers are asserted to be numbers this build does not produce.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from openalpha_cn.domain.factor import FactorDefinition
from openalpha_cn.domain.financial_statements import (
    INCOME_DATA_COLUMNS,
    INCOME_DATASET,
    statement_panel_columns,
)
from openalpha_cn.domain.panel_batch import (
    ColumnarPanelBatch,
    PanelColumn,
    TimelineColumns,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    NET_PROFIT_YOY,
    REVENUE_YOY,
    REVENUE_YOY_ACCELERATION,
    YEAR_ON_YEAR_ACCELERATION_PERIODS,
    YEAR_ON_YEAR_PERIODS,
    FactorPanel,
    compute_factor,
    load_factor_observations,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import financial_statement_requirement, write_panel_batch

SHANGHAI_OFFSET: Final[timedelta] = timedelta(hours=8)

AS_OF: Final[datetime] = datetime(2025, 5, 20, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai on 2025-05-20: a month after the newest filing."""

BUILT_AT: Final[datetime] = datetime(2025, 6, 1, 9, 0, tzinfo=UTC)
COMMIT: Final[str] = "b7e1c04"

STATEMENT_STALENESS: Final[timedelta] = timedelta(days=120)
"""A quarter, which is how often A-share issuers disclose; this corpus's widest gap between
`AS_OF` and a visible filing is 30 days, so the bound is real rather than sized to clear."""

PERIODS: Final[tuple[date, ...]] = tuple(
    date(year, month, day)
    for year in (2022, 2023, 2024, 2025)
    for month, day in ((3, 31), (6, 30), (9, 30), (12, 31))
    if not (year == 2025 and month > 3)
)
"""2022Q1 through 2025Q1: thirteen periods, enough for a nine-period window and a gap in it."""

ANNOUNCED_ON: Final[dict[date, date]] = {
    period: (
        date(period.year + 1, 4, 20)
        if period.month == 12
        else date(period.year, period.month + 1, 20 if period.month != 6 else 25)
    )
    for period in PERIODS
}
"""When each period was disclosed. An annual and the following Q1 land on **one day**, which is
what an A-share issuer routinely does and what the report-period axis exists to hold."""

STATEMENT_YEARS: Final[tuple[int, ...]] = (2022, 2023, 2024, 2025)

REVENUE_INCREMENTS: Final[tuple[float, ...]] = (
    101.0, 211.0, 307.0, 419.0,
    131.0, 233.0, 331.0, 439.0,
    151.0, 251.0, 353.0, 457.0,
    173.0,
)  # fmt: skip
"""One quarter's top line at a time, 2022Q1 through 2025Q1. No two alike and none zero.

The partition carries the *cumulative* figures these accumulate into, which is what a stored
A-share statement holds; every expectation is a ratio of two explicit sums of them.
"""

PROFIT_INCREMENTS: Final[tuple[float, ...]] = (
    11.0, 23.0, 37.0, 53.0,
    17.0, 29.0, 41.0, 59.0,
    19.0, 31.0, 43.0, 67.0,
    23.0,
)  # fmt: skip
"""The same for `n_income_attr_p`, deliberately **not** proportional to the revenue series, so
`revenue_yoy` and `net_profit_yoy` cannot come out equal on this partition."""

NEIGHBOUR_DRIFT: Final[float] = 0.04
"""How much the neighbouring columns' ratio to the read ones moves per period.

The whole reason it moves: a growth rate divides a *constant* scale out, so
`tests/integration/panel/test_value_family.py`'s flat `revenue = 0.5 * total_revenue` and
`n_income = 1.7 * n_income_attr_p` would give a factor pointed at either neighbour **the identical
number**. A drifting ratio is what makes the column choice separable on a partition at all.
"""

FULL: Final[str] = "000001.SZ"
SHORT: Final[str] = "600000.SH"
GAP: Final[str] = "000063.SZ"
LOSS: Final[str] = "603333.SH"
ZERO_BASE: Final[str] = "000795.SZ"
ZERO_EARLIER_BASE: Final[str] = "600519.SH"
NULL_CELL: Final[str] = "300750.SZ"
ANNUAL_LATEST: Final[str] = "002594.SZ"
OUTSIDE: Final[str] = "601318.SH"

SUBJECTS: Final[tuple[str, ...]] = (
    FULL,
    SHORT,
    GAP,
    LOSS,
    ZERO_BASE,
    ZERO_EARLIER_BASE,
    NULL_CELL,
    ANNUAL_LATEST,
    OUTSIDE,
)
UNIVERSE: Final[frozenset[str]] = frozenset(SUBJECTS) - {OUTSIDE}

NEWEST: Final[date] = date(2025, 3, 31)
YEAR_EARLIER: Final[date] = date(2024, 3, 31)
TWO_YEARS_EARLIER: Final[date] = date(2023, 3, 31)

LOSS_YEAR_EARLIER_PROFIT: Final[float] = -151.0
"""`603333.SH`'s 2024Q1 `n_income_attr_p`. Negative, so `net_profit_yoy`'s base is a loss and the
arithmetic the family refuses would answer `23 / -151 - 1 = -1.152`, which ranks a recovered
issuer below every shrinking one."""


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=UTC) - SHANGHAI_OFFSET


def _cumulative(increments: tuple[float, ...], period: date) -> float:
    """What a stored A-share statement carries at `period`: the fiscal year to date."""
    index = PERIODS.index(period)
    start = (index // 4) * 4
    return math.fsum(increments[start : index + 1])


def _periods_filed(subject: str) -> tuple[date, ...]:
    """Which report periods each security filed an `income` statement for."""
    if subject == SHORT:
        # Six filings: the two five-period rates compute, the nine-period one cannot.
        return PERIODS[-6:]
    if subject == GAP:
        # Nine filings whose newest nine span **ten** quarters: 2023Q1 is missing from the middle.
        return tuple(period for period in PERIODS[-10:] if period != TWO_YEARS_EARLIER)
    if subject == ANNUAL_LATEST:
        # Has not filed 2025Q1 yet, so its newest knowable period is the 2024 annual.
        return tuple(period for period in PERIODS if period != NEWEST)
    return PERIODS


def _scale(subject: str, period: date) -> float:
    if subject == LOSS and period == YEAR_EARLIER:
        return LOSS_YEAR_EARLIER_PROFIT / _cumulative(PROFIT_INCREMENTS, period)
    return 1.0


def _income_values(subject: str, period: date) -> dict[str, float | None]:
    """One `income` row, with a **drifting** ratio in every column this family could have read.

    `revenue` and `n_income` are the two neighbours a growth factor could be pointed at, and a
    constant multiple of either would give the identical growth rate -- so both move by
    `NEIGHBOUR_DRIFT` per period against the column actually read. The remaining six carry
    distinct constants: they are not read here and a second moving series would be noise.
    """
    index = PERIODS.index(period)
    revenue = _cumulative(REVENUE_INCREMENTS, period)
    profit = _cumulative(PROFIT_INCREMENTS, period) * _scale(subject, period)
    if subject == ZERO_BASE and period == YEAR_EARLIER:
        revenue = 0.0
    if subject == ZERO_EARLIER_BASE and period == TWO_YEARS_EARLIER:
        revenue = 0.0
    drift = 1.0 + NEIGHBOUR_DRIFT * index
    return {
        "total_revenue": revenue,
        "revenue": revenue * 0.5 * drift,
        "oper_cost": 3.0,
        "operate_profit": 5.0,
        "total_profit": 7.0,
        "income_tax": 11.0,
        "n_income": profit * 1.7 * drift,
        "n_income_attr_p": None if (subject == NULL_CELL and period == NEWEST) else profit,
        "basic_eps": 0.13,
        "ebit": 17.0,
    }


def _statement_batch(
    rows: tuple[tuple[str, date, date, dict[str, float | None]], ...],
) -> ColumnarPanelBatch:
    """`(subject, period, announcement, values)` through `income`'s own projection and clock."""
    announced = tuple(_midnight(item[2]) for item in rows)
    columns = (
        PanelColumn("report_period", "string", tuple(item[1].isoformat() for item in rows)),
        PanelColumn("ann_date", "string", tuple(item[2].isoformat() for item in rows)),
        PanelColumn("f_ann_date", "string", tuple(item[2].isoformat() for item in rows)),
        PanelColumn("update_flag", "string", tuple("1" for _ in rows)),
        *(
            PanelColumn(name, "float", tuple(item[3].get(name) for item in rows))
            for name in INCOME_DATA_COLUMNS
        ),
    )
    assert tuple(column.name for column in columns) == statement_panel_columns(INCOME_DATASET)
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=INCOME_DATASET,
        kind=INCOME_DATASET,
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


@pytest.fixture
def store(tmp_path: Path) -> PanelStore:
    built = PanelStore(tmp_path / "panel")
    rows = tuple(
        (subject, period, ANNOUNCED_ON[period], _income_values(subject, period))
        for subject in SUBJECTS
        for period in _periods_filed(subject)
    )
    for year in STATEMENT_YEARS:
        yearly = tuple(item for item in rows if item[2].year == year)
        if yearly:
            write_panel_batch(built, _statement_batch(yearly), year=year)
    return built


def _compute(store: PanelStore, definition: FactorDefinition, **overrides: Any) -> FactorPanel:
    settings: dict[str, Any] = {
        "as_of": AS_OF,
        "subjects": SUBJECTS,
        "universe": UNIVERSE,
        "requirements": {
            INCOME_DATASET: financial_statement_requirement(
                dataset=INCOME_DATASET,
                years=STATEMENT_YEARS,
                as_of=AS_OF,
                max_staleness=STATEMENT_STALENESS,
            )
        },
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


def _growth(increments: tuple[float, ...], *, newest: date, base: date) -> float:
    """The fixture's own expectation: two cumulative figures out of the increment table."""
    return _cumulative(increments, newest) / _cumulative(increments, base) - 1.0


# --- the three values, off one axis ---------------------------------------------------------------


def test_the_three_factors_compute_off_the_period_axis_alone_and_give_three_different_numbers(
    store: PanelStore,
) -> None:
    """The whole family through the real engine, each value pinned as a number.

    Every expectation is derived from the increment tables the partition was written from, so
    these are restatements of the fixture rather than numbers copied out of a run. The three are
    asserted pairwise distinct because a family of three reading one dataset -- two of them
    reading one *column* -- is precisely where the `V2-P3-004` review's finding recurs.

    The wrong-column answers are asserted absent, and on this partition that assertion has content
    it would not have on the value family's: `revenue` and `n_income` move against the columns
    actually read by `NEIGHBOUR_DRIFT` per period, because a **constant** multiple divides out of a
    growth rate entirely and would leave a mis-wired factor answering the right number.
    """
    answers = {
        definition.key: _value(_compute(store, definition), FULL)
        for definition in (REVENUE_YOY, NET_PROFIT_YOY, REVENUE_YOY_ACCELERATION)
    }
    revenue_now = _growth(REVENUE_INCREMENTS, newest=NEWEST, base=YEAR_EARLIER)
    revenue_then = _growth(REVENUE_INCREMENTS, newest=YEAR_EARLIER, base=TWO_YEARS_EARLIER)

    assert answers["revenue_yoy"] == pytest.approx(revenue_now)
    assert answers["net_profit_yoy"] == pytest.approx(
        _growth(PROFIT_INCREMENTS, newest=NEWEST, base=YEAR_EARLIER)
    )
    assert answers["revenue_yoy_acceleration"] == pytest.approx(revenue_now - revenue_then)
    assert len({round(value, 12) for value in answers.values()}) == 3

    # The acceleration is not its own first term, which a fixture with flat earlier growth would
    # have hidden, and it is not the wrong-sign difference either.
    assert answers["revenue_yoy_acceleration"] != pytest.approx(revenue_now)
    assert answers["revenue_yoy_acceleration"] != pytest.approx(revenue_then - revenue_now)

    # The two neighbouring columns a growth factor could be pointed at.
    wrong = {
        "revenue": _income_values(FULL, NEWEST)["revenue"],
        "n_income": _income_values(FULL, NEWEST)["n_income"],
    }
    base = {
        "revenue": _income_values(FULL, YEAR_EARLIER)["revenue"],
        "n_income": _income_values(FULL, YEAR_EARLIER)["n_income"],
    }
    for column, key in (("revenue", "revenue_yoy"), ("n_income", "net_profit_yoy")):
        numerator, denominator = wrong[column], base[column]
        assert numerator is not None and denominator is not None
        assert answers[key] != pytest.approx(numerator / denominator - 1.0)


def test_the_year_on_year_is_not_the_latest_cumulative_over_the_one_before_it(
    store: PanelStore,
) -> None:
    """The whole content of the year-on-year choice, on disk.

    Three near misses are separated: the sequential growth (`window[-1]` over `window[-2]`, which
    on cumulative figures compares three months against twelve and is not a growth at all), the
    growth over the latest annual, and the ratio itself rather than the ratio less one.
    """
    answer = _value(_compute(store, REVENUE_YOY), FULL)
    sequential = _cumulative(REVENUE_INCREMENTS, NEWEST) / _cumulative(
        REVENUE_INCREMENTS, date(2024, 12, 31)
    )

    assert answer == pytest.approx(_growth(REVENUE_INCREMENTS, newest=NEWEST, base=YEAR_EARLIER))
    assert answer != pytest.approx(sequential - 1.0)
    assert answer != pytest.approx(
        _cumulative(REVENUE_INCREMENTS, NEWEST) / _cumulative(REVENUE_INCREMENTS, YEAR_EARLIER)
    )


# --- the coverage codes ---------------------------------------------------------------------------


def test_the_nine_period_reach_and_the_five_period_reach_answer_differently_for_the_same_security(
    store: PanelStore,
) -> None:
    """The coverage difference inside the family, which is the cost of the acceleration's window.

    `600000.SH` has filed six times. That clears `YEAR_ON_YEAR_PERIODS` and falls short of
    `YEAR_ON_YEAR_ACCELERATION_PERIODS`, so the two plain rates compute off the same partition the
    acceleration reports `insufficient_history` for. The same security, the same partition, the
    same `as_of` -- so the reach is separable from everything else about the build, which is what
    the value family's own BP-against-EP test does one reach lower.
    """
    filed = _periods_filed(SHORT)
    assert YEAR_ON_YEAR_PERIODS <= len(filed) < YEAR_ON_YEAR_ACCELERATION_PERIODS

    assert _value(_compute(store, REVENUE_YOY), SHORT) == pytest.approx(
        _growth(REVENUE_INCREMENTS, newest=NEWEST, base=YEAR_EARLIER)
    )
    assert _value(_compute(store, NET_PROFIT_YOY), SHORT) == pytest.approx(
        _growth(PROFIT_INCREMENTS, newest=NEWEST, base=YEAR_EARLIER)
    )

    acceleration = _compute(store, REVENUE_YOY_ACCELERATION)
    assert _coverage(acceleration)[SHORT] == "insufficient_history"
    assert _coverage(acceleration)[FULL] == "computed"
    short_row = next(item for item in acceleration.observations if item.subject == SHORT)
    assert short_row.input_period_first is None
    assert short_row.input_period_last is None


def test_a_missed_filing_is_the_span_bound_and_not_the_count(store: PanelStore) -> None:
    """`max_window_periods` separated from `lookback_periods` at the wider reach.

    `000063.SZ` has filed exactly nine times, so the count clears; the nine span **ten** fiscal
    quarters, because 2023Q1 is missing from the middle. That is the case
    `_year_on_year`'s docstring says produces a number rather than a refusal when the guarantee is
    removed -- `tests/unit/test_factor_growth_family.py::
    test_a_gapped_window_handed_to_the_evaluator_returns_a_wrong_number`
    pins that number -- and here the engine refuses the window before an evaluator sees it.

    Asserted with the count check beside it, so the refusal cannot be satisfied by the shortfall
    branch: `600000.SH` above is short on the count at an unbroken span and this security is whole
    on the count at a broken span, and both answer `insufficient_history`.
    """
    filed = _periods_filed(GAP)
    assert len(filed) == YEAR_ON_YEAR_ACCELERATION_PERIODS
    assert TWO_YEARS_EARLIER not in filed

    coverage = _coverage(_compute(store, REVENUE_YOY_ACCELERATION))
    row = next(
        item
        for item in _compute(store, REVENUE_YOY_ACCELERATION).observations
        if item.subject == GAP
    )

    assert coverage[GAP] == "insufficient_history"
    assert coverage[FULL] == "computed"
    # The span overrun records the window it was refused for, where a count shortfall records none.
    assert row.input_period_first == filed[0]
    assert row.input_period_last == filed[-1]


def test_a_loss_year_earlier_is_undefined_value_rather_than_a_number_that_ranks_backwards(
    store: PanelStore,
) -> None:
    """The family's hardest judgement, provoked end to end on a partition the writers accept.

    `603333.SH`'s 2024Q1 `n_income_attr_p` is negative, so `net_profit_yoy`'s base is a loss. The
    refused arithmetic has an answer -- `23 / -151 - 1 = -1.152` -- and it is monotonically
    backwards, which is why the engine reports `undefined_value` instead. The two revenue factors
    compute for the same security in the same build, so this is an answer about a column rather
    than about a security, and `000001.SZ` computes on all three, which separates a fault in the
    fixture from the code under test.
    """
    refused = _cumulative(PROFIT_INCREMENTS, NEWEST) / LOSS_YEAR_EARLIER_PROFIT - 1.0
    assert refused == pytest.approx(-1.152, abs=1e-3)
    assert refused < 0.0, "the recovered issuer would have scored below every shrinking one"

    assert _coverage(_compute(store, NET_PROFIT_YOY))[LOSS] == "undefined_value"
    assert _coverage(_compute(store, NET_PROFIT_YOY))[FULL] == "computed"
    assert _coverage(_compute(store, REVENUE_YOY))[LOSS] == "computed"
    assert _coverage(_compute(store, REVENUE_YOY_ACCELERATION))[LOSS] == "computed"


def test_a_zero_year_earlier_base_is_undefined_value_for_every_factor_that_reads_it(
    store: PanelStore,
) -> None:
    """The ordinary zero division, which reaches all three factors because they share the base.

    `000795.SZ`'s 2024Q1 `total_revenue` is a stored `0.0` -- `domain/financial_statements.py`
    requires a projected cell to be finite and not to be positive, so this partition is one this
    repository's own reader accepts. It is `revenue_yoy`'s base and the acceleration's *middle*
    term, so both refuse; `net_profit_yoy` reads a different column and computes.
    """
    assert _income_values(ZERO_BASE, YEAR_EARLIER)["total_revenue"] == 0.0

    assert _coverage(_compute(store, REVENUE_YOY))[ZERO_BASE] == "undefined_value"
    assert _coverage(_compute(store, REVENUE_YOY_ACCELERATION))[ZERO_BASE] == "undefined_value"
    assert _coverage(_compute(store, NET_PROFIT_YOY))[ZERO_BASE] == "computed"
    assert _coverage(_compute(store, REVENUE_YOY))[FULL] == "computed"


def test_the_acceleration_refuses_a_base_only_it_reads_where_the_plain_rate_computes(
    store: PanelStore,
) -> None:
    """The acceleration's *second* base, which no other factor in this family reads.

    `600519.SH`'s 2023Q1 `total_revenue` is a stored `0.0`. That period is `window[-9]` of the
    nine-period window and is outside the five-period one entirely, so `revenue_yoy` computes off
    a whole window and the acceleration has no earlier rate to difference. Measured as a mutant:
    an acceleration that answered its recent rate when the earlier one is undefined -- which is
    the plausible mistake, since the recent rate is a real number -- leaves the rest of this file
    green, and this is the case that separates it.
    """
    assert _income_values(ZERO_EARLIER_BASE, TWO_YEARS_EARLIER)["total_revenue"] == 0.0
    assert _income_values(ZERO_EARLIER_BASE, YEAR_EARLIER)["total_revenue"] != 0.0

    plain = _compute(store, REVENUE_YOY)
    acceleration = _compute(store, REVENUE_YOY_ACCELERATION)

    assert _value(plain, ZERO_EARLIER_BASE) == pytest.approx(
        _growth(REVENUE_INCREMENTS, newest=NEWEST, base=YEAR_EARLIER)
    )
    assert _coverage(acceleration)[ZERO_EARLIER_BASE] == "undefined_value"
    assert _coverage(acceleration)[FULL] == "computed"


def test_a_null_cell_in_the_window_is_input_missing_for_that_factor_alone(
    store: PanelStore,
) -> None:
    """`input_missing` on one column, and the other two factors unaffected in the same corpus.

    `300750.SZ`'s newest `income` row carries a null in `n_income_attr_p` alone, so
    `net_profit_yoy`'s window cannot be completed and the remedy is a fetch, while both revenue
    factors read a whole series. That is the distinction `undefined_value` above is kept separate
    from: one is a missing row, the other is arithmetic with no answer.
    """
    assert _income_values(NULL_CELL, NEWEST)["n_income_attr_p"] is None

    assert _coverage(_compute(store, NET_PROFIT_YOY))[NULL_CELL] == "input_missing"
    assert _coverage(_compute(store, REVENUE_YOY))[NULL_CELL] == "computed"
    assert _coverage(_compute(store, REVENUE_YOY_ACCELERATION))[NULL_CELL] == "computed"


def test_two_securities_with_different_newest_filings_grow_over_different_spans(
    store: PanelStore,
) -> None:
    """The cost this family discloses, as a partition rather than as a sentence.

    `002594.SZ` has not filed 2025Q1, so its newest knowable period is the 2024 annual and its
    year-on-year is a **twelve-month** growth; `000001.SZ` has filed, so its is a **three-month**
    one. Both are `computed` and both are ranked in the same cross section, which is the
    heterogeneity a TTM-over-TTM construction would remove at a nine-period reach for the plain
    rate. Asserted on the values as well as on the window ends, so the two really are growths of
    different spans rather than two names for one number.
    """
    panel = _compute(store, REVENUE_YOY)
    rows = {item.subject: item for item in panel.observations}
    annual = date(2024, 12, 31)

    assert rows[FULL].coverage == rows[ANNUAL_LATEST].coverage == "computed"
    assert rows[FULL].input_period_last == NEWEST
    assert rows[ANNUAL_LATEST].input_period_last == annual
    assert _value(panel, FULL) == pytest.approx(
        _growth(REVENUE_INCREMENTS, newest=NEWEST, base=YEAR_EARLIER)
    )
    assert _value(panel, ANNUAL_LATEST) == pytest.approx(
        _growth(REVENUE_INCREMENTS, newest=annual, base=date(2023, 12, 31))
    )
    assert _value(panel, FULL) != pytest.approx(_value(panel, ANNUAL_LATEST))


def test_a_security_outside_the_universe_is_not_in_universe_on_every_factor(
    store: PanelStore,
) -> None:
    """The code that must not be confused with a data fault, on a security whose rows are all
    present: `601318.SH` has every filing and is simply not in the cross section the caller
    declared."""
    for definition in (REVENUE_YOY, NET_PROFIT_YOY, REVENUE_YOY_ACCELERATION):
        observation = next(
            item for item in _compute(store, definition).observations if item.subject == OUTSIDE
        )

        assert observation.coverage == "not_in_universe"
        assert observation.input_row_count == 0
        assert observation.input_period_first is None
        assert observation.input_session_first is None


# --- the round trip -------------------------------------------------------------------------------


def test_the_period_window_survives_the_round_trip_with_no_session_window_beside_it(
    store: PanelStore,
) -> None:
    """The stored observation of a factor on the report-period axis **alone**, read off Parquet.

    Every factor shipped before `V2-P3-011` was either session-only or on both axes, so a writer
    that filled the session pair from the period one -- or refused to store a row with an empty
    session window at all -- was invisible. Here the period pair is populated by value and the
    session pair is asserted null, which is the direction no existing partition covers.
    """
    write_factor_panels(store, [_compute(store, REVENUE_YOY_ACCELERATION)])
    observations = load_factor_observations(
        store, REVENUE_YOY_ACCELERATION, years=(AS_OF.year,), as_of=AS_OF
    )
    computed = next(item for item in observations if item.subject == FULL)
    revenue_now = _growth(REVENUE_INCREMENTS, newest=NEWEST, base=YEAR_EARLIER)
    revenue_then = _growth(REVENUE_INCREMENTS, newest=YEAR_EARLIER, base=TWO_YEARS_EARLIER)

    assert computed.coverage == "computed"
    assert computed.value == pytest.approx(revenue_now - revenue_then)
    assert computed.input_period_first == PERIODS[-YEAR_ON_YEAR_ACCELERATION_PERIODS]
    assert computed.input_period_last == NEWEST
    assert computed.input_session_first is None
    assert computed.input_session_last is None
    assert computed.input_row_count == YEAR_ON_YEAR_ACCELERATION_PERIODS
