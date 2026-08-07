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

    status_after = runner.invoke(
        app, ["migrate", "status", "--runtime-dir", str(runtime_dir), "--json"]
    )
    payload = json.loads(status_after.output)
    # A pure-CLI runtime dir never has a store construct `runs`, so the demo migration
    # correctly defers forever here -- this is the same precondition proven directly in
    # tests/integration/storage/test_migrations.py, exercised end to end through the CLI.
    assert payload["current_version"] == BASELINE_VERSION


def test_migrate_run_a_second_time_reports_up_to_date(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])

    second = runner.invoke(app, ["migrate", "run", "--runtime-dir", str(runtime_dir)])

    assert second.exit_code == 0, second.output
    assert "up to date" in second.output
