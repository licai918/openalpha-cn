"""Limit events read off the panel at a session's 16:30, replayable at any later `as_of`.

`domain/limit_events.py` is the rule; this module is the reading. It answers one question --
*which securities closed at their published limit on this session, and for how many sessions in
a row* -- from the panel's `daily`, `stk_limit` and `trade_cal` partitions, through the loaders
that own each of them rather than a second door onto the same rows.

## One `as_of` for every read, which is the whole point-in-time argument

A caller reads a session's events at an instant; every read this function makes -- the target
session, each earlier session the streak walk reaches, and the calendar that names them -- is
taken at that same instant through the loaders' own point-in-time doors. So a replay at
session D's evening cannot see a row published after D's 16:30 even when the panel has since
been restated, and a replay at each session's own evening equals one late read of a panel that
was never restated. The adapter does not pick an `as_of` per session; the caller states one and
it is used everywhere.

**Before the session's own 16:30 nothing is read.** `load_daily_bars` would refuse the read
anyway (a session becomes knowable at `DAILY_AVAILABILITY_TIME`), but refusing here names the
*event producer* rather than the loader, which is what a caller holding a bad schedule needs to
see.

## The lazy walk

The target session is read first; only the securities with a touch on it can produce an event.
Earlier open sessions are then read **only while some candidate is still alive** -- still
`closed_at_up`, or halted, on the newest session read -- and never more than `max_lookback` of
them. That is what keeps a day's producer cost proportional to the day's events rather than to
the market: a security with no touch today is never walked, and a security whose streak ended
one session back costs one earlier read.

**A walk that reaches the start of the year reads the prior year's `trade_cal`.** The target
session's own year is loaded first; when the walk needs a session the calendar does not cover,
the prior year (and then the one before, if the cap allows) is loaded and the walk continues.
That is exact rather than generous: a January session's streak may genuinely have begun in
December, and a calendar that stopped at 1 January would report a one-session board for a
three-session one.

**The edge of the stored panel ends the walk and lowers the bound, it does not lose the day**
(fix round 1, finding 3). On a January session of the earliest year the panel stores, a
candidate still alive at 1 January needs a prior year that may not be stored at all; that used
to raise and cost every event of the session, including the ones whose walk never went near the
boundary. A prior-year calendar or price partition that cannot be read now ends the walk and
marks the still-alive candidates' `board_count_is_lower_bound` -- the same "report the bound"
rule `MAX_BOARD_LOOKBACK_SESSIONS` already follows. A partition missing **inside** the stored
range is still a refusal: the panel claims that year and cannot answer for it.

`stk_limit`'s band is read for every walked session too, because the halt rule is a fact about
the pair -- a published band with no bar is a halt (skipped, D2), while no band at all means the
security is not listed and the walk ends. See `domain/limit_events.py`.

## Layering

Top-level beside `panel_ingest`, for the same reason: it joins `domain` (the rule and the
contracts), `panel` (`PanelStore`) and `panel_ingest` (the loaders and the publication clock),
and `openalpha_cn.panel` itself may import no sibling.
`tests/unit/test_panel_ingest_import_isolation.py` pins the dependency row, the names taken
across the seam and the datasets they can reach.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Final

from openalpha_cn.domain.limit_events import (
    MAX_BOARD_LOOKBACK_SESSIONS,
    LimitEvent,
    SessionLimitState,
    session_limit_events,
    session_state,
)
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.trading_calendar import (
    CalendarHorizonError,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.panel.store import PanelStorageError, PanelStore
from openalpha_cn.panel_ingest import (
    load_daily_bars,
    load_price_limits,
    load_trading_calendar,
    session_publication_instant,
)

DEFAULT_EXCHANGE: Final[str] = "SSE"
"""The exchange a caller gets when it names none -- `providers/tushare.py`'s own default.

