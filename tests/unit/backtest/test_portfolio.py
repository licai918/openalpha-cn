import json
import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.portfolio import (
    PORTFOLIO_TRANSITION_VERSIONS,
    PortfolioLimits,
    PortfolioOrder,
    PortfolioSimulator,
    PortfolioState,
)
from openalpha_cn.domain.versioning import read_versioned
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger


def state(*, cash: str = "20000.00") -> PortfolioState:
    return PortfolioState(
        as_of=date(2026, 7, 23),
        cash=Decimal(cash),
    )


def test_portfolio_buy_updates_cash_lots_marks_and_equity(bar) -> None:
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


def test_portfolio_enforces_t_plus_one_and_realizes_fifo_profit(bar) -> None:
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


def test_portfolio_rejects_insufficient_cash_and_hard_exposure_limit(bar) -> None:
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


# --- V2-P5-002: the target weight, the cash floor, and what the stored row costs -----------------


def test_a_buy_whose_declared_target_weight_is_over_the_position_cap_is_refused_by_name(
    bar,
) -> None:
    """`target_weight` is a declaration, and a declaration the caps already forbid is a defect in
    the plan rather than a fact about the market.

    The quantity here is tiny -- 100 shares at 10.00 against 20000 of cash is about 5% -- so the
    *realised* weight passes every existing check and only the declared one can refuse it. A
    fixture whose quantity also breached the cap would be green under an implementation that
    never read `target_weight` at all.
    """
    simulator = PortfolioSimulator(limits=PortfolioLimits(max_position_weight=Decimal("0.25")))
    transition = simulator.execute_order(
        state=state(),
        order=PortfolioOrder(
            order_id="order-over-target",
            subject="000001.SZ",
            side="buy",
            quantity=100,
            target_weight=Decimal("0.40"),
        ),
        market=bar(trade_date=date(2026, 7, 24)),
    )

    assert transition.status == "rejected"
    assert transition.reason == "declared target weight exceeds maximum position weight"
    assert transition.after == transition.before


def test_the_same_order_under_a_target_weight_the_cap_allows_is_filled_and_carries_it(bar) -> None:
    """The control for the test above, and the round trip: the declared target survives onto the
    transition, which is what makes a stored row say which plan produced it."""
    simulator = PortfolioSimulator(limits=PortfolioLimits(max_position_weight=Decimal("0.25")))
    transition = simulator.execute_order(
        state=state(),
        order=PortfolioOrder(
            order_id="order-within-target",
            subject="000001.SZ",
            side="buy",
            quantity=100,
            target_weight=Decimal("0.20"),
        ),
        market=bar(trade_date=date(2026, 7, 24)),
    )

    assert transition.status == "filled"
    assert transition.order.target_weight == Decimal("0.20")


def test_a_buy_that_would_leave_the_book_under_its_cash_floor_is_refused_naming_the_floor(
    bar,
) -> None:
    """The cash floor is a real check with a reason of its own.

    `max_total_exposure` is opened to 1 so the floor is the only bound that can bind; otherwise
    the exposure check fires first and the assertion would be green on a build that never read
    `min_cash_weight`.
    """
    simulator = PortfolioSimulator(
        limits=PortfolioLimits(
            max_position_weight=Decimal("1"),
            max_total_exposure=Decimal("1"),
            min_cash_weight=Decimal("0.90"),
        )
    )
    transition = simulator.execute_order(
        state=state(),
        order=PortfolioOrder(
            order_id="order-cash-floor",
            subject="000001.SZ",
            side="buy",
            quantity=1000,
        ),
        market=bar(trade_date=date(2026, 7, 24)),
    )

    assert transition.status == "rejected"
    assert transition.reason == "minimum cash weight breached"


