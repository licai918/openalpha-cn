from datetime import date
from decimal import Decimal

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    MarketBar,
    at_the_open,
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


def test_at_the_open_judges_an_order_against_the_open_print_alone() -> None:
    """The open-auction rule as the policy's own rules on a one-price bar (`V2-P7-017`).

    The bar opens locked at the published limit-up price (11.00) and trades off it later: low
    10.60, close 10.80. Judged on the whole bar a buy fills at the close, because `low < upper`;
    judged at the open print it is refused, because `low == open == 11.00 >= upper`. Every field
    the verdict reads besides the four prices -- previous close, band, suspension, board -- is
    carried over unchanged, so the same function serves the P6 book and the paper step.
    """
    locked_at_the_open = bar(
        open=Decimal("11.00"),
        high=Decimal("11.00"),
        low=Decimal("10.60"),
        close=Decimal("10.80"),
        up_limit=Decimal("11.00"),
        down_limit=Decimal("9.00"),
    )
    policy = AShareExecutionPolicy()
    whole_bar = policy.execute(ExecutionRequest(side="buy", quantity=100), locked_at_the_open)
    opened = at_the_open(locked_at_the_open)
    at_open = policy.execute(ExecutionRequest(side="buy", quantity=100), opened)

    assert whole_bar.status == "filled"
    assert whole_bar.filled_price == Decimal("10.80")
    assert at_open.reason == "buy cannot fill on a one-price limit-up bar"
    assert (opened.open, opened.high, opened.low, opened.close) == (Decimal("11.00"),) * 4
    assert opened.model_dump(exclude={"high", "low", "close"}) == locked_at_the_open.model_dump(
        exclude={"high", "low", "close"}
    )
