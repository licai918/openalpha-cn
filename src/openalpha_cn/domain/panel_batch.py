"""The columnar provider batch contract for the panel data plane (`V2-P1-002`).

## Why a second batch contract exists

`providers/base.py`'s `ProviderBatch`/`ProviderRecord` pair is a *row* contract: every row
is a frozen pydantic model whose payload is canonicalised to JSON at construction, whose
`record_id` is a content address of that row, and whose batch-level `payload_digest`
re-serialises every record on **each access** (it is a `computed_field`, which pydantic does
not cache). Measured on this task's development machine at 2,000 rows x 5 float fields,
best of five runs each:

    ProviderRecord construction  12.2 ms  ->  6.10 us/row
    ProviderBatch construction    5.3 ms  ->  2.63 us/row  (is_visible_at, once per record)
    payload_digest, 1st access   10.5 ms  ->  5.25 us/row
    payload_digest, 2nd access   10.2 ms  <-  no cheaper; a computed_field is not cached
    ----------------------------------------------------
    end to end                   27.9 ms  -> 13.9 us/row

The same source data through `ColumnarPanelBatch` -- construction, validation, digest and
`to_rows()` -- costs **1.80 us/row** (3.6 ms end to end: `TimelineColumns` 0.66 + batch
including digest 1.00 + `PanelColumn`s 0.09 + `to_rows()` 0.03), a **7.8x** reduction. Every
number here, in `tests/contract/panel/test_columnar_batch_parity.py`, and in the release
ledger's `OA-PANEL-002` row comes from that one re-measurement; an earlier version had the
three copies disagreeing (1.68 vs 1.79 us/row, 8.2x vs 7.7x). ADR-0002 fixes panel scale at 5,534
listed names x ~2,440 trading days ~= 1.35e7 rows per field, so those constants work out to
~188 s versus ~24 s of pure contract overhead per field, single-threaded, before any I/O --
across eight dataset groups, every one of which has to be backfilled over full history. Note
the shape of the row-wise cost: `validate_result`'s `any(...)` short-circuits on the
*violating* case, so a clean batch is the expensive one.

None of that expense buys the panel plane anything. Per-row content addressing is a
correctness property of the *evidence* plane, where a few thousand discrete events each need
to be independently re-provable on read (ADR-0002's "Consequences" section says so
explicitly, and this module does not weaken it: `ProviderBatch` and `ProviderRecord` are
untouched and remain the contract for evidence). Panel rows are not cited individually;
they are scanned, projected, and joined in bulk.

## What replaces what

| `ProviderRecord` / `ProviderBatch` | `ColumnarPanelBatch` |
|---|---|
| `subject` (per row) | `subjects`, one string column |
| `kind` (per row) | `kind`, one value per batch -- a partition is homogeneous |
| `timeline: Timeline` (per row) | `TimelineColumns`, the same four clocks transposed |
| `source_uri` (per row) | `source_uri`, one value per batch |
| `summary` (per row) | **dropped** -- prose for a human citation is an evidence-plane affordance |
| `payload: JsonValue` (per row) | `columns`, typed `PanelColumn`s -- the point of going columnar |
| `record_id` (per-row content hash) | **not replaced** -- see "Integrity" below |
| `payload_digest` (uncached, per access) | `content_digest`, computed once at construction |
| `status` / `no_data_reason` | unchanged in spirit: an empty result stays explicit |

## Integrity, and what it does *not* claim

Dropping `record_id` drops a real guarantee, so it is replaced rather than deleted.
`content_digest` is a SHA-256 over a canonical byte stream covering the batch header
(schema version, provider, dataset, kind, both clocks, status, no-data reason, row count)
followed by every storage column in declared order, each prefixed with its own name, kind,
and length. Any single changed value, renamed column, reordered column, retyped column, or
changed row count changes it -- proven in `tests/unit/domain/test_panel_batch.py` and,
across a real Parquet + DuckDB round trip, in
`tests/integration/panel/test_panel_batch_ingest.py`.

What it deliberately does **not** provide: content addressing of an individual row. A
corrupted batch is detectable, but the digest cannot say *which* row changed, and no row can
be looked up by its content. That is the property the evidence plane keeps and the panel
plane gives up, consciously, for two orders of magnitude of throughput.

The digest **is** written down, since `V2-P1-003`: `panel_ingest.write_panel_batch()` records
it as `panel_partition_coverage.batch_digest`, so a partition sitting on disk can later be
re-proved against the digest the provider's batch carried. It does not replace the store's own
`content_hash`, which covers only `(dataset, year, column names and SQL types, rows)` and
exists to make re-writing identical content a no-op; this digest additionally covers
`provider_id`, `kind`, `as_of`, `fetched_at`, `source_uri` and `schema_version`, so a refetch
of unchanged rows changes it. `panel_ingest.py`'s module docstring states why both are kept.

Two encoding details are load-bearing:

- **Every timestamp is normalised to UTC before it is hashed**, and then hashed as an
  integer count of microseconds since the epoch. The normalisation is what makes the digest
  survive storage: DuckDB hands `TIMESTAMPTZ` values back in the session's timezone rather
  than UTC (measured against DuckDB 1.5 during this task -- an instant written as
  `2024-06-28T07:00Z` reads back tagged `America/Toronto`), so hashing the value as it
  arrives would change the digest even though no information did. The choice of *integer
  microseconds* over `isoformat()` on top of that is cost, not correctness: both are
  representation-independent once the value is UTC, but `isoformat()` measured 0.443 us per
  value against 0.095 us for the subtraction, and a panel batch carries at least five
  timestamp columns -- that one substitution would more than double this contract's per-row
  cost. The integer form is exact at `datetime`'s full microsecond resolution.
- Column bodies are JSON arrays produced by the stdlib encoder rather than joined text. Two
  properties matter: `json.dumps` escapes every control character, so the `\x1e` column
  separator can never occur inside an encoded value; and it distinguishes `null` from the
  string `"None"`, which a `str()`-based encoder collapses into the same bytes (a missing
  observation and a literal `"None"` are both legal values of a string column here).

## Point-in-time, and why the batch-level check is not weaker

`ProviderBatch.validate_result` rejects a batch containing any record that was not yet
available at the request's `as_of`. This contract makes the same check in one step:
`all(t <= as_of)` holds **iff** `max(t) <= as_of`, and the maximum is attained by an actual
row, so a single late row among thousands still fails -- the maximum *is* that row. The
implementation then hands that exact row's reconstructed `Timeline` to the same
`is_visible_at` the row-wise contract calls, rather than re-implementing the comparison, and
names the offending row index in the error (which `ProviderBatch` does not).
`tests/contract/panel/test_columnar_batch_parity.py` proves the equivalence by running both
contracts over the same corpus, including 200 randomised cases and every boundary.

## Why plain dataclasses and not pydantic

Pydantic validates per element. A `tuple[float, ...]` field holding a full year of one
field's panel data would be validated value by value on every construction, which is the
cost this contract exists to remove. `panel/store.py`'s `ColumnSpec`/`PartitionRef` set the
same precedent on the same plane for the same reason. Validation here is explicit and
written to be O(row_count) with C-level primitives (`all()`, `max()`, `tuple.index()`,
`json.dumps` over a whole column) rather than O(row_count) Python-level object constructions.

Consequently this contract is not registered in `domain/versioning.py`'s `ContractVersions`
registry the way `ProviderBatch` is: that registry dispatches *stored JSON rows* back to a
pydantic model, and a panel batch is never stored as JSON -- it is stored as Parquet, whose
schema is the partition's own.

`schema_version` is carried on the batch, hashed into `content_digest`, refused by
`merge_panel_batches` when two batches disagree on it, and copied onto the coverage record
`panel_ingest.panel_coverage` builds. What makes a future `panel-batch/v2` **detectable** is
none of those: it is `panel/catalog.py`'s `PANEL_BATCH_SCHEMA_VERSIONS_READABLE`, checked by
`panel/store.py::_check_batch_schema_version` on the way into the catalog and on the way back
out. This paragraph used to claim that carrying and hashing the value was itself the
detection, which the P1 review disproved directly: a coverage record stamped
`panel-batch/v99` was accepted by `record_coverage` and `assess_readiness` returned `ready`
with `issues == []`. A value that is stored but never compared against a known set is
provenance, not a version gate.

## Layering

This module lives in `domain/` because both sides of the seam need it: `providers/` produces
a `ColumnarPanelBatch` (see `providers/base.py`'s `PanelDataProvider`) and `panel/` consumes
one (through `panel_ingest.py`). Placing it in either of those packages would create a
`panel -> providers` or `providers -> panel` edge between two peers. It imports nothing but
the standard library and `domain/time.py`, so the `domain-purity` contract in
`pyproject.toml` (ADR-0003) holds unchanged, and it deliberately speaks *logical* column
kinds (`"float"`, `"timestamp"`, ...) rather than DuckDB SQL types -- the translation table
lives in `panel_ingest.py`, on the side of the seam that already knows about DuckDB.

## What this contract validates, and where it stops trusting itself

Column names and `dataset` are validated at the boundary because both end up in a place
where an arbitrary string is dangerous: a name is interpolated into `panel/store.py`'s SQL,
and `dataset` becomes a directory under the store's root. `_check_shape` re-applies the name
and kind rules to every column the caller hands over rather than assuming
`PanelColumn.__post_init__` ran -- `columns` is an ordinary tuple field, and a duck-typed
column object used to travel straight through it into the store's DDL (reproduced against
`cb9e8f4`; see `_check_shape`'s own docstring and
`tests/integration/panel/test_panel_batch_ingest.py`). The storage layer no longer depends on
that being done correctly here either: `panel/store.py` escapes every identifier it
interpolates and takes its SQL types from a closed set.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal, cast

from openalpha_cn.domain.time import Timeline, ensure_aware, is_visible_at

PanelColumnKind = Literal["boolean", "float", "integer", "string", "timestamp"]

PANEL_BATCH_SCHEMA_VERSION: Final[str] = "panel-batch/v1"

CLOCK_COLUMN_NAMES: Final[tuple[str, ...]] = (
    "event_time",
    "available_time",
    "ingested_time",
    "revision_time",
)

SUBJECT_COLUMN_NAME: Final[str] = "subject"

RESERVED_COLUMN_NAMES: Final[frozenset[str]] = frozenset((SUBJECT_COLUMN_NAME, *CLOCK_COLUMN_NAMES))

MAX_IDENTIFIER_LENGTH: Final[int] = 63

MAX_SOURCE_URI_LENGTH: Final[int] = 2048
"""The longest `source_uri` a `ColumnarPanelBatch` will carry.

