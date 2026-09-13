"""Which securities were listed on a given day (`V2-P1-005`), rebuilt without survivorship.

## Why the obvious query is the wrong one

Tushare's `stock_basic` endpoint answers `list_status="L"` when asked nothing at all. A live
probe on 2026-08-08 measured what that costs: the bare request returns **5,539** rows, every
one of them `L`; asking `list_status="L,D"` returns **5,878** -- the same 5,539 plus **339**
securities that have been delisted (`list_status="P"`, suspended, returns 0). Rebuilding the
2019 market from the bare request therefore assumes 2019's universe was 2026's survivors. On
2019-06-28 that is 3,404 names against the 3,631 that were actually listed: **227 missing**,
every one of them delisted after that date. On 2019-12-31 it is 3,540 against 3,760.

Worse, the bare request's answer *moves*. `stock_basic` has no `as_of` parameter -- it is a
snapshot of now -- so a security that delists tomorrow leaves the `L` set tomorrow, and the
same listed-only query run on two different days gives two different answers to the fixed
historical question "who was listed in 2019". The `L`-union-`D` reconstruction does not move,
because `list_date` and `delist_date` are facts about the past rather than about the snapshot.
That stability is the whole reason this module exists, and it is bounded rather than absolute
-- see `KNOWN_UNIVERSE_LIMITATIONS`.

## `delist_date` is exclusive, and that is measured rather than assumed

`delist_date` is the first day the security is **gone**, not its last session. Tushare's own
`daily` endpoint was checked against a random sample of 40 of the 339 delisted securities,
plus 15 named cases chosen to break it -- absorption mergers (`000024.SZ`), code migrations
(`T600018.SH`), the PT era (`000003.SZ`), the share-reform B-share codes (`600286.SH`,
`600205.SH`) and ordinary post-2014 terminations -- and **zero** bars fall on or after
`delist_date` in any of them. Membership is therefore `listed_on <= day < delisted_on`. The
other reading is not a rounding error: it would put a security in the pool on a day it
demonstrably did not trade, which is the shape of a backtest that fills an order at a price
that never existed.

An earlier version of this paragraph claimed something stronger and false: that the last bar
is the *previous session*. It was drawn from seven names that had all traded through their
退市整理期 after 2014, which is a biased sample. In the random 40 only **13** have their last
bar on the previous session; 27 do not, and the gap is routinely months:

    600286.SH S*ST国瓷  delist 2007-05-31  last bar 2006-04-28   260 sessions
    000405.SZ ST鑫光    delist 2004-03-19  last bar 2003-04-25   213 sessions
    600677.SH *ST航通   delist 2021-03-18  last bar 2020-04-29   213 sessions
    000024.SZ 招商地产  delist 2015-12-30  last bar 2015-12-07    15 sessions
    T600018.SH 上港集箱 delist 2006-10-20  no daily bars at all

Only the exclusive bound is load-bearing here, and only the exclusive bound is claimed. What
fills the gap between the last session and the termination is a suspension, which this dataset
does not model -- see `KNOWN_UNIVERSE_LIMITATIONS`'s `suspension_invisible`, which is the same
fact from the other side.

`list_date` is inclusive by the same measurement: `688001.SH`, `601127.SH`, `689009.SH` and
`600087.SH` each have their first daily bar on their own `list_date`. That check is narrower
than it looks -- `daily` serves at most 6,000 rows per code, so for a long-lived security the
earliest bars are truncated and the first bar it returns is not the security's first ever.
These four are each short enough to be complete.

## The snapshot horizon, and the one thing this contract refuses to guess

`day > snapshot_date` is `beyond_snapshot`, not "the current membership". The registry we hold
was taken at one instant; a security listing next week is simply not in it, so answering
today's membership for a future day would under-report and never say so. This is
`trading_calendar.py`'s `beyond_horizon` in a different dataset, and it is guarded the same
way: `ListingStatus` raises on `bool()` for every member, and `is_listed()` raises rather than
answering `False`.

The lower bound is narrower, and it turns on whether the reconstruction knows its own window.
A day before the earliest listing is answered, not refused, because the answer -- nothing was
listed -- is true and knowable, and refusing it would make an honest empty universe
indistinguishable from an unreadable one. But that is only true when the read reached back
that far. A universe assembled from partitions carries `years_read`, and a day before the
first of those years is a day whose listings this read never saw: the honest empty answer and
the truncated one are then the same value, so `listed_on()` refuses it. Built directly from
`SecurityLifecycle` values there is no window to check and the empty answer stands. `status_on`
needs no such rule -- a security whose listing row was outside the window has no entry at all,
and `security()` already refuses an unknown code rather than answering about it.

## What `available_time` claims, and what it does not

`providers/tushare.py` dates every lifecycle row at **its own event's midnight in
Asia/Shanghai**, and this module is the reason that is the right instant rather than a
compromise. `stock_basic` carries no announcement column of any kind, so there is no clock to
read: the choice is between the listing date, the delisting date, and something invented.

The forcing argument is that a listing and a delisting **cannot share a row**. A row has one
`available_time`, and any single instant for a security's whole registry entry is either early
enough to leak the delisting (dating the row at the listing makes a 2024 termination visible
to a 2019 reader) or late enough to reintroduce survivorship (dating it at the termination
hides the security from every `as_of` before it died -- the exact bug this module exists to
fix). So each response row becomes one or two panel rows, one per lifecycle event, and each
carries only the fact that was knowable at its own instant.

What the midnight bound does **not** claim is that the market learned of the event then. Both
events are announced in advance -- a listing by the exchange's 上市公告书, a termination by the
退市整理期 that precedes it -- and *how far* in advance is not derivable from this endpoint.
The claim this rule does make is narrower and checkable against price data: a security has a
daily bar on `list_date`, and none on or after `delist_date`, so a question asked at midnight
on day D gets the membership that day D's session actually had. The residual uncertainty is
one-sided and is named in `KNOWN_UNIVERSE_LIMITATIONS` under `announcement_clock_absent`
rather than being argued away here.

## What is deliberately not stored

None of `stock_basic`'s descriptive columns -- `name`, `industry`, `area`, `market`,
`act_name` -- reaches the panel. They are attributes of the *snapshot*, with no history and no
clock, and putting one on a lifecycle row would date a 2026 value at a 1990 event: the
registry calls `000005.SZ` `ST星源(退)` today, and stamping that onto its 1990-12-10 listing
row is a 34-year look-ahead with nothing to gate it. The name on a given day is
`domain/name_history.py`'s answer, from a dataset that has an announcement clock.

## Layering

Pure `datetime`, `bisect` and `dataclasses`, plus two sibling `domain` modules
(`panel_batch` for the reserved subject-column name, `trading_calendar` for the session
alignment). No provider, no store, no clock -- the same placement, and the same reason, as
`domain/trading_calendar.py`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Final

from openalpha_cn.domain.panel_batch import SUBJECT_COLUMN_NAME
from openalpha_cn.domain.trading_calendar import TradingCalendar

STOCK_BASIC_DATASET: Final[str] = "stock_basic"
"""The panel dataset (and partition directory) the security registry is stored under.

