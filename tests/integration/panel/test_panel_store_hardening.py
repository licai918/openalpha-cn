"""Hardening tests closing five review findings against `PanelStore`: a `dataset`-traversal
write primitive, a `profile_query()` race that contradicted its own docstring, a
`write_partition()` write-write conflict the original concurrency testing never actually
exercised (all three from task 24, follow-up to V2-P1-001's `6de598e`), unescaped SQL
identifier interpolation at three call sites (task 25's review, follow-up to V2-P1-002's
`cb9e8f4`), and a non-atomic write window that reported `ready` over a partition neither
catalog row described (the P1 stage review, follow-up to `676cba3`).

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

## Finding 4 -- unescaped SQL identifiers and an open-ended column type

V2-P1-002 argued that the *columnar batch* path could not express a hostile column name and
deferred fixing `store.py` itself. Its review disproved the premise (a column object that
never ran `PanelColumn.__post_init__` reached the store's DDL), so the store is fixed here
too. Three sites interpolated caller strings into SQL, all three reproduced live against
`cb9e8f4`:

- `write_partition()`'s DDL, `f'"{column.name}" {column.duckdb_type}'`. The *type* carried no
  quoting at all, so `ColumnSpec("close", "DOUBLE); ATTACH '<path>' AS evil; CREATE TABLE
  evil.pwned(x INTEGER")` created an attacker-named DuckDB file on disk.
- `_build_scan_sql()`'s `SELECT` projection, `f'"{name}"'`. A projected name carrying a
  statement break ran `COPY (SELECT 42) TO '<path>'` and landed a file on disk. (DuckDB only
  allows prepared parameters in the *last* statement of a script, so the injected statements
  have to sit ahead of the surviving `read_parquet(?)` -- which they can.)
- `_build_scan_sql()`'s `WHERE` keys, `f'"{key}" = ?'`. A key of
  `ts_code" = 'nope' OR TRUE OR "ts_code` neutralised the filter: `query()` returned every
  row of the partition instead of the single row the caller asked for.

The fix is `_quote_identifier()` at all three sites plus a closed `DUCKDB_COLUMN_TYPES` set
enforced in `ColumnSpec.__post_init__`; see `store.py`'s "SQL identifier and type handling"
section for why escaping (not a whitelist) is the right mechanism for names at this layer.
The tests below assert on observable side effects -- whether a file landed, and whether a
filtered query returned rows it should not have -- rather than only on an exception type,
because every one of these attacks *succeeded silently* before the fix.

## Finding 5 -- the write window failed open

`write_partition()` renamed the new Parquet into place and *then* upserted the catalog, with
nothing joining the two. Interrupting the second step (measured with an unwritable catalog)
left the new bytes on disk, the old row in `panel_partitions`, and the old coverage record
next to it -- and readiness compares those two catalog rows to each other, so two equally
stale facts agreed and the verdict was `ready` with `issues == []` over a partition neither of
them described. The order is now COPY -> upsert -> rename, which turns that interruption into
a clean no-op and turns the one remaining window (a `rename(2)` after a committed row) into a
disagreement readiness can see. The section below the SQL tests has the measurements.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from openalpha_cn.panel.catalog import (
    DateCoverage,
    FieldCoverage,
    PartitionCoverage,
    ReadinessRequirement,
)
from openalpha_cn.panel.store import (
    DUCKDB_COLUMN_TYPES,
    ColumnSpec,
    PanelStorageError,
    PanelStore,
)

_COLUMNS = (
    ColumnSpec("ts_code", "VARCHAR"),
    ColumnSpec("trade_date", "VARCHAR"),
    ColumnSpec("close", "DOUBLE"),
)

_DATASET = "prices_daily"
_FROZEN = datetime(2024, 6, 28, 11, 22, 33, tzinfo=UTC)
_AS_OF = datetime(2024, 1, 4, 12, 0, tzinfo=UTC)


def _rows(*, closes: tuple[float, float] = (10.5, 22.5)) -> tuple[tuple[object, ...], ...]:
    return (
        ("000001.SZ", "2024-01-02", closes[0]),
        ("000002.SZ", "2024-01-02", closes[1]),
    )


def _three_rows() -> tuple[tuple[object, ...], ...]:
    """Different content *and* a different row count, so a catalog/disk disagreement is
    visible in both the content hash and the Parquet footer."""
    return (*_rows(closes=(99.5, 88.5)), ("000003.SZ", "2024-01-02", 77.5))


def _frozen_clock() -> datetime:
    return _FROZEN


def _coverage(*, row_count: int = 2) -> PartitionCoverage:
    """A coverage record for the partition `_rows()` (or, at `row_count=3`, `_three_rows()`)
    writes."""
    return PartitionCoverage(
        dataset=_DATASET,
        year=2024,
        provider_id="tushare",
        kind="daily",
        schema_version="panel-batch/v1",
        batch_digest="sha256:" + "1" * 64,
        as_of=_AS_OF,
        fetched_at=_AS_OF,
        row_count=row_count,
        date_timezone="Asia/Shanghai",
        last_event_time=datetime(2024, 1, 2, 7, 0, tzinfo=UTC),
        max_available_time=datetime(2024, 1, 2, 8, 30, tzinfo=UTC),
        revised_row_count=0,
        subjects=("000001.SZ", "000002.SZ"),
        fields=(FieldCoverage(name="close", kind="float"),),
        dates=(DateCoverage(event_date=date(2024, 1, 2), row_count=row_count),),
    )


def _refuse_rename(self: Path, target: object) -> Path:
    """Stand-in for the one syscall between a committed catalog row and the file it names.

    That window cannot be closed from inside this process -- a rename and a DuckDB commit are
    two systems -- so it is simulated rather than raced for: the tests that use this are about
    which *direction* the window fails in, and a real race would test the scheduler.
    """
    raise OSError("rename interrupted")


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


# --- Finding 4: SQL identifier interpolation ---------------------------------------------


def test_a_hostile_duckdb_type_cannot_be_carried_by_a_column_spec_at_all(tmp_path: Path) -> None:
    """The reviewer's DDL repro. A `duckdb_type` is not an identifier, so no amount of
    quoting makes an arbitrary string inert -- the type has to come from a closed set, and
    the set is enforced where a `ColumnSpec` is built rather than where it is used, so an
    invalid spec cannot even be handed to `write_partition()`."""
    pwned = tmp_path / "PWNED.duckdb"
    hostile_type = f"DOUBLE); ATTACH '{pwned}' AS evil; CREATE TABLE evil.pwned(x INTEGER"

    with pytest.raises(PanelStorageError, match="unsupported DuckDB type"):
        ColumnSpec("close", hostile_type)

    assert not pwned.exists()


def test_every_type_the_ingest_seam_emits_is_in_the_stores_closed_type_set() -> None:
    """`panel_ingest.PANEL_DUCKDB_TYPES` and `store.DUCKDB_COLUMN_TYPES` are two hand-written
    lists that have to agree, or `write_panel_batch()` raises on a perfectly ordinary batch.
    Same drift risk, same shape of fix, as the duplicated `dataset` rule."""
    from openalpha_cn.panel_ingest import PANEL_DUCKDB_TYPES

    assert set(PANEL_DUCKDB_TYPES.values()) <= DUCKDB_COLUMN_TYPES


def test_a_quote_bearing_column_name_becomes_a_literal_name_at_all_three_sql_sites(
    tmp_path: Path,
) -> None:
    """The positive half of the escaping claim, exercising DDL, projection and filter key
    with the same string in one round trip: the injection payload survives as the *name* of
    a real Parquet column, and the `ATTACH` it contains never runs.

    Asserting the write *succeeds* is the point. A whitelist would have rejected this name
    and proven nothing about escaping; escaping makes the name data, which is what a storage
    primitive owes a caller that legitimately has an awkward column name."""
    pwned = tmp_path / "PWNED-ddl.duckdb"
    hostile = f"close\" DOUBLE); ATTACH '{pwned}' AS evil; CREATE TABLE evil.pwned(x INTEGER"
    store = PanelStore(tmp_path / "panel")

    store.write_partition(
        "prices_daily",
        2024,
        [ColumnSpec("ts_code", "VARCHAR"), ColumnSpec(hostile, "DOUBLE")],
        [("000001.SZ", 10.5), ("600000.SH", 22.5)],
    )

    assert not pwned.exists()
    assert store.query("prices_daily", year=2024, columns=[hostile]) == [(10.5,), (22.5,)]
    assert store.query("prices_daily", year=2024, columns=["ts_code"], filters={hostile: 10.5}) == [
        ("000001.SZ",)
    ]


def test_a_hostile_projection_name_cannot_run_a_second_statement(tmp_path: Path) -> None:
    """The reviewer's `SELECT`-list repro. Pre-fix this landed a real CSV file on disk while
    `query()` returned normally; post-fix the whole payload is one (nonexistent) column
    name, so DuckDB's binder rejects it and nothing is written."""
    pwned = tmp_path / "PWNED-projection.csv"
    store = PanelStore(tmp_path / "panel")
    store.write_partition(
        "prices_daily", 2024, [ColumnSpec("ts_code", "VARCHAR")], [("000001.SZ",)]
    )
    partition = store.root / "prices_daily" / "2024" / "data.parquet"
    hostile = (
        f"ts_code\" FROM read_parquet('{partition}'); "
        f"COPY (SELECT 42 AS x) TO '{pwned}' (FORMAT CSV); "
        'SELECT "ts_code'
    )

    with pytest.raises(duckdb.Error) as failure:
        store.query("prices_daily", year=2024, columns=[hostile])

    assert "Referenced column" in str(failure.value)
    assert not pwned.exists()


