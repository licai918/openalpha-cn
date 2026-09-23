"""The trading-calendar contract (`V2-P1-004`), against real SSE rows.

Every fixture below is verbatim Tushare `trade_cal` output for the SSE exchange, in the
descending `cal_date` order the endpoint actually returns, captured while this task was
implemented. No test here touches the network: the rows are the data, inlined.

The property this file exists to pin is the three-valued verdict. A boolean answer conflates
"the exchange was shut" with "we have no idea", and the second one is the dangerous one: the
whole of 2027 is absent from the published calendar today, so a boolean calendar reports it
as one continuous holiday and every downstream backtest, label and rebalance quietly agrees.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from openalpha_cn.domain.trading_calendar import (
    KNOWN_CALENDAR_LOOKAHEAD,
    CalendarDay,
    CalendarDayStatus,
    CalendarHorizonError,
    TradingCalendar,
    TradingCalendarError,
    build_trading_calendar,
    trading_calendar_from_panel_rows,
)

# --- real Tushare `trade_cal` rows, exchange=SSE, descending as returned ----------------

SPRING_FESTIVAL_2024 = (
    ("20240223", 1, "20240222"),
    ("20240222", 1, "20240221"),
    ("20240221", 1, "20240220"),
    ("20240220", 1, "20240219"),
    ("20240219", 1, "20240208"),
    ("20240218", 0, "20240208"),
    ("20240217", 0, "20240208"),
    ("20240216", 0, "20240208"),
    ("20240215", 0, "20240208"),
    ("20240214", 0, "20240208"),
    ("20240213", 0, "20240208"),
    ("20240212", 0, "20240208"),
    ("20240211", 0, "20240208"),
    ("20240210", 0, "20240208"),
    ("20240209", 0, "20240208"),
    ("20240208", 1, "20240207"),
    ("20240207", 1, "20240206"),
    ("20240206", 1, "20240205"),
    ("20240205", 1, "20240202"),
)

NATIONAL_DAY_2024 = (
    ("20241014", 1, "20241011"),
    ("20241013", 0, "20241011"),
    ("20241012", 0, "20241011"),
    ("20241011", 1, "20241010"),
    ("20241010", 1, "20241009"),
    ("20241009", 1, "20241008"),
    ("20241008", 1, "20240930"),
    ("20241007", 0, "20240930"),
    ("20241006", 0, "20240930"),
    ("20241005", 0, "20240930"),
    ("20241004", 0, "20240930"),
    ("20241003", 0, "20240930"),
    ("20241002", 0, "20240930"),
    ("20241001", 0, "20240930"),
    ("20240930", 1, "20240927"),
    ("20240929", 0, "20240927"),
    ("20240928", 0, "20240927"),
    ("20240927", 1, "20240926"),
    ("20240926", 1, "20240925"),
    ("20240925", 1, "20240924"),
    ("20240924", 1, "20240923"),
    ("20240923", 1, "20240920"),
)

# The 2015 Victory Day parade recess. Both closed weekdays were ordinary sessions in the 2015
# schedule published in late 2014; the State Council General Office announced the closure on
# 2015-05-13, which is 132 days after the availability instant this repository assigns them.
PARADE_RECESS_2015 = (
    ("20150907", 1, "20150902"),
    ("20150906", 0, "20150902"),
    ("20150905", 0, "20150902"),
    ("20150904", 0, "20150902"),
    ("20150903", 0, "20150902"),
    ("20150902", 1, "20150901"),
    ("20150901", 1, "20150831"),
)

END_OF_2026 = (
    ("20261231", 1, "20261230"),
    ("20261230", 1, "20261229"),
    ("20261229", 1, "20261228"),
    ("20261228", 1, "20261225"),
    ("20261227", 0, "20261225"),
    ("20261226", 0, "20261225"),
    ("20261225", 1, "20261224"),
    ("20261224", 1, "20261223"),
    ("20261223", 1, "20261222"),
    ("20261222", 1, "20261221"),
    ("20261221", 1, "20261218"),
)


def _day(raw: str) -> date:
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))


def _days(rows: tuple[tuple[str, int, str], ...]) -> tuple[CalendarDay, ...]:
    return tuple(
        CalendarDay(
            calendar_date=_day(cal_date),
            is_trading=bool(is_open),
            previous_trading_date=_day(pretrade),
        )
        for cal_date, is_open, pretrade in rows
    )


def _calendar(rows: tuple[tuple[str, int, str], ...]) -> TradingCalendar:
    return build_trading_calendar("SSE", _days(rows))


# --- descending input, ascending contract ----------------------------------------------


def test_a_descending_response_yields_an_ascending_trading_day_sequence() -> None:
    calendar = _calendar(SPRING_FESTIVAL_2024)

    assert calendar.trading_days == tuple(sorted(calendar.trading_days))
    assert calendar.trading_days[0] == date(2024, 2, 5)
    assert calendar.trading_days[-1] == date(2024, 2, 23)
    assert calendar.horizon.first_date == date(2024, 2, 5)
    assert calendar.horizon.last_date == date(2024, 2, 23)


# --- real holidays the weekday approximation gets wrong ---------------------------------


def test_spring_festival_2024_closes_six_weekdays_the_weekday_rule_calls_trading() -> None:
    """`scripts/generate_replay_corpus.py`'s `current.weekday() < 5` rule against reality."""
    calendar = _calendar(SPRING_FESTIVAL_2024)
    weekdays = tuple(
        day
        for day in calendar.trading_days_between(date(2024, 2, 5), date(2024, 2, 23))
        if day.weekday() < 5
    )
    approximated = tuple(
        _day(cal_date) for cal_date, _, _ in SPRING_FESTIVAL_2024 if _day(cal_date).weekday() < 5
    )

    disagreements = tuple(sorted(set(approximated) - set(weekdays)))

    assert disagreements == (
        date(2024, 2, 9),
        date(2024, 2, 12),
        date(2024, 2, 13),
        date(2024, 2, 14),
        date(2024, 2, 15),
        date(2024, 2, 16),
    )
    for closed_weekday in disagreements:
        assert closed_weekday.weekday() < 5
        assert calendar.is_trading_day(closed_weekday) is False
        assert calendar.day_status(closed_weekday) is CalendarDayStatus.closed


