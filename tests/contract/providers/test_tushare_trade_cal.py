"""`trade_cal` on the descriptor table, and the calendar's own point-in-time clock.

No network: every response below is the shape (and, where it matters, the exact rows) the
live Tushare `trade_cal` endpoint returned while this task was implemented -- four columns,
`is_open` as an `int`, and rows in **descending** `cal_date` order.

The two assertions this file exists for are the pair the calendar's availability rule has to
satisfy at once: standing at `as_of=2026-08-08`, `2026-12-31` **is** knowable (the exchange
published this year's calendar long ago and a live probe returns it) while `2027-01-04` is
**not** (the same probe returns zero rows for all of 2027).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from openalpha_cn.domain.panel_batch import ColumnarPanelBatch
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.providers.base import (
    PanelDataProvider,
    ProviderFailure,
    ProviderRequest,
)
from openalpha_cn.providers.tushare import (
    _CHINA_TZ,
    TUSHARE_DATASETS,
    ClockStrategy,
    TushareProvider,
    _calendar_publication_timeline,
)

_FIELDS = ["exchange", "cal_date", "is_open", "pretrade_date"]

# Real SSE rows around the 2024 Spring Festival, descending exactly as returned.
_SPRING_FESTIVAL_ITEMS = [
    ["SSE", "20240219", 1, "20240208"],
    ["SSE", "20240218", 0, "20240208"],
    ["SSE", "20240217", 0, "20240208"],
    ["SSE", "20240216", 0, "20240208"],
    ["SSE", "20240215", 0, "20240208"],
    ["SSE", "20240214", 0, "20240208"],
    ["SSE", "20240213", 0, "20240208"],
    ["SSE", "20240212", 0, "20240208"],
    ["SSE", "20240211", 0, "20240208"],
    ["SSE", "20240210", 0, "20240208"],
    ["SSE", "20240209", 0, "20240208"],
    ["SSE", "20240208", 1, "20240207"],
]


def _response(items: list[list[Any]]) -> dict[str, Any]:
    return {"code": 0, "msg": None, "data": {"fields": list(_FIELDS), "items": items}}


def _provider(clock: datetime, fake_tushare_transport, items: list[list[Any]]) -> TushareProvider:
    return TushareProvider(
        token="secret-token",
        transport=fake_tushare_transport(_response(items)),
        clock=lambda: clock,
    )


def _request(as_of: datetime, subjects: tuple[str, ...] = ("SSE",)) -> ProviderRequest:
    return ProviderRequest(dataset=TRADING_CALENDAR_DATASET, as_of=as_of, subjects=subjects)


def _descriptor():
    (descriptor,) = (d for d in TUSHARE_DATASETS if d.dataset == TRADING_CALENDAR_DATASET)
    return descriptor


# --- the descriptor table grows by a row, not by an adapter ------------------------------


def test_trade_cal_is_registered_in_the_dataset_table() -> None:
    descriptor = _descriptor()

    assert descriptor.dataset == "trade_cal"
    assert descriptor.kind == "trade_cal"
    assert descriptor.subject_field == "exchange"
    assert descriptor.date_field == "cal_date"
    assert descriptor.clock is ClockStrategy.calendar_publication


def test_the_provider_advertises_the_new_dataset(fake_tushare_transport) -> None:
    provider = TushareProvider(token="secret-token", transport=fake_tushare_transport({}))

    assert "trade_cal" in provider.metadata.supported_datasets


def test_trade_cal_requests_the_whole_calendar_year_of_the_as_of(fake_tushare_transport) -> None:
    transport = fake_tushare_transport(_response(_SPRING_FESTIVAL_ITEMS))
    provider = TushareProvider(
        token="secret-token", transport=transport, clock=lambda: datetime(2024, 3, 1, tzinfo=UTC)
    )

    provider.fetch_panel(_request(datetime(2024, 3, 1, tzinfo=UTC)))

    assert transport.payload is not None
    assert transport.payload["api_name"] == "trade_cal"
    assert transport.payload["params"] == {
        "exchange": "SSE",
        "start_date": "20240101",
        "end_date": "20241231",
    }


def test_the_requested_year_is_the_as_of_year_in_shanghai_not_in_utc(
    fake_tushare_transport,
) -> None:
    """2024-12-31 17:00Z is already 2025-01-01 in Shanghai; asking UTC would fetch the wrong
    year's calendar for every evening request of the last day of a year."""
    transport = fake_tushare_transport(_response([["SSE", "20250102", 1, "20241231"]]))
    provider = TushareProvider(
        token="secret-token",
        transport=transport,
        clock=lambda: datetime(2025, 1, 2, tzinfo=UTC),
    )

    provider.fetch_panel(_request(datetime(2024, 12, 31, 17, 0, tzinfo=UTC)))

    assert transport.payload is not None
    assert transport.payload["params"]["start_date"] == "20250101"


