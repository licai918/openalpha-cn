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
filter is the obvious alternative, and it changes what `read_if_ready` *promises* rather than
only what it refuses. **P2 considered it and declined**: a filtered read hands back a short
partition, and every consumer above this plane reads shortness as missing data rather than as
withheld data, so it would turn a fail-closed refusal into a plausible-looking short answer.
The reasoning is recorded in full in
`tests/integration/panel/test_lookahead_injection.py`'s module docstring; the partition-level
gate stays. `tests/unit/panel/test_readiness_rules.py::
test_not_yet_knowable_is_partition_level_so_an_as_of_inside_a_year_reads_nothing` pins the
behaviour and carries the same disclosure next to the assertions.

**P3 took the decision the section above left open, and took neither of the two options it was
posed as.** `read_if_ready` is unchanged -- it promises exactly what it promised, every one of
its fourteen callers is untouched, and `not_yet_knowable` still refuses a whole partition
there. What `V2-P3-002` added is a *second*, differently named read on the same store,
`PanelStore.read_visible_at`, which runs the identical rule table and then, **only** when every
issue it found is in `ROW_FILTERABLE_ISSUE_CODES` (that is: only `not_yet_knowable`), scans the
partition with a `WHERE available_time <= as_of` predicate instead of refusing it.

### The correction `V2-P3-002`'s review forced, and why it was not a wording change

The first version of this section said the substitution left the rule table alone: "every other
issue still refuses the partition whole, so nothing about the structural checks weakened". That
was **false, and measurably so**, and the reason is worth stating in full because it is the
general shape of the defect rather than one instance.

`evaluate_readiness` judges *partition metadata*. `read_visible_at` answers with a *subset of
that partition's rows*. For most codes the distinction does not matter -- a missing file is
missing however you filter it -- but for three of the thirteen the check's **pass** is a
statement about rows the filter then removes, so carrying the verdict over to the filtered
answer carries a conclusion that is no longer supported. `SCOPE_SENSITIVE_ISSUE_CODES` names
those three and the constant carries the code-by-code argument.

`stale` was the sharpest case and it was not merely unsound, it was **structurally dead**.
`stale` compares `as_of - coverage.last_event_time` against the caller's bound, and
`last_event_time` is the newest event in the *whole* partition. `not_yet_knowable` fires
exactly when `max_available_time > as_of`; on a year partition read mid-year the newest event
is also after `as_of`, so the difference is negative and `stale` cannot fire. Measured on this
repository's own fixture panel through `panel_ingest.daily_requirement`: at `as_of`
2026-01-12T04:00Z the filtered read answered with `max_staleness` set to one hour, one day and
two days alike, while the newest **visible** event was 2026-01-09 -- 2 days 21 hours behind a
bound the caller had stated and the store had silently ignored. End to end that produced a
factor stamped `as_of` 2026-06-30 whose newest input session was 2026-01-09, 172 days of
staleness under a declared bound of 7 days, reported as `coverage="computed"`.

So `read_visible_at` now **re-runs the scope-sensitive checks against the rows it is about to
return**, through the same predicate `evaluate_readiness` uses (`staleness_issue`), not a
second copy of it. `VISIBLE_SLICE_RECHECKS` names which two are re-run, the third (`date_gap`)
is disclosed in `KNOWN_STORAGE_LIMITATIONS` with the measurement that rules the re-check out,
and a slice that fails one of them is **refused**, not annotated.

Three properties make the result a different trade from the one P2 declined:

- The short answer is **stated**, not inferred. `PanelVisibleReadOutcome` carries
  `withheld_row_count`, so "shortness" arrives as a number beside the rows rather than as
  something a consumer has to notice, and `visible_last_event_time` says how far the answer
  actually reaches -- which is a *different* fact, and the one that matters. The two are not
  correlated: the 172-day build above withheld four rows out of fourteen. The consumers P2
  named -- `build_index_membership`, `load_industry_histories`, `build_stock_universe` -- read
  shortness as missing data because nothing told them otherwise, and they still take the
  un-filtered path.
- `tests/unit/panel/test_visible_read_callers.py` pins which `src/` files may call it at all.
  The **types** do not carry this: `PanelVisibleReadOutcome.rows` and `PanelReadOutcome.rows`
  have the identical static type, so a type checker stops only the mistake of passing a whole
  *outcome* where the other was expected, and the three rebuilders above take rows. The AST
  allowlist is the obstacle; the type split is a smaller thing than the first version of this
  paragraph claimed, and `test_the_two_read_outcomes_expose_rows_at_the_same_static_type` pins
  the fact the claim now rests on.
- Exactly one readiness code is compensated by the predicate, and the checks whose conclusion
  the filter could invalidate are re-decided over the filtered rows rather than inherited; see
  `ROW_FILTERABLE_ISSUE_CODES` and `SCOPE_SENSITIVE_ISSUE_CODES` for the code-by-code argument.

