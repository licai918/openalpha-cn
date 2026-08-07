from typing import Any

import pytest

from openalpha_cn.models.openai_compatible import (
    ModelConfigurationError,
    ModelResponseError,
    OpenAICompatibleProvider,
)


class FakePostJsonTransport:
    """Doubles `OpenAICompatibleProvider`'s `post_json` transport Protocol.

    Renamed from the generic `FakeTransport` (was one of three same-named-but-
    incompatible classes in this suite) to name the Protocol it doubles. Single-file,
    single-response use: `tests/unit/models/test_model_governance.py`'s `SequenceTransport`
    doubles the same `post_json` Protocol but with a queued, multi-outcome sequence for
    retry testing -- a genuinely different need, deliberately not merged with this one.
    """

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.request: dict[str, Any] | None = None

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.request = {
            "url": url,
            "headers": headers,
            "payload": payload,
            "timeout_seconds": timeout_seconds,
        }
        return self.response


def test_openai_compatible_provider_sends_schema_and_returns_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-value")
    transport = FakePostJsonTransport(
        {
            "choices": [
                {"message": {"content": '{"direction":"abstain","reason":"insufficient evidence"}'}}
            ]
        }
    )
    provider = OpenAICompatibleProvider(
        provider_id="deepseek.compatible",
        model="deepseek-chat",
        base_url="https://example.invalid/v1/",
        api_key_env="TEST_MODEL_KEY",
        transport=transport,
    )

    result = provider.generate_json(
        system="Return JSON.",
        user="Analyze.",
        schema={"type": "object", "properties": {"direction": {"type": "string"}}},
    )

    assert result == {"direction": "abstain", "reason": "insufficient evidence"}
    assert transport.request is not None
    assert transport.request["url"] == "https://example.invalid/v1/chat/completions"
    assert transport.request["headers"]["Authorization"] == "Bearer secret-value"
    assert transport.request["payload"]["response_format"]["type"] == "json_schema"
    assert transport.request["payload"]["response_format"]["json_schema"]["strict"] is True
    assert "secret-value" not in provider.metadata.model_dump_json()


def test_openai_compatible_provider_fails_before_network_without_required_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISSING_MODEL_KEY", raising=False)
    provider = OpenAICompatibleProvider(
        provider_id="openai.compatible",
        model="model-id",
        base_url="https://example.invalid/v1",
        api_key_env="MISSING_MODEL_KEY",
        transport=FakePostJsonTransport({}),
    )

    with pytest.raises(ModelConfigurationError, match="MISSING_MODEL_KEY"):
        provider.generate_json(system="system", user="user", schema={"type": "object"})


def test_openai_compatible_provider_rejects_malformed_or_non_object_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-value")
    malformed = OpenAICompatibleProvider(
        provider_id="openai.compatible",
        model="model-id",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_MODEL_KEY",
        transport=FakePostJsonTransport({"choices": []}),
    )

    with pytest.raises(ModelResponseError, match="choices"):
        malformed.generate_json(system="system", user="user", schema={"type": "object"})
