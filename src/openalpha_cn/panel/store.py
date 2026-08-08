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

## SQL identifier and type handling

Every identifier this module interpolates into SQL goes through `_quote_identifier()`, which
doubles any embedded `"` -- DuckDB's own escape for a quoted identifier -- so the value can
only ever be read back as a name, never as syntax. Three call sites depend on it:
`write_partition()`'s `CREATE TABLE` column list, `_build_scan_sql()`'s `SELECT` projection,
and `_build_scan_sql()`'s `WHERE` keys. All three previously interpolated the raw string
inside bare double quotes, and all three were reproduced as live injections against
`cb9e8f4` (see `V2-P1-002`'s review report): a column *type* of `DOUBLE); ATTACH '<path>' AS
evil; CREATE TABLE evil.pwned(x INTEGER` created an attacker-named DuckDB file on disk; a
projected column *name* carrying a statement break ran a `COPY ... TO '<path>'` that landed a
file on disk; and a *filter key* of `ts_code" = 'nope' OR TRUE OR "ts_code` neutralised the
`WHERE` clause so `query()` returned every row of the partition instead of the one the caller
asked for.

Escaping, rather than a plain-identifier whitelist, is the deliberate choice at this layer.
`PanelStore` is a storage primitive with no opinion about what a panel column may be called;
the *policy* that panel column names are plain ASCII identifiers belongs to the contract that
produces them (`domain/panel_batch.py::validate_panel_identifier`, enforced at `PanelColumn`
construction) and is applied one layer up. Restating that policy here would put a third copy
of a naming rule in the tree -- `dataset` validation is already duplicated between this module
and the contract, with a dedicated drift test holding the two copies together -- and a copy
that drifts is worse than no copy. Escaping has no policy content to drift: it is complete for
any identifier DuckDB itself accepts. The two inputs escaping cannot make safe are refused
outright instead: an empty name (DuckDB: "zero-length delimited identifier") and a name
containing a NUL byte (which truncates DuckDB's parse of the quoted identifier). Both would
otherwise surface as a raw `duckdb.ParserException` rather than a `PanelStorageError`.

A `ColumnSpec`'s `duckdb_type` is *not* an identifier and cannot be quoted, so it is
restricted to the closed `DUCKDB_COLUMN_TYPES` set at `ColumnSpec` construction -- an
arbitrary type string can never reach the DDL, because a `ColumnSpec` carrying one cannot be
built in the first place.

## Dataset name validation

`dataset` must be a single, plain path segment -- no `/`, no `..`, not absolute, and no
leading or trailing whitespace -- enforced by `_validate_dataset()` at the top of every public
method that accepts it (`write_partition()`, `query()`, `profile_query()`), before anything
else runs. This closes a real gap a review found in the first version of this module:
`write_partition()` built
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
It holds small metadata tables -- `panel_partitions` (dataset, year, relative_path,
row_count, content_hash, written_at) and, since `V2-P1-003`, the coverage tables described
below -- never the panel data itself, which stays in
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

## The coverage tables, and why they are additive (`V2-P1-003`)

`panel_partitions` answers exactly one of PRD Story S8's five questions. The five that close
it live in six further tables, none of which touches `panel_partitions`:

    panel_catalog_meta            key/value; today only the schema stamp
    panel_partition_coverage      one row per partition: provenance, freshness, counts
    panel_partition_subjects      the distinct subject universe        (S8: subjects)
    panel_partition_fields        every stored column and its kind     (S8: fields)
    panel_partition_dates         the per-trading-date row census      (S8: date coverage)
    panel_partition_revisions     the per-version row census           (S8: revisions)

All six are keyed on `(dataset, year)`, the same key `panel_partitions` already uses, and are
created by the same `CREATE TABLE IF NOT EXISTS` pass. **`panel_partitions` itself is
unchanged**, which is the whole answer to "what happens to partitions written before this
existed": nothing. An old catalog keeps every row it had, its partitions stay queryable, and
it simply has no coverage row -- a state the readiness contract already models as
`coverage_missing` and treats as blocking. Recording coverage later fills it in place. There
is no backfill, no rewrite, and no data migration, because no existing column changed
meaning. `tests/integration/panel/test_panel_catalog.py::
test_a_catalog_written_before_the_coverage_tables_existed_still_works` drops all six tables
from a real catalog and proves exactly that.

That is a property of *that* change, not a promise about the next one, so the catalog stamps
its own version (`panel_catalog_meta.schema_version`). A catalog stamped with a version this
code does not know is refused rather than misread -- an older build opening a newer catalog
fails loudly instead of silently reading a column that has been repurposed. A catalog
carrying *no* stamp is a pre-`V2-P1-003` catalog and is treated as v1 by definition.

## The `v1 -> v2` bump, and why an additive column still moved the stamp

The stamp is now `panel-catalog/v2`. The only difference is one nullable column,
`panel_partition_coverage.partition_content_hash`, so the change is as structurally additive
as the six tables above were -- and the stamp moved anyway, because structural additivity is
not the test the stamp applies. The test is whether an *older* build reading this catalog
would misjudge the data, and here it plainly would: a v1 build has no notion of comparing a
coverage record against the partition it describes, so it reports `ready` for exactly the
stale-coverage partitions a v2 build blocks. That is a silent fail-open, which is the failure
mode the stamp exists to catch, so a v1 build must be stopped at the door.

In the other direction this build reads a v1 catalog rather than refusing it, because it
knows precisely what a v1 catalog lacks: `_read_coverage` selects `NULL` for the missing
column, and readiness treats an unknown `partition_content_hash` as `coverage_stale` --
fail-closed, and cleared by one `record_coverage()` call. The forward migration itself is
`_COVERAGE_ADDED_COLUMNS`, an `ALTER TABLE ... ADD COLUMN` applied by `_ensure_catalog_schema`
on the write path, followed by re-stamping `v2`. It is a real migration and not a `DROP` and
re-derive, because the catalog is not a rebuildable cache: the content-derived half of a
coverage record could be recomputed by scanning Parquet, but `provider_id`, `as_of`,
`fetched_at` and `batch_digest` exist nowhere on disk except here.

`storage/migrations.py` does not and cannot govern any of this: it is a SQLite engine built
on `PRAGMA user_version`, a `schema_migrations` audit table and SQLite's own backup API, and
the panel catalog is a DuckDB file with none of those. Nor is the panel catalog a rebuildable
cache that could sidestep the question -- the content-derived half of a coverage record
(subjects, fields, dates, revisions, counts) could indeed be recomputed by scanning the
Parquet partitions, but the provenance half (`provider_id`, `as_of`, `fetched_at`,
`batch_digest`) exists nowhere on disk except this catalog. So the first genuinely breaking
change here needs a real forward migration, written against this stamp, and must not be
mistaken for a `DROP` and re-derive.

## Coverage, readiness, and where each rule lives

`record_coverage()` is a separate call from `write_partition()`, not an extra argument to it.
The storage primitive takes raw rows and knows nothing about providers or batches, so it has
nothing to say about provenance.

That split is not atomic, and the interrupted case (`write_partition()` succeeded,
`record_coverage()` did not) needs stating precisely, because an earlier version of this
docstring got it wrong. On a **first** write there is no coverage row at all, so readiness
reports `coverage_missing` and blocks. On a **re-write** -- a backfill or a correction, which
is the entire reason `write_partition()` has overwrite semantics -- the previous coverage row
is still sitting there, and it used to satisfy every check readiness knew how to make: the
file exists, a coverage record exists, and its dates, subjects and fields all come from that
now-obsolete record. The verdict was `ready`, with no issues, describing a write that had
been replaced. Claiming the non-atomicity "fails closed" was true of the first write only.

What makes it fail closed in both cases is `PartitionCoverage.partition_content_hash`:
`record_coverage()` stamps the record with the partition's `content_hash` as it stood at that
moment, and readiness blocks (`coverage_stale`) whenever the partition's current hash differs
or is unknown. An interrupted re-write therefore leaves a record whose stamp names the old
content, and it blocks. A row count would not have been enough -- a correction that changes
values without changing how many rows there are leaves the count identical and moves the hash
-- and both values are already in hand on the same read-only connection, so the check costs
nothing.

A partition file *damaged behind the store's back* is a different fault with a different
answer: nothing in the catalog changes, so no hash comparison can see it. Readiness checks
Parquet's own magic at both ends of the file (`_looks_like_parquet`, eight bytes) and reports
`partition_file_unreadable` for a truncated or overwritten one; a file replaced by a
different but *valid* Parquet file is out of reach of any O(1) check and belongs to
`V2-P1-012`'s deep pass. `read_if_ready()` additionally wraps its scan, so even an
unanticipated corruption surfaces as a `PanelStorageError` rather than as a bare
`duckdb.InvalidInputException` escaping a method that promises a verdict.

Every value a `PartitionCoverage` carries is validated here, in `_validated_coverage()`, and
nowhere else. `panel/catalog.py`'s dataclasses deliberately have no validating
`__post_init__`: the previous task reproduced, twice, that a nominal type is not a boundary
(a duck-typed stand-in satisfies every attribute read, and a subclass can override
`__post_init__` away), so a rule enforced at construction is a rule that can be skipped. The
boundary is where the value is used, and that is this module.

## Clock injection

`written_at` used to be DuckDB's own `now()`, evaluated inside the database. That is a bare
wall-clock read no test can freeze, in a codebase that injects a clock into
`storage/migrations.py`, `runtime/batch.py`, `sdk.py` and every provider. It is now
`self._now()`, from a `clock` callable defaulting to `datetime.now(UTC)`, and so is the
coverage record's `recorded_at`. A clock returning a naive datetime is refused rather than
normalised: bound to a `TIMESTAMPTZ` column, a naive value is interpreted in the machine's
local zone, so the same code would record a different instant depending on where it ran.

The freshness this buys is *not* what S8 means by freshness. `written_at`/`recorded_at`
answer "when did this land"; S8 asks "how far does this data reach", which is
`panel_partition_coverage.last_event_time`. Both are recorded, in different columns, and
neither is presented as the other -- conflating them is exactly why a catalog with a write
timestamp still could not report freshness.

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
a business schema. No trading calendar either, so `assess_readiness()` detects date holes
against a caller-supplied `required_dates` rather than against the days the exchange was
actually open; that arrives with `V2-P1-004`. No health report (`V2-P1-012`), no fail-closed
dependency gate (`V2-P1-013`), no CLI (`V2-P1-015`) and no REST/SDK surface (`V2-P1-016`) --
each of those consumes `read_coverage()`, `assess_readiness()` and `read_if_ready()` rather
than reimplementing them.

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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import duckdb

from openalpha_cn.panel.catalog import (
    PANEL_CATALOG_SCHEMA_VERSION,
    PANEL_CATALOG_SCHEMA_VERSIONS_READABLE,
    DatasetReadiness,
    DateCoverage,
    FieldCoverage,
    PanelReadOutcome,
    PanelStorageError,
    PartitionCoverage,
    PartitionState,
    ReadinessRequirement,
    RevisionCoverage,
    evaluate_readiness,
)

__all__ = [
    "DUCKDB_COLUMN_TYPES",
    "ColumnSpec",
    # Re-exported, not redefined: `panel/catalog.py` owns it so that
    # `PanelReadOutcome.rows` can raise it without importing this module (the dependency
    # runs store -> catalog and must not run back). Every existing
    # `from openalpha_cn.panel.store import PanelStorageError` keeps working.
    "PanelStorageError",
    "PanelStore",
    "PartitionRef",
]


def _utc_now() -> datetime:
    """This package's default clock.

    A local definition rather than `providers.base.utc_now`, because
    `tests/unit/test_import_layering.py::test_panel_package_has_zero_direct_edges_into_any_other_openalpha_cn_subpackage`
    pins `openalpha_cn.panel` as importing no sibling subpackage at all. Two lines of
    stdlib is the honest cost of that guarantee; reaching for the shared helper would buy
    nothing and break the boundary.
    """
    return datetime.now(UTC)


DUCKDB_COLUMN_TYPES: frozenset[str] = frozenset(
    {"BIGINT", "BOOLEAN", "DOUBLE", "TIMESTAMPTZ", "VARCHAR"}
)
"""Every DuckDB SQL type a `ColumnSpec` may carry.

Deliberately closed and deliberately small: a `duckdb_type` is interpolated into
`write_partition()`'s `CREATE TABLE` DDL and, unlike a column name, has no quoted form that
would make an arbitrary string inert (see the module docstring's "SQL identifier and type
handling"). It holds exactly the five types this codebase writes today -- the five
`panel_ingest.PANEL_DUCKDB_TYPES` maps its logical column kinds onto, which
`tests/integration/panel/test_panel_store_hardening.py` pins against this set. Widening it is
a one-line, reviewed change; accepting an unlisted string is not.
"""


def _quote_identifier(name: str, *, role: str = "column") -> str:
    """Return `name` as a DuckDB quoted identifier, escaping any `"` it contains.

    DuckDB's escape for a double quote inside a delimited identifier is a doubled `""`, so
    `close" INTEGER); ATTACH ...` becomes the literal column name
    `"close"" INTEGER); ATTACH ..."` rather than a quote-closing statement break. This is the
    single place any identifier becomes SQL text in this module; see the module docstring for
    why escaping rather than a whitelist is the right mechanism *at this layer*.

    Two inputs escaping cannot rescue are refused here so they fail as `PanelStorageError`
    rather than as a raw `duckdb.ParserException`: an empty name (DuckDB rejects a
    zero-length delimited identifier) and a name containing a NUL byte (DuckDB stops parsing
    the identifier at the NUL, so the closing quote is never seen).
    """
    if not name:
        raise PanelStorageError(f"{role} name must not be empty")
    if "\x00" in name:
        raise PanelStorageError(f"{role} name must not contain a NUL byte; got {name!r}")
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


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

    Leading and trailing whitespace is refused too, rather than stripped. `" prices "` is a
    legal directory name on every filesystem this runs on, so accepting it would create a
    partition directory whose name no human types correctly and which reads as identical to
    `prices` in every log line; stripping it instead would mean the caller's `dataset` and
    the directory on disk are different strings, silently. `domain/panel_batch.py`'s
    `validate_panel_dataset` refuses the same shape, for the same reason, and
    `tests/unit/domain/test_panel_batch.py` pins the two copies together.
    """
    if not dataset:
        raise PanelStorageError("dataset must not be empty")
    if dataset != dataset.strip():
        raise PanelStorageError(
            f"dataset must not have leading or trailing whitespace; got {dataset!r}"
        )
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
    needs validation or content-hash caching at the per-field level.

    `duckdb_type` is the one field that *is* validated, and it is validated here rather than
    in `write_partition()` so that an invalid spec cannot be constructed at all: the type is
    interpolated into `CREATE TABLE` DDL with no quoting available to it (see the module
    docstring's "SQL identifier and type handling"). `name` is not validated -- it is escaped
    at every SQL call site instead, for the reasons that section gives.
    """

    name: str
    duckdb_type: str

    def __post_init__(self) -> None:
        if self.duckdb_type not in DUCKDB_COLUMN_TYPES:
            raise PanelStorageError(
                f"column {self.name!r} has an unsupported DuckDB type "
                f"{self.duckdb_type!r}; expected one of {sorted(DUCKDB_COLUMN_TYPES)}"
            )


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

# `V2-P1-003`'s coverage tables. Purely additive -- `panel_partitions` above is byte for
# byte what `V2-P1-001` shipped -- which is why a catalog written before this task needs no
# migration; see the module docstring's "The coverage tables, and why they are additive".
_COVERAGE_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS panel_catalog_meta (
        key VARCHAR PRIMARY KEY,
        value VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS panel_partition_coverage (
        dataset VARCHAR NOT NULL,
        year INTEGER NOT NULL,
        provider_id VARCHAR NOT NULL,
        kind VARCHAR NOT NULL,
        schema_version VARCHAR NOT NULL,
        batch_digest VARCHAR NOT NULL,
        as_of TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        row_count BIGINT NOT NULL,
        date_timezone VARCHAR NOT NULL,
        last_event_time TIMESTAMPTZ NOT NULL,
        max_available_time TIMESTAMPTZ NOT NULL,
        revised_row_count BIGINT NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL,
        partition_content_hash VARCHAR,
        PRIMARY KEY (dataset, year)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS panel_partition_subjects (
        dataset VARCHAR NOT NULL,
        year INTEGER NOT NULL,
        subject VARCHAR NOT NULL,
        PRIMARY KEY (dataset, year, subject)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS panel_partition_fields (
        dataset VARCHAR NOT NULL,
        year INTEGER NOT NULL,
        ordinal INTEGER NOT NULL,
        field_name VARCHAR NOT NULL,
        field_kind VARCHAR NOT NULL,
        PRIMARY KEY (dataset, year, field_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS panel_partition_dates (
        dataset VARCHAR NOT NULL,
        year INTEGER NOT NULL,
        event_date DATE NOT NULL,
        row_count BIGINT NOT NULL,
        PRIMARY KEY (dataset, year, event_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS panel_partition_revisions (
        dataset VARCHAR NOT NULL,
        year INTEGER NOT NULL,
        revision_label VARCHAR NOT NULL,
        row_count BIGINT NOT NULL,
        PRIMARY KEY (dataset, year, revision_label)
    )
    """,
)

_COVERAGE_CHILD_TABLES: tuple[str, ...] = (
    "panel_partition_subjects",
    "panel_partition_fields",
    "panel_partition_dates",
    "panel_partition_revisions",
)

_COVERAGE_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("panel_partition_coverage", "partition_content_hash", "VARCHAR"),
)
"""The forward migration from `panel-catalog/v1` to `panel-catalog/v2`, as data.

`CREATE TABLE IF NOT EXISTS` cannot add a column to a table that already exists, so a catalog
written before `V2-P1-003`'s review fix has `panel_partition_coverage` without
`partition_content_hash`. Each entry here is `(table, column, type)` and is applied with
`ALTER TABLE ... ADD COLUMN` on the write path only -- the read path opens the catalog
`read_only=True` and cannot alter anything, so it tolerates the column's absence instead
(`_read_coverage` reads `None` for it, which readiness blocks on as `coverage_stale`).

The column is nullable rather than `NOT NULL DEFAULT ''`: an existing coverage row genuinely
does not know which partition write it was recorded against, and inventing a value would be
the fail-open this migration exists to close. `NULL` means "unverifiable", and one
`record_coverage()` call fills it in.
"""

_MAX_TEXT_LENGTH: int = 2048

_PARQUET_MAGIC: bytes = b"PAR1"
"""Parquet's file magic, written at both ends of every Parquet file (format spec).

Checked, rather than assumed, because `Path.is_file()` is true of a zero-byte file and of a
file whose bytes have been replaced with something else entirely -- both of which the
readiness contract used to call `ready`, and the second of which made `read_if_ready()` raise
a bare `duckdb.InvalidInputException` from inside a method that promises blocked-or-ready as
its only two outcomes. Eight bytes per partition per assessment, independent of file size.
"""


class PanelStore:
    """`dataset/year/`-partitioned Parquet store with a persistent DuckDB catalog.

    See the module docstring for the storage layout, catalog placement, concurrency
    behavior, and write/idempotency semantics this class implements.
    """

    def __init__(self, root: Path, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.root / "catalog.duckdb"
        # Every catalog timestamp comes from here. `written_at` used to be DuckDB's own
        # `now()`, evaluated inside the database -- unfreezable by any test, in a codebase
        # that injects a clock everywhere else. See the module docstring's "Clock injection".
        self._clock = clock
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
        # Read (and check) the clock before anything is written, so a caller whose clock is
        # unusable fails without leaving a half-registered partition on disk.
        written_at = self._now()

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
        column_ddl = ", ".join(
            f"{_quote_identifier(column.name)} {column.duckdb_type}" for column in columns
        )
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
            self._ensure_catalog_schema(connection)
            connection.execute(
                """
                INSERT INTO panel_partitions
                    (dataset, year, relative_path, row_count, content_hash, written_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (dataset, year) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    row_count = excluded.row_count,
                    content_hash = excluded.content_hash,
                    written_at = excluded.written_at
                """,
                [dataset, year, str(relative_path), len(rows), content_hash, written_at],
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
            _check_catalog_schema_version(connection)
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
            _check_catalog_schema_version(connection)
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

    # --- the data catalog (`V2-P1-003`) ----------------------------------------------------

    def record_coverage(self, coverage: PartitionCoverage) -> PartitionCoverage:
        """Record (or replace) one partition's Story S8 coverage record.

        Returns the stored record, with `recorded_at` filled in from the injected clock.

        Every field of `coverage` is validated here rather than at construction --
        `PartitionCoverage` is deliberately a carrier with no validating `__post_init__`, see
        `panel/catalog.py`'s module docstring and `_validated_coverage` below. Two
        cross-checks against the catalog run as well: the partition must already be
        registered (coverage with no partition behind it would make the catalog claim
        knowledge of data that is not there), and its row count must agree with the
        partition's (a disagreement means the coverage describes a different write).

        The registered partition's `content_hash` is stored on the record as
        `partition_content_hash`, overriding whatever the caller put there -- it is a fact
        about this storing, not about the batch. That is what lets readiness notice later
        that a subsequent `write_partition()` has moved the partition out from under this
        record; see `PartitionCoverage.partition_content_hash` and the module docstring's
        "Coverage, readiness, and where each rule lives".

        Deliberately a separate call from `write_partition()`; the module docstring's
        "Coverage, readiness, and where each rule lives" explains why.
        """
        validated = _validated_coverage(coverage)
        recorded_at = self._now()
        if not self.catalog_path.exists():
            raise PanelStorageError(
                f"no partition registered for {validated.dataset} year={validated.year}"
            )
        with self._catalog_write_lock, duckdb.connect(str(self.catalog_path)) as connection:
            self._ensure_catalog_schema(connection)
            existing = self._lookup_with_connection(connection, validated.dataset, validated.year)
            if existing is None:
                raise PanelStorageError(
                    f"no partition registered for {validated.dataset} year={validated.year}"
                )
            if existing.row_count != validated.row_count:
                raise PanelStorageError(
                    f"coverage row_count {validated.row_count} disagrees with the registered "
                    f"partition's {existing.row_count} for {validated.dataset} "
                    f"year={validated.year}"
                )
            stored = replace(
                validated, recorded_at=recorded_at, partition_content_hash=existing.content_hash
            )
            _write_coverage(connection, stored, recorded_at=recorded_at)
        return stored

    def read_coverage(self, dataset: str, year: int) -> PartitionCoverage | None:
        """Return one partition's coverage record, or `None` if it has never been profiled.

        `None` is a real answer, not an error: `write_partition()` takes raw rows and cannot
        know a batch's provenance, so a partition written through it directly legitimately
        has no coverage. `assess_readiness()` treats that as blocking (`coverage_missing`)
        rather than as "nothing wrong".
        """
        _validate_dataset(dataset)
        if not self.catalog_path.exists():
            return None
        with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
            _check_catalog_schema_version(connection)
            return _read_coverage(connection, dataset, year)

    def registered_years(self, dataset: str) -> tuple[int, ...]:
        """Every year `dataset` has a catalog row for, ascending. Empty if it has none."""
        _validate_dataset(dataset)
        if not self.catalog_path.exists():
            return ()
        with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
            _check_catalog_schema_version(connection)
            if not _table_exists(connection, "panel_partitions"):
                return ()
            rows = connection.execute(
                "SELECT year FROM panel_partitions WHERE dataset = ? ORDER BY year", [dataset]
            ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def assess_readiness(self, requirement: ReadinessRequirement) -> DatasetReadiness:
        """Judge whether `requirement.dataset` may be read at `requirement.as_of`.

        Reads the catalog, checks each requested year's Parquet file is actually there, and
        hands the resulting `PartitionState`s to `catalog.evaluate_readiness` -- the rule
        table lives there, pure and I/O-free, so it can be exercised without a store.

        `as_of` must be timezone-aware, and the requirement is validated at this boundary for
        the same reason a `PartitionCoverage` is: `ReadinessRequirement` is a plain carrier,
        so nothing about its type guarantees its contents. The check that matters most is the
        one on `required_dates` -- a `datetime` is a `date` subclass, so a `datetime` slipped
        into that tuple would never equal any observed `date` and would report a permanent,
        entirely phantom `date_gap` for data that is in fact complete. A blocked verdict with
        an invented cause is worse than a crash, because it looks like a finding.
        """
        _validated_requirement(requirement)
        return evaluate_readiness(requirement, partitions=self._partition_states(requirement))

    def read_if_ready(
        self,
        requirement: ReadinessRequirement,
        *,
        year: int,
        columns: Sequence[str],
        filters: Mapping[str, object] | None = None,
    ) -> PanelReadOutcome:
        """Read a partition only if its dataset is ready, and say which happened.

        `PanelReadOutcome.rows_or_none` is `None` when the dataset is blocked and `()` when
        it is ready but nothing matched -- two different values for two different situations.
        `query()` collapses both to `[]`, which is precisely the ambiguity `V2-P1-013`'s
        "assert blocking, not an empty success" acceptance exists to prevent. Reading the
        plainly-named `rows` on a blocked outcome raises rather than answering `None`, so the
        one-line mistake that would re-collapse them (`if not outcome.rows:`) cannot pass
        quietly; see `PanelReadOutcome`.

        A blocked dataset short-circuits before any scan: nothing reads the partition file.
        `year` must be one of the years the requirement was assessed over, so a caller cannot
        vet one partition and then read another.

        Blocked and ready are the only two outcomes, and that now holds for a partition whose
        file is damaged too. The scan is wrapped: anything DuckDB raises out of it becomes a
        `PanelStorageError` naming the partition, instead of a bare
        `duckdb.InvalidInputException` escaping a method that promises a verdict.
        """
        if year not in requirement.years:
            raise PanelStorageError(
                f"year {year} is not among the years this requirement was assessed over "
                f"({sorted(set(requirement.years))})"
            )
        readiness = self.assess_readiness(requirement)
        if readiness.state == "blocked":
            return PanelReadOutcome(readiness=readiness, rows_or_none=None)
        try:
            rows = self.query(requirement.dataset, year=year, columns=columns, filters=filters)
        except PanelStorageError:
            raise
        except Exception as error:
            raise PanelStorageError(
                f"{requirement.dataset} year={year} passed readiness but could not be read: "
                f"{type(error).__name__}: {error}"
            ) from error
        return PanelReadOutcome(readiness=readiness, rows_or_none=tuple(rows))

    def _partition_states(self, requirement: ReadinessRequirement) -> tuple[PartitionState, ...]:
        """What the store can see about each requested year, as evidence for the rule table.

        Every fact a `PartitionState` carries is read here and handed over whole; the
        evaluator judges, this method looks. `content_hash` in particular is read from the
        same `panel_partitions` row that resolves the path -- it costs nothing extra, and
        dropping it (as an earlier version did, keeping only `path.is_file()`) is what left
        a coverage record's agreement with the partition it describes permanently unchecked.
        """
        years = sorted(set(requirement.years))
        if not self.catalog_path.exists():
            return tuple(_absent_partition(year) for year in years)
        states: list[PartitionState] = []
        with duckdb.connect(str(self.catalog_path), read_only=True) as connection:
            _check_catalog_schema_version(connection)
            catalogued = _table_exists(connection, "panel_partitions")
            for year in years:
                reference = (
                    self._lookup_with_connection(connection, requirement.dataset, year)
                    if catalogued
                    else None
                )
                if reference is None:
                    states.append(_absent_partition(year))
                    continue
                present = reference.path.is_file()
                states.append(
                    PartitionState(
                        year=year,
                        registered=True,
                        file_present=present,
                        file_readable=present and _looks_like_parquet(reference.path),
                        content_hash=reference.content_hash,
                        coverage=_read_coverage(connection, requirement.dataset, year),
                        path=reference.path,
                    )
                )
        return tuple(states)

    def _now(self) -> datetime:
        return _require_aware(self._clock(), "clock")

    def _ensure_catalog_schema(self, connection: duckdb.DuckDBPyConnection) -> None:
        """Verify the schema version, then create/upgrade every catalog table and re-stamp.

        The version check runs **first**, before any DDL. An earlier version created the
        tables first "so the stamp has somewhere to live", which meant a catalog stamped with
        an unknown version had five dropped tables silently rebuilt before the refusal
        fired -- the opposite of the error message's own "refusing to touch it".
        `_check_catalog_schema_version` needs no table to exist: a catalog with no
        `panel_catalog_meta` is v1 by definition and it returns.

        The stamp is then written with `DO UPDATE`, not `DO NOTHING`, because a `v1` catalog
        that has just been migrated forward has to *stop* being stamped `v1`.
        """
        _check_catalog_schema_version(connection)
        connection.execute(_CATALOG_DDL)
        for statement in _COVERAGE_DDL:
            connection.execute(statement)
        for table, column, column_type in _COVERAGE_ADDED_COLUMNS:
            if not _column_exists(connection, table, column):
                connection.execute(
                    f"ALTER TABLE {_quote_identifier(table, role='table')} "
                    f"ADD COLUMN {_quote_identifier(column)} {column_type}"
                )
        connection.execute(
            "INSERT INTO panel_catalog_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            [PANEL_CATALOG_SCHEMA_VERSION],
        )

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


def _absent_partition(year: int) -> PartitionState:
    """The state of a year the catalog has no row for: nothing is known, nothing is claimed."""
    return PartitionState(
        year=year,
        registered=False,
        file_present=False,
        file_readable=False,
        content_hash=None,
        coverage=None,
    )


def _build_scan_sql(
    partition_path: Path, columns: Sequence[str], filters: Mapping[str, object] | None
) -> tuple[str, list[object]]:
    if not columns:
        raise PanelStorageError("must request at least one column")
    column_list = ", ".join(_quote_identifier(name) for name in columns)
    parameters: list[object] = [str(partition_path)]
    where_sql = ""
    if filters:
        clauses = [f"{_quote_identifier(key, role='filter')} = ?" for key in filters]
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


def _table_exists(connection: duckdb.DuckDBPyConnection, name: str) -> bool:
    """Whether `name` is a table in this catalog.

    Needed because a catalog written before `V2-P1-003` has `panel_partitions` and none of
    the coverage tables, and the read path opens the file `read_only=True` so it cannot
    create them. Absence is answered as "no coverage recorded", not as a crash.
    """
    row = connection.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return row is not None


def _column_exists(connection: duckdb.DuckDBPyConnection, table: str, column: str) -> bool:
    """Whether `table.column` exists in this catalog.

    The read path opens the catalog `read_only=True` and so cannot run the `v1 -> v2`
    `ALTER TABLE`; it has to answer questions about a catalog that is still v1 on disk. This
    is how it tells "the column is not there" from "the value is NULL" without a write.
    """
    row = connection.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
        [table, column],
    ).fetchone()
    return row is not None


def _looks_like_parquet(path: Path) -> bool:
    """Whether `path` at least still carries Parquet's magic at both ends.

    Structural sanity, not content verification: it separates "a Parquet file is there" from
    "a file is there", which `Path.is_file()` cannot. A zero-byte file, a truncated write and
    a file overwritten with unrelated bytes all fail it. A file replaced by a *different but
    valid* Parquet file passes it -- catching that needs a digest of the file's bytes, which
    would mean re-hashing the whole partition on every gate check, and belongs to
    `V2-P1-012`'s deep health pass rather than to a fail-closed gate that has to be cheap
    enough to run on every read.
    """
    try:
        with path.open("rb") as handle:
            if handle.read(len(_PARQUET_MAGIC)) != _PARQUET_MAGIC:
                return False
            handle.seek(-len(_PARQUET_MAGIC), 2)
            return handle.read(len(_PARQUET_MAGIC)) == _PARQUET_MAGIC
    except OSError:
        return False


def _check_catalog_schema_version(connection: duckdb.DuckDBPyConnection) -> None:
    """Refuse a catalog stamped with a schema version this build does not understand.

    A catalog with no `panel_catalog_meta` table, or with the table but no stamp, predates
    `V2-P1-003` and is v1 by definition -- the coverage tables were purely additive, so
    nothing about its existing rows is ambiguous. A `v1` stamp is likewise readable, because
    this build knows exactly what a v1 catalog lacks and treats the gap as fail-closed rather
    than guessing (see `PANEL_CATALOG_SCHEMA_VERSIONS_READABLE`). A catalog stamped with an
    *unknown* version is a different matter: it was written by a build that knows something
    this one does not, and reading it would mean guessing at columns that may have been
    repurposed.

    Called by every public method that opens the catalog, including the three read paths that
    never look at a coverage table (`query`, `registered_years`, `profile_query`). `query()`
    in particular is the one method that reads `panel_partitions.relative_path` and opens the
    file it names -- the single path where a repurposed column would turn into reading the
    wrong bytes -- so exempting it would have left the stamp guarding everything except the
    thing most worth guarding.
    """
    if not _table_exists(connection, "panel_catalog_meta"):
        return
    row = connection.execute(
        "SELECT value FROM panel_catalog_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        return
    version = str(row[0])
    if version not in PANEL_CATALOG_SCHEMA_VERSIONS_READABLE:
        raise PanelStorageError(
            f"panel catalog is stamped {version!r}, which this build does not understand "
            f"(known: {sorted(PANEL_CATALOG_SCHEMA_VERSIONS_READABLE)}); refusing to touch it "
            "rather than misread a schema written by a newer version"
        )


def _write_coverage(
    connection: duckdb.DuckDBPyConnection, coverage: PartitionCoverage, *, recorded_at: datetime
) -> None:
    """Replace one partition's coverage record and all four of its census tables, atomically.

    A transaction rather than five independent statements: the census tables are cleared
    before they are refilled, so an interruption between the two would leave a coverage row
    advertising a partition with (say) zero subjects -- a lie that reads as data rather than
    as a missing record, and therefore *not* fail-closed. Rolling back leaves the previous
    record intact instead.
    """
    key: list[object] = [coverage.dataset, coverage.year]
    connection.execute("BEGIN TRANSACTION")
    try:
        for table in _COVERAGE_CHILD_TABLES:
            connection.execute(
                f"DELETE FROM {_quote_identifier(table, role='table')} "
                "WHERE dataset = ? AND year = ?",
                key,
            )
        connection.execute(
            """
            INSERT INTO panel_partition_coverage
                (dataset, year, provider_id, kind, schema_version, batch_digest, as_of,
                 fetched_at, row_count, date_timezone, last_event_time, max_available_time,
                 revised_row_count, recorded_at, partition_content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (dataset, year) DO UPDATE SET
                provider_id = excluded.provider_id,
                kind = excluded.kind,
                schema_version = excluded.schema_version,
                batch_digest = excluded.batch_digest,
                as_of = excluded.as_of,
                fetched_at = excluded.fetched_at,
                row_count = excluded.row_count,
                date_timezone = excluded.date_timezone,
                last_event_time = excluded.last_event_time,
                max_available_time = excluded.max_available_time,
                revised_row_count = excluded.revised_row_count,
                recorded_at = excluded.recorded_at,
                partition_content_hash = excluded.partition_content_hash
            """,
            [
                *key,
                coverage.provider_id,
                coverage.kind,
                coverage.schema_version,
                coverage.batch_digest,
                coverage.as_of,
                coverage.fetched_at,
                coverage.row_count,
                coverage.date_timezone,
                coverage.last_event_time,
                coverage.max_available_time,
                coverage.revised_row_count,
                recorded_at,
                coverage.partition_content_hash,
            ],
        )
        connection.executemany(
            "INSERT INTO panel_partition_subjects (dataset, year, subject) VALUES (?, ?, ?)",
            [(*key, subject) for subject in coverage.subjects],
        )
        connection.executemany(
            "INSERT INTO panel_partition_fields (dataset, year, ordinal, field_name, field_kind) "
            "VALUES (?, ?, ?, ?, ?)",
            [(*key, ordinal, item.name, item.kind) for ordinal, item in enumerate(coverage.fields)],
        )
        connection.executemany(
            "INSERT INTO panel_partition_dates (dataset, year, event_date, row_count) "
            "VALUES (?, ?, ?, ?)",
            [(*key, day.event_date, day.row_count) for day in coverage.dates],
        )
        if coverage.revisions:
            connection.executemany(
                "INSERT INTO panel_partition_revisions (dataset, year, revision_label, row_count) "
                "VALUES (?, ?, ?, ?)",
                [(*key, item.label, item.row_count) for item in coverage.revisions],
            )
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def _read_coverage(
    connection: duckdb.DuckDBPyConnection, dataset: str, year: int
) -> PartitionCoverage | None:
    """Read one coverage record back, normalising every instant to UTC.

    DuckDB hands a `TIMESTAMPTZ` back in the *session's* local zone, so the same stored
    instant reads as `2024-06-03 03:00:00-04:00` on one machine and `...+08:00` on another.
    The instant is identical and every comparison in this module is already correct on it,
    but `V2-P1-016`'s REST surface serialises these straight out, and a wire format whose
    offset depends on which host answered is not a wire format. Normalised on read, once,
    rather than at each of the consumers that must not each remember to.

    Tolerates a `panel-catalog/v1` catalog, which has no `partition_content_hash` column: the
    read path is `read_only=True` and cannot run the `ALTER TABLE` that adds it, so it reads
    the record with that field `None` -- which readiness blocks on rather than waves through.
    """
    if not _table_exists(connection, "panel_partition_coverage"):
        return None
    has_partition_hash = _column_exists(
        connection, "panel_partition_coverage", "partition_content_hash"
    )
    hash_projection = "partition_content_hash" if has_partition_hash else "NULL"
    key: list[object] = [dataset, year]
    row = connection.execute(
        f"""
        SELECT provider_id, kind, schema_version, batch_digest, as_of, fetched_at, row_count,
               date_timezone, last_event_time, max_available_time, revised_row_count,
               recorded_at, {hash_projection}
        FROM panel_partition_coverage WHERE dataset = ? AND year = ?
        """,
        key,
    ).fetchone()
    if row is None:
        return None
    subjects = connection.execute(
        "SELECT subject FROM panel_partition_subjects WHERE dataset = ? AND year = ? "
        "ORDER BY subject",
        key,
    ).fetchall()
    fields = connection.execute(
        "SELECT field_name, field_kind FROM panel_partition_fields WHERE dataset = ? AND year = ? "
        "ORDER BY ordinal",
        key,
    ).fetchall()
    dates = connection.execute(
        "SELECT event_date, row_count FROM panel_partition_dates WHERE dataset = ? AND year = ? "
        "ORDER BY event_date",
        key,
    ).fetchall()
    revisions = connection.execute(
        "SELECT revision_label, row_count FROM panel_partition_revisions "
        "WHERE dataset = ? AND year = ? ORDER BY revision_label",
        key,
    ).fetchall()
    return PartitionCoverage(
        dataset=dataset,
        year=year,
        provider_id=str(row[0]),
        kind=str(row[1]),
        schema_version=str(row[2]),
        batch_digest=str(row[3]),
        as_of=_as_utc(row[4]),
        fetched_at=_as_utc(row[5]),
        row_count=int(row[6]),
        date_timezone=str(row[7]),
        last_event_time=_as_utc(row[8]),
        max_available_time=_as_utc(row[9]),
        revised_row_count=int(row[10]),
        recorded_at=_as_utc(row[11]),
        partition_content_hash=None if row[12] is None else str(row[12]),
        subjects=tuple(str(entry[0]) for entry in subjects),
        fields=tuple(FieldCoverage(name=str(entry[0]), kind=str(entry[1])) for entry in fields),
        dates=tuple(
            DateCoverage(event_date=cast(date, entry[0]), row_count=int(entry[1]))
            for entry in dates
        ),
        revisions=tuple(
            RevisionCoverage(label=str(entry[0]), row_count=int(entry[1])) for entry in revisions
        ),
    )


def _validated_coverage(coverage: PartitionCoverage) -> PartitionCoverage:
    """Check every value of `coverage` before any of it reaches the catalog.

    This is the *only* place these rules live. `panel/catalog.py`'s dataclasses have no
    validating `__post_init__` on purpose: the previous task reproduced twice that a nominal
    type is not a boundary -- a duck-typed stand-in satisfies every attribute read, and a
    subclass can override `__post_init__` away -- so a rule enforced at construction is a
    rule a caller can skip. Enforcing it here, where the values are actually used, cannot be
    skipped, and keeping it here *only* means there is no second copy to drift.

    The checks are values, not types: `type(...) is str` rather than `isinstance`, because
    `bool` is an `int` and `datetime` is a `date`, and both would otherwise pass a check they
    do not satisfy. The three internal-consistency rules (dates summing to `row_count`,
    revisions summing to `row_count` when present, `revised_row_count` within `row_count`)
    are what stop a coverage record from describing a partition that could not exist.
    """
    _validate_dataset(coverage.dataset)
    if type(coverage.year) is not int:
        raise PanelStorageError(f"coverage year must be an int; got {coverage.year!r}")
    for name in ("provider_id", "kind", "schema_version", "batch_digest"):
        _require_text(f"coverage {name}", getattr(coverage, name))
    _require_timezone_name(coverage.date_timezone)
    for name in ("as_of", "fetched_at", "last_event_time", "max_available_time"):
        _require_aware(getattr(coverage, name), f"coverage {name}")

    row_count = coverage.row_count
    if type(row_count) is not int or row_count < 1:
        raise PanelStorageError(f"coverage row_count must be a positive int; got {row_count!r}")
    revised = coverage.revised_row_count
    if type(revised) is not int or not 0 <= revised <= row_count:
        raise PanelStorageError(
            f"coverage revised_row_count must be between 0 and row_count ({row_count}); "
            f"got {revised!r}"
        )

    if not coverage.subjects:
        raise PanelStorageError("coverage must name at least one subject")
    for subject in coverage.subjects:
        _require_text("subject", subject)
    if len(set(coverage.subjects)) != len(coverage.subjects):
        raise PanelStorageError("coverage subjects must be distinct")

    if not coverage.fields:
        raise PanelStorageError("coverage must name at least one field")
    for column in coverage.fields:
        _require_text("field name", column.name)
        _require_text("field kind", column.kind)
    field_names = [column.name for column in coverage.fields]
    if len(set(field_names)) != len(field_names):
        raise PanelStorageError("coverage field names must be distinct")

    if not coverage.dates:
        raise PanelStorageError("coverage must name at least one event date")
    for day in coverage.dates:
        if type(day.event_date) is not date:
            raise PanelStorageError(f"coverage event_date must be a date; got {day.event_date!r}")
        if type(day.row_count) is not int or day.row_count < 1:
            raise PanelStorageError(
                f"coverage date {day.event_date!r} must carry a positive row_count; "
                f"got {day.row_count!r}"
            )
    if len({day.event_date for day in coverage.dates}) != len(coverage.dates):
        raise PanelStorageError("coverage event dates must be distinct")
    # The date census has to live inside the partition it describes. Without this, a batch of
    # 2019 rows written with `year=2024` produced a coverage record whose dates were all in
    # 2019 and whose partition key said 2024 -- and a `required_dates` naming those 2019 days
    # against `years=(2024,)` then reported `ready`, because the pooled date check never asks
    # which year the dates came from.
    off_year = sorted({day.event_date.year for day in coverage.dates} - {coverage.year})
    if off_year:
        raise PanelStorageError(
            f"coverage date census for year={coverage.year} carries dates from {off_year}; "
            "every event date must fall inside the partition's own year"
        )
    if sum(day.row_count for day in coverage.dates) != row_count:
        raise PanelStorageError(
            "coverage date census must account for every row: "
            f"{sum(day.row_count for day in coverage.dates)} != {row_count}"
        )

    for revision in coverage.revisions:
        _require_text("revision label", revision.label)
        if type(revision.row_count) is not int or revision.row_count < 1:
            raise PanelStorageError(
                f"revision {revision.label!r} must carry a positive row_count; "
                f"got {revision.row_count!r}"
            )
    labels = [revision.label for revision in coverage.revisions]
    if len(set(labels)) != len(labels):
        raise PanelStorageError("coverage revision labels must be distinct")
    revision_rows = sum(revision.row_count for revision in coverage.revisions)
    if coverage.revisions and revision_rows != row_count:
        raise PanelStorageError(
            f"coverage revision census must account for every row: {revision_rows} != {row_count}"
        )
    return coverage


def _validated_requirement(requirement: ReadinessRequirement) -> ReadinessRequirement:
    """Check a `ReadinessRequirement`'s values before any of them decides a verdict.

    `type(day) is date` rather than `isinstance`: `datetime` subclasses `date`, so an
    `isinstance` check would admit a value that can never equal an observed `date` and would
    turn every complete partition into a permanent phantom `date_gap`. The same reasoning
    applies to `years` -- a `bool` is an `int`, and `True` would silently mean year 1.

    The *containers* are checked before their elements, which an earlier version of this
    function did not do: it iterated straight into `years`/`required_dates`/
    `required_subjects`, so passing `None` for any of them raised a bare
    `TypeError: 'NoneType' object is not iterable` out of the middle of a validator whose
    entire job is to name the malformed input. Each is now required to be a `tuple` -- the
    type the dataclass declares -- and `None` is legal for exactly the three checks that can
    be waived, where it *means* waived rather than "forgot to pass one".
    """
    _validate_dataset(requirement.dataset)
    _require_aware(requirement.as_of, "ReadinessRequirement.as_of")
    _require_tuple("years", requirement.years, optional=False)
    for name in ("required_dates", "required_subjects", "required_fields"):
        _require_tuple(name, getattr(requirement, name), optional=True)
    for year in requirement.years:
        if type(year) is not int:
            raise PanelStorageError(f"requirement years must be ints; got {year!r}")
    for day in requirement.required_dates or ():
        if type(day) is not date:
            raise PanelStorageError(
                f"required_dates must hold plain date values, not datetimes; got {day!r}"
            )
    for role, values in (
        ("required_subjects", requirement.required_subjects),
        ("required_fields", requirement.required_fields),
    ):
        for value in values or ():
            _require_text(role, value)
    if requirement.max_staleness is not None and type(requirement.max_staleness) is not timedelta:
        raise PanelStorageError(
            f"max_staleness must be a timedelta or None; got {requirement.max_staleness!r}"
        )
    return requirement


def _require_tuple(role: str, value: object, *, optional: bool) -> None:
    """A tuple, or `None` where `None` is a meaning rather than an omission."""
    if optional and value is None:
        return
    if type(value) is not tuple:
        allowed = "a tuple or None" if optional else "a tuple"
        raise PanelStorageError(f"{role} must be {allowed}; got {value!r}")


def _require_text(role: str, value: object) -> None:
    """A non-empty string with no surrounding whitespace, within a sane length bound.

    Whitespace is refused rather than stripped, exactly as
    `domain/panel_batch.py::_require_text` refuses it: a catalog whose stored `provider_id`
    differs from the one the caller passed is a catalog that lies quietly.
    """
    if type(value) is not str or not value or value != value.strip():
        raise PanelStorageError(
            f"{role} must be a non-empty string with no surrounding whitespace; got {value!r}"
        )
    if len(value) > _MAX_TEXT_LENGTH:
        raise PanelStorageError(f"{role} must be at most {_MAX_TEXT_LENGTH} characters")


def _as_utc(value: object) -> datetime:
    """A `TIMESTAMPTZ` DuckDB handed back, re-expressed in UTC. Same instant, fixed offset."""
    instant = cast(datetime, value)
    return instant.astimezone(UTC)


def _require_aware(value: datetime, role: str) -> datetime:
    """Reject a naive datetime instead of letting DuckDB guess a zone for it.

    A naive value bound to a `TIMESTAMPTZ` column is interpreted in the machine's local
    zone, so the same code would record a different instant depending on where it ran.
    """
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PanelStorageError(f"{role} must be a timezone-aware datetime; got {value!r}")
    return value


def _require_timezone_name(value: object) -> None:
    """Reject a `date_timezone` label that is not a real IANA zone.

    The label is never used to compute anything here -- readiness arithmetic is entirely on
    instants and `date` values -- but it is the record of *which convention* the stored dates
    were derived in, and a label naming no real zone is a record that cannot be checked
    against `V2-P1-004`'s trading calendar later. `ZoneInfo` also refuses absolute and
    `..`-traversing keys itself, so this doubles as the boundary check for a value that is,
    ultimately, a lookup into the tz database on disk.
    """
    if type(value) is not str or not value:
        raise PanelStorageError(f"date_timezone must be a non-empty string; got {value!r}")
    try:
        ZoneInfo(value)
    except (KeyError, ValueError, OSError) as error:
        raise PanelStorageError(f"date_timezone {value!r} is not a known IANA time zone") from error


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
