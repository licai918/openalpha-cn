"""`stock_basic` and `namechange` end to end: Tushare -> batch -> Parquet -> contract.

The whole chain runs against a real `PanelStore` on a real DuckDB catalog with real Parquet
files. Only the HTTP transport is doubled, and what it serves is real registry and rename
data captured live on 2026-08-08.

The acceptance this file carries is the survivorship one. Two universes are rebuilt for
2019-06-28 from the same partition -- one from the securities that are still listed today,
one from the whole registry -- and the difference has to be exactly the securities that died
in between. On the full market a live probe measured that difference at **227** securities
(3,404 against 3,631); the fixture reproduces the shape with real rows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.domain.name_history import (
    NAMECHANGE_DATASET,
    NameHistoryHorizonError,
    RiskWarning,
)
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.stock_universe import (
    STOCK_BASIC_DATASET,
    ListingStatus,
    StockUniverseError,
    UniverseHorizonError,
    build_stock_universe,
    listed_universe_by_trading_day,
)
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    CalendarDay,
    build_trading_calendar,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_name_histories,
    load_stock_universe,
    name_history_requirement,
    panel_partition_year,
    split_panel_batch_by_year,
    stock_universe_requirement,
    write_name_history,
    write_panel_batch,
    write_stock_universe,
    write_trading_calendar,
)
from openalpha_cn.providers.base import ProviderRequest
from openalpha_cn.providers.tushare import (
    TUSHARE_RESPONSE_TRUNCATION_FLAG,
    TushareProvider,
)

FETCHED_AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)

REGISTRY_FIELDS = [
    "ts_code",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
]

# Real Tushare `stock_basic` rows, list_status="L,D".
REGISTRY_ITEMS: list[list[Any]] = [
    ["000001.SZ", "平安银行", "SZSE", "主板", "L", "19910403", None],
    ["000002.SZ", "万科A", "SZSE", "主板", "L", "19910129", None],
    ["000003.SZ", "PT金田A(退)", "SZSE", "主板", "D", "19910703", "20020614"],
    ["000004.SZ", "国华退", "SZSE", "主板", "D", "19901201", "20260714"],
    ["000005.SZ", "ST星源(退)", "SZSE", "主板", "D", "19901210", "20240426"],
    ["000018.SZ", "神城A退(退)", "SZSE", "主板", "D", "19920616", "20200107"],
    ["000024.SZ", "招商地产(退)", "SZSE", "主板", "D", "19930607", "20151230"],
    ["T600018.SH", "上港集箱(退)", "SSE", None, "D", "20000719", "20061020"],
    ["600087.SH", "退长油(退)", "SSE", "主板", "D", "19970612", "20140605"],
    ["688001.SH", "华兴源创", "SSE", "科创板", "L", "20190722", None],
    ["601127.SH", "赛力斯", "SSE", "主板", "L", "20160615", None],
    ["689009.SH", "九号公司", "SSE", "科创板", "L", "20201029", None],
]

REGISTRY_YEARS = (
    1990,
    1991,
    1992,
    1993,
    1997,
    2000,
    2002,
    2006,
    2014,
    2015,
    2016,
    2019,
    2020,
    2024,
    2026,
)
"""Every lifecycle year this fixture produces a partition for, ascending.

Spelled out rather than derived from the store, because it is the observation window: the set
of years a caller reads is what bounds the universe, and the tests below that read a shorter
range say so. Note the gaps -- 1994 through 1996 have no listing and no delisting among these
twelve securities -- which is exactly why a load cannot demand a partition per calendar year.
"""

NAMECHANGE_FIELDS = ["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"]

# Real `namechange` rows, grouped by the announcement year each partition is keyed on.
NAMECHANGE_BY_YEAR: dict[int, list[list[Any]]] = {
    1991: [["000001.SZ", "深发展A", "19910403", "20061008", "19910403", "其他"]],
    2006: [["000001.SZ", "S深发展A", "20061009", "20070619", "20060928", "其他"]],
    2007: [["000001.SZ", "深发展A", "20070620", "20120801", "20070614", "其他"]],
    2012: [["000001.SZ", "平安银行", "20120802", None, "20120120", "其他"]],
    2014: [["600087.SH", "退市长油", "20140421", None, "20140412", "退市整理期"]],
    2021: [["000005.SZ", "ST星源", "20210506", None, "20210430", "ST"]],
    2026: [["920165.BJ", "珈凯生物", "20260811", None, "20260811", "其他"]],
}


def _response(fields: list[str], items: list[list[Any]]) -> dict[str, Any]:
    """A complete response. `has_more=False` is what a live one actually carries, and
    `stock_basic` now requires it to be present -- it has no measured row cap, so the flag is
    that endpoint's only witness that nothing was withheld."""
    return {
        "code": 0,
        "msg": None,
        "data": {
            "fields": list(fields),
            "items": items,
            TUSHARE_RESPONSE_TRUNCATION_FLAG: False,
        },
    }


class _Transport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.response


def _registry_batch(as_of: datetime, items: list[list[Any]] | None = None):
    provider = TushareProvider(
        token="secret-token",
        transport=_Transport(
            _response(REGISTRY_FIELDS, REGISTRY_ITEMS if items is None else items)
        ),
        clock=lambda: FETCHED_AT,
    )
    return provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))


