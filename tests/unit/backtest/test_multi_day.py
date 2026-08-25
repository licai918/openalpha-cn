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
    CarriedMark,
    EquityPoint,
    PortfolioBacktestReport,
    PortfolioBacktestRunner,
    PortfolioBacktestStep,
    SubjectAttribution,
)
from openalpha_cn.backtest.portfolio import (
    PortfolioLimits,
    PortfolioOrder,
    PortfolioPosition,
    PortfolioState,
    PositionLot,
    PositionMark,
)


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
                benchmark_close=Decimal("105"),
            ),
        ),
    )

    assert report.final_state.mark("BBB.SZ") == Decimal("20.00")
    assert report.final_state.market_value == Decimal("30000.00")
    assert report.final_state.as_of == DAY_TWO
    assert report.carried_marks == ()
    # Exact, not `> 0`. A `>` on an exposure is satisfied by a division replaced with a
    # multiplication (measured: that mutant survived a `> Decimal("0.80")` assertion), and it is
    # satisfied by an accumulator seeded at one instead of zero. Both are killed by the equality.
    assert tuple(
        (point.trade_date, point.equity, point.gross_exposure) for point in report.equity_curve
    ) == (
        ("2026-07-24", Decimal("99989.80"), Decimal("0.200020")),
        ("2026-07-27", Decimal("109989.80"), Decimal("0.272753")),
    )
    assert report.max_gross_exposure == Decimal("0.272753")
    assert report.max_order_notional == Decimal("10000.00")
    assert report.total_return == Decimal("0.099898")
    # The benchmark moves, and it has to. With a flat benchmark `total - benchmark` and
    # `total + benchmark` are the same number, so an `active_return` assertion on a flat
    # fixture cannot separate the two -- measured: that mutant survived exactly such an
    # assertion here before the benchmark was given somewhere to go.
    assert report.benchmark_return == Decimal("0.050000")
    assert report.active_return == Decimal("0.049898")


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


def test_two_sessions_dated_the_same_day_are_refused() -> None:
    """Ascending is not enough; it has to be strict.

    Two steps sharing a date is the single-subject step's defect arriving by the back door --
    two `EquityPoint`s on one day, the first of them mid-session -- and a `<=` that should have
    been a `<` is all it takes. Measured: with the comparison relaxed, this input produced a
    two-point curve both dated `2026-07-24` and every other test in this file stayed green.
    """
    same_day = PortfolioBacktestStep(
        trade_date=DAY_ONE,
        bars=(bar("AAA.SZ", DAY_ONE, "10.00"),),
        orders=(),
        benchmark_close=Decimal("100"),
    )

    with pytest.raises(ValueError, match="strictly ascending"):
        PortfolioBacktestRunner().run(initial=OPENING, steps=(same_day, same_day))


def test_the_first_session_may_land_on_the_opening_states_own_date() -> None:
    """A book funded on Monday may trade on Monday, and that is the boundary `backtest/paper.py`
    deliberately draws differently -- it requires *every* session to move the book forward,
    because a paper book re-advanced through a session it has lived would double every fill.
    Both sides of that difference need a test or the two modules can drift into agreeing."""
    report = PortfolioBacktestRunner().run(
        initial=PortfolioState(as_of=DAY_ONE, cash=Decimal("100000.00")),
        steps=(
            PortfolioBacktestStep(
                trade_date=DAY_ONE,
                bars=(bar("AAA.SZ", DAY_ONE, "10.00"),),
                orders=(buy("a1", "AAA.SZ", 1000),),
                benchmark_close=Decimal("100"),
            ),
        ),
    )

    assert report.transitions[0].status == "filled"
    assert report.final_state.equity == Decimal("99994.90")


def test_an_opening_book_with_no_equity_is_refused_by_name() -> None:
    """`PortfolioState.cash` is `ge=0`, so a book with no cash and no positions is constructible,
    and `total_return` divides by `initial.equity`. Until this row that input came back as
    `decimal.DivisionByZero` raised from inside a report builder, which tells a caller nothing
    about what they passed."""
    with pytest.raises(ValueError, match="opening book with equity"):
        PortfolioBacktestRunner().run(
            initial=PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("0")),
            steps=(
                PortfolioBacktestStep(
                    trade_date=DAY_ONE,
                    bars=(bar("AAA.SZ", DAY_ONE, "10.00"),),
                    orders=(),
                    benchmark_close=Decimal("100"),
                ),
            ),
        )


def test_a_name_the_execution_policy_refused_earns_no_line_in_the_attribution() -> None:
    """A rejection is not a trade, and the two are one `and` apart.

    A suspended bar comes back from `AShareExecutionPolicy` as a **rejected** `ExecutionResult`
    that is nevertheless *not* `None`, carrying `notional=0.00` -- so relaxing
    `execution is not None and status == "filled"` to an `or` leaves turnover and
    `max_order_notional` untouched (both add zero) and still opens a `pnl` entry for the refused
    name, which then appears in `attribution` with `0.00`. Measured: that mutant survived every
    other assertion in this file.
    """
    halted = bar("HALT.SZ", DAY_ONE, "10.00").model_copy(update={"suspended": True})
    report = PortfolioBacktestRunner().run(
        initial=OPENING,
        steps=(
            PortfolioBacktestStep(
                trade_date=DAY_ONE,
                bars=(bar("AAA.SZ", DAY_ONE, "10.00"), halted),
                orders=(buy("a1", "AAA.SZ", 1000), buy("h1", "HALT.SZ", 1000)),
                benchmark_close=Decimal("100"),
            ),
        ),
    )
    refusal = report.transitions[1]

    assert refusal.status == "rejected"
    assert refusal.reason == "security is suspended"
    assert refusal.execution is not None
    assert refusal.execution.notional == Decimal("0.00")
    assert tuple(item.subject for item in report.attribution) == ("AAA.SZ",)


