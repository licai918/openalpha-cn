"""The paper step builder: planned orders and one observed session into a paper-book step
(`V2-P7-017`).

Every fixture is hand-written with invented prices on the Shanghai and Shenzhen main boards, and
every number asserted is worked out in the comment beside it. The default `CostSchedule` applies
(commission 0.03% with a 5.00 floor, transfer fee 0.001%, sell stamp duty 0.05%, each rounded
half-up to the fen); `PortfolioLimits` are opened to 1 / 1 / 0, the paper book's setting, so the
only constraint a buy meets besides the market is cash. Sessions: S0 2021-03-01 is the signal
session (ordinal 100) and the opening state; S1, S2, S3 are the three sessions after it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    MarketBar,
    at_the_open,
    security_board,
)
from openalpha_cn.backtest.multi_day import PortfolioBacktestStep
from openalpha_cn.backtest.paper import PaperPortfolio
from openalpha_cn.backtest.paper_step import (
    NO_BAR_REASON,
    AttemptedOrder,
    PaperStepPlan,
    attempt_id,
    build_paper_step,
    remark_at_close,
)
from openalpha_cn.backtest.portfolio import (
    PortfolioLimits,
    PortfolioOrder,
    PortfolioState,
    PortfolioTransition,
)
from openalpha_cn.domain.portfolio import PortfolioPosition, PositionLot, PositionMark
from openalpha_cn.domain.survivor_lifecycle import advance_orders
from openalpha_cn.domain.survivor_orders import TradeOrder
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger

D = Decimal
S0, S1, S2, S3 = date(2021, 3, 1), date(2021, 3, 2), date(2021, 3, 3), date(2021, 3, 4)
BOUGHT_BEFORE = date(2021, 2, 25)
FULL = PortfolioLimits(max_position_weight=D(1), max_total_exposure=D(1), min_cash_weight=D(0))
BENCHMARK = D("6000.00")


def bar(
    code: str,
    day: date,
    prev: str,
    o: str,
    h: str,
    lo: str,
    c: str,
    up: str,
    down: str,
    *,
    suspended: bool = False,
) -> MarketBar:
    """A main-board bar with the exchange's published band, as `_PanelDays.quote` builds one."""
    return MarketBar(
        subject=code,
        trade_date=day,
        board=security_board(code),
        previous_close=D(prev),
        open=D(o),
        high=D(h),
        low=D(lo),
        close=D(c),
        suspended=suspended,
        is_st=False,
        up_limit=D(up),
        down_limit=D(down),
    )


def bars(*items: MarketBar) -> dict[str, MarketBar]:
    return {item.subject: item for item in items}


def sell(code: str, quantity: int, *, signal: int = 100) -> TradeOrder:
    return TradeOrder(
        signal_session=signal,
        execute_session=signal + 1,
        side="sell",
        ts_code=code,
        quantity=quantity,
        reference_price=D("10.00"),
        reason="EXIT_SURVIVOR_LOST",
    )


def buy(code: str, quantity: int, *, signal: int = 100) -> TradeOrder:
    return TradeOrder(
        signal_session=signal,
        execute_session=signal + 1,
        expires_session=signal + 3,
        side="buy",
        ts_code=code,
        quantity=quantity,
        reference_price=D("10.00"),
        reason="ENTRY_REPLACEMENT",
    )


def book(cash: str, *held: tuple[str, int, str]) -> PortfolioState:
    """The opening state at S0: `held` is `(code, shares, mark)`, each bought before S0."""
    return PortfolioState(
        as_of=S0,
        cash=D(cash),
        positions=tuple(
            PortfolioPosition(
                subject=code,
                lots=(
                    PositionLot(
                        open_date=BOUGHT_BEFORE,
                        quantity=shares,
                        cost_basis=D(mark) * shares,
                    ),
                ),
            )
            for code, shares, mark in held
        ),
        marks=tuple(PositionMark(subject=code, price=D(mark)) for code, _, mark in held),
    )


def paper(path: Path) -> PaperPortfolio:
    return PaperPortfolio(ledger=SQLitePortfolioLedger(path), limits=FULL)


