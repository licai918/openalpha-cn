"""`V2-P6-004`: `write_partition()`'s staging insert switches from `executemany` (one bound
statement per row) to a columnar batch insert (`unnest(?::T[])` over `_INSERT_CHUNK_ROWS`-row
chunks), because profiling a factor build on 2026-09-26 found `executemany` was 53% of its
wall clock. A standalone benchmark (116,880 rows x 10 cols) measured `executemany` at 10.84 s
against 0.88 s for the columnar path, with identical rows read back.

Every claim this file makes is about the *insert mechanism only*: `write_partition()`'s
signature, return value, idempotency and overwrite semantics (module docstring's "Write and
idempotency semantics") are unchanged, and `_content_hash()` is computed from the caller's
`(dataset, year, columns, rows)` in pure Python before any DuckDB connection is opened, so the
insert path cannot affect it either way -- the pin below is a regression guard, not a design
constraint the new code has to satisfy.

`TYPES` below is deliberately *not* the five-plus-`DATE` set the initial task brief sketched.
Grepping `src/` and `tests/` for every `ColumnSpec(...)` construction (including the
adversarial ones in `tests/integration/panel/test_panel_store_hardening.py`) turns up exactly
the five types `store.DUCKDB_COLUMN_TYPES` already names -- `BIGINT`, `BOOLEAN`, `DOUBLE`,
`TIMESTAMPTZ`, `VARCHAR` -- and nothing else (no `DECIMAL`, no list type, no `DATE`); a
`ColumnSpec("v", "DATE")` is rejected by `ColumnSpec.__post_init__` before it ever reaches
`write_partition()` (see `test_a_hostile_duckdb_type_cannot_be_carried_by_a_column_spec_at_all`
in the hardening suite), so parametrizing on `"DATE"` here would test `ColumnSpec` construction,
not the columnar insert. This file's `TYPES` is instead exactly `DUCKDB_COLUMN_TYPES`.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from openalpha_cn.panel.store import DUCKDB_COLUMN_TYPES, ColumnSpec, PanelStore

TYPES = {
    "BOOLEAN": [True, False, None],
    "DOUBLE": [1.5, -0.0, None, 1e-300],
    "BIGINT": [0, -(2**62), None],
    "VARCHAR": ["000001.SZ", "", None, "中文"],
    "TIMESTAMPTZ": [datetime(2026, 5, 6, 9, tzinfo=UTC), None],
}


def test_types_under_test_are_exactly_the_stores_closed_type_set() -> None:
    """Fixture-honesty check: if `DUCKDB_COLUMN_TYPES` is ever widened (or narrowed), this
    file's `TYPES` dict silently drifts out of covering it. Failing loudly here beats a
    round-trip suite that quietly stops exercising a type."""
    assert set(TYPES) == DUCKDB_COLUMN_TYPES


@pytest.mark.parametrize("duckdb_type", sorted(TYPES))
def test_every_panel_type_round_trips_through_the_columnar_insert(
    tmp_path: Path, duckdb_type: str
) -> None:
    store = PanelStore(tmp_path)
    values = TYPES[duckdb_type]
    rows = [(f"s{i}", values[i % len(values)]) for i in range(50_000)]
    ref = store.write_partition(
        "t", 2026, (ColumnSpec("subject", "VARCHAR"), ColumnSpec("v", duckdb_type)), rows
    )
    with duckdb.connect() as con:
        got = con.execute("SELECT subject, v FROM read_parquet(?)", [str(ref.path)]).fetchall()
    assert [tuple(row) for row in got] == rows  # order and values, across chunk boundaries


def test_write_partition_stages_its_rows_through_the_columnar_insert_in_bounded_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one test here that fails under the row-at-a-time insert: it observes the path
    (`write_partition` hands its rows to `_insert_columnar`, which cuts them at
    `_INSERT_CHUNK_ROWS`), where the round-trip and hash tests observe only the result, which an
    `executemany` insert gives as well. The chunk size is shrunk so the boundary is crossed with
    25 rows."""
    import openalpha_cn.panel.store as store_module

    monkeypatch.setattr(store_module, "_INSERT_CHUNK_ROWS", 7)
    handed: list[int] = []
    inserts: list[str] = []
    real = store_module._insert_columnar

    class CountingConnection:
        def __init__(self, connection: Any) -> None:
            self._connection = connection

        def execute(self, statement: str, parameters: Any = None) -> Any:
            inserts.append(statement)
            return self._connection.execute(statement, parameters)

    def spy(connection: Any, columns: Any, rows: Any) -> None:
        handed.append(len(rows))
        real(CountingConnection(connection), columns, rows)

    monkeypatch.setattr(store_module, "_insert_columnar", spy)
    rows = [(f"s{i}", float(i)) for i in range(25)]
    ref = PanelStore(tmp_path).write_partition(
        "t", 2026, (ColumnSpec("subject", "VARCHAR"), ColumnSpec("v", "DOUBLE")), rows
    )

    assert handed == [25]
    assert len(inserts) == 4  # ceil(25 / 7) statements, not one per row
    with duckdb.connect() as con:
        got = con.execute("SELECT subject, v FROM read_parquet(?)", [str(ref.path)]).fetchall()
    assert [tuple(row) for row in got] == rows


def test_content_hash_is_unchanged_by_the_insert_path(tmp_path: Path) -> None:
    # `content_hash` is computed by `_content_hash()` in pure Python from
    # `(dataset, year, columns, rows)`, before any DuckDB connection is opened -- the insert
    # mechanism cannot affect it. Pinned against the pre-change (`executemany`) code so a
    # future edit to either path is still caught.
    store = PanelStore(tmp_path)
    rows = [("000001.SZ", 1.5), ("600000.SH", -2.25), ("000002.SZ", None)]
    ref = store.write_partition(
        "t", 2026, (ColumnSpec("subject", "VARCHAR"), ColumnSpec("v", "DOUBLE")), rows
    )
    assert ref.content_hash == ("1cd70b6fd8d41dcf6004979829dbc95cb392f995ee5d1ecf4c74fc4a26b4064d")


def test_a_row_of_the_wrong_arity_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    # Pre-change (`executemany`), a wrong-arity row surfaces as a bare
    # `duckdb.InvalidInputException` ("Prepared statement needs N parameters, M given") rather
    # than a `PanelStorageError` -- confirmed against the unmodified code before this test was
    # written. The columnar insert keeps that same exception type so no caller catching it
    # today silently stops catching after this change.
    store = PanelStore(tmp_path)
    with pytest.raises(duckdb.InvalidInputException):
        store.write_partition(
            "t", 2026, (ColumnSpec("a", "BIGINT"), ColumnSpec("b", "BIGINT")), [(1, 2), (3,)]
        )
    assert not (tmp_path / "t" / "2026" / "data.parquet").exists()


def test_a_hundred_thousand_rows_write_in_under_three_seconds(tmp_path: Path) -> None:
    store = PanelStore(tmp_path)
    rows = [(f"{i:06d}.SZ", float(i)) for i in range(100_000)]
    start = time.perf_counter()
    store.write_partition("t", 2026, (ColumnSpec("s", "VARCHAR"), ColumnSpec("v", "DOUBLE")), rows)
    assert time.perf_counter() - start < 3.0  # executemany measured ~9s on this task's machine
