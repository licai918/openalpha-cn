"""One step is one trading session holding the whole book (`V2-P5-003`).

Every test here fails on the single-subject step in one of two ways, and both are stated
because they are different failures: the shape tests cannot be *expressed* against a model
carrying one `order` and one `market`, and the two accounting tests below
(`test_a_held_name_that_is_not_traded_today_still_re_marks_to_todays_close` and
`test_the_days_re_mark_lands_before_the_days_orders_so_the_cap_sees_todays_price`) separate two
live implementations of the new shape -- one that marks the book and one that does not, and one
that marks before the orders and one that marks after.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.multi_day import (
    PortfolioBacktestRunner,
    PortfolioBacktestStep,
)
from openalpha_cn.backtest.portfolio import PortfolioLimits, PortfolioOrder, PortfolioState


def bar(subject: str, trade_date: date, close: str, previous_close: str = "10.00") -> MarketBar:
    """A flat bar for one subject, so a test's arithmetic is the day's close and nothing else."""
    price = Decimal(close)
    return MarketBar(
        subject=subject,
        trade_date=trade_date,
        board="main",
        previous_close=Decimal(previous_close),
        open=price,
        high=price,
        low=price,
        close=price,
        suspended=False,
        is_st=False,
    )


DAY_ONE = date(2026, 7, 24)
DAY_TWO = date(2026, 7, 27)
OPENING = PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("100000.00"))


def buy(order_id: str, subject: str, quantity: int) -> PortfolioOrder:
    return PortfolioOrder(order_id=order_id, subject=subject, side="buy", quantity=quantity)


def test_one_session_of_a_k_name_book_is_one_step_and_one_equity_point() -> None:
    """The shape claim: K names on one session cost one step, and produce one dated point.

    The old step carried one order and one bar, so a two-name session was two steps and the
    curve carried the same `trade_date` twice -- measured on the pre-`V2-P5-003` runner, which
    returned `[('2026-07-24', ...), ('2026-07-24', ...)]` for exactly this book.
    """
    report = PortfolioBacktestRunner().run(
        initial=OPENING,
        steps=(
            PortfolioBacktestStep(
                trade_date=DAY_ONE,
                bars=(bar("AAA.SZ", DAY_ONE, "10.00"), bar("BBB.SZ", DAY_ONE, "10.00")),
                orders=(buy("a1", "AAA.SZ", 1000), buy("b1", "BBB.SZ", 1000)),
                benchmark_close=Decimal("100"),
            ),
        ),
    )

    assert len(report.equity_curve) == 1
    assert report.equity_curve[0].trade_date == "2026-07-24"
    assert len(report.transitions) == 2
    assert {transition.status for transition in report.transitions} == {"filled"}
    assert report.final_state.position("AAA.SZ").quantity == 1000
    assert report.final_state.position("BBB.SZ").quantity == 1000


def test_a_held_name_that_is_not_traded_today_still_re_marks_to_todays_close() -> None:
    """The accounting claim, and the defect `V2-P5-003` exists to close.

    On the single-subject step the *only* way to hand the runner a price was to attach an order
    to it, so a book could not be told what a name it was holding closed at unless it traded it.
    Measured on the pre-`V2-P5-003` runner with this exact book: reported market value
    `21000.00` against a true `31000`, and `max_gross_exposure` `0.210032` against a true
    ~`0.31`.

    Day two carries **no orders at all**, which the old model could not represent either.
    """
    runner = PortfolioBacktestRunner()
    report = runner.run(
        initial=OPENING,
        steps=(
            PortfolioBacktestStep(
                trade_date=DAY_ONE,
                bars=(bar("AAA.SZ", DAY_ONE, "10.00"), bar("BBB.SZ", DAY_ONE, "10.00")),
                orders=(buy("a1", "AAA.SZ", 1000), buy("b1", "BBB.SZ", 1000)),
                benchmark_close=Decimal("100"),
            ),
            PortfolioBacktestStep(
                trade_date=DAY_TWO,
                bars=(
                    bar("AAA.SZ", DAY_TWO, "10.00"),
                    bar("BBB.SZ", DAY_TWO, "20.00", previous_close="10.00"),
                ),
                orders=(),
                benchmark_close=Decimal("100"),
            ),
        ),
    )

    assert report.final_state.mark("BBB.SZ") == Decimal("20.00")
    assert report.final_state.market_value == Decimal("30000.00")
    assert report.final_state.as_of == DAY_TWO
    assert len(report.equity_curve) == 2
    assert report.equity_curve[1].equity > report.equity_curve[0].equity
    assert report.carried_marks == ()


def test_the_days_re_mark_lands_before_the_days_orders_so_the_cap_sees_todays_price() -> None:
    """Marking before the orders and marking after them are two different answers here.

    Day one buys 7,000 of AAA at 10.00, leaving the book at roughly 70% gross exposure against
    an 80% cap. Day two AAA closes at 20.00 and the book has drifted to ~82% -- above the cap,
    which is a fact about a book nobody rebalanced and not a rejection, because the simulator
    only checks a cap when a buy asks it to. The day-two buy of BBB is that buy:

      - marked first, it is refused `maximum total exposure exceeded` on today's prices;
      - marked afterwards (or not at all), AAA is still 10.00, the book reads ~71%, and the
        same order **fills**.
    """
    runner = PortfolioBacktestRunner(
        limits=PortfolioLimits(
            max_position_weight=Decimal("1"),
            max_total_exposure=Decimal("0.80"),
        )
    )
    report = runner.run(
        initial=OPENING,
        steps=(
            PortfolioBacktestStep(
                trade_date=DAY_ONE,
                bars=(bar("AAA.SZ", DAY_ONE, "10.00"),),
                orders=(buy("a1", "AAA.SZ", 7000),),
                benchmark_close=Decimal("100"),
            ),
            PortfolioBacktestStep(
                trade_date=DAY_TWO,
                bars=(
                    bar("AAA.SZ", DAY_TWO, "20.00", previous_close="10.00"),
                    bar("BBB.SZ", DAY_TWO, "10.00"),
                ),
                orders=(buy("b1", "BBB.SZ", 100),),
                benchmark_close=Decimal("100"),
            ),
        ),
    )

    refusal = report.transitions[-1]
    assert refusal.status == "rejected"
    assert refusal.reason == "maximum total exposure exceeded"
    assert refusal.before.mark("AAA.SZ") == Decimal("20.00")
    assert report.final_state.position("BBB.SZ").quantity == 0
    assert report.max_gross_exposure > Decimal("0.80")


def test_a_held_name_with_no_bar_today_carries_its_mark_and_is_named_for_carrying_it() -> None:
    """An A-share halt serves no daily row, so a missing bar is a real shape rather than an
    input error -- refusing it would make a halt unrepresentable. What is refused instead is
    carrying the stale price *silently*: every carry is reported with the session it happened
    on and how many consecutive sessions it has now been carried."""
    report = PortfolioBacktestRunner().run(
        initial=OPENING,
        steps=(
            PortfolioBacktestStep(
                trade_date=DAY_ONE,
                bars=(bar("AAA.SZ", DAY_ONE, "10.00"), bar("BBB.SZ", DAY_ONE, "10.00")),
                orders=(buy("a1", "AAA.SZ", 1000), buy("b1", "BBB.SZ", 1000)),
                benchmark_close=Decimal("100"),
            ),
            PortfolioBacktestStep(
                trade_date=DAY_TWO,
                bars=(bar("AAA.SZ", DAY_TWO, "11.00"),),
                orders=(),
                benchmark_close=Decimal("100"),
            ),
            PortfolioBacktestStep(
                trade_date=date(2026, 7, 28),
                bars=(bar("AAA.SZ", date(2026, 7, 28), "11.00"),),
                orders=(),
                benchmark_close=Decimal("100"),
            ),
        ),
    )

    assert tuple(
        (carried.trade_date, carried.subject, carried.price, carried.sessions_carried)
        for carried in report.carried_marks
    ) == (
        ("2026-07-27", "BBB.SZ", Decimal("10.00"), 1),
        ("2026-07-28", "BBB.SZ", Decimal("10.00"), 2),
    )
    assert report.final_state.mark("BBB.SZ") == Decimal("10.00")


def test_an_order_whose_subject_has_no_bar_on_this_session_is_refused_at_construction() -> None:
    """The old model's `validate_subject` said the step's one order and one bar had to agree.
    Its replacement says the same thing for a book: you cannot trade a name you have no price
    for, and the check is now against a set rather than against a single field."""
    with pytest.raises(ValidationError, match="no bar on this session"):
        PortfolioBacktestStep(
            trade_date=DAY_ONE,
            bars=(bar("AAA.SZ", DAY_ONE, "10.00"),),
            orders=(buy("b1", "BBB.SZ", 100),),
            benchmark_close=Decimal("100"),
        )


def test_a_bar_from_another_session_cannot_enter_this_step() -> None:
    """One step is one session, so the step owns the date and every bar must be on it. The old
    shape could not state this: the date lived on the single bar, so K bars for one day were K
    independently dated facts and nothing held them together."""
    with pytest.raises(ValidationError, match="bars must all fall on the step's trade date"):
        PortfolioBacktestStep(
            trade_date=DAY_ONE,
            bars=(bar("AAA.SZ", DAY_ONE, "10.00"), bar("BBB.SZ", DAY_TWO, "10.00")),
            orders=(),
            benchmark_close=Decimal("100"),
        )


def test_two_bars_for_one_subject_on_one_session_are_refused() -> None:
    """Two prices for one name on one day is not a book, and the last one silently winning is
    the kind of quiet resolution `PortfolioState` already refuses for its own marks."""
    with pytest.raises(ValidationError, match="bars must have unique subjects"):
        PortfolioBacktestStep(
            trade_date=DAY_ONE,
            bars=(bar("AAA.SZ", DAY_ONE, "10.00"), bar("AAA.SZ", DAY_ONE, "11.00")),
            orders=(),
            benchmark_close=Decimal("100"),
        )


def test_two_orders_sharing_an_order_id_on_one_session_are_refused() -> None:
    """`SQLitePortfolioLedger` keys on `order_id` and compares payloads by bytes, so a step
    carrying the id twice would append the first and raise a conflict on the second -- half a
    session written, from an input a validator can see is wrong."""
    with pytest.raises(ValidationError, match="orders must have unique order IDs"):
        PortfolioBacktestStep(
            trade_date=DAY_ONE,
            bars=(bar("AAA.SZ", DAY_ONE, "10.00"), bar("BBB.SZ", DAY_ONE, "10.00")),
            orders=(buy("a1", "AAA.SZ", 100), buy("a1", "BBB.SZ", 100)),
            benchmark_close=Decimal("100"),
        )


def test_sessions_must_arrive_in_strictly_ascending_order() -> None:
    """A run refuses the whole series up front rather than letting the simulator reject each
    backwards order one at a time with `market date precedes portfolio state` -- which is what
    happened before, and which left a report whose curve went backwards in time while every
    transition in it read `rejected`."""
    step = PortfolioBacktestStep(
        trade_date=DAY_TWO,
        bars=(bar("AAA.SZ", DAY_TWO, "10.00"),),
        orders=(),
        benchmark_close=Decimal("100"),
    )
    earlier = PortfolioBacktestStep(
        trade_date=DAY_ONE,
        bars=(bar("AAA.SZ", DAY_ONE, "10.00"),),
        orders=(),
        benchmark_close=Decimal("100"),
    )

    with pytest.raises(ValueError, match="strictly ascending"):
        PortfolioBacktestRunner().run(initial=OPENING, steps=(step, earlier))


def test_the_first_session_cannot_precede_the_state_it_starts_from() -> None:
    with pytest.raises(ValueError, match="precedes the initial portfolio state"):
        PortfolioBacktestRunner().run(
            initial=PortfolioState(as_of=date(2026, 8, 1), cash=Decimal("100000.00")),
            steps=(
                PortfolioBacktestStep(
                    trade_date=DAY_ONE,
                    bars=(bar("AAA.SZ", DAY_ONE, "10.00"),),
                    orders=(),
                    benchmark_close=Decimal("100"),
                ),
            ),
        )
