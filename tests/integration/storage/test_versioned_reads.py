"""Storage-layer proof for the version-dispatched contract reader (V2-P0B-005).

Two properties matter here, matching the brief's hard constraints:

1. Existing v1 rows, written by today's code through the normal write paths, must still
   read back field-for-field identical once every storage read call site is wired through
   `read_versioned()` instead of `Model.model_validate_json()`.
2. A row carrying a `schema_version` this build does not know -- the case of old code
   meeting a row written by a newer build -- must fail loudly with `UnknownSchemaVersionError`
   naming the contract, the payload's version, and the versions this build supports,
   rather than the bare `pydantic.ValidationError` a `Literal` + `extra="forbid"` mismatch
   used to raise.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from openalpha_cn.agents.base import AgentResult
from openalpha_cn.domain.decision import DECISION_LEDGER_VERSIONS, AgentDecision, DecisionLedger
from openalpha_cn.domain.run import (
    RUN_MANIFEST_VERSIONS,
    ArtifactDigest,
    CheckpointRecord,
    RunManifest,
    VersionRef,
)
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.versioning import UnknownSchemaVersionError
from openalpha_cn.storage.recovery import (
    RUN_RECOVERY_STATE_VERSIONS,
    RunRecoveryState,
    SQLiteRecoveryStore,
)
from openalpha_cn.storage.sqlite import SQLiteRunRepository

DIGEST = "a" * 64


@pytest.fixture
def _manifest(migration_now: datetime):
    def _make(run_id: str = "run_versioned_read") -> RunManifest:
        return RunManifest(
            run_id=run_id,
            mode="replay",
            as_of=migration_now,
            code_commit="0123456789abcdef",
            config_digest=DIGEST,
            provider_payload_digests=(ArtifactDigest(name="jqdata", sha256=DIGEST),),
            model_versions=(VersionRef(component="baseline", version="1.0.0"),),
            random_seed=7,
            started_at=migration_now,
            finished_at=migration_now,
            status="succeeded",
        )

    return _make


@pytest.fixture
def _decision(migration_now: datetime):
    def _make(run_id: str) -> DecisionLedger:
        return DecisionLedger(
            run_id=run_id,
            created_at=migration_now,
            agent_outputs=(
                AgentDecision(
                    agent_id="market-agent",
                    signal_id="sig_versioned_read",
                    recommendation="support",
                    rationale="Confirmed by price and volume.",
                ),
            ),
            routing_path=("market-agent", "risk-gate"),
            risk_decision="pass",
            final_action="watch",
            evidence_ids=("ev_versioned_read",),
            signal_ids=("sig_versioned_read",),
            code_commit="0123456789abcdef",
        )

    return _make


@pytest.fixture
def _signal(migration_now: datetime):
    def _make() -> SignalFrame:
        return SignalFrame(
            subject="000001.SZ",
            as_of=migration_now,
            direction="bullish",
            strength=0.4,
            confidence=0.6,
            horizon="3m",
            evidence_ids=("ev_versioned_read",),
        )

    return _make


@pytest.fixture
def _recovery_state(migration_now: datetime, _signal):
    def _make(run_id: str) -> RunRecoveryState:
        result = AgentResult(agent_id="market-agent", signal=_signal(), rationale="ok")
        return RunRecoveryState(
            run_id=run_id,
            request_digest=DIGEST,
            graph_signature=DIGEST,
            agent_ids=("market-agent", "risk-gate"),
            completed_results=(result,),
            next_agent_index=1,
            started_at=migration_now,
            updated_at=migration_now,
        )

    return _make


def test_sqlite_run_repository_reads_existing_records_field_for_field(
    tmp_path: Path, _manifest, _decision, migration_now: datetime
) -> None:
    """A database built with today's normal write paths still reads back identical."""
    repository = SQLiteRunRepository(tmp_path / "state.sqlite3")
    manifest = _manifest()
    decision = _decision(manifest.run_id)
    checkpoint = CheckpointRecord(name="risk-gate", recorded_at=migration_now, state_digest=DIGEST)
    repository.append_run(manifest)
    repository.append_decision(decision)
    repository.append_checkpoint(run_id=manifest.run_id, checkpoint=checkpoint)

    reread_manifest = repository.get_run(manifest.run_id)
    reread_decision = repository.get_decision(decision.decision_id)
    reread_decision_for_run = repository.get_decision_for_run(manifest.run_id)
    reread_checkpoints = repository.list_checkpoints(run_id=manifest.run_id)

    assert reread_manifest == manifest
    assert reread_decision == decision
    assert reread_decision_for_run == decision
    assert reread_checkpoints == (checkpoint,)


def test_sqlite_recovery_store_reads_existing_records_field_for_field(
    tmp_path: Path, _recovery_state
) -> None:
    store = SQLiteRecoveryStore(tmp_path / "state.sqlite3")
    state = _recovery_state("run_versioned_recovery")
    store.save(state)

    reread = store.get(state.run_id)

    assert reread == state


def test_sqlite_run_repository_get_run_fails_loudly_on_an_unknown_schema_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    SQLiteRunRepository(path)  # create the schema
    future_payload = json.dumps({"schema_version": "run-manifest/v2", "run_id": "run_future"})
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO runs (run_id, payload) VALUES (?, ?)",
            ("run_future", future_payload),
        )

    repository = SQLiteRunRepository(path)
    with pytest.raises(UnknownSchemaVersionError) as exc_info:
        repository.get_run("run_future")

    error = exc_info.value
    assert error.contract == "run-manifest"
    assert error.found_version == "run-manifest/v2"
    assert "run-manifest/v1" in error.supported_versions
    assert "run-manifest/v2" in str(error)


def test_sqlite_run_repository_get_decision_fails_loudly_on_an_unknown_schema_version(
    tmp_path: Path,
    _manifest,
) -> None:
    path = tmp_path / "state.sqlite3"
    repository = SQLiteRunRepository(path)
    repository.append_run(_manifest("run_decision_future"))
    future_payload = json.dumps(
        {"schema_version": "decision-ledger/v2", "run_id": "run_decision_future"}
    )
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO decisions (decision_id, run_id, payload) VALUES (?, ?, ?)",
            ("dec_future", "run_decision_future", future_payload),
        )

    with pytest.raises(UnknownSchemaVersionError) as exc_info:
        repository.get_decision("dec_future")

    error = exc_info.value
    assert error.contract == "decision-ledger"
    assert error.found_version == "decision-ledger/v2"
    assert "decision-ledger/v1" in error.supported_versions


def test_sqlite_recovery_store_get_fails_loudly_on_an_unknown_schema_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    store = SQLiteRecoveryStore(path)
    future_payload = json.dumps(
        {"schema_version": "run-recovery/v2", "run_id": "run_recovery_future"}
    )
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO run_recovery (run_id, request_digest, graph_signature, status, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("run_recovery_future", DIGEST, DIGEST, "running", future_payload),
        )

    with pytest.raises(UnknownSchemaVersionError) as exc_info:
        store.get("run_recovery_future")

    error = exc_info.value
    assert error.contract == "run-recovery"
    assert error.found_version == "run-recovery/v2"
    assert "run-recovery/v1" in error.supported_versions


def test_registries_current_version_matches_each_model_default() -> None:
    """Guard against the registry's current_version drifting from the model's own default."""
    assert RUN_MANIFEST_VERSIONS.current_version == "run-manifest/v1"
    assert DECISION_LEDGER_VERSIONS.current_version == "decision-ledger/v1"
    assert RUN_RECOVERY_STATE_VERSIONS.current_version == "run-recovery/v1"
