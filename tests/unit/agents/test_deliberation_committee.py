from datetime import UTC, datetime

from openalpha_cn.agents.base import AgentResult
from openalpha_cn.agents.committee import DeliberationCommittee
from openalpha_cn.domain.signal import SignalFrame

NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)


def result(agent_id: str, strength: float, confidence: float) -> AgentResult:
    return AgentResult(
        agent_id=agent_id,
        signal=SignalFrame(
            subject="000001.SZ",
            as_of=NOW,
            direction="bullish" if strength > 0 else "bearish",
            strength=strength,
            confidence=confidence,
            horizon="5d",
            evidence_ids=("ev-1",),
        ),
        rationale=f"{agent_id} evidence case",
    )


def test_committee_produces_bull_bear_risk_views_and_ablation_delta() -> None:
    base = SignalFrame(
        subject="000001.SZ",
        as_of=NOW,
        direction="bullish",
        strength=0.5,
        confidence=0.8,
        horizon="5d",
        evidence_ids=("ev-1",),
    )

    outcome = DeliberationCommittee().review(
        signal=base,
        results=(result("bull", 0.8, 0.9), result("bear", -0.7, 0.8)),
    )

    assert outcome.bull_case.agent_ids == ("bull",)
    assert outcome.bear_case.agent_ids == ("bear",)
    assert {vote.perspective for vote in outcome.risk_votes} == {
        "aggressive",
        "neutral",
        "conservative",
    }
    assert outcome.ablation.enabled is True
    assert outcome.ablation.confidence_delta < 0
    assert "committee-disagreement" in outcome.adjusted_signal.risk_flags
