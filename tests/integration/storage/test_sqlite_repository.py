from datetime import datetime
from pathlib import Path

import pytest

from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.run import CheckpointRecord, RunManifest
from openalpha_cn.storage.sqlite import DuplicateRecordError, SQLiteRunRepository

DIGEST = "a" * 64


@pytest.fixture
def manifest(plain_frozen_now: datetime):
    def _make() -> RunManifest:
        return RunManifest(
            run_id="run_20260724",
            mode="replay",
            as_of=plain_frozen_now,
            code_commit="0123456789abcdef",
            config_digest=DIGEST,
            random_seed=7,
            started_at=plain_frozen_now,
            status="running",
        )

    return _make


@pytest.fixture
def decision(plain_frozen_now: datetime):
    def _make(run_manifest_id: str = "run_" + "0" * 24) -> DecisionLedger:
        return DecisionLedger(
            run_id="run_20260724",
            run_manifest_id=run_manifest_id,
            created_at=plain_frozen_now,
            routing_path=("risk-gate",),
            risk_decision="block",
            final_action="abstain",
            code_commit="0123456789abcdef",
        )

    return _make


def test_repository_round_trips_runs_decisions_and_checkpoints(
    tmp_path: Path, manifest, decision, plain_frozen_now: datetime
) -> None:
    repository = SQLiteRunRepository(tmp_path / "state.sqlite3")
    run = manifest()
    ledger = decision(run.run_manifest_id)
    checkpoint = CheckpointRecord(
        name="evidence-ready",
        recorded_at=plain_frozen_now,
        state_digest=DIGEST,
    )

    repository.append_run(run)
    repository.append_decision(ledger)
    repository.append_checkpoint(run_id=run.run_id, checkpoint=checkpoint)

    assert repository.get_run(run.run_id) == run
    assert repository.get_decision(ledger.decision_id) == ledger
    assert repository.list_checkpoints(run_id=run.run_id) == (checkpoint,)


def test_repository_uses_wal_and_foreign_keys(tmp_path: Path, plain_frozen_now: datetime) -> None:
    repository = SQLiteRunRepository(tmp_path / "state.sqlite3")

    assert repository.journal_mode() == "wal"
    with pytest.raises(ValueError, match="unknown run_id"):
        repository.append_checkpoint(
            run_id="missing",
            checkpoint=CheckpointRecord(
                name="invalid",
                recorded_at=plain_frozen_now,
                state_digest=DIGEST,
            ),
        )


def test_repository_is_append_only(tmp_path: Path, manifest) -> None:
    repository = SQLiteRunRepository(tmp_path / "state.sqlite3")
    run = manifest()
    repository.append_run(run)

    with pytest.raises(DuplicateRecordError, match=run.run_id):
        repository.append_run(run)