Declared here rather than in `providers/tushare.py` for the reason `TRADING_CALENDAR_DATASET`
is: `panel_ingest` reads the rows back and is pinned to importing `domain` and `panel` only.
"""

LIFECYCLE_EVENT_COLUMN: Final[str] = "lifecycle_event"
LIFECYCLE_DATE_COLUMN: Final[str] = "lifecycle_date"
UNIVERSE_EXCHANGE_COLUMN: Final[str] = "exchange"

LISTING_EVENT: Final[str] = "listing"
DELISTING_EVENT: Final[str] = "delisting"
LIFECYCLE_EVENTS: Final[frozenset[str]] = frozenset({LISTING_EVENT, DELISTING_EVENT})

UNIVERSE_DATA_COLUMNS: Final[tuple[str, ...]] = (
    LIFECYCLE_EVENT_COLUMN,
    LIFECYCLE_DATE_COLUMN,
    UNIVERSE_EXCHANGE_COLUMN,
)
"""The columns a provider projects, in order. `subject` is not among them: it is one of
`ColumnarPanelBatch`'s reserved names and is added by the batch itself."""

UNIVERSE_PANEL_COLUMNS: Final[tuple[str, ...]] = (SUBJECT_COLUMN_NAME, *UNIVERSE_DATA_COLUMNS)
"""What a reader asks `PanelStore.query` for, and the positional contract of the rows it gets.

One tuple used for both, so the projection and the parse cannot drift. Unlike the calendar's
equivalent this one *includes* the subject column, because the subject is the answer here --
the calendar filters on its subject and reads a single exchange's rows, while the universe
reads every security's rows at once and has to know which is which.
"""