def test_more_than_one_exchange_in_one_request_is_refused(fake_tushare_transport) -> None:
    provider = _provider(datetime(2024, 3, 1, tzinfo=UTC), fake_tushare_transport, [])

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch_panel(_request(datetime(2024, 3, 1, tzinfo=UTC), ("SSE", "SZSE")))

    assert captured.value.category == "configuration"


# --- the calendar's availability clock ---------------------------------------------------


def test_a_december_session_is_already_knowable_in_august_of_the_same_year() -> None:
    """(a) of the pair: `as_of=2026-08-08` must see `2026-12-31`."""
    ingested_at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)

    timeline = _calendar_publication_timeline(
        {"cal_date": "20261231", "is_open": 1}, "cal_date", ingested_at
    )

    assert timeline.event_time == datetime(2026, 12, 31, 0, 0, tzinfo=_CHINA_TZ)
    assert timeline.available_time == datetime(2026, 1, 1, 0, 0, tzinfo=_CHINA_TZ)
    assert timeline.available_time <= datetime(2026, 8, 8, tzinfo=UTC)
    assert timeline.revision_time == timeline.available_time


def test_next_years_calendar_is_not_knowable_at_this_years_as_of() -> None:
    """(b) of the pair: `as_of=2026-08-08` must not see `2027-01-04`."""
    ingested_at = datetime(2028, 5, 1, 12, 0, tzinfo=UTC)

    timeline = _calendar_publication_timeline(
        {"cal_date": "20270104", "is_open": 1}, "cal_date", ingested_at
    )

    assert timeline.available_time == datetime(2027, 1, 1, 0, 0, tzinfo=_CHINA_TZ)
    assert timeline.available_time > datetime(2026, 8, 8, tzinfo=UTC)


def test_availability_is_never_claimed_later_than_the_moment_the_row_was_observed() -> None:
    """The exchange publishes next year's calendar in *this* December. Anchoring availability
    at the start of the calendar date's own year alone would make that row unusable -- the
    four-clock contract forbids ingesting something before it was available -- so the rule
    takes the earlier of the two facts it actually has."""
    ingested_at = datetime(2026, 12, 15, 3, 0, tzinfo=UTC)

    timeline = _calendar_publication_timeline(
        {"cal_date": "20270104", "is_open": 1}, "cal_date", ingested_at
    )

    assert timeline.available_time == ingested_at
    assert timeline.ingested_time == ingested_at
    assert timeline.available_time > datetime(2026, 8, 8, tzinfo=UTC)


def test_the_calendar_clock_is_not_the_static_reference_clock() -> None:
    """`calendar_static` sets `available_time` to the row's own midnight, which would hide
    every future session from every earlier `as_of` -- the exact defect this dataset exists
    to remove."""
    ingested_at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)

    timeline = _calendar_publication_timeline(
        {"cal_date": "20261231", "is_open": 1}, "cal_date", ingested_at
    )

    assert timeline.available_time != timeline.event_time
    assert timeline.available_time < timeline.event_time


# --- fetch_panel ------------------------------------------------------------------------


def test_the_provider_satisfies_the_panel_data_provider_protocol(fake_tushare_transport) -> None:
    provider: PanelDataProvider = TushareProvider(
        token="secret-token", transport=fake_tushare_transport({})
    )

    assert provider.metadata.provider_id == "tushare.pro"


def test_a_descending_response_is_stored_in_ascending_calendar_order(
    fake_tushare_transport,
) -> None:
    provider = _provider(
        datetime(2024, 3, 1, tzinfo=UTC), fake_tushare_transport, _SPRING_FESTIVAL_ITEMS
    )

    batch = provider.fetch_panel(_request(datetime(2024, 3, 1, tzinfo=UTC)))

    (cal_date,) = (column for column in batch.columns if column.name == "cal_date")
    assert cal_date.values == tuple(sorted(cal_date.values))
    assert cal_date.values[0] == "2024-02-08"
    assert cal_date.values[-1] == "2024-02-19"
    assert batch.timeline.event_time == tuple(sorted(batch.timeline.event_time))


