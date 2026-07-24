from datetime import UTC, datetime
from typing import Any

import pytest

from openalpha_cn.agents.base import AgentContext
from openalpha_cn.agents.model import ModelProviderFailure, StructuredSignalAgent
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.models.base import ModelMetadata

NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)


def evidence() -> EvidenceSnapshot:
    return EvidenceSnapshot(
        subject="000001.SZ",
        kind="theme",
        timeline=Timeline(
            event_time=NOW,
            available_time=NOW,
            ingested_time=NOW,
            revision_time=NOW,
        ),
        source_id="synthetic",
        source_uri="fixture://theme",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Synthetic theme evidence.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "theme",
            "facts": {"theme": "机器人", "score": 0.8},
            "quality_flags": [],
        },
    )


class FakeModelProvider:
    metadata = ModelMetadata(
        provider_id="fake.model",
        model="fake-v1",
        credential_env_vars=(),
        structured_output=True,
    )

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self.outputs = outputs
        self.calls = 0
        self.schema: dict[str, Any] | None = None

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        assert system
        assert "ev_" in user
        self.schema = schema
        output = self.outputs[self.calls]
        self.calls += 1
        return output


def valid_output(item: EvidenceSnapshot) -> dict[str, Any]:
    return {
        "signal": {
            "subject": "000001.SZ",
            "as_of": NOW.isoformat(),
            "direction": "bullish",
            "strength": 0.5,
            "confidence": 0.7,
            "horizon": "5d",
            "evidence_ids": [item.evidence_id],
            "confirmation_conditions": ["Theme score remains elevated."],
            "invalidation_conditions": ["Theme score falls below 0.5."],
            "risk_flags": [],
        },
        "rationale": "The visible theme evidence supports a cautious bullish view.",
    }


def test_structured_model_agent_retries_invalid_output_then_validates() -> None:
    item = evidence()
    invalid = valid_output(item)
    invalid["signal"]["subject"] = "999999.SH"
    provider = FakeModelProvider([invalid, valid_output(item)])
    agent = StructuredSignalAgent(
        agent_id="model-theme-agent",
        evidence_families=frozenset({"theme"}),
        provider=provider,
        max_attempts=2,
    )

    result = agent.analyze(
        AgentContext(
            run_id="run_model",
            subject="000001.SZ",
            as_of=NOW,
            evidence=(item,),
        )
    )

    assert provider.calls == 2
    assert provider.schema is not None
    assert result.signal.evidence_ids == (item.evidence_id,)
    assert result.agent_id == "model-theme-agent"


def test_structured_model_agent_fails_explicitly_after_retry_budget() -> None:
    item = evidence()
    invalid = valid_output(item)
    invalid["signal"]["evidence_ids"] = ["ev_not_visible"]
    provider = FakeModelProvider([invalid, invalid])
    agent = StructuredSignalAgent(
        agent_id="model-theme-agent",
        evidence_families=frozenset({"theme"}),
        provider=provider,
        max_attempts=2,
    )

    with pytest.raises(ModelProviderFailure, match="2 attempts"):
        agent.analyze(
            AgentContext(
                run_id="run_model",
                subject="000001.SZ",
                as_of=NOW,
                evidence=(item,),
            )
        )
