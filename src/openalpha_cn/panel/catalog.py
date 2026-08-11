"""The panel data catalog's vocabulary and its readiness rules (`V2-P1-003`).

## What this module is, and what it is not

Everything here is a plain value or a pure function. There is no DuckDB, no filesystem, and
no clock: `PanelStore` owns the catalog file and does every read, write and validation
against it, and `ReadinessRequirement.as_of` is the only "now" any judgement in this module
ever consults. That split is deliberate on both sides -- it keeps `evaluate_readiness`
testable as a rule table (`tests/unit/panel/test_readiness_rules.py` builds states by hand and
touches no I/O), and it keeps the package's single boundary discipline intact, because a
value that is about to become SQL or a filesystem path is validated where it is used, not
where it was constructed.

**None of these dataclasses validates itself, and that is the point.** The previous task
established, with two working reproductions, that a nominal type is not a boundary: a
duck-typed stand-in satisfies every attribute read, and a subclass can override
`__post_init__` away. So `PartitionCoverage` is an honest carrier with no validating
`__post_init__` at all, and the rule it must satisfy lives exactly once, in
`PanelStore.record_coverage`, where the values actually reach the database
(`tests/integration/panel/test_panel_catalog.py::
test_the_store_revalidates_coverage_instead_of_trusting_the_dataclass` builds thirty-four
malformed carriers -- every one of them constructible -- and proves each is refused there). A
second copy of the rule here would be a copy that can drift, and would invite exactly the
false confidence the reproductions broke.

## PRD Story S8, dimension by dimension

S8 asks a catalog for *subjects / fields / date coverage / revision coverage / freshness*.
`V2-P1-001`'s `panel_partitions` answers none of them -- it records where a partition's file
is, how many rows it has, its storage-idempotency hash, and when it was written.
`PartitionCoverage` is the record that answers all five:

| S8 dimension | Where it lands |
|---|---|
| subjects | `subjects` -- the distinct, ordered universe, plus `subject_count` |
| fields | `fields` -- every stored column's name and logical kind, in partition order |
| date coverage | `dates` -- the per-trading-date row census, from which a *hole* is visible |
| revision coverage | `revisions` (label census) **and** `revised_row_count` (clock-derived) |
| freshness | `last_event_time` / `last_event_date` -- how far the data reaches |

Two of those rows carry a judgement worth stating outright.

**Freshness is measured on the event clock, not the write clock.** `panel_partitions.written_at`
records when a partition landed, which is a different fact: a partition backfilled today may
hold only last month's data, and S8's question is "how current is this data", not "how
recently did someone run the ingester". Both facts are kept, in different columns, and
neither is presented as the other.

**Revision coverage needs two facets because one of them provably cannot see the case that
motivates it.** `revised_row_count` counts rows whose `revision_time` post-dates their
`available_time` -- the same notion `evidence/builder.py`'s `revised_after_initial_availability`
marks. Roadmap section 7 measured that Tushare's restatements share *both* announcement
dates, differing only in `update_flag`, so an original filing and its correction carry
identical four-clock timelines and that count reads 0 on exactly the data `V2-P1-011` exists
for. `revisions` is the label census that can see them: an opaque `(label, row_count)` pair
per distinct version. It is empty today because no dataset is wired up yet, but the shape is
here now so that `V2-P1-011` writes `update_flag` into an existing column rather than
migrating a live catalog.

## The readiness contract

`evaluate_readiness` answers a different question from the catalog's: not "what is in there"
but "can this be used". Six properties are load-bearing for the issues that depend on it (the
count said four while five were listed, which is the kind of drift a reader is entitled to
distrust the rest of a docstring for):

- **It is fail-closed.** Absence of knowledge is never absence of a problem. A registered
  partition with no coverage record cannot answer any of S8's five questions, so it blocks
  (`coverage_missing`) rather than passing for want of anything to complain about. A
  requirement naming no year at all blocks too (`no_years_requested`), because a check that
  looked at nothing must not report "ready".
- **A check that was never configured is not a check that passed.** The same rule reaches the
  requirement's own fields: each of `required_dates` / `required_subjects` / `required_fields`
  / `max_staleness` must be stated, `None` waives it *on the record* (`checks_waived`), and a
  declared-but-empty tuple blocks with `empty_requirement`. This closes the shape where a
  default-constructed requirement reported `ready` for a year holding one trading day,
  because an empty set difference is empty and an absent staleness bound cannot be exceeded.
- **A coverage record must still describe the write the catalog says is there.**
  `record_coverage` binds a record to a partition at write time; `write_partition` is
  overwrite-per-partition, so any later backfill or correction unbinds it again.
  `PartitionCoverage.partition_content_hash` carries the `panel_partitions.content_hash` the
  record was written against, and a partition whose *catalog* hash now differs -- or is
  unknown -- blocks with `coverage_stale`. Row counts alone cannot see this: a correction that
  changes values without changing how many rows there are moves the hash and leaves the count
  identical.

  Both sides of that comparison are catalog rows, and this bullet used to say "what is on
  disk", which is a stronger claim than the check makes. `PartitionState.content_hash` is read
  from `panel_partitions`, never recomputed from the Parquet file -- recomputing it would mean
  re-serialising every row of the partition on every gate check. So the rule detects a
  partition re-written *through the store*; it does not detect a file changed behind the
  store's back. Keeping those two catalog rows in agreement with the file is the write path's
  responsibility, and `panel/store.py`'s "The catalog upsert commits before the rename" is how
  it discharges it.
- **One fact is read from the file itself, and it is the cheapest one there is.** The bullet
  above used to end by handing a file changed behind the store's back entirely to
  `partition_file_unreadable`'s eight-byte magic check and to `V2-P1-012`'s deep pass, on the
  stated ground that a swap to a *different but valid* Parquet file "is out of reach of any
  O(1) check". That was wrong, and P2's product acceptance measured how wrong: a row appended
  to a real `stock_basic` partition with `available_time` in 2035 left `panel doctor` reporting
  `READY ... rows=152` (the catalog's number, over a 153-row file), `data-check` `CLEARED`,
  exit 0 -- and `load_stock_universe(as_of=2026-08-11).listed_on(2024-07-02)` then answered
  with the injected security in it. A file's *row count* is not out of reach: Parquet carries
  it in the footer, DuckDB answers `count(*)` over `read_parquet` from metadata rather than by
  scanning (0.26 ms measured on a 796,497-row, 20 MB partition; 0.3 ms on a 2,000,000-row one,
  so it is independent of size), and the store already reads it on the write path for exactly
  this reason. So `PartitionState.file_row_count` is read from the footer on every readiness
  assessment and compared against the coverage record's own `row_count`; a disagreement, or a
  footer that cannot be read at all, blocks with `partition_row_count_mismatch`.

  The whole cost is one footer read per requested year, and the widest assessment this
  repository makes is the one to measure it on: `stock_basic` over the 37 lifecycle years a
  real panel holds went from 123 ms to 134 ms per `assess_readiness` call. A single-year
  assessment pays 0.3 ms.

  Say precisely what that closes and what it does not, because the previous sentence's mistake
  was overclaiming in the other direction. Every insertion and every deletion behind the
  store's back moves the count, so all of them are now caught. An edit that changes *values*
  in place leaves the count identical and is still invisible here -- it is visible only to a
  check that reads the column, which is `V2-P1-012`'s cross-dataset work (`return_path_
  disagreement` catches a corrupted `pct_chg` on live rows) and, where no cross-check reads
  the column, to nothing. `KNOWN_STORAGE_LIMITATIONS` carries that residue as a disclosure
  rather than leaving it to be inferred from this paragraph.

  The comparison is against `PartitionCoverage.row_count` and not against
  `panel_partitions.row_count`, and it is sound because the check runs *after* `coverage_stale`
  has passed: `record_coverage` refuses a record whose `row_count` disagrees with the
  registered partition's, and `coverage_stale` proves the record still describes the catalog's
  current content hash. So at this point the two catalog numbers are provably equal and one
  field on `PartitionState` carries both.
- **It is relative to `as_of`, never to the wall clock.** Staleness is
  `as_of - last_event_time`, and a partition whose newest `available_time` post-dates `as_of`
  is refused outright (`not_yet_knowable`) rather than leaking hindsight into a point-in-time
  read. The same catalog is ready at one `as_of` and blocked at another, and no verdict here
  changes because a day passed.
- **Blocked is not empty, and cannot be quietly treated as empty.**
  `PanelReadOutcome.rows_or_none` is `None` for a blocked dataset and `()` for a ready one
  whose filter matched nothing. `PanelStore.query()` cannot tell those apart -- it answers
  `[]` to both -- and that conflation is the root cause `V2-P1-013`'s acceptance ("assert
  blocking, not an empty success") is written against. Two values are only half the fix,
  because `if not rows:` and `rows or []` merge them again at run time while type-checking
  clean, so the plainly-named `rows` raises on a blocked outcome and the merged shape is
  reachable only through `rows_or_none`.

Issues are reported as structured codes from the closed `READINESS_ISSUE_CODES` set, not as
prose, because `V2-P1-012`'s report groups by them and `V2-P1-013`'s gate branches on them.
Every issue found is reported, not just the first: a doctor run that surfaced one fault per
invocation would be a game of whack-a-mole.

### Disclosure: `not_yet_knowable` is judged per *partition*, and a partition is a year

The point-in-time check compares one number per partition -- `max_available_time`, the newest
instant at which any row in it became knowable -- against `as_of`, and blocks the whole
partition if that number is later. It does not filter rows. The consequence, which is a real
constraint on everything built above this plane and is written here because nothing else in
the tree said it:

**A partition is unreadable at every `as_of` earlier than its own newest availability
instant, and for a full year of data that instant is in December.** A complete 2015 partition
is therefore `blocked` for every `as_of` in 2015 and readable only from 2016-01-01 onward. The
bound is `max_available_time`, not the calendar year as such -- a partition holding only
January is readable from February -- but `panel_ingest`'s session census refuses a partition
missing any session the calendar reports open, so the partitions this plane actually produces
are whole years and the practical rule is the one stated. That is not a data problem -- the
same partition is `ready` one instant later -- it is the granularity of the judgement.

For the phases that consume this: **P3**'s factor computation cannot evaluate a factor at a
mid-year `as_of` through `read_if_ready`, and **P4**'s walk-forward cannot step an `as_of`
through a year while reading that year's partition, because every step inside the year is
`blocked`.

The design is deliberate and fail-closed: `evaluate_readiness` is pure over catalog metadata
and has no access to rows, so a row-level answer would mean promising something about rows it
never filtered. Splitting this into a partition-level gate plus a row-level `available_time`
filter is the obvious alternative, it changes what `read_if_ready` *promises* rather than only
what it refuses, and it is explicitly left to P2 rather than smuggled in here.
`tests/unit/panel/test_readiness_rules.py::
test_not_yet_knowable_is_partition_level_so_an_as_of_inside_a_year_reads_nothing` pins the
behaviour and carries the same disclosure next to the assertions.

## What is deliberately not here

No trading calendar. `ReadinessRequirement.required_dates` is supplied by the caller because
nothing in this repository yet knows which days the exchange was open -- that is `V2-P1-004`.
Hole detection is therefore set difference against a caller-declared expectation, and
`PartitionCoverage.date_timezone` records which timezone the observed dates were derived in
so that `V2-P1-004`'s calendar can be checked against the same convention instead of silently
disagreeing by one day (a session at 08:00 Asia/Shanghai is the previous date in UTC; see
`panel_ingest.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final, Literal


class PanelStorageError(RuntimeError):
    """Raised for panel-store usage errors: an empty write batch, a malformed column
    list, a malformed column name or SQL type, a malformed `dataset` name, a malformed
    `PartitionCoverage` or `ReadinessRequirement`, a catalog stamped with an unknown schema
    version, a `profile_query()` against a partition the catalog has never registered, or
    `PanelReadOutcome.rows` read on a blocked outcome.

    Defined here rather than in `panel/store.py` (which re-exports it, so every existing
    import keeps working) because `PanelReadOutcome.rows` has to raise it and this module
    must not import the store -- the dependency runs store -> catalog, never back.
    """


PANEL_CATALOG_SCHEMA_VERSION: Final[str] = "panel-catalog/v2"
"""The catalog's own schema stamp, recorded in `panel_catalog_meta`.

