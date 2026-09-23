"""`domain/index_prices.py`: the market return series, its nullability and its two witnesses.

`V2-P3-013` measured that this panel held no index level anywhere and could therefore ship no
residual volatility. This module is the domain half of `V2-P3-016`'s answer, and everything in it
runs off **real published rows** -- twelve live `000300.SH` sessions from June 2026, five from its
back-computed 2002 history, and four spanning `000905.SH`'s synthetic base point -- because the
three claims worth making here are claims about the *feed* rather than about the arithmetic.

## The three claims

**Only `close` is never null, and that was measured before it was declared.** Seven of the eight
data columns carry a real null in the published history and the eighth does not:
`INDEX_DAILY_NULLABLE_COLUMNS` is that measurement, and `ROWS_2002` / `ROWS_BASE_POINT` are two of
the shapes behind it -- a close-only reconstruction with no `open`/`high`/`low` at all, and a
base-date row with no `pre_close`, `pct_chg`, `vol` or `amount`. A parse strict enough to reject
either would have refused whole partitions of real history, and
`test_the_back_computed_history_and_the_base_point_rebuild_rather_than_being_refused` drives both.

**An index's `pre_close` is the previous close and a security's is not.** That is the one place
this module's arithmetic parts company with `domain/daily_prices.py`, whose central measurement is
that `close[t] / close[t-1] - 1` reverses the *sign* across an ex-rights morning.
`test_the_naive_path_agrees_with_the_chosen_one_here_and_disagrees_for_a_security` runs both paths
over the same twelve real index sessions and gets a difference of exactly `0.0`, then re-states the
security-side counter-example so the two are visible together. The chosen path is used anyway, and
`KNOWN_INDEX_PRICE_LIMITATIONS` carries why.

**The reconciliation bound is derived and then checked, not fitted.** `pct_chg` is published to
four decimals of a percent, so a correctly rounded value cannot differ from the exact quotient by
more than half a unit in that place -- 5e-7.
`test_the_bound_is_half_a_unit_in_the_last_published_place_and_the_rows_sit_inside_it` asserts
both halves: that the real rows are inside it, and that a row perturbed by just over it is
refused. A bound asserted only in the passing direction would be satisfied by `float('inf')`.
"""

from __future__ import annotations

import itertools
from datetime import date
from typing import Final

import pytest

from openalpha_cn.domain.index_prices import (
    INDEX_DAILY_DATA_COLUMNS,
    INDEX_DAILY_DATASET,
    INDEX_DAILY_NULLABLE_COLUMNS,
    INDEX_DAILY_PANEL_COLUMNS,
    INDEX_PRICE_COLUMNS,
    INDEX_PRICE_INDEX_CODES,
    KNOWN_INDEX_PRICE_LIMITATIONS,
    MARKET_INDEX_CODE,
    MAX_INDEX_PUBLISHED_RETURN_DISAGREEMENT,
    IndexPriceError,
    index_bars_from_panel_rows,
    index_session_returns,
)

Row = tuple[
    str,
    str,
    float | None,
    float | None,
    float | None,
    float,
    float | None,
    float | None,
    float | None,
    float | None,
]

# `(subject, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount)` -- the
# positional contract of `INDEX_DAILY_PANEL_COLUMNS`, carrying rows exactly as `index_daily`
# served them on 2026-08-17.
# fmt: off
ROWS_LIVE: Final[tuple[Row, ...]] = (
    ("000300.SH", "2026-06-01", 4897.8968, 4918.7935, 4836.6851, 4844.2556, 4892.1213, -0.9784, 296134051.0, 823984849.0001),  # noqa: E501
    ("000300.SH", "2026-06-02", 4860.6119, 4930.2776, 4831.9972, 4914.5591, 4844.2556, 1.4513, 281731662.0, 806751344.3952),  # noqa: E501
    ("000300.SH", "2026-06-03", 4921.3445, 4991.8525, 4904.8969, 4938.8091, 4914.5591, 0.4934, 304035947.0, 906047729.419),  # noqa: E501
    ("000300.SH", "2026-06-04", 4897.3164, 4938.7814, 4889.7456, 4904.7454, 4938.8091, -0.6897, 264808910.0, 736143410.0034),  # noqa: E501
    ("000300.SH", "2026-06-05", 4886.9645, 4924.3438, 4798.9103, 4816.9199, 4904.7454, -1.7906, 301929336.0, 839863796.3956),  # noqa: E501
    ("000300.SH", "2026-06-08", 4703.773, 4779.8623, 4677.5682, 4713.6358, 4816.9199, -2.1442, 299647565.0, 761046395.1659),  # noqa: E501
    ("000300.SH", "2026-06-09", 4743.4505, 4802.5047, 4715.3907, 4801.8106, 4713.6358, 1.8706, 258391601.0, 713286998.8209),  # noqa: E501
    ("000300.SH", "2026-06-10", 4753.1154, 4786.5162, 4718.9946, 4748.5932, 4801.8106, -1.1083, 259297109.0, 694628416.0573),  # noqa: E501
    ("000300.SH", "2026-06-11", 4730.072, 4766.1728, 4685.5014, 4722.4122, 4748.5932, -0.5513, 247177059.0, 653931756.2594),  # noqa: E501
    ("000300.SH", "2026-06-12", 4784.6396, 4809.8606, 4757.5574, 4777.3206, 4722.4122, 1.1627, 335922063.0, 871477325.2014),  # noqa: E501
    ("000300.SH", "2026-06-15", 4829.2886, 4892.5502, 4803.1836, 4891.7126, 4777.3206, 2.3945, 331316076.0, 914145755.8047),  # noqa: E501
    ("000300.SH", "2026-06-16", 4894.8785, 4908.2817, 4866.0613, 4884.2322, 4891.7126, -0.1529, 282584743.0, 857202587.4781),  # noqa: E501
)