def test_the_batch_carries_the_columns_the_calendar_contract_reads(
    fake_tushare_transport,
) -> None:
    provider = _provider(
        datetime(2024, 3, 1, tzinfo=UTC), fake_tushare_transport, _SPRING_FESTIVAL_ITEMS
    )

    batch = provider.fetch_panel(_request(datetime(2024, 3, 1, tzinfo=UTC)))

    assert isinstance(batch, ColumnarPanelBatch)
    assert batch.dataset == "trade_cal"
    assert batch.kind == "trade_cal"
    assert batch.status == "success"
    assert set(batch.subjects) == {"SSE"}
    assert tuple(column.name for column in batch.columns) == (
        "cal_date",
        "is_open",
        "pretrade_date",
    )
    (is_open,) = (column for column in batch.columns if column.name == "is_open")
    assert is_open.kind == "boolean"
    assert is_open.values[0] is True
    assert is_open.values[3] is False
    (pretrade,) = (column for column in batch.columns if column.name == "pretrade_date")
    assert pretrade.values[0] == "2024-02-07"


def test_a_string_zero_open_flag_is_false_and_not_pythons_truthy_string(
    fake_tushare_transport,
) -> None:
    """`bool("0")` is `True`. A calendar that read the flag that way would report every
    holiday as a trading day, and nothing downstream would notice."""
    items = [["SSE", "20240212", "0", "20240208"], ["SSE", "20240211", "1", "20240208"]]
    provider = _provider(datetime(2024, 3, 1, tzinfo=UTC), fake_tushare_transport, items)

    batch = provider.fetch_panel(_request(datetime(2024, 3, 1, tzinfo=UTC)))

    (is_open,) = (column for column in batch.columns if column.name == "is_open")
    assert is_open.values == (True, False)


def test_an_open_flag_that_is_neither_zero_nor_one_is_refused(fake_tushare_transport) -> None:
    items = [["SSE", "20240212", 2, "20240208"]]
    provider = _provider(datetime(2024, 3, 1, tzinfo=UTC), fake_tushare_transport, items)

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch_panel(_request(datetime(2024, 3, 1, tzinfo=UTC)))

    assert captured.value.category == "invalid_response"


def test_rows_that_were_not_yet_knowable_at_the_as_of_are_dropped(
    fake_tushare_transport,
) -> None:
    """The whole point of (b): a store holding 2027 rows must not leak them into a 2026 read.
    Fetched in 2028, replayed at 2026-08-08."""
    items = [
        ["SSE", "20270104", 1, "20261231"],
        ["SSE", "20261231", 1, "20261230"],
        ["SSE", "20261230", 1, "20261229"],
    ]
    provider = _provider(datetime(2028, 5, 1, tzinfo=UTC), fake_tushare_transport, items)

    batch = provider.fetch_panel(_request(datetime(2026, 8, 8, tzinfo=UTC)))

    (cal_date,) = (column for column in batch.columns if column.name == "cal_date")
    assert cal_date.values == ("2026-12-30", "2026-12-31")


def test_the_same_rows_are_visible_once_the_as_of_moves_past_publication(
    fake_tushare_transport,
) -> None:
    items = [
        ["SSE", "20270104", 1, "20261231"],
        ["SSE", "20261231", 1, "20261230"],
        ["SSE", "20261230", 1, "20261229"],
    ]
    provider = _provider(datetime(2028, 5, 1, tzinfo=UTC), fake_tushare_transport, items)

    batch = provider.fetch_panel(_request(datetime(2027, 6, 1, tzinfo=UTC)))

    (cal_date,) = (column for column in batch.columns if column.name == "cal_date")
    assert cal_date.values == ("2026-12-30", "2026-12-31", "2027-01-04")


def test_an_unpublished_range_is_explicit_no_data_not_an_empty_success(
    fake_tushare_transport,
) -> None:
    """A live probe for 2027 returns zero rows. That is an unpublished horizon, and it has to
    arrive as an explicit no-data result rather than as a batch with nothing in it."""
    provider = _provider(datetime(2026, 8, 8, tzinfo=UTC), fake_tushare_transport, [])

    batch = provider.fetch_panel(_request(datetime(2027, 3, 1, tzinfo=UTC)))

    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "has not published" in batch.no_data_reason
    assert batch.row_count == 0


