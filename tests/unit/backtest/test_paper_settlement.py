"""Settling one session of the paper book (`V2-P7-009`): fills, order lifecycle, cool-downs,
entry factors and the delisting write-off.

Every fixture is hand-written with invented prices on the Shanghai main board, and every number
asserted is worked out in the comment beside it. The default `CostSchedule` applies (commission
0.03% with a 5.00 floor, transfer fee 0.001%, sell stamp duty 0.05%, each rounded half-up to the
fen); `PortfolioLimits` are 1 / 1 / 0, the paper book's setting (D15), so the only constraint a
buy meets besides the market is cash. Sessions: S0 2021-03-01 is the session of the snapshot a
settlement starts from (ordinal 100); S1 ... S5 are the sessions after it. Every order in a
`previous(...)` book was planned on S0, so it is due on S1, and a buy's last session is S3 (103).

Each settlement is turned into the evening's snapshot with `tonight(...)`, so
`PaperBookSnapshot`'s checks (an entry factor per holding, the attempt count of every live order,
no cool-down for a held name, ...) run on every result below.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from openalpha_cn.backtest.execution import MarketBar, security_board
from openalpha_cn.backtest.multi_day import PortfolioBacktestStep
from openalpha_cn.backtest.paper import PaperPortfolio
from openalpha_cn.backtest.paper_settlement import (
    WRITE_OFF_REASON,
    Settlement,
    WriteOff,
    book_state,
    settle_session,
    write_off_id,
)
from openalpha_cn.backtest.paper_state import (
    EntryFactor,
    PaperBookSnapshot,
    PendingOrder,
    snapshot_payload,
)
from openalpha_cn.backtest.paper_step import NO_BAR_REASON, AttemptedOrder, attempt_id
from openalpha_cn.backtest.portfolio import PortfolioLimits
from openalpha_cn.domain.portfolio import (
    PortfolioPosition,
    PortfolioState,
    PositionLot,
    PositionMark,
)
from openalpha_cn.domain.survivor_book import (
    EligibilityVerdict,
    RankedName,
    ScreenResult,
    SurvivorBookConfig,
    survivor_config_digest,
)
from openalpha_cn.domain.survivor_orders import TradeList, TradeOrder, build_trade_list
from openalpha_cn.domain.survivor_policy import BookState, Cooldown, Holding, decide_book
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger

D = Decimal
S0, S1, S2, S3, S4, S5 = (
    date(2021, 3, 1),
    date(2021, 3, 2),
    date(2021, 3, 3),
    date(2021, 3, 4),
    date(2021, 3, 5),
    date(2021, 3, 8),
)
INDEX = {S0: 100, S1: 101, S2: 102, S3: 103, S4: 104, S5: 105}
BOUGHT_BEFORE = date(2021, 2, 25)
CONFIG = SurvivorBookConfig(seed=7)
FULL = PortfolioLimits(max_position_weight=D(1), max_total_exposure=D(1), min_cash_weight=D(0))
BENCHMARK = D("6000.00")
A, B, C, E = "600010.SH", "600020.SH", "600030.SH", "600040.SH"


# ---- fixtures ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Market:
    """A session's market as C1 delivers it; `settle_session` reads it structurally."""

    bars: Mapping[str, MarketBar]
    adj_factor: Mapping[str, Decimal]
    benchmark_close: Decimal = BENCHMARK


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


def market(*quotes: MarketBar, adj: Mapping[str, str]) -> Market:
    return Market(
        bars={quote.subject: quote for quote in quotes},
        adj_factor={code: D(value) for code, value in adj.items()},
    )


def buy(code: str, quantity: int, *, signal: int = 100, price: str = "10.00") -> TradeOrder:
    return TradeOrder(
        signal_session=signal,
        execute_session=signal + 1,
        expires_session=signal + 3,
        side="buy",
        ts_code=code,
        quantity=quantity,
        reference_price=D(price),
        reason="ENTRY_REPLACEMENT",
    )


def sell(
    code: str,
    quantity: int,
    *,
    signal: int = 100,
    price: str = "10.00",
    reason: str = "EXIT_SURVIVOR_LOST",
) -> TradeOrder:
    return TradeOrder.model_validate(
        {
            "signal_session": signal,
            "execute_session": signal + 1,
            "side": "sell",
            "ts_code": code,
            "quantity": quantity,
            "reference_price": D(price),
            "reason": reason,
        }
    )


Held = tuple[str, int, str, str, str]
"""`(code, shares, cost basis, mark, entry factor)` of a holding bought before S0."""


def previous(
    cash: str,
    *holdings: Held,
    orders: tuple[TradeOrder, ...] = (),
    config: SurvivorBookConfig = CONFIG,
    settled: bool = True,
) -> PaperBookSnapshot:
    """The book at the close of S0: `holdings`, and `orders` planned on S0 (tried 0 times)."""
    return PaperBookSnapshot(
        config_digest=survivor_config_digest(config),
        session_index=100,
        session=S0,
        book=PortfolioState(
            as_of=S0,
            cash=D(cash),
            positions=tuple(
                PortfolioPosition(
                    subject=code,
                    lots=(
                        PositionLot(open_date=BOUGHT_BEFORE, quantity=shares, cost_basis=D(basis)),
                    ),
                )
                for code, shares, basis, _, _ in holdings
            ),
            marks=tuple(
                PositionMark(subject=code, price=D(mark)) for code, _, _, mark, _ in holdings
            ),
        ),
        entry_factors=tuple(
            EntryFactor(ts_code=code, entry_adj=D(entry)) for code, _, _, _, entry in holdings
        ),
        pending_orders=tuple(PendingOrder(order=order, attempts=0) for order in orders),
        last_settled_session=100 if settled else None,
    )


def ledger_at(path: Path) -> tuple[SQLitePortfolioLedger, PaperPortfolio]:
    ledger = SQLitePortfolioLedger(path)
    return ledger, PaperPortfolio(ledger=ledger, limits=FULL)


