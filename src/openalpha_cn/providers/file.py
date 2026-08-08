"""Legal-safe provider for user-owned CSV, JSON, JSONL, and Parquet files."""

import csv
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

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


class ParquetReader(Protocol):
    """The Parquet-reading capability `FileProvider` needs, satisfied structurally.

    Mirrors the `models.governance.ModelUsageStore` precedent (V2-P0B-011): the Protocol
    lives beside its consumer (`FileProvider`, here in `providers/`), and the concrete
    DuckDB-backed implementation (`storage.parquet.read_parquet_records`) is injected by
    `FileProvider`'s composition sites (`cli.py`'s `evidence_build`, `sdk.py`'s
    `OpenAlphaSDK.build_file_evidence`) -- neither of which the `providers-no-infra-imports`
    import-linter contract's `source_modules` covers.

    `providers/file.py` does not import `storage.parquet`, or anything else that imports
    `duckdb`, even just for this Protocol's sake. Two earlier approaches were tried and
    rejected for this same fix, recorded here so neither gets repeated:

    1. A top-level `import duckdb` in this module -- the original ADR-0001 violation
       (`providers.file -> duckdb`).
    2. `providers/file.py` importing `storage.parquet` directly for the real reader. This
       looks safe (`storage.parquet` is not `duckdb`) but import-linter's `forbidden`
       contract walks the whole static import graph, not just direct edges:
       `providers.file -> storage.parquet -> duckdb` is flagged identically to a direct
       `providers.file -> duckdb` import, because `duckdb` stays transitively reachable
       from `openalpha_cn.providers`. (A third approach, wrapping `duckdb` in
       `importlib.import_module` to hide it from static analysis, was reverted: it made
       the layering gate pass without removing the runtime coupling it exists to catch,
       and it let an unwrapped `ModuleNotFoundError` escape `fetch()` on the first
       `.parquet` read whenever `duckdb` happened to be unavailable.)

    A Protocol is the only way out of both: it lets `FileProvider` depend on "a thing that
    can read a Parquet path into rows" without depending on anything that knows how.
    """

    def __call__(self, path: Path) -> list[dict[str, object]]:
        """Read every row of the Parquet file at `path` into plain dicts."""


class FileProvider:
    """Read canonical point-in-time records from a user-owned local file."""

    def __init__(
        self,
        *,
        path: Path,
        metadata: ProviderMetadata,
        clock: Callable[[], datetime] = utc_now,
        parquet_reader: ParquetReader | None = None,
    ) -> None:
        self.path = path
        self._metadata = metadata
        self._clock = clock
        self._parquet_reader = parquet_reader

    @property
    def metadata(self) -> ProviderMetadata:
        """Return the configured source policy."""
        return self._metadata

    def fetch(self, request: ProviderRequest) -> ProviderBatch:
        """Read and filter visible records or raise a structured failure."""
        try:
            raw_records = self._read()
            records = tuple(self._to_record(raw) for raw in raw_records)
        except (OSError, ValueError, TypeError, KeyError) as error:
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
            return self._read_parquet()
        raise ValueError(f"unsupported file extension: {suffix}")

    def _read_parquet(self) -> list[dict[str, object]]:
        """Read a Parquet file via the injected `parquet_reader`.

        See `ParquetReader`'s docstring for why this indirection exists in place of a
        static or dynamic `duckdb` import. When no reader was supplied, this raises a
        plain `ValueError` rather than attempting anything itself -- `providers/file.py`
        has no fallback path that could reach `duckdb`. `fetch()`'s
        `except (OSError, ValueError, TypeError, KeyError)` clause, unchanged by this
        method's existence, catches that `ValueError` the same way it would catch a real
        `duckdb` failure raised inside `parquet_reader`, translating either into a
        structured `ProviderFailure` -- so a missing or misconfigured dependency never
        surfaces as an unwrapped exception from `fetch()`.
        """
        if self._parquet_reader is None:
            raise ValueError(
                f"{self.path.name} is a Parquet file, but this FileProvider was "
                "constructed without a parquet_reader"
            )
        return self._parquet_reader(self.path)

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
