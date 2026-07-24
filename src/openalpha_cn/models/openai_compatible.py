"""Secure BYOK adapter for OpenAI-compatible chat-completions endpoints."""

import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from openalpha_cn.models.base import ModelCapabilities, ModelMetadata
from openalpha_cn.models.governance import (
    ModelRetryPolicy,
    ModelUsageRecord,
    SQLiteModelUsageStore,
)


class ModelConfigurationError(ValueError):
    """Raised before network access when provider configuration is incomplete."""


class ModelTransportError(RuntimeError):
    """Raised when an endpoint cannot return a successful JSON response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.request_id = request_id


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
        except HTTPError as error:
            request_id = error.headers.get("x-request-id") if error.headers else None
            raise ModelTransportError(
                f"model endpoint returned HTTP {error.code}",
                status_code=error.code,
                retryable=error.code in {408, 429, 500, 502, 503, 504},
                request_id=request_id,
            ) from error
        except (URLError, TimeoutError) as error:
            raise ModelTransportError(
                f"model endpoint request failed: {type(error).__name__}",
                retryable=True,
            ) from error
        except json.JSONDecodeError as error:
            raise ModelTransportError("model endpoint returned invalid JSON") from error
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
        retry_policy: ModelRetryPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        usage_store: SQLiteModelUsageStore | None = None,
        input_price_per_million: Decimal = Decimal("0"),
        output_price_per_million: Decimal = Decimal("0"),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        capabilities: ModelCapabilities | None = None,
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
        self.retry_policy = retry_policy or ModelRetryPolicy()
        self.sleeper = sleeper
        self.usage_store = usage_store
        self.input_price_per_million = input_price_per_million
        self.output_price_per_million = output_price_per_million
        self.clock = clock
        self._metadata = ModelMetadata(
            provider_id=provider_id,
            model=model,
            credential_env_vars=() if api_key_env is None else (api_key_env,),
            structured_output=True,
            capabilities=capabilities or ModelCapabilities(usage_reporting=usage_store is not None),
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
        raw: dict[str, Any] | None = None
        attempts = 0
        while raw is None:
            attempts += 1
            try:
                raw = self.transport.post_json(
                    url=f"{self.base_url}/chat/completions",
                    headers=headers,
                    payload=payload,
                    timeout_seconds=self.timeout_seconds,
                )
            except ModelTransportError as error:
                if not error.retryable or attempts >= self.retry_policy.max_attempts:
                    raise
                self.sleeper(self.retry_policy.delay(attempts))
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
        self._record_usage(raw=raw, attempts=attempts)
        return decoded

    def _record_usage(self, *, raw: dict[str, Any], attempts: int) -> None:
        if self.usage_store is None:
            return
        usage = raw.get("usage")
        request_id = raw.get("id")
        if not isinstance(usage, dict) or not isinstance(request_id, str):
            return
        input_tokens = self._token_count(usage.get("prompt_tokens", usage.get("input_tokens", 0)))
        output_tokens = self._token_count(
            usage.get("completion_tokens", usage.get("output_tokens", 0))
        )
        total_tokens = self._token_count(usage.get("total_tokens", input_tokens + output_tokens))
        million = Decimal(1_000_000)
        cost = (
            Decimal(input_tokens) * self.input_price_per_million
            + Decimal(output_tokens) * self.output_price_per_million
        ) / million
        # OpenAI reports input/output token usage on successful responses:
        # https://platform.openai.com/docs/api-reference/usage
        self.usage_store.append(
            ModelUsageRecord(
                request_id=request_id,
                provider_id=self.metadata.provider_id,
                model=self.metadata.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                attempts=attempts,
                estimated_cost=cost.quantize(Decimal("0.000001")),
                occurred_at=self.clock(),
            )
        )

    @staticmethod
    def _token_count(value: Any) -> int:
        return value if isinstance(value, int) and value >= 0 else 0
