"""Tushare Pro BYOT adapter with explicit HTTP and point-in-time semantics."""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, time
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from openalpha_cn.domain.time import Timeline
from openalpha_cn.providers.base import (
    ProviderBatch,
    ProviderFailure,
    ProviderFailureCategory,
    ProviderMetadata,
    ProviderRecord,
    ProviderRequest,
    utc_now,
)

_CHINA_TZ = ZoneInfo("Asia/Shanghai")


class TushareTransport(Protocol):
    """Injectable transport boundary for Tushare Pro."""

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a Tushare request and return its decoded object."""


class UrllibTushareTransport:
    """Minimal standard-library HTTP transport for Tushare Pro."""

    def __init__(self, endpoint: str = "https://api.tushare.pro") -> None:
        self.endpoint = endpoint

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON without logging or persisting the user token."""
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            decoded = json.loads(response.read().decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Tushare response must be a JSON object")
        return cast(dict[str, Any], decoded)


class TushareProvider:
    """Fetch Tushare daily records with a user-supplied token."""

    _metadata = ProviderMetadata(
        provider_id="tushare.pro",
        display_name="Tushare Pro BYOT",
        source_license="Tushare Pro service terms",
        redistribution="restricted",
        credential_env_vars=("TUSHARE_TOKEN",),
        supported_datasets=("daily",),
        caching_policy="provider-defined",
        rate_limit="Depends on the user's Tushare Pro account and points.",
        freshness="Daily records use a conservative 16:30 Asia/Shanghai availability time.",
        failure_semantics=(
            "Authentication, quota, transport, and schema errors raise ProviderFailure."
        ),
    )

    def __init__(
        self,
        *,
        token: str | None = None,
        transport: TushareTransport | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._token = token if token is not None else os.getenv("TUSHARE_TOKEN", "")
        self._transport = transport or UrllibTushareTransport()
        self._clock = clock

    @property
    def metadata(self) -> ProviderMetadata:
        """Return Tushare licensing and credential policy."""
        return self._metadata

    def fetch(self, request: ProviderRequest) -> ProviderBatch:
        """Fetch an allowlisted daily dataset or raise a structured failure."""
        if not self._token:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message="TUSHARE_TOKEN is required.",
                retryable=False,
            )
        if request.dataset != "daily":
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message=f"Unsupported Tushare dataset: {request.dataset}",
                retryable=False,
            )

        local_date = request.as_of.astimezone(_CHINA_TZ).strftime("%Y%m%d")
        parameters = {"trade_date": local_date}
        if request.subjects:
            parameters["ts_code"] = ",".join(request.subjects)
        payload = {
            "api_name": request.dataset,
            "token": self._token,
            "params": parameters,
            "fields": "",
        }
        try:
            response = self._transport.post(payload)
            records = self._decode_daily(response=response, request=request)
        except ProviderFailure:
            raise
        except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError) as error:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="invalid_response",
                message=f"Tushare response could not be decoded: {error}",
                retryable=False,
            ) from error

        if not records:
            return ProviderBatch(
                provider_id=self.metadata.provider_id,
                request=request,
                fetched_at=self._clock(),
                status="no_data",
                no_data_reason="Tushare returned no records visible at the request clock.",
            )
        return ProviderBatch(
            provider_id=self.metadata.provider_id,
            request=request,
            fetched_at=self._clock(),
            status="success",
            records=records,
        )

    def _decode_daily(
        self,
        *,
        response: dict[str, Any],
        request: ProviderRequest,
    ) -> tuple[ProviderRecord, ...]:
        code = response.get("code")
        if code != 0:
            category: ProviderFailureCategory = "authentication" if code == -2001 else "upstream"
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category=category,
                message=f"Tushare rejected the request: {response.get('msg') or 'unknown error'}",
                retryable=category == "upstream",
            )
        data = response.get("data")
        if not isinstance(data, dict):
            raise ValueError("Tushare data must be an object")
        fields = data.get("fields")
        items = data.get("items")
        if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
            raise ValueError("Tushare fields must be a string array")
        if not isinstance(items, list):
            raise ValueError("Tushare items must be an array")

        ingested_at = self._clock()
        records: list[ProviderRecord] = []
        for item in items:
            if not isinstance(item, list) or len(item) != len(fields):
                raise ValueError("Tushare item does not match fields")
            row = dict(zip(fields, item, strict=True))
            trade_date = datetime.strptime(str(row["trade_date"]), "%Y%m%d").date()
            event_time = datetime.combine(trade_date, time(15, 0), tzinfo=_CHINA_TZ)
            available_time = datetime.combine(trade_date, time(16, 30), tzinfo=_CHINA_TZ)
            if available_time > request.as_of:
                continue
            subject = str(row["ts_code"])
            records.append(
                ProviderRecord(
                    subject=subject,
                    kind="daily",
                    timeline=Timeline(
                        event_time=event_time,
                        available_time=available_time,
                        ingested_time=ingested_at,
                        revision_time=available_time,
                    ),
                    source_uri=f"tushare://daily/{subject}/{trade_date:%Y%m%d}",
                    summary=f"Tushare daily record for {subject} on {trade_date.isoformat()}.",
                    payload=cast(JsonValue, row),
                )
            )
        return tuple(records)