`storage/migrations.py` governs `state.sqlite3` and only that file: it is built on
`PRAGMA user_version`, a `schema_migrations` audit table and SQLite's own backup API, none
of which DuckDB has. The panel catalog is a different engine, a different file and a
different lifecycle, so it carries its own stamp. See `PanelStore`'s module docstring for
what a version bump obliges a future change to do.

`v2` adds `panel_partition_coverage.partition_content_hash`. The stamp moved even though the
column is structurally additive, because the change is *semantically* breaking in the
direction the stamp exists to guard: a `v1` build opening a `v2` catalog would ignore the
column and report `ready` for exactly the stale-coverage partitions a `v2` build blocks --
a silent fail-open, which is the failure mode the stamp is for. Structural additivity is not
the test; whether an older reader would misjudge the data is.
"""

PANEL_CATALOG_SCHEMA_VERSIONS_READABLE: Final[frozenset[str]] = frozenset(
    {"panel-catalog/v1", PANEL_CATALOG_SCHEMA_VERSION}
)
"""Every stamp this build knows how to read, as opposed to the one it writes.

A `v1` catalog is readable because this build knows precisely what it lacks: the
`partition_content_hash` column. Its coverage rows therefore read back with that field
`None`, which readiness reports as `coverage_stale` -- fail-closed, and cleared by one
`record_coverage()` call, which also migrates the catalog forward and re-stamps it `v2`. A
stamp outside this set was written by a build that knows something this one does not, and is
refused rather than guessed at.
"""

PANEL_BATCH_SCHEMA_VERSIONS_READABLE: Final[frozenset[str]] = frozenset({"panel-batch/v1"})
"""Every `PartitionCoverage.schema_version` stamp this build knows how to interpret.

