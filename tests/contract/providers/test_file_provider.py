import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from openalpha_cn.providers.base import (
    ProviderFailure,
    ProviderMetadata,
    ProviderRequest,
)
from openalpha_cn.providers.file import FileProvider
from openalpha_cn.storage.parquet import read_parquet_records


def rows() -> list[dict[str, object]]:
    return [
        {
            "subject": "000001.SZ",
            "kind": "limit_up",
            "event_time": "2026-07-24T09:30:00+00:00",
            "available_time": "2026-07-24T10:00:00+00:00",
            "ingested_time": "2026-07-24T10:01:00+00:00",
            "revision_time": "2026-07-24T10:00:00+00:00",
            "source_uri": "fixture://000001.SZ",
            "summary": "Visible before the request clock.",
            "payload": {"close": 10.5, "board_count": 2},
        },
        {
            "subject": "000002.SZ",
            "kind": "limit_up",
            "event_time": "2026-07-24T09:30:00+00:00",
            "available_time": "2026-07-24T11:00:00+00:00",
            "ingested_time": "2026-07-24T11:01:00+00:00",
            "revision_time": "2026-07-24T11:00:00+00:00",
            "source_uri": "fixture://000002.SZ",
            "summary": "Not visible at the request clock.",
            "payload": {"close": 8.2, "board_count": 1},
        },
    ]


def write_fixture(path: Path, format_name: str) -> None:
    records = rows()
    if format_name == "json":
        path.write_text(json.dumps(records), encoding="utf-8")
    elif format_name == "jsonl":
        path.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )
    elif format_name == "csv":
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "subject",
                    "kind",
                    "event_time",
                    "available_time",
                    "ingested_time",
                    "revision_time",
                    "source_uri",
                    "summary",
                    "payload_json",
                ],
            )
            writer.writeheader()
            for record in records:
                writer.writerow(
                    {
                        **{key: value for key, value in record.items() if key != "payload"},
                        "payload_json": json.dumps(record["payload"]),
                    }
                )
    elif format_name == "parquet":
        with duckdb.connect(":memory:") as connection:
            connection.execute(
                """
                CREATE TABLE records (
                    subject VARCHAR,
                    kind VARCHAR,
                    event_time VARCHAR,
                    available_time VARCHAR,
                    ingested_time VARCHAR,
                    revision_time VARCHAR,
                    source_uri VARCHAR,
                    summary VARCHAR,
                    payload_json VARCHAR
                )
                """
            )
            connection.executemany(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        record["subject"],
                        record["kind"],
                        record["event_time"],
                        record["available_time"],
                        record["ingested_time"],
                        record["revision_time"],
                        record["source_uri"],
                        record["summary"],
                        json.dumps(record["payload"]),
                    )
                    for record in records
                ],
            )
            connection.execute("COPY records TO ? (FORMAT PARQUET)", [str(path)])
    else:
        raise AssertionError(f"unsupported test format: {format_name}")


@pytest.mark.parametrize("format_name", ["csv", "json", "jsonl", "parquet"])
def test_file_provider_reads_all_supported_formats_point_in_time(
    tmp_path: Path,
    format_name: str,
    metadata: ProviderMetadata,
    frozen_now: datetime,
) -> None:
    AS_OF = frozen_now
    source = tmp_path / f"events.{format_name}"
    write_fixture(source, format_name)
    # Only `.parquet` ever calls into `parquet_reader`; the other three formats never
    # touch it, so passing it unconditionally here is harmless for them and lets this one
    # parametrized construction call cover all four formats, matching the real DuckDB-backed
    # reader `cli.py`/`sdk.py` inject in production.
    provider = FileProvider(path=source, metadata=metadata, parquet_reader=read_parquet_records)

    batch = provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF, subjects=("000001.SZ",)))

    assert batch.status == "success"
    assert batch.provider_id == provider.metadata.provider_id
    assert len(batch.records) == 1
    assert batch.records[0].subject == "000001.SZ"
    assert batch.records[0].timeline.available_time <= AS_OF
    assert batch.payload_digest.startswith("sha256:")


