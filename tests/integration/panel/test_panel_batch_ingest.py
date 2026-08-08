"""`ColumnarPanelBatch` -> `PanelStore` end to end (`V2-P1-002`, question 3 of the brief).

The whole point of a columnar batch is that it reaches storage without being taken apart a
row at a time, so the seam is deliberately thin: `write_panel_batch()` maps each column's
logical kind to a DuckDB type through a closed table and hands `PanelStore.write_partition`
the batch's own `to_rows()` block (one C-level `zip` transpose, no per-row object churn).

The round-trip tests below are what make the batch-level digest a *real* integrity check
rather than a self-consistent no-op: a batch is written to Parquet, read back through
DuckDB, rebuilt into a fresh `ColumnarPanelBatch`, and its digest compared with the
original's. `TIMESTAMPTZ` values come back in the DuckDB session's timezone rather than UTC
(measured against DuckDB 1.5 while writing this task -- e.g. an instant stored as
`2024-06-28T07:00Z` reads back tagged `America/Toronto`), so this round trip only closes
because the digest is computed over the UTC instant, never over a datetime's text form.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest

from openalpha_cn.domain.panel_batch import (
    ColumnarPanelBatch,
    PanelBatchError,
    PanelColumn,
    PanelColumnKind,
    TimelineColumns,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    PANEL_DUCKDB_TYPES,
    panel_column_specs,
    write_panel_batch,
)

AS_OF = datetime(2024, 6, 28, 12, 0, tzinfo=UTC)
AVAILABLE = datetime(2024, 6, 28, 7, 0, 0, 123456, tzinfo=UTC)
INGESTED = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)
_ROWS = 40


def _batch(*, closes: tuple[float, ...] | None = None) -> ColumnarPanelBatch:
    values = closes if closes is not None else tuple(10.0 + index for index in range(_ROWS))
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset="prices_daily",
        kind="daily",
        as_of=AS_OF,
        fetched_at=AS_OF,
        status="success",
        subjects=tuple(f"{index:06d}.SZ" for index in range(_ROWS)),
        timeline=TimelineColumns(
            event_time=tuple(AVAILABLE - timedelta(hours=1) for _ in range(_ROWS)),
            available_time=tuple(
                AVAILABLE + timedelta(microseconds=index) for index in range(_ROWS)
            ),
            ingested_time=tuple(INGESTED for _ in range(_ROWS)),
            revision_time=tuple(INGESTED for _ in range(_ROWS)),
        ),
        columns=(
            PanelColumn("close", "float", values),
            PanelColumn("vol", "integer", tuple(index * 1000 for index in range(_ROWS))),
            PanelColumn("is_st", "boolean", tuple(index % 3 == 0 for index in range(_ROWS))),
            PanelColumn("name", "string", tuple(f"stock {index}" for index in range(_ROWS))),
            PanelColumn(
                "ann_date",
                "timestamp",
                tuple(AVAILABLE - timedelta(days=index) for index in range(_ROWS)),
            ),
        ),
    )


def _read_back(store: PanelStore, batch: ColumnarPanelBatch, *, year: int) -> ColumnarPanelBatch:
    """Rebuild an equivalent batch from what actually landed in Parquet.

    Rows are re-sorted by `subject` because `PanelStore.query()` offers no ordering
    control; the claim under test is content integrity, not row-order preservation.
    """
    specs = batch.storage_columns()
    rows = store.query(batch.dataset, year=year, columns=[column.name for column in specs])
    ordered = sorted(rows, key=lambda row: str(row[0]))
    transposed = tuple(zip(*ordered, strict=True))
    by_name = {column.name: values for column, values in zip(specs, transposed, strict=True)}
    return ColumnarPanelBatch(
        provider_id=batch.provider_id,
        dataset=batch.dataset,
        kind=batch.kind,
        as_of=batch.as_of,
        fetched_at=batch.fetched_at,
        status=batch.status,
        subjects=tuple(str(value) for value in by_name["subject"]),
        timeline=TimelineColumns(
            event_time=_datetimes(by_name["event_time"]),
            available_time=_datetimes(by_name["available_time"]),
            ingested_time=_datetimes(by_name["ingested_time"]),
            revision_time=_datetimes(by_name["revision_time"]),
        ),
        columns=tuple(
            PanelColumn(column.name, column.kind, by_name[column.name]) for column in batch.columns
        ),
    )


def _datetimes(values: tuple[object, ...]) -> tuple[datetime, ...]:
    return tuple(value for value in values if isinstance(value, datetime))


# --- the seam ------------------------------------------------------------------------------


def test_write_panel_batch_persists_every_storage_column_in_one_partition(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    batch = _batch()

    reference = write_panel_batch(store, batch, year=2024)

    assert reference.dataset == "prices_daily"
    assert reference.year == 2024
    assert reference.row_count == _ROWS
    assert reference.path == tmp_path / "panel" / "prices_daily" / "2024" / "data.parquet"
    stored = store.query("prices_daily", year=2024, columns=["subject", "close", "available_time"])
    assert len(stored) == _ROWS


def test_the_column_specs_handed_to_the_store_come_from_a_closed_type_table() -> None:
    """No caller-supplied SQL type ever reaches `write_partition`'s DDL f-string: the
    logical kind is a `Literal`, and the mapping to DuckDB syntax is this closed table."""
    assert set(PANEL_DUCKDB_TYPES) == set(get_args(PanelColumnKind))

    specs = panel_column_specs(_batch())

    assert [spec.name for spec in specs] == [
        "subject",
        "event_time",
        "available_time",
        "ingested_time",
        "revision_time",
        "close",
        "vol",
        "is_st",
        "name",
        "ann_date",
    ]
    assert [spec.duckdb_type for spec in specs] == [
        "VARCHAR",
        "TIMESTAMPTZ",
        "TIMESTAMPTZ",
        "TIMESTAMPTZ",
        "TIMESTAMPTZ",
        "DOUBLE",
        "BIGINT",
        "BOOLEAN",
        "VARCHAR",
        "TIMESTAMPTZ",
    ]


def test_a_no_data_batch_is_refused_rather_than_silently_writing_nothing(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    batch = ColumnarPanelBatch(
        provider_id="tushare",
        dataset="prices_daily",
        kind="daily",
        as_of=AS_OF,
        fetched_at=AS_OF,
        status="no_data",
        no_data_reason="every listed name was suspended",
    )

    with pytest.raises(PanelBatchError, match="no_data"):
        write_panel_batch(store, batch, year=2024)

    assert store.query("prices_daily", year=2024, columns=["subject"]) == []


def test_a_hostile_column_name_cannot_reach_the_stores_sql_through_a_batch() -> None:
    """`PanelStore.write_partition` builds its DDL as `f'"{column.name}" {duckdb_type}'`, so
    a name containing `"` escapes the quoting and executes as SQL (verified directly against
    `43f3522`). Every column name that reaches the store through this seam has already
    passed the contract's plain-identifier rule, so that shape cannot be constructed here."""
    hostile = "close\" INTEGER); ATTACH '/tmp/evil.duckdb' AS evil; CREATE TABLE staging2(\"x"

    with pytest.raises(PanelBatchError):
        PanelColumn(hostile, "float", (1.0,))


