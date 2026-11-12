"""The panel adapter (`V2-P7-011`): limit events as the panel held them at a session's 16:30.

Six acceptances, and the fourth and fifth are the ones that decide whether this producer is
honest rather than merely correct on one panel.

1. **Before the session's 16:30 nothing is read.** The session's rows do not exist yet, and a
   producer that read them would be reporting a close before it happened.
2. **The hand-labelled panel reproduces every day.** The fixture's scripted security walks the
   eight sessions of `tests/unit/domain/test_limit_events.py`'s table: up, one-price-up, halt,
   up, touched-not-closed, up, flat, up.
3. **A replay at each session's own evening equals one late read of an un-restated panel.**
   Every read takes the caller's `as_of`, so a panel that has not been restated answers the same
   event whether it is asked on the evening or a week later.
4. **A streak crossing a year boundary reads the prior year's calendar.** A January session's
   walk must reach December of the year before; the panel holds two partition years and the
   fixture is built here rather than generated, because the generator prices one year.
5. **A restated earlier bar is invisible to a replay at the original `as_of`.** The restatement
   is written with a later revision instant, so the earlier session is withheld at the original
   evening: the producer refuses by name instead of answering from bytes that did not exist yet,
   and a *late* read sees the restated bar and reports the different event it now implies.
6. **Only securities with an event come back.** The panel prices eight securities a session and
   the scripted one walks the eight-session table, so the answer is a handful of rows out of
   eighty -- hard rule 1's "events only, never panel rows", as a count.

No network: the panel is generated at test time, written through the real writers, and read
through the real loaders.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest
from panel_fixtures import (
    DAILY_BASIC_EXTRA_COLUMNS,
    EXCHANGE,
    LIMIT_STREAK_FIRST_SESSION_INDEX,
    LIMIT_STREAK_SECURITY_INDEX,
    GeneratedPanel,
    generate_limit_streak_panel,
    write_generated_panel,
)

from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.limit_events import LimitEvent
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_suspensions,
    session_publication_instant,
    write_daily_panel,
    write_price_limits,
    write_trading_calendar,
)
from openalpha_cn.panel_limit_events import (
    DEFAULT_EXCHANGE,
    LimitEventReadError,
    limit_events_on,
)
from openalpha_cn.providers.tushare import TRADING_CALENDAR_DEFAULT_EXCHANGE


def _published(day: date) -> datetime:
    return session_publication_instant(day)


def _stored(tmp_path: Path, panel: GeneratedPanel) -> PanelStore:
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, panel)
    return store


def _replay(store: PanelStore, panel: GeneratedPanel) -> dict[date, tuple[LimitEvent, ...]]:
    """Every session read at its own 16:30 -- the producer's normal evening run."""
    return {
        day: limit_events_on(store, session=day, as_of=_published(day), exchange=panel.exchange)
        for day in panel.sessions
    }


def _only(events: tuple[LimitEvent, ...]) -> LimitEvent:
    assert len(events) == 1, events
    return events[0]


def test_before_the_sessions_1630_the_producer_is_refused_by_name(tmp_path: Path) -> None:
    panel = generate_limit_streak_panel()
    store = _stored(tmp_path, panel)
    day = panel.sessions[LIMIT_STREAK_FIRST_SESSION_INDEX + 1]
    published = _published(day)

    with pytest.raises(LimitEventReadError) as raised:
        limit_events_on(
            store,
            session=day,
            as_of=published - timedelta(seconds=1),
            exchange=panel.exchange,
        )

    message = str(raised.value)
    assert day.isoformat() in message
    assert published.isoformat() in message
    assert "16:30" in message


def test_at_the_sessions_1630_the_hand_labelled_panel_reproduces_every_day(
    tmp_path: Path,
) -> None:
    panel = generate_limit_streak_panel()
    store = _stored(tmp_path, panel)
    replay = _replay(store, panel)

    first = LIMIT_STREAK_FIRST_SESSION_INDEX
    # The eight rows of the domain test's table, keyed by the fixture's session index.
    table: tuple[tuple[str, int], ...] = (
        ("limit_up", 1),
        ("consecutive_board", 2),
        ("", 0),  # the halt session
        ("consecutive_board", 3),
        ("broken_board", 3),
        ("limit_up", 1),
        ("", 0),  # an ordinary session
        ("limit_up", 1),
    )
    expected = {index: ("", 0) for index in range(len(panel.sessions))}
    expected.update({first + offset: (kind, count) for offset, (kind, count) in enumerate(table)})
    produced = {
        index: ((replay[day][0].kind, replay[day][0].board_count) if replay[day] else ("", 0))
        for index, day in enumerate(panel.sessions)
    }
    assert produced == expected

    assert _only(replay[panel.sessions[first + 1]]).one_price is True
    halt_skipped = _only(replay[panel.sessions[first + 3]])
    assert halt_skipped.halted_sessions_skipped == 1
    assert halt_skipped.board_count_is_lower_bound is False
    broken = _only(replay[panel.sessions[first + 4]])
    assert broken.kind == "broken_board"
    assert broken.facts()["prior_board_count"] == 3


