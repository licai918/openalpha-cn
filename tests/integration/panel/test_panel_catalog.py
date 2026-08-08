"""The data catalog itself (`V2-P1-003`): what it records, and what it refuses to record.

`V2-P1-001` left the catalog holding one table, `panel_partitions`, which answers exactly
one of PRD Story S8's five questions ("where is the file, and how many rows") and answers
*none* of subjects / fields / date coverage / revision coverage / freshness. These tests pin
the coverage tables that close that gap, the injected clock that makes every catalog
timestamp reproducible, the schema-version stamp that makes a future DuckDB-side change
detectable, and the boundary validation that does not trust a `PartitionCoverage` merely
because it is nominally one.

Panel data is generated at test time, never checked in: `.parquet` and `.duckdb` are both
`scripts/verify_publication.py` blocked suffixes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from openalpha_cn.panel.catalog import (
    PANEL_CATALOG_SCHEMA_VERSION,
    DateCoverage,
    FieldCoverage,
    PartitionCoverage,
    RevisionCoverage,
)
from openalpha_cn.panel.store import ColumnSpec, PanelStorageError, PanelStore

FROZEN = datetime(2024, 6, 28, 11, 22, 33, tzinfo=UTC)
AS_OF = datetime(2024, 1, 4, 12, 0, tzinfo=UTC)
LAST_EVENT = datetime(2024, 1, 4, 7, 0, tzinfo=UTC)
LAST_AVAILABLE = datetime(2024, 1, 4, 8, 30, tzinfo=UTC)
DATASET = "prices_daily"

_COLUMNS = (
    ColumnSpec("ts_code", "VARCHAR"),
    ColumnSpec("trade_date", "VARCHAR"),
    ColumnSpec("close", "DOUBLE"),
)
_ROWS: tuple[tuple[object, ...], ...] = (
    ("000001.SZ", "2024-01-02", 10.5),
    ("000002.SZ", "2024-01-02", 22.5),
)


def _frozen_clock() -> datetime:
    return FROZEN


def _store(root: Path) -> PanelStore:
    return PanelStore(root, clock=_frozen_clock)


def _coverage(**overrides: object) -> PartitionCoverage:
    defaults: dict[str, object] = {
        "dataset": DATASET,
        "year": 2024,
        "provider_id": "tushare",
        "kind": "daily",
        "schema_version": "panel-batch/v1",
        "batch_digest": "sha256:" + "1" * 64,
        "as_of": AS_OF,
        "fetched_at": AS_OF,
        "row_count": 2,
        "date_timezone": "Asia/Shanghai",
        "last_event_time": LAST_EVENT,
        "max_available_time": LAST_AVAILABLE,
        "revised_row_count": 0,
        "subjects": ("000001.SZ", "000002.SZ"),
        "fields": (FieldCoverage(name="close", kind="float"),),
        "dates": (DateCoverage(event_date=date(2024, 1, 2), row_count=2),),
        "revisions": (),
    }
    defaults.update(overrides)
    return PartitionCoverage(**defaults)  # type: ignore[arg-type]


def _written_store(root: Path) -> PanelStore:
    store = _store(root)
    store.write_partition(DATASET, 2024, _COLUMNS, _ROWS)
    return store


# --- what the catalog now records -----------------------------------------------------------


def test_the_catalog_round_trips_all_five_story_s8_dimensions(tmp_path: Path) -> None:
    """subjects / fields / date coverage / revision coverage / freshness, each read back as
    the thing it is rather than inferred from a write timestamp."""
    store = _written_store(tmp_path / "panel")
    coverage = _coverage(
        subjects=("000002.SZ", "000001.SZ"),
        fields=(
            FieldCoverage(name="close", kind="float"),
            FieldCoverage(name="vol", kind="integer"),
        ),
        dates=(
            DateCoverage(event_date=date(2024, 1, 3), row_count=1),
            DateCoverage(event_date=date(2024, 1, 2), row_count=1),
        ),
        revisions=(
            RevisionCoverage(label="0", row_count=1),
            RevisionCoverage(label="1", row_count=1),
        ),
        revised_row_count=1,
    )

    store.record_coverage(coverage)
    stored = store.read_coverage(DATASET, 2024)

    assert stored is not None
    # subjects: which, not just how many -- and in a stable order, so a diff of two catalog
    # dumps is a diff of the data rather than of the write order.
    assert stored.subjects == ("000001.SZ", "000002.SZ")
    assert stored.subject_count == 2
    # fields: which columns, and what each one holds.
    assert [(field.name, field.kind) for field in stored.fields] == [
        ("close", "float"),
        ("vol", "integer"),
    ]
    assert stored.field_count == 2
    # date coverage: the real per-date census, from which a hole is visible.
    assert [(day.event_date, day.row_count) for day in stored.dates] == [
        (date(2024, 1, 2), 1),
        (date(2024, 1, 3), 1),
    ]
    assert stored.first_event_date == date(2024, 1, 2)
    assert stored.last_event_date == date(2024, 1, 3)
    # revision coverage: both facets -- the clock-derived count and the label census.
    assert stored.revised_row_count == 1
    assert [(item.label, item.row_count) for item in stored.revisions] == [("0", 1), ("1", 1)]
    # freshness: the event clock ("what date does this data reach"), not the write clock.
    assert stored.last_event_time == LAST_EVENT
    assert stored.max_available_time == LAST_AVAILABLE
    assert stored.recorded_at == FROZEN


def test_freshness_and_the_write_timestamp_are_two_different_facts(tmp_path: Path) -> None:
    """A partition backfilled today can hold last month's data. `written_at`/`recorded_at`
    answer "when did this land"; `last_event_time` answers S8's actual question, "how far
    does this data reach". Recording only the former is what made the pre-task catalog
    unable to report freshness at all."""
    store = _written_store(tmp_path / "panel")
    store.record_coverage(_coverage())

    stored = store.read_coverage(DATASET, 2024)

    assert stored is not None
    assert stored.recorded_at == FROZEN
    assert stored.last_event_time == LAST_EVENT
    assert stored.recorded_at != stored.last_event_time


def test_every_catalog_timestamp_comes_from_the_injected_clock(tmp_path: Path) -> None:
    """`panel_partitions.written_at` used to be DuckDB's own `now()` -- a bare wall-clock
    read inside the database, unfreezable by any test, in a codebase that injects a clock
    everywhere else. Both catalog tables now take their timestamp from the store's injected
    clock, so a frozen clock produces a byte-reproducible catalog."""
    store = _written_store(tmp_path / "panel")
    store.record_coverage(_coverage())

    with duckdb.connect(str(store.catalog_path), read_only=True) as connection:
        written_at = connection.execute(
            "SELECT written_at FROM panel_partitions WHERE dataset = ? AND year = ?",
            [DATASET, 2024],
        ).fetchone()
        recorded_at = connection.execute(
            "SELECT recorded_at FROM panel_partition_coverage WHERE dataset = ? AND year = ?",
            [DATASET, 2024],
        ).fetchone()

    assert written_at is not None and written_at[0] == FROZEN
    assert recorded_at is not None and recorded_at[0] == FROZEN


def test_a_clock_returning_a_naive_datetime_is_refused_at_the_write(tmp_path: Path) -> None:
    """A naive datetime bound to a `TIMESTAMPTZ` column is silently interpreted in the
    machine's local zone, so the catalog would record a different instant depending on where
    it ran. Refused rather than normalised: the caller's clock is wrong, not the value."""
    store = PanelStore(tmp_path / "panel", clock=lambda: datetime(2024, 6, 28, 11, 22, 33))

    with pytest.raises(PanelStorageError, match="timezone-aware"):
        store.write_partition(DATASET, 2024, _COLUMNS, _ROWS)