def test_a_hostile_filter_key_cannot_neutralise_the_where_clause(tmp_path: Path) -> None:
    """The reviewer's `WHERE` repro. This one never wrote a file -- it silently returned
    rows the caller had filtered out, which is why the assertion below compares the injected
    query against the honest one rather than only asserting that something was raised."""
    store = PanelStore(tmp_path / "panel")
    store.write_partition(
        "prices_daily",
        2024,
        [ColumnSpec("ts_code", "VARCHAR")],
        [("000001.SZ",), ("600000.SH",)],
    )
    honest = store.query(
        "prices_daily", year=2024, columns=["ts_code"], filters={"ts_code": "000001.SZ"}
    )
    assert honest == [("000001.SZ",)]
    hostile_key = "ts_code\" = 'nope' OR TRUE OR \"ts_code"

    with pytest.raises(duckdb.Error) as failure:
        store.query(
            "prices_daily", year=2024, columns=["ts_code"], filters={hostile_key: "000001.SZ"}
        )

    assert "Referenced column" in str(failure.value)


@pytest.mark.parametrize("name", ["", "clo\x00se"])
def test_an_identifier_escaping_cannot_rescue_is_refused_rather_than_handed_to_duckdb(
    tmp_path: Path, name: str
) -> None:
    """An empty name and a NUL-bearing one are the two inputs `"` -doubling does not make
    safe: DuckDB rejects a zero-length delimited identifier outright, and stops parsing a
    quoted identifier at a NUL so the closing quote is never seen. Neither is exploitable
    (both are parse errors), but both are refused here so callers get a `PanelStorageError`
    naming the problem instead of a raw `duckdb.ParserException`."""
    store = PanelStore(tmp_path / "panel")

    with pytest.raises(PanelStorageError, match="column name must not"):
        store.write_partition("prices_daily", 2024, [ColumnSpec(name, "DOUBLE")], [(1.0,)])