def _namechange_batch(as_of: datetime, items: list[list[Any]]):
    provider = TushareProvider(
        token="secret-token",
        transport=_Transport(_response(NAMECHANGE_FIELDS, items)),
        clock=lambda: FETCHED_AT,
    )
    return provider.fetch_panel(ProviderRequest(dataset=NAMECHANGE_DATASET, as_of=as_of))


# Two real 2024 SSE/SZSE sessions, enough to make one calendar partition per exchange. The
# calendar is here only because the overwrite guard is shared with it -- see the refusal
# tests at the bottom of this file.
CALENDAR_FIELDS = ["exchange", "cal_date", "is_open", "pretrade_date"]


def _calendar_batch(exchange: str):
    provider = TushareProvider(
        token="secret-token",
        transport=_Transport(
            _response(
                CALENDAR_FIELDS,
                [
                    [exchange, "20240104", 1, "20240103"],
                    [exchange, "20240103", 1, "20240102"],
                ],
            )
        ),
        clock=lambda: FETCHED_AT,
    )
    return provider.fetch_panel(
        ProviderRequest(
            dataset=TRADING_CALENDAR_DATASET,
            as_of=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
            subjects=(exchange,),
        )
    )


def _store(tmp_path: Path) -> PanelStore:
    return PanelStore(tmp_path / "panel")


def _upto(year: int) -> tuple[int, ...]:
    """The fixture's lifecycle years up to and including `year` -- an observation window."""
    return tuple(entry for entry in REGISTRY_YEARS if entry <= year)


def _seed_registry(tmp_path: Path) -> PanelStore:
    store = _store(tmp_path)
    write_stock_universe(store, _registry_batch(FETCHED_AT))
    return store


def _seed_registry_from(tmp_path: Path, first: int) -> PanelStore:
    """Seed only the lifecycle years at or after `first`, the way a store backfilled from a
    start year leaves one.

    `V2-P4-059` made this the only way to get a read whose first year is not the store's own
    first year: `load_stock_universe` now supplies every held year beneath the requested range,
    so on a fully seeded store a short read cannot be asked for. The two guards below are about
    what happens when there is genuinely no history to supply, which is this shape.
    """
    store = _store(tmp_path)
    for year, yearly in split_panel_batch_by_year(_registry_batch(FETCHED_AT)):
        if year >= first:
            write_panel_batch(store, yearly, year=year)
    return store


def _seed_namechange(tmp_path: Path) -> PanelStore:
    store = _store(tmp_path)
    for year, items in NAMECHANGE_BY_YEAR.items():
        if year == 2026:
            # Its only row is announced 2026-08-11, after the fetch, so the batch is
            # `no_data` and there is nothing to write. Pinned separately below.
            continue
        write_name_history(
            store, _namechange_batch(datetime(year, 12, 31, 12, 0, tzinfo=UTC), items)
        )
    return store


# --- the survivorship acceptance --------------------------------------------------------


def test_the_stored_registry_rebuilds_a_2019_universe_the_listed_set_alone_cannot(
    tmp_path: Path,
) -> None:
    store = _seed_registry(tmp_path)
    universe = load_stock_universe(
        store, years=REGISTRY_YEARS, as_of=FETCHED_AT, max_staleness=None
    )

    day = date(2019, 6, 28)
    complete = universe.listed_on(day)
    survivors_only = build_stock_universe(
        snapshot_date=universe.snapshot_date,
        securities=tuple(entry for entry in universe.securities if entry.delisted_on is None),
    ).listed_on(day)

    assert survivors_only == ("000001.SZ", "000002.SZ", "601127.SH")
    assert complete == (
        "000001.SZ",
        "000002.SZ",
        "000004.SZ",
        "000005.SZ",
        "000018.SZ",
        "601127.SH",
    )
    missing = tuple(sorted(set(complete) - set(survivors_only)))
    assert missing == ("000004.SZ", "000005.SZ", "000018.SZ")
    for ts_code in missing:
        delisted_on = universe.security(ts_code).delisted_on
        assert delisted_on is not None and delisted_on > day


def test_the_universe_reports_the_window_it_was_assembled_from(
    tmp_path: Path,
) -> None:
    store = _seed_registry(tmp_path)
    universe = load_stock_universe(
        store, years=REGISTRY_YEARS, as_of=FETCHED_AT, max_staleness=None
    )
    # 2026-08-08 12:00Z is 2026-08-08 20:00 in Asia/Shanghai.
    assert universe.snapshot_date == date(2026, 8, 8)
    assert universe.completeness().snapshot_date == date(2026, 8, 8)
    assert universe.completeness().security_count == len(REGISTRY_ITEMS)
    assert universe.completeness().first_recorded_delisting == date(2002, 6, 14)
    assert universe.completeness().years_read == REGISTRY_YEARS


def test_a_delisted_security_leaves_the_universe_on_its_delist_date_and_not_before(
    tmp_path: Path,
) -> None:
    universe = load_stock_universe(
        _seed_registry(tmp_path), years=REGISTRY_YEARS, as_of=FETCHED_AT, max_staleness=None
    )
    assert universe.is_listed("000018.SZ", date(2020, 1, 6)) is True
    assert universe.is_listed("000018.SZ", date(2020, 1, 7)) is False
    assert universe.status_on("000018.SZ", date(2020, 1, 7)) is ListingStatus.delisted