def test_national_day_2024_closes_five_weekdays_the_weekday_rule_calls_trading() -> None:
    calendar = _calendar(NATIONAL_DAY_2024)
    approximated = tuple(
        _day(cal_date) for cal_date, _, _ in NATIONAL_DAY_2024 if _day(cal_date).weekday() < 5
    )
    actual = set(calendar.trading_days)

    disagreements = tuple(sorted(set(approximated) - actual))

    assert disagreements == (
        date(2024, 10, 1),
        date(2024, 10, 2),
        date(2024, 10, 3),
        date(2024, 10, 4),
        date(2024, 10, 7),
    )
    assert calendar.is_trading_day(date(2024, 9, 30)) is True
    assert calendar.is_trading_day(date(2024, 10, 8)) is True


def test_the_weekday_rule_never_errs_the_other_way_on_this_corpus() -> None:
    """No weekend in Tushare's whole published history is a trading day (0 of 13,162 rows
    probed live), so the approximation's error is one-directional: it invents sessions, it
    never hides one. That is exactly why a boolean calendar built on it looks plausible."""
    for rows in (SPRING_FESTIVAL_2024, NATIONAL_DAY_2024, END_OF_2026):
        calendar = _calendar(rows)
        assert not [day for day in calendar.trading_days if day.weekday() >= 5]


# --- the three-valued verdict -----------------------------------------------------------


def test_a_date_beyond_the_published_horizon_is_neither_trading_nor_closed() -> None:
    calendar = _calendar(END_OF_2026)

    assert calendar.day_status(date(2026, 12, 31)) is CalendarDayStatus.trading
    assert calendar.day_status(date(2026, 12, 26)) is CalendarDayStatus.closed
    assert calendar.day_status(date(2027, 1, 4)) is CalendarDayStatus.beyond_horizon
    assert calendar.day_status(date(2027, 3, 1)) is CalendarDayStatus.beyond_horizon
    assert calendar.day_status(date(2026, 1, 5)) is CalendarDayStatus.beyond_horizon


