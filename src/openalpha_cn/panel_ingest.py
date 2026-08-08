"""The seam between the columnar batch contract and `PanelStore` (`V2-P1-002`).

`domain/panel_batch.py` deliberately speaks *logical* column kinds (`"float"`,
`"timestamp"`, ...) and knows nothing about DuckDB; `panel/store.py` speaks DuckDB SQL types
and knows nothing about provider contracts. This module is the one place the two vocabularies
meet, and it is intentionally thin: a closed kind-to-type table, and a writer that hands the
store the batch's own transposed row block.

## Why it is a top-level module and not `panel/ingest.py`

The natural home would be inside `openalpha_cn.panel`, and `panel -> domain` would be a
perfectly ordinary downward dependency. It is not there because `V2-P1-001` pinned a
stronger property with `grimp`:
`tests/unit/test_import_layering.py::test_panel_package_has_zero_direct_edges_into_any_other_openalpha_cn_subpackage`
asserts that `openalpha_cn.panel` imports *no* sibling subpackage at all -- DuckDB and the
standard library only. Putting the seam under `panel/` would break that assertion.

A neutral top-level module is one answer to that, and the project has reached for it before:
`openalpha_cn/batch_contracts.py` was created as a neutral top-level sibling in V2-P0B-012,
after a Critical review rejected relaxing a layering rule instead. The precedent is about the
*response* -- move the module rather than weaken the contract -- and not about the direction,
which is not the same here and should not be read as such: `batch_contracts` sits *below* the
packages it decouples (`storage -> batch_contracts -> domain`), whereas this module sits
*above* the package it must not be inside (`panel_ingest -> panel`).

Nor is a top-level module the only shape that satisfies the rule. The seam could instead be
inverted with the Protocol-plus-injection pattern this codebase already uses throughout
(`runtime/repository.py`'s `RunRepository`, `evidence/service.py`'s `EvidenceStore`, and the
rest of `OA-OPS-019`): `panel/` would declare the narrow writer Protocol it needs, and a
composition root would hand it an implementation. This task's reviewer built exactly that in
a scratch worktree and reported it working. It is not what is here because the seam
currently has exactly one consumer and one producer, and a Protocol pair plus a composition
edge is a heavier structure than a ten-line translation table earns until `V2-P1-004` gives
it a second caller. If that changes, the inversion is the upgrade path, not a rewrite.

## Why it is thin on purpose

The entire point of the columnar contract is that a batch reaches storage without being taken
apart a row at a time. `write_panel_batch()` therefore does no per-row work at all: column
specs come from `batch.storage_columns()` (built once, at batch construction) and the rows
come from `batch.to_rows()`, a single C-level `zip` transpose. Anything that walked the rows
here would hand back the throughput the contract exists to win.

## Why the type table is closed

`PanelStore.write_partition` builds its `CREATE TABLE` DDL by interpolation and
`_build_scan_sql` builds its projection and `WHERE` keys the same way. Two operands used to
reach that SQL unprotected -- a column name inside bare double quotes, which a `"` in the
name closes, and a `duckdb_type` with no quoting at all -- and both were reproduced as live
injections, first against `43f3522` and again against `cb9e8f4` (see task 25's review
report).

An earlier version of this section claimed that "nothing routed through this module can
express either one", and deferred fixing `store.py` on the strength of that claim. The claim
was false, and the review demonstrated it: `ColumnarPanelBatch.columns` is an ordinary tuple
field, so a column object that never ran `PanelColumn.__post_init__` -- a duck-typed
stand-in, or a subclass that overrides it away -- carried a hostile name straight through to
the DDL, and the `ATTACH` inside that name created a file at a path of the attacker's
choosing while `write_panel_batch()` returned normally. Two things changed as a result:

- `ColumnarPanelBatch._check_shape` now re-validates every caller-supplied column's name and
  kind (and rejects anything that is not a `PanelColumn`) instead of trusting that
  construction ran. That is what makes the property above actually hold for this path.
- `panel/store.py` escapes every identifier it interpolates and accepts a `duckdb_type` only
  from the closed `DUCKDB_COLUMN_TYPES` set. The gap is closed at the storage layer too, so
  it no longer depends on any one caller's discipline.

The type table here stays closed for the same reason it always was: the DuckDB type is never
caller-supplied, it comes from `PANEL_DUCKDB_TYPES` keyed by a validated logical kind, and
`tests/integration/panel/test_panel_store_hardening.py` pins its values as a subset of the
store's own accepted set.

## Deriving catalog coverage (`V2-P1-003`)

`panel_coverage()` turns a batch into the `PartitionCoverage` record `PanelStore` stores, and
`write_panel_batch()` records it alongside the partition. This is the right side of the seam
for that work: the coverage record is a *summary of a batch*, and this module is already the
only place that holds one and a store at the same time.

Three judgements are worth stating outright.

**`content_digest` is now persisted, and it does not replace the store's `content_hash`.**
The gap this module's previous version recorded ("the batch's digest is dropped on the
floor") is closed: `batch_digest` is a column of `panel_partition_coverage`. But the two
hashes are kept, because they answer different questions and each fails at the other's job.
The store's hash covers `(dataset, year, column names and SQL types, rows)` and exists to
make a re-write of identical content a true no-op; the batch digest additionally covers
`provider_id`, `kind`, `as_of`, `fetched_at`, `source_uri` and `schema_version`, and exists
to let a later reader re-prove that a partition still holds what that provider sent at that
point in time. Substituting the digest for the store's hash would rewrite an unchanged
partition on every refetch, because `fetched_at` is hashed into it; substituting the store's
hash for the digest would lose the provenance entirely. Demonstrated, rather than asserted,
in `tests/integration/panel/test_panel_coverage_ingest.py`.

**Trading dates are derived in a declared timezone, and the declaration is stored.** A row's
`event_time` is a UTC instant; the date it belongs to is not, because a session at 08:00
Asia/Shanghai is the previous calendar date in UTC (the same trap the `year` argument below
sidesteps by refusing to guess). `date_timezone` defaults to `Asia/Shanghai` -- the
convention `providers/tushare.py` already applies to daily data -- and is written onto the
coverage record, so `V2-P1-004`'s real trading calendar can be checked against the same
convention rather than silently disagreeing by a day.

**The revision census is opt-in, by column name.** `revision_field` names one of the
caller's own columns whose distinct values are the version discriminator; roadmap section 7
measured that Tushare's restatements are separated only by `update_flag`, both announcement
dates being identical, so no clock-derived measure can see them. Nothing is guessed: without
`revision_field` the census is empty, and the clock-derived `revised_row_count` is recorded
either way, because where the clocks *do* carry a correction it is real information.

## Cost shape

`panel_coverage()` does no per-row Python work, for the same reason `write_panel_batch()`
does not: at ADR-0002's panel scale a batch is ~1.35e7 rows. The subject universe is one
C-level `set()` over the subject column; the date census is a C-level `Counter()` over the
`event_time` column followed by a loop over its *distinct* instants (~244 trading days per
partition, not 1.35e7 rows), so the timezone conversion runs once per trading day rather than
once per row; the revision count is `sum(map(operator.gt, ...))`, one C-level pass. Every
one of those is O(row_count) with C-level primitives -- the same cost model
`domain/panel_batch.py` states for its own validation -- and none constructs a per-row object.

The structural claim above is what matters and is evident from the code; a one-off
measurement on this task's development machine (122,000 rows = 500 names x 244 trading days,
best of five) put `panel_coverage()` at 5.0 ms, or **0.041 us/row** -- roughly 2% of the
1.80 us/row the columnar contract itself costs to construct the batch being summarised.
Unlike the contract's own numbers, this one is deliberately *not* asserted anywhere: it is
context for a reader, and a wall-clock assertion would be a flaky gate on a claim the code
shape already carries.

## Known gaps, recorded rather than fixed here

- **No `src/` caller yet.** Nothing in `src/openalpha_cn` calls `write_panel_batch()`; the
  first real one arrives with `V2-P1-004`, when a dataset is actually wired up. Until then
  this seam is exercised by tests only, which is expected for a contract that deliberately
  landed ahead of its consumer.

## What is deliberately not decided here

`year` is a required keyword argument, never derived from the batch. It is tempting to read
it off `event_time`, but panel `event_time` is a UTC instant while A-share partitioning is by
*trading date* in Asia/Shanghai: a session on 2024-01-01 08:00 CST is 2023-12-31 24:00 UTC,
so a UTC-derived year would silently misfile every early-January morning. Choosing the
partition key needs the real trading calendar that arrives with `V2-P1-004`; until then the
caller says which partition it means.
"""