def test_recording_coverage_twice_replaces_it_rather_than_accumulating(tmp_path: Path) -> None:
    store = _written_store(tmp_path / "panel")
    store.record_coverage(
        _coverage(dates=(DateCoverage(event_date=date(2024, 1, 2), row_count=2),))
    )

    store.record_coverage(
        _coverage(
            dates=(
                DateCoverage(event_date=date(2024, 1, 3), row_count=1),
                DateCoverage(event_date=date(2024, 1, 4), row_count=1),
            )
        )
    )

    stored = store.read_coverage(DATASET, 2024)
    assert stored is not None
    assert [day.event_date for day in stored.dates] == [date(2024, 1, 3), date(2024, 1, 4)]
    with duckdb.connect(str(store.catalog_path), read_only=True) as connection:
        rows = connection.execute("SELECT COUNT(*) FROM panel_partition_coverage").fetchone()
        dates = connection.execute("SELECT COUNT(*) FROM panel_partition_dates").fetchone()
    assert rows is not None and rows[0] == 1
    assert dates is not None and dates[0] == 2


def test_reading_coverage_for_a_partition_that_was_never_profiled_returns_none(
    tmp_path: Path,
) -> None:
    store = _written_store(tmp_path / "panel")

    assert store.read_coverage(DATASET, 2024) is None
    assert store.read_coverage("balancesheet", 2024) is None