def test_file_provider_metadata_supported_datasets_is_caller_defined(tmp_path: Path) -> None:
    caller_metadata = ProviderMetadata(
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
    provider = FileProvider(path=tmp_path / "events.json", metadata=caller_metadata)

    assert provider.metadata.supported_datasets == ("events",)


def test_file_provider_returns_explicit_no_data_result(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    AS_OF = frozen_now
    source = tmp_path / "events.json"
    write_fixture(source, "json")
    provider = FileProvider(path=source, metadata=metadata)

    batch = provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF, subjects=("999999.SH",)))

    assert batch.status == "no_data"
    assert batch.records == ()
    assert batch.no_data_reason == "No visible records matched the request."


def _revised_after(as_of: datetime) -> dict[str, object]:
    """A row that first became available half an hour before `as_of` and carries a version
    revised a quarter of an hour after it."""
    return {
        "subject": "000003.SZ",
        "kind": "limit_up",
        "event_time": (as_of - timedelta(hours=1)).isoformat(),
        "available_time": (as_of - timedelta(minutes=30)).isoformat(),
        "ingested_time": (as_of + timedelta(minutes=16)).isoformat(),
        "revision_time": (as_of + timedelta(minutes=15)).isoformat(),
        "source_uri": "fixture://000003.SZ",
        "summary": "Available before the request clock, revised after it.",
        "payload": {"close": 9.1, "board_count": 1},
    }


def test_a_row_revised_after_as_of_is_dropped_rather_than_failing_the_import(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    """The file is filtered with the same predicate the batch contract enforces, so a row the
    batch would refuse never reaches it: the import keeps what was visible at `as_of` and
    drops the version nobody could have read then, exactly as it drops a row not yet available.

    Filtering on availability alone while the batch checks both clocks made one such row fail
    the whole import -- a `ValidationError` from the batch contract, outside this provider's
    own failure translation -- for every historical `as_of` inside a revision window.
    """
    source = tmp_path / "events.json"
    source.write_text(json.dumps([rows()[0], _revised_after(frozen_now)]), encoding="utf-8")
    provider = FileProvider(path=source, metadata=metadata)

    batch = provider.fetch(ProviderRequest(dataset="events", as_of=frozen_now))
    later = provider.fetch(
        ProviderRequest(dataset="events", as_of=frozen_now + timedelta(minutes=15))
    )

    assert batch.status == "success"
    assert [record.subject for record in batch.records] == ["000001.SZ"]
    assert [record.subject for record in later.records] == ["000001.SZ", "000003.SZ"]


def test_a_file_whose_only_row_is_revised_after_as_of_is_no_data(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    source = tmp_path / "events.json"
    source.write_text(json.dumps([_revised_after(frozen_now)]), encoding="utf-8")
    provider = FileProvider(path=source, metadata=metadata)

    batch = provider.fetch(ProviderRequest(dataset="events", as_of=frozen_now))

    assert batch.status == "no_data"
    assert batch.no_data_reason == "No visible records matched the request."


def test_file_provider_raises_structured_failure_for_malformed_input(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    AS_OF = frozen_now
    source = tmp_path / "events.json"
    source.write_text("{not-json", encoding="utf-8")
    provider = FileProvider(path=source, metadata=metadata)

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF))

    assert captured.value.category == "invalid_response"
    assert captured.value.provider_id == "user.file"
    assert captured.value.retryable is False


def test_file_provider_raises_structured_failure_for_malformed_parquet_input(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    """A corrupt `.parquet` file must still raise `ProviderFailure`, not a bare duckdb
    error. DuckDB's own failure (`duckdb.Error` in the current implementation, or
    whatever internal type replaces it) is provider-internal machinery -- pinning this
    behavior at the contract-test level (rather than only relying on the malformed-JSON
    case above, which never touches DuckDB at all) is what protects V2-P0B-011's move of
    the DuckDB dependency out of `providers/file.py` from silently losing this failure
    translation.
    """
    AS_OF = frozen_now
    source = tmp_path / "events.parquet"
    source.write_bytes(b"not a real parquet file")
    # Inject the real DuckDB-backed reader (as `cli.py`/`sdk.py` do) so this exercises an
    # actual duckdb.Error -> ValueError -> ProviderFailure translation, not merely the
    # "no reader configured" ValueError a provider built without one would raise instead.
    provider = FileProvider(path=source, metadata=metadata, parquet_reader=read_parquet_records)

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF))

    assert captured.value.category == "invalid_response"
    assert captured.value.provider_id == "user.file"
    assert captured.value.retryable is False
