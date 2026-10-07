"""The exchange trading calendar (`V2-P1-004`): a three-valued verdict, not a boolean.

## Why a boolean answer is the wrong type

Before this module the only "calendar" in the repository was
`scripts/generate_replay_corpus.py`'s `current.weekday() < 5` -- Monday to Friday, no
holidays. A live probe of Tushare's published SSE calendar (13,162 rows, 1990-12-19 through
2026-12-31, captured 2026-08-08) puts a number on how wrong that is: **605 weekdays are
closed** and **zero weekends are open**. The error is one-directional, which is precisely
what makes it survive review -- an approximated calendar never *hides* a session, it only
invents them, so nothing downstream ever crashes on a missing day.

That is the smaller of the two problems. The larger one is the horizon. The same probe found
that a request for any date in 2027 returns **zero rows**: the exchange has not published
next year's calendar yet. A calendar that answers `False` for a date it has no row for
reports the whole of 2027 as one uninterrupted holiday, and a backtest, a label window or a
rebalance schedule built on it produces plausible, silently empty results. So the verdict is
three-valued -- `trading` / `closed` / `beyond_horizon` -- and every navigation method
refuses rather than guesses once it would have to step outside what is known.

Two shapes guard the third state against being collapsed back into the second:

- `CalendarDayStatus` **is not a truth value**. `bool(status)` raises, so `if not
  calendar.day_status(day):` -- the one-line mistake that would merge `closed` with
  `beyond_horizon` -- fails loudly instead of type-checking clean and running wrong. This is
  the same response `panel/catalog.py`'s `PanelReadOutcome.rows` makes to the same class of
  mistake.
- `is_trading_day()` still returns a plain `bool`, because inside the horizon the question
  really is binary -- but it **raises** `CalendarHorizonError` outside it. So `if not
  calendar.is_trading_day(day):` is safe by construction: it cannot receive the unknown case
  at all.

## Why construction is fail-closed too

`build_trading_calendar` refuses a set of days with a hole in it. That is the same defect as
an unpublished year, one range narrower: rows for January and March with February missing
would present February as a two-month recess, and the horizon (`min`..`max` of what was
supplied) would claim to cover it. Requiring every natural day between the first and the last
to be present makes the horizon an honest statement about the *whole* interval rather than
about its endpoints. A caller assembling several years from separate partitions therefore
gets a refusal when one of them is missing, instead of a calendar that quietly reads that
year as closed.

## The stored availability instant has a known look-ahead

`providers/tushare.py::_calendar_publication_timeline` dates a calendar row's availability at
the start of the row's own year. That bound needs no guess about publication dates, but it is
**not** conservative, and calling it conservative was this module's first version's mistake.
China's holiday schedule is amended mid-year by the State Council, and `trade_cal` returns
only the current snapshot -- no revision history, no announcement date. Every amendment
therefore reaches this repository dated as if it had been knowable on 1 January of its own
year.

`KNOWN_CALENDAR_LOOKAHEAD` names three dates from two proven amendments, all found in the same
published data this module is built on, and `TradingCalendar.known_lookahead()` reports the
ones that fall
inside a given horizon so `V2-P1-013`'s gate can see them. Neither the list nor the method is
exhaustive: they are evidence that the defect is real and has a measurable size, not an
enumeration of every affected date. Removing the defect needs a revision history the endpoint
does not serve, or an availability model that can say "unknown between these two instants",
which `Timeline`'s four fixed clocks cannot express.

## What this module deliberately does not do

It has no notion of intraday sessions, half-days, or per-instrument suspension -- a day is
open or it is not. Suspension is `suspend_d` (`V2-P1-008`), which is per-name and belongs on
the panel plane as its own dataset, not as a modifier of the exchange calendar.

It also does not read `pretrade_date`, Tushare's own previous-trading-day column, as the
answer to `previous_trading_day()`. The column is carried on `CalendarDay` and stored on the
panel plane, and the two are cross-checked (0 disagreements across all 13,162 published rows
in the live probe; re-proved on the fixtures in
`tests/unit/domain/test_trading_calendar.py::test_previous_trading_day_agrees_with_tushares_own_pretrade_date_column`).
Deriving the answer and keeping the column as an independent witness is worth more than
taking the column as gospel: a witness can disagree, and a disagreement is a finding.

One consequence of that choice is recorded rather than hidden: `pretrade_date` on the very
first row of a fetched window points at a trading day *before* the horizon, so it carries one
step of knowledge this calendar refuses to use. `previous_trading_day(horizon.first_date)`
raises instead of answering it. Extending the horizon by one day on the strength of a single
column would make the horizon's meaning depend on which end of it you asked from.

## Layering

Pure `datetime`, `bisect` and `dataclasses`; no provider, no store, no clock. It sits in
`domain/` for the same reason `domain/panel_batch.py` does -- `providers/` produces the rows
and `panel_ingest` reads them back, and putting the contract in either would create an edge
between two peers. `openalpha_cn.panel` still imports no sibling package: the calendar is
assembled *above* it, in `panel_ingest.py`, and handed to `ReadinessRequirement` as the plain
`tuple[date, ...]` that contract already takes.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Final, cast

_ONE_DAY: Final[timedelta] = timedelta(days=1)

TRADING_CALENDAR_DATASET: Final[str] = "trade_cal"
"""The panel dataset (and partition directory) the calendar is stored under.