def test_reading_coverage_from_a_store_with_no_catalog_file_returns_none(tmp_path: Path) -> None:
    assert _store(tmp_path / "panel").read_coverage(DATASET, 2024) is None


# --- what the catalog refuses ---------------------------------------------------------------


def test_coverage_for_an_unregistered_partition_is_refused(tmp_path: Path) -> None:
    """Coverage describes a partition; a coverage row with no partition behind it would make
    the catalog claim knowledge of data that is not there."""
    store = _written_store(tmp_path / "panel")

    with pytest.raises(PanelStorageError, match="no partition registered"):
        store.record_coverage(_coverage(year=2023))


def test_coverage_whose_row_count_disagrees_with_the_partition_is_refused(
    tmp_path: Path,
) -> None:
    store = _written_store(tmp_path / "panel")
    # Internally consistent (its own date census accounts for all 99 rows) but describing a
    # partition the catalog says holds 2 -- so only the cross-check against `panel_partitions`
    # can catch it.
    inconsistent = _coverage(
        row_count=99, dates=(DateCoverage(event_date=date(2024, 1, 2), row_count=99),)
    )

    with pytest.raises(PanelStorageError, match="row_count"):
        store.record_coverage(inconsistent)


def test_the_store_revalidates_coverage_instead_of_trusting_the_dataclass(
    tmp_path: Path,
) -> None:
    """`PartitionCoverage` is a plain carrier with no validating `__post_init__`, and this is
    deliberate: the previous task established that a nominal type is not a boundary (a
    duck-typed stand-in and a subclass that overrides `__post_init__` both defeated one).
    So the rule lives once, at the store's own boundary, where the value is actually used --
    and every one of these malformed carriers is constructible.
    """
    store = _written_store(tmp_path / "panel")
    one_day = DateCoverage(event_date=date(2024, 1, 2), row_count=1)
    malformed: tuple[PartitionCoverage, ...] = (
        # the dataset name -- the value that becomes a directory under the store's root
        _coverage(dataset="../escaped"),
        _coverage(dataset=""),
        _coverage(year="2024"),
        # the subject census
        _coverage(subjects=()),
        _coverage(subjects=("000001.SZ", "")),
        _coverage(subjects=("000001.SZ", 7)),
        _coverage(subjects=("000001.SZ", "000001.SZ")),
        # the field census
        _coverage(fields=()),
        _coverage(fields=(FieldCoverage(name="close", kind=""),)),
        _coverage(
            fields=(
                FieldCoverage(name="close", kind="float"),
                FieldCoverage(name="close", kind="integer"),
            )
        ),
        # the date census
        _coverage(dates=()),
        _coverage(dates=(DateCoverage(event_date=date(2024, 1, 2), row_count=-1),)),
        _coverage(dates=(DateCoverage(event_date="2024-01-02", row_count=2),)),
        _coverage(dates=(DateCoverage(event_date=datetime(2024, 1, 2, tzinfo=UTC), row_count=2),)),
        _coverage(dates=(one_day, one_day)),
        _coverage(dates=(DateCoverage(event_date=date(2024, 1, 2), row_count=3),)),
        # the revision census
        _coverage(revisions=(RevisionCoverage(label="", row_count=1),)),
        _coverage(revisions=(RevisionCoverage(label="0", row_count=0),)),
        _coverage(
            revisions=(
                RevisionCoverage(label="0", row_count=1),
                RevisionCoverage(label="0", row_count=1),
            )
        ),
        _coverage(revisions=(RevisionCoverage(label="0", row_count=1),)),
        # the clocks
        _coverage(as_of=datetime(2024, 1, 4, 12, 0)),
        _coverage(fetched_at=datetime(2024, 1, 4, 12, 0)),
        _coverage(last_event_time=datetime(2024, 1, 4, 7, 0)),
        _coverage(max_available_time=datetime(2024, 1, 4, 8, 30)),
        # the timezone label
        _coverage(date_timezone="Not/AZone"),
        _coverage(date_timezone="../../../etc/localtime"),
        _coverage(date_timezone=""),
        _coverage(date_timezone=8),
        # the counts
        _coverage(row_count=0),
        _coverage(revised_row_count=3),
        _coverage(revised_row_count=-1),
        # the provenance strings
        _coverage(batch_digest=""),
        _coverage(provider_id=" tushare "),
        _coverage(kind="d" * 2049),
    )

    for coverage in malformed:
        with pytest.raises(PanelStorageError):
            store.record_coverage(coverage)

    assert store.read_coverage(DATASET, 2024) is None