The *second* stamp in this system, and until the P1 review it was the one with no door.
`PANEL_CATALOG_SCHEMA_VERSION` above describes this module's own DuckDB tables;
`PartitionCoverage.schema_version` describes the **provider batch contract** that produced the
record -- `domain/panel_batch.py`'s `PANEL_BATCH_SCHEMA_VERSION`. Those version independently:
a build that changes only the batch contract leaves the catalog schema at `panel-catalog/v2`,
so `_check_catalog_schema_version` waves the file through and every coverage row inside it is
read as if it were v1.

`domain/panel_batch.py` claimed this was already handled -- "the `schema_version` field is
still carried and hashed, so a future `panel-batch/v2` is detectable rather than silently
compatible" -- and it was not. Carrying a value is not checking it:
`PanelStore._validated_coverage` only required non-empty text, so a coverage row written with
`schema_version="panel-batch/v99"` produced `assess_readiness` -> `ready`, `issues == []`,
measured against `676cba3`. A stamp with a gate and a stamp without one are not the same
mechanism, and two docstrings claimed both had one.

The gate is a refusal (`PanelStorageError`) rather than a readiness issue code, for the reason
`_check_catalog_schema_version` is: an unknown stamp means this build cannot say what the
record's fields *mean*, and a verdict computed from fields whose meaning is unknown is worse
than no verdict. It is enforced on both sides in `panel/store.py` -- `_validated_coverage`
refuses to write one, `_read_coverage` refuses to interpret one another build wrote.

