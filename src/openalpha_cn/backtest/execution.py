"""Configurable A-share cash-equity execution constraints and costs."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_CENT = Decimal("0.01")


class MarketBar(BaseModel):
    """One daily bar with the fields needed by the v1 execution policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=128)
    trade_date: date
    board: Literal["main", "star", "growth", "bse"]
    previous_close: Decimal = Field(gt=0)
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    suspended: bool
    is_st: bool

    @model_validator(mode="after")
    def validate_prices(self) -> Self:
        if self.high < self.low:
            raise ValueError("high cannot be below low")
        if not self.low <= self.open <= self.high or not self.low <= self.close <= self.high:
            raise ValueError("open and close must fall within low and high")
        return self


class ExecutionRequest(BaseModel):
    """A simplified cash-equity order intent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    position_open_date: date | None = None


class CostSchedule(BaseModel):
    """Configurable transaction-cost assumptions, expressed as decimal rates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    commission_rate: Decimal = Decimal("0.0003")
    minimum_commission: Decimal = Decimal("5.00")
    transfer_fee_rate: Decimal = Decimal("0.00001")
    sell_stamp_duty_rate: Decimal = Decimal("0.0005")


class ExecutionResult(BaseModel):
    """Filled or explicitly rejected simulated execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["filled", "rejected"]
    side: Literal["buy", "sell"]
    quantity: int
    reason: str | None = None
    filled_price: Decimal | None = None
    notional: Decimal = Decimal("0.00")
    commission: Decimal = Decimal("0.00")
    transfer_fee: Decimal = Decimal("0.00")
    stamp_duty: Decimal = Decimal("0.00")
    total_cost: Decimal = Decimal("0.00")


class AShareExecutionPolicy:
    """Apply v1 A-share lot, T+1, price-limit, suspension, and cost rules."""

    def __init__(self, costs: CostSchedule | None = None) -> None:
        self.costs = costs or CostSchedule()

    def execute(self, request: ExecutionRequest, market: MarketBar) -> ExecutionResult:
        """Simulate a close-price fill after enforcing explicit constraints."""
        rejection = self._rejection_reason(request=request, market=market)
        if rejection is not None:
            return ExecutionResult(
                status="rejected",
                side=request.side,
                quantity=request.quantity,
                reason=rejection,
            )

        notional = (market.close * request.quantity).quantize(_CENT, rounding=ROUND_HALF_UP)
        commission = max(
            (notional * self.costs.commission_rate).quantize(_CENT, rounding=ROUND_HALF_UP),
            self.costs.minimum_commission,
        )
        transfer_fee = (notional * self.costs.transfer_fee_rate).quantize(
            _CENT,
            rounding=ROUND_HALF_UP,
        )
        stamp_duty = Decimal("0.00")
        if request.side == "sell":
            stamp_duty = (notional * self.costs.sell_stamp_duty_rate).quantize(
                _CENT,
                rounding=ROUND_HALF_UP,
            )
        total_cost = commission + transfer_fee + stamp_duty
        return ExecutionResult(
            status="filled",
            side=request.side,
            quantity=request.quantity,
            filled_price=market.close,
            notional=notional,
            commission=commission,
            transfer_fee=transfer_fee,
            stamp_duty=stamp_duty,
            total_cost=total_cost,
        )

    @staticmethod
    def _rejection_reason(
        *,
        request: ExecutionRequest,
        market: MarketBar,
    ) -> str | None:
        if market.suspended:
            return "security is suspended"
        if request.side == "buy":
            if market.board == "star" and request.quantity < 200:
                return "STAR-market buy quantity must be at least 200"
            if market.board != "star" and request.quantity % 100 != 0:
                return "main-board buy quantity must be a multiple of 100"
        if (
            request.side == "sell"
            and request.position_open_date is not None
            and request.position_open_date >= market.trade_date
        ):
            return "A-share cash equities cannot be sold on the purchase date"

        ratio = Decimal("0.05") if market.is_st else _board_limit(market.board)
        upper = (market.previous_close * (Decimal(1) + ratio)).quantize(
            _CENT,
            rounding=ROUND_HALF_UP,
        )
        lower = (market.previous_close * (Decimal(1) - ratio)).quantize(
            _CENT,
            rounding=ROUND_HALF_UP,
        )
        if request.side == "buy" and market.low >= upper:
            return "buy cannot fill on a one-price limit-up bar"
        if request.side == "sell" and market.high <= lower:
            return "sell cannot fill on a one-price limit-down bar"
        return None


def _board_limit(board: Literal["main", "star", "growth", "bse"]) -> Decimal:
    if board in {"star", "growth"}:
        return Decimal("0.20")
    if board == "bse":
        return Decimal("0.30")
    return Decimal("0.10")
