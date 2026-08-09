"""The daily price/valuation contract (`V2-P1-007`), against real rows.

Every number below came from a live probe of the real Tushare endpoints on 2026-08-08/09 and
is inlined here as a fixture; nothing in this file touches the network.

The centrepiece is `000001.SZ` across its 2026-06-12 ex-dividend date, where the three ways
of computing a session return separate:

    20260611  close=11.30  pre_close=11.32  pct_chg=-0.1767   adj_factor=134.5794
    20260612  close=11.24  pre_close=10.94  pct_chg= 2.7422   adj_factor=139.008

`pre_close` is **not** the previous session's close on that day (10.94 against 11.30), because
Tushare publishes it already restated for the corporate action. So:

    close/pre_close - 1                       = +2.742230%   correct
    (close*f)/(prev_close*f_prev) - 1         = +2.742251%   correct
    close/prev_close - 1                      = -0.530973%   WRONG, and the sign is wrong too

The two correct paths agree to 2.1e-7, which is the residue of `pre_close` being published to
two decimals: the exact restated previous close is 10.940003.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from openalpha_cn.domain.adjustment import (
    FactorObservation,
    build_adjustment_history,
)
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_BASIC_NULLABLE_COLUMNS,
    DAILY_BASIC_PANEL_COLUMNS,
    DAILY_DATASET,
    DAILY_PANEL_COLUMNS,
    KNOWN_PRICE_LIMITATIONS,
    MAX_PRE_CLOSE_DISAGREEMENT,
    DailyBar,
    PriceDataError,
    close_disagreements,
    close_index,
    daily_bars_from_panel_rows,
    daily_valuations_from_panel_rows,
    priced_cross_section,
    session_returns,
)
from openalpha_cn.domain.stock_universe import SecurityLifecycle, build_stock_universe
from openalpha_cn.domain.trading_calendar import CalendarDay, build_trading_calendar

PING_AN = "000001.SZ"
MAOTAI = "600519.SH"

# ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount.
# Kept unformatted so each row reads as one session rather than as ten lines.
# fmt: off
BARS: tuple[tuple[Any, ...], ...] = (
    (PING_AN, "2026-06-10", 11.07, 11.35, 11.07, 11.32, 11.13, 1.7071, 1543176.39,
     1738056.71203),
    (PING_AN, "2026-06-11", 11.32, 11.39, 11.25, 11.3, 11.32, -0.1767, 1156222.22,
     1308133.97213),
    (PING_AN, "2026-06-12", 11.0, 11.25, 10.88, 11.24, 10.94, 2.7422, 2032355.46,
     2263042.93057),
    (PING_AN, "2026-06-15", 11.21, 11.21, 10.98, 11.06, 11.24, -1.6014, 1541304.95,
     1711561.28657),
    (MAOTAI, "2026-06-10", 1252.08, 1282.0, 1250.21, 1275.88, 1256.0, 1.5828, 39244.14,
     4991686.419),
    (MAOTAI, "2026-06-11", 1272.12, 1282.88, 1266.91, 1279.0, 1275.88, 0.2445, 25351.98,
     3230008.22),
    (MAOTAI, "2026-06-12", 1271.18, 1295.0, 1265.01, 1291.91, 1279.0, 1.0094, 50494.78,
     6477910.214),
    (MAOTAI, "2026-06-15", 1292.7, 1292.7, 1270.1, 1271.1, 1291.91, -1.6108, 41585.56,
     5303656.129),
)
# fmt: on

FACTORS: dict[str, tuple[tuple[str, float], ...]] = {
    PING_AN: (
        ("2026-06-10", 134.5794),
        ("2026-06-11", 134.5794),
        ("2026-06-12", 139.008),
        ("2026-06-15", 139.008),
    ),
    MAOTAI: (
        ("2026-06-10", 8.4464),
        ("2026-06-11", 8.4464),
        ("2026-06-12", 8.4464),
        ("2026-06-15", 8.4464),
    ),
}

# ts_code, trade_date, close, turnover_rate, turnover_rate_f, volume_ratio, pe, pe_ttm, pb,
# ps, ps_ttm, dv_ratio, dv_ttm, total_share, float_share, free_share, total_mv, circ_mv
# fmt: off
VALUATIONS: tuple[tuple[Any, ...], ...] = (
    (PING_AN, "2026-06-12", 11.24, 1.0473, 2.4905, 1.71, 5.1163, 5.0655, 0.47, 1.6595,
     1.6399, 5.3204, 5.3026, 1940591.8198, 1940560.0653, 816048.1215, 21812252.0568,
     21811895.1868),
    (MAOTAI, "2026-06-12", 1291.91, 0.4039, 0.9334, 1.63, 19.6185, 19.5248, 5.9617, 9.5653,
     9.3815, 4.0045, 4.0045, 125008.1601, 125008.1601, 54094.8978, 161499291.9856,
     161499291.9856),
)
# fmt: on

JUNE_12 = date(2026, 6, 12)
JUNE_11 = date(2026, 6, 11)

PUBLISHED_RETURN_2026_06_12 = 0.02742230347349195
ADJUSTED_RETURN_2026_06_12 = 0.027422506154573423
UNADJUSTED_RETURN_2026_06_12 = -0.005309734513274433


def _bar_rows(*, ts_code: str | None = None, day: str | None = None) -> list[tuple[Any, ...]]:
    return [
        row
        for row in BARS
        if (ts_code is None or row[0] == ts_code) and (day is None or row[1] == day)
    ]


def _factor_history(ts_code: str):
    return build_adjustment_history(
        ts_code,
        [
            FactorObservation(ts_code=ts_code, observed_on=date.fromisoformat(day), factor=factor)
            for day, factor in FACTORS[ts_code]
        ],
    )


def _histories() -> dict[str, Any]:
    return {ts_code: _factor_history(ts_code) for ts_code in FACTORS}


def _calendar(
    open_days: tuple[str, ...] = ("2026-06-10", "2026-06-11", "2026-06-12", "2026-06-15"),
):
    first, last = date(2026, 6, 1), date(2026, 6, 30)
    opens = {date.fromisoformat(day) for day in open_days}
    return build_trading_calendar(
        "SZSE",
        [
            CalendarDay(
                calendar_date=first + timedelta(days=offset),
                is_trading=first + timedelta(days=offset) in opens,
            )
            for offset in range((last - first).days + 1)
        ],
    )


def _universe(codes: tuple[str, ...] = (PING_AN, MAOTAI)):
    return build_stock_universe(
        snapshot_date=date(2026, 8, 8),
        securities=[
            SecurityLifecycle(
                ts_code=ts_code,
                exchange="SZSE",
                listed_on=date(1991, 4, 3),
                delisted_on=None,
            )
            for ts_code in codes
        ],
        years_read=(1991,),
    )


# --- the panel column contract --------------------------------------------------------


def test_the_two_datasets_declare_the_columns_their_readers_positionally_unpack() -> None:
    assert DAILY_DATASET == "daily"
    assert DAILY_BASIC_DATASET == "daily_basic"
    assert DAILY_PANEL_COLUMNS == (
        "subject",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "pct_chg",
        "vol",
        "amount",
    )
    assert DAILY_BASIC_PANEL_COLUMNS == (
        "subject",
        "trade_date",
        "close",
        "turnover_rate",
        "turnover_rate_f",
        "volume_ratio",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "total_share",
        "float_share",
        "free_share",
        "total_mv",
        "circ_mv",
    )


def test_the_valuation_columns_that_are_allowed_to_be_null_are_the_measured_ones() -> None:
    """A null `pe` is ordinary -- 1,102 of the 5,338 names on 2024-06-28 had no positive
    earnings -- while a null `total_mv` never occurred on any of the five sessions probed.
    The distinction is stated so a reader knows which absences are data and which are faults.
    """
    assert (
        frozenset({"volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm"})
        == DAILY_BASIC_NULLABLE_COLUMNS
    )
    assert "close" not in DAILY_BASIC_NULLABLE_COLUMNS
    assert "total_mv" not in DAILY_BASIC_NULLABLE_COLUMNS
    assert "circ_mv" not in DAILY_BASIC_NULLABLE_COLUMNS
    assert "turnover_rate" not in DAILY_BASIC_NULLABLE_COLUMNS


# --- reading stored rows back ----------------------------------------------------------


def test_one_sessions_rows_rebuild_into_bars_keyed_by_security() -> None:
    bars = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))

    assert sorted(bars) == [PING_AN, MAOTAI]
    ping_an = bars[PING_AN]
    assert ping_an.trade_date == JUNE_12
    assert ping_an.close == 11.24
    assert ping_an.pre_close == 10.94
    assert ping_an.high == 11.25
    assert ping_an.low == 10.88
    assert ping_an.open == 11.0
    assert ping_an.vol == 2032355.46
    assert ping_an.amount == 2263042.93057


def test_a_row_of_the_wrong_width_is_refused_by_name() -> None:
    with pytest.raises(PriceDataError, match=r"row 0 has 3 values, expected 10"):
        daily_bars_from_panel_rows([(PING_AN, "2026-06-12", 11.24)])


def test_two_bars_for_one_security_in_one_cross_section_are_refused() -> None:
    """A cross section is one row per security by definition; two is two merged sessions or
    a double-counted fetch, and picking one silently would answer with whichever came last."""
    doubled = _bar_rows(day="2026-06-12")[:1] * 2
    with pytest.raises(PriceDataError, match=r"000001\.SZ appears twice"):
        daily_bars_from_panel_rows(doubled)


def test_a_cross_section_that_mixes_two_sessions_is_refused() -> None:
    """`priced_cross_section` joins against one day's factors and one day's universe, so a
    mapping holding two dates would silently adjust one of them with the other's factor."""
    with pytest.raises(PriceDataError, match=r"carries more than one trade_date"):
        daily_bars_from_panel_rows(_bar_rows(ts_code=PING_AN))