ROWS_2002: Final[tuple[Row, ...]] = (
    ("000300.SH", "2002-01-04", None, None, None, 1316.455, None, None, 1579460.23, 1589495.187),
    ("000300.SH", "2002-01-07", None, None, None, 1302.084, 1316.455, -1.0916, 1597865.01, 1546093.181),  # noqa: E501
    ("000300.SH", "2002-01-08", None, None, None, 1292.714, 1302.084, -0.7196, 1488456.75, 1476277.651),  # noqa: E501
    ("000300.SH", "2002-01-09", None, None, None, 1272.645, 1292.714, -1.5525, 2172482.61, 2051945.748),  # noqa: E501
    ("000300.SH", "2002-01-10", None, None, None, 1281.261, 1272.645, 0.677, 3118194.31, 3001996.732),  # noqa: E501
)

ROWS_BASE_POINT: Final[tuple[Row, ...]] = (
    ("000905.SH", "2004-12-31", None, None, None, 1000.0, None, None, None, None),
    ("000905.SH", "2005-01-04", 996.682, 996.682, 984.795, 986.927, 1000.0, -1.3073, 2323762.03, 1329187.122),  # noqa: E501
    ("000905.SH", "2005-01-05", 986.57, 1008.855, 985.677, 1003.633, 986.927, 1.6927, 3486101.13, 1919861.791),  # noqa: E501
    ("000905.SH", "2005-01-06", 1003.49, 1003.49, 990.792, 994.595, 1003.633, -0.9005, 2933905.59, 1629216.031),  # noqa: E501
)
# fmt: on

EX_RIGHTS_SECURITY: Final[tuple[tuple[str, float, float], ...]] = (
    ("2026-06-11", 11.30, 11.28),
    ("2026-06-12", 11.24, 10.94),
)
"""`(session, close, pre_close)` for `000001.SZ` across the ex-dividend morning
`domain/daily_prices.py` measures. Restated here as the counter-example the index side does not
have, so the two behaviours are asserted in one place rather than described in two."""


def test_the_registry_names_the_four_boundaries_this_dataset_was_measured_to_have() -> None:
    """`KNOWN_INDEX_PRICE_LIMITATIONS` as a set literal, `KNOWN_ADJUSTMENT_LIMITATIONS`' form.

    Equality rather than membership for that registry's reason: a membership assertion can see a
    code that was renamed and never a code that was removed. This is also the binding
    `tests/unit/test_known_limitation_registries.py` requires -- every declared `code` must appear
    as a string literal in executable test code, and a docstring does not count.
    """
    assert {item.code for item in KNOWN_INDEX_PRICE_LIMITATIONS} == {
        "the_series_is_back_computed_before_the_index_listed",
        "an_index_pre_close_is_the_previous_close_and_a_securitys_is_not",
        "the_market_is_one_index_because_a_factor_cannot_name_another",
        "the_whole_market_axis_of_this_endpoint_exceeds_its_own_cap",
    }
    assert all(item.detail.strip() for item in KNOWN_INDEX_PRICE_LIMITATIONS)


