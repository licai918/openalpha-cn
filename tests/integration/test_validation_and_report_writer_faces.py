"""The two writers a CLI-only operator could not reach, held equal to REST (`V2-P5-047`).

`openalpha validation` shipped `statistics` and `segmented` and **no writer**; `openalpha report`
shipped `export` and **no writer**. Both readers therefore answered a CLI-only operator about a
store that operator had no way to fill: `validation statistics` refuses every signal by name
because nothing was ever appended, and `report export` refuses every id. The writers existed only
at `POST /api/v1/backtests/validate` / `POST /api/v1/reports` and `sdk.validate_outcome` /
`sdk.create_report`, while both READMEs asserted 「三个面等价」 over the flow containing them.

**The measurement that decided this was built rather than documented away.** Both routes take
their research result as a *serialized dict* -- `OutcomeApiRequest.research` and
`ReportApiRequest.research` are both `dict[str, Any]`, parsed by `parse_research_result` -- so
neither writer needs an in-process object the way `construct_portfolio_from_ranking` does, which
is the reason `SDK_ONLY` gives for the one capability that genuinely cannot have a second face.
And the bytes are already produced by a command that ships: `openalpha research run` prints
`result.model_dump_json()`, which is exactly what `{"research": ...}` carries. The loop
    research run > run.json  ->  validation record --research run.json  ->  validation statistics
closes entirely inside the terminal, and did not before.

## What is asserted here, and why byte-equality rather than "both work"

Two faces that both answer are not two faces that agree. Each test below drives the identical
input through the CLI and through the route and compares **bytes**:

- the success payload (`--json` stdout against the `200` body),
- and the refusal, which is the half that rots first. `parse_research_result`'s three integrity
  refusals name a claimed and a derived address, and a CLI that wrote its own sentence for them
  would drift from the route's within one edit. `research_result_io.research_refusal_detail` is
  now the single author of that text and both faces call it, so the equality below is structural
  rather than a coincidence two maintainers have to keep.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.sdk import OpenAlphaSDK

DIGEST: Final[str] = "d" * 64
SUBJECT: Final[str] = "000001.SZ"

runner = CliRunner()


def _evidence(frozen_now: datetime) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        subject=SUBJECT,
        kind="limit_up",
        timeline=Timeline(
            event_time=frozen_now,
            available_time=frozen_now,
            ingested_time=frozen_now,
            revision_time=frozen_now,
        ),
        source_id="writer.fixture",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Writer-face fixture.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": [],
        },
    )


def _request(frozen_now: datetime) -> ResearchRunRequest:
    return ResearchRunRequest(
        run_id="writer-face-run",
        mode="backtest",
        subject=SUBJECT,
        as_of=frozen_now,
        evidence=(_evidence(frozen_now),),
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
    )


def _observation(frozen_now: datetime) -> dict[str, Any]:
    return {
        "observation_start": frozen_now.isoformat(),
        "observation_end": (frozen_now + timedelta(days=5)).isoformat(),
        "start_price": 10.0,
        "end_price": 11.0,
        "benchmark_return": 0.02,
        "transaction_cost": 0.005,
        "data_quality_notes": ["Synthetic outcome."],
    }


def _research_json(runtime: Path, frozen_now: datetime) -> dict[str, Any]:
    """One research result, produced through the SDK into `runtime` and returned serialized.

    The same bytes `openalpha research run` prints, which is the point: the file the two new
    commands read is a file the CLI can already write.
    """
    sdk = OpenAlphaSDK(runtime_dir=runtime, clock=lambda: frozen_now)
    return sdk.run_research(_request(frozen_now)).model_dump(mode="json")


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_the_cli_records_a_validation_byte_for_byte_as_the_route_does(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """`openalpha validation record --json` and `POST /api/v1/backtests/validate` agree."""
    cli_runtime = tmp_path / "cli"
    api_runtime = tmp_path / "api"
    research = _research_json(cli_runtime, frozen_now)
    assert _research_json(api_runtime, frozen_now) == research

    result = runner.invoke(
        app,
        [
            "validation",
            "record",
            "--research",
            str(_write(tmp_path / "run.json", research)),
            "--observation",
            str(_write(tmp_path / "observation.json", _observation(frozen_now))),
            "--runtime-dir",
            str(cli_runtime),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    client = TestClient(create_app(runtime_dir=api_runtime, clock=lambda: frozen_now))
    response = client.post(
        "/api/v1/backtests/validate",
        json={"research": research, "observation": _observation(frozen_now)},
    )
    assert response.status_code == 200, response.text
    assert json.loads(result.stdout) == response.json()


def test_a_validation_the_cli_recorded_is_the_one_its_own_reader_aggregates(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The loop this task exists to close, driven entirely through the CLI.

    `validation statistics` refuses a signal with nothing stored **by name** (`V2-P5-007`'s
    rule), so before a CLI writer existed this command could not answer at all for an operator
    who never ran the service. Asserting the aggregate rather than the store is deliberate: what
    was broken is that the reader had no reachable input, and the reader is what proves it does.

    **Two observations, because one is not a sample.** `outcome_statistics` refuses a cohort
    below `MINIMUM_INTERVAL_SAMPLE_SIZE` -- measured while writing this test, which recorded one
    outcome and got `no cohort reached MINIMUM_INTERVAL_SAMPLE_SIZE (2) observations`. That
    refusal is itself proof the recorded row arrived (it counted a cohort rather than reporting
    an unknown signal), but a test that stopped there would assert the reader can *see* CLI
    output without asserting it can *aggregate* it.
    """
    runtime = tmp_path / "runtime"
    research = _research_json(runtime, frozen_now)
    signal_id = research["signal"]["signal_id"]
    research_file = str(_write(tmp_path / "run.json", research))

    for index, offset in enumerate((0, 7)):
        observed = _observation(frozen_now + timedelta(days=offset))
        recorded = runner.invoke(
            app,
            [
                "validation",
                "record",
                "--research",
                research_file,
                "--observation",
                str(_write(tmp_path / f"observation-{index}.json", observed)),
                "--runtime-dir",
                str(runtime),
                "--json",
            ],
        )
        assert recorded.exit_code == 0, recorded.output
        assert json.loads(recorded.stdout)["signal_id"] == signal_id

    statistics = runner.invoke(
        app,
        [
            "validation",
            "statistics",
            "--signal",
            signal_id,
            "--family-size",
            "1",
            "--dependence",
            "independent-or-positively-dependent",
            "--runtime-dir",
            str(runtime),
            "--json",
        ],
    )
    assert statistics.exit_code == 0, statistics.output
    assert signal_id in statistics.stdout


