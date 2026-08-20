"""Tests for the SQLite schema-migration engine (V2-P0B-004).

These are integration tests, not unit tests: the property under test is the engine's
interaction with a *real* SQLite file -- transactional DDL, `PRAGMA user_version`, and the
SQLite backup API have no meaningful in-memory double.
"""

import logging
import multiprocessing
import sqlite3
import time
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.portfolio import PortfolioOrder, PortfolioState, PortfolioTransition
from openalpha_cn.domain.report import ResearchReport
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.runtime.memory import MemoryEntry
from openalpha_cn.storage.batch import SQLiteBatchTaskStore
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.migrations import (
    _SCHEMA_MIGRATIONS_DDL,
    ADD_RUNS_MODE_PROJECTION_VERSION,
    BASELINE_VERSION,
    CREATE_QUERY_PATH_INDEXES_VERSION,
    CREATE_VALIDATION_RESULTS_VERSION,
    DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
    MIGRATIONS,
    REWRITE_CONTRACT_IDENTITIES_VERSION,
    REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    SPLIT_BATCH_TASK_ITEMS_VERSION,
    Migration,
    MigrationFailedError,
    MigrationNotYetApplicable,
    _take_backup,
    read_status,
    run_migrations,
)
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger
from openalpha_cn.storage.product import SQLiteReportStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository

DIGEST = "a" * 64


def _manifest(*, now: datetime, run_id: str = "run_v1_shape") -> RunManifest:
    return RunManifest(
        run_id=run_id,
        mode="replay",
        as_of=now,
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
        started_at=now,
        status="running",
    )


def _decision(
    *, now: datetime, run_manifest_id: str, run_id: str = "run_v1_shape"
) -> DecisionLedger:
    return DecisionLedger(
        run_id=run_id,
        run_manifest_id=run_manifest_id,
        created_at=now,
        routing_path=("risk-gate",),
        risk_decision="block",
        final_action="abstain",
        code_commit="0123456789abcdef",
    )


def _memory_entry(run_id: str, decision_id: str, *, now: datetime) -> MemoryEntry:
    return MemoryEntry(
        run_id=run_id,
        subject="000001.SZ",
        created_at=now,
        decision_id=decision_id,
        signal_id="sig_v1_shape",
        summary="v1-shaped memory entry written before the migration system existed.",
    )


def _portfolio_transition() -> PortfolioTransition:
    before = PortfolioState(as_of=date(2026, 8, 6), cash=Decimal("10000"))
    after = PortfolioState(as_of=date(2026, 8, 6), cash=Decimal("9000"))
    return PortfolioTransition(
        status="filled",
        order=PortfolioOrder(
            order_id="order_v1_shape", subject="000001.SZ", side="buy", quantity=100
        ),
        before=before,
        after=after,
    )


def _research_report(*, now: datetime, decision_id: str) -> ResearchReport:
    return ResearchReport(
        run_id="run_v1_shape",
        subject="000001.SZ",
        created_at=now,
        title="v1-shaped report",
        summary="Report written before the migration system existed.",
        decision_id=decision_id,
        signal_id="sig_v1_shape",
        final_action="abstain",
        evidence_ids=("ev_v1_shape",),
        risk_flags=(),
    )


