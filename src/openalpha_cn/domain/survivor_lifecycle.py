"""What happens to an order after the session it was meant for (`V2-P7-008`).

An order is *filled*, or it stays *live*, or it is *expired* (a buy past its last session) or
*cancelled* (a buy whose name is no longer a survivor). A sell is live until it fills; there is no
state in which a holding that must go is quietly kept. The functions take what the paper ledger
observed -- which orders filled -- and decide nothing about prices or fills themselves.
"""

from collections.abc import Collection, Sequence

from pydantic import BaseModel, ConfigDict

from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_orders import TradeOrder
from openalpha_cn.domain.survivor_policy import Cooldown


class OrderAdvance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    live: tuple[TradeOrder, ...]
    filled: tuple[TradeOrder, ...]
    expired: tuple[TradeOrder, ...]
    cancelled: tuple[TradeOrder, ...]


def advance_orders(
    orders: Sequence[TradeOrder],
    *,
    session: int,
    filled_order_ids: Collection[str],
    survivors: Collection[str],
) -> OrderAdvance:
    """Settle `orders` after `session` has been observed.

    `survivors` is today's survivor set: a buy for a name outside it is cancelled rather than
    carried -- it was chosen yesterday and nothing today still recommends it.
    """
    live: list[TradeOrder] = []
    filled: list[TradeOrder] = []
    expired: list[TradeOrder] = []
    cancelled: list[TradeOrder] = []
    for order in sorted(orders, key=lambda o: (o.side, o.ts_code, o.order_id)):
        if order.order_id in filled_order_ids:
            filled.append(order)
        elif order.side == "sell":
            live.append(order)
        elif order.expires_session is not None and session >= order.expires_session:
            expired.append(order)
        elif order.ts_code not in survivors:
            cancelled.append(order)
        else:
            live.append(order)
    return OrderAdvance(
        live=tuple(live),
        filled=tuple(filled),
        expired=tuple(expired),
        cancelled=tuple(cancelled),
    )


def update_cooldowns(
    cooldowns: Sequence[Cooldown],
    *,
    session: int,
    sold: Collection[str],
    expired_buys: Collection[str],
    config: SurvivorBookConfig,
) -> tuple[Cooldown, ...]:
    """Start a cool-down for names sold or whose buy expired; drop the ones that have run out.

    An expired buy cools down too: a name that could not be bought for three sessions (a run of
    limit-ups, a halt) is not chased again the next morning.
    """
    kept = {
        cooldown.ts_code: cooldown
        for cooldown in cooldowns
        if session - cooldown.since_session < config.cooldown_sessions
    }
    for code in sorted(set(sold) | set(expired_buys)):
        kept[code] = Cooldown(ts_code=code, since_session=session)
    return tuple(kept[code] for code in sorted(kept))
