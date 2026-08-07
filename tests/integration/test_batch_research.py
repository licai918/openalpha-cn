from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.batch import BatchResearchService, BatchResearchTask, BatchTaskItem
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.storage.batch import SQLiteBatchTaskStore

NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)


def request(run_id: str, subject: str) -> ResearchRunRequest:
    evidence = EvidenceSnapshot(
        subject=subject,
        kind="limit_up",
        timeline=Timeline(
            event_time=NOW,
            available_time=NOW,
            ingested_time=NOW,
            revision_time=NOW,
        ),
        source_id="batch.fixture",
        source_uri=f"fixture://{subject}",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Batch fixture.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": [],
        },
    )
    return ResearchRunRequest(
        run_id=run_id,
        mode="replay",
        subject=subject,
        as_of=NOW,
        evidence=(evidence,),
        code_commit="0123456789abcdef",
        config_digest="b" * 64,
        random_seed=7,
    )


def test_batch_runs_with_bounded_workers_and_persists_progress(tmp_path: Path) -> None:
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=lambda: NOW)
    store = SQLiteBatchTaskStore(tmp_path / "state.sqlite3")
    service = BatchResearchService(store=store, runner=sdk.run_research, clock=lambda: NOW)
    task = service.submit(
        batch_id="batch-1",
        requests=(request("batch-run-1", "000001.SZ"), request("batch-run-2", "600000.SH")),
        max_concurrency=2,
    )

    completed = service.run(task.batch_id)

    assert completed.status == "succeeded"
    assert {item.status for item in completed.items} == {"succeeded"}
    assert all(item.result is not None for item in completed.items)
    assert SQLiteBatchTaskStore(tmp_path / "state.sqlite3").get(task.batch_id) == completed
    assert [event.kind for event in store.list_events(task.batch_id)] == [
        "submitted",
        "started",
        "item_started",
        "item_started",
        "item_succeeded",
        "item_succeeded",
        "finished",
    ]


def test_batch_cancel_and_interrupted_recovery_are_explicit(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    store = SQLiteBatchTaskStore(path)
    service = BatchResearchService(
        store=store,
        runner=OpenAlphaSDK(runtime_dir=tmp_path, clock=lambda: NOW).run_research,
        clock=lambda: NOW,
    )
    task = service.submit(
        batch_id="batch-cancel",
        requests=(request("cancel-run", "000001.SZ"),),
        max_concurrency=1,
    )
    cancelled = service.cancel(task.batch_id)

    assert cancelled.status == "cancelled"
    assert cancelled.items[0].status == "cancelled"

    interrupted = BatchResearchTask(
        batch_id="batch-recover",
        items=(BatchTaskItem(request=request("recover-run", "000001.SZ"), status="running"),),
        status="running",
        max_concurrency=1,
        created_at=NOW,
        updated_at=NOW,
    )
    store.save(interrupted)
    recovered = SQLiteBatchTaskStore(path).recover_interrupted(now=NOW)

    assert recovered == ("batch-recover",)
    restored = store.get("batch-recover")
    assert restored is not None
    assert restored.status == "queued"
    assert restored.items[0].status == "queued"


def test_batch_http_surface_submits_runs_and_exposes_events(tmp_path: Path) -> None:
    client = TestClient(create_app(runtime_dir=tmp_path))
    response = client.post(
        "/api/v1/research/batches",
        json={
            "batch_id": "batch-api",
            "requests": [
                request("batch-api-run", "000001.SZ").model_dump(
                    mode="json", exclude_computed_fields=True
                )
            ],
            "max_concurrency": 1,
        },
    )

    assert response.status_code == 202
    task = client.get("/api/v1/research/batches/batch-api").json()
    assert task["status"] == "succeeded"
    events = client.get("/api/v1/research/batches/batch-api/events").json()
    assert events[-1]["kind"] == "finished"
