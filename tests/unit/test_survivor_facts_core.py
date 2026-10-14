"""The pure core of the panel-to-facts adapter (`V2-P7-016`, plan 2 task A1).

Three functions and one carrier, none of which reads a panel: the ST three-state, the listing
age in open sessions, and the assembly of one `SecurityFacts` row. Every expectation below is
worked out by hand in the test's own comment, so a wrong implementation cannot make its own
answer the expectation. Name histories are built with the repository's validating
`build_name_history`, and the calendar is the real `TradingCalendar`, not a stand-in.
"""

import random
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.name_history import (
    NameHistory,
    NameHistoryError,
    NameRecord,
    build_name_history,
)
from openalpha_cn.domain.survivor_book import SecurityFacts
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.survivor_facts import (
    MAIN_BOARD_PREFIXES,
    FactsDay,
    FactsNotes,
    HeldStatus,
    ListingAgeHorizonError,
    SurvivorFactsError,
    assemble_fact,
    listing_age_sessions,
    name_status,
    name_status_reason,
)

# --- name_status -------------------------------------------------------------------------

CODE = "600518.SH"
DAY = date(2021, 6, 15)  # a Tuesday


def history(*rows: tuple[str, date, date]) -> NameHistory:
    """A real validated history from `(name, effective_from, announced_on)` rows."""
    return build_name_history(
        CODE,
        [
            NameRecord(
                ts_code=CODE,
                name=name,
                effective_from=effective,
                announced_on=announced,
                change_reason="其他",
            )
            for name, effective, announced in rows
        ],
    )


def named(name: str) -> NameHistory:
    """An ordinary baseline name followed by `name`, in effect for the whole of May 2021."""
    return history(
        ("康美药业", date(2013, 1, 4), date(2013, 1, 4)),
        (name, date(2021, 5, 6), date(2021, 5, 5)),
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("康美药业", "ordinary"),
        ("ST康美", "st"),
        ("*ST康美", "st"),
        ("PT水仙", "st"),
        ("SST华新", "st"),  # a share-reform marker in front of the ST marker is still ST
        ("国华退", "delisting"),  # delisting-period name: ends with 退
        ("退市海航", "delisting"),  # delisting-period name: begins with 退市
    ],
)
def test_the_name_in_effect_decides_when_the_history_can_judge(name: str, expected: str) -> None:
    # The record in effect on 2021-06-15 is the one effective 2021-05-06, announced the day
    # before: both clocks are at or before the day, so there is nothing pending and no gap.
    assert name_status(history=named(name), band_is_five_percent=False, day=DAY) == expected


def test_a_rename_announced_for_the_next_session_already_counts_as_st() -> None:
    # 2021-06-14 (Mon) after the close: "ST康美" is announced, effective Tue 2021-06-15. The
    # signal day is the Monday, so the record is pending: announced_on <= day < effective_from.
    pending = history(
        ("康美药业", date(2013, 1, 4), date(2013, 1, 4)),
        ("ST康美", date(2021, 6, 15), date(2021, 6, 14)),
    )
    assert name_status(history=pending, band_is_five_percent=False, day=date(2021, 6, 14)) == "st"
    # On the Tuesday it is in effect, not pending, and the answer is the same.
    assert name_status(history=pending, band_is_five_percent=False, day=date(2021, 6, 15)) == "st"


def test_an_announcement_that_has_not_been_made_yet_is_invisible_even_in_the_full_corpus() -> None:
    # The same history on Friday 2021-06-11, before the 2021-06-14 announcement. The function
    # is handed the WHOLE history and must apply the announcement clock itself: a reader on
    # the 11th could not have known, so the answer is the ordinary baseline name.
    pending = history(
        ("康美药业", date(2013, 1, 4), date(2013, 1, 4)),
        ("ST康美", date(2021, 6, 15), date(2021, 6, 14)),
    )
    assert (
        name_status(history=pending, band_is_five_percent=False, day=date(2021, 6, 11))
        == "ordinary"
    )


def test_a_rename_announced_after_it_took_effect_is_not_visible_before_its_announcement() -> None:
    # The contract allows a late announcement (effective 2021-06-10, announced 2021-06-14).
    # On 2021-06-11 the record is "in effect" on the effective clock but was not yet public, so
    # an observer on the 11th reads the baseline; on the 14th the announcement lands.
    late = history(
        ("康美药业", date(2013, 1, 4), date(2013, 1, 4)),
        ("ST康美", date(2021, 6, 10), date(2021, 6, 14)),
    )
    assert (
        name_status(history=late, band_is_five_percent=False, day=date(2021, 6, 11)) == "ordinary"
    )
    assert name_status(history=late, band_is_five_percent=False, day=date(2021, 6, 14)) == "st"


@pytest.mark.parametrize(
    ("band", "expected"),
    [(True, "st"), (False, "unknown"), (None, "unknown")],
)
def test_a_security_with_no_name_history_falls_back_to_the_main_board_band(
    band: bool | None, expected: str
) -> None:
    # The history is `None` when the corpus has no entry for the security at all (the
    # 600182.SH case: its first row arrived in 2021 while its band was 5% for years).
    assert name_status(history=None, band_is_five_percent=band, day=DAY) == expected


