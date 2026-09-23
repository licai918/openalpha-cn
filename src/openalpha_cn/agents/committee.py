"""Evidence-linked bull/bear debate, risk committee, and ablation output."""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from openalpha_cn.agents.base import AgentResult
from openalpha_cn.domain.risk_flag import RiskFlag, flags_with_severity
from openalpha_cn.domain.signal import SignalFrame

_COMMITTEE_BLOCKS_ON: Final[frozenset[RiskFlag]] = flags_with_severity("severe") | (
    flags_with_severity("blocked")
)
"""The flags this committee votes to block on, read off the vocabulary (`V2-P4-030`).

Was `{"regulatory", "data-quality", "suspension"}`, written as a **set literal inside `review`'s
body** -- not an attribute, not a module constant. That placement was the defect rather than a
detail of it: `product/governance.py` needed to know what those three strings were worth and had
no way to read them, so it drove a synthetic one-flag signal through `review` and inferred the
set from the verdict. Lifting it here is what let that probe go.

It is derived rather than moved, and it gained the `blocked` band on the way. A committee that
only reduced on `future_data` -- via its own catch-all "any flag at all is at least a
reduction", never by naming it -- was the mirror image of `RiskGate` passing on `regulatory`:
each gate's blind spot was the other's vocabulary. Both are closed now, and neither by
weakening: every answer that changed moved toward refusal.
"""


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
        """Deliberate over one conclusion, reporting the debate and what it moved.

        ## An abstention is deliberated *about* and never deliberated *away* (`V2-P4-029`)

        `direction` used to be recomputed from `adjusted_strength` into
        `Literal["bullish", "bearish", "neutral"]`, which has no `abstain` in it and no branch
        that could have produced one. `SignalFrame` calls itself "a research conclusion **or
        abstention**" in its first line and `validate_conclusion` makes the second half real --
        `direction="abstain"` requires a reason, requires zero strength, and reaches none of the
        `evidence_ids` demand -- so every abstention in this build carries no evidence. Handed
        one, this method turned it directional and then died constructing its own
        `DeliberationOutcome` with *"directional signal requires evidence"*, a **500** on
        `POST /api/v1/research/deliberate` and a `ValidationError` out of
        `OpenAlphaSDK.deliberate`, both of which pass a caller-supplied signal straight in.

        The repair is not to widen the annotation and let the arithmetic decide, because the
        arithmetic would still convert: an abstaining signal has `strength == 0`, so a live
        debate puts `debate_net / 2` into `adjusted_strength` and a conclusion appears. It is to
        say what an abstention *is* to a committee. An abstention is the claim that the evidence
        does not support a direction; a committee that overturned it would be minting a
        directional conclusion out of a frame carrying **no evidence ids at all**, which is the
        one thing `validate_conclusion` refuses outright. So the input's abstention is carried
        through: `strength` is pinned to `0.0` rather than recomputed, and `direction` stays
        `abstain`.

        The debate is not discarded, only kept off the conclusion. `bull_case`, `bear_case`,
        `risk_votes` and `ablation` are all computed and returned exactly as for a directional
        signal, so a caller can see that both sides were heard and that the committee moved
        nothing -- `strength_delta == 0.0` beside a populated `bull_case` is a reading, not a
        gap. `tests/unit/agents/test_deliberation_committee.py::
        test_a_debated_abstention_stays_an_abstention_and_reports_the_debate` is that pair.

        `abstain` is reachable **only** when the input abstains, never from the arithmetic. That
        direction is load-bearing: a directional signal carries no `abstention_reason`, so a
        conclusion that drifted into `abstain` would raise for the mirror-image reason. Both
        halves are held, by that test and by
        `test_a_directional_signal_never_becomes_an_abstention` beside it.
        """
        bulls = tuple(item for item in results if item.signal.strength > 0)
        bears = tuple(item for item in results if item.signal.strength < 0)
        bull_score = sum(item.signal.strength * item.signal.confidence for item in bulls)
        bear_score = sum(abs(item.signal.strength) * item.signal.confidence for item in bears)
        total = bull_score + bear_score
        debate_net = 0.0 if total == 0 else (bull_score - bear_score) / total
        abstaining = signal.direction == "abstain"
        adjusted_strength = (
            0.0 if abstaining else max(-1.0, min(1.0, (signal.strength + debate_net) / 2))
        )
        debated = bool(bulls and bears)
        confidence_factor = 1.0 if not debated else 0.75 + 0.25 * abs(debate_net)
        adjusted_confidence = max(0.0, min(1.0, signal.confidence * confidence_factor))
        direction: Literal["bullish", "bearish", "neutral", "abstain"] = (
            "abstain"
            if abstaining
            else "bullish"
            if adjusted_strength > 0.15
            else "bearish"
            if adjusted_strength < -0.15
            else "neutral"
        )
        flags = set(signal.risk_flags)
        if debated and abs(debate_net) < 0.35:
            flags.add(RiskFlag.committee_disagreement)
        severe = flags & _COMMITTEE_BLOCKS_ON
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