Duplicated here rather than imported, because `openalpha_cn.panel` imports no sibling
subpackage (see `panel/store.py::_utc_now` for the same tradeoff, and
`tests/unit/domain/test_panel_batch.py::
test_the_batch_contract_and_the_panel_catalog_agree_on_the_readable_batch_stamps` for the
drift pin that keeps the two copies honest).
"""

DEFAULT_DATE_TIMEZONE: Final[str] = "Asia/Shanghai"
"""The timezone panel trading dates are derived in unless a caller says otherwise.

A-share sessions are Asia/Shanghai days; `providers/tushare.py` already fixes 15:00/16:30
Asia/Shanghai as the event and availability convention for daily data. The value is recorded
on every coverage row rather than assumed, because the choice is only invisible until it is
wrong: 08:00 Asia/Shanghai is the previous calendar date in UTC.
"""

READINESS_ISSUE_CODES: Final[frozenset[str]] = frozenset(
    {
        "no_years_requested",
        "empty_requirement",
        "partition_missing",
        "partition_file_missing",
        "partition_file_unreadable",
        "partition_row_count_mismatch",
        "coverage_missing",
        "coverage_stale",
        "date_gap",
        "subject_missing",
        "field_missing",
        "stale",
        "not_yet_knowable",
    }
)
"""Every code `evaluate_readiness` can emit, as data rather than as prose.

A closed set because downstream consumers branch on these: `V2-P1-012`'s report groups by
code and `V2-P1-013`'s gate decides what to block on. A code emitted but never declared would
be invisible to both, which is why
`tests/unit/panel/test_readiness_rules.py::test_every_issue_this_evaluator_can_emit_is_declared_in_the_closed_code_set`
drives the evaluator into every branch and asserts the emitted set equals this one exactly.
"""

READINESS_WAIVABLE_CHECKS: Final[frozenset[str]] = frozenset(
    {"required_dates", "required_subjects", "required_fields", "max_staleness"}
)
"""Every check a caller may switch off, by naming `None` for it on the requirement.

Declared as data for the same reason the issue codes are: `V2-P1-012`'s report and
`V2-P1-013`'s gate need to be able to say "this verdict did not look at dates" rather than
having to infer it. A waived check produces no issue and no reassurance -- it produces an
entry in `DatasetReadiness.checks_waived`.
"""

ReadinessState = Literal["ready", "blocked"]


@dataclass(frozen=True, slots=True, kw_only=True)
class StorageLimitation:
    """One structural boundary of the **storage plane itself**, as opposed to of a dataset.

    Shaped as `(code, detail)` so that `panel_doctor._limitations` folds it in exactly the way
    it folds the seven dataset registries -- there is no base class to share, because a
    `domain/` registry may not import this module and this module may not import `panel_doctor`
    (`tests/unit/test_import_layering.py`).
    """

    code: str
    detail: str


KNOWN_STORAGE_LIMITATIONS: Final[tuple[StorageLimitation, ...]] = (
    StorageLimitation(
        code="a_value_edited_in_place_leaves_the_census_intact",
        detail=(
            "readiness reconciles a partition against its catalog record on three facts the "
            "file itself supplies -- Parquet's magic at both ends, the file's presence, and "
            "the footer's row count -- so every row inserted or removed behind the store is "
            "refused (partition_row_count_mismatch). An edit that changes values *in place* "
            "moves none of the three: measured on a real 2026 daily partition, five "
            "percentage points added to one row's pct_chg leaves row_count, subjects, dates "
            "and all four clocks identical and readiness reports ready. Such an edit is "
            "visible only to a check that reads the column -- return_path_disagreement sees a "
            "corrupted pct_chg, close_disagreement sees a corrupted close -- and to nothing "
            "at all for a column no cross-check reads. Detecting it in general needs a digest "
            "of the file's bytes, which is O(rows) on every gate check and is deliberately "
            "not paid here"
        ),
    ),
    StorageLimitation(
        code="panel_store_query_is_public_and_passes_no_point_in_time_gate",
        detail=(
            "PanelStore.query() takes no as_of, consults no readiness verdict and carries no "
            "row-level available_time predicate: it returns every row of the resolved "
            "partition. Measured on a real stock_basic 2024 partition it returns 152 rows, of "
            "which 92 were not knowable at 2024-07-01. The point-in-time gate is read_if_ready"
            "(), which is opt-in rather than structural, and every src/ reader goes through "
            "it -- pinned by tests/unit/panel/test_query_callers.py, which fails when a new "
            "module calls query() directly, because a caller that filtered by available_time "
            "itself would move the guarantee out of this plane with nothing auditing it. "
            "Adding the predicate here was considered and declined: a filtered read hands "
            "back a short partition and every consumer above this plane reads shortness as "
            "missing data (see tests/integration/panel/test_lookahead_injection.py)"
        ),
    ),
)
"""What the storage plane structurally cannot answer, whoever fetched the data.

