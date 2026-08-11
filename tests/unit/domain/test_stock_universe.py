"""The stock-universe contract (`V2-P1-005`), against real `stock_basic` rows.

Every fixture below is verbatim Tushare `stock_basic` output captured while this task was
implemented, requested with `list_status="L,D"` and an explicit `fields` list -- the default
field set has no `delist_date` at all, so a caller who omits `fields` cannot even see the
column this file is about. No test here touches the network: the rows are the data, inlined.

The property this file exists to pin is **survivorship**. `stock_basic` answers `L` when
asked nothing, so the obvious query returns only the securities that are still alive today,
and rebuilding a 2019 universe from it silently assumes 2019's market was 2026's survivors.
A live probe puts a number on it: on 2019-06-28 the listed-only reconstruction finds 3,404
names and the `L`-union-`D` reconstruction finds 3,631 -- **227 securities missing**, every
one of them delisted after that date. The fixture below reproduces that shape in miniature
with real rows.

The second property is the **delisting boundary**, and it is measured rather than assumed:
`delist_date` is the first day the security is *gone*, not its last session. Tushare's own
`daily` endpoint was checked against a random sample of 40 of the 339 delisted securities plus
15 named cases picked to break it (absorption mergers, code migrations, the PT era, the
share-reform B-share codes), and **zero** bars fall on or after `delist_date` in any of them.
So membership is `listed_on <= day < delisted_on`, and a contract that used `<=` would put a
security in the pool on a day it demonstrably did not trade.

What that does *not* establish -- and what an earlier version of this docstring asserted from a
sample of seven post-2014 退市整理期 cases -- is that the last bar is the *previous session*.
In the random 40 only 13 are; `600286.SH` stopped trading 260 sessions before its termination,
`000024.SZ` (in the fixture below, delisted 2015-12-30) 15 sessions before, and `T600018.SH`
has no daily bars at all. The gap is a suspension, which this dataset does not model; only the
exclusive bound is claimed here, and only the exclusive bound is what the contract needs.
"""

from __future__ import annotations

from datetime import date

import pytest

from openalpha_cn.domain.stock_universe import (
    KNOWN_UNIVERSE_LIMITATIONS,
    ListingStatus,
    SecurityLifecycle,
    StockUniverseError,
    UniverseHorizonError,
    build_stock_universe,
    listed_universe_by_trading_day,
    stock_universe_from_panel_rows,
)
from openalpha_cn.domain.trading_calendar import CalendarDay, build_trading_calendar

# --- real Tushare `stock_basic` rows, list_status="L,D" -------------------------------
# (ts_code, name, exchange, market, list_status, list_date, delist_date)

REGISTRY_ROWS: tuple[tuple[str, str, str, str | None, str, str, str | None], ...] = (
    ("000001.SZ", "平安银行", "SZSE", "主板", "L", "19910403", None),
    ("000002.SZ", "万科A", "SZSE", "主板", "L", "19910129", None),
    ("000003.SZ", "PT金田A(退)", "SZSE", "主板", "D", "19910703", "20020614"),
    ("000004.SZ", "国华退", "SZSE", "主板", "D", "19901201", "20260714"),
    ("000005.SZ", "ST星源(退)", "SZSE", "主板", "D", "19901210", "20240426"),
    ("000018.SZ", "神城A退(退)", "SZSE", "主板", "D", "19920616", "20200107"),
    ("000024.SZ", "招商地产(退)", "SZSE", "主板", "D", "19930607", "20151230"),
    ("T600018.SH", "上港集箱(退)", "SSE", None, "D", "20000719", "20061020"),
    ("600087.SH", "退长油(退)", "SSE", "主板", "D", "19970612", "20140605"),
    ("688001.SH", "华兴源创", "SSE", "科创板", "L", "20190722", None),
    ("601127.SH", "赛力斯", "SSE", "主板", "L", "20160615", None),
    ("689009.SH", "九号公司", "SSE", "科创板", "L", "20201029", None),
)

SNAPSHOT = date(2026, 8, 8)
"""The day the fixture rows were fetched. `stock_basic` carries no `as_of` of its own."""


def _iso(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:]))


