import json
from typing import Any

import pytest

from openalpha_cn.models.deepseek import DEEPSEEK_V4_1_FLASH
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


def test_default_payload_is_byte_identical_to_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2-P7-012 Task 4: the new knobs are opt-in; an unset provider sends what it always did.

    Pinned as bytes (not just dict equality) because the request digest of every stored
    `model-exchange/v1` document is sha256 over this JSON, and a key-order or default-value
    drift would silently re-address every recorded exchange.
    """
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-value")
    transport = FakePostJsonTransport(
        {"choices": [{"message": {"content": '{"direction":"abstain"}'}}]}
    )
    provider = OpenAICompatibleProvider(
        provider_id="deepseek.compatible",
        model="deepseek-chat",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_MODEL_KEY",
        transport=transport,
    )

    provider.generate_json(system="system", user="user", schema={"type": "object"})

    assert transport.request is not None
    payload = transport.request["payload"]
    assert payload == {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "openalpha_structured_payload",
                "strict": True,
                "schema": {"type": "object"},
            },
        },
    }
    assert json.dumps(payload, ensure_ascii=False) == (
        '{"model": "deepseek-chat", "messages": [{"role": "system", "content": "system"}, '
        '{"role": "user", "content": "user"}], "response_format": {"type": "json_schema", '
        '"json_schema": {"name": "openalpha_structured_payload", "strict": true, '
        '"schema": {"type": "object"}}}}'
    )


def test_temperature_and_max_tokens_reach_the_payload_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-value")
    transport = FakePostJsonTransport(
        {"choices": [{"message": {"content": '{"direction":"abstain"}'}}]}
    )
    provider = OpenAICompatibleProvider(
        provider_id="deepseek.compatible",
        model="deepseek-chat",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_MODEL_KEY",
        temperature=0.0,
        max_tokens=512,
        transport=transport,
    )

    provider.generate_json(system="system", user="user", schema={"type": "object"})

    assert transport.request is not None
    # 0.0 must reach the payload: it is a real setting (deterministic sampling), not "unset".
    assert transport.request["payload"]["temperature"] == 0.0
    assert transport.request["payload"]["max_tokens"] == 512


def test_require_usage_refuses_a_response_without_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-value")
    no_usage = OpenAICompatibleProvider(
        provider_id="deepseek.compatible",
        model="deepseek-chat",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_MODEL_KEY",
        require_usage=True,
        transport=FakePostJsonTransport(
            {"id": "chatcmpl-1", "choices": [{"message": {"content": '{"direction":"up"}'}}]}
        ),
    )

    with pytest.raises(ModelResponseError, match="usage"):
        no_usage.generate_json(system="system", user="user", schema={"type": "object"})

    no_id = OpenAICompatibleProvider(
        provider_id="deepseek.compatible",
        model="deepseek-chat",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_MODEL_KEY",
        require_usage=True,
        transport=FakePostJsonTransport(
            {
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "choices": [{"message": {"content": '{"direction":"up"}'}}],
            }
        ),
    )

    with pytest.raises(ModelResponseError, match="id"):
        no_id.generate_json(system="system", user="user", schema={"type": "object"})


# --- V2-P7-012 Task 2.1: the endpoint's extra payload, merged last -------------------------


def test_default_payload_is_byte_identical_without_extra_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new field is empty by default, and an unset provider sends the pinned bytes.

    The request digest of every stored `model-exchange/v1` document is sha256 over this JSON, so
    a default that added one key -- even an empty `extra_payload` wrapper -- would re-address
    every recorded exchange. Pinned as bytes rather than dict equality for that reason.
    """
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-value")
    transport = FakePostJsonTransport(
        {"choices": [{"message": {"content": '{"direction":"abstain"}'}}]}
    )
    provider = OpenAICompatibleProvider(
        provider_id="deepseek.compatible",
        model="deepseek-chat",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_MODEL_KEY",
        transport=transport,
    )

    provider.generate_json(system="system", user="user", schema={"type": "object"})

    assert transport.request is not None
    payload = transport.request["payload"]
    assert json.dumps(payload, ensure_ascii=False) == (
        '{"model": "deepseek-chat", "messages": [{"role": "system", "content": "system"}, '
        '{"role": "user", "content": "user"}], "response_format": {"type": "json_schema", '
        '"json_schema": {"name": "openalpha_structured_payload", "strict": true, '
        '"schema": {"type": "object"}}}}'
    )
    # The vendor endpoint's field exists and defaults to empty: the composition root passes it
    # through, so its empty default is what keeps the exchanged bytes unchanged.
    assert dict(DEEPSEEK_V4_1_FLASH.extra_payload) == {}


