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

import ast
import errno
import hashlib
import multiprocessing
import multiprocessing.process
import os
import sys
import threading
import time
from collections.abc import Sequence
from multiprocessing import Queue
from multiprocessing.synchronize import Barrier
from pathlib import Path
from typing import Any

import duckdb
import pytest

from openalpha_cn.panel.store import (
    ColumnSpec,
    PanelCatalogBusyError,
    PanelStorageError,
    PanelStore,
    PanelWriteConflictError,
    PartitionExpectation,
    PartitionWrite,
)

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

ctx = multiprocessing.get_context("spawn")
"""Builds every `Queue`/`Barrier`/`Process` below -- never the platform default (on Linux, `fork`
before Python 3.14 and `forkserver` from it): a default-context `Queue` handed to a spawn-context
`Process` can fail to pickle, and `fork` would make "real, separate interpreters" above untrue,
since a forked child copies its parent's memory instead of launching a fresh interpreter."""


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
    queue: multiprocessing.Queue[tuple[str, str]] = ctx.Queue()
    writer = ctx.Process(target=_writer_worker, args=(str(root), queue))
    writer.start()
    writer.join(timeout=15)
    assert writer.exitcode == 0, f"writer process crashed: exitcode={writer.exitcode}"
    assert queue.get(timeout=5) == ("writer", "ok")

    reader_queue: multiprocessing.Queue[tuple[str, str]] = ctx.Queue()
    barrier = ctx.Barrier(1)
    reader = ctx.Process(target=_reader_worker, args=(str(root), barrier, reader_queue, "reader"))
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

    barrier = ctx.Barrier(3)
    queue: multiprocessing.Queue[tuple[str, str]] = ctx.Queue()
    readers = [
        ctx.Process(target=_reader_worker, args=(str(root), barrier, queue, f"reader{i}"))
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


# --- the catalog is safe across processes (`V2-P6-028`) ---------------------------------
#
# Until `V2-P6-028` the module docstring's "Concurrency" section recorded, as a known and
# still-open limitation, that two processes touching the catalog at once -- two writers, or a
# reader beside a writer -- made the loser's `duckdb.connect` raise `duckdb.IOException`
# immediately. It cost the factor rebuild its parallelism (125 `openalpha factor build`
# commands had to run one after another) and let a research read running beside a write turn
# into a `PanelStorageError`, which the research driver records as a refused row. These tests
# run real, separate interpreters against one store, with the store generated here.

_READERS_SHARE = sys.platform != "win32"
"""Whether two readers can hold the catalog at once. Not on Windows, where `msvcrt.locking` has
no shared mode and the store takes both sides exclusively (see `panel.store._try_lock`); every
expectation below that depends on readers sharing branches on this rather than assuming it."""

_LOCK_DATASETS = ("alpha", "beta", "gamma", "delta")
_LOCK_YEARS = (2019, 2020, 2021, 2022, 2023, 2024)


def _dataset_rows(dataset: str, year: int) -> tuple[tuple[object, ...], ...]:
    """Content that differs per `(dataset, year)`, so a partition written under the wrong key
    could not hash equal to the serial run's."""
    offset = float(_LOCK_DATASETS.index(dataset) * 1000 + year)
    return tuple((f"{index:06d}.SZ", f"{year}-01-02", offset + index + 0.5) for index in range(200))


def _partition_writer_worker(
    root_str: str, dataset: str, barrier: Barrier, queue: Queue[tuple[str, str]]
) -> None:
    try:
        barrier.wait(timeout=_RENDEZVOUS_TIMEOUT_SECONDS)
        store = PanelStore(Path(root_str))
        for year in _LOCK_YEARS:
            store.write_partition(dataset, year, _COLUMNS, _dataset_rows(dataset, year))
        queue.put((dataset, "ok"))
    except Exception as error:
        queue.put((dataset, f"fail:{type(error).__name__}:{error}"))


def _catalog_rows(root: Path) -> list[tuple[object, ...]]:
    with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as connection:
        rows: list[tuple[object, ...]] = connection.execute(
            "SELECT dataset, year, relative_path, row_count, content_hash "
            "FROM panel_partitions ORDER BY dataset, year"
        ).fetchall()
    return rows


def _partition_bytes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.glob("*/*/data.parquet"))
    }


