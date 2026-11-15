"""The five-session veto window, read off the panel (`V2-P7-027`, plan A).

`domain/survivor_veto.py` is the rule: a name is vetoed when the mean weight of its limit
events over the **five sessions ending at `t`** is bearish. `panel_limit_events.veto_window`
is the reading of that window: it assembles the sessions off the calendar and reads each one
**at its own 16:30 publication instant**, the way the evening it belongs to would have.

Three acceptances, one per boundary the rule has:

1. **Five sessions, not five days.** A window crossing a two-session exchange recess is still
   five sessions, and the newest-first order is visible in the events' own `trade_date`s.
2. **Each session is read at its own 16:30.** A bar republished after session `s`'s 16:30 is
   not knowable at `s`'s own read, so it cannot move `s`'s events; the panel store keeps no
   earlier version, so the read refuses by name rather than answering with the restated bytes.
   A later single-instant read of the same session *would* see them -- which is exactly the
   substitution the per-session instant exists to prevent.
3. **The calendar's start is refused by name.** A window whose predecessor walk runs past the
   first session the calendar knows raises `LimitEventReadError` naming the window, not a
   partial answer.

Plus the interface the window is for: `vetoed_on` is `vetoed_codes(veto_window(...))`, so the
daily annotation and the evening decision read one rule.

No network: the panel is generated at test time, written through the real writers, and read
through the real loaders.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest
from panel_fixtures import (
    LIMIT_STREAK_SECURITY_INDEX,
    GeneratedPanel,
    generate_limit_streak_panel,
    generate_panel,
    write_generated_panel,
)

from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.limit_events import LimitEvent
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET
from openalpha_cn.domain.survivor_veto import vetoed_codes
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_suspensions,
    session_publication_instant,
    write_daily_panel,
)
from openalpha_cn.panel_limit_events import (
    LimitEventReadError,
    limit_events_on,
    veto_window,
    vetoed_on,
)

# --- generated panels -----------------------------------------------------------------------

# The recess shape closes 2026-01-08 and 2026-01-09, two sessions a week. A window ending on
# 2026-01-13 then spans 2026-01-05..2026-01-13 -- nine calendar days, five sessions.
RECESSED: Final[frozenset[str]] = frozenset({"calendar.multi_session_recess"})
TARGET: Final[date] = date(2026, 1, 13)
RECESSED_WINDOW: Final[tuple[date, ...]] = (
    date(2026, 1, 13),
    date(2026, 1, 12),
    date(2026, 1, 7),
    date(2026, 1, 6),
    date(2026, 1, 5),
)


def _published(day: date) -> datetime:
    return session_publication_instant(day)


def _stored(tmp_path: Path, panel: GeneratedPanel) -> PanelStore:
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, panel)
    return store


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
) -> ColumnarPanelBatch:
    """`batch` with per-row `overrides` and, for named sessions, a later revision clock."""
    dates = _row_indices(batch, date_column=date_column)
    revision = tuple(
        later_revision.get(date_text, clock)
        for date_text, clock in zip(dates, batch.timeline.revision_time, strict=True)
    )
    as_of = max([batch.as_of, *revision])
    columns = tuple(
        PanelColumn(
            column.name,
            column.kind,
            tuple(
                overrides.get((subject, date_text), {}).get(column.name, value)
                for subject, date_text, value in zip(
                    batch.subjects, dates, column.values, strict=True
                )
            ),
        )
        for column in batch.columns
    )
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
        columns=columns,
    )


def _scripted(
    panel: GeneratedPanel,
    *,
    code: str,
    rows: Mapping[date, tuple[float, float, float, float]],
) -> GeneratedPanel:
    """`panel` with one security's prices scripted on named sessions.

    `rows` maps a session to `(pre_close, close, high, low)`; the published band is derived from
    `pre_close` exactly as `generate_limit_streak_panel` derives it, so `close == up_limit` is a
    limit-up and `high >= up_limit > close` is a broken board. Everything else the generator
    built is carried across untouched.
    """
    bars: dict[tuple[str, str], dict[str, object]] = {}
    values: dict[tuple[str, str], dict[str, object]] = {}
    bands: dict[tuple[str, str], dict[str, object]] = {}
    for day, (pre_close, close, high, low) in rows.items():
        key = (code, day.isoformat())
        bars[key] = {
            "open": low,
            "high": high,
            "low": low,
            "close": close,
            "pre_close": pre_close,
            "pct_chg": (close / pre_close - 1.0) * 100.0,
        }
        values[key] = {"close": close}
        bands[key] = {
            "up_limit": round(pre_close * 1.1, 2),
            "down_limit": round(pre_close * 0.9, 2),
        }
    batches = dict(panel.batches)
    batches[DAILY_DATASET] = _rewritten(
        panel.batch(DAILY_DATASET), date_column="trade_date", overrides=bars
    )
    batches[DAILY_BASIC_DATASET] = _rewritten(
        panel.batch(DAILY_BASIC_DATASET), date_column="trade_date", overrides=values
    )
    batches[PRICE_LIMIT_DATASET] = _rewritten(
        panel.batch(PRICE_LIMIT_DATASET), date_column="trade_date", overrides=bands
    )
    return replace(panel, batches=MappingProxyType(batches))


def _only(events: tuple[LimitEvent, ...]) -> LimitEvent:
    assert len(events) == 1, events
    return events[0]


# --- 1. five sessions across a holiday ------------------------------------------------------


def test_the_window_is_five_sessions_when_it_crosses_a_holiday(tmp_path: Path) -> None:
    """The recess spans two sessions; the window is still the five sessions ending at `t`."""
    panel = generate_panel(shapes=RECESSED)
    scripted = _scripted(
        panel,
        code=panel.securities[0],
        rows={
            date(2026, 1, 5): (10.0, 11.0, 11.0, 10.2),
            date(2026, 1, 6): (11.0, 12.1, 12.1, 12.1),
            date(2026, 1, 7): (12.1, 13.31, 13.31, 12.5),
            date(2026, 1, 12): (13.31, 14.64, 14.64, 13.5),
            date(2026, 1, 13): (14.64, 16.1, 16.1, 14.8),
        },
    )
    store = _stored(tmp_path, scripted)

    window = veto_window(
        store, session=TARGET, calendar=scripted.calendar(), exchange=scripted.exchange
    )

    assert len(window) == 5
    # The events carry their own session, so a day-shifted window cannot pass this: newest
    # first, and the two middle sessions are a week apart rather than a day.
    assert [events[0].trade_date for events in window] == list(RECESSED_WINDOW)
    assert (RECESSED_WINDOW[0] - RECESSED_WINDOW[-1]).days == 8


# --- 2. each session is read at its own 16:30 -----------------------------------------------


def _restate(
    store: PanelStore,
    panel: GeneratedPanel,
    *,
    day: date,
    revision_time: datetime,
    daily: Mapping[str, object],
) -> None:
    """Republish `day`'s bars and valuations with a later revision clock."""
    key = (panel.securities[LIMIT_STREAK_SECURITY_INDEX], day.isoformat())
    bars = _rewritten(
        panel.batch(DAILY_DATASET),
        date_column="trade_date",
        overrides={key: daily},
        later_revision={day.isoformat(): revision_time},
    )
    valuations = _rewritten(
        panel.batch(DAILY_BASIC_DATASET),
        date_column="trade_date",
        overrides={key: {"close": daily["close"]}},
        later_revision={day.isoformat(): revision_time},
    )
    halts = load_suspensions(store, years=(panel.year,), as_of=panel.as_of, max_staleness=None)
    write_daily_panel(
        store, bars=[bars], fundamentals=[valuations], calendar=panel.calendar(), halts=halts
    )


