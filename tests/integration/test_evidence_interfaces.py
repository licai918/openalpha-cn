import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.providers.base import ProviderMetadata, ProviderRequest
from openalpha_cn.providers.file import FileProvider

runner = CliRunner()


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


def test_cli_and_api_return_the_same_evidence_snapshot(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    AS_OF = frozen_now
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
        metadata=metadata,
        clock=lambda: AS_OF,
    )
    batch = provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF))
    response = TestClient(create_app(clock=lambda: AS_OF)).post(
        "/api/v1/evidence/build",
        json={
            "metadata": metadata.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json", exclude_computed_fields=True),
        },
    )

    assert response.status_code == 200
    assert response.json() == cli_payload
    assert response.json()["items"][0]["evidence_id"].startswith("ev_")


def test_api_exposes_health_and_versioned_openapi(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    client = TestClient(
        create_app(runtime_dir=tmp_path / "runtime", clock=lambda: plain_frozen_now)
    )

    health = client.get("/health")
    assert health.json() == {"status": "ok", "version": "1.0.0"}
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert health.headers["content-security-policy"].startswith("default-src 'self'")
    schema = client.get("/openapi.json").json()
    assert "/api/v1/evidence/build" in schema["paths"]


def test_api_serves_built_web_assets_without_shadowing_routes(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<h1>OpenAlpha CN</h1>", encoding="utf-8")
    client = TestClient(
        create_app(
            runtime_dir=tmp_path / "runtime", web_dir=web_dir, clock=lambda: plain_frozen_now
        )
    )

    assert client.get("/").text == "<h1>OpenAlpha CN</h1>"
    assert client.get("/health").json()["status"] == "ok"


def test_api_rejects_declared_oversized_request_body(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    client = TestClient(
        create_app(
            runtime_dir=tmp_path / "runtime",
            max_request_bytes=32,
            clock=lambda: plain_frozen_now,
        )
    )

    response = client.post(
        "/api/v1/evidence/build",
        content=b"x" * 33,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    # `V2-P4-043`: the refusal names the knob that raises it, and both sides of the comparison.
    detail = response.json()["detail"]
    assert detail["reason"] == "request_too_large"
    assert "OPENALPHA_MAX_REQUEST_BYTES" in detail["message"]
    assert detail["declared_bytes"] == 33
    assert detail["limit_bytes"] == 32


def test_api_persists_and_queries_built_evidence(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    AS_OF = frozen_now
    source = tmp_path / "events.json"
    write_source(source)
    provider = FileProvider(path=source, metadata=metadata, clock=lambda: AS_OF)
    batch = provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF))
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime", clock=lambda: AS_OF))

    built = client.post(
        "/api/v1/evidence/build",
        json={
            "metadata": metadata.model_dump(mode="json"),
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


def test_api_runs_research_from_structured_evidence(
    tmp_path: Path, metadata: ProviderMetadata, frozen_now: datetime
) -> None:
    AS_OF = frozen_now
    source = tmp_path / "events.json"
    write_source(source)
    provider = FileProvider(path=source, metadata=metadata, clock=lambda: AS_OF)
    batch = provider.fetch(ProviderRequest(dataset="events", as_of=AS_OF))
    client = TestClient(create_app(runtime_dir=tmp_path / "runtime", clock=lambda: AS_OF))
    built = client.post(
        "/api/v1/evidence/build",
        json={
            "metadata": metadata.model_dump(mode="json"),
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

    memory = client.get("/api/v1/memory/000001.SZ")
    recovery = client.get("/api/v1/runs/api-golden-run/recovery")

    assert memory.status_code == 200
    assert memory.json()[0]["decision_id"] == response.json()["decision"]["decision_id"]
    assert recovery.status_code == 200
    assert recovery.json()["status"] == "succeeded"
    assert recovery.json()["next_agent_index"] == 1

    validation = client.post(
        "/api/v1/backtests/validate",
        json={
            "research": response.json(),
            "observation": {
                "observation_start": AS_OF.isoformat(),
                "observation_end": (AS_OF + timedelta(days=5)).isoformat(),
                "start_price": 10.0,
                "end_price": 11.0,
                "benchmark_return": 0.02,
                "transaction_cost": 0.005,
                "data_quality_notes": ["Synthetic outcome."],
            },
        },
    )

    assert validation.status_code == 200
    payload = validation.json()
    assert payload["signal_id"] == response.json()["signal"]["signal_id"]
    assert payload["decision_id"] == response.json()["decision"]["decision_id"]
    assert payload["net_active_return"] == pytest.approx(0.075)
    # `V2-P5-005`: the categories this used to name -- one `rule`, one `factor`, one `agent`,
    # worth a fixed 20/30/50 of the net -- were invented, and asserting the *set* of them could
    # not have noticed. The face now reports only what it measured, and says so as a number: a
    # held position leaves `realized - benchmark` unattributed rather than splitting it.
    assert [(term["category"], term["name"]) for term in payload["attribution"]] == [
        ("rule", "transaction-cost")
    ]
    assert payload["attribution"][0]["contribution"] == pytest.approx(-0.005)
    assert payload["unexplained_return"] == pytest.approx(0.08)
    assert sum(term["contribution"] for term in payload["attribution"]) + payload[
        "unexplained_return"
    ] == pytest.approx(payload["net_active_return"])

    tampered = response.json()
    tampered["signal"]["signal_id"] = "sig_tampered"
    rejected = client.post(
        "/api/v1/backtests/validate",
        json={
            "research": tampered,
            "observation": {
                "observation_start": AS_OF.isoformat(),
                "observation_end": (AS_OF + timedelta(days=5)).isoformat(),
                "start_price": 10.0,
                "end_price": 11.0,
                "benchmark_return": 0.02,
                "transaction_cost": 0.005,
            },
        },
    )
    assert rejected.status_code == 422
    # `V2-P4-041`: the refusal names which of the three content addresses moved, and on which
    # record, instead of one sentence for all four causes.
    detail = rejected.json()["detail"]
    assert detail["reason"] == "signal_id_mismatch"
    assert detail["index"] is None
    assert detail["field"] == "research.signal.signal_id"
    assert detail["claimed"] != detail["derived"]