def _stop_all(processes: Sequence[multiprocessing.process.BaseProcess]) -> None:
    """Leave no live child behind on any exit; see `_RENDEZVOUS_TIMEOUT_SECONDS`."""
    for process in processes:
        if process.is_alive():
            process.kill()
            process.join(timeout=10)


def test_writers_in_separate_processes_all_land_and_match_a_serial_run(tmp_path: Path) -> None:
    """Four processes, released together at a barrier against a store with no catalog yet,
    each write six partitions of their own dataset. Every one succeeds, the catalog ends
    holding all twenty-four partitions, and catalog and files alike are what writing the same
    partitions one after another in one process produces."""
    root = tmp_path / "concurrent"
    barrier = ctx.Barrier(len(_LOCK_DATASETS))
    queue: multiprocessing.Queue[tuple[str, str]] = ctx.Queue()
    writers = [
        ctx.Process(target=_partition_writer_worker, args=(str(root), dataset, barrier, queue))
        for dataset in _LOCK_DATASETS
    ]
    for process in writers:
        process.start()
    try:
        outcomes = sorted(queue.get(timeout=120) for _ in writers)
        for process in writers:
            process.join(timeout=30)
            assert process.exitcode == 0, f"a writer process crashed: {process.exitcode}"
    finally:
        _stop_all(writers)

    assert outcomes == [(dataset, "ok") for dataset in sorted(_LOCK_DATASETS)]

    serial_root = tmp_path / "serial"
    serial = PanelStore(serial_root)
    for dataset in _LOCK_DATASETS:
        for year in _LOCK_YEARS:
            serial.write_partition(dataset, year, _COLUMNS, _dataset_rows(dataset, year))

    concurrent_rows = _catalog_rows(root)
    assert len(concurrent_rows) == len(_LOCK_DATASETS) * len(_LOCK_YEARS)
    assert concurrent_rows == _catalog_rows(serial_root)
    assert _partition_bytes(root) == _partition_bytes(serial_root)
    assert not list(root.glob("*/*/*.tmp")), "a writer left a staged temp file behind"


_BEFORE = (10.5, 22.5)
_AFTER = (11.5, 23.5)


def _toggling_writer_worker(
    root_str: str, year: int, barrier: Barrier, queue: Queue[tuple[str, str]]
) -> None:
    try:
        barrier.wait(timeout=_RENDEZVOUS_TIMEOUT_SECONDS)
        store = PanelStore(Path(root_str))
        for iteration in range(30):
            closes = _AFTER if iteration % 2 == 0 else _BEFORE
            store.write_partition("prices_daily", year, _COLUMNS, _rows(closes=closes))
        queue.put((f"writer{year}", "ok"))
    except Exception as error:
        queue.put((f"writer{year}", f"fail:{type(error).__name__}:{error}"))


def _observing_reader_worker(
    root_str: str, barrier: Barrier, queue: Queue[tuple[str, str]], tag: str
) -> None:
    try:
        barrier.wait(timeout=_RENDEZVOUS_TIMEOUT_SECONDS)
        store = PanelStore(Path(root_str))
        observed: set[tuple[int, tuple[object, ...]]] = set()
        for _ in range(60):
            for year in (2024, 2025):
                rows = store.query("prices_daily", year=year, columns=["close"])
                observed.add((year, tuple(row[0] for row in rows)))
        queue.put((tag, "ok:" + repr(sorted(observed))))
    except Exception as error:
        queue.put((tag, f"fail:{type(error).__name__}:{error}"))