def test_a_partition_written_at_a_point_in_time_carries_only_what_was_knowable_then(
    tmp_path: Path,
) -> None:
    """Partitions built with `as_of=2019-06-28` hold no 2020 or 2024 delisting rows at all,
    so a universe read from them says those securities are still listed -- which is what a
    2019 observer would have said, and is why the delisting is a separate row."""
    store = _store(tmp_path)
    as_of = datetime(2019, 6, 28, 12, 0, tzinfo=UTC)
    write_stock_universe(store, _registry_batch(as_of))
    # Nothing in this registry listed or died between 2017 and 2019-06-28, so the newest
    # partition the point-in-time fetch produced is 2016 -- and the horizon is still the
    # as_of, because no year the store holds went unread.
    universe = load_stock_universe(
        store, years=store.registered_years(STOCK_BASIC_DATASET), as_of=as_of, max_staleness=None
    )

    assert store.registered_years(STOCK_BASIC_DATASET) == _upto(2016)
    assert universe.snapshot_date == date(2019, 6, 28)
    assert universe.security("000018.SZ").delisted_on is None
    assert universe.is_listed("000018.SZ", date(2019, 6, 28)) is True
    # The 2019 observation cannot answer a 2020 question at all.
    assert universe.status_on("000018.SZ", date(2020, 1, 7)) is ListingStatus.beyond_snapshot


def test_reading_fewer_years_than_as_of_covers_shrinks_the_horizon_not_the_answers(
    tmp_path: Path,
) -> None:
    """The snapshot date is the *earlier* of `as_of` and the end of the last year read. A
    caller who backfilled to 2026 but reads only 1990..2019 holds no lifecycle event after
    2019, and answering a 2023 question from it would report every security that died in
    2020-2023 as still listed."""
    universe = load_stock_universe(
        _seed_registry(tmp_path),
        years=_upto(2019),
        as_of=FETCHED_AT,
        max_staleness=None,
    )
    # 2020 is the first year the store holds that this read skipped, so the horizon stops the
    # day before it rather than at `as_of`.
    assert universe.snapshot_date == date(2019, 12, 31)
    assert universe.status_on("000018.SZ", date(2023, 1, 3)) is ListingStatus.beyond_snapshot
    assert universe.is_listed("000018.SZ", date(2019, 12, 31)) is True


# --- the history beneath the requested range (`V2-P4-059`/`V2-P4-060`) ---------------------


def test_one_requested_year_still_reads_the_lifecycle_years_beneath_it(
    tmp_path: Path,
) -> None:
    """The defect `V2-P4-059` closed, at the loader.

    `stock_basic` is keyed by the year a security's life *changed*, so the newest partition is
    that year's listings and terminations rather than that year's market. `factor build --year
    2026` handed exactly that year to this read and got a universe the size of one year's
    listings -- eleven securities scored out of a 5,545-security store, exit 0. The years the
    caller did not name are the history underneath its window, and they are read whether it
    named them or not.
    """
    store = _seed_registry(tmp_path)
    newest = REGISTRY_YEARS[-1]

    one_year = load_stock_universe(store, years=(newest,), as_of=FETCHED_AT, max_staleness=None)
    every_year = load_stock_universe(
        store, years=REGISTRY_YEARS, as_of=FETCHED_AT, max_staleness=None
    )

    assert one_year == every_year
    assert one_year.security_count == len(REGISTRY_ITEMS)
    assert one_year.completeness().years_read == REGISTRY_YEARS


def test_a_delisting_whose_listing_year_the_caller_did_not_name_is_not_a_partial_read(
    tmp_path: Path,
) -> None:
    """The defect `V2-P4-060` closed, and the reason it is the same defect.

    A security's delisting row lives in the year it died and its listing row in the year it
    listed, so naming only the newest year produces a delisting with no listing --
    `stock_universe_from_panel_rows` refuses that, correctly, as a partial read. Reading the
    listing years is what stops it being one. `000018.SZ` listed on 1992-06-16 and was
    terminated on 2020-01-07 -- twenty-eight partitions apart in this fixture's own rows, which
    are the ones a live probe captured on 2026-08-08.
    """
    store = _seed_registry(tmp_path)

    universe = load_stock_universe(store, years=(2020,), as_of=FETCHED_AT, max_staleness=None)

    assert universe.security("000018.SZ").listed_on == date(1992, 6, 16)
    assert universe.security("000018.SZ").delisted_on == date(2020, 1, 7)
    assert 1992 in universe.completeness().years_read


def test_the_supplied_history_does_not_move_the_snapshot_horizon(tmp_path: Path) -> None:
    """Widening downwards cannot install a look-ahead, and this is the property that says so.

    The horizon is `as_of` pulled back to the day before the first year the store holds and the
    read skipped -- which keys off the *newest* year read. Supplying earlier years does not move
    it, so a prefix read still answers `beyond_snapshot` for a question past its window and
    still refuses to report the securities that died after it as listed.
    """
    store = _seed_registry(tmp_path)

    universe = load_stock_universe(store, years=(2019,), as_of=FETCHED_AT, max_staleness=None)

    assert universe.snapshot_date == date(2019, 12, 31)
    assert universe.status_on("000018.SZ", date(2023, 1, 3)) is ListingStatus.beyond_snapshot
    assert universe.completeness().years_read == _upto(2019)


