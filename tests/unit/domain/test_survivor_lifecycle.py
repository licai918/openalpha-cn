"""What becomes of an order after its session, and who is cooling down (`V2-P7-008`)."""

from decimal import Decimal

from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_lifecycle import advance_orders, update_cooldowns
from openalpha_cn.domain.survivor_orders import TradeOrder
from openalpha_cn.domain.survivor_policy import Cooldown


def order(code: str, side: str, *, signal: int = 100, expires: int | None = None) -> TradeOrder:
    return TradeOrder(
        signal_session=signal,
        execute_session=signal + 1,
        expires_session=expires if side == "buy" else None,
        side=side,  # type: ignore[arg-type]
        ts_code=code,
        quantity=100,
        reference_price=Decimal("10"),
        reason="ENTRY_REPLACEMENT" if side == "buy" else "EXIT_SURVIVOR_LOST",
    )


def test_a_filled_order_is_done_whatever_else_is_true_of_it() -> None:
    buy = order("A", "buy", expires=103)

    # Observed on its last session, filled, and no longer a survivor: the fill is what happened.
    result = advance_orders([buy], session=103, filled_order_ids={buy.order_id}, survivors=set())

    assert result.filled == (buy,)
    assert result.live == result.expired == result.cancelled == ()


def test_an_unfilled_sell_is_carried_for_as_long_as_it_takes() -> None:
    sell = order("A", "sell")

    for session in (101, 103, 150):
        result = advance_orders([sell], session=session, filled_order_ids=set(), survivors=set())
        assert result.live == (sell,)


def test_a_buy_is_carried_until_its_last_session_and_then_expires() -> None:
    buy = order("A", "buy", expires=103)

    carried = advance_orders([buy], session=102, filled_order_ids=set(), survivors={"A"})
    assert carried.live == (buy,)

    expired = advance_orders([buy], session=103, filled_order_ids=set(), survivors={"A"})
    assert expired.expired == (buy,)
    assert expired.live == ()


def test_a_buy_for_a_name_that_is_no_longer_a_survivor_is_cancelled_not_carried() -> None:
    buy = order("A", "buy", expires=103)

    result = advance_orders([buy], session=101, filled_order_ids=set(), survivors={"B"})

    assert result.cancelled == (buy,)
    assert result.live == ()


def test_a_sell_is_carried_even_when_its_name_is_not_a_survivor() -> None:
    sell = order("A", "sell")

    result = advance_orders([sell], session=101, filled_order_ids=set(), survivors=set())

    assert result.live == (sell,)
    assert result.cancelled == ()


def test_the_outcome_is_ordered_and_does_not_depend_on_the_order_given() -> None:
    orders = [order("B", "buy", expires=103), order("A", "buy", expires=103), order("C", "sell")]

    forward = advance_orders(orders, session=101, filled_order_ids=set(), survivors={"A", "B"})
    backward = advance_orders(
        list(reversed(orders)), session=101, filled_order_ids=set(), survivors={"A", "B"}
    )

    assert forward == backward
    assert [o.ts_code for o in forward.live] == ["A", "B", "C"]


def config() -> SurvivorBookConfig:
    return SurvivorBookConfig(seed=1, cooldown_sessions=5)


def test_a_sale_and_an_expired_buy_each_start_a_cooldown() -> None:
    result = update_cooldowns((), session=100, sold={"B"}, expired_buys={"A"}, config=config())

    assert result == (
        Cooldown(ts_code="A", since_session=100),
        Cooldown(ts_code="B", since_session=100),
    )


def test_a_cooldown_that_has_run_out_is_dropped_and_one_still_running_is_kept() -> None:
    existing = (
        Cooldown(ts_code="OLD", since_session=95),
        Cooldown(ts_code="NEW", since_session=96),
    )

    result = update_cooldowns(
        existing, session=100, sold=set(), expired_buys=set(), config=config()
    )

    # Five sessions have passed for OLD (100 - 95 = 5, not less than 5): it is free again.
    assert result == (Cooldown(ts_code="NEW", since_session=96),)


def test_selling_a_name_again_restarts_its_cooldown() -> None:
    existing = (Cooldown(ts_code="A", since_session=98),)

    result = update_cooldowns(
        existing, session=100, sold={"A"}, expired_buys=set(), config=config()
    )

    assert result == (Cooldown(ts_code="A", since_session=100),)
