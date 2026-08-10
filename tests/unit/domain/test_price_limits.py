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
    EXPLAINED_SESSION_HALF_WINDOW,
    KNOWN_SUSPENSION_LIMITATIONS,
    LIMIT_FREE_RATIO,
    MIN_EXPLAINED_SESSION_SHARE,
    PRICE_LIMIT_PANEL_COLUMNS,
    SUSPENSION_CORPUS_FIRST_SESSION,
    SUSPENSION_PANEL_COLUMNS,
    PriceLimit,
    SuspensionError,
    SuspensionRecord,
    TradingState,
    build_suspension_day,
    explain_unpriced,
    halt_spans_the_close,
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

# --- inlined from stk_limit(trade_date=20240202), which served 6,741 rows. `920656.BJ` listed
# that day and is the session's *only* row with `down_limit == 0.0`; its `up_limit` of 99999.99
# is one of six sentinel encodings -- the Beijing board's, unchanged since 2022-02-28. The
# `daily` row for the same session gives pre_close 19.90 (18.00 / 19.91 / 17.55 / 19.90).
BEIJING_FIRST_SESSION_20240202: tuple[str, str, float, float] = (
    "920656.BJ",
    "2024-02-02",
    99999.99,
    0.0,
)
# --- inlined from stk_limit(ts_code=920680.BJ, start_date=20251201, end_date=20251231): the
# first session of a delisting arrangement, sitting between a 12.37/6.67 band on 12-10 and a
# 3.57/1.93 one on 12-12. `daily` gives pre_close 9.52 for 12-11.
BEIJING_DELISTING_20251211: tuple[str, str, float, float] = (
    "920680.BJ",
    "2025-12-11",
    99999.99,
    0.0,
)


# --- the whole-history calibration behind MIN_EXPLAINED_SESSION_SHARE. One `daily` row count
# and one `suspend_d` whole-day-halt count for every session of 1991-01-02..2026-08-07 (8,690
# sessions, 36 years), pulled in windows narrow enough that no response was truncated. Each row
# is (year, median bars, min explained share against the *year's* median, min explained share
# against a rolling +/-20-session median). The third column is what the first cut of this
# constant was calibrated on -- from three years -- and the fourth is what the guard computes.
# fmt: off
EXPLAINED_SESSION_CENSUS: tuple[tuple[int, int, float, float], ...] = (
    (1991, 10, 0.300, 0.429), (1992, 30, 0.200, 0.462), (1993, 98, 0.347, 0.596),
    (1994, 270, 0.607, 0.605), (1995, 296, 0.578, 0.586), (1996, 377, 0.809, 0.900),
    (1997, 670, 0.766, 0.935), (1998, 781, 0.919, 0.959), (1999, 872, 0.937, 0.970),
    (2000, 968, 0.947, 0.986), (2001, 1089, 0.940, 0.963), (2002, 1143, 0.951, 0.969),
    (2003, 1206, 0.961, 0.978), (2004, 1300, 0.941, 0.975), (2005, 1320, 0.987, 0.982),
    (2006, 1193, 0.981, 0.986), (2007, 1342, 0.958, 0.979), (2008, 1490, 0.959, 0.977),
    (2009, 1534, 0.990, 0.995), (2010, 1796, 0.906, 0.989), (2011, 2113, 0.923, 0.989),
    (2012, 2361, 0.955, 0.996), (2013, 2366, 0.996, 1.000), (2014, 2322, 0.978, 0.991),
    (2015, 2359, 0.927, 0.994), (2016, 2642, 0.977, 0.996), (2017, 3066, 0.925, 0.989),
    (2018, 3378, 0.984, 0.996), (2019, 3646, 0.980, 0.997), (2020, 3925, 0.954, 0.995),
    (2021, 4466, 0.940, 0.996), (2022, 4845, 0.969, 0.996), (2023, 5207, 0.975, 0.999),
    (2024, 5345, 0.997, 0.999), (2025, 5406, 0.994, 0.999), (2026, 5493, 0.993, 0.999),
)
# fmt: on


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