Named, exported and asserted against rather than written as a literal inside
`__post_init__`, because a *producer* has to stay under it: `providers/tushare.py` summarises
a whole-market batch's subject list precisely so the URI it builds fits here, and its
`MAX_PANEL_SOURCE_URI_LENGTH` is required to *be* this constant by
`tests/contract/providers/test_tushare_adj_factor.py::
test_the_producer_side_uri_bound_is_the_contracts_own_constant`. When the two were separate
literals and drifted apart, every fetch of 202 or so subjects built a URI this contract
refused -- outside the producer's decode `try`, so the fetch died with a contract error
instead of a provider failure, and no test noticed.
"""

# A plain, unquoted SQL identifier: an ASCII letter or underscore followed by up to
# `MAX_IDENTIFIER_LENGTH - 1` more letters, digits or underscores. The bound is interpolated
# from the constant rather than written twice, so the advertised limit and the enforced one
# cannot drift apart.
# Matched with `re.fullmatch`, never `match` with a trailing `$`: `$` also matches immediately
# *before* a final newline, so `"close\n"` satisfied the pattern under `match` and was
# accepted as a column name (reproduced against `cb9e8f4`, written into Parquet and read back
# again), and a 63-character name plus a newline passed the length limit the same way.
#
# The rule exists because `panel/store.py` puts column names into SQL text. That module now
# escapes every identifier it interpolates, so this is no longer the only thing standing
# between a hostile name and executed SQL -- it is the contract's own narrower statement about
# what a panel column may be called, and `_check_shape` re-applies it to every column the
# caller supplies rather than trusting that the object came from `PanelColumn.__post_init__`.
_IDENTIFIER = re.compile(rf"[A-Za-z_][A-Za-z0-9_]{{0,{MAX_IDENTIFIER_LENGTH - 1}}}")

# Exact types, not `isinstance`, for the four scalar kinds: `bool` is a subclass of `int` and
# would otherwise pass as `integer` while hashing as `true` rather than `1`, and an `int` in
# a `float` column would hash as `1` rather than `1.0` -- two batches holding the same
# numbers would then carry different digests. `timestamp` uses `isinstance` because the value
# is normalised through `ensure_aware` and hashed as an integer instant, so a `datetime`
# subclass is unambiguous.
_EXACT_TYPES: Final[dict[str, type]] = {
    "boolean": bool,
    "float": float,
    "integer": int,
    "string": str,
}

PANEL_COLUMN_KINDS: Final[frozenset[str]] = frozenset({*_EXACT_TYPES, "timestamp"})
"""Every logical column kind this contract admits, as data rather than as a `Literal`.