@pytest.mark.parametrize("column", ["open", "high", "low", "close", "pre_close"])
@pytest.mark.parametrize("value", [None, 0.0, -1.0, float("inf"), float("nan"), "11.24", 11])
def test_a_price_that_cannot_be_a_price_is_refused_at_the_boundary(column: str, value: Any) -> None:
    """Five sessions spanning 2023..2026 (24,188 bars) carried no null and no non-positive
    price, so a null price is a fault rather than a sparse cell -- and it would otherwise ride
    through into a return as a `TypeError` several layers away."""
    position = DAILY_PANEL_COLUMNS.index(column)
    row = list(_bar_rows(day="2026-06-12")[0])
    row[position] = value
    with pytest.raises(PriceDataError, match=column):
        daily_bars_from_panel_rows([tuple(row)])


def test_valuation_rows_rebuild_and_keep_their_nullable_cells_null() -> None:
    valuations = daily_valuations_from_panel_rows(VALUATIONS)
    ping_an = valuations[PING_AN]
    assert ping_an.close == 11.24
    assert ping_an.total_mv == 21812252.0568
    assert ping_an.circ_mv == 21811895.1868
    assert ping_an.turnover_rate == 1.0473
    assert ping_an.pe_ttm == 5.0655

    holed = list(VALUATIONS[0])
    holed[DAILY_BASIC_PANEL_COLUMNS.index("pe_ttm")] = None
    assert daily_valuations_from_panel_rows([tuple(holed)])[PING_AN].pe_ttm is None


