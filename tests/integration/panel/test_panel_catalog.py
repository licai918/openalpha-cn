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
    ReadinessRequirement,
    RevisionCoverage,
)
from openalpha_cn.panel.store import ColumnSpec, PanelStorageError, PanelStore

_COVERAGE_TABLES = (
    "panel_partition_subjects",
    "panel_partition_fields",
    "panel_partition_dates",
    "panel_partition_revisions",
    "panel_partition_coverage",
    "panel_catalog_meta",
)

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


def _requirement(**overrides: object) -> ReadinessRequirement:
    defaults: dict[str, object] = {
        "dataset": DATASET,
        "as_of": AS_OF,
        "years": (2024,),
        "required_dates": None,
        "required_subjects": None,
        "required_fields": None,
        "max_staleness": None,
    }
    defaults.update(overrides)
    return ReadinessRequirement(**defaults)  # type: ignore[arg-type]


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
    the catalog claim knowledge of data that is not there.

    The record below is internally flawless -- a 2023 partition whose dates are 2023 dates --
    so the only thing that can refuse it is the cross-check against `panel_partitions`.
    """
    store = _written_store(tmp_path / "panel")
    unregistered = _coverage(
        year=2023, dates=(DateCoverage(event_date=date(2023, 12, 29), row_count=2),)
    )

    with pytest.raises(PanelStorageError, match="no partition registered"):
        store.record_coverage(unregistered)


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
    fail-closed. The write is one transaction, and this proves the transaction *boundary* is
    there: deleting `_write_coverage`'s `BEGIN TRANSACTION` reddens 45 tests, this one
    included.

    It does not isolate the explicit `ROLLBACK` in that function's `except` clause -- deleting
    only that line leaves this test green, because `with duckdb.connect(...)` rolls an open
    transaction back when it closes. The `ROLLBACK` is the honest local statement of intent
    (the rollback should not depend on which caller happens to close the connection first),
    not something this test can distinguish.

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


def test_the_stamp_guards_every_method_that_opens_the_catalog_not_only_half_of_them(
    tmp_path: Path,
) -> None:
    """The stamp's own error says "refusing to touch it". Three of the seven public entry
    points used to touch it anyway; the P1 review found a fourth. `query()` is the pointed
    read case: it is the *only* method that reads `panel_partitions.relative_path` and opens
    the file that column names, so an unknown schema -- the one situation where that column
    might mean something else -- is exactly when it must not be trusted. It returned
    `[('000001.SZ', 10.0)]` from a v99 catalog without a word.

    The fourth was `write_partition()`, and it is the reason the word "unconditionally" is no
    longer in this docstring: the claim was not true when it was written. `write_partition()`
    probes the catalog for idempotency through `_lookup`, which opened it `read_only=True` and
    asked no version question, so against `676cba3` a v99 catalog produced

        write_partition (idempotent)     : SILENTLY SUCCEEDED -> 2 rows
        write_partition (non-idempotent) : raised PanelStorageError
        parquet on disk: before=[('A', 1.0), ('B', 2.0)] after=[('A', 99.0), ('B', 98.0), ...]

    -- the idempotent branch reporting success against a catalog it must not read, and the
    non-idempotent branch *replacing the partition file* before the refusal fired from
    `_ensure_catalog_schema`, several steps too late. `_lookup` checks the stamp now, which
    puts the refusal ahead of the Parquet `COPY`, so both branches refuse and nothing on disk
    moves. The two `write_partition` entries below are both of those branches, and the
    before/after byte comparison is what proves the second one.
    """
    store = _written_store(tmp_path / "panel")
    store.record_coverage(_coverage())
    target = store.root / DATASET / "2024" / "data.parquet"
    before = target.read_bytes()
    with duckdb.connect(str(store.catalog_path)) as connection:
        connection.execute(
            "UPDATE panel_catalog_meta SET value = 'panel-catalog/v99' WHERE key = 'schema_version'"
        )

    other_rows: tuple[tuple[object, ...], ...] = (
        ("000001.SZ", "2024-01-02", 99.5),
        ("000002.SZ", "2024-01-02", 88.5),
        ("000003.SZ", "2024-01-02", 77.5),
    )
    refusals = (
        lambda: store.read_coverage(DATASET, 2024),
        lambda: store.registered_years(DATASET),
        lambda: store.query(DATASET, year=2024, columns=["ts_code"]),
        lambda: store.profile_query(DATASET, year=2024, columns=["ts_code"]),
        lambda: store.assess_readiness(_requirement()),
        lambda: store.read_if_ready(_requirement(), year=2024, columns=["ts_code"]),
        lambda: store.record_coverage(_coverage()),
        # Both `write_partition` branches: byte-identical content (the idempotent path, which
        # used to return success) and different content (which used to overwrite first).
        lambda: store.write_partition(DATASET, 2024, _COLUMNS, _ROWS),
        lambda: store.write_partition(DATASET, 2024, _COLUMNS, other_rows),
    )
    for call in refusals:
        with pytest.raises(PanelStorageError, match="panel-catalog/v99"):
            call()

    assert target.read_bytes() == before, "a refused write must not have touched the partition"
    assert not list(target.parent.glob("*.tmp")), "a refused write must not leave a staged temp"


def test_a_v1_catalog_is_migrated_forward_rather_than_refused_or_rebuilt(
    tmp_path: Path,
) -> None:
    """The `v1 -> v2` migration, on a catalog that really is v1 on disk: the stamp says v1 and
    `panel_partition_coverage` has no `partition_content_hash` column.

    Not a `DROP` and re-derive, because the catalog is not a rebuildable cache -- `provider_id`,
    `as_of`, `fetched_at` and `batch_digest` exist nowhere else -- so the v1 row's provenance
    has to survive the upgrade. And not readable as-is either: an existing v1 row cannot say
    which write it was recorded against, so it blocks (`coverage_stale`) until re-recorded,
    rather than being taken on trust.
    """
    root = tmp_path / "panel"
    store = _written_store(root)
    store.record_coverage(_coverage())
    with duckdb.connect(str(store.catalog_path)) as connection:
        connection.execute(
            "ALTER TABLE panel_partition_coverage DROP COLUMN partition_content_hash"
        )
        connection.execute(
            "UPDATE panel_catalog_meta SET value = 'panel-catalog/v1' WHERE key = 'schema_version'"
        )

    legacy = _store(root)
    # Read-only paths work against v1 untouched -- they cannot ALTER anything.
    stored = legacy.read_coverage(DATASET, 2024)
    assert stored is not None
    assert stored.provider_id == "tushare"  # provenance survived
    assert stored.partition_content_hash is None  # but which write it describes is unknown
    assert [issue.code for issue in legacy.assess_readiness(_requirement()).issues] == [
        "coverage_stale"
    ]
    with duckdb.connect(str(legacy.catalog_path), read_only=True) as connection:
        row = connection.execute(
            "SELECT value FROM panel_catalog_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row is not None and row[0] == "panel-catalog/v1"

    # The first write migrates it forward and re-stamps it.
    legacy.record_coverage(_coverage())

    with duckdb.connect(str(legacy.catalog_path), read_only=True) as connection:
        row = connection.execute(
            "SELECT value FROM panel_catalog_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row is not None and row[0] == PANEL_CATALOG_SCHEMA_VERSION
    assert legacy.assess_readiness(_requirement()).state == "ready"


def test_an_unknown_stamp_is_refused_before_any_table_is_created(tmp_path: Path) -> None:
    """ "Refusing to touch it" was not literally true: the DDL pass ran first, so five dropped
    tables were silently rebuilt before the refusal fired. The version check now runs ahead of
    every `CREATE TABLE`, which it can, because a catalog with no `panel_catalog_meta` is v1
    by definition and needs no table to say so."""
    store = _written_store(tmp_path / "panel")
    with duckdb.connect(str(store.catalog_path)) as connection:
        for table in _COVERAGE_TABLES:
            connection.execute(f"DROP TABLE {table}")
        connection.execute(
            "CREATE TABLE panel_catalog_meta (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)"
        )
        connection.execute(
            "INSERT INTO panel_catalog_meta VALUES ('schema_version', 'panel-catalog/v99')"
        )

    with pytest.raises(PanelStorageError, match="panel-catalog/v99"):
        store.record_coverage(_coverage())

    with duckdb.connect(str(store.catalog_path), read_only=True) as connection:
        rebuilt = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN "
            "('panel_partition_coverage', 'panel_partition_subjects', 'panel_partition_fields', "
            "'panel_partition_dates', 'panel_partition_revisions')"
        ).fetchall()
    assert rebuilt == [], f"refused, yet these tables were created anyway: {rebuilt}"


# --- what `record_coverage` binds the record to ---------------------------------------------


def test_the_stored_record_names_the_partition_write_it_describes(tmp_path: Path) -> None:
    """`partition_content_hash` is a fact about the storing, not about the batch: whatever the
    caller put there is replaced with the registered partition's own hash."""
    store = _written_store(tmp_path / "panel")

    stored = store.record_coverage(_coverage(partition_content_hash="sha256:whatever-i-like"))

    with duckdb.connect(str(store.catalog_path), read_only=True) as connection:
        row = connection.execute(
            "SELECT content_hash FROM panel_partitions WHERE dataset = ? AND year = ?",
            [DATASET, 2024],
        ).fetchone()
    assert row is not None
    assert stored.partition_content_hash == row[0]
    read_back = store.read_coverage(DATASET, 2024)
    assert read_back is not None and read_back.partition_content_hash == row[0]


