"""`trade_cal` end to end: Tushare -> columnar batch -> Parquet partition -> calendar.

The whole chain runs against a real `PanelStore` on a real DuckDB catalog with real Parquet
files. Only the HTTP transport is doubled, and what it serves is real published data: the
holiday sets below are Tushare's own, captured live, and everything else is derived from the
one property the same probe established over all 13,162 published SSE rows -- that no weekend
has ever been an open session.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.trading_calendar import (
    CALENDAR_PANEL_COLUMNS,
    TRADING_CALENDAR_DATASET,
    CalendarDayStatus,
    TradingCalendarError,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_trading_calendar,
    panel_partition_year,
    panel_readiness_requirement,
    trading_calendar_requirement,
    write_trading_calendar,
)
from openalpha_cn.providers.base import ProviderRequest
from openalpha_cn.providers.tushare import TushareProvider

# Tushare's real closed *weekdays* for these years, verbatim from a live probe. Weekends are
# not listed because the same probe found zero open weekends in the entire published history,
# so `weekday() >= 5 => closed` is a measured fact about this data rather than an assumption.
CLOSED_WEEKDAYS_2024 = frozenset(
    {
        "20240101",
        "20240209",
        "20240212",
        "20240213",
        "20240214",
        "20240215",
        "20240216",
        "20240404",
        "20240405",
        "20240501",
        "20240502",
        "20240503",
        "20240610",
        "20240916",
        "20240917",
        "20241001",
        "20241002",
        "20241003",
        "20241004",
        "20241007",
    }
)
CLOSED_WEEKDAYS_2026 = frozenset(
    {
        "20260101",
        "20260102",
        "20260216",
        "20260217",
        "20260218",
        "20260219",
        "20260220",
        "20260223",
        "20260406",
        "20260501",
        "20260504",
        "20260505",
        "20260619",
        "20260925",
        "20261001",
        "20261002",
        "20261005",
        "20261006",
        "20261007",
    }
)
CLOSED_WEEKDAYS = {2024: CLOSED_WEEKDAYS_2024, 2026: CLOSED_WEEKDAYS_2026}

_FIELDS = ["exchange", "cal_date", "is_open", "pretrade_date"]


def _year_items(year: int, exchange: str = "SSE") -> list[list[Any]]:
    """One whole published year, in the descending order Tushare returns.

    `pretrade_date` is `None` until the year's first session, where the real endpoint would
    name the previous year's last one. That is the one place this generator is narrower than
    the live data, and it is the null branch `_optional_calendar_date_text` exists for.
    """
    closed = CLOSED_WEEKDAYS[year]
    days: list[list[Any]] = []
    previous_open: str | None = None
    cursor = date(year, 1, 1)
    while cursor.year == year:
        stamp = f"{cursor:%Y%m%d}"
        is_open = cursor.weekday() < 5 and stamp not in closed
        days.append([exchange, stamp, int(is_open), previous_open])
        if is_open:
            previous_open = stamp
        cursor += timedelta(days=1)
    return list(reversed(days))


class _ScriptedTransport:
    """Serves whichever year the request's `params` asked for; records every payload."""

    def __init__(self, years: dict[int, list[list[Any]]]) -> None:
        self.years = years
        self.payloads: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        year = int(payload["params"]["start_date"][:4])
        return {
            "code": 0,
            "msg": None,
            "data": {"fields": list(_FIELDS), "items": self.years.get(year, [])},
        }


def _provider(years: dict[int, list[list[Any]]], clock: datetime) -> TushareProvider:
    return TushareProvider(
        token="secret-token", transport=_ScriptedTransport(years), clock=lambda: clock
    )


def _ingest(store: PanelStore, year: int, *, clock: datetime, exchange: str = "SSE") -> None:
    provider = _provider({year: _year_items(year, exchange)}, clock)
    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=TRADING_CALENDAR_DATASET,
            # Mid-year: every row of the year carries the same availability instant
            # (1 January), so the whole year is visible and none of it is hindsight.
            as_of=datetime(year, 6, 1, 12, 0, tzinfo=UTC),
            subjects=(exchange,),
        )
    )
    write_trading_calendar(store, batch)


