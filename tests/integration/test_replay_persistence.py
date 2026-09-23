"""Replay-produced validation results are persisted and retrievable through the SDK and
REST composition roots (P0.B acceptance review, Finding 1).

Before this fix, `backtest/replay.py#ReplayRunner.run()` constructed
`SQLiteRunRepository`/`SQLiteRecoveryStore` directly against its own `sdk-replay.sqlite3`/
`api-replay.sqlite3` files, bypassing `build_storage()` entirely (never migrated, verified
stuck at `user_version = 0`), and never called a validation store at all -- so the
`ReplayReport.validation_ids` a replay run returned could not be retrieved through any
query interface. This file proves both halves fixed, end to end, through `sdk.py`'s and
`api/app.py`'s real composition roots -- not `ReplayRunner.run()` directly (see
`tests/unit/backtest/test_replay.py` for that lower-level proof).
"""

from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.backtest.replay import ReplayCase, ReplayCorpus
from openalpha_cn.backtest.validation import OutcomeObservation
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.storage.migrations import read_status

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
        source_id="replay-persistence.fixture",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Replay persistence fixture.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": [],
        },
    )


def _corpus(frozen_now: datetime, *, run_id: str) -> ReplayCorpus:
    case = ReplayCase(
        run_id=run_id,
        trading_day=frozen_now.date(),
        subject="000001.SZ",
        as_of=frozen_now,
        evidence=(_evidence(frozen_now),),
        outcome=OutcomeObservation(
            observation_start=frozen_now,
            observation_end=frozen_now + timedelta(hours=1),
            start_price=10.0,
            end_price=10.5,
            benchmark_return=0.01,
            transaction_cost=0.001,
        ),
    )
    return ReplayCorpus(
        schema_version="openalpha-replay-corpus/v1",
        trading_days=(frozen_now.date(),),
        cases=(case,),
    )


def test_sdk_replay_persists_validation_results_queryable_by_decision_and_signal(
    tmp_path: Path, frozen_now: datetime
) -> None:
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "sdk", clock=lambda: frozen_now)
    run_id = "sdk-replay-persistence-run"

    # A real caller learns the `decision_id`/`signal_id` to query afterwards the same way
    # this does: `ResearchEngine.run_cycle` is deterministic, so this direct run and the
    # replay case below (identical run_id/subject/as_of/evidence/code_commit/config_digest/
    # random_seed) produce byte-identical manifests and decisions.
    #
    # `mode` joined that list at V2-P4-025 and is why this run is `replay` rather than
    # `live`: `DecisionLedger.run_manifest_id` is the manifest's content address and `mode`
    # is one of the declared inputs inside it, so a live run and a replayed one no longer
    # share a `decision_id`. That is the intended reading -- a decision reached under replay
    # is not the same decision reached live, and before this change nothing could tell them
    # apart -- and `ReplayRunner` runs its cases as `replay`.
    research = sdk.run_research(
        ResearchRunRequest(
            run_id=run_id,
            mode="replay",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(_evidence(frozen_now),),
            code_commit="0123456789abcdef",
            config_digest=DIGEST,
            random_seed=7,
        )
    )
    assert sdk.list_validations_by_decision(research.decision.decision_id) == ()

    report = sdk.replay(
        corpus=_corpus(frozen_now, run_id=run_id),
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
    )

    assert report.succeeded == 1
    assert len(report.validation_ids) == 1

    stored = sdk.list_validations_by_decision(research.decision.decision_id)
    assert len(stored) == 1
    assert stored[0].validation_id == report.validation_ids[0]
    assert sdk.list_validations_by_signal(research.signal.signal_id) == stored

    # Finding 1's first half: the dedicated replay run/recovery database is migrated, not
    # permanently stuck at `user_version = 0` (the acceptance reviewer's other check).
    assert read_status(tmp_path / "sdk" / "sdk-replay.sqlite3").current_version != 0


def test_rest_replay_persists_validation_results_retrievable_through_the_existing_endpoints(
    tmp_path: Path, frozen_now: datetime
) -> None:
    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: frozen_now))
    run_id = "rest-replay-persistence-run"
    item = _evidence(frozen_now)

    research_response = client.post(
        "/api/v1/research/run",
        json={
            "run_id": run_id,
            # `replay`, not `live`: see the sibling SDK test above for why `mode` now reaches
            # `decision_id` through `DecisionLedger.run_manifest_id` (V2-P4-025).
            "mode": "replay",
            "subject": "000001.SZ",
            "as_of": frozen_now.isoformat(),
            "evidence": [item.model_dump(mode="json", exclude_computed_fields=True)],
            "code_commit": "0123456789abcdef",
            "config_digest": DIGEST,
            "random_seed": 7,
        },
    )
    assert research_response.status_code == 200, research_response.text
    decision_id = research_response.json()["decision"]["decision_id"]
    signal_id = research_response.json()["signal"]["signal_id"]
    assert client.get(f"/api/v1/backtests/validations/by-decision/{decision_id}").json() == []

    corpus = _corpus(frozen_now, run_id=run_id)
    replay_response = client.post(
        "/api/v1/backtests/replay",
        json={
            "corpus": corpus.model_dump(mode="json", exclude_computed_fields=True),
            "code_commit": "0123456789abcdef",
            "config_digest": DIGEST,
            "random_seed": 7,
        },
    )
    assert replay_response.status_code == 200, replay_response.text
    replay_report = replay_response.json()
    assert replay_report["succeeded"] == 1
    assert len(replay_report["validation_ids"]) == 1

    by_decision = client.get(f"/api/v1/backtests/validations/by-decision/{decision_id}")
    assert by_decision.status_code == 200
    stored = by_decision.json()
    assert len(stored) == 1
    assert stored[0]["validation_id"] == replay_report["validation_ids"][0]

    by_signal = client.get(f"/api/v1/backtests/validations/by-signal/{signal_id}")
    assert by_signal.status_code == 200
    assert by_signal.json() == stored

    # Finding 1's first half, through the REST composition root's own replay database.
    assert read_status(tmp_path / "api" / "api-replay.sqlite3").current_version != 0
