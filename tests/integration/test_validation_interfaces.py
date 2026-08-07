"""REST/SDK equivalence and persistence for outcome validation (V2-P0B-010).

Mirrors `test_portfolio_interfaces.py`'s equivalence style (Task 16's precedent): the same
research result and observation, driven once through `OpenAlphaSDK.validate_outcome` and
once through `POST /api/v1/backtests/validate`, must persist and return byte-for-byte the
same `ValidationResult`. Before this task, `POST /api/v1/backtests/validate` computed a
result and threw it away (nothing in `storage/` ever wrote a `ValidationResult`), and
`sdk.py` had no outcome-validation entry point at all (audit finding F29) -- a programmatic
caller could not validate an outcome without going through HTTP.
"""

from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.backtest.validation import OutcomeObservation
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.sdk import OpenAlphaSDK

DIGEST = "d" * 64


def _evidence(frozen_now: datetime) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        subject="000001.SZ",
        kind="limit_up",
        timeline=Timeline(
            event_time=frozen_now,
            available_time=frozen_now,
            ingested_time=frozen_now,
            revision_time=frozen_now,
        ),
        source_id="validation.fixture",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Validation interface fixture.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": [],
        },
    )


def _research_request(frozen_now: datetime) -> ResearchRunRequest:
    return ResearchRunRequest(
        run_id="validation-interface-run",
        mode="backtest",
        subject="000001.SZ",
        as_of=frozen_now,
        evidence=(_evidence(frozen_now),),
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
    )


def _observation_json(frozen_now: datetime) -> dict:
    return {
        "observation_start": frozen_now.isoformat(),
        "observation_end": (frozen_now + timedelta(days=5)).isoformat(),
        "start_price": 10.0,
        "end_price": 11.0,
        "benchmark_return": 0.02,
        "transaction_cost": 0.005,
        "data_quality_notes": ["Synthetic outcome."],
    }


def test_sdk_and_rest_validate_persist_and_return_the_same_result(
    tmp_path: Path, frozen_now: datetime
) -> None:
    request = _research_request(frozen_now)

    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "sdk", clock=lambda: frozen_now)
    sdk_research = sdk.run_research(request)
    sdk_result = sdk.validate_outcome(
        research=sdk_research,
        observation=OutcomeObservation(
            observation_start=frozen_now,
            observation_end=frozen_now + timedelta(days=5),
            start_price=10.0,
            end_price=11.0,
            benchmark_return=0.02,
            transaction_cost=0.005,
            data_quality_notes=("Synthetic outcome.",),
        ),
    )

    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: frozen_now))
    research_response = client.post(
        "/api/v1/research/run",
        json={
            "run_id": request.run_id,
            "mode": request.mode,
            "subject": request.subject,
            "as_of": request.as_of.isoformat(),
            "evidence": [
                item.model_dump(mode="json", exclude_computed_fields=True)
                for item in request.evidence
            ],
            "code_commit": request.code_commit,
            "config_digest": request.config_digest,
            "random_seed": request.random_seed,
        },
    )
    assert research_response.status_code == 200
    # Same content-addressed IDs regardless of which storage backend computed them.
    assert research_response.json()["decision"]["decision_id"] == sdk_result.decision_id
    assert research_response.json()["signal"]["signal_id"] == sdk_result.signal_id

    validate_response = client.post(
        "/api/v1/backtests/validate",
        json={"research": research_response.json(), "observation": _observation_json(frozen_now)},
    )

    assert validate_response.status_code == 200
    assert validate_response.json() == sdk_result.model_dump(mode="json")


def test_rest_persists_the_validation_and_serves_it_back_by_decision_and_signal(
    tmp_path: Path, frozen_now: datetime
) -> None:
    request = _research_request(frozen_now)
    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: frozen_now))
    research_response = client.post(
        "/api/v1/research/run",
        json={
            "run_id": request.run_id,
            "mode": request.mode,
            "subject": request.subject,
            "as_of": request.as_of.isoformat(),
            "evidence": [
                item.model_dump(mode="json", exclude_computed_fields=True)
                for item in request.evidence
            ],
            "code_commit": request.code_commit,
            "config_digest": request.config_digest,
            "random_seed": request.random_seed,
        },
    )
    assert research_response.status_code == 200
    decision_id = research_response.json()["decision"]["decision_id"]
    signal_id = research_response.json()["signal"]["signal_id"]

    # Not yet validated: both query routes report nothing on file.
    assert client.get(f"/api/v1/backtests/validations/by-decision/{decision_id}").json() == []
    assert client.get(f"/api/v1/backtests/validations/by-signal/{signal_id}").json() == []

    validate_response = client.post(
        "/api/v1/backtests/validate",
        json={"research": research_response.json(), "observation": _observation_json(frozen_now)},
    )
    assert validate_response.status_code == 200
    persisted = validate_response.json()

    by_decision = client.get(f"/api/v1/backtests/validations/by-decision/{decision_id}")
    assert by_decision.status_code == 200
    assert by_decision.json() == [persisted]

    by_signal = client.get(f"/api/v1/backtests/validations/by-signal/{signal_id}")
    assert by_signal.status_code == 200
    assert by_signal.json() == [persisted]

    # An unrelated ID sees nothing.
    assert client.get("/api/v1/backtests/validations/by-decision/dec_unrelated").json() == []
    assert client.get("/api/v1/backtests/validations/by-signal/sig_unrelated").json() == []

    # Idempotent: replaying the identical validate request is a no-op, not a duplicate row.
    replay = client.post(
        "/api/v1/backtests/validate",
        json={"research": research_response.json(), "observation": _observation_json(frozen_now)},
    )
    assert replay.status_code == 200
    assert client.get(f"/api/v1/backtests/validations/by-decision/{decision_id}").json() == [
        persisted
    ]


def test_sdk_validate_outcome_query_methods_round_trip(
    tmp_path: Path, frozen_now: datetime
) -> None:
    request = _research_request(frozen_now)
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "sdk", clock=lambda: frozen_now)
    research = sdk.run_research(request)

    assert sdk.list_validations_by_decision(research.decision.decision_id) == ()
    assert sdk.list_validations_by_signal(research.signal.signal_id) == ()

    result = sdk.validate_outcome(
        research=research,
        observation=OutcomeObservation(
            observation_start=frozen_now,
            observation_end=frozen_now + timedelta(days=5),
            start_price=10.0,
            end_price=11.0,
            benchmark_return=0.02,
            transaction_cost=0.005,
        ),
    )
    # Idempotent from the SDK side too.
    sdk.validate_outcome(
        research=research,
        observation=OutcomeObservation(
            observation_start=frozen_now,
            observation_end=frozen_now + timedelta(days=5),
            start_price=10.0,
            end_price=11.0,
            benchmark_return=0.02,
            transaction_cost=0.005,
        ),
    )

    assert sdk.list_validations_by_decision(research.decision.decision_id) == (result,)
    assert sdk.list_validations_by_signal(research.signal.signal_id) == (result,)
