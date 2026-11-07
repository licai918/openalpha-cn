"""Orders and the daily trade list (`V2-P7-005`, `V2-P7-008`).

Quantities are worked out in the comments from the sizing rule -- an equal share of equity less
the 0.5% cost reserve, in whole lots of 100, further limited by the cash at 09:15 frozen at each
buy's limit-up (1.10x its reference price, rounded half up to the fen) -- so the expectation is
arithmetic, not a transcription of what the code returned.
"""

import re
from decimal import Decimal

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.execution import derived_price_band
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_orders import (
    FREEZE_RATIO,
    FREEZE_SHORTFALL_POLICY,
    TradeList,
    TradeOrder,
    build_trade_list,
    freeze_price,
)
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
    # Cash 3,000 and nothing sold: frozen at B's 13.75 limit-up less the 0.5% reserve, 2,985 is
    # 217 shares, 200 in whole lots, which is less than the 700 an equal share would buy.
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("B", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("3000"),
    )

    assert [o.quantity for o in plan.orders] == [200]


def test_the_proceeds_of_tonights_sells_are_not_cash_at_0915() -> None:
    # Cash 5,000; A sells 300 at 10.00, which releases 3,000.00 in the same call auction the
    # buys are placed in, after the broker has frozen them. Sizing on the cash alone, B bought at
    # its 13.75 limit-up: 4,975.00 buys 300 shares (4,975 // 1,375 = 3 lots). Counting the
    # proceeds it would buy 500 (7,960 // 1,375 = 5 lots) and freeze 6,875.00 of the 8,000.00.
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("5000"),
    )

    assert [(o.side, o.quantity) for o in plan.orders] == [("sell", 300), ("buy", 300)]


def test_a_buy_only_tonights_sale_could_fund_is_unfunded_and_named() -> None:
    # No cash at all: the 3,000.00 A will release arrives after the auction, so B is not placed
    # and is named instead of silently dropped.
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("0"),
    )

    assert [(o.side, o.ts_code) for o in plan.orders] == [("sell", "A")]
    assert plan.unfunded == ("B",)


def test_each_buy_spends_from_what_the_one_before_left() -> None:
    # An equal share would buy 700 shares of B, but the cash is 5,000 less the 0.5% cost reserve,
    # 4,975, which freezes at most 361 shares at B's 13.75 limit-up -- 300 in whole lots. 4,125
    # is then frozen and 875 is left, which is not a lot of C at its 44.00 limit-up (4,400), so
    # C is not placed.
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("B", "ENTRY_REPLACEMENT"), ("C", "ENTRY_REPLACEMENT"))),
        reference_prices=PRICES,
        cash=Decimal("5000"),
    )

    assert [(o.ts_code, o.quantity) for o in plan.orders] == [("B", 300)]


def test_a_buy_that_cannot_afford_one_lot_is_not_placed() -> None:
    # 3,999 less the 0.5% reserve is 3,979.01, short of the 4,400 a lot of C freezes at its
    # 44.00 limit-up.
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

    # 9,950 buys 995 shares of A (900 in lots), 796 of B (700) and 248 of C (200); frozen at the
    # 11.00 / 13.75 / 44.00 limit-ups those three orders take 9,900.00, 9,625.00 and 8,800.00 of
    # the 40,000.00, so the cash cap never binds and the equal share is what sizes them.
    assert [(o.ts_code, o.quantity, o.reason) for o in plan.orders] == [
        ("A", 900, "INITIAL_BUILD"),
        ("B", 700, "INITIAL_BUILD"),
        ("C", 200, "INITIAL_BUILD"),
    ]


def test_a_buy_is_limited_by_the_cash_its_limit_up_price_freezes() -> None:
    # Ten equal shares of 350,000 at 10.00: 34,825.00 buys 3,400 shares each. A buy is placed at
    # the next session's limit-up, 11.00, so the broker freezes 37,400.00 for it. Nine freeze
    # 336,600.00; 13,400.00 is left, which freezes 1,200 shares of the tenth. Ten full ones
    # would freeze 374,000.00, more than the 350,000.00 the account has.
    names = tuple(f"6001{n:02d}.SH" for n in range(10))
    plan = build_trade_list(
        config=SurvivorBookConfig(seed=7),
        state=BookState(session=SESSION, initialized=False, equity=Decimal("350000")),
        decision=decision(entries=tuple((name, "INITIAL_BUILD") for name in names)),
        reference_prices=dict.fromkeys(names, Decimal("10")),
        cash=Decimal("350000"),
    )

    assert [(o.ts_code, o.quantity) for o in plan.orders] == [
        *((name, 3400) for name in names[:9]),
        (names[9], 1200),
    ]
    assert plan.unfunded == ()
    assert sum(
        (freeze_price(Decimal("10")) * o.quantity for o in plan.orders), Decimal("0")
    ) == Decimal("349800.00")
    assert sum(
        (freeze_price(Decimal("10")) * o.quantity for o in plan.orders), Decimal("0")
    ) <= Decimal("350000.00")