# --- integrity across a real storage round trip --------------------------------------------


def test_the_batch_digest_survives_a_full_parquet_and_duckdb_round_trip(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    batch = _batch()
    write_panel_batch(store, batch, year=2024)

    restored = _read_back(store, batch, year=2024)

    assert restored.content_digest == batch.content_digest


def test_a_single_corrupted_value_is_detected_after_the_round_trip(tmp_path: Path) -> None:
    """The integrity claim, exercised through real storage rather than in memory only: one
    changed float out of 40 rows x 10 columns makes the recomputed digest differ."""
    clean = _batch()
    closes = list(10.0 + index for index in range(_ROWS))
    closes[17] = closes[17] + 1e-9
    corrupted = _batch(closes=tuple(closes))
    assert corrupted.content_digest != clean.content_digest

    clean_store = PanelStore(tmp_path / "clean")
    corrupt_store = PanelStore(tmp_path / "corrupt")
    write_panel_batch(clean_store, clean, year=2024)
    write_panel_batch(corrupt_store, corrupted, year=2024)

    assert _read_back(clean_store, clean, year=2024).content_digest == clean.content_digest
    assert _read_back(corrupt_store, clean, year=2024).content_digest != clean.content_digest


def test_writing_the_same_batch_twice_is_an_idempotent_no_op(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    batch = _batch()

    first = write_panel_batch(store, batch, year=2024)
    second = write_panel_batch(store, batch, year=2024)

    assert first.content_hash == second.content_hash
    assert _read_back(store, batch, year=2024).content_digest == batch.content_digest