def test_a_date_census_that_falls_outside_the_partitions_own_year_is_refused(
    tmp_path: Path,
) -> None:
    """The twelfth internal-consistency rule, and the one that was missing: a batch of 2019
    rows written with `year=2024` produced a coverage record whose every date was in 2019 and
    whose partition key said 2024. `required_dates` naming those 2019 days against
    `years=(2024,)` then reported `ready`, because the pooled date check pools the dates it is
    given and never asks which year they came from."""
    store = _written_store(tmp_path / "panel")
    misfiled = _coverage(
        dates=(DateCoverage(event_date=date(2019, 3, 4), row_count=2),),
    )

    with pytest.raises(PanelStorageError, match="must fall inside the partition's own year"):
        store.record_coverage(misfiled)


def test_a_read_back_timestamp_is_utc_rather_than_the_sessions_local_zone(
    tmp_path: Path,
) -> None:
    """DuckDB hands a `TIMESTAMPTZ` back in the session's zone, so the same stored instant
    reads as `-04:00` on one machine and `+08:00` on another. Every comparison in this module
    is correct on either, but `V2-P1-016` serialises these onto the wire, and an offset that
    depends on which host answered is not a wire format."""
    store = _written_store(tmp_path / "panel")
    store.record_coverage(_coverage())

    stored = store.read_coverage(DATASET, 2024)

    assert stored is not None
    for value in (
        stored.as_of,
        stored.fetched_at,
        stored.last_event_time,
        stored.max_available_time,
        stored.recorded_at,
    ):
        assert value is not None
        assert value.tzinfo is not None
        assert value.utcoffset() == timedelta(0), f"{value!r} is not expressed in UTC"
    # Same instants, either way -- the normalisation is a representation choice, not a shift.
    assert stored.last_event_time == LAST_EVENT
    assert stored.recorded_at == FROZEN


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


