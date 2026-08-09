"""`trade_cal` on the descriptor table, and the calendar's own point-in-time clock.

No network: every response below is the shape (and, where it matters, the exact rows) the
live Tushare `trade_cal` endpoint returned while this task was implemented -- four columns,
`is_open` as an `int`, and rows in **descending** `cal_date` order.

The two assertions this file exists for are the pair the calendar's availability rule has to
satisfy at once: standing at `as_of=2026-08-08`, `2026-12-31` **is** knowable (the exchange
published this year's calendar long ago and a live probe returns it) while `2027-01-04` is
**not** (the same probe returns zero rows for all of 2027).

The third thing it pins is the rule's **known defect**, in the "look-ahead" section below.
`available_time` is the start of the calendar date's own year, and the holiday schedule is
amended mid-year, so an amendment is dated as though it had been knowable on 1 January. The
fixtures there are the live rows for two amendments whose real announcement dates are public
record; they are here so that "we know this rule leaks look-ahead" is a failing test the day
someone improves the rule, rather than a sentence in a docstring.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest

from openalpha_cn.domain.panel_batch import ColumnarPanelBatch
from openalpha_cn.domain.trading_calendar import (
    KNOWN_CALENDAR_LOOKAHEAD,
    TRADING_CALENDAR_DATASET,
    CalendarDay,
    TradingCalendarError,
    build_trading_calendar,
)
from openalpha_cn.providers.base import (
    PanelDataProvider,
    ProviderFailure,
    ProviderRequest,
)
from openalpha_cn.providers.tushare import (
    _CHINA_TZ,
    _TUSHARE_DATASETS_BY_NAME,
    TUSHARE_DATASETS,
    ClockStrategy,
    TushareDatasetDescriptor,
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

# Real SSE rows around the 2015 Victory Day parade recess (3-4 September 2015), captured live
# and descending exactly as returned. The 2015 schedule published in late 2014 had both as
# ordinary sessions; the State Council General Office announced the closure on 2015-05-13.
_PARADE_RECESS_2015 = [
    ["SSE", "20150907", 1, "20150902"],
    ["SSE", "20150906", 0, "20150902"],
    ["SSE", "20150905", 0, "20150902"],
    ["SSE", "20150904", 0, "20150902"],
    ["SSE", "20150903", 0, "20150902"],
    ["SSE", "20150902", 1, "20150901"],
    ["SSE", "20150901", 1, "20150831"],
]

# Real SSE rows around the 2020 Spring Festival extension, captured live and descending. The
# 2020 schedule published 2019-11-21 had 20200131 as an open session; the extension announced
# on 2020-01-26/27 closed it, and pushed the reopening to 20200203.
_SPRING_FESTIVAL_EXTENSION_2020 = [
    ["SSE", "20200203", 1, "20200123"],
    ["SSE", "20200202", 0, "20200123"],
    ["SSE", "20200201", 0, "20200123"],
    ["SSE", "20200131", 0, "20200123"],
    ["SSE", "20200130", 0, "20200123"],
    ["SSE", "20200129", 0, "20200123"],
    ["SSE", "20200128", 0, "20200123"],
    ["SSE", "20200127", 0, "20200123"],
    ["SSE", "20200126", 0, "20200123"],
    ["SSE", "20200125", 0, "20200123"],
    ["SSE", "20200124", 0, "20200123"],
    ["SSE", "20200123", 1, "20200122"],
    ["SSE", "20200122", 1, "20200121"],
    ["SSE", "20200121", 1, "20200120"],
    ["SSE", "20200120", 1, "20200117"],
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


# --- the look-ahead this rule is known to leak -------------------------------------------


def _column(batch: ColumnarPanelBatch, name: str) -> tuple[object, ...]:
    (column,) = (candidate for candidate in batch.columns if candidate.name == name)
    return column.values


def _row_index(batch: ColumnarPanelBatch, day: str) -> int:
    return _column(batch, "cal_date").index(day)


def test_the_2015_parade_recess_is_answered_132_days_before_it_was_announced(
    fake_tushare_transport,
) -> None:
    """`available_time` is 1 January of the row's own year, so a closure the State Council
    announced on 2015-05-13 is served to an `as_of` in March 2015. The rows are real; the
    announcement date is public record. This is look-ahead, it is 132 days wide, and the
    point of the test is that it is *recorded* rather than believed to be impossible."""
    provider = _provider(
        datetime(2026, 8, 8, tzinfo=UTC), fake_tushare_transport, _PARADE_RECESS_2015
    )

    batch = provider.fetch_panel(_request(datetime(2015, 3, 1, tzinfo=UTC)))

    for day in ("2015-09-03", "2015-09-04"):
        index = _row_index(batch, day)
        assert _column(batch, "is_open")[index] is False
        assert batch.timeline.available_time[index] == datetime(2015, 1, 1, tzinfo=_CHINA_TZ)
    parade = tuple(
        entry
        for entry in KNOWN_CALENDAR_LOOKAHEAD
        if entry.calendar_date in (date(2015, 9, 3), date(2015, 9, 4))
    )
    assert len(parade) == 2
    assert {entry.announced_on for entry in parade} == {date(2015, 5, 13)}
    assert {entry.lookahead_days for entry in parade} == {132}


def test_the_2020_spring_festival_extension_flips_a_session_26_days_before_it_happened(
    fake_tushare_transport,
) -> None:
    """The worse of the two, because the verdict *inverts*: the schedule published
    2019-11-21 had 2020-01-31 open, and the extension announced on 2020-01-27 closed it. At
    `as_of=2020-01-20` this batch already reports it closed -- and reports 2020-02-03, the
    session the reopening was pushed to, as the next open day."""
    provider = _provider(
        datetime(2026, 8, 8, tzinfo=UTC),
        fake_tushare_transport,
        _SPRING_FESTIVAL_EXTENSION_2020,
    )

    batch = provider.fetch_panel(_request(datetime(2020, 1, 20, 12, 0, tzinfo=UTC)))

    closed = _row_index(batch, "2020-01-31")
    assert _column(batch, "is_open")[closed] is False
    assert batch.timeline.available_time[closed] == datetime(2020, 1, 1, tzinfo=_CHINA_TZ)
    assert _column(batch, "is_open")[_row_index(batch, "2020-02-03")] is True
    (entry,) = (e for e in KNOWN_CALENDAR_LOOKAHEAD if e.calendar_date == date(2020, 1, 31))
    assert entry.announced_on == date(2020, 1, 27)
    assert entry.lookahead_days == 26


def test_every_registered_lookahead_still_matches_the_clock_that_produces_it() -> None:
    """The fixture a future improvement to the availability rule has to *change*.

    Each registered instance says what `_calendar_publication_timeline` claims for that date
    and when the decision was really announced. If the rule ever learns a better bound, this
    fails, and updating it is how the improvement gets recorded.
    """
    ingested_at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)

    for entry in KNOWN_CALENDAR_LOOKAHEAD:
        timeline = _calendar_publication_timeline(
            {"cal_date": f"{entry.calendar_date:%Y%m%d}", "is_open": 0}, "cal_date", ingested_at
        )

        assert timeline.available_time == datetime(
            entry.claimed_available_from.year,
            entry.claimed_available_from.month,
            entry.claimed_available_from.day,
            tzinfo=_CHINA_TZ,
        )
        assert timeline.available_time.date() < entry.announced_on
        assert entry.announced_on < entry.calendar_date
        assert entry.lookahead_days > 0


def test_a_calendar_that_spans_a_known_lookahead_says_so_for_the_dependency_gate() -> None:
    """`V2-P1-013` blocks on facts, not on docstrings, so the instances are reachable from a
    loaded calendar rather than only from this module."""
    days = tuple(
        CalendarDay(
            calendar_date=date(int(cal_date[:4]), int(cal_date[4:6]), int(cal_date[6:])),
            is_trading=bool(is_open),
        )
        for _, cal_date, is_open, _ in _PARADE_RECESS_2015
    )

    calendar = build_trading_calendar("SSE", days)

    assert tuple(entry.calendar_date for entry in calendar.known_lookahead()) == (
        date(2015, 9, 3),
        date(2015, 9, 4),
    )


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
    fake_tushare_transport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stand-in was `adj_factor` until `V2-P1-006` gave it a projection, then `daily` until
    `V2-P1-007` gave it one. Every row of `TUSHARE_DATASETS` now declares one, so the subject
    has to be a descriptor this test builds: the refusal is a property of
    `TushareDatasetDescriptor`, whose `panel_columns` defaults to `()`, and the next dataset
    added is exactly when it has to still hold.
    """
    projectionless = TushareDatasetDescriptor(
        dataset="probe_only",
        kind="probe_only",
        subject_field="ts_code",
        date_field="trade_date",
        clock=ClockStrategy.daily_close,
        params_builder=lambda request: {},
        source_uri_template="tushare://{dataset}/{subject}/{date}",
    )
    assert projectionless.panel_columns == ()
    monkeypatch.setitem(_TUSHARE_DATASETS_BY_NAME, "probe_only", projectionless)
    provider = _provider(datetime(2026, 8, 8, tzinfo=UTC), fake_tushare_transport, [])

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch_panel(
            ProviderRequest(dataset="probe_only", as_of=datetime(2026, 8, 8, tzinfo=UTC))
        )

    assert captured.value.category == "configuration"
    assert "panel" in str(captured.value)


