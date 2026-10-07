import json
import sqlite3
from contextlib import closing
from pathlib import Path

from typer.testing import CliRunner

from openalpha_cn.cli import app
from openalpha_cn.storage.migrations import (
    ADD_RUNS_MODE_PROJECTION_VERSION,
    BASELINE_VERSION,
    CREATE_QUERY_PATH_INDEXES_VERSION,
    CREATE_VALIDATION_RESULTS_VERSION,
    DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
    REWRITE_CONTRACT_IDENTITIES_VERSION,
    REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    SPLIT_BATCH_TASK_ITEMS_VERSION,
)

runner = CliRunner()


def test_migrate_status_reports_pending_migrations_for_a_fresh_runtime_dir(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"

    result = runner.invoke(app, ["migrate", "status", "--runtime-dir", str(runtime_dir), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["current_version"] == 0
    assert payload["applied"] == []
    assert [entry["version"] for entry in payload["pending"]] == [
        BASELINE_VERSION,
        CREATE_VALIDATION_RESULTS_VERSION,
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
        CREATE_QUERY_PATH_INDEXES_VERSION,
        REWRITE_CONTRACT_IDENTITIES_VERSION,
        ADD_RUNS_MODE_PROJECTION_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
        REWRITE_MANIFEST_COMPONENT_PLANES_VERSION,
    ]


def test_migrate_run_dry_run_reports_without_mutating(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"

    dry_run = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir), "--dry-run"])

    assert dry_run.exit_code == 0, dry_run.output
    assert "would apply" in dry_run.output
    assert str(BASELINE_VERSION) in dry_run.output

    status_after = runner.invoke(
        app, ["migrate", "status", "--runtime-dir", str(runtime_dir), "--json"]
    )
    payload = json.loads(status_after.output)
    assert payload["current_version"] == 0  # dry run touched nothing


def test_migrate_run_applies_pending_migrations_and_prints_backup_path(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"

    result = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])

    assert result.exit_code == 0, result.output
    assert "backup:" in result.output
    # Migrations always run before `build_storage()` constructs any store (by design --
    # see storage/migrations.py's module docstring), so on this *first* call `runs`
    # doesn't exist yet and the demo migration still defers, even though this command now
    # goes on to construct the stores (Finding 1a's fix) immediately afterwards. Honest
    # reporting of that still-pending state is asserted separately below.
    assert "still pending" in result.output
    assert "demo_add_runs_archived_at" in result.output

    status_after = runner.invoke(
        app, ["migrate", "status", "--runtime-dir", str(runtime_dir), "--json"]
    )
    payload = json.loads(status_after.output)
    # Both precondition-free migrations land in this first call -- baseline and
    # create_validation_results (V2-P0B-010; ordered before the still-deferring demo
    # migration, see storage/migrations.py) -- not just baseline.
    assert payload["current_version"] == CREATE_VALIDATION_RESULTS_VERSION


def test_migrate_run_converges_after_it_constructs_stores_then_reports_up_to_date(
    tmp_path: Path,
) -> None:
    """Regression for two related defects the reviewer found in the same command.

    Finding 1a: `migrate run` used to call `run_migrations()` directly, bypassing
    `build_storage()` -- so it never constructed a store, never created `runs`, and the
    demo migration stayed pending forever no matter how many times it ran. Routing through
    `build_storage()` (this command's fix) constructs the stores as a side effect of the
    *first* call, so the *second* call's migration attempt finds `runs` already present
    and actually applies the demo migration -- exactly like
    `test_build_storage_catches_up_the_demo_migration_on_a_second_call`
    (tests/unit/runtime/test_composition_migrations.py) proves for the composition root
    directly.

    Finding 1b: the previous version of this test (`test_migrate_run_a_second_time_
    reports_up_to_date`, replaced here) asserted "up to date" on that second call and
    pinned the bug: `migrate_run` branched on `not result.applied` without ever checking
    `read_status().pending`, so it could not tell "genuinely finished" apart from
    "permanently stuck" and claimed completion regardless. With 1a fixed, the second call
    here does real work, so it must say so (`migrated <n> -> <m>`), not claim "up to date"
    -- that phrase is reserved for the *third* call, once nothing is applied and nothing
    is pending.

    The first call now advances past baseline on its own (V2-P0B-010's
    create_validation_results migration is precondition-free -- see
    storage/migrations.py), so the second call's real work starts from
    `CREATE_VALIDATION_RESULTS_VERSION`, not `BASELINE_VERSION`.
    """
    runtime_dir = tmp_path / "runtime"
    first = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])
    assert second.exit_code == 0, second.output
    # Every still-deferring migration (demo, create_query_path_indexes -- task 21,
    # rewrite_contract_identities -- V2-P4-001, add_runs_mode_projection -- V2-P4-002,
    # split_batch_task_items -- V2-P4-019) catches up together here: the first call's
    # build_storage() constructed every store, so all of their preconditions are met by
    # the time this second call's run_migrations() runs.
    assert (
        f"migrated {CREATE_VALIDATION_RESULTS_VERSION} -> "
        f"{REWRITE_MANIFEST_COMPONENT_PLANES_VERSION}" in second.output
    )
    assert "up to date" not in second.output
    assert "still pending" not in second.output

    status_result = runner.invoke(
        app, ["migrate", "status", "--runtime-dir", str(runtime_dir), "--json"]
    )
    status_after_second = json.loads(status_result.output)
    assert status_after_second["current_version"] == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    assert status_after_second["pending"] == []

    third = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])
    assert third.exit_code == 0, third.output
    assert "up to date" in third.output