def _build_v1_shaped_database(
    path: Path, *, now: datetime
) -> tuple[RunManifest, DecisionLedger, MemoryEntry, PortfolioTransition, ResearchReport]:
    """Populate `path` using only the pre-existing stores -- exactly like a real v1 install.

    "v1-shaped" here is about the *table* layout a pre-migration install left behind -- no
    `schema_migrations`, no `validation_results` -- and not about payload contract versions:
    the rows are written through today's stores, so they carry today's `schema_version`. The
    payload-level v1 case, which is `V2-P4-001`'s identity rewrite, has its own database
    builder in `tests/integration/storage/test_identity_rewrite.py`.

    All seven stores that predate the migration engine (V2-P0B-004) run here, not just
    `SQLiteRunRepository`/`SQLiteResearchMemory`: `SQLitePortfolioLedger` and
    `SQLiteReportStore` are added by task 21 (`V2-P0B-015`) so `checkpoints`,
    `portfolio_transitions`, and `research_reports` -- the three tables
    `create_query_path_indexes` (see `storage/migrations.py`) indexes -- all genuinely
    exist here, the same way they would in a real v1 install. Without this, that migration
    would defer forever against this fixture (`portfolio_transitions`/`research_reports`
    would never exist), permanently stranding it behind an unmet precondition in every test
    that reuses this fixture -- itself indistinguishable, from inside a single
    `run_migrations()` call, from a database that will never converge.

    `SQLiteBatchTaskStore` joins them at `V2-P4-019` for exactly that reason: its
    `split_batch_task_items` migration requires `batch_tasks`, which every real install has
    (the store's constructor creates it) and which this fixture did not, so without this line
    the head version would be unreachable here and every "fully caught up" assertion below
    would be asserting something no database in production ever looks like. It writes no
    rows -- the split's behaviour on a batch that actually has items is
    `test_batch_item_split_migration.py`, which builds the genuinely pre-split payload shape
    this fixture's constructor no longer produces.
    """
    repository = SQLiteRunRepository(path)
    memory = SQLiteResearchMemory(path)
    portfolio_ledger = SQLitePortfolioLedger(path)
    report_store = SQLiteReportStore(path)
    SQLiteBatchTaskStore(path)
    run = _manifest(now=now)
    repository.append_run(run)
    decision = _decision(now=now, run_manifest_id=run.run_manifest_id)
    repository.append_decision(decision)
    entry = _memory_entry(run_id=run.run_id, decision_id=decision.decision_id, now=now)
    memory.append(entry)
    transition = _portfolio_transition()
    portfolio_ledger.append(transition)
    report = _research_report(now=now, decision_id=decision.decision_id)
    report_store.append(report)
    return run, decision, entry, transition, report


def _failing_apply(connection: sqlite3.Connection) -> None:
    """Run real DDL, then blow up -- proving even DDL that already executed rolls back."""
    connection.execute("ALTER TABLE runs ADD COLUMN doomed TEXT")
    raise RuntimeError("boom")


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in rows}


# --- the core acceptance proof ----------------------------------------------------------


def test_demo_migration_advances_version_and_preserves_v1_records(
    tmp_path: Path, migration_now: datetime, migration_clock: Callable[[], datetime]
) -> None:
    path = tmp_path / "state.sqlite3"
    run, decision, entry, transition, report = _build_v1_shaped_database(path, now=migration_now)

    # Confirm this really is what an old v1 library looks like: no version stamp, no ledger.
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    tables = _table_names(path)
    assert "schema_migrations" not in tables
    assert {
        "runs",
        "decisions",
        "checkpoints",
        "research_memory",
        "portfolio_transitions",
        "research_reports",
    } <= tables

    result = run_migrations(path, clock=migration_clock)

    assert result.from_version == 0
    assert result.to_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    # `runs`, `checkpoints`, `portfolio_transitions`, and `research_reports` all already
    # exist (built above), so nothing defers: baseline, then create_validation_results
    # (V2-P0B-010, ordered before the demo migration -- see that migration's docstring for
    # why), then the demo migration, then create_query_path_indexes (task 21 -- see its
    # docstring for why it is ordered *after* the demo migration instead) all apply in this
    # single call.
    assert [m.version for m in result.applied] == [
        BASELINE_VERSION,
        CREATE_VALIDATION_RESULTS_VERSION,
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
        CREATE_QUERY_PATH_INDEXES_VERSION,
        REWRITE_CONTRACT_IDENTITIES_VERSION,
        ADD_RUNS_MODE_PROJECTION_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
        REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    ]
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert result.backup_path.parent == path.parent / "backups"

    status = read_status(path)
    assert status.current_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    assert [(m.version, m.name) for m in status.applied] == [
        (BASELINE_VERSION, "baseline"),
        (CREATE_VALIDATION_RESULTS_VERSION, "create_validation_results"),
        (DEMO_ADD_RUNS_ARCHIVED_AT_VERSION, "demo_add_runs_archived_at"),
        (CREATE_QUERY_PATH_INDEXES_VERSION, "create_query_path_indexes"),
        (REWRITE_CONTRACT_IDENTITIES_VERSION, "rewrite_contract_identities"),
        (ADD_RUNS_MODE_PROJECTION_VERSION, "add_runs_mode_projection"),
        (SPLIT_BATCH_TASK_ITEMS_VERSION, "split_batch_task_items"),
        (REWRITE_MANIFEST_COMPONENT_PLANES_VERSION, "rewrite_manifest_component_planes"),
    ]
    assert status.pending == ()

    # The real schema changes actually happened.
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        index_names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
    assert "archived_at" in columns
    assert {
        "checkpoints_run_id_idx",
        "portfolio_transitions_subject_idx",
        "research_reports_subject_idx",
    } <= index_names

    # The core proof: records written before the migration system existed are still
    # readable through the *existing, unmodified* stores.
    repository = SQLiteRunRepository(path)
    memory = SQLiteResearchMemory(path)
    portfolio_ledger = SQLitePortfolioLedger(path)
    report_store = SQLiteReportStore(path)
    assert repository.get_run(run.run_id) == run
    assert repository.get_decision(decision.decision_id) == decision
    assert memory.list(subject=entry.subject) == (entry,)
    assert portfolio_ledger.get(transition.order.order_id) == transition
    assert report_store.get(report.report_id) == report


