from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.backtest.execution import MarketBar
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
