from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.product.research import (
    ResearchReportFactory,
    ResearchScreener,
    ScreeningCriteria,
    WatchlistEntry,
)
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.storage.product import SQLiteReportStore, SQLiteWatchlistStore


@pytest.fixture
def research_request(frozen_now: datetime):
    def _make() -> ResearchRunRequest:
        item = EvidenceSnapshot(
            subject="000001.SZ",
            kind="limit_up",
            timeline=Timeline(
                event_time=frozen_now,
                available_time=frozen_now,
                ingested_time=frozen_now,
                revision_time=frozen_now,
            ),
            source_id="product.fixture",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary="Product fixture.",
            payload={
                "schema": "a-share-evidence/v1",
                "family": "market_event",
                "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
                "quality_flags": [],
            },
        )
        return ResearchRunRequest(
            run_id="product-run",
            mode="replay",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(item,),
            code_commit="0123456789abcdef",
            config_digest="b" * 64,
            random_seed=7,
        )

    return _make


def test_screening_watchlist_and_report_center_are_durable(
    tmp_path: Path, research_request, frozen_now: datetime
) -> None:
    from openalpha_cn.sdk import OpenAlphaSDK

    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=lambda: frozen_now)
    result = sdk.run_research(research_request())
    screened = ResearchScreener().screen(
        results=(result,),
        criteria=ScreeningCriteria(
            min_confidence=0.1,
            directions=("bullish",),
            final_actions=("watch",),
        ),
    )
    assert screened.items[0].subject == "000001.SZ"

    watchlists = SQLiteWatchlistStore(tmp_path / "state.sqlite3")
    entry = WatchlistEntry(
        subject="000001.SZ",
        tags=("涨停", "机器人"),
        note="次日观察承接",
        created_at=frozen_now,
        updated_at=frozen_now,
    )
    watchlists.put(entry)
    assert SQLiteWatchlistStore(tmp_path / "state.sqlite3").list() == (entry,)
    assert watchlists.remove("000001.SZ") is True

    reports = SQLiteReportStore(tmp_path / "state.sqlite3")
    report = ResearchReportFactory().build(result)
    reports.append(report)
    reports.append(report)
    assert reports.get(report.report_id) == report
    assert reports.list(subject="000001.SZ") == (report,)

    client = TestClient(create_app(runtime_dir=tmp_path / "api"))
    response = client.post("/api/v1/watchlist", json=entry.model_dump(mode="json"))
    assert response.status_code == 200
    assert client.get("/api/v1/watchlist").json()[0]["subject"] == "000001.SZ"
