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

## The trading calendar's two directions (`V2-P1-004`)

`write_trading_calendar()` and `load_trading_calendar()` are the first real `src/` callers of
this seam, and the calendar is the dataset that most needs both halves of it: the panel plane
is where it is *stored*, and `domain/trading_calendar.py` is what it has to become again
before anyone can ask it a question.

Reading goes through `PanelStore.read_if_ready()`, not `query()`, on purpose. `query()`
answers `[]` for a partition that was never written, and an empty row set fed to
`build_trading_calendar` would produce -- nothing, because that constructor refuses an empty
input. Two fail-closed layers rather than one is not redundancy here: the readiness verdict
says *which* year is missing and why, in structured codes `V2-P1-012` and `V2-P1-013` already
branch on, while the constructor's contiguity rule catches the case readiness cannot see (a
partition that exists, passes every check, and happens to be missing a day in the middle).

`trading_calendar_requirement()` waives two of the four checks, and both waivers are recorded
on the verdict (`DatasetReadiness.checks_waived`) rather than being invisible:

- **`required_dates`** is waived because stating it would be circular -- the trading days of
  the year are exactly what the calendar is being loaded to find out. What replaces it is
  strictly stronger: `build_trading_calendar` demands *every natural day* between the first
  and the last, which is a superset of any list of trading days, and reports the first
  missing one by name.
- **`max_staleness`** is waived because it cannot fire. Staleness is `as_of -
  last_event_time`, and a calendar partition's newest event is the *last session of its
  year*, which is normally in the future -- so the difference is negative and the check
  silently passes for every input. A check that can never fail is worse than an absent one:
  it reads as reassurance. Freshness for a calendar is the horizon
  (`TradingCalendar.horizon`), which is a different question and has its own answer.

Two limitations of the stored calendar are stated here because nothing downstream can infer
them:

- **A calendar partition holds whichever exchanges its batch carried, and a second write
  replaces rather than extends it.** The partition key is `(dataset, year)` with no exchange
  dimension. `write_trading_calendar()` therefore refuses a batch that would drop an exchange
  the partition already holds, rather than overwriting it and reporting success; see that
  function for why widening the key is a different task.
- **`revised_row_count` is always 0 for this dataset, and 0 means unmeasured.** `trade_cal`
  carries no revision instant, so `providers/tushare.py::_calendar_publication_timeline` sets
  `revision_time == available_time` -- the only value that fabricates nothing. Real revisions
  do happen (`domain/trading_calendar.py::KNOWN_CALENDAR_LOOKAHEAD` names three dates), and
  they are invisible to this census by construction. They show up as a changed partition
  content hash on a re-fetch, not as a revised row.

`panel_readiness_requirement()` is the other direction -- the one `V2-P1-003` was waiting
for. It turns a real calendar into the `required_dates` of *another* dataset's requirement,
so hole detection for `daily`, `adj_factor` and the rest stops being a set difference against
whatever the caller happened to declare. It clamps the required range at `as_of`, because a
session that has not happened yet cannot be missing.

## Deriving the partition year

