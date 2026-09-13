"""What the deliberation committee concludes, including on the state it used to crash on.

`V2-P4-029`. `review` recomputed `direction` from `adjusted_strength` into
`Literal["bullish", "bearish", "neutral"]`, so `abstain` was unreachable and an abstaining
input came back out directional with the empty `evidence_ids` an abstention carries by
`SignalFrame.validate_conclusion` -- killing the `DeliberationOutcome` it was being put into.
The product-facing half of that finding is `tests/integration/test_abstaining_deliberation.py`,
which drives the two faces a caller met it through; the tests below are the committee's own
statement, and they cover the case the faces cannot reach cheaply: an abstention with a live
debate on both sides.
"""

from datetime import datetime

import pytest

from openalpha_cn.agents.base import AgentResult
from openalpha_cn.agents.committee import DeliberationCommittee
from openalpha_cn.domain.signal import SignalFrame


@pytest.fixture
def result(frozen_now: datetime):
    def _make(agent_id: str, strength: float, confidence: float) -> AgentResult:
        return AgentResult(
            agent_id=agent_id,
            signal=SignalFrame(
                subject="000001.SZ",
                as_of=frozen_now,
                direction="bullish" if strength > 0 else "bearish",
                strength=strength,
                confidence=confidence,
                horizon="5d",
                evidence_ids=("ev-1",),
            ),
            rationale=f"{agent_id} evidence case",
        )

    return _make


def test_committee_produces_bull_bear_risk_views_and_ablation_delta(
    result, frozen_now: datetime
) -> None:
    base = SignalFrame(
        subject="000001.SZ",
        as_of=frozen_now,
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


def test_a_debated_abstention_stays_an_abstention_and_reports_the_debate(
    result, frozen_now: datetime
) -> None:
    """The case neither shipped face reaches cheaply, and the one that decides the design.

    An abstention with a live debate is where "keep the direction" and "recompute it" actually
    differ: `debate_net` is non-zero here, so the previous arithmetic would have produced a
    directional conclusion. It must not, and not merely because `validate_conclusion` would
    refuse it -- an abstention carries no `evidence_ids`, so a direction derived from this
    debate would be a conclusion with nothing behind it. The committee reports the debate in
    `bull_case`/`bear_case` and leaves the conclusion alone, which is the honest pair.

    `strength` is asserted at exactly `0.0` rather than "unchanged": `validate_conclusion` ties
    zero strength to abstention, so any arithmetic that leaked `debate_net` into it would be a
    contract violation and not a rounding difference.
    """
    abstaining = SignalFrame(
        subject="000001.SZ",
        as_of=frozen_now,
        direction="abstain",
        strength=0.0,
        confidence=0.6,
        horizon="5d",
        abstention_reason="the two sides do not reconcile",
    )

    outcome = DeliberationCommittee().review(
        signal=abstaining,
        results=(result("bull", 0.9, 0.9), result("bear", -0.2, 0.4)),
    )

    assert outcome.adjusted_signal.direction == "abstain"
    assert outcome.adjusted_signal.strength == 0.0
    assert outcome.adjusted_signal.abstention_reason == "the two sides do not reconcile"
    assert outcome.adjusted_signal.evidence_ids == ()
    assert outcome.bull_case.agent_ids == ("bull",)
    assert outcome.bear_case.agent_ids == ("bear",)
    assert outcome.ablation.baseline_direction == "abstain"
    assert outcome.ablation.adjusted_direction == "abstain"
    assert outcome.ablation.strength_delta == 0.0


def test_a_directional_signal_never_becomes_an_abstention(result, frozen_now: datetime) -> None:
    """The converse, which is what keeps the repair from being a new way to lose a conclusion.

    `abstain` is now reachable in `review`'s direction annotation, so the branch that produces
    it has to be keyed on the *input* being an abstention and on nothing else. A signal whose
    debate cancels out lands at `neutral`; if it landed at `abstain` the outcome would claim a
    refusal to conclude that no agent made -- and would raise anyway, since a directional frame
    carries no `abstention_reason` for `validate_conclusion` to find.
    """
    directional = SignalFrame(
        subject="000001.SZ",
        as_of=frozen_now,
        direction="neutral",
        strength=0.0,
        confidence=0.5,
        horizon="5d",
        evidence_ids=("ev-1",),
    )

    outcome = DeliberationCommittee().review(
        signal=directional,
        results=(result("bull", 0.5, 0.8), result("bear", -0.5, 0.8)),
    )

    assert outcome.adjusted_signal.direction == "neutral"
    assert outcome.adjusted_signal.abstention_reason is None
