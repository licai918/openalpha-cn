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

## Many fetches, one partition (`V2-P1-006`)

`write_adjustment_factors()` is the first writer here that takes a *sequence* of batches, and
the reason is arithmetic rather than taste. Every other dataset on this seam is fetched by a
period that matches its partition -- one `trade_cal` request is one year -- while an
`adj_factor` request is one trading day of the whole market (~5,400 rows against a measured
6,000-row response cap) and its partition is a calendar year (~1.3e6 rows). A year is
therefore ~244 fetches, `PanelStore.write_partition` replaces a partition whole and has no
append, and writing the fetches one at a time would leave the year holding only its last
session.

`merge_panel_batches()` is that primitive, and it is the exact inverse of
`split_panel_batch_by_year()` -- one dataset needed the split because its request is wider
than a partition, the other needs the merge because its request is narrower.
`V2-P1-007`'s `daily` has `adj_factor`'s shape and will use the same pair.

`carry_stored_rows_forward()` is the third of the set and it answers the case neither of the
other two can (`V2-P4-071`). A *fetched* dataset has every row of a year in hand at once, so
the merge is enough. A **derived** one does not: `openalpha factor build` computes one cross
section at one instant, a year holds as many of them as somebody chose to build, and
tomorrow's invocation does not have yesterday's -- so a whole-partition replace either
recomputed the year or destroyed it. That function reads the partition's own stored rows back
and puts them in front of the arriving batch, which makes the replace an append. It is the
only caller in this module of the un-gated `PanelStore.query`, and the reason it may take it
is the opposite of the usual one: nothing it reads is answered with, and a point-in-time read
there would commit a partition missing the withheld rows.

`compress_adjustment_batch()` then applies `domain/adjustment.py`'s piecewise-constant rule
before anything is written, which is why the factor partition is two orders of magnitude
smaller than the price panel it exists to correct. The visible consequence is on the read
side: `adjustment_requirement()` **waives** `required_dates`, because a compressed partition
holds rows only on load-bearing sessions and a calendar-derived expectation would report a
permanent `date_gap` for a partition that is complete by construction. So
`panel_readiness_requirement()` must not be pointed at this dataset.

**The waiver is paid for at write time instead of being written off.** Compression is what
destroys the session census, so the census is checked in the last moment it exists:
`write_adjustment_factors()` takes a `TradingCalendar` and refuses a merged batch that is
missing any session the calendar reports open in that year. Missing only -- extra days are
tolerated, because the one thing a calendar can do wrongly here is manufacture a false alarm.
The window anchors are *not* the replacement, which an earlier version of this note claimed:
they bound one partition's ends, and a hole inside a partition -- or a partition that starts
in March -- sits inside `covered_from`/`covered_through` once two years are read together and
is answered from the last step before it. `_refuse_missing_factor_sessions` carries the
measured wrong answer.

## Two datasets, one writer (`V2-P1-007`)

`write_daily_panel()` is the first writer here that takes **two** datasets, and the reason is
that they are not independent. `daily_basic` republishes `close`, so the two fetches one
session already needs cross-check each other for free -- and a check that is optional is a
check that gets skipped. Taking both makes agreement a precondition of storage: there is no
supported way to write one of these partitions without the other having agreed with it.

`write_daily_panel()` shares `write_adjustment_factors()`' sequence-of-batches shape, for the
same arithmetic (one request is one session of the market, one partition is a year, so a year
is ~244 fetches per dataset and `PanelStore.write_partition` has no append), and
`write_stock_universe()`'s vet-everything-then-write shape, for the same reason (a guard that
trips after the first partition has been replaced leaves the store in exactly the state the
guard exists to prevent).

### The session census here is not the factor series' census

`_refuse_missing_price_sessions()` is `_refuse_missing_factor_sessions()`' homomorph and the
two share their arithmetic through `_session_census()`, but they are load-bearing in different
ways and the difference is the design, not an accident:

- **The factor census pays for a waiver.** `adjustment_requirement()` waives `required_dates`
  because compression destroys the census, so the write-time check is the *only* one, and its
  residue -- a partition written before the check existed is never re-examined -- cannot be
  closed from the read side.
- **The price census does not.** A price partition is stored uncompressed, so its date census
  survives into the catalog and `daily_requirement()` states the same expectation, derived from
  the same calendar, on **every read**. A partition this process never wrote is still checked.
- **What a hole costs differs.** A missing factor session is answered by `bisect` from an older
  step -- a wrong number. A missing price session is answered by nothing: the cross section is
  empty and `daily_bars_from_panel_rows` has nothing to interpolate from.

Both censuses take the same two bounds (the year's own start; the day before the fetch, so the
16:30 publication does not manufacture an intraday false alarm) and both refuse only the
*missing* direction.

### And a *present* session can still be wrong

Neither census answers "did this session arrive whole". A year assembled from ~244 per-session
fetches, one of which returned a handful of names, stores a partition that passes every other
guard and then reports most of the market as unpriced on that day.
`_refuse_thin_price_sessions()` takes the extreme end of that with no extra I/O -- the row
counts are already in the same date column -- by refusing a session under
`MIN_SESSION_ROW_SHARE` of its partition's median cross section. It is a floor and not a
census, and the share is set from measured full-year data rather than chosen; see that constant.

### Why `panel_readiness_requirement()` is not reused for these two

It clamps the required range at `as_of`'s calendar *date*, which is right for a dataset whose
rows are dated at midnight and one session too generous for one that publishes at 16:30:
at 10:00 on a session, `panel_readiness_requirement` would require that session's bars and
report an invented `date_gap` on a panel that is completely up to date. `daily_requirement()`
clamps at `_sessions_published_through()` instead, which reads the same
`DAILY_AVAILABILITY_TIME` the provider dates `available_time` at.

## Three series in one partition (`V2-P1-009`)

`write_index_weights()` is the second writer here whose partition holds several independent
series at once, and it inherits `write_trading_calendar()`'s problem exactly: the key is
`(dataset, year)` with no index dimension, so 沪深300, 中证500 and 中证1000 share a year and a
per-index backfill loop would leave only the index it wrote last. `_refuse_to_drop_stored_
subjects` is the same answer, and it works for the same reason -- the thing that would go
missing is what the `subject` column holds.

What is *not* here is a write-time census, and that is a deliberate difference from
`write_adjustment_factors()`. That one checks its year against a calendar because compression
is about to destroy the evidence; nothing is compressed here, so the month census survives into
the stored rows and `domain/index_membership.py::build_index_membership` re-derives it on every
read. `index_weight_requirement()` waives `required_dates` on the strength of that, and states
why the substitute is stronger rather than merely equivalent.

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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from statistics import median
from types import MappingProxyType
from typing import Final, cast
from zoneinfo import ZoneInfo

from openalpha_cn.domain.adjustment import (
    ADJ_FACTOR_DATASET,
    ADJUSTMENT_DATE_COLUMN,
    ADJUSTMENT_FACTOR_COLUMN,
    ADJUSTMENT_PANEL_COLUMNS,
    AdjustmentHistory,
    FactorObservation,
    adjustment_histories_from_panel_rows,
    load_bearing_observations,
)
from openalpha_cn.domain.daily_prices import (
    CLOSE_COLUMN,
    DAILY_AVAILABILITY_TIME,
    DAILY_BASIC_DATASET,
    DAILY_BASIC_PANEL_COLUMNS,
    DAILY_DATASET,
    DAILY_PANEL_COLUMNS,
    MIN_SESSION_ROW_SHARE,
    PRICE_DATE_COLUMN,
    DailyBar,
    DailyValuation,
    PriceDataError,
    close_disagreements,
    daily_bars_from_panel_rows,
    daily_valuations_from_panel_rows,
)
from openalpha_cn.domain.financial_statements import (
    DATASETS_WITH_REVISION_LABEL,
    FINANCIAL_STATEMENT_DATASETS,
    REVISION_LABEL_COLUMN,
    FinancialStatementError,
    StatementHistory,
    statement_histories_from_panel_rows,
    statement_panel_columns,
)
from openalpha_cn.domain.index_membership import (
    INDEX_WEIGHT_DATASET,
    INDEX_WEIGHT_PANEL_COLUMNS,
    IndexMembership,
    IndexMembershipError,
    index_memberships_from_panel_rows,
)
from openalpha_cn.domain.index_prices import (
    INDEX_DAILY_DATA_COLUMNS,
    INDEX_DAILY_DATASET,
    INDEX_DAILY_PANEL_COLUMNS,
    MARKET_INDEX_CODE,
    IndexBar,
    index_bars_from_panel_rows,
)
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_MEMBERSHIP_PANEL_COLUMNS,
    INDUSTRY_MEMBERSHIP_TAXONOMY,
    INDUSTRY_TAXONOMY_EFFECTIVE_FROM,
    INDUSTRY_TREE_DATASET,
    INDUSTRY_TREE_PANEL_COLUMNS,
    IndustryAnswer,
    IndustryClassificationError,
    IndustryHorizonError,
    IndustryTree,
    SecurityIndustryHistory,
    industry_histories_from_panel_rows,
    industry_trees_from_panel_rows,
)
from openalpha_cn.domain.name_history import (
    NAME_HISTORY_PANEL_COLUMNS,
    NAMECHANGE_DATASET,
    NameHistory,
    name_histories_from_panel_rows,
)
from openalpha_cn.domain.panel_batch import (
    CLOCK_COLUMN_NAMES,
    RESERVED_COLUMN_NAMES,
    SUBJECT_COLUMN_NAME,
    ColumnarPanelBatch,
    PanelBatchError,
    PanelColumn,
    PanelColumnKind,
    TimelineColumns,
)
from openalpha_cn.domain.price_limits import (
    EXPLAINED_SESSION_HALF_WINDOW,
    MIN_EXPLAINED_SESSION_SHARE,
    PRICE_LIMIT_DATASET,
    PRICE_LIMIT_PANEL_COLUMNS,
    SUSPENSION_CORPUS_FIRST_SESSION,
    SUSPENSION_DATA_COLUMNS,
    SUSPENSION_DATASET,
    SUSPENSION_PANEL_COLUMNS,
    PriceLimit,
    SuspensionDay,
    price_limits_from_panel_rows,
    suspensions_from_panel_rows,
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
    ROW_FILTERABLE_ISSUE_CODES,
    DateCoverage,
    FieldCoverage,
    PanelStorageError,
    PartitionCoverage,
    ReadinessRequirement,
    RevisionCoverage,
)
from openalpha_cn.panel.store import EVENT_TIME_COLUMN, ColumnSpec, PanelStore, PartitionRef

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


def _year_sample(years: Sequence[int]) -> str:
    """Render a year set for an error message, capped the same way `_subject_sample` is.

    A full-range coverage demand against a sparsely ingested store can name three decades of
    missing years, and a refusal nobody reads is a refusal that does not work.
    """
    ordered = sorted(years)
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

    ## Every year is vetted before any year is written

    The guard runs over the whole split first, and only then does anything reach the store.
    Interleaving them -- vet 1990, write 1990, vet 1991, ... -- was the earlier shape, and it
    made the refusal itself destructive: a batch that trips the guard on its 30th year would
    already have replaced 29 partitions, leaving a store whose lifecycle years stop in the
    middle. That is precisely the state `load_stock_universe` cannot see (a year that was never
    written is indistinguishable from a year in which nothing happened), so a guard that
    produced it was manufacturing the fail-open it exists to prevent. The same reasoning covers
    an interruption or a full disk between two years; those can still happen, and
    `require_years_through` on the read side is what turns the result into a refusal rather
    than a smaller universe.

    Two passes is not full atomicity -- `PanelStore` has no multi-partition transaction, and
    giving it one is a change to its write contract rather than to this function. What it does
    buy is that the one failure mode this module *can* predict never fires mid-write, and the
    operator's remedy (re-fetch the whole registry) is always applied to an unchanged store.

    An upstream correction to a `list_date` is the case that legitimately trips the guard: the
    security moves out of its old lifecycle year, so that year's batch is one subject short and
    the whole write is refused. That is the right direction -- a silent move would delete a
    listing row -- and the escape route is to drop the affected partition and re-write, not a
    flag on this function. A `force` argument here would be a switch that turns the guard off
    exactly when it has something to say.
    """
    if batch.dataset != STOCK_BASIC_DATASET:
        raise PanelBatchError(
            f"expected the {STOCK_BASIC_DATASET!r} dataset, got {batch.dataset!r}"
        )
    by_year = split_panel_batch_by_year(batch, date_timezone=date_timezone)
    for year, yearly in by_year:
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
    return tuple(
        write_panel_batch(store, yearly, year=year, date_timezone=date_timezone)
        for year, yearly in by_year
    )


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
    require_years_through: int | None = None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> StockUniverse:
    """Read stored lifecycle years back as a `StockUniverse`, or refuse to.

    Fail-closed three times over. A partition that is missing, damaged, unprofiled, stale or
    described by an out-of-date coverage record is blocked at partition scope and reported
    by its structured issue codes. A gap in the requested years is refused here, because a
    skipped year is a silently *smaller* universe -- the same defect `build_trading_calendar`
    refuses a hole in a day sequence for. And a partition that passes both and is internally
    inconsistent -- an orphan delisting row, a duplicated security -- is refused afterwards by
    `stock_universe_from_panel_rows`.

    ## The read is as-of-sensitive, and until `V2-P4-076` it was not

    It took `read_if_ready()`, which judges `not_yet_knowable` on a **partition's** newest
    `available_time` -- so one security listing on the newest session made the whole registry
    unreadable at every earlier instant of that lifecycle year. That is what `V2-P4-076` found
    still standing after `V2-P4-061` moved the price datasets: measured on a real panel,
    `stock_basic` at 2026-08-19T00:00+08 refused every cross section about a session before that
    day, and the shortlist face exited `1` on it before any bar was read.

    It now takes `_read_visible_event_dated_rows` with `_knowable_through_the_same_day`, which is
    this dataset's own clock: `providers/tushare.py::_calendar_static_timeline` sets
    `available_time == event_time == midnight` on the lifecycle date, and the response row is
    *split* first precisely so a listing and a termination carry their own instants. The rows a
    read at `as_of` keeps are therefore exactly the rows the partition's date census places at or
    before `as_of`'s own day, and any difference is a named refusal.

    **The direction the filter can fail in is the safe one, and that is worth stating.** A
    listing is never later than its own termination, so removing rows the read cannot see can
    only leave a security reported as *still listed* -- which is what it was at that instant.
    The reverse -- a termination whose listing was filtered away -- is what
    `stock_universe_from_panel_rows` refuses by name, and the predicate cannot produce it.
    `tests/integration/panel/test_event_dated_visible_reads.py` drives both halves.

    ## `years` is the top of the window; the history underneath it is not the caller's to name

    Every lifecycle year the store holds **below the earliest requested year** is read whether
    it was asked for or not, and that is the fix `V2-P4-059` and `V2-P4-060` share. The reason
    is the partitioning: this dataset is keyed by the year a security's life *changed*, so the
    2026 partition is "the securities that listed or died in 2026" and not "the market in 2026"
    -- while `trade_cal` and `daily` are keyed by the year their data is *about*. One `--year`
    over all three therefore asks three different questions, and the registry's answer to it was
    the wrong one twice:

    - **The universe came back the size of one year's listings.** `factor build --year 2026`
      over a synthetic market of `V2-P4-004`'s measured 5,545 securities scored **eleven**,
      wrote them, exited 0, and `shortlist run` published a list cut from those eleven. The
      only trace was `universe_counts: [12]`, a count of the read rather than of the store.
    - **A mid-window delisting made the read structurally invalid.** A security that listed in
      1996 and died in 2026 has its listing row in the 1996 partition, so a 2026-only read is a
      delisting with no listing -- which `stock_universe_from_panel_rows` refuses, correctly and
      by name, as a partial read. Reading 1996 alongside 2026 is what stops it being one.

    Widening **downwards** is the direction that cannot install a look-ahead, which is why it is
    safe to do unasked: an earlier lifecycle event is strictly more knowable than a later one,
    and every horizon rule below keys off `resolved[-1]`, which the widening does not move. What
    the caller still states is the *top* of the window and which years it asserts must exist --
    both unchanged, and both still refused when they cannot be satisfied.

    The alternative was to widen the interior too, so that `years=(2020, 2026)` on a store
    holding 1991..2026 filled in 2021..2025 instead of being refused by the gap rule below. It
    is declined: those are years inside a range the caller *stated*, so a hole in them is a
    claim about coverage that turned out to be false, and answering it silently would retire a
    guard that is doing real work. Years beneath the range were never claimed either way.

    Making the caller name the earlier years instead was measured and does not exist as a
    remedy. `--year` is one scope over three datasets, so `--year 2026 --year 2010` asks the
    calendar for a 2010 partition and the price panel for a 2010 year; on a store built the way
    `README` builds one, neither is there, and putting them there is the ~282,000-request
    backfill `panel build --help` prices at "days rather than hours" -- to run a **one-day**
    reversal.

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

    What this cannot see *by itself* is a year that was **never ingested**, because a year with
    no partition is indistinguishable from a year in which nothing listed and nothing died --
    both are simply absent, and requiring a partition per calendar year unconditionally would
    make a sparse but correct history unloadable. Naming a year in `years` is how a caller
    asserts it should exist (readiness then blocks it as `partition_missing`), and
    `UniverseCompleteness.years_read` is what a report shows so the window is visible rather
    than assumed. `store.registered_years(STOCK_BASIC_DATASET)` is the natural argument -- and
    it is also the trap, because passing it makes the request exactly equal to whatever the
    store happens to hold, so a missing year can never be noticed.

    `require_years_through` is the switch that turns that fail-open into a refusal. Give it the
    last year the universe must cover and the read demands a *contiguous* window from
    `years[0]` through that year: a caller that reads `registered_years()` and gets
    `(1990, 1991, 1992, 2020)` while asking for coverage through 2026 is refused here rather
    than being handed a universe in which every security that died in 2024 is still listed. It
    is opt-in because only the caller knows which window its question needs, and the demand is
    a real one on this dataset rather than a theoretical one: a live probe of the full registry
    on 2026-08-08 found lifecycle events in **every** year from 1990 to 2026 -- 6,217 rows over
    37 years, the thinnest being 2013's eight -- so a gap in a full-range read is a gap in the
    ingest, not a quiet year. On a store whose ingest is complete the contiguous demand costs
    nothing; on one whose ingest is not, it is the difference between a refusal and a
    reconstruction that reports the dead as listed.

    Starting the range after the first listing used to be the third fail-open here, left to be
    caught downstream: a security that listed in 1991 and died in 2020 has no listing row inside
    2015..2020, so `stock_universe_from_panel_rows` raised rather than inventing one, and a
    security that listed before the window and never died was caught one layer further on by
    `StockUniverse.listed_on` refusing a day earlier than the first year read. Both of those
    guards remain and both are still reachable -- on a store whose earliest lifecycle partitions
    were never ingested, there is no history to widen into. What changed in `V2-P4-059/060` is
    that an *ordinary* store no longer produces the state they guard, because the read no longer
    starts after the first listing when the store has the years to start at it.

    ## Cost, measured rather than estimated

    Readiness is assessed once per year read, because the filtered door -- like `read_if_ready()`
    before it -- vets the dataset and reads one partition. `PanelStore.assess_readiness`
    evaluates the whole requirement each time, so a full-history read re-evaluates every year's
    catalog rows once per year: N partitions cost N**2 coverage lookups.

    **This paragraph used to say that was milliseconds, and `V2-P4-059` measured it and it is
    not.** On a 36-year registry over `V2-P4-004`'s 5,545-security market, one call is **4.0 s**,
    of which 4.6 s of profiled time is 1,296 `_read_coverage` round trips and 0.21 s is the
    Parquet the read actually wanted. The old claim was true of the fixtures it was written
    against -- a handful of years -- and a whole history is 36, so the quadratic was invisible
    until something read one.

    Two things follow, and only the first is this function's to fix. The **shape** was already
    being paid: `cli._stored_universe` and `panel_doctor` have always passed every registered
    year, so `panel build` and `panel doctor` already spend it once per invocation. What the
    widening above changes is that `factor build` and `shortlist run` now pay it too, and they
    pay it **per prediction instant** rather than once. That is the price of the universe being
    the market rather than one year's listings, and it is the right trade at this size; it would
    stop being right on a walk-forward with hundreds of instants.

    The remedy is one assessment plus N reads, which cannot be done here: the verdict is
    identical across all 36 calls, but folding it into a single assessment is a change to
    `read_if_ready`'s contract -- `V2-P1-003`'s readiness surface, shared with fourteen callers
    and every other dataset -- and doing it locally would mean this one loader stepping around
    the fail-closed door the others take, losing its damaged-partition wrapping with it. Filed
    against `PanelStore` rather than worked around here.

    **`V2-P4-076` did not fix that and made it fractionally worse, which is stated rather than
    left to be discovered.** `read_visible_at` runs `assess_readiness` itself, exactly as
    `read_if_ready` did, so the quadratic is untouched; what the new door adds on top is one
    partition-scope assessment before the loop and one `read_coverage` per year for the census.
    On the 36-year registry that is 1,332 coverage lookups against 1,296 and 36 more census
    reads -- about 3% -- against a refusal it removes outright.
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise PanelBatchError(
            "load_stock_universe needs at least one lifecycle year; a read of no years would "
            "produce an empty registry that answers 'nothing was listed' to every day"
        )
    if require_years_through is not None:
        read = set(requested)
        absent = [
            year for year in range(requested[0], require_years_through + 1) if year not in read
        ]
        if absent:
            raise PanelBatchError(
                f"this read of {STOCK_BASIC_DATASET} was asked to cover every lifecycle year "
                f"from {requested[0]} through {require_years_through} and does not read "
                f"{_year_sample(absent)}; a lifecycle year that is never read is a year whose "
                "terminations are missing, and the universe reports those securities as still "
                "listed with nothing to signal it"
            )
    held = store.registered_years(STOCK_BASIC_DATASET)
    # `resolved` is the caller's window with the store's history underneath it. Everything
    # added is strictly below `requested[0]`, so `resolved[-1] is requested[-1]` and the two
    # rules below -- the gap refusal and the snapshot horizon -- judge the same span they
    # judged before the widening. Both are written against `resolved` because it is the set
    # this read actually covers; that they could equally be written against `requested` is the
    # invariant, not a coincidence, and two mutation probes confirmed the equivalence.
    resolved = tuple(year for year in held if year < requested[0]) + requested
    registered = set(held) - set(resolved)
    skipped = sorted(year for year in registered if resolved[0] < year < resolved[-1])
    if skipped:
        raise PanelBatchError(
            f"the requested {STOCK_BASIC_DATASET} years {requested[0]}..{requested[-1]} skip "
            f"{skipped}, which the store holds; a skipped year drops that year's listings and "
            "terminations and produces a smaller universe that looks entirely plausible"
        )
    requirement = stock_universe_requirement(
        years=resolved, as_of=as_of, max_staleness=max_staleness
    )
    rows = list(
        _read_visible_event_dated_rows(
            store,
            requirement,
            UNIVERSE_PANEL_COLUMNS,
            as_of=as_of,
            what="the security registry",
            availability_rule=(
                "A lifecycle row's availability is midnight on the day it is about, because "
                "the response row is split so that a listing and a termination carry their own "
                "instants"
            ),
            census_through=_knowable_through_the_same_day,
        )
    )
    snapshot_date = as_of.astimezone(_resolve_timezone(date_timezone)).date()
    unread_after = sorted(year for year in registered if year > resolved[-1])
    if unread_after:
        snapshot_date = min(snapshot_date, date(unread_after[0], 1, 1) - timedelta(days=1))
    return stock_universe_from_panel_rows(rows, snapshot_date=snapshot_date, years_read=resolved)


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

    ## As-of-sensitive since `V2-P4-076`

    It took `read_if_ready()`, so a single rename announced on the newest session refused the
    whole announcement year at every earlier instant -- and this corpus really does carry rows
    dated ahead of a fetch: a live fetch on 2026-08-08 already held `920165.BJ` / 珈凯生物
    announced 2026-08-11. It now takes `_read_visible_event_dated_rows` with
    `_knowable_through_the_same_day`, which is `_calendar_static_timeline`'s rule for this
    dataset: `available_time == event_time == midnight` on `ann_date`, with the effective date
    riding along as an ordinary column because it is published *in* the announcement.

    **`NameHistory` has deliberately no upper horizon, and that is what makes the census the
    whole grant.** The last record answers for every later day, so a corpus short by a withheld
    announcement answers with the *previous* name and nothing on it says so -- an ST name read
    as ordinary, which `shortlist_view._bars_on` turns into `is_st=False` and a screen turns
    into a wider band. What the predicate removes is announcements made after `as_of`, which is
    what "knowable at `as_of`" means rather than a shortfall; what the census refuses is an
    announcement the partition says had already been made and the read could not see.
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
    return name_histories_from_panel_rows(
        _read_visible_event_dated_rows(
            store,
            requirement,
            NAME_HISTORY_PANEL_COLUMNS,
            as_of=as_of,
            what="the rename corpus",
            availability_rule=(
                "A rename's availability is midnight on its own announcement date, because the "
                "date the new name takes effect is published in that announcement and rides "
                "along as an ordinary column"
            ),
            census_through=_knowable_through_the_same_day,
        )
    )