@pytest.mark.parametrize(
    ("band", "expected"),
    [(True, "st"), (False, "unknown"), (None, "unknown")],
)
def test_a_name_record_only_effective_after_the_day_falls_back_to_the_band(
    band: bool | None, expected: str
) -> None:
    # `before_first` (T3 2.4(ii)): 000519.SZ shaped. The only record is an ordinary rename
    # announced 2016-12-16 and effective 2017-01-10; on 2017-01-03 the history has no record in
    # effect, so `risk_warning_on` raises a horizon error that must become a verdict, not a
    # failed run. The pending record is ordinary, so it does not answer either.
    gap = history(("中兵红箭", date(2017, 1, 10), date(2016, 12, 16)))
    assert name_status(history=gap, band_is_five_percent=band, day=date(2017, 1, 3)) == expected


@pytest.mark.parametrize(
    ("band", "expected"),
    [(True, "st"), (None, "unknown")],
)
def test_a_history_with_nothing_announced_yet_falls_back_to_the_band(
    band: bool | None, expected: str
) -> None:
    # The only record is announced 2021-07-01, after the 2021-06-15 day: `announced_by` has
    # nothing left to answer with and raises a horizon error, which is again a verdict.
    later = history(("中兵红箭", date(2021, 7, 2), date(2021, 7, 1)))
    assert name_status(history=later, band_is_five_percent=band, day=DAY) == expected


def test_an_st_rename_pending_before_the_first_effective_record_is_st_without_any_band() -> None:
    # Same `before_first` shape, but the pending record IS an ST name: the announcement is
    # public on 2017-01-03, so the security is ST by name even with no record in effect and
    # no band to witness it.
    gap = history(("ST中兵", date(2017, 1, 10), date(2016, 12, 16)))
    assert name_status(history=gap, band_is_five_percent=False, day=date(2017, 1, 3)) == "st"
    assert name_status(history=gap, band_is_five_percent=None, day=date(2017, 1, 3)) == "st"


@pytest.mark.parametrize("band", [None, False])
def test_a_name_that_reads_ordinary_stays_ordinary_unless_the_band_says_otherwise(
    band: bool | None,
) -> None:
    assert name_status(history=named("康美药业"), band_is_five_percent=band, day=DAY) == "ordinary"


def test_a_five_percent_band_makes_an_ordinary_named_security_st() -> None:
    # The union rule (T3 section 7, constraint 3): the exchange's own band is a witness that
    # does not depend on the name corpus being right.
    assert name_status(history=named("康美药业"), band_is_five_percent=True, day=DAY) == "st"


def test_a_delisting_period_name_is_not_overwritten_by_the_band() -> None:
    # Delisting is its own state, not ST; the band can only add `st` to an ordinary name.
    assert name_status(history=named("国华退"), band_is_five_percent=True, day=DAY) == "delisting"


def test_an_st_name_stays_st_whatever_the_band_says() -> None:
    for band in (True, False, None):
        assert name_status(history=named("*ST康美"), band_is_five_percent=band, day=DAY) == "st"


def test_an_st_that_has_ended_reads_ordinary_once_the_lifting_is_in_force() -> None:
    # 摘帽. 康美药业 (2013) -> *ST康美 (effective 2019-03-01, announced 2019-02-28) -> 康美药业
    # (effective 2021-05-06, announced 2021-05-05). On 2021-06-15 the record in force is the
    # 2021-05-06 one, an ordinary name, and nothing is pending: the security WAS special-treated
    # and is not any more. A rule that read "ever ST" would answer `st` here.
    lifted = history(
        ("康美药业", date(2013, 1, 4), date(2013, 1, 4)),
        ("*ST康美", date(2019, 3, 1), date(2019, 2, 28)),
        ("康美药业", date(2021, 5, 6), date(2021, 5, 5)),
    )
    for band in (False, None):
        assert name_status(history=lifted, band_is_five_percent=band, day=DAY) == "ordinary"
    # While the *ST name was in force it was `st`: 2020-06-15 lies in 2019-03-01..2021-05-05.
    assert name_status(history=lifted, band_is_five_percent=None, day=date(2020, 6, 15)) == "st"


def test_an_announced_lifting_does_not_end_the_st_before_it_takes_effect() -> None:
    # The pending twin. The lifting is announced Tue 2021-06-15 and effective Wed 2021-06-16. On
    # the 15th *ST康美 is still in force and the pending record is an ordinary name, which counts
    # for nothing early, so the answer is `st`; on the 16th the lifting is in force: `ordinary`.
    lifting = history(
        ("康美药业", date(2013, 1, 4), date(2013, 1, 4)),
        ("*ST康美", date(2019, 3, 1), date(2019, 2, 28)),
        ("康美药业", date(2021, 6, 16), date(2021, 6, 15)),
    )
    for band in (False, None):
        assert name_status(history=lifting, band_is_five_percent=band, day=DAY) == "st"
        assert (
            name_status(history=lifting, band_is_five_percent=band, day=date(2021, 6, 16))
            == "ordinary"
        )


