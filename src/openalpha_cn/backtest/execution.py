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
limit today" as an out-of-range `up_limit` -- six encodings across three exchanges, listed in
`domain/price_limits.py` -- against a `down_limit` of 0.01 on SSE and SZSE and of exactly
**0.0** on the Beijing board. The two comparisons below are `low >= upper` and `high <= lower`,
both of which are simply false against those numbers, `0.0` included. A bar with no limit
therefore fills on both sides, which is correct, and it does so through the same two lines that
handle every other bar. `down_limit` is the one price on `MarketBar` bounded by `ge=0` rather
than `gt=0`, and that is what lets a `.BJ` listing day be represented at all.

## `suspended` is a bool and the halt corpus is not (`V2-P2-007`)

This policy and `domain/labels.py` both answer "could this session have been traded at its
close", and they were built from different inputs: `MarketBar.suspended` is a `bool` the caller
supplies, while the label contract reads `suspend_d`'s three-valued `TradingState` plus the
`suspend_timing` window and refuses through `halted_into_the_close`. `TradingState.__bool__`
raises precisely so that the collapse cannot happen by accident -- but this model needs a bool,
so the collapse has to happen *somewhere*, and the obvious one is wrong on the common shape:
`state is TradingState.halted` reads a security halted `13:00-15:00` as tradeable, and **39 of
the 59 timed `S` rows served across 68 whole-market sessions run through the close**, 30 of
2015-07-08's 31 among them. `suspended_at_the_close` is that collapse, made once, for
`published_limit_fields`' reason.

The two contracts still do not answer the same question everywhere, and
`KNOWN_EXECUTION_LIMITATIONS` names the three places rather than asserting an agreement that
does not hold. They are not merged: this policy judges **one order against one bar** and the
label contract judges **one window of sessions**, this policy's verdicts are pinned by tests
written against the derived band (see above), and a gate that rewrote the thing it measures
would measure nothing.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.domain.execution import ExecutionResult
from openalpha_cn.domain.price_limits import PriceLimit, TradingState, halt_spans_the_close

__all__ = [
    "KNOWN_EXECUTION_LIMITATIONS",
    "AShareExecutionPolicy",
    "CostSchedule",
    "ExecutionLimitation",
    "ExecutionRequest",
    "ExecutionResult",
    "MarketBar",
    "published_limit_fields",
    "suspended_at_the_close",
]

_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionLimitation:
    """One named boundary on what this policy's verdict can be trusted to mean."""

    code: str
    detail: str


KNOWN_EXECUTION_LIMITATIONS: Final[tuple[ExecutionLimitation, ...]] = (
    ExecutionLimitation(
        code="the_registry_verdict_is_not_an_input",
        detail=(
            "MarketBar carries a subject and a session and nothing that says whether the "
            "registry stood behind that security then. So a delisted name, a name that had "
            "not listed yet, and a session past the registry's own snapshot all execute like "
            "any other bar, while domain/labels.py refuses all three by name "
            "(delisted_in_window, not_yet_listed_in_window, beyond_registry_snapshot). The "
            "difference is not academic on the delisting side: "
            "KNOWN_ADJUSTMENT_LIMITATIONS.delisted_securities_carry_unstable_factors measured "
            "600069.SH's factor oscillating between 6.415 and 0.6604 for years after its last "
            "bar, and stock_basic serves 339 terminated securities against 5,539 listed. The "
            "defence is that a caller filters its universe before it builds bars, which is "
            "a discipline this contract cannot audit."
        ),
    ),
    ExecutionLimitation(
        code="an_absent_band_is_derived_rather_than_refused",
        detail=(
            "up_limit=None means 'the caller supplied no band', never 'the exchange published "
            "none', and _price_band then derives one from board and is_st -- which is wrong on "
            "159 of the 5,338 priced names of 2024-06-28 for the four reasons this module's "
            "docstring lists. domain/labels.py refuses the same fact "
            "(REFUSAL_UNPUBLISHED_BAND) because stk_limit serves 0 rows before 2007-01-04 and "
            "reached the Beijing board a decade after daily did, so an absent band is a real "
            "shape. The two are therefore fail-open and fail-closed on one input, and the "
            "direction here is the one that answers. Closing it would mean a third state on "
            "two optional fields, which is a signature change for every existing caller; what "
            "is done instead is that the two paths are pinned against each other and the gap "
            "is stated."
        ),
    ),
    ExecutionLimitation(
        code="a_one_price_session_refuses_one_side_here_and_both_ends_there",
        detail=(
            "_rejection_reason refuses a buy into a one-price limit-up bar and a sell into a "
            "one-price limit-down bar, and fills the other two combinations -- selling into a "
            "limit-up lock is exactly the trade that had a counterparty. domain/labels.py's "
            "REFUSAL_LOCKED_AT_LIMIT refuses the entry or the exit on *either* flag, because a "
            "label prices a round trip and does not know which side it will be. So the label "
            "is strictly the more conservative of the two and the two verdicts are not "
            "interchangeable: agreement holds on the buy side of the entry and the sell side "
            "of the exit, which is the long round trip a label assumes, and not on the other "
            "two."
        ),
    ),
)
"""What this policy cannot see that `domain/labels.py` can, and one place they disagree.

`V2-P2-007` asked whether the two implementations should be merged. They are not: this one
judges one order against one bar and that one judges a window of sessions, and this one's
verdicts are pinned by `tests/unit/backtest/test_execution.py` against the derived band. What
the two owe each other is agreement on the predicate they both claim -- "could this session be
traded at its close" -- which `tests/integration/panel/test_execution_label_parity.py` drives
from one stored panel through both. These three are where that agreement stops, stated rather
than asserted away.
"""


