"""Tushare Pro BYOT adapter with explicit HTTP and point-in-time semantics.

Every Tushare Pro HTTP endpoint shares one request envelope and one response shape
(`code` / `data.fields` / `data.items`); only four things vary per dataset: how `params`
is built, which columns hold the subject and the date, how the four PIT clocks are
derived, and the `kind`/`source_uri` used for the resulting records. `TushareDatasetDescriptor`
makes those four things data instead of code, so adding a dataset is a new row in
`TUSHARE_DATASETS`, not a new adapter.
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue

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


class ClockStrategy(StrEnum):
    """How a dataset's four PIT clocks (event/available/ingested/revision) are derived.

    Only ``daily_close`` has a real production consumer today (the ``daily`` dataset).
    ``announcement`` and ``calendar_static`` are defined and independently tested against
    synthetic data now so P1 can wire real datasets onto them without changing this shape.
    """

    daily_close = "daily_close"
    """Trading-day data: event_time=15:00, available_time=16:30, both Asia/Shanghai."""

    announcement = "announcement"
    """Financial-statement data keyed off ``ann_date``/``f_ann_date`` (original vs. revised)."""

    calendar_static = "calendar_static"
    """Static reference data: event_time == available_time == that day's 00:00."""


class TushareDatasetDescriptor(BaseModel):
    """Everything dataset-specific about one Tushare Pro endpoint.

    Frozen and ``extra="forbid"`` to match this repo's domain-model style: a descriptor
    is a value, not a place to grow ad hoc behavior.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: str = Field(min_length=1, max_length=128)
    """Tushare ``api_name``; also the value ``ProviderRequest.dataset`` must match."""
    kind: str = Field(min_length=1, max_length=64)
    """Value written to ``ProviderRecord.kind``."""
    subject_field: str | None = Field(default=None)
    """Column holding the subject (e.g. ``ts_code``); ``None`` for calendar-style datasets."""
    date_field: str = Field(min_length=1, max_length=128)
    """Column holding the record's date (e.g. ``trade_date`` / ``cal_date`` / ``end_date``)."""
    clock: ClockStrategy
    params_builder: Callable[[ProviderRequest], dict[str, str]]
    """Builds the Tushare ``params`` object from a ``ProviderRequest``."""
    source_uri_template: str = Field(min_length=1, max_length=2048)
    """``str.format`` template with ``{dataset}``, ``{subject}``, and ``{date}`` placeholders."""


def _trade_date_params(request: ProviderRequest) -> dict[str, str]:
    """Build ``{trade_date[, ts_code]}`` params for one-trading-day datasets."""
    local_date = request.as_of.astimezone(_CHINA_TZ).strftime("%Y%m%d")
    parameters = {"trade_date": local_date}
    if request.subjects:
        parameters["ts_code"] = ",".join(request.subjects)
    return parameters


TUSHARE_DATASETS: tuple[TushareDatasetDescriptor, ...] = (
    TushareDatasetDescriptor(
        dataset="daily",
        kind="daily",
        subject_field="ts_code",
        date_field="trade_date",
        clock=ClockStrategy.daily_close,
        params_builder=_trade_date_params,
        source_uri_template="tushare://{dataset}/{subject}/{date}",
    ),
)


def _dataset_names(descriptors: tuple[TushareDatasetDescriptor, ...]) -> tuple[str, ...]:
    """Return each descriptor's dataset name, in table order."""
    return tuple(descriptor.dataset for descriptor in descriptors)


_TUSHARE_DATASETS_BY_NAME: dict[str, TushareDatasetDescriptor] = {
    descriptor.dataset: descriptor for descriptor in TUSHARE_DATASETS
}


def _parse_tushare_date(value: object) -> date:
    """Parse one of Tushare's ``YYYYMMDD`` date columns."""
    return datetime.strptime(str(value), "%Y%m%d").date()


def _daily_close_timeline(row: dict[str, Any], date_field: str, ingested_at: datetime) -> Timeline:
    """15:00 close / 16:30 availability, both Asia/Shanghai, for trading-day datasets."""
    trading_day = _parse_tushare_date(row[date_field])
    event_time = datetime.combine(trading_day, time(15, 0), tzinfo=_CHINA_TZ)
    available_time = datetime.combine(trading_day, time(16, 30), tzinfo=_CHINA_TZ)
    return Timeline(
        event_time=event_time,
        available_time=available_time,
        ingested_time=ingested_at,
        revision_time=available_time,
    )