def test_a_pending_delisting_period_rename_stays_ordinary_until_it_takes_effect() -> None:
    # A deliberate choice, pinned here so changing it is a decision and not an accident. Only an
    # ST name counts while pending, because T3 2.4(iv) measured that 99.2% of ST names are
    # announced a session or more ahead. A delisting-period name has no such measurement behind
    # it, so it is read on the effective clock alone: announced Tue 2021-06-15 and effective
    # Wed 2021-06-16, the 15th is still `ordinary` and the 16th is `delisting`.
    pending = history(
        ("国华网安", date(2013, 1, 4), date(2013, 1, 4)),
        ("国华退", date(2021, 6, 16), date(2021, 6, 15)),
    )
    for band in (False, None):
        assert name_status(history=pending, band_is_five_percent=band, day=DAY) == "ordinary"
    assert (
        name_status(history=pending, band_is_five_percent=False, day=date(2021, 6, 16))
        == "delisting"
    )


def test_a_history_served_newest_first_reads_the_same_as_one_served_oldest_first() -> None:
    # The vendor serves `namechange` newest first. Five records whose order matters -- two ST
    # spells, a lifting between them and a rename pending on the signal day -- read on seven
    # days, each answer worked out by hand from the records:
    #
    #   2013-01-03  nothing announced yet (first announcement 2013-01-04)  -> unknown
    #   2018-06-01  康美药业 in force                                     -> ordinary
    #   2019-06-01  ST康美 in force (from 2019-03-01)                     -> st
    #   2020-06-01  *ST康美 in force (from 2020-04-01)                    -> st
    #   2021-05-31  康美药业 in force again (from 2021-05-06)             -> ordinary
    #   2021-06-15  康美药业 in force, ST康美 announced that day, pending -> st
    #   2021-06-16  ST康美 in force                                       -> st
    rows = (
        ("康美药业", date(2013, 1, 4), date(2013, 1, 4)),
        ("ST康美", date(2019, 3, 1), date(2019, 2, 28)),
        ("*ST康美", date(2020, 4, 1), date(2020, 3, 31)),
        ("康美药业", date(2021, 5, 6), date(2021, 5, 5)),
        ("ST康美", date(2021, 6, 16), date(2021, 6, 15)),
    )
    oldest_first = history(*rows)
    newest_first = history(*reversed(rows))
    expected = {
        date(2013, 1, 3): "unknown",
        date(2018, 6, 1): "ordinary",
        date(2019, 6, 1): "st",
        date(2020, 6, 1): "st",
        date(2021, 5, 31): "ordinary",
        date(2021, 6, 15): "st",
        date(2021, 6, 16): "st",
    }

    for day, answer in expected.items():
        assert name_status(history=oldest_first, band_is_five_percent=False, day=day) == answer
        assert name_status(history=newest_first, band_is_five_percent=False, day=day) == answer


PENDING_ST = history(
    ("康美药业", date(2013, 1, 4), date(2013, 1, 4)),
    ("ST康美", date(2021, 6, 15), date(2021, 6, 14)),
)
"""Announced Mon 2021-06-14 after the close, in force Tue 2021-06-15."""

BEFORE_FIRST = history(("中兵红箭", date(2017, 1, 10), date(2016, 12, 16)))
"""T3 2.4(ii)'s shape: the only record is announced before 2017-01-03 and in force after it."""

NOTHING_ANNOUNCED_YET = history(("中兵红箭", date(2021, 7, 2), date(2021, 7, 1)))
"""The only record is announced after `DAY`: as of `DAY` the corpus holds no rename of it."""


@pytest.mark.parametrize(
    ("given", "band", "day", "reason", "status"),
    [
        # The name decides first, so an ST name is `st_by_name` whatever the band says.
        (named("*ST康美"), False, DAY, "st_by_name", "st"),
        (named("*ST康美"), True, DAY, "st_by_name", "st"),
        # Announced the day before it takes effect: ST only because the rename is pending.
        (PENDING_ST, False, date(2021, 6, 14), "st_pending", "st"),
        (PENDING_ST, True, date(2021, 6, 14), "st_pending", "st"),
        # The name says ordinary, or nothing, and the main-board band says 5%.
        (named("康美药业"), True, DAY, "st_by_band", "st"),
        (None, True, DAY, "st_by_band", "st"),
        (BEFORE_FIRST, True, date(2017, 1, 3), "st_by_band", "st"),
        (named("国华退"), True, DAY, "delisting", "delisting"),
        (named("康美药业"), False, DAY, "ordinary", "ordinary"),
        (named("康美药业"), None, DAY, "ordinary", "ordinary"),
        # Never renamed since the corpus began: no entry at all, or none announced by the day.
        (None, None, DAY, "unknown_no_record", "unknown"),
        (None, False, DAY, "unknown_no_record", "unknown"),
        (NOTHING_ANNOUNCED_YET, None, DAY, "unknown_no_record", "unknown"),
        # A genuine gap: renames are on file, none of them is in force yet on the day.
        (BEFORE_FIRST, False, date(2017, 1, 3), "unknown_before_first", "unknown"),
        (BEFORE_FIRST, None, date(2017, 1, 3), "unknown_before_first", "unknown"),
    ],
    ids=[
        "st-name",
        "st-name-and-band",
        "st-pending",
        "st-pending-and-band",
        "band-over-ordinary",
        "band-over-no-history",
        "band-over-a-gap",
        "delisting-over-band",
        "ordinary",
        "ordinary-no-band",
        "no-history",
        "no-history-ten-percent",
        "nothing-announced-yet",
        "before-first",
        "before-first-no-band",
    ],
)
def test_every_status_comes_with_the_one_reason_it_was_reached_by(
    given: NameHistory | None, band: bool | None, day: date, reason: str, status: str
) -> None:
    assert name_status_reason(history=given, band_is_five_percent=band, day=day) == reason
    assert name_status(history=given, band_is_five_percent=band, day=day) == status