Separate from the seven `domain/` registries because these are not properties of a dataset:
they hold for `daily` and for `income` alike, and attaching them to a dataset list would make
`known_limitations('adj_factor')` stop meaning "what the adjustment corpus cannot answer".
`panel_doctor` folds them into `PanelHealthReport.limitations` with an empty `datasets` tuple,
which is what keeps the dataset-scoped selection unchanged while still putting them in front of
every reader of a report.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldCoverage:
    """One stored column of a partition: its name and its logical (not SQL) kind."""

    name: str
    kind: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DateCoverage:
    """One trading date present in a partition, and how many rows carry it.

    The row count is what turns "which dates are here" into "which dates are *thin*" for
    `V2-P1-012`; a date entirely absent from this census is a hole, which is why the census
    is stored per date rather than collapsed to a first/last range.
    """

    event_date: date
    row_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RevisionCoverage:
    """One revision version present in a partition, and how many rows carry it.

    `label` is deliberately an opaque string, not a number or an enum: the only revision
    discriminator measured to exist in real data is Tushare's `update_flag` (roadmap section
    7), and committing the catalog to that particular field's shape would be picking
    `V2-P1-011`'s disambiguation strategy for it. A string carries `update_flag`'s `0`/`1`
    today and whatever the next provider versions with tomorrow.
    """

    label: str
    row_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PartitionCoverage:
    """Everything the catalog knows about one `(dataset, year)` partition beyond where its
    file is -- Story S8's five dimensions plus the provenance that produced them.

    Carries no validation of its own; see this module's docstring for why the rule lives in
    `PanelStore.record_coverage` instead.
    """

    dataset: str
    year: int
    provider_id: str
    kind: str
    schema_version: str
    batch_digest: str
    """`ColumnarPanelBatch.content_digest`, the provider-side integrity hash.

    Not the same value as `PartitionRef.content_hash`, and neither replaces the other. The
    store's hash covers `(dataset, year, column names and SQL types, rows)` and exists to
    answer "is this write the same write" -- it is what makes a re-write of identical content
    a true no-op. This digest additionally covers `provider_id`, `kind`, `as_of`,
    `fetched_at`, `source_uri` and `schema_version`, and exists to answer "is this partition
    still what that provider sent at that point in time". Swapping either for the other
    breaks something concrete: the digest changes on every refetch (`fetched_at` is hashed
    into it), so using it for idempotency would rewrite an unchanged partition every time,
    while the store's hash carries no provenance at all, so using it for integrity would lose
    the fetch. Proven in
    `tests/integration/panel/test_panel_coverage_ingest.py::test_the_store_hash_and_the_batch_digest_answer_different_questions`.
    """
    as_of: datetime
    fetched_at: datetime
    row_count: int
    date_timezone: str
    last_event_time: datetime
    """Freshness: the newest `event_time` in the partition -- how far this data reaches.

    Distinct from `recorded_at`/`panel_partitions.written_at`, which say when the partition
    landed. A backfill run today can produce a partition whose newest event is a month old,
    and S8's freshness question is about the data, not the ingester.
    """
    max_available_time: datetime
    """The newest instant at which any row here became knowable -- the point-in-time clock.

    Separate from `last_event_time` because they answer different questions: this one is what
    makes `not_yet_knowable` decidable (may this partition be read at a given `as_of` at
    all?), while `last_event_time` is what makes `stale` decidable (is the data recent enough
    to be worth reading?).
    """
    revised_row_count: int
    subjects: tuple[str, ...]
    fields: tuple[FieldCoverage, ...]
    dates: tuple[DateCoverage, ...]
    revisions: tuple[RevisionCoverage, ...] = ()
    recorded_at: datetime | None = None
    """When the store wrote this record, from its injected clock. `None` before it is stored."""
    partition_content_hash: str | None = None
    """`PartitionRef.content_hash` as it stood when this record was written.

    Filled in by `PanelStore.record_coverage` from the catalog, never by the caller -- like
    `recorded_at`, it is a fact about the *storing*, not about the batch, so it is `None` on
    a record that has not been stored yet (and on a record read back out of a `v1` catalog,
    which has no such column).

    This is what makes a coverage record falsifiable. Without it, `record_coverage`'s
    row-count cross-check binds a record to a partition only at the instant it is written:
    any later `write_partition()` for the same `(dataset, year)` -- a backfill or a
    correction, which is precisely what overwrite-per-partition semantics exist for --
    leaves the catalog describing content that is no longer there, and readiness had no way
    to notice. Comparing this against the partition's *current* hash is what turns that from
    an undetectable fail-open into `coverage_stale`. A row count alone is not enough: a
    correction that rewrites values without changing the number of rows moves the hash and
    not the count.
    """

    @property
    def subject_count(self) -> int:
        return len(self.subjects)

    @property
    def field_count(self) -> int:
        return len(self.fields)

    @property
    def first_event_date(self) -> date | None:
        return min((day.event_date for day in self.dates), default=None)

    @property
    def last_event_date(self) -> date | None:
        return max((day.event_date for day in self.dates), default=None)