class StockUniverseError(ValueError):
    """Raised for any malformed security registry or malformed query against one.

    A `ValueError` subclass to match `domain/trading_calendar.py`'s `TradingCalendarError`
    and `domain/panel_batch.py`'s `PanelBatchError`.
    """


class UniverseHorizonError(StockUniverseError):
    """Raised when a question's answer would lie beyond the registry snapshot.

    Separate from its parent because it is not a caller mistake: the question was well formed
    and the snapshot simply does not reach that far. `V2-P1-013`'s gate blocks on it; a
    malformed query is a bug.
    """


class ListingStatus(Enum):
    """The four things the registry can say about a security on a day. Not a `bool`.

    `__bool__` raises for every member, including `listed`. Making only `beyond_snapshot`
    unusable as a truth value would leave `if status:` working for three of four members and
    exploding on the fourth -- a landmine rather than a guardrail. This is the same response
    `CalendarDayStatus` and `PanelReadOutcome.rows` make to the same class of mistake.
    """

    not_yet_listed = "not_yet_listed"
    listed = "listed"
    delisted = "delisted"
    beyond_snapshot = "beyond_snapshot"

    def __bool__(self) -> bool:
        raise StockUniverseError(
            f"{type(self).__name__}.{self.name} is a four-valued verdict and has no truth "
            "value; compare it against the member you mean (`is ListingStatus.listed`) or "
            "call `is_listed()`, which raises rather than answering False for a day beyond "
            "the registry snapshot"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityLifecycle:
    """One security's listing and, if it has ended, its delisting.

    A plain carrier with no validation of its own, following `CalendarDay`'s precedent: a
    nominal type is not a boundary, so the rules live once, in `build_stock_universe`.

    `delisted_on` is **exclusive** -- the first day the security is gone, not its last
    session. See this module's docstring for the measurement behind that.
    """

    ts_code: str
    exchange: str
    listed_on: date
    delisted_on: date | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UniverseLimitation:
    """One named boundary on what a reconstructed universe can be trusted to answer."""

    code: str
    detail: str


KNOWN_UNIVERSE_LIMITATIONS: Final[tuple[UniverseLimitation, ...]] = (
    UniverseLimitation(
        code="a_shares_only",
        detail=(
            "stock_basic serves the Shanghai, Shenzhen and Beijing A-share boards and nothing "
            "else. A live probe on 2026-08-08 found zero 200xxx or 900xxx (B-share) codes in "
            "either the 5,539 listed or the 339 delisted rows, so a B share is absent from "
            "this universe rather than reported as closed, and `is_listed` refuses it as an "
            "unknown code rather than answering False."
        ),
    ),
    UniverseLimitation(
        code="delistings_only_back_to_the_first_recorded_one",
        detail=(
            "The delisted set reaches back only as far as its earliest row. In the 2026-08-08 "
            "snapshot that is 1999-07-12 (琼民源, which is in fact the first A-share "
            "termination), and the per-year census -- 1 in 1999, 3 in 2001, 46 in 2022, 45 in "
            "2023, 52 in 2024 -- tracks the public record. That is evidence, not proof: any "
            "termination the endpoint has dropped is invisible here, and a universe built "
            "from it would over-report membership for the affected day with nothing to "
            "signal it. `UniverseCompleteness.first_recorded_delisting` reports the bound "
            "this particular snapshot actually carries."
        ),
    ),
    UniverseLimitation(
        code="suspension_invisible",
        detail=(
            "A listed security that was halted all year is `listed` here. This dataset knows "
            "only about the two ends of a listing; per-name suspension is `suspend_d` "
            "(V2-P1-008), which belongs on the panel plane as its own dataset."
        ),
    ),
    UniverseLimitation(
        code="announcement_clock_absent",
        detail=(
            "stock_basic carries no announcement date, so availability is dated at the "
            "lifecycle event's own midnight (Asia/Shanghai). Both events are announced in "
            "advance -- a listing by the exchange's 上市公告书, a termination by the 退市整理"
            "期 that precedes it -- and how far in advance is not derivable from this "
            "endpoint. The bound this rule can defend is narrower: a security has a daily bar "
            "on its list_date and none on or after its delist_date, so a question asked at "
            "midnight on day D gets the membership day D's session actually had."
        ),
    ),
    UniverseLimitation(
        code="snapshot_has_no_as_of",
        detail=(
            "The endpoint answers `now`; re-fetching on another day yields another snapshot. "
            "Membership answers are stable across snapshots only because list_date and "
            "delist_date are facts about the past -- the listed-only reconstruction is not, "
            "and a live probe measured 227 securities listed on 2019-06-28 that the current "
            "listed set no longer contains."
        ),
    ),
    UniverseLimitation(
        code="a_listed_only_registry_is_invisible_to_every_downstream_check",
        detail=(
            "listed_on is a pure interval test over whatever rows the registry holds, so a "
            "partition fetched with list_status='L' alone -- 5,539 rows against 'L,D''s 5,878 "
            "on 2026-08-08 -- is self-consistent on every cross section and reports a smaller "
            "market rather than a broken one. Nothing downstream can tell the two apart. "
            "panel_doctor's SubjectContainment asks that daily's subjects be a subset of "
            "stock_basic's, and dropping the delisted half only shrinks both sides of a "
            "historical read; the coverage census counts rows without a target; and "
            "panel_gate.require_datasets therefore clears a listed-only registry with the same "
            "verdict, the same notices and the same caveats as a whole one. That is asserted "
            "rather than assumed, in tests/integration/panel/test_panel_gate.py. What it costs "
            "is survivorship bias of a measured size: a live probe rebuilt 2019-06-28 from the "
            "listed set alone and lost 227 securities, every one of them with a delist_date "
            "after that day. The defence is at fetch time -- providers/tushare.py requests "
            "'L,D' -- and it is a request parameter, which is exactly the kind of thing a read "
            "side cannot audit."
        ),
    ),
    UniverseLimitation(
        code="ts_code_is_not_a_permanent_security_identity",
        detail=(
            "A code migration produces two entries rather than one history: 000024.SZ "
            "(招商地产, delisted 2015-12-30) and 001979.SZ (招商蛇口) are separate securities "
            "here. The snapshot also carries at least one non-standard code -- T600018.SH "
            "(上港集箱, delisted 2006-10-20) -- and the namechange corpus carries five codes "
            "this registry does not, including two withdrawn IPOs (000991.SZ 通海高科, "
            "002525.SZ 胜景山河) that never listed at all."
        ),
    ),
)
"""Named boundaries on what a reconstructed universe answers, each measured on real data.

**Not an enumeration of every way the registry could be incomplete.** These are the ones a
live probe of the endpoint could demonstrate. `UniverseCompleteness` carries them alongside
the numbers a particular snapshot actually reports, so a downstream gate (`V2-P1-013`) reads
the boundary rather than inferring it from a row count.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class UniverseCompleteness:
    """How far a reconstructed universe reaches, and what it is known not to cover.

    Public because "who was listed that day" is only usable next to an answer to "and how
    complete is that". `V2-P1-012`'s health report states the snapshot date and the counts;
    `V2-P1-013`'s gate reads `limitations`.
    """

    snapshot_date: date
    security_count: int
    still_listed_count: int
    delisted_count: int
    first_listing: date
    first_recorded_delisting: date | None
    years_read: tuple[int, ...]
    """The lifecycle-year partitions this universe was assembled from, ascending.

    Empty when the universe was built directly from `SecurityLifecycle` values rather than
    read back from partitions. It is here because a year that was never ingested is
    indistinguishable from a year in which nothing listed and nothing died, so the window a
    reconstruction actually saw cannot be inferred from the securities in it -- see
    `panel_ingest.load_stock_universe`.
    """
    limitations: tuple[UniverseLimitation, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class StockUniverse:
    """Every security the registry knows about, with the two dates that bound its listing.

    Build it with `build_stock_universe`; this constructor is not a boundary and validates
    nothing. `securities` is ascending by `ts_code`.
    """

    snapshot_date: date
    securities: tuple[SecurityLifecycle, ...]
    years_read: tuple[int, ...] = ()
    """Which lifecycle-year partitions this universe came from; see `UniverseCompleteness`."""

    @property
    def security_count(self) -> int:
        return len(self.securities)

    @property
    def first_listing(self) -> date:
        return min(entry.listed_on for entry in self.securities)

    def security(self, ts_code: str) -> SecurityLifecycle:
        """The registry entry for `ts_code`, or `StockUniverseError`.

        An unknown code is refused rather than answered, because "this code is not in the
        snapshot" and "this code was not listed that day" are different facts and only the
        second one is an answer.
        """
        for entry in self.securities:
            if entry.ts_code == ts_code:
                return entry
        raise StockUniverseError(
            f"{ts_code!r} is not in the {self.snapshot_date.isoformat()} registry snapshot; "
            "an absent code is not a security that was never listed"
        )

    def status_on(self, ts_code: str, day: date) -> ListingStatus:
        """The four-valued verdict for one security on one day. Never raises for a known code."""
        _require_plain_date(day, "day")
        entry = self.security(ts_code)
        if day > self.snapshot_date:
            return ListingStatus.beyond_snapshot
        if day < entry.listed_on:
            return ListingStatus.not_yet_listed
        if entry.delisted_on is not None and day >= entry.delisted_on:
            return ListingStatus.delisted
        return ListingStatus.listed

    def is_listed(self, ts_code: str, day: date) -> bool:
        """Whether `ts_code` was listed on `day`.

        Raises `UniverseHorizonError` for a day after the snapshot rather than answering
        `False`: a day the registry cannot see is not a day nothing was listed, and
        `if not is_listed(...)` is how a `False` would be read.
        """
        status = self.status_on(ts_code, day)
        if status is ListingStatus.beyond_snapshot:
            raise self._beyond_snapshot(day.isoformat())
        return status is ListingStatus.listed

    def listed_on(self, day: date, *, exchange: str | None = None) -> tuple[str, ...]:
        """Every `ts_code` listed on `day`, ascending; optionally one exchange only.

        Raises `UniverseHorizonError` for a day after the snapshot, and for a day before the
        first year this universe was read from when it knows which years those were. A day
        before the earliest listing in a universe with no `years_read` returns `()`, which is
        then the true answer and not a horizon -- see this module's docstring.
        """
        _require_plain_date(day, "day")
        if day > self.snapshot_date:
            raise self._beyond_snapshot(day.isoformat())
        if self.years_read and day.year < self.years_read[0]:
            raise UniverseHorizonError(
                f"{day.isoformat()} is before {self.years_read[0]}, the first lifecycle year "
                "this universe was read from; the securities that listed earlier are not in "
                "it, so an empty membership for that day would be a short read reported as an "
                "empty market"
            )
        return tuple(
            entry.ts_code
            for entry in self.securities
            if (exchange is None or entry.exchange == exchange)
            and entry.listed_on <= day
            and (entry.delisted_on is None or day < entry.delisted_on)
        )

    def completeness(self) -> UniverseCompleteness:
        """What this reconstruction covers, and the named boundaries on trusting it."""
        terminations = [
            entry.delisted_on for entry in self.securities if entry.delisted_on is not None
        ]
        return UniverseCompleteness(
            snapshot_date=self.snapshot_date,
            security_count=self.security_count,
            still_listed_count=sum(1 for entry in self.securities if entry.delisted_on is None),
            delisted_count=len(terminations),
            first_listing=self.first_listing,
            first_recorded_delisting=min(terminations, default=None),
            years_read=self.years_read,
            limitations=KNOWN_UNIVERSE_LIMITATIONS,
        )

    def _beyond_snapshot(self, subject: str) -> UniverseHorizonError:
        return UniverseHorizonError(
            f"{subject} is beyond the {self.snapshot_date.isoformat()} registry snapshot; "
            "stock_basic answers only about now, so a later day's membership is unknown "
            "rather than equal to today's"
        )


def build_stock_universe(
    *,
    snapshot_date: date,
    securities: Iterable[SecurityLifecycle],
    years_read: Sequence[int] = (),
) -> StockUniverse:
    """Assemble a `StockUniverse` from registry entries, in any order.

    Refuses, rather than repairs, five things: a malformed snapshot date, an empty registry, a
    malformed entry, a duplicated `ts_code`, and a lifecycle date that is impossible against
    either the listing or the snapshot. The duplicate rule is load-bearing: a live probe found
    zero `ts_code` collisions between the listed and the delisted sets, so a collision here
    means two sources that were never reconciled, and silently keeping one of them would
    change membership for every day between the two listings.
    """
    _require_plain_date(snapshot_date, "snapshot_date")
    seen: dict[str, SecurityLifecycle] = {}
    for entry in securities:
        _require_text(entry.ts_code, "ts_code")
        _require_text(entry.exchange, "exchange")
        _require_plain_date(entry.listed_on, "listed_on")
        if entry.ts_code in seen:
            raise StockUniverseError(
                f"{entry.ts_code} appears more than once; a security has one listing history, "
                "and two rows for it mean two sources that were never reconciled"
            )
        if entry.listed_on > snapshot_date:
            raise StockUniverseError(
                f"{entry.ts_code} lists on {entry.listed_on.isoformat()}, after the "
                f"{snapshot_date.isoformat()} registry snapshot; a listing the snapshot could "
                "not have seen did not come from it"
            )
        if entry.delisted_on is not None:
            _require_plain_date(entry.delisted_on, "delisted_on")
            if entry.delisted_on <= entry.listed_on:
                raise StockUniverseError(
                    f"{entry.ts_code} is delisted on {entry.delisted_on.isoformat()}, on or "
                    f"before its own listing on {entry.listed_on.isoformat()}; delist_date is "
                    "exclusive, so the two being equal would describe a security that never "
                    "had a session"
                )
            if entry.delisted_on > snapshot_date:
                raise StockUniverseError(
                    f"{entry.ts_code} is delisted on {entry.delisted_on.isoformat()}, after "
                    f"the {snapshot_date.isoformat()} registry snapshot; a termination the "
                    "snapshot could not have seen did not come from it"
                )
        seen[entry.ts_code] = entry
    if not seen:
        raise StockUniverseError(
            "a stock universe needs at least one security; an empty registry answers 'nothing "
            "was listed' to every day, which is indistinguishable from a failed read"
        )
    return StockUniverse(
        snapshot_date=snapshot_date,
        securities=tuple(seen[key] for key in sorted(seen)),
        years_read=tuple(sorted(set(years_read))),
    )


def stock_universe_from_panel_rows(
    rows: Iterable[Sequence[object]],
    *,
    snapshot_date: date,
    years_read: Sequence[int] = (),
) -> StockUniverse:
    """Rebuild a universe from stored panel rows shaped like `UNIVERSE_PANEL_COLUMNS`.

    The counterpart of the provider's projection, and deliberately fail-closed on the shape
    the split into lifecycle rows creates: a security is assembled from a `listing` row plus
    at most one `delisting` row, and a `delisting` row whose `listing` row is absent is
    **refused**. That case is exactly what a partial read looks like -- the listing lives in
    the partition of its own snapshot and a caller who read one year and not another would
    otherwise get a smaller, entirely plausible universe -- so it raises rather than dropping
    the security or inventing a listing date for it.

    Value rules (a plain identifier, a real ISO date, a known event name) live here because
    they are about the shape of a *stored row*, which `build_stock_universe` never sees; the
    lifecycle rules live there.
    """
    listings: dict[str, tuple[date, str]] = {}
    terminations: dict[str, tuple[date, str]] = {}
    # Keyed by the pair rather than by a joined string, so a `ts_code` that happened to
    # contain the separator could not collide with another security's event.
    counts: dict[tuple[str, str], int] = {}
    for index, row in enumerate(rows):
        if len(row) != len(UNIVERSE_PANEL_COLUMNS):
            raise StockUniverseError(
                f"row {index} has {len(row)} values, expected "
                f"{len(UNIVERSE_PANEL_COLUMNS)} ({', '.join(UNIVERSE_PANEL_COLUMNS)})"
            )
        subject, event, day_text, exchange = row
        ts_code = _require_stored_text(subject, index, SUBJECT_COLUMN_NAME)
        event_name = _require_stored_text(event, index, LIFECYCLE_EVENT_COLUMN)
        venue = _require_stored_text(exchange, index, UNIVERSE_EXCHANGE_COLUMN)
        day = _parse_iso_date(day_text, index, LIFECYCLE_DATE_COLUMN)
        if event_name not in LIFECYCLE_EVENTS:
            raise StockUniverseError(
                f"row {index}: unknown lifecycle event {event_name!r}; this dataset carries "
                f"only {sorted(LIFECYCLE_EVENTS)}, and an unrecognised one cannot be ignored "
                "without changing who is in the universe"
            )
        target = listings if event_name == LISTING_EVENT else terminations
        key = (ts_code, event_name)
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > 1:
            raise StockUniverseError(
                f"{ts_code} has {counts[key]} {event_name} rows; a security lists once and "
                "ends once, so a second row is two sources that were never reconciled"
            )
        target[ts_code] = (day, venue)

    orphans = sorted(set(terminations) - set(listings))
    if orphans:
        raise StockUniverseError(
            f"{orphans[0]} has a delisting row and no listing row (with "
            f"{len(orphans)} such security/securities in this read); a listing is what gives "
            "a security a start date, so this is a partial read, not a security that appeared "
            "already delisted"
        )
    entries: list[SecurityLifecycle] = []
    for ts_code, (listed_on, venue) in listings.items():
        termination = terminations.get(ts_code)
        if termination is not None and termination[1] != venue:
            raise StockUniverseError(
                f"{ts_code} is stored against two exchanges ({venue!r} on its listing row and "
                f"{termination[1]!r} on its delisting row); one security trades on one venue, "
                "so the two rows describe different things"
            )
        entries.append(
            SecurityLifecycle(
                ts_code=ts_code,
                exchange=venue,
                listed_on=listed_on,
                delisted_on=None if termination is None else termination[0],
            )
        )
    return build_stock_universe(
        snapshot_date=snapshot_date, securities=entries, years_read=years_read
    )


def listed_universe_by_trading_day(
    universe: StockUniverse, calendar: TradingCalendar, *, start: date, end: date
) -> tuple[tuple[date, tuple[str, ...]], ...]:
    """Universe membership on every open session in `[start, end]`, for the calendar's exchange.

    The join `V2-P1-005` owes `V2-P1-004`: a universe indexed by calendar days would carry
    entries for days the exchange was shut, and a rebalance schedule built on those is a
    schedule with phantom dates in it. Sessions come from `calendar.trading_days_between`,
    which refuses a range that leaves its horizon rather than truncating it, and membership
    comes from `universe.listed_on`, which refuses a day past the snapshot -- so a range that
    either side cannot cover raises instead of returning a short list.

    Only the calendar's own exchange is reported. `TradingCalendar` is per-exchange (SSE and
    SZSE are separate series in `trade_cal`), so mixing venues into one session index would
    assert that a Shenzhen security traded on a Shanghai session. A caller who wants the whole
    market composes one calendar per venue.

    Cost is one linear pass over the universe per session -- ~1.4e6 comparisons for a full
    year of the whole A-share market, which is a second of Python and is not on the ingest
    path. A caller sweeping many years should hoist `universe.securities` into whatever index
    its own question wants rather than calling this repeatedly.
    """
    return tuple(
        (day, universe.listed_on(day, exchange=calendar.exchange))
        for day in calendar.trading_days_between(start, end)
    )


def _require_text(value: str, role: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise StockUniverseError(
            f"{role} must be a non-empty string without surrounding whitespace; got {value!r}"
        )


def _require_stored_text(value: object, index: int, column: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise StockUniverseError(
            f"row {index}: {column} must be a non-empty string without surrounding "
            f"whitespace, got {type(value).__name__} {value!r}"
        )
    return value


def _parse_iso_date(value: object, index: int, column: str) -> date:
    if not isinstance(value, str):
        raise StockUniverseError(
            f"row {index}: {column} must be an ISO date string, got "
            f"{type(value).__name__} {value!r}"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise StockUniverseError(f"row {index}: {column} is not an ISO date: {value!r}") from error


def _require_plain_date(value: date, role: str) -> None:
    """Reject anything that is not exactly a `date`.

    `datetime` subclasses `date`, so one arriving here would compare against calendar dates
    in ways that look right and are not -- `datetime(2020, 1, 7) > date(2020, 1, 7)` is True,
    which would move a delisting by a day. `domain/trading_calendar.py` guards the same thing
    at the other end of the same wire.
    """
    if type(value) is not date:
        raise StockUniverseError(
            f"{role} must be a plain datetime.date, got {type(value).__name__} {value!r}; a "
            "datetime is a date subclass and compares against dates without ever equalling one"
        )