`year` remains a required keyword argument of `write_panel_batch()`. What has changed is that
a caller no longer has to guess it: `panel_partition_year()` derives it from the batch's own
`event_time` census in the declared timezone and **refuses a batch that spans two years**
rather than picking one. That refusal is the point. The trap this note used to describe --
a session on 2024-01-01 08:00 CST is 2023-12-31 24:00 UTC, so a UTC-derived year misfiles
every early-January morning -- is avoided by resolving in `date_timezone`, the same value
`panel_coverage()` records on the coverage row; and the second trap, a batch that genuinely
straddles a year boundary, is answered with an error instead of a silent choice.
"""

import operator
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Final
from zoneinfo import ZoneInfo

from openalpha_cn.domain.name_history import (
    NAME_HISTORY_PANEL_COLUMNS,
    NAMECHANGE_DATASET,
    NameHistory,
    name_histories_from_panel_rows,
)
from openalpha_cn.domain.panel_batch import (
    RESERVED_COLUMN_NAMES,
    SUBJECT_COLUMN_NAME,
    ColumnarPanelBatch,
    PanelBatchError,
    PanelColumn,
    PanelColumnKind,
    TimelineColumns,
)
from openalpha_cn.domain.stock_universe import (
    STOCK_BASIC_DATASET,
    UNIVERSE_PANEL_COLUMNS,
    StockUniverse,
    stock_universe_from_panel_rows,
)
from openalpha_cn.domain.trading_calendar import (
    CALENDAR_PANEL_COLUMNS,
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
    TradingCalendarError,
    trading_calendar_from_panel_rows,
)
from openalpha_cn.panel.catalog import (
    DEFAULT_DATE_TIMEZONE,
    DateCoverage,
    FieldCoverage,
    PanelStorageError,
    PartitionCoverage,
    ReadinessRequirement,
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


def panel_partition_year(
    batch: ColumnarPanelBatch, *, date_timezone: str = DEFAULT_DATE_TIMEZONE
) -> int:
    """The single calendar year `batch` belongs to, resolved in `date_timezone`.

    Refuses a batch that spans two years rather than choosing one. A straddling batch has no
    single partition, and every way of picking one -- first row, last row, most rows -- files
    part of the data under a year it does not belong to, where a later single-year re-fetch
    then overwrites the partition and drops it. `split_panel_batch_by_year()` is the answer
    for a batch that genuinely straddles; picking is never one.

    The timezone is not decoration: `event_time` is a UTC instant, and a session at 08:00
    Asia/Shanghai on 1 January is 31 December in UTC, so a UTC-derived year misfiles every
    early-January morning of the panel.
    """
    if batch.status != "success":
        raise PanelBatchError(
            f"cannot derive a partition year from a {batch.status!r} batch: "
            f"{batch.no_data_reason!r}"
        )
    years = _partition_years(batch, date_timezone)
    if len(years) != 1:
        raise PanelBatchError(
            f"this batch spans {sorted(years)} in {date_timezone} and has no single "
            f"{batch.dataset} partition; split it by year before writing"
        )
    return next(iter(years))


def _partition_years(batch: ColumnarPanelBatch, date_timezone: str) -> set[int]:
    zone = _resolve_timezone(date_timezone)
    return {instant.astimezone(zone).year for instant in set(batch.timeline.event_time)}


def split_panel_batch_by_year(
    batch: ColumnarPanelBatch, *, date_timezone: str = DEFAULT_DATE_TIMEZONE
) -> tuple[tuple[int, ColumnarPanelBatch], ...]:
    """Split one batch into one batch per event year, ascending. Every row is kept exactly once.

    `PanelStore.record_coverage` refuses a coverage record whose date census reaches outside
    its own partition's year -- a rule `V2-P1-003` added after a batch of 2019 rows written
    under `year=2024` produced a catalog that reported `ready` for dates it did not hold. That
    rule is what makes this function necessary rather than optional: a dataset whose *request*
    is not a period cannot be filed under one, and `stock_basic` is exactly that. One fetch
    returns the whole registry, so its event census runs from 1990 to last week.

    ## Cost

    This is the one place in the ingest path that does per-row Python work, and it is stated
    rather than hidden. `domain/panel_batch.py` exists to keep the contract's cost at
    ~1.8 us/row for the 1.35e7-row-per-field datasets ADR-0002 sizes; this function is
    O(row_count) with a Python-level index list and a tuple rebuild per column, and it is
    applied only to the two registry datasets, whose whole corpora are 6,217 lifecycle rows
    and 14,166 rename rows -- three orders of magnitude below panel scale, and fetched once
    rather than per trading day. Do not reach for it on a price panel; split those at the
    request instead, the way `trade_cal` and `daily` already do.
    """
    if batch.status != "success":
        raise PanelBatchError(
            f"cannot split a {batch.status!r} batch by year: {batch.no_data_reason!r}"
        )
    zone = _resolve_timezone(date_timezone)
    grouped: dict[int, list[int]] = {}
    for index, instant in enumerate(batch.timeline.event_time):
        grouped.setdefault(instant.astimezone(zone).year, []).append(index)
    return tuple((year, _select_rows(batch, grouped[year])) for year in sorted(grouped))


def _select_rows(batch: ColumnarPanelBatch, indices: Sequence[int]) -> ColumnarPanelBatch:
    """A batch holding `batch`'s rows at `indices`, in order, with everything else unchanged.

    `as_of`, `fetched_at` and `source_uri` are carried across deliberately: a split does not
    make three fetches out of one, and a sub-batch that claimed its own provenance would make
    `PartitionCoverage.batch_digest` describe a batch no provider ever sent. The digest itself
    does change, because the row count and the column bodies do -- which is correct: these are
    different partitions.
    """
    timeline = batch.timeline
    return ColumnarPanelBatch(
        provider_id=batch.provider_id,
        dataset=batch.dataset,
        kind=batch.kind,
        as_of=batch.as_of,
        fetched_at=batch.fetched_at,
        status="success",
        subjects=tuple(batch.subjects[index] for index in indices),
        timeline=TimelineColumns(
            event_time=tuple(timeline.event_time[index] for index in indices),
            available_time=tuple(timeline.available_time[index] for index in indices),
            ingested_time=tuple(timeline.ingested_time[index] for index in indices),
            revision_time=tuple(timeline.revision_time[index] for index in indices),
        ),
        columns=tuple(
            PanelColumn(column.name, column.kind, tuple(column.values[index] for index in indices))
            for column in batch.columns
        ),
        source_uri=batch.source_uri,
    )


def write_trading_calendar(
    store: PanelStore,
    batch: ColumnarPanelBatch,
    *,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> PartitionRef:
    """Write one year of exchange calendar into the panel plane (`V2-P1-004`).

    A thin composition of `panel_partition_year()` and `write_panel_batch()`: the calendar is
    the one dataset whose partition year is never ambiguous (a request covers exactly one
    year), so making the caller restate it would be an invitation to state it wrong.

    ## One partition per year holds every exchange it was written with, and no more

    `PanelStore`'s partition key is `(dataset, year)` -- there is no exchange dimension -- and
    `write_partition()` replaces a partition whole. A partition can therefore hold SSE *and*
    SZSE rows together (the `subject` column separates them and `load_trading_calendar()`
    filters on it), but only if **one batch carries both**. Two calls, one per exchange, do
    not accumulate: the second would replace the first outright, and a plain
    `for exchange in ("SSE", "SZSE"): write_trading_calendar(...)` backfill would leave only
    the exchange it happened to write last.

    That is what this function refuses. A batch whose subjects do not cover every exchange
    already stored in the target partition raises `PanelBatchError` instead of overwriting it.
    The reads were already fail-closed -- a load for the dropped exchange blocks on
    `subject_missing` rather than answering with the survivor's calendar -- but a silent write
    that destroys data and reports success is not something a downstream check should have to
    catch. Widening the key so two exchanges could be written independently is a change to
    `PanelStore`'s partition identity, its catalog primary keys and therefore
    `panel_catalog_meta.schema_version`; it belongs with whichever task first needs a second
    exchange, not with this one. Today `_trade_cal_params` refuses more than one exchange per
    request, so the refusal costs nothing and the limitation is stated rather than discovered.

    A partition with no coverage record is not protected by this check -- there is nothing to
    read the stored subjects from. That is an interrupted write, which `assess_readiness()`
    already blocks as `coverage_missing`, and refusing to overwrite it would leave the store
    with no way back.
    """
    if batch.dataset != TRADING_CALENDAR_DATASET:
        raise PanelBatchError(
            f"expected the {TRADING_CALENDAR_DATASET!r} dataset, got {batch.dataset!r}"
        )
    year = panel_partition_year(batch, date_timezone=date_timezone)
    _refuse_to_drop_stored_subjects(
        store,
        batch,
        year,
        remedy=(
            "A partition is replaced whole and its key has no exchange dimension, so two "
            "exchanges have to arrive in one batch or not at all"
        ),
    )
    return write_panel_batch(store, batch, year=year, date_timezone=date_timezone)


_SUBJECT_SAMPLE: Final[int] = 10
"""How many subject names an overwrite refusal lists before summarising the rest.