def _lifecycles(
    rows: tuple[tuple[str, str, str, str | None, str, str, str | None], ...] = REGISTRY_ROWS,
) -> tuple[SecurityLifecycle, ...]:
    return tuple(
        SecurityLifecycle(
            ts_code=ts_code,
            exchange=exchange,
            listed_on=_iso(list_date),
            delisted_on=None if delist_date is None else _iso(delist_date),
        )
        for ts_code, _name, exchange, _market, _status, list_date, delist_date in rows
    )


def _universe(rows=REGISTRY_ROWS):
    return build_stock_universe(snapshot_date=SNAPSHOT, securities=_lifecycles(rows))


def _survivors_only():
    """What a caller who never asked for `list_status="D"` would have."""
    return build_stock_universe(
        snapshot_date=SNAPSHOT,
        securities=_lifecycles(tuple(row for row in REGISTRY_ROWS if row[4] == "L")),
    )


# --- survivorship ---------------------------------------------------------------------


def test_rebuilding_2019_from_the_listed_set_alone_loses_the_securities_that_died_since() -> None:
    day = date(2019, 6, 28)
    survivors = _survivors_only().listed_on(day)
    complete = _universe().listed_on(day)

    assert survivors == ("000001.SZ", "000002.SZ", "601127.SH")
    assert complete == (
        "000001.SZ",
        "000002.SZ",
        "000004.SZ",
        "000005.SZ",
        "000018.SZ",
        "601127.SH",
    )
    missing = tuple(sorted(set(complete) - set(survivors)))
    assert missing == ("000004.SZ", "000005.SZ", "000018.SZ")


def test_every_security_the_listed_only_rebuild_loses_delisted_after_the_asked_day() -> None:
    day = date(2019, 6, 28)
    universe = _universe()
    missing = set(universe.listed_on(day)) - set(_survivors_only().listed_on(day))
    assert missing
    for ts_code in missing:
        delisted_on = universe.security(ts_code).delisted_on
        assert delisted_on is not None
        assert delisted_on > day


def test_a_security_that_died_before_the_asked_day_is_not_in_the_universe_either() -> None:
    universe = _universe()
    # 000003.SZ delisted 2002-06-14, 000024.SZ 2015-12-30, 600087.SH 2014-06-05.
    for ts_code in ("000003.SZ", "000024.SZ", "600087.SH", "T600018.SH"):
        assert ts_code not in universe.listed_on(date(2019, 6, 28))
        assert universe.status_on(ts_code, date(2019, 6, 28)) is ListingStatus.delisted


# --- the delisting boundary, measured against Tushare's own daily bars -----------------


def test_delist_date_is_exclusive_because_the_security_never_trades_on_it() -> None:
    universe = _universe()
    # 000018.SZ: last daily bar 2020-01-06, delist_date 2020-01-07, zero bars after.
    assert universe.is_listed("000018.SZ", date(2020, 1, 6)) is True
    assert universe.is_listed("000018.SZ", date(2020, 1, 7)) is False
    assert universe.status_on("000018.SZ", date(2020, 1, 7)) is ListingStatus.delisted


def test_list_date_is_inclusive_because_the_first_daily_bar_falls_on_it() -> None:
    universe = _universe()
    # 688001.SH: list_date 2019-07-22 and its first daily bar is 2019-07-22.
    assert universe.status_on("688001.SH", date(2019, 7, 21)) is ListingStatus.not_yet_listed
    assert universe.is_listed("688001.SH", date(2019, 7, 22)) is True


# --- the snapshot horizon -------------------------------------------------------------


def test_a_day_after_the_snapshot_is_not_answered_with_todays_membership() -> None:
    universe = _universe()
    beyond = date(2026, 9, 1)
    assert universe.status_on("000001.SZ", beyond) is ListingStatus.beyond_snapshot
    with pytest.raises(UniverseHorizonError, match="beyond the 2026-08-08 registry snapshot"):
        universe.is_listed("000001.SZ", beyond)
    with pytest.raises(UniverseHorizonError, match="beyond the 2026-08-08 registry snapshot"):
        universe.listed_on(beyond)