def test_rows_filtered_out_by_the_clock_report_a_different_no_data_reason(
    fake_tushare_transport,
) -> None:
    items = [["SSE", "20270104", 1, "20261231"]]
    provider = _provider(datetime(2028, 5, 1, tzinfo=UTC), fake_tushare_transport, items)

    batch = provider.fetch_panel(_request(datetime(2026, 8, 8, tzinfo=UTC)))

    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "not yet knowable" in batch.no_data_reason


def test_a_dataset_with_no_panel_projection_is_refused_by_fetch_panel(
    fake_tushare_transport,
) -> None:
    provider = _provider(datetime(2026, 8, 8, tzinfo=UTC), fake_tushare_transport, [])

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch_panel(
            ProviderRequest(dataset="daily", as_of=datetime(2026, 8, 8, tzinfo=UTC))
        )

    assert captured.value.category == "configuration"
    assert "panel" in str(captured.value)


def test_an_unsupported_dataset_is_refused_by_fetch_panel(fake_tushare_transport) -> None:
    provider = _provider(datetime(2026, 8, 8, tzinfo=UTC), fake_tushare_transport, [])

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch_panel(
            ProviderRequest(dataset="adj_factor", as_of=datetime(2026, 8, 8, tzinfo=UTC))
        )

    assert str(captured.value) == "Unsupported Tushare dataset: adj_factor"


def test_fetch_panel_without_a_token_fails_before_any_transport_call(
    fake_tushare_transport,
) -> None:
    transport = fake_tushare_transport(_response([]))
    provider = TushareProvider(token="", transport=transport)

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch_panel(_request(datetime(2026, 8, 8, tzinfo=UTC)))

    assert captured.value.category == "configuration"
    assert transport.payload is None


def test_an_upstream_rejection_is_a_provider_failure_not_an_empty_calendar(
    fake_tushare_transport,
) -> None:
    provider = TushareProvider(
        token="secret-token",
        transport=fake_tushare_transport({"code": -2001, "msg": "token invalid"}),
    )

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch_panel(_request(datetime(2026, 8, 8, tzinfo=UTC)))

    assert captured.value.category == "authentication"


def test_the_row_wise_fetch_still_works_for_the_new_dataset(fake_tushare_transport) -> None:
    """`trade_cal` is one row of the same descriptor table, so `fetch()` keeps working; the
    panel plane is where the calendar is *stored* (ADR-0002), not the only way to read it."""
    provider = _provider(
        datetime(2024, 3, 1, tzinfo=UTC), fake_tushare_transport, _SPRING_FESTIVAL_ITEMS
    )

    result = provider.fetch(_request(datetime(2024, 3, 1, tzinfo=UTC)))

    assert result.status == "success"
    assert len(result.records) == len(_SPRING_FESTIVAL_ITEMS)
    assert result.records[0].subject == "SSE"
    assert result.records[0].source_uri == "tushare://trade_cal/SSE/20240219"


def test_an_already_boolean_open_flag_passes_through_unchanged(fake_tushare_transport) -> None:
    """Tushare sends 0/1 today; a JSON `true`/`false` is the shape a schema change would take,
    and it is the one case where no conversion is needed rather than a wrong one."""
    items = [["SSE", "20240212", False, "20240208"], ["SSE", "20240211", True, "20240208"]]
    provider = _provider(datetime(2024, 3, 1, tzinfo=UTC), fake_tushare_transport, items)

    batch = provider.fetch_panel(_request(datetime(2024, 3, 1, tzinfo=UTC)))

    (is_open,) = (column for column in batch.columns if column.name == "is_open")
    assert is_open.values == (True, False)


def test_a_response_without_the_descriptors_date_column_is_refused(
    fake_tushare_transport,
) -> None:
    """Every clock and both decode paths key off the date column. Missing it used to surface
    as a `KeyError` from inside the clock builder, one row at a time."""
    response = {
        "code": 0,
        "msg": None,
        "data": {"fields": ["exchange", "is_open"], "items": [["SSE", 1]]},
    }
    provider = TushareProvider(
        token="secret-token",
        transport=fake_tushare_transport(response),
        clock=lambda: datetime(2024, 3, 1, tzinfo=UTC),
    )

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch_panel(_request(datetime(2024, 3, 1, tzinfo=UTC)))

    assert captured.value.category == "invalid_response"
    assert "cal_date" in str(captured.value)
