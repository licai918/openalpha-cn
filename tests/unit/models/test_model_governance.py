from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.models.base import ModelCapabilities, ModelMetadata
from openalpha_cn.models.governance import (
    ModelRegistry,
    ModelRetryPolicy,
    ModelUsageRecord,
)
from openalpha_cn.models.openai_compatible import (
    ModelTransportError,
    OpenAICompatibleProvider,
)
from openalpha_cn.storage.models import SQLiteModelUsageStore


class SequenceTransport:
    def __init__(self, outcomes: list[dict[str, Any] | Exception]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def post_json(self, **_kwargs: object) -> dict[str, Any]:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_registry_exposes_non_secret_provider_capabilities() -> None:
    metadata = ModelMetadata(
        provider_id="deepseek",
        model="deepseek-chat",
        credential_env_vars=("DEEPSEEK_API_KEY",),
        structured_output=True,
        capabilities=ModelCapabilities(
            context_window=64_000,
            max_output_tokens=8_192,
            reasoning=True,
            usage_reporting=True,
        ),
    )
    registry = ModelRegistry((metadata,))

    assert registry.resolve("deepseek", "deepseek-chat") == metadata
    assert registry.list()[0].capabilities.reasoning is True
    with pytest.raises(ValueError, match="already registered"):
        registry.register(metadata)


def test_provider_classifies_retry_and_persists_usage_cost(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MODEL_KEY", "secret")
    transport = SequenceTransport(
        [
            ModelTransportError(
                "rate limited",
                status_code=429,
                retryable=True,
                request_id="req-1",
            ),
            {
                "id": "chatcmpl-1",
                "choices": [{"message": {"content": '{"direction":"bullish"}'}}],
                "usage": {
                    "prompt_tokens": 1_000,
                    "completion_tokens": 500,
                    "total_tokens": 1_500,
                },
            },
        ]
    )
    store = SQLiteModelUsageStore(tmp_path / "state.sqlite3")
    sleeps: list[float] = []
    provider = OpenAICompatibleProvider(
        provider_id="test",
        model="model",
        base_url="https://example.invalid/v1",
        api_key_env="MODEL_KEY",
        transport=transport,
        retry_policy=ModelRetryPolicy(max_attempts=2, base_delay_seconds=0.25),
        sleeper=sleeps.append,
        usage_store=store,
        input_price_per_million=Decimal("2"),
        output_price_per_million=Decimal("8"),
        clock=lambda: datetime(2026, 7, 24, 10, 30, tzinfo=UTC),
    )

    result = provider.generate_json(
        system="Return JSON.",
        user="Analyze.",
        schema={"type": "object"},
    )

    assert result == {"direction": "bullish"}
    assert transport.calls == 2
    assert sleeps == [0.25]
    records = store.list(provider_id="test")
    assert records == (
        ModelUsageRecord(
            request_id="chatcmpl-1",
            provider_id="test",
            model="model",
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            attempts=2,
            estimated_cost=Decimal("0.006000"),
            occurred_at=datetime(2026, 7, 24, 10, 30, tzinfo=UTC),
        ),
    )


def test_provider_does_not_retry_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_KEY", "secret")
    transport = SequenceTransport(
        [
            ModelTransportError(
                "unauthorized",
                status_code=401,
                retryable=False,
                request_id="req-auth",
            )
        ]
    )
    provider = OpenAICompatibleProvider(
        provider_id="test",
        model="model",
        base_url="https://example.invalid/v1",
        api_key_env="MODEL_KEY",
        transport=transport,
        retry_policy=ModelRetryPolicy(max_attempts=3),
        sleeper=lambda _delay: None,
    )

    with pytest.raises(ModelTransportError, match="unauthorized"):
        provider.generate_json(system="s", user="u", schema={"type": "object"})
    assert transport.calls == 1
