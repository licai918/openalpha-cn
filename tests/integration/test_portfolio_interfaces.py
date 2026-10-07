from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.multi_day import PortfolioBacktestStep
from openalpha_cn.backtest.portfolio import (
    PortfolioOrder,
    PortfolioState,
)
from openalpha_cn.sdk import OpenAlphaSDK


def test_sdk_and_api_share_the_same_portfolio_execution_contract(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    state = PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("20000.00"))
    order = PortfolioOrder(
        order_id="portfolio-interface-buy",
        subject="000001.SZ",
        side="buy",
        quantity=100,
    )
    market = MarketBar(
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
    )
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "sdk")
    sdk_result = sdk.execute_portfolio_order(state=state, order=order, market=market)
    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: plain_frozen_now))

    response = client.post(
        "/api/v1/portfolio/execute",
        json={
            "state": state.model_dump(mode="json", exclude_computed_fields=True),
            "order": order.model_dump(mode="json"),
            "market": market.model_dump(mode="json"),
        },
    )

    assert response.status_code == 200
    assert response.json() == sdk_result.model_dump(mode="json")
    assert response.json()["status"] == "filled"
    assert response.json()["after"]["positions"][0]["quantity"] == 100
    assert len(client.get("/api/v1/portfolio/ledger").json()) == 1
    assert len(sdk.list_portfolio_transitions()) == 1


def _flat(subject: str, trade_date: date, close: str) -> MarketBar:
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


def _two_name_sessions() -> tuple[PortfolioBacktestStep, ...]:
    """One session that buys two names, then one that trades nothing while both move.

    The second session is the shape `V2-P5-003` exists to make representable: no orders at all,
    and the only reason the runner learns 600000.SH halved is that a session carries bars in its
    own right rather than hanging each one off an order.
    """
    first, second = date(2026, 7, 24), date(2026, 7, 27)
    return (
        PortfolioBacktestStep(
            trade_date=first,
            bars=(_flat("000001.SZ", first, "10.00"), _flat("600000.SH", first, "10.00")),
            orders=(
                PortfolioOrder(order_id="face-a", subject="000001.SZ", side="buy", quantity=1000),
                PortfolioOrder(order_id="face-b", subject="600000.SH", side="buy", quantity=1000),
            ),
            benchmark_close=Decimal("100"),
        ),
        PortfolioBacktestStep(
            trade_date=second,
            bars=(_flat("000001.SZ", second, "12.00"), _flat("600000.SH", second, "5.00")),
            orders=(),
            benchmark_close=Decimal("101"),
        ),
    )


def test_sdk_and_api_run_the_same_multi_name_portfolio_backtest(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    """`V2-P5-003` through the two faces that already carry it, and they carried nothing.

    `POST /api/v1/backtests/portfolio` and `OpenAlphaSDK.run_portfolio_backtest` were both
    shipped and both **untested** -- the seam audit's `F38` lists this route among the 22 nothing
    consumes, and a grep of `tests/` before this test found no caller of either. So the session
    shape reaches both faces for free (each is a pass-through of `PortfolioBacktestStep`, and
    neither `sdk.py` nor `api/app.py` needed a byte changed for this row), and this is the test
    that makes "for free" a measurement rather than a claim.
    """
    initial = PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("100000.00"))
    steps = _two_name_sessions()
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "sdk")
    sdk_report = sdk.run_portfolio_backtest(initial=initial, steps=steps)
    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: plain_frozen_now))

    response = client.post(
        "/api/v1/backtests/portfolio",
        json={
            "initial": initial.model_dump(mode="json", exclude_computed_fields=True),
            "steps": [step.model_dump(mode="json") for step in steps],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body == sdk_report.model_dump(mode="json")
    assert [point["trade_date"] for point in body["equity_curve"]] == ["2026-07-24", "2026-07-27"]
    assert len(body["transitions"]) == 2
    assert body["final_state"]["market_value"] == "17000.00"
    assert len(client.get("/api/v1/portfolio/ledger").json()) == 2
    assert len(sdk.list_portfolio_transitions()) == 2


def test_a_session_that_serves_no_bar_for_a_held_name_says_so_over_http(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    """The carry disclosure is a field of the shipped response, not a log line.

    A halted A-share serves no daily row, so the runner keeps the mark it has. What it must not
    do is keep it quietly: `carried_marks` names the session, the price it held, and how many
    consecutive sessions it has now held it.
    """
    second = date(2026, 7, 27)
    initial = PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("100000.00"))
    steps = (
        _two_name_sessions()[0],
        PortfolioBacktestStep(
            trade_date=second,
            bars=(_flat("000001.SZ", second, "12.00"),),
            orders=(),
            benchmark_close=Decimal("101"),
        ),
    )
    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: plain_frozen_now))

    response = client.post(
        "/api/v1/backtests/portfolio",
        json={
            "initial": initial.model_dump(mode="json", exclude_computed_fields=True),
            "steps": [step.model_dump(mode="json") for step in steps],
        },
    )

    assert response.status_code == 200
    assert response.json()["carried_marks"] == [
        {
            "trade_date": "2026-07-27",
            "subject": "600000.SH",
            "price": "10.00",
            "sessions_carried": 1,
        }
    ]


def test_an_order_without_a_bar_on_its_session_is_refused_by_the_route_not_by_the_simulator(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    """A malformed session is a `422`, not a report full of `rejected` transitions.

    On the single-subject step the equivalent input -- an order and a bar disagreeing about the
    subject -- reached `PortfolioSimulator.execute_order` and came back as a *risk verdict*
    (`order and market mismatch`) inside a `200`. A caller could not tell that from a book that
    really had breached a cap.
    """
    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: plain_frozen_now))
    initial = PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("100000.00"))

    response = client.post(
        "/api/v1/backtests/portfolio",
        json={
            "initial": initial.model_dump(mode="json", exclude_computed_fields=True),
            "steps": [
                {
                    "trade_date": "2026-07-24",
                    "bars": [
                        _flat("000001.SZ", date(2026, 7, 24), "10.00").model_dump(mode="json")
                    ],
                    "orders": [
                        {
                            "order_id": "face-x",
                            "subject": "600000.SH",
                            "side": "buy",
                            "quantity": 100,
                        }
                    ],
                    "benchmark_close": "100",
                }
            ],
        },
    )

    assert response.status_code == 422
    assert "no bar on this session" in response.text