def test_upgrading_a_pre_validation_database_creates_the_table_and_keeps_old_records_readable(
    tmp_path: Path, migration_now: datetime, migration_clock: Callable[[], datetime]
) -> None:
    """V2-P0B-010's explicit upgrade-path proof: a database built before this task existed
    (no `validation_results`, no `schema_migrations` -- exactly `_build_v1_shaped_database`,
    reused from the demo-migration proof above) gets `validation_results` -- table plus both
    query-path indexes -- after one `run_migrations()` call, and every record written
    through the old stores before the migration ran is still readable afterwards, unchanged.
    """
    path = tmp_path / "state.sqlite3"
    run, decision, entry, transition, report = _build_v1_shaped_database(path, now=migration_now)
    assert "validation_results" not in _table_names(path)

    run_migrations(path, clock=migration_clock)

    tables = _table_names(path)
    assert "validation_results" in tables
    with sqlite3.connect(path) as connection:
        index_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name = 'validation_results'"
            )
        }
    assert index_names == {
        "sqlite_autoindex_validation_results_1",  # from the UNIQUE column constraint
        "validation_results_decision_id_idx",
        "validation_results_signal_id_idx",
    }

    repository = SQLiteRunRepository(path)
    memory = SQLiteResearchMemory(path)
    portfolio_ledger = SQLitePortfolioLedger(path)
    report_store = SQLiteReportStore(path)
    assert repository.get_run(run.run_id) == run
    assert repository.get_decision(decision.decision_id) == decision
    assert memory.list(subject=entry.subject) == (entry,)
    assert portfolio_ledger.get(transition.order.order_id) == transition
    assert report_store.get(report.report_id) == report


# --- Finding F69: the three new query-path indexes must be chosen by the planner, not --
# --- merely present (task 21 / V2-P0B-015) -----------------------------------------------


def test_query_path_indexes_are_chosen_by_the_planner_not_a_full_scan(
    tmp_path: Path, migration_now: datetime, migration_clock: Callable[[], datetime]
) -> None:
    """A present-but-unused index is indistinguishable from no index at all -- the exact
    trap the brief warns against (`validation_results`'s indexes were verified with
    `EXPLAIN QUERY PLAN`, not just `sqlite_master` membership). This checks the query plan
    both *before* migrating (proving the plan really was a full `SCAN` beforehand, not just
    asserting the improvement) and *after* (proving the planner now picks `SEARCH ... USING
    INDEX`), for each of the three query paths Finding F69 named: `checkpoints.run_id`,
    `portfolio_transitions.subject`, `research_reports.subject`.
    """
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_database(path, now=migration_now)

    queries = {
        "checkpoints": "SELECT payload FROM checkpoints WHERE run_id = ? ORDER BY sequence",
        "portfolio_transitions": (
            "SELECT payload FROM portfolio_transitions WHERE subject = ? ORDER BY sequence"
        ),
        "research_reports": (
            "SELECT payload FROM research_reports WHERE subject = ? ORDER BY sequence"
        ),
    }

    with sqlite3.connect(path) as connection:
        plans_before = {
            table: connection.execute(f"EXPLAIN QUERY PLAN {sql}", ("anything",)).fetchall()
            for table, sql in queries.items()
        }
    for table, plan in plans_before.items():
        detail = plan[-1][3]
        assert detail == f"SCAN {table}", (
            f"{table}: expected a full scan before migrating, got {detail!r}"
        )

    run_migrations(path, clock=migration_clock)

    with sqlite3.connect(path) as connection:
        plans_after = {
            table: connection.execute(f"EXPLAIN QUERY PLAN {sql}", ("anything",)).fetchall()
            for table, sql in queries.items()
        }
    expected_after = {
        "checkpoints": "SEARCH checkpoints USING INDEX checkpoints_run_id_idx (run_id=?)",
        "portfolio_transitions": (
            "SEARCH portfolio_transitions USING INDEX portfolio_transitions_subject_idx (subject=?)"
        ),
        "research_reports": (
            "SEARCH research_reports USING INDEX research_reports_subject_idx (subject=?)"
        ),
    }
    for table, plan in plans_after.items():
        detail = plan[-1][3]
        assert detail == expected_after[table], (
            f"{table}: expected an index seek after migrating, got {detail!r}"
        )


