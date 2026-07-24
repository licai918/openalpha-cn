import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.providers.base import ProviderMetadata, ProviderRequest
from openalpha_cn.providers.file import FileProvider

runner = CliRunner()
AS_OF = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)


def write_source(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "subject": "000001.SZ",
                    "kind": "limit_up",
                    "event_time": "2026-07-24T09:30:00+00:00",
                    "available_time": "2026-07-24T10:00:00+00:00",
                    "ingested_time": "2026-07-24T10:01:00+00:00",
                    "revision_time": "2026-07-24T10:00:00+00:00",
                    "source_uri": "fixture://limit-up/000001.SZ",
                    "summary": "Synthetic limit-up event.",
                    "payload": {
                        "close": 10.5,
                        "pct_change": 9.99,
                        "board_count": 1,
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def metadata() -> ProviderMetadata:
    return ProviderMetadata(
        provider_id="user.file",
        display_name="User file",
        source_license="user-supplied",
        redistribution="restricted",
        credential_env_vars=(),
        caching_policy="local-permitted",
        rate_limit="not-applicable",
        freshness="defined-by-input",
        failure_semantics="Invalid input is an explicit failure.",
    )


def test_cli_and_api_return_the_same_evidence_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "events.json"
    write_source(source)

    cli_result = runner.invoke(
        app,
        [
            "evidence",
            "build",
            str(source),
            "--as-of",
            AS_OF.isoformat(),
            "--source-id",
            "user.file",
            "--source-license",
            "user-supplied",
            "--redistribution",
            "restricted",
        ],
    )
    assert cli_result.exit_code == 0, cli_result.stdout
    cli_payload = json.loads(cli_result.stdout)

    provider = FileProvider(
        path=source,
        metadata=metadata(),
        clock=lambda: AS_OF,
    )
    batch = provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF))
    response = TestClient(create_app()).post(
        "/api/v1/evidence/build",
        json={
            "metadata": metadata().model_dump(mode="json"),
            "batch": batch.model_dump(mode="json", exclude_computed_fields=True),
        },
    )

    assert response.status_code == 200
    assert response.json() == cli_payload
    assert response.json()["items"][0]["evidence_id"].startswith("ev_")


def test_api_exposes_health_and_versioned_openapi(tmp_path: Path) -> None:
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime"))

    assert client.get("/health").json() == {"status": "ok", "version": "0.1.0"}
    schema = client.get("/openapi.json").json()
    assert "/api/v1/evidence/build" in schema["paths"]


def test_api_persists_and_queries_built_evidence(tmp_path: Path) -> None:
    source = tmp_path / "events.json"
    write_source(source)
    provider = FileProvider(path=source, metadata=metadata(), clock=lambda: AS_OF)
    batch = provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF))
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime"))

    built = client.post(
        "/api/v1/evidence/build",
        json={
            "metadata": metadata().model_dump(mode="json"),
            "batch": batch.model_dump(mode="json", exclude_computed_fields=True),
        },
    )
    queried = client.get(
        "/api/v1/evidence",
        params={"as_of": AS_OF.isoformat(), "subject": "000001.SZ"},
    )

    assert built.status_code == 200
    assert queried.status_code == 200
    assert queried.json() == built.json()


def test_api_runs_research_from_structured_evidence(tmp_path: Path) -> None:
    source = tmp_path / "events.json"
    write_source(source)
    provider = FileProvider(path=source, metadata=metadata(), clock=lambda: AS_OF)
    batch = provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF))
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime"))
    built = client.post(
        "/api/v1/evidence/build",
        json={
            "metadata": metadata().model_dump(mode="json"),
            "batch": batch.model_dump(mode="json", exclude_computed_fields=True),
        },
    ).json()

    response = client.post(
        "/api/v1/research/run",
        json={
            "run_id": "api-golden-run",
            "mode": "live",
            "subject": "000001.SZ",
            "as_of": AS_OF.isoformat(),
            "evidence": built["items"],
            "code_commit": "0123456789abcdef",
            "config_digest": "e" * 64,
            "random_seed": 7,
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"]["final_action"] == "watch"
    assert response.json()["signal"]["evidence_ids"] == [built["items"][0]["evidence_id"]]
