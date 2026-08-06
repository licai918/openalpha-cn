from datetime import UTC, datetime
from typing import Any

import pytest

from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.chainlin import (
    ChainLinDataProvider,
    ChainLinHttpError,
)

NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)


class FakeTransport:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.request: dict[str, Any] | None = None

    def get_json(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def payload() -> dict[str, Any]:
    return {
        "schema_version": "chainlin-data/v1",
        "records": [
            {
                "subject": "000001.SZ",
                "kind": "limit_up",
                "event_time": NOW.isoformat(),
                "available_time": NOW.isoformat(),
                "revision_time": NOW.isoformat(),
                "source_uri": "chainlin://limit-up/000001.SZ",
                "summary": "涨停一板",
                "payload": {"close": 10.5, "board_count": 1},
            }
        ],
    }


def test_chainlin_contract_preserves_pit_license_and_bearer_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAINLIN_API_KEY", "secret")
    transport = FakeTransport(payload())
    provider = ChainLinDataProvider(
        base_url="https://data.chainlin.example/v1",
        api_key_env="CHAINLIN_API_KEY",
        source_license="user-held ChainLin subscription",
        transport=transport,
        clock=lambda: NOW,
    )

    batch = provider.fetch(ProviderRequest(dataset="limit_up", as_of=NOW, subjects=("000001.SZ",)))

    assert batch.status == "success"
    assert batch.records[0].timeline.available_time == NOW
    assert batch.records[0].timeline.revision_time == NOW
    assert provider.metadata.redistribution == "restricted"
    assert transport.request is not None
    assert transport.request["headers"]["Authorization"] == "Bearer secret"
    assert "secret" not in batch.model_dump_json()


def test_chainlin_metadata_declares_supported_datasets() -> None:
    provider = ChainLinDataProvider(
        base_url="https://data.chainlin.example/v1",
        api_key_env="CHAINLIN_API_KEY",
        source_license="user-held ChainLin subscription",
    )

    assert provider.metadata.supported_datasets == (
        "broken_board",
        "capital",
        "consecutive_board",
        "daily",
        "disclosure",
        "limit_up",
        "quote",
        "theme",
    )


def test_chainlin_provider_is_importable_from_providers_package() -> None:
    from openalpha_cn.providers import ChainLinDataProvider as ExportedProvider

    assert ExportedProvider is ChainLinDataProvider
    from openalpha_cn.providers import __all__ as providers_all

    assert "ChainLinDataProvider" in providers_all


def test_chainlin_auth_rate_limit_and_upstream_failures_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHAINLIN_API_KEY", raising=False)
    missing = ChainLinDataProvider(
        base_url="https://data.chainlin.example/v1",
        api_key_env="CHAINLIN_API_KEY",
        source_license="restricted",
        transport=FakeTransport(payload()),
    )
    with pytest.raises(ProviderFailure) as captured:
        missing.fetch(ProviderRequest(dataset="daily", as_of=NOW))
    assert captured.value.category == "authentication"

    monkeypatch.setenv("CHAINLIN_API_KEY", "secret")
    limited = ChainLinDataProvider(
        base_url="https://data.chainlin.example/v1",
        api_key_env="CHAINLIN_API_KEY",
        source_license="restricted",
        transport=FakeTransport(payload()),
        max_calls_per_minute=1,
        monotonic=lambda: 1.0,
    )
    limited.fetch(ProviderRequest(dataset="daily", as_of=NOW))
    with pytest.raises(ProviderFailure) as captured:
        limited.fetch(ProviderRequest(dataset="daily", as_of=NOW))
    assert captured.value.category == "rate_limit"

    upstream = ChainLinDataProvider(
        base_url="https://data.chainlin.example/v1",
        api_key_env="CHAINLIN_API_KEY",
        source_license="restricted",
        transport=FakeTransport(ChainLinHttpError(503, "unavailable")),
    )
    with pytest.raises(ProviderFailure) as captured:
        upstream.fetch(ProviderRequest(dataset="daily", as_of=NOW))
    assert captured.value.category == "upstream"
    assert captured.value.retryable is True