def test_a_calendar_verdict_is_not_a_truth_value() -> None:
    """`if not calendar.day_status(day):` is the line that would quietly re-merge
    `closed` with `beyond_horizon`; every member refuses to be one."""
    for status in CalendarDayStatus:
        with pytest.raises(TradingCalendarError):
            bool(status)


def test_is_trading_day_raises_beyond_the_horizon_rather_than_answering_false() -> None:
    calendar = _calendar(END_OF_2026)

    with pytest.raises(CalendarHorizonError) as captured:
        calendar.is_trading_day(date(2027, 3, 1))

    message = str(captured.value)
    assert "2027-03-01" in message
    assert "2026-12-21" in message
    assert "2026-12-31" in message


def test_a_whole_unpublished_year_is_refused_instead_of_looking_like_one_long_holiday() -> None:
    calendar = _calendar(END_OF_2026)

    with pytest.raises(CalendarHorizonError):
        calendar.trading_days_between(date(2027, 1, 1), date(2027, 12, 31))


def test_a_range_that_only_partly_leaves_the_horizon_is_refused_whole() -> None:
    calendar = _calendar(END_OF_2026)

    with pytest.raises(CalendarHorizonError):
        calendar.trading_days_between(date(2026, 12, 21), date(2027, 1, 5))


def test_trading_days_between_is_inclusive_and_returns_only_open_sessions() -> None:
    calendar = _calendar(SPRING_FESTIVAL_2024)

    assert calendar.trading_days_between(date(2024, 2, 8), date(2024, 2, 19)) == (
        date(2024, 2, 8),
        date(2024, 2, 19),
    )
    assert calendar.trading_days_between(date(2024, 2, 12), date(2024, 2, 16)) == ()


def test_an_empty_result_inside_the_horizon_is_a_real_answer_not_a_blocked_one() -> None:
    """The counterpart of the test above: `()` may only ever mean "no sessions in this
    range", never "we do not know", which is why the unknown case raises instead."""
    calendar = _calendar(SPRING_FESTIVAL_2024)

    assert calendar.trading_days_between(date(2024, 2, 12), date(2024, 2, 16)) == ()
    assert calendar.day_status(date(2024, 2, 12)) is CalendarDayStatus.closed


# --- navigation -------------------------------------------------------------------------


def test_next_and_previous_trading_day_step_over_the_spring_festival_recess() -> None:
    calendar = _calendar(SPRING_FESTIVAL_2024)

    assert calendar.next_trading_day(date(2024, 2, 8)) == date(2024, 2, 19)
    assert calendar.next_trading_day(date(2024, 2, 13)) == date(2024, 2, 19)
    assert calendar.previous_trading_day(date(2024, 2, 19)) == date(2024, 2, 8)
    assert calendar.previous_trading_day(date(2024, 2, 13)) == date(2024, 2, 8)


def test_previous_trading_day_agrees_with_tushares_own_pretrade_date_column() -> None:
    """`pretrade_date` is stored but never trusted as the answer; it is kept as an
    independent witness. A live probe found 0 disagreements across all 13,162 published SSE
    rows, and this test re-proves it wherever both are inside the fixture's horizon."""
    for rows in (SPRING_FESTIVAL_2024, NATIONAL_DAY_2024, END_OF_2026):
        calendar = _calendar(rows)
        compared = 0
        for cal_date, _, pretrade in rows:
            day = _day(cal_date)
            witness = _day(pretrade)
            if witness < calendar.horizon.first_date:
                continue
            assert calendar.previous_trading_day(day) == witness
            compared += 1
        assert compared > 0


def test_next_trading_day_beyond_the_horizon_is_refused() -> None:
    calendar = _calendar(END_OF_2026)

    with pytest.raises(CalendarHorizonError):
        calendar.next_trading_day(date(2026, 12, 31))


def test_previous_trading_day_before_the_horizon_is_refused() -> None:
    calendar = _calendar(SPRING_FESTIVAL_2024)

    with pytest.raises(CalendarHorizonError):
        calendar.previous_trading_day(date(2024, 2, 5))


def test_shift_counts_trading_days_not_calendar_days() -> None:
    calendar = _calendar(SPRING_FESTIVAL_2024)

    assert calendar.shift(date(2024, 2, 8), 1) == date(2024, 2, 19)
    assert calendar.shift(date(2024, 2, 8), 2) == date(2024, 2, 20)
    assert calendar.shift(date(2024, 2, 8), 0) == date(2024, 2, 8)
    assert calendar.shift(date(2024, 2, 20), -2) == date(2024, 2, 8)


