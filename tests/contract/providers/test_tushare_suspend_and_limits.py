"""`suspend_d` and `stk_limit` on the descriptor table (`V2-P1-008`).

Every response body here is a verbatim slice of a real one, recorded on 2026-08-09 against the
live endpoints, and nothing in this file touches the network. The two datasets are tested
together because the interesting decisions are the ones where they *differ*: they take opposite
answers on `requires_truncation_flag`, their caps are 5,000 and 7,800, and one of them has 67
rows of headroom while the other has thousands.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn.domain.daily_prices import DAILY_AVAILABILITY_TIME, SESSION_CLOSE_TIME
from openalpha_cn.domain.price_limits import (
    PRICE_LIMIT_DATASET,
    SUSPENSION_DATASET,
    price_limits_from_panel_rows,
    suspensions_from_panel_rows,
)
from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import (
    TUSHARE_DATASETS,
    TUSHARE_STK_LIMIT_ROW_CAP,
    TUSHARE_SUSPEND_ROW_CAP,
    ClockStrategy,
    TushareDatasetDescriptor,
    TushareProvider,
)

# 17:00 Asia/Shanghai on the session itself, so its 16:30 availability has passed.
FETCHED_AT = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)

SUSPEND_FIELDS = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
# Verbatim from suspend_d(trade_date=20240628): the two R rows and two of the 26 S rows.
SUSPEND_ITEMS: list[list[Any]] = [
    ["603050.SH", "20240628", None, "R"],
    ["000615.SZ", "20240628", None, "R"],
    ["000040.SZ", "20240628", None, "S"],
    ["000413.SZ", "20240628", None, "S"],
]
# Verbatim from suspend_d(trade_date=20150708): one of the 31 rows carrying an intraday window.
SUSPEND_TIMED_ITEM: list[Any] = ["000048.SZ", "20150708", "13:00-15:00", "S"]

LIMIT_FIELDS = ["trade_date", "ts_code", "up_limit", "down_limit"]
# Verbatim from stk_limit(trade_date=20240628).
LIMIT_ITEMS: list[list[Any]] = [
    ["20240628", "000001.SZ", 12.03, 9.85],
    ["20240628", "920924.BJ", 9.51, 5.13],
    ["20240628", "603381.SH", 99999.999, 0.01],
]


def _descriptor(dataset: str) -> TushareDatasetDescriptor:
    (descriptor,) = (entry for entry in TUSHARE_DATASETS if entry.dataset == dataset)
    return descriptor


def _response(fields: list[str], items: list[list[Any]], **data: Any) -> dict[str, Any]:
    return {"code": 0, "msg": "", "data": {"fields": fields, "items": items, **data}}


def _provider(
    transport_class: Any, response: dict[str, Any], *, clock: datetime = FETCHED_AT
) -> tuple[TushareProvider, Any]:
    transport = transport_class(response)
    return (
        TushareProvider(token="secret-token", transport=transport, clock=lambda: clock),
        transport,
    )


def _request(dataset: str, as_of: datetime = FETCHED_AT) -> ProviderRequest:
    return ProviderRequest(dataset=dataset, as_of=as_of)


def _panel_rows(batch: Any) -> list[tuple[object, ...]]:
    """The `(subject, *data)` rows a reader gets back from `PanelStore.query`.

    `ColumnarPanelBatch.to_rows()` is the *storage* block and carries the four clock columns as
    well, so it is not the shape the domain readers take. Rebuilding it here keeps this test at
    the provider/domain seam without standing a store up.
    """
    return list(zip(batch.subjects, *(column.values for column in batch.columns), strict=True))


# --- the descriptors ------------------------------------------------------------------------


def test_both_datasets_are_one_trading_day_of_the_whole_market(fake_tushare_transport) -> None:
    """The cross-section shape every dataset on this table uses, and the reason is arithmetic on
    both sides: one session of `stk_limit` is 7,733 rows against a 7,800 cap while one
    security's own band history is 4,762 rows and would fit -- but a partition is a year of
    sessions, and per-security fetching would be 5,500 requests instead of 244."""
    for dataset in (SUSPENSION_DATASET, PRICE_LIMIT_DATASET):
        descriptor = _descriptor(dataset)
        assert descriptor.subject_field == "ts_code"
        assert descriptor.date_field == "trade_date"
        assert descriptor.clock is ClockStrategy.daily_close
        assert descriptor.params_builder(_request(dataset)) == {"trade_date": "20240628"}


def test_the_two_caps_are_the_measured_ones_and_they_differ(fake_tushare_transport) -> None:
    """Measured on 2026-08-09 with multi-session windows, because one session of either fits:
    `stk_limit` returns exactly 7,800 rows with `has_more=True` for `20260701..20260807` and
    `suspend_d` exactly 5,000 for `20150601..20150831`, and `limit=8000` / `10000` / `12000`
    raise neither. A single global constant would refuse a complete `stk_limit` session while
    passing a truncated `suspend_d` window."""
    assert _descriptor(PRICE_LIMIT_DATASET).max_rows_per_response == TUSHARE_STK_LIMIT_ROW_CAP
    assert _descriptor(SUSPENSION_DATASET).max_rows_per_response == TUSHARE_SUSPEND_ROW_CAP
    assert TUSHARE_STK_LIMIT_ROW_CAP == 7800
    assert TUSHARE_SUSPEND_ROW_CAP == 5000


def test_only_the_band_dataset_demands_the_truncation_flag() -> None:
    """The two rows this issue adds take opposite answers, and the split is about what fills the
    hole a dropped row leaves. A missing halt makes `explain_unpriced` report *more* unexplained
    names -- fail-closed. A missing band makes `AShareExecutionPolicy` fall back to its derived
    rule, which disagrees with the exchange on 159 of 5,338 names on 2024-06-28 -- so the row is
    not absent, it is quietly replaced by a wrong number, which is `adj_factor`'s situation."""
    assert _descriptor(PRICE_LIMIT_DATASET).requires_truncation_flag is True
    assert _descriptor(SUSPENSION_DATASET).requires_truncation_flag is False


