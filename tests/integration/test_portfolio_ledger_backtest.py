from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.multi_day import (
    PortfolioBacktestRunner,
    PortfolioBacktestStep,
)
from openalpha_cn.backtest.portfolio import PortfolioOrder, PortfolioState
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger


def flat_bar(subject: str, trade_date: date, close: str) -> MarketBar:
    """The shared `bar` fixture with the subject opened up.

    `tests/conftest.py::bar` hard-codes `000001.SZ`, which was the right shape while a step
    could only ever hold one name. A book needs two.
    """
    price = Decimal(close)
    return MarketBar(
        subject=subject,
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


def test_portfolio_ledger_is_append_only_and_idempotent(tmp_path: Path, bar) -> None:
    ledger = SQLitePortfolioLedger(tmp_path / "state.sqlite3")
    initial = PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("20000"))
    transition = PortfolioBacktestRunner().simulator.execute_order(
        state=initial,
        order=PortfolioOrder(order_id="order-1", subject="000001.SZ", side="buy", quantity=100),
        market=bar(date(2026, 7, 24), "10"),
    )

    ledger.append(transition)
    ledger.append(transition)

    assert ledger.get("order-1") == transition
    assert ledger.list() == (transition,)
    with pytest.raises(ValueError, match="order_id conflicts"):
        ledger.append(transition.model_copy(update={"reason": "conflict"}))


def test_multi_day_report_reconciles_return_turnover_capacity_and_exposure(
    tmp_path: Path,
    bar,
) -> None:
    """The same two sessions `V2-P5-003` inherited, re-expressed as sessions.

    Every reconciled figure below is byte-identical to what the single-subject runner returned
    for this book, and that is the point of keeping it: a one-name book is the case where
    holding a book and holding a name are the same thing, so the session shape must not move a
    number here. `test_a_two_name_session_is_one_step_one_benchmark_and_one_dated_point` below
    is where the two shapes stop agreeing.
    """
    ledger = SQLitePortfolioLedger(tmp_path / "state.sqlite3")
    runner = PortfolioBacktestRunner(ledger=ledger)
    report = runner.run(
        initial=PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("20000")),
        steps=(
            PortfolioBacktestStep(
                trade_date=date(2026, 7, 24),
                bars=(bar(date(2026, 7, 24), "10"),),
                orders=(
                    PortfolioOrder(order_id="buy", subject="000001.SZ", side="buy", quantity=100),
                ),
                benchmark_close=Decimal("100"),
            ),
            PortfolioBacktestStep(
                trade_date=date(2026, 7, 25),
                bars=(bar(date(2026, 7, 25), "11"),),
                orders=(
                    PortfolioOrder(order_id="sell", subject="000001.SZ", side="sell", quantity=100),
                ),
                benchmark_close=Decimal("102"),
            ),
        ),
    )

    assert report.final_state.cash == Decimal("20089.43")
    assert report.total_return == Decimal("0.004472")
    assert report.benchmark_return == Decimal("0.020000")
    assert report.turnover == Decimal("0.104766")
    assert report.max_order_notional == Decimal("1100.00")
    assert report.max_gross_exposure > 0
    assert report.attribution[0].subject == "000001.SZ"
    assert report.attribution[0].pnl == Decimal("89.43")
    assert report.carried_marks == ()
    assert len(ledger.list()) == 2


def test_a_two_name_session_is_one_step_one_benchmark_and_one_dated_point(
    tmp_path: Path,
) -> None:
    """`V2-P5-003` end to end against the real ledger: K names, one step, K ledger rows.

    Two things the single-subject runner could not do are asserted together, because they are
    the same defect seen from two sides. Both names are bought on one session -- which used to
    be two steps carrying two independently dated bars and two benchmark closes for one day --
    and the second session trades **nothing** while both names move, which used to be
    inexpressible: the only way to hand the runner a price was to attach an order to it.
    """
    ledger = SQLitePortfolioLedger(tmp_path / "state.sqlite3")
    report = PortfolioBacktestRunner(ledger=ledger).run(
        initial=PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("100000")),
        steps=(
            PortfolioBacktestStep(
                trade_date=date(2026, 7, 24),
                bars=(
                    flat_bar("000001.SZ", date(2026, 7, 24), "10.00"),
                    flat_bar("600000.SH", date(2026, 7, 24), "10.00"),
                ),
                orders=(
                    PortfolioOrder(
                        order_id="buy-a", subject="000001.SZ", side="buy", quantity=1000
                    ),
                    PortfolioOrder(
                        order_id="buy-b", subject="600000.SH", side="buy", quantity=1000
                    ),
                ),
                benchmark_close=Decimal("100"),
            ),
            PortfolioBacktestStep(
                trade_date=date(2026, 7, 27),
                bars=(
                    flat_bar("000001.SZ", date(2026, 7, 27), "12.00"),
                    flat_bar("600000.SH", date(2026, 7, 27), "8.00"),
                ),
                orders=(),
                benchmark_close=Decimal("101"),
            ),
        ),
    )

    assert tuple(point.trade_date for point in report.equity_curve) == (
        "2026-07-24",
        "2026-07-27",
    )
    assert report.final_state.market_value == Decimal("20000.00")
    assert report.final_state.as_of == date(2026, 7, 27)
    assert len(ledger.list()) == 2
    assert {transition.order.order_id for transition in ledger.list()} == {"buy-a", "buy-b"}