def test_shift_requires_a_trading_day_anchor() -> None:
    calendar = _calendar(SPRING_FESTIVAL_2024)

    with pytest.raises(TradingCalendarError):
        calendar.shift(date(2024, 2, 12), 1)


def test_shift_past_the_end_of_the_horizon_is_refused() -> None:
    calendar = _calendar(SPRING_FESTIVAL_2024)

    with pytest.raises(CalendarHorizonError):
        calendar.shift(date(2024, 2, 23), 1)


# --- construction is fail-closed ---------------------------------------------------------


def test_a_hole_in_the_supplied_rows_is_refused_rather_than_read_as_a_recess() -> None:
    """The same failure as an unpublished year, one range narrower: rows for January and
    March with February missing would otherwise present February as a two-month holiday."""
    rows = tuple(row for row in SPRING_FESTIVAL_2024 if row[0] != "20240212")

    with pytest.raises(TradingCalendarError) as captured:
        _calendar(rows)

    assert "2024-02-12" in str(captured.value)


def test_duplicate_calendar_dates_are_refused() -> None:
    rows = (*SPRING_FESTIVAL_2024, ("20240212", 1, "20240208"))

    with pytest.raises(TradingCalendarError):
        _calendar(rows)


def test_a_datetime_masquerading_as_a_calendar_date_is_refused() -> None:
    """`datetime` subclasses `date`, so one slipped in here would silently never equal any
    real calendar date -- the same trap `PanelStore._validated_requirement` guards."""
    days = (
        CalendarDay(
            calendar_date=datetime(2024, 2, 5),  # a datetime is the point of the test
            is_trading=True,
            previous_trading_date=None,
        ),
    )

    with pytest.raises(TradingCalendarError):
        build_trading_calendar("SSE", days)


def test_an_is_trading_flag_that_is_not_a_bool_is_refused() -> None:
    days = (
        CalendarDay(calendar_date=date(2024, 2, 5), is_trading=1, previous_trading_date=None),  # type: ignore[arg-type]
    )

    with pytest.raises(TradingCalendarError):
        build_trading_calendar("SSE", days)


def test_a_calendar_with_no_days_at_all_cannot_be_built() -> None:
    with pytest.raises(TradingCalendarError):
        build_trading_calendar("SSE", ())


def test_a_calendar_with_no_open_session_cannot_be_built() -> None:
    """A window of pure holiday is real data, but a calendar that can answer no navigation
    question is not a calendar; refusing it keeps `shift`/`next`/`previous` total."""
    days = tuple(
        CalendarDay(calendar_date=_day(cal_date), is_trading=False, previous_trading_date=None)
        for cal_date, _, _ in SPRING_FESTIVAL_2024
    )

    with pytest.raises(TradingCalendarError):
        build_trading_calendar("SSE", days)


def test_an_empty_exchange_name_is_refused() -> None:
    with pytest.raises(TradingCalendarError):
        build_trading_calendar("", _days(SPRING_FESTIVAL_2024))


def test_the_exchange_the_calendar_was_built_for_is_carried_on_it() -> None:
    assert _calendar(SPRING_FESTIVAL_2024).exchange == "SSE"


def test_the_horizon_answers_what_is_known_without_going_through_a_day_query() -> None:
    calendar = _calendar(END_OF_2026)

    assert calendar.horizon.first_date == date(2026, 12, 21)
    assert calendar.horizon.last_date == date(2026, 12, 31)
    assert calendar.horizon.covers(date(2026, 12, 31)) is True
    assert calendar.horizon.covers(date(2027, 1, 1)) is False
    assert calendar.trading_day_count == 9


# --- the availability rule's known look-ahead ---------------------------------------------


