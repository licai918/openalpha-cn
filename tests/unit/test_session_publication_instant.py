"""`session_publication_instant` is `_sessions_published_through` backwards (`V2-P5-010`).

`V2-P4-063` found the 16:30 publication rule restated in three places with two of them
disagreeing, and `V2-P4-114` found a fourth restatement one row later -- inside `panel_ingest`
itself -- and fixed it by *calling* `_sessions_published_through` instead of doing the
arithmetic again. `V2-P5-010` needs the rule in the other direction (given a session, when may
a scheduler run it), and the way to add that without becoming the fifth restatement is to put
the inverse beside the original, reading the same `DAILY_AVAILABILITY_TIME`, and to pin the two
against each other rather than against a literal.

The round trip is the pin, and it is measured over a full year at half-hourly resolution rather
than at a handful of hand-picked instants -- the resolution `V2-P4-077` used for the same
module's other clock, and fine enough that a 30-minute error in either function shows up.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn.domain.daily_prices import DAILY_AVAILABILITY_TIME
from openalpha_cn.panel_ingest import (
    DEFAULT_DATE_TIMEZONE,
    _sessions_published_through,
    session_publication_instant,
)

SHANGHAI: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)
YEAR_START: Final[date] = date(2026, 1, 1)


def test_a_session_publishes_at_the_instant_that_reports_it_as_the_newest_published_one() -> None:
    """The round trip, over 365 sessions: publish(d) must report back as d.

    This is the property that makes the two functions one rule. If either drifts -- a `>` that
    becomes `>=`, a timezone that becomes UTC, a `- 1 day` that moves -- the identity breaks
    for at least one day of the year.
    """
    for offset in range(365):
        session = YEAR_START + timedelta(days=offset)
        instant = session_publication_instant(session)
        assert _sessions_published_through(instant, SHANGHAI) == session, session


def test_the_instant_is_the_earliest_one_that_reports_the_session() -> None:
    """One second earlier must report the day before, or the inverse is merely *an* instant.

    Without this, `session_publication_instant` could return midnight of the following day and
    still satisfy the round trip -- and a scheduler using it would fire a day late, every day,
    for ever. This is the assertion that separates "the publication instant" from "some instant
    after publication".
    """
    for offset in range(0, 365, 7):
        session = YEAR_START + timedelta(days=offset)
        instant = session_publication_instant(session)
        assert _sessions_published_through(instant - timedelta(seconds=1), SHANGHAI) == (
            session - timedelta(days=1)
        ), session


def test_every_half_hour_of_a_year_agrees_with_the_publication_instant_of_its_answer() -> None:
    """The other direction, swept: for any instant, publish(answer(t)) <= t < publish(answer+1).

    17,520 instants. The bound on the right is what catches an inverse that is *late*; the bound
    on the left catches one that is early. Together they say the two functions partition the
    year at exactly the same 365 boundaries.
    """
    cursor = datetime(2026, 1, 1, 0, 0, tzinfo=SHANGHAI)
    end = datetime(2027, 1, 1, 0, 0, tzinfo=SHANGHAI)
    checked = 0
    while cursor < end:
        answered = _sessions_published_through(cursor, SHANGHAI)
        assert session_publication_instant(answered) <= cursor, cursor
        assert cursor < session_publication_instant(answered + timedelta(days=1)), cursor
        cursor += timedelta(minutes=30)
        checked += 1
    assert checked == 17_520, checked


def test_the_instant_carries_the_zone_it_was_asked_for_rather_than_utc() -> None:
    """A naive or UTC-stamped answer is a silent one-day error for a caller comparing clocks.

    A scheduler holds a UTC clock. `16:30+08:00` is `08:30Z`; the same wall time read as UTC is
    eight hours later, which on the last half-hour of a day is a different calendar date.
    """
    instant = session_publication_instant(date(2026, 8, 24))

    assert instant.tzinfo is not None
    assert instant.utcoffset() == timedelta(hours=8)
    assert instant.timetz().replace(tzinfo=None) == DAILY_AVAILABILITY_TIME
    assert instant.astimezone(UTC).hour == 8


def test_an_unknown_time_zone_is_refused_by_the_same_error_the_module_already_raises() -> None:
    """Not a new error class: `_resolve_timezone` already owns this refusal."""
    from openalpha_cn.domain.panel_batch import PanelBatchError

    with pytest.raises(PanelBatchError, match="not a known IANA time zone"):
        session_publication_instant(date(2026, 8, 24), date_timezone="Mars/Olympus")
