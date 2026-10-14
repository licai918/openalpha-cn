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
    MIN_CALENDAR_LOOKBACK_SESSIONS,
    FactsNotes,
    ListingAgeHorizonError,
    SurvivorFactsError,
    assemble_fact,
    listing_age_sessions,
    name_status,
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


def age(
    listed_on: date | None,
    day: date,
    *,
    between: Callable[[date, date], int] = sessions_between,
) -> int | None:
    return listing_age_sessions(
        listed_on=listed_on,
        calendar_first=CALENDAR.horizon.first_date,
        day=day,
        sessions_between=between,
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
    assert MIN_CALENDAR_LOOKBACK_SESSIONS == 250
    assert age(date(2010, 1, 4), date(2020, 12, 15)) == 250


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


def test_the_notes_are_a_frozen_set_of_counts() -> None:
    notes = FactsNotes(
        name_unknown_assumed_ordinary=3,
        suspended_without_bar=47,
        no_listing_row=2,
        delisting=1,
        st=123,
    )

    assert (
        notes.name_unknown_assumed_ordinary,
        notes.suspended_without_bar,
        notes.no_listing_row,
        notes.delisting,
        notes.st,
    ) == (3, 47, 2, 1, 123)
    with pytest.raises(FrozenInstanceError):
        notes.st = 0  # type: ignore[misc]
    assert notes == FactsNotes(
        name_unknown_assumed_ordinary=3,
        suspended_without_bar=47,
        no_listing_row=2,
        delisting=1,
        st=123,
    )