def test_extra_payload_thinking_disabled_reaches_the_request_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek's thinking switch is a vendor dial the provider has no keyword for.

    Thinking mode is on by default and ignores `temperature`, so the endpoint must be able to
    send `thinking: {"type": "disabled"}`. The mapping is merged *last* but may not override a
    key the provider owns (fix round 1, F9): `temperature` keeps the provider keyword's value
    and a mapping naming it is refused at construction, so this test passes only `thinking`.
    """
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-value")
    transport = FakePostJsonTransport(
        {"choices": [{"message": {"content": '{"direction":"abstain"}'}}]}
    )
    provider = OpenAICompatibleProvider(
        provider_id="deepseek.compatible",
        model="deepseek-chat",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_MODEL_KEY",
        temperature=0.5,
        extra_payload={"thinking": {"type": "disabled"}},
        transport=transport,
    )

    provider.generate_json(system="system", user="user", schema={"type": "object"})

    assert transport.request is not None
    payload = transport.request["payload"]
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["temperature"] == 0.5
    # The provider-owned keys are untouched and still come first in the serialized payload.
    assert list(payload) == ["model", "messages", "response_format", "temperature", "thinking"]
    assert payload["model"] == "deepseek-chat"
    assert payload["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert payload["response_format"]["type"] == "json_schema"


def test_extra_payload_cannot_override_model_or_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The contract keys and the provider's own request keywords belong to the provider.

    Fix round 1 (F9) widened the reserved set from the three required keys to every key this
    provider writes itself: `temperature`, `max_tokens`, `stream` and `n` as well, because an
    endpoint mapping that set `stream: true` would break the response contract and one that set
    `n > 1` would change the answer's shape without changing the recorded exchange's model.
    Refused at construction, before any request.
    """
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-value")
    for reserved in ("model", "messages", "response_format"):
        transport = FakePostJsonTransport({})
        with pytest.raises(ModelConfigurationError, match=reserved):
            OpenAICompatibleProvider(
                provider_id="deepseek.compatible",
                model="deepseek-chat",
                base_url="https://example.invalid/v1",
                api_key_env="TEST_MODEL_KEY",
                extra_payload={reserved: "caller-supplied"},
                transport=transport,
            )
        assert transport.request is None, "the refusal happens before any request"


def test_extra_payload_cannot_override_the_optional_provider_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F9: the provider's own optional keywords are reserved too, not silently overridden."""
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-value")
    for reserved in ("temperature", "max_tokens", "stream", "n"):
        with pytest.raises(ModelConfigurationError, match=reserved):
            OpenAICompatibleProvider(
                provider_id="deepseek.compatible",
                model="deepseek-chat",
                base_url="https://example.invalid/v1",
                api_key_env="TEST_MODEL_KEY",
                extra_payload={reserved: 1},
                transport=FakePostJsonTransport({}),
            )


def test_extra_payload_is_copied_and_frozen_so_a_later_mutation_cannot_change_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F9: the endpoint's mapping is copied, so a caller mutating it later changes no request.

    A shallow copy would still share the nested `thinking` object: the test mutates the caller's
    nested dict *after* construction and expects the request to carry the value it was built
    with, and the provider's own mapping to be immutable.
    """
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-value")
    source: dict[str, object] = {"thinking": {"type": "disabled"}}
    transport = FakePostJsonTransport(
        {"choices": [{"message": {"content": '{"direction":"abstain"}'}}]}
    )
    provider = OpenAICompatibleProvider(
        provider_id="deepseek.compatible",
        model="deepseek-chat",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_MODEL_KEY",
        extra_payload=source,
        transport=transport,
    )
    source["thinking"]["type"] = "enabled"  # type: ignore[index]

    provider.generate_json(system="system", user="user", schema={"type": "object"})

    assert transport.request is not None
    assert transport.request["payload"]["thinking"] == {"type": "disabled"}
    assert provider.extra_payload is not source
    with pytest.raises(TypeError):
        provider.extra_payload["new"] = 1  # type: ignore[index]


def test_the_endpoint_value_owns_a_deep_copy_of_its_extra_payload() -> None:
    """F9: a caller mutating the mapping it built an endpoint with cannot change the endpoint.

    The endpoint keeps a deep copy (a plain `dict`, so `dataclasses.asdict` keeps working) and
    the provider freezes its own second copy, so neither level shares a mutable nested object.
    """
    source: dict[str, object] = {"thinking": {"type": "disabled"}}
    from openalpha_cn.models.deepseek import VendorEndpoint

    endpoint = VendorEndpoint(
        name="TEST_ENDPOINT",
        provider_id="test",
        model="test-model",
        base_url="https://example.invalid/v1",
        api_key_env="TEST_MODEL_KEY",
        structured_output_mode="json_object",
        temperature=0.0,
        max_output_tokens=16,
        input_price_per_million=__import__("decimal").Decimal("0"),
        output_price_per_million=__import__("decimal").Decimal("0"),
        documentation_url="https://example.invalid/",
        verified_on=None,
        extra_payload=source,
    )
    source["thinking"]["type"] = "enabled"  # type: ignore[index]

    assert endpoint.extra_payload["thinking"] == {"type": "disabled"}
    assert isinstance(endpoint.extra_payload, dict)


def test_blank_content_is_an_invalid_answer_not_an_empty_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek's json mode can return only spaces even with `finish_reason=stop`.

    A blank answer is an invalid answer to retry -- never an empty object the agent would accept
    and score. The refusal happens before any JSON decode, so its reason names what happened
    instead of the parser's column number.
    """
    monkeypatch.setenv("TEST_MODEL_KEY", "secret-value")
    for blank in ("", " ", "   \n\t  "):
        transport = FakePostJsonTransport({"choices": [{"message": {"content": blank}}]})
        provider = OpenAICompatibleProvider(
            provider_id="deepseek.compatible",
            model="deepseek-chat",
            base_url="https://example.invalid/v1",
            api_key_env="TEST_MODEL_KEY",
            transport=transport,
        )

        with pytest.raises(ModelResponseError, match="blank"):
            provider.generate_json(system="system", user="user", schema={"type": "object"})