def test_a_band_response_with_no_truncation_flag_is_refused(fake_tushare_transport) -> None:
    provider, _ = _provider(fake_tushare_transport, _response(LIMIT_FIELDS, LIMIT_ITEMS))

    with pytest.raises(ProviderFailure, match="carries no has_more flag"):
        provider.fetch_panel(_request(PRICE_LIMIT_DATASET))


def test_a_halt_response_with_no_truncation_flag_is_accepted(fake_tushare_transport) -> None:
    """The residue of declining the flag, pinned rather than argued away: a `suspend_d` response
    that both omits `has_more` and comes in under 5,000 rows is stored."""
    provider, _ = _provider(fake_tushare_transport, _response(SUSPEND_FIELDS, SUSPEND_ITEMS))

    batch = provider.fetch_panel(_request(SUSPENSION_DATASET))

    assert batch.status == "success"
    assert len(batch.subjects) == 4


def test_a_response_at_either_cap_is_refused(fake_tushare_transport) -> None:
    """A response at the cap cannot be told from one the cap truncated, and the rows it drops
    are the oldest."""
    halts = [["000001.SZ", "20240628", None, "S"]] * TUSHARE_SUSPEND_ROW_CAP
    provider, _ = _provider(
        fake_tushare_transport, _response(SUSPEND_FIELDS, halts, has_more=False)
    )
    with pytest.raises(ProviderFailure, match=r"measured per-response cap of 5000"):
        provider.fetch_panel(_request(SUSPENSION_DATASET))

    bands = [["20240628", "000001.SZ", 12.03, 9.85]] * TUSHARE_STK_LIMIT_ROW_CAP
    provider, _ = _provider(fake_tushare_transport, _response(LIMIT_FIELDS, bands, has_more=False))
    with pytest.raises(ProviderFailure, match=r"measured per-response cap of 7800"):
        provider.fetch_panel(_request(PRICE_LIMIT_DATASET))