def test_the_timing_column_survives_the_partition_and_is_reachable_by_code() -> None:
    """`TradingState` answers "did it trade"; the column itself answers "when did it not", and
    only the second can say whether the close was an auction price. Before `V2-P1-017`'s review
    the window was consumed by `SuspensionRecord.state` and then dropped.
    """
    days = suspensions_from_panel_rows(_suspension_rows(SUSPEND_D_20150708))
    day = days[date(2015, 7, 8)]

    assert day.timings == (("000048.SZ", "13:00-15:00"),)
    assert day.timing_of("000048.SZ") == "13:00-15:00"
    assert day.timing_of("000029.SZ") is None
    assert day.timing_of("600519.SH") is None


@pytest.mark.parametrize(
    ("timing", "spans"),
    [
        ("13:00-15:00", True),
        ("14:52-14:57", True),
        ("13:36-13:46,14:53-14:57", True),
        ("09:30-13:00", False),
        ("09:30-10:00", False),
        ("09:30-10:01", False),
        ("09:36-10:06", False),
        ("09:55-10:05", False),
        ("09:43-09:53", False),
        ("09:31-09:41,09:43-09:53", False),
        ("11:21-13:01", False),
        ("09:30", True),
        ("09:30-25:00", True),
        ("上午", True),
    ],
)
def test_a_halt_window_is_read_as_spanning_the_close_from_its_right_endpoints(
    timing: str, spans: bool
) -> None:
    """The first eleven are every distinct `suspend_timing` value served across a quarterly
    sweep of 2013-01..2026-10 plus the sessions this module already probes. The last three are
    not served shapes and are here for the unparseable arm: a window with no separator, one
    whose right endpoint is not a clock time, and a string that is not a window at all.

    `14:57` is where the closing call auction opens, so a window reaching it leaves `daily`'s
    close as an earlier print. The boundary is closed and an unreadable endpoint counts as
    spanning: reading either as "resumed in time" is the fail-open direction for a contract
    whose two prices are closes.
    """
    assert halt_spans_the_close(timing) is spans


def test_two_disagreeing_halt_windows_for_one_security_are_refused() -> None:
    """The corpus spells a two-window halt inside a single row ('13:36-13:46,14:53-14:57'), so
    one row already carries every window a session has and two rows carrying different ones
    disagree about the same question -- the shape `_reconcile_states` refuses for two `S`
    states, and refused the same way. A byte-identical duplicate still collapses.
    """
    morning = SuspensionRecord(
        ts_code="000048.SZ", trade_date=SESSION, suspend_type="S", timing="09:30-10:00"
    )
    afternoon = SuspensionRecord(
        ts_code="000048.SZ", trade_date=SESSION, suspend_type="S", timing="13:00-15:00"
    )

    assert build_suspension_day(SESSION, [morning, morning]).timings == (
        ("000048.SZ", "09:30-10:00"),
    )
    with pytest.raises(SuspensionError, match="is halted '09:30-10:00' and '13:00-15:00'"):
        build_suspension_day(SESSION, [morning, afternoon])


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


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
@pytest.mark.parametrize(
    ("timing", "state"),
    [(None, TradingState.halted), ("09:30-09:40", TradingState.interrupted)],
    ids=["untimed", "timed"],
)
def test_an_r_row_beside_an_s_row_resolves_to_the_s_rows_own_state(
    order: tuple[int, int], timing: str | None, state: TradingState
) -> None:
    """One session serving a security both an `R` and an `S` is a real shape, not a broken
    fetch, and the `S` is the row that decides.

    Inlined from `suspend_d(start_date=20260101, end_date=20261231)`: `603056.SH` on 2026-01-12
    carries `R`/null and `S`/null, and `600421.SH` on 2026-06-01 carries `R`/null and
    `S`/'9:30-9:40'. This used to raise "is both halted and resumed ... two sources that were
    never reconciled", and the attribution is what a census disproved -- one request, one
    session, both rows. Every session of 2015..2026 (334,362 rows, no response truncated) holds
    73 `(security, session)` pairs with two rows and **all 73 are one `R` and one `S`**; on the
    47 whose security `daily` carries that year, the `S` row's own timing predicts the bar 45
    times. See `KNOWN_SUSPENSION_LIMITATIONS`'
    `a_resumption_and_a_halt_can_share_one_session` for the two misses.

    Both orders, because `build_suspension_day` folds a session's rows as they arrive and a
    partition whose meaning depended on fetch order would be no contract at all.
    """
    rows = [
        SuspensionRecord(ts_code="000040.SZ", trade_date=SESSION, suspend_type="S", timing=timing),
        SuspensionRecord(ts_code="000040.SZ", trade_date=SESSION, suspend_type="R", timing=None),
    ]

    day = build_suspension_day(SESSION, [rows[order[0]], rows[order[1]]])

    assert day.state_of("000040.SZ") is state
    assert day.resumed == ()
    assert day.timing_of("000040.SZ") == timing


