from datetime import UTC, datetime

import pytest

from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import TushareProvider


def test_tushare_byot_maps_daily_payload_without_exposing_token(fake_tushare_transport) -> None:
    transport = fake_tushare_transport(
        {
            "code": 0,
            "msg": None,
            "data": {
                "fields": ["ts_code", "trade_date", "close", "pct_chg"],
                "items": [["000001.SZ", "20260724", 10.5, 9.99]],
            },
        }
    )
    provider = TushareProvider(
        token="secret-token",
        transport=transport,
        clock=lambda: datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
    )

    batch = provider.fetch(
        ProviderRequest(
            dataset="daily",
            as_of=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
            subjects=("000001.SZ",),
        )
    )

    assert batch.status == "success"
    assert batch.records[0].subject == "000001.SZ"
    assert batch.records[0].payload == {
        "ts_code": "000001.SZ",
        "trade_date": "20260724",
        "close": 10.5,
        "pct_chg": 9.99,
    }
    assert transport.payload == {
        "api_name": "daily",
        "token": "secret-token",
        "params": {"trade_date": "20260724", "ts_code": "000001.SZ"},
        "fields": "",
    }
    assert "secret-token" not in repr(batch)


def test_tushare_missing_token_is_an_explicit_configuration_failure(fake_tushare_transport) -> None:
    provider = TushareProvider(token="", transport=fake_tushare_transport({}))

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch(
            ProviderRequest(
                dataset="daily",
                as_of=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
            )
        )

    assert captured.value.category == "configuration"
    assert captured.value.retryable is False


def test_tushare_metadata_declares_supported_datasets(fake_tushare_transport) -> None:
    provider = TushareProvider(token="secret-token", transport=fake_tushare_transport({}))

    assert provider.metadata.supported_datasets == ("daily",)


def test_tushare_upstream_error_never_becomes_empty_success(fake_tushare_transport) -> None:
    provider = TushareProvider(
        token="secret-token",
        transport=fake_tushare_transport({"code": -2001, "msg": "permission denied", "data": None}),
    )

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch(
            ProviderRequest(
                dataset="daily",
                as_of=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
            )
        )

    assert captured.value.category == "authentication"
    assert captured.value.provider_id == "tushare.pro"
