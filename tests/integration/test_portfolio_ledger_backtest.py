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


def bar(trade_date: date, close: str) -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        subject="000001.SZ",
        trade_date=trade_date,
        board="main",
        previous_close=Decimal("10"),
        open=price,
        high=price,
        low=price,
        close=price,
        suspended=False,
        is_st=False,
    )


def test_portfolio_ledger_is_append_only_and_idempotent(tmp_path: Path) -> None:
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
) -> None:
    ledger = SQLitePortfolioLedger(tmp_path / "state.sqlite3")
    runner = PortfolioBacktestRunner(ledger=ledger)
    report = runner.run(
        initial=PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("20000")),
        steps=(
            PortfolioBacktestStep(
                order=PortfolioOrder(order_id="buy", subject="000001.SZ", side="buy", quantity=100),
                market=bar(date(2026, 7, 24), "10"),
                benchmark_close=Decimal("100"),
            ),
            PortfolioBacktestStep(
                order=PortfolioOrder(
                    order_id="sell", subject="000001.SZ", side="sell", quantity=100
                ),
                market=bar(date(2026, 7, 25), "11"),
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
    assert len(ledger.list()) == 2
