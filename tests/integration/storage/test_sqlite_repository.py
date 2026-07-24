from datetime import UTC, datetime
from pathlib import Path

import pytest

from openalpha_cn.domain.decision import DecisionLedger
from openalpha_cn.domain.run import CheckpointRecord, RunManifest
from openalpha_cn.storage.sqlite import DuplicateRecordError, SQLiteRunRepository

NOW = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
DIGEST = "a" * 64


def manifest() -> RunManifest:
    return RunManifest(
        run_id="run_20260724",
        mode="replay",
        as_of=NOW,
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
        started_at=NOW,
        status="running",
    )


def decision() -> DecisionLedger:
    return DecisionLedger(
        run_id="run_20260724",
        created_at=NOW,
        routing_path=("risk-gate",),
        risk_decision="block",
        final_action="abstain",
        code_commit="0123456789abcdef",
    )


def test_repository_round_trips_runs_decisions_and_checkpoints(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "state.sqlite3")
    run = manifest()
    ledger = decision()
    checkpoint = CheckpointRecord(
        name="evidence-ready",
        recorded_at=NOW,
        state_digest=DIGEST,
    )

    repository.append_run(run)
    repository.append_decision(ledger)
    repository.append_checkpoint(run_id=run.run_id, checkpoint=checkpoint)

    assert repository.get_run(run.run_id) == run
    assert repository.get_decision(ledger.decision_id) == ledger
    assert repository.list_checkpoints(run_id=run.run_id) == (checkpoint,)


def test_repository_uses_wal_and_foreign_keys(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "state.sqlite3")

    assert repository.journal_mode() == "wal"
    with pytest.raises(ValueError, match="unknown run_id"):
        repository.append_checkpoint(
            run_id="missing",
            checkpoint=CheckpointRecord(
                name="invalid",
                recorded_at=NOW,
                state_digest=DIGEST,
            ),
        )


def test_repository_is_append_only(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "state.sqlite3")
    run = manifest()
    repository.append_run(run)

    with pytest.raises(DuplicateRecordError, match=run.run_id):
        repository.append_run(run)