def test_a_whole_market_band_cross_section_still_fits_under_the_cap(
    fake_tushare_transport,
) -> None:
    """7,733 rows on 2026-08-07 against 7,800: 67 spare, after +349 and +517 in the two previous
    years. This asserts the margin exists today rather than that it will keep existing -- when
    it goes, `_check_response_completeness` refuses instead of storing a short session, and the
    escape route is `ProviderRequest.subjects`."""
    bands = [["20240628", f"{600000 + index}.SH", 12.03, 9.85] for index in range(7733)]
    provider, _ = _provider(fake_tushare_transport, _response(LIMIT_FIELDS, bands, has_more=False))

    assert len(provider.fetch_panel(_request(PRICE_LIMIT_DATASET)).subjects) == 7733


# --- the projections ------------------------------------------------------------------------


def test_the_halt_projection_round_trips_into_the_three_trading_states(
    fake_tushare_transport,
) -> None:
    """The provider's columns feed `suspensions_from_panel_rows` directly, so the two halves of
    this dataset's contract are exercised against one another rather than each against a
    hand-written row."""
    items = [*SUSPEND_ITEMS, ["000048.SZ", "20240628", "13:00-15:00", "S"]]
    provider, _ = _provider(
        fake_tushare_transport, _response(SUSPEND_FIELDS, items, has_more=False)
    )

    batch = provider.fetch_panel(_request(SUSPENSION_DATASET))
    days = suspensions_from_panel_rows(_panel_rows(batch))
    (day,) = days.values()

    assert day.halted == ("000040.SZ", "000413.SZ")
    assert day.resumed == ("000615.SZ", "603050.SH")
    assert day.interrupted == ("000048.SZ",)


def test_the_intraday_window_survives_the_projection_as_text(fake_tushare_transport) -> None:
    """The column that separates a whole-day halt from an intraday one. Dropping it, or
    normalising it to a boolean, is how 31 of 2015-07-08's 1,343 `S` rows become halts that in
    fact traded."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(SUSPEND_FIELDS, [SUSPEND_TIMED_ITEM], has_more=False),
        clock=datetime(2015, 7, 8, 9, 0, tzinfo=UTC),
    )

    as_of = datetime(2015, 7, 8, 9, 0, tzinfo=UTC)
    batch = provider.fetch_panel(_request(SUSPENSION_DATASET, as_of))
    (column,) = (col for col in batch.columns if col.name == "suspend_timing")

    assert column.values == ("13:00-15:00",)


def test_an_unknown_suspend_type_is_refused_at_the_provider(fake_tushare_transport) -> None:
    provider, _ = _provider(
        fake_tushare_transport,
        _response(SUSPEND_FIELDS, [["000001.SZ", "20240628", None, "H"]], has_more=False),
    )

    with pytest.raises(ProviderFailure, match="suspend_type must be 'S' or 'R'"):
        provider.fetch_panel(_request(SUSPENSION_DATASET))


def test_an_empty_intraday_window_is_refused_rather_than_read_as_a_whole_day_halt(
    fake_tushare_transport,
) -> None:
    """`_optional_calendar_date_text` folds `""` into `None` for `pretrade_date` and this column
    must not, because a null here is load-bearing: it *is* the whole-day halt."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(SUSPEND_FIELDS, [["000001.SZ", "20240628", "", "S"]], has_more=False),
    )

    with pytest.raises(ProviderFailure, match="expected a non-empty string"):
        provider.fetch_panel(_request(SUSPENSION_DATASET))


def test_the_limit_free_sentinel_is_stored_verbatim_rather_than_nulled(
    fake_tushare_transport,
) -> None:
    """`603381.SH` listed 2024-06-26 and had no limit on the 28th. Tushare says so with
    `up_limit=99999.999` and `down_limit=0.01`; normalising either to `None` at the provider
    would destroy the only statement that the security was unbounded that day."""
    provider, _ = _provider(
        fake_tushare_transport, _response(LIMIT_FIELDS, LIMIT_ITEMS, has_more=False)
    )

    batch = provider.fetch_panel(_request(PRICE_LIMIT_DATASET))
    limits = price_limits_from_panel_rows(_panel_rows(batch))

    assert limits["603381.SH"].up_limit == 99999.999
    assert limits["603381.SH"].down_limit == 0.01
    assert limits["603381.SH"].is_bounded(29.70) is False
    assert limits["920924.BJ"].up_limit == 9.51


