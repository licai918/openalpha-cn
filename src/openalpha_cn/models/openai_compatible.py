"""Secure BYOK adapter for OpenAI-compatible chat-completions endpoints."""

import json
import os
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from openalpha_cn.models.base import ModelMetadata


class ModelConfigurationError(ValueError):
    """Raised before network access when provider configuration is incomplete."""


class ModelTransportError(RuntimeError):
    """Raised when an endpoint cannot return a successful JSON response."""


class ModelResponseError(ValueError):
    """Raised when a successful endpoint response violates the provider contract."""


class JsonTransport(Protocol):
    """Injectable JSON transport used by the provider and deterministic tests."""

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """POST one JSON object and return one decoded JSON object."""


class UrllibJsonTransport:
    """Standard-library HTTPS transport without a mandatory SDK dependency."""

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url=url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ModelTransportError(
                f"model endpoint request failed: {type(error).__name__}"
            ) from error
        if not isinstance(decoded, dict):
            raise ModelTransportError("model endpoint returned a non-object JSON response")
        return decoded


class OpenAICompatibleProvider:
    """Call a user-selected compatible endpoint and require structured JSON."""

    def __init__(
        self,
        *,
        provider_id: str,
        model: str,
        base_url: str,
        api_key_env: str | None,
        structured_output_mode: Literal["json_schema", "json_object"] = "json_schema",
        timeout_seconds: float = 60.0,
        transport: JsonTransport | None = None,
    ) -> None:
        if not base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ModelConfigurationError(
                "base_url must use HTTPS or an explicit localhost HTTP endpoint"
            )
        if timeout_seconds <= 0:
            raise ModelConfigurationError("timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.structured_output_mode = structured_output_mode
        self.timeout_seconds = timeout_seconds
        self.transport = transport or UrllibJsonTransport()
        self._metadata = ModelMetadata(
            provider_id=provider_id,
            model=model,
            credential_env_vars=() if api_key_env is None else (api_key_env,),
            structured_output=True,
        )

    @property
    def metadata(self) -> ModelMetadata:
        """Return capabilities and credential names, never credential values."""
        return self._metadata

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a JSON object through the documented structured-output shape."""
        headers = {"Content-Type": "application/json"}
        if self.api_key_env is not None:
            api_key = os.getenv(self.api_key_env, "").strip()
            if not api_key:
                raise ModelConfigurationError(
                    f"required model credential is missing: {self.api_key_env}"
                )
            headers["Authorization"] = f"Bearer {api_key}"

        response_format: dict[str, Any]
        if self.structured_output_mode == "json_schema":
            # OpenAI structured output contract:
            # https://developers.openai.com/api/docs/guides/structured-outputs
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "openalpha_structured_payload",
                    "strict": True,
                    "schema": schema,
                },
            }
        else:
            response_format = {"type": "json_object"}
        payload = {
            "model": self.metadata.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": response_format,
        }
        raw = self.transport.post_json(
            url=f"{self.base_url}/chat/completions",
            headers=headers,
            payload=payload,
            timeout_seconds=self.timeout_seconds,
        )
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelResponseError("model response choices are missing")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ModelResponseError("model response content is missing")
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError as error:
            raise ModelResponseError("model response content is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise ModelResponseError("model response content must be a JSON object")
        return decoded