import operator
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime
from types import MappingProxyType
from typing import Final
from zoneinfo import ZoneInfo

from openalpha_cn.domain.panel_batch import (
    RESERVED_COLUMN_NAMES,
    ColumnarPanelBatch,
    PanelBatchError,
    PanelColumn,
    PanelColumnKind,
)
from openalpha_cn.panel.catalog import (
    DEFAULT_DATE_TIMEZONE,
    DateCoverage,
    FieldCoverage,
    PartitionCoverage,
    RevisionCoverage,
)
from openalpha_cn.panel.store import ColumnSpec, PanelStore, PartitionRef

PANEL_DUCKDB_TYPES: Final[Mapping[PanelColumnKind, str]] = MappingProxyType(
    {
        "boolean": "BOOLEAN",
        "float": "DOUBLE",
        "integer": "BIGINT",
        "string": "VARCHAR",
        "timestamp": "TIMESTAMPTZ",
    }
)


def panel_column_specs(batch: ColumnarPanelBatch) -> tuple[ColumnSpec, ...]:
    """Translate a batch's storage columns into `PanelStore` column specs, in order."""
    return tuple(
        ColumnSpec(column.name, PANEL_DUCKDB_TYPES[column.kind])
        for column in batch.storage_columns()
    )


def panel_coverage(
    batch: ColumnarPanelBatch,
    *,
    year: int,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
    revision_field: str | None = None,
) -> PartitionCoverage:
    """Summarise `batch` into the catalog's Story S8 coverage record.

    Answers all five of S8's questions from the batch itself: the distinct subject universe,
    every stored column and its logical kind, the per-trading-date row census, the revision
    census (both facets), and how far the data reaches on the event clock.

    `date_timezone` is the IANA zone the per-row `event_time` instants are resolved to
    trading dates in, and it is recorded on the returned record rather than assumed --
    see this module's docstring. `revision_field`, when given, names one of the batch's own
    data columns whose distinct values are the revision discriminator (`update_flag`, for
    the Tushare financials `V2-P1-011` will wire up).

    Raises `PanelBatchError` for a `no_data` batch (there is no partition to describe), for
    an unknown timezone label, and for a `revision_field` that is not a data column of this
    batch or that is not populated on every row.
    """
    if batch.status != "success":
        raise PanelBatchError(
            f"cannot summarise a {batch.status!r} batch: {batch.no_data_reason!r}"
        )
    zone = _resolve_timezone(date_timezone)
    event_time = batch.timeline.event_time
    available_time = batch.timeline.available_time
    return PartitionCoverage(
        dataset=batch.dataset,
        year=year,
        provider_id=batch.provider_id,
        kind=batch.kind,
        schema_version=batch.schema_version,
        batch_digest=batch.content_digest,
        as_of=batch.as_of,
        fetched_at=batch.fetched_at,
        row_count=batch.row_count,
        date_timezone=date_timezone,
        last_event_time=max(event_time),
        max_available_time=max(available_time),
        # `sum(map(operator.gt, ...))` rather than a comprehension: one C-level pass over two
        # already-materialised columns, no per-row Python object. `int(...)` because
        # `operator.gt` is untyped, not because the sum could be anything else.
        revised_row_count=int(sum(map(operator.gt, batch.timeline.revision_time, available_time))),
        subjects=tuple(sorted(set(batch.subjects))),
        fields=tuple(
            FieldCoverage(name=column.name, kind=column.kind) for column in batch.storage_columns()
        ),
        dates=_date_census(event_time, zone),
        revisions=_revision_census(batch, revision_field),
    )


