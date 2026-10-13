"""Orders and the daily trade list (`V2-P7-005`, `V2-P7-008`).

Quantities are worked out in the comments from the sizing rule -- an equal share of equity less
the 0.5% cost reserve, in whole lots of 100, further limited by the cash that will be there -- so
the expectation is arithmetic, not a transcription of what the code returned.
"""

import re
from decimal import Decimal

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_orders import TradeList, TradeOrder, build_trade_list
from openalpha_cn.domain.survivor_policy import (
    BookDecision,
    BookState,
    EntryDecision,
    ExitDecision,
    Holding,
)

SESSION = 100


def config(**overrides: object) -> SurvivorBookConfig:
    settings: dict[str, object] = {
        "seed": 1,
        "holding_count": 4,
        "capital": Decimal("40000"),
        "buy_order_expiry_sessions": 3,
    }
    settings.update(overrides)
    return SurvivorBookConfig(**settings)  # type: ignore[arg-type]


def book(
    *holdings: tuple[str, int],
    equity: str = "40000",
    pending_buys: tuple[str, ...] = (),
    pending_sells: tuple[str, ...] = (),
) -> BookState:
    return BookState(
        session=SESSION,
        initialized=True,
        equity=Decimal(equity),
        holdings=tuple(Holding(ts_code=code, quantity=quantity) for code, quantity in holdings),
        pending_buys=pending_buys,
        pending_sells=pending_sells,
    )


def decision(
    *,
    exits: tuple[tuple[str, str], ...] = (),
    entries: tuple[tuple[str, str], ...] = (),
) -> BookDecision:
    return BookDecision(
        session=SESSION,
        exits=tuple(ExitDecision(ts_code=c, reason=r) for c, r in exits),  # type: ignore[arg-type]
        entries=tuple(EntryDecision(ts_code=c, reason=r) for c, r in entries),  # type: ignore[arg-type]
        deferred_exits=(),
        near_exit=(),
        watch_list=(),
        unaffordable=(),
        target_holdings=(),
    )


PRICES = {"A": Decimal("10"), "B": Decimal("12.5"), "C": Decimal("40")}


def test_a_sell_is_the_whole_holding_and_a_buy_is_whole_lots_of_an_equal_share() -> None:
    # Share per name: 40,000 / 4 = 10,000, less the 0.5% reserve = 9,950. At 12.5 that is 796
    # shares, which is 700 in whole lots. Cash 20,000 plus 3,000 from selling A is plenty.
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("20000"),
    )

    sell, buy = plan.orders
    assert (sell.side, sell.ts_code, sell.quantity, sell.reason) == (
        "sell",
        "A",
        300,
        "EXIT_SURVIVOR_LOST",
    )
    assert (buy.side, buy.ts_code, buy.quantity, buy.reason) == (
        "buy",
        "B",
        700,
        "ENTRY_REPLACEMENT",
    )
    assert sell.reference_price == Decimal("10")
    assert buy.reference_price == Decimal("12.5")


def test_orders_execute_the_next_session_and_only_buys_expire() -> None:
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_ELIGIBILITY_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("20000"),
    )
    sell, buy = plan.orders

    assert (sell.signal_session, sell.execute_session, sell.expires_session) == (100, 101, None)
    assert (buy.signal_session, buy.execute_session, buy.expires_session) == (100, 101, 103)


def test_sells_come_first_by_code_and_buys_follow_in_the_decisions_order() -> None:
    plan = build_trade_list(
        config=config(),
        state=book(("C", 100), ("A", 100)),
        decision=decision(
            exits=(("C", "EXIT_SURVIVOR_LOST"), ("A", "EXIT_SURVIVOR_LOST")),
            entries=(("B", "ENTRY_REPLACEMENT"), ("C", "ENTRY_REPLACEMENT")),
        ),
        reference_prices=PRICES,
        cash=Decimal("20000"),
    )

    assert [(o.side, o.ts_code) for o in plan.orders] == [
        ("sell", "A"),
        ("sell", "C"),
        ("buy", "B"),
        ("buy", "C"),
    ]


def test_a_buy_is_limited_by_the_cash_that_will_be_there() -> None:
    # Cash 3,000 and nothing sold: at 12.5 that is 240 shares, 200 in whole lots, which is less
    # than the 700 an equal share would buy.
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("B", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("3000"),
    )

    assert [o.quantity for o in plan.orders] == [200]


def test_the_proceeds_of_a_sell_count_towards_what_a_buy_may_spend() -> None:
    # Cash 0; A sells 300 at 10 for 3,000, enough for 200 shares of B at 12.5.
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("0"),
    )

    assert [(o.side, o.quantity) for o in plan.orders] == [("sell", 300), ("buy", 200)]