def test_write_partition_rejects_a_dataset_with_surrounding_whitespace(tmp_path: Path) -> None:
    """`" prices_daily "` is a legal directory name, so accepting it would create a
    partition directory indistinguishable from `prices_daily` in every log line; stripping
    it would make the caller's string and the path on disk quietly different. Refused, in
    step with `domain/panel_batch.py::validate_panel_dataset`."""
    root = tmp_path / "panel"
    store = PanelStore(root)

    with pytest.raises(PanelStorageError, match="leading or trailing whitespace"):
        store.write_partition(" prices_daily ", 2024, _COLUMNS, _rows())

    _assert_root_is_the_only_thing_under(tmp_path, root)


# --- Finding 5 (P1 review): the write ordering and its residual window ---------------------
#
# `write_partition()` touches two systems that share no transaction: a Parquet file and a
# DuckDB row. The question was never whether there is a window, only which way it fails.
# Pre-fix the order was rename-then-upsert, and it failed *open* -- measured by making
# `catalog.duckdb` read-only, which stands in for a read-only mount, a full disk, a kill
# between the two steps, and the cross-process writer race this module deliberately does not
# solve:
#
#     write raises IOException
#     readiness  : ready, issues == []
#     coverage   : row_count == 2
#     query      : three rows -- the new content
#
# The Parquet had been swapped and neither catalog row had; readiness compares those two rows
# to each other, so two equally stale facts agreed and cleared the partition. The order is now
# COPY -> upsert -> rename, and these tests pin what that buys and what it does not.