def test_a_run_that_fills_nothing_reports_zeroes_rather_than_floors() -> None:
    """Every accumulator has to start at zero, and `max()` hides a seed that does not.

    Seeding `max_order_notional` or `max_gross_exposure` at one instead of zero is invisible to
    any book that trades -- the real figures are larger -- and shows up only on the run that
    fills nothing. Which is now a run this model can express at all.
    """
    report = PortfolioBacktestRunner().run(
        initial=OPENING,
        steps=(
            PortfolioBacktestStep(
                trade_date=DAY_ONE,
                bars=(bar("AAA.SZ", DAY_ONE, "10.00"),),
                orders=(),
                benchmark_close=Decimal("100"),
            ),
        ),
    )

    assert report.max_order_notional == Decimal("0.00")
    assert report.max_gross_exposure == Decimal("0.000000")
    assert report.turnover == Decimal("0.000000")
    assert report.active_return == Decimal("0.000000")
    assert report.attribution == ()
    assert report.transitions == ()


def test_every_contract_this_module_publishes_is_frozen_and_forbids_extra_keys() -> None:
    """The house rule, held as an equality rather than repeated five times by hand.

    Both halves earn their place. `frozen=True` is what makes a `PortfolioBacktestReport` safe to
    hand around after a run, and `extra="forbid"` is what makes an unfamiliar key in a REST body
    a `422` rather than a silently dropped field -- and a mutation sweep found **five** config
    flags on this module whose loss no test could see.
    """
    published = (
        PortfolioBacktestStep,
        EquityPoint,
        CarriedMark,
        SubjectAttribution,
        PortfolioBacktestReport,
    )

    assert {model.__name__: model.model_config.get("frozen") for model in published} == {
        model.__name__: True for model in published
    }
    assert {model.__name__: model.model_config.get("extra") for model in published} == {
        model.__name__: "forbid" for model in published
    }
    with pytest.raises(ValidationError):
        CarriedMark(
            trade_date="2026-07-24", subject="AAA.SZ", price=Decimal("10"), sessions_carried=0
        )
    # `benchmark_close` is bounded `gt=0` and not `gt=1`, which matters for the common case of a
    # benchmark normalised to start at 1.0 -- a bound of `gt=1` would refuse its first session.
    assert PortfolioBacktestStep(
        trade_date=DAY_ONE,
        bars=(bar("AAA.SZ", DAY_ONE, "10.00"),),
        orders=(),
        benchmark_close=Decimal("1"),
    ).benchmark_close == Decimal("1")


@pytest.mark.parametrize(
    ("quantity", "price", "equity"),
    [
        pytest.param(1000, "10.00", "10000.00", id="a-real-book"),
        pytest.param(1, "1.00", "1.00", id="a-book-worth-exactly-one"),
    ],
)
def test_a_fully_invested_book_reports_an_exposure_of_one(
    quantity: int, price: str, equity: str
) -> None:
    """The upper end of the gross-exposure scale, and the guard underneath it.

    `gross_exposure` is computed under a `state.equity == 0` guard that keeps a divide-by-zero
    out of a report builder. A book holding everything it has -- no cash, one position -- is
    where numerator and denominator are equal, and it has to come back as `1` rather than as the
    guard's fallback.

    The second parameter is a book worth exactly one yuan, and it is not decoration: it is the
    **only** input on which that guard's *condition* is observable. A mutation sweep flipped
    `state.equity == 0` to `== 1`, and every other fixture in this file left it alive -- a book
    worth 10,000 takes the same branch either way, and a book worth 1 with no position gives
    `0 / 1 == 0`, which is the fallback's value anyway. Equity of exactly 1 *with* a position is
    the one place the two answers differ.
    """
    report = PortfolioBacktestRunner(
        limits=PortfolioLimits(max_position_weight=Decimal("1"), max_total_exposure=Decimal("1"))
    ).run(
        initial=PortfolioState(
            as_of=date(2026, 7, 23),
            cash=Decimal("0.00"),
            positions=(
                PortfolioPosition(
                    subject="AAA.SZ",
                    lots=(
                        PositionLot(
                            open_date=date(2026, 7, 22),
                            quantity=quantity,
                            cost_basis=Decimal(equity),
                        ),
                    ),
                ),
            ),
            marks=(PositionMark(subject="AAA.SZ", price=Decimal(price)),),
        ),
        steps=(
            PortfolioBacktestStep(
                trade_date=DAY_ONE,
                bars=(bar("AAA.SZ", DAY_ONE, price),),
                orders=(),
                benchmark_close=Decimal("100"),
            ),
        ),
    )

    assert report.final_state.equity == Decimal(equity)
    assert report.equity_curve[0].gross_exposure == Decimal("1.000000")
    assert report.max_gross_exposure == Decimal("1.000000")