def settle(
    book: PaperBookSnapshot,
    day: date,
    quotes: Market,
    portfolio: PaperPortfolio,
    *,
    survivors: Collection[str] = (),
    delisted: Collection[str] = (),
    config: SurvivorBookConfig = CONFIG,
    observed_on: date | None = None,
) -> Settlement:
    return settle_session(
        previous=book,
        session=day,
        session_index=INDEX[day],
        market=quotes,
        survivors=survivors,
        delisted=delisted,
        portfolio=portfolio,
        config=config,
        observed_on=day if observed_on is None else observed_on,
    )


def tonight(settlement: Settlement, *planned: TradeOrder) -> PaperBookSnapshot:
    """The evening's snapshot: the settlement, its live orders carried, `planned` new."""
    return settlement.with_plan(
        TradeList(
            signal_session=settlement.session_index,
            orders=planned,
            carried=tuple(item.order for item in settlement.live),
        )
    )


def rows(ledger: SQLitePortfolioLedger) -> list[tuple[str, str, str | None]]:
    return [(row.order.order_id, row.status, row.reason) for row in ledger.list()]


def marks(state: PortfolioState) -> dict[str, Decimal]:
    return {mark.subject: mark.price for mark in state.marks}


def live(settlement: Settlement) -> list[tuple[str, str, int]]:
    return [(item.order.side, item.order.ts_code, item.attempts) for item in settlement.live]


def factors(settlement: Settlement) -> dict[str, Decimal]:
    return {item.ts_code: item.entry_adj for item in settlement.entry_factors}


def cooling(settlement: Settlement) -> list[tuple[str, int]]:
    return [(item.ts_code, item.since_session) for item in settlement.cooldowns]


# 600020.SH, held, trades quietly through every session it appears in.
QUIET_B = {
    day: bar(B, day, "11.50", "11.50", "11.50", "11.50", "11.50", "12.65", "10.35")
    for day in (S1, S2, S3, S4, S5)
}
# 600030.SH opens at 10.10 and closes at 10.50: a buy at the open fills at 10.10.
OPEN_C_S1 = bar(C, S1, "10.00", "10.10", "10.60", "10.05", "10.50", "11.00", "9.00")
# 600040.SH opens locked at its limit-up price three sessions running.
LOCKED_UP_E = {
    S1: bar(E, S1, "20.00", "22.00", "22.00", "22.00", "22.00", "22.00", "18.00"),
    S2: bar(E, S2, "22.00", "24.20", "24.20", "24.20", "24.20", "24.20", "19.80"),
    S3: bar(E, S3, "24.20", "26.62", "26.62", "26.62", "26.62", "26.62", "21.78"),
}


# ---- fills and the close -------------------------------------------------------------------------


def test_an_order_fills_at_the_open_and_the_book_ends_the_session_at_the_close(
    tmp_path: Path,
) -> None:
    """S0: cash 20,000.00, 600020.SH 1,000 marked 11.50; a buy of 600030.SH 1,000 is due on S1.

    It fills at its 10.10 open: notional 10,100.00, commission max(3.03, 5.00) = 5.00, transfer
    0.101 -> 0.10, so 10,105.10 out of cash -> 9,894.90. The runner leaves the name bought at the
    open it was bought at (equity 9,894.90 + 11,600 + 10,100 = 31,594.90); the session ends at
    the close, 10.50 (31,994.90). Its entry factor is the fill session's, 3.000.
    """
    order = buy(C, 1000)
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    held_b = bar(B, S1, "11.50", "11.50", "11.70", "11.40", "11.60", "12.65", "10.35")

    settled = settle(
        previous("20000.00", (B, 1000, "11500.00", "11.50", "1.000"), orders=(order,)),
        S1,
        market(held_b, OPEN_C_S1, adj={B: "1.000", C: "3.000"}),
        portfolio,
        survivors={C},
    )

    assert rows(ledger) == [(attempt_id(order, S1), "filled", None)]
    (fill,) = settled.runner_record.transitions
    assert fill.execution is not None
    assert (fill.execution.filled_price, fill.execution.total_cost) == (D("10.10"), D("5.10"))
    assert settled.runner_record.equity == D("31594.90")  # 600030.SH still at its open
    assert settled.equity == settled.book.equity == D("31994.90")  # the session's equity
    # gross exposure at the close: 22,100.00 / 31,994.90 = 0.6907350...
    assert settled.gross_exposure == D("0.690735")
    assert settled.book.as_of == S1
    assert settled.book.cash == D("9894.90")
    assert marks(settled.book) == {B: D("11.60"), C: D("10.50")}
    assert settled.book.equity == D("31994.90")
    assert [
        (p.subject, lot.open_date, lot.quantity, lot.cost_basis)
        for p in settled.book.positions
        for lot in p.lots
    ] == [(B, BOUGHT_BEFORE, 1000, D("11500.00")), (C, S1, 1000, D("10105.10"))]
    assert settled.filled == (order,)
    assert (settled.live, settled.expired, settled.cancelled) == ((), (), ())
    assert factors(settled) == {B: D("1.000"), C: D("3.000")}
    assert settled.attempts == (
        AttemptedOrder(order=order, attempt_id=attempt_id(order, S1), outcome="submitted"),
    )
    assert settled.written_off == ()
    assert tonight(settled).last_settled_session == 101