def _fail_catalog_writes(
    monkeypatch: pytest.MonkeyPatch, store: PanelStore, error: BaseException
) -> None:
    """Make every read-*write* connect to this store's catalog raise, reads untouched.

    A monkeypatch rather than `chmod`, because the reproduction has to be the same on every
    platform and under every uid -- a read-only mode bit does not stop a process running as
    root, so the chmod version of this test would pass vacuously in half the environments
    that matter.
    """
    real_connect = duckdb.connect

    def connect(database: object = ":memory:", *args: object, **kwargs: object) -> object:
        if str(database) == str(store.catalog_path) and not kwargs.get("read_only", False):
            raise error
        return real_connect(database, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(duckdb, "connect", connect)


def _requirement() -> ReadinessRequirement:
    return ReadinessRequirement(
        dataset=_DATASET,
        as_of=_AS_OF,
        years=(2024,),
        required_dates=None,
        required_subjects=None,
        required_fields=None,
        max_staleness=None,
    )


def _readiness(store: PanelStore) -> tuple[str, list[str]]:
    verdict = store.assess_readiness(_requirement())
    return verdict.state, [issue.code for issue in verdict.issues]


def test_a_failing_catalog_upsert_leaves_the_partition_and_the_verdict_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Critical, in the shape it was measured in. With the catalog unwritable, the whole
    write must be a no-op: the store keeps the previous content *and* keeps describing it
    correctly, rather than serving new bytes under an old description.

    `ready` is the right answer here and was the wrong one before. It is the same word for a
    different fact: the catalog, the coverage record and the file all still describe the
    two-row write that really is on disk.
    """
    root = tmp_path / "panel"
    store = PanelStore(root, clock=_frozen_clock)
    store.write_partition(_DATASET, 2024, _COLUMNS, _rows())
    store.record_coverage(_coverage())

    _fail_catalog_writes(monkeypatch, store, duckdb.IOException("catalog is read-only"))
    with pytest.raises(duckdb.IOException):
        store.write_partition(_DATASET, 2024, _COLUMNS, _three_rows())
    monkeypatch.undo()

    assert _readiness(store) == ("ready", [])
    stored = store.read_coverage(_DATASET, 2024)
    assert stored is not None and stored.row_count == 2
    assert store.query(_DATASET, year=2024, columns=["ts_code", "close"]) == [
        ("000001.SZ", 10.5),
        ("000002.SZ", 22.5),
    ]
    assert not list((root / _DATASET / "2024").glob("*.tmp")), (
        "the staged Parquet must not be left behind when the catalog upsert fails"
    )


def test_a_first_write_interrupted_at_the_rename_blocks_instead_of_reporting_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The residual window, shape one. The catalog row commits and the rename does not, so
    the catalog advertises a partition with no file -- `partition_file_missing`, which is
    exactly the fault code that state deserves and which blocks."""
    root = tmp_path / "panel"
    store = PanelStore(root, clock=_frozen_clock)

    monkeypatch.setattr(Path, "replace", _refuse_rename)
    with pytest.raises(OSError, match="rename interrupted"):
        store.write_partition(_DATASET, 2024, _COLUMNS, _rows())
    monkeypatch.undo()

    assert store.registered_years(_DATASET) == (2024,)
    assert not (root / _DATASET / "2024" / "data.parquet").exists()
    assert _readiness(store) == ("blocked", ["partition_file_missing"])


def test_the_missing_file_reaches_a_direct_reader_as_this_modules_own_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same window, seen from `query()` and `profile_query()` instead of from readiness.

    Every other refusal on this module's read path is a `PanelStorageError`, and the scan was
    the one that was not: it answered with a bare `duckdb.IOException` carrying the partition's
    absolute path. Not a correctness hole -- `read_if_ready` refuses this state before it scans,
    and that is the only read the gate supports -- but `query()` is public, and
    `cli._panel_command` turns an exception it does not recognise into `internal_error`
    (exit 5). "The CLI has a defect" for what readiness right above calls
    `partition_file_missing` is the same misclassification `SuspensionError` was added to
    `cli._PANEL_WRITE_REFUSALS` to close.

    DuckDB's own message is withheld rather than wrapped, so the store's filesystem layout is
    not printed to whatever is reading the error; the chained `__cause__` still carries it for
    a debugger.
    """
    store = PanelStore(tmp_path / "panel", clock=_frozen_clock)
    monkeypatch.setattr(Path, "replace", _refuse_rename)
    with pytest.raises(OSError, match="rename interrupted"):
        store.write_partition(_DATASET, 2024, _COLUMNS, _rows())
    monkeypatch.undo()

    for read in (store.query, store.profile_query):
        with pytest.raises(PanelStorageError, match="could not be scanned") as raised:
            read(_DATASET, year=2024, columns=["ts_code"])
        assert "partition_file_missing" in str(raised.value)
        assert str(tmp_path) not in str(raised.value)
        assert isinstance(raised.value.__cause__, duckdb.Error)


def test_a_rewrite_interrupted_at_the_rename_blocks_as_coverage_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The residual window, shape two -- and the direction the whole reorder exists for.

    The catalog's `panel_partitions.content_hash` now names the new write while the coverage
    record still names the old one, so the two facts readiness compares *disagree* and it
    blocks. Under the pre-fix order the same interruption left both of them naming the old
    write while the file held the new one: two stale facts in perfect agreement, and `ready`.
    """
    root = tmp_path / "panel"
    store = PanelStore(root, clock=_frozen_clock)
    store.write_partition(_DATASET, 2024, _COLUMNS, _rows())
    store.record_coverage(_coverage())

    monkeypatch.setattr(Path, "replace", _refuse_rename)
    with pytest.raises(OSError, match="rename interrupted"):
        store.write_partition(_DATASET, 2024, _COLUMNS, _three_rows())
    monkeypatch.undo()

    assert _readiness(store) == ("blocked", ["coverage_stale"])


def test_retrying_the_interrupted_write_rewrites_rather_than_reporting_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hazard the reorder introduces, and the check that closes it.

    After the interruption above, the catalog row already carries the *new* content hash. A
    retry of the same write therefore matches on the hash alone -- so an idempotency check
    built only on the hash would return "already written", turning a transient, blocked window
    into a permanent one while reporting success. `_reusable_partition` reconciles the
    catalog's row count against the Parquet footer, which disagrees, so the retry is the write
    it was meant to be.
    """
    root = tmp_path / "panel"
    store = PanelStore(root, clock=_frozen_clock)
    store.write_partition(_DATASET, 2024, _COLUMNS, _rows())
    store.record_coverage(_coverage())

    monkeypatch.setattr(Path, "replace", _refuse_rename)
    with pytest.raises(OSError, match="rename interrupted"):
        store.write_partition(_DATASET, 2024, _COLUMNS, _three_rows())
    monkeypatch.undo()

    # Precondition for the hazard: the catalog already believes the new write happened.
    assert store.query(_DATASET, year=2024, columns=["ts_code"]) == [
        ("000001.SZ",),
        ("000002.SZ",),
    ]

    reference = store.write_partition(_DATASET, 2024, _COLUMNS, _three_rows())

    assert reference.row_count == 3
    assert store.query(_DATASET, year=2024, columns=["ts_code"]) == [
        ("000001.SZ",),
        ("000002.SZ",),
        ("000003.SZ",),
    ]


def test_a_byte_identical_rewrite_is_still_a_true_no_op(tmp_path: Path) -> None:
    """The other side of the check above: when the catalog row and the file do agree, nothing
    is rewritten. Measured on the file's own mtime and size rather than on the return value,
    because the return value would look identical either way."""
    root = tmp_path / "panel"
    store = PanelStore(root, clock=_frozen_clock)
    store.write_partition(_DATASET, 2024, _COLUMNS, _rows())
    target = root / _DATASET / "2024" / "data.parquet"
    before = target.stat()

    reference = store.write_partition(_DATASET, 2024, _COLUMNS, _rows())
    after = target.stat()

    assert reference.row_count == 2
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)


def test_a_partition_file_truncated_behind_the_stores_back_is_rewritten_not_waved_through(
    tmp_path: Path,
) -> None:
    """The footer reconciliation earns its keep here too: a partition whose file has been
    truncated to half its rows no longer satisfies the no-op branch, so re-writing the same
    content repairs it instead of answering "already written"."""
    root = tmp_path / "panel"
    store = PanelStore(root, clock=_frozen_clock)
    store.write_partition(_DATASET, 2024, _COLUMNS, _three_rows())
    target = root / _DATASET / "2024" / "data.parquet"
    with duckdb.connect(":memory:") as connection:
        connection.execute(
            "COPY (SELECT * FROM read_parquet(?) LIMIT 1) TO ? (FORMAT PARQUET)",
            [str(target), str(target)],
        )
    assert len(store.query(_DATASET, year=2024, columns=["ts_code"])) == 1

    store.write_partition(_DATASET, 2024, _COLUMNS, _three_rows())

    assert len(store.query(_DATASET, year=2024, columns=["ts_code"])) == 3


def test_a_file_that_only_looks_like_parquet_is_rewritten_rather_than_trusted(
    tmp_path: Path,
) -> None:
    """The gap between the store's two on-disk checks -- **closed** -- and why both paths read
    the row count rather than inferring it.

    `_looks_like_parquet` reads eight bytes, so a file that begins and ends with `PAR1` and
    holds nothing usable in between passes it. This test used to record the consequence as a
    gap: readiness said `ready`, because every fact it had agreed, and the refusal came only
    from `read_if_ready` wrapping the scan that then failed. P2's product acceptance measured
    what that gap costs on a *well-formed* replacement rather than on this rubble -- one row
    appended to a real `stock_basic` partition, `panel doctor` reporting the catalog's count
    over a file one row longer, `data-check` `CLEARED` -- so `_parquet_row_count` now runs on
    the read path too and this file is refused before anything scans it.

    Its `None` answer is what both paths turn on, and it means the same thing in both: DuckDB
    cannot say how many rows these bytes hold, and not a confident yes is a no. The write's
    no-op branch declines and repairs the partition; readiness reports
    `partition_row_count_mismatch` and blocks.
    """
    root = tmp_path / "panel"
    store = PanelStore(root, clock=_frozen_clock)
    store.write_partition(_DATASET, 2024, _COLUMNS, _three_rows())
    store.record_coverage(_coverage(row_count=3))
    target = root / _DATASET / "2024" / "data.parquet"
    target.write_bytes(b"PAR1" + b"\x00" * 64 + b"PAR1")

    # The eight-byte check is satisfied and the footer is not: the gate refuses on the footer.
    assert _readiness(store) == ("blocked", ["partition_row_count_mismatch"])
    outcome = store.read_if_ready(_requirement(), year=2024, columns=["ts_code"])
    assert outcome.rows_or_none is None

    store.write_partition(_DATASET, 2024, _COLUMNS, _three_rows())

    assert _readiness(store) == ("ready", [])
    assert len(store.query(_DATASET, year=2024, columns=["ts_code"])) == 3
