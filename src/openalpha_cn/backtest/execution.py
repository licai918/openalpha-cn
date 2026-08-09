"""Configurable A-share cash-equity execution constraints and costs.

## Two ways to get the price band, and the derived one is measurably wrong (`V2-P1-008`)

`_rejection_reason` has always *derived* the day's limit band from the board and an `is_st`
flag. `V2-P1-008` landed `stk_limit`, the band the exchange actually published, and the two
disagree on **159 of the 5,338 priced names on 2024-06-28** -- for four independent reasons, of
which only one is rounding. `domain/price_limits.py` has the full measurement; the short
version is that the Beijing board rounds its band *inward* (131 names, one fen), an ST security
on ChiNext or STAR keeps the board's 20% rather than dropping to 5% (25 names, where the derived
band is four times too narrow), a new listing has **no** band for five sessions (2 names, where
the derived band invents one), and one share-reform `S` name carries 5% while not being ST.

So `MarketBar` gained `up_limit`/`down_limit`, both optional and both defaulting to `None`, and
`_rejection_reason` prefers them when they are there. **Nothing changes for a caller who does
not supply them**: the derivation below is untouched and every bar built without the two new
fields takes exactly the path it took before. That is deliberate rather than incidental -- this
policy is the one component of the backtest whose verdicts are pinned by tests written against
the derived rule, and quietly re-deciding them from a new dataset would be a silent behaviour
change of the worst kind. The published band is a path a caller opts into per bar, which is also
the right granularity: `stk_limit` starts in 2007 and reached the Beijing board late, so on a
historical session some names have a published band and others genuinely do not.

The limit-free sentinels ride through this path without a special case. Tushare publishes "no
limit today" as an `up_limit` of 99999.999 (or 100000.0 / 1000000.0 / 999999.999 -- the encoding
has changed twice) against a `down_limit` of 0.01, and the two comparisons below are
`low >= upper` and `high <= lower`, both of which are simply false against those numbers. A bar
with no limit therefore fills, which is correct, and it does so through the same two lines that
handle every other bar.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.domain.execution import ExecutionResult
from openalpha_cn.domain.price_limits import PriceLimit

__all__ = [
    "AShareExecutionPolicy",
    "CostSchedule",
    "ExecutionRequest",
    "ExecutionResult",
    "MarketBar",
    "published_limit_fields",
]

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
    up_limit: Decimal | None = Field(default=None, gt=0)
    """The exchange's published upper limit price, when the caller has one (`V2-P1-008`).

    `None` -- the default -- means "not supplied", and the policy then derives the band from
    `board` and `is_st` exactly as it always has. It does **not** mean "no limit": a security
    with no limit that day is published as a sentinel value, not as an absence, and passing that
    sentinel through gives the right verdict (see this module's docstring). Build the pair with
    `published_limit_fields` rather than converting by hand.
    """
    down_limit: Decimal | None = Field(default=None, gt=0)
    """The exchange's published lower limit price. Supplied with `up_limit` or not at all."""

    @model_validator(mode="after")
    def validate_prices(self) -> Self:
        if self.high < self.low:
            raise ValueError("high cannot be below low")
        if not self.low <= self.open <= self.high or not self.low <= self.close <= self.high:
            raise ValueError("open and close must fall within low and high")
        if (self.up_limit is None) != (self.down_limit is None):
            raise ValueError(
                "up_limit and down_limit are supplied together or not at all; one published "
                "side beside one derived side would be a band the exchange never set"
            )
        if (
            self.up_limit is not None
            and self.down_limit is not None
            and (self.down_limit > self.up_limit)
        ):
            raise ValueError("down_limit cannot be above up_limit")
        return self

    @property
    def has_published_limits(self) -> bool:
        """Whether this bar carries the exchange's own band rather than only the inputs to
        derive one."""
        return self.up_limit is not None and self.down_limit is not None


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

        upper, lower = _price_band(market)
        if request.side == "buy" and market.low >= upper:
            return "buy cannot fill on a one-price limit-up bar"
        if request.side == "sell" and market.high <= lower:
            return "sell cannot fill on a one-price limit-down bar"
        return None


def _price_band(market: MarketBar) -> tuple[Decimal, Decimal]:
    """The day's `(upper, lower)` limit prices: the published pair, or the derived one.

    The published pair wins when the caller supplied it, and the derivation below is byte-for-
    byte the one this policy has always applied -- see this module's docstring for the four
    measured ways the two disagree and for why adding a path was preferred to replacing one.
    """
    if market.up_limit is not None and market.down_limit is not None:
        return market.up_limit, market.down_limit
    ratio = Decimal("0.05") if market.is_st else _board_limit(market.board)
    upper = (market.previous_close * (Decimal(1) + ratio)).quantize(
        _CENT,
        rounding=ROUND_HALF_UP,
    )
    lower = (market.previous_close * (Decimal(1) - ratio)).quantize(
        _CENT,
        rounding=ROUND_HALF_UP,
    )
    return upper, lower


def _board_limit(board: Literal["main", "star", "growth", "bse"]) -> Decimal:
    """The *nominal* board ratio. Not the exchange's arithmetic, and now measurably so.

    Unchanged, because it is what `_price_band` falls back to and what every pre-`V2-P1-008`
    caller already depends on. What is now measured is how far it gets: on 2024-06-28 it
    reproduced the published band for 5,179 of 5,338 names, and the Beijing board's 30% is the
    clearest of the four misses -- the exchange truncates that band inward, so this function is
    right about the ratio and the price that comes out of it is a fen wrong on 131 of that
    day's 249 `.BJ` names.
    """
    if board in {"star", "growth"}:
        return Decimal("0.20")
    if board == "bse":
        return Decimal("0.30")
    return Decimal("0.10")


def published_limit_fields(limit: PriceLimit) -> dict[str, Decimal]:
    """Turn a stored `PriceLimit` into the two `MarketBar` fields, exactly.

    One place rather than at each call site, because the conversion has a trap in it:
    `Decimal(99999.999)` is `99999.99899999999806...` -- the binary double, carried into a type
    whose whole point is that it does not do that -- while `Decimal(str(99999.999))` is exactly
    `99999.999`. The published band is a two-decimal price (or a sentinel published to three),
    so the string form is the one that round-trips it.

    Returns a `dict` so it can be splatted into a `MarketBar(...)` call beside the other fields,
    which is how a caller that has both a bar and a band actually builds one.
    """
    return {
        "up_limit": Decimal(str(limit.up_limit)),
        "down_limit": Decimal(str(limit.down_limit)),
    }
