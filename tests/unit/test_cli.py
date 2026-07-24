import json

from typer.testing import CliRunner

from openalpha_cn.cli import app

runner = CliRunner()


def test_version_reports_project_name_and_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "OpenAlpha CN 0.1.0"


def test_doctor_json_reports_required_runtime_checks() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["checks"]["python"]["ok"] is True
    assert payload["checks"]["timezone"]["ok"] is True