def test_an_untimed_and_a_timed_halt_for_one_security_are_still_refused() -> None:
    """The one multi-row shape with no finer row to prefer, and the census never served it.

    `_reconcile_states` lets `resumed` yield because an `S` row answers a strictly finer
    question than an `R` does. Two `S` rows, one untimed and one timed, answer the *same*
    question and disagree -- "the whole session" against "these ten minutes" -- so there is
    nothing to prefer and it stays a refusal. None of the 73 multi-row pairs of 2015..2026 is
    this shape.
    """
    with pytest.raises(SuspensionError, match="is both halted and interrupted"):
        build_suspension_day(
            SESSION,
            [
                SuspensionRecord(
                    ts_code="000040.SZ", trade_date=SESSION, suspend_type="S", timing=None
                ),
                SuspensionRecord(
                    ts_code="000040.SZ",
                    trade_date=SESSION,
                    suspend_type="S",
                    timing="09:30-09:40",
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


def test_a_stored_blank_timing_is_refused_on_the_read_side_too() -> None:
    """The provider refuses `""` on the way in; this is the same refusal on the way out, and it
    is not redundant -- a partition written before that parse existed, or by any other writer,
    reaches `suspensions_from_panel_rows` directly. Folding `""` into `None` here would turn a
    row whose upstream *populated* the window into a whole-day halt, which is the one direction
    that manufactures an explanation for an absent bar."""
    with pytest.raises(SuspensionError, match="suspend_timing must be a non-empty string"):
        suspensions_from_panel_rows([("000040.SZ", "2024-06-28", "S", "")])


def test_the_halt_corpus_comes_back_read_only() -> None:
    """A year of halts is shared by `_refuse_unexplained_thin_sessions` and by whatever walks a
    backtest, so one caller replacing a session would silently edit what the others hold --
    `name_histories_from_panel_rows`' reason, and the same `MappingProxyType`."""
    days = suspensions_from_panel_rows(_suspension_rows(SUSPEND_D_20240628))

    with pytest.raises(TypeError, match="does not support item assignment"):
        days[SESSION] = days[SESSION]  # type: ignore[index]


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
    """Six sentinel encodings have been observed across three exchanges and three of them were
    retired, so a whitelist written from the four this repository knew first misreads two. A
    459-session scan of 1,918,266 joined rows puts the widest real band at 1.4409x and the
    narrowest sentinel at 115.61x, with nothing whatever in between."""
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


# --- the Beijing board's published zero floor ------------------------------------------------


def test_a_beijing_first_session_publishes_a_zero_lower_limit_and_is_stored() -> None:
    """The row that made every `stk_limit` year since 2021 unstorable. `920656.BJ` on its
    listing day carries `(99999.99, 0.0)` -- a *published* pair, not a null and not a fault --
    and a `down_limit > 0` rule refuses the whole 6,741-row cross section, which
    `write_price_limits` turns into a refusal of the whole 2024 partition.

    It is not one row either. Every `.BJ` security's first session carries this shape, which is
    235 distinct trading days at or after 2022-02-28 (64 in 2022, 75 in 2023, 23 in 2024, 26 in
    2025, 47 in 2026 by `stock_basic`'s `list_date`), so the years 2022..2026 all fail."""
    code, day, up, down = BEIJING_FIRST_SESSION_20240202
    limits = price_limits_from_panel_rows([(code, day, up, down)])

    assert limits[code].down_limit == 0.0
    assert limits[code].up_limit == 99999.99
    # 99999.99 / 19.90 = 5,025x: the ratio test reads the fifth encoding without ever having
    # been told about it, which is the whole reason `is_bounded` is not a value whitelist.
    assert limits[code].is_bounded(19.90) is False


def test_the_delisting_arrangement_first_session_carries_the_same_shape() -> None:
    """Not a listing-day quirk. `920680.BJ` traded inside a 12.37/6.67 band on 2025-12-10, had
    `(99999.99, 0.0)` on the 11th -- the first session of its delisting arrangement -- and a
    3.57/1.93 band on the 12th. So the zero floor recurs on securities that have been listed
    for years, and a fix scoped to "first five sessions" would still lose it."""
    code, day, up, down = BEIJING_DELISTING_20251211
    limits = price_limits_from_panel_rows([(code, day, up, down)])

    assert limits[code].down_limit == 0.0
    assert limits[code].is_bounded(9.52) is False


def test_a_zero_floor_bar_touches_neither_side_without_a_special_case() -> None:
    """`920656.BJ`'s own bar that session: 18.00 / 19.91 / 17.55 / 19.90. `low <= 0.0` is false
    for any real price, so "unbounded below" falls out of the comparison exactly as "unbounded
    above" does."""
    code, day, up, down = BEIJING_FIRST_SESSION_20240202
    limits = price_limits_from_panel_rows([(code, day, up, down)])
    touch = limit_touch(
        _bar(code, high=19.91, low=17.55, close=19.90, day=date(2024, 2, 2)), limits[code]
    )

    assert (touch.at_up, touch.at_down) == (False, False)
    assert (touch.one_price_up, touch.one_price_down) == (False, False)


def test_a_negative_lower_limit_is_still_refused() -> None:
    """The domain widened to admit zero, not to admit anything below it: a price floor under
    zero is malformed, and beside a real `up_limit` the `down > up` check that follows would
    wave it through."""
    with pytest.raises(SuspensionError, match="down_limit must be a finite non-negative float"):
        price_limits_from_panel_rows([("000001.SZ", "2024-06-28", 12.03, -0.01)])
    with pytest.raises(SuspensionError, match="down_limit must be a finite non-negative float"):
        price_limits_from_panel_rows([("000001.SZ", "2024-06-28", 12.03, None)])


def test_a_zero_or_negative_upper_limit_is_refused() -> None:
    """The *upper* side keeps the strict rule, and nothing published has ever broken it: an
    `up_limit` of zero would bound every price out of existence, and `is_bounded` would then
    call a real band unbounded for every previous close."""
    with pytest.raises(SuspensionError, match="up_limit must be a finite positive float"):
        price_limits_from_panel_rows([("000001.SZ", "2024-06-28", 0.0, 0.0)])
    with pytest.raises(SuspensionError, match="up_limit must be a finite positive float"):
        price_limits_from_panel_rows([("000001.SZ", "2024-06-28", -12.03, -14.0)])


def test_all_six_sentinel_encodings_land_on_the_same_verdict() -> None:
    """The case against a whitelist, made from the values a whitelist would have been written
    from. Four of these six were known when `is_bounded` was written; `10000.0` (SSE, seen once,
    2007-01-04) and `99999.99` (BSE, every listing day since 2022-02-28) were not, and a
    whitelist of the first four reads both as real prices -- `10000.0` against a 2.96 close
    would have become a 3,378x "band" the reader believed.

    Each pair below is a real `(up_limit, pre_close)` from the scan; the last two rows are the
    boundary the classification actually has to hold, and it holds by a factor of 80 and by 39%
    respectively."""
    sentinels = (
        ("600145.SH", 10000.0, 0.01, 2.96),  # SSE, 2007-01-04
        ("688030.SH", 100000.0, 0.01, 44.80),  # SSE, 2019-10-08
        ("688808.SH", 99999.999, 0.01, 864.99),  # SSE, 2026-04-29 -- the narrowest measured
        ("300879.SZ", 1000000.0, 0.01, 10.58),  # SZSE, 2020-09-01
        ("301225.SZ", 999999.999, 0.01, 38.33),  # SZSE, 2023-06-21
        ("920857.BJ", 99999.99, 0.0, 12.00),  # BSE, 2022-02-28 -- the board's first listing
    )
    for code, up, down, pre_close in sentinels:
        limit = PriceLimit(ts_code=code, trade_date=SESSION, up_limit=up, down_limit=down)
        assert limit.is_bounded(pre_close) is False, code
        assert limit.up_limit / pre_close > 115.0, code

    widest_real = PriceLimit(
        ts_code="300830.SZ", trade_date=date(2020, 5, 6), up_limit=6.34, down_limit=2.82
    )

    assert widest_real.is_bounded(4.40) is True
    assert widest_real.up_limit / 4.40 == pytest.approx(1.4409, abs=1e-4)


def test_the_ratio_test_classifies_the_boundary_itself_as_limit_free() -> None:
    """`LIMIT_FREE_RATIO` is a closed bound from above: an `up_limit` of exactly twice the
    previous close is read as a sentinel, not as a real band. The choice is arbitrary in the
    gap it sits in -- the widest real band measured is 1.4409x (`300830.SZ`, 2020-05-06) and
    the narrowest sentinel 115.61x -- and it is pinned so that it cannot drift silently."""
    assert (
        PriceLimit(
            ts_code="000001.SZ", trade_date=SESSION, up_limit=20.00, down_limit=5.00
        ).is_bounded(10.00)
        is False
    )
    assert (
        PriceLimit(
            ts_code="000001.SZ", trade_date=SESSION, up_limit=19.99, down_limit=5.00
        ).is_bounded(10.00)
        is True
    )


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


def test_the_r_plus_s_shape_is_disclosed_with_the_census_that_measured_it() -> None:
    """A reconciliation that is not disclosed is a silent repair.

    `_reconcile_states` decides something the raw rows do not say outright -- that an `S` row
    beside an `R` is the one to believe -- and it is right on 45 of the 47 pairs `daily` can
    speak to. The other two are a boundary of what this dataset answers, so they belong in the
    table beside the seven boundaries that were already there rather than in a commit message.
    Pinned separately from the subset assertion above so that renaming this entry out of the
    table fails a test instead of quietly withdrawing the disclosure.
    """
    entry = next(
        item
        for item in KNOWN_SUSPENSION_LIMITATIONS
        if item.code == "a_resumption_and_a_halt_can_share_one_session"
    )

    # The census the reconciliation rests on, and both named misses.
    assert "334,362" in entry.detail
    assert "73" in entry.detail
    assert "45" in entry.detail
    assert "603003.SH" in entry.detail
    assert "688766.SH" in entry.detail


def test_the_explained_floor_clears_every_year_the_halt_corpus_actually_covers() -> None:
    """The calibration this constant was re-derived from, carried as data so it can be read.

    `EXPLAINED_SESSION_CENSUS` is one row per year of 1991-01-02..2026-08-07 -- 8,690 sessions,
    one `daily` row count and one `suspend_d` whole-day-halt count each, pulled in windows
    narrow enough that no response was truncated. The two claims the guard rests on are both
    checkable from it:

    every year `suspend_d` covers clears 0.85 against a rolling median (the worst is 2001 at
    0.963), and the four years that a *whole-year* median would have refused -- 1994..1997,
    where nothing is short and the market is simply growing -- are the reason the median is
    local. The years that still fall short are exactly the ones with no halt corpus behind
    them, which is why `SUSPENSION_CORPUS_FIRST_SESSION` exists rather than a lower floor."""
    covered = [row for row in EXPLAINED_SESSION_CENSUS if row[0] >= 2000]
    uncovered = [row for row in EXPLAINED_SESSION_CENSUS if row[0] < 1999]

    assert len(EXPLAINED_SESSION_CENSUS) == 36
    assert min(rolling for _, _, _, rolling in covered) > MIN_EXPLAINED_SESSION_SHARE
    # 1994..1997 pass on a rolling median only from 1996; before that the corpus is empty.
    assert [year for year, _, whole, _ in uncovered if whole < MIN_EXPLAINED_SESSION_SHARE] == [
        1991,
        1992,
        1993,
        1994,
        1995,
        1996,
        1997,
    ]
    assert [year for year, _, _, rolling in uncovered if rolling < MIN_EXPLAINED_SESSION_SHARE] == [
        1991,
        1992,
        1993,
        1994,
        1995,
    ]
    assert SUSPENSION_CORPUS_FIRST_SESSION.isoformat() == "1999-05-04"
    assert EXPLAINED_SESSION_HALF_WINDOW == 20


def test_the_explained_floor_leaves_headroom_over_the_worst_measured_year() -> None:
    """A full-year census of 2015 -- 244 sessions, one `daily` and one `suspend_d` request each
    -- puts the minimum explained share at 0.9274 (2015-01-09: 2,344 bars + 249 whole-day halts
    against a median explained cross section of 2,796). The constant has to sit below that or it
    refuses a true partition of that year, and above the 0.578 the bar count alone reaches on
    2015-07-09 or it buys nothing over `MIN_SESSION_ROW_SHARE`."""
    assert MIN_SESSION_ROW_SHARE < MIN_EXPLAINED_SESSION_SHARE < 0.9274
    assert MIN_EXPLAINED_SESSION_SHARE > 0.578
