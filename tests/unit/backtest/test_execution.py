from datetime import date
from decimal import Decimal

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    MarketBar,
)


def bar(**updates: object) -> MarketBar:
    values: dict[str, object] = {
        "subject": "000001.SZ",
        "trade_date": date(2026, 7, 24),
        "board": "main",
        "previous_close": Decimal("10.00"),
        "open": Decimal("10.20"),
        "high": Decimal("10.80"),
        "low": Decimal("10.10"),
        "close": Decimal("10.50"),
        "suspended": False,
        "is_st": False,
    }
    values.update(updates)
    return MarketBar.model_validate(values)


def test_main_board_buy_requires_a_100_share_lot() -> None:
    result = AShareExecutionPolicy().execute(
        ExecutionRequest(side="buy", quantity=150),
        bar(),
    )

    assert result.status == "rejected"
    assert result.reason == "main-board buy quantity must be a multiple of 100"


def test_star_market_buy_allows_one_share_increments_after_200() -> None:
    result = AShareExecutionPolicy().execute(
        ExecutionRequest(side="buy", quantity=201),
        bar(board="star"),
    )

    assert result.status == "filled"
    assert result.quantity == 201


def test_t_plus_one_and_locked_price_limits_are_enforced() -> None:
    t_plus_one = AShareExecutionPolicy().execute(
        ExecutionRequest(
            side="sell",
            quantity=100,
            position_open_date=date(2026, 7, 24),
        ),
        bar(),
    )
    locked_limit_up = AShareExecutionPolicy().execute(
        ExecutionRequest(side="buy", quantity=100),
        bar(
            open=Decimal("11.00"),
            high=Decimal("11.00"),
            low=Decimal("11.00"),
            close=Decimal("11.00"),
        ),
    )

    assert t_plus_one.reason == "A-share cash equities cannot be sold on the purchase date"
    assert locked_limit_up.reason == "buy cannot fill on a one-price limit-up bar"


def test_sell_cost_includes_commission_transfer_fee_and_stamp_duty() -> None:
    result = AShareExecutionPolicy().execute(
        ExecutionRequest(
            side="sell",
            quantity=1000,
            position_open_date=date(2026, 7, 23),
        ),
        bar(low=Decimal("9.90"), close=Decimal("10.00")),
    )

    assert result.status == "filled"
    assert result.notional == Decimal("10000.00")
    assert result.commission == Decimal("5.00")
    assert result.transfer_fee == Decimal("0.10")
    assert result.stamp_duty == Decimal("5.00")
    assert result.total_cost == Decimal("10.10")