def test_readers_in_other_processes_see_each_partition_before_or_after_a_write_and_never_fail(
    tmp_path: Path,
) -> None:
    """Two processes rewrite two partitions back and forth while three more read both of them
    in a loop. No read raises -- not `duckdb.IOException`, not `PanelStorageError` -- and every
    read answers one whole state of its partition: the rows before a write or the rows after
    it, never a mixture and never nothing."""
    root = tmp_path / "panel"
    store = PanelStore(root)
    for year in (2024, 2025):
        store.write_partition("prices_daily", year, _COLUMNS, _rows(closes=_BEFORE))

    barrier = ctx.Barrier(5)
    queue: multiprocessing.Queue[tuple[str, str]] = ctx.Queue()
    processes = [
        ctx.Process(target=_toggling_writer_worker, args=(str(root), year, barrier, queue))
        for year in (2024, 2025)
    ] + [
        ctx.Process(
            target=_observing_reader_worker, args=(str(root), barrier, queue, f"reader{index}")
        )
        for index in range(3)
    ]
    for process in processes:
        process.start()
    try:
        outcomes = dict(queue.get(timeout=180) for _ in processes)
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0, f"a process crashed: {process.exitcode}"
    finally:
        _stop_all(processes)

    failures = {tag: outcome for tag, outcome in outcomes.items() if not outcome.startswith("ok")}
    assert failures == {}
    allowed = {(year, state) for year in (2024, 2025) for state in (_BEFORE, _AFTER)}
    for tag in ("reader0", "reader1", "reader2"):
        observed = set(ast.literal_eval(outcomes[tag].removeprefix("ok:")))
        assert observed <= allowed, f"{tag} read a state no write produced: {observed - allowed}"


def _lock_holder_worker(root_str: str, mode: str, queue: Queue[str]) -> None:
    store = PanelStore(Path(root_str))
    access = (
        store._catalog_access.exclusive() if mode == "exclusive" else store._catalog_access.shared()
    )
    with access:
        queue.put("held")
        time.sleep(600)


def _start_holder(root: Path, mode: str) -> multiprocessing.process.BaseProcess:
    queue: multiprocessing.Queue[str] = ctx.Queue()
    holder = ctx.Process(target=_lock_holder_worker, args=(str(root), mode, queue))
    holder.start()
    try:
        assert queue.get(timeout=60) == "held"
    except BaseException:
        _stop_all([holder])
        raise
    return holder


def test_a_wait_past_the_bound_is_refused_by_name_and_is_not_a_storage_refusal(
    tmp_path: Path,
) -> None:
    """While another process holds the catalog for writing, a read here waits its bound and
    then refuses with `PanelCatalogBusyError`; while another process holds it for reading, so
    does a write. Each refusal names the catalog, the lock file and the side it needed. It is
    deliberately *not* a `PanelStorageError`: every handler of that type turns it into a refusal
    about the data, and a research driver records such a refusal as a row it never re-measures
    -- contention is not a fact about the data."""
    root = tmp_path / "panel"
    PanelStore(root).write_partition("prices_daily", 2024, _COLUMNS, _rows())
    bounded = PanelStore(root, catalog_lock_timeout=0.5)
    holder = _start_holder(root, "exclusive")
    try:
        started = time.monotonic()
        with pytest.raises(PanelCatalogBusyError) as read_refusal:
            bounded.query("prices_daily", year=2024, columns=["close"])
        waited = time.monotonic() - started
    finally:
        _stop_all([holder])
    holder = _start_holder(root, "shared")
    try:
        if _READERS_SHARE:
            # A read beside a reader is no contention at all.
            assert bounded.query("prices_daily", year=2024, columns=["close"]) == [
                (10.5,),
                (22.5,),
            ]
        else:
            # Windows has no shared lock: a read beside a reader waits like any other.
            with pytest.raises(PanelCatalogBusyError):
                bounded.query("prices_daily", year=2024, columns=["close"])
        with pytest.raises(PanelCatalogBusyError) as write_refusal:
            bounded.write_partition("prices_daily", 2025, _COLUMNS, _rows())
    finally:
        _stop_all([holder])

    assert 0.5 <= waited < 10.0
    assert not isinstance(read_refusal.value, PanelStorageError)
    # On Windows the write is refused at its first step, the shared read that decides whether
    # the content is already there, because there even that waits for the holder.
    write_side = "exclusive" if _READERS_SHARE else "shared"
    for refusal, side in ((read_refusal.value, "shared"), (write_refusal.value, write_side)):
        message = str(refusal)
        assert str(root / "catalog.duckdb") in message
        assert "catalog.duckdb.lock" in message
        assert side in message
        assert "0.5" in message
    assert not list(root.glob("prices_daily/2025/*")), "a refused write left a file behind"