def test_a_coverage_write_that_fails_part_way_leaves_the_previous_record_intact(
    tmp_path: Path,
) -> None:
    """Replacing a coverage record clears four census tables before refilling them, so a
    failure between the two would leave a coverage row advertising a partition with (say) no
    subjects -- a lie that reads as data rather than as a missing record, and therefore *not*
    fail-closed. The write is one transaction; this proves the rollback.

    The injected fault is a census table whose column type no longer matches what the store
    writes -- what a half-finished hand migration of the catalog looks like. `CREATE TABLE IF
    NOT EXISTS` leaves it alone, the `DELETE` succeeds, and the `INSERT` is what fails.
    """
    store = _written_store(tmp_path / "panel")
    store.record_coverage(_coverage())
    original_digest = "sha256:" + "1" * 64

    with duckdb.connect(str(store.catalog_path)) as connection:
        connection.execute("DROP TABLE panel_partition_subjects")
        connection.execute(
            "CREATE TABLE panel_partition_subjects (dataset VARCHAR NOT NULL, "
            "year INTEGER NOT NULL, subject INTEGER NOT NULL, PRIMARY KEY (dataset, year, subject))"
        )

    with pytest.raises(duckdb.Error):
        store.record_coverage(_coverage(subjects=("000009.SZ",), batch_digest="sha256:" + "2" * 64))

    with duckdb.connect(str(store.catalog_path), read_only=True) as connection:
        digest = connection.execute(
            "SELECT batch_digest FROM panel_partition_coverage WHERE dataset = ? AND year = ?",
            [DATASET, 2024],
        ).fetchone()
        dates = connection.execute("SELECT COUNT(*) FROM panel_partition_dates").fetchone()
        fields = connection.execute("SELECT COUNT(*) FROM panel_partition_fields").fetchone()

    assert digest is not None and digest[0] == original_digest
    assert dates is not None and dates[0] == 1
    assert fields is not None and fields[0] == 1


def test_recording_coverage_before_any_partition_exists_is_refused(tmp_path: Path) -> None:
    """The catalog file itself does not exist yet, so there is nothing to describe -- and
    recording coverage must not conjure a catalog into being around an absent partition."""
    store = _store(tmp_path / "panel")

    with pytest.raises(PanelStorageError, match="no partition registered"):
        store.record_coverage(_coverage())

    assert not store.catalog_path.exists()


# --- schema evolution on the DuckDB side ----------------------------------------------------