`PanelColumnKind` stops untyped values at type-check time; this set stops them at runtime,
which is where a kind read out of a provider's config file arrives. `_check_shape` uses it to
re-validate the kind of every caller-supplied column, because a kind is the key
`panel_ingest.PANEL_DUCKDB_TYPES` looks a DuckDB SQL type up by.
"""

_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND: Final[timedelta] = timedelta(microseconds=1)
_COLUMN_SEPARATOR: Final[str] = "\x1e"


class PanelBatchError(ValueError):
    """Raised for any malformed columnar panel batch.

    A `ValueError` subclass so that callers written against `ProviderBatch`'s pydantic
    `ValueError`s keep working, while code that cares about this contract specifically can
    catch it precisely.
    """


def validate_panel_dataset(dataset: str) -> None:
    """Reject any `dataset` that is not a single, plain path segment.

    Byte-for-byte the rule `panel/store.py::_validate_dataset` enforces, applied one layer
    earlier because `write_panel_batch()` hands `batch.dataset` to
    `PanelStore.write_partition()` as a path component. `domain/` must not import `panel/`
    (that is the upward direction the layering forbids), so the rule is duplicated rather
    than shared; `tests/unit/domain/test_panel_batch.py::
    test_the_contract_and_the_store_agree_on_every_rejected_dataset` is what keeps the two
    copies from drifting apart.
    """
    if not dataset:
        raise PanelBatchError("dataset must not be empty")
    if dataset != dataset.strip():
        raise PanelBatchError(
            f"dataset must not have leading or trailing whitespace; got {dataset!r}"
        )
    segment = Path(dataset)
    if segment.is_absolute() or segment.name != dataset or dataset in {".", ".."}:
        raise PanelBatchError(
            "dataset must be a single, plain path segment (no '/', no '..', not "
            f"absolute); got {dataset!r}"
        )


def validate_panel_identifier(name: str, *, role: str = "column") -> None:
    """Reject any identifier that is not a plain, unquoted SQL name (see `_IDENTIFIER`).

    `fullmatch`, not `match`: with `match` the pattern's trailing `$` also matched before a
    final newline, so `"close\\n"` was accepted -- and so was a name one character over the
    advertised 63-character limit, as long as the extra character was that newline.
    """
    if not _IDENTIFIER.fullmatch(name):
        raise PanelBatchError(
            f"{role} name must match {_IDENTIFIER.pattern} in full (a plain identifier: "
            f"ASCII letters, digits and underscore, not starting with a digit, at most "
            f"{MAX_IDENTIFIER_LENGTH} characters); got {name!r}"
        )


@dataclass(frozen=True, slots=True)
class PanelColumn:
    """One named, typed column of a panel batch: `PanelColumn("close", "float", (10.5, 9.5))`.

    `values` carries exactly one entry per row, in the batch's row order. `None` means a
    missing observation and is permitted in any data column (panel data is genuinely sparse
    -- a suspended name has no close); it is *not* permitted in the subject column or in any
    of the four clock columns, which every row must carry.
    """

    name: str
    kind: PanelColumnKind
    values: tuple[object, ...]

    def __post_init__(self) -> None:
        validate_panel_identifier(self.name)
        values = tuple(self.values)
        _check_column_types(self.name, self.kind, values)
        if self.kind == "timestamp":
            values = tuple(
                None if value is None else ensure_aware(cast(datetime, value)) for value in values
            )
        object.__setattr__(self, "values", values)


def _check_column_types(name: str, kind: PanelColumnKind, values: tuple[object, ...]) -> None:
    """Type-check a whole column with one C-level `all()`, falling back to a Python scan
    only to locate the offending row for the error message."""
    if kind == "timestamp":
        if all(value is None or isinstance(value, datetime) for value in values):
            return
    else:
        expected = _EXACT_TYPES.get(kind)
        if expected is None:
            raise PanelBatchError(f"column {name!r} has unknown kind {kind!r}")
        if all(value is None or type(value) is expected for value in values):
            return
    index, offender = next(
        (index, value)
        for index, value in enumerate(values)
        if value is not None and not _matches_kind(kind, value)
    )
    raise PanelBatchError(
        f"column {name!r} row {index}: expected {kind}, got {type(offender).__name__}"
    )


def _matches_kind(kind: PanelColumnKind, value: object) -> bool:
    if kind == "timestamp":
        return isinstance(value, datetime)
    return type(value) is _EXACT_TYPES[kind]


@dataclass(frozen=True, slots=True, kw_only=True)
class TimelineColumns:
    """`Timeline`'s four clocks, transposed: one aligned column each instead of one
    `Timeline` object per row.

    Enforces exactly what `Timeline.__post_init__` enforces, row for row -- every value
    timezone-aware and normalised to UTC, `ingested_time` and `revision_time` never before
    that row's `available_time` -- plus the shape invariant a row model gets for free, that
    all four columns have the same length.
    """

    event_time: tuple[datetime, ...] = ()
    available_time: tuple[datetime, ...] = ()
    ingested_time: tuple[datetime, ...] = ()
    revision_time: tuple[datetime, ...] = ()
    _columns: tuple[PanelColumn, ...] = field(init=False, default=(), repr=False, compare=False)

    def __post_init__(self) -> None:
        expected = len(tuple(self.event_time))
        columns: list[PanelColumn] = []
        for name in CLOCK_COLUMN_NAMES:
            values = tuple(cast(tuple[object, ...], getattr(self, name)))
            if len(values) != expected:
                raise PanelBatchError(
                    f"clock column {name!r} has {len(values)} values, expected {expected}: "
                    "every clock column must carry exactly one value per row"
                )
            _reject_missing_clock_values(name, values)
            # `PanelColumn` is the single implementation of "a timestamp column is aware and
            # normalised to UTC", so the clocks go through it rather than repeating that rule
            # here; the only thing it does not enforce is non-nullability, which the scan
            # above adds. Building the columns now (instead of on demand in `as_columns()`)
            # also means the normalising pass runs exactly once per batch.
            column = PanelColumn(name, "timestamp", values)
            object.__setattr__(self, name, cast(tuple[datetime, ...], column.values))
            columns.append(column)
        object.__setattr__(self, "_columns", tuple(columns))
        self._check_ordering()

    @property
    def row_count(self) -> int:
        return len(self.available_time)

    def row_timeline(self, index: int) -> Timeline:
        """Rebuild the `Timeline` of a single row -- the bridge back to the row-wise
        contract, used by the point-in-time check's error path and by the equivalence
        tests."""
        return Timeline(
            event_time=self.event_time[index],
            available_time=self.available_time[index],
            ingested_time=self.ingested_time[index],
            revision_time=self.revision_time[index],
        )

    def as_columns(self) -> tuple[PanelColumn, ...]:
        """The four clocks as storage columns, in `CLOCK_COLUMN_NAMES` order."""
        return self._columns

    def _check_ordering(self) -> None:
        """Find the first row violating `Timeline`'s ordering rule with a cheap scan, then
        let `Timeline` itself raise. The scan's predicate is `Timeline.__post_init__`'s own
        (`ingested_time`/`revision_time` may not precede `available_time`); handing the row
        to the real model is what produces the message, so the wording can never drift."""
        available = self.available_time
        for column in (self.ingested_time, self.revision_time):
            if any(value < bound for value, bound in zip(column, available, strict=True)):
                index = next(
                    index
                    for index, (value, bound) in enumerate(zip(column, available, strict=True))
                    if value < bound
                )
                self.row_timeline(index)


def _reject_missing_clock_values(name: str, values: tuple[object, ...]) -> None:
    """Clock columns are the one place `PanelColumn`'s nullability is too permissive: a
    missing observation is ordinary in a data column and impossible in a clock, because
    every row's `Timeline` needs all four."""
    if None not in values:
        return
    index = values.index(None)
    raise PanelBatchError(
        f"clock column {name!r} row {index}: expected an aware datetime, got None; "
        "clock columns are never nullable"
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ColumnarPanelBatch:
    """An explicit success or no-data panel result for one provider request, held column-wise.

    See the module docstring for the contract this replaces, the integrity guarantee that
    stands in for per-row content addressing, and the proof that its point-in-time check is
    equivalent to `ProviderBatch`'s per-record one.
    """

    provider_id: str
    dataset: str
    kind: str
    as_of: datetime
    fetched_at: datetime
    status: Literal["success", "no_data"]
    subjects: tuple[str, ...] = ()
    timeline: TimelineColumns = field(default_factory=TimelineColumns)
    columns: tuple[PanelColumn, ...] = ()
    source_uri: str | None = None
    no_data_reason: str | None = None
    schema_version: Literal["panel-batch/v1"] = "panel-batch/v1"
    content_digest: str = field(init=False, default="")
    _storage_columns: tuple[PanelColumn, ...] = field(init=False, default=(), repr=False)

    def __post_init__(self) -> None:
        validate_panel_dataset(self.dataset)
        _require_text("provider_id", self.provider_id, 128)
        _require_text("kind", self.kind, 64)
        if self.source_uri is not None and len(self.source_uri) > MAX_SOURCE_URI_LENGTH:
            raise PanelBatchError(f"source_uri must be at most {MAX_SOURCE_URI_LENGTH} characters")
        object.__setattr__(self, "as_of", ensure_aware(self.as_of))
        object.__setattr__(self, "fetched_at", ensure_aware(self.fetched_at))
        object.__setattr__(self, "subjects", _validated_subjects(self.subjects))
        object.__setattr__(self, "columns", tuple(self.columns))
        self._check_status()
        self._check_shape()
        self._check_visible_at_as_of()
        object.__setattr__(
            self,
            "_storage_columns",
            (
                PanelColumn(SUBJECT_COLUMN_NAME, "string", cast(tuple[object, ...], self.subjects)),
                *self.timeline.as_columns(),
                *self.columns,
            ),
        )
        object.__setattr__(self, "content_digest", self._compute_digest())

    @property
    def row_count(self) -> int:
        return len(self.subjects)

    def storage_columns(self) -> tuple[PanelColumn, ...]:
        """Every column that gets persisted, in partition order: `subject`, the four clocks,
        then the caller's own columns. Built once at construction, not per call."""
        return self._storage_columns

    def to_rows(self) -> tuple[tuple[object, ...], ...]:
        """Transpose to the row block `PanelStore.write_partition()` takes.

        One C-level `zip` over the column tuples -- no per-row object is constructed beyond
        the row tuples the storage API itself requires, which is the whole point of the
        contract. Measured at 0.03 us/row, against 6.18 us/row for the row-wise path's
        per-record model construction alone.
        """
        return tuple(zip(*(column.values for column in self._storage_columns), strict=True))

    def row_timeline(self, index: int) -> Timeline:
        """Rebuild one row's `Timeline` -- the bridge back to the row-wise contract."""
        return self.timeline.row_timeline(index)

    def _check_status(self) -> None:
        if self.status == "success":
            if not self.subjects:
                raise PanelBatchError("success requires at least one row")
            if self.no_data_reason is not None:
                raise PanelBatchError("success cannot include no_data_reason")
            return
        if self.subjects or self.columns or self.timeline.row_count:
            raise PanelBatchError("no_data cannot include rows")
        if not self.no_data_reason:
            raise PanelBatchError("no_data requires no_data_reason")

    def _check_shape(self) -> None:
        """Check the caller's columns against the batch, re-validating each one's identity.

        Name and kind are re-checked here rather than trusted from `PanelColumn`, because
        `columns` is an ordinary tuple field and nothing about a *nominal* type stops a
        caller from putting some other object in it. A frozen dataclass with `name`, `kind`
        and `values` attributes satisfies every read this contract and `panel_ingest`
        perform, so a hostile column name used to travel all the way into
        `PanelStore.write_partition`'s DDL -- reproduced against `cb9e8f4` with a plain
        duck-typed stand-in, which wrote an attacker-named DuckDB file to disk while
        `write_panel_batch()` returned normally. The `isinstance` check below closes the
        duck-typed case; the two explicit re-validations close the subclass case, where
        `PanelColumn.__post_init__` can be overridden away.

        Both checks are O(1) per column, not per row, so this costs nothing at panel scale.
        Values are deliberately *not* re-scanned: they reach DuckDB as bound `?` parameters,
        never as SQL text, so they are not a boundary in the sense name and kind are, and
        re-scanning them would restore exactly the per-row cost this contract removes.
        """
        expected = self.row_count
        if self.timeline.row_count != expected:
            raise PanelBatchError(
                f"timeline carries {self.timeline.row_count} rows but the batch has "
                f"{expected}: every clock column must align with the subject column"
            )
        seen: set[str] = set()
        for column in self.columns:
            if not isinstance(column, PanelColumn):
                raise PanelBatchError(
                    f"columns must contain PanelColumn instances; got {type(column).__name__}"
                )
            validate_panel_identifier(column.name)
            if column.kind not in PANEL_COLUMN_KINDS:
                raise PanelBatchError(
                    f"column {column.name!r} has unknown kind {column.kind!r}; expected one "
                    f"of {sorted(PANEL_COLUMN_KINDS)}"
                )
            if column.name in RESERVED_COLUMN_NAMES:
                raise PanelBatchError(
                    f"column {column.name!r} is reserved for the batch's own subject and "
                    f"clock columns ({sorted(RESERVED_COLUMN_NAMES)})"
                )
            if column.name in seen:
                raise PanelBatchError(f"duplicate column name {column.name!r}")
            seen.add(column.name)
            if len(column.values) != expected:
                raise PanelBatchError(
                    f"column {column.name!r} carries {len(column.values)} values but the "
                    f"batch has {expected} rows"
                )

    def _check_visible_at_as_of(self) -> None:
        """The four-clock point-in-time contract, enforced in one step.

        `all(available_time <= as_of)` holds iff `max(available_time) <= as_of`, and the
        maximum is attained by a real row, so a single late row cannot hide among compliant
        ones. That row is then handed to the same `is_visible_at` the row-wise contract
        calls -- the comparison is never re-implemented here.
        """
        available = self.timeline.available_time
        if not available:
            return
        index = available.index(max(available))
        if not is_visible_at(self.timeline.row_timeline(index), self.as_of):
            raise PanelBatchError(
                f"row {index} of this batch became available at "
                f"{available[index].isoformat()}, after the requested as_of "
                f"{self.as_of.isoformat()}: a point-in-time batch may not contain "
                "information that was not yet knowable"
            )

    def _compute_digest(self) -> str:
        digest = sha256()
        digest.update(
            json.dumps(
                [
                    self.schema_version,
                    self.provider_id,
                    self.dataset,
                    self.kind,
                    self.source_uri,
                    self.status,
                    self.no_data_reason,
                    _epoch_micros(self.as_of),
                    _epoch_micros(self.fetched_at),
                    self.row_count,
                    len(self._storage_columns),
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        for column in self._storage_columns:
            digest.update(_COLUMN_SEPARATOR.encode())
            digest.update(_encode_column(column))
        return f"sha256:{digest.hexdigest()}"


def _require_text(field_name: str, value: str, max_length: int) -> None:
    """Non-empty, within `max_length`, and free of surrounding whitespace.

    The row-wise contract normalises instead: every `str` field of `ProviderRecord` and
    `ProviderBatch` is silently stripped by pydantic's `str_strip_whitespace=True`. This
    contract refuses rather than strips, which is a deliberate divergence, not an oversight:

    - `content_digest` is a promise that two batches carrying the same facts hash alike and
      two batches carrying different facts do not. Silently rewriting a caller's `kind` from
      `"  daily  "` to `"daily"` would make the digest a hash of something the caller never
      handed over; refusing keeps the digest a faithful function of the input.
    - `dataset` becomes a directory name under `PanelStore`'s root. Stripping it would leave
      the caller's string and the path on disk quietly different; accepting it unstripped
      would create a partition directory called `" prices "`. `panel/store.py`'s own
      `_validate_dataset` refuses the same shape so the two copies of that rule stay in step.
    - Nothing is lost: a caller that wants the row contract's behaviour writes `.strip()`,
      and gets a digest over the value it actually meant.
    """
    if not value or not value.strip():
        raise PanelBatchError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise PanelBatchError(
            f"{field_name} must not have leading or trailing whitespace; got {value!r}"
        )
    if len(value) > max_length:
        raise PanelBatchError(f"{field_name} must be at most {max_length} characters")


def _validated_subjects(subjects: tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(subjects)
    if all(type(value) is str and value for value in values):
        return values
    index, offender = next(
        (index, value) for index, value in enumerate(values) if type(value) is not str or not value
    )
    raise PanelBatchError(
        f"subject row {index}: expected a non-empty string, got {offender!r}; the subject "
        "column is never nullable"
    )


def _epoch_micros(value: datetime) -> int:
    """Microseconds since the UTC epoch -- exact for `datetime`'s full resolution, and
    independent of which timezone the value happens to be tagged with."""
    return (value - _EPOCH) // _MICROSECOND


def _encode_column(column: PanelColumn) -> bytes:
    """Encode one column unambiguously: a JSON header naming it, then a JSON array body.

    `json.dumps` escapes every control character, so `_COLUMN_SEPARATOR` can never appear
    inside an encoded string value; combined with the per-column length in the header, a
    value cannot be moved across a column boundary without changing the bytes. `NaN` and the
    infinities are left encodable on purpose (`allow_nan` stays at its default `True`,
    unlike `domain/json_value.py`'s `canonical_json_bytes`): a missing or non-finite panel
    observation is ordinary data here, and both encode deterministically.
    """
    header = json.dumps(
        [column.name, column.kind, len(column.values)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if column.kind == "timestamp":
        timestamps = cast(tuple[datetime | None, ...], column.values)
        body = json.dumps(
            [None if value is None else _epoch_micros(value) for value in timestamps],
            separators=(",", ":"),
        )
    else:
        body = json.dumps(list(column.values), ensure_ascii=False, separators=(",", ":"))
    return f"{header}{_COLUMN_SEPARATOR}{body}".encode()
