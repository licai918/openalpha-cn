"""`dataset/year/`-partitioned Parquet storage with a persistent DuckDB catalog.

## Why this exists (ADR-0002)

`storage.parquet.ParquetEvidenceStore` is deliberately not touched by this module. Its
design -- one content-addressed Parquet file per `append()`, `sorted(root.glob("*.parquet"))`
on every `query()`, a fresh `duckdb.connect(":memory:")` per call, and a full Pydantic
rebuild plus three SHA-256 recomputations per row read back -- is a *correctness* feature
for the evidence plane: a few thousand discrete, individually re-verifiable events, where
every read re-proving its own content hash is exactly the point. At panel-plane scale
(5,534 listed stocks x ~2,440 trading days ~= 1.35e7 rows/field; `balancesheet` alone has
152 columns), the identical design is a wall, not a feature -- see
`docs/architecture/ADR-0002-two-data-planes.md` and the seam-audit findings it cites
(F71-F75). This module is the second, separate plane ADR-0002 calls for.

## Storage layout

Each partition is exactly one file: ``<root>/<dataset>/<year>/data.parquet``, written
through a ``data.parquet.tmp`` temporary file and an atomic ``Path.replace`` (the same
temp-then-rename idiom `ParquetEvidenceStore.append` already uses, so a reader can never
observe a half-written file). Partition granularity is `dataset/year/`, matching ADR-0002's
data model directly: it bounds any single-year query to one file regardless of how many
years of history accumulate, while staying coarse enough that ~2,440 trading days of a
single field fit inside one partition without generating thousands of tiny files (the
opposite failure mode from `ParquetEvidenceStore`'s one-file-per-append).

## Dataset name validation

`dataset` must be a single, plain path segment -- no `/`, no `..`, not absolute -- enforced
by `_validate_dataset()` at the top of every public method that accepts it (`write_partition()`,
`query()`, `profile_query()`), before anything else runs. This closes a real gap a review
found in the first version of this module: `write_partition()` built
`self.root / dataset / str(year)` and `profile_query()` built its profiling temp filename
from an f-string containing `dataset`, neither validated. A relative `dataset` containing
`..` escaped `root` once the OS resolved the traversal; an *absolute* `dataset` made
`Path(root) / dataset` discard `root` entirely (pathlib's documented behavior for joining
an absolute path onto anything) -- both reproduced as real files written outside `root`
before the fix. Every `dataset` currently passed anywhere in this codebase is a hardcoded
literal, so this was not exploitable *today* -- but every future dataset group writes
through this same primitive, and `_validate_dataset()` is what keeps that true going
forward instead of by accident. See `_validate_dataset`'s own docstring for the exact
rule and this task's review report for the reproduction.

## The catalog

``<root>/catalog.duckdb`` is a **persistent** DuckDB database file (never `":memory:"`),
created lazily by the *first `write_partition()` call*, not by `PanelStore(root)`
construction -- constructing a `PanelStore` never opens the catalog at all (see
`__init__`'s docstring for why: an earlier version of this class opened it read-write in
`__init__` to run `CREATE TABLE IF NOT EXISTS`, which meant every read-only caller had to
win the catalog's exclusive write lock just to instantiate, before ever issuing a query).
It holds exactly one small metadata table, `panel_partitions` (dataset, year, relative_path,
row_count, content_hash, written_at) -- never the panel data itself, which stays in
Parquet. This is what replaces `ParquetEvidenceStore.query`'s
`sorted(root.glob("*.parquet"))`: resolving `(dataset, year)` to a file path is an indexed
point lookup against a table with, at panel scale, a few thousand rows (one per
dataset-year), not a directory listing that grows with total file count. `query()` and
`profile_query()` then hand DuckDB the exact resolved file path -- never a glob -- so
partition pruning is enforced by construction, not by hoping DuckDB's optimizer notices a
`WHERE year = ?` filter (measured empirically before writing this module: DuckDB's Parquet
reader does *not* skip opening a file based on an ordinary column's footer statistics the
way it does for Hive-style `key=value` partition columns, so relying on that would have been
a silent, scale-dependent gap).

## Concurrency

DuckDB's own file-locking rule (checked against DuckDB 1.5 empirically, across real OS
processes, before this was written) is what shapes this module's *cross-process*
concurrency behavior:

- A file opened `read_only=True` may be opened concurrently by any number of processes, as
  long as no process holds it open for writing.
- A file opened for writing (the default) takes an **exclusive** lock; any other *process*
  attempting to open it at all -- read-write or read-only -- fails immediately with
  `duckdb.IOException` (a fast, explicit error, never a hang).

That rule governs concurrency *across OS processes*. This codebase's actual concurrency
primitive -- `runtime/batch.py`'s `BatchResearchService`, built on a `ThreadPoolExecutor` --
runs multiple worker *threads inside one process* against one shared object, a materially
different regime a review found this module's original concurrency testing never actually
exercised (it only ran real separate-process repros). Verified empirically for both regimes
before writing this section:

- Multiple threads in one process opening the catalog `read_only=True` concurrently do not
  conflict with each other, same as the cross-process rule above.
- Multiple threads in one process each opening the catalog read-write and inserting
  concurrently *do* conflict, but not with `duckdb.IOException`: DuckDB shares one database
  instance across same-process connections to the same file and serializes their
  transactions with its own MVCC, so a losing thread gets
  `duckdb.TransactionException: Catalog write-write conflict` instead -- reproduced with 4
  threads writing 4 *different* datasets against a cold-start store, 3 of 4 failing,
  reproducibly. (An earlier version of this section claimed `duckdb.IOException` here
  without the same-process case ever having been tested; that claim was simply wrong for
  the regime this codebase actually uses.)

Every catalog connection this module opens is short-lived: opened, used, closed within a
single `with` block, never held across calls (mirroring `ParquetEvidenceStore`'s own
per-call `duckdb.connect()` pattern). `query()`/`profile_query()` open it `read_only=True`,
so any number of concurrent readers -- same-process threads or separate processes -- can run
in parallel: verified for `query()` by
`test_concurrent_read_only_queries_from_separate_processes_do_not_fail_each_other`, and for
`profile_query()`, same-process, by
`test_profile_query_survives_eight_concurrent_threads_against_the_same_partition` -- which
required an actual fix, not just this docstring, to become true (`profile_query()`'s
profiling-output filename used to have no per-call uniqueness, so 8 concurrent threads
against the same partition produced 3-6 failures per run across 5 runs; it is now
`uuid.uuid4()`-suffixed per call).

`write_partition()`'s catalog upsert is guarded by an in-process `threading.Lock`
(`self._catalog_write_lock`), so concurrent same-process writer *threads* -- the regime
`ThreadPoolExecutor`-based callers actually create -- no longer race each other for the
catalog: the Parquet `COPY` (the expensive part) still runs unlocked and concurrently across
threads, and only the few-millisecond catalog upsert is serialized, eliminating the
`TransactionException` above by construction rather than by retrying after it happens. Two
threads writing the *same* `(dataset, year)` partition concurrently no longer crash either
-- the pre-fix temp Parquet filename had no per-writer uniqueness, so
`temporary.replace(target)` could raise a raw `FileNotFoundError` when one writer's rename
stepped on another's still-in-progress temp file; the temp filename is now
`uuid.uuid4()`-suffixed per call. The *result* of two threads racing the same partition is a
well-defined "last write to complete wins" outcome, consistent with `write_partition()`'s
already-stated overwrite-per-partition semantics -- this store adds no extra isolation
beyond that, and does not need to.

Cross-process writers are a different story this in-process lock cannot help with: a
`threading.Lock` coordinates threads within one Python process, never across OS process
boundaries. Two independent `PanelStore` instances in two OS processes racing to write still
hit the DuckDB file-locking rule at the top of this section -- whichever process's
`duckdb.connect(catalog_path)` (default, read-write) loses the race gets
`duckdb.IOException` immediately, not retried. This module makes a deliberate choice not to
solve that here: retrying blindly on `duckdb.IOException` would hide a design question
(should concurrent multi-process writers serialize, queue, or simply not happen?) that
belongs to whatever ingestion scheduler is eventually built on top of this storage skeleton,
not to the skeleton itself. Multi-process ingestion that needs concurrent writers must
serialize them itself (a single writer process, or an external lock) -- documented here as a
known, still-open limitation, not fixed by this task; see this task's report for the
concrete recommendation this leaves for `V2-P1-004`+.

## Write and idempotency semantics

`write_partition()` is an **overwrite-per-partition**, not an append: calling it twice for
the same `(dataset, year)` replaces that partition's content, matching how panel data
actually behaves in practice (a daily-price or fundamentals partition gets corrected and
backfilled, unlike an evidence event, which is immutable once observed). Idempotency is
content-hash-scoped, the same shape as `ParquetEvidenceStore.append`'s
`if target.exists(): return target` short circuit: writing byte-identical content for a
partition that already has it is a true no-op (no Parquet rewrite, no catalog row churn);
writing different content for an existing partition replaces it, atomically.

## Numerical-stack boundary (ADR-0003)

This module deliberately imports neither `numpy` nor `pandas`. DuckDB's own relational API
(`connection.execute()` / `executemany()` / `COPY ... TO PARQUET` / `read_parquet()`) fully
covers partitioned write, column projection, and partition pruning for this skeleton -- the
same idiom `storage/parquet.py` already uses. Introducing the pandas/numpy dependency here
would also trigger the mypy-strict chain reaction ADR-0003 documents (`follow_imports =
"skip"` plus `warn_return_any` fails any function returning a pandas/numpy expression) for
no capability this module actually needs; that tradeoff is deferred to the factor/model
layers ADR-0003 was written for; see this task's report for the full reasoning.

## What is deliberately *not* here

No concrete dataset (prices, fundamentals, calendar, adjustment factors, ...) is modeled --
`write_partition()` takes caller-supplied `ColumnSpec`s and raw row tuples, nothing typed to
a business schema. No data-catalog/readiness contract (`V2-P1-003`); that is a separate,
later task.

The columnar batch contract this file's original version also listed as absent has since
landed (`V2-P1-002`), deliberately *outside* this package: the contract itself is
`openalpha_cn.domain.panel_batch`, and the seam that turns one into `ColumnSpec`s plus a row
block is `openalpha_cn.panel_ingest`, a neutral top-level module. This package still imports
nothing but DuckDB and the standard library, exactly as
`tests/unit/test_import_layering.py::test_panel_package_has_zero_direct_edges_into_any_other_openalpha_cn_subpackage`
requires; see `panel_ingest.py`'s own docstring for why the seam lives where it does.

Two further design characteristics a review flagged as worth carrying forward, not fixed by
this task (out of scope -- recorded here so the next phase does not rediscover them from
scratch):

- **No range query.** `year` is a mandatory single `int` on both `query()` and
  `profile_query()`; there is no way to ask for a span of years in one call. A multi-year
  read needs caller-side looping over `query()`, one call per year (each still hitting
  exactly one partition file, per "The catalog" above -- looping does not reintroduce a
  full-history scan, it just means the caller issues N single-partition calls instead of
  this module offering one N-partition call).
- **No row-group pruning within a partition.** An equality filter in `filters` narrows the
  *result set* `query()`/`profile_query()` return, but not the *scan*: DuckDB reads every
  row group of the resolved partition file before applying the `WHERE` clause, rather than
  using Parquet row-group statistics to skip groups the filter cannot match. Partition
  pruning (across files, via the catalog) is a structural guarantee this module provides;
  row-group pruning (within one file) is not.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

import duckdb


class PanelStorageError(RuntimeError):
    """Raised for panel-store usage errors: an empty write batch, a malformed column
    list, a malformed `dataset` name, or a `profile_query()` against a partition the
    catalog has never registered."""


def _validate_dataset(dataset: str) -> None:
    """Reject any `dataset` that is not a single, plain path segment.

    `write_partition()` and `profile_query()` both join `dataset` onto `self.root` to
    build a real filesystem path -- `write_partition()` directly (`self.root / dataset /
    str(year)`), `profile_query()` through an f-string (`self.root /
    f".profile-{dataset}-{year}.json"`). `query()` never builds a path from `dataset`
    itself (it only uses the already-validated path a prior `write_partition()` call
    stored in the catalog), but calls this too, at the same boundary, so no future change
    to any of the three methods can reintroduce this gap by accident.

    Neither `Path.joinpath` nor the `/` operator sanitizes its right-hand operand:

    - A relative `dataset` containing `..` (e.g. `"../escaped"`) is accepted silently by
      `Path` construction; the traversal is only resolved once the OS actually opens a
      file for writing, by which point it has already escaped `root`.
    - An *absolute* `dataset` (e.g. `"/abs/path"`) makes `Path(root) / dataset` discard
      `root` entirely -- pathlib's documented behavior for joining an absolute path onto
      anything -- with no error at any layer.

    Both were reproduced against the pre-fix code before this check was written: a
    relative traversal wrote a real Parquet file as a sibling of `root`, and an absolute
    `dataset` wrote one at an arbitrary absolute path with `root` silently discarded (see
    this task's review report). A single call to this function, at the very top of every
    public method that accepts `dataset`, closes both: every value it accepts is
    guaranteed, structurally, to resolve to a child of `root` once joined.
    """
    if not dataset:
        raise PanelStorageError("dataset must not be empty")
    segment = Path(dataset)
    if segment.is_absolute() or segment.name != dataset or dataset in {".", ".."}:
        raise PanelStorageError(
            "dataset must be a single, plain path segment (no '/', no '..', not "
            f"absolute); got {dataset!r}"
        )


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One partition column's name and DuckDB SQL type (e.g. ``ColumnSpec("close",
    "DOUBLE")``). A plain dataclass, not a Pydantic model -- see the module docstring's
    "What is deliberately not here" section; nothing on this plane's write or read path
    needs validation or content-hash caching at the per-field level."""

    name: str
    duckdb_type: str


@dataclass(frozen=True, slots=True)
class PartitionRef:
    """A catalog-registered partition: exactly one Parquet file for one `(dataset, year)`."""

    dataset: str
    year: int
    path: Path
    row_count: int
    content_hash: str


_CATALOG_DDL = """
CREATE TABLE IF NOT EXISTS panel_partitions (
    dataset VARCHAR NOT NULL,
    year INTEGER NOT NULL,
    relative_path VARCHAR NOT NULL,
    row_count BIGINT NOT NULL,
    content_hash VARCHAR NOT NULL,
    written_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (dataset, year)
)
"""


class PanelStore:
    """`dataset/year/`-partitioned Parquet store with a persistent DuckDB catalog.

    See the module docstring for the storage layout, catalog placement, concurrency
    behavior, and write/idempotency semantics this class implements.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.root / "catalog.duckdb"
        # Deliberately no duckdb connection here. `panel_partitions` is created lazily by
        # the first `write_partition()` call, inside the same read-write transaction as
        # that write. If construction itself opened the catalog read-write (as an earlier
        # version of this class did), *every* `PanelStore(root)` -- including read-only
        # callers that only ever intend to `query()` -- would compete for the catalog's
        # exclusive write lock just to instantiate, defeating concurrent readers before
        # they even issue a query. Caught by
        # `test_concurrent_read_only_queries_from_separate_processes_do_not_fail_each_other`
        # during development: three reader processes each constructing `PanelStore`
        # concurrently raised `duckdb.IOException` (lock conflict) inside `__init__`,
        # exactly the failure this comment now prevents structurally.
        #
        # A plain `threading.Lock`, not a duckdb connection: it costs nothing to create
        # (no I/O, no catalog touched) and only ever guards `write_partition()`'s catalog
        # upsert (see there) against *same-process* writer threads -- the concurrency
        # regime `runtime/batch.py`'s `ThreadPoolExecutor` actually creates. It cannot, and
        # is not meant to, coordinate across separate OS processes; see the module
        # docstring's "Concurrency" section for why that remains a deliberately separate,
        # still-open concern.
        self._catalog_write_lock = threading.Lock()

    def write_partition(
        self,
        dataset: str,
        year: int,
        columns: Sequence[ColumnSpec],
        rows: Sequence[tuple[object, ...]],
    ) -> PartitionRef:
        """Write (or idempotently no-op, or overwrite) one `(dataset, year)` partition.

        See the module docstring's "Write and idempotency semantics" section.

        Raises `PanelStorageError` if `dataset` is not a single, plain path segment --
        see `_validate_dataset` and the module docstring's "Dataset name validation"
        section. This is a security boundary, not just an input-shape check: pre-fix, an
        unvalidated `dataset` could write a Parquet file as a sibling of `root`
        (`dataset="../escaped"`) or at an arbitrary absolute path with `root` silently
        discarded (`dataset="/abs/path"`).
        """
        _validate_dataset(dataset)
        if not columns:
            raise PanelStorageError("cannot write a partition with zero columns")
        if not rows:
            raise PanelStorageError("cannot write an empty partition batch")

        content_hash = _content_hash(dataset, year, columns, rows)
        existing = self._lookup(dataset, year)
        if existing is not None and existing.content_hash == content_hash:
            return existing

        relative_path = Path(dataset) / str(year) / "data.parquet"
        partition_dir = self.root / dataset / str(year)
        partition_dir.mkdir(parents=True, exist_ok=True)
        target = self.root / relative_path
        # Per-writer-unique, not `target.with_suffix(".parquet.tmp")`: two writers racing
        # to overwrite the *same* partition (same-process threads or separate processes)
        # used to share this filename, so one writer's `temporary.replace(target)` could
        # find the file already renamed away by the other, raising a raw
        # `FileNotFoundError`. Reproduced with 8 same-process threads before this fix; see
        # `test_write_partition_survives_concurrent_threads_writing_the_same_partition`.
        temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        column_ddl = ", ".join(f'"{column.name}" {column.duckdb_type}' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        with duckdb.connect(":memory:") as staging:
            staging.execute(f"CREATE TABLE staging ({column_ddl})")
            staging.executemany(f"INSERT INTO staging VALUES ({placeholders})", rows)
            staging.execute(
                "COPY staging TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(temporary)]
            )
        temporary.replace(target)

        # Locked for the same reason the connection itself is short-lived: DuckDB shares
        # one database instance across same-process connections to the same file and
        # serializes their transactions with its own MVCC, so concurrent same-process
        # writer threads racing this block used to lose with
        # `duckdb.TransactionException: Catalog write-write conflict` -- reproduced with 4
        # threads writing 4 *different* datasets against a cold-start store (no catalog
        # file yet) before this fix, 3 of 4 failing, reproducibly; see
        # `test_write_partition_survives_four_concurrent_threads_writing_four_different_datasets`.
        # The lock only wraps this few-millisecond upsert, not the Parquet `COPY` above --
        # that stays unlocked and concurrent across writer threads. It cannot coordinate
        # across separate OS processes; see the module docstring's "Concurrency" section.
        with self._catalog_write_lock, duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(_CATALOG_DDL)
            connection.execute(
                """
                INSERT INTO panel_partitions
                    (dataset, year, relative_path, row_count, content_hash, written_at)
                VALUES (?, ?, ?, ?, ?, now())
                ON CONFLICT (dataset, year) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    row_count = excluded.row_count,
                    content_hash = excluded.content_hash,
                    written_at = excluded.written_at
                """,
                [dataset, year, str(relative_path), len(rows), content_hash],
            )
        return PartitionRef(dataset, year, target, len(rows), content_hash)

    def query(
        self,
        dataset: str,
        *,
        year: int,
        columns: Sequence[str],
        filters: Mapping[str, object] | None = None,
    ) -> list[tuple[object, ...]]:
        """Return the requested `columns` for `(dataset, year)`, optionally filtered by
        equality on `filters`. Empty list if the partition was never written -- no error.

        Resolves the partition's file path through the catalog (an indexed point lookup,
        never a directory listing) and hands DuckDB that single explicit path, so the scan
        can only ever touch this one partition -- see the module docstring's "The catalog"
        section for why this is a structural guarantee, not an optimizer hope.

        Raises `PanelStorageError` if `dataset` is not a single, plain path segment (see
        `_validate_dataset`). Unlike a merely-unwritten partition, this is a malformed
        request, not missing data, so it raises rather than returning `[]` -- this method
        never actually builds a filesystem path from `dataset` itself (only from a path a
        prior, already-validated `write_partition()` call stored in the catalog), but is
        validated at the same boundary as the two methods that do, so the guarantee holds
        for all three without depending on write-time validation alone.
        """
        _validate_dataset(dataset)
        if not self.catalog_path.exists():
            return []
        with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
            partition_path = self._resolve_partition_path(connection, dataset, year)
            if partition_path is None:
                return []
            sql, parameters = _build_scan_sql(partition_path, columns, filters)
            result = connection.execute(sql, parameters).fetchall()
        return cast(list[tuple[object, ...]], result)

    def profile_query(
        self,
        dataset: str,
        *,
        year: int,
        columns: Sequence[str],
        filters: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Run the exact statement `query()` would run, with DuckDB's JSON profiler
        enabled, and return the `READ_PARQUET` scan operator's `extra_info` -- the
        structural evidence (`Total Files Read`, `Filename(s)`, `Projections`,
        `total_bytes_read`) this task's tests use to prove partition pruning and column
        projection without asserting on wall-clock timing.

        Raises `PanelStorageError` if the partition was never written (there is no scan to
        profile), unlike `query()`'s empty-list-on-missing-partition behavior. Also raises
        `PanelStorageError` if `dataset` is not a single, plain path segment (see
        `_validate_dataset`), checked before the catalog is even consulted -- pre-fix, once
        a `dataset` resolved to a registered partition, the profiling temp filename below
        was built directly from it with no sanitization, and a `dataset` like
        `"/../../escaped2"` wrote that temp file outside `root` entirely.

        Safe for any number of concurrent callers -- same-process threads or separate
        processes -- because the profiling temp filename is unique per call
        (`uuid.uuid4()`-suffixed), not shared across callers. An earlier version derived it
        from only `(dataset, year)`; two concurrent callers profiling the same partition
        would then both write to and `unlink()` the same file. Measured directly with 8
        concurrent same-process threads calling this method against the same partition,
        that produced 3-6 failures per run across 5 runs (`FileNotFoundError` from one
        caller unlinking another's file, or `json.JSONDecodeError: Extra data` from two
        callers' profiling JSON landing in the same file) -- see
        `test_profile_query_survives_eight_concurrent_threads_against_the_same_partition`.
        """
        _validate_dataset(dataset)
        if not self.catalog_path.exists():
            raise PanelStorageError(f"no partition registered for {dataset} year={year}")
        with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
            partition_path = self._resolve_partition_path(connection, dataset, year)
            if partition_path is None:
                raise PanelStorageError(f"no partition registered for {dataset} year={year}")
            sql, parameters = _build_scan_sql(partition_path, columns, filters)
            profile_path = self.root / f".profile-{dataset}-{year}-{uuid.uuid4().hex}.json"
            connection.execute("PRAGMA enable_profiling='json'")
            connection.execute(f"PRAGMA profiling_output='{profile_path}'")
            connection.execute(sql, parameters).fetchall()
            connection.execute("PRAGMA disable_profiling")
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        finally:
            profile_path.unlink(missing_ok=True)
        scan_node = _find_scan_node(profile)
        if scan_node is None:
            raise PanelStorageError("DuckDB profiling output contained no READ_PARQUET scan node")
        extra_info = scan_node["extra_info"]
        assert isinstance(extra_info, dict)
        result: dict[str, object] = dict(extra_info)
        result["total_bytes_read"] = profile.get("total_bytes_read")
        return result

    def _lookup(self, dataset: str, year: int) -> PartitionRef | None:
        """Used only by `write_partition()`'s idempotency check, ahead of that method's
        own read-write catalog transaction -- so this must tolerate the catalog not
        existing yet (the very first `write_partition()` call on a fresh store) without
        attempting a `read_only=True` connect to a nonexistent file, which DuckDB rejects.
        """
        if not self.catalog_path.exists():
            return None
        with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
            return self._lookup_with_connection(connection, dataset, year)

    def _lookup_with_connection(
        self, connection: duckdb.DuckDBPyConnection, dataset: str, year: int
    ) -> PartitionRef | None:
        row = connection.execute(
            "SELECT relative_path, row_count, content_hash FROM panel_partitions "
            "WHERE dataset = ? AND year = ?",
            [dataset, year],
        ).fetchone()
        if row is None:
            return None
        relative_path, row_count, content_hash = cast(tuple[str, int, str], row)
        return PartitionRef(dataset, year, self.root / relative_path, row_count, content_hash)

    def _resolve_partition_path(
        self, connection: duckdb.DuckDBPyConnection, dataset: str, year: int
    ) -> Path | None:
        partition = self._lookup_with_connection(connection, dataset, year)
        return None if partition is None else partition.path


def _build_scan_sql(
    partition_path: Path, columns: Sequence[str], filters: Mapping[str, object] | None
) -> tuple[str, list[object]]:
    if not columns:
        raise PanelStorageError("must request at least one column")
    column_list = ", ".join(f'"{name}"' for name in columns)
    parameters: list[object] = [str(partition_path)]
    where_sql = ""
    if filters:
        clauses = [f'"{key}" = ?' for key in filters]
        parameters.extend(filters.values())
        where_sql = " WHERE " + " AND ".join(clauses)
    return f"SELECT {column_list} FROM read_parquet(?){where_sql}", parameters


def _content_hash(
    dataset: str, year: int, columns: Sequence[ColumnSpec], rows: Sequence[tuple[object, ...]]
) -> str:
    canonical = json.dumps(
        {
            "dataset": dataset,
            "year": year,
            "columns": [[column.name, column.duckdb_type] for column in columns],
            "rows": [list(row) for row in rows],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _find_scan_node(node: object) -> dict[str, object] | None:
    if not isinstance(node, dict):
        return None
    if node.get("operator_name") == "READ_PARQUET":
        return cast(dict[str, object], node)
    for child in node.get("children", []):
        found = _find_scan_node(child)
        if found is not None:
            return found
    return None
