"""Legal-safe provider for user-owned CSV, JSON, JSONL, and Parquet files."""

import csv
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import cast

import duckdb
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


class FileProvider:
    """Read canonical point-in-time records from a user-owned local file."""

    def __init__(
        self,
        *,
        path: Path,
        metadata: ProviderMetadata,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = path
        self._metadata = metadata
        self._clock = clock

    @property
    def metadata(self) -> ProviderMetadata:
        """Return the configured source policy."""
        return self._metadata

    def fetch(self, request: ProviderRequest) -> ProviderBatch:
        """Read and filter visible records or raise a structured failure."""
        try:
            raw_records = self._read()
            records = tuple(self._to_record(raw) for raw in raw_records)
        except (OSError, ValueError, TypeError, KeyError, duckdb.Error) as error:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="invalid_response",
                message=f"Cannot read {self.path.name}: {error}",
                retryable=False,
            ) from error

        subject_filter = set(request.subjects)
        visible = tuple(
            record
            for record in records
            if record.timeline.available_time <= request.as_of
            and (not subject_filter or record.subject in subject_filter)
        )
        if not visible:
            return ProviderBatch(
                provider_id=self.metadata.provider_id,
                request=request,
                fetched_at=self._clock(),
                status="no_data",
                no_data_reason="No visible records matched the request.",
            )
        return ProviderBatch(
            provider_id=self.metadata.provider_id,
            request=request,
            fetched_at=self._clock(),
            status="success",
            records=visible,
        )

    def _read(self) -> list[dict[str, object]]:
        suffix = self.path.suffix.lower()
        if suffix == ".csv":
            with self.path.open(encoding="utf-8-sig", newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        if suffix == ".json":
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
                raise ValueError("JSON input must be an array of objects")
            return [cast(dict[str, object], row) for row in value]
        if suffix in {".jsonl", ".ndjson"}:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            values = [json.loads(line) for line in lines if line.strip()]
            if not all(isinstance(row, dict) for row in values):
                raise ValueError("JSONL lines must be objects")
            return [cast(dict[str, object], row) for row in values]
        if suffix == ".parquet":
            with duckdb.connect(":memory:") as connection:
                cursor = connection.execute("SELECT * FROM read_parquet(?)", [str(self.path)])
                columns = [item[0] for item in cursor.description]
                return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        raise ValueError(f"unsupported file extension: {suffix}")

    @staticmethod
    def _to_record(raw: dict[str, object]) -> ProviderRecord:
        payload = raw.get("payload")
        if payload is None:
            payload_text = raw["payload_json"]
            if not isinstance(payload_text, str):
                raise TypeError("payload_json must be a string")
            payload = json.loads(payload_text)
        return ProviderRecord(
            subject=str(raw["subject"]),
            kind=str(raw["kind"]),
            timeline=Timeline(
                event_time=_parse_datetime(raw["event_time"]),
                available_time=_parse_datetime(raw["available_time"]),
                ingested_time=_parse_datetime(raw["ingested_time"]),
                revision_time=_parse_datetime(raw["revision_time"]),
            ),
            source_uri=None if raw.get("source_uri") is None else str(raw["source_uri"]),
            summary=str(raw["summary"]),
            payload=cast(JsonValue, payload),
        )


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError("timestamp must be an ISO-8601 string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