def test_a_malformed_call_is_still_an_error_and_not_an_unknown() -> None:
    # Only the two "the corpus does not reach that day" horizon errors become a verdict. A
    # `datetime` is a bug in the caller (it never equals a date), and swallowing it as
    # `unknown` would hide it for every security of every run.
    with pytest.raises(NameHistoryError):
        name_status(
            history=named("康美药业"),
            band_is_five_percent=False,
            day=datetime(2021, 6, 15, 15, 0),  # the point is the wrong type
        )


# --- listing_age_sessions ----------------------------------------------------------------

CALENDAR_START = date(2020, 1, 1)  # a Wednesday
CALENDAR_END = date(2021, 12, 31)


def weekday_calendar() -> TradingCalendar:
    """The real contract, over a synthetic exchange that is open every weekday of 2020-2021."""
    days = []
    day = CALENDAR_START
    while day <= CALENDAR_END:
        days.append(CalendarDay(calendar_date=day, is_trading=day.weekday() < 5))
        day += timedelta(days=1)
    return build_trading_calendar("SSE", days)


CALENDAR = weekday_calendar()


def sessions_between(first: date, last: date) -> int:
    return len(CALENDAR.trading_days_between(first, last))


def never_called(first: date, last: date) -> int:
    raise AssertionError(f"sessions_between({first}, {last}) should not have been called")


MIN_LISTING_SESSIONS = 250
"""`SurvivorBookConfig.min_listing_sessions`'s default, which the adapter passes through."""


def age(
    listed_on: date | None,
    day: date,
    *,
    between: Callable[[date, date], int] = sessions_between,
    lookback: int = MIN_LISTING_SESSIONS,
) -> int | None:
    return listing_age_sessions(
        listed_on=listed_on,
        calendar_first=CALENDAR.horizon.first_date,
        day=day,
        sessions_between=between,
        required_lookback=lookback,
    )


def test_the_listing_day_is_the_first_session() -> None:
    # Listed Monday 2020-03-02: that day alone is [03-02, 03-02] = 1 session.
    assert age(date(2020, 3, 2), date(2020, 3, 2)) == 1
    assert age(date(2020, 3, 2), date(2020, 3, 3)) == 2


def test_a_weekend_is_not_a_session() -> None:
    # Listed Friday 2020-03-06, read on Monday 2020-03-09: Fri + Mon = 2; Sat and Sun are shut.
    assert age(date(2020, 3, 6), date(2020, 3, 9)) == 2


def test_the_250th_session_is_250_and_the_one_before_is_249() -> None:
    # 250 weekdays is exactly 50 weeks. Listed Monday 2020-03-02, the 50th week ends on Friday
    # 2020-03-02 + 49 weeks + 4 days = 2021-02-12 (2020-03-02 + 365 days = 2021-03-02, less 18).
    assert age(date(2020, 3, 2), date(2021, 2, 12)) == 250
    assert age(date(2020, 3, 2), date(2021, 2, 11)) == 249


def test_a_security_with_no_listing_row_has_no_age_and_the_calendar_is_not_asked() -> None:
    assert age(None, DAY, between=never_called) is None


def test_a_security_not_yet_listed_on_the_day_has_zero_sessions() -> None:
    assert age(date(2021, 7, 1), date(2021, 6, 30), between=never_called) == 0


def test_a_listing_before_the_calendar_starts_is_counted_from_the_calendar_start() -> None:
    # 2020-01-01..2021-01-15 holds 262 weekdays in 2020 (366 days = 52 weeks + Wed + Thu, so
    # 52*5 + 2) plus Fri 2021-01-01 and the two full weeks 04..08 and 11..15 = 11, so 273.
    # 273 >= 250, so the lower bound already settles the verdict and is returned as the age.
    assert age(date(2010, 1, 4), date(2021, 1, 15)) == 273


def test_a_truncated_count_exactly_at_the_threshold_is_accepted() -> None:
    # 250 weekdays from Wed 2020-01-01 end on Tue 2020-12-15 (day 350 of the leap year; Dec 1
    # is day 336 = 48 weeks = 240 weekdays, +Dec 2-4 = 243, +Dec 7-11 = 248, +Dec 14-15 = 250).
    assert age(date(2010, 1, 4), date(2020, 12, 15), lookback=250) == 250