def merge_panel_batches(batches: Sequence[ColumnarPanelBatch]) -> ColumnarPanelBatch:
    """Concatenate several batches of one dataset into one. The inverse of
    `split_panel_batch_by_year`.

    ## Why the panel plane needs this at all

    A partition is a `(dataset, year)` pair and `PanelStore.write_partition` replaces it
    whole -- there is no append. A dataset whose *request* is narrower than a year therefore
    cannot be ingested one request at a time: every write after the first would destroy the
    partition it was meant to extend. `trade_cal` escapes this because one request is one
    year; `adj_factor` cannot, because one whole-market year is ~1.3e6 rows against a
    6,000-row response cap, so a year is 244 cross sections and they have to become one batch
    before anything reaches the store. `V2-P1-007`'s `daily` has the identical shape.

    Rows are kept in the order the batches are given, and every one of them survives -- this
    function does no filtering, so a caller can check `sum(row_count)` against the result.
    `as_of` and `fetched_at` become the **latest** of the inputs': the merged batch was
    knowable no earlier than its newest row, and `ColumnarPanelBatch` re-checks that itself.

    `source_uri` becomes `None` unless every input agrees on one. That is a real loss and is
    not dressed up: the per-fetch URIs are not recoverable from the merge. What survives is
    stronger for the question a partition is actually asked -- every row still carries its own
    subject and date columns, and `PartitionCoverage.batch_digest` still re-proves the whole
    partition against the batch that produced it.

    Refuses a `no_data` batch, an empty input, and any disagreement about provider, dataset,
    kind, schema version or column shape. None of those is repairable: a merged batch with two
    datasets in it has no partition to go to, and a column set that differs between inputs has
    no aligned row block.
    """
    entries = tuple(batches)
    if not entries:
        raise PanelBatchError(
            "merge_panel_batches needs at least one batch; an empty merge would produce a "
            "batch with no rows, which the contract refuses anyway and which would be "
            "indistinguishable from a fetch that returned nothing"
        )
    for index, batch in enumerate(entries):
        if batch.status != "success":
            raise PanelBatchError(
                f"cannot merge a {batch.status!r} batch (index {index}): "
                f"{batch.no_data_reason!r}; an explicit no-data result is something to record, "
                "not rows to concatenate"
            )
    first = entries[0]
    shape = tuple((column.name, column.kind) for column in first.columns)
    for index, batch in enumerate(entries[1:], start=1):
        if batch.dataset != first.dataset:
            raise PanelBatchError(
                f"every batch must carry the same dataset; index 0 is {first.dataset!r} and "
                f"index {index} is {batch.dataset!r}"
            )
        for field_name in ("provider_id", "kind", "schema_version"):
            if getattr(batch, field_name) != getattr(first, field_name):
                raise PanelBatchError(
                    f"every batch must carry the same {field_name}; index 0 is "
                    f"{getattr(first, field_name)!r} and index {index} is "
                    f"{getattr(batch, field_name)!r}"
                )
        if tuple((column.name, column.kind) for column in batch.columns) != shape:
            raise PanelBatchError(
                f"every batch must carry the same columns in the same order; index 0 is "
                f"{list(shape)} and index {index} is "
                f"{[(column.name, column.kind) for column in batch.columns]}"
            )
    source_uris = {batch.source_uri for batch in entries}
    return ColumnarPanelBatch(
        provider_id=first.provider_id,
        dataset=first.dataset,
        kind=first.kind,
        as_of=max(batch.as_of for batch in entries),
        fetched_at=max(batch.fetched_at for batch in entries),
        status="success",
        subjects=tuple(subject for batch in entries for subject in batch.subjects),
        timeline=TimelineColumns(
            **{
                name: tuple(value for batch in entries for value in getattr(batch.timeline, name))
                for name in ("event_time", "available_time", "ingested_time", "revision_time")
            }
        ),
        columns=tuple(
            PanelColumn(
                name,
                kind,
                tuple(value for batch in entries for value in batch.columns[position].values),
            )
            for position, (name, kind) in enumerate(shape)
        ),
        source_uri=source_uris.pop() if len(source_uris) == 1 else None,
    )


def carry_stored_rows_forward(
    store: PanelStore,
    batch: ColumnarPanelBatch,
    *,
    year: int,
    retain: Callable[[Mapping[str, object]], bool],
) -> ColumnarPanelBatch:
    """`batch` with the stored partition's own rows in front of it, wherever `retain` says so.

    `V2-P4-071`. `merge_panel_batches` above is the answer for a dataset whose *fetches* are
    narrower than its partition, and it works because every fetch of one year is in hand at once.
    A **derived** partition is not like that: `openalpha factor build` computes one cross section
    at one instant, a year holds as many of them as somebody chose to build, and tomorrow's
    invocation does not have yesterday's in hand. `PanelStore.write_partition` replaces a
    partition whole, so tomorrow's write either recomputed every instant of the year or destroyed
    them -- which is the wall the product acceptance measured, verbatim::

        $ openalpha factor build --factor reversal_1d/v1 --as-of <day one>    # exit 0
        $ openalpha factor build --factor reversal_1d/v1 --as-of <day two>    # exit 1
        factor_manifest_reversal_1d_v1 year=2026 already holds 1 subject(s) and this write
        carries 1; it would drop ['fmn_...']

    This is the third primitive beside those two: the partition's own stored bytes, read back and
    put in front of the arriving batch, so that a whole-partition replace **is** an append. The
    caller decides what survives with `retain`, which is handed one stored row as a mapping keyed
    by the batch's own storage columns (`subject`, the four clocks, then the data columns).

    ## What this does and deliberately does not weaken

    Nothing here touches a drop guard. `panel_factors._refuse_to_drop_a_stored_build` and
    `_refuse_to_drop_stored_subjects` still run, still read the catalog's stored subject list, and
    still refuse a write that would lose one. What changes is their *relationship to the caller*:
    they stop being an instruction to go and recompute the year, and become the audit on this
    merge. That is the property worth having: the merge is the thing that can be wrong, and the
    guard is what catches it being wrong.

    **The paragraph above used to end by claiming that property outright, and `V2-P4-073` measured
    it half true.** It read: "A `retain` with a hole in it -- one that mis-reads which build a row
    belongs to, or that silently drops a clock column -- produces exactly the refusal it produced
    before, naming the builds that went missing." The catalog's stored subject list names *builds*
    on a manifest partition and *securities* on an observation one, so on the plane where the
    subjects are securities the drop guard could not express the claim and the writers did not run
    it there at all. A hole confined to the observation merge therefore wrote and reported
    success. `panel_factors._refuse_a_merge_that_lost_a_stored_build` is what makes the sentence
    true now: it asks the same question of the merged batch's own build column, so it holds on
    every plane, and it is why `appended_to_the_stored_year` returns an `AppendedYear` rather than
    a bare batch.

    ## The read is un-gated, and it has to be

    `PanelStore.query` takes no `as_of` and filters no row by availability, and this is one of the
    two callers in `src/` allowed to take it (`tests/unit/panel/test_query_callers.py` is the
    allowlist). A point-in-time read here would be the fail-open, not the safe choice: a carry-
    forward that filtered by `available_time` would carry only the rows knowable at some instant
    and would then hand the store a partition **missing** the withheld ones -- which
    `write_partition` would commit, destroying exactly the data this function exists to preserve.
    The rows are not being *answered with*; they are being put back where they were found.

    `as_of` and `fetched_at` come off the stored partition's own coverage record rather than being
    invented, because the carried rows may be newer than the arriving batch: building an *earlier*
    instant after a later one is an ordinary backfill, and a batch claiming an `as_of` before its
    own newest `available_time` is one `ColumnarPanelBatch` refuses. `merge_panel_batches` then
    takes the later of the two, which is the merged partition's honest answer either way.

    A partition with no coverage record is carried forward not at all, which is
    `_refuse_to_drop_stored_subjects`' own rule and its reason: that state is an interrupted write,
    readiness already blocks it as `coverage_missing`, and there is nothing to read the stored
    shape from. Returns `batch` unchanged in that case, and whenever `retain` admits no row.

    **A stored partition whose columns are not the arriving batch's is refused rather than
    appended to**, and it is refused by the read: the columns are `batch.storage_columns()`' own
    names, so a partition written by a build with a different column list raises out of
    `PanelStore.query` before anything is merged. That is fail-closed and it is the right way
    round -- `_manifest_cells`' "refusing the wrong width" one plane over, for the same reason.
    Two column lists in one partition have no aligned row block, and the remedy is the one the
    drop guard already names: supersede the builds written under the old shape.
    """
    coverage = store.read_coverage(batch.dataset, year)
    if coverage is None:
        return batch
    names = tuple(column.name for column in batch.storage_columns())
    stored = store.query(batch.dataset, year=year, columns=names)
    kept = [row for row in stored if retain(dict(zip(names, row, strict=True)))]
    if not kept:
        return batch
    held = {name: tuple(row[index] for row in kept) for index, name in enumerate(names)}
    carried = ColumnarPanelBatch(
        provider_id=batch.provider_id,
        dataset=batch.dataset,
        kind=batch.kind,
        as_of=coverage.as_of,
        fetched_at=coverage.fetched_at,
        status="success",
        subjects=tuple(str(value) for value in held[SUBJECT_COLUMN_NAME]),
        timeline=TimelineColumns(
            **{
                name: tuple(cast(datetime, value) for value in held[name])
                for name in CLOCK_COLUMN_NAMES
            }
        ),
        columns=tuple(
            PanelColumn(column.name, column.kind, held[column.name]) for column in batch.columns
        ),
        source_uri=None,
    )
    return merge_panel_batches((carried, batch))


def compress_adjustment_batch(batch: ColumnarPanelBatch) -> ColumnarPanelBatch:
    """Reduce a factor batch to the rows its step function cannot be rebuilt without.

    Per security: the window's first observation, every observation whose factor differs from
    its predecessor, and the window's last observation -- see
    `domain/adjustment.py::load_bearing_observations` for why all three kinds are load-bearing
    and what the last one is protecting.

    Measured on the real series: `000001.SZ`'s 8,627-row history takes 43 distinct values and
    therefore moves at 42 change points, which with both anchors is **44 stored rows** -- a
    **196x** reduction, not the 201x that quoting the distinct-value count as though it were
    the row count produces. The whole market's daily form would be ~4.8e7 rows for a series
    that moves about once per security per year. Storing the days instead is a defensible
    choice and is not the one taken; storing the *steps without the closing anchor* is not
    defensible, and is the mistake this function's last line exists to avoid.

    Idempotent: compressing an already-compressed batch is a no-op, because a kept row is
    either an endpoint (still an endpoint) or a change (still a change).

    Row order in the result is `(subject, factor_date)` ascending, which makes the partition's
    content hash independent of the order the day batches happened to be merged in.

    Three shapes are refused rather than compressed, and all three are ordinary operator
    mistakes rather than hypotheticals. A **null factor** is a legal value of a `float` panel
    column (panel data is sparse) and is meaningless here -- it would compare unequal to
    nothing, ride through as an unchanged row, and only fail on the way back out, leaving a
    partition that passes readiness and explodes at parse. A **null or unparseable
    `factor_date`** is refused by the same named error for the same reason, rather than
    reaching `date.fromisoformat` and surfacing as a bare
    `ValueError: Invalid isoformat string: 'None'` -- a guard that is not of a piece with the
    one beside it is a guard a caller cannot catch uniformly. And a **repeated session** for
    one security -- what merging the same day's batch twice produces -- is refused by
    `load_bearing_observations`' ascending rule, because a step function with two entries for
    one day has no defined value on it.
    """
    _require_adjustment_batch(batch)
    if batch.status != "success":
        raise PanelBatchError(f"cannot compress a {batch.status!r} batch: {batch.no_data_reason!r}")
    dates = _column_values(batch, ADJUSTMENT_DATE_COLUMN)
    factors = _column_values(batch, ADJUSTMENT_FACTOR_COLUMN)
    if None in factors:
        raise PanelBatchError(
            f"{ADJUSTMENT_FACTOR_COLUMN} row {factors.index(None)} is null; a missing "
            "observation is ordinary in a panel column and impossible in a step function -- "
            "there is no price it could scale"
        )
    parsed = _adjustment_dates(dates)
    by_subject: dict[str, list[int]] = {}
    for index, subject in enumerate(batch.subjects):
        by_subject.setdefault(subject, []).append(index)
    kept: list[int] = []
    for subject in sorted(by_subject):
        indices = sorted(by_subject[subject], key=lambda index: parsed[index])
        observations = [
            FactorObservation(
                ts_code=subject,
                observed_on=parsed[index],
                factor=cast(float, factors[index]),
            )
            for index in indices
        ]
        wanted = {entry.observed_on for entry in load_bearing_observations(observations)}
        kept.extend(
            index
            for entry, index in zip(observations, indices, strict=True)
            if entry.observed_on in wanted
        )
    return _select_rows(batch, kept)


def _require_adjustment_batch(batch: ColumnarPanelBatch) -> None:
    """One copy of "this is the factor dataset", called by both things that walk its columns."""
    if batch.dataset != ADJ_FACTOR_DATASET:
        raise PanelBatchError(f"expected the {ADJ_FACTOR_DATASET!r} dataset, got {batch.dataset!r}")


def _stored_dates(values: tuple[object, ...], column: str) -> tuple[date, ...]:
    """Parse an ISO date column, naming a null or malformed entry rather than tripping.

    The symmetric half of `compress_adjustment_batch`'s null-factor guard. `str(None)` is
    `'None'`, so without this the null case reached `date.fromisoformat` and came back out as
    a bare `ValueError: Invalid isoformat string: 'None'` -- not a `PanelBatchError`, and so
    not caught by a caller who is catching the guard standing right next to it.

    Parameterised by column name so the price datasets' `trade_date` and the factor series'
    `factor_date` share one implementation; the wording stays the same for both because the
    complaint is the same one.
    """
    parsed: list[date] = []
    for index, value in enumerate(values):
        if not isinstance(value, str):
            raise PanelBatchError(
                f"{column} row {index} is "
                f"{'null' if value is None else f'a {type(value).__name__}'}; a step function "
                "has no value on a session it cannot name"
            )
        try:
            parsed.append(date.fromisoformat(value))
        except ValueError as error:
            raise PanelBatchError(f"{column} row {index} is not an ISO date: {value!r}") from error
    return tuple(parsed)


def _adjustment_dates(values: tuple[object, ...]) -> tuple[date, ...]:
    """`_stored_dates` for the factor series' own date column."""
    return _stored_dates(values, ADJUSTMENT_DATE_COLUMN)


def _column_values(batch: ColumnarPanelBatch, name: str) -> tuple[object, ...]:
    """One of `batch`'s own data columns by name, or a `PanelBatchError` naming what is
    there. `batch.columns` is caller-supplied, so the column may simply be absent."""
    for column in batch.columns:
        if column.name == name:
            return column.values
    raise PanelBatchError(
        f"this {batch.dataset} batch has no {name!r} column; available: "
        f"{sorted(column.name for column in batch.columns)}"
    )


