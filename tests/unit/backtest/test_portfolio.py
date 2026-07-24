from datetime import date
from decimal import Decimal

from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.portfolio import (
    PortfolioLimits,
    PortfolioOrder,
    PortfolioSimulator,
    PortfolioState,
)


def bar(*, trade_date: date, close: str = "10.00") -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        subject="000001.SZ",
        trade_date=trade_date,
        board="main",
        previous_close=Decimal("10.00"),
        open=price,
        high=price,
        low=price,
        close=price,
        suspended=False,
        is_st=False,
    )


def state(*, cash: str = "20000.00") -> PortfolioState:
    return PortfolioState(
        as_of=date(2026, 7, 23),
        cash=Decimal(cash),
    )


def test_portfolio_buy_updates_cash_lots_marks_and_equity() -> None:
    simulator = PortfolioSimulator()
    transition = simulator.execute_order(
        state=state(),
        order=PortfolioOrder(
            order_id="order-buy-1",
            subject="000001.SZ",
            side="buy",
            quantity=100,
        ),
        market=bar(trade_date=date(2026, 7, 24)),
    )

    assert transition.status == "filled"
    assert transition.after.cash == Decimal("18994.99")
    assert transition.after.position("000001.SZ").quantity == 100
    assert transition.after.position("000001.SZ").cost_basis == Decimal("1005.01")
    assert transition.after.mark("000001.SZ") == Decimal("10.00")
    assert transition.after.equity == Decimal("19994.99")
    assert transition.after.fees_paid == Decimal("5.01")


def test_portfolio_enforces_t_plus_one_and_realizes_fifo_profit() -> None:
    simulator = PortfolioSimulator()
    bought = simulator.execute_order(
        state=state(),
        order=PortfolioOrder(
            order_id="order-buy-1",
            subject="000001.SZ",
            side="buy",
            quantity=100,
        ),
        market=bar(trade_date=date(2026, 7, 24)),
    ).after
    same_day = simulator.execute_order(
        state=bought,
        order=PortfolioOrder(
            order_id="order-sell-early",
            subject="000001.SZ",
            side="sell",
            quantity=100,
        ),
        market=bar(trade_date=date(2026, 7, 24), close="11.00"),
    )
    sold = simulator.execute_order(
        state=bought,
        order=PortfolioOrder(
            order_id="order-sell-1",
            subject="000001.SZ",
            side="sell",
            quantity=100,
        ),
        market=bar(trade_date=date(2026, 7, 25), close="11.00"),
    )

    assert same_day.status == "rejected"
    assert same_day.reason == "insufficient T+1 available quantity"
    assert same_day.after == bought
    assert sold.status == "filled"
    assert sold.after.position("000001.SZ").quantity == 0
    assert sold.after.cash == Decimal("20089.43")
    assert sold.after.realized_pnl == Decimal("89.43")
    assert sold.after.fees_paid == Decimal("10.57")


def test_portfolio_rejects_insufficient_cash_and_hard_exposure_limit() -> None:
    simulator = PortfolioSimulator(
        limits=PortfolioLimits(
            max_position_weight=Decimal("0.25"),
            max_total_exposure=Decimal("0.80"),
        )
    )
    too_large = simulator.execute_order(
        state=state(cash="10000.00"),
        order=PortfolioOrder(
            order_id="order-too-large",
            subject="000001.SZ",
            side="buy",
            quantity=300,
        ),
        market=bar(trade_date=date(2026, 7, 24)),
    )
    no_cash = simulator.execute_order(
        state=state(cash="500.00"),
        order=PortfolioOrder(
            order_id="order-no-cash",
            subject="000001.SZ",
            side="buy",
            quantity=100,
        ),
        market=bar(trade_date=date(2026, 7, 24)),
    )

    assert too_large.status == "rejected"
    assert too_large.reason == "maximum position weight exceeded"
    assert too_large.after == too_large.before
    assert no_cash.status == "rejected"
    assert no_cash.reason == "insufficient cash"
    assert no_cash.after == no_cash.before