def test_a_null_in_a_valuation_column_that_is_never_null_upstream_is_refused() -> None:
    holed = list(VALUATIONS[0])
    holed[DAILY_BASIC_PANEL_COLUMNS.index("total_mv")] = None
    with pytest.raises(PriceDataError, match="total_mv"):
        daily_valuations_from_panel_rows([tuple(holed)])


# --- the return contract ---------------------------------------------------------------


def test_the_two_correct_return_paths_agree_on_the_real_ex_dividend_day() -> None:
    """The cross-validation `V2-P1-007` owes `V2-P1-006`: it exercises `daily`'s `pre_close`
    and `adj_factor`'s step function against each other on the one day where a mistake in
    either is visible."""
    bar = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))[PING_AN]
    returns = session_returns(
        bar,
        previous_close=11.3,
        previous_day=JUNE_11,
        factors=_factor_history(PING_AN),
    )

    assert returns.published == pytest.approx(PUBLISHED_RETURN_2026_06_12, abs=1e-12)
    assert returns.adjusted == pytest.approx(ADJUSTED_RETURN_2026_06_12, abs=1e-12)
    assert abs(returns.published - returns.adjusted) < 1e-6
    # Tushare's own published percentage, to the four decimals it carries.
    assert round(returns.published * 100, 4) == 2.7422


def test_the_naive_close_to_close_return_has_the_wrong_sign_and_is_reported_as_such() -> None:
    """The failure this dataset exists to prevent, pinned with its measured value. `unadjusted`
    is carried on the record so the size of the error is visible, never so it is used."""
    bar = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))[PING_AN]
    returns = session_returns(
        bar, previous_close=11.3, previous_day=JUNE_11, factors=_factor_history(PING_AN)
    )

    assert returns.unadjusted == pytest.approx(UNADJUSTED_RETURN_2026_06_12, abs=1e-12)
    assert returns.unadjusted < 0 < returns.published
    assert round(returns.unadjusted * 100, 4) == -0.531