def write_panel_batch(
    store: PanelStore,
    batch: ColumnarPanelBatch,
    *,
    year: int,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
    revision_field: str | None = None,
) -> PartitionRef:
    """Write one columnar batch into the `(batch.dataset, year)` partition, with its coverage.

    Overwrite-per-partition and content-hash-idempotent, exactly as
    `PanelStore.write_partition` documents -- this function adds no storage semantics of its
    own. The coverage record is summarised *before* the write, so a malformed
    `revision_field` or timezone fails without leaving a partition on disk, and recorded
    *after* it, so the interrupted case leaves a partition the readiness contract reports as
    `coverage_missing` (blocked) rather than one it silently trusts.

    Coverage is re-recorded even when the partition write itself was an idempotent no-op: the
    rows may be byte-identical while the batch that carried them is not the same batch (a
    refetch has a later `fetched_at`, hence a different `content_digest`), and provenance is
    exactly what the coverage record exists to keep.

    A `no_data` batch is refused rather than written: it carries no rows by construction, so
    writing it would either raise deep inside the store ("cannot write an empty partition
    batch") or, worse, be mistaken for a successful empty partition. Explicit no-data is a
    result to record somewhere, not a partition to create -- the same distinction
    `ProviderBatch` draws between `status="no_data"` and an empty success.
    """
    if batch.status != "success":
        raise PanelBatchError(
            f"cannot write a {batch.status!r} batch to a partition: {batch.no_data_reason!r}"
        )
    coverage = panel_coverage(
        batch, year=year, date_timezone=date_timezone, revision_field=revision_field
    )
    reference = store.write_partition(
        batch.dataset, year, panel_column_specs(batch), batch.to_rows()
    )
    store.record_coverage(coverage)
    return reference


