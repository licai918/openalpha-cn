"""The halt and published-band contracts (`V2-P1-008`).

Every number here comes from a live probe of the real Tushare endpoints on 2026-08-09 and every
row is inlined: nothing in this file touches the network. The sessions the fixtures are drawn
from are named at each use, because the point of several of them is that a property holds on one
stretch of history and not on another.
"""

from __future__ import annotations

from datetime import date

import pytest

from openalpha_cn.domain.daily_prices import (
    MIN_SESSION_ROW_SHARE,
    DailyBar,
    PricedCrossSection,
)
from openalpha_cn.domain.price_limits import (
    KNOWN_SUSPENSION_LIMITATIONS,
    LIMIT_FREE_RATIO,
    MIN_EXPLAINED_SESSION_SHARE,
    PRICE_LIMIT_PANEL_COLUMNS,
    SUSPENSION_PANEL_COLUMNS,
    PriceLimit,
    SuspensionError,
    SuspensionRecord,
    TradingState,
    build_suspension_day,
    explain_unpriced,
    limit_touch,
    price_limits_from_panel_rows,
    suspensions_from_panel_rows,
)

SESSION = date(2024, 6, 28)

# --- inlined from suspend_d(trade_date=20240628), which served 28 rows: 26 S and 2 R.
# The two R names are the whole R half of that session; the S names are the first four of the
# 26 in the order the endpoint returned them.
SUSPEND_D_20240628: tuple[tuple[str, str, str, str | None], ...] = (
    ("603050.SH", "2024-06-28", "R", None),
    ("000615.SZ", "2024-06-28", "R", None),
    ("000040.SZ", "2024-06-28", "S", None),
    ("000413.SZ", "2024-06-28", "S", None),
    ("000836.SZ", "2024-06-28", "S", None),
    ("000961.SZ", "2024-06-28", "S", None),
)

# --- inlined from suspend_d(trade_date=20150708). That session served 1,348 rows: 1,343 S
# (1,312 untimed, 31 carrying an intraday window) and 5 R. `000048.SZ` is one of the 31.
SUSPEND_D_20150708: tuple[tuple[str, str, str, str | None], ...] = (
    ("000048.SZ", "2015-07-08", "S", "13:00-15:00"),
    ("000029.SZ", "2015-07-08", "S", None),
)

# --- inlined from stk_limit(trade_date=20240628) joined to daily(trade_date=20240628).
# (ts_code, up_limit, down_limit, pre_close, board, is_st) -- the six shapes the published band
# takes on one ordinary session.
PUBLISHED_BANDS_20240628: tuple[tuple[str, float, float, float, str, bool], ...] = (
    # main board, 10%, ROUND_HALF_UP -- 3,068 of the day's 3,174 main-board names
    ("000001.SZ", 12.03, 9.85, 10.94, "main", False),
    # main board ST, 5%
    ("603959.SH", 1.58, 1.43, 1.50, "main", True),
    # ChiNext ST, still 20% -- one of 25 such names that day
    ("300029.SZ", 2.30, 1.54, 1.92, "growth", True),
    # Beijing board, 30% rounded *inward*: 7.32 * 1.3 = 9.516 -> 9.51, not 9.52
    ("920924.BJ", 9.51, 5.13, 7.32, "bse", False),
    # a security in its first five sessions: no limit at all, published as a sentinel
    ("603381.SH", 99999.999, 0.01, 29.70, "main", False),
    ("301580.SZ", 999999.999, 0.01, 79.46, "growth", False),
)


def _suspension_rows(
    source: tuple[tuple[str, str, str, str | None], ...],
) -> list[tuple[object, ...]]:
    return [tuple(row) for row in source]


def _limit_rows(day: str = "2024-06-28") -> list[tuple[object, ...]]:
    return [(code, day, up, down) for code, up, down, _, _, _ in PUBLISHED_BANDS_20240628]


def _bar(ts_code: str, *, high: float, low: float, close: float, day: date = SESSION) -> DailyBar:
    return DailyBar(
        ts_code=ts_code,
        trade_date=day,
        open=(high + low) / 2,
        high=high,
        low=low,
        close=close,
        pre_close=low,
        pct_chg=0.0,
        vol=1.0,
        amount=1.0,
    )


