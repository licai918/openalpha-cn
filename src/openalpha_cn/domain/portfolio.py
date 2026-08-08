"""Deterministic A-share portfolio state, orders, and accepted/rejected transitions.

Split out of `backtest/portfolio.py` (V2-P0B-012) so `storage/portfolio.py` can persist
`PortfolioTransition` without importing `openalpha_cn.backtest` at all, forbidden by the
`storage-no-upward-deps` import-linter contract. Every class here is a plain data value
(pydantic models plus deterministic computed properties and validators, no I/O); the
*engine* that produces them, `PortfolioSimulator`, along with its `PortfolioLimits`
configuration (neither needed by storage), stays behind in `backtest/portfolio.py`.
`PortfolioTransition.execution` embeds `ExecutionResult`, moved alongside it into
`domain/execution.py` for the same reason.

`backtest/portfolio.py` re-exports every name defined here unchanged, so every existing
`from openalpha_cn.backtest.portfolio import PortfolioTransition` (and friends) keeps
working.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from openalpha_cn.domain.execution import ExecutionResult
from openalpha_cn.domain.versioning import ContractVersions, single_version

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


PORTFOLIO_TRANSITION_VERSIONS: ContractVersions[PortfolioTransition] = single_version(
    "portfolio-transition", PortfolioTransition
)
