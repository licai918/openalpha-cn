"""Descriptor-table skeleton: `daily` migrated onto it, two clocks proven on synthetic data.

The core safety net is `test_daily_descriptor_decode_matches_legacy_field_for_field`: it pins
the exact `ProviderRecord` the pre-refactor `_decode_daily` produced, so the descriptor-driven
`_decode` cannot silently change observable behavior for the one dataset already in production.
"""

from datetime import UTC, datetime

import pytest

from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import (
    _CHINA_TZ,
    TUSHARE_DATASETS,
    ClockStrategy,
    TushareDatasetDescriptor,
    TushareProvider,
    _dataset_names,
)


class FakeTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    def post(self, payload: dict[str, object]) -> dict[str, object]:
        return self.response


def _provider(clock: datetime) -> TushareProvider:
    return TushareProvider(token="secret-token", transport=FakeTransport({}), clock=lambda: clock)


def _daily_descriptor() -> TushareDatasetDescriptor:
    (descriptor,) = (d for d in TUSHARE_DATASETS if d.dataset == "daily")
    return descriptor


# --- descriptor table shape ------------------------------------------------


def test_daily_dataset_descriptor_declares_expected_fields() -> None:
    descriptor = _daily_descriptor()

    assert descriptor.dataset == "daily"
    assert descriptor.kind == "daily"
    assert descriptor.subject_field == "ts_code"
    assert descriptor.date_field == "trade_date"
    assert descriptor.clock == ClockStrategy.daily_close


def test_dataset_names_derives_from_descriptor_table_and_grows_with_it() -> None:
    def _params(request: ProviderRequest) -> dict[str, str]:
        return {}

    first = TushareDatasetDescriptor(
        dataset="alpha",
        kind="alpha",
        subject_field="ts_code",
        date_field="trade_date",
        clock=ClockStrategy.daily_close,
        params_builder=_params,
        source_uri_template="tushare://{dataset}/{subject}/{date}",
    )
    second = TushareDatasetDescriptor(
        dataset="beta",
        kind="beta",
        subject_field=None,
        date_field="cal_date",
        clock=ClockStrategy.calendar_static,
        params_builder=_params,
        source_uri_template="tushare://{dataset}/{date}",
    )

    assert _dataset_names((first,)) == ("alpha",)
    assert _dataset_names((first, second)) == ("alpha", "beta")


def test_provider_metadata_supported_datasets_matches_the_descriptor_table() -> None:
    provider = TushareProvider(token="secret-token", transport=FakeTransport({}))

    assert provider.metadata.supported_datasets == _dataset_names(TUSHARE_DATASETS)


# --- daily behaves identically through the descriptor path -----------------


def test_daily_descriptor_decode_matches_legacy_field_for_field() -> None:
    provider = _provider(datetime(2026, 7, 24, 10, 0, tzinfo=UTC))
    response = {
        "code": 0,
        "msg": None,
        "data": {
            "fields": ["ts_code", "trade_date", "close", "pct_chg"],
            "items": [["000001.SZ", "20260724", 10.5, 9.99]],
        },
    }
    request = ProviderRequest(
        dataset="daily",
        as_of=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
        subjects=("000001.SZ",),
    )

    (record,) = provider._decode(descriptor=_daily_descriptor(), response=response, request=request)

    assert record.schema_version == "provider-record/v1"
    assert record.subject == "000001.SZ"
    assert record.kind == "daily"
    assert record.timeline.event_time == datetime(2026, 7, 24, 15, 0, tzinfo=_CHINA_TZ)
    assert record.timeline.available_time == datetime(2026, 7, 24, 16, 30, tzinfo=_CHINA_TZ)
    assert record.timeline.revision_time == record.timeline.available_time
    assert record.timeline.ingested_time == datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
    assert record.source_uri == "tushare://daily/000001.SZ/20260724"
    assert record.summary == "Tushare daily record for 000001.SZ on 2026-07-24."
    assert record.payload == {
        "ts_code": "000001.SZ",
        "trade_date": "20260724",
        "close": 10.5,
        "pct_chg": 9.99,
    }


def test_unsupported_dataset_still_raises_configuration_failure_with_original_message_shape() -> (
    None
):
    provider = TushareProvider(token="secret-token", transport=FakeTransport({}))

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch(
            ProviderRequest(
                dataset="fina_indicator",
                as_of=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
            )
        )

    assert captured.value.category == "configuration"
    assert captured.value.retryable is False
    assert str(captured.value) == "Unsupported Tushare dataset: fina_indicator"


# --- clock strategies, proven independently on synthetic data --------------