@pytest.fixture
def store(tmp_path: Path) -> PanelStore:
    return PanelStore(tmp_path / "panel", clock=lambda: datetime(2026, 8, 8, tzinfo=UTC))


# --- the round trip ----------------------------------------------------------------------


def test_a_fetched_calendar_round_trips_through_parquet_into_a_trading_calendar(
    store: PanelStore,
) -> None:
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))

    calendar = load_trading_calendar(
        store, exchange="SSE", years=(2024,), as_of=datetime(2025, 6, 1, tzinfo=UTC)
    )

    assert calendar.exchange == "SSE"
    assert calendar.horizon.first_date == date(2024, 1, 1)
    assert calendar.horizon.last_date == date(2024, 12, 31)
    # 242 open sessions in 2024, measured live against the real endpoint.
    assert calendar.trading_day_count == 242
    assert calendar.is_trading_day(date(2024, 2, 12)) is False
    assert calendar.is_trading_day(date(2024, 2, 19)) is True
    assert calendar.next_trading_day(date(2024, 2, 8)) == date(2024, 2, 19)


def test_the_partition_lands_under_the_year_the_batch_itself_names(store: PanelStore) -> None:
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))

    assert store.registered_years(TRADING_CALENDAR_DATASET) == (2024,)
    assert (store.root / TRADING_CALENDAR_DATASET / "2024" / "data.parquet").is_file()


def test_the_open_flag_survives_the_parquet_round_trip_as_a_real_bool(
    store: PanelStore,
) -> None:
    """`trading_calendar_from_panel_rows` re-checks the type on the way back in, so this
    proves the check is satisfied by the real storage path rather than only by the parser."""
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))

    rows = store.query(
        TRADING_CALENDAR_DATASET,
        year=2024,
        columns=CALENDAR_PANEL_COLUMNS,
        filters={"cal_date": "2024-02-12"},
    )

    assert len(rows) == 1
    assert rows[0][1] is False
    assert type(rows[0][1]) is bool


def test_tushares_pretrade_column_survives_and_still_agrees_with_the_derivation(
    store: PanelStore,
) -> None:
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))
    calendar = load_trading_calendar(
        store, exchange="SSE", years=(2024,), as_of=datetime(2025, 6, 1, tzinfo=UTC)
    )

    rows = store.query(
        TRADING_CALENDAR_DATASET,
        year=2024,
        columns=CALENDAR_PANEL_COLUMNS,
        filters={"cal_date": "2024-02-19"},
    )

    assert rows[0][2] == "2024-02-08"
    assert calendar.previous_trading_day(date(2024, 2, 19)) == date(2024, 2, 8)


# --- fail-closed on the way out ----------------------------------------------------------


def test_a_year_that_was_never_ingested_blocks_instead_of_reading_as_a_holiday(
    store: PanelStore,
) -> None:
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))

    with pytest.raises(TradingCalendarError) as captured:
        load_trading_calendar(
            store, exchange="SSE", years=(2025,), as_of=datetime(2026, 1, 2, tzinfo=UTC)
        )

    assert "partition_missing" in str(captured.value)


def test_a_gap_between_ingested_years_is_refused_rather_than_read_as_a_long_recess(
    store: PanelStore,
) -> None:
    """Both partitions are present, healthy and ready. The 2025 hole between them is exactly
    what the readiness contract cannot see and the calendar's contiguity rule can."""
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))
    _ingest(store, 2026, clock=datetime(2026, 8, 8, tzinfo=UTC))

    requirement = trading_calendar_requirement(
        exchange="SSE", years=(2024, 2026), as_of=datetime(2026, 8, 8, tzinfo=UTC)
    )
    readiness = store.assess_readiness(requirement)
    assert readiness.state == "ready"

    with pytest.raises(TradingCalendarError) as captured:
        load_trading_calendar(
            store, exchange="SSE", years=(2024, 2026), as_of=datetime(2026, 8, 8, tzinfo=UTC)
        )

    assert "2025-01-01" in str(captured.value)


