import json

from typer.testing import CliRunner

from openalpha_cn.cli import app

runner = CliRunner()


def test_version_reports_project_name_and_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "OpenAlpha CN 1.0.0"


def test_doctor_json_reports_required_runtime_checks() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["checks"]["python"]["ok"] is True
    assert payload["checks"]["timezone"]["ok"] is True


def test_doctor_human_output_names_each_runtime_check() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "PASS python" in result.stdout
    assert "PASS timezone" in result.stdout