def test_replaying_history_session_by_session_equals_one_late_read_of_an_unrestated_panel(
    tmp_path: Path,
) -> None:
    panel = generate_limit_streak_panel()
    store = _stored(tmp_path, panel)
    replay = _replay(store, panel)

    late = {
        day: limit_events_on(store, session=day, as_of=panel.as_of, exchange=panel.exchange)
        for day in panel.sessions
    }
    assert late == replay


# --- the two-year panel --------------------------------------------------------------------


A: Final[str] = "000001.SZ"
B: Final[str] = "600000.SH"
SECURITIES: Final[tuple[str, ...]] = (A, B)
BASE: Final[float] = 10.0

CLOSED_DAYS: Final[frozenset[date]] = frozenset(
    {date(2025, 1, 1), date(2026, 1, 1), date(2026, 1, 2)}
)


def _days(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _is_open(day: date) -> bool:
    return day.weekday() < 5 and day not in CLOSED_DAYS


def _close_time(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(hours=7)


def _published_time(day: date) -> datetime:
    """16:30 Asia/Shanghai on `day` -- the instant the session became knowable."""
    return datetime(day.year, day.month, day.day, 8, 30, tzinfo=UTC)


def _calendar_batch(days: Sequence[date]) -> ColumnarPanelBatch:
    previous: list[str | None] = []
    seen: date | None = None
    for day in days:
        previous.append(seen.isoformat() if seen is not None else None)
        if _is_open(day):
            seen = day
    midnight = datetime(days[0].year, days[0].month, days[0].day, tzinfo=UTC) - timedelta(hours=8)
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset=TRADING_CALENDAR_DATASET,
        kind=TRADING_CALENDAR_DATASET,
        as_of=midnight + timedelta(hours=1),
        fetched_at=midnight + timedelta(hours=1),
        status="success",
        subjects=(EXCHANGE,) * len(days),
        timeline=TimelineColumns(
            event_time=tuple(midnight for _ in days),
            available_time=(midnight,) * len(days),
            ingested_time=(midnight,) * len(days),
            revision_time=(midnight,) * len(days),
        ),
        columns=(
            PanelColumn("cal_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("is_open", "boolean", tuple(_is_open(day) for day in days)),
            PanelColumn("pretrade_date", "string", tuple(previous)),
        ),
    )


def _pre_and_close(code: str, day: date) -> tuple[float, float]:
    """Security A's scripted path across the boundary; B never moves."""
    if code != A:
        return (BASE, BASE)
    scripted: dict[date, tuple[float, float]] = {
        date(2025, 12, 29): (10.0, 11.0),
        date(2025, 12, 30): (11.0, 12.1),
        date(2025, 12, 31): (12.1, 13.31),
        date(2026, 1, 5): (13.31, 14.64),
        date(2026, 1, 6): (14.64, 14.64),
    }
    return scripted.get(day, (BASE, BASE))


def _price_batches(
    sessions: Sequence[date], *, fetched_at: datetime
) -> tuple[ColumnarPanelBatch, ColumnarPanelBatch, ColumnarPanelBatch]:
    pairs = [(day, code) for day in sessions for code in SECURITIES]
    prices = {pair: _pre_and_close(pair[1], pair[0]) for pair in pairs}
    pre_close = tuple(prices[pair][0] for pair in pairs)
    close = tuple(prices[pair][1] for pair in pairs)
    daily = ColumnarPanelBatch(
        provider_id="tushare",
        dataset=DAILY_DATASET,
        kind=DAILY_DATASET,
        as_of=fetched_at,
        fetched_at=fetched_at,
        status="success",
        subjects=tuple(code for _, code in pairs),
        timeline=TimelineColumns(
            event_time=tuple(_close_time(day) for day, _ in pairs),
            available_time=tuple(_published_time(day) for day, _ in pairs),
            ingested_time=tuple(_published_time(day) for day, _ in pairs),
            revision_time=tuple(_published_time(day) for day, _ in pairs),
        ),
        columns=(
            PanelColumn("trade_date", "string", tuple(d.isoformat() for d, _ in pairs)),
            PanelColumn("open", "float", close),
            PanelColumn("high", "float", close),
            PanelColumn("low", "float", close),
            PanelColumn("close", "float", close),
            PanelColumn("pre_close", "float", pre_close),
            PanelColumn(
                "pct_chg",
                "float",
                tuple(
                    (after / before - 1.0) * 100.0
                    for after, before in zip(close, pre_close, strict=True)
                ),
            ),
            PanelColumn("vol", "float", tuple(1000.0 for _ in close)),
            PanelColumn("amount", "float", tuple(10000.0 for _ in close)),
        ),
    )
    basic = ColumnarPanelBatch(
        provider_id="tushare",
        dataset=DAILY_BASIC_DATASET,
        kind=DAILY_BASIC_DATASET,
        as_of=fetched_at,
        fetched_at=fetched_at,
        status="success",
        subjects=tuple(code for _, code in pairs),
        timeline=daily.timeline,
        columns=(
            PanelColumn("trade_date", "string", tuple(d.isoformat() for d, _ in pairs)),
            PanelColumn("close", "float", close),
            *(
                PanelColumn(name, "float", tuple(1.0 for _ in close))
                for name in DAILY_BASIC_EXTRA_COLUMNS
            ),
        ),
    )
    limits = ColumnarPanelBatch(
        provider_id="tushare",
        dataset=PRICE_LIMIT_DATASET,
        kind=PRICE_LIMIT_DATASET,
        as_of=fetched_at,
        fetched_at=fetched_at,
        status="success",
        subjects=tuple(code for _, code in pairs),
        timeline=daily.timeline,
        columns=(
            PanelColumn("trade_date", "string", tuple(d.isoformat() for d, _ in pairs)),
            PanelColumn("up_limit", "float", tuple(round(before * 1.1, 2) for before in pre_close)),
            PanelColumn(
                "down_limit", "float", tuple(round(before * 0.9, 2) for before in pre_close)
            ),
        ),
    )
    return daily, basic, limits


def _two_year_store(tmp_path: Path) -> PanelStore:
    """A store whose calendar and prices span 2025-12 -> 2026-01.

    2025 is stored whole because `daily_requirement` requires every session of a year it
    reads; 2026 stores only the sessions this test asks about.
    """
    store = PanelStore(tmp_path / "panel")
    days_2025 = _days(date(2025, 1, 1), date(2025, 12, 31))
    days_2026 = _days(date(2026, 1, 1), date(2026, 1, 8))
    write_trading_calendar(store, _calendar_batch(days_2025))
    write_trading_calendar(store, _calendar_batch(days_2026))
    calendar_2025 = _calendar(days_2025)
    calendar_2026 = _calendar(days_2026)
    sessions_2025 = tuple(day for day in days_2025 if _is_open(day))
    sessions_2026 = (date(2026, 1, 5), date(2026, 1, 6))
    fetched_at = datetime(2026, 1, 6, 9, 0, tzinfo=UTC)
    daily, basic, limits = _price_batches(sessions_2025, fetched_at=fetched_at)
    write_daily_panel(store, bars=[daily], fundamentals=[basic], calendar=calendar_2025, halts=None)
    write_price_limits(store, [limits], calendar=calendar_2025)
    daily, basic, limits = _price_batches(sessions_2026, fetched_at=fetched_at)
    write_daily_panel(store, bars=[daily], fundamentals=[basic], calendar=calendar_2026, halts=None)
    write_price_limits(store, [limits], calendar=calendar_2026)
    return store


def _calendar(days: Sequence[date]) -> TradingCalendar:
    return build_trading_calendar(
        EXCHANGE, (CalendarDay(calendar_date=day, is_trading=_is_open(day)) for day in days)
    )


def test_a_streak_crossing_a_year_boundary_reads_the_prior_years_calendar(
    tmp_path: Path,
) -> None:
    store = _two_year_store(tmp_path)
    target = date(2026, 1, 5)
    events = limit_events_on(store, session=target, as_of=_published(target), exchange=EXCHANGE)

    event = _only(events)
    assert (event.ts_code, event.kind) == (A, "consecutive_board")
    # 2025-12-29, 12-30 and 12-31 all closed at their band; the walk had to read the prior
    # year's calendar to find them, because 2026-01-01 and 01-02 are closed.
    assert event.board_count == 4
    assert event.board_count_is_lower_bound is False


# --- restatement ---------------------------------------------------------------------------


def _row_indices(batch: ColumnarPanelBatch, *, date_column: str) -> tuple[str, ...]:
    for column in batch.columns:
        if column.name == date_column:
            return tuple(str(value) for value in column.values)
    raise AssertionError(f"{batch.dataset} has no {date_column} column")


def _rewritten(
    batch: ColumnarPanelBatch,
    *,
    date_column: str,
    overrides: Mapping[tuple[str, str], Mapping[str, object]] = MappingProxyType({}),
    later_revision: Mapping[str, datetime] = MappingProxyType({}),
    fetched_at: datetime | None = None,
) -> ColumnarPanelBatch:
    """Rebuild `batch` with per-row overrides and, for named sessions, a later revision clock."""
    dates = _row_indices(batch, date_column=date_column)
    revision = tuple(
        later_revision.get(date_text, clock)
        for date_text, clock in zip(dates, batch.timeline.revision_time, strict=True)
    )
    as_of = max([batch.as_of, fetched_at or batch.as_of, *revision])
    columns: list[PanelColumn] = []
    for column in batch.columns:
        values = tuple(
            overrides.get((subject, date_text), {}).get(column.name, value)
            for subject, date_text, value in zip(batch.subjects, dates, column.values, strict=True)
        )
        columns.append(PanelColumn(column.name, column.kind, values))
    return ColumnarPanelBatch(
        provider_id=batch.provider_id,
        dataset=batch.dataset,
        kind=batch.kind,
        as_of=as_of,
        fetched_at=as_of,
        status="success",
        subjects=batch.subjects,
        timeline=TimelineColumns(
            event_time=batch.timeline.event_time,
            available_time=batch.timeline.available_time,
            ingested_time=batch.timeline.ingested_time,
            revision_time=revision,
        ),
        columns=tuple(columns),
    )


def test_a_restated_earlier_bar_is_invisible_to_a_replay_at_the_original_as_of(
    tmp_path: Path,
) -> None:
    panel = generate_limit_streak_panel()
    store = _stored(tmp_path, panel)
    first = LIMIT_STREAK_FIRST_SESSION_INDEX
    code = panel.securities[LIMIT_STREAK_SECURITY_INDEX]
    target = panel.sessions[first + 1]  # the one-price-up session, whose count reads d1
    restated_day = panel.sessions[first]  # d1, the bar the count depends on

    baseline = _only(
        limit_events_on(store, session=target, as_of=_published(target), exchange=EXCHANGE)
    )
    assert (baseline.kind, baseline.board_count) == ("consecutive_board", 2)

    restated_at = datetime(2026, 1, 17, 20, 0, tzinfo=UTC)
    daily = _rewritten(
        panel.batch(DAILY_DATASET),
        date_column="trade_date",
        overrides={
            (code, restated_day.isoformat()): {
                "open": 10.50,
                "high": 10.60,
                "low": 10.20,
                "close": 10.50,
                "pre_close": 10.00,
                "pct_chg": 5.0,
            }
        },
        later_revision={restated_day.isoformat(): restated_at},
        fetched_at=restated_at,
    )
    basic = _rewritten(
        panel.batch(DAILY_BASIC_DATASET),
        date_column="trade_date",
        overrides={(code, restated_day.isoformat()): {"close": 10.50}},
        later_revision={restated_day.isoformat(): restated_at},
        fetched_at=restated_at,
    )
    halts = load_suspensions(store, years=(panel.year,), as_of=panel.as_of, max_staleness=None)
    write_daily_panel(
        store, bars=[daily], fundamentals=[basic], calendar=panel.calendar(), halts=halts
    )

    # The original evening cannot see the restatement: the earlier session's rows were
    # re-published after that instant, so the producer refuses rather than answering from bytes
    # that did not exist then.
    with pytest.raises(PanelStorageError) as raised:
        limit_events_on(store, session=target, as_of=_published(target), exchange=EXCHANGE)
    assert restated_day.isoformat() in str(raised.value)

    # A late read does see it, and then the event the restated bar implies is the honest one.
    late = _only(
        limit_events_on(
            store,
            session=target,
            as_of=datetime(2026, 1, 18, 4, 0, tzinfo=UTC),
            exchange=EXCHANGE,
        )
    )
    assert (late.kind, late.board_count) == ("limit_up", 1)


# --- the D1 guard --------------------------------------------------------------------------


def test_only_securities_with_an_event_are_returned_and_the_count_is_bounded(
    tmp_path: Path,
) -> None:
    panel = generate_limit_streak_panel()
    store = _stored(tmp_path, panel)
    code = panel.securities[LIMIT_STREAK_SECURITY_INDEX]
    replay = _replay(store, panel)

    produced = [event for day in panel.sessions for event in replay[day]]
    # Eight securities priced for ten sessions is eighty rows; the answer is six events.
    assert len(panel.securities) * len(panel.sessions) == 80
    assert 0 < len(produced) < 80 // 10
    assert {event.ts_code for event in produced} == {code}
    assert all(len(replay[day]) <= 1 for day in panel.sessions)
    assert {event.kind for event in produced} == {
        "limit_up",
        "consecutive_board",
        "broken_board",
    }


def test_the_default_exchange_is_the_providers_default() -> None:
    """The local literal is a restatement of one constant, so a move on either side is red."""
    assert DEFAULT_EXCHANGE == TRADING_CALENDAR_DEFAULT_EXCHANGE
