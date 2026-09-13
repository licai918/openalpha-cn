"""Unit-test `FileProvider`'s injected `parquet_reader` dependency directly.

`tests/contract/providers/test_file_provider.py` exercises the real, DuckDB-backed
`storage.parquet.read_parquet_records` end to end; `tests/unit/providers/
test_file_provider_import_isolation.py` proves `duckdb` is unreachable from a fresh
process. This module fills the gap between them: fast, in-process tests of the DI wiring
itself (a fake `parquet_reader`, never DuckDB) -- that `fetch()` calls the injected reader
with `self.path`, that its return value flows through unchanged, that a reader's own
failure is translated into `ProviderFailure` exactly like every other `_read` branch, and
that constructing a `FileProvider` without a reader at all fails the same structured way
rather than crashing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from openalpha_cn.providers.base import ProviderFailure, ProviderMetadata, ProviderRequest
from openalpha_cn.providers.file import FileProvider

_METADATA = ProviderMetadata(
    provider_id="user.file",
    display_name="User-owned file",
    source_license="user-supplied",
    redistribution="restricted",
    credential_env_vars=(),
    caching_policy="local-permitted",
    rate_limit="not-applicable",
    freshness="defined-by-input-file",
    failure_semantics="Malformed or unreadable inputs raise ProviderFailure.",
    supported_datasets=("events",),
)

_ROW = {
    "subject": "000001.SZ",
    "kind": "limit_up",
    "event_time": "2026-07-24T09:30:00+00:00",
    "available_time": "2026-07-24T10:00:00+00:00",
    "ingested_time": "2026-07-24T10:01:00+00:00",
    "revision_time": "2026-07-24T10:00:00+00:00",
    "source_uri": "fixture://000001.SZ",
    "summary": "Visible before the request clock.",
    "payload": {"close": 10.5},
}


def test_fetch_calls_the_injected_parquet_reader_with_self_path(tmp_path: Path) -> None:
    source = tmp_path / "events.parquet"
    source.write_bytes(b"irrelevant -- the fake reader never looks at file contents")
    seen_paths: list[Path] = []

    def fake_reader(path: Path) -> list[dict[str, object]]:
        seen_paths.append(path)
        return [_ROW]

    provider = FileProvider(path=source, metadata=_METADATA, parquet_reader=fake_reader)

    batch = provider.fetch(
        ProviderRequest(dataset="events", as_of=datetime.now(UTC), subjects=("000001.SZ",))
    )

    assert seen_paths == [source]
    assert batch.status == "success"
    assert batch.records[0].subject == "000001.SZ"


def test_fetch_raises_provider_failure_when_no_parquet_reader_is_configured(
    tmp_path: Path,
) -> None:
    source = tmp_path / "events.parquet"
    source.write_bytes(b"irrelevant -- fetch() must fail before any content is read")
    provider = FileProvider(path=source, metadata=_METADATA)

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch(ProviderRequest(dataset="events", as_of=datetime.now(UTC)))

    assert captured.value.category == "invalid_response"
    assert captured.value.provider_id == "user.file"
    assert captured.value.retryable is False


def test_a_parquet_readers_own_failure_is_translated_into_provider_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "events.parquet"
    source.write_bytes(b"irrelevant -- the fake reader raises unconditionally")

    def failing_reader(path: Path) -> list[dict[str, object]]:
        raise ValueError(f"cannot read parquet file {path.name}: simulated duckdb.Error")

    provider = FileProvider(path=source, metadata=_METADATA, parquet_reader=failing_reader)

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch(ProviderRequest(dataset="events", as_of=datetime.now(UTC)))

    assert captured.value.category == "invalid_response"
    assert captured.value.provider_id == "user.file"
    assert captured.value.retryable is False
    assert "simulated duckdb.Error" in str(captured.value)