What it cannot promise is disclosed rather than argued away, in
`KNOWN_STORAGE_LIMITATIONS.a_visibility_filtered_read_replays_a_partition_that_was_not_there_yet`:
filtering a retrospectively written partition reconstructs what the *stored* rows say was
knowable then, which is not the same as what a fetch made at that instant would have returned.

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
from collections.abc import Set as AbstractSet
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

ROW_FILTERABLE_ISSUE_CODES: Final[frozenset[str]] = frozenset({"not_yet_knowable"})
"""The one issue a row-level `available_time` predicate can answer better than a refusal.

`PanelStore.read_visible_at` (`V2-P3-002`) is allowed to proceed over a partition whose *only*
readiness issue is one of these, replacing the refusal with a `WHERE available_time <= as_of`
scan. Every other code stays a refusal, and the rule is written as this set difference rather
than as a second rule table, so `evaluate_readiness` is still the one place a verdict is
computed and a code added there arrives blocking on both paths.

**Exactly one member, and each of the other twelve is excluded for a reason a filter cannot
touch.** `partition_missing` / `partition_file_missing` / `partition_file_unreadable` /
`partition_row_count_mismatch` / `coverage_missing` / `coverage_stale` say the partition is not
what the catalog claims, and filtering rows out of a partition you cannot trust filters nothing
useful. `date_gap` / `subject_missing` / `field_missing` say the data the caller requires is
absent, and withholding *more* of it does not supply it. `stale` says the newest event is older
than the caller's bound, and a predicate that removes recent rows can only make that worse.
`no_years_requested` / `empty_requirement` say the requirement inspected nothing.

`not_yet_knowable` is different in kind: it is the one verdict that is true *of some rows and
not others*. `evaluate_readiness` compares one number per partition -- `max_available_time` --
and a partition is a year, so a complete 2015 partition is refused at every `as_of` in 2015
even though most of its rows were knowable at most of them. The refusal is sound and the
granularity is the whole of the problem, which is what roadmap section 11 records and what this
constant names.

**This set answers "can a filter repair the refusal", which is only half the question.** The
other half -- "does the *pass* still hold once the filter has run" -- is `SCOPE_SENSITIVE_
ISSUE_CODES`, and the first version of this constant did not distinguish them. Membership here
was and remains correct; what was missing is that three of the twelve exclusions above are
arguments about a code that *fired*, and say nothing about the same code staying silent over a
smaller row set.
"""

SCOPE_SENSITIVE_ISSUE_CODES: Final[frozenset[str]] = frozenset(
    {"stale", "date_gap", "subject_missing"}
)
"""The checks whose **pass** over a whole partition does not carry to a filtered subset of it.

`ROW_FILTERABLE_ISSUE_CODES` decides which refusals `read_visible_at` may replace with a
predicate. This decides something the first cut of `V2-P3-002` never asked: for the codes that
did **not** fire, is "no issue found" still true of the rows the caller is actually handed?

**The criterion, so a code added later is classified rather than guessed at.** A code belongs
here exactly when its verdict reads a *per-row* fact -- an event instant, a subject, a session
date -- pooled over the partition. Removing rows can then turn a pass into a failure, because
the evidence for the pass may live entirely in the removed rows. A code belongs *outside* it
when its verdict reads only catalog metadata or the requirement's own shape, where filtering
changes nothing:

- `partition_missing`, `partition_file_missing`, `partition_file_unreadable`,
  `partition_row_count_mismatch`, `coverage_missing`, `coverage_stale` read registration, the
  file, and the content hash. A subset of rows is not a different file.
- `field_missing` reads the *column* list. A row predicate removes rows, never columns, so a
  partition that carries a column still carries it after filtering.
- `no_years_requested`, `empty_requirement` read the requirement, not the data.
- `not_yet_knowable` is the one being compensated and is in `ROW_FILTERABLE_ISSUE_CODES`.

The three that are here are here for measured reasons, not by inspection:

- **`stale`** compares `as_of - last_event_time`, and `last_event_time` is read off the coverage
  records. On the path `read_visible_at` exists for -- a year partition at an `as_of` inside it
  -- the newest recorded event post-dates `as_of`, the difference is negative, and the check is
  not merely scope-wrong but *unreachable*. See this module's docstring for the two
  measurements.
- **`subject_missing`** pools `coverage.subjects`. A security whose rows all became knowable
  after `as_of` is in the coverage census and absent from the answer, so a requirement naming it
  clears and the caller silently receives a smaller cross section.
- **`date_gap`** pools `coverage.dates` the same way, with the same consequence.

**"Scope-sensitive" means the row set, and only the row set.** All three of these pool across
**every usable year of the requirement** -- `evaluate_readiness` takes `max(...)` over the
years' reaches and unions their subject and date censuses -- and re-deciding them over one
partition's visible rows changes two things at once where only one was intended. The first cut
of `read_visible_at` did exactly that and refused two-year requirements `read_if_ready` permits;
`read_visible_at` carries the measurement and now pools the re-check the same way.

`VISIBLE_SLICE_RECHECKS` says which of the three `read_visible_at` re-decides over the rows it
returns. It is deliberately a *strict* subset, and the residue is disclosed rather than
implied.
"""

