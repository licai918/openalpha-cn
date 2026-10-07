"""What a paper session records is readable on the shipped faces (`V2-P5-004`).

`PaperPortfolio` has no dedicated command or route of its own yet -- adding one means editing
`cli.py`, `sdk.py` or `api/app.py`, all three of which a sibling agent held while this row was
built, so the exact edit is reported rather than made. What *is* proved here is that a paper
book is wired into the product's real storage rather than into a fixture: it is handed
`OpenAlphaSDK.portfolio_ledger` itself, and what it writes comes back out of
`OpenAlphaSDK.list_portfolio_transitions()` and `GET /api/v1/portfolio/ledger` -- the two shipped
read faces for exactly this table -- with no bridging code in between.
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.multi_day import PortfolioBacktestStep
from openalpha_cn.backtest.paper import (
    PAPER_EXECUTION_VENUE,
    PaperPortfolio,
    PaperPortfolioReachedOutward,
)
from openalpha_cn.backtest.portfolio import PortfolioOrder, PortfolioState
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger

OPENING = PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("100000.00"))


def flat(subject: str, trade_date: date, close: str) -> MarketBar:
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


def two_name_session(trade_date: date, *, suffix: str) -> PortfolioBacktestStep:
    return PortfolioBacktestStep(
        trade_date=trade_date,
        bars=(flat("000001.SZ", trade_date, "10.00"), flat("600000.SH", trade_date, "10.00")),
        orders=(
            PortfolioOrder(
                order_id=f"paper-a-{suffix}", subject="000001.SZ", side="buy", quantity=1000
            ),
            PortfolioOrder(
                order_id=f"paper-b-{suffix}", subject="600000.SH", side="buy", quantity=1000
            ),
        ),
        benchmark_close=Decimal("100"),
    )


def test_a_paper_session_lands_in_the_sdks_own_ledger_and_reads_back_on_both_faces(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    """The paper book writes into `OpenAlphaSDK.portfolio_ledger`, unwrapped.

    No adapter, no copy: the object the SDK hands its own `execute_portfolio_order` is the object
    the paper book appends to, which is what makes the read-back on the SDK's listing and on the
    REST route a statement about the product rather than about a fixture.
    """
    runtime_dir = tmp_path / "runtime"
    sdk = OpenAlphaSDK(runtime_dir=runtime_dir)
    book = PaperPortfolio(ledger=sdk.portfolio_ledger)

    result = book.advance(
        state=OPENING,
        session=two_name_session(date(2026, 7, 24), suffix="1"),
        observed_on=date(2026, 7, 24),
    )

    assert result.execution_venue == PAPER_EXECUTION_VENUE
    assert [transition.order.order_id for transition in sdk.list_portfolio_transitions()] == [
        "paper-a-1",
        "paper-b-1",
    ]
    assert len(sdk.list_portfolio_transitions(subject="000001.SZ")) == 1

    client = TestClient(create_app(runtime_dir=runtime_dir, clock=lambda: plain_frozen_now))
    served = client.get("/api/v1/portfolio/ledger").json()

    assert [row["order"]["order_id"] for row in served] == ["paper-a-1", "paper-b-1"]
    assert {row["status"] for row in served} == {"filled"}
    assert served[0]["after"]["as_of"] == "2026-07-24"


def test_a_paper_book_running_over_the_shipped_ledger_still_refuses_to_reach_outward(
    tmp_path: Path,
) -> None:
    """The guard is live over the real store, not only over a stub.

    `SQLitePortfolioLedger` opens a database inside the guarded block, which is precisely the
    kind of legitimate I/O a ban this wide could have broken. It does not: `sqlite3.connect` is
    not on the list, and a session over it completes. The second half then shows the refusal is
    still armed on that same thread, one statement later.
    """
    ledger = SQLitePortfolioLedger(tmp_path / "paper.sqlite3")
    book = PaperPortfolio(ledger=ledger)

    book.advance(
        state=OPENING,
        session=two_name_session(date(2026, 7, 24), suffix="1"),
        observed_on=date(2026, 7, 24),
    )
    assert len(ledger.list()) == 2

    class Outward:
        def append(self, transition: object) -> None:
            ledger.append(transition)  # type: ignore[arg-type]
            __import__("socket").socket()

    with pytest.raises(PaperPortfolioReachedOutward):
        PaperPortfolio(ledger=Outward()).advance(  # type: ignore[arg-type]
            state=OPENING,
            session=two_name_session(date(2026, 7, 27), suffix="2"),
            observed_on=date(2026, 7, 27),
        )