def test_a_store_with_no_history_to_supply_still_refuses_the_partial_read_by_name(
    tmp_path: Path,
) -> None:
    """The residue, kept reachable on purpose.

    Widening removes the *cause* on a store the ingest wrote whole; it cannot invent partitions
    that were never written. A store whose earliest lifecycle years are absent still yields a
    delisting with no listing, and that must stay a named refusal rather than becoming an
    answer -- it is what `factor_view._REGISTRY_FAULTS` reports instead of withholding.
    """
    store = _seed_registry_from(tmp_path, 2020)

    with pytest.raises(StockUniverseError, match="has a delisting row and no listing row"):
        load_stock_universe(store, years=(2020,), as_of=FETCHED_AT, max_staleness=None)


def test_skipping_a_year_the_store_holds_is_refused_rather_than_silently_smaller(
    tmp_path: Path,
) -> None:
    store = _seed_registry(tmp_path)
    years = tuple(year for year in REGISTRY_YEARS if year != 2020)
    with pytest.raises(PanelBatchError, match=r"skip \[2020\], which the store holds"):
        load_stock_universe(store, years=years, as_of=FETCHED_AT, max_staleness=None)


def test_a_year_the_store_does_not_hold_is_not_invented_as_a_gap(tmp_path: Path) -> None:
    """1994-1996 have no partition because nothing in this registry listed or died then. A
    load must not confuse that with a skipped year -- requiring a partition per calendar year
    would make a sparse but correct history unloadable."""
    store = _seed_registry(tmp_path)
    assert 1995 not in store.registered_years(STOCK_BASIC_DATASET)
    universe = load_stock_universe(
        store, years=REGISTRY_YEARS, as_of=FETCHED_AT, max_staleness=None
    )
    assert universe.security_count == len(REGISTRY_ITEMS)


def test_reading_no_years_at_all_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PanelBatchError, match="needs at least one lifecycle year"):
        load_stock_universe(_store(tmp_path), years=(), as_of=FETCHED_AT, max_staleness=None)


# --- the calendar join --------------------------------------------------------------------


def _sz_calendar():
    days = [
        CalendarDay(
            calendar_date=date(2020, 1, 1) + timedelta(days=offset),
            is_trading=(date(2020, 1, 1) + timedelta(days=offset)).weekday() < 5
            and (date(2020, 1, 1) + timedelta(days=offset)).day != 1,
        )
        for offset in range(10)
    ]
    return build_trading_calendar("SZSE", days)


def test_the_stored_universe_aligns_to_the_calendars_sessions(tmp_path: Path) -> None:
    universe = load_stock_universe(
        _seed_registry(tmp_path), years=REGISTRY_YEARS, as_of=FETCHED_AT, max_staleness=None
    )
    aligned = listed_universe_by_trading_day(
        universe, _sz_calendar(), start=date(2020, 1, 2), end=date(2020, 1, 8)
    )
    by_day = dict(aligned)
    assert set(by_day) == {
        date(2020, 1, 2),
        date(2020, 1, 3),
        date(2020, 1, 6),
        date(2020, 1, 7),
        date(2020, 1, 8),
    }
    assert "000018.SZ" in by_day[date(2020, 1, 6)]
    assert "000018.SZ" not in by_day[date(2020, 1, 7)]
    assert all(code.endswith(".SZ") for members in by_day.values() for code in members)


# --- partitioning -------------------------------------------------------------------------


def test_the_registry_is_split_into_one_partition_per_lifecycle_year(tmp_path: Path) -> None:
    """One `stock_basic` fetch is a 36-year history, so `panel_partition_year` refuses it and
    `write_stock_universe` splits it. Each year's partition holds that year's events, which is
    what `PanelStore.record_coverage`'s own date-census rule requires."""
    store = _seed_registry(tmp_path)
    with pytest.raises(PanelBatchError, match=r"spans .* and has no single stock_basic"):
        panel_partition_year(_registry_batch(FETCHED_AT))

    years = store.registered_years(STOCK_BASIC_DATASET)
    assert years == (
        1990,
        1991,
        1992,
        1993,
        1997,
        2000,
        2002,
        2006,
        2014,
        2015,
        2016,
        2019,
        2020,
        2024,
        2026,
    )
    # 2020 holds exactly one event: 000018.SZ's delisting on 2020-01-07.
    assert store.query(
        STOCK_BASIC_DATASET,
        year=2020,
        columns=("subject", "lifecycle_event", "lifecycle_date"),
    ) == [
        ("000018.SZ", "delisting", "2020-01-07"),
        ("689009.SH", "listing", "2020-10-29"),
    ]


