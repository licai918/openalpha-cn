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
from typing import Final

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


# --- V2-P5-005 / V2-P5-006: the residual has to survive the trip to a caller ---------------
#
# The unit control lives in `tests/unit/backtest/test_validation.py`; this is the same
# closed-form corpus driven through the two faces a caller actually has, because a residual
# that is computed and then dropped on the way out is the defect `V2-P5-006` is about. Both
# faces serialise `ValidationResult`, so `unexplained_return` reaches them only if it is a real
# field on the model rather than something the validator knew and did not say.

CONTROL_START_PRICE: Final[float] = 100.0
CONTROL_END_PRICE: Final[float] = 125.0
CONTROL_BENCHMARK: Final[float] = 0.0625
CONTROL_COST: Final[float] = 0.0078125
CONTROL_HELD_NET: Final[float] = 0.1796875
CONTROL_HELD_RESIDUAL: Final[float] = 0.1875
CONTROL_FLAT_NET: Final[float] = -0.0703125


def _control_observation(frozen_now: datetime) -> OutcomeObservation:
    return OutcomeObservation(
        observation_start=frozen_now,
        observation_end=frozen_now + timedelta(days=5),
        start_price=CONTROL_START_PRICE,
        end_price=CONTROL_END_PRICE,
        benchmark_return=CONTROL_BENCHMARK,
        transaction_cost=CONTROL_COST,
    )


def _validate_over_rest(client: TestClient, research, frozen_now: datetime) -> dict:
    """Drive `POST /api/v1/backtests/validate` with a result this test built.

    `model_dump(mode="json")` carries the three computed identifiers `_parse_research_result`
    re-derives and checks, so a decision whose `final_action` was changed arrives with the
    `decision_id` that content actually addresses -- which is why the flat arm can be posted
    at all rather than being refused as a tampered record.
    """
    response = client.post(
        "/api/v1/backtests/validate",
        json={
            "research": research.model_dump(mode="json"),
            "observation": _control_observation(frozen_now).model_dump(mode="json"),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_held_positions_unexplained_return_reaches_both_faces_unsplit(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The held arm, end to end: `0.1875` of a `0.1796875` net active return is not claimed.

    Before `V2-P5-005` both faces answered with three terms worth 20%, 30% and 50% of the net
    and an `unexplained_return` of `0.0`, so a caller reading either one was told the whole
    active return had been accounted for. The assertion is on the exact term list rather than
    on a sum, because a sum is what the deleted last-term-absorbs trick always satisfied.
    """
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "sdk", clock=lambda: frozen_now)
    research = sdk.run_research(_research_request(frozen_now))
    assert research.decision.final_action == "watch"

    from_sdk = sdk.validate_outcome(research=research, observation=_control_observation(frozen_now))
    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: frozen_now))
    from_rest = _validate_over_rest(client, research, frozen_now)

    assert from_rest == from_sdk.model_dump(mode="json")
    assert from_rest["net_active_return"] == CONTROL_HELD_NET
    assert from_rest["unexplained_return"] == CONTROL_HELD_RESIDUAL
    assert from_rest["attribution"] == [
        {"category": "rule", "name": "transaction-cost", "contribution": -CONTROL_COST}
    ]
    # The residual is queryable afterwards, not just present in the reply.
    stored = client.get(
        f"/api/v1/backtests/validations/by-decision/{research.decision.decision_id}"
    ).json()
    assert [row["unexplained_return"] for row in stored] == [CONTROL_HELD_RESIDUAL]
    assert sdk.list_validations_by_signal(research.signal.signal_id)[0].unexplained_return == (
        CONTROL_HELD_RESIDUAL
    )


def test_a_decision_that_took_no_position_is_fully_attributed_on_both_faces(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The flat arm, end to end, and the reason one arm could not have pinned either.

    Standing flat forgoes exactly the benchmark's move, with one claimant and nothing left
    over, so here `unexplained_return` is `0.0` -- and it is `0.0` because it was measured, not
    because nobody set it. A build that routed every active return into the residual would
    serve the held arm above correctly and this one wrong.
    """
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "sdk", clock=lambda: frozen_now)
    research = sdk.run_research(_research_request(frozen_now))
    flat = research.model_copy(
        update={"decision": research.decision.model_copy(update={"final_action": "avoid"})}
    )
    assert flat.decision.decision_id != research.decision.decision_id

    from_sdk = sdk.validate_outcome(research=flat, observation=_control_observation(frozen_now))
    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: frozen_now))
    from_rest = _validate_over_rest(client, flat, frozen_now)

    assert from_rest == from_sdk.model_dump(mode="json")
    assert from_rest["realized_return"] == 0.0
    assert from_rest["net_active_return"] == CONTROL_FLAT_NET
    assert from_rest["unexplained_return"] == 0.0
    assert from_rest["attribution"] == [
        {
            "category": "rule",
            "name": "no-position-versus-benchmark",
            "contribution": -CONTROL_BENCHMARK,
        },
        {"category": "rule", "name": "transaction-cost", "contribution": -CONTROL_COST},
    ]
