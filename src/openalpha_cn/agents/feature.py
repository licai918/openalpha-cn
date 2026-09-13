"""A reference agent whose whole declaration is a panel-plane column (`V2-P4-008`, `V2-P4-009`).

## Why this ships rather than living in a test

`tools/base.py::ResearchTool` is the cautionary tale and it is one row away: a Protocol declared
as an extension point, satisfied by one class, consumed by nobody in `src/`, and still sitting
there two phases later with `V2-P4-009`'s row pointing at it as an unused seam. `FeaturePlane`
would have been the second one. So the consumer side ships too, and the seam has a user that the
suite exercises through `OpenAlphaSDK` rather than through an import.

## What it is not

It is not a baseline and it is not a claim that a factor predicts anything --
`backtest/alpha_model.py::SingleFeatureAlphaModel`'s own boundary, for its stated reason. It
reads one declared column, applies the `direction` the caller declared for that column, and
clamps. There is no fit, no cross section, no calibration, and the confidence it reports is a
constant the caller declares because one cell gives no basis for varying one. A deployment that
wants a scored candidate list wants `backtest/candidate_ranking.py`, which was built for it.

## What it cites, and why that is a disclosure rather than a design

`SignalFrame.validate_conclusion` refuses every direction except `abstain` when `evidence_ids`
is empty, so an agent that reads only the panel plane must cite *something* to be able to say
anything. What it has is the `feature_id` it read, and that is what it cites. See
`KNOWN_ROUTING_LIMITATIONS.a_feature_only_agent_cites_a_feature_id_that_no_evidence_store_resolves`
for what that does and does not buy: the two namespaces cannot collide (`EvidenceSnapshot`
mints `ev_` plus 24 hex characters and a `feature_id` never matches that), and no product face
resolves `evidence_ids` against a store, so nothing is being deceived -- but a reader who takes
`evidence_ids` to mean "rows in the evidence store" would be wrong about this one.
"""

from typing import Final, Literal

from openalpha_cn.agents.base import AgentContext, AgentProvenance, AgentResult
from openalpha_cn.domain.alpha_model import AlphaModelError
from openalpha_cn.domain.factor import FactorDirection
from openalpha_cn.domain.signal import SignalFrame

DIRECTION_THRESHOLD: Final[float] = 0.15
"""Where a clamped score stops being neutral, matching the rest of this repository's agents.

The same number `MarketAgent`, `ThemeAgent` and `ResearchEngine._aggregate` each write out, and
it is a named constant here rather than a fourth literal for `RunMode`'s reason: a value written
out per call site is a value that drifts when somebody edits three of the four. The three older
sites are left alone -- moving them is a change to what those agents score, which is not this
row's work -- and this module at least does not add to the count silently.
"""

CLAMP: Final[float] = 1.0
"""`SignalFrame.strength` is `ge=-1, le=1` and a feature value is not.

A processed-tier column is standardised and typically lands inside a few units of zero; a raw
column is whatever the factor measures, in whatever units it measures it. Clamping is therefore
not a nicety, it is the only way an arbitrary column can reach a bounded field at all -- and it
is a real loss of information, recorded in
`KNOWN_ROUTING_LIMITATIONS.a_clamped_score_cannot_separate_a_strong_reading_from_an_extreme_one`.
"""


class FeatureScoreAgent:
    """Score one declared panel-plane column, or abstain saying which one was missing."""

    provenance = AgentProvenance(kind="deterministic")

    def __init__(
        self,
        *,
        agent_id: str,
        feature_id: str,
        direction: FactorDirection,
        confidence: float = 0.5,
    ) -> None:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence is a probability; {confidence!r} is not in [0, 1]")
        self.agent_id = agent_id
        self.feature_id = feature_id
        self.direction = direction
        self.confidence = confidence
        self.evidence_families: frozenset[str] = frozenset()
        """This agent reads no evidence snapshot, and before `V2-P4-008` that made it unroutable.

        Declared empty rather than omitted: `AgentRouter` refuses an agent declaring *neither*
        half by name, so an empty family set is only well formed beside a non-empty
        `feature_dependencies`, which is exactly what this agent is.
        """
        self.feature_dependencies = frozenset({feature_id})

    def analyze(self, context: AgentContext) -> AgentResult:
        """Return one signal from one cell, or an abstention naming what was not there."""
        plane = context.features
        if plane is None:
            raise ValueError(
                f"{self.agent_id} declares {self.feature_id} and was handed a context with no "
                "feature plane; AgentRouter never routes a feature-dependent agent into one, so "
                "this context was assembled without it"
            )
        try:
            value = plane.value(ts_code=context.subject, feature_id=self.feature_id)
        except AlphaModelError as error:
            return self._abstaining(
                context,
                reason=(
                    f"The composed feature plane carries no row for {context.subject}, so "
                    f"{self.feature_id} could not be read for it: {error}"
                ),
            )
        if value is None:
            return self._abstaining(
                context,
                reason=(
                    f"{self.feature_id} carries no number for {context.subject} at "
                    f"{context.as_of.isoformat()}; an imputed value is a decision this agent "
                    "does not take."
                ),
            )
        strength = _clamped(value if self.direction == "higher_is_better" else -value)
        heading: Literal["bullish", "bearish", "neutral"] = (
            "bullish"
            if strength > DIRECTION_THRESHOLD
            else "bearish"
            if strength < -DIRECTION_THRESHOLD
            else "neutral"
        )
        return AgentResult(
            agent_id=self.agent_id,
            signal=SignalFrame(
                subject=context.subject,
                as_of=context.as_of,
                direction=heading,
                strength=strength,
                confidence=self.confidence,
                horizon="5d",
                evidence_ids=(self.feature_id,),
                confirmation_conditions=(
                    f"{self.feature_id} keeps its sign at the next cross section.",
                ),
                invalidation_conditions=(f"{self.feature_id} crosses zero.",),
            ),
            rationale=(
                f"Deterministic score of {self.feature_id} read {value} and the column is "
                f"declared {self.direction}."
            ),
        )

    def _abstaining(self, context: AgentContext, *, reason: str) -> AgentResult:
        """One abstention, so the two reachable ways of having no number share a shape."""
        return AgentResult(
            agent_id=self.agent_id,
            signal=SignalFrame(
                subject=context.subject,
                as_of=context.as_of,
                direction="abstain",
                strength=0.0,
                confidence=0.0,
                horizon="5d",
                abstention_reason=reason,
            ),
            rationale=f"{self.agent_id} read no value for {self.feature_id}.",
        )


def _clamped(score: float) -> float:
    """`score` inside `SignalFrame.strength`'s bounds."""
    return max(-CLAMP, min(CLAMP, score))