VISIBLE_SLICE_RECHECKS: Final[frozenset[str]] = frozenset({"stale", "subject_missing"})
"""The scope-sensitive checks `read_visible_at` re-runs against the rows it is about to return.

Re-run through the same functions `evaluate_readiness` calls -- `staleness_issue` and
`subject_gap_issue` -- so there is still exactly one place each verdict is computed. That is
what keeps this from being the second rule table `ROW_FILTERABLE_ISSUE_CODES` was written to
avoid: the input changes (the visible slice rather than the coverage census), the rule does not.

**`date_gap` is excluded, and the reason is structural rather than a cost argument.** Re-deciding
it means turning `event_time` instants back into session dates over the visible rows, which is
`panel_ingest._date_census`'s conversion -- and `openalpha_cn.panel` imports no sibling
subpackage, so the store could not share that code even if it wanted to. It would be a *second
implementation* of one conversion, in SQL rather than Python, against a timezone read from the
coverage record; the two would have to agree exactly or the check invents gaps in complete data,
which is the failure mode `evaluate_readiness`'s own `required_dates` validation calls "a blocked
verdict with an invented cause is worse than a crash".

What makes that trade acceptable rather than merely convenient is measured: on every path that
reaches `read_visible_at` today the re-check would be a **no-op**. Only `_price_requirement`
states `required_dates` at all (every other requirement in `panel_ingest` waives it), and it
clamps them with `_sessions_published_through`, which cuts at 16:30 Asia/Shanghai -- the *same*
instant the provider dates `available_time` at. So every required session is visible by
construction: on the fixture panel at 2026-01-12T04:00Z, `daily_requirement` names exactly the
five sessions the filtered read returns. `test_the_date_gap_recheck_would_be_a_no_op_on_every_
requirement_that_states_dates` asserts that equality, so the day a caller states dates on some
other footing the exclusion has to be re-argued instead of inherited.

The residue is real and is disclosed as
`KNOWN_STORAGE_LIMITATIONS.date_gap_clears_on_partition_rows_the_filtered_read_withholds`,
with the two things that do still bound it named there.
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
    StorageLimitation(
        code="a_visibility_filtered_read_replays_a_partition_that_was_not_there_yet",
        detail=(
            "read_visible_at() answers a mid-year as_of by scanning the year partition with a "
            "row-level available_time <= as_of predicate instead of refusing it, and reports "
            "how many rows it withheld. What it reconstructs is what the STORED partition says "
            "was knowable then -- not what a fetch made at that instant would have returned. "
            "The two differ wherever the upstream is not append-only: a partition is written "
            "whole and once, months after the sessions in it, so a filing that was later "
            "restated is stored only in its restated form (roadmap section 7 measured "
            "fina_indicator carrying two rows for 81.7% of its keys with identical four-clock "
            "timelines, so no available_time separates the versions), a security absent from "
            "the registry snapshot the partition was built from is absent at every as_of "
            "inside it, and a row the upstream served then and does not serve now is not "
            "there at all. Nothing on this plane can close that: it needs a revision history "
            "the endpoints do not publish. THE MAGNITUDE IS MEASURED, NOT ONLY THE MECHANISM: "
            "on fina_indicator the affected share of keys is 81.7%, so for that dataset the "
            "replay is wrong about the majority of what it replays, and it is wrong in one "
            "direction -- every such key reads back at its restated value, which is the value "
            "a backtest would not have had. AND THIS PATH IS WHERE THAT BIAS FIRST BECOMES "
            "REACHABLE AT ALL: read_if_ready refuses a year partition at every as_of inside "
            "it, so before read_visible_at existed a mid-year replay was not something this "
            "store could do wrongly -- it was something it could not do. The entry describes "
            "an exposure the method created, not one it inherited. Bounded, not closed: the "
            "filter replaces exactly one code (ROW_FILTERABLE_ISSUE_CODES) and a partition "
            "with any other issue is refused whole; the checks whose pass a row filter could "
            "invalidate are re-decided over the returned rows (VISIBLE_SLICE_RECHECKS) rather "
            "than inherited; and tests/unit/panel/test_visible_read_callers.py pins which src/ "
            "files may take this path, because a filtered read hands back a short partition "
            "and every domain rebuilder above this plane reads shortness as missing data"
        ),
    ),
    StorageLimitation(
        code="date_gap_clears_on_partition_rows_the_filtered_read_withholds",
        detail=(
            "date_gap asks whether every required session is present in the partition's "
            "write-time date census. read_visible_at then answers with the rows knowable at "
            "as_of, and a session whose rows all became knowable later is in the census and "
            "not in the answer -- so the check clears on evidence the caller never receives. "
            "It is the one member of SCOPE_SENSITIVE_ISSUE_CODES that is NOT re-decided over "
            "the visible slice. Re-deciding it needs event instants turned back into session "
            "dates in the partition's own timezone, which is panel_ingest._date_census's "
            "conversion, and openalpha_cn.panel imports no sibling subpackage -- so it would be "
            "a second implementation of one conversion, in SQL, that has to agree exactly or "
            "report invented gaps in complete data. MEASURED BOUND ON WHAT THAT COSTS TODAY: "
            "the re-check would be a no-op on every path that reaches read_visible_at, because "
            "_price_requirement is the only requirement in panel_ingest that states "
            "required_dates and it clamps them with _sessions_published_through, which cuts at "
            "the same 16:30 Asia/Shanghai instant the provider dates available_time at -- so "
            "every required session is visible by construction (on the fixture panel at "
            "2026-01-12T04:00Z, five required and the same five returned). What still bounds "
            "the general case: the partition-level date_gap catches a partition that stops "
            "short of as_of outright, and the re-decided stale check catches a visible slice "
            "that does not reach the caller's declared bound. What neither catches is a caller "
            "stating required_dates on some footing other than the availability clock, whose "
            "required session is present in the partition and entirely withheld from the answer"
        ),
    ),
    StorageLimitation(
        code="a_derived_partition_may_outlive_the_build_its_rows_point_at",
        detail=(
            "this plane has no cross-dataset referential integrity, and V2-P3-003 is the first "
            "issue whose rows depend on one. A processed factor observation names the raw "
            "observation it was computed from by (source_manifest_id, subject, as_of), which is "
            "the exact key of a row in factor_obs_<key>_v<n> -- and nothing stops the raw "
            "partition being rewritten without it. MEASURED, NOT INFERRED: on the fixture panel, "
            "a raw build at 2026-01-12 was written, transformed and stored, and then "
            "write_factor_panels was called for a second as_of with supersedes naming the first "
            "build. The write succeeded, the manifest partition then held one build (the new "
            "one), and all 8 processed rows still carried the superseded build's ID -- 8 of 8 "
            "resolving to nothing. The processed partition was not touched, no refusal fired and "
            "no reader can tell from the processed row alone. Closing it needs the raw writer to "
            "consult every processed partition derived from the factor it is replacing, which is "
            "a partition SCAN rather than a catalog read (PartitionCoverage.subjects on the "
            "transform manifest partition holds transform_manifest_ids, not the "
            "source_manifest_ids they point at) and would put a cross-dataset dependency inside "
            "a writer that has none today. THE BOUND THIS ENTRY FIRST CLAIMED WAS TOO NARROW AND "
            "IS CORRECTED: it said supersedes is the only path, so a dangling pointer needs a "
            "deliberate act that already names the build it is destroying. There is a simpler "
            "path that needs no such act -- nothing requires the raw partition to have been "
            "written at all. MEASURED: compute_factor, apply_factor_transform and "
            "write_processed_factor_panels with write_factor_panels never called writes both "
            "processed-plane partitions and stores 8 processed rows naming a build no partition "
            "holds, with no refusal, while load_factor_observations for the same factor and year "
            "answers 'cannot be read' with partition_missing. Write ORDER is not enforced across "
            "datasets, because this plane has no cross-dataset integrity at all, which is this "
            "entry's own first sentence; the deliberate-act framing described destroying a build "
            "that was there, not never writing one. WHAT DOES STILL BOUND IT: an ordinary "
            "rebuild of a raw build reproduces its manifest_id (the wall clock and the "
            "provider's batch digest are both out of the address, which V2-P3-001's remediation "
            "established for this reason) and _refuse_to_drop_a_stored_build refuses every other "
            "write that would remove a stored build, so a raw partition that exists cannot lose "
            "a build by accident; and the pointer is checkable by any reader willing to pay a "
            "join, since load_factor_observations returns (manifest_id, subject, as_of) for the "
            "very key the processed row carries. The recovery in both cases is to recompute the "
            "transform against the source that is actually stored -- which reproduces its own "
            "identity from that source and can be written straight over the old one"
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


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelVisibleReadOutcome:
    """A verdict, the rows that were knowable at `as_of`, and how many were held back.

    `PanelStore.read_visible_at`'s answer, and a **different type** from `PanelReadOutcome`
    rather than a flag on it, because the two make different promises.

    **What that type split is worth is smaller than `V2-P3-002` first claimed, and the claim is
    corrected here rather than quietly dropped.** It was written as "mypy refusing to pass one
    where the other is expected is what keeps a caller from handing a filtered read to something
    that reads shortness as missing data". Measured: `PanelVisibleReadOutcome.rows` and
    `PanelReadOutcome.rows` have the *identical* static type, `tuple[tuple[object, ...], ...]`,
    and the three domain rebuilders the objection is about take rows rather than outcomes -- so
    `stock_universe_from_panel_rows(list(filtered.rows), ...)` type-checks clean under
    `mypy --strict`, and so does assigning one `.rows` to a variable annotated for the other.
    What the type checker does stop is passing a whole *outcome* to a function annotated for the
    other one. The obstacle that actually holds is
    `tests/unit/panel/test_visible_read_callers.py`'s AST allowlist, and
    `test_the_two_read_outcomes_expose_rows_at_the_same_static_type` pins the fact this
    paragraph now rests on, so a later change that made the types genuinely differ would show up
    as a failing test rather than as prose drifting back into being true.

    ## What it promises that `PanelReadOutcome` does not

    `rows` here is not the partition. It is the partition minus every row whose
    `available_time` post-dates `as_of` (or is absent), and **two numbers describe what that
    did**, because one of them alone is misleading:

    - `withheld_row_count` -- how many rows the predicate removed. P2 declined a row-level
      filter on the ground that "a filtered read hands back a *short* partition, and every
      consumer above this plane reads shortness as missing data rather than as withheld data",
      and this is the number that makes shortness *stated*: `build_index_membership` cannot
      tell a filtered month from an absent one because nothing tells it, and here something
      does.
    - `visible_last_event_time` -- **how far the answer reaches**. Added by `V2-P3-002`'s review
      because the first number does not imply it and cannot substitute for it. The two are
      uncorrelated in the direction that matters: the worst measured case (a factor stamped
      2026-06-30 whose newest input session was 2026-01-09) withheld *four* rows out of
      fourteen. A small `withheld_row_count` beside a badly stale slice is the shape the
      compensation was weakest against, so the second number is not a convenience -- it is the
      one a caller has to look at.

    ## The sentinels, and the mistake each one is measured against

    - **`rows` raises on a blocked outcome**, and the merged shape is available only as
      `rows_or_none`. `PanelReadOutcome`'s own docstring records why: `if not outcome.rows:`
      and `outcome.rows or []` both type-check and both silently merge blocked with
      ready-and-empty.
    - **`withheld_row_count` raises on a blocked outcome** for the same reason, with the same
      escape hatch under the name `withheld_row_count_or_none`. A blocked read withheld
      *everything*, and answering `0` would be the one number a caller must not be told.
    - **`visible_last_event_time` raises on a blocked outcome**, and answers `None` on a ready
      outcome whose visible slice is empty. Those are two different facts -- "you may not ask"
      and "this answer reaches nothing" -- and merging them is what `rows_or_none` versus `()`
      exists to prevent one field over.
    - **`bool()`, `len()` and iteration all raise -- including on a ready outcome.** `if
      outcome:` is the most available way to write this check and it is true of a blocked
      outcome, because a dataclass instance is truthy. `DependencyClearance` (`panel_gate.py`)
      set this precedent and refuses all three on the clearing path as well as the blocking
      one, for the reason that matters: a guard that only rejects the failing case teaches
      nothing on the passing case and is removed the first time someone reads it as noise.

    ## Two verdicts, because there are two row sets

    `readiness` is what `evaluate_readiness` said about the **partition**.
    `visible_slice_issues` is what `evaluate_visible_slice` said about the **rows this outcome
    carries**, and it is non-empty only on an outcome that is blocked *after* a successful scan
    -- the case `V2-P3-002` originally had no way to express, and therefore never refused. See
    `SCOPE_SENSITIVE_ISSUE_CODES` for which checks are re-decided and why the other ten are not.

    A caller that wants "why was I refused" has to read both, which is why `blocking_issues`
    merges them in one place rather than leaving each call site to remember.

    ## `readiness.state` may say `blocked` on an outcome that carries rows

    That is not a contradiction and it is not a leak: `readiness` is the verdict
    `read_if_ready` **would** have returned, kept verbatim rather than rewritten, because the
    whole of this type's contract is "the rule table refused this partition for exactly these
    reasons, and a row predicate answered them". Rewriting the verdict to `ready` would erase
    the only record of *which* refusal was compensated, and rewriting it is also what would let
    a future second filterable code pass unnoticed.

    So `is_blocked` is this outcome's own answer and `readiness.state` is the gate's, and
    `compensated_issue_codes` names the difference -- which is what a report shows a reader and
    what `V2-P3-002`'s manifest could carry if a later issue needs it. A caller branching on
    `outcome.readiness.is_ready` instead of `outcome.is_blocked` would skip rows it was given;
    that is the one mistake this type cannot make raise, so it is named here and the positively
    stated accessor is provided next to it.
    """

    readiness: DatasetReadiness
    as_of: datetime
    rows_or_none: tuple[tuple[object, ...], ...] | None
    withheld_row_count_or_none: int | None
    visible_last_event_time_or_none: datetime | None = None
    visible_slice_issues: tuple[ReadinessIssue, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return self.rows_or_none is None

    @property
    def blocking_issues(self) -> tuple[ReadinessIssue, ...]:
        """Every reason this outcome carries no rows, from both verdicts, or `()` if it does.

        Provided because "why was I refused" now has two possible sources and asking only the
        first is the mistake that would reintroduce the defect this method was corrected for:
        an outcome refused for a stale *visible slice* has `readiness.issues ==
        (not_yet_knowable,)`, which reads as "the year has not finished" and says nothing about
        the bound that was actually breached.
        """
        if self.rows_or_none is not None:
            return ()
        return (*self.readiness.issues, *self.visible_slice_issues)

    @property
    def rows(self) -> tuple[tuple[object, ...], ...]:
        """The rows visible at `as_of`, or `PanelStorageError` if the dataset was blocked."""
        if self.rows_or_none is None:
            raise PanelStorageError(
                f"{self.readiness.dataset} is blocked at {self.as_of.isoformat()}, so it has no "
                f"visible rows to read: {[issue.code for issue in self.blocking_issues]}; "
                "use `rows_or_none` to handle blocked and empty together on purpose"
            )
        return self.rows_or_none

    @property
    def withheld_row_count(self) -> int:
        """How many rows the availability predicate held back, or an error if blocked."""
        if self.withheld_row_count_or_none is None:
            raise PanelStorageError(
                f"{self.readiness.dataset} is blocked at {self.as_of.isoformat()}, so nothing "
                "was read and 'how many rows were withheld' has no answer; use "
                "`withheld_row_count_or_none` to handle blocked and zero together on purpose"
            )
        return self.withheld_row_count_or_none

    @property
    def visible_last_event_time(self) -> datetime | None:
        """How far the returned rows reach, `None` if there are none, or an error if blocked.

        The number `withheld_row_count` does not imply. A read can withhold four rows and still
        answer with a slice whose newest session is 172 days behind `as_of`; that measurement is
        why this exists. `None` here is "this answer reaches nothing", which is a fact about a
        real answer -- the blocked case raises instead, so the two cannot be confused.
        """
        if self.rows_or_none is None:
            raise PanelStorageError(
                f"{self.readiness.dataset} is blocked at {self.as_of.isoformat()}, so nothing "
                "was read and 'how far do the visible rows reach' has no answer: "
                f"{[issue.code for issue in self.blocking_issues]}; use "
                "`visible_last_event_time_or_none` to handle blocked and empty together on "
                "purpose"
            )
        return self.visible_last_event_time_or_none

    @property
    def visible_row_count(self) -> int:
        return len(self.rows)

    @property
    def compensated_issue_codes(self) -> tuple[str, ...]:
        """The refusals the availability predicate answered, in sorted order.

        Empty on a partition the rule table cleared outright -- which is the honest answer,
        because then nothing was compensated and the predicate removed nothing. Non-empty on a
        read that was blocked and is now answered, so a caller (or a report) can say *why* the
        rows it holds are a filtered subset rather than having to infer it from
        `withheld_row_count` being greater than zero, which is a different fact: a partition can
        be refused for `not_yet_knowable` and still withhold nothing from a narrow enough
        filter.
        """
        if self.rows_or_none is None:
            return ()
        return tuple(sorted({issue.code for issue in self.readiness.issues}))

    def __bool__(self) -> bool:
        raise PanelStorageError(
            "a visibility-filtered read outcome has no truth value: `if outcome:` is true of a "
            "blocked one too. Ask `outcome.is_blocked`"
        )

    def __len__(self) -> int:
        raise PanelStorageError(
            "a visibility-filtered read outcome has no length: `len(outcome)` reads as the row "
            "count and would hide the withheld ones. Ask `outcome.visible_row_count` and "
            "`outcome.withheld_row_count`"
        )

    def __iter__(self) -> object:
        raise PanelStorageError(
            "a visibility-filtered read outcome is not iterable: iterating it would look like "
            "iterating the partition, which is exactly what it is not. Iterate `outcome.rows`"
        )


PARTITION_SCOPE: Final[str] = ""
"""What `evaluate_readiness` judges: every row the catalog says the partition holds.

