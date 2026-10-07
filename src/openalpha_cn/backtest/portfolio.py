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
    "LIMITS_ENFORCED_BY_THE_SIMULATOR",
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
    """Hard long-only exposure limits, read by the simulator and by the construction policy.

    `V2-P5-002` added the last three. They are declarations of one book's bounds and are read by
    two consumers with different reach -- `PortfolioSimulator` sees one order against one fill and
    no industry and no history, `backtest/portfolio_policy.py` sees the whole plan and no market
    -- so which fields each reads is written down as a set rather than left to be discovered:
    `LIMITS_ENFORCED_BY_THE_SIMULATOR` below and
    `portfolio_policy.LIMITS_ENFORCED_BY_THE_CONSTRUCTION_POLICY`, held covering by
    `tests/unit/backtest/test_portfolio_policy.py`. A field neither set names is a limit nothing
    enforces, which is the fail-open shape `V2-P4-030` found four instances of in the risk gate.

    **`min_cash_weight` is `max_total_exposure` restated and not a second constraint.** Under
    long-only accounting `equity == cash + market_value`, so `cash / equity >= min_cash_weight`
    and `market_value / equity <= 1 - min_cash_weight` are one inequality. Both fields exist
    because the roadmap row asks for a cash floor and because stating intent as a floor is
    legible; the binding bound is simply the tighter of the two, and the rejection reason says
    which one bound. Nothing here pretends they compose.

    `max_industry_weight` and `turnover_budget` are the two the simulator structurally cannot
    read: `MarketBar` carries no industry, and one order carries no book history. They are read
    by the construction policy, which sees both.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_position_weight: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    max_total_exposure: Decimal = Field(default=Decimal("0.80"), gt=0, le=1)
    min_cash_weight: Decimal = Field(default=Decimal("0"), ge=0, lt=1)
    max_industry_weight: Decimal | None = Field(default=None, gt=0, le=1)
    turnover_budget: Decimal | None = Field(default=None, ge=0)


LIMITS_ENFORCED_BY_THE_SIMULATOR: frozenset[str] = frozenset(
    {"max_position_weight", "max_total_exposure", "min_cash_weight"}
)
"""Which `PortfolioLimits` fields `PortfolioSimulator` actually checks.

Not every field, and the two it omits are omitted for a structural reason rather than by
oversight: an industry cap needs an industry and `MarketBar` has none, and a turnover budget
needs the book's previous weights and `execute_order` sees one order. Writing the set down is
what lets an audit prove the *other* consumer covers them, instead of a limit sitting on the
contract that nobody reads.
"""


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
        if (
            order.target_weight is not None
            and order.target_weight > self.limits.max_position_weight
        ):
            return self._reject(
                state=state,
                order=order,
                reason="declared target weight exceeds maximum position weight",
            )
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
        if after.cash / after.equity < self.limits.min_cash_weight:
            return self._reject(
                state=state,
                order=order,
                reason="minimum cash weight breached",
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