Declared here rather than in `providers/tushare.py` because three modules share it and only
one of them may import the provider: `panel_ingest` reads the calendar back out of the store
and `openalpha_cn.panel_ingest` is pinned to importing `domain` and `panel` only
(`tests/unit/test_panel_ingest_import_isolation.py`). The name happens to equal Tushare's
`api_name`; that is a convenience, not a dependency.
"""

CALENDAR_DATE_COLUMN: Final[str] = "cal_date"
CALENDAR_OPEN_COLUMN: Final[str] = "is_open"
CALENDAR_PRETRADE_COLUMN: Final[str] = "pretrade_date"

CALENDAR_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    CALENDAR_DATE_COLUMN,
    CALENDAR_OPEN_COLUMN,
    CALENDAR_PRETRADE_COLUMN,
)
"""The calendar's stored columns, in the order `trading_calendar_from_panel_rows` reads them.

One tuple, used both as the projection a reader asks `PanelStore.query` for and as the
positional contract of the rows it gets back, so the two cannot drift.
"""


class TradingCalendarError(ValueError):
    """Raised for any malformed trading calendar or malformed query against one.

    A `ValueError` subclass to match `domain/panel_batch.py`'s `PanelBatchError`: callers
    written against the broad contract keep working, and code that cares specifically can
    catch this.
    """


class CalendarHorizonError(TradingCalendarError):
    """Raised when a question's answer would lie outside what the calendar knows.

    Separate from its parent because it is the one failure that is *not* a caller mistake:
    the question was well formed and the exchange simply has not published that far. A gate
    (`V2-P1-013`) blocks on it; a malformed query is a bug.
    """


class CalendarDayStatus(Enum):
    """The three things a calendar can say about a date. Deliberately not a `bool`.

    `__bool__` raises for every member, including `trading`. Making only `beyond_horizon`
    unusable as a truth value would leave `if status:` working for two of three members and
    exploding on the third -- a landmine rather than a guardrail. A verdict is not a
    condition; ask for the one you mean.
    """

    trading = "trading"
    closed = "closed"
    beyond_horizon = "beyond_horizon"

    def __bool__(self) -> bool:
        raise TradingCalendarError(
            f"{type(self).__name__}.{self.name} is a three-valued verdict and has no truth "
            "value; compare it against the member you mean (`is CalendarDayStatus.trading`) "
            "or call `is_trading_day()`, which raises rather than answering False for a date "
            "beyond the published horizon"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CalendarHorizon:
    """The closed interval of calendar dates a `TradingCalendar` actually knows about.

    Public because downstream has to be able to *ask* rather than discover the boundary by
    tripping over it: `V2-P1-012`'s health report states how far the calendar reaches, and
    `V2-P1-017`'s label contract has to know whether a return window it wants to open is
    answerable before it opens it.
    """

    first_date: date
    last_date: date

    def covers(self, day: date) -> bool:
        """Whether `day` falls inside this interval (both endpoints included)."""
        return self.first_date <= day <= self.last_date


@dataclass(frozen=True, slots=True, kw_only=True)
class CalendarLookahead:
    """One calendar date whose stored availability instant precedes the real announcement.

    `claimed_available_from` is what `_calendar_publication_timeline` assigns the row (1
    January of the row's own year); `announced_on` is when the decision was actually made
    public. `lookahead_days` is the width of the window in which this repository answers a
    question it had no way of answering.
    """

    calendar_date: date
    claimed_available_from: date
    announced_on: date
    note: str

    @property
    def lookahead_days(self) -> int:
        """How long the stored calendar answers ahead of the real announcement."""
        return (self.announced_on - self.claimed_available_from).days


KNOWN_CALENDAR_LOOKAHEAD: Final[tuple[CalendarLookahead, ...]] = (
    CalendarLookahead(
        calendar_date=date(2015, 9, 3),
        claimed_available_from=date(2015, 1, 1),
        announced_on=date(2015, 5, 13),
        note=(
            "Victory Day parade recess. The 2015 schedule published in late 2014 had 3 and 4 "
            "September as ordinary sessions; the State Council General Office announced the "
            "closure separately on 2015-05-13."
        ),
    ),
    CalendarLookahead(
        calendar_date=date(2015, 9, 4),
        claimed_available_from=date(2015, 1, 1),
        announced_on=date(2015, 5, 13),
        note="The second day of the same 2015-05-13 announcement.",
    ),
    CalendarLookahead(
        calendar_date=date(2020, 1, 31),
        claimed_available_from=date(2020, 1, 1),
        announced_on=date(2020, 1, 27),
        note=(
            "Spring Festival extension. The 2020 schedule published 2019-11-21 had 31 January "
            "as an open session; the extension announced on 2020-01-26/27 closed it. This one "
            "inverts the verdict rather than merely moving it, and the session it pushed the "
            "reopening to -- 2020-02-03 -- is the one the market gapped down roughly 7.7% on."
        ),
    ),
)
"""Proven instances of the look-ahead described in this module's docstring.

**Not an enumeration of every affected date.** Any mid-year amendment to the holiday schedule
produces the same defect, and `trade_cal` carries nothing that would let this code find them
all: it serves one snapshot per request and no revision history. These three are here because
they were reproduced against the same live endpoint the rest of this contract is built on, so
the defect is a measured fact with a size (132 days in 2015, 26 days in 2020) rather than a
theoretical worry -- and so that a future improvement to the availability rule has a
regression fixture to change.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class CalendarDay:
    """One published calendar date: was the exchange open, and what did it call the previous
    session.

    A plain carrier with no validation of its own, following `panel/catalog.py`'s precedent:
    a nominal type is not a boundary (a duck-typed stand-in satisfies every attribute read,
    and a subclass can override `__post_init__` away), so the rules live once, in
    `build_trading_calendar`, where the values are actually used.
    """

    calendar_date: date
    is_trading: bool
    previous_trading_date: date | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TradingCalendar:
    """A contiguous window of one exchange's published calendar.

    Build it with `build_trading_calendar`; this constructor is not a boundary and validates
    nothing. `trading_days` is ascending and holds only open sessions, while `horizon` covers
    every calendar date the calendar was told about, open or not -- which is exactly what
    makes `closed` and `beyond_horizon` distinguishable at all.
    """

    exchange: str
    horizon: CalendarHorizon
    trading_days: tuple[date, ...]

    @property
    def trading_day_count(self) -> int:
        return len(self.trading_days)

    def known_lookahead(self) -> tuple[CalendarLookahead, ...]:
        """The proven look-ahead instances that fall inside this calendar's horizon.

        The interface `V2-P1-013`'s dependency gate needs: a non-empty result means this
        window contains at least one date whose availability instant this repository
        overstates, so a point-in-time claim made over it is known to be contaminated. An
        empty result means *no known instance*, not "clean" -- see `KNOWN_CALENDAR_LOOKAHEAD`
        for why no code here can enumerate them all.
        """
        return tuple(
            entry for entry in KNOWN_CALENDAR_LOOKAHEAD if self.horizon.covers(entry.calendar_date)
        )

    def day_status(self, day: date) -> CalendarDayStatus:
        """The three-valued verdict for `day`. Never raises for a well-formed date."""
        _require_plain_date(day, "day")
        if not self.horizon.covers(day):
            return CalendarDayStatus.beyond_horizon
        if self._session_index(day) is None:
            return CalendarDayStatus.closed
        return CalendarDayStatus.trading

    def is_trading_day(self, day: date) -> bool:
        """Whether the exchange was open on `day`.

        Raises `CalendarHorizonError` for a date outside the horizon rather than answering
        `False`: an unpublished date is not a holiday, and `if not is_trading_day(day):` is
        how a `False` would be read.
        """
        status = self.day_status(day)
        if status is CalendarDayStatus.beyond_horizon:
            raise self._beyond_horizon(day.isoformat())
        return status is CalendarDayStatus.trading

    def next_trading_day(self, day: date) -> date:
        """The first open session strictly after `day`.

        Raises `CalendarHorizonError` if `day` is outside the horizon, or if the answer would
        be -- the last day of the horizon has a successor in reality and none that this
        calendar may claim to know.
        """
        self._require_within_horizon(day)
        position = bisect_right(self.trading_days, day)
        if position == len(self.trading_days):
            raise self._beyond_horizon(f"the first session after {day.isoformat()}")
        return self.trading_days[position]

    def previous_trading_day(self, day: date) -> date:
        """The last open session strictly before `day`, or `CalendarHorizonError`."""
        self._require_within_horizon(day)
        position = bisect_left(self.trading_days, day)
        if position == 0:
            raise self._beyond_horizon(f"the last session before {day.isoformat()}")
        return self.trading_days[position - 1]

    def trading_days_between(self, start: date, end: date) -> tuple[date, ...]:
        """Every open session in the closed interval `[start, end]`, ascending.

        The whole interval must lie inside the horizon; a range that leaves it even partly is
        refused rather than truncated. `()` therefore means one thing only -- the exchange
        was shut for the entire range -- and can never mean "we have no rows here", which is
        what makes this safe to hand to `ReadinessRequirement.required_dates`.
        """
        _require_plain_date(start, "start")
        _require_plain_date(end, "end")
        if start > end:
            raise TradingCalendarError(f"start {start.isoformat()} is after end {end.isoformat()}")
        if not self.horizon.covers(start) or not self.horizon.covers(end):
            raise CalendarHorizonError(
                f"the {self.exchange} calendar covers {self._range_text()}; the requested "
                f"range {start.isoformat()}..{end.isoformat()} leaves it, so the sessions "
                "inside it are unknown rather than absent"
            )
        return self.trading_days[
            bisect_left(self.trading_days, start) : bisect_right(self.trading_days, end)
        ]

    def shift(self, day: date, offset: int) -> date:
        """The session `offset` open days away from `day`, counted in sessions.

        `day` must itself be an open session -- the primitive `V2-P1-017`'s label contract
        needs is "the trading day N sessions after the prediction day", and an anchor that
        was not a session has no position in the sequence to count from. Use
        `next_trading_day` / `previous_trading_day` to land on one first.
        """
        self._require_within_horizon(day)
        index = self._session_index(day)
        if index is None:
            raise TradingCalendarError(
                f"{day.isoformat()} is not an open session on the {self.exchange} calendar, so "
                "it has no position to count sessions from; use next_trading_day() or "
                "previous_trading_day() to reach one first"
            )
        target = index + offset
        if not 0 <= target < len(self.trading_days):
            raise self._beyond_horizon(f"{offset:+d} sessions from {day.isoformat()}")
        return self.trading_days[target]

    def _session_index(self, day: date) -> int | None:
        position = bisect_left(self.trading_days, day)
        if position < len(self.trading_days) and self.trading_days[position] == day:
            return position
        return None

    def _require_within_horizon(self, day: date) -> None:
        _require_plain_date(day, "day")
        if not self.horizon.covers(day):
            raise self._beyond_horizon(day.isoformat())

    def _range_text(self) -> str:
        return f"{self.horizon.first_date.isoformat()}..{self.horizon.last_date.isoformat()}"

    def _beyond_horizon(self, subject: str) -> CalendarHorizonError:
        return CalendarHorizonError(
            f"{subject} is outside the {self.exchange} calendar's published range "
            f"{self._range_text()}; an unpublished date is not a holiday, so this is a "
            "block, not a False"
        )


def build_trading_calendar(exchange: str, days: Iterable[CalendarDay]) -> TradingCalendar:
    """Assemble a `TradingCalendar` from published calendar days, in any order.

    Tushare returns `trade_cal` in *descending* `cal_date` order, so accepting any order and
    sorting here is not defensive politeness -- it is the actual shape of the data.

    Refuses, rather than repairs, five things: a malformed exchange name, an empty input, a
    duplicated calendar date, a gap in the natural-day sequence, and a window with no open
    session in it. The gap rule is the load-bearing one; see this module's docstring.
    """
    if type(exchange) is not str or not exchange or exchange != exchange.strip():
        raise TradingCalendarError(
            f"exchange must be a non-empty string without surrounding whitespace; got {exchange!r}"
        )
    states: dict[date, bool] = {}
    for entry in days:
        calendar_date = entry.calendar_date
        _require_plain_date(calendar_date, "calendar_date")
        if type(entry.is_trading) is not bool:
            raise TradingCalendarError(
                f"{calendar_date.isoformat()}: is_trading must be a bool, got "
                f"{type(entry.is_trading).__name__} {entry.is_trading!r}; a truthy stand-in "
                "hides the difference between the string '0' and a closed session"
            )
        if calendar_date in states:
            raise TradingCalendarError(
                f"{calendar_date.isoformat()} appears more than once; a calendar date has one "
                "state, and two rows for it mean two sources that were never reconciled"
            )
        states[calendar_date] = entry.is_trading
    if not states:
        raise TradingCalendarError(
            "a trading calendar needs at least one published day; an empty calendar answers "
            "'beyond horizon' to everything, which is safe but useless, and its horizon would "
            "have no endpoints to report"
        )

    first_date = min(states)
    last_date = max(states)
    expected = (last_date - first_date).days + 1
    if len(states) != expected:
        # Bounded by `expected` rather than by a `while cursor <= last_date` walk: the search
        # only runs once a shortfall is already counted, so a walk's terminating condition
        # would be a branch no input can ever take.
        missing = next(
            day
            for day in (first_date + offset * _ONE_DAY for offset in range(expected))
            if day not in states
        )
        raise TradingCalendarError(
            f"the {exchange} calendar was given {len(states)} days spanning "
            f"{first_date.isoformat()}..{last_date.isoformat()}, which needs {expected}; "
            f"{missing.isoformat()} is the first one absent. A missing row is not a closed "
            "session -- accepting the gap would report it as a holiday"
        )

    trading_days = tuple(day for day in sorted(states) if states[day])
    if not trading_days:
        raise TradingCalendarError(
            f"the {exchange} calendar was given {len(states)} days "
            f"({first_date.isoformat()}..{last_date.isoformat()}) and not one open session; "
            "a window of pure recess cannot answer any navigation question"
        )
    return TradingCalendar(
        exchange=exchange,
        horizon=CalendarHorizon(first_date=first_date, last_date=last_date),
        trading_days=trading_days,
    )


def trading_calendar_from_panel_rows(
    exchange: str, rows: Iterable[Sequence[object]]
) -> TradingCalendar:
    """Rebuild a calendar from stored panel rows shaped like `CALENDAR_PANEL_COLUMNS`.

    The counterpart of the provider's projection, and deliberately just as strict on the way
    back in: a column that came back as the string `"0"` -- from a schema drift, a
    hand-written partition, or a future storage backend without a boolean type -- would be
    truthy, and every holiday would silently become a session.

    That check is **not repeated here**. It is `build_trading_calendar`'s, which this function
    ends in, and which is where `CalendarDay`'s docstring already says the rules live once. An
    earlier version of this function duplicated it; both copies raised messages containing the
    same phrase, so deleting either one left every test still passing and neither guard was
    actually pinned. What this function owns is the shape of a *stored row* -- its width, and
    the ISO text its two date columns are stored as -- which is the part
    `build_trading_calendar` never sees.
    """
    parsed: list[CalendarDay] = []
    for index, row in enumerate(rows):
        if len(row) != len(CALENDAR_PANEL_COLUMNS):
            raise TradingCalendarError(
                f"row {index} has {len(row)} values, expected "
                f"{len(CALENDAR_PANEL_COLUMNS)} ({', '.join(CALENDAR_PANEL_COLUMNS)})"
            )
        calendar_date, is_trading, previous = row
        parsed.append(
            CalendarDay(
                calendar_date=_parse_iso_date(calendar_date, index, CALENDAR_DATE_COLUMN),
                # The `cast` records that this value is *unproven* at this line, not that it
                # is trusted: `build_trading_calendar` is what rejects anything that is not
                # exactly a `bool`, a few lines below.
                is_trading=cast(bool, is_trading),
                previous_trading_date=(
                    None
                    if previous is None
                    else _parse_iso_date(previous, index, CALENDAR_PRETRADE_COLUMN)
                ),
            )
        )
    return build_trading_calendar(exchange, parsed)


def _parse_iso_date(value: object, index: int, column: str) -> date:
    if not isinstance(value, str):
        raise TradingCalendarError(
            f"row {index}: {column} must be an ISO date string, got "
            f"{type(value).__name__} {value!r}"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise TradingCalendarError(
            f"row {index}: {column} is not an ISO date: {value!r}"
        ) from error


def _require_plain_date(value: date, role: str) -> None:
    """Reject anything that is not exactly a `date`.

    `datetime` subclasses `date`, so one arriving here would never compare equal to a real
    calendar date and would silently report every day as unknown. `PanelStore` guards its
    `required_dates` for the same reason; this is the other end of the same wire.
    """
    if type(value) is not date:
        raise TradingCalendarError(
            f"{role} must be a plain datetime.date, got {type(value).__name__} {value!r}; a "
            "datetime is a date subclass and would never equal any calendar date"
        )