def _announcement_timeline(row: dict[str, Any], date_field: str, ingested_at: datetime) -> Timeline:
    """``ann_date`` sets event/available time; a later ``f_ann_date`` is a revision.

    ``date_field`` is unused here: announcement data always keys its PIT clocks off the
    fixed ``ann_date``/``f_ann_date`` columns that every Tushare financial-statement
    endpoint shares, regardless of which column the descriptor uses for display purposes.
    """
    ann_moment = datetime.combine(
        _parse_tushare_date(row["ann_date"]), time(0, 0), tzinfo=_CHINA_TZ
    )
    f_ann_date_raw = row.get("f_ann_date")
    revision_moment = (
        datetime.combine(_parse_tushare_date(f_ann_date_raw), time(0, 0), tzinfo=_CHINA_TZ)
        if f_ann_date_raw
        else ann_moment
    )
    return Timeline(
        event_time=ann_moment,
        available_time=ann_moment,
        ingested_time=ingested_at,
        revision_time=revision_moment,
    )


def _calendar_static_timeline(
    row: dict[str, Any], date_field: str, ingested_at: datetime
) -> Timeline:
    """Static metadata: event time and availability time are the same midnight."""
    moment = datetime.combine(_parse_tushare_date(row[date_field]), time(0, 0), tzinfo=_CHINA_TZ)
    return Timeline(
        event_time=moment,
        available_time=moment,
        ingested_time=ingested_at,
        revision_time=moment,
    )


_ClockBuilder = Callable[[dict[str, Any], str, datetime], Timeline]

_CLOCK_BUILDERS: dict[ClockStrategy, _ClockBuilder] = {
    ClockStrategy.daily_close: _daily_close_timeline,
    ClockStrategy.announcement: _announcement_timeline,
    ClockStrategy.calendar_static: _calendar_static_timeline,
}


def _resolve_subject(descriptor: TushareDatasetDescriptor, row: dict[str, Any]) -> str:
    """Return the record's subject, falling back to the dataset name when none is declared."""
    if descriptor.subject_field is None:
        return descriptor.dataset
    return str(row[descriptor.subject_field])


class TushareProvider:
    """Fetch Tushare records for any dataset declared in ``TUSHARE_DATASETS``."""

    _metadata = ProviderMetadata(
        provider_id="tushare.pro",
        display_name="Tushare Pro BYOT",
        source_license="Tushare Pro service terms",
        redistribution="restricted",
        credential_env_vars=("TUSHARE_TOKEN",),
        supported_datasets=_dataset_names(TUSHARE_DATASETS),
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
        """Fetch a descriptor-table dataset or raise a structured failure."""
        if not self._token:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message="TUSHARE_TOKEN is required.",
                retryable=False,
            )
        descriptor = _TUSHARE_DATASETS_BY_NAME.get(request.dataset)
        if descriptor is None:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message=f"Unsupported Tushare dataset: {request.dataset}",
                retryable=False,
            )

        parameters = descriptor.params_builder(request)
        payload = {
            "api_name": request.dataset,
            "token": self._token,
            "params": parameters,
            "fields": "",
        }
        try:
            response = self._transport.post(payload)
            records = self._decode(descriptor=descriptor, response=response, request=request)
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

    def _decode(
        self,
        *,
        descriptor: TushareDatasetDescriptor,
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
            timeline = _CLOCK_BUILDERS[descriptor.clock](row, descriptor.date_field, ingested_at)
            if timeline.available_time > request.as_of:
                continue
            subject = _resolve_subject(descriptor, row)
            date_value = _parse_tushare_date(row[descriptor.date_field])
            source_uri = descriptor.source_uri_template.format(
                dataset=descriptor.dataset, subject=subject, date=f"{date_value:%Y%m%d}"
            )
            records.append(
                ProviderRecord(
                    subject=subject,
                    kind=descriptor.kind,
                    timeline=timeline,
                    source_uri=source_uri,
                    summary=(
                        f"Tushare {descriptor.kind} record for {subject} "
                        f"on {date_value.isoformat()}."
                    ),
                    payload=cast(JsonValue, row),
                )
            )
        return tuple(records)