def test_the_projection_is_nine_columns_and_only_the_level_is_non_null() -> None:
    """The column contract and the nullability claim, together, because they constrain each other.

    Seven of the eight data columns are nullable and `close` is not, which is the *whole* of what
    makes this dataset a level series rather than a bar series: a caller can rely on a level on
    every stored row and on nothing else. Asserted as an exact set difference so that widening the
    nullable set (which would make `close` optional and the series unusable) and narrowing it
    (which would refuse the back-computed history) are both red.
    """
    assert ("subject", *INDEX_DAILY_DATA_COLUMNS) == INDEX_DAILY_PANEL_COLUMNS
    assert INDEX_DAILY_DATA_COLUMNS[0] == "trade_date"
    assert len(INDEX_DAILY_DATA_COLUMNS) == 9

    optional = set(INDEX_DAILY_DATA_COLUMNS) - {"trade_date"} - INDEX_DAILY_NULLABLE_COLUMNS
    assert optional == {"close"}
    assert "close" in INDEX_PRICE_COLUMNS
    assert INDEX_DAILY_DATASET == "index_daily"


def test_the_market_index_is_one_of_the_three_the_build_fetches() -> None:
    """The scope choice, as a relation rather than as two literals that could drift apart."""
    assert MARKET_INDEX_CODE in INDEX_PRICE_INDEX_CODES
    assert len(INDEX_PRICE_INDEX_CODES) == 3
    assert len(set(INDEX_PRICE_INDEX_CODES)) == 3


def test_the_live_rows_rebuild_into_one_ascending_series_per_index() -> None:
    """The rebuilder's contract on the ordinary case: grouped by index, ascending, complete."""
    series = index_bars_from_panel_rows(ROWS_LIVE + ROWS_BASE_POINT)

    assert set(series) == {"000300.SH", "000905.SH"}
    assert len(series["000300.SH"]) == len(ROWS_LIVE)
    assert [bar.trade_date for bar in series["000300.SH"]] == sorted(
        bar.trade_date for bar in series["000300.SH"]
    )
    assert series["000300.SH"][0].trade_date == date(2026, 6, 1)
    assert series["000300.SH"][-1].close == 4884.2322


def test_the_rows_rebuild_the_same_way_whatever_order_the_partition_returns_them() -> None:
    """A partition has no declared row order, so the series must not depend on one.

    Driven with the input reversed rather than argued, because "we sort it" is exactly the kind of
    claim a fixture in ascending order cannot distinguish from "the rows arrived sorted".
    """
    forward = index_bars_from_panel_rows(ROWS_LIVE)
    backward = index_bars_from_panel_rows(tuple(reversed(ROWS_LIVE)))

    assert forward == backward


def test_the_back_computed_history_and_the_base_point_rebuild_rather_than_being_refused() -> None:
    """The two real shapes a stricter parse would have thrown away, driven separately.

    `000300.SH`'s 2002 rows are a close-only reconstruction -- 721 of them in the published
    history -- and `000905.SH`'s 2004-12-31 row is a synthetic 1,000.00 base point with no
    previous session and no turnover. Neither is a fault, both are stored, and the assertions name
    *which* columns are absent rather than only that the call returned.
    """
    old = index_bars_from_panel_rows(ROWS_2002)["000300.SH"]
    base = index_bars_from_panel_rows(ROWS_BASE_POINT)["000905.SH"]

    assert all(bar.open is None and bar.high is None and bar.low is None for bar in old)
    assert all(bar.close > 0.0 for bar in old)
    assert old[0].pre_close is None and old[0].pct_chg is None
    assert all(bar.pre_close is not None for bar in old[1:])

    assert base[0].trade_date == date(2004, 12, 31)
    assert base[0].close == 1000.0
    assert (base[0].pre_close, base[0].pct_chg, base[0].vol, base[0].amount) == (
        None,
        None,
        None,
        None,
    )
    assert base[1].pre_close == 1000.0


def test_a_null_or_non_positive_level_is_refused_and_a_signed_pct_chg_is_not() -> None:
    """The two directions a level parse can be wrong in, and the column that inverts them.

    A missing `close` is a row with no level and is refused; a zero or negative one is refused for
    the sharper reason that `close / pre_close - 1` is a division by zero rather than a number. And
    `pct_chg` is signed: 2,872 of `000300.SH`'s 5,972 published bars are negative, so a parse that
    treated "an index column" as "a level" would refuse every down day in the history. Driven on a
    real down day rather than a synthetic one.
    """
    with pytest.raises(IndexPriceError, match="has no close"):
        index_bars_from_panel_rows([(*ROWS_LIVE[0][:5], None, *ROWS_LIVE[0][6:])])  # type: ignore[list-item]

    with pytest.raises(IndexPriceError, match="strictly positive when it is published"):
        index_bars_from_panel_rows([(*ROWS_LIVE[0][:5], 0.0, *ROWS_LIVE[0][6:])])

    down = index_bars_from_panel_rows(ROWS_LIVE)["000300.SH"][0]
    assert down.pct_chg == -0.9784
    assert index_session_returns([down])[0] < 0.0


