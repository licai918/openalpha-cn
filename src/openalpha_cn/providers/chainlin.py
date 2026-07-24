"""Contract-first BYOK adapter for the ChainLin A-share data API."""

import json
import os
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from openalpha_cn.domain.time import Timeline, ensure_aware
from openalpha_cn.providers.base import (
    DataProvider,
    ProviderBatch,
    ProviderFailure,
    ProviderFailureCategory,
    ProviderMetadata,
    ProviderRecord,
    ProviderRequest,
    utc_now,
)

_DATASETS = {
    "daily",
    "quote",
    "limit_up",
    "broken_board",
    "consecutive_board",
    "theme",
    "capital",
    "disclosure",
}


class ChainLinHttpError(RuntimeError):
    """HTTP status returned by the ChainLin transport."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class ChainLinTransport(Protocol):
    def get_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        params: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]: ...


class UrllibChainLinTransport:
    """Minimal HTTPS transport for the documented JSON contract."""

    def get_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        params: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        request = Request(
            url=f"{url}?{urlencode(params)}",
            headers=headers,
            method="GET",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise ChainLinHttpError(error.code, f"HTTP {error.code}") from error
        except (URLError, TimeoutError) as error:
            raise ChainLinHttpError(503, type(error).__name__) from error
        except json.JSONDecodeError as error:
            raise ValueError("ChainLin response is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise ValueError("ChainLin response must be a JSON object")
        return decoded


class ChainLinDataProvider(DataProvider):
    """Fetch licensed ChainLin records while preserving all PIT clocks."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key_env: str,
        source_license: str,
        transport: ChainLinTransport | None = None,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        max_calls_per_minute: int = 60,
        timeout_seconds: float = 30,
    ) -> None:
        if not base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("ChainLin base_url must use HTTPS or explicit localhost HTTP")
        if max_calls_per_minute < 1:
            raise ValueError("max_calls_per_minute must be positive")
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.transport = transport or UrllibChainLinTransport()
        self.clock = clock
        self.monotonic = monotonic
        self.max_calls_per_minute = max_calls_per_minute
        self.timeout_seconds = timeout_seconds
        self._calls: deque[float] = deque()
        self._metadata = ProviderMetadata(
            provider_id="chainlin.api",
            display_name="ChainLin A-share Data API",
            source_license=source_license,
            redistribution="restricted",
            credential_env_vars=(api_key_env,),
            caching_policy="provider-defined",
            rate_limit=f"{max_calls_per_minute} calls/minute client-side ceiling",
            freshness="Declared by each record's available_time and revision_time.",
            failure_semantics=(
                "Authentication, rate-limit, invalid-contract, and upstream failures "
                "raise ProviderFailure; they never become empty success."
            ),
        )

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    def fetch(self, request: ProviderRequest) -> ProviderBatch:
        if request.dataset not in _DATASETS:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message=f"unsupported ChainLin dataset: {request.dataset}",
                retryable=False,
            )
        token = os.getenv(self.api_key_env, "").strip()
        if not token:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="authentication",
                message=f"required ChainLin credential is missing: {self.api_key_env}",
                retryable=False,
            )
        self._reserve_call()
        try:
            raw = self.transport.get_json(
                url=f"{self.base_url}/datasets/{request.dataset}",
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                params={
                    "as_of": request.as_of.isoformat(),
                    "subjects": ",".join(request.subjects),
                },
                timeout_seconds=self.timeout_seconds,
            )
        except ChainLinHttpError as error:
            category: ProviderFailureCategory = (
                "authentication"
                if error.status_code in {401, 403}
                else "rate_limit"
                if error.status_code == 429
                else "upstream"
            )
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category=category,
                message=f"ChainLin API returned HTTP {error.status_code}",
                retryable=error.status_code in {408, 429, 500, 502, 503, 504},
            ) from error
        try:
            return self._parse(request=request, raw=raw)
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="invalid_response",
                message=f"ChainLin response violated chainlin-data/v1: {type(error).__name__}",
                retryable=False,
            ) from error

    def _reserve_call(self) -> None:
        now = self.monotonic()
        while self._calls and now - self._calls[0] >= 60:
            self._calls.popleft()
        if len(self._calls) >= self.max_calls_per_minute:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="rate_limit",
                message="ChainLin client-side rate limit exceeded",
                retryable=True,
            )
        self._calls.append(now)

    def _parse(self, *, request: ProviderRequest, raw: dict[str, Any]) -> ProviderBatch:
        if raw.get("schema_version") != "chainlin-data/v1":
            raise ValueError("unsupported ChainLin schema_version")
        items = raw.get("records")
        if not isinstance(items, list):
            raise ValueError("records must be a list")
        fetched_at = ensure_aware(self.clock())
        records = tuple(self._record(item=item, ingested_at=fetched_at) for item in items)
        if not records:
            return ProviderBatch(
                provider_id=self.metadata.provider_id,
                request=request,
                fetched_at=fetched_at,
                status="no_data",
                no_data_reason=str(raw.get("no_data_reason") or "No visible ChainLin records."),
            )
        return ProviderBatch(
            provider_id=self.metadata.provider_id,
            request=request,
            fetched_at=fetched_at,
            status="success",
            records=records,
        )

    @staticmethod
    def _record(*, item: Any, ingested_at: datetime) -> ProviderRecord:
        if not isinstance(item, dict) or not isinstance(item.get("payload"), dict):
            raise ValueError("record and payload must be objects")
        return ProviderRecord(
            subject=str(item["subject"]),
            kind=str(item["kind"]),
            timeline=Timeline(
                event_time=datetime.fromisoformat(str(item["event_time"])),
                available_time=datetime.fromisoformat(str(item["available_time"])),
                ingested_time=ingested_at,
                revision_time=datetime.fromisoformat(str(item["revision_time"])),
            ),
            source_uri=None if item.get("source_uri") is None else str(item["source_uri"]),
            summary=str(item["summary"]),
            payload=item["payload"],
        )