def test_a_listing_status_is_not_a_truth_value() -> None:
    for status in ListingStatus:
        with pytest.raises(StockUniverseError, match=r"four-valued verdict and has no truth value"):
            bool(status)


def test_a_day_before_the_first_listing_is_answered_rather_than_refused() -> None:
    universe = _universe()
    assert universe.listed_on(date(1985, 1, 1)) == ()
    assert universe.status_on("000001.SZ", date(1985, 1, 1)) is ListingStatus.not_yet_listed


def test_the_same_day_is_refused_once_the_universe_knows_which_years_it_read() -> None:
    """The empty answer above is only honest when nothing was missed, and `years_read` is what
    tells the two apart. A universe assembled from the 2000-onwards partitions holds no
    security that listed in 1999, so `()` for a 1999 day is a short read wearing the shape of
    an empty market. Built directly from lifecycles there is no window and the answer stands
    -- which is the test directly above this one."""
    windowed = build_stock_universe(
        snapshot_date=date(2026, 8, 8),
        securities=_universe().securities,
        years_read=(2000, 2001),
    )

    assert windowed.completeness().years_read == (2000, 2001)
    with pytest.raises(UniverseHorizonError, match="the first lifecycle year this universe"):
        windowed.listed_on(date(1999, 12, 31))
    # The upper bound is untouched, and the day the window opens is answered.
    assert windowed.listed_on(date(2000, 1, 1)) == _universe().listed_on(date(2000, 1, 1))


def test_an_unknown_ts_code_is_refused_rather_than_reported_as_not_yet_listed() -> None:
    universe = _universe()
    with pytest.raises(StockUniverseError, match="is not in the 2026-08-08 registry snapshot"):
        universe.status_on("999999.SZ", date(2019, 6, 28))


# --- completeness ---------------------------------------------------------------------


def test_the_universe_states_its_own_completeness_boundary() -> None:
    completeness = _universe().completeness()
    assert completeness.snapshot_date == SNAPSHOT
    assert completeness.security_count == len(REGISTRY_ROWS)
    assert completeness.still_listed_count == 5
    assert completeness.delisted_count == 7
    assert completeness.first_listing == date(1990, 12, 1)
    assert completeness.first_recorded_delisting == date(2002, 6, 14)
    assert completeness.limitations == KNOWN_UNIVERSE_LIMITATIONS


def test_every_declared_limitation_carries_a_code_and_a_detail() -> None:
    assert KNOWN_UNIVERSE_LIMITATIONS
    codes = tuple(entry.code for entry in KNOWN_UNIVERSE_LIMITATIONS)
    assert len(set(codes)) == len(codes)
    for entry in KNOWN_UNIVERSE_LIMITATIONS:
        assert entry.detail.strip()


def test_the_known_limitations_are_named_rather_than_argued_away() -> None:
    """Set **equality**, in the shape `KNOWN_ADJUSTMENT_LIMITATIONS` has had since `V2-P1-005`.

    The test above is about the registry's *form* -- codes are unique, details are non-empty --
    and every one of its assertions survives renaming an entry, deleting one, or adding one.
    That is not hypothetical here: `a_listed_only_registry_is_invisible_to_every_downstream_check`
    is the entry `V2-P2-008`'s whole finding rests on, `test_injection_register.py`'s disclosed
    exclusion points a reader at it by name, and until this test its name appeared nowhere the
    suite could see -- only in two docstrings, which are prose the test runner does not read.
    Renaming it left every test green while the exclusion's citation silently stopped resolving.

    Naming all seven, rather than asserting membership for the interesting one, is
    `tests/unit/test_known_limitation_registries.py`'s rule and this repository's older one: a
    universal claim tested on a sample it chose cannot see what it left out.
    """
    assert {entry.code for entry in KNOWN_UNIVERSE_LIMITATIONS} == {
        "a_shares_only",
        "delistings_only_back_to_the_first_recorded_one",
        "suspension_invisible",
        "announcement_clock_absent",
        "snapshot_has_no_as_of",
        "a_listed_only_registry_is_invisible_to_every_downstream_check",
        "ts_code_is_not_a_permanent_security_identity",
    }


# --- construction is fail-closed ------------------------------------------------------


