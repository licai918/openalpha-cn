"""A cycle whose agents all abstain must produce an abstention, not a 500.

## The defect, and that it predates the row that found it

`ResearchEngine._aggregate` computed `direction` from the mean strength -- `"neutral"` when the
mean sits between the two thresholds -- and then handed that to `SignalFrame`, which refuses
every direction except `abstain` when `evidence_ids` is empty. An abstaining agent contributes
`strength=0.0` and no evidence id, so a run in which every routed agent abstained built a
`neutral` frame citing nothing and raised `ValidationError` out of `run_cycle`.

Measured on `be262ea`, before `V2-P4-008` touched anything, with an agent declaring
`market_event` and returning an abstention: `ValidationError: directional signal requires
evidence`, raised from `runtime/engine.py` inside `_aggregate`. So this is not a defect
`V2-P4-008` introduced -- it is one `V2-P4-008` made ordinary. Every agent this repository ships
today always cites the items it matched, and only a `StructuredSignalAgent` whose model chose to
abstain could reach it; a `FeatureScoreAgent` reaches it on any security the composed column has
no number for, which on a real factor panel is a routine day.

## Why the repair is an abstention rather than a relaxation

`V2-P4-029` settled this exact question one module over, for `DeliberationCommittee.review`, and
the sentence it settled on is the one that applies here: *an abstention is the claim that the
evidence supports no direction, and overturning it means minting a directional conclusion from a
frame whose `evidence_ids` is empty* -- which is the single thing `validate_conclusion` exists to
refuse. So the aggregate abstains, and it says which of the two reasons it abstained for.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from openalpha_cn.agents.base import AgentContext, AgentProvenance, AgentResult
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.risk_flag import RiskFlag
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.sdk import OpenAlphaSDK

DIGEST = "d" * 64


class AbstainingAgent:
    """An agent that runs, declares an evidence family, and concludes nothing."""

    agent_id = "abstaining-agent"
    evidence_families = frozenset({"market_event"})
    feature_dependencies: frozenset[str] = frozenset()
    provenance = AgentProvenance(kind="deterministic")

    def analyze(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_id=self.agent_id,
            signal=SignalFrame(
                subject=context.subject,
                as_of=context.as_of,
                direction="abstain",
                strength=0.0,
                confidence=0.0,
                horizon="5d",
                abstention_reason="The visible evidence contradicts itself.",
            ),
            rationale="Abstained rather than scoring contradictory evidence.",
        )


@pytest.fixture
def run_request(
    evidence: Callable[..., EvidenceSnapshot], frozen_now: datetime
) -> Callable[..., ResearchRunRequest]:
    def _make(*, run_id: str) -> ResearchRunRequest:
        item = evidence(
            kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1}
        )
        return ResearchRunRequest(
            run_id=run_id,
            mode="live",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(item,),
            code_commit="0123456789abcdef",
            config_digest=DIGEST,
            random_seed=7,
        )

    return _make


def test_a_cycle_whose_only_agent_abstains_returns_an_abstention_rather_than_raising(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    run_request: Callable[..., ResearchRunRequest],
) -> None:
    """The crash, driven through the SDK, which is where a caller met it."""
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=frozen_clock, agents=(AbstainingAgent(),))

    result = sdk.run_research(run_request(run_id="run_all_abstained"))

    assert result.signal.direction == "abstain"
    assert result.signal.strength == 0.0
    assert result.signal.confidence == 0.0
    assert result.decision.final_action == "abstain"


def test_the_two_reasons_a_cycle_can_abstain_are_two_sentences_and_not_one(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    run_request: Callable[..., ResearchRunRequest],
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
) -> None:
    """ "Nobody was routed" and "everybody abstained" are different facts about a run.

    Collapsing them would put a reader of `abstention_reason` on the wrong remedy: the first is
    fixed by supplying evidence or composing a feature plane, the second by looking at why the
    agents that *did* run declined. The run below routes nobody because its evidence family
    matches no agent in the roster.
    """
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=frozen_clock, agents=(AbstainingAgent(),))

    everybody_abstained = sdk.run_research(run_request(run_id="run_abstained"))
    nobody_ran = sdk.run_research(
        ResearchRunRequest(
            run_id="run_unrouted",
            mode="live",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(evidence(kind="capital", facts={"net_inflow": 1.0}),),
            code_commit="0123456789abcdef",
            config_digest=DIGEST,
            random_seed=7,
        )
    )

    assert nobody_ran.decision.agent_outputs == ()
    assert everybody_abstained.decision.agent_outputs != ()
    # A frame that abstains carries no confidence in the direction it declined to take, on both
    # branches. `validate_conclusion` constrains `strength` on an abstention and says nothing
    # about `confidence`, and `_aggregate` averages that number into a mixed run -- so a
    # confident abstention is a number that moves a real committee's answer (mutation sweep).
    assert nobody_ran.signal.confidence == everybody_abstained.signal.confidence == 0.0
    assert nobody_ran.signal.abstention_reason != everybody_abstained.signal.abstention_reason
    assert everybody_abstained.signal.abstention_reason == (
        "Every agent that ran abstained, so no conclusion cites any evidence."
    )


def test_one_abstention_beside_one_directional_signal_is_still_a_directional_run(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    run_request: Callable[..., ResearchRunRequest],
) -> None:
    """The repair keys on `evidence_ids` being empty and never on an abstention being present.

    A rule that abstained whenever *any* agent abstained would be a much larger behaviour
    change than the crash warranted -- one silent agent would veto a whole committee. This is
    also where the dilution disclosed in
    `KNOWN_ROUTING_LIMITATIONS.an_abstention_is_averaged_into_the_run_strength_as_a_zero`
    is visible: `MarketAgent` alone scores `0.65` on this evidence and the pair scores half of
    it, because `_aggregate` divides by the number of results and an abstention brings a zero.
    That behaviour is unchanged by this repair and predates it.
    """
    from openalpha_cn.agents.baseline import MarketAgent

    alone = OpenAlphaSDK(runtime_dir=tmp_path, clock=frozen_clock, agents=(MarketAgent(),))
    solo = alone.run_research(run_request(run_id="run_market_only"))

    both = OpenAlphaSDK(
        runtime_dir=tmp_path,
        clock=frozen_clock,
        agents=(MarketAgent(), AbstainingAgent()),
    )
    mixed = both.run_research(run_request(run_id="run_mixed"))

    assert solo.signal.direction == "bullish"
    assert mixed.signal.direction == "bullish"
    assert mixed.signal.strength == pytest.approx(solo.signal.strength / 2)
    assert mixed.signal.evidence_ids == solo.signal.evidence_ids


def test_a_risk_flag_raised_by_an_abstaining_agent_still_reaches_the_gate(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    run_request: Callable[..., ResearchRunRequest],
) -> None:
    """The abstaining aggregate carries the flags forward, because dropping them is fail-open.

    An agent that declines *because* the data looked wrong has said something, and
    `RiskGate.evaluate` reads exactly one field to hear it. An aggregate built with
    `risk_flags=()` would turn a `block` into a `pass` on the ledger -- `final_action` is
    `abstain` either way, so nothing else in the run would have shown the difference, which is
    what makes this the quiet kind of fail-open rather than the loud kind.
    """

    class SuspiciousAgent(AbstainingAgent):
        agent_id = "suspicious-agent"

        def analyze(self, context: AgentContext) -> AgentResult:
            base = super().analyze(context)
            return AgentResult(
                agent_id=self.agent_id,
                signal=base.signal.model_copy(update={"risk_flags": (RiskFlag.future_data,)}),
                rationale=base.rationale,
            )

    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=frozen_clock, agents=(SuspiciousAgent(),))

    result = sdk.run_research(run_request(run_id="run_flagged_abstention"))

    assert result.signal.direction == "abstain"
    assert result.signal.risk_flags == (RiskFlag.future_data,)
    assert result.decision.risk_decision == "block"


class ScoringAgent(AbstainingAgent):
    """An agent that returns exactly the strength it was built with."""

    agent_id = "scoring-agent"

    def __init__(self, strength: float) -> None:
        self.strength = strength

    def analyze(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_id=self.agent_id,
            signal=SignalFrame(
                subject=context.subject,
                as_of=context.as_of,
                direction="bullish" if self.strength > 0 else "bearish",
                strength=self.strength,
                confidence=0.6,
                horizon="5d",
                evidence_ids=(context.evidence[0].evidence_id,),
            ),
            rationale=f"Scored {self.strength}.",
        )


def test_a_run_whose_mean_strength_lands_exactly_on_a_threshold_is_neutral(
    tmp_path: Path,
    frozen_clock: Callable[[], datetime],
    run_request: Callable[..., ResearchRunRequest],
) -> None:
    """`_aggregate`'s two thresholds are exclusive, and both boundaries are driven (sweep).

    A sweep over the lines this row moved left `strength > 0.15` and `strength < -0.15` alive as
    `>=` and `<=`: nothing in this repository's suite stood on either boundary, so the two
    answers could not be separated. The comparison predates `V2-P4-008` -- it is the same
    expression, relocated below the `evidence_ids` check -- and it is closed here because a
    boundary nobody stands on is a boundary nobody has chosen.

    The direction on the *agent's own* frame is unconstrained by this: an agent may call `+0.15`
    bullish, and the question is what the **run** calls the average of its members.
    """
    for strength, expected in (
        (0.15, "neutral"),
        (0.16, "bullish"),
        (-0.15, "neutral"),
        (-0.16, "bearish"),
    ):
        sdk = OpenAlphaSDK(
            runtime_dir=tmp_path / f"run{strength}",
            clock=frozen_clock,
            agents=(ScoringAgent(strength),),
        )
        result = sdk.run_research(run_request(run_id=f"run_edge_{expected}_{strength}"))
        assert result.signal.strength == pytest.approx(strength)
        assert result.signal.direction == expected, strength
