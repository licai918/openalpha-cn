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


def test_the_availability_filter_is_the_visibility_predicate_because_nothing_here_is_revised() -> (
    None
):
    """Why this adapter filters on `available_time` rather than calling `is_visible_at`, and what
    keeps the two the same filter.

    `_decode` drops a bar whose availability is after `as_of` *before* it builds the record's
    `Timeline`. Building it first, to hand it to `is_visible_at`, would turn a bar dated after
    the fetch's own clock into an error -- `Timeline` refuses an `ingested_time` before its
    `available_time` -- where today it is dropped and the fetch answers `no_data`. The two
    filters agree only because every record this adapter stamps carries
    `revision_time == available_time`, so `is_visible_at` reduces to the availability test.
    Both halves are pinned: the equality, and the early fetch that must stay `no_data`.
    """
    after_the_close = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
    before_the_close = datetime(2026, 7, 24, 6, 0, tzinfo=UTC)
    request = ProviderRequest(
        dataset="stock_zh_a_hist", as_of=after_the_close, subjects=("000001.SZ",)
    )

    batch = AKShareProvider(client=FakeAKShare(), clock=lambda: after_the_close).fetch(request)
    early = AKShareProvider(client=FakeAKShare(), clock=lambda: before_the_close).fetch(
        request.model_copy(update={"as_of": before_the_close})
    )

    (record,) = batch.records
    assert record.timeline.revision_time == record.timeline.available_time
    assert early.status == "no_data"


def test_akshare_metadata_declares_supported_datasets() -> None:
    provider = AKShareProvider(client=FakeAKShare())

    assert provider.metadata.supported_datasets == ("stock_zh_a_hist",)


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
