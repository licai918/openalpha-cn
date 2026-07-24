import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from openalpha_cn.cli import app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline

runner = CliRunner()
NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)


def test_cli_runs_research_from_serialized_evidence(tmp_path: Path) -> None:
    evidence = EvidenceSnapshot(
        subject="000001.SZ",
        kind="limit_up",
        timeline=Timeline(
            event_time=NOW,
            available_time=NOW,
            ingested_time=NOW,
            revision_time=NOW,
        ),
        source_id="synthetic",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Synthetic limit-up.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": [],
        },
    )
    input_path = tmp_path / "evidence.json"
    input_path.write_text(
        json.dumps({"items": [evidence.model_dump(mode="json")]}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "research",
            "run",
            str(input_path),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--run-id",
            "cli-golden-run",
            "--mode",
            "live",
            "--subject",
            "000001.SZ",
            "--as-of",
            NOW.isoformat(),
            "--code-commit",
            "0123456789abcdef",
            "--config-digest",
            "a" * 64,
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["decision"]["final_action"] == "watch"
    assert payload["signal"]["evidence_ids"] == [evidence.evidence_id]