def test_an_unknown_exchange_blocks_rather_than_returning_an_empty_calendar(
    store: PanelStore,
) -> None:
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))

    with pytest.raises(TradingCalendarError) as captured:
        load_trading_calendar(
            store, exchange="SZSE", years=(2024,), as_of=datetime(2025, 6, 1, tzinfo=UTC)
        )

    assert "subject_missing" in str(captured.value)


def test_loading_no_years_at_all_is_refused(store: PanelStore) -> None:
    with pytest.raises(TradingCalendarError):
        load_trading_calendar(
            store, exchange="SSE", years=(), as_of=datetime(2025, 6, 1, tzinfo=UTC)
        )


def test_the_calendars_own_requirement_records_the_two_checks_it_waives(
    store: PanelStore,
) -> None:
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))

    readiness = store.assess_readiness(
        trading_calendar_requirement(
            exchange="SSE", years=(2024,), as_of=datetime(2025, 6, 1, tzinfo=UTC)
        )
    )

    assert readiness.state == "ready"
    assert set(readiness.checks_waived) == {"required_dates", "max_staleness"}


# --- the partition year is derived, not guessed ------------------------------------------


def test_the_partition_year_comes_from_the_batch_in_the_declared_timezone() -> None:
    provider = _provider({2024: _year_items(2024)}, datetime(2025, 1, 2, tzinfo=UTC))
    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=TRADING_CALENDAR_DATASET,
            as_of=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
            subjects=("SSE",),
        )
    )

    assert panel_partition_year(batch) == 2024


def test_resolving_the_partition_year_in_utc_would_misfile_the_new_year_session() -> None:
    """A 2024-01-01 session is 2023-12-31 16:00 UTC. The Shanghai answer is 2024; the UTC one
    would be 2023, and the batch would be written into a partition it does not belong to."""
    provider = _provider({2024: _year_items(2024)}, datetime(2025, 1, 2, tzinfo=UTC))
    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=TRADING_CALENDAR_DATASET,
            as_of=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
            subjects=("SSE",),
        )
    )

    assert panel_partition_year(batch, date_timezone="Asia/Shanghai") == 2024
    with pytest.raises(PanelBatchError) as captured:
        panel_partition_year(batch, date_timezone="UTC")

    assert "[2023, 2024]" in str(captured.value)


def test_a_batch_that_straddles_two_years_refuses_to_name_one_partition() -> None:
    items = _year_items(2024)[:5] + _year_items(2026)[:5]
    provider = _provider({2026: items}, datetime(2026, 8, 8, tzinfo=UTC))
    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=TRADING_CALENDAR_DATASET,
            as_of=datetime(2026, 8, 8, tzinfo=UTC),
            subjects=("SSE",),
        )
    )

    with pytest.raises(PanelBatchError) as captured:
        panel_partition_year(batch)

    assert "[2024, 2026]" in str(captured.value)


def test_a_no_data_batch_has_no_partition_year_and_is_not_written(store: PanelStore) -> None:
    provider = _provider({}, datetime(2026, 8, 8, tzinfo=UTC))
    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=TRADING_CALENDAR_DATASET,
            as_of=datetime(2027, 3, 1, tzinfo=UTC),
            subjects=("SSE",),
        )
    )

    assert batch.status == "no_data"
    with pytest.raises(PanelBatchError):
        panel_partition_year(batch)
    with pytest.raises(PanelBatchError):
        write_trading_calendar(store, batch)
    assert store.registered_years(TRADING_CALENDAR_DATASET) == ()


# --- what V2-P1-003 was waiting for ------------------------------------------------------


def test_the_calendar_supplies_real_required_dates_to_the_readiness_contract(
    store: PanelStore,
) -> None:
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))
    calendar = load_trading_calendar(
        store, exchange="SSE", years=(2024,), as_of=datetime(2025, 6, 1, tzinfo=UTC)
    )

    requirement = panel_readiness_requirement(
        calendar,
        "daily",
        as_of=datetime(2025, 6, 1, tzinfo=UTC),
        years=(2024,),
        required_subjects=("000001.SZ",),
        required_fields=("close",),
        max_staleness=None,
    )

    assert requirement.required_dates is not None
    assert len(requirement.required_dates) == 242
    assert date(2024, 2, 19) in requirement.required_dates
    # The dates the weekday approximation would have demanded and the exchange never held.
    for holiday in (date(2024, 2, 12), date(2024, 10, 1), date(2024, 5, 1)):
        assert holiday not in requirement.required_dates
    assert store.assess_readiness(requirement).issues[0].code == "partition_missing"