@dataclass(frozen=True, slots=True, kw_only=True)
class PartitionState:
    """One requested year, as the store found it: catalogued? on disk? readable? profiled?
    and does the profile still describe what is on disk?

    Separate fields rather than one tri-state because the faults have different causes and
    different fixes. `registered=False` is an un-ingested year. `file_present=False` is a
    corrupted store -- the catalog advertises a partition whose Parquet file is gone, the
    exact defect `V2-P1-012`'s acceptance injects. `file_readable=False` is a *present* file
    that is not a Parquet file (truncated, or replaced by something else). `coverage is None`
    is an un-profiled partition, which `PanelStore.write_partition` produces by design
    whenever it is called with raw rows rather than through a batch.

    `content_hash` is deliberately mandatory and deliberately nullable: the caller must state
    what the catalog says the partition's content hash is, and `None` means "not known" --
    which readiness treats as blocking, not as agreement. A field defaulting to `None` would
    have let a hand-built state silently skip the freshness cross-check that
    `partition_content_hash` exists for.

    `file_row_count` is mandatory and nullable for exactly the same reason, and it is the one
    field here read from the *file* rather than from the catalog: how many rows the Parquet
    footer says the partition holds, or `None` when the store could not ask (no file, not a
    Parquet file, an unreadable footer). `None` blocks. Defaulting it would have made the
    cheapest available check against a partition changed behind the store's back opt-in, which
    is the shape P2's product acceptance found: see this module's docstring's "One fact is read
    from the file itself".
    """

    year: int
    registered: bool
    file_present: bool
    file_readable: bool
    content_hash: str | None
    file_row_count: int | None
    coverage: PartitionCoverage | None
    path: Path | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadinessRequirement:
    """What a caller needs from a dataset before it is willing to read it.

    `as_of` is mandatory and is the only clock any readiness judgement consults; there is no
    wall-clock fallback, so a replay at a historical `as_of` gets that date's verdict rather
    than today's. `required_dates` is caller-supplied because no trading calendar exists in
    this repository yet (`V2-P1-004`); until it does, hole detection is a set difference
    against whatever the caller declares it expects.

    **None of the four checks has a default, and that is the point.** They used to default to
    `()` / `None`, which made the *most permissive* requirement also the easiest one to
    build: a default-constructed requirement never emitted `date_gap` (an empty set
    difference is empty), never emitted `stale` (no bound to exceed), and so reported `ready`
    for a dataset holding one trading day of a whole year. `evaluate_readiness` already
    refused that shape for `years` -- `no_years_requested` exists because "a check that
    looked at nothing must not report readiness" -- and the same rule now reaches the checks
    that carry Story S8's hole detection.

    So each of the four is stated outright, in one of two ways:

    - `None` **waives** the check. The verdict then names it in
      `DatasetReadiness.checks_waived`, so `V2-P1-012`'s report and `V2-P1-013`'s gate can
      see that this judgement did not look at dates, rather than having to assume it did.
    - An **empty tuple** is a declared-but-empty expectation, which is the same vacuous
      check `years=()` is, and blocks with `empty_requirement`. (`max_staleness` has no empty
      form; for it, `None` is the only waiver.)
    """

    dataset: str
    as_of: datetime
    years: tuple[int, ...]
    required_dates: tuple[date, ...] | None
    required_subjects: tuple[str, ...] | None
    required_fields: tuple[str, ...] | None
    max_staleness: timedelta | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadinessIssue:
    """One reason a dataset is not ready, as a machine-readable code plus human detail."""

    code: str
    dataset: str
    detail: str
    year: int | None = None
    missing_dates: tuple[date, ...] = ()
    missing_items: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class DatasetReadiness:
    """The verdict: `ready` with no issues, or `blocked` with every issue that was found.

    `checks_waived` names the checks the requirement switched off (see
    `READINESS_WAIVABLE_CHECKS`). It is mandatory rather than defaulted because the empty
    tuple is the *stronger* claim -- "every check ran" -- and a claim that strong must be
    made deliberately, not inherited from a field default.
    """

    dataset: str
    as_of: datetime
    state: ReadinessState
    issues: tuple[ReadinessIssue, ...]
    years_present: tuple[int, ...]
    row_count: int
    subject_count: int
    last_event_time: datetime | None
    last_event_date: date | None
    checks_waived: tuple[str, ...]

    @property
    def is_ready(self) -> bool:
        return self.state == "ready"


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelReadOutcome:
    """A readiness verdict and, only if it was `ready`, the rows.

    `rows_or_none is None` means blocked; `rows_or_none == ()` means ready with nothing
    matching. Keeping those two as different *values* is the whole point:
    `PanelStore.query()` returns `[]` for both a missing partition and an unmatched filter,
    so a gate built on it alone cannot tell a failed dataset from a genuinely empty one --
    the failure mode `V2-P1-013`'s acceptance is written to catch.

    **`rows` raises on a blocked outcome instead of answering `None`.** Two different values
    are only half a fix, because Python's own truthiness collapses them again: with
    `rows: tuple | None`, `if not outcome.rows:` and `outcome.rows or []` both type-check
    under mypy strict and both silently merge blocked with ready-and-empty at run time --
    and `if not rows:` is the way people write this. So the plainly-named accessor is the
    strict one: reading `rows` on a blocked outcome raises `PanelStorageError`, and the
    two-valued shape is available only under a name that says what it is, `rows_or_none`.
    A caller that wants to merge the two cases can still do it -- but has to name it.
    """

    readiness: DatasetReadiness
    rows_or_none: tuple[tuple[object, ...], ...] | None

    @property
    def is_blocked(self) -> bool:
        return self.rows_or_none is None

    @property
    def rows(self) -> tuple[tuple[object, ...], ...]:
        """The rows, or `PanelStorageError` if the dataset was blocked.

        Deliberately not `tuple | None`: see this class's docstring for why the merged shape
        needs a name of its own.
        """
        if self.rows_or_none is None:
            raise PanelStorageError(
                f"{self.readiness.dataset} is blocked, so it has no rows to read: "
                f"{[issue.code for issue in self.readiness.issues]}; "
                "use `rows_or_none` to handle blocked and empty together on purpose"
            )
        return self.rows_or_none


