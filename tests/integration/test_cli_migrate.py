import json
from pathlib import Path

from typer.testing import CliRunner

from openalpha_cn.cli import app
from openalpha_cn.storage.migrations import BASELINE_VERSION, DEMO_ADD_RUNS_ARCHIVED_AT_VERSION

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
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
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
    assert payload["current_version"] == BASELINE_VERSION


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
    here does real work, so it must say so (`migrated 1 -> 2`), not claim "up to date" --
    that phrase is reserved for the *third* call, once nothing is applied and nothing is
    pending.
    """
    runtime_dir = tmp_path / "runtime"
    first = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])
    assert second.exit_code == 0, second.output
    assert f"migrated {BASELINE_VERSION} -> {DEMO_ADD_RUNS_ARCHIVED_AT_VERSION}" in second.output
    assert "up to date" not in second.output
    assert "still pending" not in second.output

    status_result = runner.invoke(
        app, ["migrate", "status", "--runtime-dir", str(runtime_dir), "--json"]
    )
    status_after_second = json.loads(status_result.output)
    assert status_after_second["current_version"] == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    assert status_after_second["pending"] == []

    third = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])
    assert third.exit_code == 0, third.output
    assert "up to date" in third.output