# --- suspend_d is three states, not a suspension list ---------------------------------------


def test_the_two_r_rows_of_20240628_are_not_in_the_halted_set() -> None:
    """The brief for this issue put it plainly: reading the whole table as "today's suspended
    set" mislabels the two resumptions. Both `R` names have a real `daily` bar that session."""
    days = suspensions_from_panel_rows(_suspension_rows(SUSPEND_D_20240628))
    day = days[SESSION]

    assert day.resumed == ("000615.SZ", "603050.SH")
    assert set(day.halted).isdisjoint(day.resumed)
    assert day.is_halted("000615.SZ") is False
    assert day.is_halted("603050.SH") is False
    assert day.state_of("603050.SH") is TradingState.resumed


def test_an_s_row_carrying_an_intraday_window_is_not_a_whole_day_halt() -> None:
    """2015-07-08's 1,343 `S` rows split 1,312 / 31 on `suspend_timing`, and all 31 timed ones
    had a bar while none of the 1,312 did. Collapsing the column loses that split."""
    days = suspensions_from_panel_rows(_suspension_rows(SUSPEND_D_20150708))
    day = days[date(2015, 7, 8)]

    assert day.interrupted == ("000048.SZ",)
    assert day.halted == ("000029.SZ",)
    assert day.is_halted("000048.SZ") is False
    assert day.state_of("000048.SZ") is TradingState.interrupted
    assert day.traded == frozenset({"000048.SZ"})
    # The set form the loop-shaped callers use; the tuple form stays sorted and deterministic.
    assert day.halted_codes == frozenset({"000029.SZ"})


def test_the_three_states_have_no_truth_value() -> None:
    """Two of the three mean the security traded, so `if state:` would be right once and wrong
    twice. `RiskWarning` and `CalendarDayStatus` give the same answer."""
    for state in TradingState:
        with pytest.raises(SuspensionError, match="three-valued verdict"):
            bool(state)


def test_a_security_with_no_row_is_none_rather_than_a_fourth_state() -> None:
    days = suspensions_from_panel_rows(_suspension_rows(SUSPEND_D_20240628))

    assert days[SESSION].state_of("600519.SH") is None
    assert days[SESSION].is_halted("600519.SH") is False


def test_an_unknown_suspend_type_is_refused_rather_than_bucketed() -> None:
    """537,671 rows spanning 2007-01..2026-12, pulled in windows narrow enough that none was
    truncated, carried only `S` and `R`. A third value flips both of the things this column
    decides, so guessing is worse than failing."""
    with pytest.raises(SuspensionError, match="carries suspend_type 'H'"):
        build_suspension_day(
            SESSION,
            [
                SuspensionRecord(
                    ts_code="000001.SZ", trade_date=SESSION, suspend_type="H", timing=None
                )
            ],
        )


def test_a_blank_timing_is_refused_rather_than_read_as_a_whole_day_halt() -> None:
    with pytest.raises(SuspensionError, match="carries suspend_timing ''"):
        build_suspension_day(
            SESSION,
            [
                SuspensionRecord(
                    ts_code="000001.SZ", trade_date=SESSION, suspend_type="S", timing=""
                )
            ],
        )


def test_two_disagreeing_states_for_one_security_are_refused() -> None:
    with pytest.raises(SuspensionError, match="is both halted and resumed"):
        build_suspension_day(
            SESSION,
            [
                SuspensionRecord(
                    ts_code="000040.SZ", trade_date=SESSION, suspend_type="S", timing=None
                ),
                SuspensionRecord(
                    ts_code="000040.SZ", trade_date=SESSION, suspend_type="R", timing=None
                ),
            ],
        )


def test_a_byte_identical_duplicate_collapses() -> None:
    """One live `namechange` pull returned 380 exact duplicates, so this is a real shape rather
    than an exotic input, and a duplicate carries no fact the original does not."""
    record = SuspensionRecord(
        ts_code="000040.SZ", trade_date=SESSION, suspend_type="S", timing=None
    )

    assert build_suspension_day(SESSION, [record, record]).halted == ("000040.SZ",)