def _resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, OSError) as error:
        raise PanelBatchError(f"date_timezone {name!r} is not a known IANA time zone") from error


def _date_census(event_time: tuple[datetime, ...], zone: ZoneInfo) -> tuple[DateCoverage, ...]:
    """Rows per trading date, converting each *distinct instant* once rather than each row.

    A year of one dataset holds ~2,440 rows per name across ~244 trading days at ADR-0002's
    scale, so the column carries millions of entries drawn from a couple of hundred distinct
    values. `Counter` over the raw column is a C-level pass; the timezone conversion then runs
    once per distinct instant. Converting per row instead would make this the most expensive
    step in the whole ingest path, for identical output.
    """
    census: Counter[date] = Counter()
    for instant, count in Counter(event_time).items():
        census[instant.astimezone(zone).date()] += count
    return tuple(
        DateCoverage(event_date=day, row_count=count) for day, count in sorted(census.items())
    )


def _revision_census(
    batch: ColumnarPanelBatch, revision_field: str | None
) -> tuple[RevisionCoverage, ...]:
    """Rows per distinct value of `revision_field`, or `()` when no field was named.

    Only a *data* column qualifies. Pointing this at `revision_time` (or any other reserved
    clock column) would turn a per-row instant into thousands of one-row "versions", which
    reads as revision coverage and is nothing of the kind -- and, per roadmap section 7, the
    clocks are precisely what cannot distinguish a restatement in the real data.
    """
    if revision_field is None:
        return ()
    if revision_field in RESERVED_COLUMN_NAMES:
        raise PanelBatchError(
            f"revision_field {revision_field!r} is one of the batch's own subject and clock "
            f"columns ({sorted(RESERVED_COLUMN_NAMES)}); a revision label must be a data column"
        )
    column: PanelColumn | None = next(
        (candidate for candidate in batch.columns if candidate.name == revision_field), None
    )
    if column is None:
        raise PanelBatchError(
            f"revision_field {revision_field!r} is not a column of this batch; available: "
            f"{sorted(candidate.name for candidate in batch.columns)}"
        )
    if None in column.values:
        raise PanelBatchError(
            f"revision column {revision_field!r} row {column.values.index(None)}: a revision "
            "label is never nullable -- every row belongs to some version"
        )
    census = Counter(str(value) for value in column.values)
    return tuple(
        RevisionCoverage(label=label, row_count=count) for label, count in sorted(census.items())
    )