@pytest.mark.parametrize("close", ["9.99", "10.00", "10.03", "10.05", "1234.56"])
def test_the_freeze_price_is_the_main_boards_derived_limit_up(close: str) -> None:
    band, _ = derived_price_band(Decimal(close), board="main", is_st=False)
    assert freeze_price(Decimal(close)) == band


def test_a_normal_night_is_unchanged_when_the_freeze_does_not_bind() -> None:
    # Four equal shares of 40,000 at 12.50: the equal share buys 700 shares; the limit-up 13.75
    # freezes 9,625.00 of the 20,000.00 cash, so the cash cap never binds and the list is the one
    # the reference price alone gives.
    plan = build_trade_list(
        config=config(),
        state=book(("A", 300)),
        decision=decision(
            exits=(("A", "EXIT_SURVIVOR_LOST"),), entries=(("B", "ENTRY_REPLACEMENT"),)
        ),
        reference_prices=PRICES,
        cash=Decimal("20000"),
    )

    assert [o.quantity for o in plan.orders] == [300, 700]


def test_a_carried_buy_is_reserved_at_tonights_limit_up_price() -> None:
    # The live buy of 300 B is placed again tomorrow at B's limit-up, 13.75: 4,125.00 of the
    # 5,000.00 is frozen before today's buy sees any. The 875.00 left is not a lot of A at its
    # own 11.00 limit-up, so A is unfunded and named.
    plan = build_trade_list(
        config=config(),
        state=book(pending_buys=("B",)),
        decision=decision(entries=(("A", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("5000"),
        carried=(live_buy(),),
    )

    assert plan.orders == ()
    assert plan.unfunded == ("A",)


def test_a_config_that_trades_another_board_must_pass_its_own_freeze_prices() -> None:
    # The 10% is the main board's band; a config that also trades ChiNext must say what its
    # limit-up prices are rather than have the main board's ratio applied to it.
    with pytest.raises(ValueError, match="main board"):
        build_trade_list(
            config=config(boards=("main", "growth")),
            state=book(),
            decision=decision(entries=(("B", "ENTRY_REPLACEMENT"),)),
            reference_prices=PRICES,
            cash=Decimal("20000"),
        )


def test_the_drop_whole_policy_refuses_an_entry_it_cannot_freeze_in_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The decision this plan recommends against, implemented so the user's choice is one named
    # constant: with `drop_whole` the tenth name is not bought at all, where `reduce_lots`
    # buys it 1,200 shares.
    from openalpha_cn.domain import survivor_orders

    monkeypatch.setattr(survivor_orders, "FREEZE_SHORTFALL_POLICY", "drop_whole")
    names = tuple(f"6001{n:02d}.SH" for n in range(10))
    plan = build_trade_list(
        config=SurvivorBookConfig(seed=7),
        state=BookState(session=SESSION, initialized=False, equity=Decimal("350000")),
        decision=decision(entries=tuple((name, "INITIAL_BUILD") for name in names)),
        reference_prices=dict.fromkeys(names, Decimal("10")),
        cash=Decimal("350000"),
    )

    assert [o.quantity for o in plan.orders] == [3400] * 9
    assert plan.unfunded == (names[9],)


def test_the_freeze_ratio_and_the_shortfall_policy_are_the_ones_the_plan_froze() -> None:
    assert Decimal("1.10") == FREEZE_RATIO
    assert FREEZE_SHORTFALL_POLICY == "reduce_lots"
    assert freeze_price(Decimal("10.00")) == Decimal("11.00")


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

    # Cash 5,000 less the 4,125 the live buy freezes -- 300 B at its 13.75 limit-up -- leaves 875,
    # and 0.5% of that is kept back for costs: 870.625 is not one lot of A at its own 11.00
    # limit-up (1,100), so A is not placed. With nothing reserved the same decision would order
    # 400 (4,975 / 11), and the two buys together would freeze more than the book has.
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

    assert [o.quantity for o in with_live.orders] == []
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
    # The cap is applied to the cash at the limit-up, and the order's notional is its reference
    # price: 4,125.00 frozen is 3,750.00 at 12.50, under 5,000 x 0.995.
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


def test_an_entrant_the_cash_left_cannot_fund_is_recorded_as_unfunded() -> None:
    # As in `test_each_buy_spends_from_what_the_one_before_left`: 5,000 buys 300 of B, freezing
    # 4,125 at the 13.75 limit-up, and the 875 left is not a lot of C at its 44.00 limit-up
    # (4,400). C was decided and not ordered.
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("B", "ENTRY_REPLACEMENT"), ("C", "ENTRY_REPLACEMENT"))),
        reference_prices=PRICES,
        cash=Decimal("5000"),
    )

    assert [(o.ts_code, o.quantity) for o in plan.orders] == [("B", 300)]
    assert plan.unfunded == ("C",)


