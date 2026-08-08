"""Hardening tests closing three review findings against `PanelStore` (task 24, follow-up
to V2-P1-001's `6de598e`): a `dataset`-traversal write primitive, a `profile_query()` race
that contradicted its own docstring, and a `write_partition()` write-write conflict the
original concurrency testing never actually exercised.

Kept separate from `test_panel_store.py` (correctness) and
`test_panel_store_performance_budget.py` (the pruning/projection acceptance evidence) so
each file's docstring stays focused on one concern; see this task's report for the full
review writeup these tests close.

## Finding 1 -- `dataset` traversal

Pre-fix, `write_partition()` built `self.root / dataset / str(year)` and `profile_query()`
built `self.root / f".profile-{dataset}-{year}.json"` with no validation of `dataset` at
all. Both `Path`'s `/` operator and f-string interpolation happily accept a `dataset`
containing `..` (escapes `root` once the OS resolves the traversal) or an absolute path
(pathlib's documented behavior: joining an absolute path onto anything discards the left
operand entirely, silently, with no error). The traversal tests below reproduce both shapes
against both entry points and prove containment: after the call raises `PanelStorageError`,
`tmp_path` must contain nothing but the store's own `root` directory -- no sibling file or
directory ever got created.

## Finding 2 -- `profile_query()` is not actually concurrent-safe

The module docstring claims `query()`/`profile_query()` open the catalog `read_only=True`,
so any number of concurrent readers can run in parallel. True for `query()`. False, pre-fix,
for `profile_query()`: its profiling-output filename was derived only from
`(dataset, year)`, with no per-call uniqueness, so two concurrent callers for the same
partition would both write to and `unlink()` the same file. Reproduced below with 8 threads
(this codebase's actual concurrency primitive is `runtime/batch.py`'s `ThreadPoolExecutor`,
not separate OS processes) across 5 rounds, matching the reviewer's "3-6 failures per run
across 5 runs" measurement closely enough that a single lucky pass cannot hide a regression.

## Finding 3 -- the concurrent-write failure mode was documented wrongly

The pre-fix docstring claimed a losing concurrent writer gets `duckdb.IOException`. That is
true across OS processes (DuckDB's own file-locking rule, unchanged by this task) but was
never tested for same-process threads -- the regime `ThreadPoolExecutor`-based callers
actually create. Reproduced below:

- Same partition, many same-process threads: pre-fix, the catalog upsert's temp Parquet
  filename had no per-writer uniqueness, so `temporary.replace(target)` could raise a raw
  `FileNotFoundError` when one thread's rename stepped on another's temp file.
- Four different datasets, cold start (no catalog file yet), four same-process threads:
  pre-fix, DuckDB's own same-process MVCC (distinct from its cross-process file-locking
  rule) raised `duckdb.TransactionException: Catalog write-write conflict` for 3 of 4
  threads, reproducibly.

Both are fixed by a per-writer-unique temp filename (removes the `FileNotFoundError`
entirely) and an in-process `threading.Lock` serializing only the few-millisecond catalog
upsert (removes the `TransactionException` entirely, while leaving the expensive Parquet
`COPY` step unlocked and concurrent). See the module docstring's "Concurrency" section for
why cross-process writer races are a deliberately separate, still-open concern this
in-process lock cannot address.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
    return (
        ("000001.SZ", "2024-01-02", closes[0]),
        ("000002.SZ", "2024-01-02", closes[1]),
    )


def _assert_root_is_the_only_thing_under(tmp_path: Path, root: Path) -> None:
    """Containment proof: nothing but the store's own `root` directory exists directly
    under `tmp_path` -- no sibling file or directory the traversal could have produced."""
    assert set(tmp_path.iterdir()) == {root}


# --- Finding 1: `dataset` traversal ------------------------------------------------------


# All four traversal tests below assert on the error *message*, not just
# `PanelStorageError`'s type. That is deliberate, not decoration: `profile_query()`
# already raises `PanelStorageError` for an unrelated reason -- "no partition registered"
# -- for *any* dataset that was never written, traversal string or not, because the
# catalog lookup simply misses. A type-only assertion would pass "by accident" against the
# pre-fix code for exactly that reason (verified while writing these tests: it does). The
# message match forces the assertion to distinguish real boundary validation, which must
# fire before the catalog is ever consulted, from that pre-existing, unrelated error path.
_DATASET_VALIDATION_MESSAGE = "single, plain path segment"


def test_write_partition_rejects_an_empty_dataset(tmp_path: Path) -> None:
    """`_validate_dataset`'s empty-string branch is distinct from its single-path-segment
    branch (an empty string is trivially "not a single path segment" in spirit, but the
    function gives it its own clearer message) -- covered here rather than folded into the
    two traversal tests above, whose shared `_DATASET_VALIDATION_MESSAGE` does not match
    it."""
    store = PanelStore(tmp_path / "panel")

    with pytest.raises(PanelStorageError, match="dataset must not be empty"):
        store.write_partition("", 2024, _COLUMNS, _rows())


def test_write_partition_rejects_a_relative_dataset_that_escapes_root(tmp_path: Path) -> None:
    root = tmp_path / "panel"
    store = PanelStore(root)

    with pytest.raises(PanelStorageError, match=_DATASET_VALIDATION_MESSAGE):
        store.write_partition("../escaped", 2024, _COLUMNS, _rows())

    _assert_root_is_the_only_thing_under(tmp_path, root)


def test_write_partition_rejects_an_absolute_dataset(tmp_path: Path) -> None:
    root = tmp_path / "panel"
    store = PanelStore(root)
    escape_target = tmp_path / "escaped_abs"

    with pytest.raises(PanelStorageError, match=_DATASET_VALIDATION_MESSAGE):
        store.write_partition(str(escape_target), 2024, _COLUMNS, _rows())

    assert not escape_target.exists()
    _assert_root_is_the_only_thing_under(tmp_path, root)


def test_profile_query_rejects_a_relative_dataset_before_ever_checking_the_catalog(
    tmp_path: Path,
) -> None:
    """No prior `write_partition()` call here, deliberately: this dataset must never reach
    the catalog lookup (let alone the vulnerable f-string temp-filename construction) in
    the first place, on a completely fresh store where the catalog file does not even
    exist yet."""
    root = tmp_path / "panel"
    store = PanelStore(root)

    with pytest.raises(PanelStorageError, match=_DATASET_VALIDATION_MESSAGE):
        store.profile_query("../escaped", year=2024, columns=["close"])

    assert not (tmp_path / "escaped").exists()
    _assert_root_is_the_only_thing_under(tmp_path, root)


def test_profile_query_rejects_the_reviewers_exact_absolute_dataset_repro(
    tmp_path: Path,
) -> None:
    """Reproduces the reviewer's exact repro string, `dataset="/../../escaped2"`: pre-fix,
    once a matching catalog entry resolves, `self.root / f".profile-{dataset}-{year}.json"`
    lands at `tmp_path / "escaped2-2024.json"` -- a real profiling temp file written as a
    sibling of `root`, outside it entirely. Validated before the catalog lookup, so it is
    rejected the same way regardless of whether any partition was ever registered."""
    root = tmp_path / "panel"
    store = PanelStore(root)

    with pytest.raises(PanelStorageError, match=_DATASET_VALIDATION_MESSAGE):
        store.profile_query("/../../escaped2", year=2024, columns=["close"])

    assert not (tmp_path / "escaped2-2024.json").exists()
    _assert_root_is_the_only_thing_under(tmp_path, root)


# --- Finding 2: `profile_query()` concurrency --------------------------------------------


def test_profile_query_survives_eight_concurrent_threads_against_the_same_partition(
    tmp_path: Path,
) -> None:
    store = PanelStore(tmp_path / "panel")
    store.write_partition("prices_daily", 2024, _COLUMNS, _rows())

    for _round in range(5):
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(store.profile_query, "prices_daily", year=2024, columns=["ts_code"])
                for _ in range(8)
            ]
            # `.result()` re-raises inside the main thread; the assertion here is simply
            # that none of the 8 calls in this round raised at all.
            profiles = [future.result() for future in futures]

        assert len(profiles) == 8
        for profile in profiles:
            assert profile["Total Files Read"] == "1"


# --- Finding 3: `write_partition()` concurrency -------------------------------------------


def test_write_partition_survives_concurrent_threads_writing_the_same_partition(
    tmp_path: Path,
) -> None:
    store = PanelStore(tmp_path / "panel")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(
                store.write_partition,
                "prices_daily",
                2024,
                _COLUMNS,
                _rows(closes=(float(i), float(i) + 0.5)),
            )
            for i in range(8)
        ]
        for future in futures:
            future.result()

    # Overwrite semantics: which writer's content survives is deliberately undefined, but
    # the end state must still be exactly one file and one catalog row, with no crash.
    result = store.query("prices_daily", year=2024, columns=["ts_code"])
    assert len(result) == 2
    assert _catalog_row_count(store, "prices_daily") == 1


def test_write_partition_survives_four_concurrent_threads_writing_four_different_datasets(
    tmp_path: Path,
) -> None:
    """Reproduces the reviewer's cold-start repro: a brand-new store (no `catalog.duckdb`
    yet) with four threads each writing a *different* dataset. Looped 5 times, each with a
    fresh store, matching "reproducibly" -- a single lucky pass would not be convincing."""
    datasets = ["prices_daily", "balancesheet", "income_statement", "cashflow"]

    for round_index in range(5):
        store = PanelStore(tmp_path / f"panel-{round_index}")

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(store.write_partition, dataset, 2024, _COLUMNS, _rows())
                for dataset in datasets
            ]
            for future in futures:
                future.result()

        for dataset in datasets:
            result = store.query(dataset, year=2024, columns=["ts_code"])
            assert len(result) == 2


def _catalog_row_count(store: PanelStore, dataset: str) -> int:
    with duckdb.connect(str(store.catalog_path), read_only=True) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM panel_partitions WHERE dataset = ?", [dataset]
        ).fetchone()
    assert row is not None
    return int(row[0])
