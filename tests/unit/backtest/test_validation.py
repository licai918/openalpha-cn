from datetime import datetime, timedelta
from pathlib import Path

import pytest

from openalpha_cn.backtest.validation import OutcomeObservation, OutcomeValidator
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import InMemoryResearchMemory
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository


@pytest.fixture
def research_result(frozen_now: datetime):
    def _make(tmp_path: Path):
        evidence = EvidenceSnapshot(
            subject="000001.SZ",
            kind="limit_up",
            timeline=Timeline(
                event_time=frozen_now,
                available_time=frozen_now,
                ingested_time=frozen_now,
                revision_time=frozen_now,
            ),
            source_id="synthetic",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary="Synthetic limit-up.",
            payload={
                "schema": "a-share-evidence/v1",
                "family": "market_event",
                "facts": {"close": 10.0, "pct_change": 10.0, "board_count": 1},
                "quality_flags": [],
            },
        )
        return ResearchEngine(
            repository=SQLiteRunRepository(tmp_path / "state.sqlite3"),
            memory=InMemoryResearchMemory(),
            clock=lambda: frozen_now,
            recovery_store=SQLiteRecoveryStore(tmp_path / "state.sqlite3"),
        ).run_cycle(
            ResearchRunRequest(
                run_id="run_validation",
                mode="backtest",
                subject="000001.SZ",
                as_of=frozen_now,
                evidence=(evidence,),
                code_commit="0123456789abcdef",
                config_digest="c" * 64,
                random_seed=7,
            )
        )

    return _make


def test_outcome_validation_reconciles_rule_factor_and_agent_attribution(
    tmp_path: Path,
    research_result,
    frozen_now: datetime,
) -> None:
    research = research_result(tmp_path)

    result = OutcomeValidator().validate(
        research=research,
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

    assert result.realized_return == pytest.approx(0.1)
    assert result.net_active_return == pytest.approx(0.075)
    assert {item.category for item in result.attribution} == {"rule", "factor", "agent"}
    assert sum(item.contribution for item in result.attribution) == pytest.approx(
        result.net_active_return
    )
    assert result.signal_id == research.signal.signal_id
    assert result.decision_id == research.decision.decision_id
