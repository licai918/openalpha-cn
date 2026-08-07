"""Deterministic multi-day portfolio backtest and exposure attribution."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.portfolio import (
    PortfolioLimits,
    PortfolioOrder,
    PortfolioSimulator,
    PortfolioState,
    PortfolioTransition,
)

_SIX = Decimal("0.000001")
_CENT = Decimal("0.01")


class PortfolioLedger(Protocol):
    """Extension contract for durable portfolio-transition storage.

    Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives on the
    consumer side (`backtest/`), not in `storage/`. `PortfolioBacktestRunner` is a pure
    algorithmic module and only ever calls `append` on its optional ledger -- it never
    reads transitions back, so `SQLitePortfolioLedger.get`/`list` (used elsewhere, by
    `sdk.py` and `api/app.py` directly on the concrete store) are deliberately not part
    of this Protocol.
    """

    def append(self, transition: PortfolioTransition) -> None:
        """Append idempotently or reject conflicting reuse of an order ID."""


class PortfolioBacktestStep(BaseModel):
    """One dated order/market observation and benchmark close."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order: PortfolioOrder
    market: MarketBar
    benchmark_close: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_subject(self) -> Self:
        if self.order.subject != self.market.subject:
            raise ValueError("backtest order and market subjects must match")
        return self


class EquityPoint(BaseModel):
    """Portfolio and benchmark values after one transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_date: str
    equity: Decimal
    benchmark_close: Decimal
    gross_exposure: Decimal


class SubjectAttribution(BaseModel):
    """Realized PnL attributed to one traded subject."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    pnl: Decimal


class PortfolioBacktestReport(BaseModel):
    """Reconciled multi-day portfolio performance report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_state: PortfolioState
    final_state: PortfolioState
    transitions: tuple[PortfolioTransition, ...]
    equity_curve: tuple[EquityPoint, ...]
    total_return: Decimal
    benchmark_return: Decimal
    active_return: Decimal
    turnover: Decimal
    max_order_notional: Decimal
    max_gross_exposure: Decimal
    attribution: tuple[SubjectAttribution, ...]


class PortfolioBacktestRunner:
    """Execute ordered daily transitions through the production portfolio core."""

    def __init__(
        self,
        *,
        limits: PortfolioLimits | None = None,
        ledger: PortfolioLedger | None = None,
    ) -> None:
        self.simulator = PortfolioSimulator(limits=limits)
        self.ledger = ledger

    def run(
        self,
        *,
        initial: PortfolioState,
        steps: tuple[PortfolioBacktestStep, ...],
    ) -> PortfolioBacktestReport:
        if not steps:
            raise ValueError("multi-day backtest requires at least one step")
        state = initial
        transitions: list[PortfolioTransition] = []
        points: list[EquityPoint] = []
        pnl: dict[str, Decimal] = {}
        turnover_notional = Decimal(0)
        max_order_notional = Decimal(0)
        max_exposure = Decimal(0)
        for step in steps:
            transition = self.simulator.execute_order(
                state=state,
                order=step.order,
                market=step.market,
            )
            transitions.append(transition)
            if self.ledger is not None:
                self.ledger.append(transition)
            state = transition.after
            if transition.execution is not None and transition.status == "filled":
                turnover_notional += transition.execution.notional
                max_order_notional = max(max_order_notional, transition.execution.notional)
                pnl[step.order.subject] = (
                    pnl.get(step.order.subject, Decimal(0)) + transition.realized_pnl_delta
                )
            exposure = Decimal(0) if state.equity == 0 else state.market_value / state.equity
            max_exposure = max(max_exposure, exposure)
            points.append(
                EquityPoint(
                    trade_date=step.market.trade_date.isoformat(),
                    equity=state.equity,
                    benchmark_close=step.benchmark_close,
                    gross_exposure=exposure.quantize(_SIX),
                )
            )
        total_return = (state.equity / initial.equity - 1).quantize(_SIX)
        benchmark_return = (steps[-1].benchmark_close / steps[0].benchmark_close - 1).quantize(_SIX)
        average_equity = (initial.equity + state.equity) / 2
        turnover = (
            Decimal(0)
            if average_equity == 0
            else (turnover_notional / average_equity).quantize(_SIX)
        )
        return PortfolioBacktestReport(
            initial_state=initial,
            final_state=state,
            transitions=tuple(transitions),
            equity_curve=tuple(points),
            total_return=total_return,
            benchmark_return=benchmark_return,
            active_return=(total_return - benchmark_return).quantize(_SIX),
            turnover=turnover,
            max_order_notional=max_order_notional.quantize(_CENT, rounding=ROUND_HALF_UP),
            max_gross_exposure=max_exposure.quantize(_SIX),
            attribution=tuple(
                SubjectAttribution(subject=subject, pnl=pnl[subject].quantize(_CENT))
                for subject in sorted(pnl)
            ),
        )
