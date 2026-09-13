"""Point-in-time replay, A-share execution, validation, attribution, and the shortlist funnel.

`__all__` was the sixteen `P1`-`P3` names until `V2-P4-033`, and the omission was measured rather
than noticed: `P4` shipped `CrossSectionScreen`, `rank_candidates` and `gate_shortlist` under 159
passing tests while `from openalpha_cn.backtest import ...` still could not name one of them, so
the package's own front door said the two-stage funnel did not exist. The eight names below are
the ones a caller outside this package actually needs to drive it -- the declaration
(`ShortlistSpec`, `ScoreComponent`, `ShortlistGateSpec`), the screen, the two functions, and the
verdict -- and no more: `ComponentCrossSection` is deliberately **not** re-exported here, because
nothing outside `openalpha_cn.shortlist_view` should be constructing one by hand. That module is
the supported way to get one out of a stored panel, and it is on the panel side precisely because
`backtest-no-numeric-stack-or-panel-plane` forbids this package to reach a store.
"""

from openalpha_cn.backtest.candidate_ranking import (
    CandidateRanking,
    RankedCandidate,
    rank_candidates,
)
from openalpha_cn.backtest.cross_section import (
    CrossSectionFunnel,
    CrossSectionScreen,
    ScoreComponent,
    ShortlistSpec,
)
from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    CostSchedule,
    ExecutionRequest,
    ExecutionResult,
    MarketBar,
)
from openalpha_cn.backtest.portfolio import (
    PortfolioLimits,
    PortfolioOrder,
    PortfolioSimulator,
    PortfolioState,
    PortfolioTransition,
)
from openalpha_cn.backtest.replay import ReplayCorpus, ReplayReport, ReplayRunner
from openalpha_cn.backtest.shortlist_gate import (
    ShortlistClearance,
    ShortlistGateSpec,
    gate_shortlist,
)
from openalpha_cn.backtest.validation import (
    OutcomeObservation,
    OutcomeValidator,
    observation_from_label,
)

__all__ = [
    "AShareExecutionPolicy",
    "CandidateRanking",
    "CostSchedule",
    "CrossSectionFunnel",
    "CrossSectionScreen",
    "ExecutionRequest",
    "ExecutionResult",
    "MarketBar",
    "OutcomeObservation",
    "OutcomeValidator",
    "PortfolioLimits",
    "PortfolioOrder",
    "PortfolioSimulator",
    "PortfolioState",
    "PortfolioTransition",
    "RankedCandidate",
    "ReplayCorpus",
    "ReplayReport",
    "ReplayRunner",
    "ScoreComponent",
    "ShortlistClearance",
    "ShortlistGateSpec",
    "ShortlistSpec",
    "gate_shortlist",
    "observation_from_label",
    "rank_candidates",
]