def test_sells_go_first_and_a_buy_the_cash_cannot_cover_is_refused_whole_and_stays_live(
    tmp_path: Path,
) -> None:
    """S0: cash 1,000.00 and 600010.SH 1,000 (cost 10,000.00); due on S1: sell 600010.SH, buy
    600030.SH 1,000 and 600040.SH 1,000.

    The sale goes first: 10,000.00 less 5.00 + 0.10 + 5.00 -> cash 10,989.90, realised -10.10.
    600030.SH then fills at 10.10 (10,105.10 -> cash 884.80); 600040.SH's 10,005.10 does not fit
    and the paper book refuses it whole. It stays live, tried once. 600010.SH cools down from
    S1. At the close: 884.80 + 1,000 x 10.50 = 11,384.80.
    """
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    out, first, second = sell(A, 1000), buy(C, 1000), buy(E, 1000)

    settled = settle(
        previous("1000.00", (A, 1000, "10000.00", "10.00", "1.000"), orders=(out, first, second)),
        S1,
        market(
            bar(A, S1, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00"),
            OPEN_C_S1,
            bar(E, S1, "10.00", "10.00", "10.30", "9.95", "10.20", "11.00", "9.00"),
            adj={A: "1.000", C: "1.000", E: "1.000"},
        ),
        portfolio,
        survivors={C, E},
    )

    assert rows(ledger) == [
        (attempt_id(out, S1), "filled", None),
        (attempt_id(first, S1), "filled", None),
        (attempt_id(second, S1), "rejected", "insufficient cash"),
    ]
    assert settled.book.cash == D("884.80")
    assert settled.book.realized_pnl == D("-10.10")
    assert settled.book.equity == D("11384.80")
    assert settled.filled == (first, out)
    assert live(settled) == [("buy", E, 1)]
    assert cooling(settled) == [(A, 101)]
    assert factors(settled) == {C: D("1.000")}
    assert [item.order.ts_code for item in tonight(settled).pending_orders] == [E]


# ---- the order lifecycle -------------------------------------------------------------------------


def test_a_limit_up_buy_is_refused_carried_and_retried_under_a_new_id_with_its_tries_counted(
    tmp_path: Path,
) -> None:
    """600040.SH opens limit-up on S1 and S2: submitted, refused whole by the paper book, carried
    with one and then two tries, each try under its own id. On S3 it opens at 24.00 and fills:
    12,000.00 + 5.00 + 0.12 out of 50,000.00 -> 37,994.88; at the 24.50 close 50,244.88."""
    order = buy(E, 500, price="20.00")
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")

    first = settle(
        previous("50000.00", orders=(order,)),
        S1,
        market(LOCKED_UP_E[S1], adj={E: "1.000"}),
        portfolio,
        survivors={E},
    )
    assert [item.outcome for item in first.attempts] == ["submitted"]
    assert live(first) == [("buy", E, 1)]
    second = settle(
        tonight(first), S2, market(LOCKED_UP_E[S2], adj={E: "1.000"}), portfolio, survivors={E}
    )
    assert live(second) == [("buy", E, 2)]
    assert [item.attempts for item in tonight(second).pending_orders] == [2]
    third = settle(
        tonight(second),
        S3,
        market(
            bar(E, S3, "24.20", "24.00", "24.80", "23.90", "24.50", "26.62", "21.78"),
            adj={E: "1.000"},
        ),
        portfolio,
        survivors={E},
    )

    reason = "buy cannot fill on a one-price limit-up bar"
    assert rows(ledger) == [
        (attempt_id(order, S1), "rejected", reason),
        (attempt_id(order, S2), "rejected", reason),
        (attempt_id(order, S3), "filled", None),
    ]
    assert len({row[0] for row in rows(ledger)}) == 3
    assert third.filled == (order,)
    assert third.live == ()
    assert (third.book.cash, third.book.equity) == (D("37994.88"), D("50244.88"))


def test_a_buy_unfilled_through_its_last_session_expires_and_cools_down_from_that_session(
    tmp_path: Path,
) -> None:
    """Planned on S0 (100), last session S3 (103), limit-up on all three: expired on S3, and the
    cool-down starts on 103 -- the session it expired on, not the 100 that planned it."""
    order = buy(E, 500, price="20.00")
    _, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    book = previous("50000.00", orders=(order,))
    settled = None
    for day in (S1, S2, S3):
        settled = settle(
            book, day, market(LOCKED_UP_E[day], adj={E: "1.000"}), portfolio, survivors={E}
        )
        if day != S3:
            assert settled.expired == ()
            assert settled.cooldowns == ()
        book = tonight(settled)
    assert settled is not None

    assert settled.expired == (order,)
    assert settled.live == ()
    assert cooling(settled) == [(E, 103)]
    assert book.pending_orders == ()


def test_a_buy_whose_name_is_no_longer_a_survivor_is_cancelled_and_does_not_cool_down(
    tmp_path: Path,
) -> None:
    order = buy(E, 500, price="20.00")
    book = previous("50000.00", orders=(order,))
    quotes = market(LOCKED_UP_E[S1], adj={E: "1.000"})

    dropped = settle(book, S1, quotes, ledger_at(tmp_path / "a.sqlite3")[1], survivors={C})
    kept = settle(book, S1, quotes, ledger_at(tmp_path / "b.sqlite3")[1], survivors={E})

    assert dropped.cancelled == (order,)
    assert (dropped.live, dropped.expired, dropped.cooldowns) == ((), (), ())
    assert tonight(dropped).pending_orders == ()
    assert kept.cancelled == ()
    assert live(kept) == [("buy", E, 1)]


def test_a_sell_never_expires_and_a_sell_of_a_name_with_no_bar_stays_live(tmp_path: Path) -> None:
    """600010.SH is halted all day S1 ... S4 (no row): its sell is not attempted and the ledger
    never sees it, but it is tried -- four tries by S4 (104), past any buy's last session. On S5
    it opens at 9.50 and fills: 9,500.00 less 5.00 + 0.10 + 4.75 -> 1,000.00 + 9,490.15 =
    10,490.15, and the cool-down starts on 105."""
    order = sell(A, 1000)
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    book = previous(
        "1000.00",
        (A, 1000, "10000.00", "10.00", "1.000"),
        (B, 1000, "11500.00", "11.50", "1.000"),
        orders=(order,),
    )
    for tries, day in enumerate((S1, S2, S3, S4), start=1):
        settled = settle(book, day, market(QUIET_B[day], adj={A: "1.000", B: "1.000"}), portfolio)
        assert settled.attempts == (
            AttemptedOrder(
                order=order,
                attempt_id=attempt_id(order, day),
                outcome="not_attempted",
                reason=NO_BAR_REASON,
            ),
        )
        assert live(settled) == [("sell", A, tries)]
        assert marks(settled.book)[A] == D("10.00")  # carried
        book = tonight(settled)
    assert rows(ledger) == []

    settled = settle(
        book,
        S5,
        market(
            bar(A, S5, "10.00", "9.50", "9.80", "9.40", "9.60", "11.00", "9.00"),
            QUIET_B[S5],
            adj={A: "1.000", B: "1.000"},
        ),
        portfolio,
    )

    assert rows(ledger) == [(attempt_id(order, S5), "filled", None)]
    assert settled.filled == (order,)
    assert settled.book.cash == D("10490.15")
    assert cooling(settled) == [(A, 105)]
    assert factors(settled) == {B: D("1.000")}


def test_a_cool_down_starts_on_the_session_the_sale_filled_not_the_one_that_planned_it(
    tmp_path: Path,
) -> None:
    """Planned on S0 (100), limit-down at S1's open (refused), filled at S2's 9.10 open:
    9,100.00 less 5.00 + 0.09 + 4.55 -> 1,000.00 + 9,090.36 = 10,090.36. Cool-down from 102."""
    order = sell(A, 1000)
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    book = previous(
        "1000.00",
        (A, 1000, "10000.00", "10.00", "1.000"),
        (B, 1000, "11500.00", "11.50", "1.000"),
        orders=(order,),
    )
    adj = {A: "1.000", B: "1.000"}

    first = settle(
        book,
        S1,
        market(
            bar(A, S1, "10.00", "9.00", "9.00", "9.00", "9.00", "11.00", "9.00"),
            QUIET_B[S1],
            adj=adj,
        ),
        portfolio,
    )
    assert live(first) == [("sell", A, 1)]
    assert first.cooldowns == ()
    second = settle(
        tonight(first),
        S2,
        market(
            bar(A, S2, "9.00", "9.10", "9.30", "9.05", "9.20", "9.90", "8.10"), QUIET_B[S2], adj=adj
        ),
        portfolio,
    )

    assert rows(ledger) == [
        (attempt_id(order, S1), "rejected", "sell cannot fill on a one-price limit-down bar"),
        (attempt_id(order, S2), "filled", None),
    ]
    assert second.book.cash == D("10090.36")
    assert cooling(second) == [(A, 102)]


def test_an_order_not_due_yet_is_kept_untried_and_handed_to_the_step_once_it_is(
    tmp_path: Path,
) -> None:
    """The snapshot contract allows an order that executes two sessions after it was planned; on
    S1 it is not due, so it is neither handed to the step nor counted as tried."""
    later = TradeOrder(
        signal_session=100,
        execute_session=102,
        expires_session=104,
        side="buy",
        ts_code=C,
        quantity=1000,
        reference_price=D("10.00"),
        reason="INITIAL_BUILD",
    )
    _, portfolio = ledger_at(tmp_path / "paper.sqlite3")

    first = settle(
        previous("50000.00", orders=(later,), settled=False),
        S1,
        Market(bars={}, adj_factor={}),
        portfolio,
        survivors={C},
    )
    assert first.attempts == ()
    assert first.live == (PendingOrder(order=later, attempts=0),)
    second = settle(
        tonight(first),
        S2,
        market(
            bar(C, S2, "10.00", "10.10", "10.20", "10.00", "10.10", "11.00", "9.00"),
            adj={C: "1.000"},
        ),
        portfolio,
        survivors={C},
    )

    assert [item.order for item in second.attempts] == [later]
    assert second.filled == (later,)


# ---- adjustment events ---------------------------------------------------------------------------


def test_after_a_ten_for_ten_bonus_issue_the_book_shows_no_false_drawdown_and_sells_at_value(
    tmp_path: Path,
) -> None:
    """600030.SH is bought on S1 at 20.00 (factor 1.000): 20,000.00 + 6.00 + 0.20 out of
    30,000.00 -> cash 9,993.80, cost 20,006.20, equity at the 20.00 close 29,993.80.

    S2 is the ex-date of a 10-for-10 bonus issue: the real close is 10.10 and the factor 2.000.
    The ratio is 2.000 / 1.000 (the entry factor of the FILL session), so the book marks the
    ledger's 1,000 shares at 20.20 -- equity 30,193.80, not the 20,093.80 an unscaled mark gives.
    That 20.20 is also the reference price of the holding in the ledger's units.

    Sold on S3 at the real open 10.20: the ledger sells its 1,000 shares at 20.40, a notional of
    20,400.00 = the 2,000 real shares x 10.20; fees 6.12 + 0.20 + 10.20 = 16.52; cash 9,993.80 +
    20,383.48 = 30,377.28, realised 20,383.48 - 20,006.20 = 377.28.
    """
    order = buy(C, 1000, price="20.00")
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")

    bought = settle(
        previous("30000.00", orders=(order,)),
        S1,
        market(
            bar(C, S1, "20.00", "20.00", "20.20", "19.90", "20.00", "22.00", "18.00"),
            adj={C: "1.000"},
        ),
        portfolio,
        survivors={C},
    )
    assert bought.book.equity == D("29993.80")
    assert factors(bought) == {C: D("1.000")}
    ex_date = settle(
        tonight(bought),
        S2,
        market(
            bar(C, S2, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00"),
            adj={C: "2.000"},
        ),
        portfolio,
    )
    assert marks(ex_date.book) == {C: D("20.20")}
    assert ex_date.book.equity == D("30193.80")
    assert ex_date.runner_record.equity == D("30193.80")  # the runner saw the scaled bar too
    assert factors(ex_date) == {C: D("1.000")}  # still the fill session's
    assert book_state(ex_date, config=CONFIG).holdings == (Holding(ts_code=C, quantity=1000),)

    out = sell(C, 1000, signal=102, price="20.20")
    sold = settle(
        tonight(ex_date, out),
        S3,
        market(
            bar(C, S3, "10.10", "10.20", "10.40", "10.10", "10.30", "11.11", "9.09"),
            adj={C: "2.000"},
        ),
        portfolio,
    )

    (fill,) = sold.runner_record.transitions
    assert fill.execution is not None
    assert (fill.execution.filled_price, fill.execution.notional) == (D("20.40"), D("20400.00"))
    assert fill.execution.total_cost == D("16.52")
    assert sold.book.cash == D("30377.28")
    assert sold.book.realized_pnl == D("377.28")
    assert sold.entry_factors == ()
    assert cooling(sold) == [(C, 103)]
    assert [row[1] for row in rows(ledger)] == ["filled", "filled"]


def test_a_held_name_with_no_adjustment_factor_refuses_the_session_before_the_ledger_is_touched(
    tmp_path: Path,
) -> None:
    """Every held name needs today's factor -- never 1, never yesterday's -- with a bar or not,
    and so does a buy that has a bar (its entry factor is today's)."""
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    book = previous(
        "20000.00",
        (A, 1000, "10000.00", "10.00", "1.000"),
        (B, 1000, "11500.00", "11.50", "1.000"),
        orders=(buy(C, 1000),),
    )
    quotes = (
        bar(A, S1, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00"),
        QUIET_B[S1],
        OPEN_C_S1,
    )

    with pytest.raises(ValueError, match="adjustment factor") as missing:
        settle(book, S1, market(*quotes, adj={C: "1.000"}), portfolio, survivors={C})
    assert A in str(missing.value) and B in str(missing.value)
    with pytest.raises(ValueError, match=r"adjustment factor.*600010\.SH"):
        settle(book, S1, market(*quotes[1:], adj={B: "1.000", C: "1.000"}), portfolio)
    with pytest.raises(ValueError, match=r"adjustment factor.*600030\.SH"):
        settle(book, S1, market(*quotes, adj={A: "1.000", B: "1.000"}), portfolio, survivors={C})
    assert rows(ledger) == []


# ---- reproducibility and the evening's snapshot --------------------------------------------------


def _sells_and_buys() -> tuple[PaperBookSnapshot, Market]:
    book = previous(
        "1000.00",
        (A, 1000, "10000.00", "10.00", "1.000"),
        orders=(sell(A, 1000), buy(C, 1000), buy(E, 1000)),
    )
    quotes = market(
        bar(A, S1, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00"),
        OPEN_C_S1,
        bar(E, S1, "10.00", "10.00", "10.30", "9.95", "10.20", "11.00", "9.00"),
        adj={A: "1.000", C: "1.000", E: "1.000"},
    )
    return book, quotes


def test_settling_the_same_session_twice_gives_the_same_settlement_and_the_same_bytes(
    tmp_path: Path,
) -> None:
    book, quotes = _sells_and_buys()
    one_ledger, one = ledger_at(tmp_path / "one.sqlite3")
    two_ledger, two = ledger_at(tmp_path / "two.sqlite3")

    first = settle(book, S1, quotes, one, survivors={C, E})
    second = settle(book, S1, quotes, two, survivors={C, E})

    assert first == second
    assert snapshot_payload(tonight(first)) == snapshot_payload(tonight(second))
    assert [row.model_dump_json() for row in one_ledger.list()] == [
        row.model_dump_json() for row in two_ledger.list()
    ]
    # Settled again into the same ledger: the same transitions, each held once.
    again = settle(book, S1, quotes, one, survivors={C, E})
    assert again == first
    assert len(one_ledger.list()) == 3
    assert book == _sells_and_buys()[0]  # the snapshot settled from is not changed


def test_the_evening_snapshot_takes_tonights_orders_and_refuses_a_plan_that_disagrees(
    tmp_path: Path,
) -> None:
    book, quotes = _sells_and_buys()
    settled = settle(book, S1, quotes, ledger_at(tmp_path / "p.sqlite3")[1], survivors={C, E})
    carried = buy(E, 1000)
    new = buy(B, 100, signal=101)

    snapshot = settled.with_plan(TradeList(signal_session=101, orders=(new,), carried=(carried,)))

    assert (snapshot.session, snapshot.session_index, snapshot.last_settled_session) == (
        S1,
        101,
        101,
    )
    assert snapshot.config_digest == survivor_config_digest(CONFIG)
    assert snapshot.book == settled.book
    assert [(item.order, item.attempts) for item in snapshot.pending_orders] == [
        (new, 0),
        (carried, 1),
    ]
    assert snapshot.cooldowns == settled.cooldowns
    assert snapshot.entry_factors == settled.entry_factors
    with pytest.raises(ValueError, match=carried.order_id):
        settled.with_plan(TradeList(signal_session=101, orders=(new,)))
    stray = sell(C, 1000, signal=100)
    with pytest.raises(ValueError, match=stray.order_id):
        settled.with_plan(TradeList(signal_session=101, orders=(), carried=(carried, stray)))
    with pytest.raises(ValueError, match="102"):
        settled.with_plan(TradeList(signal_session=102, orders=(), carried=(carried,)))


def test_a_name_sold_tonight_can_be_planned_again_tonight_when_the_cool_down_is_zero(
    tmp_path: Path,
) -> None:
    """600010.SH sells on S1 (cash 50,000.00 + 9,989.90 = 59,989.90) and cools down from 101;
    with `cooldown_sessions=0` it is free that very evening, so the kernel draws it again: equal
    share 59,989.90 / 10 x 0.995 = 5,968.99 buys 500 at 10.10. The evening's snapshot holds the
    cool-down and the pending buy together."""
    config = SurvivorBookConfig(seed=7, cooldown_sessions=0)
    settled = settle(
        previous(
            "50000.00",
            (A, 1000, "10000.00", "10.00", "1.000"),
            orders=(sell(A, 1000),),
            config=config,
        ),
        S1,
        market(
            bar(A, S1, "10.00", "10.00", "10.20", "9.90", "10.10", "11.00", "9.00"),
            adj={A: "1.000"},
        ),
        ledger_at(tmp_path / "paper.sqlite3")[1],
        config=config,
    )
    state = book_state(settled, config=config)
    assert state == BookState(
        session=101,
        initialized=True,
        equity=D("59989.90"),
        cooldowns=(Cooldown(ts_code=A, since_session=101),),
    )
    screen = ScreenResult(
        verdicts=(EligibilityVerdict(ts_code=A, composite=1.0),),
        survivors=(RankedName(ts_code=A, composite=1.0, rank=1, close=D("10.10")),),
    )
    decision = decide_book(config=config, screen=screen, state=state)
    plan = build_trade_list(
        config=config,
        state=state,
        decision=decision,
        reference_prices={A: D("10.10")},
        cash=settled.book.cash,
        carried=tuple(item.order for item in settled.live),
    )

    snapshot = settled.with_plan(plan)

    assert [(o.side, o.ts_code, o.quantity) for o in plan.orders] == [("buy", A, 500)]
    assert [item.order.ts_code for item in snapshot.pending_orders] == [A]
    assert [(c.ts_code, c.since_session) for c in snapshot.cooldowns] == [(A, 101)]


# ---- the opening book and book_state -------------------------------------------------------------


def test_the_first_evening_after_the_opening_settles_nothing_and_moves_the_book_to_the_session(
    tmp_path: Path,
) -> None:
    """An opening book holds nothing and has no order, so there is no name to ask the market
    about: the session is settled without a step -- cash unchanged, the date moved -- and the
    kernel is told the book is not initialised, so its first decision is the initial build."""
    opening = previous("350000.00", settled=False)
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")

    settled = settle(opening, S1, Market(bars={}, adj_factor={}), portfolio)

    assert settled.book == PortfolioState(as_of=S1, cash=D("350000.00"))
    assert settled.runner_record.before == opening.book
    assert settled.runner_record.after == settled.book
    assert (settled.runner_record.transitions, settled.runner_record.carried_marks) == ((), ())
    assert settled.runner_record.equity == D("350000.00")
    assert settled.attempts == ()
    assert rows(ledger) == []
    assert settled.initialized is False
    assert book_state(settled, config=CONFIG) == BookState(
        session=101, initialized=False, equity=D("350000.00")
    )
    snapshot = tonight(settled, buy(C, 1000, signal=101))
    assert book_state(snapshot, config=CONFIG).initialized is True
    assert book_state(opening, config=CONFIG).initialized is False


def test_book_state_reads_holdings_live_orders_cool_downs_and_the_equity_at_the_close(
    tmp_path: Path,
) -> None:
    book, quotes = _sells_and_buys()
    settled = settle(book, S1, quotes, ledger_at(tmp_path / "p.sqlite3")[1], survivors={C, E})

    assert book_state(settled, config=CONFIG) == BookState(
        session=101,
        initialized=True,
        equity=D("11384.80"),
        holdings=(Holding(ts_code=C, quantity=1000),),
        pending_buys=(E,),
        cooldowns=(Cooldown(ts_code=A, since_session=101),),
    )
    planned = tonight(settled, buy(B, 100, signal=101), sell(C, 1000, signal=101))
    from_snapshot = book_state(planned, config=CONFIG)
    assert (from_snapshot.pending_buys, from_snapshot.pending_sells) == ((B, E), (C,))
    assert book_state(settled, config=CONFIG, equity=D("12000")).equity == D("12000")
    other = SurvivorBookConfig(seed=7, holding_count=9)
    for source in (settled, planned):
        with pytest.raises(ValueError, match="configuration"):
            book_state(source, config=other)


# ---- what a settlement refuses -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"session_index": 102}, "session index"),
        ({"session": S0, "session_index": 101}, "forward"),
        ({"config": SurvivorBookConfig(seed=7, holding_count=9)}, "configuration"),
        ({"observed_on": S0}, "observed"),
    ],
    ids=["a skipped session", "the same date again", "another configuration", "not observed yet"],
)
def test_a_settlement_that_does_not_follow_its_snapshot_is_refused(
    tmp_path: Path, change: dict[str, Any], match: str
) -> None:
    book, quotes = _sells_and_buys()
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    arguments: dict[str, Any] = {
        "previous": book,
        "session": S1,
        "session_index": 101,
        "market": quotes,
        "survivors": {C, E},
        "delisted": (),
        "portfolio": portfolio,
        "config": CONFIG,
        "observed_on": S1,
    }

    with pytest.raises(ValueError, match=match):
        settle_session(**{**arguments, **change})
    assert rows(ledger) == []


# ---- the delisting write-off (D13) ---------------------------------------------------------------


def _delisted_book(*orders: TradeOrder) -> PaperBookSnapshot:
    """S0: cash 5,000.00; 600010.SH 1,000 (cost 10,005.00, last marked 6.00) -- the kernel is
    selling it -- and 600020.SH 1,000 (cost 11,500.00, marked 11.50)."""
    return previous(
        "5000.00",
        (A, 1000, "10005.00", "6.00", "1.000"),
        (B, 1000, "11500.00", "11.50", "1.000"),
        orders=orders,
    )


def test_a_holding_delisted_with_no_bar_is_written_off_before_the_book_is_decided(
    tmp_path: Path,
) -> None:
    """600010.SH is delisted on S1 and has no bar (and no factor). It is written off: removed
    with no price and nothing recovered -- cash unchanged at 5,000.00, no fees -- for a realised
    loss of its whole cost, -10,005.00. The labelled transition is the only ledger row: its live
    sell is withdrawn, never handed to the step. Before: 5,000.00 + 6,000.00 + 11,600.00 =
    22,600.00; after: 5,000.00 + 600020.SH at its 11.60 close = 16,600.00, a fall of exactly the
    6,000.00 the holding was marked at."""
    out = sell(A, 1000, reason="EXIT_ELIGIBILITY_LOST")
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    quotes = market(
        bar(B, S1, "11.50", "11.50", "11.70", "11.40", "11.60", "12.65", "10.35"), adj={B: "1.000"}
    )

    settled = settle(_delisted_book(out), S1, quotes, portfolio, delisted={A})

    (written,) = settled.written_off
    assert isinstance(written, WriteOff)
    assert (written.ts_code, written.withdrawn) == (A, (out,))
    transition = written.transition
    assert WRITE_OFF_REASON == "delisting_write_off"
    assert write_off_id(A, S1) == "delisting_write_off:600010.SH@2021-03-02"
    assert (transition.status, transition.reason) == ("filled", "delisting_write_off")
    assert (transition.order.order_id, transition.order.side, transition.order.quantity) == (
        write_off_id(A, S1),
        "sell",
        1000,
    )
    assert transition.execution is None  # no price, no notional, no fee: nothing traded
    assert transition.realized_pnl_delta == D("-10005.00")
    assert transition.before.equity == D("22600.00")
    assert transition.after.cash == transition.before.cash == D("5000.00")
    assert transition.after.fees_paid == transition.before.fees_paid
    assert transition.before.equity - transition.after.equity == D("6000.00")
    assert transition.before.as_of == transition.after.as_of == S1
    assert rows(ledger) == [(write_off_id(A, S1), "filled", "delisting_write_off")]
    assert settled.attempts == ()
    assert settled.book.cash == D("5000.00")
    assert [p.subject for p in settled.book.positions] == [B]
    assert marks(settled.book) == {B: D("11.60")}
    assert settled.book.equity == D("16600.00")
    assert (settled.book.realized_pnl, settled.book.fees_paid) == (D("-10005.00"), D("0.00"))
    (stored,) = ledger.list()
    assert stored == transition  # a price-less filled transition reads back as written
    assert (settled.live, settled.filled, settled.expired, settled.cancelled) == ((), (), (), ())
    assert settled.cooldowns == ()
    assert factors(settled) == {B: D("1.000")}
    assert book_state(settled, config=CONFIG).holdings == (Holding(ts_code=B, quantity=1000),)
    assert tonight(settled).pending_orders == ()

    # Control: unreported, the delisted holding is a held name with no factor, and the evening
    # stops by name before anything is written.
    control_ledger, control = ledger_at(tmp_path / "control.sqlite3")
    with pytest.raises(ValueError, match=r"adjustment factor.*600010\.SH"):
        settle(_delisted_book(out), S1, quotes, control)
    assert rows(control_ledger) == []


def test_a_holding_reported_delisted_that_still_has_a_bar_is_traded_not_written_off(
    tmp_path: Path,
) -> None:
    """With a bar the name has a price: its sell is submitted (here refused at a one-price
    limit-down open) and the holding is marked at its 5.40 close."""
    out = sell(A, 1000, reason="EXIT_ELIGIBILITY_LOST")
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")

    settled = settle(
        _delisted_book(out),
        S1,
        market(
            bar(A, S1, "6.00", "5.40", "5.40", "5.40", "5.40", "6.60", "5.40"),
            QUIET_B[S1],
            adj={A: "1.000", B: "1.000"},
        ),
        portfolio,
        delisted={A},
    )

    assert settled.written_off == ()
    assert rows(ledger) == [
        (attempt_id(out, S1), "rejected", "sell cannot fill on a one-price limit-down bar")
    ]
    assert live(settled) == [("sell", A, 1)]
    assert marks(settled.book)[A] == D("5.40")


def test_a_code_reported_delisted_that_the_book_does_not_hold_is_refused_by_name(
    tmp_path: Path,
) -> None:
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    with pytest.raises(ValueError, match=r"600099\.SH"):
        settle(
            _delisted_book(),
            S1,
            market(QUIET_B[S1], adj={A: "1.000", B: "1.000"}),
            portfolio,
            delisted={"600099.SH"},
        )
    assert rows(ledger) == []


def test_a_write_off_with_nothing_else_to_settle_records_what_the_runner_would_have(
    tmp_path: Path,
) -> None:
    """The book's only holding is delisted, so no name has a bar and there is no step to run. The
    session's record is what `PaperPortfolio.advance` gives for a step in which the holding has
    no bar (it is carried at 6.00); then the write-off. Settled again into the same ledger, the
    write-off is the same transition and is held once."""
    book = previous("5000.00", (A, 1000, "10005.00", "6.00", "1.000"))
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    nothing = Market(bars={}, adj_factor={})

    settled = settle(book, S1, nothing, portfolio, delisted={A})

    unrelated = bar("600099.SH", S1, "5.00", "5.00", "5.00", "5.00", "5.00", "5.50", "4.50")
    runner = ledger_at(tmp_path / "runner.sqlite3")[1].advance(
        state=book.book,
        session=PortfolioBacktestStep(trade_date=S1, bars=(unrelated,), benchmark_close=BENCHMARK),
        observed_on=S1,
    )
    assert settled.runner_record == runner
    assert [
        (m.subject, m.price, m.sessions_carried) for m in settled.runner_record.carried_marks
    ] == [(A, D("6.00"), 1)]
    assert settled.book == PortfolioState(as_of=S1, cash=D("5000.00"), realized_pnl=D("-10005.00"))
    assert rows(ledger) == [(write_off_id(A, S1), "filled", "delisting_write_off")]
    assert settle(book, S1, nothing, portfolio, delisted={A}) == settled
    assert len(ledger.list()) == 1


# ---- the session's record ------------------------------------------------------------------------


def test_the_sessions_equity_is_after_the_close_and_after_the_write_offs_the_runner_record_before(
    tmp_path: Path,
) -> None:
    """600010.SH is delisted with no bar and 600020.SH closes at 11.50. The runner's record is
    taken before the write-off: it still holds 600010.SH, carried at 6.00, and reads 5,000.00 +
    6,000.00 + 11,500.00 = 22,500.00, gross exposure 17,500 / 22,500 = 0.777778. The session's
    own figures come after it: 5,000.00 + 11,500.00 = 16,500.00, exposure 11,500 / 16,500 =
    0.696970, and no carried mark for a name the book no longer holds. A daily log reading the
    runner's record would show the loss a session late."""
    _, portfolio = ledger_at(tmp_path / "paper.sqlite3")

    settled = settle(
        _delisted_book(), S1, market(QUIET_B[S1], adj={B: "1.000"}), portfolio, delisted={A}
    )

    record = settled.runner_record
    assert record.equity == D("22500.00")
    assert record.gross_exposure == D("0.777778")
    assert [p.subject for p in record.after.positions] == [A, B]
    assert [(m.subject, m.price, m.sessions_carried) for m in record.carried_marks] == [
        (A, D("6.00"), 1)
    ]
    assert settled.equity == settled.book.equity == D("16500.00")
    assert settled.gross_exposure == D("0.696970")
    assert settled.carried_marks == ()
    assert settled.transitions == (settled.written_off[0].transition,)


def test_the_sessions_transitions_are_the_runners_then_the_write_offs_in_ledger_order(
    tmp_path: Path,
) -> None:
    """A buy fills and a holding is written off on the same session. The runner's rows reach the
    ledger first (in `advance`), the write-off after the close; `Settlement.transitions` is that
    order, which is the order a caller appends the evening in. 600030.SH 100 at 10.10: 1,010.00 +
    5.00 + 0.01 -> cash 3,984.99. Runner: 3,984.99 + 6,000.00 + 11,500.00 + 1,010.00 =
    22,494.99; the session: 3,984.99 + 11,500.00 + 100 x 10.50 = 16,534.99."""
    order = buy(C, 100)
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")

    settled = settle(
        _delisted_book(order),
        S1,
        market(QUIET_B[S1], OPEN_C_S1, adj={B: "1.000", C: "1.000"}),
        portfolio,
        survivors={C},
        delisted={A},
    )

    assert [t.order.order_id for t in settled.transitions] == [
        attempt_id(order, S1),
        write_off_id(A, S1),
    ]
    assert settled.transitions == (
        *settled.runner_record.transitions,
        settled.written_off[0].transition,
    )
    assert list(ledger.list()) == list(settled.transitions)
    assert settled.runner_record.equity == D("22494.99")
    assert settled.equity == D("16534.99")
    assert tonight(settled).book == settled.book


# ---- a session on which nothing has a bar ------------------------------------------------------


def _two_holdings(*orders: TradeOrder) -> PaperBookSnapshot:
    """S0: cash 1,000.00; 600010.SH 1,000 (cost 10,000.00, marked 10.00) and 600020.SH 1,000
    (cost 11,500.00, marked 11.50): 22,500.00."""
    return previous(
        "1000.00",
        (A, 1000, "10000.00", "10.00", "1.000"),
        (B, 1000, "11500.00", "11.50", "1.000"),
        orders=orders,
    )


def test_a_book_whose_every_holding_is_halted_settles_without_a_step(tmp_path: Path) -> None:
    """P2a: both holdings halted all day (no daily row) and nothing due. There is no bar to build
    a step from; the session still settles -- the book moved to S1, both holdings carried."""
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")

    settled = settle(_two_holdings(), S1, Market(bars={}, adj_factor={A: D(1), B: D(1)}), portfolio)

    assert settled.book.as_of == S1
    assert (settled.book.cash, settled.equity) == (D("1000.00"), D("22500.00"))
    assert marks(settled.book) == {A: D("10.00"), B: D("11.50")}
    assert [(m.subject, m.price) for m in settled.carried_marks] == [
        (A, D("10.00")),
        (B, D("11.50")),
    ]
    assert (settled.attempts, settled.transitions) == ((), ())
    assert rows(ledger) == []
    assert tonight(settled).session == S1


def test_a_delisted_holding_beside_a_halted_one_is_still_written_off(tmp_path: Path) -> None:
    """P2b: 600010.SH is delisted and 600020.SH halted; neither has a bar. The write-off is
    reached: 600010.SH goes for nothing (cash 5,000.00, realised -10,005.00) and 600020.SH is
    carried at 11.50 -- 16,500.00."""
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")

    settled = settle(
        _delisted_book(),
        S1,
        Market(bars={}, adj_factor={B: D(1)}),
        portfolio,
        delisted={A},
    )

    assert [item.ts_code for item in settled.written_off] == [A]
    assert rows(ledger) == [(write_off_id(A, S1), "filled", "delisting_write_off")]
    assert [(m.subject, m.price) for m in settled.carried_marks] == [(B, D("11.50"))]
    assert settled.equity == D("16500.00")
    assert settled.book.realized_pnl == D("-10005.00")
    assert tonight(settled).entry_factors == (EntryFactor(ts_code=B, entry_adj=D("1.000")),)


def test_a_due_sell_of_a_halted_holding_is_not_attempted_and_stays_live_with_its_tries(
    tmp_path: Path,
) -> None:
    """P2c: the only due order is a sell of 600010.SH, halted with the rest of the book. It is
    recorded as not attempted, under its attempt id, with the step builder's reason; it stays live
    with one more try on each such session; the ledger never sees it."""
    out = sell(A, 1000)
    ledger, portfolio = ledger_at(tmp_path / "paper.sqlite3")
    nothing = Market(bars={}, adj_factor={A: D(1), B: D(1)})

    first = settle(_two_holdings(out), S1, nothing, portfolio)
    second = settle(tonight(first), S2, nothing, portfolio)

    assert first.attempts == (
        AttemptedOrder(
            order=out, attempt_id=attempt_id(out, S1), outcome="not_attempted", reason=NO_BAR_REASON
        ),
    )
    assert [item.attempt_id for item in second.attempts] == [attempt_id(out, S2)]
    assert (live(first), live(second)) == ([("sell", A, 1)], [("sell", A, 2)])
    assert [item.attempts for item in tonight(second).pending_orders] == [2]
    assert rows(ledger) == []