@pytest.mark.parametrize("mode", ["exclusive", "shared"])
def test_a_process_killed_while_holding_the_catalog_lock_does_not_wedge_the_store(
    tmp_path: Path, mode: str
) -> None:
    """The lock is an OS advisory lock, so the kernel releases it when its holder's process
    ends however it ends. Proved with `kill` (SIGKILL on POSIX, `TerminateProcess` on Windows),
    which runs no `finally`, no `atexit` and no destructor: while the holder lives the write
    below is refused, and once it is dead the same write and a read both go through."""
    root = tmp_path / "panel"
    PanelStore(root).write_partition("prices_daily", 2024, _COLUMNS, _rows())
    holder = _start_holder(root, mode)
    try:
        with pytest.raises(PanelCatalogBusyError):
            PanelStore(root, catalog_lock_timeout=0.3).write_partition(
                "prices_daily", 2024, _COLUMNS, _rows(closes=_AFTER)
            )
        holder.kill()
        holder.join(timeout=30)
        assert not holder.is_alive()
    finally:
        _stop_all([holder])

    after = PanelStore(root, catalog_lock_timeout=10.0)
    after.write_partition("prices_daily", 2024, _COLUMNS, _rows(closes=_AFTER))
    assert after.query("prices_daily", year=2024, columns=["close"]) == [(11.5,), (23.5,)]


def _churning_reader_worker(root_str: str, queue: Queue[str]) -> None:
    """Hold the shared side for 0.2 s at a time and take it again at once, for a minute."""
    store = PanelStore(Path(root_str))
    announced = False
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with store._catalog_access.shared():
            if not announced:
                queue.put("holding")
                announced = True
            time.sleep(0.2)


@pytest.mark.skipif(
    not _READERS_SHARE,
    reason="Windows has no shared lock, so readers there never hold the catalog together and "
    "there is no stream of overlapping readers to starve a writer",
)
def test_a_writer_is_not_starved_by_readers_in_other_processes_that_never_all_let_go(
    tmp_path: Path,
) -> None:
    """Three processes take turns holding the catalog for reading so that at every moment at
    least one of them holds it. A shared lock is granted to any asker while only shared locks
    are held, so a writer that merely polled for the exclusive side would never find the
    catalog free and would wait out its whole bound. The gate in front of the lock is what lets
    it in: once the writer holds the gate, readers coming back for another turn queue behind it,
    and the ones already inside finish their 0.2 s and leave."""
    root = tmp_path / "panel"
    PanelStore(root).write_partition("prices_daily", 2024, _COLUMNS, _rows())
    queue: multiprocessing.Queue[str] = ctx.Queue()
    readers = [
        ctx.Process(target=_churning_reader_worker, args=(str(root), queue)) for _ in range(3)
    ]
    for process in readers:
        process.start()
    try:
        assert [queue.get(timeout=60) for _ in readers] == ["holding"] * 3
        time.sleep(0.5)
        writer = PanelStore(root, catalog_lock_timeout=20.0)
        started = time.monotonic()
        writer.write_partition("prices_daily", 2024, _COLUMNS, _rows(closes=_AFTER))
        waited = time.monotonic() - started
    finally:
        _stop_all(readers)

    assert waited < 20.0
    assert writer.query("prices_daily", year=2024, columns=["close"]) == [(11.5,), (23.5,)]


def test_the_bound_also_covers_a_wait_behind_a_writer_thread_in_the_same_process(
    tmp_path: Path,
) -> None:
    """One deadline per acquisition, spent across both locks: a reader queued behind a writer
    *thread* of its own process waits its bound there too, rather than unboundedly on the
    in-process side and only then boundedly on the file."""
    root = tmp_path / "panel"
    store = PanelStore(root, catalog_lock_timeout=0.3)
    store.write_partition("prices_daily", 2024, _COLUMNS, _rows())
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with store._catalog_access.exclusive():
            holding.set()
            release.wait(timeout=60)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert holding.wait(timeout=30)
        with pytest.raises(PanelCatalogBusyError, match="shared"):
            store.query("prices_daily", year=2024, columns=["close"])
    finally:
        release.set()
        thread.join(timeout=30)
    assert store.query("prices_daily", year=2024, columns=["close"]) == [(10.5,), (22.5,)]