def test_an_ordinary_session_has_all_three_paths_agreeing() -> None:
    """The complement: on a day with no corporate action `pre_close` *is* the previous close,
    so the naive path is right -- which is exactly why its wrongness is invisible until an
    ex-rights day."""
    bar = daily_bars_from_panel_rows(_bar_rows(day="2026-06-15"))[PING_AN]
    returns = session_returns(
        bar, previous_close=11.24, previous_day=JUNE_12, factors=_factor_history(PING_AN)
    )
    assert returns.published == pytest.approx(returns.adjusted, abs=1e-9)
    assert returns.published == pytest.approx(returns.unadjusted, abs=1e-12)


def test_the_two_paths_disagreeing_beyond_one_published_tick_is_refused() -> None:
    """The cross-check is a live guard, not only an assertion in this file -- and what it
    catches is the failure `V2-P1-006`'s own review ended on.

    The factor history here is `000001.SZ`'s with the 2026-06-12 step *missing*: the 134.5794
    level carried forward across the ex-dividend date, which is exactly what a factor partition
    with a session hole answers (Task 29's `_refuse_missing_factor_sessions` measured the same
    134.5794-instead-of-139.008). The adjusted path then reproduces the naive answer, and the
    two paths disagree by 0.36 yuan of implied `pre_close`.

    The tolerance is one published tick (0.01): `pre_close` carries two decimals and
    `adj_factor` four, so the two paths cannot be compared more finely, and the largest
    disagreement measured across 16,246 real (previous session, session) pairs on three sessions
    was 0.0081.
    """
    bar = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))[PING_AN]
    stale = build_adjustment_history(
        PING_AN,
        [
            FactorObservation(ts_code=PING_AN, observed_on=JUNE_11, factor=134.5794),
            FactorObservation(ts_code=PING_AN, observed_on=JUNE_12, factor=134.5794),
        ],
    )
    with pytest.raises(PriceDataError, match=r"implied pre_close"):
        session_returns(bar, previous_close=11.3, previous_day=JUNE_11, factors=stale)


def test_the_disagreement_tolerance_is_one_published_tick() -> None:
    assert MAX_PRE_CLOSE_DISAGREEMENT == 0.01


def test_a_previous_day_that_is_not_before_the_bar_is_refused() -> None:
    bar = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))[PING_AN]
    with pytest.raises(PriceDataError, match="is not before"):
        session_returns(
            bar, previous_close=11.3, previous_day=JUNE_12, factors=_factor_history(PING_AN)
        )


def test_a_factor_history_for_another_security_is_refused_before_any_arithmetic() -> None:
    bar = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))[MAOTAI]
    with pytest.raises(PriceDataError, match=r"600519\.SH.*000001\.SZ"):
        session_returns(
            bar, previous_close=1279.0, previous_day=JUNE_11, factors=_factor_history(PING_AN)
        )


# --- the daily x adj_factor join -------------------------------------------------------


def test_the_cross_section_carries_every_listed_name_with_its_factor() -> None:
    section = priced_cross_section(
        daily_bars_from_panel_rows(_bar_rows(day="2026-06-12")),
        _histories(),
        universe=_universe(),
        calendar=_calendar(),
        day=JUNE_12,
    )

    assert [entry.ts_code for entry in section.priced] == [PING_AN, MAOTAI]
    assert section.priced[0].factor == 139.008
    assert section.priced[0].adjusted_close == pytest.approx(11.24 * 139.008)
    assert section.unpriced == ()
    assert section.unlisted_bars == ()


def test_a_listed_security_with_a_factor_and_no_bar_is_named_rather_than_dropped() -> None:
    """The measured shape: on 2024-06-28 `adj_factor` served 5,387 names and `daily` 5,338, and
    the 49-name difference splits into 26 securities that were listed and halted that session
    and 23 that had already been delisted. Only the first 26 reach this branch, because
    `universe.listed_on` does not return the other 23 -- and dropping them silently would make
    a halted security indistinguishable from one that was never listed.
    """
    bars = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))
    del bars[MAOTAI]
    section = priced_cross_section(
        bars, _histories(), universe=_universe(), calendar=_calendar(), day=JUNE_12
    )

    assert [entry.ts_code for entry in section.priced] == [PING_AN]
    assert section.unpriced == (MAOTAI,)
    assert section.listed_count == 2


