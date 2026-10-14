import ast
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Final

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


REBUILT_FROM_A_BAR: Final[frozenset[str]] = frozenset({"backtest/paper_step.py::_scaled"})
"""Where a `MarketBar` may be validated from another bar's own fields rather than assembled:
`paper_step._scaled` multiplies every price of a stored bar by an adjustment ratio. Not an
assembly -- nothing is read off the panel there -- and the one such site today; a second is a
reviewed change to this set."""

FACES_ON_THE_ONE_ASSEMBLY: Final[frozenset[str]] = frozenset(
    {"shortlist_view.py", "factor_view.py", "strategy_view.py", "strategy_survivor_market.py"}
)
"""The modules that build a bar off the panel, each through `stored_market_bar`."""


def _names_market_bar(node: ast.expr, aliases: frozenset[str]) -> bool:
    return (isinstance(node, ast.Name) and node.id in aliases) or (
        isinstance(node, ast.Attribute) and node.attr == "MarketBar"
    )


def _aliases_of(tree: ast.Module, name: str) -> frozenset[str]:
    """`name` and every name a module binds to it: `import ... as`, or `alias = name`."""
    found = {name}
    found.update(
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == name and alias.asname
    )
    found.update(
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Name)
        and node.value.id == name
        for target in node.targets
        if isinstance(target, ast.Name)
    )
    return frozenset(found)


def _visit(
    node: ast.AST,
    owner: str,
    *,
    relative: str,
    bars: frozenset[str],
    shared: frozenset[str],
    uses: dict[str, list[str]],
) -> None:
    site = f"{relative}::{owner}"
    if isinstance(node, ast.ClassDef) and any(_names_market_bar(base, bars) for base in node.bases):
        uses["subclass"].append(f"{relative}::{node.name}")
    if isinstance(node, ast.Call):
        callee = node.func
        if _names_market_bar(callee, bars):
            uses["assembly"].append(site)
        elif (
            isinstance(callee, ast.Attribute)
            and callee.attr.startswith(("model_validate", "model_construct"))
            and _names_market_bar(callee.value, bars)
        ):
            uses["rebuild"].append(site)
        elif (isinstance(callee, ast.Name) and callee.id in shared) or (
            isinstance(callee, ast.Attribute) and callee.attr == "stored_market_bar"
        ):
            uses["shared"].append(site)
    inner = owner
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        inner = f"{owner}.{node.name}".lstrip(".")
    for child in ast.iter_child_nodes(node):
        _visit(child, inner, relative=relative, bars=bars, shared=shared, uses=uses)


def _market_bar_uses(root: Path) -> dict[str, list[str]]:
    """Every site in `root` that assembles, subclasses or rebuilds a `MarketBar`, or calls the
    one assembly, as `"<path>::<enclosing def or class>"` by kind."""
    uses: dict[str, list[str]] = {"assembly": [], "subclass": [], "rebuild": [], "shared": []}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        _visit(
            tree,
            "",
            relative=path.relative_to(root).as_posix(),
            bars=_aliases_of(tree, "MarketBar"),
            shared=_aliases_of(tree, "stored_market_bar"),
            uses=uses,
        )
    return uses


def test_the_execution_module_holds_the_one_market_bar_assembly_and_nothing_bypasses_it() -> None:
    """T5 7.1's rule as a check: one `MarketBar` assembly, not one per face (`V2-P7-009`).

    The P6 faces each built a bar by hand until this issue moved them onto `stored_market_bar`.
    A second assembly is refused however it is spelled -- `MarketBar(...)`, an alias imported or
    assigned, `execution.MarketBar(...)`, a subclass, or `model_validate`/`model_construct`
    outside `REBUILT_FROM_A_BAR`. The walk must also find what it is meant to find: the source
    tree, the assembly inside `stored_market_bar`, and every face calling it -- so a test file
    moved where `SOURCE_ROOT` reaches nothing fails rather than passing on an empty walk.
    """
    assert SOURCE_ROOT.is_dir(), SOURCE_ROOT

    uses = _market_bar_uses(SOURCE_ROOT)

    assert uses["assembly"] == ["backtest/execution.py::stored_market_bar"]
    assert uses["subclass"] == []
    assert sorted(uses["rebuild"]) == sorted(REBUILT_FROM_A_BAR)
    callers = {site.partition("::")[0] for site in uses["shared"]}
    assert callers >= FACES_ON_THE_ONE_ASSEMBLY, sorted(callers)
    assert len(uses["shared"]) >= len(FACES_ON_THE_ONE_ASSEMBLY)