**Empty on purpose.** It is interpolated straight after the dataset name, so the partition-level
messages are byte for byte what they were before the visible-slice checks existed. That is not
tidiness -- `tests/integration/test_panel_interfaces.py` pins one of these details in full
through both the SDK and the HTTP face, and a qualifier added to the *unchanged* path would have
been a gratuitous break of an assertion nothing had falsified.
"""

VISIBLE_SLICE_SCOPE: Final[str] = " restricted to the rows its requested years return"
"""What `read_visible_at` answers with, and therefore what its re-decided checks judge.

Deliberately *not* "the rows visible at that `as_of`", which would be one qualifier short. The
selection these checks are decided over is the availability predicate **and the caller's own
`filters`**, exactly as `withheld_row_count` is -- `_equality_clauses` is shared between the
statements so the two cannot answer about different row sets. A caller that filters to one
security while requiring two is therefore refused with `subject_missing`, and that is the
intended answer: the check is about the answer being handed back, not about what the partition
holds. The partition-level `subject_missing` still reads the coverage census and ignores
`filters`, so the two verdicts remain about the two different things their `scope` strings say.

**"its requested years", plural, is the correction the P3 merge forced.** This read "the rows
this read returns", which described one `read_visible_at` call -- one year. The checks it
qualifies are pooled over `requirement.years` (see `read_visible_at`), so on a two-year
requirement the old wording named the rows of one partition beside a reach taken from another,
which is a misleading account of a refusal in exactly the way reporting `readiness.issues`
instead of `blocking_issues` was.