def test_a_row_from_another_session_is_refused() -> None:
    with pytest.raises(SuspensionError, match="one call is one session"):
        build_suspension_day(
            SESSION,
            [
                SuspensionRecord(
                    ts_code="000040.SZ",
                    trade_date=date(2024, 6, 27),
                    suspend_type="S",
                    timing=None,
                )
            ],
        )


def test_the_corpus_reader_groups_many_sessions_unlike_the_bar_reader() -> None:
    """`daily_bars_from_panel_rows` refuses two dates because a cross section joins to one day's
    factors. A halt row joins to nothing, and the unit its consumers want is a year."""
    rows = _suspension_rows(SUSPEND_D_20240628) + _suspension_rows(SUSPEND_D_20150708)
    days = suspensions_from_panel_rows(rows)

    assert sorted(days) == [date(2015, 7, 8), SESSION]


def test_a_stored_suspension_row_of_the_wrong_width_is_refused() -> None:
    with pytest.raises(SuspensionError, match="row 0 has 3 values, expected 4"):
        suspensions_from_panel_rows([("000040.SZ", "2024-06-28", "S")])


def test_the_suspension_panel_columns_are_the_positional_contract() -> None:
    assert SUSPENSION_PANEL_COLUMNS == (
        "subject",
        "trade_date",
        "suspend_type",
        "suspend_timing",
    )


# --- stk_limit: the published band --------------------------------------------------------


def test_the_six_published_shapes_of_one_session_survive_the_round_trip() -> None:
    limits = price_limits_from_panel_rows(_limit_rows())

    assert set(limits) == {code for code, *_ in PUBLISHED_BANDS_20240628}
    assert limits["920924.BJ"].up_limit == 9.51
    assert limits["920924.BJ"].down_limit == 5.13
    assert limits["603381.SH"].up_limit == 99999.999


def test_the_beijing_board_band_is_not_what_round_half_up_gives() -> None:
    """`920924.BJ`: pre_close 7.32, nominal 30%. `7.32 * 1.30 = 9.516`, which rounds half-up to
    9.52 -- and the exchange published 9.51. All 249 `.BJ` names on 2024-06-28 match the inward
    rounding and only 118 match half-up."""
    limits = price_limits_from_panel_rows(_limit_rows())
    published = limits["920924.BJ"]

    assert published.up_limit == 9.51
    assert round(7.32 * 1.30, 2) == 9.52
    assert published.implied_ratio(7.32) == pytest.approx(0.2992, abs=1e-4)


def test_an_st_chinext_name_keeps_the_boards_twenty_percent() -> None:
    """25 of 2024-06-28's 128 ST names are on ChiNext or STAR and every one of them has a 20%
    band. A rule that lets `is_st` win over the board computes 5% for all 25."""
    limits = price_limits_from_panel_rows(_limit_rows())

    assert limits["300029.SZ"].implied_ratio(1.92) == pytest.approx(0.20, abs=5e-3)


def test_the_limit_free_sentinels_are_classified_by_ratio_and_not_by_value() -> None:
    """Four sentinel encodings have been observed and two of them were retired, so a whitelist
    would misread the fifth. The two populations are 1.44x and 384x apart."""
    limits = price_limits_from_panel_rows(_limit_rows())

    assert limits["603381.SH"].is_bounded(29.70) is False
    assert limits["301580.SZ"].is_bounded(79.46) is False
    assert limits["000001.SZ"].is_bounded(10.94) is True
    assert limits["920924.BJ"].is_bounded(7.32) is True
    # The widest real band measured (2018-02-08 first-day listings, +44%) stays bounded, and it
    # is nowhere near the threshold.
    assert (
        PriceLimit(
            ts_code="300740.SZ", trade_date=date(2018, 2, 8), up_limit=30.57, down_limit=13.59
        ).is_bounded(21.23)
        is True
    )
    assert LIMIT_FREE_RATIO == 2.0