def _refuse_missing_factor_sessions(
    batch: ColumnarPanelBatch,
    calendar: TradingCalendar,
    year: int,
    *,
    date_timezone: str,
) -> None:
    """Refuse a pre-compression batch that is missing a session the calendar reports open.

    ## Why this exists at all

    `adjustment_requirement()` waives `required_dates`, so nothing on the *read* side counts
    this dataset's sessions. The compressed partition still carries each security's first and
    last observation, and it is tempting to call those anchors an equivalent guarantee. They
    are not, and the difference is not academic. `AdjustmentHistory.factor_on` refuses a day
    outside `[covered_from, covered_through]` -- but those bounds are computed over whatever
    rows a read *concatenated*, so a hole strictly inside them is answered by `bisect` from
    the last step before it, with no error anywhere. Measured on the real series: a 2026
    partition assembled from 06-15 onward, read beside earlier years, answers
    `factor_on(2026-06-12)` with 134.5794 instead of 139.008 and turns that day's return from
    +2.742251% into -0.530973% -- the unadjusted number, sign and all, from a partition that
    reports `ready=True` with no issues.

    ## Direction, and why it is not equality

    Only the **missing** direction is refused. A session the batch carries that the calendar
    calls closed is left alone, because that is a real and harmless shape: `000001.SZ` has
    factor rows on 64 dates in 1991 that are outside the SZSE calendar entirely (its series
    starts 1991-07-03, the factor starts 1991-04-03), every one of them an SSE session. An
    equality check would refuse that partition; requiring only coverage tolerates the extra
    observations, which can add information and cannot remove any. This is the same asymmetry
    that kept a calendar-derived row count out of the provider's truncation guard -- there the
    calendar could only manufacture false alarms, here it is used in the one direction where
    it cannot.

    ## The two bounds

    The lower bound is the year's own start, not the batch's first row -- a partition that
    begins in March is exactly the failure mode this function exists for, and clamping the
    expectation at its own first row would define the hole out of existence.

    The upper bound is the day before the fetch, in `date_timezone`. A backfill of a past year
    therefore has to cover that whole year (its `fetched_at` is long after it), while the
    running year is required only through the last session that had certainly closed when the
    fetch ran. The one-session slack is deliberate: `ClockStrategy.daily_close` publishes a
    session at 16:30 local, so a fetch earlier on that same day cannot hold it and requiring
    it would be a false alarm on every intraday run.

    Raises `CalendarHorizonError` (through `trading_days_between`) when the calendar does not
    reach across the year being written. That is the honest answer rather than a gap in the
    guard: a calendar that stops in June cannot testify about December.
    """
    _require_adjustment_batch(batch)
    census = _session_census(
        batch, calendar, year, date_column=ADJUSTMENT_DATE_COLUMN, date_timezone=date_timezone
    )
    if census is None:
        # The batch was fetched on 1 January of its own year, so no session in it had closed
        # before the fetch and there is nothing the calendar can require.
        return
    missing, opens_on, closes_on = census
    if missing:
        raise PanelBatchError(
            f"{ADJ_FACTOR_DATASET} year={year} is missing {len(missing)} session(s) the "
            f"{calendar.exchange} calendar reports open between {opens_on.isoformat()} and "
            f"{closes_on.isoformat()}: {_date_sample(missing)}. The partition stores steps, "
            "so a missing session is not a shorter answer -- once this year is read beside "
            "another, every day in the hole falls inside covered_from/covered_through and is "
            "answered from the last step before it, which is the unadjusted number wearing an "
            "adjusted one's name. Fetch the missing sessions and write the year in one call"
        )


def _session_census(
    batch: ColumnarPanelBatch,
    calendar: TradingCalendar,
    year: int,
    *,
    date_column: str,
    date_timezone: str,
) -> tuple[list[date], date, date] | None:
    """Which sessions the calendar reports open in `year` that `batch` does not carry.

    The arithmetic behind both write-time censuses -- `adj_factor`'s and the price panel's --
    extracted so the two cannot drift apart on the part that is easy to get wrong. The two
    callers keep their own refusal messages, because *why* a missing session matters differs:
    a factor hole answers later days from an older step, a price hole leaves a session with no
    bars for the whole market.

    Returns `None` when there is nothing the calendar can require: the batch was fetched on 1
    January of its own year, so no session of it had closed before the fetch.

    The **lower** bound is the year's own start, not the batch's first row -- a partition that
    begins in March is exactly the failure this exists for, and clamping at its own first row
    would define the hole out of existence. The **upper** bound is the day before the fetch, in
    `date_timezone`: a session publishes at 16:30 local (`DAILY_AVAILABILITY_TIME`), so a fetch
    earlier that same day cannot hold it and requiring it would be a false alarm on every
    intraday run.

    Only the *missing* direction is computed. A session the batch carries that the calendar
    calls closed is left alone, because that is a real and harmless shape -- `000001.SZ` has
    factor rows on 64 dates in 1991 that are outside the SZSE calendar entirely -- and an extra
    observation can add information while it cannot remove any.

    Raises `CalendarHorizonError` (through `trading_days_between`) when the calendar does not
    reach across the year. That is the honest answer rather than a gap in the guard: a calendar
    that stops in June cannot testify about December.
    """
    zone = _resolve_timezone(date_timezone)
    opens_on = date(year, 1, 1)
    closes_on = min(
        date(year, 12, 31), batch.fetched_at.astimezone(zone).date() - timedelta(days=1)
    )
    if closes_on < opens_on:
        return None
    observed = set(_stored_dates(_column_values(batch, date_column), date_column))
    missing = [
        day for day in calendar.trading_days_between(opens_on, closes_on) if day not in observed
    ]
    return missing, opens_on, closes_on


def _date_sample(days: Sequence[date]) -> str:
    """Render a date set for an error message, capped the way `_subject_sample` is."""
    ordered = [day.isoformat() for day in sorted(days)]
    if len(ordered) <= _SUBJECT_SAMPLE:
        return str(ordered)
    return f"{ordered[:_SUBJECT_SAMPLE]} and {len(ordered) - _SUBJECT_SAMPLE} more"