def suspended_at_the_close(state: TradingState | None, timing: str | None) -> bool:
    """Whether `MarketBar.suspended` must be `True` for a session `suspend_d` describes.

    The only supported way to build that field from a stored halt corpus, for
    `published_limit_fields`' reason: the conversion has a trap in it, and the trap is the
    obvious spelling.

    `suspend_d` is three-valued (`TradingState`) plus a window, and `suspended` is one bool, so
    something has to collapse them. `state is TradingState.halted` is what a reader reaches for
    and it is fail-open on the common shape: a security halted `13:00-15:00` *traded* -- all 31
    of 2015-07-08's timed `S` rows have a bar -- so it is `interrupted` rather than `halted`,
    while its `daily.close` is the last print before 13:00 and no order could have filled at
    it. **39 of the 59 timed `S` rows served across 68 whole-market sessions run through the
    close**, 30 of that 2015-07-08 set among them, so the naive collapse is wrong on two thirds
    of the shape it is asked about. This returns `True` for those, which is
    `domain/labels.py`'s `REFUSAL_HALTED_INTO_THE_CLOSE` under this contract's one bool.

    `None` -- no row mentions the security that session -- is `False`: 5,312 of 2024-06-28's
    5,338 priced names have no halt row at all, so an absent row is the ordinary case. That is
    only true of a corpus that knows what it covers; `HaltCorpus.require_coverage` is the guard
    that makes an absent row mean "nothing happened" rather than "nobody read the partition",
    and a caller that reaches past it gets this answer for a year it never loaded.

    `TradingState.resumed` is `False` for the same reason the label contract does not refuse
    it: an `R` row means the security traded that session, and nothing in it says the trading
    stopped again. An `interrupted` row with no window is `True`, matching
    `halt_spans_the_close`'s rule for one it cannot parse -- a right endpoint that cannot be
    read is a right endpoint that is unknown.
    """
    if state is None or state is TradingState.resumed:
        return False
    if state is TradingState.halted:
        return True
    return timing is None or halt_spans_the_close(timing)


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
    down_limit: Decimal | None = Field(default=None, ge=0)
    """The exchange's published lower limit price. Supplied with `up_limit` or not at all.

    `ge=0` rather than `gt=0`, unlike every other price on this model, because **zero is a
    published value on this one column**: the Beijing board writes "no lower bound" as
    `down_limit=0` beside an `up_limit` of 99999.99, on every `.BJ` security's first trading
    session since 2022-02-28 and on the first session of a delisting arrangement. A `gt=0`
    bound here would make those rows unrepresentable, so a bar that has a published band would
    have to be built without one -- silently falling back to the derived rule this field
    exists to replace, on exactly the board where that rule is measurably wrong. Zero still
    needs no special case below: `high <= lower` is false for every real bar, so a security
    with no floor can always be sold, which is correct.
    """

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

    One place rather than at each call site, because the conversion has a trap in it -- for the
    caller who converts by hand. `Decimal(99999.999)` is `99999.99899999999806...`, the binary
    double carried into a type whose whole point is that it does not do that, while
    `Decimal(str(99999.999))` is exactly `99999.999`. `PriceLimit` holds `float`s and
    `MarketBar` holds `Decimal`s, so *something* has to bridge them, and `Decimal(...)` is the
    obvious thing to reach for and the wrong one.

    Pydantic itself is not the hazard: handed the bare `float`, it already coerces through
    `Decimal(str(...))`, so `MarketBar(up_limit=99999.999).up_limit == Decimal("99999.999")`.
    This helper exists so that the *explicit* conversion -- the one a reader writes to satisfy
    a `Decimal`-typed field -- lands on the same value, and so that the two fields are always
    built together, which `MarketBar` requires anyway.

    Returns a `dict` so it can be splatted into a `MarketBar(...)` call beside the other fields,
    which is how a caller that has both a bar and a band actually builds one.
    """
    return {
        "up_limit": Decimal(str(limit.up_limit)),
        "down_limit": Decimal(str(limit.down_limit)),
    }
