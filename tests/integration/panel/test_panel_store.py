"""Behavioral tests for `PanelStore` (V2-P1-001): write/query round trips, idempotent
partition overwrite, and cross-process catalog persistence and concurrency.

ADR-0002 (`docs/architecture/ADR-0002-two-data-planes.md`) puts panel-plane data on a
Parquet-partitioned store with a **persistent** DuckDB catalog, separate from
`storage.parquet.ParquetEvidenceStore` (the evidence plane, unchanged by this task). This
module tests the storage mechanics -- partition layout, overwrite/idempotency semantics,
and that the catalog genuinely persists across process boundaries (a `":memory:"` catalog
could not, by construction, ever pass the cross-process test below).

`tests/integration/panel/test_panel_store_performance_budget.py` covers the scale-driving
proofs (partition pruning, column projection, zero pydantic/sha256 on the read path) this
task's brief calls its most convincing acceptance evidence; this module stays focused on
correctness.

Panel data is a synthetic fixture generated at test time, never a checked-in `.parquet`/
`.duckdb` file (`scripts/verify_publication.py`'s `BLOCKED_SUFFIXES` publication gate would
fail CI on either suffix).
"""

from __future__ import annotations

import multiprocessing
from multiprocessing import Queue
from multiprocessing.synchronize import Barrier
from pathlib import Path

import duckdb
import pytest

from openalpha_cn.panel.store import ColumnSpec, PanelStorageError, PanelStore

_COLUMNS = (
    ColumnSpec("ts_code", "VARCHAR"),
    ColumnSpec("trade_date", "VARCHAR"),
    ColumnSpec("close", "DOUBLE"),
)


def _rows(*, closes: tuple[float, float] = (10.5, 22.5)) -> tuple[tuple[object, ...], ...]:
    # `.5`-ending values are exactly representable in binary float, so tests can compare
    # query results with plain `==` instead of `pytest.approx` -- unlike a multiplier
    # (e.g. `22.1 * 3`), which would introduce real, if tiny, binary-float rounding noise
    # unrelated to anything this test suite is actually checking.
    return (
        ("000001.SZ", "2024-01-02", closes[0]),
        ("000002.SZ", "2024-01-02", closes[1]),
    )


def test_write_partition_creates_a_single_parquet_file_under_dataset_slash_year(
    tmp_path: Path,
) -> None:
    store = PanelStore(tmp_path / "panel")

    ref = store.write_partition("prices_daily", 2024, _COLUMNS, _rows())

    expected_path = tmp_path / "panel" / "prices_daily" / "2024" / "data.parquet"
    assert ref.path == expected_path
    assert expected_path.is_file()
    assert ref.row_count == 2