def build(
    session: date,
    orders: tuple[TradeOrder, ...],
    quotes: dict[str, MarketBar],
    ratio: dict[str, Decimal] | None = None,
) -> PaperStepPlan:
    return build_paper_step(
        session=session,
        orders=orders,
        bars=quotes,
        adjustment_ratio=ratio or {},
        benchmark_close=BENCHMARK,
    )


def marks(state: PortfolioState) -> dict[str, Decimal]:
    return {mark.subject: mark.price for mark in state.marks}


def filled(plan: PaperStepPlan, transitions: tuple[PortfolioTransition, ...]) -> set[str]:
    """The planned ids of this plan's attempts that filled, looked up rather than parsed."""
    planned = {item.attempt_id: item.order.order_id for item in plan.attempts}
    return {planned[t.order.order_id] for t in transitions if t.status == "filled"}


# ---- the fixture's bars ------------------------------------------------------------------------

# 600010.SH opens locked at its limit-down price (9.00) and trades off it later.
LOCKED_DOWN_S1 = bar("600010.SH", S1, "10.00", "9.00", "9.50", "9.00", "9.40", "11.00", "9.00")
# 600030.SH opens at 10.10 and closes at 10.50: an order at the open fills at 10.10.
OPEN_S1 = bar("600030.SH", S1, "10.00", "10.10", "10.60", "10.05", "10.50", "11.00", "9.00")
# 600040.SH opens locked at its limit-up price (22.00) and trades off it later (low 21.20).
LOCKED_UP_S1 = bar("600040.SH", S1, "20.00", "22.00", "22.00", "21.20", "21.50", "22.00", "18.00")
# 000060.SZ has a row, but the halt corpus says it could not be traded at the open.
HALTED_WITH_A_ROW_S1 = bar(
    "000060.SZ", S1, "10.00", "10.00", "10.00", "10.00", "10.00", "11.00", "9.00", suspended=True
)
# 600020.SH goes ex on S1: its published previous close is the ex-reference 10.00 against a
# pre-event close of 11.50, and the adjustment factor rises by 1.15 (11.50 / 10.00).
EX_DATE_S1 = bar("600020.SH", S1, "10.00", "10.00", "10.20", "9.90", "10.00", "11.00", "9.00")


def test_an_attempt_id_is_the_planned_id_at_the_session() -> None:
    order = sell("600010.SH", 1000)

    assert attempt_id(order, S1) == f"{order.order_id}@2021-03-02"
    # 28 characters of `tor_` address, `@`, ten of date: 39, inside `PortfolioOrder`'s 128.
    assert len(attempt_id(order, S1)) == 39
    assert attempt_id(order, S2) != attempt_id(order, S1)


def test_an_order_is_judged_and_filled_at_the_open_print(tmp_path: Path) -> None:
    """The ordered name's bar is collapsed to its open; a name only held keeps its whole bar."""
    held = bar("600020.SH", S1, "11.50", "11.50", "11.70", "11.40", "11.60", "12.65", "10.35")
    plan = build(S1, (buy("600030.SH", 1000),), bars(OPEN_S1, held))
    step = plan.step.prices()

    assert (step["600030.SH"].open, step["600030.SH"].close) == (D("10.10"), D("10.10"))
    assert step["600020.SH"] == held
    result = paper(tmp_path / "paper.sqlite3").advance(
        state=book("20000.00", ("600020.SH", 1000, "11.50")), session=plan.step, observed_on=S1
    )
    (fill,) = result.transitions
    assert fill.status == "filled"
    assert fill.execution is not None
    assert fill.execution.filled_price == D("10.10")  # the open, not the 10.50 close
    # notional 1,000 x 10.10 = 10,100.00; commission max(3.03, 5.00) = 5.00; transfer 0.101 -> 0.10
    assert fill.execution.total_cost == D("5.10")
    assert result.after.cash == D("9894.90")  # 20,000.00 - 10,105.10
    # marks: 600020 at its close 11.60, the name bought today at the open it was bought at.
    assert marks(result.after) == {"600020.SH": D("11.60"), "600030.SH": D("10.10")}
    assert result.equity == D("31594.90")  # 9,894.90 + 11,600.00 + 10,100.00


