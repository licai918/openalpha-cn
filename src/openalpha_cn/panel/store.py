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
processes, before this was written) is what shapes this module's concurrency behavior:

- A file opened `read_only=True` may be opened concurrently by any number of processes, as
  long as no process holds it open for writing.
- A file opened for writing (the default) takes an **exclusive** lock; any other process
  attempting to open it at all -- read-write or read-only -- fails immediately with
  `duckdb.IOException` (a fast, explicit error, never a hang).

Every catalog connection this module opens is short-lived: opened, used, closed within a
single `with` block, never held across calls (mirroring `ParquetEvidenceStore`'s own
per-call `duckdb.connect()` pattern). `write_partition()` opens the catalog read-write only
for the few milliseconds it takes to write one metadata row; `query()`/`profile_query()`
open it `read_only=True`, so any number of concurrent readers can run in parallel. A writer
racing another writer for the same process is not retried by this skeleton -- the loser's
`duckdb.connect()` raises `duckdb.IOException` immediately, and ingestion pipelines built on
top of this module are expected to run as a single sequential writer (this is a storage
skeleton, not an ingestion scheduler; see this task's report for the concrete concern).

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
a business schema. No data-catalog/readiness contract (`V2-P1-003`) and no columnar batch
contract (`V2-P1-002`). Both are separate, later tasks.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

import duckdb


class PanelStorageError(RuntimeError):
    """Raised for panel-store usage errors: an empty write batch, a malformed column
    list, or a `profile_query()` against a partition the catalog has never registered."""


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

    def write_partition(
        self,
        dataset: str,
        year: int,
        columns: Sequence[ColumnSpec],
        rows: Sequence[tuple[object, ...]],
    ) -> PartitionRef:
        """Write (or idempotently no-op, or overwrite) one `(dataset, year)` partition.

        See the module docstring's "Write and idempotency semantics" section.
        """
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
        temporary = target.with_suffix(".parquet.tmp")
        column_ddl = ", ".join(f'"{column.name}" {column.duckdb_type}' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        with duckdb.connect(":memory:") as staging:
            staging.execute(f"CREATE TABLE staging ({column_ddl})")
            staging.executemany(f"INSERT INTO staging VALUES ({placeholders})", rows)
            staging.execute(
                "COPY staging TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(temporary)]
            )
        temporary.replace(target)

        with duckdb.connect(str(self.catalog_path)) as connection:
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
        """
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
        profile), unlike `query()`'s empty-list-on-missing-partition behavior.
        """
        if not self.catalog_path.exists():
            raise PanelStorageError(f"no partition registered for {dataset} year={year}")
        with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
            partition_path = self._resolve_partition_path(connection, dataset, year)
            if partition_path is None:
                raise PanelStorageError(f"no partition registered for {dataset} year={year}")
            sql, parameters = _build_scan_sql(partition_path, columns, filters)
            profile_path = self.root / f".profile-{dataset}-{year}.json"
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