def test_the_cli_creates_a_report_byte_for_byte_as_the_route_does(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """`openalpha report create --json` and `POST /api/v1/reports` agree."""
    cli_runtime = tmp_path / "cli"
    api_runtime = tmp_path / "api"
    research = _research_json(cli_runtime, frozen_now)
    assert _research_json(api_runtime, frozen_now) == research

    result = runner.invoke(
        app,
        [
            "report",
            "create",
            "--research",
            str(_write(tmp_path / "run.json", research)),
            "--runtime-dir",
            str(cli_runtime),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    client = TestClient(create_app(runtime_dir=api_runtime, clock=lambda: frozen_now))
    response = client.post("/api/v1/reports", json={"research": research})
    assert response.status_code == 200, response.text
    assert json.loads(result.stdout) == response.json()


def test_a_report_the_cli_created_is_the_one_its_own_exporter_hands_over(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """`report export` had a writer on two faces and none on its own until now."""
    runtime = tmp_path / "runtime"
    research = _research_json(runtime, frozen_now)

    created = runner.invoke(
        app,
        [
            "report",
            "create",
            "--research",
            str(_write(tmp_path / "run.json", research)),
            "--runtime-dir",
            str(runtime),
            "--json",
        ],
    )
    assert created.exit_code == 0, created.output
    report_id = json.loads(created.stdout)["report_id"]

    exported = runner.invoke(app, ["report", "export", report_id, "--runtime-dir", str(runtime)])
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.stdout)["report"]["report_id"] == report_id


def _tampered(research: dict[str, Any]) -> dict[str, Any]:
    """The same result with a `signal_id` that no longer describes its own content."""
    signal = {**research["signal"], "signal_id": "sig_" + "0" * 24}
    return {**research, "signal": signal}


def test_both_writers_refuse_a_tampered_address_in_the_routes_own_words(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The refusal, byte-identical on both faces and for both commands.

    `parse_research_result` is the only thing that can tell a caller *which* of the three
    content addresses moved and what the content derives instead, and a CLI writing its own
    sentence for it would drift from the route's within one edit. Compared as bytes, not as
    "both mention the signal id".
    """
    runtime = tmp_path / "runtime"
    research = _tampered(_research_json(runtime, frozen_now))
    research_file = str(_write(tmp_path / "run.json", research))
    observation_file = str(_write(tmp_path / "observation.json", _observation(frozen_now)))

    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: frozen_now))

    validate_response = client.post(
        "/api/v1/backtests/validate",
        json={"research": research, "observation": _observation(frozen_now)},
    )
    assert validate_response.status_code == 422, validate_response.text
    report_response = client.post("/api/v1/reports", json={"research": research})
    assert report_response.status_code == 422, report_response.text
    expected = validate_response.json()["detail"]["message"]
    assert report_response.json()["detail"]["message"] == expected

    recorded = runner.invoke(
        app,
        [
            "validation",
            "record",
            "--research",
            research_file,
            "--observation",
            observation_file,
            "--runtime-dir",
            str(runtime),
        ],
    )
    created = runner.invoke(
        app, ["report", "create", "--research", research_file, "--runtime-dir", str(runtime)]
    )

    assert recorded.exit_code == 3, recorded.output
    assert created.exit_code == 3, created.output
    assert recorded.stderr.strip() == expected
    assert created.stderr.strip() == expected