def test_a_limit_free_bar_touches_neither_side() -> None:
    """`301580.SZ` on its third session ran from 79.46 to 268.00 and, with no limit, cannot be
    at one. The verdict falls out of the comparison rather than out of a sentinel branch."""
    limits = price_limits_from_panel_rows(_limit_rows())
    touch = limit_touch(
        _bar("301580.SZ", high=268.00, low=150.00, close=250.00), limits["301580.SZ"]
    )

    assert (touch.at_up, touch.at_down, touch.one_price_up, touch.one_price_down) == (
        False,
        False,
        False,
        False,
    )


def test_a_one_price_limit_up_bar_is_reported_as_both_touched_and_one_price() -> None:
    limits = price_limits_from_panel_rows(_limit_rows())
    touch = limit_touch(_bar("000001.SZ", high=12.03, low=12.03, close=12.03), limits["000001.SZ"])

    assert touch.at_up is True
    assert touch.closed_at_up is True
    assert touch.one_price_up is True
    assert touch.one_price_down is False


def test_a_bar_that_only_reached_the_limit_intraday_is_touched_but_not_one_price() -> None:
    limits = price_limits_from_panel_rows(_limit_rows())
    touch = limit_touch(_bar("000001.SZ", high=12.03, low=10.50, close=11.00), limits["000001.SZ"])

    assert touch.at_up is True
    assert touch.one_price_up is False
    assert touch.closed_at_up is False


def test_a_band_from_another_security_or_another_day_is_refused() -> None:
    limits = price_limits_from_panel_rows(_limit_rows())

    with pytest.raises(SuspensionError, match="another's limit"):
        limit_touch(_bar("600519.SH", high=1.0, low=1.0, close=1.0), limits["000001.SZ"])
    with pytest.raises(SuspensionError, match="have to be the same session"):
        limit_touch(
            _bar("000001.SZ", high=1.0, low=1.0, close=1.0, day=date(2024, 6, 27)),
            limits["000001.SZ"],
        )


def test_two_sessions_in_one_band_set_are_refused() -> None:
    with pytest.raises(SuspensionError, match="one call is one session"):
        price_limits_from_panel_rows(
            [
                ("000001.SZ", "2024-06-28", 12.03, 9.85),
                ("000002.SZ", "2024-06-27", 12.03, 9.85),
            ]
        )


def test_one_security_twice_in_one_band_set_is_refused() -> None:
    with pytest.raises(SuspensionError, match="appears twice"):
        price_limits_from_panel_rows(
            [
                ("000001.SZ", "2024-06-28", 12.03, 9.85),
                ("000001.SZ", "2024-06-28", 12.03, 9.85),
            ]
        )


def test_an_inverted_band_is_refused() -> None:
    with pytest.raises(SuspensionError, match="is an empty band"):
        price_limits_from_panel_rows([("000001.SZ", "2024-06-28", 9.85, 12.03)])


def test_a_null_limit_is_refused_rather_than_compared_against_every_bar() -> None:
    with pytest.raises(SuspensionError, match="up_limit must be a finite positive float"):
        price_limits_from_panel_rows([("000001.SZ", "2024-06-28", None, 9.85)])


def test_the_price_limit_panel_columns_are_the_positional_contract() -> None:
    assert PRICE_LIMIT_PANEL_COLUMNS == ("subject", "trade_date", "up_limit", "down_limit")


def test_is_bounded_refuses_a_previous_close_that_is_not_a_plain_positive_float() -> None:
    limits = price_limits_from_panel_rows(_limit_rows())

    with pytest.raises(SuspensionError, match="previous_close must be a finite positive float"):
        limits["000001.SZ"].is_bounded(0.0)
    with pytest.raises(SuspensionError, match="previous_close must be a finite positive float"):
        limits["000001.SZ"].implied_ratio(11)  # type: ignore[arg-type]


# --- the answer to daily_prices' a_partial_cross_section_is_invisible_without_suspend_d ------


def _cross_section(unpriced: tuple[str, ...], day: date = SESSION) -> PricedCrossSection:
    return PricedCrossSection(
        day=day, exchange="SSE", priced=(), unpriced=unpriced, unlisted_bars=()
    )


def test_an_unpriced_name_with_a_whole_day_halt_behind_it_is_explained() -> None:
    days = suspensions_from_panel_rows(_suspension_rows(SUSPEND_D_20240628))
    explained = explain_unpriced(_cross_section(("000040.SZ", "000413.SZ")), days[SESSION])

    assert explained.halted == ("000040.SZ", "000413.SZ")
    assert explained.unexplained == ()
    assert explained.is_fully_explained is True
    assert explained.unpriced_count == 2


