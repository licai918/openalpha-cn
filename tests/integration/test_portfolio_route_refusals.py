"""The two portfolio-writing routes refuse a caller's mistake instead of crashing (`V2-P5-013`).

`POST /api/v1/backtests/portfolio` and `POST /api/v1/portfolio/execute` both append to
`SQLitePortfolioLedger`, and that store raises a plain `ValueError` when an `order_id` is reused
with different content (`storage/portfolio.py:44`, "portfolio order_id conflicts"). Neither route
caught it, so the answer was `500` with `content-type: text/plain` and the body `Internal Server
Error` -- which tells the caller nothing, and tells an operator that this repository has a defect
when in fact the request was bad.

**Measured before it was believed, and the measurement moved the diagnosis.** The coordinator
reported this as a regression `V2-P5-003` introduced with a new strictly-ascending-session check.
Driven on `2746663` -- before any of that row exists in this worktree -- the `500` is already
there, reached by resubmitting a backtest whose orders keep their ids. `V2-P5-003` adds a third
road to a fault that already had one; it did not build the road.

**Why nothing had noticed**: `grep -rn "backtests/portfolio" tests/` returned exactly one line
before this file, and it was `tests/unit/test_surface_parity.py`'s row in the route table. The
route had a library-level test of the runner (`test_portfolio_ledger_backtest.py`) and no test at
all of the *route*, which is the shape this repository has been caught by four times: a green
unit test beside no green product path.

`_reject` versus `raise` is the distinction that makes this a small fix rather than a policy
change. `PortfolioSimulator` **returns** a rejection for every market fact it disagrees with -- a
suspended bar, a limit-locked price, a subject mismatch -- and those are `200` answers carrying a
`reason`, correctly. The ledger's conflict is different in kind: it is a fault in the *request*,
not a fact about the market, and it is the only `raise` on either path.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.multi_day import PortfolioBacktestStep
from openalpha_cn.backtest.portfolio import PortfolioOrder
from openalpha_cn.sdk import OpenAlphaSDK


def _bar(trade_date: date, close: str = "10.00") -> MarketBar:
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


def _order(order_id: str, *, quantity: int = 100) -> PortfolioOrder:
    return PortfolioOrder(order_id=order_id, subject="000001.SZ", side="buy", quantity=quantity)


def _state() -> dict[str, Any]:
    """The opening book as a request body.

    Hand-built rather than `model_dump`ed, because `PortfolioState` publishes `equity` and
    `market_value` as computed fields and the request model is `extra="forbid"` -- a dump
    round-trips into a `422` about the two keys the model derives for itself.
    """
    return {"as_of": "2026-07-23", "cash": "20000", "positions": []}


def _backtest_body(*, quantity: int) -> dict[str, Any]:
    """One step, and `quantity` is the only thing that varies between the two submissions.

    The order id stays `"reused"` on purpose and the quantity changes, because the ledger is
    *idempotent* for a byte-identical transition and only refuses a genuinely conflicting one --
    so a body repeated verbatim is a `200` both times and would prove nothing.
    """
    return {
        "initial": _state(),
        "steps": [
            PortfolioBacktestStep(
                trade_date=date(2026, 7, 24),
                bars=(_bar(date(2026, 7, 24)),),
                orders=(_order("reused", quantity=quantity),),
                benchmark_close=Decimal("100"),
            ).model_dump(mode="json")
        ],
    }


def _execute_body(*, quantity: int) -> dict[str, Any]:
    return {
        "state": _state(),
        "order": _order("reused-execute", quantity=quantity).model_dump(mode="json"),
        "market": _bar(date(2026, 7, 24)).model_dump(mode="json"),
    }


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(runtime_dir=tmp_path / "runtime")) as rest:
        yield rest


def test_a_reused_order_id_is_refused_by_the_backtest_route_rather_than_crashing_it(
    client: TestClient,
) -> None:
    """The `500` becomes a `422` that names the offending `order_id`.

    Three assertions, because three different wrong answers were available:

    1. **Not a `500`.** A `text/plain` `Internal Server Error` says "file a bug" for a request
       the caller can fix by changing one field.
    2. **The offending id is echoed.** A `422` reading only "conflict" would satisfy point 1 and
       leave a caller with a hundred-step backtest no way to find which step collided.
    3. **The first submission still succeeded**, so this is a refusal of the *second* request and
       not a route that has stopped working. Without it, a build that rejected every backtest
       would pass points 1 and 2.
    """
    first = client.post("/api/v1/backtests/portfolio", json=_backtest_body(quantity=100))
    second = client.post("/api/v1/backtests/portfolio", json=_backtest_body(quantity=200))

    assert first.status_code == 200, first.text
    assert second.status_code == 422, second.text
    assert second.headers["content-type"].startswith("application/json")
    assert "reused" in second.text


def test_a_reused_order_id_is_refused_by_the_execute_route_in_the_same_words(
    client: TestClient,
) -> None:
    """The sibling route, and the same fault, so the two cannot answer it two ways.

    `POST /api/v1/portfolio/execute` appends to the same ledger through a different code path --
    one order rather than a series -- and had the same uncaught `raise`. Asserted **equal** to
    the backtest route's message, `V2-P4-101`'s standard: one fault, one sentence, whichever
    door the caller came through.
    """
    client.post("/api/v1/portfolio/execute", json=_execute_body(quantity=100))
    execute = client.post("/api/v1/portfolio/execute", json=_execute_body(quantity=200))
    backtest = client.post("/api/v1/backtests/portfolio", json=_backtest_body(quantity=100))
    backtest = client.post("/api/v1/backtests/portfolio", json=_backtest_body(quantity=200))

    assert execute.status_code == 422, execute.text
    assert backtest.status_code == 422, backtest.text
    assert isinstance(execute.json()["detail"], str), execute.text
    assert (
        execute.json()["detail"].replace("reused-execute", "reused") == (backtest.json()["detail"])
    ), "one fault reached through two routes must not grow two sentences"


def test_the_refusal_leaves_the_ledger_holding_only_what_it_accepted(
    client: TestClient, tmp_path: Path
) -> None:
    """A refused write is a write that did not happen, read back through a second face.

    The `422` is asserted from the API and the ledger is read through `OpenAlphaSDK` over the
    same runtime directory, because a route that answered `422` *after* writing would satisfy
    every assertion above -- and the ledger is append-only, so a spurious row is permanent.
    """
    client.post("/api/v1/backtests/portfolio", json=_backtest_body(quantity=100))
    refused = client.post("/api/v1/backtests/portfolio", json=_backtest_body(quantity=200))

    held = OpenAlphaSDK(runtime_dir=tmp_path / "runtime").list_portfolio_transitions()

    assert refused.status_code == 422, refused.text
    assert [transition.order.order_id for transition in held] == ["reused"]
    assert held[0].order.quantity == 100, "the refused submission overwrote the accepted one"


def test_a_market_fact_the_simulator_disagrees_with_is_still_a_200_with_a_reason(
    client: TestClient,
) -> None:
    """The control, and it is what stops the fix above from becoming a policy change.

    `PortfolioSimulator` **returns** a rejection for a fact about the market -- here a sell of
    stock the book does not hold -- and that is a `200` carrying `status: "rejected"` and a
    `reason`. It is not an error and must not become one: a route that mapped every unhappy
    answer to `422` would lose the difference between "the market would not take this order" and
    "this request cannot be put at all", which is the distinction `SHORTLIST_HTTP_STATUS` spends
    a page on one plane over.

    It is also what stops the `except ValueError` above from being widened by a later edit: a
    rejection is still recorded in the ledger, so this request writes a row and the one above
    writes none.
    """
    body = _execute_body(quantity=100)
    body["order"] = PortfolioOrder(
        order_id="sell-what-we-do-not-hold", subject="000001.SZ", side="sell", quantity=100
    ).model_dump(mode="json")

    response = client.post("/api/v1/portfolio/execute", json=body)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"
    assert response.json()["reason"]