def test_two_rows_for_one_ts_code_are_refused_rather_than_merged() -> None:
    doubled = (*_lifecycles(), _lifecycles()[0])
    with pytest.raises(StockUniverseError, match="appears more than once"):
        build_stock_universe(snapshot_date=SNAPSHOT, securities=doubled)


def test_a_delisting_on_or_before_its_own_listing_is_refused() -> None:
    broken = (
        SecurityLifecycle(
            ts_code="000001.SZ",
            exchange="SZSE",
            listed_on=date(2019, 1, 2),
            delisted_on=date(2019, 1, 2),
        ),
    )
    with pytest.raises(StockUniverseError, match=r"delisted on 2019-01-02, on or before"):
        build_stock_universe(snapshot_date=SNAPSHOT, securities=broken)


def test_a_lifecycle_dated_after_the_snapshot_is_refused() -> None:
    future = (
        SecurityLifecycle(
            ts_code="920165.BJ",
            exchange="BSE",
            listed_on=date(2026, 8, 11),
            delisted_on=None,
        ),
    )
    with pytest.raises(StockUniverseError, match="after the 2026-08-08 registry snapshot"):
        build_stock_universe(snapshot_date=SNAPSHOT, securities=future)


def test_an_empty_registry_cannot_become_a_universe() -> None:
    with pytest.raises(StockUniverseError, match="at least one security"):
        build_stock_universe(snapshot_date=SNAPSHOT, securities=())


def test_a_datetime_masquerading_as_a_snapshot_date_is_refused() -> None:
    from datetime import datetime

    with pytest.raises(StockUniverseError, match=r"must be a plain datetime\.date"):
        build_stock_universe(
            snapshot_date=datetime(2026, 8, 8),  # type: ignore[arg-type]
            securities=_lifecycles(),
        )


# --- reading the stored panel back ----------------------------------------------------


def _panel_rows() -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for ts_code, _name, exchange, _market, _status, list_date, delist_date in REGISTRY_ROWS:
        rows.append((ts_code, "listing", _iso(list_date).isoformat(), exchange))
        if delist_date is not None:
            rows.append((ts_code, "delisting", _iso(delist_date).isoformat(), exchange))
    return tuple(rows)


def test_stored_lifecycle_rows_rebuild_the_same_universe_the_registry_did() -> None:
    rebuilt = stock_universe_from_panel_rows(_panel_rows(), snapshot_date=SNAPSHOT)
    assert rebuilt.listed_on(date(2019, 6, 28)) == _universe().listed_on(date(2019, 6, 28))
    assert rebuilt.securities == _universe().securities


def test_a_delisting_row_whose_listing_row_was_not_read_is_refused() -> None:
    rows = tuple(row for row in _panel_rows() if row[:2] != ("000018.SZ", "listing"))
    with pytest.raises(StockUniverseError, match=r"000018\.SZ has a delisting row and no listing"):
        stock_universe_from_panel_rows(rows, snapshot_date=SNAPSHOT)


def test_two_listing_rows_for_one_security_are_refused() -> None:
    rows = (*_panel_rows(), ("000001.SZ", "listing", "1991-04-03", "SZSE"))
    with pytest.raises(StockUniverseError, match=r"000001\.SZ has 2 listing rows"):
        stock_universe_from_panel_rows(rows, snapshot_date=SNAPSHOT)


def test_an_unknown_lifecycle_event_is_refused_rather_than_ignored() -> None:
    rows = (*_panel_rows(), ("000999.SZ", "suspension", "2019-01-02", "SZSE"))
    with pytest.raises(StockUniverseError, match="unknown lifecycle event 'suspension'"):
        stock_universe_from_panel_rows(rows, snapshot_date=SNAPSHOT)


def test_a_stored_row_of_the_wrong_width_is_refused() -> None:
    with pytest.raises(StockUniverseError, match="row 0 has 3 values, expected 4"):
        stock_universe_from_panel_rows(
            (("000001.SZ", "listing", "1991-04-03"),), snapshot_date=SNAPSHOT
        )


