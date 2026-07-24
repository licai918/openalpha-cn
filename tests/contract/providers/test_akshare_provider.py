from datetime import UTC, datetime
from typing import Any

import pytest

from openalpha_cn.providers.akshare import AKShareProvider
from openalpha_cn.providers.base import ProviderFailure, ProviderRequest


class FakeFrame:
    empty = False

    def to_dict(self, *, orient: str) -> list[dict[str, Any]]:
        assert orient == "records"
        return [{"日期": "2026-07-24", "收盘": 10.5, "涨跌幅": 9.99}]


class FakeAKShare:
    def __init__(self) -> None:
        self.arguments: dict[str, str] | None = None

    def stock_zh_a_hist(self, **kwargs: str) -> FakeFrame:
        self.arguments = kwargs
        return FakeFrame()


def test_akshare_research_adapter_is_allowlisted_and_point_in_time() -> None:
    client = FakeAKShare()
    provider = AKShareProvider(
        client=client,
        clock=lambda: datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
    )

    batch = provider.fetch(
        ProviderRequest(
            dataset="stock_zh_a_hist",
            as_of=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
            subjects=("000001.SZ",),
        )
    )

    assert batch.status == "success"
    assert batch.records[0].subject == "000001.SZ"
    assert batch.records[0].payload["收盘"] == 10.5
    assert client.arguments == {
        "symbol": "000001",
        "period": "daily",
        "start_date": "20260724",
        "end_date": "20260724",
        "adjust": "",
    }


def test_akshare_rejects_non_allowlisted_datasets() -> None:
    provider = AKShareProvider(client=FakeAKShare())

    with pytest.raises(ProviderFailure) as captured:
        provider.fetch(
            ProviderRequest(
                dataset="arbitrary_function",
                as_of=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
                subjects=("000001.SZ",),
            )
        )

    assert captured.value.category == "configuration"
    assert captured.value.retryable is False