def test_every_row_of_a_split_batch_lands_in_exactly_one_partition(tmp_path: Path) -> None:
    batch = _registry_batch(FETCHED_AT)
    parts = split_panel_batch_by_year(batch)
    assert sum(yearly.row_count for _year, yearly in parts) == batch.row_count
    assert tuple(year for year, _ in parts) == tuple(sorted(year for year, _ in parts))
    rebuilt = {
        (subject, event)
        for _year, yearly in parts
        for subject, event in zip(
            yearly.subjects,
            next(c.values for c in yearly.columns if c.name == "lifecycle_event"),
            strict=True,
        )
    }
    original = set(
        zip(
            batch.subjects,
            next(c.values for c in batch.columns if c.name == "lifecycle_event"),
            strict=True,
        )
    )
    assert rebuilt == original


def test_a_split_carries_all_four_clocks_across_row_by_row(tmp_path: Path) -> None:
    """The split rebuilds every column, the four clock columns included, and nothing else in
    the suite reads a clock off a split part. Swapping `ingested_time` for `event_time` inside
    `_select_rows` left the whole suite green, which meant the timeline columns were being
    carried on trust rather than on a check.

    The clocks are pinned by value, not just for internal consistency: `stock_basic` dates a
    lifecycle row at its own event's Shanghai midnight, so `event_time == available_time ==
    revision_time` and differ per row, while `ingested_time` is the one fetch instant on every
    row of every partition. A swap in either direction breaks one of those two shapes.
    """
    batch = _registry_batch(FETCHED_AT)
    original = {
        subject_event: clocks
        for subject_event, clocks in zip(
            zip(
                batch.subjects,
                next(c.values for c in batch.columns if c.name == "lifecycle_event"),
                strict=True,
            ),
            zip(
                batch.timeline.event_time,
                batch.timeline.available_time,
                batch.timeline.ingested_time,
                batch.timeline.revision_time,
                strict=True,
            ),
            strict=True,
        )
    }
    assert len(original) == batch.row_count

    seen = 0
    for _year, yearly in split_panel_batch_by_year(batch):
        events = next(c.values for c in yearly.columns if c.name == "lifecycle_event")
        for index, key in enumerate(zip(yearly.subjects, events, strict=True)):
            assert (
                yearly.timeline.event_time[index],
                yearly.timeline.available_time[index],
                yearly.timeline.ingested_time[index],
                yearly.timeline.revision_time[index],
            ) == original[key], key
            seen += 1
        assert set(yearly.timeline.ingested_time) == {FETCHED_AT}
        assert yearly.timeline.event_time == yearly.timeline.available_time
        assert yearly.timeline.event_time == yearly.timeline.revision_time
    assert seen == batch.row_count
    # Every row's own event instant is distinct from the fetch instant, so a column that
    # substituted one for the other could not pass the comparison above by coincidence.
    assert FETCHED_AT not in set(batch.timeline.event_time)


def test_the_namechange_partition_is_keyed_on_the_announcement_year(tmp_path: Path) -> None:
    """`000001.SZ`'s 2012 rename was announced 2012-01-20 and took effect 2012-08-02, and the
    2007 one was announced 2007-06-14 and took effect 2007-06-20. Both land in their
    announcement year, because a `namechange` request window filters `ann_date` -- keying on
    the effective year would make one request straddle several partitions and several requests
    contend for one."""
    store = _seed_namechange(tmp_path)
    assert store.registered_years(NAMECHANGE_DATASET) == (1991, 2006, 2007, 2012, 2014, 2021)
    assert (
        panel_partition_year(
            _namechange_batch(datetime(2006, 12, 31, 12, 0, tzinfo=UTC), NAMECHANGE_BY_YEAR[2006])
        )
        == 2006
    )
    # That 2006-announced rename takes effect on 2006-10-09, and the *next* one is effective
    # 2007-06-20 but announced 2007-06-14 -- so the two live in different partitions on both
    # readings. The 2021 row is the one that separates them: announced 2021-04-30, effective
    # 2021-05-06, both in 2021.
    assert store.query(
        NAMECHANGE_DATASET,
        year=2006,
        columns=("subject", "effective_date", "announcement_date"),
    ) == [("000001.SZ", "2006-10-09", "2006-09-28")]