def test_the_lookback_a_truncated_count_needs_is_the_callers_minimum_listing_age() -> None:
    # Controller decision 3: the bound is `config.min_listing_sessions`, passed in, not a module
    # constant. 100 weekdays from Wed 2020-01-01 is exactly 20 weeks, ending Tue 2020-05-19
    # (2020-01-01 + 140 days = day 141 of a leap year = Wed 2020-05-20, so the 20th week's last
    # weekday is the Tuesday before it).
    listed_2010 = date(2010, 1, 4)
    assert age(listed_2010, date(2020, 5, 19), lookback=100) == 100
    with pytest.raises(ListingAgeHorizonError, match=r"only 100 sessions.*101"):
        age(listed_2010, date(2020, 5, 19), lookback=101)
    # The same day under the default minimum is refused: 100 sessions cannot show 250.
    with pytest.raises(ListingAgeHorizonError, match=r"only 100 sessions.*250"):
        age(listed_2010, date(2020, 5, 19))
    # And 273 sessions (2021-01-15, see above) no longer suffice once the minimum is 274.
    with pytest.raises(ListingAgeHorizonError, match=r"only 273 sessions.*274"):
        age(listed_2010, date(2021, 1, 15), lookback=274)
    assert age(listed_2010, date(2021, 1, 15), lookback=273) == 273


def test_the_lookback_has_no_default_so_a_caller_cannot_forget_its_minimum() -> None:
    with pytest.raises(TypeError, match="required_lookback"):
        listing_age_sessions(  # type: ignore[call-arg]
            listed_on=date(2010, 1, 4),
            calendar_first=CALENDAR.horizon.first_date,
            day=date(2021, 1, 15),
            sessions_between=sessions_between,
        )


def test_a_truncated_count_below_the_threshold_is_refused_rather_than_understated() -> None:
    # One session earlier (Mon 2020-12-14) the calendar offers only 249: a security listed in
    # 2010 would be reported as 249 sessions old, i.e. "too recent", which is simply wrong.
    with pytest.raises(ListingAgeHorizonError, match="249"):
        age(date(2010, 1, 4), date(2020, 12, 14))


def test_a_day_before_the_calendar_starts_is_refused_for_a_truncated_listing() -> None:
    with pytest.raises(ListingAgeHorizonError):
        age(date(2010, 1, 4), date(2019, 12, 31), between=never_called)


def test_a_listing_on_the_calendar_start_is_exact_and_needs_no_threshold() -> None:
    # Not truncated: listed_on == calendar_first, so a short count is the true age of a young
    # security, not a calendar artefact. Jan 1, 2, 3, 6, 7, 8, 9, 10 = 8 sessions.
    assert age(date(2020, 1, 1), date(2020, 1, 10)) == 8


def test_the_horizon_error_is_a_named_domain_error() -> None:
    assert issubclass(ListingAgeHorizonError, SurvivorFactsError)
    assert issubclass(SurvivorFactsError, ValueError)


# --- assemble_fact -----------------------------------------------------------------------


def assemble(**overrides: object) -> SecurityFacts:
    arguments: dict[str, object] = {
        "ts_code": "600519.SH",
        "board": "main",
        "name_status": "ordinary",
        "listed_sessions": 1200,
        "suspended": False,
        "close": Decimal("1680.50"),
        "avg_amount": Decimal("3500000.25"),
        "oriented_values": (0.5, -1.25, 0.0, 2.0),
    }
    arguments.update(overrides)
    return assemble_fact(**arguments)  # type: ignore[arg-type]


def test_every_field_maps_through_unchanged() -> None:
    fact = assemble()

    assert fact == SecurityFacts(
        ts_code="600519.SH",
        board="main",
        listed_sessions=1200,
        special_treatment=False,
        suspended=False,
        close=Decimal("1680.50"),
        avg_amount=Decimal("3500000.25"),
        oriented_values=(0.5, -1.25, 0.0, 2.0),
    )
    # The raw close keeps its trailing zero: the Decimal is not re-rounded on the way through.
    assert str(fact.close) == "1680.50"


@pytest.mark.parametrize(
    ("status", "special"),
    [("ordinary", False), ("unknown", False), ("st", True), ("delisting", True)],
)
def test_the_name_status_decides_special_treatment(status: str, special: bool) -> None:
    # `unknown` is treated as ordinary (T3 constraint 2); `st` and `delisting` both block.
    assert assemble(name_status=status).special_treatment is special


def test_an_unrecognised_name_status_is_refused_rather_than_read_as_ordinary() -> None:
    with pytest.raises(SurvivorFactsError, match="bogus"):
        assemble(name_status="bogus")


def test_no_listing_row_is_zero_sessions_so_the_screen_calls_it_too_recent() -> None:
    assert assemble(listed_sessions=None).listed_sessions == 0


def test_a_known_listing_age_is_kept_exactly() -> None:
    assert assemble(listed_sessions=250).listed_sessions == 250


def test_the_suspended_flag_and_board_are_carried() -> None:
    fact = assemble(suspended=True, board="growth", ts_code="300001.SZ")

    assert fact.suspended is True
    assert fact.board == "growth"
    assert fact.ts_code == "300001.SZ"


def test_missing_factor_values_keep_their_place_in_the_tuple() -> None:
    fact = assemble(oriented_values=[None, 1.5, None, -0.5])

    assert fact.oriented_values == (None, 1.5, None, -0.5)
    assert isinstance(fact.oriented_values, tuple)