A local literal rather than an import: this module may not reach `openalpha_cn.providers`
(it is the research plane, and its dependency row is `domain` + `panel` + `panel_ingest`), so
the value is restated here and pinned to the provider's constant by
`tests/integration/panel/test_limit_event_producer.py` rather than inferred from it.
"""


class LimitEventReadError(ValueError):
    """Raised when the producer is asked for a session it must not read, naming the reason."""


def limit_events_on(
    store: PanelStore,
    *,
    session: date,
    as_of: datetime,
    exchange: str = DEFAULT_EXCHANGE,
    max_lookback: int = MAX_BOARD_LOOKBACK_SESSIONS,
) -> tuple[LimitEvent, ...]:
    """Every limit event `session` carries, as the panel held it at `as_of`.

    Reads at `as_of` only: the session must already have published (16:30 Asia/Shanghai), and
    every earlier read the streak walk makes uses the same instant. `exchange` names the
    calendar the sessions are counted on; `max_lookback` caps how many earlier sessions one
    walk may read (`domain.limit_events.MAX_BOARD_LOOKBACK_SESSIONS` by default).
    """
    if max_lookback < 0:
        raise LimitEventReadError(f"max_lookback cannot be negative; got {max_lookback}")
    point = ensure_aware(as_of)
    published = session_publication_instant(session)
    if point < published:
        raise LimitEventReadError(
            f"{session.isoformat()} publishes at {published.isoformat()} (16:30 Asia/Shanghai) "
            f"and the read is at {point.isoformat()}; its limit events did not exist yet, and "
            "answering them from an earlier read would report a close before it happened"
        )

    calendar = load_trading_calendar(store, exchange=exchange, years=(session.year,), as_of=point)
    if not calendar.is_trading_day(session):
        raise LimitEventReadError(
            f"{session.isoformat()} is not an open {exchange} session on the calendar read at "
            f"{point.isoformat()}, so it has no limit events"
        )

    bars = load_daily_bars(store, day=session, calendar=calendar, as_of=point, max_staleness=None)
    limits = load_price_limits(
        store, day=session, calendar=calendar, as_of=point, max_staleness=None
    )
    today = session_state(bars, limits, trade_date=session)
    candidates = frozenset(
        ts_code for ts_code, touch in today.touches.items() if touch.closed_at_up or touch.at_up
    )
    earlier, truncated_alive = _walk_earlier_sessions(
        store,
        today=today,
        candidates=candidates,
        calendar=calendar,
        session=session,
        as_of=point,
        exchange=exchange,
        max_lookback=max_lookback,
    )
    return session_limit_events(
        today,
        bars,
        earlier,
        max_lookback=max_lookback,
        truncated_alive=truncated_alive,
    )


def _walk_earlier_sessions(
    store: PanelStore,
    *,
    today: SessionLimitState,
    candidates: frozenset[str],
    calendar: TradingCalendar,
    session: date,
    as_of: datetime,
    exchange: str,
    max_lookback: int,
) -> tuple[tuple[SessionLimitState, ...], frozenset[str]]:
    """Prior sessions newest-first, read lazily while a candidate can still extend its streak.

    Returns the states read and the candidates whose streak was still alive when the walk ended
    at the **edge of the stored panel** (a prior year whose calendar or prices cannot be read).
    The caller reports those candidates' board count as a lower bound instead of losing the
    session; a partition missing **inside the target year** is still raised, because there the
    panel claims the year and cannot answer for it.
    """
    if not candidates or max_lookback == 0:
        return (), frozenset()
    earlier: list[SessionLimitState] = []
    alive = candidates
    current = session
    first_year = session.year
    while alive and len(earlier) < max_lookback:
        try:
            previous = calendar.previous_trading_day(current)
        except CalendarHorizonError:
            first_year -= 1
            if first_year < date.min.year:
                return tuple(earlier), frozenset(alive)
            try:
                calendar = load_trading_calendar(
                    store,
                    exchange=exchange,
                    years=tuple(range(first_year, session.year + 1)),
                    as_of=as_of,
                )
            except (TradingCalendarError, PanelStorageError):
                # The panel does not store (or cannot read) the year before the target year:
                # the walk has reached the edge of what exists, not a corrupt stretch inside it.
                return tuple(earlier), frozenset(alive)
            previous = calendar.previous_trading_day(current)
        try:
            bars = load_daily_bars(
                store, day=previous, calendar=calendar, as_of=as_of, max_staleness=None
            )
            limits = load_price_limits(
                store, day=previous, calendar=calendar, as_of=as_of, max_staleness=None
            )
        except (PanelStorageError, TradingCalendarError):
            if previous.year < session.year:
                # A prior-year partition the calendar knows about but the store cannot read:
                # still the year-boundary edge, so the count is a lower bound rather than lost.
                return tuple(earlier), frozenset(alive)
            raise
        state = session_state(bars, limits, trade_date=previous)
        earlier.append(state)
        alive = frozenset(ts_code for ts_code in alive if _still_alive(ts_code, state))
        current = previous
    return tuple(earlier), frozenset()


def _still_alive(ts_code: str, state: SessionLimitState) -> bool:
    """Whether `ts_code`'s streak can reach past `state`: listed, and either up or halted.

    A session with no band ends the walk (`stk_limit` does not carry the security, so there is
    no history to walk); a session where it traded and did not close at its limit ends it too;
    a halt (band, no bar) is stepped over.
    """
    if ts_code not in state.banded:
        return False
    touch = state.touches.get(ts_code)
    return touch is None or touch.closed_at_up
