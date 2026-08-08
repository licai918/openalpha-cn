"""Deterministic long-only A-share portfolio accounting and risk clamps."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    ExecutionResult,
    MarketBar,
)
from openalpha_cn.domain.portfolio import (
    PORTFOLIO_TRANSITION_VERSIONS,
    PortfolioOrder,
    PortfolioPosition,
    PortfolioState,
    PortfolioTransition,
    PositionLot,
    PositionMark,
)

__all__ = [
    "PORTFOLIO_TRANSITION_VERSIONS",
    "PortfolioLimits",
    "PortfolioOrder",
    "PortfolioPosition",
    "PortfolioSimulator",
    "PortfolioState",
    "PortfolioTransition",
    "PositionLot",
    "PositionMark",
]

_CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


class PortfolioLimits(BaseModel):
    """Hard long-only exposure limits applied before accepting a buy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_position_weight: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    max_total_exposure: Decimal = Field(default=Decimal("0.80"), gt=0, le=1)


class PortfolioSimulator:
    """Apply A-share execution, cash, T+1, FIFO, and exposure invariants."""

    def __init__(
        self,
        *,
        execution: AShareExecutionPolicy | None = None,
        limits: PortfolioLimits | None = None,
    ) -> None:
        self.execution = execution or AShareExecutionPolicy()
        self.limits = limits or PortfolioLimits()

    def execute_order(
        self,
        *,
        state: PortfolioState,
        order: PortfolioOrder,
        market: MarketBar,
    ) -> PortfolioTransition:
        """Execute one order or return an explicit unchanged rejection."""
        if order.subject != market.subject:
            return self._reject(state=state, order=order, reason="order and market mismatch")
        if market.trade_date < state.as_of:
            return self._reject(
                state=state,
                order=order,
                reason="market date precedes portfolio state",
            )
        if order.side == "buy":
            return self._buy(state=state, order=order, market=market)
        return self._sell(state=state, order=order, market=market)

    def _buy(
        self,
        *,
        state: PortfolioState,
        order: PortfolioOrder,
        market: MarketBar,
    ) -> PortfolioTransition:
        execution = self.execution.execute(
            ExecutionRequest(side="buy", quantity=order.quantity),
            market,
        )
        if execution.status == "rejected":
            return self._reject(
                state=state,
                order=order,
                reason=execution.reason or "execution rejected",
                execution=execution,
            )
        required_cash = execution.notional + execution.total_cost
        if required_cash > state.cash:
            return self._reject(state=state, order=order, reason="insufficient cash")

        positions = {position.subject: position for position in state.positions}
        existing = positions.get(order.subject, PortfolioPosition(subject=order.subject))
        positions[order.subject] = PortfolioPosition(
            subject=order.subject,
            lots=(
                *existing.lots,
                PositionLot(
                    open_date=market.trade_date,
                    quantity=order.quantity,
                    cost_basis=required_cash,
                ),
            ),
        )
        marks = {mark.subject: mark.price for mark in state.marks}
        marks[order.subject] = market.close
        after = self._state(
            state=state,
            as_of=market.trade_date,
            cash=state.cash - required_cash,
            positions=positions,
            marks=marks,
            fees_paid=state.fees_paid + execution.total_cost,
        )
        position_value = Decimal(after.position(order.subject).quantity) * market.close
        if position_value / after.equity > self.limits.max_position_weight:
            return self._reject(
                state=state,
                order=order,
                reason="maximum position weight exceeded",
            )
        if after.market_value / after.equity > self.limits.max_total_exposure:
            return self._reject(
                state=state,
                order=order,
                reason="maximum total exposure exceeded",
            )
        return PortfolioTransition(
            status="filled",
            order=order,
            before=state,
            after=after,
            execution=execution,
        )

    def _sell(
        self,
        *,
        state: PortfolioState,
        order: PortfolioOrder,
        market: MarketBar,
    ) -> PortfolioTransition:
        position = state.position(order.subject)
        available = sum(lot.quantity for lot in position.lots if lot.open_date < market.trade_date)
        if order.quantity > available:
            return self._reject(
                state=state,
                order=order,
                reason="insufficient T+1 available quantity",
            )
        execution = self.execution.execute(
            ExecutionRequest(
                side="sell",
                quantity=order.quantity,
                position_open_date=min(lot.open_date for lot in position.lots),
            ),
            market,
        )
        if execution.status == "rejected":
            return self._reject(
                state=state,
                order=order,
                reason=execution.reason or "execution rejected",
                execution=execution,
            )

        remaining = order.quantity
        sold_basis = Decimal(0)
        kept_lots: list[PositionLot] = []
        for lot in position.lots:
            if remaining == 0 or lot.open_date >= market.trade_date:
                kept_lots.append(lot)
                continue
            sold_quantity = min(remaining, lot.quantity)
            allocated_basis = _money(
                lot.cost_basis * Decimal(sold_quantity) / Decimal(lot.quantity)
            )
            sold_basis += allocated_basis
            remaining -= sold_quantity
            if sold_quantity < lot.quantity:
                kept_lots.append(
                    PositionLot(
                        open_date=lot.open_date,
                        quantity=lot.quantity - sold_quantity,
                        cost_basis=lot.cost_basis - allocated_basis,
                    )
                )

        positions = {item.subject: item for item in state.positions}
        if kept_lots:
            positions[order.subject] = PortfolioPosition(
                subject=order.subject,
                lots=tuple(kept_lots),
            )
        else:
            positions.pop(order.subject, None)
        marks = {mark.subject: mark.price for mark in state.marks}
        if order.subject in positions:
            marks[order.subject] = market.close
        else:
            marks.pop(order.subject, None)
        proceeds = execution.notional - execution.total_cost
        realized_delta = _money(proceeds - sold_basis)
        after = self._state(
            state=state,
            as_of=market.trade_date,
            cash=state.cash + proceeds,
            positions=positions,
            marks=marks,
            realized_pnl=state.realized_pnl + realized_delta,
            fees_paid=state.fees_paid + execution.total_cost,
        )
        return PortfolioTransition(
            status="filled",
            order=order,
            before=state,
            after=after,
            execution=execution,
            realized_pnl_delta=realized_delta,
        )

    @staticmethod
    def _state(
        *,
        state: PortfolioState,
        as_of: date,
        cash: Decimal,
        positions: dict[str, PortfolioPosition],
        marks: dict[str, Decimal],
        realized_pnl: Decimal | None = None,
        fees_paid: Decimal | None = None,
    ) -> PortfolioState:
        return PortfolioState(
            as_of=as_of,
            cash=_money(cash),
            positions=tuple(positions[key] for key in sorted(positions)),
            marks=tuple(PositionMark(subject=key, price=marks[key]) for key in sorted(marks)),
            realized_pnl=_money(state.realized_pnl if realized_pnl is None else realized_pnl),
            fees_paid=_money(state.fees_paid if fees_paid is None else fees_paid),
        )

    @staticmethod
    def _reject(
        *,
        state: PortfolioState,
        order: PortfolioOrder,
        reason: str,
        execution: ExecutionResult | None = None,
    ) -> PortfolioTransition:
        return PortfolioTransition(
            status="rejected",
            order=order,
            before=state,
            after=state,
            execution=execution,
            reason=reason,
        )