def test_running_migrations_again_is_idempotent(
    tmp_path: Path, migration_now: datetime, migration_clock: Callable[[], datetime]
) -> None:
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_database(path, now=migration_now)

    first = run_migrations(path, clock=migration_clock)
    second = run_migrations(path, clock=migration_clock)

    assert first.to_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    assert second.from_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    assert second.to_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    assert second.applied == ()
    assert second.backup_path is None  # nothing pending, so no backup taken

    status = read_status(path)
    assert [m.version for m in status.applied] == [
        BASELINE_VERSION,
        CREATE_VALIDATION_RESULTS_VERSION,
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
        CREATE_QUERY_PATH_INDEXES_VERSION,
        REWRITE_CONTRACT_IDENTITIES_VERSION,
        ADD_RUNS_MODE_PROJECTION_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
        REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    ]


def test_demo_migration_is_a_sql_level_no_op_when_the_column_already_exists(
    tmp_path: Path, migration_now: datetime, migration_clock: Callable[[], datetime]
) -> None:
    """Guard the demo migration's own SQL-level idempotency.

    Independent of the framework-level guard (`schema_migrations` blocking
    re-application): if `runs` already has `archived_at` -- e.g. added by hand, or by
    a future migration this one predates -- applying it must not raise a
    duplicate-column error.
    """
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_database(path, now=migration_now)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE runs ADD COLUMN archived_at TEXT")

    result = run_migrations(path, clock=migration_clock)

    assert result.to_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    with sqlite3.connect(path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(runs)")]
    assert columns.count("archived_at") == 1


def test_failing_migration_rolls_back_leaves_version_unmoved_and_backup_intact(
    tmp_path: Path, migration_now: datetime, migration_clock: Callable[[], datetime]
) -> None:
    path = tmp_path / "state.sqlite3"
    run, _decision_record, _entry, _transition, _report = _build_v1_shaped_database(
        path, now=migration_now
    )

    # Bring the database up to date first, exactly as a real upgrade would.
    run_migrations(path, clock=migration_clock)
    before = read_status(path)
    assert before.current_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION

    # One past the real registry's own highest version, computed rather than hardcoded,
    # so this injected migration's version can never collide with a real one as the
    # registry grows (it did collide, silently changing this test's meaning, when
    # V2-P0B-010 added a third real migration at what used to be this literal's value).
    doomed_version = max(m.version for m in MIGRATIONS) + 1
    doomed_migrations = (
        *MIGRATIONS,
        Migration(version=doomed_version, name="doomed_demo", apply=_failing_apply),
    )

    with pytest.raises(MigrationFailedError) as excinfo:
        run_migrations(path, clock=migration_clock, migrations=doomed_migrations)

    error = excinfo.value
    assert error.version == doomed_version
    assert error.name == "doomed_demo"
    assert error.backup_path.exists()

    after = read_status(path)
    assert after.current_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION  # unmoved
    assert [m.version for m in after.applied] == [
        BASELINE_VERSION,
        CREATE_VALIDATION_RESULTS_VERSION,
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
        CREATE_QUERY_PATH_INDEXES_VERSION,
        REWRITE_CONTRACT_IDENTITIES_VERSION,
        ADD_RUNS_MODE_PROJECTION_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
        REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    ]  # no row for doomed_version

    # Data is intact, and the DDL the doomed migration ran (before it raised) did not persist.
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
    assert "doomed" not in columns
    repository = SQLiteRunRepository(path)
    assert repository.get_run(run.run_id) == run


# --- structured logging (V2-P0B-007) ------------------------------------------------------


def test_run_migrations_logs_the_backup_path_and_each_applied_migration(
    tmp_path: Path,
    migration_now: datetime,
    migration_clock: Callable[[], datetime],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Migration execution -- version advance and backup path -- is one of the four
    call sites V2-P0B-007's brief names explicitly: "out of hours" is exactly when
    this needs to be greppable."""
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_database(path, now=migration_now)
    caplog.set_level(logging.INFO, logger="openalpha_cn.storage.migrations")

    result = run_migrations(path, clock=migration_clock)

    assert result.backup_path is not None
    backup_events = [r for r in caplog.records if r.message == "migration_backup_created"]
    assert len(backup_events) == 1
    assert backup_events[0].backup_path == str(result.backup_path)  # type: ignore[attr-defined]

    applied_events = [r for r in caplog.records if r.message == "migration_applied"]
    assert [(r.migration_version, r.migration_name) for r in applied_events] == [  # type: ignore[attr-defined]
        (BASELINE_VERSION, "baseline"),
        (CREATE_VALIDATION_RESULTS_VERSION, "create_validation_results"),
        (DEMO_ADD_RUNS_ARCHIVED_AT_VERSION, "demo_add_runs_archived_at"),
        (CREATE_QUERY_PATH_INDEXES_VERSION, "create_query_path_indexes"),
        (REWRITE_CONTRACT_IDENTITIES_VERSION, "rewrite_contract_identities"),
        (ADD_RUNS_MODE_PROJECTION_VERSION, "add_runs_mode_projection"),
        (SPLIT_BATCH_TASK_ITEMS_VERSION, "split_batch_task_items"),
        (REWRITE_MANIFEST_COMPONENT_PLANES_VERSION, "rewrite_manifest_component_planes"),
    ]


def test_run_migrations_logs_failure_without_leaking_the_underlying_exception_message(
    tmp_path: Path,
    migration_now: datetime,
    migration_clock: Callable[[], datetime],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sentinel-driven leak proof for the migration-failure log call: a migration's
    `apply()` can raise *any* exception (a `sqlite3.OperationalError` today, but
    nothing stops a future migration's precondition check from raising something
    that embeds a connection string or credential). The `migration_failed` log
    record must carry only `version`/`name`/`backup_path` -- never `str(error)`.
    """
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_database(path, now=migration_now)
    secret = "sk-migration-failure-log-must-not-leak-99001"

    def _leaking_apply(connection: sqlite3.Connection) -> None:
        raise RuntimeError(f"connection refused for postgres://user:{secret}@host/db")

    # One past the real registry's own highest version, computed rather than hardcoded --
    # see the identical rationale on `doomed_version` above.
    doomed_version = max(m.version for m in MIGRATIONS) + 1
    doomed_migrations = (
        *MIGRATIONS,
        Migration(version=doomed_version, name="doomed_leaking", apply=_leaking_apply),
    )
    caplog.set_level(logging.INFO, logger="openalpha_cn.storage.migrations")

    with pytest.raises(MigrationFailedError) as excinfo:
        run_migrations(path, clock=migration_clock, migrations=doomed_migrations)

    assert secret not in caplog.text
    failure_events = [r for r in caplog.records if r.message == "migration_failed"]
    assert len(failure_events) == 1
    record = failure_events[0]
    assert record.migration_version == doomed_version  # type: ignore[attr-defined]
    assert record.migration_name == "doomed_leaking"  # type: ignore[attr-defined]
    assert record.backup_path == str(excinfo.value.backup_path)  # type: ignore[attr-defined]


def test_new_database_applies_baseline_and_validation_results_then_defers_the_demo_migration(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """A truly fresh database, migrated once: `baseline` and `create_validation_results`
    (V2-P0B-010) both apply -- neither has a precondition -- but `demo_add_runs_archived_at`
    and `create_query_path_indexes` (task 21) both still defer exactly as before, since
    `runs`/`checkpoints`/`portfolio_transitions`/`research_reports` are each created by a
    store's own constructor, none of which this single call ever reaches.
    `create_validation_results` is ordered *before* the demo migration precisely so it
    lands here, in the very first call, instead of getting stuck behind the demo
    migration's routine deferral -- see that migration's docstring in
    `storage/migrations.py`. `create_query_path_indexes` needs no equivalent ordering
    trick: it is not precondition-free either way, so it defers here regardless of
    position -- see its own docstring.
    """
    path = tmp_path / "state.sqlite3"

    result = run_migrations(path, clock=migration_clock)

    assert result.from_version == 0
    assert result.to_version == CREATE_VALIDATION_RESULTS_VERSION
    assert [m.version for m in result.applied] == [
        BASELINE_VERSION,
        CREATE_VALIDATION_RESULTS_VERSION,
    ]
    status = read_status(path)
    assert [m.version for m in status.pending] == [
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
        CREATE_QUERY_PATH_INDEXES_VERSION,
        REWRITE_CONTRACT_IDENTITIES_VERSION,
        ADD_RUNS_MODE_PROJECTION_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
        REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    ]

    # Once the owning store creates its table (independent of the migrator, by design),
    # the deferred migration becomes applicable on the very next run. `SQLiteRunRepository`
    # creates `runs`/`decisions`/`checkpoints` together, so the demo migration (needs only
    # `runs`) catches up here -- but `create_query_path_indexes` also needs
    # `portfolio_transitions`/`research_reports`, owned by two stores this line does not
    # construct, so it stays pending even after this catch-up.
    SQLiteRunRepository(path)
    caught_up = run_migrations(path, clock=migration_clock)
    assert caught_up.from_version == CREATE_VALIDATION_RESULTS_VERSION
    assert caught_up.to_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    still_pending = read_status(path)
    assert [m.version for m in still_pending.pending] == [
        CREATE_QUERY_PATH_INDEXES_VERSION,
        REWRITE_CONTRACT_IDENTITIES_VERSION,
        ADD_RUNS_MODE_PROJECTION_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
        REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    ]

    # Constructing the last three owning stores lets the fourth call finish the job.
    # `SQLiteBatchTaskStore` is the one `split_batch_task_items` (V2-P4-019) waits on, and
    # it is added here rather than earlier deliberately: it demonstrates the same
    # store-creates-the-table-then-the-migration-catches-up handshake one migration later,
    # against a table no earlier store creates.
    SQLitePortfolioLedger(path)
    SQLiteReportStore(path)
    SQLiteBatchTaskStore(path)
    fully_caught_up = run_migrations(path, clock=migration_clock)
    assert fully_caught_up.from_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    assert fully_caught_up.to_version == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    assert read_status(path).pending == ()


def test_read_status_does_not_mutate_the_database(tmp_path: Path, migration_now: datetime) -> None:
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_database(path, now=migration_now)

    status_before = read_status(path)
    status_after = read_status(path)

    assert status_before.current_version == 0
    assert status_after.current_version == 0
    assert status_before.applied == ()
    assert [m.version for m in status_before.pending] == [
        BASELINE_VERSION,
        CREATE_VALIDATION_RESULTS_VERSION,
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
        CREATE_QUERY_PATH_INDEXES_VERSION,
        REWRITE_CONTRACT_IDENTITIES_VERSION,
        ADD_RUNS_MODE_PROJECTION_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
        REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    ]
    assert "schema_migrations" not in _table_names(path)


def test_migrations_registry_is_declared_in_strictly_increasing_version_order() -> None:
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


# --- structural guard: no precondition-free migration may be stranded behind an --------
# --- earlier-ordered deferral (reviewer finding on this task) --------------------------


def _defers_in_isolation(migration: Migration) -> bool:
    """True iff `migration.apply()` raises `MigrationNotYetApplicable` against a fresh,
    independent, completely empty `:memory:` connection of its own.

    This is the *empirical* test for "precondition-free", used instead of trusting
    `_PRECONDITION_FREE_VERSIONS` (below) as ground truth. That set is hand-maintained --
    a future migration added to `MIGRATIONS` without a matching update to it would silently
    make this guard's expectation wrong in exactly the way the guard exists to catch. Probing
    each migration directly, the same way
    `test_every_non_baseline_migration_defers_gracefully_on_a_fresh_empty_database` and
    `test_create_validation_results_table_succeeds_on_a_genuinely_empty_database` already do,
    means this test's notion of "precondition-free" self-updates as the registry grows and
    needs no second allowlist kept in sync by hand.
    """
    connection = sqlite3.connect(":memory:")
    try:
        try:
            migration.apply(connection)
        except MigrationNotYetApplicable:
            return True
        return False
    finally:
        connection.close()


def test_no_precondition_free_migration_is_stranded_behind_an_earlier_deferral(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Structural regression test for the executor's stop-not-skip semantics (see
    `run_migrations`'s docstring): a migration that raises `MigrationNotYetApplicable`
    stops the whole pass -- `_pending()` filters purely on the `PRAGMA user_version`
    watermark, not a per-migration applied set, so skipping past a deferred migration and
    then committing a higher version number would lose it permanently rather than retry it.
    That design choice is correct and is not what this test checks.

    What it checks is the *registry's* obligation under that design: every migration that is
    precondition-free (would succeed against a genuinely empty database, probed in isolation
    by `_defers_in_isolation` above) must actually be applied when `run_migrations()` runs
    once, for real, against a genuinely fresh database -- none of them may be silently
    stranded behind an earlier-ordered migration that defers. `CREATE_VALIDATION_RESULTS_VERSION`
    hit this exact trap during this task and was fixed by moving it before
    `DEMO_ADD_RUNS_ARCHIVED_AT_VERSION` in version order (see that migration's docstring in
    `storage/migrations.py`); this test turns that one-off fix into a standing invariant so
    the next migration added to `MIGRATIONS` cannot reintroduce it un-noticed.
    """
    expected_precondition_free = {
        migration.version for migration in MIGRATIONS if not _defers_in_isolation(migration)
    }

    path = tmp_path / "state.sqlite3"
    result = run_migrations(path, clock=migration_clock)

    applied_versions = {m.version for m in result.applied}
    stranded = {
        migration.name: migration.version
        for migration in MIGRATIONS
        if migration.version in expected_precondition_free
        and migration.version not in applied_versions
    }
    assert not stranded, (
        "migration(s) precondition-free on an empty database were never attempted in a "
        "single run_migrations() call because an earlier-ordered migration deferred first "
        f"(reorder MIGRATIONS so they precede the deferring migration): {stranded}"
    )


# --- structural guard: no future migration may crash a fresh install (Finding 1c) ------


# Migrations that are, by design, precondition-free: they create something of their own
# rather than altering a table some other store's constructor owns, so there is nothing on
# a genuinely empty database for them to wait for. `BASELINE_VERSION` stamps a version and
# touches no data; `CREATE_VALIDATION_RESULTS_VERSION` (V2-P0B-010) creates its own
# `validation_results` table (see that migration's docstring in `storage/migrations.py`
# for why this is not merely convenient but load-bearing: ordering it before the demo
# migration is what lets it apply within the very first `run_migrations()` call on a
# fresh install). `test_create_validation_results_table_succeeds_on_a_genuinely_empty_database`
# below is this exemption's positive-case proof: precondition-free does not mean untested,
# it means the *opposite* of "defers" is the property under test.
_PRECONDITION_FREE_VERSIONS = frozenset({BASELINE_VERSION, CREATE_VALIDATION_RESULTS_VERSION})


def test_every_non_baseline_migration_defers_gracefully_on_a_fresh_empty_database() -> None:
    """A careless future migration must not crash every fresh install.

    The demo migration is safe only because it hand-wrote a `require_table` guard; nothing
    forced that. Phase P4 will add several migrations at once, likely written separately --
    one omission crashes `OpenAlphaSDK.__init__`/`create_app()` for every new installation,
    because `build_storage()` runs migrations before any store (and therefore any table)
    exists (see `storage/migrations.py`'s module docstring). This test is the structural
    guard: it runs every migration in the real, shipped `MIGRATIONS` registry that is *not*
    known to be precondition-free (`_PRECONDITION_FREE_VERSIONS` above) against a genuinely
    empty database and requires it to raise `MigrationNotYetApplicable` (defer) rather than
    any other exception (crash).
    """
    for migration in MIGRATIONS:
        if migration.version in _PRECONDITION_FREE_VERSIONS:
            continue
        connection = sqlite3.connect(":memory:")
        try:
            with pytest.raises(MigrationNotYetApplicable):
                migration.apply(connection)
        finally:
            connection.close()


def test_create_validation_results_table_succeeds_on_a_genuinely_empty_database() -> None:
    """The positive-case complement of the guard above: `create_validation_results`
    (V2-P0B-010) is exempted from "every non-baseline migration must defer on a fresh
    database" *because* it genuinely has no precondition, not because the guard forgot
    about it. Applying it directly against a bare `:memory:` connection -- no
    `schema_migrations`, no `runs`, nothing -- must create both the table and its two
    query-path indexes, with no exception raised at all.
    """
    connection = sqlite3.connect(":memory:")
    try:
        migration = next(m for m in MIGRATIONS if m.version == CREATE_VALIDATION_RESULTS_VERSION)

        migration.apply(connection)

        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert "validation_results" in tables
        assert "validation_results_decision_id_idx" in tables
        assert "validation_results_signal_id_idx" in tables
    finally:
        connection.close()


# --- backup filenames must never collide (Finding 4b) -----------------------------------


def test_take_backup_does_not_overwrite_an_existing_backup_at_the_same_timestamp(
    tmp_path: Path, migration_now: datetime, migration_clock: Callable[[], datetime]
) -> None:
    """Two backups at the same version, taken by a frozen clock, must not collide.

    The filename is `{name}.v{version}.{timestamp}.bak` at second precision; previously
    nothing checked whether the target already existed, so two failed migrations at the
    same version within the same second silently overwrote each other's backup -- the one
    piece of data an operator would reach for after a failure. A fixed `clock` (as used
    throughout this test module, and as a real concurrent racer would experience -- see
    the concurrency test below) reproduces "same second" deterministically instead of
    relying on wall-clock luck.
    """
    path = tmp_path / "state.sqlite3"
    _build_v1_shaped_database(path, now=migration_now)

    first = _take_backup(path, current_version=0, clock=migration_clock)
    second = _take_backup(path, current_version=0, clock=migration_clock)

    assert first != second
    assert first.exists()
    assert second.exists()
    # Both are real, independently restorable backups -- not one half-overwritten by the
    # other's SQLite backup-API write landing on the same path mid-copy.
    for backup_path in (first, second):
        with sqlite3.connect(backup_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


# --- concurrent callers must never see a false failure (Finding 5) ----------------------


def _race_sleepy_apply(connection: sqlite3.Connection) -> None:
    """Hold the write lock for a while so a concurrent racer is guaranteed to block on
    its own `BEGIN IMMEDIATE` until this transaction commits -- reproducing the race
    deterministically instead of depending on OS scheduling luck (the reviewer's own
    reproduction of this bug was 1-in-5 without a forced delay)."""
    time.sleep(0.4)


_RACE_VERSION = 1
_RACE_MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=_RACE_VERSION, name="race_baseline", apply=_race_sleepy_apply),
)


def _race_worker(path_str: str, barrier, queue, now: datetime) -> None:
    """Run in a real, separate OS process (multiprocessing's `spawn` start method on this
    platform launches a fresh interpreter, not a thread) against the same database file
    as its sibling worker.

    `now` is `migration_now`'s already-*resolved* value, not the fixture itself: a spawned
    child re-imports this module fresh and has no pytest fixture graph to draw from, so the
    fixture callable can't cross the process boundary. A plain `datetime` pickles like any
    other argument, so the caller passes the resolved value positionally and this worker
    builds its own local zero-arg clock from it.
    """
    barrier.wait()
    try:
        result = run_migrations(Path(path_str), clock=lambda: now, migrations=_RACE_MIGRATIONS)
    except MigrationFailedError as error:
        queue.put(("failed", error.version, error.name))
    else:
        queue.put(("ok", result.to_version, tuple(m.version for m in result.applied)))


def test_concurrent_run_migrations_does_not_report_a_false_failure(
    tmp_path: Path, migration_now: datetime
) -> None:
    """Two real OS processes racing `run_migrations()` against the same fresh database.

    `_pending()` is computed once, from a snapshot taken before any lock is acquired, so
    both processes independently decide the same migration is pending. `BEGIN IMMEDIATE`
    correctly serializes the two writers -- but without a post-lock re-check, the loser
    would replay `migration.apply()` and then hit a primary-key collision on its own
    `INSERT INTO schema_migrations`, which the generic `except Exception` reports as a
    genuine `MigrationFailedError` even though the database converged correctly and
    nothing is actually broken. Neither worker may see that false failure.

    `schema_migrations` is pre-created below (the first statement `run_migrations()`
    itself executes is `CREATE TABLE IF NOT EXISTS schema_migrations`, which -- even
    though it is a no-op once the table exists -- still needs a write lock to check that
    safely). Without pre-creating it, that DDL statement becomes the dominant point of
    contention: the loser blocks there until the winner's entire transaction (including
    its audit-row insert) has already committed, and by the time it reads `PRAGMA
    user_version` nothing is pending anymore -- masking the actual race this test targets,
    which starts one step later, at `BEGIN IMMEDIATE`.
    """
    path = tmp_path / "state.sqlite3"
    with sqlite3.connect(path) as setup_connection:
        setup_connection.execute(_SCHEMA_MIGRATIONS_DDL)
    barrier = multiprocessing.Barrier(2)
    queue: multiprocessing.Queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=_race_worker, args=(str(path), barrier, queue, migration_now)
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    outcomes = [queue.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert not process.is_alive(), "a worker process hung"
        assert process.exitcode == 0, f"a worker process crashed: exitcode={process.exitcode}"

    for outcome in outcomes:
        assert outcome[0] == "ok", f"a concurrent migration run reported a false failure: {outcome}"

    status = read_status(path)
    assert status.current_version == _RACE_VERSION
    assert [m.version for m in status.applied] == [_RACE_VERSION]  # applied exactly once