def test_limit_up_limit_down_and_a_halt_with_a_row_are_submitted_and_refused_by_the_ledger(
    tmp_path: Path,
) -> None:
    """T5 section 4: the paper book refuses these whole and records the refusal.

    The builder does not pre-judge them: each is `submitted`, and the ledger holds a rejected
    transition under its attempt id. Judged on the whole bar instead of the open print, the
    execution policy would have filled the limit-up buy at its 21.50 close -- the defect the
    open collapse exists to prevent.
    """
    orders = (sell("600010.SH", 1000), buy("600040.SH", 500), buy("000060.SZ", 1000))
    plan = build(S1, orders, bars(LOCKED_DOWN_S1, LOCKED_UP_S1, HALTED_WITH_A_ROW_S1))
    ledger = SQLitePortfolioLedger(tmp_path / "paper.sqlite3")
    opening = book("50000.00", ("600010.SH", 1000, "10.00"))

    result = PaperPortfolio(ledger=ledger, limits=FULL).advance(
        state=opening, session=plan.step, observed_on=S1
    )

    assert [(item.outcome, item.reason) for item in plan.attempts] == [("submitted", None)] * 3
    assert [(t.order.order_id, t.status, t.reason) for t in ledger.list()] == [
        (attempt_id(orders[0], S1), "rejected", "sell cannot fill on a one-price limit-down bar"),
        (attempt_id(orders[1], S1), "rejected", "buy cannot fill on a one-price limit-up bar"),
        (attempt_id(orders[2], S1), "rejected", "security is suspended"),
    ]
    assert result.after.cash == D("50000.00")
    whole_bar = AShareExecutionPolicy().execute(
        ExecutionRequest(side="buy", quantity=500), LOCKED_UP_S1
    )
    assert (whole_bar.status, whole_bar.filled_price) == ("filled", D("21.50"))


def test_an_order_with_no_bar_is_not_attempted_and_neither_the_step_nor_the_ledger_sees_it(
    tmp_path: Path,
) -> None:
    """A whole-day halt serves no row; an order for it would fail the step's construction.

    It is taken out and recorded as not attempted, with the P6 book's reason. The held name with
    no bar keeps its mark, and the runner names the carry.
    """
    orders = (buy("600050.SH", 1000), sell("600010.SH", 1000), buy("600030.SH", 1000))
    plan = build(S1, orders, bars(OPEN_S1))
    ledger = SQLitePortfolioLedger(tmp_path / "paper.sqlite3")

    assert [(item.order, item.outcome, item.reason) for item in plan.attempts] == [
        (orders[1], "not_attempted", NO_BAR_REASON),
        (orders[0], "not_attempted", NO_BAR_REASON),
        (orders[2], "submitted", None),
    ]
    assert NO_BAR_REASON == "no bar on the execution session"
    assert [order.subject for order in plan.step.orders] == ["600030.SH"]
    result = PaperPortfolio(ledger=ledger, limits=FULL).advance(
        state=book("20000.00", ("600010.SH", 1000, "10.00")), session=plan.step, observed_on=S1
    )
    assert [t.order.order_id for t in ledger.list()] == [attempt_id(orders[2], S1)]
    assert ledger.get(attempt_id(orders[0], S1)) is None
    assert [(m.subject, m.price, m.sessions_carried) for m in result.carried_marks] == [
        ("600010.SH", D("10.00"), 1)
    ]


