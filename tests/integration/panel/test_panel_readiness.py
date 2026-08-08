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
from openalpha_cn.panel_ingest import write_panel_batch

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
            years=(2023, 2024), required_dates=(), required_subjects=(), required_fields=()
        )
    )

    assert readiness.state == "blocked"
    assert [(issue.code, issue.year) for issue in readiness.issues] == [("partition_missing", 2023)]


def test_deleting_a_partitions_parquet_file_is_reported(tmp_path: Path) -> None:
    """The injected defect in its most dangerous form: the catalog still advertises the
    partition, so any check that trusted the catalog alone would call this dataset healthy.
    `query()` on such a partition raises out of DuckDB's Parquet reader instead of returning
    data, so a caller that never asked about readiness gets a stack trace rather than a
    diagnosis."""
    store = _ready_store(tmp_path)
    partition = store.root / DATASET / "2024" / "data.parquet"
    assert partition.is_file()
    partition.unlink()

    readiness = store.assess_readiness(
        _requirement(required_dates=(), required_subjects=(), required_fields=())
    )

    assert readiness.state == "blocked"
    assert [(issue.code, issue.year) for issue in readiness.issues] == [
        ("partition_file_missing", 2024)
    ]
    assert str(partition) in readiness.issues[0].detail
    # The claim in this test's docstring, demonstrated rather than asserted in prose: a
    # caller that skips readiness and goes straight to `query()` gets DuckDB's own I/O error.
    with pytest.raises(duckdb.IOException):
        store.query(DATASET, year=2024, columns=["close"])


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
        _requirement(required_dates=(), required_subjects=(), required_fields=())
    )

    assert readiness.state == "blocked"
    assert [issue.code for issue in readiness.issues] == ["coverage_missing"]


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
    blocked = _requirement(years=(2023, 2024), required_dates=(), required_fields=())

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

    assert unready.rows is None
    assert unready.is_blocked
    assert unready.readiness.state == "blocked"
    assert [issue.code for issue in unready.readiness.issues] == ["partition_missing"]

    assert empty_but_ready != unready


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
        _requirement(required_dates=(), required_subjects=(), required_fields=()),
        year=2024,
        columns=["close"],
    )

    assert outcome.rows is None
    assert [issue.code for issue in outcome.readiness.issues] == ["partition_file_missing"]


# --- boundary handling ----------------------------------------------------------------------


def test_a_store_that_has_never_been_written_blocks_every_requested_year(
    tmp_path: Path,
) -> None:
    """There is not even a catalog file yet. The verdict is still a verdict -- one issue per
    requested year -- not an exception and not a vacuous "ready"."""
    readiness = _store(tmp_path / "panel").assess_readiness(
        _requirement(
            years=(2023, 2024), required_dates=(), required_subjects=(), required_fields=()
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
