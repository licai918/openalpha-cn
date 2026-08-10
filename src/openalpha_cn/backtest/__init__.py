"""Point-in-time replay, A-share execution, validation, and attribution."""

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
from openalpha_cn.backtest.validation import (
    OutcomeObservation,
    OutcomeValidator,
    observation_from_label,
)

__all__ = [
    "AShareExecutionPolicy",
    "CostSchedule",
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
    "ReplayCorpus",
    "ReplayReport",
    "ReplayRunner",
    "observation_from_label",
]