def test_a_stored_lifecycle_date_that_is_not_an_iso_date_is_refused() -> None:
    with pytest.raises(StockUniverseError, match="lifecycle_date is not an ISO date"):
        stock_universe_from_panel_rows(
            (("000001.SZ", "listing", "1991-04-31", "SZSE"),), snapshot_date=SNAPSHOT
        )


def test_a_stored_lifecycle_date_that_is_not_a_string_is_refused() -> None:
    with pytest.raises(StockUniverseError, match="lifecycle_date must be an ISO date string"):
        stock_universe_from_panel_rows(
            (("000001.SZ", "listing", 19910403, "SZSE"),), snapshot_date=SNAPSHOT
        )


def test_tushares_own_compact_date_form_still_reads_as_the_same_day() -> None:
    """`date.fromisoformat` accepts `YYYYMMDD` on Python 3.11+, so a partition written by an
    older projection that skipped `.isoformat()` reads back as the same date rather than as a
    silently different one. Pinned because it is a property of the runtime, not of this code."""
    compact = stock_universe_from_panel_rows(
        (("000001.SZ", "listing", "19910403", "SZSE"),), snapshot_date=SNAPSHOT
    )
    assert compact.security("000001.SZ").listed_on == date(1991, 4, 3)


def test_a_security_whose_two_rows_disagree_about_the_exchange_is_refused() -> None:
    rows = (
        ("000018.SZ", "listing", "1992-06-16", "SZSE"),
        ("000018.SZ", "delisting", "2020-01-07", "SSE"),
    )
    with pytest.raises(StockUniverseError, match="two exchanges"):
        stock_universe_from_panel_rows(rows, snapshot_date=SNAPSHOT)


# --- joining the universe to the trading calendar (`V2-P1-004`) -----------------------


def _calendar():
    days = []
    for offset in range((date(2020, 1, 10) - date(2020, 1, 1)).days + 1):
        day = date(2020, 1, 1) + __import__("datetime").timedelta(days=offset)
        days.append(CalendarDay(calendar_date=day, is_trading=day.weekday() < 5 and day.day != 1))
    return build_trading_calendar("SZSE", days)


def test_the_universe_is_aligned_to_the_calendars_sessions_not_to_calendar_days() -> None:
    aligned = listed_universe_by_trading_day(
        _universe(), _calendar(), start=date(2020, 1, 2), end=date(2020, 1, 8)
    )
    assert tuple(day for day, _ in aligned) == (
        date(2020, 1, 2),
        date(2020, 1, 3),
        date(2020, 1, 6),
        date(2020, 1, 7),
        date(2020, 1, 8),
    )
    by_day = dict(aligned)
    assert "000018.SZ" in by_day[date(2020, 1, 6)]
    assert "000018.SZ" not in by_day[date(2020, 1, 7)]


def test_the_alignment_only_reports_the_calendars_own_exchange() -> None:
    aligned = listed_universe_by_trading_day(
        _universe(), _calendar(), start=date(2020, 1, 2), end=date(2020, 1, 3)
    )
    for _day, members in aligned:
        assert all(code.endswith(".SZ") for code in members)


# --- guards on the values themselves -----------------------------------------------------


def test_a_termination_after_the_snapshot_is_refused() -> None:
    future = (
        SecurityLifecycle(
            ts_code="000001.SZ",
            exchange="SZSE",
            listed_on=date(1991, 4, 3),
            delisted_on=date(2026, 9, 1),
        ),
    )
    with pytest.raises(StockUniverseError, match="after the 2026-08-08 registry snapshot"):
        build_stock_universe(snapshot_date=SNAPSHOT, securities=future)


def test_a_blank_ts_code_is_refused_rather_than_becoming_a_security() -> None:
    blank = (SecurityLifecycle(ts_code="  ", exchange="SZSE", listed_on=date(1991, 4, 3)),)
    with pytest.raises(StockUniverseError, match="ts_code must be a non-empty string"):
        build_stock_universe(snapshot_date=SNAPSHOT, securities=blank)


def test_a_stored_subject_that_is_not_a_string_is_refused() -> None:
    with pytest.raises(StockUniverseError, match="subject must be a non-empty string"):
        stock_universe_from_panel_rows(
            ((None, "listing", "1991-04-03", "SZSE"),), snapshot_date=SNAPSHOT
        )