def test_each_buy_spends_from_what_the_one_before_left() -> None:
    # An equal share would buy 700 shares of B, but the cash is 5,000 less the 0.5% cost reserve,
    # 4,975, which buys at most 398 shares at 12.5 -- 300 in whole lots. 1,250 is then left, which
    # is not a lot of C at 40 (4,000), so C is not placed.
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("B", "ENTRY_REPLACEMENT"), ("C", "ENTRY_REPLACEMENT"))),
        reference_prices=PRICES,
        cash=Decimal("5000"),
    )

    assert [(o.ts_code, o.quantity) for o in plan.orders] == [("B", 300)]


def test_a_buy_that_cannot_afford_one_lot_is_not_placed() -> None:
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("C", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("3999"),
    )

    assert plan.orders == ()


def test_the_first_build_is_sized_to_the_equity_not_to_the_cash_left_over() -> None:
    plan = build_trade_list(
        config=config(),
        state=BookState(session=SESSION, initialized=False, equity=Decimal("40000")),
        decision=decision(
            entries=(("A", "INITIAL_BUILD"), ("B", "INITIAL_BUILD"), ("C", "INITIAL_BUILD"))
        ),
        reference_prices=PRICES,
        cash=Decimal("40000"),
    )

    # 9,950 buys 995 shares of A (900 in lots), 796 of B (700) and 248 of C (200).
    assert [(o.ts_code, o.quantity, o.reason) for o in plan.orders] == [
        ("A", 900, "INITIAL_BUILD"),
        ("B", 700, "INITIAL_BUILD"),
        ("C", 200, "INITIAL_BUILD"),
    ]


def test_every_quantity_is_a_whole_number_of_lots() -> None:
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("20000"),
    )

    assert all(order.quantity % 100 == 0 for order in plan.orders)


def test_a_missing_reference_price_is_a_named_failure() -> None:
    with pytest.raises(ValueError, match="no reference price for Z"):
        build_trade_list(
            config=config(),
            state=book(),
            decision=decision(entries=(("Z", "ENTRY_REPLACEMENT"),)),
            reference_prices=PRICES,
            cash=Decimal("20000"),
        )


def test_negative_cash_is_refused() -> None:
    with pytest.raises(ValueError, match="cash must not be negative"):
        build_trade_list(
            config=config(),
            state=book(),
            decision=decision(),
            reference_prices=PRICES,
            cash=Decimal("-1"),
        )


def planned(cash: str = "20000") -> TradeList:
    return build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal(cash),
    )


def test_the_same_inputs_give_the_same_orders_and_the_same_identities() -> None:
    first, second = planned(), planned()

    assert first == second
    assert first.trade_list_id == second.trade_list_id
    assert [o.order_id for o in first.orders] == [o.order_id for o in second.orders]


def test_the_identities_are_content_addresses_that_move_with_the_content() -> None:
    first = planned()
    assert re.fullmatch(r"trl_[0-9a-f]{24}", first.trade_list_id)
    assert all(re.fullmatch(r"tor_[0-9a-f]{24}", o.order_id) for o in first.orders)

    smaller = planned(cash="3000")  # the same decision, a different buy quantity
    assert smaller.trade_list_id != first.trade_list_id
    assert smaller.orders[0].order_id == first.orders[0].order_id  # the sell did not change
    assert smaller.orders[1].order_id != first.orders[1].order_id


def live_buy(code: str = "B", quantity: int = 300, price: str = "12.5") -> TradeOrder:
    return TradeOrder(
        signal_session=99,
        execute_session=100,
        expires_session=102,
        side="buy",
        ts_code=code,
        quantity=quantity,
        reference_price=Decimal(price),
        reason="ENTRY_REPLACEMENT",
    )


def test_a_carried_order_is_listed_without_being_planned_again() -> None:
    earlier = live_buy()
    plan = build_trade_list(
        config=config(),
        state=book(pending_buys=("B",)),
        decision=decision(),
        reference_prices=PRICES,
        cash=Decimal("0"),
        carried=(earlier,),
    )

    assert plan.orders == ()
    assert plan.carried == (earlier,)


def test_the_stored_form_reads_back_without_the_computed_identities() -> None:
    held = planned()
    stored = held.model_dump_json(exclude_computed_fields=True)

    assert TradeList.model_validate_json(stored) == held
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TradeList.model_validate_json(held.model_dump_json())


@pytest.mark.parametrize(
    "overrides",
    [{"quantity": 0}, {"reference_price": Decimal("0")}, {"execute_session": 0}],
)
def test_an_order_with_a_nonsense_field_is_refused(overrides: dict[str, object]) -> None:
    base: dict[str, object] = {
        "signal_session": 1,
        "execute_session": 2,
        "side": "buy",
        "ts_code": "A",
        "quantity": 100,
        "reference_price": Decimal("10"),
        "reason": "INITIAL_BUILD",
    }
    base.update(overrides)
    with pytest.raises(ValidationError):
        TradeOrder(**base)  # type: ignore[arg-type]