@pytest.mark.parametrize(
    "bad",
    [
        {"close": Decimal("0")},
        {"close": Decimal("-1")},
        {"avg_amount": Decimal("-0.01")},
        {"listed_sessions": -1},
        {"oriented_values": (float("nan"), 1.0)},
        {"oriented_values": (float("inf"), 1.0)},
        {"board": "gem"},
        {"ts_code": ""},
    ],
    ids=[
        "zero-close",
        "negative-close",
        "negative-amount",
        "negative-age",
        "nan-factor",
        "inf-factor",
        "unknown-board",
        "empty-code",
    ],
)
def test_input_the_kernel_contract_rejects_is_rejected_here(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        assemble(**bad)


def test_the_same_input_gives_the_same_bytes() -> None:
    first = assemble().model_dump_json()
    second = assemble().model_dump_json()

    assert first == second


def test_the_order_facts_are_assembled_in_does_not_change_the_set() -> None:
    rows = [
        {
            "ts_code": f"6000{position:02d}.SH",
            "name_status": ("ordinary", "st", "unknown", "delisting")[position % 4],
            "listed_sessions": None if position % 5 == 0 else 100 + position,
            "suspended": position % 3 == 0,
            "close": Decimal(10 + position),
            "avg_amount": Decimal(1000 * position),
            "oriented_values": (position / 10, None if position % 2 else 0.5),
        }
        for position in range(1, 13)
    ]
    shuffled = list(rows)
    random.Random(20260930).shuffle(shuffled)
    assert shuffled != rows  # the permutation is real

    forward = {assemble(**row) for row in rows}
    scrambled = {assemble(**row) for row in shuffled}

    assert forward == scrambled
    assert len(forward) == 12
    assert sorted(fact.ts_code for fact in forward) == [row["ts_code"] for row in rows]


# --- FactsNotes --------------------------------------------------------------------------


def notes(**overrides: object) -> FactsNotes:
    """A consistent set of counts: st 123 = 120 + 1 + 2, unknown 3 = 2 + 1, and one code left
    out for its board."""
    counts: dict[str, object] = {
        "name_unknown_assumed_ordinary": 3,
        "suspended_without_bar": 47,
        "no_listing_row": 2,
        "delisting": 1,
        "st": 123,
        "st_by_name": 120,
        "st_pending": 1,
        "st_by_band": 2,
        "name_unknown_no_record": 2,
        "name_unknown_before_first": 1,
        "no_bar_within_carry": 4,
        "not_listed_with_bar": 5,
        "unrecognised_board": 1,
        "unrecognised_board_codes": ("303001.SZ",),
    }
    counts.update(overrides)
    return FactsNotes(**counts)  # type: ignore[arg-type]


def test_the_notes_are_a_frozen_set_of_counts() -> None:
    held = notes()

    assert (
        held.name_unknown_assumed_ordinary,
        held.suspended_without_bar,
        held.no_listing_row,
        held.delisting,
        held.st,
        held.st_by_name,
        held.st_pending,
        held.st_by_band,
        held.name_unknown_no_record,
        held.name_unknown_before_first,
        held.no_bar_within_carry,
        held.not_listed_with_bar,
        held.unrecognised_board,
        held.unrecognised_board_codes,
    ) == (3, 47, 2, 1, 123, 120, 1, 2, 2, 1, 4, 5, 1, ("303001.SZ",))
    with pytest.raises(FrozenInstanceError):
        held.st = 0  # type: ignore[misc]
    assert held == notes()


def test_the_tally_counts_each_reason_once_and_adds_the_parts_up() -> None:
    # 3 + 1 + 2 = 6 ST, of which 1 only because the rename is pending and 2 only by the band;
    # 4 + 2 = 6 unknown, of which only the 2 before-first are a genuine gap in the corpus.
    reasons = [
        *["st_by_name"] * 3,
        "st_pending",
        *["st_by_band"] * 2,
        "delisting",
        *["ordinary"] * 5,
        *["unknown_no_record"] * 4,
        *["unknown_before_first"] * 2,
    ]
    random.Random(20261013).shuffle(reasons)

    # The codes left out for their board are named, ascending, and counted.
    tallied = FactsNotes.tally(
        reasons=reasons,
        suspended_without_bar=7,
        no_listing_row=1,
        no_bar_within_carry=2,
        not_listed_with_bar=3,
        unrecognised_board_codes=["303002.SZ", "303001.SZ"],
    )

    assert tallied == FactsNotes(
        name_unknown_assumed_ordinary=6,
        suspended_without_bar=7,
        no_listing_row=1,
        delisting=1,
        st=6,
        st_by_name=3,
        st_pending=1,
        st_by_band=2,
        name_unknown_no_record=4,
        name_unknown_before_first=2,
        no_bar_within_carry=2,
        not_listed_with_bar=3,
        unrecognised_board=2,
        unrecognised_board_codes=("303001.SZ", "303002.SZ"),
    )


def test_an_empty_day_tallies_to_zero_everywhere() -> None:
    assert FactsNotes.tally(
        reasons=[],
        suspended_without_bar=0,
        no_listing_row=0,
        no_bar_within_carry=0,
        not_listed_with_bar=0,
        unrecognised_board_codes=(),
    ) == notes(
        name_unknown_assumed_ordinary=0,
        suspended_without_bar=0,
        no_listing_row=0,
        delisting=0,
        st=0,
        st_by_name=0,
        st_pending=0,
        st_by_band=0,
        name_unknown_no_record=0,
        name_unknown_before_first=0,
        no_bar_within_carry=0,
        not_listed_with_bar=0,
        unrecognised_board=0,
        unrecognised_board_codes=(),
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"st": 124},  # 120 + 1 + 2 = 123, not 124
        {"st_by_band": 3},  # makes the parts 124 against a total of 123
        {"name_unknown_assumed_ordinary": 4},  # 2 + 1 = 3, not 4
        {"name_unknown_before_first": 0},  # makes the parts 2 against a total of 3
        {"suspended_without_bar": -1},
        {"no_listing_row": -1},
        {"delisting": -1},
        {"no_bar_within_carry": -1},
        {"not_listed_with_bar": -1},
        {"unrecognised_board": -1},
        {"unrecognised_board": 2},  # one code named, two counted
        {"unrecognised_board": 0},  # one code named, none counted
        {"unrecognised_board": 2, "unrecognised_board_codes": ("303002.SZ", "303001.SZ")},
        {"unrecognised_board": 2, "unrecognised_board_codes": ("303001.SZ", "303001.SZ")},
    ],
    ids=[
        "st-total",
        "st-part",
        "unknown-total",
        "unknown-part",
        "negative-suspended",
        "negative-no-listing",
        "negative-delisting",
        "negative-no-bar-within-carry",
        "negative-not-listed-with-bar",
        "negative-unrecognised-board",
        "board-count-over-codes",
        "board-count-under-codes",
        "board-codes-unsorted",
        "board-codes-repeated",
    ],
)
def test_counts_that_do_not_add_up_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(SurvivorFactsError):
        notes(**bad)


