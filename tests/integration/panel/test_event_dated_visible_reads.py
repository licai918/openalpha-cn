"""Withheld against absent, once per dataset that joined the filtered door (`V2-P4-076`).

`load_stock_universe`, `load_suspensions` and `load_name_histories` read whole years, and since
this issue they read them through `_read_visible_event_dated_rows` rather than through
`read_if_ready`. The objection `tests/unit/panel/test_visible_read_callers.py` puts to every
caller of `read_visible_at` is *can this caller tell a withheld row from an absent one*, and this
file is the executable half of the three answers it records.

## Both halves, per dataset, and why the second one needs a doctored partition

- **Absent** is a date the partition's census never counted. It is answered, because nothing
  happened on it -- and for `suspend_d` that is the whole hazard: a security with no halt row and
  a security whose halt row was withheld both read as "not halted"
  (`backtest/execution.py::suspended_at_the_close` returns `False` for `None`).
- **Withheld** is a date the census counted and the availability predicate emptied. It is refused
  by name.

On all three datasets the stored `available_time` is a **fixed function of the row's own event
date** -- `_calendar_static_timeline` makes the two equal, and `suspend_d`'s `daily_close` clock
adds exactly ninety minutes inside the same day -- so a well-formed partition cannot produce the
second case at all. That is the property the move rests on and it is exactly why the check must
still exist: the clock lives in `providers/tushare.py`, one package away, and nothing in the
store enforces it. So each `..._is_refused_rather_than_answered_short` test below stores a
partition whose availability instants say something else, through the real `write_panel_batch`,
and requires the read to refuse rather than answer short. This is `V2-P4-034`'s `PROBE_ROWS`
instrument, one dataset at a time.

## The bound is `as_of`'s own day for two of them and the newest published session for the third

`suspend_d` is the odd one and the difference is load-bearing rather than cosmetic:
`test_a_halt_on_the_current_session_is_not_required_before_that_session_closes` is the honest
read that `_knowable_through_the_same_day` would refuse -- a corpus read at noon, holding a halt
for that same afternoon's close. Reconciling a 16:30-clocked dataset against the calendar day
would make that refusal fire on every intraday read of a complete panel.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from openalpha_cn.domain.name_history import (
    NAMECHANGE_DATASET,
)
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.price_limits import SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.panel.store import PanelStorageError, PanelStore
from openalpha_cn.panel_ingest import (
    load_name_histories,
    load_stock_universe,
    load_suspensions,
    write_panel_batch,
)

YEAR: Final[int] = 2026
SHANGHAI_OFFSET: Final[timedelta] = timedelta(hours=8)

HALTED_SESSION: Final[date] = date(2026, 1, 14)
"""The session the probe halt corpus holds a row for. Nothing is halted on any other."""

QUIET_SESSION: Final[date] = date(2026, 1, 15)
"""A session the halt corpus holds no row for at all -- the **absent** case."""

AFTER_THE_HALT_PUBLISHED: Final[datetime] = datetime(2026, 1, 14, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on the halted session, half an hour after it became knowable."""

BEFORE_THE_HALT_PUBLISHED: Final[datetime] = datetime(2026, 1, 14, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai on the halted session, four and a half hours before its 16:30."""


def _midnight(day: date) -> datetime:
    """Midnight Asia/Shanghai, which is what `calendar_static` dates both clocks at."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC) - SHANGHAI_OFFSET


def _close(day: date) -> datetime:
    """15:00 Asia/Shanghai -- `daily_close`'s `event_time`."""
    return datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC)


def _published(day: date) -> datetime:
    """16:30 Asia/Shanghai -- `daily_close`'s `available_time`."""
    return datetime(day.year, day.month, day.day, 8, 30, tzinfo=UTC)