The calendar partitions hold two subjects and the registry holds 5,878, so an unbounded list
would turn one refusal into an unreadable wall. Small sets are still printed in full, so the
calendar's message is byte-identical to what it was before this guard was shared.
"""


def _subject_sample(subjects: Sequence[str]) -> str:
    """Render a subject set for an error message, capped at `_SUBJECT_SAMPLE` names."""
    ordered = sorted(subjects)
    if len(ordered) <= _SUBJECT_SAMPLE:
        return str(ordered)
    return f"{ordered[:_SUBJECT_SAMPLE]} and {len(ordered) - _SUBJECT_SAMPLE} more"


def _refuse_to_drop_stored_subjects(
    store: PanelStore, batch: ColumnarPanelBatch, year: int, *, remedy: str
) -> None:
    """Block an overwrite that would remove a subject the partition already holds.

    Shared by `write_trading_calendar` (where a dropped subject is an exchange) and
    `write_stock_universe` (where it is a security). The failure is the same in both: a
    partition is replaced whole, so a batch that is missing something the stored partition had
    destroys data and reports success. The reads are already fail-closed -- a load for the
    dropped subject blocks on `subject_missing` -- but a silent destructive write is not
    something a downstream check should have to catch.

    A partition with no coverage record is not protected: there is nothing to read the stored
    subjects from. That is an interrupted write, which `assess_readiness()` blocks as
    `coverage_missing`, and refusing to overwrite it would leave the store with no way back.
    """
    existing = store.read_coverage(batch.dataset, year)
    if existing is None:
        return
    dropped = sorted(set(existing.subjects) - set(batch.subjects))
    if dropped:
        raise PanelBatchError(
            f"{batch.dataset} year={year} already holds "
            f"{_subject_sample(existing.subjects)} and this batch carries "
            f"{_subject_sample(tuple(set(batch.subjects)))}; writing it would drop "
            f"{_subject_sample(dropped)}. {remedy}"
        )


def trading_calendar_requirement(
    *, exchange: str, years: Sequence[int], as_of: datetime
) -> ReadinessRequirement:
    """What the calendar dataset itself must satisfy before it may be read.

    Two of the four checks are waived, deliberately and on the record; see this module's
    docstring for why neither could do useful work here and what replaces them.
    """
    return ReadinessRequirement(
        dataset=TRADING_CALENDAR_DATASET,
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=(exchange,),
        required_fields=CALENDAR_PANEL_COLUMNS,
        max_staleness=None,
    )


def load_trading_calendar(
    store: PanelStore, *, exchange: str, years: Sequence[int], as_of: datetime
) -> TradingCalendar:
    """Read the stored calendar back as a `TradingCalendar`, or refuse to.

    Fail-closed twice over. A year whose partition is missing, damaged, unprofiled or
    described by a stale coverage record is blocked by `read_if_ready()` and reported by its
    structured issue codes; a set of years that is not contiguous -- or a partition with a
    hole in the middle -- is refused by `build_trading_calendar` afterwards. Neither path can
    hand back a calendar that reads the missing stretch as a holiday.

    Readiness is assessed once per requested year rather than once in total. That is
    `read_if_ready()`'s shape (it vets the dataset and reads one partition), and at the
    handful of years a calendar load spans the repeated catalog lookup costs nothing worth
    restructuring the contract for.
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise TradingCalendarError(
            "load_trading_calendar needs at least one year; a load that read nothing would "
            "produce a calendar that answers 'beyond horizon' to every question"
        )
    requirement = trading_calendar_requirement(exchange=exchange, years=requested, as_of=as_of)
    rows: list[tuple[object, ...]] = []
    for year in requested:
        outcome = store.read_if_ready(
            requirement,
            year=year,
            columns=CALENDAR_PANEL_COLUMNS,
            filters={SUBJECT_COLUMN_NAME: exchange},
        )
        if outcome.is_blocked:
            raise TradingCalendarError(
                f"the {exchange} calendar cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.readiness.issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.readiness.issues)}"
            )
        rows.extend(outcome.rows)
    return trading_calendar_from_panel_rows(exchange, rows)