def evaluate_readiness(
    requirement: ReadinessRequirement, *, partitions: Sequence[PartitionState]
) -> DatasetReadiness:
    """Judge `requirement` against the partition states the store found. Pure and total.

    Per-year faults are reported first, in year order, then the pooled checks that run across
    every usable year (date coverage, subjects, fields), then the two clock checks. Every
    fault found is reported. `as_of` must be timezone-aware; `PanelStore.assess_readiness`
    enforces that at its boundary, because comparing a naive datetime here would raise
    `TypeError` from inside a rule table rather than name the malformed input.
    """
    dataset = requirement.dataset
    by_year = {state.year: state for state in partitions}
    issues: list[ReadinessIssue] = []
    usable: list[PartitionCoverage] = []
    years_present: list[int] = []
    waived = tuple(
        name
        for name in ("required_dates", "required_subjects", "required_fields", "max_staleness")
        if getattr(requirement, name) is None
    )

    if not requirement.years:
        issues.append(
            ReadinessIssue(
                code="no_years_requested",
                dataset=dataset,
                detail=(
                    f"readiness for {dataset} was asked about no year at all; a check that "
                    "inspected nothing must not report readiness"
                ),
            )
        )

    empty_checks = tuple(
        name
        for name in ("required_dates", "required_subjects", "required_fields")
        if getattr(requirement, name) == ()
    )
    if empty_checks:
        issues.append(
            ReadinessIssue(
                code="empty_requirement",
                dataset=dataset,
                detail=(
                    f"{', '.join(empty_checks)} declared an empty expectation for {dataset}, "
                    "which can never find a shortfall; pass None to waive the check on purpose"
                ),
                missing_items=empty_checks,
            )
        )

    for year in sorted(set(requirement.years)):
        state = by_year.get(year)
        if state is None or not state.registered:
            issues.append(
                ReadinessIssue(
                    code="partition_missing",
                    dataset=dataset,
                    year=year,
                    detail=f"no partition is registered for {dataset} year={year}",
                )
            )
            continue
        if not state.file_present:
            issues.append(
                ReadinessIssue(
                    code="partition_file_missing",
                    dataset=dataset,
                    year=year,
                    detail=(
                        f"{dataset} year={year} is registered in the catalog but its Parquet "
                        f"file is missing: {state.path}"
                    ),
                )
            )
            continue
        if not state.file_readable:
            issues.append(
                ReadinessIssue(
                    code="partition_file_unreadable",
                    dataset=dataset,
                    year=year,
                    detail=(
                        f"{dataset} year={year} has a file at {state.path} that is not a "
                        "Parquet file (truncated, or replaced by something else); the "
                        "catalog's description of it cannot be true"
                    ),
                )
            )
            continue
        if state.coverage is None:
            issues.append(
                ReadinessIssue(
                    code="coverage_missing",
                    dataset=dataset,
                    year=year,
                    detail=(
                        f"{dataset} year={year} has no coverage record, so its subjects, "
                        "fields, date coverage, revisions and freshness are all unknown"
                    ),
                )
            )
            continue
        recorded_hash = state.coverage.partition_content_hash
        if recorded_hash is None or recorded_hash != state.content_hash:
            issues.append(
                ReadinessIssue(
                    code="coverage_stale",
                    dataset=dataset,
                    year=year,
                    detail=(
                        f"{dataset} year={year} has a coverage record describing partition "
                        f"content {recorded_hash!r}, but the catalog's partition holds "
                        f"{state.content_hash!r}; the record describes a write that is no "
                        "longer on disk, so its subjects, fields, dates, revisions and "
                        "freshness cannot be trusted"
                    ),
                )
            )
            continue
        if state.file_row_count != state.coverage.row_count:
            issues.append(
                ReadinessIssue(
                    code="partition_row_count_mismatch",
                    dataset=dataset,
                    year=year,
                    detail=(
                        f"{dataset} year={year} has a coverage record describing "
                        f"{state.coverage.row_count} row(s), but the Parquet file at "
                        f"{state.path} says it holds "
                        + (
                            "a number of rows this store could not read"
                            if state.file_row_count is None
                            else f"{state.file_row_count}"
                        )
                        + "; the file has been changed behind the store, so nothing the "
                        "catalog says about it can be trusted"
                    ),
                )
            )
            continue
        usable.append(state.coverage)
        years_present.append(year)

    observed_dates = {day.event_date for coverage in usable for day in coverage.dates}
    missing_dates = tuple(sorted(set(requirement.required_dates or ()) - observed_dates))
    if missing_dates:
        issues.append(
            ReadinessIssue(
                code="date_gap",
                dataset=dataset,
                detail=(
                    f"{len(missing_dates)} required date(s) are absent from {dataset}, "
                    f"starting at {missing_dates[0].isoformat()}"
                ),
                missing_dates=missing_dates,
            )
        )

    observed_subjects = {subject for coverage in usable for subject in coverage.subjects}
    missing_subjects = tuple(sorted(set(requirement.required_subjects or ()) - observed_subjects))
    if missing_subjects:
        issues.append(
            ReadinessIssue(
                code="subject_missing",
                dataset=dataset,
                detail=f"{len(missing_subjects)} required subject(s) are absent from {dataset}",
                missing_items=missing_subjects,
            )
        )

    observed_fields = {item.name for coverage in usable for item in coverage.fields}
    missing_fields = tuple(sorted(set(requirement.required_fields or ()) - observed_fields))
    if missing_fields:
        issues.append(
            ReadinessIssue(
                code="field_missing",
                dataset=dataset,
                detail=f"{len(missing_fields)} required field(s) are absent from {dataset}",
                missing_items=missing_fields,
            )
        )

    last_event_time = max((coverage.last_event_time for coverage in usable), default=None)
    if (
        requirement.max_staleness is not None
        and last_event_time is not None
        and requirement.as_of - last_event_time > requirement.max_staleness
    ):
        issues.append(
            ReadinessIssue(
                code="stale",
                dataset=dataset,
                detail=(
                    f"{dataset} reaches {last_event_time.isoformat()}, which is "
                    f"{requirement.as_of - last_event_time} behind the requested as_of "
                    f"{requirement.as_of.isoformat()} (tolerance {requirement.max_staleness})"
                ),
            )
        )

    max_available = max((coverage.max_available_time for coverage in usable), default=None)
    if max_available is not None and max_available > requirement.as_of:
        issues.append(
            ReadinessIssue(
                code="not_yet_knowable",
                dataset=dataset,
                detail=(
                    f"{dataset} holds information that first became available at "
                    f"{max_available.isoformat()}, after the requested as_of "
                    f"{requirement.as_of.isoformat()}"
                ),
            )
        )

    return DatasetReadiness(
        dataset=dataset,
        as_of=requirement.as_of,
        state="blocked" if issues else "ready",
        issues=tuple(issues),
        years_present=tuple(years_present),
        row_count=sum(coverage.row_count for coverage in usable),
        subject_count=len(observed_subjects),
        last_event_time=last_event_time,
        last_event_date=max(observed_dates, default=None),
        checks_waived=waived,
    )