def _batch(
    dataset: str,
    *,
    subjects: tuple[str, ...],
    columns: tuple[PanelColumn, ...],
    event_time: tuple[datetime, ...],
    available_time: tuple[datetime, ...],
) -> ColumnarPanelBatch:
    """One probe batch. `fetched_at` follows the newest instant so the batch is representable.

    `ColumnarPanelBatch` refuses a row whose `available_time` post-dates its own `as_of`, so a
    doctored availability has to arrive on a batch genuinely fetched after it -- which is what a
    corpus fetched later and queried at an earlier instant is, and the only honest way to store
    the shape these tests need.
    """
    fetched = max(available_time)
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/p4-076-probe",
        dataset=dataset,
        kind=dataset,
        as_of=fetched,
        fetched_at=fetched,
        status="success",
        subjects=subjects,
        timeline=TimelineColumns(
            event_time=event_time,
            available_time=available_time,
            ingested_time=available_time,
            revision_time=available_time,
        ),
        columns=columns,
    )


# --- suspend_d -------------------------------------------------------------------------------


def _halt_batch(*, available_time: tuple[datetime, ...]) -> ColumnarPanelBatch:
    """One halt on `HALTED_SESSION`, plus one on the session before it.

    Two rows on two dates rather than one, so the reconciliation has a date it agrees on beside
    the one it does not -- a probe with a single date cannot show that the refusal is per date.
    """
    days = (date(2026, 1, 13), HALTED_SESSION)
    return _batch(
        SUSPENSION_DATASET,
        subjects=("000001.SZ", "600000.SH"),
        columns=(
            PanelColumn("trade_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("suspend_type", "string", ("S", "S")),
            PanelColumn("suspend_timing", "string", (None, None)),
        ),
        event_time=tuple(_close(day) for day in days),
        available_time=available_time,
    )


def _halt_store(root: Path, *, available_time: tuple[datetime, ...]) -> PanelStore:
    store = PanelStore(root / "panel")
    write_panel_batch(store, _halt_batch(available_time=available_time), year=YEAR)
    return store


def test_a_session_with_no_halt_row_is_answered_as_not_halted_rather_than_refused(
    tmp_path: Path,
) -> None:
    """The absent case, and the one this dataset's crux is about.

    The corpus holds nothing for 2026-01-15 because nobody was halted, and the read has to answer
    that rather than refuse it -- 5,312 of 2024-06-28's 5,338 priced names have no halt row at
    all, so "no row" is the ordinary case and a door that refused it would refuse every panel.
    """
    store = _halt_store(
        tmp_path, available_time=(_published(date(2026, 1, 13)), _published(HALTED_SESSION))
    )

    corpus = load_suspensions(
        store, years=(YEAR,), as_of=datetime(2026, 1, 15, 9, 0, tzinfo=UTC), max_staleness=None
    )

    assert HALTED_SESSION in corpus
    assert QUIET_SESSION not in corpus


def test_a_halt_on_the_current_session_is_not_required_before_that_session_closes(
    tmp_path: Path,
) -> None:
    """The read that `_knowable_through_the_same_day` would have refused, and must not.

    Noon on the halted session: that afternoon's halt is genuinely not knowable yet, and the
    census bound has to agree. `_sessions_published_through` puts the newest required date at
    2026-01-13, so the 2026-01-14 row is neither counted nor visible and the two numbers match.
    Reconciling against `as_of`'s calendar day instead would count the row as due, find it
    withheld, and refuse an entirely honest intraday read of a complete partition -- which is why
    the bound is an argument to the shared door rather than a constant inside it.
    """
    store = _halt_store(
        tmp_path, available_time=(_published(date(2026, 1, 13)), _published(HALTED_SESSION))
    )

    corpus = load_suspensions(
        store, years=(YEAR,), as_of=BEFORE_THE_HALT_PUBLISHED, max_staleness=None
    )

    assert date(2026, 1, 13) in corpus
    assert HALTED_SESSION not in corpus


def test_a_withheld_halt_is_refused_rather_than_answered_short(tmp_path: Path) -> None:
    """The backstop, reached by storing a partition whose clock says something else.

    The 2026-01-14 row's availability is moved a day past its own 16:30, so at an `as_of` after
    that session published the census counts it and the predicate removes it. Answering short
    would hand back a corpus in which that security simply was not halted -- the same bytes an
    absent row produces, which is the collapse this dataset cannot afford.
    """
    store = _halt_store(
        tmp_path,
        available_time=(_published(date(2026, 1, 13)), _published(date(2026, 1, 15))),
    )

    with pytest.raises(PanelStorageError) as refusal:
        load_suspensions(store, years=(YEAR,), as_of=AFTER_THE_HALT_PUBLISHED, max_staleness=None)

    message = str(refusal.value)
    assert "its date census counts 1 row(s) dated 2026-01-14" in message
    assert "the visible slice carries 0 of them" in message
    assert "16:30:00 on its own trade_date" in message
    assert "does not exist" in message


# --- stock_basic -----------------------------------------------------------------------------


LISTED_ON: Final[date] = date(2026, 1, 5)
TERMINATED_ON: Final[date] = date(2026, 1, 14)


def _registry_batch(*, available_time: tuple[datetime, ...]) -> ColumnarPanelBatch:
    """One security's listing and one other's termination, on two different days."""
    return _batch(
        STOCK_BASIC_DATASET,
        subjects=("000001.SZ", "600000.SH", "600000.SH"),
        columns=(
            PanelColumn("lifecycle_event", "string", ("listing", "listing", "delisting")),
            PanelColumn(
                "lifecycle_date",
                "string",
                (LISTED_ON.isoformat(), LISTED_ON.isoformat(), TERMINATED_ON.isoformat()),
            ),
            PanelColumn("exchange", "string", ("SZSE", "SZSE", "SZSE")),
        ),
        event_time=(_midnight(LISTED_ON), _midnight(LISTED_ON), _midnight(TERMINATED_ON)),
        available_time=available_time,
    )


def _registry_store(root: Path, *, available_time: tuple[datetime, ...]) -> PanelStore:
    store = PanelStore(root / "panel")
    write_panel_batch(store, _registry_batch(available_time=available_time), year=YEAR)
    return store


def test_a_termination_that_has_not_happened_yet_is_absent_and_the_name_reads_as_listed(
    tmp_path: Path,
) -> None:
    """The absent case: at an `as_of` before the termination's own midnight it is not a row.

    The security reads as still listed, which is exactly what it was at that instant -- and the
    direction is the safety argument. A listing is never later than its own termination, so the
    predicate can only ever leave a name reported as still listed; it cannot produce the reverse,
    which `stock_universe_from_panel_rows` refuses by name as a partial read.
    """
    store = _registry_store(
        tmp_path,
        available_time=(_midnight(LISTED_ON), _midnight(LISTED_ON), _midnight(TERMINATED_ON)),
    )

    before = load_stock_universe(
        store,
        years=(YEAR,),
        as_of=datetime(2026, 1, 13, 9, 0, tzinfo=UTC),
        max_staleness=None,
    )
    after = load_stock_universe(
        store,
        years=(YEAR,),
        as_of=datetime(2026, 1, 14, 9, 0, tzinfo=UTC),
        max_staleness=None,
    )

    assert "600000.SH" in before.listed_on(date(2026, 1, 13))
    assert "600000.SH" not in after.listed_on(date(2026, 1, 14))


def test_a_withheld_lifecycle_row_is_refused_rather_than_answered_short(tmp_path: Path) -> None:
    """The backstop. A termination the census counted and the predicate removed is not "listed".

    Answered short, this read would report a delisted security as still trading, with nothing on
    the answer to say the row had been held back -- the fail-open this whole plane is built
    against, and one `stock_universe_from_panel_rows`' orphan rule cannot see because a missing
    *termination* leaves a perfectly well-formed lifecycle behind.
    """
    store = _registry_store(
        tmp_path,
        available_time=(_midnight(LISTED_ON), _midnight(LISTED_ON), _midnight(date(2026, 1, 20))),
    )

    with pytest.raises(PanelStorageError) as refusal:
        load_stock_universe(
            store,
            years=(YEAR,),
            as_of=datetime(2026, 1, 16, 9, 0, tzinfo=UTC),
            max_staleness=None,
        )

    message = str(refusal.value)
    assert "its date census counts 1 row(s) dated 2026-01-14" in message
    assert "the visible slice carries 0 of them" in message
    assert "A lifecycle row's availability is midnight on the day it is about" in message


# --- namechange ------------------------------------------------------------------------------


FIRST_NAME_ON: Final[date] = date(2026, 1, 5)
RENAMED_ON: Final[date] = date(2026, 1, 14)


def _rename_batch(*, available_time: tuple[datetime, ...]) -> ColumnarPanelBatch:
    days = (FIRST_NAME_ON, RENAMED_ON)
    return _batch(
        NAMECHANGE_DATASET,
        subjects=("000001.SZ", "000001.SZ"),
        columns=(
            PanelColumn("name", "string", ("平安银行", "ST平安")),
            PanelColumn("effective_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("announcement_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("change_reason", "string", ("其他", "ST")),
        ),
        event_time=tuple(_midnight(day) for day in days),
        available_time=available_time,
    )


def _rename_store(root: Path, *, available_time: tuple[datetime, ...]) -> PanelStore:
    store = PanelStore(root / "panel")
    write_panel_batch(store, _rename_batch(available_time=available_time), year=YEAR)
    return store


def test_a_rename_announced_after_the_read_is_absent_and_the_old_name_still_answers(
    tmp_path: Path,
) -> None:
    """The absent case: an announcement dated after `as_of` is not a row this read has.

    The history answers with the name the security carried at that instant, which is what a
    point-in-time read of an announcement corpus means. What the whole-partition door did with
    the same corpus was refuse the entire year for it.
    """
    store = _rename_store(
        tmp_path, available_time=(_midnight(FIRST_NAME_ON), _midnight(RENAMED_ON))
    )

    before = load_name_histories(
        store, years=(YEAR,), as_of=datetime(2026, 1, 13, 9, 0, tzinfo=UTC), max_staleness=None
    )
    after = load_name_histories(
        store, years=(YEAR,), as_of=datetime(2026, 1, 14, 9, 0, tzinfo=UTC), max_staleness=None
    )

    assert before["000001.SZ"].name_on(date(2026, 1, 13)) == "平安银行"
    assert after["000001.SZ"].name_on(date(2026, 1, 14)) == "ST平安"


def test_a_withheld_rename_is_refused_rather_than_answered_with_the_previous_name(
    tmp_path: Path,
) -> None:
    """The backstop, and this dataset's own reason for needing one.

    `NameHistory` has deliberately no upper horizon -- the last record answers for every later
    day -- so a corpus short by a withheld announcement answers with the *previous* name and
    nothing on it says so. Here that would report a special-treated name as ordinary, which
    `_bars_on` turns into `is_st=False` and a screen turns into a wider band.
    """
    store = _rename_store(
        tmp_path, available_time=(_midnight(FIRST_NAME_ON), _midnight(date(2026, 1, 20)))
    )

    with pytest.raises(PanelStorageError) as refusal:
        load_name_histories(
            store,
            years=(YEAR,),
            as_of=datetime(2026, 1, 16, 9, 0, tzinfo=UTC),
            max_staleness=None,
        )

    message = str(refusal.value)
    assert "its date census counts 1 row(s) dated 2026-01-14" in message
    assert "the visible slice carries 0 of them" in message
    assert "A rename's availability is midnight on its own announcement date" in message


# --- the look-ahead half, which is a different refusal and is reported first ------------------


def test_a_row_visible_before_its_own_event_is_reported_as_a_look_ahead_and_not_as_a_shortfall(
    tmp_path: Path,
) -> None:
    """`V2-P4-034`'s split, inherited by the three new callers.

    A row whose stored availability precedes its own event date is a look-ahead -- the panel's
    model forbids it outright -- while a withheld row is at worst an embargo the read cannot see.
    One message about two totals is what let a compensating pair through once already, so the two
    are two refusals and this one is decided first.
    """
    store = _registry_store(
        tmp_path,
        available_time=(_midnight(LISTED_ON), _midnight(LISTED_ON), _midnight(date(2026, 1, 6))),
    )

    with pytest.raises(PanelStorageError) as refusal:
        load_stock_universe(
            store,
            years=(YEAR,),
            as_of=datetime(2026, 1, 6, 9, 0, tzinfo=UTC),
            max_staleness=None,
        )

    message = str(refusal.value)
    assert "answered 1 visible row(s) dated 2026-01-14" in message
    assert "whose event had not happened at" in message
    assert "date census counts" not in message