def test_a_carried_order_is_attempted_under_a_new_id_so_the_ledger_raises_no_conflict(
    tmp_path: Path,
) -> None:
    """T5 prototype 3(c): the ledger records rejections, so a retry under the same id conflicts."""
    order = sell("600010.SH", 1000)
    ledger = SQLitePortfolioLedger(tmp_path / "paper.sqlite3")
    book_ = PaperPortfolio(ledger=ledger, limits=FULL)
    first = book_.advance(
        state=book("1000.00", ("600010.SH", 1000, "10.00")),
        session=build(S1, (order,), bars(LOCKED_DOWN_S1)).step,
        observed_on=S1,
    )
    assert first.transitions[0].status == "rejected"
    s2 = bars(bar("600010.SH", S2, "9.40", "9.50", "9.80", "9.30", "9.60", "10.34", "8.46"))

    # Control: the second attempt sent under the first attempt's id is refused by the ledger.
    reused = PortfolioBacktestStep(
        trade_date=S2,
        bars=(at_the_open(s2["600010.SH"]),),
        orders=(
            PortfolioOrder(
                order_id=attempt_id(order, S1), subject="600010.SH", side="sell", quantity=1000
            ),
        ),
        benchmark_close=BENCHMARK,
    )
    with pytest.raises(ValueError, match="portfolio order_id conflicts"):
        book_.advance(state=first.after, session=reused, observed_on=S2)

    second = book_.advance(state=first.after, session=build(S2, (order,), s2).step, observed_on=S2)
    assert second.transitions[0].status == "filled"
    assert [t.order.order_id for t in ledger.list()] == [
        f"{order.order_id}@2021-03-02",
        f"{order.order_id}@2021-03-03",
    ]
    # notional 9,500.00; commission 5.00, transfer 0.095 -> 0.10, stamp 4.75: 9.85
    assert second.after.cash == D("10490.15")  # 1,000.00 + 9,500.00 - 9.85