def test_a_bar_for_a_security_the_registry_does_not_know_is_named_rather_than_silently_kept() -> (
    None
):
    """Measured, and not hypothetical: `300114.SZ` has a real 2024-06-28 bar and is absent from
    `stock_basic` under every `list_status` (`L`, `D`, `P`, and the bare request). Keeping it
    would put a name in the cross section that `StockUniverse.is_listed` refuses to answer for;
    dropping it silently would shrink the market by one with no signal."""
    bars = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))
    section = priced_cross_section(
        bars, _histories(), universe=_universe((PING_AN,)), calendar=_calendar(), day=JUNE_12
    )

    assert [entry.ts_code for entry in section.priced] == [PING_AN]
    assert section.unlisted_bars == (MAOTAI,)


def test_a_listed_security_with_no_adjustment_factor_blocks_the_whole_cross_section() -> None:
    """The same refusal `adjustment_factors_on` makes, reached through this join: a partial
    cross section is a plausible market snapshot with one name missing."""
    histories = _histories()
    del histories[MAOTAI]
    with pytest.raises(Exception, match="adjustment factor"):
        priced_cross_section(
            daily_bars_from_panel_rows(_bar_rows(day="2026-06-12")),
            histories,
            universe=_universe(),
            calendar=_calendar(),
            day=JUNE_12,
        )


def test_a_day_the_exchange_was_shut_has_no_cross_section() -> None:
    with pytest.raises(Exception, match="not an open session"):
        priced_cross_section(
            daily_bars_from_panel_rows(_bar_rows(day="2026-06-12")),
            _histories(),
            universe=_universe(),
            calendar=_calendar(("2026-06-10", "2026-06-11", "2026-06-15")),
            day=JUNE_12,
        )


def test_bars_dated_other_than_the_requested_day_are_refused() -> None:
    with pytest.raises(PriceDataError, match=r"2026-06-15.*2026-06-12"):
        priced_cross_section(
            daily_bars_from_panel_rows(_bar_rows(day="2026-06-15")),
            _histories(),
            universe=_universe(),
            calendar=_calendar(),
            day=JUNE_12,
        )


# --- daily against daily_basic ---------------------------------------------------------


def test_the_two_datasets_closes_agree_on_the_real_cross_section() -> None:
    """The free data-quality check: `daily_basic` republishes `close`, so a session's two
    fetches cross-check each other with no extra request. Measured across five sessions from
    2023 to 2026 (24,188 pairs): zero disagreements."""
    bars = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))
    assert (
        close_disagreements(
            close_index(bars), close_index(daily_valuations_from_panel_rows(VALUATIONS))
        )
        == ()
    )


def test_a_close_that_disagrees_between_the_two_datasets_is_reported_with_both_values() -> None:
    bars = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))
    drifted = list(VALUATIONS[0])
    drifted[DAILY_BASIC_PANEL_COLUMNS.index("close")] = 11.25
    (finding,) = close_disagreements(
        close_index(bars),
        close_index(daily_valuations_from_panel_rows([tuple(drifted), VALUATIONS[1]])),
    )
    assert finding.ts_code == PING_AN
    assert finding.bar_close == 11.24
    assert finding.valuation_close == 11.25


def test_a_valuation_with_no_bar_at_all_is_a_disagreement_in_the_impossible_direction() -> None:
    """`daily_basic` was a subset of `daily` on every session probed and never a superset, so
    a valuation for a security that had no bar is a contradiction rather than sparse data."""
    bars = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))
    del bars[MAOTAI]
    (finding,) = close_disagreements(
        close_index(bars), close_index(daily_valuations_from_panel_rows(VALUATIONS))
    )
    assert finding.ts_code == MAOTAI
    assert finding.bar_close is None
    assert finding.valuation_close == 1291.91


def test_a_bar_with_no_valuation_is_not_a_disagreement_because_it_is_the_measured_shape() -> None:
    """The asymmetry is measured rather than assumed. On 2020-03-02 `daily` served 3,843 rows
    and `daily_basic` 3,783; all 60 of the difference were Beijing-exchange (`.BJ`) codes, and
    the same one-directional gap appears on 2015-07-08 (22), 2018-01-02 (30) and 2022-04-25
    (56). Treating it as a fault would refuse every pre-2024 partition."""
    bars = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))
    assert (
        close_disagreements(
            close_index(bars), close_index(daily_valuations_from_panel_rows([VALUATIONS[0]]))
        )
        == ()
    )


# --- what is named rather than claimed --------------------------------------------------