def test_the_known_lookahead_registry_carries_its_measured_widths() -> None:
    """The rule in `providers/tushare.py` dates every row available on 1 January of its own
    year. These are the dates where that is provably early, with the size of the error."""
    widths = {
        (entry.calendar_date, entry.announced_on): entry.lookahead_days
        for entry in KNOWN_CALENDAR_LOOKAHEAD
    }

    assert widths == {
        (date(2015, 9, 3), date(2015, 5, 13)): 132,
        (date(2015, 9, 4), date(2015, 5, 13)): 132,
        (date(2020, 1, 31), date(2020, 1, 27)): 26,
    }
    for entry in KNOWN_CALENDAR_LOOKAHEAD:
        assert entry.claimed_available_from == date(entry.calendar_date.year, 1, 1)
        assert entry.claimed_available_from < entry.announced_on < entry.calendar_date


def test_a_calendar_reports_only_the_known_lookahead_its_horizon_actually_covers() -> None:
    """`V2-P1-013` asks a loaded calendar, not the module. An empty answer means *no known
    instance in this window* -- never "this window is clean"; no code here can enumerate
    every mid-year amendment, because `trade_cal` serves no revision history."""
    assert _calendar(SPRING_FESTIVAL_2024).known_lookahead() == ()
    assert _calendar(END_OF_2026).known_lookahead() == ()

    covering = _calendar(PARADE_RECESS_2015)

    assert tuple(entry.calendar_date for entry in covering.known_lookahead()) == (
        date(2015, 9, 3),
        date(2015, 9, 4),
    )
    assert all(entry.lookahead_days == 132 for entry in covering.known_lookahead())


# --- rebuilding from stored panel rows ----------------------------------------------------


def test_stored_rows_rebuild_the_same_calendar_the_days_did() -> None:
    rows = [
        [_day(cal_date).isoformat(), bool(is_open), _day(pretrade).isoformat()]
        for cal_date, is_open, pretrade in SPRING_FESTIVAL_2024
    ]

    calendar = trading_calendar_from_panel_rows("SSE", rows)

    assert calendar.trading_days == _calendar(SPRING_FESTIVAL_2024).trading_days


def test_a_stored_open_flag_that_is_not_a_bool_is_refused_on_read_back() -> None:
    """The string `"0"` is truthy. A storage backend without a boolean type, or a
    hand-written partition, would turn every holiday into a session without this."""
    rows = [["2024-02-12", "0", "2024-02-08"], ["2024-02-08", "1", "2024-02-07"]]

    with pytest.raises(TradingCalendarError) as captured:
        trading_calendar_from_panel_rows("SSE", rows)

    assert "truthy stand-in" in str(captured.value)


def test_a_stored_row_of_the_wrong_width_is_refused() -> None:
    with pytest.raises(TradingCalendarError) as captured:
        trading_calendar_from_panel_rows("SSE", [["2024-02-12", False]])

    assert "expected 3" in str(captured.value)


def test_a_stored_calendar_date_that_is_not_a_string_is_refused() -> None:
    with pytest.raises(TradingCalendarError):
        trading_calendar_from_panel_rows("SSE", [[20240212, False, None]])


def test_a_stored_calendar_date_that_is_not_an_iso_date_is_refused() -> None:
    with pytest.raises(TradingCalendarError) as captured:
        trading_calendar_from_panel_rows("SSE", [["12/02/2024", False, None]])

    assert "not an ISO date" in str(captured.value)


def test_a_stored_pretrade_date_that_is_not_an_iso_date_is_refused() -> None:
    with pytest.raises(TradingCalendarError) as captured:
        trading_calendar_from_panel_rows("SSE", [["2024-02-12", True, "not-a-date"]])

    assert "pretrade_date" in str(captured.value)


# --- malformed queries ---------------------------------------------------------------------


def test_a_reversed_range_is_refused_rather_than_answered_with_nothing() -> None:
    calendar = _calendar(SPRING_FESTIVAL_2024)

    with pytest.raises(TradingCalendarError) as captured:
        calendar.trading_days_between(date(2024, 2, 19), date(2024, 2, 8))

    assert "is after end" in str(captured.value)


def test_navigating_from_a_date_outside_the_horizon_is_refused() -> None:
    calendar = _calendar(END_OF_2026)

    for query in (
        lambda: calendar.next_trading_day(date(2027, 1, 4)),
        lambda: calendar.previous_trading_day(date(2027, 1, 4)),
        lambda: calendar.shift(date(2027, 1, 4), 1),
    ):
        with pytest.raises(CalendarHorizonError):
            query()
