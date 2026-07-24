"""Evidence-linked bull/bear debate, risk committee, and ablation output."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from openalpha_cn.agents.base import AgentResult
from openalpha_cn.domain.signal import SignalFrame


class DebateCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    side: Literal["bull", "bear"]
    agent_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    weighted_score: float


class RiskVote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    perspective: Literal["aggressive", "neutral", "conservative"]
    decision: Literal["pass", "reduce", "block"]
    reasons: tuple[str, ...] = ()


class AblationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    baseline_direction: str
    adjusted_direction: str
    strength_delta: float
    confidence_delta: float


class DeliberationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bull_case: DebateCase
    bear_case: DebateCase
    risk_votes: tuple[RiskVote, ...]
    risk_decision: Literal["pass", "reduce", "block"]
    adjusted_signal: SignalFrame
    ablation: AblationResult


class DeliberationCommittee:
    """Balance opposing evidence and expose its measurable decision delta."""

    def review(
        self,
        *,
        signal: SignalFrame,
        results: tuple[AgentResult, ...],
    ) -> DeliberationOutcome:
        bulls = tuple(item for item in results if item.signal.strength > 0)
        bears = tuple(item for item in results if item.signal.strength < 0)
        bull_score = sum(item.signal.strength * item.signal.confidence for item in bulls)
        bear_score = sum(abs(item.signal.strength) * item.signal.confidence for item in bears)
        total = bull_score + bear_score
        debate_net = 0.0 if total == 0 else (bull_score - bear_score) / total
        adjusted_strength = max(-1.0, min(1.0, (signal.strength + debate_net) / 2))
        debated = bool(bulls and bears)
        confidence_factor = 1.0 if not debated else 0.75 + 0.25 * abs(debate_net)
        adjusted_confidence = max(0.0, min(1.0, signal.confidence * confidence_factor))
        direction: Literal["bullish", "bearish", "neutral"] = (
            "bullish"
            if adjusted_strength > 0.15
            else "bearish"
            if adjusted_strength < -0.15
            else "neutral"
        )
        flags = set(signal.risk_flags)
        if debated and abs(debate_net) < 0.35:
            flags.add("committee-disagreement")
        severe = {"regulatory", "data-quality", "suspension"}.intersection(flags)
        votes = (
            RiskVote(perspective="aggressive", decision="reduce" if severe else "pass"),
            RiskVote(
                perspective="neutral",
                decision="block" if severe else "reduce" if flags else "pass",
                reasons=tuple(sorted(flags)),
            ),
            RiskVote(
                perspective="conservative",
                decision="block" if severe else "reduce" if flags else "pass",
                reasons=tuple(sorted(flags)),
            ),
        )
        decisions = [vote.decision for vote in votes]
        risk_decision: Literal["pass", "reduce", "block"] = (
            "block"
            if decisions.count("block") >= 2
            else "reduce"
            if decisions.count("reduce") >= 2
            else "pass"
        )
        adjusted = signal.model_copy(
            update={
                "direction": direction,
                "strength": adjusted_strength,
                "confidence": adjusted_confidence,
                "risk_flags": tuple(sorted(flags)),
            }
        )
        return DeliberationOutcome(
            bull_case=self._case("bull", bulls, bull_score),
            bear_case=self._case("bear", bears, bear_score),
            risk_votes=votes,
            risk_decision=risk_decision,
            adjusted_signal=adjusted,
            ablation=AblationResult(
                enabled=True,
                baseline_direction=signal.direction,
                adjusted_direction=adjusted.direction,
                strength_delta=adjusted.strength - signal.strength,
                confidence_delta=adjusted.confidence - signal.confidence,
            ),
        )

    @staticmethod
    def _case(
        side: Literal["bull", "bear"],
        results: tuple[AgentResult, ...],
        score: float,
    ) -> DebateCase:
        return DebateCase(
            side=side,
            agent_ids=tuple(item.agent_id for item in results),
            evidence_ids=tuple(
                dict.fromkeys(
                    evidence_id for item in results for evidence_id in item.signal.evidence_ids
                )
            ),
            weighted_score=score,
        )