Threaded into the issue detail rather than into the *code*, so a consumer branching on `stale`
keeps branching on `stale` and a human reading the message is told which set of rows the number
came from. A separate code would have meant `READINESS_ISSUE_CODES` growing a member
`evaluate_readiness` can never emit, which
`test_every_issue_this_evaluator_can_emit_is_declared_in_the_closed_code_set` would (correctly)
refuse.
"""


def staleness_issue(
    *,
    dataset: str,
    as_of: datetime,
    last_event_time: datetime | None,
    max_staleness: timedelta | None,
    scope: str,
    reaches_nothing_is_stale: bool,
) -> ReadinessIssue | None:
    """Is the newest event here recent enough for the caller's declared bound? Asked once.

    Extracted from `evaluate_readiness` for `V2-P3-002`'s review rather than for tidiness.
    `read_visible_at` has to ask the same question of a different row set -- the slice it is
    about to hand back -- and a second copy of `as_of - last_event_time > max_staleness` is
    exactly the second rule table `ROW_FILTERABLE_ISSUE_CODES` was written to avoid. One
    function, two callers, two `scope` strings.

    `scope` is a **suffix** interpolated straight after the dataset name, and `PARTITION_SCOPE`
    is the empty string, so this function's partition-path message is byte for byte the one
    `evaluate_readiness` emitted before the visible-slice checks existed. See `PARTITION_SCOPE`
    for the assertion that pins one of these details in full.

    `reaches_nothing_is_stale` is the one genuine difference between the two questions and it
    is a parameter rather than a branch inside, because getting it wrong is silent either way.
    On the partition path `last_event_time is None` means *no usable coverage was found*, and
    every such year has already produced its own blocking issue -- adding `stale` on top would
    report a freshness fault for a partition that is missing. On the visible-slice path it means
    the predicate kept no row carrying an event instant, which is not a second report of an
    existing fault: the partition cleared every structural check and the answer still reaches
    nothing, which no declared bound tolerates.
    """
    if max_staleness is None:
        return None
    if last_event_time is None:
        if not reaches_nothing_is_stale:
            return None
        return ReadinessIssue(
            code="stale",
            dataset=dataset,
            detail=(
                f"{dataset}{scope} reaches no event instant at all, so nothing about it is "
                f"within the requested as_of {as_of.isoformat()} (tolerance {max_staleness}); a "
                "bound was declared and an answer reaching nothing cannot satisfy it"
            ),
        )
    behind = as_of - last_event_time
    if behind <= max_staleness:
        return None
    return ReadinessIssue(
        code="stale",
        dataset=dataset,
        detail=(
            f"{dataset}{scope} reaches {last_event_time.isoformat()}, which is "
            f"{behind} behind the requested as_of {as_of.isoformat()} "
            f"(tolerance {max_staleness})"
        ),
    )


def subject_gap_issue(
    *,
    dataset: str,
    required_subjects: Sequence[str] | None,
    observed_subjects: AbstractSet[str],
    scope: str,
) -> ReadinessIssue | None:
    """Is every subject the caller named actually here? Asked once, for the same reason.

    The partition path passes the coverage census; `read_visible_at` passes the subjects that
    survived the availability predicate. Same set difference, same code, same `missing_items`
    payload -- so a consumer that reads `issue.missing_items` to say *which* securities are
    absent keeps working on both, and the `scope` suffix is what tells a reader whether they are
    absent from the partition or only from the answer. `PARTITION_SCOPE` is empty, so the
    partition-path detail is unchanged: `tests/integration/test_panel_interfaces.py` compares
    this exact sentence through both the SDK and the HTTP face.
    """
    missing = tuple(sorted(set(required_subjects or ()) - observed_subjects))
    if not missing:
        return None
    return ReadinessIssue(
        code="subject_missing",
        dataset=dataset,
        detail=f"{len(missing)} required subject(s) are absent from {dataset}{scope}",
        missing_items=missing,
    )


def evaluate_visible_slice(
    requirement: ReadinessRequirement,
    *,
    visible_last_event_time: datetime | None,
    visible_subjects: AbstractSet[str] | None,
) -> tuple[ReadinessIssue, ...]:
    """Re-decide `VISIBLE_SLICE_RECHECKS` over the rows a filtered read is about to return.

    `evaluate_readiness` judges what the catalog records; this judges the answer. The two
    disagree exactly for `SCOPE_SENSITIVE_ISSUE_CODES`, whose *pass* is a claim about rows the
    predicate may have removed -- see that constant for the criterion and the code-by-code
    argument, and this module's docstring for the two measurements that made this function
    necessary rather than merely tidy.

    **Both arguments are pooled over `requirement.years`, and passing one partition's is a
    defect rather than a stricter reading.** `max_staleness` and `required_subjects` are fields
    of the requirement, which names a year set, and `evaluate_readiness` decides both over the
    whole of it -- `max(...)` over the usable years' reaches and the union of their subject
    censuses. This function reduces nothing and cannot tell which scope it was handed, so the
    caller owns it: `PanelStore.read_visible_at` pools, and its docstring carries the
    measurement of what happened when it did not.

    Pure and I/O-free, like the evaluator it complements: the store does the scanning and hands
    over what it found. `visible_subjects` is `None` only when the requirement waived
    `required_subjects`, and supplying `None` while the requirement names subjects **raises**
    rather than passing. That is the sentinel, and it is aimed at the likeliest mistake in this
    whole arrangement: a caller that adds a statement to the scan, forgets to thread its result
    through, and gets a green suite because an absent probe reads as an empty set difference.
    """
    if requirement.required_subjects is not None and visible_subjects is None:
        raise PanelStorageError(
            f"the {requirement.dataset} requirement names "
            f"{len(requirement.required_subjects)} required subject(s), so the visible slice "
            "has to be probed for them before it can be judged; passing None here would clear "
            "the check by not running it"
        )
    found: list[ReadinessIssue] = []
    stale = staleness_issue(
        dataset=requirement.dataset,
        as_of=requirement.as_of,
        last_event_time=visible_last_event_time,
        max_staleness=requirement.max_staleness,
        scope=VISIBLE_SLICE_SCOPE,
        reaches_nothing_is_stale=True,
    )
    if stale is not None:
        found.append(stale)
    if visible_subjects is not None:
        absent = subject_gap_issue(
            dataset=requirement.dataset,
            required_subjects=requirement.required_subjects,
            observed_subjects=visible_subjects,
            scope=VISIBLE_SLICE_SCOPE,
        )
        if absent is not None:
            found.append(absent)
    return tuple(found)


def evaluate_readiness(
    requirement: ReadinessRequirement, *, partitions: Sequence[PartitionState]
) -> DatasetReadiness:
    """Judge `requirement` against the partition states the store found. Pure and total.

    Per-year faults are reported first, in year order, then the pooled checks that run across
    every usable year (date coverage, subjects, fields), then the two clock checks. Every
    fault found is reported. `as_of` must be timezone-aware; `PanelStore.assess_readiness`
    enforces that at its boundary, because comparing a naive datetime here would raise
    `TypeError` from inside a rule table rather than name the malformed input.

    **What this judges is the partition**, which is the whole of the distinction
    `evaluate_visible_slice` exists for. Two of the pooled checks -- `subject_missing` and
    `stale` -- are computed here through functions that module also calls, so a filtered read
    can re-decide them over the rows it returns without a second copy of either rule.
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
    subject_issue = subject_gap_issue(
        dataset=dataset,
        required_subjects=requirement.required_subjects,
        observed_subjects=observed_subjects,
        scope=PARTITION_SCOPE,
    )
    if subject_issue is not None:
        issues.append(subject_issue)

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
    stale_issue = staleness_issue(
        dataset=dataset,
        as_of=requirement.as_of,
        last_event_time=last_event_time,
        max_staleness=requirement.max_staleness,
        scope=PARTITION_SCOPE,
        reaches_nothing_is_stale=False,
    )
    if stale_issue is not None:
        issues.append(stale_issue)

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