@pytest.mark.parametrize("timeout", [-1.0, float("inf"), float("nan")])
def test_the_catalog_wait_must_be_a_finite_bound(tmp_path: Path, timeout: float) -> None:
    """An infinite (or meaningless) wait would bring back the silent hang the bound exists to
    name, so it is refused at construction rather than accepted and honoured. A `ValueError`:
    it is a malformed argument, not a fact about any store."""
    with pytest.raises(ValueError, match="catalog_lock_timeout") as refused:
        PanelStore(tmp_path / "panel", catalog_lock_timeout=timeout)
    assert not isinstance(refused.value, PanelStorageError)


_POSIX_PERMISSIONS = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permission bits, which root and Windows do not enforce the same way",
)


@_POSIX_PERMISSIONS
def test_a_reader_may_lock_a_lock_file_it_cannot_write(tmp_path: Path) -> None:
    """A lock file owned by another account, or a panel root this account may only read: the
    shared side needs only a descriptor, and `flock` takes a shared lock through a read-only one,
    so a read still works exactly as it did before the lock existed. Only the exclusive side
    needs to write the file -- and a writer needs the root writable anyway."""
    root = tmp_path / "panel"
    PanelStore(root).write_partition("prices_daily", 2024, _COLUMNS, _rows())
    lock_files = [root / "catalog.duckdb.lock", root / "catalog.duckdb.gate"]
    for path in lock_files:
        path.chmod(0o444)
    try:
        assert PanelStore(root).query("prices_daily", year=2024, columns=["close"]) == [
            (10.5,),
            (22.5,),
        ]
        with pytest.raises(PanelStorageError, match=r"catalog\.duckdb\.(gate|lock)"):
            PanelStore(root).write_partition("prices_daily", 2025, _COLUMNS, _rows())
    finally:
        for path in lock_files:
            path.chmod(0o644)


@_POSIX_PERMISSIONS
def test_a_lock_file_that_cannot_be_created_is_refused_by_name(tmp_path: Path) -> None:
    """A root this account may only read, holding a catalog written before the lock existed:
    the lock file cannot be created, and a read without it could meet a writer of another
    account mid-write. Refused naming the lock file and the cause, instead of the bare `OSError`
    the `open` raises."""
    root = tmp_path / "panel"
    PanelStore(root).write_partition("prices_daily", 2024, _COLUMNS, _rows())
    for name in ("catalog.duckdb.lock", "catalog.duckdb.gate"):
        (root / name).unlink()
    root.chmod(0o555)
    try:
        with pytest.raises(PanelStorageError, match=r"catalog\.duckdb\.(gate|lock)") as refused:
            PanelStore(root).query("prices_daily", year=2024, columns=["close"])
    finally:
        root.chmod(0o755)
    assert "Permission denied" in str(refused.value) or "EACCES" in str(refused.value)


def test_a_read_only_filesystem_needs_no_lock_because_nothing_can_write_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a read-only filesystem (`EROFS`) the lock file cannot be created and no process can
    write the catalog either, so a reader goes ahead without one. Simulated at `os.open`: the
    lock files' create fails with `EROFS` and they do not exist."""
    root = tmp_path / "panel"
    PanelStore(root).write_partition("prices_daily", 2024, _COLUMNS, _rows())
    real_open = os.open

    def read_only_filesystem(path: str | os.PathLike[str], flags: int, *args: Any) -> int:
        if str(path).endswith((".duckdb.lock", ".duckdb.gate")):
            if flags & os.O_CREAT:
                raise OSError(errno.EROFS, os.strerror(errno.EROFS), str(path))
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(path))
        return real_open(path, flags, *args)

    monkeypatch.setattr(os, "open", read_only_filesystem)
    store = PanelStore(root)

    assert store.query("prices_daily", year=2024, columns=["close"]) == [(10.5,), (22.5,)]
    with pytest.raises(PanelStorageError, match=rf"errno {errno.EROFS}\b"):
        store.write_partition("prices_daily", 2025, _COLUMNS, _rows())


# --- a group write, compare-and-swap (`V2-P6-028`) ---------------------------------------------


