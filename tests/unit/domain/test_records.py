from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.decision import AgentDecision, DecisionLedger
from openalpha_cn.domain.run import ArtifactDigest, CheckpointRecord, RunManifest, VersionRef
from openalpha_cn.domain.validation import AttributionTerm, ValidationResult

DIGEST = "a" * 64


def test_decision_ledger_is_an_immutable_evidence_linked_record(plain_frozen_now: datetime) -> None:
    NOW = plain_frozen_now
    ledger = DecisionLedger(
        run_id="run_20260724",
        created_at=NOW,
        agent_outputs=(
            AgentDecision(
                agent_id="market-agent",
                signal_id="sig_123",
                recommendation="support",
                rationale="Price and volume confirm the event.",
            ),
        ),
        routing_path=("market-agent", "risk-gate"),
        risk_decision="pass",
        final_action="watch",
        evidence_ids=("ev_123",),
        signal_ids=("sig_123",),
        code_commit="0123456789abcdef",
        model_versions=(VersionRef(component="baseline", version="1.0.0"),),
        prompt_versions=(),
    )

    assert ledger.schema_version == "decision-ledger/v1"
    assert ledger.decision_id.startswith("dec_")
    assert ledger.model_copy().decision_id == ledger.decision_id
    with pytest.raises(ValidationError, match="Instance is frozen"):
        ledger.final_action = "avoid"


def test_non_abstaining_decision_requires_evidence_and_signal_references(
    plain_frozen_now: datetime,
) -> None:
    NOW = plain_frozen_now
    with pytest.raises(ValidationError, match="requires evidence_ids and signal_ids"):
        DecisionLedger(
            run_id="run_20260724",
            created_at=NOW,
            agent_outputs=(),
            routing_path=("risk-gate",),
            risk_decision="pass",
            final_action="watch",
            evidence_ids=(),
            signal_ids=(),
            code_commit="0123456789abcdef",
        )


def test_run_manifest_records_reproduction_inputs_and_terminal_state(
    plain_frozen_now: datetime,
) -> None:
    NOW = plain_frozen_now
    manifest = RunManifest(
        run_id="run_20260724",
        mode="replay",
        as_of=NOW,
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        provider_payload_digests=(ArtifactDigest(name="synthetic.limit-up", sha256=DIGEST),),
        model_versions=(VersionRef(component="baseline", version="1.0.0"),),
        prompt_versions=(),
        random_seed=7,
        environment=(VersionRef(component="python", version="3.11.15"),),
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        status="succeeded",
        checkpoints=(
            CheckpointRecord(
                name="evidence-ready",
                recorded_at=NOW,
                state_digest=DIGEST,
            ),
        ),
    )

    assert manifest.schema_version == "run-manifest/v1"
    assert manifest.provider_payload_digests[0].sha256 == DIGEST


def test_terminal_run_manifest_requires_finished_at(plain_frozen_now: datetime) -> None:
    NOW = plain_frozen_now
    with pytest.raises(ValidationError, match="finished_at is required"):
        RunManifest(
            run_id="run_20260724",
            mode="replay",
            as_of=NOW,
            code_commit="0123456789abcdef",
            config_digest=DIGEST,
            random_seed=7,
            started_at=NOW,
            status="failed",
        )


def test_validation_result_requires_reconciled_attribution(plain_frozen_now: datetime) -> None:
    NOW = plain_frozen_now
    with pytest.raises(ValidationError, match="attribution does not reconcile"):
        ValidationResult(
            signal_id="sig_123",
            decision_id="dec_123",
            observation_start=NOW,
            observation_end=NOW + timedelta(days=5),
            realized_return=0.10,
            benchmark_return=0.02,
            transaction_cost=0.005,
            attribution=(
                AttributionTerm(
                    category="agent",
                    name="market-agent",
                    contribution=0.01,
                ),
            ),
            confidence=0.8,
        )


def test_validation_result_exposes_net_active_return_and_stable_id(
    plain_frozen_now: datetime,
) -> None:
    NOW = plain_frozen_now
    result = ValidationResult(
        signal_id="sig_123",
        decision_id="dec_123",
        observation_start=NOW,
        observation_end=NOW + timedelta(days=5),
        realized_return=0.10,
        benchmark_return=0.02,
        transaction_cost=0.005,
        attribution=(
            AttributionTerm(category="rule", name="limit-up", contribution=0.025),
            AttributionTerm(category="factor", name="momentum", contribution=0.03),
            AttributionTerm(category="agent", name="market-agent", contribution=0.02),
        ),
        confidence=0.8,
        data_quality_notes=("Synthetic fixture.",),
    )

    assert result.schema_version == "validation-result/v1"
    assert result.net_active_return == pytest.approx(0.075)
    assert result.validation_id.startswith("val_")
    assert result.model_copy().validation_id == result.validation_id