def test_required_dates_stop_at_the_as_of_instead_of_demanding_future_sessions(
    store: PanelStore,
) -> None:
    _ingest(store, 2026, clock=datetime(2026, 8, 8, tzinfo=UTC))
    calendar = load_trading_calendar(
        store, exchange="SSE", years=(2026,), as_of=datetime(2026, 8, 8, tzinfo=UTC)
    )

    requirement = panel_readiness_requirement(
        calendar,
        "daily",
        as_of=datetime(2026, 8, 8, tzinfo=UTC),
        years=(2026,),
        required_subjects=None,
        required_fields=None,
        max_staleness=None,
    )

    assert requirement.required_dates is not None
    assert max(requirement.required_dates) <= date(2026, 8, 8)
    assert date(2026, 12, 31) not in requirement.required_dates
    assert calendar.day_status(date(2026, 12, 31)) is CalendarDayStatus.trading


def test_a_year_that_has_not_begun_cannot_have_required_dates(store: PanelStore) -> None:
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))
    calendar = load_trading_calendar(
        store, exchange="SSE", years=(2024,), as_of=datetime(2025, 6, 1, tzinfo=UTC)
    )

    with pytest.raises(TradingCalendarError):
        panel_readiness_requirement(
            calendar,
            "daily",
            as_of=datetime(2024, 6, 1, tzinfo=UTC),
            years=(2025,),
            required_subjects=None,
            required_fields=None,
            max_staleness=None,
        )


def test_required_dates_refuse_a_year_the_loaded_calendar_only_half_covers(
    store: PanelStore,
) -> None:
    """A requirement built from a partial calendar would under-require the rest of the year,
    which is a silent fail-open; `trading_days_between` refuses instead."""
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))
    calendar = load_trading_calendar(
        store, exchange="SSE", years=(2024,), as_of=datetime(2025, 6, 1, tzinfo=UTC)
    )

    with pytest.raises(TradingCalendarError):
        panel_readiness_requirement(
            calendar,
            "daily",
            as_of=datetime(2026, 6, 1, tzinfo=UTC),
            years=(2025,),
            required_subjects=None,
            required_fields=None,
            max_staleness=None,
        )


def test_the_coverage_record_uses_the_calendars_own_timezone_convention(
    store: PanelStore,
) -> None:
    _ingest(store, 2024, clock=datetime(2025, 1, 2, tzinfo=UTC))

    coverage = store.read_coverage(TRADING_CALENDAR_DATASET, 2024)

    assert coverage is not None
    assert coverage.date_timezone == "Asia/Shanghai"
    assert coverage.first_event_date == date(2024, 1, 1)
    assert coverage.last_event_date == date(2024, 12, 31)
    assert coverage.row_count == 366
    assert coverage.subjects == ("SSE",)
    assert tuple(field.name for field in coverage.fields) == (
        "subject",
        "event_time",
        "available_time",
        "ingested_time",
        "revision_time",
        "cal_date",
        "is_open",
        "pretrade_date",
    )


def test_write_trading_calendar_refuses_a_batch_from_another_dataset(
    store: PanelStore,
) -> None:
    """`write_trading_calendar` derives the partition year instead of asking for it, which is
    only safe for a dataset whose request covers exactly one year."""
    provider = _provider({2024: _year_items(2024)}, datetime(2025, 1, 2, tzinfo=UTC))
    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=TRADING_CALENDAR_DATASET,
            as_of=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
            subjects=("SSE",),
        )
    )
    relabelled = replace(batch, dataset="daily")

    with pytest.raises(PanelBatchError) as captured:
        write_trading_calendar(store, relabelled)

    assert "trade_cal" in str(captured.value)