def test_the_tally_refuses_a_reason_it_does_not_know() -> None:
    with pytest.raises(SurvivorFactsError, match="bogus"):
        FactsNotes.tally(
            reasons=["ordinary", "bogus"],  # type: ignore[list-item]
            suspended_without_bar=0,
            no_listing_row=0,
            no_bar_within_carry=0,
            not_listed_with_bar=0,
            unrecognised_board_codes=(),
        )


def test_the_main_board_prefixes_are_the_eight_t8_measured_each_on_its_own_exchange() -> None:
    # T8's board table: SH 600/601/603/605 and SZ 000/001/002/003 -- a prefix with the exchange
    # it is on, so `600*.SZ` and `000*.SH` are not main-board codes. Growing the set is a
    # decision (`MAIN_BOARD_PREFIXES`' docstring), so it changes this test with it.
    assert (
        frozenset(
            {
                ("600", "SH"),
                ("601", "SH"),
                ("603", "SH"),
                ("605", "SH"),
                ("000", "SZ"),
                ("001", "SZ"),
                ("002", "SZ"),
                ("003", "SZ"),
            }
        )
        == MAIN_BOARD_PREFIXES
    )


# --- FactsDay ----------------------------------------------------------------------------

EMPTY_NOTES = FactsNotes.tally(
    reasons=[],
    suspended_without_bar=0,
    no_listing_row=0,
    no_bar_within_carry=0,
    not_listed_with_bar=0,
    unrecognised_board_codes=(),
)


def day_of(*facts: SecurityFacts, **overrides: object) -> FactsDay:
    """A day over `facts` whose counts are right unless overridden: the main board only, every
    main-board row with all its values scored, and nothing held."""
    allowed = [fact for fact in facts if fact.board == "main"]
    arguments: dict[str, object] = {
        "session": DAY,
        "facts": facts,
        "notes": EMPTY_NOTES,
        "boards": ("main",),
        "universe_size": len(allowed),
        "scored_size": sum(1 for fact in allowed if None not in fact.oriented_values),
        "held": (),
        "held_status": (),
    }
    arguments.update(overrides)
    return FactsDay(**arguments)  # type: ignore[arg-type]


# Two main-board rows (one missing a value), one ChiNext row and one STAR row, by code.
MAIN_A = assemble(ts_code="600001.SH")
MAIN_B = assemble(ts_code="600002.SH", oriented_values=(0.5, None, 0.0, 2.0))
GROWTH = assemble(ts_code="300001.SZ", board="growth")
STAR_ROW = assemble(ts_code="688001.SH", board="star")
FOUR = (GROWTH, MAIN_A, MAIN_B, STAR_ROW)


def test_a_consistent_day_is_accepted_and_counts_only_the_allowed_boards() -> None:
    # 4 rows; 2 on the main board, 1 of which has every value.
    day = day_of(*FOUR)

    assert (day.universe_size, day.scored_size) == (2, 1)
    assert day.boards == ("main",)


def test_a_day_counted_over_more_boards_counts_their_rows() -> None:
    day = day_of(*FOUR, boards=("main", "growth"), universe_size=3, scored_size=2)

    assert (day.universe_size, day.scored_size) == (3, 2)


