"""Orders and the daily trade list (`V2-P7-005`, `V2-P7-008`): a decision becomes whole lots.

The trade list is **the product the user reads each evening**: what to sell, what to buy, how many
shares, at what reference price, and why. It is a pure function of the decision, the state and the
reference prices, and its identity is a content address, so two runs on the same inputs are
byte-identical and a stored list can be compared to a recomputed one.

Sizing: a buy is `lot_quantity` of the equal share (less the cost reserve) at the signal-day
reference price, further limited by **the cash the account holds at 09:15** -- tonight's sales
fill in the same auction, after the buys are frozen, so their proceeds are not this night's cash
-- less what buys still live from earlier sessions will spend, so a book that is nearly fully
invested cannot over-order.
A buy that cannot be afforded even one lot with what is left is **not placed**; the name was
drawn today and is simply not bought -- it is eligible to be drawn again in a later session.
It is not dropped silently either: the trade list names it in `unfunded`, so the evening's list
says which decided entrants it could not buy.
The cash cap carries the same cost reserve as the equal share, so a buy that is limited by cash
still leaves room for the fees and slippage that will come out of it.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.survivor_book import SurvivorBookConfig, lot_quantity
from openalpha_cn.domain.survivor_policy import BookDecision, BookState

Side = Literal["buy", "sell"]
OrderReason = Literal[
    "INITIAL_BUILD",
    "ENTRY_REPLACEMENT",
    "EXIT_SURVIVOR_LOST",
    "EXIT_ELIGIBILITY_LOST",
]


class TradeOrder(BaseModel):
    """One order, executed at the open of `execute_session`; immutable once planned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trade-order/v1"] = "trade-order/v1"
    signal_session: int = Field(ge=0)
    execute_session: int = Field(ge=1)
    expires_session: int | None = None
    """The last session a buy may still fill; `None` for a sell, which never expires."""
    side: Side
    ts_code: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    reference_price: Decimal = Field(gt=0)
    reason: OrderReason

    @model_validator(mode="after")
    def _the_order_is_one_a_book_could_have_planned(self) -> Self:
        if self.execute_session <= self.signal_session:
            raise ValueError("an order executes after the session that planned it")
        if self.side == "sell":
            if self.expires_session is not None:
                raise ValueError("a sell never expires")
            if not self.reason.startswith("EXIT_"):
                raise ValueError("a sell carries an exit reason")
        else:
            if self.expires_session is None or self.expires_session < self.execute_session:
                raise ValueError("a buy expires on or after the session it executes")
            if self.reason.startswith("EXIT_"):
                raise ValueError("a buy carries an entry reason")
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def order_id(self) -> str:
        return stable_model_id(prefix="tor", model=self)


class TradeList(BaseModel):
    """The orders planned from one signal session: sells first (by `ts_code`), then buys in the
    order the selection rule drew them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trade-list/v1"] = "trade-list/v1"
    signal_session: int = Field(ge=0)
    orders: tuple[TradeOrder, ...]
    carried: tuple[TradeOrder, ...] = ()
    """Orders planned on an earlier session that are still live; listed, not re-planned."""
    unfunded: tuple[str, ...] = ()
    """The `ts_code`s of the decision's entries that produced no order, in the order the
    selection rule drew them (`V2-P7-017`): each was decided, and not one lot of it could be
    bought with what the sizing left -- the cash at 09:15 (tonight's sells' proceeds arrive after
    the auction), the buys placed before it and the buys carried from earlier sessions, or the
    equal share itself where the reference price is above the one the decision sized with. Part
    of `trade_list_id`.

    Not `BookDecision.unaffordable`: those are skipped at **decision** time, because the equal
    share of equity cannot buy a lot of them, and never become entries. An unfunded name was an
    entry, and is not bought today; it may be drawn again in a later session."""

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def trade_list_id(self) -> str:
        return stable_model_id(prefix="trl", model=self)


def _price_of(code: str, prices: Mapping[str, Decimal]) -> Decimal:
    try:
        return prices[code]
    except KeyError:
        raise ValueError(f"no reference price for {code}") from None


def build_trade_list(
    *,
    config: SurvivorBookConfig,
    state: BookState,
    decision: BookDecision,
    reference_prices: Mapping[str, Decimal],
    cash: Decimal,
    carried: tuple[TradeOrder, ...] = (),
) -> TradeList:
    """Turn `decision` into orders for the session after `state.session`."""
    if cash < 0:
        raise ValueError("cash must not be negative")
    if decision.session != state.session:
        raise ValueError(
            f"the decision is for session {decision.session}, the state for {state.session}"
        )
    live_buys = {order.ts_code for order in carried if order.side == "buy"}
    live_sells = {order.ts_code for order in carried if order.side == "sell"}
    if live_buys != set(state.pending_buys) or live_sells != set(state.pending_sells):
        raise ValueError(
            "the carried orders disagree with the state: buys "
            f"{sorted(live_buys)} against pending_buys {sorted(state.pending_buys)}, sells "
            f"{sorted(live_sells)} against pending_sells {sorted(state.pending_sells)}"
        )
    if any(order.signal_session >= state.session for order in carried):
        raise ValueError("a carried order was planned on an earlier session than the state's")
    execute = state.session + 1
    held = {holding.ts_code: holding.quantity for holding in state.holdings}

    sells: list[TradeOrder] = []
    for decided in decision.exits:
        if decided.ts_code not in held:
            raise ValueError(f"cannot sell {decided.ts_code}: it is not held")
        price = _price_of(decided.ts_code, reference_prices)
        quantity = held[decided.ts_code]
        sells.append(
            TradeOrder(
                signal_session=state.session,
                execute_session=execute,
                side="sell",
                ts_code=decided.ts_code,
                quantity=quantity,
                reference_price=price,
                reason=decided.reason,
            )
        )

    unit_budget = config.sizing_budget(state.equity)
    # A buy still live from an earlier session will fill out of the same cash, so it is spent
    # before today's buys see any. Tonight's sales are not counted: they fill in the same
    # 09:15 auction the buys are placed in, after the broker has frozen the buys' cash.
    reserved = sum(
        (order.reference_price * order.quantity for order in carried if order.side == "buy"),
        Decimal(0),
    )
    available = max(cash - reserved, Decimal(0))
    buys: list[TradeOrder] = []
    unfunded: list[str] = []
    for entry in decision.entries:
        price = _price_of(entry.ts_code, reference_prices)
        quantity = min(
            lot_quantity(unit_budget, price, config.lot_size),
            lot_quantity(available * (1 - config.cost_reserve_fraction), price, config.lot_size),
        )
        if quantity == 0:
            unfunded.append(entry.ts_code)
            continue
        available -= price * quantity
        buys.append(
            TradeOrder(
                signal_session=state.session,
                execute_session=execute,
                expires_session=state.session + config.buy_order_expiry_sessions,
                side="buy",
                ts_code=entry.ts_code,
                quantity=quantity,
                reference_price=price,
                reason=entry.reason,
            )
        )

    return TradeList(
        signal_session=state.session,
        orders=tuple(sorted(sells, key=lambda o: o.ts_code)) + tuple(buys),
        carried=tuple(
            sorted(carried, key=lambda order: (order.side, order.ts_code, order.order_id))
        ),
        unfunded=tuple(unfunded),
    )