def test_every_named_limitation_carries_a_code_and_a_detail() -> None:
    codes = {entry.code for entry in KNOWN_PRICE_LIMITATIONS}
    assert codes == {
        "pre_close_is_already_adjusted",
        "daily_basic_omits_the_beijing_board_before_2024",
        "a_priced_security_can_be_absent_from_the_registry",
        "a_partial_cross_section_is_invisible_without_suspend_d",
        "silent_truncation_at_the_response_cap",
    }
    for entry in KNOWN_PRICE_LIMITATIONS:
        assert entry.detail.strip()


def test_a_daily_bar_is_a_plain_value_and_reports_its_own_published_return() -> None:
    bar = DailyBar(
        ts_code=PING_AN,
        trade_date=JUNE_12,
        open=11.0,
        high=11.25,
        low=10.88,
        close=11.24,
        pre_close=10.94,
        pct_chg=2.7422,
        vol=2032355.46,
        amount=2263042.93057,
    )
    assert bar.published_return == pytest.approx(PUBLISHED_RETURN_2026_06_12, abs=1e-12)


# --- the guards on the valuation reader and the arithmetic inputs ------------------------


def test_a_valuation_row_of_the_wrong_width_is_refused_by_name() -> None:
    with pytest.raises(PriceDataError, match=r"row 0 has 3 values, expected 18"):
        daily_valuations_from_panel_rows([(PING_AN, "2026-06-12", 11.24)])


def test_two_valuations_for_one_security_in_one_cross_section_are_refused() -> None:
    with pytest.raises(PriceDataError, match=r"000001\.SZ appears twice"):
        daily_valuations_from_panel_rows([VALUATIONS[0], VALUATIONS[0]])


def test_a_valuation_cross_section_that_mixes_two_sessions_is_refused() -> None:
    other = list(VALUATIONS[1])
    other[1] = "2026-06-15"
    with pytest.raises(PriceDataError, match="carries more than one trade_date"):
        daily_valuations_from_panel_rows([VALUATIONS[0], tuple(other)])


@pytest.mark.parametrize("subject", [None, "", "  000001.SZ ", 1])
def test_a_stored_subject_that_is_not_a_clean_code_is_refused(subject: Any) -> None:
    row = list(_bar_rows(day="2026-06-12")[0])
    row[0] = subject
    with pytest.raises(PriceDataError, match="subject must be a non-empty string"):
        daily_bars_from_panel_rows([tuple(row)])


@pytest.mark.parametrize(
    ("value", "message"),
    [(None, "must be an ISO date string"), ("12 June 2026", "is not an ISO date")],
)
def test_a_stored_trade_date_that_is_not_an_iso_date_is_refused(value: Any, message: str) -> None:
    row = list(_bar_rows(day="2026-06-12")[0])
    row[1] = value
    with pytest.raises(PriceDataError, match=message):
        daily_bars_from_panel_rows([tuple(row)])


@pytest.mark.parametrize("previous_close", [None, 0.0, -1.0, 11, float("nan")])
def test_a_previous_close_that_cannot_be_a_price_is_refused(previous_close: Any) -> None:
    """`session_returns` divides by it twice, so a zero would surface as a `ZeroDivisionError`
    and an `int` would run something other than `float.__truediv__`."""
    bar = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))[PING_AN]
    with pytest.raises(PriceDataError, match="previous_close must be a finite positive float"):
        session_returns(
            bar,
            previous_close=previous_close,
            previous_day=JUNE_11,
            factors=_factor_history(PING_AN),
        )


def test_a_datetime_where_a_date_belongs_is_refused_on_both_entry_points() -> None:
    """`datetime` is a `date` subclass and compares against dates without ever equalling one,
    so a caller who passed one would get a horizon error from a day that is in the window."""
    from datetime import datetime as _datetime

    bar = daily_bars_from_panel_rows(_bar_rows(day="2026-06-12"))[PING_AN]
    with pytest.raises(PriceDataError, match=r"previous_day must be a plain datetime\.date"):
        session_returns(
            bar,
            previous_close=11.3,
            previous_day=_datetime(2026, 6, 11),
            factors=_factor_history(PING_AN),
        )
    with pytest.raises(PriceDataError, match=r"day must be a plain datetime\.date"):
        priced_cross_section(
            daily_bars_from_panel_rows(_bar_rows(day="2026-06-12")),
            _histories(),
            universe=_universe(),
            calendar=_calendar(),
            day=_datetime(2026, 6, 12),
        )