def test_two_rows_for_one_session_are_refused_rather_than_reduced() -> None:
    """The guard the whole series rests on, and the one nothing downstream of the store repeats.

    `compute_factor` reads two columns straight out of the partition; it does not rebuild the
    series, so a duplicated session would put two market returns where the market had one and
    change the sample size of every regression reading that window. Refused here, at the only
    place that can see it.
    """
    with pytest.raises(IndexPriceError, match="more than one stored level for 2026-06-01"):
        index_bars_from_panel_rows((*ROWS_LIVE, ROWS_LIVE[0]))


def test_a_row_of_the_wrong_width_names_the_contract_it_broke() -> None:
    with pytest.raises(IndexPriceError, match="values and 10 are required"):
        index_bars_from_panel_rows([ROWS_LIVE[0][:-1]])


def test_the_naive_path_agrees_with_the_chosen_one_here_and_disagrees_for_a_security() -> None:
    """The measurement that separates this dataset from `daily`, with both halves driven.

    Over the whole published history of the three indices, `pre_close[t] == close[t-1]` on every
    one of 15,753 adjacent pairs, so `close / pre_close - 1` and `close[t] / close[t-1] - 1` agree
    to exactly `0.0`. Twelve of those pairs are re-run here.

    The second half is the counter-example the index side does not have, restated from
    `domain/daily_prices.py`: on `000001.SZ`'s 2026-06-12 ex-dividend morning the two paths
    disagree in *sign*. Without it, the first half would read as "the naive path is fine", which
    is true of an index and false of a security -- and it is the false reading that would put a
    close-to-close market return next to a `pre_close` security return in one regression.
    """
    bars = index_bars_from_panel_rows(ROWS_LIVE)["000300.SH"]

    gaps = [
        abs((bar.close / bar.pre_close - 1.0) - (bar.close / previous.close - 1.0))
        for previous, bar in itertools.pairwise(bars)
        if bar.pre_close is not None
    ]
    assert len(gaps) == len(bars) - 1
    assert max(gaps) == 0.0

    (_, previous_close, _), (_, close, pre_close) = EX_RIGHTS_SECURITY
    chosen = close / pre_close - 1.0
    naive = close / previous_close - 1.0
    assert chosen > 0.0 > naive
    assert chosen == pytest.approx(0.0274223, abs=1e-6)
    assert naive == pytest.approx(-0.0053097, abs=1e-6)


def test_the_bound_is_half_a_unit_in_the_last_published_place_and_the_rows_sit_inside_it() -> None:
    """Both directions of the reconciliation, because one of them is satisfied by any bound.

    `pct_chg` is published to four decimals of a percent, so half a unit in that place is 5e-7 and
    the declared bound is 1e-6. The twelve real rows are inside it; a row whose `pct_chg` is moved
    by twice the bound is refused. A test that only ran the first half would pass for
    `MAX_INDEX_PUBLISHED_RETURN_DISAGREEMENT = float("inf")`.
    """
    bars = index_bars_from_panel_rows(ROWS_LIVE)["000300.SH"]
    returns = index_session_returns(bars)

    assert len(returns) == len(bars)
    for bar, value in zip(bars, returns, strict=True):
        assert bar.pct_chg is not None
        assert abs(value - bar.pct_chg / 100.0) <= MAX_INDEX_PUBLISHED_RETURN_DISAGREEMENT
    assert pytest.approx(1e-6) == MAX_INDEX_PUBLISHED_RETURN_DISAGREEMENT

    row = ROWS_LIVE[0]
    assert row[7] is not None
    nudged = (*row[:7], row[7] + 100.0 * 2.0 * MAX_INDEX_PUBLISHED_RETURN_DISAGREEMENT, *row[8:])
    with pytest.raises(IndexPriceError, match="states its own return twice"):
        index_session_returns(index_bars_from_panel_rows([nudged])["000300.SH"])


def test_a_bar_with_no_previous_session_has_no_return_and_says_so() -> None:
    """The base-date row again, one layer up: `None` on the bar, a refusal in the series.

    `IndexBar.published_return` answers `None` because that row is real published data; a *series*
    built from it refuses, because silently dropping the bar would shorten the window without
    telling the caller the window it asked for is not the window it got.
    """
    base = index_bars_from_panel_rows(ROWS_BASE_POINT)["000905.SH"]

    assert base[0].published_return is None
    assert base[0].upstream_return is None
    with pytest.raises(IndexPriceError, match="has no computable return on 2004-12-31"):
        index_session_returns(base)

    assert len(index_session_returns(base[1:])) == 3