def _group(
    *, closes: tuple[float, float], expected: dict[tuple[str, int], str | None] | None = None
) -> list[PartitionWrite]:
    return [
        PartitionWrite(
            dataset=dataset,
            year=2024,
            columns=_COLUMNS,
            rows=_rows(closes=closes),
            expected=(
                None
                if expected is None
                else PartitionExpectation(content_hash=expected[(dataset, 2024)])
            ),
        )
        for dataset in ("observations", "manifests")
    ]


def test_a_group_write_lands_every_partition_and_hands_back_one_ref_each(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    nothing_stored: dict[tuple[str, int], str | None] = {
        ("observations", 2024): None,
        ("manifests", 2024): None,
    }

    refs = store.write_partitions(_group(closes=_BEFORE, expected=nothing_stored))

    assert [(ref.dataset, ref.year, ref.row_count) for ref in refs] == [
        ("observations", 2024, 2),
        ("manifests", 2024, 2),
    ]
    for ref in refs:
        assert store.query(ref.dataset, year=2024, columns=["close"]) == [(10.5,), (22.5,)]
        assert store.partition_content_hash(ref.dataset, 2024) == ref.content_hash


def test_a_group_write_whose_base_moved_writes_nothing_and_names_what_moved(
    tmp_path: Path,
) -> None:
    """Two writers plan against one stored state; the first commits. The second's expectation
    -- the content it read when it planned -- no longer holds for either partition, so it writes
    nothing at all and raises `PanelWriteConflictError`, which is not a `PanelStorageError`."""
    store = PanelStore(tmp_path / "panel")
    store.write_partitions(_group(closes=_BEFORE))
    planned_against = {
        (dataset, 2024): store.partition_content_hash(dataset, 2024)
        for dataset in ("observations", "manifests")
    }
    store.write_partitions(_group(closes=_AFTER, expected=planned_against))
    after_first = {key: store.partition_content_hash(*key) for key in planned_against}

    with pytest.raises(PanelWriteConflictError) as conflict:
        store.write_partitions(_group(closes=(12.5, 24.5), expected=planned_against))

    assert not isinstance(conflict.value, PanelStorageError)
    assert conflict.value.targets == ("manifests@2024", "observations@2024")
    assert {key: store.partition_content_hash(*key) for key in planned_against} == after_first
    assert store.query("observations", year=2024, columns=["close"]) == [(11.5,), (23.5,)]
    assert not list((tmp_path / "panel").glob("*/*/*.tmp"))


def test_a_group_write_expecting_an_absent_partition_conflicts_with_one_that_appeared(
    tmp_path: Path,
) -> None:
    """`content_hash=None` means "nothing was stored when I planned"; a partition someone else
    wrote since is a moved base like any other -- and the group's other, untouched target is not
    written either."""
    store = PanelStore(tmp_path / "panel")
    store.write_partition("manifests", 2024, _COLUMNS, _rows())

    with pytest.raises(PanelWriteConflictError) as conflict:
        store.write_partitions(
            _group(
                closes=_AFTER,
                expected={("observations", 2024): None, ("manifests", 2024): None},
            )
        )

    assert conflict.value.targets == ("manifests@2024",)
    assert store.registered_years("observations") == ()


def test_a_group_write_refuses_two_writes_to_one_partition(tmp_path: Path) -> None:
    store = PanelStore(tmp_path / "panel")
    twice = [*_group(closes=_BEFORE)[:1], *_group(closes=_AFTER)[:1]]
    with pytest.raises(PanelStorageError, match="observations@2024"):
        store.write_partitions(twice)
    assert store.registered_years("observations") == ()


def test_a_reader_inside_one_read_hold_sees_a_group_write_whole(tmp_path: Path) -> None:
    """`PanelStore.reading()` is the reader's half: every catalog read inside it is one shared
    hold, so a group write lands wholly before or wholly after it."""
    store = PanelStore(tmp_path / "panel")
    store.write_partitions(_group(closes=_BEFORE))
    with store.reading():
        first = store.query("observations", year=2024, columns=["close"])
        second = store.query("manifests", year=2024, columns=["close"])
    assert first == second == [(10.5,), (22.5,)]
