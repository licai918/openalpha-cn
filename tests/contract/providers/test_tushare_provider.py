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

    # Grows by exactly one entry per row added to TUSHARE_DATASETS; `trade_cal` is
    # V2-P1-004's, `stock_basic` and `namechange` are V2-P1-005's, `adj_factor` is
    # V2-P1-006's, `daily_basic` is V2-P1-007's, `suspend_d` and `stk_limit` are V2-P1-008's.
    # Still spelled out in full rather than derived
    # from the table, so that adding a dataset has to be an intentional edit here too.
    #
    # "Supported" is not "served on both planes": `stock_basic` and `namechange` declare
    # `serves_evidence_plane=False`, so `fetch()` refuses them by name while `fetch_panel()`
    # serves them. They belong in this tuple because the provider does support them -- see
    # `tests/contract/providers/test_tushare_registry_datasets.py` for both halves.
    assert provider.metadata.supported_datasets == (
        "daily",
        "trade_cal",
        "stock_basic",
        "namechange",
        "adj_factor",
        "daily_basic",
        "suspend_d",
        "stk_limit",
    )


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
