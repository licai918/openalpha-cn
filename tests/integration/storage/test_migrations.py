"""Tests for the SQLite schema-migration engine (V2-P0B-004).

These are integration tests, not unit tests: the property under test is the engine's
interaction with a *real* SQLite file -- transactional DDL, `PRAGMA user_version`, and the
SQLite backup API have no meaningful in-memory double.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.runtime.memory import MemoryEntry
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.migrations import (
    BASELINE_VERSION,
    DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
    MIGRATIONS,
    Migration,
    MigrationFailedError,
    read_status,
    run_migrations,
)
from openalpha_cn.storage.sqlite import SQLiteRunRepository

NOW = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
DIGEST = "a" * 64


def _clock() -> datetime:
    return NOW


def _manifest(run_id: str = "run_v1_shape") -> RunManifest:
    return RunManifest(
        run_id=run_id,
        mode="replay",
        as_of=NOW,
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
        started_at=NOW,
        status="running",
    )


def _decision(run_id: str = "run_v1_shape") -> DecisionLedger:
    return DecisionLedger(
        run_id=run_id,
        created_at=NOW,
        routing_path=("risk-gate",),
        risk_decision="block",
        final_action="abstain",
        code_commit="0123456789abcdef",
    )


def _memory_entry(run_id: str, decision_id: str) -> MemoryEntry:
    return MemoryEntry(
        run_id=run_id,
        subject="000001.SZ",
        created_at=NOW,
        decision_id=decision_id,
        signal_id="sig_v1_shape",
        summary="v1-shaped memory entry written before the migration system existed.",
    )


def _build_v1_shaped_database(path: Path) -> tuple[RunManifest, DecisionLedger, MemoryEntry]:
    """Populate `path` using only the pre-existing stores -- exactly like a real v1 install."""
    repository = SQLiteRunRepository(path)
    memory = SQLiteResearchMemory(path)
    run = _manifest()
    repository.append_run(run)
    decision = _decision()
    repository.append_decision(decision)
    entry = _memory_entry(run_id=run.run_id, decision_id=decision.decision_id)
    memory.append(entry)
    return run, decision, entry


def _failing_apply(connection: sqlite3.Connection) -> None:
    """Run real DDL, then blow up -- proving even DDL that already executed rolls back."""
    connection.execute("ALTER TABLE runs ADD COLUMN doomed TEXT")
    raise RuntimeError("boom")


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in rows}


# --- the core acceptance proof ----------------------------------------------------------


def test_demo_migration_advances_version_and_preserves_v1_records(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    run, decision, entry = _build_v1_shaped_database(path)

    # Confirm this really is what an old v1 library looks like: no version stamp, no ledger.
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    tables = _table_names(path)
    assert "schema_migrations" not in tables
    assert {"runs", "decisions", "research_memory"} <= tables

    result = run_migrations(path, clock=_clock)

    assert result.from_version == 0
    assert result.to_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    assert [m.version for m in result.applied] == [
        BASELINE_VERSION,
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
    ]
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert result.backup_path.parent == path.parent / "backups"

    status = read_status(path)
    assert status.current_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    assert [(m.version, m.name) for m in status.applied] == [
        (BASELINE_VERSION, "baseline"),
        (DEMO_ADD_RUNS_ARCHIVED_AT_VERSION, "demo_add_runs_archived_at"),
    ]
    assert status.pending == ()

    # The real schema change actually happened.
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
    assert "archived_at" in columns

    # The core proof: records written before the migration system existed are still
    # readable through the *existing, unmodified* stores.
    repository = SQLiteRunRepository(path)
    memory = SQLiteResearchMemory(path)
    assert repository.get_run(run.run_id) == run
    assert repository.get_decision(decision.decision_id) == decision
    assert memory.list(subject=entry.subject) == (entry,)


def test_running_migrations_again_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_database(path)

    first = run_migrations(path, clock=_clock)
    second = run_migrations(path, clock=_clock)

    assert first.to_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    assert second.from_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    assert second.to_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    assert second.applied == ()
    assert second.backup_path is None  # nothing pending, so no backup taken

    status = read_status(path)
    assert [m.version for m in status.applied] == [
        BASELINE_VERSION,
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
    ]


def test_demo_migration_is_a_sql_level_no_op_when_the_column_already_exists(
    tmp_path: Path,
) -> None:
    """Guard the demo migration's own SQL-level idempotency.

    Independent of the framework-level guard (`schema_migrations` blocking
    re-application): if `runs` already has `archived_at` -- e.g. added by hand, or by
    a future migration this one predates -- applying it must not raise a
    duplicate-column error.
    """
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE runs ADD COLUMN archived_at TEXT")

    result = run_migrations(path, clock=_clock)

    assert result.to_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    with sqlite3.connect(path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(runs)")]
    assert columns.count("archived_at") == 1


def test_failing_migration_rolls_back_leaves_version_unmoved_and_backup_intact(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    run, _decision_record, _entry = _build_v1_shaped_database(path)

    # Bring the database up to date first, exactly as a real upgrade would.
    run_migrations(path, clock=_clock)
    before = read_status(path)
    assert before.current_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION

    doomed_migrations = (
        *MIGRATIONS,
        Migration(version=3, name="doomed_demo", apply=_failing_apply),
    )

    with pytest.raises(MigrationFailedError) as excinfo:
        run_migrations(path, clock=_clock, migrations=doomed_migrations)

    error = excinfo.value
    assert error.version == 3
    assert error.name == "doomed_demo"
    assert error.backup_path.exists()

    after = read_status(path)
    assert after.current_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION  # unmoved
    assert [m.version for m in after.applied] == [
        BASELINE_VERSION,
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
    ]  # no row for version 3

    # Data is intact, and the DDL the doomed migration ran (before it raised) did not persist.
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
    assert "doomed" not in columns
    repository = SQLiteRunRepository(path)
    assert repository.get_run(run.run_id) == run


def test_new_database_lands_on_baseline_and_defers_the_demo_migration(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"

    result = run_migrations(path, clock=_clock)

    assert result.from_version == 0
    assert result.to_version == BASELINE_VERSION
    assert [m.version for m in result.applied] == [BASELINE_VERSION]
    status = read_status(path)
    assert [m.version for m in status.pending] == [DEMO_ADD_RUNS_ARCHIVED_AT_VERSION]

    # Once the owning store creates its table (independent of the migrator, by design),
    # the deferred migration becomes applicable on the very next run.
    SQLiteRunRepository(path)
    caught_up = run_migrations(path, clock=_clock)
    assert caught_up.from_version == BASELINE_VERSION
    assert caught_up.to_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION


def test_read_status_does_not_mutate_the_database(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_database(path)

    status_before = read_status(path)
    status_after = read_status(path)

    assert status_before.current_version == 0
    assert status_after.current_version == 0
    assert status_before.applied == ()
    assert [m.version for m in status_before.pending] == [
        BASELINE_VERSION,
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
    ]
    assert "schema_migrations" not in _table_names(path)


def test_migrations_registry_is_declared_in_strictly_increasing_version_order() -> None:
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