def test_a_buy_still_live_from_an_earlier_session_is_paid_for_before_todays_buys() -> None:
    live = live_buy()

    # Cash 5,000 less the 3,750 the live buy will spend leaves 1,250, and 0.5% of that is kept back
    # for costs: 1,243.75 buys 124 shares of A at 10, 100 in whole lots. With nothing reserved the
    # same decision would order 400 (4,975 / 10), and the two buys together would ask for more
    # than the book has.
    with_live = build_trade_list(
        config=config(),
        state=book(pending_buys=("B",)),
        decision=decision(entries=(("A", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("5000"),
        carried=(live,),
    )
    without = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("A", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("5000"),
    )

    assert [o.quantity for o in with_live.orders] == [100]
    assert [o.quantity for o in without.orders] == [400]


def test_a_live_buy_that_uses_up_the_cash_leaves_nothing_for_a_new_one() -> None:
    plan = build_trade_list(
        config=config(),
        state=book(pending_buys=("B",)),
        decision=decision(entries=(("A", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("5000"),
        carried=(live_buy(quantity=800),),
    )

    assert plan.orders == ()


def test_a_buy_limited_by_cash_still_leaves_the_cost_reserve() -> None:
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("B", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("5000"),
    )

    (order,) = plan.orders
    spent = order.reference_price * order.quantity
    assert spent <= Decimal("5000") * (1 - config().cost_reserve_fraction)


def test_the_carried_orders_must_be_the_ones_the_state_says_are_live() -> None:
    kwargs = {
        "config": config(),
        "decision": decision(),
        "reference_prices": PRICES,
        "cash": Decimal("0"),
    }

    with pytest.raises(ValueError, match="carried orders disagree with the state"):
        build_trade_list(state=book(), carried=(live_buy(),), **kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="carried orders disagree with the state"):
        build_trade_list(state=book(pending_buys=("B",)), carried=(), **kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="planned on an earlier session"):
        stale = live_buy().model_copy(update={"signal_session": 100, "execute_session": 101})
        build_trade_list(
            state=book(pending_buys=("B",)),
            carried=(stale,),
            **kwargs,  # type: ignore[arg-type]
        )


def test_a_decision_for_another_session_is_refused() -> None:
    other = decision().model_copy(update={"session": SESSION + 1})

    with pytest.raises(ValueError, match="the decision is for session"):
        build_trade_list(
            config=config(),
            state=book(),
            decision=other,
            reference_prices=PRICES,
            cash=Decimal("0"),
        )


def test_the_carried_orders_are_listed_in_one_canonical_order() -> None:
    first, second = live_buy("B"), live_buy("C", price="40", quantity=100)
    kwargs = {
        "config": config(),
        "state": book(pending_buys=("B", "C")),
        "decision": decision(),
        "reference_prices": PRICES,
        "cash": Decimal("100000"),
    }

    one = build_trade_list(carried=(first, second), **kwargs)  # type: ignore[arg-type]
    other = build_trade_list(carried=(second, first), **kwargs)  # type: ignore[arg-type]

    assert one == other
    assert one.trade_list_id == other.trade_list_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"execute_session": 1, "signal_session": 1},
        {"expires_session": None},  # a buy that never expires
        {"expires_session": 1},  # a buy that expires before it executes
        {"reason": "EXIT_SURVIVOR_LOST"},  # a buy with an exit reason
        {"side": "sell"},  # a sell that carries an expiry and an entry reason
        {"side": "sell", "expires_session": None},  # a sell with an entry reason
    ],
)
def test_an_order_a_book_could_not_have_planned_is_refused(overrides: dict[str, object]) -> None:
    base: dict[str, object] = {
        "signal_session": 1,
        "execute_session": 2,
        "expires_session": 4,
        "side": "buy",
        "ts_code": "A",
        "quantity": 100,
        "reference_price": Decimal("10"),
        "reason": "ENTRY_REPLACEMENT",
    }
    base.update(overrides)
    with pytest.raises(ValidationError):
        TradeOrder(**base)  # type: ignore[arg-type]


def test_selling_a_security_that_is_not_held_is_a_named_failure() -> None:
    with pytest.raises(ValueError, match="cannot sell Z: it is not held"):
        build_trade_list(
            config=config(),
            state=book(("A", 100)),
            decision=decision(exits=(("Z", "EXIT_SURVIVOR_LOST"),)),
            reference_prices={**PRICES, "Z": Decimal("10")},
            cash=Decimal("0"),
        )