def write_stock_universe(
    store: PanelStore,
    batch: ColumnarPanelBatch,
    *,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> tuple[PartitionRef, ...]:
    """Write a registry fetch into one partition per lifecycle year (`V2-P1-005`).

    ## Why this writer returns several partitions and the others return one

    Every other dataset here is fetched *by period*: a `trade_cal` request covers one year, so
    one request is one partition. `stock_basic` has no date filter at all -- one request
    returns the whole registry, listings from 1990-12-19 and terminations from 1999-07-12
    onwards -- so its event census spans 36 years and `panel_partition_year` refuses it.

    Filing the whole thing under the year it was *fetched* is the obvious alternative and is
    not available: `PanelStore.record_coverage` refuses a coverage record whose date census
    reaches outside its partition's own year, a rule `V2-P1-003` added after a batch of 2019
    rows filed under `year=2024` produced a catalog that reported `ready` for dates it did not
    hold. So the batch is split by `split_panel_batch_by_year()` and each year is written to
    its own partition, which is also what makes the years mean something: the 2019 partition
    is the securities that listed or died in 2019.

    ## Reading a year range is the point-in-time window

    Because the delisting is its own row in its own year, the set of years a caller reads *is*
    the observation window. Years 1990..2019 give a universe in which a security that died in
    2024 is still listed -- which is what a 2019 observer would have said -- and the same
    partitions plus 2020..2026 give one in which it is not. Nothing downstream has to know
    that; see `load_stock_universe`.

    ## An overwrite that would drop securities is refused

    A re-fetch replaces each year's partition whole. The registry only grows within a past
    year -- a delisted security stays in the `D` set, and a live probe found the 1999
    termination still present in 2026 -- so a batch carrying fewer securities *for a year the
    store already holds* is a partial or filtered fetch rather than news. It raises rather
    than overwriting; see `_refuse_to_drop_stored_subjects`.

    The guard's bound is stated rather than overclaimed: it covers the **destructive** case, a
    year this batch still writes but with fewer subjects than the stored partition had. A year
    that vanishes from the batch *entirely* is simply not written, so its partition keeps
    every row it had -- nothing is lost, and the store's own coverage still describes it
    correctly. That is also why the check is not extended to "every year the store holds":
    a batch legitimately fetched at an earlier `as_of` has no rows for later years, and
    refusing it would block replaying a past `as_of` over a backfilled store.
    """
    if batch.dataset != STOCK_BASIC_DATASET:
        raise PanelBatchError(
            f"expected the {STOCK_BASIC_DATASET!r} dataset, got {batch.dataset!r}"
        )
    written: list[PartitionRef] = []
    for year, yearly in split_panel_batch_by_year(batch, date_timezone=date_timezone):
        _refuse_to_drop_stored_subjects(
            store,
            yearly,
            year,
            remedy=(
                "The registry only grows within a past year, so a smaller batch is a partial "
                "or filtered fetch rather than news; re-fetch the whole registry with "
                "list_status='L,D'"
            ),
        )
        written.append(write_panel_batch(store, yearly, year=year, date_timezone=date_timezone))
    return tuple(written)


def stock_universe_requirement(
    *, years: Sequence[int], as_of: datetime, max_staleness: timedelta | None
) -> ReadinessRequirement:
    """What the registry must satisfy before a universe may be built from it.

    Two of the four checks are waived, and both waivers land in
    `DatasetReadiness.checks_waived` rather than being invisible:

    - **`required_dates`** is waived because a listing has no schedule. There is no list of
      days a year is *supposed* to contain lifecycle events on, so any expectation stated here
      would be a guess, and a year with few events is ordinary (1999 has exactly one
      termination in the whole corpus). What replaces it is structural rather than a date set:
      `stock_universe_from_panel_rows` refuses a delisting row whose listing row is absent --
      which is exactly what a skipped year looks like -- and `build_stock_universe` refuses a
      duplicated `ts_code` or a lifecycle date that post-dates the snapshot.
    - **`required_subjects`** is waived because naming the securities would be circular: the
      universe is what the read is for.

    `max_staleness` is **not** waived by default and has no default value: the caller states a
    bound or states `None` on the record. This is one dataset where the check does real work,
    unlike the calendar's -- a registry whose newest lifecycle event is a year old is a
    registry that has missed a year of listings -- so leaving it to a default would be
    choosing silence.
    """
    return ReadinessRequirement(
        dataset=STOCK_BASIC_DATASET,
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=UNIVERSE_PANEL_COLUMNS,
        max_staleness=max_staleness,
    )


def load_stock_universe(
    store: PanelStore,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> StockUniverse:
    """Read stored lifecycle years back as a `StockUniverse`, or refuse to.

    Fail-closed three times over. A partition that is missing, damaged, unprofiled, stale or
    described by an out-of-date coverage record is blocked by `read_if_ready()` and reported
    by its structured issue codes. A gap in the requested years is refused here, because a
    skipped year is a silently *smaller* universe -- the same defect `build_trading_calendar`
    refuses a hole in a day sequence for. And a partition that passes both and is internally
    inconsistent -- an orphan delisting row, a duplicated security -- is refused afterwards by
    `stock_universe_from_panel_rows`.

    ## The snapshot date stops at the first year the store holds and the caller did not read

    The universe's upper horizon is not `as_of` alone. A caller reading 1990..2019 at
    `as_of=2026` against a store that also holds 2020 and 2024 answers a 2023 question by
    reporting every security that died in those years as still listed -- a fail-open with no
    signal. Nor is it "the end of the last year read": a fetch at `as_of=2019-06-28` may
    legitimately produce no partition after 2016 because nothing listed or died in between,
    and capping the horizon at 2016 would refuse questions the data answers perfectly well.

    So it is `as_of`, pulled back to the day before the **first lifecycle year the store has
    and this read skipped**. Reading everything gives `as_of`; reading a prefix gives the
    boundary of that prefix; and a question past the result is `beyond_snapshot` rather than
    an answer.

    ## The gap rule, and the one gap it cannot see

    Skipping a year inside the requested range is refused -- if the store holds a partition
    for a year between the first and the last requested and the caller did not ask for it,
    that is a read which would drop that year's listings and terminations and produce a
    smaller universe that looks entirely plausible.

    What this cannot see is a year that was **never ingested**, because a year with no
    partition is indistinguishable from a year in which nothing listed and nothing died --
    both are simply absent, and requiring a partition per calendar year would make a sparse
    but correct history unloadable. Naming a year in `years` is how a caller asserts it should
    exist (readiness then blocks it as `partition_missing`), and
    `UniverseCompleteness.years_read` is what a report shows so the window is visible rather
    than assumed. `store.registered_years(STOCK_BASIC_DATASET)` is the natural argument.

    Starting the range after the first listing is not refused either, for the same reason, and
    what catches it in practice is the orphan-delisting rule in
    `stock_universe_from_panel_rows`: a security that listed in 1991 and died in 2020 has no
    listing row inside 2015..2020, so that read raises rather than inventing one.
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise PanelBatchError(
            "load_stock_universe needs at least one lifecycle year; a read of no years would "
            "produce an empty registry that answers 'nothing was listed' to every day"
        )
    registered = set(store.registered_years(STOCK_BASIC_DATASET)) - set(requested)
    skipped = sorted(year for year in registered if requested[0] < year < requested[-1])
    if skipped:
        raise PanelBatchError(
            f"the requested {STOCK_BASIC_DATASET} years {requested[0]}..{requested[-1]} skip "
            f"{skipped}, which the store holds; a skipped year drops that year's listings and "
            "terminations and produces a smaller universe that looks entirely plausible"
        )
    requirement = stock_universe_requirement(
        years=requested, as_of=as_of, max_staleness=max_staleness
    )
    rows: list[tuple[object, ...]] = []
    for year in requested:
        outcome = store.read_if_ready(requirement, year=year, columns=UNIVERSE_PANEL_COLUMNS)
        if outcome.is_blocked:
            raise PanelStorageError(
                f"the security registry cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.readiness.issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.readiness.issues)}"
            )
        rows.extend(outcome.rows)
    snapshot_date = as_of.astimezone(_resolve_timezone(date_timezone)).date()
    unread_after = sorted(year for year in registered if year > requested[-1])
    if unread_after:
        snapshot_date = min(snapshot_date, date(unread_after[0], 1, 1) - timedelta(days=1))
    return stock_universe_from_panel_rows(rows, snapshot_date=snapshot_date, years_read=requested)


def write_name_history(
    store: PanelStore,
    batch: ColumnarPanelBatch,
    *,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> PartitionRef:
    """Write one announcement year of the rename corpus into the panel plane (`V2-P1-005`).

    One request is one partition here, unlike the registry, because the descriptor dates a
    `namechange` row at its `ann_date` and the request window filters on the same column. The
    effective date rides along as a data column, which it may because it is published *in* the
    announcement -- see `providers/tushare.py::ClockStrategy.calendar_static` for why that
    asymmetry is what lets this dataset stay one row per record while `stock_basic` splits.

    No subject guard here, unlike the registry: a `namechange` partition is a year of
    announcements about whichever securities happened to be renamed, so a year carrying
    different names than a previous fetch is news rather than a partial read.
    """
    if batch.dataset != NAMECHANGE_DATASET:
        raise PanelBatchError(f"expected the {NAMECHANGE_DATASET!r} dataset, got {batch.dataset!r}")
    if batch.status != "success":
        raise PanelBatchError(
            f"cannot write a {batch.status!r} batch to a partition: {batch.no_data_reason!r}"
        )
    year = panel_partition_year(batch, date_timezone=date_timezone)
    return write_panel_batch(store, batch, year=year, date_timezone=date_timezone)


def name_history_requirement(
    *, years: Sequence[int], as_of: datetime, max_staleness: timedelta | None
) -> ReadinessRequirement:
    """What the rename corpus must satisfy before name histories may be built from it.

    `required_dates` and `required_subjects` are waived for the reason
    `stock_universe_requirement` waives them: a rename has no schedule, so there is no list of
    days a year is *supposed* to contain, and the securities are what the read is for. A year
    with genuinely no renames announced in it is a real and common answer -- 1991 has four
    rows in the whole corpus -- which is exactly why an expectation stated here would be a
    guess. `NameHistory.name_on()` refusing to answer before its first record is what catches
    a year that was never ingested at the point where it would matter.
    """
    return ReadinessRequirement(
        dataset=NAMECHANGE_DATASET,
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=NAME_HISTORY_PANEL_COLUMNS,
        max_staleness=max_staleness,
    )


def load_name_histories(
    store: PanelStore,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
) -> Mapping[str, NameHistory]:
    """Read the stored rename corpus back as one `NameHistory` per security, or refuse to.

    Readiness is assessed once per requested year, matching `load_trading_calendar`'s shape.
    A year that was never ingested blocks rather than being skipped, because a skipped year
    would produce histories that are *shorter* and entirely plausible -- a security's name
    would simply appear to have been stable across the gap.

    What this cannot catch is a year the caller never asked for. That is the same limitation
    `load_trading_calendar` has and it is answered the same way: `NameHistory.name_on()`
    refuses to answer for a day before its own first record rather than extrapolating the
    earliest name it happens to hold backwards.
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise PanelBatchError(
            "load_name_histories needs at least one announcement year; a read of no years "
            "would produce an empty corpus that refuses every question"
        )
    requirement = name_history_requirement(
        years=requested, as_of=as_of, max_staleness=max_staleness
    )
    rows: list[tuple[object, ...]] = []
    for year in requested:
        outcome = store.read_if_ready(requirement, year=year, columns=NAME_HISTORY_PANEL_COLUMNS)
        if outcome.is_blocked:
            raise PanelStorageError(
                f"the rename corpus cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.readiness.issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.readiness.issues)}"
            )
        rows.extend(outcome.rows)
    return name_histories_from_panel_rows(rows)


def panel_readiness_requirement(
    calendar: TradingCalendar,
    dataset: str,
    *,
    as_of: datetime,
    years: Sequence[int],
    required_subjects: tuple[str, ...] | None,
    required_fields: tuple[str, ...] | None,
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> ReadinessRequirement:
    """Build another dataset's requirement with `required_dates` taken from a real calendar.

    This is the seam `V2-P1-003` left open: its `required_dates` had to be caller-supplied
    because nothing in the repository knew which days the exchange was open, so hole
    detection was a set difference against a guess. Now it is a set difference against the
    published calendar.

    The required range for each year is clamped at `as_of`: a session that has not happened
    cannot be missing, and requiring the rest of the current year would report a permanent,
    entirely invented `date_gap` for a dataset that is completely up to date. Requesting a
    year that has not begun at `as_of` is refused outright rather than answered with an empty
    expectation, which `evaluate_readiness` would (correctly, but confusingly) block on as
    `empty_requirement`.

    Any part of a requested year that the calendar does not cover makes this raise, via
    `trading_days_between`. That is the intended behaviour: a requirement built from a
    calendar that only knows half the year would silently under-require the other half.
    """
    zone = _resolve_timezone(date_timezone)
    today = as_of.astimezone(zone).date()
    required: list[date] = []
    for year in sorted(set(years)):
        start = date(year, 1, 1)
        if start > today:
            raise TradingCalendarError(
                f"year {year} has not begun at as_of {as_of.isoformat()} ({date_timezone}), so "
                f"no {dataset} session in it can be required yet"
            )
        required.extend(calendar.trading_days_between(start, min(date(year, 12, 31), today)))
    return ReadinessRequirement(
        dataset=dataset,
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=tuple(required),
        required_subjects=required_subjects,
        required_fields=required_fields,
        max_staleness=max_staleness,
    )


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