# --- the *second* stamp: `panel-batch`, which had no door until the P1 review ---------------


def test_a_coverage_record_stamped_with_an_unknown_batch_contract_is_refused_on_write(
    tmp_path: Path,
) -> None:
    """`domain/panel_batch.py` claimed "a future `panel-batch/v2` is detectable rather than
    silently compatible" because `schema_version` is carried and hashed. It was not.

    Measured against `676cba3`: `record_coverage` accepted a record stamped
    `panel-batch/v99` -- `_validated_coverage` asked only that it be non-empty text -- and
    `assess_readiness` then returned `ready` with `issues == []`. Two version stamps exist in
    this system, `panel-catalog` and `panel-batch`; two docstrings said both were gated; one
    was.

    Refused rather than reported as a readiness issue, for the reason the catalog stamp is:
    the stamp says what the record's other columns *mean*, so a verdict computed from them
    would be a guess dressed as an answer.
    """
    store = _written_store(tmp_path / "panel")

    with pytest.raises(PanelStorageError, match="panel-batch/v99"):
        store.record_coverage(_coverage(schema_version="panel-batch/v99"))

    assert store.read_coverage(DATASET, 2024) is None


def test_a_coverage_row_another_build_stamped_with_an_unknown_contract_is_refused_on_read(
    tmp_path: Path,
) -> None:
    """The half the write-side check cannot cover, and the one the claim was really about: a
    *newer* build wrote the row. The two stamps version independently -- a change to the batch
    contract alone leaves the catalog schema at `panel-catalog/v2` -- so
    `_check_catalog_schema_version` passes the file and every row inside it would be read as
    v1. This is the exact reproduction the review ran, with the row inserted through DuckDB
    rather than through `record_coverage`.
    """
    store = _written_store(tmp_path / "panel")
    store.record_coverage(_coverage())
    with duckdb.connect(str(store.catalog_path)) as connection:
        connection.execute("UPDATE panel_partition_coverage SET schema_version = 'panel-batch/v99'")

    # The catalog's own stamp is untouched and still readable, which is the whole point.
    assert store.registered_years(DATASET) == (2024,)

    for call in (
        lambda: store.read_coverage(DATASET, 2024),
        lambda: store.assess_readiness(_requirement()),
        lambda: store.read_if_ready(_requirement(), year=2024, columns=["ts_code"]),
    ):
        with pytest.raises(PanelStorageError, match="panel-batch/v99"):
            call()
