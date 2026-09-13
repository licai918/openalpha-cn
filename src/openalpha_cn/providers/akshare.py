"""Optional, disabled-by-default AKShare research adapter."""

import importlib
import json
from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from openalpha_cn.domain.time import Timeline
from openalpha_cn.providers.base import (
    ProviderBatch,
    ProviderFailure,
    ProviderMetadata,
    ProviderRecord,
    ProviderRequest,
    utc_now,
)

_CHINA_TZ = ZoneInfo("Asia/Shanghai")


class FrameLike(Protocol):
    """The small DataFrame surface consumed by this adapter."""

    empty: bool

    def to_dict(self, *, orient: str) -> list[dict[str, Any]]:
        """Return records."""


class AKShareClient(Protocol):
    """The allowlisted AKShare client surface."""

    def stock_zh_a_hist(self, **kwargs: str) -> FrameLike:
        """Return A-share daily history."""


class AKShareProvider:
    """Read allowlisted AKShare datasets for local research use."""

    _metadata = ProviderMetadata(
        provider_id="akshare.research",
        display_name="AKShare research adapter",
        source_license="AKShare and upstream-source terms",
        redistribution="restricted",
        credential_env_vars=(),
        supported_datasets=("stock_zh_a_hist",),
        caching_policy="provider-defined",
        rate_limit="Defined by each AKShare upstream source.",
        freshness="Daily records use a conservative 16:30 Asia/Shanghai availability time.",
        failure_semantics=(
            "Missing package, unsupported functions, and upstream errors are explicit."
        ),
    )

    def __init__(
        self,
        *,
        client: AKShareClient | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._client = client
        self._clock = clock

    @property
    def metadata(self) -> ProviderMetadata:
        """Return AKShare research-use policy."""
        return self._metadata

    def probe_subjects(self, dataset: str) -> tuple[str, ...]:
        """Return the one subject `doctor --probe` must supply for `dataset`.

        `fetch()` requires exactly one subject for `stock_zh_a_hist`; without this
        hook, `cli._probe_report` (which defaults to `subjects=()`) would always hit
        that check and report `configuration`, regardless of whether AKShare is
        actually installed and reachable. `000001.SZ` (Ping An Bank) is a real,
        continuously-listed A-share symbol used only to exercise the contract -- not a
        recommendation.
        """
        if dataset != "stock_zh_a_hist":
            return ()
        return ("000001.SZ",)

    def fetch(self, request: ProviderRequest) -> ProviderBatch:
        """Fetch one allowlisted symbol/day or raise a structured failure."""
        if request.dataset != "stock_zh_a_hist":
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message=f"Unsupported AKShare dataset: {request.dataset}",
                retryable=False,
            )
        if len(request.subjects) != 1:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message="AKShare stock_zh_a_hist requires exactly one subject.",
                retryable=False,
            )
        client = self._load_client()
        day = request.as_of.astimezone(_CHINA_TZ).strftime("%Y%m%d")
        subject = request.subjects[0]
        try:
            frame = client.stock_zh_a_hist(
                symbol=subject.split(".", maxsplit=1)[0],
                period="daily",
                start_date=day,
                end_date=day,
                adjust="",
            )
            raw_records = frame.to_dict(orient="records")
            records = self._decode(
                rows=raw_records,
                subject=subject,
                request=request,
            )
        except ProviderFailure:
            raise
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="upstream",
                message=f"AKShare request failed: {error}",
                retryable=True,
            ) from error

        if not records:
            return ProviderBatch(
                provider_id=self.metadata.provider_id,
                request=request,
                fetched_at=self._clock(),
                status="no_data",
                no_data_reason="AKShare returned no records visible at the request clock.",
            )
        return ProviderBatch(
            provider_id=self.metadata.provider_id,
            request=request,
            fetched_at=self._clock(),
            status="success",
            records=records,
        )

    def _load_client(self) -> AKShareClient:
        if self._client is not None:
            return self._client
        try:
            module = importlib.import_module("akshare")
        except ModuleNotFoundError as error:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message="AKShare is not installed; install the optional akshare extra.",
                retryable=False,
            ) from error
        return cast(AKShareClient, module)

    def _decode(
        self,
        *,
        rows: list[dict[str, Any]],
        subject: str,
        request: ProviderRequest,
    ) -> tuple[ProviderRecord, ...]:
        ingested_at = self._clock()
        records: list[ProviderRecord] = []
        for raw in rows:
            row = cast(dict[str, JsonValue], _normalize_json(raw))
            trade_date = _parse_date(raw["日期"])
            event_time = datetime.combine(trade_date, time(15, 0), tzinfo=_CHINA_TZ)
            available_time = datetime.combine(trade_date, time(16, 30), tzinfo=_CHINA_TZ)
            if available_time > request.as_of:
                continue
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
                    source_uri=f"akshare://stock_zh_a_hist/{subject}/{trade_date:%Y%m%d}",
                    summary=f"AKShare daily record for {subject} on {trade_date.isoformat()}.",
                    payload=cast(JsonValue, row),
                )
            )
        return tuple(records)


def _parse_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def _normalize_json(value: object) -> JsonValue:
    return cast(JsonValue, json.loads(json.dumps(value, ensure_ascii=False, default=str)))