def test_the_2026_namechange_window_stores_nothing_because_its_only_row_is_unannounced(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    batch = _namechange_batch(FETCHED_AT, NAMECHANGE_BY_YEAR[2026])
    assert batch.status == "no_data"
    with pytest.raises(PanelBatchError, match="cannot write a 'no_data' batch"):
        write_name_history(store, batch)


# --- the name history and ST ---------------------------------------------------------------


def test_the_stored_name_history_answers_both_clocks(tmp_path: Path) -> None:
    store = _seed_namechange(tmp_path)
    histories = load_name_histories(
        store,
        years=(1991, 2006, 2007, 2012, 2014, 2021),
        as_of=FETCHED_AT,
        max_staleness=None,
    )
    ping_an = histories["000001.SZ"]
    assert ping_an.name_on(date(2012, 3, 1)) == "深发展A"
    assert ping_an.name_on(date(2012, 9, 1)) == "平安银行"
    pending = ping_an.pending_at(date(2012, 3, 1))
    assert tuple(record.name for record in pending) == ("平安银行",)
    assert pending[0].announced_on == date(2012, 1, 20)


def test_a_year_that_was_never_ingested_blocks_the_read_instead_of_shrinking_it(
    tmp_path: Path,
) -> None:
    store = _seed_namechange(tmp_path)
    with pytest.raises(RuntimeError, match="partition_missing"):
        load_name_histories(store, years=(1991, 1995), as_of=FETCHED_AT, max_staleness=None)


def test_reading_only_some_years_produces_a_history_that_says_so(tmp_path: Path) -> None:
    """Reading 2012 alone gives a history whose earliest record is 2012-08-02, and asking it
    about 2011 raises rather than answering the 2012 name."""
    store = _seed_namechange(tmp_path)
    histories = load_name_histories(store, years=(2012,), as_of=FETCHED_AT, max_staleness=None)
    ping_an = histories["000001.SZ"]
    assert ping_an.first_effective_date == date(2012, 8, 2)
    with pytest.raises(NameHistoryHorizonError, match=r"before 000001\.SZ's first known name"):
        ping_an.name_on(date(2011, 1, 1))


def test_the_delisting_period_name_survives_the_round_trip(tmp_path: Path) -> None:
    store = _seed_namechange(tmp_path)
    histories = load_name_histories(store, years=(2014,), as_of=FETCHED_AT, max_staleness=None)
    history = histories["600087.SH"]
    assert history.name_on(date(2014, 5, 1)) == "退市长油"
    assert history.risk_warning_on(date(2014, 5, 1)) is RiskWarning.delisting_process


def test_the_special_treatment_state_survives_the_round_trip(tmp_path: Path) -> None:
    store = _seed_namechange(tmp_path)
    histories = load_name_histories(store, years=(2021,), as_of=FETCHED_AT, max_staleness=None)
    assert histories["000005.SZ"].risk_warning_on(date(2021, 6, 1)) is RiskWarning.st


# --- fail-closed writes ---------------------------------------------------------------------


def test_a_registry_write_that_would_drop_securities_is_refused(tmp_path: Path) -> None:
    store = _seed_registry(tmp_path)
    # 000005.SZ listed 1990-12-10, and 000004.SZ listed 1990-12-01, so year 1990 is a
    # partition this batch still writes -- with one security fewer.
    fewer = [row for row in REGISTRY_ITEMS if row[0] != "000005.SZ"]
    with pytest.raises(PanelBatchError, match=r"would drop \['000005.SZ'\]"):
        write_stock_universe(store, _registry_batch(FETCHED_AT, fewer))


def test_the_calendars_exchange_guard_still_words_its_refusal_the_same_way(
    tmp_path: Path,
) -> None:
    """The overwrite guard is shared between the calendar and the registry now; this pins
    that generalising it did not change what the calendar's refusal says.

    It pins the **whole** message, remedy included. The earlier version of this test asserted
    `callable(_refuse_to_drop_stored_subjects)`, which is true of any function and was true of
    the guard before, during and after the generalisation -- replacing the calendar's entire
    remedy sentence with `"MUTATED REMEDY TEXT"` left the full suite green. The remedy is the
    only part of the message the shared guard does not compute, so it is the only part a
    caller can get wrong, and it is exactly what was unpinned.
    """
    store = _store(tmp_path)
    write_trading_calendar(store, _calendar_batch("SSE"))

    with pytest.raises(PanelBatchError) as captured:
        write_trading_calendar(store, _calendar_batch("SZSE"))

    assert str(captured.value) == (
        "trade_cal year=2024 already holds ['SSE'] and this batch carries ['SZSE']; writing "
        "it would drop ['SSE']. A partition is replaced whole and its key has no exchange "
        "dimension, so two exchanges have to arrive in one batch or not at all"
    )
    # The SSE calendar it would have destroyed is still there.
    assert store.read_coverage(TRADING_CALENDAR_DATASET, 2024) is not None


def test_the_registry_guard_words_its_own_remedy_and_not_the_calendars(tmp_path: Path) -> None:
    """The other caller of the same guard, pinned the same way. The shared half is identical
    byte for byte; the remedy is not, and it is the half that tells an operator what to do."""
    store = _seed_registry(tmp_path)
    fewer = [row for row in REGISTRY_ITEMS if row[0] != "000005.SZ"]

    with pytest.raises(PanelBatchError) as captured:
        write_stock_universe(store, _registry_batch(FETCHED_AT, fewer))

    assert str(captured.value) == (
        "stock_basic year=1990 already holds ['000004.SZ', '000005.SZ'] and this batch "
        "carries ['000004.SZ']; writing it would drop ['000005.SZ']. The registry only grows "
        "within a past year, so a smaller batch is a partial or filtered fetch rather than "
        "news; re-fetch the whole registry with list_status='L,D'"
    )


def test_a_refused_registry_write_leaves_the_store_exactly_as_it_found_it(
    tmp_path: Path,
) -> None:
    """The guard vets every year before any year is written.

    Interleaving them made the refusal itself destructive: this batch drops `689009.SH` from
    2020 *and* introduces a security whose lifecycle year (2018) the store has no partition
    for, so a vet-then-write-per-year loop creates the 2018 partition and only then refuses --
    leaving a store whose lifecycle years have a hole in them, which is precisely the state
    `load_stock_universe` cannot distinguish from a quiet year.
    """
    store = _seed_registry(tmp_path)
    before = store.registered_years(STOCK_BASIC_DATASET)
    assert 2018 not in before

    mutated = [row for row in REGISTRY_ITEMS if row[0] != "689009.SH"]
    mutated.append(["300750.SZ", "宁德时代", "SZSE", "创业板", "L", "20180611", None])

    with pytest.raises(PanelBatchError, match=r"would drop \['689009.SH'\]"):
        write_stock_universe(store, _registry_batch(FETCHED_AT, mutated))

    assert store.registered_years(STOCK_BASIC_DATASET) == before
    assert store.read_coverage(STOCK_BASIC_DATASET, 2018) is None


def test_writing_the_wrong_dataset_into_the_registry_writer_is_refused(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with pytest.raises(PanelBatchError, match="expected the 'stock_basic' dataset"):
        write_stock_universe(
            store,
            _namechange_batch(datetime(2012, 12, 31, 12, 0, tzinfo=UTC), NAMECHANGE_BY_YEAR[2012]),
        )


def test_writing_the_wrong_dataset_into_the_namechange_writer_is_refused(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with pytest.raises(PanelBatchError, match="expected the 'namechange' dataset"):
        write_name_history(store, _registry_batch(FETCHED_AT))


# --- readiness ---------------------------------------------------------------------------


def test_the_registry_requirement_records_the_checks_it_waives(tmp_path: Path) -> None:
    store = _seed_registry(tmp_path)
    requirement = stock_universe_requirement(
        years=REGISTRY_YEARS, as_of=FETCHED_AT, max_staleness=None
    )
    verdict = store.assess_readiness(requirement)
    assert verdict.is_ready
    assert set(verdict.checks_waived) == {
        "required_dates",
        "required_subjects",
        "max_staleness",
    }


def test_a_stale_registry_snapshot_blocks_when_a_bound_is_stated(tmp_path: Path) -> None:
    store = _seed_registry(tmp_path)
    requirement = stock_universe_requirement(
        years=REGISTRY_YEARS,
        as_of=datetime(2026, 12, 31, 12, 0, tzinfo=UTC),
        max_staleness=timedelta(days=30),
    )
    verdict = store.assess_readiness(requirement)
    assert not verdict.is_ready
    assert [issue.code for issue in verdict.issues] == ["stale"]
    with pytest.raises(RuntimeError, match="stale"):
        load_stock_universe(
            store,
            years=REGISTRY_YEARS,
            as_of=datetime(2026, 12, 31, 12, 0, tzinfo=UTC),
            max_staleness=timedelta(days=30),
        )


def test_the_registry_requirement_names_every_stored_field(tmp_path: Path) -> None:
    requirement = stock_universe_requirement(
        years=REGISTRY_YEARS, as_of=FETCHED_AT, max_staleness=None
    )
    assert requirement.required_fields == (
        "subject",
        "lifecycle_event",
        "lifecycle_date",
        "exchange",
    )


def test_the_namechange_requirement_names_every_stored_field() -> None:
    requirement = name_history_requirement(years=(2012,), as_of=FETCHED_AT, max_staleness=None)
    assert requirement.required_fields == (
        "subject",
        "name",
        "effective_date",
        "announcement_date",
        "change_reason",
    )


def test_a_registry_partition_that_was_never_written_blocks_the_load(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(RuntimeError, match="partition_missing"):
        load_stock_universe(store, years=REGISTRY_YEARS, as_of=FETCHED_AT, max_staleness=None)


def test_a_partial_read_of_the_registry_is_refused_rather_than_silently_smaller(
    tmp_path: Path,
) -> None:
    """The structural check `stock_universe_from_panel_rows` adds on top of readiness: a
    delisting row whose listing row is missing is a partial read, not a security that
    appeared already dead."""
    store = _seed_registry(tmp_path)
    rows = store.query(
        STOCK_BASIC_DATASET,
        year=2026,
        columns=("subject", "lifecycle_event", "lifecycle_date", "exchange"),
    )
    from openalpha_cn.domain.stock_universe import stock_universe_from_panel_rows

    trimmed = [row for row in rows if row[:2] != ("000018.SZ", "listing")]
    with pytest.raises(StockUniverseError, match="has a delisting row and no listing row"):
        stock_universe_from_panel_rows(trimmed, snapshot_date=date(2026, 8, 8))


# --- a lifecycle year that was never ingested ----------------------------------------------


def _seed_registry_without(tmp_path: Path, omitted: int) -> PanelStore:
    """Seed every lifecycle year except one, the way an interrupted backfill leaves a store."""
    store = _store(tmp_path)
    for year, yearly in split_panel_batch_by_year(_registry_batch(FETCHED_AT)):
        if year == omitted:
            continue
        write_panel_batch(store, yearly, year=year)
    return store


def test_a_lifecycle_year_that_was_never_ingested_reports_the_dead_as_still_listed(
    tmp_path: Path,
) -> None:
    """The fail-open this read has by default, reproduced rather than argued about.

    A year with no partition is indistinguishable from a year in which nothing listed and
    nothing died, so a read of `registered_years()` -- the natural argument, and the one the
    docstring recommends -- asks for exactly what the store happens to hold and can never
    notice that 2024 is absent. The result is not a smaller universe, which would at least be
    conservative: it is `000005.SZ`, terminated 2024-04-26, reported as listed today. On the
    real registry 2024 carries 52 terminations.
    """
    store = _seed_registry_without(tmp_path, 2024)
    registered = store.registered_years(STOCK_BASIC_DATASET)
    assert 2024 not in registered

    universe = load_stock_universe(store, years=registered, as_of=FETCHED_AT, max_staleness=None)

    assert universe.completeness().snapshot_date == date(2026, 8, 8)
    assert universe.status_on("000005.SZ", date(2024, 6, 1)) is ListingStatus.listed
    assert universe.is_listed("000005.SZ", date(2026, 8, 7)) is True
    assert "000005.SZ" in universe.listed_on(date(2026, 8, 7))


def test_a_coverage_demand_turns_that_silence_into_a_refusal(tmp_path: Path) -> None:
    """`require_years_through` is how a caller says which window its question needs. The same
    store, the same years, one extra argument -- and the read refuses instead of answering."""
    store = _seed_registry_without(tmp_path, 2024)
    registered = store.registered_years(STOCK_BASIC_DATASET)

    with pytest.raises(PanelBatchError, match="does not read") as captured:
        load_stock_universe(
            store,
            years=registered,
            as_of=FETCHED_AT,
            max_staleness=None,
            require_years_through=2026,
        )

    assert "every lifecycle year from 1990 through 2026" in str(captured.value)
    assert "still listed with nothing to signal it" in str(captured.value)


def test_a_coverage_demand_names_the_years_the_read_stops_short_of(tmp_path: Path) -> None:
    """A demand small enough to print in full, so the message itself is pinned. 2019 and 2020
    are not quiet years in this fixture -- 688001.SH lists in one and 000018.SZ dies in the
    other -- which is what makes stopping at 2016 a hole rather than a shorter history."""
    store = _seed_registry(tmp_path)

    with pytest.raises(PanelBatchError) as captured:
        load_stock_universe(
            store,
            years=(2014, 2015, 2016),
            as_of=FETCHED_AT,
            max_staleness=None,
            require_years_through=2020,
        )

    assert "from 2014 through 2020" in str(captured.value)
    assert "does not read [2017, 2018, 2019, 2020]" in str(captured.value)


def test_a_satisfied_coverage_demand_is_invisible(tmp_path: Path) -> None:
    """The demand is a precondition, not a filter: when it holds, the universe is the one the
    same read produces without it.

    On a store seeded only from 2016, so the read really is the one year and the equality is
    about the demand rather than about the history `V2-P4-059` now supplies underneath it.
    """
    store = _seed_registry_from(tmp_path, 2016)
    plain = load_stock_universe(store, years=(2016,), as_of=FETCHED_AT, max_staleness=None)
    demanded = load_stock_universe(
        store,
        years=(2016,),
        as_of=FETCHED_AT,
        max_staleness=None,
        require_years_through=2016,
    )

    assert demanded == plain
    assert demanded.listed_on(date(2016, 12, 31)) == ("601127.SH",)


def test_a_day_before_the_first_year_read_is_refused_rather_than_answered_empty(
    tmp_path: Path,
) -> None:
    """The other half of the same fail-open, on the query side.

    A universe read from 2016 onwards holds no security that listed earlier, so `listed_on` for
    a 2015 day would answer `()` -- the same value an honest empty market gives. It is a short
    read, not an empty market, and `years_read` is what lets the difference be seen. A universe
    built straight from `SecurityLifecycle` values has no window to check and keeps the empty
    answer.

    Still reachable after `V2-P4-059`, and this is the shape that reaches it: the store's
    earliest lifecycle partition *is* 2016, so there is no history to supply and the read is
    genuinely short. On a fully seeded store the same call now reads from 1990 and answers.
    """
    store = _seed_registry_from(tmp_path, 2016)
    universe = load_stock_universe(store, years=(2016,), as_of=FETCHED_AT, max_staleness=None)

    with pytest.raises(UniverseHorizonError, match="the first lifecycle year this universe"):
        universe.listed_on(date(2015, 12, 31))
    assert universe.listed_on(date(2016, 6, 15)) == ("601127.SH",)


# --- error-message shape -------------------------------------------------------------------


def test_a_drop_refusal_summarises_a_large_subject_set_instead_of_printing_it_whole() -> None:
    """The overwrite guard is shared with the calendar, whose partitions hold two subjects,
    and the registry's hold thousands. Small sets print in full -- which is what keeps the
    calendar's refusal byte-identical to what it was -- and large ones are capped."""
    from openalpha_cn.panel_ingest import _subject_sample

    assert _subject_sample(("SZSE", "SSE")) == "['SSE', 'SZSE']"
    rendered = _subject_sample(tuple(f"{index:06d}.SZ" for index in range(25)))
    assert rendered.startswith("['000000.SZ', '000001.SZ'")
    assert rendered.endswith("and 15 more")


def test_splitting_a_no_data_batch_is_refused_rather_than_answered_with_nothing(
    tmp_path: Path,
) -> None:
    batch = _namechange_batch(FETCHED_AT, NAMECHANGE_BY_YEAR[2026])
    with pytest.raises(PanelBatchError, match="cannot split a 'no_data' batch by year"):
        split_panel_batch_by_year(batch)


def test_reading_no_announcement_years_at_all_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PanelBatchError, match="needs at least one announcement year"):
        load_name_histories(_store(tmp_path), years=(), as_of=FETCHED_AT, max_staleness=None)
