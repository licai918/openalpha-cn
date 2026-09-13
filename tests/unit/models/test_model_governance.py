import io
from datetime import UTC, datetime
from decimal import Decimal
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

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
    UrllibJsonTransport,
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


def test_provider_gives_up_after_max_attempts_with_capped_exponential_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OA-MODEL-003's "bounded exponential delay" and "no unbounded retry", driven to the bound.

    Every attempt fails with a retryable error, so only the attempt limit can end the loop:
    exactly `max_attempts` calls, then the last error propagates. Between attempts the delay
    doubles from `base_delay_seconds` and is capped at `max_delay_seconds` -- 1.0, then 2.0
    capped to 1.5, then 4.0 capped to 1.5. The transport holds more failures than the limit,
    so a loop that ignored the limit would run the list dry and raise `IndexError` instead of
    the transport error this asserts.

    "The last error" is asserted by `request_id`, not by message: all ten queued errors say
    `unavailable`, so the `match=` alone passes whichever of them propagates -- measured, a
    provider changed to re-raise the first attempt's error left this test green until the
    `request_id` assertion was added. The fourth attempt consumes `req-3`, so that is the one
    that must surface.
    """
    monkeypatch.setenv("MODEL_KEY", "secret")
    transport = SequenceTransport(
        [
            ModelTransportError(
                "unavailable",
                status_code=503,
                retryable=True,
                request_id=f"req-{attempt}",
            )
            for attempt in range(10)
        ]
    )
    sleeps: list[float] = []
    provider = OpenAICompatibleProvider(
        provider_id="test",
        model="model",
        base_url="https://example.invalid/v1",
        api_key_env="MODEL_KEY",
        transport=transport,
        retry_policy=ModelRetryPolicy(
            max_attempts=4, base_delay_seconds=1.0, max_delay_seconds=1.5
        ),
        sleeper=sleeps.append,
    )

    with pytest.raises(ModelTransportError, match="unavailable") as caught:
        provider.generate_json(system="s", user="u", schema={"type": "object"})

    assert caught.value.request_id == "req-3", "the last attempt's error must propagate"
    assert transport.calls == 4
    assert sleeps == [1.0, 1.5, 1.5]


@pytest.mark.parametrize(
    ("status", "retryable"),
    [
        (408, True),
        (429, True),
        (500, True),
        (502, True),
        (503, True),
        (504, True),
        (400, False),
        (401, False),
        (403, False),
        (404, False),
        (422, False),
        (501, False),
    ],
)
def test_the_urllib_transport_classifies_each_http_status_as_retryable_or_not(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    retryable: bool,
) -> None:
    """The HTTP-status half of OA-MODEL-003, which the provider tests above cannot reach.

    Those tests hand the provider a `ModelTransportError` whose `retryable` flag is already set.
    Which statuses are transient is decided one layer down, in `UrllibJsonTransport.post_json`:
    408, 429, 500, 502, 503 and 504 are retryable and every other status is not. `urlopen` is
    replaced with a function that raises the HTTP error directly, so no socket is ever opened.
    """
    headers = Message()
    headers["x-request-id"] = "req-classify"

    def refuse(request: object, timeout: float) -> object:
        raise HTTPError(
            "https://example.invalid/v1/chat/completions",
            status,
            "refused",
            headers,
            io.BytesIO(b""),
        )

    monkeypatch.setattr("openalpha_cn.models.openai_compatible.urlopen", refuse)

    with pytest.raises(ModelTransportError) as caught:
        UrllibJsonTransport().post_json(
            url="https://example.invalid/v1/chat/completions",
            headers={},
            payload={},
            timeout_seconds=1.0,
        )

    assert caught.value.status_code == status
    assert caught.value.retryable is retryable
    assert caught.value.request_id == "req-classify"


def test_the_urllib_transport_treats_a_network_failure_as_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request that never got an HTTP status -- refused, unreachable, timed out -- is retried."""

    def refuse(request: object, timeout: float) -> object:
        raise URLError("connection refused")

    monkeypatch.setattr("openalpha_cn.models.openai_compatible.urlopen", refuse)

    with pytest.raises(ModelTransportError) as caught:
        UrllibJsonTransport().post_json(
            url="https://example.invalid/v1/chat/completions",
            headers={},
            payload={},
            timeout_seconds=1.0,
        )

    assert caught.value.retryable is True
    assert caught.value.status_code is None