def test_a_short_fetch_is_visible_because_no_halt_accounts_for_it() -> None:
    """The failure `KNOWN_PRICE_LIMITATIONS`' `a_partial_cross_section_is_invisible_without_
    suspend_d` names: a session that came back short stores a well-formed partition and reports
    the rest of the market as unpriced. With the halts joined on, the population without a halt
    behind it is the finding."""
    days = suspensions_from_panel_rows(_suspension_rows(SUSPEND_D_20240628))
    explained = explain_unpriced(
        _cross_section(("000040.SZ", "600519.SH", "000651.SZ")), days[SESSION]
    )

    assert explained.halted == ("000040.SZ",)
    assert explained.unexplained == ("600519.SH", "000651.SZ")
    assert explained.is_fully_explained is False


def test_a_resumption_and_an_intraday_halt_never_explain_a_missing_bar() -> None:
    """Both mean the security traded. Counting them would let a session with 1,300 intraday
    halts explain away 1,300 absent bars, which is what makes an alarm useless."""
    day = build_suspension_day(
        SESSION,
        [
            SuspensionRecord(
                ts_code="000615.SZ", trade_date=SESSION, suspend_type="R", timing=None
            ),
            SuspensionRecord(
                ts_code="000048.SZ",
                trade_date=SESSION,
                suspend_type="S",
                timing="13:00-15:00",
            ),
        ],
    )
    explained = explain_unpriced(_cross_section(("000615.SZ", "000048.SZ")), day)

    assert explained.halted == ()
    assert explained.unexplained == ("000615.SZ", "000048.SZ")


def test_halts_from_the_wrong_day_are_refused_rather_than_explaining_nothing() -> None:
    days = suspensions_from_panel_rows(_suspension_rows(SUSPEND_D_20150708))

    with pytest.raises(SuspensionError, match="explains nothing"):
        explain_unpriced(_cross_section(("000029.SZ",)), days[date(2015, 7, 8)])


# --- the disclosures ------------------------------------------------------------------------


def test_the_known_limitations_name_the_measured_boundaries() -> None:
    """Named disclosure, the shape `KNOWN_CALENDAR_LOOKAHEAD` / `KNOWN_UNIVERSE_LIMITATIONS` /
    `KNOWN_ADJUSTMENT_LIMITATIONS` / `KNOWN_PRICE_LIMITATIONS` already use. Asserted as a
    subset rather than an equality so a later issue can add one without this test forcing an
    edit -- the enumerative form is what bound `V2-P1-007` and this issue in turn."""
    codes = {entry.code for entry in KNOWN_SUSPENSION_LIMITATIONS}

    assert {
        "intraday_halts_are_unmarked_before_2015",
        "stk_limit_starts_in_2007_and_reached_the_beijing_board_late",
        "the_limit_free_sentinel_encoding_has_changed_twice",
        "the_published_band_is_not_reproducible_from_board_and_st_alone",
        "both_datasets_are_dated_one_session_late_rather_than_at_the_open",
        "silent_truncation_at_a_cap_this_cross_section_is_close_to",
    } <= codes
    assert all(len(entry.detail) > 120 for entry in KNOWN_SUSPENSION_LIMITATIONS)


def test_the_explained_floor_leaves_headroom_over_the_worst_measured_year() -> None:
    """A full-year census of 2015 -- 244 sessions, one `daily` and one `suspend_d` request each
    -- puts the minimum explained share at 0.9274 (2015-01-09: 2,344 bars + 249 whole-day halts
    against a median explained cross section of 2,796). The constant has to sit below that or it
    refuses a true partition of that year, and above the 0.578 the bar count alone reaches on
    2015-07-09 or it buys nothing over `MIN_SESSION_ROW_SHARE`."""
    assert MIN_SESSION_ROW_SHARE < MIN_EXPLAINED_SESSION_SHARE < 0.9274
    assert MIN_EXPLAINED_SESSION_SHARE > 0.578
