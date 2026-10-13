"""Orders and the daily trade list (`V2-P7-005`, `V2-P7-008`): a decision becomes whole lots.

The trade list is **the product the user reads each evening**: what to sell, what to buy, how many
shares, at what reference price, and why. It is a pure function of the decision, the state and the
reference prices, and its identity is a content address, so two runs on the same inputs are
byte-identical and a stored list can be compared to a recomputed one.

Sizing: a buy is `lot_quantity` of the equal share (less the cost reserve) at the signal-day
reference price, and is further limited by the cash that will be there -- today's cash plus the
estimated proceeds of today's sells, less what buys still live from earlier sessions will spend --
so a book that is nearly fully invested cannot over-order.
A buy that cannot be afforded even one lot with what is left is **not placed**; the name stays on
the watch list and is drawn again tomorrow.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

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

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def order_id(self) -> str:
        return stable_model_id(prefix="tor", model=self)


class TradeList(BaseModel):
    """The orders planned from one signal session, sells before buys, each by `ts_code`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trade-list/v1"] = "trade-list/v1"
    signal_session: int = Field(ge=0)
    orders: tuple[TradeOrder, ...]
    carried: tuple[TradeOrder, ...] = ()
    """Orders planned on an earlier session that are still live; listed, not re-planned."""

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
    execute = state.session + 1
    held = {holding.ts_code: holding.quantity for holding in state.holdings}

    sells: list[TradeOrder] = []
    proceeds = Decimal(0)
    for decided in decision.exits:
        if decided.ts_code not in held:
            raise ValueError(f"cannot sell {decided.ts_code}: it is not held")
        price = _price_of(decided.ts_code, reference_prices)
        quantity = held[decided.ts_code]
        proceeds += price * quantity
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
    # before today's buys see any. Live sells are not counted: when they fill is not known.
    reserved = sum(
        (order.reference_price * order.quantity for order in carried if order.side == "buy"),
        Decimal(0),
    )
    available = max(cash + proceeds - reserved, Decimal(0))
    buys: list[TradeOrder] = []
    for entry in decision.entries:
        price = _price_of(entry.ts_code, reference_prices)
        quantity = min(
            lot_quantity(unit_budget, price, config.lot_size),
            lot_quantity(available, price, config.lot_size),
        )
        if quantity == 0:
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
        carried=carried,
    )
