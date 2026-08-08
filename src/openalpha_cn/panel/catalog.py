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
but "can this be used". Three properties are load-bearing for the issues that depend on it:

- **It is fail-closed.** Absence of knowledge is never absence of a problem. A registered
  partition with no coverage record cannot answer any of S8's five questions, so it blocks
  (`coverage_missing`) rather than passing for want of anything to complain about. A
  requirement naming no year at all blocks too (`no_years_requested`), because a check that
  looked at nothing must not report "ready".
- **It is relative to `as_of`, never to the wall clock.** Staleness is
  `as_of - last_event_time`, and a partition whose newest `available_time` post-dates `as_of`
  is refused outright (`not_yet_knowable`) rather than leaking hindsight into a point-in-time
  read. The same catalog is ready at one `as_of` and blocked at another, and no verdict here
  changes because a day passed.
- **Blocked is not empty.** `PanelReadOutcome.rows` is `None` for a blocked dataset and `()`
  for a ready one whose filter matched nothing. `PanelStore.query()` cannot tell those apart
  -- it answers `[]` to both -- and that conflation is the root cause `V2-P1-013`'s
  acceptance ("assert blocking, not an empty success") is written against.

Issues are reported as structured codes from the closed `READINESS_ISSUE_CODES` set, not as
prose, because `V2-P1-012`'s report groups by them and `V2-P1-013`'s gate branches on them.
Every issue found is reported, not just the first: a doctor run that surfaced one fault per
invocation would be a game of whack-a-mole.

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

PANEL_CATALOG_SCHEMA_VERSION: Final[str] = "panel-catalog/v1"
"""The catalog's own schema stamp, recorded in `panel_catalog_meta`.

`storage/migrations.py` governs `state.sqlite3` and only that file: it is built on
`PRAGMA user_version`, a `schema_migrations` audit table and SQLite's own backup API, none
of which DuckDB has. The panel catalog is a different engine, a different file and a
different lifecycle, so it carries its own stamp. See `PanelStore`'s module docstring for
what a version bump obliges a future change to do.
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
        "partition_missing",
        "partition_file_missing",
        "coverage_missing",
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

ReadinessState = Literal["ready", "blocked"]


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
    """One requested year, as the store found it: catalogued? present on disk? profiled?

    Three separate booleans rather than one tri-state because the three faults have different
    causes and different fixes. `registered=False` is an un-ingested year. `file_present=False`
    is a corrupted store -- the catalog advertises a partition whose Parquet file is gone, the
    exact defect `V2-P1-012`'s acceptance injects. `coverage is None` is an un-profiled
    partition, which `PanelStore.write_partition` produces by design whenever it is called
    with raw rows rather than through a batch.
    """

    year: int
    registered: bool
    file_present: bool
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
    """

    dataset: str
    as_of: datetime
    years: tuple[int, ...]
    required_dates: tuple[date, ...] = ()
    required_subjects: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    max_staleness: timedelta | None = None


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
    """The verdict: `ready` with no issues, or `blocked` with every issue that was found."""

    dataset: str
    as_of: datetime
    state: ReadinessState
    issues: tuple[ReadinessIssue, ...]
    years_present: tuple[int, ...]
    row_count: int
    subject_count: int
    last_event_time: datetime | None
    last_event_date: date | None

    @property
    def is_ready(self) -> bool:
        return self.state == "ready"


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelReadOutcome:
    """A readiness verdict and, only if it was `ready`, the rows.

    `rows is None` means blocked; `rows == ()` means ready with nothing matching. Keeping
    those two as different *values* is the whole point: `PanelStore.query()` returns `[]` for
    both a missing partition and an unmatched filter, so a gate built on it alone cannot tell
    a failed dataset from a genuinely empty one -- the failure mode `V2-P1-013`'s acceptance
    is written to catch.
    """

    readiness: DatasetReadiness
    rows: tuple[tuple[object, ...], ...] | None

    @property
    def is_blocked(self) -> bool:
        return self.rows is None


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
        usable.append(state.coverage)
        years_present.append(year)

    observed_dates = {day.event_date for coverage in usable for day in coverage.dates}
    missing_dates = tuple(sorted(set(requirement.required_dates) - observed_dates))
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
    missing_subjects = tuple(sorted(set(requirement.required_subjects) - observed_subjects))
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
    missing_fields = tuple(sorted(set(requirement.required_fields) - observed_fields))
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
    )