def test_the_catalog_stamps_its_own_schema_version(tmp_path: Path) -> None:
    """`storage/migrations.py` governs the SQLite `state.sqlite3` and nothing else -- it is
    keyed on `PRAGMA user_version`, which DuckDB does not have. The panel catalog carries its
    own stamp instead, so a future breaking change is detectable rather than a silent
    misread."""
    store = _written_store(tmp_path / "panel")

    with duckdb.connect(str(store.catalog_path), read_only=True) as connection:
        row = connection.execute(
            "SELECT value FROM panel_catalog_meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row is not None and row[0] == PANEL_CATALOG_SCHEMA_VERSION


def test_a_catalog_stamped_by_a_newer_version_of_this_code_is_refused(tmp_path: Path) -> None:
    store = _written_store(tmp_path / "panel")
    with duckdb.connect(str(store.catalog_path)) as connection:
        connection.execute(
            "UPDATE panel_catalog_meta SET value = 'panel-catalog/v99' WHERE key = 'schema_version'"
        )

    with pytest.raises(PanelStorageError, match="panel-catalog/v99"):
        store.record_coverage(_coverage())
    with pytest.raises(PanelStorageError, match="panel-catalog/v99"):
        store.read_coverage(DATASET, 2024)


def test_a_catalog_written_before_the_coverage_tables_existed_still_works(
    tmp_path: Path,
) -> None:
    """The migration answer for partitions that predate this task: the coverage tables are
    purely additive, so an old catalog needs no data migration at all. Its partitions stay
    queryable, they simply have no coverage row -- which readiness reports as `coverage_missing`
    rather than mistaking for "nothing wrong". Recording coverage later brings them up to
    date in place.
    """
    root = tmp_path / "panel"
    store = _written_store(root)
    with duckdb.connect(str(store.catalog_path)) as connection:
        for table in (
            "panel_partition_subjects",
            "panel_partition_fields",
            "panel_partition_dates",
            "panel_partition_revisions",
            "panel_partition_coverage",
            "panel_catalog_meta",
        ):
            connection.execute(f"DROP TABLE {table}")

    legacy = _store(root)
    assert legacy.query(DATASET, year=2024, columns=["ts_code"]) != []
    assert legacy.read_coverage(DATASET, 2024) is None

    legacy.record_coverage(_coverage())
    assert legacy.read_coverage(DATASET, 2024) is not None


def test_writing_a_partition_leaves_an_existing_coverage_row_addressable(
    tmp_path: Path,
) -> None:
    """Coverage is keyed on `(dataset, year)` exactly as the partition is, so a partition
    overwrite never orphans its coverage row -- the next `record_coverage` replaces it."""
    store = _written_store(tmp_path / "panel")
    store.record_coverage(_coverage())

    store.write_partition(DATASET, 2024, _COLUMNS, (*_ROWS[:1], ("000003.SZ", "2024-01-02", 33.5)))
    refreshed = replace(_coverage(), subjects=("000001.SZ", "000003.SZ"))
    store.record_coverage(refreshed)

    stored = store.read_coverage(DATASET, 2024)
    assert stored is not None
    assert stored.subjects == ("000001.SZ", "000003.SZ")


def test_registered_years_lists_only_the_requested_dataset(tmp_path: Path) -> None:
    store = _store(tmp_path / "panel")
    store.write_partition(DATASET, 2024, _COLUMNS, _ROWS)
    store.write_partition(DATASET, 2023, _COLUMNS, _ROWS)
    store.write_partition("balancesheet", 2022, _COLUMNS, _ROWS)

    assert store.registered_years(DATASET) == (2023, 2024)
    assert store.registered_years("balancesheet") == (2022,)
    assert store.registered_years("never_written") == ()


def test_registered_years_on_a_store_that_has_never_been_written_is_empty(
    tmp_path: Path,
) -> None:
    """Two distinct "nothing here" paths: no catalog file at all, and a catalog file with no
    `panel_partitions` table (which is what a hand-built or half-initialised database looks
    like). Neither is an error -- both mean the dataset has no partitions."""
    assert _store(tmp_path / "fresh").registered_years(DATASET) == ()

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    with duckdb.connect(str(empty_root / "catalog.duckdb")) as connection:
        connection.execute("CREATE TABLE unrelated (x INTEGER)")

    assert _store(empty_root).registered_years(DATASET) == ()


def test_registered_years_validates_the_dataset_name(tmp_path: Path) -> None:
    store = _store(tmp_path / "panel")

    with pytest.raises(PanelStorageError):
        store.registered_years("../escaped")


def test_a_partition_recorded_under_a_frozen_clock_is_byte_identical_on_a_replay(
    tmp_path: Path,
) -> None:
    """Two independent stores, same frozen clock, same input -> the same catalog contents.
    This is what the DuckDB-side `now()` made impossible: the two runs would have differed in
    a column no caller could control, so no test could ever assert on it.
    """
    first = _written_store(tmp_path / "one")
    second = _written_store(tmp_path / "two")
    first.record_coverage(_coverage())
    second.record_coverage(_coverage())

    assert first.read_coverage(DATASET, 2024) == second.read_coverage(DATASET, 2024)


def test_the_default_clock_is_the_real_utc_wall_clock(tmp_path: Path) -> None:
    """The injected clock is a seam, not a behaviour change: an un-injected `PanelStore`
    still stamps the catalog with the actual current UTC instant."""
    before = datetime.now(UTC)
    store = PanelStore(tmp_path / "panel")
    store.write_partition(DATASET, 2024, _COLUMNS, _ROWS)
    after = datetime.now(UTC)

    with duckdb.connect(str(store.catalog_path), read_only=True) as connection:
        row = connection.execute("SELECT written_at FROM panel_partitions").fetchone()

    assert row is not None
    assert before - timedelta(seconds=1) <= row[0] <= after + timedelta(seconds=1)