def write_adjustment_factors(
    store: PanelStore,
    batches: Sequence[ColumnarPanelBatch],
    *,
    calendar: TradingCalendar,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> PartitionRef:
    """Merge one year of factor cross sections, compress them, and write the partition.

    Takes a *sequence* of batches where every other writer here takes one, and the reason is
    the arithmetic rather than a preference: an `adj_factor` request is one trading day of the
    whole market (~5,400 rows against a 6,000-row cap), a partition is a calendar year, and
    one whole-market year is ~1.3e6 rows. So a year is ~244 fetches, and they have to become
    one batch before the store sees them -- `PanelStore.write_partition` replaces a partition
    whole, so writing them one at a time would leave the year holding only its last session.

    Three guards, in order, and the middle one is the reason `calendar` is a required argument
    rather than a convenience:

    1. `panel_partition_year` refuses a set of batches that straddles two years rather than
       picking one.
    2. `_refuse_missing_factor_sessions` refuses a year that is missing a session the calendar
       reports open -- **before** compression, which is the only moment the raw session census
       still exists. See that function for the measured wrong answer it prevents.
    3. `_refuse_to_drop_stored_subjects` refuses a rewrite that would remove a security the
       stored partition already had -- the same guard the registry uses, for the same reason.

    Guards 2 and 3 are the two halves of the same failure and neither substitutes for the
    other: a missing *security* makes the answer shorter, a missing *session* makes it wrong.
    """
    # The dataset check belongs to `_require_adjustment_batch` and is called by both the
    # session census and `compress_adjustment_batch`; `merge_panel_batches` has already made
    # every input agree on one dataset by this point.
    merged = merge_panel_batches(batches)
    _require_adjustment_batch(merged)
    year = panel_partition_year(merged, date_timezone=date_timezone)
    _refuse_missing_factor_sessions(merged, calendar, year, date_timezone=date_timezone)
    compressed = compress_adjustment_batch(merged)
    _refuse_to_drop_stored_subjects(
        store,
        compressed,
        year,
        remedy=(
            "A year's partition is replaced whole, so every session of the year has to arrive "
            "in one call; a narrower cross section is a partial fetch rather than news"
        ),
    )
    return write_panel_batch(store, compressed, year=year, date_timezone=date_timezone)


def adjustment_requirement(
    *, years: Sequence[int], as_of: datetime, max_staleness: timedelta | None
) -> ReadinessRequirement:
    """What the factor series must satisfy before histories may be built from it.

    Two of the four checks are waived, and both waivers land in
    `DatasetReadiness.checks_waived` rather than being invisible:

    - **`required_dates`** is waived because the partition is a *compressed* step function.
      Its date census holds only the load-bearing sessions -- eight rows for two securities
      across a real trading week -- so a calendar-derived expectation would report a
      permanent `date_gap` on a partition that is complete by construction.
      `panel_readiness_requirement` must therefore **not** be pointed at this dataset.

      **What replaces it is a check at write time, not a property of the stored rows.**
      `write_adjustment_factors` requires a `TradingCalendar` and refuses a partition that is
      missing any session the calendar reports open, while the raw census still exists. An
      earlier version of this docstring claimed the window anchors were the replacement --
      that every day inside `covered_from`/`covered_through` is "answered from a real
      observation". The clause is true and the safety it implies is not: the anchors bound
      one partition's *ends*, and a read that concatenates years is bounded only by the
      outermost pair, so a hole inside a partition (or a partition that starts in March) sits
      inside those bounds and is answered by `bisect` from the previous step, with nothing
      raising. That is measured, not hypothetical -- see `_refuse_missing_factor_sessions`
      for the real security, day and pair of numbers.

      Two residues remain named rather than papered over. A year written from a batch fetched
      on 1 January of that same year is not checked (nothing in it had closed before the
      fetch). And this is a write-time gate: a partition written before the gate existed is
      not re-examined on read, because the compressed rows no longer carry the census that
      would settle it.
    - **`required_subjects`** is waived because naming the securities would be circular: the
      cross section is what the read is for. `adjustment_factors_on` is where a listed
      security with no factor becomes a refusal, because that is where the universe is known.

    `max_staleness` is **not** waived by default and has no default value: the caller states a
    bound or states `None` on the record. A factor series whose newest session is a month old
    has missed a month of corporate actions, so leaving it to a default would be choosing
    silence.
    """
    return ReadinessRequirement(
        dataset=ADJ_FACTOR_DATASET,
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=ADJUSTMENT_PANEL_COLUMNS,
        max_staleness=max_staleness,
    )


def load_adjustment_histories(
    store: PanelStore,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
) -> Mapping[str, AdjustmentHistory]:
    """Read stored factor years back as one `AdjustmentHistory` per security, or refuse to.

    Fail-closed three times over. A partition that is missing, damaged, unprofiled, stale or
    described by an out-of-date coverage record is blocked by `read_if_ready()` and reported
    by its structured issue codes. A **gap in the requested years** is refused here. And a
    partition that passes both and is internally inconsistent -- two different factors on one
    session, a factor that cannot scale a price -- is refused afterwards by
    `adjustment_histories_from_panel_rows`.

    ## Why the gap rule is stricter here than it is for the registry

    `load_stock_universe` refuses a requested range that skips a year *the store holds*, and
    tolerates one it does not, because a lifecycle year with no partition is genuinely
    indistinguishable from a year in which nothing listed and nothing died. A factor year is
    not like that. The history is a step function read by `bisect`, so a missing year in the
    middle is not a smaller answer -- it is a **wrong** one: 2023's closing anchor would answer
    every 2024 and 2025 day, asserting that no corporate action happened in two years nobody
    looked at. So the requested years must be a contiguous run, whatever the store happens to
    hold, and a year inside that run with no partition then blocks as `partition_missing`.

    What this still cannot see is a year outside the requested range -- the same limitation
    `load_trading_calendar` has, and answered the same way: `AdjustmentHistory.factor_on`
    refuses a day outside `[covered_from, covered_through]` rather than extrapolating, so the
    window a read actually saw is the window it will answer for.
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise PanelBatchError(
            "load_adjustment_histories needs at least one factor year; a read of no years "
            "would produce an empty corpus that refuses every question"
        )
    absent = [year for year in range(requested[0], requested[-1] + 1) if year not in set(requested)]
    if absent:
        raise PanelBatchError(
            f"the requested {ADJ_FACTOR_DATASET} years {requested[0]}..{requested[-1]} skip "
            f"{_year_sample(absent)}; a factor history is a step function, so a skipped year "
            "is not a shorter answer but a wrong one -- the last factor before the gap would "
            "answer every day inside it"
        )
    requirement = adjustment_requirement(years=requested, as_of=as_of, max_staleness=max_staleness)
    rows: list[tuple[object, ...]] = []
    for year in requested:
        outcome = store.read_if_ready(requirement, year=year, columns=ADJUSTMENT_PANEL_COLUMNS)
        if outcome.is_blocked:
            raise PanelStorageError(
                f"the adjustment factor series cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.readiness.issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.readiness.issues)}"
            )
        rows.extend(outcome.rows)
    return adjustment_histories_from_panel_rows(rows)


def _refuse_missing_price_sessions(
    batch: ColumnarPanelBatch,
    calendar: TradingCalendar,
    year: int,
    *,
    date_timezone: str,
) -> None:
    """Refuse a price year that is missing a session the calendar reports open.

    `_refuse_missing_factor_sessions`' homomorph, and the three differences from it are the
    point rather than incidental.

    **It is not paying for a waiver.** `adjustment_requirement` waives `required_dates`,
    because compression leaves the factor partition holding only load-bearing sessions -- so
    that census is the *replacement* for a read-side check, and its own residue is that a
    partition written before it existed is never re-examined. A price partition is stored
    uncompressed, so its date census survives into the catalog and `daily_requirement` states
    the same expectation on the read side, from the same calendar, on every read.

    **It runs on two datasets before either is written.** `daily` and `daily_basic` are two
    partitions of one set of sessions, and a `daily` year that is whole must not be stored
    beside a `daily_basic` year that is not.

    **What is missing costs something different.** A factor hole is answered by `bisect` from
    the previous step -- a wrong number. A price hole is answered by nothing: the session
    simply has no bars, and every cross section on it is empty. That is why the read side can
    carry this one and could not carry the factor one.
    """
    census = _session_census(
        batch, calendar, year, date_column=PRICE_DATE_COLUMN, date_timezone=date_timezone
    )
    if census is None:
        return
    missing, opens_on, closes_on = census
    if missing:
        raise PanelBatchError(
            f"{batch.dataset} year={year} is missing {len(missing)} session(s) the "
            f"{calendar.exchange} calendar reports open between {opens_on.isoformat()} and "
            f"{closes_on.isoformat()}: {_date_sample(missing)}. A session with no rows is a "
            "session on which every cross section is empty and every return that spans it is "
            "computed from the wrong pair of closes. Fetch the missing sessions and write the "
            "year in one call"
        )


def _refuse_thin_price_sessions(batch: ColumnarPanelBatch) -> None:
    """Refuse a price year holding a session whose cross section is a fraction of its siblings.

    ## The hole `_refuse_missing_price_sessions` leaves

    That census answers "is this session present at all". It cannot answer "did this session
    arrive whole", and the difference is not theoretical. A year assembled from 244 per-session
    fetches where **one** of them returned 3 of the market's 40 names stores a partition that
    passes every other guard: the date census carries the session, the year's subject set is
    complete because the other 243 sessions supply every code, `assess_readiness` reports
    `is_ready=True` with no issues, and `load_daily_bars` on that day returns 3 bars with no
    error and no warning. `priced_cross_section` then reports 3 priced and 37 unpriced -- which
    is honest, and is also exactly what a session with 37 halted names looks like.
    `_refuse_to_drop_stored_subjects` does not see it either, on a first write or a rewrite,
    because it compares *year* subject sets and this year's is whole.

    ## Why a row-count floor, and why it is this shape

    The per-session row counts are already in hand -- the same date column the census reads --
    so this costs no request and no extra pass over the store. The comparison is against the
    **median session of the same partition** rather than an absolute number, because the market
    grew from 1,022 names in 2001 to 5,535 in 2026 and any constant would be wrong at one end.

    `MIN_SESSION_ROW_SHARE` is set from measurement, not from taste; see its docstring in
    `domain/daily_prices.py` for the full-year censuses that fix it and for the residue, which
    is real: a fetch that returned *most* of the market is still invisible here, and `suspend_d`
    (`V2-P1-008`) remains what settles a thin session from a halted one.

    A partition holding a single session cannot fire this check -- its own count is the median
    -- and that is stated rather than defended: there is nothing in the batch to compare it to.
    There is no empty-batch branch for the same kind of reason: `ColumnarPanelBatch` refuses a
    batch with no rows and `merge_panel_batches` refuses an empty sequence of them, so `counts`
    always has at least one session and a guard here would be unreachable code.
    """
    counts = Counter(_stored_dates(_column_values(batch, PRICE_DATE_COLUMN), PRICE_DATE_COLUMN))
    typical = median(counts.values())
    floor = typical * MIN_SESSION_ROW_SHARE
    thin = sorted(day for day, rows in counts.items() if rows < floor)
    if not thin:
        return
    worst = min(thin, key=lambda day: counts[day])
    raise PanelBatchError(
        f"{batch.dataset} carries {len(thin)} session(s) with fewer than "
        f"{MIN_SESSION_ROW_SHARE:.0%} of this partition's median cross section "
        f"({typical:.0f} rows): {_date_sample(thin)}. {worst.isoformat()} has "
        f"{counts[worst]} row(s). A session that arrived short is stored as a well-formed "
        "partition -- the date census carries it, the year's subject set is complete from the "
        "other sessions, and readiness reports ready -- so every cross section on that day "
        "silently reports most of the market as unpriced. Re-fetch that session and write the "
        "year in one call"
    )


def _refuse_unexplained_thin_sessions(
    batch: ColumnarPanelBatch, halts: Mapping[date, SuspensionDay]
) -> None:
    """Refuse a price year whose thin sessions are **not** accounted for by halts (`V2-P1-008`).

    ## What this buys over `_refuse_thin_price_sessions`

    That floor sits at `MIN_SESSION_ROW_SHARE` = 0.5 of the partition's median, and it is there
    because 2015-07-09 legitimately served 1,363 rows against that year's median of 2,359 --
    a ratio of **0.578**. Any floor above that refuses a true partition of 2015, so "a fetch
    that returned most of the market" was invisible by construction.

    `suspend_d` removes the reason for the low floor rather than the floor. Counting each
    session's whole-day halts alongside its bars, that same session becomes 2,801 (1,363 bars +
    1,438 halts) against a comparable median of 2,796 -- it stops being thin at all.
    `MIN_EXPLAINED_SESSION_SHARE` is set from a census of **every session of 1991..2026**; see
    its docstring in `domain/price_limits.py` for that table and for the two boundaries below.

    ## The comparison figure is a rolling median, and the guard has a start date

    Both come out of that census and both exist because a three-year sample got them wrong.

    A session is compared against the median of the 41 sessions centred on it
    (`EXPLAINED_SESSION_HALF_WINDOW`), not against the partition's. Against the year's median,
    1994..1997 each hold dozens of sessions under 0.85 and **none of them is short** -- 1996
    opened at 313 bars and closed at 514 against a year median of 377, with `suspend_d` empty
    all year. The window compares a January session against January. On a partition of 41
    sessions or fewer it degenerates to the whole-partition median, which is what every
    small-batch caller sees.

    Sessions before `SUSPENSION_CORPUS_FIRST_SESSION` are **not judged here at all**, because
    the halt corpus has no rows before 1999-05-04. On those sessions the explained count is the
    bar count, so this would be a bare row-count floor at 0.85 sitting on top of one
    deliberately set to 0.5 -- a stronger refusal justified by no extra information, and it
    refuses true partitions of 1994 and 1995. `_refuse_thin_price_sessions` still runs on them,
    unconditionally, as it does on every session.

    ## Only whole-day halts count, and a missing day counts as zero

    `TradingState.interrupted` and `TradingState.resumed` both mean the security **traded**, so
    including them would let a session with 1,300 intraday halts explain away 1,300 missing
    bars -- the over-explanation that makes an alarm useless. `SuspensionDay.halted` is the only
    input.

    A session the mapping does not mention contributes zero halts. That is deliberate and it is
    the safe direction: `suspend_d` legitimately serves no rows for a session on which nothing
    happened, so "absent" and "no halts" are genuinely indistinguishable here, and a caller who
    supplied an incomplete suspension corpus gets a **false refusal** -- loud, and repaired by
    fetching the missing sessions -- rather than a quiet pass.
    """
    counts = Counter(_stored_dates(_column_values(batch, PRICE_DATE_COLUMN), PRICE_DATE_COLUMN))
    explained = {
        day: rows + (len(halts[day].halted) if day in halts else 0) for day, rows in counts.items()
    }
    judged = sorted(day for day in explained if day >= SUSPENSION_CORPUS_FIRST_SESSION)
    if not judged:
        return
    totals = [explained[day] for day in judged]
    local: dict[date, float] = {}
    for position, day in enumerate(judged):
        low = max(0, position - EXPLAINED_SESSION_HALF_WINDOW)
        high = position + EXPLAINED_SESSION_HALF_WINDOW + 1
        local[day] = median(totals[low:high])
    thin = [day for day in judged if explained[day] < local[day] * MIN_EXPLAINED_SESSION_SHARE]
    if not thin:
        return
    worst = min(thin, key=lambda day: explained[day] / local[day])
    halted_on_worst = len(halts[worst].halted) if worst in halts else 0
    raise PanelBatchError(
        f"{batch.dataset} carries {len(thin)} session(s) whose bars and halts together fall "
        f"under {MIN_EXPLAINED_SESSION_SHARE:.0%} of the median explained cross section of the "
        f"{2 * EXPLAINED_SESSION_HALF_WINDOW + 1} sessions around them: {_date_sample(thin)}. "
        f"{worst.isoformat()} has {counts[worst]} row(s) and {halted_on_worst} whole-day "
        f"halt(s), {explained[worst]} together, against a local median of "
        f"{local[worst]:.0f}. A session that arrived short and a session on which the "
        "market was shut look identical in the bars alone, which is why the row-count floor "
        "has to sit at half the median; with suspend_d they are distinguishable, and this one "
        "is not explained. Re-fetch that session -- and check that the suspension corpus covers "
        "it, because a session missing from it counts as zero halts here"
    )


def _close_index(batch: ColumnarPanelBatch) -> dict[tuple[str, date], float]:
    """Index a price batch's `close` column by `(subject, trade_date)`, refusing duplicates.

    Built from the columns directly rather than by constructing a `DailyBar` per row: a year of
    the whole market is ~1.3e6 rows, and this runs once per ingested year. Two C-level column
    reads and one `zip` is the same cost model `panel_coverage` states for its own census.

    **A repeated `(subject, session)` is refused rather than collapsed**, and that is not
    housekeeping. This index is what `_refuse_close_disagreement` compares, so a `dict` that
    silently kept the last of two rows would make the cross-check blind to duplication by
    construction -- and duplication is a real shape here, because a caller assembles a year
    from ~244 per-session batches and passing one of them twice is a loop bug rather than an
    exotic input. Left alone it stores a year with more rows than sessions, the write reports
    success, and every subsequent read of that session fails in `daily_bars_from_panel_rows`
    with "appears twice": a partition that is fail-closed on read and unreadable for good,
    since the only remedy is to rewrite the year. Refusing at the boundary keeps the store
    unchanged instead.
    """
    dates = _stored_dates(_column_values(batch, PRICE_DATE_COLUMN), PRICE_DATE_COLUMN)
    closes = _column_values(batch, CLOSE_COLUMN)
    index: dict[tuple[str, date], float] = {}
    for subject, day, close in zip(batch.subjects, dates, closes, strict=True):
        if type(close) is not float:
            raise PanelBatchError(
                f"{batch.dataset} {subject} on {day.isoformat()} has a "
                f"{'null' if close is None else type(close).__name__} close; the two price "
                "datasets cross-check each other on this column, so it cannot be missing"
            )
        if (subject, day) in index:
            raise PanelBatchError(
                f"{batch.dataset} carries {subject} twice on {day.isoformat()}; a session has "
                "one row per security, so this is one session's fetch merged into the year "
                "more than once. Stored, it would answer every later read of that session "
                "with 'appears twice' and could only be repaired by rewriting the year"
            )
        index[(subject, day)] = close
    return index


def _refuse_close_disagreement(bars: ColumnarPanelBatch, fundamentals: ColumnarPanelBatch) -> None:
    """Refuse a pair of price years whose `close` columns contradict each other.

    `daily_basic` republishes `close`, so the two fetches one session already needs cross-check
    each other with no extra request -- measured across five sessions from 2023-01-03 to
    2026-08-07, zero disagreements in 24,188 shared rows. Making it a **write** guard rather
    than a report is what stops a partition that contradicts its sibling from existing at all.

    Direction is asymmetric and measured; see `domain/daily_prices.py::close_disagreements`.
    """
    findings = close_disagreements(_close_index(bars), _close_index(fundamentals))
    if not findings:
        return
    first = findings[0]
    detail = (
        f"has a {DAILY_BASIC_DATASET} row and no {DAILY_DATASET} bar"
        if first.bar_close is None
        else f"closed at {first.bar_close!r} in {DAILY_DATASET} and "
        f"{first.valuation_close!r} in {DAILY_BASIC_DATASET}"
    )
    raise PanelBatchError(
        f"{len(findings)} row(s) disagree between {DAILY_DATASET} and {DAILY_BASIC_DATASET}; "
        f"{first.ts_code} on {first.trade_date.isoformat()} {detail}. The two endpoints publish "
        "the same close for the same session, so a difference is a partial or mismatched fetch "
        "rather than news, and storing it would leave two partitions that answer differently"
    )


def write_daily_panel(
    store: PanelStore,
    *,
    bars: Sequence[ColumnarPanelBatch],
    fundamentals: Sequence[ColumnarPanelBatch],
    calendar: TradingCalendar,
    halts: Mapping[date, SuspensionDay] | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> tuple[PartitionRef, PartitionRef]:
    """Write one year of `daily` and `daily_basic` cross sections as two partitions (`V2-P1-007`).

    ## Why one writer takes both datasets

    They are not independent. `daily_basic` republishes `close`, which makes the two fetches a
    session already needs into a cross-check of each other -- and a check that is optional is a
    check that gets skipped. Taking both here makes it a precondition of storage instead: there
    is no supported way to write one of these partitions without the other having agreed with
    it. The cost is that a caller cannot ingest `daily` alone, and that is the intended
    trade: the two datasets are fetched for the same session by the same loop anyway.

    ## Sequences, for `write_adjustment_factors`' reason

    A request is one trading day of the whole market (~5,400 rows against a measured 6,000-row
    cap) and a partition is a calendar year (~1.3e6 rows). So a year is ~244 fetches per
    dataset, `PanelStore.write_partition` replaces a partition whole and has no append, and
    writing the fetches one at a time would leave each year holding only its last session.

    ## Five guards, and every one of them runs before anything is written

    1. `panel_partition_year` refuses a set of batches that straddles two years rather than
       picking one, on each side independently, and the two sides must agree on the year.
    2. `_refuse_missing_price_sessions` refuses a year that is missing a session the calendar
       reports open -- on **both** datasets, so a whole `daily` year cannot be stored beside a
       holed `daily_basic` one.
    3. `_refuse_thin_price_sessions` refuses a year holding a session that arrived short, which
       is the failure guard 2 is blind to: a present-but-partial cross section passes every
       other check and then reports most of the market as unpriced on that day.
    4. `_refuse_close_disagreement` refuses two years that contradict each other -- and refuses
       a year that merged one session's fetch in twice, which would otherwise make the
       cross-check blind to itself and leave the session unreadable (see `_close_index`).
    5. `_refuse_to_drop_stored_subjects` refuses a rewrite that would remove a security the
       stored partition already had -- the same guard the registry and the factor series use.
       It runs on **both** datasets: a `daily_basic` rewrite that dropped a security would
       leave the two partitions disagreeing about which names the year covers, and guard 4
       cannot catch that direction because a bar with no valuation is the measured, tolerated
       shape of every pre-2024 year.
    6. `_refuse_unexplained_thin_sessions`, when `halts` is not `None`, refuses a session whose
       missing bars are not accounted for by that day's whole-day halts. This is guard 3 with
       the reason for its low threshold removed -- see below.

    ## `halts` is required and nullable, which makes skipping guard 6 a decision (`V2-P1-008`)

    Guard 3's floor sits at half the partition median because 2015-07-09 really did serve 1,363
    rows against a year median of 2,359. With `suspend_d` in hand that session is explained
    (1,363 bars + 1,438 whole-day halts, against a comparable median of 2,796), so guard 6 can
    sit at `MIN_EXPLAINED_SESSION_SHARE` and catch a fetch that lost a seventh of the market --
    which guard 3, by construction, cannot.

    The parameter has **no default**. `halts=None` still means "run guards 1-5 only", so the
    weaker behaviour is reachable and is exactly `V2-P1-007`'s -- but it has to be asked for.
    A default of `None` would have made the strongest guard in this function the one a caller
    skips by not knowing it exists, which is the failure mode `ColumnarPanelBatch.checks_waived`
    was introduced to remove elsewhere in this module: a waiver that is recorded is a decision,
    a waiver that is a default is an accident. The cost is one keyword at every call site and
    nothing else -- there are no production callers yet; `V2-P1-015`'s `panel build` is the
    first, and it fetches all three datasets for the same session in the same loop, so it will
    pass a real corpus rather than a `None`.

    ## What this writer's coupling costs `V2-P1-015`

    There is no supported way to write one of these partitions without the other. So a repair
    to either side is a re-fetch of ~244 sessions on **both**, and `V2-P1-015`'s `panel build`
    cannot offer a `--dataset daily` that means anything: the smallest honest unit of work here
    is the pair. That is the intended trade -- the two datasets are fetched for the same session
    by the same loop anyway -- but it is a constraint on that issue's CLI surface rather than
    an implementation detail of this one, so it is named here.

    Vetting everything first is `write_stock_universe`'s shape and is here for its reason: a
    guard that trips after the first partition has been replaced leaves a store whose two price
    datasets disagree about which sessions exist, which is precisely the state guard 3 exists to
    prevent. It is not full atomicity -- `PanelStore` has no multi-partition transaction, so an
    interruption or a full disk between the two writes can still leave one written and the other
    not. What is bought is that the failure modes this module can *predict* never fire mid-write,
    and the operator's remedy (re-run the year) always applies to an unchanged store. The
    residual half-written case is visible: the second dataset's partition is absent, which
    `assess_readiness` reports as `partition_missing` rather than trusting.
    """
    merged_bars = merge_panel_batches(bars)
    merged_fundamentals = merge_panel_batches(fundamentals)
    if merged_bars.dataset != DAILY_DATASET:
        raise PanelBatchError(
            f"expected the {DAILY_DATASET!r} dataset, got {merged_bars.dataset!r}"
        )
    if merged_fundamentals.dataset != DAILY_BASIC_DATASET:
        raise PanelBatchError(
            f"expected the {DAILY_BASIC_DATASET!r} dataset, got {merged_fundamentals.dataset!r}"
        )
    year = panel_partition_year(merged_bars, date_timezone=date_timezone)
    fundamentals_year = panel_partition_year(merged_fundamentals, date_timezone=date_timezone)
    if fundamentals_year != year:
        raise PanelBatchError(
            f"the {DAILY_DATASET} batches are year {year} and the {DAILY_BASIC_DATASET} ones "
            f"are year {fundamentals_year}; the two partitions of one set of sessions have to "
            "be written together or they will disagree about which sessions exist"
        )
    _refuse_missing_price_sessions(merged_bars, calendar, year, date_timezone=date_timezone)
    _refuse_missing_price_sessions(merged_fundamentals, calendar, year, date_timezone=date_timezone)
    _refuse_thin_price_sessions(merged_bars)
    _refuse_thin_price_sessions(merged_fundamentals)
    if halts is not None:
        _refuse_unexplained_thin_sessions(merged_bars, halts)
        _refuse_unexplained_thin_sessions(merged_fundamentals, halts)
    _refuse_close_disagreement(merged_bars, merged_fundamentals)
    remedy = (
        "A year's partition is replaced whole, so every session of the year has to arrive in "
        "one call; a narrower cross section is a partial fetch rather than news"
    )
    for merged in (merged_bars, merged_fundamentals):
        _refuse_to_drop_stored_subjects(store, merged, year, remedy=remedy)
    return (
        write_panel_batch(store, merged_bars, year=year, date_timezone=date_timezone),
        write_panel_batch(store, merged_fundamentals, year=year, date_timezone=date_timezone),
    )


def _sessions_published_through(as_of: datetime, zone: ZoneInfo) -> date:
    """The newest calendar day whose session had published at `as_of`.

    A session's data becomes knowable at `DAILY_AVAILABILITY_TIME` (16:30 Asia/Shanghai) --
    the same constant `providers/tushare.py::_daily_close_timeline` dates `available_time` at,
    imported rather than restated so the two cannot drift. Before that instant the current day
    has not published, so requiring it would report an invented `date_gap` on every intraday
    read; after it, omitting it would stop requiring the newest session.

    This is why `panel_readiness_requirement` is not reused for the price datasets: it clamps
    the required range at `as_of`'s calendar *date*, which is right for a dataset whose rows are
    dated at midnight and one session too generous for one whose rows publish in the afternoon.
    """
    local = as_of.astimezone(zone)
    if local.time() >= DAILY_AVAILABILITY_TIME:
        return local.date()
    return local.date() - timedelta(days=1)


def _price_requirement(
    dataset: str,
    fields: tuple[str, ...],
    calendar: TradingCalendar,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
    date_timezone: str,
    required_subjects: tuple[str, ...] | None = None,
) -> ReadinessRequirement:
    """The session-census requirement the three session-dated price datasets share.

    `required_subjects` defaults to `None` because the two market-wide datasets cannot name
    theirs -- the cross section is what the read is for. `index_daily` can and does: its
    partition holds three series told apart by the subject column alone, so naming the one a
    factor reads is what turns "this year holds 中证500 but not 沪深300" into a blocked read
    with a `subject_missing` code rather than an empty market series.
    """
    zone = _resolve_timezone(date_timezone)
    published_through = _sessions_published_through(as_of, zone)
    required: list[date] = []
    for year in sorted(set(years)):
        start = date(year, 1, 1)
        if start > published_through:
            raise TradingCalendarError(
                f"year {year} has not begun at as_of {as_of.isoformat()} ({date_timezone}), so "
                f"no {dataset} session in it can be required yet"
            )
        required.extend(
            calendar.trading_days_between(start, min(date(year, 12, 31), published_through))
        )
    return ReadinessRequirement(
        dataset=dataset,
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=tuple(required),
        required_subjects=required_subjects,
        required_fields=fields,
        max_staleness=max_staleness,
    )


def daily_requirement(
    calendar: TradingCalendar,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> ReadinessRequirement:
    """What the price panel must satisfy before bars may be read from it.

    **`required_dates` is not waived**, and that is the substantive difference from
    `adjustment_requirement`. A factor partition is compressed to its load-bearing sessions, so
    a calendar-derived expectation would report a permanent `date_gap` on a partition that is
    complete by construction; a price partition is stored uncompressed, so the expectation is
    exactly right and every read re-checks it -- including a read of a partition written before
    the write-time census existed, which is the residue `adjustment_requirement` has to name and
    this one does not.

    Requiring the calendar as an argument rather than defaulting it to `None` is deliberate:
    an optional date check is one that is off by default, and hole detection is the whole
    reason `V2-P1-004` was sequenced before this issue.

    **`required_subjects` is waived** because naming the securities would be circular -- the
    cross section is what the read is for. `priced_cross_section` is where a listed security
    with no bar becomes visible, because that is where the universe is known.

    **`max_staleness` is not waived by default and has no default value**: the caller states a
    bound or states `None` on the record. A price panel whose newest session is a month old has
    missed a month of the market, so leaving it to a default would be choosing silence.

    Raises `TradingCalendarError` for a year that has not begun at `as_of`, and (through
    `trading_days_between`) for any requested year the calendar does not cover -- a requirement
    built from a calendar that knows half the year would silently under-require the other half.
    """
    return _price_requirement(
        DAILY_DATASET,
        DAILY_PANEL_COLUMNS,
        calendar,
        years=years,
        as_of=as_of,
        max_staleness=max_staleness,
        date_timezone=date_timezone,
    )


def daily_basic_requirement(
    calendar: TradingCalendar,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> ReadinessRequirement:
    """What the valuation panel must satisfy before market caps may be read from it.

    The same shape and the same waivers as `daily_requirement`: the two datasets cover the same
    sessions by construction, and `write_daily_panel` refuses to store them otherwise.
    """
    return _price_requirement(
        DAILY_BASIC_DATASET,
        DAILY_BASIC_PANEL_COLUMNS,
        calendar,
        years=years,
        as_of=as_of,
        max_staleness=max_staleness,
        date_timezone=date_timezone,
    )


def _read_visible_price_session(
    store: PanelStore,
    requirement: ReadinessRequirement,
    columns: tuple[str, ...],
    *,
    day: date,
    calendar: TradingCalendar,
    as_of: datetime,
    date_timezone: str,
) -> tuple[tuple[object, ...], ...]:
    """One session's rows as they were knowable at `as_of`, or a refusal (`V2-P4-026`).

    **The only door onto a session of a price dataset, since `V2-P4-061`.** It answers the
    question a whole-partition read cannot: *"what did this session hold, read from inside its own
    year?"* All three session-dated price loaders take it -- `load_daily_bars`,
    `load_price_limits` and `load_daily_valuations` -- and `_read_price_session`, the
    `read_if_ready` twin the first two used to take, is gone rather than left beside it: a second
    door onto one question is how the two came to answer it at two scopes once already (see the
    `max_staleness` section below).

    ## The problem, in one number

    `read_if_ready` decides `not_yet_knowable` on a partition's **max** `available_time`, and a
    partition is a calendar year, so a complete price year is refused at every `as_of`
    inside it for the sake of its December rows. Everything built on top inherits that: a
    neutralised residual for any trading day of year Y could only be *built* at an `as_of` at or
    after Y's last session, and since `neutralized_observation_batch` stamps every clock with the
    build's `as_of`, every residual of year Y became visible at one instant. Roadmap section 11
    records the consequence -- annual walk-forward and nothing finer.

    **`V2-P4-061` is what that constraint cost at the surface, and it is why `daily` and
    `stk_limit` are here too.** `openalpha shortlist run` prices a stored cross section on the
    session that cross section is about, so a store holding two cross sections in one year could
    screen only the newest: the panel advancing a single session made every earlier one
    unscreenable, and two days' shortlists could not be compared, yesterday's could not be re-run
    and nothing could be audited after the fact. Three shipped sentences said the opposite --
    `shortlist_view.load_shortlist_cross_section`'s docstring, `docs/api/http.md` and `README.md`
    all promise a fortnight-old cross section the market of its own session.

    ## Why a row predicate is safe here, and how `index_member_all` had to answer it differently

    The objection `tests/unit/panel/test_visible_read_callers.py` makes every new caller of
    `read_visible_at` answer is *can this caller tell a withheld row from an absent one*. For a
    **session-scoped** read of a `daily_close`-clocked dataset the answer is yes, and it is a
    measured property of the dataset's shape rather than an argument about it:

    `providers/tushare.py::_daily_close_timeline` dates every price row's `available_time` at
    `DAILY_AVAILABILITY_TIME` on its own `trade_date`, so **every row of one session carries one
    and the same availability instant**. `daily`, `stk_limit` and `daily_basic` all declare
    `ClockStrategy.daily_close` and there is no second way in: `TushareProvider.fetch_panel` is
    the only `PanelDataProvider` in `src/`, `_CLOCK_BUILDERS` dispatches its descriptor's clock,
    `write_daily_panel` refuses a batch of any other dataset, and `_split_batch_by_year` and
    `merge_panel_batches` carry a batch's four clock columns across untouched. Re-measured for
    `V2-P4-061` on the generated fixture panel: 10 of 10 stored sessions in each of the three
    datasets carry exactly one distinct `available_time`, and it is 16:30 Asia/Shanghai on that
    session's own `trade_date`. The caller's filter is `trade_date = day`, and
    `_build_visible_census_sql` counts what the predicate removed *within that same filter*, so
    the answer to a session read is all-or-nothing: either every row of the session is visible
    and `withheld_row_count` is 0, or none is and `withheld_row_count` is the session's whole row
    count. Measured on the generated fixture panel at `as_of` 2026-01-12T04:00Z: 2026-01-09
    answers 7 rows with 0 withheld (the eighth security was halted and has no row at all -- an
    **absent** row, and it reads as one), while 2026-01-12, 2026-01-13 and 2026-01-16 each answer
    0 rows with 8 withheld. The two situations are two different pairs of numbers, and this
    function turns the second into a refusal rather than into an empty cross section.

    **This docstring's claim about the other dataset was too strong and `V2-P4-027` corrected
    it.** It read "for the industry corpus the answer is no, which is why
    `SecurityIndustryHistory.answerable_through` exists and why that half is deliberately left on
    the unfiltered door". The premise is right and the conclusion was wrong. `index_member_all`
    genuinely has no all-or-nothing shape and never will -- it is event-driven and a partial
    partition is what an honest mid-year read of it returns -- but that is not the only way to
    tell a withheld row from an absent one. `load_industry_cross_section` tells them apart from
    the partition's own **date census**, which counts rows per event date and therefore says
    exactly how many rows an `as_of` must see; and it declines to hand back a history at all, so
    the interval-with-no-end that `answerable_through` exists for cannot escape. What stayed true
    is that `load_industry_histories` itself does not take the filtered door.

    ## The three refusals, and why each one is a separate name

    - **A session whose own publication instant has not arrived is refused before any read.**
      `_sessions_published_through` is the same 16:30 the provider stamps and the same one
      `_price_requirement` clamps its date census at, imported rather than restated. Without this
      gate a `day` beyond the partition's coverage would answer `()` -- no rows, nothing withheld,
      no issue -- which is the fail-open shape this whole plane is built against.
    - **A session the store holds and `as_of` cannot see is refused by name**, not answered
      empty. This is the backstop under the paragraph above: it is unreachable through the gate
      for a partition whose rows carry the provider's own clock, and it fires on one whose rows
      say something else.
    - **A session whose rows do *not* share one availability instant is refused outright.** That
      is the all-or-nothing property failing, and the moment it fails a short answer becomes
      indistinguishable from a thin session -- exactly the objection this door is allowed past
      only because it does not apply. Checked rather than assumed, because the property lives in
      a provider one package away and nothing in the store enforces it.

    ## The caller's `max_staleness` is decided where it was declared, and that took two calls

    **The single-call version of this function was wrong about the freshness bound, measurably,
    and the two-step below is the correction.** `read_visible_at` re-decides `stale` over the rows
    it is about to return (`VISIBLE_SLICE_RECHECKS`); the rows here are **one session**, so
    `visible_last_event_time` is `day`'s own close and the bound silently became
    `as_of - (day at SESSION_CLOSE_TIME)`. That is not what `daily_requirement`'s record means --
    it means "this panel has not fallen behind the market" -- and reading it the other way turns
    an ordinary question into a false finding: `panel doctor`'s `daily` bound is the exchange's
    longest closure plus a day, so on the fixture gate panel it made session 2026-01-12 block at
    an `as_of` of 2026-01-17 with `stale`, while `load_daily_bars` on the identical store cleared
    it (`tests/integration/panel/test_panel_gate.py::
    test_naming_the_session_after_the_hole_still_blocks_and_the_window_is_two_sessions_wide`
    caught it). Two twins answering one argument at two scopes is the defect, not a tightening.

    So the bound is decided **once, at partition scope, through exactly the verdict
    `read_if_ready` would have returned**: `assess_readiness` is run on the caller's own
    requirement and anything outside `ROW_FILTERABLE_ISSUE_CODES` refuses the read. That is the
    same function, the same rule table and the same scope as the unfiltered door, so a caller
    that stated a bound is still answered against the bound it stated. The requirement handed to
    `read_visible_at` then waives `max_staleness`, which is what stops the recheck re-deciding it
    one scope smaller. Since `V2-P4-061` the three price loaders share this one door, so the two
    twins that produced that defect cannot disagree again by construction rather than by
    agreement; the gate test named above still drives the scope.

    **Waiving it there is not the fail-open `V2-P3-002`'s review closed, and the reason is
    specific to this dataset rather than general.** That review found a *factor* read accepting a
    declared bound and structurally ignoring it, because `stale` cannot fire at a mid-year `as_of`
    (the catalog's newest event post-dates the read, so the difference is negative) and nothing
    else looked at reach. Here something else does: `_price_requirement` states `required_dates`
    and clamps them at `_sessions_published_through`, so **every** session from 1 January through
    the newest one that had published at `as_of` is required, and a partition that has fallen
    behind the market fails `date_gap` -- at every `as_of`, mid-year included, and at partition
    scope where the question belongs. That is a sharper guard than a duration bound, not a weaker
    one, and it is the guard `daily_requirement`'s docstring already says is "the substantive
    difference from `adjustment_requirement`".
    `test_a_partition_that_has_fallen_behind_the_market_is_refused_at_a_mid_year_as_of` drives it.

    What is left un-guarded is the age of the answer itself -- `as_of - day` -- and that is a
    function of two arguments the caller supplied, needing no store to compute.
    """
    if not calendar.is_trading_day(day):
        raise PriceDataError(
            f"{day.isoformat()} is not an open session on the {calendar.exchange} calendar, so "
            f"there are no {requirement.dataset} rows to read for it"
        )
    published_through = _sessions_published_through(as_of, _resolve_timezone(date_timezone))
    if day > published_through:
        raise PanelStorageError(
            f"{requirement.dataset} cannot be read for {day.isoformat()} at "
            f"{as_of.isoformat()}: that session had not published yet, because a session becomes "
            f"knowable at {DAILY_AVAILABILITY_TIME.isoformat()} {date_timezone} and the newest "
            f"one that had is {published_through.isoformat()}. Reading it would be a look-ahead, "
            "and answering it with an empty cross section would be one dressed as thin data"
        )
    gate = store.assess_readiness(requirement)
    if {issue.code for issue in gate.issues} - ROW_FILTERABLE_ISSUE_CODES:
        raise PanelStorageError(
            f"{requirement.dataset} cannot be read at {as_of.isoformat()}: "
            f"{[issue.code for issue in gate.issues]}; "
            f"{'; '.join(issue.detail for issue in gate.issues)}"
        )
    outcome = store.read_visible_at(
        replace(requirement, max_staleness=None),
        year=day.year,
        columns=columns,
        filters={PRICE_DATE_COLUMN: day.isoformat()},
    )
    if outcome.is_blocked:
        raise PanelStorageError(
            f"{requirement.dataset} cannot be read at {as_of.isoformat()}: "
            f"{[issue.code for issue in outcome.blocking_issues]}; "
            f"{'; '.join(issue.detail for issue in outcome.blocking_issues)}"
        )
    rows = outcome.rows
    withheld = outcome.withheld_row_count
    if withheld and rows:
        raise PanelStorageError(
            f"{requirement.dataset} year={day.year} carries {len(rows)} row(s) for "
            f"{day.isoformat()} that were knowable at {as_of.isoformat()} and {withheld} that "
            "were not, so this session's rows do not share one availability instant. A "
            "session-level visible read is only sound because they do -- a partial session is "
            "indistinguishable from a thin one once it is handed back -- so it is refused rather "
            "than returned short"
        )
    if withheld:
        raise PanelStorageError(
            f"{requirement.dataset} year={day.year} holds {withheld} row(s) for "
            f"{day.isoformat()} and none of them was knowable at {as_of.isoformat()}; the stored "
            "availability instant of that session post-dates the read. An empty cross section "
            "would say the market was empty that day, which is a different fact"
        )
    return rows


def load_daily_bars(
    store: PanelStore,
    *,
    day: date,
    calendar: TradingCalendar,
    as_of: datetime,
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> dict[str, DailyBar]:
    """Read one session's bars back as `DailyBar`s, or refuse to.

    Fail-closed six times over. A partition that is missing, damaged, unprofiled, stale, or
    missing a session the calendar reports open is blocked at partition scope by
    `assess_readiness()` -- the same rule table `read_if_ready()` runs -- and reported by its
    structured issue codes. A day the exchange was shut is refused before any read, and so is a
    session whose own 16:30 has not arrived at `as_of`. A session the store holds and `as_of`
    cannot see is refused rather than answered empty, and one whose rows do not all share one
    availability instant is refused rather than returned short. And a partition that passes all of
    those and carries a malformed row -- a null close, two bars for one security, two sessions in
    one filter -- is refused afterwards by `daily_bars_from_panel_rows`.

    **`V2-P4-061` moved this read off `read_if_ready` and onto `_read_visible_price_session`.**
    The whole-partition door judges `not_yet_knowable` on the newest `available_time` anywhere in
    the year, so a panel that had advanced one session refused every earlier session of that year
    -- and `openalpha shortlist run`, which prices a stored cross section on the session that
    cross section is about, could therefore screen only the newest one. `V2-P4-026` had already
    built the session read and wired `load_daily_valuations` to it; what that issue declined to do
    was widen it here, because "nothing measured yet asks for it". Product acceptance asked.

    Widening it is admissible for the same reason, re-measured rather than inherited: `daily`
    declares `ClockStrategy.daily_close`, so every row of one session carries one
    `available_time` and a session read is either wholly visible or wholly withheld. See
    `_read_visible_price_session` for the measurement and for the three named refusals that stand
    where the partition refusal did.

    One session per call, because that is the unit every downstream question is asked in: a
    cross section joins against one day's factors and one day's registry membership. Readiness
    is assessed once per call, which matches `load_trading_calendar`'s shape; a caller walking a
    year re-evaluates the catalog 244 times, and on catalog metadata rather than Parquet that is
    milliseconds. Making it one assessment plus N reads is a change to `assess_readiness`'s
    contract, shared with every other dataset, and belongs with whichever task first has a load
    that hurts.
    """
    requirement = daily_requirement(
        calendar,
        years=(day.year,),
        as_of=as_of,
        max_staleness=max_staleness,
        date_timezone=date_timezone,
    )
    rows = _read_visible_price_session(
        store,
        requirement,
        DAILY_PANEL_COLUMNS,
        day=day,
        calendar=calendar,
        as_of=as_of,
        date_timezone=date_timezone,
    )
    return daily_bars_from_panel_rows(rows)


def load_daily_valuations(
    store: PanelStore,
    *,
    day: date,
    calendar: TradingCalendar,
    as_of: datetime,
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> dict[str, DailyValuation]:
    """Read one session's market caps, turnover and valuation ratios back, or refuse to.

    `load_daily_bars`' twin, with one honest asymmetry: the result may hold **fewer** securities
    than the bars do, and that is data rather than a fault. `daily_basic` omits Beijing-board
    names on historical sessions -- 60 of 3,843 on 2020-03-02, all `.BJ` -- so a caller joining
    the two must expect a `total_mv` to be absent for a security that traded. The reverse never
    happened on any session probed and is refused at write time.

    **`V2-P4-026` made this the first price loader onto `_read_visible_price_session`, and
    `V2-P4-061` made it no longer the only one.** That issue scoped the widening to what was
    measured to need it -- `panel_neutralization` reads exactly this dataset and
    `index_member_all`, and the residual's clocks were the whole of roadmap section 11's
    `V2-P3-004` finding -- and named the two callers a wider diff would have to re-argue:
    `factor_view` prices label windows off `load_daily_bars` and `panel_doctor`'s `_close_check`
    pairs the two on one session. Both are re-argued in `load_daily_bars`, so the three price
    loaders now take one door and this one carries no asymmetry of read at all.

    Where `read_if_ready` answered, this answers the identical rows: a partition with no issue
    has `max_available_time <= as_of`, so the predicate removes nothing. What changes is only the
    partitions that door refused whole.
    """
    requirement = daily_basic_requirement(
        calendar,
        years=(day.year,),
        as_of=as_of,
        max_staleness=max_staleness,
        date_timezone=date_timezone,
    )
    rows = _read_visible_price_session(
        store,
        requirement,
        DAILY_BASIC_PANEL_COLUMNS,
        day=day,
        calendar=calendar,
        as_of=as_of,
        date_timezone=date_timezone,
    )
    return daily_valuations_from_panel_rows(rows)


def _refuse_unrebuildable_suspensions(batch: ColumnarPanelBatch) -> None:
    """Refuse a `suspend_d` batch the reader's own reconstruction would refuse.

    Reads the batch in exactly the shape a stored partition is read in --
    `SUSPENSION_PANEL_COLUMNS`, subject first -- and hands it to
    `suspensions_from_panel_rows`. Same function, same order, so "the writer accepted it" and
    "the reader can return it" stop being two different questions.

    A `no_data` batch has no rows and no columns to project; there is nothing to rebuild and
    `suspensions_from_panel_rows` of nothing is an empty mapping, so it short-circuits rather
    than asking `_column_values` for a column a no-data batch is forbidden to carry.
    """
    if batch.status != "success":
        return
    columns = [_column_values(batch, name) for name in SUSPENSION_DATA_COLUMNS]
    suspensions_from_panel_rows(zip(batch.subjects, *columns, strict=True))


def write_suspensions(
    store: PanelStore,
    batches: Sequence[ColumnarPanelBatch],
    *,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> PartitionRef:
    """Write one year of `suspend_d` cross sections as a partition (`V2-P1-008`).

    A sequence for `write_adjustment_factors`' reason -- one request is one session and one
    partition is a year -- and it is the *cheap* one of this issue's two datasets: 28 rows on
    2024-06-28 against `daily`'s 5,338, and 1,466 on 2015-07-09, the worst session measured.

    ## No calendar census here, and that is not an oversight

    `_refuse_missing_price_sessions` refuses a price year that lacks a session the calendar
    reports open, because every open session has bars. This dataset is the opposite: a session
    on which nothing was halted and nothing resumed serves **zero** rows, so an absent session
    is the ordinary case and a census built from the calendar would refuse almost every year.
    That is also why `_refuse_unexplained_thin_sessions` treats an absent session as zero halts
    rather than as an error -- the two facts are indistinguishable in this dataset by
    construction, and the choice is made where the consequence is (a false refusal of a price
    year, which is loud) rather than here.

    The subject guard *is* kept, unlike `write_name_history`'s deliberate omission of it: a
    rename corpus for a year legitimately covers different securities on a re-fetch, but a
    security that was halted in 2015 does not stop having been halted, so losing one on a
    rewrite is a partial read rather than news.

    ## The partition is rebuilt into its domain type before it is stored

    `_refuse_unrebuildable_suspensions` runs `suspensions_from_panel_rows` -- the very function
    `load_suspensions` will run -- over the merged rows and refuses the write if it raises. That
    is not belt-and-braces over the guards above it. Until `V2-P1-013`'s follow-up this writer
    merged, checked the year and the subject set, and stored; nothing on the way in ever built
    a `SuspensionDay`, so a partition whose rows contradict that contract landed on disk, was
    registered with a row count, and was reported `READY` by `panel doctor` and `CLEARED` by
    `data-check` -- while every read of it raised. A store that accepts what it cannot return
    is worse than one that refuses at either end, because the failure then surfaces in whatever
    reads it next rather than in the command that caused it.

    The cost is one pass over a year of rows, and this is the cheap dataset: 2,293 rows for the
    2026 year to date against `daily`'s ~1.3 million.
    """
    merged = merge_panel_batches(batches)
    if merged.dataset != SUSPENSION_DATASET:
        raise PanelBatchError(
            f"expected the {SUSPENSION_DATASET!r} dataset, got {merged.dataset!r}"
        )
    _refuse_unrebuildable_suspensions(merged)
    year = panel_partition_year(merged, date_timezone=date_timezone)
    _refuse_to_drop_stored_subjects(
        store,
        merged,
        year,
        remedy=(
            "A year's partition is replaced whole, and a security that was halted stays halted; "
            "re-fetch every session of the year and write it in one call"
        ),
    )
    return write_panel_batch(store, merged, year=year, date_timezone=date_timezone)


def suspension_requirement(
    *, years: Sequence[int], as_of: datetime, max_staleness: timedelta | None
) -> ReadinessRequirement:
    """What the halt corpus must satisfy before trading states may be built from it.

    `required_dates` is waived for the reason `write_suspensions` states no census: a year has
    no list of days it is *supposed* to contain, because a session with no halts has no rows.
    That is the same shape `name_history_requirement` has and the opposite of
    `daily_requirement`'s, and it is the honest one here -- an expectation that every open
    session appears would report a permanent `date_gap` on a complete partition.

    `required_subjects` is waived because the securities are what the read is for.

    `max_staleness` has no default, for `daily_requirement`'s reason: a halt corpus whose newest
    partition is a year old will answer "nothing was halted" for every session since, which is
    the fail-open answer, and leaving that to a default would be choosing silence.
    """
    return ReadinessRequirement(
        dataset=SUSPENSION_DATASET,
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=SUSPENSION_PANEL_COLUMNS,
        max_staleness=max_staleness,
    )


def load_suspensions(
    store: PanelStore,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
) -> Mapping[date, SuspensionDay]:
    """Read the stored halt corpus back as one `SuspensionDay` per session, or refuse to.

    Whole years rather than one session, unlike `load_daily_bars`, because that is the unit its
    two consumers want: `_refuse_unexplained_thin_sessions` needs every session of a partition
    at once, and a caller walking a backtest wants the year in memory (a year of this dataset is
    thousands of rows, not the ~1.3e6 a price year is).

    A year that was never ingested blocks rather than being skipped, for
    `load_name_histories`' reason and more sharply: a skipped year here answers "nothing was
    halted" for every session in it, which is a plausible, silent, and completely wrong answer.

    ## As-of-sensitive since `V2-P4-076`, and this dataset's bound is the odd one

    It took `read_if_ready()`, which refuses a whole year for the sake of its newest row, so a
    halt on the newest session made every earlier cross section in that year unscreenable
    (measured on a real panel: `suspend_d` at 2026-08-19T16:30+08). It now takes
    `_read_visible_event_dated_rows`.

    The census bound is `_sessions_published_through` and **not** `as_of`'s own calendar day,
    which is the one thing this caller does differently from the other three. `suspend_d` is
    `ClockStrategy.daily_close`: a halt is knowable at `DAILY_AVAILABILITY_TIME` on its own
    `trade_date`, the same instant that session's bar is. Reconciling against the calendar day
    would count the current session's halts as due from midnight, find them withheld, and refuse
    every honest read taken before that session's close --
    `test_a_halt_on_the_current_session_is_not_required_before_that_session_closes` is that read.

    **Withheld and absent collapse in the values here and not in the numbers, which is why the
    census is load-bearing rather than decorative.** A security with no halt row and one whose
    halt row was withheld both read as "not halted" (`suspended_at_the_close` answers `False` for
    `None`, and 5,312 of 2024-06-28's 5,338 priced names have no row at all). So the separation
    is made before the rows are decoded: a session the census counted and the predicate emptied
    is refused by name, and a session the census never counted is answered, because nobody was
    halted. `HaltCorpus.require_coverage` is unchanged and still the guard that makes an absent
    row mean "nothing happened" rather than "nobody read the partition".
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise PanelBatchError(
            "load_suspensions needs at least one year; a read of no years would answer "
            "'nothing was ever halted', which is indistinguishable from a failed read"
        )
    requirement = suspension_requirement(years=requested, as_of=as_of, max_staleness=max_staleness)
    return suspensions_from_panel_rows(
        _read_visible_event_dated_rows(
            store,
            requirement,
            SUSPENSION_PANEL_COLUMNS,
            as_of=as_of,
            what=f"the {SUSPENSION_DATASET} corpus",
            availability_rule=(
                f"A halt's availability is {DAILY_AVAILABILITY_TIME.isoformat()} on its own "
                "trade_date, the same instant that session's bar becomes knowable"
            ),
            census_through=_sessions_published_through,
        )
    )


def write_price_limits(
    store: PanelStore,
    batches: Sequence[ColumnarPanelBatch],
    *,
    calendar: TradingCalendar,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> PartitionRef:
    """Write one year of `stk_limit` cross sections as a partition (`V2-P1-008`).

    The price panel's guards apply here almost unchanged, because this dataset has the price
    panel's shape: every open session publishes a band, the counts are stable within a year, and
    a partition is stored uncompressed.

    - `_refuse_missing_price_sessions`: a session with no bands is one on which every
      `limit_touch` question is unanswerable. **This is what refuses a pre-2007 year outright**,
      and correctly: `stk_limit` serves 0 rows for 2005-01-04 and 2006-01-04, so there is no
      2006 partition to be had and a year that silently held a handful of days would be worse
      than none.
    - `_refuse_thin_price_sessions`: the same floor `daily` uses. It is the weak one here for
      the same reason it is weak there, and it is not strengthened with `suspend_d` the way the
      price panel's is -- a halted security still gets a published band (all 26 of 2024-06-28's
      halts are in `stk_limit`), so a halt explains nothing about a missing band.
    - `_refuse_to_drop_stored_subjects`: a rewrite that loses a security is a partial read.

    Note what is **not** checked: that every bar has a band. It does not hold on history --
    60 bars had no published limit on 2020-03-02, all `.BJ` -- so the join is asked per security
    and a missing band is an absent key rather than a fault. See
    `domain/price_limits.py::KNOWN_SUSPENSION_LIMITATIONS`.
    """
    merged = merge_panel_batches(batches)
    if merged.dataset != PRICE_LIMIT_DATASET:
        raise PanelBatchError(
            f"expected the {PRICE_LIMIT_DATASET!r} dataset, got {merged.dataset!r}"
        )
    year = panel_partition_year(merged, date_timezone=date_timezone)
    _refuse_missing_price_sessions(merged, calendar, year, date_timezone=date_timezone)
    _refuse_thin_price_sessions(merged)
    _refuse_to_drop_stored_subjects(
        store,
        merged,
        year,
        remedy=(
            "A year's partition is replaced whole, so every session of the year has to arrive "
            "in one call; a narrower cross section is a partial fetch rather than news"
        ),
    )
    return write_panel_batch(store, merged, year=year, date_timezone=date_timezone)


def price_limit_requirement(
    calendar: TradingCalendar,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> ReadinessRequirement:
    """What the published-band panel must satisfy before limits may be read from it.

    `daily_requirement`'s shape and its waivers, because this dataset has `daily`'s shape: every
    open session publishes bands, the partition is stored uncompressed, and so the calendar-
    derived `required_dates` is exactly right rather than a permanent false gap.

    The one thing it inherits that is worth naming: a year before 2007 cannot satisfy this,
    because the endpoint has no rows at all before 2007-01-04. That is a horizon rather than a
    hole, and it surfaces here as `date_gap` rather than as a silent empty read.
    """
    return _price_requirement(
        PRICE_LIMIT_DATASET,
        PRICE_LIMIT_PANEL_COLUMNS,
        calendar,
        years=years,
        as_of=as_of,
        max_staleness=max_staleness,
        date_timezone=date_timezone,
    )


def load_price_limits(
    store: PanelStore,
    *,
    day: date,
    calendar: TradingCalendar,
    as_of: datetime,
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> dict[str, PriceLimit]:
    """Read one session's published bands back as `PriceLimit`s, or refuse to.

    `load_daily_bars`' twin -- one session per call, the same door, the same fail-closed layers --
    with one honest asymmetry in the other direction from `load_daily_valuations`': the result
    holds **more** securities than the bars do, and most of the surplus is not equity. `stk_limit`
    served 6,867 rows on 2024-06-28 against `daily`'s 5,338, and the 1,529 extra are 1,418
    funds, 85 B shares and the session's 26 halted stocks. A caller joining the two iterates the
    bars and looks bands up, never the reverse.

    **This one moved with the bars in `V2-P4-061`, and it had to.** The two are read together --
    `shortlist_view._bars_on` and `factor_view._PanelInputs.market_bar` both pair a bar with a
    published band before either reaches the execution policy, and a band the caller has no bar
    for is skipped -- so leaving this on the whole-partition door would have moved the shortlist's
    refusal one line down rather than removing it. Measured: with `load_daily_bars` alone on the
    session read, `openalpha shortlist run` at an earlier cross section still exited `1` with
    `stk_limit cannot be read at ...: ['not_yet_knowable']`.

    Admissible on the same measured shape and not by association: `stk_limit`'s descriptor
    declares `ClockStrategy.daily_close`, the same builder `daily` and `daily_basic` use, so every
    row of one session carries one `available_time`. Re-measured on the generated fixture panel,
    10 of 10 stored sessions carry exactly one, at 16:30 Asia/Shanghai on their own `trade_date`.
    """
    requirement = price_limit_requirement(
        calendar,
        years=(day.year,),
        as_of=as_of,
        max_staleness=max_staleness,
        date_timezone=date_timezone,
    )
    rows = _read_visible_price_session(
        store,
        requirement,
        PRICE_LIMIT_PANEL_COLUMNS,
        day=day,
        calendar=calendar,
        as_of=as_of,
        date_timezone=date_timezone,
    )
    return price_limits_from_panel_rows(rows)


def write_index_weights(
    store: PanelStore,
    batches: Sequence[ColumnarPanelBatch],
    *,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> PartitionRef:
    """Write one year of index constituent weights as a partition (`V2-P1-009`).

    A sequence of batches for `write_adjustment_factors`' reason and one step further out: a
    request here is one *index* for one *calendar month*, so a full year of the three indices
    `V2-P1-009` names is 36 fetches feeding one partition, and `PanelStore.write_partition`
    replaces a partition whole rather than appending to it.

    ## The subject guard is this dataset's `exchange` problem, and it is the same one

    The partition key is `(dataset, year)` with no index dimension. A partition can therefore
    hold 沪深300, 中证500 and 中证1000 together -- the `subject` column separates them and
    `load_index_membership` filters on it -- but only if **one write carries all three**. A
    plain `for index_code in INDEX_WEIGHT_INDEX_CODES: write_index_weights(...)` backfill would
    leave the year holding whichever index it wrote last, silently and with a success return.
    That is exactly what `V2-P1-004` hit with SSE and SZSE, and it gets the same answer:
    `_refuse_to_drop_stored_subjects` blocks a batch whose indices do not cover the ones the
    stored partition already has.

    The guard works because the **index** is what the subject column holds. Reading the
    constituent out of it instead would trip this check too, today, and only by coincidence --
    these three indices are disjoint by construction, so a second index's constituents happen to
    be a disjoint set. It would *not* start misfiring on ordinary rebalances, which is worth
    stating because it is the obvious objection and it is wrong:
    `_refuse_to_drop_stored_subjects` compares a whole partition's subjects against a whole
    batch's, and a year's batch carries every name that year published, so a June review that
    replaces 50 names in 中证500 drops nothing from the union and the guard stays quiet either
    way. What the constituent subject would lose is the case the guard is here for --
    `000906.SH` 中证800 is `000300.SH` plus `000905.SH`, so a batch for it covers both of their
    constituent sets and would replace their partition with nothing appearing to go missing.

    ## What this guard does not catch: a per-*month* loop

    It compares subjects, so it sees a lost index and is blind to a lost month. A
    `for month in months: write_index_weights(store, batches_for(month))` backfill carries all
    three indices every time, drops no subject, and replaces the year's partition with one
    month -- silently, with a success return, exactly as the per-index loop does for an index.
    Nothing here refuses it. What refuses it is the read: `build_index_membership`'s month rule
    sees the hole if two or more months survive, and a partition narrowed to a single month has
    `covered_from == covered_through`, so every day but that one is refused by name. The
    outcome is a blocked read rather than a wrong answer, which is the same fail-closed shape
    the waived `required_dates` relies on -- but the write is still destructive, and a caller
    assembling a year has to hand `write_index_weights` the whole year in one call.

    ## No month census here, because the read has a better one

    `write_adjustment_factors` checks its year against a calendar because compression is about
    to destroy the evidence. Nothing is compressed here: publications *are* the stored rows, so
    the month census survives into the catalog and into every read.
    `domain/index_membership.py::build_index_membership` refuses a gap in the month sequence
    and a doubled month on **every** load, including of a partition this process never wrote,
    and a partition that is short at either end narrows `covered_from`/`covered_through` so the
    days it cannot answer are refused by name. Adding a write-time copy of a check that already
    runs on every read would buy nothing and would need a calendar this function does not take.
    """
    merged = merge_panel_batches(batches)
    if merged.dataset != INDEX_WEIGHT_DATASET:
        raise PanelBatchError(
            f"expected the {INDEX_WEIGHT_DATASET!r} dataset, got {merged.dataset!r}"
        )
    year = panel_partition_year(merged, date_timezone=date_timezone)
    _refuse_to_drop_stored_subjects(
        store,
        merged,
        year,
        remedy=(
            "A year's partition is replaced whole and its key has no index dimension, so every "
            "index that year holds has to arrive in one call or not at all"
        ),
    )
    return write_panel_batch(store, merged, year=year, date_timezone=date_timezone)


def index_weight_requirement(
    *, index_code: str, years: Sequence[int], as_of: datetime, max_staleness: timedelta | None
) -> ReadinessRequirement:
    """What the index-weight panel must satisfy before a membership may be read from it.

    `required_dates` is **waived**, and the waiver is paid for rather than written off.

    Stating it would mean naming the last open session of each month, which this function would
    have to derive from a `TradingCalendar` it does not take -- and pointing
    `panel_readiness_requirement` at this dataset would be actively wrong, because it requires
    *every* open session and this one publishes on twelve of them a year. The substitute is
    `build_index_membership`'s month rule, and it is not weaker in the way a waiver usually is:

    - It runs on **every read**, including of a partition written before it existed, which is
      the residue a write-time census cannot close.
    - It sees a hole that straddles two partitions, because a load concatenates the years first.
    - It needs no calendar, so it cannot inherit a calendar's own horizon.
    - A partition short at either end is caught by the horizon instead: `covered_from` and
      `covered_through` move with the rows actually read, and every day outside them raises
      `IndexMembershipHorizonError` rather than being answered from the nearest publication.

    `required_subjects` is **not** waived: it is the index, and it is the one thing a caller
    always knows. That is what turns "this year holds 沪深300 but not 中证1000" into a blocked
    read with a `subject_missing` code rather than an empty membership.

    `max_staleness` has no default, for `stock_universe_requirement`'s reason: this dataset
    publishes monthly, so a bound under about 35 days will refuse a completely healthy panel for
    most of every month, and one over a year hides a panel that stopped updating. There is no
    value this function could choose that would not be choosing for the caller.
    """
    return ReadinessRequirement(
        dataset=INDEX_WEIGHT_DATASET,
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=(index_code,),
        required_fields=INDEX_WEIGHT_PANEL_COLUMNS,
        max_staleness=max_staleness,
    )


def load_index_membership(
    store: PanelStore,
    *,
    index_code: str,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
) -> IndexMembership:
    """Read one index's stored publications back as an `IndexMembership`, or refuse to.

    Fail-closed three times over, which is `load_trading_calendar`'s shape with one more layer.
    A year whose partition is missing, damaged, unprofiled, stale or described by an out-of-date
    coverage record is blocked by `read_if_ready()` and reported by its structured issue codes.
    A partition that holds the year but not this index is blocked by the same call, on
    `subject_missing`, because the requirement names the index. And a set of rows with a month
    missing in the middle -- or a publication whose weights do not add up, which is what a
    response truncated mid-publication looks like -- is refused afterwards by
    `index_memberships_from_panel_rows`.

    One index per call rather than all three at once. A membership is per index by construction
    (`build_index_membership` refuses a foreign publication), and a caller wanting the set
    composes the calls -- which also keeps the blocked-year error naming the index it is about.
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise IndexMembershipError(
            f"load_index_membership needs at least one year; a read of no years would produce a "
            f"membership for {index_code} that refuses every question, which is "
            "indistinguishable from a failed read"
        )
    requirement = index_weight_requirement(
        index_code=index_code, years=requested, as_of=as_of, max_staleness=max_staleness
    )
    rows: list[tuple[object, ...]] = []
    for year in requested:
        outcome = store.read_if_ready(
            requirement,
            year=year,
            columns=INDEX_WEIGHT_PANEL_COLUMNS,
            filters={SUBJECT_COLUMN_NAME: index_code},
        )
        if outcome.is_blocked:
            raise PanelStorageError(
                f"{index_code}'s composition cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.readiness.issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.readiness.issues)}"
            )
        rows.extend(outcome.rows)
    memberships = index_memberships_from_panel_rows(rows)
    membership = memberships.get(index_code)
    if membership is None:
        raise IndexMembershipError(
            f"the {sorted(requested)} partitions hold no {index_code} publication; a subject "
            "filter that matched nothing is a read that found nothing, not an index with no "
            "constituents"
        )
    return membership


def _refuse_unrebuildable_index_prices(batch: ColumnarPanelBatch) -> None:
    """Refuse an `index_daily` batch the reader's own reconstruction would refuse.

    `_refuse_unrebuildable_suspensions`' shape and its argument, on a dataset where the residue
    is larger rather than smaller. A `suspend_d` partition that cannot be rebuilt fails loudly
    at the next read; an `index_daily` partition that cannot be rebuilt is the **regressor** of
    every residual volatility in the cross section, and `compute_factor` does not rebuild it --
    it reads two columns straight out of the partition. So a duplicated session, which
    `index_bars_from_panel_rows` refuses and nothing downstream of the store would notice, would
    put two market returns where the market had one and change the sample size of every
    regression reading that window.

    A `no_data` batch has nothing to rebuild and short-circuits, for the reason the suspension
    guard does: a no-data batch is forbidden to carry columns, so asking `_column_values` for
    one would fail on the shape rather than on the data.
    """
    if batch.status != "success":
        return
    columns = [_column_values(batch, name) for name in INDEX_DAILY_DATA_COLUMNS]
    index_bars_from_panel_rows(zip(batch.subjects, *columns, strict=True))


def write_index_prices(
    store: PanelStore,
    batches: Sequence[ColumnarPanelBatch],
    *,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> PartitionRef:
    """Write one year of index levels for every fetched index as a partition (`V2-P3-016`).

    A sequence because one request is one `(index, year)` window and one partition is a year, so
    the three series of `INDEX_PRICE_INDEX_CODES` arrive as three batches that have to be merged
    before either of the guards below can see the whole year.

    ## The subject guard, and why it is the same one `index_weight` needs

    `PanelStore`'s key is `(dataset, year)` with no index dimension and `write_partition`
    replaces a partition whole, so a `for code in codes: write_index_prices(store, [batch])`
    backfill would leave the year holding whichever index went last.
    `_refuse_to_drop_stored_subjects` is what refuses that, and it works here for exactly the
    reason it works one dataset over: the subject column is the index, so a lost series is a
    lost subject.

    ## No calendar census, and this is the one dataset where that is a *narrowing*

    `write_daily_panel` refuses a price year missing a session the calendar reports open. This
    writer takes no calendar and makes no such check, and the reason is that the three series do
    not begin together: `000300.SH` is published from 2002-01-04 while `000905.SH` and
    `000852.SH` both begin at their common 2004-12-31 base point. A census over the union would
    refuse every year from 2002 to 2004 for two indices that legitimately have no rows in it,
    and a census per subject is a per-series calendar this function has no argument for.

    What closes it instead is the **read**: `index_price_requirement` states `required_dates`
    from the calendar and `required_subjects` as `MARKET_INDEX_CODE`, so a year missing a
    session of the one series a factor reads is blocked at `read_visible_at` with a `date_gap`,
    on every read, including of a partition written before this writer existed. That is the
    fail-closed shape `index_weight_requirement` relies on, reached from the opposite direction:
    there the census could not be *stated*, here it could not be stated *per subject*.
    """
    merged = merge_panel_batches(batches)
    if merged.dataset != INDEX_DAILY_DATASET:
        raise PanelBatchError(
            f"expected the {INDEX_DAILY_DATASET!r} dataset, got {merged.dataset!r}"
        )
    _refuse_unrebuildable_index_prices(merged)
    year = panel_partition_year(merged, date_timezone=date_timezone)
    _refuse_to_drop_stored_subjects(
        store,
        merged,
        year,
        remedy=(
            "A year's partition is replaced whole and its key has no index dimension, so every "
            "index that year holds has to arrive in one call or not at all"
        ),
    )
    return write_panel_batch(store, merged, year=year, date_timezone=date_timezone)


def index_price_requirement(
    calendar: TradingCalendar,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> ReadinessRequirement:
    """What the index level panel must satisfy before a market return may be read from it.

    `daily_requirement`'s shape with one field added, and the field is the point.

    **`required_dates` is not waived**, for `daily_requirement`'s reason exactly: the index is
    quoted on every open session, the partition is stored uncompressed, and a factor window that
    silently skipped a session the market was open would pair a security's return on day `t`
    with the market's on day `t-1` for the whole rest of the window.

    **`required_subjects` is `(MARKET_INDEX_CODE,)`, which no other price requirement can
    state.** `daily` and `daily_basic` waive it because naming the securities would be circular.
    Here there are exactly three subjects, only one of them is reachable from an evaluator, and
    a partition that holds 中证500 and 中证1000 but not 沪深300 is a complete-looking year with
    every date present and no market series in it -- `date_gap` cannot see that and
    `subject_missing` can.

    **`max_staleness` has no default**, for `daily_requirement`'s reason: a market series whose
    newest session is a month old will still answer a 60-session window, with a beta estimated
    against a month-old market.
    """
    return _price_requirement(
        INDEX_DAILY_DATASET,
        INDEX_DAILY_PANEL_COLUMNS,
        calendar,
        years=years,
        as_of=as_of,
        max_staleness=max_staleness,
        date_timezone=date_timezone,
        required_subjects=(MARKET_INDEX_CODE,),
    )


def load_index_prices(
    store: PanelStore,
    calendar: TradingCalendar,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> Mapping[str, tuple[IndexBar, ...]]:
    """Read the stored index levels back as one ascending bar series per index, or refuse to.

    Whole years rather than one session, unlike `load_daily_bars` and for the reason a level
    series exists at all: nobody wants the market on a day, they want the market *over a
    window*. A single-session reader would make every caller reassemble the series itself and
    would put the ordering rule -- which `index_bars_from_panel_rows` owns -- in as many places
    as there are callers.

    Every index the partitions hold, not only `MARKET_INDEX_CODE`. The requirement names the one
    the factor engine can reach so that a partition without it is blocked, and the read then
    returns what is there: a caller comparing 沪深300 against 中证1000 is doing something this
    build's factors cannot, and refusing to *read* the other two would be a restriction with no
    reason behind it.
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise PanelBatchError(
            "load_index_prices needs at least one year; a read of no years would answer 'the "
            "market has no levels', which is indistinguishable from a failed read"
        )
    requirement = index_price_requirement(
        calendar,
        years=requested,
        as_of=as_of,
        max_staleness=max_staleness,
        date_timezone=date_timezone,
    )
    rows: list[tuple[object, ...]] = []
    for year in requested:
        outcome = store.read_if_ready(requirement, year=year, columns=INDEX_DAILY_PANEL_COLUMNS)
        if outcome.is_blocked:
            raise PanelStorageError(
                f"the {INDEX_DAILY_DATASET} panel cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.readiness.issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.readiness.issues)}"
            )
        rows.extend(outcome.rows)
    return index_bars_from_panel_rows(rows)


def write_industry_memberships(
    store: PanelStore,
    batches: Sequence[ColumnarPanelBatch],
    *,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> tuple[PartitionRef, ...]:
    """Write industry assignments into one partition per membership-event year (`V2-P1-010`).

    The event, not the assignment: `providers/tushare.py` splits a closed assignment into an
    opening row dated at `in_date` and a closing row dated at `out_date`, so a 1993 assignment
    that ended in 2017 puts one row in each of those two partitions. That is what lets an `as_of`
    inside the SW2021 era read the 1993 partition at all -- see
    `_industry_membership_panel_rows` -- and it is why a year here means "the events of that
    year" rather than "the assignments that began in it".

    This writer has both of the shapes the earlier ones have separately, because this dataset
    needs both.

    **A sequence of batches**, like `write_index_weights`: one request is one `l1_code` slice of
    one membership state, so the whole corpus is 62 fetches (31 L1 codes x `is_new` in `Y`/`N`)
    and `PanelStore.write_partition` replaces a partition whole.

    **One partition per year**, like `write_stock_universe`: `index_member_all` has no date
    filter at all, so a single fetch's assignments start anywhere from 1984 to last month and
    `panel_partition_year` refuses it. Filing the lot under the fetch year is not available --
    `record_coverage` refuses a census that reaches outside its partition's year -- so the merged
    batch is split by `split_panel_batch_by_year`, and the years then mean something: the 2021
    partition is the assignments that began in 2021 and the ones that ended there.

    ## The subject guard, and the two backfill loops it does and does not catch

    The subject is the **security**, so `_refuse_to_drop_stored_subjects` blocks a batch that
    would replace a year's partition with one holding fewer securities. That is exactly what a
    per-`l1_code` loop produces: `for code in codes: write_industry_memberships(store, [fetch(
    code)])` writes 1991 with the banks' securities, then replaces it with the utilities', and
    returns success both times.

    What it cannot see is a loop over the **membership state**: fetching every `l1_code` with
    `is_new='Y'` and writing that carries the full security set for the current assignments, and
    a later `is_new='N'` pass carries a *different* set (1,645 securities rather than 5,889), so
    the second write is refused -- but the first one, on its own, is a complete-looking corpus
    with every security in it and no history at all. Nothing here can distinguish that from a
    market in which nobody has ever been reclassified. What catches it is the request contract
    instead: `providers/tushare.py` refuses a membership request that does not name its state,
    so the current-only fetch has to be written out deliberately rather than fallen into.
    """
    merged = merge_panel_batches(batches)
    if merged.dataset != INDUSTRY_MEMBERSHIP_DATASET:
        raise IndustryClassificationError(
            f"expected the {INDUSTRY_MEMBERSHIP_DATASET!r} dataset, got {merged.dataset!r}"
        )
    by_year = split_panel_batch_by_year(merged, date_timezone=date_timezone)
    for year, yearly in by_year:
        _refuse_to_drop_stored_subjects(
            store,
            yearly,
            year,
            remedy=(
                "A year's partition is replaced whole and its key has no l1_code dimension, so "
                "every slice that year touches has to arrive in one call or not at all"
            ),
        )
    return tuple(
        write_panel_batch(store, yearly, year=year, date_timezone=date_timezone)
        for year, yearly in by_year
    )


def write_industry_tree(
    store: PanelStore,
    batch: ColumnarPanelBatch,
    *,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> PartitionRef:
    """Write one taxonomy vintage's whole tree into the panel plane (`V2-P1-010`).

    One request is one partition, and the partition year is the vintage's own effective year --
    2014 for SW2014 and 2021 for SW2021 -- because `providers/tushare.py` dates every node of a
    vintage at that day. So the two vintages never contend for a partition and no subject guard
    is needed: a year holds exactly one tree, and re-fetching it replaces that tree with itself.

    That is also why this writer takes one batch where `write_industry_memberships` takes many.
    Two vintages written in one call would straddle two years and be refused by
    `panel_partition_year`, correctly: they are two classifications, not two halves of one.
    """
    if batch.dataset != INDUSTRY_TREE_DATASET:
        raise IndustryClassificationError(
            f"expected the {INDUSTRY_TREE_DATASET!r} dataset, got {batch.dataset!r}"
        )
    year = panel_partition_year(batch, date_timezone=date_timezone)
    return write_panel_batch(store, batch, year=year, date_timezone=date_timezone)


def industry_membership_requirement(
    *, years: Sequence[int], as_of: datetime, max_staleness: timedelta | None
) -> ReadinessRequirement:
    """What the industry panel must satisfy before assignments may be read from it.

    `required_dates` and `required_subjects` are waived for the reason
    `stock_universe_requirement` waives them: a reclassification has no schedule, so a list of
    days a year is *supposed* to contain would be a guess -- 2016 carries 4 of the corpus's 2,004
    transitions and 2021 carries 587 -- and the securities are what the read is for.

    What is **not** waived, and what does the work this dataset needs, is the availability check
    already inside `evaluate_readiness`: every stored row's `available_time` is at or after the
    taxonomy's effective date, so a requirement whose `as_of` predates 2021-12-13 blocks with
    `not_yet_knowable` rather than answering a 2015 question in a 2021 classification. That is
    not a rule stated here; it is what the honest clock makes the generic rule say.

    `max_staleness` has no default, for `stock_universe_requirement`'s reason: assignments change
    in bursts around the annual review, so any bound this function chose would be choosing for
    the caller.
    """
    return ReadinessRequirement(
        dataset=INDUSTRY_MEMBERSHIP_DATASET,
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=INDUSTRY_MEMBERSHIP_PANEL_COLUMNS,
        max_staleness=max_staleness,
    )


def industry_tree_requirement(
    *, years: Sequence[int], as_of: datetime, max_staleness: timedelta | None
) -> ReadinessRequirement:
    """What the industry tree must satisfy before it may be read back.

    `required_dates` is waived because a vintage's whole tree is dated at one day, so the only
    honest date set would be that one day -- which `years` already names. `required_subjects` is
    waived because the nodes are what the read is for.
    """
    return ReadinessRequirement(
        dataset=INDUSTRY_TREE_DATASET,
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=INDUSTRY_TREE_PANEL_COLUMNS,
        max_staleness=max_staleness,
    )


def load_industry_histories(
    store: PanelStore,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
) -> Mapping[str, SecurityIndustryHistory]:
    """Read stored assignment years back as one history per security, or refuse to.

    Fail-closed three times over, which is `load_name_histories`' shape with one more layer. A
    year whose partition is missing, damaged, unprofiled or stale is blocked by `read_if_ready()`
    and reported by its structured issue codes. A read whose `as_of` predates the taxonomy is
    blocked by the same call, on `not_yet_knowable`, because the honest clock put every row's
    availability at or after 2021-12-13. And a set of rows that overlaps, or gives a security two
    open assignments, is refused afterwards by `industry_histories_from_panel_rows`.

    A year that was never ingested cannot be seen from here -- it is indistinguishable from a
    year in which nobody was reclassified, and 2016 really does carry only four transitions. What
    catches it is the query side: `SecurityIndustryHistory.assignment_on` refuses a day before
    its own first assignment and a day inside a gap, rather than carrying a neighbouring label
    across it. Naming the year in `years` is how a caller asserts it should exist.

    The years asked for are compared against the years the store actually holds, and the first
    stored year this read skipped becomes the histories' `answerable_through` bound. That is not
    bookkeeping: an assignment's close is stored as its own row in its own year, so a read that
    stops short of it reassembles an interval that never ends, and `assignment_on` would answer
    a later day with an industry the security had already left. A read covering every stored year
    gets no bound. See
    `KNOWN_INDUSTRY_LIMITATIONS.a_partial_year_read_cannot_see_an_interval_close`.

    **This door stays on `read_if_ready` and `V2-P4-027` did not move it.** A membership year is
    unreadable here until its last adjustment takes effect, which on the real corpus is the annual
    review, so a mid-year `as_of` inside a year that holds one is refused whole. That is not a
    defect to be filtered away at this signature: the value this returns is a *history*, and its
    only bound is `answerable_through`, a **year** -- so a mid-year read could either be bounded
    at the year before (refusing the day the caller asked about) or at the year itself (permitting
    a December question a June `as_of` cannot answer). Neither is the honest bound, which is
    `as_of`'s own day. `load_industry_cross_section` is the door that takes that day as an
    argument and can therefore be as-of-sensitive without an interval escaping unbounded.
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise IndustryClassificationError(
            "load_industry_histories needs at least one assignment year; a read of no years "
            "would produce a corpus that refuses every question, which is indistinguishable "
            "from a failed read"
        )
    requirement = industry_membership_requirement(
        years=requested, as_of=as_of, max_staleness=max_staleness
    )
    rows: list[tuple[object, ...]] = []
    for year in requested:
        outcome = store.read_if_ready(
            requirement, year=year, columns=INDUSTRY_MEMBERSHIP_PANEL_COLUMNS
        )
        if outcome.is_blocked:
            raise PanelStorageError(
                f"the industry classification cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.readiness.issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.readiness.issues)}"
            )
        rows.extend(outcome.rows)
    skipped = sorted(set(store.registered_years(INDUSTRY_MEMBERSHIP_DATASET)) - set(requested))
    return industry_histories_from_panel_rows(
        rows,
        taxonomy=INDUSTRY_MEMBERSHIP_TAXONOMY,
        answerable_through=(skipped[0] - 1) if skipped else None,
    )


def load_industry_cross_section(
    store: PanelStore,
    *,
    day: date,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> Mapping[str, IndustryAnswer]:
    """Every security's industry on `day` as it was knowable at `as_of`, or a refusal
    (`V2-P4-027`).

    `load_industry_histories`' as-of-sensitive twin, and the whole of `V2-P4-027`. It answers the
    question that door cannot: *"which industry was each security in on this day, read from
    inside a membership year that has not finished happening?"*

    ## The problem, in two numbers

    `read_if_ready` decides `not_yet_knowable` on a partition's **max** `available_time`, and a
    membership partition is the events of one calendar year, so a year is unreadable until its
    last adjustment takes effect. On the real corpus that last adjustment is the annual
    constituent review -- 613 rows effective 2021-07-30 and 255 effective 2022-07-29 -- so a
    walk-forward that fetches today and replays history is refused once a year, every year.
    `V2-P4-026` removed the same bottleneck from `daily_basic`; after it this dataset was the only
    input still refusing a mid-year `as_of` outright.

    ## Why this is not `V2-P4-026`'s solution with the dataset name changed

    `daily_basic`'s safety is an **all-or-nothing** shape: every row of one session carries one
    `available_time`, so a session read is either wholly visible or wholly withheld, and the two
    are two different pairs of numbers. `index_member_all` has no such shape and cannot be given
    one -- it is event-driven, its rows carry as many availability instants as there are event
    days, and a partial partition is exactly what an honest mid-year read *should* return. The
    objection `tests/unit/panel/test_visible_read_callers.py` makes every caller answer -- can
    this one tell a withheld row from an absent one? -- therefore has to be answered a different
    way, and it is answered twice over:

    **First, by the partition's own date census.** `panel_coverage` records how many rows carry
    each event date (`DateCoverage`), and `providers/tushare.py::_taxonomy_backfill_timeline`
    dates a row's availability at its own event, floored at the taxonomy's effective date. So
    once the floor is behind `as_of`, "visible" and "event date at or before `as_of`" are the
    same set, and the census says **exactly** how many rows carry each of those days. This read
    counts the visible rows by their own event date and refuses on any disagreement with the
    census, one date at a time. A withheld row is one the census counted and the predicate
    removed; an absent row is one the census never counted. That is a stronger answer than
    `V2-P4-026`'s, because it is an equality rather than a partition of the row set into two
    allowed shapes: a partition whose clocks are not the provider's is refused however it is
    wrong.

    **Per date, not per year, and `V2-P4-034` is why.** `V2-P4-027` wrote this as one comparison
    of two whole-year totals, which two errors in opposite directions cancel in exactly -- a
    withheld row traded against a look-ahead row leaves both totals equal and the read admitted.
    See `_read_visible_membership_rows` for the probe that measures it and for what the admitted
    cross section then contained.

    **Second, by refusing to hand back a history at all.** The fail-open this dataset invites is
    not a short row set, it is an *interval with no end in it*: an assignment whose closing row is
    withheld reassembles as one that never closed, and answering a later day from it names an
    industry the security had already left. `SecurityIndustryHistory.answerable_through` closes
    that at year granularity, which is exactly the granularity a mid-year `as_of` needs and does
    not have -- the honest bound at `as_of` 2024-06-30 is *2024-06-30*, and `answerable_through`
    can only say 2023 (refusing the day the caller asked about) or 2024 (permitting 2024-12-31,
    which is the fail-open). So this door does not return histories. It takes the day as an
    argument, resolves it inside, and returns the cross section -- and the day it will not
    resolve is refused rather than answered.

    ## The six refusals, and why each one is a separate name

    - **A `day` later than the newest event `as_of` could see is refused before any read.** A
      membership event becomes knowable at midnight (`date_timezone`) on the day it takes effect,
      so `as_of`'s own day in that zone is the last day this read can speak for. Beyond it, an
      open interval and a withheld close are the same rows, which is the one situation this whole
      issue exists to keep out of an answer.
    - **An `as_of` before the taxonomy's effective date is refused before any read.** Every
      membership row's availability is floored there (`INDUSTRY_TAXONOMY_EFFECTIVE_FROM`), so the
      predicate would withhold the entire corpus and hand back an empty mapping -- a market with
      no industries in it, which is a different fact from a classification that did not exist yet.
      This is the outer bound `V2-P4-027` explicitly does **not** move, and it is what makes
      2021-12-13 the earliest `as_of` anything here can serve.
    - **A stored membership year at or before `day`'s year that this read did not name is
      refused.** `answerable_through`'s rule, asked about a day instead of about a year: an
      assignment's close is filed in its own year, so an unread year at or before `day` can hold
      the close that ends an interval this cross section is about to report as current.
    - **A partition holding fewer visible rows on an event date than its own census counts there
      is refused.** The equality above, checked rather than assumed, because the clock it rests on
      lives in a provider one package away and nothing in the store enforces it.
    - **A partition answering with a visible row whose event date `as_of` cannot see is refused**,
      separately and first. That row's availability precedes its own event, which
      `_taxonomy_backfill_timeline` cannot produce at all, and it is a look-ahead rather than a
      shortfall. `V2-P4-034` split it out of the bullet above, where the two were one number that
      could be traded against each other.
    - **Anything the readiness rule table finds outside `ROW_FILTERABLE_ISSUE_CODES` refuses the
      read**, at partition scope, through the same `assess_readiness` call `read_if_ready` makes.

    ## `max_staleness` is decided at partition scope, which is `V2-P4-026`'s correction inherited

    `read_visible_at` re-decides `stale` over the rows it is about to return, and the rows here
    are a whole dataset's history. Reclassifications happen in bursts around an annual review --
    2016 carries 4 of the corpus's 2,004 transitions -- so a bound re-decided against the newest
    *visible* event would refuse nearly every honest read for a reason that has nothing to do with
    whether the panel has fallen behind. So the caller's bound is decided once, at partition
    scope, through exactly the verdict `read_if_ready` would have returned, and the requirement
    handed to the filtered read waives it. Same function, same rule table, same scope as the
    unfiltered door.

    ## What it does not promise

    Everything `read_visible_at`'s own "what it does not promise" says, plus one thing specific to
    this dataset: `KNOWN_INDUSTRY_LIMITATIONS.every_pre_2021_answer_is_a_backfill` is untouched
    here. A cross section for a 2015 day read at a 2022 `as_of` is SW2021's opinion about 2015,
    which `IndustryAnswer.is_backfilled` says on every row and this function neither hides nor
    fixes. A security with no assignment covering `day` -- including one of the 49 measured
    coverage holes -- is **absent from the mapping** rather than raising, which is
    `SecurityIndustryHistory.is_classified_on`'s distinction and the same fold
    `panel_neutralization._industry_answer` already makes; what is no longer folded in with it is
    "this read cannot speak for that day", which is now one of the refusals above.
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise IndustryClassificationError(
            "load_industry_cross_section needs at least one assignment year; a read of no years "
            "would produce a cross section with no securities in it, which is indistinguishable "
            "from a market nobody has ever classified"
        )
    zone = _resolve_timezone(date_timezone)
    knowable_through = as_of.astimezone(zone).date()
    if day > knowable_through:
        raise PanelStorageError(
            f"{INDUSTRY_MEMBERSHIP_DATASET} cannot be read for {day.isoformat()} at "
            f"{as_of.isoformat()}: a membership event becomes knowable at midnight "
            f"{date_timezone} on the day it takes effect, so the newest one this read can see is "
            f"{knowable_through.isoformat()}. On a later day an assignment that is open only "
            "because its closing row had not happened yet is indistinguishable from one that "
            "genuinely never ended, and answering it would name an industry the security may "
            "already have left"
        )
    floor = datetime.combine(
        INDUSTRY_TAXONOMY_EFFECTIVE_FROM[INDUSTRY_MEMBERSHIP_TAXONOMY], time(0, 0), tzinfo=zone
    )
    if as_of < floor:
        raise PanelStorageError(
            f"{INDUSTRY_MEMBERSHIP_DATASET} cannot be read at {as_of.isoformat()}: every "
            f"membership row's availability is floored at {floor.isoformat()}, the instant "
            f"{INDUSTRY_MEMBERSHIP_TAXONOMY} came into force, so no row of any partition was "
            "knowable then. A visibility-filtered read would withhold the whole corpus and hand "
            "back an empty cross section, which says the market had no industries rather than "
            "that this classification did not exist yet"
        )
    stored = sorted(set(store.registered_years(INDUSTRY_MEMBERSHIP_DATASET)))
    skipped = [year for year in stored if year not in set(requested)]
    if skipped and skipped[0] <= day.year:
        raise PanelStorageError(
            f"{INDUSTRY_MEMBERSHIP_DATASET} cannot answer {day.isoformat()}: the store holds a "
            f"{skipped[0]} partition this read did not name, and an assignment's close is stored "
            "as its own row in its own year, so an interval that ended there is indistinguishable "
            "here from one still open. Name every stored year at or before "
            f"{day.year} in `years`, or ask about a day before {skipped[0]}"
        )
    requirement = industry_membership_requirement(
        years=requested, as_of=as_of, max_staleness=max_staleness
    )
    rows = _read_visible_membership_rows(store, requirement, as_of=as_of)
    histories = industry_histories_from_panel_rows(
        rows,
        taxonomy=INDUSTRY_MEMBERSHIP_TAXONOMY,
        answerable_through=(skipped[0] - 1) if skipped else None,
    )
    answers: dict[str, IndustryAnswer] = {}
    for ts_code, history in histories.items():
        try:
            answers[ts_code] = history.industry_on(day)
        except IndustryHorizonError:
            # No assignment covers `day` in what this read could see. Absent from the cross
            # section rather than raised, for `is_classified_on`'s reason: an unclassified day is
            # ordinary data -- 3% of a 2015 cross section -- and the day this read is *not
            # allowed* to speak for was refused above, so the two can no longer arrive here as
            # one exception.
            continue
    return MappingProxyType(answers)


def _knowable_through_the_same_day(as_of: datetime, zone: ZoneInfo) -> date:
    """`as_of`'s own day: the census bound for a dataset whose rows publish at midnight.

    The bound for `ClockStrategy.calendar_static` (`stock_basic`, `namechange`) and for
    `ClockStrategy.taxonomy_backfill` (`index_member_all`). All three date a row's availability
    at midnight `date_timezone` on the day the row is about -- exactly, for the first two, and
    floored at the taxonomy's effective date for the third -- so a row dated `D` is visible to
    every `as_of` on `D` and to none before it, and the newest event date this read can see is
    the day `as_of` falls on.

    `_sessions_published_through` is the same function for a dataset that publishes at 16:30
    instead, and the pair is why `_read_visible_event_dated_rows` takes the bound as an argument
    rather than computing one: `suspend_d`'s rows are `daily_close`-clocked, and reconciling them
    against this bound would count the current session's halts as knowable from midnight and
    refuse every honest read taken before that session's close.
    """
    return as_of.astimezone(zone).date()


def _read_visible_membership_rows(
    store: PanelStore,
    requirement: ReadinessRequirement,
    *,
    as_of: datetime,
) -> tuple[tuple[object, ...], ...]:
    """Every membership row of `requirement.years` that was knowable at `as_of`, or a refusal.

    `V2-P4-027`/`034`'s door, and since `V2-P4-076` a call into
    `_read_visible_event_dated_rows` with this dataset's own availability rule rather than a
    fourth copy of it. What is stated here is what is specific to `index_member_all`: the
    availability rule the reconciliation rests on, and the bound that rule implies.

    The two-step `_read_visible_price_session` established, with the third step this dataset needs
    in place of the all-or-nothing check that dataset gets for free.

    **Step one** runs `assess_readiness` on the caller's own requirement -- the same function, the
    same rule table and the same partition scope as `read_if_ready` -- and refuses on anything
    outside `ROW_FILTERABLE_ISSUE_CODES`. That is where `max_staleness` is decided, and deciding
    it here is what stops `read_visible_at`'s slice-scope recheck from re-deciding it against the
    newest *reclassification*, which is an annual event and not a measure of whether the panel has
    fallen behind.

    **Step two** takes the filtered read per year with the bound waived.

    **Step three** is the check that makes this caller's answer to
    `tests/unit/panel/test_visible_read_callers.py`'s objection a measurement rather than an
    argument. The partition's coverage record counts rows per event date, and a membership row's
    `available_time` is its own event floored at the taxonomy's effective date, so with that floor
    already behind `as_of` the rows the predicate keeps are exactly the rows the census places at
    or before `as_of`'s own day. Any difference refuses the read: a row the census counted and the
    predicate removed is **withheld**, a row the census never counted is **absent**, and a cross
    section short by the first is indistinguishable from one honestly missing the second.

    ## Why the reconciliation is per event date and not per year (`V2-P4-034`)

    `V2-P4-027` wrote step three as one equality between two whole-year totals, and **a sum cannot
    hold a claim about a set of days**: two errors in opposite directions cancel exactly, and the
    read is then admitted with no measurement having taken place. Measured on a five-row 2024
    probe at 12:00 Asia/Shanghai on 2024-06-15 (`tests/integration/panel/test_industry_ingest.py`,
    `PROBE_ROWS`): withhold one row dated 2024-02-01 and reveal one dated 2024-09-01, and the
    census still counts three rows as having happened while three rows still come back. The cross
    section that read admits has a security missing whose assignment began four and a half months
    earlier, and dates another security's assignment as ending 2024-09-01 -- a close two and a half
    months *after* the instant the read stands at, which is the look-ahead this whole plane exists
    to prevent. The one-sided halves of the same corpus were refused correctly, which is why
    nothing saw it: the check was live and failed only where the two faults met.

    So the visible rows' own event dates are counted and held against the census entry by entry,
    which needs `event_time` in the projection. It is **prepended to
    `INDUSTRY_MEMBERSHIP_PANEL_COLUMNS` and stripped before the rows are returned** rather than
    added to that tuple, which is `panel_factors.load_factor_observations` and
    `panel_neutralization.load_neutralized_factor_observations`' idiom on this same method. That
    constant documents itself as "the positional contract of the rows back", and
    `industry_histories_from_panel_rows` both checks its width and unpacks it positionally; the
    clock this reconciliation needs is a property of *this read*, not of the row shape the domain
    decodes, and widening the contract for one caller's benefit would move every consumer of it.

    The two faults are named separately, because they are two different statements about the
    corpus and one message that could only say the totals differed was what let them be traded
    against each other. **The look-ahead is reported first** where both are present: a row visible
    before its own event date has an availability earlier than its event, which the panel's model
    forbids outright, whereas a withheld row is at worst an embargo this read cannot see.

    The census's own `date_timezone` is what `as_of` and every visible row's `event_time` are
    converted in, not the caller's: the census's dates were resolved in that zone, and comparing
    them against days computed in another would be comparing two different calendars.
    """
    return _read_visible_event_dated_rows(
        store,
        requirement,
        INDUSTRY_MEMBERSHIP_PANEL_COLUMNS,
        as_of=as_of,
        what="the industry classification",
        availability_rule=(
            "A membership row's availability is its own event floored at the taxonomy's "
            "effective date"
        ),
        census_through=_knowable_through_the_same_day,
    )


def _read_visible_event_dated_rows(
    store: PanelStore,
    requirement: ReadinessRequirement,
    columns: tuple[str, ...],
    *,
    as_of: datetime,
    what: str,
    availability_rule: str,
    census_through: Callable[[datetime, ZoneInfo], date],
) -> tuple[tuple[object, ...], ...]:
    """Every row of `requirement.years` that was knowable at `as_of`, reconciled per event date.

    **The only door onto a whole-year partition of an event-driven dataset, since `V2-P4-076`.**
    Four callers take it -- `load_industry_cross_section`, `load_stock_universe`,
    `load_suspensions` and `load_name_histories` -- and it is one function rather than four
    because `_read_visible_price_session`'s own docstring records what two doors onto one
    question cost the last time there were two.

    ## What `V2-P4-076` measured, and why these three joined the fourth

    `V2-P4-061` moved `daily`, `daily_basic` and `stk_limit` onto the as-of-sensitive session
    read so that a store holding two cross sections could screen both. On a real panel it could
    still screen only the newest, because `load_shortlist_cross_section` reads four more things
    at the cross section's own instant and three of them still went through `read_if_ready`,
    which judges `not_yet_knowable` on a **partition's** newest `available_time`. Measured on a
    real panel: `stock_basic` 2026-08-19T00:00+08 and `suspend_d` 2026-08-19T16:30+08 against a
    price panel whose newest session was that same day, so every earlier cross section in the
    year was refused for the sake of the newest session's rows. The wall did not fall; it moved.

    The fourth read is the exchange calendar, and it is **not** here. `trade_cal` is clocked
    `calendar_publication`, which dates every row of year Y at 1 January of Y, so a year
    partition's newest availability instant is the *earliest* instant in it and
    `not_yet_knowable` cannot fire at any `as_of` inside the year. It is left on the unfiltered
    door because there is no refusal there to remove.

    ## Why a row predicate is safe here, and what makes that a measurement

    The objection `tests/unit/panel/test_visible_read_callers.py` puts to every caller of
    `read_visible_at` is *can this caller tell a withheld row from an absent one*. For a
    **whole-year** read of an event-driven dataset the answer is no from the rows alone --
    `V2-P4-027` established that, and `V2-P4-034` proved that comparing whole-partition sums
    instead lets a compensating pair admit a look-ahead. It is yes from the partition's own
    **date census**, which counts rows per event date and therefore says exactly how many rows
    an `as_of` must see. A row the census counted and the predicate removed is **withheld**; a
    row the census never counted is **absent**; and the two are two different pairs of numbers
    rather than one short answer.

    That reconciliation is exact only while a row's `available_time` is a function of its own
    event date, and each caller states which function. `stock_basic` and `namechange` are
    `ClockStrategy.calendar_static`, where `_calendar_static_timeline` sets
    `available_time == event_time == midnight` on the row's own date -- so the bound is
    `_knowable_through_the_same_day`. `suspend_d` is `ClockStrategy.daily_close`, where a row is
    knowable at 16:30 on its own `trade_date` -- so the bound is `_sessions_published_through`,
    and using the other one would count the current session's halts as knowable from midnight
    and refuse every honest read taken before that session's close. `index_member_all` is
    `taxonomy_backfill`, whose floor sits behind `as_of` by the time
    `load_industry_cross_section` reaches here, leaving `_knowable_through_the_same_day`.

    **On a partition whose rows carry the provider's own clock the reconciliation therefore
    cannot disagree, and that is the point rather than a weakness.** It is the same backstop
    `_read_visible_price_session`'s second refusal is: unreachable through a well-formed
    partition and live on one whose stored availability instants say something else. The clock
    lives in a provider one package away and nothing in the store enforces it, so it is checked
    rather than assumed -- and for `index_member_all` it is not merely a backstop, because the
    taxonomy floor really can push a row's availability past its own event date.

    ## The two steps before it

    **Step one** runs `assess_readiness` on the caller's own requirement -- the same function,
    the same rule table and the same partition scope as `read_if_ready` -- and refuses on
    anything outside `ROW_FILTERABLE_ISSUE_CODES`. That is where `max_staleness` is decided, and
    deciding it here is what stops `read_visible_at`'s slice-scope recheck from re-deciding it
    against the newest *event*, which on every one of these datasets is a burst rather than a
    measure of whether the panel has fallen behind.

    **Step two** takes the filtered read per year with the bound waived.

    `EVENT_TIME_COLUMN` is **prepended to the caller's own column contract and stripped before
    the rows are returned** rather than added to it, which is `panel_factors`' idiom on this
    same method: those constants document themselves as "the positional contract of the rows
    back", their decoders check the width and unpack positionally, and the clock this
    reconciliation needs is a property of *this read* rather than of the row shape the domain
    decodes.

    The census's own `date_timezone` is what `as_of` and every visible row's `event_time` are
    converted in, not the caller's: the census's dates were resolved in that zone, and comparing
    them against days computed in another would be comparing two different calendars.
    """
    gate = store.assess_readiness(requirement)
    if {issue.code for issue in gate.issues} - ROW_FILTERABLE_ISSUE_CODES:
        raise PanelStorageError(
            f"{what} cannot be read at {as_of.isoformat()}: "
            f"{[issue.code for issue in gate.issues]}; "
            f"{'; '.join(issue.detail for issue in gate.issues)}"
        )
    filtered = replace(requirement, max_staleness=None)
    rows: list[tuple[object, ...]] = []
    for year in requirement.years:
        outcome = store.read_visible_at(filtered, year=year, columns=(EVENT_TIME_COLUMN, *columns))
        if outcome.is_blocked:
            raise PanelStorageError(
                f"{what} cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.blocking_issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.blocking_issues)}"
            )
        coverage = store.read_coverage(requirement.dataset, year)
        if coverage is None:
            raise PanelStorageError(
                f"{requirement.dataset} year={year} passed readiness with no coverage record, so "
                "the row census this read checks the visible slice against does not exist"
            )
        zone = _resolve_timezone(coverage.date_timezone)
        census_day = census_through(as_of, zone)
        visible: Counter[date] = Counter(
            _visible_event_date(row[0], dataset=requirement.dataset, year=year, zone=zone)
            for row in outcome.rows
        )
        happened = Counter(
            {
                entry.event_date: entry.row_count
                for entry in coverage.dates
                if entry.event_date <= census_day
            }
        )
        _refuse_a_slice_the_census_disagrees_with(
            visible,
            happened,
            dataset=requirement.dataset,
            year=year,
            as_of=as_of,
            census_day=census_day,
            withheld_row_count=outcome.withheld_row_count,
            availability_rule=availability_rule,
        )
        rows.extend(tuple(row[1:]) for row in outcome.rows)
    return tuple(rows)


def _visible_event_date(value: object, *, dataset: str, year: int, zone: ZoneInfo) -> date:
    """One visible row's `event_time` as the day the partition's census filed it under.

    The same conversion `_date_census` performs at write time, in the same zone, so the two sides
    of the reconciliation are the same function of the same column rather than two spellings of
    one intention.

    Per row rather than per distinct instant, which is the one place it departs from that helper.
    `_date_census` folds first because a year of daily prices is millions of rows over a couple of
    hundred instants; the whole membership corpus is 7,893 rows over every year it has, so folding
    would trade a measurable nothing for a `Counter` keyed on values this function has not yet
    established are even hashable.
    """
    if not isinstance(value, datetime):
        raise PanelStorageError(
            f"{dataset} year={year} read back {type(value).__name__} for {EVENT_TIME_COLUMN}, not "
            "a datetime; the row census this read reconciles against is keyed by the event date "
            "resolved from that column and cannot be resolved from anything else"
        )
    return value.astimezone(zone).date()


def _refuse_a_slice_the_census_disagrees_with(
    visible: Counter[date],
    happened: Counter[date],
    *,
    dataset: str,
    year: int,
    as_of: datetime,
    census_day: date,
    withheld_row_count: int,
    availability_rule: str,
) -> None:
    """Hold the visible rows' event dates against the partition's census, date by date.

    Two refusals rather than one, because a **look-ahead** and a **withheld** row are two
    different statements and `V2-P4-034` is what a single message about two totals cost. The
    look-ahead is decided first: it says a row was visible before its own event, which the
    availability rule this dataset is stored under cannot produce at all, while a shortfall says
    only that a row this read should have seen was held back.

    `availability_rule` is the caller's own sentence about how its dataset's `available_time`
    follows from its event date, and it is an argument rather than a constant because the four
    callers' rules genuinely differ -- a floor at a taxonomy's effective date, a midnight, and a
    16:30 close. The message has to name the rule it is holding the partition to, or a reader
    handed "those two numbers should be equal" has no way to check whether they should.
    """
    ahead = sorted(day for day in visible if day > census_day)
    if ahead:
        day = ahead[0]
        raise PanelStorageError(
            f"{dataset} year={year} answered {visible[day]} visible row(s) dated "
            f"{day.isoformat()}, whose event had not happened at {as_of.isoformat()} -- the "
            f"partition's date census places it after the {census_day.isoformat()} this read can "
            f"see. {availability_rule}, so it is never earlier than the event, and a row visible "
            "before its own event carries an availability this panel's model does not allow. The "
            "answer it feeds would carry a fact from after the instant the read stands at"
        )
    disagreed = sorted(day for day in set(visible) | set(happened) if visible[day] != happened[day])
    if not disagreed:
        return
    day = disagreed[0]
    raise PanelStorageError(
        f"{dataset} year={year} cannot be read at {as_of.isoformat()}: its date census counts "
        f"{happened[day]} row(s) dated {day.isoformat()}, whose event had already happened, and "
        f"the visible slice carries {visible[day]} of them ({withheld_row_count} row(s) withheld "
        f"in all). {availability_rule}, so on a partition this read may answer from those two "
        "numbers are equal on every event date one at a time -- not merely in sum, which two "
        "errors in opposite directions cancel in. Where they differ, a row is being withheld for "
        "a reason this read cannot see and an answer short by it is indistinguishable from one "
        "where the row does not exist"
    )


def load_industry_trees(
    store: PanelStore,
    *,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
) -> Mapping[str, IndustryTree]:
    """Read stored tree partitions back as one `IndustryTree` per vintage, or refuse to.

    Keyed by taxonomy rather than by year, because the year is an artefact of how a vintage is
    filed and the vintage is what a caller asks about. The parent-chain rule runs on every load,
    inside `build_industry_tree`, so a partition that lost part of a vintage is refused here
    rather than surfacing later as a leaf whose L1 cannot be resolved.
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise IndustryClassificationError(
            "load_industry_trees needs at least one vintage year; a read of no years would "
            "produce no tree at all, which is indistinguishable from a failed read"
        )
    requirement = industry_tree_requirement(
        years=requested, as_of=as_of, max_staleness=max_staleness
    )
    rows: list[tuple[object, ...]] = []
    for year in requested:
        outcome = store.read_if_ready(requirement, year=year, columns=INDUSTRY_TREE_PANEL_COLUMNS)
        if outcome.is_blocked:
            raise PanelStorageError(
                f"the industry tree cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.readiness.issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.readiness.issues)}"
            )
        rows.extend(outcome.rows)
    return industry_trees_from_panel_rows(rows)


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


def write_financial_statements(
    store: PanelStore,
    batches: Sequence[ColumnarPanelBatch],
    *,
    date_timezone: str = DEFAULT_DATE_TIMEZONE,
) -> tuple[PartitionRef, ...]:
    """Write one financial-statement dataset into one partition per **announcement** year.

    The announcement, not the period. `providers/tushare.py` dates every row of these four
    endpoints at its own `ann_date`, so `001278.SZ`'s 2018 annual report -- announced
    2022-01-06 -- lands in the 2022 partition. Filing it under 2018 would put a row in a
    partition every reader of 2018, 2019, 2020 and 2021 can see, which is the look-ahead this
    dataset's whole clock exists to prevent.

    ## Why a sequence of batches, and why the split runs anyway

    A sequence, like `write_index_weights`: one request is one `(security, year)` window (see
    `_financial_statement_params`), so a year of the whole market is ~5,500 fetches and
    `PanelStore.write_partition` replaces a partition whole -- writing them one at a time would
    leave the year holding whichever security went last.

    The split runs even so, and for `fina_indicator` it is load-bearing rather than defensive.
    That endpoint's request window filters the **report period**, not the announcement, so a
    window for period-year *Y* returns rows announced in *Y* and in *Y+1* -- the annual report
    of *Y* is announced the following spring. `panel_partition_year` refuses a batch that
    straddles two years, so `split_panel_batch_by_year` places them. The three statement
    endpoints filter `ann_date` and produce a single-year batch, and the split is then the
    identity.

    ## The revision census is switched on here, and only where a label exists

    `revision_field="update_flag"` is what fills `PartitionCoverage.revisions`, the facet
    `panel/catalog.py` built for exactly this dataset because the clock-derived
    `revised_row_count` provably cannot see these corrections -- both rows of a corrected pair
    carry the same `ann_date`. `fina_indicator` gets `None`, because it has no label column at
    all; `_revision_census` refuses a field that is not a column, and passing one would trade a
    real absence for a crash.

    **The census is a count of labels, not a resolution.** It says the 2024 partition holds
    1,204 rows labelled `0` and 1,187 labelled `1`; it does not say which of a pair is current,
    and nothing here does. See `domain/financial_statements.py`.

    ## The subject guard

    The subject is the security, so `_refuse_to_drop_stored_subjects` blocks a batch that would
    replace a year's partition with one holding fewer securities -- which is exactly what a
    naive `for code in universe: write_financial_statements(store, [fetch(code)])` produces.
    Every security whose window touches a year has to arrive in one call.
    """
    merged = merge_panel_batches(batches)
    if merged.dataset not in FINANCIAL_STATEMENT_DATASETS:
        raise FinancialStatementError(
            f"expected one of the financial-statement datasets "
            f"{list(FINANCIAL_STATEMENT_DATASETS)}, got {merged.dataset!r}"
        )
    revision_field = (
        REVISION_LABEL_COLUMN if merged.dataset in DATASETS_WITH_REVISION_LABEL else None
    )
    by_year = split_panel_batch_by_year(merged, date_timezone=date_timezone)
    for year, yearly in by_year:
        _refuse_to_drop_stored_subjects(
            store,
            yearly,
            year,
            remedy=(
                "An announcement year's partition is replaced whole and its key has no "
                "ts_code dimension, so every security whose filings fall in that year has to "
                "arrive in one call or not at all"
            ),
        )
    return tuple(
        write_panel_batch(
            store,
            yearly,
            year=year,
            date_timezone=date_timezone,
            revision_field=revision_field,
        )
        for year, yearly in by_year
    )


def financial_statement_requirement(
    *, dataset: str, years: Sequence[int], as_of: datetime, max_staleness: timedelta | None
) -> ReadinessRequirement:
    """What a statement panel must satisfy before filings may be read from it.

    `required_dates` is waived for `industry_membership_requirement`'s reason and a sharper
    version of it: the announcement days of a year are the disclosure calendar of ~5,500
    issuers, and a list of the days a year is "supposed" to contain would be a guess that
    refuses every real year. `required_subjects` is waived because the securities are what the
    read is for.

    `required_fields` is **not** waived and is the dataset's own projection, which is what makes
    a partition written before a column was added block rather than answer `None` for it -- the
    distinction `ReportFiling.value_of` depends on, since it reports a genuinely empty upstream
    cell as `None`.

    `max_staleness` has no default, for `stock_universe_requirement`'s reason: filings arrive in
    four bursts a year and any bound chosen here would be chosen for the caller.
    """
    return ReadinessRequirement(
        dataset=_require_statement_dataset(dataset),
        as_of=as_of,
        years=tuple(sorted(set(years))),
        required_dates=None,
        required_subjects=None,
        required_fields=statement_panel_columns(dataset),
        max_staleness=max_staleness,
    )


def load_statement_histories(
    store: PanelStore,
    *,
    dataset: str,
    years: Sequence[int],
    as_of: datetime,
    max_staleness: timedelta | None,
) -> Mapping[str, StatementHistory]:
    """Read stored announcement years back as one history per security, or refuse to.

    Fail-closed in `load_industry_histories`' shape: a year whose partition is missing, damaged,
    unprofiled or stale is blocked by `read_if_ready()` with its structured issue codes, and the
    rows that survive are assembled by `statement_histories_from_panel_rows`, which refuses a
    row missing a projected column.

    The years asked for are compared against the years the store actually holds, and the first
    stored year this read skipped becomes the histories' `answerable_through` bound --
    `load_industry_histories`' rule, arrived at from the other direction. An industry read that
    stops short of an interval's closing year answers **wrongly**, carrying a label past the
    day the security left it. A statement read that stops short answers **narrowly**: a filing
    is one announcement on one day, complete in its own partition, so every day inside the
    years this read covered gets exactly the answer a reader standing on that day would have
    had. What is lost is the restatement -- `920403.BJ` restated its 2022 annual on 2024-01-05,
    so a read of 2018..2023 answers `filing_for(2022-12-31, 2026-08-01)` with the 2023-03-14
    version. That is right for a day in 2023 and wrong for a day in 2026, and without the bound
    the returned object says nothing about which day it stopped speaking for. The bound is what
    makes the difference visible; a read covering every stored year gets none. See
    `KNOWN_FINANCIAL_STATEMENT_LIMITATIONS.a_partial_year_read_answers_from_inside_its_window`.
    """
    requested = tuple(sorted(set(years)))
    if not requested:
        raise FinancialStatementError(
            f"load_statement_histories needs at least one announcement year for "
            f"{dataset!r}; a read of no years produces a corpus that refuses every question, "
            "which is indistinguishable from a failed read"
        )
    columns = (SUBJECT_COLUMN_NAME, *statement_panel_columns(dataset))
    requirement = financial_statement_requirement(
        dataset=dataset, years=requested, as_of=as_of, max_staleness=max_staleness
    )
    rows: list[tuple[object, ...]] = []
    for year in requested:
        outcome = store.read_if_ready(requirement, year=year, columns=columns)
        if outcome.is_blocked:
            raise PanelStorageError(
                f"the {dataset} panel cannot be read at {as_of.isoformat()}: "
                f"{[issue.code for issue in outcome.readiness.issues]}; "
                f"{'; '.join(issue.detail for issue in outcome.readiness.issues)}"
            )
        rows.extend(outcome.rows)
    skipped = sorted(set(store.registered_years(dataset)) - set(requested))
    return statement_histories_from_panel_rows(
        dataset=dataset,
        columns=columns,
        rows=rows,
        answerable_through=(skipped[0] - 1) if skipped else None,
    )


def _require_statement_dataset(dataset: str) -> str:
    if dataset not in FINANCIAL_STATEMENT_DATASETS:
        raise FinancialStatementError(
            f"expected one of the financial-statement datasets "
            f"{list(FINANCIAL_STATEMENT_DATASETS)}, got {dataset!r}"
        )
    return dataset
