"""The readiness contract against a real store (`V2-P1-003`).

Three deliberately injected defects -- a deleted partition, a hole in a partition's date
coverage, and data that has fallen behind the requested `as_of` -- must each be *reported*,
which is the acceptance `V2-P1-012` inherits. And the distinction `V2-P1-013`'s acceptance
turns on ("assert blocking, not an empty success") must be structural: a blocked dataset and
a ready dataset whose filter matched nothing cannot both come back as the same empty result.

`tests/unit/panel/test_readiness_rules.py` covers the evaluator's own rule table without any
I/O; this module is about the store actually producing those states from what is on disk.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import ColumnSpec, PanelStorageError, PanelStore
from openalpha_cn.panel_ingest import panel_column_specs, write_panel_batch

DATASET = "prices_daily"
SUBJECTS = ("000001.SZ", "000002.SZ")
TRADING_DATES = (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4))
# 15:00 / 16:30 Asia/Shanghai, the availability convention `providers/tushare.py` already
# uses for daily data, expressed here as the UTC instants they are.
_CLOSE_UTC_HOUR = 7
_AVAILABLE_UTC_HOUR = 8
AS_OF = datetime(2024, 1, 5, 0, 0, tzinfo=UTC)
FROZEN = datetime(2024, 1, 5, 1, 0, tzinfo=UTC)


def _event_time(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, _CLOSE_UTC_HOUR, 0, tzinfo=UTC)


def _available_time(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, _AVAILABLE_UTC_HOUR, 30, tzinfo=UTC)


def _batch(
    *, days: tuple[date, ...] = TRADING_DATES, subjects: tuple[str, ...] = SUBJECTS
) -> ColumnarPanelBatch:
    grid = [(day, subject) for day in days for subject in subjects]
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset=DATASET,
        kind="daily",
        as_of=AS_OF,
        fetched_at=AS_OF,
        status="success",
        subjects=tuple(subject for _, subject in grid),
        timeline=TimelineColumns(
            event_time=tuple(_event_time(day) for day, _ in grid),
            available_time=tuple(_available_time(day) for day, _ in grid),
            ingested_time=tuple(_available_time(day) for day, _ in grid),
            revision_time=tuple(_available_time(day) for day, _ in grid),
        ),
        columns=(
            PanelColumn("close", "float", tuple(10.5 + index for index in range(len(grid)))),
            PanelColumn("vol", "integer", tuple(index * 100 for index in range(len(grid)))),
        ),
    )


def _store(root: Path) -> PanelStore:
    return PanelStore(root, clock=lambda: FROZEN)


def _requirement(**overrides: object) -> ReadinessRequirement:
    defaults: dict[str, object] = {
        "dataset": DATASET,
        "as_of": AS_OF,
        "years": (2024,),
        "required_dates": TRADING_DATES,
        "required_subjects": SUBJECTS,
        "required_fields": ("close", "vol"),
        "max_staleness": timedelta(days=2),
    }
    defaults.update(overrides)
    return ReadinessRequirement(**defaults)  # type: ignore[arg-type]


def _ready_store(tmp_path: Path) -> PanelStore:
    store = _store(tmp_path / "panel")
    write_panel_batch(store, _batch(), year=2024)
    return store


def test_a_fully_ingested_dataset_reports_ready(tmp_path: Path) -> None:
    readiness = _ready_store(tmp_path).assess_readiness(_requirement())

    assert readiness.state == "ready"
    assert readiness.issues == ()
    assert readiness.row_count == 6
    assert readiness.subject_count == 2
    assert readiness.last_event_date == date(2024, 1, 4)


# --- the three injected defects -------------------------------------------------------------


def test_an_unwritten_year_is_reported_as_a_missing_partition(tmp_path: Path) -> None:
    store = _ready_store(tmp_path)

    readiness = store.assess_readiness(
        _requirement(
            years=(2023, 2024), required_dates=None, required_subjects=None, required_fields=None
        )
    )

    assert readiness.state == "blocked"
    assert [(issue.code, issue.year) for issue in readiness.issues] == [("partition_missing", 2023)]


def test_deleting_a_partitions_parquet_file_is_reported(tmp_path: Path) -> None:
    """The injected defect in its most dangerous form: the catalog still advertises the
    partition, so any check that trusted the catalog alone would call this dataset healthy.

    **The second assertion changed in the P1 re-verification, and it is the only pre-existing
    assertion that group moved.** It used to be `pytest.raises(duckdb.IOException)`, pinning
    the claim this docstring made in prose -- that a caller who skips readiness "gets a stack
    trace rather than a diagnosis". That was a description of a defect written down as though
    it were a design: `PanelStore` promises `PanelStorageError` on every other refusal on this
    path, and `cli._panel_command` turns an exception it does not recognise into
    `internal_error` (exit 5), so `query()` answered "the CLI has a defect" for the state
    `assess_readiness` names four lines up. The diagnosis is now available from `query()` too,
    and it names the same state -- which is a strictly stronger version of what this test was
    written to say, so the test says it instead.
    """
    store = _ready_store(tmp_path)
    partition = store.root / DATASET / "2024" / "data.parquet"
    assert partition.is_file()
    partition.unlink()

    readiness = store.assess_readiness(
        _requirement(required_dates=None, required_subjects=None, required_fields=None)
    )

    assert readiness.state == "blocked"
    assert [(issue.code, issue.year) for issue in readiness.issues] == [
        ("partition_file_missing", 2024)
    ]
    assert str(partition) in readiness.issues[0].detail
    with pytest.raises(PanelStorageError, match="partition_file_missing") as raised:
        store.query(DATASET, year=2024, columns=["close"])
    assert isinstance(raised.value.__cause__, duckdb.IOException)


def test_a_hole_in_the_date_coverage_is_reported_with_the_missing_day(tmp_path: Path) -> None:
    store = _store(tmp_path / "panel")
    write_panel_batch(store, _batch(days=(TRADING_DATES[0], TRADING_DATES[2])), year=2024)

    readiness = store.assess_readiness(_requirement())

    assert readiness.state == "blocked"
    assert [issue.code for issue in readiness.issues] == ["date_gap"]
    assert readiness.issues[0].missing_dates == (date(2024, 1, 3),)


def test_data_that_has_fallen_behind_the_requested_as_of_is_reported_as_stale(
    tmp_path: Path,
) -> None:
    store = _ready_store(tmp_path)
    late = _requirement(as_of=datetime(2024, 3, 1, tzinfo=UTC), max_staleness=timedelta(days=7))

    readiness = store.assess_readiness(late)

    assert readiness.state == "blocked"
    assert [issue.code for issue in readiness.issues] == ["stale"]
    # The same store is ready at an `as_of` inside the tolerance: the verdict is a function
    # of the requirement, never of the day the suite happens to run.
    assert store.assess_readiness(_requirement()).state == "ready"


def test_a_subject_missing_from_the_universe_is_reported(tmp_path: Path) -> None:
    store = _ready_store(tmp_path)

    readiness = store.assess_readiness(_requirement(required_subjects=(*SUBJECTS, "600519.SH")))

    assert readiness.state == "blocked"
    assert [issue.code for issue in readiness.issues] == ["subject_missing"]
    assert readiness.issues[0].missing_items == ("600519.SH",)


def test_a_partition_written_without_a_batch_is_blocked_not_silently_accepted(
    tmp_path: Path,
) -> None:
    """`PanelStore.write_partition` is a storage primitive that takes raw rows, so it cannot
    know a batch's provenance and records no coverage. Fail-closed: an unprofiled partition
    is blocked, never "no issues found"."""
    store = _store(tmp_path / "panel")
    store.write_partition(DATASET, 2024, (ColumnSpec("close", "DOUBLE"),), ((10.5,),))

    readiness = store.assess_readiness(
        _requirement(required_dates=None, required_subjects=None, required_fields=None)
    )

    assert readiness.state == "blocked"
    assert [issue.code for issue in readiness.issues] == ["coverage_missing"]


# --- a coverage record must still describe what is on disk ----------------------------------
#
# `record_coverage()` binds a record to a partition once, at write time. `write_partition()`
# is overwrite-per-partition -- a backfill or a correction is the reason it has those
# semantics -- so any later write unbinds it again, and every check readiness knew how to make
# still passed on the obsolete record. Three reproductions, all of which reported
# `state=ready issues=[]` before `partition_content_hash` existed.


def _overwrite_partition_without_recording_coverage(
    store: PanelStore, *, days: tuple[date, ...], subjects: tuple[str, ...]
) -> None:
    """A partition rewrite that never reaches `record_coverage` -- an interrupted ingest, or
    a caller reaching for the storage primitive directly."""
    replacement = _batch(days=days, subjects=subjects)
    store.write_partition(DATASET, 2024, panel_column_specs(replacement), replacement.to_rows())


def test_a_partition_rewritten_with_fewer_rows_no_longer_passes_on_its_old_coverage(
    tmp_path: Path,
) -> None:
    store = _ready_store(tmp_path)
    before = store.read_coverage(DATASET, 2024)
    assert before is not None and before.row_count == 6

    _overwrite_partition_without_recording_coverage(
        store, days=(TRADING_DATES[0],), subjects=(SUBJECTS[0],)
    )

    readiness = store.assess_readiness(_requirement())
    assert readiness.state == "blocked"
    assert readiness.issues[0].code == "coverage_stale"
    # The stale record is dropped from the pool rather than believed, so the requirement's
    # own checks find the shortfall too instead of being reassured by it.
    assert [issue.code for issue in readiness.issues] == [
        "coverage_stale",
        "date_gap",
        "subject_missing",
        "field_missing",
    ]
    assert store.read_if_ready(_requirement(), year=2024, columns=["close"]).is_blocked


def test_a_partition_rewritten_with_the_same_row_count_is_caught_too(tmp_path: Path) -> None:
    """The variant a row-count cross-check cannot see, and the reason the record carries a
    hash rather than a count. A correction that restates values -- or, as here, replaces the
    subject universe entirely -- leaves exactly as many rows as before, so
    `record_coverage`'s `row_count` agreement still holds while the coverage record has
    become fiction."""
    store = _ready_store(tmp_path)

    _overwrite_partition_without_recording_coverage(
        store, days=TRADING_DATES, subjects=("998.XX", "999.XX")
    )

    stale = store.read_coverage(DATASET, 2024)
    assert stale is not None
    assert stale.row_count == 6  # unchanged -- the count check has nothing to complain about
    assert stale.subjects == SUBJECTS  # and it still names securities that are no longer there

    readiness = store.assess_readiness(_requirement())
    assert readiness.state == "blocked"
    assert readiness.issues[0].code == "coverage_stale"


def test_the_interrupted_rewrite_fails_closed_not_merely_the_interrupted_first_write(
    tmp_path: Path,
) -> None:
    """`write_partition()` succeeded, `record_coverage()` did not. On a *first* write that
    leaves no coverage row at all, which readiness has always blocked as `coverage_missing`.
    On a re-write the previous row is still there and used to satisfy every check -- the
    verdict was `ready` with no issues, describing content that had been replaced. Both
    halves are asserted here, because only the second one was ever in doubt.
    """
    store = _store(tmp_path / "panel")
    waived = _requirement(
        required_dates=None, required_subjects=None, required_fields=None, max_staleness=None
    )

    # First write, interrupted before coverage: blocked, as it always was.
    first = _batch()
    store.write_partition(DATASET, 2024, panel_column_specs(first), first.to_rows())
    assert [issue.code for issue in store.assess_readiness(waived).issues] == ["coverage_missing"]

    # Now complete it, then interrupt a *re-write* the same way.
    write_panel_batch(store, first, year=2024)
    assert store.assess_readiness(waived).state == "ready"
    _overwrite_partition_without_recording_coverage(
        store, days=(TRADING_DATES[0],), subjects=(SUBJECTS[0],)
    )

    readiness = store.assess_readiness(waived)
    assert readiness.state == "blocked"
    assert [issue.code for issue in readiness.issues] == ["coverage_stale"]
    assert store.read_if_ready(waived, year=2024, columns=["subject"]).is_blocked
    # Recording coverage for the new content clears it -- the fault is a stale record, not a
    # permanently poisoned partition.
    write_panel_batch(store, _batch(days=(TRADING_DATES[0],), subjects=(SUBJECTS[0],)), year=2024)
    assert store.assess_readiness(waived).state == "ready"


# --- a partition file damaged behind the store's back ---------------------------------------


def test_a_partition_truncated_to_zero_bytes_is_reported_rather_than_read(
    tmp_path: Path,
) -> None:
    """`Path.is_file()` is true of a zero-byte file, so readiness used to report `ready` with
    no issues and `read_if_ready()` then raised a bare `duckdb.InvalidInputException` out of a
    method whose whole contract is "blocked or ready, nothing else"."""
    store = _ready_store(tmp_path)
    (store.root / DATASET / "2024" / "data.parquet").write_bytes(b"")

    readiness = store.assess_readiness(
        _requirement(required_dates=None, required_subjects=None, required_fields=None)
    )

    assert readiness.state == "blocked"
    assert [issue.code for issue in readiness.issues] == ["partition_file_unreadable"]
    outcome = store.read_if_ready(
        _requirement(required_dates=None, required_subjects=None, required_fields=None),
        year=2024,
        columns=["close"],
    )
    assert outcome.is_blocked


def test_a_partition_overwritten_with_unrelated_bytes_is_reported(tmp_path: Path) -> None:
    store = _ready_store(tmp_path)
    (store.root / DATASET / "2024" / "data.parquet").write_bytes(b"not a parquet file at all")

    readiness = store.assess_readiness(
        _requirement(required_dates=None, required_subjects=None, required_fields=None)
    )

    assert [issue.code for issue in readiness.issues] == ["partition_file_unreadable"]


def test_a_scan_that_fails_after_a_ready_verdict_surfaces_as_a_panel_storage_error(
    tmp_path: Path,
) -> None:
    """The residual case the eight-byte magic check cannot reach: the file *is* a valid
    Parquet file, just not the one the catalog describes. Detecting that needs a digest of
    the file's bytes on every gate check, which is `V2-P1-012`'s deep pass, not this one's.
    What must not happen is a raw DuckDB exception escaping `read_if_ready()` -- the method
    promises a verdict, so a failure it did not predict is still reported in its own
    vocabulary."""
    store = _ready_store(tmp_path)
    partition = store.root / DATASET / "2024" / "data.parquet"
    with duckdb.connect(":memory:") as swap:
        swap.execute("COPY (SELECT 1 AS unrelated) TO ? (FORMAT PARQUET)", [str(partition)])

    # Readiness still says ready: the catalog row, the coverage record and the file's own
    # Parquet magic are all untouched by a swap performed outside the store.
    waived = _requirement(required_dates=None, required_subjects=None, required_fields=None)
    assert store.assess_readiness(waived).state == "ready"

    with pytest.raises(PanelStorageError, match="passed readiness but could not be read"):
        store.read_if_ready(waived, year=2024, columns=["close"])


# --- a check that was never configured is not a check that passed ---------------------------


def test_a_requirement_cannot_be_built_without_saying_what_it_checks() -> None:
    """The four checks used to default to the most permissive value they had, so the easiest
    requirement to construct was also the one that could not find anything. There is no such
    default now: each is stated, `None` waiving it on the record."""
    with pytest.raises(TypeError, match="required_dates"):
        ReadinessRequirement(dataset=DATASET, as_of=AS_OF, years=(2024,))  # type: ignore[call-arg]


def test_a_year_long_partition_holding_one_trading_day_is_not_ready_by_default(
    tmp_path: Path,
) -> None:
    """The reviewer's I1 reproduction, end to end. A 2024 partition containing a single
    trading day, assessed on 2024-12-31, reported `state=ready issues=[]` under a
    default-constructed requirement -- 364 days stale, with a hole in every other day of the
    year, and nothing to say about either."""
    store = _store(tmp_path / "panel")
    write_panel_batch(store, _batch(days=(TRADING_DATES[0],)), year=2024)
    year_end = datetime(2024, 12, 31, tzinfo=UTC)

    verdict = store.assess_readiness(_requirement(as_of=year_end, max_staleness=timedelta(days=5)))

    assert verdict.state == "blocked"
    assert [issue.code for issue in verdict.issues] == ["date_gap", "stale"]
    assert verdict.issues[0].missing_dates == TRADING_DATES[1:]


def test_waiving_a_check_is_recorded_and_declaring_an_empty_one_blocks(tmp_path: Path) -> None:
    """`None` and `()` are different answers. `None` switches the check off and says so in
    `checks_waived`, which is what lets `V2-P1-012` report "this verdict did not look at
    dates" instead of assuming it did. `()` is a declared expectation that can never find a
    shortfall -- the same vacuous shape `years=()` already blocked on."""
    store = _ready_store(tmp_path)

    waived = store.assess_readiness(
        _requirement(
            required_dates=None, required_subjects=None, required_fields=None, max_staleness=None
        )
    )
    declared_empty = store.assess_readiness(_requirement(required_dates=()))

    assert waived.state == "ready"
    assert waived.checks_waived == (
        "required_dates",
        "required_subjects",
        "required_fields",
        "max_staleness",
    )
    assert store.assess_readiness(_requirement()).checks_waived == ()
    assert declared_empty.state == "blocked"
    assert [issue.code for issue in declared_empty.issues] == ["empty_requirement"]
    assert declared_empty.issues[0].missing_items == ("required_dates",)


def test_a_requirement_whose_container_is_none_by_accident_is_named_not_a_type_error(
    tmp_path: Path,
) -> None:
    """`years=None` is not a waiver of anything -- there is no "check no years" -- so it is a
    malformed requirement, and `_validated_requirement` exists precisely to name malformed
    input. It used to iterate straight into it and raise `TypeError: 'NoneType' object is not
    iterable` from the middle of the validator."""
    store = _ready_store(tmp_path)

    with pytest.raises(PanelStorageError, match="years must be a tuple"):
        store.assess_readiness(_requirement(years=None))
    with pytest.raises(PanelStorageError, match="required_dates must be a tuple or None"):
        store.assess_readiness(_requirement(required_dates=[date(2024, 1, 2)]))


# --- blocked is not empty -------------------------------------------------------------------


def test_blocked_and_ready_but_empty_are_two_distinguishable_returns(tmp_path: Path) -> None:
    """The root cause `V2-P1-013`'s acceptance names. `PanelStore.query()` answers `[]` both
    for "this partition does not exist" and for "the filter matched nothing", so a gate
    built on `query()` alone cannot tell a failed dataset from a genuinely empty one.
    `read_if_ready()` makes the two structurally different values: `rows is None` for a
    blocked dataset, `rows == ()` for a ready one with nothing to show.
    """
    store = _ready_store(tmp_path)
    ready = _requirement()
    blocked = _requirement(years=(2023, 2024), required_dates=None, required_fields=None)

    empty_but_ready = store.read_if_ready(
        ready, year=2024, columns=["close"], filters={"subject": "999999.SZ"}
    )
    unready = store.read_if_ready(blocked, year=2024, columns=["close"])

    # Both would be an empty list through `query()` alone -- that is the confusion.
    assert (
        store.query(DATASET, year=2024, columns=["close"], filters={"subject": "999999.SZ"}) == []
    )
    assert store.query(DATASET, year=2023, columns=["close"]) == []

    assert empty_but_ready.rows == ()
    assert not empty_but_ready.is_blocked
    assert empty_but_ready.readiness.state == "ready"

    assert unready.rows_or_none is None
    assert unready.is_blocked
    assert unready.readiness.state == "blocked"
    assert [issue.code for issue in unready.readiness.issues] == ["partition_missing"]

    assert empty_but_ready != unready


def test_the_one_line_mistake_that_would_re_merge_them_cannot_pass_quietly(
    tmp_path: Path,
) -> None:
    """Two different *values* are only half the fix, and the missing half is what
    `V2-P1-013` would have inherited. With `rows: tuple | None`, `bool(())` and `bool(None)`
    are both `False`, so `if not outcome.rows:` and `outcome.rows or []` -- the ordinary way
    people write this -- silently merge blocked with ready-and-empty while type-checking
    clean under mypy strict. `V2-P1-013` exists because callers forget; a shape that requires
    them to remember is not a fix.

    So the plainly-named accessor is the strict one, and the two-valued shape has to be asked
    for by name.
    """
    store = _ready_store(tmp_path)
    empty_but_ready = store.read_if_ready(
        _requirement(), year=2024, columns=["close"], filters={"subject": "999999.SZ"}
    )
    blocked = store.read_if_ready(
        _requirement(years=(2023, 2024), required_dates=None, required_fields=None),
        year=2024,
        columns=["close"],
    )

    # The shape that used to merge them, on the ready side: still a plain falsy tuple.
    assert not empty_but_ready.rows
    assert (empty_but_ready.rows or ["fallback"]) == ["fallback"]

    # And on the blocked side: the same two expressions now fail loudly instead.
    with pytest.raises(PanelStorageError, match="is blocked, so it has no rows"):
        _ = not blocked.rows
    with pytest.raises(PanelStorageError):
        _ = blocked.rows or []
    with pytest.raises(PanelStorageError):
        _ = len(blocked.rows)

    # The merged shape is still reachable -- deliberately, under a name that says so.
    assert blocked.rows_or_none is None
    assert empty_but_ready.rows_or_none == ()
    # And the error names the codes, so a caller that lets it propagate still learns why.
    with pytest.raises(PanelStorageError, match="partition_missing"):
        _ = blocked.rows


def test_a_ready_dataset_hands_back_its_rows(tmp_path: Path) -> None:
    store = _ready_store(tmp_path)

    outcome = store.read_if_ready(
        _requirement(), year=2024, columns=["subject", "close"], filters={"subject": SUBJECTS[0]}
    )

    assert outcome.rows is not None
    assert len(outcome.rows) == len(TRADING_DATES)
    assert {row[0] for row in outcome.rows} == {SUBJECTS[0]}


def test_a_blocked_read_never_touches_the_partition_file(tmp_path: Path) -> None:
    """Fail-closed in the strong sense: a blocked dataset short-circuits before any scan, so
    a caller cannot accidentally consume half-ingested data by ignoring the verdict."""
    store = _ready_store(tmp_path)
    (store.root / DATASET / "2024" / "data.parquet").unlink()

    outcome = store.read_if_ready(
        _requirement(required_dates=None, required_subjects=None, required_fields=None),
        year=2024,
        columns=["close"],
    )

    assert outcome.rows_or_none is None
    assert [issue.code for issue in outcome.readiness.issues] == ["partition_file_missing"]


# --- boundary handling ----------------------------------------------------------------------


def test_a_store_that_has_never_been_written_blocks_every_requested_year(
    tmp_path: Path,
) -> None:
    """There is not even a catalog file yet. The verdict is still a verdict -- one issue per
    requested year -- not an exception and not a vacuous "ready"."""
    readiness = _store(tmp_path / "panel").assess_readiness(
        _requirement(
            years=(2023, 2024), required_dates=None, required_subjects=None, required_fields=None
        )
    )

    assert readiness.state == "blocked"
    assert [(issue.code, issue.year) for issue in readiness.issues] == [
        ("partition_missing", 2023),
        ("partition_missing", 2024),
    ]


def test_reading_a_year_the_requirement_never_covered_is_refused(tmp_path: Path) -> None:
    """Otherwise a caller could vet 2024 and then read 2023 through the same "checked" call,
    which is the readiness contract defeating itself."""
    store = _ready_store(tmp_path)

    with pytest.raises(PanelStorageError, match="not among the years"):
        store.read_if_ready(_requirement(), year=2023, columns=["close"])


def test_a_requirement_with_a_naive_as_of_is_refused(tmp_path: Path) -> None:
    store = _ready_store(tmp_path)

    with pytest.raises(PanelStorageError, match="timezone-aware"):
        store.assess_readiness(_requirement(as_of=datetime(2024, 1, 5, 0, 0)))


def test_a_datetime_among_the_required_dates_is_refused_rather_than_read_as_a_gap(
    tmp_path: Path,
) -> None:
    """`datetime` subclasses `date`, so a `datetime` here type-checks, never equals any
    observed `date`, and would report a permanent `date_gap` against data that is in fact
    complete. A blocked verdict with an invented cause is worse than a crash: it looks like a
    finding, and `V2-P1-012` would print it as one.
    """
    store = _ready_store(tmp_path)
    disguised = _requirement(required_dates=(datetime(2024, 1, 2, tzinfo=UTC), *TRADING_DATES[1:]))

    with pytest.raises(PanelStorageError, match="not datetimes"):
        store.assess_readiness(disguised)


def test_a_requirement_carrying_malformed_values_is_refused_at_the_boundary(
    tmp_path: Path,
) -> None:
    """`ReadinessRequirement` is a plain carrier too, so the rule lives at the store."""
    store = _ready_store(tmp_path)
    malformed = (
        _requirement(dataset="../escaped"),
        _requirement(dataset=""),
        _requirement(years=("2024",)),
        _requirement(required_subjects=("000001.SZ", "")),
        _requirement(required_fields=("close", 7)),
        _requirement(max_staleness=7),
    )

    for requirement in malformed:
        with pytest.raises(PanelStorageError):
            store.assess_readiness(requirement)
