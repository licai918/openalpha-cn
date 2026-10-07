from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from openalpha_cn.agents.base import AgentResult
from openalpha_cn.api.app import create_app
from openalpha_cn.domain.signal import SignalFrame


def test_committee_and_event_study_are_public_api_capabilities(
    tmp_path: Path, frozen_now: datetime
) -> None:
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: frozen_now))
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    signal = SignalFrame(
        subject="000001.SZ",
        as_of=now,
        direction="bullish",
        strength=0.5,
        confidence=0.8,
        horizon="5d",
        evidence_ids=("ev-1",),
    )
    agent = AgentResult(agent_id="bull", signal=signal, rationale="evidence case")

    committee = client.post(
        "/api/v1/research/deliberate",
        json={
            "signal": signal.model_dump(mode="json", exclude_computed_fields=True),
            "agent_results": [agent.model_dump(mode="json", exclude_computed_fields=True)],
        },
    )
    study = client.post(
        "/api/v1/backtests/event-study",
        json={
            "windows": [
                {
                    "event_id": "e1",
                    "asset_returns": [0.03],
                    "benchmark_returns": [0.01],
                },
                {
                    "event_id": "e2",
                    "asset_returns": [0.04],
                    "benchmark_returns": [0.01],
                },
            ],
            "bootstrap_samples": 100,
            "random_seed": 7,
        },
    )

    assert committee.status_code == 200
    assert committee.json()["ablation"]["enabled"] is True
    assert study.status_code == 200
    assert study.json()["sample_size"] == 2