def test_a_null_band_is_refused_and_names_its_column(fake_tushare_transport) -> None:
    provider, _ = _provider(
        fake_tushare_transport,
        _response(LIMIT_FIELDS, [["20240628", "000001.SZ", None, 9.85]], has_more=False),
    )

    with pytest.raises(ProviderFailure, match="up_limit must be a finite positive number"):
        provider.fetch_panel(_request(PRICE_LIMIT_DATASET))


def test_a_response_missing_a_projected_column_is_named_rather_than_a_bare_key_error(
    fake_tushare_transport,
) -> None:
    provider, _ = _provider(
        fake_tushare_transport,
        _response(
            ["trade_date", "ts_code", "up_limit"],
            [["20240628", "000001.SZ", 12.03]],
            has_more=False,
        ),
    )

    with pytest.raises(ProviderFailure, match="has no down_limit column"):
        provider.fetch_panel(_request(PRICE_LIMIT_DATASET))


# --- the clocks -----------------------------------------------------------------------------


def test_both_datasets_are_knowable_at_the_sessions_close_and_not_before(
    fake_tushare_transport,
) -> None:
    """16:30 Asia/Shanghai, the same instant `daily` uses and imported from the same constant.
    An `as_of` of 09:00 local on the session itself sees none of its rows, and the batch says
    "served but not yet knowable" rather than reporting an empty market."""
    morning = datetime(2024, 6, 28, 1, 0, tzinfo=UTC)  # 09:00 Asia/Shanghai
    for dataset, fields, items in (
        (SUSPENSION_DATASET, SUSPEND_FIELDS, SUSPEND_ITEMS),
        (PRICE_LIMIT_DATASET, LIMIT_FIELDS, LIMIT_ITEMS),
    ):
        provider, _ = _provider(fake_tushare_transport, _response(fields, items, has_more=False))
        batch = provider.fetch_panel(_request(dataset, morning))

        assert batch.status == "no_data"
        assert "not yet knowable is not the same as absent" in (batch.no_data_reason or "")

    for dataset, fields, items in (
        (SUSPENSION_DATASET, SUSPEND_FIELDS, SUSPEND_ITEMS),
        (PRICE_LIMIT_DATASET, LIMIT_FIELDS, LIMIT_ITEMS),
    ):
        provider, _ = _provider(fake_tushare_transport, _response(fields, items, has_more=False))
        batch = provider.fetch_panel(_request(dataset))
        shanghai = ZoneInfo("Asia/Shanghai")

        assert batch.timeline.event_time[0] == datetime(
            2024, 6, 28, SESSION_CLOSE_TIME.hour, SESSION_CLOSE_TIME.minute, tzinfo=shanghai
        )
        assert batch.timeline.available_time[0] == datetime(
            2024,
            6,
            28,
            DAILY_AVAILABILITY_TIME.hour,
            DAILY_AVAILABILITY_TIME.minute,
            tzinfo=shanghai,
        )


def test_both_datasets_serve_the_evidence_plane_too(fake_tushare_transport) -> None:
    """Unlike `stock_basic` and `namechange`, a row of either dataset carries facts from exactly
    one instant, so `fetch()` can hand it back verbatim under one `available_time`."""
    for dataset, fields, items in (
        (SUSPENSION_DATASET, SUSPEND_FIELDS, SUSPEND_ITEMS),
        (PRICE_LIMIT_DATASET, LIMIT_FIELDS, LIMIT_ITEMS),
    ):
        provider, _ = _provider(fake_tushare_transport, _response(fields, items, has_more=False))
        batch = provider.fetch(_request(dataset))

        assert batch.status == "success"
        assert batch.records[0].kind == dataset


def test_the_token_never_reaches_a_record_or_a_source_uri(fake_tushare_transport) -> None:
    provider, transport = _provider(
        fake_tushare_transport, _response(LIMIT_FIELDS, LIMIT_ITEMS, has_more=False)
    )

    batch = provider.fetch_panel(_request(PRICE_LIMIT_DATASET))

    assert transport.payload is not None
    assert transport.payload["token"] == "secret-token"
    assert "secret-token" not in batch.source_uri
    assert all("secret-token" not in str(value) for value in batch.columns[0].values)