def test_sells_go_before_buys_so_a_sale_funds_a_buy_on_the_same_session(tmp_path: Path) -> None:
    """`advance_orders` lists live buys before live sells; the builder puts the sells first."""
    holding_bar = bar("600010.SH", S1, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00")
    as_advance_orders_lists_them = (buy("600030.SH", 1000), sell("600010.SH", 1000))
    plan = build(S1, as_advance_orders_lists_them, bars(holding_bar, OPEN_S1))
    opening = book("1000.00", ("600010.SH", 1000, "10.00"))

    assert [order.side for order in plan.step.orders] == ["sell", "buy"]
    result = paper(tmp_path / "paper.sqlite3").advance(
        state=opening, session=plan.step, observed_on=S1
    )
    # sell: 10,000.00 less 5.00 + 0.10 + 5.00 -> cash 10,989.90; buy: 10,100.00 + 5.10 = 10,105.10
    assert [t.status for t in result.transitions] == ["filled", "filled"]
    assert result.after.cash == D("884.80")  # 10,989.90 - 10,105.10

    # Control: the same orders buy-first -- 10,105.10 against 1,000.00 of cash.
    buy_first = PortfolioBacktestStep(
        trade_date=S1,
        bars=plan.step.bars,
        orders=tuple(reversed(plan.step.orders)),
        benchmark_close=BENCHMARK,
    )
    control = paper(tmp_path / "control.sqlite3").advance(
        state=opening, session=buy_first, observed_on=S1
    )
    assert [(t.order.side, t.status, t.reason) for t in control.transitions] == [
        ("buy", "rejected", "insufficient cash"),
        ("sell", "filled", None),
    ]


def test_a_carried_buy_goes_before_a_new_buy_because_the_trade_list_reserved_its_cash(
    tmp_path: Path,
) -> None:
    """`build_trade_list` sized today's buys after reserving cash for the buys still live.

    So the older buy is executed first whichever order the caller hands them in; when the cash
    covers one, it is the carried one that fills.
    """
    carried = buy("600040.SH", 1000, signal=99)
    new = buy("600030.SH", 1000, signal=100)
    carried_bar = bar("600040.SH", S1, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00")
    plan = build(S1, (new, carried), bars(OPEN_S1, carried_bar))

    assert [item.order for item in plan.attempts] == [carried, new]
    result = paper(tmp_path / "paper.sqlite3").advance(
        state=book("10200.00"), session=plan.step, observed_on=S1
    )
    # carried: 10,000.00 + 5.00 + 0.10 = 10,005.10 -> 194.90 left; new: 10,105.10 does not fit.
    assert [(t.order.subject, t.status, t.reason) for t in result.transitions] == [
        ("600040.SH", "filled", None),
        ("600030.SH", "rejected", "insufficient cash"),
    ]
    assert result.after.cash == D("194.90")


def test_an_adjustment_event_is_scaled_away_and_leaves_no_false_drawdown(tmp_path: Path) -> None:
    """A 1.15 event on a held name (`adj_t / adj_entry = 1.15`) is not a loss.

    The holding is 1,000 shares in the ledger's units, marked 11.50 on S0. On the ex-date the real
    close is 10.00. Scaled by 1.15 the whole bar is back in the ledger's units -- every price,
    published band included -- and the mark is 11.50: no change. Unscaled, the book marks 10.00
    and reports a fall of 1,500.00, i.e. 1 - 1 / 1.15 = 13.04% of the position, on a session on
    which no holder lost anything.
    """
    opening = book("1000.00", ("600020.SH", 1000, "11.50"))
    scaled = build(S1, (), bars(EX_DATE_S1), {"600020.SH": D("1.15")})
    unscaled = build(S1, (), bars(EX_DATE_S1))
    book_ = paper(tmp_path / "paper.sqlite3")

    (step_bar,) = scaled.step.bars
    assert (
        step_bar.previous_close,
        step_bar.open,
        step_bar.high,
        step_bar.low,
        step_bar.close,
        step_bar.up_limit,
        step_bar.down_limit,
    ) == (D("11.50"), D("11.50"), D("11.73"), D("11.385"), D("11.50"), D("12.65"), D("10.35"))
    assert opening.equity == D("12500.00")  # 1,000.00 + 1,000 x 11.50
    assert book_.advance(state=opening, session=scaled.step, observed_on=S1).equity == D("12500.00")
    assert book_.advance(state=opening, session=unscaled.step, observed_on=S1).equity == D(
        "11000.00"
    )  # 1,000.00 + 1,000 x 10.00: the false fall


def test_a_holding_sold_on_its_ex_date_raises_what_the_real_shares_would_have(
    tmp_path: Path,
) -> None:
    """The ledger sells 1,000 shares at the scaled open 11.50; the account sells 1,150 at 10.00.

    Both are a notional of 11,500.00, so the cash is the same; what differs is only the units the
    ledger records the trade in (disclosed in the module docstring).
    """
    order = sell("600020.SH", 1000)
    plan = build(S1, (order,), bars(EX_DATE_S1), {"600020.SH": D("1.15")})

    result = paper(tmp_path / "paper.sqlite3").advance(
        state=book("1000.00", ("600020.SH", 1000, "11.50")), session=plan.step, observed_on=S1
    )
    (fill,) = result.transitions
    assert fill.execution is not None
    assert fill.execution.filled_price == D("11.50")
    assert fill.execution.notional == 1150 * D("10.00")
    # commission max(3.45, 5.00) = 5.00; transfer 0.115 -> 0.12; stamp 5.75: 10.87
    assert fill.execution.total_cost == D("10.87")
    assert result.after.cash == D("12489.13")  # 1,000.00 + 11,500.00 - 10.87


def test_remark_at_close_values_what_traded_today_at_its_close(tmp_path: Path) -> None:
    """The runner marks an ordered name at its collapsed bar, i.e. the open; the day ends at the
    close. Re-marked: the name bought at 10.10 closes 10.50, the name whose sale was refused at
    9.00 closes 9.40, and the ex-date name stays 10.00 x 1.15 = 11.50. Nothing else moves."""
    ratio = {"600020.SH": D("1.15")}
    quotes = bars(LOCKED_DOWN_S1, EX_DATE_S1, OPEN_S1)
    orders = (sell("600010.SH", 1000), buy("600030.SH", 1000))
    opening = book("20000.00", ("600010.SH", 1000, "10.00"), ("600020.SH", 1000, "11.50"))
    result = paper(tmp_path / "paper.sqlite3").advance(
        state=opening, session=build(S1, orders, quotes, ratio).step, observed_on=S1
    )
    assert marks(result.after) == {
        "600010.SH": D("9.00"),
        "600020.SH": D("11.50"),
        "600030.SH": D("10.10"),
    }
    assert result.equity == D("40494.90")  # 9,894.90 + 9,000 + 11,500 + 10,100

    closed = remark_at_close(result.after, bars=quotes, adjustment_ratio=ratio)

    assert marks(closed) == {
        "600010.SH": D("9.40"),
        "600020.SH": D("11.50"),
        "600030.SH": D("10.50"),
    }
    assert closed.equity == D("41294.90")  # 9,894.90 + 9,400 + 11,500 + 10,500
    unchanged = ("as_of", "cash", "positions", "realized_pnl", "fees_paid")
    assert closed.model_dump(include=set(unchanged)) == result.after.model_dump(
        include=set(unchanged)
    )
    halted = remark_at_close(result.after, bars=bars(OPEN_S1), adjustment_ratio=ratio)
    assert marks(halted)["600010.SH"] == D("9.00")  # no bar: the mark it had


def test_three_sessions_through_the_paper_book_with_the_order_lifecycle(tmp_path: Path) -> None:
    """The step builder, `PaperPortfolio` and `advance_orders` over three sessions (T5 section 5).

    S0: cash 30,000.00; 600010.SH 1,000 marked 10.00; 600020.SH 1,000 marked 11.50 (51,500.00).
    Planned on S0: sell 600010.SH; buy 600030.SH 1,000, 600040.SH 500, 600050.SH 1,000 (buys
    expire at ordinal 103).
    """
    ledger = SQLitePortfolioLedger(tmp_path / "paper.sqlite3")
    book_ = PaperPortfolio(ledger=ledger, limits=FULL)
    planned = (
        sell("600010.SH", 1000),
        buy("600030.SH", 1000),
        buy("600040.SH", 500),
        buy("600050.SH", 1000),
    )
    state = book("30000.00", ("600010.SH", 1000, "10.00"), ("600020.SH", 1000, "11.50"))
    assert state.equity == D("51500.00")

    # ---- S1: 600010 opens limit-down, 600030 fills at its open, 600040 opens limit-up, 600050
    # is halted all day (no row). Cash 30,000.00 - 10,105.10 = 19,894.90. As returned: 600010 at
    # its 9.00 open, 600030 at its 10.10 open -> 19,894.90 + 9,000 + 11,500 + 10,100 = 50,494.90;
    # at the close 9.40 and 10.50 -> 51,294.90.
    s1 = bars(
        LOCKED_DOWN_S1,
        bar("600020.SH", S1, "11.50", "11.50", "11.60", "11.40", "11.50", "12.65", "10.35"),
        OPEN_S1,
        LOCKED_UP_S1,
    )
    plan = build(S1, planned, s1)
    result = book_.advance(state=state, session=plan.step, observed_on=S1)
    assert [(i.order.ts_code, i.outcome) for i in plan.attempts] == [
        ("600010.SH", "submitted"),
        ("600030.SH", "submitted"),
        ("600040.SH", "submitted"),
        ("600050.SH", "not_attempted"),
    ]
    assert [t.status for t in result.transitions] == ["rejected", "filled", "rejected"]
    assert result.equity == D("50494.90")
    state = remark_at_close(result.after, bars=s1, adjustment_ratio={})
    assert (state.cash, state.equity) == (D("19894.90"), D("51294.90"))
    settled = advance_orders(
        planned,
        session=101,
        filled_order_ids=filled(plan, result.transitions),
        survivors={"600030.SH", "600040.SH", "600050.SH"},
    )
    assert [o.ts_code for o in settled.filled] == ["600030.SH"]
    # `advance_orders` lists buys before sells; the builder will put the sell first again.
    assert [(o.side, o.ts_code) for o in settled.live] == [
        ("buy", "600040.SH"),
        ("buy", "600050.SH"),
        ("sell", "600010.SH"),
    ]

    # ---- S2: 600020 goes ex (ratio 1.15); 600010 sells at its 9.50 open (notional 9,500.00,
    # fees 5.00 + 0.10 + 4.75 = 9.85, cash 19,894.90 + 9,490.15 = 29,385.05); 600040 is
    # limit-up all day; 600050 has a row but is halted. Marks: 600020 10.10 x 1.15 = 11.615,
    # 600030 10.60 -> 29,385.05 + 11,615.00 + 10,600.00 = 51,600.05. Unscaled, 600020 would
    # mark 10.10 and the book would show 50,085.05.
    ratio = {"600020.SH": D("1.15")}
    halted_with_a_row = bar(
        "600050.SH",
        S2,
        "10.00",
        "10.00",
        "10.00",
        "10.00",
        "10.00",
        "11.00",
        "9.00",
        suspended=True,
    )
    s2 = bars(
        bar("600010.SH", S2, "9.40", "9.50", "9.80", "9.30", "9.60", "10.34", "8.46"),
        bar("600020.SH", S2, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00"),
        bar("600030.SH", S2, "10.50", "10.40", "10.70", "10.30", "10.60", "11.55", "9.45"),
        bar("600040.SH", S2, "21.50", "23.65", "23.65", "23.65", "23.65", "23.65", "19.35"),
        halted_with_a_row,
    )
    plan = build(S2, settled.live, s2, ratio)
    result = book_.advance(state=state, session=plan.step, observed_on=S2)
    assert [(t.order.subject, t.status, t.reason) for t in result.transitions] == [
        ("600010.SH", "filled", None),
        ("600040.SH", "rejected", "buy cannot fill on a one-price limit-up bar"),
        ("600050.SH", "rejected", "security is suspended"),
    ]
    state = remark_at_close(result.after, bars=s2, adjustment_ratio=ratio)
    assert (state.cash, state.equity) == (D("29385.05"), D("51600.05"))
    settled = advance_orders(
        settled.live,
        session=102,
        filled_order_ids=filled(plan, result.transitions),
        survivors={"600040.SH", "600050.SH"},
    )
    assert [o.ts_code for o in settled.live] == ["600040.SH", "600050.SH"]

    # ---- S3: 600040 limit-up again; 600050 fills at its 10.05 open (10,050.00 + 5.10, cash
    # 19,329.95). As returned: 600020 10.20 x 1.15 = 11.73, 600030 10.70, 600050 10.05 ->
    # 51,809.95; 600050 closes 10.20 -> 51,959.95.
    s3 = bars(
        bar("600020.SH", S3, "10.10", "10.10", "10.30", "10.00", "10.20", "11.11", "9.09"),
        bar("600030.SH", S3, "10.60", "10.60", "10.80", "10.50", "10.70", "11.66", "9.54"),
        bar("600040.SH", S3, "23.65", "26.02", "26.02", "26.02", "26.02", "26.02", "21.29"),
        bar("600050.SH", S3, "10.00", "10.05", "10.30", "10.00", "10.20", "11.00", "9.00"),
    )
    plan = build(S3, settled.live, s3, ratio)
    result = book_.advance(state=state, session=plan.step, observed_on=S3)
    assert result.equity == D("51809.95")
    state = remark_at_close(result.after, bars=s3, adjustment_ratio=ratio)
    assert (state.cash, state.equity) == (D("19329.95"), D("51959.95"))
    settled = advance_orders(
        settled.live,
        session=103,
        filled_order_ids=filled(plan, result.transitions),
        survivors={"600040.SH"},
    )
    assert ([o.ts_code for o in settled.filled], [o.ts_code for o in settled.expired]) == (
        ["600050.SH"],
        ["600040.SH"],
    )

    # Eight rows: 3 + 3 + 2. 600040's one planned order was attempted under three ids.
    rows = ledger.list()
    assert len(rows) == 8
    assert [t.order.order_id for t in rows if t.order.subject == "600040.SH"] == [
        attempt_id(planned[2], day) for day in (S1, S2, S3)
    ]


def test_the_same_inputs_build_the_same_plan() -> None:
    orders = (buy("600050.SH", 1000), sell("600010.SH", 1000), buy("600030.SH", 1000))
    quotes = bars(LOCKED_DOWN_S1, EX_DATE_S1, OPEN_S1)
    reordered = dict(reversed(list(quotes.items())))
    ratio = {"600020.SH": D("1.15")}

    first = build(S1, orders, quotes, ratio)

    assert build(S1, orders, quotes, ratio) == first
    assert build(S1, orders, reordered, ratio) == first
    assert [item.subject for item in first.step.bars] == ["600010.SH", "600020.SH", "600030.SH"]


# ---- refusals ----------------------------------------------------------------------------------


def test_a_bar_dated_after_the_session_is_refused_rather_than_read() -> None:
    """Look-ahead guard: a later session's bar handed in under today's call is not used."""
    tomorrow = bar("600030.SH", S2, "10.50", "10.40", "10.70", "10.30", "10.60", "11.55", "9.45")
    with pytest.raises(ValueError, match=r"600030\.SH.*2021-03-03.*2021-03-02"):
        build(S1, (buy("600030.SH", 1000),), bars(tomorrow))
    state = book("1000.00", ("600030.SH", 1000, "10.50")).model_copy(update={"as_of": S1})
    with pytest.raises(ValueError, match=r"600030\.SH.*2021-03-03.*2021-03-02"):
        remark_at_close(state, bars=bars(tomorrow), adjustment_ratio={})


def test_a_bar_without_the_published_band_is_refused_by_name() -> None:
    """Scaling a *derived* band is not proportional, because the derivation rounds to the fen.

    Previous close 10.03, a one-price limit-up bar at 11.03 (= round(10.03 x 1.1)): refused. Scaled
    by 1.15 the low is 12.6845 while the derived limit is round(11.5345 x 1.1 = 12.68795) = 12.69,
    so the same bar would fill. The published band scales with the bar and the verdict holds.
    """
    derived = MarketBar(
        subject="600030.SH",
        trade_date=S1,
        board="main",
        previous_close=D("10.03"),
        open=D("11.03"),
        high=D("11.03"),
        low=D("11.03"),
        close=D("11.03"),
        suspended=False,
        is_st=False,
    )
    with pytest.raises(ValueError, match=r"published.*600030\.SH"):
        build(S1, (), bars(derived))


def test_a_bar_filed_under_another_code_is_refused() -> None:
    with pytest.raises(ValueError, match=r"600099\.SH.*600030\.SH"):
        build(S1, (), {"600099.SH": OPEN_S1})


def test_the_same_order_given_twice_is_refused() -> None:
    order = buy("600030.SH", 1000)
    with pytest.raises(ValueError, match=order.order_id):
        build(S1, (order, order), bars(OPEN_S1))


def test_a_session_with_no_bar_at_all_is_refused() -> None:
    with pytest.raises(ValueError, match="no bar at all"):
        build(S1, (buy("600030.SH", 1000),), {})


@pytest.mark.parametrize("value", ["0", "-1.15"])
def test_an_adjustment_ratio_that_is_not_positive_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match=r"600020\.SH"):
        build(S1, (), bars(EX_DATE_S1), {"600020.SH": D(value)})


def test_a_buy_into_a_name_whose_adjustment_ratio_is_not_one_is_refused() -> None:
    """One entry factor per holding: a buy is at ratio 1 on the session it fills."""
    with pytest.raises(ValueError, match=r"600020\.SH"):
        build(S1, (buy("600020.SH", 1000),), bars(EX_DATE_S1), {"600020.SH": D("1.15")})


def test_an_order_not_attempted_says_why_and_a_submitted_one_does_not() -> None:
    order = buy("600030.SH", 1000)
    with pytest.raises(ValueError):
        AttemptedOrder(
            order=order, attempt_id=attempt_id(order, S1), outcome="not_attempted", reason=None
        )
    with pytest.raises(ValueError):
        AttemptedOrder(
            order=order, attempt_id=attempt_id(order, S1), outcome="submitted", reason="why"
        )