def test_a_row_restated_after_the_sessions_1630_does_not_change_that_sessions_events(
    tmp_path: Path,
) -> None:
    """The evening's answer for `s` cannot move: `s` is read at its own 16:30.

    The panel store keeps no earlier version of a restated row, so "not changed" is
    fail-closed rather than byte-preserving: the read at the session's own instant refuses by
    name instead of handing back the restated rows. The late read below shows the event the
    restatement would have substituted -- the change a single `as_of` for the whole window
    would have let into the evening.
    """
    panel = generate_limit_streak_panel()
    store = _stored(tmp_path, panel)
    target = panel.sessions[5]  # 2026-01-12: the scripted broken board
    before = veto_window(store, session=target, calendar=panel.calendar(), exchange=panel.exchange)
    assert _only(before[0]).kind == "broken_board"

    restated_at = _published(target) + timedelta(hours=2)
    _restate(
        store,
        panel,
        day=target,
        revision_time=restated_at,
        daily={
            "open": 13.31,
            "high": 13.60,
            "low": 13.30,
            "close": 13.31,
            "pre_close": 13.31,
            "pct_chg": 0.0,
        },
    )

    # The session read at its own 16:30 cannot see the restatement: refused, not substituted.
    with pytest.raises(PanelStorageError) as raised:
        veto_window(store, session=target, calendar=panel.calendar(), exchange=panel.exchange)
    assert target.isoformat() in str(raised.value)

    # A later single-instant read does see it, and reports no event -- the different answer the
    # per-session read exists to keep out of the evening.
    late = limit_events_on(
        store,
        session=target,
        as_of=restated_at + timedelta(hours=1),
        exchange=panel.exchange,
    )
    assert late == ()
    assert _only(before[0]).trade_date == target