@pytest.mark.parametrize(
    ("facts", "match"),
    [
        ((MAIN_A, GROWTH), "ascending"),  # out of order
        ((MAIN_A, MAIN_A), "ascending"),  # the same code twice
    ],
    ids=["unsorted", "repeated"],
)
def test_facts_out_of_order_or_repeated_are_refused(
    facts: tuple[SecurityFacts, ...], match: str
) -> None:
    with pytest.raises(SurvivorFactsError, match=match):
        day_of(*facts, universe_size=sum(1 for fact in facts if fact.board == "main"))


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        # Every row counted, though two are on boards the book cannot trade.
        ({"universe_size": 4}, "universe_size"),
        ({"universe_size": 1}, "universe_size"),
        # More scored than the universe holds.
        ({"universe_size": 2, "scored_size": 3}, "scored_size"),
        # MAIN_B lacks a value, so at most one main-board row can be scored.
        ({"scored_size": 2}, "scored_size"),
        ({"scored_size": -1}, "scored_size"),
    ],
    ids=["all-boards", "short", "scored-over-universe", "scored-without-values", "negative"],
)
def test_counts_that_do_not_match_the_rows_are_refused(
    overrides: dict[str, int], match: str
) -> None:
    with pytest.raises(SurvivorFactsError, match=match):
        day_of(*FOUR, **overrides)


# --- FactsDay: what became of every held code ------------------------------------------------


def held_day(*statuses: tuple[str, str], **overrides: object) -> FactsDay:
    """`FOUR`'s day holding the codes of `statuses`, each with its status, in the order given."""
    arguments: dict[str, object] = {
        "held": tuple(code for code, _ in statuses),
        "held_status": tuple(
            HeldStatus(ts_code=code, status=status)  # type: ignore[arg-type]
            for code, status in statuses
        ),
    }
    arguments.update(overrides)
    return day_of(*FOUR, **arguments)


def test_a_day_names_every_held_code_and_what_became_of_it() -> None:
    # MAIN_A (600001.SH) has a row; 600009.SH, 600010.SH, 600011.SH and 600012.SH have none.
    # A code the registry has no listing row for may have one (a bar on the day: aged 0) or not.
    day = held_day(
        ("600001.SH", "row"),
        ("600009.SH", "no_bar_in_reach"),
        ("600010.SH", "not_listed"),
        ("600011.SH", "not_in_registry"),
        ("600012.SH", "board_unrecognised"),
    )

    assert day.held == ("600001.SH", "600009.SH", "600010.SH", "600011.SH", "600012.SH")
    assert [status.status for status in day.held_status] == [
        "row",
        "no_bar_in_reach",
        "not_listed",
        "not_in_registry",
        "board_unrecognised",
    ]
    assert held_day(("600002.SH", "not_in_registry")).held_status[0].status == "not_in_registry"


def test_a_day_holding_nothing_has_no_held_status() -> None:
    day = day_of(*FOUR)

    assert (day.held, day.held_status) == ((), ())


@pytest.mark.parametrize(
    ("statuses", "overrides", "match"),
    [
        # `held` must be ascending, each code once.
        ((("600002.SH", "row"), ("600001.SH", "row")), {}, "held"),
        ((("600001.SH", "row"), ("600001.SH", "row")), {}, "held"),
        # `held_status` must cover exactly `held`, in its order.
        ((("600001.SH", "row"),), {"held": ("600001.SH", "600002.SH")}, "held_status"),
        ((("600001.SH", "row"), ("600002.SH", "row")), {"held": ("600001.SH",)}, "held_status"),
        (
            (("600001.SH", "row"), ("600002.SH", "row")),
            {
                "held_status": (
                    HeldStatus(ts_code="600002.SH", status="row"),
                    HeldStatus(ts_code="600001.SH", status="row"),
                )
            },
            "held_status",
        ),
        # "row" exactly when the code has one; only a code not in the registry may have a row
        # and another status.
        ((("600009.SH", "row"),), {}, "600009.SH"),
        ((("600001.SH", "no_bar_in_reach"),), {}, "600001.SH"),
        ((("600001.SH", "not_listed"),), {}, "600001.SH"),
        ((("600001.SH", "board_unrecognised"),), {}, "600001.SH"),
    ],
    ids=[
        "held-unsorted",
        "held-repeated",
        "status-missing",
        "status-extra",
        "status-out-of-order",
        "row-without-a-row",
        "no-bar-with-a-row",
        "not-listed-with-a-row",
        "board-with-a-row",
    ],
)
def test_held_codes_and_their_statuses_that_do_not_match_the_rows_are_refused(
    statuses: tuple[tuple[str, str], ...], overrides: dict[str, object], match: str
) -> None:
    with pytest.raises(SurvivorFactsError, match=match):
        held_day(*statuses, **overrides)


def test_a_held_status_outside_the_vocabulary_is_refused_and_a_status_is_frozen() -> None:
    with pytest.raises(ValidationError):
        HeldStatus(ts_code="600001.SH", status="sold")  # type: ignore[arg-type]
    status = HeldStatus(ts_code="600001.SH", status="row")
    with pytest.raises(ValidationError):
        status.status = "not_listed"
