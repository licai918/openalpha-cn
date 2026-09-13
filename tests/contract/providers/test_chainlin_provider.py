from datetime import datetime, timedelta
from typing import Any, Final

import pytest

from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.chainlin import (
    ChainLinDataProvider,
    ChainLinHttpError,
)


class FakeChainLinTransport:
    """Doubles ChainLin's `get_json` transport Protocol.

    Named distinctly (not `FakeTransport`) from the `post_json`/`post` doubles elsewhere
    in this suite -- see `tests/contract/providers/conftest.py`'s `FakeTushareTransport`
    docstring for the full inventory. Single-file use: no other test needs a `get_json`
    double, so this stays local rather than moving to the directory conftest.
    """

    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.request: dict[str, Any] | None = None

    def get_json(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


EVENT_BEFORE_NOW: Final[timedelta] = timedelta(hours=3)
"""How long before `frozen_now` the fixture's record happened."""

AVAILABLE_BEFORE_NOW: Final[timedelta] = timedelta(hours=2)
"""How long before `frozen_now` it became available."""

REVISED_BEFORE_NOW: Final[timedelta] = timedelta(hours=1)
"""How long before `frozen_now` it was revised, which is after it became available."""


@pytest.fixture
def payload(frozen_now: datetime):
    """A one-record chainlin-data/v1 response whose three clocks are three different instants.

    All three were `frozen_now`, so the contract test could not tell a client that read
    `revision_time` into `available_time`, or `event_time` into it, from one that read each
    clock into its own field. An hour apart, and each before the request's `as_of`, they can.
    """

    def _make() -> dict[str, Any]:
        return {
            "schema_version": "chainlin-data/v1",
            "records": [
                {
                    "subject": "000001.SZ",
                    "kind": "limit_up",
                    "event_time": (frozen_now - EVENT_BEFORE_NOW).isoformat(),
                    "available_time": (frozen_now - AVAILABLE_BEFORE_NOW).isoformat(),
                    "revision_time": (frozen_now - REVISED_BEFORE_NOW).isoformat(),
                    "source_uri": "chainlin://limit-up/000001.SZ",
                    "summary": "涨停一板",
                    "payload": {"close": 10.5, "board_count": 1},
                }
            ],
        }

    return _make


def test_chainlin_contract_preserves_pit_license_and_bearer_auth(
    monkeypatch: pytest.MonkeyPatch,
    payload,
    frozen_now: datetime,
) -> None:
    monkeypatch.setenv("CHAINLIN_API_KEY", "secret")
    transport = FakeChainLinTransport(payload())
    provider = ChainLinDataProvider(
        base_url="https://data.chainlin.example/v1",
        api_key_env="CHAINLIN_API_KEY",
        source_license="user-held ChainLin subscription",
        transport=transport,
        clock=lambda: frozen_now,
    )

    batch = provider.fetch(
        ProviderRequest(dataset="limit_up", as_of=frozen_now, subjects=("000001.SZ",))
    )

    assert batch.status == "success"
    assert batch.records[0].timeline.available_time == frozen_now - AVAILABLE_BEFORE_NOW
    assert batch.records[0].timeline.revision_time == frozen_now - REVISED_BEFORE_NOW
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
    payload,
    frozen_now: datetime,
) -> None:
    monkeypatch.delenv("CHAINLIN_API_KEY", raising=False)
    missing = ChainLinDataProvider(
        base_url="https://data.chainlin.example/v1",
        api_key_env="CHAINLIN_API_KEY",
        source_license="restricted",
        transport=FakeChainLinTransport(payload()),
    )
    with pytest.raises(ProviderFailure) as captured:
        missing.fetch(ProviderRequest(dataset="daily", as_of=frozen_now))
    assert captured.value.category == "authentication"

    monkeypatch.setenv("CHAINLIN_API_KEY", "secret")
    limited = ChainLinDataProvider(
        base_url="https://data.chainlin.example/v1",
        api_key_env="CHAINLIN_API_KEY",
        source_license="restricted",
        transport=FakeChainLinTransport(payload()),
        max_calls_per_minute=1,
        monotonic=lambda: 1.0,
    )
    limited.fetch(ProviderRequest(dataset="daily", as_of=frozen_now))
    with pytest.raises(ProviderFailure) as captured:
        limited.fetch(ProviderRequest(dataset="daily", as_of=frozen_now))
    assert captured.value.category == "rate_limit"

    upstream = ChainLinDataProvider(
        base_url="https://data.chainlin.example/v1",
        api_key_env="CHAINLIN_API_KEY",
        source_license="restricted",
        transport=FakeChainLinTransport(ChainLinHttpError(503, "unavailable")),
    )
    with pytest.raises(ProviderFailure) as captured:
        upstream.fetch(ProviderRequest(dataset="daily", as_of=frozen_now))
    assert captured.value.category == "upstream"
    assert captured.value.retryable is True