def test_daily_close_clock_uses_1500_event_and_1630_available_in_asia_shanghai() -> None:
    def _params(request: ProviderRequest) -> dict[str, str]:
        return {}

    descriptor = TushareDatasetDescriptor(
        dataset="daily_basic",
        kind="daily_basic",
        subject_field="ts_code",
        date_field="trade_date",
        clock=ClockStrategy.daily_close,
        params_builder=_params,
        source_uri_template="tushare://{dataset}/{subject}/{date}",
    )
    response = {
        "code": 0,
        "msg": None,
        "data": {
            "fields": ["ts_code", "trade_date", "turnover_rate"],
            "items": [["600000.SH", "20260102", 1.23]],
        },
    }
    provider = _provider(datetime(2026, 1, 3, 1, 0, tzinfo=UTC))
    request = ProviderRequest(dataset="daily_basic", as_of=datetime(2026, 1, 3, 1, 0, tzinfo=UTC))

    (record,) = provider._decode(descriptor=descriptor, response=response, request=request)

    assert record.timeline.event_time == datetime(2026, 1, 2, 15, 0, tzinfo=_CHINA_TZ)
    assert record.timeline.available_time == datetime(2026, 1, 2, 16, 30, tzinfo=_CHINA_TZ)
    assert record.timeline.revision_time == record.timeline.available_time


def test_announcement_clock_distinguishes_ann_date_from_f_ann_date() -> None:
    def _params(request: ProviderRequest) -> dict[str, str]:
        return {}

    descriptor = TushareDatasetDescriptor(
        dataset="fina_indicator",
        kind="fina_indicator",
        subject_field="ts_code",
        date_field="end_date",
        clock=ClockStrategy.announcement,
        params_builder=_params,
        source_uri_template="tushare://{dataset}/{subject}/{date}",
    )
    response = {
        "code": 0,
        "msg": None,
        "data": {
            "fields": ["ts_code", "end_date", "ann_date", "f_ann_date", "roe"],
            "items": [["000001.SZ", "20251231", "20260228", "20260315", 12.3]],
        },
    }
    provider = _provider(datetime(2026, 4, 1, 0, 0, tzinfo=UTC))
    request = ProviderRequest(
        dataset="fina_indicator", as_of=datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    )

    (record,) = provider._decode(descriptor=descriptor, response=response, request=request)

    assert record.timeline.event_time == datetime(2026, 2, 28, 0, 0, tzinfo=_CHINA_TZ)
    assert record.timeline.available_time == record.timeline.event_time
    assert record.timeline.revision_time == datetime(2026, 3, 15, 0, 0, tzinfo=_CHINA_TZ)
    assert record.timeline.revision_time != record.timeline.available_time


def test_announcement_clock_falls_back_to_ann_date_when_no_revision() -> None:
    def _params(request: ProviderRequest) -> dict[str, str]:
        return {}

    descriptor = TushareDatasetDescriptor(
        dataset="fina_indicator",
        kind="fina_indicator",
        subject_field="ts_code",
        date_field="end_date",
        clock=ClockStrategy.announcement,
        params_builder=_params,
        source_uri_template="tushare://{dataset}/{subject}/{date}",
    )
    response = {
        "code": 0,
        "msg": None,
        "data": {
            "fields": ["ts_code", "end_date", "ann_date", "f_ann_date", "roe"],
            "items": [["000001.SZ", "20251231", "20260228", None, 12.3]],
        },
    }
    provider = _provider(datetime(2026, 4, 1, 0, 0, tzinfo=UTC))
    request = ProviderRequest(
        dataset="fina_indicator", as_of=datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    )

    (record,) = provider._decode(descriptor=descriptor, response=response, request=request)

    assert record.timeline.revision_time == record.timeline.available_time
    assert record.timeline.revision_time == datetime(2026, 2, 28, 0, 0, tzinfo=_CHINA_TZ)


def test_calendar_static_clock_sets_event_time_equal_to_available_time() -> None:
    def _params(request: ProviderRequest) -> dict[str, str]:
        return {}

    descriptor = TushareDatasetDescriptor(
        dataset="trade_cal",
        kind="trade_cal",
        subject_field=None,
        date_field="cal_date",
        clock=ClockStrategy.calendar_static,
        params_builder=_params,
        source_uri_template="tushare://{dataset}/{date}",
    )
    response = {
        "code": 0,
        "msg": None,
        "data": {
            "fields": ["cal_date", "is_open"],
            "items": [["20260724", 1]],
        },
    }
    provider = _provider(datetime(2026, 7, 25, 0, 0, tzinfo=UTC))
    request = ProviderRequest(dataset="trade_cal", as_of=datetime(2026, 7, 25, 0, 0, tzinfo=UTC))

    (record,) = provider._decode(descriptor=descriptor, response=response, request=request)

    assert record.timeline.event_time == record.timeline.available_time
    assert record.timeline.event_time == datetime(2026, 7, 24, 0, 0, tzinfo=_CHINA_TZ)
    assert record.timeline.revision_time == record.timeline.available_time
    assert record.subject == "trade_cal"
