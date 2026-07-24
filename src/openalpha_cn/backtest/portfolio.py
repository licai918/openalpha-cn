"""Deterministic long-only A-share portfolio accounting and risk clamps."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    ExecutionResult,
    MarketBar,
)

_CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


class PositionLot(BaseModel):
    """One acquisition-date lot used for T+1 and FIFO cost accounting."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    open_date: date
    quantity: int = Field(gt=0)
    cost_basis: Decimal = Field(gt=0)


class PortfolioPosition(BaseModel):
    """All open lots for one A-share security."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=128)
    lots: tuple[PositionLot, ...] = ()

    @computed_field(return_type=int)  # type: ignore[prop-decorator]
    @property
    def quantity(self) -> int:
        return sum(lot.quantity for lot in self.lots)

    @computed_field(return_type=Decimal)  # type: ignore[prop-decorator]
    @property
    def cost_basis(self) -> Decimal:
        return _money(sum((lot.cost_basis for lot in self.lots), start=Decimal(0)))


class PositionMark(BaseModel):
    """Latest close used to value one open position."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=128)
    price: Decimal = Field(gt=0)


class PortfolioState(BaseModel):
    """Immutable cash, lots, marks, fees, and realized profit after one cycle."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["portfolio-state/v1"] = "portfolio-state/v1"
    as_of: date
    cash: Decimal = Field(ge=0)
    positions: tuple[PortfolioPosition, ...] = ()
    marks: tuple[PositionMark, ...] = ()
    realized_pnl: Decimal = Decimal("0.00")
    fees_paid: Decimal = Field(default=Decimal("0.00"), ge=0)

    @model_validator(mode="after")
    def validate_identity_and_marks(self) -> Self:
        position_subjects = tuple(position.subject for position in self.positions)
        mark_subjects = tuple(mark.subject for mark in self.marks)
        if len(position_subjects) != len(set(position_subjects)):
            raise ValueError("portfolio positions must have unique subjects")
        if len(mark_subjects) != len(set(mark_subjects)):
            raise ValueError("portfolio marks must have unique subjects")
        if any(position.quantity <= 0 for position in self.positions):
            raise ValueError("empty positions must not be persisted")
        missing_marks = set(position_subjects) - set(mark_subjects)
        if missing_marks:
            raise ValueError(f"open positions require marks: {sorted(missing_marks)}")
        return self

    def position(self, subject: str) -> PortfolioPosition:
        """Return an open position or a zero-lot view for the subject."""
        return next(
            (position for position in self.positions if position.subject == subject),
            PortfolioPosition(subject=subject),
        )

    def mark(self, subject: str) -> Decimal | None:
        """Return the latest valuation mark for one subject."""
        item = next((mark for mark in self.marks if mark.subject == subject), None)
        return None if item is None else item.price

    @computed_field(return_type=Decimal)  # type: ignore[prop-decorator]
    @property
    def market_value(self) -> Decimal:
        values = (
            Decimal(position.quantity) * self._required_mark(position.subject)
            for position in self.positions
        )
        return _money(sum(values, start=Decimal(0)))

    @computed_field(return_type=Decimal)  # type: ignore[prop-decorator]
    @property
    def equity(self) -> Decimal:
        return _money(self.cash + self.market_value)

    def _required_mark(self, subject: str) -> Decimal:
        mark = self.mark(subject)
        if mark is None:
            raise ValueError(f"position has no valuation mark: {subject}")
        return mark


class PortfolioLimits(BaseModel):
    """Hard long-only exposure limits applied before accepting a buy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_position_weight: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    max_total_exposure: Decimal = Field(default=Decimal("0.80"), gt=0, le=1)


class PortfolioOrder(BaseModel):
    """A deterministic order intent at one daily-bar close."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    order_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=128)
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)


class PortfolioTransition(BaseModel):
    """Accepted or rejected order plus immutable before/after states."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["filled", "rejected"]
    order: PortfolioOrder
    before: PortfolioState
    after: PortfolioState
    execution: ExecutionResult | None = None
    reason: str | None = None
    realized_pnl_delta: Decimal = Decimal("0.00")


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