def test_a_transition_stored_before_the_target_weight_existed_still_reads_back(tmp_path) -> None:
    """AGENTS.md rule 3, measured rather than assumed.

    `PortfolioTransition` is a stored row and `V2-P4-001`'s breaking-change window is closed, so
    the question is whether adding this field is a breaking change to one. Measured in both
    directions: an old payload -- byte-identical to what the previous build wrote, with no
    `target_weight` key anywhere -- reads back through the same `read_versioned` the ledger uses,
    because the default supplies the key. What does move is the *bytes*: the payload the ledger
    compares on re-append now carries `"target_weight":null`, so re-appending a transition an
    older build stored raises the conflict guard. That is the migration cost, and it is a ledger
    rewrite rather than a contract version bump -- there is no second version of this model.
    """
    simulator = PortfolioSimulator()
    transition = simulator.execute_order(
        state=state(),
        order=PortfolioOrder(
            order_id="order-old-row",
            subject="000001.SZ",
            side="buy",
            quantity=100,
        ),
        market=MarketBar(
            subject="000001.SZ",
            trade_date=date(2026, 7, 24),
            board="main",
            previous_close=Decimal("10.00"),
            open=Decimal("10.00"),
            high=Decimal("10.00"),
            low=Decimal("10.00"),
            close=Decimal("10.00"),
            suspended=False,
            is_st=False,
        ),
    )
    current = transition.model_dump_json(exclude_computed_fields=True)
    old = json.loads(current)
    del old["order"]["target_weight"]
    old_payload = json.dumps(old)

    reopened = read_versioned(PORTFOLIO_TRANSITION_VERSIONS, old_payload)

    assert "target_weight" not in old_payload
    assert '"target_weight":null' in current
    assert reopened.order.target_weight is None
    assert reopened == transition

    ledger = SQLitePortfolioLedger(tmp_path / "state.sqlite3")
    with closing(sqlite3.connect(tmp_path / "state.sqlite3")) as connection, connection:
        connection.execute(
            "INSERT INTO portfolio_transitions (order_id, subject, payload) VALUES (?, ?, ?)",
            (transition.order.order_id, transition.order.subject, old_payload),
        )
    with pytest.raises(ValueError, match="portfolio order_id conflicts"):
        ledger.append(transition)


def test_a_declared_target_exactly_at_the_cap_is_accepted_and_the_boundary_is_the_cap_itself(
    bar,
) -> None:
    """The `>` in the target-weight check, pinned. A mutation sweep found `>` and `>=`
    indistinguishable on every fixture here: nobody had declared a target *equal* to the cap.

    A cap is the largest weight that is allowed, so equality is the allowed side.
    """
    simulator = PortfolioSimulator(limits=PortfolioLimits(max_position_weight=Decimal("0.25")))
    transition = simulator.execute_order(
        state=state(),
        order=PortfolioOrder(
            order_id="order-at-the-cap",
            subject="000001.SZ",
            side="buy",
            quantity=100,
            target_weight=Decimal("0.25"),
        ),
        market=bar(trade_date=date(2026, 7, 24)),
    )

    assert transition.status == "filled"


def test_a_book_landing_exactly_on_its_cash_floor_is_accepted(bar) -> None:
    """The `<` in the cash-floor check, pinned by the same argument and the same sweep.

    The floor is read off an unbounded run and fed back, so the comparison is on exactly equal
    Decimals rather than on two numbers that happen to be close -- which is the only way to make
    `<` and `<=` disagree.
    """
    order = PortfolioOrder(
        order_id="order-on-the-floor",
        subject="000001.SZ",
        side="buy",
        quantity=100,
    )
    market = bar(trade_date=date(2026, 7, 24))
    unbounded = PortfolioSimulator().execute_order(state=state(), order=order, market=market)
    exact = unbounded.after.cash / unbounded.after.equity

    at_the_floor = PortfolioSimulator(limits=PortfolioLimits(min_cash_weight=exact)).execute_order(
        state=state(), order=order, market=market
    )
    just_above = PortfolioSimulator(
        limits=PortfolioLimits(min_cash_weight=exact + Decimal("0.000001"))
    ).execute_order(state=state(), order=order, market=market)

    assert unbounded.status == "filled"
    assert at_the_floor.status == "filled"
    assert just_above.status == "rejected"
    assert just_above.reason == "minimum cash weight breached"


def test_the_shipped_limit_defaults_hold_no_cash_back_and_declare_no_other_bound() -> None:
    """What a caller who types nothing gets, as an equality.

    A default `min_cash_weight` above zero would refuse orders nobody asked it to refuse, and the
    sweep found no test could tell `0` from `0.10`. The two `None`s matter for the opposite
    reason: an industry cap that is on by default would be refused by the construction policy on
    every shipped face, since no candidate there carries an industry.
    """
    limits = PortfolioLimits()

    assert limits.min_cash_weight == Decimal("0")
    assert limits.max_industry_weight is None
    assert limits.turnover_budget is None
    assert (limits.max_position_weight, limits.max_total_exposure) == (
        Decimal("0.25"),
        Decimal("0.80"),
    )


def test_a_declared_target_weight_is_a_share_of_equity_and_neither_zero_nor_leveraged() -> None:
    """`gt=0` and `le=1` on the new field, both directions.

    Zero is refused because a *buy* aiming at nothing is a contradiction rather than a small
    order, and above one is refused because this book has no borrowing to account for. The sweep
    found both bounds unpinned -- `ge=0` and `le=2` were each green on every fixture.
    """
    with pytest.raises(ValidationError):
        PortfolioOrder(
            order_id="order-zero-target",
            subject="000001.SZ",
            side="buy",
            quantity=100,
            target_weight=Decimal("0"),
        )
    with pytest.raises(ValidationError):
        PortfolioOrder(
            order_id="order-levered-target",
            subject="000001.SZ",
            side="buy",
            quantity=100,
            target_weight=Decimal("1.5"),
        )