def test_query_round_trips_the_written_rows_for_the_requested_columns(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    store.write_partition("prices_daily", 2024, _COLUMNS, _rows())

    result = store.query(
        "prices_daily", year=2024, columns=["ts_code", "close"], filters={"ts_code": "000002.SZ"}
    )

    assert result == [("000002.SZ", 22.5)]


def test_query_for_an_unwritten_partition_returns_empty_without_raising(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")

    assert store.query("prices_daily", year=2024, columns=["close"]) == []


def test_write_partition_is_idempotent_for_byte_identical_content(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")

    first = store.write_partition("prices_daily", 2024, _COLUMNS, _rows())
    written_at_first = first.path.stat().st_mtime_ns
    second = store.write_partition("prices_daily", 2024, _COLUMNS, _rows())

    assert second.content_hash == first.content_hash
    assert second.path == first.path
    # The file was never rewritten -- content-identical writes are true no-ops, exactly
    # `ParquetEvidenceStore.append`'s `if target.exists(): return target` short circuit.
    assert second.path.stat().st_mtime_ns == written_at_first
    _assert_catalog_row_count(store, "prices_daily", expected=1)


def test_write_partition_overwrites_a_partition_when_content_differs(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    store.write_partition("prices_daily", 2024, _COLUMNS, _rows(closes=(10.5, 22.5)))

    store.write_partition("prices_daily", 2024, _COLUMNS, _rows(closes=(11.5, 23.5)))

    result = store.query("prices_daily", year=2024, columns=["ts_code", "close"])
    assert sorted(result) == [("000001.SZ", 11.5), ("000002.SZ", 23.5)]
    # Overwrite, not append: exactly one file, one catalog row, per partition.
    _assert_catalog_row_count(store, "prices_daily", expected=1)


def test_write_partition_rejects_an_empty_column_list(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")

    with pytest.raises(PanelStorageError):
        store.write_partition("prices_daily", 2024, (), _rows())


def test_write_partition_rejects_an_empty_row_batch(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")

    with pytest.raises(PanelStorageError):
        store.write_partition("prices_daily", 2024, _COLUMNS, ())


def test_query_rejects_an_empty_column_list(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    store.write_partition("prices_daily", 2024, _COLUMNS, _rows())

    with pytest.raises(PanelStorageError):
        store.query("prices_daily", year=2024, columns=[])


def test_query_for_a_different_year_than_the_one_written_returns_empty(tmp_path: Path) -> None:
    """Distinct from `test_query_for_an_unwritten_partition_returns_empty_without_raising`:
    here the catalog file itself already exists (another partition was written), so this
    exercises the point-lookup-misses-inside-an-existing-catalog path, not the
    catalog-file-does-not-exist-at-all path."""
    store = PanelStore(tmp_path / "panel")
    store.write_partition("prices_daily", 2023, _COLUMNS, _rows())

    assert store.query("prices_daily", year=2024, columns=["close"]) == []


def test_profile_query_raises_when_the_catalog_has_never_been_written(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")

    with pytest.raises(PanelStorageError):
        store.profile_query("prices_daily", year=2024, columns=["close"])


def test_profile_query_raises_when_the_requested_partition_is_not_registered(
    tmp_path: Path,
) -> None:
    """As with `test_query_for_a_different_year_than_the_one_written_returns_empty`, the
    catalog already exists (from a different partition) -- this is the point-lookup-misses
    path, not the catalog-file-absent path `test_profile_query_raises_when_the_catalog_has_
    never_been_written` covers."""
    store = PanelStore(tmp_path / "panel")
    store.write_partition("prices_daily", 2023, _COLUMNS, _rows())

    with pytest.raises(PanelStorageError):
        store.profile_query("prices_daily", year=2024, columns=["close"])


def test_different_datasets_and_years_do_not_collide_in_the_catalog(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    store.write_partition("prices_daily", 2023, _COLUMNS, _rows(closes=(10.5, 22.5)))
    store.write_partition("prices_daily", 2024, _COLUMNS, _rows(closes=(11.5, 23.5)))
    store.write_partition("balancesheet", 2024, _COLUMNS, _rows(closes=(12.5, 24.5)))

    assert store.query("prices_daily", year=2023, columns=["close"]) == [(10.5,), (22.5,)]
    assert store.query("prices_daily", year=2024, columns=["close"]) == [(11.5,), (23.5,)]
    assert store.query("balancesheet", year=2024, columns=["close"]) == [(12.5,), (24.5,)]


def _assert_catalog_row_count(store: PanelStore, dataset: str, *, expected: int) -> None:
    with duckdb.connect(str(store.catalog_path), read_only=True) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM panel_partitions WHERE dataset = ?", [dataset]
        ).fetchone()
    assert count is not None
    assert count[0] == expected


# --- catalog persistence and concurrency across real OS processes -----------------------
#
# A `":memory:"` DuckDB catalog (the flaw `ParquetEvidenceStore.query` has today, see
# ADR-0002 finding F73) cannot, by construction, ever be visible to a second process --
# there is nothing to open. These tests run `PanelStore` in real, separate interpreters
# (matching the `multiprocessing` pattern `tests/integration/storage/test_migrations.py`
# already established for `SQLiteRunRepository`'s own concurrency proof) to demonstrate the
# catalog file genuinely persists, and that concurrent readers do not fail each other.


def _writer_worker(root_str: str, queue: Queue[tuple[str, str]]) -> None:
    store = PanelStore(Path(root_str))
    store.write_partition("prices_daily", 2024, _COLUMNS, _rows())
    queue.put(("writer", "ok"))


_RENDEZVOUS_TIMEOUT_SECONDS = 60.0
"""How long a reader waits for its siblings at the barrier before giving up.

`Barrier.wait()` with no timeout is unbounded, and a sibling that dies on the way to it (a
spawn failure, an import error, an OOM kill) leaves the survivors parked there forever. The
parent's own `queue.get(timeout=...)` does not save it: `multiprocessing`'s exit handler joins
every non-daemon child **without a timeout**, so the failing assertion is followed by a
`pytest` process that never returns -- observed once as a run still resident after 36 hours,
holding no repository file and burning no CPU. A `BrokenBarrierError` here becomes an ordinary
`fail:` outcome, which the assertions below already report by name. Generous rather than tight
because this is a deadlock bound, not a performance budget.
"""


def _reader_worker(
    root_str: str,
    barrier: Barrier,
    queue: Queue[tuple[str, str]],
    tag: str,
) -> None:
    try:
        barrier.wait(timeout=_RENDEZVOUS_TIMEOUT_SECONDS)
        store = PanelStore(Path(root_str))
        rows = store.query("prices_daily", year=2024, columns=["ts_code"])
        queue.put((tag, f"ok:{len(rows)}"))
    except Exception as error:
        queue.put((tag, f"fail:{type(error).__name__}:{error}"))


def test_catalog_persists_across_a_fresh_process_after_the_writer_exits(tmp_path: Path) -> None:
    """Process A writes and exits; process B, a fresh interpreter with no shared memory,
    opens the same `root` and reads the data back -- proof the catalog is a real file, not
    an in-memory structure scoped to one process's lifetime."""
    root = tmp_path / "panel"
    queue: multiprocessing.Queue[tuple[str, str]] = multiprocessing.Queue()
    writer = multiprocessing.Process(target=_writer_worker, args=(str(root), queue))
    writer.start()
    writer.join(timeout=15)
    assert writer.exitcode == 0, f"writer process crashed: exitcode={writer.exitcode}"
    assert queue.get(timeout=5) == ("writer", "ok")

    reader_queue: multiprocessing.Queue[tuple[str, str]] = multiprocessing.Queue()
    barrier = multiprocessing.Barrier(1)
    reader = multiprocessing.Process(
        target=_reader_worker, args=(str(root), barrier, reader_queue, "reader")
    )
    reader.start()
    reader.join(timeout=15)
    assert reader.exitcode == 0, f"reader process crashed: exitcode={reader.exitcode}"
    assert reader_queue.get(timeout=5) == ("reader", "ok:2")


def test_concurrent_read_only_queries_from_separate_processes_do_not_fail_each_other(
    tmp_path: Path,
) -> None:
    """Multiple processes may hold the catalog open `read_only=True` at the same time
    (verified against real DuckDB 1.5 concurrency behavior before writing this test -- a
    read-write connection excludes every other connection, in-process or not, but
    concurrent `read_only=True` connections across processes do not conflict)."""
    root = tmp_path / "panel"
    store = PanelStore(root)
    store.write_partition("prices_daily", 2024, _COLUMNS, _rows())

    barrier = multiprocessing.Barrier(3)
    queue: multiprocessing.Queue[tuple[str, str]] = multiprocessing.Queue()
    readers = [
        multiprocessing.Process(
            target=_reader_worker, args=(str(root), barrier, queue, f"reader{i}")
        )
        for i in range(3)
    ]
    for process in readers:
        process.start()
    try:
        outcomes = [queue.get(timeout=15) for _ in readers]
        for process in readers:
            process.join(timeout=15)
            assert not process.is_alive(), "a reader process hung"
            assert process.exitcode == 0, f"a reader process crashed: exitcode={process.exitcode}"

        for outcome in outcomes:
            assert outcome[1] == "ok:2", f"a concurrent reader failed: {outcome}"
    finally:
        # Every exit from the block above -- a failed assertion, a `queue.Empty` -- has to leave
        # no live child behind, or `multiprocessing`'s untimed exit-time join turns a red test
        # into a wedged interpreter. See `_RENDEZVOUS_TIMEOUT_SECONDS`.
        for process in readers:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
