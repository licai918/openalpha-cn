import ast
from datetime import date
from decimal import Decimal
from pathlib import Path

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    MarketBar,
    at_the_open,
    stored_market_bar,
    suspended_at_the_close,
    suspended_at_the_open,
)
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.price_limits import PriceLimit, TradingState

SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src" / "openalpha_cn"


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


def test_a_stored_bar_and_its_published_band_make_one_market_bar_at_their_exact_prices() -> None:
    """`stored_market_bar`: one assembly of a stored bar and band into `MarketBar` (`V2-P7-009`).

    Every price crosses from the panel's `float` through `Decimal(str(...))`, so `10.1` lands on
    `Decimal("10.1")` and not on the binary double's expansion; the band crosses through
    `published_limit_fields`, the limit-free sentinel included; the board comes from the code
    (`688*` is STAR); subject and session are the stored bar's own. `suspended` and `is_st` are
    the caller's to decide and are carried as given.
    """
    stored = DailyBar(
        ts_code="688981.SH",
        trade_date=date(2026, 1, 7),
        open=10.1,
        high=10.7,
        low=9.9,
        close=10.3,
        pre_close=10.2,
        pct_chg=0.98,
        vol=100.0,
        amount=1000.0,
    )
    band = PriceLimit(
        ts_code="688981.SH", trade_date=date(2026, 1, 7), up_limit=12.24, down_limit=8.16
    )
    sentinel = PriceLimit(
        ts_code="688981.SH", trade_date=date(2026, 1, 7), up_limit=99999.999, down_limit=0.01
    )

    built = stored_market_bar(stored, band, suspended=True, is_st=True)

    assert built == MarketBar(
        subject="688981.SH",
        trade_date=date(2026, 1, 7),
        board="star",
        previous_close=Decimal("10.2"),
        open=Decimal("10.1"),
        high=Decimal("10.7"),
        low=Decimal("9.9"),
        close=Decimal("10.3"),
        suspended=True,
        is_st=True,
        up_limit=Decimal("12.24"),
        down_limit=Decimal("8.16"),
    )
    assert built.open != Decimal(stored.open)  # the binary expansion it never reaches
    unlimited = stored_market_bar(stored, sentinel, suspended=False, is_st=False)
    assert (unlimited.up_limit, unlimited.down_limit) == (Decimal("99999.999"), Decimal("0.01"))
    assert (unlimited.suspended, unlimited.is_st) == (False, False)


def test_an_order_at_the_open_meets_a_suspended_bar_on_any_halt_row_but_a_resumption() -> None:
    """`suspended_at_the_open`: the P6 book's halt collapse for a fill at the open (`V2-P7-009`).

    `suspended_at_the_close`, and also every timed interruption whatever its window -- a stored
    timing string is not parsed for its left endpoint, so a morning halt that ended long before
    the close is untradeable at the open here and tradeable at the close there. That is the
    fail-closed direction `strategy_backtest`'s
    `an_intraday_halt_makes_the_whole_session_untradeable_at_the_open` records.
    """
    cases = (
        (TradingState.halted, None),
        (TradingState.interrupted, "09:30-10:30"),
        (TradingState.interrupted, "13:00-15:00"),
        (TradingState.interrupted, None),
        (TradingState.resumed, None),
        (None, None),
    )

    assert [suspended_at_the_open(state, timing) for state, timing in cases] == [
        True,
        True,
        True,
        True,
        False,
        False,
    ]
    assert suspended_at_the_close(TradingState.interrupted, "09:30-10:30") is False


def test_no_module_but_the_execution_policy_assembles_a_market_bar_by_hand() -> None:
    """T5 7.1's rule as a check: one `MarketBar` assembly, not one per face (`V2-P7-009`).

    Every `MarketBar(...)` call in `src/` outside `backtest/execution.py` is another copy of the
    conversion `stored_market_bar` owns, and the P6 faces each held one until this issue moved
    them onto it. A copy of an existing bar (`model_copy`, `model_validate`) is not an assembly
    and is not counted.
    """
    assemblies = sorted(
        f"{path.relative_to(SOURCE_ROOT)}:{node.lineno}"
        for path in SOURCE_ROOT.rglob("*.py")
        if path != SOURCE_ROOT / "backtest" / "execution.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "MarketBar"
    )

    assert assemblies == []