def test_migrate_run_reports_the_horizon_refusal_the_operator_has_to_act_on(
    tmp_path: Path,
) -> None:
    """`V2-P4-001`'s one un-migratable case, through the surface an operator actually sees.

    `MigrationFailedError`'s own message names the migration and the backup and stops there,
    which is right for an arbitrary migration whose exception text is unvetted. It is wrong
    for this one: `UnmigratableHorizonError` is a refusal this package makes on purpose, and
    its message *is* the remedy -- which run carries a horizon `SignalFrame` no longer admits,
    and the two things that can be done about it. Without this the operator gets "migration 5
    failed" and no way to find the row.
    """
    runtime_dir = tmp_path / "runtime"
    assert runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)]).exit_code == 0
    assert runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)]).exit_code == 0

    stranded = {
        "schema_version": "run-recovery/v1",
        "run_id": "run_calendar_horizon",
        "request_digest": "a" * 64,
        "graph_signature": "a" * 64,
        "agent_ids": ["market-agent"],
        "completed_results": [
            {
                "agent_id": "market-agent",
                "signal": {
                    "schema_version": "signal-frame/v1",
                    "subject": "000001.SZ",
                    "as_of": "2026-01-16T07:00:00+00:00",
                    "direction": "bullish",
                    "strength": 0.4,
                    "confidence": 0.6,
                    "horizon": "3m",
                    "evidence_ids": ["ev_pre_p4"],
                    "confirmation_conditions": [],
                    "invalidation_conditions": [],
                    "risk_flags": [],
                    "abstention_reason": None,
                },
                "rationale": "ok",
            }
        ],
        "next_agent_index": 1,
        "attempt_count": 1,
        "status": "running",
        "started_at": "2026-01-16T07:00:00+00:00",
        "updated_at": "2026-01-16T07:00:00+00:00",
        "error_type": None,
    }
    path = runtime_dir / "state.sqlite3"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA user_version = 4")
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = ?",
            (REWRITE_CONTRACT_IDENTITIES_VERSION,),
        )
        connection.execute(
            """
            INSERT INTO run_recovery (run_id, request_digest, graph_signature, status, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("run_calendar_horizon", "a" * 64, "a" * 64, "running", json.dumps(stranded)),
        )

    failed = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])

    assert failed.exit_code == 1
    assert "rewrite_contract_identities" in failed.output
    assert "run_calendar_horizon" in failed.output
    assert "3m" in failed.output
    assert "trading days" in failed.output


def _stall_a_migrated_runtime_dir(runtime_dir: Path) -> None:
    """Turn a fully-migrated `runtime_dir` into the shape the version renumbering leaves.

    Rewinds the counter to 4 and rewrites the audit trail to what commit `6eba39c` leaves in a
    database that had already crossed version 2 under the previous numbering: version 2 named
    `demo_add_runs_archived_at` rather than `create_validation_results`, that name therefore
    recorded twice, and no `validation_results` table. This is the state measured on a copy of
    the user's own `runtime/state.sqlite3`. The engine-level test that *derives* the same shape
    by replaying both registry generations, rather than writing it out, is
    `tests/integration/storage/test_migration_repair.py`; this one exists to prove the command
    an operator actually types reports and resolves it.
    """
    path = runtime_dir / "state.sqlite3"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE IF EXISTS validation_results")
        connection.execute("DELETE FROM schema_migrations WHERE version >= ?", (2,))
        connection.executemany(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            [
                (2, "demo_add_runs_archived_at", "2026-08-07T12:33:46+00:00"),
                (3, "demo_add_runs_archived_at", "2026-08-07T18:50:18+00:00"),
                (4, "create_query_path_indexes", "2026-08-08T13:02:20+00:00"),
            ],
        )
        connection.execute("PRAGMA user_version = 4")


def test_migrate_status_names_the_skipped_migration_that_froze_the_chain(tmp_path: Path) -> None:
    """`migrate status` on the stalled database used to give an operator nothing to go on.

    It reported `schema version 4` with four migrations pending and no hint that the reason
    they were pending was a *fifth* migration sitting below the watermark, unrecorded and
    unapplied. `unrecorded` is that hint, and it is the difference between "your database is
    behind" and "your database cannot catch up".
    """
    runtime_dir = tmp_path / "runtime"
    assert runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)]).exit_code == 0
    assert runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)]).exit_code == 0
    _stall_a_migrated_runtime_dir(runtime_dir)

    result = runner.invoke(app, ["migrate", "status", "--runtime-dir", str(runtime_dir), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["current_version"] == CREATE_QUERY_PATH_INDEXES_VERSION
    assert [entry["name"] for entry in payload["unrecorded"]] == ["create_validation_results"]
    assert payload["repaired"] == []


def test_migrate_run_repairs_the_skipped_migration_and_reaches_head(tmp_path: Path) -> None:
    """The whole defect and its remedy, through the command a person types.

    One `migrate run` takes the stalled database from a permanent version 4 to head, says which
    migration it repaired and why, and a second run reports it up to date rather than repeating
    the repair. Before this, `migrate run` printed "4 migration(s) still pending" forever, on
    every invocation, with exit code 0.
    """
    runtime_dir = tmp_path / "runtime"
    assert runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)]).exit_code == 0
    assert runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)]).exit_code == 0
    _stall_a_migrated_runtime_dir(runtime_dir)

    repaired = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])

    assert repaired.exit_code == 0, repaired.output
    assert "repaired 2 create_validation_results" in repaired.output
    assert "its effect was missing and has been created" in repaired.output
    assert "still pending" not in repaired.output

    status = json.loads(
        runner.invoke(
            app, ["migrate", "status", "--runtime-dir", str(runtime_dir), "--json"]
        ).output
    )
    assert status["current_version"] == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    assert status["unrecorded"] == []
    assert [(entry["version"], entry["resolution"]) for entry in status["repaired"]] == [
        (CREATE_VALIDATION_RESULTS_VERSION, "applied")
    ]

    again = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])
    assert again.exit_code == 0, again.output
    assert "is up to date; nothing to do" in again.output
    assert "repaired" not in again.output


def test_migrate_run_that_only_repairs_does_not_claim_there_was_nothing_to_do(
    tmp_path: Path,
) -> None:
    """A run whose only work is a repair applies no migration and leaves nothing pending.

    That is the exact combination `migrate run` used to read as "up to date; nothing to do" --
    it branched on `result.applied` and `status.pending` alone, so a call that recreated a
    missing table would report having done nothing at all. The database here is at head with
    `validation_results` gone and its audit row with it, which is what a dropped table looks
    like from the engine's side; the command must say what it repaired.
    """
    runtime_dir = tmp_path / "runtime"
    assert runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)]).exit_code == 0
    assert runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)]).exit_code == 0
    with closing(sqlite3.connect(runtime_dir / "state.sqlite3")) as connection, connection:
        connection.execute("DROP TABLE validation_results")
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = ?", (CREATE_VALIDATION_RESULTS_VERSION,)
        )

    repaired = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])

    assert repaired.exit_code == 0, repaired.output
    assert "repaired 2 create_validation_results" in repaired.output
    assert "nothing to do" not in repaired.output
    status = json.loads(
        runner.invoke(
            app, ["migrate", "status", "--runtime-dir", str(runtime_dir), "--json"]
        ).output
    )
    assert status["current_version"] == REWRITE_MANIFEST_COMPONENT_PLANES_VERSION
    assert [entry["resolution"] for entry in status["repaired"]] == ["applied"]