def test_an_entrant_whose_lot_costs_more_than_the_equal_share_is_recorded_as_unfunded() -> None:
    # The equal share is 40,000 / 4 less 0.5% = 9,950, and one lot of D at 100 is 10,000: the
    # 100,000 of cash would freeze it at D's 110.00 limit-up (11,000), the sizing budget does not.
    # A at 10 still gets its 900.
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(entries=(("D", "ENTRY_REPLACEMENT"), ("A", "ENTRY_REPLACEMENT"))),
        reference_prices={**PRICES, "D": Decimal("100")},
        cash=Decimal("100000"),
    )

    assert [(o.ts_code, o.quantity) for o in plan.orders] == [("A", 900)]
    assert plan.unfunded == ("D",)


def test_an_entrant_the_carried_buys_leave_no_cash_for_is_recorded_as_unfunded() -> None:
    # The live buy reserves 800 x 13.75 = 11,000.00 of the 5,000.00, more than all of it:
    # nothing is left for A.
    plan = build_trade_list(
        config=config(),
        state=book(pending_buys=("B",)),
        decision=decision(entries=(("A", "ENTRY_REPLACEMENT"),)),
        reference_prices=PRICES,
        cash=Decimal("5000"),
        carried=(live_buy(quantity=800),),
    )

    assert plan.orders == ()
    assert plan.unfunded == ("A",)


def test_unfunded_names_keep_the_order_the_selection_rule_drew_them_in() -> None:
    # Drawn D, B, C with 5,000: D's lot (10,000 at 100) is over the 9,950 share, B takes 300
    # (freezing 4,125 of the 5,000, leaving 875) and C's limit-up lot (4,400) is over the
    # 870.625 left. D then C -- not sorted.
    plan = build_trade_list(
        config=config(),
        state=book(),
        decision=decision(
            entries=(
                ("D", "ENTRY_REPLACEMENT"),
                ("B", "ENTRY_REPLACEMENT"),
                ("C", "ENTRY_REPLACEMENT"),
            )
        ),
        reference_prices={**PRICES, "D": Decimal("100")},
        cash=Decimal("5000"),
    )

    assert [(o.ts_code, o.quantity) for o in plan.orders] == [("B", 300)]
    assert plan.unfunded == ("D", "C")


def test_a_trade_list_that_funds_every_entrant_has_nothing_unfunded() -> None:
    first_build = build_trade_list(
        config=config(),
        state=BookState(session=SESSION, initialized=False, equity=Decimal("40000")),
        decision=decision(
            entries=(("A", "INITIAL_BUILD"), ("B", "INITIAL_BUILD"), ("C", "INITIAL_BUILD"))
        ),
        reference_prices=PRICES,
        cash=Decimal("40000"),
    )

    assert planned().unfunded == ()
    assert len(first_build.orders) == 3
    assert first_build.unfunded == ()


def test_the_unfunded_names_are_part_of_the_trade_lists_content_address() -> None:
    funded = TradeList(signal_session=SESSION, orders=())
    short = TradeList(signal_session=SESSION, orders=(), unfunded=("C",))

    assert funded.unfunded == ()
    assert short.trade_list_id != funded.trade_list_id
    assert TradeList.model_validate_json(short.model_dump_json(exclude_computed_fields=True)) == (
        short
    )


def test_selling_a_security_that_is_not_held_is_a_named_failure() -> None:
    with pytest.raises(ValueError, match="cannot sell Z: it is not held"):
        build_trade_list(
            config=config(),
            state=book(("A", 100)),
            decision=decision(exits=(("Z", "EXIT_SURVIVOR_LOST"),)),
            reference_prices={**PRICES, "Z": Decimal("10")},
            cash=Decimal("0"),
        )