def test_an_unsupported_dataset_is_refused_by_fetch_panel(fake_tushare_transport) -> None:
    # The stand-in used to be `adj_factor`, which `V2-P1-006` then made a real dataset. A
    # name no Tushare endpoint can ever have is what this test actually needs.
    provider = _provider(datetime(2026, 8, 8, tzinfo=UTC), fake_tushare_transport, [])

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch_panel(
            ProviderRequest(
                dataset="not_a_tushare_endpoint", as_of=datetime(2026, 8, 8, tzinfo=UTC)
            )
        )

    assert str(captured.value) == "Unsupported Tushare dataset: not_a_tushare_endpoint"


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


def test_the_row_wise_path_hands_back_is_open_unparsed_and_cannot_build_a_calendar(
    fake_tushare_transport,
) -> None:
    """`_open_flag` runs in the panel projection, which only `fetch_panel()` applies.

    `fetch()` is payload-passthrough for every dataset, so the same `is_open=2` that
    `fetch_panel()` refuses (see above) arrives on `ProviderRecord.payload` untouched. That
    asymmetry is deliberate -- an evidence-plane record is the upstream response, which is
    what makes it re-provable -- and it is bounded rather than trusted: the value cannot
    become a calendar, because `build_trading_calendar` takes only an exact `bool`.
    """
    items = [["SSE", "20240212", 2, "20240208"]]
    provider = _provider(datetime(2024, 3, 1, tzinfo=UTC), fake_tushare_transport, items)

    result = provider.fetch(_request(datetime(2024, 3, 1, tzinfo=UTC)))

    payload = result.records[0].payload
    assert isinstance(payload, Mapping)
    assert payload["is_open"] == 2
    assert type(payload["is_open"]) is int
    with pytest.raises(TradingCalendarError):
        build_trading_calendar(
            "SSE",
            (CalendarDay(calendar_date=date(2024, 2, 12), is_trading=payload["is_open"]),),  # type: ignore[arg-type]
        )


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