# --- 3. the calendar's start is refused by name ---------------------------------------------


def test_a_window_reaching_the_start_of_the_calendar_is_refused_by_name(tmp_path: Path) -> None:
    """The first session of the generated calendar has no four predecessors: refused."""
    panel = generate_limit_streak_panel()
    store = _stored(tmp_path, panel)
    first = panel.sessions[0]

    with pytest.raises(LimitEventReadError) as raised:
        veto_window(store, session=first, calendar=panel.calendar(), exchange=panel.exchange)

    message = str(raised.value)
    assert f"5-session window ending {first.isoformat()}" in message
    assert "start of the calendar" in message


# --- the interface: the veto is the window's rule -------------------------------------------


def test_vetoed_on_is_the_rule_applied_to_the_window(tmp_path: Path) -> None:
    """One broken board and nothing else in the window is the rule's own bearish case."""
    panel = generate_panel(shapes=RECESSED)
    code = panel.securities[0]
    scripted = _scripted(
        panel,
        code=code,
        rows={TARGET: (10.0, 9.8, 11.0, 9.7)},
    )
    store = _stored(tmp_path, scripted)
    calendar = scripted.calendar()

    window = veto_window(store, session=TARGET, calendar=calendar, exchange=scripted.exchange)
    vetoed = vetoed_on(store, session=TARGET, calendar=calendar, exchange=scripted.exchange)

    assert vetoed == frozenset({code})
    assert vetoed == vetoed_codes(window)


# --- R-N3 on an *earlier* session: the window does not read the whole window at `t`'s 16:30 ---


def test_an_earlier_sessions_restatement_is_not_visible_at_that_sessions_own_1630(
    tmp_path: Path,
) -> None:
    """The window reads `t-2` at `t-2`'s own 16:30, not at `t`'s.

    `t-2`'s bars are republished after `t-2`'s 16:30 and before `t`'s, so they are visible at
    `t`'s instant -- and there they imply a *different* event. Read at `t-2`'s own evening, the
    instant the window uses, they are not knowable, so `veto_window` refuses by name instead of
    answering with the restated event. A window that read every session at `t`'s 16:30 would
    report the restated broken board and raise nothing here.
    """
    panel = generate_limit_streak_panel()
    store = _stored(tmp_path, panel)
    target = panel.sessions[6]  # 2026-01-13; its window is 13, 12, 09, 08, 07
    earlier = panel.sessions[4]  # 2026-01-09 = t-2, the scripted consecutive board
    original = limit_events_on(
        store, session=earlier, as_of=_published(earlier), exchange=panel.exchange
    )
    assert _only(original).kind == "consecutive_board"

    restated_at = _published(earlier) + timedelta(hours=2)  # after t-2's 16:30 ...
    assert restated_at < _published(target)  # ... and before t's
    _restate(
        store,
        panel,
        day=earlier,
        revision_time=restated_at,
        daily={
            "open": 12.50,
            "high": 13.35,  # >= the published up_limit 13.31
            "low": 12.50,
            "close": 12.90,  # and closes below it: a broken board
            "pre_close": 12.10,
            "pct_chg": (12.90 / 12.10 - 1.0) * 100.0,
        },
    )

    # The window reads `t-2` at `t-2`'s own 16:30: the restatement is not knowable there, so
    # the whole window is refused and names the session that cannot be answered.
    with pytest.raises(PanelStorageError) as raised:
        veto_window(store, session=target, calendar=panel.calendar(), exchange=panel.exchange)
    assert earlier.isoformat() in str(raised.value)

    # Read at `t`'s 16:30 the same session answers with the restated event, which is what a
    # single-instant window would have put into the evening.
    at_targets_instant = limit_events_on(
        store, session=earlier, as_of=_published(target), exchange=panel.exchange
    )
    assert _only(at_targets_instant).kind == "broken_board"
    assert at_targets_instant != original
